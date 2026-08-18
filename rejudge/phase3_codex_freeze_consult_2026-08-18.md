# Consult: pre-freeze stress-test of the phase-3 protocol and a reopened owner decision

One-shot brief, self-contained. Debate-style AI-control oversight research.

## Banked context

Phase 2 (pre-registered, complete, consult-hardened): judges evaluate 3-round blind
uncapped debates between two self-play debaters (Llama-3.3-70B, Qwen3.7-Plus) on 82
questions across 3 fictional worlds; a 2-query clean oracle budget RAISED judge error
+3.9pp CI[2.2,5.7] Holm p .0006 vs no oracle (content +2.6 robust, packaging +1.4
fragile). Exploratory: harm concentrated in weaker judges. 492 uncapped transcripts are
frozen and budget-agnostic (debater prompts never state a numeric budget; judge query
prompt is parameterized {remaining_budget}/{total_budget}).

Phase 3 (owner+lead signed off 2026-08-18): map error(b) for b in {0,1,2,4,8}, sequential
clean oracle only, reusing the 492 transcripts verbatim (zero debater calls). 7 judges =
4 phase-2 judges + Llama-3-8B-Lite + gemma-3n-E4B + Qwen3.7-Max (under a pre-declared
inclusion rule). Primary: four contrasts Delta(b)=error(seq_b)-error(b0), Holm-4,
world-stratified question bootstrap with common draws, ITT. Secondary: ONE test, the LS
slope of per-judge Delta(8) on a centered continuous capability anchor (24 held-out
questions x 2 mirrored sides, temp 0). Stage cap $450.

## What the five-lens pre-freeze review found (all now fixed in the draft or reopened)

1. REOPENED - throughput ceiling. The owner picked a 20,000-ruling reviewer ceiling from
   my menu. My projection was wrong by 4-6x. Measured from the phase-2 hash-chained
   decision store: 13,753 unique-payload gate rulings / 15,744 theoretical query slots =
   0.874 yield per query slot; sustained pace ~3,000 rulings/day through the
   nightly-refresh reviewer quota. Honest projections at 7 judges (yield 0.874,
   exhaustion bracket [0.65,0.94] since b2's measured exhaustion was 0.94 and b4/b8 are
   unknown): full grid WITH Option D 106-153k rulings (~35-51 review-days); Option D with
   b4/b8 on a 41-question fallback subset 59-85k (~20-28 days); NO Option D on the full
   82 questions 59-85k (identical arithmetic, coincidence); no-D plus 41-subset 35-51k
   (~12-17 days). Cross-arm/cross-judge dedup and Option-D replicate-pair dedup are
   unknown reducers the canary will measure.
2. REOPENED - Option D (doubling judgment replicates at b4/b8, 6 to 12 slots/cell,
   +$50-70). Adopted on my back-of-envelope "40-60% tail power". The verified simulation
   (same frozen error structure, per-budget slot counts, 400 sims, Holm-4) gives
   U-recovery power b4 0.29 / b8 0.36 with D, vs 0.17 / 0.27 without. Question-level
   heterogeneity dominates the cluster bootstrap, so replicate-doubling buys little; but
   it doubles b4/b8 review volume, the binding constraint.
