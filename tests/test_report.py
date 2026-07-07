import pytest
from analysis import run_report, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


def _tiny_df():
    """Smallest DataFrame accepted by every table/stat function build_report calls.

    Two questions (one per correct_side, one per world), four primary budgets each,
    for both judges. Wrong exactly at budgets {1,2,5} and never at budget 0, so
    Delta-few is a deterministic 100pp for every possible cluster-bootstrap resample
    (only two distinct question clusters, both with the same pattern) -- this makes
    the harm-claim gate BANKED so build_report exercises the full recommendation
    branch (not the early "NOT banked" return).
    """
    import pandas as pd

    rows = []
    for judge in ("70B", "8B"):
        for qid, side, world in (("q1", "A", "w1"), ("q2", "B", "w2")):
            for budget in (0, 1, 2, 5):
                wrong = budget != 0
                rows.append({
                    "question_id": qid,
                    "transcript_index": 0,
                    "world": world,
                    "judge_short": judge,
                    "query_budget": budget,
                    "correct_side": side,
                    "verdict_correct": not wrong,
                    "wrong": wrong,
                    "confidence": 0.7,
                    "reasoning": "ok",
                })
    return pd.DataFrame(rows)


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


def test_kappa_perfect_and_chance():
    from analysis.run_report import _kappa
    assert _kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    # 50/50 marginals, agreement at chance level -> kappa ~ 0
    assert abs(_kappa(["a", "b"] * 10, ["a"] * 10 + ["b"] * 10)) < 0.15


def test_mechanism_section_carries_correction_and_pass2(monkeypatch):
    import pandas as pd
    from analysis import run_report
    df = _tiny_df()  # reuse the existing fixture helper in this test file; if named differently, use that one
    labels = [{"case_id": "c1", "label": "FM1"}, {"case_id": "c2", "label": "FM2"}]
    labels2 = [{"case_id": "c1", "label": "FM1"}, {"case_id": "c2", "label": "other"}]
    text = run_report.build_report(df, B=10, seed=0, labels=labels, labels2=labels2)
    assert "corrupted" in text.lower()          # correction framing present
    assert "kappa" in text.lower() or "κ" in text
    assert "Deliverable D" not in text          # stale recommendation gone
    assert "mechanism-label validation ($0)" not in text
