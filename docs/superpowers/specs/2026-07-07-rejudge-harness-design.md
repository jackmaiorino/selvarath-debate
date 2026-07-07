# Fixed-Harness Re-Judge: Runner + Report Hygiene — Design

**Date:** 2026-07-07 · **Status:** approved (user, 2026-07-07) · **Scope:** $0 build work only — no live API calls
**Prereqs:** the 2026-07-06 harness audit (two oracle-channel bugs), the 17-finding adversarial review, Codex consults #05–#06.

## Goal

Build everything needed to run the fixed-harness re-judge experiment the moment spend is approved — and repair the
documentation debts the review confirmed — while spending $0. Live API calls are explicitly out of scope and gated
separately (spend policy: line-item estimate + explicit approval; Codex no-go until golden replay tests pass).

## Workstream A — Report hygiene ($0, prose + regeneration)

The review confirmed our correction was half-applied: retracted claims are restated as settled *below* the correction
banner. Fixes, all in existing files:

1. **`reports/2026-07-06-preliminary-findings.md`**
   - Summary bullets: inline caveats on "not a measurement artifact" and the 24%/26%/46% mechanism split — each gets
     "*(pre-audit; reset to unknown — see Correction)*".
   - §4.2 title/body: scope to "not the **default-to-B parse** artifact"; state explicitly the two oracle bugs are
     side-symmetric and NOT cleared by this check.
   - §4.3, §4.4: header caveat "*(pre-audit analysis of the corrupted oracle channel — retained for the record)*".
     §4.4 additionally notes the confidence field is near-degenerate (4 in 1,695/2,583 rows, 5 in 887, one 3) — the
     "confidence rises" trend is a 4→5 shift, secondary at best.
   - §6 Bottom line: rewrite to the honest post-audit state (narrow surviving claim + what resets + what decides it).
   - Every "pre-registered" for the pilot re-analysis → "pre-specified before recomputation (post-hoc relative to
     first look)"; the design spec's §1 records the prior qualitative look. "Pre-registered" is reserved for the
     re-judge gates (genuinely ex-ante).
   - §5 limitations: add the turn-count confound (budget>0 inserts extra conversation turns before the verdict) and
     the budget-20 cell being a truncated single-world partial run (non-representative, not merely underpowered).
   - Fix stale "40 tests" → current collected count; add `mechanism_cases.md` to §8 artifacts.
   - §7 next steps → replaced by a pointer to the frozen re-judge protocol (Workstream B deliverable).
2. **`reports/findings-dashboard.html`**: "What we can bank" footer — remove the mechanism-split entry, keep only the
   narrow surviving claim; "Artifact check: 70B clean" tile → "not the parse-fallback artifact"; same §4.2-style
   scoping in chart captions. Redeploy the Artifact after edits.
3. **`analysis/output/mechanism_validation.md`**: run_report/mechanism generators are not re-run; instead the
   generating script (`analysis/mechanism.py` writer or `run_report.py`) gains a mandatory correction header on this
   output: "postmortem of the corrupted pilot harness — NOT a decomposition of clean verification (see report
   Correction)". Regenerate outputs.
4. **`analysis/run_report.py`**: stale-mechanism fix — fold pass-2/validation results (labels_pass2.csv,
   κ, consensus split) into report.md's mechanism section with the same correction framing, and drop the "do
   Deliverable D" recommendation (D is done). Regenerate `analysis/output/report.md`.
5. **`data/DATA.md`** (new, sits inside untracked `data/`; a copy of the notice goes in the report §2): marks
   `judgments.jsonl`/`transcripts.jsonl` as **legacy buggy-harness data** — lists each bug, affected fields
   (`queries_submitted[].response` unreliable; `[].query` is pre-doubling text; `verdict` subject to default-to-B;
   `confidence` subject to int(raw[0])), and status.

## Workstream B — Re-judge runner (new `rejudge/` package)

### Design decisions (locked)

- **Pilot files are never modified** (`judge.py`, `api.py`, etc. stay as the record). The runner is a new package
  that *ports* pilot behavior where replay-fidelity requires it.
- **Arms are configuration, not code paths.** One runner; an `ArmSpec` selects the oracle normalizer, query composer,
  DONE detector, and placebo injection. Scope changes (which arms/budgets/replicates to run) are CLI parameters, so
  the pending $10k scope decision does not change the build.
- **Treatment-side vs measurement-side:** oracle normalization, query composition, DONE detection, and placebo are
  treatment-side (per-arm). Verdict/confidence parsing is measurement-side: every raw verdict is stored and parsed
  BOTH ways (strict + pilot-compat) in every arm. Primary outcome = strict parse with INVALID as a reported third
  outcome, never coerced.
