"""Strict local provenance validation for Phase 3 main reviewer waves.

The verifier is intentionally offline.  It proves that the retained local files form one
consistent graph from frozen worklists through packet bytes and reviewer invocation evidence
to the hash-chained decision store.  It does not attest the remote service, billing account,
or hidden provider state. It also does not prove the CLI-reported version for each invocation;
the retained invocation receipt identifies the CLI wrapper path and bytes.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from rejudge.phase2_canary_live import (
    SUBAGENT_PAYLOAD_SEPARATOR,
    compose_subagent_prompt,
)
from rejudge.phase2_dual_gate import (
    DECISION_ROW_KEYS,
    DualGateDecisionStore,
    parse_reviewer_output,
    payload_hash,
)
from rejudge import phase3_main_reviewer_commit, phase3_main_reviewer_recovery
from scripts import codex_reviewer_batch, phase3_main_review_capacity_preflight
from scripts.codex_reviewer_batch import validate_invocation_evidence


REVIEWER_WAVE_SCHEMA = phase3_main_reviewer_commit.REVIEWER_WAVE_SCHEMA
REVIEWER_PROVENANCE_STATUS = "reviewer_provenance_verified"
REVIEWER_FAILURE_POLICY_SCHEMA = "phase3_main_reviewer_failure_policy_v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WAVE_FIELDS = phase3_main_reviewer_commit.REVIEWER_INDEX_ROW_FIELDS
_COMMIT_COUNT_FIELDS = frozenset({"parsed", "malformed", "reviewer_error"})
_WORKLIST_FIELDS = frozenset({"frozen_prompt_sha256", "separator", "items"})
_WORKLIST_ITEM_FIELDS = frozenset({
    "payload_sha256",
    "query",
    "candidate_a",
    "candidate_b",
    "subagent_prompt",
    "subagent_prompt_sha256",
})
_PACKET_INDEX_FIELDS = frozenset({"count", "items"})
_DISPATCH_GUARD_FIELDS = frozenset({
    "schema_version",
    "run_id",
    "manifest_canonical_sha256",
    "authorization_canonical_sha256",
    "authorization_approved_at_utc",
    "authorization_valid_until_utc",
    "capacity_completed_at_utc",
    "capacity_expires_at_utc",
    "reviewer_model",
    "reviewer_reasoning_effort",
    "reviewer_concurrency",
    "reviewer_cli_version",
    "capacity_host_identity",
    "packet_directory",
    "output_path",
    "packet_bindings",
    "artifact_bindings",
})
_DISPATCH_GUARD_ARTIFACT_NAMES = frozenset({
    "authorization",
    "authorization_signature",
    "capacity_plan",
    "capacity_result",
    "capacity_dispatch_history",
    "reviewer_cli_wrapper",
    "packet_index",
    "worklist_snapshot",
    "batch_runner",
})
_DISPATCH_GUARD_BINDING_FIELDS = frozenset({"path", "raw_sha256", "byte_count"})
_DISPATCH_GUARD_PACKET_BINDING_FIELDS = frozenset({
    "file", "payload_sha256", "prompt_sha256", "byte_count",
})
_DISPATCH_RESERVATION_FIELDS = frozenset({
    "schema_version",
    "guard_raw_sha256",
    "payload_sha256",
    "prompt_sha256",
    "packet_file",
    "packet_directory",
    "output_path",
    "reviewer_model",
    "reviewer_reasoning_effort",
    "reviewer_concurrency",
    "reviewer_cli_version",
    "capacity_host_identity",
    "reserved_at_utc",
})
_DISPATCH_RESERVATION_BINDING_FIELDS = frozenset({
    "path", "raw_sha256", "byte_count",
})
_PACKET_INDEX_ITEM_FIELDS = frozenset({
    "n", "file", "payload_sha256", "prompt_sha256",
})
_CLEAN_RULING_FIELDS = frozenset({
    "payload_sha256", "raw_output", "prompt_sha256", "tool_uses", "evidence",
})
_ERROR_RULING_FIELDS = frozenset({
    "payload_sha256", "status", "raw_output", "evidence",
})
_FAILURE_POLICY_FIELDS = frozenset({
    "schema_version",
    "stage",
    "status",
    "reviewer_prompt_sha256",
    "overrides_reviewer_prompt_field",
    "parse_failure",
    "tool_use",
    "timeout_or_unavailability",
    "execution_authorized",
    "provider_calls_authorized",
})


class MainReviewerProvenanceError(ValueError):
    """Raised when retained reviewer provenance is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class _FrozenReviewerInputs:
    prompt: str
    prompt_sha256: str
    cli_wrapper_raw_sha256: str
    cli_wrapper_byte_count: int
    reviewer_prompt_raw_sha256: str
    reviewer_failure_policy_raw_sha256: str
    capacity_plan_raw_sha256: str
    capacity_result_raw_sha256: str
    capacity_dispatch_history_raw_sha256: str
    capacity_host_identity: str
    reviewer_cli_version: str
    capacity_completed_at: datetime
    capacity_expires_at: datetime
    reviewer_concurrency: int
    wave_size: int
    maximum_unique_payloads: int


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise MainReviewerProvenanceError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_non_finite(value: str) -> None:
    raise MainReviewerProvenanceError(f"non-finite JSON number is forbidden: {value}")


def _strict_json(raw: bytes, *, subject: str) -> dict[str, Any]:
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite,
        )
    except (UnicodeError, json.JSONDecodeError, MainReviewerProvenanceError) as exc:
        raise MainReviewerProvenanceError(
            f"{subject} is not strict unique-key UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise MainReviewerProvenanceError(f"{subject} must be a JSON object")
    return cast(dict[str, Any], value)


def _strict_jsonl_material(
    raw: bytes,
    *,
    subject: str,
) -> tuple[list[dict[str, Any]], list[bytes]]:
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MainReviewerProvenanceError(f"{subject} is not UTF-8") from exc
    if not raw:
        return [], []
    if not raw.endswith(b"\n"):
        raise MainReviewerProvenanceError(f"{subject} lacks its final newline")
    raw_lines = [part + b"\n" for part in raw[:-1].split(b"\n")]
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw_lines, 1):
        if line in {b"\n", b"\r\n"}:
            raise MainReviewerProvenanceError(
                f"{subject} contains a blank row at line {line_number}")
        rows.append(_strict_json(line, subject=f"{subject} line {line_number}"))
    return rows, raw_lines


def _strict_jsonl(raw: bytes, *, subject: str) -> list[dict[str, Any]]:
    rows, _raw_lines = _strict_jsonl_material(raw, subject=subject)
    return rows


def _read_stable(path: Path, *, subject: str) -> bytes:
    if not path.is_file():
        raise MainReviewerProvenanceError(f"{subject} is not a file: {path}")
    first = path.read_bytes()
    second = path.read_bytes()
    if first != second:
        raise MainReviewerProvenanceError(f"{subject} changed while it was being validated")
    return first


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise MainReviewerProvenanceError(f"{field} must be a lowercase SHA-256")
    return value


def _require_non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MainReviewerProvenanceError(f"{field} must be a non-negative integer")
    return value


def _require_positive_int(value: object, *, field: str) -> int:
    parsed = _require_non_negative_int(value, field=field)
    if parsed == 0:
        raise MainReviewerProvenanceError(f"{field} must be positive")
    return parsed


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MainReviewerProvenanceError(f"{field} must be nonempty text")
    return value


def _require_string(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise MainReviewerProvenanceError(f"{field} must be text")
    return value


def _require_utc_datetime(value: object, *, field: str) -> datetime:
    text = _require_text(value, field=field)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise MainReviewerProvenanceError(f"{field} must be ISO-8601 UTC text") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MainReviewerProvenanceError(f"{field} must use UTC")
    return parsed.astimezone(timezone.utc)


def _require_utc(value: object, *, field: str) -> str:
    return _require_utc_datetime(value, field=field).isoformat()


def _require_fields(value: Mapping[str, Any], expected: frozenset[str], *, subject: str) -> None:
    if set(value) != expected:
        raise MainReviewerProvenanceError(f"{subject} fields drifted")


def _expected_worklist_raw(worklist: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(worklist), ensure_ascii=False, allow_nan=False, indent=1,
        )
        + "\n"
    ).encode("utf-8")


def validate_main_reviewer_failure_policy(
    policy: Mapping[str, Any], *, reviewer_prompt_sha256: str,
) -> None:
    """Validate the Phase 3 override of the inherited prompt's failure rule."""
    _require_fields(
        policy, _FAILURE_POLICY_FIELDS, subject="reviewer failure policy")
    expected = {
        "schema_version": REVIEWER_FAILURE_POLICY_SCHEMA,
        "stage": "main",
        "status": "offline_contract_pending_exact_manifest_authorization",
        "reviewer_prompt_sha256": _require_sha256(
            reviewer_prompt_sha256, field="reviewer_prompt_sha256"),
        "overrides_reviewer_prompt_field": "failure_rule",
        "parse_failure": "commit_malformed_non_allow",
        "tool_use": "commit_evidenced_reviewer_error_non_allow",
        "timeout_or_unavailability": (
            "abort_wave_without_decision_and_void_no_resume_identity"),
        "execution_authorized": False,
        "provider_calls_authorized": False,
    }
    if dict(policy) != expected:
        raise MainReviewerProvenanceError(
            "reviewer failure policy differs from the exact Phase 3 contract")


