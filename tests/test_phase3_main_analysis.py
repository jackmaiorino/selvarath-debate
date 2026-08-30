import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from rejudge import phase3_plan
from scripts import phase3_main_analysis as analysis


ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = analysis.ALL_CONDITIONS
JUDGES = ("J1", "J2")
DEBATERS = ("D1", "D2")
QUESTIONS = {
    "Q1": "world_a",
    "Q2": "world_a",
    "Q3": "world_b",
    "Q4": "world_b",
}


def _is_error(question: str, judge: str, condition: str) -> bool:
    b0 = {
        ("Q1", "J1"): False,
        ("Q1", "J2"): True,
        ("Q2", "J1"): False,
        ("Q2", "J2"): False,
        ("Q3", "J1"): True,
        ("Q3", "J2"): True,
        ("Q4", "J1"): True,
        ("Q4", "J2"): False,
    }
    if condition in {"b0", "sequential_b1"}:
        return b0[(question, judge)]
    if condition == "sequential_b2":
        return True if question == "Q2" else b0[(question, judge)]
    if condition == "sequential_b4":
        return True
    if condition == "sequential_b8":
        return False
    raise AssertionError(condition)


def _fixture_records() -> list[analysis.AnalysisRecord]:
    records = []
    sequence = 0
    for question, world in QUESTIONS.items():
        for judge in JUDGES:
            for debater in DEBATERS:
                for condition in CONDITIONS:
                    for transcript in range(3):
                        for side in (0, 1):
                            records.append(analysis.AnalysisRecord(
                                cell_key=f"cell-{sequence}",
                                question_id=question,
                                world=world,
                                condition=condition,
                                judge=judge,
                                debater=debater,
                                transcript_index=transcript,
                                side=side,
                                within_side_replicate=0,
                                correct=not _is_error(question, judge, condition),
                                correct_position="A" if side == 0 else "B",
                            ))
                            sequence += 1
    return records


def _replace_one(records, predicate, **changes):
    changed = []
    count = 0
    for record in records:
        if predicate(record):
            changed.append(replace(record, **changes))
            count += 1
        else:
            changed.append(record)
    assert count == 1
    return changed


def test_hand_computed_primary_s1_and_equal_judge_weighting():
    result = analysis.analyze_records(_fixture_records(), b=200, seed=7)
    assert result["primary"]["D1"]["estimate"] == pytest.approx(0.0)
    assert result["primary"]["D2"]["estimate"] == pytest.approx(0.25)
    assert result["primary"]["D4"]["estimate"] == pytest.approx(0.5)
    assert result["primary"]["D8"]["estimate"] == pytest.approx(-0.5)
    assert result["S1"]["estimate"] == pytest.approx(-0.75)
    assert result["per_judge_descriptive"]["J1"]["D8"]["p_two_sided"] is None
    assert result["per_judge_descriptive"]["J2"]["D8"]["p_holm"] is None
    assert result["claims"]["per_judge"].endswith("no per-judge p-values")


def test_strict_invalid_counts_wrong_but_valid_only_drops_matched_slot_only():
    records = _fixture_records()
    predicate = lambda r: (
        r.question_id == "Q1" and r.judge == "J1" and r.debater == "D1"
        and r.condition == "sequential_b1" and r.transcript_index == 0 and r.side == 0)
    records = _replace_one(records, predicate, correct=None)
    strict = analysis.contrast_question_values(
        records, "sequential_b1", "b0", valid_only=False)
    valid = analysis.contrast_question_values(
        records, "sequential_b1", "b0", valid_only=True)
    assert strict["support"]["kept_matched_slots"] == 4 * 2 * 2 * 3 * 2
    assert valid["support"]["dropped_invalid_slots"] == 1
    assert valid["support"]["kept_matched_slots"] == strict["support"]["kept_matched_slots"] - 1
    assert strict["pooled"]["Q1"] > valid["pooled"]["Q1"]


def test_zero_based_replicate_translation_preserves_both_sides():
    assert analysis.plan_replicate_to_side(0, 1) == (0, 0)
    assert analysis.plan_replicate_to_side(1, 1) == (1, 0)
    assert {analysis.plan_replicate_to_side(index, 1)[0] for index in (0, 1)} == {0, 1}
    with pytest.raises(analysis.AnalysisError, match="K2 side"):
        analysis.plan_replicate_to_side(2, 1)


