import copy
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys

import pytest

from scripts import phase5_analysis as analysis


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
                            prompt, context = arm.split("_", 1)
                            seed_text = analysis.CALL_SEED_NAMESPACE + "|" + uid + "|" + judge
                            call = {"cell_id": f"{uid}:{judge}:{arm}", "unit_id": uid, "judge": judge, "arm": arm,
                                    "packet_id": f"{uid}:{arm}", "messages_sha256": "a" * 64,
                                    "prompt_variant": prompt, "context_arm": context,
                                    "seed": int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16) % 2147483647,
                                    "temperature": .3, "max_tokens": 16384 if judge == analysis.QWEN else 512, "stream": False}
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


@pytest.mark.parametrize("count,passes", [(78, False), (79, True)])
def test_frozen_integer_boundary_and_distinct_support_levels(panel, count, passes):
    ids = selected_units(panel[1], count)
    for row in panel[2]:
        if row["unit_id"] in ids and row["judge"] == analysis.QWEN and row["arm"] == "ordinary_qwen_history":
            mark_error(row)
    result, scored = analysis.analyze_rows(*panel)
    primary = result["primary"]["S_pooled"]
    assert primary["integer_numerator"] == count and primary["denominator"] == 2624
    assert primary["estimate"] == pytest.approx(count / 2624) and primary["statistical_pass"]
    assert primary["positive_practical_pass"] is passes
    assert result["primary_supported"] is passes and result["semantic_primary_supported"] is passes
    assert result["candidate_readiness"]["passed"] is passes
    assert not result["next_stage_paid_execution_authorized"] and not result["independent_human_validation"]
    assert result["source_review_mode"] == "ai_source_review" and len(scored) == 7872
    assert result["bootstrap"]["seed"] == 2026091208 and result["bootstrap"]["replicates"] == 10000


def test_baseline_worsening_cannot_create_candidate_readiness(panel):
    for row in panel[2]:
        if row["arm"] == "scope_empty":
            mark_error(row)
    result, _ = analysis.analyze_rows(*panel)
    assert result["primary"]["S_pooled"]["estimate"] == 1
    assert result["primary_supported"] and result["semantic_primary_supported"]
    assert result["descriptive_effects"]["B_pooled"]["estimate"] == 0
    assert result["descriptive_effects"]["E_pooled"]["estimate"] == -1
    assert not result["candidate_readiness"]["passed"]
    assert not result["candidate_readiness"]["strict_checks"]["B_pooled_ci95_lower_positive"]
    assert not result["candidate_readiness"]["coefficient_bound_checks"]["Qwen_E_lower_above_minus_1pp"]


def test_one_recipient_benefits_other_harmed_cannot_pool_away_harm(panel):
    unit_by_id = {u["unit_id"]: u for u in panel[1]}
    for row in panel[2]:
        unit = unit_by_id[row["unit_id"]]
        if row["context_arm"] != "empty" and (
            (row["judge"] == analysis.QWEN and row["prompt_variant"] == "ordinary") or
            (row["judge"] == analysis.LLAMA and row["prompt_variant"] == "scope" and unit["transcript_index"] == 0 and unit["side"] == 0)
        ):
            mark_error(row)
    result, _ = analysis.analyze_rows(*panel)
    assert result["primary_supported"] and result["semantic_primary_supported"]
    assert result["primary"]["S_pooled"]["estimate"] == .375
    assert result["descriptive_effects"]["B_Qwen"]["estimate"] == 1
    assert result["descriptive_effects"]["B_Llama"]["estimate"] == -.25
    assert not result["candidate_readiness"]["passed"]
    assert not result["candidate_readiness"]["strict_checks"]["Llama_B_point_nonnegative"]
    assert not result["candidate_readiness"]["common_valid_checks"]["Llama_H_scope_upper95_below_1pp"]


def test_negative_primary_reported_without_positive_support(panel):
    for row in panel[2]:
        if row["prompt_variant"] == "scope" and row["context_arm"] != "empty":
            mark_error(row)
    result, _ = analysis.analyze_rows(*panel)
    primary = result["primary"]["S_pooled"]
    assert primary["estimate"] == -1 and primary["integer_numerator"] == -2624 and primary["statistical_pass"]
    assert not result["primary_supported"] and not result["candidate_readiness"]["passed"]
    assert result["paired_transitions_descriptive"][1]["correct_to_error"] == 656


def good_inference():
    inference = {"B_pooled": {"ci95": [.001, .1]}}
    for name in analysis.RECIPIENT_NAMES:
        inference.update({f"B_{name}": {"estimate": 0, "lower_one_sided95": -.009},
                          f"E_{name}": {"lower_one_sided95": -.009}, f"H_scope_{name}": {"upper_one_sided95": .009}})
    return inference


