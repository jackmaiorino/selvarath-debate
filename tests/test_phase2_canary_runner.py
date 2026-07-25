"""The canary run driver: ordering, pausing, resuming, and halting.

The load-bearing decision here is what a pause does to the rest of the run. A cell pauses the
moment it proposes a query with no committed reviewer decision. If that stopped the run, each
labelling batch would contain a single payload and the canary would need hundreds of
round trips. Continuing past a paused cell instead accumulates payloads across every cell that
pauses, so one batch covers hundreds of them and the whole canary takes a handful of passes:
roughly one per query slot, since slot two is only proposed once slot one has been labelled
and dispatched.

Halts are the opposite. A checker outage or a malformed checker verdict is not a per-cell
inconvenience, it is evidence that the gate is not behaving as frozen, so it stops the run.
"""
import json
from pathlib import Path

import pytest

from rejudge.phase2_call_cache import CallCache
from rejudge.phase2_caching_client import CachingClient
from rejudge.phase2_canary_fixtures import DeterministicCanaryClient, StubReviewer, scripted
from rejudge.phase2_canary_runner import RunOutcome, run_canary
from rejudge.phase2_dual_gate import DualGateDecisionStore


ANCHOR = "Qwen/Qwen2.5-7B-Instruct-Turbo"


def _run(tmp_path, *, limit=None, client=None, reviewer=None, cell_filter=None):
    return run_canary(
        results_path=tmp_path / "results.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        client=client or DeterministicCanaryClient(),
        reviewer=reviewer or StubReviewer(),
        anchor_judge_model=ANCHOR,
        limit=limit, cell_filter=cell_filter)


def _no_query_cells(cell):
    return not cell.produces_queries


# --- the happy path over a slice of the plan ------------------------------------------------