def test_terminal_invalid_stays_primary_and_excludes_whole_diagnostic_pair_only():
    records = _fixture_records()
    predicate = lambda r: (
        r.question_id == "Q1" and r.judge == "J1" and r.debater == "D1"
        and r.condition == "sequential_b1" and r.transcript_index == 0 and r.side == 0)
    terminal = _replace_one(
        records, predicate, correct=None, correct_position=None, origin="terminal_invalid")
    strict = analysis.contrast_question_values(
        terminal, "sequential_b1", "b0", valid_only=False)
    assert strict["support"]["kept_matched_slots"] == 4 * 2 * 2 * 3 * 2
    diagnostics = analysis.paired_mirror_diagnostics(terminal)
    key = "J1|sequential_b1"
    assert diagnostics[key]["affected_pairs_excluded_from_diagnostic_only"] == 1
    assert diagnostics[key]["retained_pairs"] == diagnostics[key]["planned_pairs"] - 1


def test_common_draws_are_seeded_world_stratified_and_row_order_invariant():
    worlds = {"Q3": "world_b", "Q1": "world_a", "Q4": "world_b", "Q2": "world_a"}
    first = analysis.stratified_question_draws(worlds, 100, 91)
    second = analysis.stratified_question_draws(dict(reversed(list(worlds.items()))), 100, 91)
    assert first == second
    assert analysis.draw_matrix_sha256(first) == analysis.draw_matrix_sha256(second)
    for draw in first:
        assert sum(draw.get(q, 0) for q in ("Q1", "Q2")) == 2
        assert sum(draw.get(q, 0) for q in ("Q3", "Q4")) == 2


def test_frozen_seed_has_no_zero_subset_world_draw_and_hash_is_pinned():
    protocol = json.loads(
        (ROOT / "rejudge" / "phase3_protocol_v3_r6.json").read_text(encoding="utf-8"))
    main_ids, _ = phase3_plan.load_reference_question_ids(protocol, ROOT)
    bank = analysis._load_protocol_bound_question_bank(protocol, ROOT)
    assert len(bank) == 106
    assert set(main_ids) <= set(bank)
    worlds = {question: bank[question]["world"] for question in main_ids}
    subset = set(protocol["decisions"]["configuration_selection"]["subset_question_ids"])
    draws = analysis.stratified_question_draws(worlds, 10_000, 20_260_829)
    assert analysis.draw_matrix_sha256(draws) == (
        "8ee28ad44b7db3ff32481303333f9f4ccfe75c35310436f46058af75183d7a54")
    for draw in draws:
        for world in set(worlds.values()):
            assert sum(draw.get(q, 0) for q in subset if worlds[q] == world) > 0


def test_fallback_s1_restricts_both_b8_and_b2():
    records = _fixture_records()
    # Outside Q1/Q3, make b8 much worse. If only one S1 arm were restricted,
    # this would change the answer. The paired domain must filter both arms.
    records = [
        replace(record, correct=False)
        if record.question_id in {"Q2", "Q4"} and record.condition == "sequential_b8"
        else record
        for record in records
    ]
    domain = ("Q1", "Q3")
    paired = analysis.contrast_question_values(
        records, "sequential_b8", "sequential_b2", domain_questions=domain)
    point = analysis.weighted_domain_mean(
        paired["pooled"], None, QUESTIONS, domain)
    assert point == pytest.approx(-0.75)
    assert set(paired["pooled"]) == set(domain)


def test_holm_uncentered_p_and_tie_order_are_deterministic():
    assert analysis.bootstrap_p_two_sided([-1.0, 0.0, 1.0]) == 1.0
    adjusted = analysis.holm({"D8": 0.01, "D4": 0.01, "D2": 0.04, "D1": 0.2})
    assert adjusted == {
        "D4": pytest.approx(0.04),
        "D8": pytest.approx(0.04),
        "D2": pytest.approx(0.08),
        "D1": pytest.approx(0.2),
    }


def test_sup_t_hand_matrix_and_zero_se_rule():
    result = analysis.sup_t_band(
        {"D1": 0.0, "D2": 1.0, "D4": 2.0},
        {
            "D1": [-1.0, 0.0, 1.0],
            "D2": [0.0, 1.0, 2.0],
            "D4": [2.0, 2.0, 2.0],
        },
    )
    assert result["critical_value"] == pytest.approx(1.0)
    assert result["bands"]["D1"] == pytest.approx([-1.0, 1.0])
    assert result["bands"]["D4"] == pytest.approx([2.0, 2.0])
    with pytest.raises(analysis.AnalysisError, match="zero bootstrap SE"):
        analysis.sup_t_band({"D1": 2.1}, {"D1": [2.0, 2.0, 2.0]})


