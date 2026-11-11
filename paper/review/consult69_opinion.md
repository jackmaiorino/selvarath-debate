REQUIRED CHANGES

1. **Must fix: explain that the gate sensitivity weakens the exclusion of a substantial effect, too.**

   [results.tex:29](paper/sections/results.tex:29): “the data rule out a peak that large.” [limits.tex:7](paper/sections/limits.tex:7) likewise presents smallness without this qualification.

   This is supported under the implemented gate. However, the prescribed sensitivity also loses the exclusion of a 1.5-point peak, at common affected shares of 0.31% for one query and 0.93% for two. Both are below the audited exposure rates. The main text explains fragility of the positive finding but leaves this other direction implicit.

   **Source:** `amendment3_analysis.json`, tipping results; amendment 3 §5; the draft’s own `tab_tipping.tex`.

   **Replacement for the size paragraph:**

   > The observed peak is small. Before the run we set 1.5 percentage points as the smallest peak of practical interest. Under the gate as implemented, the 95% analysis rules out a peak that large. This exclusion also fails the gate sensitivity analysis: the results do not establish either the presence or the absence of a practically important peak under verification restricted to permitted queries.

   Retain the numerical simultaneous bounds in Appendix A. Add to the first limitation:

   > The exclusion of a practically important peak is itself sensitive to the gate assumptions.

2. **Must fix: Phase 2’s reweighting is not a corrected mirrored experiment.**

   [appendix.tex:481](paper/sections/appendix.tex:481): “Phase 2’s figure was corrected after the run.” The same description appears in `earlier_phases.json`.

   The source expressly rejects this wording. The displayed estimate is a post-hoc, assumption-based reweighting of observed answer-position groups. It cannot recover the missing opposite-position judgments. Its interval excludes uncertainty about those missing outcomes.

   **Source:** `reports/2026-08-11-phase2-main-results.md:107–140`, especially its explicit warning against “corrected.”

   **Replace the Phase 2 bullet with:**

   > Phase 2 did not implement its planned mirrored answer positions. The estimate shown is a post-hoc analysis that gives the two observed answer-position groups equal weight within each world. This reweighting cannot recover the missing judgments under the opposite answer position, and its interval does not account for those missing outcomes. The original as-run estimate was 3.94 points [2.21, 5.67].

   In the JSON caveat, replace “corrected after the run” with:

   > post-hoc side-standardized estimate after a defect in planned answer-position mirroring; assumption-based reweighting, not a corrected mirrored result

   Label the Phase 2 table and figure entry **“Phase 2: post-hoc reweighting.”** The numerical estimate, +4.56 [2.89, 6.25], is correct.

3. **Must fix: separate failure to reproduce the old gate from an established consequence of re-hosting.**

   [results.tex:111](paper/sections/results.tex:111): “the gate used in the main run was more permissive … To measure the consequence, we audited…”. [appendix.tex:180](paper/sections/appendix.tex:180) draws the same general conclusion.

   The replacement rejected fewer queries on the archived sample, but its own repeat decisions were unstable. Amendment 3 explicitly says neither equivalence nor a large deployment difference was established. The main audit measures rule-breaking admissions under the deployed gate; it does not estimate the change caused by re-hosting.

   **Source:** amendment 3 §1; `gate-endpoint-check-v1/comparison.json`.

   **Replace the results opening with:**

   > As Section 3 explains, the main gate did not reproduce the development gate’s decisions closely enough to pass the prespecified check. We audited rule-breaking admissions in the main run using a sample of 840 judge sessions.

   **Replace the appendix conclusion with:**

   > The deployment rejected fewer queries on this archived set, but its decisions were also unstable on repeat testing. The check established neither equivalence nor a large deployment difference.

4. **Must fix: identify model reviewers where the prose implies human review.**

   [methods.tex:68](paper/sections/methods.tex:68): “We read both”; [appendix.tex:177](paper/sections/appendix.tex:177): “a hand audit”; lines 220 and 295: “labelled by hand” and “hand labels.”

   The records identify Claude sessions as these reviewers. The distinction matters because the gate calibration and compliance adjudication otherwise appear to have human reference labels.

   **Sources:** `pilot-reviews.md`; the actual `coordinator/compliance_reading_20261029/reading.md`; `explanation_note_20261030.md` §§2–3.

   **Use these replacements, respectively:**

   > A coordinating Claude Opus 5.5 session read both flags and a fixed sample of 20 unflagged turns.

   > a coordinating Claude session’s audit of the pilot

   > labelled by the coordinating Claude session

   > the model readers’ case labels

