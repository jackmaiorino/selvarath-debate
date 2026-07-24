"""Dual gate for canary query dispatch (amendment canary_claude_gate_2026-07-23).

Composes the frozen checker verdict with a metadata-blinded Claude reviewer
decision. Design contract, from the amendment:

- The reviewer decision for a payload is committed immutably (hash-chained,
  fsynced) BEFORE the checker/Claude join and before any dispatch.
- Decisions are keyed by the payload hash: an exact retry or an identical query
  in another arm INHERITS the committed decision; the reviewer is never asked
  twice about the same payload (no second chance to flip).
- The decision store is metadata-free: payload, decision, chain fields only.
  Arm/mode/judge linkage lives in the runner's dispatch log, never here.
- Reviewer output must parse as the frozen three-line protocol; parse failure,
  timeout, reviewer error, or CONTRACT_AMBIGUOUS commits as effectively
  non-ALLOW (fail closed). The raw output is preserved verbatim.
- Dispatch (real oracle or placebo responder alike) requires
  checker ALLOW AND reviewer ALLOW; every other combination is the identical
  blocked transition, with checker-ALLOW/reviewer-non-ALLOW additionally
  tagged checker_false_allow_intercept.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REVIEWER_LABELS = ("ALLOW", "REJECT", "CONTRACT_AMBIGUOUS")
CLAUSES = ("Allowed", "P1", "P2", "P3", "P4")

_LINE_RE = re.compile(
    r"^\s*LABEL:\s*(?P<label>ALLOW|REJECT|CONTRACT_AMBIGUOUS)\s*\n"
    r"\s*CLAUSE:\s*(?P<clause>Allowed|P1|P2|P3|P4)\s*\n"
    r"\s*RATIONALE:\s*(?P<rationale>\S.*?)\s*$",
    re.DOTALL,
)


def payload_hash(raw_query: str, candidate_a: str, candidate_b: str) -> str:
    canonical = json.dumps(
        {"query": raw_query, "candidate_a": candidate_a, "candidate_b": candidate_b},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_reviewer_output(raw: str) -> tuple[str | None, str | None, str | None]:
    """Return (label, clause, rationale) or (None, None, None) if malformed."""
    match = _LINE_RE.match(raw.strip()) if raw else None
    if not match:
        return None, None, None
    return match.group("label"), match.group("clause"), match.group("rationale")


@dataclass(frozen=True)
class ReviewerDecision:
    payload_sha256: str
    label: str | None          # parsed label, None when malformed/errored
    clause: str | None
    rationale: str | None
    raw_output: str
    status: str                # "parsed" | "malformed" | "reviewer_error"
    sequence: int
    event_hash: str

    @property
    def effective_allow(self) -> bool:
        return self.status == "parsed" and self.label == "ALLOW"


@dataclass(frozen=True)
class GateOutcome:
    dispatch_allowed: bool
    checker_decision: str      # "allow" | "reject" | "unresolved" (frozen gate vocab)
    reviewer: ReviewerDecision
    checker_false_allow_intercept: bool


class DualGateDecisionStore:
    """Append-only, hash-chained, metadata-free reviewer decision log."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._by_payload: dict[str, ReviewerDecision] = {}
        self._last_hash = "genesis"
        self._sequence = -1
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                expected = self._row_hash(row)
                if expected != row["event_hash"]:
                    raise ValueError(
                        f"decision chain corrupt at sequence {row['sequence']}")
                self._last_hash = row["event_hash"]
                self._sequence = row["sequence"]
                self._by_payload[row["payload_sha256"]] = ReviewerDecision(
                    payload_sha256=row["payload_sha256"], label=row["label"],
                    clause=row["clause"], rationale=row["rationale"],
                    raw_output=row["raw_output"], status=row["status"],
                    sequence=row["sequence"], event_hash=row["event_hash"])

    @staticmethod
    def _row_hash(row: dict) -> str:
        material = json.dumps(
            {k: row[k] for k in ("payload_sha256", "label", "clause", "rationale",
                                  "raw_output", "status", "sequence", "prev_event_hash")},
            ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, payload_sha: str) -> ReviewerDecision | None:
        return self._by_payload.get(payload_sha)

    def commit(self, payload_sha: str, label: str | None, clause: str | None,
               rationale: str | None, raw_output: str, status: str) -> ReviewerDecision:
        if payload_sha in self._by_payload:
            raise ValueError(f"decision already committed for {payload_sha}")
        row = {
            "payload_sha256": payload_sha, "label": label, "clause": clause,
            "rationale": rationale, "raw_output": raw_output, "status": status,
            "sequence": self._sequence + 1, "prev_event_hash": self._last_hash,
        }
        row["event_hash"] = self._row_hash(row)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._sequence = row["sequence"]
        self._last_hash = row["event_hash"]
        decision = ReviewerDecision(
            payload_sha256=payload_sha, label=label, clause=clause,
            rationale=rationale, raw_output=raw_output, status=status,
            sequence=row["sequence"], event_hash=row["event_hash"])
        self._by_payload[payload_sha] = decision
        return decision


class DualGate:
    """checker AND metadata-blinded reviewer; reviewer decisions via the store.

    ``reviewer_call`` receives ONLY the payload text (query, candidate_a,
    candidate_b) and returns the reviewer's raw output string. It must be wired
    to an isolated context with the frozen reviewer prompt; this module never
    passes it any metadata. Exceptions from ``reviewer_call`` commit as
    reviewer_error -> non-ALLOW (fail closed).
    """

    def __init__(self, store: DualGateDecisionStore,
                 reviewer_call: Callable[[str, str, str], str]) -> None:
        self.store = store
        self.reviewer_call = reviewer_call

    def review(self, raw_query: str, candidate_a: str, candidate_b: str) -> ReviewerDecision:
        sha = payload_hash(raw_query, candidate_a, candidate_b)
        existing = self.store.get(sha)
        if existing is not None:
            return existing
        try:
            raw = self.reviewer_call(raw_query, candidate_a, candidate_b)
        except Exception as exc:  # noqa: BLE001 - fail closed, never crash dispatch
            return self.store.commit(sha, None, None, None,
                                     f"<reviewer_error: {type(exc).__name__}: {exc}>",
                                     "reviewer_error")
        label, clause, rationale = parse_reviewer_output(raw)
        status = "parsed" if label is not None else "malformed"
        return self.store.commit(sha, label, clause, rationale, raw or "", status)

    def decide(self, *, checker_decision: str, raw_query: str,
               candidate_a: str, candidate_b: str) -> GateOutcome:
        reviewer = self.review(raw_query, candidate_a, candidate_b)
        checker_allow = checker_decision == "allow"
        dispatch = checker_allow and reviewer.effective_allow
        intercept = checker_allow and not reviewer.effective_allow
        return GateOutcome(
            dispatch_allowed=dispatch, checker_decision=checker_decision,
            reviewer=reviewer, checker_false_allow_intercept=intercept)
