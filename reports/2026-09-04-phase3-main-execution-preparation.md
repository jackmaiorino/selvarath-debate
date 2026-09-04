# Phase 3 main execution preparation

Date: 2026-09-04.

Jack granted full spend and execute authority for Phase 3 in the active Codex task. This report
applies that authority to the already ratified Together account, fixed two-model experiment,
USD 1,100 cumulative stage cap, and 59,040 external reviewer dispatch ceiling. It does not
replace the repository's requirement for Jack's detached signatures over exact execution
authorization records.

## Fresh Together price evidence

The authenticated read-only refresh completed with zero inference calls at
`2026-09-04T21:18:47.663553Z`.

| Artifact | Raw SHA-256 | Canonical SHA-256 |
|---|---|---|
| `E:/selvarath-archive/phase3-main-price-2026-09-04/raw-provider-catalog.json` | `04ba1d1eb3f3057251b2423a7366176d2ada5db49fe669153a44073eb1a86d24` | `bbf53ef090deb6492ae02f9f5bb614ebdcf85ad88a709f44e1f3e9d00121e065` |
| `E:/selvarath-archive/phase3-main-price-2026-09-04/raw-serverless-endpoints.json` | `7aa28b2ccc0e0abc590bb70fdd669bc2d4c5aea7d3b8955ec967851be6722137` | `86cd60cb0438bfeb1a1e2d92cf229984efc5f7010ece9f4a7cc6bc7e015e77ad` |
| `E:/selvarath-archive/phase3-main-price-2026-09-04/price-snapshot-v2.json` | `a74715b4a7c5f6da6e6787c0f33b3838a17c6795b6532a788541d6ed3254dc78` | `61da226f413d2653d742aa1f4c4e033e65cb2f6277af316318e8da5112eae887` |

Both fixed models remain serverless. Their input and output prices are unchanged.

## Fresh-price forecast

The recomputed forecast is
`E:/selvarath-archive/phase3-main-current-forecast-2026-09-04/cost_forecast.json`,
raw SHA-256 `2c141f1ee5816cfa2154369f5807d718e72f9c5ee34447f948d2e2d029963547`,
canonical SHA-256 `6b619b987b73008fb9cdcae80ffaab2365ac1d1fa2397d284eeac5581ec59584`.

It passes with projected main spend USD 908.82, projected cumulative stage spend
USD 1,028.09238490, and USD 71.90761510 headroom under the ratified USD 1,100 cap.

## Reviewer-capacity successor

The v5 measurement remains a valid historical pass at 1,440 rulings per 24 hours, but its
24-hour evidence window expired. Its completed cohort and every v5 prompt are excluded from
reuse.

The remaining semantically neutral line-order transformation is materialized as the v6
proposal at
`rejudge/phase3_main_review_capacity_v6_successor_proposal_2026-09-04.json`, raw SHA-256
`b04cc7348763339d15469ab26b84effe0bc44775530b884f6ac3a09b26b52dd0`, canonical SHA-256
`d78cbfb64f51eacd75543020a34105d321e8ebbe98b24a81976733deff47becd`.

The proposal yields 571 byte-new eligible prompts, two disjoint 180-packet cohorts, and 211
unused reserves. It records 420 cumulative prior reviewer reservations, leaving 58,620 under
the 59,040 ceiling. Focused v6, v5, and capacity-preflight validation passed 70 tests.

The proposal uses a 1,080-hour capacity window. The previous 24-hour window could not govern
the predeclared workload: 59,040 rulings at the certified 1,440-per-day rate requires 41 days,
while every individual reviewer invocation is required to start inside the measured window.
The 1,080-hour window matches the predeclared 45-day D_max and is still invalidated immediately
by any reviewer model, effort, CLI, profile, version header, concurrency, wave-size, prompt, or
host change.

## Remaining owner actions

The next required decision is the exact v6 proposal ratification. After it is recorded, the
offline plan, workload, clean-commit execution manifest, and unsigned 180-dispatch
authorization can be materialized. Jack must then sign that exact authorization. Codex will
not use Jack's private key or synthesize the detached signature.

After the capacity pass, the current price evidence will be refreshed if needed, the exact main
manifest will be materialized, and Jack must sign its exact main authorization. The full main
run can then proceed unattended within the ratified USD and reviewer ceilings.
