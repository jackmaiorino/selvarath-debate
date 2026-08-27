# Codex consult: stage-2 screening results and stage-3 plan (2026-08-27)

One-shot, gpt-5.6-sol high, codex session 01a0445f-ba05-7890-aa75-617646ce4c11, ephemeral read-only sandbox.

## Brief

```
One-shot consult, selvarath phase-3 successor canary (same project as this morning's empty-verdict and screen-plan consults). Stage-2 judgment screening is complete. Review the results and the proposed next step; answer with numbered blockers or 'no blockers', plus a one-line ruling on each of the three DECISIONS. Brief and self-contained; do not re-litigate strategy you already approved (screen-select-rebuild, independent pilot bank, 0-of-96 distribution-matched confirmation, admission unit = model x serving mode x role x thinking mode x max_tokens).

STAGE-2 RESULTS (96 distribution-matched probes per stage-1 passer, from the independent 318-transcript pilot bank, stage-1 probes excluded; 24-probe upper-tail triage for the two weak candidates):
- qwen38-verdict-8192-confirm (Qwen3.8-2.4T thinking): 96/96 parseable verdicts, ALL finish_reason=stop, 0 invalid. REJECTED on the 80% headroom rule: 1 completion of 7,653 tokens > 6,553 (80% of 8,192). Distribution p50=1,278 p90=2,551 max=7,653.
- gemma4-verdict-4096-confirm (gemma-4-31B thinking): 96/96 parseable, all stop, 0 invalid. REJECTED on headroom: 2 completions (3,407 and 3,580) > 3,276 (80% of 4,096). p50=1,352 p90=2,139 max=3,580.
- gemma4-checker-4096-confirm: PASS_stage2 (96/96, max completion 46% of cap).
- llama-verdict-512-confirm: PASS_stage2 (96/96, max 40% of cap).
- gemma4e4b-verdict-8192-triage: DEAD. Together returns 400 'Unable to access non-serverless model google/gemma-4-E4B-it' on every attempt; the model is not serverless-servable, and dedicated endpoints are out of scope.
- qwen35_9b-verdict-16384-triage: DEAD. Longest probe emitted 16,384 tokens finish_reason=length, no verdict. Combined with the 8,192 rejection at stage 1, degenerate thinking, not under-budgeting.

Spend: stage-2 reserved $7.08 (envelope $14), actual settled $2.46, uncertain $0.03. All screening to date: actual $3.10. Aggregate accounted spend stands near $33.7 of the $60 cap.

PROPOSED NEXT STEP (stage 3): re-screen the two thinking-verdict models at raised caps, since each raised cap is a NEW admission unit under the frozen unit definition (no paper-only inference): qwen38-verdict-16384 and gemma4-verdict-8192, 96-probe 0-of-96 confirmation each, same acceptance rules (0 invalid, none over 80% of the NEW cap, complete batch). Worst-case reservation ~$11.7 (qwen38 at 16,384 dominates), expected actual ~$2.2 given observed generation lengths. On both passing, the successor rebuilds as N=3 (gemma-4-31B verdict@8192 + checker@4096, Llama-3.3-70B verdict@512, Qwen3.8 verdict@16384) under a fresh identity, with owner sign-off sought on the N=3 roster and raised caps before the rebuild (the owner already selected 'Last 4-judge try, N=3 fallback' at the r19 gate).

DECISIONS to rule on:
1. Probe selection for stage 3: (a) reuse the SAME 96 stage-2 probes per model for a paired before/after comparison at the new cap, or (b) draw a FRESH distribution-matched 96 excluding all previously used probes (bank has 636 candidate prompts per verdict role; ~516 remain). I lean (a) for direct comparability and because temp-0 generation lengths are stable, but (b) is cleaner for an independence claim.
2. Qwen3.8 verdict cap: 16,384 (observed max 7,653 = 47%) vs 12,288 (= 62%, cheaper worst-case reservation). I lean 16,384: reservation cost is transient, truncation at scale is not.
3. Any objection to treating the weak slot as closed (N=3) rather than screening further exotic candidates, given every remaining Together serverless model is either non-thinking (already covered by Llama), delisted, degenerate, or in another family already on the roster?
```

## Verdict

No blockers. Stage 3 is a valid screen of two new admission units; the reservation remains within the $60 cap, and owner sign-off before rebuilding is appropriate.

1. **Decision 1: (b), fresh 96-probe samples.** Reuse would be adaptive to observed failures and weaken the confirmation claim; direct comparability is less valuable than independent admission evidence.

2. **Decision 2: 16,384.** The 12,288 cap leaves limited protection against an observed heavy tail, while 16,384 gives meaningful deployment headroom at acceptable expected cost.

3. **Decision 3: Close the weak slot and use N=3.** Further exotic screening has poor expected value given the exhausted serverless candidate set; retain the stated owner sign-off on the final roster and raised caps.
