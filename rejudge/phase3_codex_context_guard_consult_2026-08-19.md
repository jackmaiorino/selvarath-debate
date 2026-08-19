# Consult: context-guard estimator, byte-conservative vs token-accurate, before the phase-3 main run

One-shot, self-contained. Pre-registered AI-control experiment (debate oversight), phase-3
canary live, 96% converged, ~$10 spent of $60. The frozen protocol measures judge error at
oracle-query budgets {0,1,2,4,8} on 492 frozen debate transcripts, 6 judges, main run 29,520
judgment slots pending authorization.

## The finding

The transport layer's context guard (phase-2 code, hash-bound, audited, live through the
entire phase-2 main run) estimates request size as PROMPT BYTES + max_tokens and compares
that against the model's context ceiling in TOKENS. Bytes overcount tokens roughly 4x for
English text. At budgets 0-2 (phase 2's whole range) even the overcount fits every ceiling,
so this conservatism was invisible and harmless. At budget 8 the accumulated query history
pushes the OVERCOUNT past ceilings: a deterministic ex-ante precheck that mirrors the guard
exactly (built today, tested, byte-identical reruns) excludes 100% of b8 cells for 3 of 6
judges (Qwen2.5-7B and gemma-3n-E4B at 32,768; gpt-oss-120b at 128,000 where the reasoning
floor of 4,096 max_tokens per query call inflates the worst case to ~246k "tokens"), plus a
genuinely marginal 36 of 2,952 b4 cells. True token counts for the same b8 contexts are
plausibly 6-12k plus output: comfortably inside every ceiling. So the b8 arm of the knob,
the pre-registered dose-response deliverable, is blocked for half the roster by estimator
conservatism, almost certainly not by physics.

Ground truth available: the canary's usage ledger holds ~3,900 SETTLED calls with
provider-reported actual prompt/completion token counts alongside the byte-based estimates,
so any replacement estimator can be validated empirically against real tokenizer behavior on
exactly this workload before it is trusted.

## Options

A. Keep the byte-conservative guard, pre-register the exclusion list (it is deterministic,
   hash-bound, ex ante). Consequences: Delta(8) becomes a 3-judge contrast (its b0
   comparator restricted to the same 3 judges, paired); the b8 point of the knob loses the
   two weakest judges, which phase-2 exploratory analysis says carry most of the harm signal,
   arguably gutting the b8 measurement scientifically; 36 b4 cells excluded with paired
   common support.
B. Replace the guard's ESTIMATE with a token-accurate one (exact tokenizer via the already
   vendored tokenizers/transformers dev deps, or bytes/k divisor with a safety margin),
   validated against the ~3,900 ledger actuals with a pre-registered never-underestimate
   requirement (e.g. estimator >= provider-actual on 100% of observed calls, plus a fixed
   headroom). The guard's comparison logic, ceilings, and fail-closed behavior stay
   untouched; only the size estimate changes. Requires: code-bundle change to audited
   transport code, manifest re-mint, owner sign-off, targeted tests, and re-running the 72
   blocked canary cells (the budget-smoke gate needs b8 for every judge).
C. Anything better we have not considered.

## Questions (crisp, numbered; disagree freely)

1. A or B (or C)? Is there any defensible scientific reading under which A's 3-judge b8 arm
   is preferable to touching audited transport code under B's controls?
2. If B: exact tokenizer per model family vs a single conservative divisor with margin?
   Models span Qwen/gemma/Llama/gpt-oss tokenizers; exact per-family adds dependency and
   drift risk; a divisor with the empirical-validation gate may be sufficient. Specify the
   validation rule you would demand (quantile, margin, sample).
3. The reasoning-model worst case: for history-size estimation the precheck assumed each
   judge_query can return up to its 4,096-token reasoning floor as QUERY TEXT entering the
   history. Actual query text is capped by the template contract (one atomic claim). Should
   the worst-case history term use the role's max_tokens or an empirical maximum from the
   ledger (with which safety factor)?
4. Governance: we treat the guard change as an owner-signed amendment with manifest re-mint
   (the estimator is a safety control). Sufficient, or does it need more (fresh consult on
   the diff, independent review before the re-minted manifest executes)?
5. If any residual exclusions survive B (true overflows on the longest transcripts), state
   the exact pre-registered handling you would put in the protocol amendment: exclusion with
   paired common support across ALL arms of the affected contrasts, or cell-level INVALID,
   or something else.
6. The canary currently finishes with 72 b8 cells blocked pending this decision; its
   completion gate says 100% of manifested slots. Is "100% of non-blocked slots + the
   blocklist hash recorded + the 72 re-run under the amended guard before gates finalize"
   an acceptable gate reading, or must the canary re-run wholesale under the amended guard?


---

# Codex opinion (GPT-5.6, one-shot, 2026-08-19)

Choose C: option B, but with a context-only estimator and an enforceable bound on visible query history. A is acceptable only as a secondary restricted-roster analysis.

1. **C over A.** A changes D8 from the registered six-judge estimand into a selected three-judge estimand. Pairing each survivor with its b0 result removes baseline differences within those judges, but it does not repair judge-selection bias or make D8 comparable with the lower-budget points.

   A is defensible only as a secondary result labeled “D8 among high-context-capable judges,” or if no replacement guard can pass validation. It is not scientifically preferable here. The current blocklist has already been falsified as a physics boundary: three listed b8 cells completed all eight queries successfully despite precheck estimates above 32,768.

