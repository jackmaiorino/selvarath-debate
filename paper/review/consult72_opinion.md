REQUIRED CHANGES

1. **Must fix: the collection chronology is not an absolute split.**

   [methods.tex:143](paper/sections/methods.tex:143) says “every verdict in the query conditions came after the run resumed.” The same assertion appears in Appendix B and the collection-order limitation.

   The initial-launch log records 51,360 completed sessions after round 1 and 57,906 after round 2, before relaunch. The additional 6,546 comprise 6,420 Llama refusals and **126 query-condition sessions from the retained judges**. These verdicts were preserved. `finalphase/judging.py:94–106,147–149` permits a verdict after an immediate stop or failed first query step; `engine.py:26–55` explains their completion in round 2. See `E:/finalphase-runs/final-phase-2026-10-03/main_20261025.out:6–7`.

   Replace the Methods chronology with:

   > The run was also split in time: the three conditions without queries finished before the gate outage. Most query-condition verdicts came after the run resumed about a day later; some sessions had already finished after an immediate stop or a failed first query step.

   Replace Appendix B’s chronology with:

   > The three conditions without queries completed in the first judging round, which ended on 2026-10-27 UTC before the gate outage. Some query-condition sessions completed during the initial launch without reaching the oracle. Most query-condition verdicts, including every verdict following an oracle answer, were collected after the relaunch on 2026-10-28.

   Replace the collection-order limitation with:

   > **Collection order.** The conditions without queries finished before the gate outage, and most query-condition verdicts were collected after the relaunch about a day later. Some query-condition sessions finished earlier without reaching the oracle. We have no evidence that the judges’ behaviour changed in between, and no test of it.

   This corrects an overstatement inherited from council finding S9.

2. **Must fix: Table 6 still omits registered secondary results and their corrections for two panels.**

   [appendix.tex:91](paper/sections/appendix.tex:91) says Table 6 “gives the registered contrasts behind each row”; its caption reports the last two contrasts as “not adjusted.”

   The printed values are correct. However, preregistration §7.5 requires **all six secondary contrasts, Holm-corrected**, for the 806-question and added-two-question panels. Their per-judge results and adjusted pooled values remain absent. This matters because Qwen’s one-query increase passes the registered correction on the 806-question subset. Sources: `main-preregistration.md:251–266` and `main_analysis.json → sensitivity.{determinacy_0_or_1,with_sensitivity_only_questions}.secondary`.

   Replace the caption opening with:

   > One-query primary contrasts and pooled secondary contrasts in each sensitivity analysis, in percentage points, on the scale of the judges that produced data.

   Add this supplementary paragraph:

   > The panels excluding judges registered the two pooled secondary tests without correction. The 806-question and 1,070-question panels registered all six secondary tests with a Holm correction. In both panels, corrected p-values were 1.0000 for six queries against none and 0.0006 for debate-and-world against six queries. For Qwen3.8, the one-query increase was 0.99 points on the 806-question subset (95% interval 0.47 to 1.62; p = 0.0021, corrected p = 0.0105) and 1.07 points with the two questions added back (0.27 to 1.93; p = 0.0109, corrected p = 0.0545). Its six-query differences were +0.19 points (−0.30 to +0.70; p = 0.4842, corrected p = 1.0000) and −0.61 points (−1.40 to +0.06; p = 0.0968, corrected p = 0.3872), respectively. Both Llama contrasts were unevaluable and carried p = 1 in each correction. These sensitivity results cannot rescue or overturn the main analysis.

   This belongs entirely in the supplement. The narrower D1 repair concerning the pooled contrasts was applied correctly.

3. **Must fix: distinguish declining from one query from returning to baseline.**

   [results.tex:94](paper/sections/results.tex:94): “The fall from one to six is not broad.”

   Five judges declined: Luna, Haiku, Sonnet, Opus and Qwen. The concentration concerns the **size** of the decline. The following sentence switches to comparison with the no-query baseline. Sources: `main_analysis.json → curves`, `tab_by_judge.tex`, and council debate D3, which explicitly conceded this distinction.

   Replace that sentence with:

   > Five of seven judges’ error fell from one query to six, though the sizes of those declines differed.

   The subsequent explanation that Haiku and Qwen supply most of the pooled decline can remain.