def test_capability_tolerant_tie_is_nonestimable_and_strict_spread_has_no_p_value():
    tied = analysis.capability_slope({"J1": 0.1, "J2": 0.2}, {"J1": 47/48, "J2": 47/48})
    assert tied["status"] == "not_estimable_zero_anchor_spread"
    assert tied["estimate"] is None
    strict = analysis.capability_slope({"J1": 0.1, "J2": 0.2}, {"J1": 33/48, "J2": 47/48})
    assert strict["status"] == "estimate_and_plot_only_no_p_value"
    assert strict["p_value"] is None


def test_analyze_records_reports_frozen_capability_slope_on_common_d8_draws():
    records = [
        replace(record, correct=False)
        if record.judge == "J2" and record.condition == "sequential_b8"
        else record
        for record in _fixture_records()
    ]
    anchors = {
        "tolerant": {"J1": 47 / 48, "J2": 47 / 48},
        "strict": {"J1": 33 / 48, "J2": 47 / 48},
    }
    result = analysis.analyze_records(
        records, b=200, seed=7, capability_anchor_scores=anchors)
    slope = result["capability_slope"]
    assert slope["common_draw_matrix_sha256"] == result["bootstrap"][
        "draw_matrix_sha256"]
    tolerant = slope["tolerant_primary"]
    assert tolerant["status"] == "not_estimable_zero_anchor_spread"
    assert tolerant["estimate"] is None
    assert tolerant["ci95_percentile_descriptive"] is None
    assert tolerant["p_value"] is None
    assert tolerant["slope_bootstrap_replicates"] == 0
    strict = slope["strict_sensitivity"]
    expected = (
        result["per_judge_descriptive"]["J2"]["D8"]["estimate"]
        - result["per_judge_descriptive"]["J1"]["D8"]["estimate"]
    ) / ((47 - 33) / 48)
    assert strict["estimate"] == pytest.approx(expected)
    assert strict["ci95_percentile_descriptive"] is not None
    assert strict["p_value"] is None
    assert strict["slope_bootstrap_replicates"] == 200
    assert all(
        refit["status"] == "not_estimable_zero_anchor_spread"
        for refit in strict["leave_one_judge_out"].values())


def test_all_invalid_scenarios_and_zero_count_replicate_strata_are_reported():
    records = _fixture_records()
    predicate = lambda r: (
        r.question_id == "Q1" and r.judge == "J1" and r.debater == "D1"
        and r.condition == "sequential_b8" and r.transcript_index == 0 and r.side == 0)
    records = _replace_one(records, predicate, correct=None)
    result = analysis.analyze_records(records, b=200, seed=7)
    scenarios = result["all_invalid_scenarios"]
    assert scenarios["common_draw_matrix_sha256"] == result["bootstrap"][
        "draw_matrix_sha256"]
    for key in (*analysis.PRIMARY_IDS, "S1"):
        assert scenarios["all_invalid_wrong"][key]["estimate"] == pytest.approx(
            result["primary"][key]["estimate"] if key in analysis.PRIMARY_IDS
            else result["S1"]["estimate"])
        assert scenarios["all_invalid_wrong"][key]["p_two_sided"] is None
    assert scenarios["all_invalid_correct"]["D8"]["estimate"] < (
        scenarios["all_invalid_wrong"]["D8"]["estimate"])
    strata = result["invalid_counts_by_budget_judge_replicate_block"]
    assert len(strata) == len(CONDITIONS) * len(JUDGES) * 2
    affected = [row for row in strata if row["invalid_count"] == 1]
    assert len(affected) == 1
    assert affected[0]["query_budget"] == 8
    assert affected[0]["replicate_block_zero_based"] == 0
    assert any(row["invalid_count"] == 0 for row in strata)


def test_rounding_is_half_even_and_decision_values_remain_raw():
    assert analysis.format_effect_pp(0.012345) == "1.234"
    assert analysis.format_effect_pp(0.012355) == "1.236"
    assert analysis.format_p_value(0.1234565) == "0.123456"


def test_record_structure_rejects_missing_mirror_and_zero_support():
    records = _fixture_records()
    with pytest.raises(analysis.AnalysisError, match="mirror unit"):
        analysis.validate_record_structure(records[:-1])
    predicate = lambda r: (
        r.question_id == "Q1" and r.judge == "J1" and r.debater == "D1"
        and r.condition == "sequential_b1")
    invalid = [replace(record, correct=None) if predicate(record) else record
               for record in records]
    with pytest.raises(analysis.AnalysisError, match="zero support"):
        analysis.contrast_question_values(
            invalid, "sequential_b1", "b0", valid_only=True)


