# Final-phase authoring precheck, 2026-10-05

All eight worlds comply with the existing 1,000 to 1,500 word requirement,
required fields, twelve candidate questions and all six task types. The final
benchmark has 96 candidates and 95 mechanical passes. Independent answer-key
validation, substantial authoring, canary, pilot and main remain unstarted.
Cumulative authoring spend is $22.086565, within the revised $23.09 ceiling
approved following the funding request. No additional paid request is needed for
this precheck.

## Final quality

| World | Author | Words | Admission | Mechanical question checks |
|---|---|---:|---|---:|
| W001 | Fable, corrected | 1,469 | Pass | 11/12 |
| W002 | Astra, unchanged | 1,227 | Pass | 12/12 |
| W003 | Fable, corrected | 1,442 | Pass | 12/12 |
| W004 | Astra, unchanged | 1,244 | Pass | 12/12 |
| W005 | Fable, corrected | 1,462 | Pass | 12/12 |
| W006 | Astra, unchanged | 1,279 | Pass | 12/12 |
| W007 | Fable, corrected | 1,475 | Pass | 12/12 |
| W008 | Astra, unchanged | 1,160 | Pass | 12/12 |

W001-Q01 fails the existing 1.25 answer-length ratio at 48/36 words. It remains
in the authored artifact and is rejected by the mechanical retention check.
No prose or candidate answer was trimmed or repaired by hand. The 95 mechanical
passes still require independent answer-key validation; they are not validated
scientific examples or evidence of the study's effects.

Fable's original four worlds had 1,628 to 1,782 words. Astra's original four
already complied and retain their exact artifact hashes. Jack authorized the
prospective authoring-only `length-v2` amendment before corrected requests.
W001 complied before the remaining three were dispatched. Their first batch
produced compliant W003 and truncated W005/W007. Only those two failures were
retried with fresh `a1` IDs. Both corrected retries completed and passed admission.
Collection finished at 9:01 p.m. Eastern on October 5. All batches are collected,
no calls remain active and the finite collector has exited.

## Paid requests and funding

| Corrected request | Result | Input tokens | Output tokens | Cost |
|---|---|---:|---:|---:|
| W001 `length-v2` | Accepted | 2,186 | 59,872 | $1.507730 |
| W003 `length-v2` | Accepted | 2,188 | 53,294 | $1.343290 |
| W005 `length-v2` | Truncated, rejected | 2,193 | 64,000 | $1.610965 |
| W007 `length-v2` | Truncated, rejected | 2,188 | 64,000 | $1.610940 |
| W005 `a1:length-v2` | Accepted | 2,193 | 45,503 | $1.148540 |
| W007 `a1:length-v2` | Accepted | 2,188 | 44,423 | $1.121515 |

The two truncated responses were not salvaged. `author_rejections.jsonl` retains
two distinct rejected generation IDs; their cached failures were logged again
when the retry command replayed `a0`. Four rejection events therefore represent
two paid rejected generations, not four paid requests.

Original authoring cost was $13.743585. The correction added $8.342980, including
the two truncated calls, for $22.086565 total. Provider authoring totals are
$20.234775 Anthropic and $1.851790 OpenAI. Against reported top-ups totaling
$40/$10, subtracting the recorded $0.000420/$0.000410 access checks leaves
estimated credit of $19.764805/$8.147800. These are ledger-derived estimates,
not provider balance queries.

At the original $21.75 ceiling, reserving both retries required $1.334175 more
allowance and $1.232805 more estimated Anthropic credit. Jack added $20 to
Anthropic and approved the requested $23.09 ceiling. Before dispatch, both retry
maxima were reserved at $3.267665 combined, for maximum cumulative authoring
spend $23.084175. The two retries actually cost $2.270055. The revised allowance
has $1.003435 unused. Stage allocations and the overall $6,000 ceiling are unchanged;
the funding update authorizes no further generation or later paid stage.

The exact executed retry command was:

```powershell
uv run --locked python -m finalphase.cli author --worlds 8 --only W005,W007 --quality-check --mode batch --attempts 2 --spend-cap 23.09 --replace-invalid
```

`--attempts 2` reused measured `a0` failures and sent only one fresh `a1` per world.
Fable remained at high effort and 64,000 tokens. Automatic live fallback was disabled.
The settled reservation is `length_v2_retry_budget.json` in the existing run root.

## Implementation, provenance and verification