def _load_frozen_inputs(
    *,
    reviewer_prompt_path: Path,
    expected_reviewer_prompt_raw_sha256: str,
    reviewer_failure_policy_path: Path,
    expected_reviewer_failure_policy_raw_sha256: str,
    capacity_plan_path: Path,
    expected_capacity_plan_raw_sha256: str,
    capacity_result_path: Path,
    expected_capacity_result_raw_sha256: str,
    capacity_dispatch_history_path: Path,
    expected_capacity_dispatch_history_raw_sha256: str,
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    expected_reviewer_cli_resolved_path: str,
) -> _FrozenReviewerInputs:
    prompt_raw = _read_stable(reviewer_prompt_path, subject="reviewer prompt")
    prompt_raw_sha = _raw_sha256(prompt_raw)
    if prompt_raw_sha != _require_sha256(
        expected_reviewer_prompt_raw_sha256,
        field="expected_reviewer_prompt_raw_sha256",
    ):
        raise MainReviewerProvenanceError("reviewer prompt bytes differ from the expected input")
    prompt_artifact = _strict_json(prompt_raw, subject="reviewer prompt")
    prompt = _require_text(prompt_artifact.get("prompt"), field="reviewer_prompt.prompt")
    prompt_sha = _raw_sha256(prompt.encode("utf-8"))
    if prompt_artifact.get("prompt_sha256") != prompt_sha:
        raise MainReviewerProvenanceError("reviewer prompt content differs from its declared hash")

    policy_raw = _read_stable(
        reviewer_failure_policy_path, subject="reviewer failure policy")
    policy_raw_sha = _raw_sha256(policy_raw)
    if policy_raw_sha != _require_sha256(
        expected_reviewer_failure_policy_raw_sha256,
        field="expected_reviewer_failure_policy_raw_sha256",
    ):
        raise MainReviewerProvenanceError(
            "reviewer failure policy bytes differ from the expected input")
    policy = _strict_json(policy_raw, subject="reviewer failure policy")
    validate_main_reviewer_failure_policy(
        policy, reviewer_prompt_sha256=prompt_sha)

    capacity_raw = _read_stable(capacity_plan_path, subject="capacity plan")
    capacity_raw_sha = _raw_sha256(capacity_raw)
    if capacity_raw_sha != _require_sha256(
        expected_capacity_plan_raw_sha256,
        field="expected_capacity_plan_raw_sha256",
    ):
        raise MainReviewerProvenanceError("capacity plan bytes differ from the expected input")
    capacity = _strict_json(capacity_raw, subject="capacity plan")
    configuration = capacity.get("reviewer_configuration")
    if not isinstance(configuration, Mapping):
        raise MainReviewerProvenanceError(
            "capacity plan has no reviewer_configuration object")
    if configuration.get("model") != expected_reviewer_model:
        raise MainReviewerProvenanceError("capacity plan reviewer model drifted")
    if configuration.get("reasoning_effort") != expected_reviewer_reasoning_effort:
        raise MainReviewerProvenanceError("capacity plan reviewer effort drifted")
    concurrency = _require_positive_int(
        configuration.get("concurrency"),
        field="capacity_plan.reviewer_configuration.concurrency",
    )
    if concurrency != expected_reviewer_concurrency:
        raise MainReviewerProvenanceError("capacity plan reviewer concurrency drifted")
    if configuration.get("reviewer_cli_resolved_path") != expected_reviewer_cli_resolved_path:
        raise MainReviewerProvenanceError("capacity plan reviewer CLI path drifted")
    cli_sha = _require_sha256(
        configuration.get("reviewer_cli_wrapper_raw_sha256"),
        field="capacity_plan.reviewer_cli_wrapper_raw_sha256",
    )
    cli_bytes = _require_non_negative_int(
        configuration.get("reviewer_cli_wrapper_byte_count"),
        field="capacity_plan.reviewer_cli_wrapper_byte_count",
    )
    wave_size = _require_positive_int(
        configuration.get("actual_capacity_wave_size"),
        field="capacity_plan.reviewer_configuration.actual_capacity_wave_size",
    )
    pending_limit = _require_positive_int(
        configuration.get("wave_pending_payload_limit"),
        field="capacity_plan.reviewer_configuration.wave_pending_payload_limit",
    )
    workload = capacity.get("workload")
    thresholds = capacity.get("capacity_thresholds")
    if not isinstance(workload, Mapping) or not isinstance(thresholds, Mapping):
        raise MainReviewerProvenanceError(
            "capacity plan omits workload or capacity_thresholds")
    measured_wave_size = _require_positive_int(
        workload.get("wave_size"), field="capacity_plan.workload.wave_size")
    maximum_payloads = _require_non_negative_int(
        thresholds.get("maximum_unique_review_payloads_zero_dedup"),
        field=(
            "capacity_plan.capacity_thresholds."
            "maximum_unique_review_payloads_zero_dedup"),
    )
    if wave_size > pending_limit or wave_size != measured_wave_size:
        raise MainReviewerProvenanceError(
            "capacity plan reviewer wave contract is inconsistent")

    result_raw = _read_stable(capacity_result_path, subject="capacity result")
    result_raw_sha = _raw_sha256(result_raw)
    if result_raw_sha != _require_sha256(
        expected_capacity_result_raw_sha256,
        field="expected_capacity_result_raw_sha256",
    ):
        raise MainReviewerProvenanceError(
            "capacity result bytes differ from the expected input")
    result = _strict_json(result_raw, subject="capacity result")

    history_raw = _read_stable(
        capacity_dispatch_history_path, subject="capacity dispatch history")
    history_raw_sha = _raw_sha256(history_raw)
    if history_raw_sha != _require_sha256(
        expected_capacity_dispatch_history_raw_sha256,
        field="expected_capacity_dispatch_history_raw_sha256",
    ):
        raise MainReviewerProvenanceError(
            "capacity dispatch history bytes differ from the expected input")
    try:
        source = capacity.get("source_locations")
        if not isinstance(source, Mapping):
            raise ValueError("capacity plan omits source_locations")
        archive = Path(str(source["sealed_archive"]))
        finalization = Path(str(source["finalization_record"]))
        if not finalization.is_absolute():
            finalization = Path(__file__).resolve().parents[1] / finalization
        snapshot = phase3_main_review_capacity_preflight.collect_source_snapshot(
            archive_dir=archive,
            finalization_path=finalization,
        )
        derivation_tag = (
            phase3_main_review_capacity_preflight.derivation_tag_from_plan(capacity)
        )
        if derivation_tag == phase3_main_review_capacity_preflight.DERIVATION_TAG_V1:
            derived_workload = phase3_main_review_capacity_preflight.derive_workload(snapshot)
        else:
            derived_workload = phase3_main_review_capacity_preflight.derive_workload(
                snapshot, derivation_tag=derivation_tag
            )
        phase3_main_review_capacity_preflight.validate_plan(
            capacity,
            snapshot=snapshot,
            workload=derived_workload,
        )
        dispatch_history = (
            phase3_main_review_capacity_preflight.load_bound_dispatch_history(
                capacity_dispatch_history_path,
                plan=capacity,
            )
        )
        if dispatch_history.raw_sha256 != history_raw_sha:
            raise ValueError(
                "capacity dispatch history changed during semantic validation")
        phase3_main_review_capacity_preflight.validate_result(
            result,
            plan=capacity,
            workload=derived_workload,
            dispatch_history=dispatch_history,
            as_of_utc=datetime.now(timezone.utc),
            require_current_freshness=False,
        )
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise MainReviewerProvenanceError(
            f"capacity evidence failed semantic validation: {exc}") from exc
    measurement_environment = result.get("measurement_environment")
    if not isinstance(measurement_environment, Mapping):
        raise MainReviewerProvenanceError(
            "capacity result omits measurement_environment")
    capacity_host_identity = _require_text(
        measurement_environment.get("host_identity"),
        field="capacity_result.measurement_environment.host_identity",
    )
    reviewer_cli_version = _require_text(
        measurement_environment.get("reviewer_cli_version"),
        field="capacity_result.measurement_environment.reviewer_cli_version",
    )
    capacity_completed_at = _require_utc_datetime(
        result.get("completed_at_utc"),
        field="capacity_result.completed_at_utc",
    )
    validity = capacity.get("validity")
    if not isinstance(validity, Mapping):
        raise MainReviewerProvenanceError("capacity plan omits validity")
    validity_hours = _require_positive_int(
        validity.get("valid_for_hours"),
        field="capacity_plan.validity.valid_for_hours",
    )
    capacity_expires_at = capacity_completed_at + timedelta(hours=validity_hours)
    return _FrozenReviewerInputs(
        prompt=prompt,
        prompt_sha256=prompt_sha,
        cli_wrapper_raw_sha256=cli_sha,
        cli_wrapper_byte_count=cli_bytes,
        reviewer_prompt_raw_sha256=prompt_raw_sha,
        reviewer_failure_policy_raw_sha256=policy_raw_sha,
        capacity_plan_raw_sha256=capacity_raw_sha,
        capacity_result_raw_sha256=result_raw_sha,
        capacity_dispatch_history_raw_sha256=history_raw_sha,
        capacity_host_identity=capacity_host_identity,
        reviewer_cli_version=reviewer_cli_version,
        capacity_completed_at=capacity_completed_at,
        capacity_expires_at=capacity_expires_at,
        reviewer_concurrency=concurrency,
        wave_size=wave_size,
        maximum_unique_payloads=maximum_payloads,
    )


