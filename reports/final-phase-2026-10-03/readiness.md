# Final-phase readiness, 2026-10-05

The length correction is incomplete: six of eight worlds comply. Corrected Fable
W001 and W003 comply; W005 and W007 exhausted their 64,000-token allowances and
were rejected. Their original noncompliant artifacts remain preserved and active
until model-generated replacements pass admission. Cumulative authoring spend is
$19.816510, below the original $21.75 ceiling. Jack subsequently added $20 to Anthropic
and approved the requested $23.09 ceiling for one fresh retry each on W005/W007.
Their $3.267665 combined maximum is reserved before dispatch. Independent
answer-key validation, substantial authoring, canary, pilot and main are unstarted.

## Current quality and accounting

| World | Author | Words | Length/schema admission | Mechanical question checks |
|---|---|---:|---|---:|
| W001 | Fable, corrected | 1,469 | Pass | 11/12 |
| W002 | Astra, unchanged | 1,227 | Pass | 12/12 |
| W003 | Fable, corrected | 1,442 | Pass | 12/12 |
| W004 | Astra, unchanged | 1,244 | Pass | 12/12 |
| W005 | Fable, original retained | 1,716 | Reject | 12/12 |
| W006 | Astra, unchanged | 1,279 | Pass | 12/12 |
| W007 | Fable, original retained | 1,628 | Reject | 12/12 |
| W008 | Astra, unchanged | 1,160 | Pass | 12/12 |

Every saved world contains twelve candidate questions, required fields and all six
task types. The six compliant worlds contain 72 candidates with 71 mechanical
passes. W001-Q01 fails the unchanged 1.25 answer-length ratio at 48/36 words.
Across all saved artifacts there are 96 candidates and 95 mechanical passes,
including 24 candidates in rejected original worlds. Those totals do not imply
eight accepted worlds. No candidate answer was repaired by hand, and no mechanical
pass is independent answer-key validation.

| Corrected request | Provider result | Admission | Cost |
|---|---|---|---:|
| W001 `length-v2` | Complete | Pass | $1.507730 |
| W003 `length-v2` | Complete | Pass | $1.343290 |
| W005 `length-v2` | Truncated at 64,000 output tokens | Reject | $1.610965 |
| W007 `length-v2` | Truncated at 64,000 output tokens | Reject | $1.610940 |

W001 complied before the remaining three requests were dispatched at 21:04:35 UTC.
The truncated responses were not salvaged. Both rejections are recorded in
`author_rejections.jsonl`; all twenty paid responses, original requests and
spending entries remain in `author.db`. All batches are collected and no calls
remain active. The finite collector has exited; no watcher remains pending.

Original authoring cost was $13.743585; correction cost was $6.072925. Current
provider authoring totals are $17.964720 Anthropic and $1.851790 OpenAI. Against
reported top-ups now totaling $40/$10, subtracting the recorded $0.000420/$0.000410 access
checks leaves calculated credit of $22.034860/$8.147800. These are ledger-derived
estimates, not provider balance queries. Overall stage caps and the $6,000 ceiling
are unchanged.

| Prepared next attempt | Fresh request ID | Maximum batch cost |
|---|---|---:|
| W005 | `author:W005:fable:a1:t64000:length-v2` | $1.633845 |
| W007 | `author:W007:fable:a1:t64000:length-v2` | $1.633820 |
| Both | One new attempt each | $3.267665 |

At the original $21.75 ceiling, the allowance gap was $1.334175 and the
estimated Anthropic credit gap was $1.232805. Jack's subsequent $20 Anthropic
top-up resolves the credit gap, and his approval following the $23.09 ceiling
request authorizes only one new attempt each on W005/W007. The combined maximum
is $3.267665 and maximum cumulative authoring spend is $23.084175, within $23.09.
The updated reservation is `length_v2_retry_budget.json`; the earlier manifest,
quality report and retry-budget receipt are preserved under
`preserved/pre-funded-retries`. Each retry may still truncate. No additional
attempt or later paid stage is authorized by this funding update.

## Implementation and provenance

The authorized prospective amendment is recorded in
`authoring-length-amendment.md` and the existing run manifest before corrected
requests. Fable remains at high effort and 64,000 tokens; Astra retains its
original prompts, 32,000 tokens and byte-identical artifacts. Revision `length-v2`
appends an explicit count and model self-revision instruction to the original
Fable authoring prompt. Scientific conditions, retention rules, seeds, model
assignments and remaining launch gates are preserved.

The supported authoring path enforces the existing 1,000 to 1,500 word range,
twelve questions, required fields and all six task types. Invalid saved worlds
raise an explicit error. `--replace-invalid` preserves their exact bytes and
promotes a versioned replacement only after admission. Maximum request/retry
costs are reserved before dispatch; automatic live fallback is disabled for
authoring. The finite helper invokes this supported CLI, tests W001 first and
gates the remaining three on its compliance.

