*DRAFT -- not committed, not inserted into `reports/2026-08-11-phase2-main-results.md`. Prepared
2026-08-21 for owner review, per `rejudge/phase2_mirror_reanalysis_spec_2026-08-21.json`
step 6 and `rejudge/phase3_codex_mirroring_consult_2026-08-21.md` section 2 (template
language), completed with the numbers computed by `scripts/phase2_mirror_reanalysis.py`
against `analysis_out/phase2_mirror_reanalysis.json`.*

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

Realized assignment: of the 246 (question, transcript) units, 128 are A-correct and 118 are
B-correct; 63 of the 82 questions carry both polarities across their three transcripts, 19 are
single-polarity (all three transcripts on one side). Polarity was independently re-derived from
the archived rendered judge-facing prompts (`judge_messages` in `main_results.jsonl`) for every
one of 19,680 judgment rows and found internally consistent across every condition and both K2
replicates within every unit, with zero exceptions; the pre-analysis reproduction of the banked
H/P/R numbers from the raw archive matched byte-for-byte.

The original **+3.9pp** H estimate is the **realized-schedule** estimate: the mean effect over
these 82 questions under the one label assignment each happened to receive. It remains valid as
that quantity but is not the preregistered mirror-averaged estimand, because sides were never
balanced within a unit.

A **50/50 side-standardized** estimate (equal weight to the A-correct and B-correct strata
within each of the three worlds, an assumption-based approximation to the mirrored ideal, not a
unit-level counterfactual) gives:

| Estimand | Realized-schedule (strict) | 50/50-standardized (strict) | 50/50-standardized (valid-only) | 63-question within-question corroboration |
|---|---|---|---|---|
| H | +3.94pp [+2.24, +5.67] | **+4.56pp [+2.89, +6.25]** | +5.33pp [+3.58, +7.04] | +4.49pp [+2.65, +6.40] |
| P | +1.37pp [+0.10, +2.69] | +1.58pp [+0.14, +3.06] | +1.18pp [-0.36, +2.75] | +1.66pp [+0.03, +3.31] |
| R | +2.57pp [+0.56, +4.50] | +2.98pp [+0.89, +4.98] | +4.14pp [+1.94, +6.21] | +2.83pp [+0.52, +5.00] |

The A-minus-B side-by-condition interaction is large and statistically distinguishable from
zero: **H interaction -6.24pp [-9.37, -3.18]** (strict), driven mostly by two judges
(Qwen2.5-7B and Llama-3.3-70B; see diagnostics in the machine-readable output) -- confirming a
real side effect exists, exactly as the vacuous gate failed to rule out, but one that the 50/50
standardization above is designed to average out rather than leave uncontrolled in the headline.

**Per the frozen decision rule, H survives as a post-hoc-robust result**: the 50/50-standardized
estimate is positive, its 95% question-cluster CI excludes zero, and the valid-only and
within-question estimates agree in direction. The qualitative headline -- limited-verification
oracle access increased judge error in these three worlds -- stands, using the **corrected,
standardized +4.56pp (strict) / +5.33pp (valid-only)** estimate rather than the uncorrected
+3.94pp. The original +3.94pp remains reportable only as the realized-schedule estimate under
the assignment actually run.

P and R do not automatically inherit H's stability and were checked on their own terms: both
call **stable** (same-direction 50/50-standardized point estimates under strict and valid-only,
with the strict CI excluding zero), though P's valid-only CI ([-0.36, +2.75]) crosses zero even
as its point estimate keeps the same sign -- the packaging component is the least robust of the
three, as it already was in the original banked analysis.

The secondary family was audited on the same terms. **C** (cap-protection interaction) stratifies
by side exactly like H/P/R (both arms it compares derive their label from the same
`(question_id, transcript_index)`) and is **stable**: 50/50-standardized +17.4pp [+11.3, +24.3],
against the banked +16.5pp. **D_clean** (debate-vs-no-debate) has **no well-defined 50/50
standardization for the officially banked quantity**: the no-debate K3 comparator always reuses
transcript 0's realized side, so there is no "no-debate B-side" population to standardize the
(three-transcript-averaged) debate side against. The well-defined alternative -- debate error
restricted to transcript 0 only, so both arms of the comparison share the same realized side by
construction -- is **stable**: 50/50-standardized +10.6pp [+6.8, +14.5] (strict) against the
banked, differently-defined +9.7pp.

**"Fully preregistered mirrored result" and "all eligibility gates passed" are not restored by
this analysis** and are formally withdrawn as descriptions of the phase-2 main run. The main
H/P/R/C/D_clean contrasts are estimates under the realized fixed-label schedule, now
supplemented by a frozen post-hoc mirror-robustness analysis whose primary standardized H
estimate is positive with a CI excluding zero.

Full machine-readable results, including per-judge and per-world diagnostics
(leave-one-judge-out, leave-one-world-out, judge-specific interaction contributions, all under
both the strict and valid-only invalid-verdict rules): `analysis_out/phase2_mirror_reanalysis.json`.
