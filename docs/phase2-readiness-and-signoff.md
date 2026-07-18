# Phase 2 Readiness and Sign-off Record

**Prepared:** 2026-07-15

**Updated:** 2026-07-18

**Current gate:** **BLOCKED — approved design pending materialization**

**Machine-readable authority:** `rejudge/phase2_protocol.json`

**Cost model:** `rejudge/phase2_cost_model.json`

**Execution status:** `execution_authorized: false`

This is the authoritative launch checklist. The lead approved the scientific defaults proposed in
`docs/phase2-decision-proposal.md`; that approval does **not** authorize provider calls. Protocol
design freeze, calibration-recovery spend, capability-preflight spend, canary spend, and main-run
spend remain separate gates. The design record stays immutable after freeze; each paid stage needs
an append-only execution manifest that binds the remaining prompts, inputs, provider settings, and
authorization.

## Approval recorded

- **Design approver:** Jack Maiorino
- **Recorded at:** 2026-07-16T04:45:48Z
- **Approved framing:** pooled H/P/R decomposition is confirmatory; capability trends remain
  exploratory and model identity remains categorical.
- **Approved optional scope:** full capped interaction, empty-evidence diagnostic, and two
  full-document gold-context anchors.
- **Legacy bridge:** dropped; quantitative old-versus-new cohort comparison is prohibited.
- **Financial interpretation:** $1,500 is the incremental Phase 2 hard ceiling.
- **Capability-preflight spend authorized:** **NO** (superseded 2026-07-18: see Approvals recorded 2026-07-18)
- **Canary spend authorized:** **NO**
- **Main-run spend authorized:** **NO**

The three NO entries above record the 2026-07-16 approval event as it happened; the
2026-07-18 section below records the later policy approvals without rewriting this history.

The machine protocol remains `approved_design_pending_materialization`, publicly committed/frozen,
and never itself executable. Amendment A1 is an append-only pre-outcome provenance change. The exact
approved decisions and required artifact slots live in `rejudge/phase2_protocol.json`; completed
paid-stage values belong in append-only execution manifests rather than mutations to the frozen
design record.

## Banked evidence

- Stage 1 clean re-judging preserves a smaller positive limited-verification harm signal:
  +3.41 percentage points, 95% cluster-bootstrap CI [+1.31, +5.87].
- The audited pilot bugs explain roughly half the original headline effect; the pilot's apparent
  U-shaped recovery does not reproduce.
- Stage 1 repaired judging/oracle behavior but reused opponent-preview-contaminated legacy debate
  transcripts. Phase 2 therefore regenerates every debate with blind opening turns.
- Mechanism review found both oracle mistakes and judge over-reading. Presenting identical Q&A as
  a neutral batch removes a substantial share of the harm.
- Calibration selected blind, uncapped, three-round debate and the final roster. Eleven Gemma
  calibration cells remain missing after provider failures (10 timeouts, one HTTP 500).

Sources: `reports/2026-07-09-stage1-rejudge-results.md`,
`reports/2026-07-12-mechanism-and-packaging-memo.md`, and
`reports/2026-07-14-calibration-results.md`.

## Approved scientific design

### Confirmatory family

Three two-sided, question-clustered tests share world-stratified bootstrap draws and use Holm
adjustment:

1. `H = error(sequential_b2) - error(b0)` — total limited-verification harm;
2. `P = error(sequential_b2) - error(batch_same_qa_b2)` — packaging/commitment harm;
3. `R = error(batch_same_qa_b2) - error(b0)` — residual content harm.

`H = P + R`; these are a decomposition, not independent replications. Average K/transcripts within
question and weight questions, judges, and debaters equally. Strict `INVALID` counts wrong in the
primary analysis, with a valid-only sensitivity. Exact provider-cell completion is required.

### Secondary family and diagnostics

- `C` is the Llama-70B-judge cap interaction across both debaters:
  `(QwenPlus uncapped - QwenPlus capped150) - (Llama70 uncapped - Llama70 capped150)`.