def test_a_short_run_completes_and_records_every_cell(tmp_path):
    outcome = _run(tmp_path, limit=3)
    assert isinstance(outcome, RunOutcome)
    assert outcome.completed == 3
    assert outcome.halted_reason is None
    recorded = [json.loads(line) for line
                in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(recorded) == 3


def test_transcripts_run_first(tmp_path):
    _run(tmp_path, limit=3)
    recorded = [json.loads(line)["result"] for line
                in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all("debate_transcript" in result for result in recorded)


def test_a_resumed_run_skips_completed_cells(tmp_path):
    first = _run(tmp_path, limit=3)
    second = _run(tmp_path, limit=5)
    assert first.completed == 3
    # limit bounds NEW cells attempted, not total cells seen: the three already recorded are
    # skipped for free and five fresh ones run.
    assert second.completed == 5
    assert second.skipped == 3


# --- pausing ---------------------------------------------------------------------------------

def test_a_paused_cell_does_not_stop_the_run(tmp_path):
    # This is the property that makes labelling tractable: payloads accumulate across cells.
    outcome = run_canary(
        results_path=tmp_path / "results.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        client=DeterministicCanaryClient(), reviewer=StubReviewer(),
        anchor_judge_model=ANCHOR, pause_when_unlabeled=True, limit=520)
    assert outcome.paused > 1, "several cells should pause, not just the first"
    assert outcome.halted_reason is None
    # Deduplicated: distinct cells routinely propose identical query text, and one committed
    # decision serves every one of them by inheritance.
    assert 1 < len(outcome.pending_payloads) <= outcome.paused
    shas = [p["payload_sha256"] for p in outcome.pending_payloads]
    assert len(shas) == len(set(shas))


def test_pending_payloads_carry_only_the_blinded_fields(tmp_path):
    outcome = run_canary(
        results_path=tmp_path / "results.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        client=DeterministicCanaryClient(), reviewer=StubReviewer(),
        anchor_judge_model=ANCHOR, pause_when_unlabeled=True, limit=520)
    payload = outcome.pending_payloads[0]
    assert set(payload) == {"payload_sha256", "query", "candidate_a", "candidate_b"}


def test_a_paused_cell_is_not_recorded_as_complete(tmp_path):
    outcome = run_canary(
        results_path=tmp_path / "results.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        client=DeterministicCanaryClient(), reviewer=StubReviewer(),
        anchor_judge_model=ANCHOR, pause_when_unlabeled=True, limit=520)
    recorded = {json.loads(line)["cell_key"] for line
                in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()}
    assert len(recorded) == outcome.completed
    assert outcome.paused > 0


def test_the_label_and_resume_cycle_converges(tmp_path):
    """The real operational loop: run, label everything pending, run again, until done.

    Each pass gets one query slot further, because slot two is only proposed once slot one
    has been labelled and dispatched. With a budget of two that is a small, bounded number of
    passes over the whole plan rather than one round trip per query.
    """
    cache_path = tmp_path / "calls.jsonl"
    passes = 0
    while True:
        passes += 1
        assert passes <= 6, "the label-and-resume cycle should converge quickly"
        outcome = run_canary(
            results_path=tmp_path / "results.jsonl",
            decisions_path=tmp_path / "decisions.jsonl",
            client=CachingClient(DeterministicCanaryClient(), CallCache(cache_path)),
            reviewer=StubReviewer(), anchor_judge_model=ANCHOR,
            pause_when_unlabeled=True, limit=520)
        assert outcome.halted_reason is None
        if not outcome.needs_labelling:
            break
        store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
        for payload in outcome.pending_payloads:
            store.commit(payload["payload_sha256"], "ALLOW", "Allowed", "fine",
                         "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine", "parsed")

    # Everything reachable within the limit finished, and nothing is still waiting.
    assert outcome.paused == 0
    assert outcome.deferred == 0
    recorded = (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(recorded) >= 513 + 336


def test_each_pass_gets_one_query_slot_further(tmp_path):
    cache_path = tmp_path / "calls.jsonl"
    kwargs = dict(results_path=tmp_path / "results.jsonl",
                  decisions_path=tmp_path / "decisions.jsonl",
                  anchor_judge_model=ANCHOR, pause_when_unlabeled=True, limit=520)
    first = run_canary(client=CachingClient(DeterministicCanaryClient(), CallCache(cache_path)),
                       reviewer=StubReviewer(), **kwargs)
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    for payload in first.pending_payloads:
        store.commit(payload["payload_sha256"], "ALLOW", "Allowed", "fine",
                     "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine", "parsed")
    second = run_canary(
        client=CachingClient(DeterministicCanaryClient(), CallCache(cache_path)),
        reviewer=StubReviewer(), **kwargs)

    # Still paused, but on the *second* slot now, and on payloads not seen before.
    assert second.paused > 0
    assert not ({p["payload_sha256"] for p in second.pending_payloads}
                & {p["payload_sha256"] for p in first.pending_payloads})


# --- halting -----------------------------------------------------------------------------------

def test_a_checker_outage_halts_the_run(tmp_path):
    client = DeterministicCanaryClient(
        query_checker=scripted([TimeoutError("provider timeout")]))
    outcome = _run(tmp_path, client=client, limit=520)
    assert outcome.halted_reason == "checker_outage"
    assert outcome.halted_cell_key


def test_a_malformed_checker_verdict_halts_the_run(tmp_path):
    client = DeterministicCanaryClient(query_checker=scripted(["Allow"]))
    outcome = _run(tmp_path, client=client, limit=520)
    assert outcome.halted_reason == "checker_malformed"


def test_a_halt_preserves_everything_already_recorded(tmp_path):
    client = DeterministicCanaryClient(query_checker=scripted(["Allow"]))
    outcome = _run(tmp_path, client=client, limit=520)
    recorded = (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(recorded) == outcome.completed > 0
    # And a rerun resumes from there rather than starting over.
    assert _run(tmp_path, client=DeterministicCanaryClient(), limit=1).skipped >= 1


def test_an_unexpected_error_halts_rather_than_being_swallowed(tmp_path):
    class Exploding(DeterministicCanaryClient):
        def complete(self, *args, **kwargs):
            raise RuntimeError("something unmodelled")

    outcome = _run(tmp_path, client=Exploding(), limit=1)
    assert outcome.halted_reason == "RuntimeError"
    assert outcome.completed == 0


# --- selecting a subset -------------------------------------------------------------------------

def test_a_filter_restricts_which_cells_run(tmp_path):
    outcome = _run(tmp_path, cell_filter=lambda cell: cell.is_transcript, limit=10)
    assert outcome.completed == 10
    assert outcome.paused == 0


def test_a_filter_that_orphans_a_dependency_is_refused(tmp_path):
    # Dropping the sequential judgments would leave the batch replays depending on cells that
    # are not in the run. Refusing beats silently running a different experiment.
    from rejudge.phase2_canary_order import MissingDependency
    with pytest.raises(MissingDependency):
        _run(tmp_path, cell_filter=_no_query_cells, limit=10)
