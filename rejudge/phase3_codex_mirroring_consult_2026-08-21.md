# Consult: K2 mirroring was never implemented, in either phase. Blast radius on banked results and the remedy.

One-shot, self-contained. Pre-registered debate-oversight experiment. Phase 2 BANKED and
published to the team: oracle budget b2 raised judge error +3.9pp CI[2.2,5.7] Holm p .0006
vs b0 (H), decomposed P +1.4 / R +2.6, on 82 questions x 4 judges x 2 debaters x 3
transcripts x K2, world-stratified question-cluster bootstrap. Phase 3 (budgets 0,1,2,4,8,
6 judges, same transcripts reused) is mid-canary, main run neither authorized nor built.

## The defect (forensically confirmed today)

The design docs and both frozen protocols say K2 = "mirrored side assignment; labels fixed
across matched conditions." As executed, the A/B position function takes only
(question_id, transcript_index). No side/replicate input exists anywhere downstream of the
plan enumerators. Both K2 slots of every judgment cell in BOTH phases presented identical
labels: duplication, not mirroring. Verified by pairing raw archive records: phase-3 canary
0/288 b0 pairs mirrored (and 0 at every budget); phase-2 bridge canary control 0/192.
Realized assignment: position_a_is_correct(question_id) is a fixed per-question draw
(13A/11B among the 24 held-out; the 82 main questions have their own fixed draw).

Two aggravators:
1. Phase 2's side-bias calibration gate REPORTED pass (<=10pp per judge). Recomputing the
   frozen gate arithmetic on REALIZED positions against the phase-2 archive FAILS 3 of 4
   judges (39.7pp, 11.5pp, 10.84pp; plus gpt-oss 4.17% invalid vs the 2% gate). The
   reported phase-2 gate must have grouped by intended-side metadata, which under
   duplication splits identical rows arbitrarily and measures ~0: a vacuous gate.
2. These big numbers are plausibly REAL side preferences of the judges, now measured
   honestly for the first time (on an unbalanced 52/44 split).

Unaffected: phase-3 capability anchors (separate, verified-mirrored path, 24/24 per judge);
transcripts (generation had its own counterbalanced opening order and is not in question).

## My blast-radius analysis of the BANKED phase-2 result - attack it

(a) Identification of H/P/R: within each (question, judge, debater) cell, every condition
    (b0, sequential_b2, batch, placebo) shared the SAME fixed assignment (this was
    deliberate: "labels fixed across matched conditions"). Contrasts therefore difference
    out any side main effect. The residual exposure is a SIDE x CONDITION interaction
    (e.g. oracle results shifting a judge toward/away from its preferred side), which true
    mirroring would have averaged out and duplication does not. Its contribution is
    weighted by the assignment imbalance across the 82 questions.
(b) CI validity: the question-cluster bootstrap treats each question's assignment as part
    of the question; coverage for "mean effect over these 82 questions under this
    realized assignment" holds. The estimand is subtly narrower than the mirrored ideal
    ("...averaged over both label assignments").
(c) K2 as replication: two temp-0.3 draws of the identical prompt are honest i.i.d. draws
    of the same conditional distribution, so no pseudo-replication bias; we just never got
    the variance/bias reduction mirroring promised.
(d) Empirical bound available: realized positions are known for every phase-2 row, so we
    can estimate the side x condition interaction directly (stratify H/P/R by A-correct vs
    B-correct questions; also reweight to a balanced pseudo-assignment) and bound the bias
    on the banked numbers. Phase-2 data is already unblinded, so this is a post-hoc
    robustness analysis and must be labeled as such, but its SPEC can be fixed before
    anyone computes it.

## Remedy sketch for phase 3

Fix: _polarity gains a side input (XOR the per-question base draw with the recovered side
bit); re-run all 1,113 completed canary judgment cells + 39 pending under a corrected
successor identity (~$12-14); anchors/transcripts carry forward; gates re-evaluated on the
true 48/48 balance. Open policy question: the 10pp gate threshold was chosen when the gate
(unknowingly) measured nothing; under honest measurement, judges with large REAL side
preference (possibly 20-40pp) would fail. Under true mirroring, side preference cancels in
every pooled estimate by symmetry, so a large-but-stable preference arguably should NOT
disqualify a judge; the gate's real purpose becomes flagging judges so biased that
mirroring leaves mostly noise.

