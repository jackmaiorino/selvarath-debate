"""The frozen-spec analysis engine, proven on synthetic data before real data.

Every number asserted here is hand-computed from the constructed dataset, so a
regression in weighting, support handling, the identity, stratification or Holm
shows up as an exact mismatch, not a statistical flake.
"""
import collections

from scripts.phase2_main_analysis import (
    PRIMARY_CONDITIONS,
    bootstrap_p_two_sided,
    condition_means,
    error_by_cell,
    estimands,
    holm,
    stratified_question_draws,
)


def _rec(q, world, cond, judge, deb, slot, correct):
    return {"kind": "debate_judgment", "question_id": q, "world": world,
            "condition": cond, "judge": judge, "debater": deb,
            "slot": slot, "correct": correct}


def _mini_dataset():
    """One question, one judge, one debater, two slots, fully hand-computable.

    b0: both slots correct (error 0.0). sequential_b2: one wrong (0.5).
    batch_same_qa_b2: both wrong (1.0). So H = 0.5, P = -0.5, R = 1.0.
    """
    rows = []
    for slot, (b0, seq, bat) in enumerate([(True, True, False), (True, False, False)]):
        rows.append(_rec("Q1", "wA", "b0", "J", "D", (slot, 0), b0))
        rows.append(_rec("Q1", "wA", "sequential_b2", "J", "D", (slot, 0), seq))
        rows.append(_rec("Q1", "wA", "batch_same_qa_b2", "J", "D", (slot, 0), bat))
    return rows


def test_point_estimands_are_exact():
    rates = error_by_cell(_mini_dataset(), PRIMARY_CONDITIONS,
                          invalid_wrong=True, common_support=False)
    e = estimands(condition_means(rates, PRIMARY_CONDITIONS))
    assert e == {"H": 0.5, "P": -0.5, "R": 1.0}


def test_identity_holds_exactly():
    e = estimands(condition_means(
        error_by_cell(_mini_dataset(), PRIMARY_CONDITIONS,
                      invalid_wrong=True, common_support=False),
        PRIMARY_CONDITIONS))
    assert abs((e["P"] + e["R"]) - e["H"]) == 0.0


def test_invalid_counts_wrong_in_primary():
    rows = _mini_dataset()
    # Turn a CORRECT b0 slot into a strict INVALID: primary error for b0 rises
    # from 0.0 to 0.5, so H drops from 0.5 to 0.0.
    rows[0] = dict(rows[0], correct=None)
    rates = error_by_cell(rows, PRIMARY_CONDITIONS,
                          invalid_wrong=True, common_support=False)
    e = estimands(condition_means(rates, PRIMARY_CONDITIONS))
    assert e["H"] == 0.0


def test_valid_only_sensitivity_drops_the_slot_from_every_arm():
    rows = _mini_dataset()
    rows[0] = dict(rows[0], correct=None)   # slot 0 invalid in b0 only
    rates = error_by_cell(rows, PRIMARY_CONDITIONS,
                          invalid_wrong=False, common_support=True)
    # Only slot 1 survives, in ALL arms: b0 correct, seq wrong, batch wrong.
    ((_cell, r),) = rates.items()
    assert r == {"b0": 0.0, "sequential_b2": 1.0, "batch_same_qa_b2": 1.0}


def test_equal_question_judge_debater_weights():
    # Two (q, j, d) cells with different slot counts must weigh equally.
    rows = []
    for slot in range(4):   # four slots, all wrong in b0
        rows.append(_rec("Q1", "wA", "b0", "J1", "D", (slot, 0), False))
        rows.append(_rec("Q1", "wA", "sequential_b2", "J1", "D", (slot, 0), True))
        rows.append(_rec("Q1", "wA", "batch_same_qa_b2", "J1", "D", (slot, 0), True))
    for slot in range(2):   # two slots, all correct in b0
        rows.append(_rec("Q2", "wA", "b0", "J1", "D", (slot, 0), True))
        rows.append(_rec("Q2", "wA", "sequential_b2", "J1", "D", (slot, 0), True))
        rows.append(_rec("Q2", "wA", "batch_same_qa_b2", "J1", "D", (slot, 0), True))
    rates = error_by_cell(rows, PRIMARY_CONDITIONS,
                          invalid_wrong=True, common_support=False)
    means = condition_means(rates, PRIMARY_CONDITIONS)
    assert means["b0"] == 0.5   # (1.0 + 0.0) / 2, not 4/6


