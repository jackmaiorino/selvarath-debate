"""Crash-consistent local closeout for one Phase 3 main reviewer wave.

This module has no reviewer, provider, subprocess, or callback seam. It accepts only retained
local evidence that the caller has already validated. A prepared intent contains the exact
append bytes for the decision store and reviewer wave index. Recovery can append only a missing
suffix of those bytes and never repeats external work.

The caller must hold the formal run lease for preparation and recovery. Each reviewer wave uses
its own packet directory, so the fixed intent and receipt filenames are unique per wave.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rejudge import phase2_dual_gate, phase3_v3_live


INTENT_SCHEMA = "phase3_main_reviewer_wave_commit_intent_v1"
RECEIPT_SCHEMA = "phase3_main_reviewer_wave_commit_receipt_v1"
TRANSACTION_ID_SCHEMA = "phase3_main_reviewer_wave_commit_id_v1"
REVIEWER_WAVE_SCHEMA = "phase3_main_reviewer_wave_v4"
WAVE_COMMIT_INTENT = "WAVE_COMMIT_INTENT.json"
WAVE_COMMIT_RECEIPT = "WAVE_COMMIT_RECEIPT.json"
FORMAL_DECISION_STORE_FILENAME = "main_reviewer_decisions.jsonl"
FORMAL_REVIEWER_INDEX_FILENAME = "main_reviewer_index.jsonl"
FORMAL_REVIEW_PACKETS_DIRECTORY = "main_review_packets"

COMMIT_COUNT_FIELDS = frozenset({"parsed", "malformed", "reviewer_error"})
EVIDENCE_BINDING_FIELDS = frozenset({
    "authorization_canonical_sha256",
    "authorization_raw_sha256",
    "authorization_signature_raw_sha256",
    "capacity_plan_raw_sha256",
    "worklist_snapshot_raw_sha256",
    "packet_index_raw_sha256",
    "dispatch_guard_raw_sha256",
    "rulings_raw_sha256",
})
REVIEWER_INDEX_ROW_FIELDS = frozenset({
    "schema_version",
    "run_id",
    "manifest_canonical_sha256",
    "authorization_canonical_sha256",
    "authorization_raw_sha256",
    "authorization_signature_raw_sha256",
    "capacity_plan_raw_sha256",
    "wave",
    "recorded_at_utc",
    "payload_count",
    "packet_directory",
    "worklist_snapshot_raw_sha256",
    "packet_index_raw_sha256",
    "dispatch_guard_raw_sha256",
    "rulings_raw_sha256",
    "reviewer_model",
    "reviewer_reasoning_effort",
    "reviewer_concurrency",
    "reviewer_cli_resolved_path",
    "commit_counts",
    "run_lease_path",
    "wave_commit_transaction_id",
})

_INTENT_FIELDS = frozenset({
    "schema_version",
    "status",
    "wave_commit_transaction_id",
    "run_id",
    "manifest_canonical_sha256",
    "wave",
    "run_lease_path",
    "decision_store_path",
    "reviewer_index_path",
    "evidence_bindings",
    "worklist_canonical_sha256",
    "entries_canonical_sha256",
    "reviewer_index_row_canonical_sha256",
    "commit_counts",
    "decisions",
    "reviewer_index",
})
_DECISION_DELTA_FIELDS = frozenset({
    "prior_raw_sha256",
    "prior_byte_count",
    "prior_tail",
    "target_raw_sha256",
    "target_byte_count",
    "target_tail",
    "append_raw_sha256",
    "append_byte_count",
    "append_base64",
})
_INDEX_DELTA_FIELDS = frozenset({
    "prior_raw_sha256",
    "prior_byte_count",
    "target_raw_sha256",
    "target_byte_count",
    "append_raw_sha256",
    "append_byte_count",
    "append_base64",
})
_TAIL_FIELDS = frozenset({"sequence", "event_hash"})
_RECEIPT_FIELDS = frozenset({
    "schema_version",
    "status",
    "wave_commit_transaction_id",
    "intent_raw_sha256",
    "run_id",
    "manifest_canonical_sha256",
    "wave",
    "run_lease_path",
    "decision_store_path",
    "reviewer_index_path",
    "evidence_bindings",
    "commit_counts",
    "decisions_target",
    "reviewer_index_target",
    "completed_at_utc",
})
_DECISION_TARGET_FIELDS = frozenset({
    "raw_sha256", "byte_count", "tail",
})
_INDEX_TARGET_FIELDS = frozenset({"raw_sha256", "byte_count"})
_NORMAL_ENTRY_FIELDS = frozenset({"payload_sha256", "raw_output", "prompt_sha256"})
_ERROR_ENTRY_FIELDS = frozenset({"payload_sha256", "status", "raw_output"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ReviewerWaveCommitError(RuntimeError, ValueError):
    """A reviewer wave transaction is invalid, divergent, or incomplete."""


class _PublishConflict(ReviewerWaveCommitError):
    """Another publisher atomically created the immutable target first."""


@dataclass(frozen=True, slots=True)
class ReviewerWaveTransactionPaths:
    """Fixed transaction artifact paths within one reviewer packet directory."""

    intent: Path
    receipt: Path


@dataclass(frozen=True, slots=True)
class ReviewerWaveCommitPlan:
    """A durably prepared wave transaction."""

    wave_commit_transaction_id: str
    intent_path: Path
    receipt_path: Path
    parsed: int
    malformed: int
    reviewer_error: int

    @property
    def commit_counts(self) -> dict[str, int]:
        return {
            "parsed": self.parsed,
            "malformed": self.malformed,
            "reviewer_error": self.reviewer_error,
        }


@dataclass(frozen=True, slots=True)
class ReviewerWaveDecisionTail:
    """One immutable decision-chain tail bound by a prepared transaction."""

    sequence: int
    event_hash: str


@dataclass(frozen=True, slots=True)
class ReviewerWaveStoreDeltaBinding:
    """Validated exact byte-prefix and append binding for one local store."""

    prior_raw_sha256: str
    prior_byte_count: int
    target_raw_sha256: str
    target_byte_count: int
    append_raw_sha256: str
    append_byte_count: int
    append_bytes: bytes
    prior_tail: ReviewerWaveDecisionTail | None
    target_tail: ReviewerWaveDecisionTail | None


@dataclass(frozen=True, slots=True)
class ReviewerWaveCommitResult:
    """A completed transaction plus its validated provenance bindings."""

    wave_commit_transaction_id: str
    intent_path: Path
    receipt_path: Path
    parsed: int
    malformed: int
    reviewer_error: int
    decisions: ReviewerWaveStoreDeltaBinding
    reviewer_index: ReviewerWaveStoreDeltaBinding
    reviewer_index_row: dict[str, Any]

    @property
    def commit_counts(self) -> dict[str, int]:
        return {
            "parsed": self.parsed,
            "malformed": self.malformed,
            "reviewer_error": self.reviewer_error,
        }


@dataclass(frozen=True, slots=True)
class ReviewerWaveRecoveryContext:
    """Strict context loaded from one durable intent for offline recovery."""

    transaction_directory: Path
    wave_commit_transaction_id: str
    intent_raw_sha256: str
    decision_store_path: Path
    reviewer_index_path: Path
    run_id: str
    manifest_canonical_sha256: str
    wave: int
    run_lease_path: Path
    evidence_bindings: dict[str, str]


@dataclass(frozen=True, slots=True)
class _LoadedReviewerWaveRecoveryContext:
    """One strictly validated recovery context plus its exact intent bytes."""

    context: ReviewerWaveRecoveryContext
    intent: dict[str, Any]
    intent_raw: bytes


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReviewerWaveCommitError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> Any:
    raise ReviewerWaveCommitError(f"non-finite JSON number is forbidden: {value}")


def _json_bytes(value: Any, *, newline: bool) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ReviewerWaveCommitError("transaction input is not strict JSON") from exc
    return (text + ("\n" if newline else "")).encode("utf-8")


def _index_row_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(
            dict(value), ensure_ascii=True, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ReviewerWaveCommitError("reviewer index row is not strict JSON") from exc
    return (text + "\n").encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value, newline=False)).hexdigest()


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_json_object(raw: bytes, *, label: str, canonical: bool) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeError, json.JSONDecodeError, ReviewerWaveCommitError) as exc:
        raise ReviewerWaveCommitError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReviewerWaveCommitError(f"{label} must be a JSON object")
    if canonical and raw != _json_bytes(value, newline=True):
        raise ReviewerWaveCommitError(f"{label} is not in canonical transaction encoding")
    return value


def _exact_fields(value: Any, fields: frozenset[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ReviewerWaveCommitError(f"{label} fields drifted")
    return value


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ReviewerWaveCommitError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewerWaveCommitError(f"{label} must be non-empty text")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewerWaveCommitError(f"{label} must be a non-negative integer")
    return value


def _positive_int(value: Any, *, label: str) -> int:
    result = _nonnegative_int(value, label=label)
    if result == 0:
        raise ReviewerWaveCommitError(f"{label} must be positive")
    return result


def _utc(value: Any, *, label: str) -> datetime:
    text = _text(value, label=label)
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewerWaveCommitError(f"{label} must be an ISO-8601 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() != timezone.utc.utcoffset(result):
        raise ReviewerWaveCommitError(f"{label} must identify UTC")
    return result


def _absolute_file_path(value: str | Path, *, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ReviewerWaveCommitError(f"{label} must be absolute")
    if path.is_symlink():
        raise ReviewerWaveCommitError(f"{label} must not be a symbolic link")
    return path.resolve()


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _existing_real_path(
    value: str | Path,
    *,
    label: str,
    directory: bool,
) -> Path:
    """Return one canonical existing path with no symlinked path component."""
    path = Path(value)
    if not path.is_absolute():
        raise ReviewerWaveCommitError(f"{label} must be absolute")
    for component in (path, *path.parents):
        try:
            if _is_link_like(component):
                raise ReviewerWaveCommitError(
                    f"{label} must not contain a symbolic link or junction")
        except OSError as exc:
            raise ReviewerWaveCommitError(f"could not inspect {label}") from exc
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ReviewerWaveCommitError(f"{label} must already exist") from exc
    if resolved != path:
        raise ReviewerWaveCommitError(
            f"{label} must be a canonical path without symbolic links")
    if directory:
        if not resolved.is_dir():
            raise ReviewerWaveCommitError(f"{label} must be an existing directory")
    elif not resolved.is_file():
        raise ReviewerWaveCommitError(f"{label} must be an existing regular file")
    return resolved


def reviewer_wave_transaction_paths(
    transaction_directory: str | Path,
) -> ReviewerWaveTransactionPaths:
    """Return the two fixed artifact paths for one wave packet directory."""
    root = Path(transaction_directory)
    if not root.is_absolute():
        raise ReviewerWaveCommitError("transaction directory must be absolute")
    if root.is_symlink():
        raise ReviewerWaveCommitError(
            "transaction directory must not be a symbolic link")
    root = root.resolve()
    return ReviewerWaveTransactionPaths(
        intent=root / WAVE_COMMIT_INTENT,
        receipt=root / WAVE_COMMIT_RECEIPT,
    )


def _require_held_run_lease(
    lease: phase3_v3_live.RunLease,
    *,
    expected_path: Path,
) -> None:
    if type(lease) is not phase3_v3_live.RunLease:
        raise ReviewerWaveCommitError("a held RunLease instance is required")
    observed_path = Path(lease.path)
    if not observed_path.is_absolute() or observed_path.resolve() != expected_path:
        raise ReviewerWaveCommitError("held run lease path differs from the bound lease")
    handle = lease._handle  # noqa: SLF001
    if handle is None or handle.closed:
        raise ReviewerWaveCommitError("the bound run lease is not currently held")


def require_held_run_lease(
    lease: phase3_v3_live.RunLease,
    *,
    expected_path: str | Path,
) -> None:
    """Read-only assertion that the exact formal run lease is currently held."""
    path = _absolute_file_path(expected_path, label="expected run lease path")
    _require_held_run_lease(lease, expected_path=path)


def _normalize_evidence(value: Mapping[str, Any]) -> dict[str, str]:
    _exact_fields(value, EVIDENCE_BINDING_FIELDS, label="evidence bindings")
    result: dict[str, str] = {}
    for name in sorted(EVIDENCE_BINDING_FIELDS):
        if _EVIDENCE_NAME_RE.fullmatch(name) is None:  # pragma: no cover
            raise ReviewerWaveCommitError("internal evidence field name is unsafe")
        result[name] = _sha256(value[name], label=f"evidence binding {name}")
    return result


def derive_wave_commit_transaction_id(
    *,
    run_id: str,
    manifest_canonical_sha256: str,
    wave: int,
    run_lease_path: str | Path,
    evidence_bindings: Mapping[str, Any],
) -> str:
    """Derive the ID independently shared by the wave row, intent, and receipt."""
    material = {
        "schema_version": TRANSACTION_ID_SCHEMA,
        "run_id": _text(run_id, label="run_id"),
        "manifest_canonical_sha256": _sha256(
            manifest_canonical_sha256, label="manifest_canonical_sha256"),
        "wave": _positive_int(wave, label="wave"),
        "run_lease_path": _absolute_file_path(
            run_lease_path, label="run lease path").as_posix(),
        "evidence_bindings": _normalize_evidence(evidence_bindings),
    }
    return _canonical_sha256(material)


def evidence_bindings_from_wave_row(row: Mapping[str, Any]) -> dict[str, str]:
    """Extract the exact evidence hashes that independently identify one wave."""
    return _normalize_evidence({name: row.get(name) for name in EVIDENCE_BINDING_FIELDS})


def _commit_counts(value: Any, *, label: str) -> dict[str, int]:
    mapping = _exact_fields(value, COMMIT_COUNT_FIELDS, label=label)
    return {
        name: _nonnegative_int(mapping[name], label=f"{label}.{name}")
        for name in sorted(COMMIT_COUNT_FIELDS)
    }


def _tail(value: Any, *, label: str) -> dict[str, Any]:
    mapping = _exact_fields(value, _TAIL_FIELDS, label=label)
    sequence = mapping["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < -1:
        raise ReviewerWaveCommitError(f"{label}.sequence must be an integer at least -1")
    event_hash = mapping["event_hash"]
    if sequence == -1:
        if event_hash != "genesis":
            raise ReviewerWaveCommitError(f"{label} genesis tail drifted")
    else:
        _sha256(event_hash, label=f"{label}.event_hash")
    return {"sequence": sequence, "event_hash": event_hash}


def _strict_jsonl_lines(raw: bytes, *, label: str) -> list[bytes]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise ReviewerWaveCommitError(f"{label} has a torn final row")
    lines = raw.splitlines(keepends=True)
    if any(line in {b"\n", b"\r\n"} for line in lines):
        raise ReviewerWaveCommitError(f"{label} contains a blank row")
    if any(not line.endswith(b"\n") for line in lines):  # pragma: no cover
        raise ReviewerWaveCommitError(f"{label} has a torn row")
    return lines


def _decision_rows(
    raw: bytes,
    *,
    label: str,
    prior_tail: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if prior_tail is None:
        sequence = -1
        event_hash = "genesis"
    else:
        tail = _tail(prior_tail, label=f"{label} prior tail")
        sequence = int(tail["sequence"])
        event_hash = str(tail["event_hash"])
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for row_number, line in enumerate(_strict_jsonl_lines(raw, label=label), 1):
        row = _strict_json_object(line, label=f"{label} row {row_number}", canonical=False)
        _exact_fields(
            row, phase2_dual_gate.DECISION_ROW_KEYS,
            label=f"{label} row {row_number}",
        )
        if type(row["sequence"]) is not int or row["sequence"] != sequence + 1:
            raise ReviewerWaveCommitError(
                f"{label} decision sequence is not contiguous at row {row_number}")
        if row["prev_event_hash"] != event_hash:
            raise ReviewerWaveCommitError(
                f"{label} decision chain is broken at row {row_number}")
        expected_hash = phase2_dual_gate.DualGateDecisionStore._row_hash(row)  # noqa: SLF001
        if row["event_hash"] != expected_hash:
            raise ReviewerWaveCommitError(
                f"{label} decision event hash is corrupt at row {row_number}")
        payload = row["payload_sha256"]
        if payload in seen:
            raise ReviewerWaveCommitError(f"{label} repeats decision payload {payload!r}")
        try:
            phase2_dual_gate._validate_decision_fields(  # noqa: SLF001
                payload_sha=payload,
                label=row["label"],
                clause=row["clause"],
                rationale=row["rationale"],
                raw_output=row["raw_output"],
                status=row["status"],
            )
        except ValueError as exc:
            raise ReviewerWaveCommitError(
                f"{label} decision semantics are invalid at row {row_number}") from exc
        seen.add(payload)
        sequence = int(row["sequence"])
        event_hash = str(row["event_hash"])
        rows.append(row)
    return rows, {"sequence": sequence, "event_hash": event_hash}


def _validate_index_row(
    row: Mapping[str, Any],
    *,
    expected_run_id: str,
    expected_manifest_sha256: str,
    expected_run_lease_path: Path,
    expected_wave: int | None = None,
    expected_evidence: Mapping[str, str] | None = None,
    expected_counts: Mapping[str, int] | None = None,
    expected_transaction_id: str | None = None,
) -> tuple[int, datetime]:
    _exact_fields(row, REVIEWER_INDEX_ROW_FIELDS, label="reviewer index row")
    if row["schema_version"] != REVIEWER_WAVE_SCHEMA:
        raise ReviewerWaveCommitError("unsupported reviewer index row schema")
    if row["run_id"] != expected_run_id:
        raise ReviewerWaveCommitError("reviewer index row run_id drifted")
    if row["manifest_canonical_sha256"] != expected_manifest_sha256:
        raise ReviewerWaveCommitError("reviewer index row manifest digest drifted")
    row_lease_path = _absolute_file_path(
        row["run_lease_path"], label="reviewer index row run_lease_path")
    if row_lease_path != expected_run_lease_path:
        raise ReviewerWaveCommitError("reviewer index row run lease path drifted")
    wave = _positive_int(row["wave"], label="reviewer index row wave")
    if expected_wave is not None and wave != expected_wave:
        raise ReviewerWaveCommitError("reviewer index row wave drifted")
    _positive_int(row["payload_count"], label="reviewer index row payload_count")
    _text(row["packet_directory"], label="reviewer index row packet_directory")
    _text(row["reviewer_model"], label="reviewer index row reviewer_model")
    _text(
        row["reviewer_reasoning_effort"],
        label="reviewer index row reviewer_reasoning_effort",
    )
    _positive_int(
        row["reviewer_concurrency"], label="reviewer index row reviewer_concurrency")
    _text(
        row["reviewer_cli_resolved_path"],
        label="reviewer index row reviewer_cli_resolved_path",
    )
    recorded_at = _utc(row["recorded_at_utc"], label="reviewer index row recorded_at_utc")
    counts = _commit_counts(row["commit_counts"], label="reviewer index row commit_counts")
    if sum(counts.values()) != row["payload_count"]:
        raise ReviewerWaveCommitError("reviewer index row commit counts do not cover its payloads")
    evidence = evidence_bindings_from_wave_row(row)
    if expected_evidence is not None and evidence != dict(expected_evidence):
        raise ReviewerWaveCommitError("reviewer index row evidence bindings drifted")
    if expected_counts is not None and counts != dict(expected_counts):
        raise ReviewerWaveCommitError("reviewer index row commit counts drifted")
    transaction_id = _sha256(
        row["wave_commit_transaction_id"],
        label="reviewer index row wave_commit_transaction_id",
    )
    derived = derive_wave_commit_transaction_id(
        run_id=expected_run_id,
        manifest_canonical_sha256=expected_manifest_sha256,
        wave=wave,
        run_lease_path=expected_run_lease_path,
        evidence_bindings=evidence,
    )
    if transaction_id != derived:
        raise ReviewerWaveCommitError("reviewer index row transaction ID is not derived")
    if expected_transaction_id is not None and transaction_id != expected_transaction_id:
        raise ReviewerWaveCommitError("reviewer index row transaction ID drifted")
    return wave, recorded_at


def _index_rows(
    raw: bytes,
    *,
    run_id: str,
    manifest_canonical_sha256: str,
    run_lease_path: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prior_wave = 0
    prior_time: datetime | None = None
    for row_number, line in enumerate(_strict_jsonl_lines(raw, label="reviewer index"), 1):
        row = _strict_json_object(
            line, label=f"reviewer index row {row_number}", canonical=False)
        if line != _index_row_bytes(row):
            raise ReviewerWaveCommitError(
                f"reviewer index row {row_number} is not canonically encoded")
        wave, recorded_at = _validate_index_row(
            row,
            expected_run_id=run_id,
            expected_manifest_sha256=manifest_canonical_sha256,
            expected_run_lease_path=run_lease_path,
        )
        if wave <= prior_wave:
            raise ReviewerWaveCommitError("reviewer index waves are not strictly increasing")
        if prior_time is not None and recorded_at <= prior_time:
            raise ReviewerWaveCommitError(
                "reviewer index recorded times are not strictly increasing")
        prior_wave = wave
        prior_time = recorded_at
        rows.append(row)
    return rows


def _read_store(path: Path, *, label: str) -> bytes:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise ReviewerWaveCommitError(f"{label} must be an existing regular file")
    try:
        first = path.read_bytes()
        second = path.read_bytes()
    except OSError as exc:
        raise ReviewerWaveCommitError(f"could not read {label}") from exc
    if first != second:
        raise ReviewerWaveCommitError(f"{label} changed while it was read")
    return first


def _assert_stores_aligned(
    decision_rows: Sequence[Mapping[str, Any]],
    index_rows: Sequence[Mapping[str, Any]],
) -> None:
    indexed = sum(int(row["payload_count"]) for row in index_rows)
    decided = len(decision_rows)
    if indexed > decided:
        raise ReviewerWaveCommitError(
            "reviewer index is ahead of the durable decision store")
    if decided > indexed:
        raise ReviewerWaveCommitError(
            "decision store has an unindexed prefix with no prepared transaction")


def _normalized_json(value: Any, *, label: str) -> Any:
    raw = _json_bytes(value, newline=False)
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (json.JSONDecodeError, ReviewerWaveCommitError) as exc:  # pragma: no cover
        raise ReviewerWaveCommitError(f"{label} is not strict JSON") from exc


def _prevalidate_entries(
    *,
    prior_rows: Sequence[Mapping[str, Any]],
    prior_tail: Mapping[str, Any],
    worklist: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
) -> tuple[bytes, dict[str, int], dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(worklist, Mapping):
        raise ReviewerWaveCommitError("reviewer worklist must be an object")
    items = worklist.get("items")
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise ReviewerWaveCommitError("reviewer worklist items must be a sequence")
    known: dict[str, str] = {}
    for position, item in enumerate(items, 1):
        if not isinstance(item, Mapping):
            raise ReviewerWaveCommitError(f"reviewer worklist item {position} is not an object")
        payload = _sha256(
            item.get("payload_sha256"),
            label=f"reviewer worklist item {position} payload_sha256",
        )
        prompt = _sha256(
            item.get("subagent_prompt_sha256"),
            label=f"reviewer worklist item {position} subagent_prompt_sha256",
        )
        if payload in known:
            raise ReviewerWaveCommitError(f"reviewer worklist repeats payload {payload}")
        known[payload] = prompt
    if not known:
        raise ReviewerWaveCommitError("reviewer worklist must not be empty")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise ReviewerWaveCommitError("reviewer decision entries must be a sequence")
    normalized_entries = _normalized_json(list(entries), label="reviewer decision entries")
    if not isinstance(normalized_entries, list) or not normalized_entries:
        raise ReviewerWaveCommitError("reviewer decision entries must not be empty")
    prior_payloads = {str(row["payload_sha256"]) for row in prior_rows}
    seen: set[str] = set()
    counts = {"parsed": 0, "malformed": 0, "reviewer_error": 0}
    sequence = int(prior_tail["sequence"])
    event_hash = str(prior_tail["event_hash"])
    encoded_rows: list[bytes] = []
    for position, entry in enumerate(normalized_entries, 1):
        if not isinstance(entry, Mapping):
            raise ReviewerWaveCommitError(f"reviewer decision entry {position} is not an object")
        is_error = entry.get("status") == "reviewer_error"
        expected_fields = _ERROR_ENTRY_FIELDS if is_error else _NORMAL_ENTRY_FIELDS
        _exact_fields(entry, expected_fields, label=f"reviewer decision entry {position}")
        payload = _sha256(
            entry["payload_sha256"],
            label=f"reviewer decision entry {position} payload_sha256",
        )
        if payload not in known:
            raise ReviewerWaveCommitError(f"decision for unknown payload {payload}")
        if payload in prior_payloads:
            raise ReviewerWaveCommitError(f"decision already committed for payload {payload}")
        if payload in seen:
            raise ReviewerWaveCommitError(f"decision batch repeats payload {payload}")
        raw_output = entry["raw_output"]
        if not isinstance(raw_output, str):
            raise ReviewerWaveCommitError(
                f"reviewer decision entry {position} raw_output must be text")
        if is_error:
            label_value = clause = rationale = None
            status = "reviewer_error"
        else:
            prompt = _sha256(
                entry["prompt_sha256"],
                label=f"reviewer decision entry {position} prompt_sha256",
            )
            if prompt != known[payload]:
                raise ReviewerWaveCommitError(
                    f"decision for {payload} was produced from the wrong prompt bytes")
            label_value, clause, rationale = phase2_dual_gate.parse_reviewer_output(raw_output)
            status = "parsed" if label_value is not None else "malformed"
        try:
            phase2_dual_gate._validate_decision_fields(  # noqa: SLF001
                payload_sha=payload,
                label=label_value,
                clause=clause,
                rationale=rationale,
                raw_output=raw_output,
                status=status,
            )
        except ValueError as exc:  # pragma: no cover - parser and shape checks cover this
            raise ReviewerWaveCommitError(
                f"reviewer decision entry {position} is semantically invalid") from exc
        row: dict[str, Any] = {
            "payload_sha256": payload,
            "label": label_value,
            "clause": clause,
            "rationale": rationale,
            "raw_output": raw_output,
            "status": status,
            "sequence": sequence + 1,
            "prev_event_hash": event_hash,
        }
        row["event_hash"] = phase2_dual_gate.DualGateDecisionStore._row_hash(row)  # noqa: SLF001
        encoded_rows.append(
            (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        counts[status] += 1
        sequence = int(row["sequence"])
        event_hash = str(row["event_hash"])
        seen.add(payload)
    if seen != set(known):
        raise ReviewerWaveCommitError(
            "decision batch does not cover the exact reviewer worklist payload set")
    suffix = b"".join(encoded_rows)
    return suffix, counts, {"sequence": sequence, "event_hash": event_hash}, normalized_entries


def _store_delta(
    *,
    prior: bytes,
    suffix: bytes,
    prior_tail: Mapping[str, Any] | None = None,
    target_tail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = prior + suffix
    result: dict[str, Any] = {
        "prior_raw_sha256": _raw_sha256(prior),
        "prior_byte_count": len(prior),
        "target_raw_sha256": _raw_sha256(target),
        "target_byte_count": len(target),
        "append_raw_sha256": _raw_sha256(suffix),
        "append_byte_count": len(suffix),
        "append_base64": base64.b64encode(suffix).decode("ascii"),
    }
    if prior_tail is not None and target_tail is not None:
        result["prior_tail"] = dict(prior_tail)
        result["target_tail"] = dict(target_tail)
    return result


def _publish_temp_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.publish.tmp")


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    parent_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def _cleanup_publish_temp(path: Path, *, label: str) -> None:
    temp = _publish_temp_path(path)
    if temp.is_symlink():
        raise ReviewerWaveCommitError(f"{label} publish temp is not a regular file")
    if not temp.exists():
        return
    if not temp.is_file():
        raise ReviewerWaveCommitError(f"{label} publish temp is not a regular file")
    try:
        temp.unlink()
        _fsync_parent(path)
    except OSError as exc:
        raise ReviewerWaveCommitError(f"could not clean {label} publish temp") from exc


def _publish_exclusive(path: Path, raw: bytes, *, label: str) -> None:
    """Publish from one deterministic, fully fsynced sibling temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = _publish_temp_path(path)
    if path.exists():
        raise _PublishConflict(f"{label} already exists")
    _cleanup_publish_temp(path, label=label)
    temp_owned = False
    try:
        # This deterministic sibling name belongs only to this module. A prior crash may
        # leave it torn, so recreate it completely before the atomic publication point.
        with temp.open("xb") as handle:
            temp_owned = True
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Linking a fully fsynced inode publishes it atomically without the overwrite
            # behavior of os.replace. A crash before temp cleanup leaves two names for the
            # same complete bytes, and the next lease holder safely removes the temp name.
            os.link(temp, path)
        except FileExistsError as exc:
            raise _PublishConflict(
                f"{label} appeared during publication") from exc
        except OSError as exc:
            raise ReviewerWaveCommitError(f"could not publish {label}") from exc
        try:
            _fsync_parent(path)
            published_raw = path.read_bytes()
        except OSError as exc:
            raise ReviewerWaveCommitError(
                f"could not durably verify published {label}") from exc
        if published_raw != raw:
            raise ReviewerWaveCommitError(f"{label} changed during publication")
    finally:
        if temp_owned:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


