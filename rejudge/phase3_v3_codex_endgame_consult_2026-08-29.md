# Codex consult: endgame ruling (2026-08-29)

One-shot, gpt-5.6-sol high, codex session 01a04d97-61b1-7041-a704-0cc750712a15, ephemeral read-only sandbox.

## Brief

```
One-shot consult, selvarath phase-3 fourteenth attempt (continuation of today's consults; your amendment-10/11 rulings executed). The run is 81% complete, every mechanism finally works, and TWO new facts force an endgame ruling. Answer with a ranked recommendation plus blockers.

GOOD NEWS FIRST: run aec7f6c00b8dde94 committed 426/528 rows. The Llama checker (your B path) is executing in production at scale: many parsed allow/reject verdicts, 2-token completions, ZERO checker_malformed, disparity telemetry accumulating. The r29 wiring fix held. Core b0 is 192/192 COMPLETE, b1 48/48, b2 41/48. Uncertain $1.42 of $7.50. All controls quiet.

FACT 1: REPLAY-DIVERGENCE WEDGE. One cell (f1441bda) is permanently stuck on CallReplayMismatch: its first judge query truncated at the 4,096 query cap (refused memoization by frozen doctrine), the gate retried and cached downstream calls (checker, oracle embedding the retry's claim); after a transport relaunch the provider NON-DETERMINISTICALLY un-truncated the same call (same prompt, same seed: 4,096-truncation became a clean 1,132-token claim), the path lawfully diverged, and the cached oracle row now mismatches forever. The integrity guard is doing its job; the cell is unfillable in this identity. EIGHT more uncommitted cells carry the same conjunction (unmemoized truncated call + cached downstream rows): each relaunch rolls dice on wedging up to 7 more. Disposing of wedged cells needs a terminal-reason extension (amendment 12, 'replay_divergence') with amendment-5-style bounds. Missingness correlation: partly content (truncation-prone prompts), partly operational (relaunch timing); 9 potential cells = 4.7% of b2-family cells, ~3.1% of mirror units (under the 4% bound, barely).

FACT 2: MY COST MODEL WAS STRUCTURALLY WRONG. All prior estimates extrapolated runs that died before the budget-smoke tail. The remaining 102 rows are 7 b2 + 47 b4 + 48 b8 cells; b4/b8 carry 4-8 full query+checker+oracle exchanges each plus 16,384-budget Qwen verdicts. Bottom-up from THIS run's observed per-cell costs (b0 mean $0.013, b2 mean $0.047, scaling ~linearly in query budget): finishing needs ~$13-16 more. Projection: ~$78-81 in-ledger + $5.24 screening = $83-86 total, against the owner's $68. The owner has extended twice already on my numbers.

RULE ON:
1. Amendment 12 (replay_divergence terminal disposition): legitimate analog of the checker_malformed disposition you shaped, or gate-weakening like the verdict-truncation exemption you rejected? The mechanism differs from both: orchestration-integrity casualty, deterministically unfillable, flagged by the cache guard, partially content-correlated.
2. If the owner funds the finish (~$18-20 more for margin): any blockers beyond amendment 12 + the divergence-risk disclosure? Should the b8 cells' unknown cost tail get a per-condition spend checkpoint (report-only) so the estimate error can't compound silently again?
3. If the owner stops at 426/528: what analysis is legitimately reportable? The frozen completion gate cannot pass. Core b0 (192/192) and b1 (48/48) are complete condition-strata; b2 is 85%. Can a clearly-labeled NON-PROTOCOL descriptive analysis of the sealed store accompany the operational report without laundering it as the pre-registered result, or is the honest position that no treatment-effect number of any kind gets reported?
4. Your overall ranking: fund-and-finish vs stop-and-report, given the science value of a converged 528-row canary (whose purpose was always to qualify the MAIN run, which at these observed b4/b8 costs would itself need repricing).
```

## Verdict

## Ranked recommendation

1. **Fund and finish under one final all-in ceiling**, suggested at **$88 inclusive of screening**. This is conditional on Amendment 12 and the blockers below.
2. **Stop now and report an incomplete protocol result**, with a separately labeled exploratory analysis of complete strata.
3. **Do not fund only part of the tail.** Paying for b4 without completing b8 still cannot qualify the MAIN run.

