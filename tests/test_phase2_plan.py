import json
import socket
from copy import deepcopy
from collections import Counter
from pathlib import Path

import pytest

from rejudge import phase2_plan


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"
CALIBRATION_MODELS_PATH = ROOT / "rejudge" / "output" / "calibration_models.json"


@pytest.fixture(scope="module")
def protocol():
    return phase2_plan.load_protocol(PROTOCOL_PATH)


@pytest.fixture(scope="module")
def main_question_ids(protocol):
    return phase2_plan.load_main_question_ids(protocol, ROOT)


@pytest.fixture(scope="module")
def plan(protocol, main_question_ids):
    return phase2_plan.build_plan(protocol, main_question_ids)


def test_protocol_has_approved_design_but_no_spend_or_execution_authority(protocol):
    models = json.loads(CALIBRATION_MODELS_PATH.read_text(encoding="utf-8"))
    expected_judges = [
        models["judges"]["low_fallback"],
        models["judges"]["mid_gemma"],
        models["judges"]["anchor"],
        models["judges"]["top_oss"],
    ]
    assert protocol["schema_version"] == "phase2_plan_v2"
    assert protocol["status"] == "approved_design_pending_materialization"
    assert protocol["offline_planning_only"] is True
    assert protocol["execution_authorized"] is False
    assert protocol["authorization"]["design_approved"] is True
    assert protocol["authorization"]["capability_preflight_spend_authorized"] is False
    assert protocol["authorization"]["canary_spend_authorized"] is False
    assert protocol["authorization"]["main_run_spend_authorized"] is False
    assert protocol["roster"] == {
        "judges": expected_judges,
        "debaters": models["debaters"],
        "oracle": models["oracle"],
    }

    used_models = set(expected_judges + models["debaters"] + [models["oracle"]])
    for model_id in used_models:
        source_price = models["prices_per_mtok"][model_id]
        plan_price = protocol["model_registry"][model_id]["price_usd_per_million_tokens"]
        assert plan_price == {"input": source_price["in"], "output": source_price["out"]}


def test_question_sources_exclude_exactly_24_and_leave_82(protocol, main_question_ids):
    question_set = protocol["question_set"]
    excluded = set(question_set["calibration_excluded_question_ids"])
    assert len(excluded) == question_set["expected_calibration_exclusion_count"] == 24
    assert len(main_question_ids) == question_set["expected_main_question_count"] == 82
    assert excluded.isdisjoint(main_question_ids)
    assert len(set(main_question_ids) | excluded) == 106


def test_source_bindings_are_semantic_and_cross_eol_stable(protocol, tmp_path):
    bindings = protocol["source_bindings"]["canonical_json_sha256"]
    for index, relative_path in enumerate(bindings):
        source = ROOT / relative_path
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(source.read_text(encoding="utf-8"))
        newline = "\r\n" if index % 2 else "\n"
        rendered = json.dumps(payload, ensure_ascii=False, indent=1)
        target.write_text(rendered.replace("\n", newline) + newline, encoding="utf-8")

    labels_path = protocol["materialization_requirements"]["resolvability_labels"][
        "review_template_path"
    ]
    labels_target = tmp_path / labels_path
    labels_target.parent.mkdir(parents=True, exist_ok=True)
    labels_payload = json.loads((ROOT / labels_path).read_text(encoding="utf-8"))
    labels_target.write_text(
        json.dumps(labels_payload, ensure_ascii=False, indent=3).replace("\n", "\r\n")
        + "\r\n",
        encoding="utf-8",
    )

    phase2_plan.validate_source_bindings(protocol, tmp_path)

    first_path = protocol["question_set"]["question_sources"][0]
    mutated_path = tmp_path / first_path
    mutated = json.loads(mutated_path.read_text(encoding="utf-8"))
    mutated[0]["question"] += " semantic drift"
    mutated_path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(phase2_plan.ProtocolValidationError, match="source hash mismatch"):
        phase2_plan.validate_source_bindings(protocol, tmp_path)


