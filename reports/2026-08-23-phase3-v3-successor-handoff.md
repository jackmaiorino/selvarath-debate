# Phase 3 v3 successor handoff

Status: the owner-approved offline successor design and its implementation are complete on
`codex/phase3-v3-successor` through `ce473fea099acb2a72dec586cb8f357bc75f15f0`.
The final roster is unresolved, so no operational v3 protocol or run manifest exists yet. No
provider call, archive write, GPU run, tokenizer download, or spend authorization occurred in
this branch.

Fable's original worktree remains untouched at
`C:\Users\Jack\Dev\FailureModeExperiment\selvarath-debate`, branch `rerun-new-models`, HEAD
`265ef526b2cecd415316ade4539ab1df1cd6fabb`. Its original untracked handoff files remain there.

## Fixed design

- Base judges: `google/gemma-4-31B-it`,
  `meta-llama/Llama-3.3-70B-Instruct-Turbo`, `google/gemma-3n-E4B-it`, and
  `Qwen/Qwen3.7-Max`.
- `openai/gpt-oss-120b` remains excluded. Its 3/96 strict INVALID result failed the `<2%`
  gate. Do not waive, retry, or replace it.
- `Qwen/Qwen2.5-7B-Instruct-Turbo` remains conditional under the existing v2 deferral until
  all 192 cells complete and pass, `2026-08-29T00:00:00Z`, or the owner invokes the
  main-authorization resolution boundary, whichever occurs first. The resolution artifact
  itself cannot authorize provider calls.
- Final roster size is four or five. With fewer than six judges, capability slope is
  estimate-and-plot only, with no p-value.
- Every v3 calibration, budget-smoke, and capability-anchor cell is fresh under the v3
  identity. No v1 or v2 result row carries forward.

The approved design is
`rejudge/phase3_v3_successor_design_2026-08-23.json`, canonical SHA-256
`75c1790a54d7a6ca780839f8a1efe4ca5a5db9aee075d4c473be516004cc0479`.

## Completed implementation

- `72c0a5ebbd321893d55acb2d7ea26606a7c78f7a`: records separate reserved prompt and
  completion tokens, including reasoning-model reservation release.
- `dd573dc989eb4ea5e8d08b00ad7734d24b721557`: deterministic roster resolution, v3 protocol
  and pin materialization, dynamic four- or five-judge planning, and a small non-authorizing
  run-manifest contract.
- `b819ef1c720d0784e25b7b8b5237f6385e705745`: zero-filled slot-role inputs plus exact
  tokenizer and fresh-price gates.
- `9a6ec79cf6535b5e0f935ffb73d7dee494aaefb2`: byte-identical end-to-end protocol
  materialization test.
- `6ad591ed6069757c66a5c7c9bbe380c2aa8c3b13`: exact offline corpus builder for all main and
  canary static contexts, live frozen query-checker prompt binding, offline saved-catalog price
  builder, environment-captured run-manifest builder, and two-stage canary/main authorization.
- `ce473fea099acb2a72dec586cb8f357bc75f15f0`: canary dynamic-history residuals and the
  complete main-grid cost forecast, including actual billed-model pricing, U90 clustering,
  per-line cent ceilings, transport multiplier, and cumulative spend.

Important corrections in the final implementation:

- The live checker uses the 5,612-character frozen runtime system prompt from
  `phase2_checker_frozen_config_2026-07-23.json`, not the shorter checker text in the prompt
  bundle. Both runtime checker sources and exact prompt hashes are now bound and tested.
- A successor canary cannot require the main forecast because fresh canary usage is an input
  to that forecast. Canary calls require separate owner authorization. Main calls require a
  certified fresh-canary forecast and separate owner authorization.
- The run manifest has an explicit `harness_verified` state. The bit-identical one-seed rerun
  must pass before formal outputs can be finalized.
- Any used GPU must be exclusive headless GPU ordinal 1. Provider-only work records
  `not_used`.
- Saved catalog prices must be strictly positive, and every new CLI artifact write is
  create-only.

## Forecast contract

For every planned judgment slot, the frame contains `judge_query`, `judge_verdict`,
`query_checker`, and `oracle_verification` rows. Uncalled roles, early DONE paths, and
budget-zero query roles are explicit zeros. Every attempt is counted. Success and charged
malformed attempts use actual tokens, released-no-charge attempts use zero, and unknown
charges require their full reserved input/output split.

