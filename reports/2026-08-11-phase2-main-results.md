# Phase-2 main run: pre-registered results

**Run:** 22,140 cells (82 questions x 4 judges x 2 self-play debater models x 10 conditions), converged 2026-08-11, 100% completion. ~~all eligibility gates passed at 0.0pp~~ **(withdrawn 2026-08-21: see the erratum below; the side-bias gate as computed was non-diagnostic).** **Design frozen and posted 2026-07-18** ([Manifund update](../docs/manifund-updates/2026-07-18-stage1-results-phase2-prelaunch.md), protocol commit `0a21191`). **Analysis:** every degree of freedom the frozen protocol left open was pinned before unblinding (`rejudge/phase2_analysis_pins_2026-08-11.json`, reviewed by an independent model consult), engine at commit `1a4c2f6`, run once. Spend: $172.86 against a $173.80 projection and a $400 cap.

> **Erratum notice (2026-08-21):** a harness defect discovered by the phase-3 canary affects the presentation schedule behind these results. Per the frozen reanalysis, the headline H survives as a post-hoc-robust result at +4.56pp (post-hoc 50/50 side-standardized, strict rule); the fully-preregistered-mirrored framing and the eligibility-gate claim are withdrawn. Full erratum at the end of this report.

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

---

## Erratum (2026-08-21): K2 "mirrored sides" was never implemented

The frozen protocol required two mirrored A/B label assignments per judgment unit ("K2":
one judgment with the correct answer labeled Position A, one with it labeled Position B).
The execution instead generated two stochastic judgments with **identical** label order,
because the polarity function (`analysis/infra/design.position_a_is_correct`, consumed via
`rejudge/config.py:position_for`) takes only `(question_id, transcript_index)` -- no
side, replicate, judge, debater, or budget input. Both K2 slots of every judgment cell in
phase 2 (and, separately, phase 3) therefore presented the same rendered prompt: duplication,
not mirroring. Full forensic account: `rejudge/phase3_incident1_mirroring_2026-08-21.json`.

The reported phase-2 side-bias calibration gate **passed**. Recalculating the same gate
arithmetic on the realized (rendered) prompt positions, rather than the intended-side
metadata the reported computation grouped by, fails 3 of the 4 continuing judges: side-stratum
differences of 39.7pp (Qwen2.5-7B), 11.5pp (gemma-4-31B), and 10.84pp (Llama-3.3-70B) against
the frozen 10pp threshold, plus gpt-oss-120b's 4.17% strict-INVALID rate against the 2% gate.
These unpaired differences may combine true position preference with finite-sample question
difficulty and are not equivalent to the intended paired gate; they are not confirmed "real side
preferences" pending a corrected paired run. **We withdraw the statement that all phase-2
eligibility gates passed.**

There was no deception: the defect originated in the gate computation grouping by intended-side
metadata rather than realized rendered position, a distinction that was vacuous under
duplication. Transcripts (which had their own, correctly implemented counterbalanced opening
order) and the separately-implemented, verified-mirrored phase-3 capability anchors are
unaffected.

### Post-hoc mirror-robustness reanalysis

Because phase-2 outcomes are already unblinded, no true mirrored rerun is available for phase 2;
what follows is a **post-hoc robustness analysis**, specified and frozen
(`rejudge/phase2_mirror_reanalysis_spec_2026-08-21.json`) before any side-stratified or
standardized quantity was computed, and executed once
(`scripts/phase2_mirror_reanalysis.py` -> `analysis_out/phase2_mirror_reanalysis.json`).

**Polarity verification (hardened).** Realized assignment: of the 246 (question, transcript)
units, 128 are A-correct and 118 are B-correct; 63 of the 82 questions carry both polarities
across their three transcripts, 19 are single-polarity (all three transcripts on one side). This
was established by a DIRECT comparison, performed independently for every one of the 19,680
judgment rows, between the rendered POSITION A / POSITION B block text in the archived judge
prompts (`judge_messages` in `main_results.jsonl`) and the frozen question bank's own
correct-answer/wrong-answer text -- not by trusting the archive's stored
`position_a_is_correct` field or by re-deriving the polarity function and assuming agreement.
Result: 0 rows with a missing position block, 0 rows whose rendered text matched neither known
answer, 0 rows where the stored field disagreed with this direct comparison, and internal
consistency across every condition and both K2 replicates within every unit. Separately, the
complete banked H/P/R semantic artifact -- every estimate, CI, p-value, and integrity field --
was reproduced exactly from the raw archive, excluding the expected `engine_commit` provenance
field (which necessarily differs by run time and is not a claim about this script's committed
content; the artifact separately records this script's own content sha256 as the
commit-independent integrity binding, plus whether that content is already present at the
recorded git HEAD).

The original **+3.9pp** H estimate is the **realized-schedule** estimate: the mean effect over
these 82 questions under the one label assignment each happened to receive. It remains valid as
that quantity but is not the preregistered mirror-averaged estimand, because sides were never
balanced within a unit.

A **post-hoc 50/50 side-standardized** estimate (equal weight to the A-correct and B-correct
strata within each of the three worlds, an assumption-based approximation to the mirrored ideal,
not a unit-level counterfactual) gives:

