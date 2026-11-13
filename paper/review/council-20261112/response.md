# Author response to the second council (2026-11-13)

The council's synthesis is `synthesis.md` in this folder. The owner asked for its agreed changes to be applied.
Each factual claim was checked against its source before any text changed: the frozen analysis files in
`paper/data`, the frozen code and preregistration, and the reviewed 2026-10-29 report in `paper/reference`. No
frozen number changed and no new outcome analysis was run. Every added number is a macro generated from the frozen
files, with an assertion in `scripts/build_generated.py` for each claim made in words ("about a quarter", "five of
the seven judges", "no other judge moved by more than 0.3 points", which judges end above or below their no-query
level). The 164 earlier macros are unchanged; 16 were added.

## Surviving findings

| # | Finding | What changed | Where |
|---|---|---|---|
| S1 | What the time split implies | Two sentences: comparisons of a query condition with the no-query or document conditions largely cross the split and assume the judges behaved the same on both sides, which was not tested; comparisons among budgets largely do not. 3.5 now says "given the stability assumed in Section 3.4". The limitation in Appendix I names the three comparisons. | 3.4, 3.5, Appendix I |
| S2 | Section 4.2 | Heading is now "With the document, judges erred less than with six queries". The built-in advantage, the different judge instructions and the different collection time are stated in 4.2. The pointer is repaired: 4.4 now gives this comparison's limit (1.40%). "Error fell to" is now "error was". | 4.2, 4.4 |
| S3 | Interface caveat for the second pattern | "Judges were not told that the oracle answers only from what the document states, so this second pattern may partly reflect the interface." "One-word answer" is now "bare label". | 4.5, 3.4, Appendix I |
| S4 | Section 4.4 | (a) flags are split into breaking the rule (7.1%) and possibly breaking it (3.6%), and the worst-case paragraph says "flagged" throughout; (b) the check is said to come from an amendment, before the run resumed and before any result was seen; (c) 4.1 now says the bounds at two queries are lower; (d) the third paragraph was rewritten and is shorter; (e) the sentence on verification that stays within the rule is back. "Not robust" is kept, and "could not have cleared" is tied to the three named findings at their observed sizes. "By this auditor's standard" added. | 4.4, 4.1, Appendix D |
| S5 | Section 3 opening and the panel | "A dishonest debater expected to be stronger"; "the original design, hypotheses and analysis"; "three amendments written before any result was seen"; 3.3 says an amendment written before any result was seen made the seven judges the primary panel. | 3, 3.3 |
| S6 | Heading 4.1 | "Pooled error was higher with one query offered; with six it was close to the no-query level". | 4.1 |
| S7 | Validator flags | Defined in 3.1 with the count (262 of 1,068 in at least two of the four responses). | 3.1 |
| S8 | Who supplies the rise and the fall | Haiku supplies about a quarter of the rise (29 of the 115 net verdicts). The declines from one query to six are given by size: Haiku 3.84 and Qwen3.8 1.69 points, together 0.79 of the 0.82 pooled points; no other judge moved by more than 0.3 points. Sonnet 5.5 (below) and Opus 5.5 (above) are now in the sentence on no-query levels. Table 8 is cited. | 4.3, Appendix I |
| S9 | Haiku and room to write | One sentence in 4.3 and a note under "What is measured" in Appendix I. 3.5 and Appendix I restore the registered words "anticipation, stopping and extra computation". | 4.3, 3.5, Appendix I |
| S10 | Section 6 | The deterrence consequence is stated (the warning is in every condition, the no-query baseline included, and is not measured). The pointer is "Sections 3 and 4". Appendix I's opening sentence is corrected. The pointer to Appendix I is now in the prose and not only in the co-author note. | 6, Appendix I |
| S11 | Figure 2 caption | "The budgets are evenly spaced on the axis; 3 to 5 queries were not tested." restored. | Figure 2 |
| S12 | Small repairs | 3.5 says the cut-off counts are over all 1,070 questions; Appendix E no longer says "misreadings"; Table 9 and its paragraph say "touched none" includes sessions with no admitted query; Appendix G reconciles the token-priced gate record ($8) with the deployment's posted charges; Table 15 has no empty cells and its last column is headed "Setting or scope"; 3.2 gives the direction of the debaters' settings; "the gate as it had run in development"; "ruling out a larger peak"; 4.6 cites Appendices A and D; Appendix I says no result was registered or computed by world author; Table 7 says recovery closed before any result was computed (recovery closed at 21:13 UTC on 2026-10-29 and the analyses ran from 21:14, per the reviewed report). | several |
| S13 | Working title | Changed to "Limited verification in AI debate: a small error increase under an imperfect query gate", the alternative from consult 69 that two seats proposed again. It remains a working title for the authors to settle. | `main.tex` |

