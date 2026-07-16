# Phase 2 Decision Proposal

**Prepared:** 2026-07-16

**Status:** **NON-BINDING DRAFT — not a preregistration or execution authorization**

**Authoritative blocker record:** `docs/phase2-readiness-and-signoff.md`

This memo proposes repo-grounded defaults for the nine unresolved sections in
`rejudge/phase2_protocol.json`. Nothing here fills those null fields. The lead must approve or
replace the choices, after which the protocol ID, cell namespace, source hashes, inventory, and
cost model must be regenerated and committed before implementation.

## Four choices that cannot be inferred from the repo

1. Is Phase 2 confirmatory about a pooled decomposition of verification harm, or about how harm
   changes with judge/debater capability? The current base grid supports either framing, but they
   require different primary tests.
2. Which additional design-v2 arms justify their cost: capped cap-protection cells, empty-table
   control, full-document ceilings, and the descriptive legacy bridge?
3. Who owns the human query-screen validation/audit, and which checker is acceptable after that
   validation?
4. Is the reported $1,500 ceiling incremental Phase 2 spend, or the whole-project cumulative cap?

## Recommended defaults if pooled decomposition is the confirmatory question

### 1. Primary family

Use exactly three two-sided, question-clustered tests with Holm adjustment:

- `H = error(sequential clean b2) - error(b0)` — total limited-verification harm;
- `P = error(sequential clean b2) - error(batch same-Q&A b2)` — packaging/commitment harm;
- `R = error(batch same-Q&A b2) - error(b0)` — residual content harm.

These are a decomposition with `H = P + R`, not three independent replications. Holm permits
dependent tests, but the preregistration should name H as the total primary and P/R as co-primary
components. This is a proposed replacement family; the missing consult-#15 definitions are not
recoverable from the repo.

Use all 82 main questions. Average replicates/transcripts within question and weight questions,
judges, and debaters equally. Use common world-stratified question-bootstrap draws for all three
tests. Count strict `INVALID` as wrong in the primary analysis, with a valid-only sensitivity, and
require exact provider-cell completion. Report every judge×debater cell and categorical
heterogeneity descriptively.

If capability heterogeneity remains the confirmatory question, do not approve this family: design
a capability-focused family explicitly instead of quietly interpreting pooled H/P/R as slopes.

### 2. Capability measurement

Run full-document solo QA for the five unique roster models (the four judges plus Qwen3.7-Plus) on
all 106 questions, K=2 with mirrored A/B labels, temperature 0, strict exact-choice parsing, invalid
counted wrong, and equal question weighting. Freeze prompts, raw records, scores, hashes, and
exclusions before opening Phase 2 outcome data. Report the 82-main-question score as a sensitivity
because the other 24 questions informed calibration.

Keep model identity categorical in confirmatory analyses. Treat capability score and
judge/debater capability-gap trends as exploratory unless the primary family is redesigned.

### 3. Cap-protection secondary

The clean replication of the calibration result is the cap-by-debater interaction, not a lone
capped-versus-uncapped strong-debater contrast:

`C = (QwenPlus uncapped - QwenPlus capped150) - (Llama70 uncapped - Llama70 capped150)`.

Measure C at the Llama-70B judge, b0, across all 82 main questions, both debaters, three
transcripts/question, and K=2 mirrored; use two-sided question-cluster inference. Put C and the
named no-debate D in a separate two-test Holm secondary family. This requires a full capped
transcript block for both debaters and must be added to the inventory and cost model. A cheaper
QwenPlus-only contrast does not replicate the interaction.

### 4. Query screening

Retain the frozen query contract and do not launch with only the mechanical screener. Validate
candidate checker/prompt pairs on a frozen human-labeled set of historical and adversarial atomic,
compound, and meta queries. Use one checker and prompt across all cells.

Interpret the contract as one free retry per attempted budget slot; a second rejection consumes
the slot. Checker outage, malformed output, or unresolved ambiguity halts the cell without silently
allowing or rejecting the query. Human-review all rejections/retries plus a deterministic,
world/resolvability-stratified 1% sample of accepted queries, blind to outcomes. If no checker meets
the frozen validation target, amend the contract before generation.

### 5. Design-v2 scope

- Include an empty-evidence-table control at the Llama-70B judge/Llama-70B debater anchor. This
  separates evidence packaging from the batch header/prompt itself; keep it diagnostic and outside
  multiplicity.
- Include two descriptive full-document gold-context ceilings: Llama70/Llama70 and the highest
  pre-frozen solo-QA judge against Qwen3.7-Plus. These are not oracle-budget conditions.
