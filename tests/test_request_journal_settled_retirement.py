"""Recovery retires only a fully settled legacy marker, without new paid work."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rejudge import api_client
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD, JournalDispatchUnresolved, JournalingClient,
    RequestJournal, journal_key, recover_interrupted_dispatches,
    validate_settled_unknown_history, validate_settled_unknown_retirement,
)


RECOVERY = "b" * 64


def _call(client):
    return client.complete(
        [{"role": "user", "content": "fixture"}], "judge-model", 0.0, 7, 256,
        kind="query", request_metadata={"cell_key": "cell", "call_role": "judge_query",
                                         "query_index": 0})


def _fixture(tmp_path):
    ledger = tmp_path / "usage.jsonl"
    api_client.prepare_usage_ledger(ledger, allow_create=True)

    def fail(**kwargs):
        raise TimeoutError("unobserved fixture request")

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail)))
    inner = api_client.RejudgeClient(
        approved_cap_usd=5, max_retries=0, halt_on_unknown_charge=True,
        _sdk_client=sdk, usage_log_path=ledger,
        _ledger_snapshot=api_client.load_chained_usage_ledger(ledger))
    # Reproduce a wrapper that omitted the causal-frontier forwarding method.
    wrapper = SimpleNamespace(complete=inner.complete)
    journal = RequestJournal(tmp_path / "journal.jsonl", execution_identity="settled-run")
    client = JournalingClient(wrapper, journal, max_concurrent_requests=2)
    for _ in range(3):
        with pytest.raises(api_client.UnknownChargeHalt):
            _call(client)

    def refuse(*args, **kwargs):
        raise api_client.UncertainCeilingHalt("fixture cap refuses before reservation")

    wrapper.complete = refuse
    with pytest.raises(api_client.UncertainCeilingHalt):
        _call(client)
    marker_path, = journal.dispatch_marker_paths()
    raw = marker_path.read_bytes()
    marker = json.loads(raw)
    assert "ledger_boundary" not in marker
    events = api_client._read_usage_events(ledger)
    dispatch = {
        "disposition": "settled_unknown_history_no_response",
        "marker_path": str(marker_path), "marker_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "marker": marker, "request_sha256": marker["request_sha256"],
        "settled_attempts": [{"reservation": events[n], "terminal": events[n + 1]}
                             for n in (1, 3, 5)],
        "recovery_ledger_boundary": {k: events[-1][k]
                                     for k in ("ledger_id", "sequence", "event_hash")},
        "retirement_receipt_path": str(journal.path.with_name(
            f"{journal.path.name}.retired-{hashlib.sha256(raw).hexdigest()}.json")),
    }
    return journal, ledger, dispatch, inner, sdk


def _recover(journal, ledger, dispatch):
    return recover_interrupted_dispatches(
        journal, ledger, [dispatch], recovery_manifest_sha256=RECOVERY)


def test_retirement_preserves_all_three_charges_and_exact_original_marker(tmp_path):
    journal, ledger, dispatch, inner, sdk = _fixture(tmp_path)
    before = ledger.read_bytes()
    original_marker = journal.dispatch_marker_paths()[0].read_bytes()
    outcome, = _recover(journal, ledger, dispatch)
    assert outcome["uncertain_cost_usd"] == 0 and outcome["attempt_id"] is None
    assert ledger.read_bytes() == before
    assert not journal.dispatch_marker_paths()
    receipt = validate_settled_unknown_retirement(
        journal.path, api_client._read_usage_events(ledger), [], dispatch, RECOVERY)
    assert base64.b64decode(receipt["marker_raw_base64"]) == original_marker
    assert receipt["new_uncertain_cost_usd"] == 0
    assert len(receipt["settled_attempt_ids"]) == 3
    receipt_path = Path(dispatch["retirement_receipt_path"])
    receipt_before = receipt_path.read_bytes(), receipt_path.stat().st_mtime_ns
    assert _recover(journal, ledger, dispatch) == [outcome]
    assert (receipt_path.read_bytes(), receipt_path.stat().st_mtime_ns) == receipt_before
    assert ledger.read_bytes() == before

    def succeed(**kwargs):
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
            choices=[SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="stop")],
            model="judge-model", id="fixture-response", system_fingerprint=None)

    sdk.chat.completions.create = succeed
    assert _call(JournalingClient(inner, journal, max_concurrent_requests=2)) == ""
    grown = ledger.read_bytes(), journal.path.read_bytes()
    _recover(journal, ledger, dispatch)
    assert (ledger.read_bytes(), journal.path.read_bytes()) == grown
    assert api_client.load_chained_usage_ledger(ledger).summary["uncertain_spend_usd"] == pytest.approx(
        sum(pair["reservation"]["cost_usd"] for pair in dispatch["settled_attempts"]))


@pytest.mark.parametrize("after_remove", [False, True])
def test_retirement_crash_before_or_after_marker_removal_is_idempotent(
        tmp_path, monkeypatch, after_remove):
    journal, ledger, dispatch, _, _ = _fixture(tmp_path)
    before = ledger.read_bytes()
    finish = journal._finish_dispatch

    def crash(marker):
        if after_remove:
            finish(marker)
        raise KeyboardInterrupt("crash after durable retirement receipt")

    with monkeypatch.context() as scoped:
        scoped.setattr(journal, "_finish_dispatch", crash)
        with pytest.raises(KeyboardInterrupt):
            _recover(journal, ledger, dispatch)
    assert bool(journal.dispatch_marker_paths()) != after_remove
    assert ledger.read_bytes() == before
    _recover(journal, ledger, dispatch)
    assert not journal.dispatch_marker_paths()
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("mutation", ["history_omitted", "history_changed", "marker_bytes",
                                     "boundary", "receipt_path", "missing_marker", "response"])
def test_retirement_refuses_changed_or_unproven_snapshot(tmp_path, mutation):
    journal, ledger, dispatch, _, _ = _fixture(tmp_path)
    if mutation == "history_omitted":
        dispatch["settled_attempts"].pop()
    elif mutation == "history_changed":
        dispatch["settled_attempts"][0]["terminal"]["error"] = "different history"
    elif mutation == "marker_bytes":
        journal.dispatch_marker_paths()[0].write_bytes(b"{}\n")
    elif mutation == "boundary":
        dispatch["recovery_ledger_boundary"]["event_hash"] = "c" * 64
    elif mutation == "receipt_path":
        dispatch["retirement_receipt_path"] = str(tmp_path / "wrong.json")
    elif mutation == "missing_marker":
        journal.dispatch_marker_paths()[0].unlink()
    else:
        with journal.dispatch_guard():
            journal._put_guarded(journal_key(dispatch["marker"]), dispatch["request_sha256"], "")
    before = ledger.read_bytes()
    with pytest.raises((JournalDispatchUnresolved, api_client.UsageLedgerError)):
        _recover(journal, ledger, dispatch)
    assert ledger.read_bytes() == before
    assert not list(tmp_path.glob("*.retired-*.json"))


@pytest.mark.parametrize("mutation", ["success", "released", "fingerprint", "observed_tokens",
                                     "response_metadata", "empty_content", "partial_charge", "open_other"])
def test_settled_history_requires_full_unknown_cost_for_every_attempt_and_no_open(
        tmp_path, mutation):
    _, ledger, dispatch, _, _ = _fixture(tmp_path)
    events = copy.deepcopy(api_client._read_usage_events(ledger))
    if mutation == "success":
        events[2]["status"] = "success"
    elif mutation == "released":
        events[2].update(status="released_no_charge", cost_usd=0)
    elif mutation == "fingerprint":
        for n in (1, 2):
            events[n]["metadata"][JOURNAL_REQUEST_SHA256_FIELD] = "f" * 64
    elif mutation == "observed_tokens":
        events[2]["prompt_tokens"] = 10
    elif mutation == "response_metadata":
        events[2]["response_metadata"] = None
    elif mutation == "empty_content":
        events[2]["content"] = ""
    elif mutation == "partial_charge":
        events[2]["cost_usd"] *= 0.5
    else:
        extra = copy.deepcopy(events[1])
        extra["attempt_id"] = "another-open-request"
        extra["metadata"]["cell_key"] = "another-cell"
        events.append(extra)
    previous = None
    for sequence, event in enumerate(events):
        event.update(sequence=sequence, prev_event_hash=previous)
        event["event_hash"] = api_client._usage_event_hash(event)
        previous = event["event_hash"]
    with pytest.raises((JournalDispatchUnresolved, api_client.UsageLedgerError)):
        validate_settled_unknown_history(events, dispatch["marker"])


def test_retirement_rejects_new_history_after_signed_frontier(tmp_path):
    journal, ledger, dispatch, inner, _ = _fixture(tmp_path)
    with pytest.raises(api_client.UnknownChargeHalt):
        _call(inner)
    before = ledger.read_bytes()
    with pytest.raises(JournalDispatchUnresolved, match="new ledger history"):
        _recover(journal, ledger, dispatch)
    assert ledger.read_bytes() == before
    assert journal.dispatch_marker_paths()


@pytest.mark.parametrize("mutation", ["corrupt", "missing", "different_recovery"])
def test_removed_marker_requires_its_exact_durable_receipt(tmp_path, mutation):
    journal, ledger, dispatch, _, _ = _fixture(tmp_path)
    _recover(journal, ledger, dispatch)
    path = Path(dispatch["retirement_receipt_path"])
    if mutation == "corrupt":
        path.write_bytes(b"{}\n")
    elif mutation == "missing":
        path.unlink()
    before = ledger.read_bytes()
    with pytest.raises(JournalDispatchUnresolved):
        recover_interrupted_dispatches(journal, ledger, [dispatch],
            recovery_manifest_sha256="c" * 64 if mutation == "different_recovery" else RECOVERY)
    assert ledger.read_bytes() == before
