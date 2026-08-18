"""Offline fixtures for rehearsing the canary without touching a provider.

``RejudgeClient(dry_run=True)`` cannot serve this purpose: its dry-run whitelist covers only
the query/oracle/verdict kinds and rejects the ``query_checker`` role outright, so the canary's
gate path is unrehearsable through it. This module supplies a client that answers every canary
call role, in the exact formats the frozen parsers accept, plus a reviewer stub for the dual
gate.

Two properties matter more than convenience. Every default output is chosen so the *real*
parser accepts it, so a rehearsal exercises the same parsing code the live run will. And an
unrecognised call role is refused rather than answered, because silently serving one would let
a rehearsal pass over a path the fixture never modelled.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence


class UnknownCallRole(ValueError):
    """Raised when a call carries a role this fixture does not model."""


def scripted(items: Sequence[Any]) -> Callable[..., Any]:
    """Return a callable that yields ``items`` in order, raising any exception it finds.

    Exhausting the script repeats its final entry, so a fixture that only cares about the
    first few calls does not have to enumerate the rest.
    """
    remaining = list(items)
    if not remaining:
        raise ValueError("a script needs at least one entry")

    def _next(*_args, **_kwargs):
        item = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(item, BaseException):
            raise item
        return item

    return _next


class DeterministicCanaryClient:
    """Answers every canary call role offline, deterministically, at zero cost.

    Each role may be overridden with a callable taking ``(metadata, messages)``; the
    :func:`scripted` helper covers the common case of a fixed sequence.
    """

    def __init__(self, *, debater_turn=None, judge_query=None, oracle_verification=None,
                 query_checker=None, judge_verdict=None, verdict_side: str = "A",
                 capability_qa=None, capability_side: str = "A") -> None:
        self.verdict_side = verdict_side
        # Additive: capability_qa is a phase-3-only call role (no phase-2 cell ever carries
        # it), so a phase-2 fixture user is unaffected by this default.
        self.capability_side = capability_side
        self.calls: list[dict[str, Any]] = []
        self._overrides = {
            "debater_turn": debater_turn,
            "judge_query": judge_query,
            "oracle_verification": oracle_verification,
            "query_checker": query_checker,
            "judge_verdict": judge_verdict,
            "batch_verdict": judge_verdict,
            "capability_qa": capability_qa,
        }

    @property
    def dry_run(self) -> bool:
        return True

    # -- defaults, each chosen to satisfy the frozen parser for its role -----------------

    def _default(self, call_role: str, metadata: Mapping[str, Any]) -> str:
        if call_role == "debater_turn":
            return (f"Deterministic debater turn for round "
                    f"{metadata.get('round_index', 0)} slot {metadata.get('slot_index', 0)}.")
        if call_role == "judge_query":
            # clean_extract_claim requires the CLAIM: prefix to count as well formed.
            return (f"CLAIM: deterministic fixture fact "
                    f"{metadata.get('query_index', 0)}")
        if call_role == "oracle_verification":
            return "YES"
        if call_role == "query_checker":
            # The frozen checker parser accepts an exact lower-case token and nothing else.
            return "allow"
        if call_role in ("judge_verdict", "batch_verdict"):
            return (f"VERDICT: Position {self.verdict_side}\nCONFIDENCE: 4\n"
                    "REASONING: deterministic fixture verdict")
        if call_role == "capability_qa":
            # Phase-3's tolerant parser (strip whitespace, allow one optional trailing
            # period): a bare period exercises that tolerance rather than only ever
            # producing the period-free form.
            return f"ANSWER: {self.capability_side}."
        raise UnknownCallRole(
            f"no fixture behaviour for call_role {call_role!r}; add one rather than letting a "
            "rehearsal pass over an unmodelled path")

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata=None) -> str:
        metadata = dict(request_metadata or {})
        call_role = metadata.get("call_role")
        if not call_role:
            raise UnknownCallRole(
                "request_metadata must carry call_role; this fixture dispatches on it")
        if call_role not in self._overrides:
            raise UnknownCallRole(
                f"no fixture behaviour for call_role {call_role!r}; add one rather than "
                "letting a rehearsal pass over an unmodelled path")
        self.calls.append({**metadata, "model": model, "kind": kind, "messages": messages})
        override = self._overrides.get(call_role)
        if override is not None:
            return override(metadata, messages)
        return self._default(str(call_role), metadata)


class StubReviewer:
    """Metadata-blinded reviewer stub emitting the frozen three-line protocol.

    A script entry may be a ``(label, clause)`` pair, or a raw string when the rehearsal needs
    to reach the fail-closed path for output the frozen parser rejects.
    """

    def __init__(self, script: Callable[..., Any] | None = None) -> None:
        self._script = script

    def __call__(self, raw_query: str, candidate_a: str, candidate_b: str) -> str:
        if self._script is None:
            return "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: single atomic factual claim"
        item = self._script(raw_query, candidate_a, candidate_b)
        if isinstance(item, str):
            return item
        label, clause = item
        return f"LABEL: {label}\nCLAUSE: {clause}\nRATIONALE: fixture rationale"
