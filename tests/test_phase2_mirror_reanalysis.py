"""The phase-2 mirror-robustness reanalysis engine, proven on synthetic fixtures.

Every number here is hand-computed, with side ("A"/"B") assignment monkeypatched to
known values so the test controls exactly which synthetic (question, transcript)
units are "mirrored" (both sides observed and combinable) vs "duplicated" (only one
side observed, as the real archive's K2 defect produced). The central claim under
test is that the 50/50 world-standardized estimator recovers the correct answer when
fed properly side-balanced data, and specifically CORRECTS the bias a naive
realized-schedule (question-equal-weight, side-blind) estimate carries when sides are
unevenly represented across questions -- which is exactly the archive's situation
(128 A-correct / 118 B-correct units, 19 single-polarity questions).
"""
import json

import pytest

from scripts.phase2_main_analysis import (
    PRIMARY_CONDITIONS,
    PRIMARY_IDS,
    condition_means,
    error_by_cell,
    estimands,
)
from scripts.phase2_mirror_reanalysis import (
    _extract_positions,
    _stability_call,
    apply_decision_rule,
    build_per_unit_C,
    build_per_unit_D_clean_official_stratified_by_t0,
    build_per_unit_D_clean_t0,
    error_by_unit,
    per_unit_to_per_q_side,
    question_side_means,
    scalar_estimand,
    standardized_family,
    within_question_corroboration,
)
import scripts.phase2_mirror_reanalysis as mod


def _rec(q, t, world, cond, judge, deb, replicate, correct, kind="debate_judgment"):
    return {"kind": kind, "question_id": q, "transcript_index": t, "world": world,
            "condition": cond, "judge": judge, "debater": deb,
            "slot": (replicate, t), "correct": correct}


# ---------------------------------------------------------------------------------
# _extract_positions: rendered-prompt parsing (step 1's polarity-verification input)
# ---------------------------------------------------------------------------------

def test_extract_positions_debate_style():
    content = ("QUESTION: does X?\n\nPOSITION A: alpha text here.\n\n"
              "POSITION B: beta text here.\n\nDEBATE TRANSCRIPT:\nTurn 1...")
    a, b = _extract_positions(content)
    assert a == "alpha text here."
    assert b == "beta text here."


def test_extract_positions_no_debate_style_has_no_transcript_terminator():
    content = "NO-DEBATE CONDITION\n\nQUESTION: does X?\n\nPOSITION A: alpha.\n\nPOSITION B: beta."
    a, b = _extract_positions(content)
    assert a == "alpha."
    assert b == "beta."


def test_extract_positions_missing_block_returns_none():
    assert _extract_positions("no position blocks in this text") is None


# ---------------------------------------------------------------------------------
# error_by_unit: K2 collapse at (q, t, j, d) grain, preserving H = P + R.
# ---------------------------------------------------------------------------------

def test_error_by_unit_collapses_k2_replicates():
    rows = [
        _rec("Q1", 0, "wA", "b0", "J", "D", 0, True),
        _rec("Q1", 0, "wA", "b0", "J", "D", 1, False),   # K2: one right, one wrong -> 0.5
        _rec("Q1", 0, "wA", "sequential_b2", "J", "D", 0, False),
        _rec("Q1", 0, "wA", "sequential_b2", "J", "D", 1, False),
        _rec("Q1", 0, "wA", "batch_same_qa_b2", "J", "D", 0, True),
        _rec("Q1", 0, "wA", "batch_same_qa_b2", "J", "D", 1, True),
    ]
    rates = error_by_unit(rows, PRIMARY_CONDITIONS, invalid_wrong=True, common_support=False)
    assert rates[("Q1", 0, "J", "D")] == {"b0": 0.5, "sequential_b2": 1.0,
                                          "batch_same_qa_b2": 0.0}
    e = estimands(rates[("Q1", 0, "J", "D")])
    assert abs((e["P"] + e["R"]) - e["H"]) < 1e-12


def test_error_by_unit_keeps_transcripts_separate_unlike_error_by_cell():
    # Two transcripts of the same (question, judge, debater): error_by_cell pools
    # them into one (q, j, d) cell; error_by_unit must keep two distinct units.
    rows = []
    for t, correct in ((0, True), (1, False)):
        for cond in PRIMARY_CONDITIONS:
            rows.append(_rec("Q1", t, "wA", cond, "J", "D", 0, correct))
    unit_rates = error_by_unit(rows, PRIMARY_CONDITIONS, invalid_wrong=True, common_support=False)
    assert set(unit_rates) == {("Q1", 0, "J", "D"), ("Q1", 1, "J", "D")}
    cell_rates = error_by_cell(rows, PRIMARY_CONDITIONS, invalid_wrong=True, common_support=False)
    assert set(cell_rates) == {("Q1", "J", "D")}


