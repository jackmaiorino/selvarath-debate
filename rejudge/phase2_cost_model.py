"""Deterministic preflight cost model for the approved, non-executable Phase-2 scope."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from rejudge import phase2_plan


DEFAULT_PROTOCOL_PATH = phase2_plan.DEFAULT_PROTOCOL_PATH
DEFAULT_ARTIFACT_PATH = Path(__file__).with_name("phase2_cost_model.json")


class CostModelError(ValueError):
    """The frozen scope and tracked cost artifact disagree."""


def canonical_sha256(value: Any) -> str:
    return phase2_plan.canonical_sha256(value)


def canonical_json_file_sha256(path: str | Path) -> str:
    """Hash parsed JSON so checkout EOL and formatting changes do not alter bindings."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return canonical_sha256(value)


def normalized_text_file_sha256(path: str | Path) -> str:
    """Hash text with canonical LF endings while preserving all other content."""
    value = (Path(path).read_text(encoding="utf-8")
             .replace("\r\n", "\n").replace("\r", "\n"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_MAIN_CORE_KINDS = {
    "debate_transcript", "debate_judgment", "no_debate_judgment",
}
_MAIN_CAP_KINDS = {"capped_debate_transcript", "cap_protection_judgment"}
_MAIN_OPTIONAL_KINDS = {"empty_evidence_judgment", "full_document_judgment"}
_MAIN_CAPABILITY_KINDS = {"capability_qa"}
_TRANSCRIPT_KINDS = {
    "debate_transcript", "capped_debate_transcript",
    "canary_debate_transcript", "canary_capped_debate_transcript",
}
_OUTCOME_KINDS = {
    "debate_judgment", "no_debate_judgment", "cap_protection_judgment",
    "empty_evidence_judgment", "full_document_judgment", "capability_qa",
    "canary_debate_judgment", "canary_no_debate_judgment",
    "canary_cap_protection_judgment", "canary_empty_evidence_judgment",
    "canary_full_document_judgment",
}


def _condition_semantics(
    protocol: Mapping[str, Any], cell: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    kind = str(cell["kind"])
    if kind in {"debate_judgment", "canary_debate_judgment"}:
        conditions = protocol["debate_grid"]["conditions"]
    elif kind in {"no_debate_judgment", "canary_no_debate_judgment"}:
        conditions = protocol["no_debate_references"]["conditions"]
    else:
        return None
    matches = [condition for condition in conditions
               if condition["id"] == cell["condition"]]
    if len(matches) != 1:
        raise CostModelError(
            f"missing or ambiguous condition semantics for {cell['condition']!r}")
    return matches[0]


def derive_call_inventory(
    cells: Sequence[Mapping[str, Any]], protocol: Mapping[str, Any],
) -> dict[str, int]:
    """Derive the upper-bound provider-call inventory from enumerated cells.

    A transcript has one call per debater turn. Query budgets are upper bounds. Batch
    replay reuses the sequential Q&A and therefore makes no fresh query/checker/oracle
    calls. Every fresh clean or placebo query is screened; only clean accepted slots
    make an oracle call.
    """
    rounds_by_kind = {
        "debate_transcript": int(protocol["debate_protocol"]["rounds"]),
        "canary_debate_transcript": int(protocol["debate_protocol"]["rounds"]),
        "capped_debate_transcript": int(
            protocol["decisions"]["cap_protection_secondary"]
            ["capped_debate_protocol"]["rounds"]),
        "canary_capped_debate_transcript": int(
            protocol["decisions"]["cap_protection_secondary"]
            ["capped_debate_protocol"]["rounds"]),
    }
    counts = {
        "cells": len(cells),
        "transcript_generation_calls": 0,
        "outcome_calls": 0,
        "query_generation_calls": 0,
        "oracle_calls": 0,
        "checker_calls_before_retries": 0,
    }
    for cell in cells:
        kind = str(cell["kind"])
        if kind in _TRANSCRIPT_KINDS:
            counts["transcript_generation_calls"] += 2 * rounds_by_kind[kind]
            continue
        if kind not in _OUTCOME_KINDS:
            raise CostModelError(f"no call semantics for cell kind {kind!r}")
        counts["outcome_calls"] += 1
        query_budget = int(cell.get("query_budget") or 0)
        if query_budget == 0:
            continue
        semantics = _condition_semantics(protocol, cell)
        if semantics is None:
            raise CostModelError(f"query-producing cell kind has no condition semantics: {kind!r}")
        oracle_mode = str(semantics["oracle_mode"])
        if oracle_mode == "replay_clean_qa":
            continue
        if oracle_mode not in {"clean", "placebo"}:
            raise CostModelError(
                f"unsupported query-producing oracle mode {oracle_mode!r}")
        counts["query_generation_calls"] += query_budget
        counts["checker_calls_before_retries"] += query_budget
        if oracle_mode == "clean":
            counts["oracle_calls"] += query_budget
    counts["calls_before_checker"] = (
        counts["transcript_generation_calls"] + counts["outcome_calls"]
        + counts["query_generation_calls"] + counts["oracle_calls"]
    )
    counts["calls_including_checker_before_retries"] = (
        counts["calls_before_checker"] + counts["checker_calls_before_retries"]
    )
    return counts


def _line_item(
    *, component: str, cells: int, calls_before_checker: int,
    checker_calls: int, minimum_usd: float, maximum_usd: float,
    evidence: str,
) -> dict[str, Any]:
    return {
        "component": component,
        "cells": cells,
        "calls_before_checker": calls_before_checker,
        "checker_calls": checker_calls,
        "provisional_manual_band_usd": {
            "minimum": minimum_usd, "maximum": maximum_usd,
        },
        "band_basis": "manual provisional allocation from empirical aggregate anchors",
        "evidence": evidence,
    }


def build_cost_model(
    protocol: Mapping[str, Any], plan: Mapping[str, Any], project_root: str | Path,
) -> dict[str, Any]:
    phase2_plan.validate_protocol(protocol)
    summary = plan["summary"]
    root = Path(project_root)
    models_path = root / protocol["sources"]["calibration_models"]
    models = json.loads(models_path.read_text(encoding="utf-8"))
    canary_plan = phase2_plan.build_canary_plan(protocol)
    canary_summary = canary_plan["summary"]
    main_cells = plan["cells"]
    canary_cells = canary_plan["cells"]
    phase2_plan.validate_cells(main_cells, str(protocol["cell_key_namespace"]))
    phase2_plan.validate_cells(canary_cells, str(protocol["cell_key_namespace"]))
    main_inventory = derive_call_inventory(main_cells, protocol)
    canary_inventory = derive_call_inventory(canary_cells, protocol)

    def inventory_for(kinds: set[str]) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
        selected = [cell for cell in main_cells if str(cell["kind"]) in kinds]
        return selected, derive_call_inventory(selected, protocol)

    core_cells, core_inventory = inventory_for(_MAIN_CORE_KINDS)
    cap_cells, cap_inventory = inventory_for(_MAIN_CAP_KINDS)
    optional_cells, optional_inventory = inventory_for(_MAIN_OPTIONAL_KINDS)
    capability_cells, capability_inventory = inventory_for(_MAIN_CAPABILITY_KINDS)
    component_cell_count = sum(map(len, (
        core_cells, cap_cells, optional_cells, capability_cells,
    )))
    if component_cell_count != len(main_cells):
        raise CostModelError("main call-model components do not partition the enumerated cells")

    canary_transcript_cells = int(canary_summary["transcript_cells"])
    canary_outcome_cells = int(canary_summary["outcome_cells"])
    if (canary_inventory["cells"]
            != canary_transcript_cells + canary_outcome_cells):
        raise CostModelError("canary summary and enumerated cell inventory disagree")

    gemma_selector_path = root / protocol["sources"]["gemma_recovery_selector"]
    gemma_selector = json.loads(gemma_selector_path.read_text(encoding="utf-8"))
    if (not isinstance(gemma_selector, list)
            or not all(isinstance(cell_key, str) for cell_key in gemma_selector)
            or len(set(gemma_selector)) != len(gemma_selector)):
        raise CostModelError("Gemma recovery selector must be a unique JSON string list")
    gemma_recovery_cells = len(gemma_selector)

    main_pre_checker_calls = main_inventory["calls_before_checker"]
    canary_pre_checker_calls = canary_inventory["calls_before_checker"]
    total_pre_checker_calls = (
        main_pre_checker_calls + gemma_recovery_cells + canary_pre_checker_calls)
    main_query_slots = main_inventory["query_generation_calls"]
    main_oracle_calls = main_inventory["oracle_calls"]
    canary_query_slots = canary_inventory["query_generation_calls"]
    canary_oracle_calls = canary_inventory["oracle_calls"]
    total_checker_calls = (
        main_inventory["checker_calls_before_retries"]
        + canary_inventory["checker_calls_before_retries"])
    total_calls_before_retries = total_pre_checker_calls + total_checker_calls

    rejection_rate = 0.05
    planned_rejected_slots = math.ceil(total_checker_calls * rejection_rate)
    planned_retry_query_calls = planned_rejected_slots
    planned_retry_checker_calls = planned_rejected_slots
    semantic_retry_calls = planned_retry_query_calls + planned_retry_checker_calls
    total_calls_after_semantic_retries = total_calls_before_retries + semantic_retry_calls

    line_items = [
        _line_item(
            component="core_generation_debate_and_no_debate",
            cells=len(core_cells),
            calls_before_checker=core_inventory["calls_before_checker"],
            checker_calls=0,
            minimum_usd=450,
            maximum_usd=750,
            evidence="Stage 1 and calibration call shapes, widened for the mixed reasoning roster",
        ),
        _line_item(
            component="full_capped_interaction_block",
            cells=len(cap_cells),
            calls_before_checker=cap_inventory["calls_before_checker"],
            checker_calls=0,
            minimum_usd=25,
            maximum_usd=60,
            evidence="492 capped transcripts plus 984 Llama-70B b0 judgments",
        ),
        _line_item(
            component="empty_table_and_full_document_anchors",
            cells=len(optional_cells),
            calls_before_checker=optional_inventory["calls_before_checker"],
            checker_calls=0,
            minimum_usd=10,
            maximum_usd=35,
            evidence="one verdict call for each of 1,476 optional judgments",
        ),
        _line_item(
            component="capability_qa",
            cells=len(capability_cells),
            calls_before_checker=capability_inventory["calls_before_checker"],
            checker_calls=0,
            minimum_usd=5,
            maximum_usd=15,
            evidence="five models x 106 questions x K2 full-document QA",
        ),
        _line_item(
            component="gemma_recovery_supplement",
            cells=gemma_recovery_cells,
            calls_before_checker=gemma_recovery_cells,
            checker_calls=0,
            minimum_usd=0,
            maximum_usd=2,
            evidence="exact 11-cell selector; separate spend authority required",
        ),
        _line_item(
            component="manifested_canary",
            cells=canary_transcript_cells + canary_outcome_cells,
            calls_before_checker=canary_pre_checker_calls,
            checker_calls=0,
            minimum_usd=15,
            maximum_usd=35,
            evidence="fresh generation plus core and optional-arm canary paths",
        ),
        _line_item(
            component="checker_retries_timeouts_and_reconciliation_reserve",
            cells=0,
            calls_before_checker=planned_retry_query_calls,
            checker_calls=total_checker_calls + planned_retry_checker_calls,
            minimum_usd=100,
            maximum_usd=250,
            evidence=(
                "all base checker calls, one replacement query and checker for a 5% "
                "first-rejection planning case, plus unknown-charge reserve"),
        ),
    ]
    line_item_calls = sum(
        item["calls_before_checker"] + item["checker_calls"] for item in line_items)
    if line_item_calls != total_calls_after_semantic_retries:
        raise CostModelError("line-item calls do not partition the post-retry call inventory")
    raw_minimum = sum(
        item["provisional_manual_band_usd"]["minimum"] for item in line_items)
    raw_maximum = sum(
        item["provisional_manual_band_usd"]["maximum"] for item in line_items)
    if (raw_minimum, raw_maximum) != (605, 1_147):
        raise CostModelError("line-item planning bands drifted")

    empirical_anchors = [
        {
            "source": "reports/2026-07-09-stage1-rejudge-results.md",
            "source_sha256": normalized_text_file_sha256(
                root / "reports/2026-07-09-stage1-rejudge-results.md"),
            "cost_usd": 182.16,
            "api_calls": 52_543,
        },
        {
            "source": "reports/2026-07-12-mechanism-and-packaging-memo.md",
            "source_sha256": normalized_text_file_sha256(
                root / "reports/2026-07-12-mechanism-and-packaging-memo.md"),
            "cost_usd": 14.57,
            "api_calls": 3_816,
        },
        {
            "source": "reports/2026-07-14-calibration-results.md",
            "source_sha256": normalized_text_file_sha256(
                root / "reports/2026-07-14-calibration-results.md"),
            "generation_cost_usd": 4.75,
            "transcripts": 192,
        },
    ]
    main_plan_sha256 = canonical_sha256(plan)
    canary_plan_sha256 = canonical_sha256(canary_plan)
    spend = protocol["decisions"]["cumulative_spend"]
    cost_model = {
        "schema_version": "phase2_cost_model_v2",
        "status": "preflight_pending_checker_and_provider_reconciliation",
        "protocol_id": protocol["protocol_id"],
        "cell_key_namespace": protocol["cell_key_namespace"],
        "protocol_sha256": canonical_sha256(protocol),
        "scope_sha256": main_plan_sha256,
        "canary_scope_sha256": canary_plan_sha256,
        "input_bindings": {
            "approved_phase2_plan_sha256": main_plan_sha256,
            "approved_phase2_cells_sha256": canonical_sha256(main_cells),
            "canary_plan_sha256": canary_plan_sha256,
            "canary_cells_sha256": canonical_sha256(canary_cells),
            "gemma_recovery_selector": {
                "source_path": protocol["sources"]["gemma_recovery_selector"],
                "source_sha256": canonical_json_file_sha256(gemma_selector_path),
                "selected_cell_count": gemma_recovery_cells,
            },
            "empirical_evidence_sha256": canonical_sha256(empirical_anchors),
        },
        "price_schedule": {
            "provider": models["provider"],
            "verified_at": models["price_verified_at"],
            "source_url": models["price_source_url"],
            "source_path": protocol["sources"]["calibration_models"],
            "source_sha256": canonical_json_file_sha256(models_path),
            "hash_basis": "canonical parsed JSON",
            "prices_per_mtok": models["prices_per_mtok"],
        },
        "inventory": {
            "approved_phase2_cells": int(summary["all_cells"]),
            "approved_phase2_transcript_cells": int(summary["debate_transcript_cells"]),
            "approved_phase2_analysis_cells": int(summary["judgment_cells"]),
            "capability_preflight_cells": int(summary["capability_preflight_cells"]),
            "post_canary_main_cells": int(summary["post_canary_main_cells"]),
            "gemma_recovery_supplement_cells": gemma_recovery_cells,
            "canary_transcript_cells": canary_transcript_cells,
            "canary_outcome_cells": canary_outcome_cells,
            "canary_symbolic_model_cells": int(canary_summary["symbolic_model_cells"]),
            "all_planned_cells_including_gemma_and_canary": (
                int(summary["all_cells"]) + gemma_recovery_cells
                + canary_transcript_cells + canary_outcome_cells
            ),
        },
        "call_model": {
            "approved_phase2_calls_before_checker": main_pre_checker_calls,
            "capability_preflight_calls_before_checker": (
                capability_inventory["calls_before_checker"]),
            "post_canary_main_calls_before_checker": (
                main_pre_checker_calls - capability_inventory["calls_before_checker"]),
            "gemma_and_canary_calls_before_checker": (
                gemma_recovery_cells + canary_pre_checker_calls),
            "total_calls_before_checker": total_pre_checker_calls,
            "post_canary_main_query_generation_calls": main_query_slots,
            "post_canary_main_checker_calls_before_retries": (
                main_inventory["checker_calls_before_retries"]),
            "post_canary_main_oracle_calls": main_oracle_calls,
            "canary_query_generation_calls": canary_query_slots,
            "canary_checker_calls_before_retries": (
                canary_inventory["checker_calls_before_retries"]),
            "canary_query_budget_slots_and_checker_calls": canary_query_slots,
            "canary_oracle_calls": canary_oracle_calls,
            "total_query_generation_calls_before_retries": (
                main_query_slots + canary_query_slots),
            "total_oracle_calls": main_oracle_calls + canary_oracle_calls,
            "total_checker_calls_before_retries": total_checker_calls,
            "total_calls_before_semantic_or_transport_retries": total_calls_before_retries,
            "total_calls_after_planned_semantic_retries": (
                total_calls_after_semantic_retries),
            "semantic_retry_planning": {
                "assumed_first_rejection_rate": rejection_rate,
                "planned_rejected_slots": planned_rejected_slots,
                "extra_calls_per_rejected_slot": 2,
                "planned_retry_query_calls": planned_retry_query_calls,
                "planned_retry_checker_calls": planned_retry_checker_calls,
                "planned_extra_calls": semantic_retry_calls,
            },
            "derivation": (
                "enumerated cells plus frozen rounds, query_budget, oracle_mode, "
                "batch-replay, and checker-scope semantics"),
            "checker_scope": protocol["decisions"]["query_screening"]["checker_scope"],
            "checker_model_id": None,
        },
        "line_items": line_items,
        "cost_controls_usd": {
            "estimate_status": "empirical_manual_provisional_not_token_derived",
            "estimate_basis": (
                "manual component bands anchored to aggregate historical spend; "
                "the frozen price catalog is recorded but cannot produce a token-level "
                "forecast until role-specific token profiles and checker model are frozen"),
            "raw_line_item_minimum": raw_minimum,
            "raw_line_item_maximum": raw_maximum,
            "communicated_planning_minimum": 650,
            "communicated_planning_maximum": 1_150,
            "working_budget": spend["working_budget_usd"],
            "incremental_hard_ceiling": spend["incremental_phase2_hard_ceiling_usd"],
            "estimated_prepaid_credit": spend["estimated_prepaid_credit_usd"],
        },
        "empirical_anchors": empirical_anchors,
        "limitations": [
            "The checker model and validated token profile are not yet selected.",
            "Dollar bands are manual provisional estimates, not outputs of the recorded token prices.",
            "Historical outputs predate durable per-call ledgers, so empirical report totals only anchor the band.",
            "Provider starting spend and prepaid credit remain estimates until dashboard reconciliation.",
            "One canary full-document cell retains a symbolic judge until capability preflight selects it.",
            "The post-retry total covers the 5% semantic first-rejection case, not transport retries or unknown charges.",
            "The $1,500 ceiling is a fail-closed boundary, not expected spend.",
        ],
    }
    if cost_model["cost_controls_usd"]["communicated_planning_maximum"] > (
            cost_model["cost_controls_usd"]["incremental_hard_ceiling"]):
        raise CostModelError("conservative planning estimate exceeds the hard ceiling")
    return cost_model


def render_cost_model(model: Mapping[str, Any]) -> str:
    return json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_from_paths(
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    protocol_path = Path(protocol_path)
    root = Path(project_root) if project_root is not None else protocol_path.resolve().parent.parent
    protocol = phase2_plan.load_protocol(protocol_path)
    plan = phase2_plan.build_plan(
        protocol, phase2_plan.load_main_question_ids(protocol, root))
    return build_cost_model(protocol, plan, root)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or check the offline Phase-2 cost model.")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        model = build_from_paths(args.protocol, args.project_root)
        rendered = render_cost_model(model)
        if args.check:
            actual = args.artifact.read_text(encoding="utf-8")
            if actual != rendered:
                raise CostModelError(
                    f"tracked cost model is stale: {args.artifact}; regenerate from frozen inputs")
        else:
            sys.stdout.write(rendered)
    except (OSError, json.JSONDecodeError, phase2_plan.ProtocolValidationError,
            phase2_plan.PlanValidationError, CostModelError) as exc:
        print(f"phase2 cost-model error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
