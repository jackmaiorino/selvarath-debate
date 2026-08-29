# Phase-3 v3 successor canary close-out

Date: 2026-08-29. Run: `phase3-v3-82c8f75feba42a9e` (fifteenth and final identity of the v3 successor line). Archive: `E:/selvarath-archive/phase3-v3r15-clean-2026-08-29` (sealed read-only; full file manifest in `rejudge/phase3_v3_finalization_record_2026-08-29.json`, canonical sha256 `6c83954cd52f8eb341c1ce6162399d8cacf83ff41d745cd11af3668e861c34cd`).

## Verdict

The canary CONVERGED and PASSED. Completion label `PASS_WITH_FROZEN_EXCLUSIONS`: 525 of 528 expected rows, with 3 judgment cells terminally resolved under the amendment-12 replay-divergence disposition (cumulative bound used: 3 of 20). All frozen gates pass: strict-invalid (0 of 96 slots per judge, gate allows 1), structural mirroring (no problems), ledger (`PASS_CONSERVATIVE_UNCERTAIN`), main-spend (zero, as always). `formal_status: complete`, `gate_failures: []`. Report sha256 in the finalization record; result store `ce5264c5...`, ledger `cd057c85...`.

This qualifies the engineering pipeline for a main-run ask. It is not main-run evidence for the budget-effect estimands, and no main-run call is authorized (see Non-claims).

## Scope disclosure: a two-judge canary

The pre-registered v3 design had four verdict judges. This run has two: `Qwen/Qwen3.8-2.4T-A95B` and `meta-llama/Llama-3.3-70B-Instruct-Turbo` (amendment 8, N=2 roster, Codex-ratified). Any main run inherits this scope: conclusions will be about these two judges, not a four-model panel. The two removals:

- `gemma-4` was removed from all verdict and checker roles after a per-prompt-deterministic thinking runaway: on live production queries it burned unbounded thinking tokens on 4.3% of calls (versus about 1% on the pilot probes used for admission screening). The screening blind spot is content-dependence: probe prompts did not contain the triggering content, so probe-based admission passed a model that fails on production traffic. Future admissions must screen on production-distribution inputs (the Llama checker admission below did exactly this).
- The fourth judge was removed earlier in the v3 line for verdict-budget runaways that role-limit caps could not bound safely (record trail in amendments 8 and prior incident records).

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

Consequences for a main run: missingness of this type is content-correlated, not random, and concentrates in one judge and (here) one question. The frozen bounds (20 cumulative terminal cells, 4% mirror units, concentration) held with large margin in the canary, but the main run has 82 questions and 9,840 slots; if even two or three questions behave like VS-019, terminal counts scale by roughly 41x per affected cell type. The main-run ask should present this as the principal execution risk, and the analysis plan already treats such cells as INVALID with whole-mirror-unit exclusion from paired analyses.

Also banked as a science observation: a frontier-scale judge (Qwen3.8, 2.4T) exhibits question-specific deterministic degeneration that a 70B model does not, on identical prompts.

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

Read: roughly $575 actual, budget about $700 under the same conservative uncertain-spend accounting this phase used. b4 plus b8 are 63% of judgment cost. This estimate is grounded in the full tail (all 48 b8 slots completed), unlike every earlier extrapolation. It excludes: transcript generation (phase 3 reuses frozen transcripts, so $0), reruns of wasted identities (canary history suggests budgeting a contingency), and any capability-anchor expansion beyond canary scale. Against the roughly $7k grant remainder this is affordable but is the largest single spend of the project; it requires a fresh owner authorization and a Codex methods consult, and remains blocked by the review-window requirement above.

## Qwen3.8 serverless viability

Qwen3.8-2.4T on Together serverless is viable as a judge with the frozen role-limit regime: 16,384 verdict budget (amendment schema v6) produced zero verdict truncations and zero invalid verdicts in 96 slots. Its failure mode is not capacity but content-triggered degeneration (VS-019 above) at the query stage, plus provider non-determinism at temperature 0 with a fixed seed (same prompt truncates then completes on re-dispatch). Any protocol that memoizes calls must treat serverless Qwen replies as non-replayable and plan dispositions accordingly.

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

1. Owner methods review of this close-out and the two-judge scope.
2. The frozen eight-full-hour configuration-review windows (pace requirement), independently met and documented.
3. Codex methods consult on the main-run ask with this repricing.
4. Owner spend authorization (about $700 conservative) plus a contingency policy for VS-019-class content-driven terminal cells at main scale.
5. Together billing reconciliation addendum.