5. **Must fix: the document comparison does not isolate the dishonest debater’s contribution.**

   [results.tex:61](paper/sections/results.tex:61): “a persuasive dishonest debater cost judges accuracy even with the evidence in front of them.”

   The comparison adds the complete debate, including both sides and a different instruction context. There is no honest-only or dishonest-only condition. The descriptive disclaimer does not establish the more specific attribution that follows it.

   **Source:** main preregistration §4; the six conditions implemented in `finalphase/judging.py`.

   **Replacement:**

   > This comparison was not a registered test. Descriptively, judges made more errors when the complete debate accompanied the document. The design does not isolate the dishonest debater’s contribution to that difference.

6. **Must fix: do not identify every paired verdict difference as query-caused.**

   [results.tex:148](paper/sections/results.tex:148): “So one query changed verdicts in both directions, somewhat more often for the worse.”

   These are separate executions matched on judge, question and answer order. Their aggregate difference estimates the budget contrast, but individual discordant pairs can also reflect ordinary execution variability. Reading only harmful pairs does not identify what caused the net increase.

   **Sources:** main preregistration §2; `explanation_note_20261030.md`, “Short answer” and §1.

   **Replacement:**

   > The two conditions produced different verdicts in both directions, with 115 more right-to-wrong than wrong-to-right pairs. Reading the harmful pairs characterizes those cases; it does not identify what caused the net increase.

   Add to the explanation cautions:

   > Query writing, retries, additional deliberation and oracle information were not separated.

7. **Must fix: include the disclosed answer-length imbalance.**

   [methods.tex:7](paper/sections/methods.tex:7) says Appendix B “lists every departure,” but the paper omits the planned answer-length balance that the split did not enforce.

   **Source:** main preregistration §3, lines 49–51.

   **Add to Appendix B:**

   > The planned answer-length balance was not enforced by the split. The keyed answer was longer in 478 of 1,068 counted questions (44.8%), just below the planned 45–55% range; excluding 65 ties, it was longer in 47.7%. This was disclosed before the main run, and the split was unchanged.

8. **Must fix: finish the gate reporting explicitly required by the amendments.**

   [appendix.tex:171](paper/sections/appendix.tex:171), “Equivalence check,” and line 211, “Audit,” still omit required outputs.

   Amendment 2 §3.6 requires the decision table, disagreements by judge/budget/world, and throughput. Amendment 3 §§4 and 8 additionally require the audit’s labels by gate decision and the pilot-admission reference. Tensor parallelism 2, explicitly requested by consult 68, is also absent.

   **Sources:** those amendments; actual `gate-endpoint-check-v1/comparison.json`; frozen `gate_audit.json`.

   A linked supplementary artifact can carry the detailed disagreement tables. Add this sentence with an actual artifact link:

   > The complete decision table, disagreements by judge, budget and world, and throughput are provided in the supplementary equivalence-check artifact.

   Replace the hardware phrase with:

   > two NVIDIA B200 GPUs, NVFP4 quantisation and tensor parallelism 2

   Add the audit disclosure:

   > Among the 1,982 distinct admitted main-run requests, 141 were labelled violating and 71 borderline (10.7% combined). Among 54 rejected requests, 52 were labelled violating, one admissible and one borderline. Of the pilot gate’s 100 sampled admissions, ten were labelled violating and four borderline (14% combined). The pilot reference uses different questions and judges and does not establish a change in the main-run violation rate.

9. **Must fix: make the explanation figure’s provenance and exclusions accurate in its caption.**

   [results.tex:137](paper/sections/results.tex:137): “Estimated reasons … from 77 cases read.”

   Two rows instead use complete counts. The 17 no-answer cases were specifically not read. Panel B also omits unmatched claims and no-answer sessions, so its bars do not account for all 331 harmful and 216 helpful pairs. Appendix E explains part of this correctly, but the main caption remains misleading.

   **Sources:** `make_figures.py`, `explanations_counts.json`, `explanations_summary.json`, and `explanations_mechanical.json`.

   **Replacement caption:**

   > Exploratory reading of judge explanations. (a) Mechanism shares among the 331 harmful flips, estimated from 77 cases read; the no-answer and unreadable-verdict rows are complete counts. Lines show 95% sampling intervals conditional on the labels; no reliable interval is available for “unclear.” (b) Verdict flips grouped by whose wording the checked claim most resembled. Unmatched claims and sessions without an oracle answer are omitted.