Optional additions that no seat opposed: the bound on cut-off replies (at most 0.15, 0.04 and 0.01 points of the
pooled rate at none, one and six queries) is in Appendix A. The clause on query use in 4.3 was left out for space;
the figures are in Appendix C.

## Debated disputes

| # | Outcome in the synthesis | What changed |
|---|---|---|
| D1 | Add "Haiku supplies only about a quarter of the rise" | Applied in 4.3. The optional four-judge base rate was not added. |
| D2 | Jury 2 to 0 for a descriptive 4.1 heading and Figure 2 caption; 1 to 1 on the 4.2 heading and the title | Heading 4.1 and the Figure 2 caption are descriptive. The 4.2 heading and its prose were also made descriptive, because S2 (all five seats) asked for a narrower heading in any case. The title no longer says "cost". 3.5 keeps "the effect of offering" with the stability assumption stated, as the defenders accepted. |
| D3 | "Contested answer keys" withdrawn; a factual bullet agreed | The agreed wording is in Appendix I as "Questions flagged for another reading", word for word. Appendix A says the 53 of 115 is arithmetic on two registered estimates and not a separate test. |
| D4 | Strong claims about Haiku withdrawn; narrow disclosure agreed | 4.3 carries the agreed sentence. 4.5 says the judges' reasons were requested after the verdict line, and "only harmful flips were read" stays. Appendix E adds three facts checked in the code and the records: the reasons read are the text under the REASONING line and no other text of the verdict reply was kept with the judgment records; with one query the oracle's answer and the verdict request arrive in one message; compliance with the requested order was not counted. The seats' sentences about hidden reasoning were not used (see below). |

## Disputes the council did not debate: the author's choices

| Sub-claim | Choice | Reason |
|---|---|---|
| Report the 86.4% YES share as evidence of deterrence | Not applied | Four seats against: judges chose what to check, and no condition lacks the warning. |
| Retitle 4.4 "non-robust" | Not applied | Four seats against. |
| More bounds and p-values in the main text | Not applied | The co-author's rule for the main text. The dangling reference to two queries is fixed with a few words in 4.1. |
| The GPT-5.6 flat-step sentence for Appendix I | Not applied | Seats split two to two; it is a post hoc subgroup pattern that cannot separate the two readings. |
| "Could not have been passed" without a condition, and dropping "not robust" | Not applied | Three seats against; the amendment fixes the label, and the document comparison's limit is above the zero-flag bound. |
| Section 6 naming the four main caveats | Not applied | Seats split three to two; Section 6 is at its page budget and the co-author will merge it into the discussion. This one is worth the authors' attention when the discussion is written. |
| "Under a gate that leaked" in the title | Not applied | "Imperfect query gate" was preferred by two seats and overstates less. |
| Clustering in the 4.5 intervals | Not applied | Four seats against; the intervals are for a sample from the 331 observed flips. |
| The replacement paragraph for 3.4 | Not applied | Seats split two to two; it drops "neither equivalent nor shown to differ by much". |

## Points the council listed as missed by all

These were raised by one seat each and not cross-examined. Those that are facts and could be checked were applied
in the supplement; the interpretive ones were not.