The exact tokenizer builder renders every applicable static prompt against both frozen
transcript sets. Four judges require 56,700 exact rendered prompts. Five judges require 70,740.
The checker variants expand from 32 to 40 when the fifth source judge is present. Proxy
tokenizers, missing roles, incomplete transcript coverage, prompt drift, file drift, negative
dynamic residuals, stale prices, or a missing reservation split block certification.

The canary estimator computes per-question mean per slot, including zeros, followed by the
central-90-percent upper endpoint across questions. The main projection adds that dynamic and
completion U90 to each exact main static context. It prices checker and oracle calls by their
actual billed models, multiplies by 1.15, ceilings each model/source-judge/role/condition line to
whole cents, and then adds all actual and uncertain prior-stage spend.

## Verified state

- Final full repository regression: `2246 passed, 65 skipped`, exit code 0, 777.57 seconds.
- Focused v3 regression: `58 passed`, exit code 0.
- Focused type analysis over every changed module, CLI, and test: pass.
- All four new CLI `--help` entry points: pass.
- The last repository-wide `ty` run still has 110 legacy diagnostics outside this lane. Every
  changed file is clean under focused `ty`.
- Synthetic four-judge test-fixture protocol canonical SHA-256:
  `5fedef9b39d5b7c45416fcc1c6ac2639de98caa49eaebdca1c2838130c888296`.
  Synthetic pin canonical SHA-256:
  `c0ea19302e3088a61be6e379a991d5290ae3e5c800175db2842e4cbe94480ede`.
  This fixture uses a synthetic deadline outcome and is never an operational early deadline
  selection.
- The frozen main transcript bundle validates at 492 transcripts, canonical SHA-256
  `d1361003e637b6d8f42fe4d04e8d37f983dd2947e7c29e05c94a99eec091e0d8`.
- The frozen canary bundle validates at 48 transcripts, canonical SHA-256
  `080baac5c53b96f295dbcf39025ac6bf4d742edd62372def1c416e90b0a03864`.
- The ignored `data/transcripts.jsonl` fixture remains byte-identical to Fable's preserved copy,
  SHA-256 `3ea8e7f68ebd6a50369ed924b99c34412094c93bc3dea4e60bdebbed9bf31887`.

## Remaining external blockers

1. Qwen2.5 has 0/192 completed deferred cells in the bound v2 closeout. Wait for a legitimate
   recovery completion, the deadline, or an explicit owner resolution at the main-authorization
   boundary. Never select the deadline outcome early.
2. No required provider-matched tokenizer is present in the checked local Hugging Face cache.
   The cache contains only `models--stabilityai--sd-vae-ft-mse`. No tokenizer was downloaded.
3. A fresh Together serverless catalog captured after final roster resolution is absent.
4. A fresh complete successor canary, exact usage ledger, and dynamic residual frame are absent.
5. Jack has authorized neither successor-canary spend nor main spend.

## Return sequence for Fable

1. Materialize and commit the legitimate terminal roster-resolution evidence.
2. Run `scripts\phase3_v3_materialize.py` to create the protocol and pin. Four judges produce
   19,680 main judgment slots and 960 fresh canary slots. Five produce 24,600 and 1,200.
3. Supply local provider-matched tokenizer revisions and run
   `scripts\phase3_v3_build_tokenizer_corpus.py` with both frozen transcript bundles.
4. Capture Together's raw serverless catalog after roster resolution and run
   `scripts\phase3_v3_build_price_snapshot.py`. Refresh it whenever the 24-hour window expires
   and immediately before main authorization.
5. Commit the bound inputs, then run `scripts\phase3_v3_build_run_manifest.py` from a clean
   worktree. The manifest never authorizes execution.
6. Ask Jack for separate successor-canary spend authorization and a stated canary cap.
7. Under that authorization, run one seed end to end twice. Use
   `phase3_v3_run_manifest.record_harness_check` to require identical output-store SHA-256s
   before formal canary measurement.
8. Run the fresh canary once, then run `scripts\phase3_v3_build_cost_forecast.py` against its
   result store, usage ledger, exact contexts, refreshed prices, and cumulative spend segments.
9. Require all canary gates, an estimable L90, a certified forecast within the stage cap, and
   separate owner main authorization before any main provider call.

Do not restart v2, infer the missing historical reservation split, carry old result rows into
v3, download tokenizers without owner direction, or treat this offline implementation as spend
authorization.
