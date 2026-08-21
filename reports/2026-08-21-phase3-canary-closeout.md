# Phase-3 canary close-out

**Run:** 1,449 of 1,488 manifested canary cells settled (39 pending, never imputed), execution identity `5d7f6a56` (7th in the identity chain; binds `rejudge/phase3_manifest_2026-08-18.json` and protocol `615d1e1f`). **Blinded by design:** this close-out reports zero verdict-correctness or error-rate quantities for the debate-judgment cells. The only correctness-derived numbers below are the two pre-registered calibration gates (pass/fail only) and the capability-anchor scores, which are pre-outcome by construction (scored on a 24-question set disjoint from the 82 outcome questions). Derived by `scripts/phase3_canary_closeout.py` from the read-only archive at `E:/selvarath-archive/phase3-2026-08-18/`; machine-readable output at `rejudge/phase3_canary_closeout_2026-08-21.json`.

## Calibration gates (96 core-b0 judgments per judge)

| Judge | Invalid rate | Gate (\<2%) | Side bias | Gate (\<=10pp) |
|---|---|---|---|---|
| Qwen3.7-Max | 0.0% | pass | 1.2pp | pass |
| Llama-3.3-70B | 0.0% | pass | 10.8pp | **fail** |
| gemma-4-31B | 2.1% | **fail** | 7.0pp | pass |
| gpt-oss-120b | 6.3% | **fail** | 1.0pp | pass |
| gemma-3n-E4B | 0.0% | pass | 25.5pp | **fail** |
| Qwen2.5-7B | 0.0% | pass | 41.6pp | **fail** |

Only Qwen3.7-Max clears both gates. Three judges fail on side bias, two on the invalid rate; no judge fails both. The invalid-rate levels themselves are the reported quantity for this gate (invalid vs. valid is not a correctness judgment); side bias is reported strictly as an absolute pp difference and pass/fail per the blinding constraint, never as the underlying per-side error levels.

One real finding surfaced in building this table: the protocol's side-bias gate assumes a mirrored 48/48 A-correct/B-correct split ("K2 mirrored"). The executed pipeline assigns which position holds the correct answer as a function of `question_id` alone (`rejudge.config.position_for`, `randomize_ab_per_budget=False` for the clean arm), independent of the K2 side/replicate axis. Both replicates of a given (question, debater) therefore share one position label, and the realized split across this canary's 24 held-out questions is 13/11, not 12/12, giving 52/44 rows rather than 48/48. The gate is computed on the realized split exactly as worded; this is a design-vs-implementation gap worth the owner's attention before the same mechanism runs at main scale, not a defect in this close-out.

Given the gate failure rate (5 of 6 judges fail at least one gate), the roster as canaried does not clear the frozen launch gates. `roster.new_judge_failure_rule` drops a failing *new* judge without replacement; a failing *continuing* judge is a halt-and-escalate to the owner. All four continuing judges (Qwen2.5-7B, gemma-4-31B, Llama-3.3-70B, gpt-oss-120b) are among the failures, so this is squarely an owner decision, not one this close-out resolves.

## Capability anchors (48 held-out cells per judge, tolerant parse; strict-parse sensitivity)

| Judge | Tolerant | Strict | Zero spread? |
|---|---|---|---|
| Llama-3.3-70B | 48/48 | 35/48 | **yes (tolerant)** |
| Qwen3.7-Max | 47/48 | 47/48 | no |
| gemma-4-31B | 47/48 | 47/48 | no |
| gemma-3n-E4B | 45/48 | 45/48 | no |
| Qwen2.5-7B | 45/48 | 45/48 | no |
| gpt-oss-120b | 44/48 | 44/48 | no |

Llama-3.3-70B is a perfect tolerant score, flagged per protocol for zero spread; its capability-slope contribution will need the leverage diagnostic the frozen secondary already calls for. The same judge's strict score (35/48) is far below its tolerant score, the largest tolerant/strict gap in the roster, meaning it reliably answers correctly but rarely in the exact `ANSWER: A`/`ANSWER: B` (+ optional period) format; every other judge's tolerant and strict scores match exactly. Every anchor score was independently re-parsed from `raw_verdict_text` with the frozen parser rule and cross-checked against the archive's own `is_correct_tolerant`/`is_correct_strict` fields: 0 mismatches across all 288 cells.

## Review volume, dedup, and pace

1,279 unique-payload gate rulings settled across the canary (778 ALLOW, 500 REJECT, 1 malformed). All 1,279 are attributable to auto-resume review packets once packets are deduplicated by first-seen payload (a payload can be echoed in more than one packet without being a new ruling; naively summing packet line counts overcounts by roughly 5%, corrected in the script).

