# Mechanism-proxy calibration — result: NOT yet good enough (κ = 0.39)

Goal: test whether an **automatable** mechanism classifier (`analysis/mechanism_proxy.py` — a 4-signal rubric filled by an LLM + a deterministic `derive_label` mapping) can reproduce the hand-labeled FM1/FM2/other split, so mechanism analysis can scale to the capability grid instead of hand-labeling every cell.

Method: a Sonnet classifier filled the 4 signals (oracle_validity, query_quality, claim_relevance, judge_relied) for all 54 forward flips (grounded in the world docs); `derive_label` mapped them to the taxonomy. Compared to the two hand-passes and their 44-case consensus.

## Result

- Proxy 3-way: **FM1 = 11, FM2 = 3, other = 40** (6-way: O1 11 / Q1 10 / R1 3 / R2 13 / M1 17). Hand: pass1 16/19/19, pass2 13/17/24.
- Agreement: proxy vs pass1 **54% (κ 0.30)**; vs pass2 **61% (κ 0.36)**; **vs consensus 27/44 = 61% (κ 0.39)** — only *fair*.
- Confusion (rows = consensus, cols = proxy):

|  | FM1 | FM2 | other |
|---|---|---|---|
| **FM1** | 7 | 0 | 4 |
| **FM2** | 0 | **3** | **13** |
| **other** | 0 | 0 | 17 |

## Diagnosis

- The proxy reproduces **"other" perfectly (17/17)** and **FM1 moderately (7/11)**, but **badly under-detects FM2** (3 vs ~16–19): it routes 13 consensus-FM2 cases to "other."
- Root cause = the **`claim_relevance` signal**. FM2 (R1) requires the classifier to judge a *true, confirmed* claim as **strategically irrelevant**; the single-pass rubric instead rated most such claims "partial"/"decisive" (→ R2 → other), and over-used oracle_validity "ambiguous" (→ M1 → 17 cases). This is exactly the "strategic relevance is subjective" risk flagged in the Codex design consult.
- The **deterministic mapping is not the problem** (it's unit-tested and behaves as specified); the weak link is the classifier's relevance/validity judgment.

## Verdict & impact on the capability-experiment plan

**The proxy is NOT yet fit to replace hand-labels at scale** — especially for the load-bearing FM2 ("deep judge myopia") count, which it collapses. Options, in order of effort:
1. **Few-shot the classifier** with 2–3 exemplars per category drawn from the 54 hand-labels, and sharpen the "irrelevant vs partial" criterion; re-calibrate. (Cheapest; risk: overfitting to these 3 worlds.)
2. **Ensemble / multi-pass** the classifier and take majority per signal.
3. **Human-in-the-loop on a stratified sample** of grid cells (don't automate all) — the safe default until a proxy clears ~κ ≥ 0.6 against consensus.

Recommendation for the capability grid: proceed with **option 3** (sample + human/multi-pass audit) for mechanism attribution, and treat the automatable proxy as a *screen* (good for O1/other, unreliable for FM2) rather than a labeler, unless option 1/2 raises agreement first.

## Recalibration v2 — sharpened rubric (option 1, done)

Sharpened `PROXY_INSTRUCTIONS` to counter the two v1 biases (over-use of "ambiguous"/"partial"): score "incorrect" when a claim is true-in-substance by entailment, and "irrelevant" (not "partial") when a true confirmation is over-read to favor the wrong side. Re-ran the Sonnet classifier on the same 54 cases.

- proxy_v2 3-way: **FM1 = 20, FM2 = 23, other = 11**.
- Agreement: vs pass1 **72% (κ 0.58)**; vs pass2 61% (κ 0.43); **vs consensus 32/44 = 73% (κ 0.59)** — up from κ 0.39.
- Confusion (rows = consensus, cols = proxy_v2): FM1 10/0/1, FM2 0/**14**/2, other **3/6**/8. The v1 FM2 blind spot is fixed (14/16 recall); the new residual bias is the **opposite** — it over-attributes FM1/FM2 to ~9 true-"other" cases, so it **inflates** the failure-mode rates.

**Caveat (important):** the rubric was tuned on these same 54 cases, so **κ 0.59 is in-sample/optimistic**; real out-of-sample agreement is likely lower and must be re-validated on held-out grid cells.

**Updated verdict:** the calibrated proxy is a **usable screen** (good FM1/FM2 recall) but **not a stand-alone labeler** — it over-attributes to FM1/FM2. For the capability grid: run the proxy as a first-pass screen, then **human-audit the cases it flags FM1/FM2** (to remove the over-attribution) and re-validate κ out-of-sample. The deterministic mapping remains fixed and unit-tested.