- `D_clean = error(debate sequential_b2) - error(no_debate clean_b2)`.
- `C` and `D_clean` form a separate two-sided Holm family using shared world-stratified
  question-bootstrap draws, strict `INVALID`-as-wrong scoring, a valid-only sensitivity, and 100%
  exact completion. C averages K/transcripts within question and debater before the interaction;
  D averages its debate and shared no-debate replicates within question without duplicating the
  comparator.
- Empty-evidence and two full-document ceiling anchors are descriptive/diagnostic.
- H/P/R/D are reported descriptively by the frozen algorithmic full/partial/none direct-query
  oracle-reply-pattern class without filtering questions. The source/binding/mapping audit is
  AI-assisted; these are not human-validated semantic judgments.

### Capability measurement

Before the canary, run full-document solo QA for the five unique roster models on all 106
questions, K=2 mirrored, temperature 0, strict exact-choice parsing, and `INVALID` wrong. Freeze
the scores and raw records
before Phase 2 outcomes. Use the highest pre-frozen roster-judge score to materialize the second
full-document anchor; report the 82-main-question score as a sensitivity. These 1,060 calls are a
separately authorized capability preflight (proposed cap $15), because the anchor must be selected
before the canary can be materialized. Selection is restricted to the four roster judges and
requires 100% completion across all five measured models. Exact all-106 strict-correct count wins;
ties use main-82 count, then lower frozen price, then exact model ID, all before canary outcomes.

## Exact approved Phase 2 inventory

| Component | Transcript cells | Analysis cells |
|---|---:|---:|
| Base uncapped debate grid | 492 | 15,744 |
| No-debate references | 0 | 2,952 |
| Full capped interaction block | 492 | 984 |
| Empty-evidence diagnostic | 0 | 492 |
| Two full-document anchors | 0 | 984 |
| Capability QA preflight | 0 | 1,060 |
| **Approved Phase 2 total** | **984** | **22,216** |

The approved Phase 2 plan therefore contains **23,200 cells**: **1,060 capability-preflight cells**
followed, if later gates pass, by **22,140 post-canary main cells**. The 11-cell Gemma recovery and
945-cell canary (50 fresh transcript cells + 895 outcome cells) are separately manifested
supplements, producing **24,156 total planned cells** if every stage is authorized.

The planner validates unique planning keys and dependencies; those keys are explicitly
non-executable. Paid-stage keys must additionally bind the immutable design hash, canonical
question-bank hashes, prompt bundle, role limits, provider request fields, and side/seed policy in
an external execution manifest. The provisional call model contains 57,640 approved Phase 2 calls
before checker calls, 59,854 calls including Gemma recovery and canary, 20,352 checker calls before
retries, 80,206 total calls before retries, and 82,242 calls after the explicit 5% semantic-retry
planning allowance. Transport retries and unknown charges remain reserve risks, not silently
modeled calls.

## Cost and credit boundary

- Historical reported project spend: **approximately $208**; a rounded report, not yet reconciled
  to provider usage.
- Provider funding: **$500 + $1,300** moved into prepaid Together credit.
- Dashboard-reported available credit on 2026-07-16: **$1,590.78**.
- Balance-implied net credit draw: **$209.22** (`$1,800 - $1,590.78`); reconcile the **$1.22**
  difference from the rounded spend report against provider usage before the next call.
- User-specified spendable grant budget: **$8,000**.
- Provisional empirical planning band: **$650–$1,150**.
- Operational working budget: **$1,200**.
- Incremental fail-closed Phase 2 ceiling: **$1,500**.
- Provisional cumulative project ceiling: **$1,708**, replaced by
  `provider-verified starting spend + $1,500` before any call.

