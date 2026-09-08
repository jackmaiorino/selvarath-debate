"""Parallel dispatch, durable replay, and conservative crash recovery integration tests."""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from rejudge import api_client
from rejudge.phase2_call_cache import request_fingerprint
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD, JournalDispatchUnresolved, JournalWriterLocked,
    JournalingClient, RequestJournal, find_ambiguous_dispatches, journal_key,
    recover_interrupted_dispatches, validate_unreserved_dispatch,
)


def _metadata(cell):
    return {"cell_key": cell, "call_role": "judge_query", "query_index": 0}


def _complete(client, cell):
    return client.complete(
        [{"role": "user", "content": cell}], "judge-model", 0.0, 7, 256,
        kind="query", request_metadata=_metadata(cell))


def _fingerprint(cell):
    return request_fingerprint(
        messages=[{"role": "user", "content": cell}], model="judge-model",
        temperature=0.0, seed=7, max_tokens=256)


def test_independent_requests_overlap_and_duplicate_key_replays_once(tmp_path):
    entered = threading.Barrier(4)
    release = threading.Event()
    state_lock = threading.Lock()
    calls = []

    class Provider:
        def complete(self, messages, *args, **kwargs):
            cell = kwargs["request_metadata"]["cell_key"]
            with state_lock:
                calls.append(cell)
            entered.wait(timeout=5)
            assert release.wait(timeout=5)
            return "" if cell == "empty" else "CLAIM: " + cell

    journal = RequestJournal(tmp_path / "journal.jsonl", execution_identity="parallel")
    client = JournalingClient(Provider(), journal, max_concurrent_requests=3)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_complete, client, cell)
                   for cell in ("empty", "two", "three", "empty")]
        try:
            entered.wait(timeout=5)
            markers = journal.dispatch_marker_paths()
            assert len(markers) == 3
            assert len({json.loads(path.read_bytes())["cell_key"] for path in markers}) == 3
            other = JournalingClient(Provider(), RequestJournal(journal.path),
                                     max_concurrent_requests=3)
            with pytest.raises(JournalWriterLocked):
                _complete(other, "outsider")
        finally:
            release.set()
        assert [future.result(timeout=5) for future in futures] == [
            "", "CLAIM: two", "CLAIM: three", ""]
    assert sorted(calls) == ["empty", "three", "two"]
    assert not journal.dispatch_marker_paths()
    reloaded = RequestJournal(journal.path, execution_identity="parallel")
    assert reloaded.get(journal_key(_metadata("empty")), _fingerprint("empty")) == ""
    rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert [row["sequence"] for row in rows] == [0, 1, 2]
    assert len(reloaded.entry_identities()) == 3


def test_failed_parallel_dispatch_drains_paid_peers_and_blocks_new_calls(tmp_path):
    entered = threading.Barrier(3)
    failed = threading.Event()
    release_peer = threading.Event()
    calls = []

    class Provider:
        def complete(self, messages, *args, **kwargs):
            cell = kwargs["request_metadata"]["cell_key"]
            calls.append(cell)
            entered.wait(timeout=5)
            if cell == "fail":
                failed.set()
                raise TimeoutError("unobserved request")
            assert release_peer.wait(timeout=5)
            return "CLAIM: paid peer must survive"

    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(Provider(), journal, max_concurrent_requests=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_complete, client, "fail")
        second = pool.submit(_complete, client, "peer")
        try:
            entered.wait(timeout=5)
            assert failed.wait(timeout=5)
            with pytest.raises(TimeoutError):
                first.result(timeout=5)
            with pytest.raises(JournalDispatchUnresolved):
                _complete(client, "third")
        finally:
            release_peer.set()
        assert second.result(timeout=5) == "CLAIM: paid peer must survive"
    assert sorted(calls) == ["fail", "peer"]
    assert len(journal.dispatch_marker_paths()) == 1
    assert journal.has(journal_key(_metadata("peer")))
    reopened = JournalingClient(Provider(), RequestJournal(journal.path),
                               max_concurrent_requests=2)
    with pytest.raises(JournalDispatchUnresolved):
        _complete(reopened, "third")


