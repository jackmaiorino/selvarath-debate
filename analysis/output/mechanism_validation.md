> **⚠️ Correction (2026-07-06):** this validation was performed on the CORRUPTED pilot oracle channel
> (NOT-ADDRESSED→NO miscoding, doubled queries — see the report Correction). It validates label
> *reliability* (two-pass κ=0.72), not the mechanism *conclusion*. The O1/Q1/R1/R2 shares are a
> postmortem of the buggy harness and reset to unknown pending the fixed-harness re-judge.

# Mechanism validation (Deliverable D) — supersedes the report's preliminary FM1/FM2 section

Two **independent, blind** labeling passes over the 54 forward flip cases (70B judge, correct@0 → wrong@{1,2}), plus classification of the 8 reverse (beneficial) flips. Both passes used Claude Sonnet with per-world ground-truth docs; pass 2 additionally required verbatim world-doc + judge-reasoning quotes and a refined taxonomy. (An opus/human cross-pass remains a cheap follow-up — the opus attempt hit a session limit.)

## Inter-rater agreement (forward, n=54)

| | FM1 | FM2 | other |
|---|---|---|---|
| **Pass 1** | 16 | 19 | 19 |
| **Pass 2** | 13 | 17 | 24 |

- **Raw agreement 44/54 = 81.5%; Cohen's κ = 0.72 (substantial).**
- Confusion (rows = pass 1, cols = pass 2): the only material slippage is **5 cases pass 1 labeled FM1 that pass 2 labeled `other` (Q1)** — the "oracle-answer error vs malformed judge query" boundary. FM2 is stable (16 of 19 agree).
- **Consensus (both passes agree):** FM1 = 11, FM2 = 16, other = 17.

## Refined taxonomy (pass 2 fine-grained, forward n=54)

| code | meaning | count | fixable by… |
|---|---|---|---|
| **O1** | oracle gave a wrong answer vs the text (= FM1) | 13 (~24%) | a **better oracle** |
| **Q1** | judge's *query* was malformed/compound/underspecified; oracle answer defensible | 14 (~26%) | **constrained/decomposed query format** (partially) |
| **R1** | oracle correctly confirmed a true-but-irrelevant claim; judge over-updated (= FM2) | 17 (~31%) | **not** oracle-fixable (judge myopia) |
| **R2** | oracle correctly surfaced a real honest-side gap; judge over-penalized the correct side | 8 (~15%) | **not** oracle-fixable (judge myopia) |
| **M1** | query answer correct but query→verdict relevance ambiguous | 2 (~4%) | — |

**Protocol-fixable (O1 + Q1) ≈ 50%; deep judge myopia (R1 + R2) ≈ 46%.**

## Cluster-weighting (per question, not per flip)

- FM1 flips span only **8 (pass 2) – 10 (pass 1) distinct questions**; **CN-003 alone contributes 4 FM1 flips** from one recurring oracle-error template (a wrong "NO" to the true "removal threshold fixed at 24 votes" claim). So per-flip FM1 **overstates independent** oracle-error prevalence — report both impact-weighted (per flip) and cluster-weighted (per question).

## Net-aware picture (reverse flips)

- 8 reverse flips (wrong@0 → correct@{1,2}), **all in vethun_sarak**: **7 = oracle correctly helped** (correctly refuted a dishonest claim / confirmed a true honest one), **1 = stochastic**. **Zero** oracle-error-driven benefits → oracle errors cause harm but not benefit; no offset. Net harm = +22 (budget 1: 27−5), +24 (budget 2: 27−3).

## Validated conclusion

The limited-verification harm for the strong (70B) judge is **real** (Δfew 7.2 pp, CI [4.6, 10.2]) and is **NOT mostly a simple oracle bug**:
- **~1/4 (O1, ~20–24%, consensus ~20%, only ~8–10 distinct questions)** are genuine oracle-answer errors → fixable by a better oracle.
- **~1/4 (Q1, ~26%)** are the judge asking bad/compound queries → partially fixable by constraining/decomposing the query format.
- **~1/2 (R1 + R2, ~46%)** are the judge **over-updating on correct verification** (crediting irrelevant confirmations, over-penalizing real honest-side gaps) → a reasoning failure a better oracle will **not** fix.
- κ = 0.72; the exact **O1↔Q1 split is a genuine gray zone** (10 boundary cases) — worth a human spot-check before finalizing the "fixable oracle" number.

## Updated recommendation

- **C — paired ablation** should now test **two protocol fixes, not one:** (a) a stronger/citation-calibrated **oracle** (targets O1) and (b) **constrained/decomposed judge queries** (targets Q1), plus the **placebo/sham-oracle** arm. **Gate:** if the protocol fixes drop Δfew to ≤2 pp → mostly protocol-fixable; if the ~46% R1/R2 myopia keeps Δfew ≥4 pp → the residue is judge-side → **B (frontier)** to test whether a stronger *judge* avoids the over-updating pathology.
- **Before spend:** human spot-check the ~10 O1/Q1 boundary cases (`labels.csv` vs `labels_pass2.csv`; audit trail in `mechanism_labels.md`).
