"""Offline launch validator and blocked driver skeleton for the Phase 3 main measurement.

The public preparation path is read-only. It derives the exact main inventory and validates
every bound input before accepting a separate, active owner authorization. The public run path
has no client, factory, path, concurrency, cap, or resume injection points. It acquires one
persistent registry lease, consumes the identity, creates fresh formal stores, and only then
constructs the private provider client.

Importing this module and calling :func:`load_prepared_main` cannot create a provider client or
mutate formal output state. The public paid path also refuses before formal state mutation until
the external authority and provenance blockers listed below are implemented.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from rejudge import (
    api_client,
    phase3_main_authorization,
    phase3_main_billing_reconciliation,
    phase3_main_harness,
    phase3_main_context,
    phase3_main_finalization,
    phase3_main_manifest,
    phase3_main_runner,
    phase3_plan,
    phase3_runner,
    phase3_v3_forecast,
    phase3_v3_inputs,
    phase3_v3_live,
)
from rejudge.phase2_canary_execute import GenerationForbiddenError
from rejudge.phase2_canary_live import (
    RoleLimitResolvingClient,
    _PauseModeReviewer,
    commit_decisions_into,
    export_reviewer_worklist,
)
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_canary_runner import run_canary
from rejudge.phase2_dual_gate import DualGateDecisionStore
from rejudge.phase2_execution import canonical_sha256
from rejudge.request_journal import JournalingClient, RequestJournal, find_ambiguous_dispatches
from scripts import phase3_main_analysis, phase3_main_review_capacity_preflight
from scripts.phase3_preseed_transcripts import _rows_for_bundle, _verify_bundle_hash


IDENTITY_START_SCHEMA = "phase3_main_identity_start_v1"
IDENTITY_COMPLETE_SCHEMA = "phase3_main_identity_complete_v1"
AUTHORIZATION_CONSUMED_SCHEMA = "phase3_main_authorization_consumed_v2"
REVIEWER_PROMPT_SCHEMA = "phase2_reviewer_prompt_v1"
LIVE_PROJECT_ROOT = Path(__file__).resolve().parents[1]
OWNER_SIGNATURE_NAMESPACE = phase3_main_authorization.OWNER_SIGNATURE_NAMESPACE
OWNER_SIGNATURE_PRINCIPAL = phase3_main_authorization.OWNER_SIGNATURE_PRINCIPAL
OWNER_SIGNING_PUBLIC_KEY = phase3_main_authorization.OWNER_SIGNING_PUBLIC_KEY
OWNER_SIGNING_KEY_FINGERPRINT = (
    phase3_main_authorization.OWNER_SIGNING_KEY_FINGERPRINT)
SSH_KEYGEN_PATH = phase3_main_authorization.SSH_KEYGEN_PATH
PRODUCTION_EXECUTION_BLOCKERS = (
    "the owner signing key is not pinned",
    "provider-authenticated billing evidence is not implemented",
    "the runtime credential is not bound to the reconciled provider account",
    "predecessor-ledger completeness has no independent authoritative inventory",
    "one-attempt consumption has no non-resettable external authority store",
    "the signed run has no authorized response to in-run provider price changes",
    "Codex reviewer authority and spend accounting are unresolved",
    "non-verdict provider request fingerprints are not independently reconstructed",
    "reviewer packet and worklist provenance are not independently revalidated",
)


class Phase3MainLiveError(RuntimeError, ValueError):
    """A Phase 3 main launch or execution invariant failed closed."""


def _require_production_execution_unblocked() -> None:
    """Keep the public paid path closed until its external authority adapters exist."""
    if PRODUCTION_EXECUTION_BLOCKERS:
        raise Phase3MainLiveError(
            "formal main execution is intentionally blocked: "
            + "; ".join(PRODUCTION_EXECUTION_BLOCKERS))


def _verify_execution_code_root(root: Path) -> None:
    """Require every loaded project module to match its path in the verified checkout."""
    root = root.resolve()
    offenders: list[str] = []
    for name, module in sorted(sys.modules.items()):
        if not (
            name == "rejudge"
            or name.startswith("rejudge.")
            or name == "scripts"
            or name.startswith("scripts.")
        ):
            continue
        raw_path = getattr(module, "__file__", None)
        if raw_path is None:
            if name in {"scripts"}:
                continue
            offenders.append(f"{name}=<no file>")
            continue
        module_path = Path(raw_path).resolve()
        relative = Path(*name.split("."))
        expected_paths = {
            (root / relative).with_suffix(".py").resolve(),
            (root / relative / "__init__.py").resolve(),
        }
        if module_path not in expected_paths:
            offenders.append(f"{name}={module_path}")
    if offenders:
        raise Phase3MainLiveError(
            "loaded execution code does not originate in the verified project root: "
            f"{offenders[:5]!r}")


@dataclass(frozen=True, slots=True)
class PreparedMainRun:
    """Fully validated, read-only launch context."""

    project_root: Path
    manifest_path: Path
    authorization_path: Path
    authorization_raw_sha256: str
    authorization_signature_raw_sha256: str
    manifest: Mapping[str, Any]
    authorization: Mapping[str, Any]
    manifest_validation: Mapping[str, Any]
    authorization_validation: Mapping[str, Any]
    input_paths: Mapping[str, Path]
    protocol: Mapping[str, Any]
    prompt_bundle: Mapping[str, Any]
    reviewer_prompt: Mapping[str, Any]
    role_limits: Mapping[str, Any]
    price_snapshot: Mapping[str, Any]
    billing_reconciliation: Mapping[str, Any]
    cost_forecast: Mapping[str, Any]
    capacity_validation: Mapping[str, Any]
    harness_validation: Mapping[str, Any]
    inventory: phase3_main_runner.MainInventory
    context_excluded_cell_keys: tuple[str, ...]

    @property
    def identity(self) -> phase3_main_runner.MainRunIdentity:
        return phase3_main_runner.MainRunIdentity(
            run_id=str(self.manifest["run_id"]),
            manifest_sha256=str(self.manifest_validation["manifest_canonical_sha256"]),
            artifact_root=Path(self.manifest_validation["artifact_root"]),
            identity_registry_root=Path(self.manifest_validation["identity_registry_root"]),
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Phase3MainLiveError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _parse_strict_json(raw: bytes, source: Path) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Phase3MainLiveError(f"could not load strict JSON from {source}") from exc


def _load_strict_json(path: str | Path) -> Any:
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise Phase3MainLiveError(f"could not load strict JSON from {source}") from exc
    return _parse_strict_json(raw, source)


def _load_strict_object(path: str | Path, label: str) -> dict[str, Any]:
    value = _load_strict_json(path)
    if not isinstance(value, dict):
        raise Phase3MainLiveError(f"{label} must be a JSON object")
    return value


def _load_bound_input_bytes(
    manifest: Mapping[str, Any], input_paths: Mapping[str, Path],
    name: str, label: str,
) -> bytes:
    """Read and hash one manifest input from the same immutable byte sample."""
    path = input_paths[name]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Phase3MainLiveError(f"could not read bound {label}: {path}") from exc
    expected = manifest["input_bindings"][name]["sha256"]
    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected:
        raise Phase3MainLiveError(
            f"bound {label} raw SHA-256 drifted: {observed} != {expected}")
    return raw


def _load_bound_input_object(
    manifest: Mapping[str, Any], input_paths: Mapping[str, Path],
    name: str, label: str,
) -> dict[str, Any]:
    """Read, hash, and parse one manifest input from the same immutable byte sample."""
    path = input_paths[name]
    raw = _load_bound_input_bytes(manifest, input_paths, name, label)
    value = _parse_strict_json(raw, path)
    if not isinstance(value, dict):
        raise Phase3MainLiveError(f"{label} must be a JSON object")
    return value


def _load_authenticated_owner_authorization(path: str | Path) -> dict[str, Any]:
    """Load exact authorization bytes and verify Jack's detached SSH signature."""
    try:
        return phase3_main_authorization.load_authenticated_owner_authorization(
            path,
            public_key=OWNER_SIGNING_PUBLIC_KEY,
            key_fingerprint=OWNER_SIGNING_KEY_FINGERPRINT,
            ssh_keygen_path=SSH_KEYGEN_PATH,
        )
    except phase3_main_authorization.MainAuthorizationSignatureError as exc:
        raise Phase3MainLiveError(str(exc)) from exc


