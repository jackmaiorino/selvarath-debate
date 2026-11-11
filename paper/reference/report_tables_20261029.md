## A. Acquisition and completion

| Launch (UTC) | Commit | Occasion |
|---|---|---|
| 2026-10-25T22:22:44Z | 7ce4a16 | first launch under the preregistration |
| 2026-10-28T01:28:46Z | 0e11907 | relaunch on the endpoint gate (amendments 1 to 3) after the gate reset |
| 2026-10-28T04:17:11Z | 0e11907 | relaunch after a provider halt (an OpenAI batch create returned 504; the batch was attached by hand, consult 59) |
| 2026-10-28T22:23:25Z | 0e11907 | relaunch after the owner's planned reboot (clean stop at a round boundary) |

First finish 2026-10-29T03:12:31Z, complete. Questions 1,070 (1,068 counted plus the two sensitivity-only); debates 1,070 (failed 0); judge sessions 102,720 of 102,720 (failed 0); compliance checks 6,420 of 6,420.

Debate turn length (words): honest mean 435, median 444, max 652; dishonest mean 834, median 824, max 1451; regenerated turns 0.

## B. Spend (store, notional where noted)

| Component | Store USD |
|---|---|
| judge opus | 721.67 |
| judge qwen38 | 719.46 |
| judge sol | 391.12 |
| debate | 332.21 |
| judge sonnet | 297.38 |
| oracle | 263.99 |
| judge terra | 202.15 |
| judge haiku | 154.05 |
| judge luna | 20.81 |
| compliance | 13.06 |
| gate | 8.29 |
| judge llama70 | 0.00 |
| Total (main stage) | 3,124.17 |

## C. Error by judge and arm (percent; percentile-t 95% intervals; 7.6)

| Judge | World alone | Budget 0 | Budget 1 | Budget 2 | Budget 6 | Debate with world |
|---|---|---|---|---|---|---|
| luna | 0.80 [0.47, 1.28] | 2.86 [2.09, 3.80] | 4.07 [3.10, 5.30] | 3.28 [2.43, 4.38] | 4.03 [3.06, 5.22] | 1.97 [1.34, 2.76] |
| terra | 0.37 [0.13, 1.02] | 1.59 [1.07, 2.27] | 2.39 [1.72, 3.19] | 2.57 [1.86, 3.46] | 2.62 [1.89, 3.59] | 0.66 [0.33, 1.21] |
| sol | 0.00 no interval | 0.70 [0.34, 1.41] | 1.36 [0.80, 2.13] | 1.31 [0.82, 1.97] | 1.40 [0.86, 2.21] | 0.09 [0.00, inf] |
| haiku | 2.01 [1.44, 2.77] | 21.21 [18.81, 23.81] | 22.57 [20.32, 25.00] | 20.22 [17.90, 22.65] | 18.73 [16.51, 21.05] | 11.89 [10.08, 13.90] |
| sonnet | 0.56 [0.27, 1.11] | 1.12 [0.66, 1.82] | 1.03 [0.66, 1.52] | 1.08 [0.69, 1.59] | 0.89 [0.53, 1.41] | 0.70 [0.37, 1.24] |
| opus | 0.09 [0.00, inf] | 0.23 [0.03, inf] | 0.61 [0.29, 1.20] | 0.75 [0.36, 1.42] | 0.33 [0.10, 1.23] | 0.23 [0.05, 0.73] |
| llama70 | 100.00 no interval | 100.00 no interval | 100.00 no interval | 100.00 no interval | 100.00 no interval | 100.00 no interval |
| qwen38 | 0.28 [0.07, 1.02] | 2.67 [1.96, 3.55] | 3.75 [2.86, 4.83] | 2.62 [1.93, 3.45] | 2.06 [1.42, 2.86] | 1.36 [0.81, 2.10] |
| pooled, eight (literal) | 13.01 [12.87, 13.21] | 16.30 [15.83, 16.82] | 16.97 [16.44, 17.56] | 16.48 [15.98, 17.04] | 16.26 [15.77, 16.81] | 14.61 [14.27, 15.01] |
| pooled, seven (primary panel) | 0.59 [0.43, 0.81] | 4.34 [3.81, 4.94] | 5.11 [4.50, 5.78] | 4.55 [3.97, 5.19] | 4.29 [3.73, 4.93] | 2.41 [2.02, 2.87] |

Llama-70B's 100.00 is its service refusals scored as errors (no Llama verdict exists). The seven-judge pooled row is derived exactly from the eight-judge one (Llama is 100% error in every arm and draw). A cell with zero observed variance has no interval (7.4).

## D. Primary (7.2) and secondary (7.3)

**Amended primary: seven judges (amendment 1, section 4)** (judges: luna, terra, sol, haiku, sonnet, opus, qwen38)