def test_bound_calibration_questions_freeze_the_excluded_cohort(protocol):
    mutated = deepcopy(protocol)
    original_namespace = mutated["cell_key_namespace"]
    mutated["question_set"]["calibration_excluded_question_ids"][0] = "CN-001"
    assert mutated["cell_key_namespace"] == original_namespace
    with pytest.raises(
        phase2_plan.ProtocolValidationError, match="calibration exclusions disagree"
    ):
        phase2_plan.load_main_question_ids(mutated, ROOT)


def test_scientific_decisions_are_filled_while_materialization_is_explicit(protocol):
    decisions = protocol["decisions"]
    assert set(decisions) == {
        "primary_tests",
        "capability_measurement",
        "cap_protection_secondary",
        "query_screening",
        "design_scope_reconciliation",
        "secondary_analyses",
        "execution_semantics",
        "launch_gates",
        "cumulative_spend",
    }
    assert [item["id"] for item in decisions["primary_tests"]["family"]] == ["H", "P", "R"]
    assert decisions["primary_tests"]["multiplicity_adjustment"] == "Holm"
    assert decisions["capability_measurement"]["question_set"] == "all_106"
    assert decisions["capability_measurement"]["replicate_count"] == 2
    assert decisions["design_scope_reconciliation"]["matched_legacy_bridge"][
        "included"] is False

    requirements = protocol["materialization_requirements"]
    assert requirements["status"] == "pending_before_protocol_freeze"
    assert requirements["query_checker"]["model_id"] is None
    assert requirements["resolvability_labels"]["review_template_path"] == (
        "rejudge/phase2_resolvability_review.json"
    )
    assert len(requirements["resolvability_labels"]["review_template_sha256"]) == 64
    assert requirements["resolvability_labels"]["completed_review_path"] is None
    assert requirements["resolvability_labels"]["human_pass_complete"] is False
    assert requirements["prompt_bundle"]["sha256"] is None
    assert requirements["top_full_document_anchor"]["selected_model_id"] is None
    assert requirements["provider_reconciliation"]["verified_starting_spend_usd"] is None
    assert requirements["gemma_recovery"]["spend_authorized"] is False
    assert requirements["capability_preflight"] == {
        "disposition": (
            "run the 106-question x five-model x K=2 capability QA before canary "
            "anchor selection"
        ),
        "planned_cells": 1_060,
        "proposed_cap_usd": 15,
        "manifest_path": None,
        "result_sha256": None,
    }
    assert requirements["transition_model"]["strategy"] == (
        "append_only_external_execution_manifests"
    )


def test_every_approved_decision_field_is_hash_frozen(protocol):
    mutations = [
        lambda value: value["primary_tests"]["family"][0].__setitem__(
            "estimand", "error(b0) - error(sequential_b2)"
        ),
        lambda value: value["launch_gates"].__setitem__(
            "completion_gate", "50% completion"
        ),
        lambda value: value["query_screening"].__setitem__(
            "checker_failure_policy", "silently allow"
        ),
        lambda value: value["cumulative_spend"].__setitem__(
            "reconciliation_policy", "ignore discrepancies"
        ),
    ]
    for mutate in mutations:
        changed = deepcopy(protocol)
        mutate(changed["decisions"])
        with pytest.raises(
            phase2_plan.ProtocolValidationError, match="approved decision semantics drifted"
        ):
            phase2_plan.validate_protocol(changed)


def test_secondary_family_and_anchor_selection_are_analysis_complete(protocol):
    decisions = protocol["decisions"]
    secondary = decisions["secondary_analyses"]
    assert secondary["family"] == ["C", "D_clean"]
    assert secondary["sidedness"] == "two_sided"
    assert secondary["invalid_policy"] == (
        "strict INVALID counts wrong; valid-only sensitivity"
    )
    assert secondary["completion_policy"] == "100% exact provider-cell completion"
    assert "world-stratified" in secondary["inference"]

    selection = decisions["capability_measurement"]["anchor_selection"]
    assert selection["candidate_models"] == protocol["roster"]["judges"]
    assert "212 mirrored cells" in selection["primary_score"]
    assert selection["completion_policy"].startswith("100% exact")
    assert selection["tie_break_order"][-1] == (
        "lexicographically ascending exact model ID"
    )


