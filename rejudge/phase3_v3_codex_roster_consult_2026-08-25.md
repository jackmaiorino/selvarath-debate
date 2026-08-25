# Codex consult: gemma-3n delisting roster decision (2026-08-25)

One-shot consult, gpt-5.6-sol via codex-cli 0.149.0, run by the successor orchestrator session 3cf5f3a5 after the r11 halt. Brief and verdict verbatim.

## Brief

```
One-shot consult. Self-contained; no repo access needed. You are the standing
second-opinion oracle for Jack's debate-oversight control experiment (selvarath,
phase-3 v3 successor canary). I need a vetted recommendation menu for an OWNER
decision (roster change), which exceeds my delegated authority. Please answer with
numbered verdicts, blockers first.

TONIGHT'S EVENT
Together DELISTED google/gemma-3n-E4B-it from its serverless catalog mid-canary at
~21:20Z (errors escalated 500 -> 503 -> 400 'model_not_available ... create a
dedicated endpoint'; authenticated catalog capture at 21:24Z lacks the model,
count 278 -> 276 vs the 18:53Z capture that still had it; hash-pinned evidence
committed). I stopped the run at 21:23Z to avoid spending on an identity a roster
amendment voids: with one of four judges unfillable, the 1,008-row completion gate
is unreachable. State at stop: 614/1,008 rows, all 192 capability anchors complete
(including gemma-3n's 48), 422/768 judgments, harness had passed bit-identical,
$3.19 accounted this attempt, $8.17 aggregate of the $60 cap, uncertain $0.034
(includes one killed-in-flight reservation), $1.00 per-run uncertain ceiling never
tripped (the amendment-3 tolerance worked exactly as designed through two provider
degradation bursts).

ROSTER HISTORY (relevant)
Phase-3 v1 designed 7 judges; attrition so far: Llama-8B weak slot dropped (all
catalog variants non-serverless), Qwen2.5-7B-Turbo endpoint removed, gpt-oss-120b
failed the frozen strict-INVALID gate, Qwen3.7-Max replaced by Qwen3.5 (pricing),
Qwen3.5 replaced by Qwen3.8 (dedicated-endpoint-only). Roster before tonight:
google/gemma-4-31B-it, meta-llama/Llama-3.3-70B-Instruct-Turbo,
google/gemma-3n-E4B-it (the WEAK-judge anchor), Qwen/Qwen3.8-2.4T-A95B.
Capability slope is already estimate-and-plot only (no p-value) since roster < 6.
gemma-3n was the low-capability anchor; without a weak judge the slope loses its
bottom end entirely. The canary is an engineering/eligibility gate; the scientific
primary is the budget-knob contrast family, pooled over judges.

CANDIDATE PATHS (owner decides; rank and correct these)
A. Drop to a 3-judge roster (gemma-4-31B, Llama-3.3-70B, Qwen3.8). Cheapest and
   fastest; kills the weak-judge anchor; capability spread collapses to
   mid-vs-frontier; is the budget-knob primary still meaningfully interpretable?
B. Substitute google/gemma-4-E4B-it (appeared in the catalog where 3n-E4B was;
   same E4B form factor, newer family). Preserves a weak slot; family-correlation
   concern with gemma-4-31B already on the roster (two gemma-4 variants); needs
   serverless STARTED check, price, exact public tokenizer + provider chat
   template match, fresh protocol identity.
C. Substitute a small non-gemma-4 judge (e.g. google/gemma-3-4b-it or another
   small serverless chat model). Better family diversity for the slope; same
   materialization requirements; unknown INVALID-gate behavior (screened by the
   canary itself).
D. Pause the phase-3 canary family and reassess provider stability with Jack:
   three roster-relevant provider changes in 72 hours (Qwen3.5 dedicated-only,
   gemma-3n delisted, plus earlier removals) suggest catalog churn risk to the
   MAIN run itself; criteria for roster admission might need a stability
   requirement before more spend.

QUESTIONS
1. Rank A-D with reasons. Is a weak-judge slot load-bearing enough for the
   phase-3 science to justify B or C over A?
2. If B or C: exact admission gates you would freeze for the substitute before
   any spend (catalog/serverless/price/tokenizer/template/INVALID screening), and
   whether TWO substitutes should be pre-approved as an ordered fallback list to
   survive further churn without another owner round-trip.
3. Does the roster change require a new protocol identity with regenerated cell
   namespace (as the Qwen swaps did)? Any way the 192 completed capability anchor
   rows or the frozen 48 canary transcripts are affected? (Transcripts are fixed
   protocol inputs; anchors are per-judge measurements.)
4. Churn risk to the MAIN run: should the roster amendment add a pre-main
   re-verification step or a mid-run delisting contingency (e.g. pre-declared
   drop-to-N rule) so a delisting during the ~$220-450 main run does not void it?
5. Anything in tonight's stop decision or accounting treatment (killed-in-flight
   reservation booked as uncertain, $8.17 aggregate carry) you would correct
   before it is sealed into the successor chain?
6. Confirm: this decision is the owner's, and nothing here fits under in-phase
   operational delegation.
```

## Verdict

1. **Blocker: the current canary identity is finished.** It cannot resume after a roster change. A, B, or C requires a successor protocol identity and fresh cell namespace. The 614 rows establish neither canary success nor scientific failure; record the attempt as stopped because provider delisting made completion unreachable.

