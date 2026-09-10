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

# The pilot transcript corpus is untracked by design (data/*.jsonl is gitignored so the
# fictional eval worlds stay out of public training corpora). These tests bind the real
# corpus and can only run where it exists; skipping elsewhere is the honest outcome.
needs_corpus = pytest.mark.skipif(not Path("data/transcripts.jsonl").exists(),
                                  reason="pilot transcript corpus is untracked by design")

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

    def run_pass():
        return run_canary(
            results_path=tmp_path / "results.jsonl",
            decisions_path=tmp_path / "decisions.jsonl",
            client=CachingClient(DeterministicCanaryClient(), CallCache(cache_path)),
            reviewer=StubReviewer(), anchor_judge_model=ANCHOR,
            pause_when_unlabeled=True, limit=520)

    first = run_pass()
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    for payload in first.pending_payloads:
        store.commit(payload["payload_sha256"], "ALLOW", "Allowed", "fine",
                     "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine", "parsed")
    second = run_pass()

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


@pytest.mark.parametrize("workers", [1, 4])
@pytest.mark.parametrize("kind", ["checker", "ceiling"])
def test_serial_and_parallel_halts_keep_bounded_operational_detail(tmp_path, monkeypatch, workers, kind):
    from rejudge import phase2_canary_runner as runner
    from rejudge.api_client import UncertainCeilingHalt
    from rejudge.phase2_canary_gate import CanaryCellHalted

    def halt(*args, **kwargs):
        if kind == "checker":
            raise CanaryCellHalted("checker_outage", "TimeoutError: unavailable\n" + "x" * 1200)
        raise UncertainCeilingHalt("projected uncertainty 100.04 exceeds cap 100.00")

    monkeypatch.setattr(runner, "execute_cell", halt)
    outcome = _run_concurrent(tmp_path, max_workers=workers, limit=4)
    assert outcome.completed == 0
    assert outcome.halted_cell_key
    if kind == "checker":
        assert outcome.halted_reason == "checker_outage"
        assert outcome.halted_detail.startswith("TimeoutError: unavailable ")
        assert len(outcome.halted_detail) == 1000
    else:
        assert outcome.halted_reason == "UncertainCeilingHalt"
        assert outcome.halted_detail == "projected uncertainty 100.04 exceeds cap 100.00"


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


# --- concurrency ------------------------------------------------------------------------
#
# The canary ran one cell at a time, which is a property of the loop rather than of the work:
# 888 of its 940 cells landed inside 22 active hours at 40 cells/hour. The main run cannot
# afford that, but going wide must not change what is measured.

def _two_question_subset(cell):
    """Transcripts plus the budget-0 judgments that depend only on them, for two questions.

    Dependency-closed, so execution_order accepts it, and gate-free, so these tests exercise
    scheduling, claiming and capping rather than the reviewer (which the dual-gate suite
    covers directly). Two questions keeps it to a few dozen cells.
    """
    if cell.question_id not in ("CN-011", "SV-001"):
        return False
    return cell.is_transcript or cell.condition == "b0"


def _run_concurrent(tmp_path, **kwargs):
    return run_canary(
        results_path=tmp_path / "results.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        client=kwargs.pop("client", None) or DeterministicCanaryClient(),
        reviewer=kwargs.pop("reviewer", None) or StubReviewer(),
        anchor_judge_model=ANCHOR, **kwargs)


