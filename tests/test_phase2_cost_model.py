import json
import socket
from pathlib import Path

from rejudge import phase2_cost_model, phase2_plan


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "rejudge" / "phase2_cost_model.json"


def _model():
    return phase2_cost_model.build_from_paths(project_root=ROOT)


def _protocol_and_plan():
    protocol = phase2_plan.load_protocol(phase2_plan.DEFAULT_PROTOCOL_PATH)
    plan = phase2_plan.build_plan(
        protocol, phase2_plan.load_main_question_ids(protocol, ROOT))
    return protocol, plan


def test_tracked_cost_model_is_deterministic_and_current():
    expected = phase2_cost_model.render_cost_model(_model())
    assert ARTIFACT.read_text(encoding="utf-8") == expected


def test_cost_model_covers_main_supplement_canary_checker_and_retry_calls():
    model = _model()
    assert model["inventory"] == {
        "approved_phase2_cells": 23_200,
        "approved_phase2_transcript_cells": 984,
        "approved_phase2_analysis_cells": 22_216,
        "capability_preflight_cells": 1_060,
        "post_canary_main_cells": 22_140,
        "gemma_recovery_supplement_cells": 11,
        "canary_transcript_cells": 50,
        "canary_outcome_cells": 895,
        "canary_symbolic_model_cells": 1,
        "all_planned_cells_including_gemma_and_canary": 24_156,
    }
    calls = model["call_model"]
    assert calls["approved_phase2_calls_before_checker"] == 57_640
    assert calls["capability_preflight_calls_before_checker"] == 1_060
    assert calls["post_canary_main_calls_before_checker"] == 56_580
    assert calls["gemma_and_canary_calls_before_checker"] == 2_214
    assert calls["total_calls_before_checker"] == 59_854
    assert calls["post_canary_main_query_generation_calls"] == 19_680
    assert calls["post_canary_main_checker_calls_before_retries"] == 19_680
    assert calls["canary_query_generation_calls"] == 672
    assert calls["canary_checker_calls_before_retries"] == 672
    assert calls["canary_query_budget_slots_and_checker_calls"] == 672
    assert calls["post_canary_main_oracle_calls"] == 9_840
    assert calls["canary_oracle_calls"] == 336
    assert calls["total_query_generation_calls_before_retries"] == 20_352
    assert calls["total_oracle_calls"] == 10_176
    assert calls["total_checker_calls_before_retries"] == 20_352
    assert calls["total_calls_before_semantic_or_transport_retries"] == 80_206
    assert calls["semantic_retry_planning"]["planned_extra_calls"] == 2_036
    assert calls["total_calls_after_planned_semantic_retries"] == 82_242
    assert sum(
        item["calls_before_checker"] + item["checker_calls"]
        for item in model["line_items"]
    ) == 82_242


def test_call_counts_are_derived_from_enumerated_cell_and_protocol_semantics():
    protocol, plan = _protocol_and_plan()
    main = phase2_cost_model.derive_call_inventory(plan["cells"], protocol)
    canary_plan = phase2_plan.build_canary_plan(protocol)
    canary = phase2_cost_model.derive_call_inventory(canary_plan["cells"], protocol)
    assert main == {
        "cells": 23_200,
        "transcript_generation_calls": 5_904,
        "outcome_calls": 22_216,
        "query_generation_calls": 19_680,
        "oracle_calls": 9_840,
        "checker_calls_before_retries": 19_680,
        "calls_before_checker": 57_640,
        "calls_including_checker_before_retries": 77_320,
    }
    assert canary["query_generation_calls"] == 672
    assert canary["oracle_calls"] == 336
    assert canary["checker_calls_before_retries"] == 672

    cells_by_condition = {
        condition: next(
            cell for cell in plan["cells"]
            if cell["kind"] == "debate_judgment" and cell["condition"] == condition)
        for condition in ("sequential_b2", "batch_same_qa_b2", "placebo_b2")
    }
    clean = phase2_cost_model.derive_call_inventory(
        [cells_by_condition["sequential_b2"]], protocol)
    batch = phase2_cost_model.derive_call_inventory(
        [cells_by_condition["batch_same_qa_b2"]], protocol)
    placebo = phase2_cost_model.derive_call_inventory(
        [cells_by_condition["placebo_b2"]], protocol)
    assert (clean["query_generation_calls"], clean["oracle_calls"],
            clean["checker_calls_before_retries"]) == (2, 2, 2)
    assert (batch["query_generation_calls"], batch["oracle_calls"],
            batch["checker_calls_before_retries"]) == (0, 0, 0)
    assert (placebo["query_generation_calls"], placebo["oracle_calls"],
            placebo["checker_calls_before_retries"]) == (2, 0, 2)