Dedup, measured on the allowed-query subset (the only subset whose claim text survives in the result store; rejected-attempt payloads are not recoverable from the archived stores, so cross-arm/cross-judge dedup among REJECTs is not derivable and is reported as a scope limit, not a zero): 865 unique payloads behind 1,673 allowed-query events, a 48.3% duplicate rate. Of the 808 duplicate reuses, 369 reused a payload first ruled under a different judge, 292 reused one first ruled under the same judge in a different arm, and 147 repeated within the same (judge, arm).

Realized rulings per quota-day (UTC calendar day of each review packet's commit): 128 (8/18, partial day), 713 (8/19), 438 (8/20, partial, ending in the terminal STOP). This calendar-day pace is well below phase-2's reference sustained pace (~3,000/day); the gap is incident downtime (STOPs, the two driver livelocks, the context-guard block), not a throughput ceiling, which is exactly why a clean review-pace crank test is on the remaining list below rather than something this close-out can settle.

## Configuration-selection verdict

Per `decisions.configuration_selection.projection_rule`, official projection (zero dedup credit, as pinned):

- **R_U = 69,248** against R_max 135,000: **pass**.
- **D_U = 163** against D_max 45: **fail** (L90 pace 426/quota-day).

Informational-only variant crediting the observed 48.3% dedup rate: R_U = 36,422, D_U = 86. D_U still fails even with dedup credit, so the pace conclusion is not an artifact of the zero-credit pin. R_U's comfortable pass and D_U's fail on the *same* data means the constraint that actually binds is throughput, not budget, on the current per-day cadence. Per `spend_cap_compliance`, this is the same ladder that governs the 450 USD stage cap: a pace failure this large should route through the same lexicographic degradation the protocol already specifies (shed tail replicates, then Qwen3.7-Max, then the 41-question subset) rather than be waved through on the strength of R_U's pass. The wide interval here (df=5 per smoke arm, t-critical 2.015) is expected from only 6 smoke questions per arm and is not itself evidence of a throughput problem; the crank test is what separates "genuinely slow" from "noisy small-sample estimate."

## Forecast (decisions.spend.forecast_method)

Projected stage total: accrued 19.78 USD + projected main-run 650.91 USD (with the 1.15 transport multiplier) = **670.69 USD**, against the 450 USD stage cap: **fail**. Per-judge projected main-run share: Qwen3.7-Max 278.76 (43%), Llama-3.3-70B 166.73, gemma-4-31B 100.53, Qwen2.5-7B 51.94, gpt-oss-120b 41.45, gemma-3n-E4B 11.49.

Qwen3.7-Max inclusion evaluation: projected total *with* Qwen3.7-Max is 650.91, over cap; projected total *without* it is 372.14, comfortably under. Shedding it is the natural next rung of the lexicographic ladder (`configuration_selection.selection_procedure` step 2) if this forecast holds, and it is the single largest lever available: removing 43% of the projected main-run cost from one candidate judge closes most of the gap by itself.

One caveat on method, not on the pinned formula: no offline per-model tokenizer is available in this environment (Llama/Qwen/Gemma each use a different tokenizer, none reliably loadable without a live download this task's read-only posture treats as unavailable), so every transcript-length token count here, canary and main alike, uses a chars/4 approximation rather than a provider-verified count. The exact frozen main-transcript *text* is used (492 transcripts, byte-identical to the frozen bundle, not extrapolated from the 6 smoke questions, per `base_context_rule`); only the text-to-token conversion is approximate. Everything else in the forecast, canary per-turn/oracle-block overhead, the U90 t-intervals, prices, the 1.15 multiplier, is exact archived data. Given the size of the cap breach (670 vs. 450, a 49% overage), this caveat does not change the qualitative conclusion.

## Spend to date

Settled 15.31 USD (7,351 terminal calls) + uncertain 4.42 USD (526 unknown-charge calls) + 3 open reservations (0.05 USD) = **19.78 USD total**, against the 60 USD canary cap. Computed from the usage ledger's attempt-id pairing (a `reserved` row is a provisional estimate superseded by its terminal `success`/`unknown_charge` row sharing the same `attempt_id`; summing every row's `cost_usd` naively would double the true total by roughly 2.4x on this ledger). No unexplained status combinations in 15,758 usage rows.

## Completion

1,449 / 1,488 manifested canary cells (97.4%), 39 pending, all `sequential_b8` judgment cells, none core-b0 or capability-anchor (both 100% complete). By judge: Qwen2.5-7B 23, gpt-oss-120b 12, gemma-3n-E4B 4. The orchestrator's own convergence log independently confirms 1,449/1,488 at the final halt, matching this derivation exactly. The 39 cells are marked PENDING and are not imputed; the terminal STOP (`abandoned_cell_rate`, not an enumerated transient reason) is a genuine provider-side degradation (503 Service unavailable, dominant on Qwen2.5-7B-Instruct-Turbo, also intermittently hitting gpt-oss-120b and gemma-3n-E4B-it), and the supervisor correctly refused to auto-resume through it.

## Incident narrative

The canary caught five distinct failure modes, each at a different control layer, before any of them reached main-run spend:

1. **A dead roster model**, twice. The provider-verification snapshot caught the protocol's original weak-Llama candidate (`Meta-Llama-3-8B-Instruct-Lite`) absent from the catalog under any spelling before any spend (amendment 1). Its substitute (`Meta-Llama-3.1-8B-Instruct-Turbo`) was catalog-listed but not serverless-accessible on this account; the transport/abandoned-call accounting caught that live, 83 abandoned calls with "Unable to access non-serverless model," and the slot was dropped entirely under the no-replacement rule (amendment 3), taking the roster to 6 judges.
2. **A driver livelock**, twice (2026-08-18T20:46Z, 2026-08-19T08:22Z). The 30-minute stall watchdog killed the process tree both times; no silent hang, no undetected spend.
3. **A supervisor/host-path mismatch**, surfaced as a stale code-provenance hash by the manifest's `ManifestValidationError` check before any relaunch spend (2026-08-19T15:58Z).
4. **A byte-vs-token guard conservatism**. Phase-2's context guard (prompt bytes + max_tokens against token ceilings, ~4x conservative for English text) blocked 100% of b8 cells for 3 of 6 judges on cells that, once unblocked, completed all 8 queries. A Codex-reviewed replacement (a context-only estimator, mechanically enforced visible-history caps, a validation gate replayed against 4,800+ live calls with zero misses and minimum headroom 67% relative) fixed it under amendment 4, independently reviewed before the successor manifest executed.
5. **A provider outage**, terminal. The supervisor's fail-closed benign-transient enumeration correctly refused to auto-resume through a genuine, non-enumerated halt reason (`abandoned_cell_rate`) rather than imputing the affected cells, leaving 39 sequential_b8 cells honestly PENDING.

Two smaller auto-resume amendments tightened the supervisor's corroboration requirements in flight (checker_malformed no longer needs transport-shaped evidence; Together's 400 "Input validation error" added to the enumerated transient set after 13 clean-request recurrences), both in-phase operational decisions under standing delegation, both recorded append-only.

