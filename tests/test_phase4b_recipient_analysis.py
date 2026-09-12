import copy
import hashlib
import json
import random

import pytest

from scripts import phase4b_recipient_analysis as analysis


@pytest.fixture
def panel():
    calls, units, rows = [], [], []
    for world, count in analysis.WORLD_COUNTS.items():
        for number in range(count):
            qid = f"{world}-{number:03d}"
            for d, debater in enumerate(analysis.DEBATERS):
                for transcript in (0, 1):
                    for side in (0, 1):
                        uid, gold = f"{qid}-{d}-{transcript}-{side}", "A" if side == 0 else "B"
                        units.append({"unit_id": uid, "question_id": qid, "world": world, "debater": debater,
                                      "transcript_index": transcript, "side": side, "correct_position": gold})
                        for judge, arm in analysis.CELLS:
                            donor, repair = arm.rsplit("_", 1)
                            call = {"cell_id": f"{uid}:{judge}:{arm}", "unit_id": uid, "judge": judge, "arm": arm,
                                    "packet_id": f"{uid}:{arm}", "messages_sha256": "a" * 64,
                                    "donor_arm": donor, "repair_status": repair}
                            calls.append(call)
                            rows.append({**call, "raw_verdict_text": f"VERDICT: Position {gold}\nCONFIDENCE: 3\nREASONING: Fixture.",
                                         "attempt_id": "attempt:" + call["cell_id"], "completed_at": "2026-09-12T10:00:00Z",
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 12}, "cost_usd": "0.001",
                                         "configuration_valid": True, "returned_model_id": judge, "request_sha256": "b" * 64})
    return calls, units, rows


def mark_error(row):
    text = row["raw_verdict_text"]
    row["raw_verdict_text"] = text.replace("Position A", "Position B") if "Position A" in text else text.replace("Position B", "Position A")


def diagnostic(panel):
    return analysis.analyze_rows(*panel, bootstrap_replicates=101, bootstrap_seed=7)[0]


def selected_units(units, count):
    first = {}
    for unit in units:
        first.setdefault(unit["question_id"], unit["unit_id"])
    return set(list(first.values())[:count])


def test_known_pooled_benefit_and_four_donor_recipient_transitions(panel):
    for row in panel[2]:
        if row["arm"].endswith("_original"):
            mark_error(row)
    result = diagnostic(panel)
    pooled, difference = result["primary"].values()
    assert pooled["integer_numerator"] == 2624 and pooled["denominator"] == 2624 and pooled["estimate"] == 1
    assert difference["integer_numerator"] == 0 and difference["denominator"] == 1312 and difference["estimate"] == 0
    assert all(v["estimate"] == 1 for v in result["descriptive_effects"].values())
    assert [r["strict_errors"] for r in result["arm_counts"]] == [656, 0, 656, 0, 656, 0, 656, 0]
    assert all(p["error_to_correct"] == 656 and p["correct_to_error"] == 0 for p in result["paired_transitions_descriptive"])
    assert not result["formal_gates_evaluated"] and not result["stage_4c_semantic_entry_eligible"]


@pytest.mark.parametrize("count,passes", [(78, False), (79, True)])
def test_frozen_pooled_integer_boundary_and_positive_transfer_gate(panel, count, passes):
    ids = selected_units(panel[1], count)
    for row in panel[2]:
        if row["unit_id"] in ids and row["judge"] == analysis.QWEN and row["arm"] == "qwen_history_original":
            mark_error(row)
    result, scored = analysis.analyze_rows(*panel)
    pooled = result["primary"]["R_pooled"]
    assert pooled["integer_numerator"] == count and pooled["estimate"] == pytest.approx(count / 2624)
    assert pooled["statistical_pass"] and pooled["practical_pass"] is passes
    assert pooled["decision_gate_pass"] is passes and pooled["semantic_followup_gate_pass"] is passes
    assert result["stage_4c_semantic_entry_eligible"] is passes
    assert result["stage_4c_paid_execution_authorized"] is False and result["independent_human_validation"] is False
    assert len(scored) == 5248 and result["bootstrap"]["seed"] == 2026091203


