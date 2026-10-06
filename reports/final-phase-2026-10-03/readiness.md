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

Jack subsequently approved independent validation of these eight worlds in this
chat: "you're approved to run it". That authorization is recorded in the existing
manifest before any validation request. It includes bounded real throughput and
correctness qualification. Broader authoring, canary, pilot and main remain
unlaunched. Validation is currently waiting for provider funding and a real
qualification receipt; `validate.db` does not exist and no validation or qualification
request has been sent.

The supported dispatcher now submits both frontier batches before Together live
work and uses the requested worker count. The qualification compares the same
eight frozen Together requests at one and eight workers, including inference,
collection and SQLite serialization. It covers both world authors, both answer
orders and fact checks. Eight fresh serial control IDs never enter retention;
the parallel arm's first canonical responses are reused. Four canonical frontier
checks then verify the unchanged 4,000-token limits and output format before the
remaining cohort is dispatched. Frontier inference runs asynchronously at the
providers and does not use local worker slots. No fake-provider test is a real
throughput receipt, and no receipt has yet been recorded.

The read-only plan contains 757 canonical requests for 95 mechanically eligible
questions, plus eight serial controls. Its conservative maximum reservation is
$41.75890856: $14.475730 Anthropic, $14.043700 OpenAI and $13.23947856 Together.
That fits the unchanged $200 validation cap. The OpenAI credit gap is $5.895900;
Together's available credit is unconfirmed. Anthropic's ledger-derived credit
is sufficient. Full provider maxima are checked before qualification as well as
validation. Saved-world hashes bind the authorized scope. Automatic live fallback
is disabled, and transport attempts are limited to one; a failure stops completion
and requires a fresh paid retry reservation. Missing calls cannot be reported as
completed validation.

The current placement inventory records Jack's PC at 24 logical processors and
127.82 GiB RAM, with existing Python/Claude/Node work preserved. API inference uses
no local GPU. HaleysPC failed strict SSH host-key verification; trust was not
changed. RunPod's read-only inventory found five exited allocations and no running
one; a new compute lease is outside this API validation scope. The prospective
collector placement is local, subject to the real qualification result. Private
evidence is `validation_placements.json` and `validation_placement_runpod.json`.

Supported commands, after account credit is recorded, are:

```powershell
uv run --locked python -m finalphase.cli validation-plan --mode batch
uv run --locked python -m finalphase.cli qualify-validate --mode batch
if ($LASTEXITCODE -ne 0) { throw 'Qualification did not pass' }
$validationWorkers = (Get-Content -Raw 'D:/finalphase-runs/final-phase-2026-10-03/validation_qualification.json' | ConvertFrom-Json).selected_workers
uv run --locked python -m finalphase.cli preflight --stage validate --workers $validationWorkers --mode batch
if ($LASTEXITCODE -ne 0) { throw 'Validation preflight did not pass' }
uv run --locked python -m finalphase.cli validate --mode batch --workers $validationWorkers
```

Qualification receipts bind execution-source and lockfile hashes, host, mode,
inputs and worker counts. Compatible receipts survive documentation-only commits;
changed execution bytes invalidate them even under the same Git HEAD. Validation
completion still leaves the design's Claude audit and all later scientific gates.

Preparation verification: 52 affected tests passed in 19.66 seconds; the project
type checker passed for validation, preflight, store and the new validation tests;
the diff check passed. The actual author ledger replay recovered all 22 cached
responses with zero provider dispatches, preserving its primary SHA-256 exactly.
The supported read-only validation preflight refuses missing real throughput,
the OpenAI credit gap and unconfirmed Together credit. No paid validation store
was created. The earlier full CI failures described above remain separate from
these affected checks.

### Provider funding plan

For a practical bulk top-up now, target available credit of $1,200 Anthropic,
$800 OpenAI and $400 Together through the pilot. The upper preparation forecast
is approximately $2,181, including authoring, full validation, canary, pilot and
oracle selection, before campaign reserve. These $2,400 credit targets leave
some forecasting margin. Ledger estimates imply additions of $1,180.235195
Anthropic and $791.852200 OpenAI; bring Together to $400 using its actual balance.
Main funding can then use the pilot's measured forecast.

For advance funding within the overall $6,000 spending ceiling, provisional
available-credit targets are $3,000 Anthropic, $1,800 OpenAI and $1,200 Together.
Subtract actual current balances when depositing. Our ledger estimates imply
top-ups of $2,980.235195 Anthropic and $1,791.852200 OpenAI. Together's deposit is
$1,200 minus its actual available balance. Funding these accounts does not change
the spending ceiling or authorize a later stage.

The following refresh the October 3 illustrative low/high forecasts, rather than
request reservations or promises. They describe 160 worlds, up to 192 pilot
questions and 1,068 main questions. Authoring includes the subsequent $8.342980
correction; validation includes the $0.18797724 extra control reservation. The
high validation case now uses the supported frozen 4,000-token output limit;
the older forecast assumed 5,000. Frozen September 30 rates and other token
assumptions are unchanged. The calculation is preserved in the private
`provider_funding_plan.json`.

| Phase | Anthropic | OpenAI | Together |
|---|---:|---:|---:|
| Authoring, 160 worlds including the completed precheck | $99 to $145 | $45 to $65 | $0 |
| Full benchmark validation | $105 to $192 | $105 to $192 | $65 to $92 |
| Debater canary | $28 to $64 | $27 to $63 | $3 |
| Pilot | $363 to $711 | $221 to $379 | $168 to $253 |
| Oracle selection | $5 to $11 | $5 to $11 | $0 |
| Main, 1,068 questions | $2,021 to $3,953 | $1,230 to $2,108 | $936 to $1,410 |

Keep the $590 campaign reserve inside the $6,000 ceiling. A full 1,068-question
main does not fit at high token usage; a measured pilot must set an affordable
question count before main. With these illustrative assumptions, the affordable
capacity is 1,063 questions in the low case and 461 in the high case, preserving
the $590 reserve. These are planning capacities, not selected sample sizes.
Some authoring, validation, canary and pilot forecasts exceed current stage
allocations, so those allocations require reconciliation before broader launches.
This preparation changes no cap, model, effort, prompt,
retention rule, seed, roster or measurement gate. OpenAI account usage limits and
configured hard limits are separate from prepaid credit; see
[official OpenAI spend-limit guidance](https://developers.openai.com/api/docs/guides/spend-limits).

The preflight is read-only and creates no provider client. Recorded provider
access passes; real validation throughput and sufficient confirmed account
credit remain prerequisites. Authoring completion does not close independent
answer-key validation. The supported commands above select workers from actual
timing and refuse a missing, incompatible or unsuccessful qualification.

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
