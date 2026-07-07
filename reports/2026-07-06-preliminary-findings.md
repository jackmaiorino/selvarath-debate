# Limited Verification Can Hurt Debate Oversight — Preliminary Findings

**Study:** Selvarath debate-oversight experiment · **Status:** preliminary (re-analysis of existing pilot data)
**Date:** 2026-07-06 · **Contributors:** J. Marcellino (pilot design & data), J. Maiorino

Interactive dashboard: [`reports/findings-dashboard.html`](findings-dashboard.html) (open in a browser).
Underlying analysis: [`analysis/`](../analysis) · generated tables in [`analysis/output/`](../analysis/output).

---

## Summary

We re-analyzed the pilot data for *debate as scalable oversight* — an honest and a dishonest debater argue a factual question before a weaker judge that cannot see the source document and may spend a limited budget of yes/no **oracle** verification calls. We reproduced the pilot's headline table exactly and asked whether its counterintuitive result — **a few oracle calls make oversight worse than none** — is real, and if so, why.

- **The effect is real for a strong judge.** For the 70B judge, the dishonest-debater win rate rises from **1.9% (0 calls) to 9.4% (2 calls)** before partly recovering. The pre-registered contrast is **Δfew = +7.2 pp, 95% CI [4.6, 10.2]** (question-cluster bootstrap), positive in both correct-side strata.
- **It is not a measurement artifact.** The inflation is symmetric across which side is correct, ruling out a known verdict-parsing bug. (The weak 8B judge *is* contaminated by a side bias and is treated as secondary.)
- **It is not mostly a simple oracle bug.** Across 54 harmful flips, only ~24% are direct oracle-answer errors; a comparable share are malformed judge *queries*; and **~46% are the judge over-updating on *correct* verification** — a reasoning failure a better oracle will not fix.
- **Total spend: $0** (re-analysis of already-collected data). All code is test-covered.

---

## 1. Background & question

