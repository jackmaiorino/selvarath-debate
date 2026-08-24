"""Live adapter for the authorized Phase 3 v3 successor canary.

This module has no main-run entry point. It executes exactly two isolated one-cell harness
runs and, after their result stores hash identically, the 48-transcript, 768-judgment,
192-capability successor canary. Every Together call shares one durable incremental-cap ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import subprocess
import sys
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from rejudge import (
    api_client,
    phase3_orchestrator_support,
    phase3_plan,
    phase3_runner,
    phase3_v3_inputs,
    phase3_v3_run_manifest,
    records,
)
from rejudge.phase2_call_cache import CallCache
from rejudge.phase2_caching_client import CachingClient
from rejudge.phase2_canary_execute import CellContext, GenerationForbiddenError
from rejudge.phase2_canary_live import (
    RoleLimitResolvingClient,
    _PauseModeReviewer,
    commit_decisions_into,
    export_reviewer_worklist,
    local_path,
)
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_canary_runner import run_canary
from rejudge.phase2_dual_gate import DualGateDecisionStore
from rejudge.phase2_execution import canonical_sha256
from scripts import phase3_canary_closeout_v2, phase3_polarity_verify, review_daemon
from scripts.phase3_preseed_transcripts import preseed_canary


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_RELATIVE_PATH = "rejudge/phase3_protocol_v3_r2.json"
PROTOCOL_PIN_RELATIVE_PATH = "rejudge/phase3_protocol_v3_pin_r2.json"
TOKENIZER_MANIFEST_RELATIVE_PATH = (
    "rejudge/phase3_v3_exact_tokenizer_manifest_r3_2026-08-23.json")
PRICE_SNAPSHOT_RELATIVE_PATH = "rejudge/phase3_v3_price_snapshot_r3_2026-08-24.json"
ROLE_LIMITS_RELATIVE_PATH = "rejudge/phase3_v3_role_limits_2026-08-24.json"
EXECUTION_BINDING_RELATIVE_PATH = "rejudge/phase3_v3_execution_binding_2026-08-24.json"
PROMPT_BUNDLE_RELATIVE_PATH = "rejudge/phase2_prompt_bundle.json"
CHECKER_CONFIG_RELATIVE_PATH = "rejudge/phase2_checker_frozen_config_2026-07-23.json"
CHECKER_DESIGN_RELATIVE_PATH = "rejudge/phase2_checker_validation_design_2026-07-18.json"
REVIEWER_PROMPT_RELATIVE_PATH = "rejudge/phase2_reviewer_prompt_2026-07-23.json"
TRANSCRIPT_REPORT_RELATIVE_PATH = "rejudge/phase3_transcript_verification_2026-08-18.json"
DEPENDENCY_LOCK_RELATIVE_PATH = "uv.lock"

AUTHORIZATION_SCHEMA = "phase3_v3_canary_authorization_v1"
BINDING_SCHEMA = "phase3_v3_execution_binding_v1"
ROLE_LIMITS_SCHEMA = "phase3_v3_role_limits_v1"
EXPECTED_TRANSCRIPT_ROWS = 48
EXPECTED_JUDGMENT_ROWS = 768
EXPECTED_CAPABILITY_ROWS = 192
EXPECTED_FRESH_GATE_ROWS = EXPECTED_JUDGMENT_ROWS + EXPECTED_CAPABILITY_ROWS
EXPECTED_TOTAL_ROWS = EXPECTED_TRANSCRIPT_ROWS + EXPECTED_FRESH_GATE_ROWS
EXPECTED_HARNESS_EXECUTIONS = 2
AUTHORIZED_INCREMENTAL_CAP_USD = 60.0
EXPECTED_REASONING_MODELS = frozenset({
    "google/gemma-4-31B-it",
    "Qwen/Qwen3.5-397B-A17B",
})
NON_MANIFEST_OUTPUT_PATH_KEYS = frozenset({
    "archive_dir", "run_lock", "harness_verified_manifest", "final_manifest",
})

REQUIRED_INPUT_PATHS = frozenset({
    PROTOCOL_RELATIVE_PATH,
    PROTOCOL_PIN_RELATIVE_PATH,
    TOKENIZER_MANIFEST_RELATIVE_PATH,
    PRICE_SNAPSHOT_RELATIVE_PATH,
    ROLE_LIMITS_RELATIVE_PATH,
    EXECUTION_BINDING_RELATIVE_PATH,
    PROMPT_BUNDLE_RELATIVE_PATH,
    CHECKER_CONFIG_RELATIVE_PATH,
    CHECKER_DESIGN_RELATIVE_PATH,
    REVIEWER_PROMPT_RELATIVE_PATH,
    TRANSCRIPT_REPORT_RELATIVE_PATH,
})

EXECUTION_CODE_PATHS = (
    "rejudge/phase3_v3_live.py",
    "rejudge/phase3_v3_inputs.py",
    "rejudge/phase3_v3_materialization.py",
    "rejudge/phase3_v3_run_manifest.py",
    "rejudge/phase3_orchestrator_support.py",
    "rejudge/phase3_plan.py",
    "rejudge/phase3_runner.py",
    "rejudge/phase2_canary_runner.py",
    "rejudge/phase2_canary_execute.py",
    "rejudge/phase2_canary_cells.py",
    "rejudge/phase2_canary_compose.py",
    "rejudge/phase2_canary_gate.py",
    "rejudge/phase2_dual_gate.py",
    "rejudge/phase2_query_gate.py",
    "rejudge/phase2_canary_order.py",
    "rejudge/phase2_call_cache.py",
    "rejudge/phase2_caching_client.py",
    "rejudge/phase2_canary_live.py",
    "rejudge/phase2_execution.py",
    "rejudge/judge_loop.py",
    "rejudge/api_client.py",
    "rejudge/records.py",
    "rejudge/run_accounting.py",
    "scripts/phase3_preseed_transcripts.py",
    "scripts/phase3_polarity_verify.py",
    "scripts/phase3_canary_closeout_v2.py",
    "scripts/codex_reviewer_batch.py",
    "scripts/review_daemon.py",
)


class Phase3V3LiveError(RuntimeError, ValueError):
    """A v3 launch or closeout invariant failed."""


def _load_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase3V3LiveError(f"could not read JSON {path}: {exc}") from exc


def _raw_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_jsonl(path: str | Path, row: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(dict(row), sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    with target.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_exclusive(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(value), indent=1, sort_keys=True, ensure_ascii=True) + "\n"
    if target.exists():
        existing = _load_json(target)
        if existing != value:
            raise Phase3V3LiveError(f"refusing to overwrite different artifact: {target}")
        return
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


class RunLease(AbstractContextManager):
    """One process owns the successor archive while it can dispatch or commit work."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write(b"0")
            self._handle.flush()
            os.fsync(self._handle.fileno())
        self._handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise Phase3V3LiveError(
                f"another process holds the v3 run lease {self.path}") from exc
        return self

    def __exit__(self, *exc_info):
        if self._handle is not None:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
        return False


