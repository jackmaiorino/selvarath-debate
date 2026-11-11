# Council synthesis: final report draft (methods, results, limitations, supplement)

Run: 2026-11-10, `council-runs/20261110-1950-final-report-draft`. Artifact: the 19-page draft in `paper/` (base commit
0e11907, paper folder uncommitted). Two rounds plus debate. The clerk (this session) wrote the briefs, ran the rounds
and wrote this synthesis; it did not vote. Member labels used in rounds 2 and 3: A Fable, B Sol, C Carbon, D Opus,
E Astra (`roster.json`).

## Roster

| Seat | Model that served | Effort | Round 1 | Round 2 |
|---|---|---|---|---|
| Fable | Claude Fable 5.2 (Agent tool, model "fable") | xhigh stated in brief | revise, 88 | revise, 90 |
| Opus | Claude Opus 5.5 (Agent tool, model "opus") | xhigh stated in brief | revise, 80 | revise, 85 |
| Astra | gpt-6-astra (log header) | xhigh | revise, 93 | revise, 94 |
| Sol | gpt-6.1-sol (log header) | xhigh | revise, 91 | revise, 93 |
| Carbon | Gemini 4 Carbon (High) / gemini-4-carbon (meta file), no tool steps in any call | High | revise, 88 | revise, 93 |

The Agent tool returns no served-model record for the two Claude seats; the model named is the one requested.
Carbon judged from the pasted text alone; the other four could read the repository.

## Verdict

Round 1: five of five "revise". Round 2: five of five "revise" (confidence 85 to 94). The verdicts did not split. No
seat found a wrong number: every seat that checked the frozen files reported that text, tables and figures agree
with them. Every seat said the required changes are wording, reporting and disclosure, and that none needs a new
outcome analysis.

## Survived findings

Raised by at least one seat and not refuted with a concrete reason in round 2. "All five" means raised or affirmed
by every seat, from three model families. Sub-claims that were refuted are listed under the disputes below.