@pytest.mark.parametrize("metric,field,boundary,direction", [
    ("B_Qwen", "lower_one_sided95", -.01, math.inf),
    ("E_Llama", "lower_one_sided95", -.01, math.inf),
    ("H_scope_Qwen", "upper_one_sided95", .01, -math.inf),
])
def test_noninferiority_interval_boundaries_are_strict(metric, field, boundary, direction):
    inference = good_inference()
    assert all(analysis.readiness_interval_checks(inference).values())
    inference[metric][field] = boundary
    assert not all(analysis.readiness_interval_checks(inference).values())
    inference[metric][field] = math.nextafter(boundary, direction)
    assert all(analysis.readiness_interval_checks(inference).values())


def test_direct_benefit_point_and_bound_inclusivity():
    inference = good_inference()
    inference["B_Qwen"]["estimate"] = -1e-12
    assert not analysis.readiness_interval_checks(inference)["Qwen_B_point_nonnegative"]
    bounds = {name: {"lower": 0, "upper": 0} for name in analysis.GUARD_METRICS}
    bounds["S_pooled"]["lower"] = bounds["B_pooled"]["lower"] = .001
    assert all(analysis.readiness_bound_checks(bounds).values())
    for metric, field, value in (("B_Qwen", "lower", -1e-12), ("B_pooled", "lower", 0),
                                 ("E_Llama", "lower", -.01), ("H_scope_Llama", "upper", .01)):
        changed = copy.deepcopy(bounds)
        changed[metric][field] = value
        assert not all(analysis.readiness_bound_checks(changed).values())


def test_missing_is_administrative_unknown_and_no_formal_inference(panel):
    panel[2].pop(0)  # Qwen ordinary-empty has coefficient -2, not an error by default.
    result = diagnostic(panel)
    assert result["status"] == "incomplete" and result["coverage"]["administrative_missing"] == 1
    assert result["primary"] is None and not result["formal_gates_evaluated"]
    assert not result["candidate_readiness"]["passed"]
    assert result["arm_counts"][0]["strict_errors"] == 0
    bounds = result["strict_missing_outcome_bounds"]["S_pooled"]
    assert (bounds["lower_numerator"], bounds["upper_numerator"]) == (-2, 0)


def test_invalid_only_signal_fails_semantic_gate(panel):
    ids = selected_units(panel[1], 79)
    for row in panel[2]:
        if row["unit_id"] in ids and row["judge"] == analysis.QWEN and row["arm"] == "ordinary_qwen_history":
            row["raw_verdict_text"] = "Position A"
    result, _ = analysis.analyze_rows(*panel)
    assert result["primary_supported"] and result["coverage"]["completed_invalid"] == 79
    assert result["common_valid_sensitivity"]["primary"]["S_pooled"]["estimate"] == 0
    assert result["invalid_and_missing_outcome_bounds"]["S_pooled"]["lower"] == 0
    assert not result["semantic_primary_supported"] and not result["candidate_readiness"]["passed"]


def test_common_valid_checks_all_twelve_and_reweights_debaters(panel):
    first_unit = panel[1][0]
    # The last of twelve cells must drop the whole unit, including other recipient cells.
    panel[2][11]["raw_verdict_text"] = ""
    qid = first_unit["question_id"]
    for row in panel[2]:
        if row["unit_id"] == panel[1][1]["unit_id"] and row["arm"] == "ordinary_qwen_history" and row["judge"] == analysis.QWEN:
            mark_error(row)
    call_map, units = analysis.validate_panel(panel[0], panel[1])
    scored, *_ = analysis.score_rows(call_map, units, panel[2])
    means, support = analysis.question_means(units, scored, common_valid=True)
    assert support["retained_units"] == 655 and means[qid][1] == Fraction(1, 6)
    assert all(means[qid][i] == 0 for i in range(12) if i != 1)


def test_empty_common_valid_stratum_is_undefined(panel):
    ids = {u["unit_id"] for u in panel[1][:4]}
    for row in panel[2]:
        if row["unit_id"] in ids and row["arm"] == "scope_llama_history" and row["judge"] == analysis.LLAMA:
            row["raw_verdict_text"] = "invalid"
    result = diagnostic(panel)
    assert not result["common_valid_sensitivity"]["defined"]
    assert len(result["common_valid_sensitivity"]["support"]["empty_question_debater_strata"]) == 1
    assert result["candidate_readiness"]["common_valid_checks"] is None