3. Fixed in draft: mixed-population bootstrap under the fallback is now pinned (common
   draws always resample the full 82; when the fallback is active, each draw's D4/D8
   statistic restricts that draw's resampled clusters to the frozen 41-subset; no
   separate subset bootstrap). Holm p<.05 is the SOLE confirmatory criterion; the sup-t
   band (studentized max-modulus: c = 95th pct of max_j |Delta_j(r)-Delta_j_hat|/se_j,
   band = Delta_j_hat +/- c*se_j) is descriptive only. INVALID common support at
   (transcript,side) level: INVALID in an arm if ANY replicate is INVALID; drop from both
   arms of the contrast, per contrast pair. Secondary: anchor is a FIXED covariate
   (never resampled), CI explicitly conditional on the fixed roster, pre-registered
   leave-one-judge-out jackknife, errors-in-variables attenuation disclosed with NO
   correction. Capability anchor preregisters the period-tolerant parse (permitted by the
   phase-2 parser policy's future_rule for fresh cycles; fixes the known Llama-70B
   trailing-period pathology, strict parse reported as sensitivity). A frozen spend
   forecast method (canary per-slot token means at upper 90% CI x verified prices x main
   slot counts + accrued + 1.15 transport multiplier) now governs the Qwen3.7-Max
   inclusion rule and cap compliance. Source bindings, model registry, slot-level
   completion vocabulary (48,216 main slots with D / 41,328 at 6 judges; 2,016 canary
   slots), engineering gates for the phase-3 plan/ingestion code (phase-2's enumerators
   hard-code its condition tuple; transcript ingestion pre-seeds the result store with a
   hard runtime assert against live generation), seed extension (debater+namespace),
   audit sampling restated with named auditor.

## The re-decision menu I intend to give the owner

- Option 1 (my recommendation): DROP Option D, full 82 questions everywhere, ceiling
  ~90,000 rulings (~3-4 weeks review). Rationale: no-D-full-82 costs the same review
  volume as D-on-41-subset but keeps the full question population for every contrast and
  a single-population sup-t band; halving questions at the tail inflates SE ~1.4x, which
  roughly cancels D's gain, so "D + subsample" is dominated. Tail power stays honest-but-
  weak (0.17/0.27 for mild recovery); phase-2-magnitude effects are >=0.99 everywhere.
- Option 2: KEEP Option D, full 82, ceiling ~155,000 (~5-7 weeks). Max tail power
  (0.29/0.36). Owner accepts calendar cost.
- Fallback ladder in either case: subsample b4/b8 to the frozen 41-subset, then (step 2)
  drop b8's second replicate if D was kept, then halt-to-owner (paid review not
  pre-authorized).

## Questions (answer crisply, numbered; disagree wherever warranted)

1. Attack the re-decision menu. Is Option 1 (drop D) actually dominant given the
   numbers, or is there a configuration I have not considered that beats both (e.g.
   asymmetric replicates only at b8; b-arm-specific question counts; K>2 sides at the
   tail; dropping b1 to fund the tail - the proposal says b1 is never dropped, but say so
   if you think that commitment is wrong)?
2. Is a ~90k or ~155k "ceiling" even the right control variable, versus pre-registering a
   review-days budget with the ceiling derived at canary time from the measured per-slot
   ruling rate? The trigger must stay mechanical and blinded; which formulation is
   cleaner governance?
3. The mixed-population bootstrap pin (restrict per-draw resampled clusters to the fixed
   41-subset for D4/D8): any validity problem for the sup-t band or the Holm family when
   two contrasts live on a subset population and two on the full one? Is the "same draws,
   restricted statistic" scheme the right one vs a separate stratified subset bootstrap?
4. The INVALID any-replicate rule at (transcript,side) level: does it introduce
   differential attrition between 1-replicate and 2-replicate arms that could bias the
   valid-only sensitivity at exactly the arms Option D strengthens (if D survives)?
5. The exhaustion bracket [0.65,0.94] for b4/b8 projections: defensible, or should the
   protocol pre-register a more conservative (higher) planning bracket given weak judges
   exhausted 94% at b2 and the harm concentrates in weak judges?
6. The secondary slope test now has: fixed-covariate bootstrap, roster-conditional CI,
   LOJO jackknife, attenuation disclosure. With ~7 points, is there any remaining fix
   you would demand before freeze, or is this now honest enough to pre-register?
7. Anything else that would embarrass this protocol in external review - especially the
   Qwen3.7-Max inclusion rule (gates + frozen forecast under cap), the no-replacement
   rule for failed new judges, the tolerant-parse preregistration, or the decision to run
   b0/b2 fresh rather than pooling phase 2.


---

# Codex opinion (GPT-5.6, one-shot, 2026-08-18)

Bottom line: recommend Option 1, but do not freeze yet. The power simulation, workload arithmetic, and joint Qwen/fallback gate need correction first.

