"""Robustness checks: leave-one-world-out on Δfew and per-transcript discordance."""
from __future__ import annotations

import pandas as pd

from analysis.inference import PRIMARY_BUDGETS, point_estimate


def leave_one_world_out(df, judge, budgets=PRIMARY_BUDGETS):
    out = [{"dropped": "none", "delta_few_pp": point_estimate(df, judge, "few", budgets)}]
    for w in sorted(x for x in df.world.dropna().unique()):
        out.append({"dropped": w,
                    "delta_few_pp": point_estimate(df[df.world != w], judge, "few", budgets)})
    return pd.DataFrame(out)


def discordance(df, judge, base_budget=0, flip_budgets=(1, 2)):
    sub = df[df.judge_short == judge]
    base = (sub[sub.query_budget == base_budget]
            .set_index(["question_id", "transcript_index"])["verdict_correct"])
    out = []
    for fb in flip_budgets:
        cur = (sub[sub.query_budget == fb]
               .set_index(["question_id", "transcript_index"])["verdict_correct"])
        j = pd.DataFrame({"base": base, "flip": cur}).dropna().astype(bool)
        c2w = int((j.base & ~j.flip).sum())
        w2c = int((~j.base & j.flip).sum())
        out.append({"flip_budget": fb, "correct_to_wrong": c2w, "wrong_to_correct": w2c,
                    "net_new_errors": c2w - w2c, "n_transcripts": len(j)})
    return pd.DataFrame(out)
