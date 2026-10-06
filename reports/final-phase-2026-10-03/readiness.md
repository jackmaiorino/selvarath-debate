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

## Current validation execution, October 5

Jack reported OpenAI credit of $799.90 and Together credit of $854.83 in the
current goal chat. Anthropic's existing ledger estimate is $19.764805. These cover
the unchanged full-cohort maximum of $41.75890856: Anthropic $14.475730,
OpenAI $14.043700 and Together $13.23947856. No further deposit is needed for
this validation. Reported balances are preserved with their source; they are
not represented as API balance responses.

The API catalog confirms access to frozen `gpt-6-astra`. The billing endpoint
requires a browser session key, and management endpoints deny the inference
key's missing management scope. A read-only Together identity query succeeds
with the SDK-compatible HTTP transport; there is no balance endpoint at the
tested credit URL. Both successful receipts and errors remain in the private
run root. No inference or purchase was used for these checks.

Jack supplied current Astra limits of 4,000,000 TPM, 10,000 RPM and 200,000,000
Batch input tokens, plus a current $120 spending limit. Keep the $120 setting
for this task. The known October OpenAI ledger uses $1.852200 before validation,
so the ledger-derived remaining headroom is $118.147800. Unrelated account usage
and a separately scoped project override cannot be queried with this key; that
headroom is an estimate, and explicit provider spending refusals stop dispatch.
Deposits do not establish a usage tier. Published Tier 1 defaults are recorded
as background, while the account limits supplied by Jack govern this run.
No automatic tier graduation, credit purchase or account-setting change occurs.
See [OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)
and [spend limits](https://developers.openai.com/api/docs/guides/spend-limits).

The supported runner partitions batches by both request count and conservative
UTF-8 input-token bounds. Current waves allow at most 100 requests and 1,500,000
input tokens with one active batch per model. The cohort has 94 Astra requests
with a 949,752-token bound and 96 Fable requests with a 997,466-token bound.
Other active OpenAI queues are included conservatively before submission.
Completed and submitted identities reconnect. Submission is journaled before
networking; unknown acceptance remains possibly billable and cannot be resent.
Explicit provider creation refusals are recorded as unsent, preserving the
attempt allowance. Together rate-limit refusals apply backpressure without
creating a paid retry. Automatic live fallback remains disabled.

The full-cohort funding guard applies before qualification and validation.
All 757 canonical requests and eight serial controls retain their exact
scientific bodies, identities, models, output allowances and seeds. Their
input hash remains `075f348038e8e808a48d98fdf30e6c9e0b1d37c6b687414e738c02b7645d4959`.
Controls never enter retention. The qualification compares eight matched
Together inputs at one and eight workers, including provider inference,
collection and SQLite serialization; four canonical frontier probes then
check the frozen output format before the remaining cohort. Timing receipts
begin before dispatch. Resume includes elapsed downtime and never times cached
replay as fresh provider throughput.

The refreshed placement inventory observes 24 logical processors, 16 cores,
127.82 GiB RAM and existing local workloads. No local GPU is used. HaleysPC
still fails strict host-key verification through the existing trusted path;
trust is unchanged. RunPod's authenticated inventory shows five exited pods
and none running. No rental or resource reservation changed. Provider inference
runs remotely, with the finite collector on Jack's PC. Actual qualification
determines the local worker count.

All 56 affected tests pass in 17.13 seconds. Scoped type checks and the diff check
pass. Tests include wave capacity, partial-wave restart, ambiguous acceptance,
explicit unsent refusals and reservation of in-flight work. Author replay
recovers all 22 saved responses with zero provider calls and a bit-identical
primary database hash. SQLite online backup includes committed WAL contents.
Raw provider responses and full errors remain in the durable ledger.

Real Together timing now completed: eight valid calls took 68.519896 seconds
with one worker (0.116754 calls/second) and 26.150548 seconds with eight workers
(0.305921 calls/second), a 2.620208-fold speedup on identical frozen inputs.
The 16 Together responses cost $0.08323656. All four canonical frontier probes
completed valid outputs at frozen token allowances; total qualification spend
is $0.18778656. Qualification completed at 10:26 p.m. Eastern on October 5 and
selected eight workers. The remaining cohort is running. Two observed Together
503 transport errors remain recorded under their original identities; the current
one-attempt runner does not resend them. A prospective exact-body retry reservation
for those two requests is $0.04939308, with no funding gap. It is not dispatched,
and its final scope must be reconciled after the current collector settles. Measured
truncations and refusals are not eligible for transport retry.

The finite supported chain has started through:

```powershell
uv run --locked python scripts/finalphase_validate_authorized.py
```

It invokes guarded `qualify-validate`, then guarded `preflight` and `validate`
using the selected worker count. A qualification error stops the chain.
Its authoritative state is the existing manifest's `validation_execution`,
with output in `validation_supervisor.log` and errors in
`validation_supervisor.err`. Do not start a duplicate collector. To resume a
settled, interrupted chain, reuse the same command after resolving its recorded
constraint; do not resend possibly billable requests.

Validation writes every rejection and retention count by world, author and
task type. The original W001-Q01 mechanical rejection stays separate.
`validation_audit_packet.json` selects a reproducible random 5% of retained
questions and every split-validator question for the Claude coordinating
session required by `design.md:55`. Audit status remains pending until actual
per-question review. No paid coordinator audit is authorized. A source defect
in a retained question requires review of every retained question from that
world. The runner stops after this cohort; no authoring, canary, pilot or main
can be launched by this chain.

### Incremental later-phase funding plan

The current task requires no further deposit. Credit already deposited is
separate from the campaign's unchanged $6,000 spending ceiling, stage caps and
launch authorization. Purchase minimum/maximum and approved organization-tier
allowance are unavailable to the inference key. Check the purchase dialog when
a later deposit is needed; use its allowed amount and do not spend to force
graduation. The observed deposit succeeded, which supplies no promise about
future purchase limits.

Replace the earlier bulk-deposit targets with deposits for the next authorized,
fully reserved stage only. Preserve the existing reserve. After reserving this
cohort at its maximum, conservative available balances are Anthropic $5.289075,
OpenAI $785.856300 and Together $841.59052144. Settle actual validation charges
before calculating later gaps. OpenAI and Together already cover the existing
illustrative preparation-through-pilot forecast; Anthropic is the likely source
of incremental deposits. Every later stage still requires its own authority,
funding reservation, compatible throughput and scientific gates.

| Later stage | Illustrative total, low to high | Existing cap | Current blocker |
|---|---:|---:|---|
| Authoring, 160 worlds including precheck | $143.86 to $209.86 | $200 | Upper forecast exceeds cap; freeze remaining scope before any deposit |
| Full benchmark validation | $275.42 to $475.59 | $200 | Both forecasts exceed cap |
| Debater canary | $58.04 to $130.88 | $60 | Upper forecast exceeds cap |
| Pilot | $752.65 to $1,343.02 | $650 | Both forecasts exceed cap |
| Main, 1,068 questions | $4,186.61 to $7,470.53 | $4,300 | Upper forecast exceeds stage and campaign budgets |

The $120 OpenAI spending setting is sufficient for this cohort's $14.043700
maximum plus known prior usage. It does not fund the later full validation or
pilot within one month's known headroom: OpenAI's illustrative full validation
alone is $105.41 to $191.81, and the pilot is $221.10 to $379.04. A later plan
must reconcile the effective organization and project allowance, month already
used and stage cap before execution. More prepaid credit alone does not close
these blockers.

For a feasible incremental Anthropic preparation step, the illustrative
remaining 160-world authoring forecast is about $78.62 to $124.62 after the
already settled $20.234775 Anthropic authoring spend. Against the conservative
$5.289075 left after this validation reservation, the planning gap is about
$73.33 to $119.33. This is a forecast, not an authorized request maximum or
a top-up request. Reprice and reserve the chosen scope first; round a later
deposit only to the provider's actually allowed purchase increment.

The existing private `provider_funding_plan.json` retains its original low/high
forecast and adds the incremental plan and blockers. The full illustrative low
forecast plus the $590 reserve is approximately $6,017.53; even that nominal
1,068-question scope exceeds the unchanged ceiling. Its previously calculated
affordable capacities, 1,063 low or 461 high, remain illustrative and are not
selected sample sizes. A measured pilot must determine any later affordable
scope. No stage allocation, roster, token allowance or frozen measurement
changes here.

Canonical PR #1 remains the delivery item. Full historical CI fails in unchanged
Phase 3 files; affected checks passing does not clear those failed checks.
No merge or scientific audit is claimed. Issues are disabled in this repository.

### Settled collector and separately approved transport retries

The initial finite collector has exited with 755/757 canonical responses saved.
Its ledger records $8.75179620, including eight qualification controls, and 29
measured truncations. The provisional retention count is 71; it is not the final
result while two responses are missing. All four frontier batches are collected.
Original partial rows, summary, audit packet and manifest are preserved before
any retry changes.

Jack explicitly approved one exact-body retry for `factcheck:W001-Q06:2` and
`validate:W001-Q07:dspro:key_a`, at a combined maximum of $0.04939308. The
reservation includes the possibility of charges for their original 503 attempts.
Existing balances cover the additional reservation; the $200 validation and
$6,000 campaign limits stay unchanged. No measured truncation, refusal or
scientific rejection is retried.

The guarded command uses distinct transport IDs, keeps the original requests,
errors and attempt history, and charges each retry only on its transport row.
A zero-cost canonical response alias preserves the scientific question identity.
It requires the settled collector, unchanged body hashes, the real qualification
receipt, full-cohort funding and the separately recorded approval:

```powershell
uv run --locked python scripts/finalphase_validation_retry.py execute
```

After retry collection, the supported `preflight --stage validate --workers 8
--mode batch` and `validate --workers 8 --mode batch` commands recompute retention
from saved responses. All 64 affected tests pass in 25.36 seconds, including
retry authorization, precise body/identity/error/charge preservation, refusal
of other failed launch guards and safe resume of proven unsent retry rows.
Scoped type checks and the diff check pass. Final outcome and replay receipts
remain to be recorded after the two approved attempts settle.
