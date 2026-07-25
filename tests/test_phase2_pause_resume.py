"""End-to-end proof that a paused canary cell resumes without re-querying the judge.

This is the integration the whole cache exists for. Judge queries run at temperature 0.3, so
a cell that pauses for reviewer labelling and is then re-run would ordinarily emit *different*
query text, hash to a different payload, find that payload unlabelled, and pause again --
re-spending the judge call every round. The cache makes the resumed cell replay its earlier
calls verbatim, so the payload the reviewer labelled is the payload the resumed cell presents.
"""
import json
from pathlib import Path

import pytest

from rejudge import config, judge_loop
from rejudge import phase2_canary_gate as gate_mod
from rejudge.phase2_call_cache import CallCache
from rejudge.phase2_caching_client import CachingClient
from rejudge.phase2_dual_gate import DualGate, DualGateDecisionStore, payload_hash


TRANSCRIPTS_PATH = Path("data/transcripts.jsonl")
MISSING_TRANSCRIPTS_REASON = (
    "requires local research corpus data/transcripts.jsonl (not included in clean clones)"
)

VERDICT = "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok"
REJECTION = "Query rejected: ask a single specific factual claim"
NO_QUERY = "No verification result is available for this query."


class NondeterministicClient:
    """Stands in for a judge at temperature 0.3.

    ``offset`` models the part that matters here: sampling does not reset just because the
    process did, so two runs of the same cell need not agree. A stand-in that restarted at a
    fixed value would be a deterministic judge and would hide the very failure under test.
    """

    def __init__(self, offset=0):
        self.query_calls = 0
        self.offset = offset
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict",
                 **kwargs):
        if kind == "query":
            self.query_calls += 1
            year = 30 + self.offset + self.query_calls
            return f"CLAIM: the council was established in Year {year}"
        if kind == "oracle":
            return "YES"
        return VERDICT


def _tr():
    if not TRANSCRIPTS_PATH.is_file():
        pytest.skip(MISSING_TRANSCRIPTS_REASON)
    return json.loads(TRANSCRIPTS_PATH.open(encoding="utf-8").readline())


def _reviewer_allow():
    return "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: single atomic claim"


def _run(transcript, client, store, *, pause):
    """One pass over a budget-1 clean cell, with the dual gate wired in."""
    position_a, position_b = transcript["correct_answer"], transcript["wrong_answer"]

    def exploding_reviewer(raw_query, candidate_a, candidate_b):
        raise AssertionError("pause mode must not invoke the reviewer")

    canary_gate = gate_mod.CanaryQueryGate(
        candidate_a=position_a, candidate_b=position_b, total_slots=1,
        checker=lambda request: "allow", dual_gate=DualGate(store, exploding_reviewer),
        rejection_payload=REJECTION, no_query_payload=NO_QUERY,
        pause_when_unlabeled=pause)
    record = judge_loop.run_judgment(
        transcript, "WORLD DOC", config.ARMS["clean"], 1, 0, client,
        config.load_protocol(), query_gate=canary_gate, position_override=True)
    return record, canary_gate


def test_a_paused_cell_resumes_on_the_very_payload_the_reviewer_labelled(tmp_path):
    transcript = _tr()
    cache = CallCache(tmp_path / "calls.jsonl")
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    inner = NondeterministicClient()
    client = CachingClient(inner, cache)

    # First pass: the judge produces a query, the payload has no decision, the cell pauses.
    with pytest.raises(gate_mod.PendingReviewerDecision) as excinfo:
        _run(transcript, client, store, pause=True)
    paused_sha = excinfo.value.payload_sha256
    assert inner.query_calls == 1

    # The reviewer labels that exact payload out of band.
    store.commit(paused_sha, "ALLOW", "Allowed", "fine", _reviewer_allow(), "parsed")

    # Resume in a fresh process: new cache and store objects over the same files.
    resumed_client = CachingClient(
        NondeterministicClient(offset=1), CallCache(tmp_path / "calls.jsonl"))
    record, canary_gate = _run(
        transcript, resumed_client, DualGateDecisionStore(tmp_path / "decisions.jsonl"),
        pause=True)

    # The resumed cell presented the labelled payload, not a fresh one, and never asked the
    # judge again -- the nondeterministic stand-in would have answered "Year 31".
    assert resumed_client.inner.query_calls == 0
    assert record["exchanges"][0]["extracted_claim"] == (
        "the council was established in Year 31")
    assert payload_hash(record["exchanges"][0]["raw_query_response"],
                        transcript["correct_answer"],
                        transcript["wrong_answer"]) == paused_sha
    assert canary_gate.pending == []


def test_without_the_cache_the_resumed_cell_pauses_again(tmp_path):
    # The livelock this cache exists to prevent: a fresh client re-queries the judge, gets
    # different text, and the payload the reviewer just labelled never recurs.
    transcript = _tr()
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")

    with pytest.raises(gate_mod.PendingReviewerDecision) as first:
        _run(transcript, NondeterministicClient(offset=0), store, pause=True)
    store.commit(first.value.payload_sha256, "ALLOW", "Allowed", "fine",
                 _reviewer_allow(), "parsed")

    with pytest.raises(gate_mod.PendingReviewerDecision) as second:
        _run(transcript, NondeterministicClient(offset=1), store, pause=True)
    assert second.value.payload_sha256 != first.value.payload_sha256


def test_the_resumed_cell_completes_through_the_oracle_and_verdict(tmp_path):
    transcript = _tr()
    cache = CallCache(tmp_path / "calls.jsonl")
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    client = CachingClient(NondeterministicClient(), cache)

    with pytest.raises(gate_mod.PendingReviewerDecision) as excinfo:
        _run(transcript, client, store, pause=True)
    store.commit(excinfo.value.payload_sha256, "ALLOW", "Allowed", "fine",
                 _reviewer_allow(), "parsed")

    record, _gate = _run(
        transcript,
        CachingClient(NondeterministicClient(offset=1), CallCache(tmp_path / "calls.jsonl")),
        DualGateDecisionStore(tmp_path / "decisions.jsonl"), pause=True)
    exchange = record["exchanges"][0]
    assert exchange.get("blocked") is not True
    assert exchange["normalized"] == "YES"
    assert record["queries_used"] == 1
    assert record["verdict_strict"]["verdict"] == "A"


def test_a_reviewer_reject_blocks_the_resumed_cell_without_dispatch(tmp_path):
    transcript = _tr()
    cache = CallCache(tmp_path / "calls.jsonl")
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    client = CachingClient(NondeterministicClient(), cache)

    with pytest.raises(gate_mod.PendingReviewerDecision) as excinfo:
        _run(transcript, client, store, pause=True)
    store.commit(excinfo.value.payload_sha256, "REJECT", "P3", "two facts",
                 "LABEL: REJECT\nCLAUSE: P3\nRATIONALE: two facts", "parsed")

    record, canary_gate = _run(
        transcript,
        CachingClient(NondeterministicClient(offset=1), CallCache(tmp_path / "calls.jsonl")),
        DualGateDecisionStore(tmp_path / "decisions.jsonl"), pause=True)
    exchange = record["exchanges"][0]
    assert exchange["blocked"] is True
    assert exchange["oracle_prompt"] is None
    assert canary_gate.intercepts == [excinfo.value.payload_sha256]