| Contrast | Estimate (pp) | SE (pp) | 95% interval | Simultaneous 95% | One-sided p |
|---|---|---|---|---|---|
| e1 - e0 | +0.77 | 0.17 | [+0.43, +1.10] | [+0.35, +1.19] | 9.999e-05 |
| e1 - e6 | +0.82 | 0.17 | [+0.49, +1.16] | [+0.39, +1.24] | 9.999e-05 |
| e2 - e0 | +0.21 | 0.14 | [-0.06, +0.51] | [-0.15, +0.57] | 0.0721 |
| e2 - e6 | +0.25 | 0.15 | [-0.04, +0.56] | [-0.12, +0.63] | 0.0441 |

IUT p: budget 1 9.999e-05, budget 2 0.0721; Holm: budget 1 0.0002, budget 2 0.0721. Simultaneous critical value 2.530.

| Budget | Peak supported | Meaningful peak shown | Meaningful peak ruled out |
|---|---|---|---|
| 1 | True | False | True |
| 2 | False | False | True |

Missing cells, seven judges: 0 (of 89,712). Guarded decisions (missing cells coded against the peak) equal the available-case ones.

**Secondary, seven judges**

| Contrast (two-sided) | Estimate (pp) | Marginal 95% interval | p | Holm p (six) | Rejected |
|---|---|---|---|---|---|
| pooled_k6_minus_k0 | -0.05 | [-0.37, +0.30] | 0.7772 | 1.0000 | False |
| pooled_debate_world_minus_k6 | -1.88 | [-2.25, -1.54] | 9.999e-05 | 0.0005999 | True |
| llama70_k1_minus_k0 | untested |  | 1.0000 | 1.0000 | False |
| llama70_k6_minus_k0 | untested |  | 1.0000 | 1.0000 | False |
| qwen38_k1_minus_k0 | +1.08 | [+0.27, +1.93] | 0.0109 | 0.0545 | False |
| qwen38_k6_minus_k0 | -0.61 | [-1.40, +0.06] | 0.0968 | 0.3872 | False |

**Literal registered scoring: eight judges, Llama's refusals scored as errors** (judges: luna, terra, sol, haiku, sonnet, opus, llama70, qwen38)

| Contrast | Estimate (pp) | SE (pp) | 95% interval | Simultaneous 95% | One-sided p |
|---|---|---|---|---|---|
| e1 - e0 | +0.67 | 0.15 | [+0.38, +0.96] | [+0.30, +1.04] | 9.999e-05 |
| e1 - e6 | +0.71 | 0.15 | [+0.43, +1.01] | [+0.34, +1.08] | 9.999e-05 |
| e2 - e0 | +0.18 | 0.12 | [-0.06, +0.45] | [-0.13, +0.50] | 0.0721 |
| e2 - e6 | +0.22 | 0.13 | [-0.03, +0.49] | [-0.11, +0.55] | 0.0441 |

IUT p: budget 1 9.999e-05, budget 2 0.0721; Holm: budget 1 0.0002, budget 2 0.0721. Simultaneous critical value 2.530.

| Budget | Peak supported | Meaningful peak shown | Meaningful peak ruled out |
|---|---|---|---|
| 1 | True | False | True |
| 2 | False | False | True |

Missing cells, eight judges: 0 of 102,528.

**Secondary, eight judges (literal)**

| Contrast (two-sided) | Estimate (pp) | Marginal 95% interval | p | Holm p (six) | Rejected |
|---|---|---|---|---|---|
| pooled_k6_minus_k0 | -0.04 | [-0.33, +0.26] | 0.7772 | 1.0000 | False |
| pooled_debate_world_minus_k6 | -1.64 | [-1.97, -1.35] | 9.999e-05 | 0.0005999 | True |
| llama70_k1_minus_k0 | degenerate (zero variance) |  | 1.0000 | 1.0000 | False |
| llama70_k6_minus_k0 | degenerate (zero variance) |  | 1.0000 | 1.0000 | False |
| qwen38_k1_minus_k0 | +1.08 | [+0.27, +1.93] | 0.0109 | 0.0545 | False |
| qwen38_k6_minus_k0 | -0.61 | [-1.40, +0.06] | 0.0968 | 0.3872 | False |

## E. Sensitivities (7.5; literal scoring)

