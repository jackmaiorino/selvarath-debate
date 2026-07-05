import pytest
from analysis import run_report, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


@real
def test_report_has_key_sections():
    df = load.load_judgments_df()
    md = run_report.build_report(df, B=500, seed=0)
    for header in ["# Limited-Verification Re-analysis", "## Primary inference (70B)",
                   "## Parse-sensitivity", "## Robustness", "## Gate evaluation"]:
        assert header in md
    assert "Δfew" in md


def test_gate_logic_boundaries():
    import pandas as pd
    from analysis.run_report import _gate

    def summ(few_overall, ci_lo, a_pt, b_pt):
        return pd.DataFrame([
            {"stat": "few", "stratum": "overall", "point_pp": few_overall, "ci_lo_pp": ci_lo, "ci_hi_pp": ci_lo + 5},
            {"stat": "few", "stratum": "A", "point_pp": a_pt, "ci_lo_pp": 0, "ci_hi_pp": 0},
            {"stat": "few", "stratum": "B", "point_pp": b_pt, "ci_lo_pp": 0, "ci_hi_pp": 0},
        ])

    assert bool(_gate(summ(7.2, 4.5, 8.2, 6.2))[0])       # BANKED
    assert not bool(_gate(summ(7.2, 1.5, 8.2, 6.2))[0])   # CI lower bound < 2pp
    assert not bool(_gate(summ(7.2, 4.5, -1.0, 6.2))[0])  # stratum A not positive
