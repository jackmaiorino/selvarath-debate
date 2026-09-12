# Phase 4: explain and test transfer of verification harm

**Proposed for review, 2026-09-12. Preparation is authorized; paid execution is not.**

Execution update: Jack subsequently approved **4A under the $250 cap**. This original design is retained; the [execution record and runbook](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/docs/phase4a-operations.md) govern launch and recovery. Later stages remain conditional and separately funded.

**Question.** Does the Qwen/Llama difference follow the verification history supplied to a judge, the receiving judge's response, or their combination? If an intervention explains part of the difference, does its benefit transfer to new model families and tasks? Phase 3 remains a result for two fixed endpoints and three worlds; Llama has not passed a no-harm test.

**4A, captured-history crossover.** Both original judges receive each of three inputs for the same debate: an empty verification history, Qwen's saved eight-slot history, and Llama's saved eight-slot history. All final verdicts are fresh. Preserve the archived conversation exactly, including retries, blocked slots and repeated summaries, removing only the saved final verdict. Both recipients receive identical input bytes for each history. Gold answers and source-model labels are not supplied.

Use all **82 questions**, two debater sources, two deterministically selected transcripts and both mirrored sides: **656 matched units and 3,936 verdict calls**. Exclude the two incomplete source mirror pairs before selection; every question/debater stratum still has two eligible transcripts. The selection seed is fixed without using old correctness or case labels. This stage requires no new debate, query, oracle, checker or gate-reviewer calls.

**Frozen comparisons.** Let E(j,h) be a recipient's error rate on an input history. The two co-primary contrasts are:

- **Recipient difference:** average history-minus-empty effect for Qwen minus that for Llama.
- **History-source difference:** Qwen-history minus Llama-history error, averaged across both recipients.

Use a two-sided Holm family of two at 0.05, with 10,000 common question-cluster bootstrap draws stratified by world. A follow-up signal also needs an absolute integer contrast numerator of at least **40/1312**, a **3.049 percentage-point point estimate**. This is not proof the true effect exceeds three points. Report all six arm means, the interaction and subgroup patterns descriptively. A null is inconclusive; no equivalence or universal model claim is planned. Historical variance suggests this frame can resolve large differences, not reliably exclude small ones.

**Interpretation.** A recipient difference points toward how the configurations respond to identical history distributions. A source difference points toward the supplied history package, which combines query choice, reply quality, length, ordering and feedback. Neither identifies an internal neural cause or the effect of changing live query selection. Interaction can prevent a single history-quality ranking; inspect all cells even when marginal contrasts cancel.

**4B, conditional repair.** Advance only if a 4A primary passes the specified effect, significance and invalid-output sensitivity rules. Adjudicate oracle labels without seeing outcomes or source identity. Compare fresh original versus corrected-label verdicts on the same captured histories; preserve everything else. This tests final-readout label repair, not corrected live querying. Insufficient unambiguous corrections or edited-history coverage stop this branch. Adjudication and recipient evaluation each need a separate scope and cap before spending.

**4C, conditional transfer.** Advance only after a positive pooled 4B repair benefit passes the specified gates. Test that repair on two prespecified new model families, retaining the two original judges as anchors, and 48 questions across two newly authored worlds. Review answer keys before evaluation. Freeze a directional prediction, exact endpoints, new corpus and separate budget before transfer outcomes. Report each new model's result alongside the average; a pooled benefit cannot establish benefit for both families.

**Cost and operation.** 4A forecast: approximately **$50 successful usage plus $7 uncertain-delivery allowance** from historical tokens. Propose a separate **$250 hard cap**, including $5 preflight and $25 uncertain delivery. Plan 2–4 hours with healthy endpoints, not a deadline. Resume missing requests after crashes, preserve completed responses, account for every attempt, parallelize within measured limits, and stop before exceeding the cap. A 10-minute monitor is created only when execution starts.

Full analysis, branch rules and limits: [protocol JSON](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/rejudge/phase4_protocol_v1.json). Inputs, pricing evidence and implementation readiness: [preparation record](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/reports/phase4-preparation-2026-09-12/README.md).
