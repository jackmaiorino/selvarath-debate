"""Offline launch validator and blocked driver for the Phase 3 main measurement.

The public preparation path is read-only. It derives the exact main inventory and validates
every bound input before accepting a separate, active owner authorization. The public run path
has no client, factory, path, concurrency, cap, or resume injection points. It acquires one
persistent registry lease, creates and binds fresh formal stores, constructs the private provider
client without dispatch, and records the identity start immediately before formal work.

Importing this module and calling :func:`load_prepared_main` cannot create a provider client or
mutate formal output state. The public paid path also refuses before formal state mutation until
the remaining launch blockers listed below are closed.
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
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, MAX_EMAX, MIN_EMIN, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any, cast

from rejudge import (
    api_client,
    phase3_main_authorization,
    phase3_main_billing_reconciliation,
    phase3_main_capacity_execution,
    phase3_main_harness,
    phase3_main_context,
    phase3_main_finalization,
    phase3_main_manifest,
    phase3_main_runtime_policies,
    phase3_main_together_billing_capture,
    phase3_main_reviewer_provenance,
    phase3_main_reviewer_commit,
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
    export_reviewer_worklist,
)
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_canary_runner import run_canary
from rejudge.phase2_dual_gate import parse_reviewer_output
from rejudge.phase2_execution import canonical_sha256
from rejudge.request_journal import JournalingClient, RequestJournal, find_ambiguous_dispatches
from scripts import (
    codex_reviewer_batch,
    phase3_main_analysis,
    phase3_main_review_capacity_preflight,
)
from scripts.phase3_preseed_transcripts import _rows_for_bundle, _verify_bundle_hash


IDENTITY_START_SCHEMA = "phase3_main_identity_start_v3"
IDENTITY_COMPLETE_SCHEMA = "phase3_main_identity_complete_v1"
IDENTITY_START_FIELDS = frozenset({
    "schema_version",
    "status",
    "run_id",
    "manifest_canonical_sha256",
    "manifest_identity_sha256",
    "authorization_id",
    "authorization_canonical_sha256",
    "authorization_raw_sha256",
    "authorization_signature_raw_sha256",
    "artifact_root",
    "usage_ledger_schema_version",
    "usage_ledger_id",
    "usage_ledger_path",
    "usage_ledger_state_path",
    "usage_ledger_identity_canonical_sha256",
    "usage_ledger_genesis_event_hash",
    "usage_ledger_genesis_raw_sha256",
    "usage_ledger_genesis_state_raw_sha256",
    "recorded_at_utc",
})
IDENTITY_VOID_SCHEMA = "phase3_main_identity_void_v2"
IDENTITY_VOID_FIELDS = frozenset({
    "schema_version",
    "status",
    "run_id",
    "manifest_canonical_sha256",
    "manifest_identity_sha256",
    "artifact_root",
    "start_record_path",
    "start_record_raw_sha256",
    "usage_ledger_path",
    "usage_ledger_raw_sha256",
    "usage_ledger_state_path",
    "usage_ledger_state_raw_sha256",
    "usage_ledger_identity_canonical_sha256",
    "usage_ledger_genesis_event_hash",
    "reason_code",
    "recorded_at_utc",
})
ENVIRONMENTAL_INTERRUPTION_REASONS = frozenset({
    "host_failure",
    "network_outage",
    "power_loss",
    "provider_outage",
})
REVIEWER_WAVE_SCHEMA = phase3_main_reviewer_commit.REVIEWER_WAVE_SCHEMA
REVIEWER_PROMPT_SCHEMA = "phase2_reviewer_prompt_v1"
ANALYSIS_RESULT_SCHEMA = "phase3_main_analysis_results_v1"
ANALYSIS_RESULT_FIELDS = frozenset({
    "schema_version",
    "bootstrap",
    "domains",
    "primary",
    "primary_sup_t",
    "S1",
    "valid_only_sensitivity",
    "all_invalid_scenarios",
    "strict_support",
    "per_judge_descriptive",
    "capability_slope",
    "paired_mirror_diagnostics",
    "invalid_counts_by_judge_condition",
    "invalid_counts_by_budget_judge_replicate_block",
    "terminal_invalid_counts_by_judge_condition",
    "claims",
    "integrity",
})
ANALYSIS_INTEGRITY_FIELDS = frozenset({
    "repository_head_at_analysis",
    "engine_git_commit",
    "engine_git_state",
    "engine_git_status_porcelain",
    "engine_raw_sha256",
    "python_version",
    "protocol_canonical_sha256",
    "protocol_question_bank_bundle_sha256",
    "protocol_phase2_question_source_canonical_sha256",
    "main_question_ids_canonical_sha256",
    "main_question_rows_canonical_sha256",
    "pins_raw_sha256",
    "results_raw_sha256",
    "finalization_raw_sha256",
    "terminal_cell_keys_raw_sha256",
    "context_ineligible_cell_keys_raw_sha256",
    "record_count",
})
LIVE_PROJECT_ROOT = Path(__file__).resolve().parents[1]
OWNER_SIGNATURE_NAMESPACE = phase3_main_authorization.OWNER_SIGNATURE_NAMESPACE
OWNER_SIGNATURE_PRINCIPAL = phase3_main_authorization.OWNER_SIGNATURE_PRINCIPAL
SSH_KEYGEN_PATH = phase3_main_authorization.SSH_KEYGEN_PATH
PRODUCTION_EXECUTION_BLOCKERS = (
    "the owner signing key is not pinned",
    "the protocol cap and proposed main cap are not ratified to one value",
    "Together billing-usage API access is not enabled for the selected organization",
    "no authenticated provider settlement watermark has been materialized",
    "no approved provider account identity has been materialized in a signed main manifest",
    "predecessor-ledger completeness has no independent authoritative inventory",
    "fresh reviewer capacity evidence has not been authorized or measured",
)

ACTIVE_MARKER_FIELDS = frozenset({
    "schema_version",
    "status",
    "run_id",
    "manifest_sha256",
    "artifact_root",
    "artifact_root_sha256",
    "journal_execution_identity",
    "started_at_utc",
    "pid",
})


class Phase3MainLiveError(RuntimeError, ValueError):
    """A Phase 3 main launch or execution invariant failed closed."""


def _subprocess_environment_without_together_credentials() -> dict[str, str]:
    """Return the current environment without provider credentials or endpoint overrides."""
    forbidden = {"TOGETHER_API_KEY", "TOGETHER_BASE_URL"}
    return {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in forbidden
    }


def _require_production_execution_unblocked() -> None:
    """Keep the public paid path closed until every remaining launch blocker is closed."""
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
    price_change_policy_validation: Mapping[str, Any]
    reviewer_usage_policy_validation: Mapping[str, Any]
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
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {value!r}")),
        )
        pending = [value]
        while pending:
            current = pending.pop()
            if isinstance(current, float) and not math.isfinite(current):
                raise ValueError("non-finite JSON number")
            if isinstance(current, Mapping):
                pending.extend(current.values())
            elif isinstance(current, list):
                pending.extend(current)
        return value
    except Phase3MainLiveError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
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
            capture_output=True, text=True,
            env=_subprocess_environment_without_together_credentials()).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
            check=True, capture_output=True, text=True,
            env=_subprocess_environment_without_together_credentials()).stdout
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


def _validate_reviewer_prompt(
    artifact: Mapping[str, Any], failure_policy: Mapping[str, Any],
) -> None:
    if artifact.get("schema_version") != REVIEWER_PROMPT_SCHEMA:
        raise Phase3MainLiveError("unsupported inherited reviewer prompt schema")
    prompt = artifact.get("prompt")
    expected = artifact.get("prompt_sha256")
    if not isinstance(prompt, str) or not prompt:
        raise Phase3MainLiveError("reviewer prompt artifact has no prompt")
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != expected:
        raise Phase3MainLiveError("reviewer prompt differs from its declared hash")
    try:
        phase3_main_reviewer_provenance.validate_main_reviewer_failure_policy(
            failure_policy, reviewer_prompt_sha256=str(expected))
    except phase3_main_reviewer_provenance.MainReviewerProvenanceError as exc:
        raise Phase3MainLiveError(
            f"reviewer failure policy failed: {exc}") from exc


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
    plan_path: Path | None = None,
    result_path: Path | None = None,
    execution_manifest_path: Path | None = None,
    execution_authorization_path: Path | None = None,
    execution_authorization_signature_path: Path | None = None,
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
        capacity_completed_at = phase3_main_manifest._utc(  # noqa: SLF001
            result.get("completed_at_utc"), "capacity_result.completed_at_utc")
        capacity_expires_at = phase3_main_manifest._utc(  # noqa: SLF001
            measurement.get("evidence_expires_at_utc"),
            "capacity measurement evidence_expires_at_utc",
        )
        if capacity_expires_at < capacity_completed_at:
            raise ValueError("capacity validity window ends before measurement completion")
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase3MainLiveError(f"review capacity evidence failed: {exc}") from exc
    validation = {
        "plan": plan_validation,
        "measurement": measurement,
        "capacity_completed_at": capacity_completed_at,
        "capacity_expires_at": capacity_expires_at,
    }
    provenance_paths = (
        plan_path,
        result_path,
        execution_manifest_path,
        execution_authorization_path,
        execution_authorization_signature_path,
    )
    if not any(path is not None for path in provenance_paths):
        return validation
    if not all(path is not None for path in provenance_paths):
        raise Phase3MainLiveError(
            "review capacity execution provenance paths are incomplete"
        )
    plan_path = cast(Path, plan_path)
    result_path = cast(Path, result_path)
    execution_manifest_path = cast(Path, execution_manifest_path)
    execution_authorization_path = cast(Path, execution_authorization_path)
    execution_authorization_signature_path = cast(
        Path, execution_authorization_signature_path
    )
    expected_signature_path = execution_authorization_path.with_name(
        f"{execution_authorization_path.name}.sig"
    ).resolve()
    if execution_authorization_signature_path.resolve() != expected_signature_path:
        raise Phase3MainLiveError(
            "review capacity authorization signature is not its exact detached sidecar"
        )
    signature_raw = _stable_regular_file_bytes(
        execution_authorization_signature_path,
        "capacity execution authorization signature",
    )
    try:
        context = phase3_main_capacity_execution.load_capacity_context(
            plan_path,
            project_root=LIVE_PROJECT_ROOT,
        )
        if dict(context.plan) != dict(plan):
            raise phase3_main_capacity_execution.CapacityExecutionError(
                "capacity plan differs from its execution context"
            )
        execution_manifest_raw, execution_manifest = (
            phase3_main_capacity_execution.load_execution_manifest(
                execution_manifest_path,
                context=context,
            )
        )
        if Path(str(execution_manifest["result_path"])).resolve() != result_path.resolve():
            raise phase3_main_capacity_execution.CapacityExecutionError(
                "capacity execution manifest binds another result path"
            )
        execution_history = cast(
            Mapping[str, Any], execution_manifest["dispatch_history"]
        )
        if Path(str(execution_history["path"])).resolve() != history_path.resolve():
            raise phase3_main_capacity_execution.CapacityExecutionError(
                "capacity execution manifest binds another dispatch history"
            )
        authorization_raw, authorization = (
            phase3_main_capacity_execution.load_authenticated_capacity_authorization(
                execution_authorization_path
            )
        )
        validated_authorization = (
            phase3_main_capacity_execution.validate_authorization(
                authorization,
                manifest=execution_manifest,
                manifest_raw=execution_manifest_raw,
                observed_at=capacity_completed_at,
            )
        )
        execution_validation = (
            phase3_main_capacity_execution.validate_execution_result(
                result,
                manifest=execution_manifest,
                manifest_raw=execution_manifest_raw,
                authorization=validated_authorization,
                authorization_raw=authorization_raw,
                context=context,
                as_of_utc=as_of,
                require_current_freshness=require_current_freshness,
            )
        )
        if _stable_regular_file_bytes(
            execution_authorization_signature_path,
            "capacity execution authorization signature",
        ) != signature_raw:
            raise phase3_main_capacity_execution.CapacityExecutionError(
                "capacity authorization signature changed during main admission"
            )
    except phase3_main_capacity_execution.CapacityExecutionError as exc:
        raise Phase3MainLiveError(
            f"review capacity execution provenance failed: {exc}"
        ) from exc
    return {
        **validation,
        "execution_provenance": {
            "manifest_raw_sha256": hashlib.sha256(
                execution_manifest_raw
            ).hexdigest(),
            "authorization_raw_sha256": hashlib.sha256(
                authorization_raw
            ).hexdigest(),
            "authorization_signature_raw_sha256": hashlib.sha256(
                signature_raw
            ).hexdigest(),
            "authorization_id": validated_authorization["authorization_id"],
            "run_id": execution_manifest["run_id"],
            "attempt_id": execution_manifest["attempt_id"],
            "validation": execution_validation,
        },
    }


def _reopen_capacity_execution_provenance_inputs(
    manifest: Mapping[str, Any],
    input_paths: Mapping[str, Path],
) -> None:
    """Reopen every main-bound capacity execution authority input."""
    for name, label in (
        ("capacity_execution_manifest", "capacity execution manifest"),
        ("capacity_execution_authorization", "capacity execution authorization"),
        (
            "capacity_execution_authorization_signature",
            "capacity execution authorization signature",
        ),
    ):
        _load_bound_input_bytes(manifest, input_paths, name, label)


def _reviewer_cli_version(path: Path) -> str:
    try:
        completed = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=30,
            env=_subprocess_environment_without_together_credentials(),
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


def _admit_reviewer_wave_quantity(
    prepared: PreparedMainRun,
    *,
    previously_admitted: int,
    incoming: int,
) -> int:
    """Apply the signed aggregate reviewer-dispatch ceiling before child release."""
    maximum = prepared.reviewer_usage_policy_validation.get(
        "maximum_reviewer_dispatches")
    for value, label in (
        (previously_admitted, "previous reviewer dispatch count"),
        (incoming, "incoming reviewer dispatch count"),
        (maximum, "maximum reviewer dispatch count"),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise Phase3MainLiveError(f"{label} must be an integer")
    assert isinstance(maximum, int)
    if previously_admitted < 0 or incoming <= 0 or maximum <= 0:
        raise Phase3MainLiveError("reviewer dispatch counts must be positive and monotone")
    admitted = previously_admitted + incoming
    if admitted > maximum:
        raise Phase3MainLiveError(
            "reviewer dispatch wave would exceed the exact authorized usage ceiling")
    return admitted


def _validate_reviewer_invocation_capacity_binding(
    invocation: Mapping[str, Any],
    *,
    reviewer_configuration: Mapping[str, Any],
    capacity_validation: Mapping[str, Any],
) -> str:
    """Require one live reviewer invocation to match the measured wrapper and host."""
    runtime_evidence = capacity_validation.get("runtime")
    if not isinstance(runtime_evidence, Mapping):
        raise Phase3MainLiveError(
            "review capacity validation omits its runtime binding")
    expected_cli_path = Path(str(
        reviewer_configuration.get("reviewer_cli_resolved_path"))).resolve().as_posix()
    if invocation.get("codex_cli_resolved_path") != expected_cli_path:
        raise Phase3MainLiveError(
            "reviewer invocation used a CLI other than the measured capacity CLI")
    if invocation.get("codex_cli_wrapper_raw_sha256") != (
        reviewer_configuration.get("reviewer_cli_wrapper_raw_sha256")
    ):
        raise Phase3MainLiveError(
            "reviewer invocation CLI wrapper hash differs from capacity evidence")
    if invocation.get("codex_cli_wrapper_byte_count") != (
        reviewer_configuration.get("reviewer_cli_wrapper_byte_count")
    ):
        raise Phase3MainLiveError(
            "reviewer invocation CLI wrapper size differs from capacity evidence")
    if invocation.get("host_identity") != runtime_evidence.get("host_identity"):
        raise Phase3MainLiveError(
            "reviewer invocation host differs from the capacity-measured host")
    expected_cli_version = runtime_evidence.get("reviewer_cli_version")
    if (
        not isinstance(expected_cli_version, str)
        or not expected_cli_version
        or invocation.get("codex_cli_version") != expected_cli_version
    ):
        raise Phase3MainLiveError(
            "reviewer invocation CLI version differs from capacity evidence")
    return expected_cli_path


def _validate_reviewer_invocation_time_binding(
    outcome: Mapping[str, Any],
    *,
    authorization: Mapping[str, Any],
    capacity_validation: Mapping[str, Any],
    observed_at: datetime,
) -> tuple[datetime, datetime]:
    """Enforce capacity and dispatch-only authorization time bounds before commit."""
    capacity_completed_at = capacity_validation.get("capacity_completed_at")
    capacity_expires_at = capacity_validation.get("capacity_expires_at")
    if not isinstance(capacity_completed_at, datetime) or not isinstance(
        capacity_expires_at, datetime
    ):
        raise Phase3MainLiveError(
            "review capacity validation omits its validity window")
    try:
        started_at = phase3_main_manifest._utc(  # noqa: SLF001
            outcome.get("started_at_utc"), "reviewer evidence started_at_utc")
        completed_at = phase3_main_manifest._utc(  # noqa: SLF001
            outcome.get("completed_at_utc"), "reviewer evidence completed_at_utc")
        approved_at = phase3_main_manifest._utc(  # noqa: SLF001
            authorization.get("approved_at_utc"), "authorization.approved_at_utc")
        deadline = phase3_main_manifest._utc(  # noqa: SLF001
            authorization.get("valid_until_utc"), "authorization.valid_until_utc")
    except (TypeError, ValueError) as exc:
        raise Phase3MainLiveError(
            "reviewer invocation evidence has an invalid timeline") from exc
    if not capacity_completed_at <= started_at <= capacity_expires_at:
        raise Phase3MainLiveError(
            "reviewer invocation started outside the capacity validity window")
    if not approved_at <= started_at <= completed_at <= observed_at:
        raise Phase3MainLiveError(
            "reviewer invocation falls outside the authorized wave timeline")
    if started_at > deadline:
        raise Phase3MainLiveError(
            "reviewer invocation started after the authorization deadline")
    return started_at, completed_at


def _reviewer_guard_artifact_binding(
    path: Path,
    *,
    expected_raw_sha256: str,
    expected_byte_count: int | None = None,
    label: str,
) -> dict[str, Any]:
    supplied = Path(path)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise Phase3MainLiveError(
            f"reviewer dispatch guard {label} path must be absolute and unlinked")
    resolved = supplied.resolve()
    try:
        first = resolved.read_bytes()
        second = resolved.read_bytes()
    except OSError as exc:
        raise Phase3MainLiveError(
            f"reviewer dispatch guard {label} is unavailable") from exc
    if first != second:
        raise Phase3MainLiveError(
            f"reviewer dispatch guard {label} changed while snapshotted")
    observed_sha = hashlib.sha256(first).hexdigest()
    if observed_sha != expected_raw_sha256 or (
        expected_byte_count is not None and len(first) != expected_byte_count
    ):
        raise Phase3MainLiveError(
            f"reviewer dispatch guard {label} differs from its bound identity")
    return {
        "path": resolved.as_posix(),
        "raw_sha256": observed_sha,
        "byte_count": len(first),
    }


def _build_reviewer_dispatch_guard(
    prepared: PreparedMainRun,
    *,
    authorization: Mapping[str, Any],
    capacity_validation: Mapping[str, Any],
    reviewer_configuration: Mapping[str, Any],
    packet_dir: Path,
    output_path: Path,
    worklist_snapshot_path: Path,
    packet_index_path: Path,
    packet_bindings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze the complete per-invocation reviewer dispatch authority."""
    capacity_completed_at = capacity_validation.get("capacity_completed_at")
    capacity_expires_at = capacity_validation.get("capacity_expires_at")
    if not isinstance(capacity_completed_at, datetime) or not isinstance(
        capacity_expires_at, datetime
    ):
        raise Phase3MainLiveError(
            "reviewer dispatch guard lacks the capacity validity window")
    signature_path = prepared.authorization_path.with_name(
        f"{prepared.authorization_path.name}.sig")
    cli_path = Path(str(
        reviewer_configuration.get("reviewer_cli_resolved_path"))).resolve()
    runtime_evidence = capacity_validation.get("runtime")
    if not isinstance(runtime_evidence, Mapping):
        raise Phase3MainLiveError(
            "reviewer dispatch guard lacks the capacity runtime binding")
    reviewer_cli_version = runtime_evidence.get("reviewer_cli_version")
    capacity_host_identity = runtime_evidence.get("host_identity")
    if not isinstance(reviewer_cli_version, str) or not reviewer_cli_version:
        raise Phase3MainLiveError(
            "reviewer dispatch guard lacks the measured CLI version")
    if not isinstance(capacity_host_identity, str) or not capacity_host_identity:
        raise Phase3MainLiveError(
            "reviewer dispatch guard lacks the capacity host identity")
    reviewer_cli_wrapper_sha = reviewer_configuration.get(
        "reviewer_cli_wrapper_raw_sha256")
    reviewer_cli_wrapper_byte_count = reviewer_configuration.get(
        "reviewer_cli_wrapper_byte_count")
    if (
        not isinstance(reviewer_cli_wrapper_sha, str)
        or len(reviewer_cli_wrapper_sha) != 64
        or isinstance(reviewer_cli_wrapper_byte_count, bool)
        or not isinstance(reviewer_cli_wrapper_byte_count, int)
        or reviewer_cli_wrapper_byte_count < 0
    ):
        raise Phase3MainLiveError(
            "reviewer dispatch guard lacks the measured CLI wrapper identity")
    batch_runner_path = (
        prepared.project_root / "scripts" / "codex_reviewer_batch.py").resolve()
    loaded_runner_path, runner_sha, runner_bytes = (
        codex_reviewer_batch._batch_runner_identity())  # noqa: SLF001
    if Path(loaded_runner_path).resolve() != batch_runner_path:
        raise Phase3MainLiveError(
            "reviewer dispatch guard batch runner differs from loaded project code")
    bindings = {
        "authorization": _reviewer_guard_artifact_binding(
            prepared.authorization_path,
            expected_raw_sha256=prepared.authorization_raw_sha256,
            label="authorization",
        ),
        "authorization_signature": _reviewer_guard_artifact_binding(
            signature_path,
            expected_raw_sha256=prepared.authorization_signature_raw_sha256,
            label="authorization signature",
        ),
        "capacity_plan": _reviewer_guard_artifact_binding(
            prepared.input_paths["capacity_plan"],
            expected_raw_sha256=str(
                prepared.manifest["input_bindings"]["capacity_plan"]["sha256"]),
            label="capacity plan",
        ),
        "capacity_result": _reviewer_guard_artifact_binding(
            prepared.input_paths["capacity_result"],
            expected_raw_sha256=str(
                prepared.manifest["input_bindings"]["capacity_result"]["sha256"]),
            label="capacity result",
        ),
        "capacity_dispatch_history": _reviewer_guard_artifact_binding(
            prepared.input_paths["capacity_dispatch_history"],
            expected_raw_sha256=str(
                prepared.manifest["input_bindings"][
                    "capacity_dispatch_history"]["sha256"]),
            label="capacity dispatch history",
        ),
        "reviewer_cli_wrapper": _reviewer_guard_artifact_binding(
            cli_path,
            expected_raw_sha256=reviewer_cli_wrapper_sha,
            expected_byte_count=reviewer_cli_wrapper_byte_count,
            label="reviewer CLI wrapper",
        ),
        "worklist_snapshot": _reviewer_guard_artifact_binding(
            worklist_snapshot_path,
            expected_raw_sha256=_raw_sha256(worklist_snapshot_path),
            label="worklist snapshot",
        ),
        "packet_index": _reviewer_guard_artifact_binding(
            packet_index_path,
            expected_raw_sha256=_raw_sha256(packet_index_path),
            label="packet index",
        ),
        "batch_runner": _reviewer_guard_artifact_binding(
            batch_runner_path,
            expected_raw_sha256=runner_sha,
            expected_byte_count=runner_bytes,
            label="batch runner",
        ),
    }
    approved_at = phase3_main_manifest._utc(  # noqa: SLF001
        authorization.get("approved_at_utc"), "authorization.approved_at_utc")
    valid_until = phase3_main_manifest._utc(  # noqa: SLF001
        authorization.get("valid_until_utc"), "authorization.valid_until_utc")
    runtime = prepared.manifest.get("runtime")
    if not isinstance(runtime, Mapping):  # pragma: no cover - manifest validation
        raise Phase3MainLiveError("reviewer dispatch guard lacks main runtime")
    resolved_packet_dir = packet_dir.resolve()
    resolved_output_path = output_path.resolve()
    if (
        not resolved_packet_dir.is_dir()
        or packet_dir.is_symlink()
        or not resolved_output_path.is_file()
        or output_path.is_symlink()
        or resolved_output_path.parent != resolved_packet_dir
    ):
        raise Phase3MainLiveError(
            "reviewer dispatch guard packet directory or output path is unavailable")
    return {
        "schema_version": codex_reviewer_batch.DISPATCH_GUARD_SCHEMA,
        "run_id": prepared.identity.run_id,
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        "authorization_canonical_sha256": canonical_sha256(authorization),
        "authorization_approved_at_utc": approved_at.isoformat(),
        "authorization_valid_until_utc": valid_until.isoformat(),
        "capacity_completed_at_utc": capacity_completed_at.isoformat(),
        "capacity_expires_at_utc": capacity_expires_at.isoformat(),
        "reviewer_model": str(runtime["reviewer_model"]),
        "reviewer_reasoning_effort": str(
            runtime["reviewer_reasoning_effort"]),
        "reviewer_concurrency": int(runtime["reviewer_concurrency"]),
        "reviewer_cli_version": reviewer_cli_version,
        "capacity_host_identity": capacity_host_identity,
        "packet_directory": resolved_packet_dir.as_posix(),
        "output_path": resolved_output_path.as_posix(),
        "packet_bindings": [dict(binding) for binding in packet_bindings],
        "artifact_bindings": bindings,
    }