def _verify_execution_code_commit(manifest: Mapping[str, Any], root: Path) -> None:
    commit = str(manifest["git_commit"])
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=root, check=True,
            capture_output=True)
        diff = subprocess.run(
            ["git", "diff", "--quiet", commit, "--", *EXECUTION_CODE_PATHS], cwd=root)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Phase3V3LiveError(f"could not verify bound execution commit: {exc}") from exc
    if diff.returncode != 0:
        raise Phase3V3LiveError(
            "execution code differs from the git commit bound by the run manifest")


def _validate_all_input_hashes(manifest: Mapping[str, Any], root: Path) -> None:
    inputs = manifest["input_sha256s"]
    missing = sorted(REQUIRED_INPUT_PATHS - set(inputs))
    if missing:
        raise Phase3V3LiveError(f"run manifest is missing required input bindings: {missing}")
    for relative, expected in inputs.items():
        path = root / relative
        payload = _load_json(path)
        observed = canonical_sha256(payload)
        if observed != expected:
            raise Phase3V3LiveError(
                f"input hash drift for {relative}: observed {observed}, expected {expected}")


def _validate_role_limits(role_limits: Mapping[str, Any], protocol: Mapping[str, Any]) -> None:
    if role_limits.get("schema_version") != ROLE_LIMITS_SCHEMA:
        raise Phase3V3LiveError("unsupported v3 role-limits schema")
    if role_limits.get("execution_authorized") is not False:
        raise Phase3V3LiveError("role limits cannot authorize execution")
    if role_limits.get("protocol_id") != protocol.get("protocol_id"):
        raise Phase3V3LiveError("role limits bind a different protocol")
    roster = set(protocol["roster"]["judges_final"])
    limits = role_limits.get("model_role_limits")
    if not isinstance(limits, Mapping) or set(limits) != roster:
        raise Phase3V3LiveError("role-limits model set differs from the final roster")
    base = role_limits.get("base_role_max_tokens")
    if not isinstance(base, Mapping):
        raise Phase3V3LiveError("role limits have no base_role_max_tokens")
    reasoning = set((role_limits.get("reasoning_models") or {}).get("model_ids") or ())
    floor = int((role_limits.get("reasoning_models") or {}).get("floor_max_tokens") or 0)
    if reasoning != EXPECTED_REASONING_MODELS or floor != 4096:
        raise Phase3V3LiveError("reasoning-model roster or 4096-token floor drifted")
    required_roles: dict[str, set[str]] = {
        model: {"judge_query", "judge_verdict", "capability_qa"} for model in roster}
    required_roles[str(protocol["roster"]["query_checker"])].add("query_checker")
    required_roles[str(protocol["roster"]["oracle"])].add("oracle")
    for model, roles in required_roles.items():
        model_limits = limits[model]
        if not roles <= set(model_limits):
            raise Phase3V3LiveError(
                f"role limits for {model} omit {sorted(roles - set(model_limits))}")
        for role, entry in model_limits.items():
            expected_base = int(base[role])
            expected_effective = max(expected_base, floor) if model in reasoning else expected_base
            if (int(entry.get("base_role_max_tokens", -1)) != expected_base
                    or int(entry.get("effective_request_max_tokens", -1)) != expected_effective):
                raise Phase3V3LiveError(f"role limit drift for ({model}, {role})")
    contexts = role_limits.get("context_ceilings")
    if not isinstance(contexts, Mapping) or set(contexts) != roster:
        raise Phase3V3LiveError("context ceilings differ from the final roster")
    request = role_limits.get("request_settings") or {}
    if request.get("base_fields") != ["model", "messages", "temperature", "max_tokens", "seed"]:
        raise Phase3V3LiveError("base provider request fields drifted")
    if set(request.get("streaming_pinned_models") or {}) != EXPECTED_REASONING_MODELS:
        raise Phase3V3LiveError("streaming model pins differ from the reasoning-model roster")
    if request.get("per_model_extra_fields") != {}:
        raise Phase3V3LiveError("unapproved per-model provider request fields are present")
    transport = request.get("transport") or {}
    if set(transport.get("http_timeout") or {}) != {"connect", "read", "write", "pool"}:
        raise Phase3V3LiveError("role limits have incomplete HTTP timeout pins")
    if (int(transport.get("sdk_internal_max_retries", -1)) != 0
            or int(transport.get("ledger_max_retries", -1)) != 2
            or int(transport.get("ledger_max_attempts", -1)) != 3):
        raise Phase3V3LiveError("role limits have unexpected retry pins")
    if "require exact equality" not in str(request.get("returned_model_policy")):
        raise Phase3V3LiveError("role limits do not require exact returned-model equality")


def _validate_contexts_against_catalog(
    role_limits: Mapping[str, Any], price_snapshot: Mapping[str, Any], root: Path,
) -> None:
    catalog_path = root / str(price_snapshot["raw_catalog"]["path"])
    catalog = _load_json(catalog_path)
    by_id = {str(entry.get("id")): entry for entry in catalog if isinstance(entry, Mapping)}
    for model, limit in role_limits["context_ceilings"].items():
        entry = by_id.get(model)
        if entry is None:
            raise Phase3V3LiveError(f"role-limit model is absent from the bound catalog: {model}")
        if int(entry.get("context_length", -1)) != int(limit["context_length_tokens"]):
            raise Phase3V3LiveError(
                f"context ceiling for {model} differs from the bound provider catalog")


def _binding_paths(binding: Mapping[str, Any]) -> dict[str, Path]:
    return {name: local_path(value) for name, value in binding["paths"].items()
            if name != "archive_dir"}


