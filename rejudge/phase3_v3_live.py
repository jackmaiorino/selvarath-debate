"""Live adapter for the authorized Phase 3 v3 successor canary.

This module has no main-run entry point. It executes exactly two isolated one-cell harness
runs and, after their result stores hash identically, the 48-transcript, 768-judgment,
192-capability successor canary. Every Together call uses one durable successor ledger, with
any bound prior-attempt accounting carried into the authorized aggregate cap.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
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
AUTHORIZATION_SCHEMA_V2 = "phase3_v3_canary_authorization_v2"
BINDING_SCHEMA = "phase3_v3_execution_binding_v1"
BINDING_SCHEMA_V2 = "phase3_v3_execution_binding_v2"
BINDING_SCHEMA_V3 = "phase3_v3_execution_binding_v3"
BINDING_SCHEMA_V4 = "phase3_v3_execution_binding_v4"
BINDING_SCHEMA_V5 = "phase3_v3_execution_binding_v5"
BINDING_SCHEMA_V6 = "phase3_v3_execution_binding_v6"
BINDING_SCHEMA_V7 = "phase3_v3_execution_binding_v7"
BINDING_SCHEMA_V8 = "phase3_v3_execution_binding_v8"
BINDING_SCHEMA_V9 = "phase3_v3_execution_binding_v9"
BINDING_SCHEMA_V10 = "phase3_v3_execution_binding_v10"
BINDING_SCHEMA_V11 = "phase3_v3_execution_binding_v11"
BINDING_SCHEMA_V12 = "phase3_v3_execution_binding_v12"
ROLE_LIMITS_SCHEMA = "phase3_v3_role_limits_v1"
ROLE_LIMITS_SCHEMA_V2 = "phase3_v3_role_limits_v2"
ROLE_LIMITS_SCHEMA_V3 = "phase3_v3_role_limits_v3"
ROLE_LIMITS_SCHEMA_V4 = "phase3_v3_role_limits_v4"
ROLE_LIMITS_SCHEMA_V5 = "phase3_v3_role_limits_v5"
ROLE_LIMITS_SCHEMA_V6 = "phase3_v3_role_limits_v6"
# Amendment 8 (2026-08-27): the screened, headroom-verified verdict budget for the one
# admitted thinking judge (0-of-96 judgment-shaped screen at this exact cap, every probe
# finish_reason=stop, max completion 38% of cap). gemma-4-31B failed verdict admission at
# every screened cap (rare per-prompt-deterministic runaway; Codex ruled a post-hoc
# exemption gate-weakening) and keeps ONLY its screened checker role, so it takes no
# verdict budget at all under the v6 schema. The raise applies to judge_verdict and
# batch_verdict only; every other role keeps the 4,096 reasoning floor or its
# non-reasoning base limit.
JUDGMENT_BUDGET_RAISE_V6 = {
    "Qwen/Qwen3.8-2.4T-A95B": 16384,
}
JUDGMENT_BUDGET_RAISE_ROLES = ("judge_verdict", "batch_verdict")
EXPECTED_TRANSCRIPT_ROWS = 48
# Fresh gate rows scale with the resolved roster (192 judgment + 48 capability-anchor rows
# per judge; the 48 preseeded canary transcripts are roster-independent), so every row-count
# check derives its expectation from the protocol via _expected_row_counts rather than
# assuming the original four-judge inventory.
EXPECTED_JUDGMENT_ROWS_PER_JUDGE = 192
EXPECTED_CAPABILITY_ROWS_PER_JUDGE = 48
EXPECTED_HARNESS_EXECUTIONS = 2
AUTHORIZED_INCREMENTAL_CAP_USD = 60.0
PRIOR_ACCOUNTED_SPEND_USD = 0.21711289000000006
PRIOR_ACCOUNTED_SPEND_USD_R3 = 0.30985289000000005
PRIOR_ACCOUNTED_SPEND_USD_R4 = 4.977210709999999
PRIOR_ACCOUNTED_SPEND_USD_R5 = 8.167501109999998
PRIOR_ACCOUNTED_SPEND_USD_R6 = 14.726113819999991
PRIOR_ACCOUNTED_SPEND_USD_R7 = 21.663206619999986
PRIOR_ACCOUNTED_SPEND_USD_R8 = 25.406553089999985
PRIOR_ACCOUNTED_SPEND_USD_R9 = 30.526558439999988
# R10 carry (2026-08-27): R9 plus the wedged final-four-judge identity's sealed ledger
# (run phase3-v3-534667eded4165df, two events, $0.00130943 accounted, computed via
# load_chained_usage_ledger against the read-only r9 archive). The r21 empty-verdict
# discovery record is that identity's terminal observation; no formal spend occurred.
PRIOR_ACCOUNTED_SPEND_USD_R10 = 30.527867869999987
# R11 carry (2026-08-28): R10 plus the first N=2 attempt's sealed ledger (run
# phase3-v3-5cc134ec5730dfaf, $4.1226888 accounted, zero open reservations), which went
# identity-terminal on the r23 exclusion-filter defect after 336 clean rows.
PRIOR_ACCOUNTED_SPEND_USD_R11 = 34.650556669999986
# R12 carry (2026-08-28): R11 plus the second N=2 attempt's sealed ledger (run
# phase3-v3-d5836a2141cb3736, $7.88070266 accounted, zero open reservations), stopped at
# the frozen amendment-5 concentration bound when gemma-4's checker runaway reached five
# sequential_b2 cells (r25 record).
PRIOR_ACCOUNTED_SPEND_USD_R12 = 42.53125933
# Amendment 7 (2026-08-27): the owner-approved final four-judge attempt runs under a $5.00
# per-run uncertain ceiling (role-limits schema v5, Codex-ratified as sufficient under an
# r19-like stationary drip by linear projection, with no claim across weather regimes).
RUN_UNCERTAIN_CEILING_USD_V5 = 5.0
# Amendment 6 (2026-08-26): the owner-selected raised per-run uncertain ceiling for the
# post-ceiling-trip successor (role-limits schema v4). Historical schema revisions keep
# their $1.00 pin so sealed history validates under its own policy.
RUN_UNCERTAIN_CEILING_USD_V4 = 2.5
# Amendment 5 (2026-08-26, Codex-set): frozen terminal-halt disposition bounds. A record
# names one judgment cell whose frozen checker returned an unparseable response (the
# temperature-0 nondeterminism the phase-2 missing-data policy already decides); the cell is
# excluded from execution convergence, counts INVALID in the primary, and its whole mirror
# unit leaves the paired polarity analyses. Crossing any bound stops the run for owner
# review and may never be relaxed mid-run.
TERMINAL_HALTS_SCHEMA = "phase3_v3_terminal_halts_v1"
MAX_TERMINAL_JUDGMENT_CELLS = 20
MAX_AFFECTED_MIRROR_UNIT_FRACTION = 0.04
CONCENTRATION_MIN_CELLS = 5
CONCENTRATION_RATE = 0.02
CONCENTRATION_RATIO = 3.0
# Per-run ceiling on unresolved billing-uncertain exposure (amendment 3, 2026-08-25,
# Codex-set at $1.00: >10x the observed $0.0919 burst, ~15% of the expected run cost).
# Uncertain spend always ALSO counts in full against the aggregate cap. Tripping the
# ceiling is a fail-closed halt for owner review; it never auto-chains a successor.
RUN_UNCERTAIN_CEILING_USD = 1.0
R9_HALT_RELATIVE_PATH = "rejudge/phase3_v3_r9_uncertain_spend_halt_2026-08-25.json"
NON_MANIFEST_OUTPUT_PATH_KEYS = frozenset({
    "archive_dir", "run_lock", "harness_verified_manifest", "final_manifest",
})

REQUIRED_COMMON_INPUT_PATHS = frozenset({
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


def _resolve_manifest_input_paths(manifest: Mapping[str, Any]) -> dict[str, str]:
    inputs = manifest.get("input_sha256s")
    if not isinstance(inputs, Mapping):
        raise Phase3V3LiveError("run manifest has no input_sha256s mapping")
    paths = [str(path).replace("\\", "/") for path in inputs]

    def select(label: str, predicate) -> str:
        matches = [path for path in paths if predicate(path, Path(path).name)]
        if len(matches) != 1:
            raise Phase3V3LiveError(
                f"run manifest must bind exactly one {label}; found {matches}")
        return matches[0]

    return {
        "protocol": select(
            "v3 protocol",
            lambda path, name: name.startswith("phase3_protocol_v3_")
            and not name.startswith("phase3_protocol_v3_pin_")
            and inputs[path] == manifest.get("protocol_sha256")),
        "protocol_pin": select(
            "v3 protocol pin",
            lambda _path, name: name.startswith("phase3_protocol_v3_pin_")),
        "tokenizer_manifest": select(
            "v3 exact-tokenizer manifest",
            lambda path, name: name.startswith("phase3_v3_exact_tokenizer_manifest_")
            and inputs[path] == manifest.get("tokenizer_manifest_sha256")),
        "price_snapshot": select(
            "v3 price snapshot",
            lambda path, name: name.startswith("phase3_v3_price_snapshot_")
            and inputs[path] == manifest.get("price_snapshot_sha256")),
        "role_limits": select(
            "v3 role-limits artifact",
            lambda _path, name: name.startswith("phase3_v3_role_limits")),
        "execution_binding": select(
            "v3 execution binding",
            lambda _path, name: name.startswith("phase3_v3_execution_binding")),
    }


def _validate_all_input_hashes(
    manifest: Mapping[str, Any], root: Path,
) -> dict[str, str]:
    inputs = manifest["input_sha256s"]
    resolved = _resolve_manifest_input_paths(manifest)
    missing = sorted(REQUIRED_COMMON_INPUT_PATHS - set(inputs))
    if missing:
        raise Phase3V3LiveError(f"run manifest is missing required input bindings: {missing}")
    for relative, expected in inputs.items():
        path = root / relative
        payload = _load_json(path)
        observed = canonical_sha256(payload)
        if observed != expected:
            raise Phase3V3LiveError(
                f"input hash drift for {relative}: observed {observed}, expected {expected}")
    return resolved


def _expected_row_counts(protocol: Mapping[str, Any]) -> dict[str, int]:
    """Per-kind canary row expectations derived from the resolved roster.

    The protocol validator already cross-checks its own canary_slot_inventory against
    len(judges_final); this derives the same arithmetic for the driver's row-count gates
    so a roster amendment cannot silently desynchronize the two.
    """
    roster_size = len(protocol["roster"]["judges_final"])
    judgment = EXPECTED_JUDGMENT_ROWS_PER_JUDGE * roster_size
    capability = EXPECTED_CAPABILITY_ROWS_PER_JUDGE * roster_size
    return {
        "transcript": EXPECTED_TRANSCRIPT_ROWS,
        "judgment": judgment,
        "capability": capability,
        "fresh_gate": judgment + capability,
        "total": EXPECTED_TRANSCRIPT_ROWS + judgment + capability,
    }


def _expected_reasoning_models(protocol: Mapping[str, Any]) -> frozenset[str]:
    roster = set(protocol["roster"]["judges_final"])
    # gemma-4 is a reasoning caller only while it BILLS something: under the r6 protocol
    # (amendment 9) it leaves the registry entirely, so its reasoning membership follows
    # the billed-model registry rather than being unconditional.
    billed = set(protocol["model_registry"]["models"])
    expected = {"google/gemma-4-31B-it"} & billed
    qwen = roster & {"Qwen/Qwen3.5-397B-A17B", "Qwen/Qwen3.8-2.4T-A95B"}
    if len(qwen) != 1:
        raise Phase3V3LiveError("v3 roster must contain exactly one approved Qwen reasoner")
    # The amendment-4 weak-slot substitute renders thinking-on by default (screening record
    # phase3_v3_qwen35_9b_screening_2026-08-25: the default template ends in an opened
    # <think> block and exactly matches provider prompt-token counts), so it carries the
    # same 4096-token reasoning floor as the other thinking models.
    small_qwen = roster & {"Qwen/Qwen3.5-9B"}
    return frozenset(expected | qwen | small_qwen)


def _validate_role_limits(role_limits: Mapping[str, Any], protocol: Mapping[str, Any]) -> None:
    schema_version = role_limits.get("schema_version")
    if schema_version not in {
            ROLE_LIMITS_SCHEMA, ROLE_LIMITS_SCHEMA_V2, ROLE_LIMITS_SCHEMA_V3,
            ROLE_LIMITS_SCHEMA_V4, ROLE_LIMITS_SCHEMA_V5, ROLE_LIMITS_SCHEMA_V6}:
        raise Phase3V3LiveError("unsupported v3 role-limits schema")
    if role_limits.get("execution_authorized") is not False:
        raise Phase3V3LiveError("role limits cannot authorize execution")
    if role_limits.get("protocol_id") != protocol.get("protocol_id"):
        raise Phase3V3LiveError("role limits bind a different protocol")
    roster = set(protocol["roster"]["judges_final"])
    checker_model = str(protocol["roster"]["query_checker"])
    oracle_model = str(protocol["roster"]["oracle"])
    # Under the v6 schema a service model (checker or oracle) may serve WITHOUT holding a
    # judge seat (amendment 8 keeps gemma-4 checker-only after its verdict exclusion), so
    # the limits table covers roster judges plus service models. Earlier schemas keep the
    # exact-roster rule they ran under.
    expected_models = (
        roster | {checker_model, oracle_model}
        if schema_version == ROLE_LIMITS_SCHEMA_V6 else roster)
    limits = role_limits.get("model_role_limits")
    if not isinstance(limits, Mapping) or set(limits) != expected_models:
        raise Phase3V3LiveError("role-limits model set differs from the final roster")
    base = role_limits.get("base_role_max_tokens")
    if not isinstance(base, Mapping):
        raise Phase3V3LiveError("role limits have no base_role_max_tokens")
    reasoning = set((role_limits.get("reasoning_models") or {}).get("model_ids") or ())
    floor = int((role_limits.get("reasoning_models") or {}).get("floor_max_tokens") or 0)
    expected_reasoning = _expected_reasoning_models(protocol)
    if reasoning != expected_reasoning or floor != 4096:
        raise Phase3V3LiveError("reasoning-model roster or 4096-token floor drifted")
    required_roles: dict[str, set[str]] = {
        model: {"judge_query", "judge_verdict", "capability_qa"} for model in roster}
    required_roles.setdefault(checker_model, set()).add("query_checker")
    required_roles.setdefault(oracle_model, set()).add("oracle")
    if schema_version == ROLE_LIMITS_SCHEMA_V6:
        for model in expected_models - roster:
            extra = set(limits[model]) - required_roles.get(model, set())
            if extra:
                raise Phase3V3LiveError(
                    f"non-judge service model {model} may not hold limits for {sorted(extra)}")
    for model, roles in required_roles.items():
        model_limits = limits[model]
        if not roles <= set(model_limits):
            raise Phase3V3LiveError(
                f"role limits for {model} omit {sorted(roles - set(model_limits))}")
        for role, entry in model_limits.items():
            expected_base = int(base[role])
            expected_effective = max(expected_base, floor) if model in reasoning else expected_base
            if (schema_version == ROLE_LIMITS_SCHEMA_V6
                    and role in JUDGMENT_BUDGET_RAISE_ROLES
                    and model in JUDGMENT_BUDGET_RAISE_V6):
                expected_effective = JUDGMENT_BUDGET_RAISE_V6[model]
            if (int(entry.get("base_role_max_tokens", -1)) != expected_base
                    or int(entry.get("effective_request_max_tokens", -1)) != expected_effective):
                raise Phase3V3LiveError(f"role limit drift for ({model}, {role})")
    contexts = role_limits.get("context_ceilings")
    if not isinstance(contexts, Mapping) or set(contexts) != expected_models:
        raise Phase3V3LiveError("context ceilings differ from the final roster")
    request = role_limits.get("request_settings") or {}
    if request.get("base_fields") != ["model", "messages", "temperature", "max_tokens", "seed"]:
        raise Phase3V3LiveError("base provider request fields drifted")
    if schema_version in {
            ROLE_LIMITS_SCHEMA_V2, ROLE_LIMITS_SCHEMA_V3, ROLE_LIMITS_SCHEMA_V4,
            ROLE_LIMITS_SCHEMA_V5, ROLE_LIMITS_SCHEMA_V6}:
        # The gemma-4 streaming pin exists only while gemma-4 bills something; under the
        # r6 protocol it left the registry, and pinning transport for a model that makes
        # no calls would be an untruthful artifact.
        expected_streaming = (
            {"google/gemma-4-31B-it": {"stream": True}}
            if "google/gemma-4-31B-it" in set(protocol["model_registry"]["models"])
            else {})
        transport_fix = role_limits.get("qwen38_usage_transport_fix") or {}
        if transport_fix != {
            "model_id": "Qwen/Qwen3.8-2.4T-A95B",
            "stream": False,
            "trigger_halt_path": "rejudge/phase3_v3_r7_provider_halt_2026-08-25.json",
            "trigger_halt_canonical_sha256": (
                "ffca5b11d855ed51ba37a33a4b31bc90d08e2bb05ae850d44400364e69495a08"),
            "trigger_error": "streaming response ended without usage chunk",
        }:
            raise Phase3V3LiveError("Qwen3.8 usage-transport fix drifted")
        if schema_version == ROLE_LIMITS_SCHEMA_V3:
            tolerance = role_limits.get("uncertain_spend_tolerance") or {}
            if tolerance != {
                "run_uncertain_ceiling_usd": RUN_UNCERTAIN_CEILING_USD,
                "counts_fully_against_aggregate_cap": True,
                "halts_run_for_owner_review_when_exceeded": True,
                "trigger_halt_path": R9_HALT_RELATIVE_PATH,
                "trigger_halt_canonical_sha256": (
                    "bda47615c351b81568cb82f05749e321b635d7fe284ba1b3871310ffb27553e7"),
                "trigger_error": "usage ledger contains unresolved uncertain spend",
            }:
                raise Phase3V3LiveError("uncertain-spend tolerance policy drifted")
        elif schema_version == ROLE_LIMITS_SCHEMA_V4:
            tolerance = role_limits.get("uncertain_spend_tolerance") or {}
            if tolerance != {
                "run_uncertain_ceiling_usd": RUN_UNCERTAIN_CEILING_USD_V4,
                "counts_fully_against_aggregate_cap": True,
                "halts_run_for_owner_review_when_exceeded": True,
                "owner_choice": "Raise ceiling to $2.50 (Recommended)",
                "trigger_halt_path": (
                    "rejudge/phase3_v3_r15_uncertain_ceiling_stop_2026-08-26.json"),
                "trigger_halt_canonical_sha256": (
                    "2a3d7551ad63a166cd105221c9e149e7048fa144807d8ed83688384cfdabbc14"),
                "trigger_error": "run-local uncertain spend exceeds the frozen ceiling",
            }:
                raise Phase3V3LiveError("uncertain-spend tolerance policy drifted")
        elif schema_version == ROLE_LIMITS_SCHEMA_V5:
            tolerance = role_limits.get("uncertain_spend_tolerance") or {}
            if tolerance != {
                "run_uncertain_ceiling_usd": RUN_UNCERTAIN_CEILING_USD_V5,
                "counts_fully_against_aggregate_cap": True,
                "halts_run_for_owner_review_when_exceeded": True,
                "owner_choice": "Last 4-judge try, N=3 fallback (Recommended)",
                "trigger_halt_path": (
                    "rejudge/phase3_v3_r19_offpeak_pause_stop_2026-08-27.json"),
                "trigger_halt_canonical_sha256": (
                    "40ae2fe7940e5280ba73c1229f25f771678ade3c37a9e2267bc4860faefad531"),
                "trigger_error": "run-local uncertain spend exceeds the frozen ceiling",
            }:
                raise Phase3V3LiveError("uncertain-spend tolerance policy drifted")
        elif schema_version == ROLE_LIMITS_SCHEMA_V6:
            tolerance = role_limits.get("uncertain_spend_tolerance") or {}
            if tolerance != {
                "run_uncertain_ceiling_usd": RUN_UNCERTAIN_CEILING_USD_V5,
                "counts_fully_against_aggregate_cap": True,
                "halts_run_for_owner_review_when_exceeded": True,
                "owner_choice": "Last 4-judge try, N=3 fallback (Recommended)",
                "carried_from": "phase3_v3_role_limits_v5 (amendment 7)",
                "trigger_halt_path": (
                    "rejudge/phase3_v3_r19_offpeak_pause_stop_2026-08-27.json"),
                "trigger_halt_canonical_sha256": (
                    "40ae2fe7940e5280ba73c1229f25f771678ade3c37a9e2267bc4860faefad531"),
                "trigger_error": "run-local uncertain spend exceeds the frozen ceiling",
            }:
                raise Phase3V3LiveError("uncertain-spend tolerance policy drifted")
            results_sha = phase3_plan.FROZEN_JUDGMENT_SCREEN_RESULTS_CANONICAL_SHA256
            if results_sha is None:
                raise Phase3V3LiveError(
                    "v6 role limits require the frozen judgment-screen results binding, "
                    "which is not frozen yet")
            raise_spec = role_limits.get("judgment_budget_raise") or {}
            if raise_spec != {
                "roles": list(JUDGMENT_BUDGET_RAISE_ROLES),
                "model_effective_request_max_tokens": dict(JUDGMENT_BUDGET_RAISE_V6),
                "owner_choice": "Full path (Recommended)",
                "evidence_path": (
                    "rejudge/phase3_v3_judgment_screen_results_record_2026-08-27.json"),
                "evidence_canonical_sha256": results_sha,
            }:
                raise Phase3V3LiveError("judgment-budget raise policy drifted")
        elif role_limits.get("uncertain_spend_tolerance") is not None:
            raise Phase3V3LiveError(
                "uncertain-spend tolerance requires the v3 role-limits schema")
    else:
        expected_streaming = {
            model: {"stream": True} for model in expected_reasoning}
    if request.get("streaming_pinned_models") != expected_streaming:
        raise Phase3V3LiveError("streaming model pins differ from the frozen transport policy")
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


def _validate_prior_attempt_accounting(
    binding: Mapping[str, Any], root: Path | None,
) -> dict[str, float]:
    schema_version = binding.get("schema_version")
    if schema_version not in {
            BINDING_SCHEMA_V2, BINDING_SCHEMA_V3, BINDING_SCHEMA_V4, BINDING_SCHEMA_V5,
            BINDING_SCHEMA_V6, BINDING_SCHEMA_V7, BINDING_SCHEMA_V8, BINDING_SCHEMA_V9,
            BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
        return {
            "actual_spend_usd": 0.0,
            "uncertain_spend_usd": 0.0,
            "accounted_spend_usd": 0.0,
        }
    if root is None:
        raise Phase3V3LiveError(
            "aggregate execution binding requires a project root for prior-attempt verification")
    prior = binding.get("prior_attempt_accounting")
    if not isinstance(prior, Mapping):
        raise Phase3V3LiveError("aggregate execution binding has no prior-attempt accounting")

    if schema_version in {
            BINDING_SCHEMA_V3, BINDING_SCHEMA_V4, BINDING_SCHEMA_V5, BINDING_SCHEMA_V6,
            BINDING_SCHEMA_V7, BINDING_SCHEMA_V8, BINDING_SCHEMA_V9, BINDING_SCHEMA_V10,
            BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
        if (prior.get("measurement_rows_reused") != 0
                or float(prior.get("aggregate_cap_usd", -1)) != AUTHORIZED_INCREMENTAL_CAP_USD):
            raise Phase3V3LiveError("prior-attempt aggregate cap or row-reuse policy drifted")
        attempts = prior.get("attempts")
        expected_attempts = (
            {
                "run_id": "phase3-v3-07501cfa62bf55e5",
                "manifest_path": "rejudge/phase3_v3_run_manifest_preflight_r5_2026-08-24.json",
                "halt_path": "rejudge/phase3_v3_r5_provider_halt_2026-08-24.json",
                "ledger_path": (
                    "E:/selvarath-archive/phase3-v3-successor-2026-08-24/"
                    "phase3_v3_usage.jsonl"),
            },
            {
                "run_id": "phase3-v3-b6c0bf895e78d216",
                "manifest_path": "rejudge/phase3_v3_run_manifest_preflight_r7_2026-08-24.json",
                "halt_path": "rejudge/phase3_v3_r7_provider_halt_2026-08-25.json",
                "ledger_path": (
                    "E:/selvarath-archive/phase3-v3r2-qwen38-successor-2026-08-24/"
                    "phase3_v3_usage.jsonl"),
            },
        )
        frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R3
        if schema_version in {
                BINDING_SCHEMA_V4, BINDING_SCHEMA_V5, BINDING_SCHEMA_V6,
                BINDING_SCHEMA_V7, BINDING_SCHEMA_V8, BINDING_SCHEMA_V9,
                BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-ab48e68863878f49",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r8_2026-08-25.json"),
                    "halt_path": R9_HALT_RELATIVE_PATH,
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r3-qwen38-nonstream-2026-08-25/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R4
        if schema_version in {
                BINDING_SCHEMA_V5, BINDING_SCHEMA_V6, BINDING_SCHEMA_V7,
                BINDING_SCHEMA_V8, BINDING_SCHEMA_V9, BINDING_SCHEMA_V10,
                BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-476792b58e273b48",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r10_2026-08-25.json"),
                    "halt_path": (
                        "rejudge/phase3_v3_r11_gemma3n_delisting_halt_2026-08-25.json"),
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r4-uncertain-tolerance-2026-08-25/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R5
        if schema_version in {
                BINDING_SCHEMA_V6, BINDING_SCHEMA_V7, BINDING_SCHEMA_V8,
                BINDING_SCHEMA_V9, BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-120ce58628620fce",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r12_2026-08-25.json"),
                    "halt_path": (
                        "rejudge/phase3_v3_r13_checker_malformed_halt_2026-08-26.json"),
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r5-qwen35-9b-2026-08-25/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R6
        if schema_version in {
                BINDING_SCHEMA_V7, BINDING_SCHEMA_V8, BINDING_SCHEMA_V9,
                BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-3382c17bc4b33909",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r14_2026-08-26.json"),
                    "halt_path": (
                        "rejudge/phase3_v3_r15_uncertain_ceiling_stop_2026-08-26.json"),
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r6-checker-disposition-2026-08-26/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R7
        if schema_version in {
                BINDING_SCHEMA_V8, BINDING_SCHEMA_V9, BINDING_SCHEMA_V10,
                BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-0407589edb2d8a6b",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r16_2026-08-26.json"),
                    "halt_path": (
                        "rejudge/phase3_v3_r17_evening_capacity_stop_2026-08-26.json"),
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r7-raised-ceiling-2026-08-26/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R8
        if schema_version in {BINDING_SCHEMA_V9, BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-25d73d86c66a1e42",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r18_2026-08-26.json"),
                    "halt_path": (
                        "rejudge/phase3_v3_r19_offpeak_pause_stop_2026-08-27.json"),
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r8-offpeak-2026-08-27/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R9
        if schema_version in {BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
            # The ninth chained attempt never launched formally: its identity wedged at
            # the harness's first Qwen3.5-9B cell (empty response, completeness check),
            # which became the r21 empty-verdict discovery. That observation record is
            # the identity's terminal disposition; the sealed archive ledger carries the
            # two harness events.
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-534667eded4165df",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r20_2026-08-27.json"),
                    "halt_path": (
                        "rejudge/phase3_v3_r21_empty_verdict_discovery_2026-08-27.json"),
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r9-final4judge-2026-08-27/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R10
        if schema_version in {BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
            # The tenth chained attempt: the first N=2 canary (harness-verified, 336
            # clean rows, one ratified checker_malformed disposition) went identity-
            # terminal when its resumption exposed the r23 exclusion-filter defect.
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-5cc134ec5730dfaf",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r22_2026-08-27.json"),
                    "halt_path": (
                        "rejudge/phase3_v3_r23_exclusion_filter_defect_2026-08-28.json"),
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r10-n2-2026-08-27/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R11
        if schema_version == BINDING_SCHEMA_V12:
            # The eleventh chained attempt: the second N=2 canary reached 394 clean rows
            # and stopped at the frozen amendment-5 concentration bound when gemma-4's
            # checker runaway hit five sequential_b2 cells (r25 record). The stop bound
            # worked exactly as ratified; the identity is terminal by that bound.
            expected_attempts = expected_attempts + (
                {
                    "run_id": "phase3-v3-d5836a2141cb3736",
                    "manifest_path": (
                        "rejudge/phase3_v3_run_manifest_preflight_r24_2026-08-28.json"),
                    "halt_path": (
                        "rejudge/phase3_v3_r25_concentration_bound_stop_2026-08-28.json"),
                    "ledger_path": (
                        "E:/selvarath-archive/phase3-v3r11-n2-2026-08-28/"
                        "phase3_v3_usage.jsonl"),
                },
            )
            frozen_carry = PRIOR_ACCOUNTED_SPEND_USD_R12
        if not isinstance(attempts, list) or len(attempts) != len(expected_attempts):
            raise Phase3V3LiveError(
                f"prior-attempt chain must contain exactly {len(expected_attempts)} attempts")
        totals = {
            "actual_spend_usd": 0.0,
            "uncertain_spend_usd": 0.0,
            "accounted_spend_usd": 0.0,
        }
        current_ledger = local_path(binding["paths"]["usage_ledger"]).resolve()
        for attempt, expected in zip(attempts, expected_attempts, strict=True):
            if attempt.get("run_id") != expected["run_id"]:
                raise Phase3V3LiveError("prior-attempt chain identity drifted")
            for label, key, expected_path in (
                ("prior run manifest", "run_manifest", expected["manifest_path"]),
                ("halt observation", "halt_observation", expected["halt_path"]),
            ):
                artifact = attempt.get(key) or {}
                if artifact.get("path") != expected_path:
                    raise Phase3V3LiveError(f"{label} path drifted")
                payload = _load_json(root / expected_path)
                if canonical_sha256(payload) != artifact.get("canonical_sha256"):
                    raise Phase3V3LiveError(f"{label} canonical hash drifted")
            ledger = attempt.get("usage_ledger") or {}
            if ledger.get("path") != expected["ledger_path"]:
                raise Phase3V3LiveError("prior usage ledger path drifted")
            ledger_path = local_path(ledger["path"])
            if ledger_path.resolve() == current_ledger:
                raise Phase3V3LiveError("successor and prior attempts cannot share a usage ledger")
            if not ledger_path.is_file() or _raw_sha256(ledger_path) != ledger.get("raw_sha256"):
                raise Phase3V3LiveError("prior usage ledger raw hash drifted")
            snapshot = api_client.load_chained_usage_ledger(ledger_path)
            if snapshot.last_event_hash != ledger.get("last_event_sha256"):
                raise Phase3V3LiveError("prior usage ledger tail hash drifted")
            for field in totals:
                expected_value = float(ledger.get(field, -1))
                if not math.isclose(
                        float(snapshot.summary[field]), expected_value,
                        rel_tol=0.0, abs_tol=1e-15):
                    raise Phase3V3LiveError(f"prior usage ledger {field} drifted")
                totals[field] += expected_value
            if (int(snapshot.summary["unmatched_reservations"])
                    != int(ledger.get("unmatched_reservations", -1))):
                raise Phase3V3LiveError("prior usage ledger reservation count drifted")
        frozen_totals = prior.get("totals") or {}
        for field, observed in totals.items():
            if not math.isclose(
                    float(frozen_totals.get(field, -1)), observed,
                    rel_tol=0.0, abs_tol=1e-15):
                raise Phase3V3LiveError(f"prior-attempt total {field} drifted")
        if not math.isclose(
                totals["accounted_spend_usd"], frozen_carry,
                rel_tol=0.0, abs_tol=1e-15):
            raise Phase3V3LiveError(
                "prior accounted spend differs from the frozen successor carry")
        expected_remaining = AUTHORIZED_INCREMENTAL_CAP_USD - frozen_carry
        if not math.isclose(
                float(prior.get("remaining_before_successor_usd", -1)), expected_remaining,
                rel_tol=0.0, abs_tol=1e-12):
            raise Phase3V3LiveError("prior remaining aggregate cap drifted")
        return totals

    if (prior.get("run_id") != "phase3-v3-07501cfa62bf55e5"
            or prior.get("measurement_rows_reused") != 0
            or float(prior.get("aggregate_cap_usd", -1)) != AUTHORIZED_INCREMENTAL_CAP_USD):
        raise Phase3V3LiveError("prior-attempt identity or aggregate cap drifted")
    manifest_binding = prior.get("run_manifest") or {}
    halt_binding = prior.get("halt_observation") or {}
    for label, artifact, expected_path in (
        ("prior run manifest", manifest_binding,
         "rejudge/phase3_v3_run_manifest_preflight_r5_2026-08-24.json"),
        ("halt observation", halt_binding,
         "rejudge/phase3_v3_r5_provider_halt_2026-08-24.json"),
    ):
        if artifact.get("path") != expected_path:
            raise Phase3V3LiveError(f"{label} path drifted")
        payload = _load_json(root / expected_path)
        if canonical_sha256(payload) != artifact.get("canonical_sha256"):
            raise Phase3V3LiveError(f"{label} canonical hash drifted")

    ledger = prior.get("usage_ledger") or {}
    ledger_path = local_path(ledger.get("path"))
    if not ledger_path.is_file() or _raw_sha256(ledger_path) != ledger.get("raw_sha256"):
        raise Phase3V3LiveError("prior usage ledger raw hash drifted")
    snapshot = api_client.load_chained_usage_ledger(ledger_path)
    if snapshot.last_event_hash != ledger.get("last_event_sha256"):
        raise Phase3V3LiveError("prior usage ledger tail hash drifted")
    expected_summary = {
        "actual_spend_usd": float(ledger.get("actual_spend_usd", -1)),
        "uncertain_spend_usd": float(ledger.get("uncertain_spend_usd", -1)),
        "accounted_spend_usd": float(ledger.get("accounted_spend_usd", -1)),
    }
    for field, expected in expected_summary.items():
        if not math.isclose(
                float(snapshot.summary[field]), expected, rel_tol=0.0, abs_tol=1e-15):
            raise Phase3V3LiveError(f"prior usage ledger {field} drifted")
    if (int(snapshot.summary["unmatched_reservations"])
            != int(ledger.get("unmatched_reservations", -1))):
        raise Phase3V3LiveError("prior usage ledger reservation count drifted")
    if not math.isclose(
            expected_summary["accounted_spend_usd"], PRIOR_ACCOUNTED_SPEND_USD,
            rel_tol=0.0, abs_tol=1e-15):
        raise Phase3V3LiveError("prior accounted spend differs from the frozen carry")
    expected_remaining = AUTHORIZED_INCREMENTAL_CAP_USD - PRIOR_ACCOUNTED_SPEND_USD
    if not math.isclose(
            float(prior.get("remaining_before_successor_usd", -1)), expected_remaining,
            rel_tol=0.0, abs_tol=1e-12):
        raise Phase3V3LiveError("prior remaining aggregate cap drifted")
    if local_path(binding["paths"]["usage_ledger"]).resolve() == ledger_path.resolve():
        raise Phase3V3LiveError("successor and prior attempts cannot share a usage ledger")
    return expected_summary


def _validate_execution_binding(
    binding: Mapping[str, Any], manifest: Mapping[str, Any], protocol: Mapping[str, Any],
    *, root: Path | None = None,
) -> None:
    if binding.get("schema_version") not in {
            BINDING_SCHEMA, BINDING_SCHEMA_V2, BINDING_SCHEMA_V3, BINDING_SCHEMA_V4,
            BINDING_SCHEMA_V5, BINDING_SCHEMA_V6, BINDING_SCHEMA_V7, BINDING_SCHEMA_V8,
            BINDING_SCHEMA_V9, BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}:
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
    expected_rows = _expected_row_counts(protocol)
    if inventory != {
        "canary_transcript_rows": expected_rows["transcript"],
        "fresh_judgment_rows": expected_rows["judgment"],
        "fresh_capability_rows": expected_rows["capability"],
        "fresh_gate_rows": expected_rows["fresh_gate"],
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
    expected_formal = {
        "provider_max_workers": 1,
        "pending_payload_limit": 64,
        "reviewer_model": "gpt-5.6-sol",
        "reviewer_reasoning_effort": "high",
        "reviewer_concurrency": 12,
        "transcript_generation_forbidden": True,
        ("shared_aggregate_cap_accounting"
         if binding.get("schema_version") in {
             BINDING_SCHEMA_V2, BINDING_SCHEMA_V3, BINDING_SCHEMA_V4, BINDING_SCHEMA_V5,
             BINDING_SCHEMA_V6, BINDING_SCHEMA_V7, BINDING_SCHEMA_V8, BINDING_SCHEMA_V9,
             BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}
         else "shared_incremental_cap_ledger"): True,
    }
    if formal != expected_formal:
        raise Phase3V3LiveError("formal execution settings drifted")
    expected_state = api_client.usage_ledger_state_path(local_path(paths["usage_ledger"]))
    if local_path(paths["usage_state"]).resolve() != expected_state.resolve():
        raise Phase3V3LiveError("execution binding names the wrong usage-ledger state path")
    _validate_prior_attempt_accounting(binding, root)


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
    input_paths = _validate_all_input_hashes(manifest, root)
    protocol = phase3_plan.load_protocol(root / input_paths["protocol"])
    pin = _load_json(root / input_paths["protocol_pin"])
    tokenizer = _load_json(root / input_paths["tokenizer_manifest"])
    prices = _load_json(root / input_paths["price_snapshot"])
    phase3_v3_run_manifest.validate_run_manifest(
        manifest, protocol=protocol, protocol_pin=pin, tokenizer_manifest=tokenizer,
        price_snapshot=prices, project_root=root, verify_external_files=True)
    if verify_git:
        _verify_execution_code_commit(manifest, root)
    role_limits = _load_json(root / input_paths["role_limits"])
    _validate_role_limits(role_limits, protocol)
    _validate_contexts_against_catalog(role_limits, prices, root)
    binding = _load_json(root / input_paths["execution_binding"])
    _validate_execution_binding(binding, manifest, protocol, root=root)
    _validate_runtime_toolchain(manifest, binding, root)
    authorization = validate_authorization(
        _load_json(authorization_path), manifest, manifest_path=manifest_path,
        protocol=protocol, protocol_relative_path=input_paths["protocol"],
        prior_accounted_spend_usd=_validate_prior_attempt_accounting(binding, root)[
            "accounted_spend_usd"])
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
        "input_paths": input_paths,
        "paths": _binding_paths(binding),
    }
    validate_ledger(context)
    if require_harness:
        context["harness_manifest"] = load_harness_manifest(context)
    return context


def validate_authorization(
    authorization: Mapping[str, Any], manifest: Mapping[str, Any], *,
    manifest_path: Path, protocol: Mapping[str, Any],
    protocol_relative_path: str = PROTOCOL_RELATIVE_PATH,
    prior_accounted_spend_usd: float = 0.0,
) -> dict[str, Any]:
    _authorization_recorded_at(authorization)
    schema_version = authorization.get("schema_version")
    if schema_version not in {AUTHORIZATION_SCHEMA, AUTHORIZATION_SCHEMA_V2}:
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
    if binds.get("protocol_tracked_path") != protocol_relative_path:
        raise Phase3V3LiveError("authorization binds a different protocol path")
    seed_name = manifest["harness_check"]["seed_name"]
    if (binds.get("harness_seed_name") != seed_name
            or binds.get("harness_seed") != manifest["seeds"][seed_name]):
        raise Phase3V3LiveError("authorization binds a different harness seed")
    scope = authorization.get("scope") or {}
    if schema_version == AUTHORIZATION_SCHEMA_V2:
        cap = scope.get("aggregate_cap_usd")
        if "incremental_cap_usd" in scope:
            raise Phase3V3LiveError("aggregate authorization cannot reset an incremental cap")
        if not math.isclose(
                float(scope.get("prior_accounted_spend_usd", -1)),
                prior_accounted_spend_usd, rel_tol=0.0, abs_tol=1e-15):
            raise Phase3V3LiveError("authorization prior accounted spend drifted")
    else:
        cap = scope.get("incremental_cap_usd")
    if (isinstance(cap, bool) or not isinstance(cap, (int, float))
            or float(cap) != AUTHORIZED_INCREMENTAL_CAP_USD):
        raise Phase3V3LiveError(
            f"authorization cap must equal ${AUTHORIZED_INCREMENTAL_CAP_USD:.2f}")
    if (scope.get("harness_execution_count") != EXPECTED_HARNESS_EXECUTIONS
            or scope.get("formal_successor_canary_execution_count") != 1
            or scope.get("successor_canary_fresh_gate_slots") != (
                _expected_row_counts(protocol)["fresh_gate"])
            or scope.get("gpu_ordinal_or_not_used") != "not_used"):
        raise Phase3V3LiveError("authorization scope differs from the successor canary")
    expected_text = (
        f"Approved: {manifest['run_id']} harness and successor canary, $60 USD aggregate "
        f"cap including ${prior_accounted_spend_usd:.8f} prior accounted spend, no main spend"
        if schema_version == AUTHORIZATION_SCHEMA_V2 else
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
    # Amendment 3 (2026-08-25, r9 halt evidence): under the v4 binding a LIVE run-local
    # ledger may carry terminal unknown_charge events, whose reserved cost stays booked as
    # uncertain spend, counts fully against the aggregate cap, and is bounded by the frozen
    # per-run ceiling. A FRESH ledger (genesis only) must still be exactly zero. Earlier
    # binding schemas keep the original zero-uncertain rule so sealed history validates
    # under the policy it ran under.
    tolerant = (
        binding.get("schema_version") in {
            BINDING_SCHEMA_V4, BINDING_SCHEMA_V5, BINDING_SCHEMA_V6, BINDING_SCHEMA_V7,
            BINDING_SCHEMA_V8, BINDING_SCHEMA_V9, BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}
        and int(snapshot.summary["events"]) > 0)
    uncertain = float(snapshot.summary["uncertain_spend_usd"])
    if tolerant:
        ceiling = float(
            ((context.get("role_limits") or {}).get("uncertain_spend_tolerance") or {})
            .get("run_uncertain_ceiling_usd", RUN_UNCERTAIN_CEILING_USD))
        if int(snapshot.summary["unmatched_reservations"]) != 0:
            raise Phase3V3LiveError(
                "usage ledger contains an open reservation; every ambiguous in-flight "
                "attempt must reach a conservative terminal state before validation")
        if uncertain > ceiling:
            raise Phase3V3LiveError(
                f"run-local uncertain spend ${uncertain:.8f} exceeds the frozen "
                f"${ceiling:.2f} ceiling; owner review required")
    elif uncertain != 0:
        raise Phase3V3LiveError("usage ledger contains unresolved uncertain spend")
    for event in _ledger_events(path):
        status = event.get("status")
        if status == "unknown_charge" and not tolerant:
            raise Phase3V3LiveError("usage ledger contains an unknown charge")
        if status == "charged_malformed":
            raise Phase3V3LiveError("usage ledger contains a charged malformed response")
        if status == "success":
            returned = (event.get("response_metadata") or {}).get("returned_model_id")
            if returned != event.get("model"):
                raise Phase3V3LiveError(
                    f"usage ledger contains unresolved returned-model drift: requested "
                    f"{event.get('model')!r}, returned {returned!r}")
    _validate_prior_attempt_accounting(
        binding, Path(context["root"]) if context.get("root") is not None else None)
    return snapshot


def _authorized_cap(context: Mapping[str, Any]) -> float:
    scope = context["authorization"]["scope"]
    key = (
        "aggregate_cap_usd"
        if context["authorization"].get("schema_version") == AUTHORIZATION_SCHEMA_V2
        else "incremental_cap_usd")
    return float(scope[key])


def aggregate_accounting_summary(
    context: Mapping[str, Any],
    snapshot: api_client.UsageLedgerSnapshot | None = None,
) -> dict[str, float | int]:
    current = snapshot or validate_ledger(context)
    prior = _validate_prior_attempt_accounting(
        context["binding"],
        Path(context["root"]) if context.get("root") is not None else None,
    )
    current_summary = current.summary
    return {
        "prior_actual_spend_usd": prior["actual_spend_usd"],
        "prior_uncertain_spend_usd": prior["uncertain_spend_usd"],
        "prior_accounted_spend_usd": prior["accounted_spend_usd"],
        "successor_actual_spend_usd": float(current_summary["actual_spend_usd"]),
        "successor_uncertain_spend_usd": float(current_summary["uncertain_spend_usd"]),
        "successor_accounted_spend_usd": float(current_summary["accounted_spend_usd"]),
        "aggregate_actual_spend_usd": (
            prior["actual_spend_usd"] + float(current_summary["actual_spend_usd"])),
        "aggregate_uncertain_spend_usd": (
            prior["uncertain_spend_usd"]
            + float(current_summary["uncertain_spend_usd"])),
        "aggregate_accounted_spend_usd": (
            prior["accounted_spend_usd"]
            + float(current_summary["accounted_spend_usd"])),
        "successor_events": int(current_summary["events"]),
        "successor_unmatched_reservations": int(
            current_summary["unmatched_reservations"]),
    }


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
    accounting = aggregate_accounting_summary(context, snapshot)
    role_limits = context["role_limits"]
    request = role_limits["request_settings"]
    transport = request["transport"]
    cap = _authorized_cap(context)
    spent = float(accounting["aggregate_accounted_spend_usd"])
    if spent >= cap:
        raise Phase3V3LiveError(
            f"accounted aggregate spend ${spent:.4f} has reached the ${cap:.2f} cap")
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
        initial_spend_usd=float(accounting["aggregate_actual_spend_usd"]),
        initial_uncertain_spend_usd=float(
            accounting["aggregate_uncertain_spend_usd"]),
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
        **(
            {
                "run_uncertain_ceiling_usd": float(
                    role_limits["uncertain_spend_tolerance"][
                        "run_uncertain_ceiling_usd"]),
                "initial_run_uncertain_spend_usd": float(
                    accounting["successor_uncertain_spend_usd"]),
            }
            if context["binding"].get("schema_version") in {
                BINDING_SCHEMA_V4, BINDING_SCHEMA_V5, BINDING_SCHEMA_V6,
                BINDING_SCHEMA_V7, BINDING_SCHEMA_V8, BINDING_SCHEMA_V9,
                BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}
            else {}
        ),
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
        "accounted_spend_usd": aggregate_accounting_summary(context)[
            "aggregate_accounted_spend_usd"],
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
        protocol_path=context["root"] / context["input_paths"]["protocol"],
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
    if (len(capability_keys) != _expected_row_counts(context["protocol"])["capability"]
            or not capability_keys <= observed_after):
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
    expected_rows = _expected_row_counts(context["protocol"])
    if counts != {
        phase3_plan.CANARY_TRANSCRIPT_KIND: expected_rows["transcript"],
        phase3_plan.CANARY_JUDGMENT_KIND: expected_rows["judgment"],
        phase3_plan.CAPABILITY_ANCHOR_KIND: expected_rows["capability"],
    }:
        raise Phase3V3LiveError(f"formal canary plan inventory drifted: {counts}")
    bundle = _load_json(context["root"] / PROMPT_BUNDLE_RELATIVE_PATH)
    judgments, capabilities = phase3_runner.resolve_canary_cells(
        plan, protocol=context["protocol"], bundle=bundle)
    partition = terminal_partition(
        context,
        load_terminal_halt_records(context, CellResultStore(formal_results)))
    terminal_cells = partition["terminal_cells"]
    if terminal_cells:
        # judgments holds phase3_runner.ResolvedCell objects (attribute access), not the
        # plan's dict cells. The first live exercise of this exclusion path (run
        # 5cc134ec5730dfaf, r23) wedged on dict indexing here; the regression test drives
        # this filter with real resolved cells.
        judgments = [
            cell for cell in judgments
            if str(cell.cell_key) not in terminal_cells]
        _append_jsonl(context["paths"]["run_log"], {
            "event": "terminal_halt_exclusions_loaded",
            "recorded_at_utc": _utc_now(),
            "excluded_cell_count": len(terminal_cells),
            "affected_mirror_units": len(partition["affected_units"]),
        })
    convergence_target = len(plan) - len(terminal_cells)
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
            "accounted_spend_usd": aggregate_accounting_summary(context)[
                "aggregate_accounted_spend_usd"],
        })
        spend = aggregate_accounting_summary(context)
        print(json.dumps({
            "pass": pass_index, "complete": complete, "planned": len(plan),
            "pending_labels": len(outcome.pending_payloads),
            "spend_usd": spend["aggregate_accounted_spend_usd"],
        }, sort_keys=True), flush=True)
        if complete == convergence_target:
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
    terminal_cells: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    b0 = [cell for cell in plan if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND
          and cell["condition"] == "b0"]
    report = {}
    for judge in roster:
        cells = [cell for cell in b0 if cell["judge_model"] == judge]
        invalid = 0
        terminal_invalid = 0
        for cell in cells:
            key = str(cell["cell_key"])
            if key in terminal_cells:
                # Frozen missing-data policy: a terminally halted cell counts INVALID
                # (which counts wrong); it never has a result row to parse.
                invalid += 1
                terminal_invalid += 1
                continue
            invalid += store.get(key)["verdict_strict"]["verdict"] is None
        report[judge] = {
            "slots": len(cells), "invalid": invalid,
            "terminal_invalid": terminal_invalid,
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


def _uncertain_event_report(
    ledger_events: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Summarize every terminal unknown_charge event for the final report (amendment 3).

    Each entry names the cell it deferred and the success attempt that ultimately completed
    that cell, so an auditor can confirm no measurement row depends on a billing-uncertain
    call. The by-condition counts expose retry clustering by experimental arm (a
    content-dependent failure pattern would otherwise hide a retry-selection effect).
    """
    success_by_cell: dict[str, str] = {}
    for event in ledger_events:
        if event.get("status") == "success":
            cell = (event.get("metadata") or {}).get("cell_key")
            if isinstance(cell, str):
                success_by_cell[cell] = str(event.get("attempt_id"))
    uncertain_events = [
        {
            "model": event.get("model"),
            "cell_key": (event.get("metadata") or {}).get("cell_key"),
            "condition": (event.get("metadata") or {}).get("condition"),
            "attempt_id": event.get("attempt_id"),
            "ts": event.get("ts"),
            "reserved_cost_usd": event.get("cost_usd"),
            "error": event.get("error"),
            "completing_success_attempt_id": success_by_cell.get(
                (event.get("metadata") or {}).get("cell_key")),
        }
        for event in ledger_events if event.get("status") == "unknown_charge"
    ]
    uncertain_by_condition: dict[str, int] = {}
    for entry in uncertain_events:
        key = str(entry["condition"])
        uncertain_by_condition[key] = uncertain_by_condition.get(key, 0) + 1
    return uncertain_events, uncertain_by_condition


