# Author response to consult 70 (round 2 of the adversarial review)

Each finding was checked against the cited source before any change. All four required findings were found correct
and applied. Nothing was contested.

| # | Finding | Action | Where |
|---|---|---|---|
| 1 | Four labelling sessions, not one | Applied. Section 4.5 now says four Claude Opus 5.5 sessions read the 77 cases and labelled each once under a written rubric; Appendix E says each case was labelled by one of four sessions, each given a quarter of the cases (run record `coordinator/explanation_reading_20261030/README.md`). | `results.tex` 4.5; `appendix.tex` E |
| 2 | Appendix F altered exact output formats while claiming verbatim text | Applied. The verdict and compliance formats are back on separate lines, the query instruction shows the literal backticks and the trailing space in `CLAIM: `, and the opening sentence now says which breaks are compressed and which are kept. Checked in the compiled PDF's extracted text. | `appendix.tex` F |
| 3 | Gate deployment's cost bound and posted charges missing | Applied: recorded bound $178.98, of which $13.98 was the equivalence check; posted charges about $50.35 over the deployment's lifetime, stated as derived from the credit balance and recorded serverless spending, not from an itemised invoice (reviewed report tables, line 251). | `appendix.tex` D, "Deployment and reset" |
| 4 | Figure 6 clipped the Phase 2 qualifier | Applied. "post-hoc reweighting" is on its own line under the Phase 2 label, the figure is taller, and the PNG and the compiled page were both inspected: nothing is clipped. | `make_figures.py` `fig_phases` |

Verification limit raised by the reviewer: the pilot's month. The upstream repository's commit history has "generated
debates and judgements" and "finished inference" on 2026-04-08 and "final commit" on 2026-04-15, which supports
"April 2026"; this source is now recorded in `paper/data/earlier_phases.json` (`when_source`).

After these changes the PDF is 19 pages and builds with no layout warnings.