def _validate_execution_binding(
    binding: Mapping[str, Any], manifest: Mapping[str, Any], protocol: Mapping[str, Any],
) -> None:
    if binding.get("schema_version") != BINDING_SCHEMA:
        raise Phase3V3LiveError("unsupported v3 execution-binding schema")
    if binding.get("execution_authorized") is not False:
        raise Phase3V3LiveError("execution binding cannot authorize execution")
    if binding.get("main_run_spend_authorized") is not False:
        raise Phase3V3LiveError("execution binding must prohibit main spend")
    paths = binding.get("paths")
    if not isinstance(paths, Mapping) or paths.get("archive_dir") is None:
        raise Phase3V3LiveError("execution binding has no archive paths")
    file_paths = [str(value).replace("\\", "/") for key, value in paths.items()
                  if key not in NON_MANIFEST_OUTPUT_PATH_KEYS]
    if len(file_paths) != len(set(file_paths)):
        raise Phase3V3LiveError("execution-binding output paths contain duplicates")
    if set(file_paths) != set(manifest["planned_output_paths"]):
        raise Phase3V3LiveError(
            "execution-binding files differ from manifest planned_output_paths")
    expected_path_fields = {
        "archive_dir", "harness_1_results", "harness_1_cache", "harness_2_results",
        "harness_2_cache", "formal_results", "formal_decisions", "formal_cache",
        "usage_ledger", "usage_state", "formal_error_log", "run_log",
        "reviewer_worklist", "reviewer_index", "harness_verified_manifest",
        "final_manifest", "final_report", "run_lock",
    }
    if set(paths) != expected_path_fields:
        raise Phase3V3LiveError("execution-binding path fields drifted")
    archive = str(paths["archive_dir"]).replace("\\", "/").rstrip("/") + "/"
    if any(not path.startswith(archive) for path in file_paths):
        raise Phase3V3LiveError("every successor output must stay under its archive directory")
    inventory = binding.get("inventory") or {}
    if inventory != {
        "canary_transcript_rows": EXPECTED_TRANSCRIPT_ROWS,
        "fresh_judgment_rows": EXPECTED_JUDGMENT_ROWS,
        "fresh_capability_rows": EXPECTED_CAPABILITY_ROWS,
        "fresh_gate_rows": EXPECTED_FRESH_GATE_ROWS,
        "main_rows": 0,
    }:
        raise Phase3V3LiveError("execution-binding canary inventory drifted")
    harness = binding.get("harness") or {}
    if (harness.get("seed_name") != manifest["harness_check"]["seed_name"]
            or harness.get("seed") != manifest["seeds"][harness.get("seed_name")]
            or harness.get("execution_count") != EXPECTED_HARNESS_EXECUTIONS
            or not isinstance(harness.get("selected_capability_cell_key"), str)):
        raise Phase3V3LiveError("execution-binding harness selection drifted")
    transcript = binding.get("canary_transcript_bundle") or {}
    report = binding.get("transcript_verification_report") or {}
    if report.get("tracked_path") != TRANSCRIPT_REPORT_RELATIVE_PATH:
        raise Phase3V3LiveError("execution binding names the wrong transcript report")
    if report.get("canonical_sha256") != manifest["input_sha256s"][
            TRANSCRIPT_REPORT_RELATIVE_PATH]:
        raise Phase3V3LiveError("execution binding transcript-report hash drifted")
    if not isinstance(transcript.get("path"), str) or not isinstance(
            transcript.get("canonical_sha256"), str):
        raise Phase3V3LiveError("execution binding has no frozen canary transcript bundle")
    if list(protocol["roster"]["judges_final"]) != list(manifest["final_roster"]):
        raise Phase3V3LiveError("execution binding loaded against a different roster")
    formal = binding.get("formal_execution") or {}
    if formal != {
        "provider_max_workers": 1,
        "pending_payload_limit": 64,
        "reviewer_model": "gpt-5.6-sol",
        "reviewer_reasoning_effort": "high",
        "reviewer_concurrency": 12,
        "transcript_generation_forbidden": True,
        "shared_incremental_cap_ledger": True,
    }:
        raise Phase3V3LiveError("formal execution settings drifted")
    expected_state = api_client.usage_ledger_state_path(local_path(paths["usage_ledger"]))
    if local_path(paths["usage_state"]).resolve() != expected_state.resolve():
        raise Phase3V3LiveError("execution binding names the wrong usage-ledger state path")


def _validate_runtime_toolchain(
    manifest: Mapping[str, Any], binding: Mapping[str, Any], root: Path,
) -> None:
    observed_python = platform.python_version()
    if observed_python != manifest.get("python_version"):
        raise Phase3V3LiveError(
            f"Python version drift: observed {observed_python}, expected "
            f"{manifest.get('python_version')}")
    lock_path = root / DEPENDENCY_LOCK_RELATIVE_PATH
    observed_lock = phase3_v3_inputs.sha256_file(lock_path)
    if observed_lock != manifest.get("dependency_lock_sha256"):
        raise Phase3V3LiveError("dependency-lock hash drifted from the run manifest")

    toolchain = binding.get("toolchain") or {}
    if toolchain.get("linker_version_or_not_applicable") != manifest.get(
            "linker_version_or_not_applicable"):
        raise Phase3V3LiveError("linker toolchain binding drifted from the run manifest")
    sdk = toolchain.get("provider_sdk") or {}
    if sdk.get("package") != "together" or not isinstance(sdk.get("version"), str):
        raise Phase3V3LiveError("execution binding has no exact Together SDK version")
    try:
        installed_sdk = importlib.metadata.version("together")
    except importlib.metadata.PackageNotFoundError as exc:
        raise Phase3V3LiveError("Together SDK is not installed") from exc
    if installed_sdk != sdk["version"]:
        raise Phase3V3LiveError(
            f"Together SDK drift: observed {installed_sdk}, expected {sdk['version']}")

    reviewer = toolchain.get("reviewer_cli") or {}
    binary = reviewer.get("binary")
    expected_version = reviewer.get("version_output")
    if not isinstance(binary, str) or not isinstance(expected_version, str):
        raise Phase3V3LiveError("execution binding has no exact reviewer CLI version")
    try:
        completed = subprocess.run(
            [binary, "--version"], cwd=root, check=True, capture_output=True, text=True,
            timeout=30)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise Phase3V3LiveError(f"could not verify reviewer CLI toolchain: {exc}") from exc
    observed_version = completed.stdout.strip()
    if observed_version != expected_version:
        raise Phase3V3LiveError(
            f"reviewer CLI drift: observed {observed_version!r}, expected "
            f"{expected_version!r}")


def _authorization_recorded_at(authorization: Mapping[str, Any]) -> datetime:
    raw = authorization.get("recorded_at_utc")
    if not isinstance(raw, str):
        raise Phase3V3LiveError("authorization has no recorded_at_utc timestamp")
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Phase3V3LiveError("authorization recorded_at_utc is invalid") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise Phase3V3LiveError("authorization recorded_at_utc must be timezone-aware")
    return observed


