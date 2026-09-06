"""Capacity-only execution contract for the Phase 3 reviewer preflight.

The frozen capacity plan can derive, materialize, and validate a workload, but it is
deliberately non-authorizing.  This module adds the small execution manifest and separate
owner authorization needed to run cohort 1.  It has no provider or main-run surface.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from rejudge import phase3_owner_signing
from scripts import codex_reviewer_batch
from scripts import phase3_main_review_capacity_preflight as capacity


MANIFEST_SCHEMA = "phase3_main_capacity_execution_manifest_v1"
AUTHORIZATION_SCHEMA = "phase3_main_capacity_execution_authorization_v1"
USAGE_LIMIT_SCHEMA = "phase3_main_capacity_reviewer_usage_limit_v1"
USAGE_RECEIPT_SCHEMA = "phase3_main_capacity_reviewer_usage_receipt_v1"
ATTEMPT_RESERVATION_SCHEMA = "phase3_main_capacity_attempt_reservation_v1"
DISPATCH_RESERVATION_SCHEMA = "phase3_main_capacity_dispatch_reservation_v1"
SCOPE = "phase3_main_reviewer_capacity_preflight_cohort_1"
USAGE_UNIT = "external_reviewer_dispatch"
CAPACITY_SIGNATURE_NAMESPACE = "selvarath-phase3-capacity-authorization-v1"
CAPACITY_SIGNATURE_PRINCIPAL = "jack-maiorino"
SSH_KEYGEN_PATH = Path("C:/Windows/System32/OpenSSH/ssh-keygen.exe")
DOWNSTREAM_LAUNCH_BLOCKER = (
    "capacity evidence alone cannot authorize provider calls or main launch"
)
PACKET_COUNT = 180
WAVE_COUNT = 3
WAVE_SIZE = 60
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")

_AUTHORITY_BOUNDARY = {
    "capacity_execution_authorized": False,
    "external_reviewer_dispatch_authorized": False,
    "provider_calls_authorized": False,
    "together_calls_authorized": False,
    "main_run_authorized": False,
    "main_artifact_mutation_authorized": False,
}
_REQUIRED_AUTHORITY = {
    "capacity_execution_authorized": True,
    "external_reviewer_dispatch_authorized": True,
    "provider_calls_authorized": False,
    "together_calls_authorized": False,
    "main_run_authorized": False,
    "main_artifact_mutation_authorized": False,
}
_PROHIBITIONS = {
    "provider_calls": True,
    "together_calls": True,
    "main_run_execution": True,
    "main_artifact_mutation": True,
    "cohort_2_dispatch": True,
    "resume_or_redispatch": True,
}

_BINDING_FIELDS = frozenset({"path", "raw_sha256", "byte_count"})
_PLAN_BINDING_FIELDS = frozenset(
    {"path", "raw_sha256", "byte_count", "canonical_sha256"}
)
_PACKET_BINDING_FIELDS = frozenset(
    {
        "position",
        "file",
        "payload_sha256",
        "prompt_sha256",
        "raw_sha256",
        "byte_count",
    }
)
_WAVE_FIELDS = frozenset(
    {"wave", "directory", "index", "output_path", "packet_bindings"}
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "scope",
        "run_id",
        "attempt_id",
        "authority_boundary",
        "plan_binding",
        "repository",
        "code_bindings",
        "reviewer",
        "cohort",
        "timing_limits",
        "dispatch_history",
        "workload",
        "result_path",
        "usage_receipt_contract",
        "prohibitions",
    }
)
_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "scope",
        "approved_by",
        "approved_at_utc",
        "valid_until_utc",
        "manifest_raw_sha256",
        "manifest_canonical_sha256",
        "run_id",
        "attempt_id",
        "cohort_number",
        "authority",
        "maximum_reviewer_dispatches",
        "reviewer_usage_limit",
    }
)
_USAGE_LIMIT_FIELDS = frozenset(
    {"schema_version", "unit", "maximum", "accounting_treatment"}
)
_DISPATCH_RESERVATION_FIELDS = frozenset({
    "schema_version",
    "scope",
    "run_id",
    "attempt_id",
    "cohort_number",
    "wave",
    "position",
    "packet_file",
    "packet_path",
    "payload_sha256",
    "prompt_sha256",
    "packet_raw_sha256",
    "manifest_raw_sha256",
    "authorization_raw_sha256",
    "reviewer_model",
    "reviewer_reasoning_effort",
    "reviewer_concurrency",
    "reviewer_cli_raw_sha256",
    "reviewer_batch_runner_raw_sha256",
    "reserved_at_utc",
    "usage_unit",
    "quantity",
})


class CapacityExecutionError(RuntimeError, ValueError):
    """The capacity-only execution contract failed closed."""


@dataclass(frozen=True)
class CapacityContext:
    """The exact frozen plan bytes and their deterministically derived workload."""

    plan_path: Path
    plan_raw: bytes
    plan: Mapping[str, Any]
    workload: capacity.DerivedWorkload


ReviewerRunner = Callable[..., Mapping[str, Any]]
Clock = Callable[[], float]
UtcNow = Callable[[], datetime]
RepositoryProbe = Callable[[Path], tuple[str, bool]]
CliVersionReader = Callable[[str], str]
HostReader = Callable[[], str]
AuthorizationVerifier = Callable[[Path, bytes], None]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CapacityExecutionError(f"JSON object repeats key {key!r}")
        value[key] = item
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strict_object(raw: bytes, *, subject: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                CapacityExecutionError(f"non-finite JSON value {token!r}")),
        )
    except (UnicodeError, json.JSONDecodeError, CapacityExecutionError) as exc:
        if isinstance(exc, CapacityExecutionError):
            raise
        raise CapacityExecutionError(f"{subject} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CapacityExecutionError(f"{subject} must be a JSON object")
    return value


def _stable_read(path: Path, *, subject: str) -> bytes:
    supplied = Path(path)
    if supplied.is_symlink():
        raise CapacityExecutionError(f"{subject} cannot be a symbolic link")
    try:
        first = supplied.read_bytes()
        second = supplied.read_bytes()
    except OSError as exc:
        raise CapacityExecutionError(f"{subject} is unavailable: {supplied}") from exc
    if first != second:
        raise CapacityExecutionError(f"{subject} changed while checked")
    return first


def _sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise CapacityExecutionError(f"{field} must be a lowercase SHA-256")
    return value


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CapacityExecutionError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CapacityExecutionError(f"{field} must be a nonnegative integer")
    return value


def _text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapacityExecutionError(f"{field} must be nonempty text")
    return value


def _identifier(value: Any, *, field: str) -> str:
    text = _text(value, field=field)
    if _ID_RE.fullmatch(text) is None:
        raise CapacityExecutionError(f"{field} has an invalid identifier")
    return text


def _commit(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _COMMIT_RE.fullmatch(value) is None:
        raise CapacityExecutionError(f"{field} must be a lowercase 40-byte commit ID")
    return value


def _utc(value: Any, *, field: str) -> datetime:
    text = _text(value, field=field)
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    except ValueError as exc:
        raise CapacityExecutionError(f"{field} must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise CapacityExecutionError(f"{field} must use UTC")
    return parsed.astimezone(timezone.utc)


def _absolute_path(value: Any, *, field: str) -> Path:
    text = _text(value, field=field)
    supplied = Path(text)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise CapacityExecutionError(f"{field} must be an absolute unlinked path")
    resolved = supplied.resolve()
    if resolved.as_posix() != text:
        raise CapacityExecutionError(f"{field} must be a resolved POSIX path")
    return resolved


def _lexical_absolute(path: Path) -> Path:
    """Return an absolute normalized path without following filesystem links."""
    return Path(os.path.abspath(os.fspath(path)))


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    """Return whether either normalized path contains the other."""
    return _path_is_within(left, right) or _path_is_within(right, left)


def _reject_link_alias_risks(
    path: Path,
    *,
    field: str,
    capacity_root: Path,
) -> None:
    """Reject existing indirection and hard-link aliases under the capacity root.

    A path that does not exist cannot yet have a hard-link identity. Its existing
    ancestors are still checked, and later writes remain exclusive.
    """
    lexical_path = _lexical_absolute(path)
    lexical_root = _lexical_absolute(capacity_root)
    if not _path_is_within(lexical_path, lexical_root):
        raise CapacityExecutionError(
            f"capacity {field} must remain under the capacity artifact root"
        )
    current = lexical_path
    while True:
        try:
            metadata = current.lstat()
        except (FileNotFoundError, NotADirectoryError):
            metadata = None
        except OSError as exc:
            raise CapacityExecutionError(
                f"capacity {field} path identity is unavailable: {current}"
            ) from exc
        if metadata is not None:
            file_attributes = getattr(metadata, "st_file_attributes", 0)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                reparse_flag and file_attributes & reparse_flag
            ):
                raise CapacityExecutionError(
                    f"capacity {field} traverses a symlink, junction, or reparse point: "
                    f"{current}"
                )
            if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
                raise CapacityExecutionError(
                    f"capacity {field} has a hard-link alias: {current}"
                )
            if current != lexical_path and not stat.S_ISDIR(metadata.st_mode):
                raise CapacityExecutionError(
                    f"capacity {field} has a non-directory ancestor: {current}"
                )
        if current == lexical_root:
            break
        current = current.parent


def _reject_critical_path_aliases(
    critical_paths: Sequence[tuple[str, Path]],
    *,
    capacity_root: Path,
    workload_root: Path,
) -> None:
    """Reject equality, containment, reparse, and existing file-identity aliases."""
    normalized = [
        (label, _lexical_absolute(path)) for label, path in critical_paths
    ]
    for label, path in normalized:
        _reject_link_alias_risks(
            path,
            field=label,
            capacity_root=capacity_root,
        )
    for position, (left_label, left_path) in enumerate(normalized):
        for right_label, right_path in normalized[position + 1 :]:
            if _paths_overlap(left_path, right_path):
                raise CapacityExecutionError(
                    "capacity runtime paths alias or contain one another: "
                    f"{left_label} and {right_label}"
                )
            if left_path.exists() and right_path.exists():
                try:
                    same_file = left_path.samefile(right_path)
                except OSError as exc:
                    raise CapacityExecutionError(
                        "capacity runtime path identities are unavailable: "
                        f"{left_label} and {right_label}"
                    ) from exc
                if same_file:
                    raise CapacityExecutionError(
                        "capacity runtime paths share one filesystem identity: "
                        f"{left_label} and {right_label}"
                    )
    resolved_workload = _lexical_absolute(workload_root)
    for label, path in normalized:
        if label.startswith("workload.waves["):
            continue
        if _paths_overlap(path, resolved_workload):
            raise CapacityExecutionError(
                "capacity workload root aliases or contains another runtime path: "
                f"workload.root and {label}"
            )


def _artifact_binding(path: Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    raw = _stable_read(resolved, subject=f"artifact {resolved}")
    return {
        "path": resolved.as_posix(),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
    }


def _verify_binding(binding: Any, *, field: str) -> tuple[Path, bytes]:
    if not isinstance(binding, Mapping) or set(binding) != _BINDING_FIELDS:
        raise CapacityExecutionError(f"{field} artifact binding fields drifted")
    path = _absolute_path(binding.get("path"), field=f"{field}.path")
    raw = _stable_read(path, subject=field)
    if len(raw) != _nonnegative_int(binding.get("byte_count"), field=f"{field}.byte_count"):
        raise CapacityExecutionError(f"{field} byte count drifted")
    if hashlib.sha256(raw).hexdigest() != _sha(
        binding.get("raw_sha256"), field=f"{field}.raw_sha256"
    ):
        raise CapacityExecutionError(f"{field} bytes drifted")
    return path, raw


def _write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    _write_exclusive(path, raw)


def load_capacity_context(
    plan_path: Path,
    *,
    project_root: Path,
    archive_path: Path | None = None,
    finalization_path: Path | None = None,
) -> CapacityContext:
    """Rebuild and validate the frozen capacity workload without mutating state."""
    resolved_plan = Path(plan_path).resolve()
    raw = _stable_read(resolved_plan, subject="capacity plan")
    plan = _strict_object(raw, subject="capacity plan")
    sources = plan.get("source_locations")
    if not isinstance(sources, Mapping):
        raise CapacityExecutionError("capacity plan lacks source locations")
    archive = archive_path or Path(_text(sources.get("sealed_archive"), field="sealed_archive"))
    finalization_value = _text(
        sources.get("finalization_record"), field="finalization_record"
    )
    finalization = finalization_path or (Path(project_root).resolve() / finalization_value)
    try:
        snapshot = capacity.collect_source_snapshot(
            archive_dir=Path(archive).resolve(),
            finalization_path=Path(finalization).resolve(),
        )
        workload = capacity.derive_workload(
            snapshot,
            derivation_tag=capacity.derivation_tag_from_plan(plan),
        )
        capacity.validate_plan(plan, snapshot=snapshot, workload=workload)
    except (OSError, ValueError) as exc:
        raise CapacityExecutionError(f"capacity plan validation failed: {exc}") from exc
    return CapacityContext(
        plan_path=resolved_plan,
        plan_raw=raw,
        plan=plan,
        workload=workload,
    )


def _packet_catalog(
    context: CapacityContext,
    *,
    workload_root: Path,
    require_pristine: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(workload_root).resolve()
    manifest_path = root / "WORKLOAD_MANIFEST.json"
    materialization_raw = _stable_read(
        manifest_path, subject="capacity workload manifest"
    )
    materialization = _strict_object(
        materialization_raw, subject="capacity workload manifest"
    )
    expected_materialization = {
        "schema_version": capacity.MATERIALIZATION_SCHEMA_VERSION,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "plan_canonical_sha256": canonical_sha256(context.plan),
        "cohort_number": 1,
        "cohort_records_canonical_sha256": context.workload.summary[
            "cohort_records_canonical_sha256s"
        ][0],
        "dispatch_history_raw_sha256_before_materialization": hashlib.sha256(b"").hexdigest(),
    }
    for key, expected in expected_materialization.items():
        if materialization.get(key) != expected:
            raise CapacityExecutionError(
                f"capacity workload manifest {key} differs from cohort 1"
            )
    declared_waves = materialization.get("waves")
    if not isinstance(declared_waves, list) or len(declared_waves) != WAVE_COUNT:
        raise CapacityExecutionError("capacity workload must contain three waves")

    wave_records: list[dict[str, Any]] = []
    expected_items = context.workload.selected
    if len(expected_items) != PACKET_COUNT:
        raise CapacityExecutionError("capacity cohort 1 must contain 180 packets")
    for wave_offset in range(WAVE_COUNT):
        wave_number = wave_offset + 1
        wave_dir = root / f"wave_{wave_number:02d}"
        if not wave_dir.is_dir() or wave_dir.is_symlink():
            raise CapacityExecutionError(f"capacity wave {wave_number} directory is unavailable")
        index_path = wave_dir / "INDEX.json"
        index_raw = _stable_read(index_path, subject=f"capacity wave {wave_number} index")
        index = _strict_object(index_raw, subject=f"capacity wave {wave_number} index")
        items = index.get("items")
        if index.get("count") != WAVE_SIZE or not isinstance(items, list) or len(items) != WAVE_SIZE:
            raise CapacityExecutionError(f"capacity wave {wave_number} index must contain 60 items")
        declared = declared_waves[wave_offset]
        if not isinstance(declared, Mapping) or dict(declared) != {
            "wave": wave_number,
            "count": WAVE_SIZE,
            "index_raw_sha256": hashlib.sha256(index_raw).hexdigest(),
        }:
            raise CapacityExecutionError(f"capacity wave {wave_number} manifest binding drifted")

        packet_bindings: list[dict[str, Any]] = []
        wave_expected = expected_items[wave_offset * WAVE_SIZE : (wave_offset + 1) * WAVE_SIZE]
        allowed_names = {"INDEX.json"}
        for position, (item, expected_item) in enumerate(zip(items, wave_expected), 1):
            if not isinstance(item, Mapping):
                raise CapacityExecutionError(f"capacity wave {wave_number} index item is invalid")
            expected_file = f"{position:03d}_{expected_item.variant_payload_sha256[:12]}.txt"
            expected_index = {
                "n": position,
                "file": expected_file,
                "payload_sha256": expected_item.variant_payload_sha256,
                "prompt_sha256": expected_item.variant_prompt_sha256,
                "source_payload_sha256": expected_item.source_payload_sha256,
            }
            if dict(item) != expected_index:
                raise CapacityExecutionError(
                    f"capacity wave {wave_number} packet {position} index drifted"
                )
            packet_path = wave_dir / expected_file
            packet_raw = _stable_read(
                packet_path,
                subject=f"capacity wave {wave_number} packet {position}",
            )
            if (
                packet_raw != expected_item.prompt.encode("utf-8")
                or hashlib.sha256(packet_raw).hexdigest()
                != expected_item.variant_prompt_sha256
            ):
                raise CapacityExecutionError(
                    f"capacity wave {wave_number} packet {position} bytes drifted"
                )
            packet_bindings.append(
                {
                    "position": position,
                    "file": expected_file,
                    "payload_sha256": expected_item.variant_payload_sha256,
                    "prompt_sha256": expected_item.variant_prompt_sha256,
                    "raw_sha256": hashlib.sha256(packet_raw).hexdigest(),
                    "byte_count": len(packet_raw),
                }
            )
            allowed_names.add(expected_file)
        observed_names = {path.name for path in wave_dir.iterdir()}
        permitted_runtime_names = {
            codex_reviewer_batch.EVIDENCE_DIRECTORY_NAME,
            codex_reviewer_batch.DISPATCH_RESERVATION_DIRECTORY_NAME,
            "capacity_results.jsonl",
        }
        if (
            not allowed_names.issubset(observed_names)
            or (
                require_pristine
                and observed_names != allowed_names
            )
            or (
                not require_pristine
                and not observed_names.issubset(allowed_names | permitted_runtime_names)
            )
        ):
            raise CapacityExecutionError(
                f"capacity wave {wave_number} contains prior or unexpected artifacts"
            )
        wave_records.append(
            {
                "wave": wave_number,
                "directory": wave_dir.as_posix(),
                "index": _artifact_binding(index_path),
                "output_path": (wave_dir / "capacity_results.jsonl").resolve().as_posix(),
                "packet_bindings": packet_bindings,
            }
        )
    return _artifact_binding(manifest_path), wave_records


def build_execution_manifest(
    *,
    context: CapacityContext,
    project_root: Path,
    workload_root: Path,
    result_path: Path,
    run_id: str,
    attempt_id: str,
    repository_head: str,
    reviewer_cli_version: str,
    host_identity: str,
    runner_script_path: Path,
    require_pristine: bool = True,
) -> dict[str, Any]:
    """Build one non-authorizing manifest from an initialized pristine workload."""
    _identifier(run_id, field="run_id")
    _identifier(attempt_id, field="attempt_id")
    if _COMMIT_RE.fullmatch(repository_head) is None:
        raise CapacityExecutionError("repository_head must be a lowercase commit SHA")
    reviewer_cli_version = _text(
        reviewer_cli_version, field="reviewer_cli_version"
    )
    host_identity = _text(host_identity, field="host_identity")
    plan = context.plan
    reviewer = plan.get("reviewer_configuration")
    if not isinstance(reviewer, Mapping):
        raise CapacityExecutionError("capacity plan lacks reviewer configuration")
    if (
        reviewer.get("model") != "gpt-5.6-sol"
        or reviewer.get("reasoning_effort") != "high"
        or reviewer.get("concurrency") != 12
    ):
        raise CapacityExecutionError("capacity reviewer configuration drifted")
    if (
        "openai_provider_supports_websockets" in reviewer
        and not isinstance(
            reviewer.get("openai_provider_supports_websockets"), bool
        )
    ):
        raise CapacityExecutionError("capacity reviewer transport configuration drifted")
    try:
        model_provider_profile = codex_reviewer_batch.normalize_model_provider_profile(
            reviewer.get("model_provider_profile")
        )
    except ValueError as exc:
        raise CapacityExecutionError(
            f"capacity reviewer model provider configuration drifted: {exc}"
        ) from exc
    if (
        "openai_provider_supports_websockets" in reviewer
        and model_provider_profile is not None
    ):
        raise CapacityExecutionError(
            "capacity reviewer transport configurations are mutually exclusive"
        )
    if model_provider_profile is not None:
        version_header = cast(
            Mapping[str, object], model_provider_profile["http_headers"]
        )["version"]
        if reviewer_cli_version != f"codex-cli {version_header}":
            raise CapacityExecutionError(
                "capacity reviewer model provider version header differs from the CLI"
            )
    cli_path = Path(_text(
        reviewer.get("reviewer_cli_resolved_path"), field="reviewer_cli_resolved_path"
    )).resolve()
    cli_binding = _artifact_binding(cli_path)
    if (
        cli_binding["raw_sha256"] != reviewer.get("reviewer_cli_wrapper_raw_sha256")
        or cli_binding["byte_count"] != reviewer.get("reviewer_cli_wrapper_byte_count")
    ):
        raise CapacityExecutionError("capacity reviewer CLI wrapper differs from the plan")

    contract = plan.get("dispatch_history_contract")
    if not isinstance(contract, Mapping):
        raise CapacityExecutionError("capacity plan lacks dispatch history contract")
    supplied_history_path = Path(
        _text(contract.get("required_path"), field="history path")
    )
    history_path = supplied_history_path.resolve()
    capacity_root = history_path.parent.resolve()
    pending_path, initialization_receipt_path = capacity._initialization_paths(  # noqa: SLF001
        history_path
    )
    append_intent_path = capacity._append_intent_path(history_path)  # noqa: SLF001
    writer_lock_path = history_path.with_name(f"{history_path.name}.lock")
    supplied_anchor_directory = Path(
        _text(contract.get("anchor_directory"), field="anchor directory")
    )
    supplied_interruption_root = Path(
        _text(
            contract.get("interruption_evidence_root"),
            field="interruption evidence root",
        )
    )
    anchor_directory = supplied_anchor_directory.resolve()
    interruption_evidence_root = supplied_interruption_root.resolve()
    supplied_workload = _lexical_absolute(Path(workload_root))
    supplied_result = _lexical_absolute(Path(result_path))
    for label, path in (
        ("dispatch_history.path", supplied_history_path),
        ("dispatch_history.initialization_pending", pending_path),
        ("dispatch_history.initialization_receipt", initialization_receipt_path),
        ("dispatch_history.append_intent", append_intent_path),
        ("dispatch_history.writer_lock", writer_lock_path),
        ("dispatch_history.anchor_directory", supplied_anchor_directory),
        ("dispatch_history.interruption_evidence_root", supplied_interruption_root),
        ("workload.root", supplied_workload),
        ("result_path", supplied_result),
    ):
        _reject_link_alias_risks(
            path,
            field=label,
            capacity_root=capacity_root,
        )
    history = capacity.load_bound_dispatch_history(history_path, plan=plan)
    try:
        capacity._validate_dispatch_history_chain(  # noqa: SLF001
            history, plan=plan, workload=context.workload
        )
    except ValueError as exc:
        raise CapacityExecutionError(f"capacity history validation failed: {exc}") from exc
    if require_pristine and (
        history.events or history.raw_sha256 != hashlib.sha256(b"").hexdigest()
    ):
        raise CapacityExecutionError("capacity manifest requires pristine dispatch history")
    if pending_path.exists() or pending_path.is_symlink():
        raise CapacityExecutionError("capacity history has pending initialization state")
    initialization_binding = _artifact_binding(initialization_receipt_path)

    resolved_workload = supplied_workload.resolve()
    resolved_result = supplied_result.resolve()
    for value, label in (
        (resolved_workload, "workload root"),
        (resolved_result, "result path"),
    ):
        try:
            value.relative_to(capacity_root)
        except ValueError as exc:
            raise CapacityExecutionError(
                f"capacity {label} must remain under the capacity artifact root"
            ) from exc
    if require_pristine and (resolved_result.exists() or resolved_result.is_symlink()):
        raise CapacityExecutionError("capacity result path must not already exist")
    workload_binding, waves = _packet_catalog(
        context, workload_root=resolved_workload, require_pristine=require_pristine
    )
    reservation_path = (
        capacity_root / f"{attempt_id}.capacity_attempt_reservation.json"
    ).resolve()
    failure_path = (
        capacity_root / f"{attempt_id}.capacity_failure_receipt.json"
    ).resolve()
    critical_paths = [
        ("result_path", resolved_result),
        ("dispatch_history.path", history_path),
        ("dispatch_history.initialization_pending", pending_path.resolve()),
        ("dispatch_history.initialization_receipt", initialization_receipt_path.resolve()),
        ("dispatch_history.append_intent", append_intent_path.resolve()),
        ("dispatch_history.writer_lock", writer_lock_path.resolve()),
        ("dispatch_history.anchor_directory", anchor_directory),
        ("dispatch_history.interruption_evidence_root", interruption_evidence_root),
        ("dispatch_history.attempt_reservation_path", reservation_path),
        ("dispatch_history.failure_receipt_path", failure_path),
        *[
            (f"workload.waves[{wave['wave']}].output_path", Path(str(wave["output_path"])))
            for wave in waves
        ],
    ]
    _reject_critical_path_aliases(
        critical_paths,
        capacity_root=capacity_root,
        workload_root=resolved_workload,
    )
    module_path = Path(__file__).resolve()
    expected_cli_script = (
        module_path.parents[1] / "scripts" / "phase3_main_run_capacity_preflight.py"
    ).resolve()
    if Path(runner_script_path).resolve() != expected_cli_script:
        raise CapacityExecutionError("capacity manifest must bind the project capacity CLI")
    reviewer_runner_path, reviewer_runner_sha, reviewer_runner_size = (
        codex_reviewer_batch._batch_runner_identity()  # noqa: SLF001
    )
    code_bindings = {
        "capacity_execution": _artifact_binding(module_path),
        "capacity_cli": _artifact_binding(expected_cli_script),
        "reviewer_batch": {
            "path": reviewer_runner_path,
            "raw_sha256": reviewer_runner_sha,
            "byte_count": reviewer_runner_size,
        },
    }
    thresholds = plan.get("capacity_thresholds")
    if not isinstance(thresholds, Mapping):
        raise CapacityExecutionError("capacity plan lacks timing thresholds")
    manifest_reviewer = {
        "model": reviewer["model"],
        "reasoning_effort": reviewer["reasoning_effort"],
        "concurrency": reviewer["concurrency"],
        "cli": {
            **cli_binding,
            "version": reviewer_cli_version,
        },
        "host_identity": host_identity,
    }
    if "openai_provider_supports_websockets" in reviewer:
        manifest_reviewer["openai_provider_supports_websockets"] = reviewer[
            "openai_provider_supports_websockets"
        ]
    if model_provider_profile is not None:
        manifest_reviewer["model_provider_profile"] = model_provider_profile
    return {
        "schema_version": MANIFEST_SCHEMA,
        "scope": SCOPE,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "authority_boundary": dict(_AUTHORITY_BOUNDARY),
        "plan_binding": {
            "path": context.plan_path.resolve().as_posix(),
            "raw_sha256": hashlib.sha256(context.plan_raw).hexdigest(),
            "byte_count": len(context.plan_raw),
            "canonical_sha256": canonical_sha256(plan),
        },
        "repository": {
            "project_root": Path(project_root).resolve().as_posix(),
            "head_commit": repository_head,
            "tracked_clean_required": True,
        },
        "code_bindings": code_bindings,
        "reviewer": manifest_reviewer,
        "cohort": {
            "cohort_number": 1,
            "packet_count": PACKET_COUNT,
            "wave_count": WAVE_COUNT,
            "wave_size": WAVE_SIZE,
            "maximum_reviewer_dispatches": PACKET_COUNT,
            "cohort_records_canonical_sha256": context.workload.summary[
                "cohort_records_canonical_sha256s"
            ][0],
        },
        "timing_limits": {
            "per_reviewer_timeout_seconds": codex_reviewer_batch.PER_REVIEW_TIMEOUT_SECONDS,
            "maximum_seconds_per_wave": thresholds[
                "maximum_seconds_per_60_packet_wave"
            ],
            "maximum_seconds_total": thresholds[
                "maximum_seconds_for_three_waves"
            ],
        },
        "dispatch_history": {
            "path": history_path.as_posix(),
            "required_initial_raw_sha256": hashlib.sha256(b"").hexdigest(),
            "initialization_receipt": initialization_binding,
            "attempt_reservation_path": reservation_path.as_posix(),
            "failure_receipt_path": failure_path.as_posix(),
            "anchor_directory": anchor_directory.as_posix(),
            "interruption_evidence_root": interruption_evidence_root.as_posix(),
        },
        "workload": {
            "root": resolved_workload.as_posix(),
            "manifest": workload_binding,
            "waves": waves,
        },
        "result_path": resolved_result.as_posix(),
        "usage_receipt_contract": {
            "schema_version": USAGE_RECEIPT_SCHEMA,
            "unit": USAGE_UNIT,
            "quantity_source": "one_reopened_durable_invocation_receipt_per_dispatch",
            "non_claim": "dispatch count is not USD or token accounting",
        },
        "prohibitions": dict(_PROHIBITIONS),
    }


def _validate_manifest(
    manifest: Mapping[str, Any],
    *,
    context: CapacityContext,
) -> dict[str, Any]:
    """Rebuild the expected manifest and require exact equality."""
    if set(manifest) != _MANIFEST_FIELDS or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise CapacityExecutionError("capacity execution manifest fields or schema drifted")
    if manifest.get("scope") != SCOPE:
        raise CapacityExecutionError("capacity execution manifest has the wrong scope")
    if manifest.get("authority_boundary") != _AUTHORITY_BOUNDARY:
        raise CapacityExecutionError("capacity manifest must remain non-authorizing")
    if manifest.get("prohibitions") != _PROHIBITIONS:
        raise CapacityExecutionError("capacity manifest prohibitions drifted")
    if not isinstance(manifest.get("cohort"), Mapping) or manifest["cohort"].get(
        "cohort_number"
    ) != 1:
        raise CapacityExecutionError("capacity manifest must bind cohort 1 exactly")
    try:
        repository = cast(Mapping[str, Any], manifest["repository"])
        reviewer = cast(Mapping[str, Any], manifest["reviewer"])
        cli = cast(Mapping[str, Any], reviewer["cli"])
        code = cast(Mapping[str, Any], manifest["code_bindings"])
        cli_script = cast(Mapping[str, Any], code["capacity_cli"])
        workload = cast(Mapping[str, Any], manifest["workload"])
        expected = build_execution_manifest(
            context=context,
            project_root=_absolute_path(
                repository["project_root"], field="repository.project_root"
            ),
            workload_root=_absolute_path(workload["root"], field="workload.root"),
            result_path=_absolute_path(manifest["result_path"], field="result_path"),
            run_id=_identifier(manifest["run_id"], field="manifest.run_id"),
            attempt_id=_identifier(manifest["attempt_id"], field="manifest.attempt_id"),
            repository_head=_commit(
                repository["head_commit"], field="repository.head_commit"
            ),
            reviewer_cli_version=_text(
                cli["version"], field="reviewer.cli.version"
            ),
            host_identity=_text(
                reviewer["host_identity"], field="reviewer.host_identity"
            ),
            runner_script_path=_absolute_path(
                cli_script["path"], field="code_bindings.capacity_cli.path"
            ),
            require_pristine=False,
        )
    except (KeyError, TypeError) as exc:
        raise CapacityExecutionError(
            "capacity execution manifest nested fields drifted"
        ) from exc
    if dict(manifest) != expected:
        raise CapacityExecutionError(
            "capacity execution manifest differs from exact rebuilt inputs"
        )
    return expected


def load_execution_manifest(
    path: Path, *, context: CapacityContext
) -> tuple[bytes, dict[str, Any]]:
    """Reopen an exact manifest before or after execution without requiring pristine state."""
    raw, manifest = _load_exact_json(
        Path(path).resolve(), subject="capacity execution manifest"
    )
    return raw, _validate_manifest(manifest, context=context)


def build_unsigned_capacity_authorization(
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    authorization_id: str,
    approved_at_utc: datetime,
    valid_until_utc: datetime,
) -> dict[str, Any]:
    """Build exact review bytes that remain non-authorizing until owner-signed."""
    if not isinstance(approved_at_utc, datetime) or not isinstance(
        valid_until_utc, datetime
    ):
        raise CapacityExecutionError("capacity authorization times must be datetimes")
    approved = _utc(approved_at_utc.isoformat(), field="approved_at_utc")
    valid_until = _utc(valid_until_utc.isoformat(), field="valid_until_utc")
    authorization = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "authorization_id": authorization_id,
        "scope": SCOPE,
        "approved_by": "Jack Maiorino",
        "approved_at_utc": approved.isoformat(),
        "valid_until_utc": valid_until.isoformat(),
        "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "manifest_canonical_sha256": canonical_sha256(manifest),
        "run_id": manifest.get("run_id"),
        "attempt_id": manifest.get("attempt_id"),
        "cohort_number": 1,
        "authority": dict(_REQUIRED_AUTHORITY),
        "maximum_reviewer_dispatches": PACKET_COUNT,
        "reviewer_usage_limit": {
            "schema_version": USAGE_LIMIT_SCHEMA,
            "unit": USAGE_UNIT,
            "maximum": PACKET_COUNT,
            "accounting_treatment": (
                "owner-authorized capacity invocation count only"
            ),
        },
    }
    return validate_authorization(
        authorization,
        manifest=manifest,
        manifest_raw=manifest_raw,
        observed_at=approved,
    )


def validate_authorization(
    authorization: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    observed_at: datetime,
) -> dict[str, Any]:
    """Validate one capacity-only authority record against exact manifest bytes."""
    if (
        set(authorization) != _AUTHORIZATION_FIELDS
        or authorization.get("schema_version") != AUTHORIZATION_SCHEMA
    ):
        raise CapacityExecutionError("capacity authorization fields or schema drifted")
    _identifier(authorization.get("authorization_id"), field="authorization_id")
    if authorization.get("scope") != SCOPE:
        raise CapacityExecutionError("capacity authorization has the wrong scope")
    if authorization.get("approved_by") != "Jack Maiorino":
        raise CapacityExecutionError("capacity authorization is not owner-approved")
    if authorization.get("authority") != _REQUIRED_AUTHORITY:
        raise CapacityExecutionError("capacity reviewer authority is absent or false")
    if authorization.get("manifest_raw_sha256") != hashlib.sha256(manifest_raw).hexdigest():
        raise CapacityExecutionError("capacity authorization binds other manifest bytes")
    if authorization.get("manifest_canonical_sha256") != canonical_sha256(manifest):
        raise CapacityExecutionError("capacity authorization binds another manifest")
    if (
        authorization.get("run_id") != manifest.get("run_id")
        or authorization.get("attempt_id") != manifest.get("attempt_id")
        or authorization.get("cohort_number") != 1
    ):
        raise CapacityExecutionError("capacity authorization identity drifted")
    if authorization.get("maximum_reviewer_dispatches") != PACKET_COUNT:
        raise CapacityExecutionError("capacity authorization must cap cohort 1 at 180 dispatches")
    usage = authorization.get("reviewer_usage_limit")
    if not isinstance(usage, Mapping) or set(usage) != _USAGE_LIMIT_FIELDS:
        raise CapacityExecutionError("capacity authorization usage limit fields drifted")
    if (
        usage.get("schema_version") != USAGE_LIMIT_SCHEMA
        or usage.get("unit") != USAGE_UNIT
        or usage.get("maximum") != PACKET_COUNT
    ):
        raise CapacityExecutionError(
            "capacity authorization usage limit must explicitly cap 180 reviewer dispatches"
        )
    _text(usage.get("accounting_treatment"), field="reviewer_usage_limit.accounting_treatment")
    approved = _utc(authorization.get("approved_at_utc"), field="approved_at_utc")
    valid_until = _utc(authorization.get("valid_until_utc"), field="valid_until_utc")
    if valid_until < approved:
        raise CapacityExecutionError("capacity authorization window is reversed")
    if observed_at.tzinfo is None or observed_at.utcoffset() != timezone.utc.utcoffset(observed_at):
        raise CapacityExecutionError("capacity authorization observation must use UTC")
    observed = observed_at.astimezone(timezone.utc)
    if not approved <= observed <= valid_until:
        raise CapacityExecutionError("capacity authorization is inactive")
    return dict(authorization)


def _default_repository_probe(project_root: Path) -> tuple[str, bool]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if head.returncode != 0 or status.returncode != 0:
        raise CapacityExecutionError("capacity repository probe failed")
    return head.stdout.strip(), not status.stdout.strip()


def _default_cli_version(cli: str) -> str:
    return codex_reviewer_batch._reviewer_cli_version(cli)  # noqa: SLF001


def _default_host() -> str:
    return codex_reviewer_batch._host_identity()  # noqa: SLF001


def _default_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_exact_json(path: Path, *, subject: str) -> tuple[bytes, dict[str, Any]]:
    raw = _stable_read(path, subject=subject)
    return raw, _strict_object(raw, subject=subject)


def verify_capacity_authorization_signature(
    path: Path,
    expected_raw: bytes,
    *,
    public_key: str | None = None,
    key_fingerprint: str | None = None,
    ssh_keygen_path: Path | None = None,
) -> None:
    """Verify the exact capacity authorization bytes under a separate SSH namespace."""
    pinned_key = (
        phase3_owner_signing.OWNER_SIGNING_PUBLIC_KEY
        if public_key is None
        else public_key
    )
    pinned_fingerprint = (
        phase3_owner_signing.OWNER_SIGNING_KEY_FINGERPRINT
        if key_fingerprint is None
        else key_fingerprint
    )
    if pinned_key is None or pinned_fingerprint is None:
        raise CapacityExecutionError(
            "capacity owner signing key is not pinned; reviewer dispatch remains blocked"
        )
    source = Path(path).resolve()
    signature_path = source.with_name(f"{source.name}.sig")
    raw = _stable_read(source, subject="capacity authorization")
    if raw != expected_raw:
        raise CapacityExecutionError(
            "capacity authorization changed before signature verification"
        )
    signature_raw = _stable_read(
        signature_path, subject="capacity authorization signature"
    )
    if not signature_raw:
        raise CapacityExecutionError("capacity authorization signature is empty")
    verifier = Path(ssh_keygen_path or SSH_KEYGEN_PATH)
    if not verifier.is_file():
        raise CapacityExecutionError("capacity authorization signature verifier is unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="phase3-capacity-auth-") as temp_text:
            temporary = Path(temp_text)
            public_key_path = temporary / "owner.pub"
            allowed_signers = temporary / "allowed_signers"
            public_key_path.write_text(
                pinned_key.strip() + "\n", encoding="utf-8", newline="\n"
            )
            allowed_signers.write_text(
                f'{CAPACITY_SIGNATURE_PRINCIPAL} namespaces="{CAPACITY_SIGNATURE_NAMESPACE}" '
                f"{pinned_key.strip()}\n",
                encoding="utf-8",
                newline="\n",
            )
            fingerprint = subprocess.run(
                [str(verifier), "-lf", str(public_key_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            fields = fingerprint.stdout.split()
            observed_fingerprint = fields[1] if len(fields) >= 2 else None
            if fingerprint.returncode != 0 or observed_fingerprint != pinned_fingerprint:
                raise CapacityExecutionError(
                    "capacity owner public key does not match its pinned fingerprint"
                )
            # OpenSSH for Windows 9.5p2 never sees end-of-file on a pipe here, so
            # ``ssh-keygen -Y verify`` blocks until the timeout when the message is fed
            # through ``input=``. Hand it a real file handle over the exact bytes that
            # were read above; the post-verification reread below still binds the file.
            message_path = public_key_path.with_name("message.bin")
            message_path.write_bytes(raw)
            with message_path.open("rb") as message_handle:
                verified = subprocess.run(
                    [
                        str(verifier),
                        "-Y",
                        "verify",
                        "-f",
                        str(allowed_signers),
                        "-I",
                        CAPACITY_SIGNATURE_PRINCIPAL,
                        "-n",
                        CAPACITY_SIGNATURE_NAMESPACE,
                        "-s",
                        str(signature_path),
                    ],
                    stdin=message_handle,
                    capture_output=True,
                    timeout=30,
                )
    except CapacityExecutionError:
        raise
    except subprocess.TimeoutExpired as exc:
        # On OpenSSH for Windows 9.5p2 a signature that does not verify (tampered
        # message or namespace mismatch) leaves ``ssh-keygen -Y verify`` blocked instead of
        # exiting nonzero. A verifier that never confirms the signature is a failed
        # verification; nothing is accepted without exit 0 and the "Good" line below.
        raise CapacityExecutionError(
            "capacity authorization signature is invalid "
            "(verifier did not confirm it before its deadline)"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise CapacityExecutionError(
            "capacity authorization signature verification failed"
        ) from exc
    if verified.returncode != 0 or not verified.stdout.startswith(
        f'Good "{CAPACITY_SIGNATURE_NAMESPACE}" signature for '
        f"{CAPACITY_SIGNATURE_PRINCIPAL}".encode("utf-8")
    ):
        raise CapacityExecutionError("capacity authorization signature is invalid")
    if (
        _stable_read(source, subject="capacity authorization") != raw
        or _stable_read(
            signature_path, subject="capacity authorization signature"
        )
        != signature_raw
    ):
        raise CapacityExecutionError(
            "capacity authorization or signature changed during verification"
        )
    return None


def load_authenticated_capacity_authorization(
    path: Path,
    *,
    public_key: str | None = None,
    key_fingerprint: str | None = None,
    ssh_keygen_path: Path | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Load strict bytes and authenticate them under the capacity namespace."""
    raw, authorization = _load_exact_json(
        Path(path).resolve(), subject="capacity authorization"
    )
    verify_capacity_authorization_signature(
        Path(path).resolve(),
        raw,
        public_key=public_key,
        key_fingerprint=key_fingerprint,
        ssh_keygen_path=ssh_keygen_path,
    )
    return raw, authorization


