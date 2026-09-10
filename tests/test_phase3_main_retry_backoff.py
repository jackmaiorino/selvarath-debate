"""Exact durable request failures cool temporarily without changing scientific work."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from rejudge.phase2_call_cache import CallKey, request_fingerprint
from rejudge.phase2_canary_cells import ResolvedCell
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_canary_runner import RunOutcome, run_canary
from rejudge import phase2_canary_runner as canary
from rejudge import phase3_main_retry_backoff as retry
from rejudge.request_journal import (
    JournalingClient, RequestJournal, JournalReplayMismatch, JournalDispatchUnresolved,
    journal_key,
)
from test_phase3_main_recovery import stopped_run, build, validate
from test_phase3_main_live import _prepared, inventory
from test_phase3_main_resume import _stopped


NOW = datetime(2026, 9, 9, 20, tzinfo=timezone.utc)
META = {"cell_key": "cool", "call_role": "judge_query", "query_index": 0, "attempt": 1}
ARGS = ([{"role": "user", "content": "fixed"}], "model-a", 0.0, 123, 256)
FP = request_fingerprint(messages=ARGS[0], model=ARGS[1], temperature=ARGS[2],
                         seed=ARGS[3], max_tokens=ARGS[4])


class Clock:
    def __init__(self):
        self.now = NOW
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert 0 < seconds <= 60
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def events(count=3, *, metadata=None, fingerprint=FP, timestamp=NOW, offset=0):
    return [{"status": "unknown_charge", "attempt_id": f"physical-{i + offset}",
             "attempt": 0, "ts": timestamp.isoformat(), "cost_usd": 0.1,
             "metadata": {**(metadata or META), "journal_request_sha256": fingerprint}}
            for i in range(count)]


def setup(tmp_path, count=3):
    clock = Clock()
    journal = RequestJournal(tmp_path / "journal.jsonl", execution_identity="same-run")
    backoff = retry.ProviderRetryBackoff(journal, clock=clock)
    backoff.observe(events(count))
    return clock, journal, backoff


def cell(key, deps=(), model="model-a"):
    return ResolvedCell(key, "no_debate_judgment", "fixed", "question", model, None,
                        None, 0, 0, tuple(deps), {}, None, "clean", None)


def defer(backoff):
    with pytest.raises(retry.ProviderRetryDeferred):
        backoff.before_dispatch(journal_key(META), FP)


def test_distinct_physical_attempts_reconstruct_and_observation_is_idempotent(tmp_path):
    clock, journal, backoff = setup(tmp_path)
    before = journal.path.read_bytes() if journal.path.exists() else b""
    backoff.observe(events())
    window = backoff.window(journal_key(META), FP)
    assert window.failures == 3
    assert window.ready_at == NOW + timedelta(minutes=15)
    assert backoff.cooling_cells() == {}  # cold failures cannot predict next request
    defer(backoff)
    assert backoff.cooling_cells() == {"cool": window.ready_at}
    rebuilt = retry.ProviderRetryBackoff(
        RequestJournal(journal.path, execution_identity="same-run"), clock=clock)
    rebuilt.observe(events())
    assert rebuilt.window(journal_key(META), FP) == window
    assert rebuilt.cooling_cells() == {}
    assert (journal.path.read_bytes() if journal.path.exists() else b"") == before


@pytest.mark.parametrize("change", [
    {"cell_key": "other"}, {"call_role": "oracle"}, {"query_index": 1}, {"attempt": 2},
])
def test_logical_identity_is_exact(tmp_path, change):
    _, _, backoff = setup(tmp_path, 2)
    backoff.observe(events(1, metadata={**META, **change}, offset=2))
    assert backoff.window(journal_key(META), FP) is None


def test_fingerprint_and_irrelevant_metadata_do_not_mix_failures(tmp_path):
    _, _, backoff = setup(tmp_path, 2)
    backoff.observe(events(1, fingerprint="e" * 64, offset=2))
    assert backoff.window(journal_key(META), FP) is None
    backoff.observe(events(1, metadata={**META, "logical_dispatch_authorized_at_utc": "later"}, offset=3))
    assert backoff.window(journal_key(META), FP).failures == 3
    assert backoff.window(journal_key(META), "e" * 64) is None


@pytest.mark.parametrize("status", ["reserved", "released_no_charge", "charged_malformed", "success", "provider_error"])
def test_non_unknown_events_do_not_increase_failure_count(tmp_path, status):
    _, _, backoff = setup(tmp_path, 2)
    extra = events(1, offset=2)[0]
    extra["status"] = status
    backoff.observe([extra])
    assert backoff.window(journal_key(META), FP) is None


def test_elapsed_time_does_not_erase_failures_and_delay_is_bounded(tmp_path):
    clock, _, backoff = setup(tmp_path)
    clock.now += timedelta(minutes=15)
    assert backoff.window(journal_key(META), FP) is None  # exact expiry is eligible
    backoff.before_dispatch(journal_key(META), FP)
    for count, minutes in [(4, 30), (5, 60), (6, 60)]:
        backoff.observe(events(1, timestamp=clock.now, offset=count - 1))
        window = backoff.window(journal_key(META), FP)
        assert window.failures == count and window.backoff_seconds == minutes * 60
        assert window.last_unknown_at == clock.now
        clock.now += timedelta(minutes=minutes)
        assert backoff.window(journal_key(META), FP) is None


class Provider:
    def __init__(self):
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "saved"


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("response", ["", "saved"])
def test_saved_success_bypasses_backoff_without_dispatch(tmp_path, workers, response):
    _, journal, backoff = setup(tmp_path)
    defer(backoff)
    journal.put(journal_key(META), FP, response)
    provider = Provider()
    client = JournalingClient(provider, journal, max_concurrent_requests=workers,
                              before_uncached_dispatch=backoff.before_dispatch)
    assert client.complete(*ARGS, request_metadata=META) == response
    assert provider.calls == []
    assert backoff.cooling_cells() == {}
    with pytest.raises(JournalReplayMismatch):
        client.complete(*ARGS[:-1], ARGS[-1] + 1, request_metadata=META)


@pytest.mark.parametrize("workers", [1, 2])
def test_deferred_request_creates_no_marker_and_other_request_runs(tmp_path, workers):
    clock, journal, backoff = setup(tmp_path)
    provider = Provider()
    client = JournalingClient(provider, journal, max_concurrent_requests=workers,
                              before_uncached_dispatch=backoff.before_dispatch)
    with pytest.raises(retry.ProviderRetryDeferred):
        client.complete(*ARGS, request_metadata=META)
    assert provider.calls == [] and not journal.dispatch_marker_paths()
    assert client.complete(*ARGS, request_metadata={**META, "cell_key": "other"}) == "saved"
    assert len(provider.calls) == 1
    clock.now += timedelta(minutes=15)
    assert client.complete(*ARGS, request_metadata=META) == "saved"
    assert len(provider.calls) == 2
    assert client.complete(*ARGS, request_metadata=META) == "saved"
    assert len(provider.calls) == 2


def test_parallel_deferral_does_not_acquire_provider_permits(tmp_path):
    _, journal, backoff = setup(tmp_path)
    client = JournalingClient(Provider(), journal, max_concurrent_requests=2,
                              before_uncached_dispatch=backoff.before_dispatch)
    class NoPermits:
        def __enter__(self):
            raise AssertionError("cooldown acquired a provider permit")
        def __exit__(self, *args):
            pass
    client._parallel_slots = NoPermits()
    with pytest.raises(retry.ProviderRetryDeferred):
        client.complete(*ARGS, request_metadata=META)
    journal.put(journal_key(META), FP, "")
    assert client.complete(*ARGS, request_metadata=META) == ""


@pytest.mark.parametrize("workers", [1, 2])
def test_cooldown_does_not_hide_unresolved_accounting_or_metadata_mismatch(tmp_path, workers):
    _, journal, backoff = setup(tmp_path)
    client = JournalingClient(Provider(), journal, max_concurrent_requests=workers,
                              before_uncached_dispatch=backoff.before_dispatch)
    with pytest.raises(JournalReplayMismatch):
        client.complete(*ARGS, request_metadata={**META, "journal_request_sha256": "f" * 64})
    client._unresolved_dispatch = "unreconciled"
    with pytest.raises(JournalDispatchUnresolved):
        client.complete(*ARGS, request_metadata=META)


@pytest.mark.parametrize("workers", [1, 2])
def test_canary_deferrals_do_not_count_as_attempts_abandonment_or_results(tmp_path, monkeypatch, workers):
    _, _, backoff = setup(tmp_path)
    attempted = []
    def execute(candidate, context, **kwargs):
        attempted.append(candidate.cell_key)
        if candidate.cell_key == "cool":
            backoff.before_dispatch(journal_key(META), FP)
        return {"cell_key": candidate.cell_key}
    monkeypatch.setattr(canary, "execute_cell", execute)
    outcome = run_canary(results_path=tmp_path / "results.jsonl", decisions_path=tmp_path / "decisions.jsonl",
                         client=object(), reviewer=object(), anchor_judge_model="", protocol={}, bundle={},
                         cells=[cell("cool"), cell("other")], max_workers=workers, block_size=2, limit=1)
    assert outcome.retry_deferred == outcome.deferred == 1
    assert outcome.attempted == outcome.completed == 1
    assert outcome.abandoned == 0 and outcome.halted_reason is None
    assert attempted == ["cool", "other"]
    assert set(CellResultStore(tmp_path / "results.jsonl")._results) == {"other"}


def test_only_cooling_queue_waits_in_same_pass_then_eventually_retries(tmp_path, monkeypatch):
    clock, journal, backoff = setup(tmp_path)
    provider = Provider()
    client = JournalingClient(provider, journal, max_concurrent_requests=2,
                              before_uncached_dispatch=backoff.before_dispatch)
    calls, logs = [], []
    path = tmp_path / "results.jsonl"
    def execute(candidate, context):
        calls.append(candidate.cell_key)
        result = context.client.complete(*ARGS, request_metadata={**META, "cell_key": candidate.cell_key})
        return {"cell_key": candidate.cell_key, "result": result}
    monkeypatch.setattr(canary, "execute_cell", execute)
    def run(cells):
        return run_canary(results_path=path, decisions_path=tmp_path / "decisions.jsonl",
                          client=client, reviewer=object(), anchor_judge_model="", protocol={}, bundle={},
                          cells=cells, max_workers=2)
    outcome = retry.run_eligible_pass(cells=[cell("cool"), cell("dependent", ["cool"])],
        backoff=backoff, completed_keys=lambda: set(CellResultStore(path)._results),
        run=run, sleep=clock.sleep, log=logs.append)
    assert outcome.completed == 2
    assert calls == ["cool", "cool", "dependent"]  # cold exact guard, then eligible execution
    assert len(provider.calls) == 2 and sum(clock.sleeps) == 900
    assert len(clock.sleeps) == 15
    waits = [row for row in logs if row["event"] == "provider_retry_cooldown"]
    assert {row["next_eligible_at_utc"] for row in waits} == {(NOW + timedelta(minutes=15)).isoformat()}
    assert {row["last_unknown_at_utc"] for row in waits} == {NOW.isoformat()}
    assert logs[-1]["event"] == "provider_retry_cooldown_complete"


def test_known_cooling_parent_and_dependents_leave_other_models_runnable(tmp_path):
    clock, _, backoff = setup(tmp_path)
    defer(backoff)
    seen = []
    outcome = retry.run_eligible_pass(
        cells=[cell("cool"), cell("child", ["cool"]), cell("grandchild", ["child"]),
               cell("other", model="model-b")], backoff=backoff, completed_keys=lambda: set(),
        run=lambda cells: seen.extend(c.cell_key for c in cells) or RunOutcome(completed=1, attempted=1),
        sleep=clock.sleep, log=lambda row: None)
    assert seen == ["other"] and outcome.completed == 1 and clock.sleeps == []


def test_completed_cells_do_not_keep_a_queue_cooling(tmp_path):
    clock, _, backoff = setup(tmp_path)
    defer(backoff)
    result = retry.run_eligible_pass(cells=[cell("cool")], backoff=backoff,
        completed_keys=lambda: {"cool"}, run=lambda cells: RunOutcome(),
        sleep=clock.sleep, log=lambda row: None)
    assert result.completed == 0 and clock.sleeps == []


def test_recovery_backoff_is_explicit_bounded_opt_in(stopped_run):
    original = build(stopped_run)
    assert "provider_retry_backoff_policy" not in original
    stopped_run.kwargs["provider_retry_backoff_policy"] = retry.POLICY
    enabled = build(stopped_run)
    assert validate(stopped_run, enabled)["provider_retry_backoff_policy"] == retry.POLICY
    assert enabled["stage_cap_usd"] == original["stage_cap_usd"]
    assert enabled["scientific_contract_sha256"] == original["scientific_contract_sha256"]
    enabled["provider_retry_backoff_policy"] = "no_delay"
    with pytest.raises(ValueError, match="unsupported provider retry"):
        validate(stopped_run, enabled)


def test_live_driver_reconstructs_durable_failures_and_waits_without_consuming_passes(
    tmp_path, inventory, monkeypatch,
):
    from rejudge import api_client, phase3_main_live as live
    from test_request_journal_unknown_charge import _FailsThenSucceeds, _strict_client

    prepared = _stopped(_prepared(tmp_path, inventory))
    prepared = replace(prepared, recovery_validation={**prepared.recovery_validation,
                       "provider_retry_backoff_policy": retry.POLICY})
    paths = prepared.identity.paths
    sdk = _FailsThenSucceeds(failures=3)
    raw = _strict_client(sdk, usage_log_path=paths.usage_ledger,
                         _ledger_snapshot=api_client.load_chained_usage_ledger(paths.usage_ledger))
    journal = RequestJournal(paths.request_journal,
                             execution_identity=prepared.identity.journal_execution_identity)
    client = JournalingClient(raw, journal, max_concurrent_requests=8)
    model = next(iter(prepared.price_snapshot["models"]))
    args = (ARGS[0], model, *ARGS[2:])
    for _ in range(3):
        with pytest.raises(api_client.UnknownChargeHalt):
            client.complete(*args, request_metadata=META)
    snapshot = api_client.load_chained_usage_ledger(paths.usage_ledger)
    prefix = paths.usage_ledger.read_bytes()
    unknowns = [row for row in api_client._read_usage_events(paths.usage_ledger)
                if row.get("status") == "unknown_charge"]
    assert len({row["attempt_id"] for row in unknowns}) == 3
    assert {row["attempt"] for row in unknowns} == {0}
    state = live.phase3_main_recovery_driver.restore_driver_state(
        paths, expected_run_id=prepared.identity.run_id,
        expected_manifest_sha256=prepared.identity.manifest_sha256)
    assert state.existing_unknown_charge_count == 3
    clock = Clock()
    clock.now = max(datetime.fromisoformat(row["ts"].replace("Z", "+00:00")) for row in unknowns)
    monkeypatch.setattr(live, "_RETRY_BACKOFF_CLOCK", clock)
    def sleep(seconds):
        assert sdk.calls == 3
        clock.sleep(seconds)
    monkeypatch.setattr(live, "_RETRY_BACKOFF_SLEEP", sleep)
    monkeypatch.setattr(live.phase3_runner, "resolve_main_cells", lambda *args, **kwargs: [cell("cool")])
    monkeypatch.setattr(live, "_load_bound_input_object", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "_reviewer_loop_contract", lambda plan: (60, 10))
    called = []
    def run(**kwargs):
        called.append(kwargs)
        assert [candidate.cell_key for candidate in kwargs["cells"]] == ["cool"]
        try:
            kwargs["client"].complete(*args, request_metadata=META)
        except retry.ProviderRetryDeferred:
            return RunOutcome(retry_deferred=1, deferred=1)
        return RunOutcome(attempted=1, paused=1, pending_payloads=["new-payload"])
    monkeypatch.setattr(live, "run_canary", run)
    class ReviewReached(Exception):
        pass
    def review(candidate, payloads, *, wave, held_run_lease):
        assert wave == 4 and payloads == ["new-payload"]
        raise ReviewReached
    monkeypatch.setattr(live, "_review_wave_same_process", review)
    with live.phase3_v3_live.RunLease(paths.lease) as lease:
        with pytest.raises(ReviewReached):
            live._drive_and_finalize(prepared, client, held_run_lease=lease, resume_state=state)
    assert sdk.calls == 4 and len(called) == 2 and sum(clock.sleeps) == 900
    assert paths.usage_ledger.read_bytes().startswith(prefix)
    assert api_client.load_chained_usage_ledger(paths.usage_ledger).summary["uncertain_spend_usd"] == (
        snapshot.summary["uncertain_spend_usd"])
    logs = [json.loads(line) for line in paths.run_log.read_text().splitlines()]
    starts = [row for row in logs if row["event"] == "formal_main_pass_started"]
    assert [row["pass_index"] for row in starts] == [4]
    waits = [row for row in logs if row["event"] == "provider_retry_cooldown"]
    assert len(waits) == 15 and {row["pass_index"] for row in waits} == {4}
    assert called[-1]["max_workers"] == 8
    assert called[-1]["model_caps"] == prepared.recovery_validation["initial_per_model_limits"]
    assert called[-1]["protocol"] == prepared.protocol