@pytest.mark.parametrize(
    ("section", "condition_id", "field", "replacement"),
    [
        ("debate_grid", "b0", "query_budget", 2),
        ("debate_grid", "sequential_b2", "oracle_mode", "none"),
        ("debate_grid", "batch_same_qa_b2", "presentation", "sequential"),
        ("debate_grid", "placebo_b2", "oracle_mode", "clean"),
        ("no_debate_references", "clean_b2", "query_budget", 3),
        ("no_debate_references", "placebo_b2", "oracle_mode", "clean"),
    ],
)
def test_condition_semantics_are_frozen_fail_closed(
    protocol, section, condition_id, field, replacement
):
    mutated = deepcopy(protocol)
    condition = next(
        item for item in mutated[section]["conditions"] if item["id"] == condition_id
    )
    condition[field] = replacement
    with pytest.raises(phase2_plan.ProtocolValidationError, match="condition semantics"):
        phase2_plan.validate_protocol(mutated)


def test_capability_preflight_has_a_separate_fail_closed_spend_gate(protocol):
    mutated = deepcopy(protocol)
    mutated["authorization"]["capability_preflight_spend_authorized"] = True
    with pytest.raises(
        phase2_plan.ProtocolValidationError, match="capability preflight spend"
    ):
        phase2_plan.validate_protocol(mutated)

    recovery_drift = deepcopy(protocol)
    recovery_drift["materialization_requirements"]["gemma_recovery"][
        "proposed_cap_usd"
    ] = 2_000
    with pytest.raises(phase2_plan.ProtocolValidationError, match="Gemma recovery"):
        phase2_plan.validate_protocol(recovery_drift)


def test_full_approved_main_plan_counts_are_exact(plan):
    summary = plan["summary"]
    assert summary["main_question_count"] == 82
    assert summary["base_debate_transcript_cells"] == 492
    assert summary["capped_debate_transcript_cells"] == 492
    assert summary["debate_transcript_cells"] == 984
    assert summary["base_debate_judgment_cells"] == 15_744
    assert summary["cap_protection_judgment_cells"] == 984
    assert summary["empty_evidence_judgment_cells"] == 492
    assert summary["full_document_judgment_cells"] == 984
    assert summary["debate_judgment_cells"] == 18_204
    assert summary["no_debate_judgment_cells"] == 2_952
    assert summary["capability_qa_cells"] == 1_060
    assert summary["capability_preflight_cells"] == 1_060
    assert summary["post_canary_main_cells"] == 22_140
    assert summary["judgment_cells"] == 22_216
    assert summary["all_cells"] == 23_200
    assert summary["debate_judgments_by_condition"] == {
        "b0": 3_936,
        "batch_same_qa_b2": 3_936,
        "placebo_b2": 3_936,
        "sequential_b2": 3_936,
    }
    assert summary["no_debate_judgments_by_condition"] == {
        "b0": 984,
        "clean_b2": 984,
        "placebo_b2": 984,
    }
    assert summary["batch_to_sequential_dependency_edges"] == 3_936


def test_cell_enumeration_and_keys_are_deterministic(protocol, main_question_ids, plan):
    again = phase2_plan.enumerate_cells(protocol, reversed(main_question_ids))
    assert [cell["cell_key"] for cell in again] == [
        cell["cell_key"] for cell in plan["cells"]
    ]
    assert phase2_plan.duplicate_cell_keys(plan["cells"]) == ()

    first = plan["cells"][0]
    expected = phase2_plan.make_cell_key(
        protocol["cell_key_namespace"],
        kind=first["kind"],
        condition=first["condition"],
        question_id=first["question_id"],
        judge_model=first["judge_model"],
        debater_model=first["debater_model"],
        transcript_index=first["transcript_index"],
        replicate_index=first["replicate_index"],
        query_budget=first["query_budget"],
    )
    assert first["cell_key"] == expected


