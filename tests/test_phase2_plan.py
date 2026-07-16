import json
import socket
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


def test_protocol_is_non_executable_draft_with_final_roster_and_frozen_prices(protocol):
    models = json.loads(CALIBRATION_MODELS_PATH.read_text(encoding="utf-8"))
    expected_judges = [
        models["judges"]["low_fallback"],
        models["judges"]["mid_gemma"],
        models["judges"]["anchor"],
        models["judges"]["top_oss"],
    ]
    assert protocol["status"] == "draft_requires_signoff"
    assert protocol["offline_planning_only"] is True
    assert protocol["execution_authorized"] is False
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

    all_source_ids = set(main_question_ids) | excluded
    assert len(all_source_ids) == question_set["expected_total_question_count"] == 106


def test_protocol_keeps_all_signoff_choices_explicitly_unresolved(protocol):
    unresolved = protocol["unresolved"]
    assert set(unresolved) == {
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
    assert all(item["status"] == "unresolved_requires_signoff"
               for item in unresolved.values())
    assert unresolved["primary_tests"]["known_count"] == 3
    assert unresolved["primary_tests"]["known_multiplicity_adjustment"] == "Holm"
    assert unresolved["primary_tests"]["exact_test_definitions"] is None
    assert unresolved["capability_measurement"]["scoring_rule"] is None
    assert unresolved["cap_protection_secondary"]["exact_contrast"] is None
    assert unresolved["query_screening"]["model_checker_definition"] is None
    assert unresolved["design_scope_reconciliation"][
        "empty_evidence_table_control_disposition"] is None
    assert unresolved["secondary_analyses"]["no_debate_D_definition"] is None
    assert unresolved["secondary_analyses"]["resolvability_labels_source"] is None
    assert unresolved["execution_semantics"]["exact_prompt_bundle_and_hashes"] is None
    assert unresolved["launch_gates"]["invalid_rate_threshold"] is None
    assert unresolved["cumulative_spend"]["verified_starting_spend_usd"] is None
    assert unresolved["cumulative_spend"]["project_wide_locking_policy"] is None
    assert unresolved["cumulative_spend"]["approved_cumulative_ceiling_usd"] is None


def test_full_plan_counts_are_exact(plan):
    summary = plan["summary"]
    assert summary["main_question_count"] == 82
    assert summary["debate_transcript_cells"] == 492
    assert summary["debate_judgment_cells"] == 15_744
    assert summary["no_debate_judgment_cells"] == 2_952
    assert summary["judgment_cells"] == 18_696
    assert summary["all_cells"] == 19_188
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


def test_every_batch_cell_depends_on_the_matching_sequential_cell(plan):
    by_key = {cell["cell_key"]: cell for cell in plan["cells"]}
    batch_cells = [
        cell for cell in plan["cells"] if cell["condition"] == "batch_same_qa_b2"
    ]
    assert len(batch_cells) == 3_936
    for batch in batch_cells:
        dependencies = [by_key[key] for key in batch["dependency_keys"]]
        sequential = [cell for cell in dependencies if cell["condition"] == "sequential_b2"]
        transcripts = [cell for cell in dependencies if cell["kind"] == "debate_transcript"]
        assert len(sequential) == len(transcripts) == 1
        for field in (
            "question_id", "judge_model", "debater_model", "transcript_index",
            "replicate_index",
        ):
            assert sequential[0][field] == batch[field]
        for field in ("question_id", "debater_model", "transcript_index"):
            assert transcripts[0][field] == batch[field]


def test_no_debate_cells_have_k3_and_no_debate_only_dimensions(plan):
    cells = [cell for cell in plan["cells"] if cell["kind"] == "no_debate_judgment"]
    assert len(cells) == 2_952
    assert {cell["replicate_index"] for cell in cells} == {0, 1, 2}
    assert all(cell["debater_model"] is None for cell in cells)
    assert all(cell["transcript_index"] is None for cell in cells)
    assert all(cell["dependency_keys"] == [] for cell in cells)


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


def test_plan_is_json_serializable(plan):
    decoded = json.loads(json.dumps(plan))
    assert decoded["summary"]["all_cells"] == 19_188
    assert len(decoded["cells"]) == 19_188


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
    assert payload["status"] == "draft_requires_signoff"
    assert payload["offline_planning_only"] is True
    assert payload["execution_authorized"] is False
    assert payload["summary"]["all_cells"] == 19_188
    assert "cells" not in payload