def _load_intent(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = _read_store(path, label="wave commit intent")
    except ReviewerWaveCommitError as exc:
        raise ReviewerWaveCommitError("wave commit intent is unavailable") from exc
    intent = _strict_json_object(raw, label="wave commit intent", canonical=True)
    _validate_intent(intent)
    return intent, raw


def _decode_delta_suffix(
    delta: Mapping[str, Any],
    *,
    fields: frozenset[str],
    label: str,
) -> bytes:
    _exact_fields(delta, fields, label=label)
    prior_count = _nonnegative_int(delta["prior_byte_count"], label=f"{label}.prior_byte_count")
    target_count = _nonnegative_int(
        delta["target_byte_count"], label=f"{label}.target_byte_count")
    append_count = _positive_int(delta["append_byte_count"], label=f"{label}.append_byte_count")
    _sha256(delta["prior_raw_sha256"], label=f"{label}.prior_raw_sha256")
    _sha256(delta["target_raw_sha256"], label=f"{label}.target_raw_sha256")
    append_sha = _sha256(delta["append_raw_sha256"], label=f"{label}.append_raw_sha256")
    encoded = _text(delta["append_base64"], label=f"{label}.append_base64")
    try:
        suffix = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReviewerWaveCommitError(f"{label}.append_base64 is invalid") from exc
    if base64.b64encode(suffix).decode("ascii") != encoded:
        raise ReviewerWaveCommitError(f"{label}.append_base64 is not canonical")
    if len(suffix) != append_count or _raw_sha256(suffix) != append_sha:
        raise ReviewerWaveCommitError(f"{label} append bytes drifted")
    if target_count != prior_count + append_count:
        raise ReviewerWaveCommitError(f"{label} byte counts are inconsistent")
    return suffix


def _validate_intent(intent: Mapping[str, Any]) -> None:
    _exact_fields(intent, _INTENT_FIELDS, label="wave commit intent")
    if intent["schema_version"] != INTENT_SCHEMA or intent["status"] != "prepared":
        raise ReviewerWaveCommitError("unsupported wave commit intent")
    run_id = _text(intent["run_id"], label="wave commit intent run_id")
    manifest_sha = _sha256(
        intent["manifest_canonical_sha256"],
        label="wave commit intent manifest_canonical_sha256",
    )
    wave = _positive_int(intent["wave"], label="wave commit intent wave")
    run_lease_path = _absolute_file_path(
        intent["run_lease_path"], label="intent run lease path")
    decision_path = _absolute_file_path(
        intent["decision_store_path"], label="intent decision store path")
    index_path = _absolute_file_path(
        intent["reviewer_index_path"], label="intent reviewer index path")
    if decision_path == index_path:
        raise ReviewerWaveCommitError("decision and reviewer index paths must differ")
    evidence = _normalize_evidence(intent["evidence_bindings"])
    transaction_id = _sha256(
        intent["wave_commit_transaction_id"],
        label="wave commit intent transaction ID",
    )
    expected_id = derive_wave_commit_transaction_id(
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        wave=wave,
        run_lease_path=run_lease_path,
        evidence_bindings=evidence,
    )
    if transaction_id != expected_id:
        raise ReviewerWaveCommitError("wave commit intent transaction ID is not derived")
    for field in (
        "worklist_canonical_sha256",
        "entries_canonical_sha256",
        "reviewer_index_row_canonical_sha256",
    ):
        _sha256(intent[field], label=f"wave commit intent {field}")
    counts = _commit_counts(intent["commit_counts"], label="wave commit intent commit_counts")
    decision_delta = _exact_fields(
        intent["decisions"], _DECISION_DELTA_FIELDS, label="decision delta")
    decision_suffix = _decode_delta_suffix(
        decision_delta, fields=_DECISION_DELTA_FIELDS, label="decision delta")
    prior_tail = _tail(decision_delta["prior_tail"], label="decision delta prior_tail")
    target_tail = _tail(decision_delta["target_tail"], label="decision delta target_tail")
    decision_rows, suffix_tail = _decision_rows(
        decision_suffix, label="decision append suffix", prior_tail=prior_tail)
    if suffix_tail != target_tail:
        raise ReviewerWaveCommitError("decision append suffix target tail drifted")
    if len(decision_rows) != sum(counts.values()):
        raise ReviewerWaveCommitError("decision append suffix count drifted")
    index_delta = _exact_fields(
        intent["reviewer_index"], _INDEX_DELTA_FIELDS, label="reviewer index delta")
    index_suffix = _decode_delta_suffix(
        index_delta, fields=_INDEX_DELTA_FIELDS, label="reviewer index delta")
    lines = _strict_jsonl_lines(index_suffix, label="reviewer index append suffix")
    if len(lines) != 1:
        raise ReviewerWaveCommitError("reviewer index append suffix must contain one row")
    index_row = _strict_json_object(
        lines[0], label="reviewer index append row", canonical=False)
    if lines[0] != _index_row_bytes(index_row):
        raise ReviewerWaveCommitError("reviewer index append row is not canonically encoded")
    _validate_index_row(
        index_row,
        expected_run_id=run_id,
        expected_manifest_sha256=manifest_sha,
        expected_run_lease_path=run_lease_path,
        expected_wave=wave,
        expected_evidence=evidence,
        expected_counts=counts,
        expected_transaction_id=transaction_id,
    )
    if _canonical_sha256(index_row) != intent["reviewer_index_row_canonical_sha256"]:
        raise ReviewerWaveCommitError("reviewer index append row digest drifted")


def _intent_matches_call(
    intent: Mapping[str, Any],
    *,
    run_id: str,
    manifest_canonical_sha256: str,
    wave: int,
    run_lease_path: Path,
    decision_store_path: Path,
    reviewer_index_path: Path,
    worklist: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    reviewer_index_row: Mapping[str, Any],
) -> None:
    evidence = evidence_bindings_from_wave_row(reviewer_index_row)
    expected_id = derive_wave_commit_transaction_id(
        run_id=run_id,
        manifest_canonical_sha256=manifest_canonical_sha256,
        wave=wave,
        run_lease_path=run_lease_path,
        evidence_bindings=evidence,
    )
    expected = {
        "run_id": run_id,
        "manifest_canonical_sha256": manifest_canonical_sha256,
        "wave": wave,
        "run_lease_path": run_lease_path.as_posix(),
        "decision_store_path": decision_store_path.as_posix(),
        "reviewer_index_path": reviewer_index_path.as_posix(),
        "evidence_bindings": evidence,
        "worklist_canonical_sha256": _canonical_sha256(worklist),
        "entries_canonical_sha256": _canonical_sha256(list(entries)),
        "reviewer_index_row_canonical_sha256": _canonical_sha256(reviewer_index_row),
        "wave_commit_transaction_id": expected_id,
    }
    for field, value in expected.items():
        if intent.get(field) != value:
            raise ReviewerWaveCommitError(
                f"existing wave commit intent differs from requested {field}")


def prepare_reviewer_wave_commit(
    *,
    transaction_directory: str | Path,
    decision_store_path: str | Path,
    reviewer_index_path: str | Path,
    run_id: str,
    manifest_canonical_sha256: str,
    wave: int,
    run_lease_path: str | Path,
    held_run_lease: phase3_v3_live.RunLease,
    worklist: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    reviewer_index_row: Mapping[str, Any],
) -> ReviewerWaveCommitPlan:
    """Prevalidate a complete wave and exclusively persist its exact append intent."""
    run_id = _text(run_id, label="run_id")
    manifest_sha = _sha256(
        manifest_canonical_sha256, label="manifest_canonical_sha256")
    wave = _positive_int(wave, label="wave")
    lease_path = _absolute_file_path(run_lease_path, label="run lease path")
    _require_held_run_lease(held_run_lease, expected_path=lease_path)
    decision_path = _absolute_file_path(decision_store_path, label="decision store path")
    index_path = _absolute_file_path(reviewer_index_path, label="reviewer index path")
    if decision_path == index_path:
        raise ReviewerWaveCommitError("decision and reviewer index paths must differ")
    paths = reviewer_wave_transaction_paths(transaction_directory)
    if paths.intent.exists():
        _cleanup_publish_temp(paths.intent, label="wave commit intent")
    if paths.receipt.exists():
        _cleanup_publish_temp(paths.receipt, label="wave commit receipt")
    if paths.receipt.exists() and not paths.intent.exists():
        raise ReviewerWaveCommitError("wave commit receipt exists without its intent")
    if paths.intent.exists():
        intent, _ = _load_intent(paths.intent)
        _intent_matches_call(
            intent,
            run_id=run_id,
            manifest_canonical_sha256=manifest_sha,
            wave=wave,
            run_lease_path=lease_path,
            decision_store_path=decision_path,
            reviewer_index_path=index_path,
            worklist=worklist,
            entries=entries,
            reviewer_index_row=reviewer_index_row,
        )
        counts = _commit_counts(intent["commit_counts"], label="wave commit intent counts")
        return ReviewerWaveCommitPlan(
            wave_commit_transaction_id=str(intent["wave_commit_transaction_id"]),
            intent_path=paths.intent,
            receipt_path=paths.receipt,
            **counts,
        )

    decision_raw = _read_store(decision_path, label="decision store")
    index_raw = _read_store(index_path, label="reviewer index")
    prior_decisions, prior_tail = _decision_rows(decision_raw, label="decision store")
    prior_index = _index_rows(
        index_raw,
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        run_lease_path=lease_path,
    )
    _assert_stores_aligned(prior_decisions, prior_index)
    if prior_index and wave <= int(prior_index[-1]["wave"]):
        raise ReviewerWaveCommitError("new reviewer wave is not after the indexed wave tail")

    decision_suffix, counts, target_tail, normalized_entries = _prevalidate_entries(
        prior_rows=prior_decisions,
        prior_tail=prior_tail,
        worklist=worklist,
        entries=entries,
    )
    normalized_worklist = _normalized_json(worklist, label="reviewer worklist")
    normalized_index_row = _normalized_json(
        reviewer_index_row, label="reviewer index row")
    if not isinstance(normalized_index_row, dict):  # pragma: no cover
        raise ReviewerWaveCommitError("reviewer index row must be an object")
    evidence = evidence_bindings_from_wave_row(normalized_index_row)
    transaction_id = derive_wave_commit_transaction_id(
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        wave=wave,
        run_lease_path=lease_path,
        evidence_bindings=evidence,
    )
    _validate_index_row(
        normalized_index_row,
        expected_run_id=run_id,
        expected_manifest_sha256=manifest_sha,
        expected_run_lease_path=lease_path,
        expected_wave=wave,
        expected_evidence=evidence,
        expected_counts=counts,
        expected_transaction_id=transaction_id,
    )
    if normalized_index_row["payload_count"] != len(normalized_entries):
        raise ReviewerWaveCommitError(
            "reviewer index payload count differs from the decision batch")
    if prior_index:
        prior_time = _utc(
            prior_index[-1]["recorded_at_utc"], label="prior reviewer wave recorded_at_utc")
        current_time = _utc(
            normalized_index_row["recorded_at_utc"],
            label="reviewer index row recorded_at_utc",
        )
        if current_time <= prior_time:
            raise ReviewerWaveCommitError(
                "new reviewer wave time is not after the indexed wave tail")
    index_suffix = _index_row_bytes(normalized_index_row)
    intent: dict[str, Any] = {
        "schema_version": INTENT_SCHEMA,
        "status": "prepared",
        "wave_commit_transaction_id": transaction_id,
        "run_id": run_id,
        "manifest_canonical_sha256": manifest_sha,
        "wave": wave,
        "run_lease_path": lease_path.as_posix(),
        "decision_store_path": decision_path.as_posix(),
        "reviewer_index_path": index_path.as_posix(),
        "evidence_bindings": evidence,
        "worklist_canonical_sha256": _canonical_sha256(normalized_worklist),
        "entries_canonical_sha256": _canonical_sha256(normalized_entries),
        "reviewer_index_row_canonical_sha256": _canonical_sha256(normalized_index_row),
        "commit_counts": counts,
        "decisions": _store_delta(
            prior=decision_raw,
            suffix=decision_suffix,
            prior_tail=prior_tail,
            target_tail=target_tail,
        ),
        "reviewer_index": _store_delta(prior=index_raw, suffix=index_suffix),
    }
    _validate_intent(intent)
    intent_raw = _json_bytes(intent, newline=True)
    try:
        _publish_exclusive(paths.intent, intent_raw, label="wave commit intent")
    except _PublishConflict as exc:
        if not paths.intent.exists():
            raise
        existing, existing_raw = _load_intent(paths.intent)
        if existing_raw != intent_raw:
            raise ReviewerWaveCommitError(
                "another wave commit intent won publication with different bytes") from exc
        intent = existing
    return ReviewerWaveCommitPlan(
        wave_commit_transaction_id=transaction_id,
        intent_path=paths.intent,
        receipt_path=paths.receipt,
        **counts,
    )


def _validate_expected_context(
    intent: Mapping[str, Any],
    *,
    decision_store_path: Path,
    reviewer_index_path: Path,
    run_id: str,
    manifest_canonical_sha256: str,
    wave: int,
    run_lease_path: Path,
    evidence_bindings: Mapping[str, Any],
) -> None:
    evidence = _normalize_evidence(evidence_bindings)
    expected_id = derive_wave_commit_transaction_id(
        run_id=run_id,
        manifest_canonical_sha256=manifest_canonical_sha256,
        wave=wave,
        run_lease_path=run_lease_path,
        evidence_bindings=evidence,
    )
    expected = {
        "decision_store_path": decision_store_path.as_posix(),
        "reviewer_index_path": reviewer_index_path.as_posix(),
        "run_id": run_id,
        "manifest_canonical_sha256": manifest_canonical_sha256,
        "wave": wave,
        "run_lease_path": run_lease_path.as_posix(),
        "evidence_bindings": evidence,
        "wave_commit_transaction_id": expected_id,
    }
    for field, value in expected.items():
        if intent.get(field) != value:
            raise ReviewerWaveCommitError(f"wave commit recovery {field} drifted")


def _delta_progress(current: bytes, delta: Mapping[str, Any], *, label: str) -> int:
    fields = _DECISION_DELTA_FIELDS if "prior_tail" in delta else _INDEX_DELTA_FIELDS
    suffix = _decode_delta_suffix(delta, fields=fields, label=label)
    prior_count = int(delta["prior_byte_count"])
    target_count = int(delta["target_byte_count"])
    if len(current) < prior_count:
        raise ReviewerWaveCommitError(f"{label} was truncated below its prepared prefix")
    if _raw_sha256(current[:prior_count]) != delta["prior_raw_sha256"]:
        raise ReviewerWaveCommitError(f"{label} prepared prefix diverged")
    appended = current[prior_count:]
    if len(current) > target_count or not suffix.startswith(appended):
        raise ReviewerWaveCommitError(f"{label} diverged from its exact append suffix")
    if len(current) == target_count and _raw_sha256(current) != delta["target_raw_sha256"]:
        raise ReviewerWaveCommitError(f"{label} target digest drifted")
    return len(appended)


def _append_missing_exact(path: Path, delta: Mapping[str, Any], *, label: str) -> None:
    fields = _DECISION_DELTA_FIELDS if "prior_tail" in delta else _INDEX_DELTA_FIELDS
    suffix = _decode_delta_suffix(delta, fields=fields, label=label)
    try:
        with path.open("r+b") as handle:
            current = handle.read()
            progress = _delta_progress(current, delta, label=label)
            missing = suffix[progress:]
            if missing:
                handle.seek(0, os.SEEK_END)
                handle.write(missing)
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            completed = handle.read()
    except OSError as exc:
        raise ReviewerWaveCommitError(f"could not append {label}") from exc
    if (
        len(completed) != delta["target_byte_count"]
        or _raw_sha256(completed) != delta["target_raw_sha256"]
    ):
        raise ReviewerWaveCommitError(f"{label} did not reach its exact prepared target")


def _receipt_from_intent(intent: Mapping[str, Any], intent_raw: bytes) -> dict[str, Any]:
    decisions = intent["decisions"]
    reviewer_index = intent["reviewer_index"]
    return {
        "schema_version": RECEIPT_SCHEMA,
        "status": "committed",
        "wave_commit_transaction_id": intent["wave_commit_transaction_id"],
        "intent_raw_sha256": _raw_sha256(intent_raw),
        "run_id": intent["run_id"],
        "manifest_canonical_sha256": intent["manifest_canonical_sha256"],
        "wave": intent["wave"],
        "run_lease_path": intent["run_lease_path"],
        "decision_store_path": intent["decision_store_path"],
        "reviewer_index_path": intent["reviewer_index_path"],
        "evidence_bindings": intent["evidence_bindings"],
        "commit_counts": intent["commit_counts"],
        "decisions_target": {
            "raw_sha256": decisions["target_raw_sha256"],
            "byte_count": decisions["target_byte_count"],
            "tail": decisions["target_tail"],
        },
        "reviewer_index_target": {
            "raw_sha256": reviewer_index["target_raw_sha256"],
            "byte_count": reviewer_index["target_byte_count"],
        },
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _validate_receipt(
    receipt: Mapping[str, Any],
    *,
    intent: Mapping[str, Any],
    intent_raw: bytes,
) -> None:
    _exact_fields(receipt, _RECEIPT_FIELDS, label="wave commit receipt")
    if receipt["schema_version"] != RECEIPT_SCHEMA or receipt["status"] != "committed":
        raise ReviewerWaveCommitError("unsupported wave commit receipt")
    _utc(receipt["completed_at_utc"], label="wave commit receipt completed_at_utc")
    expected = _receipt_from_intent(intent, intent_raw)
    expected.pop("completed_at_utc")
    observed = dict(receipt)
    observed.pop("completed_at_utc")
    if observed != expected:
        raise ReviewerWaveCommitError("wave commit receipt differs from its intent")
    _exact_fields(
        receipt["decisions_target"],
        _DECISION_TARGET_FIELDS,
        label="receipt decisions target",
    )
    _tail(receipt["decisions_target"]["tail"], label="receipt decisions target tail")
    _sha256(
        receipt["decisions_target"]["raw_sha256"],
        label="receipt decisions target raw_sha256",
    )
    _nonnegative_int(
        receipt["decisions_target"]["byte_count"],
        label="receipt decisions target byte_count",
    )
    _exact_fields(
        receipt["reviewer_index_target"],
        _INDEX_TARGET_FIELDS,
        label="receipt reviewer index target",
    )
    _sha256(
        receipt["reviewer_index_target"]["raw_sha256"],
        label="receipt reviewer index target raw_sha256",
    )
    _nonnegative_int(
        receipt["reviewer_index_target"]["byte_count"],
        label="receipt reviewer index target byte_count",
    )


def _load_receipt(
    path: Path,
    *,
    intent: Mapping[str, Any],
    intent_raw: bytes,
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = _read_store(path, label="wave commit receipt")
    except ReviewerWaveCommitError as exc:
        raise ReviewerWaveCommitError("wave commit receipt is unavailable") from exc
    receipt = _strict_json_object(raw, label="wave commit receipt", canonical=True)
    _validate_receipt(receipt, intent=intent, intent_raw=intent_raw)
    return receipt, raw


def _require_completed_store_prefixes(
    *,
    decision_path: Path,
    index_path: Path,
    intent: Mapping[str, Any],
) -> None:
    decision_raw = _read_store(decision_path, label="decision store")
    index_raw = _read_store(index_path, label="reviewer index")
    decision_delta = intent["decisions"]
    index_delta = intent["reviewer_index"]

    def require_exact_prefix(
        raw: bytes,
        delta: Mapping[str, Any],
        *,
        fields: frozenset[str],
        label: str,
    ) -> None:
        suffix = _decode_delta_suffix(delta, fields=fields, label=label)
        prior_count = int(delta["prior_byte_count"])
        target_count = int(delta["target_byte_count"])
        if len(raw) < target_count:
            raise ReviewerWaveCommitError(
                f"receipted {label} target prefix diverged")
        if _raw_sha256(raw[:prior_count]) != delta["prior_raw_sha256"]:
            raise ReviewerWaveCommitError(
                f"receipted {label} prior prefix diverged")
        if raw[prior_count:target_count] != suffix:
            raise ReviewerWaveCommitError(
                f"receipted {label} append prefix diverged")
        if _raw_sha256(raw[:target_count]) != delta["target_raw_sha256"]:
            raise ReviewerWaveCommitError(
                f"receipted {label} target prefix diverged")

    require_exact_prefix(
        decision_raw,
        decision_delta,
        fields=_DECISION_DELTA_FIELDS,
        label="decision store",
    )
    require_exact_prefix(
        index_raw,
        index_delta,
        fields=_INDEX_DELTA_FIELDS,
        label="reviewer index",
    )


def _result(
    *,
    intent: Mapping[str, Any],
    paths: ReviewerWaveTransactionPaths,
) -> ReviewerWaveCommitResult:
    counts = _commit_counts(intent["commit_counts"], label="wave commit intent counts")
    decision_delta = intent["decisions"]
    index_delta = intent["reviewer_index"]
    decision_suffix = _decode_delta_suffix(
        decision_delta, fields=_DECISION_DELTA_FIELDS, label="decision delta")
    index_suffix = _decode_delta_suffix(
        index_delta, fields=_INDEX_DELTA_FIELDS, label="reviewer index delta")
    index_lines = _strict_jsonl_lines(
        index_suffix, label="reviewer index append suffix")
    if len(index_lines) != 1:  # Already enforced by _validate_intent.
        raise ReviewerWaveCommitError(
            "reviewer index append suffix must contain one row")
    index_row = _strict_json_object(
        index_lines[0], label="reviewer index append row", canonical=False)
    prior_tail = _tail(
        decision_delta["prior_tail"], label="decision delta prior_tail")
    target_tail = _tail(
        decision_delta["target_tail"], label="decision delta target_tail")

    def binding(
        delta: Mapping[str, Any],
        append_bytes: bytes,
        *,
        bound_prior_tail: Mapping[str, Any] | None = None,
        bound_target_tail: Mapping[str, Any] | None = None,
    ) -> ReviewerWaveStoreDeltaBinding:
        return ReviewerWaveStoreDeltaBinding(
            prior_raw_sha256=str(delta["prior_raw_sha256"]),
            prior_byte_count=int(delta["prior_byte_count"]),
            target_raw_sha256=str(delta["target_raw_sha256"]),
            target_byte_count=int(delta["target_byte_count"]),
            append_raw_sha256=str(delta["append_raw_sha256"]),
            append_byte_count=int(delta["append_byte_count"]),
            append_bytes=append_bytes,
            prior_tail=(
                ReviewerWaveDecisionTail(
                    sequence=int(bound_prior_tail["sequence"]),
                    event_hash=str(bound_prior_tail["event_hash"]),
                )
                if bound_prior_tail is not None else None
            ),
            target_tail=(
                ReviewerWaveDecisionTail(
                    sequence=int(bound_target_tail["sequence"]),
                    event_hash=str(bound_target_tail["event_hash"]),
                )
                if bound_target_tail is not None else None
            ),
        )

    return ReviewerWaveCommitResult(
        wave_commit_transaction_id=str(intent["wave_commit_transaction_id"]),
        intent_path=paths.intent,
        receipt_path=paths.receipt,
        decisions=binding(
            decision_delta,
            decision_suffix,
            bound_prior_tail=prior_tail,
            bound_target_tail=target_tail,
        ),
        reviewer_index=binding(index_delta, index_suffix),
        reviewer_index_row=index_row,
        **counts,
    )


def validate_reviewer_wave_commit(
    *,
    transaction_directory: str | Path,
    decision_store_path: str | Path,
    reviewer_index_path: str | Path,
    run_id: str,
    manifest_canonical_sha256: str,
    wave: int,
    run_lease_path: str | Path,
    evidence_bindings: Mapping[str, Any],
) -> ReviewerWaveCommitResult:
    """Read-only validation that permits exact, valid later-wave store suffixes."""
    run_id = _text(run_id, label="run_id")
    manifest_sha = _sha256(
        manifest_canonical_sha256, label="manifest_canonical_sha256")
    wave = _positive_int(wave, label="wave")
    lease_path = _absolute_file_path(run_lease_path, label="run lease path")
    decision_path = _absolute_file_path(decision_store_path, label="decision store path")
    index_path = _absolute_file_path(reviewer_index_path, label="reviewer index path")
    paths = reviewer_wave_transaction_paths(transaction_directory)
    for target, label in (
        (paths.intent, "wave commit intent"),
        (paths.receipt, "wave commit receipt"),
    ):
        temp = _publish_temp_path(target)
        if temp.is_symlink() or temp.exists():
            raise ReviewerWaveCommitError(f"{label} has an unclosed publish temp")
    intent, intent_raw = _load_intent(paths.intent)
    _validate_expected_context(
        intent,
        decision_store_path=decision_path,
        reviewer_index_path=index_path,
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        wave=wave,
        run_lease_path=lease_path,
        evidence_bindings=evidence_bindings,
    )
    _load_receipt(paths.receipt, intent=intent, intent_raw=intent_raw)
    _require_completed_store_prefixes(
        decision_path=decision_path, index_path=index_path, intent=intent)
    decision_raw = _read_store(decision_path, label="decision store")
    index_raw = _read_store(index_path, label="reviewer index")
    decision_rows, _ = _decision_rows(decision_raw, label="decision store")
    index_rows = _index_rows(
        index_raw,
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        run_lease_path=lease_path,
    )
    _assert_stores_aligned(decision_rows, index_rows)
    decision_target_count = int(intent["decisions"]["target_byte_count"])
    _, target_tail = _decision_rows(
        decision_raw[:decision_target_count], label="receipted decision prefix")
    if target_tail != intent["decisions"]["target_tail"]:
        raise ReviewerWaveCommitError("receipted decision prefix tail drifted")
    index_target_count = int(intent["reviewer_index"]["target_byte_count"])
    target_index_rows = _index_rows(
        index_raw[:index_target_count],
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        run_lease_path=lease_path,
    )
    if not target_index_rows or target_index_rows[-1]["wave"] != wave:
        raise ReviewerWaveCommitError("receipted reviewer index prefix wave drifted")
    return _result(intent=intent, paths=paths)


def _intent_reviewer_index_row(intent: Mapping[str, Any]) -> dict[str, Any]:
    suffix = _decode_delta_suffix(
        intent["reviewer_index"],
        fields=_INDEX_DELTA_FIELDS,
        label="reviewer index delta",
    )
    lines = _strict_jsonl_lines(suffix, label="reviewer index append suffix")
    if len(lines) != 1:  # Already enforced by _validate_intent.
        raise ReviewerWaveCommitError(
            "reviewer index append suffix must contain one row")
    return _strict_json_object(
        lines[0], label="reviewer index append row", canonical=False)


def _load_reviewer_wave_recovery_context(
    *,
    transaction_directory: str | Path,
    artifact_root: str | Path,
    run_lease_path: str | Path,
) -> _LoadedReviewerWaveRecoveryContext:
    artifact_path = _existing_real_path(
        artifact_root, label="formal artifact root", directory=True)
    packets_root = _existing_real_path(
        artifact_path / FORMAL_REVIEW_PACKETS_DIRECTORY,
        label="formal reviewer packet root",
        directory=True,
    )
    transaction_path = _existing_real_path(
        transaction_directory,
        label="reviewer wave transaction directory",
        directory=True,
    )
    if transaction_path.parent != packets_root:
        raise ReviewerWaveCommitError(
            "reviewer wave transaction directory must be a direct child of "
            "the formal reviewer packet root"
        )
    decision_path = _existing_real_path(
        artifact_path / FORMAL_DECISION_STORE_FILENAME,
        label="formal decision store",
        directory=False,
    )
    index_path = _existing_real_path(
        artifact_path / FORMAL_REVIEWER_INDEX_FILENAME,
        label="formal reviewer index",
        directory=False,
    )
    lease_path = _existing_real_path(
        run_lease_path, label="formal run lease", directory=False)
    if lease_path in {decision_path, index_path}:
        raise ReviewerWaveCommitError(
            "formal run lease must differ from the formal reviewer stores")

    paths = reviewer_wave_transaction_paths(transaction_path)
    if not paths.intent.exists():
        if paths.receipt.exists():
            raise ReviewerWaveCommitError(
                "wave commit receipt exists without its intent")
        raise ReviewerWaveCommitError("wave commit intent does not exist")
    intent, intent_raw = _load_intent(paths.intent)
    expected_paths = {
        "decision_store_path": decision_path,
        "reviewer_index_path": index_path,
        "run_lease_path": lease_path,
    }
    for field, expected in expected_paths.items():
        supplied = Path(str(intent[field]))
        if supplied != expected:
            raise ReviewerWaveCommitError(
                f"wave commit intent {field} is not the independently bound formal path")
        observed = _existing_real_path(
            supplied,
            label=f"wave commit intent {field}",
            directory=False,
        )
        if observed != expected:  # pragma: no cover - canonical equality covers this.
            raise ReviewerWaveCommitError(
                f"wave commit intent {field} escaped its formal path")
    index_row = _intent_reviewer_index_row(intent)
    if Path(str(index_row["packet_directory"])) != transaction_path:
        raise ReviewerWaveCommitError(
            "reviewer index packet directory is not the formal transaction directory")

    context = ReviewerWaveRecoveryContext(
        transaction_directory=transaction_path,
        wave_commit_transaction_id=str(intent["wave_commit_transaction_id"]),
        intent_raw_sha256=_raw_sha256(intent_raw),
        decision_store_path=decision_path,
        reviewer_index_path=index_path,
        run_id=str(intent["run_id"]),
        manifest_canonical_sha256=str(intent["manifest_canonical_sha256"]),
        wave=int(intent["wave"]),
        run_lease_path=lease_path,
        evidence_bindings=_normalize_evidence(intent["evidence_bindings"]),
    )
    return _LoadedReviewerWaveRecoveryContext(
        context=context,
        intent=intent,
        intent_raw=intent_raw,
    )


def load_reviewer_wave_recovery_context(
    *,
    transaction_directory: str | Path,
    artifact_root: str | Path,
    run_lease_path: str | Path,
) -> ReviewerWaveRecoveryContext:
    """Load intent context only after independent formal-path validation."""
    return _load_reviewer_wave_recovery_context(
        transaction_directory=transaction_directory,
        artifact_root=artifact_root,
        run_lease_path=run_lease_path,
    ).context


def _recover_reviewer_wave_commit_loaded(
    *,
    paths: ReviewerWaveTransactionPaths,
    intent: Mapping[str, Any],
    intent_raw: bytes,
    decision_path: Path,
    index_path: Path,
    run_id: str,
    manifest_sha: str,
    wave: int,
    lease_path: Path,
    evidence_bindings: Mapping[str, Any],
) -> ReviewerWaveCommitResult:
    """Recover from the exact intent object loaded while the run lease is held."""
    _validate_expected_context(
        intent,
        decision_store_path=decision_path,
        reviewer_index_path=index_path,
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        wave=wave,
        run_lease_path=lease_path,
        evidence_bindings=evidence_bindings,
    )
    if paths.intent.exists():
        _cleanup_publish_temp(paths.intent, label="wave commit intent")
    if paths.receipt.exists():
        _cleanup_publish_temp(paths.receipt, label="wave commit receipt")
    if _read_store(paths.intent, label="wave commit intent") != intent_raw:
        raise ReviewerWaveCommitError(
            "wave commit intent changed after its locked validation")
    if paths.receipt.exists():
        return validate_reviewer_wave_commit(
            transaction_directory=paths.intent.parent,
            decision_store_path=decision_path,
            reviewer_index_path=index_path,
            run_id=run_id,
            manifest_canonical_sha256=manifest_sha,
            wave=wave,
            run_lease_path=lease_path,
            evidence_bindings=evidence_bindings,
        )

    decision_raw = _read_store(decision_path, label="decision store")
    index_raw = _read_store(index_path, label="reviewer index")
    decision_progress = _delta_progress(
        decision_raw, intent["decisions"], label="decision store")
    index_progress = _delta_progress(
        index_raw, intent["reviewer_index"], label="reviewer index")
    decision_append_count = int(intent["decisions"]["append_byte_count"])
    if index_progress and decision_progress != decision_append_count:
        raise ReviewerWaveCommitError(
            "reviewer index is ahead of the prepared decision suffix")
    _append_missing_exact(decision_path, intent["decisions"], label="decision store")
    _append_missing_exact(index_path, intent["reviewer_index"], label="reviewer index")

    completed_decisions = _read_store(decision_path, label="decision store")
    completed_index = _read_store(index_path, label="reviewer index")
    decision_rows, target_tail = _decision_rows(
        completed_decisions, label="completed decision store")
    index_rows = _index_rows(
        completed_index,
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        run_lease_path=lease_path,
    )
    _assert_stores_aligned(decision_rows, index_rows)
    if target_tail != intent["decisions"]["target_tail"]:
        raise ReviewerWaveCommitError("completed decision store tail drifted")
    receipt = _receipt_from_intent(intent, intent_raw)
    _validate_receipt(receipt, intent=intent, intent_raw=intent_raw)
    if _read_store(paths.intent, label="wave commit intent") != intent_raw:
        raise ReviewerWaveCommitError(
            "wave commit intent changed before receipt publication")
    receipt_raw = _json_bytes(receipt, newline=True)
    try:
        _publish_exclusive(paths.receipt, receipt_raw, label="wave commit receipt")
    except _PublishConflict:
        if not paths.receipt.exists():
            raise
        return validate_reviewer_wave_commit(
            transaction_directory=paths.intent.parent,
            decision_store_path=decision_path,
            reviewer_index_path=index_path,
            run_id=run_id,
            manifest_canonical_sha256=manifest_sha,
            wave=wave,
            run_lease_path=lease_path,
            evidence_bindings=evidence_bindings,
        )
    return _result(intent=intent, paths=paths)


def recover_reviewer_wave_commit(
    *,
    transaction_directory: str | Path,
    decision_store_path: str | Path,
    reviewer_index_path: str | Path,
    run_id: str,
    manifest_canonical_sha256: str,
    wave: int,
    run_lease_path: str | Path,
    held_run_lease: phase3_v3_live.RunLease,
    evidence_bindings: Mapping[str, Any],
) -> ReviewerWaveCommitResult:
    """Complete only the exact missing local suffixes named by a durable intent."""
    run_id = _text(run_id, label="run_id")
    manifest_sha = _sha256(
        manifest_canonical_sha256, label="manifest_canonical_sha256")
    wave = _positive_int(wave, label="wave")
    lease_path = _absolute_file_path(run_lease_path, label="run lease path")
    _require_held_run_lease(held_run_lease, expected_path=lease_path)
    decision_path = _absolute_file_path(decision_store_path, label="decision store path")
    index_path = _absolute_file_path(reviewer_index_path, label="reviewer index path")
    paths = reviewer_wave_transaction_paths(transaction_directory)
    if paths.intent.exists():
        _cleanup_publish_temp(paths.intent, label="wave commit intent")
    if paths.receipt.exists():
        _cleanup_publish_temp(paths.receipt, label="wave commit receipt")
    if not paths.intent.exists():
        if paths.receipt.exists():
            raise ReviewerWaveCommitError("wave commit receipt exists without its intent")
        raise ReviewerWaveCommitError("wave commit intent does not exist")
    intent, intent_raw = _load_intent(paths.intent)
    return _recover_reviewer_wave_commit_loaded(
        paths=paths,
        intent=intent,
        intent_raw=intent_raw,
        decision_path=decision_path,
        index_path=index_path,
        run_id=run_id,
        manifest_sha=manifest_sha,
        wave=wave,
        lease_path=lease_path,
        evidence_bindings=evidence_bindings,
    )


def recover_reviewer_wave_commit_from_intent(
    *,
    transaction_directory: str | Path,
    artifact_root: str | Path,
    run_lease_path: str | Path,
) -> ReviewerWaveCommitResult:
    """Recover only under independently supplied formal paths and run lease."""
    loaded = _load_reviewer_wave_recovery_context(
        transaction_directory=transaction_directory,
        artifact_root=artifact_root,
        run_lease_path=run_lease_path,
    )
    context = loaded.context
    try:
        with phase3_v3_live.RunLease(context.run_lease_path) as held_run_lease:
            locked = _load_reviewer_wave_recovery_context(
                transaction_directory=context.transaction_directory,
                artifact_root=artifact_root,
                run_lease_path=run_lease_path,
            )
            locked_context = locked.context
            if locked_context.intent_raw_sha256 != context.intent_raw_sha256:
                raise ReviewerWaveCommitError(
                    "wave commit intent changed while its bound lease was acquired")
            require_held_run_lease(
                held_run_lease, expected_path=locked_context.run_lease_path)
            return _recover_reviewer_wave_commit_loaded(
                paths=reviewer_wave_transaction_paths(
                    locked_context.transaction_directory),
                intent=locked.intent,
                intent_raw=locked.intent_raw,
                decision_path=locked_context.decision_store_path,
                index_path=locked_context.reviewer_index_path,
                run_id=locked_context.run_id,
                manifest_sha=locked_context.manifest_canonical_sha256,
                wave=locked_context.wave,
                lease_path=locked_context.run_lease_path,
                evidence_bindings=locked_context.evidence_bindings,
            )
    except phase3_v3_live.Phase3V3LiveError as exc:
        raise ReviewerWaveCommitError(
            "could not acquire the run lease bound by the wave commit intent") from exc


def commit_reviewer_wave(
    *,
    transaction_directory: str | Path,
    decision_store_path: str | Path,
    reviewer_index_path: str | Path,
    run_id: str,
    manifest_canonical_sha256: str,
    wave: int,
    run_lease_path: str | Path,
    held_run_lease: phase3_v3_live.RunLease,
    worklist: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    reviewer_index_row: Mapping[str, Any],
) -> ReviewerWaveCommitResult:
    """Prepare and locally commit one wave without any external-dispatch capability."""
    prepare_reviewer_wave_commit(
        transaction_directory=transaction_directory,
        decision_store_path=decision_store_path,
        reviewer_index_path=reviewer_index_path,
        run_id=run_id,
        manifest_canonical_sha256=manifest_canonical_sha256,
        wave=wave,
        run_lease_path=run_lease_path,
        held_run_lease=held_run_lease,
        worklist=worklist,
        entries=entries,
        reviewer_index_row=reviewer_index_row,
    )
    evidence = evidence_bindings_from_wave_row(reviewer_index_row)
    return recover_reviewer_wave_commit(
        transaction_directory=transaction_directory,
        decision_store_path=decision_store_path,
        reviewer_index_path=reviewer_index_path,
        run_id=run_id,
        manifest_canonical_sha256=manifest_canonical_sha256,
        wave=wave,
        run_lease_path=run_lease_path,
        held_run_lease=held_run_lease,
        evidence_bindings=evidence,
    )


__all__ = [
    "EVIDENCE_BINDING_FIELDS",
    "FORMAL_DECISION_STORE_FILENAME",
    "FORMAL_REVIEWER_INDEX_FILENAME",
    "FORMAL_REVIEW_PACKETS_DIRECTORY",
    "INTENT_SCHEMA",
    "RECEIPT_SCHEMA",
    "REVIEWER_WAVE_SCHEMA",
    "REVIEWER_INDEX_ROW_FIELDS",
    "ReviewerWaveCommitError",
    "ReviewerWaveCommitPlan",
    "ReviewerWaveCommitResult",
    "ReviewerWaveDecisionTail",
    "ReviewerWaveRecoveryContext",
    "ReviewerWaveStoreDeltaBinding",
    "ReviewerWaveTransactionPaths",
    "WAVE_COMMIT_INTENT",
    "WAVE_COMMIT_RECEIPT",
    "commit_reviewer_wave",
    "derive_wave_commit_transaction_id",
    "evidence_bindings_from_wave_row",
    "load_reviewer_wave_recovery_context",
    "prepare_reviewer_wave_commit",
    "recover_reviewer_wave_commit",
    "recover_reviewer_wave_commit_from_intent",
    "require_held_run_lease",
    "reviewer_wave_transaction_paths",
    "validate_reviewer_wave_commit",
]