| # | Point | Choice |
|---|---|---|
| 1 | The gate caveat may act differently on the two sides of the peak | Not applied. It rests on a guess about which way rule-breaking queries act, which the audit cannot check. Left for the authors. |
| 2 | The oracle's label arrives in the same message as the verdict request | Applied in Appendix E (checked in `finalphase/judging.py`). |
| 3 | Qwen3.8's query-step replies were also cut off (11, 10 and 6) and are not scored as errors | Applied in Appendix A (counts from the frozen readout; the scoring checked in the code). |
| 4 | The registered estimand includes "extra computation" | Applied in 3.5 and Appendix I (checked in the preregistration, section 2). |
| 5 | The classifier rejected 52 of 193 sampled claims labelled violating, and the audit covers only claims that reached it | Applied in Appendix D ("about a quarter"; claims stopped by the mechanical checks were not labelled). Not added to the main text. |
| 6 | Appendix H's "independently validated" | Applied: "checked by models that had not written each world", with the 365 of 1,068 questions that drew at least one flag, and a note that the two audits are not comparable. The co-author placeholder in Section 2 now says the reading notes should move with the figure. |
| 7 | Facts from debate D4 raised by one seat each | The two that were checked are used (item 2 above and the REASONING field). The statements about reasoning-token counts were not used. |
| 8 | The 4.2 prose made the same unconditional claim as its heading | Applied with S2. |

## The page budget, and what left the main text

The author's part of the main text (title block, Sections 3, 4 and 6) measured 6.06 pages before this revision and
6.07 after, by the same script. The additions were paid for by tightening sentences. The report is now 24 pages (the
supplement grew by one).

No substantive content was dropped from the report. These are the places where the main text now says less than
before, with where the content now is (in the same words or in equivalent ones):

| Main text before | Now | Where the content is |
|---|---|---|
| 3.4: "some sessions had already finished after an immediate stop or a failed first query step" | "most query-condition verdicts came about a day later" | Table 7, interruptions row (the detail was added there) |
| 3.4: "A single rule stated with its conditions, or a single list, counts as one fact" | pointer to the full rule | Appendix F, gate rule |
| 3.4: the oracle's check "from three other models" | "model-set reference answers" | Appendix G |
| 3.5: "So the registered error measure is not the same as wrong choices among completed verdicts." | removed | Appendix I, "Cut-off replies" |
| 4.1: "Before the run we set 1.5 points as the smallest peak that would matter in practice" | "the 1.5 points set in advance (Section 3.5)" | 3.5 |
| 4.3: "With so few errors elsewhere, there was little room for queries to improve most judges' accuracy." | removed | Appendix I, "Little room to improve" |
| 4.4: the list of what the narrower flag category contains | "flags that could reveal the answer (Appendix D)" | Appendix D, audit paragraph |
| 4.4: "(averaged over judges)" and "The robustness decision uses the audit's upper bound." | removed | Appendix D, worst-case analysis (the second sentence was added there) |
| 4.5: "These readings describe the cases read and do not identify what caused the net increase." | "These readings do not identify what caused the net increase." | Appendix E, cautions |

Two sentences were replaced, not moved, because the council found them wrong or unsupported as written: 4.2's
"Under this protocol, offering up to six queries left judges with higher error than giving them the document" and
its pointer "not robust to the gate analysis of Section 4.4".

## Corrections to earlier records

- `review/consult74_response.md` said nothing was deleted from the report in the second trim. That was wrong for
  the Figure 2 caption sentence (S11). A dated correction is appended to that file.
- `review/council-20261110/response.md` listed the one-word-answer caveat as applied in 3.4, 4.5 and Section 6. It
  was in 3.4 and the supplement only. A dated correction is appended to that file.

## Not done

- The council is advisory. The independent review of this revision is consult 75 (`review/consult75_*`).
- The single-seat claims about hidden reasoning in debate D4 were not checked and are not in the paper.
- The count that would settle D4 (how often the verdict line comes first in the stored replies) was not made.