def _validate_worklist(
    raw: bytes,
    *,
    frozen_prompt: str,
    frozen_prompt_sha256: str,
    subject: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    worklist = _strict_json(raw, subject=subject)
    _require_fields(worklist, _WORKLIST_FIELDS, subject=subject)
    if raw != _expected_worklist_raw(worklist):
        raise MainReviewerProvenanceError(f"{subject} is not encoded by the frozen writer")
    if worklist.get("frozen_prompt_sha256") != frozen_prompt_sha256:
        raise MainReviewerProvenanceError(f"{subject} names the wrong reviewer prompt")
    if worklist.get("separator") != SUBAGENT_PAYLOAD_SEPARATOR:
        raise MainReviewerProvenanceError(f"{subject} separator drifted")
    raw_items = worklist.get("items")
    if not isinstance(raw_items, list):
        raise MainReviewerProvenanceError(f"{subject}.items must be a list")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, value in enumerate(raw_items, 1):
        if not isinstance(value, Mapping):
            raise MainReviewerProvenanceError(
                f"{subject} item {position} must be an object")
        item = dict(value)
        _require_fields(item, _WORKLIST_ITEM_FIELDS, subject=f"{subject} item {position}")
        payload = _require_sha256(
            item.get("payload_sha256"), field=f"{subject} item {position}.payload_sha256")
        if payload in seen:
            raise MainReviewerProvenanceError(f"{subject} repeats payload {payload}")
        seen.add(payload)
        query = _require_string(item.get("query"), field=f"{subject} item {position}.query")
        candidate_a = _require_string(
            item.get("candidate_a"), field=f"{subject} item {position}.candidate_a")
        candidate_b = _require_string(
            item.get("candidate_b"), field=f"{subject} item {position}.candidate_b")
        if payload_hash(query, candidate_a, candidate_b) != payload:
            raise MainReviewerProvenanceError(
                f"{subject} item {position} payload hash drifted")
        prompt = compose_subagent_prompt(
            frozen_prompt,
            query=query,
            candidate_a=candidate_a,
            candidate_b=candidate_b,
        )
        prompt_sha = _raw_sha256(prompt.encode("utf-8"))
        if item.get("subagent_prompt") != prompt:
            raise MainReviewerProvenanceError(
                f"{subject} item {position} prompt composition drifted")
        if item.get("subagent_prompt_sha256") != prompt_sha:
            raise MainReviewerProvenanceError(
                f"{subject} item {position} prompt hash drifted")
        items.append(item)
    return worklist, items


def _validate_packet_index(
    raw: bytes,
    *,
    worklist_items: list[dict[str, Any]],
    packet_dir: Path,
) -> tuple[list[dict[str, Any]], set[Path]]:
    index = _strict_json(raw, subject="reviewer packet index")
    _require_fields(index, _PACKET_INDEX_FIELDS, subject="reviewer packet index")
    count = _require_non_negative_int(index.get("count"), field="packet index count")
    values = index.get("items")
    if not isinstance(values, list) or count != len(values) or count != len(worklist_items):
        raise MainReviewerProvenanceError("reviewer packet index count drifted")
    items: list[dict[str, Any]] = []
    packet_files: set[Path] = set()
    for position, (value, worklist_item) in enumerate(zip(values, worklist_items), 1):
        if not isinstance(value, Mapping):
            raise MainReviewerProvenanceError(
                f"reviewer packet index item {position} must be an object")
        item = dict(value)
        _require_fields(
            item, _PACKET_INDEX_ITEM_FIELDS,
            subject=f"reviewer packet index item {position}")
        if item.get("n") != position or isinstance(item.get("n"), bool):
            raise MainReviewerProvenanceError("reviewer packet numbering drifted")
        payload = worklist_item["payload_sha256"]
        expected_name = f"{position:05d}_{payload[:12]}.txt"
        if item.get("file") != expected_name:
            raise MainReviewerProvenanceError("reviewer packet filename drifted")
        if item.get("payload_sha256") != payload:
            raise MainReviewerProvenanceError("reviewer packet payload order drifted")
        prompt_sha = worklist_item["subagent_prompt_sha256"]
        if item.get("prompt_sha256") != prompt_sha:
            raise MainReviewerProvenanceError("reviewer packet prompt hash drifted")
        packet = packet_dir / expected_name
        packet_raw = _read_stable(packet, subject=f"reviewer packet {expected_name}")
        expected_raw = str(worklist_item["subagent_prompt"]).encode("utf-8")
        if packet_raw != expected_raw:
            raise MainReviewerProvenanceError(
                f"reviewer packet {expected_name} differs from the exact composed prompt")
        packet_files.add(packet)
        items.append(item)
    return items, packet_files


def _validate_dispatch_guard_snapshot(
    raw: bytes,
    *,
    expected_raw_sha256: str,
    expected_run_id: str,
    expected_manifest_canonical_sha256: str,
    expected_authorization_canonical_sha256: str,
    expected_authorization_raw_sha256: str,
    expected_authorization_signature_raw_sha256: str,
    expected_authorization_approved_at: datetime,
    expected_authorization_deadline: datetime,
    expected_capacity_completed_at: datetime,
    expected_capacity_expires_at: datetime,
    expected_capacity_paths: Mapping[str, Path],
    expected_capacity_raw_sha256s: Mapping[str, str],
    expected_reviewer_cli_resolved_path: str,
    expected_cli_wrapper_raw_sha256: str,
    expected_cli_wrapper_byte_count: int,
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    expected_reviewer_cli_version: str,
    expected_capacity_host_identity: str,
    packet_dir: Path,
    output_path: Path,
    worklist_snapshot_path: Path,
    worklist_snapshot_raw: bytes,
    packet_index_path: Path,
    packet_index_raw: bytes,
    index_items: list[dict[str, Any]],
    reviewer_recovery_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if _raw_sha256(raw) != expected_raw_sha256:
        raise MainReviewerProvenanceError("reviewer dispatch guard hash drifted")
    guard = _strict_json(raw, subject="reviewer dispatch guard")
    _require_fields(guard, _DISPATCH_GUARD_FIELDS, subject="reviewer dispatch guard")
    exact_values = {
        "schema_version": codex_reviewer_batch.DISPATCH_GUARD_SCHEMA,
        "run_id": expected_run_id,
        "manifest_canonical_sha256": expected_manifest_canonical_sha256,
        "authorization_canonical_sha256": expected_authorization_canonical_sha256,
        "authorization_approved_at_utc": expected_authorization_approved_at.isoformat(),
        "authorization_valid_until_utc": expected_authorization_deadline.isoformat(),
        "capacity_completed_at_utc": expected_capacity_completed_at.isoformat(),
        "capacity_expires_at_utc": expected_capacity_expires_at.isoformat(),
        "reviewer_model": expected_reviewer_model,
        "reviewer_reasoning_effort": expected_reviewer_reasoning_effort,
        "reviewer_concurrency": expected_reviewer_concurrency,
        "reviewer_cli_version": expected_reviewer_cli_version,
        "capacity_host_identity": expected_capacity_host_identity,
        "packet_directory": packet_dir.resolve().as_posix(),
        "output_path": output_path.resolve().as_posix(),
    }
    for field, expected in exact_values.items():
        if guard.get(field) != expected:
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard {field} drifted")
    bindings = guard.get("artifact_bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != _DISPATCH_GUARD_ARTIFACT_NAMES:
        raise MainReviewerProvenanceError(
            "reviewer dispatch guard artifact bindings drifted")
    batch_runner_path, batch_runner_sha, batch_runner_bytes = (
        codex_reviewer_batch._batch_runner_identity())  # noqa: SLF001
    if reviewer_recovery_context is not None:
        historical = bindings["batch_runner"]
        if historical not in reviewer_recovery_context["accepted_batch_runner_bindings"]:
            raise MainReviewerProvenanceError("historical reviewer guard code differs from signed recovery")
        batch_runner_path, batch_runner_sha, batch_runner_bytes = (
            historical["path"], historical["raw_sha256"], historical["byte_count"])
    expected_shas = {
        "authorization": expected_authorization_raw_sha256,
        "authorization_signature": expected_authorization_signature_raw_sha256,
        "capacity_plan": expected_capacity_raw_sha256s["capacity_plan"],
        "capacity_result": expected_capacity_raw_sha256s["capacity_result"],
        "capacity_dispatch_history": expected_capacity_raw_sha256s[
            "capacity_dispatch_history"],
        "reviewer_cli_wrapper": expected_cli_wrapper_raw_sha256,
        "worklist_snapshot": _raw_sha256(worklist_snapshot_raw),
        "packet_index": _raw_sha256(packet_index_raw),
        "batch_runner": batch_runner_sha,
    }
    for name, expected_sha in expected_shas.items():
        binding = bindings[name]
        if not isinstance(binding, Mapping) or set(binding) != _DISPATCH_GUARD_BINDING_FIELDS:
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard {name} binding fields drifted")
        if binding.get("raw_sha256") != expected_sha:
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard {name} hash drifted")
        byte_count = _require_non_negative_int(
            binding.get("byte_count"), field=f"dispatch guard {name} byte_count")
        raw_path = binding.get("path")
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard {name} path must be absolute")
        supplied_path = Path(raw_path)
        if supplied_path.is_symlink() or supplied_path.resolve().as_posix() != raw_path:
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard {name} path must be resolved and unlinked")
        if name != "batch_runner" or reviewer_recovery_context is None:
            reopened = _read_stable(
                supplied_path, subject=f"reviewer dispatch guard {name} artifact")
            if len(reopened) != byte_count or _raw_sha256(reopened) != expected_sha:
                raise MainReviewerProvenanceError(
                    f"reviewer dispatch guard {name} bytes drifted")
    for name, expected_path in expected_capacity_paths.items():
        binding = cast(Mapping[str, Any], bindings[name])
        if Path(str(binding["path"])).resolve() != expected_path.resolve():
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard {name} path drifted")
    cli_binding = cast(Mapping[str, Any], bindings["reviewer_cli_wrapper"])
    if (
        cli_binding.get("path") != expected_reviewer_cli_resolved_path
        or cli_binding.get("byte_count") != expected_cli_wrapper_byte_count
    ):
        raise MainReviewerProvenanceError(
            "reviewer dispatch guard CLI wrapper identity drifted")
    exact_artifact_paths = {
        "worklist_snapshot": worklist_snapshot_path,
        "packet_index": packet_index_path,
        "batch_runner": Path(batch_runner_path),
    }
    for name, expected_path in exact_artifact_paths.items():
        binding = cast(Mapping[str, Any], bindings[name])
        if Path(str(binding["path"])).resolve() != expected_path.resolve():
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard {name} path drifted")
    if cast(Mapping[str, Any], bindings["worklist_snapshot"]).get(
        "byte_count"
    ) != len(worklist_snapshot_raw):
        raise MainReviewerProvenanceError(
            "reviewer dispatch guard worklist snapshot size drifted")
    if cast(Mapping[str, Any], bindings["packet_index"]).get(
        "byte_count"
    ) != len(packet_index_raw):
        raise MainReviewerProvenanceError(
            "reviewer dispatch guard packet index size drifted")
    if cast(Mapping[str, Any], bindings["batch_runner"]).get(
        "byte_count"
    ) != batch_runner_bytes:
        raise MainReviewerProvenanceError(
            "reviewer dispatch guard batch runner size drifted")

    packet_bindings = guard.get("packet_bindings")
    if not isinstance(packet_bindings, list) or len(packet_bindings) != len(index_items):
        raise MainReviewerProvenanceError(
            "reviewer dispatch guard packet binding count drifted")
    for position, (binding, index_item) in enumerate(
        zip(packet_bindings, index_items), 1
    ):
        if (
            not isinstance(binding, Mapping)
            or set(binding) != _DISPATCH_GUARD_PACKET_BINDING_FIELDS
        ):
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard packet binding {position} fields drifted")
        packet_path = packet_dir / str(index_item["file"])
        packet_raw = _read_stable(
            packet_path, subject=f"reviewer dispatch guard packet {position}")
        expected_packet_binding = {
            "file": index_item["file"],
            "payload_sha256": index_item["payload_sha256"],
            "prompt_sha256": index_item["prompt_sha256"],
            "byte_count": len(packet_raw),
        }
        if dict(binding) != expected_packet_binding:
            raise MainReviewerProvenanceError(
                f"reviewer dispatch guard packet binding {position} drifted")
    return guard


def _normalized_ruling_from_receipt(packet_dir: Path, receipt: Mapping[str, Any]) -> str:
    artifacts = cast(Mapping[str, Mapping[str, Any]], receipt["artifacts"])
    ruling_path = packet_dir / str(artifacts["ruling"]["path"])
    try:
        return ruling_path.read_bytes().decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise MainReviewerProvenanceError("reviewer ruling evidence is not UTF-8") from exc


def _validate_dispatch_reservation(
    packet_dir: Path,
    *,
    guard_evidence: Mapping[str, Any],
    guard_raw_sha256: str,
    index_item: Mapping[str, Any],
    output_path: Path,
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    expected_reviewer_cli_version: str,
    expected_capacity_host_identity: str,
    outcome: Mapping[str, Any],
) -> Path:
    binding = guard_evidence.get("reservation")
    if (
        not isinstance(binding, Mapping)
        or set(binding) != _DISPATCH_RESERVATION_BINDING_FIELDS
    ):
        raise MainReviewerProvenanceError(
            "reviewer invocation lacks its exact durable dispatch reservation")
    payload = str(index_item["payload_sha256"])
    expected_relative = (
        f"{codex_reviewer_batch.DISPATCH_RESERVATION_DIRECTORY_NAME}/"
        f"{guard_raw_sha256}_{payload}.json"
    )
    if binding.get("path") != expected_relative:
        raise MainReviewerProvenanceError(
            "reviewer dispatch reservation path differs from its deterministic identity")
    reservation_path = packet_dir / expected_relative
    if reservation_path.is_symlink():
        raise MainReviewerProvenanceError(
            "reviewer dispatch reservation must not be linked")
    reservation_raw = _read_stable(
        reservation_path, subject="reviewer dispatch reservation")
    if (
        _raw_sha256(reservation_raw) != _require_sha256(
            binding.get("raw_sha256"),
            field="reviewer dispatch reservation raw_sha256",
        )
        or len(reservation_raw) != _require_non_negative_int(
            binding.get("byte_count"),
            field="reviewer dispatch reservation byte_count",
        )
    ):
        raise MainReviewerProvenanceError(
            "reviewer dispatch reservation bytes drifted")
    reservation = _strict_json(
        reservation_raw, subject="reviewer dispatch reservation")
    _require_fields(
        reservation,
        _DISPATCH_RESERVATION_FIELDS,
        subject="reviewer dispatch reservation",
    )
    exact_values = {
        "schema_version": codex_reviewer_batch.DISPATCH_RESERVATION_SCHEMA,
        "guard_raw_sha256": guard_raw_sha256,
        "payload_sha256": payload,
        "prompt_sha256": index_item["prompt_sha256"],
        "packet_file": index_item["file"],
        "packet_directory": packet_dir.resolve().as_posix(),
        "output_path": output_path.resolve().as_posix(),
        "reviewer_model": expected_reviewer_model,
        "reviewer_reasoning_effort": expected_reviewer_reasoning_effort,
        "reviewer_concurrency": expected_reviewer_concurrency,
        "reviewer_cli_version": expected_reviewer_cli_version,
        "capacity_host_identity": expected_capacity_host_identity,
    }
    for field, expected in exact_values.items():
        if reservation.get(field) != expected:
            raise MainReviewerProvenanceError(
                f"reviewer dispatch reservation {field} drifted")
    reserved_at = _require_utc(
        reservation.get("reserved_at_utc"),
        field="reviewer dispatch reservation reserved_at_utc",
    )
    if (
        guard_evidence.get("checked_at_utc") != reserved_at
        or outcome.get("started_at_utc") != reserved_at
    ):
        raise MainReviewerProvenanceError(
            "reviewer dispatch reservation timestamp differs from invocation start")
    checked_at = _require_utc_datetime(
        reserved_at, field="reviewer dispatch reservation reserved_at_utc")
    released_at = _require_utc_datetime(
        guard_evidence.get("released_at_utc"),
        field="reviewer dispatch guard released_at_utc",
    )
    completed_at = _require_utc_datetime(
        outcome.get("completed_at_utc"),
        field="reviewer dispatch outcome completed_at_utc",
    )
    approved_at = _require_utc_datetime(
        guard_evidence.get("authorization_approved_at_utc"),
        field="reviewer dispatch guard authorization_approved_at_utc",
    )
    valid_until = _require_utc_datetime(
        guard_evidence.get("authorization_valid_until_utc"),
        field="reviewer dispatch guard authorization_valid_until_utc",
    )
    capacity_completed = _require_utc_datetime(
        guard_evidence.get("capacity_completed_at_utc"),
        field="reviewer dispatch guard capacity_completed_at_utc",
    )
    capacity_expires = _require_utc_datetime(
        guard_evidence.get("capacity_expires_at_utc"),
        field="reviewer dispatch guard capacity_expires_at_utc",
    )
    if not (
        checked_at <= released_at <= completed_at
        and approved_at <= released_at <= valid_until
        and capacity_completed <= released_at <= capacity_expires
    ):
        raise MainReviewerProvenanceError(
            "reviewer dispatch reservation was not actively released before subprocess")
    return reservation_path


def _expected_evidence_paths(
    packet_dir: Path,
    packet: Path,
    receipt: Mapping[str, Any],
) -> tuple[set[Path], set[Path]]:
    evidence_dir = packet_dir / "reviewer_evidence" / f"{packet.name}.evidence"
    receipt_path = evidence_dir / "invocation_receipt.json"
    artifacts = cast(Mapping[str, Mapping[str, Any]], receipt["artifacts"])
    files = {receipt_path}
    if receipt.get("reconnect_recovery") is not None:
        files.add(packet_dir / str(receipt["reconnect_recovery"]["path"]))
    for binding in artifacts.values():
        files.add(packet_dir / str(binding["path"]))
    guard = cast(Mapping[str, Any], receipt["dispatch_guard"])
    reservation = cast(Mapping[str, Any], guard["reservation"])
    reservation_path = packet_dir / str(reservation["path"])
    files.add(reservation_path)
    return files, {
        packet_dir / "reviewer_evidence",
        evidence_dir,
        packet_dir / codex_reviewer_batch.DISPATCH_RESERVATION_DIRECTORY_NAME,
    }


def _validate_rulings(
    raw: bytes,
    *,
    packet_dir: Path,
    index_items: list[dict[str, Any]],
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    expected_reviewer_cli_resolved_path: str,
    expected_reviewer_cli_version: str,
    expected_capacity_host_identity: str,
    expected_capacity_completed_at: datetime,
    expected_capacity_expires_at: datetime,
    expected_authorization_deadline_utc: str,
    expected_authorization_approved_at: datetime,
    expected_wave_recorded_at: datetime,
    expected_dispatch_guard_raw_sha256: str,
    expected_cli_wrapper_raw_sha256: str,
    expected_cli_wrapper_byte_count: int,
    reviewer_recovery_context: Mapping[str, Any] | None = None,
    accepted_batch_runner_bindings: Any = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, int],
    set[Path],
    set[Path],
]:
    expected_authorization_deadline = _require_utc_datetime(
        expected_authorization_deadline_utc,
        field="expected_authorization_deadline_utc",
    )
    rows = _strict_jsonl(raw, subject="reviewer rulings")
    expected = {str(item["payload_sha256"]): item for item in index_items}
    if len(rows) != len(expected):
        raise MainReviewerProvenanceError("reviewer ruling count is not exact")
    observed_payloads: set[str] = set()
    expected_decisions: list[dict[str, Any]] = []
    counts = {"parsed": 0, "malformed": 0, "reviewer_error": 0}
    evidence_files: set[Path] = set()
    evidence_dirs: set[Path] = set()
    for row_number, row in enumerate(rows, 1):
        payload = _require_sha256(
            row.get("payload_sha256"), field=f"reviewer ruling {row_number}.payload_sha256")
        if payload not in expected or payload in observed_payloads:
            raise MainReviewerProvenanceError(
                f"reviewer ruling identity set is not exact at row {row_number}")
        observed_payloads.add(payload)
        index_item = expected[payload]
        packet = packet_dir / str(index_item["file"])
        evidence = row.get("evidence")
        if not isinstance(evidence, Mapping):
            raise MainReviewerProvenanceError(
                f"reviewer ruling {row_number} has no invocation evidence")
        try:
            receipt = validate_invocation_evidence(
                packet,
                cast(Mapping[str, Any], evidence),
                expected_model=expected_reviewer_model,
                expected_effort=expected_reviewer_reasoning_effort,
                expected_concurrency=expected_reviewer_concurrency,
                **({"accepted_batch_runner_bindings": accepted_batch_runner_bindings}
                   if accepted_batch_runner_bindings is not None else {}),
                **({"reviewer_recovery_context": reviewer_recovery_context}
                   if reviewer_recovery_context is not None else {}),
            )
        except (OSError, TypeError, ValueError) as exc:
            raise MainReviewerProvenanceError(
                f"reviewer ruling {row_number} invocation evidence is invalid: {exc}") from exc
        invocation = cast(Mapping[str, Any], receipt["invocation"])
        if invocation.get("batch_concurrency") != expected_reviewer_concurrency:
            raise MainReviewerProvenanceError(
                "reviewer invocation concurrency drifted")
        if invocation.get("codex_cli_resolved_path") != expected_reviewer_cli_resolved_path:
            raise MainReviewerProvenanceError("reviewer invocation CLI path drifted")
        if invocation.get("codex_cli_wrapper_raw_sha256") != expected_cli_wrapper_raw_sha256:
            raise MainReviewerProvenanceError("reviewer invocation CLI wrapper hash drifted")
        if invocation.get("codex_cli_wrapper_byte_count") != expected_cli_wrapper_byte_count:
            raise MainReviewerProvenanceError("reviewer invocation CLI wrapper size drifted")
        if invocation.get("codex_cli_version") != expected_reviewer_cli_version:
            raise MainReviewerProvenanceError("reviewer invocation CLI version drifted")
        if invocation.get("host_identity") != expected_capacity_host_identity:
            raise MainReviewerProvenanceError(
                "reviewer invocation host differs from the capacity-measured host")
        outcome = cast(Mapping[str, Any], receipt["outcome"])
        dispatch_guard = receipt.get("dispatch_guard")
        if (
            not isinstance(dispatch_guard, Mapping)
            or dispatch_guard.get("verified") is not True
            or dispatch_guard.get("snapshot_raw_sha256")
            != expected_dispatch_guard_raw_sha256
            or dispatch_guard.get("reviewer_cli_version")
            != expected_reviewer_cli_version
        ):
            raise MainReviewerProvenanceError(
                "reviewer invocation lacks the exact successful dispatch guard")
        _validate_dispatch_reservation(
            packet_dir,
            guard_evidence=dispatch_guard,
            guard_raw_sha256=expected_dispatch_guard_raw_sha256,
            index_item=index_item,
            output_path=packet_dir / "rulings.jsonl",
            expected_reviewer_model=expected_reviewer_model,
            expected_reviewer_reasoning_effort=expected_reviewer_reasoning_effort,
            expected_reviewer_concurrency=expected_reviewer_concurrency,
            expected_reviewer_cli_version=expected_reviewer_cli_version,
            expected_capacity_host_identity=expected_capacity_host_identity,
            outcome=outcome,
        )
        if outcome.get("authorization_deadline_utc") != expected_authorization_deadline_utc:
            raise MainReviewerProvenanceError("reviewer invocation authorization deadline drifted")
        started_at = _require_utc_datetime(
            outcome.get("started_at_utc"),
            field=f"reviewer ruling {row_number} started_at_utc",
        )
        completed_at = _require_utc_datetime(
            outcome.get("completed_at_utc"),
            field=f"reviewer ruling {row_number} completed_at_utc",
        )
        if not expected_capacity_completed_at <= started_at <= expected_capacity_expires_at:
            raise MainReviewerProvenanceError(
                "reviewer invocation started outside the capacity validity window")
        if started_at > expected_authorization_deadline:
            raise MainReviewerProvenanceError(
                "reviewer invocation started after the signed authorization deadline")
        if not (
            expected_authorization_approved_at
            <= started_at
            <= completed_at
            <= expected_wave_recorded_at
        ):
            raise MainReviewerProvenanceError(
                "reviewer invocation falls outside the authorized wave timeline")
        if outcome.get("result_ok") is not True or outcome.get("event_stream_errors") != []:
            raise MainReviewerProvenanceError(
                "completed reviewer ruling lacks one successful structural event stream")
        commands = outcome.get("commands")
        if not isinstance(commands, list):
            raise MainReviewerProvenanceError("reviewer invocation command evidence drifted")
        new_files, new_dirs = _expected_evidence_paths(packet_dir, packet, receipt)
        evidence_files.update(new_files)
        evidence_dirs.update(new_dirs)

        raw_output = row.get("raw_output")
        if not isinstance(raw_output, str):
            raise MainReviewerProvenanceError("reviewer ruling raw_output must be text")
        if set(row) == _CLEAN_RULING_FIELDS:
            if type(row.get("tool_uses")) is not int or row["tool_uses"] != 0:
                raise MainReviewerProvenanceError(
                    "clean reviewer ruling tool_uses must be the integer zero")
            if commands:
                raise MainReviewerProvenanceError(
                    "clean reviewer ruling has nonempty tool-use evidence")
            if row.get("prompt_sha256") != index_item["prompt_sha256"]:
                raise MainReviewerProvenanceError("reviewer ruling prompt proof drifted")
            if raw_output != _normalized_ruling_from_receipt(packet_dir, receipt):
                raise MainReviewerProvenanceError(
                    "reviewer ruling output differs from retained ruling bytes")
            label, clause, rationale = parse_reviewer_output(raw_output)
            status = "parsed" if label is not None else "malformed"
        elif set(row) == _ERROR_RULING_FIELDS:
            if row.get("status") != "reviewer_error":
                raise MainReviewerProvenanceError("reviewer error ruling status drifted")
            if not commands:
                raise MainReviewerProvenanceError(
                    "completed reviewer_error lacks retained tool-use evidence")
            expected_refusal = (
                "TOOL_USE_DETECTED: reviewer issued "
                f"{len(commands)} command(s); ruling discarded as non-blind. "
                f"first={commands[0]!r}"
            )
            if raw_output != expected_refusal:
                raise MainReviewerProvenanceError(
                    "tool-use reviewer refusal differs from the frozen synthetic output")
            label = clause = rationale = None
            status = "reviewer_error"
        else:
            raise MainReviewerProvenanceError(
                f"reviewer ruling {row_number} fields drifted")
        counts[status] += 1
        expected_decisions.append({
            "payload_sha256": payload,
            "label": label,
            "clause": clause,
            "rationale": rationale,
            "raw_output": raw_output,
            "status": status,
        })
    if observed_payloads != set(expected):
        raise MainReviewerProvenanceError("reviewer ruling identity set is not exact")
    return rows, expected_decisions, counts, evidence_files, evidence_dirs


def _validate_packet_tree(
    packet_dir: Path,
    *,
    packet_files: set[Path],
    evidence_files: set[Path],
    evidence_dirs: set[Path],
    dispatch_guard_path: Path,
) -> None:
    transaction_paths = phase3_main_reviewer_commit.reviewer_wave_transaction_paths(
        packet_dir)
    expected_files = packet_files | evidence_files | {
        packet_dir / "WORKLIST.json",
        packet_dir / "INDEX.json",
        packet_dir / "rulings.jsonl",
        dispatch_guard_path,
        transaction_paths.intent,
        transaction_paths.receipt,
    }
    actual_files: set[Path] = set()
    actual_dirs: set[Path] = set()
    for child in packet_dir.rglob("*"):
        if child.is_symlink():
            raise MainReviewerProvenanceError("reviewer packet tree contains a symbolic link")
        if child.is_file():
            actual_files.add(child)
        elif child.is_dir():
            actual_dirs.add(child)
        else:
            raise MainReviewerProvenanceError("reviewer packet tree contains a special file")
    if actual_files != expected_files or actual_dirs != evidence_dirs:
        raise MainReviewerProvenanceError("reviewer packet tree has missing or extra artifacts")


def _load_decision_rows(path: Path) -> list[dict[str, Any]]:
    raw = _read_stable(path, subject="review decision store")
    try:
        DualGateDecisionStore(path)
    except ValueError as exc:
        raise MainReviewerProvenanceError(
            f"review decision store chain is invalid: {exc}") from exc
    rows = _strict_jsonl(raw, subject="review decision store")
    for row in rows:
        if set(row) != DECISION_ROW_KEYS:
            raise MainReviewerProvenanceError("review decision row fields drifted")
    return rows


def _validate_decisions(
    decisions_path: Path,
    expected_decisions: list[dict[str, Any]],
) -> None:
    rows = _load_decision_rows(decisions_path)
    if len(rows) != len(expected_decisions):
        raise MainReviewerProvenanceError(
            "review decision store length differs from completed reviewer rulings")
    for sequence, (row, expected) in enumerate(zip(rows, expected_decisions)):
        if row.get("sequence") != sequence:
            raise MainReviewerProvenanceError("review decision sequence drifted")
        for field, value in expected.items():
            if row.get(field) != value:
                raise MainReviewerProvenanceError(
                    f"review decision order or content drifted at sequence {sequence}")


def _tree_digest(root: Path) -> str:
    entries: list[dict[str, Any]] = []
    for child in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = child.relative_to(root).as_posix()
        if child.is_symlink():
            raise MainReviewerProvenanceError("review packet root contains a symbolic link")
        if child.is_dir():
            entries.append({"path": relative, "type": "directory"})
        elif child.is_file():
            raw = _read_stable(child, subject=f"review packet tree file {relative}")
            entries.append({
                "path": relative,
                "type": "file",
                "byte_count": len(raw),
                "raw_sha256": _raw_sha256(raw),
            })
        else:
            raise MainReviewerProvenanceError("review packet root contains a special file")
    material = json.dumps(
        entries, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return _raw_sha256(material)


def review_packets_tree_canonical_sha256(root: str | Path) -> str:
    """Return the canonical digest of one stable, link-free reviewer packet tree."""
    supplied = Path(root)
    if not supplied.is_absolute():
        raise MainReviewerProvenanceError("review packet root must be absolute")
    if supplied.is_symlink():
        raise MainReviewerProvenanceError(
            "review packet root must not be a symbolic link")
    resolved = supplied.resolve()
    if not resolved.is_dir():
        raise MainReviewerProvenanceError(
            f"review packet root is not a directory: {resolved}")
    return _tree_digest(resolved)


def verify_main_reviewer_provenance(
    *,
    reviewer_index_path: str | Path,
    reviewer_worklist_path: str | Path,
    review_packets_root: str | Path,
    reviewer_prompt_path: str | Path,
    expected_reviewer_prompt_raw_sha256: str,
    reviewer_failure_policy_path: str | Path,
    expected_reviewer_failure_policy_raw_sha256: str,
    capacity_plan_path: str | Path,
    expected_capacity_plan_raw_sha256: str,
    capacity_result_path: str | Path,
    expected_capacity_result_raw_sha256: str,
    capacity_dispatch_history_path: str | Path,
    expected_capacity_dispatch_history_raw_sha256: str,
    expected_run_id: str,
    expected_manifest_canonical_sha256: str,
    expected_authorization_canonical_sha256: str,
    expected_authorization_raw_sha256: str,
    expected_authorization_signature_raw_sha256: str,
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    expected_reviewer_cli_resolved_path: str,
    expected_authorization_approved_at_utc: str,
    expected_authorization_deadline_utc: str,
    expected_finalization_recorded_at_utc: str,
    max_passes: int,
    decisions_path: str | Path,
    expected_reviewed_payload_sha256s: Collection[str],
    reviewer_recovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify all local reviewer-wave evidence and its exact decision-store join."""
    if isinstance(max_passes, bool) or not isinstance(max_passes, int) or max_passes < 1:
        raise MainReviewerProvenanceError("max_passes must be a positive integer")
    expected_run_id = _require_text(expected_run_id, field="expected_run_id")
    expected_manifest_canonical_sha256 = _require_sha256(
        expected_manifest_canonical_sha256,
        field="expected_manifest_canonical_sha256",
    )
    expected_authorization_canonical_sha256 = _require_sha256(
        expected_authorization_canonical_sha256,
        field="expected_authorization_canonical_sha256",
    )
    expected_authorization_raw_sha256 = _require_sha256(
        expected_authorization_raw_sha256,
        field="expected_authorization_raw_sha256",
    )
    expected_authorization_signature_raw_sha256 = _require_sha256(
        expected_authorization_signature_raw_sha256,
        field="expected_authorization_signature_raw_sha256",
    )
    expected_reviewer_model = _require_text(
        expected_reviewer_model, field="expected_reviewer_model")
    expected_reviewer_reasoning_effort = _require_text(
        expected_reviewer_reasoning_effort,
        field="expected_reviewer_reasoning_effort",
    )
    expected_reviewer_concurrency = _require_positive_int(
        expected_reviewer_concurrency,
        field="expected_reviewer_concurrency",
    )
    expected_reviewer_cli_resolved_path = _require_text(
        expected_reviewer_cli_resolved_path,
        field="expected_reviewer_cli_resolved_path",
    )
    expected_authorization_approved_at = _require_utc_datetime(
        expected_authorization_approved_at_utc,
        field="expected_authorization_approved_at_utc",
    )
    expected_authorization_deadline_utc = _require_utc(
        expected_authorization_deadline_utc,
        field="expected_authorization_deadline_utc",
    )
    expected_authorization_deadline = _require_utc_datetime(
        expected_authorization_deadline_utc,
        field="expected_authorization_deadline_utc",
    )
    expected_finalization_recorded_at = _require_utc_datetime(
        expected_finalization_recorded_at_utc,
        field="expected_finalization_recorded_at_utc",
    )
    expected_payload_list = list(expected_reviewed_payload_sha256s)
    expected_payloads = {
        _require_sha256(value, field="expected_reviewed_payload_sha256s item")
        for value in expected_payload_list
    }
    if len(expected_payloads) != len(expected_payload_list):
        raise MainReviewerProvenanceError("expected reviewed payload set contains duplicates")

    supplied_root = Path(review_packets_root)
    if not supplied_root.is_absolute():
        raise MainReviewerProvenanceError("review packet root must be absolute")
    if supplied_root.is_symlink():
        raise MainReviewerProvenanceError("review packet root must not be a symbolic link")
    root = supplied_root.resolve()
    if not root.is_dir():
        raise MainReviewerProvenanceError(f"review packet root is not a directory: {root}")
    frozen = _load_frozen_inputs(
        reviewer_prompt_path=Path(reviewer_prompt_path),
        expected_reviewer_prompt_raw_sha256=expected_reviewer_prompt_raw_sha256,
        reviewer_failure_policy_path=Path(reviewer_failure_policy_path),
        expected_reviewer_failure_policy_raw_sha256=(
            expected_reviewer_failure_policy_raw_sha256),
        capacity_plan_path=Path(capacity_plan_path),
        expected_capacity_plan_raw_sha256=expected_capacity_plan_raw_sha256,
        capacity_result_path=Path(capacity_result_path),
        expected_capacity_result_raw_sha256=expected_capacity_result_raw_sha256,
        capacity_dispatch_history_path=Path(capacity_dispatch_history_path),
        expected_capacity_dispatch_history_raw_sha256=(
            expected_capacity_dispatch_history_raw_sha256),
        expected_reviewer_model=expected_reviewer_model,
        expected_reviewer_reasoning_effort=expected_reviewer_reasoning_effort,
        expected_reviewer_concurrency=expected_reviewer_concurrency,
        expected_reviewer_cli_resolved_path=expected_reviewer_cli_resolved_path,
    )
    if len(expected_payloads) > frozen.maximum_unique_payloads:
        raise MainReviewerProvenanceError(
            "expected reviewer payload set exceeds the capacity-plan maximum")

    reviewer_index_file = Path(reviewer_index_path)
    decision_store_file = Path(decisions_path)
    reviewer_index_raw = _read_stable(reviewer_index_file, subject="reviewer index")
    wave_rows, wave_row_raws = _strict_jsonl_material(
        reviewer_index_raw, subject="reviewer index")
    # Validate the index spine before reopening any transaction. Otherwise a later
    # malformed row could make an earlier transaction report a prefix mismatch and
    # obscure the actual wave-order or timestamp defect.
    indexed_wave = 0
    indexed_recorded_at: datetime | None = None
    for row_number, row in enumerate(wave_rows, 1):
        _require_fields(row, _WAVE_FIELDS, subject=f"reviewer index row {row_number}")
        if row.get("schema_version") != REVIEWER_WAVE_SCHEMA:
            raise MainReviewerProvenanceError("unsupported reviewer wave schema")
        wave = _require_positive_int(row.get("wave"), field="reviewer wave")
        if wave <= indexed_wave or wave > max_passes:
            raise MainReviewerProvenanceError(
                "reviewer waves must be strictly increasing, unique, and within max_passes")
        recorded_at = _require_utc_datetime(
            row.get("recorded_at_utc"), field="reviewer wave recorded_at_utc")
        if indexed_recorded_at is not None and recorded_at <= indexed_recorded_at:
            raise MainReviewerProvenanceError(
                "reviewer wave recorded times must be strictly increasing")
        if recorded_at > expected_finalization_recorded_at:
            raise MainReviewerProvenanceError(
                "reviewer wave was recorded after finalization")
        indexed_wave = wave
        indexed_recorded_at = recorded_at
    seen_packet_dirs: set[Path] = set()
    seen_payloads: set[str] = set()
    expected_decisions: list[dict[str, Any]] = []
    prior_wave = 0
    prior_wave_recorded_at: datetime | None = None
    total_counts = {"parsed": 0, "malformed": 0, "reviewer_error": 0}
    last_worklist_raw: bytes | None = None
    expected_run_lease_path: Path | None = None
    empty_raw_sha256 = _raw_sha256(b"")
    expected_decision_raw_sha256 = empty_raw_sha256
    expected_decision_byte_count = 0
    expected_decision_tail = (-1, "genesis")
    expected_index_raw_sha256 = empty_raw_sha256
    expected_index_byte_count = 0
    for row_number, row in enumerate(wave_rows, 1):
        _require_fields(row, _WAVE_FIELDS, subject=f"reviewer index row {row_number}")
        if row.get("schema_version") != REVIEWER_WAVE_SCHEMA:
            raise MainReviewerProvenanceError("unsupported reviewer wave schema")
        exact_values = {
            "run_id": expected_run_id,
            "manifest_canonical_sha256": expected_manifest_canonical_sha256,
            "authorization_canonical_sha256": expected_authorization_canonical_sha256,
            "authorization_raw_sha256": expected_authorization_raw_sha256,
            "authorization_signature_raw_sha256": (
                expected_authorization_signature_raw_sha256),
            "capacity_plan_raw_sha256": frozen.capacity_plan_raw_sha256,
            "reviewer_model": expected_reviewer_model,
            "reviewer_reasoning_effort": expected_reviewer_reasoning_effort,
            "reviewer_concurrency": expected_reviewer_concurrency,
            "reviewer_cli_resolved_path": expected_reviewer_cli_resolved_path,
        }
        for field, expected in exact_values.items():
            if row.get(field) != expected:
                raise MainReviewerProvenanceError(
                    f"reviewer index row {row_number} {field} drifted")
        run_lease_text = _require_text(
            row.get("run_lease_path"), field="reviewer wave run_lease_path")
        run_lease_path = Path(run_lease_text)
        if not run_lease_path.is_absolute():
            raise MainReviewerProvenanceError(
                "reviewer wave run lease path must be absolute")
        run_lease_path = run_lease_path.resolve()
        if expected_run_lease_path is None:
            expected_run_lease_path = run_lease_path
        elif run_lease_path != expected_run_lease_path:
            raise MainReviewerProvenanceError(
                "reviewer waves bind different run lease paths")
        wave_recorded_at = _require_utc_datetime(
            row.get("recorded_at_utc"), field="reviewer wave recorded_at_utc")
        if (
            prior_wave_recorded_at is not None
            and wave_recorded_at <= prior_wave_recorded_at
        ):
            raise MainReviewerProvenanceError(
                "reviewer wave recorded times must be strictly increasing")
        if wave_recorded_at > expected_finalization_recorded_at:
            raise MainReviewerProvenanceError(
                "reviewer wave was recorded after finalization")
        prior_wave_recorded_at = wave_recorded_at
        wave = _require_positive_int(row.get("wave"), field="reviewer wave")
        if wave <= prior_wave or wave > max_passes:
            raise MainReviewerProvenanceError(
                "reviewer waves must be strictly increasing, unique, and within max_passes")
        prior_wave = wave
        payload_count = _require_positive_int(
            row.get("payload_count"), field="reviewer wave payload_count")
        if payload_count > frozen.wave_size:
            raise MainReviewerProvenanceError(
                "reviewer wave exceeds the capacity-plan wave size")
        packet_text = _require_text(
            row.get("packet_directory"), field="reviewer wave packet_directory")
        packet_path = Path(packet_text)
        if not packet_path.is_absolute():
            raise MainReviewerProvenanceError("reviewer packet directory must be absolute")
        if ".." in packet_path.parts or packet_path.parent.resolve() != root:
            raise MainReviewerProvenanceError(
                "reviewer packet directory is not a lexical direct child of the expected root")
        packet_dir = packet_path.resolve()
        if packet_dir.parent != root or packet_dir in seen_packet_dirs:
            raise MainReviewerProvenanceError(
                "reviewer packet directory is not a unique direct child of the expected root")
        if not packet_dir.is_dir() or packet_path.is_symlink():
            raise MainReviewerProvenanceError("reviewer packet directory is unavailable or linked")
        seen_packet_dirs.add(packet_dir)

        worklist_raw = _read_stable(
            packet_dir / "WORKLIST.json", subject="reviewer worklist snapshot")
        if _raw_sha256(worklist_raw) != _require_sha256(
            row.get("worklist_snapshot_raw_sha256"),
            field="worklist_snapshot_raw_sha256",
        ):
            raise MainReviewerProvenanceError("reviewer worklist snapshot hash drifted")
        _worklist, worklist_items = _validate_worklist(
            worklist_raw,
            frozen_prompt=frozen.prompt,
            frozen_prompt_sha256=frozen.prompt_sha256,
            subject=f"reviewer wave {wave} worklist",
        )
        if len(worklist_items) != payload_count:
            raise MainReviewerProvenanceError("reviewer wave payload count drifted")
        wave_payloads = {str(item["payload_sha256"]) for item in worklist_items}
        if seen_payloads & wave_payloads:
            raise MainReviewerProvenanceError("reviewer payload appears in more than one wave")
        seen_payloads.update(wave_payloads)

        index_raw = _read_stable(packet_dir / "INDEX.json", subject="reviewer packet index")
        if _raw_sha256(index_raw) != _require_sha256(
            row.get("packet_index_raw_sha256"), field="packet_index_raw_sha256"):
            raise MainReviewerProvenanceError("reviewer packet index hash drifted")
        index_items, packet_files = _validate_packet_index(
            index_raw, worklist_items=worklist_items, packet_dir=packet_dir)
        dispatch_guard_raw_sha256 = _require_sha256(
            row.get("dispatch_guard_raw_sha256"),
            field="dispatch_guard_raw_sha256",
        )
        dispatch_guard_path = packet_dir / codex_reviewer_batch.DISPATCH_GUARD_FILENAME
        dispatch_guard_raw = _read_stable(
            dispatch_guard_path, subject="reviewer dispatch guard")
        reviewer_recovery_context = None
        if reviewer_recovery is not None:
            historical_paths = {binding["path"] for binding in reviewer_recovery[
                "reviewer_transport_repair"]["historical_guards"]}
            if dispatch_guard_path.resolve().as_posix() in historical_paths:
                reviewer_recovery_context = phase3_main_reviewer_recovery.load_reviewer_recovery_context(
                    reviewer_recovery["recovery_path"], packet_directory=packet_dir,
                    output_path=packet_dir / "rulings.jsonl", guard_path=dispatch_guard_path,
                    guard_raw_sha256=dispatch_guard_raw_sha256, verify_artifacts=False,
                    require_recoverable=False)
        _validate_dispatch_guard_snapshot(
            dispatch_guard_raw,
            expected_raw_sha256=dispatch_guard_raw_sha256,
            expected_run_id=expected_run_id,
            expected_manifest_canonical_sha256=expected_manifest_canonical_sha256,
            expected_authorization_canonical_sha256=(
                expected_authorization_canonical_sha256),
            expected_authorization_raw_sha256=expected_authorization_raw_sha256,
            expected_authorization_signature_raw_sha256=(
                expected_authorization_signature_raw_sha256),
            expected_authorization_approved_at=expected_authorization_approved_at,
            expected_authorization_deadline=expected_authorization_deadline,
            expected_capacity_completed_at=frozen.capacity_completed_at,
            expected_capacity_expires_at=frozen.capacity_expires_at,
            expected_capacity_paths={
                "capacity_plan": Path(capacity_plan_path),
                "capacity_result": Path(capacity_result_path),
                "capacity_dispatch_history": Path(capacity_dispatch_history_path),
            },
            expected_capacity_raw_sha256s={
                "capacity_plan": frozen.capacity_plan_raw_sha256,
                "capacity_result": frozen.capacity_result_raw_sha256,
                "capacity_dispatch_history": (
                    frozen.capacity_dispatch_history_raw_sha256),
            },
            expected_reviewer_cli_resolved_path=expected_reviewer_cli_resolved_path,
            expected_cli_wrapper_raw_sha256=frozen.cli_wrapper_raw_sha256,
            expected_cli_wrapper_byte_count=frozen.cli_wrapper_byte_count,
            expected_reviewer_model=expected_reviewer_model,
            expected_reviewer_reasoning_effort=(
                expected_reviewer_reasoning_effort),
            expected_reviewer_concurrency=expected_reviewer_concurrency,
            expected_reviewer_cli_version=frozen.reviewer_cli_version,
            expected_capacity_host_identity=frozen.capacity_host_identity,
            packet_dir=packet_dir,
            output_path=packet_dir / "rulings.jsonl",
            worklist_snapshot_path=packet_dir / "WORKLIST.json",
            worklist_snapshot_raw=worklist_raw,
            packet_index_path=packet_dir / "INDEX.json",
            packet_index_raw=index_raw,
            index_items=index_items,
            **({"reviewer_recovery_context": reviewer_recovery_context}
               if reviewer_recovery_context is not None else {}),
        )
        rulings_raw = _read_stable(packet_dir / "rulings.jsonl", subject="reviewer rulings")
        if _raw_sha256(rulings_raw) != _require_sha256(
            row.get("rulings_raw_sha256"), field="rulings_raw_sha256"):
            raise MainReviewerProvenanceError("reviewer rulings hash drifted")
        (
            _rulings,
            wave_decisions,
            actual_counts,
            evidence_files,
            evidence_dirs,
        ) = _validate_rulings(
            rulings_raw,
            packet_dir=packet_dir,
            index_items=index_items,
            expected_reviewer_model=expected_reviewer_model,
            expected_reviewer_reasoning_effort=expected_reviewer_reasoning_effort,
            expected_reviewer_concurrency=expected_reviewer_concurrency,
            expected_reviewer_cli_resolved_path=expected_reviewer_cli_resolved_path,
            expected_reviewer_cli_version=frozen.reviewer_cli_version,
            expected_capacity_host_identity=frozen.capacity_host_identity,
            expected_capacity_completed_at=frozen.capacity_completed_at,
            expected_capacity_expires_at=frozen.capacity_expires_at,
            expected_authorization_deadline_utc=expected_authorization_deadline_utc,
            expected_authorization_approved_at=(
                expected_authorization_approved_at),
            expected_wave_recorded_at=wave_recorded_at,
            expected_dispatch_guard_raw_sha256=dispatch_guard_raw_sha256,
            expected_cli_wrapper_raw_sha256=(
                frozen.cli_wrapper_raw_sha256),
            expected_cli_wrapper_byte_count=frozen.cli_wrapper_byte_count,
            **({"reviewer_recovery_context": reviewer_recovery_context}
               if reviewer_recovery_context is not None else {}),
            **({"accepted_batch_runner_bindings": phase3_main_reviewer_recovery.accepted_batch_runner_bindings(
                reviewer_recovery)} if reviewer_recovery is not None else {}),
        )
        commit_counts = row.get("commit_counts")
        if not isinstance(commit_counts, Mapping) or set(commit_counts) != _COMMIT_COUNT_FIELDS:
            raise MainReviewerProvenanceError("reviewer wave commit_counts fields drifted")
        for field in _COMMIT_COUNT_FIELDS:
            if _require_non_negative_int(
                commit_counts.get(field), field=f"commit_counts.{field}"
            ) != actual_counts[field]:
                raise MainReviewerProvenanceError("reviewer wave commit_counts drifted")
            total_counts[field] += actual_counts[field]
        if sum(actual_counts.values()) != payload_count:
            raise MainReviewerProvenanceError("reviewer wave committed count drifted")
        expected_decisions.extend(wave_decisions)
        try:
            transaction = (
                phase3_main_reviewer_commit.validate_reviewer_wave_commit(
                    transaction_directory=packet_dir,
                    decision_store_path=decision_store_file,
                    reviewer_index_path=reviewer_index_file,
                    run_id=expected_run_id,
                    manifest_canonical_sha256=(
                        expected_manifest_canonical_sha256),
                    wave=wave,
                    run_lease_path=run_lease_path,
                    evidence_bindings=(
                        phase3_main_reviewer_commit
                        .evidence_bindings_from_wave_row(row)),
                )
            )
        except phase3_main_reviewer_commit.ReviewerWaveCommitError as exc:
            raise MainReviewerProvenanceError(
                f"reviewer wave {wave} commit transaction failed: {exc}") from exc

        transaction_id = _require_sha256(
            row.get("wave_commit_transaction_id"),
            field="wave_commit_transaction_id",
        )
        if transaction.wave_commit_transaction_id != transaction_id:
            raise MainReviewerProvenanceError(
                "reviewer wave transaction ID drifted")
        if transaction.commit_counts != actual_counts:
            raise MainReviewerProvenanceError(
                "reviewer wave transaction commit counts drifted")
        if transaction.reviewer_index_row != row:
            raise MainReviewerProvenanceError(
                "reviewer wave transaction index row drifted")
        wave_row_raw = wave_row_raws[row_number - 1]
        if transaction.reviewer_index.append_bytes != wave_row_raw:
            raise MainReviewerProvenanceError(
                "reviewer wave transaction does not bind the exact index row bytes")

        decision_delta = transaction.decisions
        decision_prior_tail = decision_delta.prior_tail
        decision_target_tail = decision_delta.target_tail
        if decision_prior_tail is None or decision_target_tail is None:
            raise MainReviewerProvenanceError(
                "reviewer wave transaction omits its decision chain tails")
        if (
            decision_delta.prior_raw_sha256 != expected_decision_raw_sha256
            or decision_delta.prior_byte_count != expected_decision_byte_count
            or (
                decision_prior_tail.sequence,
                decision_prior_tail.event_hash,
            ) != expected_decision_tail
        ):
            raise MainReviewerProvenanceError(
                "reviewer wave transaction decision prior state is discontinuous")
        if decision_target_tail.sequence != len(expected_decisions) - 1:
            raise MainReviewerProvenanceError(
                "reviewer wave transaction decision target sequence drifted")

        index_delta = transaction.reviewer_index
        if index_delta.prior_tail is not None or index_delta.target_tail is not None:
            raise MainReviewerProvenanceError(
                "reviewer wave transaction gives the unchained index a decision tail")
        if (
            index_delta.prior_raw_sha256 != expected_index_raw_sha256
            or index_delta.prior_byte_count != expected_index_byte_count
        ):
            raise MainReviewerProvenanceError(
                "reviewer wave transaction index prior state is discontinuous")
        expected_index_byte_count += len(wave_row_raw)
        expected_index_prefix = reviewer_index_raw[:expected_index_byte_count]
        if (
            index_delta.target_byte_count != expected_index_byte_count
            or index_delta.target_raw_sha256 != _raw_sha256(expected_index_prefix)
        ):
            raise MainReviewerProvenanceError(
                "reviewer wave transaction index target state drifted")

        expected_decision_raw_sha256 = decision_delta.target_raw_sha256
        expected_decision_byte_count = decision_delta.target_byte_count
        expected_decision_tail = (
            decision_target_tail.sequence,
            decision_target_tail.event_hash,
        )
        expected_index_raw_sha256 = index_delta.target_raw_sha256
        _validate_packet_tree(
            packet_dir,
            packet_files=packet_files,
            evidence_files=evidence_files,
            evidence_dirs=evidence_dirs,
            dispatch_guard_path=dispatch_guard_path,
        )
        last_worklist_raw = worklist_raw

    actual_root_children = {child.resolve() for child in root.iterdir()}
    if any(child.is_symlink() for child in root.iterdir()):
        raise MainReviewerProvenanceError("review packet root contains a symbolic link")
    if actual_root_children != seen_packet_dirs:
        raise MainReviewerProvenanceError("review packet root has an unindexed child")
    if seen_payloads != expected_payloads:
        raise MainReviewerProvenanceError(
            "reviewer wave payloads differ from the expected reviewed payload set")

    final_worklist_raw = _read_stable(
        Path(reviewer_worklist_path), subject="final reviewer worklist")
    if last_worklist_raw is None:
        _worklist, final_items = _validate_worklist(
            final_worklist_raw,
            frozen_prompt=frozen.prompt,
            frozen_prompt_sha256=frozen.prompt_sha256,
            subject="final reviewer worklist",
        )
        if final_items:
            raise MainReviewerProvenanceError(
                "zero-wave reviewer provenance requires an empty final worklist")
    elif final_worklist_raw != last_worklist_raw:
        raise MainReviewerProvenanceError(
            "final reviewer worklist differs from the last wave snapshot")

    final_decision_raw = _read_stable(
        decision_store_file, subject="final review decision store")
    if (
        len(final_decision_raw) != expected_decision_byte_count
        or _raw_sha256(final_decision_raw) != expected_decision_raw_sha256
        or expected_decision_tail[0] != len(expected_decisions) - 1
    ):
        raise MainReviewerProvenanceError(
            "final review decision store differs from the transaction chain target")
    if (
        len(reviewer_index_raw) != expected_index_byte_count
        or _raw_sha256(reviewer_index_raw) != expected_index_raw_sha256
    ):
        raise MainReviewerProvenanceError(
            "final reviewer index differs from the transaction chain target")

    _validate_decisions(decision_store_file, expected_decisions)
    tree_sha = review_packets_tree_canonical_sha256(root)
    if _raw_sha256(_read_stable(
        Path(capacity_result_path), subject="capacity result final snapshot",
    )) != frozen.capacity_result_raw_sha256:
        raise MainReviewerProvenanceError(
            "capacity result changed while reviewer provenance was validated")
    if _raw_sha256(_read_stable(
        Path(capacity_dispatch_history_path),
        subject="capacity dispatch history final snapshot",
    )) != frozen.capacity_dispatch_history_raw_sha256:
        raise MainReviewerProvenanceError(
            "capacity dispatch history changed while reviewer provenance was validated")
    if _read_stable(
        Path(reviewer_index_path), subject="reviewer index final snapshot",
    ) != reviewer_index_raw:
        raise MainReviewerProvenanceError(
            "reviewer index changed while reviewer provenance was validated")
    if _read_stable(
        decision_store_file, subject="review decision store final snapshot",
    ) != final_decision_raw:
        raise MainReviewerProvenanceError(
            "review decision store changed while reviewer provenance was validated")
    if _read_stable(
        Path(reviewer_worklist_path), subject="final reviewer worklist final snapshot",
    ) != final_worklist_raw:
        raise MainReviewerProvenanceError(
            "final reviewer worklist changed while reviewer provenance was validated")
    if review_packets_tree_canonical_sha256(root) != tree_sha:
        raise MainReviewerProvenanceError(
            "review packet tree changed while reviewer provenance was validated")
    return {
        "reviewer_provenance_status": REVIEWER_PROVENANCE_STATUS,
        "reviewer_wave_count": len(wave_rows),
        "reviewed_payload_count": len(seen_payloads),
        "parsed_decision_count": total_counts["parsed"],
        "malformed_decision_count": total_counts["malformed"],
        "reviewer_error_decision_count": total_counts["reviewer_error"],
        "review_packets_root": root.as_posix(),
        "review_packets_tree_canonical_sha256": tree_sha,
        "reviewer_index_raw_sha256": _raw_sha256(reviewer_index_raw),
        "reviewer_worklist_raw_sha256": _raw_sha256(final_worklist_raw),
        "reviewer_prompt_raw_sha256": frozen.reviewer_prompt_raw_sha256,
        "reviewer_failure_policy_raw_sha256": (
            frozen.reviewer_failure_policy_raw_sha256),
        "capacity_plan_raw_sha256": frozen.capacity_plan_raw_sha256,
        "capacity_result_raw_sha256": frozen.capacity_result_raw_sha256,
        "capacity_dispatch_history_raw_sha256": (
            frozen.capacity_dispatch_history_raw_sha256),
    }
