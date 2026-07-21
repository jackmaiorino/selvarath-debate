The halt is valid and must remain in force. The run decisively rejects the tested configurations, but it does not establish that these model families cannot implement the contract under a materially better prompt.

Your proposed sentence is slightly too broad. The bankable result is narrower:

> On the frozen, hashed benchmark, none of the four tested model-endpoint configurations met the preregistered gates under the frozen one-line prompt. The strongest candidate, Gemma, allowed 54 of 145 benchmark rejects. Therefore no checker was selected and no query-producing checker call is authorized.

“Open-weight checker” implies a broader capability claim than one prompt and four endpoints support. “Not allow recall” is also too broad: Qwen’s 0.42 and Llama’s 0.82 are real allow-side deficiencies. For the leading candidates, however, reject discrimination—especially on real-query cases—was plainly the dominant problem.

### Adversarial interpretation

I endorse R1 strongly at the configuration level, R2 as unresolved, and R3 only as a gate-design observation.

R1 explains the magnitude. Gemma’s 54 false allows are 37% of the reject set. Relaxing zero false allows to 2% would permit only 2 of 145, so gate stringency is nowhere near sufficient to explain this failure. The one-line prompt appears to omit distinctions the gold labels depend on. That shows the prompt/configuration is inadequate; it does not yet show the models are incapable.

R2 must be resolved before prompt engineering. Agreement among models is not evidence of gold-label validity: the models are neither independent judges nor authorities on the contract. Conversely, disagreement among them does not validate the labels. Owner approval of an amendment bundle is weaker than blind, item-level owner interpretation unless Jack actually reviewed the disputed examples against the contract.

R3 is statistically correct but rhetorically overstated. Assuming independent 1% false-allow probability, the chance of at least one false allow in 145 rejects is:

\[
1 - 0.99^{145} \approx 0.77
\]

But the gate does not “guarantee” failure. It intentionally demands effectively perfect observed safety behavior. Also, the probabilistic framing is about sampling from an error distribution, not temperature-induced randomness. The important governance question is whether zero observed false allows was intended as:

- A deliberately stringent benchmark acceptance test, or
- Evidence that the underlying false-allow rate is zero.

It can serve the first purpose. It cannot prove the second: even 0/145 has a one-sided 95% upper bound of about 2.05%.

Also, `parseable = 1.00` only establishes syntactic compliance with the three-token output protocol. Qwen’s many `unresolved` outputs demonstrate that it does not establish binary coverage or useful decisiveness.

### Recommended disposition

Use a combination of (a) and a strengthened version of (c). Do not adopt (b) as a rescue amendment based on these results.

The correct sequence is:

1. Audit the benchmark against the underlying contract.
2. Freeze any contract/codebook corrections and relabel affected items.
3. Treat the existing 350 as a development/regression set.
4. Develop the richer prompt on that set.
5. Freeze one model/prompt pair and validate it once on a fresh, cluster-separated holdout.
6. If that fails, remain halted or use mechanical screening plus 100% human approval—not sampling—for every checker-allowed or unresolved query.

Reuse of the 350 is not “contamination” because the models remember having answered it. It is contamination because the experimenters now know aggregate weaknesses and may inspect item-level errors. The set is perfectly usable for development, but no longer supplies independent confirmation.

A defensible amendment would say:

- The existing 350-item set is redesignated `DEV-350` and may not support the final deployment-gate claim.
- The contract, operative codebook, label policy, category definitions, and handling of `unresolved` must be frozen before prompt development begins.
- Few-shot examples must come from `DEV-350` or a separately designated training set.
- No validation item, paraphrase, source-record sibling, or near-duplicate claim cluster may appear in prompt examples.
- Prompt development has a predeclared maximum number of variants and runs.
- After development, exactly one model, one prompt, one parser, and one decoding/transport configuration are frozen by hash.
- Final validation uses fresh records, split at the source-record/claim-cluster level, with labels produced before any model outputs are opened.
- The holdout is queried once. If it fails, it becomes development data; another attempt requires another fresh holdout and amendment.
- Labels, gates, scoring rules, subgroup definitions, retry acceptance, and excluded-item rules may not be changed after holdout outputs are seen.
- No candidate-specific prompt, selective relabeling of adverse examples, subgroup removal, or model-specific threshold is permitted.

