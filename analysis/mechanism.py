"""Extract the 70B correct@0 -> wrong@{1,2} flip cases for in-session FM1/FM2 labeling.

No API calls: this only prepares human-readable cases and tallies labels supplied
by the analyst.
"""
from __future__ import annotations

import pandas as pd

from analysis.load import load_world


def extract_flip_cases(df, judge="70B", base_budget=0, flip_budgets=(1, 2)):
    sub = df[df.judge_short == judge]
    base = (sub[sub.query_budget == base_budget]
            .set_index(["question_id", "transcript_index"])["verdict_correct"])
    cases = []
    for fb in flip_budgets:
        for r in sub[sub.query_budget == fb].itertuples(index=False):
            key = (r.question_id, r.transcript_index)
            if key in base.index and bool(base.loc[key]) and r.wrong:
                cases.append({
                    "question_id": r.question_id,
                    "transcript_index": r.transcript_index,
                    "world": r.world,
                    "flip_budget": fb,
                    "question": r.question,
                    "correct_answer": r.correct_answer,
                    "wrong_answer": r.wrong_answer,
                    "oracle_exchanges": r.queries_submitted,
                    "reasoning": r.reasoning,
                    "debate_transcript": r.debate_transcript,
                })
    return cases


def render_cases_markdown(cases, world_dir=None):
    worlds = {}
    parts = [f"# FM1/FM2 labeling — {len(cases)} flip cases (70B, correct@0 -> wrong@{{1,2}})\n"]
    for i, c in enumerate(cases, 1):
        if c["world"] not in worlds:
            worlds[c["world"]] = load_world(c["world"], world_dir)
        parts.append(f"\n---\n\n## Case {i}: {c['question_id']} / t{c['transcript_index']} "
                     f"(flip budget {c['flip_budget']})\n")
        parts.append(f"**Question:** {c['question']}\n")
        parts.append(f"**Correct answer:** {c['correct_answer']}\n")
        parts.append(f"**Wrong answer:** {c['wrong_answer']}\n")
        parts.append("\n**Oracle exchanges:**\n")
        for ex in c["oracle_exchanges"]:
            parts.append(f"- Q: {ex.get('query')} -> **{ex.get('response')}**\n")
        parts.append(f"\n**Judge reasoning:** {c['reasoning']}\n")
        parts.append("\n**Debate transcript:**\n")
        for turn in c["debate_transcript"]:
            parts.append(f"- ({turn.get('speaker')}) {turn.get('text')}\n")
        parts.append(f"\n<details><summary>World doc: {c['world']}</summary>\n\n"
                     f"{worlds[c['world']]}\n\n</details>\n")
        parts.append(f"\n**LABEL (FM1 / FM2 / other):** _____\n")
    return "".join(parts)


def summarize_labels(labels):
    df = pd.DataFrame(list(labels))
    total = len(df)
    out = (df.groupby("label").size().rename("count").reset_index())
    out["frac"] = out["count"] / total if total else 0.0
    return out