The amendment is recorded in `authoring-length-amendment.md` and the existing
manifest before corrected paid requests. `length-v2` appends a word-count and
model self-revision instruction to the original Fable authoring prompt. Astra's
prompts and 32,000-token allowance are unchanged. Scientific conditions, question
retention, seeds, model assignments and remaining launch gates are preserved.

The supported path rejects invalid world lengths and incomplete schema, and
raises an explicit error for invalid saved worlds. `--replace-invalid` preserves
exact original bytes and promotes a versioned replacement only after admission.
Every permitted request/retry maximum is reserved before dispatch.

Original benchmark, ledger, quality report and manifest bytes remain under
`preserved/pre-length-v2`, with verified hashes. All sixteen original call rows
still match that snapshot exactly. Original and corrected Fable artifacts are
preserved in benchmark version directories. Each promoted artifact matches its
raw model response, apart from the required world/author/request metadata. All
Astra artifacts remain byte-identical. The pre-retry manifest, quality report and
reservation remain under `preserved/pre-funded-retries`.

A complete SQLite backup containing all 22 calls is preserved under
`preserved/after-length-v2/author.db`, with SHA-256
`4066407cd1716ff75fd9c57c02786cbda53707165620dd043def4c2e4457e66c`.
The manifest and `author_quality_summary.json` record every current world hash,
mechanical rejection and cumulative charge.

The initial launcher stopped before submission because a borrowed environment
lacked the Anthropic SDK. A worktree-owned Python 3.13.5 environment was created
with `uv sync --locked --python 3.13.5`, preserving package pins. Only proven
unsent requests can resume. Collection also exposed a main-file-only SQLite replay
snapshot that omitted committed WAL rows. Replay now uses SQLite backup and closes
connections; terminal helper errors record attention status.

All 46 affected tests passed in 18.99 seconds. A source diff confirms admission,
store, helper, tests, lockfile and dependency configuration are unchanged since
that passing run. Tests cover the offline pipeline, length/schema admission,
invalid saved-artifact preservation, rejected generations, retry reservations,
batch identity/restart, disabled fallback, safe unsent recovery, failed-probe
gating, WAL replay and terminal errors. Targeted type checks pass for admission,
accounting/store, helper and helper tests. Two pre-existing `Hashable` diagnostics
remain in unrelated CLI canary/judge loops. The diff check passes.

The actual ledger replay recovered all 22 cached responses with zero provider
dispatches. The primary store remained bit-identical before and after replay,
SHA-256 `716b1bebde52d66f1e1bbdc396dbd6b2bfbd99e905a87967cc64a40185216a38`.

Full GitHub CI at code head `99de891` failed: 3,739 passed, 115 failed and 145
skipped. Failures are in ten unchanged historical Phase 3 test files, including
Windows absolute-path assumptions and missing private recovery/provider artifacts.
No final-phase test failed; CI type checks were skipped. Later documentation-head
CI also failed. This is distinct from the passing affected checks. The canonical
draft remains open without bypassing failed checks or rewriting historical evidence.

Changes are committed on owned branch `codex/final-phase-next-stage` and delivered
through [canonical PR #1](https://github.com/jackmaiorino/selvarath-debate/pull/1).
The original Claude worktree is unchanged. Private artifacts remain outside Git
at `D:/finalphase-runs/final-phase-2026-10-03`.

## Supported next stage and unmet prerequisites

From `C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-final-phase-codex`,
with the existing run root, independent validation's supported commands are:

```powershell
uv run --locked python -m finalphase.cli preflight --stage validate --workers 16 --mode batch
uv run --locked python -m finalphase.cli validate --mode batch
```

The preflight is read-only. Paid validation is not authorized by this goal and
still requires representative serial/parallel completed-work throughput, current
placement evidence, sufficient provider funding and stage-cost reconciliation.
Recorded provider access passes, but validation throughput/placement evidence is
missing. Authoring completion does not close independent answer-key validation.

Broader authoring's eventual command is
`uv run --locked python -m finalphase.cli author --worlds 160 --mode batch`, with
its own scope, funding and throughput qualification. The six arms and both answer
orders remain world alone, debate k0/k1/k2/k6 and debate plus world. No selector
requests are dispatched. Reversal flags are descriptive; equal defensibility,
key disagreement and failed fact checks reject questions. Splits cannot change
once main requests are registered.

The $200 author, $200 validation, $60 canary, $650 pilot and $4,300 main caps plus
$590 reserve remain $6,000. Existing forecasts are illustrative and require
reconciliation with actual authoring costs and measured later-stage costs.
Canary, oracle, pre-registration, question count/roster, measured forecast and
reconciled funding remain prerequisites before main. No later paid stage has run.
