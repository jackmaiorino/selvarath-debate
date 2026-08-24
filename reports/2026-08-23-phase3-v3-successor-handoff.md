# Phase 3 v3 successor handoff

Status: offline canary materialization is ready on branch
`codex/phase3-v3-successor`. Jack approved replacing `Qwen/Qwen3.7-Max` with
`Qwen/Qwen3.5-397B-A17B`, and Hugging Face access for Llama 3.3 is active. The final
protocol, fresh prices, portable exact-tokenizer corpus, run manifest, and readiness report
all validate. No provider inference, archive write, GPU run, or paid spend occurred.

The final readiness report says:

- `offline_canary_materialization_ready: true`
- `paid_preflight_ready: true`
- `canary_spend_authorized: false`
- `main_authorization_ready: false`

`paid_preflight_ready` means the offline inputs are complete enough to request a capped paid
canary authorization. It is not authorization.

Fable's original worktree remains untouched at
`C:\Users\Jack\Dev\FailureModeExperiment\selvarath-debate`, branch `rerun-new-models`, HEAD
`265ef526b2cecd415316ade4539ab1df1cd6fabb`. Its nine original untracked handoff files remain
there.

## Effective roster

- `google/gemma-4-31B-it`
- `meta-llama/Llama-3.3-70B-Instruct-Turbo`
- `google/gemma-3n-E4B-it`
- `Qwen/Qwen3.5-397B-A17B`

`Qwen/Qwen2.5-7B-Instruct-Turbo` remains excluded because its Together endpoint is no longer
available. `openai/gpt-oss-120b` remains excluded after failing the frozen strict INVALID
gate. Final roster size is four. Capability slope is estimate-and-plot only, with no p-value.
No v1 or v2 result row carries into a v3 measurement slot.

The original Qwen 3.7 protocol and tokenizer artifacts remain historical. Do not use them for
execution. The effective protocol is r2 and the portable tokenizer manifest is r3.

## Authoritative artifacts

- Roster amendment:
  `rejudge/phase3_v3_amendment1_qwen3_7_replacement_2026-08-23.json`, canonical SHA-256
  `44f082a459cd22e5c8586043e18f781e624ebb487c0f7fa074087f9930136b58`.
- Roster resolution: `rejudge/phase3_v3_roster_resolution_r2_2026-08-23.json`, canonical
  SHA-256 `12449711abb2aab91249d65a9f9765339f381e13805ab209e83df3e371802d1d`.
- Protocol: `rejudge/phase3_protocol_v3_r2.json`, canonical SHA-256
  `1415949888eefdd995d2ae8c7870b0fd5949fcdfe5f3ea4ea4e6d3ec4c93038e`.
- Protocol pin: `rejudge/phase3_protocol_v3_pin_r2.json`, canonical SHA-256
  `20db4e6bcc098abe63ab78e62c0af37cd702f7e1dbfaa8f40567b949fc7e6620`.
- Post-resolution Together catalog:
  `rejudge/phase3_provider_models_raw_r2_2026-08-23.json`, canonical SHA-256
  `b1b3e99fd6a7697707f8c39e05663aa5dc4609fb0c62b2dddf19f63088e1b541`.
- Price snapshot: `rejudge/phase3_v3_price_snapshot_r2_2026-08-23.json`, canonical SHA-256
  `f87f73d4755da16c42a210b93aaa8e833f2a745136d70185ccb4df8ebc0383d5`.
- Portable tokenizer spec:
  `rejudge/phase3_v3_exact_tokenizer_spec_r3_2026-08-23.json`, canonical SHA-256
  `7aa1601d8fa3a95d03bc21312b1e32e891050cc93567f89d0795a02568e38241`.
- Portable tokenizer manifest:
  `rejudge/phase3_v3_exact_tokenizer_manifest_r3_2026-08-23.json`, canonical SHA-256
  `b6958c1c6bb4db275e49e6444c8abb405d377ea0db7e9ccac16d133cd589b127`.
- Tokenizer acquisition summary:
  `rejudge/phase3_v3_tokenizer_acquisition_r3_2026-08-23.json`, canonical SHA-256
  `b2d8d827abb8a8589218f513469e811f1c45baa84704ff3d82cec48a3ba984b8`.