def load_terminal_halt_records(
    context: Mapping[str, Any], store: CellResultStore,
    *, records_directory: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load, validate, and bound the append-only terminal-halt dispositions for this run.

    Never a discretionary skip list (amendment 5, Codex-corrected): every record must bind
    this exact run identity, name a planned judgment cell with no result row, carry the
    checker-failure evidence, and cite the frozen missing-data policy. Records for other run
    identities are prior-attempt evidence and are ignored here.
    """
    root = Path(context["root"])
    directory = (
        Path(records_directory) if records_directory is not None else root / "rejudge")
    run_id = str(context["manifest"]["run_id"])
    plan_by_key = {str(cell["cell_key"]): cell for cell in _canary_plan(context)}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("phase3_v3_terminal_halts_*.json")):
        record = _load_json(path)
        if not isinstance(record, dict) or record.get("run_id") != run_id:
            continue
        if record.get("schema_version") != TERMINAL_HALTS_SCHEMA:
            raise Phase3V3LiveError(f"unsupported terminal-halts schema in {path.name}")
        cell_key = str(record.get("cell_key"))
        cell = plan_by_key.get(cell_key)
        if cell is None or cell["kind"] != phase3_plan.CANARY_JUDGMENT_KIND:
            raise Phase3V3LiveError(
                f"terminal-halt record {path.name} names an unplanned or non-judgment cell")
        if record.get("reason") != "checker_malformed":
            raise Phase3V3LiveError(
                f"terminal-halt record {path.name} names a reason outside the frozen "
                "disposition")
        evidence = record.get("evidence") or {}
        for field in ("ledger_attempt_id", "ledger_event_sha256", "finish_reason",
                      "completion_tokens", "parse_failure"):
            if evidence.get(field) in (None, ""):
                raise Phase3V3LiveError(
                    f"terminal-halt record {path.name} is missing evidence field {field}")
        for field in ("reviewer", "recorded_at_utc", "frozen_policy_citation"):
            if not record.get(field):
                raise Phase3V3LiveError(
                    f"terminal-halt record {path.name} is missing {field}")
        if cell_key in seen:
            raise Phase3V3LiveError(
                f"duplicate terminal-halt records name cell {cell_key}")
        if cell_key in set(store._results):
            raise Phase3V3LiveError(
                "a terminally halted cell already has a result row; the disposition "
                f"in {path.name} is stale")
        seen.add(cell_key)
        records.append(dict(record))
    if len(records) > MAX_TERMINAL_JUDGMENT_CELLS:
        raise Phase3V3LiveError(
            f"{len(records)} terminal judgment cells exceed the frozen bound of "
            f"{MAX_TERMINAL_JUDGMENT_CELLS}; owner review required")
    return records


def _mirror_unit_index(
    context: Mapping[str, Any],
) -> tuple[dict[str, tuple], dict[tuple, list[str]]]:
    protocol = context["protocol"]
    judges = list(context["manifest"]["final_roster"])
    _main, held_out = phase3_plan.load_reference_question_ids(
        protocol, context["root"])
    plan_index = phase3_polarity_verify.build_plan_index(protocol, judges, held_out)
    replicates = phase3_polarity_verify.condition_replicates(protocol)
    unit_of: dict[str, tuple] = {}
    cells_of: dict[tuple, list[str]] = {}
    for cell_key, cell in plan_index.items():
        reps = int(replicates[str(cell["condition"])])
        within = int(cell["replicate_index"]) % reps
        unit = (str(cell["question_id"]), str(cell["judge_model"]),
                str(cell["debater_model"]), cell.get("transcript_index"),
                str(cell["condition"]), within)
        unit_of[cell_key] = unit
        cells_of.setdefault(unit, []).append(cell_key)
    return unit_of, cells_of


def terminal_partition(
    context: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Partition support: terminal cells, their mirror-unit overlay, and the stop bounds."""
    plan = _canary_plan(context)
    plan_by_key = {str(cell["cell_key"]): cell for cell in plan}
    judgment_cells = [
        cell for cell in plan if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND]
    terminal = frozenset(str(record["cell_key"]) for record in records)
    unit_of, cells_of = _mirror_unit_index(context)
    affected_units = {unit_of[key] for key in terminal}
    affected_cells = frozenset(
        key for unit in affected_units for key in cells_of[unit])
    total_units = len(cells_of)
    if total_units and len(affected_units) / total_units > (
            MAX_AFFECTED_MIRROR_UNIT_FRACTION):
        raise Phase3V3LiveError(
            f"{len(affected_units)} affected mirror units exceed the frozen "
            f"{MAX_AFFECTED_MIRROR_UNIT_FRACTION:.0%} bound; owner review required")
    for group_field in ("judge_model", "condition"):
        exposure: dict[str, int] = {}
        hits: dict[str, int] = {}
        for cell in judgment_cells:
            group = str(cell[group_field])
            exposure[group] = exposure.get(group, 0) + 1
        for key in terminal:
            group = str(plan_by_key[key][group_field])
            hits[group] = hits.get(group, 0) + 1
        for group, count in hits.items():
            if count < CONCENTRATION_MIN_CELLS:
                continue
            rate = count / exposure[group]
            complement_hits = sum(hits.values()) - count
            complement_exposure = sum(exposure.values()) - exposure[group]
            complement_rate = (
                complement_hits / complement_exposure if complement_exposure else 0.0)
            if rate > CONCENTRATION_RATE or rate > CONCENTRATION_RATIO * complement_rate:
                raise Phase3V3LiveError(
                    f"terminal-cell concentration bound crossed for {group_field}="
                    f"{group}; owner review required")
    return {
        "terminal_cells": terminal,
        "affected_unit_cells": affected_cells,
        "affected_units": affected_units,
        "total_mirror_units": total_units,
        "causally_excluded": frozenset(),
    }


def _checker_truncation_diagnostic(
    context: Mapping[str, Any], plan: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Pre-registered checker diagnostic (amendment 5): truncation rates and clustering."""
    plan_by_key = {str(cell["cell_key"]): cell for cell in plan}
    total = 0
    length_calls: list[dict[str, Any]] = []
    by: dict[str, dict[str, dict[str, int]]] = {"judge_model": {}, "condition": {}}
    bands: dict[str, dict[str, int]] = {}
    for event in _ledger_events(context["paths"]["usage_ledger"]):
        metadata = event.get("metadata") or {}
        if metadata.get("call_role") != "query_checker" or event.get("status") != "success":
            continue
        total += 1
        response_metadata = event.get("response_metadata") or {}
        is_length = response_metadata.get("finish_reason") == "length"
        prompt_tokens = event.get("prompt_tokens") or 0
        band = ("<2k" if prompt_tokens < 2048 else "2-4k" if prompt_tokens < 4096
                else "4-8k" if prompt_tokens < 8192 else ">=8k")
        band_entry = bands.setdefault(band, {"checker_calls": 0, "length": 0})
        band_entry["checker_calls"] += 1
        band_entry["length"] += int(is_length)
        cell = plan_by_key.get(metadata.get("cell_key"))
        if is_length:
            length_calls.append({
                "cell_key": metadata.get("cell_key"),
                "completion_tokens": event.get("completion_tokens"),
                "ts": event.get("ts"),
            })
        if cell is not None:
            for group_field in ("judge_model", "condition"):
                group = str(cell[group_field])
                entry = by[group_field].setdefault(
                    group, {"checker_calls": 0, "length": 0})
                entry["checker_calls"] += 1
                entry["length"] += int(is_length)
    return {
        "checker_calls_total": total,
        "finish_length_calls": length_calls,
        "finish_length_count": len(length_calls),
        "finish_length_rate": (len(length_calls) / total) if total else None,
        "by_judge": by["judge_model"],
        "by_condition": by["condition"],
        "by_prompt_token_band": bands,
        "non_claim": (
            "truncation is not assumed missing completely at random; difficult or "
            "lengthy cases may be likelier to trigger it"),
    }


def audit_and_finalize(context: Mapping[str, Any]) -> dict[str, Any]:
    harness_manifest = load_harness_manifest(context)
    plan = _canary_plan(context)
    store = CellResultStore(context["paths"]["formal_results"])
    terminal_records = load_terminal_halt_records(context, store)
    partition = terminal_partition(context, terminal_records)
    terminal_cells = partition["terminal_cells"]
    expected_keys = {str(cell["cell_key"]) for cell in plan}
    observed_keys = set(store._results)
    # Amendment 5: every planned cell is partitioned exactly once as completed, terminal
    # INVALID, or causally excluded (the loader guarantees terminal cells have no rows).
    if observed_keys != expected_keys - terminal_cells:
        raise Phase3V3LiveError(
            "formal result set differs from plan minus frozen exclusions: "
            f"missing={len((expected_keys - terminal_cells) - observed_keys)}, "
            f"extra={len(observed_keys - (expected_keys - terminal_cells))}")
    expected_total_rows = _expected_row_counts(context["protocol"])["total"]
    if len(observed_keys) + len(terminal_cells) != expected_total_rows:
        raise Phase3V3LiveError(
            f"formal partition does not cover the {expected_total_rows}-row canary")
    _usage_scope_check(context, plan)
    snapshot = validate_ledger(context)
    accounting = aggregate_accounting_summary(context, snapshot)
    cap = _authorized_cap(context)
    if float(accounting["aggregate_accounted_spend_usd"]) > cap:
        raise Phase3V3LiveError("accounted spend exceeds the authorized aggregate cap")
    CallCache(
        context["paths"]["formal_cache"],
        execution_identity=f"{context['manifest']['run_id']}:formal")
    DualGateDecisionStore(context["paths"]["formal_decisions"])

    rows = _load_result_rows(context["paths"]["formal_results"])
    _main_ids, held_out = phase3_plan.load_reference_question_ids(
        context["protocol"], context["root"])
    question_bank = phase3_polarity_verify._load_question_bank()
    # Amendment 5 mirror overlay: an affected mirror unit contributes nothing to the paired
    # polarity analyses; its independent partner rows keep their primary outcomes and stay
    # in the result store untouched.
    polarity_rows = [
        row for row in rows
        if row.get("cell_key") not in partition["affected_unit_cells"]]
    full_polarity = phase3_polarity_verify.verify(
        polarity_rows, protocol=context["protocol"],
        judges=context["manifest"]["final_roster"],
        held_out_ids=held_out, question_bank=question_bank)
    b0_keys = {
        str(cell["cell_key"]) for cell in plan
        if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND and cell["condition"] == "b0"}
    b0_rows = [row for row in polarity_rows if row.get("cell_key") in b0_keys]
    b0_polarity = phase3_polarity_verify.verify(
        b0_rows, protocol=context["protocol"], judges=context["manifest"]["final_roster"],
        held_out_ids=held_out, question_bank=question_bank)
    polarity_problems = phase3_orchestrator_support.evaluate_polarity_gate(
        full_polarity, b0_polarity)
    invalid = _invalid_gate(
        store, plan, context["manifest"]["final_roster"],
        terminal_cells=terminal_cells)
    capability = _capability_anchor_diagnostics(
        store, plan, context["manifest"]["final_roster"])
    paired_position = _paired_position_diagnostics(
        rows, plan, context["protocol"], question_bank,
        context["manifest"]["final_roster"])
    tolerant_binding = context["binding"].get("schema_version") in {
        BINDING_SCHEMA_V4, BINDING_SCHEMA_V5, BINDING_SCHEMA_V6, BINDING_SCHEMA_V7,
        BINDING_SCHEMA_V8, BINDING_SCHEMA_V9, BINDING_SCHEMA_V10, BINDING_SCHEMA_V11, BINDING_SCHEMA_V12}
    run_ceiling = float(
        ((context.get("role_limits") or {}).get("uncertain_spend_tolerance") or {})
        .get("run_uncertain_ceiling_usd", RUN_UNCERTAIN_CEILING_USD))
    uncertain_events, uncertain_by_condition = _uncertain_event_report(
        _ledger_events(context["paths"]["usage_ledger"]))
    uncertain_by_model: dict[str, dict[str, float | int]] = {}
    for entry in uncertain_events:
        model_entry = uncertain_by_model.setdefault(
            str(entry["model"]), {"events": 0, "reserved_usd": 0.0})
        model_entry["events"] += 1
        model_entry["reserved_usd"] += float(entry["reserved_cost_usd"] or 0.0)
    successor_uncertain = float(accounting["successor_uncertain_spend_usd"])
    ledger_pass = (
        float(accounting["aggregate_accounted_spend_usd"]) <= cap
        and (successor_uncertain == 0
             or (tolerant_binding and successor_uncertain <= run_ceiling))
        and all(entry["completing_success_attempt_id"] for entry in uncertain_events))
    gates = {
        "completion": {
            "expected_rows": expected_total_rows,
            "observed_rows": len(observed_keys),
            "terminal_invalid_rows": len(terminal_cells),
            "causally_excluded_rows": len(partition["causally_excluded"]),
            "partition_exact": True,
            "label": ("PASS_WITH_FROZEN_EXCLUSIONS" if terminal_cells else "PASS"),
            "terminal_records": [
                {"cell_key": record["cell_key"], "reason": record["reason"],
                 "recorded_at_utc": record["recorded_at_utc"],
                 "reviewer": record["reviewer"]}
                for record in terminal_records],
            "pass": True},
        "structural_mirroring": {"pass": not polarity_problems,
                                 "problems": polarity_problems,
                                 "mirror_unit_overlay": {
                                     "total_planned_units": partition[
                                         "total_mirror_units"],
                                     "affected_units": len(partition["affected_units"]),
                                     "retained_units": (
                                         partition["total_mirror_units"]
                                         - len(partition["affected_units"])),
                                     "note": (
                                         "affected units are excluded from the paired "
                                         "polarity analyses as complete units; their "
                                         "independent partner rows keep their primary "
                                         "outcomes")},
                                 "full": full_polarity, "b0": b0_polarity},
        "strict_invalid_per_judge": invalid,
        "ledger": {**accounting, "aggregate_cap_usd": cap,
                   "run_uncertain_ceiling_usd": (
                       run_ceiling if tolerant_binding else None),
                   "accounting_label": (
                       "PASS_CLEAN" if ledger_pass and successor_uncertain == 0
                       else "PASS_CONSERVATIVE_UNCERTAIN" if ledger_pass
                       else "FAIL"),
                   "uncertain_event_count": len(uncertain_events),
                   "uncertain_events": uncertain_events,
                   "uncertain_events_by_condition": uncertain_by_condition,
                   "uncertain_events_by_model": uncertain_by_model,
                   "uncertain_accounting_basis": (
                       "every figure is the worst-case reservation BOOKED at decision "
                       "time; provider billing reconciliation may later mark events "
                       "confirmed billed, credited, or unresolved in an addendum, and "
                       "never revises sealed history"),
                   "pass": ledger_pass},
        "main_spend": {"authorized": False, "observed_main_usage_events": 0, "pass": True},
    }
    gate_failures = []
    if polarity_problems:
        gate_failures.append("structural_mirroring")
    if not all(entry["pass"] for entry in invalid.values()):
        gate_failures.append("strict_invalid_per_judge")
    if not ledger_pass:
        gate_failures.append("ledger")

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
            "checker_truncation": _checker_truncation_diagnostic(context, plan),
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
        "accounted_spend_usd": accounting["aggregate_accounted_spend_usd"],
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
