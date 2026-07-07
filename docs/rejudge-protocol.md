# Re-Judge Protocol (frozen pre-run)

**Frozen at commit:** `d727d40` *(state of the harness config when this document was
generated; the document itself lands in the following commit)* · **parser_version:**
`2.0.0` · **Replicates:** K=2 (legacy K=1)
**Judge = Oracle model:** `meta-llama/Llama-3.3-70B-Instruct-Turbo` · **Transcripts:** the 318 pilot transcripts (`data/transcripts.jsonl`), unchanged.

## Arms

| arm | oracle normalizer | composer | DONE | placebo | budgets | A/B assignment |
|---|---|---|---|---|---|---|
| clean | strict | clean | robust | no | [0, 1, 2, 5] | fixed |
| both | pilot | pilot | pilot | no | [1, 2, 5] | fixed |
| placebo | strict | clean | robust | yes | [1, 2, 5] | fixed |
| na_only | pilot | clean | robust | no | [1, 2] | fixed |
| doubled_only | strict | pilot | robust | no | [1, 2] | fixed |
| legacy | pilot | pilot | pilot | no | [1, 2] | per-budget (pilot replay) |

Placebo feedback text (verbatim):

> ORACLE PLACEBO: no factual verification was performed for this query. This message contains no evidence about the world document.

## Pre-registered gates (ex-ante: frozen before any clean data exists)

- **Primary (CLEAN):** Δfew = ½[p(1)+p(2)] − p(0) on strict-parsed verdicts (INVALID excluded and
  reported), question-cluster bootstrap (B=10,000, seeded). Δfew ≥ 4 pp with 95% CI excluding 0 →
  **limited-verification harm survives**. Δfew ≤ 2 pp with CI including 0 → **mostly harness
  artifact**. 2–4 pp → indeterminate (escalate replicates to K=3 before re-judging the gate).
- **Attribution:** BOTH−CLEAN ≥ 3.5 pp, or bugs explain > 50% of the original +7.2 pp → the pilot
  headline was mostly harness-induced.
- **Deliberation:** |PLACEBO − CLEAN| ≤ 2 pp (and PLACEBO−p(0) ≥ 4 pp) → the harm is
  deliberation/turn-count, not verification content.
- **Secondary (reported, not gated):** Δrecover5, dual-parse disagreement rate, INVALID rate,
  well_formed_claim rate, single-bug decomposition (NA_ONLY, DOUBLED_ONLY), legacy-vs-pilot
  agreement (QA only).

## Spend control

Live runs require `--approved-cap` (hard abort on projected overrun). Estimated Stage 1
(all five core arms, K=2, plus legacy subset): ~$185–230 at $1.04/M; approved cap to be
recorded here at launch alongside the account price.
