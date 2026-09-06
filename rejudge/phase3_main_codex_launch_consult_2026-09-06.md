# Codex consult: Phase 3 main launch package and unknown-charge amendment (2026-09-06)

One-shot consult, model gpt-6-astra, reasoning effort ultra, session 01a077a1-a8d6-72d2-8015-bc7a0e48f437, run from a non-repository directory with the brief piped on stdin. Brief raw SHA-256 b5a4b1f4c72df7f31fcbc7debe4b443705ff16de24b2f4b5a1ceed283a1c5b34; opinion raw SHA-256 1554d8cf4522f940aaa128dcdd00212de5de74a67d73964cda426553a2b77ec4.

## Brief

# Consult: Phase 3 main launch package review (ask-package item 9) and a required execution-policy amendment

You are an independent methods and execution reviewer. Everything you need is in this brief; you have no repository access. Answer tersely, numbered, decision-grade. Flag anything you would NOT sign off on.

## 1. Project and design (frozen)

Debate-style AI-control oversight experiment (Manifund-funded, about 7k USD of grant remaining). Phase 3 main run on Together serverless: two judges, Qwen/Qwen3.8-2.4T-A95B (reasoning model, 16,384-token verdict cap, 4,096 floor on other roles) and meta-llama/Llama-3.3-70B-Instruct-Turbo (non-reasoning, 512-token verdict cap; also the query checker at 16 tokens). Conditions: b0 (no oracle) and sequential oracle budgets b1, b2, b4, b8. 82 questions across three fictional worlds, 492 preseeded debate transcripts, 9,840 judgment slots (984 per judge per condition), temperature 0.3 for judge roles with per-slot seeds, oracle and checker at temperature 0. Provider worker concurrency 1 (serial). Reviewer waves of 60 pending oracle-query payloads are ruled by an external Codex CLI reviewer (gpt-5.6-sol, effort high, concurrency 12, ChatGPT login, ceiling 59,040 dispatches counted as non-USD usage).

Primary family: D1, D2, D4, D8 = error(sequential_bk) minus error(b0), question-clustered bootstrap B=10,000, Holm-adjusted two-sided p<0.05 as the sole confirmatory criterion. Secondary S1 = error(b8) minus error(b2), family of one. Strict INVALID counts wrong. Terminal-cell dispositions are pre-registered (checker_malformed, replay_divergence) with frozen bounds: at most 20 unique terminal judgment cells, at most 4% affected mirror units, plus a concentration rule; a terminal cell counts wrong in the primary and its whole mirror unit is excluded from paired analyses. Claims are endpoint-specific only. A request-level journal (your condition C from the 8/29 close-out) persists every returned response before processing, never redispatches a journaled request, and treats a settled success without journal bytes as a fatal ambiguity.

## 2. Where the launch stands (2026-09-06)

- Canary (528 rows) converged 8/29 PASS_WITH_FROZEN_EXCLUSIONS (3 terminal cells, all VS-019/Qwen). You reviewed it 8/29 and signed a conditional funding ask. Your conditions since then: journal built and live-validated 8/29 on VS-019 plus controls (0.32 USD); representative reviewer-capacity preflight replacing the 8-hour pace rule (v5 passed 9/2: 180/180 rulings in 117 s = 1,440 per 24 h vs 1,312 required; expired 9/3; v6 re-measurement with a 1,080-hour window runs today under the identical reviewer profile); tolerant parser confirmed syntax-only; scenario analysis done (3/24 canary questions truncation-prone for Qwen, none for Llama).
- Cap: the owner ratified a 1,100 USD stage cap on 9/4 against a certified forecast of 908.82 USD main spend (U90 per-question clustered estimates, full reserved tokens for unknown charges, 1.15 transport multiplier, whole-cent ceilings), predecessor upper bound 119.27238490 USD (18 ledgers; Together console exact-window 94.07 vs local settled 95.68 plus 23.59 uncertain), projected stage total 1,028.09, headroom 71.91. The close-out actuals-only estimate was 573.60.
- Governance: the owner wrote on 9/4 "stepping away for a couple days. You have full spend and execute auth for the entire phase 3." and on 9/6 a standing authorization: "I authorize full future decision making in the sale of time efficiency, just be sure to communicate to me these things when I return." He then instructed the orchestrator (an AI agent, Claude) to "continue yourself". The orchestrator recorded the v6 capacity ratification with the proposal's exact text verbatim, channel and provenance disclosed (the owner did not retype it), and intends to produce the two detached ssh-ed25519 owner signatures (capacity and main authorizations) with the owner's key under that delegation, disclosed in the ratification record, the launch record, and the report. The authorization schema hard-codes approver "Jack Maiorino".
- Reviewer CLI: the shared npm codex had drifted to 0.153.4; the ratified profile pins http_headers.version=0.149.0 and the manifest builder requires the CLI version to equal it. A dedicated codex-cli 0.149.0 copy was installed (wrapper byte-identical to v5); a transport probe passed (exit 0, clean event stream, ChatGPT auth).