def test_model_limits_apply_to_actual_requested_model_across_workers(tmp_path):
    ready = threading.Event()
    release = threading.Event()
    mutex = threading.Lock()
    active = {"judge": 0, "checker": 0}
    peaks = dict(active)
    calls = []

    class Provider:
        def complete(self, messages, model, *args, **kwargs):
            with mutex:
                calls.append(model)
                active[model] += 1
                peaks[model] = max(peaks[model], active[model])
                if sum(active.values()) == 3:
                    ready.set()
            assert release.wait(timeout=5)
            with mutex:
                active[model] -= 1
            return "CLAIM: same scientific response"

    client = JournalingClient(
        Provider(), RequestJournal(tmp_path / "journal.jsonl"),
        max_concurrent_requests=4, model_caps={"judge": 2, "checker": 1})

    def call(index, model):
        return client.complete(
            [{"role": "user", "content": "same"}], model, 0.0, 7, 256,
            kind="query", request_metadata=_metadata(str(index)))

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(call, index, model) for index, model in enumerate(
            ["judge", "judge", "checker", "judge", "checker", "checker"])]
        try:
            assert ready.wait(timeout=5)
            assert peaks == {"judge": 2, "checker": 1}
        finally:
            release.set()
        assert all(future.result(timeout=5) == "CLAIM: same scientific response"
                   for future in futures)
    assert peaks == {"judge": 2, "checker": 1}
    assert len(calls) == 6


def _crashed_dispatch(tmp_path, *, parallel=False):
    ledger_path = tmp_path / "usage.jsonl"
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    snapshot = api_client.load_chained_usage_ledger(ledger_path)

    def die(**kwargs):
        raise KeyboardInterrupt("simulated process interruption before response")

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=die)))
    inner = api_client.RejudgeClient(
        approved_cap_usd=5, max_retries=0, _sdk_client=sdk,
        usage_log_path=ledger_path, _ledger_snapshot=snapshot)
    journal = RequestJournal(tmp_path / "journal.jsonl", execution_identity="recover-me")
    client = JournalingClient(inner, journal, max_concurrent_requests=2 if parallel else 1)
    with pytest.raises(KeyboardInterrupt):
        _complete(client, "lost")
    marker_path, = journal.dispatch_marker_paths()
    raw = marker_path.read_bytes()
    marker = json.loads(raw)
    reservation = json.loads(ledger_path.read_text().splitlines()[-1])
    assert reservation["status"] == "reserved"
    dispatch = {
        "marker_path": str(marker_path), "marker_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "marker": marker, "reservation": reservation,
        "reservation_attempt_id": reservation["attempt_id"],
        "request_sha256": marker["request_sha256"],
    }
    return journal, ledger_path, dispatch


@pytest.mark.parametrize("parallel", [False, True])
def test_recovery_keeps_full_uncertain_cost_and_retry_replays_exactly(tmp_path, parallel):
    journal, ledger, dispatch = _crashed_dispatch(tmp_path, parallel=parallel)
    before = api_client.load_chained_usage_ledger(ledger).summary
    prefix = ledger.read_bytes()
    outcomes = recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert outcomes[0]["uncertain_cost_usd"] == dispatch["reservation"]["cost_usd"]
    assert ledger.read_bytes().startswith(prefix)
    after = api_client.load_chained_usage_ledger(ledger).summary
    assert before["accounted_spend_usd"] == after["accounted_spend_usd"]
    assert after["unmatched_reservations"] == 0
    assert after["uncertain_spend_usd"] == before["uncertain_spend_usd"]
    assert not journal.dispatch_marker_paths()
    once = ledger.read_bytes()
    recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert ledger.read_bytes() == once
    event = json.loads(once.splitlines()[-1])
    assert event["metadata"] == dispatch["reservation"]["metadata"]
    assert event["prompt_tokens"] is None and event["completion_tokens"] is None
    assert "response_metadata" not in event

    calls = []

    def succeed(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
            choices=[SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="stop")],
            model="judge-model", id="new-attempt", system_fingerprint=None)

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=succeed)))
    snapshot = api_client.load_chained_usage_ledger(ledger)
    inner = api_client.RejudgeClient(
        approved_cap_usd=5, max_retries=0, _sdk_client=sdk,
        usage_log_path=ledger, _ledger_snapshot=snapshot,
        initial_spend_usd=1 + snapshot.summary["actual_spend_usd"],
        initial_uncertain_spend_usd=snapshot.summary["uncertain_spend_usd"],
        initial_run_uncertain_spend_usd=snapshot.summary["uncertain_spend_usd"])
    client = JournalingClient(inner, journal, max_concurrent_requests=3)
    assert _complete(client, "lost") == ""
    assert _complete(client, "lost") == ""
    assert len(calls) == 1
    events = [json.loads(line) for line in ledger.read_text().splitlines()]
    findings = find_ambiguous_dispatches(journal, events)
    assert [finding["problem"] for finding in findings] == ["unknown_charge"]
    summary = api_client.load_chained_usage_ledger(ledger).summary
    assert inner.spent_usd == pytest.approx(1 + summary["accounted_spend_usd"])
    completed = ledger.read_bytes(), journal.path.read_bytes()
    recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert (ledger.read_bytes(), journal.path.read_bytes()) == completed


