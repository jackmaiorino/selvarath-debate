"""Deterministic offline materialization for the Phase 3 v3 successor.

The conditional Qwen2.5 branch must resolve before a v3 protocol can exist. This module accepts
one evidence-bound resolution, validates that it follows the owner-approved branch rules, and
produces one offline protocol plus one exact protocol pin. It has no network, credential,
provider-call, authorization, or execution path.
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase3_plan
from rejudge.phase2_execution import canonical_sha256


DESIGN_PATH = Path("rejudge/phase3_v3_successor_design_2026-08-23.json")
V2_PROTOCOL_PATH = Path("rejudge/phase3_protocol_v2.json")
PROMPT_BUNDLE_PATH = Path("rejudge/phase2_prompt_bundle.json")
CHECKER_CONFIG_PATH = Path("rejudge/phase2_checker_frozen_config_2026-07-23.json")
CHECKER_VALIDATION_DESIGN_PATH = Path(
    "rejudge/phase2_checker_validation_design_2026-07-18.json")
DEFAULT_PROTOCOL_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3.json")
DEFAULT_PROTOCOL_PIN_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_pin.json")

DESIGN_SCHEMA_VERSION = "phase3_v3_successor_design_v1"
RESOLUTION_SCHEMA_VERSION = "phase3_v3_roster_resolution_v1"
PROTOCOL_SCHEMA_VERSION = "phase3_plan_v3"
PIN_SCHEMA_VERSION = "phase3_v3_protocol_pin_v1"

DESIGN_CANONICAL_SHA256 = phase3_plan.FROZEN_SUCCESSOR_DESIGN_CANONICAL_SHA256
V2_PROTOCOL_CANONICAL_SHA256 = phase3_plan.FROZEN_PROTOCOL_V2_CANONICAL_SHA256
PROMPT_BUNDLE_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_PHASE2_PROMPT_BUNDLE_CANONICAL_SHA256)
CHECKER_CONFIG_CANONICAL_SHA256 = phase3_plan.FROZEN_CHECKER_CONFIG_CANONICAL_SHA256
CHECKER_VALIDATION_DESIGN_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_CHECKER_VALIDATION_DESIGN_CANONICAL_SHA256)
QWEN_DEADLINE_UTC = datetime(2026, 8, 29, tzinfo=timezone.utc)
QWEN_EXPECTED_DEFERRED_CELLS = 192
QWEN_STRICT_INVALID_DENOMINATOR = 96

BASE_JUDGES = phase3_plan.PHASE3_V3_BASE_JUDGES
CONDITIONAL_JUDGE = phase3_plan.PHASE3_V3_CONDITIONAL_JUDGE
EXCLUDED_JUDGE = phase3_plan.PHASE3_V3_EXCLUDED_JUDGE

INCLUDED_OUTCOME = "included_recovery_pass"
EXCLUDED_OUTCOMES = frozenset({
    "excluded_recovery_fail",
    "excluded_deadline",
    "excluded_main_authorization",
})
ALL_OUTCOMES = frozenset({INCLUDED_OUTCOME, *EXCLUDED_OUTCOMES})

RESOLUTION_FIELDS = frozenset({
    "schema_version",
    "resolution_id",
    "tracked_path",
    "resolved_at_utc",
    "outcome",
    "trigger",
    "qwen2_5",
    "evidence",
    "final_roster",
    "execution_authorized",
})
QWEN_FIELDS = frozenset({
    "model_id",
    "completed_deferred_cells",
    "expected_deferred_cells",
    "strict_invalid_count",
    "strict_invalid_denominator",
    "strict_invalid_gate_pass",
    "structural_mirroring_gate_pass",
})
EVIDENCE_FIELDS = frozenset({"tracked_path", "canonical_sha256"})


class MaterializationError(ValueError):
    """Raised when roster resolution or successor materialization is invalid."""


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise MaterializationError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise MaterializationError(f"{label} must be a non-empty string")
    return value


def _sha256(value: Any, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise MaterializationError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _integer(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MaterializationError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise MaterializationError(f"{label} must be between {minimum} and {maximum}")
    return value


def _utc(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise MaterializationError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MaterializationError(f"{label} must have a UTC offset")
    return parsed.astimezone(timezone.utc)


def _load_json_object(path: str | Path) -> dict[str, Any]:
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise MaterializationError(f"{path} must contain a JSON object")
    return loaded


def _canonical_json_file_sha256(path: Path) -> str:
    return canonical_sha256(_load_json_object(path))


def validate_design(design: Mapping[str, Any]) -> None:
    """Validate the exact owner-approved successor design used by this materializer."""
    if design.get("schema_version") != DESIGN_SCHEMA_VERSION:
        raise MaterializationError("unsupported successor design schema")
    if canonical_sha256(design) != DESIGN_CANONICAL_SHA256:
        raise MaterializationError("successor design canonical hash drifted")
    if design.get("execution_authorized") is not False:
        raise MaterializationError("successor design must not authorize execution")
    if design.get("main_run_authorized") is not False:
        raise MaterializationError("successor design must not authorize main spend")
    roster = _object(design.get("roster"), "design.roster")
    if tuple(roster.get("provisional_judges", ())) != BASE_JUDGES:
        raise MaterializationError("successor design base roster drifted")
    if roster.get("conditional_judge") != CONDITIONAL_JUDGE:
        raise MaterializationError("successor design conditional judge drifted")
    if roster.get("final_size_range") != [4, 5]:
        raise MaterializationError("successor design final roster range drifted")
    bindings = _object(design.get("source_bindings"), "design.source_bindings")
    if bindings.get(str(V2_PROTOCOL_PATH).replace("\\", "/")) != V2_PROTOCOL_CANONICAL_SHA256:
        raise MaterializationError("successor design does not bind the immutable v2 protocol")


def expected_final_roster(outcome: str) -> list[str]:
    if outcome == INCLUDED_OUTCOME:
        return [*BASE_JUDGES, CONDITIONAL_JUDGE]
    if outcome in EXCLUDED_OUTCOMES:
        return list(BASE_JUDGES)
    raise MaterializationError(f"unsupported roster outcome: {outcome!r}")


def validate_roster_resolution(
    resolution: Mapping[str, Any],
    design: Mapping[str, Any],
    *,
    project_root: str | Path | None = None,
) -> None:
    """Validate one terminal Qwen2.5 outcome and its exact final roster."""
    validate_design(design)
    if set(resolution) != RESOLUTION_FIELDS:
        raise MaterializationError(
            f"roster resolution fields must be exactly {sorted(RESOLUTION_FIELDS)!r}")
    if resolution.get("schema_version") != RESOLUTION_SCHEMA_VERSION:
        raise MaterializationError("unsupported roster-resolution schema")
    _text(resolution.get("resolution_id"), "resolution_id")
    tracked_path = _text(resolution.get("tracked_path"), "tracked_path")
    if Path(tracked_path).is_absolute() or not tracked_path.replace("\\", "/").startswith(
            "rejudge/"):
        raise MaterializationError("tracked_path must be a relative rejudge JSON path")
    if not tracked_path.endswith(".json"):
        raise MaterializationError("tracked_path must name a JSON artifact")
    if resolution.get("execution_authorized") is not False:
        raise MaterializationError("roster resolution cannot authorize execution")

    resolved_at = _utc(resolution.get("resolved_at_utc"), "resolved_at_utc")
    outcome = _text(resolution.get("outcome"), "outcome")
    if outcome not in ALL_OUTCOMES:
        raise MaterializationError(f"unsupported roster outcome: {outcome!r}")

    qwen = _object(resolution.get("qwen2_5"), "qwen2_5")
    if set(qwen) != QWEN_FIELDS:
        raise MaterializationError(f"qwen2_5 fields must be exactly {sorted(QWEN_FIELDS)!r}")
    if qwen.get("model_id") != CONDITIONAL_JUDGE:
        raise MaterializationError("qwen2_5.model_id drifted")
    expected_cells = _integer(
        qwen.get("expected_deferred_cells"), "qwen2_5.expected_deferred_cells",
        minimum=QWEN_EXPECTED_DEFERRED_CELLS, maximum=QWEN_EXPECTED_DEFERRED_CELLS,
    )
    completed = _integer(
        qwen.get("completed_deferred_cells"), "qwen2_5.completed_deferred_cells",
        minimum=0, maximum=expected_cells,
    )
    denominator = _integer(
        qwen.get("strict_invalid_denominator"), "qwen2_5.strict_invalid_denominator",
        minimum=QWEN_STRICT_INVALID_DENOMINATOR,
        maximum=QWEN_STRICT_INVALID_DENOMINATOR,
    )
    invalid_count = qwen.get("strict_invalid_count")
    invalid_pass = qwen.get("strict_invalid_gate_pass")
    structural_pass = qwen.get("structural_mirroring_gate_pass")
    trigger = _text(resolution.get("trigger"), "trigger")

    if outcome in {INCLUDED_OUTCOME, "excluded_recovery_fail"}:
        if trigger != "recovery_completion":
            raise MaterializationError("recovery outcomes require trigger recovery_completion")
        if resolved_at > QWEN_DEADLINE_UTC:
            raise MaterializationError("Qwen recovery completed after the owner deadline")
        if completed != expected_cells:
            raise MaterializationError("recovery outcomes require all 192 deferred cells")
        invalid_count = _integer(
            invalid_count, "qwen2_5.strict_invalid_count", minimum=0, maximum=denominator)
        expected_invalid_pass = invalid_count / denominator < 0.02
        if invalid_pass is not expected_invalid_pass:
            raise MaterializationError("strict-invalid gate result disagrees with its integer count")
        if not isinstance(structural_pass, bool):
            raise MaterializationError("completed recovery requires a structural gate result")
        gates_pass = expected_invalid_pass and structural_pass
        if outcome == INCLUDED_OUTCOME and not gates_pass:
            raise MaterializationError("Qwen2.5 inclusion requires both unchanged v2 gates")
        if outcome == "excluded_recovery_fail" and gates_pass:
            raise MaterializationError("passing Qwen2.5 recovery must use inclusion outcome")
    else:
        expected_trigger = (
            "deadline" if outcome == "excluded_deadline" else "main_authorization"
        )
        if trigger != expected_trigger:
            raise MaterializationError(
                f"{outcome} requires trigger {expected_trigger}")
        if outcome == "excluded_deadline" and resolved_at < QWEN_DEADLINE_UTC:
            raise MaterializationError("deadline exclusion cannot resolve before the deadline")
        if outcome == "excluded_main_authorization" and resolved_at >= QWEN_DEADLINE_UTC:
            raise MaterializationError("main-authorization exclusion must precede the deadline")
        if completed == expected_cells:
            raise MaterializationError("a complete recovery must resolve through its gates")
        if invalid_count is not None or invalid_pass is not None or structural_pass is not None:
            raise MaterializationError("incomplete boundary resolution cannot claim gate results")

    raw_final_roster = resolution.get("final_roster")
    if (not isinstance(raw_final_roster, list)
            or not all(isinstance(model, str) and model for model in raw_final_roster)):
        raise MaterializationError("final_roster must contain non-empty model IDs")
    final_roster = [str(model) for model in raw_final_roster]
    if final_roster != expected_final_roster(outcome):
        raise MaterializationError("final_roster disagrees with the terminal Qwen2.5 outcome")
    if EXCLUDED_JUDGE in final_roster:
        raise MaterializationError("gpt-oss cannot rejoin the successor roster")

    evidence = _object(resolution.get("evidence"), "evidence")
    if set(evidence) != EVIDENCE_FIELDS:
        raise MaterializationError(f"evidence fields must be exactly {sorted(EVIDENCE_FIELDS)!r}")
    evidence_path = _text(evidence.get("tracked_path"), "evidence.tracked_path")
    evidence_sha = _sha256(evidence.get("canonical_sha256"), "evidence.canonical_sha256")
    if Path(evidence_path).is_absolute():
        raise MaterializationError("evidence.tracked_path must be relative")
    if project_root is not None:
        observed = _canonical_json_file_sha256(Path(project_root) / evidence_path)
        if observed != evidence_sha:
            raise MaterializationError(
                f"roster-resolution evidence hash drift: observed {observed}, expected "
                f"{evidence_sha}")


def _model_registry(final_roster: list[str]) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for model in final_roster:
        roles = ["judge_query", "judge_verdict", "capability_qa"]
        if model == "google/gemma-4-31B-it":
            roles.append("query_checker")
        if model == "meta-llama/Llama-3.3-70B-Instruct-Turbo":
            roles.append("oracle_verification")
        models[model] = {"billed_roles": roles}
    return {
        "models": models,
        "price_status": "fresh serverless price snapshot required after roster resolution",
        "tokenizer_status": "exact provider-matched tokenizer evidence required per billed model",
        "provider_revision_policy": (
            "record exposed provider revision metadata on every call; halt an affected model on "
            "unresolved alias drift"
        ),
    }


def _configuration_selection(v2: Mapping[str, Any], design: Mapping[str, Any]) -> dict[str, Any]:
    previous = _object(
        _object(v2.get("decisions"), "v2.decisions").get("configuration_selection"),
        "v2.decisions.configuration_selection",
    )
    return {
        "status": "approved_successor_rule",
        "selected_configuration": "A_no_d",
        "fixed_roster_rule": (
            "the roster resolved before this protocol materialized and is not reduced post hoc"
        ),
        "continuous_rate_l90": deepcopy(design["continuous_rate_l90"]),
        "projection_rule": (
            "use successor-canary per-question upper bounds for unique rulings per slot and the "
            "declared continuous-rate L90 denominator; v2 pace is diagnostic only"
        ),
        "limits": deepcopy(previous["limits"]),
        "fallback": [
            "if configuration A exceeds a frozen limit, restrict b4 and b8 to the bound "
            "41-question subset with their paired comparators",
            "if a frozen limit still fails, halt before main authorization and return to owner",
        ],
        "subset_derivation": deepcopy(previous["subset_derivation"]),
        "subset_question_ids": deepcopy(previous["subset_question_ids"]),
    }


def _launch_gates(v2: Mapping[str, Any], design: Mapping[str, Any], roster_size: int) -> dict[str, Any]:
    previous = _object(
        _object(v2.get("decisions"), "v2.decisions").get("launch_gates"),
        "v2.decisions.launch_gates",
    )
    return {
        "status": "materialized_successor_canary_pending_separate_authorization",
        "canary_question_set": previous["canary_question_set"],
        "canary_slot_inventory": {
            "per_judge": {
                "core_b0_judgment_slots": 96,
                "budget_smoke_judgment_slots": 96,
                "capability_anchor_slots": 48,
                "combined_fresh_gate_slots": 240,
            },
            "final_roster_size": roster_size,
            "fresh_judgment_slots": 192 * roster_size,
            "fresh_capability_anchor_slots": 48 * roster_size,
            "combined_fresh_gate_slots": 240 * roster_size,
            "carry_forward_result_rows": 0,
        },
        "calibration_gates_per_judge": deepcopy(previous["calibration_gates_per_judge"]),
        "budget_smoke_subset": deepcopy(previous["budget_smoke_subset"]),
        "fresh_scope": design["successor_canary"]["fresh_scope"],
        "formal_measurement": design["successor_canary"]["formal_measurement"],
        "environmental_interruption": design["successor_canary"]["environmental_interruption"],
        "offline_checker_fixtures": previous["offline_checker_fixtures"],
        "completion_gate": "100% exact completion of the manifest-pinned fresh successor slots",
        "zero_tolerance_gates": deepcopy(previous["zero_tolerance_gates"]),
        "failure_action": "halt before main spend and return the concrete failing gate to owner",
        "pace_measurement_window": deepcopy(design["continuous_rate_l90"]),
    }


def materialize_protocol(
    v2: Mapping[str, Any],
    design: Mapping[str, Any],
    resolution: Mapping[str, Any],
    *,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build one v3 protocol from an already terminal, evidence-bound roster outcome."""
    phase3_plan.validate_protocol(v2)
    if canonical_sha256(v2) != V2_PROTOCOL_CANONICAL_SHA256:
        raise MaterializationError("v2 protocol canonical hash drifted")
    validate_roster_resolution(resolution, design, project_root=project_root)

    design_sha = canonical_sha256(design)
    resolution_sha = canonical_sha256(resolution)
    final_roster = list(resolution["final_roster"])
    roster_size = len(final_roster)
    old_bindings = _object(v2.get("source_bindings"), "v2.source_bindings")
    question_bank_sha = _sha256(
        old_bindings.get("question_bank_bundle_sha256"),
        "v2.source_bindings.question_bank_bundle_sha256",
    )
    resolution_path = str(resolution["tracked_path"]).replace("\\", "/")
    evidence = _object(resolution["evidence"], "resolution.evidence")
    evidence_path = str(evidence["tracked_path"]).replace("\\", "/")
    canonical_bindings = {
        str(v2["sources"]["phase2_protocol"]): v2["source_bindings"][
            "canonical_json_sha256"][v2["sources"]["phase2_protocol"]],
        str(V2_PROTOCOL_PATH).replace("\\", "/"): V2_PROTOCOL_CANONICAL_SHA256,
        str(DESIGN_PATH).replace("\\", "/"): design_sha,
        str(PROMPT_BUNDLE_PATH).replace("\\", "/"): PROMPT_BUNDLE_CANONICAL_SHA256,
        str(CHECKER_CONFIG_PATH).replace("\\", "/"): CHECKER_CONFIG_CANONICAL_SHA256,
        str(CHECKER_VALIDATION_DESIGN_PATH).replace("\\", "/"): (
            CHECKER_VALIDATION_DESIGN_CANONICAL_SHA256),
        resolution_path: resolution_sha,
        evidence_path: evidence["canonical_sha256"],
    }

    transcript_reuse = deepcopy(v2["transcript_reuse"])
    transcript_reuse["canary_bundle"]["materialization"] = (
        "extract and verify all 48 reused held-out transcripts; any missing or mismatched "
        "transcript blocks successor-canary authorization"
    )

    secondary = deepcopy(v2["decisions"]["secondary_analyses"])
    secondary["capability_slope"]["p_value_rule"] = (
        "never_report_with_v3_roster_below_6"
    )
    capability_anchor = deepcopy(v2["decisions"]["capability_anchor"])
    capability_anchor["status"] = "fresh_successor_measurement_required"
    capability_anchor["population"] = (
        "all final rostered judges are measured fresh under the v3 identity; no v1 or v2 result "
        "row is pooled, copied, or substituted"
    )

    protocol: dict[str, Any] = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "protocol_id": "phase3_budget_knob_2026_08_23_v3",
        "cell_key_namespace": (
            "phase3-budget-knob-2026-08-23-v3."
            f"rr-{resolution_sha[:12]}.qb-{question_bank_sha[:12]}"
        ),
        "planning_cell_identity": {
            "status": "planning_only_not_executable",
            "question_bank_bundle_sha256": question_bank_sha,
            "roster_resolution_sha256": resolution_sha,
            "execution_key_requirement": (
                "the run manifest must pin this protocol, final roster, prompt and transcript "
                "inputs, exact tokenizer manifest, fresh price snapshot, seeds, and outputs"
            ),
        },
        "status": "materialized_offline_protocol",
        "offline_planning_only": True,
        "execution_authorized": False,
        "authorization": {
            "design_scope_approved": True,
            "approval_record": str(DESIGN_PATH).replace("\\", "/"),
            "approver": design["owner_approval"]["approver"],
            "approved_on_date": design["owner_approval"]["approved_on_date"],
            "canary_spend_authorized": False,
            "main_run_spend_authorized": False,
        },
        "sources": {
            "phase2_protocol": v2["sources"]["phase2_protocol"],
            "phase3_protocol_v2": str(V2_PROTOCOL_PATH).replace("\\", "/"),
            "successor_design": str(DESIGN_PATH).replace("\\", "/"),
            "prompt_bundle": str(PROMPT_BUNDLE_PATH).replace("\\", "/"),
            "query_checker_frozen_config": str(CHECKER_CONFIG_PATH).replace("\\", "/"),
            "query_checker_validation_design": str(
                CHECKER_VALIDATION_DESIGN_PATH).replace("\\", "/"),
            "roster_resolution": resolution_path,
            "roster_resolution_evidence": evidence_path,
        },
        "source_bindings": {
            "hash_convention": "canonical_sha256 for JSON artifacts",
            "canonical_json_sha256": canonical_bindings,
            "question_bank_bundle_sha256": question_bank_sha,
        },
        "unit_definitions": deepcopy(v2["unit_definitions"]),
        "question_set": deepcopy(v2["question_set"]),
        "transcript_reuse": transcript_reuse,
        "model_registry": _model_registry(final_roster),
        "roster_resolution": {
            "tracked_path": resolution_path,
            "canonical_sha256": resolution_sha,
            "outcome": resolution["outcome"],
            "resolved_at_utc": resolution["resolved_at_utc"],
        },
        "roster": {
            "judges_final": final_roster,
            "final_size": roster_size,
            "debaters": deepcopy(v2["roster"]["debaters"]),
            "debater_role_note": "debater identity is reused-transcript metadata; debaters are never called",
            "oracle": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "query_checker": "google/gemma-4-31B-it",
            "gate_reviewer": v2["roster"]["gate_reviewer"],
            "capability_slope_inference": "estimate_and_plot_only_no_p_value",
            "replacement_policy": "none",
            "gpt_oss_disposition": "excluded_binding_v2_strict_invalid_gate_failure",
        },
        "debate_grid": {
            "k": 2,
            "conditions": deepcopy(v2["debate_grid"]["conditions"]),
            "selected_configuration": "A_no_d",
            "slot_arithmetic": {
                "per_judge_per_condition": 984,
                "per_judge_total": 4920,
                "final_roster_size": roster_size,
                "total_judgment_slots": 4920 * roster_size,
            },
        },
        "decisions": {
            "primary_tests": deepcopy(v2["decisions"]["primary_tests"]),
            "secondary_analyses": secondary,
            "capability_anchor": capability_anchor,
            "configuration_selection": _configuration_selection(v2, design),
            "query_screening": deepcopy(v2["decisions"]["query_screening"]),
            "execution_semantics": deepcopy(v2["decisions"]["execution_semantics"]),
            "launch_gates": _launch_gates(v2, design, roster_size),
            "spend": {
                "status": "canary_pending_separate_authorization_main_pending_forecast",
                "stage_cap_usd": v2["decisions"]["spend"]["stage_cap_usd"],
                "forecast_contract": deepcopy(design["forecast_contract"]),
                "authorization_rule": {
                    "successor_canary": {
                        "separate_owner_authorization_required": True,
                        "certified_main_forecast_required": False,
                        "dependency_reason": (
                            "fresh successor-canary usage is an input to the main forecast"
                        ),
                    },
                    "main": {
                        "separate_owner_authorization_required": True,
                        "certified_main_forecast_required": True,
                        "forecast_input_requirement": (
                            "fresh successor-canary usage ledger"
                        ),
                    },
                },
            },
            "context_guard": deepcopy(v2["decisions"]["context_guard"]),
        },
        "materialization_requirements": deepcopy(design["materialization_manifest"]),
        "process_commitments": {
            "preflight": "checkout, build, dependency, archive, and provider checks are retryable",
            "formal_measurement": "single-shot only after analysis gates are frozen",
            "environmental_interruption": (
                "void and restart the pace window with one log line; keep completed semantic cells"
            ),
            "harness_verification": design["materialization_manifest"]["harness_check"],
        },
        "supersedes": {
            "protocol_id": v2["protocol_id"],
            "canonical_sha256": V2_PROTOCOL_CANONICAL_SHA256,
            "reason": (
                "the binding gpt-oss failure and conditional Qwen2.5 branch require a resolved "
                "four- or five-judge identity, fresh canary rows, corrected forecast inputs, and "
                "a new namespace"
            ),
        },
        "non_claims": [
            "This protocol does not authorize provider calls, canary spend, main spend, or GPU work.",
            "No v1 or v2 result row satisfies a v3 canary slot.",
            "No v3 forecast is certified without exact tokenizers, fresh prices, and fresh canary usage.",
        ],
    }
    protocol["protocol_content_sha256"] = canonical_sha256(protocol)
    phase3_plan.validate_protocol(protocol)
    return protocol


