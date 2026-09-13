# Existing judge explanations, 2026-09-13

All 26,892 completed judge verdicts in the audited Phase 3, 4A, 4B recipient and Phase 5 main results contain a nonempty visible rationale and confidence from 1 to 5. No new calls are needed to begin a qualitative audit.

| Phase | Completed verdicts | Visible rationale | Confidence 1-5 |
|---|---:|---:|---:|
| phase3 | 9,836 | 9,836 | 9,836 |
| phase4a | 3,936 | 3,936 | 3,936 |
| phase4b_recipients | 5,248 | 5,248 | 5,248 |
| phase5 | 7,872 | 7,872 | 7,872 |

Phase 3 has four additional terminal-invalid planned cells, which have no completed verdict and are excluded from the 9,836 count. Phase 4B adjudicator explanations are a different role and excluded.

The final JSONL text is the user-visible brief `REASONING` response, not the separately returned provider reasoning field. The SQLite raw responses additionally preserve nonempty Qwen `reasoning_content` for all 8,528 main Phase 4A/4B/5 Qwen verdicts. Llama has zero nonempty corresponding fields in these stores. This asymmetric availability makes the common final rationale the better primary comparison. Neither output proves the actual cause of a choice.

There is no standardized source quotation or evidence-ID field in these final verdict schemas. Some explanations refer to verified facts in prose. Confidence is an ordinal self-report, not an established probability; Phase 5 Llama used 4 or 5 on 3,927/3,936 responses and Qwen on 2,012/3,936. Those differences alone do not establish better calibration.

Three illustrative saved cases, from the previous deterministic outcome-stratified audit:

| Case | Source and verification | What the final rationale says |
|---|---|---|
| Qwen, VS-022, case 01 | Source says near-total food imports, but does not attribute nearly all imports to one supplier. Oracle says NO to that stronger supplier-specific claim. | The rationale drops the broader dependence premise: "Without that near-total dependence". This is a candidate scope error, with oracle ambiguity. |
| Qwen, CN-017, case 03 | Source explicitly gives a fixed 24-vote threshold and a vacancy leaving 29 members. Oracle confirms the threshold. | Correctly uses "the same 24 votes must be obtained from only 29 sitting Factors" and qualifies personal versus institutional interest. |
| Llama, SEL-023, case 09 | Oracle correctly confirms grain farming; source also records disputed territorial encroachment. | Infers low priority of the dispute "due to its lack of economic value to their core industry", which the verified fact does not establish. |

These are hypotheses and counterexamples, not frequencies or case-level causal effects. Original selection: SHA-256 rank within model x budget(1,8) x outcome-pair x any-blocked strata; prefer a previously unused question within each judge, otherwise first ranked. One per nonempty stratum. The three illustrated strata were chosen to include both harmful and helpful behavior. Full source quotes and packet identities are in the JSON companion.

A useful next offline audit:

1. Extract all saved visible rationales, confidence, matched verdict transitions, query claims and replies. Join the question-evidence audit labels; do not treat author-key disagreement as established factual error on underdetermined questions.
2. Freeze a rubric for evidence reference, exact scope, relevance to decision, unsupported extensions, acknowledging missing evidence, counterarguments, and uncertainty. Include helpful and harmful outcomes.
3. Choose a seeded question-level sample stratified by world, recipient, condition, and outcome transition, including stable correct and stable incorrect cases. Avoid taking only largest regressions. Keep question clusters and stratum denominators.
4. Have two reviewers code independently with model identity and saved correctness hidden where possible. The rationale may itself reveal the position or model, so blinding is partial. Report disagreement and adjudication.
5. Report sample-specific patterns; use known sampling weights for population estimates, and question-cluster intervals. Compare model confidence categories descriptively against benchmark-key agreement, separately for unambiguous source-determined items.

For future calls, request a short decision summary with evidence IDs, exact short citations, evidence sufficiency and confidence. Validate references against supplied evidence. Do not request private chain of thought. Keep output-format changes as a separate intervention because explaining can alter the answer.

A more persuasive mechanism test is to perturb the evidence itself: remove cited versus uncited facts, consistently reverse decisive facts in a controlled world, and change irrelevant facts while preserving decisive ones. A rationale supplies a prediction; paired randomized input changes test it.

Detailed counts, paths and result hashes: [private inventory](E:/selvarath-archive/partner-feedback-2026-09-13/explanations_audit.json).
