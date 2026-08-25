# Codex consult: uncertain-spend halt resolution (2026-08-25)

One-shot consult, gpt-5.6-sol via codex-cli 0.149.0, run by the successor orchestrator session 3cf5f3a5 after the r9 halt. Brief and verdict verbatim.

## Brief

```
One-shot consult. Self-contained brief; no repo access needed. You are the standing
second-opinion oracle for Jack's debate-oversight control experiment (selvarath).
Decision needed tonight; owner has granted blanket approval for in-phase amendments
and asked for autonomous overnight operation. Please stress-test the plan.

CONTEXT
Phase-3 v3 successor canary (eligibility/engineering gate, NOT main-run evidence).
Frozen protocol v3 r3. 1,008 planned rows: 48 preseeded transcripts + 192 capability
anchors + 768 judgment cells, 4 judges (gemma-4-31B, Llama-3.3-70B-Turbo,
gemma-3n-E4B, Qwen3.8-2.4T-A95B), serial provider worker, reviewer = codex CLI
(64-payload waves between passes). Append-only hash-chained usage ledger; every call
reserves worst-case cost first; $60 aggregate cap including all halted-predecessor
spend; main spend prohibited in code. Manifest binds the execution-code git commit,
so ANY code change forces a new run identity. Prior halt records state successor
runs must not reuse partial measurement rows.

HISTORY (all fail-closed, cheap): r5 halt = Qwen3.5 needs dedicated endpoint
(roster swapped to Qwen3.8, owner approved). r7 halt = Qwen3.8 stream ended without
usage chunk; fix = non-streaming for that model. Today run ab48e68863878f49:
one-seed harness passed bit-identical twice (the exact previously-failing call);
formal drive launched 13:08Z.

TODAY'S HALT (new, 18:28Z)
gemma-4-31B degraded 17:06-18:17Z: 5 timeouts + 1 HTTP 503. Strict client policy
(halt_on_unknown_charge=True at call level) recorded each as terminal
unknown_charge, holding reserved cost ($0.0919 total) as uncertain spend; the run
DEFERRED those cells and continued; all six cells later completed under fresh
reservations. But the end-of-pass accounting path calls the same validate_ledger
used as the LAUNCH gate, which requires uncertain_spend == 0 and zero
unknown_charge events. Pass 1 accounting ran 17:00:50Z (before first timeout) and
passed; pass 2 accounting at 18:28Z refused: run exited fail-closed. State at halt:
674/1,008 rows committed (192/192 capability, 434/768 judgments), 64 pending-label
cells with PAID completed provider work sitting in the call cache awaiting review
rulings, wave 1 = 64/64 rulings committed clean. Spend: $4.5755 actual + $0.0919
uncertain = $4.6674 successor; $4.9772 aggregate of $60. Ledger has zero unmatched
reservations; archive sealed read-only; halt record committed.

THE DEFECT (my diagnosis)
Control-plane inconsistency between two frozen policies: (a) in-run transport
policy deliberately survives billing-uncertain events by deferring cells and
holding reserved cost against the cap; (b) validate_ledger, written as a
zero-spend launch gate, is also invoked per-pass, at resume, and at finalize, and
refuses any uncertain spend. Consequence: the first billing-uncertain transport
event after launch makes the run unfinishable AND unresumable, with the halt
deferred to the next pass boundary. Not a provider defect, not science.

OPTIONS
A. Code amendment + full redo under a fresh identity: introduce an explicit
   uncertain-spend tolerance for in-run/resume/finalize ledger validation
   (launch of a FRESH ledger still requires exactly zero). Uncertain spend counts
   fully against the $60 cap (already true) and gets its own ceiling (I propose $5
   per successor run; the phase-2 main run used a $25 uncertain ceiling under
   owner amendment 13). Final report must state uncertain totals and event list.
   Redo all 960 gate rows fresh: ~$6-7, ~5-6 h. No row/cache reuse.
B. Same amendment + authorize reuse of the 674 committed rows and/or the paid
   call cache from the halted identity, redo only the remainder (~$2-3). Violates
   the standing no-row-reuse successor constraint; cheaper but muddies the
   gate's single-execution coherence.
C. As A but wait for gemma-4 to stabilize first (Together status unknown; the
   endpoint was still completing most calls during the degradation window).
D. Resolve uncertainty externally: query Together billing per attempt to convert
   unknown charges to known amounts, add a reconciliation event type. New external
   dependency and schema surgery mid-phase; feels out of scope tonight.

MY RECOMMENDATION: A, relaunch tonight without waiting (the amended policy
tolerates recurrence by design; gemma-4 was completing most calls even while
degraded). Implementation: new binding/role-limits schema rev carrying the
tolerance constants bound to the r9 halt-record hash (mirroring how the
non-streaming fix was bound to r7); new manifest identity; authorization r5 with
the same $60 aggregate cap and $4.97721071 prior accounted carry, owner provenance
= his standing blanket amendment approval (verbatim quotes preserved, as in r4);
new tests for the tolerated-uncertain paths + fix the now-stale zero-fresh-ledger
test; full suite green before launch.

QUESTIONS
1. A vs B vs C vs D, and why. Is redo-all clearly right for an eligibility gate?
2. Exact invariant shape: where should zero-uncertain still bind (launch only?),
   what ceiling value, and should unknown_charge events be allowed as terminal
   states everywhere else? Any reporting requirements you would mandate in the
   final report/gates section?
3. Does a full redo under a fresh identity preserve the canary's meaning without
   any protocol change? (Same frozen protocol v3 r3, same plan, fresh seeds not
   needed since seeds are protocol-derived per cell, deterministic.)
4. Blockers I might be missing: price snapshot age (r4 snapshot captured
   2026-08-24T23:52Z; relaunch would be ~26-30 h later; refresh rule says refresh
   if >24 h old, which forces a new snapshot artifact + manifest rebind anyway);
   review-pace crank window; per-model retry policy for a flaky-but-alive
   endpoint; anything else?
5. Any reason this rises above in-phase operational authority (transport/control
   plane, no science change) such that I should wake the owner rather than
   proceed under his blanket amendment approval + autonomy instruction?

Answer with numbered verdicts and any hard blockers first.
```

