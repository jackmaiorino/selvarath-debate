import copy
import hashlib
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


def test_question_bank_snapshot_retains_exact_bound_source_bytes(tmp_path):
    protocol = json.loads(
        (ROOT / "rejudge" / "phase3_protocol_v3_r6.json").read_text(
            encoding="utf-8"))
    phase2_relative = protocol["sources"]["phase2_protocol"]
    phase2_protocol = json.loads((ROOT / phase2_relative).read_text(encoding="utf-8"))
    copied = [phase2_relative, *phase2_protocol["question_set"]["question_sources"]]
    for relative in copied:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())

    snapshot = analysis._snapshot_protocol_bound_question_bank(protocol, tmp_path)
    expected_main, expected_held_out = phase3_plan.load_reference_question_ids(
        protocol, ROOT)
    assert snapshot.main_ids == expected_main
    assert snapshot.held_out_ids == expected_held_out
    assert set(snapshot.bank) == set(expected_main) | set(expected_held_out)

    question_path, question_raw, question_label = snapshot.stable_inputs[1]
    question_path.write_bytes(question_raw + b"\n")
    with pytest.raises(analysis.AnalysisError, match="changed during analysis"):
        analysis._require_unchanged(question_path, question_raw, question_label)


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


def _write_analysis_finalization_fixture(tmp_path):
    terminal = ["terminal-cell"]
    context = ["context-cell"]
    results_path = tmp_path / analysis.phase3_main_manifest.OUTPUT_FILENAMES["results"]
    results_path.write_text("", encoding="utf-8")
    record = {
        "schema_version": analysis.MAIN_FINALIZATION_SCHEMA,
        "partition": {
            "terminal_cell_keys": terminal,
            "context_ineligible_cell_keys": context,
        },
    }
    finalization_path = tmp_path / analysis.phase3_main_manifest.OUTPUT_FILENAMES[
        "finalization"]
    finalization_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    protocol = json.loads(
        (ROOT / "rejudge" / "phase3_protocol_v3_r6.json").read_text(encoding="utf-8"))
    pins_path = ROOT / "rejudge" / "phase3_main_analysis_pins_2026-08-29.json"
    return finalization_path, results_path, pins_path, protocol, terminal, context, record


def _expected_launch_kwargs(tmp_path):
    provider_input_names = sorted(
        analysis.phase3_main_finalization.PROVIDER_INPUT_FIELDS)
    reviewer_input_names = sorted(
        analysis.phase3_main_finalization.REVIEWER_INPUT_FIELDS)
    output_paths = {
        name: (tmp_path / filename).resolve()
        for name, filename in analysis.phase3_main_manifest.OUTPUT_FILENAMES.items()
    }
    return {
        "expected_run_id": "phase3-main-test",
        "expected_manifest_canonical_sha256": "a" * 64,
        "expected_authorization_canonical_sha256": "b" * 64,
        "expected_authorization_raw_sha256": "c" * 64,
        "expected_authorization_signature_raw_sha256": "d" * 64,
        "authorization_approved_at_utc": "2026-08-29T12:00:00Z",
        "authorization_valid_until_utc": "2026-08-30T12:00:00Z",
        "expected_context_blocklist_path": (tmp_path / "context.json").resolve(),
        "expected_manifest_output_paths": output_paths,
        "expected_provider_input_paths": {
            name: (tmp_path / f"{name}.json").resolve()
            for name in provider_input_names
        },
        "expected_provider_input_raw_sha256s": {
            name: hashlib.sha256(name.encode("utf-8")).hexdigest()
            for name in provider_input_names
        },
        "expected_reviewer_input_paths": {
            name: (tmp_path / f"{name}.json").resolve()
            for name in reviewer_input_names
        },
        "expected_reviewer_input_raw_sha256s": {
            name: hashlib.sha256(name.encode("utf-8")).hexdigest()
            for name in reviewer_input_names
        },
        "expected_capacity_result_path": (
            tmp_path / "capacity_result.json").resolve(),
        "expected_capacity_result_raw_sha256": hashlib.sha256(
            b"capacity_result").hexdigest(),
        "expected_capacity_dispatch_history_path": (
            tmp_path / "capacity_dispatch_history.jsonl").resolve(),
        "expected_capacity_dispatch_history_raw_sha256": hashlib.sha256(
            b"capacity_dispatch_history").hexdigest(),
        "expected_review_packets_root_path": output_paths["review_packets_root"],
        "expected_reviewer_model": "gpt-5.6-sol",
        "expected_reviewer_reasoning_effort": "high",
        "expected_reviewer_concurrency": 12,
        "prior_reconciled_usd": "0",
        "stage_cap_usd": "1",
    }