## 3. The blocking finding: the main driver cannot survive one provider timeout

As built (offline, by the previous orchestrator, never run live):
- The accounted client is constructed with halt_on_unknown_charge=True: any attempt whose billing status is unknown (read timeout, 503 after dispatch, wall-clock ceiling) is durably recorded unknown_charge with its full reservation held as uncertain, and the call raises immediately (no retry).
- The journaling wrapper around that client begins each dispatch by writing an unresolved-dispatch marker and, if the inner call raises for any reason, keeps the marker on disk and latches the client so every later dispatch (in this process or any other) is refused "until the usage ledger is reconciled and the interrupted formal run is voided".
- The pass runner is invoked with fatal_unknown_charge=True: the first unknown charge halts the pass.
- The drive loop raises on any halted reason other than checker_malformed. run_main cannot resume: "Any failure after identity start leaves the persistent started record in place and cannot resume." A successor requires a fresh manifest, fresh identity, empty stores, and a fresh signed authorization; no result row is ever reused.
- Finalization requires zero unknown_charge attempts and zero uncertain spend: the journal reconciliation emits a finding for every unknown_charge attempt ("final ledger/journal reconciliation is not empty") and the accounting section raises "current main usage ledger has nonzero uncertain spend".

Evidence from the sealed canary ledgers under the identical transport pins (read timeout 120 s, write 60 s, connect 10 s, per-call wall clock 1,200 s, SDK retries 0, ledger retries 2):
- r15 (the converged canary): 11 unknown_charge events in 5.27 h across 2,594 reservations (0.42% of calls): 9 Qwen "Request timed out." on judge_verdict calls (about 18k prompt tokens, 16,384 completion cap, reasoning model) and 2 Llama HTTP 503 Service unavailable. Reserved cost per Qwen event about 0.35 USD; total 3.25 USD uncertain against a 7.50 per-run ceiling. Together's console later billed 94.07 for a window where local settled spend was 95.68, so most reservations were probably not charged, but that cannot be known per event.
- r14: 4 events in 2.67 h (all Qwen timeouts). r13: 2 events in 1.44 h (1 Qwen timeout, 1 Llama 503).
- Rate about 1.5 to 2.1 unknown charges per hour of serial execution on ordinary daytime weather; evening capacity crunches on 8/26 to 8/28 produced 429s and 503s every few minutes.

Main scale: about 19x the canary's rows, serial, so on the order of 100+ hours of provider execution and 150 to 250 expected unknown charges. Under the fatal policy the expected time to identity death is about 30 minutes; the probability of completing 9,840 slots without a single timeout is effectively zero; every death wastes the identity's spend and consumes a signed authorization. The canary itself only converged because amendment 3 (8/25, Codex-vetted) introduced a bounded per-run uncertain-spend ceiling with abandoned cells retried on later passes; the r15 result you signed was PASS_CONSERVATIVE_UNCERTAIN under that tolerance. The journal-validation record deliberately left the policy for un-journaled failed calls to this consult: "failed calls journal nothing and stay dispatchable, final policy reserved for the main-protocol consult."

