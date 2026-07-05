import json
from analysis import load


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _fixture(tmp_path):
    trans = [{
        "question_id": "Q1", "transcript_index": 0, "world": "selvarath",
        "question": "q?", "correct_answer": "C", "wrong_answer": "W",
        "honest_first": True, "debate_transcript": [{"speaker": "honest", "text": "hi"}],
    }]
    judg = [{
        "question_id": "Q1", "transcript_index": 0,
        "judge_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "query_budget": 0, "position_a_is_correct": True,
        "queries_submitted": [], "queries_used": 0, "verdict": "Position A",
        "verdict_correct": True, "confidence": 4, "reasoning": "because", "seed": 1,
    }]
    tp = tmp_path / "transcripts.jsonl"; jp = tmp_path / "judgments.jsonl"
    _write_jsonl(tp, trans); _write_jsonl(jp, judg)
    return jp, tp


def test_join_and_derived_columns(tmp_path):
    jp, tp = _fixture(tmp_path)
    df = load.load_judgments_df(jp, tp)
    assert len(df) == 1
    row = df.iloc[0]
    assert row.world == "selvarath"          # joined from transcript
    assert row.judge_short == "70B"
    assert row.correct_side == "A"           # position_a_is_correct True
    assert row.wrong is False or row.wrong == False  # ~verdict_correct
    assert row.question == "q?"


import pytest
from analysis.load import DATA_DIR


real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


@real
def test_real_data_counts():
    df = load.load_judgments_df()
    assert len(df) == 2583
    assert df.world.notna().all()            # every judgment joined a transcript
    assert set(df.judge_short) == {"70B", "8B"}
    assert df[df.judge_short == "70B"].world.nunique() == 3