def _reopen_authority(
    authorization_path: Path,
    *,
    expected_raw_sha256: str,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    observed_at: datetime,
    authorization_verifier: AuthorizationVerifier,
    verify_signature: bool = True,
) -> dict[str, Any]:
    raw, authorization = _load_exact_json(
        authorization_path, subject="capacity authorization"
    )
    if hashlib.sha256(raw).hexdigest() != expected_raw_sha256:
        raise CapacityExecutionError("capacity authorization changed after admission")
    validated = validate_authorization(
        authorization,
        manifest=manifest,
        manifest_raw=manifest_raw,
        observed_at=observed_at,
    )
    if verify_signature:
        authorization_verifier(authorization_path, raw)
    return validated


def _preexecution_runtime_validation(
    manifest: Mapping[str, Any],
    *,
    repository_probe: RepositoryProbe,
    cli_version_reader: CliVersionReader,
    host_reader: HostReader,
    context: CapacityContext,
) -> None:
    repository = cast(Mapping[str, Any], manifest["repository"])
    root = Path(str(repository["project_root"]))
    observed_head, tracked_clean = repository_probe(root)
    if observed_head != repository["head_commit"] or tracked_clean is not True:
        raise CapacityExecutionError("capacity repository identity or tracked state drifted")
    reviewer = cast(Mapping[str, Any], manifest["reviewer"])
    cli = cast(Mapping[str, Any], reviewer["cli"])
    cli_path = str(cli["path"])
    if cli_version_reader(cli_path) != cli["version"]:
        raise CapacityExecutionError("capacity reviewer CLI version drifted")
    if host_reader() != reviewer["host_identity"]:
        raise CapacityExecutionError("capacity reviewer host drifted")
    history_path = Path(str(cast(Mapping[str, Any], manifest["dispatch_history"])["path"]))
    history = capacity.load_bound_dispatch_history(history_path, plan=context.plan)
    try:
        capacity._validate_dispatch_history_chain(  # noqa: SLF001
            history, plan=context.plan, workload=context.workload
        )
    except ValueError as exc:
        raise CapacityExecutionError(f"capacity history validation failed: {exc}") from exc
    if history.events or history.raw_sha256 != hashlib.sha256(b"").hexdigest():
        raise CapacityExecutionError("capacity execution cannot resume or redispatch")
    reservation_path = Path(str(
        cast(Mapping[str, Any], manifest["dispatch_history"])[
            "attempt_reservation_path"
        ]
    ))
    if reservation_path.exists() or reservation_path.is_symlink():
        raise CapacityExecutionError("capacity attempt reservation already exists")
    failure_path = Path(str(
        cast(Mapping[str, Any], manifest["dispatch_history"])[
            "failure_receipt_path"
        ]
    ))
    if failure_path.exists() or failure_path.is_symlink():
        raise CapacityExecutionError("capacity failure receipt already exists")
    if Path(str(manifest["result_path"])).exists():
        raise CapacityExecutionError("capacity result already exists")
    workload = cast(Mapping[str, Any], manifest["workload"])
    for wave in cast(list[Mapping[str, Any]], workload["waves"]):
        wave_dir = Path(str(wave["directory"]))
        if Path(str(wave["output_path"])).exists():
            raise CapacityExecutionError("capacity wave output already exists")
        evidence_root = wave_dir / codex_reviewer_batch.EVIDENCE_DIRECTORY_NAME
        reservation_root = wave_dir / codex_reviewer_batch.DISPATCH_RESERVATION_DIRECTORY_NAME
        if evidence_root.exists() or reservation_root.exists():
            raise CapacityExecutionError("capacity invocation evidence already exists")


