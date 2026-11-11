# Consult 73: check of the corrected and trimmed draft (methods, results, limitations, supplement)

You are an independent, adversarial reviewer. You have read-only access to this repository worktree
(`selvarath-debate-final-report`, branch `claude/final-report`, cut from the frozen code commit 0e11907; the `paper/`
folder is uncommitted work). Do not edit anything. Your reply is the review.

## Context

The project studies AI debate as an oversight protocol: an honest and a dishonest debater argue a question about a
fictional world document; a judge that cannot see the document picks an answer, in some conditions after asking a
limited number of oracle-verified factual queries. The preregistered final experiment is closed and its analysis
outputs are frozen. One author (with Claude) wrote the methods, results, limitations, figures and supplement of a
paper-style report; the other author will write the abstract, introduction, background and discussion.

The co-author's rule: the main text must be comfortably readable and must not read like a statistics paper; heavier
material belongs in the supplement. Do not ask for more statistical machinery in the main text.

## History

- Consults 68 to 71 (same model as you): an accuracy review and a three-round adversarial loop.
- A five-model council then reviewed the draft and all five seats said "revise" on interpretation and disclosure
  (`paper/review/council-20261110/synthesis.md`; the author's answer is `response.md` in that folder).
- Consult 72 reviewed the post-council revision and required seven changes (`paper/review/consult72_opinion.md`).
- The author applied them and, at the owner's request, trimmed the main text from 10 pages to about 7.5 by moving
  material to the supplement. `paper/review/consult72_response.md` lists what was done for each finding, what moved
  where, and which sentences were newly written for the trim.

No frozen number may change and no new outcome analysis may be run.

## What to review

- `paper/sections/methods.tex`, `paper/sections/results.tex`, `paper/sections/limits.tex`,
  `paper/sections/appendix.tex`, `paper/main.tex`; the compiled `paper/main.pdf` (24 pages) and
  `paper/figures/*.png`.
- `paper/generated/numbers.tex` and `paper/generated/tab_*.tex`, written by `paper/scripts/build_generated.py` from
  `paper/data/*.json`. New since consult 72: `tab_sens_secondary.tex` and the macros `\detQwenHolm`,
  `\nOracleErrCases`, `\oracleErrorCI`, `\nAnsweredFlips`.

## Sources of truth

- Frozen outputs in `paper/data/`; `paper/reference/`; `reports/final-phase-2026-10-03/` (preregistration, three
  amendments, `difficulty-next-steps.md`, `pilot-reviews.md`); `reports/final-phase-2026-09-30/design.md`;
  `finalphase/` (code and prompts).
- The run's records if your sandbox can read them: `E:\finalphase-runs\final-phase-2026-10-03`.

## What to do

A. Consult 72's findings. For each of the nine: resolved, resolved with a new problem, or not resolved. Check the
   new Table 7 (`tab_sens_secondary.tex`) against `main_analysis.json` and preregistration section 7.5, including
   the scale used for the pooled rows and the statement about Qwen3.8.
B. The trim, part 1: nothing lost. Using the table in `consult72_response.md`, confirm that each item moved out of
   the main text is present in the supplement where the table says, that every main-text pointer to an appendix
   resolves to the right place, and that no disclosure required by the preregistration or its amendments now
   appears nowhere. If you find a statement that was in the main text before (the pre-council text is preserved in
   `paper/review/council-20261110/brief.md`, and consult 72's quotes show the post-council wording) and is now in
   neither the main text nor the supplement, say which.
C. The trim, part 2: faithful condensation. The main text now carries shorter statements whose detail is in the
   supplement. Check each newly written passage against the supplement and the sources: the Figure 1 caption's
   sentence about the gate; Section 3.2's pairing paragraph and role-break sentence; Section 3.4's oracle sentence
   and deviation paragraph; Section 4.4; Section 4.5; and the six bullets of Section 6. Is any of them stronger than
   the fuller statement it summarises, or missing a caveat a reader of the main text alone would need in order not
   to be misled? A reader of the main text alone must still learn: that the result is small and below the
   practical-size threshold; that it is not robust to the gate, in both directions; that it rests on seven
   particular judges and that the return by six is not broad; that the explanation reading is exploratory and was
   labelled by the oracle's own model; and what the comparisons do and do not measure.
D. Numbers: recheck every macro and generated table against `paper/data/`, and every hand-typed number in the .tex
   files against its source. List any you could not verify.
E. Readability of the trimmed main text (Sections 3, 4 and 6): flag anything hard to follow on a first read, with
   replacement wording that stays faithful.
F. Look at the rendered figures and the compiled PDF for clipped, overlapping or unreadable text, and for floats
   that land far from the text that discusses them.

## What counts as "must fix"

A factual error; a claim the sources do not support as worded; a misleading presentation; an internal
inconsistency; a missing caveat or disclosure that changes how a result should be read; or a breach of the
readability rule that would stop a general ML reader. Matters of taste are "suggested" at most.

## Reply format

First line: `REQUIRED CHANGES` or `NO REQUIRED CHANGES`.
Then a numbered list of findings, most serious first. For each: the file and the quoted text, what is wrong, the
source that shows it, and exact replacement text. Mark each `must fix` or `suggested`.
Then a table for part A: consult 72 finding number, status, note.
End with a short list of anything you could not verify from the files you could open.
