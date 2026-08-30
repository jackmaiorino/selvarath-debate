# Phase 3 main offline closure and next gates

Date: 2026-08-30. Status: offline provenance, finalization, and analysis closure complete;
one crash-consistency implementation blocker remains. This document authorizes no external
execution, reviewer dispatch, provider call, or spend.

This report supersedes the current-state claims in the 2026-08-29 launch foundation,
run ask package, and protocol pre-review. Those documents remain useful historical records.

## Outcome

Commits `d9d6db6` and `8299c52` provide the blocked Phase 3 main driver, exact manifest and
signed-authorization validation, provider and reviewer dispatch guards, complete transcript
and request reconstruction, finalization admission, and confirmatory-analysis handoff. The
public paid path remains hard-blocked before formal state mutation.

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
- The finalization admission is rebuilt from bound artifacts before analysis, and final
  boundary inputs are reopened after analysis before completion is recorded.

Independent read-only audits found one remaining P1: reviewer decision and wave-index closeout
does not yet satisfy the frozen crash-consistency requirement. They found no other P0 or P1
issue in the external-dispatch boundary or writer-reader schema path. Verification completed
on the committed provenance implementation:

- Integrated closure: 353 passed.
- Expanded closure: 841 passed, 1 skipped.
- Full repository: 2,760 passed, 65 skipped in 586.31 seconds.
- Static compilation, whitespace checks, JSON parsing, and the repository no-em-dash rule:
  passed.

No paid call, external reviewer call, production launch, or push occurred during this closure.

## Remaining production blockers

The first seven items are external authority or fresh-evidence gates. The eighth is the one
remaining offline implementation blocker. None is missing request or reviewer provenance:

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
8. Reviewer decisions are fsynced before the corresponding wave index. A process death can
   therefore leave a durable decision prefix without its provenance row. Finalization rejects
   that state, but it does not satisfy the frozen requirement that decision stores remain
   crash-consistent.

## Crash-consistency closure

No-resume prevents a partial wave from contaminating a clean finalization, but it does not
supersede the protocol's separate crash-consistency requirement. The next offline implementation
must make decision and wave-index closeout recoverable without repeating external work.

The safe design is local closeout only: persist an exact per-wave intent before either store is
changed, bind the prior and target bytes and chain tails for both stores, append and fsync the
decision suffix, append and fsync the index suffix, then persist an immutable completion receipt.
Recovery may append only a missing exact suffix under the existing run lease. It must never
truncate, overwrite, construct a provider client, or dispatch a provider or reviewer.

## Next sequence

1. Implement and fault-test the local reviewer closeout transaction, then materialize the
   two-execution offline harness receipt from the exact resulting commit. The harness uses a
   module-owned fake and requires no provider, reviewer, or spend authorization.
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
