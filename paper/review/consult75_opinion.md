REQUIRED CHANGES

1. **Must fix, review record only:** [response.md:9](paper/review/council-20261112/response.md:9): “The 164 earlier macros are unchanged; **17 were added**.”

   Exactly **16** were added, giving 180 total. The diff and an in-memory reproduction of the generator agree.

   **Exact replacement:** “The 164 earlier macros are unchanged; 16 were added.”

2. **Suggested:** [results.tex:116](paper/sections/results.tex:116): “every session with a flagged query”.

   Amendment 3 §§4–5 count exposure to **admitted** flagged queries. The auditor also labelled rejected queries. The preceding paragraph supplies the intended restriction, so I do not consider this a material misstatement in context, but the definition should carry it explicitly.

   **Exact replacement:** “every session with an admitted query flagged by the auditor”.

3. **Suggested:** [results.tex:82](paper/sections/results.tex:82): “Both large declines need care.”

   The following Qwen evidence compares cut-offs at **no queries and six**, whereas “both large declines” refers to **one versus six**. Appendix A supports the narrower attachment used before this revision.

   **Exact replacements:** Replace “Both large declines need care.” with “Qwen3.8’s position below its no-query level needs care.” Replace “And Haiku ran without extended thinking” with “Haiku’s decline also needs care: it ran without extended thinking”.

**I found no required manuscript change or new outcome analysis.** The remaining checks support the revision:

- **Timing:** Both uses of “largely” are appropriate. Table 7 preserves early query-condition completions after an immediate stop or failed first step. Thus neither comparison class is wholly confined to its predominant collection period.
- **Judge arithmetic:** Haiku supplies 29/115, or 25.22%, of the rise. Haiku and Qwen contribute 0.7892 of the pooled 0.8155-point decline, correctly printed 0.79 of 0.82. Five judges decline; every other movement is at most 0.3 points. At six, Luna, Terra, Sol and Opus are above baseline; Haiku, Qwen and Sonnet are below.
- **Protocol and explanations:** The code supports the shared oracle-result/verdict-request message, requested verdict-first format, and subsequent verdict request after a failed query step. The parser and explanation note support the account of reading stored `REASONING` text. The revision correctly distinguishes requested order from observed compliance and avoids the council’s withdrawn claims about hidden reasoning.
- **Worst-case check:** The limits round correctly to 0.25%, 0.31% and 1.40%. The two-query practical-size exclusion tips at approximately 0.93%. The zero-flag upper bound is 1.0642%, exceeding the first three relevant thresholds but below the document comparison’s 1.40%. The paragraph correctly excludes the document comparison from the three findings that could not clear a clean audit.
- **Amendments:** Their records support the outcome-blind timing and seven-judge fallback. The reviewed report places recovery closure at 21:13:35 UTC and analysis beginning at 21:14, supporting Table 7.
- **Other additions:** The cut-off bounds and query-step consequences, $8 token-priced gate record versus approximately $50.35 posted deployment charges, new Table 15 cells, and 365/1,068 validator-flag count are supported.

The flagged-question arithmetic is exact after converting the registered estimates to counts using their respective denominators and the documented seven-judge adjustment:

| Quantity | Full set | Restricted set | Difference |
|---|---:|---:|---:|
| Net one-query rise | 115 | 62 | **53** |
| Six-query versus document gap | 281 | 143 | **138** |

These are decompositions of already frozen registered estimates, without new outcome processing or inference. The statements that this is arithmetic, not a separate test, and that the groups were not compared statistically adequately delimit the claims. **Neither number needs removal.**

All nine “what left the main text” entries retain their substantive content at the stated destinations: early completions in Table 7; the single-rule/list allowance in Appendix F; three reference models in Appendix G; the completed-verdict distinction in Appendix I; the practical threshold in §3.5; limited room to improve in Appendix I; narrow flag categories and averaging/upper-bound rules in Appendix D; and the scope of explanation readings in Appendix E. “Full wording” sometimes means equivalent substance rather than verbatim text. I found no additional substantive loss.

The council application is faithful:

| Item | Disposition |
|---|---|
| S1 | Applied: timing implications and conditional estimand. |
| S2 | Applied: narrower document comparison and its qualifications. |
| S3 | Applied: interface caveat and “bare label”. |
| S4 | Applied: flag categories, amendment timing, bounds, shorter explanation and compliant-verification caveat. |
| S5 | Applied: expected strength, original plan and amendments. |
| S6 | Applied: six described as close to baseline. |
| S7 | Applied: validator-flag definition and count. |
| S8 | Applied: rise/decline contributions and all seven baseline positions. |
| S9 | Applied: narrow Haiku disclosure and extra-computation wording. |
| S10 | Applied: deterrence consequence and corrected pointers. |
| S11 | Applied: untested budgets restored. |
| S12 | Applied: the listed consistency and disclosure repairs. |
| S13 | Applied: revised, explicitly provisional title. |
| D1 | Required quarter-share clause applied; optional additions reasonably omitted. |
| D2 | Agreed descriptive heading/caption applied. Changes to the title and §4.2 exceed that debate’s unanimous outcome but are supported by S13 and S2. |
| D3 | Accepted factual bullet retained without the withdrawn interpretations. |
| D4 | Accepted narrow disclosures applied; supplementary implementation details are supported. |

None of the author’s choices on the undebated disputes is indefensible. The additional supplementary disclosures are supported and do not introduce an unagreed substantive interpretation.

Read alone, the main text still plainly communicates the small absolute effect and practical threshold; gate sensitivity in both directions; fixed-panel scope and concentrated return by six; exploratory explanations labelled by the oracle’s model; offered-budget interpretation and timing assumption; and all three design limits. No tightening materially strengthens a conclusion beyond its support. Section 4.4 remains dense, but it does not breach the general-ML readability rule.

The generator reproduced **180 macros and 14 tables** in memory without writing files. The original 164 macros and all 14 tables are unchanged. One provenance qualification: the new `nFlaggedAny` macro uses the already reported, preregistered literal **703**, rather than obtaining that count from `paper/data`. All 88 cross-reference uses resolve, with no duplicate definitions or unresolved-reference markers.

I visually inspected pages **1–7, 21 and 23** of :codex-file-citation{path="paper/main.pdf" purpose="source"}. No clipping, overlap or unreadable material appeared. The PDF has 24 pages. An independent occupied-height estimate gives approximately **6.08 pages**, versus 6.06 before, supporting “about six pages”. The example split across pages 1–2 is mildly awkward but not a required repair.

Could not verify:

- Exact session counts on each side of the interruption without additional session-level processing.
- Actual compliance with verdict-first ordering, or whether any sampled explanation reached the parser’s 4,000-character storage limit. The revision does not claim these were measured.
- The author’s exact page-measurement script; the independent estimate above confirms the approximate budget.


