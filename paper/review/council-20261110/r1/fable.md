VERDICT: revise. The numbers and the registered conclusions hold up, but findings 1 to 6 must be fixed before the draft goes to the co-author or the public; every fix is wording, reporting or disclosure from the frozen files, and none needs a new outcome analysis.

CONFIDENCE: 88

FINDINGS:

Severity: blocker = fix before anyone builds the abstract or discussion on it; major = changes how a result is read; minor = wording or presentation. I checked claims against `paper/data/*.json`, the preregistration, its three amendments, `design.md`, `difficulty-next-steps.md` and `finalphase/judging.py`. I could not open per-session records.

1. [blocker] The "without the Claude judges" check is reported in a way that reverses its meaning for the headline, and registered sensitivity outputs are missing.
- Passages:
  - Section 4.3: "Without the three Claude judges the peak at one query is no longer statistically supported (Appendix A)."
  - Section 4.6: "two do not retain support for the finding ... it is not supported without the Claude judges".
  - Section 6: "the peak is not supported without the Claude judges."
  - Appendix A: "Table 5 lists the registered sensitivity analyses."
- Problem: `main_analysis.json` (`sensitivity.without_claude_judges`, also `reference/report_tables_20261029.md` section E) shows that only the fall from one query to six fails, and narrowly. On the registered five-judge scale (Llama constant):

  | Contrast, without Claude judges | Estimate (points) | p |
  |---|---|---|
  | One query vs none | +0.75 (+0.94 over the four judges with data) | 0.0001 one-sided |
  | One vs six | +0.29 | 0.028; Holm 0.056 |
  | Two vs none | +0.39 | 0.002 |
  | Six vs none (registered pooled secondary) | +0.46, interval +0.16 to +0.80 (+0.57 over four judges) | 0.006 two-sided, unadjusted |

- So the rise is intact and larger than the seven-judge 0.77, and in that panel six queries are worse than none. A reader, or the co-author, will take the three sentences to mean the one-query cost depends on Claude judges, which is the opposite.
- The pooled "no gain at six" (-0.05, 7 sessions) is +62 wrong verdicts for the three GPT-5.6 judges against -53 for Haiku.
- Preregistration 7.5 registers "the primary, and the two pooled secondary contrasts" for these panels and all six secondaries for the 806-question subset; Table 5 prints only yes/no and one p-value.
- On the 806 questions the one-query rise is 0.48 registered-scale (0.55 seven-judge scale) against 0.77 overall. Section 4.6 omits this while reporting the favourable two-query result.
- Fix, Section 4.3: "The registered check without the three Claude judges shows the same split. The rise from none to one is intact (0.94 points over the four remaining judges; p at most 0.0001). The fall from one to six is weak (0.36 points; p = 0.028, 0.056 after correction), so the registered peak is not supported in that panel. For those four judges error with six queries was 0.57 points higher than with none (p = 0.006, not adjusted). The pooled 'no difference at six' is therefore a balance of opposite movements, lower error for Haiku and higher error for the GPT-5.6 judges."
- Fix, Section 4.6: "... without the Claude judges the fall from one query to six is no longer supported, although the rise from none to one is (Section 4.3) ...". Add: "On the 806 questions the one-query rise is smaller (0.55 against 0.77 points) and still supported."
- Fix, Section 6: "The rise at one query appears in six of seven judges and survives leaving out the Claude judges. The fall by six does not: about two thirds of it is Haiku 4.5 and most of the rest Qwen3.8 (82 and 36 of the 122 net sessions)."
- Fix, Table 5: add the two estimates per row, rescaled to the judges that produced data, and a companion table of the registered secondary contrasts for each sensitivity panel.

2. [major] Cut-off replies are scored as errors and differ by condition; one stated per-judge result is probably this artifact.
- Passages:
  - Figure 3 caption: "the GPT-5.6 judges stayed elevated, while Haiku and Qwen3.8 fell below where they started."
  - Section 4.3: "while Haiku and Qwen3.8 dropped below their no-query level."
  - Section 4.1: "Error with six queries was 0.05 points lower".
  - Appendix A: "a seven-judge version of that check was not computed."
