# Phase 2 Readiness and Sign-off Record

**Prepared:** 2026-07-15

**Current gate:** **BLOCKED — offline planning only**

**Machine-readable draft:** `rejudge/phase2_protocol.json`

**Execution status:** `execution_authorized: false`

This is the authoritative launch checklist. Protocol approval, canary-spend approval, and
main-run approval are separate decisions. None has yet been granted.

`docs/phase2-decision-proposal.md` contains non-binding recommended defaults intended to make the
lead review concrete; it does not resolve any item in this record.

## Banked evidence

- Stage 1 clean re-judging preserves a smaller positive limited-verification harm signal:
  +3.41 percentage points, 95% cluster-bootstrap CI [+1.31, +5.87].
- The audited pilot bugs explain roughly half the original headline effect; the pilot's
  U-shaped recovery does not reproduce cleanly.
- Mechanism review found both oracle mistakes and judge over-reading. Presenting identical
  query/answer evidence as a neutral batch removes a substantial share of the observed harm.
- Calibration selected blind, uncapped, three-round debate and proposed the final roster below.
  Eleven Gemma calibration cells remain missing after provider failures (10 timeouts, one HTTP 500).

Source reports: `reports/2026-07-09-stage1-rejudge-results.md`,
`reports/2026-07-12-mechanism-and-packaging-memo.md`, and
`reports/2026-07-14-calibration-results.md`.

## Proposed frozen inventory

- Questions: 82 main questions after excluding the 24 calibration questions.
- Judges: Qwen2.5-7B, Gemma-4-31B, Llama-3.3-70B, GPT-OSS-120B.
- Debaters: Llama-3.3-70B and hosted Qwen3.7-Plus; honest and dishonest roles use the same
  model within a transcript.
- Oracle: Llama-3.3-70B.
- Debate generation: 3 blind, counterbalanced transcripts per question and debater.
- Debate conditions, K=2: b0, sequential clean b2, batch-same-Q&A b2, sequential placebo b2.
- No-debate conditions, K=3: b0, clean b2, placebo b2.
- Exact offline inventory: 492 transcript cells, 15,744 debate judgments, 2,952 no-debate
  judgments, 18,696 total judgments, and 3,936 batch-to-sequential dependencies.

## Readiness matrix

| Area | Current evidence | Gate |
|---|---|---|
| Scientific protocol | Roster and current grid are proposed; every machine-readable decision section remains unresolved in `phase2_protocol.json` | **Blocked on lead sign-off** |
| Primary analysis | H, packaging P, and residual batch-vs-b0 content harm are candidate named quantities; exact three tests are not frozen | **Blocked** |
| Capability measurement | Solo QA with the full world document is the agreed family; question set, scoring, K, and exclusions are unset | **Blocked** |
| Cap-protection secondary | Llama-70B is the target judge; exact contrast and new cell inventory are unset | **Blocked** |
| Design-scope reconciliation | The current inventory omits the design-v2 empty-evidence control, full-document ceiling anchors, and matched legacy bridge | **Blocked on explicit include/drop decisions** |
| Secondary analysis | The no-debate D contrast, weighting, inference, multiplicity, and resolvability-stratified analysis are not frozen | **Blocked** |
| Calibration completeness | 385/396 Gemma cells complete; the exact 11-cell recovery list is tracked in `rejudge/calibration_recovery_gemma_2026-07-15.json` | **Decision and small-spend approval needed** |
| Query enforcement | Strict oracle parsing and a deterministic mechanical screener exist; the promised model-check/retry policy is not integrated | **Blocked on design** |
| Execution semantics | Prompts, sampling/token settings, seeds/sides, retries, placebo/batch construction, no-debate prompt, and provider pins are not frozen | **Blocked** |
| Smoke/launch gates | Canary inventory, INVALID and side-bias thresholds, evaluation rule, and failure action are not frozen | **Blocked** |
| Phase 2 execution | Offline planner enumerates the current cell identities and dependencies; no executable Phase 2 runner exists | **Blocked by design** |
| Spend control | Hardened rejudge clients use strict per-model prices, fsynced pre-call reservations, cumulative per-run ledgers, and conservative unknown-charge accounting | **Project-wide start, locking, provider/output reconciliation, cap policy, and ceiling unresolved** |
| Reproducibility | CI, static checks, immutable manifests, exact completion gates, and an 81-file / 572,844,405-byte local artifact inventory exist | **Large artifacts still lack durable retrieval URIs** |
| Security | A plaintext key file exists outside the repo | **Rotate and move to secret storage before live work** |
| Public record | The grant update and original article do not yet reflect the completed validation and mechanism revision | **Posting authorization/owner needed** |

