NO REQUIRED CHANGES

1. **Suggested: make the absence of queries explicit in both document conditions.**  
   [methods.tex:77](paper/sections/methods.tex:77):

   > “the debate with 0, 1, 2 or 6 oracle queries on offer; the debate together with the world document; and the world document alone, with no debate.”

   This preserves the six conditions but does **not literally say everything the deleted table said**: its two document-condition rows explicitly said “none” under oracle queries. The surrounding methods and supplement support that interpretation, so this is a clarity suggestion rather than a misleading omission requiring correction.

   **Source:** the conditions table at `8370737`; the retained collection chronology in Section 3.4 and Appendix B.

   **Exact replacement:**

   > Every judge saw every question in six conditions, twice, with the order of the two answers swapped: the debate with 0, 1, 2 or 6 oracle queries on offer; the debate together with the world document, with no queries; and the world document alone, with no debate or queries.

The deletion audit found no lost disclosure that requires restoration. The following accounts for the removed or replaced content in [methods.tex](paper/sections/methods.tex), [results.tex](paper/sections/results.tex) and [limits.tex](paper/sections/limits.tex). Appendix locations refer to [appendix.tex](paper/sections/appendix.tex).

| Removed or condensed content | Where it survives, or why removal is acceptable |
|---|---|
| **3.1:** two questions were still debated and judged | Appendix A, “Robustness checks,” lines 96–98. The main text retains their count, source defect, exclusion before the run and robustness-only use. |
| **3.2:** honest side opened 520 of 1,068 debates | Appendix B, “Speaking order,” lines 212–214, also supplies the counts by world author. |
| **3.2:** complete prompt templates are in the repository | Appendix F, lines 517–519, names the request-assembly code and introduces the verbatim prompts. |
| **3.2:** role-break definition, 2/6,420 flags and exclusion result | Appendix B, lines 216–221. Section 4.6 now identifies the check explicitly. The supplement also retains the classifier’s limited validation. |
| **3.3:** six-condition table and unchanged-transcript caption | The six arms, questions, answer orders and choices remain in 3.3; transcript reuse remains in 3.2; offered budgets and early stopping remain explicit. The “no queries” cells are the clarity point above. |
| **3.3:** document conditions show performance with direct access to evidence | Explanatory framing, expressed concretely by the comparisons and qualifications in 4.2. |
| **3.4:** identical queries reuse stored oracle answers | Appendix C, lines 262–264. |
| **3.5:** Qwen’s one- and two-query cut-off counts | Appendix A, lines 31–36, retains all condition counts. Main text retains the no-query/six-query contrast and its scoring caveat. |
| **3.5:** cut-off replies’ intended answers are unknowable | Explicit in Appendix A, lines 35–36. Main text still distinguishes registered errors from wrong choices among completed verdicts. |
| **3.5:** interval explanation and full-procedure/table pointer | The 95% intervals, whole-world bootstrap and clustering rationale remain in 3.5. Appendix A supplies the procedure, secondary comparisons and tables. Only redundant navigation was removed. |
| **4 opening:** “All rates below…” | Equivalent scope remains: analysed questions, averaged over seven judges unless one is named. |
| **4.1:** error-rate and contrast table | Table 1 in Appendix A. Numerical contents are unchanged; its caption is equivalently shortened. |
| **Figure 2:** document access produces fewer errors | Retained quantitatively in 4.2 and visually in Figure 2. |
| **Figure 2:** evenly spaced budgets; 3–5 were not tested | No longer stated in that sentence, but the labelled categorical axis and exhaustive condition list convey it. No interpolation claim is introduced. |
| **4.2:** “Descriptively, judges made more errors…” and expanded Haiku comparison | The same rates, unregistered status, Haiku share and instruction/text-length confounds remain. The removed sentence repeated the numerical comparison. |
| **Figure 3:** GPT judges remained elevated; Haiku/Qwen ended below baseline | Retained in 4.3. The reason for the unbounded interval remains in Appendix A, “Uncertainty.” |
| **4.3:** unavailable Llama tests and stricter correction | Appendix A, “Secondary tests,” lines 73–77. Main text retains Qwen’s failure to survive correction. |
| **4.3:** tests followed an earlier phase | That motivation is no longer explicit. Appendix H retains the earlier results. This is dispensable historical context; the registered comparisons and correction remain fully disclosed. |
| **4.4:** introductory explanation of the gate audit | The rewrite retains the failed agreement check, sampling, timing, auditor and blinding. Its causal framing agrees with amendment 3. |
| **4.6:** eight-judge rescoring gives the same conclusions by construction | Appendix A, “Seven judges instead of eight,” lines 87–94, explains the algebra and its limits. |
| **4.6:** fuller restatement of the two unsuccessful checks | Both remain named, with accurate pointers to the full explanations in 4.3 and 4.4. |
| **6:** small-effect bullet | Section 4.1 retains the absolute size, practical threshold, historical 10% requirement, relative increase and gate qualification. Appendix I retains the full limitation. |
| **6:** gate-sensitivity bullet | Section 4.4 retains sensitivity in both directions and explicitly distinguishes a worst-case bound from evidence that leakage caused the peak. |
| **6:** seven-judge bullet | Section 3.5 retains the fixed-panel scope; 4.3 retains error concentration, limited room for improvement, broad rise and uneven return. |
| **6:** pairing and fixed-debate bullet | Section 6 retains both; 3.2 retains exact roles, settings, verification warning and ignorance of the offered budget. Appendix I states the adaptation limitation explicitly. |
| **6:** model-built benchmark and overlapping-role bullet | Retained in the new paragraph, with authorship and validation details in 3.1 and the fuller list in Appendix I. |
| **6:** offered-budget interpretation and exploratory explanations | Retained plainly in 3.5 and 4.5, respectively. |
| Paragraph merges and discussion/limitations placeholder changes | No additional substantive content lost; the navigation matches the revised structure. |

