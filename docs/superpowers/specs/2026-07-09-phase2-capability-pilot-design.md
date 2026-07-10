# Phase 2: Reduced-Scope Capability Pilot (Design)

**Date:** 2026-07-09 · **Status:** SUPERSEDED by `2026-07-10-phase2-capability-pilot-design-v2.md` (lead feedback: task hardening, debate regeneration, batch-same-Q&A condition, H as primary) · **Vetted:** Codex consults #04, #09
**Licensed by:** `docs/rejudge-protocol.md` Amendment 1 (Stage-1 outcome: real smaller effect, +3.4pp CI [+1.3, +5.9])

## Question

Stage 1 established that a small verification budget degrades a 70B judge on a clean harness, and
that part of the harm comes from mere deliberation turns. Phase 2 asks: **does the
verification-content harm decline as judge capability grows, controlling for the judge-debater
capability gap?** Secondary: does the turn-count harm decline with capability?

## Estimands (per judge x debater cell)

```
total_harm    H = p(clean_b2)   - p(clean_b0)
turn_harm     T = p(placebo_b2) - p(clean_b0)
content_harm  V = p(clean_b2)   - p(placebo_b2)     <- primary
```

Primary analysis: slope of V vs judge capability (z-scored task-specific solo-QA accuracy),
controlling for gap (debater_cap - judge_cap), question-cluster bootstrap. Power note (Codex #09):
this design detects large trends (roughly 1.5-2pp change per capability-z at 80% power), i.e.
"content harm disappears across the ladder", not 1pp subtleties. A tight null is informative.

## Design

**Grid: 3 judges x 2 debaters, fully crossed (6 cells).**

| Role | Models | Notes |
|---|---|---|
| Judges | low-capability long-context model (candidate: Qwen2.5-7B-Instruct-Turbo, 32k context), Llama-3.3-70B-Instruct-Turbo (Stage-1 anchor), Llama-3.1-405B (top anchor) | 8B Llama excluded: severe pilot side bias and 8k context. Low judge must pass the smoke gate or be swapped (fallback Qwen2.5-14B) |
| Debaters | Llama-3.3-70B (reuse the 318 pilot transcripts), Llama-3.1-405B (generate 318 new debates: 3 transcripts x 106 questions, same protocol) | honest = dishonest model always; same transcripts/question count at both levels (asymmetry would bias debater-level comparisons) |
| Oracle | FIXED at Llama-3.3-70B clean oracle for every cell | isolates judge capability from oracle quality |

**Conditions per cell:** clean_b0, clean_b2, placebo_b2. K=2. Budgets beyond {0,2} dropped
(Stage 1: effect lives at b2, no recovery at b5). Placebo runs in EVERY cell (V is the primary
estimand; partial placebo coverage breaks the model).

**Capability axis:** each model's solo-QA accuracy on the 106 questions with the full world
document, frozen before any outcome analysis. Benchmark composite reported as secondary.

**Optional extension (pre-authorized only if smoke gates pass and budget holds):** a 4th judge
(candidate Qwen2.5-72B) crossed with BOTH debater levels, making 4x2.

## Frozen gates (to be embedded in the phase-2 protocol before launch)

Pre-launch: model IDs/context/pricing verified live; smoke test per judge (INVALID < 2%,
malformed claims < 2%, no truncation); side-bias diagnostic per judge (drop or swap a judge with
> ~10pp side asymmetry at b0); capability scores frozen before outcomes.
Outcome: no phase-3 expansion unless V is nontrivial and not placebo-explained; if judge
capability and gap remain too collinear in the realized design, NO gap claim is made.
Mechanism labeling (human-in-loop) only on clean harmful flips, only if V is nontrivial.

## Cost (bottom-up, to verify at launch)

| Item | Est. |
|---|---|
| Judgments: 6 cells x 3 conditions x 636 (318 transcripts x K=2) = 11,448; ~13.3k tokens each; judge tokens split across $0.30/$1.04/$3.50 per M tiers, oracle fixed at $1.04 | ~$240 |
| 405B debate generation (318 debates, ~25k tokens each at $3.50/M) | ~$30 |
| Solo-QA capability runs (5-6 models x 106 questions) | ~$10 |
| Smoke tests + retries margin (~25%) | ~$70 |
| **Total (3x2)** | **~$350, cap $500** |
| Optional 4th judge (+2 cells) | +$80, cap $600 total |

This is far below the earlier $1.5-2.5k guess because conditions and budgets were cut; ~$9.4k of
the grant would remain after phase 2 for new worlds + a confirmatory block.

## Build work required ($0, before any spend)

1. Verify MODEL_REGISTRY ids against Together's live catalog (6/9 currently unverified) and pin
   exact strings, context windows, prices.
2. Extend `rejudge/` runner: parametrize judge model, transcript set, and pinned oracle;
   add a debate-generation module (port of the pilot `debate.py` flow against the fixed harness
   conventions: raw logging, provenance, caps); solo-QA capability runner.
3. Phase-2 protocol generator (arms, gates, capability axis, spend record), frozen pre-run.
4. Smoke-test script per judge (formatting, INVALID rate, side-bias diagnostic at b0).

## Out of scope

New worlds (post-phase-2, per Codex #07 allocation); frontier closed models; mechanism-at-scale
automation; anything touching the pilot files.
