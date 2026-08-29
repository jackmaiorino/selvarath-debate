"""Immutable request-level journal: the main-run replacement for the call cache.

Why the cache is not enough. The canary cache deliberately refuses to memoise a
visibly-empty response so that a transient empty can be recovered by a later live call
(rejudge.phase2_caching_client). Run phase3-v3-82c8f75feba42a9e demonstrated the cost of
that doctrine at scale: some prompts (question VS-019 for the Qwen3.8 judge) reproducibly
drive the judge into visible-empty truncation, the unmemoised call is re-dispatched after a
relaunch, the provider completes it NON-deterministically even at temperature 0 with a fixed
seed, and the fresh path permanently mismatches every cached downstream row. Three judgment
cells were terminally excluded that way (terminal-halt records 006 to 008), and the
2026-08-29 Codex close-out review made a journal of this exact shape a blocking condition
for any main run (rejudge/phase3_v3_codex_closeout_consult_2026-08-29.md, point C).

The journal inverts the doctrine: EVERY response a provider returns is durably recorded
before the caller sees it, including a visibly-empty one, and a journaled request is never
dispatched again. Nondeterminism therefore enters at most once per request; every replay is
byte-deterministic from the journal. A degenerate response is not silently retried into a
different sample: it flows to the caller, whose frozen machinery already gives it a bounded
disposition (the query gate's retry-then-block ladder consumes an attempt, an empty checker
decision is a terminal checker_malformed, an empty verdict is counted by the invalid gate).

What replaces re-dispatch for genuinely lost responses: if the spend ledger settled a
success but no journal entry exists (the process died in the window between settle and
journal append), the response bytes are gone and dispatching a substitute would silently
select among nondeterministic generations. Per the Codex review, such a cell is resolved
INVALID, never re-dispatched; :func:`find_ambiguous_dispatches` detects exactly this at
resume time and the driver must refuse to execute the affected cells. A call that FAILED
(timeout, HTTP error) journals nothing and stays re-dispatchable: no response bytes ever
existed, so a later live call cannot contradict recorded state. Whether that transient
policy needs further restriction is explicitly reserved for the dedicated main-protocol
consult.

Cell atomicity holds by construction: a result row is composed only from responses that
came through the journal, so by the time the row is written every constituent request is
already durably recorded.

Chain discipline is identical to the call cache (append-only, hash-chained, fsynced,
identity-seeded): a torn write, an edited row, or a foreign identity is a hard stop on
load, never a silent partial replay.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

# One source of truth for call identity and request fingerprints: the frozen cache module.
# Re-deriving either here would let the two drift and break the mismatch guards.
from rejudge.phase2_call_cache import CallKey, request_fingerprint
# The slot/attempt derivations are deliberately imported from the caching client even though
# they are module-private there: every call site numbers its calls through those exact
# functions, and the journal must key a ledger event the same way the dispatch path did.
from rejudge.phase2_caching_client import UncacheableCall, _derive_attempt, _derive_slot


class JournalReplayMismatch(RuntimeError):
    """A journaled request no longer matches the request being made.

    Same fail-closed semantics as the cache's CallReplayMismatch: serving a response that
    was generated for a different prompt would silently corrupt the run.
    """


class JournalIdentityMismatch(RuntimeError):
    """The journal file belongs to a different execution identity."""


def journal_key(request_metadata: Mapping[str, Any] | None) -> CallKey:
    """Derive the journal key for a call, byte-compatible with the cache's keying."""
    if not request_metadata:
        raise UncacheableCall(
            "request_metadata is required: an unjournalable call cannot be replayed on "
            "resume, and would be re-dispatched on every relaunch")
    cell_key = request_metadata.get("cell_key")
    call_role = request_metadata.get("call_role")
    if not cell_key or not call_role:
        raise UncacheableCall(
            "request_metadata must carry both cell_key and call_role; got "
            f"cell_key={cell_key!r}, call_role={call_role!r}")
    return CallKey(cell_key=str(cell_key), call_role=str(call_role),
                   slot=_derive_slot(request_metadata),
                   attempt=_derive_attempt(request_metadata))


