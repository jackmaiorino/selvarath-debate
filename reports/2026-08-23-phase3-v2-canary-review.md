# Phase-3 v2 canary review

Status: `HALT_AND_ESCALATE`. Main authorization is not ready.

This review is derived by `scripts/phase3_canary_closeout_v2.py` from the read-only v1 and v2
archives. The machine-readable result is
`rejudge/phase3_canary_closeout_v2_2026-08-23.json`. The derivation made no provider calls and
did not modify either archive.

## Result

- Manifest, authorization, deferral, result, decision, cache, and both usage-ledger chains:
  pass.
- Provisional completion under the Qwen carve-out: pass. The v2 store has exactly 1,500 rows:
  960 active judgments, 48 canary transcript references, and 492 main transcript references.
  The 192 Qwen2.5 judgment cells are deferred. All 288 carried anchors still match their exact
  v1 cell-key and event-hash bindings. There are no unexpected, missing, copied-anchor, or
  executed-deferred rows.
- Rendered structural gate: pass. All 480 active K2 pairs are mirrored, none are duplicated or
  malformed, and every active judge has an exact 48/48 rendered split on core b0.
- Strict-invalid gate: fail. `openai/gpt-oss-120b`, a continuing judge, has 3 INVALID rows out
  of 96, or 3.125%, against the frozen `<2%` threshold. The other active judges pass.

The three gpt-oss rows are genuine strict-parser failures. One response emits a query object
and then a verdict on the same line. Two emit only a query and await the oracle. This is not a
close-out parser defect.

## Mandatory paired diagnostics

These use the 48 rendered core-b0 mirror pairs per active judge. Position effect is signed as
error with the correct answer in A minus error with it in B. INVALID counts wrong. Semantic
consistency uses only pairs with two parseable verdicts. The variance ratio compares observed
pair-mean error variance with the independent-row reference. These are descriptive and have no
roster consequence.

| Judge | INVALID | Position effect | Semantic consistency | Variance ratio |
|---|---:|---:|---:|---:|
| Qwen3.7-Max | 0/96 | +8.33 pp | 44/48, 91.7% | 0.98 |
| gemma-3n-E4B | 0/96 | +2.08 pp | 41/48, 85.4% | 1.57 |
| gemma-4-31B | 1/96 | +2.08 pp | 46/47, 97.9% | 1.89 |
| Llama-3.3-70B | 0/96 | 0.00 pp | 44/48, 91.7% | 1.65 |
| gpt-oss-120b | 3/96 | 0.00 pp | 42/45, 93.3% | 1.62 |

## Pace and spend

The final uninterrupted window opens at `2026-08-22T21:51:40.753091Z` after the last STOP and
closes at `2026-08-23T09:41:25.515988Z`. It contains 1,352 unique rulings over 11.8291 elapsed
hours, a point rate of 2,743.07 rulings per 24 elapsed hours. The maximum ledger-event gap
inside the window is 207.05 seconds.

That point rate is not the protocol's required L90. V2 says L90 is a lower 90% bound over the
continuous rate, but it defines neither sampling units nor an interval construction. The old
calendar-day t interval contradicts the v2 window rule and is not reused.

Accounted canary-family spend is $34.56943544: $19.78244913 from v1 plus $14.78698631 from v2.
This is below the continued $60 canary cap, with $25.43056456 headroom. Unknown charges and the
three unmatched v1 reservations remain counted in full.

## Successor decision

Recommended disposition:

1. Keep the gpt-oss failure binding. Do not waive it and do not rerun the same 96 cells. The
   current protocol requires halt and owner escalation for a continuing-judge failure.
2. Do not authorize main spend. Continuing without gpt-oss requires a new protocol ID and hash.
   Under the current `failure_action`, a semantic repair also requires a complete canary rerun.
3. Leave Qwen2.5 deferred under its existing amendment until endpoint recovery, the
   `2026-08-29T00:00:00Z` boundary, or main authorization, whichever resolves it first.
4. Do not add a replacement merely to restore the nominal capability-slope p-value. If roster
   breadth is scientifically required, freeze the candidate eligibility rule from a fresh
   serverless-availability and price snapshot before seeing any candidate output. Then canary
   the complete successor roster under one new identity.
5. Before any paid successor canary, repair the forecast contract. It must include zero-query
   and early-DONE slots as zeros, charged failures and unknown charges, exact pinned tokenizers
   for all 492 main transcripts and role templates, cumulative spend across all segments, and
   a fresh price snapshot. Also freeze a mathematically explicit continuous-rate L90 formula.

No R_U, D_U, roster selection, or main-spend forecast is certified here. The historical v1
close-out applies the retired side-bias gate, calendar-day pace, success-only role samples,
chars-per-four token estimates, and v2-only accrued spend, so its apparent configuration output
is not decision-grade for v2.