1. **Option 1 is efficient, not strictly dominant.**

Option 2 buys power, so neither strictly dominates the other. Option 1 does practically dominate D plus 41 questions if the scientific target remains the 82-question corpus. With between-question variance \(A\) and within-question variance \(B\):

- Full 82, one replicate: \((A+B)/82\)
- Half 41, two replicates: \((2A+B)/82\)

The subset design retains the same within-question term but doubles the between-question term. Its b0 comparator also loses half its clusters.

More importantly, the quoted powers are not yet decision-grade. The [baseline simulator](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/scripts/phase3_power_sim.py:55) drops `debater_model` from its cell key, then simulates one 6-slot cell per question and judge. The actual design has two separate debater cells. The [Option D simulator](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/scripts/phase3_power_sim_optiond.py:27) similarly uses 6/12 independent Bernoulli draws rather than the design’s two 6/12-slot debater cells, and assumes replicate independence. That likely exaggerates replication’s value relative to question heterogeneity.

Before the owner decides, rerun paired, common-random-number simulations for tail slots:

- `(b4,b8) = (6,6)`
- `(12,6)`
- `(6,12)`
- `(12,12)`

The query-weight totals are 15, 19, 23, and 27 respectively. If only one middle option is offered, b8-only best matches the “useful setting” objective. B4-only may be more power-efficient per ruling, but significance for the scenario’s \(+1\)pp b4 effect establishes residual harm, not recovery.

Keep b1. It costs only 1/15 of no-D query weight and identifies low-dose onset. K greater than 2 adds replicates, not new mirror orientations. Avoid arm-specific question counts except as a forced fallback.

2. **Use both a ruling ceiling and a quota-day limit.**

Per-slot ruling yield measures demand. Rulings per refresh-day measures capacity. One cannot be derived from the other.

Freeze:

\[
R_U=\text{accrued}+\sum_s N_s\,U_{90}(\text{unique rulings per slot}_s)
\]

\[
D_U=\left\lceil R_U/L_{90}(\text{rulings per quota-day})\right\rceil
\]

Then select the highest-priority frozen configuration satisfying both \(R_U\le R_{\max}\) and \(D_U\le D_{\max}\). The owner should choose the calendar tolerance; the manifest should enforce the mechanically derived ruling ceiling. If forced to use one operational control, unique rulings is cleaner because wall-clock days are polluted by outages.

At 3,000/day, 90k is 30 refresh-days, about 4.3 weeks. 155k is 52 days, about 7.4 weeks. The current labels are optimistic.

3. **Holm is valid; the curve interpretation and bootstrap implementation need tightening.**