def _validate_one_result(
    *,
    packet: Path,
    binding: Mapping[str, Any],
    result: Mapping[str, Any],
    manifest: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    if result.get("ok") is not True or result.get("error") is not None:
        raise CapacityExecutionError(
            f"capacity reviewer failed for {packet.name}: {result.get('error')}"
        )
    if result.get("commands") != []:
        raise CapacityExecutionError(f"capacity reviewer used a tool for {packet.name}")
    if result.get("prompt_sha256") != binding["prompt_sha256"]:
        raise CapacityExecutionError(f"capacity reviewer prompt hash drifted for {packet.name}")
    raw_output = result.get("raw_output")
    if not isinstance(raw_output, str) or raw_output != raw_output.strip():
        raise CapacityExecutionError(f"capacity reviewer output is invalid for {packet.name}")
    if capacity.parse_reviewer_output(raw_output) == (None, None, None):
        raise CapacityExecutionError(f"capacity reviewer output does not parse for {packet.name}")
    reference = result.get("evidence")
    if not isinstance(reference, Mapping):
        raise CapacityExecutionError(f"capacity reviewer evidence is absent for {packet.name}")
    reviewer = cast(Mapping[str, Any], manifest["reviewer"])
    try:
        validation_kwargs: dict[str, Any] = {
            "expected_model": str(reviewer["model"]),
            "expected_effort": str(reviewer["reasoning_effort"]),
            "expected_concurrency": int(reviewer["concurrency"]),
        }
        if "openai_provider_supports_websockets" in reviewer:
            validation_kwargs[
                "expected_openai_provider_supports_websockets"
            ] = reviewer["openai_provider_supports_websockets"]
        if "model_provider_profile" in reviewer:
            validation_kwargs["expected_model_provider_profile"] = reviewer[
                "model_provider_profile"
            ]
        receipt = codex_reviewer_batch.validate_invocation_evidence(
            packet,
            reference,
            **validation_kwargs,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise CapacityExecutionError(
            f"capacity reviewer evidence failed for {packet.name}: {exc}"
        ) from exc
    invocation = cast(Mapping[str, Any], receipt["invocation"])
    cli = cast(Mapping[str, Any], reviewer["cli"])
    if (
        invocation.get("codex_cli_resolved_path") != cli["path"]
        or invocation.get("codex_cli_wrapper_raw_sha256") != cli["raw_sha256"]
        or invocation.get("codex_cli_wrapper_byte_count") != cli["byte_count"]
        or invocation.get("host_identity") != reviewer["host_identity"]
    ):
        raise CapacityExecutionError(
            f"capacity reviewer runtime identity drifted for {packet.name}"
        )
    code = cast(Mapping[str, Any], manifest["code_bindings"])
    runner_binding = cast(Mapping[str, Any], code["reviewer_batch"])
    if (
        invocation.get("batch_runner_path") != runner_binding["path"]
        or invocation.get("batch_runner_raw_sha256") != runner_binding["raw_sha256"]
        or invocation.get("batch_runner_byte_count") != runner_binding["byte_count"]
    ):
        raise CapacityExecutionError(
            f"capacity reviewer batch identity drifted for {packet.name}"
        )
    outcome = cast(Mapping[str, Any], receipt["outcome"])
    if (
        outcome.get("result_ok") is not True
        or outcome.get("dispatch_attempted") is not True
        or outcome.get("timed_out") is not False
        or outcome.get("process_exit_code") != 0
        or outcome.get("commands") != []
        or outcome.get("event_stream_errors") != []
        or outcome.get("error") is not None
    ):
        raise CapacityExecutionError(
            f"capacity reviewer receipt records a failed or non-blind call for {packet.name}"
        )
    deadline = _utc(
        authorization["valid_until_utc"], field="authorization.valid_until_utc"
    )
    if outcome.get("authorization_deadline_utc") != deadline.isoformat():
        raise CapacityExecutionError(
            f"capacity reviewer deadline drifted for {packet.name}"
        )
    started = _utc(outcome.get("started_at_utc"), field="receipt.started_at_utc")
    completed = _utc(outcome.get("completed_at_utc"), field="receipt.completed_at_utc")
    approved = _utc(
        authorization["approved_at_utc"], field="authorization.approved_at_utc"
    )
    if not approved <= started <= completed <= deadline:
        raise CapacityExecutionError(
            f"capacity reviewer invocation is outside authority for {packet.name}"
        )
    normalized = raw_output.strip().encode("utf-8")
    if (
        outcome.get("normalized_ruling_raw_sha256")
        != hashlib.sha256(normalized).hexdigest()
        or outcome.get("normalized_ruling_byte_count") != len(normalized)
    ):
        raise CapacityExecutionError(
            f"capacity reviewer ruling differs from its receipt for {packet.name}"
        )
    receipt_sha = _sha(
        reference.get("receipt_raw_sha256"), field="evidence.receipt_raw_sha256"
    )
    row = {
        "payload_sha256": binding["payload_sha256"],
        "prompt_sha256": binding["prompt_sha256"],
        "ok": True,
        "error": None,
        "commands": [],
        "tool_uses": 0,
        "raw_output": raw_output,
        "invocation_evidence": dict(reference),
    }
    return row, receipt_sha


def _recheck_dispatch_inputs(
    *,
    packet: Path,
    binding: Mapping[str, Any],
    manifest: Mapping[str, Any],
    host_reader: HostReader,
) -> None:
    raw = _stable_read(packet, subject=f"capacity packet {packet.name}")
    if (
        len(raw) != binding["byte_count"]
        or hashlib.sha256(raw).hexdigest() != binding["raw_sha256"]
        or binding["raw_sha256"] != binding["prompt_sha256"]
    ):
        raise CapacityExecutionError(
            f"capacity packet changed immediately before dispatch: {packet.name}"
        )
    reviewer = cast(Mapping[str, Any], manifest["reviewer"])
    cli = cast(Mapping[str, Any], reviewer["cli"])
    _verify_binding(
        {key: cli[key] for key in _BINDING_FIELDS},
        field="reviewer.cli before dispatch",
    )
    code = cast(Mapping[str, Any], manifest["code_bindings"])
    _verify_binding(
        code["reviewer_batch"], field="reviewer batch before dispatch"
    )
    if host_reader() != reviewer["host_identity"]:
        raise CapacityExecutionError("capacity reviewer host changed before dispatch")


def _append_history(
    *,
    context: CapacityContext,
    history_path: Path,
    event_type: str,
    attempt_id: str,
    recorded_at: datetime,
) -> dict[str, Any]:
    try:
        return capacity.append_dispatch_history_event(
            history_path,
            event_type=event_type,
            attempt_id=attempt_id,
            plan=context.plan,
            workload=context.workload,
            recorded_at_utc=recorded_at.astimezone(timezone.utc).isoformat(),
        )
    except ValueError as exc:
        raise CapacityExecutionError(f"capacity history append failed: {exc}") from exc


def _predicted_pass_history(
    *,
    context: CapacityContext,
    history_path: Path,
    attempt_id: str,
    recorded_at: datetime,
) -> capacity.DispatchHistorySnapshot:
    history = capacity.load_bound_dispatch_history(history_path, plan=context.plan)
    if len(history.events) != 1 or history.events[0].get("event") != "dispatch_started":
        raise CapacityExecutionError("capacity completion prediction requires one start event")
    previous = history.events[0]
    event: dict[str, Any] = {
        "schema_version": capacity.DISPATCH_HISTORY_SCHEMA_VERSION,
        "sequence": 1,
        "event": "attempt_completed_pass",
        "attempt_number": 1,
        "cohort_number": 1,
        "attempt_id": attempt_id,
        "plan_canonical_sha256": capacity.canonical_sha256(context.plan),
        "cohort_records_canonical_sha256": context.workload.summary[
            "cohort_records_canonical_sha256s"
        ][0],
        "cohort_variant_prompts_canonical_sha256": context.workload.summary[
            "cohort_variant_prompts_canonical_sha256s"
        ][0],
        "environmental_interruption_evidence": None,
        "recorded_at_utc": recorded_at.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "prev_event_hash": previous["event_hash"],
        "event_hash": "",
    }
    event["event_hash"] = capacity.dispatch_history_event_hash(event)
    current = history_path.read_bytes()
    line = (capacity.canonical_json(event) + "\n").encode("utf-8")
    return capacity.parse_dispatch_history_bytes(
        current + line, source="predicted capacity PASS history"
    )


def _reserve_attempt(
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    authorization: Mapping[str, Any],
    authorization_raw: bytes,
    reserved_at: datetime,
) -> dict[str, Any]:
    history = cast(Mapping[str, Any], manifest["dispatch_history"])
    path = Path(str(history["attempt_reservation_path"]))
    record = {
        "schema_version": ATTEMPT_RESERVATION_SCHEMA,
        "scope": SCOPE,
        "run_id": manifest["run_id"],
        "attempt_id": manifest["attempt_id"],
        "cohort_number": 1,
        "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "manifest_canonical_sha256": canonical_sha256(manifest),
        "authorization_raw_sha256": hashlib.sha256(authorization_raw).hexdigest(),
        "authorization_canonical_sha256": canonical_sha256(authorization),
        "plan_canonical_sha256": cast(Mapping[str, Any], manifest["plan_binding"])[
            "canonical_sha256"
        ],
        "maximum_reviewer_dispatches": PACKET_COUNT,
        "reviewer_usage_limit": dict(
            cast(Mapping[str, Any], authorization["reviewer_usage_limit"])
        ),
        "reserved_at_utc": reserved_at.astimezone(timezone.utc).isoformat(),
    }
    write_json_exclusive(path, record)
    return _artifact_binding(path)


def _reserve_capacity_dispatch(
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    authorization_raw: bytes,
    wave_number: int,
    packet: Path,
    binding: Mapping[str, Any],
    reserved_at: datetime,
) -> dict[str, Any]:
    """Persist one non-replayable reviewer usage unit before child release."""
    payload_sha256 = _sha(
        binding.get("payload_sha256"), field="dispatch binding payload_sha256")
    prompt_sha256 = _sha(
        binding.get("prompt_sha256"), field="dispatch binding prompt_sha256")
    packet_raw_sha256 = _sha(
        binding.get("raw_sha256"), field="dispatch binding raw_sha256")
    position = _positive_int(
        binding.get("position"), field="dispatch binding position")
    reviewer = cast(Mapping[str, Any], manifest["reviewer"])
    cli = cast(Mapping[str, Any], reviewer["cli"])
    runner = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], manifest["code_bindings"])["reviewer_batch"],
    )
    reservation_path = (
        packet.parent
        / codex_reviewer_batch.DISPATCH_RESERVATION_DIRECTORY_NAME
        / f"capacity_{payload_sha256}.json"
    )
    record = {
        "schema_version": DISPATCH_RESERVATION_SCHEMA,
        "scope": SCOPE,
        "run_id": manifest["run_id"],
        "attempt_id": manifest["attempt_id"],
        "cohort_number": 1,
        "wave": wave_number,
        "position": position,
        "packet_file": packet.name,
        "packet_path": packet.resolve().as_posix(),
        "payload_sha256": payload_sha256,
        "prompt_sha256": prompt_sha256,
        "packet_raw_sha256": packet_raw_sha256,
        "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "authorization_raw_sha256": hashlib.sha256(authorization_raw).hexdigest(),
        "reviewer_model": reviewer["model"],
        "reviewer_reasoning_effort": reviewer["reasoning_effort"],
        "reviewer_concurrency": reviewer["concurrency"],
        "reviewer_cli_raw_sha256": cli["raw_sha256"],
        "reviewer_batch_runner_raw_sha256": runner["raw_sha256"],
        "reserved_at_utc": reserved_at.astimezone(timezone.utc).isoformat(),
        "usage_unit": USAGE_UNIT,
        "quantity": 1,
    }
    write_json_exclusive(reservation_path, record)
    return _artifact_binding(reservation_path)


def _validate_capacity_dispatch_reservation(
    reservation_binding: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    authorization: Mapping[str, Any],
    authorization_raw: bytes,
    wave_number: int,
    packet: Path,
    packet_binding: Mapping[str, Any],
) -> str:
    path, raw = _verify_binding(
        reservation_binding, field="capacity dispatch reservation")
    payload_sha256 = _sha(
        packet_binding.get("payload_sha256"), field="packet payload_sha256")
    expected_path = (
        packet.parent
        / codex_reviewer_batch.DISPATCH_RESERVATION_DIRECTORY_NAME
        / f"capacity_{payload_sha256}.json"
    ).resolve()
    if path != expected_path:
        raise CapacityExecutionError("capacity dispatch reservation path drifted")
    record = _strict_object(raw, subject="capacity dispatch reservation")
    if set(record) != _DISPATCH_RESERVATION_FIELDS:
        raise CapacityExecutionError("capacity dispatch reservation fields drifted")
    expected_raw = (
        json.dumps(record, ensure_ascii=False, indent=1) + "\n"
    ).encode("utf-8")
    if raw != expected_raw:
        raise CapacityExecutionError("capacity dispatch reservation bytes drifted")
    reviewer = cast(Mapping[str, Any], manifest["reviewer"])
    cli = cast(Mapping[str, Any], reviewer["cli"])
    runner = cast(
        Mapping[str, Any],
        cast(Mapping[str, Any], manifest["code_bindings"])["reviewer_batch"],
    )
    expected = {
        "schema_version": DISPATCH_RESERVATION_SCHEMA,
        "scope": SCOPE,
        "run_id": manifest["run_id"],
        "attempt_id": manifest["attempt_id"],
        "cohort_number": 1,
        "wave": wave_number,
        "position": packet_binding["position"],
        "packet_file": packet.name,
        "packet_path": packet.resolve().as_posix(),
        "payload_sha256": payload_sha256,
        "prompt_sha256": packet_binding["prompt_sha256"],
        "packet_raw_sha256": packet_binding["raw_sha256"],
        "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "authorization_raw_sha256": hashlib.sha256(authorization_raw).hexdigest(),
        "reviewer_model": reviewer["model"],
        "reviewer_reasoning_effort": reviewer["reasoning_effort"],
        "reviewer_concurrency": reviewer["concurrency"],
        "reviewer_cli_raw_sha256": cli["raw_sha256"],
        "reviewer_batch_runner_raw_sha256": runner["raw_sha256"],
        "usage_unit": USAGE_UNIT,
        "quantity": 1,
    }
    for field, expected_value in expected.items():
        if record.get(field) != expected_value:
            raise CapacityExecutionError(
                f"capacity dispatch reservation {field} drifted")
    reserved_at = _utc(
        record.get("reserved_at_utc"), field="dispatch reservation reserved_at_utc")
    approved_at = _utc(
        authorization["approved_at_utc"], field="authorization approved_at_utc")
    valid_until = _utc(
        authorization["valid_until_utc"], field="authorization valid_until_utc")
    if not approved_at <= reserved_at <= valid_until:
        raise CapacityExecutionError(
            "capacity dispatch reservation lies outside signed authority")
    return payload_sha256


