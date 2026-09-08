"""Run blinded reviews through the codex CLI, proving prompt bytes and zero tool use.

The reviewer model was substituted mid-canary under an owner deviation because the pinned
claude-fable-5 quota is unavailable until 2026-08-04. This runner preserves both controls the
subagent transport gave us, and adds one the external-chat transport could never have had:

- **prompt integrity**: the packet file is piped to ``codex exec`` as stdin, so the bytes the
  model receives are exactly the bytes on disk, and their sha256 is recorded per ruling and
  checked against the frozen ``subagent_prompt_sha256`` at commit time;
- **no tool use**: codex's ``--json`` event stream reports every shell call as a
  ``command_execution`` item. Any ruling whose stream contains one is refused rather than
  recorded, because the sandbox permits filesystem reads and a reviewer that consulted the
  world document would no longer be blind.
- **durable invocation evidence**: the exact JSON event stream, stderr, and ruling bytes are
  retained beside the packet. A canonical receipt binds those bytes to the packet, frozen
  argv, requested model and effort, local CLI wrapper, runner, host, deadline, and outcome.

Isolation per review: a fresh ephemeral session in an empty working root, with user config
and project rules disabled, so nothing about this experiment is in context but the payload.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

MODEL_DEFAULT = "gpt-5.6-sol"
EFFORT_DEFAULT = "high"
PER_REVIEW_TIMEOUT_SECONDS = 600
INVOCATION_EVIDENCE_SCHEMA = "codex_reviewer_invocation_evidence_v5"
LEGACY_INVOCATION_EVIDENCE_SCHEMA = "codex_reviewer_invocation_evidence_v4"
RULING_EVIDENCE_REFERENCE_SCHEMA = "codex_reviewer_evidence_reference_v1"
DISPATCH_GUARD_SCHEMA = "phase3_main_reviewer_dispatch_guard_v2"
DISPATCH_GUARD_EVIDENCE_SCHEMA = "codex_reviewer_dispatch_guard_evidence_v2"
DISPATCH_RESERVATION_SCHEMA = "codex_reviewer_dispatch_reservation_v1"
EVIDENCE_DIRECTORY_NAME = "reviewer_evidence"
DISPATCH_GUARD_FILENAME = "DISPATCH_GUARD.json"
DISPATCH_RESERVATION_DIRECTORY_NAME = ".reviewer_dispatch_reservations"
RECONNECT_RECOVERY_FILENAME = "reconnect_recovery.json"
RECONNECT_RECOVERY_SCHEMA = "codex_reviewer_reconnect_recovery_v1"
RECONNECT_POLICY = "numbered_body_decode_reconnect_then_exact_completed_ruling_v1"
_RECONNECT_WARNING_RE = re.compile(
    r"Reconnecting\.\.\. ([1-5])/5 \(stream disconnected before completion: "
    r"Transport error: network error: error decoding response body\)")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_EVENT_TYPES = frozenset({"error", "turn.failed"})
_ITEM_EVENT_TYPES = frozenset({"item.started", "item.updated", "item.completed"})
_NON_TOOL_ITEM_TYPES = frozenset({"agent_message", "reasoning", "user_message"})
_TOOL_ITEM_TYPES = frozenset({
    "collab_tool_call",
    "command_execution",
    "dynamic_tool_call",
    "entered_review_mode",
    "exited_review_mode",
    "file_change",
    "image_view",
    "mcp_tool_call",
    "plan",
    "todo_list",
    "web_search",
})
_EVIDENCE_REFERENCE_FIELDS = frozenset({
    "schema_version",
    "receipt_path",
    "receipt_raw_sha256",
    "receipt_byte_count",
})
_RECEIPT_FIELDS = frozenset({
    "schema_version",
    "packet",
    "invocation",
    "outcome",
    "artifacts",
    "dispatch_guard",
})
_PACKET_FIELDS = frozenset({"file", "raw_sha256", "byte_count"})
_INVOCATION_FIELDS = frozenset({
    "argv",
    "model_requested",
    "reasoning_effort_requested",
    "batch_concurrency",
    "codex_cli_argument",
    "codex_cli_resolved_path",
    "codex_cli_wrapper_raw_sha256",
    "codex_cli_wrapper_byte_count",
    "codex_cli_version",
    "working_directory",
    "output_file",
    "sandbox_mode",
    "ephemeral",
    "ignore_user_config",
    "ignore_rules",
    "json_event_stream",
    "python_executable",
    "python_implementation",
    "python_version",
    "host_identity",
    "batch_runner_path",
    "batch_runner_raw_sha256",
    "batch_runner_byte_count",
})
_INVOCATION_FIELDS_WITH_LEGACY_TRANSPORT = _INVOCATION_FIELDS | frozenset({
    "openai_provider_supports_websockets",
})
_INVOCATION_FIELDS_WITH_MODEL_PROVIDER = _INVOCATION_FIELDS | frozenset({
    "model_provider_profile",
})
_EXPECTED_TRANSPORT_UNSET = object()
_EXPECTED_MODEL_PROVIDER_UNSET = object()
_MODEL_PROVIDER_PROFILE_FIELDS = frozenset({
    "id",
    "name",
    "wire_api",
    "requires_openai_auth",
    "supports_websockets",
    "http_headers",
})
_RESERVED_MODEL_PROVIDER_IDS = frozenset({"openai", "ollama", "lmstudio"})
_MODEL_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_OUTCOME_FIELDS = frozenset({
    "authorization_deadline_utc",
    "deadline_active_before_dispatch",
    "dispatch_attempted",
    "started_at_utc",
    "completed_at_utc",
    "timed_out",
    "process_exit_code",
    "result_ok",
    "error",
    "commands",
    "event_stream_errors",
    "normalized_ruling_raw_sha256",
    "normalized_ruling_byte_count",
})
_ARTIFACT_FIELDS = frozenset({"path", "raw_sha256", "byte_count"})
_RECEIPT_ARTIFACT_NAMES = frozenset({"event_stream", "stderr", "ruling"})
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
    "file",
    "payload_sha256",
    "prompt_sha256",
    "byte_count",
})
_DISPATCH_GUARD_EVIDENCE_FIELDS = frozenset({
    "schema_version",
    "snapshot_path",
    "snapshot_raw_sha256",
    "snapshot_byte_count",
    "checked_at_utc",
    "released_at_utc",
    "authorization_approved_at_utc",
    "authorization_valid_until_utc",
    "capacity_completed_at_utc",
    "capacity_expires_at_utc",
    "reviewer_cli_version",
    "reservation",
    "verified",
    "error",
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
_DISPATCH_RESERVATION_EVIDENCE_FIELDS = frozenset({
    "path",
    "raw_sha256",
    "byte_count",
})
_PACKET_INDEX_FIELDS = frozenset({"count", "items"})
_PACKET_INDEX_ITEM_FIELDS = frozenset({
    "n",
    "file",
    "payload_sha256",
    "prompt_sha256",
})
_WORKLIST_FIELDS = frozenset({"frozen_prompt_sha256", "separator", "items"})
_WORKLIST_ITEM_FIELDS = frozenset({
    "payload_sha256",
    "query",
    "candidate_a",
    "candidate_b",
    "subagent_prompt",
    "subagent_prompt_sha256",
})


def _active_deadline(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("reviewer authorization deadline must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("reviewer authorization deadline must use UTC")
    return parsed.astimezone(timezone.utc)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _raw_sha256(raw)


def normalize_model_provider_profile(
    value: object,
) -> dict[str, object] | None:
    """Validate the exact custom OpenAI HTTP profile used by governed reviewers."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _MODEL_PROVIDER_PROFILE_FIELDS:
        raise ValueError("model provider profile fields drifted")
    provider_id = value.get("id")
    if (
        not isinstance(provider_id, str)
        or _MODEL_PROVIDER_ID_RE.fullmatch(provider_id) is None
        or provider_id in _RESERVED_MODEL_PROVIDER_IDS
    ):
        raise ValueError("model provider profile ID is invalid or reserved")
    if value.get("name") != "OpenAI":
        raise ValueError("model provider profile name must be OpenAI")
    if value.get("wire_api") != "responses":
        raise ValueError("model provider profile wire API must be responses")
    if value.get("requires_openai_auth") is not True:
        raise ValueError("model provider profile must require OpenAI authentication")
    if value.get("supports_websockets") is not False:
        raise ValueError("model provider profile must disable WebSocket transport")
    headers = value.get("http_headers")
    if not isinstance(headers, Mapping) or set(headers) != {"version"}:
        raise ValueError("model provider profile HTTP headers drifted")
    version = headers.get("version")
    if not isinstance(version, str) or re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        raise ValueError("model provider profile version header is invalid")
    return {
        "id": provider_id,
        "name": "OpenAI",
        "wire_api": "responses",
        "requires_openai_auth": True,
        "supports_websockets": False,
        "http_headers": {"version": version},
    }


def _model_provider_argv(profile: Mapping[str, object]) -> list[str]:
    normalized = normalize_model_provider_profile(profile)
    if normalized is None:  # pragma: no cover - guarded by the Mapping input
        raise ValueError("model provider profile is required")
    provider_id = str(normalized["id"])
    prefix = f"model_providers.{provider_id}"
    version = cast(Mapping[str, object], normalized["http_headers"])["version"]
    return [
        "-c", f"model_provider={json.dumps(provider_id)}",
        "-c", f"{prefix}.name={json.dumps(normalized['name'])}",
        "-c", f"{prefix}.wire_api={json.dumps(normalized['wire_api'])}",
        "-c", f"{prefix}.requires_openai_auth=true",
        "-c", f"{prefix}.supports_websockets=false",
        "-c", f"{prefix}.http_headers.version={json.dumps(version)}",
    ]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _strict_json_object(raw: bytes, *, subject: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {item!r}")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{subject} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{subject} must be a JSON object")
    return value


def _stable_bound_artifact(
    binding: object,
    *,
    field: str,
) -> tuple[Path, bytes]:
    if not isinstance(binding, Mapping) or set(binding) != _DISPATCH_GUARD_BINDING_FIELDS:
        raise ValueError(f"reviewer dispatch guard {field} binding fields drifted")
    raw_path = binding.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"reviewer dispatch guard {field} path must be nonempty text")
    supplied = Path(raw_path)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValueError(f"reviewer dispatch guard {field} path must be absolute and unlinked")
    resolved = supplied.resolve()
    if resolved.as_posix() != raw_path:
        raise ValueError(f"reviewer dispatch guard {field} path is not resolved")
    first = resolved.read_bytes()
    second = resolved.read_bytes()
    if first != second:
        raise ValueError(f"reviewer dispatch guard {field} changed while checked")
    expected_sha = _strict_sha256(
        binding.get("raw_sha256"), field=f"dispatch_guard.{field}.raw_sha256")
    expected_size = _strict_non_negative_int(
        binding.get("byte_count"), field=f"dispatch_guard.{field}.byte_count")
    if len(first) != expected_size or _raw_sha256(first) != expected_sha:
        raise ValueError(f"reviewer dispatch guard {field} bytes drifted")
    return resolved, first


def _host_identity() -> str:
    return (
        os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or platform.node().strip()
        or "unknown-host"
    )


def _reviewer_cli_version(codex: str) -> str:
    try:
        completed = subprocess.run(
            [codex, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"reviewer CLI version probe failed: {type(exc).__name__}: {exc}") from exc
    version = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0 or not version:
        raise ValueError("reviewer CLI version probe did not return a version")
    return version


def _strict_guard_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"reviewer dispatch guard {field} must be nonempty text")
    supplied = Path(value)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise ValueError(f"reviewer dispatch guard {field} must be absolute and unlinked")
    resolved = supplied.resolve()
    if resolved.as_posix() != value:
        raise ValueError(f"reviewer dispatch guard {field} is not resolved")
    return resolved


def _payload_sha256(query: str, candidate_a: str, candidate_b: str) -> str:
    canonical = json.dumps(
        {"query": query, "candidate_a": candidate_a, "candidate_b": candidate_b},
        ensure_ascii=False,
        sort_keys=True,
    )
    return _raw_sha256(canonical.encode("utf-8"))