10. **Must fix: define the “least ambiguous” subset as a validator-based proxy.**

    [results.tex:183](paper/sections/results.tex:183) and `tab_sensitivity.tex`: “806 least ambiguous questions.”

    Neither the main text nor supplement defines this selection. It means zero or one reversing-reading flags among four validator responses. It is not an independently verified ordering of ambiguity.

    **Sources:** original design §5; main preregistration §§3 and 7.5.

    **Replacement in the results:**

    > On the 806 questions with at most one validator flag for an alternative reading, the peak at two queries was also supported.

    **Add to Appendix A:**

    > Each of the four validator responses also recorded whether another reading consistent with the document could reverse the answer. These flags were recorded rather than used to reject questions. The restricted analysis retained the 806 questions with zero or one flag.

    Label the table row **“Validator flags ≤1: 806 questions.”**

11. **Must fix: distinguish scored errors from explicit wrong-answer selections.**

    [results.tex:19](paper/sections/results.tex:19): “judges chose the wrong answer 4.34% of the time.”

    The rate includes unreadable verdicts. There are 24 such verdicts at budget zero and nine at budget one. Explicit wrong-answer selections are approximately 4.18% and 5.05%, respectively. The registered error rates are correct; this sentence misnames them.

    **Sources:** preregistration §7.1; `finalphase/main_analysis.py`; actual counted records in `main/judgments_open.jsonl`.

    **Replacement:**

    > With no queries, judge error was 4.34%. With one query offered, it rose to 5.11%.

    Keep the existing definition that unreadable verdicts count as errors.

12. **Must fix: Qwen was not the only judge with registered individual tests.**

    [results.tex:89](paper/sections/results.tex:89): “The only per-judge tests we registered were for Qwen3.8.”

    Two Llama tests were also registered and retained as untested within the correction. Appendix A correctly says so.

    **Source:** main preregistration §7.3; current Appendix A, lines 47–50.

    **Replacement:**

    > Among the judges that produced data, only Qwen3.8 had registered per-judge tests, to follow up an earlier phase. The corresponding Llama tests could not be run.

13. **Must fix: a design stopping threshold is not a statistical impossibility.**

    [appendix.tex:132](paper/sections/appendix.tex:132): “too few errors without queries for an effect to be measurable (under 10%).”

    The sources establish a predeclared difficulty checkpoint, not that effects cannot be measured below 10% baseline error. The final experiment itself measures an effect below that baseline.

    **Sources:** original design §7; `difficulty-next-steps.md`; main preregistration §1.

    **Replacement:**

    > The original protocol used the same model on both sides of each debate and stopped at its predeclared difficulty checkpoint because error without queries was below 10% for both self-play families.

14. **Must fix, minor: Appendix F does not contain the “full prompts.”**

    [methods.tex:58](paper/sections/methods.tex:58): “The full prompts are in Appendix…”

    Appendix F omits, among other things, the two document-access judge system prompts, user-message templates, and rejection messages. Those differences matter to reproducing the conditions.

    **Source:** `finalphase/prompts_study.py`, particularly `JUDGE_SYSTEM_DEBATE_WORLD`, `JUDGE_SYSTEM_WORLD`, `debater_user`, `REJECTION`, `GATE_USER`, and `ORACLE_USER`.

    **Replacement:**

    > Selected prompts appear in Appendix F; the complete prompt templates are in `finalphase/prompts_study.py`.

    Replace Appendix F’s opening with:

    > Selected system prompts and query instructions are reproduced below, with formatting simplified.

15. **Must fix, minor: two Phase 3 metadata values have been double-rounded.**

    [earlier_phases.json:58](paper/data/earlier_phases.json:58): “Qwen3.8 +5.29 … Llama … 5.29.”

    Both underlying values are 5.2845528455 percentage points, which round directly to 5.28. This affects the JSON caveat, not the displayed pooled Phase 3 estimate.

    **Source:** external Phase 3 `results-summary.json`, individual-judge D1 results.

    **Replacement:**

    > by judge: Qwen3.8 +5.28 [2.54, 8.23], Llama 3.3 70B +2.34 [-0.61, 5.28]

16. **Suggested: reduce the main text’s repeated testing notation.**

    [results.tex:23](paper/sections/results.tex:23) repeatedly appends “corrected \(p\)” to already explained comparisons. This is accurate, and does not itself stop a general ML reader, but the accumulation works against the co-author’s requested style.

    **Source:** the stated writing rule; Appendix A already supplies the testing details.

    **Replacement for the primary comparison paragraph:**

    > Offering one query raised error by 0.77 percentage points compared with none (95% interval 0.43 to 1.10), and by 0.82 points compared with six (0.49 to 1.16). The registered test supports both comparisons; it does not directly compare one query with two. At two queries, the differences were smaller and their intervals included zero, so a peak at two was not established.

    The other main-text corrected-\(p\) parentheticals can likewise remain in the supplement.

