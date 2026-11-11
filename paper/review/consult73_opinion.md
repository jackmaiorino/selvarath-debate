NO REQUIRED CHANGES

1. **Suggested: retain a short qualifier on the oracle’s development score.**  
   [methods.tex:117](paper/sections/methods.tex:117): “In a development check the oracle agreed with reference answers set by three other models on 93% of 300 queries…”

   This is accurate, but the condensation leaves the selection-sample caveat entirely in Appendix G. That appendix and `finalphase/oracle_select.py` establish that the same sample helped select the oracle. A short qualifier would prevent readers from treating 93% as independently measured deployment accuracy.

   Exact replacement:
   > In the 300-query development sample also used to select the oracle, it agreed with reference answers from three other models in 93% of cases, exactly the minimum fixed in advance. This is not an estimate of accuracy on the main run’s queries (Appendix G).

2. **Suggested: make the denominator behind “half” explicit.**  
   [results.tex:166](paper/sections/results.tex:166): “only harmful flips were read, about half of them Haiku’s”.

   “Them” could refer to the cases read or the full harmful-flip population. The intended population figure is 170 of 331, as stated in Appendix E and `paper/data/explanations_counts.json`.

   Exact replacement for that clause:
   > only harmful flips were read, and Haiku supplied about half of all harmful flips

3. **Suggested: simplify the longest sentence in Section 4.4.**  
   [results.tex:137](paper/sections/results.tex:137): “The verdict ‘not robust’ is set by the audit’s upper bound, and …”

   The sentence is correct, but combines the decision rule, sample size, minimum bound and three affected findings. Appendix D’s worst-case analysis supports this simpler replacement:

   > The robustness decision uses the audit’s upper bound. With 280 sessions per budget, even an audit with no flags would leave an upper bound of 1.06%. For the observed results, that exceeds the tipping points for the one-query peak and the exclusions of a 1.5-point peak at one and two queries, so all three would still fail this check.

| Consult 72 finding | Status | Note |
|---|---|---|
| 1. Collection chronology | Resolved | Methods, Appendix B and Appendix I now distinguish the majority collected after relaunch from earlier query sessions without oracle answers. The initial-launch log supports this correction. |
| 2. Missing secondary sensitivity results | Resolved | Table 7 supplies the evaluable contrasts; adjacent text accounts for both unevaluable Llama tests as \(p=1\) within each six-test correction. |
| 3. Decline versus return to baseline | Resolved | Section 4.3 explicitly distinguishes five judges declining from one to six from their positions relative to no queries. |
| 4. Repeat denominator and 488-item provenance | Resolved | The saved records confirm 488 items, including the malformed classifier item, and 357 repeat-set members. The text correctly reports 154/488 versus 60/357 and explains the selection difference. |
| 5. Explanation reviewer’s coverage | Resolved | Appendix E specifies 18 other-category cases plus three of the 56 largest-category cases, excluding the three unreadable-verdict cases. |
| 6. Sparse oracle-error estimate | Resolved | Both locations disclose four of 77 cases, the estimated 6%, and the 0.5–11% sampling interval. |
| 7. Oracle outcome versus answer | Resolved | Figure 5 and Appendix E explicitly include the failed call; the 93% sentence uses 314 recorded outcomes. |
| 8. Historical 10% requirement | Resolved | The wording correctly identifies a pooled development difficulty requirement. |
| 9. “Both exclusions” | Resolved | Section 4.4 identifies the exclusions at one and two queries. |

**Table 7 checks out.** Pooled estimates and confidence limits correctly multiply the frozen eight-judge contrasts by \(8/7\), matching the caption’s seven-judge scale. Per-judge estimates and all p-values remain unchanged. Preregistration §7.5 requires the six-test Holm correction used here. Qwen3.8’s one-query increase passes on the 806-question subset (\(p_{\mathrm{Holm}}=0.0105\)); with the two questions restored, it does not (\(0.0545\)). The accompanying statement is accurate.

**The relocation audit passed.** All eight entries in `consult72_response.md` are present where claimed:

- Development-run details and role-break readings: Appendix B.
- Oracle development sample, reference readers, selection and balance: Appendix G.
- Equivalence-check explanation: Appendix D.
- Query-use narrative: Appendix C.
- Gate figure and per-budget rates: Appendix D.
- Explanation figure, pattern counts, 1446 example, interface caution, overlap counts and cautions: Appendix E.
- All fourteen limitations: Appendix I.

Every main-text appendix pointer resolves to the promised material. I found no substantive disclosure lost from both main text and supplement through the trim. One pre-existing reporting detail remains unprinted: preregistration §7.6’s speaking-order counts by world author, 256/535 and 264/533. The aggregate 520/1,068 is retained; this does not change interpretation.

The requested condensed passages remain faithful. A main-text reader still learns the effect’s small absolute size and practical threshold, gate sensitivity in both directions, the fixed seven-judge scope and uneven return by six, the exploratory and same-model nature of explanation labelling, and the limits of the comparisons. Apart from the optional edits above, Sections 3, 4 and 6 meet the readability rule.

**The numerical audit passed.** All **160 macros and 14 generated tables** matched generation from the frozen inputs with writes intercepted in memory. Hand-typed numerical claims matched the accessible frozen data, preregistration, design documents, reference reports and run records. No new outcome analysis was run.

**The visual audit passed.** All 24 pages of :codex-file-citation{path="paper/main.pdf" purpose="source"} and all six figure PNGs were inspected. No clipped, overlapping or unreadable text, broken tables, or materially distant floats were found.

Could not independently verify:

- Phase 3’s original cited `results-summary.json`, absent at the stated branch/path. The manuscript matches `paper/data/earlier_phases.json`.
- Actual provider invoices. Cost statements match the frozen accounting records and reviewed report.


