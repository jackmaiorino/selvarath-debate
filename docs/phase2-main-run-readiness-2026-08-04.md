# Main-run readiness plan

Supersedes nothing. Records what the 2026-08-04 Codex consult changed about the path from a
converged canary to an authorized main run, and enumerates what must be true before the
main-run manifest is frozen.

Inputs: `docs/phase2-canary-review-2026-08-04.md` (gate 12 review), the canary close-out
`rejudge/phase2_canary_completion_2026-08-04.json`, and the polarity fixes at commit
`9cd39f3`.

## Owner decisions taken

- **2026-08-04: the main-run plan is approved in principle** (23,200 cells, the H = P + R
  decomposition, ~$162 of provider spend against the $1,500 ceiling).
- **2026-08-04: a full 945-cell bridge canary is approved** on the corrected, concurrent
  code, under a fresh provenance and cache namespace. Binding spend authorization still
  attaches to its manifest hash, not to this document.

## Why a bridge canary at all

The 2026-07-28 canary validated a **defective, serial** implementation. The main run would
use a **corrected, concurrent** one. Two of the three things that would be under test in the
main run (the fixed A/B polarity, and concurrent execution against shared stores) have never
run live. The canary costs about $7, so declining to repeat it cannot be justified on cost,
and repeating it measures the reviewer bottleneck as a side effect.

## Blocking work before the bridge canary

### 1. Concurrency that is science-neutral, not merely fast

An append lock is insufficient. Each of the following can change results rather than only the
schedule:

- **Concurrent gate miss.** Two workers find no committed ruling for one payload hash, both
  request review, and produce two rulings where the store admits one. Needs durable
  `absent -> reserved -> committed` singleflight, one immutable ruling, waiters inheriting it,
  and a conflicting commit treated as fatal.
- **Call-cache miss race.** The same shape one layer down. Duplicate provider calls can
  return different content despite equal seeds. Same singleflight semantics, keyed on the
  exact request identity.
- **Store append race.** The lock must span tail read, event construction, append, flush, and
  durable visibility. A thread lock does not exclude a second runner process, so the archive
  lease stays mandatory.
- **Gate key completeness.** Inheritance by hash is only sound if the key covers every
  decision-relevant input. An incomplete key means first arrival decides which context's
  ruling everyone inherits.
- **Within-cell ordering.** The second query stays downstream of the first gate decision and
  oracle answer. The two interactive rounds are never parallelized.
- **Stale rulings.** Decisions made under defect B were rendered against swapped candidate
  labels. The bridge canary uses a fresh decision namespace rather than inheriting them.

### 2. Deterministic blocked dispatch

Batch cells depend on their sequential parents, so batch necessarily executes later. A
work-conserving ready queue therefore correlates time with condition, and any time-varying
provider degradation lands preferentially on one of the two co-primary components. Dispatch
in small deterministic blocks with a bounded parent-to-batch lag.

### 3. Per-provider concurrency caps, chosen by measurement

Measured from the canary ledger, gemma-4-31B is **95% of all call time**: 12.80 h of the
13.69 h of successful calls, and 9.06 h of the 9.09 h burned on abandoned ones. The
`query_checker` role alone is 9.95 h of 13.69 h. gemma serves both the frozen checker and one
judge role, and abandoned 189 of 1,702 calls at concurrency **one**.

So a global cap is the wrong instrument. Cap per provider quota domain, share one limiter
between gemma's checker and judge traffic, and reserve checker capacity so judge traffic
cannot starve every gated condition. Choose the caps with a stepped 1/2/4/8 preflight per
provider rather than assuming 32 works.

### 4. A missing-data policy, frozen before launch

Verdict parse failures, malformed gate outputs, exhausted retries, failed parents, and
dependent cells all need their disposition fixed in advance. Deciding after the fact makes
exclusions condition- and model-dependent, which is the same confound as (2) by another
route. The canary's own five excluded cells are the worked example.

### 5. H = P + R must hold on identical support

The identity is only meaningful if all three estimands are computed on the same weighting and
the same cell support inside **every** bootstrap replicate. A batch parent that fails must not
silently give P a different population from R.

## Empirical checks owed

- **Defect B blast radius.** The frozen contract's four clauses are symmetric under swapping
  the two candidates: P1 prohibits the query naming any label, P2 prohibits restating
  *either* candidate, P3 and P4 do not reference candidates. So a correctly applied ruling
  cannot change under the swap, and the residual risk is reviewer presentation sensitivity
  rather than contract semantics. Measure it: re-review a sample of the 224 swapped payloads
  with corrected labels and count flips. Do not assert "negligible" without that number.
- **Reviewer throughput.** About 9,900 distinct reviews are implied for the main run.
  Clearing them in 48 hours needs ~206 high-effort rulings per hour sustained, and the
  reviewer channel has already produced one 66.5-hour quota stall. The bridge canary's own
  ~400 rulings, run through the proposed queue at target concurrency, are the measurement.
  This is the schedule's largest single risk.

## Corrections to the gate-12 review

- The 12.5% figure reported there is **not** the frozen checker's error rate. It is reviewer
  disagreement among checker *allows*, conditional on treating the reviewer as authoritative.
  False rejects are unmeasured.
- That review bounded defect B's contamination by the 37 of 813 gate events whose query text
  named a position. That is the wrong mechanism to bound on. See the clause-symmetry argument
  above, and the measurement it calls for.

## Scope limit to carry into the write-up

The 82 main questions sit across only three world documents (27 / 28 / 27), so questions
within a world share a source. With three worlds there is no world-level resample, so the
question bootstrap cannot fully express that dependence and the claims stay conditional on
these three worlds.

## Sequence

1. Singleflight for the decision store and the call cache, plus the concurrent runner.
2. Deterministic blocked dispatch and per-provider caps.
3. Frozen missing-data policy artifact.
4. Stepped 1/2/4/8 per-provider concurrency preflight.
5. Bridge canary manifest, owner authorization against its hash, run.
6. Defect-B re-review sample and the reviewer-throughput number.
7. Main-run manifest, repriced, owner authorization against its hash.

Any code, cache-key, reviewer, cap, or failure-policy change after step 5 invalidates the
bridge canary's validation and requires it to be repeated.
