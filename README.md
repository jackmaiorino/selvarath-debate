# Selvarath Debate: Failure Modes of Debate-Style AI Control

Testing when debate-as-oversight breaks: an honest and a dishonest LLM debater argue a factual
question before a weaker judge that cannot see the source document but may spend a limited budget
of yes/no oracle verification calls. The pilot found the counterintuitive result that a few oracle
calls made the judge *worse* than none. This repo re-analyzes that pilot and runs the follow-up
experiments.

Funded by a [Manifund grant](https://manifund.org/projects/testing-failure-modes-of-debate-style-ai-control-schemes-tewkbpvy1s).
Pilot write-up: [Limited verification can hurt debate oversight](https://www.lesswrong.com/posts/2a3vce7WooJ4XkDqw/limited-verification-can-hurt-debate-oversight) (LessWrong).

## Status (2026-07-08)

- Pilot re-analysis complete: headline effect quantified at Delta_few = +7.2pp, 95% CI [4.6, 10.2].
- A code audit found two data-corrupting bugs in the pilot's oracle channel (NOT-ADDRESSED replies
  miscoded to NO; ~100% of oracle queries garbled). Mechanism-level conclusions were retracted; see
  the correction in `reports/2026-07-06-preliminary-findings.md`.
- **Stage 1 validation run in progress**: the 318 pilot transcripts re-judged under six arms
  (clean harness, bug replay, placebo oracle, two single-bug arms, legacy QA), K=2 replicates,
  with pre-registered gates frozen before any clean data existed: `docs/rejudge-protocol.md`.

## Repo layout

| Path | What it is |
|---|---|
| `judge.py`, `api.py`, `debate.py`, `orchestrate.py`, `models.py`, `experiment_protocol.json` | The original pilot harness, kept unmodified as the historical record (contains the audited bugs) |
| `world_specs/`, `questions/` | The three fictional worlds and question sets |
| `data/` (untracked) | Pilot output: `judgments.jsonl`, `transcripts.jsonl`. See `data/DATA.md` for known-bug provenance before using |
| `analysis/` | Re-analysis package: loaders, pre-specified contrasts, cluster bootstrap, mechanism labeling, report generation |
| `rejudge/` | The fixed re-judge harness: arm configs, dual verdict parsing, cost-capped API client, resumable runner |
| `docs/rejudge-protocol.md` | Frozen pre-registration: arms, rationale, gates, spend record |
| `reports/` | Findings report and interactive dashboard (post-audit corrected) |
| `docs/manifund-updates/` | Grant progress updates as posted |

## Running

```bash
uv run pytest                      # full offline test suite (no API calls)
uv run python -m analysis.run_report   # regenerate the re-analysis report from data/
uv run python -m rejudge.runner --dry-run --limit 2 --arms clean,both,placebo  # offline smoke
```

Live runs cost money and are spend-gated: the runner refuses to start without `--approved-cap USD`
and hard-aborts if projected spend crosses it. Requires `TOGETHER_API_KEY` in the environment.

```bash
uv run python -m rejudge.runner --arms clean,both,placebo,na_only,doubled_only,legacy \
  --replicates 2 --approved-cap 290 --workers 8 --out rejudge/output/records.jsonl
```

Runs are resumable: rerun the same command and completed cells are skipped.

## Contributors

J. Marcellino (pilot design and data), J. Maiorino (re-analysis, audit, re-judge harness).