## Questions (crisp, numbered; disagree freely)

1. Attack (a)-(d). Is the banked phase-2 headline (H +3.9pp) defensible pending the
   stratified robustness analysis, or should we treat it as provisionally suspect in ALL
   communications right now? What exact reanalysis would you demand, and what result
   pattern would force a retraction vs a footnote?
2. Phase-2 disclosure: what must the erratum say about the vacuous side-bias gate, given
   the close-out reported it as passed? (No one intended deception; the gate computation
   grouped by intended side. But the record says "passed" for a check that measured
   nothing.)
3. Phase-3 gate policy after the fix: keep the 10pp side-bias threshold on honestly
   measured bias (likely failing several judges whose bias mirroring would neutralize), or
   amend the gate's ROLE (e.g. threshold on residual imbalance after mirroring, or a much
   looser raw-preference bound plus a variance-inflation disclosure)? The gate must be
   re-fixed BEFORE we see the re-run gate numbers... except we already saw the broken-run
   numbers, which are strongly correlated. How do you keep this honest?
4. Re-run scope: b0-only (gates) vs all 1,113 judgment cells? The smoke cells feed
   review-volume/cost/dedup measurements (side-invariant operational quantities) - do they
   need re-running at all, or only the b0 gate set plus enough smoke to re-verify the
   pipeline under the corrected identity?
5. Severity classification: the phase-3 protocol says a semantic repair requires a new
   protocol ID and full canary rerun. Does fixing never-implemented mirroring that the
   protocol TEXT always claimed constitute a semantic repair of the design (new protocol
   ID), or a defect fix restoring the frozen design's stated semantics (amendment +
   successor identity + targeted rerun)? Phase-2 precedent: incident-5 remediation used
   amendments, not a new protocol.
6. Sequencing: this now queues ahead of three open items (review-pace crank test, the $671
   vs $450 forecast with the shed-Qwen3.7-Max ladder step, the Qwen2.5-7B provider outage).
   Any interaction we should exploit (e.g. the rerun doubles as the pace crank test)?


---

# Codex opinion (GPT-5.6, one-shot, 2026-08-21)

Bottom line: suspend the confirmatory status of phase 2 now, but do not yet retract the substantive direction of H. The +3.9pp estimate remains valid for the realized duplicated-label schedule. It is not currently defensible as the preregistered mirror-averaged estimate, and “all eligibility gates passed” is false.

1. Attack on (a) through (d)

(a) is directionally right but incomplete.

Let \(u=(q,t,j,d)\), \(S_u\) be the realized label polarity, and \(\Delta_E(u,s)\) be an H, P, or R contrast under polarity \(s\). Then:

\[
E_{\text{observed}}=\operatorname{mean}_u \Delta_E(u,S_u)
\]

while the promised target was:

\[
E_{\text{mirrored}}=\operatorname{mean}_u
\frac{\Delta_E(u,A)+\Delta_E(u,B)}{2}
\]

The difference depends on the unit-level side-by-condition interaction. Matching polarity across conditions removes a pure position main effect from H/P/R, but not this interaction.

Two corrections:

- The main-run assignment unit is question by transcript, not merely question. The function includes `transcript_index` but not K2 replicate. From the frozen phase-2 plan and current assignment code, the 246 question-transcript units are 128 A-correct and 118 B-correct. Nineteen questions have all three transcripts on one polarity; 63 have both. The relevant imbalance is therefore 128/118, plus its allocation across questions and worlds.
- Exact marginal 50/50 balance would not itself eliminate finite-sample interaction bias. It eliminates the bias only if the side interaction is sufficiently homogeneous or exchangeable across assigned units. True within-unit mirroring eliminates it by construction.

There is also a separate eligibility-population problem: the correct canary gate was never evaluated. Therefore the four-judge population used in phase 2 was not shown to satisfy the frozen launch criterion in [phase2_protocol.json](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase2_protocol.json:367). A stable H reanalysis cannot retroactively repair that protocol-compliance claim.

(b) overstates CI validity.

