"""The canary dual-key query gate, as one callable the judge loop can drive.

Composes three already-frozen pieces that nothing had yet composed:

- :class:`rejudge.phase2_query_gate.Phase2QueryGate`, which owns the mechanical screen and the
  frozen one-free-retry slot machine and drives an injected checker;
- the frozen gemma checker configuration (``phase2_checker_frozen_config_2026-07-23.json``);
- :class:`rejudge.phase2_dual_gate.DualGate`, the AND-join with the metadata-blinded reviewer.

The result is callable as ``(raw_query, claim, slot, attempt) -> (action, feedback)``, the
contract ``rejudge.judge_loop.run_judgment`` expects for its ``query_gate`` parameter.

Two protocol properties shape the ordering, and both are load-bearing.

**Every raw query gets a reviewer decision**, including ones the mechanical screen or the
checker rejected. The ratified governance keeps all raw candidates in the review queue so that
"inclusion reveals nothing"; reviewing only checker-allowed queries would make the reviewer's
inclusion set identical to the checker's allow set, leaking to a gate that is required to be
blind to the checker's verdict. The reviewer decision is therefore obtained first, before the
checker is consulted at all.

**An unlabelled payload must never reach DualGate.** ``DualGate.review`` commits any
``reviewer_call`` exception permanently as non-ALLOW, and the store refuses a second commit for
the same payload hash, so signalling "not labelled yet" through the gate would irreversibly burn
that query closed. Under ``pause_when_unlabeled`` the gate queues the payload and raises
:class:`PendingReviewerDecision` before touching either the reviewer or the checker.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rejudge.phase2_dual_gate import DualGate, payload_hash
from rejudge.phase2_query_gate import (
    CheckerDecision, CheckerRequest, Phase2QueryGate)
from rejudge.phase2_role_limits import resolve_request_parameters

ROOT = Path(__file__).resolve().parents[1]
FROZEN_CONFIG_PATH = ROOT / "rejudge" / "phase2_checker_frozen_config_2026-07-23.json"
DESIGN_PATH = ROOT / "rejudge" / "phase2_checker_validation_design_2026-07-18.json"
ROLE_LIMITS_PATH = ROOT / "rejudge" / "phase2_role_limits_v5_2026-07-19.json"
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"

CHECKER_LIMITS_ROLE = "query_checker"


class FrozenCheckerDrift(RuntimeError):
    """Raised when the frozen checker prompt no longer matches its ratified hash."""


class CanaryCellHalted(RuntimeError):
    """Raised when the query gate halts a cell fail-closed (outage, malformed, unresolved)."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class PendingReviewerDecision(RuntimeError):
    """Raised when a payload has no committed reviewer decision and the runner must pause."""

    def __init__(self, payload_sha256: str) -> None:
        super().__init__(f"reviewer decision pending for payload {payload_sha256}")
        self.payload_sha256 = payload_sha256


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _frozen_variant_prompt() -> str:
    """The v4_merged system prompt, read from the module that defines it."""
    from rejudge.phase2_checker_dev_runner import VARIANTS

    return VARIANTS["v4_merged"]


def load_frozen_checker_prompt() -> str:
    """Return the ratified checker system prompt, refusing any drift from its frozen hash."""
    expected = _load(FROZEN_CONFIG_PATH)["configuration"]["system_prompt_sha256"]
    prompt = _frozen_variant_prompt()
    actual = sha256_text(prompt)
    if actual != expected:
        raise FrozenCheckerDrift(
            f"frozen checker system prompt drifted: expected {expected}, got {actual}")
    return prompt


def load_frozen_checker_user_template() -> str:
    return _load(DESIGN_PATH)["candidate_models"]["checker_prompt"]["user_prompt_template"]


def resolve_checker_max_tokens() -> int:
    """The max_tokens the provider actually receives for a checker call.

    The frozen configuration records 16 requested with the 4096 reasoning floor applied, because
    the development client left the floor implicit. Under the strict production client a request
    below the floor raises instead of being floored, so the value must be resolved explicitly
    from the frozen role limits rather than passed as the bare 16.
    """
    resolved = resolve_request_parameters(
        _load(ROLE_LIMITS_PATH), _load(PROTOCOL_PATH),
        _load(FROZEN_CONFIG_PATH)["configuration"]["model"], CHECKER_LIMITS_ROLE)
    return resolved.effective_max_tokens


