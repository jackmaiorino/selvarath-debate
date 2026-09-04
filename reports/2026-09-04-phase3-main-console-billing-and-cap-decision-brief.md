# Phase 3 main console billing and stage-cap decision brief

Date: 2026-09-04

Status: two owner decisions required. This brief grants no execution authority.

## Outcome

Together support closed the `/v1/billing/usage` enablement request and directed current
reconciliation to Cost Analytics and invoices in the Together console. The supported console
route is now implemented and validated offline.

The exact-window Cost Analytics view was rechecked for August 18 through August 29 UTC, which
maps to the ratified half-open interval ending August 30. It shows 146 rows and a total of
`$94.07`. The saved CSV independently reconstructs `$94.068746110` from its displayed
quantities and unit prices, which rounds to `$94.07`. Its amount column sums to `$94.02`
because each line is rounded to cents.

The September 1 monthly invoice covers August 1 through August 31. It shows a zero amount due
because prepaid commitments offset usage. The invoice establishes final monthly settlement,
but it is not used as the exact-window numeric total.

All three account components match the 2026-08-31 owner ratification:

- the CSV's sole explicit API-key-ID hash matches;
- the CSV's sole project-ID hash matches;
- the signed-in organization URL hash matches.

The offline candidate reopens the 18 owner-selected ledgers. It preserves all 827 unresolved
reservation records and produces:

| Quantity | Value |
|---|---:|
| Provider exact-window total | `$94.07` |
| Local settled total | `$95.68306917999999801859` |
| Provider minus local settled | `-$1.61306917999999801859` |
| Local uncertain reservations | `$23.5893157200000000378` |
| Exact local accounted total | `$119.27238489999999805639` |
| Retained eight-decimal predecessor upper bound | `$119.27238490` |

The proposed disposition is `closed_provider_final_below_local_actual`. It treats the provider's
final total as settlement of the exact window while retaining the larger local accounted amount
as the launch upper bound. It does not rewrite ledger history, erase ambiguous reservations, or
permit redispatch.

## Exact policy binding

The proposed console route is recorded in
`rejudge/phase3_main_console_billing_policy_proposal_2026-09-04.json`:

- raw SHA-256: `5c2a4997cf9ee64d457e855bcb869e2b53ae47e9ce146ff9f9c2330499da73a3`
- canonical SHA-256: `51e1bdaa687dbc8969b00819e89ad4475b025be8275b328f841cbcfe19f86c71`

The reconciliation file is deliberately a candidate with run ID
`phase3-main-candidate-2026-09-04`. After ratification, it will be regenerated without changing
the evidence or arithmetic for the exact final main run ID.

## Stage-cap decision

The separate 2026-09-03 cap brief remains unchanged:

- path: `reports/2026-09-03-phase3-main-stage-cap-decision-brief.md`
- raw SHA-256: `17ecafec8a9a91eb18ae40c74a59f7abf34e9a6db17a194508b43be79531c88c`
- certified projected main spend: `$908.82`
- retained predecessor upper bound: `$119.27238490`
- projected stage total: `$1,028.09238490`
- recommended stage cap: `$1,100.00`

The cap is a ceiling, not permission to spend. Fresh prices, capacity evidence, forecast,
manifest, and detached owner authorization remain mandatory before any paid main run.

## Exact ratification text

> I ratify the exact Phase 3 console billing policy proposal dated 2026-09-04, raw SHA-256
> 5c2a4997cf9ee64d457e855bcb869e2b53ae47e9ce146ff9f9c2330499da73a3 and canonical
> SHA-256 51e1bdaa687dbc8969b00819e89ad4475b025be8275b328f841cbcfe19f86c71. This includes
> retiring the unavailable /v1/billing/usage route for the selected Together account, using the
> exact-window Cost Analytics total of 94.07 USD as numeric settlement evidence, using the
> completed August invoice only for monthly finality and prepaid-credit treatment, preserving
> all 18 selected ledgers and all 827 unresolved reservation records unchanged, closing the
> provider-final total below local actual spend with disposition
> closed_provider_final_below_local_actual, and retaining 119.27238490 USD as the predecessor
> spend upper bound. I also ratify a 1,100.00 USD Phase 3 stage cap as proposed in the unchanged
> 2026-09-03 stage-cap brief with raw SHA-256
> 17ecafec8a9a91eb18ae40c74a59f7abf34e9a6db17a194508b43be79531c88c. This ratification
> authorizes offline policy, protocol, reconciliation, forecast, and manifest materialization
> only. It grants no reviewer dispatch, provider call, Together call, main run, or spend
> authority.

## Work after ratification

1. Record the owner decision and update the protocol cap without changing analysis estimands.
2. Refresh reviewer capacity and Together prices just in time.
3. Recompute the certified forecast under the ratified cap.
4. Generate the exact main run identity, reconciliation, harness receipt, and manifest.
5. Present a separate detached execution authorization for owner signature.

