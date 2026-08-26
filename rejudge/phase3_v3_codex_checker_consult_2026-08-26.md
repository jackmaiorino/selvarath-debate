# Codex consult: checker-malformed disposition (2026-08-26)

One-shot consult, gpt-5.6-sol via codex-cli 0.149.0, run by the successor orchestrator session 3cf5f3a5 after the r13 halt. Brief and verdict verbatim.

## Brief

```
One-shot consult. Self-contained; no repo access needed. Same selvarath phase-3 v3
successor canary as tonight's earlier consults. Owner asleep; standing blanket
approval covers in-phase control-plane amendments vetted by you; owner sign-off
required for science changes. Decision needed: how to implement the frozen
missing-data disposition for a checker_malformed terminal halt in the v3 driver.

EVENT (2026-08-26 07:30Z)
Run phase3-v3-120ce58628620fce (Qwen3.5-9B substitute roster) was healthy: 692+
of 1,008 rows, two 64-ruling review waves committed clean, $13.66 aggregate of
$60, uncertain $0.19 of the $1.00 per-run ceiling. In pass 3 one judgment cell
halted the driver: checker_malformed. Ledger evidence for that cell: the
gemma-4-31B query-checker call returned finish_reason=length with exactly 4096
completion tokens (its reasoning-model effective cap): the thinking-enabled
checker spent its whole budget reasoning and never emitted the verdict token.
This differs from phase-2's documented empty-string signature but is the same
class: temp-0 provider nondeterminism making one checker response unparseable
(phase-2 measured 0.27-0.6% of checker calls; ~1 in ~700 checker calls tonight).
The per-call cache memoises the response by design, so the cell can never
complete within this identity.

FROZEN POLICY (phase-2, bound then; v3 protocol carries the same missing-data
commitments): "Not a fresh judgement call. A cell that cannot produce a verdict
after the frozen allowances is excluded from execution convergence, counts
INVALID (which counts wrong) in the primary, and MUST appear in the close-out
distributions under its terminal outcome." Doctrine: checker_malformed always
stops for manual review; phase-2 resumed after appending an append-only
terminal-halts record naming the cell; its runner loaded those records and
skipped the cells, cascading exclusion to dependents. The v3 driver
(phase3_v3_live.drive_formal) never wired this: it raises and exits, and its
audit requires all 1,008 rows, so the run is unfinishable without a code
amendment.

PROPOSED AMENDMENT (control-plane, mirrors phase-2 mechanics)
1. phase3_v3_terminal_halts record schema (append-only, names cell + ledger
   evidence + policy citation); loader verifies no result row exists for a named
   cell.
2. drive_formal: exclude named cells via run_canary's existing cell_filter;
   convergence target = plan minus exclusions; log exclusions.
3. audit_and_finalize: completion gate reports exclusions; the strict INVALID
   gate counts each terminally halted judgment cell as INVALID (counts wrong)
   for its judge, per the frozen policy.
4. checker_malformed still halts for review every time; the orchestrator reviews
   the signature, appends the record, resumes. No auto-disposition.

OPEN QUESTIONS
1. The v3 structural-mirroring gate (new since phase 2) requires every unit
   rendered A+B with both sides complete. A terminally excluded judgment cell
   breaks its mirror pair. Options: (a) cascade-exclude the entire mirror unit
   (partner cells too), reporting all of them, keeping the polarity sets
   internally complete; (b) carve excluded units out of the polarity
   completeness check individually and report them. Which preserves the gate's
   meaning for an eligibility canary?
2. Code change forces a fresh manifest identity. Default (your prior verdict) is
   no row carry-across identities without an owner-created equivalence rule.
   Here the protocol, namespace, cells, and seeds are IDENTICAL; only disposition
   wiring changes, affecting no completed row. Redoing the ~692 rows costs ~$5.5
   and ~6h, both acceptable and pre-cleared; the owner is asleep. I intend the
   clean full redo and will present the carry-forward option to the owner in the
   morning report as the road not taken. Confirm or correct.
3. The redo statistically expects a few more checker truncations (0.3-0.6% of
   ~1,500-2,000 checker calls). Halt-review-resume per event is fine overnight.
   Should the amendment also bound total terminal exclusions (e.g., halt
   permanently for owner review if exclusions exceed N or any single judge or
   condition concentrates them), so a systematic checker failure cannot be
   nibbled to death as a series of "rare" events? Propose N.
4. The truncation mechanism is the reasoning checker's 4096 cap. Raising the cap
   or disabling checker thinking would be a science/measurement change (frozen
   role limits) and is NOT proposed. Flag anything else you would pre-register
   about this mechanism for the main run (e.g., report checker truncation rate
   as a canary diagnostic).
5. Confirm this stays within in-phase control-plane authority given the frozen
   policy pre-decides the disposition, or name what must wait for the owner.

Answer with numbered verdicts, blockers first.
```