Original benchmark, response ledger, quality report and manifest bytes are
preserved under `preserved/pre-length-v2`, with verified hashes. Original call rows
still match that snapshot exactly. Corrected artifact hashes are recorded in the
existing manifest and versioned benchmark files. W001's corrected SHA-256 is
`11c91046d6695713041a40c3b8c70b648d80c5cb74bba531b88cc9181e6aa44b`;
W003's is `8747593a30c327c0bddb4ee42c312b6958a9c1ea7db94937b1ba64a35b511d90`.

The initial launcher stopped before submission because the borrowed environment
lacked the Anthropic SDK. Its request had zero attempts, no batch ID, no response
and zero cost. A worktree-owned Python 3.13.5 environment was created with
`uv sync --locked --python 3.13.5`, preserving pinned package versions. The helper
permits only proven unsent requests to resume and refuses uncertain provider state.

Collection exposed a replay snapshot error: copying only SQLite's main file
omitted committed WAL responses. Provider dispatch was disabled, so the failed
replay sent no request. Replay now uses SQLite's backup API and closes its
connections. Terminal helper exceptions record attention status instead of leaving
a stale running manifest. The current report successfully replays all 20 cached
responses with zero provider dispatches and an unchanged primary-file SHA-256
(`97754fe02037dbcf23f27c990834028bb88a2d6372e9af2956385148b26405ec`).

## Verification and delivery

All 46 affected tests pass in 18.99 seconds. They cover admission boundaries/schema, preserved invalid artifacts,
rejection reporting, reservation of all retries, disabled live fallback, batch
identity/restart, the offline pipeline, safe unsent-request recovery, failed-probe
gating, WAL replay and terminal error reporting. Targeted type checks pass for
admission, accounting/store, the helper and its tests. Two pre-existing `Hashable`
diagnostics remain in unrelated CLI canary/judge loops. The diff check passes.

Full GitHub CI at code head `99de891` failed: 3,739 passed, 115 failed and 145
skipped in 650.69 seconds. Failures are confined to ten unchanged historical
Phase 3 test files, including Windows absolute-path assumptions and missing
private recovery/provider artifacts. None is in final-phase tests; type checks
were skipped after the test failure. Private receipts are `ci_99de891_failed.log`
and `ci_99de891_summary.json`. Later documentation-head CI also failed. Full CI
is not a pass, and the canonical draft PR remains open without bypassing checks.

Changes are owned by branch `codex/final-phase-next-stage` in
`C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-final-phase-codex` and
delivered through [canonical PR #1](https://github.com/jackmaiorino/selvarath-debate/pull/1).
The original Claude worktree is unchanged. Private artifacts remain outside Git
at `D:/finalphase-runs/final-phase-2026-10-03`.

## Exact next commands and unmet prerequisites

From this worktree, with the existing run root, the supported retry command under
the newly approved allowance is:

```powershell
uv run --locked python -m finalphase.cli author --worlds 8 --only W005,W007 --quality-check --mode batch --attempts 2 --spend-cap 23.09 --replace-invalid
```

This reserves both fresh attempts before dispatch. `--attempts 2` reuses the
measured `a0` failures and allows only a fresh `a1` for each world. The $23.09
ceiling and adequate Anthropic credit are recorded in the existing manifest.

After eight worlds comply, independent validation's supported preflight and stage
commands are:

```powershell
uv run --locked python -m finalphase.cli preflight --stage validate --workers 16 --mode batch
uv run --locked python -m finalphase.cli validate --mode batch
```

Paid validation still requires explicit authorization, representative serial and
parallel completed-work throughput, current placement evidence, sufficient
provider funding and stage-cost reconciliation. Mechanical passes remain subject
to independent validation. Broader authoring's eventual command is
`uv run --locked python -m finalphase.cli author --worlds 160 --mode batch`, with
its own scope/funding and throughput qualification. These paid stages are not run.

The six arms and both answer orders remain world alone, debate k0/k1/k2/k6 and
debate plus world; no selector requests are dispatched. Reversal flags remain
descriptive; equal defensibility, key disagreement and failed fact checks reject
questions. Reproducible question-count reductions may be chosen on cost before
main, and splits cannot change after main requests are registered. The $200
author, $200 validation, $60 canary, $650 pilot and $4,300 main caps plus $590
reserve remain $6,000. Illustrative validation, pilot and high authoring forecasts
exceed their individual caps and require reconciliation using measured costs.
Canary, oracle, pre-registration, question count/roster, measured forecast and
reconciled funding remain prerequisites before main.

The next wake condition is collection of the two reserved W005/W007 retries.
Their admission, mechanical checks, original provenance and cached replay must be
verified before the requested eight compliant worlds can be reported achieved.
