## The dispute

Finding 2 of Member A (severity given: major): replies cut off at the output limit are scored as errors and differ by condition.

Defender: Member A (author of the finding). Challenger(s): Member E, Member B.

What is in dispute: how far the cut-off counts license the finding's two interpretive statements: that Qwen3.8's fall below its no-query level is "probably this artifact" and not supported for wrong choices, and that the sign of the pooled six-against-none difference "carries no information".

## The finding as written in round 1 (Member A)

2. [major] Cut-off replies are scored as errors and differ by condition; one stated per-judge result is probably this artifact.
- Passages:
  - Figure 3 caption: "the GPT-5.6 judges stayed elevated, while Haiku and Qwen3.8 fell below where they started."
  - Section 4.3: "while Haiku and Qwen3.8 dropped below their no-query level."
  - Section 4.1: "Error with six queries was 0.05 points lower".
  - Appendix A: "a seven-judge version of that check was not computed."
- Problem: `judging.py` parses a verdict only when the call status is "ok", so a reply cut off at the output limit is always an error. `main_readout.json` records cut-off verdicts (of 2,140 sessions each):

  | Judge | None | One | Two | Six | Debate and world | World only |
  |---|---|---|---|---|---|---|
  | Qwen3.8 | 19 | 6 | 2 | 1 | 12 | 6 |
  | Haiku | 3 | 0 | 0 | 0 | 0 | 0 |

- No other judge has any. `main_analysis.json` gives 57 unreadable verdicts among the seven judges (12,873 minus Llama's 12,816).
- Qwen3.8's drop from none to six is 13 wrong verdicts (57 to 44), smaller than its 18-session difference in cut-off replies. "Fell below where it started" is therefore not supported for wrong choices.
- Pooled, the six-versus-none difference is 7 sessions against a 21-session difference in cut-off replies (22 against 1), so its sign carries no information.
- The rise at one is, if anything, understated: an earlier review counted 24 unreadable verdicts at none and 9 at one.
- The one registered check built for this (unreadable as missing) is the one that could not be run.
- Limits of my check: the counts cover 1,070 questions, so up to four per condition may sit on the two set-aside questions.
- Fix, Section 3.5, after "counts as an error": "Unreadable verdicts were rare (57 of 89,712) but uneven. Nearly all were replies cut off at the output limit, mostly Qwen3.8's: 19 of its no-query sessions against 6, 2 and 1 with one, two and six queries."
- Fix, Figure 3 caption and Section 4.3: "... while Haiku fell below where it started. Qwen3.8's scored error also ended lower, but 19 of its no-query replies were cut off and scored as errors against 1 at six queries, which is more than the drop."
- Fix, Section 4.1: "... 0.05 points lower (7 of 14,952 sessions) ... Cut-off replies were more frequent with no queries (22) than with six (1), so the direction of this small difference should not be read into."
- Add the counts to the Appendix A sentence and to Section 6.

## The defender's own round-2 remarks on this finding (Member A)

2. The helpful-flip count includes cut-off replies. `explanations_counts.json` shows that 15 of the 216 helpful flips have an unreadable no-query verdict followed by a correct one-query verdict, and 7 of the 331 harmful flips have an unreadable one-query verdict. Appendix E reports only the harmful side. Among pairs with two readable verdicts the counts are 324 against 201. Section 4.5 and Table 13 should say so. This extends my round-1 finding 2, which I re-verified in `main_readout.json` (Qwen3.8 cut-off verdicts 19, 6, 2 and 1 at none, one, two and six queries; Haiku 3, 0, 0, 0) and which no other member raised.

## Opening refutation(s) from round 2

Member E:

Member A, finding 2(b): REFUTE: Calling the scored decline an “artifact” overstates what these counts establish. Cutoffs are explicitly part of the registered error outcome, and their frequency does not identify what those sessions would have answered with a larger output allowance; distinguish protocol failures from wrong choices without invalidating the registered measure.

Member B:

Member A, finding 2(b): REFUTE: “The sign carries no information” is too strong: it remains the direction of the registered composite outcome, which includes unsuccessful verdict production. The cut-off imbalance does not establish what the model would have chosen with additional output capacity.