def test_stratified_draws_preserve_world_counts():
    rows = [_rec(f"Q{i}", "wA", "b0", "J", "D", (0, 0), True) for i in range(5)]
    rows += [_rec(f"R{i}", "wB", "b0", "J", "D", (0, 0), True) for i in range(3)]
    draws = stratified_question_draws(rows, b=50, seed=7)
    for mult in draws:
        a = sum(n for q, n in mult.items() if q.startswith("Q"))
        b = sum(n for q, n in mult.items() if q.startswith("R"))
        assert (a, b) == (5, 3)


def test_bootstrap_multiplicity_weights_questions():
    rows = _mini_dataset()
    for slot in range(2):
        rows.append(_rec("Q2", "wA", "b0", "J", "D", (slot, 0), False))
        rows.append(_rec("Q2", "wA", "sequential_b2", "J", "D", (slot, 0), False))
        rows.append(_rec("Q2", "wA", "batch_same_qa_b2", "J", "D", (slot, 0), False))
    rates = error_by_cell(rows, PRIMARY_CONDITIONS,
                          invalid_wrong=True, common_support=False)
    # Q1 drawn twice, Q2 zero times: means must equal Q1's alone.
    means = condition_means(rates, PRIMARY_CONDITIONS,
                            question_multiplicity={"Q1": 2, "Q2": 0})
    assert means == {"b0": 0.0, "sequential_b2": 0.5, "batch_same_qa_b2": 1.0}


def test_holm_adjustment_is_step_down_and_monotone():
    adj = holm({"H": 0.01, "P": 0.04, "R": 0.03})
    assert adj["H"] == 0.03            # 3 * 0.01
    assert adj["R"] == 0.06            # max(0.03, 2 * 0.03)
    assert adj["P"] == 0.06            # max(0.06, 1 * 0.04) with monotonicity
    assert all(0 < p <= 1 for p in adj.values())


def test_bootstrap_p_is_corrected_and_capped():
    assert bootstrap_p_two_sided([1.0] * 99) == 2 * (1 / 100)
    assert bootstrap_p_two_sided([-1.0, 1.0] * 50) == 1.0


def _rec_kind(kind, q, cond, judge, deb, slot, correct):
    return {"kind": kind, "question_id": q, "world": "wA", "condition": cond,
            "judge": judge, "debater": deb, "slot": slot, "correct": correct}


def test_D_clean_per_question_weighting():
    from scripts.phase2_main_analysis import per_question_D, weighted_question_mean
    rows = []
    # Debate sequential_b2, one judge, two debaters: errors 1.0 and 0.0, mean 0.5.
    rows.append(_rec_kind("debate_judgment", "Q1", "sequential_b2", "J", "D1", (0, 0), False))
    rows.append(_rec_kind("debate_judgment", "Q1", "sequential_b2", "J", "D2", (0, 0), True))
    # No-debate clean_b2, same judge: error 0.0 over two replicates.
    rows.append(_rec_kind("no_debate_judgment", "Q1", "clean_b2", "J", None, (0, None), True))
    rows.append(_rec_kind("no_debate_judgment", "Q1", "clean_b2", "J", None, (1, None), True))
    per_q = per_question_D(rows, "sequential_b2", "clean_b2")
    assert per_q == {"Q1": 0.5}
    assert weighted_question_mean(per_q) == 0.5
    assert weighted_question_mean(per_q, {"Q1": 3}) == 0.5


def test_C_interaction_sign_and_judge_filter():
    from scripts.phase2_main_analysis import per_question_C
    rows = []
    # At the cap judge: debater A harmed by uncapping (uncapped 1.0, capped 0.0),
    # debater B unaffected (0.0, 0.0): C = (1.0 - 0.0) - (0.0 - 0.0) = 1.0.
    rows.append(_rec_kind("debate_judgment", "Q1", "b0", "CAPJ", "A", (0, 0), False))
    rows.append(_rec_kind("cap_protection_judgment", "Q1", "capped150_b0", "CAPJ", "A", (0, 0), True))
    rows.append(_rec_kind("debate_judgment", "Q1", "b0", "CAPJ", "B", (0, 0), True))
    rows.append(_rec_kind("cap_protection_judgment", "Q1", "capped150_b0", "CAPJ", "B", (0, 0), True))
    # Rows at a different judge must be ignored entirely.
    rows.append(_rec_kind("debate_judgment", "Q1", "b0", "OTHER", "A", (0, 0), True))
    assert per_question_C(rows, "CAPJ", "A", "B") == {"Q1": 1.0}