def _raw_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_nested_path(value: Any, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise Phase3MainLiveError(f"{label} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute() and ".." in path.parts:
        raise Phase3MainLiveError(f"{label} escapes the project root")
    return (path if path.is_absolute() else root / path).resolve()


def _verify_clean_git_identity(manifest: Mapping[str, Any], root: Path) -> None:
    """Require the current clean checkout to equal the source commit exactly."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
            check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Phase3MainLiveError("could not verify the launch Git identity") from exc
    if head != manifest["source_commit"]:
        raise Phase3MainLiveError(
            f"checkout HEAD {head!r} differs from source commit {manifest['source_commit']!r}")
    if status:
        raise Phase3MainLiveError("main launch requires a clean worktree including untracked files")


def _validate_prompt_bundle(bundle: Mapping[str, Any]) -> None:
    if canonical_sha256(bundle) != phase3_plan.FROZEN_PHASE2_PROMPT_BUNDLE_CANONICAL_SHA256:
        raise Phase3MainLiveError("prompt bundle differs from the frozen Phase 2 bundle")


def _validate_reviewer_prompt(artifact: Mapping[str, Any]) -> None:
    prompt = artifact.get("prompt")
    expected = artifact.get("prompt_sha256")
    if not isinstance(prompt, str) or not prompt:
        raise Phase3MainLiveError("reviewer prompt artifact has no prompt")
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != expected:
        raise Phase3MainLiveError("reviewer prompt differs from its declared hash")


def _validate_scope_decision(
    decision: Mapping[str, Any], protocol: Mapping[str, Any],
) -> None:
    if decision.get("schema_version") != "phase3_main_scope_capacity_decision_v1":
        raise Phase3MainLiveError("unsupported main scope decision")
    owner = decision.get("owner_decision")
    if not isinstance(owner, Mapping) or owner.get("approver") != "Jack Maiorino":
        raise Phase3MainLiveError("main scope decision is not owner-confirmed")
    confirmed = decision.get("confirmed_scope")
    if not isinstance(confirmed, Mapping):
        raise Phase3MainLiveError("main scope decision has no confirmed scope")
    if set(confirmed.get("judges") or ()) != set(protocol["roster"]["judges_final"]):
        raise Phase3MainLiveError("main scope decision judge roster drifted")
    inventory = confirmed.get("inventory")
    if not isinstance(inventory, Mapping) or inventory.get("judgment_slots_total") != 9_840:
        raise Phase3MainLiveError("main scope decision inventory drifted")
    boundary = decision.get("authority_boundary")
    if not isinstance(boundary, Mapping) or boundary != {
        "provider_calls_authorized": False,
        "paid_execution_authorized": False,
        "main_run_authorized": False,
        "spend_authorized_usd": 0,
        "required_later_authority": (
            "Any main provider call requires a separate exact owner authorization bound to "
            "the final main manifest and dollar cap."),
    }:
        raise Phase3MainLiveError("scope decision must remain non-authorizing")


def _validate_transcript_inputs(
    *, protocol: Mapping[str, Any], inventory: phase3_main_runner.MainInventory,
    bundle: Mapping[str, Any], verification: Mapping[str, Any],
) -> None:
    if verification.get("schema_version") != "phase3_transcript_verification_v1":
        raise Phase3MainLiveError("unsupported transcript verification schema")
    expected = (verification.get("bundle_canonical_sha256") or {}).get("main_bundle")
    if not isinstance(expected, str):
        raise Phase3MainLiveError("transcript verification omits the main bundle hash")
    try:
        _verify_bundle_hash(bundle, expected=expected, label="main transcript bundle")
        rows = _rows_for_bundle(
            bundle,
            kind=phase3_plan.MAIN_TRANSCRIPT_KIND,
            cells=[dict(cell) for cell in inventory.cells],
            label="main transcript bundle",
        )
    except ValueError as exc:
        raise Phase3MainLiveError(f"main transcript materialization failed: {exc}") from exc
    if len(rows) != phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT:
        raise Phase3MainLiveError("main transcript bundle does not cover exactly 492 rows")


def _validate_context_blocklist(
    value: Mapping[str, Any], *, protocol: Mapping[str, Any],
    inventory: phase3_main_runner.MainInventory,
    prompt_bundle: Mapping[str, Any], role_limits: Mapping[str, Any],
    transcript_bundle: Mapping[str, Any],
) -> tuple[str, ...]:
    try:
        return phase3_main_context.validate_main_context_blocklist(
            value,
            protocol=protocol,
            inventory=inventory,
            prompt_bundle=prompt_bundle,
            role_limits=role_limits,
            transcript_bundle=transcript_bundle,
        )
    except phase3_main_context.MainContextBlocklistError as exc:
        raise Phase3MainLiveError(f"context blocklist failed: {exc}") from exc


def _validate_price_bindings(
    price: Mapping[str, Any], *, protocol: Mapping[str, Any], root: Path,
    input_paths: Mapping[str, Path], as_of: datetime,
    require_current_freshness: bool = True,
) -> dict[str, Any]:
    catalog = price.get("raw_catalog")
    endpoints = price.get("raw_serverless_endpoints")
    if not isinstance(catalog, Mapping) or not isinstance(endpoints, Mapping):
        raise Phase3MainLiveError("price snapshot must bind catalog and serverless endpoints")
    if _resolve_nested_path(catalog.get("path"), root, "price raw catalog") != input_paths[
        "raw_provider_catalog"]:
        raise Phase3MainLiveError("price snapshot raw catalog path differs from the manifest")
    if _resolve_nested_path(endpoints.get("path"), root, "price endpoints") != input_paths[
        "raw_serverless_endpoints"]:
        raise Phase3MainLiveError("price snapshot endpoint path differs from the manifest")
    try:
        return phase3_v3_inputs.validate_price_snapshot(
            price,
            protocol=protocol,
            as_of=as_of,
            project_root=root,
            verify_catalog=True,
            require_current_freshness=require_current_freshness,
        )
    except phase3_v3_inputs.InputGateError as exc:
        raise Phase3MainLiveError(f"price snapshot failed: {exc}") from exc


def _validate_capacity(
    *, plan: Mapping[str, Any], result: Mapping[str, Any],
    history_path: Path, as_of: datetime,
    require_current_freshness: bool = True,
) -> dict[str, Any]:
    try:
        source = plan["source_locations"]
        archive = Path(str(source["sealed_archive"]))
        finalization = Path(str(source["finalization_record"]))
        if not finalization.is_absolute():
            finalization = Path(__file__).resolve().parents[1] / finalization
        snapshot = phase3_main_review_capacity_preflight.collect_source_snapshot(
            archive_dir=archive, finalization_path=finalization)
        workload = phase3_main_review_capacity_preflight.derive_workload(snapshot)
        plan_validation = phase3_main_review_capacity_preflight.validate_plan(
            plan, snapshot=snapshot, workload=workload)
        history = phase3_main_review_capacity_preflight.load_bound_dispatch_history(
            history_path, plan=plan)
        measurement = phase3_main_review_capacity_preflight.validate_result(
            result, plan=plan, workload=workload, dispatch_history=history,
            as_of_utc=as_of,
            require_current_freshness=require_current_freshness,
        )
    except (KeyError, ValueError) as exc:
        raise Phase3MainLiveError(f"review capacity evidence failed: {exc}") from exc
    return {"plan": plan_validation, "measurement": measurement}


def _reviewer_cli_version(path: Path) -> str:
    try:
        completed = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase3MainLiveError(
            f"could not verify the capacity-bound reviewer CLI version: {path}") from exc
    output = completed.stdout.strip() or completed.stderr.strip()
    if completed.returncode != 0 or not output:
        raise Phase3MainLiveError(
            f"capacity-bound reviewer CLI version check failed: {path}")
    return output


def _validate_capacity_runtime_binding(
    *, plan: Mapping[str, Any], result: Mapping[str, Any], runtime: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the measured reviewer configuration and host to the actual main runtime."""
    reviewer = plan.get("reviewer_configuration")
    if not isinstance(reviewer, Mapping):
        raise Phase3MainLiveError("capacity plan omits reviewer_configuration")
    expected_runtime = {
        "reviewer_cli_binary": reviewer.get("reviewer_cli_binary"),
        "reviewer_model": reviewer.get("model"),
        "reviewer_reasoning_effort": reviewer.get("reasoning_effort"),
        "reviewer_concurrency": reviewer.get("concurrency"),
    }
    observed_runtime = {field: runtime.get(field) for field in expected_runtime}
    if observed_runtime != expected_runtime:
        raise Phase3MainLiveError(
            "main reviewer runtime differs from the capacity-measured configuration")
    environment = result.get("measurement_environment")
    if not isinstance(environment, Mapping):
        raise Phase3MainLiveError("capacity result omits measurement_environment")
    host = platform.node().strip()
    if not host or environment.get("host_identity") != host:
        raise Phase3MainLiveError(
            "main host identity differs from the capacity-measured host")
    raw_cli_path = reviewer.get("reviewer_cli_resolved_path")
    if not isinstance(raw_cli_path, str) or not raw_cli_path:
        raise Phase3MainLiveError("capacity plan omits the resolved reviewer CLI path")
    cli_path = Path(raw_cli_path).resolve()
    try:
        cli_bytes = cli_path.read_bytes()
    except OSError as exc:
        raise Phase3MainLiveError(
            f"capacity-bound reviewer CLI is unavailable: {cli_path}") from exc
    expected_size = reviewer.get("reviewer_cli_wrapper_byte_count")
    expected_sha = reviewer.get("reviewer_cli_wrapper_raw_sha256")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or len(cli_bytes) != expected_size
        or hashlib.sha256(cli_bytes).hexdigest() != expected_sha
    ):
        raise Phase3MainLiveError(
            "capacity-bound reviewer CLI bytes changed after measurement")
    measured_version = environment.get("reviewer_cli_version")
    if not isinstance(measured_version, str) or _reviewer_cli_version(
        cli_path
    ) != measured_version:
        raise Phase3MainLiveError(
            "main reviewer CLI version differs from the capacity measurement")
    return {
        "reviewer_cli_resolved_path": cli_path,
        "reviewer_cli_version": measured_version,
        "host_identity": host,
    }


def _reviewer_loop_contract(plan: Mapping[str, Any]) -> tuple[int, int]:
    """Derive a complete loop bound from the measured wave and worst-case workload."""
    try:
        reviewer = plan["reviewer_configuration"]
        workload = plan["workload"]
        thresholds = plan["capacity_thresholds"]
        pending_raw = reviewer["actual_capacity_wave_size"]
        pending_ceiling_raw = reviewer["wave_pending_payload_limit"]
        measured_wave_raw = workload["wave_size"]
        maximum_raw = thresholds["maximum_unique_review_payloads_zero_dedup"]
    except (KeyError, TypeError) as exc:
        raise Phase3MainLiveError(
            "capacity plan omits the reviewer loop contract") from exc
    for value, label in (
        (pending_raw, "actual capacity wave size"),
        (pending_ceiling_raw, "pending-payload ceiling"),
        (measured_wave_raw, "measured workload wave size"),
        (maximum_raw, "maximum unique reviewer payloads"),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise Phase3MainLiveError(f"{label} must be an integer")
    pending_limit = int(pending_raw)
    pending_ceiling = int(pending_ceiling_raw)
    measured_wave = int(measured_wave_raw)
    maximum_unique_payloads = int(maximum_raw)
    if pending_limit <= 0 or pending_limit > pending_ceiling:
        raise Phase3MainLiveError(
            "measured reviewer wave size exceeds the pending-payload ceiling")
    if pending_limit != measured_wave:
        raise Phase3MainLiveError(
            "production reviewer wave size differs from the measured workload wave")
    if maximum_unique_payloads < 0:
        raise Phase3MainLiveError("maximum reviewer payload count must be non-negative")
    max_passes = (
        math.ceil(maximum_unique_payloads / pending_limit)
        + phase3_main_finalization.MAX_TERMINAL_JUDGMENT_CELLS
        + 2
    )
    return pending_limit, max_passes


class _AuthorizationDeadlineClient:
    """Recheck exact authority and launch-bound price evidence before every paid call."""

    def __init__(self, prepared: PreparedMainRun, inner: Any) -> None:
        self._prepared = prepared
        self._inner = inner

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self._inner, "dry_run", False))

    def complete(self, *args: Any, **kwargs: Any) -> str:
        _revalidate_authenticated_authorization(self._prepared)
        _revalidate_price_snapshot(self._prepared)
        return str(self._inner.complete(*args, **kwargs))