2. **Ranking: D, then C, then B, then A.** D is a required decision checkpoint, not the eventual roster.

   - **D first:** Three roster-relevant changes in 72 hours make another unqualified serverless roster unsafe for the main run. Keep this pause short and use it to ratify admission, fallback, and mid-run dropout rules.
   - **C second:** A genuinely non-Gemma small judge best preserves both capability range and family diversity. `google/gemma-3-4b-it` is only non-Gemma-4, not non-Gemma, so it does not fully deliver the stated diversity benefit.
   - **B third:** Plausible fallback, but E4B does not establish comparable weakness, tokenizer/template equivalence, or behavioral continuity with Gemma-3n. Two Gemma-4 models also overweight one family in a four-model average.
   - **A fourth:** Scientifically usable, but it changes the scope most. It becomes a fixed-roster result about three mid/frontier judges.

3. **The weak slot is not required for identifying the budget-knob contrast, but it is important to its scope.** With A, the primary remains meaningful if it is stated as the equal-weight, within-judge contrast for those three named judges. It cannot support claims about weak oversight, capability-wide behavior, or a general population of judges. The capability slope would become little more than a descriptive comparison over a narrow range.

   That is enough reason to prefer a valid C, or then B, over A when the extra cost is modest. It is not enough reason to admit an inadequately materialized model. â€œWeakâ€ must be established by the 48 anchors, not inferred from E4B or parameter count.

4. **Freeze these substitute gates before the first paid probe.** STARTED and INVALID checks themselves cost money, so they require a small owner-authorized screening allowance.

   - **Identity and availability:** Exact API model ID appears in the authenticated serverless catalog at two checks at least 24 hours apart, then again within 15 minutes of launch.
   - **Actual serverless execution:** From the production account and endpoint, a request receives a request ID or STARTED state and reaches successful completion. Any dedicated-endpoint requirement or `model_not_available` response fails admission.
   - **Price:** Capture authenticated input/output prices and require  
     `confirmed aggregate + uncertain upper bounds + worst-case screening + worst-case successor canary <= $60`.  
     If $8.17 excludes the $0.034 uncertainty, remaining maximum exposure is $51.796.
   - **Tokenizer:** Pin the exact public repository revision, tokenizer files, special-token configuration, library version, and hashes.
   - **Chat template:** Pin the public template and test every distinct message shape used by the protocol. Provider prompt-token counts must exactly match locally rendered counts. Token-count agreement is evidence of operational equivalence, not proof of the providerâ€™s internal template. If exact internal identity is a hard protocol premise, require provider documentation or a raw preformatted-prompt path; otherwise the candidate is blocked.
   - **INVALID behavior:** Reuse the existing frozen parser, decoding configuration, screening cells, and integer threshold byte-for-byte, with no repair or selective retry. The numerical threshold was not supplied here, so I would not invent a replacement. Apply it to the same defined screen and then to all 192 substitute judgment cells before main admission.
   - **Weak-slot qualification:** The substituteâ€™s 48-anchor point estimate must be below every retained judge. If not, it may only proceed as a four-model nonweak roster under a further owner amendment.
   - **Harness:** If roster materialization changes executable configuration, repeat the single one-seed bit-identical store check.

5. **Preapprove two ordered substitutes, but as separate identities.** I recommend:

   1. One exact, genuinely non-Gemma small serverless model that passes every gate.
   2. `google/gemma-4-E4B-it`, conditional on the same gates.

   Preapproval must name exact IDs, price ceilings, screening allowance, and order. It can authorize moving to the next candidate before formal measurement without another owner round-trip. It must not authorize replacing a judge under the same protocol identity or swapping models after the main measurement begins.

6. **Yes, every roster option requires a new protocol identity and namespace.** That includes A because both roster and estimand change.

   - The frozen 48 transcripts are roster-independent protocol inputs. Preserve and reference their existing hashes; do not regenerate them.
   - Gemma-3nâ€™s 48 anchor rows remain valid predecessor evidence but cannot count for its replacement.
   - The 144 anchor rows for unchanged judges can be carried forward only through an explicit provenance-preserving equivalence rule: identical request bytes, model IDs, decoding, and anchor protocol. Reference their original row IDs and hashes rather than relabeling them as newly executed.
   - The same principle could apply to completed judgments from retained judges, but this must be explicitly authorized. Given the prior regenerated-namespace precedent, the default is that the 422 rows do not count toward successor completion unless the amendment creates that carry-forward rule.

7. **Add both pre-main verification and a mid-run contingency.** Pre-main checking alone cannot protect a $220 to $450 run.

   - Repeat authenticated catalog, live serverless completion, identity, and price checks at T-24h and immediately before formal launch.
   - Schedule work in complete paired blocks containing every budget condition and permuted control needed for the frozen contrast.
   - Do not substitute a new model during formal measurement.
   - Predeclare separate integer win-count and bootstrap gates for N=4 and contingency N=3.
   - If a judge disappears before completing its required blocks, drop that entire judge from the contingency analysis rather than using an opportunistic partial sample.
   - Continue only with at least three eligible start-roster judges. Below three, stop.
   - If the lost judge is the weak judge, the N=3 result remains a valid fixed-roster budget contrast but carries an explicit no-claim about weak-judge behavior or capability-wide effects.

8. **Tonightâ€™s stop and accounting were correct, with one bookkeeping clarification.** Stopping prevented spend on an identity that could no longer satisfy its completion gate. Keeping the killed-in-flight reservation as uncertain is appropriately conservative until the provider ledger resolves it. Carry all predecessor spend against the $60 aggregate cap.

   State explicitly whether $8.17 includes the $0.034. If it excludes it, current cap exposure is $8.204; if it includes it, do not add the uncertainty twice. Seal the event as an external eligibility loss, not an INVALID failure or scientific result.

9. **Authority verdict:** Yes, the roster choice, successor identity, ordered fallbacks, carry-forward rule, changed estimand, contingency gates, and renewed spending authority belong to the owner. Operational delegation covered stopping an unreachable run, preserving evidence, and reconciling charges. It does not cover resuming under A, B, C, or a flexible fallback roster.
