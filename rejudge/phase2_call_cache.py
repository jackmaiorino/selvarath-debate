"""Durable per-call response cache, the prerequisite for the canary pause protocol.

The canary pauses a cell whenever a query payload has no committed reviewer decision, then
resumes it once the payload is labelled. Judge queries run at temperature 0.3 (the frozen
``execution_semantics.temperature_by_call_role``), so a naive resume re-runs the cell, gets
different query text, hashes to a different payload, finds it unlabelled, and pauses again --
re-spending every call it repeats. Without this cache the protocol livelocks.

Each provider response is memoised against the call that produced it, so a resumed cell
replays its earlier calls verbatim and only the genuinely new call goes live. Replay is sound
only while the request is unchanged, so every entry binds a fingerprint of the request and a
mismatch raises :class:`CallReplayMismatch` rather than serving a response that was generated
for a different prompt.

The log is append-only, hash-chained and fsynced, matching the durability contract the rest of
the phase-2 execution machinery uses: a torn write or an edited row is a hard stop on load,
never a silent partial replay.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


class CallReplayMismatch(RuntimeError):
    """Raised when a cached call's request no longer matches the request being made."""


class CacheIdentityMismatch(RuntimeError):
    """Raised when a cache file belongs to a different execution identity.

    A cache hit bypasses the provider and the spend ledger, so a stale cache from a prior or
    aborted attempt would silently inject responses generated under a different manifest. The
    request fingerprint cannot catch that: an identical request from an unrelated run matches
    perfectly. The identity is therefore chained into the log, so a foreign cache fails to
    open rather than being partially trusted.
    """


@dataclass(frozen=True, slots=True)
class CallKey:
    """Identifies one provider call within one cell, including its retry attempt."""

    cell_key: str
    call_role: str
    slot: int
    attempt: int


def request_fingerprint(*, messages, model: str, temperature, seed: int,
                        max_tokens: int) -> str:
    """Hash every field that can change the response.

    Deliberately excludes request_metadata, which carries audit linkage rather than anything
    the provider sees.
    """
    material = json.dumps(
        {"messages": messages, "model": model, "temperature": temperature,
         "seed": seed, "max_tokens": max_tokens},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class CallCache:
    """Append-only, hash-chained, fsynced memo of provider responses."""

    def __init__(self, path: str | Path, *, execution_identity: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.execution_identity = execution_identity
        self._entries: dict[tuple, dict] = {}
        # Guards the chain tail and the entry table. Every put reads the tail hash, builds a
        # row committing to it, and appends; two of those interleaved produce a file that no
        # longer verifies, which makes every response already in it unreplayable. Excluding a
        # second PROCESS is the archive lease's job, not this lock's.
        self._lock = threading.Lock()
        # Seeding the chain with the identity is what makes a foreign cache unopenable: its
        # first row commits to a different predecessor and cannot be re-derived.
        self._seed = f"identity:{execution_identity}" if execution_identity else "genesis"
        self._last_hash = self._seed
        self._sequence = -1
        self.replayed = 0
        self.missed = 0
        if self.path.exists():
            self._load()

    # -- persistence ---------------------------------------------------------------------

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
                # Only the genuine first row can be an identity mismatch. A later row that
                # claims sequence 0 is a corrupt or interleaved chain, and reporting that as
                # "belongs to another run" sends the reader somewhere useless.
                if row["sequence"] == 0 and self._sequence == -1:
                    raise CacheIdentityMismatch(
                        f"call cache {self.path} does not belong to this run: its first row "
                        f"chains from {row['prev_event_hash']!r}, but this run expects "
                        f"{self._seed!r}. A cache from another execution identity must never "
                        "be replayed; start a fresh cache under this run's archive directory")
                raise ValueError(
                    f"call cache chain broken at sequence {row['sequence']}: expected "
                    f"previous {self._last_hash}, found {row['prev_event_hash']}")
            if self._row_hash(row) != row["event_hash"]:
                raise ValueError(
                    f"call cache row tampered at sequence {row['sequence']}")
            self._last_hash = row["event_hash"]
            self._sequence = row["sequence"]
            self._entries[self._identity(row)] = row

    @staticmethod
    def _identity(row) -> tuple:
        if isinstance(row, CallKey):
            return (row.cell_key, row.call_role, row.slot, row.attempt)
        return (row["cell_key"], row["call_role"], row["slot"], row["attempt"])

    # -- api -----------------------------------------------------------------------------

    def get(self, key: CallKey, fingerprint: str) -> str | None:
        """Return the memoised response, or None when the call has never been made.

        Raises :class:`CallReplayMismatch` when the call was made before under a different
        request: replaying that response would silently corrupt the run.
        """
        with self._lock:
            row = self._entries.get(self._identity(key))
            if row is None:
                self.missed += 1
                return None
        if row["request_sha256"] != fingerprint:
            raise CallReplayMismatch(
                f"cached call {key.call_role} slot {key.slot} attempt {key.attempt} in cell "
                f"{key.cell_key} was made under request {row['request_sha256']}, but the "
                f"current request is {fingerprint}; refusing to replay a response generated "
                "for a different prompt")
        self.replayed += 1
        return row["response"]

    def put(self, key: CallKey, fingerprint: str, response: str) -> None:
        """Memoise one response. Refuses to overwrite: the log is append-only."""
        identity = self._identity(key)
        with self._lock:
            if identity in self._entries:
                raise ValueError(f"call already cached: {asdict(key)}")
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