def test_recovery_is_idempotent_after_terminal_append_before_marker_removal(tmp_path, monkeypatch):
    journal, ledger, dispatch = _crashed_dispatch(tmp_path)
    finish = journal._finish_dispatch

    def die(marker):
        raise KeyboardInterrupt("crash after recovery accounting fsync")

    monkeypatch.setattr(journal, "_finish_dispatch", die)
    with pytest.raises(KeyboardInterrupt):
        recover_interrupted_dispatches(
            journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    once = ledger.read_bytes()
    assert journal.unresolved_marker_path.exists()
    monkeypatch.setattr(journal, "_finish_dispatch", finish)
    recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert ledger.read_bytes() == once
    assert not journal.dispatch_marker_paths()


@pytest.mark.parametrize("mutation", ["marker", "reservation", "identity", "missing_marker"])
def test_recovery_refuses_unbound_or_modified_interrupted_dispatch(tmp_path, mutation):
    journal, ledger, dispatch = _crashed_dispatch(tmp_path)
    if mutation == "marker":
        journal.unresolved_marker_path.write_text("{}")
    elif mutation == "reservation":
        dispatch["reservation"]["cost_usd"] += 1
    elif mutation == "identity":
        dispatch["marker"]["execution_identity"] = "other-run"
    else:
        journal.unresolved_marker_path.unlink()
    ledger_before = ledger.read_bytes()
    with pytest.raises((JournalDispatchUnresolved, api_client.UsageLedgerError)):
        recover_interrupted_dispatches(
            journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert ledger.read_bytes() == ledger_before


def test_recovery_never_replaces_a_journaled_response(tmp_path):
    journal, ledger, dispatch = _crashed_dispatch(tmp_path)
    with journal.dispatch_guard():
        journal._put_guarded(journal_key(_metadata("lost")), _fingerprint("lost"), "")
    before = ledger.read_bytes()
    with pytest.raises(JournalDispatchUnresolved, match="already journaled"):
        recover_interrupted_dispatches(
            journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert ledger.read_bytes() == before
    assert journal.get(journal_key(_metadata("lost")), _fingerprint("lost")) == ""


def _completed_marker(tmp_path, monkeypatch, *, response="", parallel=False):
    ledger_path = tmp_path / "usage.jsonl"
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    snapshot = api_client.load_chained_usage_ledger(ledger_path)
    calls = []

    def succeed(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
            choices=[SimpleNamespace(message=SimpleNamespace(content=response), finish_reason="stop")],
            model="judge-model", id="observed", system_fingerprint=None)

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=succeed)))
    inner = api_client.RejudgeClient(
        approved_cap_usd=5, max_retries=0, _sdk_client=sdk,
        usage_log_path=ledger_path, _ledger_snapshot=snapshot)
    journal = RequestJournal(tmp_path / "journal.jsonl", execution_identity="completed-marker")

    def crash(marker):
        raise KeyboardInterrupt("crash after response fsync before marker cleanup")

    with monkeypatch.context() as scoped:
        scoped.setattr(journal, "_finish_dispatch", crash)
        with pytest.raises(KeyboardInterrupt):
            _complete(JournalingClient(inner, journal, max_concurrent_requests=2 if parallel else 1),
                      "observed")
    marker_path, = journal.dispatch_marker_paths()
    raw = marker_path.read_bytes()
    marker = json.loads(raw)
    reservation, terminal = [json.loads(line) for line in ledger_path.read_text().splitlines()][1:]
    dispatch = {
        "disposition": "completed_journaled_response",
        "marker_path": str(marker_path), "marker_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "marker": marker, "reservation": reservation, "terminal": terminal,
        "reservation_attempt_id": reservation["attempt_id"],
        "request_sha256": marker["request_sha256"],
        "journal_entry": journal.entry_binding(journal_key(_metadata("observed"))),
    }
    return journal, ledger_path, dispatch, calls


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("response", ["", "CLAIM: preserved Unicode λ"])
def test_completed_marker_cleanup_preserves_success_and_response_without_charge(
        tmp_path, monkeypatch, parallel, response):
    journal, ledger, dispatch, calls = _completed_marker(
        tmp_path, monkeypatch, response=response, parallel=parallel)
    before = ledger.read_bytes(), journal.path.read_bytes()
    results = recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert results[0]["uncertain_cost_usd"] == 0
    assert (ledger.read_bytes(), journal.path.read_bytes()) == before
    assert not journal.dispatch_marker_paths()
    recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert (ledger.read_bytes(), journal.path.read_bytes()) == before

    class RefuseFreshCall:
        def complete(self, *args, **kwargs):
            pytest.fail("observed request was sent again")

    assert _complete(JournalingClient(RefuseFreshCall(), journal, max_concurrent_requests=3),
                     "observed") == response
    assert len(calls) == 1


