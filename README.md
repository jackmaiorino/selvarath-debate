# Selvarath Debate: Failure Modes of Debate-Style AI Control

Testing when debate-as-oversight breaks: an honest and a dishonest LLM debater argue a factual
question before a weaker judge that cannot see the source document but may spend a limited budget
of yes/no oracle verification calls. The pilot found the counterintuitive result that a few oracle
calls made the judge *worse* than none. This repo re-analyzes that pilot and runs the follow-up
experiments.

Funded by a [Manifund grant](https://manifund.org/projects/testing-failure-modes-of-debate-style-ai-control-schemes-tewkbpvy1s).
Pilot write-up: [Limited verification can hurt debate oversight](https://www.lesswrong.com/posts/2a3vce7WooJ4XkDqw/limited-verification-can-hurt-debate-oversight) (LessWrong).

## Status (2026-07-16)

- Pilot re-analysis complete: headline effect quantified at Delta_few = +7.2pp, 95% CI [4.6, 10.2].
- A code audit found two data-corrupting bugs in the pilot's oracle channel (NOT-ADDRESSED replies
  miscoded to NO; ~100% of oracle queries garbled). Mechanism-level conclusions were retracted; see
  the correction in `reports/2026-07-06-preliminary-findings.md`.
- **Stage 1 validation complete** (pre-registered, 11,330 judgments, $182): the clean harness
  preserves a smaller but statistically positive harm signal (Delta_few = +3.4pp, CI [+1.3, +5.9]);
  the bugs contributed about half the pilot's headline; extra deliberation turns alone cause a
  small degradation; the pilot's U-shaped recovery is not reproduced. Full results:
  `reports/2026-07-09-stage1-rejudge-results.md`.
- **Mechanism and packaging follow-up complete:** oracle mistakes and judge over-reading both cause
  harmful flips; replaying identical Q&A as a neutral evidence table removes a large share of the
  harm. See `reports/2026-07-12-mechanism-and-packaging-memo.md`.
- **Phase-2 calibration complete, with 11 Gemma provider-failure cells (10 timeouts and one HTTP
  500) still to recover:** the selected task
  is blind, uncapped, three-round debate. The proposed final roster is four open-weight judges,
  Llama-70B plus hosted Qwen3.7-Plus debaters, and a Llama-70B oracle. See
  `reports/2026-07-14-calibration-results.md`.
- **The Phase-2 scientific design is approved; paid execution is not authorized.** The deterministic
  offline plan now enumerates 23,200 approved Phase-2 cells: a 1,060-cell capability preflight and
  22,140 post-canary main cells, including 984 uncapped/capped transcript cells. H/P/R, the C/D
  secondary family, optional scope, query-screen policy, canary gates, a $1,200 working budget, and a $1,500
  incremental hard ceiling are recorded. The source-bound 106-row AI audit and owner-approved
  pre-outcome Amendment A1 are complete; the resulting strata are algorithmic oracle-reply-pattern
  classes, not human-validated semantic judgments. Checker validation, prompt and provider hashes,
  top-anchor materialization, provider reconciliation, storage, credentials, and separate
  capability-preflight/canary/main spend approvals still block launch. The authoritative checklist is
  `docs/phase2-readiness-and-signoff.md`.

## Repo layout

| Path | What it is |
|---|---|
| `judge.py`, `api.py`, `debate.py`, `orchestrate.py`, `models.py`, `experiment_protocol.json` | The original pilot harness, retained for dry reproduction (contains the audited bugs); `api.py` now refuses live calls |
| `world_specs/`, `questions/` | The three fictional worlds and question sets |
| `data/` (untracked) | Pilot output: `judgments.jsonl`, `transcripts.jsonl`. See `data/DATA.md` for known-bug provenance before using |
| `analysis/` | Re-analysis package: loaders, pre-specified contrasts, cluster bootstrap, mechanism labeling, report generation |
| `rejudge/` | The fixed re-judge harness: arm configs, strict parsing, manifest-bound outputs, strict per-model pricing, cumulative usage ledgers, and exact completion gates |
| `rejudge/phase2_protocol.json`, `rejudge/phase2_plan.py` | Approved-but-non-executable Phase-2 design and deterministic 23,200-cell inventory; paid stages require external manifests |
| `rejudge/phase2_cost_model.py`, `rejudge/phase2_cost_model.json` | Deterministic call inventory, provisional empirical cost bands, credit estimate, working budget, and hard ceiling |
| `docs/phase2-readiness-and-signoff.md` | Authoritative materialization blockers and staged launch gate |
| `docs/rejudge-protocol.md` | Frozen pre-registration: arms, rationale, gates, spend record |
| `reports/` | Findings report and interactive dashboard (post-audit corrected) |
| `docs/manifund-updates/` | Grant progress updates as posted |
| `artifacts/` | Checksums, sizes, and row counts for large local-only research artifacts |

## Running

```bash
uv run pytest                      # full offline test suite (no API calls)
uv run ty check                    # static checks
uv run python -m analysis.run_report   # regenerate the re-analysis report from data/
uv run python -m rejudge.runner --dry-run --limit 2 --arms clean,both,placebo \
  --out rejudge/output/dry-run/records.jsonl  # offline smoke, isolated from live output
uv run python -m rejudge.phase2_plan   # enumerate approved Phase-2 scope; cannot make API calls
uv run python -m rejudge.phase2_cost_model --check  # verify tracked cost artifact is current
uv run python -m rejudge.phase2_resolvability_review --check  # verify historical blank template
uv run python -m rejudge.phase2_resolvability_ai_review --check  # verify A1 audit and amendment
uv run python -m rejudge.artifact_manifest verify --root . artifacts/local-research-artifacts.json
```

Corpus-backed regression tests are skipped in clean clones until the separately distributed JSONL
artifacts are installed. Their expected hashes and row counts are tracked under `artifacts/`.

Live rejudge runs cost money and are fail-closed: they require an explicit `--approved-cap USD`, a
clean committed worktree, a current frozen per-model price schedule, a writable cumulative usage
ledger, and a new or exactly matching output manifest. Every request is durably reserved before it
reaches the provider; unmatched crash/timeout reservations continue to count against the cap.
Requires `TOGETHER_API_KEY` in the environment. The original pilot API and historical canary path
refuse live calls.

Historical Stage-1 runs are resumable only when the output's immutable run manifest matches the
output path, current protocol, models, exact prices, inputs, code state, cumulative cap/ledger path,
and dry/live mode. Existing pre-manifest outputs must be migrated explicitly or resumed into a new
supplemental output; they are never adopted silently. The exact 11-cell Gemma recovery selection is
tracked, but no recovery call is authorized yet.

After any abnormal live termination, do not resume automatically: reconcile charged ledger events
and their cell metadata against durable output rows and provider billing first. A crash between the
ledger fsync and result fsync can otherwise cause a paid missing cell to be requested twice.

There is currently no executable Phase-2 main runner. Do not infer spend authorization from the
presence of the offline plan.

## Contributors

J. Marcellino (pilot design and data), J. Maiorino (re-analysis, audit, re-judge harness).
