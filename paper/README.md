# Final report (paper folder)

Draft of the methods, results, figures and supplement for the final-phase report. The compiled draft is `main.pdf`.

## Who writes what

| Files | Owner |
|---|---|
| `sections/abstract.tex`, `introduction.tex`, `background.tex`, `discussion.tex`, `references.bib` | Joey |
| `sections/methods.tex`, `results.tex`, `limits.tex`, `appendix.tex`, `scripts/`, `generated/`, `figures/`, `data/` | Jack |

Each section is its own file, so two people can edit at once without conflicts. Joey's files hold gray
`\joey{...}` placeholders. To quote a result, use its macro from `generated/numbers.tex` (for example `\errKone`) so
the text stays in step with the data. Whoever changes the text rebuilds and commits `main.pdf` with it.

## Build

Editing text needs only LaTeX, because the generated tables, numbers and figures are committed:

```
cd paper && latexmk -pdf main.tex
```

Rerun the two scripts only if `data/` or the scripts change (needs Python with matplotlib):

```
python paper/scripts/build_generated.py   # numbers.tex and the tables, from paper/data
python paper/scripts/make_figures.py      # figures (PDF and PNG), from paper/data
```

`main.tex` loads the official NeurIPS 2026 style, `neurips_2026.sty`, with the `preprint` option. The file is
unmodified from `Formatting_Instructions_For_NeurIPS_2026.zip` on media.neurips.cc (downloaded 2026-11-10, SHA-256
`c3fc2894e83d2517ca18b66741d6c595986d97957dc08ec08bb2125a7ec4555a`). One draft-stage line in `main.tex`,
`\raggedbottom`, stops the style from stretching short pages; remove it once the text has settled. If the style file
is missing, `style/report_fallback.sty` gives the same text block.

## Layout

- `main.tex`: skeleton and section order.
- `sections/`: one file per section (owners above).
- `sections/appendix.tex`: the supplement (statistical details, deviations, per-judge tables, gate audit,
  explanation reading, prompts, models and cost, earlier phases, and the full list of limitations).
- `generated/`: number macros and tables written by `scripts/build_generated.py`. Do not edit by hand. Every
  result quoted in the text is a macro from `generated/numbers.tex`.
- `figures/`: written by `scripts/make_figures.py`.
- `data/`: byte-for-byte copies of the frozen analysis outputs (hashes below), plus `benchmark_facts.json`,
  computed from the main split for this draft.
- `reference/`: the reviewed 2026-10-29 results report and the 2026-10-30 explanation note, for provenance.
- `review/`: independent review briefs and opinions for this draft.

## Frozen inputs (SHA-256)

| File | SHA-256 |
|---|---|
| `data/main_analysis.json` | 3e2c5d22833909c2cd78356f522a31f3702923e5bc2258496ffa0ebb359a829e |
| `data/amendment1_analysis.json` | c1011b4b1867fff6414897d766fc562e4feecaa768e049d402ca889e6d23e683 |
| `data/amendment3_analysis.json` | f251be92f0967a45a452a6986e0ed0076603df9d2eaa90a2c7e57c5ea10142cb |
| `data/gate_audit.json` | 5397026d43430598ce00988d7782bb0f3253a3efeda02d6d4157db4973ab2419 |
| `data/gate_endpoint_comparison.json` | 572462fe8ee1d7745134c8f7438881bf54632ee9e6d80e2f371f371ccb63be80 |

Source: the final phase run root, `E:\finalphase-runs\final-phase-2026-10-03` (`main/analysis/`,
`gate-audit-v1/audit.json`, `gate-endpoint-check-v1/comparison.json`, `main/main_readout.json`,
`coordinator/explanation_reading_20261030/`).

## Review record

- Consult 68 (2026-11-10): one-shot GPT-6 Astra, ultra effort, read-only, session
  `01a1c78a-fa40-7ec0-9be5-58a309db46c5`. Brief and opinion in `review/consult68_*` (the run log stays local; `.log`
  files are not committed). Verdict: accurate with
  corrections. It reproduced all 91 number macros and ten generated tables from `data/`, and listed 15 required
  wording corrections and 3 suggestions. All 18 were applied the same day. The corrected text has not been through a
  second review round.