- Run manifest: `rejudge/phase3_v3_run_manifest_preflight_r3_2026-08-23.json`, canonical
  SHA-256 `d000aa7369150d05abf08fac36610608ffab129aaa1fc7af2c483b10225ad3b6`.
  Run ID is `phase3-v3-b233b50fabe479f9`; it binds Git commit
  `935e25fc8432e245eeab334fe5ece7821ad471ef`.
- Final readiness report: `rejudge/phase3_v3_successor_preflight_r5_2026-08-23.json`,
  canonical SHA-256 `ea9a8cc2ac6e31ce0708eb68fce253705a5c4e21cd7692cbf2776cd6f8f76bf6`.

The r2 tokenizer manifest is superseded because it included Hugging Face cache metadata. The
r3 manifest binds exactly 14 tokenizer files and is the only tokenizer manifest to use.

## Exact tokenizer state

- Gemma 4 repository `google/gemma-4-31B-it`, revision
  `842da3794eaa0b77d5f08bae87a17459d91ff475`.
- Llama repository `meta-llama/Llama-3.3-70B-Instruct`, revision
  `6f6073b423013f6a7d4d9f39144961bfbfbc386b`. Its local chat-template SHA-256 exactly
  matches Together's exposed template.
- Gemma 3n repository `google/gemma-3n-E4B-it`, revision
  `c1221e9c62e34a43ab7ffacd1be0ea71f126ef10`.
- Qwen repository `Qwen/Qwen3.5-397B-A17B`, revision
  `8472618112abcbd45acbcdc58436aff4233c23f7`.

Together accepts consecutive user messages for Gemma 3n while the public template contains a
role-validation guard. The local renderer removes only that guard for the one frozen template
hash and preserves every message boundary. It matched all 20 sampled historical first-query
prompt counts exactly and 19 of 20 sampled budget-zero verdict counts exactly; the remaining
verdict differed by one token. The full corpus then passed a second render-and-count check over
56,700 prompts.

Portable tokenizer directories and the corpus are intentionally ignored local inputs:

- `rejudge/output/phase3_v3_tokenizers/google--gemma-4-31B-it-exact`
- `rejudge/output/phase3_v3_tokenizers/meta-llama--Llama-3.3-70B-Instruct-exact`
- `rejudge/output/phase3_v3_tokenizers/google--gemma-3n-E4B-it-exact`
- `rejudge/output/phase3_v3_tokenizers/Qwen--Qwen3.5-397B-A17B`
- `rejudge/output/phase3_v3_exact_tokenizer_corpus_r3_2026-08-23`

Frozen transcript bundles remain at
`E:/selvarath-archive/phase3-materialization-2026-08-18/`. Preserve these local inputs when
moving worktrees.

## Run identity and verification

The run manifest records seeds `harness=20260829` and `analysis_bootstrap=20260830`, GPU
`not_used`, and four planned append-only canary stores under
`E:/selvarath-archive/phase3-v3-2026-08-23/`. All output hashes remain null because no run has
started. Its harness status is `pending`.

Verification completed:

- Protocol create/check cycle: bit-identical pass.
- Focused v3 regression after the roster and tokenizer changes: 69 passed.
- Full repository regression: 2,258 passed, 65 skipped in 501.87 seconds.
- Final readiness-report and run-manifest regression: 20 passed.
- Focused type analysis over every changed Python file: pass.
- Tracked r3 tokenizer manifest replay: four models, 14 tokenizer files, 56,700 prompts, pass.
- Fresh price snapshot: four required serverless models, pass.

The price snapshot was retrieved at `2026-08-24T02:27:45.415224Z`. Refresh it if more than
24 hours old and immediately before requesting paid main authorization.

## Remaining sequence

1. Obtain Jack's separate authorization for the successor canary with an exact dollar cap.
2. Under that cap, run one declared seed end to end twice and require a bit-identical output
   store SHA-256. This is paid provider work and has not started.
3. Record the harness pass, then run the fresh successor canary once.
4. Build the exact dynamic-residual and cost forecast from the fresh canary ledger.
5. Require all canary gates, an estimable L90, a certified forecast within the stage cap, and
   separate owner main authorization before any main call.

Do not restart v2, infer the 149 historical missing reservation splits, carry old rows into v3,
use the superseded r2 tokenizer manifest, run on a different Qwen model, or treat offline
readiness as spend authorization.
