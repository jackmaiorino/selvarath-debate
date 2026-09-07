# Codex one-shot consult: main identity void and corrective successor (2026-09-07)

Model gpt-6-astra, reasoning ultra, session 01a0797b-d153-7a13-a7f9-31cb01ab7045, run ~02:10Z by Claude session f817b4ab under the owner's Phase 3 delegation. The brief is reproduced verbatim, then the opinion. Em-dashes in the opinion were replaced by commas.

## Brief

You are a one-shot methods and engineering reviewer for a pre-registered, hash-bound AI-oversight experiment (Phase 3 main run, debate judges with oracle budgets). Answer in under 900 words, plain text, no em-dashes, no tools. Be decisive; give verdicts and the strongest objection to each.

WHAT HAPPENED (2026-09-07)
- Main identity phase3-main-34010df12dee7c38 (source commit 2707cd3, manifest canonical 1ea1a5d8, signed authorization under the owner's written delegation, $1,100 stage cap including $119.27 prior accounted, certified forecast $908.82) launched 19:48Z on 9/6. Pass 1 completed 2,460 of 10,332 rows (2,029 settled Together calls, $24.90, zero unknown charges). Reviewer wave 1 (60 packets, codex-cli 0.149.0 pinned wrapper, gpt-5.6-sol) dispatched under the dispatch guard; every packet verified at dispatch time and every ruling was written.
- The driver then exited with: REFUSED Phase3MainLiveError: reviewer invocation evidence failed for <payload>: reviewer dispatch guard evidence no longer verifies exactly.
- Root cause, reproduced offline: scripts/codex_reviewer_batch.py validate_invocation_evidence() re-evaluates the DISPATCH_GUARD snapshot after the wave, calling evaluate_dispatch_guard_snapshot(codex, model, effort, concurrency, packet_directory, checked_at) WITHOUT the invocation's model_provider_profile (or legacy transport flag). The v6 capacity plan's reviewer_configuration carries model_provider_profile {id openai-http, wire_api responses, ...}; the guard evaluator compares plan.reviewer_configuration.model_provider_profile against the supplied value (None) and raises "capacity model provider profile drifted". The recheck therefore fails for every receipt under any v5/v6 plan. Re-running the identical recheck with the profile passes and yields byte-identical evidence. The dispatch-time evaluation and the batch preflight both pass the profile; only the receipt-validation recheck omits it. The same validator is used by finalization/provenance verification, so this would also have refused at the end.
- Why it was not caught: the capacity preflight has no dispatch guard (the guard binds a main manifest/authorization), so the recheck path never ran live under a profile-bearing plan; the provenance test fixture's plan carries neither a profile nor the legacy transport flag, so None == None passed. No environmental change occurred (pinned wrapper sha unchanged, host unchanged, worktree clean).

DESIGN FACTS THAT CONSTRAIN THE SUCCESSOR
- Single-shot rule: the identity is void; no resume; rows and rulings retained as evidence only. Nobody has read any verdict content from the dead identity (only counts and costs).
- Manifest restart modes: "initial" and "environmental_successor". The environmental mode requires a void record with reason_code in {host_failure, network_outage, power_loss, provider_outage} and a billing reconciliation with provider_settlement.status == provider_authenticated_finalized covering the predecessor lifetime. The ratified billing policy uses Together console Cost Analytics evidence (the API route is unavailable), whose settlement status is the console kind, so the environmental successor path appears unusable under current policy regardless of reason. None of the four reason codes describes a code defect anyway.
- The identity registry keys identities by run id + manifest sha; a new identity at a new commit is not blocked by the started-but-unvoided predecessor.
- Cost forecast validation requires the forecast's cumulative_spend_segments to equal the billing reconciliation's reconciled ledgers (18 ledgers, $119.27 accounted). Adding the dead identity's ledger as a segment would require a new console reconciliation and its ratification chain (owner-side, days). The stage cap module hardcodes the predecessor upper bound $119.27 and the ratification binds it. The runtime client enforces the $1,100 cap on the identity's own spend.
- Capacity v6 evidence (valid to 2026-10-21) binds the reviewer CLI wrapper, model, effort, concurrency, host, and provider profile, but NOT the batch runner script hash (that is bound per wave in the dispatch guard). So fixing codex_reviewer_batch.py does not invalidate the capacity evidence.
- Price snapshot verified 2026-09-06T17:45Z is fresh for 24 h. Manifest requires clean tree at the bound commit, harness two-execution bit-identical receipt at that commit, fresh forecast, signed authorization (delegated signature with the disclosure sentence), validate-only, then run.
- Owner delegation: "full spend and execute auth for the entire phase 3"; standing authorization to decide and act, report afterwards. Scientific laws: no outcome tuning, single-shot discipline, hash-resolving audits.

PROPOSED PLAN
1. Fix: in validate_invocation_evidence, pass the invocation's normalized model_provider_profile (or its legacy openai_provider_supports_websockets when that field is present instead) into the recheck; they are mutually exclusive by construction. Add regression tests: (a) provenance fixture variant whose plan/result/invocation carry the real v6 model_provider_profile, asserting the full provenance verification passes; (b) a direct validate_invocation_evidence call on every receipt of that fixture with the expected profile. Run the focused suites and the full suite.
2. Successor: fresh "initial" manifest at the fix commit, new run id, new artifact root, same capacity v6 evidence, same price snapshot (still fresh), recomputed certified forecast with the UNCHANGED 18-ledger segments ($119.27) because the reconciliation cannot change without owner console work, new signed authorization under the same delegation. Record amendment 15 (defect, fix, void of the predecessor, successor) and account the dead identity's $24.90 settled spend explicitly in the authorization's exact text arithmetic and the amendment as stage spend counted against the $1,100 cap out-of-ledger (precedent: screening spend was handled this way in canary authorization r11): 119.27 + 24.90 + 908.82 = 1,052.99 <= 1,100. Nothing about outcomes or pre-registered constants changes.
3. Add a pre-launch gate that exercises the exact receipt recheck path under the real bound capacity plan (offline, synthetic receipt under a temporary guard) so this class of gap cannot recur; at minimum the regression test loads the real v6 profile.

QUESTIONS
1. Verdict on the fix and tests; anything else in the validation chain likely to share the gap (finalization provenance verifier, reviewer commit path)?
2. Verdict on relaunching as an "initial" manifest with the dead identity documented by amendment and out-of-ledger cap accounting, versus waiting for an owner-side reconciliation that adds the dead ledger as a segment. Is the out-of-ledger treatment scientifically and financially honest? What must the amendment say?
3. Is the environmental-successor mode a latent deadlock under the console billing policy? Should that be recorded as a known limitation now (no code change) rather than fixed before relaunch?
4. Does the delegation cover relaunching after a code fix, given the owner ratified the cap and proposal at commit 2707cd3? The fix touches only the receipt validator (no measurement semantics). What disclosure wording should the successor authorization and the run report carry?
5. Any reason the retained 2,460 rows or 60 rulings should influence anything downstream? (Our position: evidence only, never analyzed.)

## Opinion

1. Approve the fix, with negative regression coverage.

Passing the recorded, normalized provider configuration restores the dispatch-time contract. Preserve the original checked_at and exact evidence comparison. Detect legacy field presence, not truthiness: an explicitly recorded false must survive. Do not fall back to current defaults or regenerate evidence to make verification pass.

The proposed positive tests are appropriate. Add rejection cases for a missing or mismatched required profile and coverage for the legacy transport field. Exercise receipt validation through reviewer commit and finalization once using synthetic material. The shared finalization validator should inherit the fix; independent evaluator calls and configuration serialization boundaries are the other likely failure sites.

Strongest objection: positive fixtures can reproduce the same omission on both sides and pass. The regression must contain the actual v6 profile structure and demonstrate that removing or changing it fails.

2. Approve the fresh-identity route in principle, but do not launch exactly as proposed without enforcing the aggregate cap.

Out-of-ledger accounting can be scientifically and financially honest. The $24.90 must be explicitly outside the reconciled ledger segments but inside stage expenditure. Do not describe the unchanged reconciliation or certified forecast as incorporating that spend.

The arithmetic is:

Prior reconciled spend: $119.27
Voided identity spend: $24.90
Successor forecast: $908.82
Expected stage total: $1,052.99
Remaining forecast headroom: $47.01
Maximum successor expenditure: $955.83

An identity-local $1,100 limit does not enforce the stage cap. Require an effective successor ceiling of $955.83, including outstanding liabilities and every charge category covered by the stage cap. Authorization prose alone is insufficient. If predecessor liabilities exceed $24.90, reduce that ceiling accordingly.

The amendment must identify the predecessor and its authoritative void record; describe the defect and fix commit; state that no verdict content was inspected; exclude predecessor observations from inference; explicitly authorize a code-defect restart represented by an “initial” manifest; preserve scientific constants and seed rules; explain the reconciliation limitation; and distinguish reconciled spend, additional accounted spend, forecast, and enforceable remaining budget.

Strongest objection: “initial” could become a loophole around restricted successor rules. Registry acceptance does not establish methodological permission. The amendment must explicitly establish this restart’s authority rather than imply that it was the experiment’s first attempt. If the governing contract requires every stage charge to enter reconciled segments before further spending, the screening precedent cannot override that requirement. Then reconciliation or an authorized policy amendment is necessary.

3. Yes, environmental-successor mode is a latent deadlock under the stated policy.

Its required settlement status cannot be produced by the ratified console route. Separately, its reason-code vocabulary excludes this defect. These are independent incompatibilities.

Record the limitation now and defer the implementation change. Fixing it before this relaunch would expand scope without making this failure environmental.

Strongest objection: deferral leaves a promised recovery path unavailable during the successor. Document that consequence explicitly, and do not claim environmental recovery is operational.

4. On the quoted delegation, yes: it covers a corrective relaunch within Phase 3 and the unchanged aggregate cap.

Ratification at commit 2707cd3 establishes the approved source identity; it does not necessarily prohibit a freshly authorized identity after a validation-only correction. The broad standing delegation supports signing that new authorization. It does not authorize silently changing scientific laws or bypassing mandatory accounting requirements.

Suggested authorization disclosure:

“Signed by [delegate] under the owner’s written delegation granting full Phase 3 spend and execution authority. This authorization binds [successor identity, manifest hash, full commit]. It replaces no prior authorization retroactively. Predecessor phase3-main-34010df12dee7c38 was voided after a provider-configuration omission in receipt revalidation. Its outputs are excluded from scientific analysis. The correction changes validation only; measurement semantics and preregistered analysis gates remain unchanged. Stage accounting includes $119.27 previously reconciled and $24.90 additional predecessor spend, leaving a maximum successor expenditure of $955.83.”

Retain the exact required delegation disclosure sentence as well.

The run report should disclose the failed attempt, completion counts, actual costs, amendment, successor identity, and exclusion of predecessor outputs.

Strongest objection: execution authority is broader than a commit pin but may be narrower than amendment authority. Do not portray this signature as fresh personal owner ratification of the fix.

5. No downstream scientific use of the 2,460 rows or 60 rulings.

They may support billing, count reconciliation, hashes, and operational failure diagnosis without inspecting verdict content. Exclude them from estimates, bootstrap samples, power calculations, prompts, training, fixtures, example selection, and configuration decisions.

Strongest objection: “evidence only” can quietly become exploratory analysis. Reproduce the defect with synthetic receipts and retain the original contents without examining them. Under the stated no-inspection condition, restarting with unchanged scientific rules does not constitute outcome tuning.
