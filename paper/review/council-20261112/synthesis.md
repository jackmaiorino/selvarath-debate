# Council synthesis: final report draft after the cut to six pages (second council)

Run: 2026-11-12, `council-runs/20261112-2138-final-report-six-pages`. Artifact: the 23-page draft in `paper/` at commit
af4657e (branch `claude/final-report-paper`), after the first council's findings were applied and the main text was
cut to about six pages. Two rounds plus debate, access mode `files`. The clerk (this session) wrote the briefs, ran
the rounds and wrote this synthesis; it did not vote. Member labels used in round 2 and the debates: A Astra,
B Fable, C Sol, D Carbon, E Opus (`roster.json`).

## Roster

| Seat | Model that served | Effort | Tool use (files mode) | Round 1 | Round 2 |
|---|---|---|---|---|---|
| Fable | claude-fable-5-2 (subagent transcript) | xhigh stated in brief | 47 and 58 tool calls | revise (light), 74 | revise (light), 80 |
| Opus | claude-opus-5-5 (subagent transcript) | xhigh stated in brief | 44 and 27 tool calls | revise, 80 | revise, 83 |
| Astra | gpt-6-astra (log header) | xhigh | 10 and 13 read-only commands | revise, 90 | revise lightly, 92 |
| Sol | gpt-6.1-sol (log header) | xhigh | 17 and 17 read-only commands | revise, 93 | revise, 92 |
| Carbon | Gemini 4 Carbon (High) / gemini-4-carbon (meta files) | High | none (pasted text only) | revise, 88 | revise, 92 |

Carbon judged from the pasted text alone; the other four could read the repository and the frozen code worktree.
One debate reply of the Gemini seat (D2, turn 2) was first served by the fallback model, Gemini 4 Argon (High),
after the Carbon stream was interrupted; it was rerun on Carbon alone and the Carbon reply is the one in the
transcript. The Argon reply is kept as `debate/D2/fallback-argon-t2.md`; it took the same position with similar
arguments. Astra and Sol share a family and standing instructions, as do Fable and Opus, so agreement within a pair
counts for less than agreement across pairs.

## Verdict

Round 1: five of five "revise". Round 2: five of five "revise" (confidence 80 to 92). No seat found a blocker and no
seat found a wrong number. Every seat said the changes are sentence-level wording, disclosure and cross-reference
repairs, that none needs a new outcome analysis, and that Table 1 can stay in the supplement. Every seat's own
strongest counterargument was the same: the draft is already unusually well caveated and could be handed over
as it is, with these points in a cover note.

## Survived findings

Raised by at least one seat and not refuted with a concrete reason in round 2. "All five" means raised or affirmed
by every seat, from three model families.