| # | Finding | Raised in round 1 by | Round 2 |
|---|---|---|---|
| S1 | The oracle's own model graded the oracle. Claude Opus 5.5 was the oracle, the four explanation labellers and the 16-case checker, so "the oracle was rarely the problem" and the "misread a correct oracle answer" label are not independent judgments. Name the outside reviewer (GPT-6 Astra, which re-read 21 cases, at most 3 of the 56 in the largest group). | Fable, Opus, Astra, Sol, Carbon | all five |
| S2 | The wording "not supported without the Claude judges" (Sections 4.3, 4.6, 6) invites the reading that the one-query rise depends on the Claude judges. In that registered check the rise is intact and the joint peak test misses its correction. The registered secondary contrasts for the sensitivity panels are missing from Table 5. (Exact wording settled in debates D1 and D3.) | Fable, Opus, Carbon | all five |
| S3 | Overlapping model roles are not disclosed where they matter: Fable 5.1 wrote half the worlds, their wrong answers and the "why this is defensible" note it then received as dishonest debater, and validated the other half; Sonnet 5.5 debated and judged; Opus 5.5 was judge, oracle, gate auditor and explanation labeller; no human labels anywhere. Questions were kept only if model validators answered them from the document, so low error with the document is partly built in. Table 14 omits several roles. | Fable, Opus, Astra, Sol, Carbon | all five |
| S4 | Debates were written once, by debaters told the judge might verify claims, and the same transcript was used at every budget. Debaters could not adapt to the budget, and the no-query baseline is not a world without verification. The "what is measured" limitation should say so. | Fable, Opus, Carbon | all five |
| S5 | Inference is about these seven judges. The bootstrap resamples worlds only; the pooled mean weights judges equally, so the one high-error judge (Haiku 4.5, about 70% of pooled errors) moves it most. | Fable, Opus, Astra, Sol | all five |
| S6 | "Small" needs its base rate. The 1.5-point threshold was fixed when the design required at least 10% no-query error; the realised rate is 4.34%, so the rise is about a sixth more errors. Most of the development-run 22.9% came from Llama 3.3 70B (38 of 56 errors), which was then lost. | Fable, Opus | all five |
| S7 | Replies cut off at the output limit are scored as errors and are uneven across conditions (Qwen3.8: 19, 6, 2 and 1 at none, one, two and six queries; Haiku 3 at none). The draft should give the counts and separate the registered error measure from wrong choices among completed verdicts. (Scope settled in debate D4.) | Fable | all five |
| S8 | Gate section. The rate of flagged queries is flat across budgets (about 11% per admitted query); the session rate rises only because sessions have more queries. The leak is a property of the classifier and was not shown to come from re-hosting (the same auditor flagged 14 of 100 admissions of the development gate). "Affected share" is never defined. (What the worst-case check can show: debate D5.) | Fable, Opus, Carbon | all five |
| S9 | Timing. Every no-query, world-only and debate-and-world verdict was collected before the gate outage, and every query-condition verdict after the relaunch about a day later. Disclose it as an untested stability assumption; no drift is shown. | Sol | all five (Fable and Opus verified in the code and amendment 2) |
| S10 | Identical gate and oracle requests reused stored responses across judge sessions (75,367 oracle calls behind 109,090 recorded outcomes). State it in Methods. | Astra | all five |
| S11 | Exploratory section (4.5, Figure 5, Appendix E). State the interval method (normal approximation with finite-population correction) and its poor coverage for sparse categories; give the group sizes behind Figure 5b (873, 5,171 and 7,838 pairs); say the word matcher drops numbers and short negations; "the split is stable" holds for direction only; "a mechanical check points the same way" should be descriptive; grouping flips by the claim chosen in the one-query run is selection on that run; give counts for the two patterns (16 of 26, 13 of 23); Haiku supplies 170 of the 331 harmful flips; the oracle-error share rests on 4 of 77 cases. | Astra, Sol, Carbon, Fable, Opus | all five |
| S12 | Section 4.2's heading promotes an unregistered comparison of which Haiku supplies about 77%; the two conditions also differ in the judge's system prompt. | Fable, Opus, Carbon | all five |
| S13 | Statistical presentation. The headline p-value sits at the bootstrap's resolution floor; level intervals in Figures 2 and 3 overlap although the paired differences exclude zero; the infinite upper limits come from the interval procedure on sparse cells; Qwen3.8 misses after correction because the family still counts two tests that never ran; the eight-judge rescoring is algebra, not evidence of robustness; "every analysis had been recorded" should be "every registered analysis". | Fable, Opus, Carbon, Astra | all five |
| S14 | Readability. The passages that break the co-author's rule are the equivalence-check sentence in 3.4, the hypothesis sentence in 3.5, the third paragraph of 4.1, the worst-case paragraph of 4.4 and the first paragraph of 4.6. Each seat supplied replacements; the disputed ones are listed below. | all five | all five |
| S15 | Smaller corrections: "we control exactly what the judge knows"; "held-out" for development questions; "things that were true" for claims the oracle confirmed; the gate rule allows a rule with its conditions or a list as one claim; "stronger model" is a design choice, not a measured ranking; 488 against 487 rejections; denominators for "154 and 60"; 2,416 is 2,000 plus 416; the oracle's 93% is exactly the preset floor and its reference readers (GPT-6.1 Sol, DeepSeek V4 Pro, Kimi K3) are unnamed; the internal file reference in Appendix D; the unattributed $21. | Opus, Fable, Carbon, Sol, Astra | all five, with two "unsure" on the 488 explanation |

## Debated disputes

Five disputes were debated, most severe first. All five ended without a jury: two by concession with named reasons,
three as narrowed and accepted. Eighteen debate calls in all.