@pytest.mark.parametrize("mutation", ["response_hash", "success", "missing_journal"])
def test_completed_marker_cleanup_refuses_missing_or_changed_success_evidence(
        tmp_path, monkeypatch, mutation):
    journal, ledger, dispatch, calls = _completed_marker(tmp_path, monkeypatch)
    if mutation == "response_hash":
        dispatch["journal_entry"]["response_raw_sha256"] = "f" * 64
    elif mutation == "success":
        dispatch["terminal"]["cost_usd"] += 1
    else:
        journal.path.unlink()
        journal = RequestJournal(journal.path, execution_identity=journal.execution_identity)
    before = ledger.read_bytes()
    with pytest.raises(JournalDispatchUnresolved, match="exact successful"):
        recover_interrupted_dispatches(
            journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert ledger.read_bytes() == before
    assert journal.dispatch_marker_paths()
    assert len(calls) == 1


def _unreserved_marker(tmp_path, monkeypatch, *, previous_unknown=False):
    ledger = tmp_path / "usage.jsonl"
    api_client.prepare_usage_ledger(ledger, allow_create=True)
    snapshot = api_client.load_chained_usage_ledger(ledger)
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("previous unobserved attempt")

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail)))
    inner = api_client.RejudgeClient(
        approved_cap_usd=5, max_retries=0, halt_on_unknown_charge=True, _sdk_client=sdk,
        usage_log_path=ledger, _ledger_snapshot=snapshot)
    journal = RequestJournal(tmp_path / "journal.jsonl", execution_identity="unreserved")
    client = JournalingClient(inner, journal, max_concurrent_requests=2)
    if previous_unknown:
        with pytest.raises(api_client.UnknownChargeHalt):
            _complete(client, "local-crash")

    def die(*args, **kwargs):
        raise KeyboardInterrupt("crash before accounting reservation")

    with monkeypatch.context() as scoped:
        scoped.setattr(inner, "complete", die)
        with pytest.raises(KeyboardInterrupt):
            _complete(client, "local-crash")
    marker_path, = journal.dispatch_marker_paths()
    marker = json.loads(marker_path.read_bytes())
    # The causal proof must work even if wall-clock timestamps jump backwards.
    marker["created_at"] = "1900-01-01T00:00:00+00:00"
    raw = (json.dumps(marker) + "\n").encode()
    marker_path.write_bytes(raw)
    last = json.loads(ledger.read_bytes().splitlines()[-1])
    dispatch = {
        "disposition": "unreserved_dispatch", "marker_path": str(marker_path),
        "marker_raw_sha256": hashlib.sha256(raw).hexdigest(), "marker": marker,
        "request_sha256": marker["request_sha256"],
        "recovery_ledger_boundary": {field: last[field]
                                     for field in ("ledger_id", "sequence", "event_hash")},
    }
    return journal, ledger, dispatch, inner, sdk, calls


