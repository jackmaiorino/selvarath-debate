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

Genuinely unobserved interrupted dispatches require explicit recovery: the exact outstanding
reservation is booked in full as an unknown charge before its durable marker is removed.
The signed recovery contract binds preserved journal, ledger, and marker snapshots. A
settled success without journal bytes or a charged malformed response is not eligible.
Known responses, including empty responses, are always replayed unchanged. Recovery does
not claim that the provider never computed a response; it records that no response was
observed by the runner and retains the entire possible charge.

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
from contextlib import contextmanager, nullcontext
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
_PARALLEL_DISPATCH_MARKER_SCHEMA = "request_journal_dispatch_marker_v2"


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
        """Own this journal path across one serial call or a concurrent dispatch group."""
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

    def parallel_marker_path(self, key: CallKey) -> Path:
        identity = json.dumps(asdict(key), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.path.with_name(f"{self.path.name}.dispatch-{digest}.json")

    def dispatch_marker_paths(self) -> tuple[Path, ...]:
        """Inventory both the legacy marker and independently durable parallel markers."""
        paths = list(self.path.parent.glob(f"{self.path.name}.dispatch-*.json"))
        if self.unresolved_marker_path.exists():
            paths.append(self.unresolved_marker_path)
        return tuple(sorted(paths))

    def _assert_no_unresolved_marker(self, *, owned: frozenset[Path] = frozenset()) -> None:
        surviving = set(self.dispatch_marker_paths()) - owned
        if surviving:
            raise JournalDispatchUnresolved(
                f"request journal {self.path} has a surviving provider-capable dispatch "
                f"marker at {sorted(surviving)[0]}; explicit reconciliation is required "
                "before another dispatch")

    def _begin_dispatch(self, key: CallKey, fingerprint: str, *,
                        ledger_boundary: Mapping[str, Any] | None = None) -> bytes:
        """Durably mark a provider-capable call before control enters the inner client."""
        self._assert_no_unresolved_marker()
        return self._write_dispatch_marker(
            key, fingerprint, self.unresolved_marker_path, _DISPATCH_MARKER_SCHEMA,
            ledger_boundary=ledger_boundary)

    def _begin_parallel_dispatch(self, key: CallKey, fingerprint: str, *,
                                 ledger_boundary: Mapping[str, Any] | None = None) -> bytes:
        return self._write_dispatch_marker(
            key, fingerprint, self.parallel_marker_path(key),
            _PARALLEL_DISPATCH_MARKER_SCHEMA, ledger_boundary=ledger_boundary)

    def _write_dispatch_marker(
            self, key: CallKey, fingerprint: str, path: Path, schema: str, *,
            ledger_boundary: Mapping[str, Any] | None = None) -> bytes:
        payload = {
            "schema_version": schema,
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
        if ledger_boundary is not None:
            payload["ledger_boundary"] = dict(ledger_boundary)
        encoded = (json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n").encode(
            "utf-8")
        try:
            with path.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_parent_directory(path)
        except FileExistsError as exc:
            raise JournalDispatchUnresolved(
                f"provider-capable dispatch marker already exists: {path}") from exc
        except OSError as exc:
            raise JournalDispatchUnresolved(
                f"could not persist dispatch marker {path}: "
                f"{exc}") from exc
        return encoded

    def _finish_dispatch(self, expected_marker: bytes) -> None:
        """Remove only the exact marker whose response was durably appended."""
        payload = json.loads(expected_marker)
        path = (self.parallel_marker_path(CallKey(
            payload["cell_key"], payload["call_role"], payload["slot"], payload["attempt"]))
            if payload["schema_version"] == _PARALLEL_DISPATCH_MARKER_SCHEMA
            else self.unresolved_marker_path)
        try:
            observed = path.read_bytes()
        except OSError as exc:
            raise JournalDispatchUnresolved(
                f"could not verify dispatch marker {path}: {exc}") from exc
        if observed != expected_marker:
            raise JournalDispatchUnresolved(
                f"dispatch marker changed before completion: {path}")
        try:
            path.unlink()
            _fsync_parent_directory(path)
        except BaseException as exc:
            # If removal succeeded but its directory fsync failed, restore the marker when
            # possible. The conservative state is interrupted, never silently clean.
            if not path.exists():
                try:
                    with path.open("xb") as handle:
                        handle.write(expected_marker)
                        handle.flush()
                        os.fsync(handle.fileno())
                    _fsync_parent_directory(path)
                except OSError:
                    pass
            if isinstance(exc, OSError):
                raise JournalDispatchUnresolved(
                    f"could not clear completed dispatch marker "
                    f"{path}: {exc}") from exc
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

    def entry_binding(self, key: CallKey) -> dict[str, Any] | None:
        """Bind a durable response without putting its content in a recovery receipt."""
        with self._lock:
            row = self._entries.get(self._identity(key))
            if row is None:
                return None
            return {
                "sequence": row["sequence"], "event_hash": row["event_hash"],
                "request_sha256": row["request_sha256"],
                "response_raw_sha256": hashlib.sha256(
                    row["response"].encode("utf-8")).hexdigest(),
            }


class JournalingClient:
    """Replays journaled responses and latches after any unresolved live dispatch.

    Cross-process safety additionally requires :func:`find_ambiguous_dispatches` over the
    complete validated usage ledger before constructing a new dispatch-capable client. The
    caller must hold its run lease and explicitly reconcile an interrupted dispatch before
    reopening it. Independent requests can overlap when bounded concurrency is enabled;
    one path lease still excludes every other wrapper and process.
    """

    def __init__(self, inner, journal: RequestJournal, *,
                 max_concurrent_requests: int = 1,
                 model_caps: Mapping[str, int] | None = None) -> None:
        if (not isinstance(max_concurrent_requests, int)
                or isinstance(max_concurrent_requests, bool)
                or max_concurrent_requests < 1):
            raise ValueError("max_concurrent_requests must be a positive integer")
        self.inner = inner
        self.journal = journal
        self._dispatch_lock = threading.Lock()
        self.max_concurrent_requests = max_concurrent_requests
        self._parallel_slots = threading.BoundedSemaphore(max_concurrent_requests)
        self.model_caps = dict(model_caps or {})
        if any(not isinstance(model, str) or not model.strip()
               or not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
               for model, limit in self.model_caps.items()):
            raise ValueError("model_caps must map model IDs to positive integers")
        self._model_slots = {
            model: threading.BoundedSemaphore(limit)
            for model, limit in self.model_caps.items()
        }
        self._key_locks: dict[tuple, threading.Lock] = {}
        self._active_calls = 0
        self._path_guard = None
        self._owned_markers: set[Path] = set()
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

    def _dispatch_boundary_kwargs(self) -> dict[str, Any]:
        boundary_reader = getattr(self.inner, "journal_dispatch_boundary", None)
        boundary = boundary_reader() if callable(boundary_reader) else None
        return {"ledger_boundary": boundary} if boundary is not None else {}

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata: Mapping[str, Any] | None = None) -> str:
        if self.max_concurrent_requests > 1:
            return self._complete_parallel(
                messages, model, temperature, seed, max_tokens, kind,
                request_metadata=request_metadata)
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
                marker = self.journal._begin_dispatch(
                    key, fingerprint, **self._dispatch_boundary_kwargs())
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

    @contextmanager
    def _parallel_guard(self, key: CallKey, model: str) -> Iterator[None]:
        """Share one path lease only among this client's active calls.

        The state lock covers lease acquisition and the last release, so a waiting call
        cannot accidentally use a released lease. Provider execution never holds it.
        """
        with self._dispatch_lock:
            key_lock = self._key_locks.setdefault(
                RequestJournal._identity(key), threading.Lock())
        if self.model_caps and model not in self._model_slots:
            raise ValueError(f"no concurrency limit configured for provider model {model!r}")
        model_slot = self._model_slots.get(model, nullcontext())
        # No state mutex is held while waiting for capacity. In particular, several judge
        # workers may all request the same checker model; the actual request model, rather
        # than the worker's judge label, selects this semaphore.
        with key_lock, model_slot, self._parallel_slots:
            with self._dispatch_lock:
                if self._active_calls == 0:
                    guard = self.journal.dispatch_guard()
                    guard.__enter__()
                    self._path_guard = guard
                self._active_calls += 1
            try:
                yield
            finally:
                with self._dispatch_lock:
                    self._active_calls -= 1
                    if self._active_calls == 0:
                        guard, self._path_guard = self._path_guard, None
                        assert guard is not None
                        guard.__exit__(None, None, None)

    def _complete_parallel(
            self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
            request_metadata: Mapping[str, Any] | None = None) -> str:
        key = journal_key(request_metadata)
        assert request_metadata is not None
        fingerprint = request_fingerprint(
            messages=messages, model=model, temperature=temperature, seed=seed,
            max_tokens=max_tokens)
        with self._parallel_guard(key, model):
            with self._dispatch_lock:
                self.journal._assert_no_unresolved_marker(
                    owned=frozenset(self._owned_markers))
                journaled = self.journal.get(key, fingerprint)
                if journaled is not None:
                    return journaled
                if self._unresolved_dispatch is not None:
                    raise JournalDispatchUnresolved(
                        "a prior provider-capable call did not reach a durable journal "
                        f"entry ({self._unresolved_dispatch}); explicit reconciliation "
                        "is required before new dispatch")
                bound_metadata = dict(request_metadata)
                prior_binding = bound_metadata.get(JOURNAL_REQUEST_SHA256_FIELD)
                if prior_binding is not None and prior_binding != fingerprint:
                    raise JournalReplayMismatch(
                        f"request metadata binds {JOURNAL_REQUEST_SHA256_FIELD} to "
                        f"{prior_binding!r}, but request fingerprint is {fingerprint}")
                bound_metadata[JOURNAL_REQUEST_SHA256_FIELD] = fingerprint
                marker_path = self.journal.parallel_marker_path(key)
                marker = self.journal._begin_parallel_dispatch(
                    key, fingerprint, **self._dispatch_boundary_kwargs())
                self._owned_markers.add(marker_path)
            try:
                response = self.inner.complete(
                    messages, model, temperature, seed, max_tokens, kind=kind,
                    request_metadata=bound_metadata)
                self.journal._validate_put(key, fingerprint, response)
                # This lock holds only through the durable append. The provider request
                # above overlaps other keys, while sequence and hash chaining stay atomic.
                self.journal._put_guarded(key, fingerprint, response)
                with self._dispatch_lock:
                    self.journal._finish_dispatch(marker)
                    self._owned_markers.remove(marker_path)
                return response
            except UnknownChargeHalt as exc:
                with self._dispatch_lock:
                    if not self._unknown_charge_resolvable(key, fingerprint, exc):
                        self._unresolved_dispatch = f"{type(exc).__name__}: {exc}"
                        raise
                    try:
                        self.journal._finish_dispatch(marker)
                        self._owned_markers.remove(marker_path)
                    except BaseException as marker_exc:
                        self._unresolved_dispatch = (
                            f"{type(marker_exc).__name__}: {marker_exc}")
                        raise
                    self.resolved_unknown_charges.append({
                        **asdict(key), "attempt_id": exc.attempt_id, "model": exc.model,
                        "request_sha256": fingerprint,
                    })
                raise
            except BaseException as exc:
                # Other already-dispatched keys still persist their responses. The latch
                # prohibits new dispatches without throwing away paid in-flight results.
                with self._dispatch_lock:
                    self._unresolved_dispatch = f"{type(exc).__name__}: {exc}"
                raise


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


def validate_unreserved_dispatch(
        ledger_events: Iterable[Mapping[str, Any]], marker: Mapping[str, Any]) -> None:
    """Prove that no reservation followed this marker's causal ledger frontier.

    This check is read-only and never infers dispatch order from wall-clock timestamps.
    The caller also validates the durable ledger tail state and absence of a response.
    Legacy markers without a frontier are eligible only when this key has no reservations
    anywhere in the complete ledger. A prior unknown attempt is not silently mistaken for
    this dispatch.
    """
    from rejudge import api_client

    events = [dict(event) for event in ledger_events]
    diagnostic_path = Path("recovery-usage-ledger")
    api_client._validate_usage_chain(events, diagnostic_path)
    api_client._summarize_usage_events(events[1:], diagnostic_path, strict_lifecycle=True)
    key = CallKey(marker["cell_key"], marker["call_role"], marker["slot"], marker["attempt"])
    RequestJournal._validate_put(key, marker.get("request_sha256"), "")
    boundary = marker.get("ledger_boundary")
    sequence = -1
    if boundary is not None:
        if (not isinstance(boundary, Mapping)
                or set(boundary) != {"ledger_id", "sequence", "event_hash"}
                or type(boundary.get("sequence")) is not int
                or boundary["sequence"] < 0 or boundary["sequence"] >= len(events)
                or not RequestJournal._is_sha256(boundary.get("event_hash"))):
            raise JournalDispatchUnresolved("unreserved marker has an invalid ledger frontier")
        sequence = boundary["sequence"]
        event = events[sequence]
        if (event.get("sequence") != sequence
                or event.get("ledger_id") != boundary["ledger_id"]
                or event.get("event_hash") != boundary["event_hash"]):
            raise JournalDispatchUnresolved("unreserved marker ledger frontier changed")
    for event in events[sequence + 1:]:
        if event.get("status") == "reserved" and journal_key(event.get("metadata")) == key:
            raise JournalDispatchUnresolved(
                "a reservation exists after the dispatch frontier; unreserved cleanup refused")


def recover_interrupted_dispatches(
        journal: RequestJournal, ledger_path: str | Path,
        interrupted_dispatches: Iterable[Mapping[str, Any]], *,
        recovery_manifest_sha256: str) -> list[dict[str, Any]]:
    """Reconcile only exact dispatch markers named in a validated recovery contract.

    The main runner must hold its run lease, validate the signed recovery contract and
    preserved artifact snapshots, and call this before constructing its accounting client.
    An unobserved dispatch retains its full reservation as uncertain spend. A separately
    declared completed-journaled-response disposition clears its marker without another
    charge or request, after checking the exact success and durable response binding.
    Settled successes missing their journal entry are never eligible for this operation.
    Repeating recovery after its terminal append is safe, including a crash before marker
    removal. Markers not named in the contract remain a hard stop.
    """
    from rejudge import api_client

    outcomes = []
    with journal.dispatch_guard():
        for dispatch in interrupted_dispatches:
            marker = dispatch["marker"]
            reservation = dispatch.get("reservation")
            disposition = dispatch.get("disposition", "conservative_unknown_charge")
            if disposition not in {
                "conservative_unknown_charge", "completed_journaled_response", "unreserved_dispatch",
            }:
                raise JournalDispatchUnresolved("unsupported dispatch recovery disposition")
            if not isinstance(marker, Mapping) or (
                    disposition != "unreserved_dispatch" and not isinstance(reservation, Mapping)):
                raise JournalDispatchUnresolved("recovery marker/reservation must be objects")
            key = CallKey(marker["cell_key"], marker["call_role"],
                          marker["slot"], marker["attempt"])
            schema = marker.get("schema_version")
            if schema not in {_DISPATCH_MARKER_SCHEMA, _PARALLEL_DISPATCH_MARKER_SCHEMA}:
                raise JournalDispatchUnresolved("unsupported interrupted dispatch marker")
            expected_path = (journal.parallel_marker_path(key)
                             if schema == _PARALLEL_DISPATCH_MARKER_SCHEMA
                             else journal.unresolved_marker_path)
            marker_path = Path(str(dispatch["marker_path"]))
            if marker_path.resolve() != expected_path.resolve():
                raise JournalDispatchUnresolved("recovery marker path is not this journal's")
            fingerprint = marker.get("request_sha256")
            if (marker.get("execution_identity") != journal.execution_identity
                    or (marker.get("journal_path") is not None
                        and Path(str(marker["journal_path"])).resolve()
                        != journal.path.resolve())
                    or not journal._is_sha256(fingerprint)
                    or dispatch.get("request_sha256") != fingerprint):
                raise JournalDispatchUnresolved("recovery marker/reservation identity mismatch")
            if disposition != "unreserved_dispatch":
                metadata = reservation.get("metadata")
                if (not isinstance(metadata, Mapping) or journal_key(metadata) != key
                        or metadata.get(JOURNAL_REQUEST_SHA256_FIELD) != fingerprint
                        or dispatch.get("reservation_attempt_id") != reservation.get("attempt_id")
                        or reservation.get("status") != "reserved"):
                    raise JournalDispatchUnresolved("recovery marker/reservation identity mismatch")
            elif any(field in dispatch for field in ("reservation", "reservation_attempt_id", "terminal")):
                raise JournalDispatchUnresolved("unreserved recovery cannot carry a reservation")
            if (disposition == "conservative_unknown_charge"
                    and journal.has(key) and marker_path.exists()):
                raise JournalDispatchUnresolved(
                    "a response is already journaled; unobserved-dispatch recovery refused")
            expected_hash = dispatch.get("marker_raw_sha256")
            if not journal._is_sha256(expected_hash):
                raise JournalDispatchUnresolved("recovery marker hash is invalid")
            marker_bytes = None
            if marker_path.exists():
                marker_bytes = marker_path.read_bytes()
                if (hashlib.sha256(marker_bytes).hexdigest() != expected_hash
                        or json.loads(marker_bytes) != marker):
                    raise JournalDispatchUnresolved("recovery marker differs from snapshot")
            elif disposition == "conservative_unknown_charge":
                # Absence is only valid after this exact recovery already booked its
                # terminal event. Do not append an event for an unexplained lost marker.
                api_client.load_chained_usage_ledger(ledger_path)
                events = api_client._read_usage_events(Path(ledger_path))
                if not any(
                    event.get("attempt_id") == reservation["attempt_id"]
                    and event.get("status") == "unknown_charge"
                    and event.get("recovery_manifest_sha256") == recovery_manifest_sha256
                    for event in events
                ):
                    raise JournalDispatchUnresolved(
                        "interrupted marker is absent without its recovery terminal event")
            if disposition == "unreserved_dispatch":
                api_client.load_chained_usage_ledger(ledger_path)
                events = api_client._read_usage_events(Path(ledger_path))
                boundary = dispatch.get("recovery_ledger_boundary")
                if (not isinstance(boundary, Mapping)
                        or set(boundary) != {"ledger_id", "sequence", "event_hash"}
                        or type(boundary.get("sequence")) is not int
                        or not 0 <= boundary["sequence"] < len(events)
                        or any(events[boundary["sequence"]].get(field) != boundary[field]
                               for field in boundary)):
                    raise JournalDispatchUnresolved("unreserved recovery snapshot frontier changed")
                if marker_bytes is not None:
                    if journal.has(key):
                        raise JournalDispatchUnresolved("unreserved marker already has a journaled response")
                    validate_unreserved_dispatch(events, marker)
                else:
                    # The contract's signed original prefix proves this cleanup was free.
                    # Later resumed calls may now exist and must not invalidate that proof.
                    validate_unreserved_dispatch(events[:boundary["sequence"] + 1], marker)
                uncertain_cost = 0.0
            elif disposition == "completed_journaled_response":
                api_client.load_chained_usage_ledger(ledger_path)
                events = api_client._read_usage_events(Path(ledger_path))
                attempt_events = [event for event in events
                                  if event.get("attempt_id") == reservation["attempt_id"]]
                terminal = dispatch.get("terminal")
                binding = journal.entry_binding(key)
                successes_for_key = [
                    event for event in events if event.get("status") == "success"
                    and journal_key(event.get("metadata")) == key
                ]
                if (not isinstance(terminal, Mapping)
                        or terminal.get("status") != "success"
                        or attempt_events != [dict(reservation), dict(terminal)]
                        or successes_for_key != [dict(terminal)]
                        or terminal.get("metadata") != reservation.get("metadata")
                        or binding is None or binding != dispatch.get("journal_entry")
                        or binding["request_sha256"] != fingerprint):
                    raise JournalDispatchUnresolved(
                        "completed marker lacks its exact successful ledger/journal response")
                uncertain_cost = 0.0
            else:
                terminal = api_client.reconcile_interrupted_reservation(
                    ledger_path, reservation=reservation,
                    recovery_manifest_sha256=recovery_manifest_sha256)
                uncertain_cost = terminal["cost_usd"]
            if marker_bytes is not None:
                journal._finish_dispatch(marker_bytes)
            outcomes.append({
                "attempt_id": reservation["attempt_id"] if reservation is not None else None,
                "request_sha256": fingerprint,
                "uncertain_cost_usd": uncertain_cost,
                "recovery_manifest_sha256": recovery_manifest_sha256,
                "disposition": disposition,
            })
        journal._assert_no_unresolved_marker()
    return outcomes