| Id | Disputed claim | Defender, challengers | How it ended | Decisive argument | What stands |
|---|---|---|---|---|---|
| D1 | Fable's blocker: the draft's wording "reverses the meaning" of the check without the Claude judges | Fable; Astra | Narrowed, accepted (3 turns). Fable withdrew "reverses its meaning" and accepted Astra's position in full. | The 0.056 belongs to the corrected joint test of the peak, not to the decline alone: the joint p is 0.028 before correction, so neither comparison fails on its own (Astra; Fable confirmed it in `main_analysis.json`). | Severity major. Agreed text: "Without the three Claude judges, the estimated rise from no queries to one remains positive and statistically supported. The estimated decline from one to six is smaller, and the joint test for a peak does not pass the correction for testing two budgets. This does not establish that the decline is absent." Print the registered contrasts Table 5 omits, including six against none for that panel (+0.46 on the registered scale, p = 0.006, unadjusted). Open placement question: whether that sentence sits in 4.3 or only in the supplement. |
| D2 | Carbon's blocker, sub-claim: same-model labelling biases the labels toward clearing the oracle | Carbon; Astra | Conceded by Carbon (3 turns), reasons named. | The agreement algebra needs matched error distributions, and the labellers did a different task from the oracle with different inputs; a missed oracle error need not land in the "misread" category (Astra). | Disclose a possible shared-error threat without a stated direction. Agreed wording: "By the Opus 5.5 labellers' assessment, most sampled harmful flips involved a correct oracle answer followed by an unsupported conclusion. Because the oracle and primary labellers used the same model, shared interpretive errors could go undetected; the reported intervals exclude labelling uncertainty." Name the reviewer model. |
| D3 | Carbon's blocker, sub-claims: the pooled result at six is an "aggregation cancellation" the draft obscures; "only two recovered by six" | Carbon; Astra | Conceded by Carbon (3 turns), reasons named. | Section 4.3 already states that the GPT-5.6 judges stayed elevated at six while Haiku and Qwen3.8 fell below their no-query level, so the cancellation is disclosed, and five judges decline from one to six, not two (Astra). | Minor edits only: add "on average" (and the gate's scope) to the 4.1 heading; make the 4.6 sentence precise as in D1. |
| D4 | Fable's major: Qwen3.8's fall is "probably this artifact" of cut-offs; the pooled sign at six "carries no information" | Fable; Astra, Sol | Narrowed, accepted (2 turns). Fable withdrew both phrases. | Cut-offs are part of the registered error measure, and their counts cannot show what a cut-off reply would have answered; a six-query verdict cannot stand in for an unobserved no-query verdict (Astra, Sol). | Disclose the unequal cut-off counts; distinguish the registered error measure from wrong choices among completed verdicts; assign no direction to the unobserved answers. |
| D5 | Fable's major: the worst-case check "could not be passed ... whatever the data" | Fable; Astra, Sol | Narrowed, then the defender's new argument was conceded by both challengers (4 turns), reasons named. | With 280 sessions per budget, an audit that flagged nothing still has an upper bound of 1.06%, above the three tipping points (0.25%, 0.31%, 0.93%), so under the prespecified upper-bound rule those three findings could not have been labelled robust whatever the audit found (Fable; accepted by Astra and Sol). | Withdrawn: "whatever the data", "bound to", "settled before any contrast was computed". Agreed: "At these observed error rates, the broad all-flag allowances are too large for any of the four findings to survive this worst-case check", and "For the observed contrasts and confidence bounds, the one-query peak and both practical-size exclusions would fail this upper-bound sensitivity check even if the audit flagged no sessions: 280 sessions per budget give a minimum exposure upper bound of 1.06%." The tipping points stay informative; the audit measures exposure, not changed verdicts. |

## Undebated disputes

Live after round 2 and beyond the cap. Both sides in one sentence each; not resolved here.

