# Codex consult: concentration-bound stop and checker options (2026-08-28)

One-shot, gpt-5.6-sol high, codex session 01a04655-c812-7471-9b3b-76795a44ff9f, ephemeral read-only sandbox.

## Brief

```
One-shot consult, selvarath phase-3 N=2 canary (continuation of today's stage-3 consult where you ruled N=2 with gemma-4 checker-only). The run stopped at the frozen amendment-5 concentration bound and the owner needs an option ruling. Answer with a ranked recommendation and any blockers; brief and self-contained.

WHAT HAPPENED: The N=2 rebuild (judges Llama + Qwen3.8@16384, gemma-4 checker-only at 4096) ran well: harness bit-identical, probe passed, 394/528 rows, one Codex review wave committed. But gemma-4's checker hit its known per-prompt-deterministic runaway (all-thinking to finish=length at the full 4096 cap, no verdict) on FIVE unique sequential_b2 cells. Five cells in one condition at 5/192 = 2.6% crosses the frozen concentration bound (>=5 cells in a judge/condition at >2%), which is never relaxable mid-run. The identity is stopped and cannot resume.

THE MATERIAL FINDING: 5 runaways across 117 executed checker calls = 4.3% PER CALL, roughly 4x the rate your screening protocol measured (gemma4-checker passed 0-of-96 at stage 2, max completion 46% of cap). The canary's checker prompts embed LIVE judge queries composed mid-debate by Qwen3.8 and Llama; the screening bank's probes were protocol-composed from pilot transcripts. P(0-of-96 | 4.3%) ~ 1.5%, so sampling luck is an unlikely full explanation: the runaway rate is prompt-content-dependent and live-query content triggers it ~4x more often. Direct cross-identity replication: one cell (27cf7cbe) ran away identically in both N=2 identities. One cell (1373d5f0) had a healthy first-query checker call (597 tokens, stop) and a runaway second-query call: the mechanism is per-CALL.

PROJECTION: at 4.3%/call the full 192-cell b2 condition yields ~13-16 terminal cells, also crossing the 4% affected-mirror-unit bound. The b2 condition cannot converge under the frozen gemma-4 checker regardless of resumption policy. b0 needs no checker, but b2 IS the treatment (the protocol is the budget knob, b0 vs b2), so a checkerless phase 3 is no experiment.

BUDGET: aggregate would stand at $42.53 of the $60 cap if this identity is chained terminal. A fresh N=2 identity rerun costs ~$6-8; remaining headroom fits ONE more full attempt with modest margin.

OPTIONS (rank, or propose better):
A. Substitute the checker MODEL: meta-llama/Llama-3.3-70B-Instruct-Turbo (non-thinking, structurally immune to thinking-runaway; already on the roster; its verdict role passed 0-of-96 at 512 with max 40% cap). The checker PROMPTS, parser, and gate semantics stay the frozen phase-2 artifacts; only the executing model changes. Requires: admission screening for the llama-checker configuration (0-of-96 on checker-shaped probes, ~$0.5), amendment 9, protocol r6, fresh identity, ~$6-8 rerun. Science cost: the phase-2 checker was gemma-4; phase 3 would disclose the checker-model substitution as an operational-validity necessity, with the checker being a validity-gate component rather than a measured judge. Note: the checker validates JUDGE queries, and Llama is also a judge; a judge's own queries being checked by the same model family is a disclosed conflict-adjacency (gemma-4 checking was independent of both judges; Llama-checker checks its own queries in half the cells).
B. Same as A but screen the checker on LIVE-QUERY-shaped probes, not pilot-bank probes: compose checker prompts from the 394 sealed rows' actual judge queries (evidence-only reuse: screening inputs, never measurement rows). This addresses the content-dependence gap your screening protocol just exhibited. Slightly more build work, same cost.
C. Stop phase 3 entirely and write the report: the canary established that the frozen phase-2 checker configuration is not operationally viable with any admissible thinking model, and the weak-judge and judge-diversity dimensions were already lost. Publishable operational findings, no further spend.
D. Anything involving raising the checker cap or relaxing bounds: I assume refused (cap raises are proven not to contain runaways; bounds are frozen).

My lean: B (the substitution is operationally forced if phase 3 continues, and live-query screening fixes the exact blind spot just demonstrated), with the Llama-checks-Llama-queries adjacency disclosed, and C honestly presented to the owner as the no-further-spend alternative. Rule on: (1) A vs B vs C ranking; (2) whether Llama checking its own judge queries is disqualifying or a disclosable adjacency; (3) whether the live-query screening probes may be composed from the sealed predecessor rows' queries (evidence reuse for screening only, no measurement-row reuse).
```

## Verdict

Ruling: **B > C > A. D is refused.**

1. **Recommend B.** The failure exposed a specific distribution shift between pilot-composed and live-query checker prompts. Repeating the old screening design under A would leave that demonstrated blind spot untested. If Llama fails the live-query screen, stop and take C. Do not shop for another checker.

2. **Llama checking Llama-generated queries is not automatically disqualifying.** It is a disclosable correlated-validity risk because checker decisions can favor errors characteristic of the same model family. Proceed only if the checker gate is narrow and mechanical, and report checker rejection rates separately by originating judge. Any large Llama-versus-Qwen acceptance disparity should block interpretation of the treatment effect.

3. **Using sealed predecessor queries for screening is acceptable**, provided they remain development evidence only:

   - Freeze a deterministic, judge-balanced selection before executing Llama.
   - Include both query positions and all five known Gemma triggers.
   - Make no prompt, parser, or threshold changes after seeing Llama outputs.
   - Reuse none of the predecessor verdicts or outcomes in measurement.
   - Run the fresh identity from scratch and disclose the adaptive checker substitution.

Blockers before spending the final attempt:

- Screen the exact production configuration, including cap, parser, templates, and toolchain. The earlier 512-token verdict-role result does not qualify a different deployed configuration.
- Require 0/96 valid parsed completions plus successful completion on every known trigger.
- Predeclare the judge-origin disparity diagnostic and its consequence.
- Treat the stopped identity as terminal.

C remains the clean no-further-spend choice. A is inferior because it ignores the principal fact learned by the canary.
