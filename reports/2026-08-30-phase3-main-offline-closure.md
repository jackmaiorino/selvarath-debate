# Phase 3 main offline closure and next gates

Date: 2026-08-30. Status: offline provenance, finalization, analysis, reviewer-closeout,
capacity-foundation, billing-inventory, and offline hardening work complete. Production
remains blocked on external authority, authenticated account evidence, and fresh measurement.
This document authorizes no external execution, reviewer dispatch, provider call, or spend.

This report supersedes the current-state claims in the 2026-08-29 launch foundation,
run ask package, and protocol pre-review. Those documents remain useful historical records.

## Outcome

Commits `d9d6db6`, `8299c52`, `f8a1798`, and `acee8b4` provide the blocked Phase 3 main
driver, exact manifest and signed-authorization validation, provider and reviewer dispatch
guards, complete transcript and request reconstruction, finalization admission,
confirmatory-analysis handoff, and current readiness gates. The public paid path remains
hard-blocked before formal state mutation.

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

## Offline harness milestone

The two-execution fake-only harness passed from source commit
`acee8b4252f32f2ed46e9d890d753909a0145874` with seed `20260829`. Both fresh executions
preseeded the 492 bound main transcripts, completed one selected b0 judgment through the
module-owned deterministic client, and produced 493-row result stores with the identical raw
SHA-256 `32d3f91aacec80b1faf943444df4aae8ee842a2df39618552fdb693163a67d7d`.

The receipt is
`E:/selvarath-archive/phase3-main-harness-acee8b4-2026-08-30/phase3_main_harness_receipt.json`
with raw SHA-256
`479cab54a05be295419e5176a48e770ef24af0d0583c4dacbfa50fb8d3df1e24`. Independent
validation reopened all linked stores, journals, ledgers, transcript inputs, and frozen JSON
bindings. Each execution has a distinct identity and zero-spend genesis ledger, each journal
contains exactly one fake call, and `execution_authorized`, `provider_calls_authorized`, and
`main_run_spend_authorized` are all false. The compact tracked materialization record is
`rejudge/phase3_main_harness_materialization_2026-08-30.json`.

Post-materialization checks passed: the record's declared source, input, receipt, and output
hashes all match disk; the focused harness suite passed 9 tests; all 349 top-level `rejudge`
JSON files parsed; and whitespace plus no-em-dash checks passed.

This closes the current offline harness milestone, not a permanent launch gate. The eventual
exact main manifest must bind seed `20260829`, validate this receipt against the chosen formal
artifact root, and regenerate it after any harness-sensitive execution-code or frozen-input
change.

## Offline capacity and billing foundations

The successor patch adds two strictly offline foundations.

The capacity execution module binds the frozen plan, repository commit, reviewer CLI and
runner bytes, host identity, exact three-wave workload, dispatch history, result path, and a
separate capacity-only authorization. Its fake-only orchestrator exercises exactly three
waves of 60 packets and requires 180 unique reopened invocation receipts. It validates the
predicted PASS result against the complete base capacity contract before publication and
keeps downstream launch admission false.

Real reviewer dispatch is not enabled. Both the public execution entry point and CLI `--run`
stop before context loading, file reads, signature work, subprocess release, reservation, or
mutation. Enablement still requires detached-signature provenance with a pinned owner public
key, hard-crash reviewer-usage reconciliation, a guarded atomic launch-input handoff,
and authenticated integration with `phase3_main_live`.

The billing evidence inventory accepts only explicit `ID=PATH` source tuples. It reopens and
hashes immutable usage-ledger and state pairs plus the two known auxiliary screen-log shapes,
enforces one half-open UTC window, and sums JSON-number costs with exact decimal arithmetic.
Null-actual error rows retain the full reservation as uncertain. It rejects duplicate source
IDs, paths, raw hashes, ledger IDs, and attempt IDs across ledgers, and publishes through an
exclusive fully-fsynced sibling file with exact-byte reopen. Every inventory permanently says
`authoritative_completeness: not_established`, and all execution and spend authority flags are
false.

The hardening follow-up keeps auxiliary integer lexemes distinct from decimal or exponent
cost lexemes. Numeric strings and integral float counters are now rejected, while exact
decimal cost arithmetic is preserved. Private raw-row digests reject exact row overlap across distinct
auxiliary snapshots without changing the public inventory schema.

Capacity manifest construction now rejects equality and ancestor or descendant containment
across every critical result, history, history-control, anchor, interruption, reservation,
failure, and wave output path. Existing symlink, junction, reparse, hard-link, and same-file
aliases are rejected before authorization verification or reviewer release. The legitimate
sibling workload layout is preserved, and nonexistent destinations remain protected by
exclusive creation. Public result validators always require dispatch anchors; only the private
predicted-PASS validator can disable on-disk anchor lookup before the terminal history append.