The dollar band is a manually asserted empirical budget range anchored to prior Stage 1 and
calibration usage; it is not yet a token-profile calculation from the recorded price catalog.
Frozen prompts, per-model/per-role observed token profiles, and a current provider price snapshot
must replace it before paid-stage authorization.

The $1,300 credit purchase is available funding, not additional experiment spend. The $1,500
figure is a hard boundary, not expected burn. One project-wide chained ledger and one exclusive
live-run owner must enforce the cumulative boundary across invocations. Unknown/timeouts count
until reconciled; any provider/local discrepancy or charged event missing its required output
halts rather than replays.

## Readiness matrix

| Area | Approved/current state | Remaining gate |
|---|---|---|
| Scientific scope | H/P/R, capability QA, C/D, optional arms, and legacy disposition approved | Materialize and hash final artifacts |
| Main inventory | 23,200 offline planning cells enumerated and tested; 1,060 are pre-canary capability QA | Materialize executable stage manifests and empirical top-anchor model |
| Cost model | Deterministic call inventory plus a clearly labeled empirical dollar band, dashboard balance, working budget, and ceiling | Freeze role-token profiles and prices; reconcile the provider usage ledger |
| Gemma completeness | **COMPLETE 2026-07-18:** 11/11 cells recovered in a fresh manifested supplement (git `eb1f882`, $0.03 of the $2 cap, 41,301 tokens, zero errors), archived to E: with SHA256SUMS, merged non-destructively into the analysis (mid_gemma b0 n=96 everywhere, consistent with the calibration memo). Run record: `rejudge/gemma_recovery_run_record_2026-07-18.json` | Record Jack's post-run dashboard delta (expected spend $209.25 / credit $1,590.75) |
| Query enforcement | Contract, symmetric clean/placebo scope, retry, failure, and audit policies approved | Frozen checker/prompt, validation set/threshold, named validator/auditor |
| Resolvability | Source-bound 106-row AI audit complete under owner-approved pre-outcome Amendment A1; all mappings confirmed at 6/41/59, with semantic concerns frozen separately | Bind the base protocol, A1, and combined audit hashes in every execution manifest; semantic annotations cannot relabel, filter, exclude, or reweight questions |
| Execution semantics | Temperatures, seeds/sides, retry semantics, placebo, batch, and no-debate policies approved | Prompt bundle, per-model role limits, provider pins and hashes |
| Capability preflight | Five models x 106 questions x K=2 approved; policy spend approval granted 2026-07-18, cap $15 | Freeze QA settings/limits, preflight cost forecast, storage, reconciliation, exact manifest, then Jack's binding approval of the final execution-identity hash before any call |
| Canary | Questions, six-question stratum, gates, and halt rule approved | Select anchor, create exact executable manifest, exercise checker outcomes, separate spend approval |
| Runner | Existing clients/manifests/accounting are hardened | No executable Phase 2 orchestrator yet; implement and test offline |
| Storage | Local artifact inventory exists | Versioned retrieval destination/policy and backup owner |
| Credentials | **ROTATED 2026-07-18T14:37Z.** Jack rotated the key on the Together dashboard; the new key lives only in the `TOGETHER_API_KEY` user-scope environment variable (verified live via a zero-cost `GET /v1/models`, HTTP 200); the plaintext file outside the repo was scrubbed to a non-secret rotation notice; no key was ever tracked in the repo | None; live runs must read the environment variable, never a file |
| Public record | Before-launch update posted by Jack and verified public on 2026-07-18, before any paid call from the approved 23,200-cell plan | Preserve the immutable posted source; publish results, failures, artifacts, and actual spend after execution |

## Canary policy

Use only the 24 main-excluded calibration questions. Generate one fresh uncapped transcript per
question and debater, then run b0 mirrored K=2 for all four judges. Run the frozen six-question
world/resolvability-balanced subset (`CN-011`, `CN-021`, `SEL-010`, `SEL-030`, `VS-019`,
`VS-023`) through sequential, batch, placebo, and all no-debate conditions. Exercise exact cap,
empty-evidence, and both full-document-anchor paths. The accept/reject/retry/malformed/outage
checker paths are deterministic offline runner fixtures that make no provider calls and sit outside
the 945-cell canary; they must pass before it starts.

