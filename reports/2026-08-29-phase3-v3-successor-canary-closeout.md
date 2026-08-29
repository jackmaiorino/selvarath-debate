# Phase-3 v3 successor canary close-out

Date: 2026-08-29. Run: `phase3-v3-82c8f75feba42a9e` (fifteenth and final identity of the v3 successor line). Archive: `E:/selvarath-archive/phase3-v3r15-clean-2026-08-29` (sealed read-only; full file manifest in `rejudge/phase3_v3_finalization_record_2026-08-29.json`, canonical sha256 `6c83954cd52f8eb341c1ce6162399d8cacf83ff41d745cd11af3668e861c34cd`).

## Verdict

The canary CONVERGED and PASSED. Completion label `PASS_WITH_FROZEN_EXCLUSIONS`: 525 of 528 expected rows, with 3 judgment cells terminally resolved under the amendment-12 replay-divergence disposition (cumulative bound used: 3 of 20). All frozen gates pass: strict-invalid (0 of 96 slots per judge, gate allows 1), structural mirroring (no problems), ledger (`PASS_CONSERVATIVE_UNCERTAIN`), main-spend (zero, as always). `formal_status: complete`, `gate_failures: []`. Report sha256 in the finalization record; result store `ce5264c5...`, ledger `cd057c85...`.

Qualification claim, per the 2026-08-29 Codex methods review (`rejudge/phase3_v3_codex_closeout_consult_2026-08-29.md`): this makes the project ELIGIBLE TO REQUEST FUNDING AND COMPLETE LAUNCH VALIDATION for a main run. It does NOT demonstrate main-execution readiness, because the observed missingness is content-correlated and judge-concentrated (next section). It is not main-run evidence for the budget-effect estimands, and no main-run call is authorized (see Non-claims).

## Scope disclosure: a two-judge canary

The pre-registered v3 design had four verdict judges. This run has two: `Qwen/Qwen3.8-2.4T-A95B` and `meta-llama/Llama-3.3-70B-Instruct-Turbo` (amendment 8, N=2 roster, Codex-ratified). Any main run inherits this scope: conclusions will be about these two judges, not a four-model panel. The two removals:

- `gemma-4` was removed from all verdict and checker roles after a per-prompt-deterministic thinking runaway: on live production queries it burned unbounded thinking tokens on 4.3% of calls (versus about 1% on the pilot probes used for admission screening). The screening blind spot is content-dependence: probe prompts did not contain the triggering content, so probe-based admission passed a model that fails on production traffic. Future admissions must screen on production-distribution inputs (the Llama checker admission below did exactly this).
- The fourth judge was removed earlier in the v3 line for verdict-budget runaways that role-limit caps could not bound safely (record trail in amendments 8 and prior incident records).

Codex review conditions on this scope: main-run claims must be stated per named model endpoint and version, with no generalization to judge scale or frontier models. Judge results are reported separately unless both judges use semantically equivalent parsing. The tolerant parser must be confirmed frozen, deterministic, and syntax-only (no substantive repair of answers) before Llama participates in any pooled primary analysis; otherwise Llama is secondary. This confirmation is a pre-main checklist item.

## Checker substitution

`gemma-4` was also the frozen query checker. Amendment 9 substituted Llama as checker at the frozen 16-token checker budget after a live-query screen: all 123 archived production checker requests (117 cache, 6 ledger, including all 6 known runaway triggers) were byte-recomposed, hash-verified, and replayed against Llama. Result: 0 malformed of 96 unique, all triggers clean. In production this run: 782 checker calls, zero truncations, zero malformed, across all four sequential conditions (b1: 56, b2: 109, b4: 215, b8: 402 calls).

Checker-origin disparity (amendment 9, pre-registered blocking rule): rejection rates 15.3% (Qwen-originated queries) vs 15.9% (Llama-originated), difference 0.5pp, Fisher exact two-sided p = 0.92. Interpretation NOT blocked (rule: blocked iff p < 0.05 and difference >= 10pp).

## Terminal cells: one question drives all the missingness

All three terminally resolved cells are the same question, VS-019, clean arm, Qwen judge:

| record | cell (prefix) | condition | disposition path |
|---|---|---|---|
| 006 | 49e892e5 | b2, replicate 0 | mechanical (allow-shape collision at old oracle row) |
| 007 | 7ba37609 | b2, replicate 1 | mechanical (reject-shape collision at old retry-query row) |
| 008 | f1441bda | b4, replicate 1 | orchestrator review fallback (multi-generation cache state) |

Mechanism (amendment 12): VS-019's prompts reproducibly drive the Qwen judge into degenerate generation, truncating at the 4,096 query cap or emitting special-token spam. Truncated and empty responses are refused memoization by frozen doctrine, so a mid-cell relaunch re-dispatches them; the provider completes them non-deterministically at temperature 0 with a fixed seed; the new path diverges from cached downstream rows and the cache-integrity guard halts the cell permanently (`CallReplayMismatch`). Cell f1441bda wedged the predecessor identity too, so the content-correlation replicates across independent runs.