No real inventory was materialized because the exact account scope and authoritative source
set still require owner decisions. A read-only local audit found these candidate amounts:

- Seventeen Phase 3 measurement ledgers: settled
  `$95.36142456999999802619`, uncertain `$23.5893157200000000378`, accounted
  `$118.95074028999999806399`, with 827 unresolved attempt IDs.
- Judgment and checker auxiliary screens: settled `$5.23608743`; the judgment screen also
  carries `$0.23533403` of full-reservation uncertainty for 13 null-actual rows.
- The separate journal-validation ledger: settled `$0.32164461`.
- If the owner selects all identified sources as disjoint evidence, the mechanical local sum
  is settled `$100.91915660999999802619`, uncertain `$23.8246497500000000378`, and accounted
  `$124.74380635999999806399`.

These are local inventory candidates, not provider-authenticated completeness, account
binding, or a billing reconciliation. Existing auxiliary formats also cannot prove that an
auxiliary call is disjoint from every ledger call, so source disposition must be explicit.

Verification on the completed successor tree:

- Focused capacity execution: 34 passed; base capacity suite: 59 passed.
- Focused billing inventory: 38 passed; inventory plus existing billing reconciliation:
  64 passed.
- Combined capacity and billing integration: 157 passed in 27.03 seconds on a fresh external
  Windows basetemp.
- Targeted type checking, whitespace checks, diff checks, and the repository no-em-dash rule:
  passed.
- Independent capacity, billing, and integration audits found no P0 or P1 within the accepted
  offline, fake-only, non-authorizing scope.

The remaining limitations are explicit rather than silently inferred. Existing auxiliary
formats cannot prove semantic call disjointness from each other or from ledgers. A nonexistent
path cannot yet have a discoverable hard-link identity, so later creation stays exclusive.
Inventory publication requires same-filesystem hard-link support and fails closed where it is
unavailable. None of these limitations grants execution authority or provider access.

## Remaining production blockers

The remaining items require owner decisions, internal contract reconciliation, or fresh
external evidence. The already closed main request provenance, reviewer provenance, and
reviewer-closeout crash consistency remain intact. The new capacity lane is deliberately not
enabled:

1. Jack's public signing key and fingerprint are not pinned. The private key must remain outside
   the repository and inaccessible to Codex.
2. The protocol still fixes a $450 stage cap while the sealed canary supports a proposed $875
   planning cap. One value must be ratified after a fresh certified forecast.
3. Provider-authenticated billing evidence, runtime credential-to-account binding, and an
   authoritative predecessor-ledger inventory do not exist yet.
4. The production protocol and driver have not yet been reconciled to the ratified process
   reset. Formal measurement remains single-shot, while an environmental interruption voids
   that run and permits a fresh identity with a one-line log entry. A non-resettable external
   consumption store is not a new project requirement.
5. No signed response rule exists for a provider price change during the formal run.
6. Codex reviewer usage has no separately ratified spend treatment. The representative
   capacity preflight has a frozen plan and a verified fake-only execution foundation, but no
   authorized external measurement result. Real dispatch remains hard-disabled.
7. Fresh prices, capacity evidence, billing evidence, forecast, exact manifest, and detached
   owner authorization have not been materialized for a production identity.

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

1. Jack selects the stable Together account, organization, project, and API-key-ID scope;
   supplies provider-authenticated dashboard evidence; and decides the exact predecessor
   ledger and auxiliary source set.
2. Materialize the explicit local billing inventory, bind the runtime credential to the
   selected account scope, reconcile the inventory to the authenticated dashboard, and obtain
   an authoritative completeness attestation.
3. Reconcile the protocol and driver to the ratified environmental-restart rule. Ratify the
   price-change response and reviewer usage treatment, then pin Jack's public signing key and
   fingerprint.
4. Close the capacity real-enable gaps and request a separate bounded authorization for the
   representative reviewer-capacity preflight. Do not treat this report, the existing plan,
   or fake-only tests as dispatch authority.
5. If capacity passes, materialize fresh prices, billing reconciliation, and the certified
   forecast.
6. Use the certified forecast to ratify one exact stage cap. At exact-manifest construction,
   revalidate the harness receipt against the chosen formal artifact root and exact frozen
   inputs. Regenerate it if the seed or harness-sensitive code differs from `acee8b4`.
7. Build the exact manifest and owner-signed authorization, then run `--validate-only` and prove
   zero formal-state mutation.
8. Perform the dedicated final methods and launch review. A separate explicit authorization is
   still required before any paid `--run` invocation.