## 4. Proposed amendment (execution-only, no science content changes)

A. Client: construct the main identity's accounted client with run_uncertain_ceiling_usd = 100.00 (about 11% of the certified main forecast; about 285 Qwen-verdict-sized events; expected 50 to 90 USD) and initial_run_uncertain_spend_usd = 0. Uncertain reservations already count fully against the 1,100 USD cap through the client's cap check. Reaching the ceiling raises UncertainCeilingHalt before dispatch, which halts the identity fail-closed for owner review; no automatic successor.

B. Journal latch: when the inner call raises UnknownChargeHalt (the ledger has already recorded a terminal unknown_charge event for that attempt and no response bytes were received), the journaling wrapper resolves its unresolved-dispatch marker as a failed dispatch instead of latching, and re-raises so the pass runner abandons the cell for this pass. Every other inner failure keeps today's latch (marker retained, client refuses further dispatch). A process death mid-call still leaves the marker and still voids the identity.

C. Pass runner: fatal_unknown_charge=False. An unknown-charge cell is abandoned for the current pass and retried on a later pass through the existing canary machinery; the existing abandoned-rate guard (at least 100 abandoned cells, or at least 90% of at least 10 attempted) halts the identity as abandoned_cell_rate. The max-pass bound (ceil(59,040/60) + 20 + 2) gets an explicit abandonment allowance.

D. Journal policy for un-journaled failed attempts: the redispatch is a fresh sample with no observed prior generation, so there is no selection among generations; the only exposure is possible double billing, which the uncertain accounting bounds. Ambiguity in your sense (a response received or settled as success but not journaled) remains fatal.

E. Finalization: the reconciliation tolerates findings whose problem is exactly unknown_charge and nothing else (reservation_without_terminal_event, charged_malformed_response, success_without_journal_entry, fingerprint findings, duplicate_success_for_key, journal_entry_without_success all stay fatal). Accounting allows current_uncertain_usd > 0 with completion label PASS_CONSERVATIVE_UNCERTAIN (PASS_CLEAN when zero), reports per-model unknown-charge counts and uncertain dollars, and still requires prior + settled + uncertain <= cap.

F. Binding: a tracked policy artifact rejudge/phase3_main_uncertain_spend_policy_2026-09-06.json (ceiling, treatment, provenance) pinned by raw SHA-256 in the runtime-policies module like the price-change and reviewer-usage policies, so the manifest's source-commit binding covers it. Question: should it also become a required manifest input binding (manifest schema v8) before launch?

G. Not proposed but on the table: the 120-second read timeout is a frozen transport pin in role limits r10. A 16,384-token reasoning completion can legitimately exceed 120 s, so many "timeouts" are probably completed, billed generations we abandon. Raising the read timeout for the reasoning model (for example 600 s, under the existing 1,200 s wall clock) would cut Qwen timeouts and wasted spend but changes a canary-validated transport pin and forces a role-limits r11 rebuild plus rebinding. Keep the pin or change it?

Implementation would be followed by unit tests plus the offline two-execution harness at the final commit, a fresh price snapshot, fresh forecast, exact manifest, signed authorization, validate-only, then launch. Any later identity death still requires a new manifest and a new signed authorization; the orchestrator would report each to the owner.

## 5. Questions (answer each, numbered)

1. Do you sign amendment A to F as specified? If not, state the exact change (ceiling amount, latch resolution rule, abandonment guard, finding tolerance, labeling).
2. Journal policy D for un-journaled failed attempts: is redispatch acceptable as the final policy, or do you require INVALID?
3. Transport pin G: keep 120 s, or change (value, scope)?
4. Is code-pinning by raw SHA-256 plus source-commit binding sufficient for the policy artifact, or is a manifest input binding required before launch?
5. Delegated signing: is producing the owner's detached signatures under the quoted delegation, with full disclosure in records and report, acceptable for this scientific record? Required wording?
6. Main authorization validity window of 46 days (D_max 45; every reviewer release and provider call is checked against valid_until): acceptable?
7. Capacity v6 (same design as v5, 1,080-hour window, pinned CLI copy): any objection?
8. Anything else that must block launch, and your GO / NO-GO conditions.