def test_analysis_routes_finalization_through_full_bound_artifact_validator(
    tmp_path, monkeypatch,
):
    (finalization_path, results_path, pins_path, protocol,
     terminal, context, record) = _write_analysis_finalization_fixture(tmp_path)
    captured = {}

    def validate(candidate, **kwargs):
        captured["candidate"] = candidate
        captured.update(kwargs)
        return record

    monkeypatch.setattr(
        analysis.phase3_main_finalization,
        "validate_finalization_from_bound_artifacts",
        validate,
    )
    observed_terminal, observed_context, raw_sha256 = (
        analysis._load_finalization_exclusions(
            finalization_path,
            results_path=results_path,
            protocol=protocol,
            pins_path=pins_path,
            project_root=ROOT,
            **_expected_launch_kwargs(tmp_path),
        )
    )
    assert observed_terminal == terminal
    assert observed_context == context
    assert raw_sha256 == hashlib.sha256(finalization_path.read_bytes()).hexdigest()
    assert captured["candidate"] == record
    assert captured["expected_result_store_path"] == results_path.resolve()
    assert captured["expected_analysis_pins_path"] == pins_path.resolve()
    assert captured["expected_manifest_output_paths"] == (
        _expected_launch_kwargs(tmp_path)["expected_manifest_output_paths"])
    assert captured["expected_provider_input_paths"] == (
        _expected_launch_kwargs(tmp_path)["expected_provider_input_paths"])
    assert captured["expected_provider_input_raw_sha256s"] == (
        _expected_launch_kwargs(tmp_path)["expected_provider_input_raw_sha256s"])
    assert captured["expected_reviewer_input_paths"] == (
        _expected_launch_kwargs(tmp_path)["expected_reviewer_input_paths"])
    assert captured["expected_reviewer_input_raw_sha256s"] == (
        _expected_launch_kwargs(tmp_path)["expected_reviewer_input_raw_sha256s"])
    assert captured["expected_capacity_result_path"] == (
        _expected_launch_kwargs(tmp_path)["expected_capacity_result_path"])
    assert captured["expected_capacity_result_raw_sha256"] == (
        _expected_launch_kwargs(tmp_path)["expected_capacity_result_raw_sha256"])
    assert captured["expected_capacity_dispatch_history_path"] == (
        _expected_launch_kwargs(tmp_path)["expected_capacity_dispatch_history_path"])
    assert captured["expected_capacity_dispatch_history_raw_sha256"] == (
        _expected_launch_kwargs(tmp_path)[
            "expected_capacity_dispatch_history_raw_sha256"])
    assert captured["expected_review_packets_root_path"] == (
        _expected_launch_kwargs(tmp_path)["expected_review_packets_root_path"])
    assert captured["expected_checker_model"] == protocol["roster"]["query_checker"]
    assert captured["expected_oracle_model"] == protocol["roster"]["oracle"]
    assert captured["expected_reviewer_model"] == "gpt-5.6-sol"
    assert captured["expected_reviewer_reasoning_effort"] == "high"
    assert captured["authorization_valid_until_utc"] == "2026-08-30T12:00:00Z"
    assert captured["expected_authorization_raw_sha256"] == "c" * 64
    assert captured["expected_authorization_signature_raw_sha256"] == "d" * 64
    assert analysis.phase3_main_finalization.inventory_canonical_sha256(
        captured["inventory"]
    ) == analysis.phase3_main_finalization.inventory_canonical_sha256(
        analysis.phase3_main_runner.build_main_inventory(protocol, ROOT)
    )


