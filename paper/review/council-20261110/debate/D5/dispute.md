## The dispute

Finding 4 of Member A (severity given: major), part (a): what the gate's worst-case check can show.

Defender: Member A (author of the finding). Challenger(s): Member E, Member B.

What is in dispute: the statement that the worst-case check "could not be passed" and that "every confirmatory claim was bound to fail under all four audit readings whatever the data", and the proposed main-text sentence "It was bound to: error rates here are near 5%, so no difference between conditions could survive shifts that large". The other parts of the finding (exposure rising with the budget, leakage not being special to re-hosting) are not in dispute here.

## The finding as written in round 1 (Member A)

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

## The defender's own round-2 remarks on this finding (Member A)

Member E, finding 6(b): REFUTE: [the 4.4 replacement] It removes the 0.25% tipping point and the audited rates and keeps "cannot exclude", which reads as an empirical fragility finding. At error rates near 5% the check could not pass (the narrowest audit reading moves the one-against-six difference by 6.8 points, `amendment3_analysis.json`), and the reader needs the numbers to see that.
- Finding 4, one clause. I wrote that Haiku "had the most flagged sessions (20% with one query, 57.5% with six)". At one query Sonnet 5.5 had more (25% against 20%); Haiku leads only at two and six queries, and its answer-revealing flags (1, 1 and 3 of 40) are no higher than other judges'. The replacement paragraph should say "Haiku had the most flagged sessions at two and six queries (42.5% and 57.5%)" and add that the flag rate per admitted query is flat across budgets. The rest of finding 4 stands.

## Opening refutation(s) from round 2

Member E:

Member A, finding 4(a): REFUTE: “Every confirmatory claim was bound to fail … whatever the data” is too strong, particularly for excluding a practical-size peak under the narrower exposure bounds. Large exposure allowances make failure unsurprising for these observed contrasts, but that does not make the sensitivity calculation uninformative or illegitimate.

Member B:

Member A, finding 4(a): REFUTE: “Every confirmatory claim was bound to fail ... whatever the data” overgeneralizes from these results. In particular, ruling out a large peak depends on the direction and uncertainty of the contrasts; low condition error rates alone do not make that conclusion impossible under every exposure reading.