# ---------------------------------------------------------------------------------
# question_side_means + standardized_family: the core standardization machinery.
# ---------------------------------------------------------------------------------

def _patch_side(monkeypatch, mapping):
    """mapping: {(question_id, transcript_index): "A"|"B"}"""
    def fake_side_of(q, t):
        return mapping[(q, t)]
    monkeypatch.setattr(mod, "side_of", fake_side_of)


def _unit(b0, seq, bat):
    return {"b0": b0, "sequential_b2": seq, "batch_same_qa_b2": bat}


def test_fifty_fifty_standardization_recovers_hand_computed_truth(monkeypatch):
    # Q1, Q2 each have one A-transcript and one B-transcript: a properly MIRRORED
    # design (both labels observed per question), one judge, one debater. unit_rates
    # is built directly (rather than derived from K2-boolean rows, which can only
    # express {0, 0.5, 1} per unit) so the hand-computed fractions are exact.
    _patch_side(monkeypatch, {("Q1", 0): "A", ("Q1", 1): "B",
                              ("Q2", 0): "A", ("Q2", 1): "B"})
    unit_rates = {
        ("Q1", 0, "J", "D"): _unit(0.0, 0.25, 0.0),     # A
        ("Q1", 1, "J", "D"): _unit(1.0, 0.75, 1.0),     # B
        ("Q2", 0, "J", "D"): _unit(0.25, 0.5, 0.25),    # A
        ("Q2", 1, "J", "D"): _unit(0.75, 0.5, 0.75),    # B
    }
    per_q_side = question_side_means(unit_rates, PRIMARY_CONDITIONS)
    world_of = {"Q1": "wA", "Q2": "wA"}
    fam = standardized_family(per_q_side, PRIMARY_CONDITIONS, world_of)

    assert fam["A_only"]["H"] == pytest.approx(0.25)     # (0.25-0.0 + 0.5-0.25)/2
    assert fam["B_only"]["H"] == pytest.approx(-0.25)    # (0.75-1.0 + 0.5-0.75)/2
    assert fam["fifty_fifty"]["H"] == pytest.approx(0.0)         # 0.5*(0.25 + -0.25)
    assert fam["interaction"]["H"] == pytest.approx(0.5)         # 0.25 - (-0.25)
    # H = P + R must survive the standardization exactly, per side and combined.
    for group in ("A_only", "B_only", "fifty_fifty", "interaction"):
        assert abs((fam[group]["P"] + fam[group]["R"]) - fam[group]["H"]) < 1e-9


def test_fifty_fifty_standardization_corrects_realized_side_imbalance(monkeypatch):
    # Three SINGLE-polarity questions (the archive's actual pathology): Q1, Q2 are
    # A-only, Q3 is B-only. Side A units have a true H of +0.5, side B units have a
    # true H of -0.5 (a pure, symmetric position effect -- no real average effect).
    # The realized-schedule (question-equal-weight, side-blind) estimate is biased
    # toward +0.5 because A is 2-to-1 overrepresented; the 50/50 world-standardized
    # estimate must recover the true, unbiased 0.0. unit_rates is built directly
    # (each question has exactly one transcript here, so its (q, j, d) cell rate,
    # as error_by_cell would compute it, is identical to its single unit's rate).
    _patch_side(monkeypatch, {("Q1", 0): "A", ("Q2", 0): "A", ("Q3", 0): "B"})
    unit_rates = {
        ("Q1", 0, "J", "D"): _unit(0.0, 0.5, 0.0),
        ("Q2", 0, "J", "D"): _unit(0.0, 0.5, 0.0),
        ("Q3", 0, "J", "D"): _unit(0.5, 0.0, 0.5),
    }
    cell_rates = {(q, j, d): rates for (q, _t, j, d), rates in unit_rates.items()}
    realized_H = estimands(condition_means(cell_rates, PRIMARY_CONDITIONS))["H"]
    assert realized_H == pytest.approx(0.5 / 3)   # biased: (0.5 + 0.5 - 0.5) / 3

    per_q_side = question_side_means(unit_rates, PRIMARY_CONDITIONS)
    world_of = {"Q1": "wA", "Q2": "wA", "Q3": "wA"}
    fam = standardized_family(per_q_side, PRIMARY_CONDITIONS, world_of)
    assert fam["fifty_fifty"]["H"] == pytest.approx(0.0)   # standardization removes the bias
    assert fam["A_only"]["H"] == pytest.approx(0.5)
    assert fam["B_only"]["H"] == pytest.approx(-0.5)


