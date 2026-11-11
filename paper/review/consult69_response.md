# Author response to consult 69 (round 1 of the adversarial review)

Each finding was checked against the cited source before any change. All 15 required findings were found correct and
applied. Two of the three suggestions were adopted in part, with reasons. Nothing was contested outright.

| # | Finding | Action | Where |
|---|---|---|---|
| 1 | Gate sensitivity also weakens the exclusion of a 1.5-point peak | Applied. The size paragraph now says the exclusion holds under the gate as it ran, is as sensitive to the gate as the peak, and that neither the presence nor the absence of a peak of practical size is established for verification within the rule. Section 4.4 now gives the 0.31% tipping point. First limitation extended. Departure from the proposed text: the two simultaneous bounds (1.19, 1.24) stay in the main text, because they are plain numbers a reader needs to see what "rule out" means. | `results.tex` 4.1 and 4.4; `limits.tex` |
| 2 | Phase 2 estimate is a post-hoc reweighting, not a corrected result | Applied. Bullet rewritten; JSON caveat replaced; table row labelled "Phase 2 (post-hoc reweighting)"; figure row carries "post-hoc reweighting" on its second line (the first line clipped). The as-run figure is now introduced as "as originally reported" (3.94, 2.21 to 5.67, the registered result at line 15 of the Phase 2 report; the erratum's recomputed realized-schedule interval is 2.24 to 5.67). | `appendix.tex` H; `data/earlier_phases.json`; both scripts |
| 3 | Failure to reproduce the old gate is not an established effect of re-hosting | Applied in all three places. Also removed "permissive gate" and "leniency" from Sections 4.4 and 4.6 in favour of "rule-breaking queries the gate admitted". Methods now gives both facts (about two thirds of the development gate's rejections kept; about three quarters of its own kept on repeat) and says the check showed neither equivalence nor a large difference. | `results.tex` 4.4, 4.6; `methods.tex` 3.4; `appendix.tex` D |
| 4 | Model reviewers where the prose implied human review | Applied to all four places, and to a fifth the review did not list: Section 4.5 said "We read the judges' written reasons"; it now names the Claude Opus 5.5 session and its written rubric. | `methods.tex` 3.2; `results.tex` 4.5; `appendix.tex` D, E |
| 5 | Document comparison does not isolate the dishonest debater | Applied. The subsection heading no longer says "the debate itself cost accuracy". | `results.tex` 4.2 |
| 6 | Paired verdict differences are not each query-caused | Applied. A fourth caution lists the unseparated parts of a query step. The subsection heading "Why one query hurt" became "What judges wrote in the cases that went wrong (exploratory)". | `results.tex` 4.5 |
| 7 | Answer-length balance not enforced | Applied as a paragraph in Appendix B with the preregistration's figures. | `appendix.tex` B |
| 8 | Gate reporting required by amendments 2 and 3 | Applied inside the paper, with no external artifact: a cross-table of the two gates' decisions (generated), disagreements by judge and by budget, the range across the 16 pilot worlds with the per-world counts in `paper/data/gate_endpoint_comparison.json` (a byte copy of the run's `comparison.json`, SHA-256 572462fe...be80), throughput, tensor parallelism 2, the audit's labels by gate decision, and the pilot-admission reference. All new numbers are generated macros. | `appendix.tex` D; `build_generated.py` |
| 9 | Explanation figure caption | Applied (reworded for reading level). Appendix E also states that panel (b) does not add up to all flips. | `results.tex` Fig. 5 caption; `appendix.tex` E |
| 10 | "Least ambiguous" is a validator-flag proxy | Applied in the results, in Appendix A (definition added) and in the table row. | `results.tex` 4.6; `appendix.tex` A; `build_generated.py` |
| 11 | Error is not the same as choosing the wrong answer | Applied. | `results.tex` 4.1 |
| 12 | Llama tests were also registered | Applied. | `results.tex` 4.3 |
| 13 | Difficulty checkpoint, not "not measurable" | Applied. | `appendix.tex` B |
| 14 | Appendix F was not the full prompts | Went further than proposed: Appendix F now also reproduces the two document-access judge system prompts, the rejection message, the gate and oracle request formats and the compliance-check prompt. Its opening and the methods sentence say that the assembling code is in `finalphase/prompts_study.py`. | `appendix.tex` F; `methods.tex` 3.2 |
| 15 | Phase 3 per-judge values double-rounded | Applied (5.28 in both places; JSON caveat only). | `data/earlier_phases.json` |
| 16 | Suggested: fewer "corrected p" in the main text | Adopted in part. Four of the six were removed (Sections 4.2, 4.3 twice, 4.6); each remains in an appendix table. The two for the registered primary test stay, the first now worded "p = 0.0002 after correcting for the two budgets tested", because the primary outcome is the one place a reader will look for a p-value. | `results.tex` |
| 17 | Suggested: title | Kept as a working title; the choice belongs to the two authors. The alternative is recorded as a comment above `\title` in `main.tex`. Finding 1's fix makes "fragile" explicit in both directions in the text. | `main.tex` |
| 18 | Suggested: nominal coverage of pooled audit bounds | Applied. | `appendix.tex` Table caption |

Consult 68 finding 14 (reported incomplete): tensor parallelism 2 added; the remaining reporting is covered by
finding 8 above.

After these changes the PDF is 19 pages and builds with no layout warnings. `generated/numbers.tex` and the tables
were regenerated; the new macros are `equiv*`, `auditAdm*`, `auditRej*` and `pilotAdm*`, and the new table is
`generated/tab_equiv_decisions.tex`.
