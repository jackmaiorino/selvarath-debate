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
