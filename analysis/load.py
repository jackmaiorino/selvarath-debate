"""Load and join the pilot judgment + transcript data into one tidy table."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
DATA_DIR = _REPO / "data"
WORLD_DIR = _REPO / "world_specs"

JUDGE_SHORT = {
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": "70B",
    "meta-llama/Meta-Llama-3-8B-Instruct-Lite": "8B",
}


def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_transcripts(path=None):
    path = Path(path) if path else DATA_DIR / "transcripts.jsonl"
    return {(r["question_id"], r["transcript_index"]): r for r in _read_jsonl(path)}


def load_judgments_df(judg_path=None, trans_path=None):
    judg_path = Path(judg_path) if judg_path else DATA_DIR / "judgments.jsonl"
    transcripts = load_transcripts(trans_path)
    rows = []
    for j in _read_jsonl(judg_path):
        t = transcripts.get((j["question_id"], j["transcript_index"]), {})
        rows.append({
            "question_id": j["question_id"],
            "transcript_index": j["transcript_index"],
            "world": t.get("world"),
            "judge_model": j["judge_model"],
            "judge_short": JUDGE_SHORT.get(j["judge_model"], j["judge_model"]),
            "query_budget": j["query_budget"],
            "position_a_is_correct": j["position_a_is_correct"],
            "correct_side": "A" if j["position_a_is_correct"] else "B",
            "verdict": j["verdict"],
            "verdict_correct": bool(j["verdict_correct"]),
            "wrong": not bool(j["verdict_correct"]),
            "queries_used": j["queries_used"],
            "confidence": j["confidence"],
            "reasoning": j.get("reasoning", "") or "",
            "queries_submitted": j.get("queries_submitted", []),
            "question": t.get("question"),
            "correct_answer": t.get("correct_answer"),
            "wrong_answer": t.get("wrong_answer"),
            "honest_first": t.get("honest_first"),
            "debate_transcript": t.get("debate_transcript", []),
        })
    return pd.DataFrame(rows)


def load_world(world, world_dir=None):
    world_dir = Path(world_dir) if world_dir else WORLD_DIR
    return (world_dir / f"{world}.txt").read_text(encoding="utf-8")
