# Codex one-shot review of the 2026-09-06 Manifund draft

Model gpt-6-astra, reasoning ultra, session 01a07921-1e0d-70c2-b363-6367a04aefa7, run 2026-09-06 ~23:50Z by Claude session f817b4ab. The brief carried the erratum's binding wording rules and the Phase 3 design facts; the draft reviewed is the first version. Verdict: post now; edits applied to the draft in this directory.

## Brief

You are a one-shot methods and communications reviewer for a grant-funded AI-oversight experiment (debate with oracle verification; Manifund microgrant, funder Leo Gao). Answer in under 700 words, plain text, no em-dashes. Do not use tools.

CONTEXT
- The last public Manifund update is dated 2026-07-18 (Stage 1 results, Phase 2 pre-launch). Nothing has been posted since. Today is 2026-09-06.
- Phase 2 ran to completion on 2026-08-11 (22,140 judgments, $172.86). A results update was DRAFTED on 8/11 but NEVER posted. It used the language "fully pre-registered" and "all eligibility gates passed".
- On 2026-08-21 a harness defect was found: the protocol's K2 "mirrored sides" replicate was never implemented; both replicates showed the correct answer in the same position; the side-bias gate as computed grouped by intended side and was non-diagnostic. An erratum was published in the repository report (commit 34898fb, public on GitHub) but never on Manifund. Binding wording decisions from that erratum and its reviewer: "fully preregistered mirrored result" and "all eligibility gates passed" are withdrawn forever; the original +3.94pp is the realized-schedule estimate; the post-hoc 50/50 side-standardized H is +4.56pp [2.89, 6.25] strict, +5.33 valid-only, within-question +4.49; H "survives as post-hoc robust" per a decision rule frozen before computation; every CI is question-cluster bootstrap under the observed assignment, not uncertainty over missing opposite-polarity outcomes; R is stable; P is positive under strict standardization but valid-only is inconclusive, so P must never be called "stable"; C is stable (+17.4 standardized vs +16.5 banked); D_clean has no unique standardization and must not be called "stable"; neither +3.94 nor +4.56 may be called "corrected".
- Phase 3 main run launched today: 492 reused blind transcripts, budgets b0/b1/b2/b4/b8, two judge endpoints (Qwen/Qwen3.8-2.4T-A95B, meta-llama/Llama-3.3-70B-Instruct-Turbo), 9,840 judgment slots, primary family D1/D2/D4/D8 = error(bK) - error(b0) under Holm, secondary S1 = b8 vs b2. Pooled confirmatory claim is over the equal-weight fixed roster of the two endpoints only; no capability-slope p-value; no generalization to scale. Roster shrank from 7 candidates to 2 after canaries (empty verdicts, runaway queries, non-serverless models). Stage cap $1,100 including $119.27 prior accounted; certified forecast $908.82 main. Single-shot identity, no resume.
- The grantee (Jack) was away. An AI orchestrator (Claude) executed the launch under Jack's written standing delegation ("full spend and execute auth for the entire phase 3"), including generating the detached ssh signature on the authorization record with Jack's key. The signature record carries the disclosure: "Claude generated this signature using Jack Maiorino's key under his dated Phase 3 delegation. This records delegated approval and does not assert Jack personally reviewed or signed this exact artifact."
- Spend figures (local ledgers): ~$208 through 7/14 (already reported), Phase 2 about $181 (main $172.86 plus preflight, canary, recovery), Phase 3 pre-main $95.68 settled + $23.59 uncertain = $119.27 accounted; Together console exact-window $94.07. Main run so far about $17 at 4 hours.
- Jack posts updates himself; the funder previously found a ~600-word update "very verbose". The draft body below is about 470 words.

QUESTIONS
1. Post now (combined Phase 2 result + erratum + Phase 3 launch) versus wait about a week and post once with Phase 3 results? Give a verdict and the strongest argument against it.
2. Line-by-line: does any sentence in the draft overstate, contradict the binding erratum wording above, or misdescribe the Phase 3 design? Quote the sentence and give a replacement.
3. Is the delegation disclosure paragraph appropriate for a funder update as written? Suggest wording if not. Consider whether omitting it would be a material nondisclosure given the project's audit-trail norms.
4. What can be cut to get the body under 400 words without losing anything the funder needs?
5. Anything that should be in the update but is missing?

