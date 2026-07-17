# Phase 2 Readiness and Sign-off Record

**Prepared:** 2026-07-15

**Updated:** 2026-07-16

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
- **Capability-preflight spend authorized:** **NO**
- **Canary spend authorized:** **NO**
- **Main-run spend authorized:** **NO**

The machine protocol remains `approved_design_pending_materialization`, not yet committed/frozen
and never itself executable. The exact approved decisions and required artifact slots live in
`rejudge/phase2_protocol.json`; completed paid-stage values belong in append-only execution
manifests rather than mutations to the frozen design record.

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
- H/P/R/D are reported descriptively by human-validated full/partial/none direct-resolvability
  class without filtering questions.

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

- Historical reported project spend: **approximately $208**; not provider-verified evidence.
- Provider funding: **$500 + $1,300** moved into prepaid Together credit.
- Estimated current credit: **approximately $1,592** (`$1,800 - ~$208`); verify in the provider
  dashboard before the next call.
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
| Cost model | Deterministic call inventory plus a clearly labeled empirical dollar band, credit estimate, working budget, and ceiling | Freeze role-token profiles and prices; reconcile provider dashboard |
| Gemma completeness | Recovery of exact 11 cells selected; proposed cap $2 | Separate spend authorization and manifested run |
| Query enforcement | Contract, symmetric clean/placebo scope, retry, failure, and audit policies approved | Frozen checker/prompt, validation set/threshold, named validator/auditor |
| Resolvability | Immutable 106-row blank template generated; preliminary full/partial/none counts are 6/41/59 | Create a separate review copy, complete the human pass, and name reviewer/auditor; final hash goes in the execution manifest |
| Execution semantics | Temperatures, seeds/sides, retry semantics, placebo, batch, and no-debate policies approved | Prompt bundle, per-model role limits, provider pins and hashes |
| Capability preflight | Five models x 106 questions x K=2 approved; proposed cap $15 | Freeze QA prompt/settings, separate manifest and spend approval, then select anchor |
| Canary | Questions, six-question stratum, gates, and halt rule approved | Select anchor, create exact executable manifest, exercise checker outcomes, separate spend approval |
| Runner | Existing clients/manifests/accounting are hardened | No executable Phase 2 orchestrator yet; implement and test offline |
| Storage | Local artifact inventory exists | Versioned retrieval destination/policy and backup owner |
| Credentials | Plaintext key file remains outside the repo | Rotate and move to environment/secret storage before live work |
| Public record | Before-launch update drafted; Jack is the poster | Post after immutable design/cost commit, before the first paid call from the approved 23,200-cell plan |

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

1. Create a separate working copy from the immutable 106-question resolvability template, complete
   the human pass, and assign reviewer/auditor ownership. Never edit/reset the blank template.
2. Validate and select one query checker and frozen prompt against a human-labeled set.
3. Create/hash the prompt bundle, per-model/per-role output/reasoning limits, provider pins, and
   observed role-token profiles.
4. Implement the Phase 2 runner, exact completion gates, and project-wide ledger/lock entirely
   offline.
5. Set durable artifact storage/retrieval and rotate the API credential.
6. Verify provider spend, credit, and current prices; replace the provisional empirical dollar
   band with the frozen prompt/token-profile forecast.
7. Commit and publish the immutable design/cost package.
8. Post the before-launch Manifund update before any call from the approved 23,200-cell plan.
9. Recover or separately waive the 11 Gemma cells; rerun calibration analysis.
10. Create the capability-preflight execution manifest; obtain its separate <=$15 authorization,
    run the 1,060 QA cells, and freeze the selected top full-document anchor.
11. Materialize and separately authorize the small canary after capability preflight closes.
12. After canary review, materialize and separately authorize the main-run manifest.

## Signatures

- Design defaults approved: **YES**
- Design protocol validated for checkpoint: **YES**
- Public design package pushed/frozen: **NO**
- Gemma-recovery spend approved: **NO**
- Capability-preflight spend approved: **NO**
- Canary spend approved: **NO**
- Main-run spend approved: **NO**
- Design protocol canonical JSON SHA-256: `54dce0c325b83989a1f50c26a76b687362bbdeee09f52cb23b6a0a62ecd89d75`
- Approved Phase 2 plan canonical SHA-256: `686dc961434093c82e682fba2182ce7bdb551bdfc17562c43ae3f12661b0ce66`
- Package commit: `0a21191539daae2e0807d92fcb5b1e8c179af027` (local; public push pending)
- Lead approver: Jack Maiorino / 2026-07-16T04:45:48Z
