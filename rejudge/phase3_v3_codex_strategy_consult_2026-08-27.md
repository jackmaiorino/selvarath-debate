# Codex consult: canary completion strategy (2026-08-27)

One-shot consult, gpt-5.6-sol via codex-cli 0.149.0, run by the successor orchestrator session 3cf5f3a5 after the r19 stop. Brief and verdict verbatim.

## Brief

```
One-shot consult. Self-contained; same selvarath phase-3 v3 canary. This is now a
STRATEGY question, not operations: eight run identities have died to environmental
and control-plane causes, $30.53 of the $60 aggregate cap is accounted, and each
fresh attempt costs ~$5-6 with no row carry. Two more failures exhaust the cap.
The owner wakes in ~2h; I need a vetted recommendation menu. Blockers first.

WEEK'S CAUSAL LEDGER (all fail-closed, zero data contamination, full audit trail)
1. r5: Qwen3.5-397B delisted to dedicated-only mid-run.
2. r7: Qwen3.8 stream omitted usage; fixed via non-streaming.
3. r9: zero-uncertain validator reused mid-run (control-plane bug); fixed by the
   $1 tolerance (amendment 3).
4. r11: gemma-3n DELISTED mid-run; owner substituted Qwen3.5-9B (amendment 4);
   weakness qualification later passed cleanly (0.708 vs 0.979/0.979/1.000).
5. r13: gemma-4 checker thinking-truncation; disposition machinery built
   (amendment 5, never yet needed in later runs).
6. r15: $1 ceiling filled by a Qwen3.8 timeout burst; owner raised to $2.50
   (amendment 6).
7. r17: $2.50 filled by a sustained EVENING capacity crunch on Qwen3.8 (instant
   429/503 on large requests); owner chose an off-peak retry.
8. r19 (today): off-peak retry ran WELL (568/1,008 rows, 91% Qwen3.8 success,
   review waves clean) but the ~4/h timeout drip still filled $2.20 of $2.50 in
   3.5h. I stopped below threshold hoping to resume in a calmer window; the stop
   caught an in-flight call, and the open-reservation rule makes the identity
   unresumable. Lesson recorded: external stops are identity-terminal.

ATTRIBUTION: Qwen/Qwen3.8-2.4T-A95B is the dominant instability (three distinct
failure shapes) AND the dominant uncertain-spend driver (~$0.12 reservations vs
~$0.02 for others). Every other component now works: harness gates pass
bit-identically every time, review waves commit clean, the substitute weak judge
qualified, checker truncation has not recurred.

WEATHER DATA: 2026-08-26 08:24-16:01Z: 2 Qwen3.8 timeouts in 5h. 26th evening:
crunch. 27th 06:00-09:35Z: ~4 timeouts/h steady. Windows are unpredictable at the
several-hour scale a full run needs.

BUDGET: $30.53 accounted of $60 ($26.0 actual + $4.5 uncertain-reserved; billing
reconciliation of the ~40 timed-out/rejected calls will likely show most uncertain
was never charged, but sealed accounting never nets that down). A complete run
needs ~$5.5-6.5 actual. Remaining headroom: ~$29.5 = about 4-5 attempts at
today's burn, or 2 attempts at evening burn.

OPTIONS
A. Calm-window retry, same policies: launch gated on shaped probes in yesterday's
   demonstrated-calm band (~12:00-16:01Z). If calm holds, done for ~$6. If not,
   ~$5 more sunk and we are at $36+.
B. Ceiling raise to $5 for the next successor (owner amendment): tolerates ~40
   Qwen3.8 timeouts; at today's 91% success rate the run converges with high
   probability even in a drip; still fully counted against the $60 cap. Risk:
   normalizes ballooning uncertain; but reconciliation likely recovers most.
C. Drop Qwen3.8 for this CANARY under the spirit of the pre-declared main-run
   delisting contingency (owner amendment, roster {gemma-4-31B, Llama-3.3-70B,
   Qwen3.5-9B}, N=3): removes the sole unstable component AND the expensive
   reservations; keeps the weak-judge anchor and a 0.71/0.98/1.00 capability
   spread; cheap (~$3-4) and near-certain to complete. Cost: loses the frontier
   MoE judge; fixed-three-judge scope caveats (your r11-consult language); the
   canary would no longer exercise the 4-judge configuration the main run
   nominally plans, so the main-run roster question comes forward: is the main
   run even viable on serverless Qwen3.8 given this week?
D. A+B combined: calm-window launch AND $5 ceiling. Highest completion
   probability without roster surgery (~$6-7 expected).
E. Stop: take the roster and provider questions to a full owner review before
   any more spend.

MY LEAN: D for the canary completion, with C explicitly pre-authorized as the
in-run fallback if D's identity ALSO dies (no further owner round-trip), plus a
mandate that the gates report carry a main-run-viability section on Qwen3.8
serverless stability (this week's evidence suggests the MAIN run, 34k+ slots over
days, cannot ride on serverless Qwen3.8 without either dedicated capacity,
generous uncertain budgeting, or the N=3 contingency).

QUESTIONS
1. Rank A-E (or propose better). Is pre-authorizing C as D's fallback sound, or
   does a roster change always deserve its own owner decision at the moment it
   triggers?
2. If B/D: ratify $5 or correct it; same flat-ceiling logic as before?
3. For the main-run-viability section: what evidence and framing should the
   canary report carry about Qwen3.8 serverless stability, without overclaiming
   from one week of weather?
4. Anything about the stop-is-identity-terminal lesson that should become frozen
   policy (e.g., orchestrator stops only between wrapper cycles)?
5. Confirm what needs the owner: I believe B/D ceiling raise, and C roster change,
   both do; A does not (same policies, timing choice was already his).
```