def _validate_reviewer_dispatch_reservation(
    packet_dir: Path,
    *,
    guard_evidence: Mapping[str, Any],
    guard_raw_sha256: str,
    packet_meta: Mapping[str, Any],
    output_path: Path,
    reviewer_model: str,
    reviewer_reasoning_effort: str,
    reviewer_concurrency: int,
    reviewer_cli_version: str,
    capacity_host_identity: str,
    outcome: Mapping[str, Any],
) -> Path:
    """Reopen one exact durable reservation before its ruling may commit."""
    binding = guard_evidence.get("reservation")
    required_binding_fields = frozenset({"path", "raw_sha256", "byte_count"})
    if not isinstance(binding, Mapping) or set(binding) != required_binding_fields:
        raise Phase3MainLiveError(
            "reviewer invocation lacks its exact durable dispatch reservation")
    payload = str(packet_meta["payload_sha256"])
    expected_relative = (
        f"{codex_reviewer_batch.DISPATCH_RESERVATION_DIRECTORY_NAME}/"
        f"{guard_raw_sha256}_{payload}.json"
    )
    if binding.get("path") != expected_relative:
        raise Phase3MainLiveError(
            "reviewer dispatch reservation path differs from its deterministic identity")
    reservation_path = packet_dir / expected_relative
    if reservation_path.is_symlink():
        raise Phase3MainLiveError("reviewer dispatch reservation must not be linked")
    try:
        first = reservation_path.read_bytes()
        second = reservation_path.read_bytes()
    except OSError as exc:
        raise Phase3MainLiveError(
            "reviewer dispatch reservation is unavailable") from exc
    if first != second:
        raise Phase3MainLiveError(
            "reviewer dispatch reservation changed while validated")
    if (
        hashlib.sha256(first).hexdigest() != binding.get("raw_sha256")
        or len(first) != binding.get("byte_count")
    ):
        raise Phase3MainLiveError("reviewer dispatch reservation bytes drifted")
    try:
        reservation = json.loads(
            first.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {value!r}")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise Phase3MainLiveError(
            "reviewer dispatch reservation is not strict UTF-8 JSON") from exc
    expected_fields = frozenset({
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
    if not isinstance(reservation, Mapping) or set(reservation) != expected_fields:
        raise Phase3MainLiveError("reviewer dispatch reservation fields drifted")
    exact_values = {
        "schema_version": codex_reviewer_batch.DISPATCH_RESERVATION_SCHEMA,
        "guard_raw_sha256": guard_raw_sha256,
        "payload_sha256": payload,
        "prompt_sha256": packet_meta["prompt_sha256"],
        "packet_file": packet_meta["file"],
        "packet_directory": packet_dir.resolve().as_posix(),
        "output_path": output_path.resolve().as_posix(),
        "reviewer_model": reviewer_model,
        "reviewer_reasoning_effort": reviewer_reasoning_effort,
        "reviewer_concurrency": reviewer_concurrency,
        "reviewer_cli_version": reviewer_cli_version,
        "capacity_host_identity": capacity_host_identity,
    }
    for field, expected in exact_values.items():
        if reservation.get(field) != expected:
            raise Phase3MainLiveError(
                f"reviewer dispatch reservation {field} drifted")
    reserved_at = reservation.get("reserved_at_utc")
    if (
        not isinstance(reserved_at, str)
        or guard_evidence.get("checked_at_utc") != reserved_at
        or outcome.get("started_at_utc") != reserved_at
    ):
        raise Phase3MainLiveError(
            "reviewer dispatch reservation timestamp differs from invocation start")
    try:
        checked_at = phase3_main_manifest._utc(  # noqa: SLF001
            reserved_at, "reviewer dispatch reservation reserved_at_utc")
        released_at = phase3_main_manifest._utc(  # noqa: SLF001
            guard_evidence.get("released_at_utc"),
            "reviewer dispatch guard released_at_utc",
        )
        completed_at = phase3_main_manifest._utc(  # noqa: SLF001
            outcome.get("completed_at_utc"), "reviewer evidence completed_at_utc")
        approved_at = phase3_main_manifest._utc(  # noqa: SLF001
            guard_evidence.get("authorization_approved_at_utc"),
            "reviewer dispatch guard authorization_approved_at_utc",
        )
        valid_until = phase3_main_manifest._utc(  # noqa: SLF001
            guard_evidence.get("authorization_valid_until_utc"),
            "reviewer dispatch guard authorization_valid_until_utc",
        )
        capacity_completed = phase3_main_manifest._utc(  # noqa: SLF001
            guard_evidence.get("capacity_completed_at_utc"),
            "reviewer dispatch guard capacity_completed_at_utc",
        )
        capacity_expires = phase3_main_manifest._utc(  # noqa: SLF001
            guard_evidence.get("capacity_expires_at_utc"),
            "reviewer dispatch guard capacity_expires_at_utc",
        )
    except (TypeError, ValueError) as exc:
        raise Phase3MainLiveError(
            "reviewer dispatch reservation release timeline is invalid") from exc
    if not (
        checked_at <= released_at <= completed_at
        and approved_at <= released_at <= valid_until
        and capacity_completed <= released_at <= capacity_expires
    ):
        raise Phase3MainLiveError(
            "reviewer dispatch reservation was not actively released before subprocess")
    return reservation_path


class _AuthorizationDeadlineClient:
    """Inject the attempt-0 authorization hook into every new logical paid call."""

    def __init__(self, prepared: PreparedMainRun, inner: Any) -> None:
        self._prepared = prepared
        self._inner = inner

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self._inner, "dry_run", False))

    def complete(self, *args: Any, **kwargs: Any) -> str:
        hook_field = "_logical_dispatch_authorization_hook"
        if hook_field in kwargs:
            raise Phase3MainLiveError(
                "logical dispatch authorization hook is reserved for the main runtime")
        return str(self._inner.complete(
            *args,
            **kwargs,
            _logical_dispatch_authorization_hook=(
                lambda: _authorize_provider_logical_dispatch(self._prepared)),
        ))


