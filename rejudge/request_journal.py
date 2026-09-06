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

What replaces re-dispatch for genuinely lost responses: if the spend ledger can no longer
prove that a dispatch completed without ambiguity, the formal measurement is stopped. This
includes a settled success with no journal entry, an unknown charge, a charged malformed
response, and a reservation with no terminal ledger event. A timeout is not evidence that
no response existed: the provider may have completed after the client stopped waiting.
Under the 2026-08-29 process reset, an environmental interruption voids the formal run and
the run restarts from a fresh identity. It is never repaired by substituting a fresh sample
inside the interrupted run. :func:`find_ambiguous_dispatches` exposes these states before a
recovery diagnosis can mistake them for clean completion. The main formal runner does not
resume an interrupted identity.

Request-before-result ordering holds by construction: a result row is composed only from
responses that came through the journal, so by the time the row writer is called every
constituent request is already durably recorded. Result-store atomicity is a separate runner
property and is not claimed here.

Chain discipline is identical to the call cache (append-only, hash-chained, fsynced,
identity-seeded): a torn write, an edited row, or a foreign identity is a hard stop on
load, never a silent partial replay.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

# One source of truth for call identity and request fingerprints: the frozen cache module.
# Re-deriving either here would let the two drift and break the mismatch guards.
from rejudge.api_client import UnknownChargeHalt
from rejudge.phase2_call_cache import CallKey, request_fingerprint
# The slot/attempt derivations are deliberately imported from the caching client even though
# they are module-private there: every call site numbers its calls through those exact
# functions, and the journal must key a ledger event the same way the dispatch path did.
from rejudge.phase2_caching_client import UncacheableCall, _derive_attempt, _derive_slot
from rejudge.run_manifest import OutputLockedError, output_lock


class JournalReplayMismatch(RuntimeError):
    """A journaled request no longer matches the request being made.

    Same fail-closed semantics as the cache's CallReplayMismatch: serving a response that
    was generated for a different prompt would silently corrupt the run.
    """


class JournalIdentityMismatch(RuntimeError):
    """The journal file belongs to a different execution identity."""


class JournalDispatchUnresolved(RuntimeError):
    """A prior dispatch on this client failed before journal durability was proven."""


class JournalLedgerMismatch(ValueError):
    """The usage ledger cannot be paired into one reservation and terminal per attempt."""


class JournalWriterLocked(RuntimeError):
    """Another wrapper or process currently owns this journal's dispatch path."""


JOURNAL_REQUEST_SHA256_FIELD = "journal_request_sha256"
_TERMINAL_LEDGER_STATUSES = frozenset({
    "success", "charged_malformed", "unknown_charge", "released_no_charge",
})
_JOURNAL_ROW_FIELDS = frozenset({
    "cell_key", "call_role", "slot", "attempt", "request_sha256", "response",
    "sequence", "prev_event_hash", "event_hash",
})
_DISPATCH_MARKER_SCHEMA = "request_journal_dispatch_marker_v1"


