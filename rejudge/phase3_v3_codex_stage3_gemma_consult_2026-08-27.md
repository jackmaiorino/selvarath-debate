# Codex consult: stage-3 gemma-4 runaway finding (2026-08-27)

One-shot, gpt-5.6-sol high, codex session 01a04574-9c6c-7bb1-997b-45c775c24927, ephemeral read-only sandbox. Note: the orchestrator's brief leaned toward option C; Codex ruled C gate-weakening and ranked B first. The sandbox blocked codex's one attempted repo scan (visible in the session log); the ruling used only the brief.

## Brief

```
One-shot consult, selvarath phase-3 successor (continuation of today's stage-2 consult where you ruled: fresh probes, Qwen3.8 at 16,384, weak slot closed at N=3). Stage-3 raised-cap screens are done and produced a split with a mechanism finding. Answer with numbered blockers or 'no blockers' plus a ranked recommendation among the OPTIONS.

STAGE-3 RESULTS (96 fresh distribution-matched probes each, zero overlap with earlier stages, verified):
- qwen38-verdict-16384: PASS. 96/96 parseable, all finish_reason=stop, max completion 6,215 = 38% of cap (p50 1,245, p90 2,604). Across all 216 probes at three caps Qwen3.8 always terminates, tail ~6-8k. Genuinely bounded; admitted per your prior ruling.
- gemma4-verdict-8192: REJECTED on the headroom rule, and the failing probe is a MECHANISM finding, not a near-miss: finish_reason=length at the FULL 8,192 with a 3,133-token prompt, while the other 95 probes all finished (stop) at <= 2,686 tokens (p50 1,408, p95 2,538). The completion still contained a parseable strict verdict (runaway continued after the verdict was emitted), so invalid=0.

CORROBORATION of a rare runaway mode for gemma-4-31B thinking (temp 0, seeded, so per-prompt deterministic):
- Sealed r6-archive store: exactly 1/96 gemma-4 b0 verdicts EMPTY at the old 4,096 cap (runaway inside the think block -> no verdict).
- The r13 checker halt: one gemma-4 checker cell, finish=length ct=4,096, all thinking, no verdict (the event that motivated the amendment-5 terminal-halt disposition).
- 120 verdict probes at 4,096 (stages 1-2): zero truncation, max 3,580; 96 probes at 8,192: one full-cap runaway on a prompt never probed at 4,096.
Model: ~1% of judgment prompts deterministically trigger unbounded generation; where the runaway starts decides whether the verdict still parses (this probe) or the output is empty (r6 store, r13). No cap contains it; larger caps only change the cost per event (trivial, ~$0.02).

CONSEQUENCE: gemma-4 has NO passing verdict configuration (4,096 rejected at stage 2 with 2 near-cap; 8,192 rejected at stage 3 with the runaway). The frozen admission rule, applied honestly, excludes gemma-4 from the judge_verdict role. Its query_checker config PASSED 0-of-96 at 4,096 (max 46% of cap), and the amendment-5 terminal-halt machinery already handles rare checker runaways with hard stop bounds.

OPTIONS (rank, or propose better):
A. One more screen at gemma4-verdict-16384 (~$1.7 reserved, ~$0.3 actual). Under the runaway model P(pass) ~ 0.99^96 ~ 38%, and a pass would be sampling luck that admits a judge with ~1% deterministic-truncation cells; the canary's strict INVALID gate (invalid <= 1 of 96 per judge on b0) then sits exactly at the expected event count. I see this as buying a coin flip, not evidence.
B. N=2 rebuild: judges = Llama-3.3-70B (non-thinking, passed 0-of-96 at 512) + Qwen3.8 (passed at 16,384); gemma-4 keeps ONLY the checker role it passed. Large disclosed scope reduction (judge-diversity dimension shrinks further), but every admitted configuration has clean screening evidence and the canary stays a fair test.
C. Admit gemma-4-verdict at 8,192 with a pre-registered amendment-5-STYLE verdict-truncation disposition: a finish=length verdict cell whose strict parse still succeeds keeps its row (verdict was emitted before the runaway); one that is empty/unparseable becomes a terminal INVALID cell under the existing frozen stop bounds (>20 cells, >4% mirror units, concentration -> owner halt). This treats the known ~1% mechanism the way the checker side already treats it, changes no gate thresholds, and keeps N=3. Requires a control-plane amendment extending the terminal-halt record kinds to the verdict role BEFORE launch (pre-registered, not reactive).
D. Pause: report that screening excluded gemma-4's verdict role and let the owner rethink scope.

My lean: C, with A as an optional cheap adjunct (screen 16,384 for cost/telemetry, not admission), because C is honest about a mechanism that cap-raising provably does not fix, uses machinery you already ratified for the same mechanism on the checker, and preserves the N=3 science. B if you judge C to be gate-weakening. The owner decides regardless; I need your technical ruling first.
```

## Verdict

1. **Option C is gate-weakening.** Gemma-4 failed the frozen verdict admission rule. A pre-launch amendment cannot turn that failure into passing evidence; it creates a mechanism-specific exception after observing the failure.

2. **The checker analogy does not transfer.** A checker runaway is bounded orchestration failure. A judge runaway creates prompt-dependent missingness in the measured endpoint. Because the trigger is deterministic by prompt, INVALID cells cannot be assumed exchangeable across treatments or controls.

3. **The canary gate has little power against this mechanism.** At a 1% event rate, \(P(X \le 1)\) for 96 cells is about 75%. Thus the existing `invalid <= 1` gate will usually admit the known failure mode.

4. **Option A cannot rehabilitate Gemma-4.** A 16,384 screen could characterize cost, but a pass would be expected sampling luck. The established mechanism, not the particular cap, is dispositive.

Ranked recommendation:

1. **B.** Rebuild at N=2 with Llama-3.3-70B and Qwen3.8. Keep Gemma-4 checker-only. Explicitly revise any N=3 aggregation, disagreement, and statistical language before launch.
2. **D.** Choose this if N=3 judge diversity is scientifically essential. Prefer replacing Gemma-4 with a freshly screened model over exempting it.
3. **C.** Technically executable, but it is a revised, weaker experiment and should not be described as preserving the frozen admission contract.
4. **A.** Do not use for admission. Run only if the extra telemetry independently matters.

Technical ruling: **B**. Qwen3.8 is admitted at 16,384; Gemma-4 is excluded from `judge_verdict` and remains admitted only for `query_checker`.
