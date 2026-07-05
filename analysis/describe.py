"""Descriptive smoke tables — NOT primary inference."""
from __future__ import annotations

import math

import pandas as pd


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (100 * (center - half), 100 * (center + half))


def win_rate_table(df, judge):
    sub = df[df.judge_short == judge]
    out = []
    for b in sorted(sub.query_budget.unique()):
        cell = sub[sub.query_budget == b]
        n = len(cell); k = int(cell.wrong.sum())
        lo, hi = wilson_ci(k, n)
        out.append({"judge": judge, "budget": b, "n": n,
                    "wrong_pct": 100 * k / n if n else float("nan"),
                    "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(out)


def side_stratified_table(df, judge):
    sub = df[df.judge_short == judge]
    out = []
    for b in sorted(sub.query_budget.unique()):
        row = {"judge": judge, "budget": b}
        for side in ("A", "B"):
            cell = sub[(sub.query_budget == b) & (sub.correct_side == side)]
            n = len(cell); k = int(cell.wrong.sum())
            row[f"wrong_pct_{side}correct"] = (100 * k / n) if n else float("nan")
            row[f"n_{side}"] = n
        out.append(row)
    return pd.DataFrame(out)


def confidence_by_correctness(df, judge):
    sub = df[df.judge_short == judge]
    out = []
    for b in sorted(sub.query_budget.unique()):
        cell = sub[sub.query_budget == b]
        correct = cell[~cell.wrong]; wrong = cell[cell.wrong]
        out.append({"judge": judge, "budget": b,
                    "mean_conf": cell.confidence.mean(),
                    "mean_conf_correct": correct.confidence.mean() if len(correct) else float("nan"),
                    "mean_conf_wrong": wrong.confidence.mean() if len(wrong) else float("nan")})
    return pd.DataFrame(out)