The remaining b4/b8 cells are disproportionately important. They are the regimes needed both for scientific qualification and for an honest MAIN-run cost model. Spending another roughly $15 to resolve that question is defensible. Sunk cost alone is not the reason.

## 1. Amendment 12

**Ratify Amendment 12.** This is a legitimate terminal disposition, not the verdict-truncation exemption previously rejected.

The distinction is:

- The verdict exemption would have reinterpreted an otherwise valid model output and enlarged the evaluable success set.
- `replay_divergence` identifies a unit with an internally inconsistent provenance graph. There is no valid observation to classify.
- The integrity guard proves invalidity. It does not prove that the resulting missingness is ignorable.

Amendment 12 should require all of the following:

- A mechanical predicate based only on call identity, response/provenance hashes, the cached dependency mismatch, and `CallReplayMismatch`. No semantic or treatment-result judgment.
- Disposal only after actual divergence. Do not preemptively discard the eight merely at-risk cells.
- Disposal of the entire matched or mirror unit needed to preserve pairing, not only the inconvenient row.
- No imputation, pass/reject classification, or reuse of the mismatched cached oracle.
- The existing Amendment 5 denominator and bounds, including cumulative terminal dispositions. Do not introduce a more favorable denominator.
- Exact counts by condition and arm, plus disclosure that truncation propensity makes the missingness partly content-correlated.
- A sealed list of the nine currently known affected or at-risk cell identities before continuation.
- No repeated replay attempts intended to obtain the version that happens to match the cache.
- If the cumulative bound is exceeded, the completion gate fails. No Amendment 13 to raise it.

Also, do not call the amended result â€œ528 observed rows.â€ It can be **528 terminally resolved rows**, with the valid-observation count reported separately.

## 2. Funded-finish blockers

The blockers are:

1. Amendment 12 must be frozen before further dispositions or outcome analysis.
2. The owner must authorize one final total ceiling, not another rolling estimate.
3. The exact divergence count must remain within the existing missingness bound.
4. Treatment balance and matched-seed requirements must still hold after whole-unit exclusions.
5. The final report must disclose Amendment 12, content-correlated missingness, and sensitivity or worst-case bounds.
6. The MAIN run must be explicitly repriced from observed b4/b8 costs. Canary completion does not preserve the old MAIN budget.

Add cost checkpoints, but make them operational and outcome-blind:

- cumulative spend and revised finish forecast at b2 completion;
- the same at b4 completion;
- an early b8 checkpoint after roughly 8 cells, then at b8 midpoint.

These checkpoints must not alter inclusion, analysis, or condition ordering. The final authorized ceiling remains the only administrative stop rule.

## 3. If stopped at 426/528

The formal result is: **completion gate failed, so the preregistered treatment-effect decision is unavailable and the canary cannot qualify MAIN.**

A separate non-protocol descriptive analysis is legitimate. Reporting no treatment-effect number at all would be unnecessarily restrictive because b0 and b1 are complete condition-strata and stopping was driven by cost, not observed effects.

Permissible reporting:

- b0 and b1 within-stratum paired effects using the originally specified contrast;
- descriptive bootstrap intervals, clearly labeled non-confirmatory;
- exact denominators and disposition counts;
- b2 missingness diagnostics and worst-case bounds.

Do not report:

- a pooled canary treatment effect;
- a preregistered win/loss decision;
- confirmatory p-values or gate language;
- an observed-case b2 estimate without prominent bounds;
- any claim that the analysis qualifies MAIN.

Seal the store and document the stop decision before computing these effects. Put them in an explicitly titled â€œExploratory, non-protocol analysisâ€ section separate from the operational report.

## Bottom line

**Fund and finish if $88 all-in is genuinely affordable and a repriced MAIN run remains a live possibility.** The mechanisms now work, the controls are quiet, and the remaining conditions contain most of the information needed for qualification and costing.

**Stop now if the revised MAIN economics already make MAIN impossible.** In that case, the canaryâ€™s confirmatory value has largely vanished, and the honest deliverable is an incomplete protocol report plus carefully fenced exploratory results.
