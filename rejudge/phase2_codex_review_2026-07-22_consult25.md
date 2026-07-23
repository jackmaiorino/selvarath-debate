No label should be touched yet. The defensible path is a versioned, prospective amendment plus a complete sweep—not a same-version “clarification” that retroactively turns v5 into a clean gate pass.

### 1. Identity/composition adjudication

The claimed dichotomy is not airtight. These are similar, but not necessarily structural twins:

- “coalition **of seven clans**” makes the count a constitutive complement of the identity noun.
- “non-sedentary … **move … in 14 bands**” attaches the count to an independent behavioral proposition.

A general rule could therefore preserve Taran=allow and Orath=reject without inconsistency: constitutive nominal composition is bundled; counts attached to independently verifiable actions or states are joined. The fact that both sentences can be logically split does not settle the issue—almost every composition claim can be split, which is why the identity-composition exception exists.

If the owner nevertheless rules that the same convention governs both, that is legitimate only as an explicit new amendment superseding amendment 7. It is not a clarification “under” the frozen version. It must not rewrite the historical fact that v5 produced one false-allow under the labels operative when it ran.

Required safeguards:

1. Record the general semantic rule before editing any item, including scope, counterexamples, and treatment of locations, counts, actions, and constituent composition.
2. State explicitly that the ruling is non-blind and post-output. Pre-documentation helps, but does not restore blindness.
3. Freeze a search procedure and enumerate every DEV-350 identity-plus-count candidate before adjudicating the list.
4. Apply the rule uniformly to all candidates, including prompt-source items.
5. Preserve old labels and rationales; publish an old→new audit ledger and a new codebook/benchmark version.
6. Re-score every prior variant, not just v5, on both the historical labels and amended labels.
7. If any current prompt example changes class, the prompt must be corrected and treated as a new variant/run.
8. Reconcile and freeze the example-exclusion set.
9. Apply the amended rule to holdout labeling from the outset.
10. Describe any amended-DEV pass as post-hoc development evidence, never as an originally pre-specified pass.

So: amendment 7 does not make correction impossible forever, but it bars silently treating a consequential relabel as part of the original frozen experiment.

### 2. If both are reject

Choose **(a), v6**.

- v5 fails a hard gate. “The holdout is final” does not erase a development gate.
- v4 also fails a hard gate. The free-retry cushion cannot convert `.937` into a pass unless retry behavior is part of the frozen configuration and the reported metric evaluates the complete retry protocol.
- If Taran remains in v5 as an allow example, “both reject” makes v5’s prompt inconsistent with the amended codebook. Correcting or removing it necessarily creates a new configuration; the reject side cannot literally remain untouched.

Make v6 a precommitted, narrow successor to v4: general allow-recovery structures, invented examples only, no item-specific exception, fixed scored denominator, and one run. If it passes, freeze immediately. Keep the remaining runs for a predeclared fallback or stability replication, not continued label-responsive tuning.

### 3. Example-source exclusion

Exclusion is necessary to prevent literal self-grading, but the present implementation is biased for estimating allow recall and potentially invalid for comparing variants.

Because hard allows were preferentially selected as examples, the remaining allow population is easier. The resulting recall is conditional on the non-random remainder, not an unbiased DEV-350 estimate. Weighting cannot repair outcome-driven exclusion reliably.

There is also an arithmetic discrepancy: 350 − 25 = 325, not 329. A denominator of 329 means only 21 distinct DEV items were excluded—perhaps four examples were invented, duplicated, or outside DEV. Reconcile this before banking any number.

Report:

- Exact `n_allow`, `n_reject`, TP/FN/TN/FA for every variant.
- Per-variant contamination-safe scores.
- Cross-variant scores on one fixed core: the union of all distinct real DEV example sources, excluded from every variant.
- The excluded stratum’s class/category composition.
- Inclusive DEV-350 scores only as explicitly contaminated diagnostics.
- The fact that adaptive prompt development and hard-allow exclusion can inflate allow recall.

Use no more real DEV examples in future prompts; invented examples only.

### 4. Holdout

The design is basically sound, with these additions:

- Sample and score unique `question_id` clusters—not multiple correlated records counted as independent observations.
- Separate from DEV and all prompt-source clusters; preferably check semantic/template near-duplicates in addition to exact IDs.
- Randomly sample within label strata. The quota design estimates class-conditional recall, not natural prevalence.
- Freeze and hash the eligibility manifest, sampled IDs, labels, rationales, codebook version, prompt bytes, runner commit, model revision, decoding settings, parser, retry policy, and aggregation rule before inference.
- Resolve all label disagreements before sealing. No post-output exclusions or adjudications.
- Count malformed/missing outputs in the parseability denominator.
- Specify whether the free retry is part of the evaluated checker. If it is, gate the final protocol-level disposition and retain both attempts. Keep transport retries separate.
- Authorize and cap holdout spend separately if the $8 cap covers development only.
- After the one run, the holdout is consumed regardless of outcome.

At 40% reject prevalence, obtaining 149 rejects requires 372.5 candidates in expectation. Drawing 373 gives only about a 53% chance of reaching 149; approximately **412 independent eligible candidates** gives a 95% chance. Zero false-allows among 149 random rejects yields a one-sided 95% upper bound of about 1.99% on the false-allow rate.

If fewer than 149 eligible reject clusters exist, either expand the fresh pool while preserving separation or use all eligible rejects and explicitly call the result underpowered. Do not relax separation, backfill from DEV, or quietly lower the target.

Also note that zero false-allows over the same reject denominator already implies 100% reject recall; the separate ≥95% reject-recall gate is mathematically redundant unless “benchmark false-allow” refers to a narrower subset.

### 5. Interim claim

Directionally fair, but currently too clean because it omits the changing, selected denominator. A safer bankable version—after reconciling `329` versus `25 exclusions`—is:

> On the adaptively used, prompt-source-excluded DEV-350 subsets, the v4–v5 codebook-explicit Gemma 4 31B prompts achieved allow recall of .937–.963 and reject recall of .993–1.000, with 0–1 false-allows. These are development diagnostics on non-random, potentially changing scored subsets and may overstate allow recall because difficult allow items were disproportionately used as prompt examples. Residual errors are predominantly false rejects, concentrated in boundary-atomic claims. No configuration has yet unambiguously met every frozen gate under the original labels, and performance remains unconfirmed on a fresh holdout.

Crisp answers:

1. **Prospective versioned amendment: legitimate. Same-version retroactive clarification/pass: not legitimate. Forced symmetric labeling is not logically required.**
2. **Choose v6. Neither v4 nor v5 qualifies under “both reject.”**
3. **Exclusion prevents self-grading but introduces selection bias; fix the common denominator and report conditional development metrics.**
4. **Holdout plan is sound after adding unique-cluster sampling, sealing, retry semantics, and the thin-pool fallback. Plan on roughly 412 candidates at 40% reject prevalence.**
5. **The claim is usable only with the development-selection and exclusion caveats above.**

This ruling uses your self-contained record; prior governance context informed only the conservative authority boundary, not current experimental facts.


