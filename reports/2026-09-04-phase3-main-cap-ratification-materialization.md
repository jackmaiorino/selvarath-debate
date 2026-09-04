# Phase 3 main billing and stage-cap ratification materialization

Date: 2026-09-04.

Status: offline materialization complete. No reviewer dispatch, provider call, Together call,
main run, or spend was performed or authorized.

## Owner decision binding

The owner-ratified console billing policy and USD 1,100.00 stage cap are recorded in
`rejudge/phase3_main_console_billing_and_stage_cap_ratification_2026-09-04.json`.

| Binding | SHA-256 |
|---|---|
| Ratification record, raw | `9c199251617f33a0a5d721d02f1bca9532821a01850ce47eaf4ddc5df632fa5f` |
| Ratification record, canonical | `046ae59b04cea1462ff9cf14b120f8574b4a6b8d8c13ea4fde6b556ded4796b6` |
| Ratified console policy, raw | `5c2a4997cf9ee64d457e855bcb869e2b53ae47e9ce146ff9f9c2330499da73a3` |
| Ratified console policy, canonical | `51e1bdaa687dbc8969b00819e89ad4475b025be8275b328f841cbcfe19f86c71` |
| Ratified stage-cap brief, raw | `17ecafec8a9a91eb18ae40c74a59f7abf34e9a6db17a194508b43be79531c88c` |

The cap amendment changes only `decisions.spend.stage_cap_usd` for successor forecast and
manifest validation. The two-judge, five-condition, 82-question, 9,840-slot experiment design
is unchanged. The ratification validator rejects any execution, reviewer, provider, Together,
main-run, or spend authority in this record.

## Billing reconciliation

The immutable reconciliation candidate named by the ratified policy validates successfully
against all 18 selected ledgers and the finalized Together console evidence. Its filename is
retained because the policy binds its raw bytes. Rewriting or renaming it would break that
binding.

| Quantity | Validated value |
|---|---:|
| Selected ledgers | 18 |
| Unresolved reservations preserved | 827 |
| Local actual spend | $95.68306917999999801859 |
| Local uncertain spend | $23.5893157200000000378 |
| Local accounted spend | $119.27238489999999805639 |
| Together Cost Analytics exact-window total | $94.07 |
| Retained predecessor upper bound | $119.27238490 |
| Disposition | `closed_provider_final_below_local_actual` |

The reconciliation record raw SHA-256 is
`120320396a089ddea20ec5ea8ddcaf5c1ae56825179b76acf5fdbee1a458bab9`.
The selected ledger coverage raw SHA-256 is
`955903cfe429586748198ce8368b0ca91abedf5b6e7b2f558d04080d005667a5`.
The predecessor cumulative-spend selection remains
`rejudge/phase3_main_cumulative_spend_segments_2026-09-03.json`, raw SHA-256
`05f0f294a8ca601c54d9e365d70b9720ad10296331b211c3e4f32e2098aaae04`.

The predecessor reconciliation has a descriptive stage-scoped run ID. Manifest validation no
longer requires that ID to equal a new main run ID. Its raw hash is already a required manifest
input, and deriving the new run ID from an input whose contents must name that same run ID would
create a hash cycle.

## Ratified forecast

The offline successor forecast is:
`E:/selvarath-archive/phase3-main-ratified-forecast-2026-09-04/cost_forecast.json`.

| Quantity | Value |
|---|---:|
| Projected main spend | $908.82 |
| Predecessor upper bound | $119.27238490 |
| Projected stage total | $1,028.09238490 |
| Ratified stage cap | $1,100.00 |
| Headroom | $71.90761510 |
| Certification | `pass` |
| Within cap | `true` |

The forecast raw SHA-256 is
`cc22f6091458ad5c4df450588b09b99855ea872aabe0b520e7e7acf9cf8dac37` and its
canonical SHA-256 is
`a2836516c9919e273abf7a372196e4d49dd022bdf95c9cfe528de4f3dd3ece11`.
Relative to the prior certified forecast, only the schema version, explicit stage-cap binding,
cap value, and within-cap result changed. All cost line items are unchanged.

This is a historical decision-grade forecast, not launch-current evidence. It used the provider
price snapshot verified at `2026-09-02T12:39:20.158037Z`, which is outside its required 24-hour
freshness window.

## Manifest and capacity state

The main manifest schema is now v7. It requires the ratification record as a hashed input, and
the future detached main authorization must bind that exact raw hash. Live forecast validation
recomputes the owner-ratified cap and rejects a protocol-only or drifted cap.

No launch manifest was materialized. A manifest built now would contain expired launch evidence
and could not pass the live boundary. The two current freshness blockers are:

1. The Together price catalog and serverless endpoint snapshot is stale.
2. The v5 reviewer capacity result is historically valid but expired at
   `2026-09-03T12:17:04.166976Z`.

The v5 result still validates with freshness disabled: 180 complete rulings in
117.48771380000107 seconds, certifying 1,440 rulings per 24 hours. Its raw SHA-256 is
`91669a62cce498771c6e7bc83fe3df890e5f38ad7798744ba773e50e7dcf81f5`.
Current-freshness validation rejects it with `capacity evidence has expired`.

During this audit, live and provenance validation were corrected to derive the workload using
the plan's v1 through v5 derivation tag. The previous unconditional v1 derivation made valid v5
evidence fail deterministic rematerialization.

## Verification and next authority boundary

All `test_phase3_main_*` modules pass: 612 passed and 2 skipped. `git diff --check` also passes.

The next launch preparation requires two authorities that this ratification expressly withholds:

1. One fresh read-only Together catalog and serverless-endpoint capture for the two fixed model
   IDs and the selected account. This is a Together/provider call, even though it is not an
   inference request.
2. One fresh external reviewer capacity measurement under a byte-new successor workload. The
   exact successor proposal and its reviewer reservation accounting must be materialized and
   ratified before dispatch.

Main-run and spend authority are not yet requested. They become relevant only after fresh price
and capacity evidence produce a valid exact manifest.