Seven execution identities across the canary's life; every code or config change that could affect a call, a spend reservation, or a completion determination re-minted the identity and is traceable in `rejudge/phase3_canary_authorization_2026-08-18.json`'s `identity_history` field.

## What remains before the main-run ask

- **39 pending cells** (Qwen2.5-7B 23, gpt-oss-120b 12, gemma-3n-E4B 4, all sequential_b8): resolve or explicitly accept as permanently missing before main authorization.
- **Owner decision on the calibration gates**: 5 of 6 judges fail at least one gate, including all four continuing judges (a halt-and-escalate case per `new_judge_failure_rule`, not something this close-out or the no-replacement rule resolves).
- **Owner key rotation + provider reconciliation** (amendment 2's deferred hard blockers): not started; both are unconditional gates before any main-run authorization record can be written.
- **Main-run driver concurrency work**: two independent driver livelocks during the canary; the stall watchdog is a safety net, not a fix, for whatever is causing the hang at main-run scale (29,520 slots vs. the canary's 1,488).
- **A review-pace crank test**: the D_U pace gate fails under both the official and dedup-credited projections, but the realized 426/quota-day pace is measured over a run whose calendar days were dominated by STOP downtime, not sustained reviewing. A clean, uninterrupted pace measurement is needed to know whether D_U genuinely fails or whether this canary just never got a clean day to show its real throughput.
- **The forecast's 49% cap overage** and the gate failures both point toward the same lever (shedding Qwen3.7-Max, per `configuration_selection.selection_procedure` step 2), but that is the owner's call under the frozen lexicographic ladder, not a default this close-out applies.

## Artifacts

`rejudge/phase3_canary_closeout_2026-08-21.json` (full machine-readable output), `scripts/phase3_canary_closeout.py` (the derivation, deterministic and reproducible against the read-only archive), `tests/test_phase3_canary_closeout.py` (40 tests on synthetic fixtures, no archive dependency).
