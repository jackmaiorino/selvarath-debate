# Consult 72: review of the revision made after the council (methods, results, limitations, supplement)

You are an independent, adversarial reviewer. You have read-only access to this repository worktree
(`selvarath-debate-final-report`, branch `claude/final-report`, cut from the frozen code commit 0e11907; the `paper/`
folder is uncommitted work). Do not edit anything. Your reply is the review.

## Context

The project studies AI debate as an oversight protocol: an honest and a dishonest debater argue a question about a
fictional world document; a judge that cannot see the document picks an answer, in some conditions after asking a
limited number of oracle-verified factual queries. The preregistered final experiment is closed and its analysis
outputs are frozen. One author (with Claude) wrote the methods, results, limitations, figures and supplement of a
paper-style report; the other author will write the abstract, introduction, background and discussion.

A stated requirement from the co-author: the main text must be comfortably readable and must not read like a
statistics paper. Plain rates, differences and 95% intervals in the main text; heavier material belongs in the
supplement. Do not ask for more statistical machinery in the main text.

## History

- Consults 68 to 71 (same model as you, earlier today): an accuracy review and a three-round adversarial loop that
  ended with "no required changes". Opinions and the author's answers are in `paper/review/consult6*` and
  `consult7*`.
- Then a five-model council (Claude Fable 5.2, Claude Opus 5.5, GPT-6 Astra, GPT-6.1 Sol, Gemini 4 Carbon) reviewed
  the same draft in two rounds and five debates. All five seats said "revise": no wrong number, but findings on
  interpretation, validity threats and disclosure. Its synthesis is
  `paper/review/council-20261110/synthesis.md`; the positions and debate turns are in the same folder.
- The author then revised the draft. The answer to the council, finding by finding, is
  `paper/review/council-20261110/response.md`. The draft as it stood before this revision is preserved as extracted
  text inside `paper/review/council-20261110/brief.md` (from the line "## The draft" to the closing fence), so you
  can compare old and new.

No frozen number may change and no new outcome analysis may be run. The author states that every number added in
this revision is read from the frozen files, is arithmetic on values already reported, or is typed from a cited
project document (listed at the end of `response.md`).

## What to review

- `paper/sections/methods.tex`, `paper/sections/results.tex`, `paper/sections/limits.tex`,
  `paper/sections/appendix.tex`, `paper/main.tex`.
- `paper/generated/numbers.tex` and `paper/generated/tab_*.tex`, written by `paper/scripts/build_generated.py` from
  `paper/data/*.json`. New this revision: the macros after `\pilotAdmFlaggedShare` (from `\relRise` to
  `\spendOther`) and the table `tab_sens_contrasts.tex`.
- `paper/scripts/make_figures.py` (Figure 5a's labels changed) and the rendered `paper/figures/*.png`; the compiled
  `paper/main.pdf` (23 pages).

## Sources of truth

- Frozen outputs in `paper/data/` (`main_analysis.json`, `amendment1_analysis.json`, `amendment3_analysis.json`,
  `gate_audit.json`, `main_readout.json`, `gate_endpoint_comparison.json`, `explanations_*.json`).
- `paper/reference/` (the reviewed results report and the explanation note).
- `reports/final-phase-2026-10-03/` (preregistration, three amendments, `difficulty-next-steps.md`,
  `pilot-reviews.md`), `reports/final-phase-2026-09-30/design.md`, and `finalphase/` (code, prompts,
  `oracle_select.py`, `judging.py`, `engine.py`, `gate_audit.py`).
- The run's records if your sandbox can read them: `E:\finalphase-runs\final-phase-2026-10-03`.

## What to do

A. Check every changed or added statement against its source. In particular:
   1. Section 3.4 and Appendix B: verdicts without queries were collected in the run's first round, before the gate
      pause, and every query-condition verdict after the run resumed. Is that exactly true, including retried calls?
   2. Section 3.4: the oracle's development sample (three reference readers, 93% at exactly the preset minimum, used
      to choose the oracle, balanced across judge families); judges saw only the one-word answer; identical queries
      reused stored answers.
   3. Section 3.5 and Appendix A: the counts of unreadable and cut-off verdicts, and what they are counted over.
   4. Section 4.1: "the design required judges to err at least 10% of the time without queries", the relative rise,
      and the wording about the floor of the p-value.
   5. Sections 4.3, 4.6 and 6 and Appendix A: the wording agreed in the council's debates D1 and D3 for the check
      without the Claude judges, and the new Table 6 (scale, p-values, which contrasts were registered for which
      panel).
   6. Section 4.4 and Appendix D: per-query flag rates, the definition of the affected share against amendment 3,
      section 5, the statement that an audit with no flags would still not have cleared three of the findings (the
      rule that assigns "not robust", the 1.06% bound, which findings), and the limiting comparison for the peak.
   7. Section 4.5 and Appendix E: the same-model caveat, the pattern counts, the reviewer's coverage, the interval
      method, the group sizes, and the selection cautions.
   8. Section 6: every limitation, sentence by sentence.
   9. Appendix G: the completed models table (output limits, reference readers, labellers) and the cost sentence.
B. Check that the council's surviving findings and debate outcomes were applied faithfully and that nothing the
   debates knocked down has crept back in (the withdrawn and conceded claims are listed in the synthesis).
C. Numbers: recheck every macro and generated table against `paper/data/`, and every hand-typed number in the .tex
   files against its source. List any you could not verify.
D. New errors: a statement stronger than its evidence, an internal inconsistency between main text, tables, figures
   and supplement, or a required disclosure under the preregistration and its amendments that this revision removed
   or softened.
E. Readability: this revision added caveats. Flag main-text passages (Sections 3, 4 and 6) that now break the
   co-author's rule or are hard to follow on a first read, with replacement wording that stays faithful. Section 6
   is a list of caveats for the co-author to merge; judge it for accuracy and clarity, not for length.
F. Look at the rendered figures and the compiled PDF for clipped, overlapping or unreadable text.

## What counts as "must fix"

A factual error; a claim the sources do not support as worded; a misleading presentation; an internal
inconsistency; a missing caveat or disclosure that changes how a result should be read; or a breach of the
readability rule that would stop a general ML reader. Matters of taste are "suggested" at most.

## Reply format

First line: `REQUIRED CHANGES` or `NO REQUIRED CHANGES`.
Then a numbered list of findings, most serious first. For each: the file and the quoted text, what is wrong, the
source that shows it, and exact replacement text. Mark each `must fix` or `suggested`.
End with a short list of anything you could not verify from the files you could open.