def _validate_guard_packet_catalog(
    guard: Mapping[str, object],
    reopened: Mapping[str, tuple[Path, bytes]],
    *,
    packet_directory: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    expected_paths = {
        "packet_index": (packet_directory / "INDEX.json").resolve(),
        "worklist_snapshot": (packet_directory / "WORKLIST.json").resolve(),
    }
    for name, expected in expected_paths.items():
        if reopened[name][0] != expected:
            raise ValueError(f"reviewer dispatch guard {name} path drifted")

    runner_path, runner_sha, runner_size = _batch_runner_identity()
    if (
        reopened["batch_runner"][0].as_posix() != runner_path
        or _raw_sha256(reopened["batch_runner"][1]) != runner_sha
        or len(reopened["batch_runner"][1]) != runner_size
    ):
        raise ValueError("reviewer dispatch guard batch runner drifted")

    index = _strict_json_object(
        reopened["packet_index"][1], subject="bound reviewer packet index")
    if set(index) != _PACKET_INDEX_FIELDS:
        raise ValueError("reviewer dispatch guard packet index fields drifted")
    index_items = index.get("items")
    if not isinstance(index_items, list):
        raise ValueError("reviewer dispatch guard packet index items must be a list")
    count = _strict_non_negative_int(index.get("count"), field="packet_index.count")
    if count != len(index_items):
        raise ValueError("reviewer dispatch guard packet index count drifted")

    worklist = _strict_json_object(
        reopened["worklist_snapshot"][1], subject="bound reviewer worklist snapshot")
    if set(worklist) != _WORKLIST_FIELDS:
        raise ValueError("reviewer dispatch guard worklist fields drifted")
    _strict_sha256(
        worklist.get("frozen_prompt_sha256"), field="worklist.frozen_prompt_sha256")
    if not isinstance(worklist.get("separator"), str) or not worklist["separator"]:
        raise ValueError("reviewer dispatch guard worklist separator must be nonempty text")
    worklist_items = worklist.get("items")
    if not isinstance(worklist_items, list) or len(worklist_items) != count:
        raise ValueError("reviewer dispatch guard worklist count drifted")

    packet_bindings = guard.get("packet_bindings")
    if not isinstance(packet_bindings, list) or len(packet_bindings) != count:
        raise ValueError("reviewer dispatch guard packet bindings count drifted")
    normalized_index: list[dict[str, object]] = []
    normalized_worklist: list[dict[str, object]] = []
    seen_payloads: set[str] = set()
    for position, (index_item, worklist_item, packet_binding) in enumerate(
        zip(index_items, worklist_items, packet_bindings), 1
    ):
        if not isinstance(index_item, Mapping) or set(index_item) != _PACKET_INDEX_ITEM_FIELDS:
            raise ValueError(
                f"reviewer dispatch guard packet index item {position} fields drifted")
        if not isinstance(worklist_item, Mapping) or set(worklist_item) != _WORKLIST_ITEM_FIELDS:
            raise ValueError(
                f"reviewer dispatch guard worklist item {position} fields drifted")
        if (
            not isinstance(packet_binding, Mapping)
            or set(packet_binding) != _DISPATCH_GUARD_PACKET_BINDING_FIELDS
        ):
            raise ValueError(
                f"reviewer dispatch guard packet binding {position} fields drifted")
        if index_item.get("n") != position or isinstance(index_item.get("n"), bool):
            raise ValueError("reviewer dispatch guard packet numbering drifted")
        payload = _strict_sha256(
            worklist_item.get("payload_sha256"),
            field=f"worklist.items[{position}].payload_sha256",
        )
        if payload in seen_payloads:
            raise ValueError("reviewer dispatch guard worklist repeats a payload")
        seen_payloads.add(payload)
        string_values: dict[str, str] = {}
        for field in ("query", "candidate_a", "candidate_b", "subagent_prompt"):
            value = worklist_item.get(field)
            if not isinstance(value, str):
                raise ValueError(
                    f"reviewer dispatch guard worklist item {position}.{field} must be text")
            string_values[field] = value
        if _payload_sha256(
            string_values["query"],
            string_values["candidate_a"],
            string_values["candidate_b"],
        ) != payload:
            raise ValueError("reviewer dispatch guard worklist payload hash drifted")
        prompt_raw = string_values["subagent_prompt"].encode("utf-8")
        prompt_sha = _strict_sha256(
            worklist_item.get("subagent_prompt_sha256"),
            field=f"worklist.items[{position}].subagent_prompt_sha256",
        )
        if _raw_sha256(prompt_raw) != prompt_sha:
            raise ValueError("reviewer dispatch guard worklist prompt hash drifted")
        expected_file = f"{position:05d}_{payload[:12]}.txt"
        if (
            index_item.get("file") != expected_file
            or index_item.get("payload_sha256") != payload
            or index_item.get("prompt_sha256") != prompt_sha
        ):
            raise ValueError("reviewer dispatch guard packet index semantics drifted")
        expected_binding = {
            "file": expected_file,
            "payload_sha256": payload,
            "prompt_sha256": prompt_sha,
            "byte_count": len(prompt_raw),
        }
        if dict(packet_binding) != expected_binding:
            raise ValueError("reviewer dispatch guard packet binding semantics drifted")
        normalized_index.append(dict(index_item))
        normalized_worklist.append(dict(worklist_item))
    return normalized_index, normalized_worklist


def _evaluate_dispatch_guard_snapshot(
    snapshot_path: Path,
    expected_snapshot_raw_sha256: str,
    *,
    codex: str,
    model: str | None = None,
    effort: str | None = None,
    concurrency: int | None = None,
    openai_provider_supports_websockets: bool | None = None,
    model_provider_profile: Mapping[str, object] | None = None,
    packet_directory: Path | None = None,
    output_path: Path | None = None,
    selected_packet: Path | None = None,
    checked_at: datetime | None = None,
    reviewer_recovery_context: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object] | None, bytes | None]:
    normalized_model_provider = normalize_model_provider_profile(model_provider_profile)
    if (
        openai_provider_supports_websockets is not None
        and normalized_model_provider is not None
    ):
        raise ValueError(
            "legacy OpenAI transport override and model provider profile are mutually exclusive"
        )
    expected_sha = _strict_sha256(
        expected_snapshot_raw_sha256,
        field="expected_dispatch_guard_snapshot_raw_sha256",
    )
    supplied = Path(snapshot_path)
    evidence: dict[str, object] = {
        "schema_version": DISPATCH_GUARD_EVIDENCE_SCHEMA,
        "snapshot_path": DISPATCH_GUARD_FILENAME,
        "snapshot_raw_sha256": expected_sha,
        "snapshot_byte_count": None,
        "checked_at_utc": None,
        "released_at_utc": None,
        "authorization_approved_at_utc": None,
        "authorization_valid_until_utc": None,
        "capacity_completed_at_utc": None,
        "capacity_expires_at_utc": None,
        "reviewer_cli_version": None,
        "reservation": None,
        "verified": False,
        "error": None,
    }
    guard: dict[str, object] | None = None
    verified_prompt_raw: bytes | None = None
    try:
        if supplied.name != DISPATCH_GUARD_FILENAME or supplied.is_symlink():
            raise ValueError("reviewer dispatch guard snapshot path drifted")
        first = supplied.read_bytes()
        second = supplied.read_bytes()
        if first != second:
            raise ValueError("reviewer dispatch guard snapshot changed while checked")
        evidence["snapshot_byte_count"] = len(first)
        if _raw_sha256(first) != expected_sha:
            raise ValueError("reviewer dispatch guard snapshot hash drifted")
        guard = _strict_json_object(first, subject="reviewer dispatch guard snapshot")
        if set(guard) != _DISPATCH_GUARD_FIELDS:
            raise ValueError("reviewer dispatch guard snapshot fields drifted")
        if guard.get("schema_version") != DISPATCH_GUARD_SCHEMA:
            raise ValueError("unsupported reviewer dispatch guard schema")
        for field in (
            "run_id",
            "authorization_approved_at_utc",
            "authorization_valid_until_utc",
            "capacity_completed_at_utc",
            "capacity_expires_at_utc",
            "reviewer_model",
            "reviewer_reasoning_effort",
            "reviewer_cli_version",
            "capacity_host_identity",
        ):
            if not isinstance(guard.get(field), str) or not guard[field]:
                raise ValueError(f"reviewer dispatch guard {field} must be nonempty text")
        guard_concurrency = _strict_positive_int(
            guard.get("reviewer_concurrency"), field="dispatch_guard.reviewer_concurrency")
        for field in ("manifest_canonical_sha256", "authorization_canonical_sha256"):
            _strict_sha256(guard.get(field), field=f"dispatch_guard.{field}")
        guard_packet_directory = _strict_guard_path(
            guard.get("packet_directory"), field="packet_directory")
        guard_output_path = _strict_guard_path(guard.get("output_path"), field="output_path")
        if (
            not guard_packet_directory.is_dir()
            or guard_packet_directory.is_symlink()
            or not guard_output_path.is_file()
            or guard_output_path.is_symlink()
            or guard_output_path.parent != guard_packet_directory
        ):
            raise ValueError(
                "reviewer dispatch guard packet directory or output path is unavailable")
        if packet_directory is not None and (
            packet_directory.is_symlink()
            or packet_directory.resolve() != guard_packet_directory
        ):
            raise ValueError("reviewer dispatch guard packet directory differs from runtime")
        if output_path is not None and (
            output_path.is_symlink()
            or output_path.resolve() != guard_output_path
        ):
            raise ValueError("reviewer dispatch guard output path differs from runtime")
        if model is not None and model != guard["reviewer_model"]:
            raise ValueError("reviewer dispatch guard model differs from runtime")
        if effort is not None and effort != guard["reviewer_reasoning_effort"]:
            raise ValueError("reviewer dispatch guard effort differs from runtime")
        if concurrency is not None and _strict_positive_int(
            concurrency, field="runtime.reviewer_concurrency"
        ) != guard_concurrency:
            raise ValueError("reviewer dispatch guard concurrency differs from runtime")
        if guard["capacity_host_identity"] != _host_identity():
            raise ValueError("reviewer dispatch guard capacity host differs from runtime")

        bindings = guard.get("artifact_bindings")
        if not isinstance(bindings, Mapping) or set(bindings) != _DISPATCH_GUARD_ARTIFACT_NAMES:
            raise ValueError("reviewer dispatch guard artifact bindings drifted")
        active_bindings = dict(bindings)
        if reviewer_recovery_context is not None:
            from rejudge.phase3_main_reviewer_recovery import load_reviewer_recovery_context
            verified_recovery = load_reviewer_recovery_context(
                str(reviewer_recovery_context["recovery_path"]),
                packet_directory=guard_packet_directory, output_path=guard_output_path,
                guard_path=supplied, guard_raw_sha256=expected_sha,
                verify_artifacts=False,
                require_recoverable=reviewer_recovery_context.get("require_recoverable", True))
            if (verified_recovery["recovery_manifest_sha256"]
                    != reviewer_recovery_context.get("recovery_manifest_sha256")):
                raise ValueError("reviewer recovery authority changed before release")
            if bindings["batch_runner"] not in verified_recovery[
                    "accepted_batch_runner_bindings"]:
                raise ValueError("reviewer recovery differs from original guarded code")
            active_bindings["batch_runner"] = verified_recovery["replacement_batch_runner"]
        reopened = {
            name: _stable_bound_artifact(active_bindings[name], field=name)
            for name in _DISPATCH_GUARD_ARTIFACT_NAMES
        }
        authorization = _strict_json_object(
            reopened["authorization"][1], subject="bound reviewer authorization")
        if _canonical_sha256(authorization) != guard["authorization_canonical_sha256"]:
            raise ValueError("reviewer dispatch guard authorization semantics drifted")
        approved_at = _active_deadline(str(guard["authorization_approved_at_utc"]))
        valid_until = _active_deadline(str(guard["authorization_valid_until_utc"]))
        if (
            _active_deadline(str(authorization.get("approved_at_utc"))) != approved_at
            or _active_deadline(str(authorization.get("valid_until_utc"))) != valid_until
        ):
            raise ValueError("reviewer dispatch guard authorization window drifted")
        capacity_plan = _strict_json_object(
            reopened["capacity_plan"][1], subject="bound reviewer capacity plan")
        capacity_result = _strict_json_object(
            reopened["capacity_result"][1], subject="bound reviewer capacity result")
        reviewer_configuration = capacity_plan.get("reviewer_configuration")
        if not isinstance(reviewer_configuration, Mapping):
            raise ValueError("reviewer dispatch guard capacity configuration is missing")
        expected_configuration = {
            "model": guard["reviewer_model"],
            "reasoning_effort": guard["reviewer_reasoning_effort"],
            "concurrency": guard_concurrency,
        }
        for field, expected in expected_configuration.items():
            if reviewer_configuration.get(field) != expected:
                raise ValueError(
                    f"reviewer dispatch guard capacity configuration {field} drifted")
        if reviewer_configuration.get(
            "openai_provider_supports_websockets"
        ) != openai_provider_supports_websockets:
            raise ValueError(
                "reviewer dispatch guard capacity configuration transport drifted"
            )
        if reviewer_configuration.get("model_provider_profile") != normalized_model_provider:
            raise ValueError(
                "reviewer dispatch guard capacity model provider profile drifted"
            )
        result_configuration = capacity_result.get("reviewer_configuration")
        if result_configuration != reviewer_configuration:
            raise ValueError("reviewer dispatch guard measured capacity configuration drifted")
        measurement_environment = capacity_result.get("measurement_environment")
        if not isinstance(measurement_environment, Mapping):
            raise ValueError("reviewer dispatch guard capacity measurement environment is missing")
        capacity_completed = _active_deadline(str(capacity_result.get("completed_at_utc")))
        validity = capacity_plan.get("validity")
        if not isinstance(validity, Mapping):
            raise ValueError("reviewer dispatch guard capacity validity is missing")
        valid_hours = validity.get("valid_for_hours")
        if isinstance(valid_hours, bool) or not isinstance(valid_hours, int) or valid_hours <= 0:
            raise ValueError("reviewer dispatch guard capacity validity hours drifted")
        capacity_expires = capacity_completed + timedelta(hours=valid_hours)
        if (
            _active_deadline(str(guard["capacity_completed_at_utc"])) != capacity_completed
            or _active_deadline(str(guard["capacity_expires_at_utc"])) != capacity_expires
        ):
            raise ValueError("reviewer dispatch guard capacity window drifted")

        cli_path, cli_sha, cli_size = _executable_identity(codex)
        cli_binding = bindings["reviewer_cli_wrapper"]
        if (
            cli_path != cli_binding.get("path")
            or cli_sha != cli_binding.get("raw_sha256")
            or cli_size != cli_binding.get("byte_count")
        ):
            raise ValueError("reviewer dispatch guard CLI wrapper drifted")
        expected_cli_binding = {
            "reviewer_cli_resolved_path": cli_path,
            "reviewer_cli_wrapper_raw_sha256": cli_sha,
            "reviewer_cli_wrapper_byte_count": cli_size,
        }
        for field, expected in expected_cli_binding.items():
            if reviewer_configuration.get(field) != expected:
                raise ValueError(
                    f"reviewer dispatch guard capacity configuration {field} drifted")
            if measurement_environment.get(field) != expected:
                raise ValueError(
                    f"reviewer dispatch guard capacity environment {field} drifted")
        cli_version = _reviewer_cli_version(codex)
        evidence["reviewer_cli_version"] = cli_version
        if cli_version != guard["reviewer_cli_version"]:
            raise ValueError("reviewer dispatch guard CLI version differs from runtime")
        if measurement_environment.get("reviewer_cli_version") != cli_version:
            raise ValueError("reviewer dispatch guard measured CLI version drifted")
        if measurement_environment.get("host_identity") != guard["capacity_host_identity"]:
            raise ValueError("reviewer dispatch guard measured capacity host drifted")

        index_items, worklist_items = _validate_guard_packet_catalog(
            guard, reopened, packet_directory=guard_packet_directory)
        if selected_packet is not None:
            selected_supplied = Path(selected_packet)
            selected = selected_supplied.resolve()
            try:
                selected.relative_to(guard_packet_directory)
            except ValueError as exc:
                raise ValueError("selected reviewer packet escapes the guarded directory") from exc
            matching = [
                (index_item, worklist_item, packet_binding)
                for index_item, worklist_item, packet_binding in zip(
                    index_items, worklist_items, cast(list[object], guard["packet_bindings"]))
                if selected == (guard_packet_directory / str(index_item["file"])).resolve()
            ]
            if len(matching) != 1 or selected_supplied.is_symlink():
                raise ValueError("selected reviewer packet is not uniquely guard-bound")
            index_item, worklist_item, packet_binding = matching[0]
            first_prompt = selected.read_bytes()
            second_prompt = selected.read_bytes()
            if first_prompt != second_prompt:
                raise ValueError("selected reviewer packet changed while checked")
            if (
                first_prompt != str(worklist_item["subagent_prompt"]).encode("utf-8")
                or len(first_prompt) != packet_binding["byte_count"]
                or _raw_sha256(first_prompt) != index_item["prompt_sha256"]
            ):
                raise ValueError("selected reviewer packet bytes drifted")
            verified_prompt_raw = first_prompt

        observed_at = checked_at or _utc_now()
        if observed_at.tzinfo is None or observed_at.utcoffset() != timezone.utc.utcoffset(
            observed_at
        ):
            raise ValueError("reviewer dispatch guard check timestamp must use UTC")
        observed_at = observed_at.astimezone(timezone.utc)
        evidence.update({
            "checked_at_utc": observed_at.isoformat(),
            "authorization_approved_at_utc": approved_at.isoformat(),
            "authorization_valid_until_utc": valid_until.isoformat(),
            "capacity_completed_at_utc": capacity_completed.isoformat(),
            "capacity_expires_at_utc": capacity_expires.isoformat(),
        })
        if not approved_at <= observed_at <= valid_until:
            raise ValueError("signed reviewer authorization is inactive at dispatch")
        if not capacity_completed <= observed_at <= capacity_expires:
            raise ValueError("reviewer capacity evidence is inactive at dispatch")
        evidence["verified"] = True
        return evidence, guard, verified_prompt_raw
    except (OSError, TypeError, ValueError) as exc:
        if evidence["checked_at_utc"] is None:
            observed_at = checked_at or _utc_now()
            evidence["checked_at_utc"] = observed_at.astimezone(timezone.utc).isoformat()
        evidence["error"] = str(exc)
        return evidence, guard, None