The existing bootstrap resamples question clusters while carrying each question’s realized polarity pattern with it. It can be interpreted as uncertainty over a superpopulation of question-plus-assignment clusters within these three worlds. It is not coverage for a fixed finite set of 82 questions, and it does not include uncertainty from the missing opposite-polarity potential outcomes. The original CI remains appropriate for the realized-schedule estimand, subject to the usual question-superpopulation interpretation, but not for the promised mirrored estimand.

(c) is mostly right.

The two K2 rows are legitimate repeated temperature-0.3 judgments of the same prompt, and the analysis averages them before question-cluster inference. That avoids treating them as independent experimental assignments. “IID” is slightly too strong because provider sampling independence is not demonstrated, but there is no obvious point-estimate pseudo-replication bias. They improve precision only for the observed polarity.

(d) is too strong.

A-versus-B stratification and 50/50 reweighting do not directly identify or bound the finite-sample mirrored target. Each question-transcript unit lacks its opposite-polarity outcome. Differences between the observed A and B strata can reflect:

- genuine side-by-condition interaction;
- question or transcript effect heterogeneity allocated unevenly by the hash draw;
- both.

Random-looking hash assignment makes post-stratification a reasonable assumption-based estimator, but not a direct empirical counterfactual. Without exchangeability or a corrected rerun, the worst-case bound is not informative.

The exact reanalysis I would freeze is:

1. Verify polarity from rendered prompts, not intended metadata. Confirm matching across b0, sequential-b2, and batch for every \(q,t,j,d,r\), and reproduce the banked H/P/R exactly.

2. Collapse the duplicated K2 judgments to condition means at \(q,t,j,d\). Compute atomic H/P/R contrasts, preserving \(H=P+R\).

3. For strict-INVALID and common-support valid-only estimands, report:

   - realized-schedule H/P/R;
   - world-standardized A-polarity and B-polarity estimates;
   - a 50/50 standardized estimate;
   - A-minus-B interaction;
   - shift from the banked estimate.

4. Use common world-stratified question-cluster bootstrap draws for all quantities. Add a within-question analysis restricted to the 63 questions having both polarities across transcripts, with question fixed effects or direct within-question centering. Treat this as corroboration, since transcript content still differs.

5. Predeclare judge-specific interaction contributions, leave-one-judge-out, leave-one-world-out, and valid-only results as diagnostics. Do not select among them afterward.

6. Audit every published phase-2 judgment result, not only H/P/R. C has the same side-by-condition issue. D_clean is more exposed because debate averages three transcript polarities while the no-debate K3 repetitions appear to reuse the question’s transcript-0 polarity, so even a raw side main effect may not cancel.

Decision rule:

- An erratum is mandatory regardless of the reanalysis.
- The qualitative “budget increased error” headline may survive as a post-hoc robust result if the frozen 50/50 standardized H is positive, its 95% question-cluster CI excludes zero, and the valid-only and within-question estimates agree in direction.
- If that primary standardized CI includes zero, withdraw the directional headline and report phase 2 as inconclusive for the mirrored target.
- If the estimate is zero or negative, retract the substantive headline.
- Any numerical mirrored-effect claim must use the corrected estimate. The original +3.9pp can remain only as the realized-schedule estimate.
- P and R require their own stability. A stable H does not rescue packaging or content claims whose standardized estimates reverse or become inconclusive.
- “Fully preregistered mirrored result” and “all eligibility gates passed” cannot be restored by post-hoc robustness analysis.

2. Required phase-2 erratum

This needs a body-level correction, not a footnote. It should say, approximately:

> The frozen protocol required two mirrored A/B label assignments per judgment unit. The execution instead generated two stochastic judgments with identical label order because polarity did not receive the K2 replicate or side input. The reported side-bias gate grouped rows by intended-side metadata, not the labels actually presented, so its reported pass was non-diagnostic. Recalculation on realized prompt positions produces side-stratum differences of 39.7pp, 11.5pp, and 10.84pp for three judges. These unpaired differences may combine true position preference with finite-sample question difficulty and are not equivalent to the intended paired gate. Separately, gpt-oss has a 4.17% strict-INVALID rate against the 2% gate. We therefore withdraw the statement that all phase-2 eligibility gates passed. The main H/P/R contrasts remain estimates under the realized fixed-label schedule and are undergoing a frozen post-hoc mirror-robustness analysis.

