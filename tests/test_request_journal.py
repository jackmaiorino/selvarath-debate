"""Request-journal replay, durability, and ledger-reconciliation tests.

These tests reproduce the replay-divergence mechanism that terminally excluded three cells
from run phase3-v3-82c8f75feba42a9e (terminal-halt records 006 to 008) and prove the
journal closes it: a visibly-empty response is recorded and replayed rather than
re-dispatched, an offline injected interruption leaves a durable refusal marker, and every
ambiguous surviving ledger state is detected instead of silently re-dispatched. Formal-run
resume is prohibited: the 2026-08-29 process reset requires voiding the interrupted identity
and restarting with empty stores.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from rejudge import api_client, config, judge_loop
from rejudge import request_journal as request_journal_module
from rejudge.phase2_call_cache import CallCache, CallKey, request_fingerprint
from rejudge.phase2_caching_client import CachingClient, UncacheableCall
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD, JournalDispatchUnresolved, JournalIdentityMismatch,
    JournalLedgerMismatch, JournalReplayMismatch, JournalWriterLocked, JournalingClient,
    RequestJournal, find_ambiguous_dispatches, journal_key)


TRANSCRIPT = {
    "question_id": "VS-TEST", "transcript_index": 0, "world": "w",
    "question": "Which body governs the region?",
    "correct_answer": "The Council", "wrong_answer": "The Assembly",
    "debate_transcript": [
        {"speaker": "honest", "text": "It is the Council."},
        {"speaker": "dishonest", "text": "It is the Assembly."},
    ],
}

PROTOCOL = {
    "judge": {
        "system_prompt": "You are the judge.",
        "user_prompt_template": (
            "Q: {question}\nA: {position_a}\nB: {position_b}\n{debate_transcript}"),
        "query_phase_prompt": "Budget {remaining_budget}/{total_budget}.\n{previous_queries}",
        "verdict_prompt": "Give verdict.\n{query_results}",
    },
    "oracle": {
        "system_prompt": "Oracle.",
        "user_prompt_template": "DOC: {world_document}\nCLAIM: {query_claim}",
    },
    "protocol": {
        "models": {"oracle": "oracle-model"},
        "temperature": {"judge": 0.3, "oracle": 0.0},
    },
}

QUERY_METADATA = {"cell_key": "cell-1", "call_role": "judge_query", "query_index": 0}


class GenerationProvider:
    """A provider whose outputs are distinguishable per dispatch, like a real sampler.

    ``tag`` marks which process generation produced a response, so a test can tell a
    replayed response from a re-dispatched one by looking at the text itself. ``crash_on``
    kills the process (raises) on the Nth call, before any response exists.
    """

    def __init__(self, tag, crash_on=None):
        self.tag = tag
        self.calls = 0
        self.crash_on = crash_on
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict",
                 **kwargs):
        self.calls += 1
        if self.crash_on is not None and self.calls == self.crash_on:
            raise KeyboardInterrupt(f"simulated death at call {self.calls}")
        if kind == "query":
            return f"CLAIM: fact {self.tag}-{self.calls}"
        if kind == "oracle":
            return "YES"
        return f"VERDICT: Position A\nCONFIDENCE: 4\nREASONING: {self.tag}-{self.calls}"


class EmptyThenTextProvider:
    """First dispatch returns a visibly-empty response, later dispatches return text.

    This is the exact Qwen/VS-019 shape: a truncated call whose visible text is empty,
    which the provider then completes differently on a re-dispatch.
    """

    def __init__(self):
        self.calls = 0
        self.dry_run = False
        self.last_metadata = None

    def complete(self, *args, **kwargs):
        self.calls += 1
        self.last_metadata = kwargs.get("request_metadata")
        return "" if self.calls == 1 else f"CLAIM: a different generation {self.calls}"


def _complete(client, metadata=QUERY_METADATA):
    return client.complete(
        [{"role": "user", "content": "q"}], "judge-model", 0.3, 7, 256, kind="query",
        request_metadata=dict(metadata))


def _canonical(record):
    """The record minus its wall-clock stamp: created_at says when a row was composed, not
    what was composed, and replay equivalence is a claim about the latter."""
    return json.dumps({k: v for k, v in record.items() if k != "created_at"},
                      sort_keys=True)


def _run_cell(client):
    return judge_loop.run_judgment(
        TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 2, 0, client, PROTOCOL,
        judge_model="judge-model", position_override=True,
        cell_key_override="cell-e2e",
        query_template_override=PROTOCOL["judge"]["query_phase_prompt"])


# -- the doctrine inversion ----------------------------------------------------------------


def test_journal_records_and_replays_a_visibly_empty_response(tmp_path):
    provider = EmptyThenTextProvider()
    client = JournalingClient(provider, RequestJournal(tmp_path / "journal.jsonl"))
    assert _complete(client) == ""
    expected_fingerprint = request_fingerprint(
        messages=[{"role": "user", "content": "q"}], model="judge-model",
        temperature=0.3, seed=7, max_tokens=256)
    assert provider.last_metadata[JOURNAL_REQUEST_SHA256_FIELD] == expected_fingerprint
    assert not client.journal.unresolved_marker_path.exists()
    # The re-ask that previously produced a divergent fresh generation now replays the
    # recorded empty: the provider is never consulted again.
    assert _complete(client) == ""
    assert provider.calls == 1


def test_the_cache_doctrine_this_replaces_would_have_redispatched(tmp_path):
    # Contrast pin, not a regression: the cache's refuse-memoise-empties doctrine is the
    # documented root cause of the three terminal cells, and this is it doing so.
    provider = EmptyThenTextProvider()
    client = CachingClient(provider, CallCache(tmp_path / "cache.jsonl"))
    assert _complete(client) == ""
    assert _complete(client).startswith("CLAIM: a different generation")
    assert provider.calls == 2


def test_a_failed_call_latches_the_client_against_blind_redispatch(tmp_path):
    class FailsOnce:
        def __init__(self):
            self.calls = 0
            self.dry_run = False

        def complete(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("simulated provider timeout")
            return "CLAIM: recovered"

    provider = FailsOnce()
    client = JournalingClient(provider, RequestJournal(tmp_path / "journal.jsonl"))
    with pytest.raises(TimeoutError):
        _complete(client)
    assert not client.journal.has(journal_key(QUERY_METADATA))
    assert client.journal.unresolved_marker_path.exists()
    with pytest.raises(JournalDispatchUnresolved, match="surviving provider-capable"):
        _complete(client)
    assert provider.calls == 1


def test_unresolved_marker_blocks_a_second_preopened_wrapper(tmp_path):
    class FailsThenSucceeds:
        def __init__(self):
            self.calls = 0
            self.dry_run = False

        def complete(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("ambiguous first call")
            return "CLAIM: unsafe substitute"

    provider = FailsThenSucceeds()
    path = tmp_path / "journal.jsonl"
    first = JournalingClient(provider, RequestJournal(path, execution_identity="run-1"))
    second = JournalingClient(provider, RequestJournal(path, execution_identity="run-1"))
    with pytest.raises(TimeoutError):
        _complete(first)
    with pytest.raises(JournalDispatchUnresolved, match="surviving provider-capable"):
        _complete(second)
    assert provider.calls == 1
    assert not path.exists()


def test_failure_after_marker_fsync_blocks_without_calling_provider(tmp_path, monkeypatch):
    provider = EmptyThenTextProvider()
    path = tmp_path / "journal.jsonl"
    journal = RequestJournal(path, execution_identity="run-marker")
    original_begin = journal._begin_dispatch

    def begin_then_interrupt(key, fingerprint):
        original_begin(key, fingerprint)
        raise KeyboardInterrupt("after marker fsync")

    monkeypatch.setattr(journal, "_begin_dispatch", begin_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        _complete(JournalingClient(provider, journal))
    assert provider.calls == 0
    assert journal.unresolved_marker_path.exists()
    with pytest.raises(JournalDispatchUnresolved):
        _complete(JournalingClient(
            provider, RequestJournal(path, execution_identity="run-marker")))
    assert provider.calls == 0


def test_accounted_success_before_journal_append_stays_ambiguous(
        tmp_path, monkeypatch):
    class CountingSDK:
        def __init__(self):
            self.calls = 0
            outer = self

            class Completions:
                @staticmethod
                def create(**kwargs):
                    outer.calls += 1
                    return SimpleNamespace(
                        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
                        choices=[SimpleNamespace(
                            message=SimpleNamespace(content="CLAIM: accounted"),
                            finish_reason="stop")],
                        model="judge-model", id="response-1", system_fingerprint=None)

            self.chat = SimpleNamespace(completions=Completions())

    sdk = CountingSDK()
    inner = api_client.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, max_retries=0)
    path = tmp_path / "journal.jsonl"
    journal = RequestJournal(path, execution_identity="run-settled")

    def interrupt_append(*args):
        raise KeyboardInterrupt("after ledger success, before journal append")

    monkeypatch.setattr(journal, "_put_guarded", interrupt_append)
    with pytest.raises(KeyboardInterrupt):
        _complete(JournalingClient(inner, journal))
    assert sdk.calls == 1
    assert [event["status"] for event in inner.usage_events] == ["reserved", "success"]
    assert journal.unresolved_marker_path.exists()
    findings = find_ambiguous_dispatches(journal, inner.usage_events)
    assert [finding["problem"] for finding in findings] == [
        "success_without_journal_entry"]
    with pytest.raises(JournalDispatchUnresolved):
        _complete(JournalingClient(
            inner, RequestJournal(path, execution_identity="run-settled")))
    assert sdk.calls == 1


def test_failure_after_journal_fsync_leaves_row_and_marker_blocking_replay(
        tmp_path, monkeypatch):
    provider = EmptyThenTextProvider()
    path = tmp_path / "journal.jsonl"
    journal = RequestJournal(path, execution_identity="run-after-journal")

    def fail_marker_removal(expected_marker):
        raise OSError("simulated marker removal failure")

    monkeypatch.setattr(journal, "_finish_dispatch", fail_marker_removal)
    with pytest.raises(OSError, match="marker removal"):
        _complete(JournalingClient(provider, journal))
    assert provider.calls == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert journal.unresolved_marker_path.exists()
    with pytest.raises(JournalDispatchUnresolved):
        _complete(JournalingClient(
            provider, RequestJournal(path, execution_identity="run-after-journal")))
    assert provider.calls == 1


def test_keyboard_interrupt_after_marker_unlink_restores_path_wide_refusal(
        tmp_path, monkeypatch):
    provider = EmptyThenTextProvider()
    path = tmp_path / "journal.jsonl"
    journal = RequestJournal(path, execution_identity="run-unlink-interrupt")
    original_fsync_parent = request_journal_module._fsync_parent_directory
    fsync_calls = 0

    def interrupt_third_parent_fsync(target):
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 3:
            raise KeyboardInterrupt("after marker unlink")
        original_fsync_parent(target)

    monkeypatch.setattr(
        request_journal_module, "_fsync_parent_directory", interrupt_third_parent_fsync)
    with pytest.raises(KeyboardInterrupt, match="after marker unlink"):
        _complete(JournalingClient(provider, journal))
    assert provider.calls == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert journal.unresolved_marker_path.exists()
    with pytest.raises(JournalDispatchUnresolved):
        _complete(JournalingClient(
            provider, RequestJournal(path, execution_identity="run-unlink-interrupt")))
    assert provider.calls == 1


def test_concurrent_same_key_calls_share_one_dispatch(tmp_path):
    class SlowProvider:
        def __init__(self):
            self.calls = 0
            self.dry_run = False

        def complete(self, *args, **kwargs):
            self.calls += 1
            time.sleep(0.02)
            return "CLAIM: one generation"

    provider = SlowProvider()
    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(provider, journal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _complete(client), range(2)))
    assert results == ["CLAIM: one generation", "CLAIM: one generation"]
    assert provider.calls == 1
    assert journal.recorded == 1
    assert journal.replayed == 1


def test_two_preopened_wrappers_cannot_dispatch_concurrently(tmp_path):
    class SlowProvider:
        def __init__(self):
            self.calls = 0
            self.dry_run = False
            self.entered = threading.Event()
            self.release = threading.Event()

        def complete(self, *args, **kwargs):
            self.calls += 1
            self.entered.set()
            assert self.release.wait(timeout=2)
            return "CLAIM: sole writer"

    provider = SlowProvider()
    path = tmp_path / "journal.jsonl"
    clients = [
        JournalingClient(provider, RequestJournal(path)),
        JournalingClient(provider, RequestJournal(path)),
    ]

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(_complete, clients[0])
        assert provider.entered.wait(timeout=2)
        with pytest.raises(JournalWriterLocked):
            _complete(clients[1])
        provider.release.set()
        assert first.result(timeout=2) == "CLAIM: sole writer"
    assert provider.calls == 1
    assert [json.loads(line)["sequence"] for line in path.read_text(
        encoding="utf-8").splitlines()] == [0]


# -- chain discipline ----------------------------------------------------------------------


def test_journal_refuses_overwrite_and_mismatched_fingerprint(tmp_path):
    journal = RequestJournal(tmp_path / "journal.jsonl")
    key = journal_key(QUERY_METADATA)
    fingerprint = request_fingerprint(
        messages=[{"role": "user", "content": "q"}], model="judge-model", temperature=0.3,
        seed=7, max_tokens=256)
    journal.put(key, fingerprint, "response")
    with pytest.raises(ValueError, match="already journaled"):
        journal.put(key, fingerprint, "another")
    with pytest.raises(JournalReplayMismatch):
        journal.get(key, "0" * 64)


def test_journal_reloads_and_refuses_foreign_identity_and_tampering(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = RequestJournal(path, execution_identity="run-a:formal")
    key = journal_key(QUERY_METADATA)
    journal.put(key, "f" * 64, "response one")
    reloaded = RequestJournal(path, execution_identity="run-a:formal")
    assert reloaded.get(key, "f" * 64) == "response one"

    with pytest.raises(JournalIdentityMismatch):
        RequestJournal(path, execution_identity="run-b:formal")

    rows = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(rows[0])
    tampered["response"] = "edited"
    path.write_text(json.dumps(tampered, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tampered"):
        RequestJournal(path, execution_identity="run-a:formal")


def test_journal_rejects_sequence_gaps_duplicate_keys_and_torn_rows(tmp_path):
    gap_path = tmp_path / "gap.jsonl"
    journal = RequestJournal(gap_path)
    journal.put(journal_key(QUERY_METADATA), "a" * 64, "response")
    gap_row = json.loads(gap_path.read_text(encoding="utf-8"))
    gap_row["sequence"] = 2
    gap_path.write_text(json.dumps(gap_row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sequence broken"):
        RequestJournal(gap_path)

    duplicate_path = tmp_path / "duplicate.jsonl"
    duplicate = RequestJournal(duplicate_path)
    first_key = journal_key(QUERY_METADATA)
    second_key = journal_key({
        "cell_key": "cell-2", "call_role": "judge_query", "query_index": 0,
    })
    duplicate.put(first_key, "b" * 64, "one")
    duplicate.put(second_key, "c" * 64, "two")
    duplicate_rows = [
        json.loads(line)
        for line in duplicate_path.read_text(encoding="utf-8").splitlines()
    ]
    for field in ("cell_key", "call_role", "slot", "attempt"):
        duplicate_rows[1][field] = duplicate_rows[0][field]
    duplicate_rows[1]["event_hash"] = RequestJournal._row_hash(duplicate_rows[1])
    duplicate_path.write_text(
        "\n".join(json.dumps(row) for row in duplicate_rows) + "\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="repeats call identity"):
        RequestJournal(duplicate_path)

    torn_path = tmp_path / "torn.jsonl"
    torn_path.write_text('{"cell_key": "cell-1"', encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete or invalid JSON row"):
        RequestJournal(torn_path)


def test_journal_rejects_non_string_responses_and_invalid_persisted_schema(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = RequestJournal(path)
    key = journal_key(QUERY_METADATA)
    with pytest.raises(ValueError, match="response must be a string"):
        journal.put(key, "a" * 64, None)

    journal.put(key, "a" * 64, "response")
    row = json.loads(path.read_text(encoding="utf-8"))
    row["response"] = None
    row["event_hash"] = RequestJournal._row_hash(row)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="response must be a string"):
        RequestJournal(path)


def test_journal_key_requires_cell_and_role_metadata():
    with pytest.raises(UncacheableCall):
        journal_key(None)
    with pytest.raises(UncacheableCall):
        journal_key({"cell_key": "cell-1"})


# -- crash/resume equivalence through the real judgment loop -------------------------------


def test_an_offline_interruption_blocks_fresh_wrapper_dispatch(tmp_path):
    path = tmp_path / "journal.jsonl"

    # Generation 1 dies mid-cell, at the second oracle call: three responses journaled.
    gen1 = GenerationProvider("gen1", crash_on=4)
    with pytest.raises(KeyboardInterrupt):
        _run_cell(JournalingClient(gen1, RequestJournal(path)))
    assert gen1.calls == 4

    # A fresh wrapper cannot turn the missing suffix into substitute samples. The completed
    # prefix remains available for diagnosis through RequestJournal, but this formal identity
    # cannot dispatch again.
    gen2 = GenerationProvider("gen2")
    with pytest.raises(JournalDispatchUnresolved):
        _run_cell(JournalingClient(gen2, RequestJournal(path)))
    assert gen2.calls == 0
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_completed_run_replays_byte_identically_across_wrappers(tmp_path):
    path = tmp_path / "journal.jsonl"
    record = _run_cell(JournalingClient(
        GenerationProvider("gen1"), RequestJournal(path)))
    replay_provider = GenerationProvider("different")
    replayed = _run_cell(JournalingClient(replay_provider, RequestJournal(path)))
    assert replay_provider.calls == 0
    assert _canonical(replayed) == _canonical(record)


# -- ledger/journal reconciliation ---------------------------------------------------------


def _ledger_attempt(attempt_id, status, sequence, metadata):
    reservation = {
        "status": "reserved", "attempt_id": attempt_id, "sequence": sequence,
        "metadata": dict(metadata),
    }
    terminal = {
        "status": status, "attempt_id": attempt_id, "sequence": sequence + 1,
        "metadata": dict(metadata),
    }
    return [reservation, terminal]


def test_ambiguous_dispatch_detection(tmp_path):
    journal = RequestJournal(tmp_path / "journal.jsonl")
    journaled_metadata = {"cell_key": "cell-1", "call_role": "judge_query",
                          "query_index": 0,
                          JOURNAL_REQUEST_SHA256_FIELD: "f" * 64}
    journal.put(journal_key(journaled_metadata), "f" * 64, "recorded")

    missing_metadata = {
        "cell_key": "cell-2", "call_role": "judge_verdict",
        JOURNAL_REQUEST_SHA256_FIELD: "a" * 64,
    }
    unknown_metadata = {
        "cell_key": "cell-3", "call_role": "judge_query", "query_index": 0,
        JOURNAL_REQUEST_SHA256_FIELD: "b" * 64,
    }
    malformed_metadata = {
        "cell_key": "cell-4", "call_role": "judge_verdict",
        JOURNAL_REQUEST_SHA256_FIELD: "c" * 64,
    }
    released_metadata = {
        "cell_key": "cell-5", "call_role": "judge_query", "query_index": 0,
        JOURNAL_REQUEST_SHA256_FIELD: "d" * 64,
    }
    open_metadata = {
        "cell_key": "cell-6", "call_role": "judge_query", "query_index": 0,
        JOURNAL_REQUEST_SHA256_FIELD: "e" * 64,
    }
    ledger = []
    ledger += _ledger_attempt("clean-1", "success", 10, journaled_metadata)
    ledger += _ledger_attempt("missing-1", "success", 12, missing_metadata)
    ledger += _ledger_attempt("unknown-1", "unknown_charge", 14, unknown_metadata)
    ledger += _ledger_attempt("malformed-1", "charged_malformed", 16,
                              malformed_metadata)
    ledger += _ledger_attempt("released-1", "released_no_charge", 18,
                              released_metadata)
    ledger.append({
        "status": "reserved", "attempt_id": "open-1", "sequence": 20,
        "metadata": open_metadata,
    })
    # An unkeyed harness probe is accepted only through an explicit attempt-ID allowlist.
    ledger += _ledger_attempt("probe-1", "success", 21, {})

    with pytest.raises(JournalLedgerMismatch, match="not journal-bound"):
        find_ambiguous_dispatches(journal, ledger)
    findings = find_ambiguous_dispatches(
        journal, ledger,
        allowed_unjournaled_attempt_ids=frozenset({"probe-1"}))
    assert {(finding["cell_key"], finding["problem"]) for finding in findings} == {
        ("cell-2", "success_without_journal_entry"),
        ("cell-3", "unknown_charge"),
        ("cell-4", "charged_malformed_response"),
        ("cell-6", "reservation_without_terminal_event"),
    }

    # A second settled success for a journaled key cannot happen under the journal regime;
    # if the ledger shows one, that is a violation to halt on, not a retry to tolerate.
    ledger += _ledger_attempt("clean-2", "success", 23, journaled_metadata)
    findings = find_ambiguous_dispatches(
        journal, ledger,
        allowed_unjournaled_attempt_ids=frozenset({"probe-1"}))
    duplicate = [
        finding for finding in findings
        if finding["problem"] == "duplicate_success_for_key"
    ]
    assert len(duplicate) == 1
    assert duplicate[0]["cell_key"] == "cell-1"
    assert duplicate[0]["success_events"] == 2


def test_reconciliation_requires_exact_request_binding_and_well_formed_lifecycle(tmp_path):
    journal = RequestJournal(tmp_path / "journal.jsonl")
    metadata = {
        "cell_key": "cell-1", "call_role": "judge_verdict",
        JOURNAL_REQUEST_SHA256_FIELD: "a" * 64,
    }
    journal.put(journal_key(metadata), "b" * 64, "recorded")
    findings = find_ambiguous_dispatches(
        journal, _ledger_attempt("attempt-1", "success", 0, metadata))
    assert [finding["problem"] for finding in findings] == [
        "ledger_journal_request_fingerprint_mismatch"]

    unbound = {"cell_key": "cell-1", "call_role": "judge_verdict"}
    findings = find_ambiguous_dispatches(
        journal, _ledger_attempt("attempt-2", "success", 2, unbound))
    assert [finding["problem"] for finding in findings] == [
        "success_without_valid_request_fingerprint_binding"]

    bad_lifecycle = [{
        "status": "success", "attempt_id": "orphan", "sequence": 0,
        "metadata": metadata,
    }]
    with pytest.raises(JournalLedgerMismatch, match="has no reservation"):
        find_ambiguous_dispatches(journal, bad_lifecycle)


def test_accounted_client_ledger_events_bind_and_reconcile_to_the_journal(tmp_path):
    class StubSDK:
        def __init__(self):
            response = SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="CLAIM: accounted"),
                    finish_reason="stop")],
                model="judge-model", id="response-1", system_fingerprint=None)
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response))

    inner = api_client.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=StubSDK(), max_retries=0)
    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(inner, journal)
    assert _complete(client) == "CLAIM: accounted"
    assert [event["status"] for event in inner.usage_events] == ["reserved", "success"]
    assert inner.usage_events[0]["metadata"][JOURNAL_REQUEST_SHA256_FIELD] == (
        journal.request_sha256(journal_key(QUERY_METADATA)))
    assert find_ambiguous_dispatches(journal, inner.usage_events) == []


def test_accounted_timeout_is_unknown_and_never_freely_redispatched(tmp_path):
    class FailingSDK:
        def __init__(self):
            class Completions:
                @staticmethod
                def create(**kwargs):
                    raise TimeoutError("simulated transport timeout")

            self.chat = SimpleNamespace(completions=Completions())

    inner = api_client.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=FailingSDK(), max_retries=0)
    journal = RequestJournal(tmp_path / "journal.jsonl")
    client = JournalingClient(inner, journal)
    with pytest.raises(RuntimeError, match="API call failed"):
        _complete(client)
    assert [event["status"] for event in inner.usage_events] == [
        "reserved", "unknown_charge"]
    findings = find_ambiguous_dispatches(journal, inner.usage_events)
    assert [finding["problem"] for finding in findings] == ["unknown_charge"]
    with pytest.raises(JournalDispatchUnresolved):
        _complete(client)