class RequestJournal:
    """Append-only, hash-chained, fsynced record of every provider response."""

    def __init__(self, path: str | Path, *, execution_identity: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.execution_identity = execution_identity
        self._entries: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self._seed = f"identity:{execution_identity}" if execution_identity else "genesis"
        self._last_hash = self._seed
        self._sequence = -1
        self.replayed = 0
        self.recorded = 0
        if self.path.exists():
            self._load()

    @staticmethod
    def _row_hash(row: dict) -> str:
        material = json.dumps(
            {k: row[k] for k in ("cell_key", "call_role", "slot", "attempt",
                                 "request_sha256", "response", "sequence",
                                 "prev_event_hash")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["prev_event_hash"] != self._last_hash:
                if row["sequence"] == 0 and self._sequence == -1:
                    raise JournalIdentityMismatch(
                        f"request journal {self.path} does not belong to this run: its "
                        f"first row chains from {row['prev_event_hash']!r}, but this run "
                        f"expects {self._seed!r}. A journal from another execution identity "
                        "must never be replayed")
                raise ValueError(
                    f"request journal chain broken at sequence {row['sequence']}: expected "
                    f"previous {self._last_hash}, found {row['prev_event_hash']}")
            if self._row_hash(row) != row["event_hash"]:
                raise ValueError(
                    f"request journal row tampered at sequence {row['sequence']}")
            self._last_hash = row["event_hash"]
            self._sequence = row["sequence"]
            self._entries[self._identity(row)] = row

    @staticmethod
    def _identity(row) -> tuple:
        if isinstance(row, CallKey):
            return (row.cell_key, row.call_role, row.slot, row.attempt)
        return (row["cell_key"], row["call_role"], row["slot"], row["attempt"])

    def get(self, key: CallKey, fingerprint: str) -> str | None:
        """Return the journaled response, or None when the request was never dispatched.

        Unlike the cache, an empty string is a real journaled response and is returned as
        such; only a genuinely absent entry returns None.
        """
        with self._lock:
            row = self._entries.get(self._identity(key))
        if row is None:
            return None
        if row["request_sha256"] != fingerprint:
            raise JournalReplayMismatch(
                f"journaled call {key.call_role} slot {key.slot} attempt {key.attempt} in "
                f"cell {key.cell_key} was dispatched under request {row['request_sha256']}, "
                f"but the current request is {fingerprint}; refusing to replay a response "
                "generated for a different prompt")
        self.replayed += 1
        return row["response"]

    def put(self, key: CallKey, fingerprint: str, response: str) -> None:
        """Record one response, empty or not. Refuses to overwrite: append-only."""
        identity = self._identity(key)
        with self._lock:
            if identity in self._entries:
                raise ValueError(f"request already journaled: {asdict(key)}")
            row = {
                **asdict(key), "request_sha256": fingerprint, "response": response,
                "sequence": self._sequence + 1, "prev_event_hash": self._last_hash,
            }
            row["event_hash"] = self._row_hash(row)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._sequence = row["sequence"]
            self._last_hash = row["event_hash"]
            self._entries[identity] = row
            self.recorded += 1

    def has(self, key: CallKey) -> bool:
        with self._lock:
            return self._identity(key) in self._entries


class JournalingClient:
    """Dispatches each request at most once, ever; replays everything else verbatim."""

    def __init__(self, inner, journal: RequestJournal) -> None:
        self.inner = inner
        self.journal = journal

    @property
    def dry_run(self) -> bool:
        return getattr(self.inner, "dry_run", False)

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata=None) -> str:
        key = journal_key(request_metadata)
        fingerprint = request_fingerprint(
            messages=messages, model=model, temperature=temperature, seed=seed,
            max_tokens=max_tokens)
        journaled = self.journal.get(key, fingerprint)
        if journaled is not None:
            return journaled
        # A failed call (timeout, HTTP error) raises out of here with nothing journaled:
        # no response bytes exist, so the request stays dispatchable. A RETURNED response
        # is journaled unconditionally -- visibly-empty included -- before the caller can
        # act on it, so replay after this point is byte-deterministic.
        response = self.inner.complete(
            messages, model, temperature, seed, max_tokens, kind=kind,
            request_metadata=request_metadata)
        self.journal.put(key, fingerprint, response)
        return response


def find_ambiguous_dispatches(
        journal: RequestJournal,
        ledger_events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Detect ledger-settled successes whose response bytes were never journaled.

    Covers the one crash window the client cannot close: the ledger settles inside the
    inner client BEFORE the journal append, so a death in between leaves a success event
    with no recorded response. Re-dispatching would substitute a fresh nondeterministic
    generation for one that demonstrably existed; the affected cell must instead be
    resolved INVALID under the main protocol's ambiguous-dispatch disposition.

    Also flags a key with more than one settled success: under the journal regime a
    journaled request is never re-dispatched, so a duplicate success is a protocol
    violation worth halting on, not a normal retry.

    Ledger events without journalable metadata (no cell_key/call_role) are ignored: only
    per-cell provider calls are journaled.
    """
    success_counts: dict[tuple, int] = {}
    samples: dict[tuple, dict] = {}
    for event in ledger_events:
        if event.get("status") != "success":
            continue
        metadata = event.get("metadata") or {}
        if not metadata.get("cell_key") or not metadata.get("call_role"):
            continue
        key = journal_key(metadata)
        identity = (key.cell_key, key.call_role, key.slot, key.attempt)
        success_counts[identity] = success_counts.get(identity, 0) + 1
        samples.setdefault(identity, {
            "cell_key": key.cell_key, "call_role": key.call_role,
            "slot": key.slot, "attempt": key.attempt,
            "ledger_sequence": event.get("sequence")})
    findings = []
    for identity, count in sorted(success_counts.items()):
        journaled = journal.has(CallKey(*identity))
        if not journaled:
            findings.append({**samples[identity], "problem": "success_without_journal_entry",
                             "success_events": count})
        elif count > 1:
            findings.append({**samples[identity], "problem": "duplicate_success_for_key",
                             "success_events": count})
    return findings
