# Phase 3 main stage-cap decision brief

Date: 2026-09-03. Source commit:
`c5de9d7e614528245bfc5fe0e5fc6549f55b1329`.

Status: decision support only. This brief authorizes no reviewer dispatch, provider call,
Together call, main run, or spend.

## Certified forecast result

The corrected offline materializer bound 381 completed canary judgment rows and three
validated terminal-disposition records to all 384 planned canary judgment slots. The focused
forecast, main-live, and main-manifest regression set passed with 113 tests passed and two
skipped.

| Quantity | Value |
|---|---:|
| Ratified predecessor actual spend | $95.68306918 |
| Ratified predecessor uncertain spend | $23.58931572 |
| Ratified predecessor accounted spend | $119.27238490 |
| Certified projected main spend | $908.82 |
| Certified projected stage total | $1,028.09238490 |
| Current protocol stage cap | $450.00 |
| Cap overrun | $578.09238490 |
| Minimum whole-cent cap that contains this forecast | $1,028.10 |

The cost forecast certified its input and price gates but returned
`within_stage_cap: false`. The forecast already uses per-question clustered U90 estimates,
full reserved input and output tokens for unknown charges, a 1.15 transport multiplier, and
per-line whole-cent ceiling. The result is therefore not a simple mean-cost extrapolation.

The materialized artifacts are under
`E:/selvarath-archive/phase3-main-forecast-2026-09-03`:

| Artifact | Raw SHA-256 | Canonical SHA-256 |
|---|---|---|
| `slot_role_frame.json` | `20dae59cc3cc610f1730575ecf58571f01aa5a9f0ef197db65577df4668d5c01` | `9b1911f51224cd8ac3a13b6f97def6843235aafacf202b188575541d2dc0f175` |
| `dynamic_residual_frame.json` | `72430cc1414ecfe29edce9839d0bf0807d305f61cef0953611a55845deef2c55` | `188f7f4a89347ce9ee852be89cbaf6a0af1e1b3fe5ce3b44aa3acc48722e9db6` |
| `cost_forecast.json` | `919ae1d984a10a8f5305fb96a6ff229ef57f93f7b79c7a29a3e91f8bf952310a` | `59c20f3cfda2f33bc086e3bb13afb707f12064e13f2a12c9b7bef676673c9670` |

The forecast used price snapshot canonical SHA-256
`f06c972d45255ab4483fca7931f8c51670c493ad9deb0f144200d76fcd37eb5e`
at `2026-09-03T11:42:00Z`, when its measured age was 82,959.841963 seconds and still
inside the required 24-hour window. That snapshot is historical evidence after its freshness
window expires and must be refreshed before launch.

The predecessor selection is materialized in
`rejudge/phase3_main_cumulative_spend_segments_2026-09-03.json`, raw SHA-256
`05f0f294a8ca601c54d9e365d70b9720ad10296331b211c3e4f32e2098aaae04`, canonical
SHA-256 `919bd50542654cc55a47da47a4d049e6d553b11755bc7a51268e06abd0aa192b`.
It exactly matches the owner-ratified 18-source union.

## Why $450 cannot preserve the ratified experiment

The protocol fixes two endpoint-specific judges, five conditions, 82 questions, and 9,840
judgment slots. Qwen-source slots account for $728.31 of projected main spend and Llama-source
slots account for $180.51. Removing Qwen would fit the old cap but violates the fixed-roster
rule and removes the endpoint comparison the owner confirmed.

The predeclared 41-question fallback applies only to b4 and b8 and was designed for reviewer
workload limits, not this cost failure. Even a simple half-cost approximation for those two
conditions leaves about $749.94 of total stage cost. Removing b4 and b8 entirely would still
leave about $471.79 and would destroy D4, D8, and S1. Neither path solves the $450 cap while
preserving the approved design.

Fitting under $450 would require a new, materially smaller experiment. At the observed average
cost it would retain no more than about 29 of 82 questions before exact stratified selection and
new power analysis. That is a new protocol decision, not a routine main-run materialization.

## Lead recommendation

Preserve the ratified experiment and replace the $450 stage cap and the earlier uncertified
$875 planning proposal with a $1,100 stage cap. This leaves $71.90761510 above the current
certified conservative total. It does not authorize spending. The exact final main manifest and
detached owner authorization must independently bind an amount no greater than this cap.

Provider billing reconciliation is still pending because Together has not enabled the selected
organization's billing-usage API. The exact-window dashboard showed $94.07, which is below the
local conservative predecessor envelope and would not close the cap gap. If authoritative
billing or a refreshed price snapshot causes the certified total to exceed $1,100, launch stays
blocked and requires a new owner decision.

## Exact non-execution ratification text

If the recommendation is accepted, Jack can reply with exactly:

> I ratify the 2026-09-03 Phase 3 main stage-cap decision for the existing two-judge,
> five-condition, 82-question, 9,840-judgment-slot design. I replace the USD 450.00 protocol
> stage cap and supersede the earlier USD 875.00 planning proposal with a USD 1,100.00 stage
> cap. This decision is bound to predecessor accounted spend USD 119.27238490 and the certified
> projected main spend USD 908.82, for projected stage total USD 1,028.09238490. If fresh
> provider billing or pricing raises the certified total above USD 1,100.00, a new decision is
> required. This ratification authorizes offline cap-amendment and successor-protocol
> materialization only. It grants no reviewer dispatch, provider call, Together call, main run,
> or spend authority.

Any different cap or design scope is a different decision. General approval or an instruction
to continue does not ratify this cap.
