# Limited-Verification Re-analysis

## Win rates (70B)

| judge   |   budget |   n |   wrong_pct |   ci_lo |    ci_hi |
|:--------|---------:|----:|------------:|--------:|---------:|
| 70B     |        0 | 318 |     1.88679 | 0.86752 |  4.05465 |
| 70B     |        1 | 318 |     8.80503 | 6.16205 | 12.4314  |
| 70B     |        2 | 318 |     9.43396 | 6.68819 | 13.1482  |
| 70B     |        5 | 318 |     5.34591 | 3.36416 |  8.39368 |
| 70B     |       20 |  39 |     0       | 0       |  8.96699 |

## Side split (70B)

| judge   |   budget |   wrong_pct_Acorrect |   n_A |   wrong_pct_Bcorrect |   n_B |
|:--------|---------:|---------------------:|------:|---------------------:|------:|
| 70B     |        0 |              1.64835 |   182 |              2.20588 |   136 |
| 70B     |        1 |              9.7561  |   164 |              7.79221 |   154 |
| 70B     |        2 |              9.93377 |   151 |              8.98204 |   167 |
| 70B     |        5 |              6.45161 |   155 |              4.29448 |   163 |
| 70B     |       20 |              0       |    16 |              0       |    23 |

## Confidence x correctness (70B)

| judge   |   budget |   mean_conf |   mean_conf_correct |   mean_conf_wrong |
|:--------|---------:|------------:|--------------------:|------------------:|
| 70B     |        0 |     4.09119 |             4.09295 |           4       |
| 70B     |        1 |     4.49371 |             4.53448 |           4.07143 |
| 70B     |        2 |     4.58176 |             4.63542 |           4.06667 |
| 70B     |        5 |     4.69811 |             4.72093 |           4.29412 |
| 70B     |       20 |     4.74359 |             4.74359 |         nan       |

## Primary inference (70B)

Pre-registered contrasts (pp), question-cluster bootstrap 95% CI:

| stat     | stratum   |   point_pp |   ci_lo_pp |   ci_hi_pp |
|:---------|:----------|-----------:|-----------:|-----------:|
| few      | overall   |    7.2327  |   4.55975  |   10.2201  |
| few      | A         |    8.19658 |   4.69538  |   12.0309  |
| few      | B         |    6.18124 |   2.23821  |   10.3937  |
| recover5 | overall   |    3.77358 |   0.943396 |    6.76101 |
| recover5 | A         |    3.39332 |  -0.949556 |    7.93944 |
| recover5 | B         |    4.09264 |  -0.244093 |    8.5793  |

- Δfew overall = 7.23 pp [4.56, 10.22]

## Parse-sensitivity (Δfew under treatments, pp)

- baseline: 7.23
- exclude: 7.23
- suspect_wrong: 7.23
- suspect_correct: 7.23
- suspect_5050: 7.23

> **Bounded check.** The fallback proxy flags 0 suspect 70B rows, so for the 70B judge these treatments are near-vacuous: the PASS means there are essentially no format-noncompliant 70B verdicts to perturb, NOT that a large perturbation was absorbed. Raw verdict text was never logged, so a definitive parse audit requires the instrumented re-judge (deliverable C).

## Robustness

### Leave-one-world-out (Δfew pp)

| dropped      |   delta_few_pp |
|:-------------|---------------:|
| none         |        7.2327  |
| carath_norn  |        8.21596 |
| selvarath    |        6.19048 |
| vethun_sarak |        7.277   |

### Discordance (0 -> {1,2})

|   flip_budget |   correct_to_wrong |   wrong_to_correct |   net_new_errors |   n_transcripts |
|--------------:|-------------------:|-------------------:|-----------------:|----------------:|
|             1 |                 27 |                  5 |               22 |             318 |
|             2 |                 27 |                  3 |               24 |             318 |

## Secondary: 8B (side-bias caveat)

| judge   |   budget |   wrong_pct_Acorrect |   n_A |   wrong_pct_Bcorrect |   n_B |
|:--------|---------:|---------------------:|------:|---------------------:|------:|
| 8B      |        0 |              27.439  |   164 |              9.09091 |   154 |
| 8B      |        1 |              25.974  |   154 |             17.0732  |   164 |
| 8B      |        2 |              38.4106 |   151 |             14.9701  |   167 |
| 8B      |        5 |              37.5758 |   165 |              7.18954 |   153 |

## Mechanism (FM1/FM2)

| label   |   count |     frac |
|:--------|--------:|---------:|
| FM1     |      16 | 0.296296 |
| FM2     |      19 | 0.351852 |
| other   |      19 | 0.351852 |

## Gate evaluation

- Δfew CI excludes 0 with lower bound ≳ +2pp: **4.56 pp** → PASS
- Positive in both strata (A=8.20, B=6.18): PASS
- Survives parse-sensitivity (min treatment 7.23 pp, bounded — 0 suspect 70B rows, see caveat): PASS
- **Overall harm claim: BANKED**
- See the Recommendation section below (audit trail: `mechanism_labels.md`, `labels.csv`).


## Recommendation (go/no-go)

- **Statistical finding BANKED:** for the strong (70B) judge a small oracle budget significantly worsens oversight (Δfew ≈ 7.2 pp, cluster-bootstrap 95% CI [4.6, 10.2], positive in both correct-side strata). Δrecover5 ≈ 3.8 pp (partial recovery by budget 5); budget-20 'recovery' is NOT claimable (n=39). The 8B judge is contaminated by a Position-B side bias and is secondary only.
- **Mechanism (PRELIMINARY — single LLM labeler):** of 54 gross correct→wrong flips, FM1 (fixable oracle-answer errors) = 16 (30%), FM2 (irrelevant true confirmation) = 19 (35%), other = 19 (35%). Per the spec's decision rule this is the **'substantial FM2 present'** case, NOT the conservative 'mostly FM1 / fixable oracle' case: only ~a third of gross harmful flips are direct oracle-answer errors.
- **Do NOT over-claim** 'a better oracle won't fix the majority.' The split is under-validated: single LLM labeler; counts are GROSS flips (net harm also involves the 8 unclassified wrong→correct reverse flips); and one oracle-error template (CN-003 fixed-threshold) recurs across 4 transcripts, so per-flip FM1 overstates independent prevalence. The point estimate carries a wide (~19–43%) interval.
- **Recommended next steps, in order:**
  1. **D — mechanism-label validation ($0):** second independent blind labeler on all 54 flips + classify the 8 reverse flips (net-aware), with required world-doc evidence quotes; cluster FM1/FM2 per question. Firms up the load-bearing 'only ~1/3 is fixable oracle' number before any spend.
  2. **C — paired oracle ablation (~$5–15, needs approval + estimate):** same 70B judge and transcripts, budgets 1/2; original oracle vs a stronger/citation-calibrated oracle, rerunning the original oracle concurrently for rerun-noise, A/B labels held fixed across budgets; optionally a placebo/sham-oracle arm (isolate whether harm is from oracle ANSWERS or the verification prompt itself). **Gate:** better oracle drops Δfew ≤2 pp / CI includes 0 → mostly oracle-protocol artifact; Δfew stays ≥4 pp → judge-side effect, frontier justified.
  3. **B — frontier rerun (needs approval + cost-out):** does a stronger JUDGE avoid the FM2/over-updating pathology (the project's capability-gap question)?
- Audit trail: `mechanism_labels.md` (per-case oracle_check + justification) and `labels.csv`.
