"""Offline Phase-2 oracle-query gate and retry state machine.

The gate is intentionally provider-agnostic.  Its only model-facing dependency is an
injected checker callable, which receives an immutable request containing the raw
query and neutrally named Candidate A/B text.  It never receives a world document or
truth-labelled ``correct``/``wrong`` inputs.

Mechanical violations short-circuit the injected checker.  Otherwise the checker
must return either an exact lower-case decision token (``allow``, ``reject``, or
``unresolved``) or a validated :class:`CheckerResult`.  Malformed output, checker
outage, and unresolved ambiguity halt the cell rather than silently choosing a side.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol, TypeAlias

from rejudge.api_client import UnknownChargeHalt
from rejudge.query_screen import screen_query


# Amendment 4 (2026-08-19), package item 2, blocker fix (Codex re-review, 2026-08-19): a
# reason code for the gate's OWN mechanical byte-cap check, disjoint from query_screen.py's
# frozen content-screening reason codes (EMPTY_QUERY etc.) -- this is a transport-safety
# concern, not a content-quality one, and query_screen.py's contract must not be touched to
# express it.
VISIBLE_HISTORY_CAP_EXCEEDED = "visible_history_cap_exceeded"


class CheckerDecision(str, Enum):
    """The only decisions a Phase-2 query checker may return."""

    ALLOW = "allow"
    REJECT = "reject"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class CheckerRequest:
    """Truth-neutral checker input; deliberately contains no world document."""

    raw_query: str
    candidate_a: str
    candidate_b: str
    slot: int
    attempt: int


@dataclass(frozen=True, slots=True)
class CheckerResult:
    """A checker result already parsed by an injected checker adapter."""

    decision: CheckerDecision
    reasons: tuple[str, ...] = ()
    raw_output: str | None = None


class QueryChecker(Protocol):
    """Injected checker interface; implementations may be local deterministic fakes."""

    def __call__(self, request: CheckerRequest) -> CheckerResult | str: ...


CheckerCallable: TypeAlias = Callable[[CheckerRequest], CheckerResult | str]


class MalformedCheckerOutput(ValueError):
    """Raised internally when a checker result does not satisfy the strict schema."""


class QueryGateClosed(RuntimeError):
    """Raised when a caller submits after the cell halted or exhausted its budget."""


def _validate_structured_result(result: CheckerResult) -> CheckerResult:
    if not isinstance(result.decision, CheckerDecision):
        raise MalformedCheckerOutput("structured checker decision is not a CheckerDecision")
    if not isinstance(result.reasons, tuple):
        raise MalformedCheckerOutput("structured checker reasons must be a tuple")
    if any(not isinstance(reason, str) or not reason for reason in result.reasons):
        raise MalformedCheckerOutput("structured checker reasons must be non-empty strings")
    if result.raw_output is not None and not isinstance(result.raw_output, str):
        raise MalformedCheckerOutput("structured checker raw_output must be text or None")
    return result


def parse_checker_output(output: CheckerResult | str) -> CheckerResult:
    """Strictly parse an injected checker result.

    Raw output is accepted only when it is exactly one lower-case decision token;
    whitespace, casing variants, explanations, and unknown values are malformed.
    A richer adapter can parse a provider-specific response itself and return a
    structured :class:`CheckerResult`, while preserving its raw text for the audit
    event.
    """

    if isinstance(output, CheckerResult):
        return _validate_structured_result(output)
    if type(output) is not str:
        raise MalformedCheckerOutput("checker output must be text or CheckerResult")
    try:
        decision = CheckerDecision(output)
    except ValueError as exc:
        raise MalformedCheckerOutput(
            "raw checker output must be exactly allow, reject, or unresolved"
        ) from exc
    return CheckerResult(decision=decision, raw_output=output)


@dataclass(frozen=True, slots=True)
class QueryGateEvent:
    """Immutable, JSON-ready evidence for one submitted query attempt."""

    sequence: int
    raw_query: str
    candidate_a: str
    candidate_b: str
    slot: int
    attempt: int
    mechanical_reasons: tuple[str, ...]
    checker_raw_output: str | None
    checker_decision: CheckerDecision | None
    checker_reasons: tuple[str, ...]
    final_decision: CheckerDecision | None
    decision_source: str
    slot_consumed: bool
    oracle_eligible: bool
    halted: bool
    halt_reason: str | None
    checker_error: str | None
    exhausted: bool

    def as_record(self) -> dict[str, object]:
        """Return a fresh JSON-serializable record without weakening event immutability."""

        return {
            "sequence": self.sequence,
            "raw_query": self.raw_query,
            "candidate_a": self.candidate_a,
            "candidate_b": self.candidate_b,
            "slot": self.slot,
            "attempt": self.attempt,
            "mechanical_reasons": list(self.mechanical_reasons),
            "checker_raw_output": self.checker_raw_output,
            "checker_decision": (
                self.checker_decision.value if self.checker_decision is not None else None
            ),
            "checker_reasons": list(self.checker_reasons),
            "final_decision": (
                self.final_decision.value if self.final_decision is not None else None
            ),
            "decision_source": self.decision_source,
            "slot_consumed": self.slot_consumed,
            "oracle_eligible": self.oracle_eligible,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "checker_error": self.checker_error,
            "exhausted": self.exhausted,
        }


class Phase2QueryGate:
    """Stateful implementation of the frozen one-free-retry query policy."""

    def __init__(
        self,
        *,
        candidate_a: str,
        candidate_b: str,
        total_slots: int,
        checker: QueryChecker | CheckerCallable,
        max_response_bytes: int | None = None,
    ) -> None:
        if not isinstance(candidate_a, str) or not isinstance(candidate_b, str):
            raise TypeError("candidate_a and candidate_b must be strings")
        if type(total_slots) is not int or total_slots < 0:
            raise ValueError("total_slots must be a non-negative integer")
        if not callable(checker):
            raise TypeError("checker must be callable")
        if max_response_bytes is not None:
            if (isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int)
                    or max_response_bytes <= 0):
                raise ValueError(
                    "max_response_bytes must be a positive integer or None, got "
                    f"{max_response_bytes!r}")

        self._candidate_a = candidate_a
        self._candidate_b = candidate_b
        self._total_slots = total_slots
        self._checker = checker
        # Amendment 4 (2026-08-19), package item 2: additive, default-off (every pre-amendment
        # caller omits it, so their behavior is byte-for-byte unchanged). When set, a raw query
        # whose UTF-8 byte length exceeds this cap is treated as a MECHANICAL rejection --
        # checked in submit() BEFORE query_screen.screen_query and BEFORE the (possibly billed)
        # checker is ever consulted -- so it flows through the frozen retry-then-block state
        # machine exactly like any other mechanical violation. This is the ONLY place that
        # decides "is this raw query over the visible-history cap": the gate owns its own
        # attempt/slot state, so no caller may synthesize that transition externally.
        self._max_response_bytes = max_response_bytes
        self._slot = 1
        self._attempt = 1
        self._halted = False
        self._halt_reason: str | None = None
        self._events: list[QueryGateEvent] = []

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    @property
    def exhausted(self) -> bool:
        return self._slot > self._total_slots

    @property
    def events(self) -> tuple[QueryGateEvent, ...]:
        return tuple(self._events)

    def _closed_message(self) -> str:
        if self._halted:
            return f"query gate halted: {self._halt_reason}"
        return "query budget exhausted"

    def _append_event(
        self,
        *,
        raw_query: str,
        mechanical_reasons: tuple[str, ...],
        checker_raw_output: str | None = None,
        checker_decision: CheckerDecision | None = None,
        checker_reasons: tuple[str, ...] = (),
        final_decision: CheckerDecision | None,
        decision_source: str,
        slot_consumed: bool,
        oracle_eligible: bool,
        halted: bool,
        halt_reason: str | None = None,
        checker_error: str | None = None,
    ) -> QueryGateEvent:
        event = QueryGateEvent(
            sequence=len(self._events) + 1,
            raw_query=raw_query,
            candidate_a=self._candidate_a,
            candidate_b=self._candidate_b,
            slot=self._slot,
            attempt=self._attempt,
            mechanical_reasons=mechanical_reasons,
            checker_raw_output=checker_raw_output,
            checker_decision=checker_decision,
            checker_reasons=checker_reasons,
            final_decision=final_decision,
            decision_source=decision_source,
            slot_consumed=slot_consumed,
            oracle_eligible=oracle_eligible,
            halted=halted,
            halt_reason=halt_reason,
            checker_error=checker_error,
            exhausted=(self._slot >= self._total_slots if slot_consumed else False),
        )
        self._events.append(event)
        return event

    def _apply_rejection(
        self,
        *,
        raw_query: str,
        mechanical_reasons: tuple[str, ...],
        checker_raw_output: str | None,
        checker_decision: CheckerDecision | None,
        checker_reasons: tuple[str, ...],
        decision_source: str,
    ) -> QueryGateEvent:
        second_rejection = self._attempt == 2
        event = self._append_event(
            raw_query=raw_query,
            mechanical_reasons=mechanical_reasons,
            checker_raw_output=checker_raw_output,
            checker_decision=checker_decision,
            checker_reasons=checker_reasons,
            final_decision=CheckerDecision.REJECT,
            decision_source=decision_source,
            slot_consumed=second_rejection,
            oracle_eligible=False,
            halted=False,
        )
        if second_rejection:
            self._slot += 1
            self._attempt = 1
        else:
            self._attempt = 2
        return event

    def _halt(
        self,
        *,
        raw_query: str,
        mechanical_reasons: tuple[str, ...],
        checker_raw_output: str | None,
        checker_decision: CheckerDecision | None,
        checker_reasons: tuple[str, ...],
        final_decision: CheckerDecision | None,
        halt_reason: str,
        checker_error: str | None = None,
    ) -> QueryGateEvent:
        self._halted = True
        self._halt_reason = halt_reason
        return self._append_event(
            raw_query=raw_query,
            mechanical_reasons=mechanical_reasons,
            checker_raw_output=checker_raw_output,
            checker_decision=checker_decision,
            checker_reasons=checker_reasons,
            final_decision=final_decision,
            decision_source="checker",
            slot_consumed=False,
            oracle_eligible=False,
            halted=True,
            halt_reason=halt_reason,
            checker_error=checker_error,
        )

    def submit(self, raw_query: str) -> QueryGateEvent:
        """Screen one query and advance the retry/budget state exactly once."""

        if self._halted or self.exhausted:
            raise QueryGateClosed(self._closed_message())
        if not isinstance(raw_query, str):
            raise TypeError("raw_query must be a string")

        # Amendment 4 (2026-08-19), package item 2: checked FIRST, before query_screen and
        # before the (possibly billed) checker is ever consulted -- a transport-safety rejection,
        # never a content judgement, and never worth spending a checker call to discover. Uses
        # this gate's OWN attempt/slot state via _apply_rejection, exactly like any other
        # mechanical violation, so no caller-side state can ever desync from it.
        if (self._max_response_bytes is not None
                and len(raw_query.encode("utf-8")) > self._max_response_bytes):
            return self._apply_rejection(
                raw_query=raw_query, mechanical_reasons=(VISIBLE_HISTORY_CAP_EXCEEDED,),
                checker_raw_output=None, checker_decision=None, checker_reasons=(),
                decision_source="mechanical")

        # The overlap rule is symmetric.  Positional arguments prevent the legacy
        # implementation's truth-labelled parameter names from entering this API.
        mechanical = screen_query(raw_query, self._candidate_a, self._candidate_b)
        mechanical_reasons = mechanical.reasons
        if mechanical_reasons:
            return self._apply_rejection(
                raw_query=raw_query,
                mechanical_reasons=mechanical_reasons,
                checker_raw_output=None,
                checker_decision=None,
                checker_reasons=(),
                decision_source="mechanical",
            )

        request = CheckerRequest(
            raw_query=raw_query,
            candidate_a=self._candidate_a,
            candidate_b=self._candidate_b,
            slot=self._slot,
            attempt=self._attempt,
        )
        try:
            raw_result = self._checker(request)
        except UnknownChargeHalt:
            # Deliberately NOT wrapped. This is the client's accounting guard firing because a
            # call's billing outcome is ambiguous; it says nothing about the checker's
            # behaviour. Reporting it as checker_outage conflated an accounting event with
            # evidence that the frozen gate is misbehaving, which cost twice over: the
            # runner's per-cell handling for ambiguous charges never fired on the dominant
            # path (gemma serves the checker and abandons ~16.8% of calls), and the supervisor
            # had to treat checker_outage as broadly resumable to compensate, blunting a
            # signal that should be rare and serious. The caller decides what an ambiguous
            # charge means for its cell.
            raise
        except Exception as exc:
            return self._halt(
                raw_query=raw_query,
                mechanical_reasons=(),
                checker_raw_output=None,
                checker_decision=None,
                checker_reasons=(),
                final_decision=None,
                halt_reason="checker_outage",
                checker_error=f"{type(exc).__name__}: {exc}",
            )

        raw_output = (
            raw_result.raw_output
            if isinstance(raw_result, CheckerResult)
            else raw_result if isinstance(raw_result, str) else repr(raw_result)
        )
        try:
            result = parse_checker_output(raw_result)
        except MalformedCheckerOutput as exc:
            return self._halt(
                raw_query=raw_query,
                mechanical_reasons=(),
                checker_raw_output=raw_output,
                checker_decision=None,
                checker_reasons=(),
                final_decision=None,
                halt_reason="checker_malformed",
                checker_error=str(exc),
            )

        if result.decision is CheckerDecision.UNRESOLVED:
            return self._halt(
                raw_query=raw_query,
                mechanical_reasons=(),
                checker_raw_output=result.raw_output,
                checker_decision=result.decision,
                checker_reasons=result.reasons,
                final_decision=result.decision,
                halt_reason="checker_unresolved",
            )
        if result.decision is CheckerDecision.REJECT:
            return self._apply_rejection(
                raw_query=raw_query,
                mechanical_reasons=(),
                checker_raw_output=result.raw_output,
                checker_decision=result.decision,
                checker_reasons=result.reasons,
                decision_source="checker",
            )

        event = self._append_event(
            raw_query=raw_query,
            mechanical_reasons=(),
            checker_raw_output=result.raw_output,
            checker_decision=result.decision,
            checker_reasons=result.reasons,
            final_decision=CheckerDecision.ALLOW,
            decision_source="checker",
            slot_consumed=True,
            oracle_eligible=True,
            halted=False,
        )
        self._slot += 1
        self._attempt = 1
        return event

