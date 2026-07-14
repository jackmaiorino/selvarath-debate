# Calibration Results and the Amended Main-Experiment Design

**Date:** 2026-07-14 · **Cost:** $10.51 (generation $4.75, judging $5.76) · **Data:** 192 blind
debates (24 held-out questions), 1,668 judgments, mirrored side assignments, all endpoints
canary-verified.

## Failure rates with no oracle (percent wrong, n=96 per cell)

| Judge | 70B debates, capped | 70B, uncapped | strong debates, capped | strong, uncapped |
|---|---|---|---|---|
| Qwen2.5-7B | 9.4 | 14.6 | 15.6 | 20.8 |
| Llama-3.3-70B | 2.1 | 1.0 | 3.1 | 12.5 |
| Qwen3.7-Plus | 5.2 | 5.2 | 9.4 | 12.5 |

Quality: side bias at most 6.2 points for every surviving judge (limit 10); zero unparseable
verdicts except the disqualified model; zero rule-breaking oracle queries; word caps held in all
546 capped turns. Qwen3.5-9B was disqualified (empty responses in 396/396 calls); the
pre-declared fallback Qwen2.5-7B takes the small-judge slot.

## What the calibration decided

1. **The word limit did not harden the task** (selection cell: 2.1% capped vs 1.0% uncapped,
   both far under the 8-15 target). The pre-declared selection rule therefore failed, and the
   protocol choice is an ex-ante amendment made before any oracle-treatment data: **blind
   uncapped 3-round debate with strong debaters** is the hardened task (12.5% at the anchor
   judge). Selection used only no-oracle cells; the 24 calibration questions are excluded from
   the main analysis.
2. **Strong debaters, not bandwidth, create difficulty.** Checked for a length confound: the
   strong debater's uncapped debates are shorter than the 70B's (median 2,144 vs 2,439 words).
3. **Word caps protect the judge against strong debaters**: the cap-by-debater interaction at
   the anchor judge is +10.4 points, range [+3.1, +18.8]. Kept as a targeted secondary test,
   not a grid factor.
4. **The strongest model is not the best judge** (suggestive): Qwen3.7-Plus judges worse than
   Llama-70B pooled across cells (+3.4 points, range [+0.0, +7.8]). Judge capability will be
   measured directly (solo QA with the document), never assumed from model size; judge identity
   stays categorical in the primary analysis.

## Amended main experiment (pending sign-off and Together top-up)

Judges: Qwen2.5-7B, Llama-3.3-70B, Qwen3.7-Plus (note: Plus is a hosted endpoint, likely not
open-weight; it is recorded as a hosted top anchor unless the leads prefer canarying open
alternatives such as GPT-OSS-120B or Qwen3-235B). Debaters: Llama-70B and Qwen3.7-Plus, honest
and dishonest always the same model. Protocol: blind uncapped 3-round, counterbalanced opening.
Per judge x debater cell: no-oracle, sequential clean b2, batch-same-Q&A b2, placebo b2; 3
transcripts per question on the 82 non-calibration questions, K=2. No-debate references per
judge. Secondary: the cap-protection test at the 70B judge. Analysis: categorical judge and
debater contrasts for sequential harm and packaging; measured-capability trends exploratory.

**Cost: expect $400-1,000, ceiling $1,500** (thinking-token variance is the wildcard).
Spend to date across the whole project: $208.

## Addendum (same day): open-weight candidates rescue the roster

A lead question about Gemma exposed that three "failed" models were reasoning models starved by
small output-token limits; after a client fix (token floor for reasoning endpoints), retests on
the same debates show:

| Candidate | Unparseable verdicts | Side bias | Wrong at b0 | Verdict |
|---|---|---|---|---|
| Gemma-4-31B | 0% | max 2.4 pts | 3.3% (70B debates) to 9.4% (strong) | passes |
| GPT-OSS-120B | 1% | max 6.2 pts | 8.4% to 15.6% | passes |
| Qwen3.5-9B | 50-71% even after the fix | n/a | n/a | disqualified (pre-declared INVALID < 2% rule) |

GPT-OSS-120B (120B, open-weight) judges far worse than Llama-70B on identical debates (14.7% vs
1.0% uncapped), the second reasoning model to underperform a smaller plain judge. Judging skill
does not track size; the grid treats judges categorically and measures capability directly.

**Roster options for sign-off:**
- A (minimal, per consult #14): Qwen2.5-7B, Llama-70B, GPT-OSS-120B. All open-weight. ~$400-1,000.
- B (recommended): add Gemma-4-31B as a fourth judge: four families, all open-weight, baselines
  spanning 1% to 21%, no hosted-model exception needed. Roughly +$150-250 over A.
- Qwen3.7-Plus optionally rides along as a hosted exploratory judge in either option.

## Addendum 2: the strong-debater slot (final)

Sol (consult #15) proposed GPT-OSS-120B as strong debater to make the whole roster open-weight,
gated on a debater-role canary. Canary results (48 blind debates + anchor judging, ~$6):

| Strong debater option | Anchor judge error | Debate length | Protocol compliance |
|---|---|---|---|
| Hosted Qwen3.7-Plus, uncapped | 12.5% (in band) | shorter than 70B's | clean |
| GPT-OSS-120B, uncapped | 22.9% (over band) | 2x everyone else | clean |
| GPT-OSS-120B, 300-word cap | 4.2% (under band) | capped | **85 cap violations in 288 turns** |
| Llama-70B, 300-word cap (reference) | 3.1% | capped | 0 violations |

OSS fails the pre-declared gates: its uncapped danger is mostly verbosity (collapses 22.9 to 4.2
under caps), it cannot reliably obey word limits, and its document knowledge tests slightly below
the 70B it would debate (94% vs 100% solo QA). Per the pre-committed fallback, the strong debater
remains **hosted Qwen3.7-Plus**, documented as the design's one non-open-weight exception (its
debates are in-band, shorter than the 70B's, and fully protocol-compliant). The verbosity-driven
danger result itself joins the exploratory findings alongside the caps-protect-judges interaction.

**Final experiment roster:** judges Qwen2.5-7B, Gemma-4-31B, Llama-3.3-70B, GPT-OSS-120B (all
open-weight); debaters Llama-3.3-70B and Qwen3.7-Plus (hosted exception); oracle Llama-3.3-70B.
Pre-registered analysis per consult #15 (three Holm-adjusted primary tests). Cost $550-1,200,
ceiling $1,500.

## Reproducibility

`rejudge/debate_gen.py` (blind generator), `rejudge/calibrate.py` and `calibrate_analyze.py`
(mirrored judging + criteria printout), `rejudge/output/calibration_*.jsonl`, frozen roster in
`rejudge/output/calibration_models.json`. Consults #13-#14.
