import copy
from fractions import Fraction
import hashlib
import json
import random
import sys

import pytest

from scripts import phase4_analysis as analysis


@pytest.fixture
def panel():
    calls, units, rows = [], [], []
    for world, count in analysis.WORLD_COUNTS.items():
        for number in range(count):
            qid = f"{world}-{number:03d}"
            for d, debater in enumerate(analysis.DEBATERS):
                for transcript in (0, 1):
                    for side in (0, 1):
                        uid = f"{qid}-{d}-{transcript}-{side}"
                        gold = "A" if side == 0 else "B"
                        units.append({"unit_id": uid, "question_id": qid, "world": world,
                                      "debater": debater, "transcript_index": transcript,
                                      "side": side, "correct_position": gold})
                        for judge, arm in analysis.CELLS:
                            call = {"cell_id": f"{uid}:{judge}:{arm}", "unit_id": uid,
                                    "judge": judge, "arm": arm}
                            calls.append(call)
                            rows.append({**call, "raw_verdict_text": f"VERDICT: Position {gold}\nCONFIDENCE: 3\nREASONING: Fixture.",
                                         "attempt_id": "attempt:" + call["cell_id"],
                                         "completed_at": "2026-09-12T10:00:00Z",
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 12},
                                         "cost_usd": "0.001"})
    return calls, units, rows


def mark_error(row):
    row["raw_verdict_text"] = row["raw_verdict_text"].replace(
        "Position A" if "Position A" in row["raw_verdict_text"] else "Position B",
        "Position B" if "Position A" in row["raw_verdict_text"] else "Position A")


def diagnostic(panel):
    return analysis.analyze_rows(*panel, bootstrap_replicates=101, bootstrap_seed=7)[0]


def test_hand_computed_six_arm_contrasts_and_mirrored_truth(panel):
    calls, units, rows = panel
    errors = {(analysis.QWEN, "empty"), (analysis.QWEN, "llama_history"),
              (analysis.LLAMA, "llama_history")}
    for row in rows:
        if (row["judge"], row["arm"]) in errors:
            mark_error(row)
    result = diagnostic(panel)
    assert result["primary"]["D_recipient"]["integer_numerator"] == -1312
    assert result["primary"]["D_source"]["integer_numerator"] == -1312
    assert result["primary"]["D_recipient"]["estimate"] == -1
    assert result["primary"]["D_source"]["estimate"] == -1
    assert result["descriptive_effects"]["recipient_source_interaction"]["estimate"] == 0
    assert result["descriptive_effects"]["Q_qwen_vs_empty"]["estimate"] == -1
    assert result["descriptive_effects"]["L_llama_vs_empty"]["estimate"] == 1
    assert [row["strict_errors"] for row in result["arm_counts"]] == [656, 0, 656, 0, 0, 656]
    assert not result["formal_gates_evaluated"]
    assert not result["stage_4b_semantic_entry_eligible"]


@pytest.mark.parametrize("count,passes", [(39, False), (40, True)])
def test_frozen_integer_boundary_and_semantic_gate(panel, count, passes):
    calls, units, rows = panel
    first_per_question = {}
    for unit in units:
        first_per_question.setdefault(unit["question_id"], unit["unit_id"])
    selected = set(list(first_per_question.values())[:count])
    for row in rows:
        if row["unit_id"] in selected and row["judge"] == analysis.QWEN and row["arm"] == "qwen_history":
            mark_error(row)
    result, scored = analysis.analyze_rows(calls, units, rows)
    assert result["formal_gates_evaluated"]
    assert len(scored) == 3936
    for contrast in result["primary"].values():
        assert contrast["integer_numerator"] == count
        assert contrast["estimate"] == pytest.approx(count / 1312)
        assert contrast["statistical_pass"]
        assert contrast["practical_pass"] is passes
        assert contrast["decision_gate_pass"] is passes
        assert contrast["semantic_followup_gate_pass"] is passes
    assert result["stage_4b_semantic_entry_eligible"] is passes


def test_common_valid_reweights_debaters_equally_after_unequal_drops(panel):
    calls, units, rows = panel
    first = units[0]
    peer = next(u for u in units if u["question_id"] == first["question_id"]
                and u["debater"] == first["debater"] and u["unit_id"] != first["unit_id"])
    for row in rows:
        if row["unit_id"] == first["unit_id"] and row["judge"] == analysis.LLAMA and row["arm"] == "llama_history":
            row["raw_verdict_text"] = "I cannot choose."
        if row["unit_id"] == peer["unit_id"] and row["judge"] == analysis.QWEN and row["arm"] == "qwen_history":
            mark_error(row)
    result = diagnostic(panel)
    common = result["common_valid_sensitivity"]
    assert common["defined"] and common["support"]["retained_units"] == 655
    # One error among three retained slots in one of two debaters, then two
    # donor histories and 82 questions: 1 / (3*2*2*82), not 1/(7*2*82).
    assert common["primary"]["D_recipient"]["estimate"] == pytest.approx(1 / 984)
    assert common["primary"]["D_source"]["estimate"] == pytest.approx(1 / 984)
    assert result["coverage"]["completed_invalid"] == 1


