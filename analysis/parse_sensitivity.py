"""Bounded parse-fallback sensitivity for Δfew.

The pilot never logged raw verdict text, so a definitive parse audit is impossible
here (that is deliverable C). We approximate: the clean parser writes only the
REASONING: line into `reasoning`; leaked VERDICT:/CONFIDENCE: tokens signal the
model broke format and thus had a higher chance of hitting the silent
default-to-Position-B fallback. We recompute Δfew treating those rows four ways.
"""
from __future__ import annotations

import numpy as np

from analysis.inference import (PRIMARY_BUDGETS, _count_matrices, _p_from_sums, _stat)


def suspected_fallback(row):
    r = (getattr(row, "reasoning", "") or "").upper()
    return ("VERDICT:" in r) or ("CONFIDENCE:" in r)


def flag(df):
    df = df.copy()
    df["suspect"] = [suspected_fallback(r) for r in df.itertuples(index=False)]
    return df


def _delta_few_pp(df, judge, budgets):
    _, W, N = _count_matrices(df, judge, budgets)
    p = _p_from_sums(W.sum(0), N.sum(0))
    return float(_stat(p, budgets, "few")) * 100


def delta_few_under_treatments(df, judge, budgets=PRIMARY_BUDGETS):
    df = flag(df)
    wcol = df.columns.get_loc("wrong")
    treatments = {"baseline": _delta_few_pp(df, judge, budgets),
                  "exclude": _delta_few_pp(df[~df.suspect], judge, budgets)}

    d = df.copy(); d.loc[d.suspect, "wrong"] = True
    treatments["suspect_wrong"] = _delta_few_pp(d, judge, budgets)

    d = df.copy(); d.loc[d.suspect, "wrong"] = False
    treatments["suspect_correct"] = _delta_few_pp(d, judge, budgets)

    d = df.copy()
    idx = np.where(d.suspect.values)[0]
    coin = np.random.default_rng(0).random(len(idx)) < 0.5
    d.iloc[idx[coin], wcol] = True
    d.iloc[idx[~coin], wcol] = False
    treatments["suspect_5050"] = _delta_few_pp(d, judge, budgets)
    return treatments