| # | Finding | Raised in round 1 by | Round 2 |
|---|---|---|---|
| S1 | The time split is disclosed in 3.4, but not what it implies. Comparisons of a query condition with the no-query or document conditions (one against none, six against none, document against six) largely cross the collection split and assume judges behaved the same on both sides, which was not tested. Comparisons among query budgets largely do not cross it. One or two sentences in 3.4 or 3.5; "largely", because some query sessions finished before the outage. | Astra, Sol, Fable, Opus (independently) | all five |
| S2 | Section 4.2: the document's advantage is partly built in (questions were kept only if validators answered them from the document, 3.1) and the two conditions use different judge instructions; neither is said in 4.2. Its pointer "not robust to the gate analysis of Section 4.4" leads nowhere, because 4.4 never mentions this comparison (its limit, 1.40%, is only in Table 13, and it fails on the measured six-query flag rates, not on the zero-flag bound). The heading "Reading the evidence beat querying it" generalises from short, pre-validated documents. | Fable, Opus, Carbon | all five |
| S3 | Section 4.5, second pattern: the caveat that judges were never told the three labels or that the oracle answers only from what the document states is only in Appendix E. Without it the pattern reads as a pure reasoning failure by judges. Add one clause ("so this pattern may partly reflect the interface"). Also 3.4: "one-word answer" is wrong for NOT ADDRESSED; write "bare label". | Opus, Carbon | all five |
| S4 | Section 4.4: (a) "flagged" includes borderline labels (141 violating and 71 borderline, 7.1% and 3.6% of 1,982 admitted), but the next paragraph calls them all "rule-breaking"; (b) "fixed in advance" reads as the preregistration, while the check came from an amendment written before the run resumed and before any result was seen; (c) 4.4 refers to ruling out a 1.5-point peak at two queries, which 4.1 never states; (d) the third paragraph is the densest passage in the main text and should be shortened; (e) the sentence that the query results "do not by themselves show what verification that stays within the rule would do" is now only in the supplement. Keep the registered label "not robust" and keep "could not have passed" tied to the three named findings at their observed sizes. | Fable, Sol, Carbon, Opus | all five |
| S5 | Section 3 opening: "a stronger dishonest debater" asserts an ordering that 3.2 says was only expected ("expected to be stronger"). "The design, the hypotheses and the analysis were ... frozen before the main run" needs "original" and a mention of the outcome-blind amendments; 3.3 should say the seven-judge primary panel was fixed by an amendment before any result was seen. | Astra, Sol, Opus, Fable | all five |
| S6 | Heading 4.1, "six brought no average gain": the difference is 0.05 points lower with an interval from 0.37 lower to 0.30 higher, so the heading states an absence the test does not establish. Use "six was close to the no-query level". | Astra | Astra, Sol, Fable, Opus (Carbon did not contest it) |
| S7 | Validator flags are used in 4.6 (the 806-question check) but never defined in the main text. Add to 3.1 that validators also recorded whether another reading could reverse the answer, that the flag did not exclude a question, and that 262 of 1,068 questions drew it in two or more of four responses. | Fable, Carbon, Opus | all five |
| S8 | Section 4.3 gives Haiku's share of the error level (70%) but not of the rise, which is about a quarter (29 of 115 net verdicts); the "back down by six" side comes almost entirely from Haiku and Qwen3.8 (0.79 of the 0.82 pooled points); Luna's fall is 0.04 points; the sentence listing who ends above or below the no-query level omits Sonnet (below) and Opus (above). Give sizes ("the largest declines were"), not "fell clearly". | Fable, Opus, Carbon | all five on the substance (see D1) |
| S9 | Haiku ran without extended thinking in a format that asks for the verdict first, so each query step also gave it room to write before its verdict, and the design cannot separate that from verification. One sentence in 4.3 and a note in Appendix I. The preregistered estimand already says "extra computation included"; 3.5 dropped those words. | Fable, Carbon | all five (see D4) |
| S10 | Section 6: debates were written once under a warning that the judge might verify claims, so any deterrent effect of that warning is in every condition, the no-query baseline included, and is not measured. Section 6 now says only "written once and reused". One clause. Also point Section 6 to "Sections 3 and 4" (the list omits 3.1, 3.4 and 4.2), and fix Appendix I's opening sentence, which still describes the old Section 6. | Carbon, Opus, Fable | all five |
| S11 | Figure 2 caption lost "3 to 5 queries were not tested" in the second trim (an applied first-council item). The axis is evenly spaced with a joining line. Restore it. | Opus, Fable | all five |
| S12 | Small consistency repairs, all agreed: 3.5 mixes "57 of 89,712 sessions" (1,068 questions) with cut-off counts taken over 1,070 questions; Appendix E still says "misreadings"; Table 9's "touched none" includes sessions with no admitted query; Appendix G's $21 (compliance check plus token-priced gate calls) is not reconciled with Appendix D's $50.35 posted charge for the dedicated gate; Table 15 has three empty Setting cells and two scope notes; 3.2 should give the direction of the debaters' settings (dishonest side: high effort and twice the output limit); "the development gate" and "the exclusion" are unclear on first read; 4.6 should cite Appendices A and D; 4.3 should cite Table 8; "by this auditor's standard" in 4.4; no result was registered or computed by world author (Appendix I); Table 7 can say recovery closed before any result was read. | Fable, Carbon, Opus, Sol | four or five each |
| S13 | Working title (an open item): all five would change it. "Fragile" fuses two different things, the gate exposure of every query finding and the dependence of the return by six on two judges; the rise itself is supported in every registered panel. No seat defends "cost" without a condition. | Fable, Carbon, Opus, Astra, Sol | all five on changing it; see the undebated list for the reasoning dispute |

Optional additions that no seat opposed: one clause on query use in 4.3 (with six on offer judges used 2.2 to 5.6
on average); a supplement note bounding the pooled size of cut-off replies (at most 0.15, 0.04 and 0.01 points at
none, one and six queries: too few to explain the rise, of the same order as the six-against-none difference).

## Debated disputes

max_disputes was cut from 5 to 4 under the cost guard (two disputes had four challengers each). 30 debate calls.