def test_empty_valid_stratum_is_undefined_and_cannot_qualify(panel):
    _, units, rows = panel
    chosen = {u["unit_id"] for u in units if u["question_id"] == units[0]["question_id"]
              and u["debater"] == units[0]["debater"]}
    for row in rows:
        if row["unit_id"] in chosen and row["judge"] == analysis.QWEN and row["arm"] == "qwen_history":
            row["raw_verdict_text"] = ""
    result = diagnostic(panel)
    common = result["common_valid_sensitivity"]
    assert not common["defined"]
    assert common["primary"] is None
    assert len(common["support"]["empty_question_debater_strata"]) == 1
    assert not result["stage_4b_semantic_entry_eligible"]


def test_coefficient_bounds_differ_from_uniform_invalid_recoding(panel):
    _, units, rows = panel
    for row in rows:
        if row["unit_id"] == units[0]["unit_id"] and row["arm"] == "qwen_history":
            row["raw_verdict_text"] = "VERDICT: A or B"
    result = diagnostic(panel)
    bounds = result["invalid_and_missing_outcome_bounds"]
    assert bounds["D_recipient"]["lower_numerator"] == -1
    assert bounds["D_recipient"]["upper_numerator"] == 1
    assert result["primary"]["D_recipient"]["integer_numerator"] == 0
    assert result["uniform_invalid_as_correct_sensitivity"]["primary"]["D_recipient"]["estimate"] == 0
    assert bounds["D_source"]["lower_numerator"] == 0
    assert bounds["D_source"]["upper_numerator"] == 2


def test_administrative_missing_is_not_error_or_formal_result(panel):
    calls, units, rows = panel
    removed = next(r for r in rows if r["judge"] == analysis.QWEN and r["arm"] == "empty")
    rows.remove(removed)
    result, scored = analysis.analyze_rows(calls, units, rows)
    assert result["status"] == "incomplete" and not result["formal_gates_evaluated"]
    assert result["coverage"]["administrative_missing"] == 1
    assert result["primary"] is None and result["common_valid_sensitivity"] is None
    assert sum(r["strict_errors"] for r in result["arm_counts"]) == 0
    assert result["strict_missing_outcome_bounds"]["D_recipient"]["lower_numerator"] == -2
    assert result["strict_missing_outcome_bounds"]["D_recipient"]["upper_numerator"] == 0
    assert not result["stage_4b_semantic_entry_eligible"] and len(scored) == 3935


def test_idempotent_duplicate_and_conflicting_completed_response(panel):
    calls, units, rows = panel
    indexed_calls, indexed_units = analysis.validate_panel(calls, units)
    scored, _, duplicates, cost = analysis.score_rows(indexed_calls, indexed_units, [rows[0], copy.deepcopy(rows[0])])
    assert len(scored) == 1 and duplicates == 1 and cost == "0.001"
    conflicting = copy.deepcopy(rows[0])
    conflicting["attempt_id"] = "different completed attempt"
    with pytest.raises(analysis.AnalysisError, match="Conflicting completed duplicate"):
        analysis.score_rows(indexed_calls, indexed_units, [rows[0], conflicting])


@pytest.mark.parametrize("mutation", [
    {"cell_id": "unplanned"}, {"judge": "wrong-judge"}, {"raw_verdict_text": None},
    {"cost_usd": "NaN"}, {"usage": {"prompt_tokens": -1, "completion_tokens": 2}},
    {"completed_at": ""}, {"configuration_valid": False}, {"returned_model_id": "different-model"},
    {"usage": None},
])
def test_bad_completed_contract_rejected(panel, mutation):
    calls, units, rows = panel
    indexed_calls, indexed_units = analysis.validate_panel(calls, units)
    row = {**rows[0], **mutation}
    with pytest.raises(analysis.AnalysisError):
        analysis.score_rows(indexed_calls, indexed_units, [row])


