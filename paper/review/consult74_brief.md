# Consult 74: check of a further cut to the main text (methods, results, limitations)

You are an independent, adversarial reviewer. You have read-only access to this repository worktree
(`selvarath-debate-final-report`, branch `claude/final-report-paper`). Do not edit anything. Your reply is the review.

## Context

The project studies AI debate as an oversight protocol: an honest and a dishonest debater argue a question about a
fictional world document; a judge that cannot see the document picks an answer, in some conditions after asking a
limited number of oracle-verified factual queries. The preregistered final experiment is closed and its analysis
outputs are frozen. One author (with Claude) wrote the methods, results, limitations, figures and supplement of a
paper-style report under `paper/`; the other author will write the abstract, introduction, background and
discussion.

The co-author's rule: the main text must be comfortably readable and must not read like a statistics paper; heavier
material belongs in the supplement.

## What changed

Commit `8370737` is the draft that consult 73 (same model as you) checked and passed with no required changes
(`paper/review/consult73_opinion.md`). The owner then asked for the author's part (title, Sections 3, 4 and 6) to be
cut to six pages, to leave three for the co-author inside a nine-page limit. The working tree holds that cut. See it
with:

    git diff 8370737 -- paper/sections paper/scripts paper/main.tex

(`paper/main.pdf` and the figures were rebuilt; the PDF is 23 pages and the main text now ends about halfway down
page 7, of which the co-author's placeholder headings take about 0.4 page.) What was done:

- Section 3.1: the sentence about the two set-aside questions is shorter.
- Section 3.2: the pointer to the complete prompt templates is dropped (Appendix F states it). The count of
  debates the honest side opened is replaced by a pointer to Appendix B ("Speaking order"). The paragraph on the
  role-break check is removed from the main text: Appendix B ("Role-break check") now carries the definition and
  the robustness pointer, and Section 4.6 names the check where it uses it.
- Section 3.3: the table of the six conditions is replaced by a sentence; the sentence on what the two
  world-document conditions tell us is dropped; "The debate is the same transcript wherever it is shown" is dropped
  (Section 3.2 says each debate was written once and shown unchanged); two paragraphs are merged.
- Section 3.4: the sentence that identical queries about a world reused a stored oracle answer is dropped
  (Appendix C states it).
- Section 3.5: the sentence on cut-off replies is shorter (the per-budget counts are in Appendix A); two paragraphs
  are merged; the sentence on intervals is shorter.
- Section 4, opening: shorter.
- The table of error rates and differences (Table 1, formerly in Section 4.1) is moved unchanged to the start of
  Appendix A. The main text now has no table; it points to that table twice, and every number in it that the
  argument uses is still quoted in the text of Sections 4.1 and 4.2.
- Figure 2 caption: two sentences dropped. Figure 3 caption: one sentence shortened, and the note on the asterisk
  now points to Appendix A. Figures 1 and 2 are set at 0.9 of the text width, and Figure 3 was regenerated at a
  smaller height (`make_figures.py`).
- Section 4.2: second paragraph shortened.
- Section 4.3: the paragraph on Qwen3.8's registered tests is shorter; the detail about the two Llama tests is now
  one sentence in Appendix A ("Secondary tests").
- Section 4.4: first paragraph shortened.
- Section 4.6: shortened. The two checks that do not support the finding are now named with pointers to
  Sections 4.3 and 4.4 and not restated; the sentence that the eight-judge rescoring gives the same conclusions by
  construction is dropped (Appendix A, "Seven judges instead of eight", has it).
- Section 6: the six bullets are replaced by one paragraph: a pointer to the caveats stated with each result, then
  three limits of the design. Appendix I still has the full list of 14. The placeholder note in Section 5 was
  reworded to match.
- Appendix A: the moved table and a sentence introducing it; one sentence added under "Secondary tests" and one
  under "Robustness checks" (the set-aside questions were debated and judged like the others).
- Appendix B: the role-break paragraph gained the definition of a role break and the pointer to the robustness
  table.

No frozen number may change and no new outcome analysis may be run. No number was changed in this cut.

## Sources of truth

`paper/data/` (frozen outputs), `paper/reference/`, and the review record in `paper/review/` (the council synthesis
and consults 68 to 73). The final-phase preregistration, amendments and code are not on this branch; a frozen copy
is in the sibling worktree `../selvarath-debate-llama-recovery` (`reports/final-phase-2026-10-03/`, `finalphase/`),
if your sandbox can read it.

## What to do

A. Nothing lost. For every sentence or table removed from Sections 3, 4 and 6 in the diff, say whether its content
   is still stated in the main text or the supplement, or was pure exposition that carried no claim or disclosure.
   Name anything that now appears nowhere and that a reader or the preregistration needs.
B. Faithful condensation. Check each shortened or rewritten passage against the supplement and the frozen data.
   Is any now stronger than the fuller statement it replaces, or missing a caveat that a reader of the main text
   alone would need in order not to be misled? A reader of the main text alone must still learn: that the result is
   small and below the practical-size threshold; that it is not robust to the gate, in both directions; that it
   rests on seven particular judges and that the return by six is not broad; that the explanation reading is
   exploratory and was labelled by the oracle's own model; what the comparisons do and do not measure; and the three
   design limits now named in Section 6 (one pairing with fixed debates, an all-model pipeline, overlapping roles).
   Section 6 no longer restates the first five; say whether each is still stated plainly enough where it arises.
C. The six conditions. Does the sentence that replaced the table in Section 3.3 say everything the table said?
D. Numbers and references. Confirm that no macro, table or cross-reference broke (the main text now has no table;
   Table 1 is in Appendix A), and that every number in the changed passages matches `paper/generated/numbers.tex`.
   Say whether moving Table 1 out of the main text leaves any claim in Sections 4.1 or 4.2 without its number or
   interval.
E. Figures. Look at `paper/figures/fig_error_by_judge.png` and pages 2, 4 and 5 of `paper/main.pdf`: is anything
   clipped, overlapping or too small to read after the resizing?
F. Readability of the changed passages on a first read.

## What counts as "must fix"

A factual error; a claim the sources do not support as worded; a misleading presentation; an internal
inconsistency; a missing caveat or disclosure that changes how a result should be read; or a breach of the
readability rule that would stop a general ML reader. Matters of taste are "suggested" at most.

## Reply format

First line: `REQUIRED CHANGES` or `NO REQUIRED CHANGES`.
Then a numbered list of findings, most serious first, each with the file and quoted text, what is wrong, the source,
and exact replacement text, marked `must fix` or `suggested`.
End with a short list of anything you could not verify.