def _revalidate_authenticated_authorization(
    prepared: PreparedMainRun,
) -> Mapping[str, Any]:
    """Require the original signed bytes and active semantic authorization."""
    current = _load_authenticated_owner_authorization(prepared.authorization_path)
    signature_path = prepared.authorization_path.with_name(
        f"{prepared.authorization_path.name}.sig")
    if (
        _raw_sha256(prepared.authorization_path) != prepared.authorization_raw_sha256
        or _raw_sha256(signature_path)
        != prepared.authorization_signature_raw_sha256
        or canonical_sha256(current) != canonical_sha256(prepared.authorization)
    ):
        raise Phase3MainLiveError(
            "main authorization or detached signature bytes changed during run")
    phase3_main_manifest.validate_main_authorization(
        current,
        prepared.manifest,
        as_of=datetime.now(timezone.utc),
    )
    return current


def _revalidate_price_snapshot(prepared: PreparedMainRun) -> Mapping[str, Any]:
    """Reload exact price inputs without extending launch freshness into the run."""
    for name, label in (
        ("raw_provider_catalog", "raw provider catalog"),
        ("raw_serverless_endpoints", "raw serverless endpoints"),
    ):
        _load_bound_input_bytes(
            prepared.manifest, prepared.input_paths, name, label)
    current = _load_bound_input_object(
        prepared.manifest,
        prepared.input_paths,
        "price_snapshot",
        "price snapshot",
    )
    if canonical_sha256(current) != canonical_sha256(prepared.price_snapshot):
        raise Phase3MainLiveError("price snapshot semantics changed during run")
    _validate_price_bindings(
        current,
        protocol=prepared.protocol,
        root=prepared.project_root,
        input_paths=prepared.input_paths,
        as_of=datetime.now(timezone.utc),
        require_current_freshness=False,
    )
    return current