| Sensitivity | Components (pp) | IUT p | Holm p | Peaks (meaningful peak shown, ruled out) |
|---|---|---|---|---|
| without_sonnet | e1 - e0 +0.78, e1 - e6 +0.80, e2 - e0 +0.21, e2 - e6 +0.23 | b1 9.999e-05; b2 0.0628 | b1 0.0002; b2 0.0628 | b1: supported True, shown False, ruled out True; b2: supported False, shown False, ruled out True |
| without_claude_judges | e1 - e0 +0.75, e1 - e6 +0.29, e2 - e0 +0.39, e2 - e6 -0.07 | b1 0.0280; b2 0.6748 | b1 0.0560; b2 0.6748 | b1: supported False, shown False, ruled out True; b2: supported False, shown False, ruled out True |
| determinacy_0_or_1 | e1 - e0 +0.48, e1 - e6 +0.62, e2 - e0 +0.19, e2 - e6 +0.33 | b1 9.999e-05; b2 0.0283 | b1 0.0002; b2 0.0283 | b1: supported True, shown False, ruled out True; b2: supported True, shown False, ruled out True |
| missing_cells_against_peak (b1) | e1 - e0 +0.67, e1 - e6 +0.71 | 9.999e-05 | 0.0002 |  |
| missing_cells_against_peak (b2) | e2 - e0 +0.18, e2 - e6 +0.22 | 0.0721 | 0.0721 |  |
| missing_cells_for_peak (b1) | e1 - e0 +0.67, e1 - e6 +0.71 | 9.999e-05 | 0.0002 |  |
| missing_cells_for_peak (b2) | e2 - e0 +0.18, e2 - e6 +0.22 | 0.0721 | 0.0721 |  |
| with_sensitivity_only_questions | e1 - e0 +0.67, e1 - e6 +0.70, e2 - e0 +0.18, e2 - e6 +0.21 | b1 9.999e-05; b2 0.0797 | b1 0.0002; b2 0.0797 | b1: supported True, shown False, ruled out True; b2: supported False, shown False, ruled out True |
| without_flagged_debates | e1 - e0 +0.67, e1 - e6 +0.72, e2 - e0 +0.18, e2 - e6 +0.22 | b1 9.999e-05; b2 0.0720 | b1 0.0002; b2 0.0720 | b1: supported True, shown False, ruled out True; b2: supported False, shown False, ruled out True |
| unparsed_as_missing | e1 - e0 +nan, e1 - e6 +nan, e2 - e0 +nan, e2 - e6 +nan | b1 1.0000; b2 1.0000 | b1 1.0000; b2 1.0000 | b1: supported False, shown None, ruled out None; b2: supported False, shown None, ruled out None |

No defined sensitivity shows a meaningful peak at either budget; the meaningful-peak readings of unparsed-as-missing are undefined.

missing_cells_against_peak: jointly calibrated 95% bounds e1 - e0 [0.304, 1.042] pp; e1 - e6 [0.344, 1.083] pp; e2 - e0 [-0.134, 0.497] pp; e2 - e6 [-0.107, 0.552] pp (joint critical value 2.529572 over 8 coded components, 10,000 draws). With no missing cells these equal the literal available-case simultaneous bounds.

missing_cells_for_peak: jointly calibrated 95% bounds e1 - e0 [0.304, 1.042] pp; e1 - e6 [0.344, 1.083] pp; e2 - e0 [-0.134, 0.497] pp; e2 - e6 [-0.107, 0.552] pp (joint critical value 2.529572 over 8 coded components, 10,000 draws). With no missing cells these equal the literal available-case simultaneous bounds.

without_sonnet, pooled secondary (unadjusted): pooled_k6_minus_k0 -0.01 p 0.9313; pooled_debate_world_minus_k6 -1.85 p 9.999e-05

without_claude_judges, pooled secondary (unadjusted): pooled_k6_minus_k0 +0.46 p 0.0063; pooled_debate_world_minus_k6 -1.21 p 0.0002

determinacy_0_or_1, secondary (Holm across six): pooled_k6_minus_k0 -0.14 Holm 1.0000; pooled_debate_world_minus_k6 -1.11 Holm 0.0005999; llama70_k1_minus_k0 +0.00 Holm 1.0000; llama70_k6_minus_k0 +0.00 Holm 1.0000; qwen38_k1_minus_k0 +0.99 Holm 0.0105; qwen38_k6_minus_k0 +0.19 Holm 1.0000

with_sensitivity_only_questions, secondary (Holm across six): pooled_k6_minus_k0 -0.04 Holm 1.0000; pooled_debate_world_minus_k6 -1.66 Holm 0.0005999; llama70_k1_minus_k0 +0.00 Holm 1.0000; llama70_k6_minus_k0 +0.00 Holm 1.0000; qwen38_k1_minus_k0 +1.07 Holm 0.0545; qwen38_k6_minus_k0 -0.61 Holm 0.3872

