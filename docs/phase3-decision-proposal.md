# Phase-3 decision proposal: the budget knob

**Status: DRAFT for owner and lead review. Not execution authorization. No spend approved.**
Prepared 2026-08-14. Companion to `docs/phase2-decision-proposal.md`; assumes phase-2 results
(`reports/2026-08-11-phase2-main-results.md`) as given.

## The question phase 3 answers

The project's deliverable is a knob: "giving the judge X oracle queries changes accuracy by Y."
Phase 2 measured exactly two points on that knob (0 and 2 queries) and found the second point
worse than the first (+3.9pp error, pre-registered). Phase 3 maps the curve: error(b) for
b in {0, 1, 2, 4, 8}, with pre-registered contrasts at every level, and tests whether the
curve's shape depends on judge capability, the strongest exploratory signal in phase 2.

Two honest possible outcomes, both useful: the curve turns downward somewhere (the knob has a
useful setting, and we name it), or it does not in this regime (verification budgets are not
a safety lever here, and the field should know). The pilot's "U-shaped recovery" claim is the
b4/b8 question; stage 1 could not reproduce it at small budgets, and no clean experiment has
tested it.

## Design

**Reuse the phase-2 transcripts verbatim.** The 492 uncapped blind debates (82 questions x 2
self-play debaters x 3 transcripts) were generated blind to the oracle: the budget only
affects the judge. Reusing them makes phase 3 a pure judge-side dose-response on literally
identical debates, removes debater variance between phases, and costs nothing to generate.

**Conditions: sequential_b for b in {0, 1, 2, 4, 8}.** Sequential clean oracle only: the
packaging and placebo decompositions were phase-2 questions and are settled or parked. b0 and
b2 are re-run under the phase-3 manifest rather than pooled from phase 2 (option A below).

**Judges: measured-capability roster, 6 to 7 models.** A 2026-08-14 availability check found
Together's serverless catalog no longer carries any 3+ size same-generation family ladder
(Qwen2.5 is down to 7B only; Llama 3.x to 8B-Lite and 70B; Qwen3 dense and Mistral are
dedicated-only). The design therefore drops the size-ladder mechanism and keeps what the
capability axis actually requires: judges spanning a wide range of MEASURED capability.
Roster: the four calibration-passed phase-2 judges (Qwen2.5-7B, gemma-4-31B, Llama-3.3-70B,
gpt-oss-120b; all currently low deprecation risk) plus Llama-3-8B-Lite and gemma-3n-E4B,
which form two genuine within-family two-point pairs, plus Qwen3.7-Max as a high-capability point
under a pre-declared inclusion rule (it joins if and only if it passes the calibration gates
and its per-cell cost projection keeps total spend under the stage cap; "optional" is not
left open past roster freeze). New judges pass the same pre-declared calibration gates
(parseability, side bias) before roster freeze; any that fail are replaced or dropped,
recorded before outcomes. Every judge gets the frozen solo-QA capability anchor (all 106 questions, K2,
scored before any phase-3 outcome exists). Capability enters the confirmatory analysis as a
pre-frozen binary tier split on the anchor median, never as parameter count: phase-2
calibration showed judging skill does not track size (gpt-oss-120b judged far worse than
Llama-70B). Within-family pairs and the continuous anchor score stay exploratory.

**Primary family (consult-reviewed 2026-08-14):** the four budget contrasts
Delta(b) = error(sequential_b) - error(b0) for b in {1, 2, 4, 8}, two-sided, Holm over four,
reported as the full vector with simultaneous confidence intervals: the knob deliverable is
exactly "at 1, 2, 4, 8 queries, error changes by this much versus none." No slope or
isotonic primary: a slope assumes the shape the experiment exists to discover. A trend test
may ride as a pre-registered secondary. The estimand is intention-to-treat: the effect of
GRANTING a budget under the frozen adaptive stopping policy; queries_used is a post-treatment
variable and stays exploratory.