| Estimand | Realized-schedule (strict) | Post-hoc 50/50 side-standardized (strict) | Post-hoc 50/50 side-standardized (valid-only) | 63-question within-question corroboration |
|---|---|---|---|---|
| H | +3.94pp [+2.24, +5.67] | **+4.56pp [+2.89, +6.25]** | +5.33pp [+3.58, +7.04] | +4.49pp [+2.65, +6.40] |
| P | +1.37pp [+0.10, +2.69] | +1.58pp [+0.14, +3.06] | +1.18pp [-0.36, +2.75] | +1.66pp [+0.03, +3.31] |
| R | +2.57pp [+0.56, +4.50] | +2.98pp [+0.89, +4.98] | +4.14pp [+1.94, +6.21] | +2.83pp [+0.52, +5.00] |

Every CI above represents question-cluster bootstrap uncertainty under the *observed* label
assignment and, for the standardized/interaction columns, under the post-stratification
(equal-side, equal-world) reweighting assumption. **It is not uncertainty over the missing
opposite-polarity potential outcomes**: no unit in this archive was ever judged under both
labels, so no CI here reflects a true mirrored-design sampling distribution.

The A-minus-B side-by-condition contrast is large and statistically distinguishable from zero:
**H interaction -6.24pp [-9.37, -3.18]** (strict), concentrated in two judges (Qwen2.5-7B and
Llama-3.3-70B; see the per-judge diagnostics in the machine-readable output). This is a
statistically distinguishable **observed side-stratum interaction**, not a causally identified
position effect: it can combine genuine position-by-condition interaction with question/
transcript heterogeneity allocated unevenly by the hash-based side assignment, and no unit-level
counterfactual exists in this archive to separate those two sources. That the 50/50-standardized
H differs from the realized-schedule H is a **reweighting consequence** of the observed
side-stratum composition, not additional confirmatory evidence for the interaction's cause.

**Per the frozen decision rule, H survives as a post-hoc-robust result**: the 50/50-standardized
estimate is positive, its 95% question-cluster CI excludes zero, and the valid-only and
within-question estimates agree in direction. The qualitative headline -- limited-verification
oracle access increased judge error in these three worlds -- stands, using the **post-hoc 50/50
side-standardized +4.56pp (strict) / +5.33pp (valid-only)** estimate rather than the
realized-schedule +3.94pp. The original +3.94pp remains reportable only as the realized-schedule
estimate under the assignment actually run; describing either number as "corrected" overstates
what a post-hoc, assumption-based reweighting can establish.

P and R do not automatically inherit H's stability and were checked on their own terms.
**R is stable** (same-direction 50/50-standardized point estimates under strict and valid-only,
both CIs excluding zero: strict +2.98pp [+0.89, +4.98], valid-only +4.14pp [+1.94, +6.21]). **P
is positive under strict standardization (+1.58pp [+0.14, +3.06]); valid-only is inconclusive
(CI crosses zero: +1.18pp [-0.36, +2.75]) with the same point-estimate direction** -- this is
weaker than "stable" and is reported as such; the packaging component remains the least robust
of the three, as it already was in the original banked analysis.

The secondary family was audited on the same terms. **C** (cap-protection interaction) stratifies
by side exactly like H/P/R (both arms it compares derive their label from the same
`(question_id, transcript_index)`) and is **stable**: post-hoc 50/50 side-standardized +17.4pp
[+11.3, +24.3], against the banked +16.5pp.

**D_clean** (debate-vs-no-debate) has **no unique H-analogous standardization**: its two arms
have different polarity-exposure structures (debate averages three transcripts' worth of
possibly-mixed sides; the no-debate K3 comparator always reuses transcript 0's realized side
alone), so there is no single well-defined way to standardize the arms against each other. Two
individually well-defined constructions are reported instead of one standardized number:
- **Realized-schedule official D_clean (banked): +9.7pp**, the estimand as published.
- **Official D_clean stratified by transcript-0 side: +9.73pp [+6.59, +12.85]** (strict) -- the
  banked, all-three-transcripts-averaged per-question D_clean, merely bucketed by transcript 0's
  realized side for standardization weighting; transcripts 1 and 2 remain uncontrolled for side
  inside this number.
- **Matched-transcript-0 sensitivity: +10.6pp [+6.8, +14.5]** (strict) -- a **separately defined
  sensitivity analysis**, not a standardization of the official estimand: it restricts the
  debate arm itself to transcript 0 only (discarding transcripts 1-2) so both arms share the same
  realized side by construction.

Do not call this "D_clean stable." The matched-transcript-0 variant is positive and robust across
both invalid-verdict rules; mirror robustness of the *official* D_clean estimand is not uniquely
defined by the frozen specification, and none of the three numbers above should be read as
superseding the others.

**"Fully preregistered mirrored result" and "all eligibility gates passed" are not restored by
this analysis** and are formally withdrawn as descriptions of the phase-2 main run. The main
H/P/R/C/D_clean contrasts are estimates under the realized fixed-label schedule, now
supplemented by a frozen post-hoc mirror-robustness analysis whose primary post-hoc 50/50
side-standardized H estimate is positive with a CI excluding zero, under the CI-scope
qualification stated above.

Full machine-readable results, including per-judge and per-world diagnostics
(leave-one-judge-out, leave-one-world-out, judge-specific interaction contributions, all under
both the strict and valid-only invalid-verdict rules): `analysis_out/phase2_mirror_reanalysis.json`.
