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

## Gate evaluation

- Δfew CI excludes 0 with lower bound ≳ +2pp: **4.56 pp** → PASS
- Positive in both strata (A=8.20, B=6.18): PASS
- Survives parse-sensitivity (min treatment 7.23 pp): PASS
- **Overall harm claim: BANKED**
- Next step gated on FM1/FM2 split (see mechanism_cases.md).
