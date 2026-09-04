# Phase 3 main real-input launch audit

Date: 2026-09-04.

Status: offline real-input audit complete. No reviewer dispatch, provider call, Together call,
main run, or additional spend occurred.

## Findings corrected

The first real 28-input dry manifest exposed a predecessor-cost representation mismatch. The
certified forecast carried `119.27238489999999` after summing binary JSON numbers, while the
ratified manifest value is the exact eight-decimal ledger amount `119.27238490`. These values
refer to the same ledger total but failed exact `Decimal(str(value))` comparison. Forecast
materialization, launch materialization, and live validation now normalize every predecessor
actual and uncertain amount to the established eight-decimal ledger boundary using
`ROUND_HALF_UP`.

The first real semantic forecast recomputation then exposed an immutable-container mismatch.
`MainInventory` freezes nested dependency lists as tuples. Live recomputation converted only
the outer cell mappings back to dictionaries, leaving tuple dependencies that the canonical
plan validator correctly rejects. Live recomputation now fully thaws each validated inventory
cell before passing it to the forecast builder.

Forecast recomputation also previously used the current launch clock. Because the forecast
records `price_validation.age_seconds`, recomputing later necessarily changed the canonical
forecast even while the price snapshot remained fresh. Live validation now reconstructs and
validates the original certification clock from the bound snapshot time plus recorded age.
Current price freshness remains a separate launch-time gate and is not weakened.

## Corrected forecast

The corrected forecast was materialized without a provider call by reusing the still-fresh
price snapshot verified at `2026-09-04T21:18:47.663553Z`.

| Field | Value |
|---|---:|
| Path | `E:/selvarath-archive/phase3-main-current-forecast-v2-2026-09-04/cost_forecast.json` |
| Raw SHA-256 | `9d4690eb8dd311d09a99f1fe8cab545bb5688a7b0e8adab1d2a521d1af7b68c5` |
| Canonical SHA-256 | `744fa695b66777bd6bd7761e60b4698bfc1f8bbe107fe77ef198aaf70e78ffae` |
| Predecessor spend | USD 119.27238490 |
| Projected main spend | USD 908.82 |
| Projected stage total | USD 1,028.09238490 |
| Ratified cap | USD 1,100.00 |
| Headroom | USD 71.90761510 |
| Certification | `pass` |

The corrected forecast passed a real semantic recomputation against the exact tokenizer,
9,840 judgment-slot inventory, dynamic residual frame, raw Together catalog and serverless
endpoint inventory, 18-ledger reconciliation, and owner-ratified cap.

## Verification

The focused forecast, launch-materialization, and main-live suite passed with 117 tests passed
and two skipped. The actual 28-input dry manifest also passed construction with the corrected
forecast. The remaining historical v5 capacity inputs are intentionally not launchable because
their measured validity expired. They will be replaced by the v6 capacity result after exact
owner ratification and detached authorization.
