"""Focused tests for scripts/phase3_canary_closeout.py's core computations.

All tests run on synthetic, in-memory fixtures -- no E:/ archive dependency, no provider calls.
Covers: t-interval numerics, the capability-anchor parser (tolerant/strict), the calibration
gate arithmetic, spend summation (the reservation-vs-terminal double-count trap), dedup
counting, and R_U/D_U arithmetic.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import phase3_canary_closeout as pcc  # noqa: E402


# --------------------------------------------------------------------------------------------
# t-distribution numerics
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("df,expected", [(1, 6.3138), (5, 2.0150), (23, 1.7139), (47, 1.6779)])
def test_t_ppf_matches_textbook_critical_values(df, expected):
    assert pcc.t_ppf(0.95, df) == pytest.approx(expected, abs=1e-3)


def test_mean_t_interval_90_constant_values_has_zero_width():
    result = pcc.mean_t_interval_90([5.0, 5.0, 5.0, 5.0])
    assert result["mean"] == pytest.approx(5.0)
    assert result["lower90"] == pytest.approx(5.0)
    assert result["upper90"] == pytest.approx(5.0)
    assert result["interval_defined"] is True


def test_mean_t_interval_90_single_value_is_undefined():
    result = pcc.mean_t_interval_90([7.0])
    assert result["interval_defined"] is False
    assert result["upper90"] == pytest.approx(7.0)
    assert result["n"] == 1


def test_mean_t_interval_90_upper_bound_exceeds_mean_with_spread():
    result = pcc.mean_t_interval_90([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert result["upper90"] > result["mean"]
    assert result["lower90"] < result["mean"]
    # symmetric interval around the mean
    assert (result["upper90"] - result["mean"]) == pytest.approx(result["mean"] - result["lower90"])


# --------------------------------------------------------------------------------------------
# Capability-anchor parser (decisions.capability_anchor.parser_rule)
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("ANSWER: A", "A"),
    ("ANSWER: B", "B"),
    ("ANSWER: A.", "A"),
    ("  ANSWER: B  ", "B"),
    ("ANSWER: B..", "INVALID"),
    ("ANSWER: C", "INVALID"),
    ("The answer is A", "INVALID"),
    ("answer: a", "INVALID"),
    ("", "INVALID"),
    (None, "INVALID"),
])
def test_parse_capability_verdict_tolerant(raw, expected):
    assert pcc.parse_capability_verdict_tolerant(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("ANSWER: A", "A"),
    ("ANSWER: B", "B"),
    ("ANSWER: A.", "INVALID"),   # strict rejects the trailing period the tolerant parse allows
    ("ANSWER: C", "INVALID"),
    (None, "INVALID"),
])
def test_parse_capability_verdict_strict(raw, expected):
    assert pcc.parse_capability_verdict_strict(raw) == expected


def test_compute_anchor_for_judge_scores_and_flags_zero_spread():
    # 4 cells, all tolerant-correct (side always matches the tolerant parse) -> zero spread;
    # strict parse fails on the trailing-period cell, so strict has spread.
    rows = [
        {"raw_verdict_text": "ANSWER: A", "side": "A"},
        {"raw_verdict_text": "ANSWER: B", "side": "B"},
        {"raw_verdict_text": "ANSWER: A.", "side": "A"},  # tolerant OK, strict INVALID (wrong)
        {"raw_verdict_text": "ANSWER: B", "side": "B"},
    ]
    result = pcc.compute_anchor_for_judge(rows)
    assert result["n_anchor_cells"] == 4
    assert result["tolerant_score"] == 4
    assert result["zero_spread_tolerant"] is True
    assert result["strict_score"] == 3
    assert result["zero_spread_strict"] is False


def test_compute_anchor_for_judge_empty():
    result = pcc.compute_anchor_for_judge([])
    assert result["n_anchor_cells"] == 0
    assert result["tolerant_fraction"] is None
    assert result["zero_spread_tolerant"] is None


# --------------------------------------------------------------------------------------------
# Calibration gate arithmetic (decisions.launch_gates.calibration_gates_per_judge)
# --------------------------------------------------------------------------------------------

def _row(parse_ok: bool, verdict: str | None, position_a_is_correct: bool) -> dict:
    return {"parse_ok": parse_ok, "verdict": verdict, "position_a_is_correct": position_a_is_correct}


def test_compute_gate_for_judge_all_correct_no_bias_passes_both_gates():
    rows = (
        [_row(True, "A", True) for _ in range(48)]
        + [_row(True, "B", False) for _ in range(48)]
    )
    result = pcc.compute_gate_for_judge(rows)
    assert result["n_core_b0"] == 96
    assert result["invalid_count"] == 0
    assert result["invalid_rate"] == 0.0
    assert result["invalid_gate_pass"] is True
    assert result["side_bias_pp"] == pytest.approx(0.0)
    assert result["side_bias_gate_pass"] is True
    # the underlying per-side error levels must never be exposed (blinding constraint)
    assert "err_a" not in result and "err_b" not in result
    assert set(result) == {
        "n_core_b0", "n_a_correct", "n_b_correct", "invalid_count", "invalid_rate",
        "invalid_gate_threshold", "invalid_gate_pass", "side_bias_pp",
        "side_bias_gate_threshold_pp", "side_bias_gate_pass",
    }


def test_compute_gate_for_judge_invalid_rate_threshold():
    # exactly 2/96 = 2.08% >= 0.02 -> fails (threshold is strict "<")
    rows = (
        [_row(False, None, True) for _ in range(2)]
        + [_row(True, "A", True) for _ in range(46)]
        + [_row(True, "B", False) for _ in range(48)]
    )
    result = pcc.compute_gate_for_judge(rows)
    assert result["invalid_rate"] == pytest.approx(2 / 96)
    assert result["invalid_gate_pass"] is False


def test_compute_gate_for_judge_invalid_rate_just_under_threshold_passes():
    # 1/96 = 1.04% < 2% -> passes
    rows = (
        [_row(False, None, True)]
        + [_row(True, "A", True) for _ in range(47)]
        + [_row(True, "B", False) for _ in range(48)]
    )
    result = pcc.compute_gate_for_judge(rows)
    assert result["invalid_gate_pass"] is True


def test_compute_gate_for_judge_side_bias_detects_asymmetric_error():
    # A-correct group: all correct (error 0). B-correct group: all wrong (error 1).
    # side_bias_pp = |0 - 1| * 100 = 100, fails the <=10pp gate.
    rows = (
        [_row(True, "A", True) for _ in range(48)]  # judge says A, A is correct -> right
        + [_row(True, "A", False) for _ in range(48)]  # judge says A, B is correct -> wrong
    )
    result = pcc.compute_gate_for_judge(rows)
    assert result["side_bias_pp"] == pytest.approx(100.0)
    assert result["side_bias_gate_pass"] is False


def test_compute_gate_for_judge_strict_invalid_counts_as_wrong_in_side_bias():
    # An INVALID (parse_ok=False) row counts wrong regardless of position, per invalid_policy.
    rows = (
        [_row(True, "A", True) for _ in range(47)] + [_row(False, None, True)]  # A-correct: 1 wrong of 48
        + [_row(True, "B", False) for _ in range(48)]  # B-correct: all right
    )
    result = pcc.compute_gate_for_judge(rows)
    # err_a = 1/48, err_b = 0 -> side_bias_pp ~= 2.083
    assert result["side_bias_pp"] == pytest.approx(100 / 48, abs=1e-6)


def test_compute_gate_for_judge_realized_split_need_not_be_48_48():
    # A real phase-3 finding: position assignment is per-question, not per-K2-side, so the
    # realized A-correct/B-correct split need not be exactly 48/48. The gate must still compute
    # correctly (and report the realized counts) on an unbalanced split.
    rows = [_row(True, "A", True) for _ in range(60)] + [_row(True, "B", False) for _ in range(36)]
    result = pcc.compute_gate_for_judge(rows)
    assert result["n_a_correct"] == 60
    assert result["n_b_correct"] == 36
    assert result["side_bias_pp"] == pytest.approx(0.0)  # both groups still all-correct here


# --------------------------------------------------------------------------------------------
# payload_hash reproduction
# --------------------------------------------------------------------------------------------

def test_payload_hash_is_deterministic():
    h1 = pcc.payload_hash("CLAIM: x", "correct text", "wrong text")
    h2 = pcc.payload_hash("CLAIM: x", "correct text", "wrong text")
    assert h1 == h2
    assert len(h1) == 64


def test_payload_hash_is_sensitive_to_candidate_order():
    h_ab = pcc.payload_hash("CLAIM: x", "correct text", "wrong text")
    h_ba = pcc.payload_hash("CLAIM: x", "wrong text", "correct text")
    assert h_ab != h_ba


# --------------------------------------------------------------------------------------------
# Spend summation: the reservation-vs-terminal double-count trap
# --------------------------------------------------------------------------------------------

def test_compute_spend_does_not_double_count_reservations():
    rows = [
        {"status": "ledger_genesis"},
        # attempt 1: reserved then settled -- only the terminal row's cost counts
        {"attempt_id": "a1", "status": "reserved", "cost_usd": 0.02},
        {"attempt_id": "a1", "status": "success", "cost_usd": 0.005},
        # attempt 2: reserved then unknown_charge (uncertain, counted separately, in full)
        {"attempt_id": "a2", "status": "reserved", "cost_usd": 0.03},
        {"attempt_id": "a2", "status": "unknown_charge", "cost_usd": 0.03},
        # attempt 3: open reservation, no terminal row yet
        {"attempt_id": "a3", "status": "reserved", "cost_usd": 0.01},
    ]
    result = pcc.compute_spend(rows)
    assert result["settled_usd"] == pytest.approx(0.005)
    assert result["settled_count"] == 1
    assert result["uncertain_usd"] == pytest.approx(0.03)
    assert result["uncertain_count"] == 1
    assert result["open_reservation_usd"] == pytest.approx(0.01)
    assert result["open_reservation_count"] == 1
    # the naive (wrong) sum would be 0.02+0.005+0.03+0.03+0.01 = 0.095; correct total is 0.045
    naive_sum = sum(r.get("cost_usd", 0.0) for r in rows if r.get("status") != "ledger_genesis")
    assert naive_sum == pytest.approx(0.095)
    assert result["total_usd"] == pytest.approx(0.045)
    assert result["total_usd"] != pytest.approx(naive_sum)


def test_compute_spend_empty():
    result = pcc.compute_spend([{"status": "ledger_genesis"}])
    assert result["total_usd"] == 0.0
    assert result["settled_count"] == 0


# --------------------------------------------------------------------------------------------
# Dedup counting
# --------------------------------------------------------------------------------------------

def test_compute_dedup_classifies_cross_judge_and_cross_arm():
    events = [
        # payload P1: first seen judge=J1/arm=b1, then reused by J2 (cross-judge), then by
        # J1 again under arm=b2 (cross-arm, same judge).
        {"payload_sha256": "P1", "judge_model": "J1", "condition": "b1", "question_id": "Q1"},
        {"payload_sha256": "P1", "judge_model": "J2", "condition": "b1", "question_id": "Q1"},
        {"payload_sha256": "P1", "judge_model": "J1", "condition": "b2", "question_id": "Q1"},
        # payload P2: unique, seen once
        {"payload_sha256": "P2", "judge_model": "J1", "condition": "b1", "question_id": "Q2"},
        # payload P3: repeated twice by the exact same (judge, arm)
        {"payload_sha256": "P3", "judge_model": "J2", "condition": "b4", "question_id": "Q3"},
        {"payload_sha256": "P3", "judge_model": "J2", "condition": "b4", "question_id": "Q3"},
    ]
    result = pcc.compute_dedup(events)
    assert result["total_events"] == 6
    assert result["unique_payloads"] == 3
    assert result["duplicate_events"] == 3
    assert result["duplicate_cross_judge"] == 1
    assert result["duplicate_cross_arm_same_judge"] == 1
    assert result["duplicate_same_judge_same_arm"] == 1
    assert result["duplicate_rate"] == pytest.approx(3 / 6)


def test_compute_dedup_empty():
    result = pcc.compute_dedup([])
    assert result["total_events"] == 0
    assert result["unique_payloads"] == 0
    assert result["duplicate_rate"] is None


# --------------------------------------------------------------------------------------------
# R_U / D_U arithmetic (decisions.configuration_selection.projection_rule)
# --------------------------------------------------------------------------------------------

def test_compute_R_U_D_U_basic_arithmetic():
    accrued = 1000
    rates = {("J1", "b1"): 2.0, ("J2", "b1"): 1.5}
    slots = {("J1", "b1"): 1000, ("J2", "b1"): 1000}
    quota_days = [500.0, 500.0, 500.0]  # constant -> zero-width interval, L90 == mean
    result = pcc.compute_R_U_D_U(accrued, rates, slots, quota_days, dedup_discount=0.0)
    # projected = 1000*2.0 + 1000*1.5 = 3500; R_U = 1000 + 3500 = 4500
    assert result["projected_rulings"] == pytest.approx(3500.0)
    assert result["R_U"] == pytest.approx(4500.0)
    assert result["R_U_pass"] is True  # well under R_max
    assert result["L90_rulings_per_quota_day"] == pytest.approx(500.0)
    assert result["D_U"] == math.ceil(4500.0 / 500.0)
    assert result["D_U"] == 9


def test_compute_R_U_D_U_dedup_discount_reduces_projection_but_not_accrued():
    accrued = 100
    rates = {("J1", "b1"): 10.0}
    slots = {("J1", "b1"): 100}
    quota_days = [50.0, 50.0]
    no_credit = pcc.compute_R_U_D_U(accrued, rates, slots, quota_days, dedup_discount=0.0)
    with_credit = pcc.compute_R_U_D_U(accrued, rates, slots, quota_days, dedup_discount=0.5)
    assert no_credit["projected_rulings"] == pytest.approx(1000.0)
    assert with_credit["projected_rulings"] == pytest.approx(500.0)
    # accrued component is untouched by the discount
    assert no_credit["R_U"] - no_credit["accrued_rulings"] == pytest.approx(1000.0)
    assert with_credit["R_U"] - with_credit["accrued_rulings"] == pytest.approx(500.0)
    assert with_credit["R_U"] < no_credit["R_U"]


def test_compute_R_U_D_U_respects_limits():
    # deliberately exceed R_max and D_max
    accrued = 0
    rates = {("J1", "b8"): 100.0}
    slots = {("J1", "b8"): 5000}  # 500,000 projected rulings, far past R_max=135000
    quota_days = [10.0]  # very slow pace (single observation -> falls back to mean)
    result = pcc.compute_R_U_D_U(accrued, rates, slots, quota_days)
    assert result["R_U_pass"] is False
    assert result["D_U_pass"] is False
    assert result["D_U"] is not None and result["D_U"] > result["D_max"]


def test_compute_R_U_D_U_no_quota_day_data_leaves_D_U_undefined():
    result = pcc.compute_R_U_D_U(10, {("J1", "b1"): 1.0}, {("J1", "b1"): 10}, [])
    assert result["D_U"] is None
    assert result["D_U_pass"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
