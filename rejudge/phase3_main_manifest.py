"""Small launch manifest and separate exact authorization for Phase 3 main.

The manifest describes one fresh main identity. It cannot authorize itself. Provider-call
authority lives only in a separate record that binds the canonical manifest digest, exact
run ID, endpoint roster, provider, reconciliation, forecast, and dollar cap.

This module is read-only. It validates files and runtime identity but never creates an output
directory, ledger, client, or provider connection.
"""
from __future__ import annotations

import hashlib
import platform
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from rejudge.phase2_execution import canonical_sha256
from rejudge import phase3_main_runtime_policies
from rejudge.phase3_main_runner import (
    CONFIRMED_MAIN_JUDGES,
    EXPECTED_MAIN_INVENTORY_SHA256,
    EXPECTED_MAIN_CELL_COUNT,
    EXPECTED_MAIN_JUDGMENT_COUNT,
    EXPECTED_MAIN_QUESTION_COUNT,
    EXPECTED_MAIN_TRANSCRIPT_COUNT,
)


MANIFEST_SCHEMA = "phase3_main_launch_manifest_v6"
AUTHORIZATION_SCHEMA = "phase3_main_exact_authorization_v6"
MANIFEST_FIELDS = frozenset({
    "schema_version",
    "stage",
    "run_id",
    "recorded_at_utc",
    "execution_authorized",
    "source_commit",
    "toolchain",
    "seeds",
    "input_bindings",
    "inventory",
    "output_contract",
    "restart",
    "runtime",
    "spend",
    "harness_check",
    "manifest_identity_sha256",
})
AUTHORIZATION_FIELDS = frozenset({
    "schema_version",
    "authorization_id",
    "stage",
    "run_id",
    "manifest_canonical_sha256",
    "manifest_identity_sha256",
    "provider",
    "provider_account_identity_sha256",
    "exact_model_ids",
    "reviewer_model",
    "reviewer_reasoning_effort",
    "reviewer_concurrency",
    "stage_cap_usd",
    "price_snapshot_sha256",
    "price_change_policy_sha256",
    "prior_reconciliation_sha256",
    "forecast_sha256",
    "reviewer_usage_policy_sha256",
    "maximum_reviewer_dispatches",
    "harness_execution_count",
    "formal_main_attempt_count",
    "approver",
    "approved_at_utc",
    "valid_until_utc",
    "exact_text",
    "execution_authorized",
    "provider_calls_authorized",
    "main_run_spend_authorized",
    "no_resume",
})
TOOLCHAIN_FIELDS = frozenset({
    "python_implementation",
    "python_version",
    "dependency_lock_path",
    "dependency_lock_raw_sha256",
    "linker_version_or_not_applicable",
})
SEED_FIELDS = frozenset({
    "harness_seed",
    "analysis_bootstrap_seed",
    "subsample_seed",
})
INPUT_BINDING_FIELDS = frozenset({"path", "sha256", "hash_kind"})
REQUIRED_INPUT_BINDINGS = frozenset({
    "protocol",
    "prompt_bundle",
    "reviewer_prompt",
    "reviewer_failure_policy",
    "role_limits",
    "analysis_pins",
    "scope_decision",
    "main_transcript_bundle",
    "transcript_verification",
    "tokenizer_manifest",
    "dynamic_residual_frame",
    "context_blocklist",
    "capacity_plan",
    "capacity_dispatch_history",
    "capacity_result",
    "capacity_execution_manifest",
    "capacity_execution_authorization",
    "capacity_execution_authorization_signature",
    "price_snapshot",
    "price_change_policy",
    "raw_provider_catalog",
    "raw_serverless_endpoints",
    "billing_reconciliation",
    "certified_cost_forecast",
    "reviewer_usage_policy",
    "harness_receipt",
    "canary_finalization",
})
INVENTORY_FIELDS = frozenset({
    "question_count",
    "transcript_count",
    "judgment_count",
    "cell_count",
    "canonical_sha256",
})
OUTPUT_CONTRACT_FIELDS = frozenset({
    "artifact_root",
    "identity_registry_root",
    "paths",
    "sha256s",
})
OUTPUT_PATH_FIELDS = frozenset({
    "active_marker",
    "identity_binding",
    "usage_ledger",
    "request_journal",
    "results",
    "decisions",
    "provider_error_log",
    "reviewer_worklist",
    "reviewer_index",
    "review_packets_root",
    "price_change_signal",
    "terminal_dispositions",
    "run_log",
    "finalization",
    "analysis_results",
    "completion",
})
OUTPUT_FILENAMES = {
    "active_marker": "main_run.active.json",
    "identity_binding": "main_run.identity.json",
    "usage_ledger": "main_usage.jsonl",
    "request_journal": "main_request_journal.jsonl",
    "results": "main_results.jsonl",
    "decisions": "main_reviewer_decisions.jsonl",
    "provider_error_log": "main_provider_errors.jsonl",
    "reviewer_worklist": "reviewer_worklist.json",
    "reviewer_index": "main_reviewer_index.jsonl",
    "review_packets_root": "main_review_packets",
    "price_change_signal": phase3_main_runtime_policies.PRICE_CHANGE_SIGNAL_FILENAME,
    "terminal_dispositions": "main_terminal_dispositions.jsonl",
    "run_log": "main_run_log.jsonl",
    "finalization": "main_finalization.json",
    "analysis_results": "main_analysis_results.json",
    "completion": "main_completion.json",
}
RESTART_FIELDS = frozenset({"mode", "predecessor"})
RESTART_MODE_INITIAL = "initial"
RESTART_MODE_ENVIRONMENTAL_SUCCESSOR = "environmental_successor"
PREDECESSOR_FIELDS = frozenset({
    "run_id",
    "manifest_canonical_sha256",
    "manifest_identity_sha256",
    "authorization_id",
    "authorization_canonical_sha256",
    "authorization_raw_sha256",
    "authorization_signature_raw_sha256",
    "artifact_root",
    "void_record_path",
    "void_record_raw_sha256",
    "usage_ledger_path",
    "usage_ledger_raw_sha256",
})
RUNTIME_FIELDS = frozenset({
    "provider",
    "provider_account_identity_sha256",
    "model_ids",
    "provider_worker_concurrency",
    "reviewer_cli_binary",
    "reviewer_model",
    "reviewer_reasoning_effort",
    "reviewer_concurrency",
    "gpu_ordinal",
})
SPEND_FIELDS = frozenset({
    "prior_reconciled_usd",
    "forecast_main_usd",
    "stage_cap_usd",
})
HARNESS_FIELDS = frozenset({
    "receipt_input_name",
    "seed_name",
    "status",
    "first_output_store_sha256",
    "rerun_output_store_sha256",
})