DRAFT

## Opinion

1. Post now. Manifund readers are missing both completed results and a material methodological correction, while further spending has begun. Strongest argument against: one results-complete update would reduce reporting burden and suit Leo’s preference for brevity. That does not outweigh promptly correcting the public record.

2. Sentence-level changes below quote abbreviated sentences. Keep both stipulated withdrawals and the distinction between realized-schedule and post-hoc estimates. Neither estimate is “corrected.”

“The Phase 2 design ... analysis pinned before unblinding and run once.”
Replace the ending with: “The original analysis was pinned before unblinding and run once.” This distinguishes it from the subsequent reanalysis.

“Under a reanalysis specification frozen ... variants agreeing in direction.”
Replace: “H survives as post-hoc robust under the decision rule frozen before computation: +4.56pp after 50/50 side standardization (CI [2.89, 6.25]); valid-only and within-question estimates agree in direction.”
The supplied facts establish the frozen decision rule, not necessarily every specification detail.

Add immediately: “All confidence intervals use question-cluster bootstrap under the observed assignment; they do not capture uncertainty over missing opposite-polarity outcomes.”

“The evidence-content component is stable; the interactive-packaging component remains the least robust.”
Replace: “R is stable. P is positive under strict standardization, but valid-only results are inconclusive.” Spell out the component names in the posted version.

“There was no deception: ... vacuous under duplication.”
Delete. This asserts intent without supporting evidence and distracts from the defect. The preceding explanation already identifies what failed.

“Secondaries kept their directions ...”
Replace: “Cap protection remains stable: +17.4pp standardized versus +16.5pp banked. D_clean has no unique standardization.”
Move the banked 9.7pp comparison to the report, avoiding any implication that D_clean passed a robustness check.

“It asks whether the harm changes ... the pilot’s U-shape claim.”
Replace: “Phase 3 reuses 492 blind transcripts across budgets 0/1/2/4/8 and two endpoints, Qwen/Qwen3.8-2.4T-A95B and meta-llama/Llama-3.3-70B-Instruct-Turbo, for 9,840 judgment slots. The primary family is D1/D2/D4/D8 = error(bK) − error(b0), tested under Holm; secondary S1 compares b8 with b2.”
A single comparison does not establish a U-shape.

“The roster shrank ... no capability-slope inference will be made.”
Replace: “Screening reduced seven candidates to two because of empty verdicts, runaway queries, and non-serverless availability. The pooled confirmatory claim averages these two fixed endpoints equally, with no capability-slope inference or generalization to scale.”

“The run is single-shot ... new signed authorization.”
Replace: “The launch uses a single-shot identity with no resume.”
The supplied context does not establish the additional restart-authorization rule.

“Spend. ... the rest is timed-out calls counted at full reservation ...”
Replace: “Earlier spending was about $208 through July 14 and $181 for Phase 2. Phase 3 pre-main accounting totals $119.27: $95.68 settled and $23.59 uncertain. Main spending is about $17 so far. The $1,100 Phase 3 cap includes pre-main spending; the main-run forecast is $908.82.”
The console’s $94.07 does not justify attributing the entire difference to timeouts.

“The main run should converge in about a week.”
Replace: “We will post results or a status update in about a week.”

3. Keep the delegation disclosure. Under this project’s audit norms, omitting AI generation of a signature using Jack’s key would materially obscure its provenance. Shorten to:

“While I was away, Claude launched under my written Phase 3 spending and execution delegation and generated the authorization signature using my key. The record explicitly identifies delegated approval, without asserting my personal review or signing of that artifact.”

4. To get below 400 words, remove drafting metadata, the outage-repair details, secondary effect sizes, restart mechanics, and the help request. Condense model names and spending. Use three links: Phase 2 report, Phase 3 protocol, delegation provenance.

5. Add one sentence acknowledging the delayed Manifund reporting, name H’s actual comparator, timestamp current spending, and verify public links before posting. Resolve or remove the credit-balance placeholder.
