"""Restore operational counters and tune bounded provider concurrency after a crash.

No model output is inspected here. Reviewer recovery only completes an existing local commit
intent; an uncertain external reviewer dispatch is never repeated by this module.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rejudge import phase3_main_reviewer_commit as reviewer_commit


class RecoveryDriverError(ValueError):
    """Saved operational evidence does not support an unambiguous continuation."""


@dataclass(frozen=True, slots=True)
class DriverState:
    first_pass_index: int
    reviewer_dispatches: int
    consecutive_abandoned_rate: int
    existing_unknown_charge_count: int
    recovered_reviewer_waves: tuple[int, ...]
    usage_event_count: int


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise RecoveryDriverError(f"{label} must be an integer >= {minimum}")
    return value


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RecoveryDriverError(f"duplicate JSON field {key}")
        value[key] = item
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, object_pairs_hook=_object)
        except (ValueError, UnicodeError) as exc:
            raise RecoveryDriverError(f"invalid operational JSON at {path}:{number}") from exc
        if not isinstance(row, dict):
            raise RecoveryDriverError(f"operational row is not an object at {path}:{number}")
        rows.append(row)
    return rows


def restore_driver_state(
    paths: Any,
    *,
    held_run_lease: Any = None,
    expected_run_id: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> DriverState:
    """Restore cumulative limits, completing only already-prepared reviewer commits.

    The caller validates the usage ledger chain and run identity before calling. Any returned
    ``recovered_reviewer_waves`` needs a durable ``reviewer_usage_wave_recovered`` log event.
    Its dispatches have already been counted and must not be reserved or dispatched again.
    """
    log = _rows(Path(paths.run_log))
    reservations: dict[int, tuple[int, int]] = {}
    completed = set()
    reviewer_dispatches = max_pass = consecutive_abandoned = 0
    for row in log:
        event = row.get("event")
        for key in ("pass_index", "wave"):
            if key in row:
                max_pass = max(max_pass, _integer(row[key], key, minimum=1))
        if event == "abandoned_cell_rate_pass":
            consecutive_abandoned = _integer(
                row.get("consecutive_abandoned_rate_passes"),
                "consecutive abandoned-rate passes", minimum=1)
        elif event == "formal_main_pass_complete":
            consecutive_abandoned = 0
        if event == "reviewer_usage_reserved":
            wave = _integer(row.get("wave"), "reviewer wave", minimum=1)
            if wave in reservations:
                raise RecoveryDriverError(f"reviewer wave {wave} reserved more than once")
            quantity = _integer(row.get("dispatches_this_wave"), "reviewer dispatches", minimum=1)
            reviewer_dispatches += quantity
            if row.get("cumulative_reviewer_dispatches") != reviewer_dispatches:
                raise RecoveryDriverError(f"reviewer wave {wave} cumulative reservation drifted")
            reservations[wave] = quantity, reviewer_dispatches
        elif event in {"reviewer_usage_wave_completed", "reviewer_usage_wave_recovered"}:
            wave = _integer(row.get("wave"), "completed reviewer wave", minimum=1)
            if wave not in reservations:
                raise RecoveryDriverError(f"reviewer wave {wave} completed without reservation")
            if wave in completed:
                raise RecoveryDriverError(f"reviewer wave {wave} completed more than once")
            quantity, cumulative = reservations[wave]
            if event == "reviewer_usage_wave_completed" and (
                row.get("dispatches_this_wave") != quantity
                or row.get("cumulative_reviewer_dispatches") != cumulative
            ):
                raise RecoveryDriverError(f"reviewer wave {wave} completion count drifted")
            completed.add(wave)

    def load_index():
        indexed = {}
        for row in _rows(Path(paths.reviewer_index)):
            wave = _integer(row.get("wave"), "indexed reviewer wave", minimum=1)
            if wave in indexed or wave not in reservations:
                raise RecoveryDriverError(f"reviewer index wave {wave} is duplicate or unreserved")
            if row.get("payload_count") != reservations[wave][0]:
                raise RecoveryDriverError(f"reviewer index wave {wave} payload count drifted")
            if expected_run_id is not None and row.get("run_id") != expected_run_id:
                raise RecoveryDriverError(f"reviewer index wave {wave} run identity drifted")
            if (expected_manifest_sha256 is not None
                    and row.get("manifest_canonical_sha256") != expected_manifest_sha256):
                raise RecoveryDriverError(f"reviewer index wave {wave} manifest identity drifted")
            indexed[wave] = row
        return indexed

    indexed = load_index()
    if completed - indexed.keys():
        raise RecoveryDriverError("a completed reviewer wave has no committed index row")
    recovered = []
    for wave in sorted(reservations.keys() - completed):
        intent_directories = []
        if Path(paths.review_packets_root).exists():
            for directory in Path(paths.review_packets_root).iterdir():
                intent_path = directory / reviewer_commit.WAVE_COMMIT_INTENT
                if not directory.is_dir() or not intent_path.exists():
                    continue
                try:
                    intent = json.loads(intent_path.read_text(encoding="utf-8"),
                                        object_pairs_hook=_object)
                except (ValueError, UnicodeError) as exc:
                    raise RecoveryDriverError(f"invalid reviewer commit intent {intent_path}") from exc
                if intent.get("wave") == wave:
                    intent_directories.append(directory)
        if len(intent_directories) > 1:
            raise RecoveryDriverError(f"reviewer wave {wave} has multiple commit intents")
        if intent_directories:
            directory = intent_directories[0]
            context = reviewer_commit.load_reviewer_wave_recovery_context(
                transaction_directory=directory, artifact_root=paths.root,
                run_lease_path=paths.lease)
            if expected_run_id is not None and context.run_id != expected_run_id:
                raise RecoveryDriverError(f"reviewer wave {wave} intent run identity drifted")
            if (expected_manifest_sha256 is not None
                    and context.manifest_canonical_sha256 != expected_manifest_sha256):
                raise RecoveryDriverError(f"reviewer wave {wave} intent manifest identity drifted")
            arguments = dict(
                transaction_directory=directory, decision_store_path=paths.decisions,
                reviewer_index_path=paths.reviewer_index, run_id=context.run_id,
                manifest_canonical_sha256=context.manifest_canonical_sha256,
                wave=wave, run_lease_path=paths.lease,
                evidence_bindings=context.evidence_bindings)
            receipt = directory / reviewer_commit.WAVE_COMMIT_RECEIPT
            if wave not in indexed or not receipt.exists():
                if held_run_lease is None:
                    raise RecoveryDriverError(f"reviewer wave {wave} needs its held run lease for local recovery")
                reviewer_commit.recover_reviewer_wave_commit(
                    **arguments, held_run_lease=held_run_lease)
            else:
                reviewer_commit.validate_reviewer_wave_commit(**arguments)
            indexed = load_index()
        if wave not in indexed:
            raise RecoveryDriverError(
                f"reviewer wave {wave} has {reservations[wave][0]} reserved dispatches but "
                "no committed index or recoverable intent; retained reviewer outputs must "
                "be reconciled before continuation, without redispatching that wave")
        recovered.append(wave)

    usage = _rows(Path(paths.usage_ledger))
    unknown_attempts = set()
    for row in usage:
        if row.get("status") == "unknown_charge":
            attempt = row.get("attempt_id")
            if not isinstance(attempt, str) or not attempt:
                raise RecoveryDriverError("unknown charge omitted its attempt identity")
            if attempt in unknown_attempts:
                raise RecoveryDriverError("unknown charge attempt was recorded more than once")
            unknown_attempts.add(attempt)
    return DriverState(
        first_pass_index=max_pass + 1,
        reviewer_dispatches=reviewer_dispatches,
        consecutive_abandoned_rate=consecutive_abandoned,
        existing_unknown_charge_count=len(unknown_attempts),
        recovered_reviewer_waves=tuple(recovered),
        usage_event_count=len(usage),
    )


class ProviderConcurrencyTuner:
    """Promote within signed model limits using transport outcomes only."""

    def __init__(self, initial_limits: Mapping[str, int], max_limits: Mapping[str, int],
                 clean_calls_for_promotion: int = 100) -> None:
        if not initial_limits or set(initial_limits) != set(max_limits):
            raise RecoveryDriverError("initial and maximum model sets must match and be nonempty")
        self.initial_limits = dict(initial_limits)
        self.max_limits = dict(max_limits)
        self.clean_calls_for_promotion = _integer(
            clean_calls_for_promotion, "promotion calls", minimum=1)
        for model in self.initial_limits:
            initial = _integer(self.initial_limits[model], "initial model limit", minimum=1)
            maximum = _integer(self.max_limits[model], "maximum model limit", minimum=1)
            if initial > maximum:
                raise RecoveryDriverError("initial model limit exceeds signed maximum")
        self._limits = dict(self.initial_limits)
        self._clean_calls = dict.fromkeys(self.initial_limits, 0)
        self._seen_terminal_attempts: set[str] = set()

    @property
    def limits(self) -> dict[str, int]:
        return dict(self._limits)

    def observe(self, new_usage_events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        changes = []
        for row in new_usage_events:
            status, model = row.get("status"), row.get("model")
            if status not in {"success", "unknown_charge"} or model not in self._limits:
                continue
            attempt = row.get("attempt_id")
            if not isinstance(attempt, str) or not attempt:
                raise RecoveryDriverError("transport outcome omitted its attempt identity")
            if attempt in self._seen_terminal_attempts:
                continue
            self._seen_terminal_attempts.add(attempt)
            previous = self._limits[model]
            if status == "unknown_charge":
                self._clean_calls[model] = 0
                self._limits[model] = self.initial_limits[model]
                reason = "uncertain_transport_charge"
            else:
                self._clean_calls[model] += 1
                if self._clean_calls[model] < self.clean_calls_for_promotion:
                    continue
                self._limits[model] = self.max_limits[model]
                reason = "clean_transport_window"
            if previous != self._limits[model] or status == "unknown_charge":
                changes.append({"model": model, "previous_limit": previous,
                                "new_limit": self._limits[model], "reason": reason,
                                "clean_successful_calls": self._clean_calls[model]})
        return changes