- Noise share of the harmful flips (Fable's finding 3). Fable, with Opus agreeing: about two thirds of harmful flips are matched by helpful flips and would be expected with no effect of the query. Astra and Sol: 216 against 331 cannot split harmful flips into noise and treatment, because queries may change verdicts in both directions.
- "p at most 0.0002" (Fable's finding 8). Fable: write it as an upper limit at the floor. Astra, Sol and Carbon: zero exceedances in 10,000 draws is a resolution floor, not an upper bound; write "p = 0.0002, the smallest value 10,000 draws can return".
- Deterrence (Carbon's finding 3). Carbon: the verification warning means deterrence was already active with no queries, which explains the 86% YES rate and the lack of gain at six. Fable, Opus, Astra and Sol: no condition removes the warning, so disclose the design fact and assert no effect.
- Settings as explanations (Carbon's finding 4). Carbon: the effort and ceiling gap explains the word-count gap, and Haiku's lack of extended thinking explains its error rate. Fable, Opus, Astra and Sol: nothing varied those settings; report them without the causal gloss.
- Figure 5b under noise (Carbon's finding 5). Carbon: run-to-run noise plus pre-query leaning reproduces the crossover exactly, helped by the dishonest debater's longer turns. Fable: selection alone predicts a net of zero and the net is +115, and only 873 pairs matched the dishonest debater's wording against 7,838 for the honest debater's.
- Flagged against affected share (Carbon's finding 6). Carbon: Section 4.4 compares a verdict-affected tipping point with flagged-session rates. Opus: under amendment 3 the tipping point is an exposure share, so the comparison is like for like and only a definition is missing.
- A four-test Holm value for Qwen3.8 (Carbon's finding 6). Carbon: note that the value would be 0.033 over the four tests that ran. Fable and Astra: that family was never registered and in print would read as a rescued result; give 0.011, 0.055 and the reason only.
- Replacement sentences (Carbon's finding 7). Carbon: open 3.4 with "agreed on 98%", call the auditor "a stronger model" and the subset "the 806 cleanest questions". Fable, Opus and Astra: a gate admitting everything would agree 96.5%, the auditor is the oracle's own model, and the flags are validators' judgments.
- Coverage of the pooled audit bound (Carbon's finding 8). Carbon, with Fable and Opus: unequal judge rates make the pooled Clopper-Pearson bound conservative (Fable computed 96.3% at the audit's rates against 95.8% if equal), so the caption's "can fall slightly below 95% when judges differ" names the wrong mechanism. Astra and Sol: a two-judge counterexample shows slight undercoverage is possible in principle, so the caption is not wrong. The two positions are compatible: possible in principle, not at these rates.
- Audit sampling and the eight-judge row (Carbon's finding 8). Carbon: cite world clustering in the caption and add an eight-judge row to Table 5. Astra: the audit samples sessions within judge and budget cells, not worlds, and the row is optional because Appendix A gives the scaling.
- Heading of 4.3 (Opus's finding 2). Opus: naming two judges in the heading conflicts with a check that removes three. Astra: a descriptive statement and a sensitivity check are different statements and do not conflict. (All seats now agree the heading should distinguish the broad rise from the judge-dependent fall.)
- Fixed transcripts, two phrases (Opus's finding 4). Opus: the mechanism in 4.5 "follows directly" from the dishonest prompt, and "in use, debaters would adapt". Astra, with Fable on the second: the prompt is a plausible influence only, and adaptation is a prediction the study cannot support.
- Floor effect (Opus's finding 5). Opus: low baseline error leaves little room for queries "to help or hurt". Astra and Sol: it caps improvement only; error can rise from a low base.
- Connecting line in Figure 2 (Opus's finding 9). Opus: the line from 2 to 6 implies values at 3 to 5. Astra: a labelled categorical axis asserts nothing in between; a note is optional.
- Haiku and the cases read (Opus's finding 12). Opus: Haiku supplies 170 of 331 harmful flips, so the cases read mostly describe Haiku. Astra: that is its share of the population, not shown for the 77 sampled cases.
- "Directly answer-revealing" (Opus's finding 14). Opus: rename the narrow flag category. Astra and Sol: the category includes unclassified and unreadable flags, so the label would overstate it.
- "Labelled every admitted query" (Opus's finding 16). Opus: it should say every query sent to the gate. Astra: both are true; specify "sent to the classifier" so mechanically rejected proposals are not implied.
- Astra's replacement for the worst-case paragraph (Astra's finding 6). Astra: drop the numbers from the main text. Fable, with Opus: keep 0.25% against the audited 11 to 33% so the reader sees the scale.
- "Separate runs" (Fable's finding 9). Fable: say "separate runs" in 4.5. Carbon: applied to the four labelling sessions it would wrongly suggest repeated passes over the same cases.

## Withdrawn

- Opus, finding 1 in part: the claim about direction (that rule-breaking queries would lower error most at six queries, so the rise at one is understated). Reasons: the flag rate per query is flat, Haiku's six-query flags are mostly not answer-revealing, and the evidence cited the other way is itself selected.
- Carbon, finding 2 in part: treating the working title as a blocker.
- Sol and Astra, finding 6 in part: their round-1 endorsement of the Section 4.3 heading.
- Fable: the clause that Haiku had the most flagged sessions at one query (Sonnet 5.5 had more); the "near-default label" bullet in finding 3 (the field's definition could not be verified); its round-1 view that the 4.3 heading was fair.
- In debate: Fable withdrew "reverses its meaning" (D1), "probably this artifact" and "carries no information" (D4), and "whatever the data", "bound to" and "settled before any contrast was computed" (D5). Carbon conceded the direction-of-bias sub-claim (D2) and the "aggregation artifact" and "only two recovered" sub-claims (D3).

## Missed by all in round 1 (named in round 2)

- Judges saw only the oracle's one-word answer and were never told how the oracle was instructed. The oracle's request asks "Is it supported by the text that ...", for which NO reads as "not supported", while its system prompt defines NO as contradiction. The "refuting a loose version" pattern and the "misread" label are therefore partly properties of the interface (Fable).
- Table 13 and the sentence "the net excess of harmful flips sits where an answer came back" group pairs by what happened in the one-query run, the same selection problem raised for Figure 5b; the no-answer group is not a control (Opus).
- The helpful-flip count includes 15 pairs whose no-query verdict was unreadable, and the harmful count 7 whose one-query verdict was unreadable; Appendix E reports only the harmful side (Fable).
- The Section 4.3 sentence "The exception is Sonnet 5.5, the same model as the honest debater" invites a same-model explanation, although Sonnet's change is 0.09 points (Opus).
- The oracle's 93% comes from a 300-claim development sample that also served to select the oracle and was balanced across proposing judge families; it is not a usage-weighted estimate of main-run accuracy (Astra). The labelled oracle-error share in harmful flips (about 6%) is no higher than the development error rate (about 7%), which is the defensible version of "rarely the problem" (Opus).
- Table 14 omits the judges' output limits, although a cut-off reply is scored as an error (Fable).
- Section 3.2 does not report what the 20 unflagged turns showed (all complied), nor that 20 turns cannot bound the classifier's miss rate (Fable).
- The 0.25% tipping point comes from the one-against-six comparison, which is moved by both conditions' exposure; for the rise over no queries alone the tipping point is nearly twice as high (Carbon).
- Table 8's "mean used" (0.95 to 0.96 at one query with no early stops) needs a definition against the rule that a second rejection uses up a query (Carbon).
- The interruptions row of Table 6 should state the chronology in S9 (Opus).

## Clerk's notes (procedure, not findings)

- The two Claude seats read their briefs from files in the repository instead of receiving the full text in the
  prompt; the content was identical. In round 2 the Opus seat saved its own reply; the Fable seat declined to write
  any file, so the clerk transcribed its replies.
- Debates were capped at four at first, to stay under about 30 calls; a fifth was added when the first four ended in
  12 calls. No jury was needed.
- One brief (Sol's second turn in D4) carried an empty heading for a turn still being written. No content leaked.
- Two seats cited files by full local path in round 1, and those positions went into the round-2 briefs, including
  the one sent to the Gemini seat. Local paths were stripped from every debate brief.
- The council is advisory for this project. Its record does not replace the project's own review rule (a fresh
  one-shot GPT-6 Astra session at ultra effort) for any text changed in response.