- Include old-versus-new Llama70/Llama70 b0 and sequential-b2 cohorts only if the project intends
  quantitative cross-phase comparison. Otherwise drop the bridge explicitly and prohibit that
  comparison.

The legacy bridge is descriptive, not a causal estimate of the opponent-leak fix: regeneration
changes more than leak status. A causal test would require newly generated matched leaky/blind
transcripts with common seeds.

### 6. No-debate D and resolvability

Define the named secondary as

`D_clean = error(debate sequential clean b2) - error(no-debate clean b2)`.

Pool with equal judge, debater, and question weights; average K=2 debate and K=3 no-debate
replicates within question without duplicating the shared no-debate comparator. Use question-cluster
inference. Report matched b0 and placebo D contrasts as descriptive sensitivities.

Complete the promised human pass over all 106 shortcut-audit questions. Track a versioned label
file with question ID, full/partial/none class, rubric decision, reviewer, input hashes, and UTC
timestamp, then hash it into the protocol. Stratify H/P/R/D descriptively without filtering; the
full class is too small for a separately powered confirmatory test. Waiving the human pass would
require amending the oracle contract and dropping any claim that the current classes are
human-validated.

### 7. Execution semantics

Create and hash a dedicated Phase 2 prompt bundle rather than dynamically rewriting pilot prompts.
It must include debate, sequential judge, query checker, oracle, placebo, batch, no-debate,
empty-table, full-document, and legacy templates.

Continuity defaults are debater temperature 0.7, judge/query/verdict 0.3, and oracle/checker 0.
Freeze explicit per-model/per-role output and reasoning limits; do not rely on an implicit model-name
prefix heuristic. Seeds should include protocol namespace, question, debater, transcript, judge,
condition, replicate, call role, and attempt. Mirror A/B for K=2 and hold labels fixed across matched
conditions.

Transport retries repeat the identical request and seed and remain conservatively charged until
provider reconciliation. Do not regenerate an invalid verdict. Query-screen retry is the only
semantic retry. Preserve the existing exact placebo payload. Batch uses the matched sequential
cell's raw extracted claims and normalized replies, in original order and fresh context; shuffling
is sensitivity-only. Give no-debate a dedicated prompt rather than an empty-transcript placeholder.

Pin exact model IDs, endpoint/API mode, SDK/lockfile version, request fields, and price-catalog
verification time. Record any provider-returned model/backend fingerprint.

### 8. Canary and launch gates

Use only the 24 main-excluded calibration questions through the final runner. A defensible canary is:

- b0 on all 24 questions × both debaters × one preselected uncapped transcript × K=2 mirrored for
  each judge;
- a frozen six-question world/resolvability-stratified subset through sequential b2, batch,
  placebo, and all no-debate conditions;
- at least one cell through every approved optional arm and every checker outcome.

Require strict `INVALID < 2%` and absolute mirrored b0 side bias `<= 10pp` per judge, 100% exact
completion, and zero prompt truncations, query-screen bypasses, malformed oracle normalizations,
dependency mismatches, or unexplained ledger/provider discrepancies. Any failure stops before main
spend. A semantic repair requires a new protocol ID/hash and a complete canary rerun; never swap a
model after seeing main outcomes.

### 9. Cumulative spending

Verify provider spend immediately before the next call; `$208` is a historical report, not current
billing evidence. A reasonable reading is a $1,500 incremental Phase 2 ceiling, making the
whole-project cap `verified starting spend + $1,500`, but only the lead/funder can authorize that
interpretation.

Calibration recovery, capability measurement, canary, optional arms, retries, and main cells should
all be subcaps inside the Phase 2 ceiling. Re-cost the reconciled inventory from observed
model/role token distributions with a reasoning/timeout margin; reduce scope before sign-off if the
upper estimate exceeds the ceiling.

Implement one project-wide ledger and exclusive live-run owner; concurrency may occur only within
that owner. Unknown/timeout reservations count until provider evidence resolves them, and any
provider/local disagreement halts work. Existing caps are immutable. New authority creates a
separately manifested, linked supplement rather than raising an old ceiling.

## Approval sequence

1. Lead answers the four irreducible choices and accepts/edits the defaults above.
2. Complete/waive the 11-cell Gemma recovery and the 106-question human resolvability pass.
3. Rebuild the machine inventory and conservative cost model, then approve the project-wide cap.
4. Freeze/hash the protocol and prompt bundle under a new ID and commit.
5. Implement and validate the Phase 2 runner entirely offline.
6. Separately authorize the canary; main spend still requires a second approval after gate review.