def _load_unchanged_authenticated_authorization(
    prepared: PreparedMainRun,
) -> Mapping[str, Any]:
    """Reload the exact signed authorization bytes and semantic object."""
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
    return current


def _revalidate_authenticated_authorization(
    prepared: PreparedMainRun,
    *,
    as_of: datetime | None = None,
) -> Mapping[str, Any]:
    """Require active exact authority at one explicit validation time."""
    current = _load_unchanged_authenticated_authorization(prepared)
    phase3_main_manifest.validate_main_authorization(
        current,
        prepared.manifest,
        as_of=as_of or datetime.now(timezone.utc),
    )
    return current


def _authorize_provider_logical_dispatch(prepared: PreparedMainRun) -> str:
    """Return the durable start time after all local paid-call prerequisites pass."""
    signal_path = prepared.identity.paths.price_change_signal
    signal_publish_temp = prepared.identity.paths.price_change_signal_publish_temp
    if os.path.lexists(signal_path) or os.path.lexists(signal_publish_temp):
        raise Phase3MainLiveError(
            "provider price-change signal or publish stage is present; "
            "no new logical provider call is allowed")
    _revalidate_price_snapshot(prepared)
    current = _load_unchanged_authenticated_authorization(prepared)
    authorized_at = datetime.now(timezone.utc)
    phase3_main_manifest.validate_main_authorization(
        current,
        prepared.manifest,
        as_of=authorized_at,
    )
    return authorized_at.isoformat()


def _revalidate_authenticated_authorization_scope(
    prepared: PreparedMainRun,
) -> Mapping[str, Any]:
    """Revalidate immutable signed scope for local closeout after dispatch expiry."""
    current = _load_unchanged_authenticated_authorization(prepared)
    approved_at = phase3_main_manifest._utc(  # noqa: SLF001
        current["approved_at_utc"], "authorization.approved_at_utc")
    phase3_main_manifest.validate_main_authorization(
        current,
        prepared.manifest,
        as_of=approved_at,
    )
    return current


