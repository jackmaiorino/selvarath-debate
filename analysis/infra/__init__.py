"""Infrastructure for the capability-gap experiment (write-now, run-on-budget).

Pure, unit-tested logic that fixes the pilot harness's known problems before any
paid run:
- `parsing.py`  — STRICT verdict parsing (no silent default-to-Position-B; INVALID
  instead) and robust oracle normalization (INVALID for un-committed replies).
- `design.py`   — A/B labels held FIXED across budgets, the judge×debater×budget
  grid, a model registry (fill exact provider strings at run time), and the
  capability-axis solo-accuracy scorer.

The actual inference calls live in a runner that imports these; running it spends
money and is gated on explicit budget approval (see spend-policy).
"""