**Secondary family (revised per consult: a tier interaction is not confirmable with 7
judges):** ONE pre-registered interaction of the Delta(8) contrast with the judge's
CONTINUOUS frozen anchor score. The binary tier split becomes descriptive. The capability
threshold and roster freeze BEFORE any new judge's anchor is scored, and the moderation
anchor uses the 24 held-out calibration-excluded questions (or pre-registered cross-fitting)
so anchor items never overlap the 82 outcome questions. Stated plainly in the proposal: with
roughly 7 judges, capability moderation is at best weakly powered; the confirmatory weight of
phase 3 is the dose-response curve, and capability stays honestly labeled.

Same inference machinery as phase 2: world-stratified question bootstrap resampling whole
question clusters (every budget, judge, debater, transcript, and replicate travels with its
question), common draws, pins fixed before unblinding. Execution randomizes arm order within
question, all judge calls stateless, caching and gate-review dedup documented. Claims are
conditional on the three worlds, the frozen transcript set, and this judge roster; the
interpretation is the total effect of granting sequential oracle access, which phase 3 cannot
decompose into information versus interaction (that was phase 2's packaging question).

**What is deliberately NOT in phase 3:** a fourth world (the conditional-on-three-worlds
limitation stands; corpus construction is a project of its own), capped-debate arms (caps are
a settled phase-2 result; the task stays the uncapped phase-2 task for continuity), and any
debater-side variation.

## Cost and timeline

Judgment cells: 82 x 7 judges x 2 debaters x 3 transcripts x K2 x 5 budgets = 34,440, plus
about 1,000 capability-anchor cells for new judges. At phase-2 per-cell costs, provider spend
lands near **$220-380** (b4/b8 cells carry more oracle calls and longer judge contexts).

The binding constraint is not money but **gate-review throughput**. At b2, judges exhausted
the budget in 94% of cells, so review volume scales roughly linearly with budget. Phase 2
consumed about 13,700 reviewer rulings; phase 3's grid is plausibly 2-4x that, against a
reviewer quota that refreshes nightly. Per the consult, the fallback is PRE-REGISTERED NOW,
not improvised at the canary: the canary measures unique-payload review volume per cell per
budget (a blinded operational metric, never efficacy); if projected total reviews exceed a
stated ceiling, the pre-declared fallback is subsampling the b4 and b8 arms to a
pre-registered question subset, with the paired b0 observations analyzed on the same subset
for those contrasts. b1 is not dropped: it is the most informative low-dose point and cheap.
The exact ceiling and subset rule go in the frozen protocol. A paid-reviewer path remains an
owner decision if even the fallback grid exceeds quota.

**Pre-freeze gate: blinded power simulation.** The phase-2 question-level covariance (real
per-question error structure, no phase-3 outcomes involved) drives simulations of plausible
curves before the grid locks. Known from phase 2: SE(Delta) is near 0.9pp, so a 1pp effect
at b1 is unlikely to survive four-way multiplicity; the simulation makes that precise and
informs whether the proposal's power claims are honest. Results append to this document
before freeze. Timeline estimate: design freeze and consult 2-3 days,
capability anchors and canary 2-3 days, main run 3-10 days dominated by review throughput,
analysis 1 day under pre-fixed pins. **Roughly 1.5 to 3 weeks end to end.**

## Options requiring a decision

- **A (recommended): re-run b0/b2 under the phase-3 manifest** (~$60 of the total) for single-
  manifest provenance, versus pooling phase-2 rows across harness versions with a pre-registered
  bridge rule.
- **B: roster size.** 7 judges as sketched, versus 5 (one ladder plus anchors) to cut review
  volume by 30%, at the cost of a weaker capability axis.
- **C: reviewer throughput fallback** if the canary measures review volume beyond quota:
  subsample high budgets, or approve paid reviewing.

## Process commitments (unchanged from phase 2)

Design frozen and posted before launch; owner-authorized manifest and execution identity;
hash-chained stores; spend caps with the amended supervisor controls; analysis pins fixed and
independently reviewed before unblinding; the analysis runs once; every deviation recorded as
an append-only amendment.