def test_coefficient_bounds_differ_from_uniform_recoding(panel):
    panel[2][0]["raw_verdict_text"] = "invalid"  # -2
    panel[2][3]["raw_verdict_text"] = "invalid"  # +2
    result = diagnostic(panel)
    bounds = result["invalid_and_missing_outcome_bounds"]["S_pooled"]
    assert (bounds["lower_numerator"], bounds["upper_numerator"]) == (-2, 2)
    assert result["uniform_invalid_as_correct_sensitivity"]["primary"]["S_pooled"]["estimate"] == 0
    assert result["invalid_and_missing_outcome_bounds"]["E_Qwen"]["lower_numerator"] == -1
    assert result["invalid_and_missing_outcome_bounds"]["H_scope_Qwen"]["upper_numerator"] == 0


def test_nonconstant_independent_cluster_bootstrap_all_interval_tails():
    worlds = {"a": "one", "b": "one", "c": "two", "d": "two", "e": "two"}
    values = {"S": {"a": Fraction(-1, 3), "b": Fraction(1, 2), "c": Fraction(2, 3), "d": Fraction(1, 4), "e": Fraction(-1, 2)}}
    values["opposite"] = {q: -v for q, v in values["S"].items()}
    actual = analysis.bootstrap_joint(values, worlds, replicates=431, seed=2026091208)
    rng, samples = random.Random(2026091208), []
    for _ in range(431):
        keys = [rng.choice(["a", "b"]) for _ in range(2)] + [rng.choice(["c", "d", "e"]) for _ in range(3)]
        samples.append(float(sum((values["S"][q] for q in keys), Fraction()) / 5))
    def quantile(samples, p):
        ordered = sorted(samples)
        index = (len(ordered) - 1) * p
        low = math.floor(index)
        return ordered[low] + (index - low) * (ordered[min(low + 1, len(ordered) - 1)] - ordered[low])
    for name, samples_for_metric in (("S", samples), ("opposite", [-v for v in samples])):
        result = actual[name]
        assert result["ci95"] == [quantile(samples_for_metric, .025), quantile(samples_for_metric, .975)]
        assert result["ci97_5"] == [quantile(samples_for_metric, .0125), quantile(samples_for_metric, .9875)]
        assert result["lower_one_sided95"] == quantile(samples_for_metric, .05)
        assert result["upper_one_sided95"] == quantile(samples_for_metric, .95)
        expected_p = min(1, 2 * min((1 + sum(x <= 0 for x in samples_for_metric)) / 432,
                                  (1 + sum(x >= 0 for x in samples_for_metric)) / 432))
        assert result["p_two_sided"] == expected_p


def test_diagnostic_settings_disable_formal_gates(panel):
    for row in panel[2]:
        if row["prompt_variant"] == "ordinary" and row["context_arm"] != "empty":
            mark_error(row)
    result = diagnostic(panel)
    assert result["primary"]["S_pooled"]["estimate"] == 1
    assert not result["formal_gates_evaluated"] and not result["primary_supported"]
    assert not result["candidate_readiness"]["evaluated"] and not result["candidate_readiness"]["passed"]
    assert "p_two_sided" not in result["descriptive_effects"]["B_pooled"]


@pytest.mark.parametrize("field,value", [("returned_model_id", "wrong/model"), ("configuration_valid", False),
                                         ("messages_sha256", "c" * 64), ("packet_id", "wrong"), ("request_sha256", None)])
def test_result_model_and_request_identity_fail_closed(panel, field, value):
    panel[2][0][field] = value
    with pytest.raises(analysis.AnalysisError):
        diagnostic(panel)


def test_completed_missing_usage_is_retained_with_reservation(panel):
    panel[2][0].update(usage=None, cost_usd="0", uncertain_usd=".02")
    result = diagnostic(panel)
    assert result["status"] == "complete" and result["coverage"]["completed"] == 7872
    assert result["completed_responses_missing_usage"] == 1 and result["completed_response_uncertain_usd"] == "0.02"


def test_duplicate_first_completion_idempotent_but_conflict_rejected(panel):
    panel[2].append(copy.deepcopy(panel[2][0]))
    assert diagnostic(panel)["coverage"]["identical_duplicate_rows_ignored"] == 1
    mark_error(panel[2][-1])
    with pytest.raises(analysis.AnalysisError):
        diagnostic(panel)


@pytest.mark.parametrize("field,value", [("seed", 1), ("temperature", .4), ("max_tokens", 512),
                                         ("stream", True), ("top_p", .9), ("reasoning_effort", "high")])
