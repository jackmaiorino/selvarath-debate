# DRAFT — Stage 1 Results and Phase 2 Pre-Launch Update (Not Posted)

**Status:** local draft only. Post after the design/cost package is committed publicly and before
the first paid call from the approved 23,200-cell plan. Replace every placeholder with an immutable
commit URL.

## What progress have you made since your last update?

The validation run from my last update is complete. We re-judged the same 318 pilot transcripts
under six controlled arms: 11,330 judgments for $182.16.

With the oracle and judging bugs fixed, a few oracle calls still increased judge error by 3.4
percentage points (95% CI [1.3, 5.9]), about half the original 7.2-point estimate. Bug replay
indicates that the two oracle-channel bugs contributed roughly half the original effect.
Information-free placebo turns caused a smaller 1.6-point degradation, and the pilot's apparent
recovery at larger oracle budgets did not reproduce.

Under the frozen gates, the original >=4-point primary gate was indeterminate, not passed. The bug
attribution gate technically fired by 0.01 points, while the amendment frozen before the K=3 data
passed and licensed only a reduced-scope Phase 2 pilot. The design below is that reduced-scope
follow-up, not an unqualified launch of the originally proposed full grid.

One distinction matters: Stage 1 cleanly re-judged the legacy transcripts, but it did not repair a
separate debate-generation problem in which each debater had advance knowledge of the opponent's
upcoming case. Phase 2 therefore regenerates every debate with blind opening turns.

Follow-up mechanism work also changed our interpretation. Replaying the same query/answer evidence
as a neutral table in fresh context removed a large share of the harm. This points to conversational
presentation, turn structure, or commitment to earlier questions as part of the mechanism, rather
than bad factual content alone.

Held-out calibration selected blind, uncapped, three-round debate. The selected roster has judges
Qwen2.5-7B, Gemma-4-31B, Llama-3.3-70B, and GPT-OSS-120B; the debaters are Llama-3.3-70B and hosted
Qwen3.7-Plus; the oracle is Llama-3.3-70B. The approved offline design contains 23,200 Phase 2
cells: a 1,060-cell capability preflight followed, if the later gates pass, by 22,140 post-canary
main cells. It includes a full cap-protection interaction, an empty-evidence diagnostic, and two
full-document gold-context anchors. No calls from this approved 23,200-cell plan have begun.

Total project spend remains approximately $208. Across two transfers, I have moved $1,800 of the
grant into prepaid Together API credit ($500 earlier and $1,300 now). Subtracting reported spend
gives a simple estimated current credit of approximately $1,592, but I will reconcile that against
the provider dashboard before another call. Transfers are funding, not experiment spend.

- Stage 1 protocol: [frozen protocol](https://github.com/jackmaiorino/selvarath-debate/blob/5493864296b0c63dba595d08563f4bd2ad7f1f31/docs/rejudge-protocol.md)
- Stage 1 results: [validation report](https://github.com/jackmaiorino/selvarath-debate/blob/360605a51bb4b7ea0e0269a68ee8d9260708a452/reports/2026-07-09-stage1-rejudge-results.md)
- Mechanism memo: [mechanism and packaging memo](https://github.com/jackmaiorino/selvarath-debate/blob/e3122e607d9bd1104f86ccbe9b64ca08d64edc45/reports/2026-07-12-mechanism-and-packaging-memo.md)
- Calibration report: [held-out calibration results](https://github.com/jackmaiorino/selvarath-debate/blob/0fbadcec63eadab201a562914f7269065e925117/reports/2026-07-14-calibration-results.md)

## What are your next steps?

Before examining any Phase 2 debate outcomes, we will finish the human resolvability audit, validate the
query checker, and freeze/hash the prompts, model settings, primary tests, exact inventory, retry
rules, provider reconciliation, and spending controls.

After this update, we will separately recover or waive 11 failed Gemma calibration cells (proposed
recovery cap $2). A separately authorized capability preflight (five models x 106 questions x two
mirrored answers; proposed cap $15) will then select the second full-document anchor without
examining Phase 2 debate outcomes. Only after that will we separately authorize a small manifested
canary on questions excluded from the main analysis. We will review validity and parsing, side
bias, provider reliability, artifact completeness, resumability, and ledger-versus-provider cost
before separately authorizing the main run. Any failed gate stops the experiment before main spend.

The current provisional empirical planning band is $650–$1,150 in incremental Phase 2 spend, with
a $1,200 operational working budget and an immutable $1,500 incremental hard ceiling. We will
replace the provisional band with frozen prompt/token profiles and current provider prices before
authorizing paid work. The ceiling is a fail-closed boundary, not expected spending. We will post
results, failures, artifacts, and actual spend either way.

- Frozen Phase 2 protocol and cost model: `[IMMUTABLE_PHASE2_PACKAGE_URL]`

## Is there anything others could help you with?

Methods scrutiny before the canary—especially the H/P/R decomposition, capability measurement,
query-screen validation, clean-versus-placebo comparison, and stopping rules—would be valuable.
Pointers to related work on verification interfaces, conversational presentation effects, or
deliberation-induced degradation are also welcome.