## Verdict

Recommendation: a modified D followed by a conditionally authorized C successor is defensible, provided the owner explicitly values one final attempt at the nominal four-judge canary. C must not be represented as an in-run rescue or as completing the same canary.

## Blockers

1. The menu mixes actual cost with sealed accounted cost. At the r19 rate:

   - Projected runtime: `3.5h Ã— 1008 / 568 â‰ˆ 6.2h`
   - Projected uncertain reservations: `$2.20 Ã— 1008 / 568 â‰ˆ $3.90`
   - Projected accounted increment: roughly `$5.5-6.5 actual + $3.9 uncertain = $9.4-10.4`

   Therefore D is only a `$6-7` accounted run if the genuinely calm regime persists. At the full $5 ceiling, its maximum is approximately `$10.5-11.5` using your actual-cost estimate. The owner should see expected actual, expected accounted, and maximum accounted separately.

2. The proposed 12:00-16:01Z band is shorter than the approximately 6.2-hour projected run. A probe can screen out an active 429/503 crunch, but cannot establish that the whole run will remain calm. Remove â€œhigh probabilityâ€ from the recommendation.

3. C changes the roster, estimand, and readiness claim. It must use a fresh identity and manifest, with no row carry or pooling.

4. Even a successful D does not qualify unchanged serverless Qwen3.8 for a 34k-plus, multi-day main run. It establishes one selected-window canary completion only.

## Ranking

For the stated goal of giving the nominal four-judge canary one final chance:

1. **D, modified as below**
2. **C**
3. **E**
4. **A**
5. **B**

B is dominated by D because it accepts the same $5 exposure without the crunch screen. A is poorly calibrated to the r19 drip, which projects beyond the existing $2.50 ceiling.

If the owner already believes N=3 is the likely main-run configuration, move C to first place and skip D. Spending another approximately $10 accounted merely to complete a configuration that will not be used would have little strategic value.

## Conditional C authorization

Preauthorization is sound. A roster change always requires an owner decision, but that decision can be made conditionally in advance. It does not need another owner interaction at trigger time.

The authorization should specify:

- C is a new N=3 successor identity, not an in-run fallback.
- It triggers only on an objective Qwen3.8 event, such as delisting, dedicated-only conversion, or the $5 ceiling being reached by Qwen3.8-tagged reservations.
- It does not trigger for an external stop, host failure, controller defect, ambiguous cause, or another judge failing.
- D and C are reported separately, with no pooled rows or shared completion claim.
- Only one C attempt is authorized. Any C failure returns to the owner.
- The decision tree has one combined maximum accounted-spend authorization.

Using your estimates, D could account for up to about $11.5. If C costs $4 actual and retains the existing $2.50 uncertainty ceiling, the combined worst envelope is about $18, taking the ledger from $30.53 to about $48.53. Replace those estimates with manifest-derived maxima before authorization.

## The $5 ceiling

I ratify $5 as a flat uncertain-reservation ceiling for one D identity.

It is empirically reasonable because the r19-like linear projection is approximately $3.9, leaving about $1.1 or 28 percent margin. At roughly $0.12 per Qwen3.8 reservation, $5 covers about 41 reservations.

The defensible claim is:

> The $5 ceiling is sufficient under an r19-like stationary drip by linear projection.

It is not evidence of high completion probability across weather regimes. A correlated crunch can still consume $5 without useful convergence. I would not raise it further within that identity.

The ceiling should be enforced prospectively: reserve before dispatch, refuse a dispatch that would breach either the per-identity or $60 aggregate cap, then drain to a safe boundary. It should never be enforced by killing an in-flight request.

## Main-run viability section

The report should include:

- Exact Qwen3.8 request counts and denominators by UTC window, not only rows completed.
- Successes, timeouts, 429s, 503s, latency distribution, retry count, and uncertain dollars per hour and per completed Qwen3.8 result.
- Request shape, timeout, concurrency, route, and non-streaming configuration.
- Contemporaneous results for the other judges.
- Separate causal categories:
  - r7 was a resolved protocol/integration issue.
  - r15 and r17 are endpoint availability evidence.
  - r19 contains endpoint drip evidence, but its terminal cause was the external stop.
- Explicit treatment of r19 as censored at 568/1,008 rows.
- Calm, drip, and crunch scenarios projected onto the planned number of main-run Qwen3.8 calls. Do not model failures as independent or derive a general uptime percentage from these windows.

Recommended conclusion:

> Under the tested request shape, the Qwen3.8 serverless route showed materially regime-dependent availability. These observations do not estimate a general service SLA or prove that serverless Qwen3.8 cannot complete. They do show that the unchanged serverless route is not yet qualified for a multi-day main run under the current no-row-carry, identity-terminal, and uncertainty-budget contract. Before main-run launch, the owner must choose dedicated capacity, a separately budgeted serverless design, or the N=3 contingency.

## Stop policy

â€œOnly between wrapper cyclesâ€ is necessary but insufficient. Freeze this:

> An external stop is identity-terminal. A planned pause is resumable only after the wrapper records a quiescent checkpoint with no active request, no open reservation, all responses and dispositions durably committed, and the next cycle boundary recorded. Stop intent is latched and honored at that boundary. Any kill, host shutdown, ambiguous checkpoint, or inability to drain forbids resume.

Also require enough uninterrupted operator and orchestrator availability for the conservative run duration before launching.

## Owner authority

Your authority assessment is mostly correct:

- **A:** No new amendment, assuming the prior authorization covered successor retries and off-peak timing, not one specifically named identity.
- **B/D:** Owner required for the $5 ceiling and combined spend envelope.
- **C:** Owner required for the roster and scope change. Conditional authorization now is sufficient.
- **D followed by C:** Owner must approve the complete decision tree, trigger, separate claims, attempt count, and combined maximum.
- **Main run:** Owner decision required before using serverless Qwen3.8, dedicated capacity, or N=3.
- **Frozen cross-run stop policy:** Owner ratification is appropriate because it changes standing resume behavior, although treating the already-stopped r19 identity as terminal needs no further approval.
