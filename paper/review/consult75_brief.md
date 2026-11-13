# Consult 75: check of the revision that applies the second council's findings

You are an independent, adversarial reviewer. You have read-only access to this repository worktree
(`selvarath-debate-final-report`, branch `claude/final-report-paper`). Do not edit anything. Your reply is the review.

## Context

The project studies AI debate as an oversight protocol: an honest and a dishonest debater argue a question about a
fictional world document; a judge that cannot see the document picks an answer, in some conditions after asking a
limited number of oracle-verified factual queries. The preregistered final experiment is closed and its analysis
outputs are frozen. One author (with Claude) wrote the methods, results, limitations, figures and supplement of a
paper-style report under `paper/`; the other author will write the abstract, introduction, background and
discussion.

Two rules bind the text. The co-author's rule: the main text must be comfortably readable and must not read like a
statistics paper; heavier material belongs in the supplement. The owner's page budget: the author's part of the main
text (title block, Sections 3, 4 and 6) is to stay at about six pages, so the co-author has three of nine.

No frozen number may change and no new outcome analysis may be run.

## What changed

Commit `af4657e` is the draft that consult 74 (same model as you) passed with no required changes
(`paper/review/consult74_opinion.md`). A five-seat council from three model families then reviewed it
(`paper/review/council-20261112/synthesis.md`): five of five said "revise", with no wrong number, and listed 13
surviving findings, four debated disputes and some points raised by one seat each. The owner asked for the agreed
changes to be applied. The working tree holds that revision, uncommitted. See it with:

    git diff af4657e -- paper/sections paper/scripts paper/main.tex paper/generated

`paper/review/council-20261112/response.md` says, finding by finding, what was applied, what was not and why, and
lists every place where the main text now says less than before with where the full wording is. Read it first; it
is a claim to check, not a fact. `paper/main.pdf` was rebuilt (24 pages; the supplement grew by one).

In outline:

- Title: the working title is now "Limited verification in AI debate: a small error increase under an imperfect
  query gate" (still marked as a working title).
- Section 3 opening: "a dishonest debater expected to be stronger"; "the original design, hypotheses and analysis";
  "three amendments written before any result was seen".
- 3.1: validator flags defined, with 262 of 1,068. 3.2: direction of the debaters' settings. 3.3: the seven-judge
  panel was fixed by an amendment before any result was seen. 3.4: "bare label"; the oracle's development check is
  shorter; the gate rule's "counts as one fact" detail is replaced by a pointer to Appendix F; the time split now
  states what it implies for the comparisons. 3.5: cut-off counts are said to be over all 1,070 questions; one
  sentence removed (Appendix I has it); the estimand sentence carries the stability assumption and the registered
  words "anticipation, stopping and extra computation".
- 4.1: new heading and Figure 2 caption (descriptive; the sentence "3 to 5 queries were not tested" is restored);
  the bounds at two queries are mentioned; "ruling out a larger peak".
- 4.2: new heading; the built-in advantage, the different instructions and collection time, and the failed gate
  check are stated; one unconditional sentence and a pointer that led nowhere are gone.
- 4.3: Haiku supplies about a quarter of the rise; the declines from one query to six are given by size; all seven
  judges are placed above or below their no-query level; a caution for Haiku (no extended thinking, verdict-first
  format) joins the one for Qwen3.8; one sentence removed (Appendix I has it).
- 4.4: flags split into violating and borderline; the worst-case paragraph rewritten and shortened, with the
  amendment timing, the document comparison's limit (1.40%) and the sentence on verification that stays within the
  rule; the narrower category's definition replaced by a pointer to Appendix D.
- 4.5: interface caveat for the second pattern; reasons were requested after the verdict line; two sentences merged.
- 4.6: cites Appendices A and D. Section 6: deterrence consequence; pointer to Sections 3 and 4.
- Supplement: Appendix A (bound on cut-off verdicts; Qwen3.8's cut-off query steps; arithmetic for the flagged
  questions), Table 7 (two details), Table 9 caption and its paragraph, Appendix D (about a quarter; what the audit
  covers; amendment timing; "the robustness decision uses the audit's upper bound"), Appendix E (label wording; three
  facts about what was read), Table 15 (no empty cells), Appendix G (gate cost), Appendix H (validation wording),
  Appendix I (opening sentence; five bullets changed; one new bullet, "Questions flagged for another reading").
- `scripts/build_generated.py`: 16 new macros at the end, each read from the frozen files or computed from values
  already reported, with assertions for the claims the text makes in words. `generated/numbers.tex` gains those 16
  lines and nothing else changed in `generated/`.
