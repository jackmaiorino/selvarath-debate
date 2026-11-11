# Author response to the council (2026-11-10)

The council's synthesis is `synthesis.md` in this folder. The owner asked for its surviving findings to be applied.
Each factual claim was checked against its source before any text changed. No frozen number changed and no new
outcome analysis was run: every added number is read from the frozen files, is arithmetic on values already
reported, or is typed from a cited project document. The earlier text is preserved in `brief.md` (the full draft as
the council saw it).

## Surviving findings

| # | Finding | What changed | Where |
|---|---|---|---|
| S1 | Oracle graded by its own model | "The oracle was rarely the problem" replaced by the wording agreed in debate D2; same-model caveat added as a fifth caution; reviewer named (GPT-6 Astra) with its coverage (at most 3 of the 56 cases in the largest group); Figure 5a label and title changed | 4.5, Appendix E, Figure 5, Section 6 |
| S2 | "Not supported without the Claude judges" | Replaced by the three sentences agreed in D1, plus the registered six-against-none contrast for that panel; 4.6 and Section 6 reworded; new Table 6 prints the registered contrasts of every sensitivity panel | 4.3, 4.6, Section 6, Appendix A |
| S3 | Overlapping roles, selection | New limitation "Overlapping roles and no human labels"; selection by validators stated in 3.1 and Section 6; strategy note and settings stated in 3.2 and 3.3; models table completed | 3.1, 3.2, 3.3, Section 6, Appendix G |
| S4 | Fixed debates | Stated in 3.2; new limitation "Fixed debates" with no causal claim about deterrence | 3.2, Section 6 |
| S5 | Seven fixed judges | Stated in 3.5 and Appendix A; new limitation; Haiku's share of pooled no-query error (70%) given | 3.5, 4.3, Section 6, Appendix A |
| S6 | "Small" needs its base rate | 4.1 says "small in absolute terms", gives the relative rise (18%) and the design's 10% expectation; development errors split by judge in 3.2 and Appendix B | 4.1, 3.2, Section 6, Appendix B |
| S7 | Cut-off replies | Counts in 3.5 and Appendix A; Qwen3.8 note in 4.3 and Figure 3; new limitation; no direction assigned (D4) | 3.5, 4.3, Appendix A, Section 6 |
| S8 | Gate section | Per-query flag rates added; leak stated as a property of the classifier; "affected share" defined; worst-case paragraph rewritten with the D5 wording | 4.4, Appendix D |
| S9 | Timing | Stated in 3.4, in the interruptions row of the deviations table and as a limitation | 3.4, Appendix B, Section 6 |
| S10 | Stored responses reused | Stated in 3.4 and Appendix D | 3.4, Appendix D |
| S11 | Exploratory section | Pattern counts, the "both" group, Haiku's share, the selection caution, interval method, group sizes, matcher limits and "direction only" added | 4.5, Appendix E |
| S12 | 4.2 heading | Second clause removed; Haiku's share (77%) and the prompt and length difference stated | 4.2 |
| S13 | Statistical presentation | p at the floor; level intervals against paired differences in both figure captions; infinite limits explained; Qwen3.8 correction explained without printing an unregistered value; eight-judge rescoring called algebra; "every registered analysis" | 4.1, 4.3, 4.6, captions, Appendices A and B |
| S14 | Readability | 3.4, 3.5, 4.1, 4.4 and 4.6 rewritten with the seats' replacements, avoiding the disputed phrases | Sections 3 and 4 |
| S15 | Smaller corrections | All applied (control of evidence, development questions, oracle-confirmed claims, gate rule's compound claims, pairing as a design choice, 488 against 487, repeat-pass denominators, oracle floor and readers, internal file pointer removed, the remaining $21) | throughout |

## Debates

D1 to D5 are applied with the agreed wording. Where a debate left a placement open (D1: whether the six-against-none
sentence sits in 4.3), it is in both 4.3 and Appendix A.

## Undebated disputes: the author's choices

- Noise share of harmful flips: no "two thirds" claim was added; the existing caution about run-to-run variation stays.
- p-value floor: "p = 0.0002 ..., the smallest value the procedure can return", not "at most".
- Deterrence and settings: the design facts are stated; no causal explanation is asserted.
- Figure 5b: the selection caution is strengthened; the "exact crossover" and turn-length claims are not used.
- Flagged against affected share: "affected share" is defined as the share of sessions with an admitted rule-breaking query, each assumed to have changed its verdict, which is amendment 3's rule.
- Four-test Holm value: not printed.
- Carbon's replacement sentences: "98% agreement", "stronger model" and "cleanest questions" are not used.
- Coverage caption of the audit table: unchanged (it is amendment 3's wording; both positions are compatible).
- Audit sampling and eight-judge row: no change; 4.6 now says the rescoring gives the same conclusions by construction.
- 4.3 heading: the Opus seat's heading is adopted.
- "Follows directly" and "debaters would adapt": neither is used.
- Floor effect: "little room to improve" only.
- Figure 2 line: an optional note that 3 to 5 queries were not tested is added to the caption.
- Haiku and the cases read: stated as its share of harmful flips, with the note that shares describe the observed mixture.
- "Directly answer-revealing": the category is not renamed.
- "Labelled every admitted query": now "every query those sessions had sent to the gate classifier".
- Astra's shorter worst-case paragraph: the numbers are kept.

## Missed-by-all items

Applied: judges saw only the oracle's one-word answer (3.4, 4.5, Section 6); selection in Table 14's split by oracle
answer; unreadable verdicts inside the flip counts; the Sonnet 5.5 sentence; the oracle's development sample; judges'
output limits; the 20 unflagged turns; the limiting comparison behind the 0.25% tipping point; the definition of
"mean used"; the chronology in the deviations table. Not applied: the comparison of the labelled oracle-error share
with the development error rate, which would set two unlike samples side by side.

## Numbers typed by hand in this revision (not generated)

56, 38, 17 and 1 development errors (`difficulty-next-steps.md`); 703, 103, 113, 65 and 84 questions by validator
flags (preregistration, section 3); 16 of 26 and 13 of 23, 80 to 137, the reviewer's 21 cases and 18 (the frozen
explanation note); 2,000 and 2,416, 487 and 488 (amendment 2, `gate_audit.json`); the oracle's reference readers
(`finalphase/oracle_select.py`); the judges' output limits (preregistration, section 4); "at least 10%" (design).