17. **Suggested: make “fragile” refer unmistakably to the evidence.**

    [main.tex:33](paper/main.tex:33): “a small and fragile cost of offering one oracle query.”

    “Small” is supported for the observed effect. “Fragile” is defensible for its sensitivity to the gate assumptions and panel composition. It should not imply that repairing the gate would remove the cost, or that a large permissible-verification effect has been robustly excluded. Finding 1 is the necessary substantive repair.

    **Possible replacement title:**

    > Limited verification in AI debate: a small error increase under an imperfect query gate

    The current title can remain if its interpretation is made explicit in the main text.

18. **Suggested: preserve the nominal-coverage qualification for pooled audit bounds.**

    [appendix.tex:225](paper/sections/appendix.tex:225): “Upper bounds are one-sided 95% (Clopper-Pearson).”

    Amendment 3 notes that pooling across judges can slightly under-cover when their rates differ. This does not change the reported sensitivity conclusion, but the supplement should preserve the qualification.

    **Sources:** amendment 3 §4; `finalphase/gate_audit.py:21–26`.

    **Replacement:**

    > Upper bounds are nominal one-sided 95% Clopper-Pearson bounds on the pooled counts. Coverage can fall slightly below 95% when judges differ; the bounds are not simultaneous and do not account for labelling error.

The numerical audit otherwise passes: **96 macros and 11 generated tables** reproduce from the supplied JSON. The frozen analysis/readout/audit/explanation copies match their accessible `E:` originals. The seven-judge transformation is valid for estimates and interval endpoints because Llama contributes constant error in every draw. The benchmark summaries reproduce from the underlying questions and worlds. The handwritten model settings, equivalence-table entries, recorded costs, counts and chronology agree with their sources.

All six PNGs were inspected. No finite interval is clipped. Haiku’s different vertical range uses the same physical height per percentage point as the other judges; Opus’s unbounded interval is explicitly marked. Those treatments do not require changes. Appendix H’s headline estimates, panels, counts and dates check out apart from the interpretation and metadata issues above. Its existing warning against treating the phases as repeated comparable experiments is appropriate.

Consult 68 reconciliation:

| Finding | Status | Note |
|---|---|---|
| 1 | applied | No-difference wording and directional interval corrected. |
| 2 | applied | Contrast shifts and per-condition exposure denominator specified. |
| 3 | applied | Document-versus-six result now has its gate caveat. |
| 4 | applied | Oracle qualification uses model references and discloses unresolved cases. |
| 5 | applied | No word cap distinguished from unequal token ceilings; confounding disclosed. |
| 6 | applied | M3 label now includes overridden/reinterpreted answers. |
| 7 | applied | Requested appendix disclosure is present; the main caption still needs finding 9 above. |
| 8 | applied | Gate categories include unclassified flags and broader restrictions. |
| 9 | applied | Main and pilot audit items correctly separated. |
| 10 | applied | Development rates explicitly world-weighted. |
| 11 | applied | Bounds now identified as confidence bounds; finding 1 adds the missing sensitivity qualification. |
| 12 | applied | Undefined unreadable-as-missing check and missing-cell codings disclosed. |
| 13 | applied | Second implementation’s dependence on the first core correctly described. |
| 14 | incomplete | Tensor parallelism 2 remains absent; further required gate reporting is identified in finding 8. |
| 15 | applied | Low baseline error limits improvement only. |
| 16 | applied | One-versus-two is explicitly not established by the primary test. |
| 17 | applied | Infinite and unavailable intervals distinguished. |
| 18 | applied | All four corrections present: order assignment, empty verdicts, oracle outcomes, and 17/19 flips. |

I found no substantive new numerical error introduced by the applied consult 68 corrections.

Remaining verification limits:

- Provider charges match the recorded report figures, but I did not reconcile provider statements or resolve the approximately 17% billing discrepancy.
- I checked the explanation estimates against their frozen labels and verified the quoted C67 example, but did not independently relabel all 77 cases.
- I could not establish that the NeurIPS migration changed no wording without a preserved pre-migration draft. This review checks the current text directly.
- The specified `E:` run records and external Phase 3 results were accessible.