@pytest.mark.parametrize("previous_unknown", [False, True])
def test_unreserved_cleanup_uses_causal_frontier_and_does_not_add_charge(
        tmp_path, monkeypatch, previous_unknown):
    journal, ledger, dispatch, inner, sdk, calls = _unreserved_marker(
        tmp_path, monkeypatch, previous_unknown=previous_unknown)
    prefix = ledger.read_bytes()
    assert len(calls) == int(previous_unknown)
    outcome = recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert outcome[0]["uncertain_cost_usd"] == 0
    assert outcome[0]["attempt_id"] is None
    assert ledger.read_bytes() == prefix
    assert not journal.dispatch_marker_paths()
    recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert ledger.read_bytes() == prefix

    def succeed(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
            choices=[SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="stop")],
            model="judge-model", id="resumed", system_fingerprint=None)

    sdk.chat.completions.create = succeed
    resumed = JournalingClient(inner, journal, max_concurrent_requests=2)
    assert _complete(resumed, "local-crash") == ""
    grown = ledger.read_bytes(), journal.path.read_bytes()
    recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert (ledger.read_bytes(), journal.path.read_bytes()) == grown
    assert len(calls) == 1 + int(previous_unknown)


@pytest.mark.parametrize("mutation", ["new_reservation", "frontier_hash", "legacy_with_prior"])
def test_unreserved_cleanup_refuses_dispatch_after_frontier_or_ambiguous_legacy_marker(
        tmp_path, monkeypatch, mutation):
    journal, ledger, dispatch, inner, sdk, calls = _unreserved_marker(
        tmp_path, monkeypatch, previous_unknown=True)
    if mutation == "new_reservation":
        inner._reserve_attempt(
            model="judge-model", prompt_tokens=1, completion_tokens=256,
            kind="query", seed=7, attempt=0,
            request_metadata={**_metadata("local-crash"),
                              JOURNAL_REQUEST_SHA256_FIELD: _fingerprint("local-crash")})
    elif mutation == "frontier_hash":
        dispatch["marker"]["ledger_boundary"]["event_hash"] = "f" * 64
    else:
        del dispatch["marker"]["ledger_boundary"]
    if mutation != "new_reservation":
        raw = (json.dumps(dispatch["marker"]) + "\n").encode()
        journal.dispatch_marker_paths()[0].write_bytes(raw)
        dispatch["marker_raw_sha256"] = hashlib.sha256(raw).hexdigest()
    before = ledger.read_bytes()
    with pytest.raises(JournalDispatchUnresolved):
        recover_interrupted_dispatches(
            journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert ledger.read_bytes() == before
    assert journal.dispatch_marker_paths()


def test_legacy_unreserved_marker_without_any_prior_attempt_is_provably_unused(tmp_path, monkeypatch):
    journal, ledger, dispatch, inner, sdk, calls = _unreserved_marker(tmp_path, monkeypatch)
    del dispatch["marker"]["ledger_boundary"]
    raw = (json.dumps(dispatch["marker"]) + "\n").encode()
    journal.dispatch_marker_paths()[0].write_bytes(raw)
    dispatch["marker_raw_sha256"] = hashlib.sha256(raw).hexdigest()
    before = ledger.read_bytes()
    recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256="b" * 64)
    assert ledger.read_bytes() == before
    assert not journal.dispatch_marker_paths()
    assert calls == []


@pytest.mark.parametrize("cap_kind", ["aggregate", "uncertain"])
def test_parallel_active_reservations_cannot_overcommit_either_cap(tmp_path, cap_kind):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(timeout=5)
        raise TimeoutError("possible charge")

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    cost = sum(api_client._estimate_usage([{"role": "user", "content": "a"}], 256)) / 1_000_000
    cap = cost * 1.5
    inner = api_client.RejudgeClient(
        approved_cap_usd=cap if cap_kind == "aggregate" else 5,
        run_uncertain_ceiling_usd=cap if cap_kind == "uncertain" else None,
        price_per_mtok=1, _sdk_client=sdk, max_retries=0,
        halt_on_unknown_charge=True)
    client = JournalingClient(inner, RequestJournal(tmp_path / "journal.jsonl"),
                               max_concurrent_requests=2)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(_complete, client, "a")
        try:
            assert entered.wait(timeout=5)
            expected = (api_client.CapExceededError if cap_kind == "aggregate"
                        else api_client.UncertainCeilingHalt)
            with pytest.raises(expected):
                _complete(client, "b")
        finally:
            release.set()
        with pytest.raises(api_client.UnknownChargeHalt):
            first.result(timeout=5)
    assert len(calls) == 1
    assert inner.spent_usd <= cap
