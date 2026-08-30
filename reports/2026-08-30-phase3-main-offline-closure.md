# Phase 3 main offline closure and next gates

Date: 2026-08-30. Status: offline provenance, finalization, analysis, reviewer-closeout,
capacity-foundation, billing-inventory, authenticated billing-capture foundation,
process-reset reconciliation, runtime account-binding implementation, runtime response-policy
implementation, and offline hardening work complete. Production remains blocked on external
authority, provider enablement, materialized approved-account evidence, authoritative source
selection, and fresh measurement. This document authorizes no external execution, reviewer
dispatch, provider call, or spend.

This report supersedes the current-state claims in the 2026-08-29 launch foundation,
run ask package, and protocol pre-review. Those documents remain useful historical records.

## Outcome

Commits `d9d6db6`, `8299c52`, `f8a1798`, and `acee8b4` provide the blocked Phase 3 main
driver, exact manifest and signed-authorization validation, provider and reviewer dispatch
guards, complete transcript and request reconstruction, finalization admission,
confirmatory-analysis handoff, and current readiness gates. The public paid path remains
hard-blocked before manifest loading or formal state mutation.

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

This closes the historical offline harness milestone, not a permanent launch gate. The current
exact production source now differs from `acee8b4`, so the receipt must be regenerated at the
chosen exact source commit before production manifest construction. The eventual exact main
manifest must bind seed `20260829` and validate the new receipt against the chosen formal
artifact root.

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

A non-authorizing candidate inventory is now materialized at
`rejudge/phase3_main_billing_evidence_inventory_candidate_2026-08-30.json`. It reopens all
20 identified sources and ledger state tails. It remains evidence-only because the exact
account scope, authoritative completeness, and semantic disjointness still require owner
decisions. The bound source-selection proposal is
`rejudge/phase3_main_billing_source_selection_proposal_2026-08-30.json`. The exact candidate
amounts are:

- Seventeen Phase 3 measurement ledgers: settled
  `$95.36142456999999802619`, uncertain `$23.5893157200000000378`, accounted
  `$118.95074028999999806399`, with 827 unresolved attempt IDs.
- Judgment and checker auxiliary screens: settled `$5.2360874299999999661`; the judgment
  screen also carries `$0.23533403` of full-reservation uncertainty for 13 null-actual rows.
- The separate journal-validation ledger: settled `$0.3216446099999999924`.
- If the owner selects all identified sources as disjoint evidence, the mechanical local sum
  is settled `$100.91915660999999798469`, uncertain `$23.8246497500000000378`, and accounted
  `$124.74380635999999802249` across 50,141 rows. The 840 uncertain references comprise 827
  ledger attempts and 13 auxiliary null-actual rows.

These are local inventory candidates, not provider-authenticated completeness, account
binding, or a billing reconciliation. Existing auxiliary formats also cannot prove that an
auxiliary call is disjoint from every ledger call, so source disposition must be explicit.
The two auxiliary logs each have three byte-identical reviewer-transaction replicas, and their
closed timestamp intervals overlap no ledger interval. Those observations support recovery but
do not create the missing durable cross-format call identity.

Verification on the completed successor tree:

- Focused capacity execution: 34 passed; base capacity suite: 59 passed.
- Legacy-ledger compatibility and the tracked source-selection proposal: 42 passed. The v1
  and v2 exact field sets are accepted as one consistent legacy schema per ledger; a ledger
  mixing legacy and modern reservation fields is rejected.
- Complete billing boundary, including evidence inventory, proposal, reconciliation, and
  authenticated-capture tests: 112 passed.
- Complete Phase 3 main suite: 539 passed, 2 skipped in 113.03 seconds.
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

## Environmental restart contract

The ratified process reset is now implemented locally without weakening single-shot formal
measurement. Main manifests use an exact v3 restart object. An initial identity has no
predecessor. An environmental successor must use a fresh run ID and a separate, noncontained
artifact root, and it binds the predecessor manifest identity, signed authorization hashes,
void record, and final usage ledger.