def test_loader_requires_exact_transcript_and_judgment_plan_partitions():
    protocol = json.loads(
        (ROOT / "rejudge" / "phase3_protocol_v3_r6.json").read_text(encoding="utf-8"))
    main_ids, _ = phase3_plan.load_reference_question_ids(protocol, ROOT)
    plan = phase3_plan.enumerate_cells(protocol, protocol["roster"]["judges_final"], main_ids)
    judgments = [cell for cell in plan if cell["kind"] == phase3_plan.MAIN_JUDGMENT_KIND]
    transcripts = [cell for cell in plan if cell["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND]
    assert len(judgments) == 9_840
    assert len(transcripts) == 492
    with pytest.raises(analysis.AnalysisError, match="transcript partition is incomplete"):
        analysis.build_analysis_records(
            rows=[], plan_cells=plan, protocol=protocol, question_bank={})
    with pytest.raises(analysis.AnalysisError, match="exactly 9,840"):
        analysis.build_analysis_records(
            rows=[], plan_cells=plan[:-1], protocol=protocol, question_bank={})


def test_jsonl_reader_rejects_blank_rows(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"cell_key":"a"}\n\n', encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="blank JSONL row"):
        analysis._read_jsonl(path)


def test_key_lists_reject_empty_duplicate_and_unadmitted_nonempty_keys(tmp_path):
    empty_key_path = tmp_path / "empty-key.json"
    empty_key_path.write_text(json.dumps([""]), encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="empty cell key"):
        analysis._load_key_list(empty_key_path)
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(json.dumps(["cell-a", "cell-a"]), encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="duplicate cell keys"):
        analysis._load_key_list(duplicate_path)
    admitted_empty_path = tmp_path / "empty.json"
    admitted_empty_path.write_text("[]", encoding="utf-8")
    assert analysis._load_key_list(admitted_empty_path) == []
    analysis._require_admitted_exclusions([], [])
    with pytest.raises(analysis.AnalysisError, match="not admitted for confirmatory"):
        analysis._require_admitted_exclusions(["cell-a"], [])
    with pytest.raises(analysis.AnalysisError, match="not admitted for confirmatory"):
        analysis._require_admitted_exclusions([], ["cell-b"])


def test_analysis_pins_validate_and_seed_drift_fails_closed():
    protocol = json.loads(
        (ROOT / "rejudge" / "phase3_protocol_v3_r6.json").read_text(encoding="utf-8"))
    pins = json.loads(
        (ROOT / "rejudge" / "phase3_main_analysis_pins_2026-08-29.json").read_text(
            encoding="utf-8"))
    analysis.validate_analysis_pins(pins, protocol, root=ROOT)
    pins["bootstrap"]["seed"] += 1
    with pytest.raises(analysis.AnalysisError, match="bootstrap.seed drifted"):
        analysis.validate_analysis_pins(pins, protocol, root=ROOT)


def test_analysis_pins_reject_d8_invalid_sup_t_and_anchor_mutations():
    protocol = json.loads(
        (ROOT / "rejudge" / "phase3_protocol_v3_r6.json").read_text(encoding="utf-8"))
    frozen = json.loads(
        (ROOT / "rejudge" / "phase3_main_analysis_pins_2026-08-29.json").read_text(
            encoding="utf-8"))
    mutations = []
    d8 = copy.deepcopy(frozen)
    d8["estimands"]["primary_family"]["D8"] = "error(b0) - error(sequential_b8)"
    mutations.append((d8, "estimands block drifted"))
    invalid = copy.deepcopy(frozen)
    invalid["invalid_policy"]["primary"] = "strict INVALID counts correct"
    mutations.append((invalid, "invalid_policy block drifted"))
    sup_t = copy.deepcopy(frozen)
    sup_t["sup_t"]["scope"] = "confirmatory"
    mutations.append((sup_t, "sup_t block drifted"))
    anchor = copy.deepcopy(frozen)
    anchor["capability_anchor_scores"]["strict"][
        "meta-llama/Llama-3.3-70B-Instruct-Turbo"]["correct_count"] = 34
    mutations.append((anchor, "capability_anchor_scores block drifted"))
    for mutated, message in mutations:
        with pytest.raises(analysis.AnalysisError, match=message):
            analysis.validate_analysis_pins(mutated, protocol, root=ROOT)


def test_rendered_position_is_derived_from_prompt_not_stored_metadata():
    question = {"correct_answer": "right", "wrong_answer": "wrong"}
    row = {
        "cell_key": "x",
        "result": {
            "position_a_is_correct": False,
            "judge_messages": [{"role": "system", "content": "s"}, {
                "role": "user",
                "content": (
                    "QUESTION: q\n\nPOSITION A: right\n\nPOSITION B: wrong\n\n"
                    "DEBATE TRANSCRIPT:\ntext"),
            }],
        },
    }
    assert analysis._rendered_correct_position(row, question) == "A"