def test_every_batch_cell_depends_on_matching_sequential_and_transcript(plan):
    by_key = {cell["cell_key"]: cell for cell in plan["cells"]}
    batch_cells = [cell for cell in plan["cells"]
                   if cell["condition"] == "batch_same_qa_b2"]
    assert len(batch_cells) == 3_936
    for batch in batch_cells:
        dependencies = [by_key[key] for key in batch["dependency_keys"]]
        sequential = [cell for cell in dependencies
                      if cell["condition"] == "sequential_b2"]
        transcripts = [cell for cell in dependencies
                       if cell["kind"] == "debate_transcript"]
        assert len(sequential) == len(transcripts) == 1
        for field in (
            "question_id", "judge_model", "debater_model", "transcript_index",
            "replicate_index",
        ):
            assert sequential[0][field] == batch[field]


def test_optional_arms_have_distinct_and_correct_transcript_dependencies(plan):
    by_key = {cell["cell_key"]: cell for cell in plan["cells"]}
    kinds = Counter(cell["kind"] for cell in plan["cells"])
    assert kinds["cap_protection_judgment"] == 984
    assert kinds["empty_evidence_judgment"] == 492
    assert kinds["full_document_judgment"] == 984

    for cell in plan["cells"]:
        if cell["kind"] not in {
            "cap_protection_judgment",
            "empty_evidence_judgment",
            "full_document_judgment",
        }:
            continue
        dependencies = [by_key[key] for key in cell["dependency_keys"]]
        transcript_dependencies = [dep for dep in dependencies if "transcript" in dep["kind"]]
        assert len(transcript_dependencies) == 1
        expected_kind = (
            "capped_debate_transcript"
            if cell["kind"] == "cap_protection_judgment"
            else "debate_transcript"
        )
        assert transcript_dependencies[0]["kind"] == expected_kind

    full_document = [cell for cell in plan["cells"]
                     if cell["kind"] == "full_document_judgment"]
    assert Counter(cell["judge_model"] for cell in full_document) == {
        "meta-llama/Llama-3.3-70B-Instruct-Turbo": 492,
        "selector:highest_pre_frozen_solo_qa_roster_judge": 492,
    }


def test_capability_cells_cover_all_106_questions_and_five_models(protocol, plan):
    cells = [cell for cell in plan["cells"] if cell["kind"] == "capability_qa"]
    assert len(cells) == 1_060
    assert len({cell["question_id"] for cell in cells}) == 106
    assert {cell["judge_model"] for cell in cells} == set(
        protocol["decisions"]["capability_measurement"]["models"])
    assert {cell["replicate_index"] for cell in cells} == {0, 1}
    assert all(cell["debater_model"] is None for cell in cells)
    assert all(cell["transcript_index"] is None for cell in cells)
    assert all(cell["dependency_keys"] == [] for cell in cells)


def test_no_debate_cells_have_k3_and_no_debate_only_dimensions(plan):
    cells = [cell for cell in plan["cells"] if cell["kind"] == "no_debate_judgment"]
    assert len(cells) == 2_952
    assert {cell["replicate_index"] for cell in cells} == {0, 1, 2}
    assert all(cell["debater_model"] is None for cell in cells)
    assert all(cell["transcript_index"] is None for cell in cells)
    assert all(cell["dependency_keys"] == [] for cell in cells)


def test_legacy_bridge_is_absent_and_financial_boundary_is_not_authority(protocol, plan):
    assert not any("legacy" in cell["kind"] or "legacy" in cell["condition"]
                   for cell in plan["cells"])
    spend = protocol["decisions"]["cumulative_spend"]
    assert spend["working_budget_usd"] == 1_200
    assert spend["provisional_empirical_phase2_planning_band_usd"] == {
        "minimum": 650,
        "maximum": 1_150,
    }
    assert spend["incremental_phase2_hard_ceiling_usd"] == 1_500
    assert spend["provisional_cumulative_project_ceiling_usd"] == 1_708
    assert spend["provider_topups_usd"] == [500, 1_300]
    assert spend["estimated_prepaid_credit_usd"] == 1_592
    assert protocol["execution_authorized"] is False


