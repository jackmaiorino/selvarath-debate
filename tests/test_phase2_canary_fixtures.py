"""The offline client used to rehearse the canary without touching a provider.

A fixture client is only useful if the real parsing code accepts what it emits, so these
tests run its output through the genuine parsers rather than asserting on strings: a fixture
that drifts out of the frozen formats would otherwise pass its own tests and fail the
rehearsal it exists to support.

It is a separate class rather than RejudgeClient(dry_run=True) because that client's dry-run
whitelist covers only query/oracle/verdict and rejects the query_checker role outright, so the
canary's gate path cannot be rehearsed through it at all.
"""
import pytest

from rejudge import oracle_channel
from rejudge.composer import clean_extract_claim
from rejudge.parsers import parse_both
from rejudge.phase2_canary_fixtures import (
    DeterministicCanaryClient, StubReviewer, UnknownCallRole, scripted)
from rejudge.phase2_dual_gate import parse_reviewer_output
from rejudge.phase2_query_gate import parse_checker_output


def _call(client, call_role, **metadata):
    return client.complete(
        [{"role": "user", "content": "prompt"}], "model", 0.3, 1, 256, kind="query",
        request_metadata={"cell_key": "cell", "call_role": call_role, **metadata})


# --- every role emits something the real parsers accept ---------------------------------

def test_a_judge_query_parses_as_a_clean_claim():
    raw = _call(DeterministicCanaryClient(), "judge_query", query_index=0)
    claim, well_formed = clean_extract_claim(raw)
    assert well_formed is True
    assert claim


def test_a_verdict_parses_strictly():
    parsed = parse_both(_call(DeterministicCanaryClient(), "judge_verdict"))
    assert parsed["strict"]["parse_ok"] is True
    assert parsed["strict"]["verdict"] == "A"
    assert parsed["strict"]["confidence"] == 4


def test_a_batch_verdict_parses_strictly():
    parsed = parse_both(_call(DeterministicCanaryClient(), "batch_verdict"))
    assert parsed["strict"]["parse_ok"] is True


def test_the_verdict_side_is_selectable():
    parsed = parse_both(_call(DeterministicCanaryClient(verdict_side="B"), "judge_verdict"))
    assert parsed["strict"]["verdict"] == "B"


def test_an_oracle_reply_normalises_strictly():
    raw = _call(DeterministicCanaryClient(), "oracle_verification", query_index=0)
    assert oracle_channel.normalize_strict(raw) == "YES"


def test_a_checker_reply_is_an_exact_frozen_token():
    raw = _call(DeterministicCanaryClient(), "query_checker", slot=1, attempt=1)
    assert parse_checker_output(raw).decision.value == "allow"


def test_a_debater_turn_is_non_empty_and_within_the_word_cap():
    raw = _call(DeterministicCanaryClient(), "debater_turn", round_index=0, slot_index=0)
    assert raw.strip()
    assert len(raw.split()) <= 150


# --- determinism ---------------------------------------------------------------------------

def test_the_same_call_answers_identically():
    a = DeterministicCanaryClient()
    b = DeterministicCanaryClient()
    meta = {"round_index": 1, "slot_index": 0}
    assert _call(a, "debater_turn", **meta) == _call(b, "debater_turn", **meta)


def test_distinct_calls_answer_distinctly():
    client = DeterministicCanaryClient()
    first = _call(client, "judge_query", query_index=0)
    second = _call(client, "judge_query", query_index=1)
    assert first != second


# --- scripting the frozen checker outcomes --------------------------------------------------

def test_the_five_required_checker_fixture_outcomes_are_expressible():
    # build_canary_plan names these as the offline fixture outcomes the canary must cover.
    outage = TimeoutError("provider timeout")
    client = DeterministicCanaryClient(
        query_checker=scripted(["allow", "reject", "allow", "reject", "reject",
                                "Allow", outage]))
    assert parse_checker_output(_call(client, "query_checker")).decision.value == "allow"
    assert parse_checker_output(_call(client, "query_checker")).decision.value == "reject"
    assert parse_checker_output(_call(client, "query_checker")).decision.value == "allow"
    assert _call(client, "query_checker") == "reject"
    assert _call(client, "query_checker") == "reject"
    assert _call(client, "query_checker") == "Allow"      # malformed: casing is not tolerated
    with pytest.raises(TimeoutError):                      # outage
        _call(client, "query_checker")


def test_a_scripted_exception_is_raised_rather_than_returned():
    client = DeterministicCanaryClient(query_checker=scripted([RuntimeError("boom")]))
    with pytest.raises(RuntimeError):
        _call(client, "query_checker")


def test_an_unresolved_checker_verdict_is_expressible():
    client = DeterministicCanaryClient(query_checker=scripted(["unresolved"]))
    assert parse_checker_output(_call(client, "query_checker")).decision.value == "unresolved"


def test_a_judge_can_be_scripted_to_signal_done():
    client = DeterministicCanaryClient(judge_query=scripted(["DONE"]))
    assert oracle_channel.is_done_robust(_call(client, "judge_query", query_index=0))


# --- fail closed -----------------------------------------------------------------------------

def test_an_unknown_call_role_is_refused():
    # Silently answering an unrecognised role would let a rehearsal pass over a code path the
    # fixture never actually modelled.
    with pytest.raises(UnknownCallRole):
        _call(DeterministicCanaryClient(), "invented_role")


def test_a_call_without_a_call_role_is_refused():
    with pytest.raises(UnknownCallRole):
        DeterministicCanaryClient().complete(
            [{"role": "user", "content": "x"}], "m", 0.3, 1, 256, kind="query",
            request_metadata={"cell_key": "c"})


# --- observability ----------------------------------------------------------------------------

def test_calls_are_recorded_for_assertions():
    client = DeterministicCanaryClient()
    _call(client, "judge_query", query_index=0)
    _call(client, "judge_verdict")
    assert [c["call_role"] for c in client.calls] == ["judge_query", "judge_verdict"]


def test_the_client_declares_itself_offline():
    assert DeterministicCanaryClient().dry_run is True


# --- the stub reviewer -------------------------------------------------------------------------

def test_the_stub_reviewer_emits_the_frozen_three_line_protocol():
    label, clause, rationale = parse_reviewer_output(StubReviewer()("q", "a", "b"))
    assert (label, clause) == ("ALLOW", "Allowed")
    assert rationale


def test_the_stub_reviewer_can_be_scripted_per_verdict():
    reviewer = StubReviewer(scripted([("REJECT", "P3"), ("CONTRACT_AMBIGUOUS", "P4")]))
    assert parse_reviewer_output(reviewer("q", "a", "b"))[:2] == ("REJECT", "P3")
    assert parse_reviewer_output(reviewer("q", "a", "b"))[:2] == ("CONTRACT_AMBIGUOUS", "P4")


def test_the_stub_reviewer_can_emit_output_the_frozen_parser_rejects():
    # Parse failure is a designed fail-closed path, so the rehearsal must be able to reach it.
    reviewer = StubReviewer(scripted(["Sure, here is my review:\nLABEL: ALLOW"]))
    assert parse_reviewer_output(reviewer("q", "a", "b")) == (None, None, None)