- Problem: `judging.py` parses a verdict only when the call status is "ok", so a reply cut off at the output limit is always an error. `main_readout.json` records cut-off verdicts (of 2,140 sessions each):

  | Judge | None | One | Two | Six | Debate and world | World only |
  |---|---|---|---|---|---|---|
  | Qwen3.8 | 19 | 6 | 2 | 1 | 12 | 6 |
  | Haiku | 3 | 0 | 0 | 0 | 0 | 0 |

- No other judge has any. `main_analysis.json` gives 57 unreadable verdicts among the seven judges (12,873 minus Llama's 12,816).
- Qwen3.8's drop from none to six is 13 wrong verdicts (57 to 44), smaller than its 18-session difference in cut-off replies. "Fell below where it started" is therefore not supported for wrong choices.
- Pooled, the six-versus-none difference is 7 sessions against a 21-session difference in cut-off replies (22 against 1), so its sign carries no information.
- The rise at one is, if anything, understated: an earlier review counted 24 unreadable verdicts at none and 9 at one.
- The one registered check built for this (unreadable as missing) is the one that could not be run.
- Limits of my check: the counts cover 1,070 questions, so up to four per condition may sit on the two set-aside questions.
- Fix, Section 3.5, after "counts as an error": "Unreadable verdicts were rare (57 of 89,712) but uneven. Nearly all were replies cut off at the output limit, mostly Qwen3.8's: 19 of its no-query sessions against 6, 2 and 1 with one, two and six queries."
- Fix, Figure 3 caption and Section 4.3: "... while Haiku fell below where it started. Qwen3.8's scored error also ended lower, but 19 of its no-query replies were cut off and scored as errors against 1 at six queries, which is more than the drop."
- Fix, Section 4.1: "... 0.05 points lower (7 of 14,952 sessions) ... Cut-off replies were more frequent with no queries (22) than with six (1), so the direction of this small difference should not be read into."
- Add the counts to the Appendix A sentence and to Section 6.

3. [major] The exploratory section's lead claim overreaches.
- Passages:
  - Section 4.5: "The main finding is that the oracle was rarely the problem (Figure 5a) ... Wrong oracle answers account for about 6%."
  - Figure 5a label: "Judge misread a correct oracle answer".
  - Section 4.5: "A mechanical check points the same way."
- Problems:
  - Same model grading itself. The labellers and the 16-case checker are Claude Opus 5.5, the oracle's own model. The cross-family reviewer (GPT-6 Astra, unnamed in the draft) re-read 21 cases including all 18 not in the 74% group, so at most 3 of the 56 "oracle correct" cases were seen by another family (`reference/explanation_note_20261030.md`, section 2).
  - Thin base. "About 6%" is 4 of 77 cases, interval 0.5 to 11%, which the main text does not give.
  - Mostly noise. Harmful flips are 331 against 216 helpful, so about two thirds of the cases read would be expected with no effect of the query. Haiku alone supplies 170 harmful and 141 helpful.
  - Near-default label. With cases selected for a wrong verdict, "correct answer, cited, wrong conclusion" is what one expects. The frozen `mentions_check` flag is true in 83% of harmful flips, 80% of helpful flips and 84% of pairs right both times; I could not verify its definition.
  - "Misread" suggests a comprehension error; pattern 1 is over-weighting a true but undiagnostic fact.
- Fix, replace the two sentences: "In most cases read, the labellers judged that the oracle's answer was correct and that the judge's stated reasoning used it for a conclusion it does not support (an estimated 74% of harmful flips; 95% sampling interval 66 to 83%). They judged the oracle wrong in 4 of the 77 cases (about 6%; interval 0.5 to 11%)."
- Fix, add two cautions: "The labellers are the same model as the oracle, so their judgment that an answer was correct is not independent of it; a reviewer from another model family re-read 21 cases, at most three of them from the largest group. And about two thirds of harmful flips are matched by helpful flips the other way, half of all harmful flips are Haiku's, and only harmful flips were read, so these shares describe wrong verdicts after a check, not the cause of the rise."
- Fix, relabel the Figure 5a bar: "Correct oracle answer used for an unsupported conclusion".
- Fix, replace "points the same way" with "is consistent with the first pattern", and give the denominators: 873 pairs resembled the dishonest debater's wording, 7,838 the honest debater's.

4. [major] The gate section does not tell the reader what the worst-case check can and cannot show, and omits the two facts that make the concern concrete.
- Passages:
  - Section 4.4: "The answer is very small ... far below the rates we measured."
  - Section 4.4: "as not robust to the rule-breaking queries it admitted".
  - Section 4.2: "Like the one-query finding, this comparison is not robust to the gate analysis".
  - Section 4.4 heading: "the gate let some rule-breaking queries through".
  - Section 3.4: "One deviation matters for reading the results."
- Problem (a), the check could not be passed. Pooled error in the debate arms is at most 5.1%, so no contrast can exceed about 5 points. The check allows shifts of 11 to 53 points at the audited rates, and 1.8 to 11 on the narrow reading (`amendment3_analysis.json`). Every confirmatory claim was bound to fail under all four audit readings whatever the data. "Not robust" is the label Amendment 3 prescribes, but repeated four times without this it reads as an empirical fragility result.
- Problem (b), exposure rises with budget. Flagged sessions go 11.4%, 17.9%, 33.2%; answer-revealing flags go 1.8%, 3.2%, 5.0%. Budget-to-budget comparisons are therefore the most exposed. Haiku supplies two thirds of the one-versus-six difference and has the highest flag rate (20% at one query, 57.5% at six; `gate_audit.json`). That link appears only across Section 4.3 and Appendix D.
- Problem (c), leakage is not special to re-hosting, and "some" understates it. The development gate admitted 7 false admissions in 100 by the coordinating session's audit, and 14 of the same 100 by the main-run labeller, against 10.7% in the main run. In the audit sample the classifier rejected 52 of the 193 claims the auditor labelled violating.
- Fix, replace the worst-case paragraph: "Before the run resumed we fixed a worst-case check. Suppose a rule-breaking query changed the verdict in some share of query sessions, always in the direction that weakens a finding. How large can that share be before the finding fails? About 0.25% of sessions for the one-query peak and 0.31% for excluding a 1.5-point peak. The audit flagged a query in 11 to 33% of sessions, so the check fails by a wide margin. It was bound to: error rates here are near 5%, so no difference between conditions could survive shifts that large. Failing it does not show that rule-breaking queries produced the pattern, or that they did not. Two facts make the concern concrete. Flagged queries were three times as common with six queries as with one, so comparisons between budgets are the most exposed. And Haiku, which supplies about two thirds of the one-versus-six difference, had the most flagged sessions (20% with one query, 57.5% with six). Screening was imperfect in development too (14 of 100 sampled admissions flagged by the same labeller), so this is a property of the gate and not only of its re-hosting. As fixed in advance, we report the findings as holding under this gate and as not robust to the measured rate of rule-breaking admissions."
- Fix, add to the audit paragraph: "The classifier rejected 52 of the 193 sampled claims that the auditor labelled as violating."

5. [major] Section 6 omits design-level limits that change how results should be read.
- Passage: the Section 6 list, especially "One benchmark, written and checked by models" and "rests on ... labels assigned by a model".
- Missing, with proposed bullets:
  - "Fixed transcripts. Debaters were told only that the judge might verify some claims; they did not know the budget and could not respond to an oracle answer. The results describe adding queries after a finished debate, not a debate in which both sides adapt to verification."
  - "Seven fixed judges. The intervals reflect which worlds were sampled, not which judges. The pooled mean weights judges equally in percentage points, so the one judge with many errors moves it most."
  - "No human labels, and overlapping roles. Answer keys, the oracle's reference answers, the gate audit and the explanation labels all come from models. Claude Opus 5.5 was the oracle, a judge, the gate-audit labeller and the explanation labeller. Claude Fable 5.1 wrote half the worlds, including each wrong answer and a note on why it is defensible, and was the dishonest debater in every debate, where it was given that note. Claude Sonnet 5.5 was the honest debater and a judge."
  - "The oracle is a model and is sometimes wrong (93% agreement with model-consensus references, exactly the minimum set in advance)."
  - "Questions were kept only if validator models reading the document answered them correctly, so low error with the document is partly built in."
- Also needed:
  - The coordinating sessions that wrote the amendments, the results report and the 16-case check were Claude Opus 5.5 (amendment headers); the draft's "we" and "session" hide this. Say once what a coordinating session is.
  - Table 14 lacks the explanation labellers, the cross-family reviewer, the oracle-reference readers (GPT-6.1 Sol, DeepSeek V4 Pro, Kimi K3) and the coordinating session.
  - Section 3.2 should mention the defensibility note; it is only in Appendix F.

6. [major] "Small" needs its base-rate context.
- Passages:
  - Section 3.5: "a peak smaller than 1.5 percentage points would be too small to matter in practice".
  - Section 4.1: "The observed peak is small."
  - Section 6: "The effect is small."
  - Section 3.2: "because it gives judges something to get wrong ... 22.9% with Sonnet on the honest side."
- Problem: the 1.5-point threshold was fixed in `design.md` on 2026-10-03, when the design required pooled no-query error of at least 10%, and kept when the development estimate was 22.9%. Realised error was 4.34%.
- In relative terms the rise is 115 extra wrong verdicts on 649 (18%), and 86 on 196 (44%) for the six judges other than Haiku.
- The 22.9% was 56 errors: 38 Llama's (46% error alone), 17 Haiku's and 1 Luna's (`difficulty-next-steps.md`, lines 629 to 650). Llama was then lost, and the main-run baseline fell below the project's own 10% difficulty checkpoint. The reader is told only not to compare.
- Fix, Section 4.1: "The rise is 115 more wrong verdicts out of 14,952, on a base of 649: about 18% more errors (44% more for the six judges other than Haiku). The 1.5-point threshold was fixed when the design required no-query error of at least 10%; the realised rate was 4.34%."
- Fix, Section 3.2: "Of the 56 errors behind the 22.9%, 38 were Llama 3.3 70B's and 17 Haiku's. Llama was later lost, and in the main run six of seven judges erred less than 3% of the time without queries."

7. [minor] The Section 4.2 heading promotes an unregistered, mostly one-judge comparison.
- Passage: "... and judges erred more when the debate came with it"; "Claude Haiku 4.5 shows it most strongly".
- Problem: Haiku is 211 of the 273 extra wrong verdicts (77%). The two arms also differ in system prompt and task framing.
- Fix: heading "Reading the document beat querying the oracle". Text: "About three quarters of this descriptive gap is Haiku; the two conditions also differ in the judge's instructions."

8. [minor] Statistical presentation.
- "p = 0.0002" sits at the bootstrap floor; write "p at most 0.0002, the smallest value the procedure returns".
- Figures 2 and 3 show intervals for levels, which overlap although the paired differences exclude zero. Add to both captions: "Bars are for each rate separately; differences between conditions are paired and more precise than overlapping bars suggest."
- An upper limit of "∞" on a percentage will confuse; write "no finite upper limit (too few errors)".
- Qwen3.8's result "does not survive the correction" only because the family still counts the two Llama tests that never ran; add "(p = 0.011 before correction, 0.055 after; the correction counts six tests, two of which could not be run)".
- Section 4.1's "the registered test" is the amended seven-judge panel; add "applied to the seven judges that produced data".
- Section 4.6's "unchanged" should be "the conclusions are unchanged" (estimates scale by 7/8).

9. [minor] Readability replacements.
- Section 4.1 paragraph 3: "The peak is small in absolute terms. Before the run we set 1.5 points as the smallest peak worth caring about, and the data as collected exclude one that large: allowing for the several comparisons, the two differences at one query are at most 1.19 and 1.24 points. That conclusion carries the same gate caveat as the peak itself (Section 4.4), so we cannot say how large the peak would be if every query had obeyed the rule."
- Section 4.3: replace "The first half of the pattern ... The second half" with "The rise from none to one ... The fall from one to six".
- Section 3.4: "it rejected only about two thirds of the queries the development gate had rejected, and on a second pass only about three quarters of those it had itself rejected the first time."
- Section 4.5: "sessions" should be "separate runs".
- Heading 4.1: "Offering one query raised error slightly; six queries brought no average gain".

10. [minor] Small disclosures and consistency.
- Appendix D: "488 rejections ... confirmed" against "478 of 487 correct rejections"; explain the one-item difference.
- Appendix D: "admitted 154 on its first pass and 60 on the repeat" needs denominators (the repeat covered 2,000 random requests plus every first-pass rejection, so the 60 are largely additional).
- Appendix E: "Two patterns recur" should give counts (the note has 16 of 26 and 13 of 23).
- Section 3.1 or Section 6: state that 262 of 1,068 questions drew two or more "another reading could reverse the answer" flags, 84 of them all four, and were kept.
- I found no numerical disagreement between text, tables, figures and frozen files.

STRONGEST COUNTERARGUMENT TO MY OWN VERDICT: The draft already discloses its three biggest weaknesses (the gate, dependence on Haiku and Qwen3.8, and the exploratory status of the explanation reading). Every number reproduces, and none of my findings changes a number or a registered decision. Several of my requested additions are themselves unadjusted or descriptive: the non-Claude six-versus-none contrast, relative effect sizes and the cut-off counts. A referee could call foregrounding them post hoc emphasis, and the cut-off tabulation comes close to the "no new outcome analysis" line. Adding five limitation bullets and a longer gate paragraph also cuts against the co-author's readability rule in a text that is already heavily hedged. Since this is a draft for a co-author who will write the discussion, one could approve it and let these points land there. I do not accept this because findings 1 and 2 are not extra caveats: they are places where the current sentences point the reader the wrong way, and the co-author's abstract will be built on them.

ANSWERS:
1. Claims and evidence: no wrong number found. Misleading or overstated: "not supported without the Claude judges" (the rise is intact and six is worse than none there); "Qwen3.8 fell below where it started" (cut-off replies); "the oracle was rarely the problem"; "small" without base-rate context; the second clause of heading 4.2. Causal wording is acceptable for "offering a budget under this protocol" but should say "offering".
2. Internal consistency: text, tables, figures and frozen files agree on numbers, denominators and pooling. Gaps: Table 5 against preregistration 7.5, 487 against 488, "unchanged" against the 7/8 scaling, "registered test" on the amended panel, and an incomplete Table 14.
3. Framing: heading 4.1 is fair on the rise but should say "offering" and "no average gain"; 4.2's second clause should not be a heading; 4.3 is fair; 4.4's "some" is mild; the title is fair. A hostile referee attacks first with "115 net verdicts, six of seven judges near ceiling, an all-model pipeline and a leaky gate". That lands on practical importance and generality, not on the existence of the rise under the protocol as run. The second attack, that the registered peak is essentially Haiku, lands and the draft half-concedes it.
4. Statistics: the primary test, the world bootstrap, the lost-judge handling and the simultaneous bounds are correctly described and legitimate. A statistician would reject four readings: failure of the worst-case check as evidence (it could not pass at these error rates); p = 0.056 as "not supported" without estimates; overlapping level intervals in Figures 2 and 3; and the 74% and 6% as if labels were truth. The text should also say the judges are treated as fixed.
5. Threats to validity, unacknowledged: fixed transcripts with no adaptation or rebuttal; Opus as oracle, judge, auditor, explanation labeller and coordinator; Fable as world author and dishonest debater, with its own defensibility note; no human labels; cut-off replies varying by condition; flag exposure tripling with budget; questions selected by document-reading validators; and a baseline below the project's own 10% checkpoint.
6. Exploratory section: mostly well hedged. Overreach is in "main finding ... rarely the problem", "misread" and "points the same way". Add the same-model caveat, the 4-of-77 basis, the noise share and the Haiku share.
7. Readability: the main text largely meets the rule. Unclear on first read: 4.1 paragraph 3, the 4.4 worst-case paragraph, "first half / second half" in 4.3, "kept ... rejections" in 3.4, and the asterisk and "∞" intervals. Replacements are in findings 4, 8 and 9.
8. Overall: revise. Required before sharing: findings 1 to 6. Then 7 to 10.