The persistent registry records one exact start immediately before formal provider work. It
does not consume authorization or claim that an attempt is permanently spent. The start binds
the exact fresh ledger ID, ledger and state paths, canonical ledger identity, genesis event,
and initial ledger and state hashes. The public interruption command accepts only the four
frozen environmental reason codes, acquires the same run lease used by execution and
completion, rejects never-started or completed identities, validates the final ledger chain
and state against the start-bound genesis, and writes one immutable one-line void record. A
void never authorizes a replacement.

Successor admission reopens the exact start, void, final ledger, and ledger state; checks the
start-to-void-to-successor timestamp order; requires the predecessor authorization hashes to
match; and requires exactly one validated billing-reconciliation row for the predecessor
ledger path and raw hash. Its authenticated billing window must cover the whole predecessor
lifetime. The provider delta may never exceed the accounted upper bound, and a successor also
requires a validated account-matched provider settlement watermark strictly after the void and
strictly inside the half-open billing window. The authenticated billing validator can now issue
that watermark from a complete hourly API capture only after the provider HTTP Date is beyond
the requested whole-hour target plus the documented lag. No real watermark exists because the
selected organization does not currently have billing-usage API access.

Persistent registry records are staged in a fully fsynced sibling file and published without
replacement. POSIX uses a hard link followed by a parent-directory fsync. Windows uses a
same-directory `MoveFileExW` operation with `MOVEFILE_WRITE_THROUGH`, no replace flag, and an
exact byte reopen. Microsoft documents that the write-through move does not return until the
move is on disk in the [MoveFileExW contract](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
and demonstrates the same operation for a directory whose target does not exist in its
[directory example](https://learn.microsoft.com/en-us/windows/win32/fileio/moving-directories).
Fresh Windows registry and artifact ancestors are therefore created as unique empty
sibling stages and write-through moved into place one level at a time. A stable bootstrap
lease serializes registry-tree creation, and any concurrently appearing target fails the
current attempt closed. POSIX retains one-level creation plus directory fsync. A post-link
POSIX crash alias is removed only when it is the same file as the final record; a
different-file alias fails closed. The artifact volume is independently probed through the
platform publication path, exact reopen, and cleanup under the run lease before persistent
start or provider-client construction. The fresh genesis ledger uses the same fsynced,
no-replace publication, and every ledger-state creation or replacement is write-through moved
and exactly reopened on Windows. Win32 calls receive extended-length absolute paths, so a deep
but valid artifact tree does not pass the root probe and then fail after paid work solely due
to the legacy `MAX_PATH` boundary.

The signed authorization text states that the identity is single-shot, environmental
interruption voids it, and restart requires both a fresh successor manifest and a separate
exact authorization. The manifest still fixes `formal_main_attempt_count` at one.

Verification on the process-reset tree:

- Focused runner, manifest, and live-driver suite: 114 passed, 2 skipped.
- Usage-client and accounting regression suite: 132 passed.
- Complete Phase 3 main suite: 486 passed, 2 skipped in 127.98 seconds.
- Full repository exact-snapshot suite: 2,903 passed, 67 skipped in 617.47 seconds.
- The two skips are direct Windows symlink exercises on a host without symlink privilege.
  Deterministic lexical dangling-link and reparse-point refusal tests passed.
- Scoped type checking, static compilation, whitespace checks, and stale-schema searches:
  passed.
- Adversarial review found and closed ordering, ledger-provenance, billing-window,
  provider-settlement, prior-upper-bound, directory-durability, crash-alias, and cross-volume
  publication gaps. The settled snapshot has no remaining P0, P1, or P2 finding.

No inference request, paid provider call, reviewer dispatch, capacity measurement, production
launch, push, or spend occurred during this work. The provider exploration consisted only of
one authenticated identity GET and one authenticated billing-usage GET.

## Authenticated Together billing foundation

The successor now has a separate read-only Together capture command. It permits only fixed
HTTPS GET requests to `/v1/whoami` and `/v1/billing/usage`, follows no redirects, performs no
retries, constructs no inference client, and publishes no index unless every response and
binding validates. The API-key secret remains in memory only. Raw response sidecars are
SHA-256 bound and published before an index through exclusive write-through moves, with exact
rollback on publication errors and process-level interruptions.

A fresh live check showed that Together legitimately repeats `Set-Cookie`. The transport now
discards cookies and all other irrelevant response metadata, retaining and duplicate-checking
only `Date` and `Content-Type`. This preserves the security-relevant ambiguity checks without
rejecting a standards-compliant live response or persisting cookie material.

The account identity hash binds the versioned API-key ID, project ID, organization ID, and
provider tuple. Billing capture requires one complete hourly response per intersecting UTC
month, `limit=1000`, no cursor, USD, exact fixed-point costs, and matching key and project
attribution on every counted line item. Finality does not use `latest_window_end`, which marks
only the latest window containing usage. It uses a caller-selected whole-hour target and
requires the provider HTTP Date to be strictly beyond that target plus Together's documented
one-hour current-month or 24-hour prior-month lag. Empty usage intervals can therefore be
covered without inventing usage.

Reconciliation v3 reopens the raw authenticated sidecars, recomputes exact provider and ledger
arithmetic independently of ambient Decimal context, applies a half-open ledger-event window,
and emits the exact account-matched settlement map consumed by the live gate. Legacy v2
dashboard evidence remains readable for archives but cannot enter any formal main run,
including an initial identity.

A sanitized live [identity GET](https://docs.together.ai/reference/whoami) returned 200. The
[billing-usage GET](https://docs.together.ai/reference/billing-usage) returned 404, which
Together documents as the beta endpoint not being enabled for the organization. The code path
is ready, but no authenticated billing capture or settlement watermark was materialized. This
work does not establish which local sources are authoritative and disjoint or authorize any
provider execution. A fresh recheck at provider time `Sun, 30 Aug 2026 18:13:54 GMT` again
returned 200 for identity and 404 for billing usage.

Verification on this milestone:

- Focused capture and reconciliation: 63 passed.
- Complete Phase 3 main suite: 523 passed, 2 skipped in 120.03 seconds.
- Full repository: 2,940 passed, 67 skipped in 651.98 seconds.
- Scoped type checking, static compilation, diff checks, and the no-em-dash rule: passed.
- Independent capture-security and reconciliation/live reviews found no remaining P0, P1, or
  P2 finding.

## Runtime account binding and credential isolation

The launch manifest and detached owner authorization are now v4. Both visibly and exactly name
the approved `provider_account_identity_sha256`, and the signed authorization text includes that
hash. The live billing gate requires its authenticated scope and settlement account to equal the
same manifest account, so a self-consistent reconciliation for another account cannot enter the
run.

After all local freshness and authorization checks, but before creating the persistent identity
registry or formal artifact tree, the runner reads `TOGETHER_API_KEY` once and performs one fixed
read-only `/v1/whoami`. The verifier accepts no redirect, retry, schema drift, secret echo, HTTP
error, or account mismatch. It returns only irreversible identity hashes. The exact same in-memory
key is immediately passed to the Together SDK with the inference base URL, timeouts, retry count,
and no-redirect policy explicitly pinned. Later environment changes therefore cannot substitute a
different key or endpoint.

Every child process launched by the main runner now receives an environment with Together API-key
and base-URL variables removed case-insensitively. This includes Git identity checks, reviewer CLI
version checks, and formal reviewer waves. The Codex reviewer can no longer inherit the inference
credential.

Verification on this milestone:

- Focused boundary suites: 252 passed, 2 skipped; installed SDK pin smoke: 1 passed.
- Complete Phase 3 suite: 979 passed, 3 skipped in 262.79 seconds.
- Full repository: 2,954 passed, 67 skipped in 664.08 seconds.
- Scoped type checking, static compilation, diff checks, and the no-em-dash rule: passed.
- No inference request, paid provider call, reviewer dispatch, production launch, push, or spend
  occurred.

## Runtime price and reviewer usage contracts

The v5 launch manifest and exact owner authorization now bind two tracked, non-authorizing
runtime policy candidates by raw SHA-256. The authorization also binds an exact maximum of
59,040 external reviewer dispatches, equal to the capacity plan's worst-case unique payload
bound. Each reviewer wave is admitted against the cumulative count before release, every
durable reservation counts even when the dispatch later fails or is ambiguous, and the run log
records the wave and cumulative quantities. This count is separate from Together provider
usage, is excluded from the Together USD stage cap, and is explicitly neither USD nor token
accounting. No claim of zero monetary cost or unlimited reviewer capacity is made.

Every new logical Together call now checks both the fixed price-change signal path and its
deterministic publication stage before price-snapshot and signed-authorization revalidation.
Either path halts new calls. Already-started work may finish and be accounted, but the identity
cannot resume. A successor requires a fresh price snapshot, forecast, manifest, cap, and exact
authorization.

The standalone `scripts/phase3_main_record_price_change.py` command creates the stop signal
without accepting an authorization or constructing a provider client. It requires the exact
manifest-derived identity to have matching persistent start, active-marker, and root-binding
evidence; binds a stable local evidence file by canonical path, byte count, and raw SHA-256; and
publishes the signal exclusively. A duplicate cannot replace the first signal. Signal presence
is the safety boundary, so malformed or partially staged signal material still blocks new calls.
The structured payload remains independently reopenable for diagnosis and carries all authority
flags as false.

The exact candidate policies remain proposals until Jack's final v5 authorization binds their
hashes and exact text. That missing authorization prevents execution, but no additional runtime
mechanism is required for these two response rules. Verification on this successor:

- Focused runner, manifest, policy, and live-driver suite: 141 passed, 2 skipped.
- Complete Phase 3 main suite: 561 passed, 2 skipped in 121.83 seconds.
- Static compilation of the policy, manifest, runner, live driver, and stop-signal command:
  passed.
- No inference request, paid provider call, reviewer dispatch, production launch, push, or spend
  occurred.

## Remaining production blockers

The remaining items require owner decisions, internal contract reconciliation, or fresh
external evidence. The already closed main request provenance, reviewer provenance, and
reviewer-closeout crash consistency remain intact. The new capacity lane is deliberately not
enabled. The exact current decisions and non-execution ratification text are consolidated in
`reports/2026-08-30-phase3-main-owner-decision-brief.md`:

1. Jack's public signing key and fingerprint are not pinned. The private key must remain outside
   the repository and inaccessible to Codex.
2. The protocol still fixes a $450 stage cap while the sealed canary supports a proposed $875
   planning cap. One value must be ratified after a fresh certified forecast.
3. Together billing-usage beta access is not enabled for the selected organization. No live
   authenticated capture, settlement watermark, or v5 signed approved-account selection exists.
   The binding mechanism is complete; an authoritative, disjoint predecessor-ledger inventory
   remains open.
4. The representative reviewer-capacity preflight has a frozen plan and a verified fake-only
   execution foundation, but no authorized external measurement result. Real dispatch remains
   hard-disabled.
5. Fresh prices, capacity evidence, billing evidence, forecast, regenerated exact-source
   harness receipt, exact manifest, and detached owner authorization have not been
   materialized for a production identity.

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

1. Ask Together to enable the billing-usage beta endpoint for the selected organization, then
   confirm the stable account, organization, project, and API-key-ID scope.
2. Ratify or amend the three exact source groups in the tracked source-selection proposal,
   establish authoritative completeness and semantic disjointness, and bind the approved account
   scope. Then run the read-only authenticated capture and reconciliation. The v5 launch gate will
   bind the exact runtime credential to that signed scope. Any environmental predecessor also
   needs a post-void settlement watermark.
3. Ratify or amend the tracked price-change and reviewer-usage policy candidates, then bind
   their exact hashes, the 59,040 reviewer ceiling, and the required response text in the final
   v5 authorization. Pin Jack's public signing key and fingerprint.
4. Close the capacity real-enable gaps and request a separate bounded authorization for the
   representative reviewer-capacity preflight. Do not treat this report, the existing plan,
   or fake-only tests as dispatch authority.
5. If capacity passes, materialize fresh prices, billing reconciliation, and the certified
   forecast.
6. Use the certified forecast to ratify one exact stage cap. Regenerate the harness receipt at
   the exact source commit and revalidate it against the chosen formal artifact root and exact
   frozen inputs.
7. Build the exact manifest and owner-signed authorization, then run `--validate-only` and prove
   zero formal-state mutation.
8. Perform the dedicated final methods and launch review. A separate explicit authorization is
   still required before any paid `--run` invocation.