def test_completed_missing_usage_remains_scored_with_uncertain_exposure(panel):
    calls, units, rows = panel
    row = {**rows[0], "usage": None, "cost_usd": 0, "uncertain_usd": "0.75",
           "configuration_valid": True, "returned_model_id": rows[0]["judge"]}
    result, scored = analysis.analyze_rows(calls, units, [row])
    assert result["coverage"]["completed"] == 1
    assert result["completed_responses_missing_usage"] == 1
    assert result["completed_response_cost_usd"] == "0"
    assert result["completed_response_uncertain_usd"] == "0.75"
    assert scored[0]["valid"] and scored[0]["strict_error"] == 0


def test_frozen_panel_rejects_duplicate_or_missing_matched_arms(panel):
    calls, units, _ = panel
    calls[0]["arm"] = calls[1]["arm"]
    with pytest.raises(analysis.AnalysisError, match="duplicated unit/recipient/arm"):
        analysis.validate_panel(calls, units)


def test_joint_cluster_bootstrap_matches_independent_exact_reference():
    worlds = {"a": "w0", "b": "w0", "c": "w1"}
    q = {"a": Fraction(1, 3), "b": Fraction(-1, 3), "c": Fraction(0)}
    values = {"x": q, "twice_x": {key: 2 * value for key, value in q.items()}}
    result = analysis.bootstrap_joint(values, worlds, replicates=199, seed=18)
    rng = random.Random(18)
    draws = []
    for _ in range(199):
        selected = [rng.choice(["a", "b"]), rng.choice(["a", "b"]), rng.choice(["c"])]
        draws.append(float(sum((q[key] for key in selected), Fraction()) / 3))
    assert result["x"]["p_two_sided"] == analysis.sign_tail_p(draws)
    assert result["x"]["ci95"] == [analysis.type7(draws, .025), analysis.type7(draws, .975)]
    assert result["twice_x"]["ci95"] == [2 * x for x in result["x"]["ci95"]]
    assert result["twice_x"]["p_two_sided"] == result["x"]["p_two_sided"]


def test_finite_tail_ties_holm_and_type7():
    assert analysis.sign_tail_p([1] * 9) == .2
    assert analysis.sign_tail_p([0] * 9) == 1
    assert analysis.sign_tail_p([-1] * 9) == .2
    assert analysis.holm({"first": .02, "second": .03}) == {"first": .04, "second": .04}
    assert analysis.type7([0, 10, 20, 30], .25) == 7.5