def test_unmatched_seed_or_altered_request_profile_rejected(panel, field, value):
    panel[0][0][field] = value
    with pytest.raises(analysis.AnalysisError):
        analysis.validate_panel(panel[0], panel[1])


def write_prepared(path, panel):
    path.mkdir()
    calls, units, _ = panel
    protocol = json.loads((analysis.ROOT / "rejudge/phase5_protocol_v1.json").read_bytes())
    suffix, packets = protocol["intervention"]["scope_suffix"], {}
    for call in calls:
        messages = [{"role": "system", "content": "Ordinary fixture." + (suffix if call["prompt_variant"] == "scope" else "")},
                    {"role": "user", "content": "Fixed transcript."}]
        call["messages_sha256"] = analysis.base._message_sha(messages)
        packets[call["packet_id"]] = {"packet_id": call["packet_id"], "messages": messages, "messages_sha256": call["messages_sha256"],
                                      "prompt_variant": call["prompt_variant"], "context_arm": call["context_arm"]}
    artifacts = {"calls.jsonl": calls, "units_private.jsonl": units, "packets.jsonl": list(packets.values()),
                 "prompt_edits_private.jsonl": [{"fixture": "hash-bound source edit map"}]}
    for name, rows in artifacts.items():
        (path / name).write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    manifest = {"schema_version": "phase5_evidence_scope_prepared_panel_v1", "protocol_sha256": analysis.PROTOCOL_SHA256,
                "models": list(analysis.JUDGES), "arms": list(analysis.ARMS), "analysis_seed": analysis.BOOTSTRAP_SEED,
                "order_seed": analysis.ORDER_SEED, "call_seed_namespace": analysis.CALL_SEED_NAMESPACE,
                "review_mode": "ai_source_review", "independent_human_validation": False,
                "provenance": {"repair_labels_sha256": protocol["sources"]["repair_labels_sha256"], "review_amendment_sha256": "c" * 64},
                "outputs": {name: {"bytes": (path / name).stat().st_size, "sha256": analysis.base._sha(path / name)} for name in artifacts}}
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_cli_incomplete_outputs_hashes_and_provenance_without_mutation(tmp_path, panel):
    inputs, out, results = tmp_path / "inputs", tmp_path / "analysis", tmp_path / "results.jsonl"
    manifest = write_prepared(inputs, panel)
    results.write_text("", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in inputs.iterdir()}
    process = subprocess.run([sys.executable, "-B", str(Path(analysis.__file__)), "--inputs", str(inputs),
                              "--results", str(results), "--out", str(out)], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    receipt = json.loads(process.stdout)
    assert receipt["status"] == "incomplete" and receipt["completed"] == 0
    result = json.loads((out / "phase5_analysis.json").read_bytes())
    assert result["provenance"]["input_hashes"] == {name: value["sha256"] for name, value in manifest["outputs"].items()}
    assert result["provenance"]["reviewed_sources"] == manifest["provenance"]
    assert result["provenance"]["results_sha256"] == hashlib.sha256(b"").hexdigest()
    assert (out / "scored_cells.jsonl").read_bytes() == b""
    assert not result["candidate_readiness"]["passed"] and not result["next_stage_paid_execution_authorized"]
    assert before == {p.name: p.read_bytes() for p in inputs.iterdir()}
    (inputs / "prompt_edits_private.jsonl").write_text("changed", encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="hash mismatch"):
        analysis.load_inputs(inputs)


def test_scope_packet_cannot_change_another_message_even_with_refreshed_hashes(tmp_path, panel):
    inputs = tmp_path / "inputs"
    manifest = write_prepared(inputs, panel)
    packets = analysis.base._read_jsonl(inputs / "packets.jsonl")
    packet = next(p for p in packets if p["prompt_variant"] == "scope")
    packet["messages"][1]["content"] = "Changed transcript"
    packet["messages_sha256"] = analysis.base._message_sha(packet["messages"])
    for call in panel[0]:
        if call["packet_id"] == packet["packet_id"]:
            call["messages_sha256"] = packet["messages_sha256"]
    for name, rows in (("packets.jsonl", packets), ("calls.jsonl", panel[0])):
        (inputs / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        manifest["outputs"][name] = {"bytes": (inputs / name).stat().st_size, "sha256": analysis.base._sha(inputs / name)}
    (inputs / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="more than the frozen system suffix"):
        analysis.load_inputs(inputs)


def test_manifest_cannot_misrepresent_review_as_human(tmp_path, panel):
    inputs = tmp_path / "inputs"
    manifest = write_prepared(inputs, panel)
    manifest["independent_human_validation"] = True
    (inputs / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="review provenance"):
        analysis.load_inputs(inputs)