def test_question_side_means_equal_judge_debater_weight(monkeypatch):
    # One question, two (judge, debater) pairs at side A with different rates:
    # per_q_side must average them equally, not weight by row count.
    _patch_side(monkeypatch, {("Q1", 0): "A"})
    rows = []
    for j, d, err in (("J1", "D1", 0.0), ("J2", "D2", 1.0)):
        for cond in PRIMARY_CONDITIONS:
            correct = err == 0.0
            rows.append(_rec("Q1", 0, "wA", cond, j, d, 0, correct))
    unit_rates = error_by_unit(rows, PRIMARY_CONDITIONS, invalid_wrong=True, common_support=False)
    per_q_side = question_side_means(unit_rates, PRIMARY_CONDITIONS)
    assert per_q_side[("Q1", "A")]["b0"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------------
# within_question_corroboration: direct within-question centering on dual-polarity Qs.
# ---------------------------------------------------------------------------------

def test_within_question_corroboration_direct_centering(monkeypatch):
    _patch_side(monkeypatch, {("Q1", 0): "A", ("Q1", 1): "B"})
    unit_rates = {
        ("Q1", 0, "J", "D"): _unit(0.0, 0.2, 0.0),
        ("Q1", 1, "J", "D"): _unit(1.0, 0.6, 1.0),
    }
    corro = within_question_corroboration(unit_rates, draws=[], dual_qs={"Q1"})
    assert corro["n_questions"] == 1
    # A: H = 0.2 - 0.0 = 0.2; B: H = 0.6 - 1.0 = -0.4; fifty = 0.5*(0.2-0.4) = -0.1
    assert corro["fifty_fifty"]["H"]["estimate"] == pytest.approx(-0.1)
    assert corro["interaction"]["H"]["estimate"] == pytest.approx(0.6)  # 0.2 - (-0.4)


# ---------------------------------------------------------------------------------
# build_per_unit_C / build_per_unit_D_clean_t0: step-6 audit builders.
# ---------------------------------------------------------------------------------

def test_build_per_unit_C_sign_and_grain():
    CAP_JUDGE = mod.CAP_JUDGE
    A, B = mod.DEBATER_A, mod.DEBATER_B
    rows = [
        _rec("Q1", 0, "wA", "b0", CAP_JUDGE, A, 0, False, kind="debate_judgment"),
        _rec("Q1", 0, "wA", "capped150_b0", CAP_JUDGE, A, 0, True, kind="cap_protection_judgment"),
        _rec("Q1", 0, "wA", "b0", CAP_JUDGE, B, 0, True, kind="debate_judgment"),
        _rec("Q1", 0, "wA", "capped150_b0", CAP_JUDGE, B, 0, True, kind="cap_protection_judgment"),
        # A different judge must be ignored entirely.
        _rec("Q1", 0, "wA", "b0", "OTHER", A, 0, True, kind="debate_judgment"),
    ]
    per_unit = build_per_unit_C(rows, valid_only=False)
    # debater A: uncapped err 1.0, capped err 0.0 -> diff 1.0. debater B: 0.0 - 0.0 = 0.0.
    # C = 1.0 - 0.0 = 1.0.
    assert per_unit[("Q1", 0)] == pytest.approx(1.0)


def test_build_per_unit_D_clean_t0_ignores_other_transcripts():
    rows = [
        _rec("Q1", 0, "wA", "sequential_b2", "J", "D1", 0, True, kind="debate_judgment"),
        _rec("Q1", 0, "wA", "sequential_b2", "J", "D2", 0, False, kind="debate_judgment"),
        # transcript 1 must be excluded from the t0-matched builder entirely.
        _rec("Q1", 1, "wA", "sequential_b2", "J", "D1", 0, False, kind="debate_judgment"),
        _rec("Q1", 1, "wA", "sequential_b2", "J", "D2", 0, False, kind="debate_judgment"),
        _rec("Q1", 0, "wA", "clean_b2", "J", None, 0, True, kind="no_debate_judgment"),
        _rec("Q1", 0, "wA", "clean_b2", "J", None, 1, True, kind="no_debate_judgment"),
        _rec("Q1", 0, "wA", "clean_b2", "J", None, 2, True, kind="no_debate_judgment"),
    ]
    per_unit = build_per_unit_D_clean_t0(rows, valid_only=False)
    # debate t0 error mean over 2 debaters: (0.0 + 1.0)/2 = 0.5; no-debate error 0.0.
    assert per_unit[("Q1", 0)] == pytest.approx(0.5)


def test_build_per_unit_D_clean_official_stratified_by_t0_uses_all_transcripts():
    # Same fixture as the t0-only builder above, but the OFFICIAL-stratified builder
    # must pool transcripts 0 AND 1 into the per-debater error (matching the banked
    # per_question_D exactly), then only use transcript 0's side as the bucket label
    # -- giving a DIFFERENT number (0.75, not 0.5) from the t0-restricted sensitivity.
    rows = [
        _rec("Q1", 0, "wA", "sequential_b2", "J", "D1", 0, True, kind="debate_judgment"),
        _rec("Q1", 0, "wA", "sequential_b2", "J", "D2", 0, False, kind="debate_judgment"),
        _rec("Q1", 1, "wA", "sequential_b2", "J", "D1", 0, False, kind="debate_judgment"),
        _rec("Q1", 1, "wA", "sequential_b2", "J", "D2", 0, False, kind="debate_judgment"),
        _rec("Q1", 0, "wA", "clean_b2", "J", None, 0, True, kind="no_debate_judgment"),
        _rec("Q1", 0, "wA", "clean_b2", "J", None, 1, True, kind="no_debate_judgment"),
        _rec("Q1", 0, "wA", "clean_b2", "J", None, 2, True, kind="no_debate_judgment"),
    ]
    per_unit_official = build_per_unit_D_clean_official_stratified_by_t0(rows, valid_only=False)
    per_unit_t0_only = build_per_unit_D_clean_t0(rows, valid_only=False)
    # D1: (0.0 t0 + 1.0 t1)/2 = 0.5; D2: (1.0 + 1.0)/2 = 1.0; mean over debaters 0.75.
    assert per_unit_official[("Q1", 0)] == pytest.approx(0.75)
    assert per_unit_t0_only[("Q1", 0)] == pytest.approx(0.5)
    assert per_unit_official[("Q1", 0)] != per_unit_t0_only[("Q1", 0)]


# ---------------------------------------------------------------------------------
# per_unit_to_per_q_side + scalar_estimand: the C/D_clean adapter into the same
# world-standardization machinery H/P/R uses.
# ---------------------------------------------------------------------------------

def test_scalar_family_shares_the_hpr_standardization_machinery(monkeypatch):
    _patch_side(monkeypatch, {("Q1", 0): "A", ("Q2", 0): "B"})
    per_unit = {("Q1", 0): 1.0, ("Q2", 0): -1.0}
    pqs = per_unit_to_per_q_side(per_unit)
    world_of = {"Q1": "wA", "Q2": "wA"}
    fam = standardized_family(pqs, ("value",), world_of, estimand_fn=scalar_estimand,
                              estimand_ids=("value",))
    assert fam["A_only"]["value"] == pytest.approx(1.0)
    assert fam["B_only"]["value"] == pytest.approx(-1.0)
    assert fam["fifty_fifty"]["value"] == pytest.approx(0.0)
    assert fam["interaction"]["value"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------------
# apply_decision_rule / _stability_call: the mechanical, un-editorialized verdict.
# ---------------------------------------------------------------------------------

def _entry(estimate, ci):
    return {"estimate": estimate, "ci95": ci}


def test_decision_rule_survives():
    v = apply_decision_rule(_entry(0.04, (0.02, 0.06)), _entry(0.03, (0.01, 0.05)),
                            _entry(0.02, (0.0, 0.04)))
    assert v["verdict"] == "survives_as_post_hoc_robust"


def test_decision_rule_withdraw_when_ci_includes_zero():
    v = apply_decision_rule(_entry(0.02, (-0.01, 0.05)), _entry(0.03, (0.01, 0.05)),
                            _entry(0.02, (0.0, 0.04)))
    assert v["verdict"] == "withdraw_directional_headline"


def test_decision_rule_retract_when_negative():
    v = apply_decision_rule(_entry(-0.02, (-0.05, -0.005)), _entry(-0.01, (-0.03, -0.001)),
                            _entry(-0.02, (-0.04, -0.001)))
    assert v["verdict"] == "retract"


def test_decision_rule_flags_disagreement_even_with_clean_positive_ci():
    v = apply_decision_rule(_entry(0.04, (0.02, 0.06)), _entry(-0.01, (-0.03, 0.005)),
                            _entry(0.02, (0.0, 0.04)))
    assert v["verdict"] == "survival_conditions_not_all_met"
    assert v["valid_only_direction_agrees"] is False


def test_stability_call_stable_and_inconclusive():
    stable = _stability_call(_entry(0.02, (0.01, 0.03)), _entry(0.015, (0.005, 0.025)), "P")
    assert stable["call"] == "stable"
    inconclusive = _stability_call(_entry(0.01, (-0.01, 0.03)), _entry(0.01, (-0.01, 0.02)), "R")
    assert inconclusive["call"] == "inconclusive_ci_includes_zero"


def test_stability_call_never_says_stable_when_valid_only_ci_crosses_zero():
    # Exactly the real P scenario: strict CI excludes zero, valid-only CI crosses
    # zero but keeps the same point-estimate sign. Must NOT be called "stable".
    result = _stability_call(_entry(0.0158, (0.0014, 0.0306)),
                             _entry(0.0118, (-0.0036, 0.0275)), "P")
    assert result["call"] == "positive_under_strict_valid_only_inconclusive_same_direction"
    assert result["call"] != "stable"
    assert "inconclusive" in result["prose"] and "same point-estimate direction" in result["prose"]
    assert result["strict_ci_excludes_zero"] is True
    assert result["valid_only_ci_excludes_zero"] is False


def test_stability_call_sign_disagreement_is_unstable():
    result = _stability_call(_entry(0.02, (0.01, 0.03)), _entry(-0.01, (-0.03, 0.005)), "X")
    assert result["call"] == "unstable_sign_disagreement_with_valid_only"


# ---------------------------------------------------------------------------------
# verify_step1: the hardened gate performs its OWN direct rendered-answer comparison
# against the frozen question bank, rather than trusting the stored field.
# ---------------------------------------------------------------------------------

def _judgment_row(cell_key, question_id, transcript_index, position_a_is_correct,
                  position_a_text, position_b_text, condition="b0", replicate=0):
    return {
        "cell_key": f"plan:{cell_key}:x",
        "result": {
            "question_id": question_id, "transcript_index": transcript_index,
            "position_a_is_correct": position_a_is_correct, "condition": condition,
            "replicate": replicate,
            "judge_messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": (
                    f"QUESTION: does X?\n\nPOSITION A: {position_a_text}\n\n"
                    f"POSITION B: {position_b_text}\n\nDEBATE TRANSCRIPT:\nTurn 1...")},
            ],
        },
    }