Also state that there was no deception, that the defect originated in intended-side grouping, and that transcripts and separately mirrored anchors are unaffected. Do not describe the 39.7pp figures as confirmed “real side preferences” until the corrected paired run measures the same questions under both positions.

3. Phase-3 gate policy

Do not loosen 10pp to 20, 30, or 40pp. That would look directly fitted to the observed broken-run proxy.

There are two honest choices:

- Keep the current phase-3 protocol and its 10pp threshold. Then corrected failures bind.
- Preferred: issue phase-3 protocol v2 and remove raw position preference as an eligibility gate. Replace it with exact structural mirroring and completion requirements, while reporting raw paired position effect and semantic consistency as precision and construct-validity diagnostics.

Under complete, equally weighted mirroring, stable raw position preference does not bias the pooled estimand. It can reduce informativeness, but that is not the same as identification failure. The actual gates should be:

- every matched unit has one rendered A-correct and one rendered B-correct prompt;
- both sides complete in every condition;
- zero polarity or dependency mismatches;
- the existing parseability/INVALID gate;
- mandatory per-judge reporting of paired position effect, semantic-answer consistency, and resulting variance.

“Residual imbalance after mirroring” should be a zero-tolerance structural audit, not a statistical threshold. If judge exclusion for position sensitivity is still desired, use a newly reserved independent calibration set and a threshold justified without the broken canary values. Otherwise freeze the roster independently of side-bias outcomes.

4. Rerun scope

Rerun all 1,152 phase-3 judgment cells: the 1,113 completed cells plus the 39 pending cells. Carry forward transcripts and the verified-mirrored capability anchors.

The smoke quantities are not safely side-invariant:

- label polarity can change whether and what a judge queries;
- it can change DONE behavior and output length;
- it changes payload identity and therefore dedup;
- those changes propagate into review volume, pace, and cost.

A b0-only rerun is sufficient only if every old smoke-derived forecast, pace, and dedup measurement is discarded. Since those measurements are needed for the current open decisions and the full correction costs only about $12 to $14, the full judgment rerun is the clean scope. Preserve the old rows as superseded defect evidence rather than mixing them with v2.

5. Severity and identity

Classify this as a major protocol deviation, not a catastrophic loss:

- Phase 2: confirmatory-estimand deviation plus invalid launch-gate evaluation; substantive conclusion unresolved pending correction.
- Phase 3: complete invalidation of judgment-canary results, but no main-run outcome contamination because main execution was neither built nor authorized.

Use a new phase-3 protocol ID, with a concise amendment explaining that v2 restores the design semantics already stated in v1. The frozen protocol explicitly says semantic repair requires a new ID and complete canary rerun in [phase3_protocol.json](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase3_protocol.json:293). Changing the second K2 prompt from duplicate to opposite polarity is a semantic change in executed prompts even though it corrects the text’s intent.

Incident 5 is not controlling precedent. It replaced contaminated rulings within an already implemented treatment. Here, an entire counterfactual presentation was never executed.

6. Sequencing and combining open work

Recommended order:

1. Immediately mark the phase-2 result “confirmatory status suspended pending mirror robustness analysis” and circulate the gate correction.
2. Freeze the one-page phase-2 reanalysis specification before computing side-stratified outcomes.
3. Create phase-3 protocol v2, fix polarity threading, and verify rendered prompt pairs end to end.
4. Freely retry build, provider, and GPU-independent preflight as needed.
5. Run one complete corrected canary under a fixed reviewer configuration.

The corrected canary can also be the review-pace crank test if the crank settings and timing denominator are frozen before its formal measurement window. Do not tune concurrency during that window. Environmental interruption can void and restart the pace measurement without invalidating already correct semantic cells.

That same rerun should replace the $671 versus $450 forecast inputs and exercise the existing Qwen3.7-Max shedding ladder. The Qwen2.5 outage remains an environmental provider issue: retry or defer it, and do not convert it into a semantic judge failure. Only after the corrected gates, pace, and forecast are settled should phase-3 main be built or authorized.

The implementation evidence matches the forensic account: [config.py](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/config.py:94) omits replicate/side from polarity, while [_polarity](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase2_canary_execute.py:108) cannot recover it. No repository changes were made.


