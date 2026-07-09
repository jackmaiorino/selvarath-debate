# Stage 1 Re-Judge Results: The Harm Is Real, But Half of It Was Bugs

**Study:** Selvarath debate-oversight, fixed-harness re-judge · **Status:** complete, pre-registered
**Date:** 2026-07-09 · **Contributors:** J. Marcellino (pilot design and data), J. Maiorino
**Pre-registration:** [`docs/rejudge-protocol.md`](../docs/rejudge-protocol.md) (gates frozen before any clean data; Amendment 1 frozen before the K=3 escalation) · **Spend:** $182.16 · **Records:** 11,330 judgments

## Summary

We re-judged the pilot's 318 debate transcripts under six controlled arms to decompose the pilot's
headline result (+7.2pp harm from a small oracle budget) into real effect, harness bugs, and
deliberation burden.

1. **The clean harness preserves a smaller but statistically positive harm signal:
   Δfew = +3.41pp, 95% CI [+1.31, +5.87].** The pilot overstated the effect roughly 2x.
2. **The two harness bugs contributed about half the pilot's headline** (BOTH−CLEAN = +3.51pp,
   CI [+1.55, +5.60], about 49% of +7.2). This formally trips the pre-registered attribution
   threshold (≥3.5pp, cleared by 0.01pp), so under the frozen rule the pilot headline is labeled
   "mostly harness-induced"; substantively the split is half-and-half. Nearly all of the bug
   contribution came from the NOT-ADDRESSED-to-NO miscoding (+2.25pp, CI [+0.91, +3.72]); the
   garbled doubled queries were benign (+0.84pp, CI [−0.47, +2.22]).
3. **Extra deliberation turns alone cause a small real degradation** (placebo vs budget-0:
   +1.62pp, CI [+0.26, +3.30]). The clean-vs-placebo contrast (+1.79pp, CI [−0.15, +3.99]) is not
   individually significant, so whether the remaining clean effect is verification content or
   deliberation burden is unresolved. This is the central question for phase 2.
4. **The clean harness does not reproduce the pilot's U-shaped recovery.** Clean Δrecover5 =
   −0.99pp (CI includes 0): harm at budget 5 is as high as at budget 2 in the clean arm, while the
   bug-replay arm does recover (8.49% at b2 to 5.03% at b5).
5. **The original ≥4pp gate is indeterminate, not passed.** Under pre-committed Amendment 1
   (Δfew ≥ 3pp, CI lower bound > 0, clean−placebo positive), the outcome licenses a
   **reduced-scope phase-2 capability pilot**, not the full grid.

## Design

Six arms over the same 318 transcripts (106 questions, 3 worlds), judge = oracle =
Llama-3.3-70B-Instruct-Turbo, K=3 replicates for CLEAN and PLACEBO (K=2 others, K=1 legacy):

| Arm | Purpose |
|---|---|
| CLEAN {0,1,2,5} | bugs fixed; carries the primary gate |
| BOTH {1,2,5} | both oracle bugs faithfully re-injected; paired attribution vs CLEAN |
| PLACEBO {1,2,5} | full turn structure, information-free oracle result; deliberation control |
| NA_ONLY / DOUBLED_ONLY {1,2} | single-bug decomposition |
| LEGACY {1,2} | pilot replay incl. per-budget A/B and pilot parser; fidelity QA only |

Every verdict was parsed with both the hardened strict parser (primary; INVALID excluded and
reported) and a bug-for-bug port of the pilot parser. All contrasts use a question-cluster
bootstrap (B=10,000, seeded).

## Results

Dishonest-debater win rate (%), strict parse:

| Arm | b0 | b1 | b2 | b5 |
|---|---|---|---|---|
| clean (n=954/cell; b2 n=953 after the single INVALID exclusion) | 1.26 | 3.25 | 6.09 | 5.66 |
| both (n=636) | | 7.86 | 8.49 | 5.03 |
| placebo (n=954) | | 3.14 | 2.62 | 3.56 |
| na_only (n=636) | | 5.97 | 7.86 | |
| doubled_only (n=636) | | 4.25 | 6.76 | |

Raw wrong counts (clean, of attempted): 12/954 at b0, 31/954 at b1, 58/954 at b2 (rate computed
over 953 valid), 54/954 at b5.

Gates (pre-registered):

| Gate | Result | Verdict |
|---|---|---|
| Primary: clean Δfew ≥ 4pp, CI excl. 0 | +3.41 [+1.31, +5.87] | **Indeterminate** (real, below the bar) |
| Attribution: BOTH−CLEAN ≥ 3.5pp or >50% of +7.2 | +3.51 [+1.55, +5.60], ~49% | **Fired (by 0.01pp): "mostly harness-induced"; substantively half-and-half** |
| Deliberation: placebo ≈ clean and placebo−p(0) ≥ 4pp | placebo−p(0) = +1.62 [+0.26, +3.30] | **Not a pure deliberation story** |
| Amendment 1 (frozen pre-K=3): Δfew ≥ 3, CI > 0, clean−placebo > 0 | 3.41 ✓, +1.31 ✓, +1.79 ✓ | **Passed: reduced-scope phase-2 pilot** |

Exploratory (clustered CIs): clean dose step b2−b1 = +2.84 [+0.32, +5.70], suggestive of harm
emerging after the second call and saturating; recovery interaction (BOTH minus CLEAN recovery)
= +3.03 [−0.48, +6.66], directionally supporting bug-driven recovery but not conclusive.

Quality and fidelity: 1 INVALID verdict of 11,330 (0.01%); 0 strict-vs-pilot side disagreements;
100% well-formed claims in clean-composer arms; queries_used parity clean 2.66 vs placebo 2.64
(so the placebo comparison is turn-count valid); LEGACY replay reproduced the pilot on the same
cells (16.0% vs 16.5%), validating the bug-replay machinery.

## What this changes from the pilot write-up

- "A few oracle calls hurt the judge" survives, at half the size: +3.4pp, not +7.2pp.
- The pilot's mechanism split (oracle errors vs judge over-updating) remains retracted; the
  taxonomy should be re-derived from clean-arm harmful flips if phase 2 proceeds.
- The U-shape's recovery leg is not supported under the clean harness.
- New finding the pilot could not see: merely inserting verification-style turns with no
  information degrades the judge slightly (+1.6pp).

## Next steps

Per Amendment 1: a reduced-scope judge x debater capability pilot (design in
`docs/superpowers/specs/`, pending sign-off), targeting two questions: does the clean harm shrink
as judge capability grows, and does the content-vs-deliberation split move with capability. Full
write-up of Stage 1 for LessWrong and the Manifund update precede any further spend.

## Reproducibility

`uv run python -m rejudge.analyze_stage1` over `rejudge/output/records.jsonl` (11,330 rows;
untracked, available on request). Protocol and amendment: `docs/rejudge-protocol.md`. Harness:
`rejudge/` at commit `7346bec`, 106 offline tests.
