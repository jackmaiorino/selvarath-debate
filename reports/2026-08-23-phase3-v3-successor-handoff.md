# Phase 3 v3 successor handoff

Status: the owner-approved offline design is ready. Paid preflight and main authorization are
not ready. No provider call, archive write, GPU run, or spend authorization occurred during this
work.

## Fixed design

- Provisional judges: `google/gemma-4-31B-it`,
  `meta-llama/Llama-3.3-70B-Instruct-Turbo`, `google/gemma-3n-E4B-it`, and
  `Qwen/Qwen3.7-Max`.
- `openai/gpt-oss-120b` remains excluded because its 3/96 strict INVALID result failed the
  `<2%` gate. Do not waive, retry, or replace it.
- `Qwen/Qwen2.5-7B-Instruct-Turbo` remains conditional under the existing v2 deferral until its
  192 cells complete and pass, `2026-08-29T00:00:00Z`, or main authorization, whichever resolves
  first. Final roster size is four or five.
- With fewer than six judges, the capability slope is estimate-and-plot only, with no p-value.
- The successor needs a new protocol ID, namespace, hash, and run identity. Every calibration,
  budget-smoke, and capability-anchor cell runs fresh for the final roster. No v1 or v2 result row
  carries forward.

The machine-readable contract is
`rejudge/phase3_v3_successor_design_2026-08-23.json`, canonical SHA-256
`75c1790a54d7a6ca780839f8a1efe4ca5a5db9aee075d4c473be516004cc0479`.

## Frozen operational formulas

L90 uses the final uninterrupted canary window. Anchor one-hour half-open blocks at its first
unique ruling, include zero-ruling blocks, discard the last partial block, and require at least
eight full blocks. For block rates `x_i = 24 * count_i`, use
`max(0, mean(x) - t_0.95,n-1 * sample_sd(x) / sqrt(n))`.

Applied retrospectively to v2 only as a diagnostic, the 11 full-hour counts are
`128,128,64,128,128,64,64,128,128,128,164`. The resulting L90 is 2,287.247746 rulings per 24
elapsed hours. The partial 49.75-minute tail contains 100 rulings and is excluded by the formula.
This is not a v3 configuration selection.

The forecast must include zero-query and early-DONE slots as zeros and sum every retry. Unknown
charges require separate reserved input and output token counts. Price by the model actually
called, so checker and oracle calls never inherit the primary judge's price. Use exact
provider-matched tokenizer revisions to render every applicable role template against all 492
main transcripts. Proxy tokenizers, byte bounds, character estimates, missing prompts, or stale
prices block certification. Add accounted spend across v1, v2, and v3 segments.

## Materialization work for Fable

1. Resolve the Qwen2.5 branch, then materialize the final roster in a full v3 protocol and new
   namespace.
2. Extend reservations and unknown terminal rows with separate
   `reserved_prompt_tokens` and `reserved_completion_tokens`. The v2 ledger has 149 unknown
   charges and none has this split, so it cannot support the corrected forecast.
3. Materialize a zero-filled slot-role frame for `judge_query`, `judge_verdict`, `query_checker`,
   and `oracle_verification`, grouped by billed model, source judge, role, condition, and question.
4. Produce the exact-tokenizer manifest and rendered 492-transcript role corpus. Stop if an exact
   provider-equivalent tokenizer for any billed model cannot be established. Do not substitute a
   proxy.
5. Take a serverless availability and input-output price snapshot after roster resolution and
   within 24 hours of paid authorization. Refresh it immediately before any main authorization.
6. Create one small run manifest containing the commit, Python and dependency versions, linker
   version or `not_applicable`, seeds, input SHA-256s, planned outputs, GPU ordinal or `not_used`,
   final roster, protocol hash, tokenizer-manifest hash, and price-snapshot hash.
7. Rerun one preflight seed end to end and require a bit-identical output-store SHA-256. Then ask
   Jack for separate successor-canary spend authorization.

Offline verification command:

```powershell
uv run python scripts\phase3_v3_successor_preflight.py
uv run pytest -q tests\test_phase3_v3_successor_preflight.py tests\test_phase3_canary_closeout_v2.py
uv run ty check scripts\phase3_v3_successor_preflight.py tests\test_phase3_v3_successor_preflight.py
```

The current preflight output is
`rejudge/phase3_v3_successor_preflight_2026-08-23.json`. Its expected state is
`successor_design_ready=true`, `paid_preflight_ready=false`, and
`main_authorization_ready=false`.