@pytest.mark.parametrize("count,passes", [(39, False), (40, True)])
def test_recipient_difference_integer_boundary_cannot_alone_qualify_transfer(panel, count, passes):
    ids = selected_units(panel[1], count)
    for row in panel[2]:
        if row["unit_id"] in ids and row["judge"] == analysis.QWEN and row["arm"] == "qwen_history_original":
            mark_error(row)
    result, _ = analysis.analyze_rows(*panel)
    contrast = result["primary"]["R_recipient_difference"]
    assert contrast["integer_numerator"] == count and contrast["estimate"] == pytest.approx(count / 1312)
    assert contrast["statistical_pass"] and contrast["practical_pass"] is passes
    assert contrast["semantic_followup_gate_pass"] is passes
    assert not result["stage_4c_semantic_entry_eligible"]


def test_significant_pooled_harm_never_qualifies_positive_transfer(panel):
    for row in panel[2]:
        if row["arm"].endswith("_repaired"):
            mark_error(row)
    result, _ = analysis.analyze_rows(*panel)
    pooled = result["primary"]["R_pooled"]
    assert pooled["estimate"] == -1 and pooled["integer_numerator"] == -2624
    assert pooled["decision_gate_pass"] and pooled["semantic_followup_gate_pass"]
    assert not result["stage_4c_semantic_entry_eligible"]
    assert all(p["correct_to_error"] == 656 and p["net_repairs"] == -656 for p in result["paired_transitions_descriptive"])


def test_opposite_recipient_effects_have_zero_pooled_benefit_and_no_transfer(panel):
    for row in panel[2]:
        if row["arm"].endswith("_original" if row["judge"] == analysis.QWEN else "_repaired"):
            mark_error(row)
    result, _ = analysis.analyze_rows(*panel)
    assert result["primary"]["R_pooled"]["estimate"] == 0
    assert result["primary"]["R_pooled"]["p_two_sided"] == 1
    difference = result["primary"]["R_recipient_difference"]
    assert difference["estimate"] == 2 and difference["semantic_followup_gate_pass"]
    assert result["descriptive_effects"]["R_Qwen"]["estimate"] == 1
    assert result["descriptive_effects"]["R_Llama"]["estimate"] == -1
    assert not result["stage_4c_semantic_entry_eligible"]


def test_missing_cell_is_not_wrong_and_suppresses_complete_panel_inference(panel):
    removed = panel[2].pop(0)
    result, scored = analysis.analyze_rows(*panel)
    assert result["status"] == "incomplete" and result["coverage"]["administrative_missing"] == 1
    assert result["primary"] is None and result["common_valid_sensitivity"] is None
    assert not result["formal_gates_evaluated"] and not result["stage_4c_semantic_entry_eligible"]
    assert sum(r["strict_errors"] for r in result["arm_counts"]) == 0 and len(scored) == 5247
    assert result["strict_missing_outcome_bounds"]["R_pooled"]["lower_numerator"] == 0
    assert result["strict_missing_outcome_bounds"]["R_pooled"]["upper_numerator"] == 1
    assert result["coverage"]["missing_cell_ids"] == [removed["cell_id"]]
    assert result["paired_transitions_descriptive"][0]["administrative_missing_pairs"] == 1


def test_all_eight_cells_select_common_valid_support_with_equal_debater_weights(panel):
    first = panel[1][0]
    peer = next(u for u in panel[1] if u["question_id"] == first["question_id"] and u["debater"] == first["debater"]
                and u["unit_id"] != first["unit_id"])
    for row in panel[2]:
        if row["unit_id"] == first["unit_id"] and row["judge"] == analysis.LLAMA and row["arm"] == "llama_history_repaired":
            row["raw_verdict_text"] = ""
        if row["unit_id"] == peer["unit_id"] and row["judge"] == analysis.QWEN and row["arm"] == "qwen_history_original":
            mark_error(row)
    result = diagnostic(panel)
    valid = result["common_valid_sensitivity"]
    assert valid["defined"] and valid["support"]["retained_units"] == 655
    # One of three retained units for one debater; equal debaters, four donor /
    # recipient pairs, and 82 questions. Do not reweight by seven total units.
    assert valid["primary"]["R_pooled"]["estimate"] == pytest.approx(1 / (3 * 2 * 4 * 82))
    assert valid["primary"]["R_recipient_difference"]["estimate"] == pytest.approx(1 / (3 * 2 * 2 * 82))
    assert result["coverage"]["completed_invalid"] == 1


