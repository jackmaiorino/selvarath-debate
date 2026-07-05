import pandas as pd
import pytest
from analysis import robustness, load
from analysis.load import DATA_DIR

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


def _row(qid, ti, budget, correct):
    return {"question_id": qid, "transcript_index": ti, "judge_short": "70B",
            "query_budget": budget, "verdict_correct": correct, "wrong": not correct,
            "correct_side": "A", "world": "selvarath"}


def test_discordance_counts_flips():
    df = pd.DataFrame([
        _row("Q1", 0, 0, True), _row("Q1", 0, 1, False),   # correct->wrong
        _row("Q2", 0, 0, False), _row("Q2", 0, 1, True),   # wrong->correct
    ])
    d = robustness.discordance(df, "70B", flip_budgets=(1,)).iloc[0]
    assert d.correct_to_wrong == 1 and d.wrong_to_correct == 1
    assert d.net_new_errors == 0


@real
def test_lowo_has_none_plus_three_worlds():
    df = load.load_judgments_df()
    tbl = robustness.leave_one_world_out(df, "70B")
    assert list(tbl.dropped)[0] == "none"
    assert len(tbl) == 4          # none + 3 worlds