def _revalidate_capacity_snapshot(
    prepared: PreparedMainRun,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Reload exact capacity evidence without extending launch freshness into the run."""
    plan = _load_bound_input_object(
        prepared.manifest, prepared.input_paths, "capacity_plan", "capacity plan")
    result = _load_bound_input_object(
        prepared.manifest, prepared.input_paths, "capacity_result", "capacity result")
    _load_bound_input_bytes(
        prepared.manifest,
        prepared.input_paths,
        "capacity_dispatch_history",
        "capacity dispatch history",
    )
    validation = _validate_capacity(
        plan=plan,
        result=result,
        history_path=prepared.input_paths["capacity_dispatch_history"],
        as_of=datetime.now(timezone.utc),
        require_current_freshness=False,
    )
    runtime = _validate_capacity_runtime_binding(
        plan=plan, result=result, runtime=prepared.manifest["runtime"])
    return plan, {**validation, "runtime": runtime}


def _validate_launch_freshness(prepared: PreparedMainRun) -> None:
    """Establish current launch evidence immediately before identity consumption."""
    now = datetime.now(timezone.utc)
    for name, label in (
        ("raw_provider_catalog", "raw provider catalog"),
        ("raw_serverless_endpoints", "raw serverless endpoints"),
    ):
        _load_bound_input_bytes(
            prepared.manifest, prepared.input_paths, name, label)
    price = _load_bound_input_object(
        prepared.manifest, prepared.input_paths, "price_snapshot", "price snapshot")
    if canonical_sha256(price) != canonical_sha256(prepared.price_snapshot):
        raise Phase3MainLiveError("price snapshot semantics changed before launch")
    _validate_price_bindings(
        price,
        protocol=prepared.protocol,
        root=prepared.project_root,
        input_paths=prepared.input_paths,
        as_of=now,
    )
    plan = _load_bound_input_object(
        prepared.manifest, prepared.input_paths, "capacity_plan", "capacity plan")
    result = _load_bound_input_object(
        prepared.manifest, prepared.input_paths, "capacity_result", "capacity result")
    _load_bound_input_bytes(
        prepared.manifest,
        prepared.input_paths,
        "capacity_dispatch_history",
        "capacity dispatch history",
    )
    _validate_capacity(
        plan=plan,
        result=result,
        history_path=prepared.input_paths["capacity_dispatch_history"],
        as_of=now,
    )
    _validate_capacity_runtime_binding(
        plan=plan, result=result, runtime=prepared.manifest["runtime"])
    billing_record = _load_bound_input_object(
        prepared.manifest,
        prepared.input_paths,
        "billing_reconciliation",
        "billing reconciliation",
    )
    billing_validation = (
        phase3_main_billing_reconciliation.validate_billing_reconciliation(
            billing_record,
            project_root=prepared.project_root,
            as_of=now,
        )
    )
    _require_clean_pre_main_billing(billing_validation, prepared.manifest)
    phase3_main_manifest.validate_main_manifest(
        prepared.manifest,
        project_root=prepared.project_root,
        verify_files=True,
        verify_runtime=True,
    )
    _verify_execution_code_root(prepared.project_root)
    _verify_clean_git_identity(prepared.manifest, prepared.project_root)


def _load_verified_exact_context_index(
    manifest: Mapping[str, Any], *, input_paths: Mapping[str, Path],
    protocol: Mapping[str, Any], root: Path,
) -> Mapping[str, Any]:
    """Recompute the tuple-keyed context index from the bound tokenizer manifest."""
    tokenizer_manifest = _load_bound_input_object(
        manifest, input_paths, "tokenizer_manifest", "tokenizer manifest")
    try:
        return phase3_v3_inputs.load_exact_context_index(
            tokenizer_manifest,
            protocol=protocol,
            project_root=root,
        )
    except phase3_v3_inputs.InputGateError as exc:
        raise Phase3MainLiveError(
            f"exact tokenizer and context inputs failed: {exc}") from exc


def _decimal_number(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise Phase3MainLiveError(f"{label} must be numeric")
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise Phase3MainLiveError(f"{label} must be numeric") from exc
    if not amount.is_finite() or amount < 0:
        raise Phase3MainLiveError(f"{label} must be finite and non-negative")
    return amount


def _require_clean_pre_main_billing(
    validation: Mapping[str, Any], manifest: Mapping[str, Any],
) -> None:
    """Require a closed exact or conservative prior-spend upper bound."""
    if validation.get("run_id") != manifest.get("run_id"):
        raise Phase3MainLiveError(
            "pre-main billing reconciliation run ID differs from the manifest")
    disposition = validation.get("disposition")
    if (
        disposition not in phase3_main_billing_reconciliation.CLOSED_DISPOSITIONS
        or validation.get("closed") is not True
        or (
            disposition == phase3_main_billing_reconciliation.DISPOSITION_CLOSED_CONSERVATIVE
            and validation.get("within_conservative_envelope") is not True
        )
        or _decimal_number(
            validation.get("accounted_spend_usd"), "accounted predecessor spend")
        != _decimal_number(
            manifest["spend"]["prior_reconciled_usd"], "manifest predecessor spend")
    ):
        raise Phase3MainLiveError(
            "pre-main billing reconciliation is not closed at the manifest upper bound")


def _validate_cost_forecast(
    forecast: Mapping[str, Any], *, protocol: Mapping[str, Any],
    inventory: phase3_main_runner.MainInventory, dynamic_frame: Mapping[str, Any],
    exact_context_index: Mapping[str, Any], price: Mapping[str, Any],
    reconciliation_record: Mapping[str, Any], manifest: Mapping[str, Any],
    root: Path, as_of: datetime,
) -> None:
    if forecast.get("schema_version") != phase3_v3_forecast.COST_SCHEMA_VERSION:
        raise Phase3MainLiveError("unsupported certified cost forecast schema")
    if forecast.get("execution_authorized") is not False:
        raise Phase3MainLiveError("cost forecast cannot authorize execution")
    if forecast.get("certification") != "pass" or forecast.get("within_stage_cap") is not True:
        raise Phase3MainLiveError("cost forecast is not an in-cap certification")
    if forecast.get("planned_main_judgment_slot_count") != 9_840:
        raise Phase3MainLiveError("cost forecast main slot count drifted")
    spend = manifest["spend"]
    prior = Decimal(spend["prior_reconciled_usd"])
    projected = Decimal(spend["forecast_main_usd"])
    cap = Decimal(spend["stage_cap_usd"])
    if _decimal_number(forecast.get("projected_main_usd"), "projected main") != projected:
        raise Phase3MainLiveError("manifest main forecast differs from the certified forecast")
    if _decimal_number(forecast.get("cumulative_spend_usd"), "cumulative spend") != prior:
        raise Phase3MainLiveError("manifest prior spend differs from the certified forecast")
    if _decimal_number(forecast.get("stage_cap_usd"), "stage cap") != cap:
        raise Phase3MainLiveError("manifest cap differs from the certified forecast")
    if _decimal_number(protocol["decisions"]["spend"]["stage_cap_usd"], "protocol cap") != cap:
        raise Phase3MainLiveError("protocol cap differs from the exact main authorization cap")

    raw_segments = forecast.get("cumulative_spend_segments")
    if isinstance(raw_segments, (str, bytes)) or not isinstance(raw_segments, Sequence):
        raise Phase3MainLiveError("cost forecast cumulative segments are missing")
    ledger_rows = reconciliation_record.get("ledgers")
    if isinstance(ledger_rows, (str, bytes)) or not isinstance(ledger_rows, Sequence):
        raise Phase3MainLiveError("billing reconciliation ledger rows are missing")
    expected = {
        str(row["raw_sha256"]): (
            Decimal(str(row["actual_spend_usd"])),
            Decimal(str(row["uncertain_spend_usd"])),
        )
        for row in ledger_rows if isinstance(row, Mapping)
    }
    observed: dict[str, tuple[Decimal, Decimal]] = {}
    segments: list[Mapping[str, Any]] = []
    for segment in raw_segments:
        if not isinstance(segment, Mapping):
            raise Phase3MainLiveError("cost forecast segment is not an object")
        segment = cast(Mapping[str, Any], segment)
        segments.append(segment)
        digest = str(segment.get("ledger_sha256"))
        if digest in observed:
            raise Phase3MainLiveError("cost forecast repeats a cumulative ledger")
        observed[digest] = (
            _decimal_number(segment.get("actual_spend_usd"), "segment actual spend"),
            _decimal_number(segment.get("uncertain_spend_usd"), "segment uncertain spend"),
        )
    if observed != expected:
        raise Phase3MainLiveError("cost forecast segments differ from reconciled ledgers")
    try:
        recomputed = phase3_v3_forecast.build_cost_forecast(
            protocol=protocol,
            planned_main_cells=[dict(cell) for cell in inventory.cells],
            dynamic_residual_frame=dynamic_frame,
            exact_context_index=exact_context_index,
            price_snapshot=price,
            price_as_of=as_of,
            cumulative_spend_segments=segments,
            project_root=str(root),
            verify_price_catalog=True,
        )
    except phase3_v3_forecast.ForecastInputError as exc:
        raise Phase3MainLiveError(f"cost forecast recomputation failed: {exc}") from exc
    if canonical_sha256(recomputed) != canonical_sha256(forecast):
        raise Phase3MainLiveError("certified cost forecast does not recompute exactly")


def load_prepared_main(
    manifest_path: str | Path,
    authorization_path: str | Path,
    *,
    verify_git: bool = True,
) -> PreparedMainRun:
    """Read and fully validate one exact main launch without mutating output state."""
    root = LIVE_PROJECT_ROOT
    manifest_file = Path(manifest_path).resolve()
    authorization_file = Path(authorization_path).resolve()
    _verify_execution_code_root(root)
    manifest = _load_strict_object(manifest_file, "main manifest")

    phase3_main_manifest.validate_main_manifest(
        manifest, project_root=root, verify_files=False, verify_runtime=True)
    if verify_git:
        _verify_clean_git_identity(manifest, root)
    manifest_validation = phase3_main_manifest.validate_main_manifest(
        manifest, project_root=root, verify_files=True, verify_runtime=True)
    for label in ("artifact_root", "identity_registry_root"):
        output_root = Path(manifest_validation[label])
        try:
            output_root.relative_to(root)
        except ValueError:
            pass
        else:
            raise Phase3MainLiveError(
                f"{label} must be outside the source checkout for a clean formal run")
    input_paths = dict(manifest_validation["input_paths"])

    authorization = _load_authenticated_owner_authorization(authorization_file)
    authorization_raw_sha256 = _raw_sha256(authorization_file)
    authorization_signature_raw_sha256 = _raw_sha256(
        authorization_file.with_name(f"{authorization_file.name}.sig"))
    protocol = _load_bound_input_object(
        manifest, input_paths, "protocol", "main protocol")
    phase3_plan.validate_protocol(protocol)
    if protocol.get("protocol_id") != phase3_main_runner.CONFIRMED_PROTOCOL_ID:
        raise Phase3MainLiveError("main manifest does not bind the confirmed r6 protocol")
    inventory = phase3_main_runner.build_main_inventory(protocol, root)

    prompt_bundle = _load_bound_input_object(
        manifest, input_paths, "prompt_bundle", "prompt bundle")
    _validate_prompt_bundle(prompt_bundle)
    reviewer_prompt = _load_bound_input_object(
        manifest, input_paths, "reviewer_prompt", "reviewer prompt")
    _validate_reviewer_prompt(reviewer_prompt)
    role_limits = _load_bound_input_object(
        manifest, input_paths, "role_limits", "role limits")
    try:
        phase3_v3_live._validate_role_limits(role_limits, protocol)
    except phase3_v3_live.Phase3V3LiveError as exc:
        raise Phase3MainLiveError(f"role limits failed: {exc}") from exc

    analysis_pins = _load_bound_input_object(
        manifest, input_paths, "analysis_pins", "analysis pins")
    try:
        phase3_main_analysis.validate_analysis_pins(analysis_pins, protocol, root=root)
    except phase3_main_analysis.AnalysisError as exc:
        raise Phase3MainLiveError(f"analysis pins failed: {exc}") from exc
    scope_decision = _load_bound_input_object(
        manifest, input_paths, "scope_decision", "scope decision")
    _validate_scope_decision(scope_decision, protocol)
    bound_scope_path = _resolve_nested_path(
        analysis_pins["bindings"]["scope_decision_path"], root,
        "analysis-pins scope decision")
    if bound_scope_path != input_paths["scope_decision"]:
        raise Phase3MainLiveError("analysis pins and manifest bind different scope decisions")

    transcript_bundle = _load_bound_input_object(
        manifest, input_paths, "main_transcript_bundle", "main transcript bundle")
    transcript_verification = _load_bound_input_object(
        manifest, input_paths, "transcript_verification", "transcript verification")
    _validate_transcript_inputs(
        protocol=protocol, inventory=inventory, bundle=transcript_bundle,
        verification=transcript_verification)

    context_blocklist = _load_bound_input_object(
        manifest, input_paths, "context_blocklist", "context blocklist")
    excluded = _validate_context_blocklist(
        context_blocklist,
        protocol=protocol,
        inventory=inventory,
        prompt_bundle=prompt_bundle,
        role_limits=role_limits,
        transcript_bundle=transcript_bundle,
    )

    now = datetime.now(timezone.utc)
    capacity_plan = _load_bound_input_object(
        manifest, input_paths, "capacity_plan", "capacity plan")
    capacity_result = _load_bound_input_object(
        manifest, input_paths, "capacity_result", "capacity result")
    capacity_validation = _validate_capacity(
        plan=capacity_plan, result=capacity_result,
        history_path=input_paths["capacity_dispatch_history"], as_of=now)
    capacity_runtime = _validate_capacity_runtime_binding(
        plan=capacity_plan, result=capacity_result, runtime=manifest["runtime"])
    capacity_validation = {**capacity_validation, "runtime": capacity_runtime}

    price = _load_bound_input_object(
        manifest, input_paths, "price_snapshot", "price snapshot")
    authorization_validation = phase3_main_manifest.validate_main_authorization(
        authorization, manifest, as_of=now)
    approved_at = phase3_main_manifest._utc(
        authorization["approved_at_utc"], "authorization.approved_at_utc")
    _validate_price_bindings(
        price, protocol=protocol, root=root, input_paths=input_paths, as_of=approved_at)
    _validate_price_bindings(
        price, protocol=protocol, root=root, input_paths=input_paths, as_of=now)
    try:
        phase3_v3_live._validate_contexts_against_catalog(role_limits, price, root)
    except phase3_v3_live.Phase3V3LiveError as exc:
        raise Phase3MainLiveError(f"provider context ceilings failed: {exc}") from exc

    billing_record = _load_bound_input_object(
        manifest, input_paths, "billing_reconciliation", "billing reconciliation")
    billing_validation = phase3_main_billing_reconciliation.validate_billing_reconciliation(
        billing_record, project_root=root, as_of=now)
    _require_clean_pre_main_billing(billing_validation, manifest)

    dynamic_frame = _load_bound_input_object(
        manifest, input_paths, "dynamic_residual_frame", "dynamic residual frame")
    exact_context_index = _load_verified_exact_context_index(
        manifest, input_paths=input_paths, protocol=protocol, root=root)
    forecast = _load_bound_input_object(
        manifest, input_paths, "certified_cost_forecast", "certified cost forecast")
    _validate_cost_forecast(
        forecast, protocol=protocol, inventory=inventory, dynamic_frame=dynamic_frame,
        exact_context_index=exact_context_index, price=price,
        reconciliation_record=billing_record, manifest=manifest, root=root, as_of=now)

    harness_receipt = _load_bound_input_object(
        manifest, input_paths, "harness_receipt", "harness receipt")
    harness_validation = phase3_main_harness.validate_harness_receipt(
        harness_receipt, protocol=protocol, prompt_bundle=prompt_bundle,
        role_limits=role_limits, project_root=root,
        formal_artifact_root=manifest_validation["artifact_root"],
        main_transcript_bundle_path=input_paths["main_transcript_bundle"],
        transcript_verification_path=input_paths["transcript_verification"],
    )
    harness_manifest = manifest["harness_check"]
    if (
        harness_receipt["harness_seed"] != manifest["seeds"]["harness_seed"]
        or harness_receipt["first_output_store_sha256"]
        != harness_manifest["first_output_store_sha256"]
        or harness_receipt["rerun_output_store_sha256"]
        != harness_manifest["rerun_output_store_sha256"]
    ):
        raise Phase3MainLiveError("manifest harness binding differs from the validated receipt")

    canary_finalization = input_paths["canary_finalization"]
    source = analysis_pins["capability_anchor_scores"]["source"]
    if (
        _resolve_nested_path(source["finalization_record_path"], root, "canary finalization")
        != canary_finalization
        or source["finalization_record_raw_sha256"] != _raw_sha256(canary_finalization)
    ):
        raise Phase3MainLiveError("manifest and analysis pins bind different canary finalization")

    return PreparedMainRun(
        project_root=root,
        manifest_path=manifest_file,
        authorization_path=authorization_file,
        authorization_raw_sha256=authorization_raw_sha256,
        authorization_signature_raw_sha256=authorization_signature_raw_sha256,
        manifest=manifest,
        authorization=authorization,
        manifest_validation=manifest_validation,
        authorization_validation=authorization_validation,
        input_paths=input_paths,
        protocol=protocol,
        prompt_bundle=prompt_bundle,
        reviewer_prompt=reviewer_prompt,
        role_limits=role_limits,
        price_snapshot=price,
        billing_reconciliation=billing_record,
        cost_forecast=forecast,
        capacity_validation=capacity_validation,
        harness_validation=harness_validation,
        inventory=inventory,
        context_excluded_cell_keys=excluded,
    )


def _model_prices(price_snapshot: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    return {
        str(model): {
            "in": float(entry["input_usd_per_million"]),
            "out": float(entry["output_usd_per_million"]),
        }
        for model, entry in price_snapshot["models"].items()
    }


def _construct_provider_client(
    prepared: PreparedMainRun, snapshot: api_client.UsageLedgerSnapshot,
) -> Any:
    """Construct the sole live client. Tests may monkeypatch this private seam."""
    request = prepared.role_limits["request_settings"]
    transport = request["transport"]
    prior = Decimal(prepared.manifest["spend"]["prior_reconciled_usd"])
    cap = Decimal(prepared.authorization["stage_cap_usd"])
    raw = api_client.RejudgeClient(
        approved_cap_usd=float(cap),
        dry_run=False,
        error_log_path=str(prepared.identity.paths.provider_error_log),
        max_retries=int(transport["ledger_max_retries"]),
        model_prices=_model_prices(prepared.price_snapshot),
        strict_model_pricing=True,
        initial_spend_usd=float(prior),
        initial_uncertain_spend_usd=0.0,
        usage_log_path=str(snapshot.path),
        _ledger_snapshot=snapshot,
        _accounting_factory_token=api_client._LIVE_ACCOUNTING_FACTORY_TOKEN,
        require_explicit_reasoning_max_tokens=True,
        model_context_limits={
            model: int(entry["context_length_tokens"])
            for model, entry in prepared.role_limits["context_ceilings"].items()
        },
        strict_context_mode=True,
        streaming_pinned_models=frozenset(request["streaming_pinned_models"]),
        reasoning_models=frozenset(
            prepared.role_limits["reasoning_models"]["model_ids"]),
        extra_request_fields={
            model: dict(fields)
            for model, fields in request["per_model_extra_fields"].items()
        },
        halt_on_unknown_charge=True,
        http_timeout=dict(transport["http_timeout"]),
        sdk_internal_max_retries=int(transport["sdk_internal_max_retries"]),
        per_call_wall_clock_ceiling_seconds=float(
            transport["per_call_wall_clock_ceiling_seconds"]),
        require_returned_model_match=True,
    )
    return RoleLimitResolvingClient(raw, prepared.role_limits["model_role_limits"])


def _identity_start_path(identity: phase3_main_runner.MainRunIdentity) -> Path:
    registry = identity.identity_registry_root
    if registry is None:  # defensive; MainRunIdentity canonicalizes a default in __post_init__
        raise Phase3MainLiveError("main identity has no persistent registry root")
    return (
        registry / "identities"
        / f"{identity.run_id}-{identity.manifest_sha256}.started.json"
    )


def _identity_complete_path(identity: phase3_main_runner.MainRunIdentity) -> Path:
    registry = identity.identity_registry_root
    if registry is None:  # defensive; MainRunIdentity canonicalizes a default in __post_init__
        raise Phase3MainLiveError("main identity has no persistent registry root")
    return (
        registry / "identities"
        / f"{identity.run_id}-{identity.manifest_sha256}.completed.json"
    )


def _authorization_consumed_path(prepared: PreparedMainRun) -> Path:
    registry = prepared.identity.identity_registry_root
    if registry is None:
        raise Phase3MainLiveError("main identity has no persistent registry root")
    authorization_id = str(prepared.authorization["authorization_id"])
    identity = hashlib.sha256(authorization_id.encode("utf-8")).hexdigest()
    return registry / "authorizations" / f"{identity}.consumed.json"


def _write_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(dict(value), sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise Phase3MainLiveError(f"formal identity was already consumed: {path}") from exc


def _consume_identity(prepared: PreparedMainRun) -> Path:
    identity = prepared.identity
    authorization_sha = canonical_sha256(prepared.authorization)
    _write_exclusive_json(_authorization_consumed_path(prepared), {
        "schema_version": AUTHORIZATION_CONSUMED_SCHEMA,
        "status": "consumed_no_reuse",
        "authorization_id": prepared.authorization["authorization_id"],
        "authorization_canonical_sha256": authorization_sha,
        "authorization_raw_sha256": prepared.authorization_raw_sha256,
        "authorization_signature_raw_sha256": (
            prepared.authorization_signature_raw_sha256),
        "run_id": identity.run_id,
        "manifest_canonical_sha256": identity.manifest_sha256,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    start = _identity_start_path(identity)
    _write_exclusive_json(start, {
        "schema_version": IDENTITY_START_SCHEMA,
        "status": "started_no_resume",
        "run_id": identity.run_id,
        "manifest_canonical_sha256": identity.manifest_sha256,
        "manifest_identity_sha256": prepared.manifest["manifest_identity_sha256"],
        "authorization_id": prepared.authorization["authorization_id"],
        "artifact_root": identity.artifact_root.as_posix(),
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    return start


def run_main(
    manifest_path: str | Path,
    authorization_path: str | Path,
) -> dict[str, Any]:
    """Run one fresh main identity after all read-only gates pass.

    The execution loop is installed with finalization in this module's closeout section. This
    entry already enforces the irreversible startup and private factory boundary. Any failure
    after identity consumption leaves the persistent started record in place and cannot resume.
    """
    prepared = load_prepared_main(
        manifest_path, authorization_path, verify_git=True)
    _require_production_execution_unblocked()
    identity = prepared.identity
    paths = identity.paths
    with phase3_v3_live.RunLease(paths.lease):
        phase3_main_runner._assert_fresh_identity(paths)
        _validate_launch_freshness(prepared)
        _revalidate_authenticated_authorization(prepared)
        _consume_identity(prepared)
        active = phase3_main_runner._write_active_marker(identity, paths)
        binding = phase3_main_runner._identity_binding_bytes(identity)
        try:
            phase3_main_runner._write_identity_binding(identity, paths)
            from scripts.phase3_preseed_transcripts import preseed_main

            seeded = preseed_main(
                protocol_path=prepared.input_paths["protocol"],
                project_root=prepared.project_root,
                main_bundle_path=prepared.input_paths["main_transcript_bundle"],
                verification_report_path=prepared.input_paths["transcript_verification"],
                target_store_path=paths.results,
            )
            if seeded["main_bundle_count"] != 492 or seeded["written"] != 492:
                raise Phase3MainLiveError("fresh main identity did not preseed exactly 492 rows")
            snapshot, ledger_events = phase3_main_runner._fresh_ledger_snapshot(
                paths.usage_ledger)
            journal = RequestJournal(
                paths.request_journal,
                execution_identity=identity.journal_execution_identity)
            findings = find_ambiguous_dispatches(journal, ledger_events)
            if findings:
                raise Phase3MainLiveError(
                    f"fresh main ledger/journal reconciliation failed: {findings[:3]!r}")
            phase3_main_manifest.validate_main_manifest(
                prepared.manifest, project_root=prepared.project_root,
                verify_files=True, verify_runtime=True)
            _verify_execution_code_root(prepared.project_root)
            _verify_clean_git_identity(prepared.manifest, prepared.project_root)
            phase3_main_manifest.validate_main_authorization(
                prepared.authorization, prepared.manifest,
                as_of=datetime.now(timezone.utc))
            _revalidate_price_snapshot(prepared)
            _revalidate_capacity_snapshot(prepared)
            billing_record = _load_bound_input_object(
                prepared.manifest, prepared.input_paths,
                "billing_reconciliation", "billing reconciliation")
            billing_validation = (
                phase3_main_billing_reconciliation.validate_billing_reconciliation(
                    billing_record,
                    project_root=prepared.project_root,
                    as_of=datetime.now(timezone.utc),
                )
            )
            _require_clean_pre_main_billing(
                billing_validation, prepared.manifest)
            raw_client = _construct_provider_client(prepared, snapshot)
            client = _AuthorizationDeadlineClient(
                prepared, JournalingClient(raw_client, journal))
            return _drive_and_finalize(prepared, client)
        finally:
            phase3_main_runner._restore_start_evidence(
                paths, active_marker=active, identity_binding=binding)


def _drive_and_finalize(prepared: PreparedMainRun, client: Any) -> dict[str, Any]:
    """Drive serial provider work, same-process review waves, and exact closeout."""
    identity = prepared.identity
    paths = identity.paths
    paths.decisions.touch(exist_ok=False)
    paths.reviewer_index.touch(exist_ok=False)
    paths.run_log.touch(exist_ok=False)
    paths.review_packets_root.mkdir()
    terminal_store = phase3_main_finalization.MainTerminalDispositionStore(
        paths.terminal_dispositions,
        run_id=identity.run_id,
        manifest_canonical_sha256=identity.manifest_sha256,
        inventory=prepared.inventory,
        checker_model=str(prepared.protocol["roster"]["query_checker"]),
    )
    export_reviewer_worklist(
        [], str(prepared.reviewer_prompt["prompt"]), paths.reviewer_worklist)
    resolved = phase3_runner.resolve_main_cells(
        prepared.inventory.cells,
        protocol=prepared.protocol,
        bundle=prepared.prompt_bundle,
    )
    context_excluded = frozenset(prepared.context_excluded_cell_keys)
    reviewer = _PauseModeReviewer()
    capacity_plan = _load_bound_input_object(
        prepared.manifest, prepared.input_paths, "capacity_plan", "capacity plan")
    pending_limit, max_passes = _reviewer_loop_contract(capacity_plan)
    _append_jsonl(paths.run_log, {
        "event": "formal_main_started",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": identity.run_id,
        "planned_transcripts": 492,
        "planned_judgments": 9_840,
        "context_ineligible_judgments": len(context_excluded),
        "provider_worker_concurrency": 1,
        "reviewer_concurrency": 12,
        "reviewer_wave_size": pending_limit,
        "maximum_driver_passes": max_passes,
    })

    for pass_index in range(1, max_passes + 1):
        terminal = terminal_store.cell_keys
        omitted = context_excluded | terminal
        active = [cell for cell in resolved if str(cell.cell_key) not in omitted]
        outcome = run_canary(
            results_path=paths.results,
            decisions_path=paths.decisions,
            client=client,
            reviewer=reviewer,
            anchor_judge_model="",
            protocol=dict(prepared.protocol),
            bundle=dict(prepared.prompt_bundle),
            pause_when_unlabeled=True,
            cells=active,
            max_workers=1,
            transcript_generation_forbidden=True,
            namespace=str(prepared.protocol["cell_key_namespace"]),
            pending_payload_limit=pending_limit,
            role_limits=dict(prepared.role_limits),
            fatal_unknown_charge=True,
        )
        if outcome.halted_reason == "GenerationForbiddenError":
            raise GenerationForbiddenError(
                f"unseeded transcript reached main execution: {outcome.halted_cell_key}")
        if outcome.halted_reason == "checker_malformed":
            if not outcome.halted_cell_key:
                raise Phase3MainLiveError("checker_malformed halt omitted its cell key")
            _record_terminal_checker_malformed(
                prepared, terminal_store, outcome.halted_cell_key)
            phase3_main_finalization.evaluate_terminal_bounds(
                inventory=prepared.inventory,
                terminal_records=terminal_store.records,
            )
            _append_jsonl(paths.run_log, {
                "event": "checker_malformed_terminal_disposition",
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "cell_key": outcome.halted_cell_key,
                "terminal_count": len(terminal_store.cell_keys),
            })
            continue
        if outcome.halted_reason is not None:
            raise Phase3MainLiveError(
                f"formal main halted at {outcome.halted_cell_key}: "
                f"{outcome.halted_reason}")

        observed_keys = phase3_main_finalization.load_result_cell_keys(paths.results)
        target = phase3_main_runner.EXPECTED_MAIN_CELL_COUNT - len(
            context_excluded | terminal_store.cell_keys)
        complete = len(observed_keys)
        _append_jsonl(paths.run_log, {
            "event": "formal_main_pass_complete",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "pass_index": pass_index,
            "completed_this_pass": outcome.completed,
            "rows_complete": complete,
            "rows_target": target,
            "pending_payloads": len(outcome.pending_payloads),
        })
        if complete == target:
            return _finalize_main(prepared, terminal_store)
        if outcome.pending_payloads:
            _review_wave_same_process(
                prepared, outcome.pending_payloads, wave=pass_index)
            continue
        if outcome.completed == 0:
            raise Phase3MainLiveError(
                f"main made no progress with {target - complete} required rows remaining")
    raise Phase3MainLiveError(
        f"main did not converge within the derived {max_passes}-pass bound")


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        dict(value), ensure_ascii=True, allow_nan=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _record_terminal_checker_malformed(
    prepared: PreparedMainRun,
    store: phase3_main_finalization.MainTerminalDispositionStore,
    cell_key: str,
) -> None:
    paths = prepared.identity.paths
    events = api_client._read_usage_events(paths.usage_ledger)  # noqa: SLF001
    candidates = []
    for event in events:
        metadata = event.get("metadata") or {}
        if (
            event.get("status") == "success"
            and metadata.get("cell_key") == cell_key
            and metadata.get("call_role") == "query_checker"
            and isinstance(event.get("attempt_id"), str)
        ):
            try:
                phase3_main_finalization.mechanically_validate_checker_malformed(
                    cell_key=cell_key,
                    ledger_attempt_id=event["attempt_id"],
                    expected_checker_model=str(
                        prepared.protocol["roster"]["query_checker"]),
                    usage_ledger_path=paths.usage_ledger,
                    request_journal_path=paths.request_journal,
                    journal_execution_identity=prepared.identity.journal_execution_identity,
                )
            except phase3_main_finalization.TerminalDispositionError:
                continue
            candidates.append(str(event["attempt_id"]))
    if len(candidates) != 1:
        raise Phase3MainLiveError(
            "checker_malformed halt does not join to exactly one mechanically malformed "
            f"checker attempt: {candidates!r}")
    store.record_checker_malformed(
        cell_key,
        ledger_attempt_id=candidates[0],
        usage_ledger_path=paths.usage_ledger,
        request_journal_path=paths.request_journal,
        journal_execution_identity=prepared.identity.journal_execution_identity,
        result_cell_keys=phase3_main_finalization.load_result_cell_keys(paths.results),
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),
    )


def _review_wave_same_process(
    prepared: PreparedMainRun,
    pending_payloads: Sequence[Mapping[str, Any]],
    *,
    wave: int,
) -> None:
    """Dispatch a bound reviewer wave and commit it without re-entering the run lease."""
    paths = prepared.identity.paths
    current_authorization = _revalidate_authenticated_authorization(prepared)
    capacity_plan, _capacity_validation = _revalidate_capacity_snapshot(prepared)
    worklist = export_reviewer_worklist(
        pending_payloads,
        str(prepared.reviewer_prompt["prompt"]),
        paths.reviewer_worklist,
    )
    items = list(worklist["items"])
    payload_hash = hashlib.sha256(
        "".join(str(item["payload_sha256"]) for item in items).encode("utf-8")
    ).hexdigest()[:12]
    packet_dir = paths.review_packets_root / (
        f"wave-{wave:03d}-{payload_hash}-{uuid.uuid4().hex[:8]}")
    packet_dir.mkdir()
    index: list[dict[str, Any]] = []
    for number, item in enumerate(items, 1):
        prompt = str(item["subagent_prompt"])
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if prompt_sha != item["subagent_prompt_sha256"]:
            raise Phase3MainLiveError("reviewer worklist prompt hash drifted")
        filename = f"{number:05d}_{str(item['payload_sha256'])[:12]}.txt"
        (packet_dir / filename).write_text(prompt, encoding="utf-8", newline="")
        index.append({
            "n": number,
            "file": filename,
            "payload_sha256": item["payload_sha256"],
            "prompt_sha256": prompt_sha,
        })
    (packet_dir / "INDEX.json").write_text(
        json.dumps({"count": len(index), "items": index}, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    rulings_path = packet_dir / "rulings.jsonl"
    runtime = prepared.manifest["runtime"]
    reviewer_configuration = capacity_plan["reviewer_configuration"]
    command = [
        sys.executable,
        str(prepared.project_root / "scripts" / "codex_reviewer_batch.py"),
        "--packets", str(packet_dir),
        "--out", str(rulings_path),
        "--codex", str(reviewer_configuration["reviewer_cli_resolved_path"]),
        "--model", str(runtime["reviewer_model"]),
        "--effort", str(runtime["reviewer_reasoning_effort"]),
        "--concurrency", str(runtime["reviewer_concurrency"]),
        "--not-after-utc", str(current_authorization["valid_until_utc"]),
    ]
    phase3_main_manifest.validate_main_manifest(
        prepared.manifest,
        project_root=prepared.project_root,
        verify_files=True,
        verify_runtime=True,
    )
    _verify_execution_code_root(prepared.project_root)
    _verify_clean_git_identity(prepared.manifest, prepared.project_root)
    completed = subprocess.run(command, cwd=prepared.project_root, capture_output=True, text=True)
    if completed.returncode != 0:
        raise Phase3MainLiveError(
            f"reviewer wave {wave} failed with exit {completed.returncode}: "
            f"{completed.stderr[-500:]}")
    rows = []
    for line_number, line in enumerate(
        rulings_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            raise Phase3MainLiveError(
                f"reviewer rulings contain a blank row at line {line_number}")
        row = json.loads(line)
        if not isinstance(row, dict):
            raise Phase3MainLiveError("reviewer ruling is not an object")
        rows.append(row)
    expected = {item["payload_sha256"]: item["prompt_sha256"] for item in index}
    if len(rows) != len(expected) or {row.get("payload_sha256") for row in rows} != set(
        expected):
        raise Phase3MainLiveError("reviewer wave result identity set is not exact")
    commit_entries = []
    for row in rows:
        payload = row["payload_sha256"]
        if "tool_uses" in row:
            if row.get("prompt_sha256") != expected[payload]:
                raise Phase3MainLiveError("reviewer ruling prompt proof drifted")
            commit_entries.append({
                "payload_sha256": payload,
                "raw_output": row.get("raw_output"),
                "prompt_sha256": row.get("prompt_sha256"),
            })
        else:
            commit_entries.append({
                "payload_sha256": payload,
                "status": "reviewer_error",
                "raw_output": row.get("raw_output"),
            })
    counts = commit_decisions_into(
        DualGateDecisionStore(paths.decisions), worklist, commit_entries)
    _append_jsonl(paths.reviewer_index, {
        "wave": wave,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload_count": len(items),
        "packet_directory": packet_dir.as_posix(),
        "packet_index_raw_sha256": _raw_sha256(packet_dir / "INDEX.json"),
        "rulings_raw_sha256": _raw_sha256(rulings_path),
        "commit_counts": counts,
    })


def _finalize_main(
    prepared: PreparedMainRun,
    terminal_store: phase3_main_finalization.MainTerminalDispositionStore,
) -> dict[str, Any]:
    paths = prepared.identity.paths
    artifacts = {
        "result_store": paths.results,
        "usage_ledger": paths.usage_ledger,
        "usage_ledger_state": api_client.usage_ledger_state_path(paths.usage_ledger),
        "request_journal": paths.request_journal,
        "review_decisions": paths.decisions,
        "context_blocklist": prepared.input_paths["context_blocklist"],
        "terminal_dispositions": paths.terminal_dispositions,
        "analysis_pins": prepared.input_paths["analysis_pins"],
        "run_log": paths.run_log,
        "reviewer_index": paths.reviewer_index,
        "reviewer_worklist": paths.reviewer_worklist,
        "provider_error_log": paths.provider_error_log,
    }
    current_authorization = _revalidate_authenticated_authorization(prepared)
    authorization_sha = canonical_sha256(current_authorization)
    finalization = phase3_main_finalization.build_finalization_admission(
        run_id=prepared.identity.run_id,
        manifest_canonical_sha256=prepared.identity.manifest_sha256,
        authorization_canonical_sha256=authorization_sha,
        authorization_raw_sha256=prepared.authorization_raw_sha256,
        authorization_signature_raw_sha256=(
            prepared.authorization_signature_raw_sha256),
        inventory=prepared.inventory,
        context_blocklist_path=prepared.input_paths["context_blocklist"],
        terminal_store=terminal_store,
        result_store_path=paths.results,
        usage_ledger_path=paths.usage_ledger,
        request_journal_path=paths.request_journal,
        journal_execution_identity=prepared.identity.journal_execution_identity,
        analysis_pins_path=prepared.input_paths["analysis_pins"],
        artifact_paths=artifacts,
        expected_oracle_model=str(prepared.protocol["roster"]["oracle"]),
        prior_reconciled_usd=str(
            prepared.manifest["spend"]["prior_reconciled_usd"]),
        stage_cap_usd=str(current_authorization["stage_cap_usd"]),
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    phase3_main_finalization.validate_finalization_admission(
        finalization,
        run_id=prepared.identity.run_id,
        manifest_canonical_sha256=prepared.identity.manifest_sha256,
        authorization_canonical_sha256=authorization_sha,
        authorization_raw_sha256=prepared.authorization_raw_sha256,
        authorization_signature_raw_sha256=(
            prepared.authorization_signature_raw_sha256),
        inventory=prepared.inventory,
        context_blocklist_path=prepared.input_paths["context_blocklist"],
        terminal_store=terminal_store,
        result_store_path=paths.results,
        usage_ledger_path=paths.usage_ledger,
        request_journal_path=paths.request_journal,
        journal_execution_identity=prepared.identity.journal_execution_identity,
        analysis_pins_path=prepared.input_paths["analysis_pins"],
        artifact_paths=artifacts,
        expected_oracle_model=str(prepared.protocol["roster"]["oracle"]),
        prior_reconciled_usd=str(
            prepared.manifest["spend"]["prior_reconciled_usd"]),
        stage_cap_usd=str(current_authorization["stage_cap_usd"]),
    )
    completed_authorization = _revalidate_authenticated_authorization(prepared)
    completed_at = datetime.now(timezone.utc)
    phase3_main_manifest.validate_main_authorization(
        completed_authorization,
        prepared.manifest,
        as_of=completed_at,
    )
    finalization = {
        **finalization,
        "recorded_at_utc": completed_at.isoformat(),
    }
    phase3_main_finalization.write_finalization_admission(paths.finalization, finalization)
    finalization_raw_sha = _raw_sha256(paths.finalization)
    analysis_rc = phase3_main_analysis.main([
        "--results", str(paths.results),
        "--manifest", str(prepared.manifest_path),
        "--authorization", str(prepared.authorization_path),
        "--protocol", str(prepared.input_paths["protocol"]),
        "--pins", str(prepared.input_paths["analysis_pins"]),
        "--finalization", str(paths.finalization),
        "--out", str(paths.analysis_results),
        "--project-root", str(prepared.project_root),
    ])
    if analysis_rc != 0:
        raise Phase3MainLiveError(f"main analysis returned exit {analysis_rc}")
    if _raw_sha256(paths.finalization) != finalization_raw_sha:
        raise Phase3MainLiveError("main finalization bytes changed during analysis")
    output_hashes = _completion_output_hashes(prepared)
    completion = {
        "schema_version": "phase3_main_completion_v1",
        "status": "complete",
        "run_id": prepared.identity.run_id,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        "authorization_canonical_sha256": authorization_sha,
        "finalization_raw_sha256": finalization_raw_sha,
        "analysis_results_raw_sha256": _raw_sha256(paths.analysis_results),
        "output_hashes": output_hashes,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
    }
    _write_exclusive_json(paths.completion, completion)
    completion_sha = _raw_sha256(paths.completion)
    _write_exclusive_json(_identity_complete_path(prepared.identity), {
        "schema_version": IDENTITY_COMPLETE_SCHEMA,
        "status": "complete",
        "run_id": prepared.identity.run_id,
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        "completion_path": paths.completion.as_posix(),
        "completion_raw_sha256": completion_sha,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    return completion


def _tree_sha256(path: Path) -> str:
    if not path.is_dir():
        raise Phase3MainLiveError(f"expected output directory is missing: {path}")
    rows = []
    for child in sorted((item for item in path.rglob("*") if item.is_file()),
                        key=lambda item: item.relative_to(path).as_posix()):
        rows.append({
            "path": child.relative_to(path).as_posix(),
            "raw_sha256": _raw_sha256(child),
            "bytes": child.stat().st_size,
        })
    return canonical_sha256(rows)


def _completion_output_hashes(prepared: PreparedMainRun) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    paths = prepared.manifest_validation["output_paths"]
    for name in sorted(paths):
        path = Path(paths[name])
        if name == "completion":
            result[name] = None
        elif name == "review_packets_root":
            result[name] = _tree_sha256(path)
        else:
            if not path.is_file():
                raise Phase3MainLiveError(f"required final output is missing: {path}")
            result[name] = _raw_sha256(path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Validate or execute one exact Phase 3 main launch.

    The mode flag is deliberately required. Merely supplying a manifest and authorization
    cannot start provider work.
    """
    parser = argparse.ArgumentParser(prog="phase3_main_live")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--authorization", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--validate-only", action="store_true",
        help="validate every launch binding without creating formal state or a provider client",
    )
    mode.add_argument(
        "--run", action="store_true",
        help="consume the exact identity and perform the one authorized formal attempt",
    )
    args = parser.parse_args(argv)

    try:
        if args.validate_only:
            prepared = load_prepared_main(
                args.manifest,
                args.authorization,
                verify_git=True,
            )
            payload: Mapping[str, Any] = {
                "status": "validated_only",
                "run_id": prepared.identity.run_id,
                "manifest_canonical_sha256": prepared.identity.manifest_sha256,
                "execution_started": False,
                "provider_client_created": False,
            }
        else:
            payload = run_main(
                args.manifest,
                args.authorization,
            )
    except Exception as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(dict(payload), sort_keys=True, ensure_ascii=True))
    return 0


__all__ = [
    "Phase3MainLiveError",
    "PreparedMainRun",
    "load_prepared_main",
    "main",
    "run_main",
]


if __name__ == "__main__":
    raise SystemExit(main())