def build_protocol_pin(
    protocol: Mapping[str, Any], *, protocol_tracked_path: str | Path,
) -> dict[str, Any]:
    """Build the external full-document pin for one generated v3 protocol."""
    phase3_plan.validate_protocol(protocol)
    tracked_path = str(protocol_tracked_path).replace("\\", "/")
    if Path(tracked_path).is_absolute():
        raise MaterializationError("protocol_tracked_path must be relative")
    pin = {
        "schema_version": PIN_SCHEMA_VERSION,
        "protocol_tracked_path": tracked_path,
        "protocol_canonical_sha256": canonical_sha256(protocol),
        "protocol_content_sha256": protocol["protocol_content_sha256"],
        "successor_design_canonical_sha256": DESIGN_CANONICAL_SHA256,
        "roster_resolution_canonical_sha256": protocol["roster_resolution"]["canonical_sha256"],
        "final_roster": list(protocol["roster"]["judges_final"]),
        "execution_authorized": False,
    }
    validate_protocol_pin(pin, protocol)
    return pin


def validate_protocol_pin(pin: Mapping[str, Any], protocol: Mapping[str, Any]) -> None:
    expected_fields = {
        "schema_version", "protocol_tracked_path", "protocol_canonical_sha256",
        "protocol_content_sha256", "successor_design_canonical_sha256",
        "roster_resolution_canonical_sha256", "final_roster", "execution_authorized",
    }
    if set(pin) != expected_fields:
        raise MaterializationError(f"protocol pin fields must be exactly {sorted(expected_fields)!r}")
    if pin.get("schema_version") != PIN_SCHEMA_VERSION:
        raise MaterializationError("unsupported protocol-pin schema")
    if pin.get("execution_authorized") is not False:
        raise MaterializationError("protocol pin cannot authorize execution")
    phase3_plan.validate_protocol(protocol)
    if pin.get("protocol_canonical_sha256") != canonical_sha256(protocol):
        raise MaterializationError("protocol pin canonical hash drifted")
    if pin.get("protocol_content_sha256") != protocol.get("protocol_content_sha256"):
        raise MaterializationError("protocol pin content digest drifted")
    if pin.get("successor_design_canonical_sha256") != DESIGN_CANONICAL_SHA256:
        raise MaterializationError("protocol pin successor-design binding drifted")
    if pin.get("roster_resolution_canonical_sha256") != protocol[
            "roster_resolution"]["canonical_sha256"]:
        raise MaterializationError("protocol pin roster-resolution binding drifted")
    if pin.get("final_roster") != protocol["roster"]["judges_final"]:
        raise MaterializationError("protocol pin final roster drifted")


def load_materialization_inputs(
    project_root: str | Path,
    resolution_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(project_root)
    v2 = _load_json_object(root / V2_PROTOCOL_PATH)
    design = _load_json_object(root / DESIGN_PATH)
    resolution_file = Path(resolution_path)
    if not resolution_file.is_absolute():
        resolution_file = root / resolution_file
    resolution = _load_json_object(resolution_file)
    expected_resolution_path = str(resolution_file.resolve().relative_to(root.resolve())).replace(
        "\\", "/")
    if resolution.get("tracked_path") != expected_resolution_path:
        raise MaterializationError(
            "resolution tracked_path does not match the supplied file location")
    validate_roster_resolution(resolution, design, project_root=root)
    return v2, design, resolution
