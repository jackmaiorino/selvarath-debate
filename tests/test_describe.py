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