def evaluate_dispatch_guard_snapshot(
    snapshot_path: Path,
    expected_snapshot_raw_sha256: str,
    *,
    codex: str,
    model: str | None = None,
    effort: str | None = None,
    concurrency: int | None = None,
    openai_provider_supports_websockets: bool | None = None,
    model_provider_profile: Mapping[str, object] | None = None,
    packet_directory: Path | None = None,
    output_path: Path | None = None,
    checked_at: datetime | None = None,
    reviewer_recovery_context: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Reopen all guard inputs and return durable per-invocation evidence."""
    evidence, guard, _prompt_raw = _evaluate_dispatch_guard_snapshot(
        snapshot_path,
        expected_snapshot_raw_sha256,
        codex=codex,
        model=model,
        effort=effort,
        concurrency=concurrency,
        openai_provider_supports_websockets=(
            openai_provider_supports_websockets
        ),
        model_provider_profile=model_provider_profile,
        packet_directory=packet_directory,
        output_path=output_path,
        checked_at=checked_at,
        reviewer_recovery_context=reviewer_recovery_context,
    )
    return evidence, guard


def _as_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"subprocess output must be bytes or text, not {type(value).__name__}")


def _durable_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _reserve_guarded_dispatch(
    *,
    packet: Path,
    prompt_bytes: bytes,
    guard: Mapping[str, object],
    guard_raw_sha256: str,
    checked_at_utc: str,
) -> dict[str, object]:
    packet_bindings = cast(list[Mapping[str, object]], guard["packet_bindings"])
    matches = [binding for binding in packet_bindings if binding.get("file") == packet.name]
    if len(matches) != 1:
        raise ValueError("selected reviewer packet lacks one exact reservation binding")
    packet_binding = matches[0]
    payload_sha = _strict_sha256(
        packet_binding.get("payload_sha256"), field="packet_binding.payload_sha256")
    prompt_sha = _strict_sha256(
        packet_binding.get("prompt_sha256"), field="packet_binding.prompt_sha256")
    if _raw_sha256(prompt_bytes) != prompt_sha:
        raise ValueError("selected reviewer packet changed before reservation")
    reservation_path = (
        packet.parent
        / DISPATCH_RESERVATION_DIRECTORY_NAME
        / f"{guard_raw_sha256}_{payload_sha}.json"
    )
    reservation = {
        "schema_version": DISPATCH_RESERVATION_SCHEMA,
        "guard_raw_sha256": guard_raw_sha256,
        "payload_sha256": payload_sha,
        "prompt_sha256": prompt_sha,
        "packet_file": packet.name,
        "packet_directory": str(guard["packet_directory"]),
        "output_path": str(guard["output_path"]),
        "reviewer_model": str(guard["reviewer_model"]),
        "reviewer_reasoning_effort": str(guard["reviewer_reasoning_effort"]),
        "reviewer_concurrency": int(guard["reviewer_concurrency"]),
        "reviewer_cli_version": str(guard["reviewer_cli_version"]),
        "capacity_host_identity": str(guard["capacity_host_identity"]),
        "reserved_at_utc": checked_at_utc,
    }
    raw = (
        json.dumps(
            reservation,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            indent=1,
        )
        + "\n"
    ).encode("utf-8")
    try:
        _durable_write_bytes(reservation_path, raw)
    except FileExistsError as exc:
        raise ValueError(
            "reviewer dispatch reservation already exists for this guard and payload"
        ) from exc
    if reservation_path.read_bytes() != raw:
        raise ValueError("reviewer dispatch reservation changed during durable write")
    return {
        "path": reservation_path.relative_to(packet.parent).as_posix(),
        "raw_sha256": _raw_sha256(raw),
        "byte_count": len(raw),
    }


def _artifact_binding(path: Path, raw: bytes, *, relative_to: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "raw_sha256": _raw_sha256(raw),
        "byte_count": len(raw),
    }


def _executable_identity(codex: str) -> tuple[str | None, str | None, int | None]:
    candidate = shutil.which(codex)
    if candidate is None:
        explicit = Path(codex)
        if explicit.is_file():
            candidate = str(explicit)
    if candidate is None:
        return None, None, None
    resolved = Path(candidate).resolve()
    try:
        raw = resolved.read_bytes()
    except OSError:
        return None, None, None
    return resolved.as_posix(), _raw_sha256(raw), len(raw)


def _batch_runner_identity() -> tuple[str, str, int]:
    path = Path(__file__).resolve()
    raw = path.read_bytes()
    return path.as_posix(), _raw_sha256(raw), len(raw)


def _evidence_directory(packet: Path) -> Path:
    return packet.parent / EVIDENCE_DIRECTORY_NAME / f"{packet.name}.evidence"


def _persist_invocation_evidence(
    *,
    packet: Path,
    prompt_bytes: bytes,
    invocation: Mapping[str, object],
    outcome: Mapping[str, object],
    event_stream_raw: bytes,
    stderr_raw: bytes,
    ruling_raw: bytes,
    dispatch_guard: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Write one immutable evidence bundle and return its ruling-row reference."""
    evidence_dir = _evidence_directory(packet)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    event_path = evidence_dir / "codex_events.jsonl"
    stderr_path = evidence_dir / "codex_stderr.bin"
    ruling_path = evidence_dir / "ruling.txt"
    receipt_path = evidence_dir / "invocation_receipt.json"
    _durable_write_bytes(event_path, event_stream_raw)
    _durable_write_bytes(stderr_path, stderr_raw)
    _durable_write_bytes(ruling_path, ruling_raw)

    root = packet.parent.resolve()
    receipt_schema = (
        INVOCATION_EVIDENCE_SCHEMA
        if "model_provider_profile" in invocation
        else LEGACY_INVOCATION_EVIDENCE_SCHEMA
    )
    receipt = {
        "schema_version": receipt_schema,
        "packet": {
            "file": packet.name,
            "raw_sha256": _raw_sha256(prompt_bytes),
            "byte_count": len(prompt_bytes),
        },
        "invocation": dict(invocation),
        "outcome": dict(outcome),
        "dispatch_guard": (
            dict(dispatch_guard) if dispatch_guard is not None else None),
        "artifacts": {
            "event_stream": _artifact_binding(
                event_path.resolve(), event_stream_raw, relative_to=root),
            "stderr": _artifact_binding(
                stderr_path.resolve(), stderr_raw, relative_to=root),
            "ruling": _artifact_binding(
                ruling_path.resolve(), ruling_raw, relative_to=root),
        },
    }
    receipt_raw = (
        json.dumps(
            receipt,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=1,
        )
        + "\n"
    ).encode("utf-8")
    _durable_write_bytes(receipt_path, receipt_raw)
    if (
        event_path.read_bytes() != event_stream_raw
        or stderr_path.read_bytes() != stderr_raw
        or ruling_path.read_bytes() != ruling_raw
        or receipt_path.read_bytes() != receipt_raw
    ):
        raise RuntimeError("reviewer invocation evidence changed during durable write")
    return {
        "schema_version": RULING_EVIDENCE_REFERENCE_SCHEMA,
        "receipt_path": receipt_path.resolve().relative_to(root).as_posix(),
        "receipt_raw_sha256": _raw_sha256(receipt_raw),
        "receipt_byte_count": len(receipt_raw),
    }


def _strict_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _strict_non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _strict_positive_int(value: object, *, field: str) -> int:
    parsed = _strict_non_negative_int(value, field=field)
    if parsed == 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _strict_relative_artifact(root: Path, binding: Any, *, field: str) -> tuple[Path, bytes]:
    if not isinstance(binding, Mapping) or set(binding) != _ARTIFACT_FIELDS:
        raise ValueError(f"{field} fields drifted")
    typed = cast(Mapping[str, Any], binding)
    path_text = typed.get("path")
    if not isinstance(path_text, str) or not path_text:
        raise ValueError(f"{field}.path must be nonempty text")
    relative = Path(path_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{field}.path must remain under the packet directory")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field}.path escapes the packet directory") from exc
    raw = path.read_bytes()
    expected_bytes = _strict_non_negative_int(
        typed.get("byte_count"), field=f"{field}.byte_count")
    expected_sha = _strict_sha256(typed.get("raw_sha256"), field=f"{field}.raw_sha256")
    if len(raw) != expected_bytes or _raw_sha256(raw) != expected_sha:
        raise ValueError(f"{field} bytes differ from the receipt")
    return path, raw


def _inspect_json_event_stream(
    raw: bytes, *, allow_reconnect_warnings: bool = True,
) -> tuple[list[str], list[str]]:
    """Return detected tool uses and structural errors from one Codex JSONL stream."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], ["stdout is not valid UTF-8"]
    commands: list[str] = []
    errors: list[str] = []
    lifecycle_state = "await_thread"
    completed_item_seen = False
    completed_agent_message_seen = False
    thread_ids: set[str] = set()
    turn_ids: set[str] = set()
    item_lifecycles: dict[str, str] = {}
    item_types: dict[str, str] = {}
    last_reconnect_attempt = 0

    def reject_non_finite(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value!r}")

    def contains_non_finite(value: object) -> bool:
        if isinstance(value, float):
            return not math.isfinite(value)
        if isinstance(value, Mapping):
            return any(contains_non_finite(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_non_finite(item) for item in value)
        return False

    def record_associations(
        value: Mapping[str, object], *, line_number: int, subject: str,
    ) -> None:
        for field, observed, label in (
            ("thread_id", thread_ids, "thread"),
            ("turn_id", turn_ids, "turn"),
        ):
            if field not in value:
                continue
            identifier = value[field]
            if not isinstance(identifier, str) or not identifier:
                errors.append(
                    f"line {line_number} {subject}.{field} is not a nonempty string")
                continue
            if observed and identifier not in observed:
                errors.append(
                    f"line {line_number} crosses {label} association from "
                    f"{sorted(observed)!r} to {identifier!r}")
            observed.add(identifier)

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            errors.append(f"line {line_number} is blank")
            continue
        try:
            event = json.loads(
                line,
                object_pairs_hook=_unique_json_object,
                parse_constant=reject_non_finite,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"line {line_number} is not unique-key JSON: {exc}")
            continue
        if contains_non_finite(event):
            errors.append(f"line {line_number} contains a non-finite JSON number")
            continue
        if not isinstance(event, dict):
            errors.append(f"line {line_number} is not a JSON object")
            continue
        record_associations(event, line_number=line_number, subject="event")
        event_type = event.get("type")
        if not isinstance(event_type, str):
            errors.append(f"line {line_number} has no string event type")
            continue
        if event_type in _FAILURE_EVENT_TYPES:
            warning = _reconnect_warning_attempt(event) if allow_reconnect_warnings else None
            if (warning is not None and lifecycle_state == "in_turn"
                    and warning == last_reconnect_attempt + 1):
                last_reconnect_attempt = warning
                continue
            errors.append(f"line {line_number} reports {event_type}")
            lifecycle_state = "failed"
            continue
        if event_type == "thread.started":
            if "thread_id" not in event:
                errors.append(
                    f"line {line_number} thread.started has no thread_id")
            if lifecycle_state != "await_thread":
                errors.append(
                    f"line {line_number} thread.started is out of lifecycle order")
            else:
                lifecycle_state = "await_turn"
            continue
        if event_type == "turn.started":
            if lifecycle_state != "await_turn":
                errors.append(
                    f"line {line_number} turn.started is out of lifecycle order")
            else:
                lifecycle_state = "in_turn"
            continue
        if event_type == "turn.completed":
            if lifecycle_state != "in_turn":
                errors.append(
                    f"line {line_number} turn.completed is out of lifecycle order")
            else:
                lifecycle_state = "complete"
                if not completed_item_seen:
                    errors.append(
                        "event stream completed without a completed item")
                unfinished = sorted(
                    item_id for item_id, state in item_lifecycles.items()
                    if state != "completed"
                )
                if unfinished:
                    errors.append(
                        "turn.completed has unfinished item IDs "
                        f"{unfinished!r}")
            continue
        if event_type not in _ITEM_EVENT_TYPES:
            errors.append(f"line {line_number} has unknown event type {event_type!r}")
            continue
        active_turn = lifecycle_state == "in_turn"
        if not active_turn:
            errors.append(
                f"line {line_number} {event_type} is outside the active turn")
        item = event.get("item")
        if not isinstance(item, dict):
            errors.append(f"line {line_number} {event_type} has no item object")
            continue
        record_associations(item, line_number=line_number, subject="item")
        item_type = item.get("type")
        if not isinstance(item_type, str):
            errors.append(f"line {line_number} {event_type} has no string item type")
            continue
        item_id = item.get("id")
        if item_id is not None and (not isinstance(item_id, str) or not item_id):
            errors.append(
                f"line {line_number} {event_type} item.id is not a nonempty string")
            item_id = None
        if isinstance(item_id, str):
            prior_type = item_types.get(item_id)
            if prior_type is not None and prior_type != item_type:
                errors.append(
                    f"line {line_number} item {item_id!r} changes type from "
                    f"{prior_type!r} to {item_type!r}")
            else:
                item_types[item_id] = item_type
            prior_state = item_lifecycles.get(item_id)
            if event_type == "item.started":
                if prior_state is not None:
                    errors.append(
                        f"line {line_number} duplicates or reorders item.started "
                        f"for {item_id!r}")
                else:
                    item_lifecycles[item_id] = "started"
            elif event_type == "item.updated":
                if prior_state not in {"started", "updated"}:
                    errors.append(
                        f"line {line_number} item.updated has no active item "
                        f"{item_id!r}")
                else:
                    item_lifecycles[item_id] = "updated"
            else:
                if prior_state == "completed":
                    errors.append(
                        f"line {line_number} duplicates item.completed for {item_id!r}")
                else:
                    item_lifecycles[item_id] = "completed"
        if event_type == "item.completed" and active_turn:
            completed_item_seen = True
            if item_type == "agent_message":
                completed_agent_message_seen = True
        if item_type not in _NON_TOOL_ITEM_TYPES and item_type not in _TOOL_ITEM_TYPES:
            errors.append(f"line {line_number} has unknown item type {item_type!r}")
            continue
        if item_type in _NON_TOOL_ITEM_TYPES:
            continue
        command = item.get("command")
        if item_type == "command_execution":
            if not isinstance(command, str) or not command:
                errors.append(
                    f"line {line_number} command_execution has no nonempty command")
                detail = item_type
            else:
                detail = command[:200]
        else:
            detail = item_type
        if detail not in commands:
            commands.append(detail)
    if lifecycle_state == "await_thread":
        errors.append("event stream never started a thread")
    elif lifecycle_state == "await_turn":
        errors.append("event stream never started a turn")
    elif lifecycle_state == "in_turn":
        errors.append("event stream never completed its turn")
    elif lifecycle_state == "complete" and not commands and not completed_agent_message_seen:
        errors.append("event stream completed without a completed agent message")
    return commands, errors


def _reconnect_warning_attempt(event: Mapping[str, Any]) -> int | None:
    if (event.get("type") != "error"
            or set(event) - {"type", "message", "thread_id", "turn_id"}
            or not isinstance(event.get("message"), str)):
        return None
    match = _RECONNECT_WARNING_RE.fullmatch(event["message"])
    return int(match[1]) if match else None


def _inspect_reviewer_execution(event_raw: bytes, ruling_raw: bytes) -> tuple[list[str], list[str]]:
    """Accept a recovered stream only when it retained the exact completed message."""
    commands, errors = _inspect_json_event_stream(event_raw)
    if errors:
        return commands, errors
    events = [json.loads(line) for line in event_raw.decode("utf-8").splitlines()]
    if not any(_reconnect_warning_attempt(event) is not None for event in events):
        return commands, errors
    messages = [event["item"].get("text") for event in events
                if event.get("type") == "item.completed"
                and event.get("item", {}).get("type") == "agent_message"]
    try:
        ruling = ruling_raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        ruling = ""
    if (commands or len(messages) != 1 or not isinstance(messages[0], str)
            or not ruling or messages[0].strip() != ruling):
        errors.append("reconnected stream lacks one exact matching retained agent ruling")
    return commands, errors


def _reconnect_attestation(reference, receipt, event_raw, ruling_raw, recovery_sha) -> dict[str, Any]:
    return {
        "schema_version": RECONNECT_RECOVERY_SCHEMA, "policy": RECONNECT_POLICY,
        "original_receipt": dict(reference),
        "recovery_manifest_sha256": recovery_sha,
        "event_stream_raw_sha256": _raw_sha256(event_raw),
        "ruling_raw_sha256": _raw_sha256(ruling_raw),
        "original_outcome": dict(receipt["outcome"]),
        "effective_result_ok": True,
        "effective_event_stream_errors": [],
        "interpretation": "The original receipt reported failure; retained same-invocation evidence proves recovery without another dispatch.",
    }


def validate_invocation_evidence(
    packet: Path,
    reference: Mapping[str, Any],
    *,
    expected_model: str | None = None,
    expected_effort: str | None = None,
    expected_concurrency: int | None = None,
    expected_openai_provider_supports_websockets: object = (
        _EXPECTED_TRANSPORT_UNSET
    ),
    expected_model_provider_profile: object = _EXPECTED_MODEL_PROVIDER_UNSET,
    accepted_batch_runner_bindings: Sequence[Mapping[str, Any]] | None = None,
    reviewer_recovery_context: Mapping[str, Any] | None = None,
    _allow_unattested_reconnect: bool = False,
) -> dict[str, Any]:
    """Reopen and strictly validate one persisted reviewer invocation bundle."""
    if set(reference) != _EVIDENCE_REFERENCE_FIELDS:
        raise ValueError("reviewer evidence reference fields drifted")
    if reference.get("schema_version") != RULING_EVIDENCE_REFERENCE_SCHEMA:
        raise ValueError("unsupported reviewer evidence reference schema")
    root = packet.parent.resolve()
    receipt_text = reference.get("receipt_path")
    if not isinstance(receipt_text, str) or not receipt_text:
        raise ValueError("reviewer evidence receipt path must be nonempty text")
    receipt_relative = Path(receipt_text)
    if receipt_relative.is_absolute() or ".." in receipt_relative.parts:
        raise ValueError("reviewer evidence receipt must remain under the packet directory")
    expected_evidence_root = Path(EVIDENCE_DIRECTORY_NAME) / f"{packet.name}.evidence"
    if receipt_relative != expected_evidence_root / "invocation_receipt.json":
        raise ValueError("reviewer evidence receipt path differs from the frozen layout")
    receipt_path = (root / receipt_relative).resolve()
    try:
        receipt_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("reviewer evidence receipt escapes the packet directory") from exc
    receipt_raw = receipt_path.read_bytes()
    if len(receipt_raw) != _strict_non_negative_int(
        reference.get("receipt_byte_count"), field="receipt_byte_count"
    ):
        raise ValueError("reviewer evidence receipt byte count drifted")
    if _raw_sha256(receipt_raw) != _strict_sha256(
        reference.get("receipt_raw_sha256"), field="receipt_raw_sha256"
    ):
        raise ValueError("reviewer evidence receipt hash drifted")
    try:
        receipt = json.loads(
            receipt_raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("reviewer evidence receipt is not strict UTF-8 JSON") from exc
    if not isinstance(receipt, dict) or set(receipt) != _RECEIPT_FIELDS:
        raise ValueError("reviewer evidence receipt fields drifted")
    receipt_schema = receipt.get("schema_version")
    if receipt_schema not in {
        LEGACY_INVOCATION_EVIDENCE_SCHEMA,
        INVOCATION_EVIDENCE_SCHEMA,
    }:
        raise ValueError("unsupported reviewer invocation evidence schema")
    canonical_receipt_raw = (
        json.dumps(
            receipt,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=1,
        )
        + "\n"
    ).encode("utf-8")
    if receipt_raw != canonical_receipt_raw:
        raise ValueError("reviewer invocation evidence receipt is not canonically encoded")

    packet_binding = receipt.get("packet")
    if not isinstance(packet_binding, Mapping) or set(packet_binding) != _PACKET_FIELDS:
        raise ValueError("reviewer evidence packet fields drifted")
    prompt_raw = packet.read_bytes()
    if packet_binding.get("file") != packet.name:
        raise ValueError("reviewer evidence names another packet")
    if len(prompt_raw) != _strict_non_negative_int(
        packet_binding.get("byte_count"), field="packet.byte_count"
    ) or _raw_sha256(prompt_raw) != _strict_sha256(
        packet_binding.get("raw_sha256"), field="packet.raw_sha256"
    ):
        raise ValueError("reviewer evidence packet bytes drifted")

    invocation = receipt.get("invocation")
    allowed_invocation_fields = (
        {_INVOCATION_FIELDS_WITH_MODEL_PROVIDER}
        if receipt_schema == INVOCATION_EVIDENCE_SCHEMA
        else {
            _INVOCATION_FIELDS,
            _INVOCATION_FIELDS_WITH_LEGACY_TRANSPORT,
        }
    )
    if not isinstance(invocation, Mapping) or set(invocation) not in allowed_invocation_fields:
        raise ValueError("reviewer evidence invocation fields drifted")
    model = invocation.get("model_requested")
    effort = invocation.get("reasoning_effort_requested")
    if not isinstance(model, str) or not model or not isinstance(effort, str) or not effort:
        raise ValueError("reviewer evidence model and effort must be nonempty")
    if expected_model is not None and model != expected_model:
        raise ValueError("reviewer evidence model differs from the expected runtime")
    if expected_effort is not None and effort != expected_effort:
        raise ValueError("reviewer evidence effort differs from the expected runtime")
    concurrency = _strict_positive_int(
        invocation.get("batch_concurrency"), field="invocation.batch_concurrency")
    if expected_concurrency is not None and concurrency != _strict_positive_int(
        expected_concurrency, field="expected_concurrency",
    ):
        raise ValueError("reviewer evidence concurrency differs from the expected runtime")
    transport_value = invocation.get("openai_provider_supports_websockets")
    if transport_value is not None and not isinstance(transport_value, bool):
        raise ValueError(
            "reviewer evidence OpenAI WebSocket support must be boolean or null"
        )
    if (
        expected_openai_provider_supports_websockets
        is not _EXPECTED_TRANSPORT_UNSET
        and transport_value != expected_openai_provider_supports_websockets
    ):
        raise ValueError(
            "reviewer evidence transport differs from the expected runtime"
        )
    model_provider_profile = normalize_model_provider_profile(
        invocation.get("model_provider_profile")
    )
    if (
        expected_model_provider_profile is not _EXPECTED_MODEL_PROVIDER_UNSET
        and model_provider_profile
        != normalize_model_provider_profile(expected_model_provider_profile)
    ):
        raise ValueError(
            "reviewer evidence model provider differs from the expected runtime"
        )
    if transport_value is not None and model_provider_profile is not None:
        raise ValueError(
            "reviewer evidence combines legacy and custom provider transports"
        )
    for field in (
        "codex_cli_argument",
        "working_directory",
        "output_file",
        "sandbox_mode",
        "python_executable",
        "python_implementation",
        "python_version",
        "batch_runner_path",
        "batch_runner_raw_sha256",
        "host_identity",
    ):
        if not isinstance(invocation.get(field), str) or not invocation[field]:
            raise ValueError(f"reviewer evidence invocation.{field} must be nonempty text")
    if invocation.get("sandbox_mode") != "read-only" or any(
        invocation.get(field) is not True
        for field in ("ephemeral", "ignore_user_config", "ignore_rules", "json_event_stream")
    ):
        raise ValueError("reviewer evidence isolation flags drifted")
    _strict_non_negative_int(
        invocation.get("batch_runner_byte_count"),
        field="invocation.batch_runner_byte_count",
    )
    _strict_sha256(
        invocation.get("batch_runner_raw_sha256"),
        field="invocation.batch_runner_raw_sha256",
    )
    current_runner_path, current_runner_sha, current_runner_bytes = (
        _batch_runner_identity())
    accepted_runners = [{"path": current_runner_path, "raw_sha256": current_runner_sha,
                         "byte_count": current_runner_bytes}]
    if accepted_batch_runner_bindings is not None:
        accepted_runners = []
        for binding in accepted_batch_runner_bindings:
            if set(binding) != {"path", "raw_sha256", "byte_count"}:
                raise ValueError("accepted batch runner binding fields drifted")
            _strict_sha256(binding["raw_sha256"], field="accepted_runner.raw_sha256")
            _strict_positive_int(binding["byte_count"], field="accepted_runner.byte_count")
            if not isinstance(binding["path"], str) or not binding["path"]:
                raise ValueError("accepted batch runner path is invalid")
            accepted_runners.append(dict(binding))
    if {"path": invocation.get("batch_runner_path"),
        "raw_sha256": invocation.get("batch_runner_raw_sha256"),
        "byte_count": invocation.get("batch_runner_byte_count")} not in accepted_runners:
        raise ValueError(
            "reviewer evidence batch runner differs from the active verifier")
    wrapper_values = (
        invocation.get("codex_cli_resolved_path"),
        invocation.get("codex_cli_wrapper_raw_sha256"),
        invocation.get("codex_cli_wrapper_byte_count"),
    )
    if any(value is None for value in wrapper_values) and any(
        value is not None for value in wrapper_values
    ):
        raise ValueError("reviewer CLI wrapper identity must be wholly known or wholly unknown")
    if wrapper_values[0] is not None:
        if not isinstance(wrapper_values[0], str) or not wrapper_values[0]:
            raise ValueError("reviewer CLI wrapper path must be nonempty text")
        _strict_sha256(
            wrapper_values[1], field="invocation.codex_cli_wrapper_raw_sha256")
        _strict_non_negative_int(
            wrapper_values[2], field="invocation.codex_cli_wrapper_byte_count")
    cli_version = invocation.get("codex_cli_version")
    if cli_version is not None and (not isinstance(cli_version, str) or not cli_version):
        raise ValueError("reviewer CLI version must be nonempty text or null")
    argv = invocation.get("argv")
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
        raise ValueError("reviewer evidence argv must be an exact string list")
    expected_argv = [
        invocation["codex_cli_argument"],
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
    ]
    if transport_value is not None:
        expected_argv.extend([
            "-c",
            "model_providers.openai.supports_websockets="
            + str(transport_value).lower(),
        ])
    if model_provider_profile is not None:
        expected_argv.extend(_model_provider_argv(model_provider_profile))
    expected_argv.extend([
        "-C",
        invocation["working_directory"],
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-s",
        "read-only",
        "--json",
        "-o",
        invocation["output_file"],
        "-",
    ])
    if argv != expected_argv:
        raise ValueError("reviewer evidence argv differs from the frozen invocation")
    if Path(str(invocation["output_file"])) != (
        Path(str(invocation["working_directory"])) / "ruling.txt"
    ):
        raise ValueError("reviewer evidence output path differs from the isolated invocation")

    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != _RECEIPT_ARTIFACT_NAMES:
        raise ValueError("reviewer evidence artifact fields drifted")
    expected_artifact_paths = {
        "event_stream": expected_evidence_root / "codex_events.jsonl",
        "stderr": expected_evidence_root / "codex_stderr.bin",
        "ruling": expected_evidence_root / "ruling.txt",
    }
    for artifact_name, expected_path in expected_artifact_paths.items():
        binding = artifacts[artifact_name]
        if not isinstance(binding, Mapping) or binding.get("path") != expected_path.as_posix():
            raise ValueError(
                f"reviewer evidence {artifact_name} path differs from the frozen layout")
    _event_path, event_raw = _strict_relative_artifact(
        root, artifacts["event_stream"], field="artifacts.event_stream")
    _stderr_path, _stderr_raw = _strict_relative_artifact(
        root, artifacts["stderr"], field="artifacts.stderr")
    _ruling_path, ruling_raw = _strict_relative_artifact(
        root, artifacts["ruling"], field="artifacts.ruling")

    outcome = receipt.get("outcome")
    if not isinstance(outcome, Mapping) or set(outcome) != _OUTCOME_FIELDS:
        raise ValueError("reviewer evidence outcome fields drifted")
    for field in ("dispatch_attempted", "deadline_active_before_dispatch", "timed_out",
                  "result_ok"):
        if type(outcome.get(field)) is not bool:
            raise ValueError(f"reviewer evidence outcome.{field} must be boolean")
    timestamps: dict[str, datetime] = {}
    for field in ("started_at_utc", "completed_at_utc"):
        value = outcome.get(field)
        if not isinstance(value, str):
            raise ValueError(f"reviewer evidence outcome.{field} must be UTC text")
        timestamps[field] = _active_deadline(value)
    if timestamps["completed_at_utc"] < timestamps["started_at_utc"]:
        raise ValueError("reviewer evidence completion precedes dispatch start")
    guard_evidence = receipt.get("dispatch_guard")
    guard_verified: bool | None = None
    if guard_evidence is not None:
        if (
            not isinstance(guard_evidence, Mapping)
            or set(guard_evidence) != _DISPATCH_GUARD_EVIDENCE_FIELDS
        ):
            raise ValueError("reviewer dispatch guard evidence fields drifted")
        if guard_evidence.get("schema_version") != DISPATCH_GUARD_EVIDENCE_SCHEMA:
            raise ValueError("unsupported reviewer dispatch guard evidence schema")
        if guard_evidence.get("snapshot_path") != DISPATCH_GUARD_FILENAME:
            raise ValueError("reviewer dispatch guard evidence path drifted")
        guard_sha = _strict_sha256(
            guard_evidence.get("snapshot_raw_sha256"),
            field="dispatch_guard.snapshot_raw_sha256",
        )
        guard_size = guard_evidence.get("snapshot_byte_count")
        if guard_size is not None:
            _strict_non_negative_int(
                guard_size, field="dispatch_guard.snapshot_byte_count")
        checked_text = guard_evidence.get("checked_at_utc")
        if not isinstance(checked_text, str):
            raise ValueError("reviewer dispatch guard checked_at_utc must be UTC text")
        checked_at = _active_deadline(checked_text)
        retained_cli_version = guard_evidence.get("reviewer_cli_version")
        if retained_cli_version is not None and (
            not isinstance(retained_cli_version, str) or not retained_cli_version
        ):
            raise ValueError("reviewer dispatch guard CLI version must be nonempty text or null")
        guard_verified_value = guard_evidence.get("verified")
        if type(guard_verified_value) is not bool:
            raise ValueError("reviewer dispatch guard verified must be boolean")
        guard_verified = cast(bool, guard_verified_value)
        guard_error = guard_evidence.get("error")
        if guard_verified:
            reservation_evidence = guard_evidence.get("reservation")
            if (
                guard_error is not None
                or guard_size is None
                or retained_cli_version is None
                or cli_version != retained_cli_version
                or not isinstance(reservation_evidence, Mapping)
                or set(reservation_evidence) != _DISPATCH_RESERVATION_EVIDENCE_FIELDS
            ):
                raise ValueError("successful reviewer dispatch guard evidence is inconsistent")
            window: dict[str, datetime] = {}
            for field in (
                "authorization_approved_at_utc",
                "authorization_valid_until_utc",
                "capacity_completed_at_utc",
                "capacity_expires_at_utc",
            ):
                value = guard_evidence.get(field)
                if not isinstance(value, str):
                    raise ValueError(f"reviewer dispatch guard {field} must be UTC text")
                window[field] = _active_deadline(value)
            if not (
                window["authorization_approved_at_utc"]
                <= checked_at
                <= window["authorization_valid_until_utc"]
                and window["capacity_completed_at_utc"]
                <= checked_at
                <= window["capacity_expires_at_utc"]
            ):
                raise ValueError("reviewer dispatch guard check falls outside its retained window")
            released_text = guard_evidence.get("released_at_utc")
            if not isinstance(released_text, str):
                raise ValueError("reviewer dispatch release timestamp must be UTC text")
            released_at = _active_deadline(released_text)
            if not (
                checked_at <= released_at
                and window["authorization_approved_at_utc"]
                <= released_at
                <= window["authorization_valid_until_utc"]
                and window["capacity_completed_at_utc"]
                <= released_at
                <= window["capacity_expires_at_utc"]
            ):
                raise ValueError("reviewer dispatch release falls outside its retained window")
            if checked_at != timestamps["started_at_utc"]:
                raise ValueError("reviewer dispatch start differs from its guard check")
            snapshot_path = root / DISPATCH_GUARD_FILENAME
            # The recheck must reopen the guard under the same transport identity the
            # invocation recorded. The two transport fields are mutually exclusive above,
            # so exactly one of them is non-null here. Omitting them made every recheck
            # under a plan that carries a model provider profile fail with "capacity model
            # provider profile drifted" (Phase 3 main identity 34010df1, 2026-09-07).
            rechecked, _guard = evaluate_dispatch_guard_snapshot(
                snapshot_path,
                guard_sha,
                codex=str(invocation["codex_cli_argument"]),
                model=model,
                effort=effort,
                concurrency=concurrency,
                openai_provider_supports_websockets=transport_value,
                model_provider_profile=model_provider_profile,
                packet_directory=root,
                checked_at=checked_at,
                reviewer_recovery_context=reviewer_recovery_context,
            )
            expected_recheck = dict(guard_evidence)
            expected_recheck["reservation"] = None
            expected_recheck["released_at_utc"] = None
            if rechecked != expected_recheck or _guard is None:
                raise ValueError("reviewer dispatch guard evidence no longer verifies exactly")
            matching_packet_bindings = [
                binding
                for binding in cast(
                    list[Mapping[str, object]], _guard["packet_bindings"])
                if binding.get("file") == packet.name
            ]
            if len(matching_packet_bindings) != 1:
                raise ValueError("reviewer dispatch reservation packet binding drifted")
            payload_sha = _strict_sha256(
                matching_packet_bindings[0].get("payload_sha256"),
                field="reservation.payload_sha256",
            )
            expected_reservation_relative = (
                Path(DISPATCH_RESERVATION_DIRECTORY_NAME)
                / f"{guard_sha}_{payload_sha}.json"
            )
            reservation_path_text = reservation_evidence.get("path")
            if (
                not isinstance(reservation_path_text, str)
                or Path(reservation_path_text) != expected_reservation_relative
            ):
                raise ValueError("reviewer dispatch reservation path drifted")
            reservation_path = (root / expected_reservation_relative).resolve()
            try:
                reservation_path.relative_to(root)
            except ValueError as exc:
                raise ValueError("reviewer dispatch reservation escapes packet directory") from exc
            first_reservation = reservation_path.read_bytes()
            second_reservation = reservation_path.read_bytes()
            if first_reservation != second_reservation:
                raise ValueError("reviewer dispatch reservation changed while checked")
            if (
                len(first_reservation) != _strict_non_negative_int(
                    reservation_evidence.get("byte_count"),
                    field="dispatch_guard.reservation.byte_count",
                )
                or _raw_sha256(first_reservation) != _strict_sha256(
                    reservation_evidence.get("raw_sha256"),
                    field="dispatch_guard.reservation.raw_sha256",
                )
            ):
                raise ValueError("reviewer dispatch reservation bytes drifted")
            reservation = _strict_json_object(
                first_reservation, subject="reviewer dispatch reservation")
            if set(reservation) != _DISPATCH_RESERVATION_FIELDS:
                raise ValueError("reviewer dispatch reservation fields drifted")
            expected_reservation = {
                "schema_version": DISPATCH_RESERVATION_SCHEMA,
                "guard_raw_sha256": guard_sha,
                "payload_sha256": payload_sha,
                "prompt_sha256": packet_binding["raw_sha256"],
                "packet_file": packet.name,
                "packet_directory": _guard["packet_directory"],
                "output_path": _guard["output_path"],
                "reviewer_model": model,
                "reviewer_reasoning_effort": effort,
                "reviewer_concurrency": concurrency,
                "reviewer_cli_version": retained_cli_version,
                "capacity_host_identity": invocation["host_identity"],
                "reserved_at_utc": checked_at.isoformat(),
            }
            if reservation != expected_reservation:
                raise ValueError("reviewer dispatch reservation semantics drifted")
        elif not isinstance(guard_error, str) or not guard_error:
            raise ValueError("failed reviewer dispatch guard evidence lacks an error")
    deadline = outcome.get("authorization_deadline_utc")
    parsed_deadline: datetime | None = None
    if deadline is not None:
        if not isinstance(deadline, str):
            raise ValueError("reviewer evidence authorization deadline must be UTC text or null")
        parsed_deadline = _active_deadline(deadline)
    expected_deadline_active = (
        parsed_deadline is None or timestamps["started_at_utc"] <= parsed_deadline)
    if outcome.get("deadline_active_before_dispatch") is not expected_deadline_active:
        raise ValueError("reviewer evidence authorization-deadline decision drifted")
    expected_dispatch = expected_deadline_active and guard_verified is not False
    if outcome.get("dispatch_attempted") is not expected_dispatch:
        raise ValueError("reviewer evidence dispatch state contradicts the deadline decision")
    process_exit = outcome.get("process_exit_code")
    if process_exit is not None and (isinstance(process_exit, bool) or not isinstance(
            process_exit, int)):
        raise ValueError("reviewer evidence process exit code must be an integer or null")
    commands, stream_errors = _inspect_reviewer_execution(event_raw, ruling_raw)
    recovered_reconnect = False
    if outcome.get("commands") != commands or outcome.get("event_stream_errors") != stream_errors:
        old_commands, old_errors = _inspect_json_event_stream(event_raw, allow_reconnect_warnings=False)
        expected_old_error = (f"exit=0, ruling_empty=False, ruling_utf8_error=False, "
                              f"event_stream_error={old_errors[0]!r}") if old_errors else None
        if (commands or stream_errors or not old_errors
                or outcome.get("commands") != old_commands
                or outcome.get("event_stream_errors") != old_errors
                or outcome.get("result_ok") is not False
                or outcome.get("process_exit_code") != 0
                or outcome.get("timed_out") is not False
                or outcome.get("dispatch_attempted") is not True
                or outcome.get("error") != expected_old_error):
            raise ValueError("reviewer evidence event-stream classification drifted")
        recovered_reconnect = True
    try:
        normalized = ruling_raw.decode("utf-8").strip().encode("utf-8")
    except UnicodeDecodeError:
        normalized = b""
    if _raw_sha256(normalized) != _strict_sha256(
        outcome.get("normalized_ruling_raw_sha256"),
        field="outcome.normalized_ruling_raw_sha256",
    ) or len(normalized) != _strict_non_negative_int(
        outcome.get("normalized_ruling_byte_count"),
        field="outcome.normalized_ruling_byte_count",
    ):
        raise ValueError("reviewer evidence normalized ruling drifted")
    result_ok = cast(bool, outcome["result_ok"])
    timed_out = cast(bool, outcome["timed_out"])
    dispatch_attempted = cast(bool, outcome["dispatch_attempted"])
    error = outcome.get("error")
    if result_ok:
        if (
            not dispatch_attempted
            or timed_out
            or process_exit != 0
            or stream_errors
            or error is not None
        ):
            raise ValueError("reviewer evidence successful outcome is internally inconsistent")
    elif not isinstance(error, str) or not error:
        raise ValueError("reviewer evidence failed outcome lacks an error")
    if timed_out and (not dispatch_attempted or process_exit is not None or result_ok):
        raise ValueError("reviewer evidence timeout outcome is internally inconsistent")
    if not dispatch_attempted and (event_raw or ruling_raw or process_exit is not None):
        raise ValueError("reviewer evidence records process artifacts without a dispatch")
    if recovered_reconnect:
        if reviewer_recovery_context is None:
            raise ValueError("legacy reconnect interpretation requires signed recovery context")
        recovery_sha = _strict_sha256(reviewer_recovery_context.get("recovery_manifest_sha256"),
                                      field="recovery_manifest_sha256")
        attestation = _reconnect_attestation(reference, receipt, event_raw, ruling_raw, recovery_sha)
        attestation_path = _evidence_directory(packet) / RECONNECT_RECOVERY_FILENAME
        encoded = _canonical_recovery_bytes(attestation)
        if attestation_path.exists():
            if attestation_path.read_bytes() != encoded:
                raise ValueError("reconnect recovery attestation differs from retained evidence")
        elif not _allow_unattested_reconnect:
            raise ValueError("legacy reconnect interpretation requires a separate recovery attestation")
        receipt = {**receipt, "outcome": {**outcome, "result_ok": True,
                   "event_stream_errors": [], "error": None}, "transport_recovery": attestation,
                   "reconnect_recovery": {"path": attestation_path.relative_to(root).as_posix(),
                                          "raw_sha256": _raw_sha256(encoded), "byte_count": len(encoded)}}
    return cast(dict[str, Any], receipt)


def _canonical_recovery_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True, indent=1)
            + "\n").encode("utf-8")


def run_one(
    packet: Path, model: str, effort: str, codex: str,
    not_after_utc: datetime | None = None,
    batch_concurrency: int = 1,
    dispatch_guard_path: Path | None = None,
    dispatch_guard_raw_sha256: str | None = None,
    batch_output_path: Path | None = None,
    expected_prompt_raw_sha256: str | None = None,
    expected_cli_raw_sha256: str | None = None,
    expected_batch_runner_raw_sha256: str | None = None,
    openai_provider_supports_websockets: bool | None = None,
    model_provider_profile: Mapping[str, object] | None = None,
    reviewer_recovery_context: Mapping[str, Any] | None = None,
) -> dict:
    """Review one packet and durably retain its exact local invocation evidence."""
    batch_concurrency = _strict_positive_int(
        batch_concurrency, field="batch_concurrency")
    if (
        openai_provider_supports_websockets is not None
        and not isinstance(openai_provider_supports_websockets, bool)
    ):
        raise ValueError(
            "openai_provider_supports_websockets must be boolean or null"
        )
    normalized_model_provider = normalize_model_provider_profile(model_provider_profile)
    if (
        openai_provider_supports_websockets is not None
        and normalized_model_provider is not None
    ):
        raise ValueError(
            "legacy OpenAI transport override and model provider profile are mutually exclusive"
        )
    frozen_binding_values = (
        expected_prompt_raw_sha256,
        expected_cli_raw_sha256,
        expected_batch_runner_raw_sha256,
    )
    if any(value is not None for value in frozen_binding_values) and not all(
        value is not None for value in frozen_binding_values
    ):
        raise ValueError(
            "frozen reviewer launch bindings must supply prompt, CLI, and runner hashes")
    if all(value is not None for value in frozen_binding_values):
        expected_prompt_raw_sha256 = _strict_sha256(
            expected_prompt_raw_sha256, field="expected_prompt_raw_sha256")
        expected_cli_raw_sha256 = _strict_sha256(
            expected_cli_raw_sha256, field="expected_cli_raw_sha256")
        expected_batch_runner_raw_sha256 = _strict_sha256(
            expected_batch_runner_raw_sha256,
            field="expected_batch_runner_raw_sha256",
        )
        if dispatch_guard_path is not None or dispatch_guard_raw_sha256 is not None:
            raise ValueError(
                "frozen reviewer launch bindings cannot be combined with a dispatch guard")
    prompt_bytes = b""
    prompt_sha = _raw_sha256(prompt_bytes)
    workdir = Path(tempfile.mkdtemp(prefix="reviewer-iso-"))
    out_file = workdir / "ruling.txt"
    argv = [
        codex,
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
    ]
    if openai_provider_supports_websockets is not None:
        argv.extend([
            "-c",
            "model_providers.openai.supports_websockets="
            + str(openai_provider_supports_websockets).lower(),
        ])
    if normalized_model_provider is not None:
        argv.extend(_model_provider_argv(normalized_model_provider))
    argv.extend([
        "-C",
        str(workdir),
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "-s",
        "read-only",
        "--json",
        "-o",
        str(out_file),
        "-",
    ])
    cli_path, cli_sha, cli_bytes = _executable_identity(codex)
    runner_path, runner_sha, runner_bytes = _batch_runner_identity()
    frozen_binding_error: str | None = None
    if expected_cli_raw_sha256 is not None and cli_sha != expected_cli_raw_sha256:
        frozen_binding_error = "reviewer CLI bytes differ from the frozen launch binding"
    elif (
        expected_batch_runner_raw_sha256 is not None
        and runner_sha != expected_batch_runner_raw_sha256
    ):
        frozen_binding_error = "reviewer batch runner differs from the frozen launch binding"
    if (dispatch_guard_path is None) != (dispatch_guard_raw_sha256 is None):
        raise ValueError(
            "reviewer dispatch guard path and raw SHA-256 must be supplied together")
    dispatch_guard_evidence: dict[str, object] | None = None
    dispatch_guard: dict[str, object] | None = None
    reservation_replay = False
    if dispatch_guard_path is not None and dispatch_guard_raw_sha256 is not None:
        if batch_output_path is None:
            raise ValueError("guarded reviewer dispatch requires the batch output path")
        dispatch_guard_evidence, dispatch_guard, verified_prompt = (
            _evaluate_dispatch_guard_snapshot(
            dispatch_guard_path,
            dispatch_guard_raw_sha256,
            codex=codex,
            model=model,
            effort=effort,
            concurrency=batch_concurrency,
            openai_provider_supports_websockets=(
                openai_provider_supports_websockets
            ),
            model_provider_profile=normalized_model_provider,
            packet_directory=packet.parent,
            output_path=batch_output_path,
            selected_packet=packet,
            reviewer_recovery_context=reviewer_recovery_context,
        ))
        if verified_prompt is not None:
            prompt_bytes = verified_prompt
            prompt_sha = _raw_sha256(prompt_bytes)
        started = _active_deadline(str(dispatch_guard_evidence["checked_at_utc"]))
        retained_deadline = dispatch_guard_evidence.get(
            "authorization_valid_until_utc")
        if (
            dispatch_guard_evidence.get("verified") is True
            and (
                not_after_utc is None
                or not isinstance(retained_deadline, str)
                or _active_deadline(retained_deadline) != not_after_utc
            )
        ):
            dispatch_guard_evidence["verified"] = False
            dispatch_guard_evidence["error"] = (
                "reviewer dispatch guard deadline differs from the batch deadline")
        if (
            dispatch_guard_evidence.get("verified") is True
            and dispatch_guard is not None
            and verified_prompt is not None
        ):
            try:
                dispatch_guard_evidence["reservation"] = _reserve_guarded_dispatch(
                    packet=packet,
                    prompt_bytes=verified_prompt,
                    guard=dispatch_guard,
                    guard_raw_sha256=dispatch_guard_raw_sha256,
                    checked_at_utc=str(dispatch_guard_evidence["checked_at_utc"]),
                )
            except (OSError, TypeError, ValueError) as exc:
                dispatch_guard_evidence["verified"] = False
                dispatch_guard_evidence["error"] = str(exc)
                reservation_replay = "reservation already exists" in str(exc)
            if dispatch_guard_evidence.get("verified") is True:
                # checked_at is the durable logical dispatch start. This second sample proves
                # authority is still active after the reservation and before process release.
                released_at = _utc_now()
                if (
                    released_at.tzinfo is None
                    or released_at.utcoffset() != timezone.utc.utcoffset(released_at)
                ):
                    dispatch_guard_evidence["verified"] = False
                    dispatch_guard_evidence["error"] = (
                        "reviewer dispatch release timestamp must use UTC")
                else:
                    released_at = released_at.astimezone(timezone.utc)
                    dispatch_guard_evidence["released_at_utc"] = released_at.isoformat()
                authorization_start = _active_deadline(
                    str(dispatch_guard_evidence["authorization_approved_at_utc"]))
                authorization_end = _active_deadline(
                    str(dispatch_guard_evidence["authorization_valid_until_utc"]))
                capacity_start = _active_deadline(
                    str(dispatch_guard_evidence["capacity_completed_at_utc"]))
                capacity_end = _active_deadline(
                    str(dispatch_guard_evidence["capacity_expires_at_utc"]))
                if dispatch_guard_evidence.get("verified") is not True:
                    pass
                elif released_at < started:
                    dispatch_guard_evidence["verified"] = False
                    dispatch_guard_evidence["error"] = (
                        "reviewer dispatch release precedes its reservation")
                elif not authorization_start <= released_at <= authorization_end:
                    dispatch_guard_evidence["verified"] = False
                    dispatch_guard_evidence["error"] = (
                        "signed reviewer authorization expired before subprocess release")
                elif not capacity_start <= released_at <= capacity_end:
                    dispatch_guard_evidence["verified"] = False
                    dispatch_guard_evidence["error"] = (
                        "reviewer capacity evidence expired before subprocess release")
    else:
        prompt_bytes = packet.read_bytes()
        prompt_sha = _raw_sha256(prompt_bytes)
        started = _utc_now()
    if (
        frozen_binding_error is None
        and expected_prompt_raw_sha256 is not None
        and prompt_sha != expected_prompt_raw_sha256
    ):
        frozen_binding_error = "reviewer prompt differs from the frozen launch binding"
    if frozen_binding_error is not None:
        shutil.rmtree(workdir, ignore_errors=True)
        raise ValueError(frozen_binding_error)
    deadline_active = not_after_utc is None or started <= not_after_utc
    guard_active = (
        dispatch_guard_evidence is None
        or dispatch_guard_evidence.get("verified") is True)
    event_stream_raw = b""
    stderr_raw = b""
    ruling_raw = b""
    commands: list[str] = []
    stream_errors: list[str] = []
    dispatch_attempted = False
    timed_out = False
    process_exit_code: int | None = None
    result: dict[str, object]
    try:
        if not deadline_active:
            commands, stream_errors = _inspect_json_event_stream(event_stream_raw)
            result = {
                "packet": packet.name,
                "prompt_sha256": prompt_sha,
                "ok": False,
                "error": "authorization deadline expired before reviewer dispatch",
                "commands": [],
            }
        elif not guard_active:
            commands, stream_errors = _inspect_json_event_stream(event_stream_raw)
            result = {
                "packet": packet.name,
                "prompt_sha256": prompt_sha,
                "ok": False,
                "error": (
                    "reviewer dispatch guard rejected invocation: "
                    f"{dispatch_guard_evidence.get('error')}"),
                "commands": [],
            }
        else:
            dispatch_attempted = True
            try:
                proc = subprocess.run(
                    argv,
                    input=prompt_bytes,
                    capture_output=True,
                    timeout=PER_REVIEW_TIMEOUT_SECONDS,
                )
                process_exit_code = int(proc.returncode)
                event_stream_raw = _as_bytes(proc.stdout)
                stderr_raw = _as_bytes(getattr(proc, "stderr", None))
                ruling_raw = out_file.read_bytes() if out_file.exists() else b""
                commands, stream_errors = _inspect_reviewer_execution(event_stream_raw, ruling_raw)
                try:
                    ruling = ruling_raw.decode("utf-8").strip()
                    ruling_utf8_error = False
                except UnicodeDecodeError:
                    ruling = ""
                    ruling_utf8_error = True
                if proc.returncode != 0 or stream_errors or ruling_utf8_error:
                    event_error = stream_errors[0] if stream_errors else None
                    result = {
                        "packet": packet.name,
                        "prompt_sha256": prompt_sha,
                        "ok": False,
                        "error": (
                            f"exit={proc.returncode}, ruling_empty={not ruling}, "
                            f"ruling_utf8_error={ruling_utf8_error}, "
                            f"event_stream_error={event_error!r}"
                        ),
                        "commands": commands,
                    }
                else:
                    result = {
                        "packet": packet.name,
                        "prompt_sha256": prompt_sha,
                        "ok": True,
                        "raw_output": ruling,
                        "commands": commands,
                    }
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                event_stream_raw = _as_bytes(exc.stdout)
                stderr_raw = _as_bytes(exc.stderr)
                ruling_raw = out_file.read_bytes() if out_file.exists() else b""
                commands, stream_errors = _inspect_json_event_stream(event_stream_raw)
                result = {
                    "packet": packet.name,
                    "prompt_sha256": prompt_sha,
                    "ok": False,
                    "error": f"timeout after {PER_REVIEW_TIMEOUT_SECONDS}s",
                    "commands": [],
                }
            except OSError as exc:
                commands, stream_errors = _inspect_json_event_stream(event_stream_raw)
                result = {
                    "packet": packet.name,
                    "prompt_sha256": prompt_sha,
                    "ok": False,
                    "error": f"reviewer process unavailable: {type(exc).__name__}: {exc}",
                    "commands": [],
                }

        completed = _utc_now()
        try:
            normalized_ruling = ruling_raw.decode("utf-8").strip().encode("utf-8")
        except UnicodeDecodeError:
            normalized_ruling = b""
        invocation = {
            "argv": argv,
            "model_requested": model,
            "reasoning_effort_requested": effort,
            "batch_concurrency": batch_concurrency,
            "codex_cli_argument": codex,
            "codex_cli_resolved_path": cli_path,
            "codex_cli_wrapper_raw_sha256": cli_sha,
            "codex_cli_wrapper_byte_count": cli_bytes,
            "codex_cli_version": (
                dispatch_guard_evidence.get("reviewer_cli_version")
                if dispatch_guard_evidence is not None
                else None
            ),
            "working_directory": str(workdir),
            "output_file": str(out_file),
            "sandbox_mode": "read-only",
            "ephemeral": True,
            "ignore_user_config": True,
            "ignore_rules": True,
            "json_event_stream": True,
            "python_executable": sys.executable,
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "host_identity": _host_identity(),
            "batch_runner_path": runner_path,
            "batch_runner_raw_sha256": runner_sha,
            "batch_runner_byte_count": runner_bytes,
        }
        if normalized_model_provider is not None:
            invocation["model_provider_profile"] = normalized_model_provider
        else:
            invocation["openai_provider_supports_websockets"] = (
                openai_provider_supports_websockets
            )
        outcome = {
            "authorization_deadline_utc": (
                not_after_utc.astimezone(timezone.utc).isoformat()
                if not_after_utc is not None
                else None
            ),
            "deadline_active_before_dispatch": deadline_active,
            "dispatch_attempted": dispatch_attempted,
            "started_at_utc": started.isoformat(),
            "completed_at_utc": completed.isoformat(),
            "timed_out": timed_out,
            "process_exit_code": process_exit_code,
            "result_ok": bool(result["ok"]),
            "error": result.get("error"),
            "commands": commands,
            "event_stream_errors": stream_errors,
            "normalized_ruling_raw_sha256": _raw_sha256(normalized_ruling),
            "normalized_ruling_byte_count": len(normalized_ruling),
        }
        if not (reservation_replay and _evidence_directory(packet).exists()):
            result["evidence"] = _persist_invocation_evidence(
                packet=packet,
                prompt_bytes=prompt_bytes,
                invocation=invocation,
                outcome=outcome,
                event_stream_raw=event_stream_raw,
                stderr_raw=stderr_raw,
                ruling_raw=ruling_raw,
                dispatch_guard=dispatch_guard_evidence,
            )
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)



class ReviewerUnavailable(RuntimeError):
    """The reviewer was never reached. Abort the wave; do not rule on its queue.

    The distinction this draws is the one that cost 329 payloads on 2026-08-07. A reviewer
    that RULED and produced something unusable (unparseable output, or tool use that breaks
    blindness) has told us something about THAT PAYLOAD, and the frozen failure rule rightly
    commits it as non-ALLOW. A reviewer that was never reached has told us something about the
    REVIEWER, and committing it writes a permanent verdict, in an append-only store, from
    nothing at all.

    Failing closed means refusing to proceed. It does not mean manufacturing a refusal for
    every payload in the queue.
    """


def _attach_evidence(row: dict, result: Mapping[str, object]) -> dict:
    evidence = result.get("evidence")
    if evidence is not None:
        if not isinstance(evidence, Mapping) or set(evidence) != _EVIDENCE_REFERENCE_FIELDS:
            raise ValueError("reviewer result carries an invalid evidence reference")
        row["evidence"] = dict(evidence)
    return row


def classify_result(meta: dict, result: dict, *, packet_ok: bool) -> dict:
    """One dispatched packet's outcome as a decision row, or raise if the reviewer was down."""
    if not packet_ok:
        raise ReviewerUnavailable(
            "packet bytes differ from the frozen prompt hash; the wave cannot commit "
            "a ruling for a prompt other than the indexed prompt")
    if not result.get("ok"):
        raise ReviewerUnavailable(str(result.get("error")))
    if result.get("commands"):
        # Blindness cannot be assumed after the fact; refuse the ruling outright.
        return _attach_evidence({
            "payload_sha256": meta["payload_sha256"],
            "status": "reviewer_error",
            "raw_output": (
                "TOOL_USE_DETECTED: reviewer issued "
                f"{len(result['commands'])} command(s); ruling discarded "
                f"as non-blind. first={result['commands'][0]!r}"
            ),
        }, result)
    return _attach_evidence({
        "payload_sha256": meta["payload_sha256"],
        "raw_output": result.get("raw_output"),
        "prompt_sha256": result.get("prompt_sha256"),
        "tool_uses": 0,
    }, result)


def recover_retained_packet_result(
    packet: Path, *, expected_model: str, expected_effort: str,
    expected_concurrency: int,
    expected_openai_provider_supports_websockets: object = _EXPECTED_TRANSPORT_UNSET,
    expected_model_provider_profile: object = _EXPECTED_MODEL_PROVIDER_UNSET,
    reviewer_recovery_context: Mapping[str, Any],
    create_attestation: bool = True,
) -> dict[str, Any]:
    """Reconstruct one original result without invoking or rewriting its reviewer."""
    evidence_dir = _evidence_directory(packet)
    receipt_path = evidence_dir / "invocation_receipt.json"
    receipt_raw = receipt_path.read_bytes()
    reference = {
        "schema_version": RULING_EVIDENCE_REFERENCE_SCHEMA,
        "receipt_path": receipt_path.relative_to(packet.parent).as_posix(),
        "receipt_raw_sha256": _raw_sha256(receipt_raw),
        "receipt_byte_count": len(receipt_raw),
    }
    receipt = validate_invocation_evidence(
        packet, reference, expected_model=expected_model, expected_effort=expected_effort,
        expected_concurrency=expected_concurrency,
        expected_openai_provider_supports_websockets=expected_openai_provider_supports_websockets,
        expected_model_provider_profile=expected_model_provider_profile,
        accepted_batch_runner_bindings=reviewer_recovery_context["accepted_batch_runner_bindings"],
        reviewer_recovery_context=reviewer_recovery_context,
        _allow_unattested_reconnect=True,
    )
    guard = receipt.get("dispatch_guard")
    if (not isinstance(guard, Mapping) or guard.get("verified") is not True
            or guard.get("snapshot_raw_sha256") != reviewer_recovery_context["dispatch_guard"]["raw_sha256"]):
        raise ValueError("retained reviewer invocation differs from the signed partial-wave guard")
    outcome = receipt["outcome"]
    if outcome["result_ok"] is not True:
        raise ReviewerUnavailable(f"retained reviewer invocation is unresolved: {packet.name}")
    attestation = receipt.get("transport_recovery")
    if attestation is not None and create_attestation:
        path = evidence_dir / RECONNECT_RECOVERY_FILENAME
        encoded = _canonical_recovery_bytes(attestation)
        if not path.exists():
            _durable_write_bytes(path, encoded)
        if path.read_bytes() != encoded:
            raise ValueError("reconnect recovery attestation changed during durable write")
    return {"packet": packet.name, "prompt_sha256": receipt["packet"]["raw_sha256"],
            "ok": True, "raw_output": (evidence_dir / "ruling.txt").read_bytes().decode("utf-8").strip(),
            "commands": list(outcome["commands"]), "evidence": reference}


def recover_retained_batch_results(
    packets_dir: Path, out_path: Path, *, expected_model: str, expected_effort: str,
    expected_concurrency: int,
    expected_openai_provider_supports_websockets: object = _EXPECTED_TRANSPORT_UNSET,
    expected_model_provider_profile: object = _EXPECTED_MODEL_PROVIDER_UNSET,
    reviewer_recovery_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the complete partial batch before appending retained results or dispatching."""
    context = reviewer_recovery_context
    retained = set(context.get("retained_payload_sha256s", []))
    never = set(context.get("never_started_payload_sha256s", []))
    if not retained or retained & never:
        raise ValueError("retained resume requires one signed partial-wave partition")
    index = _strict_json_object((packets_dir / "INDEX.json").read_bytes(), subject="reviewer packet index")
    items = index["items"]
    by_payload = {item["payload_sha256"]: item for item in items}
    if len(by_payload) != len(items) or set(by_payload) != retained | never:
        raise ValueError("signed reviewer recovery partition differs from the unique packet index")
    guard_sha = context["dispatch_guard"]["raw_sha256"]
    reservations = packets_dir / DISPATCH_RESERVATION_DIRECTORY_NAME
    expected_reservations = {f"{guard_sha}_{payload}.json" for payload in by_payload}
    if reservations.exists() and any(p.name not in expected_reservations for p in reservations.iterdir()):
        raise ValueError("partial reviewer wave contains an unbound reservation")
    evidence_root = packets_dir / EVIDENCE_DIRECTORY_NAME
    expected_evidence = {f"{item['file']}.evidence" for item in items}
    if evidence_root.exists() and any(p.name not in expected_evidence for p in evidence_root.iterdir()):
        raise ValueError("partial reviewer wave contains unbound invocation evidence")
    prefix = out_path.read_bytes() if out_path.exists() else b""
    if prefix and not prefix.endswith(b"\n"):
        raise ValueError("partial reviewer rulings end with an incomplete row")
    rows = [_strict_json_object(line, subject="retained reviewer ruling")
            for line in prefix.splitlines() if line.strip()]
    existing = {row["payload_sha256"]: row for row in rows}
    if len(existing) != len(rows) or not set(existing) <= set(by_payload):
        raise ValueError("partial reviewer rulings contain duplicate or unbound payloads")
    kwargs = dict(expected_model=expected_model, expected_effort=expected_effort,
                  expected_concurrency=expected_concurrency,
                  expected_openai_provider_supports_websockets=expected_openai_provider_supports_websockets,
                  expected_model_provider_profile=expected_model_provider_profile,
                  reviewer_recovery_context=context)
    recovered = []
    for item in items:
        payload = item["payload_sha256"]
        packet = packets_dir / item["file"]
        if _raw_sha256(packet.read_bytes()) != item["prompt_sha256"]:
            raise ValueError("partial reviewer packet bytes differ from the frozen index")
        reservation = reservations / f"{guard_sha}_{payload}.json"
        evidence_dir = _evidence_directory(packet)
        if reservation.exists() or evidence_dir.exists() or payload in retained:
            if not reservation.is_file() or not evidence_dir.is_dir():
                raise ValueError("reserved reviewer packet lacks complete retained evidence")
            result = recover_retained_packet_result(packet, create_attestation=False, **kwargs)
            row = classify_result(item, result, packet_ok=result["prompt_sha256"] == item["prompt_sha256"])
            if payload in existing and existing[payload] != row:
                raise ValueError("existing reviewer ruling differs from its retained invocation")
            recovered.append((packet, row))
        elif payload in existing:
            raise ValueError("existing reviewer ruling lacks a reserved retained invocation")
    # No output or attestation is added until every reserved invocation and old row verifies.
    for packet, _ in recovered:
        recover_retained_packet_result(packet, **kwargs)
    if (out_path.read_bytes() if out_path.exists() else b"") != prefix:
        raise ValueError("partial reviewer rulings changed while recovery was checked")
    missing = [row for _, row in recovered if row["payload_sha256"] not in existing]
    if missing:
        with out_path.open("ab") as handle:
            for row in missing:
                handle.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
    return {"retained_count": len(recovered), "imported_count": len(missing),
            "done_payload_sha256s": [row["payload_sha256"] for _, row in recovered]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="codex_reviewer_batch")
    ap.add_argument("--packets", required=True, help="directory holding INDEX.json + packets")
    ap.add_argument("--out", required=True, help="JSONL to append results to")
    ap.add_argument("--model", default=MODEL_DEFAULT)
    ap.add_argument("--effort", default=EFFORT_DEFAULT)
    ap.add_argument("--codex", default="codex")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--resume-retained", action="store_true",
                    help="import exact retained results under a signed partial-wave recovery")
    ap.add_argument("--recovery", help="signed same-run operational recovery manifest")
    ap.add_argument(
        "--openai-provider-supports-websockets",
        choices=("true", "false"),
        default=None,
        help="pin the OpenAI provider WebSocket capability in the Codex CLI",
    )
    ap.add_argument(
        "--model-provider-profile-json",
        default=None,
        help="exact JSON object for the governed custom OpenAI HTTP provider",
    )
    ap.add_argument(
        "--not-after-utc",
        help="signed authorization deadline checked immediately before every reviewer call",
    )
    ap.add_argument(
        "--dispatch-guard",
        help="Phase 3 per-invocation dispatch guard snapshot",
    )
    ap.add_argument(
        "--dispatch-guard-raw-sha256",
        help="expected raw SHA-256 of the Phase 3 dispatch guard snapshot",
    )
    args = ap.parse_args(argv)
    openai_provider_supports_websockets = (
        None
        if args.openai_provider_supports_websockets is None
        else args.openai_provider_supports_websockets == "true"
    )
    try:
        model_provider_profile = (
            None
            if args.model_provider_profile_json is None
            else normalize_model_provider_profile(
                _strict_json_object(
                    args.model_provider_profile_json.encode("utf-8"),
                    subject="--model-provider-profile-json",
                )
            )
        )
    except (UnicodeError, ValueError) as exc:
        ap.error(str(exc))
    if (
        openai_provider_supports_websockets is not None
        and model_provider_profile is not None
    ):
        ap.error(
            "--openai-provider-supports-websockets and "
            "--model-provider-profile-json are mutually exclusive"
        )
    if args.concurrency < 1:
        ap.error("--concurrency must be positive")
    try:
        not_after_utc = (
            _active_deadline(args.not_after_utc)
            if args.not_after_utc is not None else None)
    except ValueError as exc:
        ap.error(str(exc))
    if (args.dispatch_guard is None) != (args.dispatch_guard_raw_sha256 is None):
        ap.error("--dispatch-guard and --dispatch-guard-raw-sha256 are required together")
    dispatch_guard_path: Path | None = None
    dispatch_guard_raw_sha256: str | None = None
    if args.dispatch_guard is not None:
        dispatch_guard_path = Path(args.dispatch_guard)
        if dispatch_guard_path.name != DISPATCH_GUARD_FILENAME:
            ap.error(f"--dispatch-guard must name {DISPATCH_GUARD_FILENAME}")
        try:
            dispatch_guard_raw_sha256 = _strict_sha256(
                args.dispatch_guard_raw_sha256,
                field="--dispatch-guard-raw-sha256",
            )
        except ValueError as exc:
            ap.error(str(exc))

    packets_dir = Path(args.packets)
    out_path = Path(args.out)
    reviewer_recovery_context = None
    if args.resume_retained and args.recovery is None:
        ap.error("--resume-retained requires --recovery")
    if args.recovery is not None:
        if dispatch_guard_path is None:
            ap.error("--recovery requires a dispatch guard")
        from rejudge.phase3_main_reviewer_recovery import load_reviewer_recovery_context
        try:
            reviewer_recovery_context = load_reviewer_recovery_context(
                args.recovery, packet_directory=packets_dir, output_path=out_path,
                guard_path=dispatch_guard_path,
                guard_raw_sha256=cast(str, dispatch_guard_raw_sha256))
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ABORT: reviewer recovery authority rejected batch: {exc}", flush=True)
            return 4
    guarded_index: dict[str, object] | None = None
    preflight_guard: dict[str, object] | None = None
    if dispatch_guard_path is not None:
        if not_after_utc is None:
            ap.error("--not-after-utc is required with a dispatch guard")
        if args.limit is not None:
            ap.error("--limit is forbidden with a dispatch guard")
        if dispatch_guard_path.resolve() != (
            packets_dir / DISPATCH_GUARD_FILENAME
        ).resolve():
            ap.error("--dispatch-guard must be the packet directory guard snapshot")
        preflight_evidence, preflight_guard = evaluate_dispatch_guard_snapshot(
            dispatch_guard_path,
            cast(str, dispatch_guard_raw_sha256),
            codex=args.codex,
            model=args.model,
            effort=args.effort,
            concurrency=args.concurrency,
            openai_provider_supports_websockets=(
                openai_provider_supports_websockets
            ),
            model_provider_profile=model_provider_profile,
            packet_directory=packets_dir,
            output_path=out_path,
            reviewer_recovery_context=reviewer_recovery_context,
        )
        if preflight_evidence.get("verified") is not True or preflight_guard is None:
            print(
                "ABORT: reviewer dispatch guard rejected batch: "
                f"{preflight_evidence.get('error')}",
                flush=True,
            )
            return 4
        guarded_index = {
            "count": len(cast(list[object], preflight_guard["packet_bindings"])),
            "items": [
                {
                    "n": position,
                    "file": binding["file"],
                    "payload_sha256": binding["payload_sha256"],
                    "prompt_sha256": binding["prompt_sha256"],
                }
                for position, binding in enumerate(
                    cast(list[dict[str, object]], preflight_guard["packet_bindings"]), 1)
            ],
        }
    index = guarded_index or _strict_json_object(
        (packets_dir / "INDEX.json").read_bytes(), subject="reviewer packet index")
    if args.resume_retained:
        try:
            recover_retained_batch_results(
                packets_dir, out_path, expected_model=args.model, expected_effort=args.effort,
                expected_concurrency=args.concurrency,
                expected_openai_provider_supports_websockets=openai_provider_supports_websockets,
                expected_model_provider_profile=model_provider_profile,
                reviewer_recovery_context=cast(Mapping[str, Any], reviewer_recovery_context))
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"ABORT: retained reviewer evidence rejected batch: {exc}", flush=True)
            return 3
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["payload_sha256"])

    todo = [i for i in index["items"] if i["payload_sha256"] not in done]
    if args.limit is not None:
        todo = todo[:args.limit]
    if preflight_guard is not None:
        guard_sha = cast(str, dispatch_guard_raw_sha256)
        existing_reservations = []
        for binding in cast(list[Mapping[str, object]], preflight_guard["packet_bindings"]):
            if args.resume_retained and binding["payload_sha256"] in done:
                continue
            reservation = (
                packets_dir
                / DISPATCH_RESERVATION_DIRECTORY_NAME
                / f"{guard_sha}_{binding['payload_sha256']}.json"
            )
            if reservation.exists() or reservation.is_symlink():
                existing_reservations.append(reservation)
        if existing_reservations:
            print(
                "ABORT: reviewer dispatch guard already has durable reservation(s); "
                "this no-resume identity cannot dispatch again",
                flush=True,
            )
            return 3
    if dispatch_guard_path is None:
        for item in todo:
            packet = packets_dir / item["file"]
            try:
                packet_raw = packet.read_bytes()
            except OSError as exc:
                print(f"ABORT: reviewer packet is unreadable: {packet}: {exc}", flush=True)
                return 4
            if _raw_sha256(packet_raw) != item.get("prompt_sha256"):
                print(
                    f"ABORT: reviewer packet bytes drifted before dispatch: {packet}",
                    flush=True,
                )
                return 4
    print(f"{len(done)} already done; dispatching {len(todo)} "
          f"(model={args.model}, effort={args.effort}, concurrency={args.concurrency})",
          flush=True)

    written = clean = refused = failed = 0
    transport_kwargs: dict[str, object] = {}
    if openai_provider_supports_websockets is not None:
        transport_kwargs["openai_provider_supports_websockets"] = (
            openai_provider_supports_websockets
        )
    if model_provider_profile is not None:
        transport_kwargs["model_provider_profile"] = model_provider_profile
    if reviewer_recovery_context is not None:
        transport_kwargs["reviewer_recovery_context"] = reviewer_recovery_context
    unavailable = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        if not_after_utc is None and dispatch_guard_path is None:
            futures = {
                pool.submit(
                    run_one, packets_dir / item["file"], args.model, args.effort,
                    args.codex, None, args.concurrency,
                    **transport_kwargs,
                ): item
                for item in todo
            }
        elif dispatch_guard_path is None:
            futures = {
                pool.submit(
                    run_one, packets_dir / item["file"], args.model, args.effort,
                    args.codex, not_after_utc, args.concurrency,
                    **transport_kwargs,
                ): item
                for item in todo
            }
        else:
            futures = {
                pool.submit(
                    run_one, packets_dir / item["file"], args.model, args.effort,
                    args.codex, not_after_utc, args.concurrency,
                    dispatch_guard_path, dispatch_guard_raw_sha256, out_path,
                    **transport_kwargs,
                ): item
                for item in todo
            }
        for fut in concurrent.futures.as_completed(futures):
            if fut.cancelled():
                continue
            meta = futures[fut]
            # The packet's bytes must still hash to what the frozen index recorded.
            try:
                result = fut.result()
                row = classify_result(
                    meta, result,
                    packet_ok=result["prompt_sha256"] == meta["prompt_sha256"])
            except Exception as down:
                print(f"ABORT: reviewer unavailable ({down}); canceling queued work "
                      "and retaining successful in-flight results.", flush=True)
                for pending in futures:
                    pending.cancel()
                unavailable = True
                continue
            if "tool_uses" in row:
                clean += 1
            elif "TOOL_USE_DETECTED" in row["raw_output"]:
                refused += 1
            else:
                failed += 1
            with out_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            written += 1
            print(f"  [{written}/{len(todo)}] {meta['file']} -> "
                  f"{'clean' if 'tool_uses' in row else row['status']}", flush=True)

    print(f"done: {written} written | clean={clean} refused_tool_use={refused} failed={failed}",
          flush=True)
    return 3 if unavailable else 0


if __name__ == "__main__":
    raise SystemExit(main())