## Opinion

1. **A–F: conditional approval, not as currently specified.** Keep **$100**, with these requirements:
   - Before dispatch, uncertain spend plus the next reservation must fit the ceiling. Initialize at zero only for a fresh ledger.
   - Resolve the marker only after an attempt-matched, durable `unknown_charge` record confirms no generation content was received, no success exists, and the full reservation remains held. An HTTP error body is not generation content.
   - Keep the abandonment guard **per pass**, with its denominator explicit. A lifetime threshold of 100 conflicts with the expected 150–250 events. Freeze the numerical abandonment-pass allowance before launch.
   - Accept E’s exact finding whitelist and accounting labels. Preserve exclusion status separately. Exhausted bounds mean incomplete execution, not additional terminal-cell exclusions.

2. **Redispatch is acceptable; INVALID is unnecessary for an unobserved transport failure.** Preserve the exact request, slot seed, and accepted upstream history; assign a new attempt ID. Never regenerate already journaled upstream responses. **Reject D’s “only exposure is double billing” claim:** timeout filtering can favor faster generations and correlate with correctness despite no content-based cherry-picking. Report retry rates by model, condition, and question, and describe results as applying to the declared timeout/retry procedure.

3. **Change Qwen’s read timeout to 600 seconds**, retaining the 1,200-second wall ceiling and Llama’s 120 seconds. Rebuild r11, rebind, exercise a representative long Qwen verdict live, and refresh the forecast. HTTPX read timeout measures waiting for a data chunk, not total generation duration. Neither the timeout nor aggregate console totals establish whether abandoned generations completed or were billed. [HTTPX timeout documentation](https://www.python-httpx.org/advanced/timeouts/)

4. **Source-commit binding plus the enforced raw SHA-256 is sufficient.** Manifest v8 is unnecessary if runtime rejects mismatching policy bytes and the manifest binds the implementing commit. Include the policy digest in the launch record.

5. **Delegated signing is acceptable as a disclosed delegated attestation.** The quoted authorization supports proceeding without renewed owner permission. Jack may remain the authorizing principal, but the signed payload or hash-bound provenance must identify Claude as the actual signer. Required wording:

   “Claude generated this signature using Jack Maiorino’s key under his dated Phase 3 delegation. This records delegated approval and does not assert Jack personally reviewed or signed this exact artifact.”

   **I would not sign off on personal-owner-signature semantics**, or copied proposal text represented as owner-authored ratification. The hard-coded approver field alone is insufficient.

6. **Accept 46 days**, with explicit UTC bounds and the 45-day execution fitting within them. Preserve each gate’s separate expiry. Expiry stops new dispatches; already-dispatched responses must still be journaled and accounted. No automatic extension.

7. **No objection to v6’s setup, pending PASS and a defensible capacity calculation.** The dedicated CLI copy resolves the stated version mismatch if actually invoked. However, **180 rulings in 117 seconds does not establish 1,440/day**. Document the release schedule and usage allowance behind that conservative rate. Required capacity is 1,312/day; 1,440/day completes 59,040 dispatches in 41 days, leaving four days.

8. **NO-GO on the current driver; GO once the amendments above and final-package checks pass.** The refreshed forecast must explicitly cover uncertain reservations **and replacement attempts**, explaining what the 1.15 multiplier already includes. The $100 ceiling creates no additional spending authority. Whole-cent ceiling arithmetic gives the existing projection as **$1,028.10**, leaving **$71.90**. Final verification must exercise retry-after-unknown, replay without redispatch, fatal success-without-journal, and ceiling enforcement. This is conditional methods approval based on the brief, not verification of the implementation.


