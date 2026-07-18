from dataclasses import FrozenInstanceError, fields

import pytest

from rejudge.phase2_query_gate import (
    CheckerDecision,
    CheckerRequest,
    CheckerResult,
    Phase2QueryGate,
    QueryGateClosed,
)
from rejudge.query_screen import ANSWER_OR_DEBATE_REFERENCE


CANDIDATE_A = "The Compact was established in Year 31 after the coastal accord."
CANDIDATE_B = "The Compact was established in Year 44 after the inland revolt."
ATOMIC_QUERY = "The removal threshold is 24 votes."


class ScriptedChecker:
    def __init__(self, *results):
        self.results = list(results)
        self.requests: list[CheckerRequest] = []

    def __call__(self, request: CheckerRequest):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_gate(checker, *, total_slots=1):
    return Phase2QueryGate(
        candidate_a=CANDIDATE_A,
        candidate_b=CANDIDATE_B,
        total_slots=total_slots,
        checker=checker,
    )


def test_accept_consumes_slot_and_is_oracle_eligible_with_neutral_audit_inputs():
    checker = ScriptedChecker(
        CheckerResult(
            decision=CheckerDecision.ALLOW,
            raw_output='{"decision":"allow"}',
        )
    )
    gate = make_gate(checker)

    event = gate.submit(ATOMIC_QUERY)

    assert event.slot == 1
    assert event.attempt == 1
    assert event.mechanical_reasons == ()
    assert event.checker_raw_output == '{"decision":"allow"}'
    assert event.checker_decision is CheckerDecision.ALLOW
    assert event.final_decision is CheckerDecision.ALLOW
    assert event.slot_consumed is True
    assert event.oracle_eligible is True
    assert event.halted is False
    assert event.exhausted is True
    assert gate.exhausted is True

    request = checker.requests[0]
    assert request.candidate_a == CANDIDATE_A
    assert request.candidate_b == CANDIDATE_B
    assert {field.name for field in fields(request)} == {
        "raw_query", "candidate_a", "candidate_b", "slot", "attempt"
    }
    record = event.as_record()
    assert record["candidate_a"] == CANDIDATE_A
    assert record["candidate_b"] == CANDIDATE_B
    assert "correct_answer" not in record
    assert "wrong_answer" not in record
    assert "world_document" not in record
    with pytest.raises(FrozenInstanceError):
        event.slot = 2


def test_first_rejection_is_free_and_retry_accepts_in_the_same_slot():
    checker = ScriptedChecker("reject", "allow")
    gate = make_gate(checker)

    rejected = gate.submit(ATOMIC_QUERY)
    accepted = gate.submit("The Compact began in Year 31.")

    assert (rejected.slot, rejected.attempt) == (1, 1)
    assert rejected.final_decision is CheckerDecision.REJECT
    assert rejected.slot_consumed is False
    assert rejected.oracle_eligible is False
    assert rejected.exhausted is False
    assert (accepted.slot, accepted.attempt) == (1, 2)
    assert accepted.slot_consumed is True
    assert accepted.oracle_eligible is True
    assert accepted.exhausted is True
    assert [(r.slot, r.attempt) for r in checker.requests] == [(1, 1), (1, 2)]


def test_second_rejection_consumes_the_slot_without_oracle_eligibility():
    checker = ScriptedChecker("reject", "reject")
    gate = make_gate(checker)

    first = gate.submit(ATOMIC_QUERY)
    second = gate.submit("The Compact began in Year 31.")

    assert first.slot_consumed is False
    assert second.final_decision is CheckerDecision.REJECT
    assert second.slot == 1
    assert second.attempt == 2
    assert second.slot_consumed is True
    assert second.oracle_eligible is False
    assert second.exhausted is True
    assert gate.exhausted is True


def test_mechanical_rejection_short_circuits_checker_and_gets_one_free_retry():
    checker = ScriptedChecker("allow")
    gate = make_gate(checker)

    rejected = gate.submit("Position A is correct.")

    assert rejected.decision_source == "mechanical"
    assert rejected.mechanical_reasons == (ANSWER_OR_DEBATE_REFERENCE,)
    assert rejected.checker_raw_output is None
    assert rejected.checker_decision is None
    assert rejected.final_decision is CheckerDecision.REJECT
    assert rejected.slot_consumed is False
    assert checker.requests == []

    accepted = gate.submit(ATOMIC_QUERY)
    assert accepted.attempt == 2
    assert accepted.oracle_eligible is True
    assert len(checker.requests) == 1


@pytest.mark.parametrize("malformed", ["ALLOW", " allow", "allow\n", "explanation: allow", object()])
def test_malformed_checker_output_halts_fail_closed(malformed):
    checker = ScriptedChecker(malformed)
    gate = make_gate(checker, total_slots=2)

    event = gate.submit(ATOMIC_QUERY)

    assert event.halted is True
    assert event.halt_reason == "checker_malformed"
    assert event.final_decision is None
    assert event.slot_consumed is False
    assert event.oracle_eligible is False
    assert gate.halted is True


def test_checker_outage_halts_fail_closed():
    checker = ScriptedChecker(RuntimeError("offline checker unavailable"))
    gate = make_gate(checker, total_slots=2)

    event = gate.submit(ATOMIC_QUERY)

    assert event.halted is True
    assert event.halt_reason == "checker_outage"
    assert event.checker_error == "RuntimeError: offline checker unavailable"
    assert event.checker_decision is None
    assert event.slot_consumed is False
    assert event.oracle_eligible is False


def test_unresolved_checker_decision_halts_fail_closed():
    checker = ScriptedChecker("unresolved")
    gate = make_gate(checker, total_slots=2)

    event = gate.submit(ATOMIC_QUERY)

    assert event.halted is True
    assert event.halt_reason == "checker_unresolved"
    assert event.checker_raw_output == "unresolved"
    assert event.checker_decision is CheckerDecision.UNRESOLVED
    assert event.final_decision is CheckerDecision.UNRESOLVED
    assert event.slot_consumed is False
    assert event.oracle_eligible is False


def test_no_checker_calls_or_events_after_halt():
    checker = ScriptedChecker("unresolved", "allow")
    gate = make_gate(checker, total_slots=2)
    gate.submit(ATOMIC_QUERY)

    with pytest.raises(QueryGateClosed, match="checker_unresolved"):
        gate.submit("The Compact began in Year 31.")

    assert len(checker.requests) == 1
    assert len(gate.events) == 1


def test_no_checker_calls_or_events_after_exhaustion():
    checker = ScriptedChecker("allow", "allow")
    gate = make_gate(checker)
    gate.submit(ATOMIC_QUERY)

    with pytest.raises(QueryGateClosed, match="budget exhausted"):
        gate.submit("The Compact began in Year 31.")

    assert len(checker.requests) == 1
    assert len(gate.events) == 1