def test_empty_question_debater_valid_support_is_undefined(panel):
    first = panel[1][0]
    ids = {u["unit_id"] for u in panel[1] if u["question_id"] == first["question_id"] and u["debater"] == first["debater"]}
    for row in panel[2]:
        if row["unit_id"] in ids and row["judge"] == analysis.LLAMA and row["arm"] == "llama_history_repaired":
            row["raw_verdict_text"] = "Malformed"
    result = diagnostic(panel)
    common = result["common_valid_sensitivity"]
    assert not common["defined"] and common["primary"] is None
    assert len(common["support"]["empty_question_debater_strata"]) == 1
    assert not result["stage_4c_semantic_entry_eligible"]


def test_coefficient_specific_invalid_bounds_differ_from_uniform_recoding(panel):
    uid = panel[1][0]["unit_id"]
    for row in panel[2]:
        if row["unit_id"] == uid and row["judge"] == analysis.QWEN and row["arm"].startswith("qwen_history_"):
            row["raw_verdict_text"] = "VERDICT: A or B"
    result = diagnostic(panel)
    assert result["coverage"]["completed_invalid"] == 2
    for name in analysis.PRIMARY:
        bounds = result["invalid_and_missing_outcome_bounds"][name]
        assert (bounds["lower_numerator"], bounds["upper_numerator"]) == (-1, 1)
        assert result["primary"][name]["integer_numerator"] == 0
        assert result["uniform_invalid_as_correct_sensitivity"]["primary"][name]["estimate"] == 0


def test_invalid_only_apparent_benefit_fails_semantic_gate(panel):
    ids = selected_units(panel[1], 79)
    for row in panel[2]:
        if row["unit_id"] in ids and row["judge"] == analysis.QWEN and row["arm"] == "qwen_history_original":
            row["raw_verdict_text"] = ""
    result, _ = analysis.analyze_rows(*panel)
    pooled = result["primary"]["R_pooled"]
    assert pooled["integer_numerator"] == 79 and pooled["decision_gate_pass"]
    assert not pooled["semantic_followup_gate_pass"] and not result["stage_4c_semantic_entry_eligible"]
    assert result["invalid_and_missing_outcome_bounds"]["R_pooled"]["lower"] == 0
    assert result["common_valid_sensitivity"]["primary"]["R_pooled"]["estimate"] == 0


def test_scoring_is_idempotent_only_for_identical_completed_rows(panel):
    calls, units = analysis.validate_panel(*panel[:2])
    scored, _, duplicates, cost = analysis.score_rows(calls, units, [panel[2][0], copy.deepcopy(panel[2][0])])
    assert len(scored) == duplicates == 1 and cost == "0.001"
    bad = {**panel[2][0], "attempt_id": "different attempt"}
    with pytest.raises(analysis.AnalysisError, match="Conflicting completed duplicate"):
        analysis.score_rows(calls, units, [panel[2][0], bad])


@pytest.mark.parametrize("mutation", [{"configuration_valid": None}, {"returned_model_id": "wrong"},
                                     {"packet_id": "wrong"}, {"messages_sha256": "b" * 64}, {"request_sha256": "bad"},
                                     {"raw_verdict_text": None}, {"cost_usd": "NaN"}, {"usage": None}])
def test_bad_completed_identity_or_accounting_rejected(panel, mutation):
    calls, units = analysis.validate_panel(*panel[:2])
    with pytest.raises(analysis.AnalysisError):
        analysis.score_rows(calls, units, [{**panel[2][0], **mutation}])


def test_completed_unknown_usage_is_scored_with_retained_uncertainty(panel):
    row = {**panel[2][0], "usage": None, "cost_usd": 0, "uncertain_usd": "0.3"}
    result, scored = analysis.analyze_rows(panel[0], panel[1], [row])
    assert result["completed_responses_missing_usage"] == 1 and result["completed_response_uncertain_usd"] == "0.3"
    assert scored[0]["valid"] and scored[0]["strict_error"] == 0


