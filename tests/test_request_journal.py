"""The request journal's contract: dispatch at most once ever, replay byte-identically.

These tests reproduce the replay-divergence mechanism that terminally excluded three cells
from run phase3-v3-82c8f75feba42a9e (terminal-halt records 006 to 008) and prove the
journal closes it: a visibly-empty response is recorded and replayed rather than
re-dispatched, a killed cell resumes into a bit-identical record, and the one crash window
the client cannot close (ledger settled, journal not written) is detected for INVALID
disposition instead of silent re-dispatch.
"""
import json

import pytest

from rejudge import config, judge_loop
from rejudge.phase2_call_cache import CallCache, CallKey, request_fingerprint
from rejudge.phase2_caching_client import CachingClient, UncacheableCall
from rejudge.request_journal import (
    JournalIdentityMismatch, JournalReplayMismatch, JournalingClient, RequestJournal,
    find_ambiguous_dispatches, journal_key)


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

    def complete(self, *args, **kwargs):
        self.calls += 1
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


def test_a_failed_call_journals_nothing_and_stays_dispatchable(tmp_path):
    class FailsOnce:
        def __init__(self):
            self.calls = 0
            self.dry_run = False

        def complete(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("provider timeout, no response bytes ever existed")
            return "CLAIM: recovered"

    provider = FailsOnce()
    client = JournalingClient(provider, RequestJournal(tmp_path / "journal.jsonl"))
    with pytest.raises(TimeoutError):
        _complete(client)
    assert not client.journal.has(journal_key(QUERY_METADATA))
    assert _complete(client) == "CLAIM: recovered"
    assert provider.calls == 2


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


def test_journal_key_requires_cell_and_role_metadata():
    with pytest.raises(UncacheableCall):
        journal_key(None)
    with pytest.raises(UncacheableCall):
        journal_key({"cell_key": "cell-1"})


# -- crash/resume equivalence through the real judgment loop -------------------------------


def test_a_killed_cell_resumes_into_a_bit_identical_record(tmp_path):
    path = tmp_path / "journal.jsonl"

    # Generation 1 dies mid-cell, at the second oracle call: three responses journaled.
    gen1 = GenerationProvider("gen1", crash_on=4)
    with pytest.raises(KeyboardInterrupt):
        _run_cell(JournalingClient(gen1, RequestJournal(path)))
    assert gen1.calls == 4

    # Generation 2 would answer every prompt differently; it is only ever asked the calls
    # generation 1 never completed (the second oracle call and the verdict).
    gen2 = GenerationProvider("gen2")
    record = _run_cell(JournalingClient(gen2, RequestJournal(path)))
    assert gen2.calls == 2
    assert record["exchanges"][0]["extracted_claim"] == "fact gen1-1"
    assert record["exchanges"][1]["extracted_claim"] == "fact gen1-3"
    assert "gen2" in record["raw_verdict_text"]

    # Generation 3 is never consulted at all: a full re-drive replays the journal verbatim
    # and reproduces the record byte for byte. This is the property the three terminally
    # excluded cells lacked.
    gen3 = GenerationProvider("gen3")
    replayed = _run_cell(JournalingClient(gen3, RequestJournal(path)))
    assert gen3.calls == 0
    assert _canonical(replayed) == _canonical(record)


def test_an_uninterrupted_run_and_a_crashed_run_agree_when_the_generation_agrees(tmp_path):
    # Same scripted generation, one run interrupted and one not: identical records. The
    # journal must not itself perturb composition.
    baseline = _run_cell(JournalingClient(
        GenerationProvider("gen1"), RequestJournal(tmp_path / "a.jsonl")))

    path = tmp_path / "b.jsonl"
    with pytest.raises(KeyboardInterrupt):
        _run_cell(JournalingClient(
            GenerationProvider("gen1", crash_on=4), RequestJournal(path)))
    resumed_provider = GenerationProvider("gen1")
    resumed_provider.calls = 3   # the same sampler state the baseline had at this point
    resumed = _run_cell(JournalingClient(resumed_provider, RequestJournal(path)))
    assert _canonical(resumed) == _canonical(baseline)


# -- the crash window the client cannot close ----------------------------------------------


def test_ambiguous_dispatch_detection(tmp_path):
    journal = RequestJournal(tmp_path / "journal.jsonl")
    journaled_metadata = {"cell_key": "cell-1", "call_role": "judge_query",
                          "query_index": 0}
    journal.put(journal_key(journaled_metadata), "f" * 64, "recorded")

    ledger = [
        # Settled and journaled: clean.
        {"status": "success", "sequence": 10, "metadata": dict(journaled_metadata)},
        # Settled but never journaled: the process died between settle and append. The
        # response bytes are gone; the cell must be resolved INVALID, never re-dispatched.
        {"status": "success", "sequence": 11,
         "metadata": {"cell_key": "cell-2", "call_role": "judge_verdict"}},
        # A reservation or failure is not a settled success and is not ambiguous.
        {"status": "reserved", "sequence": 12,
         "metadata": {"cell_key": "cell-3", "call_role": "judge_query",
                      "query_index": 0}},
        # Events without journalable metadata (harness probes) are ignored.
        {"status": "success", "sequence": 13, "metadata": {}},
    ]
    findings = find_ambiguous_dispatches(journal, ledger)
    assert [f["problem"] for f in findings] == ["success_without_journal_entry"]
    assert findings[0]["cell_key"] == "cell-2"
    assert findings[0]["ledger_sequence"] == 11

    # A second settled success for a journaled key cannot happen under the journal regime;
    # if the ledger shows one, that is a violation to halt on, not a retry to tolerate.
    ledger.append({"status": "success", "sequence": 14,
                   "metadata": dict(journaled_metadata)})
    findings = find_ambiguous_dispatches(journal, ledger)
    assert [f["problem"] for f in findings] == [
        "duplicate_success_for_key", "success_without_journal_entry"]