### Question-clustered scenario analysis (Codex launch condition)

The crude rate looks alarming naively extrapolated: 3/528 canary rows scales to about 56/9,840 main slots, nearly three times the frozen 20-cell bound. But the cells are clustered, so the right unit is the question. Census of 4,096-cap query truncations (the necessary precondition): 3 of 24 canary questions triggered them for the Qwen judge (VS-019: 13 truncations, SEL-030: 4, CN-021: 1), zero for Llama. A terminal cell requires BOTH a truncation-prone prompt AND a mid-cell relaunch afterwards; only VS-019 crossed both, and only because this run was relaunched repeatedly.

At main scale each (question, judge, condition) has 12 slots. Exposure per VS-019-class question is about 48 sequential Qwen slots; at the canary's conditional terminal rate under relaunch-heavy operation (3 of 16 exposed VS-019 slots, about 19%), one such question yields roughly 9 terminal cells and two put the run at the frozen bound. With 1 to 3 truncation-prone questions per 24 observed, the main run's 82 questions plausibly contain several. Conclusion: disclosure plus the frozen INVALID and whole-mirror-unit analysis plan is NOT sufficient on its own; main-run completion is robust only if the divergence mechanism itself is removed. We do NOT propose pre-screening questions (outcome-correlated selection); the mitigation is mechanical (next paragraph).

Required pre-main mitigation (Codex-specified, to be frozen before any main launch): an immutable request-level journal. Persist every dispatched request key and raw response before downstream processing; never overwrite a committed response or regenerate a completed request on resume; resolve ambiguous dispatch completion as INVALID rather than redispatching; commit the assembled cell atomically once all request records exist. Rejected alternatives: memoize-first-successful-retry (selects among nondeterministic generations) and whole-cell single-dispatch (converts recoverable interruptions into exclusions). The mechanism must be validated on VS-019 plus ordinary controls with demonstrated crash/resume equivalence before freezing. This is harness validation, not a science change, but it touches EXECUTION_CODE_PATHS and therefore forces a fresh identity and its own review.

Supportable science observation (wording per Codex; the earlier scale-causal framing is withdrawn): the Qwen3.8 endpoint showed recurrent, question-specific degeneration on VS-019 under the tested configuration; the Llama-3.3-70B endpoint did not show that failure in observed attempts. No claim about model scale as the cause, and "deterministic" is wrong in the strict sense since redispatches of the same prompt sometimes completed cleanly.

## Diagnostics (descriptive, no gate consequence)

- Capability anchors (48 held-out per judge): Qwen 47/48 strict and tolerant (97.9%). Llama 47/48 tolerant (97.9%), 33/48 strict (68.8%); the strict-parse shortfall is format compliance, not knowledge, consistent with phase-2 behavior.
- Paired-position consistency (48 pairs per judge): Qwen 93.8%, Llama 89.6%.
- Capability slope: estimate-and-plot only per protocol, no p-value.
- Configuration-selection pace: not evaluable at canary close-out; the frozen eight-full-hour review-window requirement remains unmet and blocks main authorization until independently satisfied.

## Orchestration defects this cycle (owner transparency)