The pilot ([Marcellino, "Limited verification can hurt debate oversight"](https://www.lesswrong.com/posts/2a3vce7WooJ4XkDqw/limited-verification-can-hurt-debate-oversight)) engineers a **knowledge asymmetry**: two Llama-3.3-70B debaters (one honest, one dishonest) see a hand-authored fictional-world document; the judge does not and may learn facts only by submitting yes/no claims to an **oracle**, up to a per-debate budget. The metric is the **dishonest-debater win rate** (judge picks the wrong side; lower is better). The pilot swept budgets {0, 1, 2, 5} for two judges (Llama-3.3-70B, "70B", and Llama-3-8B, "8B"), with a small 20-call cell for the 70B judge, and reported a non-monotonic ("U-shaped") curve. This report determines whether that dip is real and decomposes its causes.

## 2. Data & reproduction

The raw pilot data (`data/judgments.jsonl`, 2,583 judgments; `data/transcripts.jsonl`, 318 transcripts across 3 worlds and 106 questions) was recovered and re-analyzed. Our recomputation reproduces the published win-rate table to the decimal, confirming the analysis operates on the genuine pilot output.

## 3. Analysis (pre-registered)

- **Population:** 70B judge, budgets {0, 1, 2, 5} (budget 20 exploratory, n=39).
- **Outcome:** judge picks the wrong side. **Design:** the same 318 transcripts are re-judged at each budget → a paired/repeated-measures structure.
- **Contrasts (percentage points):** `Δfew = ½[p(1)+p(2)] − p(0)`; `Δrecover5 = ½[p(1)+p(2)] − p(5)`, overall and within each correct-side stratum.
- **Uncertainty:** question-cluster bootstrap (resample the 106 questions, B=10,000, seeded).
- **Gate to "bank" the harm claim:** Δfew > 0, cluster-bootstrap 95% CI excludes 0 with lower bound ≳ +2 pp, positive in both correct-side strata, and surviving a bounded parse-sensitivity check.

## 4. Results

### 4.1 Limited verification hurts (the U-shape)

Dishonest-debater win rate (%), Wilson 95% CI, by judge and oracle budget:

| Judge | 0 | 1 | 2 | 5 | 20 |
|---|---|---|---|---|---|
| **70B** | 1.9 [0.9, 4.1] | 8.8 [6.2, 12.4] | **9.4 [6.7, 13.1]** | 5.3 [3.4, 8.4] | 0.0 [0.0, 9.0] (n=39) |
| **8B** | 18.6 [14.7, 23.2] | 21.4 [17.2, 26.2] | **26.1 [21.6, 31.2]** | 23.0 [18.7, 27.9] | — |

Pre-registered result (70B): **Δfew = +7.23 pp, cluster-bootstrap 95% CI [4.56, 10.22]**, positive in both strata (A ≈ +8.2, B ≈ +6.2). **Gate: BANKED.** `Δrecover5 = +3.8 pp` → only *partial* recovery by budget 5; the 20-call "recovery to 0%" is **not** claimable (n=39, CI reaches ~9%).

### 4.2 Not a measurement artifact

The pilot silently defaulted an unparseable verdict to "Position B," which can only create errors when *A* is the correct side. Wrong-rate at budget 2, split by correct side:

| Judge | A correct | B correct | reading |
|---|---|---|---|
| **70B** | 9.9% | 9.0% | **symmetric → the parse bug is not driving it** |
| **8B** | 38.4% | 15.0% | strong Position-B bias → **contaminated, secondary only** |

The 70B inflation is symmetric across correct-side (and its format-noncompliance proxy is flat across budgets), so the effect is a genuine change in judge decisions, not a parsing artifact. (A *definitive* parse audit requires re-running with instrumented logging — a follow-up.)

### 4.3 Mechanism: why the judge flips

The 54 transcripts that flipped **correct@0 → wrong@{1,2}** (70B) were classified by **two independent, blind LLM labelers** (Claude Sonnet, one per world, each grounded in the source document and required to quote evidence). Inter-rater agreement: **44/54 = 81.5%, Cohen's κ = 0.72** (substantial). Refined taxonomy:

| Code | Failure mode | Share | Fixable by |
|---|---|---|---|
| **O1** | Oracle gave a *wrong* answer vs the text | ~24% | a better oracle |
| **Q1** | The judge's *query* was malformed / compound | ~26% | constrained / decomposed query format |
| **R1** | Judge over-trusted a true-but-*irrelevant* confirmation | ~31% | *not oracle-fixable (deep myopia)* |
| **R2** | Judge over-penalized a *real* honest-side gap | ~15% | *not oracle-fixable (deep myopia)* |
| **M1** | Ambiguous | ~4% | — |

**≈ 50% is protocol-fixable (O1 + Q1); ≈ 46% is deep judge over-updating (R1 + R2).** The defensible headline is narrow: *only about a quarter of harmful flips are direct oracle-answer errors.* Cluster-weighted, oracle errors span only ~8–10 distinct questions (one recurring error template on question CN-003 alone accounts for 4 flips), so the per-flip O1 rate overstates independent prevalence. Net-aware: the 8 reverse (beneficial) flips were 7 cases of the oracle *correctly* helping and 1 stochastic — oracle errors cause harm but not benefit, so there is no offset.

### 4.4 Confidence goes the wrong way

For the 70B judge, mean self-reported confidence **rises** with budget (4.09 → 4.49 → 4.58 → 4.70) — i.e., the judge becomes *more* confident at exactly the budgets where it becomes *more* wrong. Partial verification produces overconfidence, not calibration. (Secondary; confidence is self-reported and post-treatment.)

### 4.5 The damage is broad, not a few outliers

Per-transcript verdict changes (70B), added by oracle access:

| Budget | correct → wrong | wrong → correct | net new errors |
|---|---|---|---|
| 1 | 27 | 5 | **+22** |
| 2 | 27 | 3 | **+24** |

Degradation is distributed across many transcripts, not driven by a handful of pathological cases (leave-one-world-out is stable).

## 5. Robustness & limitations

- **Parse-sensitivity is bounded, not definitive:** the pilot never logged raw verdict text, so the parse audit is a proxy (it flags ~0 suspect 70B rows). A definitive audit needs an instrumented re-run.
- **Mechanism labels are LLM-produced:** two independent blind passes agree at κ=0.72, but the O1↔Q1 boundary is a genuine gray zone (~10 cases) that warrants a human spot-check; an automatable proxy was calibrated but only reaches κ≈0.59 (in-sample) and over-attributes to O1/R1, so it is a *screen*, not a stand-alone labeler.
- **External validity:** one model family, three hand-authored worlds, one task style. These findings characterize *this pilot*, not debate-as-oversight in general.
- **8B judge:** unusable for the headline (Position-B side bias); reported as a secondary "weak judges are position-unstable" observation.

## 6. Bottom line

For a strong judge under a knowledge asymmetry, a *small* verification budget measurably **worsens** debate oversight, the effect is not a harness artifact, and it is only about half attributable to fixable oracle/query problems — the remainder is the judge **mis-using correct verification** (over-trusting irrelevant confirmations, over-penalizing real gaps). Whether that residual is a property of *weak* judges or of debate itself is the open question.

## 7. Next steps (to be detailed separately)

An **open-source judge × debater capability experiment** (Llama / Qwen / Gemma size ladders) to test whether the deep-myopia component shrinks as judge capability rises — disentangling capability *gap* from *absolute* capability. A cheap staged pilot (2×2 → 3×3 fractional grid) behind a hard identification/quality gate. Harness fixes for that run (strict verdict parsing with no silent default, A/B labels held fixed across budgets, the capability grid, and a calibrated mechanism screen) are already implemented under `analysis/infra/` and `analysis/mechanism_proxy.py`.

## 8. Artifacts & reproducibility

- **Report figures:** [`reports/findings-dashboard.html`](findings-dashboard.html) (self-contained, interactive).
- **Generated outputs:** `analysis/output/report.md` (auto gate report), `mechanism_validation.md`, `proxy_calibration.md`, `mechanism_labels.md`, `labels.csv`, `labels_pass2.csv`.
- **Code:** `analysis/` (loader, descriptives, inference, parse-sensitivity, mechanism, robustness, report) with tests in `tests/`; run `uv run pytest` (40 tests) and `uv run python -m analysis.run_report`.
- **Design & plan:** `docs/superpowers/specs/` and `docs/superpowers/plans/`.