I would retain the original safety gates for the next confirmatory attempt unless the operational control changes. If every `ALLOW` and `UNRESOLVED` is reviewed by a human before materialization, the LLM becomes a triage tool and its false-allow threshold may rationally become a workload/quality metric. If human review remains sampling-only, replacing zero false allows with 2% is a substantive safety relaxation, not a statistical cleanup.

If a 2% population-rate target is ever adopted, specify whether it means a point estimate or a one-sided confidence bound. For a one-sided exact 95% upper bound no greater than 2%, at least 149 reject examples with zero false allows are needed. The current 145 is just short.

### Benchmark audit

Yes: require a blind human audit before prompt iteration and before any gate relaxation.

A practical 40-item diagnostic sample:

- 28 real benchmark rejects: 7 from each of the four difficult families.
- 8 real benchmark allows: four uniformly sampled and four boundary-adjacent examples selected without using model outputs.
- 4 synthetic rejects, uniformly sampled.

Selection should use a committed seed and benchmark hash. Overlapping rejects need a frozen primary-family assignment. Jack should see only the item and underlying contract—not the existing label, source class, family annotation, candidate outputs, or disagreement statistics—and assign:

- `ALLOW`
- `REJECT`
- `CONTRACT_AMBIGUOUS`

Each decision should cite the controlling contract clause and include a one-sentence rationale. Ideally, a second human labels independently; Jack remains the normative adjudicator.

This is a defect-discovery audit, not a precise error-rate estimate. Even 0 disagreements in 40 leaves a one-sided 95% upper bound of roughly 7.2%. Any contract ambiguity or systematic rule disagreement requires clarification and full relabeling of the affected family before prompt work. Multiple non-systematic disagreements across families should trigger a full benchmark re-audit.

### Canary

I would preregister a narrowly separated non-query canary now, provided it has no carryover or shared-state effect on the later query-producing conditions.

The amendment should authorize only `b0`/batch/placebo conditions using already frozen, pre-supplied Q&A. It must explicitly prohibit:

- Query generation
- Checker calls
- New query materialization
- Substitution of those results for validation of the blocked query-producing arms
- Using scientific outcomes to tune the checker prompt, labels, or gates

Operational observables can be inspected, but scientific results should remain quarantined until the checker design is frozen. The full query-producing canary remains halted. If running these conditions would change debater state, reuse participants, disturb randomization, or create temporal incomparability, wait instead.

### Run-mechanics caveats

Nothing described invalidates the aggregate result, subject to four checks:

- Every `(candidate, item_id)` must map to exactly one accepted response, with no content-based choice between a late original response and a retry.
- Streaming and retry pins must not have changed the semantic request payload. If Gemma’s completed items used materially different model parameters or aliases, report the result as a mixed transport/configuration run.
- Record timestamps, provider model aliases/revisions, and payload hashes. Putting Gemma last is harmless unless the provider endpoint changed over time.
- Describe `$0.634` as conservative accounted spend unless it has been reconciled against provider billing. Unknown-charge reservations affect cost certainty, not classification validity.

The split claims should also be accompanied by exact per-stratum confusion matrices and denominators. Rounded recall summaries are insufficient for auditing whether all synthetic-versus-real comparisons use the same eligible populations.

### Crisp answers

1. **Interpretation:** R1 is supported for the four tested configurations; R2 remains live; R3 explains gate harshness but not the enormous observed gap. Narrow the claim to the frozen benchmark, prompt, endpoints, and gates.

2. **Disposition:** Audit labels first; use the current 350 only for prompt development; validate one frozen model/prompt pair once on a fresh cluster-separated holdout. Do not relax to 2% merely because all candidates failed. Mechanical screening is acceptable only with 100% human approval of non-rejected queries.

3. **Benchmark validity:** Yes, require a blind 40-item, real-reject-heavy owner audit. Treat it as ambiguity/systematic-error discovery, not proof of a low benchmark error rate.

4. **Canary:** Pre-register only the separable, pre-supplied-Q&A conditions now, with no query production and quarantined outcomes. Keep every query-producing arm blocked.

5. **Mechanics:** Timeouts, reshuffling, and conservative charge booking do not invalidate the result if response acceptance was unique and non-selective and request semantics stayed frozen. Caveat provider-time/version drift and report spend as conservative rather than exact.