def test_cost_controls_distinguish_forecast_working_budget_credit_and_ceiling():
    model = _model()
    controls = model["cost_controls_usd"]
    assert controls["communicated_planning_minimum"] == 650
    assert controls["communicated_planning_maximum"] == 1_150
    assert controls["working_budget"] == 1_200
    assert controls["incremental_hard_ceiling"] == 1_500
    assert controls["estimated_prepaid_credit"] == 1_592
    assert controls["estimate_status"] == "empirical_manual_provisional_not_token_derived"
    assert controls["communicated_planning_maximum"] <= controls["working_budget"]
    assert controls["working_budget"] < controls["incremental_hard_ceiling"]
    assert model["call_model"]["checker_model_id"] is None
    assert "not expected spend" in model["limitations"][-1]


def test_frozen_prices_and_inputs_are_hash_bound():
    model = _model()
    schedule = model["price_schedule"]
    source = ROOT / schedule["source_path"]
    assert schedule["source_sha256"] == phase2_cost_model.canonical_json_file_sha256(source)
    raw = json.loads(source.read_text(encoding="utf-8"))
    assert schedule["prices_per_mtok"] == raw["prices_per_mtok"]
    protocol, plan = _protocol_and_plan()
    canary_plan = phase2_plan.build_canary_plan(protocol)
    bindings = model["input_bindings"]
    assert model["scope_sha256"] == phase2_cost_model.canonical_sha256(plan)
    assert bindings["approved_phase2_plan_sha256"] == (
        phase2_cost_model.canonical_sha256(plan))
    assert bindings["approved_phase2_cells_sha256"] == (
        phase2_cost_model.canonical_sha256(plan["cells"]))
    assert bindings["canary_plan_sha256"] == phase2_cost_model.canonical_sha256(canary_plan)
    assert bindings["canary_cells_sha256"] == phase2_cost_model.canonical_sha256(
        canary_plan["cells"])
    selector = bindings["gemma_recovery_selector"]
    selector_path = ROOT / selector["source_path"]
    assert selector["source_sha256"] == phase2_cost_model.canonical_json_file_sha256(
        selector_path)
    assert selector["selected_cell_count"] == len(
        json.loads(selector_path.read_text(encoding="utf-8")))
    for anchor in model["empirical_anchors"]:
        assert anchor["source_sha256"] == phase2_cost_model.normalized_text_file_sha256(
            ROOT / anchor["source"])
    assert (len(model["protocol_sha256"])
            == len(model["scope_sha256"])
            == len(model["canary_scope_sha256"])
            == 64)


def test_json_hash_is_independent_of_formatting_and_checkout_eol(tmp_path):
    lf = tmp_path / "lf.json"
    crlf = tmp_path / "crlf.json"
    lf.write_bytes(b'{\n  "a": 1,\n  "b": [2, 3]\n}\n')
    crlf.write_bytes(b'{\r\n"b":[2,3],\r\n"a":1\r\n}\r\n')
    assert (phase2_cost_model.canonical_json_file_sha256(lf)
            == phase2_cost_model.canonical_json_file_sha256(crlf))


def test_cost_model_cli_check_is_offline(monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("cost model attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    assert phase2_cost_model.main([
        "--project-root", str(ROOT),
        "--artifact", str(ARTIFACT),
        "--check",
    ]) == 0
