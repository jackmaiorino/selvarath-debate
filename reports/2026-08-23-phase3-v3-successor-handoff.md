# Phase 3 v3 successor handoff

Status: the owner-approved offline successor implementation is complete on
`codex/phase3-v3-successor` through `649634b`. The authenticated Together catalog and price
snapshot are captured, and one fixed-roster exact tokenizer is locally validated. The approved
four-judge roster cannot yet satisfy its exact-tokenizer contract because Qwen 3.7 Max exposes
no exact tokenizer. No run manifest exists. No provider inference call, archive write, GPU run,
or paid spend occurred in this branch.

Fable's original worktree remains untouched at
`C:\Users\Jack\Dev\FailureModeExperiment\selvarath-debate`, branch `rerun-new-models`, HEAD
`265ef526b2cecd415316ade4539ab1df1cd6fabb`. Its original untracked handoff files remain there.

## Fixed roster

- Final judges: `google/gemma-4-31B-it`,
  `meta-llama/Llama-3.3-70B-Instruct-Turbo`, `google/gemma-3n-E4B-it`, and
  `Qwen/Qwen3.7-Max`.
- `openai/gpt-oss-120b` remains excluded. Its 3/96 strict INVALID result failed the `<2%`
  gate. Do not waive, retry, or replace it.
- Jack reported that `Qwen/Qwen2.5-7B-Instruct-Turbo` was removed from Together. The exact
  endpoint page currently labels it unavailable while Together's serverless table still lists
  it. The resolution records that documentation conflict, treats the model as unavailable for
  this project, and excludes it without replacement.
- Final roster size is exactly four. Capability slope is estimate-and-plot only, with no
  p-value.
- Every v3 calibration, budget-smoke, and capability-anchor cell is fresh under the v3
  identity. No v1 or v2 result row carries forward.

Bound artifacts:

- Approved design: `rejudge/phase3_v3_successor_design_2026-08-23.json`, canonical SHA-256
  `75c1790a54d7a6ca780839f8a1efe4ca5a5db9aee075d4c473be516004cc0479`.
- Provider observation:
  `rejudge/phase3_v3_qwen2_5_provider_unavailability_2026-08-23.json`, canonical SHA-256
  `ef64ffe1a55c704a9c96074b1ecad0ee1851257eebba8c9389203661bba545f3`.
- Roster resolution: `rejudge/phase3_v3_roster_resolution_2026-08-23.json`, canonical SHA-256
  `ef0cca2c6877e05f2a3c41727d2f538f04cb7382ff1379cb2523d450ff91d22e`.
- Materialized protocol: `rejudge/phase3_protocol_v3.json`, canonical SHA-256
  `f5367f9d3b176aba7f0a2d94e9d3f028920c6c52153c8b6b1ef35e98e61794fd`.
- Protocol pin: `rejudge/phase3_protocol_v3_pin.json`, canonical SHA-256
  `bef50d940466b3c2062f3047d3102b9c111d8242e0100d1e9c3cffbc93e40412`.
- Authenticated provider catalog: `rejudge/phase3_provider_models_raw_2026-08-23.json`,
  canonical SHA-256
  `b1b3e99fd6a7697707f8c39e05663aa5dc4609fb0c62b2dddf19f63088e1b541`.
- Four-roster price snapshot: `rejudge/phase3_v3_price_snapshot_2026-08-23.json`, canonical
  SHA-256 `5025daf763e09678e7a4dbcc64e0ad2eed5452f321cca047b6ee889805a1cec4`.
- Tokenizer acquisition state: `rejudge/phase3_v3_tokenizer_acquisition_2026-08-23.json`,
  canonical SHA-256
  `eed28c8072c2c7fb15d4ce3b70e49aae8f0c416d916252768a5a5858076d835b`.

## Completed implementation

- `72c0a5ebbd321893d55acb2d7ea26606a7c78f7a`: records separate reserved prompt and
  completion tokens, including reasoning-model reservation release.
- `dd573dc989eb4ea5e8d08b00ad7734d24b721557`: deterministic roster resolution, v3 protocol
  and pin materialization, dynamic roster planning, and a non-authorizing run manifest.
- `b819ef1c720d0784e25b7b8b5237f6385e705745`: zero-filled slot-role inputs plus exact
  tokenizer and fresh-price gates.
- `9a6ec79cf6535b5e0f935ffb73d7dee494aaefb2`: byte-identical end-to-end protocol
  materialization test.
- `6ad591ed6069757c66a5c7c9bbe380c2aa8c3b13`: exact offline corpus builder, live frozen
  checker prompt binding, saved-catalog price builder, environment-captured run manifest, and
  separate canary and main authorization.
- `ce473fea099acb2a72dec586cb8f357bc75f15f0`: dynamic-history residuals and complete
  main-grid cost forecast with actual billed-model pricing, U90 clustering, per-line cent
  ceilings, transport multiplier, and cumulative spend.
- `398b8d2629f3e968f9603371a313a73e5eb10029`: provider-unavailable roster outcome, owner
  observation and resolution, fixed four-judge protocol and pin, and resolved preflight.
- `649634b`: create-only authenticated Together catalog capture, fresh four-roster price
  snapshot, exact-tokenizer acquisition findings, and refreshed preflight.

Important implementation invariants:

- The live checker uses the 5,612-character frozen runtime system prompt from
  `phase2_checker_frozen_config_2026-07-23.json`.
- Canary calls require separate owner authorization. Main calls require a certified
  fresh-canary forecast and separate owner authorization.