def test_verify_step1_direct_comparison_passes_on_consistent_data(tmp_path, monkeypatch):
    bank = {"Q1": {"correct_answer": "the right answer.", "wrong_answer": "the wrong answer."}}
    monkeypatch.setattr(mod, "_load_answer_bank", lambda: bank)
    monkeypatch.setattr(mod, "design_position_a_is_correct", lambda q, t: True)
    rows = [
        _judgment_row("debate_judgment", "Q1", 0, True,
                      "the right answer.", "the wrong answer."),
        _judgment_row("debate_judgment", "Q1", 0, True,
                      "the right answer.", "the wrong answer.", replicate=1),
    ]
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = mod.verify_step1(p, {})
    assert out["gate_pass"] is True
    assert out["direct_comparison_n_A_correct_units"] == 1
    assert out["direct_comparison_n_B_correct_units"] == 0
    assert out["n_rows_unmatched_answer_block"] == 0
    assert out["n_rows_field_vs_direct_comparison_disagreement"] == 0


def test_verify_step1_direct_comparison_catches_field_disagreement(tmp_path, monkeypatch):
    # The RENDERED text says B is correct (position_a_text is the wrong answer),
    # but the stored field claims A is correct: the direct comparison must flag
    # this even though nothing about the rendered-text-consistency check would.
    bank = {"Q1": {"correct_answer": "the right answer.", "wrong_answer": "the wrong answer."}}
    monkeypatch.setattr(mod, "_load_answer_bank", lambda: bank)
    monkeypatch.setattr(mod, "design_position_a_is_correct", lambda q, t: True)
    rows = [_judgment_row("debate_judgment", "Q1", 0, True,
                          "the wrong answer.", "the right answer.")]
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = mod.verify_step1(p, {})
    assert out["gate_pass"] is False
    assert out["n_rows_field_vs_direct_comparison_disagreement"] == 1
    assert out["direct_comparison_n_B_correct_units"] == 1


def test_verify_step1_flags_unmatched_answer_block(tmp_path, monkeypatch):
    bank = {"Q1": {"correct_answer": "the right answer.", "wrong_answer": "the wrong answer."}}
    monkeypatch.setattr(mod, "_load_answer_bank", lambda: bank)
    monkeypatch.setattr(mod, "design_position_a_is_correct", lambda q, t: True)
    rows = [_judgment_row("debate_judgment", "Q1", 0, True,
                          "neither known answer.", "the wrong answer.")]
    p = tmp_path / "results.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = mod.verify_step1(p, {})
    assert out["gate_pass"] is False
    assert out["n_rows_unmatched_answer_block"] == 1