def test_joint_bootstrap_uses_declared_seed_and_paired_world_draws(panel):
    selected = {u["unit_id"] for u in panel[1] if u["unit_id"].endswith("-0-0-0")
                and int(u["question_id"].rsplit("-", 1)[1]) % 3 == 0}
    for row in panel[2]:
        if row["judge"] == analysis.QWEN and row["arm"] == "qwen_history_original" and row["unit_id"] in selected:
            mark_error(row)
    result = diagnostic(panel)
    questions = sorted({u["question_id"] for u in panel[1]})
    groups = [[q for q in questions if q.startswith(world)] for world in sorted(analysis.WORLD_COUNTS)]
    rng = random.Random(7)
    draws = [sum(int(rng.choice(group).rsplit("-", 1)[1]) % 3 == 0 for group in groups for _ in group) / (82 * 32)
             for _ in range(101)]
    def quantile(probability):
        values = sorted(draws)
        location = 100 * probability
        low = int(location)
        return values[low] + (location - low) * (values[min(100, low + 1)] - values[low])
    assert result["primary"]["R_pooled"]["estimate"] == pytest.approx(len(selected) / (82 * 32))
    assert result["primary"]["R_pooled"]["ci95"] == [quantile(.025), quantile(.975)]
    assert result["primary"]["R_recipient_difference"]["ci97_5"] == [2 * quantile(.0125), 2 * quantile(.9875)]
    p = min(1, 2 * min((1 + sum(x <= 0 for x in draws)) / 102, (1 + sum(x >= 0 for x in draws)) / 102))
    assert result["primary"]["R_pooled"]["p_holm"] == min(1, 2 * p)
    assert not result["formal_gates_evaluated"]


def write_inputs(path, panel):
    calls, units, _ = panel
    packets = {}
    for call in calls:
        messages = [{"role": "user", "content": "Synthetic matched history."}]
        digest = analysis.base._message_sha(messages)
        call["messages_sha256"] = digest
        packets[call["packet_id"]] = {k: call[k] for k in ("packet_id", "donor_arm", "repair_status")}
        packets[call["packet_id"]].update(messages=messages, messages_sha256=digest)
    files = {"calls.jsonl": calls, "units_private.jsonl": units, "packets.jsonl": list(packets.values()), "edit_map_private.jsonl": []}
    path.mkdir()
    bindings = {}
    for name, data in files.items():
        raw = "".join(json.dumps(r, sort_keys=True) + "\n" for r in data).encode()
        (path / name).write_bytes(raw)
        bindings[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    manifest = {"schema_version": "phase4b_recipient_prepared_panel_v1", "phase4_protocol_sha256": analysis.PROTOCOL_SHA256,
                "outputs": bindings, "analysis_seed": analysis.BOOTSTRAP_SEED, "arms": list(analysis.ARMS),
                "review_mode": "ai_source_review", "independent_human_validation": False}
    (path / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_cli_hash_bound_incomplete_output_preserves_inputs(tmp_path, monkeypatch, panel):
    inputs, results, out = tmp_path / "prepared", tmp_path / "results.jsonl", tmp_path / "analysis"
    manifest = write_inputs(inputs, panel)
    results.write_text("")
    before = {p.name: p.read_bytes() for p in inputs.iterdir()}
    monkeypatch.setattr(analysis.sys, "argv", ["analysis", "--inputs", str(inputs), "--results", str(results), "--out", str(out)])
    assert analysis.main() == 0
    result = json.loads((out / "phase4b_recipient_analysis.json").read_text())
    assert result["status"] == "incomplete" and result["coverage"]["administrative_missing"] == 5248
    assert result["provenance"]["input_hashes"] == {n: b["sha256"] for n, b in manifest["outputs"].items()}
    assert {p.name: p.read_bytes() for p in inputs.iterdir()} == before
    with (inputs / "edit_map_private.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(analysis.AnalysisError, match="input hash mismatch"):
        analysis.load_inputs(inputs)


def test_panel_rejects_duplicate_arm_and_broken_mirror(panel):
    calls, units, _ = panel
    bad = copy.deepcopy(calls)
    bad[0]["arm"] = bad[1]["arm"]
    with pytest.raises(analysis.AnalysisError):
        analysis.validate_panel(bad, units)
    units[0]["correct_position"] = units[1]["correct_position"]
    with pytest.raises(analysis.AnalysisError, match="Mirrored"):
        analysis.validate_panel(calls, units)