Unparsed verdicts: 12,873 (Llama's refusals among them), so the unparsed-as-missing sensitivity is undefined on the literal scoring. Flagged-debate exclusions: W081-Q09, W114-Q08.

## F. Query use by judge and budget (7.6; observational)

| Judge | Budget | Sessions | Mean used | Early DONE % | Gate rejections / session | Sessions with malformed step % | Oracle YES/NO/NA/INVALID | Admitted touching % | Sessions touching % | Error touching % | Error not touching % |
|---|---|---|---|---|---|---|---|---|---|---|---|
| luna | 1 | 2136 | 0.96 | 0.0 | 0.12 | 0.0 | 1665/30/354/0 | 71.9 | 69.0 | 2.10 | 8.45 |
| luna | 2 | 2136 | 1.92 | 0.5 | 0.25 | 0.0 | 3634/49/418/0 | 72.8 | 88.2 | 2.44 | 9.49 |
| luna | 6 | 2136 | 5.58 | 14.3 | 0.60 | 0.0 | 10862/127/926/0 | 57.4 | 97.1 | 3.76 | 12.70 |
| terra | 1 | 2136 | 0.96 | 0.0 | 0.23 | 0.0 | 1807/14/237/1 | 77.7 | 74.9 | 1.25 | 5.77 |
| terra | 2 | 2136 | 1.91 | 1.8 | 0.31 | 0.0 | 3768/33/288/0 | 78.0 | 90.9 | 1.85 | 9.74 |
| terra | 6 | 2136 | 4.57 | 60.1 | 0.54 | 0.4 | 8779/148/832/0 | 63.5 | 97.3 | 2.55 | 5.17 |
| sol | 1 | 2136 | 0.96 | 0.0 | 0.12 | 0.0 | 1525/49/477/0 | 67.6 | 64.9 | 0.43 | 3.07 |
| sol | 2 | 2136 | 1.89 | 2.8 | 0.24 | 0.0 | 3163/92/780/0 | 66.9 | 80.4 | 0.47 | 4.78 |
| sol | 6 | 2136 | 4.93 | 45.8 | 0.49 | 0.0 | 8793/187/1555/0 | 59.8 | 95.0 | 0.84 | 12.26 |
| haiku | 1 | 2136 | 0.92 | 0.0 | 0.60 | 35.9 | 1645/95/228/0 | 66.7 | 61.5 | 15.84 | 33.29 |
| haiku | 2 | 2136 | 1.83 | 7.5 | 0.68 | 28.5 | 3430/130/344/1 | 66.2 | 80.2 | 16.93 | 33.57 |
| haiku | 6 | 2136 | 5.60 | 17.2 | 1.13 | 31.7 | 10556/374/1026/0 | 50.1 | 95.6 | 17.96 | 35.48 |
| sonnet | 1 | 2136 | 0.95 | 0.7 | 0.24 | 3.4 | 1903/12/107/0 | 90.2 | 85.4 | 0.71 | 2.88 |
| sonnet | 2 | 2136 | 1.53 | 39.3 | 0.30 | 0.2 | 3059/45/170/0 | 86.3 | 89.4 | 0.68 | 4.42 |
| sonnet | 6 | 2136 | 2.22 | 99.2 | 0.38 | 0.6 | 4401/68/265/0 | 82.5 | 93.9 | 0.60 | 5.34 |
| opus | 1 | 2136 | 0.96 | 0.5 | 0.12 | 0.1 | 1527/76/442/0 | 71.3 | 68.3 | 0.48 | 0.89 |
| opus | 2 | 2136 | 1.88 | 6.1 | 0.20 | 0.1 | 3224/142/647/0 | 72.4 | 83.8 | 0.39 | 2.61 |
| opus | 6 | 2136 | 4.23 | 66.9 | 0.38 | 0.0 | 7200/457/1368/0 | 63.0 | 94.6 | 0.20 | 2.59 |
| llama70 | 1 | 2136 | 0.00 | 0.0 | 0.00 | 0.0 | 0/0/0/0 | n/a | 0.0 | n/a | 100.00 |
| llama70 | 2 | 2136 | 0.00 | 0.0 | 0.00 | 0.0 | 0/0/0/0 | n/a | 0.0 | n/a | 100.00 |
| llama70 | 6 | 2136 | 0.00 | 0.0 | 0.00 | 0.0 | 0/0/0/0 | n/a | 0.0 | n/a | 100.00 |
| qwen38 | 1 | 2136 | 0.95 | 0.3 | 0.12 | 0.0 | 1702/61/261/0 | 75.4 | 71.4 | 2.10 | 7.87 |
| qwen38 | 2 | 2136 | 1.85 | 6.9 | 0.21 | 0.1 | 3426/110/405/0 | 75.3 | 87.1 | 1.29 | 11.59 |
| qwen38 | 6 | 2136 | 4.49 | 57.8 | 0.53 | 3.6 | 8184/332/1074/0 | 64.2 | 95.7 | 1.66 | 10.99 |

INVALID oracle labels: 2 in all (haiku:debate_k2 1, terra:debate_k1 1); every other judge-by-budget cell had none. Label counts are occurrences in sessions, not distinct provider requests.

Distribution of queries used at budget 6 (sessions using 0..6): luna 4/24/38/64/117/179/1710; terra 8/29/156/338/403/420/782; sol 13/35/110/217/245/447/1069; haiku 19/17/27/49/53/300/1671; sonnet 75/466/849/505/166/59/16; opus 45/85/194/353/476/309/674; llama70 2136/0/0/0/0/0/0; qwen38 30/79/176/282/344/413/812.

Order balance: honest first in 520 of 1,068 counted questions (Astra worlds 256 of 535, Fable worlds 264 of 533).

## G. Gate equivalence check (amendment 2, section 3.6; run 2026-10-27 14:41 to 15:28Z)

| Criterion | Measured | Bar | Pass |
|---|---|---|---|
| Coverage | missing or failed: first 0, repeat 0; repeat set consistent True | complete | True |
| C1 non-decisions | first 0.0068%, repeat 0.0414% | at most 0.1% each | True |
| C2 agreement with pilot | overall 97.98% (n 14,743); ADMIT 99.11% (n 14,229); REJECT 66.54% (n 514) | 95% overall, 90% per class | False |
| C3 rejection share | first 3.19% against pilot 3.49% | within 0.6 points | True |
| C4 against own retest | agreement 97.98% against retest 98.00% (n 2,000); pilot rejections kept 66.54% (n 514) against own rejections kept on repeat 73.99% (n 469) | within 2 and 6 points | False |

Requests 14,747; repeat set 2,416. Qualifies: False; adopted automatically: False (adopted by the owner as a reported deviation, amendment 3).

| Pilot decision -> first pass | Requests |
|---|---|
| ADMIT->ADMIT | 14,103 |
| ADMIT->REJECT | 126 |
| REJECT->ADMIT | 172 |
| REJECT->REJECT | 342 |
| non-decision->ADMIT | 1 |
| non-decision->REJECT | 2 |
| non-decision->non-decision | 1 |

Protected rejections (the pilot hand audit's 488 confirmed rejections; pilot decisions {'REJECT': 485, 'non-decision': 3}): the first pass admits 154, the repeat 60.

| Disagreements by judge | Count |
|---|---|
| haiku | 118 |
| llama70 | 62 |
| sonnet | 57 |
| luna | 41 |
| terra | 40 |
| opus | 18 |
| qwen38 | 15 |
| sol | 8 |

| Disagreements by arm | Count |
|---|---|
| debate_k6 | 190 |
| debate_k2 | 97 |
| debate_k1 | 72 |

| Disagreements by world | Count |
|---|---|
| W024 | 32 |
| W093 | 30 |
| W128 | 29 |
| W148 | 23 |
| W073 | 22 |
| W096 | 21 |
| W150 | 21 |
| W115 | 17 |
| W135 | 17 |
| W009 | 16 |
| W051 | 16 |
| W152 | 16 |
| W038 | 14 |
| W023 | 9 |
| W082 | 9 |
| W133 | 6 |

There were 298 distinct requests whose ADMIT/REJECT decision changed. The judge and arm breakdowns each total 359 because a shared request is counted under every judge or arm whose sessions used it. An always-ADMIT gate would agree with the pilot on 96.51% of decisions. The deployment re-rejected 73.99% of its own rejections; its C4 gap (7.45 points) exceeded the 6-point margin by 1.45 points, about half a standard error (consult 56). Neither equivalence nor a large deployment difference was established. Together's certification of this configuration for its serverless target, dated 2026-09-17, predates the pilot but does not establish what deployment served the pilot. From the check's saved replies (amendment 3, section 1): the deployment admits all 7 of the pilot audit's false admissions, as the pilot's gate did, and all 93 other sampled admissions; of the pilot gate's 5 false rejections it admits 4, and of its 24 borderline rejections 14.

| Pass | Sent | Replies | Seconds | Calls per s | Output tokens |
|---|---|---|---|---|---|
| first | 14,747 | 14,747 | 1645.5 | 8.96 | 1,915,320 |
| repeat | 2,416 | 2,416 | 246.7 | 9.79 | 370,885 |

## H. Gate deployment, resets and endpoint cost

Deployment (main_amendment2_endpoint.json): deepseek-ai/DeepSeek-V4-Flash-0731 on Together's certified profile, one replica on 2x NVIDIA B200, NVFP4, TP2, with a request pool of 63 sized to the replica's concurrency and a 30-minute inactivity timeout, model revision rv_CdgV5GFj8H9uwcbVDGTs7, config cr_Cf8Pt4DcSPzufMM5khwUx, $17.98 per hour while ready; endpoint ep_CgSn7nEvKtA14VxHgRcVQ, deployment dep_CgSn7uZSwujLM8oSM8Fn1, all traffic to it. Gate requests: the preregistered prompt, max_tokens 2,000, no sampling parameter (0 of main's 77,993 stored gate requests carry temperature, top_p or top_k; 77,989 ok, 4 truncated).

| Reset | Date (UTC) | Rows | Cost |
|---|---|---|---|
| Amendment 2: refused gate rows (main-reset-gate-failures) | 2026-10-28T01:28:04Z | 25,987 (first refusal 2026-10-27T03:03:29Z) | $0 (refusals carried no cost); archive d8ac8daf... |
| Amendment 1: refused Llama rows (main-reset-access-failures) | not run | 0 (19,260 refused request rows retained: 12,840 verdict refusals and 6,420 query-step refusals; the 12,816 verdict sessions on counted questions score as errors in the literal analysis) | $0 |

Endpoint cost: recorded upper bound $178.98 of the $400 lease (main's 23 bursts $165.00 from each scale-up to its confirmed stop, plus the check's $13.98); ready-to-stop time for main's bursts $43.47; Together's posted charges for the endpoint about $50.35 over its lifetime, the check's burst included (derived from the credit balance against serverless store spend; nothing posted after 03:18Z on 2026-10-29). The gate's store cost ($8.29) is notional.

## I. Gate leakage audit (amendment 3, section 4)

| Cell | Sessions | With admitted | Exposed | Exposed % (upper) | Leak-exposed | Leak % (upper) | Admitted steps | Admitted labels | Rejected steps | Rejected labels |
|---|---|---|---|---|---|---|---|---|---|---|
| haiku:debate_k1 | 40 | 36 | 8 | 20.0 (<= 33.2) | 1 | 2.5 (<= 11.3) | 36 | {ADMISSIBLE: 28, VIOLATES: 8} | 10 | {VIOLATES: 9, ADMISSIBLE: 1} |
| haiku:debate_k2 | 40 | 39 | 17 | 42.5 (<= 56.7) | 1 | 2.5 (<= 11.3) | 76 | {ADMISSIBLE: 56, BORDERLINE: 6, VIOLATES: 14} | 6 | {VIOLATES: 6} |
| haiku:debate_k6 | 40 | 40 | 23 | 57.5 (<= 70.8) | 3 | 7.5 (<= 18.3) | 226 | {ADMISSIBLE: 188, VIOLATES: 24, BORDERLINE: 14} | 14 | {VIOLATES: 14} |
| luna:debate_k1 | 40 | 40 | 5 | 12.5 (<= 24.5) | 1 | 2.5 (<= 11.3) | 40 | {ADMISSIBLE: 35, VIOLATES: 3, BORDERLINE: 2} | 2 | {VIOLATES: 2} |
| luna:debate_k2 | 40 | 40 | 10 | 25.0 (<= 38.7) | 0 | 0.0 (<= 7.2) | 78 | {ADMISSIBLE: 67, VIOLATES: 7, BORDERLINE: 4} | 2 | {VIOLATES: 2} |
| luna:debate_k6 | 40 | 40 | 13 | 32.5 (<= 46.6) | 1 | 2.5 (<= 11.3) | 221 | {ADMISSIBLE: 198, VIOLATES: 13, BORDERLINE: 10} | 3 | {VIOLATES: 3} |
| opus:debate_k1 | 40 | 39 | 3 | 7.5 (<= 18.3) | 0 | 0.0 (<= 7.2) | 39 | {ADMISSIBLE: 36, BORDERLINE: 1, VIOLATES: 2} | 1 | {VIOLATES: 1} |
| opus:debate_k2 | 40 | 40 | 4 | 10.0 (<= 21.4) | 2 | 5.0 (<= 14.9) | 75 | {ADMISSIBLE: 71, BORDERLINE: 2, VIOLATES: 2} | 3 | {VIOLATES: 3} |
| opus:debate_k6 | 40 | 38 | 9 | 22.5 (<= 36.0) | 1 | 2.5 (<= 11.3) | 167 | {ADMISSIBLE: 155, BORDERLINE: 1, VIOLATES: 11} | 2 | {VIOLATES: 2} |
| qwen38:debate_k1 | 40 | 39 | 0 | 0.0 (<= 7.2) | 0 | 0.0 (<= 7.2) | 39 | {ADMISSIBLE: 39} | 0 | {} |
| qwen38:debate_k2 | 40 | 39 | 2 | 5.0 (<= 14.9) | 2 | 5.0 (<= 14.9) | 76 | {ADMISSIBLE: 74, VIOLATES: 1, BORDERLINE: 1} | 0 | {} |
| qwen38:debate_k6 | 40 | 39 | 8 | 20.0 (<= 33.2) | 3 | 7.5 (<= 18.3) | 175 | {ADMISSIBLE: 166, VIOLATES: 6, BORDERLINE: 3} | 1 | {VIOLATES: 1} |
| sol:debate_k1 | 40 | 36 | 3 | 7.5 (<= 18.3) | 1 | 2.5 (<= 11.3) | 36 | {BORDERLINE: 2, ADMISSIBLE: 33, VIOLATES: 1} | 0 | {} |
| sol:debate_k2 | 40 | 40 | 6 | 15.0 (<= 27.5) | 2 | 5.0 (<= 14.9) | 76 | {ADMISSIBLE: 70, BORDERLINE: 4, VIOLATES: 2} | 0 | {} |
| sol:debate_k6 | 40 | 40 | 13 | 32.5 (<= 46.6) | 3 | 7.5 (<= 18.3) | 186 | {ADMISSIBLE: 169, VIOLATES: 9, BORDERLINE: 8} | 0 | {} |
| sonnet:debate_k1 | 40 | 38 | 10 | 25.0 (<= 38.7) | 1 | 2.5 (<= 11.3) | 38 | {BORDERLINE: 4, ADMISSIBLE: 28, VIOLATES: 6} | 4 | {VIOLATES: 4} |
| sonnet:debate_k2 | 40 | 35 | 8 | 20.0 (<= 33.2) | 1 | 2.5 (<= 11.3) | 57 | {BORDERLINE: 2, ADMISSIBLE: 48, VIOLATES: 7} | 1 | {VIOLATES: 1} |
| sonnet:debate_k6 | 40 | 38 | 17 | 42.5 (<= 56.7) | 3 | 7.5 (<= 18.3) | 75 | {ADMISSIBLE: 57, VIOLATES: 15, BORDERLINE: 3} | 3 | {VIOLATES: 3} |
| terra:debate_k1 | 40 | 40 | 3 | 7.5 (<= 18.3) | 1 | 2.5 (<= 11.3) | 40 | {VIOLATES: 3, ADMISSIBLE: 37} | 0 | {} |
| terra:debate_k2 | 40 | 40 | 3 | 7.5 (<= 18.3) | 1 | 2.5 (<= 11.3) | 76 | {ADMISSIBLE: 72, VIOLATES: 2, BORDERLINE: 2} | 0 | {} |
| terra:debate_k6 | 40 | 40 | 10 | 25.0 (<= 38.7) | 0 | 0.0 (<= 7.2) | 186 | {ADMISSIBLE: 176, VIOLATES: 7, BORDERLINE: 3} | 3 | {BORDERLINE: 1, VIOLATES: 2} |

| Arm | Cells | Every cell full | Exposed % (upper) | Leak-exposed % (upper) |
|---|---|---|---|---|
| debate_k1 | 7 | True | 11.4 (15.0) | 1.8 (3.7) |
| debate_k2 | 7 | True | 17.9 (22.1) | 3.2 (5.5) |
| debate_k6 | 7 | True | 33.2 (38.1) | 5.0 (7.7) |

Distinct requests by gate decision and audit label: {admitted: {ADMISSIBLE: 1770, VIOLATES: 141, BORDERLINE: 71}, rejected: {VIOLATES: 52, ADMISSIBLE: 1, BORDERLINE: 1}}.

Labels by source: {main: {VIOLATES: 193, ADMISSIBLE: 1771, BORDERLINE: 72}, pilot:admitted: {ADMISSIBLE: 86, VIOLATES: 10, BORDERLINE: 4}, pilot:classifier: {VIOLATES: 492, BORDERLINE: 17, ADMISSIBLE: 8}}.

Pilot hand label against audit label (calibration; the pilot labels were kept out of every request): {pilot:admitted:AMB: {ADMISSIBLE: 5, BORDERLINE: 1, VIOLATES: 3}, pilot:admitted:FA: {VIOLATES: 6, BORDERLINE: 1}, pilot:admitted:OK: {ADMISSIBLE: 81, VIOLATES: 1, BORDERLINE: 2}, pilot:classifier:AMB: {VIOLATES: 13, BORDERLINE: 9, ADMISSIBLE: 2}, pilot:classifier:FMT:not_admissible: {VIOLATES: 1}, pilot:classifier:FR: {ADMISSIBLE: 4, BORDERLINE: 1}, pilot:classifier:OK: {VIOLATES: 478, BORDERLINE: 7, ADMISSIBLE: 2}}.

Unanswered items: 0. Spend $11.54 of the $200 cap.

Definitions (amendment 3, section 4): a sampled session is exposed if any claim it had admitted is labelled VIOLATES, BORDERLINE or UNREADABLE, and leak-exposed if such a label names condition 2 or 3 (a candidate named, or a candidate's conclusion asserted), names no condition, or is UNREADABLE. Upper bounds are nominal one-sided 95% Clopper-Pearson (pooled when every cell is full), not simultaneous, can undercover slightly when judges' rates differ, and do not account for the labeler's misses.

## J. Gate sensitivity (amendment 3, section 5)

| Common exposure | Component shifts (pp) | b1 peak_supported | b1 meaningful_peak_shown | b1 meaningful_peak_ruled_out | b2 peak_supported | b2 meaningful_peak_shown | b2 meaningful_peak_ruled_out | world - k6 rejected |
|---|---|---|---|---|---|---|---|---|
| 0.0% | e1 - e0 0.00 e1 - e6 0.00 e2 - e0 0.00 e2 - e6 0.00 | True | False | True | False | False | True | True |
| 0.5% | e1 - e0 0.50 e1 - e6 1.00 e2 - e0 0.50 e2 - e6 1.00 | False | False | False | False | False | True | True |
| 1.0% | e1 - e0 1.00 e1 - e6 2.00 e2 - e0 1.00 e2 - e6 2.00 | False | False | False | False | False | False | True |
| 2.0% | e1 - e0 2.00 e1 - e6 4.00 e2 - e0 2.00 e2 - e6 4.00 | False | False | False | False | False | False | False |
| 5.0% | e1 - e0 5.00 e1 - e6 10.00 e2 - e0 5.00 e2 - e6 10.00 | False | False | False | False | False | False | False |

| Claim | Status | Tipping exposure |
|---|---|---|
| b1.peak_supported | tips | 0.246% |
| b1.meaningful_peak_shown | not held |  |
| b1.meaningful_peak_ruled_out | tips | 0.309% |
| b2.peak_supported | not held |  |
| b2.meaningful_peak_shown | not held |  |
| b2.meaningful_peak_ruled_out | tips | 0.932% |
| secondary.pooled_k6_minus_k0 | not held |  |
| secondary.pooled_debate_world_minus_k6 | tips | 1.401% |
| secondary.llama70_k1_minus_k0 | not held |  |
| secondary.llama70_k6_minus_k0 | not held |  |
| secondary.qwen38_k1_minus_k0 | not held |  |
| secondary.qwen38_k6_minus_k0 | not held |  |

| Audit reading | Exposure % (b1/b2/b6) | Component shifts (pp) | b1 peak_supported | b1 meaningful_peak_shown | b1 meaningful_peak_ruled_out | b2 peak_supported | b2 meaningful_peak_shown | b2 meaningful_peak_ruled_out | world - k6 rejected |
|---|---|---|---|---|---|---|---|---|---|
| exposed:estimate | 11.4 / 17.9 / 33.2 | e1 - e0 11.43 e1 - e6 44.64 e2 - e0 17.86 e2 - e6 51.07 | False | False | False | False | False | False | False |
| exposed:upper | 15.0 / 22.1 / 38.1 | e1 - e0 15.05 e1 - e6 53.19 e2 - e0 22.05 e2 - e6 60.19 | False | False | False | False | False | False | False |
| leak_exposed:estimate | 1.8 / 3.2 / 5.0 | e1 - e0 1.79 e1 - e6 6.79 e2 - e0 3.21 e2 - e6 8.21 | False | False | False | False | False | False | False |
| leak_exposed:upper | 3.7 / 5.5 / 7.7 | e1 - e0 3.72 e1 - e6 11.42 e2 - e0 5.54 e2 - e6 13.25 | False | False | False | False | False | False | False |

| Reading | pooled e6 - e0 shift (pp) | pooled world - e6 shift (pp) | Qwen3.8 e1 - e0 shift (pp) | Qwen3.8 e6 - e0 shift (pp) | Secondary rejections that hold |
|---|---|---|---|---|---|
| common 0.0% | 0.000 | 0.000 | 0.000 | 0.000 | pooled_debate_world_minus_k6 |
| common 0.5% | 0.500 | 0.500 | 0.500 | 0.500 | pooled_debate_world_minus_k6 |
| common 1.0% | 1.000 | 1.000 | 1.000 | 1.000 | pooled_debate_world_minus_k6 |
| common 2.0% | 2.000 | 2.000 | 2.000 | 2.000 | none |
| common 5.0% | 5.000 | 5.000 | 5.000 | 5.000 | none |
| exposed:estimate | 33.214 | 33.214 | 0.000 | 20.000 | none |
| exposed:upper | 38.141 | 38.141 | 7.216 | 33.203 | none |
| leak_exposed:estimate | 5.000 | 5.000 | 0.000 | 7.500 | none |
| leak_exposed:upper | 7.706 | 7.706 | 7.216 | 18.259 | none |

Every secondary claim and displacement bound at each reading: a secondary contrast is moved toward zero, never past it; Qwen3.8's contrasts use Qwen3.8's own exposure; Llama's two contrasts are untested and carry no bound (shift 0). Only the debate-with-world versus budget-six rejection holds at zero exposure, and it holds at 0.5% and 1% but at no other reading. With no missing cells, the coded (against-peak and for-peak) primary components' displacement bounds equal the primary ones above.

