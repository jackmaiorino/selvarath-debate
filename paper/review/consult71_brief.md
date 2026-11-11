# Consult 71: adversarial review of the final report draft (methods, results, limits, supplement), round 3

You are an independent, adversarial reviewer. Act as the most demanding referee this paper could meet, and as a
sceptical co-author who will have to defend every sentence in public. Your job is to find what is wrong, overstated,
misleading, inconsistent or missing. You have read-only access to this repository worktree
(`selvarath-debate-final-report`, branch `claude/final-report`, cut from the frozen code commit 0e11907). Do not edit
anything. Your reply is the review.

This is round 3 of a loop. Two different fresh reviewers went before you: round 1 (consult 69) required 15 changes
and suggested 3; round 2 (consult 70) confirmed those resolved and required 4 more. The author has answered each one.
You have not seen the earlier rounds; you owe the earlier reviewers and the author nothing. The loop ends only when a reviewer finds nothing that must be fixed, so be thorough, and be honest in both
directions: do not hold findings back, and do not invent findings to fill a list.

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

## History and records

- `paper/review/consult68_opinion.md`: accuracy review of draft 1 (18 findings, all applied).
- `paper/review/consult69_opinion.md`: round 1 of this adversarial loop (15 required, 3 suggested).
- `paper/review/consult69_response.md`: the author's answer to each round 1 finding, with what changed and where.
  Two suggestions were adopted only in part (16: two p-values kept in the main text; 17: working title kept), and
  one required finding was applied with a stated departure (1: the two simultaneous bounds stay in the main text).
  Round 2 judged all three acceptable.
- `paper/review/consult70_opinion.md`: round 2 (4 required: the number of labelling sessions in the explanation
  reading, exact output formats in Appendix F, the gate deployment's cost disclosure, a clipped label in Figure 6).
- `paper/review/consult70_response.md`: the author's answer to each round 2 finding.

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
  sensitivity), `gate_audit.json`, `main_readout.json`, `gate_endpoint_comparison.json` (the equivalence check,
  added this round), and the exploratory explanation-reading files `explanations_*.json`.
  `paper/data/benchmark_facts.json` was computed for the draft from the main split.
- The earlier results report, already reviewed, and its exploratory addendum:
  `paper/reference/report_narrative_20261029.md`, `paper/reference/report_tables_20261029.md`,
  `paper/reference/explanation_note_20261030.md`.
- The protocol: `reports/final-phase-2026-10-03/main-preregistration.md`, `main-amendment-01-llama-access.md`,
  `main-amendment-02-gate.md`, `main-amendment-03-gate-adoption.md`, `difficulty-next-steps.md`, `pilot-reviews.md`,
  `reports/final-phase-2026-09-30/design.md`, `gate-qualification.md`, the prompts in
  `finalphase/prompts_study.py`, and the analysis code in `finalphase/`.
- The run's own records, if your sandbox can read them (say so if it cannot):
  `E:\finalphase-runs\final-phase-2026-10-03` (`main/analysis/`, `gate-audit-v1/`, `gate-endpoint-check-v1/`,
  `main/main_readout.json`, `coordinator/compliance_reading_20261029/`,
  `coordinator/explanation_reading_20261030/`, the benchmark's main split).
- Earlier phases, for Appendix H: `reports/2026-07-06-preliminary-findings.md`,
  `reports/2026-07-09-stage1-rejudge-results.md`, `reports/2026-08-11-phase2-main-results.md`, and for Phase 3
  `../selvarath-debate-v3-codex/reports/phase3-results-2026-09-11/results-summary.json`.

## What to do

A. Attack the current draft first, before you read the earlier opinions or the response. For every claim in
   Sections 3, 4 and 6 and in the supplement, ask what a hostile referee would say and whether the sources back the
   claim as worded. Look for: claims stronger than the evidence; a non-significant, descriptive or exploratory result
   worded as a finding; causal wording for a correlation; a result stated without the caveat that changes its
   reading; main text, tables, figures and supplement that disagree with each other; statements about process (what
   was frozen when, who or what read or labelled something, who was blind to what) that the records do not support;
   and required disclosures under the preregistration and its three amendments that are missing or softened.
B. Then check the author's response to consult 70, finding by finding, against the current text and the sources.
   For each: resolved, resolved with a new problem, or not resolved. You may also reopen anything from consult 68
   or 69 that you find was not in fact resolved.
C. Check the material that is new since draft 1 with particular care, above all what changed in round 2 (the four
   labelling sessions in Section 4.5 and Appendix E; the verdict, query and compliance formats in Appendix F; the
   cost sentence in Appendix D; the phases figure). Open the rendered figures (`paper/figures/*.png`) and, if you
   can, the compiled `paper/main.pdf`, and look for clipped, overlapping or unreadable text: a clean LaTeX build
   does not detect those. The rest of the newer material: the new gate paragraphs and the cross-table in
   Appendix D (`tab_equiv_decisions.tex`, the `equiv*`, `auditAdm*`, `auditRej*`, `pilotAdm*` macros) against
   `gate_endpoint_comparison.json`, `gate_audit.json` and the amendments; the answer-length paragraph in Appendix B
   against the preregistration; the validator-flag definition in Appendix A against the design; the prompts added to
   Appendix F, word for word against `finalphase/prompts_study.py`; the rewritten size paragraph in Section 4.1 and
   the gate paragraph in Sections 3.4 and 4.4; the rewritten Section 4.5 opening and the Figure 5 caption; and the
   Phase 2 wording, table label and figure label in Appendix H.
D. Numbers again. Recheck every macro and generated table against `paper/data/`, and every hand-typed number in the
   .tex files against its source. List any you could not verify.
E. Readability. Flag main-text passages (Sections 3, 4, 6) that are more technical than the co-author's rule allows
   or hard to follow on a first read, including any made worse by this round's caveats, and give replacement wording
   that stays faithful. Flag any simplification that changes the meaning.
F. Omissions. Is any result or caveat missing from the main text or from Section 6 that a careful reader needs in
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
Then a table for part B: consult 70 finding number, status (resolved, resolved with a new problem, not resolved),
note.
End with a short list of anything you could not verify from the files you could open.
