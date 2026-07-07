# Limited Verification Can Hurt Debate Oversight — Preliminary Findings

**Study:** Selvarath debate-oversight experiment · **Status:** preliminary (re-analysis of existing pilot data)
**Date:** 2026-07-06 · **Contributors:** J. Marcellino (pilot design & data), J. Maiorino

Interactive dashboard: [`reports/findings-dashboard.html`](findings-dashboard.html) (open in a browser).
Underlying analysis: [`analysis/`](../analysis) · generated tables in [`analysis/output/`](../analysis/output).

---

## ⚠️ Correction (2026-07-06): harness bugs invalidate the mechanism conclusions

A subsequent code audit found the pilot's **oracle pipeline was broken for ~100% of calls**: every oracle "NOT ADDRESSED" reply was silently miscoded to "NO" (across 5,733 exchanges the distribution is {YES 4546, NO 1187, **NOT ADDRESSED 0**}), and **99.8%** of oracle queries were sent as garbled doubled questions ("*Is it supported by the text that Is it stated in the text that …*"). Raw oracle prompts/replies and raw verdict text were never logged.

- **Survives** (as descriptive facts about the *buggy* pilot): the win-rate table (§4.1), the discordance counts (§4.5), and the side-symmetry result **only** as "not the default-to-Position-B parse fallback" — those two bugs are naturally side-symmetric, so §4.2 does *not* clear them.
- **Resets to unknown** pending a fixed re-run: the general claim *"limited verification hurts debate oversight"* (the intervention was verification *through a corrupted interface*, not verification itself); the **mechanism split** (§4.3 — a postmortem of the corrupted harness, since labelers scored normalized/garbled exchanges, not what the oracle actually saw); the **deep-myopia share**; and the **confidence-rises interpretation** (§4.4).
- **Only clean claim right now:** *in the original pilot implementation, adding a few oracle calls worsened the 70B judge's accuracy.*
- **Fix path:** a fixed-harness re-judge of the existing 318 transcripts (correct NOT-ADDRESSED handling, fixed query composition, strict verdict parsing, A/B fixed across budgets, full raw logging) plus a bug-factorial replay arm, to measure how much of Δfew and the mechanism survive the fixes — *before* any capability-grid spend.
- **Status update (2026-07-07):** the re-judge experiment is now designed and specced — see [`docs/superpowers/specs/2026-07-07-rejudge-harness-design.md`](../docs/superpowers/specs/2026-07-07-rejudge-harness-design.md) and the frozen pre-run protocol `docs/rejudge-protocol.md`.

The sections below are the **pre-audit** analysis, retained for the record.

---

## Summary

We re-analyzed the pilot data for *debate as scalable oversight* — an honest and a dishonest debater argue a factual question before a weaker judge that cannot see the source document and may spend a limited budget of yes/no **oracle** verification calls. We reproduced the pilot's headline table exactly and asked whether its counterintuitive result — **a few oracle calls make oversight worse than none** — is real, and if so, why.

- **The effect is real for a strong judge.** For the 70B judge, the dishonest-debater win rate rises from **1.9% (0 calls) to 9.4% (2 calls)** before partly recovering. The pre-specified contrast is **Δfew = +7.2 pp, 95% CI [4.6, 10.2]** (question-cluster bootstrap), positive in both correct-side strata.
- **It is not the verdict-parse artifact.** The inflation is symmetric across which side is correct, ruling out the known default-to-Position-B parsing bug. *(Pre-audit scope: the two oracle-channel bugs are side-symmetric, so this check does NOT clear them — see Correction.)* (The weak 8B judge *is* contaminated by a side bias and is treated as secondary.)
- *(Pre-audit; reset to unknown — see Correction.)* The mechanism split measured on the corrupted oracle channel was: ~24% direct oracle-answer errors, ~26% malformed judge queries, ~46% over-updating on confirmations the labelers scored as correct. None of these shares survive the audit.
- **Total spend: $0** (re-analysis of already-collected data). All code is test-covered.

---

## 1. Background & question