Holm can combine valid marginal p-values for different estimands under arbitrary dependence, so \(\Delta_F(1),\Delta_F(2),\Delta_S(4),\Delta_S(8)\) form a valid family. [Holm’s procedure](https://doi.org/10.2307/4615733) does not require a common population.

But those four points are not one coherent dose-response curve. Under fallback, report a no-cost descriptive curve with b0, b1, and b2 also restricted to the same 41 questions.

Do not use an independent subset bootstrap for the sup-t band. It loses the covariance induced by shared questions and b0. Instead use common mean-one question weights within world, then normalize separately over the full and subset domains using frozen domain-specific world weights. This is the clean shared-weight formulation supported by [exchangeably weighted bootstrap theory](https://stat.uw.edu/research/preprints/tech-report/bootstrap-general-weights-and-multiplier-central-limit-theorems).

“Resample 82, then filter” is defensible only if those separate normalizations are explicit. Otherwise random retained counts also randomize the subset’s world mixture. Finally, pin the exact primary bootstrap p-value formula. “Holm-adjusted p-value” is currently insufficient.

4. **Yes, Option D creates differential attrition in the valid-only sensitivity.**

If per-replicate invalidity is \(p\), a one-replicate arm fails with probability \(p\); a two-replicate arm fails with \(2p-p^2\). Pairwise deletion then removes the paired b0 observation too. If invalidity correlates with judge weakness, difficulty, or correctness, the resulting complete-case estimand is selected differently at b4/b8.

The primary ITT is unaffected because INVALID counts wrong. If D survives, define the principal valid-only sensitivity using one frozen replicate index per transcript-side in every arm. Report an all-valid-replicates estimate separately as descriptive. Also report INVALID counts by budget, judge, and replicate block, plus all-correct versus all-wrong bounds.

5. **The \([0.65,0.94]\) range is not a defensible planning upper bound.**

The 0.94 is an observed point, not a ceiling. High-budget utilization can reach 1.00.

More seriously, the protocol defines 0.874 as unique rulings divided by theoretical query opportunities, already net of early DONE, retries, exhaustion, and dedup, then multiplies it by another exhaustion factor in the [workload formula](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase3_protocol.json:204). That double-discounts workload.

If 0.94 is compatible with the same exhaustion definition, the conditional factor is approximately \(0.874/0.94=0.930\). At full utilization, the provisional main-grid forecasts become roughly:

- No D: 90.3k using 0.874 directly, or 96.1k using 0.930.
- Full D: 162.5k or 173.0k.

Those exclude accrued canary volume, and retries can make rulings per nominal opportunity exceed one. Use arm-by-judge canary \(U_{90}\) estimates of the direct combined ratio and take no unmeasured cross-arm dedup credit.

6. **The slope is honest enough only as a finite-roster observed-score association.**

The fixed-anchor, question-bootstrap construction is appropriate for that estimand. Before freeze, add:

- A non-estimable rule for zero or inadequate anchor spread and reduced rosters. My choice is estimate-and-plot only, with no p-value, below six judges.
- “Classical independent measurement error tends to attenuate in expectation,” not a categorical claim that realized bias is toward zero.
- Explicit status for the p-value. If Holm is the sole confirmatory criterion, the slope is nominal and nonconfirmatory. If a confirmatory capability claim is intended, it needs multiplicity protection.
- Propagation of the strict-parser sensitivity through both anchor scores and the slope.

LOJO should remain an influence diagnostic, not a seven-model jackknife CI. No EIV correction is required if the observed anchor score itself is the covariate.

7. **Remaining external-review issues.**

- The Qwen inclusion and reviewer fallback gates are circular. Freeze a lexicographic configuration order. I recommend full 82-question coverage first, Qwen-Max retention second, extra replicates third. The current [inclusion rule](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase3_protocol.json:126) considers spend but not whether keeping Qwen forces the review subset.
- Derive canary and main counts for every final roster size \(N=4,\ldots,7\). Candidate canary spend remains accrued even when a candidate later fails.
- Make the spend forecast executable before canary data exist: CI estimator and cluster, zero-query slots, failed calls, retries, price changes, equality/rounding, and unknown charges. Use exact frozen-main transcript lengths for base context rather than treating six smoke questions as representative.
- Either give no credit for cross-arm/cross-judge dedup or freeze the byte-exact full reviewer-input key and global-union estimator. Estimated dedup should never rescue a threshold case.
- No replacement is the right rule because it prevents model fishing, but obtain explicit owner ratification and distinguish semantic calibration failure from temporary endpoint unavailability.
- Tolerant parsing is substantively defensible, but the cited [phase-2 policy](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase2_anchor_parser_policy_2026-07-21.json:4) is still marked pending approval. Ratify it as a new phase-3 rule rather than inherited authority.
- Fresh b0/b2 is correct. It avoids calendar, harness, seed, and roster mixing. Call D2 a fresh within-corpus retest, not an independent replication.
- Public wording should say “assignment to up to \(b\) screened query opportunities under the frozen stopping and retry policy.” The current primary family can certify benefit versus b0, but it cannot formally certify a downturn from b2 without a b8-minus-b2 contrast.
- Over a multiweek run, record provider revision metadata and predeclare the response to alias drift. Describe the named assistant auditor as internal QA unless an independent party audits.

My freeze blockers are therefore: correct the power simulation, repair the workload model, order the joint configuration gates, and fully pin the shared-weight bootstrap plus p-value construction.