## Verdict

Verdict: choose **A**, with two corrections: use a **$1.00 uncertain-spend ceiling**, not $5, and rerun the paid one-seed harness under the amended code before formal launch.

Hard blockers before relaunch:

1. Refresh the price snapshot immediately before manifest freeze. Recompute every worst-case reservation and bind the new snapshot hash.
2. Commit the amendment, create a fresh identity, and authorize that exact identity. Keep the halted archive, 674 rows, 64 cached calls, and rulings strictly read-only.
3. Rerun the amended end-to-end harness twice. The formal opening carry must therefore be `$4.97721071 + exact new harness exposure`, not necessarily `$4.97721071`.
4. Split ledger integrity from phase policy. If the â€œzero-fresh-ledgerâ€ test literally asserts that a new run-local ledger is empty, it remains correct and must not be weakened.
5. Add bounded retry and circuit-breaker behavior. Unknown-charge tolerance must not become unlimited paid retrying.

1. **A vs B vs C vs D: A is clearly right.**

B violates the explicit fresh-row successor rule and mixes identities, code versions, provider periods, and reviewer state. That is a scientific/provenance amendment, not merely an operational repair. The paid cache is especially unsuitable because those outputs were never converted into committed rulings.

C should be an automatic consequence of failed preflight, not an arbitrary waiting period. Togetherâ€™s public page reported all services online at 18:27Z, including Gemma 4 31B, despite your attempt-level failures. That makes the page too coarse to serve as the launch gate. Use the fresh harness and circuit breaker instead. [Together status](https://status.together.ai/)

D does not repair the underlying defect and cannot rescue a sealed identity. Reconciliation may be useful later as a separate append-only financial artifact, but schema surgery tonight adds risk without avoiding the full redo.

2. **Exact invariant shape**

Use separate validation modes:

- `FRESH_LAUNCH`: new run-local ledger, cache, results, and rulings are empty; actual, uncertain, open reservations, and unknown-event count are all zero. Predecessor carry may be nonzero and is checked separately.
- `CHECKPOINT` and `RESUME`: unknown-charge events are allowed after ledger recovery, provided the hash chain and attempt lifecycle are valid, all ambiguous in-flight reservations have become conservative terminal unknowns, and bounds hold.
- `FINALIZE`: same accounting rules, plus zero open reservations, every planned cell completed, no pending labels, and exactly one committed result per cell.

Always enforce:

```text
aggregate_exposure =
  predecessor_actual
+ predecessor_uncertain
+ current_actual
+ current_uncertain
+ open_reservations
<= $60.00
```

Before each dispatch:

```text
current_uncertain + proposed_worst_case_reservation <= $1.00
```

I recommend **$1.00**, because it is more than ten times todayâ€™s $0.0919 burst but only about 14 to 17 percent of the expected $6 to $7 run. A $5 ceiling would allow unresolved exposure approaching the expected cost of the entire canary.

An `unknown_charge` may be terminal for an attempt, but never counts as cell completion. It must have a matching prior reservation and dispatch, retain the full reserved amount, contribute no partial response to review, and remain immutable. A later cell attempt gets a new reservation.

If no bounded policy already exists, use:

- Maximum three paid attempts per cell.
- Defer after an ambiguous failure; no immediate hot-loop retry.
- Open the model circuit after three consecutive ambiguous failures or five in the last twenty attempts.
- Pause 15 minutes, then issue one accounted probe. Two failed probes halt the run.
- Stop provider dispatch whenever 64 paid payloads await review.

Final reporting should include exact actual and uncertain totals, predecessor carry, remaining cap, every unknown event with model/cell/attempt/timestamp/reserved amount/failure class, retry counts by model and experimental arm, and the successful attempt that ultimately completed each affected cell. Label accounting as `PASS_CLEAN` or `PASS_CONSERVATIVE_UNCERTAIN`, not simply `PASS`.

3. **The full redo preserves the canaryâ€™s meaning.**

Yes. The experimental protocol remains v3 r3 if the row plan, protocol-derived seeds, prompts, roster, endpoint parameters, reviewer instructions, and analysis gates retain identical hashes. The new execution-policy version must be disclosed, but it is not a scientific-protocol change.

Reusing the same deterministic seeds is appropriate. The essential condition is that all 960 generated gate rows and rulings come from the new identity and no old cache lookup is possible. The 48 preseeded transcripts remain fixed protocol inputs.

4. **Additional blockers and checks**

Besides the price refresh:

- Test the complete injected path: reservation, timeout or 503, terminal unknown, deferred cell, fresh reservation, successful completion, review, checkpoint, process resume, and finalize.
- Give the paid harness its own empty ledger or preflight identity. Fold its exact exposure into the formal runâ€™s opening carry.
- Bind the exact reviewer CLI version, model selection, model endpoints, streaming settings, request timeouts, and retry policy. Do not tune timeouts mid-run.
- Ensure cache keys and paths contain the execution identity and reject nonempty foreign state.
- Report whether failures cluster by arm or cell class. Content-dependent timeouts could otherwise create a retry-selection effect.
- Authorize only this exact fresh successor identity. Do not automatically chain further formal successors if it hits the uncertainty ceiling.
- Preserve the main-run prohibition. Canary completion must not automatically unlock main spend.

5. **Owner authority**

On the facts stated, this does **not** require waking the owner. It is an in-phase transport/accounting amendment, keeps the $60 aggregate cap, preserves fresh-row execution, and changes no scientific gate.

The authorization record still must bind the exact post-commit identity, refreshed pricing artifact, predecessor actual and uncertain components, and the ownerâ€™s verbatim approval basis. If the standing quote covers amendments but not paid relaunches under new identities, then wake him. Option B, a cap increase, a protocol/roster/gate change, or enabling main spend would also exceed the stated authority.

I did not inspect the repository. Prior project memory was used only to cross-check the exact-identity authorization and fresh-run safeguards.