4. **Must fix: the repeat-pass denominator and the 488-item provenance are still misstated.**

   [appendix.tex:227](paper/sections/appendix.tex:227): “Of 488 rejections … (487 by the classifier and one by the format check), the deployment admitted 154 on its first pass and 60 on the repeat.”

   Only **357 of those 488 items** were in the saved repeat set. The sentence suggests comparable denominators despite selection partly by first-pass rejection, leaving council S15 unresolved.

   Also, all 488 items have `source: classifier` in the pilot audit. The extra item, `R0430`, received the audit label `FMT:not_admissible`; that is not evidence that the operational format check rejected it. Sources: the run’s `pilot/reviews/gate_audit.json`, `gate-endpoint-check-v1/endpoint_compare.py`, and `repeat_set.json`. The 357 is a membership count from those saved records.

   Replace with:

   > The pilot audit identified 488 confirmed-rejection items: 487 labelled OK and one malformed query whose embedded claim was judged inadmissible. The deployment admitted 154 of these 488 items on its first pass. Of the 357 included in the selected repeat set, it admitted 60 on the repeat. The repeat set was selected partly by first-pass rejection, so these counts have different denominators and do not measure a change in screening performance.

5. **Must fix: the explanation reviewer’s coverage is described with the wrong complement.**

   [appendix.tex:382](paper/sections/appendix.tex:382): “Those included all 18 cases not labelled ‘correct answer, unsupported conclusion’…”

   There were **21** cases outside that label: 18 in the three other mechanism categories and three unreadable verdicts. Consult 66 reviewed the former 18 plus three of the 56 cases in the largest category. Sources: `paper/reference/explanation_note_20261030.md:66–72,104–106` and the run’s `coordinator/consults/consult66_opinion.md:23–38`.

   Replace the coverage sentences with:

   > A reviewer from another model family, GPT-6 Astra, re-labelled 21 difficult cases. It reviewed all 18 cases in the three other mechanism categories and three of the 56 labelled “correct answer, unsupported conclusion”; it did not review the three unreadable-verdict cases.

6. **Must fix: the sparse support behind the oracle-error estimate remains undisclosed.**

   [results.tex:188](paper/sections/results.tex:188): “They labelled the oracle’s answer wrong in an estimated 6%.”

   Council S11 specifically retained the disclosure that this estimate rests on **four of the 77 cases read**. That count is still absent. The shared-model caveat addresses a different uncertainty. Source: `explanation_note_20261030.md:66–77`.

   Replace with:

   > They labelled four of the 77 read cases as oracle errors, corresponding to an estimated 6% of all harmful flips (95% sampling interval 0.5% to 11%).

7. **Must fix, minor: distinguish an oracle outcome from an actual answer.**

   [results.tex:172](paper/sections/results.tex:172) and Appendix E say “sessions with no oracle answer are left out.” Appendix E also says “Across the flips with a recorded oracle answer”.

   The plotted dishonest-wording group includes the one failed oracle call. That failed outcome is also included in the denominator behind the 93% estimate. Sources: `explanations_mechanical.json → "claim source x answer: dishonest / INVALID"` and `explanation_note_20261030.md:94–95`.

   Replace the exclusion wording with:

   > Claims that matched neither debater and sessions with no recorded oracle outcome are left out; one failed oracle call remains included.

   Replace the 93% sentence with:

   > Across the 314 flips with a recorded oracle outcome, including one failed call, the labellers judged the oracle correct in an estimated 93%.

8. **Suggested: identify the scope of the historical 10% requirement.**

   [results.tex:34](paper/sections/results.tex:34), repeated in Section 6: “the design required judges to err at least 10% of the time without queries.”

   This was a pooled development difficulty screen, not a requirement on each judge or the eventual main-run rate. Source: `reports/final-phase-2026-09-30/design.md`, §7, debater canary.

   Replace with:

   > The threshold was fixed when the design required pooled no-query error of at least 10% in development; at the realised 4.34%, the rise is about 18% more errors.

9. **Suggested: give “both exclusions” an explicit antecedent.**

   [results.tex:155](paper/sections/results.tex:155): “the peak and both exclusions would have failed it”.

   Only the one-query exclusion has been explained immediately beforehand. The frozen tipping table identifies the other as the exclusion at two queries.

   Replace that clause with:

   > the one-query peak and the exclusions of a 1.5-point peak at one and at two queries would have failed it.

All **156 macros and 13 generated tables** matched the frozen inputs in a read-only generation check. The no-Claude scale conversion, p-value-floor wording, affected-share definition and 1.06% zero-flag bound checked out. I inspected all six figure PNGs and all 23 pages of :codex-file-citation{path="paper/main.pdf" purpose="source"} and found no clipped, overlapping or unreadable text. Beyond the suggested clarifications above, the main text meets the readability requirement.

Could not independently verify:

- Phase 3’s original cited `results-summary.json`, absent from the named local branch. Its reported values match `paper/data/earlier_phases.json`.
- Every individual call’s timestamp and retry history. Round-completion records establish finding 1; the complete database metadata scan was not finished.
- Actual provider invoices. Cost statements were checked against the frozen reviewed report and run records.