The pilot ([Marcellino, "Limited verification can hurt debate oversight"](https://www.lesswrong.com/posts/2a3vce7WooJ4XkDqw/limited-verification-can-hurt-debate-oversight)) engineers a **knowledge asymmetry**: two Llama-3.3-70B debaters (one honest, one dishonest) see a hand-authored fictional-world document; the judge does not and may learn facts only by submitting yes/no claims to an **oracle**, up to a per-debate budget. The metric is the **dishonest-debater win rate** (judge picks the wrong side; lower is better). The pilot swept budgets {0, 1, 2, 5} for two judges (Llama-3.3-70B, "70B", and Llama-3-8B, "8B"), with a small 20-call cell for the 70B judge, and reported a non-monotonic ("U-shaped") curve. This report determines whether that dip is real and decomposes its causes.

## 2. Data & reproduction

The raw pilot data (`data/judgments.jsonl`, 2,583 judgments; `data/transcripts.jsonl`, 318 transcripts across 3 worlds and 106 questions) was recovered and re-analyzed. Our recomputation reproduces the published win-rate table to the decimal, confirming the analysis operates on the genuine pilot output.

## 3. Analysis (pre-specified before recomputation)

- **Population:** 70B judge, budgets {0, 1, 2, 5} (budget 20 exploratory, n=39).
- **Outcome:** judge picks the wrong side. **Design:** the same 318 transcripts are re-judged at each budget → a paired/repeated-measures structure.
- **Contrasts (percentage points):** `Δfew = ½[p(1)+p(2)] − p(0)`; `Δrecover5 = ½[p(1)+p(2)] − p(5)`, overall and within each correct-side stratum.
- **Uncertainty:** question-cluster bootstrap (resample the 106 questions, B=10,000, seeded).
- **Gate to "bank" the harm claim:** Δfew > 0, cluster-bootstrap 95% CI excludes 0 with lower bound ≳ +2 pp, positive in both correct-side strata, and surviving a bounded parse-sensitivity check.

> **Framing note (2026-07-07):** these contrasts and the gate were fixed *after* the session had already recomputed the published table and seen the qualitative U-shape on this same data (recorded in the design spec §1). They are pre-specified relative to the confirmatory recomputation, but post-hoc relative to first look. "Pre-registered" is reserved for the re-judge gates in `docs/rejudge-protocol.md`, which are genuinely ex-ante.

## 4. Results

### 4.1 Limited verification hurts (the U-shape)

Dishonest-debater win rate (%), Wilson 95% CI, by judge and oracle budget:

| Judge | 0 | 1 | 2 | 5 | 20 |
|---|---|---|---|---|---|
| **70B** | 1.9 [0.9, 4.1] | 8.8 [6.2, 12.4] | **9.4 [6.7, 13.1]** | 5.3 [3.4, 8.4] | 0.0 [0.0, 9.0] (n=39) |
| **8B** | 18.6 [14.7, 23.2] | 21.4 [17.2, 26.2] | **26.1 [21.6, 31.2]** | 23.0 [18.7, 27.9] | — |

Pre-specified result (70B): **Δfew = +7.23 pp, cluster-bootstrap 95% CI [4.56, 10.22]**, positive in both strata (A ≈ +8.2, B ≈ +6.2). **Gate: BANKED.** `Δrecover5 = +3.8 pp` → only *partial* recovery by budget 5; the 20-call "recovery to 0%" is **not** claimable (n=39, CI reaches ~9%).

### 4.2 Not the verdict-parse artifact (does not clear the oracle-channel bugs)

The pilot silently defaulted an unparseable verdict to "Position B," which can only create errors when *A* is the correct side. Wrong-rate at budget 2, split by correct side:

| Judge | A correct | B correct | reading |
|---|---|---|---|
| **70B** | 9.9% | 9.0% | **symmetric → the parse bug is not driving it** |
| **8B** | 38.4% | 15.0% | strong Position-B bias → **contaminated, secondary only** |

The 70B inflation is symmetric across correct-side (and its format-noncompliance proxy is flat across budgets), so the effect is not driven by the verdict-parse fallback. The two oracle-channel bugs are themselves side-symmetric, so this check does **not** clear them (see Correction). (A *definitive* parse audit requires re-running with instrumented logging — a follow-up.)

### 4.3 Mechanism: why the judge flips *(pre-audit — labels scored the corrupted oracle channel; retained for the record, reset to unknown)*

The 54 transcripts that flipped **correct@0 → wrong@{1,2}** (70B) were classified by **two independent, blind LLM labelers** (Claude Sonnet, one per world, each grounded in the source document and required to quote evidence). Inter-rater agreement: **44/54 = 81.5%, Cohen's κ = 0.72** (substantial). Refined taxonomy:

| Code | Failure mode | Share | Fixable by |
|---|---|---|---|
| **O1** | Oracle gave a *wrong* answer vs the text | ~24% | a better oracle |
| **Q1** | The judge's *query* was malformed / compound | ~26% | constrained / decomposed query format |
| **R1** | Judge over-trusted a true-but-*irrelevant* confirmation | ~31% | *not oracle-fixable (deep myopia)* |
| **R2** | Judge over-penalized a *real* honest-side gap | ~15% | *not oracle-fixable (deep myopia)* |
| **M1** | Ambiguous | ~4% | — |

**≈ 50% is protocol-fixable (O1 + Q1); ≈ 46% is deep judge over-updating (R1 + R2).** The defensible headline is narrow: *only about a quarter of harmful flips are direct oracle-answer errors.* Cluster-weighted, oracle errors span only ~8–10 distinct questions (one recurring error template on question CN-003 alone accounts for 4 flips), so the per-flip O1 rate overstates independent prevalence. Net-aware: the 8 reverse (beneficial) flips were 7 cases of the oracle *correctly* helping and 1 stochastic — oracle errors cause harm but not benefit, so there is no offset.

### 4.4 Confidence goes the wrong way *(pre-audit, and near-degenerate)*

For the 70B judge, mean self-reported confidence **rises** with budget (4.09 → 4.49 → 4.58 → 4.70) — i.e., the judge becomes *more* confident at exactly the budgets where it becomes *more* wrong. Partial verification produces overconfidence, not calibration. (Secondary; confidence is self-reported and post-treatment.) The confidence distribution is also nearly degenerate — 4 in 1,695/2,583 rows, 5 in 887, 3 exactly once — so the "rise" is a shift from 4s to 5s, not a calibrated signal.

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
- **Turn-count confound:** budget>0 judgments insert extra conversation turns (query prompt, judge reply, oracle result — per query) before the verdict; budget-0 goes straight to verdict. A surviving clean-harness Δfew could therefore reflect deliberation/turn effects rather than verification content; the re-judge PLACEBO arm isolates this.
- **Budget-20 cell:** not a small random subsample but a truncated, single-world partial run — non-representative, not merely underpowered.

## 6. Bottom line

What survives the harness audit is narrow: **in the original pilot implementation, adding a few oracle calls worsened the 70B judge's accuracy** (Δfew = +7.2 pp, CI [4.6, 10.2]). Whether that is a fact about *limited verification*, about *two specific harness bugs*, or about *extra deliberation turns* is exactly what the fixed-harness re-judge (CLEAN / bug-replay / PLACEBO arms, pre-specified gates in `docs/rejudge-protocol.md`) will decide. The mechanism split and the confidence trend are pre-audit observations about the corrupted pipeline and carry no interpretive weight until re-measured.

## 7. Next steps (to be detailed separately)

The next experiment is the **fixed-harness re-judge** of the same 318 transcripts (design: `docs/superpowers/specs/2026-07-07-rejudge-harness-design.md`; frozen protocol & gates: `docs/rejudge-protocol.md`): CLEAN {0,1,2,5}, bug-replay BOTH {1,2,5}, PLACEBO {1,2,5}, single-bug arms {1,2}, K=2 replicates, plus a legacy QA-replay subset. The open-source judge × debater capability grid (Llama/Qwen/Gemma ladders) remains designed but **gated** on the re-judge outcome and inherits the fixed harness.

## 8. Artifacts & reproducibility

- **Report figures:** [`reports/findings-dashboard.html`](findings-dashboard.html) (self-contained, interactive).
- **Generated outputs:** `analysis/output/report.md` (auto gate report), `mechanism_validation.md`, `proxy_calibration.md`, `mechanism_labels.md`, `mechanism_cases.md`, `labels.csv`, `labels_pass2.csv`.
- **Code:** `analysis/` (loader, descriptives, inference, parse-sensitivity, mechanism, robustness, report) with tests in `tests/`; run `uv run pytest` and `uv run python -m analysis.run_report`.
- **Design & plan:** `docs/superpowers/specs/` and `docs/superpowers/plans/`.
