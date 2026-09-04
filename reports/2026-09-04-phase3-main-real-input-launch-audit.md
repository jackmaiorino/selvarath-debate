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

The full live-input path then rejected the 2026-08-21 main context blocklist. That file predates
the final r6 namespace, two-model roster, and r10 role limits. A fresh deterministic r6 report
is now tracked at `rejudge/phase3_main_context_blocklist_r6_2026-09-04.json`, raw SHA-256
`07c16736b1ae02c904af72a670e884ec562131e44a7186f2741986c4a2bc114e`. It recomputes exactly
from the frozen protocol, prompt bundle, role limits, 492 transcripts, and 10,332 main cells.
All 9,840 judgment cells remain context-eligible, so the exclusion count is zero.

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
forecast.

The corrected non-authorizing audit manifest is stored at
`E:/selvarath-archive/phase3-main-real-input-audit-2026-09-04/non-authorizing-expired-v5-audit-manifest-r2.json`.
Its raw SHA-256 is `2dfff8b66240c9a134f5ae6a990c23638f2dcc55e6a85606ab61e7aebcabc46f`,
its canonical SHA-256 is `1614c3d6c79a4ea1edf27469286ef2c1e7def5eff4ff7190039cb93f5eaef182`,
and its run ID is `phase3-main-3eeb61535ab5755d`. Normal live admission with the existing
v5 inputs cleared the protocol, inventory, prompt, role-limit, analysis-pin, scope, transcript,
and final r6 context gates, then failed exactly at `capacity evidence has expired`.

A separate read-only audit disabled only current capacity freshness in memory and supplied an
unsigned authorization-shaped object in memory. `load_prepared_main` then returned successfully,
which exercised the remaining capacity provenance, runtime-policy, authorization-shape, current
price, catalog context, billing, environmental-restart, exact-context, forecast, harness, and
canary-binding gates. The later diagnostic print failed on a nonexistent convenience `status`
field only after the loader had returned. The manifest's deliberately impossible formal output
root remained absent throughout. This audit does not create owner authority and is not launch
readiness evidence.

The remaining historical v5 capacity inputs are intentionally not launchable because their
measured validity expired. They will be replaced by the v6 capacity result after exact owner
ratification and detached authorization.
