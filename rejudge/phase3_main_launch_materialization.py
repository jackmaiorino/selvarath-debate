"""Deterministically materialize the Phase 3 main launch package, offline only.

The manifest is non-authorizing.  The separate authorization record is also inert until
its exact bytes carry Jack's valid detached SSH signature.  This module never creates a
formal artifact root, identity registry, provider client, or reviewer process.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
from datetime import datetime, timezone
from decimal import (
    Decimal,
    InvalidOperation,
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_UP,
    localcontext,
)
from pathlib import Path
from typing import Any, Mapping

from rejudge import (
    phase3_main_manifest,
    phase3_main_runtime_policies,
    phase3_main_runner,
    phase3_main_stage_cap,
)


ACCOUNT_IDENTITY_SHA256 = (
    "8a54a740aa7098d51327ed0ab1c40b533a005ff5cf20566fff0b4fe44e9d2c3e"
)
ANALYSIS_BOOTSTRAP_SEED = 20260829
SUBSAMPLE_SEED = 20260818
PROVIDER_WORKER_CONCURRENCY = 1
GPU_ORDINAL = "not_applicable"


class MainLaunchMaterializationError(ValueError):
    """An offline launch-package input or output failed closed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MainLaunchMaterializationError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> Any:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda raw: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {raw!r}")
            ),
        )
    except MainLaunchMaterializationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MainLaunchMaterializationError(
            f"could not read {label} at {path}: {exc}"
        ) from exc
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, float) and not math.isfinite(current):
            raise MainLaunchMaterializationError(f"{label} contains a non-finite number")
        if isinstance(current, Mapping):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return value