- r23: the drive-time terminal-exclusion filter indexed a ResolvedCell as a dict and crashed the driver. Fixed with a regression test. Wasted identity cost $4.21.
- r29: the amendment-9 checker substitution was never wired into the execution adapter, so every checker call targeted the removed gemma-4 config and failed pre-reservation. Two earlier halts were misattributed to provider weather, and a stale-evidence loop in the resume wrapper relaunched blindly 48 times. Fixed with adapter overrides, a 15-minute evidence-recency guard, three regression tests, and a live production-path probe. Wasted identity cost $4.21.
- Structural cost-model error: every earlier full-run estimate ($6 to $16) extrapolated from runs that died before the b4/b8 tail. True cost scales roughly linearly with query budget (see repricing below), so the tail dominates. This caused two budget re-asks before the final envelope.
- Two hand-typed-hash near-misses were caught before commit (an execution-binding carry off by one ulp, and a fabricated full sha extrapolated from a 12-character prefix in record 008's draft). Both are documented in the incident trail; all hashes in committed artifacts are computed programmatically.

## Spend

Final envelope (amendment 13, TRULY FINAL): $94.00 in-ledger aggregate cap. Position at convergence:

- Aggregate accounted: $84.38 (prior identities carry $64.83 + this run $19.56).
- This run: actual $16.31, uncertain $3.25 (of the $7.50 per-run ceiling), 5,188 ledger events, accounting label `PASS_CONSERVATIVE_UNCERTAIN` (uncertain spend counts fully against caps).
- Headroom retired unspent: $9.62. Main-run spend: $0.00 (never authorized).

Per-slot judgment economics observed (actual, success events): b0 $0.0124, b1 $0.0279, b2 $0.0414, b4 $0.0671, b8 $0.1179. Checker $0.0061 per sequential slot. Capability $0.0056 per slot.

## Main-run repricing (required before any main ask)

Frozen main inventory (protocol r6): 9,840 judgment slots = 984 per judge per condition x 2 judges x 5 conditions, question set identical to phase 2 (82 main questions).

At observed per-slot costs:

| component | slots | estimate |
|---|---|---|
| b0 judgments | 1,968 | $24.48 |
| b1 judgments | 1,968 | $54.83 |
| b2 judgments | 1,968 | $81.39 |
| b4 judgments | 1,968 | $132.02 |
| b8 judgments | 1,968 | $232.11 |
| query checker | 7,872 seq. slots | $48.22 |
| capability anchors | 96 (canary-scale assumption) | $0.54 |
| actual subtotal | | $573.60 |
| conservative-accounted (+19.9% observed uncertain overhead) | | about $688 |

Read: roughly $575 actual as a planning mean, about $688 conservative-accounted. Codex's review treats this as a mean, not a ceiling (48 observations per stratum are thin for tail costs, and slots within a question are not independent), and attaches a 25% execution reserve: RECOMMENDED CAP $875. A discarded late identity is a separate risk class that could approach another full run's cost; it is NOT absorbed by the reserve and requires explicit owner reauthorization if it happens. b4 plus b8 are 63% of judgment cost. The estimate is grounded in the full tail (all 48 b8 slots completed), unlike every earlier extrapolation, and excludes transcript generation (phase 3 reuses frozen transcripts, so $0) and capability-anchor expansion beyond canary scale. Against the roughly $7k grant remainder $875 is affordable but is the largest single spend of the project; it requires a fresh owner authorization and a dedicated Codex main-protocol consult, and remains blocked by the review-window requirement above.

## Qwen3.8 serverless viability

Qwen3.8-2.4T on Together serverless is viable as a judge with the frozen role-limit regime: 16,384 verdict budget (amendment schema v6) produced zero verdict truncations and zero invalid verdicts in 96 slots. Its failure mode is not capacity but recurrent content-triggered degeneration at the query stage (VS-019 and, less severely, SEL-030 and CN-021), plus provider non-determinism at temperature 0 with a fixed seed (the same prompt truncates on one dispatch and completes on another). Any protocol that memoizes calls must treat serverless Qwen replies as non-replayable; the request-level journal above is the required consequence.

## Together billing reconciliation (addendum pending)

Ledger-side totals ready for reconciliation: this run actual $16.31 plus uncertain $3.25 across 5,188 events under ledger id `3cea0e65...`; whole-phase actuals by identity are in each archive's usage ledger. The dashboard-side pull needs the owner's Together console access; the addendum will compare invoiced vs ledger-accounted totals and explain deltas (the uncertain component should upper-bound them).

## Non-claims

Verbatim from the sealed report: this successor canary is an engineering and eligibility gate, not main-run evidence for the budget-effect estimands; no main-run provider call is authorized or executed; configuration-selection pace is not estimable unless the frozen eight-full-hour review-window requirement is independently met.

## Artifacts

- Sealed archive with 18-file sha256 manifest: `rejudge/phase3_v3_finalization_record_2026-08-29.json`.
- Identity chain: protocol r6 `e366147d`, role limits r10 `bb545e80`, execution binding r15 `a2f65c9c`, price snapshot r11 `05c39181`, manifest r32, authorization r16 `0754d342`.
- Amendments this cycle: 8 `dfdff7a1`, 9 `ec73ee30`, 10 `e2edc996`, 11 `1e034b76`, 12 `541d8b64`, 13 `123dda75`.
- Terminal records: `phase3_v3_terminal_halts_006/007/008_2026-08-29.json`.
- Incident records: r21, r23, r25, r27, r29, r31.
- Outcome-blind cost checkpoints: `phase3_v3_cost_checkpoints.jsonl` (in archive; final forecast $18.58 vs actual accounted $19.56).

## What remains before the main-run ask

Per the Codex close-out review (all blocking):

1. Owner methods review of this close-out and the two-judge scope.
2. Request-level journal mitigation: built, validated on VS-019 plus ordinary controls with crash/resume equivalence demonstrated, then frozen (execution-code change, fresh identity).
3. The frozen eight-full-hour configuration-review windows (pace requirement), independently met and documented.
4. Tolerant-parser confirmation (frozen, deterministic, syntax-only) for the pooled-analysis question.
5. Dedicated Codex methods consult on the main protocol itself.
6. Owner spend authorization at the $875 recommended cap, with discarded-identity reauthorization explicit.
7. Together billing reconciliation addendum.

Codex signed: the formal canary gate result and a conditional main-run funding ask. Codex did not sign: an unconditional readiness claim, a $700 all-in budget, or the earlier scale-causal science wording (all corrected above).