The condensation remains faithful. In particular, a reader of the main text alone still receives every requested qualification:

| Required qualification | Main-text evidence |
|---|---|
| Small effect below the practical threshold | **4.1:** “The observed peak is small in absolute terms,” followed by the 1.5-point threshold and upper bounds. |
| Gate sensitivity in both directions | **4.1 and 4.4:** both the supported peak and exclusion of a practically large peak are qualified; “in either direction” remains explicit. |
| Seven particular judges; return by six is not broad | **3.5:** intervals concern sampled worlds, not judges. **4.3:** the heading, individual trajectories and no-Claude check explain the uneven return. |
| Exploratory explanations labelled by the oracle’s model | **4.5:** explicitly unregistered, designed after results, noncausal, and vulnerable to shared interpretive errors. |
| What comparisons measure | **3.5:** offering a budget, not an isolated additional oracle answer. **4.2:** the document comparison is unregistered and cannot isolate the dishonest debater’s contribution. |
| Three design limits | **6:** one pairing with reused debates, an all-model pipeline without human labels, and overlapping roles are all explicit. |

No shortened passage strengthens the supported conclusion. The changed prose remains readable for a general ML reader. Section 4.4 is still the densest passage, but its plain-language explanation makes the statistical caveat understandable. Neither the merged paragraphs nor the shortened Section 6 breaches the readability rule.

The numerical and reference checks passed. [numbers.tex](paper/generated/numbers.tex), generated tables and frozen data are unchanged from `8370737`. Read-only formatting of the frozen inputs reproduced **164 macros and 14 tables exactly**, with writes intercepted in memory. Literal counts in changed passages also agree with the frozen outputs and accessible preregistration.

All cross-references resolve, no duplicate labels or stale `tab:conditions` references remain, and **Table 1 appears on page 8 in Appendix A**. The existing build has no unresolved-reference markers.

Moving Table 1 leaves no inferential claim without the numerical support needed to interpret it. Sections 4.1–4.2 retain all six condition rates and six contrast estimates. The two one-query intervals, six-versus-none interval and document-versus-six interval remain explicit. Exact two-query interval endpoints are now supplement-only, but the main text retains their estimates, says both intervals include zero and gives corrected \(p=0.072\). Exact rate intervals remain available in Table 1 and are shown graphically.

The visual check passed for [fig_error_by_judge.png](paper/figures/fig_error_by_judge.png) and pages **2, 4 and 5** of :codex-file-citation{path="paper/main.pdf" purpose="source"}. No clipping, overlap or unreadably small labels were found. Figure 3’s shorter panels retain readable titles, ticks, intervals, baseline lines and asterisk. The PDF has 23 pages.

Could not verify:

- No outstanding verification limitation within A–F. I inspected the existing build without rebuilding it, edited no files and ran no new outcome analysis.


