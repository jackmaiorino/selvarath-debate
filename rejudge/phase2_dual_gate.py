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
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# How long a worker waits to inherit a decision another worker is currently obtaining. It
# bounds a wait, it does not bound the review itself: exceeding it means the owner has
# neither committed nor released, which is a stuck run rather than a slow reviewer, and the
# waiter raises instead of proceeding ungated.
INHERIT_WAIT_SECONDS = 900.0


class ReservationAbandoned(RuntimeError):
    """Raised when the worker that reserved a payload neither committed nor released it."""

REVIEWER_LABELS = ("ALLOW", "REJECT", "CONTRACT_AMBIGUOUS")
CLAUSES = ("Allowed", "P1", "P2", "P3", "P4")
DECISION_STATUSES = ("parsed", "malformed", "reviewer_error")
DECISION_ROW_KEYS = frozenset({
    "payload_sha256", "label", "clause", "rationale", "raw_output", "status",
    "sequence", "prev_event_hash", "event_hash",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

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


def _label_clause_contract_violated(label: str, clause: str) -> bool:
    """The same two rules ``_validate_decision_fields`` enforces on a committed 'parsed' row.

    Kept here, not just there, so a violation is caught at PARSE time and never reaches a
    commit call as a self-contradictory 'parsed' ruling in the first place (see
    ``parse_reviewer_output``'s docstring for why that distinction matters).
    """
    if label == "ALLOW" and clause != "Allowed":
        return True
    if label == "REJECT" and clause not in {"P1", "P2", "P3", "P4"}:
        return True
    return False


def parse_reviewer_output(raw: str | None) -> tuple[str | None, str | None, str | None]:
    """Return (label, clause, rationale) or (None, None, None) if malformed.

    Malformed covers two different failure shapes, both committed identically by the frozen
    failure rule: text that never matches the LABEL/CLAUSE/RATIONALE three-line protocol at
    all (garbage, truncation, a refusal), and text that matches the three-line shape but pairs
    a label with a clause the contract forbids for it (ALLOW with anything but 'Allowed';
    REJECT with anything but P1-P4). The second shape is a real gap in the frozen clause
    taxonomy rather than a misbehaving reviewer: the four frozen clauses (P1 answer-label
    queries, P2 candidate restatements, P3 compound claims, P4 meta/evaluative queries) have
    no entry for an EMPTY query, which asserts nothing, or an OPEN QUESTION, which requests
    information rather than asserting a claim. Facing one of those two shapes, a reviewer that
    correctly rejects the query has no valid clause left to cite and picks the only remaining
    one, 'Allowed', producing LABEL: REJECT / CLAUSE: Allowed -- a combination the line regex
    accepts (both are individually valid tokens) but the contract forbids.

    That happened four times across roughly 22,000 canary/main gate reviews (two empty-query
    occurrences on 2026-08-09, two open-question occurrences on 2026-08-10 and 2026-08-11; see
    rejudge/phase2_main_contract_gap_2026-08-09.json and its occurrence3/occurrence4
    successors). Every
    time, parsing had called the ruling 'parsed' while ``_validate_decision_fields`` refused it
    as self-contradictory, so ``DualGateDecisionStore.commit`` raised an uncaught ValueError
    and aborted the rest of the in-flight commit wave -- one abort mid-wave left 158 of 216
    rulings committed and the other 58 undone. The operator applied the frozen failure rule by
    hand each time (commit as 'malformed', null parsed fields, raw output preserved verbatim,
    non-ALLOW so nothing dispatches) rather than let the abort stand. Folding the same
    contract check in here means that disposition now happens automatically: a label/clause
    pair the contract forbids is malformed, exactly like text with no parseable lines at all,
    so the wave keeps committing past it instead of raising.

    A REJECT citing a valid P1-P4 clause, or an ALLOW citing 'Allowed', is unaffected: only
    the two contract-forbidden pairings are reclassified. Text that never matches the
    three-line shape in the first place was already malformed and stays that way.
    """
    match = _LINE_RE.match(raw.strip()) if raw else None
    if not match:
        return None, None, None
    label, clause, rationale = (
        match.group("label"), match.group("clause"), match.group("rationale"))
    if _label_clause_contract_violated(label, clause):
        return None, None, None
    return label, clause, rationale


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str):
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _strict_json_row(line: str) -> dict:
    row = json.loads(
        line, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_non_finite)
    if not isinstance(row, dict):
        raise ValueError("decision row must be a JSON object")
    return row


def _validate_decision_fields(*, payload_sha: str, label: str | None,
                              clause: str | None, rationale: str | None,
                              raw_output: str, status: str) -> None:
    if not isinstance(payload_sha, str) or _SHA256_RE.fullmatch(payload_sha) is None:
        raise ValueError("payload_sha256 must be exactly 64 lower-case hexadecimal characters")
    if status not in DECISION_STATUSES:
        raise ValueError(f"unknown reviewer decision status: {status!r}")
    if not isinstance(raw_output, str):
        raise ValueError("reviewer raw_output must be text")
    if status == "parsed":
        parsed = parse_reviewer_output(raw_output)
        if parsed != (label, clause, rationale):
            raise ValueError(
                "parsed reviewer fields do not match the preserved raw three-line output")
        if label == "ALLOW" and clause != "Allowed":
            raise ValueError("reviewer ALLOW must cite clause 'Allowed'")
        if label == "REJECT" and clause not in {"P1", "P2", "P3", "P4"}:
            raise ValueError("reviewer REJECT must cite one of P1, P2, P3, or P4")
    elif any(value is not None for value in (label, clause, rationale)):
        raise ValueError(f"{status} reviewer decisions must carry null parsed fields")


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
        # Guards the chain tail and the reservation table together: a commit that read the
        # tail hash outside this lock could be appended after another commit had already
        # moved it, producing a file that no longer verifies. Process-level exclusion is a
        # separate concern and remains the archive lease's job.
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        self._last_hash = "genesis"
        self._sequence = -1
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = _strict_json_row(line)
                if set(row) != DECISION_ROW_KEYS:
                    raise ValueError("decision row fields drifted")
                if type(row["sequence"]) is not int or row["sequence"] != self._sequence + 1:
                    raise ValueError(
                        f"decision sequence is not contiguous: expected {self._sequence + 1}, "
                        f"found {row['sequence']!r}")
                if row["prev_event_hash"] != self._last_hash:
                    raise ValueError(
                        f"decision chain broken at sequence {row['sequence']}: expected "
                        f"previous {self._last_hash!r}, found {row['prev_event_hash']!r}")
                # Integrity before semantics: a row that fails its own event hash was
                # modified after write, and must be reported as tampering rather than as
                # whatever field inconsistency the tampering happens to produce.
                expected = self._row_hash(row)
                if expected != row["event_hash"]:
                    raise ValueError(
                        f"decision chain corrupt at sequence {row['sequence']}")
                if row["payload_sha256"] in self._by_payload:
                    raise ValueError(
                        f"duplicate reviewer decision for payload {row['payload_sha256']}")
                _validate_decision_fields(
                    payload_sha=row["payload_sha256"], label=row["label"],
                    clause=row["clause"], rationale=row["rationale"],
                    raw_output=row["raw_output"], status=row["status"])
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
        with self._lock:
            return self._by_payload.get(payload_sha)

    def get_or_reserve(self, payload_sha: str) -> tuple[ReviewerDecision | None, bool]:
        """Claim the right to review a payload, or learn that someone else has it.

        Returns ``(decision, owned)``. A non-null decision is already committed and should be
        inherited. ``owned`` true means this caller must go on to commit or release. Both
        null and not owned means another worker is mid-review: wait on
        :meth:`await_decision`.

        This exists because keying decisions by payload hash makes concurrent duplicates the
        normal case rather than a corner case. Two workers that both miss the store would both
        ask the reviewer, and the store admits exactly one ruling per payload, so the loser's
        commit would raise and its cell would die on a question that was answered correctly.
        """
        with self._lock:
            existing = self._by_payload.get(payload_sha)
            if existing is not None:
                return existing, False
            if payload_sha in self._inflight:
                return None, False
            self._inflight[payload_sha] = threading.Event()
            return None, True

    def await_decision(self, payload_sha: str,
                       timeout: float = INHERIT_WAIT_SECONDS) -> ReviewerDecision | None:
        """Block until the owning worker commits or releases this payload.

        Returns the committed decision, or None if the owner released without committing (the
        caller should then try to claim it itself). Raises rather than returning ungated when
        the owner does neither: a silent fall-through here would dispatch a query no reviewer
        ever ruled on, which is the exact failure the dual gate exists to prevent.
        """
        with self._lock:
            event = self._inflight.get(payload_sha)
            if event is None:
                return self._by_payload.get(payload_sha)
        if not event.wait(timeout):
            raise ReservationAbandoned(
                f"no reviewer decision for payload {payload_sha} after {timeout:.0f}s; the "
                "worker holding its reservation neither committed nor released it")
        with self._lock:
            return self._by_payload.get(payload_sha)

    def release(self, payload_sha: str) -> None:
        """Give up a reservation without committing, waking anyone waiting on it."""
        with self._lock:
            event = self._inflight.pop(payload_sha, None)
        if event is not None:
            event.set()

    def commit(self, payload_sha: str, label: str | None, clause: str | None,
               rationale: str | None, raw_output: str, status: str) -> ReviewerDecision:
        _validate_decision_fields(
            payload_sha=payload_sha, label=label, clause=clause, rationale=rationale,
            raw_output=raw_output, status=status)
        with self._lock:
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
            event = self._inflight.pop(payload_sha, None)
        if event is not None:
            event.set()
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
        while True:
            existing, owned = self.store.get_or_reserve(sha)
            if existing is not None:
                return existing
            if owned:
                break
            # Another worker is asking the reviewer about this exact payload. Wait for its
            # answer rather than asking a second time: the store admits one ruling per
            # payload precisely so a query gets no second chance to flip.
            inherited = self.store.await_decision(sha)
            if inherited is not None:
                return inherited
            # The owner released without committing, so the payload is claimable again.

        committed = False
        try:
            try:
                raw = self.reviewer_call(raw_query, candidate_a, candidate_b)
            except Exception as exc:  # noqa: BLE001 - fail closed, never crash dispatch
                decision = self.store.commit(
                    sha, None, None, None,
                    f"<reviewer_error: {type(exc).__name__}: {exc}>", "reviewer_error")
                committed = True
                return decision
            label, clause, rationale = parse_reviewer_output(raw)
            status = "parsed" if label is not None else "malformed"
            decision = self.store.commit(sha, label, clause, rationale, raw or "", status)
            committed = True
            return decision
        finally:
            # Anything that escapes without a commit (a cancelled worker, a validation error
            # inside commit) must hand the payload back rather than strand every other cell
            # that proposes the same query text.
            if not committed:
                self.store.release(sha)

    def decide(self, *, checker_decision: str, raw_query: str,
               candidate_a: str, candidate_b: str) -> GateOutcome:
        reviewer = self.review(raw_query, candidate_a, candidate_b)
        checker_allow = checker_decision == "allow"
        dispatch = checker_allow and reviewer.effective_allow
        intercept = checker_allow and not reviewer.effective_allow
        return GateOutcome(
            dispatch_allowed=dispatch, checker_decision=checker_decision,
            reviewer=reviewer, checker_false_allow_intercept=intercept)