def _revalidate_final_boundary_inputs(
    prepared: PreparedMainRun,
    finalization: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Reopen launch authority after analysis without extending its spend window."""
    current_manifest = _load_strict_object(prepared.manifest_path, "main manifest")
    current_manifest_sha256 = phase3_main_manifest.manifest_canonical_sha256(
        current_manifest)
    if (
        current_manifest_sha256 != prepared.identity.manifest_sha256
        or current_manifest != dict(prepared.manifest)
    ):
        raise Phase3MainLiveError("main manifest changed before completion")
    try:
        validation = phase3_main_manifest.validate_main_manifest(
            current_manifest,
            project_root=prepared.project_root,
            verify_files=True,
            verify_runtime=True,
        )
    except phase3_main_manifest.MainManifestError as exc:
        raise Phase3MainLiveError(
            f"main manifest failed final completion validation: {exc}") from exc
    if validation.get("manifest_canonical_sha256") != prepared.identity.manifest_sha256:
        raise Phase3MainLiveError(
            "final manifest validation returned another canonical identity")
    _verify_clean_git_identity(current_manifest, prepared.project_root)

    current_authorization = _load_authenticated_owner_authorization(
        prepared.authorization_path)
    signature_path = prepared.authorization_path.with_name(
        f"{prepared.authorization_path.name}.sig")
    if (
        _raw_sha256(prepared.authorization_path) != prepared.authorization_raw_sha256
        or _raw_sha256(signature_path)
        != prepared.authorization_signature_raw_sha256
        or canonical_sha256(current_authorization)
        != canonical_sha256(prepared.authorization)
    ):
        raise Phase3MainLiveError(
            "main authorization or detached signature changed before completion")
    try:
        finalization_time = phase3_main_manifest._utc(  # noqa: SLF001
            finalization["recorded_at_utc"], "finalization.recorded_at_utc")
        approved_at = phase3_main_manifest._utc(  # noqa: SLF001
            current_authorization["approved_at_utc"],
            "authorization.approved_at_utc",
        )
        if finalization_time < approved_at:
            raise ValueError("finalization predates the signed authorization")
        phase3_main_manifest.validate_main_authorization(
            current_authorization,
            current_manifest,
            as_of=approved_at,
        )
    except (KeyError, TypeError, ValueError, phase3_main_manifest.MainManifestError) as exc:
        raise Phase3MainLiveError(
            f"main authorization failed final completion validation: {exc}") from exc
    return current_authorization


def _validate_analysis_result_snapshot(
    prepared: PreparedMainRun,
    *,
    expected_finalization_raw_sha256: str,
    expected_results_raw_sha256: str,
    expected_analysis_raw: bytes,
) -> tuple[bytes, str]:
    """Validate one stable analysis output snapshot and return its reusable digest."""
    path = prepared.identity.paths.analysis_results
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Phase3MainLiveError(
            f"could not read main analysis result: {path}") from exc
    if raw != expected_analysis_raw:
        raise Phase3MainLiveError(
            "main analysis result differs from the trusted in-process output")
    value = _parse_strict_json(raw, path)
    if not isinstance(value, dict) or set(value) != ANALYSIS_RESULT_FIELDS:
        raise Phase3MainLiveError("main analysis result fields drifted")
    if value.get("schema_version") != ANALYSIS_RESULT_SCHEMA:
        raise Phase3MainLiveError("main analysis result schema drifted")
    integrity = value.get("integrity")
    if not isinstance(integrity, dict) or set(integrity) != ANALYSIS_INTEGRITY_FIELDS:
        raise Phase3MainLiveError("main analysis integrity fields drifted")
    bootstrap = value.get("bootstrap")
    if (
        not isinstance(bootstrap, Mapping)
        or set(bootstrap) != {"B", "seed", "prng", "draw_matrix_sha256", "strata"}
    ):
        raise Phase3MainLiveError("main analysis bootstrap evidence is not an object")
    list_sections = {"invalid_counts_by_budget_judge_replicate_block"}
    for name in ANALYSIS_RESULT_FIELDS - {
        "schema_version", "bootstrap", "integrity", *list_sections,
    }:
        if not isinstance(value[name], Mapping):
            raise Phase3MainLiveError(
                f"main analysis section {name!r} is not an object")
    invalid_strata = value["invalid_counts_by_budget_judge_replicate_block"]
    invalid_stratum_fields = {
        "condition",
        "query_budget",
        "judge",
        "replicate_block_zero_based",
        "side_zero_based",
        "within_side_replicate_zero_based",
        "planned_rows",
        "context_ineligible_rows",
        "invalid_count",
        "terminal_invalid_count",
    }
    if not isinstance(invalid_strata, list) or any(
        not isinstance(row, Mapping) or set(row) != invalid_stratum_fields
        for row in invalid_strata
    ):
        raise Phase3MainLiveError(
            "main analysis invalid-count strata are malformed")

    pins = _load_bound_input_object(
        prepared.manifest,
        prepared.input_paths,
        "analysis_pins",
        "analysis pins",
    )
    try:
        phase3_main_analysis.validate_analysis_pins(
            pins, prepared.protocol, root=prepared.project_root)
        question_snapshot = phase3_main_analysis._snapshot_protocol_bound_question_bank(  # noqa: SLF001
            prepared.protocol, prepared.project_root)
        expected_integrity = {
            "repository_head_at_analysis": str(prepared.manifest["source_commit"]),
            "engine_git_commit": str(prepared.manifest["source_commit"]),
            "engine_git_state": "clean_tracked_at_head",
            "engine_git_status_porcelain": None,
            "engine_raw_sha256": _raw_sha256(Path(phase3_main_analysis.__file__).resolve()),
            "python_version": str(prepared.manifest["toolchain"]["python_version"]),
            "protocol_canonical_sha256": canonical_sha256(prepared.protocol),
            "protocol_question_bank_bundle_sha256": prepared.protocol[
                "planning_cell_identity"]["question_bank_bundle_sha256"],
            "protocol_phase2_question_source_canonical_sha256": prepared.protocol[
                "source_bindings"]["canonical_json_sha256"][
                    "rejudge/phase2_protocol.json"],
            "main_question_ids_canonical_sha256": canonical_sha256(
                list(question_snapshot.main_ids)),
            "main_question_rows_canonical_sha256": canonical_sha256({
                question_id: question_snapshot.bank[question_id]
                for question_id in sorted(question_snapshot.main_ids)
            }),
            "pins_raw_sha256": _raw_sha256(prepared.input_paths["analysis_pins"]),
            "results_raw_sha256": expected_results_raw_sha256,
            "finalization_raw_sha256": expected_finalization_raw_sha256,
            "terminal_cell_keys_raw_sha256": None,
            "context_ineligible_cell_keys_raw_sha256": None,
            "record_count": len(prepared.inventory.judgment_cells),
        }
        expected_bootstrap = {
            "B": int(pins["bootstrap"]["B"]),
            "seed": int(pins["bootstrap"]["seed"]),
            "draw_matrix_sha256": str(
                pins["bootstrap"]["precomputed_full_draw_matrix_sha256"]),
            "prng": "CPython random.Random MT19937",
        }
        expected_strata: dict[str, list[str]] = {}
        for question_id in question_snapshot.main_ids:
            world = question_snapshot.bank[question_id]["world"]
            if not isinstance(world, str) or not world:
                raise ValueError(
                    f"main question {question_id!r} has no non-empty world")
            expected_strata.setdefault(world, []).append(question_id)
        expected_strata = {
            world: sorted(question_ids)
            for world, question_ids in sorted(expected_strata.items())
        }
    except (
        KeyError,
        TypeError,
        ValueError,
        phase3_main_analysis.AnalysisError,
    ) as exc:
        raise Phase3MainLiveError(
            f"could not derive expected main analysis integrity: {exc}") from exc
    if integrity != expected_integrity:
        raise Phase3MainLiveError(
            "main analysis integrity does not bind the current formal inputs")
    for name, expected in expected_bootstrap.items():
        if bootstrap.get(name) != expected:
            raise Phase3MainLiveError(
                f"main analysis bootstrap field {name!r} drifted")
    if bootstrap.get("strata") != expected_strata:
        raise Phase3MainLiveError("main analysis bootstrap strata drifted")
    try:
        primary_ids = frozenset(phase3_main_analysis.PRIMARY_IDS)
        estimand_ids = primary_ids | {"S1"}
        judges = frozenset(prepared.protocol["roster"]["judges_final"])
        conditions = frozenset(phase3_main_analysis.ALL_CONDITIONS)
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase3MainLiveError(
            f"could not derive the main analysis scientific envelope: {exc}") from exc
    exact_keys = {
        "domains": estimand_ids,
        "primary": primary_ids,
        "valid_only_sensitivity": estimand_ids,
        "strict_support": estimand_ids,
        "per_judge_descriptive": judges,
        "claims": frozenset({"pooled", "per_judge", "prohibited"}),
    }
    for section, expected_keys in exact_keys.items():
        if set(value[section]) != expected_keys:
            raise Phase3MainLiveError(
                f"main analysis section {section!r} has the wrong identity set")
    for estimand in estimand_ids:
        if (
            not isinstance(value["domains"][estimand], list)
            or not isinstance(value["valid_only_sensitivity"][estimand], Mapping)
            or not isinstance(value["strict_support"][estimand], Mapping)
        ):
            raise Phase3MainLiveError(
                f"main analysis estimand {estimand!r} has a malformed result")
    for estimand in primary_ids:
        if not isinstance(value["primary"][estimand], Mapping):
            raise Phase3MainLiveError(
                f"main analysis primary estimand {estimand!r} is malformed")
    if not isinstance(value["S1"], Mapping):  # pragma: no cover - guarded above
        raise Phase3MainLiveError("main analysis S1 result is malformed")
    for judge in judges:
        judge_results = value["per_judge_descriptive"][judge]
        if not isinstance(judge_results, Mapping) or set(judge_results) != estimand_ids:
            raise Phase3MainLiveError(
                f"main analysis judge result {judge!r} has the wrong estimand set")
        if any(not isinstance(entry, Mapping) for entry in judge_results.values()):
            raise Phase3MainLiveError(
                f"main analysis judge result {judge!r} is malformed")
    scenario_keys = frozenset({
        "role", "common_draw_matrix_sha256",
        "all_invalid_correct", "all_invalid_wrong",
    })
    scenarios = value["all_invalid_scenarios"]
    if set(scenarios) != scenario_keys:
        raise Phase3MainLiveError("main analysis invalid scenarios have the wrong fields")
    for scenario in ("all_invalid_correct", "all_invalid_wrong"):
        entries = scenarios[scenario]
        if not isinstance(entries, Mapping) or set(entries) != estimand_ids:
            raise Phase3MainLiveError(
                f"main analysis invalid scenario {scenario!r} has the wrong estimand set")
        if any(not isinstance(entry, Mapping) for entry in entries.values()):
            raise Phase3MainLiveError(
                f"main analysis invalid scenario {scenario!r} is malformed")
    expected_count_keys = frozenset(
        f"{judge}|{condition}" for judge in judges for condition in conditions)
    for section in (
        "invalid_counts_by_judge_condition",
        "terminal_invalid_counts_by_judge_condition",
    ):
        counts = value[section]
        if set(counts) != expected_count_keys or any(
            type(count) is not int or count < 0 for count in counts.values()
        ):
            raise Phase3MainLiveError(
                f"main analysis section {section!r} has malformed counts")
    for source, expected_raw, label in question_snapshot.stable_inputs:
        try:
            if source.read_bytes() != expected_raw:
                raise Phase3MainLiveError(
                    f"{label} changed while analysis output was validated")
        except OSError as exc:
            raise Phase3MainLiveError(
                f"could not recheck {label} during analysis validation") from exc
    try:
        if path.read_bytes() != raw:
            raise Phase3MainLiveError(
                "main analysis result changed while it was validated")
    except OSError as exc:
        raise Phase3MainLiveError(
            "main analysis result became unreadable while it was validated") from exc
    return raw, hashlib.sha256(raw).hexdigest()


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
    _reopen_capacity_execution_provenance_inputs(
        prepared.manifest, prepared.input_paths
    )
    validation = _validate_capacity(
        plan=plan,
        result=result,
        history_path=prepared.input_paths["capacity_dispatch_history"],
        as_of=datetime.now(timezone.utc),
        require_current_freshness=False,
        plan_path=prepared.input_paths["capacity_plan"],
        result_path=prepared.input_paths["capacity_result"],
        execution_manifest_path=prepared.input_paths[
            "capacity_execution_manifest"
        ],
        execution_authorization_path=prepared.input_paths[
            "capacity_execution_authorization"
        ],
        execution_authorization_signature_path=prepared.input_paths[
            "capacity_execution_authorization_signature"
        ],
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
    _reopen_capacity_execution_provenance_inputs(
        prepared.manifest, prepared.input_paths
    )
    _validate_capacity(
        plan=plan,
        result=result,
        history_path=prepared.input_paths["capacity_dispatch_history"],
        as_of=now,
        plan_path=prepared.input_paths["capacity_plan"],
        result_path=prepared.input_paths["capacity_result"],
        execution_manifest_path=prepared.input_paths[
            "capacity_execution_manifest"
        ],
        execution_authorization_path=prepared.input_paths[
            "capacity_execution_authorization"
        ],
        execution_authorization_signature_path=prepared.input_paths[
            "capacity_execution_authorization_signature"
        ],
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
    _validate_environmental_restart(
        manifest_validation=prepared.manifest_validation,
        billing_record=billing_record,
        billing_validation=billing_validation,
        project_root=prepared.project_root,
    )
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


def _normalized_predecessor_segment(value: Any, label: str) -> Decimal:
    """Recover the frozen eight-decimal ledger-cost representation."""
    amount = _decimal_number(value, label)
    with localcontext() as context:
        context.prec = max(100, len(amount.as_tuple().digits) + 10)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return amount.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _require_clean_pre_main_billing(
    validation: Mapping[str, Any], manifest: Mapping[str, Any],
) -> None:
    """Require a closed exact or conservative prior-spend upper bound."""
    if validation.get("run_id") != manifest.get("run_id"):
        raise Phase3MainLiveError(
            "pre-main billing reconciliation run ID differs from the manifest")
    billing_scope = validation.get("billing_scope")
    settlement = validation.get("provider_settlement")
    settlement_fields = {
        "status", "account_identity_sha256", "finalized_through_utc",
    }
    settlement_status_by_kind = {
        phase3_main_billing_reconciliation.AUTHENTICATED_EVIDENCE_KIND:
            phase3_main_billing_reconciliation.PROVIDER_SETTLEMENT_STATUS,
        phase3_main_billing_reconciliation.CONSOLE_EVIDENCE_KIND:
            phase3_main_billing_reconciliation.CONSOLE_SETTLEMENT_STATUS,
    }
    evidence_kind = validation.get("evidence_kind")
    if (
        evidence_kind not in settlement_status_by_kind
        or not isinstance(billing_scope, Mapping)
        or not isinstance(settlement, Mapping)
        or set(settlement) != settlement_fields
        or settlement.get("status")
        != settlement_status_by_kind.get(evidence_kind)
        or settlement.get("account_identity_sha256")
        != billing_scope.get("account_identity_sha256")
        or billing_scope.get("account_identity_sha256")
        != manifest.get("runtime", {}).get("provider_account_identity_sha256")
    ):
        raise Phase3MainLiveError(
            "pre-main billing reconciliation is not provider-verified and finalized")
    disposition = validation.get("disposition")
    if (
        disposition not in phase3_main_billing_reconciliation.CLOSED_DISPOSITIONS
        or validation.get("closed") is not True
        or (
            disposition == phase3_main_billing_reconciliation.DISPOSITION_CLOSED_CONSERVATIVE
            and validation.get("within_conservative_envelope") is not True
        )
        or _decimal_number(
            validation.get("provider_delta_usd"), "provider predecessor spend")
        > _decimal_number(
            validation.get("prior_spend_upper_bound_usd"),
            "predecessor spend upper bound")
        or _decimal_number(
            validation.get("prior_spend_upper_bound_usd"),
            "predecessor spend upper bound")
        != _decimal_number(
            manifest["spend"]["prior_reconciled_usd"], "manifest predecessor spend")
    ):
        raise Phase3MainLiveError(
            "pre-main billing reconciliation is not closed at the manifest upper bound")


def _validate_environmental_restart(
    *,
    manifest_validation: Mapping[str, Any],
    billing_record: Mapping[str, Any],
    billing_validation: Mapping[str, Any],
    project_root: Path,
) -> None:
    """Bind a successor to one real void record and its reconciled predecessor ledger."""
    restart = manifest_validation.get("restart")
    if not isinstance(restart, Mapping):
        raise Phase3MainLiveError("main manifest restart validation is missing")
    if restart.get("mode") == phase3_main_manifest.RESTART_MODE_INITIAL:
        if restart.get("predecessor") is not None:
            raise Phase3MainLiveError("initial main identity unexpectedly has a predecessor")
        return
    if restart.get("mode") != phase3_main_manifest.RESTART_MODE_ENVIRONMENTAL_SUCCESSOR:
        raise Phase3MainLiveError("main manifest restart mode is unsupported")
    predecessor = restart.get("predecessor")
    if not isinstance(predecessor, Mapping):
        raise Phase3MainLiveError("environmental successor predecessor binding is missing")

    void_path = Path(predecessor["void_record_path"])
    if _raw_sha256(void_path) != predecessor["void_record_raw_sha256"]:
        raise Phase3MainLiveError("environmental predecessor void record bytes drifted")
    void = _load_strict_object(void_path, "environmental predecessor void record")
    if set(void) != IDENTITY_VOID_FIELDS:
        raise Phase3MainLiveError("environmental predecessor void record fields drifted")
    predecessor_run_id = str(predecessor["run_id"])
    predecessor_manifest_sha = str(predecessor["manifest_canonical_sha256"])
    predecessor_identity_sha = str(predecessor["manifest_identity_sha256"])
    predecessor_root = Path(predecessor["artifact_root"])
    usage_ledger_path = Path(predecessor["usage_ledger_path"]).resolve()
    usage_ledger_state_path = api_client.usage_ledger_state_path(
        usage_ledger_path).resolve()
    expected_stem = f"{predecessor_run_id}-{predecessor_manifest_sha}"
    start_path = Path(str(void.get("start_record_path", ""))).resolve()
    expected_start_path = void_path.with_name(f"{expected_stem}.started.json").resolve()
    if (
        void_path.name != f"{expected_stem}.voided.json"
        or start_path != expected_start_path
        or void.get("schema_version") != IDENTITY_VOID_SCHEMA
        or void.get("status") != "voided_environmental_interruption"
        or void.get("run_id") != predecessor_run_id
        or void.get("manifest_canonical_sha256") != predecessor_manifest_sha
        or void.get("manifest_identity_sha256") != predecessor_identity_sha
        or void.get("artifact_root") != predecessor_root.as_posix()
        or void.get("usage_ledger_path") != usage_ledger_path.as_posix()
        or void.get("usage_ledger_raw_sha256") != predecessor["usage_ledger_raw_sha256"]
        or void.get("usage_ledger_state_path") != usage_ledger_state_path.as_posix()
        or void.get("reason_code") not in ENVIRONMENTAL_INTERRUPTION_REASONS
    ):
        raise Phase3MainLiveError("environmental predecessor void record binding drifted")
    try:
        expected_start_sha = str(void["start_record_raw_sha256"])
        if (
            len(expected_start_sha) != 64
            or any(character not in "0123456789abcdef" for character in expected_start_sha)
            or _raw_sha256(start_path) != expected_start_sha
        ):
            raise Phase3MainLiveError(
                "environmental predecessor start record binding drifted")
        start = _load_strict_object(start_path, "environmental predecessor start record")
    except OSError as exc:
        raise Phase3MainLiveError(
            "environmental predecessor start record is unavailable") from exc
    if set(start) != IDENTITY_START_FIELDS or (
        start.get("schema_version") != IDENTITY_START_SCHEMA
        or start.get("status") != "started_single_shot"
        or start.get("run_id") != predecessor_run_id
        or start.get("manifest_canonical_sha256") != predecessor_manifest_sha
        or start.get("manifest_identity_sha256") != predecessor_identity_sha
        or start.get("artifact_root") != predecessor_root.as_posix()
        or start.get("authorization_id") != predecessor["authorization_id"]
        or start.get("authorization_canonical_sha256")
        != predecessor["authorization_canonical_sha256"]
        or start.get("authorization_raw_sha256")
        != predecessor["authorization_raw_sha256"]
        or start.get("authorization_signature_raw_sha256")
        != predecessor["authorization_signature_raw_sha256"]
    ):
        raise Phase3MainLiveError("environmental predecessor start record semantics drifted")
    _, ledger_raw, ledger_state_raw = _load_stable_start_bound_ledger(
        start, expected_ledger_path=usage_ledger_path)
    if (
        hashlib.sha256(ledger_raw).hexdigest()
        != predecessor["usage_ledger_raw_sha256"]
        or void.get("usage_ledger_state_raw_sha256")
        != hashlib.sha256(ledger_state_raw).hexdigest()
        or void.get("usage_ledger_identity_canonical_sha256")
        != start.get("usage_ledger_identity_canonical_sha256")
        or void.get("usage_ledger_genesis_event_hash")
        != start.get("usage_ledger_genesis_event_hash")
    ):
        raise Phase3MainLiveError(
            "environmental predecessor usage-ledger provenance drifted")
    start_time = phase3_main_manifest._utc(  # noqa: SLF001
        start.get("recorded_at_utc"), "environmental predecessor start recorded_at_utc")
    void_time = phase3_main_manifest._utc(  # noqa: SLF001
        void.get("recorded_at_utc"), "environmental predecessor void recorded_at_utc")
    successor_time = manifest_validation.get("recorded_at_utc")
    if not isinstance(successor_time, datetime):
        raise Phase3MainLiveError("successor manifest recorded_at validation is missing")
    if not start_time <= void_time <= successor_time:
        raise Phase3MainLiveError(
            "environmental predecessor and successor timestamps are out of order")
    billing_scope = billing_validation.get("billing_scope")
    if not isinstance(billing_scope, Mapping):
        raise Phase3MainLiveError(
            "environmental successor billing validation omits its scope")
    billing_window_start = phase3_main_manifest._utc(  # noqa: SLF001
        billing_scope.get("window_start_utc"),
        "environmental successor billing window_start_utc",
    )
    billing_window_end = phase3_main_manifest._utc(  # noqa: SLF001
        billing_scope.get("window_end_utc"),
        "environmental successor billing window_end_utc",
    )
    if billing_window_start > start_time or billing_window_end < void_time:
        raise Phase3MainLiveError(
            "environmental successor billing window does not cover the predecessor lifetime")
    settlement = billing_validation.get("provider_settlement")
    settlement_fields = {
        "status",
        "account_identity_sha256",
        "finalized_through_utc",
    }
    if not isinstance(settlement, Mapping) or set(settlement) != settlement_fields:
        raise Phase3MainLiveError(
            "environmental successor lacks provider-authenticated settlement finality")
    if settlement.get("status") != "provider_authenticated_finalized":
        raise Phase3MainLiveError(
            "environmental successor provider settlement is not finalized")
    settlement_account = phase3_main_manifest._sha256(  # noqa: SLF001
        settlement.get("account_identity_sha256"),
        "environmental successor settlement account_identity_sha256",
    )
    billing_account = phase3_main_manifest._sha256(  # noqa: SLF001
        billing_scope.get("account_identity_sha256"),
        "environmental successor billing account_identity_sha256",
    )
    finalized_through = phase3_main_manifest._utc(  # noqa: SLF001
        settlement.get("finalized_through_utc"),
        "environmental successor settlement finalized_through_utc",
    )
    if (
        settlement_account != billing_account
        or finalized_through <= void_time
        or billing_window_end <= finalized_through
    ):
        raise Phase3MainLiveError(
            "environmental successor settlement does not cover the predecessor account "
            "through a post-void billing window")

    matched_ledgers = []
    raw_ledgers = billing_record.get("ledgers")
    if not isinstance(raw_ledgers, list):
        raise Phase3MainLiveError("billing reconciliation omits its ledger rows")
    for row in raw_ledgers:
        if not isinstance(row, Mapping):
            continue
        candidate = Path(str(row.get("path", "")))
        if not candidate.is_absolute():
            candidate = project_root / candidate
        if (
            candidate.resolve() == usage_ledger_path
            and row.get("raw_sha256") == predecessor["usage_ledger_raw_sha256"]
        ):
            matched_ledgers.append(row)
    if len(matched_ledgers) != 1:
        raise Phase3MainLiveError(
            "billing reconciliation does not cover the exact environmental predecessor ledger")


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
            _normalized_predecessor_segment(
                row["actual_spend_usd"], "reconciled segment actual spend"),
            _normalized_predecessor_segment(
                row["uncertain_spend_usd"], "reconciled segment uncertain spend"),
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
            _normalized_predecessor_segment(
                segment.get("actual_spend_usd"), "segment actual spend"),
            _normalized_predecessor_segment(
                segment.get("uncertain_spend_usd"), "segment uncertain spend"),
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
    reviewer_failure_policy = _load_bound_input_object(
        manifest,
        input_paths,
        "reviewer_failure_policy",
        "reviewer failure policy",
    )
    _validate_reviewer_prompt(reviewer_prompt, reviewer_failure_policy)
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
    _reopen_capacity_execution_provenance_inputs(manifest, input_paths)
    capacity_validation = _validate_capacity(
        plan=capacity_plan, result=capacity_result,
        history_path=input_paths["capacity_dispatch_history"], as_of=now,
        plan_path=input_paths["capacity_plan"],
        result_path=input_paths["capacity_result"],
        execution_manifest_path=input_paths["capacity_execution_manifest"],
        execution_authorization_path=input_paths[
            "capacity_execution_authorization"
        ],
        execution_authorization_signature_path=input_paths[
            "capacity_execution_authorization_signature"
        ])
    capacity_runtime = _validate_capacity_runtime_binding(
        plan=capacity_plan, result=capacity_result, runtime=manifest["runtime"])
    capacity_validation = {**capacity_validation, "runtime": capacity_runtime}
    try:
        price_change_policy_validation = (
            phase3_main_runtime_policies.load_and_validate_price_change_policy(
                input_paths["price_change_policy"]
            )
        )
        reviewer_usage_policy_validation = (
            phase3_main_runtime_policies.load_and_validate_reviewer_usage_policy(
                input_paths["reviewer_usage_policy"],
                capacity_plan=capacity_plan,
            )
        )
    except phase3_main_runtime_policies.MainRuntimePolicyError as exc:
        raise Phase3MainLiveError(f"main runtime policy validation failed: {exc}") from exc

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
    _validate_environmental_restart(
        manifest_validation=manifest_validation,
        billing_record=billing_record,
        billing_validation=billing_validation,
        project_root=root,
    )

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
        price_change_policy_validation=price_change_policy_validation,
        reviewer_usage_policy_validation=reviewer_usage_policy_validation,
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
    prepared: PreparedMainRun,
    snapshot: api_client.UsageLedgerSnapshot,
    *,
    sdk_client: Any,
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
        _sdk_client=sdk_client,
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
    authorized = _AuthorizationDeadlineClient(prepared, raw)
    return RoleLimitResolvingClient(
        authorized, prepared.role_limits["model_role_limits"])


def _construct_verified_runtime_provider_sdk(prepared: PreparedMainRun) -> Any:
    """Bind one environment snapshot to the approved account and inference transport."""
    expected_account = prepared.manifest["runtime"][
        "provider_account_identity_sha256"]
    transport = prepared.role_limits["request_settings"]["transport"]
    api_key = os.environ.get("TOGETHER_API_KEY")
    if api_key is None:
        raise Phase3MainLiveError(
            "TOGETHER_API_KEY is missing; runtime account identity cannot be verified")
    try:
        phase3_main_together_billing_capture.verify_runtime_account_identity(
            api_key=api_key,
            expected_account_identity_sha256=expected_account,
        )
        return api_client.build_pinned_together_client(
            api_key=api_key,
            base_url=api_client.PINNED_TOGETHER_INFERENCE_BASE_URL,
            follow_redirects=False,
            http_timeout=dict(transport["http_timeout"]),
            sdk_internal_max_retries=int(transport["sdk_internal_max_retries"]),
        )
    except phase3_main_together_billing_capture.TogetherBillingCaptureError as exc:
        raise Phase3MainLiveError(
            f"runtime Together account verification failed: {exc}") from exc
    except (TypeError, ValueError, RuntimeError) as exc:
        raise Phase3MainLiveError(
            "could not construct the identity-bound Together inference client") from exc


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


def _identity_void_path(identity: phase3_main_runner.MainRunIdentity) -> Path:
    registry = identity.identity_registry_root
    if registry is None:
        raise Phase3MainLiveError("main identity has no persistent registry root")
    return (
        registry / "identities"
        / f"{identity.run_id}-{identity.manifest_sha256}.voided.json"
    )


def _assert_identity_not_started(identity: phase3_main_runner.MainRunIdentity) -> None:
    if (
        _identity_start_path(identity).exists()
        or _identity_void_path(identity).exists()
        or _identity_complete_path(identity).exists()
    ):
        raise Phase3MainLiveError(
            "formal identity already has persistent single-shot state")


def _require_identity_not_voided(identity: phase3_main_runner.MainRunIdentity) -> None:
    if _identity_void_path(identity).exists():
        raise Phase3MainLiveError("a voided formal identity cannot be completed")


def _write_exclusive_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    label: str,
) -> None:
    encoded = (json.dumps(dict(value), sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8")
    _publish_exclusive_bytes(path, encoded, label=label)


def _exclusive_publish_temp_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.publish.tmp")


def _directory_publish_temp_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.mkdir-{uuid.uuid4().hex}.tmp")


def _publish_missing_directory(directory: Path) -> None:
    """Publish one previously absent directory with platform durability semantics."""
    if os.name != "nt":
        try:
            directory.mkdir()
        except FileExistsError as exc:
            raise Phase3MainLiveError(
                f"durable directory path appeared concurrently: {directory}") from exc
        phase3_main_runner._fsync_directory(directory)  # noqa: SLF001
        phase3_main_runner._fsync_directory(directory.parent)  # noqa: SLF001
        return

    stage = _directory_publish_temp_path(directory)
    stage_owned = False
    try:
        try:
            stage.mkdir()
            stage_owned = True
        except FileExistsError as exc:
            raise Phase3MainLiveError(
                f"durable directory publish stage already exists: {stage}") from exc
        try:
            phase3_main_runner._windows_move_no_replace_write_through(  # noqa: SLF001
                stage, directory)
        except FileExistsError as exc:
            raise Phase3MainLiveError(
                f"durable directory path appeared concurrently: {directory}") from exc
        except OSError as exc:
            raise Phase3MainLiveError(
                f"could not durably publish directory: {directory}") from exc
        if directory.is_symlink() or not directory.is_dir():
            raise Phase3MainLiveError(
                f"durably published directory has the wrong type: {directory}")
    finally:
        if stage_owned:
            try:
                stage.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise Phase3MainLiveError(
                    f"could not clean durable directory publish stage: {stage}") from exc


def _durably_create_directory_tree(path: Path) -> None:
    """Create each missing directory and durably anchor every new parent entry."""
    target = Path(path)
    missing: list[Path] = []
    cursor = target
    while not cursor.exists():
        if cursor.parent == cursor:
            raise Phase3MainLiveError(
                f"no existing ancestor for durable directory tree: {target}")
        missing.append(cursor)
        cursor = cursor.parent
    if cursor.is_symlink() or not cursor.is_dir():
        raise Phase3MainLiveError(
            f"durable directory tree has a linked or non-directory ancestor: {cursor}")
    try:
        phase3_main_runner._fsync_directory(cursor)  # noqa: SLF001
        for directory in reversed(missing):
            _publish_missing_directory(directory)
    except OSError as exc:
        raise Phase3MainLiveError(
            f"could not durably establish directory tree: {target}") from exc


def _registry_bootstrap_lease_path(registry_target: Path) -> Path:
    canonical = str(Path(registry_target).resolve())
    if os.name == "nt":
        canonical = canonical.casefold()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()).resolve() / (
        f"phase3-main-registry-bootstrap-{digest}.lock")


def _durably_prepare_identity_registry(registry_root: Path) -> None:
    target = Path(registry_root) / "identities"
    with phase3_v3_live.RunLease(_registry_bootstrap_lease_path(target)):
        _durably_create_directory_tree(target)


def _cleanup_exclusive_publish_temp(path: Path, *, label: str) -> None:
    temp = _exclusive_publish_temp_path(path)
    if temp.is_symlink():
        raise Phase3MainLiveError(f"{label} publish temp is not a regular file")
    if not temp.exists():
        return
    if not temp.is_file():
        raise Phase3MainLiveError(f"{label} publish temp is not a regular file")
    try:
        temp.unlink()
        phase3_main_runner._fsync_parent_directory(path)  # noqa: SLF001
    except OSError as exc:
        raise Phase3MainLiveError(f"could not clean {label} publish temp") from exc


def _publish_staged_no_replace(temp: Path, path: Path) -> None:
    if os.name == "nt":
        phase3_main_runner._windows_move_no_replace_write_through(  # noqa: SLF001
            temp, path)
        return
    os.link(temp, path)


def _publish_exclusive_bytes(path: Path, raw: bytes, *, label: str) -> None:
    """Publish complete bytes atomically without replacing an existing record."""
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise Phase3MainLiveError(
            f"{label} parent directory was not durably prepared: {path.parent}")
    temp = _exclusive_publish_temp_path(path)
    if path.exists() or path.is_symlink():
        if temp.is_symlink() or (temp.exists() and not temp.is_file()):
            raise Phase3MainLiveError(
                f"{label} publish temp conflicts with the existing record")
        if temp.exists():
            try:
                same_file = os.path.samefile(temp, path)
            except OSError as exc:
                raise Phase3MainLiveError(
                    f"could not compare {label} with its publish temp") from exc
            if not same_file:
                raise Phase3MainLiveError(
                    f"{label} publish temp is a different file from the existing record")
            try:
                published = path.read_bytes()
                if temp.read_bytes() != published:
                    raise Phase3MainLiveError(
                        f"{label} linked publish aliases have different bytes")
                temp.unlink()
                phase3_main_runner._fsync_parent_directory(path)  # noqa: SLF001
                if path.read_bytes() != published:
                    raise Phase3MainLiveError(
                        f"{label} changed while its linked publish alias was cleaned")
            except OSError as exc:
                raise Phase3MainLiveError(
                    f"could not clean {label} linked publish alias") from exc
        raise Phase3MainLiveError(f"{label} already exists: {path}")
    _cleanup_exclusive_publish_temp(path, label=label)
    temp_owned = False
    try:
        try:
            with temp.open("xb") as handle:
                temp_owned = True
                written = handle.write(raw)
                if written != len(raw):
                    raise Phase3MainLiveError(
                        f"could not completely stage {label} for publication")
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise Phase3MainLiveError(
                f"{label} publish temp appeared during staging") from exc
        try:
            _publish_staged_no_replace(temp, path)
        except FileExistsError as exc:
            raise Phase3MainLiveError(f"{label} already exists: {path}") from exc
        except OSError as exc:
            raise Phase3MainLiveError(
                f"could not atomically publish {label}") from exc
        try:
            phase3_main_runner._fsync_parent_directory(path)  # noqa: SLF001
            published = path.read_bytes()
        except OSError as exc:
            raise Phase3MainLiveError(
                f"could not durably reopen published {label}") from exc
        if published != raw:
            raise Phase3MainLiveError(f"published {label} bytes drifted")
    finally:
        if temp_owned:
            try:
                temp.unlink()
                phase3_main_runner._fsync_parent_directory(path)  # noqa: SLF001
            except FileNotFoundError:
                pass


def _write_exclusive_bytes(path: Path, raw: bytes, *, label: str) -> None:
    _publish_exclusive_bytes(path, raw, label=label)


def _new_artifact_publication_probe_path(artifact_root: Path) -> Path:
    return artifact_root / f".phase3-main-publication-probe-{uuid.uuid4().hex}"


def _probe_artifact_publication(artifact_root: Path) -> None:
    """Exercise artifact-volume publication before the persistent formal start."""
    target = _new_artifact_publication_probe_path(artifact_root)
    temp = _exclusive_publish_temp_path(target)
    for candidate in (target, temp):
        if candidate.is_symlink() or candidate.exists():
            raise Phase3MainLiveError(
                f"artifact publication probe path already exists: {candidate}")
    raw = b"phase3-main-exclusive-publication-probe-v1\n"
    published_by_this_invocation = False
    try:
        _publish_exclusive_bytes(
            target, raw, label="artifact-volume publication probe")
        published_by_this_invocation = True
        if target.read_bytes() != raw:
            raise Phase3MainLiveError("artifact publication probe bytes drifted")
    finally:
        if published_by_this_invocation and target.exists():
            try:
                target.unlink()
                phase3_main_runner._fsync_parent_directory(target)  # noqa: SLF001
            except OSError as exc:
                raise Phase3MainLiveError(
                    "could not clean the artifact publication probe") from exc


def _fresh_ledger_start_binding(
    prepared: PreparedMainRun,
    snapshot: api_client.UsageLedgerSnapshot,
) -> dict[str, Any]:
    """Reopen and bind the exact genesis-only ledger immediately before start."""
    ledger_path = prepared.identity.paths.usage_ledger.resolve()
    state_path = api_client.usage_ledger_state_path(ledger_path).resolve()
    expected_summary = {
        "events": 0,
        "actual_spend_usd": 0.0,
        "uncertain_spend_usd": 0.0,
        "accounted_spend_usd": 0.0,
        "unmatched_reservations": 0,
    }
    if (
        snapshot.path.resolve() != ledger_path
        or snapshot.state_path.resolve() != state_path
        or snapshot.last_sequence != 0
        or snapshot.summary != expected_summary
    ):
        raise Phase3MainLiveError(
            "formal identity start requires the exact fresh zero-spend usage ledger")
    try:
        verified = api_client.load_chained_usage_ledger(
            ledger_path, expected_identity=snapshot.identity)
        ledger_raw = ledger_path.read_bytes()
        state_raw = state_path.read_bytes()
        events = api_client._read_usage_events(ledger_path)  # noqa: SLF001
        verified_again = api_client.load_chained_usage_ledger(
            ledger_path, expected_identity=snapshot.identity)
        if ledger_path.read_bytes() != ledger_raw or state_path.read_bytes() != state_raw:
            raise Phase3MainLiveError(
                "fresh usage ledger changed while its start binding was recorded")
    except (OSError, api_client.UsageLedgerError) as exc:
        raise Phase3MainLiveError(
            "fresh usage ledger could not be bound to the formal identity start") from exc
    if (
        verified.last_sequence != 0
        or verified.summary != expected_summary
        or verified_again.last_event_hash != verified.last_event_hash
        or len(events) != 1
        or ledger_raw.count(b"\n") != 1
        or not ledger_raw.endswith(b"\n")
    ):
        raise Phase3MainLiveError(
            "formal identity usage ledger is not a stable genesis-only ledger")
    ledger_identity = dict(verified.identity)
    if (
        set(ledger_identity) != {
            "schema_version", "ledger_id", "ledger_path", "state_path"
        }
        or ledger_identity.get("schema_version") != api_client.USAGE_LEDGER_SCHEMA_VERSION
        or not isinstance(ledger_identity.get("ledger_id"), str)
        or not ledger_identity["ledger_id"]
        or ledger_identity.get("ledger_path") != ledger_path.as_posix()
        or ledger_identity.get("state_path") != state_path.as_posix()
        or events[0].get("event_hash") != verified.last_event_hash
    ):
        raise Phase3MainLiveError(
            "fresh usage ledger identity or genesis event is invalid")
    return {
        "usage_ledger_schema_version": ledger_identity["schema_version"],
        "usage_ledger_id": ledger_identity["ledger_id"],
        "usage_ledger_path": ledger_path.as_posix(),
        "usage_ledger_state_path": state_path.as_posix(),
        "usage_ledger_identity_canonical_sha256": canonical_sha256(ledger_identity),
        "usage_ledger_genesis_event_hash": verified.last_event_hash,
        "usage_ledger_genesis_raw_sha256": hashlib.sha256(ledger_raw).hexdigest(),
        "usage_ledger_genesis_state_raw_sha256": hashlib.sha256(state_raw).hexdigest(),
    }


def _start_bound_ledger_identity(
    start: Mapping[str, Any], *, expected_ledger_path: Path,
) -> dict[str, object]:
    """Recover the exact ledger identity durably anchored in a start record."""
    ledger_path = expected_ledger_path.resolve()
    state_path = api_client.usage_ledger_state_path(ledger_path).resolve()
    ledger_id = start.get("usage_ledger_id")
    if (
        start.get("usage_ledger_schema_version")
        != api_client.USAGE_LEDGER_SCHEMA_VERSION
        or not isinstance(ledger_id, str)
        or not ledger_id
        or start.get("usage_ledger_path") != ledger_path.as_posix()
        or start.get("usage_ledger_state_path") != state_path.as_posix()
    ):
        raise Phase3MainLiveError(
            "formal identity start usage-ledger identity drifted")
    identity: dict[str, object] = {
        "schema_version": api_client.USAGE_LEDGER_SCHEMA_VERSION,
        "ledger_id": ledger_id,
        "ledger_path": ledger_path.as_posix(),
        "state_path": state_path.as_posix(),
    }
    if canonical_sha256(identity) != start.get(
        "usage_ledger_identity_canonical_sha256"
    ):
        raise Phase3MainLiveError(
            "formal identity start usage-ledger identity hash drifted")
    for field in (
        "usage_ledger_genesis_event_hash",
        "usage_ledger_genesis_raw_sha256",
        "usage_ledger_genesis_state_raw_sha256",
    ):
        phase3_main_manifest._sha256(  # noqa: SLF001
            start.get(field), f"formal identity start {field}")
    return identity


def _load_stable_start_bound_ledger(
    start: Mapping[str, Any], *, expected_ledger_path: Path,
) -> tuple[api_client.UsageLedgerSnapshot, bytes, bytes]:
    """Validate stable final ledger and state descent from the started genesis."""
    ledger_path = expected_ledger_path.resolve()
    state_path = api_client.usage_ledger_state_path(ledger_path).resolve()
    expected_identity = _start_bound_ledger_identity(
        start, expected_ledger_path=ledger_path)
    if not ledger_path.is_file() or not state_path.is_file():
        raise Phase3MainLiveError(
            "started formal identity is missing its bound usage ledger or state")
    try:
        snapshot = api_client.load_chained_usage_ledger(
            ledger_path, expected_identity=expected_identity)
        ledger_raw = ledger_path.read_bytes()
        state_raw = state_path.read_bytes()
        events = api_client._read_usage_events(ledger_path)  # noqa: SLF001
        confirmed = api_client.load_chained_usage_ledger(
            ledger_path, expected_identity=expected_identity)
        if ledger_path.read_bytes() != ledger_raw or state_path.read_bytes() != state_raw:
            raise Phase3MainLiveError(
                "started usage ledger changed while its final state was validated")
    except (OSError, api_client.UsageLedgerError) as exc:
        raise Phase3MainLiveError(
            "started usage ledger does not validate against its start binding") from exc
    if (
        not events
        or len(events) != snapshot.last_sequence + 1
        or confirmed.last_sequence != snapshot.last_sequence
        or confirmed.last_event_hash != snapshot.last_event_hash
        or events[0].get("event_hash")
        != start.get("usage_ledger_genesis_event_hash")
    ):
        raise Phase3MainLiveError(
            "started usage ledger does not descend from its bound genesis")
    first_newline = ledger_raw.find(b"\n")
    if (
        first_newline < 0
        or hashlib.sha256(ledger_raw[:first_newline + 1]).hexdigest()
        != start.get("usage_ledger_genesis_raw_sha256")
        or (
            snapshot.last_sequence == 0
            and hashlib.sha256(state_raw).hexdigest()
            != start.get("usage_ledger_genesis_state_raw_sha256")
        )
    ):
        raise Phase3MainLiveError(
            "started usage ledger genesis bytes or state do not match the start record")
    return snapshot, ledger_raw, state_raw


def _start_identity(
    prepared: PreparedMainRun,
    usage_snapshot: api_client.UsageLedgerSnapshot,
) -> Path:
    """Record the single-shot formal identity without consuming its authorization."""
    identity = prepared.identity
    start = _identity_start_path(identity)
    ledger_binding = _fresh_ledger_start_binding(prepared, usage_snapshot)
    _write_exclusive_json(start, {
        "schema_version": IDENTITY_START_SCHEMA,
        "status": "started_single_shot",
        "run_id": identity.run_id,
        "manifest_canonical_sha256": identity.manifest_sha256,
        "manifest_identity_sha256": prepared.manifest["manifest_identity_sha256"],
        "authorization_id": prepared.authorization["authorization_id"],
        "authorization_canonical_sha256": canonical_sha256(prepared.authorization),
        "authorization_raw_sha256": prepared.authorization_raw_sha256,
        "authorization_signature_raw_sha256": (
            prepared.authorization_signature_raw_sha256),
        "artifact_root": identity.artifact_root.as_posix(),
        **ledger_binding,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    }, label="formal identity start record")
    return start


def _stable_regular_file_bytes(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise Phase3MainLiveError(f"{label} must be a regular file: {path}")
    try:
        first = path.read_bytes()
        second = path.read_bytes()
    except OSError as exc:
        raise Phase3MainLiveError(f"could not read {label}: {path}") from exc
    if first != second:
        raise Phase3MainLiveError(f"{label} changed while read")
    return first


def _load_stable_strict_object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _stable_regular_file_bytes(path, label)
    value = _parse_strict_json(raw, path)
    if not isinstance(value, dict):
        raise Phase3MainLiveError(f"{label} must be a JSON object")
    return value, raw


def _price_change_start_evidence(
    identity: phase3_main_runner.MainRunIdentity,
    manifest: Mapping[str, Any],
) -> tuple[bytes, bytes, bytes]:
    """Reopen exact start evidence without taking the formal run lease."""
    start_path = _identity_start_path(identity)
    if _identity_void_path(identity).exists():
        raise Phase3MainLiveError(
            "a provider price change cannot be recorded for a voided identity")
    if _identity_complete_path(identity).exists() or identity.paths.completion.exists():
        raise Phase3MainLiveError(
            "a provider price change cannot be recorded for a completed identity")
    start, start_raw = _load_stable_strict_object(
        start_path, "formal identity start record")
    if set(start) != IDENTITY_START_FIELDS:
        raise Phase3MainLiveError("formal identity start record fields drifted")
    if (
        start.get("schema_version") != IDENTITY_START_SCHEMA
        or start.get("status") != "started_single_shot"
        or start.get("run_id") != identity.run_id
        or start.get("manifest_canonical_sha256") != identity.manifest_sha256
        or start.get("manifest_identity_sha256")
        != manifest.get("manifest_identity_sha256")
        or start.get("artifact_root") != identity.artifact_root.as_posix()
    ):
        raise Phase3MainLiveError("formal identity start record binding drifted")
    phase3_main_manifest._utc(  # noqa: SLF001
        start.get("recorded_at_utc"), "formal identity start recorded_at_utc")

    active, active_raw = _load_stable_strict_object(
        identity.paths.active_marker, "formal active marker")
    if set(active) != ACTIVE_MARKER_FIELDS:
        raise Phase3MainLiveError("formal active marker fields drifted")
    expected_active = {
        "schema_version": phase3_main_runner.ACTIVE_MARKER_SCHEMA,
        "status": "active",
        "run_id": identity.run_id,
        "manifest_sha256": identity.manifest_sha256,
        "artifact_root": identity.artifact_root.as_posix(),
        "artifact_root_sha256": identity.artifact_root_sha256,
        "journal_execution_identity": identity.journal_execution_identity,
    }
    if any(active.get(field) != expected for field, expected in expected_active.items()):
        raise Phase3MainLiveError("formal active marker binding drifted")
    phase3_main_manifest._utc(  # noqa: SLF001
        active.get("started_at_utc"), "formal active marker started_at_utc")
    active_pid = active.get("pid")
    if isinstance(active_pid, bool) or not isinstance(active_pid, int) or active_pid <= 0:
        raise Phase3MainLiveError("formal active marker pid is invalid")

    binding_raw = _stable_regular_file_bytes(
        identity.paths.identity_binding, "formal identity binding")
    if binding_raw != phase3_main_runner._identity_binding_bytes(identity):  # noqa: SLF001
        raise Phase3MainLiveError("formal identity binding bytes drifted")
    return start_raw, active_raw, binding_raw


def record_provider_price_change(
    manifest_path: str | Path,
    *,
    trigger_kind: str,
    evidence_path: str | Path,
    note: str,
) -> dict[str, Any]:
    """Exclusively record a non-authorizing stop signal for one active identity."""
    manifest_file = Path(manifest_path).resolve()
    manifest, manifest_raw = _load_stable_strict_object(
        manifest_file, "main manifest")
    try:
        validation = phase3_main_manifest.validate_main_manifest(
            manifest,
            project_root=LIVE_PROJECT_ROOT,
            verify_files=False,
            verify_runtime=False,
        )
    except phase3_main_manifest.MainManifestError as exc:
        raise Phase3MainLiveError(
            f"main manifest cannot identify the price-change stop target: {exc}") from exc
    identity = phase3_main_runner.MainRunIdentity(
        run_id=str(validation["run_id"]),
        manifest_sha256=str(validation["manifest_canonical_sha256"]),
        artifact_root=Path(validation["artifact_root"]),
        identity_registry_root=Path(validation["identity_registry_root"]),
    )
    if identity.paths.price_change_signal.name != (
        phase3_main_runtime_policies.PRICE_CHANGE_SIGNAL_FILENAME
    ):
        raise Phase3MainLiveError("price-change signal filename drifted")
    if _exclusive_publish_temp_path(identity.paths.price_change_signal) != (
        identity.paths.price_change_signal_publish_temp
    ):
        raise Phase3MainLiveError("price-change signal publish-stage filename drifted")
    if os.path.lexists(identity.paths.price_change_signal):
        raise Phase3MainLiveError(
            f"provider price-change signal already exists: "
            f"{identity.paths.price_change_signal}")
    initial_start_evidence = _price_change_start_evidence(identity, manifest)

    evidence = Path(evidence_path).resolve()
    if evidence == identity.paths.price_change_signal:
        raise Phase3MainLiveError("price-change evidence cannot be the signal itself")
    evidence_raw = _stable_regular_file_bytes(evidence, "provider price-change evidence")
    if not evidence_raw:
        raise Phase3MainLiveError("provider price-change evidence cannot be empty")
    observed_at_utc = datetime.now(timezone.utc).isoformat()
    signal = {
        "schema_version": phase3_main_runtime_policies.PRICE_CHANGE_SIGNAL_SCHEMA,
        "status": "provider_price_change_observed",
        "run_id": identity.run_id,
        "manifest_canonical_sha256": identity.manifest_sha256,
        "artifact_root": identity.artifact_root.as_posix(),
        "artifact_root_sha256": identity.artifact_root_sha256,
        "journal_execution_identity": identity.journal_execution_identity,
        "observed_at_utc": observed_at_utc,
        "trigger_kind": trigger_kind,
        "evidence": {
            "path": evidence.as_posix(),
            "raw_sha256": hashlib.sha256(evidence_raw).hexdigest(),
            "byte_count": len(evidence_raw),
        },
        "note": note,
        "stop_new_logical_provider_calls": True,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
    }
    try:
        phase3_main_runtime_policies.validate_price_change_signal(
            signal,
            run_id=identity.run_id,
            manifest_canonical_sha256=identity.manifest_sha256,
            artifact_root=identity.artifact_root,
            journal_execution_identity=identity.journal_execution_identity,
            verify_evidence=True,
        )
    except phase3_main_runtime_policies.MainRuntimePolicyError as exc:
        raise Phase3MainLiveError(f"provider price-change signal is invalid: {exc}") from exc

    current_manifest_raw = _stable_regular_file_bytes(manifest_file, "main manifest")
    current_start_evidence = _price_change_start_evidence(identity, manifest)
    current_evidence_raw = _stable_regular_file_bytes(
        evidence, "provider price-change evidence")
    if manifest_raw != current_manifest_raw:
        raise Phase3MainLiveError("main manifest changed before stop-signal publication")
    if initial_start_evidence != current_start_evidence:
        raise Phase3MainLiveError(
            "formal identity start evidence changed before stop-signal publication")
    if evidence_raw != current_evidence_raw:
        raise Phase3MainLiveError(
            "provider price-change evidence changed before stop-signal publication")
    _write_exclusive_json(
        identity.paths.price_change_signal,
        signal,
        label="provider price-change signal",
    )
    try:
        signal_validation = (
            phase3_main_runtime_policies.load_and_validate_price_change_signal(
                identity.paths.price_change_signal,
                run_id=identity.run_id,
                manifest_canonical_sha256=identity.manifest_sha256,
                artifact_root=identity.artifact_root,
                journal_execution_identity=identity.journal_execution_identity,
                verify_evidence=True,
            )
        )
    except phase3_main_runtime_policies.MainRuntimePolicyError as exc:
        raise Phase3MainLiveError(
            "provider price-change signal was published but did not reopen exactly; "
            "signal presence still blocks new provider calls"
        ) from exc
    return {
        "status": "provider_price_change_observed",
        "run_id": identity.run_id,
        "signal_path": identity.paths.price_change_signal.as_posix(),
        "signal_raw_sha256": _raw_sha256(identity.paths.price_change_signal),
        "trigger_kind": signal_validation["trigger_kind"],
        "stop_new_logical_provider_calls": True,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
    }


def record_environmental_interruption(
    manifest_path: str | Path,
    *,
    reason_code: str,
) -> dict[str, Any]:
    """Void one started, incomplete identity with exactly one short durable record.

    This function neither authorizes nor starts a replacement identity. A replacement must
    use a fresh manifest-derived identity and pass the normal launch gates.
    """
    if reason_code not in ENVIRONMENTAL_INTERRUPTION_REASONS:
        raise Phase3MainLiveError(
            "environmental interruption reason is not one of the frozen reason codes")
    manifest = _load_strict_object(manifest_path, "main manifest")
    validation = phase3_main_manifest.validate_main_manifest(
        manifest,
        project_root=LIVE_PROJECT_ROOT,
        verify_files=False,
        verify_runtime=False,
    )
    identity = phase3_main_runner.MainRunIdentity(
        run_id=str(validation["run_id"]),
        manifest_sha256=str(validation["manifest_canonical_sha256"]),
        artifact_root=Path(validation["artifact_root"]),
        identity_registry_root=Path(validation["identity_registry_root"]),
    )
    try:
        with phase3_v3_live.RunLease(identity.paths.lease):
            return _record_environmental_interruption_locked(
                identity=identity,
                manifest=manifest,
                reason_code=reason_code,
            )
    except phase3_v3_live.Phase3V3LiveError as exc:
        raise Phase3MainLiveError(
            "environmental interruption record could not acquire the main run lease") from exc


def _record_environmental_interruption_locked(
    *,
    identity: phase3_main_runner.MainRunIdentity,
    manifest: Mapping[str, Any],
    reason_code: str,
) -> dict[str, Any]:
    start_path = _identity_start_path(identity)
    if not start_path.is_file():
        raise Phase3MainLiveError(
            "environmental interruption cannot void an identity that never started")
    start = _load_strict_object(start_path, "formal identity start record")
    if set(start) != IDENTITY_START_FIELDS:
        raise Phase3MainLiveError("formal identity start record fields drifted")
    if (
        start.get("schema_version") != IDENTITY_START_SCHEMA
        or start.get("status") != "started_single_shot"
        or start.get("run_id") != identity.run_id
        or start.get("manifest_canonical_sha256") != identity.manifest_sha256
        or start.get("manifest_identity_sha256") != manifest["manifest_identity_sha256"]
        or start.get("artifact_root") != identity.artifact_root.as_posix()
    ):
        raise Phase3MainLiveError("formal identity start record binding drifted")
    authorization_id = start.get("authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id:
        raise Phase3MainLiveError("formal identity start authorization_id is invalid")
    for field in (
        "authorization_canonical_sha256",
        "authorization_raw_sha256",
        "authorization_signature_raw_sha256",
    ):
        phase3_main_manifest._sha256(  # noqa: SLF001
            start.get(field), f"formal identity start {field}")
    phase3_main_manifest._utc(  # noqa: SLF001
        start.get("recorded_at_utc"), "formal identity start recorded_at_utc")
    void_path = _identity_void_path(identity)
    if _identity_complete_path(identity).exists() or identity.paths.completion.exists():
        raise Phase3MainLiveError("a completed formal identity cannot be voided")
    usage_ledger_path = identity.paths.usage_ledger.resolve()
    usage_ledger_state_path = api_client.usage_ledger_state_path(
        usage_ledger_path).resolve()
    _, usage_ledger_raw, usage_ledger_state_raw = _load_stable_start_bound_ledger(
        start, expected_ledger_path=usage_ledger_path)
    _write_exclusive_json(void_path, {
        "schema_version": IDENTITY_VOID_SCHEMA,
        "status": "voided_environmental_interruption",
        "run_id": identity.run_id,
        "manifest_canonical_sha256": identity.manifest_sha256,
        "manifest_identity_sha256": manifest["manifest_identity_sha256"],
        "artifact_root": identity.artifact_root.as_posix(),
        "start_record_path": start_path.as_posix(),
        "start_record_raw_sha256": _raw_sha256(start_path),
        "usage_ledger_path": usage_ledger_path.as_posix(),
        "usage_ledger_raw_sha256": hashlib.sha256(usage_ledger_raw).hexdigest(),
        "usage_ledger_state_path": usage_ledger_state_path.as_posix(),
        "usage_ledger_state_raw_sha256": hashlib.sha256(
            usage_ledger_state_raw).hexdigest(),
        "usage_ledger_identity_canonical_sha256": (
            start["usage_ledger_identity_canonical_sha256"]),
        "usage_ledger_genesis_event_hash": (
            start["usage_ledger_genesis_event_hash"]),
        "reason_code": reason_code,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    }, label="environmental interruption record")
    return {
        "status": "voided_environmental_interruption",
        "run_id": identity.run_id,
        "reason_code": reason_code,
        "record_path": void_path.as_posix(),
        "replacement_authorized": False,
    }


def run_main(
    manifest_path: str | Path,
    authorization_path: str | Path,
) -> dict[str, Any]:
    """Run one fresh main identity after all read-only gates pass.

    The execution loop is installed with finalization in this module's closeout section. This
    entry already enforces the single-shot startup and private factory boundary. Any failure
    after identity start leaves the persistent started record in place and cannot resume.
    """
    _require_production_execution_unblocked()
    prepared = load_prepared_main(
        manifest_path, authorization_path, verify_git=True)
    _validate_launch_freshness(prepared)
    _revalidate_authenticated_authorization(prepared)
    sdk_client = _construct_verified_runtime_provider_sdk(prepared)
    identity = prepared.identity
    paths = identity.paths
    registry_root = identity.identity_registry_root
    if registry_root is None:
        raise Phase3MainLiveError("main identity has no persistent registry root")
    _durably_prepare_identity_registry(registry_root)
    with phase3_v3_live.RunLease(paths.lease) as held_run_lease:
        _assert_identity_not_started(identity)
        phase3_main_runner._assert_fresh_identity(paths)
        _validate_launch_freshness(prepared)
        _revalidate_authenticated_authorization(prepared)
        _durably_create_directory_tree(paths.root)
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
            _validate_environmental_restart(
                manifest_validation=prepared.manifest_validation,
                billing_record=billing_record,
                billing_validation=billing_validation,
                project_root=prepared.project_root,
            )
            _probe_artifact_publication(paths.root)
            raw_client = _construct_provider_client(
                prepared, snapshot, sdk_client=sdk_client)
            client = JournalingClient(raw_client, journal)
            _start_identity(prepared, snapshot)
            return _drive_and_finalize(
                prepared,
                client,
                held_run_lease=held_run_lease,
            )
        finally:
            phase3_main_runner._restore_start_evidence(
                paths, active_marker=active, identity_binding=binding)


def _drive_and_finalize(
    prepared: PreparedMainRun,
    client: Any,
    *,
    held_run_lease: phase3_v3_live.RunLease,
) -> dict[str, Any]:
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
    reviewer_dispatches = 0
    reviewer_dispatch_ceiling = int(
        prepared.reviewer_usage_policy_validation["maximum_reviewer_dispatches"])
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
        "reviewer_usage_unit": phase3_main_runtime_policies.REVIEWER_USAGE_UNIT,
        "maximum_reviewer_dispatches": reviewer_dispatch_ceiling,
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
            reviewer_dispatches = _admit_reviewer_wave_quantity(
                prepared,
                previously_admitted=reviewer_dispatches,
                incoming=len(outcome.pending_payloads),
            )
            _append_jsonl(paths.run_log, {
                "event": "reviewer_usage_reserved",
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "wave": pass_index,
                "usage_unit": phase3_main_runtime_policies.REVIEWER_USAGE_UNIT,
                "dispatches_this_wave": len(outcome.pending_payloads),
                "cumulative_reviewer_dispatches": reviewer_dispatches,
                "maximum_reviewer_dispatches": reviewer_dispatch_ceiling,
                "failed_or_ambiguous_dispatches_count": True,
                "non_claim": "dispatch count is not USD or token accounting",
            })
            _review_wave_same_process(
                prepared,
                outcome.pending_payloads,
                wave=pass_index,
                held_run_lease=held_run_lease,
            )
            _append_jsonl(paths.run_log, {
                "event": "reviewer_usage_wave_completed",
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "wave": pass_index,
                "usage_unit": phase3_main_runtime_policies.REVIEWER_USAGE_UNIT,
                "dispatches_this_wave": len(outcome.pending_payloads),
                "cumulative_reviewer_dispatches": reviewer_dispatches,
                "maximum_reviewer_dispatches": reviewer_dispatch_ceiling,
                "non_claim": "dispatch count is not USD or token accounting",
            })
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
    held_run_lease: phase3_v3_live.RunLease,
) -> None:
    """Dispatch a bound reviewer wave and commit it without re-entering the run lease."""
    paths = prepared.identity.paths
    try:
        phase3_main_reviewer_commit.require_held_run_lease(
            held_run_lease,
            expected_path=paths.lease,
        )
    except phase3_main_reviewer_commit.ReviewerWaveCommitError as exc:
        raise Phase3MainLiveError(
            "reviewer wave requires the exact held formal run lease") from exc
    current_authorization = _revalidate_authenticated_authorization(prepared)
    capacity_plan, capacity_validation = _revalidate_capacity_snapshot(prepared)
    worklist = export_reviewer_worklist(
        pending_payloads,
        str(prepared.reviewer_prompt["prompt"]),
        paths.reviewer_worklist,
    )
    worklist_raw = paths.reviewer_worklist.read_bytes()
    try:
        persisted_worklist = json.loads(
            worklist_raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Phase3MainLiveError(
            "persisted reviewer worklist is not strict UTF-8 JSON") from exc
    if persisted_worklist != worklist:
        raise Phase3MainLiveError(
            "persisted reviewer worklist differs from the exported wave")
    items = list(worklist["items"])
    payload_hash = hashlib.sha256(
        "".join(str(item["payload_sha256"]) for item in items).encode("utf-8")
    ).hexdigest()[:12]
    packet_dir = paths.review_packets_root / (
        f"wave-{wave:03d}-{payload_hash}-{uuid.uuid4().hex[:8]}")
    packet_dir.mkdir()
    worklist_snapshot_path = packet_dir / "WORKLIST.json"
    _write_exclusive_bytes(
        worklist_snapshot_path,
        worklist_raw,
        label="reviewer worklist snapshot",
    )
    index: list[dict[str, Any]] = []
    packet_bindings: list[dict[str, Any]] = []
    for number, item in enumerate(items, 1):
        prompt = str(item["subagent_prompt"])
        prompt_raw = prompt.encode("utf-8")
        prompt_sha = hashlib.sha256(prompt_raw).hexdigest()
        if prompt_sha != item["subagent_prompt_sha256"]:
            raise Phase3MainLiveError("reviewer worklist prompt hash drifted")
        filename = f"{number:05d}_{str(item['payload_sha256'])[:12]}.txt"
        _write_exclusive_bytes(
            packet_dir / filename,
            prompt_raw,
            label="reviewer packet",
        )
        index.append({
            "n": number,
            "file": filename,
            "payload_sha256": item["payload_sha256"],
            "prompt_sha256": prompt_sha,
        })
        packet_bindings.append({
            "file": filename,
            "payload_sha256": item["payload_sha256"],
            "prompt_sha256": prompt_sha,
            "byte_count": len(prompt_raw),
        })
    packet_index_path = packet_dir / "INDEX.json"
    _write_exclusive_bytes(
        packet_index_path,
        (json.dumps(
            {"count": len(index), "items": index},
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
        ) + "\n").encode("utf-8"),
        label="reviewer packet index",
    )
    rulings_path = packet_dir / "rulings.jsonl"
    _write_exclusive_bytes(
        rulings_path,
        b"",
        label="reviewer rulings output",
    )
    runtime = prepared.manifest["runtime"]
    reviewer_configuration = capacity_plan["reviewer_configuration"]
    dispatch_guard = _build_reviewer_dispatch_guard(
        prepared,
        authorization=current_authorization,
        capacity_validation=capacity_validation,
        reviewer_configuration=reviewer_configuration,
        packet_dir=packet_dir,
        output_path=rulings_path,
        worklist_snapshot_path=worklist_snapshot_path,
        packet_index_path=packet_index_path,
        packet_bindings=packet_bindings,
    )
    dispatch_guard_path = packet_dir / codex_reviewer_batch.DISPATCH_GUARD_FILENAME
    dispatch_guard_raw = (
        json.dumps(
            dispatch_guard,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            indent=1,
        )
        + "\n"
    ).encode("utf-8")
    _write_exclusive_bytes(
        dispatch_guard_path,
        dispatch_guard_raw,
        label="reviewer dispatch guard snapshot",
    )
    dispatch_guard_raw_sha256 = hashlib.sha256(dispatch_guard_raw).hexdigest()
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
        "--dispatch-guard", str(dispatch_guard_path),
        "--dispatch-guard-raw-sha256", dispatch_guard_raw_sha256,
    ]
    if "openai_provider_supports_websockets" in reviewer_configuration:
        transport_value = reviewer_configuration[
            "openai_provider_supports_websockets"
        ]
        if not isinstance(transport_value, bool):
            raise Phase3MainLiveError(
                "capacity plan reviewer transport configuration is invalid"
            )
        command.extend([
            "--openai-provider-supports-websockets",
            str(transport_value).lower(),
        ])
    try:
        model_provider_profile = (
            codex_reviewer_batch.normalize_model_provider_profile(
                reviewer_configuration.get("model_provider_profile")
            )
        )
    except ValueError as exc:
        raise Phase3MainLiveError(
            f"capacity plan reviewer model provider configuration is invalid: {exc}"
        ) from exc
    if (
        "openai_provider_supports_websockets" in reviewer_configuration
        and model_provider_profile is not None
    ):
        raise Phase3MainLiveError(
            "capacity plan reviewer transport configurations are mutually exclusive"
        )
    if model_provider_profile is not None:
        command.extend([
            "--model-provider-profile-json",
            json.dumps(
                model_provider_profile,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        ])
    phase3_main_manifest.validate_main_manifest(
        prepared.manifest,
        project_root=prepared.project_root,
        verify_files=True,
        verify_runtime=True,
    )
    _verify_execution_code_root(prepared.project_root)
    _verify_clean_git_identity(prepared.manifest, prepared.project_root)
    completed = subprocess.run(
        command,
        cwd=prepared.project_root,
        capture_output=True,
        text=True,
        env=_subprocess_environment_without_together_credentials(),
    )
    if completed.returncode != 0:
        raise Phase3MainLiveError(
            f"reviewer wave {wave} failed with exit {completed.returncode}: "
            f"{completed.stderr[-500:]}")
    if not rulings_path.is_file():
        raise Phase3MainLiveError(
            f"reviewer wave {wave} completed without a ruling store")
    wave_tree_sha_after_batch = (
        phase3_main_reviewer_provenance.review_packets_tree_canonical_sha256(
            packet_dir.resolve()))
    rulings_raw = rulings_path.read_bytes()
    rows = []
    try:
        rulings_text = rulings_raw.decode("utf-8")
    except UnicodeError as exc:
        raise Phase3MainLiveError("reviewer rulings are not UTF-8") from exc
    for line_number, line in enumerate(rulings_text.splitlines(), 1):
        if not line.strip():
            raise Phase3MainLiveError(
                f"reviewer rulings contain a blank row at line {line_number}")
        try:
            row = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON number {value!r}")),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise Phase3MainLiveError(
                f"reviewer ruling line {line_number} is not strict JSON") from exc
        if not isinstance(row, dict):
            raise Phase3MainLiveError("reviewer ruling is not an object")
        rows.append(row)
    expected = {item["payload_sha256"]: item["prompt_sha256"] for item in index}
    if len(rows) != len(expected) or {row.get("payload_sha256") for row in rows} != set(
        expected):
        raise Phase3MainLiveError("reviewer wave result identity set is not exact")
    commit_entries = []
    expected_model = str(runtime["reviewer_model"])
    expected_effort = str(runtime["reviewer_reasoning_effort"])
    expected_concurrency = int(runtime["reviewer_concurrency"])
    expected_cli_path = Path(str(
        reviewer_configuration["reviewer_cli_resolved_path"])).resolve().as_posix()
    capacity_runtime = capacity_validation.get("runtime")
    if not isinstance(capacity_runtime, Mapping):  # pragma: no cover - guarded above
        raise Phase3MainLiveError(
            "review capacity validation omits its runtime binding")
    expected_cli_version = str(capacity_runtime["reviewer_cli_version"])
    expected_capacity_host = str(capacity_runtime["host_identity"])
    expected_deadline = phase3_main_manifest._utc(  # noqa: SLF001
        current_authorization["valid_until_utc"],
        "authorization.valid_until_utc",
    )
    evidence_observed_at = datetime.now(timezone.utc)
    reservation_checks: list[tuple[
        Mapping[str, Any], Mapping[str, Any], Mapping[str, Any],
    ]] = []
    for row in rows:
        payload = row["payload_sha256"]
        packet_meta = next(item for item in index if item["payload_sha256"] == payload)
        evidence = row.get("evidence")
        if not isinstance(evidence, Mapping):
            raise Phase3MainLiveError(
                "reviewer ruling omits its invocation evidence reference")
        try:
            evidence_validation_kwargs: dict[str, Any] = {
                "expected_model": expected_model,
                "expected_effort": expected_effort,
                "expected_concurrency": expected_concurrency,
            }
            if "openai_provider_supports_websockets" in reviewer_configuration:
                evidence_validation_kwargs[
                    "expected_openai_provider_supports_websockets"
                ] = reviewer_configuration[
                    "openai_provider_supports_websockets"
                ]
            if "model_provider_profile" in reviewer_configuration:
                evidence_validation_kwargs[
                    "expected_model_provider_profile"
                ] = reviewer_configuration["model_provider_profile"]
            receipt = codex_reviewer_batch.validate_invocation_evidence(
                packet_dir / str(packet_meta["file"]),
                evidence,
                **evidence_validation_kwargs,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise Phase3MainLiveError(
                f"reviewer invocation evidence failed for {payload}: {exc}") from exc
        invocation = receipt["invocation"]
        outcome = receipt["outcome"]
        receipt_guard = receipt.get("dispatch_guard")
        if (
            not isinstance(receipt_guard, Mapping)
            or receipt_guard.get("verified") is not True
            or receipt_guard.get("snapshot_raw_sha256")
            != dispatch_guard_raw_sha256
            or receipt_guard.get("reviewer_cli_version")
            != expected_cli_version
        ):
            raise Phase3MainLiveError(
                "reviewer invocation lacks the exact successful dispatch guard")
        _validate_reviewer_dispatch_reservation(
            packet_dir,
            guard_evidence=receipt_guard,
            guard_raw_sha256=dispatch_guard_raw_sha256,
            packet_meta=packet_meta,
            output_path=rulings_path,
            reviewer_model=expected_model,
            reviewer_reasoning_effort=expected_effort,
            reviewer_concurrency=expected_concurrency,
            reviewer_cli_version=expected_cli_version,
            capacity_host_identity=expected_capacity_host,
            outcome=outcome,
        )
        reservation_checks.append((receipt_guard, packet_meta, outcome))
        observed_cli_path = _validate_reviewer_invocation_capacity_binding(
            invocation,
            reviewer_configuration=reviewer_configuration,
            capacity_validation=capacity_validation,
        )
        if observed_cli_path != expected_cli_path:  # pragma: no cover
            raise Phase3MainLiveError(
                "reviewer invocation capacity CLI path changed during validation")
        receipt_deadline = outcome.get("authorization_deadline_utc")
        try:
            observed_deadline = phase3_main_manifest._utc(  # noqa: SLF001
                receipt_deadline, "reviewer evidence authorization deadline")
        except (TypeError, ValueError) as exc:
            raise Phase3MainLiveError(
                "reviewer invocation evidence has an invalid authorization deadline") from exc
        if observed_deadline != expected_deadline:
            raise Phase3MainLiveError(
                "reviewer invocation used another authorization deadline")
        _validate_reviewer_invocation_time_binding(
            outcome,
            authorization=current_authorization,
            capacity_validation=capacity_validation,
            observed_at=evidence_observed_at,
        )
        if outcome.get("result_ok") is not True or outcome.get(
                "event_stream_errors") != []:
            raise Phase3MainLiveError(
                "reviewer invocation did not retain one structurally valid result")

        clean_fields = {
            "payload_sha256", "prompt_sha256", "raw_output", "tool_uses", "evidence",
        }
        error_fields = {"payload_sha256", "status", "raw_output", "evidence"}
        if set(row) == clean_fields:
            if type(row.get("tool_uses")) is not int or row["tool_uses"] != 0:
                raise Phase3MainLiveError(
                    "reviewer ruling tool_uses must be the exact integer zero")
            if row.get("prompt_sha256") != expected[payload]:
                raise Phase3MainLiveError("reviewer ruling prompt proof drifted")
            if outcome.get("commands") != []:
                raise Phase3MainLiveError(
                    "clean reviewer ruling has command evidence")
            ruling_binding = receipt["artifacts"]["ruling"]
            retained_ruling = (
                packet_dir / str(ruling_binding["path"])).read_bytes()
            try:
                retained_text = retained_ruling.decode("utf-8").strip()
            except UnicodeError as exc:
                raise Phase3MainLiveError(
                    "retained reviewer ruling is not UTF-8") from exc
            if row.get("raw_output") != retained_text:
                raise Phase3MainLiveError(
                    "reviewer ruling text differs from retained invocation evidence")
            commit_entries.append({
                "payload_sha256": payload,
                "raw_output": row.get("raw_output"),
                "prompt_sha256": row.get("prompt_sha256"),
            })
        elif set(row) == error_fields:
            commands = outcome.get("commands")
            if (
                row.get("status") != "reviewer_error"
                or not isinstance(row.get("raw_output"), str)
                or not row["raw_output"].startswith("TOOL_USE_DETECTED:")
                or not isinstance(commands, list)
                or not commands
            ):
                raise Phase3MainLiveError(
                    "completed reviewer_error row is not an evidenced tool-use refusal")
            commit_entries.append({
                "payload_sha256": payload,
                "status": "reviewer_error",
                "raw_output": row.get("raw_output"),
            })
        else:
            raise Phase3MainLiveError("reviewer ruling fields drifted")
    if (
        paths.reviewer_worklist.read_bytes() != worklist_raw
        or worklist_snapshot_path.read_bytes() != worklist_raw
        or packet_index_path.read_bytes() != (
            json.dumps(
                {"count": len(index), "items": index},
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
            ) + "\n"
        ).encode("utf-8")
        or dispatch_guard_path.read_bytes() != dispatch_guard_raw
        or rulings_path.read_bytes() != rulings_raw
        or any(
            (packet_dir / str(binding["file"])).read_bytes()
            != str(items[position]["subagent_prompt"]).encode("utf-8")
            for position, binding in enumerate(packet_bindings)
        )
    ):
        raise Phase3MainLiveError(
            "reviewer wave evidence changed before its index was committed")
    for receipt_guard, packet_meta, outcome in reservation_checks:
        _validate_reviewer_dispatch_reservation(
            packet_dir,
            guard_evidence=receipt_guard,
            guard_raw_sha256=dispatch_guard_raw_sha256,
            packet_meta=packet_meta,
            output_path=rulings_path,
            reviewer_model=expected_model,
            reviewer_reasoning_effort=expected_effort,
            reviewer_concurrency=expected_concurrency,
            reviewer_cli_version=expected_cli_version,
            capacity_host_identity=expected_capacity_host,
            outcome=outcome,
        )
    if (
        phase3_main_reviewer_provenance.review_packets_tree_canonical_sha256(
            packet_dir.resolve())
        != wave_tree_sha_after_batch
    ):
        raise Phase3MainLiveError(
            "reviewer wave evidence tree changed before decision commit")
    evidence_bindings = {
        "authorization_canonical_sha256": canonical_sha256(current_authorization),
        "authorization_raw_sha256": prepared.authorization_raw_sha256,
        "authorization_signature_raw_sha256": (
            prepared.authorization_signature_raw_sha256),
        "capacity_plan_raw_sha256": str(
            prepared.manifest["input_bindings"]["capacity_plan"]["sha256"]),
        "worklist_snapshot_raw_sha256": hashlib.sha256(worklist_raw).hexdigest(),
        "packet_index_raw_sha256": _raw_sha256(packet_index_path),
        "dispatch_guard_raw_sha256": dispatch_guard_raw_sha256,
        "rulings_raw_sha256": hashlib.sha256(rulings_raw).hexdigest(),
    }
    transaction_id = phase3_main_reviewer_commit.derive_wave_commit_transaction_id(
        run_id=prepared.identity.run_id,
        manifest_canonical_sha256=prepared.identity.manifest_sha256,
        wave=wave,
        run_lease_path=paths.lease,
        evidence_bindings=evidence_bindings,
    )
    reviewer_error_count = sum(
        entry.get("status") == "reviewer_error" for entry in commit_entries)
    parsed_count = sum(
        entry.get("status") != "reviewer_error"
        and parse_reviewer_output(str(entry["raw_output"]))[0] is not None
        for entry in commit_entries
    )
    wave_row = {
        "schema_version": REVIEWER_WAVE_SCHEMA,
        "run_id": prepared.identity.run_id,
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        **evidence_bindings,
        "wave": wave,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload_count": len(items),
        "packet_directory": packet_dir.resolve().as_posix(),
        "reviewer_model": expected_model,
        "reviewer_reasoning_effort": expected_effort,
        "reviewer_concurrency": int(runtime["reviewer_concurrency"]),
        "reviewer_cli_resolved_path": expected_cli_path,
        "commit_counts": {
            "parsed": parsed_count,
            "malformed": len(commit_entries) - parsed_count - reviewer_error_count,
            "reviewer_error": reviewer_error_count,
        },
        "run_lease_path": paths.lease.resolve().as_posix(),
        "wave_commit_transaction_id": transaction_id,
    }
    committed = phase3_main_reviewer_commit.commit_reviewer_wave(
        transaction_directory=packet_dir,
        decision_store_path=paths.decisions,
        reviewer_index_path=paths.reviewer_index,
        run_id=prepared.identity.run_id,
        manifest_canonical_sha256=prepared.identity.manifest_sha256,
        wave=wave,
        run_lease_path=paths.lease,
        held_run_lease=held_run_lease,
        worklist=worklist,
        entries=commit_entries,
        reviewer_index_row=wave_row,
    )
    if (
        committed.wave_commit_transaction_id != transaction_id
        or committed.commit_counts != wave_row["commit_counts"]
    ):
        raise Phase3MainLiveError(
            "reviewer wave transaction result differs from its prepared row")


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
    provider_input_paths = {
        name: prepared.input_paths[name]
        for name in phase3_main_finalization.PROVIDER_INPUT_FIELDS
    }
    provider_input_raw_sha256s = {
        name: str(prepared.manifest["input_bindings"][name]["sha256"])
        for name in phase3_main_finalization.PROVIDER_INPUT_FIELDS
    }
    reviewer_input_paths = {
        name: prepared.input_paths[name]
        for name in phase3_main_finalization.REVIEWER_INPUT_FIELDS
    }
    reviewer_input_raw_sha256s = {
        name: str(prepared.manifest["input_bindings"][name]["sha256"])
        for name in phase3_main_finalization.REVIEWER_INPUT_FIELDS
    }
    runtime = prepared.manifest["runtime"]
    current_authorization = _revalidate_authenticated_authorization_scope(prepared)
    authorization_sha = canonical_sha256(current_authorization)
    finalization_inputs: dict[str, Any] = dict(
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
        provider_input_paths=provider_input_paths,
        provider_input_raw_sha256s=provider_input_raw_sha256s,
        reviewer_input_paths=reviewer_input_paths,
        reviewer_input_raw_sha256s=reviewer_input_raw_sha256s,
        capacity_result_path=prepared.input_paths["capacity_result"],
        expected_capacity_result_raw_sha256=str(
            prepared.manifest["input_bindings"]["capacity_result"]["sha256"]),
        capacity_dispatch_history_path=(
            prepared.input_paths["capacity_dispatch_history"]),
        expected_capacity_dispatch_history_raw_sha256=str(
            prepared.manifest["input_bindings"]["capacity_dispatch_history"]["sha256"]),
        review_packets_root_path=paths.review_packets_root,
        artifact_paths=artifacts,
        expected_oracle_model=str(prepared.protocol["roster"]["oracle"]),
        expected_reviewer_model=str(runtime["reviewer_model"]),
        expected_reviewer_reasoning_effort=str(
            runtime["reviewer_reasoning_effort"]),
        expected_reviewer_concurrency=int(runtime["reviewer_concurrency"]),
        authorization_approved_at_utc=str(
            current_authorization["approved_at_utc"]),
        authorization_valid_until_utc=str(
            current_authorization["valid_until_utc"]),
        prior_reconciled_usd=str(
            prepared.manifest["spend"]["prior_reconciled_usd"]),
        stage_cap_usd=str(current_authorization["stage_cap_usd"]),
    )
    finalization = phase3_main_finalization.build_finalization_admission(
        **finalization_inputs,
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    phase3_main_finalization.validate_finalization_admission(
        finalization,
        **finalization_inputs,
    )
    completed_authorization = _revalidate_authenticated_authorization_scope(prepared)
    completed_at = datetime.now(timezone.utc)
    if canonical_sha256(completed_authorization) != authorization_sha:
        raise Phase3MainLiveError(
            "main authorization scope changed during finalization")
    finalization = {
        **finalization,
        "recorded_at_utc": completed_at.isoformat(),
    }
    phase3_main_finalization.write_finalization_admission(paths.finalization, finalization)
    finalization_raw_sha = _raw_sha256(paths.finalization)
    analysis_run = phase3_main_analysis.run_analysis([
        "--results", str(paths.results),
        "--manifest", str(prepared.manifest_path),
        "--authorization", str(prepared.authorization_path),
        "--protocol", str(prepared.input_paths["protocol"]),
        "--pins", str(prepared.input_paths["analysis_pins"]),
        "--finalization", str(paths.finalization),
        "--out", str(paths.analysis_results),
        "--project-root", str(prepared.project_root),
    ])
    if analysis_run.returncode != 0:
        raise Phase3MainLiveError(
            f"main analysis returned exit {analysis_run.returncode}")
    if _raw_sha256(paths.finalization) != finalization_raw_sha:
        raise Phase3MainLiveError("main finalization bytes changed during analysis")
    _revalidate_final_boundary_inputs(prepared, finalization)
    phase3_main_finalization.validate_finalization_admission(
        finalization,
        **finalization_inputs,
    )
    artifact_hashes_value = finalization.get("artifact_hashes")
    artifact_hashes = (
        cast(Mapping[str, Any], artifact_hashes_value)
        if isinstance(artifact_hashes_value, Mapping)
        else None
    )
    result_store_binding = (
        artifact_hashes.get("result_store")
        if artifact_hashes is not None
        else None
    )
    finalization_results_raw_sha256 = (
        result_store_binding.get("raw_sha256")
        if isinstance(result_store_binding, Mapping)
        else None
    )
    if (
        not isinstance(finalization_results_raw_sha256, str)
        or len(finalization_results_raw_sha256) != 64
    ):
        raise Phase3MainLiveError(
            "validated finalization omits its result-store digest")
    analysis_raw, analysis_raw_sha256 = _validate_analysis_result_snapshot(
        prepared,
        expected_finalization_raw_sha256=finalization_raw_sha,
        expected_results_raw_sha256=finalization_results_raw_sha256,
        expected_analysis_raw=analysis_run.output_raw,
    )
    output_hashes = _completion_output_hashes(prepared)
    if (
        output_hashes.get("results") != finalization_results_raw_sha256
        or output_hashes.get("finalization") != finalization_raw_sha
        or output_hashes.get("analysis_results") != analysis_raw_sha256
    ):
        raise Phase3MainLiveError(
            "completion output hashes disagree with validated finalization or analysis")
    _require_completion_hashes_match_finalization(output_hashes, finalization)
    try:
        if (
            paths.analysis_results.read_bytes() != analysis_raw
            or _raw_sha256(paths.results) != finalization_results_raw_sha256
            or _raw_sha256(paths.finalization) != finalization_raw_sha
        ):
            raise Phase3MainLiveError(
                "formal results changed before completion was written")
    except OSError as exc:
        raise Phase3MainLiveError(
            "formal results became unreadable before completion") from exc
    final_output_hashes = _completion_output_hashes(prepared)
    if final_output_hashes != output_hashes:
        raise Phase3MainLiveError(
            "formal outputs changed during completion hashing")
    output_hashes = final_output_hashes
    completion = {
        "schema_version": "phase3_main_completion_v1",
        "status": "complete",
        "run_id": prepared.identity.run_id,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        "authorization_canonical_sha256": authorization_sha,
        "finalization_raw_sha256": finalization_raw_sha,
        "analysis_results_raw_sha256": analysis_raw_sha256,
        "output_hashes": output_hashes,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
    }
    _require_identity_not_voided(prepared.identity)
    _write_exclusive_json(
        paths.completion,
        completion,
        label="formal completion record",
    )
    completion_sha = _raw_sha256(paths.completion)
    _write_exclusive_json(_identity_complete_path(prepared.identity), {
        "schema_version": IDENTITY_COMPLETE_SCHEMA,
        "status": "complete",
        "run_id": prepared.identity.run_id,
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        "completion_path": paths.completion.as_posix(),
        "completion_raw_sha256": completion_sha,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    }, label="identity completion record")
    return completion


def _completion_output_hashes(
    prepared: PreparedMainRun,
) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    paths = prepared.manifest_validation["output_paths"]
    for name in sorted(paths):
        path = Path(paths[name])
        if name == "completion":
            result[name] = None
        elif name == "review_packets_root":
            try:
                result[name] = (
                    phase3_main_reviewer_provenance
                    .review_packets_tree_canonical_sha256(path)
                )
            except phase3_main_reviewer_provenance.MainReviewerProvenanceError as exc:
                raise Phase3MainLiveError(
                    "reviewer packet tree failed completion hashing") from exc
        else:
            if not path.is_file():
                raise Phase3MainLiveError(f"required final output is missing: {path}")
            result[name] = _raw_sha256(path)
    return result


def _require_completion_hashes_match_finalization(
    output_hashes: Mapping[str, str | None],
    finalization: Mapping[str, Any],
) -> None:
    artifact_hashes = finalization.get("artifact_hashes")
    if not isinstance(artifact_hashes, Mapping):
        raise Phase3MainLiveError(
            "validated finalization omits artifact hashes at completion")
    for artifact_name, output_name in (
        phase3_main_finalization.FINALIZATION_ARTIFACT_TO_MANIFEST_OUTPUT.items()
    ):
        artifact_binding = artifact_hashes.get(artifact_name)
        expected_sha256 = (
            artifact_binding.get("raw_sha256")
            if isinstance(artifact_binding, Mapping)
            else None
        )
        if output_hashes.get(output_name) != expected_sha256:
            raise Phase3MainLiveError(
                f"completion output {output_name!r} differs from finalization")
    reviewer_provenance = finalization.get("reviewer_provenance")
    expected_review_tree_sha256 = (
        reviewer_provenance.get("review_packets_tree_canonical_sha256")
        if isinstance(reviewer_provenance, Mapping)
        else None
    )
    if output_hashes.get("review_packets_root") != expected_review_tree_sha256:
        raise Phase3MainLiveError(
            "completion reviewer packet tree differs from finalization")


def main(argv: Sequence[str] | None = None) -> int:
    """Validate or execute one exact Phase 3 main launch.

    The mode flag is deliberately required. Merely supplying a manifest and authorization
    cannot start provider work.
    """
    parser = argparse.ArgumentParser(prog="phase3_main_live")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--authorization")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--validate-only", action="store_true",
        help="validate every launch binding without creating formal state or a provider client",
    )
    mode.add_argument(
        "--run", action="store_true",
        help="start the exact single-shot formal measurement",
    )
    mode.add_argument(
        "--record-environmental-interruption", action="store_true",
        help="void one started incomplete identity without authorizing a replacement",
    )
    parser.add_argument(
        "--reason-code",
        choices=sorted(ENVIRONMENTAL_INTERRUPTION_REASONS),
    )
    args = parser.parse_args(argv)
    if args.record_environmental_interruption:
        if args.authorization is not None:
            parser.error("--authorization is not used when recording an interruption")
        if args.reason_code is None:
            parser.error(
                "--reason-code is required with --record-environmental-interruption")
    else:
        if args.authorization is None:
            parser.error("--authorization is required with --validate-only or --run")
        if args.reason_code is not None:
            parser.error("--reason-code is only valid when recording an interruption")

    try:
        if args.record_environmental_interruption:
            payload = record_environmental_interruption(
                args.manifest,
                reason_code=args.reason_code,
            )
        elif args.validate_only:
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
    "record_environmental_interruption",
    "record_provider_price_change",
    "run_main",
]


if __name__ == "__main__":
    raise SystemExit(main())