- The run manifest has an explicit `harness_verified` state. One seed must produce a
  bit-identical output-store SHA-256 on rerun before formal measurement.
- Any used GPU must be exclusive headless GPU ordinal 1. Provider-only work records
  `not_used`.
- Saved catalog prices must be strictly positive. Every new CLI artifact write is create-only.

## Forecast contract

The fixed roster produces 19,680 main judgment slots, 960 fresh canary slots, and 56,700 exact
rendered static prompts. Every planned judgment slot contains `judge_query`, `judge_verdict`,
`query_checker`, and `oracle_verification` rows. Uncalled roles and early-DONE paths are explicit
zeros. Every attempt is counted. Success and charged malformed attempts use actual tokens,
released-no-charge attempts use zero, and unknown charges require their full reserved input and
output split.

The canary estimator computes per-question mean per slot, including zeros, followed by the
central-90-percent upper endpoint across questions. The main projection adds dynamic and
completion U90 to each exact main static context. It prices checker and oracle calls by their
actual billed models, multiplies by 1.15, ceilings each model/source-judge/role/condition line to
whole cents, and adds all actual and uncertain prior-stage spend.

## Verified state

- Full repository regression: `2248 passed, 65 skipped`, exit code 0, 535.98 seconds.
- Focused v3 regression: `61 passed`, exit code 0.
- Focused type analysis over every changed Python file: pass.
- Protocol create/check cycle: byte-identical pass.
- The prior repository-wide `ty` run still had 110 legacy diagnostics outside this lane. Every
  file changed here is clean under focused `ty`.
- Frozen main transcripts: 492, canonical SHA-256
  `d1361003e637b6d8f42fe4d04e8d37f983dd2947e7c29e05c94a99eec091e0d8`.
- Frozen canary transcripts: 48, canonical SHA-256
  `080baac5c53b96f295dbcf39025ac6bf4d742edd62372def1c416e90b0a03864`.
- Ignored `data/transcripts.jsonl` remains byte-identical to Fable's preserved copy, SHA-256
  `3ea8e7f68ebd6a50369ed924b99c34412094c93bc3dea4e60bdebbed9bf31887`.
- Authenticated Together catalog retrieval returned 279 entries and all four fixed-roster model
  IDs. This was one model-list request and zero inference calls.
- `google/gemma-4-31B-it` exact tokenizer revision
  `842da3794eaa0b77d5f08bae87a17459d91ff475` loads locally as `GemmaTokenizer` with the
  required chat template.
- `Qwen/Qwen3.5-397B-A17B` revision
  `8472618112abcbd45acbcdc58436aff4233c23f7` is locally validated as a possible replacement,
  but is not in the roster without Jack's approval.
- Catalog-capture, price-materialization, and successor-preflight tests: `17 passed`.
- Focused type analysis for the catalog and price lane: pass.

## Remaining blockers

1. `Qwen/Qwen3.7-Max` is proprietary. Its authenticated catalog row exposes no repository,
   tokenizer, chat template, BOS token, or EOS token, and Together exposes no tokenizer-count
   endpoint. The approved exact non-proxy contract cannot be satisfied for this judge. Jack must
   approve a roster replacement or a scientific contract amendment. The locally validated
   replacement recommendation is `Qwen/Qwen3.5-397B-A17B`.
2. Exact provider mappings exist for `google/gemma-3n-E4B-it` and
   `meta-llama/Llama-3.3-70B-Instruct-Turbo`, but both Hugging Face repositories require manual
   license access. Jack must grant access and authenticate the local Hugging Face client.
3. The run manifest cannot be built until the final tokenizer manifest exists. If the roster
   changes, rematerialize the protocol, catalog price snapshot, tokenizer corpus, and manifest.
4. A fresh complete successor canary, exact usage ledger, and dynamic residual frame are absent.
5. Jack has authorized neither successor-canary spend nor main spend.

The historical v2 ledger's 149 unknown-charge rows remain unmodified. Their missing token split
does not block v3: the v3 slot and residual frames consume only fresh successor-canary usage, and
historical stages enter cumulative accounting through actual and uncertain spend totals. Never
infer or retrofit the old split.

## Return sequence for Fable

1. Obtain Jack's roster decision for Qwen 3.7 Max. Do not substitute a model silently.
2. Obtain owner-granted Hugging Face access for Gemma 3n and Llama 3.3, then download their exact
   pinned tokenizer files.
3. If Jack approves the replacement recommendation, rematerialize the v3 roster and protocol,
   then rebuild the saved-catalog price snapshot for the new final roster.
4. Run `scripts\phase3_v3_build_tokenizer_corpus.py` with both frozen transcript bundles and all
   final exact tokenizers. Refresh prices whenever the 24-hour window expires and immediately
   before paid authorization.
5. Commit the bound inputs, then run `scripts\phase3_v3_build_run_manifest.py` from a clean
   worktree. The manifest never authorizes execution.
6. Ask Jack for separate successor-canary spend authorization and a stated canary cap.
7. Under that authorization, run one seed end to end twice and require identical output-store
   SHA-256s before formal canary measurement.
8. Run the fresh canary once, then build the cost forecast from its exact result store, usage
   ledger, contexts, prices, and cumulative spend segments.
9. Require all canary gates, an estimable L90, a certified forecast within the stage cap, and
   separate owner main authorization before any main provider call.

Do not restart v2, infer the missing reservation split, carry old result rows into v3, accept
third-party license terms for Jack, replace an unavailable judge without owner approval, or treat
this offline implementation as spend authorization.
