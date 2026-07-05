"""Primary inference: pre-registered contrasts with question-cluster bootstrap CIs.

Δfew      = 1/2 [p(1) + p(2)] - p(0)
Δrecover5 = 1/2 [p(1) + p(2)] - p(5)
p(b) = judge-wrong rate at oracle budget b. Values reported in percentage points.
"""
from __future__ import annotations

import numpy as np

PRIMARY_BUDGETS = [0, 1, 2, 5]


def _count_matrices(df, judge, budgets, correct_side=None):
    sub = df[df.judge_short == judge]
    if correct_side is not None:
        sub = sub[sub.correct_side == correct_side]
    qids = sorted(sub.question_id.unique())
    qindex = {q: i for i, q in enumerate(qids)}
    bindex = {b: j for j, b in enumerate(budgets)}
    W = np.zeros((len(qids), len(budgets)))
    N = np.zeros((len(qids), len(budgets)))
    for r in sub.itertuples(index=False):
        if r.query_budget not in bindex:
            continue
        i, j = qindex[r.question_id], bindex[r.query_budget]
        N[i, j] += 1
        if r.wrong:
            W[i, j] += 1
    return np.array(qids), W, N


def _p_from_sums(Wsum, Nsum):
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(Nsum > 0, Wsum / Nsum, np.nan)


def _stat(p, budgets, stat):
    b = {v: i for i, v in enumerate(budgets)}
    few = 0.5 * (p[b[1]] + p[b[2]]) - p[b[0]]
    if stat == "few":
        return few
    if stat == "recover5":
        return 0.5 * (p[b[1]] + p[b[2]]) - p[b[5]]
    raise ValueError(stat)


def point_estimate(df, judge, stat="few", budgets=PRIMARY_BUDGETS, correct_side=None):
    _, W, N = _count_matrices(df, judge, budgets, correct_side)
    p = _p_from_sums(W.sum(0), N.sum(0))
    return float(_stat(p, budgets, stat)) * 100


def cluster_bootstrap_ci(df, judge, stat="few", budgets=PRIMARY_BUDGETS,
                         correct_side=None, B=10000, seed=0, alpha=0.05):
    _, W, N = _count_matrices(df, judge, budgets, correct_side)
    Q = W.shape[0]
    rng = np.random.default_rng(seed)
    vals = np.empty(B)
    for it in range(B):
        idx = rng.integers(0, Q, Q)
        p = _p_from_sums(W[idx].sum(0), N[idx].sum(0))
        vals[it] = _stat(p, budgets, stat)
    lo = float(np.nanpercentile(vals, 100 * alpha / 2)) * 100
    hi = float(np.nanpercentile(vals, 100 * (1 - alpha / 2))) * 100
    return lo, hi


def summarize(df, judge, budgets=PRIMARY_BUDGETS, B=10000, seed=0):
    import pandas as pd
    rows = []
    for stat in ("few", "recover5"):
        for side in (None, "A", "B"):
            pt = point_estimate(df, judge, stat, budgets, side)
            lo, hi = cluster_bootstrap_ci(df, judge, stat, budgets, side, B, seed)
            rows.append({"stat": stat, "stratum": side or "overall",
                         "point_pp": pt, "ci_lo_pp": lo, "ci_hi_pp": hi})
    return pd.DataFrame(rows)