## Decisions requiring named sign-off

Record the approver, UTC timestamp, protocol SHA-256, and Git commit with every answer.

### 1. Roster and inventory

- [ ] Approve the four judges, two debaters, and Llama-70B oracle exactly as proposed.
- [ ] Approve the 82-question main set, 3 transcripts/question/debater, debate K=2,
  no-debate K=3, and all seven conditions exactly as enumerated.
- Decision/changes:
- Approver / UTC:

### 2. Three Holm-family primary tests

One possible candidate family from the design history is:

1. sequential harm, `H = error(sequential clean b2) - error(b0)`;
2. packaging harm, `P = error(sequential clean b2) - error(batch same-Q&A b2)`;
3. residual content harm, `R = error(batch same-Q&A b2) - error(b0)`.

These quantities are algebraically related (`H = P + R`), and the repo does not establish that
they are the consult-#15 family. The lead must confirm or replace this candidate family. For each
test, freeze the population/cells, estimand and weighting, one- or two-sided null,
question-level clustering/resampling method, handling of invalid/missing cells, pooling of mirrored
side assignments, and whether the effect is pooled or heterogeneous across categorical judge and
debater identities. Confirm Holm adjustment across exactly these three tests.

- Exact definitions:
- Approver / UTC:

### 3. Capability measurement

Freeze the solo-QA question set, measured models, prompt and answer format, exact scoring rule,
replicate count, missing/invalid policy, exclusions, and the point at which scores become immutable.
Confirm that capability trends remain exploratory and do not replace categorical primary contrasts.

- Decision:
- Approver / UTC:

### 4. Cap-protection secondary

Freeze the capped-versus-uncapped contrast, debater(s), question set, transcript count/K, target
condition, inference method, and whether it is outside the Holm family.

- Decision:
- Approver / UTC:

### 5. Missing Gemma calibration cells

Choose one:

- [ ] Recover the exact 11 cells in a separately manifested supplement with a separately approved
  cap. The runner and selection file are ready; do not reuse the pre-manifest output.
- [ ] Accept 385/396 completeness with a written missingness rationale and sensitivity policy.

If recovery is authorized, use a new output directory and the exact selector (substitute only the
approved cumulative supplement cap):

```bash
uv run python -m rejudge.calibrate --judges mid_gemma --cells b0 \
  --cell-key-file rejudge/calibration_recovery_gemma_2026-07-15.json \
  --out-dir rejudge/output/gemma-recovery-2026-07-15 --workers 1 \
  --approved-cap <APPROVED_USD>
```

- Decision / maximum recovery spend:
- Approver / UTC:

### 6. Oracle-query screening

Approve the checker design: mechanical rules, any model checker and frozen prompt, audit sampling,
one-free-retry behavior, second-offense slot consumption, and failure behavior when the checker is
unavailable or ambiguous.

- Decision:
- Approver / UTC:

### 7. Reconcile the current inventory with design v2

The design-v2 source names three elements that the current 19,188-cell inventory does not include.
For each item, either approve dropping it with a scientific rationale or define its exact cells,
replicates, dependencies, analysis role, and multiplicity handling:

- empty-evidence-table control at the Llama-70B anchor;
- full-document ceiling anchors;
- matched old-versus-new Llama-70B legacy bridge, kept separate from the main grid.