def _load_object(path: Path, label: str) -> dict[str, Any]:
    value = _load_json(path, label)
    if not isinstance(value, dict):
        raise MainLaunchMaterializationError(f"{label} must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise MainLaunchMaterializationError(f"could not hash input {path}: {exc}") from exc
    return digest.hexdigest()


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise MainLaunchMaterializationError(f"{label} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise MainLaunchMaterializationError(f"{label} must use UTC")
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime, label: str) -> str:
    return _utc(value, label).isoformat().replace("+00:00", "Z")


def _money(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float, str)):
        raise MainLaunchMaterializationError(f"{label} must be a finite decimal")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise MainLaunchMaterializationError(f"{label} must be a finite decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise MainLaunchMaterializationError(f"{label} must be a finite decimal")
    return amount


def _money_text(value: Any, label: str) -> str:
    amount = _money(value, label)
    return format(amount, "f")


def _predecessor_money(value: Any, label: str) -> Decimal:
    amount = _money(value, label)
    with localcontext() as context:
        context.prec = max(100, len(amount.as_tuple().digits) + 10)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _path_text(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _resolve_input_path(value: Any, project_root: Path, name: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise MainLaunchMaterializationError(
            f"input path {name!r} must be a non-empty string"
        )
    path = Path(value)
    if not path.is_absolute() and ".." in path.parts:
        raise MainLaunchMaterializationError(
            f"relative input path {name!r} escapes the project root"
        )
    resolved = (path if path.is_absolute() else project_root / path).resolve()
    if not resolved.is_file():
        raise MainLaunchMaterializationError(f"input {name!r} is missing: {resolved}")
    return resolved


def load_input_paths(path: str | Path, *, project_root: str | Path) -> dict[str, Path]:
    """Load the exact required-name to file-path map for one main manifest."""
    root = Path(project_root).resolve()
    source = Path(path).resolve()
    value = _load_object(source, "main input-path map")
    observed = set(value)
    expected = set(phase3_main_manifest.REQUIRED_INPUT_BINDINGS)
    if observed != expected:
        raise MainLaunchMaterializationError(
            "main input-path names drifted: "
            f"missing={sorted(expected - observed)!r}, "
            f"unexpected={sorted(observed - expected)!r}"
        )
    resolved = {
        name: _resolve_input_path(value[name], root, name)
        for name in sorted(expected)
    }
    authorization = resolved["capacity_execution_authorization"]
    expected_signature = authorization.with_name(f"{authorization.name}.sig")
    if resolved["capacity_execution_authorization_signature"] != expected_signature:
        raise MainLaunchMaterializationError(
            "capacity authorization signature must be the exact detached sidecar"
        )
    return resolved


def repository_probe(project_root: str | Path) -> tuple[str, bool]:
    """Return the current commit and whether tracked plus untracked state is clean."""
    root = Path(project_root).resolve()
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in {"TOGETHER_API_KEY", "TOGETHER_BASE_URL"}
    }
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MainLaunchMaterializationError("could not inspect the Git checkout") from exc
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise MainLaunchMaterializationError("Git HEAD is not a lowercase commit SHA")
    return head, not bool(status)


def _required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MainLaunchMaterializationError(f"{label} must be an object")
    return value


def _build_runtime(
    *, capacity_plan: Mapping[str, Any], billing: Mapping[str, Any]
) -> dict[str, Any]:
    reviewer = _required_mapping(
        capacity_plan.get("reviewer_configuration"),
        "capacity reviewer configuration",
    )
    expected_reviewer = {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "concurrency": 12,
    }
    for name, expected in expected_reviewer.items():
        if reviewer.get(name) != expected:
            raise MainLaunchMaterializationError(
                f"capacity reviewer {name} differs from the main runtime"
            )
    cli = reviewer.get("reviewer_cli_binary")
    if not isinstance(cli, str) or not cli.strip():
        raise MainLaunchMaterializationError("capacity reviewer CLI is missing")
    billing_scope = _required_mapping(billing.get("billing_scope"), "billing scope")
    account = billing_scope.get("account_identity_sha256")
    if account != ACCOUNT_IDENTITY_SHA256:
        raise MainLaunchMaterializationError(
            "billing reconciliation account differs from the owner-selected account"
        )
    return {
        "provider": "Together",
        "provider_account_identity_sha256": account,
        "model_ids": list(phase3_main_runner.CONFIRMED_MAIN_JUDGES),
        "provider_worker_concurrency": PROVIDER_WORKER_CONCURRENCY,
        "reviewer_cli_binary": cli,
        "reviewer_model": reviewer["model"],
        "reviewer_reasoning_effort": reviewer["reasoning_effort"],
        "reviewer_concurrency": reviewer["concurrency"],
        "gpu_ordinal": GPU_ORDINAL,
    }


def _build_spend(
    *, forecast: Mapping[str, Any], stage_cap_ratification: Mapping[str, Any]
) -> dict[str, str]:
    try:
        ratified = phase3_main_stage_cap.validate_stage_cap_ratification(
            stage_cap_ratification
        )
    except phase3_main_stage_cap.StageCapRatificationError as exc:
        raise MainLaunchMaterializationError(
            f"stage-cap ratification failed: {exc}"
        ) from exc
    prior = phase3_main_stage_cap.PREDECESSOR_UPPER_BOUND_USD
    cap = ratified["stage_cap_usd"]
    projected = _money(forecast.get("projected_main_usd"), "projected main spend")
    if forecast.get("certification") != "pass" or forecast.get("within_stage_cap") is not True:
        raise MainLaunchMaterializationError("cost forecast is not an in-cap certification")
    if _predecessor_money(
        forecast.get("cumulative_spend_usd"), "cumulative spend"
    ) != prior:
        raise MainLaunchMaterializationError(
            "cost forecast does not carry the ratified predecessor upper bound"
        )
    if _money(forecast.get("stage_cap_usd"), "forecast stage cap") != cap:
        raise MainLaunchMaterializationError(
            "cost forecast does not carry the owner-ratified stage cap"
        )
    if prior + projected > cap:
        raise MainLaunchMaterializationError("projected stage spend exceeds the ratified cap")
    return {
        "prior_reconciled_usd": _money_text(prior, "predecessor spend"),
        "forecast_main_usd": _money_text(projected, "projected main spend"),
        "stage_cap_usd": _money_text(cap, "stage cap"),
    }


def _build_harness(receipt: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    if receipt.get("schema_version") != "phase3_main_harness_receipt_v2":
        raise MainLaunchMaterializationError("unsupported main harness receipt schema")
    if receipt.get("status") != "bit_identical_pass":
        raise MainLaunchMaterializationError("main harness receipt is not a pass")
    seed = receipt.get("harness_seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise MainLaunchMaterializationError("main harness seed is invalid")
    first = receipt.get("first_output_store_sha256")
    rerun = receipt.get("rerun_output_store_sha256")
    if (
        not isinstance(first, str)
        or len(first) != 64
        or first != rerun
        or any(character not in "0123456789abcdef" for character in first)
    ):
        raise MainLaunchMaterializationError(
            "main harness output stores are not one bit-identical SHA-256"
        )
    return {
        "receipt_input_name": "harness_receipt",
        "seed_name": "harness_seed",
        "status": "bit_identical_pass",
        "first_output_store_sha256": first,
        "rerun_output_store_sha256": rerun,
    }, seed


def build_launch_manifest(
    *,
    project_root: str | Path,
    input_paths: Mapping[str, Path],
    artifact_root: str | Path,
    identity_registry_root: str | Path,
    source_commit: str,
    recorded_at_utc: datetime,
) -> dict[str, Any]:
    """Build and fully validate one non-authorizing initial main manifest."""
    root = Path(project_root).resolve()
    expected_names = set(phase3_main_manifest.REQUIRED_INPUT_BINDINGS)
    if set(input_paths) != expected_names:
        raise MainLaunchMaterializationError("main input-path set is incomplete")
    resolved_inputs = {
        name: Path(input_paths[name]).resolve()
        for name in sorted(expected_names)
    }
    for name, path in resolved_inputs.items():
        if not path.is_file():
            raise MainLaunchMaterializationError(f"input {name!r} is missing: {path}")

    formal = Path(artifact_root).resolve()
    registry = Path(identity_registry_root).resolve()
    if formal.exists() or formal.is_symlink():
        raise MainLaunchMaterializationError(
            f"formal artifact root must not exist before launch: {formal}"
        )
    if formal == registry:
        raise MainLaunchMaterializationError(
            "formal artifact and identity-registry roots must differ"
        )

    capacity_plan = _load_object(resolved_inputs["capacity_plan"], "capacity plan")
    billing = _load_object(
        resolved_inputs["billing_reconciliation"], "billing reconciliation"
    )
    forecast = _load_object(
        resolved_inputs["certified_cost_forecast"], "certified cost forecast"
    )
    ratification = _load_object(
        resolved_inputs["stage_cap_ratification"], "stage-cap ratification"
    )
    receipt = _load_object(resolved_inputs["harness_receipt"], "harness receipt")
    harness, harness_seed = _build_harness(receipt)
    dependency_lock = (root / "uv.lock").resolve()
    if not dependency_lock.is_file():
        raise MainLaunchMaterializationError("the project uv.lock is missing")

    output_paths = {
        name: (formal / filename).as_posix()
        for name, filename in phase3_main_manifest.OUTPUT_FILENAMES.items()
    }
    manifest: dict[str, Any] = {
        "schema_version": phase3_main_manifest.MANIFEST_SCHEMA,
        "stage": "main",
        "run_id": "pending",
        "recorded_at_utc": _utc_text(recorded_at_utc, "recorded_at_utc"),
        "execution_authorized": False,
        "source_commit": source_commit,
        "toolchain": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "dependency_lock_path": _path_text(dependency_lock, root),
            "dependency_lock_raw_sha256": _sha256(dependency_lock),
            "linker_version_or_not_applicable": "not_applicable",
        },
        "seeds": {
            "harness_seed": harness_seed,
            "analysis_bootstrap_seed": ANALYSIS_BOOTSTRAP_SEED,
            "subsample_seed": SUBSAMPLE_SEED,
        },
        "input_bindings": {
            name: {
                "path": _path_text(path, root),
                "sha256": _sha256(path),
                "hash_kind": "raw_sha256",
            }
            for name, path in resolved_inputs.items()
        },
        "inventory": {
            "question_count": phase3_main_runner.EXPECTED_MAIN_QUESTION_COUNT,
            "transcript_count": phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT,
            "judgment_count": phase3_main_runner.EXPECTED_MAIN_JUDGMENT_COUNT,
            "cell_count": phase3_main_runner.EXPECTED_MAIN_CELL_COUNT,
            "canonical_sha256": phase3_main_runner.EXPECTED_MAIN_INVENTORY_SHA256,
        },
        "output_contract": {
            "artifact_root": formal.as_posix(),
            "identity_registry_root": registry.as_posix(),
            "paths": output_paths,
            "sha256s": {name: None for name in output_paths},
        },
        "restart": {
            "mode": phase3_main_manifest.RESTART_MODE_INITIAL,
            "predecessor": None,
        },
        "runtime": _build_runtime(capacity_plan=capacity_plan, billing=billing),
        "spend": _build_spend(
            forecast=forecast,
            stage_cap_ratification=ratification,
        ),
        "harness_check": harness,
        "manifest_identity_sha256": "0" * 64,
    }
    manifest["manifest_identity_sha256"] = (
        phase3_main_manifest.manifest_identity_sha256(manifest)
    )
    manifest["run_id"] = phase3_main_manifest.expected_run_id(manifest)
    try:
        phase3_main_manifest.validate_main_manifest(
            manifest,
            project_root=root,
            verify_files=True,
            verify_runtime=True,
        )
    except phase3_main_manifest.MainManifestError as exc:
        raise MainLaunchMaterializationError(
            f"constructed main manifest failed validation: {exc}"
        ) from exc
    return manifest


def load_main_manifest(
    path: str | Path, *, project_root: str | Path
) -> dict[str, Any]:
    """Strictly load and fully validate one materialized main manifest."""
    manifest = _load_object(Path(path).resolve(), "main manifest")
    try:
        phase3_main_manifest.validate_main_manifest(
            manifest,
            project_root=project_root,
            verify_files=True,
            verify_runtime=True,
        )
    except phase3_main_manifest.MainManifestError as exc:
        raise MainLaunchMaterializationError(
            f"main manifest failed validation: {exc}"
        ) from exc
    return manifest


def build_unsigned_main_authorization(
    *,
    manifest: Mapping[str, Any],
    authorization_id: str,
    approved_at_utc: datetime,
    valid_until_utc: datetime,
) -> dict[str, Any]:
    """Build exact bytes that stay non-authorizing until owner-signed."""
    approved = _utc(approved_at_utc, "approved_at_utc")
    valid_until = _utc(valid_until_utc, "valid_until_utc")
    inputs = _required_mapping(manifest.get("input_bindings"), "manifest inputs")
    runtime = _required_mapping(manifest.get("runtime"), "manifest runtime")
    spend = _required_mapping(manifest.get("spend"), "manifest spend")
    authorization = {
        "schema_version": phase3_main_manifest.AUTHORIZATION_SCHEMA,
        "authorization_id": authorization_id,
        "stage": "main",
        "run_id": manifest.get("run_id"),
        "manifest_canonical_sha256": phase3_main_manifest.manifest_canonical_sha256(
            manifest
        ),
        "manifest_identity_sha256": manifest.get("manifest_identity_sha256"),
        "provider": runtime.get("provider"),
        "provider_account_identity_sha256": runtime.get(
            "provider_account_identity_sha256"
        ),
        "exact_model_ids": runtime.get("model_ids"),
        "reviewer_model": runtime.get("reviewer_model"),
        "reviewer_reasoning_effort": runtime.get("reviewer_reasoning_effort"),
        "reviewer_concurrency": runtime.get("reviewer_concurrency"),
        "stage_cap_usd": spend.get("stage_cap_usd"),
        "stage_cap_ratification_sha256": inputs["stage_cap_ratification"]["sha256"],
        "price_snapshot_sha256": inputs["price_snapshot"]["sha256"],
        "price_change_policy_sha256": inputs["price_change_policy"]["sha256"],
        "prior_reconciliation_sha256": inputs["billing_reconciliation"]["sha256"],
        "forecast_sha256": inputs["certified_cost_forecast"]["sha256"],
        "reviewer_usage_policy_sha256": inputs["reviewer_usage_policy"]["sha256"],
        "maximum_reviewer_dispatches": (
            phase3_main_runtime_policies.MAXIMUM_REVIEWER_DISPATCHES
        ),
        "harness_execution_count": 2,
        "formal_main_attempt_count": 1,
        "approver": "Jack Maiorino",
        "approved_at_utc": _utc_text(approved, "approved_at_utc"),
        "valid_until_utc": _utc_text(valid_until, "valid_until_utc"),
        "exact_text": phase3_main_manifest.expected_authorization_text(manifest),
        "execution_authorized": True,
        "provider_calls_authorized": True,
        "main_run_spend_authorized": True,
        "no_resume": True,
    }
    try:
        phase3_main_manifest.validate_main_authorization(
            authorization,
            manifest,
            as_of=approved,
        )
    except phase3_main_manifest.MainManifestError as exc:
        raise MainLaunchMaterializationError(
            f"constructed unsigned main authorization failed validation: {exc}"
        ) from exc
    return authorization


def write_json_exclusive(path: str | Path, value: Mapping[str, Any]) -> bytes:
    """Publish deterministic JSON once and return its exact bytes."""
    destination = Path(path).resolve()
    if destination.exists() or destination.is_symlink():
        raise MainLaunchMaterializationError(
            f"refusing to overwrite launch artifact: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(dict(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    try:
        with destination.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise MainLaunchMaterializationError(
            f"could not publish launch artifact {destination}: {exc}"
        ) from exc
    if destination.read_bytes() != raw:
        raise MainLaunchMaterializationError(
            f"launch artifact changed during publication: {destination}"
        )
    return raw


__all__ = [
    "ACCOUNT_IDENTITY_SHA256",
    "MainLaunchMaterializationError",
    "build_launch_manifest",
    "build_unsigned_main_authorization",
    "load_input_paths",
    "load_main_manifest",
    "repository_probe",
    "write_json_exclusive",
]
