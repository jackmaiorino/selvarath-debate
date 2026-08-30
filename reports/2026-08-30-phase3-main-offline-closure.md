# Phase 3 main offline closure and next gates

Date: 2026-08-30. Status: offline provenance, finalization, analysis, and reviewer-closeout
closure complete. Production remains blocked on external authority and fresh evidence. This
document authorizes no external execution, reviewer dispatch, provider call, or spend.

This report supersedes the current-state claims in the 2026-08-29 launch foundation,
run ask package, and protocol pre-review. Those documents remain useful historical records.

## Outcome

Commits `d9d6db6`, `8299c52`, and `f8a1798` provide the blocked Phase 3 main driver, exact
manifest and signed-authorization validation, provider and reviewer dispatch guards, complete
transcript and request reconstruction, finalization admission, confirmatory-analysis handoff,
and current readiness gates. The public paid path remains hard-blocked before formal state
mutation.

The closure adds these properties:

- Every new logical provider call reopens price and signed-authorization evidence immediately
  before its durable reservation. The persisted authorization timestamp is replay-validated,
  and an immediate structured streaming-negotiation retry inherits the same logical authority.
- Ambiguous provider errors, including unstructured streaming text and errors on an
  already-streaming attempt, remain `unknown_charge` and halt the identity.
- Finalization independently reconstructs every verdict and non-verdict request, including the
  post-role-limit provider fields, and joins each logical call to its journal and settled ledger
  lifecycle.
- Every reviewer worker reopens the exact signed authority, capacity evidence, CLI identity,
  worklist, packet index, packet bytes, output path, model, effort, and concurrency before an
  exclusive durable reservation and subprocess release.
- Reviewer finalization independently validates the worklist, packet tree, guard, reservation,
  invocation evidence, zero-tool-use rule, decisions, and wave index.
- Reviewer decisions and the wave index now close through one v4 transaction. A durable intent
  binds both prior and target stores before either append; a receipt records exact completion.
- Offline recovery can append only an exact missing local suffix under the exact run lease. Its
  command requires an independently supplied formal artifact root and pre-existing lease, and
  has no reviewer, provider, subprocess, callback, or network surface.
- The finalization admission is rebuilt from bound artifacts before analysis, and final
  boundary inputs are reopened after analysis before completion is recorded.

Independent read-only audits initially found two recovery P1s: intent-selected paths could
escape the formal artifact root, and recovery reloaded the intent after its post-lock hash
check. Both were fixed before closure. The final adversarial review found no remaining P0 or P1
in the reviewer-closeout transaction, live wiring, provenance join, or finalization binding.
Verification on the resulting tree:

- Reviewer-closeout integration: 188 passed.
- Full repository: 2,807 passed, 65 skipped in 514.98 seconds.
- Post-closure blocker-list reconciliation: 57 live-driver tests passed.
- Static compilation, whitespace checks, JSON parsing, and the repository no-em-dash rule:
  passed.

No paid call, external reviewer call, production launch, or push occurred during this closure.

## Remaining production blockers

All remaining items require owner authority or fresh external evidence. None is missing
request provenance, reviewer provenance, or reviewer-closeout crash consistency:

1. Jack's public signing key and fingerprint are not pinned. The private key must remain outside
   the repository and inaccessible to Codex.
2. The protocol still fixes a $450 stage cap while the sealed canary supports a proposed $875
   planning cap. One value must be ratified after a fresh certified forecast.
3. Provider-authenticated billing evidence, runtime credential-to-account binding, and an
   authoritative predecessor-ledger inventory do not exist yet.
4. One-attempt consumption has no non-resettable external authority source.
5. No signed response rule exists for a provider price change during the formal run.
6. Codex reviewer usage has no separately ratified spend treatment. The representative
   capacity preflight has a frozen plan but no authorized measurement result.
7. Fresh prices, capacity evidence, billing evidence, forecast, two-run harness receipt, exact
   manifest, and detached owner authorization have not been materialized for a production
   identity.

## Crash-consistency closure

The frozen crash-consistency requirement is now implemented. Each reviewer wave persists an
immutable intent containing the exact decision and index append bytes, their prior and target
hashes and byte counts, the decision-chain tails, evidence hashes, transaction ID, and lease
path. It then appends and fsyncs decisions, appends and fsyncs the index row, and atomically
publishes a completion receipt.

Recovery accepts only a strict prior-to-target prefix, never truncates or overwrites, and never
repeats external work. The standalone command additionally derives the only permitted store
paths from an independently supplied formal artifact root, requires the existing formal lease,
rejects linked path components, and carries the exact intent loaded under that lease through
the append operation. Provenance validates every intent and receipt, exact raw index row,
cross-wave continuity, final store targets, and the complete packet tree.

## Next sequence

1. Materialize the two-execution offline harness receipt from the exact commit containing this
   closure. The harness uses a module-owned fake and requires no provider, reviewer, or spend
   authorization. Rerun it if execution code changes before the production manifest is built.
2. Obtain the owner-supplied Together billing-console evidence and decide the authoritative
   account, predecessor-ledger, and one-attempt consumption sources.
3. Ratify the price-change rule, reviewer usage treatment, and one exact stage cap. Then pin
   Jack's public signing key and fingerprint.
4. Request a separate bounded authorization for the representative reviewer-capacity preflight.
   Do not treat this report or the existing plan as dispatch authority.
5. If capacity passes, materialize fresh prices, billing reconciliation, and the certified
   forecast.
6. Build the exact manifest and owner-signed authorization, then run `--validate-only` and prove
   zero formal-state mutation.
7. Perform the dedicated final methods and launch review. A separate explicit authorization is
   still required before any paid `--run` invocation.
