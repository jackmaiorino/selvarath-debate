import pandas as pd
from analysis import mechanism


def _row(qid, ti, budget, correct, side="A"):
    return {"question_id": qid, "transcript_index": ti, "judge_short": "70B",
            "query_budget": budget, "verdict_correct": correct, "wrong": not correct,
            "correct_side": side, "world": "selvarath", "question": "q",
            "correct_answer": "C", "wrong_answer": "W",
            "queries_submitted": [{"query": "x", "response": "NO"}],
            "reasoning": "r", "debate_transcript": []}


def test_extract_finds_correct0_wrong2_flip():
    df = pd.DataFrame([
        _row("Q1", 0, 0, True),    # correct at budget 0
        _row("Q1", 0, 2, False),   # wrong at budget 2  -> a flip
        _row("Q2", 0, 0, True),
        _row("Q2", 0, 2, True),    # stays correct -> not a flip
    ])
    cases = mechanism.extract_flip_cases(df)
    assert len(cases) == 1
    assert cases[0]["question_id"] == "Q1" and cases[0]["flip_budget"] == 2


def test_summarize_labels_counts_fractions():
    out = mechanism.summarize_labels([{"label": "FM1"}, {"label": "FM2"}, {"label": "FM1"}])
    d = out.set_index("label")
    assert d.loc["FM1", "count"] == 2
    assert abs(d.loc["FM1", "frac"] - 2 / 3) < 1e-9


def test_render_cases_markdown_smoke():
    cases = [{
        "question_id": "SEL-001", "transcript_index": 0, "world": "selvarath",
        "flip_budget": 2, "question": "q?", "correct_answer": "C", "wrong_answer": "W",
        "oracle_exchanges": [{"query": "claim", "response": "NO"}],
        "reasoning": "r", "debate_transcript": [{"speaker": "honest", "text": "hi"}],
    }]
    md = mechanism.render_cases_markdown(cases)
    assert "SEL-001" in md
    assert "claim" in md and "NO" in md
    assert "LABEL (FM1 / FM2 / other)" in md


def test_summarize_labels_accepts_dataframe_and_empty():
    df_in = pd.DataFrame([{"label": "FM1"}, {"label": "FM2"}, {"label": "FM1"}])
    out = mechanism.summarize_labels(df_in).set_index("label")
    assert out.loc["FM1", "count"] == 2
    empty = mechanism.summarize_labels([])
    assert list(empty.columns) == ["label", "count", "frac"]
    assert len(empty) == 0
