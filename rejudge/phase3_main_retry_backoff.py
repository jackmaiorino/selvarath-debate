"""Reconstruct temporary exact-request cooldowns from durable transport failures."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import threading
from typing import Any, Callable, Iterable, Mapping

from rejudge.phase2_call_cache import CallKey
from rejudge.request_journal import JOURNAL_REQUEST_SHA256_FIELD, RequestJournal, journal_key

POLICY = "unknown_charge_exact_request_3_15m_max60m_v1"


@dataclass(frozen=True)
class RetryWindow:
    key: CallKey
    request_sha256: str
    failures: int
    ready_at: datetime

    @property
    def backoff_seconds(self) -> int:
        return 900 * (2 ** min(self.failures - 3, 2))

    @property
    def last_unknown_at(self) -> datetime:
        return self.ready_at - timedelta(seconds=self.backoff_seconds)


class ProviderRetryDeferred(RuntimeError):
    """No provider dispatch occurred; the exact uncached request remains required."""

    def __init__(self, window: RetryWindow):
        self.window = window
        super().__init__(f"provider request cooling until {window.ready_at.isoformat()}")


class ProviderRetryBackoff:
    def __init__(self, journal: RequestJournal, *, clock: Callable[[], datetime] | None = None):
        self.journal = journal
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._attempts: dict[str, tuple[CallKey, str, datetime]] = {}
        self._failures: dict[tuple[CallKey, str], list[datetime]] = {}
        # A cell is excluded only after its execution reaches this exact fingerprint.
        # An old ledger failure alone cannot predict the cell's next request.
        self._deferred: set[tuple[CallKey, str]] = set()

    def now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("provider retry clock must include a timezone")
        return now.astimezone(timezone.utc)

    def observe(self, events: Iterable[Mapping[str, Any]]) -> None:
        """Consume already validated ledger events, counting each physical attempt once."""
        with self._lock:
            for event in events:
                if event.get("status") != "unknown_charge":
                    continue
                metadata = event.get("metadata")
                key = journal_key(metadata)
                fingerprint = metadata.get(JOURNAL_REQUEST_SHA256_FIELD)
                attempt_id = event.get("attempt_id")
                if not RequestJournal._is_sha256(fingerprint) or not isinstance(attempt_id, str) or not attempt_id:
                    raise ValueError("provider retry failure lacks exact request/attempt identity")
                timestamp = datetime.fromisoformat(str(event.get("ts")).replace("Z", "+00:00"))
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError("provider retry failure timestamp must include a timezone")
                value = (key, fingerprint, timestamp.astimezone(timezone.utc))
                prior = self._attempts.get(attempt_id)
                if prior is not None:
                    if prior != value:
                        raise ValueError("provider retry attempt identity changed")
                    continue
                self._attempts[attempt_id] = value
                self._failures.setdefault((key, fingerprint), []).append(value[2])

    def window(self, key: CallKey, fingerprint: str) -> RetryWindow | None:
        with self._lock:
            # Metadata-only inspection must not increment the journal replay counter.
            saved = self.journal.request_sha256(key)
            if saved is not None:
                # The normal journal lookup retains its hard mismatch check. Either way,
                # a saved response must be handled by that lookup rather than a cooldown.
                return None
            failures = self._failures.get((key, fingerprint), ())
            count = len(failures)
            if count < 3:
                return None
            delay = 900 * (2 ** min(count - 3, 2))
            ready = max(failures) + timedelta(seconds=delay)
            return RetryWindow(key, fingerprint, count, ready) if self.now() < ready else None

    def before_dispatch(self, key: CallKey, fingerprint: str) -> None:
        with self._lock:
            window = self.window(key, fingerprint)
            if window is not None:
                self._deferred.add((key, fingerprint))
                raise ProviderRetryDeferred(window)

    def cooling_windows(self) -> dict[str, RetryWindow]:
        with self._lock:
            cooling: dict[str, RetryWindow] = {}
            for identity in tuple(self._deferred):
                window = self.window(*identity)
                if window is None:
                    self._deferred.remove(identity)
                else:
                    cell = window.key.cell_key
                    if cell not in cooling or window.ready_at > cooling[cell].ready_at:
                        cooling[cell] = window
            return cooling

    def cooling_cells(self) -> dict[str, datetime]:
        return {cell: window.ready_at for cell, window in self.cooling_windows().items()}


def run_eligible_pass(*, cells, backoff: ProviderRetryBackoff, completed_keys,
                      run, sleep, log):
    """Keep a driver pass open while its remaining ready cells are temporarily cooling.

    Only the exact uncached-request guard can establish a deferral. In particular, a
    cold ledger reconstruction cannot hide a changed request fingerprint or saved replay.
    The callback finishes and releases its worker pool before any bounded sleep here.
    """
    waited = False
    retry_deferrals = deferred = 0
    while True:
        cooling = backoff.cooling_windows()
        complete = completed_keys()
        ready = [cell for cell in cells if cell.cell_key not in complete
                 and all(key in complete for key in cell.dependency_keys)]
        if ready and all(cell.cell_key in cooling for cell in ready):
            window = min((cooling[cell.cell_key] for cell in ready), key=lambda w: w.ready_at)
            now = backoff.now()
            duration = min(60.0, (window.ready_at - now).total_seconds())
            if duration <= 0:
                continue
            log({"event": "provider_retry_cooldown", "recorded_at_utc": now.isoformat(),
                 "next_eligible_at_utc": window.ready_at.isoformat(),
                 "last_unknown_at_utc": window.last_unknown_at.isoformat(),
                 "backoff_seconds": window.backoff_seconds, "sleep_seconds": duration,
                 "deferred_cell_count": len(ready), "retry_policy": POLICY})
            waited = True
            sleep(duration)
            continue
        if waited:
            log({"event": "provider_retry_cooldown_complete",
                 "recorded_at_utc": backoff.now().isoformat(), "retry_policy": POLICY})
            waited = False
        # The canary validates that every supplied dependency is in its plan. Temporarily
        # remove dependents too, then restore the full frozen graph when the parent is due.
        omitted = set(cooling)
        while True:
            blocked = {cell.cell_key for cell in cells
                       if any(key in omitted for key in cell.dependency_keys)} - omitted
            if not blocked:
                break
            omitted.update(blocked)
        outcome = run([cell for cell in cells if cell.cell_key not in omitted])
        if (getattr(outcome, "retry_deferred", 0)
                and not outcome.completed and not outcome.paused and not outcome.abandoned
                and outcome.halted_reason is None):
            retry_deferrals += outcome.retry_deferred
            deferred += outcome.deferred
            continue
        outcome.retry_deferred += retry_deferrals
        outcome.deferred += deferred
        return outcome