## Verdict

0. **Blockers before resume.** I ratify the amendment only with these corrections:

   - Do not resume `phase3-v3-120ce58628620fce` under amended code. Seal it as halted, preserve its rows and spend read-only, and use a fresh identity, ledger, cache, and result store.
   - The terminal-halts file must not become a discretionary skip list. Each record must bind the run identity, planned cell and mirror-unit IDs, checker ledger/cache entry, response hash, model, `finish_reason`, completion tokens, parse failure, reviewer, timestamp, and frozen-policy citation. Loading must reject unplanned cells, conflicting duplicates, missing evidence, or a result row for the terminal cell.
   - Audit must partition every planned cell exactly once as completed, terminal INVALID, or causally excluded. Mirror-analysis exclusion should be a separate overlay. This prevents double counting and scheduling-dependent results.

1. **Structural mirroring: use option (a) at the analysis layer, not as blanket execution suppression.**

   Exclude the smallest complete mirror unit from the polarity-completeness gate and all paired polarity analyses. Every retained unit must still contain complete A+B observations.

   However, continue independent mirror-partner cells. Skip only cells causally dependent on the missing verdict. This gives stable semantics regardless of whether a partner happened to run before the halt:

   - The malformed cell contributes synthetic `INVALID`, hence wrong, in the strict primary.
   - Valid partner cells retain their ordinary primary outcomes.
   - The affected mirror unit contributes nothing to paired polarity analyses.
   - True downstream dependents receive a distinct `UPSTREAM_TERMINAL_EXCLUDED` outcome, not `INVALID`.
   - Closeout reports planned units, affected units, retained complete units, and coverage. Use `PASS_WITH_FROZEN_EXCLUSIONS`, not an unqualified 100-percent-complete claim.

   Simply carving out the missing cell while retaining its orphan partner in polarity analysis would weaken the mirroring gate.

2. **Confirm the clean full redo.**

   No row carry-forward. The amendment changes bound completion and audit behavior, so mint a fresh manifest identity and use fresh ledger, cache, and result namespaces. All previous spend remains charged against the aggregate `$60` cap.

   The carry-forward alternative would require an owner-approved equivalence rule. Creating that rule after approximately 692 outcomes are known would be retroactive, so it should remain the road not taken for this canary. A prospective rule could be considered for future successors.

3. **Set `N = 20` unique terminal judgment cells.**

   Halt before disposition of the 21st cell. Re-encountering the same cached terminal cell does not increment the count, and cascade exclusions are reported separately.

   At 2,000 calls and the historical upper rate of 0.6 percent, exceeding 20 has roughly a 1.1 percent binomial tail, so this distinguishes an unusual run from expected background noise without making an overnight halt likely.

   Add two concentration stops:

   - Halt if affected mirror units exceed 4 percent of planned mirror units.
   - Halt if any predeclared judge, condition, polarity, or checker route has at least five terminal cells and its exposure-normalized rate exceeds either 2 percent or three times the complementary rate.

   Crossing any bound requires owner review. The bound may stop a run but may never be relaxed mid-run or convert missing data into success.

4. **Pre-register the checker diagnostic for canary and main-run reporting.**

   Record and report:

   - All `finish_reason=length` calls, including any that still contain a parseable verdict.
   - The exact `length + 4096 completion tokens + no verdict` terminal signature.
   - Overall rate using all checker calls as denominator.
   - Rates by judge, condition, polarity, pass or wave, checker route, and input-length band.
   - Terminal cell count, affected mirror-unit count, causal exclusions, and retained paired coverage.
   - A clear non-claim that truncation is missing completely at random. Difficult or lengthy cases may be more likely to trigger it.

   Raising the token cap, disabling thinking, changing the checker prompt/model/parser allowances, or adding retries remains a science change requiring owner approval and a new identity.

5. **Authority verdict: within the stated blanket approval.**

   The evidence-bound halt ledger, manual disposition, convergence adjustment, conservative INVALID accounting, unit-level paired exclusion, fresh successor redo, and stop bounds implement an already frozen missing-data rule. They do not change the estimand or checker behavior.

   Owner sign-off is required for row carry-forward, any checker-behavior change, allowing incomplete units into paired analyses, changing terminal INVALID treatment, relaxing a crossed bound, or altering main-run science. On the authority facts you supplied, no owner sign-off is needed for this clean successor canary amendment and redo.

The fresh-identity and no-row-reuse conclusion also matches the prior recorded handoff. I did not inspect the live repository.