def _ordered_dispatch_reservations(
    manifest: Mapping[str, Any],
    reservations_by_payload: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return the observed reservation subset in frozen manifest order."""
    ordered: list[dict[str, Any]] = []
    observed_payloads: set[str] = set()
    workload = cast(Mapping[str, Any], manifest["workload"])
    for wave in cast(list[Mapping[str, Any]], workload["waves"]):
        for binding in cast(list[Mapping[str, Any]], wave["packet_bindings"]):
            payload_sha256 = _sha(
                binding.get("payload_sha256"), field="packet payload_sha256"
            )
            reservation = reservations_by_payload.get(payload_sha256)
            if reservation is not None:
                ordered.append(dict(reservation))
                observed_payloads.add(payload_sha256)
    if observed_payloads != set(reservations_by_payload):
        raise CapacityExecutionError(
            "capacity dispatch reservations contain an undeclared payload"
        )
    return ordered


def audit_capacity_dispatch_reservations(
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    authorization: Mapping[str, Any],
    authorization_raw: bytes,
) -> dict[str, Any]:
    """Reconstruct the exact conservative usage subset after an interruption."""
    if _strict_object(manifest_raw, subject="capacity manifest") != dict(manifest):
        raise CapacityExecutionError("capacity manifest differs from supplied bytes")
    if _strict_object(
        authorization_raw, subject="capacity authorization"
    ) != dict(authorization):
        raise CapacityExecutionError("capacity authorization differs from supplied bytes")
    approved_at = _utc(
        authorization.get("approved_at_utc"),
        field="authorization approved_at_utc",
    )
    validated_authorization = validate_authorization(
        authorization,
        manifest=manifest,
        manifest_raw=manifest_raw,
        observed_at=approved_at,
    )
    reservations_by_payload: dict[str, dict[str, Any]] = {}
    expected_paths: set[Path] = set()
    workload = cast(Mapping[str, Any], manifest["workload"])
    for wave in cast(list[Mapping[str, Any]], workload["waves"]):
        wave_number = _positive_int(wave.get("wave"), field="workload wave")
        wave_dir = Path(str(wave["directory"]))
        reservation_root = (
            wave_dir / codex_reviewer_batch.DISPATCH_RESERVATION_DIRECTORY_NAME
        )
        bindings = cast(list[Mapping[str, Any]], wave["packet_bindings"])
        for packet_binding in bindings:
            payload_sha256 = _sha(
                packet_binding.get("payload_sha256"),
                field="packet payload_sha256",
            )
            expected_path = (
                reservation_root / f"capacity_{payload_sha256}.json"
            ).resolve()
            expected_paths.add(expected_path)
            if not expected_path.exists():
                continue
            reservation_binding = _artifact_binding(expected_path)
            observed_payload = _validate_capacity_dispatch_reservation(
                reservation_binding,
                manifest=manifest,
                manifest_raw=manifest_raw,
                authorization=validated_authorization,
                authorization_raw=authorization_raw,
                wave_number=wave_number,
                packet=wave_dir / str(packet_binding["file"]),
                packet_binding=packet_binding,
            )
            if observed_payload in reservations_by_payload:
                raise CapacityExecutionError(
                    "capacity dispatch reservation repeats a payload"
                )
            reservations_by_payload[observed_payload] = reservation_binding
        if reservation_root.exists():
            if reservation_root.is_symlink() or not reservation_root.is_dir():
                raise CapacityExecutionError(
                    "capacity dispatch reservation root is not a plain directory"
                )
            for candidate in reservation_root.iterdir():
                if candidate.is_symlink() or not candidate.is_file():
                    raise CapacityExecutionError(
                        "capacity dispatch reservation directory has an invalid entry"
                    )
                if candidate.resolve() not in expected_paths:
                    raise CapacityExecutionError(
                        "capacity dispatch reservation directory has an undeclared entry"
                    )
    ordered = _ordered_dispatch_reservations(manifest, reservations_by_payload)
    authorized_usage = cast(
        Mapping[str, Any], validated_authorization["reviewer_usage_limit"]
    )
    if len(ordered) > int(authorized_usage["maximum"]):
        raise CapacityExecutionError(
            "capacity dispatch reservations exceed signed authority"
        )
    return {
        "schema_version": "phase3_main_capacity_dispatch_reservation_audit_v1",
        "usage_unit": USAGE_UNIT,
        "authorized_maximum": authorized_usage["maximum"],
        "observed_quantity": len(ordered),
        "dispatch_reservations": ordered,
        "accounting_treatment": authorized_usage["accounting_treatment"],
        "resume_or_redispatch_authorized": False,
        "non_claim": "reservation count is not USD or token accounting",
    }


def _write_failure_receipt(
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    authorization_raw: bytes,
    attempted_dispatches: int,
    dispatch_reservations: Sequence[Mapping[str, Any]],
    valid_receipt_hashes: Sequence[str],
    failed_at: datetime,
    error: BaseException,
) -> None:
    history = cast(Mapping[str, Any], manifest["dispatch_history"])
    record = {
        "schema_version": "phase3_main_capacity_failure_receipt_v1",
        "scope": SCOPE,
        "run_id": manifest["run_id"],
        "attempt_id": manifest["attempt_id"],
        "cohort_number": 1,
        "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "authorization_raw_sha256": hashlib.sha256(authorization_raw).hexdigest(),
        "usage_unit": USAGE_UNIT,
        "attempted_dispatches": attempted_dispatches,
        "dispatch_reservation_count": len(dispatch_reservations),
        "dispatch_reservations": [dict(binding) for binding in dispatch_reservations],
        "validated_invocation_receipts": len(valid_receipt_hashes),
        "invocation_receipt_raw_sha256s": list(valid_receipt_hashes),
        "failed_at_utc": failed_at.astimezone(timezone.utc).isoformat(),
        "error_type": type(error).__name__,
        "error": str(error),
        "non_claim": "dispatch count is not USD or token accounting",
    }
    write_json_exclusive(Path(str(history["failure_receipt_path"])), record)


def _validate_execution_result(
    result: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    authorization: Mapping[str, Any],
    authorization_raw: bytes,
    context: CapacityContext,
    as_of_utc: datetime,
    require_current_freshness: bool,
    dispatch_history: capacity.DispatchHistorySnapshot | None = None,
    _validate_history_anchors: bool,
) -> dict[str, Any]:
    """Join result evidence with one private predicted-history anchor mode."""
    if result.get("execution_manifest_raw_sha256") != hashlib.sha256(manifest_raw).hexdigest():
        raise CapacityExecutionError("capacity result manifest binding drifted")
    if result.get("authorization_raw_sha256") != hashlib.sha256(authorization_raw).hexdigest():
        raise CapacityExecutionError("capacity result authorization binding drifted")
    if result.get("downstream_launch_admission") != {
        "eligible": False,
        "reason": DOWNSTREAM_LAUNCH_BLOCKER,
    }:
        raise CapacityExecutionError(
            "capacity result must remain ineligible for downstream launch admission"
        )
    reservation = result.get("attempt_reservation")
    if not isinstance(reservation, Mapping):
        raise CapacityExecutionError("capacity result omits its attempt reservation")
    reservation_path, reservation_raw = _verify_binding(
        reservation, field="capacity attempt reservation"
    )
    expected_reservation_path = Path(str(
        cast(Mapping[str, Any], manifest["dispatch_history"])[
            "attempt_reservation_path"
        ]
    ))
    if reservation_path != expected_reservation_path:
        raise CapacityExecutionError("capacity attempt reservation path drifted")
    reservation_record = _strict_object(
        reservation_raw, subject="capacity attempt reservation"
    )
    if (
        reservation_record.get("schema_version") != ATTEMPT_RESERVATION_SCHEMA
        or reservation_record.get("manifest_raw_sha256")
        != hashlib.sha256(manifest_raw).hexdigest()
        or reservation_record.get("authorization_raw_sha256")
        != hashlib.sha256(authorization_raw).hexdigest()
        or reservation_record.get("attempt_id") != manifest["attempt_id"]
    ):
        raise CapacityExecutionError("capacity attempt reservation semantics drifted")
    history_path = Path(str(cast(Mapping[str, Any], manifest["dispatch_history"])["path"]))
    history = dispatch_history or capacity.load_bound_dispatch_history(
        history_path, plan=context.plan
    )
    try:
        base_validation = capacity._validate_result(  # noqa: SLF001
            result,
            plan=context.plan,
            workload=context.workload,
            dispatch_history=history,
            as_of_utc=as_of_utc,
            require_current_freshness=require_current_freshness,
            _validate_history_anchors=_validate_history_anchors,
        )
        if not _validate_history_anchors:
            base_validation = {
                **base_validation,
                "validation": "predicted_pass_prepublication",
            }
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CapacityExecutionError):
            raise
        raise CapacityExecutionError(f"capacity result validation failed: {exc}") from exc
    if len(history.events) != 2:
        raise CapacityExecutionError("capacity result requires one start and one terminal event")
    started_event, terminal_event = history.events
    if (
        started_event.get("event") != "dispatch_started"
        or terminal_event.get("event") != "attempt_completed_pass"
        or started_event.get("attempt_id") != manifest["attempt_id"]
        or terminal_event.get("attempt_id") != manifest["attempt_id"]
        or started_event.get("recorded_at_utc") != result.get("started_at_utc")
        or terminal_event.get("recorded_at_utc") != result.get("completed_at_utc")
    ):
        raise CapacityExecutionError("capacity result timeline differs from dispatch history")
    usage = result.get("reviewer_usage_receipt")
    if not isinstance(usage, Mapping) or set(usage) != {
        "schema_version",
        "unit",
        "authorized_maximum",
        "observed_quantity",
        "dispatch_reservations",
        "invocation_receipt_raw_sha256s",
        "accounting_treatment",
        "non_claim",
    }:
        raise CapacityExecutionError("capacity usage receipt fields drifted")
    authorized_usage = cast(Mapping[str, Any], authorization["reviewer_usage_limit"])
    if (
        usage.get("schema_version") != USAGE_RECEIPT_SCHEMA
        or usage.get("unit") != authorized_usage["unit"]
        or usage.get("authorized_maximum") != authorized_usage["maximum"]
        or usage.get("observed_quantity") != PACKET_COUNT
        or usage.get("accounting_treatment")
        != authorized_usage["accounting_treatment"]
        or usage.get("non_claim")
        != "dispatch count is not USD or token accounting"
    ):
        raise CapacityExecutionError("capacity usage receipt exceeds or differs from authority")
    declared_receipts = usage.get("invocation_receipt_raw_sha256s")
    if not isinstance(declared_receipts, list) or len(declared_receipts) != PACKET_COUNT:
        raise CapacityExecutionError("capacity usage receipt must bind 180 invocations")
    declared_reservations = usage.get("dispatch_reservations")
    if (
        not isinstance(declared_reservations, list)
        or len(declared_reservations) != PACKET_COUNT
    ):
        raise CapacityExecutionError("capacity usage receipt must bind 180 reservations")
    observed_receipts: list[str] = []
    observed_reservation_payloads: list[str] = []
    reservation_position = 0
    waves = result.get("waves")
    manifest_waves = cast(list[Mapping[str, Any]], cast(Mapping[str, Any], manifest["workload"])["waves"])
    if not isinstance(waves, list) or len(waves) != WAVE_COUNT:
        raise CapacityExecutionError("capacity result must contain three waves")
    for wave, manifest_wave in zip(waves, manifest_waves):
        if not isinstance(wave, Mapping):
            raise CapacityExecutionError("capacity result wave is invalid")
        rows = wave.get("results")
        bindings = cast(list[Mapping[str, Any]], manifest_wave["packet_bindings"])
        if not isinstance(rows, list) or len(rows) != WAVE_SIZE:
            raise CapacityExecutionError("capacity result wave must contain 60 rows")
        durable_output = wave.get("durable_output")
        if not isinstance(durable_output, Mapping):
            raise CapacityExecutionError("capacity result wave omits durable output")
        output_path, output_raw = _verify_binding(
            durable_output, field=f"capacity wave {wave.get('wave')} output"
        )
        if output_path != Path(str(manifest_wave["output_path"])):
            raise CapacityExecutionError("capacity wave durable output path drifted")
        expected_output_raw = b"".join(
            (capacity.canonical_json(row) + "\n").encode("utf-8")
            for row in rows
        )
        if output_raw != expected_output_raw:
            raise CapacityExecutionError("capacity wave durable output bytes drifted")
        wave_dir = Path(str(manifest_wave["directory"]))
        for row, binding in zip(rows, bindings):
            if not isinstance(row, Mapping):
                raise CapacityExecutionError("capacity result row is invalid")
            declared_reservation = declared_reservations[reservation_position]
            reservation_position += 1
            if not isinstance(declared_reservation, Mapping):
                raise CapacityExecutionError(
                    "capacity usage receipt reservation binding is invalid"
                )
            observed_reservation_payloads.append(
                _validate_capacity_dispatch_reservation(
                    declared_reservation,
                    manifest=manifest,
                    manifest_raw=manifest_raw,
                    authorization=authorization,
                    authorization_raw=authorization_raw,
                    wave_number=_positive_int(
                        manifest_wave.get("wave"), field="manifest wave"
                    ),
                    packet=wave_dir / str(binding["file"]),
                    packet_binding=binding,
                )
            )
            reference = row.get("invocation_evidence")
            if not isinstance(reference, Mapping):
                raise CapacityExecutionError("capacity result row omits invocation evidence")
            rebuilt, receipt_sha = _validate_one_result(
                packet=wave_dir / str(binding["file"]),
                binding=binding,
                result={
                    "ok": row.get("ok"),
                    "error": row.get("error"),
                    "commands": row.get("commands"),
                    "prompt_sha256": row.get("prompt_sha256"),
                    "raw_output": row.get("raw_output"),
                    "evidence": reference,
                },
                manifest=manifest,
                authorization=authorization,
            )
            if dict(row) != rebuilt:
                raise CapacityExecutionError("capacity result row differs from its receipt")
            observed_receipts.append(receipt_sha)
    if declared_receipts != observed_receipts or len(set(observed_receipts)) != PACKET_COUNT:
        raise CapacityExecutionError("capacity usage receipt list drifted or repeats a dispatch")
    expected_payloads = [
        str(binding["payload_sha256"])
        for manifest_wave in manifest_waves
        for binding in cast(
            list[Mapping[str, Any]], manifest_wave["packet_bindings"]
        )
    ]
    if (
        observed_reservation_payloads != expected_payloads
        or len(set(observed_reservation_payloads)) != PACKET_COUNT
    ):
        raise CapacityExecutionError(
            "capacity reservation list drifted or repeats a dispatch"
        )
    return {
        **base_validation,
        "history_event_count": 2,
        "reopened_dispatch_reservations": PACKET_COUNT,
        "reopened_invocation_receipts": PACKET_COUNT,
        "usage_unit": USAGE_UNIT,
        "observed_usage": PACKET_COUNT,
    }


def validate_execution_result(
    result: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_raw: bytes,
    authorization: Mapping[str, Any],
    authorization_raw: bytes,
    context: CapacityContext,
    as_of_utc: datetime,
    require_current_freshness: bool = True,
    dispatch_history: capacity.DispatchHistorySnapshot | None = None,
) -> dict[str, Any]:
    """Validate durable execution evidence with dispatch anchors always enforced."""
    return _validate_execution_result(
        result,
        manifest=manifest,
        manifest_raw=manifest_raw,
        authorization=authorization,
        authorization_raw=authorization_raw,
        context=context,
        as_of_utc=as_of_utc,
        require_current_freshness=require_current_freshness,
        dispatch_history=dispatch_history,
        _validate_history_anchors=True,
    )


def _execute_capacity_preflight(
    *,
    manifest_path: Path,
    authorization_path: Path,
    context: CapacityContext,
    reviewer_runner: ReviewerRunner,
    monotonic: Clock,
    utc_now: UtcNow,
    repository_probe: RepositoryProbe,
    cli_version_reader: CliVersionReader,
    host_reader: HostReader,
    authorization_verifier: AuthorizationVerifier,
) -> dict[str, Any]:
    """Run exactly cohort 1 after exact authority, retaining every invocation receipt."""
    resolved_manifest = Path(manifest_path).resolve()
    resolved_authorization = Path(authorization_path).resolve()
    manifest_raw, manifest = load_execution_manifest(
        resolved_manifest, context=context
    )
    authorization_raw, authorization_object = _load_exact_json(
        resolved_authorization, subject="capacity authorization"
    )
    admitted_at = utc_now()
    authorization = validate_authorization(
        authorization_object,
        manifest=manifest,
        manifest_raw=manifest_raw,
        observed_at=admitted_at,
    )
    authorization_verifier(resolved_authorization, authorization_raw)
    authorization_raw_sha = hashlib.sha256(authorization_raw).hexdigest()

    _preexecution_runtime_validation(
        manifest,
        repository_probe=repository_probe,
        cli_version_reader=cli_version_reader,
        host_reader=host_reader,
        context=context,
    )
    _reopen_authority(
        resolved_authorization,
        expected_raw_sha256=authorization_raw_sha,
        manifest=manifest,
        manifest_raw=manifest_raw,
        observed_at=utc_now(),
        authorization_verifier=authorization_verifier,
    )

    history_path = Path(str(cast(Mapping[str, Any], manifest["dispatch_history"])["path"]))
    attempt_id = str(manifest["attempt_id"])
    reservation_at = utc_now().astimezone(timezone.utc)
    attempt_reservation = _reserve_attempt(
        manifest=manifest,
        manifest_raw=manifest_raw,
        authorization=authorization,
        authorization_raw=authorization_raw,
        reserved_at=reservation_at,
    )
    attempt_started_utc = utc_now().astimezone(timezone.utc)
    attempt_started_monotonic = monotonic()
    _append_history(
        context=context,
        history_path=history_path,
        event_type="dispatch_started",
        attempt_id=attempt_id,
        recorded_at=attempt_started_utc,
    )
    terminal_appended = False
    reviewer = cast(Mapping[str, Any], manifest["reviewer"])
    cli = cast(Mapping[str, Any], reviewer["cli"])
    deadline = _utc(
        authorization["valid_until_utc"], field="authorization.valid_until_utc"
    )
    result_waves: list[dict[str, Any]] = []
    receipt_hashes: list[str] = []
    attempted_dispatches = 0
    dispatch_reservations_by_payload: dict[str, dict[str, Any]] = {}
    attempted_dispatches_lock = threading.Lock()
    try:
        waves = cast(
            list[Mapping[str, Any]], cast(Mapping[str, Any], manifest["workload"])["waves"]
        )
        for wave in waves:
            wave_number = int(wave["wave"])
            _reopen_authority(
                resolved_authorization,
                expected_raw_sha256=authorization_raw_sha,
                manifest=manifest,
                manifest_raw=manifest_raw,
                observed_at=utc_now(),
                authorization_verifier=authorization_verifier,
            )
            wave_started = monotonic()
            wave_dir = Path(str(wave["directory"]))
            bindings = cast(list[Mapping[str, Any]], wave["packet_bindings"])

            def invoke(binding: Mapping[str, Any]) -> Mapping[str, Any]:
                nonlocal attempted_dispatches
                reserved_at = utc_now().astimezone(timezone.utc)
                _reopen_authority(
                    resolved_authorization,
                    expected_raw_sha256=authorization_raw_sha,
                    manifest=manifest,
                    manifest_raw=manifest_raw,
                    observed_at=reserved_at,
                    authorization_verifier=authorization_verifier,
                    verify_signature=False,
                )
                packet = wave_dir / str(binding["file"])
                _recheck_dispatch_inputs(
                    packet=packet,
                    binding=binding,
                    manifest=manifest,
                    host_reader=host_reader,
                )
                dispatch_reservation = _reserve_capacity_dispatch(
                    manifest=manifest,
                    manifest_raw=manifest_raw,
                    authorization_raw=authorization_raw,
                    wave_number=wave_number,
                    packet=packet,
                    binding=binding,
                    reserved_at=reserved_at,
                )
                payload_sha256 = _sha(
                    binding.get("payload_sha256"),
                    field="dispatch binding payload_sha256",
                )
                with attempted_dispatches_lock:
                    if payload_sha256 in dispatch_reservations_by_payload:
                        raise CapacityExecutionError(
                            "capacity dispatch reservation repeats a payload"
                        )
                    dispatch_reservations_by_payload[payload_sha256] = (
                        dispatch_reservation
                    )
                    attempted_dispatches += 1
                reviewer_kwargs: dict[str, Any] = {
                    "packet": packet,
                    "model": str(reviewer["model"]),
                    "effort": str(reviewer["reasoning_effort"]),
                    "codex": str(cli["path"]),
                    "not_after_utc": deadline,
                    "batch_concurrency": int(reviewer["concurrency"]),
                    "expected_prompt_raw_sha256": str(binding["raw_sha256"]),
                    "expected_cli_raw_sha256": str(cli["raw_sha256"]),
                    "expected_batch_runner_raw_sha256": str(
                        cast(Mapping[str, Any], manifest["code_bindings"])[
                            "reviewer_batch"
                        ]["raw_sha256"]
                    ),
                }
                if "openai_provider_supports_websockets" in reviewer:
                    reviewer_kwargs[
                        "openai_provider_supports_websockets"
                    ] = reviewer["openai_provider_supports_websockets"]
                if "model_provider_profile" in reviewer:
                    reviewer_kwargs["model_provider_profile"] = reviewer[
                        "model_provider_profile"
                    ]
                return reviewer_runner(
                    **reviewer_kwargs,
                )

            ordered_results: list[Mapping[str, Any] | None] = [None] * WAVE_SIZE
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=int(reviewer["concurrency"])
            ) as pool:
                futures = {
                    pool.submit(invoke, binding): position
                    for position, binding in enumerate(bindings)
                }
                for future in concurrent.futures.as_completed(futures):
                    ordered_results[futures[future]] = future.result()
            rows: list[dict[str, Any]] = []
            for binding, reviewer_result in zip(bindings, ordered_results):
                if reviewer_result is None:
                    raise CapacityExecutionError("capacity reviewer returned no result")
                row, receipt_sha = _validate_one_result(
                    packet=wave_dir / str(binding["file"]),
                    binding=binding,
                    result=reviewer_result,
                    manifest=manifest,
                    authorization=authorization,
                )
                rows.append(row)
                receipt_hashes.append(receipt_sha)
            wave_output_raw = b"".join(
                (capacity.canonical_json(row) + "\n").encode("utf-8")
                for row in rows
            )
            wave_output_path = Path(str(wave["output_path"]))
            _write_exclusive(wave_output_path, wave_output_raw)
            wave_output_binding = _artifact_binding(wave_output_path)
            wave_completed = monotonic()
            wave_elapsed = wave_completed - wave_started
            if wave_elapsed < 0 or wave_elapsed > float(
                cast(Mapping[str, Any], manifest["timing_limits"])[
                    "maximum_seconds_per_wave"
                ]
            ):
                raise CapacityExecutionError(
                    f"capacity wave {wave_number} exceeded its timing limit"
                )
            result_waves.append(
                {
                    "wave": wave_number,
                    "attempt_status": "complete",
                    "interrupted": False,
                    "process_exit_code": 0,
                    "failure_counts": {
                        "timeouts": 0,
                        "reviewer_errors": 0,
                        "tool_uses": 0,
                        "prompt_hash_mismatches": 0,
                        "empty_outputs": 0,
                        "unexpected_rows": 0,
                        "duplicate_rows": 0,
                    },
                    "monotonic_started_seconds": wave_started,
                    "monotonic_completed_seconds": wave_completed,
                    "elapsed_monotonic_seconds": wave_elapsed,
                    "expected_payload_sha256s": [
                        binding["payload_sha256"] for binding in bindings
                    ],
                    "results": rows,
                    "durable_output": wave_output_binding,
                }
            )
        attempt_completed_monotonic = monotonic()
        elapsed = attempt_completed_monotonic - attempt_started_monotonic
        if elapsed < 0 or elapsed > float(
            cast(Mapping[str, Any], manifest["timing_limits"])[
                "maximum_seconds_total"
            ]
        ):
            raise CapacityExecutionError("capacity attempt exceeded its timing limit")
        if len(receipt_hashes) != PACKET_COUNT or len(set(receipt_hashes)) != PACKET_COUNT:
            raise CapacityExecutionError("capacity attempt lacks 180 unique invocation receipts")
        if attempted_dispatches != PACKET_COUNT:
            raise CapacityExecutionError("capacity attempt did not release exactly 180 calls")
        ordered_dispatch_reservations = _ordered_dispatch_reservations(
            manifest, dispatch_reservations_by_payload
        )
        if len(ordered_dispatch_reservations) != PACKET_COUNT:
            raise CapacityExecutionError(
                "capacity attempt lacks 180 unique dispatch reservations"
            )
        attempt_completed_utc = utc_now().astimezone(timezone.utc)
        _reopen_authority(
            resolved_authorization,
            expected_raw_sha256=authorization_raw_sha,
            manifest=manifest,
            manifest_raw=manifest_raw,
            observed_at=attempt_completed_utc,
            authorization_verifier=authorization_verifier,
        )
        predicted_history = _predicted_pass_history(
            context=context,
            history_path=history_path,
            attempt_id=attempt_id,
            recorded_at=attempt_completed_utc,
        )
        environment = {
            "reviewer_cli_resolved_path": cli["path"],
            "reviewer_cli_wrapper_raw_sha256": cli["raw_sha256"],
            "reviewer_cli_wrapper_byte_count": cli["byte_count"],
            "reviewer_cli_version": cli["version"],
            "host_identity": reviewer["host_identity"],
        }
        result = {
            "schema_version": capacity.RESULT_SCHEMA_VERSION,
            "attempt_number": 1,
            "cohort_number": 1,
            "attempt_id": attempt_id,
            "attempt_status": "complete",
            "interrupted": False,
            "dispatch_history_raw_sha256": predicted_history.raw_sha256,
            "plan_canonical_sha256": canonical_sha256(context.plan),
            "cohort_records_canonical_sha256": context.workload.summary[
                "cohort_records_canonical_sha256s"
            ][0],
            "reviewer_configuration": context.plan["reviewer_configuration"],
            "reviewer_configuration_canonical_sha256": canonical_sha256(
                context.plan["reviewer_configuration"]
            ),
            "measurement_environment": environment,
            "measurement_environment_canonical_sha256": canonical_sha256(environment),
            "started_at_utc": attempt_started_utc.isoformat().replace("+00:00", "Z"),
            "completed_at_utc": attempt_completed_utc.isoformat().replace("+00:00", "Z"),
            "monotonic_started_seconds": attempt_started_monotonic,
            "monotonic_completed_seconds": attempt_completed_monotonic,
            "elapsed_monotonic_seconds": elapsed,
            "waves": result_waves,
            "execution_manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "authorization_raw_sha256": authorization_raw_sha,
            "attempt_reservation": attempt_reservation,
            "reviewer_usage_receipt": {
                "schema_version": USAGE_RECEIPT_SCHEMA,
                "unit": USAGE_UNIT,
                "authorized_maximum": PACKET_COUNT,
                "observed_quantity": PACKET_COUNT,
                "dispatch_reservations": ordered_dispatch_reservations,
                "invocation_receipt_raw_sha256s": receipt_hashes,
                "accounting_treatment": cast(
                    Mapping[str, Any], authorization["reviewer_usage_limit"]
                )["accounting_treatment"],
                "non_claim": "dispatch count is not USD or token accounting",
            },
            "downstream_launch_admission": {
                "eligible": False,
                "reason": DOWNSTREAM_LAUNCH_BLOCKER,
            },
        }
        result_path = Path(str(manifest["result_path"]))
        _validate_execution_result(
            result,
            manifest=manifest,
            manifest_raw=manifest_raw,
            authorization=authorization,
            authorization_raw=authorization_raw,
            context=context,
            as_of_utc=attempt_completed_utc,
            require_current_freshness=True,
            dispatch_history=predicted_history,
            _validate_history_anchors=False,
        )
        write_json_exclusive(result_path, result)
        append = _append_history(
            context=context,
            history_path=history_path,
            event_type="attempt_completed_pass",
            attempt_id=attempt_id,
            recorded_at=attempt_completed_utc,
        )
        terminal_appended = True
        if append["dispatch_history_raw_sha256"] != predicted_history.raw_sha256:
            raise CapacityExecutionError(
                "capacity PASS history differs from its durable result prediction"
            )
        reopened_raw, reopened = _load_exact_json(
            result_path, subject="capacity result"
        )
        if reopened != result:
            raise CapacityExecutionError("capacity result changed during durable write")
        reopened_validation = validate_execution_result(
            reopened,
            manifest=manifest,
            manifest_raw=manifest_raw,
            authorization=authorization,
            authorization_raw=authorization_raw,
            context=context,
            as_of_utc=attempt_completed_utc,
        )
        return {
            "execution": "pass",
            "result_path": result_path.resolve().as_posix(),
            "result_raw_sha256": hashlib.sha256(reopened_raw).hexdigest(),
            "validation": reopened_validation,
        }
    except BaseException as exc:
        if not terminal_appended:
            failed_at = utc_now().astimezone(timezone.utc)
            try:
                reservation_audit = audit_capacity_dispatch_reservations(
                    manifest=manifest,
                    manifest_raw=manifest_raw,
                    authorization=authorization,
                    authorization_raw=authorization_raw,
                )
                durable_dispatch_reservations = cast(
                    list[Mapping[str, Any]],
                    reservation_audit["dispatch_reservations"],
                )
                durable_dispatch_count = _nonnegative_int(
                    reservation_audit["observed_quantity"],
                    field="capacity dispatch reservation audit observed_quantity",
                )
                if attempted_dispatches > durable_dispatch_count:
                    raise CapacityExecutionError(
                        "capacity dispatch reservation disappeared before failure accounting"
                    )
                _write_failure_receipt(
                    manifest=manifest,
                    manifest_raw=manifest_raw,
                    authorization_raw=authorization_raw,
                    attempted_dispatches=durable_dispatch_count,
                    dispatch_reservations=durable_dispatch_reservations,
                    valid_receipt_hashes=receipt_hashes,
                    failed_at=failed_at,
                    error=exc,
                )
                _append_history(
                    context=context,
                    history_path=history_path,
                    event_type="attempt_completed_fail",
                    attempt_id=attempt_id,
                    recorded_at=failed_at,
                )
            except BaseException as history_exc:
                raise CapacityExecutionError(
                    f"capacity execution failed and terminal history append failed: {history_exc}"
                ) from exc
        if isinstance(exc, CapacityExecutionError):
            raise
        raise CapacityExecutionError(f"capacity execution failed: {exc}") from exc


def execute_capacity_preflight(
    *,
    manifest_path: Path,
    authorization_path: Path,
    context: CapacityContext,
) -> dict[str, Any]:
    """Run one signed cohort with fixed production dependencies and no injection seam."""
    return _execute_capacity_preflight(
        manifest_path=manifest_path,
        authorization_path=authorization_path,
        context=context,
        reviewer_runner=codex_reviewer_batch.run_one,
        monotonic=time.monotonic,
        utc_now=_default_utc_now,
        repository_probe=_default_repository_probe,
        cli_version_reader=_default_cli_version,
        host_reader=_default_host,
        authorization_verifier=verify_capacity_authorization_signature,
    )


def repository_probe(project_root: Path) -> tuple[str, bool]:
    """Public wrapper used by the capacity CLI when building an offline manifest."""
    return _default_repository_probe(Path(project_root).resolve())


def reviewer_cli_version(cli: str) -> str:
    """Public wrapper used by the capacity CLI when building an offline manifest."""
    return _default_cli_version(cli)


def host_identity() -> str:
    """Public wrapper used by the capacity CLI when building an offline manifest."""
    return _default_host()


__all__ = [
    "AUTHORIZATION_SCHEMA",
    "CAPACITY_SIGNATURE_NAMESPACE",
    "CAPACITY_SIGNATURE_PRINCIPAL",
    "CapacityContext",
    "CapacityExecutionError",
    "MANIFEST_SCHEMA",
    "SCOPE",
    "USAGE_LIMIT_SCHEMA",
    "USAGE_RECEIPT_SCHEMA",
    "USAGE_UNIT",
    "audit_capacity_dispatch_reservations",
    "build_execution_manifest",
    "build_unsigned_capacity_authorization",
    "canonical_sha256",
    "execute_capacity_preflight",
    "host_identity",
    "load_capacity_context",
    "load_execution_manifest",
    "load_authenticated_capacity_authorization",
    "repository_probe",
    "reviewer_cli_version",
    "validate_authorization",
    "validate_execution_result",
    "verify_capacity_authorization_signature",
    "write_json_exclusive",
]