- **A/B assignment fixed across budgets** in ALL arms via `analysis.infra.design.position_a_is_correct`
  (seed excludes budget). BOTH is therefore *not* a pilot reproduction and is never described as one; it isolates
  the two oracle-channel bugs against CLEAN. (An optional legacy-replay subset is a possible Stage-1 add-on, config
  only, pending the #07 scope decision.)
- **Gates are frozen pre-run** in `docs/rejudge-protocol.md` (Workstream B deliverable): clean Δfew ≥ 4pp with
  cluster-bootstrap 95% CI excluding 0 → harm survives; ≤ 2pp with CI including 0 → mostly harness artifact;
  2–4pp indeterminate. Attribution: BOTH−CLEAN ≥ ~3.5pp or > 50% of the original 7.2pp → headline mostly
  harness-induced. Placebo: PLACEBO ≈ CLEAN (within 2pp), and PLACEBO−p(0) ≥ 4pp, → deliberation/turn-count effect, not verification content.
  Protocol doc embeds commit hash, model IDs, arm definitions, parser version, seed scheme, placebo text.

### Arms

| Arm | Oracle normalizer | Query composer | DONE detector | Budgets (default) |
|---|---|---|---|---|
| CLEAN | strict enum {YES, NO, NOT ADDRESSED, INVALID} | structured: claim passed once, judge instruction and oracle template share identical phrasing, no string stripping | robust (regex, case/punct tolerant) | 0,1,2,5 |
| BOTH | pilot port: startswith chain (NA→NO) | pilot port: template double-wrap + dead prefix-strip | pilot port: exact `DONE` | 1,2,5 |
| PLACEBO | n/a (no oracle call) | clean composer (query still generated + logged) | robust | 1,2,5 |
| NA_ONLY | pilot normalizer | clean composer | robust | 1,2 |
| DOUBLED_ONLY | strict enum | pilot composer | robust | 1,2 |
| LEGACY (QA subset) | pilot port | pilot port | pilot port | 1,2 — 100 transcripts, per-budget A/B re-randomization + pilot parser primary; sanity check only, not a gate |

Default replicates **K=2** (independent seeds per (transcript, budget, arm) cell; `replicate` recorded per row);
LEGACY runs K=1. Scope per Codex #07: full factorial upfront, one launch after a canary batch — staging is
engineering safety only, not budget.

Placebo feedback text (verbatim, from Codex #06):
`ORACLE PLACEBO: no factual verification was performed for this query. This message contains no evidence about the world document.`

### Components

- `rejudge/config.py` — `ArmSpec` dataclasses for the 5 arms; budgets; model IDs; temperatures and prompt templates
  loaded from `experiment_protocol.json`; seed scheme (pilot's `make_seed` minus budget for A/B; per-call seeds keep
  the pilot structure).
- `rejudge/composer.py` — clean structured composition + faithful pilot port (double-wrap reproduction, verified
  against reconstructed pilot strings).
- `rejudge/oracle_channel.py` — oracle call + both normalizers; INVALID never coerced.
- `rejudge/parsers.py` — `parse_pilot_compat()` (port of `judge._parse_verdict` incl. default-to-B and
  `int(raw[0])` confidence) alongside the hardened strict parser (see below). `parser_version` constant.
- `rejudge/judge_loop.py` — iterative loop (query calls, oracle feedback, verdict call) with per-arm hooks and
  **full raw logging**: raw judge query text, literal oracle-facing prompt, raw oracle reply, normalized reply, raw
  verdict text, both parses, full message history.
- `rejudge/records.py` — output schema. Provenance on every row: `harness_version` (git SHA), `arm`, `dry_run`,
  `created_at`, `judge_model`/`oracle_model`, `seed`, `parser_version`, `budget`, `replicate`.
- `rejudge/api_client.py` — wraps the Together SDK: exponential-backoff retries, persisted error log (JSONL),
  running token/cost accounting checked against an `--approved-cap` (abort when projected spend exceeds it),
  context-length guard (preflight max-context per request, not mean), and a dry-run mode whose canned responses are
  unmistakably synthetic AND row-tagged `dry_run: true`.
- `rejudge/runner.py` — CLI: `--arms --budgets --replicates --limit --approved-cap --dry-run`; resumable (skips
  cells already present in the output JSONL); writes `rejudge/output/records.jsonl` (untracked, like `data/`).

### Parser hardening (`analysis/infra/parsing.py`)

Review-confirmed holes to fix, with tests: hedged/negated verdict lines currently mis-parse via A-before-B substring
("VERDICT: Not Position A", "VERDICT: Position A or B" → must be INVALID); markdown/blockquote leads
("**VERDICT:** A", "> VERDICT: A"); verdict on the following line ("VERDICT:\nPosition A"); multi-line REASONING
(accumulate until the next `KEY:` line, not single-line); `NO EVIDENCE` stays non-committal → INVALID (never NO).
Bump `parser_version`.

### Testing (all offline)

- **Golden replay:** recorded-response fixtures (hand-authored canned API responses in `tests/fixtures/rejudge/`);
  the runner in replay mode must produce byte-stable records for every arm — including reproducing the exact doubled
  query string and NA→NO miscoding in BOTH, and their absence in CLEAN.
- Composer equivalence: pilot-port composer output == reconstruction of pilot strings for sampled real queries from
  `data/judgments.jsonl`.
- Parser: table-driven adversarial cases (above) for both parsers; pilot-compat parser verified against pilot
  behavior on the same inputs (incl. default-to-B).
- Determinism: A/B fixed across budgets and replicate-stable; seeds reproducible.
- api_client: cap-abort, retry, context-guard, dry-run tagging — unit-tested with a stub SDK.
- **Definition of run-ready:** full suite green + `runner --dry-run` end-to-end on 2 transcripts × all arms
  produces schema-valid, provenance-tagged records. Live calls remain blocked on user spend approval.

## Out of scope

Live API calls; the analysis of re-judge results (existing `analysis/` package extends naturally later);
MODEL_REGISTRY live verification (phase-2 gate); new worlds / capability grid (separate design, pending #07).