- Decision for each item:
- Resulting inventory change, if any:
- Approver / UTC:

### 8. No-debate secondary and resolvability reporting

Freeze the named secondary `D = error(debate) - error(no debate)`: specify the matched debate
condition, population, judge/debater pooling, weighting across questions and transcripts, handling of
K=2 debate versus K=3 no-debate replicates, inference method, invalid/missing policy, and
multiplicity treatment.

The oracle contract also promises reporting by direct-resolvability class. Identify a tracked,
versioned label source and hash; freeze the exact stratified analysis rule; and either complete the
promised human pass over the shortcut audit or formally waive it with a rationale. The existing
ignored local audit file is not yet a durable Phase 2 input.

- D definition and analysis:
- Resolvability label source / hash and analysis rule:
- Shortcut-audit human-pass disposition:
- Approver / UTC:

### 9. Execution semantics and smoke gates

Freeze and hash the exact prompt bundle, temperatures, maximum-output and reasoning-token settings
by model/call role, seeds and side-assignment policy, retry/regeneration rules, placebo payload,
batch-same-Q&A construction, no-debate prompt, and provider endpoint/version pins.

Separately freeze the manifested canary inventory and sample size, INVALID-rate and side-bias
thresholds, how each gate is calculated, and the exact stop/escalate action after a failure. The
calibration design used INVALID <2% and side bias below approximately 10 percentage points as
precedents; those values are not automatically approved for the Phase 2 launch gate.

- Execution bundle / hashes:
- Canary inventory and sample:
- Gate thresholds / evaluation / failure action:
- Approver / UTC:

### 10. Financial boundary

- Provider-verified project spend immediately before the next call:
- Durable provider/billing evidence and local ledger source:
- Available balance/top-up:
- Does the reported $1,500 ceiling mean incremental Phase 2 spend or whole-project cumulative spend?
- Approved cumulative project ceiling:
- Approved calibration-recovery cap:
- Approved later canary cap:
- Project-wide locking and concurrent-run policy:
- Cross-invocation enforcement policy:
- Unknown/timeout charge reconciliation policy:
- Provider-to-local-ledger reconciliation rule:
- Abnormal-resume rule for charged ledger events whose cell output is missing:
- Immutable-cap amendment policy (for example, separately manifested supplements):
- Approver / UTC:

### 11. Storage, publication, and credentials

- Versioned artifact destination, retrieval policy, and backup owner:
- Canonical repository/branch and PR policy:
- Code/data license and release terms:
- Public correction/update authorized? Poster and destination:
- API credential rotated and moved to environment/secret storage? Evidence without secret value:
- Approver / UTC:

## Launch sequence after decisions

1. Resolve every null decision in `phase2_protocol.json`, reconcile the inventory with design v2,
   bump the protocol ID/cell-key namespace, record source hashes, and commit.
2. If authorized, recover or formally waive the 11 Gemma cells; rerun calibration analysis.
3. Implement the Phase 2 runner, project-wide spend lock/ledger, smoke gates, and pre-specified
   analysis against the frozen plan.
4. Validate entirely offline with fixture replays, manifest/resume tests, dependency checks, and a
   clean-clone CI run.
5. Copy and verify all required inputs/artifacts in durable storage; record retrieval URIs.
6. Rotate the API credential and reconcile provider spend to the project-wide ledger.
7. Obtain a separate, small canary authorization; run a manifested canary and review raw outputs,
   query-screen behavior, exact completeness, frozen smoke gates, and ledger/provider cost agreement.
   Apply the predeclared halt/escalation action if any gate fails.
8. Obtain a separate main-run authorization. Protocol sign-off or a successful canary alone does
   not authorize the paid main run.

## Signatures

- Protocol approved: **NO**
- Canary spend approved: **NO**
- Main-run spend approved: **NO**
- Protocol SHA-256:
- Git commit:
- Lead approver / UTC:
