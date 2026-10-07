# Proposed Fable author output-cap amendment, 2026-10-07

Status: proposed. Jack chose this direction on October 7, after the expansion
qualification failed. Implementation and any rerun still need his explicit OK.
Nothing here is applied, and no paid request is authorized.

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

## Implementation, after approval

1. `finalphase/authoring.py`: `AUTHOR_MAX_TOKENS_BY_MODEL["fable"] = 128000`.
2. `finalphase/cli.py`: allow 128000 in `--max-tokens`.
3. `scripts/finalphase_expansion_qualify.py`: qualification scope
   `author-expansion-v2`, fresh receipt path, and refusal to reuse the v1 store.
4. Regenerate the cost forecasts and the qualification plan, update the tests that
   pin the 64,000 allowance and run the affected tests.
5. Run the unpaid prepare, plan and preflight, then fill in a new approval file
   for Jack's separate paid go.