def test_canary_inventory_is_exact_disjoint_and_dependency_safe(protocol, main_question_ids):
    canary = phase2_plan.build_canary_plan(protocol)
    summary = canary["summary"]
    assert summary["all_cells"] == 945
    assert summary["transcript_cells"] == 50
    assert summary["outcome_cells"] == 895
    assert summary["symbolic_model_cells"] == 1
    assert "capability preflight" in summary["execution_blockers"][0]
    assert summary["by_kind"] == {
        "canary_cap_protection_judgment": 4,
        "canary_capped_debate_transcript": 2,
        "canary_debate_judgment": 672,
        "canary_debate_transcript": 48,
        "canary_empty_evidence_judgment": 1,
        "canary_full_document_judgment": 2,
        "canary_no_debate_judgment": 216,
    }
    assert set(summary["offline_checker_fixture_outcomes_required"]) == {
        "accept", "reject_then_accept", "reject_twice_consumes_slot",
        "malformed_halts", "outage_halts",
    }
    assert summary["offline_fixture_cells_in_canary_count"] == 0
    canary_question_ids = {cell["question_id"] for cell in canary["cells"]}
    assert canary_question_ids == set(protocol["question_set"][
        "calibration_excluded_question_ids"])
    assert canary_question_ids.isdisjoint(main_question_ids)
    assert phase2_plan.duplicate_cell_keys(canary["cells"]) == ()

    by_key = {cell["cell_key"]: cell for cell in canary["cells"]}
    batches = [cell for cell in canary["cells"]
               if cell["condition"] == "batch_same_qa_b2"]
    assert len(batches) == 96
    for batch in batches:
        dependencies = [by_key[key] for key in batch["dependency_keys"]]
        assert {dependency["kind"] for dependency in dependencies} == {
            "canary_debate_transcript", "canary_debate_judgment",
        }
        assert any(dependency["condition"] == "sequential_b2"
                   for dependency in dependencies)


def test_duplicate_detection_and_missing_dependency_are_fail_closed(protocol, plan):
    first = plan["cells"][0]
    assert phase2_plan.duplicate_cell_keys([first, dict(first)]) == (first["cell_key"],)
    with pytest.raises(phase2_plan.PlanValidationError, match="duplicate cell keys"):
        phase2_plan.validate_cells([first, dict(first)], protocol["cell_key_namespace"])

    debate = next(cell for cell in plan["cells"] if cell["kind"] == "debate_judgment")
    broken = dict(debate)
    broken["dependency_keys"] = ["missing"]
    with pytest.raises(phase2_plan.PlanValidationError, match="missing dependencies"):
        phase2_plan.validate_cells([broken], protocol["cell_key_namespace"])

    by_key = {cell["cell_key"]: cell for cell in plan["cells"]}
    transcript = by_key[debate["dependency_keys"][0]]
    capability = next(cell for cell in plan["cells"] if cell["kind"] == "capability_qa")
    extra = dict(debate)
    extra["dependency_keys"] = [*debate["dependency_keys"], capability["cell_key"]]
    with pytest.raises(phase2_plan.PlanValidationError, match="extra dependency"):
        phase2_plan.validate_cells(
            [transcript, capability, extra], protocol["cell_key_namespace"]
        )


def test_plan_is_json_serializable(plan):
    decoded = json.loads(json.dumps(plan))
    assert decoded["summary"]["all_cells"] == 23_200
    assert len(decoded["cells"]) == 23_200


def test_cli_prints_summary_json_without_network_access(monkeypatch, capsys):
    def forbid_network(*args, **kwargs):
        raise AssertionError("offline Phase-2 planner attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    rc = phase2_plan.main([
        "--protocol", str(PROTOCOL_PATH),
        "--project-root", str(ROOT),
        "--json",
        "--summary-only",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "approved_design_pending_materialization"
    assert payload["offline_planning_only"] is True
    assert payload["execution_authorized"] is False
    assert payload["summary"]["all_cells"] == 23_200
    assert payload["materialization_requirements"]["status"] == (
        "pending_before_protocol_freeze")
    assert "cells" not in payload