class FrozenCheckerAdapter:
    """The frozen gemma checker, shaped as the injected checker Phase2QueryGate expects.

    Returns the provider completion verbatim. The frozen parser accepts only an exact
    lower-case token, so a trailing newline is malformed by design; normalising it here would
    silently change the configuration's observed error rate.
    """

    def __init__(self, client, *, request_metadata: dict | None = None) -> None:
        config = _load(FROZEN_CONFIG_PATH)["configuration"]
        self._client = client
        self._model = config["model"]
        self._temperature = config["decoding"]["temperature"]
        self._seed = config["decoding"]["seed"]
        self._system = load_frozen_checker_prompt()
        self._user_template = load_frozen_checker_user_template()
        self._max_tokens = resolve_checker_max_tokens()
        self._request_metadata = dict(request_metadata or {})

    def __call__(self, request: CheckerRequest) -> str:
        user = self._user_template.format(
            candidate_a=request.candidate_a, candidate_b=request.candidate_b,
            query=request.raw_query)
        messages = [{"role": "system", "content": self._system},
                    {"role": "user", "content": user}]
        return self._client.complete(
            messages, self._model, self._temperature, self._seed, self._max_tokens,
            kind="verdict",
            request_metadata={**self._request_metadata, "call_role": CHECKER_LIMITS_ROLE,
                              "slot": request.slot, "attempt": request.attempt})


class CanaryQueryGate:
    """Dual-key gate for one judgment cell, callable as judge_loop's ``query_gate``."""

    def __init__(self, *, candidate_a: str, candidate_b: str, total_slots: int,
                 checker, dual_gate: DualGate, rejection_payload: str,
                 no_query_payload: str, pause_when_unlabeled: bool = False) -> None:
        self._candidate_a = candidate_a
        self._candidate_b = candidate_b
        self._dual_gate = dual_gate
        self._rejection_payload = rejection_payload
        self._no_query_payload = no_query_payload
        self._pause_when_unlabeled = pause_when_unlabeled
        self._gate = Phase2QueryGate(
            candidate_a=candidate_a, candidate_b=candidate_b, total_slots=total_slots,
            checker=checker)
        self.pending: list[dict[str, str]] = []
        self.intercepts: list[str] = []
        self.events: list[dict] = []

    def _ensure_reviewer_decision(self, raw_query: str):
        """Obtain the committed reviewer decision, or pause before anything is spent."""
        sha = payload_hash(raw_query, self._candidate_a, self._candidate_b)
        if self._dual_gate.store.get(sha) is None and self._pause_when_unlabeled:
            payload = {"payload_sha256": sha, "query": raw_query,
                       "candidate_a": self._candidate_a, "candidate_b": self._candidate_b}
            if payload not in self.pending:
                self.pending.append(payload)
            raise PendingReviewerDecision(sha)
        return self._dual_gate.review(raw_query, self._candidate_a, self._candidate_b)

    def __call__(self, raw_query: str, claim: str, slot: int, attempt: int):
        # The reviewer decision is committed first, for every raw query and before the checker
        # is consulted, so the reviewer's inclusion set carries no information about the
        # checker's verdict.
        reviewer = self._ensure_reviewer_decision(raw_query)

        event = self._gate.submit(raw_query)
        if event.halted:
            raise CanaryCellHalted(event.halt_reason, event.checker_error)

        if event.final_decision is CheckerDecision.REJECT:
            action = "block" if event.slot_consumed else "retry"
            feedback = self._no_query_payload if event.slot_consumed else self._rejection_payload
            self._record(event, reviewer, action)
            return action, feedback

        outcome = self._dual_gate.decide(
            checker_decision=event.final_decision.value, raw_query=raw_query,
            candidate_a=self._candidate_a, candidate_b=self._candidate_b)
        if outcome.checker_false_allow_intercept:
            self.intercepts.append(reviewer.payload_sha256)
        if outcome.dispatch_allowed:
            self._record(event, reviewer, "allow")
            return "allow", None
        self._record(event, reviewer, "block")
        return "block", self._no_query_payload

    def _record(self, event, reviewer, action: str) -> None:
        """Arm, mode and cell linkage lives here, never in the metadata-free decision store."""
        self.events.append({
            **event.as_record(),
            "reviewer_payload_sha256": reviewer.payload_sha256,
            "reviewer_label": reviewer.label,
            "reviewer_clause": reviewer.clause,
            "reviewer_status": reviewer.status,
            "action": action,
        })
