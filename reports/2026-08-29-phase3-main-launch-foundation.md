# Phase 3 main offline launch foundation

Date: 2026-08-29. Status: offline implementation candidate. This document authorizes no provider call, reviewer dispatch, paid execution, or spend.

## Current outcome

The repository now has an offline launch validator, a deliberately blocked execution skeleton, artifact-bound finalization, and the confirmatory analysis handoff for the Phase 3 main design. The planned inventory remains 82 questions, 492 frozen transcript rows, 9,840 judgment slots, and 10,332 total result rows. The judge endpoints remain `Qwen/Qwen3.8-2.4T-A95B` and `meta-llama/Llama-3.3-70B-Instruct-Turbo`.

The public `--run` path refuses before it creates the formal artifact root, consumes a local identity, constructs a provider client, or spends money. Tests may exercise private seams with that blocker patched out, but those tests are not an authorization mechanism.

## What is implemented offline

- The small manifest binds the exact source identity, toolchain, seeds, inputs, outputs, inventory, local billing record, forecast, capacity evidence, and harness receipt. It cannot authorize execution.
- A separate authorization schema binds the canonical manifest, run, endpoints, reviewer configuration, cap, forecast, and attempt count. Detached SSH verification is implemented, but no owner public key or fingerprint is pinned.
- The formal driver has no public client, cap, concurrency, path, or resume injection seam. Its lower-level loop is serial for provider work, journals every response, halts on unknown charge, uses the measured 60-packet reviewer wave, and derives enough passes for the declared 59,040-payload worst case. The reviewer JSONL parser rejects malformed or unknown event shapes and refuses every recognized tool-bearing item type.
- Current price and reviewer-capacity freshness are established immediately before irreversible identity consumption. During the no-resume run, exact evidence bytes, source bindings, runtime identity, and the active signed authorization are revalidated without incorrectly treating a 24-hour launch-freshness window as the lifetime of a 41-day worst-case run.
- The exact tokenizer context index is reconstructed in memory from the bound tokenizer manifest. A nonconstructible JSON tuple-key index is no longer part of the launch manifest.
- The context blocklist is rebuilt from the frozen prompts, role limits, transcript bundle, and live-guard estimator in both launch validation and analysis. The bound report must equal the deterministic recomputation in full.
- Pre-main local billing accepts only a validated closed disposition. Historical unknown-charge attempts may close only inside the conservative provider-observed envelope, and the complete accounted upper bound must equal the manifest prior spend.
- For completed result-bearing judgments, finalization reparses raw verdicts, recomputes correctness and polarity, validates frozen seeds and parser identity, binds stored verdict messages to the journal request fingerprint, checks exact call schedules, and joins each expected call to one journal row and one settled ledger success. It validates checker and oracle model identities and requires the reviewer decision store to cover exactly the journaled query payloads. It also binds the exact authorization and detached-signature bytes and the finalization-relevant manifest output paths, rejecting same-directory shadow artifacts.
- Standalone analysis independently binds the signed manifest and authorization, exact run and paths, authorization window, context report, prior spend, cap, result snapshot, and analysis pins. It reopens and rebuilds finalization before analysis.
- The isolated harness proves one declared property only: rerunning one seed produces a bit-identical output-store hash. It does not independently prove prompt correctness or playing strength.
- Generic transcript preseeding now rejects a conflicting existing row instead of silently trusting it. Formal preseeding continues to require all 492 exact frozen rows.

## Why production remains blocked

The following are real launch blockers, not documentation chores:

1. No owner signing key is pinned, and no final signed authorization exists.
2. The protocol cap is $450 while the earlier launch discussion proposed $875. One exact cap must be ratified consistently before a manifest can be authorized.
3. The billing export is a local JSON artifact, not provider-authenticated evidence. The runtime credential is not bound to the reconciled Together account, and the predecessor-ledger inventory has no independent authoritative source.
4. The local one-attempt registry can be defeated by deleting the registry. A production authorization needs a non-resettable external consumption authority.
5. The planned run may last 41 days, but there is no signed response protocol for a provider price change during that interval. The authorization deadline must also cover every provider dispatch, reviewer dispatch, and finalization, preferably through the declared 45-day maximum.
6. Codex reviewer calls do not yet have separately ratified authority and spend accounting. The child process checks only a deadline, not the complete signed authorization and capacity bytes before every dispatch.
7. Finalization does not yet reconstruct every transcript row and logical request from the frozen protocol, prompt bundle, role limits, and bound transcript bundle. That gap covers verdicts, `judge_query`, `query_checker`, and `oracle_verification`. It also does not independently compare the post-role-limit provider kwargs hash recorded by the ledger.
8. Reviewer decisions are joined semantically to result events, but the final reviewer packet tree, worklist, model, effort, and zero-tool-use evidence are not yet independently rebuilt by finalization.
9. No final current price snapshot, certified forecast, isolated harness receipt, small manifest, or launch authorization has been materialized for a production identity.

The correct current execution state is therefore $0 authorized, zero provider calls, and zero reviewer dispatches.

## Next sequence

1. Finish repository-wide tests, static checks, diff review, and commit this offline milestone.
2. Add independent reconstruction for all non-verdict logical requests, post-role-limit provider kwargs, and reviewer packet provenance.
3. Ratify the in-run price-change response, complete-run authorization window, and separately bounded reviewer authority and spend.
4. Choose the external sources of truth for provider billing, account identity, predecessor-ledger completeness, and one-attempt authorization consumption.
5. Ratify one cap and pin Jack's public signing key. Keep the private key outside the repository and inaccessible to Codex.
6. Materialize fresh prices, capacity evidence, billing evidence, forecast, harness receipt, manifest, and exact owner authorization only after blockers 2 through 5 are closed.
7. Run `--validate-only` and confirm zero state mutation. A separate explicit authorization would still be required before any paid `--run` invocation.
