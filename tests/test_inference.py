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
