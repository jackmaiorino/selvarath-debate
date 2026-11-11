# Consult 69: adversarial review of the final report draft (methods, results, limits, supplement), round 1

You are an independent, adversarial reviewer. Act as the most demanding referee this paper could meet, and as a
sceptical co-author who will have to defend every sentence in public. Your job is to find what is wrong, overstated,
misleading, inconsistent or missing. You have read-only access to this repository worktree
(`selvarath-debate-final-report`, branch `claude/final-report`, cut from the frozen code commit 0e11907). Do not edit
anything. Your reply is the review.

This is round 1 of a loop. The author will answer every finding (apply it, or contest it with evidence), and a fresh
reviewer will then check the answers and attack the draft again. The loop ends only when a reviewer finds nothing
that must be fixed. So be thorough now, and be honest in both directions: do not hold findings back, and do not
invent findings to fill a list.

## Context

The project studies AI debate as an oversight protocol: an honest and a dishonest debater argue a question about a
fictional world document; a judge that cannot see the document picks an answer, in some conditions after asking a
limited number of oracle-verified factual queries. The preregistered final experiment ("main") is closed and its
analysis outputs are frozen. Two people are writing a paper-style final report. One of them (with Claude) wrote the
methods, results, limits, figures and supplement, which are what you review. The other author will write the
abstract, introduction, background and discussion later; those files hold placeholders only.

A stated requirement from the co-author: the main text must be comfortably readable and must not read like a
statistics paper. Plain rates, differences and 95% intervals in the main text; the multiplicity procedure, bootstrap
details, sensitivity analyses and audit bounds belong in the supplement. Do not ask for more statistical machinery
in the main text. Do flag any place where the plain wording says something the frozen results do not support, hides
a caveat a careful reader needs, or is harder to read than it needs to be.

## History

- Consult 68 (same model, earlier today) reviewed draft 1 for accuracy: verdict "accurate with corrections", 15
  required and 3 suggested changes. Its opinion is `paper/review/consult68_opinion.md`. The author states that all 18
  were applied. Nobody has checked that.
- After consult 68 the author added Appendix H (the estimate in earlier phases: `fig_phases`, `tab_phases`,
  `paper/data/earlier_phases.json`, numbers entered by hand). It has never been reviewed.
- The layout then moved to the NeurIPS style and the co-author's placeholders moved into their own files. The author
  states no wording or number changed in that step.

## What to review

- `paper/main.tex` (title, structure), `paper/sections/methods.tex` (Section 3), `paper/sections/results.tex`
  (Section 4), `paper/sections/limits.tex` (Section 6), `paper/sections/appendix.tex` (supplement A to H).
- `paper/generated/numbers.tex` and `paper/generated/tab_*.tex` (every number macro and table, written by
  `paper/scripts/build_generated.py` from `paper/data/*.json`).
- `paper/scripts/make_figures.py` (six figures; rendered PNG and PDF copies are in `paper/figures/`; captions are in
  the .tex files).

## Sources of truth

- Frozen analysis outputs, copied byte for byte into `paper/data/`: `main_analysis.json` (registered eight-judge
  scoring), `amendment1_analysis.json` (the seven-judge primary panel), `amendment3_analysis.json` (gate
  sensitivity), `gate_audit.json`, `main_readout.json`, and the exploratory explanation-reading files
  `explanations_*.json`. `paper/data/benchmark_facts.json` was computed for the draft from the main split.
- The earlier results report, already reviewed (consult 65), and its exploratory addendum (consults 66, 67):
  `paper/reference/report_narrative_20261029.md`, `paper/reference/report_tables_20261029.md`,
  `paper/reference/explanation_note_20261030.md`.
- The protocol: `reports/final-phase-2026-10-03/main-preregistration.md`, `main-amendment-01-llama-access.md`,
  `main-amendment-02-gate.md`, `main-amendment-03-gate-adoption.md`, `difficulty-next-steps.md`,
  `reports/final-phase-2026-09-30/design.md`, `gate-qualification.md`, the prompts in `finalphase/prompts_study.py`,
  and the analysis code in `finalphase/` (for example `main_analysis_amendment3.py`).
