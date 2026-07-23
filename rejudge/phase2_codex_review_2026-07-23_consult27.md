## Ruling

Conditional assent. Under the owner’s fixed no-blind-human-at-volume constraint, the proposed checker plus metadata-blinded Claude gate is the best defensible containment design for this canary.

I dissent from describing it as equivalent to checker plus blind human, independently validated, or eligible to earn checker autonomy. It is two automated gates with correlated provenance: Claude authored the codebook labels, influenced checker development, and supplies the runtime second decision.

The canary may proceed if the amendment makes that limitation explicit and implements the controls below. Main-run authorization does not follow.

### Principal attacks on the proposal

- “CLAUDE-BLIND” overstates the blindness. Claude can be blinded to metadata and outcomes, but not to query content, learned codebook judgments, or known worlds. Call it a **metadata-blinded Claude policy gate**.
- The post-hoc audit does not retroactively protect a contaminated run. It can detect and bound errors relative to the owner. “The run stands” should mean the artifacts remain usable—not that scientific or safety claims survive an adverse or declined audit.
- Sixty labels provide only a sentinel audit. They cannot establish a low violation rate.
- A pooled “exact CI” is invalid after disproportionate stratified sampling. The analysis must use stratum weights and finite-population methods.
- Exact retries must not give Claude another chance to flip from non-ALLOW to ALLOW. Reuse the committed label for an identical payload.
- Checker-versus-Claude results are consistency measurements, not false-accept/false-reject measurements.

## 1. Operational adequacy and blindness

Yes, conditionally: the Claude gate is adequate as a canary containment mechanism, but not as independent validation of the contract or checker.

Required rules:

- Freeze and hash the codebook, reviewer prompt, model/build identifier, decoding parameters, parser, and failure behavior before execution.
- Give Claude only the immutable query payload, the frozen codebook, and the minimum context strictly required by the codebook.
- Exclude checker verdicts, scores, rationales, arm, clean/placebo mode, judge identity, debate outcome, oracle answer, dispatch decision, stage results, DEV-350 labels/rationales, and prior adjudication notes.
- Use fresh isolated reviewer contexts with no repository access, tools, retrieval, conversation memory, or cross-query discussion.
- Present clean and placebo queries in the same metadata-free queue, shuffled within each causally available stage. Do not expose queue boundaries or meaningful IDs.
- Require `ALLOW`, `REJECT`, or `CONTRACT_AMBIGUOUS`, plus clause and short rationale. Parse failure, timeout, missing clause, reviewer unavailability, or ambiguity is non-ALLOW.
- Commit the Claude decision immutably before the checker/Claude join and before any oracle dispatch. No result-dependent relabeling.
- An exact retry inherits the original decision by payload hash. A changed payload receives a new blinded decision.
- Identical queries appearing in different arms should share the same committed decision.
- Claude adjudicates contract compliance only—not truth, usefulness, likely debate benefit, or whether the oracle’s answer would change the result.
- Log any accidental metadata leak as a protocol deviation; affected queries fail closed unless the amendment expressly specifies otherwise.

Because content can reveal a world or suggest an arm, the honest claim is “metadata-blinded,” not “unable to infer mode.”

## 2. Owner audit: sample, estimator, and bounds

Sixty is reasonable as the maximum feasible sentinel audit. It is not enough for strong validation, especially if clause-level rationales are expected in only 20–30 minutes. Allow closer to the full 60-minute ceiling or reduce the fixed sample prospectively; do not stop early based on emerging labels.

### Recommended allocation

Use these mutually exclusive strata:

1. Real-oracle, checker-ALLOW, Claude-ALLOW dispatches.
2. Placebo, checker-ALLOW, Claude-ALLOW simulated dispatches.
3. Checker-ALLOW, Claude-REJECT.
4. Checker-ALLOW, Claude-AMBIGUOUS.
5. Checker-REJECT, Claude-ALLOW.
6. Checker-REJECT, Claude-REJECT.
7. Checker-REJECT, Claude-AMBIGUOUS.

Draw:

- 40 seeded SRS-without-replacement observations from stratum 1.
- From the remaining 20, take at least two from every nonempty stratum 2–7.
- Allocate remaining slots proportional to the realized stratum sizes.
- Generate the packet before showing the owner any canary results. The owner sees none of the strata, prior labels, modes, outcomes, or IDs carrying meaning.

Prefer unique immutable query-decision clusters as the audit units. Do not count multiple identical retries as independent evidence. Report a secondary transmission-weighted estimate using each cluster’s retry/dispatch multiplicity. If an exact raw-transmission bound is desired instead, sample directly from raw dispatch records and accept that duplicates may waste owner effort.

### Estimands

Map owner `AMBIGUOUS` to non-ALLOW for operational safety, while retaining the full three-way confusion matrix.

For stratum \(h\):

- \(N_h\): population size.
- \(n_h\): audit sample size.
- \(x_h\): owner–Claude binary disagreements.
- \(W_h=N_h/N\).
- \(\hat p_h=x_h/n_h\).

Overall Claude-key disagreement:

\[
\hat p=\sum_h W_h\hat p_h
\]

Report separately:

- Claude false-ALLOW relative to owner: Claude ALLOW, owner non-ALLOW.
- Claude overblocking: Claude non-ALLOW, owner ALLOW.
- Three-way exact-label disagreement.
- Among actual double-ALLOW dispatches, owner non-ALLOW prevalence.

Do not pool the 60 and apply an ordinary binomial interval. For the overall stratified rate, either:

- invert the joint finite-population hypergeometric distribution, or
- use per-stratum exact hypergeometric intervals with simultaneous Bonferroni coverage, then weight their endpoints.

Call the latter a **conservative stratified 95% interval**, not an exact pooled CI.

For the 40 dispatched observations, use an exact finite-population hypergeometric upper bound. With zero observed violations:

| Audited dispatches | Conservative one-sided 95% upper bound |
|---:|---:|
| 30 | 9.50% |
| 36 | 7.98% |
| 40 | 7.22% |
| 60 | 4.87% |
| 149 | 1.99% |

If the finite dispatch population were exactly 336, sampling 40 with zero violations gives an exact finite-population upper bound of approximately 22/336 = 6.55%. Use the actual realized population size in the final calculation.

Thus, 40 clean samples cannot support a claim such as “violations are below 5%.” To reach a conservative 5% upper bound with zero violations requires approximately 59 dispatched samples, leaving almost no audit capacity for the other strata.

### Honest writeup template

> In the realized frozen canary population of \(N\) unique query-decision units, a pre-seeded, metadata-blind owner audit sampled \(n\) units using the preregistered stratified design. The estimated binary policy disagreement between the Claude gate and owner was \(\hat p\), with [specified weighted finite-population interval]. Among \(N_D\) actual real-oracle double-ALLOW dispatches, the owner audited \(n_D\) and judged \(x_D\) non-ALLOW; the exact one-sided 95% finite-population upper bound was \(U_D\). These estimates treat the owner’s blinded interpretation as the reference. They do not validate the checker independently, prove zero contract violations, measure performance on the main-run distribution, or eliminate content-dependent selection bias.

If the owner declines:

> The canary completed under two automated, correlated policy gates. No independent owner audit was completed; therefore no owner-referenced gate-error estimate or dispatched-query violation bound is available.

A declined or adverse audit should leave the raw run preserved but prevent claims that contract safety was independently audited or that this canary unlocks the main run.

## 3. Amendment requirements

Make the amendment prospective, append-only, and canary-specific. It should record:

- The owner’s dated instruction verbatim, including the repeated refusal of volume labeling, selection of full-canary scope A, and approval of the approximately USD 40 cap.
- The exact frozen canary manifest and hashes to which the amendment applies.
- That it supersedes only the every-query blind-human key and autonomy-earning provisions for this epoch.
- The Claude-gate identity, blindness controls, retry behavior, logging, fail-closed behavior, and audit procedure.
- That checker/Claude agreement is consistency evidence only.
- That approving the canary does not authorize the 19,680-query main run.
- The result/audit status vocabulary: `AUDITED`, `UNAUDITED_OWNER_DECLINED`, `AUDIT_INCOMPLETE`, or `AUDIT_SAFETY_CONCERN`.

