"""Offline fixtures for the dual gate (amendment canary_claude_gate_2026-07-23).

Deterministic, zero provider calls. Covers the protocol's required paths
(accept, reject, retry-inheritance, malformed, outage) composed with the
Claude-gate rules: intercept tagging, ambiguous fail-closed, identical-payload
sharing, chain integrity, and crash-resume.
"""
import pytest

from rejudge.phase2_dual_gate import (
    DualGate, DualGateDecisionStore, parse_reviewer_output, payload_hash)

GOOD = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: One atomic checkable fact."
REJ = "LABEL: REJECT\nCLAUSE: P3\nRATIONALE: Two independently checkable facts."
AMB = "LABEL: CONTRACT_AMBIGUOUS\nCLAUSE: P4\nRATIONALE: The contract does not decide."


def make_gate(tmp_path, outputs):
    calls = []

    def reviewer(q, a, b):
        calls.append(q)
        result = outputs[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    return DualGate(store, reviewer), calls


def test_parse_good_and_malformed():
    assert parse_reviewer_output(GOOD) == ("ALLOW", "Allowed", "One atomic checkable fact.")
    for bad in ("", "allow", "LABEL: ALLOW", "LABEL: MAYBE\nCLAUSE: P1\nRATIONALE: x",
                "LABEL: ALLOW\nCLAUSE: P9\nRATIONALE: x", None):
        assert parse_reviewer_output(bad) == (None, None, None)


def test_double_allow_dispatches(tmp_path):
    gate, _ = make_gate(tmp_path, [GOOD])
    outcome = gate.decide(checker_decision="allow", raw_query="q1",
                          candidate_a="a", candidate_b="b")
    assert outcome.dispatch_allowed
    assert not outcome.checker_false_allow_intercept


def test_reviewer_intercepts_checker_allow(tmp_path):
    gate, _ = make_gate(tmp_path, [REJ])
    outcome = gate.decide(checker_decision="allow", raw_query="q1",
                          candidate_a="a", candidate_b="b")
    assert not outcome.dispatch_allowed
    assert outcome.checker_false_allow_intercept


def test_checker_reject_blocks_even_with_reviewer_allow(tmp_path):
    gate, _ = make_gate(tmp_path, [GOOD])
    outcome = gate.decide(checker_decision="reject", raw_query="q1",
                          candidate_a="a", candidate_b="b")
    assert not outcome.dispatch_allowed
    assert not outcome.checker_false_allow_intercept


def test_ambiguous_fails_closed(tmp_path):
    gate, _ = make_gate(tmp_path, [AMB])
    outcome = gate.decide(checker_decision="allow", raw_query="q1",
                          candidate_a="a", candidate_b="b")
    assert not outcome.dispatch_allowed
    assert outcome.checker_false_allow_intercept
    assert outcome.reviewer.label == "CONTRACT_AMBIGUOUS"


def test_malformed_and_outage_fail_closed(tmp_path):
    gate, _ = make_gate(tmp_path, ["gibberish", RuntimeError("outage")])
    bad = gate.decide(checker_decision="allow", raw_query="q1",
                      candidate_a="a", candidate_b="b")
    assert not bad.dispatch_allowed and bad.reviewer.status == "malformed"
    down = gate.decide(checker_decision="allow", raw_query="q2",
                       candidate_a="a", candidate_b="b")
    assert not down.dispatch_allowed and down.reviewer.status == "reviewer_error"
    assert "outage" in down.reviewer.raw_output


def test_retry_inherits_no_second_chance(tmp_path):
    gate, calls = make_gate(tmp_path, [REJ, GOOD])
    first = gate.decide(checker_decision="allow", raw_query="q1",
                        candidate_a="a", candidate_b="b")
    again = gate.decide(checker_decision="allow", raw_query="q1",
                        candidate_a="a", candidate_b="b")
    assert len(calls) == 1, "identical payload must never re-ask the reviewer"
    assert not first.dispatch_allowed and not again.dispatch_allowed
    assert again.reviewer.event_hash == first.reviewer.event_hash


def test_changed_payload_gets_fresh_decision(tmp_path):
    gate, calls = make_gate(tmp_path, [REJ, GOOD])
    gate.decide(checker_decision="allow", raw_query="q1", candidate_a="a", candidate_b="b")
    second = gate.decide(checker_decision="allow", raw_query="q1 reworded",
                         candidate_a="a", candidate_b="b")
    assert len(calls) == 2
    assert second.dispatch_allowed


def test_store_resume_and_chain_integrity(tmp_path):
    path = tmp_path / "decisions.jsonl"
    store = DualGateDecisionStore(path)
    sha = payload_hash("q1", "a", "b")
    store.commit(sha, "ALLOW", "Allowed", "One atomic checkable fact.", GOOD,
                 "parsed")
    resumed = DualGateDecisionStore(path)
    decision = resumed.get(sha)
    assert decision is not None and decision.effective_allow
    with pytest.raises(ValueError):
        resumed.commit(sha, "REJECT", "P3", "Two independently checkable facts.", REJ,
                       "parsed")
    tampered = path.read_text(encoding="utf-8").replace('"ALLOW"', '"REJECT"', 1)
    path.write_text(tampered, encoding="utf-8")
    with pytest.raises(ValueError, match="chain corrupt"):
        DualGateDecisionStore(path)


def test_store_is_metadata_free(tmp_path):
    gate, _ = make_gate(tmp_path, [GOOD])
    gate.decide(checker_decision="allow", raw_query="q1", candidate_a="a", candidate_b="b")
    text = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
    import json
    row = json.loads(text.splitlines()[0])
    assert set(row) == {"payload_sha256", "label", "clause", "rationale", "raw_output",
                        "status", "sequence", "prev_event_hash", "event_hash"}


# --- concurrency ------------------------------------------------------------------------
#
# The canary ran strictly serially, so these paths were unreachable. The main run runs
# concurrently, and a payload hash is shared across cells by design: inheritance is the
# whole point of keying decisions by payload. So two workers proposing identical query text
# is the common case, not the corner case.

def test_two_workers_on_one_payload_ask_the_reviewer_exactly_once(tmp_path):
    """Without singleflight both workers miss the store, both call the reviewer, and the
    second commit raises because the store admits one ruling per payload."""
    import threading

    calls = []
    started = threading.Barrier(8)
    gate_lock = threading.Lock()

    def reviewer(q, a, b):
        with gate_lock:
            calls.append(q)
        return GOOD

    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    gate = DualGate(store, reviewer)
    results, errors = [], []

    def worker():
        started.wait(timeout=10)
        try:
            results.append(gate.review("The threshold is 24 votes.", "A text", "B text"))
        except Exception as exc:  # noqa: BLE001 - the test is what exceptions escape
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"no worker should fail: {errors}"
    assert len(calls) == 1, "the reviewer must be asked exactly once per payload"
    assert len({d.event_hash for d in results}) == 1, "all workers inherit one ruling"
    assert len(results) == 8


def test_concurrent_commits_leave_a_contiguous_verifiable_chain(tmp_path):
    """Each commit reads the tail hash and appends; interleaving two of those corrupts the
    chain, and the store refuses to reload."""
    import threading

    path = tmp_path / "decisions.jsonl"
    store = DualGateDecisionStore(path)
    gate = DualGate(store, lambda q, a, b: GOOD)
    started = threading.Barrier(12)
    errors = []

    def worker(index):
        started.wait(timeout=10)
        try:
            gate.review(f"Fact number {index} is stated.", "A text", "B text")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"no worker should fail: {errors}"
    reloaded = DualGateDecisionStore(path)  # re-verifies sequence, chain and row hashes
    assert len(reloaded._by_payload) == 12


def test_an_abandoned_reservation_does_not_strand_the_payload(tmp_path):
    """If the worker holding a reservation dies without committing, the payload must not be
    permanently unreviewable: fail closed on the run, never silently skip the gate."""
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    sha = payload_hash("A claim.", "A text", "B text")

    decision, owned = store.get_or_reserve(sha)
    assert decision is None and owned is True
    store.release(sha)

    decision, owned = store.get_or_reserve(sha)
    assert decision is None and owned is True, "the released payload is claimable again"
