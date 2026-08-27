"""Deterministic offline materialization for the N=2 raised-budget successor identity.

Third recovery generation (2026-08-27): the r21 empty-verdict discovery showed thinking
judges exhausting the frozen 4,096-token effective completion cap on judgment-shaped
prompts, which structurally failed the strict INVALID gate in every four-judge attempt.
Judgment-shaped screening then established: the weak slot is unfillable (Qwen3.5-9B
degenerate at 8,192 and 16,384; gemma-4-E4B not serverless-servable; gemma-3n delisted);
Qwen3.8 is genuinely bounded and admitted at a screened 16,384 verdict budget; and
gemma-4-31B carries a rare per-prompt-deterministic runaway mode that no cap contains,
failing verdict admission at every screened cap while passing its checker role 0-of-96.
Codex ruled a post-hoc exemption gate-weakening, so the owner-approved amendment 8
rebuilds N=2 (Llama + Qwen3.8) with gemma-4 retained checker-only. This module mirrors
:mod:`rejudge.phase3_v3_recovery2_materialization` for that event.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase3_plan, phase3_v3_materialization
from rejudge.phase2_execution import canonical_sha256


PRIOR_PROTOCOL_PATH = Path("rejudge/phase3_protocol_v3_r4.json")
AMENDMENT_PATH = Path("rejudge/phase3_v3_amendment8_n2_roster_2026-08-27.json")
DISCOVERY_PATH = Path("rejudge/phase3_v3_r21_empty_verdict_discovery_2026-08-27.json")
SCREEN_PLAN_PATH = Path("rejudge/phase3_v3_judgment_screen_plan_2026-08-27.json")
SCREEN_RESULTS_PATH = Path(
    "rejudge/phase3_v3_judgment_screen_results_record_2026-08-27.json")
DEFAULT_PROTOCOL_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_r5.json")
DEFAULT_PROTOCOL_PIN_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_pin_r5.json")

PRIOR_PROTOCOL_CANONICAL_SHA256 = phase3_plan.FROZEN_PROTOCOL_V3_R4_CANONICAL_SHA256
DISCOVERY_CANONICAL_SHA256 = phase3_plan.FROZEN_V3_R21_DISCOVERY_CANONICAL_SHA256
SCREEN_PLAN_CANONICAL_SHA256 = phase3_plan.FROZEN_JUDGMENT_SCREEN_PLAN_CANONICAL_SHA256

REMOVED_MODELS = phase3_plan.PHASE3_V3_RECOVERY3_REMOVED_JUDGES
CHECKER_ONLY_MODEL = phase3_plan.PHASE3_V3_RECOVERY3_CHECKER_ONLY_MODEL
FINAL_ROSTER = phase3_plan.PHASE3_V3_RECOVERY3_BASE_JUDGES
# R9 carry plus the wedged ninth identity's sealed ledger ($0.00130943, run
# phase3-v3-534667eded4165df), matching phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD_R10.
PRIOR_ACCOUNTED_SPEND_USD = 30.527867869999987
APPROVAL_OPTIONS = (
    "Full path (Recommended)",
    "N=2 rebuild (Codex ruling)",
)
VERDICT_BUDGET_RAISE = {
    "Qwen/Qwen3.8-2.4T-A95B": 16384,
}
JUDGMENT_SLOTS_PER_JUDGE = 4920
CANARY_JUDGMENT_SLOTS_PER_JUDGE = 192
CANARY_CAPABILITY_SLOTS_PER_JUDGE = 48
CANARY_COMBINED_SLOTS_PER_JUDGE = 240


class Recovery3MaterializationError(ValueError):
    """Raised when the screening evidence cannot support a fresh successor protocol."""


def _amendment_canonical_sha256() -> str:
    frozen = phase3_plan.FROZEN_V3_RECOVERY3_AMENDMENT_CANONICAL_SHA256
    if frozen is None:
        raise Recovery3MaterializationError(
            "recovery3 amendment binding is not frozen yet; author amendment 8 from the "
            "screening evidence and freeze its hash in phase3_plan first")
    return frozen


def _screen_results_canonical_sha256() -> str:
    frozen = phase3_plan.FROZEN_JUDGMENT_SCREEN_RESULTS_CANONICAL_SHA256
    if frozen is None:
        raise Recovery3MaterializationError(
            "judgment-screen results binding is not frozen yet; record the stage-3 "
            "section and freeze its hash in phase3_plan first")
    return frozen


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Recovery3MaterializationError(f"could not load {path}: {exc}") from exc


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise Recovery3MaterializationError(f"{label} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bound_json(root: Path, path: str | Path, expected_sha: str, label: str) -> Any:
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    payload = _load_json(target)
    if canonical_sha256(payload) != expected_sha:
        raise Recovery3MaterializationError(f"{label} canonical hash drifted")
    return payload


def validate_discovery_observation(
    observation: Mapping[str, Any], *, verify_archive: bool = False,
) -> None:
    if canonical_sha256(observation) != DISCOVERY_CANONICAL_SHA256:
        raise Recovery3MaterializationError("discovery observation canonical hash drifted")
    if observation.get("status") != "final_four_judge_attempt_canceled_before_formal_spend":
        raise Recovery3MaterializationError("discovery observation status drifted")
    if (observation.get("execution_authorized") is not False
            or observation.get("main_run_spend_authorized") is not False):
        raise Recovery3MaterializationError("discovery observation cannot authorize spend")
    accounting = _object(
        observation.get("aggregate_accounting"), "aggregate cap accounting")
    if (float(accounting.get("cap_usd", -1)) != 60.0
            or accounting.get("no_formal_spend_occurred") is not True):
        raise Recovery3MaterializationError("discovery aggregate cap accounting drifted")
    if verify_archive:
        ledger_path = Path(
            "E:/selvarath-archive/phase3-v3r9-final4judge-2026-08-27/phase3_v3_usage.jsonl")
        if not ledger_path.is_file():
            raise Recovery3MaterializationError(
                f"wedged ninth-identity ledger is missing: {ledger_path}")


def _require_screen_verdict(
    results: Mapping[str, Any], stage: str, config_id: str, expected_verdict: str,
) -> None:
    stage_obj = _object(results.get(stage), f"screen results {stage}")
    verdicts = _object(stage_obj.get("verdicts"), f"{stage} verdicts")
    entry = verdicts.get(config_id)
    verdict = entry.get("verdict") if isinstance(entry, Mapping) else entry
    if not isinstance(verdict, str) or not verdict.startswith(expected_verdict):
        raise Recovery3MaterializationError(
            f"screen verdict for {config_id} is not {expected_verdict}")


def validate_amendment(
    amendment: Mapping[str, Any], *, project_root: str | Path | None = None,
) -> None:
    if canonical_sha256(amendment) != _amendment_canonical_sha256():
        raise Recovery3MaterializationError("recovery3 amendment canonical hash drifted")
    if amendment.get("schema_version") != "phase3_v3_roster_amendment_v4":
        raise Recovery3MaterializationError("unsupported recovery3 amendment schema")
    owner = _object(amendment.get("owner_approval"), "amendment owner approval")
    if (owner.get("approver") != "Jack Maiorino"
            or tuple(owner.get("selected_options") or ()) != APPROVAL_OPTIONS):
        raise Recovery3MaterializationError("recovery3 owner approval drifted")
    change = _object(amendment.get("roster_change"), "amendment roster change")
    if (change.get("removed_models") != list(REMOVED_MODELS)
            or "added_model" not in change or change.get("added_model") is not None
            or change.get("checker_only_model") != CHECKER_ONLY_MODEL
            or change.get("final_roster") != list(FINAL_ROSTER)
            or change.get("final_size") != len(FINAL_ROSTER)):
        raise Recovery3MaterializationError("recovery3 roster change drifted")
    raise_spec = _object(amendment.get("verdict_budget_raise"), "verdict budget raise")
    if (raise_spec.get("model_effective_request_max_tokens") != VERDICT_BUDGET_RAISE
            or raise_spec.get("roles") != ["judge_verdict", "batch_verdict"]):
        raise Recovery3MaterializationError("recovery3 verdict-budget raise drifted")
    if (amendment.get("execution_authorized") is not False
            or amendment.get("canary_spend_authorized") is not False
            or amendment.get("main_spend_authorized") is not False):
        raise Recovery3MaterializationError(
            "recovery3 amendment cannot authorize paid execution")
    aggregate = _object(amendment.get("aggregate_cap"), "amendment aggregate cap")
    if (float(aggregate.get("approved_cap_usd", -1)) != 60.0
            or not math.isclose(
                float(aggregate.get("prior_accounted_spend_usd", -1)),
                PRIOR_ACCOUNTED_SPEND_USD, rel_tol=0.0, abs_tol=1e-15)
            or aggregate.get("reset_on_successor") is not False
            or aggregate.get("main_run_spend_authorized") is not False):
        raise Recovery3MaterializationError("recovery3 aggregate cap drifted")
    bindings = _object(
        _object(amendment.get("source_bindings"), "amendment source bindings").get(
            "canonical_json_sha256"),
        "amendment canonical bindings")
    for path, expected in (
        (DISCOVERY_PATH, DISCOVERY_CANONICAL_SHA256),
        (SCREEN_PLAN_PATH, SCREEN_PLAN_CANONICAL_SHA256),
        (SCREEN_RESULTS_PATH, _screen_results_canonical_sha256()),
    ):
        if bindings.get(path.as_posix()) != expected:
            raise Recovery3MaterializationError(
                f"recovery3 amendment binding drifted for {path.as_posix()}")
    if project_root is not None:
        root = Path(project_root)
        _bound_json(root, DISCOVERY_PATH, DISCOVERY_CANONICAL_SHA256,
                    "recovery3 discovery observation")
        _bound_json(root, SCREEN_PLAN_PATH, SCREEN_PLAN_CANONICAL_SHA256,
                    "judgment screen plan")
        results = _bound_json(
            root, SCREEN_RESULTS_PATH, _screen_results_canonical_sha256(),
            "judgment screen results record")
        # The admitted configurations must have passed at their exact pinned caps, and the
        # exclusions must be recorded as the failures they are: no silent rehabilitation.
        _require_screen_verdict(results, "stage_3", "qwen38-verdict-16384", "PASS_stage3")
        _require_screen_verdict(
            results, "stage_2", "gemma4-checker-4096-confirm", "PASS_stage2")
        _require_screen_verdict(results, "stage_2", "llama-verdict-512-confirm", "PASS_stage2")
        _require_screen_verdict(
            results, "stage_3", "gemma4-verdict-8192", "REJECTED_cap_utilization")


def materialize_recovery3_protocol(
    prior_protocol: Mapping[str, Any],
    amendment: Mapping[str, Any],
    discovery: Mapping[str, Any],
    *,
    project_root: str | Path | None = None,
    verify_archive: bool = False,
) -> dict[str, Any]:
    """Create a fresh non-authorizing N=2 protocol by removing both inadmissible judges."""
    phase3_plan.validate_protocol(prior_protocol)
    if canonical_sha256(prior_protocol) != PRIOR_PROTOCOL_CANONICAL_SHA256:
        raise Recovery3MaterializationError("prior v3 r4 protocol canonical hash drifted")
    validate_amendment(amendment, project_root=project_root)
    validate_discovery_observation(discovery, verify_archive=verify_archive)

    amendment_sha = canonical_sha256(amendment)
    discovery_sha = canonical_sha256(discovery)
    protocol = deepcopy(dict(prior_protocol))
    protocol["protocol_id"] = "phase3_budget_knob_2026_08_27_v3r5"
    question_bank_sha = protocol["source_bindings"]["question_bank_bundle_sha256"]
    protocol["cell_key_namespace"] = (
        "phase3-budget-knob-2026-08-27-v3r5."
        f"rr-{discovery_sha[:12]}.ra-{amendment_sha[:12]}.qb-{question_bank_sha[:12]}")
    protocol["planning_cell_identity"] = {
        "status": "planning_only_not_executable",
        "question_bank_bundle_sha256": question_bank_sha,
        "roster_resolution_sha256": discovery_sha,
        "roster_amendment_sha256": amendment_sha,
        "execution_key_requirement": (
            "the run manifest must pin this recovery protocol, fresh roster, exact tokenizer "
            "manifest, live serverless price snapshot, prior aggregate spend, seeds, and outputs"
        ),
    }
    protocol["authorization"] = {
        "design_scope_approved": True,
        "approval_record": AMENDMENT_PATH.as_posix(),
        "amendment_record": AMENDMENT_PATH.as_posix(),
        "approver": "Jack Maiorino",
        "approved_on_date": "2026-08-27",
        "aggregate_cap_usd": 60.0,
        "prior_accounted_spend_usd": PRIOR_ACCOUNTED_SPEND_USD,
        "canary_spend_authorized": False,
        "main_run_spend_authorized": False,
    }
    protocol["sources"].update({
        "prior_protocol_r4": PRIOR_PROTOCOL_PATH.as_posix(),
        "recovery3_amendment": AMENDMENT_PATH.as_posix(),
        "recovery3_discovery_observation": DISCOVERY_PATH.as_posix(),
        "recovery3_judgment_screen_plan": SCREEN_PLAN_PATH.as_posix(),
        "recovery3_judgment_screen_results": SCREEN_RESULTS_PATH.as_posix(),
    })
    bindings = protocol["source_bindings"]["canonical_json_sha256"]
    bindings.update({
        PRIOR_PROTOCOL_PATH.as_posix(): PRIOR_PROTOCOL_CANONICAL_SHA256,
        AMENDMENT_PATH.as_posix(): amendment_sha,
        DISCOVERY_PATH.as_posix(): DISCOVERY_CANONICAL_SHA256,
        SCREEN_PLAN_PATH.as_posix(): SCREEN_PLAN_CANONICAL_SHA256,
        SCREEN_RESULTS_PATH.as_posix(): _screen_results_canonical_sha256(),
    })
    registry = protocol["model_registry"]["models"]
    registry.pop("Qwen/Qwen3.5-9B")
    # gemma-4 keeps ONLY the checker role it passed 0-of-96; it holds no judge seat and
    # bills nothing else.
    registry[CHECKER_ONLY_MODEL] = {"billed_roles": ["query_checker"]}
    protocol["roster_resolution"] = {
        "tracked_path": DISCOVERY_PATH.as_posix(),
        "canonical_sha256": discovery_sha,
        "outcome": "excluded_completion_infeasible",
        "resolved_at_utc": amendment["decided_at_utc"],
        "amendment_tracked_path": AMENDMENT_PATH.as_posix(),
        "amendment_canonical_sha256": amendment_sha,
        "replacement": {
            "removed_models": list(REMOVED_MODELS),
            "added_model": None,
            "checker_only_model": CHECKER_ONLY_MODEL,
        },
    }
    protocol["roster"]["judges_final"] = list(FINAL_ROSTER)
    protocol["roster"]["final_size"] = len(FINAL_ROSTER)
    protocol["roster"]["replacement_policy"] = (
        "both removals are CLOSED on completion-infeasibility evidence bound above: the "
        "weak slot has no admissible serverless candidate, and gemma-4-31B failed verdict "
        "admission at every screened cap (rare per-prompt-deterministic runaway) while "
        "keeping only its screened checker role. No judge may be admitted after successor "
        "materialization. The two-judge roster is a disclosed scope reduction: every "
        "judge-aggregation, disagreement, and diversity claim in the analysis and report "
        "is limited to two judges (one thinking, one non-thinking) and says so.")
    arithmetic = protocol["debate_grid"]["slot_arithmetic"]
    arithmetic["final_roster_size"] = len(FINAL_ROSTER)
    arithmetic["total_judgment_slots"] = JUDGMENT_SLOTS_PER_JUDGE * len(FINAL_ROSTER)
    inventory = protocol["decisions"]["launch_gates"]["canary_slot_inventory"]
    inventory["final_roster_size"] = len(FINAL_ROSTER)
    inventory["fresh_judgment_slots"] = (
        CANARY_JUDGMENT_SLOTS_PER_JUDGE * len(FINAL_ROSTER))
    inventory["fresh_capability_anchor_slots"] = (
        CANARY_CAPABILITY_SLOTS_PER_JUDGE * len(FINAL_ROSTER))
    inventory["combined_fresh_gate_slots"] = (
        CANARY_COMBINED_SLOTS_PER_JUDGE * len(FINAL_ROSTER))
    protocol["process_commitments"]["environmental_interruption"] = (
        "void the interrupted measurement and restart under a fresh successor identity; "
        "preserve the old archive only for evidence and aggregate cap accounting")
    protocol["supersedes"] = {
        "protocol_id": prior_protocol["protocol_id"],
        "canonical_sha256": PRIOR_PROTOCOL_CANONICAL_SHA256,
        "reason": (
            "the r21 discovery showed thinking judges exhausting the frozen 4,096-token "
            "verdict cap on judgment prompts; screening admitted Qwen3.8 at 16,384, found "
            "the weak slot unfillable, and excluded gemma-4's verdict role on a runaway "
            "mode no cap contains (Codex ruled a post-hoc exemption gate-weakening), so "
            "the owner-approved amendment 8 rebuilds N=2 with gemma-4 checker-only")
    }
    protocol["non_claims"] = [
        "This protocol does not authorize provider calls, canary spend, main spend, or GPU work.",
        "No measurement row from any halted attempt satisfies a recovery canary slot.",
        "The prior accounted spend remains charged against the same 60 USD aggregate cap.",
        "Judgment-screen passes are operational blocker screens, not invalid-rate certifications.",
        "Two-judge results support no claim about judge-diversity effects beyond the pair measured.",
    ]
    protocol.pop("protocol_content_sha256", None)
    protocol["protocol_content_sha256"] = canonical_sha256(protocol)
    phase3_plan.validate_protocol(protocol)
    return protocol


def load_materialization_inputs(
    project_root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(project_root)
    prior = _bound_json(
        root, PRIOR_PROTOCOL_PATH, PRIOR_PROTOCOL_CANONICAL_SHA256, "prior protocol")
    amendment = _bound_json(
        root, AMENDMENT_PATH, _amendment_canonical_sha256(), "recovery3 amendment")
    discovery = _bound_json(
        root, DISCOVERY_PATH, DISCOVERY_CANONICAL_SHA256, "discovery observation")
    if not all(isinstance(item, dict) for item in (prior, amendment, discovery)):
        raise Recovery3MaterializationError("recovery3 inputs must be JSON objects")
    return prior, amendment, discovery


def build_protocol_pin(
    protocol: Mapping[str, Any], *, protocol_tracked_path: str | Path,
) -> dict[str, Any]:
    """Reuse the v3 full-document pin after recovery3 bindings enter the protocol hash."""
    return phase3_v3_materialization.build_protocol_pin(
        protocol, protocol_tracked_path=protocol_tracked_path)
