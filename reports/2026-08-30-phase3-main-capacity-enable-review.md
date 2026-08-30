# Phase 3 reviewer-capacity enable review

Date: 2026-08-30. Status: read-only implementation review. No reviewer dispatch or external
execution occurred, and `--run` remains hard-disabled.

## Findings

1. P1, public execution is not connected. `phase3_main_capacity_execution.py` contains a
   fake-validated 180-dispatch engine, exact manifest and authorization validators, and detached
   SSH signature verification. Its public `execute_capacity_preflight` function still raises
   unconditionally, and the CLI refuses `--run` before loading context. Consequence: a correct
   signed capacity authorization cannot currently produce capacity evidence.
2. P1, hard-crash usage is bounded but not exactly reconstructible. The engine durably writes one
   attempt reservation before its dispatch history start and refuses any reuse, so a crashed
   identity cannot redispatch. Per-child attempted count exists only in memory until ordinary
   exception closeout or the successful result. Consequence: after process death, the exact number
   of released reviewer calls cannot be recovered from capacity-owned records.
3. P1, the last input check is outside the child launch. `_recheck_dispatch_inputs` verifies packet,
   CLI, runner, and host immediately before calling the reviewer runner, but the unguarded
   `codex_reviewer_batch.run_one` then reopens the packet and executable without comparing them to
   manifest-supplied hashes. Consequence: a change in that handoff gap can reach the reviewer and
   be detected only after external work.
4. P1, main admission authenticates only the base capacity result. `phase3_main_live._validate_capacity`
   calls the base plan and result validator. The main manifest does not bind the capacity execution
   manifest, signed capacity authorization, detached signature, attempt reservation, or 180 reopened
   invocation receipts. Consequence: the stronger execution provenance already checked by
   `phase3_main_capacity_execution.validate_execution_result` is not a launch prerequisite.
5. P2, blocker strings are stale relative to partial implementation. Detached signature
   verification and ordinary failure receipts exist, but the public blocker still groups them with
   unimplemented work. Consequence: the remaining closure scope is harder to review precisely.

## Minimal closure contract

1. Add a capacity-specific durable reservation for each packet before child release. Bind run,
   attempt, manifest bytes, authorization bytes, wave, packet, payload, prompt, reviewer runtime,
   UTC reservation time, usage unit, and quantity one. A reservation survives success, failure, or
   hard crash and can never be reused.
2. Give `codex_reviewer_batch.run_one` optional exact expected hashes for packet bytes, CLI wrapper,
   and batch-runner bytes. Capacity execution must supply all three. The internal last read must
   compare them before `subprocess.run`; partial or mismatched bindings release no child.
3. Bind all 180 reservation records into successful usage evidence. Add a read-only crash auditor
   that reopens any exact reservation prefix without requiring active authorization and reports a
   conservative observed dispatch count. It must never resume or redispatch.
4. Wire the public capacity function only to fixed production dependencies: the exact reviewer
   runner, monotonic and UTC clocks, repository and host probes, CLI version reader, and pinned-key
   signature verifier. Preserve the current noninjectable public signature and exact 180-dispatch
   authorization limit.
5. Extend the main manifest with raw bindings for the capacity execution manifest,
   authorization, and detached signature. Main preparation must authenticate the signature,
   validate the capacity authorization at its recorded execution time, and call the strong
   execution-result validator to reopen the attempt reservation, dispatch history anchors, wave
   outputs, and 180 unique invocation receipts.
6. Remove the real-capacity and downstream-admission blockers only after focused crash-window,
   handoff-race, signature-drift, reservation-prefix, and main-integration tests pass. Keep all
   provider and main-run authority false. Running the 180 external reviews still requires a fresh
   clean manifest and a separate short-lived owner-signed authorization.

The existing fake engine proves result construction on an ordinary successful process. It does
not yet prove this closure contract, and no existing instruction to continue grants the missing
180-dispatch authority.

## Implementation update

The capacity engine now closes findings 2, 3, and 5 without enabling external execution:

1. Every capacity-owned reviewer handoff writes an exclusive, fsynced per-packet reservation
   before the reviewer runner is called. The record binds the manifest, authorization, wave,
   packet, reviewer runtime, reservation time, usage unit, and quantity one.
2. Successful results bind and reopen all 180 reservation records. Terminal failure receipts
   rebuild their count from disk, including a reservation written before local bookkeeping fails.
   A read-only auditor reconstructs any exact concurrent reservation subset and never grants
   resume or redispatch authority.
3. The reviewer child now compares final packet, CLI-wrapper, and batch-runner reads with all three
   manifest-supplied hashes before `subprocess.run`. Partial bindings and mismatches release no
   child.
4. Blocker text now names only the remaining public wiring and main-admission provenance work.

Verification: 225 capacity, reviewer provenance, reviewer commit, recovery, and failure-path tests
passed. Focused static type checks and bytecode compilation passed. No reviewer dispatch, provider
call, main launch, or external execution occurred. Findings 1 and 4 remain open, and the public
capacity function and CLI `--run` path remain unconditionally disabled.

The main-admission provenance work in finding 4 is also closed. The v6 main manifest and v6 exact
authorization contract require raw bindings for the capacity execution manifest, capacity
authorization, and its exact detached signature sidecar. Initial preparation, launch freshness,
and in-run revalidation authenticate the capacity signature, validate its authority at the
recorded capacity completion time, rebuild the execution manifest, and reopen the attempt
reservation, dispatch history, 180 per-child reservations, wave outputs, and 180 invocation
receipts. Capacity evidence remains non-authorizing on its own. A combined fake-only test exercises
the actual capacity executor and main admission join end to end. Verification is 324 passed and 2
skipped across the related main, capacity, reviewer provenance, reviewer commit, recovery, and
failure-path suites. Finding 1 is the only implementation gap left in this review.