def test_a_concurrent_run_records_every_cell_exactly_once(tmp_path):
    outcome = _run_concurrent(tmp_path, max_workers=8, cell_filter=_two_question_subset)
    rows = [json.loads(line) for line
            in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    keys = [r["cell_key"] for r in rows]
    assert outcome.halted_reason is None
    assert len(keys) == len(set(keys)), "a cell recorded twice has been paid for twice"
    assert outcome.completed == len(keys)


def test_concurrent_and_serial_runs_agree_cell_for_cell(tmp_path):
    """The load-bearing claim. Going wide is allowed to change the schedule and nothing else,
    so the two runs must produce identical results for identical cells."""
    serial_dir, wide_dir = tmp_path / "serial", tmp_path / "wide"
    serial_dir.mkdir()
    wide_dir.mkdir()
    serial = _run_concurrent(serial_dir, max_workers=1, cell_filter=_two_question_subset)
    wide = _run_concurrent(wide_dir, max_workers=8, cell_filter=_two_question_subset)

    def scored(directory):
        out = {}
        for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            result = row["result"]
            out[row["cell_key"]] = (
                result.get("verdict_correct_strict"),
                result.get("position_a_is_correct"),
                (result.get("verdict_strict") or {}).get("verdict"),
                result.get("queries_used"))
        return out

    assert serial.completed == wide.completed
    assert scored(serial_dir) == scored(wide_dir)


def _peak_in_flight(tmp_path, *, model_caps, delay=0.02):
    """Highest simultaneous call count per model over one run.

    The delay matters: the fixture client returns instantly, so without it no two calls ever
    overlap, every model peaks at 1, and a cap assertion passes while proving nothing.
    """
    import threading
    import time

    live: dict[str, int] = {}
    peak: dict[str, int] = {}
    lock = threading.Lock()
    base = DeterministicCanaryClient()

    class Watched:
        def __init__(self):
            self.calls = base.calls

        def complete(self, messages, model, temperature, seed, max_tokens, **kwargs):
            with lock:
                live[model] = live.get(model, 0) + 1
                peak[model] = max(peak.get(model, 0), live[model])
            try:
                time.sleep(delay)
                return base.complete(messages, model, temperature, seed, max_tokens, **kwargs)
            finally:
                with lock:
                    live[model] -= 1

    _run_concurrent(tmp_path, max_workers=8, client=Watched(), model_caps=model_caps,
                    cell_filter=_two_question_subset)
    return peak


GEMMA = "google/gemma-4-31B-it"


def test_per_model_caps_bound_how_many_calls_are_in_flight(tmp_path):
    """gemma is 95% of the canary's call time and serves both the frozen checker and a judge
    role, and it abandoned 189 of 1702 calls at concurrency ONE. A global worker count would
    put most of the fleet on the one model already failing, so the cap has to be per model."""
    uncapped = _peak_in_flight(tmp_path / "uncapped", model_caps=None)
    assert uncapped.get(GEMMA, 0) > 2, (
        "the test is vacuous unless gemma would otherwise exceed the cap it is about to be "
        f"given; peak was {uncapped.get(GEMMA)}")

    capped = _peak_in_flight(tmp_path / "capped", model_caps={GEMMA: 2})
    assert capped.get(GEMMA, 0) <= 2, f"gemma exceeded its cap: peak {capped.get(GEMMA)}"
    assert max(v for m, v in capped.items() if m != GEMMA) > 2, (
        "the cap must bind gemma alone, not throttle the whole run")


def test_a_slow_cell_does_not_stall_the_others(tmp_path):
    """The block barrier made throughput the MAXIMUM over a block, not the mean.

    _run_concurrent collected every future in a block before recording anything or
    dispatching the next, so one straggler idled the other seven workers. Measured on the
    bridge canary that cost 8x: 3.5 calls per cell at a 15.4s mean predicts 534 cells/hour
    with eight workers, and it delivered 67.

    The test pins the property rather than the implementation: with one very slow cell among
    many fast ones, the run must finish in about the slow cell's own time, not that plus
    everything queued behind it.
    """
    import threading
    import time

    slow_seen = threading.Event()
    fast_after_slow = []
    base = DeterministicCanaryClient()

    class Staggered:
        def __init__(self):
            self.calls = base.calls

        def complete(self, messages, model, temperature, seed, max_tokens, **kwargs):
            role = (kwargs.get("request_metadata") or {}).get("call_role")
            if role == "debater_turn" and not slow_seen.is_set():
                slow_seen.set()
                time.sleep(1.0)          # one straggler
            elif slow_seen.is_set():
                fast_after_slow.append(1)  # progress made WHILE the straggler is blocked
            return base.complete(messages, model, temperature, seed, max_tokens, **kwargs)

    started = time.time()
    _run_concurrent(tmp_path, max_workers=8, client=Staggered(),
                    cell_filter=_two_question_subset)
    elapsed = time.time() - started

    assert fast_after_slow, "other workers must keep working while one cell is slow"
    assert elapsed < 20, (
        f"took {elapsed:.1f}s; a barrier would serialise the straggler against every block")


def test_dispatch_stays_condition_balanced_without_the_barrier(tmp_path):
    """Removing the barrier must not remove the confound protection it was carrying.

    Batch cells depend on their sequential parents and so always run later; the balance is
    what stops that becoming 'all of one condition, then all of another' and handing
    time-varying provider degradation to one co-primary.
    """
    import json as _json

    _run_concurrent(tmp_path, max_workers=8, cell_filter=_two_question_subset)
    rows = [_json.loads(line) for line
            in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    order = [r["result"].get("condition") or "transcript" for r in rows]
    judgments = [c for c in order if c == "b0"]
    if len(judgments) >= 8:
        # b0 cells must not all be bunched at one end of the run.
        first_half = order[:len(order) // 2].count("b0")
        second_half = order[len(order) // 2:].count("b0")
        assert first_half and second_half, (
            f"b0 cells clustered: {first_half} in the first half, {second_half} in the second")


# --- one ambiguous charge should not stop everything -----------------------------------------
#
# Measured on the bridge canary: 124 driver attempts, median 4 cells each, 4.77 hours of pure
# backoff. One abandoned call raised UnknownChargeHalt, which halted the whole run, and the
# supervisor then paid a backoff plus a full restart before completing another handful of
# cells. With gemma abandoning 16.8% of ~3,300 calls that is hundreds of full-run halts.
#
# The exception exists to stop THAT CALL being retried into a possible second unknown charge.
# It says nothing about unrelated cells, and the uncertain-spend ceiling is the control that
# actually bounds total exposure.

def test_an_abandoned_call_fails_its_cell_not_the_run(tmp_path):
    from rejudge.api_client import UnknownChargeHalt

    state = {"n": 0}
    base = DeterministicCanaryClient()

    class Flaky:
        def __init__(self):
            self.calls = base.calls

        def complete(self, messages, model, temperature, seed, max_tokens, **kwargs):
            state["n"] += 1
            if state["n"] == 3:
                raise UnknownChargeHalt("billing status unknown for this attempt")
            return base.complete(messages, model, temperature, seed, max_tokens, **kwargs)

    outcome = _run_concurrent(tmp_path, max_workers=4, client=Flaky(),
                              cell_filter=_two_question_subset)
    assert outcome.halted_reason is None, (
        f"one ambiguous charge halted the whole run: {outcome.halted_reason}")
    assert outcome.abandoned >= 1, "the affected cell should be reported as abandoned"
    assert outcome.completed > 5, "every other cell should still have run"


def test_the_abandoned_cell_is_left_unrecorded_so_a_later_pass_retries_it(tmp_path):
    from rejudge.api_client import UnknownChargeHalt

    state = {"n": 0}
    base = DeterministicCanaryClient()

    class Flaky:
        def __init__(self):
            self.calls = base.calls

        def complete(self, messages, model, temperature, seed, max_tokens, **kwargs):
            state["n"] += 1
            if state["n"] == 3:
                raise UnknownChargeHalt("billing status unknown")
            return base.complete(messages, model, temperature, seed, max_tokens, **kwargs)

    first = _run_concurrent(tmp_path, max_workers=4, client=Flaky(),
                            cell_filter=_two_question_subset)
    assert first.abandoned >= 1
    second = _run_concurrent(tmp_path, max_workers=4, cell_filter=_two_question_subset)
    assert second.completed >= first.abandoned, "the abandoned cells run on the next pass"


def test_a_provider_abandoning_everything_still_halts(tmp_path):
    """Tolerating an abandoned cell must not become tolerating a dead provider."""
    from rejudge.api_client import UnknownChargeHalt

    class Dead:
        calls = []

        def complete(self, *a, **k):
            raise UnknownChargeHalt("billing status unknown")

    outcome = _run_concurrent(tmp_path, max_workers=4, client=Dead(),
                              cell_filter=_two_question_subset)
    assert outcome.halted_reason is not None, "a wholly failing provider must stop the run"


def test_main_mode_makes_the_first_unknown_charge_fatal(tmp_path):
    from rejudge.api_client import UnknownChargeHalt

    class Ambiguous:
        calls = []

        def complete(self, *args, **kwargs):
            raise UnknownChargeHalt("billing status unknown")

    outcome = run_canary(
        results_path=tmp_path / "results.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        client=Ambiguous(), reviewer=StubReviewer(), anchor_judge_model=ANCHOR,
        limit=1, fatal_unknown_charge=True)
    assert outcome.completed == 0
    assert outcome.abandoned == 1
    assert outcome.halted_reason == "unknown_charge"
    assert outcome.halted_cell_key


@needs_corpus
def test_the_runner_can_be_pointed_at_the_main_plan(tmp_path):
    """run_canary enumerated the canary plan unconditionally, so the main grid had no
    execution path at all: its manifest validated while nothing could run it."""
    from rejudge.phase2_main_manifest import enumerate_main_cells

    main = [c for c in enumerate_main_cells(".")
            if c["kind"] != "capability_qa" and c["question_id"] == "CN-001"
            and (c["kind"] == "debate_transcript" or c["condition"] == "b0")]
    assert main, "fixture assumption: the main plan has transcript and b0 cells for CN-001"

    outcome = run_canary(
        results_path=tmp_path / "results.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        client=DeterministicCanaryClient(), reviewer=StubReviewer(),
        anchor_judge_model=ANCHOR, cells=main, max_workers=4)
    assert outcome.completed == len(main)
    assert outcome.halted_reason is None
    recorded = {json.loads(line)["cell_key"] for line
                in (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()}
    assert recorded == {c["cell_key"] for c in main}


def test_the_canary_plan_is_still_the_default(tmp_path):
    """Two completed runs and every existing caller depend on the default."""
    outcome = _run(tmp_path, limit=3)
    assert outcome.completed == 3