def _fsync_parent_directory(path: Path) -> None:
    """Persist a created or removed directory entry where the platform supports it."""
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def journal_key(request_metadata: Mapping[str, Any] | None) -> CallKey:
    """Derive the journal key for a call, byte-compatible with the cache's keying."""
    if not request_metadata:
        raise UncacheableCall(
            "request_metadata is required: an unjournalable call cannot be replayed on "
            "resume, and would be re-dispatched on every relaunch")
    cell_key = request_metadata.get("cell_key")
    call_role = request_metadata.get("call_role")
    if (not isinstance(cell_key, str) or not cell_key.strip()
            or not isinstance(call_role, str) or not call_role.strip()):
        raise UncacheableCall(
            "request_metadata must carry non-empty string cell_key and call_role; got "
            f"cell_key={cell_key!r}, call_role={call_role!r}")
    return CallKey(cell_key=cell_key, call_role=call_role,
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

    @staticmethod
    def _is_sha256(value: Any) -> bool:
        return (isinstance(value, str) and len(value) == 64
                and all(char in "0123456789abcdef" for char in value))

    @classmethod
    def _validate_row_schema(cls, row: Mapping[str, Any], *, location: str) -> None:
        observed = set(row)
        if observed != _JOURNAL_ROW_FIELDS:
            missing = sorted(_JOURNAL_ROW_FIELDS - observed)
            unexpected = sorted(observed - _JOURNAL_ROW_FIELDS)
            raise ValueError(
                f"request journal {location} has invalid fields: missing={missing}, "
                f"unexpected={unexpected}")
        for field in ("cell_key", "call_role"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(
                    f"request journal {location} field {field} must be a non-empty string")
        for field in ("slot", "attempt", "sequence"):
            if (not isinstance(row[field], int) or isinstance(row[field], bool)
                    or row[field] < 0):
                raise ValueError(
                    f"request journal {location} field {field} must be a non-negative "
                    "integer")
        if not cls._is_sha256(row["request_sha256"]):
            raise ValueError(
                f"request journal {location} request_sha256 must be lowercase SHA-256")
        if not isinstance(row["response"], str):
            raise ValueError(
                f"request journal {location} response must be a string")
        if not isinstance(row["prev_event_hash"], str) or not row["prev_event_hash"]:
            raise ValueError(
                f"request journal {location} prev_event_hash must be a non-empty string")
        if not cls._is_sha256(row["event_hash"]):
            raise ValueError(
                f"request journal {location} event_hash must be lowercase SHA-256")

    def _load(self) -> None:
        for line_number, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                raise ValueError(
                    f"request journal {self.path} has a blank row at line {line_number}")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"request journal {self.path} has an incomplete or invalid JSON row "
                    f"at line {line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(
                    f"request journal {self.path} row {line_number} is not an object")
            self._validate_row_schema(row, location=f"{self.path}:line {line_number}")
            expected_sequence = self._sequence + 1
            if (not isinstance(row["sequence"], int)
                    or isinstance(row["sequence"], bool)
                    or row["sequence"] != expected_sequence):
                raise ValueError(
                    f"request journal sequence broken at line {line_number}: expected "
                    f"{expected_sequence}, found {row['sequence']!r}")
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
            identity = self._identity(row)
            if identity in self._entries:
                raise ValueError(
                    f"request journal repeats call identity at sequence "
                    f"{row['sequence']}: {identity!r}")
            self._last_hash = row["event_hash"]
            self._sequence = row["sequence"]
            self._entries[identity] = row

    def refresh(self) -> None:
        """Reload the complete journal after the caller acquires its writer lock."""
        with self._lock:
            self._entries = {}
            self._last_hash = self._seed
            self._sequence = -1
            if self.path.exists():
                self._load()

    @contextmanager
    def dispatch_guard(self) -> Iterator[None]:
        """Own this journal path across refresh, provider dispatch, and append."""
        try:
            with output_lock(self.path):
                self.refresh()
                yield
        except OutputLockedError as exc:
            raise JournalWriterLocked(
                f"another wrapper or process owns request journal {self.path}; refusing "
                "provider dispatch") from exc

    @property
    def unresolved_marker_path(self) -> Path:
        return self.path.with_name(f"{self.path.name}.unresolved.json")

    def _assert_no_unresolved_marker(self) -> None:
        if self.unresolved_marker_path.exists():
            raise JournalDispatchUnresolved(
                f"request journal {self.path} has a surviving provider-capable dispatch "
                f"marker at {self.unresolved_marker_path}; this execution identity is "
                "interrupted and cannot dispatch again")

    def _begin_dispatch(self, key: CallKey, fingerprint: str) -> bytes:
        """Durably mark a provider-capable call before control enters the inner client."""
        self._assert_no_unresolved_marker()
        payload = {
            "schema_version": _DISPATCH_MARKER_SCHEMA,
            "execution_identity": self.execution_identity,
            "journal_path": str(self.path.resolve()),
            "cell_key": key.cell_key,
            "call_role": key.call_role,
            "slot": key.slot,
            "attempt": key.attempt,
            "request_sha256": fingerprint,
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        encoded = (json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n").encode(
            "utf-8")
        try:
            with self.unresolved_marker_path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_parent_directory(self.unresolved_marker_path)
        except FileExistsError:
            self._assert_no_unresolved_marker()
            raise AssertionError("unreachable: existing dispatch marker was not detected")
        except OSError as exc:
            raise JournalDispatchUnresolved(
                f"could not persist dispatch marker {self.unresolved_marker_path}: "
                f"{exc}") from exc
        return encoded

    def _finish_dispatch(self, expected_marker: bytes) -> None:
        """Remove only the exact marker whose response was durably appended."""
        try:
            observed = self.unresolved_marker_path.read_bytes()
        except OSError as exc:
            raise JournalDispatchUnresolved(
                f"could not verify dispatch marker {self.unresolved_marker_path}: {exc}") from exc
        if observed != expected_marker:
            raise JournalDispatchUnresolved(
                f"dispatch marker changed before completion: {self.unresolved_marker_path}")
        try:
            self.unresolved_marker_path.unlink()
            _fsync_parent_directory(self.unresolved_marker_path)
        except BaseException as exc:
            # If removal succeeded but its directory fsync failed, restore the marker when
            # possible. The conservative state is interrupted, never silently clean.
            if not self.unresolved_marker_path.exists():
                try:
                    with self.unresolved_marker_path.open("xb") as handle:
                        handle.write(expected_marker)
                        handle.flush()
                        os.fsync(handle.fileno())
                    _fsync_parent_directory(self.unresolved_marker_path)
                except OSError:
                    pass
            if isinstance(exc, OSError):
                raise JournalDispatchUnresolved(
                    f"could not clear completed dispatch marker "
                    f"{self.unresolved_marker_path}: {exc}") from exc
            raise

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

    @classmethod
    def _validate_put(cls, key: CallKey, fingerprint: str, response: str) -> None:
        for field in ("cell_key", "call_role"):
            value = getattr(key, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"journal key {field} must be a non-empty string")
        for field in ("slot", "attempt"):
            value = getattr(key, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"journal key {field} must be a non-negative integer")
        if not cls._is_sha256(fingerprint):
            raise ValueError("journal request fingerprint must be lowercase SHA-256")
        if not isinstance(response, str):
            raise ValueError("journal response must be a string")

    def _put_guarded(self, key: CallKey, fingerprint: str, response: str) -> None:
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
            _fsync_parent_directory(self.path)
            self._sequence = row["sequence"]
            self._last_hash = row["event_hash"]
            self._entries[identity] = row
            self.recorded += 1

    def put(self, key: CallKey, fingerprint: str, response: str) -> None:
        """Record one response under the sole path-level writer lock."""
        self._validate_put(key, fingerprint, response)
        with self.dispatch_guard():
            self._assert_no_unresolved_marker()
            self._put_guarded(key, fingerprint, response)

    def has(self, key: CallKey) -> bool:
        with self._lock:
            return self._identity(key) in self._entries

    def request_sha256(self, key: CallKey) -> str | None:
        """Return the recorded request fingerprint without exposing response bytes."""
        with self._lock:
            row = self._entries.get(self._identity(key))
            return None if row is None else str(row["request_sha256"])

    def entry_identities(self) -> frozenset[tuple[str, str, int, int]]:
        """Return the immutable call identities represented by the validated journal."""
        with self._lock:
            return frozenset(self._entries)


class JournalingClient:
    """Replays journaled responses and latches after any unresolved live dispatch.

    Cross-process safety additionally requires :func:`find_ambiguous_dispatches` over the
    complete validated usage ledger before constructing a new dispatch-capable client. The
    main-run process-reset contract is stricter still: an interrupted formal run is void and
    never resumes dispatch under the same execution identity.
    """

    def __init__(self, inner, journal: RequestJournal) -> None:
        self.inner = inner
        self.journal = journal
        self._dispatch_lock = threading.Lock()
        self._unresolved_dispatch: str | None = None
        # Amendment 14 (2026-09-06): unobserved transport failures that the accounting
        # client durably booked as ``unknown_charge`` release the marker instead of latching;
        # each resolution is kept here for the run log and the retry report.
        self.resolved_unknown_charges: list[dict[str, Any]] = []

    def _unknown_charge_resolvable(
            self, key: CallKey, fingerprint: str, exc: BaseException) -> bool:
        """True only for an attempt-matched, durable, content-free unknown charge.

        The accounting client attaches the exact ``unknown_charge`` ledger event it
        recorded before raising. The marker may be released only when that event names
        this dispatch's key and request fingerprint, carries no usage or response, and no
        success for the key has been journaled. Anything else keeps the latch.
        """
        if not isinstance(exc, UnknownChargeHalt):
            return False
        attempt_id = getattr(exc, "attempt_id", None)
        event = getattr(exc, "ledger_event", None)
        if not isinstance(attempt_id, str) or not attempt_id:
            return False
        if not isinstance(event, Mapping):
            return False
        if event.get("status") != "unknown_charge" or event.get("attempt_id") != attempt_id:
            return False
        if event.get("prompt_tokens") is not None or event.get("completion_tokens") is not None:
            return False
        if "response_metadata" in event or "content" in event:
            return False
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            return False
        try:
            if journal_key(metadata) != key:
                return False
        except Exception:  # noqa: BLE001 - malformed metadata keeps the latch
            return False
        if metadata.get(JOURNAL_REQUEST_SHA256_FIELD) != fingerprint:
            return False
        if self.journal.get(key, fingerprint) is not None:
            return False
        return True

    @property
    def dry_run(self) -> bool:
        return getattr(self.inner, "dry_run", False)

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata: Mapping[str, Any] | None = None) -> str:
        key = journal_key(request_metadata)
        assert request_metadata is not None  # journal_key refuses missing metadata
        fingerprint = request_fingerprint(
            messages=messages, model=model, temperature=temperature, seed=seed,
            max_tokens=max_tokens)
        with self._dispatch_lock:
            # The path-level guard makes refresh -> get -> dispatch -> append one critical
            # section across every wrapper and process sharing this journal.
            with self.journal.dispatch_guard():
                self.journal._assert_no_unresolved_marker()
                journaled = self.journal.get(key, fingerprint)
                if journaled is not None:
                    return journaled
                if self._unresolved_dispatch is not None:
                    raise JournalDispatchUnresolved(
                        "a prior provider-capable call attempt on this client did not "
                        f"reach a durable journal entry ({self._unresolved_dispatch}); "
                        "refusing any new dispatch until the usage ledger is reconciled "
                        "and the interrupted formal run is voided or otherwise resolved")

                bound_metadata = dict(request_metadata)
                prior_binding = bound_metadata.get(JOURNAL_REQUEST_SHA256_FIELD)
                if prior_binding is not None and prior_binding != fingerprint:
                    raise JournalReplayMismatch(
                        f"request metadata binds {JOURNAL_REQUEST_SHA256_FIELD} to "
                        f"{prior_binding!r}, but the current request fingerprint is "
                        f"{fingerprint}; refusing to dispatch")
                bound_metadata[JOURNAL_REQUEST_SHA256_FIELD] = fingerprint
                marker = self.journal._begin_dispatch(key, fingerprint)
                try:
                    response = self.inner.complete(
                        messages, model, temperature, seed, max_tokens, kind=kind,
                        request_metadata=bound_metadata)
                    self.journal._validate_put(key, fingerprint, response)
                    self.journal._put_guarded(key, fingerprint, response)
                    self.journal._finish_dispatch(marker)
                except UnknownChargeHalt as exc:
                    if not self._unknown_charge_resolvable(key, fingerprint, exc):
                        self._unresolved_dispatch = f"{type(exc).__name__}: {exc}"
                        raise
                    # The reservation stays booked as uncertain spend in the ledger; no
                    # response exists to journal; the key stays dispatchable under a new
                    # attempt id. The marker is released only after the checks above.
                    self.journal._finish_dispatch(marker)
                    self.resolved_unknown_charges.append({
                        "cell_key": key.cell_key,
                        "call_role": key.call_role,
                        "slot": key.slot,
                        "attempt": key.attempt,
                        "attempt_id": exc.attempt_id,
                        "model": exc.model,
                        "request_sha256": fingerprint,
                    })
                    raise
                except BaseException as exc:
                    self._unresolved_dispatch = f"{type(exc).__name__}: {exc}"
                    raise
                return response


def _find_ambiguous_dispatches_loaded(
        journal: RequestJournal,
        ledger_events: Iterable[Mapping[str, Any]], *,
        allowed_unjournaled_attempt_ids: frozenset[str] = frozenset(),
        ) -> list[dict[str, Any]]:
    """Find every ledger state that makes a journal resume unsafe.

    The full ledger is paired by ``attempt_id`` first. Structural mismatches raise
    :class:`JournalLedgerMismatch`; recoverable-looking partial interpretation is unsafe.
    Journalable attempts then produce findings for unknown charges, charged malformed
    responses, unmatched reservations, successes without journal bytes, journal entries
    without a settled success, duplicate successes, and missing or mismatched
    request-fingerprint bindings. A paired
    ``released_no_charge`` is the only terminal state that permits a later attempt without
    a finding because the accounted client uses it only for a locally rejected transport
    shape before inference.

    Every attempt must be journal-bound by default. A preflight ledger may explicitly
    allowlist complete, unkeyed probe attempts by ID. Partial metadata and unmatched probe
    reservations always fail. Formal measurement should use the strict default and keep
    probes in a separate ledger.
    """
    reservations: dict[str, Mapping[str, Any]] = {}
    terminal_ids: set[str] = set()
    terminals: list[Mapping[str, Any]] = []
    unjournaled_attempts: set[str] = set()

    if any(not isinstance(value, str) or not value
           for value in allowed_unjournaled_attempt_ids):
        raise ValueError("allowed unjournaled attempt IDs must be non-empty strings")

    for index, event in enumerate(ledger_events):
        status = event.get("status")
        if status == "ledger_genesis":
            continue
        attempt_id = event.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise JournalLedgerMismatch(
                f"usage ledger event {index} has no non-empty attempt_id")
        if status == "reserved":
            if attempt_id in reservations or attempt_id in terminal_ids:
                raise JournalLedgerMismatch(
                    f"usage ledger repeats reservation for attempt {attempt_id}")
            metadata = event.get("metadata") or {}
            if not isinstance(metadata, Mapping):
                raise JournalLedgerMismatch(
                    f"usage ledger reservation metadata is not an object: {attempt_id}")
            has_cell = bool(metadata.get("cell_key"))
            has_role = bool(metadata.get("call_role"))
            if has_cell != has_role:
                raise JournalLedgerMismatch(
                    f"usage ledger attempt has partial journal metadata: {attempt_id}")
            if not has_cell:
                if attempt_id not in allowed_unjournaled_attempt_ids:
                    raise JournalLedgerMismatch(
                        f"usage ledger attempt is not journal-bound: {attempt_id}")
                unjournaled_attempts.add(attempt_id)
            else:
                journal_key(metadata)
            reservations[attempt_id] = event
            continue
        if status not in _TERMINAL_LEDGER_STATUSES:
            raise JournalLedgerMismatch(
                f"usage ledger event {index} has unknown status {status!r}")
        reservation = reservations.pop(attempt_id, None)
        if reservation is None:
            raise JournalLedgerMismatch(
                f"usage ledger terminal event has no reservation: {attempt_id}")
        if attempt_id in terminal_ids:
            raise JournalLedgerMismatch(
                f"usage ledger repeats terminal event for attempt {attempt_id}")
        if (reservation.get("metadata") or {}) != (event.get("metadata") or {}):
            raise JournalLedgerMismatch(
                f"usage ledger terminal event changed metadata for attempt {attempt_id}")
        terminal_ids.add(attempt_id)
        terminals.append(event)

    findings: list[dict[str, Any]] = []
    success_counts: dict[tuple, int] = {}
    success_samples: dict[tuple, dict[str, Any]] = {}

    def _sample(event: Mapping[str, Any], key: CallKey) -> dict[str, Any]:
        return {
            "cell_key": key.cell_key,
            "call_role": key.call_role,
            "slot": key.slot,
            "attempt": key.attempt,
            "attempt_id": event.get("attempt_id"),
            "ledger_sequence": event.get("sequence"),
        }

    for attempt_id, reservation in reservations.items():
        if attempt_id in unjournaled_attempts:
            raise JournalLedgerMismatch(
                f"allowlisted unjournaled attempt has no terminal event: {attempt_id}")
        metadata = reservation.get("metadata") or {}
        key = journal_key(metadata)
        findings.append({
            **_sample(reservation, key),
            "problem": "reservation_without_terminal_event",
            "attempt_id": attempt_id,
        })

    for event in terminals:
        metadata = event.get("metadata") or {}
        if str(event["attempt_id"]) in unjournaled_attempts:
            continue
        key = journal_key(metadata)
        identity = (key.cell_key, key.call_role, key.slot, key.attempt)
        status = str(event["status"])
        sample = _sample(event, key)
        if status == "unknown_charge":
            findings.append({**sample, "problem": "unknown_charge"})
            continue
        if status == "charged_malformed":
            findings.append({**sample, "problem": "charged_malformed_response"})
            continue
        if status == "released_no_charge":
            continue

        success_counts[identity] = success_counts.get(identity, 0) + 1
        success_samples.setdefault(identity, sample)
        journal_fingerprint = journal.request_sha256(key)
        if journal_fingerprint is None:
            findings.append({**sample, "problem": "success_without_journal_entry"})
            continue
        ledger_fingerprint = metadata.get(JOURNAL_REQUEST_SHA256_FIELD)
        if not journal._is_sha256(ledger_fingerprint):
            findings.append({
                **sample,
                "problem": "success_without_valid_request_fingerprint_binding",
            })
        elif ledger_fingerprint != journal_fingerprint:
            findings.append({
                **sample,
                "problem": "ledger_journal_request_fingerprint_mismatch",
                "ledger_request_sha256": ledger_fingerprint,
                "journal_request_sha256": journal_fingerprint,
            })

    for identity, count in success_counts.items():
        if count > 1:
            findings.append({
                **success_samples[identity],
                "problem": "duplicate_success_for_key",
                "success_events": count,
            })

    for identity in journal.entry_identities() - success_counts.keys():
        cell_key, call_role, slot, attempt = identity
        findings.append({
            "cell_key": cell_key,
            "call_role": call_role,
            "slot": slot,
            "attempt": attempt,
            "attempt_id": None,
            "ledger_sequence": None,
            "problem": "journal_entry_without_success",
        })

    return sorted(
        findings,
        key=lambda finding: (
            str(finding.get("cell_key")), str(finding.get("call_role")),
            int(finding.get("slot", 0)), int(finding.get("attempt", 0)),
            str(finding.get("problem")), str(finding.get("attempt_id")),
        ),
    )


def find_ambiguous_dispatches(
        journal: RequestJournal,
        ledger_events: Iterable[Mapping[str, Any]], *,
        allowed_unjournaled_attempt_ids: frozenset[str] = frozenset(),
        ) -> list[dict[str, Any]]:
    """Refresh and reconcile a journal under its sole path-level lock.

    ``ledger_events`` must come from a complete, chain-validated ledger that cannot change
    for the duration of this call. The main runner must establish that condition by holding
    its run lease while it validates the ledger and calls this function.
    """
    with journal.dispatch_guard():
        return _find_ambiguous_dispatches_loaded(
            journal, ledger_events,
            allowed_unjournaled_attempt_ids=allowed_unjournaled_attempt_ids)