- Not covered by consult 68: Appendix H (earlier phases) and `data/earlier_phases.json`, added after the brief was
  sent. Its numbers were entered by hand and checked against each phase's own report or results file (sources are in
  the JSON). The adversarial loop below reviewed it.
- After consult 68 the layout moved to the official NeurIPS style and Joey's placeholders moved into their own
  section files. No wording or number changed in that step.
- Adversarial loop (2026-11-10), three rounds, each a new one-shot GPT-6 Astra session at ultra effort, read-only,
  asked to attack the whole draft and to check the answers to the round before. Each finding was checked against its
  source before it was applied; the answers are in `review/consult69_response.md` to `consult71_response.md`.

  | Round | Consult | Session | Required | Suggested | Outcome |
  |---|---|---|---|---|---|
  | 1 | 69 | `01a1c7e8-8dc0-75c0-9f3b-940e033d5e26` | 15 | 3 | all required applied; suggestions 16 and 17 in part |
  | 2 | 70 | `01a1c7fb-47f9-7833-8600-d8067f5539a9` | 4 | 0 | all applied |
  | 3 | 71 | `01a1c802-5512-7f41-8c61-505ad8788c3f` | 0 | 1 | "no required changes"; the suggestion applied verbatim |

  Rounds 1 to 3 covered Appendix H. After round 3 the only further change is a line break in the Section 4.2
  heading (no wording changed). The reviewers' stated limits: provider charges were checked against the run's
  accounting records and not provider statements; the 77 explanation cases were not relabelled; blinding and
  chronology rest on the operational records.

- Council (2026-11-10): five seats from three model families (Claude Fable 5.2, Claude Opus 5.5, GPT-6 Astra,
  GPT-6.1 Sol, Gemini 4 Carbon), two rounds and five debates. All five said "revise" in both rounds; no seat found a
  wrong number. The findings concern interpretation, validity threats and disclosure. The record is in
  `review/council-20261110/`: `synthesis.md` first, then `response.md` (what was applied and the author's choices on
  the disputes the council did not debate). The council is advisory; it does not replace the reviews below.
- Consult 72 (session `01a1c865-0eb8-7180-8866-ba11e974fb67`): review of the post-council revision. Seven required
  changes and two suggestions, all applied (`review/consult72_response.md`). The most important: the split in
  collection time between conditions is not absolute.
- Trim (2026-11-10): the main text was shortened from 10 pages to about 7.5 by moving material to the supplement,
  with nothing deleted. `review/consult72_response.md` lists what moved where and which sentences were newly written.
- Consult 73 (session `01a1c876-03dd-7151-9269-29a45538135e`): check of the corrected and trimmed draft. Verdict
  "no required changes": 160 number macros and 14 generated tables reproduced; the relocation audit passed; all 24
  pages and six figures inspected. Its three suggested rewordings were applied verbatim. One sentence was added
  after it, with numbers the reviewer itself cited: the speaking order by world author in Appendix B, a descriptive
  output listed in preregistration 7.6.
- Second trim (2026-11-11): Jack's part of the main text (title, Sections 3, 4 and 6) was cut from about 7.1 pages
  to about 6, leaving three of the nine pages for Joey's sections. The conditions table became a sentence, Table 1
  (rates and differences) moved to Appendix A, a few single-sentence details moved to the supplement, and Section 6
  became one paragraph. No number changed and nothing left the report (`review/consult74_response.md`).
- Consult 74 (session `01a1ca86-d3d4-7702-a209-56129cf653aa`): check of that cut. Verdict "no required changes":
  every removed sentence accounted for, 164 number macros and 14 tables reproduced, pages 2, 4 and 5 inspected. Its
  one suggested rewording was applied verbatim.
- The review records are kept as the reviewers wrote them, with one change before publication: local absolute file
  paths were replaced by repository-relative ones. Reviewers' run logs are not committed.
- The preregistration, amendments, code and run records that the reviews cite (`reports/final-phase-*`,
  `finalphase/`, the run root) are not on this branch; they are on a final-phase branch that is not yet published.
- Standing limits of every review above: provider charges were checked against the run's accounting records and
  not against provider statements; the 77 explanation cases were not independently relabelled; blinding and
  chronology rest on the operational records.

## Writing rules for this report

- Main text: plain rates, differences and 95% intervals. Heavier statistics go in the supplement.
- No em-dashes.
