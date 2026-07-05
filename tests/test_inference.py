import numpy as np
import pytest
from analysis import inference, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


@real
def test_delta_few_point_is_7_2():
    df = load.load_judgments_df()
    assert abs(inference.point_estimate(df, "70B", "few") - 7.2) < 0.15


@real
def test_bootstrap_ci_brackets_point_and_excludes_zero():
    df = load.load_judgments_df()
    pt = inference.point_estimate(df, "70B", "few")
    lo, hi = inference.cluster_bootstrap_ci(df, "70B", "few", B=2000, seed=0)
    assert lo < pt < hi
    assert lo > 0                     # harm effect excludes zero


@real
def test_bootstrap_is_seeded():
    df = load.load_judgments_df()
    a = inference.cluster_bootstrap_ci(df, "70B", "few", B=1000, seed=7)
    b = inference.cluster_bootstrap_ci(df, "70B", "few", B=1000, seed=7)
    assert a == b


@real
def test_summarize_shape_and_content():
    df = load.load_judgments_df()
    s = inference.summarize(df, "70B", B=500, seed=0)
    assert len(s) == 6                                   # 2 stats x 3 strata
    assert set(s.stat) == {"few", "recover5"}
    assert set(s.stratum) == {"overall", "A", "B"}
    few_overall = s[(s.stat == "few") & (s.stratum == "overall")].iloc[0]
    assert abs(few_overall.point_pp - 7.2) < 0.2
    assert few_overall.ci_lo_pp < few_overall.point_pp < few_overall.ci_hi_pp


@real
def test_delta_few_positive_in_both_strata():
    df = load.load_judgments_df()
    a = inference.point_estimate(df, "70B", "few", correct_side="A")
    b = inference.point_estimate(df, "70B", "few", correct_side="B")
    assert a > 0 and b > 0


@real
def test_recover5_point_is_hand_checked():
    df = load.load_judgments_df()
    r = inference.point_estimate(df, "70B", "recover5")
    assert abs(r - 3.8) < 0.3      # 1/2(8.8+9.4) - 5.3 = 3.8 pp
