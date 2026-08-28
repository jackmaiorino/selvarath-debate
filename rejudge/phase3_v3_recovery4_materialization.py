"""Deterministic offline materialization for the Llama-checker successor identity.

Fourth recovery generation (2026-08-28): the r25 concentration-bound stop measured
gemma-4's checker runaway at 4.3% per call on live judge queries (prompt-content-
dependent; the pilot-composed screening probes under-predicted it fourfold), so the b2
condition cannot converge under the frozen gemma-4 checker. The owner-approved
amendment 9 substitutes the Llama checker admitted by a 0-of-96 screen on hash-verified
LIVE-query probes including every known runaway trigger. The checker prompts, parser,
decoding, and gate semantics stay the frozen phase-2 artifacts; only the executing model
changes, and gemma-4 leaves the billed registry entirely. This module mirrors
:mod:`rejudge.phase3_v3_recovery3_materialization` for that event.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase3_plan, phase3_v3_materialization
from rejudge.phase2_execution import canonical_sha256


PRIOR_PROTOCOL_PATH = Path("rejudge/phase3_protocol_v3_r5.json")
AMENDMENT_PATH = Path("rejudge/phase3_v3_amendment9_llama_checker_2026-08-28.json")
STOP_PATH = Path("rejudge/phase3_v3_r25_concentration_bound_stop_2026-08-28.json")
SCREEN_PLAN_PATH = Path("rejudge/phase3_v3_llama_checker_screen_plan_2026-08-28.json")
SCREEN_BANK_PATH = Path("rejudge/phase3_v3_llama_checker_screen_bank_2026-08-28.json")
SCREEN_RESULTS_PATH = Path(
    "rejudge/phase3_v3_llama_checker_screen_results_record_2026-08-28.json")
DEFAULT_PROTOCOL_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_r6.json")
DEFAULT_PROTOCOL_PIN_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_pin_r6.json")

PRIOR_PROTOCOL_CANONICAL_SHA256 = phase3_plan.FROZEN_PROTOCOL_V3_R5_CANONICAL_SHA256
STOP_CANONICAL_SHA256 = phase3_plan.FROZEN_V3_R25_STOP_CANONICAL_SHA256
SCREEN_PLAN_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_LLAMA_CHECKER_SCREEN_PLAN_CANONICAL_SHA256)
SCREEN_BANK_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_LLAMA_CHECKER_SCREEN_BANK_CANONICAL_SHA256)
SCREEN_RESULTS_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_LLAMA_CHECKER_SCREEN_RESULTS_CANONICAL_SHA256)

REMOVED_CHECKER = phase3_plan.PHASE3_V3_RECOVERY4_REMOVED_CHECKER
CHECKER_MODEL = phase3_plan.PHASE3_V3_RECOVERY4_CHECKER_MODEL
FINAL_ROSTER = phase3_plan.PHASE3_V3_RECOVERY3_BASE_JUDGES
# R11 carry plus the second N=2 attempt's sealed ledger ($7.88070266, run
# phase3-v3-d5836a2141cb3736), matching phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD_R12.
PRIOR_ACCOUNTED_SPEND_USD = 42.53125933
APPROVAL_OPTION = "Llama checker (Codex B)"


class Recovery4MaterializationError(ValueError):
    """Raised when the checker evidence cannot support a fresh successor protocol."""


def _amendment_canonical_sha256() -> str:
    frozen = phase3_plan.FROZEN_V3_RECOVERY4_AMENDMENT_CANONICAL_SHA256
    if frozen is None:
        raise Recovery4MaterializationError(
            "recovery4 amendment binding is not frozen yet; author amendment 9 from the "
            "checker-screen evidence and freeze its hash in phase3_plan first")
    return frozen


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Recovery4MaterializationError(f"could not load {path}: {exc}") from exc


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise Recovery4MaterializationError(f"{label} must be an object")
    return value


def _bound_json(root: Path, path: str | Path, expected_sha: str, label: str) -> Any:
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    payload = _load_json(target)
    if canonical_sha256(payload) != expected_sha:
        raise Recovery4MaterializationError(f"{label} canonical hash drifted")
    return payload


def validate_stop_observation(observation: Mapping[str, Any]) -> None:
    if canonical_sha256(observation) != STOP_CANONICAL_SHA256:
        raise Recovery4MaterializationError("r25 stop observation canonical hash drifted")
    if observation.get("status") != (
            "stopped_at_frozen_concentration_bound_owner_review_required"):
        raise Recovery4MaterializationError("r25 stop observation status drifted")
    if (observation.get("execution_authorized") is not False
            or observation.get("main_run_spend_authorized") is not False):
        raise Recovery4MaterializationError("r25 stop observation cannot authorize spend")
    accounting = _object(
        observation.get("aggregate_accounting"), "aggregate cap accounting")
    if (float(accounting.get("cap_usd", -1)) != 60.0
            or not math.isclose(
                float(accounting.get("prior_accounted_spend_usd_if_terminal", -1)),
                PRIOR_ACCOUNTED_SPEND_USD, rel_tol=0.0, abs_tol=1e-15)):
        raise Recovery4MaterializationError("r25 aggregate cap accounting drifted")


def validate_amendment(
    amendment: Mapping[str, Any], *, project_root: str | Path | None = None,
) -> None:
    if canonical_sha256(amendment) != _amendment_canonical_sha256():
        raise Recovery4MaterializationError("recovery4 amendment canonical hash drifted")
    if amendment.get("schema_version") != "phase3_v3_checker_amendment_v1":
        raise Recovery4MaterializationError("unsupported recovery4 amendment schema")
    owner = _object(amendment.get("owner_approval"), "amendment owner approval")
    if (owner.get("approver") != "Jack Maiorino"
            or owner.get("selected_option") != APPROVAL_OPTION):
        raise Recovery4MaterializationError("recovery4 owner approval drifted")
    substitution = _object(
        amendment.get("checker_substitution"), "amendment checker substitution")
    if (substitution.get("removed_checker") != REMOVED_CHECKER
            or substitution.get("added_checker") != CHECKER_MODEL
            or substitution.get("frozen_unchanged") != [
                "system_prompt", "user_template", "parser", "temperature", "seed",
                "gate_semantics"]
            or int(substitution.get("max_tokens", -1)) != 16):
        raise Recovery4MaterializationError("recovery4 checker substitution drifted")
    diagnostic = _object(
        amendment.get("judge_origin_disparity_diagnostic"), "disparity diagnostic")
    if (diagnostic.get("blocking_rule") is None
            or diagnostic.get("reported_in") != "successor canary audit report"):
        raise Recovery4MaterializationError("recovery4 disparity diagnostic drifted")
    if (amendment.get("execution_authorized") is not False
            or amendment.get("canary_spend_authorized") is not False
            or amendment.get("main_spend_authorized") is not False):
        raise Recovery4MaterializationError(
            "recovery4 amendment cannot authorize paid execution")
    aggregate = _object(amendment.get("aggregate_cap"), "amendment aggregate cap")
    if (float(aggregate.get("approved_cap_usd", -1)) != 60.0
            or not math.isclose(
                float(aggregate.get("prior_accounted_spend_usd", -1)),
                PRIOR_ACCOUNTED_SPEND_USD, rel_tol=0.0, abs_tol=1e-15)
            or aggregate.get("reset_on_successor") is not False
            or aggregate.get("main_run_spend_authorized") is not False):
        raise Recovery4MaterializationError("recovery4 aggregate cap drifted")
    bindings = _object(
        _object(amendment.get("source_bindings"), "amendment source bindings").get(
            "canonical_json_sha256"),
        "amendment canonical bindings")
    for path, expected in (
        (STOP_PATH, STOP_CANONICAL_SHA256),
        (SCREEN_PLAN_PATH, SCREEN_PLAN_CANONICAL_SHA256),
        (SCREEN_BANK_PATH, SCREEN_BANK_CANONICAL_SHA256),
        (SCREEN_RESULTS_PATH, SCREEN_RESULTS_CANONICAL_SHA256),
    ):
        if bindings.get(path.as_posix()) != expected:
            raise Recovery4MaterializationError(
                f"recovery4 amendment binding drifted for {path.as_posix()}")
    if project_root is not None:
        root = Path(project_root)
        validate_stop_observation(
            _bound_json(root, STOP_PATH, STOP_CANONICAL_SHA256, "r25 stop"))
        _bound_json(root, SCREEN_PLAN_PATH, SCREEN_PLAN_CANONICAL_SHA256,
                    "checker screen plan")
        _bound_json(root, SCREEN_BANK_PATH, SCREEN_BANK_CANONICAL_SHA256,
                    "checker screen bank")
        results = _bound_json(
            root, SCREEN_RESULTS_PATH, SCREEN_RESULTS_CANONICAL_SHA256,
            "checker screen results record")
        if results.get("verdict") != "PASS":
            raise Recovery4MaterializationError(
                "the Llama checker screen did not pass; the substitution is not "
                "admissible")


def materialize_recovery4_protocol(
    prior_protocol: Mapping[str, Any],
    amendment: Mapping[str, Any],
    stop_observation: Mapping[str, Any],
    *,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Create a fresh non-authorizing protocol by substituting only the checker model."""
    phase3_plan.validate_protocol(prior_protocol)
    if canonical_sha256(prior_protocol) != PRIOR_PROTOCOL_CANONICAL_SHA256:
        raise Recovery4MaterializationError("prior v3 r5 protocol canonical hash drifted")
    validate_amendment(amendment, project_root=project_root)
    validate_stop_observation(stop_observation)

    amendment_sha = canonical_sha256(amendment)
    protocol = deepcopy(dict(prior_protocol))
    protocol["protocol_id"] = "phase3_budget_knob_2026_08_28_v3r6"
    question_bank_sha = protocol["source_bindings"]["question_bank_bundle_sha256"]
    resolution_sha = protocol["planning_cell_identity"]["roster_resolution_sha256"]
    protocol["cell_key_namespace"] = (
        "phase3-budget-knob-2026-08-28-v3r6."
        f"rr-{resolution_sha[:12]}.ra-{amendment_sha[:12]}.qb-{question_bank_sha[:12]}")
    protocol["planning_cell_identity"]["roster_amendment_sha256"] = amendment_sha
    protocol["authorization"] = {
        "design_scope_approved": True,
        "approval_record": AMENDMENT_PATH.as_posix(),
        "amendment_record": AMENDMENT_PATH.as_posix(),
        "approver": "Jack Maiorino",
        "approved_on_date": "2026-08-28",
        "aggregate_cap_usd": 60.0,
        "prior_accounted_spend_usd": PRIOR_ACCOUNTED_SPEND_USD,
        "canary_spend_authorized": False,
        "main_run_spend_authorized": False,
    }
    protocol["sources"].update({
        "prior_protocol_r5": PRIOR_PROTOCOL_PATH.as_posix(),
        "recovery4_amendment": AMENDMENT_PATH.as_posix(),
        "recovery4_stop_observation": STOP_PATH.as_posix(),
        "recovery4_checker_screen_plan": SCREEN_PLAN_PATH.as_posix(),
        "recovery4_checker_screen_bank": SCREEN_BANK_PATH.as_posix(),
        "recovery4_checker_screen_results": SCREEN_RESULTS_PATH.as_posix(),
    })
    bindings = protocol["source_bindings"]["canonical_json_sha256"]
    bindings.update({
        PRIOR_PROTOCOL_PATH.as_posix(): PRIOR_PROTOCOL_CANONICAL_SHA256,
        AMENDMENT_PATH.as_posix(): amendment_sha,
        STOP_PATH.as_posix(): STOP_CANONICAL_SHA256,
        SCREEN_PLAN_PATH.as_posix(): SCREEN_PLAN_CANONICAL_SHA256,
        SCREEN_BANK_PATH.as_posix(): SCREEN_BANK_CANONICAL_SHA256,
        SCREEN_RESULTS_PATH.as_posix(): SCREEN_RESULTS_CANONICAL_SHA256,
    })
    registry = protocol["model_registry"]["models"]
    registry.pop(REMOVED_CHECKER)
    checker_roles = list(registry[CHECKER_MODEL]["billed_roles"])
    if "query_checker" not in checker_roles:
        checker_roles.append("query_checker")
    registry[CHECKER_MODEL]["billed_roles"] = checker_roles
    protocol["roster"]["query_checker"] = CHECKER_MODEL
    resolution = protocol["roster_resolution"]
    resolution["amendment_tracked_path"] = AMENDMENT_PATH.as_posix()
    resolution["amendment_canonical_sha256"] = amendment_sha
    resolution["resolved_at_utc"] = amendment["decided_at_utc"]
    resolution["replacement"] = {
        "removed_models": list(phase3_plan.PHASE3_V3_RECOVERY3_REMOVED_JUDGES),
        "added_model": None,
        "checker_only_model": None,
        "checker_substitution": {
            "removed_checker": REMOVED_CHECKER,
            "added_checker": CHECKER_MODEL,
        },
    }
    protocol["roster"]["replacement_policy"] = (
        "gemma-4-31B is fully excluded: its verdict role failed the frozen headroom rule "
        "at every screened cap and its checker role reached the frozen concentration "
        "bound at a live-query runaway rate of 4.3% per call (r25). The checker role is "
        "served by the Llama configuration admitted 0-of-96 on hash-verified live-query "
        "probes including every known runaway trigger, with the frozen phase-2 prompts, "
        "parser, and decoding unchanged. The Llama-checks-Llama-queries adjacency is a "
        "disclosed correlated-validity risk governed by the pre-registered judge-origin "
        "disparity diagnostic in amendment 9. No further substitution is permitted after "
        "successor materialization.")
    protocol["supersedes"] = {
        "protocol_id": prior_protocol["protocol_id"],
        "canonical_sha256": PRIOR_PROTOCOL_CANONICAL_SHA256,
        "reason": (
            "the r25 concentration-bound stop established that the b2 condition cannot "
            "converge under the frozen gemma-4 checker (prompt-content-dependent runaway "
            "at 4.3% per live call); the owner-approved amendment 9 substitutes the "
            "screened Llama checker with every other frozen element unchanged")
    }
    protocol["non_claims"] = [
        "This protocol does not authorize provider calls, canary spend, main spend, or GPU work.",
        "No measurement row from any halted attempt satisfies a recovery canary slot.",
        "The prior accounted spend remains charged against the same 60 USD aggregate cap.",
        "Checker screening passes are operational blocker screens, not error-rate certifications.",
        "Two-judge results support no claim about judge-diversity effects beyond the pair measured.",
        "The checker substitution is an adaptive operational-validity change, disclosed as such.",
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
        root, AMENDMENT_PATH, _amendment_canonical_sha256(), "recovery4 amendment")
    stop = _bound_json(root, STOP_PATH, STOP_CANONICAL_SHA256, "r25 stop observation")
    if not all(isinstance(item, dict) for item in (prior, amendment, stop)):
        raise Recovery4MaterializationError("recovery4 inputs must be JSON objects")
    return prior, amendment, stop


def build_protocol_pin(
    protocol: Mapping[str, Any], *, protocol_tracked_path: str | Path,
) -> dict[str, Any]:
    """Reuse the v3 full-document pin after recovery4 bindings enter the protocol hash."""
    return phase3_v3_materialization.build_protocol_pin(
        protocol, protocol_tracked_path=protocol_tracked_path)
