# Phase 3 v3 successor handoff

Status: the owner-approved offline design and its v3 implementation are ready on
`codex/phase3-v3-successor`. Final roster resolution, real exact-tokenizer inputs, and a fresh
price snapshot are still absent. Paid preflight and main authorization are not ready. No provider
call, archive write, GPU run, or spend authorization occurred during this work.

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

## Implemented on the Codex branch

- `72c0a5ebbd321893d55acb2d7ea26606a7c78f7a`: explicit reserved prompt and completion
  tokens, including correct reservation release for reasoning models.
- `dd573dc989eb4ea5e8d08b00ad7734d24b721557`: deterministic four-way Qwen2.5 roster
  resolution, v3 protocol and pin materialization, dynamic four- or five-judge planning, and the
  small non-authorizing run-manifest builder.
- `b819ef1c720d0784e25b7b8b5237f6385e705745`: zero-filled slot-role forecast inputs,
  exact-tokenizer role-corpus validation, raw-catalog-bound price validation, and enforcement of
  those gates at the run-manifest boundary.
- `9a6ec79cf6535b5e0f935ffb73d7dee494aaefb2`: end-to-end deterministic materializer test
  across two fresh roots plus check mode.

The original v2 branch and Fable worktree remain untouched at
`C:\Users\Jack\Dev\FailureModeExperiment\selvarath-debate`.

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

## Return state for Fable

The implementation items above are complete. Do not redo them. Remaining work depends on live or
owner-controlled state:

1. Resolve Qwen2.5 only at a legitimate terminal event: completed recovery, the
   `2026-08-29T00:00:00Z` deadline, or main authorization. Never select the deadline branch early.
2. Run `scripts/phase3_v3_materialize.py` from the terminal resolution. The result is 19,680 main
   judgment slots and 960 fresh canary slots for four judges, or 24,600 and 1,200 for five.
3. Produce the real exact-tokenizer manifest and all role corpora for the resolved billed models.
   The validator rejects a proxy, an omitted role or condition, incomplete 492-transcript
   coverage, count drift, or a file-hash mismatch.
4. Capture Together's raw serverless catalog after roster resolution and no more than 24 hours
   before authorization. The validator binds each model entry and its input-output prices.
5. Build the small run manifest. Its default path verifies all local tokenizer and catalog files,
   and it cannot authorize execution.
6. Ask Jack for separate successor-canary spend authorization. After an authorized canary, rerun
   one seed and require a bit-identical output-store SHA-256 before using its forecast.

The v2 ledger remains diagnostic only: its 149 unknown charges lack the required reserved
input-output split and cannot be repaired into v3 forecast inputs.

## Verification

- Full repository regression: `2231 passed, 65 skipped`, exit code 0.
- Focused v3 regression: `45 passed`, exit code 0.
- Focused static analysis over every v3 implementation and test file: pass.
- Repository-wide static analysis still has 110 legacy diagnostics outside this v3 lane. These
  include Windows `fcntl` stubs and older invariant-list annotations.
- Synthetic four-judge end-to-end materialization was byte-identical across two fresh roots and
  passed check mode. Protocol canonical SHA-256:
  `3fde8054cd16c5f1f36d882408151723611fdbdc60582661ddfebd65e88e956f`. Pin canonical
  SHA-256: `71cb6569f65cf70364be54c8900beefed1c80cd062adaae4fb69f5e0f74895c0`.
- The ignored `data/transcripts.jsonl` fixture was copied into the isolated worktree from the
  preserved Fable worktree. Both copies have SHA-256
  `3ea8e7f68ebd6a50369ed924b99c34412094c93bc3dea4e60bdebbed9bf31887`.

Offline verification command:

```powershell
uv run python scripts\phase3_v3_successor_preflight.py
uv run pytest -q tests\test_phase3_v3_materialization.py tests\test_phase3_v3_materialize_cli.py tests\test_phase3_v3_forecast.py tests\test_phase3_v3_inputs.py tests\test_phase3_v3_run_manifest.py tests\test_phase3_v3_successor_preflight.py
uv run ty check rejudge\phase3_v3_materialization.py rejudge\phase3_v3_forecast.py rejudge\phase3_v3_inputs.py rejudge\phase3_v3_run_manifest.py scripts\phase3_v3_materialize.py scripts\phase3_v3_successor_preflight.py
```

The current preflight output is
`rejudge/phase3_v3_successor_preflight_2026-08-23.json`. Its expected state is
`successor_design_ready=true`, `paid_preflight_ready=false`, and
`main_authorization_ready=false`.