- Two earlier review records gained a dated correction (`consult74_response.md`, `council-20261110/response.md`).

## Sources of truth

`paper/data/` (frozen outputs), `paper/reference/` (the reviewed 2026-10-29 report and the explanation note), and
the review record in `paper/review/`. The final-phase preregistration, amendments and code are not on this branch; a
frozen copy is in the sibling worktree `../selvarath-debate-llama-recovery` (`reports/final-phase-2026-10-03/`,
`finalphase/`, `analysis/infra/parsing.py`), if your sandbox can read it.

## What to do

A. Accuracy of every new or changed statement. Check each against the sources. In particular:
   1. 3.4 and Appendix I: "comparisons of a query condition with the no-query or document conditions largely cross
      that split ... comparisons among query budgets largely do not". Is "largely" right in both places, given
      Table 7's interruptions row?
   2. 4.3: 29 of 115; 0.79 of 0.82; "for five of the seven judges"; "no other judge moved by more than 0.3 points in
      either direction"; which judges are above and below their no-query level at six queries.
   3. 4.3, 4.5, Appendix E and Appendix I on Haiku and on what was read: check against `finalphase/judging.py`
      (the message that carries the oracle's result, and what happens after a failed query step),
      `finalphase/prompts_study.py` (the verdict format), `analysis/infra/parsing.py` (the parser) and
      `paper/reference/explanation_note_20261030.md` (what the labellers opened). Is anything said that these do not
      support?
   4. 4.4: is the rewritten worst-case paragraph still a correct account of the amendment's check and of the frozen
      results in `paper/data/amendment3_analysis.json`? Check "every session with a flagged query", the three limits
      (0.25%, 0.31%, 1.40%), "even an audit with no flags could not have cleared the one-query peak, or the ruling-out
      of a 1.5-point peak at one or two queries", and that the document comparison is not among those three.
   5. "Three amendments written before any result was seen" and "an amendment written before any result was seen
      made the other seven the primary panel"; Table 7's "before any result was computed" for the closing of the
      Llama recovery.
   6. Appendix A: the cut-off bounds and the sentence on what a cut-off query step costs. Appendix G: the account of
      the $8 and the $50.35. Table 15's new cells. Appendix H's new sentence (365 of 1,068).
B. The arithmetic on flagged questions (Appendix A, Appendix I): 53 of 115 net flips and 138 of 281 net errors are
   obtained by subtracting the registered 806-question estimates from the full ones. Is the arithmetic exact, and
   is it labelled in a way that stays inside "no new outcome analysis"? If you think it crosses that line, say so
   and say what should be removed.
C. Nothing lost. For each row of the table "what left the main text" in `response.md`, confirm the full wording is
   where it says. Then look for anything else that was in the main text at `af4657e` and is now nowhere.
D. Faithful application. For each surviving finding S1 to S13 and each debate outcome D1 to D4 in the synthesis,
   say whether the revision does what the council agreed, and whether any change went further than agreed. Say
   whether any of the author's choices on the undebated disputes is indefensible.
E. Main text read alone. A reader of the main text alone must still learn: that the result is small and below the
   practical-size threshold; that it is not robust to the gate, in both directions; that it rests on seven
   particular judges and that the return by six is not broad; that the explanation reading is exploratory and was
   labelled by the oracle's own model; what the comparisons do and do not measure, including the time split; and the
   three design limits in Section 6. Is each still stated plainly where it arises? Did any tightening make a
   sentence stronger than the fuller statement it replaced?
F. Numbers and references. Confirm that the 16 new macros reproduce from `paper/data`, that the 164 earlier macros
   and the 14 tables are unchanged, and that no macro or cross-reference is broken in the built PDF.
G. Layout and readability. Look at pages 1 to 7 and pages 21 and 23 of `paper/main.pdf`: anything clipped,
   overlapping or hard to read? Does the author's part still fit in about six pages? Is any new sentence hard on a
   first read?

## What counts as "must fix"

A factual error; a claim the sources do not support as worded; a misleading presentation; an internal
inconsistency; a missing caveat or disclosure that changes how a result should be read; a new outcome analysis; or
a breach of the readability rule that would stop a general ML reader. Matters of taste are "suggested" at most.

## Reply format

First line: `REQUIRED CHANGES` or `NO REQUIRED CHANGES`.
Then a numbered list of findings, most serious first, each with the file and quoted text, what is wrong, the source,
and exact replacement text, marked `must fix` or `suggested`.
End with a short list of anything you could not verify.