def load_run_context(
    manifest_path: str | Path,
    authorization_path: str | Path,
    *,
    project_root: str | Path = ".",
    verify_git: bool = True,
    require_harness: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    manifest_path = Path(manifest_path).resolve()
    manifest = _load_json(manifest_path)
    _validate_all_input_hashes(manifest, root)
    protocol = phase3_plan.load_protocol(root / PROTOCOL_RELATIVE_PATH)
    pin = _load_json(root / PROTOCOL_PIN_RELATIVE_PATH)
    tokenizer = _load_json(root / TOKENIZER_MANIFEST_RELATIVE_PATH)
    prices = _load_json(root / PRICE_SNAPSHOT_RELATIVE_PATH)
    phase3_v3_run_manifest.validate_run_manifest(
        manifest, protocol=protocol, protocol_pin=pin, tokenizer_manifest=tokenizer,
        price_snapshot=prices, project_root=root, verify_external_files=True)
    if verify_git:
        _verify_execution_code_commit(manifest, root)
    role_limits = _load_json(root / ROLE_LIMITS_RELATIVE_PATH)
    _validate_role_limits(role_limits, protocol)
    _validate_contexts_against_catalog(role_limits, prices, root)
    binding = _load_json(root / EXECUTION_BINDING_RELATIVE_PATH)
    _validate_execution_binding(binding, manifest, protocol)
    _validate_runtime_toolchain(manifest, binding, root)
    authorization = validate_authorization(
        _load_json(authorization_path), manifest, manifest_path=manifest_path,
        protocol=protocol)
    phase3_v3_inputs.validate_price_snapshot(
        prices, protocol=protocol, as_of=_authorization_recorded_at(authorization),
        project_root=root, verify_catalog=True)
    context = {
        "root": root,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "authorization": authorization,
        "protocol": protocol,
        "tokenizer_manifest": tokenizer,
        "price_snapshot": prices,
        "role_limits": role_limits,
        "binding": binding,
        "paths": _binding_paths(binding),
    }
    validate_ledger(context)
    if require_harness:
        context["harness_manifest"] = load_harness_manifest(context)
    return context


def validate_authorization(
    authorization: Mapping[str, Any], manifest: Mapping[str, Any], *,
    manifest_path: Path, protocol: Mapping[str, Any],
) -> dict[str, Any]:
    _authorization_recorded_at(authorization)
    if authorization.get("schema_version") != AUTHORIZATION_SCHEMA:
        raise Phase3V3LiveError("unsupported successor-canary authorization schema")
    if authorization.get("execution_authorized") is not True:
        raise Phase3V3LiveError("successor canary has no execution authorization")
    if (authorization.get("main_run_spend_authorized") is not False
            or (authorization.get("scope") or {}).get("main_run_spend_authorized") is not False):
        raise Phase3V3LiveError("authorization must explicitly prohibit main spend")
    binds = authorization.get("binds") or {}
    if binds.get("run_id") != manifest.get("run_id"):
        raise Phase3V3LiveError("authorization binds a different run_id")
    if binds.get("run_manifest_canonical_sha256") != canonical_sha256(manifest):
        raise Phase3V3LiveError("authorization binds a different run-manifest hash")
    if Path(str(binds.get("run_manifest_tracked_path"))).name != manifest_path.name:
        raise Phase3V3LiveError("authorization binds a different run-manifest path")
    if binds.get("protocol_canonical_sha256") != canonical_sha256(protocol):
        raise Phase3V3LiveError("authorization binds a different protocol")
    if binds.get("protocol_tracked_path") != PROTOCOL_RELATIVE_PATH:
        raise Phase3V3LiveError("authorization binds a different protocol path")
    seed_name = manifest["harness_check"]["seed_name"]
    if (binds.get("harness_seed_name") != seed_name
            or binds.get("harness_seed") != manifest["seeds"][seed_name]):
        raise Phase3V3LiveError("authorization binds a different harness seed")
    scope = authorization.get("scope") or {}
    cap = scope.get("incremental_cap_usd")
    if (isinstance(cap, bool) or not isinstance(cap, (int, float))
            or float(cap) != AUTHORIZED_INCREMENTAL_CAP_USD):
        raise Phase3V3LiveError(
            f"authorization incremental cap must equal ${AUTHORIZED_INCREMENTAL_CAP_USD:.2f}")
    if (scope.get("harness_execution_count") != EXPECTED_HARNESS_EXECUTIONS
            or scope.get("formal_successor_canary_execution_count") != 1
            or scope.get("successor_canary_fresh_gate_slots") != EXPECTED_FRESH_GATE_ROWS
            or scope.get("gpu_ordinal_or_not_used") != "not_used"):
        raise Phase3V3LiveError("authorization scope differs from the successor canary")
    expected_text = (
        f"Approved: {manifest['run_id']} harness and successor canary, $60 USD incremental "
        "cap, no main spend")
    owner = authorization.get("owner_authorization") or {}
    if owner.get("approver") != "Jack Maiorino" or owner.get("exact_text") != expected_text:
        raise Phase3V3LiveError("authorization does not preserve the owner's exact approval")
    return dict(authorization)


def _ledger_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise Phase3V3LiveError(f"bound usage ledger is missing: {path}")
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise Phase3V3LiveError(f"usage event {line_number} is not an object")
        events.append(row)
    return events


def validate_ledger(context: Mapping[str, Any]) -> api_client.UsageLedgerSnapshot:
    binding = context["binding"]
    path = context["paths"]["usage_ledger"]
    identity = binding.get("usage_ledger_identity")
    snapshot = api_client.load_chained_usage_ledger(path, expected_identity=identity)
    if float(snapshot.summary["uncertain_spend_usd"]) != 0:
        raise Phase3V3LiveError("usage ledger contains unresolved uncertain spend")
    for event in _ledger_events(path):
        status = event.get("status")
        if status == "unknown_charge":
            raise Phase3V3LiveError("usage ledger contains an unknown charge")
        if status == "charged_malformed":
            raise Phase3V3LiveError("usage ledger contains a charged malformed response")
        if status == "success":
            returned = (event.get("response_metadata") or {}).get("returned_model_id")
            if returned != event.get("model"):
                raise Phase3V3LiveError(
                    f"usage ledger contains unresolved returned-model drift: requested "
                    f"{event.get('model')!r}, returned {returned!r}")
    return snapshot


def _model_prices(price_snapshot: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    return {
        model: {
            "in": float(entry["input_usd_per_million"]),
            "out": float(entry["output_usd_per_million"]),
        }
        for model, entry in price_snapshot["models"].items()
    }


def build_client(context: Mapping[str, Any], *, cache_path: Path, phase: str) -> Any:
    snapshot = validate_ledger(context)
    role_limits = context["role_limits"]
    request = role_limits["request_settings"]
    transport = request["transport"]
    cap = float(context["authorization"]["scope"]["incremental_cap_usd"])
    spent = float(snapshot.summary["accounted_spend_usd"])
    if spent >= cap:
        raise Phase3V3LiveError(
            f"accounted incremental spend ${spent:.4f} has reached the ${cap:.2f} cap")
    error_log = context["paths"]["formal_error_log"]
    error_log.parent.mkdir(parents=True, exist_ok=True)
    error_log.touch(exist_ok=True)
    raw = api_client.RejudgeClient(
        approved_cap_usd=cap,
        dry_run=False,
        error_log_path=str(error_log),
        max_retries=int(transport["ledger_max_retries"]),
        model_prices=_model_prices(context["price_snapshot"]),
        strict_model_pricing=True,
        initial_spend_usd=float(snapshot.summary["actual_spend_usd"]),
        initial_uncertain_spend_usd=float(snapshot.summary["uncertain_spend_usd"]),
        usage_log_path=str(snapshot.path),
        _ledger_snapshot=snapshot,
        _accounting_factory_token=api_client._LIVE_ACCOUNTING_FACTORY_TOKEN,
        require_explicit_reasoning_max_tokens=True,
        model_context_limits={
            model: int(entry["context_length_tokens"])
            for model, entry in role_limits["context_ceilings"].items()},
        strict_context_mode=True,
        streaming_pinned_models=frozenset(request["streaming_pinned_models"]),
        reasoning_models=frozenset(role_limits["reasoning_models"]["model_ids"]),
        extra_request_fields={
            model: dict(fields)
            for model, fields in request["per_model_extra_fields"].items()},
        halt_on_unknown_charge=True,
        http_timeout=dict(transport["http_timeout"]),
        sdk_internal_max_retries=int(transport["sdk_internal_max_retries"]),
        per_call_wall_clock_ceiling_seconds=float(
            transport["per_call_wall_clock_ceiling_seconds"]),
        require_returned_model_match=True,
    )
    resolving = RoleLimitResolvingClient(raw, role_limits["model_role_limits"])
    identity = f"{context['manifest']['run_id']}:{phase}"
    return CachingClient(
        resolving, CallCache(cache_path, execution_identity=identity))


def _canary_plan(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    _main, held_out = phase3_plan.load_reference_question_ids(
        context["protocol"], context["root"])
    return phase3_plan.enumerate_canary_cells(
        context["protocol"], context["manifest"]["final_roster"], held_out)


def selected_harness_cell(context: Mapping[str, Any]) -> dict[str, Any]:
    capability = sorted(
        (cell for cell in _canary_plan(context)
         if cell["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND),
        key=lambda cell: str(cell["cell_key"]),
    )
    seed_name = context["manifest"]["harness_check"]["seed_name"]
    seed = int(context["manifest"]["seeds"][seed_name])
    selected = random.Random(seed).choice(capability)
    expected = context["binding"]["harness"]["selected_capability_cell_key"]
    if selected["cell_key"] != expected:
        raise Phase3V3LiveError(
            f"mechanical harness selection {selected['cell_key']} differs from binding {expected}")
    return selected


def _harness_result_summary(
    context: Mapping[str, Any], execution_index: int, *, resumed: bool,
) -> dict[str, Any]:
    selected = selected_harness_cell(context)
    results_path = context["paths"][f"harness_{execution_index}_results"]
    cache_path = context["paths"][f"harness_{execution_index}_cache"]
    if not results_path.is_file() or not cache_path.is_file():
        raise Phase3V3LiveError(f"harness execution {execution_index} is incomplete")
    store = CellResultStore(results_path)
    cell_key = str(selected["cell_key"])
    if set(store._results) != {cell_key}:
        raise Phase3V3LiveError(
            f"harness execution {execution_index} contains the wrong cell set")
    record = store.get(cell_key)
    if record.get("execution_git_commit") != context["manifest"]["git_commit"]:
        raise Phase3V3LiveError(
            f"harness execution {execution_index} carries the wrong execution commit")
    return {
        "execution_index": execution_index,
        "cell_key": selected["cell_key"],
        "question_id": selected["question_id"],
        "judge_model": selected["judge_model"],
        "result_store_sha256": _raw_sha256(results_path),
        "cache_sha256": _raw_sha256(cache_path),
        "accounted_spend_usd": validate_ledger(context).summary["accounted_spend_usd"],
        "resumed": resumed,
    }


def run_harness(context: Mapping[str, Any], execution_index: int) -> dict[str, Any]:
    if execution_index not in (1, 2):
        raise Phase3V3LiveError("harness execution index must be 1 or 2")
    results_path = context["paths"][f"harness_{execution_index}_results"]
    cache_path = context["paths"][f"harness_{execution_index}_cache"]
    if results_path.exists():
        result = _harness_result_summary(context, execution_index, resumed=True)
        _append_jsonl(context["paths"]["run_log"], {
            "event": "harness_execution_resumed", "recorded_at_utc": _utc_now(), **result})
        return result
    cache_preexisting = cache_path.exists()
    selected = selected_harness_cell(context)
    bundle = _load_json(context["root"] / PROMPT_BUNDLE_RELATIVE_PATH)
    client = build_client(
        context, cache_path=cache_path, phase=f"harness-{execution_index}")
    cell_context = CellContext(
        client=client, protocol=context["protocol"], bundle=bundle,
        decision_store=None, reviewer=None, anchor_judge_model="",
        transcript_generation_forbidden=True, role_limits=context["role_limits"])
    record = phase3_runner._execute_capability_cell(
        selected, context=cell_context,
        base_max_tokens=int(context["role_limits"]["base_role_max_tokens"][
            phase3_runner.CAPABILITY_QA_ROLE]))
    record["execution_git_commit"] = context["manifest"]["git_commit"]
    store = CellResultStore(results_path)
    store.record(str(selected["cell_key"]), record)
    result = _harness_result_summary(
        context, execution_index, resumed=cache_preexisting)
    _append_jsonl(context["paths"]["run_log"], {
        "event": "harness_execution_complete", "recorded_at_utc": _utc_now(), **result})
    return result


def verify_harness(context: Mapping[str, Any]) -> dict[str, Any]:
    hashes = []
    for index in (1, 2):
        result = _harness_result_summary(context, index, resumed=True)
        hashes.append(result["result_store_sha256"])
    if hashes[0] != hashes[1]:
        raise Phase3V3LiveError(
            f"harness result stores are not bit-identical: {hashes[0]} != {hashes[1]}")
    verified = phase3_v3_run_manifest.record_harness_check(
        context["manifest"], first_output_store_sha256=hashes[0],
        rerun_output_store_sha256=hashes[1])
    _write_json_exclusive(context["paths"]["harness_verified_manifest"], verified)
    _append_jsonl(context["paths"]["run_log"], {
        "event": "harness_bit_identical_pass", "recorded_at_utc": _utc_now(),
        "result_store_sha256": hashes[0]})
    return verified


def load_harness_manifest(context: Mapping[str, Any]) -> dict[str, Any]:
    path = context["paths"]["harness_verified_manifest"]
    if not path.is_file():
        raise Phase3V3LiveError("formal canary is blocked until the harness is verified")
    observed = _load_json(path)
    first = _harness_result_summary(context, 1, resumed=True)
    second = _harness_result_summary(context, 2, resumed=True)
    expected = phase3_v3_run_manifest.record_harness_check(
        context["manifest"],
        first_output_store_sha256=first["result_store_sha256"],
        rerun_output_store_sha256=second["result_store_sha256"],
    )
    if observed != expected:
        raise Phase3V3LiveError("harness-verified manifest does not derive from this run")
    return observed


def _ensure_formal_preseed(context: Mapping[str, Any]) -> None:
    transcript = context["binding"]["canary_transcript_bundle"]
    bundle_path = local_path(transcript["path"])
    bundle = _load_json(bundle_path)
    if canonical_sha256(bundle) != transcript["canonical_sha256"]:
        raise Phase3V3LiveError("frozen canary transcript bundle hash drifted")
    result = preseed_canary(
        protocol_path=context["root"] / PROTOCOL_RELATIVE_PATH,
        project_root=context["root"],
        canary_bundle_path=bundle_path,
        verification_report_path=context["root"] / TRANSCRIPT_REPORT_RELATIVE_PATH,
        target_store_path=context["paths"]["formal_results"])
    if result["canary_bundle_count"] != EXPECTED_TRANSCRIPT_ROWS:
        raise Phase3V3LiveError("canary-only preseed count drifted")
    store = CellResultStore(context["paths"]["formal_results"])
    plan = _canary_plan(context)
    allowed = {str(cell["cell_key"]) for cell in plan}
    observed = set(store._results)
    extra = observed - allowed
    if extra:
        raise Phase3V3LiveError(
            f"formal store contains {len(extra)} row(s) outside the successor canary plan")
    transcript_keys = {
        str(cell["cell_key"]) for cell in plan
        if cell["kind"] == phase3_plan.CANARY_TRANSCRIPT_KIND}
    if len(transcript_keys) != EXPECTED_TRANSCRIPT_ROWS or not transcript_keys <= observed:
        raise Phase3V3LiveError("formal store does not contain all frozen canary transcripts")


def _frozen_reviewer_prompt(context: Mapping[str, Any]) -> str:
    artifact = _load_json(context["root"] / REVIEWER_PROMPT_RELATIVE_PATH)
    prompt = str(artifact["prompt"])
    observed = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if observed != artifact.get("prompt_sha256"):
        raise Phase3V3LiveError("frozen reviewer prompt hash drifted")
    return prompt


def commit_reviewer_decisions(context: Mapping[str, Any], decisions_file: str | Path) -> dict:
    worklist = _load_json(context["paths"]["reviewer_worklist"])
    entries = _load_json(decisions_file)
    if not isinstance(entries, list):
        raise Phase3V3LiveError("reviewer decision file must contain an array")
    store = DualGateDecisionStore(context["paths"]["formal_decisions"])
    return commit_decisions_into(store, worklist, entries)


def _review_wave(context: Mapping[str, Any], pending_payloads: Sequence[Mapping[str, Any]],
                 *, codex: str, concurrency: int, wave: int) -> None:
    worklist = export_reviewer_worklist(
        pending_payloads, _frozen_reviewer_prompt(context),
        context["paths"]["reviewer_worklist"])
    archive = local_path(context["binding"]["paths"]["archive_dir"])
    before = {path.name for path in archive.glob("review_packets_auto_*") if path.is_dir()}
    started = _utc_now()
    args = SimpleNamespace(
        codex=codex, concurrency=concurrency,
        driver_module="rejudge.phase3_v3_live",
        manifest=str(context["manifest_path"]),
        authorization=str(context["authorization_path"]),
    )
    message = review_daemon.review_and_commit(list(worklist["items"]), archive, args)
    after = {path.name for path in archive.glob("review_packets_auto_*") if path.is_dir()}
    row = {
        "wave": wave,
        "started_at_utc": started,
        "completed_at_utc": _utc_now(),
        "payload_count": len(worklist["items"]),
        "worklist_raw_sha256": _raw_sha256(context["paths"]["reviewer_worklist"]),
        "new_packet_directories": sorted(after - before),
        "result": message,
    }
    _append_jsonl(context["paths"]["reviewer_index"], row)
    if message.startswith("ABORT"):
        raise Phase3V3LiveError(message)


def _formal_complete_count(context: Mapping[str, Any], plan: Sequence[Mapping[str, Any]]) -> int:
    store = CellResultStore(context["paths"]["formal_results"])
    planned = {str(cell["cell_key"]) for cell in plan}
    return len(planned & set(store._results))


def _run_capability_anchor_before_judgments(
    context: Mapping[str, Any], *, plan: Sequence[Mapping[str, Any]],
    capabilities: list[dict[str, Any]], client: Any, bundle: Mapping[str, Any],
) -> None:
    store = CellResultStore(context["paths"]["formal_results"])
    observed = set(store._results)
    capability_keys = {
        str(cell["cell_key"]) for cell in plan
        if cell["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND}
    judgment_keys = {
        str(cell["cell_key"]) for cell in plan
        if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND}
    missing_capability = capability_keys - observed
    if missing_capability and observed & judgment_keys:
        raise Phase3V3LiveError(
            "formal judgment rows exist before the capability anchor was fully frozen")
    cap_context = CellContext(
        client=client, protocol=context["protocol"], bundle=bundle,
        decision_store=None, reviewer=None, anchor_judge_model="", results={},
        transcript_generation_forbidden=True, role_limits=context["role_limits"])
    outcome = phase3_runner.run_capability_cells(
        capabilities, context=cap_context, store=store,
        base_max_tokens=int(context["role_limits"]["base_role_max_tokens"][
            phase3_runner.CAPABILITY_QA_ROLE]))
    if outcome.halted_reason is not None:
        raise Phase3V3LiveError(
            f"capability phase halted at {outcome.halted_cell_key}: "
            f"{outcome.halted_reason}")
    observed_after = set(CellResultStore(context["paths"]["formal_results"])._results)
    if len(capability_keys) != EXPECTED_CAPABILITY_ROWS or not capability_keys <= observed_after:
        raise Phase3V3LiveError("capability anchor did not reach exact completion")
    _append_jsonl(context["paths"]["run_log"], {
        "event": "capability_anchor_frozen_before_judgments",
        "recorded_at_utc": _utc_now(),
        "completed_this_invocation": outcome.completed,
        "capability_rows_complete": len(capability_keys),
    })


def drive_formal(
    context: Mapping[str, Any], *, codex: str = "codex.cmd", review_concurrency: int = 12,
    max_passes: int = 100, pending_payload_limit: int = 64,
) -> dict[str, Any]:
    load_harness_manifest(context)
    formal_results = context["paths"]["formal_results"]
    _ensure_formal_preseed(context)
    for empty_path in (
        context["paths"]["formal_decisions"], context["paths"]["reviewer_index"],
        context["paths"]["formal_error_log"],
    ):
        empty_path.parent.mkdir(parents=True, exist_ok=True)
        empty_path.touch(exist_ok=True)
    if not context["paths"]["reviewer_worklist"].exists():
        export_reviewer_worklist(
            [], _frozen_reviewer_prompt(context), context["paths"]["reviewer_worklist"])

    plan = _canary_plan(context)
    counts = {
        kind: sum(cell["kind"] == kind for cell in plan)
        for kind in (
            phase3_plan.CANARY_TRANSCRIPT_KIND,
            phase3_plan.CANARY_JUDGMENT_KIND,
            phase3_plan.CAPABILITY_ANCHOR_KIND,
        )
    }
    if counts != {
        phase3_plan.CANARY_TRANSCRIPT_KIND: EXPECTED_TRANSCRIPT_ROWS,
        phase3_plan.CANARY_JUDGMENT_KIND: EXPECTED_JUDGMENT_ROWS,
        phase3_plan.CAPABILITY_ANCHOR_KIND: EXPECTED_CAPABILITY_ROWS,
    }:
        raise Phase3V3LiveError(f"formal canary plan inventory drifted: {counts}")
    bundle = _load_json(context["root"] / PROMPT_BUNDLE_RELATIVE_PATH)
    judgments, capabilities = phase3_runner.resolve_canary_cells(
        plan, protocol=context["protocol"], bundle=bundle)
    expected_codex = context["binding"]["toolchain"]["reviewer_cli"]["binary"]
    expected_concurrency = int(
        context["binding"]["formal_execution"]["reviewer_concurrency"])
    expected_pending = int(
        context["binding"]["formal_execution"]["pending_payload_limit"])
    if codex != expected_codex:
        raise Phase3V3LiveError(
            f"reviewer CLI must remain {expected_codex!r}, observed {codex!r}")
    if review_concurrency != expected_concurrency:
        raise Phase3V3LiveError("reviewer concurrency differs from the execution binding")
    if pending_payload_limit != expected_pending:
        raise Phase3V3LiveError("pending-payload limit differs from the execution binding")
    if max_passes < 1:
        raise Phase3V3LiveError("max_passes must be positive")
    client = build_client(
        context, cache_path=context["paths"]["formal_cache"], phase="formal")
    records._GIT_SHA = str(context["manifest"]["git_commit"])[:7]
    reviewer = _PauseModeReviewer()
    _append_jsonl(context["paths"]["run_log"], {
        "event": "formal_drive_started", "recorded_at_utc": _utc_now(),
        "planned_total_rows": len(plan), "main_spend_authorized": False})
    _run_capability_anchor_before_judgments(
        context, plan=plan, capabilities=capabilities, client=client, bundle=bundle)

    for pass_index in range(1, max_passes + 1):
        outcome = run_canary(
            results_path=formal_results,
            decisions_path=context["paths"]["formal_decisions"],
            client=client,
            reviewer=reviewer,
            anchor_judge_model="",
            protocol=context["protocol"],
            bundle=bundle,
            pause_when_unlabeled=True,
            cells=judgments,
            max_workers=1,
            transcript_generation_forbidden=True,
            namespace=str(context["protocol"]["cell_key_namespace"]),
            pending_payload_limit=pending_payload_limit,
            role_limits=context["role_limits"],
        )
        if outcome.halted_reason == "GenerationForbiddenError":
            raise GenerationForbiddenError(
                f"unseeded transcript cell reached execution: {outcome.halted_cell_key}")
        if outcome.halted_reason is not None:
            raise Phase3V3LiveError(
                f"formal pass halted at {outcome.halted_cell_key}: {outcome.halted_reason}")

        complete = _formal_complete_count(context, plan)
        _append_jsonl(context["paths"]["run_log"], {
            "event": "formal_pass_complete", "recorded_at_utc": _utc_now(),
            "pass_index": pass_index, "completed_this_pass": outcome.completed,
            "planned_rows_complete": complete, "pending_labels": len(outcome.pending_payloads),
            "accounted_spend_usd": validate_ledger(context).summary["accounted_spend_usd"],
        })
        print(json.dumps({
            "pass": pass_index, "complete": complete, "planned": len(plan),
            "pending_labels": len(outcome.pending_payloads),
            "spend_usd": validate_ledger(context).summary["accounted_spend_usd"],
        }, sort_keys=True), flush=True)
        if complete == len(plan):
            report = audit_and_finalize(context)
            return report
        if outcome.pending_payloads:
            _review_wave(
                context, outcome.pending_payloads, codex=codex,
                concurrency=review_concurrency, wave=pass_index)
            continue
        if outcome.completed == 0:
            raise Phase3V3LiveError(
                f"formal canary made no progress with {len(plan) - complete} rows remaining")
    raise Phase3V3LiveError(f"formal canary did not converge within {max_passes} passes")


def _load_result_rows(path: Path) -> list[dict[str, Any]]:
    return phase3_polarity_verify.load_result_rows(path)


def _usage_scope_check(context: Mapping[str, Any], plan: Sequence[Mapping[str, Any]]) -> None:
    allowed = {str(cell["cell_key"]) for cell in plan}
    for event in _ledger_events(context["paths"]["usage_ledger"]):
        if event.get("status") == "ledger_genesis":
            continue
        metadata = event.get("metadata") or {}
        cell_key = metadata.get("cell_key")
        if cell_key not in allowed:
            raise Phase3V3LiveError(
                f"usage event is outside the canary plan: {cell_key!r}")


def _invalid_gate(
    store: CellResultStore, plan: Sequence[Mapping[str, Any]], roster: Sequence[str],
) -> dict[str, Any]:
    b0 = [cell for cell in plan if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND
          and cell["condition"] == "b0"]
    report = {}
    for judge in roster:
        cells = [cell for cell in b0 if cell["judge_model"] == judge]
        invalid = sum(
            store.get(str(cell["cell_key"]))["verdict_strict"]["verdict"] is None
            for cell in cells)
        report[judge] = {
            "slots": len(cells), "invalid": invalid,
            "integer_gate": "invalid <= 1", "pass": len(cells) == 96 and invalid <= 1,
        }
    return report


def _capability_anchor_diagnostics(
    store: CellResultStore, plan: Sequence[Mapping[str, Any]], roster: Sequence[str],
) -> dict[str, Any]:
    rows_by_judge: dict[str, list[Mapping[str, Any]]] = {judge: [] for judge in roster}
    for cell in plan:
        if cell["kind"] != phase3_plan.CAPABILITY_ANCHOR_KIND:
            continue
        rows_by_judge[str(cell["judge_model"])].append(
            store.get(str(cell["cell_key"])))
    report: dict[str, Any] = {}
    for judge in roster:
        rows = rows_by_judge[judge]
        tolerant_score = 0
        strict_score = 0
        stored_parse_mismatches = 0
        for row in rows:
            tolerant = phase3_runner.parse_capability_verdict_tolerant(
                row.get("raw_verdict_text"))
            strict = phase3_runner.parse_capability_verdict_strict(
                row.get("raw_verdict_text"))
            side = row.get("side")
            tolerant_score += int(tolerant == side)
            strict_score += int(strict == side)
            stored_parse_mismatches += int(
                bool(row.get("is_correct_tolerant")) != (tolerant == side))
            stored_parse_mismatches += int(
                bool(row.get("is_correct_strict")) != (strict == side))
        if len(rows) != 48 or stored_parse_mismatches:
            raise Phase3V3LiveError(
                f"capability-anchor audit failed for {judge}: n={len(rows)}, "
                f"stored_parse_mismatches={stored_parse_mismatches}")
        report[judge] = {
            "n": len(rows),
            "tolerant_score": tolerant_score,
            "tolerant_fraction": tolerant_score / len(rows),
            "strict_score": strict_score,
            "strict_fraction": strict_score / len(rows),
            "tolerant_invalid_or_wrong": len(rows) - tolerant_score,
            "strict_invalid_or_wrong": len(rows) - strict_score,
            "gate_consequence": "none; descriptive capability covariate",
        }
    return report


def _paired_position_diagnostics(
    rows: Sequence[Mapping[str, Any]], plan: Sequence[Mapping[str, Any]],
    protocol: Mapping[str, Any], question_bank: Mapping[str, Mapping[str, Any]],
    roster: Sequence[str],
) -> dict[str, Any]:
    row_by_key = {str(row["cell_key"]): row for row in rows}
    conditions = {
        str(condition["id"]): condition for condition in protocol["debate_grid"]["conditions"]}
    core_condition = next(
        condition_id for condition_id, condition in conditions.items()
        if int(condition["query_budget"]) == 0)
    pairs_by_judge: dict[str, dict[tuple[Any, ...], dict[str, Mapping[str, Any]]]] = {
        judge: {} for judge in roster}
    unresolved_by_judge: dict[str, list[str]] = {judge: [] for judge in roster}
    for cell in plan:
        if (cell["kind"] != phase3_plan.CANARY_JUDGMENT_KIND
                or cell["condition"] != core_condition):
            continue
        judge = str(cell["judge_model"])
        key = str(cell["cell_key"])
        observation = phase3_canary_closeout_v2._rendered_observation(
            row_by_key[key], question_bank)
        if not observation.get("resolved"):
            unresolved_by_judge[judge].append(key)
            continue
        replicates = int(
            conditions[core_condition]["judgment_replicates_per_transcript_side"])
        pair_key = (
            str(cell["question_id"]), str(cell["debater_model"]),
            int(cell.get("transcript_index") or 0), int(cell["replicate_index"]) % replicates,
        )
        label = f"{observation['correct_position']}_correct"
        pair = pairs_by_judge[judge].setdefault(pair_key, {})
        if label in pair:
            unresolved_by_judge[judge].append(key)
            continue
        pair[label] = observation

    report: dict[str, Any] = {}
    for judge in roster:
        pairs = pairs_by_judge[judge]
        complete = [pair for pair in pairs.values()
                    if set(pair) == {"A_correct", "B_correct"}]
        diagnostic = phase3_canary_closeout_v2.compute_paired_position_diagnostic(complete)
        diagnostic.update({
            "expected_pairs": 48,
            "incomplete_pair_count": len(pairs) - len(complete),
            "unresolved_row_count": len(unresolved_by_judge[judge]),
            "unresolved_cell_keys": sorted(unresolved_by_judge[judge]),
        })
        report[judge] = diagnostic
    return report


def audit_and_finalize(context: Mapping[str, Any]) -> dict[str, Any]:
    harness_manifest = load_harness_manifest(context)
    plan = _canary_plan(context)
    store = CellResultStore(context["paths"]["formal_results"])
    expected_keys = {str(cell["cell_key"]) for cell in plan}
    observed_keys = set(store._results)
    if observed_keys != expected_keys:
        raise Phase3V3LiveError(
            f"formal result set differs from plan: missing={len(expected_keys - observed_keys)}, "
            f"extra={len(observed_keys - expected_keys)}")
    if len(observed_keys) != EXPECTED_TOTAL_ROWS:
        raise Phase3V3LiveError("formal result count differs from the 1,008-row canary")
    _usage_scope_check(context, plan)
    snapshot = validate_ledger(context)
    cap = float(context["authorization"]["scope"]["incremental_cap_usd"])
    if float(snapshot.summary["accounted_spend_usd"]) > cap:
        raise Phase3V3LiveError("accounted spend exceeds the authorized incremental cap")
    CallCache(
        context["paths"]["formal_cache"],
        execution_identity=f"{context['manifest']['run_id']}:formal")
    DualGateDecisionStore(context["paths"]["formal_decisions"])

    rows = _load_result_rows(context["paths"]["formal_results"])
    _main_ids, held_out = phase3_plan.load_reference_question_ids(
        context["protocol"], context["root"])
    question_bank = phase3_polarity_verify._load_question_bank()
    full_polarity = phase3_polarity_verify.verify(
        rows, protocol=context["protocol"], judges=context["manifest"]["final_roster"],
        held_out_ids=held_out, question_bank=question_bank)
    b0_keys = {
        str(cell["cell_key"]) for cell in plan
        if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND and cell["condition"] == "b0"}
    b0_rows = [row for row in rows if row.get("cell_key") in b0_keys]
    b0_polarity = phase3_polarity_verify.verify(
        b0_rows, protocol=context["protocol"], judges=context["manifest"]["final_roster"],
        held_out_ids=held_out, question_bank=question_bank)
    polarity_problems = phase3_orchestrator_support.evaluate_polarity_gate(
        full_polarity, b0_polarity)
    invalid = _invalid_gate(store, plan, context["manifest"]["final_roster"])
    capability = _capability_anchor_diagnostics(
        store, plan, context["manifest"]["final_roster"])
    paired_position = _paired_position_diagnostics(
        rows, plan, context["protocol"], question_bank,
        context["manifest"]["final_roster"])
    gates = {
        "completion": {"expected_rows": EXPECTED_TOTAL_ROWS,
                       "observed_rows": len(observed_keys), "pass": True},
        "structural_mirroring": {"pass": not polarity_problems,
                                 "problems": polarity_problems,
                                 "full": full_polarity, "b0": b0_polarity},
        "strict_invalid_per_judge": invalid,
        "ledger": {**snapshot.summary, "incremental_cap_usd": cap,
                   "pass": float(snapshot.summary["accounted_spend_usd"]) <= cap},
        "main_spend": {"authorized": False, "observed_main_usage_events": 0, "pass": True},
    }
    gate_failures = []
    if polarity_problems:
        gate_failures.append("structural_mirroring")
    if not all(entry["pass"] for entry in invalid.values()):
        gate_failures.append("strict_invalid_per_judge")

    report = {
        "schema_version": "phase3_v3_successor_canary_report_v1",
        "run_id": context["manifest"]["run_id"],
        "recorded_at_utc": _utc_now(),
        "formal_status": "complete",
        "gate_status": "pass" if not gate_failures else "fail",
        "gate_failures": gate_failures,
        "gates": gates,
        "diagnostics": {
            "capability_anchor_by_judge": capability,
            "paired_position_by_judge": paired_position,
            "capability_slope_inference": "estimate_and_plot_only_no_p_value",
            "configuration_selection_pace": {
                "status": "not_evaluated_by_canary_closeout",
                "minimum_full_hour_blocks": 8,
                "main_authorization_blocked": True,
            },
        },
        "result_store_sha256": _raw_sha256(context["paths"]["formal_results"]),
        "usage_ledger_sha256": _raw_sha256(context["paths"]["usage_ledger"]),
        "harness_result_store_sha256": harness_manifest["harness_check"][
            "first_output_store_sha256"],
        "non_claims": [
            "This successor canary is an engineering and eligibility gate, not main-run "
            "evidence for the budget-effect estimands.",
            "No main-run provider call is authorized or executed by this adapter.",
            "Configuration-selection pace is not estimable unless the frozen eight-full-hour "
            "review window requirement is independently met.",
        ],
    }
    _write_json_exclusive(context["paths"]["final_report"], report)
    _append_jsonl(context["paths"]["run_log"], {
        "event": "formal_audit_complete", "recorded_at_utc": _utc_now(),
        "gate_status": report["gate_status"], "gate_failures": gate_failures,
        "accounted_spend_usd": snapshot.summary["accounted_spend_usd"],
    })
    output_hashes = {
        path: _raw_sha256(local_path(path)) for path in context["manifest"]["planned_output_paths"]}
    completed_manifest = phase3_v3_run_manifest.finalize_run_manifest(
        harness_manifest, output_sha256s=output_hashes)
    _write_json_exclusive(context["paths"]["final_manifest"], completed_manifest)
    return report


def _context_with_authorization_path(context: dict[str, Any], path: str | Path) -> dict[str, Any]:
    context["authorization_path"] = Path(path).resolve()
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase3_v3_live")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--project-root", default=".")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--harness-index", type=int)
    actions.add_argument("--verify-harness", action="store_true")
    actions.add_argument("--drive-formal", action="store_true")
    actions.add_argument("--audit", action="store_true")
    actions.add_argument("--commit-decisions")
    parser.add_argument("--codex", default="codex.cmd")
    parser.add_argument("--review-concurrency", type=int, default=12)
    parser.add_argument("--max-passes", type=int, default=100)
    parser.add_argument("--pending-payload-limit", type=int, default=64)
    args = parser.parse_args(argv)
    try:
        require_harness = bool(args.drive_formal or args.audit or args.commit_decisions)
        context = load_run_context(
            args.manifest, args.authorization, project_root=args.project_root,
            require_harness=require_harness)
        _context_with_authorization_path(context, args.authorization)
        if args.commit_decisions:
            result = commit_reviewer_decisions(context, args.commit_decisions)
        else:
            lease_path = local_path(context["binding"]["paths"]["run_lock"])
            with RunLease(lease_path):
                if args.harness_index is not None:
                    result = run_harness(context, args.harness_index)
                elif args.verify_harness:
                    result = verify_harness(context)
                elif args.drive_formal:
                    result = drive_formal(
                        context, codex=args.codex,
                        review_concurrency=args.review_concurrency,
                        max_passes=args.max_passes,
                        pending_payload_limit=args.pending_payload_limit)
                else:
                    result = audit_and_finalize(context)
    except Exception as exc:  # noqa: BLE001 - fail closed at the CLI boundary
        print(f"REFUSED/HALTED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