class MainManifestError(ValueError):
    """A Phase 3 main launch manifest or authorization failed closed."""


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MainManifestError(f"{label} must be an object")
    observed = set(value)
    if observed != expected:
        raise MainManifestError(
            f"{label} fields drifted: missing={sorted(expected - observed)!r}, "
            f"unexpected={sorted(observed - expected)!r}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MainManifestError(f"{label} must be a non-empty string")
    return value


def _sha256(value: Any, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise MainManifestError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _utc(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise MainManifestError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MainManifestError(f"{label} must use UTC")
    return parsed.astimezone(timezone.utc)


def _money(value: Any, label: str) -> Decimal:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise MainManifestError(f"{label} must be an exact non-negative decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise MainManifestError(
            f"{label} must be an exact non-negative decimal string") from exc
    if not amount.is_finite() or amount < 0:
        raise MainManifestError(f"{label} must be an exact non-negative decimal string")
    return amount


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MainManifestError(f"{label} must be a non-negative integer")
    return value


def _input_path(path_text: str, project_root: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path.resolve()
    if ".." in path.parts:
        raise MainManifestError(f"relative input path escapes project root: {path_text!r}")
    return (project_root / path).resolve()


def _raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: manifest[key]
        for key in sorted(MANIFEST_FIELDS - {"run_id", "manifest_identity_sha256"})
    }


def manifest_identity_sha256(manifest: Mapping[str, Any]) -> str:
    """Digest the immutable identity payload without its derived ID and digest fields."""
    return canonical_sha256(_identity_payload(manifest))


def manifest_canonical_sha256(manifest: Mapping[str, Any]) -> str:
    """Digest the complete validated manifest for the separate authorization record."""
    return canonical_sha256(dict(manifest))


def expected_run_id(manifest: Mapping[str, Any]) -> str:
    return f"phase3-main-{manifest_identity_sha256(manifest)[:16]}"


def expected_authorization_text(manifest: Mapping[str, Any]) -> str:
    """Exact owner sentence required by the separately stored authorization record."""
    runtime = manifest["runtime"]
    models = runtime["model_ids"]
    restart = manifest["restart"]
    identity_description = "an initial formal identity"
    if restart["mode"] == RESTART_MODE_ENVIRONMENTAL_SUCCESSOR:
        identity_description = (
            "an environmental successor to "
            f"{restart['predecessor']['run_id']}"
        )
    return (
        f"Approved: {manifest['run_id']} Phase 3 main on Together account identity SHA-256 "
        f"{runtime['provider_account_identity_sha256']}, with endpoints exactly {models[0]} "
        f"and {models[1]}, plus Codex reviewer dispatches using "
        f"{runtime['reviewer_model']} at {runtime['reviewer_reasoning_effort']} effort and "
        f"concurrency {runtime['reviewer_concurrency']}, {identity_description}, one "
        f"single-shot formal measurement, ${manifest['spend']['stage_cap_usd']} USD "
        "cumulative stage cap, and no resume of this identity. An environmental interruption "
        "voids this identity; any restart requires a fresh successor manifest and separate "
        "exact authorization. "
        "valid_until_utc is the latest start of a new logical provider call or individual "
        "reviewer invocation; already-started work and local evidence closeout may finish "
        "later. Any detected provider price change stops new logical provider calls, permits "
        "only already-started work to finish and be accounted, and requires a fresh price "
        "snapshot, forecast, manifest, cap, and exact authorization. External reviewer usage "
        f"is separately capped at {phase3_main_runtime_policies.MAXIMUM_REVIEWER_DISPATCHES} "
        "dispatches. Dispatch count is neither USD nor token accounting and is excluded from "
        "the Together USD stage cap."
    )


def _validate_input_bindings(
    value: Any, *, project_root: Path, verify_files: bool,
) -> dict[str, Path]:
    bindings = _exact_keys(value, REQUIRED_INPUT_BINDINGS, "input_bindings")
    resolved: dict[str, Path] = {}
    for name, raw in bindings.items():
        binding = _exact_keys(raw, INPUT_BINDING_FIELDS, f"input_bindings[{name!r}]")
        path_text = _text(binding["path"], f"input_bindings[{name!r}].path")
        if binding["hash_kind"] != "raw_sha256":
            raise MainManifestError(
                f"input_bindings[{name!r}].hash_kind must be 'raw_sha256'")
        expected = _sha256(binding["sha256"], f"input_bindings[{name!r}].sha256")
        path = _input_path(path_text, project_root)
        if verify_files:
            if not path.is_file():
                raise MainManifestError(f"bound input {name!r} is missing: {path}")
            observed = _raw_sha256(path)
            if observed != expected:
                raise MainManifestError(
                    f"bound input {name!r} raw SHA-256 drifted: {observed} != {expected}")
        resolved[name] = path
    return resolved


def _validate_output_contract(value: Any) -> tuple[Path, Path, dict[str, Path]]:
    contract = _exact_keys(value, OUTPUT_CONTRACT_FIELDS, "output_contract")
    artifact_root = Path(_text(contract["artifact_root"], "output_contract.artifact_root"))
    registry_root = Path(
        _text(contract["identity_registry_root"], "output_contract.identity_registry_root"))
    if not artifact_root.is_absolute() or not registry_root.is_absolute():
        raise MainManifestError("artifact and identity-registry roots must be absolute")
    artifact_root = artifact_root.resolve()
    registry_root = registry_root.resolve()
    if artifact_root == registry_root:
        raise MainManifestError("identity registry must be outside the disposable artifact root")
    for child, parent in ((registry_root, artifact_root), (artifact_root, registry_root)):
        try:
            child.relative_to(parent)
        except ValueError:
            continue
        raise MainManifestError(
            "artifact and identity-registry roots must not contain one another")

    paths = _exact_keys(contract["paths"], OUTPUT_PATH_FIELDS, "output_contract.paths")
    hashes = _exact_keys(contract["sha256s"], OUTPUT_PATH_FIELDS, "output_contract.sha256s")
    resolved: dict[str, Path] = {}
    for name, filename in OUTPUT_FILENAMES.items():
        path = Path(_text(paths[name], f"output_contract.paths[{name!r}]")).resolve()
        if path != artifact_root / filename:
            raise MainManifestError(
                f"output path {name!r} must be exactly {artifact_root / filename}")
        if hashes[name] is not None:
            raise MainManifestError("pre-formal output SHA-256 values must remain null")
        resolved[name] = path
    return artifact_root, registry_root, resolved


def _validate_restart(
    value: Any,
    *,
    artifact_root: Path,
    current_run_id: str,
    verify_files: bool,
) -> dict[str, Any]:
    restart = _exact_keys(value, RESTART_FIELDS, "restart")
    mode = restart["mode"]
    if mode == RESTART_MODE_INITIAL:
        if restart["predecessor"] is not None:
            raise MainManifestError("an initial manifest cannot name a predecessor")
        return {"mode": mode, "predecessor": None}
    if mode != RESTART_MODE_ENVIRONMENTAL_SUCCESSOR:
        raise MainManifestError("restart.mode is not supported")

    predecessor = _exact_keys(
        restart["predecessor"], PREDECESSOR_FIELDS, "restart.predecessor")
    predecessor_run_id = _text(predecessor["run_id"], "restart.predecessor.run_id")
    if predecessor_run_id == current_run_id:
        raise MainManifestError("an environmental successor must use a fresh run ID")
    predecessor_manifest_sha = _sha256(
        predecessor["manifest_canonical_sha256"],
        "restart.predecessor.manifest_canonical_sha256",
    )
    predecessor_identity_sha = _sha256(
        predecessor["manifest_identity_sha256"],
        "restart.predecessor.manifest_identity_sha256",
    )
    predecessor_authorization_id = _text(
        predecessor["authorization_id"], "restart.predecessor.authorization_id")
    predecessor_authorization_sha = _sha256(
        predecessor["authorization_canonical_sha256"],
        "restart.predecessor.authorization_canonical_sha256",
    )
    predecessor_authorization_raw_sha = _sha256(
        predecessor["authorization_raw_sha256"],
        "restart.predecessor.authorization_raw_sha256",
    )
    predecessor_signature_raw_sha = _sha256(
        predecessor["authorization_signature_raw_sha256"],
        "restart.predecessor.authorization_signature_raw_sha256",
    )
    predecessor_root = Path(_text(
        predecessor["artifact_root"], "restart.predecessor.artifact_root")).resolve()
    if not Path(str(predecessor["artifact_root"])).is_absolute():
        raise MainManifestError("restart.predecessor.artifact_root must be absolute")
    if predecessor_root == artifact_root:
        raise MainManifestError("an environmental successor must use a new artifact root")
    for child, parent in ((predecessor_root, artifact_root), (artifact_root, predecessor_root)):
        try:
            child.relative_to(parent)
        except ValueError:
            continue
        raise MainManifestError(
            "successor and predecessor artifact roots must not contain one another")

    void_path = Path(_text(
        predecessor["void_record_path"], "restart.predecessor.void_record_path"))
    ledger_path = Path(_text(
        predecessor["usage_ledger_path"], "restart.predecessor.usage_ledger_path"))
    if not void_path.is_absolute() or not ledger_path.is_absolute():
        raise MainManifestError("restart predecessor evidence paths must be absolute")
    void_path = void_path.resolve()
    ledger_path = ledger_path.resolve()
    if ledger_path != predecessor_root / OUTPUT_FILENAMES["usage_ledger"]:
        raise MainManifestError(
            "restart predecessor usage ledger is outside its exact artifact root")
    void_sha = _sha256(
        predecessor["void_record_raw_sha256"],
        "restart.predecessor.void_record_raw_sha256",
    )
    ledger_sha = _sha256(
        predecessor["usage_ledger_raw_sha256"],
        "restart.predecessor.usage_ledger_raw_sha256",
    )
    if verify_files:
        for path, expected, label in (
            (void_path, void_sha, "environmental predecessor void record"),
            (ledger_path, ledger_sha, "environmental predecessor usage ledger"),
        ):
            if not path.is_file() or _raw_sha256(path) != expected:
                raise MainManifestError(f"{label} is missing or its raw SHA-256 drifted")
    return {
        "mode": mode,
        "predecessor": {
            "run_id": predecessor_run_id,
            "manifest_canonical_sha256": predecessor_manifest_sha,
            "manifest_identity_sha256": predecessor_identity_sha,
            "authorization_id": predecessor_authorization_id,
            "authorization_canonical_sha256": predecessor_authorization_sha,
            "authorization_raw_sha256": predecessor_authorization_raw_sha,
            "authorization_signature_raw_sha256": predecessor_signature_raw_sha,
            "artifact_root": predecessor_root,
            "void_record_path": void_path,
            "void_record_raw_sha256": void_sha,
            "usage_ledger_path": ledger_path,
            "usage_ledger_raw_sha256": ledger_sha,
        },
    }


def validate_main_manifest(
    manifest: Mapping[str, Any], *, project_root: str | Path = ".",
    verify_files: bool = True, verify_runtime: bool = True,
) -> dict[str, Any]:
    """Validate one non-authorizing, fresh Phase 3 main launch identity."""
    _exact_keys(manifest, MANIFEST_FIELDS, "manifest")
    if manifest["schema_version"] != MANIFEST_SCHEMA or manifest["stage"] != "main":
        raise MainManifestError("unsupported Phase 3 main manifest schema or stage")
    if manifest["execution_authorized"] is not False:
        raise MainManifestError("a main manifest can never authorize execution")
    recorded_at = _utc(manifest["recorded_at_utc"], "recorded_at_utc")
    commit = _text(manifest["source_commit"], "source_commit")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise MainManifestError("source_commit must be a lowercase 40-character Git commit")

    root = Path(project_root).resolve()
    toolchain = _exact_keys(manifest["toolchain"], TOOLCHAIN_FIELDS, "toolchain")
    implementation = _text(toolchain["python_implementation"], "toolchain.python_implementation")
    version = _text(toolchain["python_version"], "toolchain.python_version")
    lock_text = _text(toolchain["dependency_lock_path"], "toolchain.dependency_lock_path")
    lock_path = _input_path(lock_text, root)
    lock_sha = _sha256(
        toolchain["dependency_lock_raw_sha256"], "toolchain.dependency_lock_raw_sha256")
    linker = _text(
        toolchain["linker_version_or_not_applicable"],
        "toolchain.linker_version_or_not_applicable",
    )
    if linker != "not_applicable":
        raise MainManifestError("the Python-only main run must record linker as not_applicable")
    if verify_files:
        if not lock_path.is_file() or _raw_sha256(lock_path) != lock_sha:
            raise MainManifestError("dependency lock is missing or its raw SHA-256 drifted")
    if verify_runtime and (
        implementation != platform.python_implementation()
        or version != platform.python_version()
    ):
        raise MainManifestError("active Python runtime differs from the manifest toolchain")

    seeds = _exact_keys(manifest["seeds"], SEED_FIELDS, "seeds")
    for name, seed in seeds.items():
        _non_negative_int(seed, f"seeds.{name}")
    if seeds["analysis_bootstrap_seed"] != 20260829:
        raise MainManifestError("analysis bootstrap seed differs from the frozen pin")
    if seeds["subsample_seed"] != 20260818:
        raise MainManifestError("subsample seed differs from the frozen protocol")

    input_paths = _validate_input_bindings(
        manifest["input_bindings"], project_root=root, verify_files=verify_files)
    capacity_authorization_path = input_paths["capacity_execution_authorization"]
    expected_capacity_signature_path = capacity_authorization_path.with_name(
        f"{capacity_authorization_path.name}.sig"
    )
    if (
        input_paths["capacity_execution_authorization_signature"]
        != expected_capacity_signature_path
    ):
        raise MainManifestError(
            "capacity authorization signature input must be the exact detached sidecar"
        )
    if verify_files:
        try:
            phase3_main_runtime_policies.load_and_validate_price_change_policy(
                input_paths["price_change_policy"])
            phase3_main_runtime_policies.load_and_validate_reviewer_usage_policy(
                input_paths["reviewer_usage_policy"])
        except phase3_main_runtime_policies.MainRuntimePolicyError as exc:
            raise MainManifestError(f"main runtime policy validation failed: {exc}") from exc
    inventory = _exact_keys(manifest["inventory"], INVENTORY_FIELDS, "inventory")
    expected_inventory = {
        "question_count": EXPECTED_MAIN_QUESTION_COUNT,
        "transcript_count": EXPECTED_MAIN_TRANSCRIPT_COUNT,
        "judgment_count": EXPECTED_MAIN_JUDGMENT_COUNT,
        "cell_count": EXPECTED_MAIN_CELL_COUNT,
        "canonical_sha256": EXPECTED_MAIN_INVENTORY_SHA256,
    }
    if dict(inventory) != expected_inventory:
        raise MainManifestError("manifest inventory differs from the exact confirmed main plan")

    artifact_root, registry_root, output_paths = _validate_output_contract(
        manifest["output_contract"])
    restart = _validate_restart(
        manifest["restart"],
        artifact_root=artifact_root,
        current_run_id=str(manifest["run_id"]),
        verify_files=verify_files,
    )
    runtime = _exact_keys(manifest["runtime"], RUNTIME_FIELDS, "runtime")
    if runtime["provider"] != "Together":
        raise MainManifestError("main provider must be Together")
    _sha256(
        runtime["provider_account_identity_sha256"],
        "runtime.provider_account_identity_sha256",
    )
    if tuple(runtime["model_ids"]) != CONFIRMED_MAIN_JUDGES:
        raise MainManifestError("runtime model IDs differ from the confirmed endpoint roster")
    if _non_negative_int(
        runtime["provider_worker_concurrency"],
        "runtime.provider_worker_concurrency",
    ) != 1:
        raise MainManifestError("provider worker concurrency must be exactly 1")
    if runtime["reviewer_concurrency"] != 12:
        raise MainManifestError("reviewer concurrency must be exactly 12")
    for field in ("reviewer_cli_binary", "reviewer_model", "reviewer_reasoning_effort"):
        _text(runtime[field], f"runtime.{field}")
    if runtime["gpu_ordinal"] != "not_applicable":
        raise MainManifestError("GPU ordinal must be not_applicable")

    spend = _exact_keys(manifest["spend"], SPEND_FIELDS, "spend")
    prior = _money(spend["prior_reconciled_usd"], "spend.prior_reconciled_usd")
    forecast = _money(spend["forecast_main_usd"], "spend.forecast_main_usd")
    cap = _money(spend["stage_cap_usd"], "spend.stage_cap_usd")
    if cap <= 0 or prior + forecast > cap:
        raise MainManifestError("certified main forecast must fit inside a positive stage cap")

    harness = _exact_keys(manifest["harness_check"], HARNESS_FIELDS, "harness_check")
    if harness["receipt_input_name"] != "harness_receipt":
        raise MainManifestError("harness receipt must use the bound harness_receipt input")
    if harness["seed_name"] != "harness_seed" or harness["status"] != "bit_identical_pass":
        raise MainManifestError("main harness must carry the frozen seed and bit-identical pass")
    first = _sha256(
        harness["first_output_store_sha256"],
        "harness_check.first_output_store_sha256",
    )
    rerun = _sha256(
        harness["rerun_output_store_sha256"],
        "harness_check.rerun_output_store_sha256",
    )
    if first != rerun:
        raise MainManifestError("one-seed harness output stores are not bit-identical")

    identity_sha = _sha256(manifest["manifest_identity_sha256"], "manifest_identity_sha256")
    observed_identity = manifest_identity_sha256(manifest)
    if identity_sha != observed_identity or manifest["run_id"] != expected_run_id(manifest):
        raise MainManifestError("run ID or manifest identity digest does not match its contents")
    return {
        "run_id": manifest["run_id"],
        "recorded_at_utc": recorded_at,
        "manifest_canonical_sha256": manifest_canonical_sha256(manifest),
        "input_paths": input_paths,
        "artifact_root": artifact_root,
        "identity_registry_root": registry_root,
        "output_paths": output_paths,
        "restart": restart,
        "stage_cap_usd": str(cap),
        "prior_reconciled_usd": str(prior),
        "forecast_main_usd": str(forecast),
    }


def validate_main_authorization(
    authorization: Mapping[str, Any], manifest: Mapping[str, Any], *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Require exact, unexpired owner authority for this manifest and no other."""
    _exact_keys(authorization, AUTHORIZATION_FIELDS, "authorization")
    if authorization["schema_version"] != AUTHORIZATION_SCHEMA:
        raise MainManifestError("unsupported Phase 3 main authorization schema")
    if authorization["stage"] != "main":
        raise MainManifestError("authorization stage must be main")
    for field in ("authorization_id", "approver", "exact_text"):
        _text(authorization[field], f"authorization.{field}")
    if authorization["approver"] != "Jack Maiorino":
        raise MainManifestError("main authorization approver must be Jack Maiorino")
    approved_at = _utc(authorization["approved_at_utc"], "authorization.approved_at_utc")
    valid_until = _utc(authorization["valid_until_utc"], "authorization.valid_until_utc")
    manifest_recorded_at = _utc(manifest["recorded_at_utc"], "manifest.recorded_at_utc")
    if approved_at < manifest_recorded_at:
        raise MainManifestError("main authorization predates the exact manifest")
    current = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if valid_until <= approved_at or current < approved_at or current > valid_until:
        raise MainManifestError("main authorization is not active at the validation time")
    if not all(authorization[field] is True for field in (
        "execution_authorized", "provider_calls_authorized", "main_run_spend_authorized",
        "no_resume",
    )):
        raise MainManifestError("main authorization must explicitly authorize and prohibit resume")

    expected_manifest_sha = manifest_canonical_sha256(manifest)
    if authorization["run_id"] != manifest["run_id"]:
        raise MainManifestError("authorization run ID differs from the manifest")
    if authorization["manifest_canonical_sha256"] != expected_manifest_sha:
        raise MainManifestError("authorization does not bind the exact manifest digest")
    if authorization["manifest_identity_sha256"] != manifest["manifest_identity_sha256"]:
        raise MainManifestError("authorization does not bind the manifest execution identity")
    runtime = manifest["runtime"]
    if authorization["provider"] != runtime["provider"]:
        raise MainManifestError("authorization provider differs from the manifest")
    authorization_account = _sha256(
        authorization["provider_account_identity_sha256"],
        "authorization.provider_account_identity_sha256",
    )
    manifest_account = _sha256(
        runtime["provider_account_identity_sha256"],
        "runtime.provider_account_identity_sha256",
    )
    if authorization_account != manifest_account:
        raise MainManifestError(
            "authorization provider account identity differs from the manifest")
    if authorization["exact_model_ids"] != runtime["model_ids"]:
        raise MainManifestError("authorization endpoint roster differs from the manifest")
    for field in ("reviewer_model", "reviewer_reasoning_effort"):
        if authorization[field] != runtime[field]:
            raise MainManifestError(
                f"authorization {field} differs from the manifest reviewer runtime")
    reviewer_concurrency = authorization["reviewer_concurrency"]
    if (
        isinstance(reviewer_concurrency, bool)
        or not isinstance(reviewer_concurrency, int)
        or reviewer_concurrency != runtime["reviewer_concurrency"]
    ):
        raise MainManifestError(
            "authorization reviewer_concurrency differs from the manifest reviewer runtime")
    cap = _money(authorization["stage_cap_usd"], "authorization.stage_cap_usd")
    if authorization["stage_cap_usd"] != manifest["spend"]["stage_cap_usd"]:
        raise MainManifestError("authorization cap must exactly equal the manifest stage cap")
    inputs = manifest["input_bindings"]
    if authorization["price_snapshot_sha256"] != inputs["price_snapshot"]["sha256"]:
        raise MainManifestError("authorization price binding differs from the manifest")
    if authorization["price_change_policy_sha256"] != inputs[
        "price_change_policy"
    ]["sha256"]:
        raise MainManifestError(
            "authorization price-change policy binding differs from the manifest")
    if authorization["prior_reconciliation_sha256"] != inputs["billing_reconciliation"]["sha256"]:
        raise MainManifestError("authorization reconciliation binding differs from the manifest")
    if authorization["forecast_sha256"] != inputs["certified_cost_forecast"]["sha256"]:
        raise MainManifestError("authorization forecast binding differs from the manifest")
    if authorization["reviewer_usage_policy_sha256"] != inputs[
        "reviewer_usage_policy"
    ]["sha256"]:
        raise MainManifestError(
            "authorization reviewer-usage policy binding differs from the manifest")
    maximum_reviewer_dispatches = _non_negative_int(
        authorization["maximum_reviewer_dispatches"],
        "authorization.maximum_reviewer_dispatches",
    )
    if maximum_reviewer_dispatches != (
        phase3_main_runtime_policies.MAXIMUM_REVIEWER_DISPATCHES
    ):
        raise MainManifestError(
            "authorization reviewer dispatch ceiling differs from the frozen policy")
    if authorization["harness_execution_count"] != 2:
        raise MainManifestError("authorization must bind exactly two isolated harness executions")
    if _non_negative_int(
        authorization["formal_main_attempt_count"],
        "authorization.formal_main_attempt_count",
    ) != 1:
        raise MainManifestError("authorization must bind exactly one formal main attempt")
    if authorization["exact_text"] != expected_authorization_text(manifest):
        raise MainManifestError("authorization does not preserve the exact owner approval text")
    return {
        "authorization_id": authorization["authorization_id"],
        "approved_cap_usd": str(cap),
        "valid_until_utc": valid_until,
    }


__all__ = [
    "AUTHORIZATION_FIELDS",
    "AUTHORIZATION_SCHEMA",
    "MANIFEST_FIELDS",
    "MANIFEST_SCHEMA",
    "MainManifestError",
    "OUTPUT_FILENAMES",
    "RESTART_MODE_ENVIRONMENTAL_SUCCESSOR",
    "RESTART_MODE_INITIAL",
    "REQUIRED_INPUT_BINDINGS",
    "expected_run_id",
    "expected_authorization_text",
    "manifest_canonical_sha256",
    "manifest_identity_sha256",
    "validate_main_authorization",
    "validate_main_manifest",
]
