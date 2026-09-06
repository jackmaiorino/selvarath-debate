"""Amendment 14: an attempt-matched durable unknown charge releases the journal marker.

A strict accounting client (``halt_on_unknown_charge=True``) records ``unknown_charge`` and
raises ``UnknownChargeHalt`` carrying the exact ledger event. Only that shape may release
the unresolved-dispatch marker; the key then stays dispatchable under a new attempt id, the
reservation stays booked as uncertain, and every other failure keeps the latch.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from rejudge import api_client
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD,
    JournalDispatchUnresolved,
    JournalingClient,
    RequestJournal,
    find_ambiguous_dispatches,
    journal_key,
)

QUERY_METADATA = {"cell_key": "cell-1", "call_role": "judge_query", "query_index": 0}


def _complete(client, metadata=QUERY_METADATA):
    return client.complete(
        [{"role": "user", "content": "q"}], "judge-model", 0.3, 7, 256, kind="query",
        request_metadata=dict(metadata))


class _FailsThenSucceeds:
    """SDK fake: the first call times out at the transport, later calls succeed."""

    def __init__(self, failures: int = 1):
        self.calls = 0
        self._failures = failures
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="CLAIM: recovered"),
                finish_reason="stop")],
            model="judge-model", id="response-1", system_fingerprint=None)

        def create(**kwargs):
            self.calls += 1
            if self.calls <= self._failures:
                raise TimeoutError("simulated transport timeout")
            return response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _strict_client(sdk, **overrides):
    kwargs = dict(
        approved_cap_usd=5.0, _sdk_client=sdk, max_retries=0,
        halt_on_unknown_charge=True)
    kwargs.update(overrides)
    return api_client.RejudgeClient(**kwargs)


def test_strict_unknown_charge_releases_marker_and_redispatches_under_new_attempt(tmp_path):
    sdk = _FailsThenSucceeds()
    inner = _strict_client(sdk)
    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(inner, journal)

    with pytest.raises(api_client.UnknownChargeHalt) as halted:
        _complete(client)
    halt = halted.value
    assert halt.attempt_id and halt.model == "judge-model"
    assert halt.ledger_event["status"] == "unknown_charge"
    assert halt.ledger_event["attempt_id"] == halt.attempt_id
    # The marker is released; the key has no journal entry; the client is not latched.
    assert not journal.unresolved_marker_path.exists()
    assert not journal.has(journal_key(QUERY_METADATA))
    key = journal_key(QUERY_METADATA)
    assert client.resolved_unknown_charges == [{
        "cell_key": "cell-1", "call_role": "judge_query", "slot": key.slot,
        "attempt": key.attempt,
        "attempt_id": halt.attempt_id, "model": "judge-model",
        "request_sha256": inner.usage_events[0]["metadata"][JOURNAL_REQUEST_SHA256_FIELD],
    }]

    assert _complete(client) == "CLAIM: recovered"
    statuses = [event["status"] for event in inner.usage_events]
    assert statuses == ["reserved", "unknown_charge", "reserved", "success"]
    first, retry = inner.usage_events[0]["attempt_id"], inner.usage_events[2]["attempt_id"]
    assert first == halt.attempt_id and retry != first
    # Same exact request, seed, and key on the redispatch.
    assert inner.usage_events[2]["metadata"] == inner.usage_events[0]["metadata"]
    assert inner.usage_events[2]["seed"] == inner.usage_events[0]["seed"]
    assert sdk.calls == 2
    # The uncertain reservation stays booked; only the tolerated finding remains.
    assert inner.usage_events[1]["cost_usd"] > 0
    findings = find_ambiguous_dispatches(journal, inner.usage_events)
    assert [finding["problem"] for finding in findings] == ["unknown_charge"]
    assert findings[0]["attempt_id"] == first
    # The journaled response replays without a third dispatch.
    assert _complete(client) == "CLAIM: recovered"
    assert sdk.calls == 2


def test_forged_halt_without_ledger_event_keeps_the_latch(tmp_path):
    class Forging:
        dry_run = False

        def complete(self, *args, **kwargs):
            raise api_client.UnknownChargeHalt("no accounting evidence attached")

    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(Forging(), journal)
    with pytest.raises(api_client.UnknownChargeHalt):
        _complete(client)
    assert journal.unresolved_marker_path.exists()
    with pytest.raises(JournalDispatchUnresolved):
        _complete(client)


def test_halt_for_a_different_key_or_fingerprint_keeps_the_latch(tmp_path):
    sdk = _FailsThenSucceeds()
    inner = _strict_client(sdk)

    class Mislabeling:
        """Passes the halt through but with the ledger event pointing elsewhere."""
        dry_run = False

        def complete(self, *args, **kwargs):
            try:
                return inner.complete(*args, **kwargs)
            except api_client.UnknownChargeHalt as halt:
                halt.ledger_event = dict(halt.ledger_event)
                halt.ledger_event["metadata"] = {
                    **halt.ledger_event["metadata"], "cell_key": "cell-other"}
                raise

    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(Mislabeling(), journal)
    with pytest.raises(api_client.UnknownChargeHalt):
        _complete(client)
    assert journal.unresolved_marker_path.exists()
    with pytest.raises(JournalDispatchUnresolved):
        _complete(client)
    assert sdk.calls == 1


def test_unknown_charge_with_usage_or_response_keeps_the_latch(tmp_path):
    sdk = _FailsThenSucceeds()
    inner = _strict_client(sdk)

    class ContentBearing:
        dry_run = False

        def complete(self, *args, **kwargs):
            try:
                return inner.complete(*args, **kwargs)
            except api_client.UnknownChargeHalt as halt:
                halt.ledger_event = {**halt.ledger_event, "completion_tokens": 3}
                raise

    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(ContentBearing(), journal)
    with pytest.raises(api_client.UnknownChargeHalt):
        _complete(client)
    assert journal.unresolved_marker_path.exists()


def test_uncertain_ceiling_halts_before_dispatch_and_keeps_the_marker_clean(tmp_path):
    sdk = _FailsThenSucceeds(failures=5)
    inner = _strict_client(sdk, run_uncertain_ceiling_usd=0.0006)
    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(inner, journal)
    with pytest.raises(api_client.UnknownChargeHalt):
        _complete(client)
    assert not journal.unresolved_marker_path.exists()
    # The next reservation would exceed the ceiling: refused before any provider call,
    # and the marker was never written for it.
    with pytest.raises(api_client.UncertainCeilingHalt):
        _complete(client)
    assert sdk.calls == 1
    assert [event["status"] for event in inner.usage_events] == [
        "reserved", "unknown_charge"]
    # A ceiling halt is not an unknown charge: the wrapper latches on it.
    assert journal.unresolved_marker_path.exists()


def test_non_strict_client_failure_still_latches(tmp_path):
    inner = api_client.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=_FailsThenSucceeds(), max_retries=0)
    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(inner, journal)
    with pytest.raises(RuntimeError, match="API call failed"):
        _complete(client)
    assert journal.unresolved_marker_path.exists()
    with pytest.raises(JournalDispatchUnresolved):
        _complete(client)