2. **Use one conservative divisor, not local “exact” tokenizers.** I recommend:

   `E_prompt = ceil(prompt_bound_utf8_bytes / 3) + 512`

   `E_total = E_prompt + effective_request_max_tokens`

   Exact local tokenizers would still need pinned vocabulary, revision, chat-template, and provider-serialization assets. The installed libraries alone do not reproduce Together’s framing. Provider-reported prompt usage is the better ground truth.

   Crucially, introduce a separate context estimator. Do not replace `_estimate_usage`: its byte estimate also drives spend and unknown-charge reservations in [api_client.py](/C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/api_client.py:194) and [api_client.py](/C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/api_client.py:1342). Weakening it globally would change another safety control.

   Validation rule:

   - Freeze the ledger prefix by sequence, terminal hash, and raw SHA-256.
   - Use every terminal call with provider `prompt_tokens`, covering every model, role, budget, and query index. No quantile gate.
   - Require, for every call:  
     `E_prompt >= actual_prompt_tokens + max(512, ceil(0.25 * actual_prompt_tokens))`.
   - Apply the same rule prospectively to every call made while completing the previously blocked b8 cells.
   - Any miss blocks main authorization and requires a new amendment. Unknown-charge calls have no token ground truth and must be reported separately.

   Preliminary read-only replay over the live prefix, which advanced beyond 4,800 successful calls during this review, found zero misses for `/3 + 512`, with at least 1,248 tokens and 67% relative headroom. An in-memory replay of both frozen plans yielded zero exclusions under this estimator and the history caps below. That is encouraging, but the formal report must use a frozen ledger prefix.

3. **Do not use the 4,096 reasoning floor as historical query text.** Hidden reasoning tokens never enter later messages. The 4,096 belongs once as the current call’s output allowance.

   I disagree that “one atomic claim” currently provides a hard cap. The template has no numeric limit, raw output is appended before screening in [judge_loop.py](/C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/judge_loop.py:160), and the semantic screen has no length check. A fixed ledger prefix contained visible query responses as large as 2,204 UTF-8 bytes for gpt-oss and 2,839 for gemma-4. The existing precheck’s `"X" * max_tokens` construction is therefore not a valid token or byte bound; see [phase3_context_precheck.py](/C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/scripts/phase3_context_precheck.py:163).

   My preferred amendment is a mechanically enforced visible-response cap, checked before history insertion and never implemented by truncation:

   - Base-256 query models: 1,024 UTF-8 bytes.
   - Reasoning-floor query models: 6,144 UTF-8 bytes.
   - An over-cap response follows a fixed malformed-query retry/block transition.
   - The precheck uses that cap for both possible attempts.

   These are at least twice the observed per-class maxima, rounded upward. Replay all completed canary query responses against the caps before carryforward. If you decline enforcement, a 2x empirical maximum is only a forecast, not a worst-case guarantee, and an unexpected runtime overflow must halt.

4. **Require one independent diff review.** Owner-signed amendment, successor manifest, targeted tests, and fresh authorization are sufficient governance, provided one independent reviewer checks the actual implementation before execution. No larger process is needed.

   The successor identity should bind:

   - Estimator formula and constants.
   - Visible-history cap and transition semantics.
   - Frozen ledger prefix and validation report.
   - Estimator, precheck, runner, and completion-control code.
   - Any eligibility list and its generating inputs.

   This matters because the current provenance list omits the precheck and orchestrator scripts; see [phase3_manifest.py](/C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase3_manifest.py:94). The review must confirm admitted-call request bytes, seeds, model parameters, cache keys, ceilings, and accounting reservations remain unchanged.

5. **Use frozen common support, never cell-level INVALID.** INVALID counts wrong in the registered primary analysis, so coding transport ineligibility as INVALID would manufacture errors.

   Put this rule in the amendment:

   > Before main authorization, freeze and hash eligibility from frozen prompts, transcripts, role limits, estimator version, and history caps. For D_b, analyze b and b0 on matched common support keyed by `(question, judge, debater, transcript, side, replicate)`. For S1, analyze b8 and b2 on their matched intersection. Do not encode context-ineligible slots as INVALID. Any runtime ContextGuardError outside the frozen list halts execution. If any judge-condition stratum has zero support, main authorization requires an owner re-decision.

   Keep D1 and D2 full-domain if unaffected. For the readable five-point dose-response curve, additionally report a descriptive curve restricted to the all-arm intersection. The existing inference design already permits contrast-specific frozen domains in [phase3_protocol.json](/C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase3_protocol.json:187).

6. **No wholesale canary rerun, but amend the gate explicitly.** Completed cells may carry forward if replay proves that the new history cap would not have changed them and the estimator change affects admission only. Then execute every still-missing manifested slot under the successor identity.

   The final gate must remain:

   > Every original manifested canary slot is settled across the predecessor and successor execution segments; blocked records do not count as completion. Completion is evaluated against the original 1,440-slot denominator.

   “100% of nonblocked slots plus blocklist hash” is acceptable only as an interim operational checkpoint. It is not a valid reading of the current frozen completion gate in [phase3_protocol.json](/C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/rejudge/phase3_protocol.json:288).

   Also, do not hard-code “72 remaining.” My current store snapshot found three of the 72 blocklisted keys already completed, leaving 69 absent at that instant. Compute the successor worklist as `manifested keys - completed keys`. The current orchestrator subtracts all 72 from its raw denominator, so it can declare convergence three rows early; see [phase3_canary_orchestrator.sh](/C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate/scripts/phase3_canary_orchestrator.sh:93). Treat any such reduced-denominator convergence as provisional.

No files were changed.