def test_shallow_fabricated_finalization_is_not_analysis_admissible(tmp_path):
    (finalization_path, results_path, pins_path, protocol,
     _terminal, _context, _record) = _write_analysis_finalization_fixture(tmp_path)
    with pytest.raises(analysis.AnalysisError, match="full bound-artifact validation"):
        analysis._load_finalization_exclusions(
            finalization_path,
            results_path=results_path,
            protocol=protocol,
            pins_path=pins_path,
            project_root=ROOT,
            **_expected_launch_kwargs(tmp_path),
        )


def test_analysis_requires_finalization_to_bind_the_loaded_result_and_pins_snapshots(
    tmp_path,
):
    (finalization_path, results_path, pins_path, protocol,
     _terminal, _context, record) = _write_analysis_finalization_fixture(tmp_path)
    record["artifact_hashes"] = {
        "result_store": {"raw_sha256": "0" * 64},
        "analysis_pins": {"raw_sha256": hashlib.sha256(pins_path.read_bytes()).hexdigest()},
    }
    finalization_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="result-store snapshot"):
        analysis._load_finalization_exclusions(
            finalization_path,
            results_path=results_path,
            protocol=protocol,
            pins_path=pins_path,
            project_root=ROOT,
            **_expected_launch_kwargs(tmp_path),
            expected_results_raw_sha256=hashlib.sha256(
                results_path.read_bytes()).hexdigest(),
            expected_pins_raw_sha256=hashlib.sha256(pins_path.read_bytes()).hexdigest(),
        )


def test_analysis_output_cannot_alias_an_immutable_input(tmp_path):
    results_path = tmp_path / "results.jsonl"
    results_path.write_text("", encoding="utf-8")
    with pytest.raises(analysis.AnalysisError, match="output path must differ"):
        analysis.main([
            "--results", str(results_path),
            "--manifest", str(tmp_path / "manifest.json"),
            "--authorization", str(tmp_path / "authorization.json"),
            "--finalization", str(tmp_path / "finalization.json"),
            "--out", str(results_path),
        ])


def test_analysis_output_receipt_binds_exact_immutable_bytes(tmp_path):
    output = tmp_path / "analysis.json"
    result = {"schema_version": "fixture", "estimate": 0.25}

    raw = analysis._write_analysis_output(output, result)

    assert raw == output.read_bytes()
    assert json.loads(raw) == result
    with pytest.raises(analysis.AnalysisError, match="already exists"):
        analysis._write_analysis_output(output, result)


def test_analysis_output_writer_rejects_nonfinite_results(tmp_path):
    with pytest.raises(ValueError, match="Out of range float values"):
        analysis._write_analysis_output(
            tmp_path / "analysis.json",
            {"estimate": float("inf")},
        )


def test_analysis_rejects_finalization_mutation_after_validation(tmp_path):
    finalization_path = tmp_path / "finalization.json"
    admitted = b'{"status":"admitted"}\n'
    finalization_path.write_bytes(admitted)
    stable_input = analysis._snapshot_sha256_bound_input(
        finalization_path,
        hashlib.sha256(admitted).hexdigest(),
        "main finalization",
    )

    finalization_path.write_bytes(b'{"status":"replaced-during-bootstrap"}\n')

    with pytest.raises(analysis.AnalysisError, match="changed during analysis"):
        analysis._require_unchanged(*stable_input)


