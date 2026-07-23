Choose **(C)** as the primary disposition, but do **not** run (A) yet. Preserve those seven clusters for a possible expanded confirmatory holdout. Treat (B) as the pre-registered contingency if canary traffic cannot meet the cluster quotas.

(C) is a substantive protocol amendment: it authorizes a human-controlled query canary, not the previously blocked autonomous checker arms.

## Required canary pre-registration

### Review and runtime protocol

- Every raw proposed query must be durably logged before either disposition, including checker-rejected, `UNRESOLVED`, malformed, missing, and retry attempts.
- A named primary reviewer must label **every raw query**, not merely checker-allowed queries.
- The reviewer must be blind to the checker verdict. An interface that only presents checker-allowed queries is not blind because inclusion reveals the verdict.
- Show only the query, frozen contract/codebook, and necessary debate context. Hide checker output, arm identity where unnecessary, oracle response, subsequent outcomes, and aggregate performance.
- Human labels are `ALLOW`, `REJECT`, or `CONTRACT_AMBIGUOUS`, with a controlling clause and one-sentence rationale.
- Name a normative adjudicator before execution. Human ambiguities go to that adjudicator while still blinded. Checker–human disagreements are observations, not cases to be “adjudicated away.”
- Runtime dispatch is dual-key: the oracle receives a query only when both checker and human say `ALLOW`.
- A human-rejected checker-allowed query is blocked, recorded as `checker_false_allow_intercept`, and consumes that query slot. No silent editing, substitution, or fishing for a replacement. The debate may continue under a frozen no-query transition, but checker autonomy has failed.
- Checker `REJECT`, `UNRESOLVED`, malformed, or missing output also blocks dispatch. Any pre-existing retry must be specified in advance; both attempts remain in the validation population and the same cluster.
- Review capacity exhaustion, reviewer substitution, or unavailable adjudication fails closed.
- Canary performance remains sealed from checker developers until the frozen stopping rule fires. Any prompt, codebook, parser, model, or transport change starts a new validation epoch.

Sequential, verdict-aware review remains acceptable as a **safety veto**, but its labels are not clean confirmatory evidence. For the formal checker evaluation, blind parallel judgment is required.

### Metrics and correlation

The independence unit should be the highest shared live `question_id`, not a raw exchange or debate turn. Repeated debates, arms, retries, and all queries derived from the same underlying live question remain one cluster. Exact and semantic/template overlap with DEV-350 must be checked before inference.

Pre-register:

- Query-level false-allow rate:
  `checker ALLOW and human REJECT / all human REJECT`.
- Reject recall:
  `checker REJECT / all human REJECT`; `UNRESOLVED` does not count as correct.
- Allow recall:
  `checker ALLOW / all human ALLOW`.
- Human-intervention workload:
  `human REJECT / checker ALLOW`.
- Cluster escape:
  for each reject-bearing cluster, whether it contains at least one checker-allowed/human-rejected query.
- Equal-cluster macro rates as primary class-conditional summaries; micro/query-level rates as workload summaries.
- A cluster bootstrap with a frozen seed for macro-rate intervals, plus an exact one-sided bound for the binary cluster-escape rate.
- Counts by raw query, debate, question cluster, arm, label, retry status, and checker disposition. Freeze arm mixture weights and per-cluster query caps so large/easy arms cannot dominate.

With zero escapes across seven clusters, the one-sided 95% upper bound on the cluster-escape probability is about **34.8%**. The 247 exchanges do not repair that.

For an autonomy decision, retain the #25 standard:

- At least **149 independent reject-bearing question clusters**, with zero false-allow escapes.
- At least **100 independent allow-bearing question clusters** if retaining the prior allow target.
- All frozen parseability and recall gates met.
- No configuration or label changes during the epoch.

At 149 zero-escape clusters, the one-sided 95% upper bound is approximately 1.99%. Before those quotas, results are useful diagnostics but cannot authorize autonomy. Stop on quota, frozen calendar/spend cap, or safety stop—whichever occurs first. Class-count stopping may use blinded human labels, but not checker performance.

Do not arithmetically pool the static records holdout and canary traffic after seeing results. They are different sampling frames. Report them separately unless a weighting/meta-analysis rule is frozen now.

## The seven-cluster set

Running (A) now would be procedurally sound as an explicitly underpowered secondary diagnostic, but it has poor decision value and consumes the only remaining clean records-based clusters.

Preserve it because:

- If canary volume is insufficient, those seven clusters can join an expanded confirmatory holdout.
- If the canary exposes a configuration defect, an already-consumed seven-cluster result cannot validate the successor.
- If the canary succeeds at full volume, the seven-cluster diagnostic adds little.
- The scarce resource is pristine clusters, not the approximately $0.35.

Seal the eligibility manifest and labels, but do not expose checker outputs. Pre-register a trigger for later use, such as proceeding only if an expanded records holdout is commissioned and the checker configuration remains frozen.

There is also a flaw in the present estimate for (B): if `question_id` is the independence unit, adding 30–50 questions produces only 37–57 total fresh clusters including the existing seven. That cannot yield 149 independent reject-bearing clusters. The expansion estimate must be recomputed at the question-cluster level; multiple exchanges generated from one new question do not multiply independence.

## Canary amendment language

A suitable core provision is:

> Query-producing canary arms are authorized solely as human-gated arms. Every raw query candidate shall receive an independently blinded human contract disposition before oracle access. An oracle call is authorized only when both the frozen checker and the human reviewer return ALLOW. All other dispositions produce a logged no-query outcome; no silent replacement or editing is permitted. All raw candidates remain in the validation population. These arms estimate the human-gated operational policy and do not authorize an autonomous query arm or the main run. Removal of the human gate requires completion of the prospectively frozen canary validation gates. Any change to the checker, prompt, parser, codebook, transport, review protocol, or aggregation rule begins a new validation epoch.

Also state explicitly:

- The original autonomous query arms remain blocked.
- Human-gated query arms are scientifically different arm versions.
- Blocked slots remain in intent-to-treat analysis.
- Results cannot tune the checker and simultaneously validate that tuned checker.
- Pre-registration does not authorize canary execution or spend.
- The owner must ratify the amendment and separately approve the canary budget and human-review commitment.

## Spend and authority

No objection to treating the owner’s existing `≤$1.50` approval as covering (A) **if** it is executed as the #25 thin-pool fallback: the same frozen configuration, all seven eligible clusters, one run, underpowered reporting, and cumulative holdout spend within $1.50. Calling it a secondary diagnostic must not turn it into an additional experiment alongside another purportedly covered holdout.

Free drafting and sealing work requires no model-spend authorization. The existing approval does **not** cover:

- Canary checker or oracle spend.
- Human-review operational commitment.
- New-question/debate generation under (B).
- A later expanded holdout exceeding or replacing the approved scope.

My recommendation remains to spend none of the holdout authorization now and preserve the seven clusters.

## Crisp answers

1. **Disposition:** (C) now, under a prospective blind human gate; (B) only if the canary misses its cluster quotas. Preserve (A) rather than running it immediately.

2. **Blindness:** Blind parallel human judgment of every raw candidate is required for formal validation. Verdict-aware sequential review is sufficient only as an operational veto.

3. **(A) alongside (C):** Sound but strategically inferior. It burns scarce clean clusters for a nearly vacuous bound; defer it.

4. **Query arms:** Yes, they are pre-registerable now—but only as newly defined human-gated arms. The original autonomous arms and main run remain blocked.

5. **Authority:** The existing `≤$1.50` approval can cover (A) as the declared thin-pool holdout fallback. It covers neither canary execution nor (B); both require separate approval.


