# Phase-2 main run: pre-registered results

**Run:** 22,140 cells (82 questions x 4 judges x 2 self-play debater models x 10 conditions), converged 2026-08-11, 100% completion, all eligibility gates passed at 0.0pp. **Design frozen and posted 2026-07-18** ([Manifund update](../docs/manifund-updates/2026-07-18-stage1-results-phase2-prelaunch.md), protocol commit `0a21191`). **Analysis:** every degree of freedom the frozen protocol left open was pinned before unblinding (`rejudge/phase2_analysis_pins_2026-08-11.json`, reviewed by an independent model consult), engine at commit `1a4c2f6`, run once. Spend: $172.86 against a $173.80 projection and a $400 cap.

## Topline

In these three fictional worlds, the pre-registered sequential two-call oracle protocol increased judge error by **3.9 percentage points** (CI [2.2, 5.7], Holm p = 0.0006). The total effect is robust to the valid-only sensitivity (+4.6pp). The allocation between content and packaging, and the mechanism, are not established.

## Primary family (Holm over three tests, alpha 0.05)

| Estimand | Definition | Estimate | 95% CI | Holm p | Valid-only |
|---|---|---|---|---|---|
| H | sequential_b2 vs b0 | +3.94pp | [+2.21, +5.67] | 0.0006 | +4.63 [+2.90, +6.39] |
| P | sequential_b2 vs batch replay | +1.37pp | [+0.08, +2.67] | 0.042 | +1.02 [-0.30, +2.34] |
| R | batch replay vs b0 | +2.57pp | [+0.56, +4.50] | 0.028 | +3.60 [+1.56, +5.59] |

Error rates: b0 17.3%, sequential_b2 21.2%, batch_same_qa_b2 19.8%. The identity H = P + R holds exactly in the point estimate and in every bootstrap replicate.

This is an algebraic decomposition, not a mechanistic one. The pre-registered contrasts attribute +2.6pp to the batch-versus-b0 content contrast and +1.4pp to sequential-versus-batch packaging. The packaging component is statistically detectable under the primary strict-INVALID rule but fragile across estimands: its pre-declared valid-only sensitivity crosses zero. The content component strengthens under the same sensitivity.

The strict-INVALID rule is conservative against the headline: 93 of 15,744 judgments (0.59%) were strict-INVALID, and they concentrate in the b0 baseline (46, vs 18 in sequential_b2 and 0 in the batch table format), mostly weak judges emitting a query or empty string instead of a verdict. Counting them wrong inflates baseline error and shrinks H; dropping them (valid-only) raises H.

## Secondary family (Holm over two tests)

**Cap-protection interaction C = +16.5pp** (CI [+10.6, +23.0], Holm p = 0.0004). At the Llama-3.3-70B judge, removing the 150-word cap had a 16.5pp larger adverse effect with the Qwen3.7-Plus self-play debater than with Llama-3.3-70B self-play. Simple effects: capped error 1.0% (Llama debater) and 2.0% (Qwen3.7-Plus); uncapped 2.9% and 20.3%. This is an interaction at one judge with two self-play debaters. It is not evidence that Qwen3.7-Plus is generally more deceptive, that caps help all judges, or a general capability result; model identity and family-specific behavior remain alternative explanations.

**Debate disadvantage D_clean = +9.7pp** (CI [+6.5, +12.9], Holm p = 0.0004). Under the matched two-call evidence presentation, adding debate increased error by 9.7pp. Descriptively, the matched zero-budget contrast is +11.3pp, so debate was substantially worse than evidence-only presentation and this disadvantage persisted with verification; the disadvantage is not created by verification.

## Exploratory (descriptive only, no tests, per the frozen spec)

Judge-stratified H point estimates were positive for the two weaker judges (Qwen2.5-7B roughly +8 to +10pp; gemma-4-31B +5 to +8.5pp) and near zero or negative for the two stronger judges (Llama-3.3-70B, gpt-oss-120b). This motivates, but does not establish, capability dependence; it is the natural phase-3 confirmatory question. The placebo condition's error (19.3%) was intermediate between b0 (17.3%) and sequential_b2 (21.2%), consistent with a substantial protocol-level contribution to the harm; this descriptive contrast does not establish the mechanism.

## Process integrity

A mid-run reviewer outage combined with a packet-cache bug fabricated 329 oracle-gate rulings. The contamination was measured (1,070 affected cells including text-level dependents), predeclared for invalidation before any replacement ruling was seen, surgically removed via the hash-chained stores' rebuild machinery, and every affected cell re-run under corrected rulings; the converged store was verified to reference no fabricated ruling. Re-review found roughly three quarters of the fabricated denials had wrongly blocked queries. Fourteen governance amendments and five incident records document the run end to end; the full audit trail is in `rejudge/`. All 22,140 cells derive from the owner-authorized execution identity `ba017fe3`.

## Limitations

The 82 questions sit across only three fictional worlds (27/28/27); the question bootstrap cannot express world-level dependence, so claims stay conditional on these three worlds. Judges and debaters are single pinned model versions; capability claims are exploratory. The oracle is Llama-3.3-70B with a frozen query-gate contract whose taxonomy missed two rare query shapes (4 occurrences in about 22,000 reviews, all dispositioned conservatively non-ALLOW under the frozen failure rule).

## Artifacts

`analysis_out/phase2_main_results.json` (full results with integrity block: input sha256, draw-matrix hash, engine commit, execution identity), `analysis_out/eligibility_report.md`, `rejudge/phase2_analysis_pins_2026-08-11.json`.