def test_post_analysis_repeats_full_finalization_validation(monkeypatch, tmp_path):
    captured = {}

    def reject_tampered_evidence(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        raise analysis.AnalysisError(
            "main finalization failed full bound-artifact validation: reviewer tree drifted")

    monkeypatch.setattr(
        analysis, "_load_finalization_exclusions", reject_tampered_evidence)
    finalization_path = tmp_path / "finalization.json"
    with pytest.raises(
        analysis.AnalysisError,
        match="reviewer tree drifted",
    ):
        analysis._require_finalization_admission_unchanged(
            finalization_path,
            results_path=tmp_path / "results.jsonl",
            protocol={},
            pins_path=tmp_path / "pins.json",
            project_root=ROOT,
            validation_kwargs={"expected_run_id": "phase3-main-test"},
            expected_results_raw_sha256="a" * 64,
            expected_pins_raw_sha256="b" * 64,
            expected_terminal_cell_keys=[],
            expected_context_ineligible_cell_keys=[],
            expected_finalization_raw_sha256="c" * 64,
        )
    assert captured["path"] == finalization_path
    assert captured["expected_run_id"] == "phase3-main-test"
    assert captured["expected_results_raw_sha256"] == "a" * 64
    assert captured["expected_pins_raw_sha256"] == "b" * 64


def test_finalization_cannot_mix_with_even_empty_explicit_key_files(tmp_path):
    with pytest.raises(analysis.AnalysisError, match="cannot be combined"):
        analysis._resolve_exclusions(
            finalization_path=tmp_path / "finalization.json",
            terminal_path=tmp_path / "empty-terminal.json",
            context_path=None,
            results_path=tmp_path / "results.jsonl",
            protocol={},
            pins_path=tmp_path / "pins.json",
            project_root=ROOT,
            **_expected_launch_kwargs(tmp_path),
        )
    with pytest.raises(SystemExit) as exc:
        analysis.main([
            "--results", str(tmp_path / "missing-results.jsonl"),
            "--manifest", str(tmp_path / "manifest.json"),
            "--authorization", str(tmp_path / "authorization.json"),
            "--finalization", str(tmp_path / "finalization.json"),
            "--context-ineligible-cell-keys", str(tmp_path / "empty-context.json"),
        ])
    assert exc.value.code == 2


def test_resolve_exclusions_forwards_provenance_bindings(tmp_path, monkeypatch):
    expected = _expected_launch_kwargs(tmp_path)
    captured = {}

    def load(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return ["terminal"], ["context"], "e" * 64

    monkeypatch.setattr(analysis, "_load_finalization_exclusions", load)
    finalization_path = tmp_path / "finalization.json"
    observed = analysis._resolve_exclusions(
        finalization_path=finalization_path,
        terminal_path=None,
        context_path=None,
        results_path=tmp_path / "results.jsonl",
        protocol={},
        pins_path=tmp_path / "pins.json",
        project_root=ROOT,
        **expected,
    )

    assert observed == (["terminal"], ["context"], "e" * 64)
    assert captured["path"] == finalization_path
    assert captured["expected_provider_input_paths"] == (
        expected["expected_provider_input_paths"])
    assert captured["expected_provider_input_raw_sha256s"] == (
        expected["expected_provider_input_raw_sha256s"])
    assert captured["expected_reviewer_input_paths"] == (
        expected["expected_reviewer_input_paths"])
    assert captured["expected_reviewer_input_raw_sha256s"] == (
        expected["expected_reviewer_input_raw_sha256s"])
    assert captured["expected_capacity_result_path"] == (
        expected["expected_capacity_result_path"])
    assert captured["expected_capacity_result_raw_sha256"] == (
        expected["expected_capacity_result_raw_sha256"])
    assert captured["expected_capacity_dispatch_history_path"] == (
        expected["expected_capacity_dispatch_history_path"])
    assert captured["expected_capacity_dispatch_history_raw_sha256"] == (
        expected["expected_capacity_dispatch_history_raw_sha256"])
    assert captured["expected_review_packets_root_path"] == (
        expected["expected_review_packets_root_path"])
    assert captured["expected_reviewer_model"] == expected["expected_reviewer_model"]
    assert captured["expected_reviewer_reasoning_effort"] == (
        expected["expected_reviewer_reasoning_effort"])
    assert captured["authorization_valid_until_utc"] == (
        expected["authorization_valid_until_utc"])


def test_confirmatory_analysis_cli_requires_finalization():
    with pytest.raises(SystemExit) as exc:
        analysis.main([
            "--results", "results.jsonl",
        ])
    assert exc.value.code == 2


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