def write_inputs(path, panel):
    calls, units, _ = panel
    path.mkdir()
    packets, sources, by_packet = [], [], {}
    for unit in units:
        for arm in analysis.ARMS:
            packet_id = unit["unit_id"] + ":" + arm
            source_judge = analysis.LLAMA if arm == "llama_history" else analysis.QWEN
            condition = "b0" if arm == "empty" else "sequential_b8"
            key = packet_id + ":source"
            messages = [{"role": "system", "content": "Fixture"}, {"role": "user", "content": unit["unit_id"]}]
            message_hash = analysis._message_sha(messages)
            packet = {"packet_id": packet_id, "source_cell_key": key, "messages": messages, "messages_sha256": message_hash}
            packets.append(packet)
            by_packet[packet_id] = packet
            verdict = "VERDICT: Position " + unit["correct_position"]
            sources.append({"cell_key": key, "result": {
                "cell_key": key, "question_id": unit["question_id"], "world": unit["world"],
                "transcript_index": unit["transcript_index"], "judge_model": source_judge,
                "condition": condition, "budget": 0 if arm == "empty" else 8, "replicate": unit["side"],
                "position_a_is_correct": unit["correct_position"] == "A", "queries_used": 0, "exchanges": [], "gate_events": [],
                "raw_verdict_text": verdict, "verdict_strict": analysis.parse_both(verdict)["strict"],
                "judge_messages": [*messages, {"role": "assistant", "content": verdict}]}})
    for call in calls:
        call["packet_id"] = call["unit_id"] + ":" + call["arm"]
        call["messages_sha256"] = by_packet[call["packet_id"]]["messages_sha256"]
    outputs = {}
    for name, rows in (("calls.jsonl", calls), ("units_private.jsonl", units), ("packets.jsonl", packets)):
        raw = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
        (path / name).write_bytes(raw)
        outputs[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    source_path = path.parent / "main_results.jsonl"
    source_path.write_text("".join(json.dumps(row) + "\n" for row in sources), encoding="utf-8")
    (path / "manifest.json").write_text(json.dumps({"schema_version": "phase4a_prepared_panel_v1",
        "outputs": outputs, "phase4_protocol_sha256": "test-protocol",
        "source_hashes": {str(source_path): hashlib.sha256(source_path.read_bytes()).hexdigest()}}), encoding="utf-8")


def test_cli_incomplete_artifact_and_input_hash_protection(panel, tmp_path, monkeypatch, capsys):
    inputs, out = tmp_path / "inputs", tmp_path / "analysis"
    write_inputs(inputs, panel)
    results = tmp_path / "results.jsonl"
    results.write_text(json.dumps(panel[2][0]) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["phase4_analysis.py", "--inputs", str(inputs), "--results", str(results), "--out", str(out)])
    assert analysis.main() == 0
    emitted = json.loads(capsys.readouterr().out)
    saved = json.loads((out / "phase4_analysis.json").read_text(encoding="utf-8"))
    assert emitted["status"] == saved["status"] == "incomplete"
    assert saved["coverage"]["completed"] == 1 and saved["coverage"]["administrative_missing"] == 3935
    assert saved["provenance"]["results_sha256"] == hashlib.sha256(results.read_bytes()).hexdigest()
    assert saved["source_history_descriptive"]["provenance"]["matched_packets"] == 1968
    assert saved["source_history_descriptive"]["by_arm"]["qwen_history"]["packets"] == 656
    assert len((out / "scored_cells.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    with (inputs / "units_private.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(analysis.AnalysisError, match="input hash mismatch"):
        analysis.load_inputs(inputs)


def test_source_exposure_separates_consumed_blocks_and_free_retries():
    source = {"budget": 8, "queries_used": 3,
              "exchanges": [{"blocked": True, "raw_oracle_reply": None, "extracted_claim": "Same claim."},
                            {"raw_oracle_reply": "NO", "normalized": "NO", "extracted_claim": " same  CLAIM!"},
                            {"raw_oracle_reply": "NOT ADDRESSED", "normalized": "NOT ADDRESSED", "extracted_claim": "Different claim."}],
              "gate_events": [{"action": "retry", "slot_consumed": False}, {"action": "block", "slot_consumed": True},
                              {"action": "allow", "slot_consumed": True}, {"action": "allow", "slot_consumed": True}]}
    exposure = analysis.source_exposure(source)
    assert exposure["consumed_slots"] == 3 and exposure["blocked_slots"] == 1
    assert exposure["oracle_replies"] == 2 and exposure["free_retry_events"] == 1
    assert exposure["no"] == exposure["not_addressed"] == 1 and exposure["yes"] == 0
    assert exposure["repeated_exact_claims"] == 1
    assert exposure["exposure"] == "mixed_blocked_and_answered"
    source["queries_used"] = 4
    with pytest.raises(analysis.AnalysisError, match="count disagrees"):
        analysis.source_exposure(source)


def test_historical_join_preserves_matching_and_rejects_source_hash_drift(panel, tmp_path):
    inputs = tmp_path / "inputs"
    write_inputs(inputs, panel)
    calls, units, manifest = analysis.load_inputs(inputs)
    source_packets, provenance = analysis.load_source_packets(inputs, manifest, calls, units)
    assert provenance["matched_packets"] == 1968
    _, fresh = analysis.analyze_rows(calls, units, [panel[2][1], panel[2][2]])
    # The first unit's Qwen own-history reply changes from historical correct to wrong.
    next(row for row in fresh if row["arm"] == "qwen_history")["strict_error"] = 1
    own = analysis.source_descriptives(source_packets, fresh)["own_history_old_vs_fresh"]
    qwen = next(item for item in own if item["judge"] == analysis.QWEN)
    assert qwen["matched"] == 1 and qwen["administrative_missing_fresh"] == 655
    assert qwen["old_correct_to_fresh_error"] == 1 and qwen["net_extra_fresh_errors"] == 1
    assert qwen["fresh_minus_old_on_matched"] == 1 and qwen["ci95"] is None
    complete_own = [{"unit_id": old["unit_id"], "judge": old["source_judge"], "arm": old["arm"],
                     "strict_error": int(old["source_judge"] == analysis.QWEN), "valid": True}
                    for old in source_packets if old["arm"] != "empty"]
    full = analysis.source_descriptives(source_packets, complete_own)["own_history_old_vs_fresh"]
    qwen_full = next(item for item in full if item["judge"] == analysis.QWEN)
    llama_full = next(item for item in full if item["judge"] == analysis.LLAMA)
    assert qwen_full["complete_planned_comparison"] and qwen_full["matched"] == 656
    assert qwen_full["ci95"] == [1, 1] and llama_full["ci95"] == [0, 0]
    assert "p_two_sided" not in qwen_full
    with (tmp_path / "main_results.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(analysis.AnalysisError, match="Historical source result hash mismatch"):
        analysis.load_source_packets(inputs, manifest, calls, units)
