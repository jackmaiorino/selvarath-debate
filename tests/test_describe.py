import pytest
from analysis import describe, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


def test_wilson_ci_known():
    lo, hi = describe.wilson_ci(6, 318)
    assert 0.8 < lo < 1.1 and 3.9 < hi < 4.3     # ~[0.9, 4.1]


@real
def test_70b_budget0_winrate():
    df = load.load_judgments_df()
    tbl = describe.win_rate_table(df, "70B").set_index("budget")
    assert abs(tbl.loc[0, "wrong_pct"] - 1.887) < 0.05   # 6/318
    assert tbl.loc[0, "n"] == 318


@real
def test_side_split_matches_known():
    df = load.load_judgments_df()
    tbl = describe.side_stratified_table(df, "70B").set_index("budget")
    assert abs(tbl.loc[2, "wrong_pct_Acorrect"] - 9.9) < 0.6
    assert abs(tbl.loc[2, "wrong_pct_Bcorrect"] - 9.0) < 0.6


def test_confidence_by_correctness_split_logic():
    import math
    import pandas as pd
    df = pd.DataFrame([
        {"judge_short": "70B", "query_budget": 0, "confidence": 5, "wrong": False},
        {"judge_short": "70B", "query_budget": 0, "confidence": 1, "wrong": True},
        {"judge_short": "70B", "query_budget": 2, "confidence": 4, "wrong": False},
    ])
    tbl = describe.confidence_by_correctness(df, "70B").set_index("budget")
    assert tbl.loc[0, "mean_conf"] == 3.0
    assert tbl.loc[0, "mean_conf_correct"] == 5.0
    assert tbl.loc[0, "mean_conf_wrong"] == 1.0
    assert math.isnan(tbl.loc[2, "mean_conf_wrong"])


@real
def test_confidence_rises_with_budget_70b():
    df = load.load_judgments_df()
    tbl = describe.confidence_by_correctness(df, "70B").set_index("budget")
    assert 1 <= tbl.loc[0, "mean_conf"] <= 5
    assert tbl.loc[2, "mean_conf"] > tbl.loc[0, "mean_conf"]
