# Fable author output-cap amendment, 2026-10-07

Status: implemented in code on October 7, after Jack's OK. No paid request is
authorized; the v2 qualification needs his separate paid go.

## Why

The 52-call expansion qualification (approved October 6) stopped at step 2 of 4.
In the parallel author arm, Fable's W001 request (`claude-fable-5-1`, high effort,
frozen `length-v2` prompt) used its entire 64,000-token output allowance and
returned no world text. The identical request in the serial arm finished at
41,665 output tokens. The other three author controls passed; the 48 Together
token controls never ran. Observed spend: $3.48 of the $7.84778520 maximum. The
qualifier refused to continue and made no retry. The failed receipt is preserved
as `expansion_qualification_receipt.json` (`author_parallel.valid = false`).

History of the Fable allowance: all eight 32,000-token precheck attempts
truncated; completions at 64,000 have been about 41,000 tokens (40,985 in the
precheck, 41,665 here). One of two calls at 64,000 now reaches the cap.
Thinking is always on for Fable and its depth is set only by effort, so the
spread of reasoning length is wide at a fixed effort. Across about 76 remaining
Fable worlds, a cap the model reaches even occasionally would waste paid calls
and leave worlds missing.

## Change

- Fable author `max_tokens`: 64,000 to **128,000**, the model's maximum output.
- Unchanged: model, `high` effort, the frozen `length-v2` prompt, seeds, the
  1,000 to 1,500 word admission rule, mechanical checks, validation, retention
  rules, Astra (32,000) and every validator allowance.
- A truncation at 128,000 remains a failure: no salvage, and no automatic
  retry, effort change or prompt tuning.
- New request identities: the author `custom_id` already carries `:t<max_tokens>`
  when the allowance differs from the 32,000 base, so 128,000 requests cannot
  collide with any saved 64,000 request. The rerun uses a new qualification
  scope (`author-expansion-v2`) and a new receipt. The failed v1 receipt, call
  store and logs stay unchanged.
- The `max_tokens` cap only bounds output. A completion of about 41,000 tokens
  costs the same at either cap; only calls that would have run past 64,000 cost more.

## Price

Fable batch output is $25 per million tokens, half the $50 list rate. Reserved
maximum per Fable author request rises from about $1.60 to about **$3.20** of
output, plus unchanged input.

| Scope | Current maximum | With 128,000 | Change |
|---|---:|---:|---:|
| Qualification, Anthropic (2 Fable calls) | $3.267580 | about $6.467580 | +$3.20 |
| Qualification, total (52 calls) | $7.84778520 | about **$11.05** | +$3.20 |
| Expansion authoring, per Fable request slot | about $1.60 output | about $3.20 output | +$1.60 |

Expected actual spend changes little: at about 41,000 tokens a Fable world costs
about $1.05 in batch either way. The larger maximum affects reservations and
caps. Author-stage preflight already reserves $396.64 at 64,000, against the
unchanged $200 author cap, and that cap decision was already open. The exact
expansion envelope is recomputed by `finalphase_expansion_prepare.py` after
implementation and replaces the estimates above.

## Implementation

Jack approved implementation on October 7 ("implement it"). Paid execution still
needs his separate approval.

1. `finalphase/authoring.py`: new `EXPANSION_AUTHOR_MAX_TOKENS_BY_MODEL`
   (Fable 128,000, Astra 32,000), the default for worlds after W008. W001 to W008
   keep 64,000 and their saved `:t64000` identities, so offline replay of the
   frozen cohort is unchanged.
2. `finalphase/cli.py`: `--max-tokens` also accepts 128000.
3. `finalphase/expansion.py` and the qualification scripts: v2 scope
   `author-expansion-v2`, version `benchmark-expansion-qualification-v2`, a fresh
   `expansion_qualification_v2_receipt.json` and `expansion_qualification_v2.db`.
   The v1 receipt, store and logs are never reopened.
4. Tests cover the per-world default, the v2 controls and their allowance, and
   that v2 never reuses v1 paths. Cost-forecast token assumptions are unchanged
   because they model expected use, not the cap. The exact reservation envelope
   comes from `finalphase_expansion_prepare.py`.
5. Next, on Jack's PC: preserve the five v1 prepare outputs, run the unpaid
   prepare, plan and preflight on this code, and fill in a new v2 approval file
   for Jack's paid go.

## Qualification v2 and the author-stage cap, 2026-10-07

Jack approved the v2 qualification ("launch v2"). It passed: all 4 steps valid,
52 of 52 calls ok, no cutoffs or refusals, $3.56 spent of $11.05 (qualification
total $7.04 with v1). Parallel authoring took 457 s against 1,000 s serial and
Together validation took 139 s with 8 workers against 640 s serial. Both Fable
controls finished under 64,000 tokens (49,827 and 30,418), so the run shows the
128,000 setting works end to end but does not measure how often it rescues a
cutoff. Receipt: `expansion_qualification_v2_receipt.json`, code e83399e.

At 128,000 tokens the full-cohort author reservation is $639.84, above the $200
author cap. Realistic spend is about $130 to $195. Jack chose to raise the author
cap to $648, the prepared allocation need. `finalphase/cli.py` now sets
author $648 and main $3,852 (down $448 from $4,300), so stage caps plus the $590
reserve still total $6,000. `finalphase/expansion.py` checks the reservation
against that cap instead of a hardcoded $200. The validation cap is unchanged
at $200; raising it to the prepared $1,939 is a separate decision and would
bring main to $2,113.

This changes only spend limits, not request bodies or identities. It does change
`preflight.execution_sha256()`, so registering the v2 throughput receipt for the
expansion must record that the qualified code (e83399e) differs from the launch
code only by these caps. No paid request is authorized by this change.