The canary requires:

- strict `INVALID / 96 < 2%` separately for each judge on the mirrored core b0 judgments;
- absolute difference in strict error rate between the 48 A-correct and 48 B-correct core b0
  judgments `<= 10pp` separately for each judge;
- 100% exact completion of all 945 manifested provider cells (offline fixtures are a separate
  prerequisite); and
- zero prompt truncations, screen bypasses, malformed normalizations, dependency mismatches, or
  unexplained ledger/provider discrepancies.

Any failure stops before main spend. A semantic repair creates a new protocol ID/hash and reruns
the entire canary.

## Remaining materialization requirements

1. Bind the owner-approved A1 amendment and combined 106-question AI-audit hashes in every execution
   manifest. Preserve the historical blank template; never call the reply-pattern classes
   human-validated or use semantic annotations to relabel/filter questions.
2. Validate and select one query checker and frozen prompt against a human-labeled set.
3. Create/hash the prompt bundle, per-model/per-role output/reasoning limits, provider pins, and
   observed role-token profiles.
4. Implement the Phase 2 runner, exact completion gates, and project-wide ledger/lock entirely
   offline.
5. Set durable artifact storage/retrieval and rotate the API credential.
6. Verify provider spend, credit, and current prices; replace the provisional empirical dollar
   band with the frozen prompt/token-profile forecast.
7. **Complete:** commit and publish the immutable design/cost package.
8. **Complete (2026-07-18):** post the before-launch Manifund update before any call from the
   approved 23,200-cell plan.
9. Recover or separately waive the 11 Gemma cells; rerun calibration analysis. (Spend approved
   2026-07-18, cap $2; blocked on durable storage and provider reconciliation.)
10. Create the capability-preflight execution manifest; policy authorization <=$15 granted
    2026-07-18, with Jack's binding approval of the final execution-identity hash still required;
    run the 1,060 QA cells and freeze the selected top full-document anchor.
11. Materialize and separately authorize the small canary after capability preflight closes.
12. After canary review, materialize and separately authorize the main-run manifest.

## Materialization progress log

**2026-07-18 (offline, $0, no provider calls):** five artifacts landed on `rerun-new-models`
advancing requirements 2, 3, 4, and 6. No numbered gate is fully closed by this entry.

- `rejudge/phase2_query_gate.py`: offline query gate implementing the frozen one-free-retry
  policy with strict checker parsing (`allow`/`reject`/`unresolved`), fail-closed
  malformed/outage/unresolved halts, and immutable audit events.
- `rejudge/phase2_provider_price_snapshot.py` plus
  `phase2_provider_price_snapshot_2026-07-18.json`: public Together catalog snapshot binding the
  frozen roster and prices (canonical sha `4f8eecf63dd1eff5...`); public-catalog evidence only,
  account reconciliation still open.
- `rejudge/phase2_prompt_bundle.json` (candidate, canonical sha `cc02d29cfc8e7410...`), amended
  before owner methods review: checker reply tokens aligned to the gate parser vocabulary, the
  legacy template replaced with byte-exact pilot judge prompts from `experiment_protocol.json`,
  and an explicit condition-to-template composition map added.
