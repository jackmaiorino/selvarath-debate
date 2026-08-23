"""Focused coverage for deterministic Phase 3 v3 roster and protocol materialization."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase3_plan, phase3_v3_materialization as materialization
from rejudge.phase2_execution import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
V2 = json.loads((ROOT / materialization.V2_PROTOCOL_PATH).read_text(encoding="utf-8"))
DESIGN = json.loads((ROOT / materialization.DESIGN_PATH).read_text(encoding="utf-8"))


def _resolution(outcome: str) -> dict:
    recovery = outcome in {materialization.INCLUDED_OUTCOME, "excluded_recovery_fail"}
    if outcome == materialization.INCLUDED_OUTCOME:
        resolved_at = "2026-08-24T10:00:00Z"
        trigger = "recovery_completion"
        completed = 192
        invalid_count = 1
        invalid_pass = True
        structural_pass = True
    elif outcome == "excluded_recovery_fail":
        resolved_at = "2026-08-24T10:00:00Z"
        trigger = "recovery_completion"
        completed = 192
        invalid_count = 2
        invalid_pass = False
        structural_pass = True
    elif outcome == "excluded_deadline":
        resolved_at = "2026-08-29T00:00:00Z"
        trigger = "deadline"
        completed = 0
        invalid_count = None
        invalid_pass = None
        structural_pass = None
    elif outcome == "excluded_main_authorization":
        resolved_at = "2026-08-25T10:00:00Z"
        trigger = "main_authorization"
        completed = 100
        invalid_count = None
        invalid_pass = None
        structural_pass = None
    else:
        raise AssertionError(outcome)
    assert recovery == (trigger == "recovery_completion")
    return {
        "schema_version": materialization.RESOLUTION_SCHEMA_VERSION,
        "resolution_id": f"test_{outcome}",
        "tracked_path": f"rejudge/test_{outcome}.json",
        "resolved_at_utc": resolved_at,
        "outcome": outcome,
        "trigger": trigger,
        "qwen2_5": {
            "model_id": materialization.CONDITIONAL_JUDGE,
            "completed_deferred_cells": completed,
            "expected_deferred_cells": 192,
            "strict_invalid_count": invalid_count,
            "strict_invalid_denominator": 96,
            "strict_invalid_gate_pass": invalid_pass,
            "structural_mirroring_gate_pass": structural_pass,
        },
        "evidence": {
            "tracked_path": f"rejudge/test_{outcome}_evidence.json",
            "canonical_sha256": "a" * 64,
        },
        "final_roster": materialization.expected_final_roster(outcome),
        "execution_authorized": False,
    }


@pytest.mark.parametrize("outcome", sorted(materialization.ALL_OUTCOMES))
def test_all_terminal_resolution_branches_validate(outcome: str):
    resolution = _resolution(outcome)
    materialization.validate_roster_resolution(resolution, DESIGN)


def test_recovery_inclusion_requires_all_cells_and_both_gates():
    resolution = _resolution(materialization.INCLUDED_OUTCOME)
    resolution["qwen2_5"]["completed_deferred_cells"] = 191
    with pytest.raises(materialization.MaterializationError, match="all 192"):
        materialization.validate_roster_resolution(resolution, DESIGN)

    resolution = _resolution(materialization.INCLUDED_OUTCOME)
    resolution["qwen2_5"]["structural_mirroring_gate_pass"] = False
    with pytest.raises(materialization.MaterializationError, match="both unchanged"):
        materialization.validate_roster_resolution(resolution, DESIGN)


def test_strict_invalid_gate_is_derived_from_integer_count():
    resolution = _resolution("excluded_recovery_fail")
    resolution["qwen2_5"]["strict_invalid_gate_pass"] = True
    with pytest.raises(materialization.MaterializationError, match="integer count"):
        materialization.validate_roster_resolution(resolution, DESIGN)


def test_deadline_cannot_be_selected_early_or_after_complete_recovery():
    resolution = _resolution("excluded_deadline")
    resolution["resolved_at_utc"] = "2026-08-28T23:59:59Z"
    with pytest.raises(materialization.MaterializationError, match="before the deadline"):
        materialization.validate_roster_resolution(resolution, DESIGN)

    resolution = _resolution("excluded_deadline")
    resolution["qwen2_5"]["completed_deferred_cells"] = 192
    with pytest.raises(materialization.MaterializationError, match="resolve through its gates"):
        materialization.validate_roster_resolution(resolution, DESIGN)


def test_main_authorization_boundary_cannot_be_backdated_after_deadline():
    resolution = _resolution("excluded_main_authorization")
    resolution["resolved_at_utc"] = "2026-08-29T00:00:00Z"
    with pytest.raises(materialization.MaterializationError, match="precede the deadline"):
        materialization.validate_roster_resolution(resolution, DESIGN)


def test_resolution_evidence_hash_is_verified_when_root_is_supplied(tmp_path: Path):
    evidence_path = tmp_path / "rejudge" / "evidence.json"
    evidence_path.parent.mkdir()
    evidence = {"result": "incomplete_at_deadline"}
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    resolution = _resolution("excluded_deadline")
    resolution["evidence"] = {
        "tracked_path": "rejudge/evidence.json",
        "canonical_sha256": canonical_sha256(evidence),
    }
    materialization.validate_roster_resolution(resolution, DESIGN, project_root=tmp_path)
    resolution["evidence"]["canonical_sha256"] = "b" * 64
    with pytest.raises(materialization.MaterializationError, match="evidence hash drift"):
        materialization.validate_roster_resolution(resolution, DESIGN, project_root=tmp_path)


@pytest.mark.parametrize(
    ("outcome", "expected_roster_size", "expected_main", "expected_canary"),
    [
        ("excluded_deadline", 4, 19680, 960),
        (materialization.INCLUDED_OUTCOME, 5, 24600, 1200),
    ],
)
def test_materialized_protocol_is_valid_and_matches_slot_arithmetic(
    outcome: str,
    expected_roster_size: int,
    expected_main: int,
    expected_canary: int,
):
    protocol = materialization.materialize_protocol(V2, DESIGN, _resolution(outcome))
    phase3_plan.validate_protocol(protocol)
    judges = phase3_plan.candidate_roster_judges(protocol, expected_roster_size)
    main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, ROOT)
    main_cells = phase3_plan.enumerate_cells(protocol, judges, main_ids)
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, judges, held_out_ids)
    assert phase3_plan.summarize_cells(main_cells)["slot_count"] == expected_main
    canary_summary = phase3_plan.summarize_cells(canary_cells)
    assert canary_summary["slot_count"] == expected_canary
    assert canary_summary["by_kind"][phase3_plan.CANARY_JUDGMENT_KIND] == (
        192 * expected_roster_size)
    assert canary_summary["by_kind"][phase3_plan.CAPABILITY_ANCHOR_KIND] == (
        48 * expected_roster_size)
    assert protocol["decisions"]["launch_gates"]["canary_slot_inventory"][
        "carry_forward_result_rows"] == 0


def test_v3_candidate_roster_helper_rejects_nonfinal_size():
    protocol = materialization.materialize_protocol(
        V2, DESIGN, _resolution("excluded_deadline"))
    with pytest.raises(phase3_plan.PlanValidationError, match="resolved at exactly 4"):
        phase3_plan.candidate_roster_judges(protocol, 5)


def test_protocol_content_digest_detects_tampering():
    protocol = materialization.materialize_protocol(
        V2, DESIGN, _resolution("excluded_deadline"))
    protocol["roster"]["replacement_policy"] = "quietly changed"
    with pytest.raises(phase3_plan.ProtocolValidationError, match="content digest drift"):
        phase3_plan.validate_protocol(protocol)


def test_protocol_pin_binds_full_protocol_resolution_and_roster():
    protocol = materialization.materialize_protocol(
        V2, DESIGN, _resolution(materialization.INCLUDED_OUTCOME))
    pin = materialization.build_protocol_pin(
        protocol, protocol_tracked_path="rejudge/phase3_protocol_v3.json")
    assert pin["protocol_canonical_sha256"] == canonical_sha256(protocol)
    assert pin["final_roster"] == [*materialization.BASE_JUDGES,
                                    materialization.CONDITIONAL_JUDGE]
    changed = deepcopy(pin)
    changed["protocol_canonical_sha256"] = "0" * 64
    with pytest.raises(materialization.MaterializationError, match="canonical hash drifted"):
        materialization.validate_protocol_pin(changed, protocol)