- The run's own records, if your sandbox can read them (say so if it cannot):
  `E:\finalphase-runs\final-phase-2026-10-03` (`main/analysis/`, `gate-audit-v1/`, `main/main_readout.json`,
  `coordinator/explanation_reading_20261030/`, the benchmark's main split).
- Earlier phases, for Appendix H: `reports/2026-07-06-preliminary-findings.md` (pilot reanalysis, about line 30),
  `reports/2026-07-09-stage1-rejudge-results.md` (about line 14), `reports/2026-08-11-phase2-main-results.md` (about
  lines 15 and 113, and any erratum on the answer-position defect you can find under `reports/`), and for Phase 3
  `../selvarath-debate-v3-codex/reports/phase3-results-2026-09-11/results-summary.json`
  (`primary.D1`).

## What to do

A. Attack the claims first, before you read consult 68's opinion. For every claim in Sections 3, 4 and 6 and in the
   supplement, ask what a hostile referee would say and whether the sources back the claim as worded. Look for:
   claims stronger than the evidence; a non-significant or exploratory result worded as a finding; causal wording
   for a correlation; a result stated without the caveat that changes its reading; main text, tables, figures and
   supplement that disagree with each other; statements about process (what was frozen when, who was blind to what,
   what was decided before results) that the records do not support; and required disclosures under the
   preregistration and its three amendments that are missing or softened.
B. Then check consult 68's 18 findings one by one against the current text. For each: applied faithfully, applied
   but wrong or incomplete, or not applied. Flag any new error a correction introduced.
C. Appendix H. Check every number, date, judge, debater, question count and comparison in `earlier_phases.json`,
   `tab_phases.tex`, `fig_phases` and the Appendix H prose against the phase sources. Is each number the right
   headline for that phase (right contrast, right panel, corrected versus as-run)? Does the figure or its prose
   invite a reading the phases cannot support (for example a like-for-like shrinking effect), and is the stated
   caution enough? Is anything said about the earlier phases wrong or unfair?
D. Numbers again. `build_generated.py` and `make_figures.py` changed after consult 68 (new macros, the phases table,
   table headings, figure labels). Recheck every macro and generated table against `paper/data/`, including the
   seven-judge pooled rates derived as (8p - 1)/7 from the eight-judge pooled curve.
E. Hand-typed numbers. Find every number in the .tex files that is typed by hand and not a macro (counts, dates,
   percentages, costs, the equivalence table in Appendix D, the model settings table) and verify each one. List any
   you could not verify.
F. Figures. From `make_figures.py`, the captions and the PNGs if you can view them: does any figure mislead (axis
   ranges and breaks, the two vertical ranges in the per-judge figure, the unbounded interval for Opus with no
   queries, the mixture of census counts and sample estimates in the explanation figure, the phases figure)?
G. Readability. Flag main-text passages (Sections 3, 4, 6) that are more technical than the co-author's rule allows
   or hard to follow on a first read, and give replacement wording that stays faithful. Flag any simplification that
   changes the meaning.
H. The working title in `main.tex`: "Limited verification in AI debate: a small and fragile cost of offering one
   oracle query". Is it supported, and is "fragile" fair in both directions?
I. Omissions. Is any result or caveat missing from the main text or from Section 6 that a careful reader needs in
   order not to be misled (not merely nice to have)?

## What counts as "must fix"

A factual error; a claim the sources do not support as worded; a misleading presentation; an internal
inconsistency; a missing caveat or disclosure that changes how a result should be read; or a breach of the
readability rule that would stop a general ML reader. Matters of taste are "suggested" at most, and only if they
change meaning or ease of reading.

## Reply format

First line: `REQUIRED CHANGES` or `NO REQUIRED CHANGES`.
Then a numbered list of findings, most serious first. For each: the file and the quoted text, what is wrong, the
source that shows it, and exact replacement text. Mark each `must fix` or `suggested`.
Then a table for part B: consult 68 finding number, status (applied, incomplete, not applied), note.
End with a short list of anything you could not verify from the files you could open.