Record Consult #27 as:

> **Conditional assent:** execution of the frozen canary with a metadata-blinded Claude second gate is the best available containment design under the fixed owner constraint.  
> **Dissent:** the Claude gate is not an independent blind-human key; checker agreement does not validate either gate, does not satisfy the 149-cluster autonomy requirement, and does not authorize the main run.

The seven-cluster reserve and prior artifacts remain unchanged, sealed, and dormant. Do not consume, relabel, reallocate, or rewrite them. If their only intended use was autonomy validation, mark that use `NOT_EVALUATED_THIS_EPOCH`.

Preserve the 149-cluster quota text historically, but set its current status to `SUSPENDED/NOT_EVALUATED`. No Claude-reviewed cluster increments it. Preserve any genuinely independent pre-amendment count; otherwise the eligible increment for this epoch is zero. Do not call the quota failed or satisfied.

## 4. Placebo arm

Yes, placebo needs the identical Claude review for scientific comparability even though it presents no oracle-safety risk.

The same gate must control the same state transition:

- Double-ALLOW invokes the placebo responder/simulated dispatch.
- Any other combination produces the same blocked-query transition used in clean mode.
- Logging, ordering, reviewer prompt, timeout behavior, and retry commitment are identical.
- Claude must receive no mode indicator and must not see separate clean/placebo queues.

If placebo is always allowed to continue regardless of the gate, only review workload is comparable; turn structure and evidence availability are not.

Placebo observations do not belong in the real-dispatch contract-violation bound, but they do belong in comparability and overall consistency analyses.

## 5. Threats to debate-science validity

Yes. The runtime labeler can shape which facts become available in content-dependent ways. Because Claude knows the worlds and has seen Stage-1 results, implicit judgments may correlate with question difficulty, fact type, debate trajectory, or anticipated usefulness even without explicit arm metadata.

That changes the estimand. The result is about **the debate system with this frozen Claude gate**, not ungated oracle access and not the previously ratified human-gated system.

Mitigations:

- Declare the labeler’s prior world and Stage-1 exposure as an unavoidable conflict.
- Freeze the policy before the run and forbid outcome/helpfulness reasoning.
- Reuse decisions for identical cross-arm queries.
- Analyze all randomized cells intention-to-treat; never condition outcome analysis on whether the gate dispatched.
- Report gate pass, intercept, ambiguity, and clause distributions by arm, stage, held-out question, and judge after unblinding.
- Report realized oracle exposure as a mediator, not as a basis for excluding cells.
- Use question/cluster-aware uncertainty; retries are not independent observations.
- Pre-register a sensitivity analysis for arm imbalance in gate rates and for queries overlapping DEV-350 or prior Stage-1 material.
- Do not interpret equal overall allow rates as proof of non-differential selection; semantic composition can differ despite equal totals.
- Treat a reviewer model/build change mid-canary as a protocol deviation.

## Crisp answers

1. **Operationally adequate?** Yes, as metadata-blinded automated containment; no, as an independent human-equivalent validation key.
2. **Sixty labels?** Reasonable for a sentinel audit. Put about 40 on actual dispatches and 20 across disagreement, ambiguity, checker-reject, and placebo strata. Zero errors among 40 still permits roughly a 7% one-sided upper bound.
3. **Amendment?** Scoped, prospective, append-only, and explicit about the owner’s instruction and this conditional assent/dissent. Seven-cluster reserve untouched; 149 quota suspended and not incremented.
4. **Placebo?** Yes—identical gate and state transition, with clean/placebo mode hidden from Claude.
5. **Debate-validity threat?** Yes—content-dependent evidence selection. Freeze and isolate the gate, use ITT analysis, report differential gating, and define the estimand as the system with this gate.

I treated the owner instruction quoted in this prompt as the governing current evidence; prior project memory was used only for the standing rule that scope-changing authority must come from the actual owner.