- `rejudge/phase2_prompt_bundle.py`: fail-closed bundle validator (literal payload and provenance
  bindings, truth-neutrality information boundaries, sentinel rendering, composition-map
  validation driven by the frozen protocol's own condition attributes).
- `rejudge/phase2_execution.py`: inert execution control plane; validates
  `phase2_execution_manifest_v1` identities by rehashing every bound artifact, requires a
  separate append-only authorization record, derives non-circular call keys, and audits resumes
  (a charge without its durable output blocks reconciliation and is never replayed). Capability
  preflight is the only supported stage; canary, main, and Gemma recovery are refused.

Verification at this checkpoint: 688 tests passing, type check clean, both artifact CLI checks
verify, and no new module imports a provider SDK. The blocking review ask is now the owner
methods review of the candidate bundle wording, including the two amendments and the
composition map.

## Approvals recorded 2026-07-18

Jack Maiorino approved, by direct chat approval recorded at 2026-07-18T14:32:24Z:

- **Prompt-bundle owner methods review: APPROVED.** Scope: the full candidate wording including
  the checker-token alignment, the literal legacy provenance replacement, and the
  condition-composition map. Append-only record:
  `rejudge/phase2_prompt_bundle_approval_2026-07-18.json`, binding canonical SHA-256
  `cc02d29cfc8e7410c270c21f53da56457e44c31f74f8e512299e4e80726a076f`.
- **Gemma-recovery spend: APPROVED**, cap $2, for the exact 11 selected cells; still requires its
  separately manifested run before any call.
- **Capability-preflight spend: APPROVED**, cap $15, for the 1,060 QA cells; still requires the
  remaining offline prerequisites (per-model role limits, token-profile cost forecast replacing
  the provisional band, durable storage, credential rotation, provider reconciliation) and its
  execution manifest before any call.
- **Canary and main run:** design, sequencing, and the project-wide $1,500 incremental ceiling
  were endorsed in principle. Stage execution authorization remains NO pending the exact stage
  manifest and completion of preceding gates; neither stage can be materialized before the
  capability preflight closes and its anchor is selected.
- **Push of the 2026-07-18 offline checkpoint commits to the public fork: APPROVED.**

Chat approvals authorize recording these decisions; every paid call still requires its stage
manifest, bound caps, and the fail-closed control-plane path before execution.

**Provider-contact log:** as of 2026-07-18, no paid experimental calls have been made in Phase 2.
One zero-cost authenticated `GET /v1/models` catalog request occurred on 2026-07-18 to verify the
rotated credential; the "no provider calls" wording in the materialization progress entry applies
to that artifact checkpoint, not to this verification request.

## Signatures

- Design defaults approved: **YES**
- Design protocol validated for checkpoint: **YES**
- Public design package pushed/frozen: **YES**
- Resolvability Amendment A1 approved pre-outcome: **YES**
- Before-launch Manifund update posted and verified: **YES** / 2026-07-18T12:15:01Z
- Prompt-bundle owner methods review approved: **YES** / 2026-07-18T14:32:24Z
- Gemma-recovery spend approved: **YES** / 2026-07-18T14:32:24Z (cap $2; manifested run still required)
- Capability-preflight spend approved: **YES** / 2026-07-18T14:32:24Z (cap $15; offline prerequisites and manifest still required)
- Canary spend approved: **NO** (approved in principle 2026-07-18; binding authorization deferred to its manifest)
- Main-run spend approved: **NO** (approved in principle 2026-07-18; binding authorization deferred to its manifest)
- Design protocol canonical JSON SHA-256: `54dce0c325b83989a1f50c26a76b687362bbdeee09f52cb23b6a0a62ecd89d75`
- Approved Phase 2 plan canonical SHA-256: `686dc961434093c82e682fba2182ce7bdb551bdfc17562c43ae3f12661b0ce66`
- Base package commit: `0a21191539daae2e0807d92fcb5b1e8c179af027` (public)
- Combined AI-audit canonical JSON SHA-256: `682d19af5c1cb454fff9264ec6e96a4d2406930ec2c6003fd5cc416df98e28c1`
- Approved Amendment A1 canonical JSON SHA-256: `9ba1082a15e796a54d5e6849317e781fae09fdab8b59a99304b8d1b34fe7027a`
- Lead approver: Jack Maiorino / 2026-07-16T04:45:48Z
- Amendment A1 approver: Jack Maiorino / 2026-07-17T20:06:38Z