| # | Finding in dispute | Defender, challengers | How it ended | Decisive argument | What would settle it |
|---|---|---|---|---|---|
| D1 | Fable 1 (major): the main text must add a descriptive split of who supplies the one-query rise (Haiku a quarter; the other six judges 1.53% to 2.20%, about 44% more errors; the four-judge panel's base rate beside its 0.94 points). | Fable; Astra, Sol | Concession on the remaining point, turn 3. Outcome all three accept: add "Haiku supplies only about a quarter of the rise" to 4.3 (required). The six-judge sentence and the 4.1 pointer were withdrawn; the four-judge base rate is optional. | The challengers' point that the four-judge paragraph makes no size claim, so "small needs its base rate" does not reach it; and Fable's own check that 4.3 already caps that panel's base at 2.86%. In the other direction, Fable's point that the rise is the one quantity in 4.3 with no share moved Sol to accept the quarter clause. | A reader check: do readers attribute most of the rise to Haiku, or apply the pooled 18% to the four-judge panel? The base rate (1.95%, not 1.96%) becomes required only if a later sentence sizes the 0.94. |
| D2 | Astra 1 and Sol 1 (major): replace intervention wording ("raised", "the effect of offering", "cost", "beat") with descriptive wording in the 4.1 and 4.2 headings, the Figure 2 caption, 3.5 and the title. | Astra, Sol; Carbon | Turn cap, both sides maintaining after the defenders narrowed in turn 1 (3.5 may keep "the effect of offering" once the stability assumption is stated; only cross-period headlines need to change). Jury (Fable, Opus): 2 to 0 "narrowed" for the 4.1 heading and the Figure 2 caption (make them descriptive); 1 to 1 on the 4.2 heading and the title. | For the jury: the defenders' identification counterexample. The one-against-six leg lies within one period and fixes only the order of the budgets, not the sign of a comparison that crosses the split; Carbon conceded the algebra. The split on 4.2: Fable's juror held that Carbon's unrebutted bound (a shift on the three GPT judges alone can be at most 1.15 points, below the 1.54 lower bound of the document gap) leaves 4.2 standing as written; Opus's juror held that plausibility does not restore identification. On the title: Fable's juror says "fragile" already hedges it; Opus's juror says it must not assert an unconditional "cost". | Interleaved no-query controls collected at the same time as the query conditions. The frozen data have none, so this is a reporting choice. |
| D3 | Opus 1(b) (major): a new limitation calling the 262 flagged questions "contested answer keys", with "part of what is scored as error may be a defensible alternative reading". | Opus; Astra, Fable, Sol, Carbon | Resolved as narrowed, turn 3. Opus withdrew the label, the phrase "contested on a quarter of the analysed questions" and the "defensible reading" clause, then also the closing clause on benchmark composition, and lowered the severity to minor. All parties accept a factual Appendix I bullet. | Fable's two facts from the validator prompt and the kept responses (validators were told to try hard to construct a reversing reading; every kept response still chose the key and none called the other answer about as defensible), and Carbon's and Fable's arithmetic that the flagged questions carry about half of the document gap as well (138 of 281), so the flag marks where judges without the document err, not a separate effect. | Human adjudication of the 84 questions flagged in all four responses; a world-level interval for the difference between the two groups (a new outcome analysis, ruled out). |
| D4 | Carbon 1 (major): with no queries Haiku committed to its verdict "in a single forward pass" with "zero pre-verdict reasoning"; its reasons are "structurally post-hoc"; proposed wording for 4.3 and 4.5. | Carbon; Astra, Fable, Sol, Opus | Resolved as narrowed at the turn cap. Carbon conceded every stronger statement in turn 1 and the Haiku-only clause in turn 3. All four challengers accept the main-text scope: in 4.3, Haiku ran without extended thinking in a format that asks for the verdict first, so query steps gave it room to write text before its verdict; in 4.5, the explanations were requested after the verdict line (for every judge) and "only harmful flips were read" stays. Left open at the cap: the exact supplement wording (Fable and Opus each gave one; Carbon had no further turn). | Against the strong claims: the parser accepts a verdict line anywhere and compliance was never measured; a missing thinking trace does not show unfaithful reasons. For the narrowed claim: Carbon's point that with one query the oracle's answer and the verdict request arrive in one message, so a compliant reply states its verdict before any written account of that answer (Astra and Sol conceded on this). | A count from the stored replies, by judge and condition and not by outcome: how often the verdict line comes first, and whether Sonnet and Opus payloads contain thinking blocks. It would settle output order, not faithfulness. |

Final wording offered by the defender in D3 (accepted in substance by all four challengers; Astra and Sol had
proposed softer endings about transfer, which the defender did not take):

> Questions flagged for another reading. On 262 of the 1,068 analysed questions, at least two of the four validator
> responses (all four on 84) described a reading under which the other answer would win. The validators had been told
> to try hard to find one and allowed to fill gaps with assumptions the document does not rule out. Every response
> still chose the key, and none judged the other answer about as defensible. These questions were kept by design.
> Nearly half of the net one-query rise (53 of 115 net harmful flips) falls on them, as does about half of the gap
> between six queries and debate with the world document (138 of 281 net errors). The difference between the two
> groups of questions was not tested.

## Undebated disputes

| Sub-claim | For | Against |
|---|---|---|
| Carbon 2(b): report the oracle outcome split (86.4% YES, 2.4% NO) in the main text as evidence that debaters were deterred from lying. | Carbon: it reflects deterrence empirically. | Astra, Fable, Sol, Opus: judges chose which claims to check and no condition lacks the warning, so nothing measures deterrence; the split may appear only as a query-use fact. |
| Carbon 3(d): retitle 4.4 "The gate admitted rule-breaking queries, leaving query findings non-robust". | Carbon: "a caveat" and "some" understate that every query finding fails the check. | Astra, Fable, Sol, Opus: three of the four findings could not have passed at this audit size, so "non-robust" in a heading reads as demonstrated fragility; flags include borderline cases. |
| Carbon 3(e), 4(b), 8: add the two-query bounds (0.57, 0.63) to 4.1 and p-values to 4.3. | Carbon: they repair dangling references and say whether 0.57 is noise. Fable and Sol accept only "p = 0.006" for the 0.57 points. | Astra, Opus (and Sol for the bounds): more statistics in the main text breaks the co-author's rule; a few words or a pointer fixes the references. |
| Fable 3(b): add to Appendix I that the three GPT-5.6 judges show a step of similar size at every budget (1.72% against 2.61%, 2.39%, 2.68%), which a period effect and an effect of offering any queries would both produce. | Fable, Opus: it makes the timing disclosure concrete without claiming drift. | Astra, Carbon: a post hoc subgroup pattern that cannot distinguish the two; the general assumption is enough. |
| Opus 5(b): say "at this audit size the check could not have been passed" and drop "not robust". | Opus: "not robust" reads as evidence of fragility. | Astra, Fable, Sol: the sentence holds only for the three named findings at their observed sizes (not the document comparison, whose 1.40% is above the 1.06% bound); the amendment fixes the label "not robust". |
| Fable 9(a): Section 6 should name the four main caveats (small, gate, two judges, exploratory), not only point to them. | Fable, Opus, Carbon: a reader of Section 6 alone learns nothing about size or gate. | Astra, Sol: Section 6 supplements the caveats stated with each result and need not repeat them. |
| Fable 7 and Carbon 3(a): "fragile" is attached to the most stable result. | Fable, Carbon: the rise is supported in every registered panel. | Astra, Sol, Opus: the rise is also the finding most exposed to the gate (lost at 0.25%), so "fragile" is accurate about the gate; the defect is that one word fuses two things. |
| Opus 9(b): title "under a gate that leaked". | Opus: names the gate. | Fable: more than four in five flagged sessions carry no answer-revealing flag, so "leaked" overstates. Astra, Sol: prefer "under an imperfect query gate". |
| Carbon 6(1): the 4.5 intervals ignore clustering by world. | Carbon (narrowed in round 2 to labelling them "finite-sample" and "approximate"). | Astra, Fable, Sol, Opus: they are design-based intervals for a sample from the fixed 331 flips, the stated target. |
| Carbon 8, the replacement paragraph for 3.4. | Opus, Sol (with adjustments): faithful and shorter. | Fable, Astra: it drops "neither equivalent nor shown to differ by much" and says rejection rates "matched". |

## Withdrawn

- Carbon: the Table 15 item (withdrawn on a misreading of another member; Astra, Fable and Sol confirmed from the
  TeX that three Setting cells are empty and two hold scope notes, so the point stands, see S12); the request to
  restate the Holm conservatism and give the four-test value 0.033 (Appendix A already says it; that family was
  never registered); in debate: "single forward pass", "zero pre-verdict reasoning", "structurally post-hoc",
  "confounding model scale", the reading of Qwen3.8's cut-offs as "unresolved debates", and "textual hooks for
  adversarial framing".
- Fable: "six brought no average gain" in its example titles (Astra's finding 2 is right); "if space allows" for the
  Figure 2 note; "do not" span the gap, now "mostly do not"; the six-judge 44% sentence, the 4.1 pointer and, as a
  requirement, the four-judge base rate (D1); its own figure 1.96%, which is 1.95% in the frozen file; its reliance
  on the readout's zero reasoning tokens for Haiku (that field is never filled for Anthropic calls).
- Opus: the label "contested answer keys", "the answer key is contested on a quarter of the analysed questions",
  "may be a defensible alternative reading", the closing clause on benchmark composition, and the "major" rating of
  that finding (D3); its objection to naming the verdict-first format (D4).
- Astra: its blanket assurance that cross-references point to the right place (4.2 should point to Appendix D,
  Table 13); a blanket change of causal vocabulary (D2).
- Sol: none in round 2; in D2 it narrowed to cross-period headlines only.

## Missed by all

Raised in round 2 or in debate; none has been cross-examined by the other seats except where noted.

1. The gate caveat is not symmetric across the two sides of the peak (Opus). Answer-revealing queries would most
   plausibly help judges, and exposure is about three times higher at six queries than at one. If they helped, the
   rise from none to one and the document's advantage are conservative and the fall from one to six is inflated.
   Wording for Appendix D; the audit cannot say which way they acted.
2. With one query, the oracle's bare label arrives in the same message as the verdict request (Opus, from
   `finalphase/judging.py`; confirmed by Fable and used by Carbon in D4). Add to Appendix E's cautions.
3. Qwen3.8's query-step replies were also cut off at the limit (11, 10 and 6 with one, two and six queries), and a
   cut-off query step is not scored as an error, so the imbalance in cut-offs is partly a product of scoring: a
   reply that runs out of room costs the verdict with no queries and usually only the queries otherwise (Fable).
   This cannot produce the rise at one query; it bears on Qwen3.8's place below its no-query level.
4. The preregistration defines the budget effect with "anticipation, stopping and extra computation included";
   3.5 kept only "how the judge chooses to use it" (Fable). Restoring the words gives S9 a registered footing.
5. How much the gate stopped: the classifier rejected 52 of the 193 sampled claims the auditor labelled violating,
   about a quarter, and the audit covers only claims that reached the classifier (Fable). A reader of 3.4 and the
   4.4 heading will assume most violations were stopped.
6. Appendix H calls the final benchmark "independently validated" and contrasts it with earlier questions "fully
   determined" in only 5 of 82 cases; by the final benchmark's own flags 365 of 1,068 questions drew at least one
   reversing-reading flag, and the validators were models. The Section 2 placeholder invites the co-author to move
   Appendix H's figure into the main text; its caveat bullets must travel with it (Fable).
7. From debate D4, one seat each: the explanation labellers read only the parsed REASONING field, and text before
   the verdict line was not stored with the judgments (Fable); the readout's reasoning-token counts are zero for all
   Anthropic calls because the field is never filled, so they do not show that Haiku wrote no hidden reasoning
   (Fable); on one-query verdict calls the median logged reasoning is 0 for GPT-5.6 Sol and above 0 for Luna, Terra
   and Qwen3.8 (Opus, Fable). Check these against the files before using them.
8. From the D2 jury: the 4.2 prose ("offering up to six queries left judges with higher error than giving them the
   document") carries the same unconditional claim as its heading (Opus juror); the draft already words its
   registered findings descriptively in the 4.1 body and Table 13 (Fable juror).

## Clerk's notes (facts checked by the clerk, no view on the merits)

- Figure 2 caption: confirmed with git. "The budgets are evenly spaced on the axis; 3 to 5 queries were not
  tested." is in `paper/sections/results.tex` at 8370737 and absent from the paper at af4657e. So the statement in
  `paper/review/consult74_response.md` that nothing was deleted from the report is wrong for this one sentence.
- Section 4.5 interface caveat: `paper/review/council-20261110/response.md` lists "judges saw only the oracle's
  one-word answer" as applied in 3.4, 4.5 and Section 6. At both published commits it is in 3.4 and the supplement
  only, as Opus and Fable reported.
- Deterrence: the pre-cut Section 6 (8370737) said debaters "could not adapt to the budget actually offered" and
  did not state that deterrence is already in the baseline; that sentence is in Appendix I. Opus's reading of the
  history is the accurate one; Carbon's and Fable's description of it as cut text is not. The finding itself (S10)
  does not depend on this.
- The frozen code worktree is unchanged after the run (clean at 0e11907); the report worktree is clean at af4657e.
- This council is advisory. The project's review gate for any revision remains a fresh one-shot GPT-6 Astra review
  at ultra effort.
