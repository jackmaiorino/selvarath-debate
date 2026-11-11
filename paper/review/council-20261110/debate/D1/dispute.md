## The dispute

Finding 1 of Member A (severity given: blocker): how the draft reports the registered check without the Claude judges.

Defender: Member A (author of the finding). Challenger(s): Member E.

What is in dispute: whether the draft's sentences "reverse the meaning" of that check and must be replaced by the finding's fuller text (including the registered six-against-none contrast for the remaining judges and the statement that the pooled result at six is a balance of opposite movements), or whether they are misleading shorthand that needs clarifying without wording that implies the fall is absent or that model families differ.

## The finding as written in round 1 (Member A)

1. [blocker] The "without the Claude judges" check is reported in a way that reverses its meaning for the headline, and registered sensitivity outputs are missing.
- Passages:
  - Section 4.3: "Without the three Claude judges the peak at one query is no longer statistically supported (Appendix A)."
  - Section 4.6: "two do not retain support for the finding ... it is not supported without the Claude judges".
  - Section 6: "the peak is not supported without the Claude judges."
  - Appendix A: "Table 5 lists the registered sensitivity analyses."
- Problem: `main_analysis.json` (`sensitivity.without_claude_judges`, also `reference/report_tables_20261029.md` section E) shows that only the fall from one query to six fails, and narrowly. On the registered five-judge scale (Llama constant):

  | Contrast, without Claude judges | Estimate (points) | p |
  |---|---|---|
  | One query vs none | +0.75 (+0.94 over the four judges with data) | 0.0001 one-sided |
  | One vs six | +0.29 | 0.028; Holm 0.056 |
  | Two vs none | +0.39 | 0.002 |
  | Six vs none (registered pooled secondary) | +0.46, interval +0.16 to +0.80 (+0.57 over four judges) | 0.006 two-sided, unadjusted |

- So the rise is intact and larger than the seven-judge 0.77, and in that panel six queries are worse than none. A reader, or the co-author, will take the three sentences to mean the one-query cost depends on Claude judges, which is the opposite.
- The pooled "no gain at six" (-0.05, 7 sessions) is +62 wrong verdicts for the three GPT-5.6 judges against -53 for Haiku.
- Preregistration 7.5 registers "the primary, and the two pooled secondary contrasts" for these panels and all six secondaries for the 806-question subset; Table 5 prints only yes/no and one p-value.
- On the 806 questions the one-query rise is 0.48 registered-scale (0.55 seven-judge scale) against 0.77 overall. Section 4.6 omits this while reporting the favourable two-query result.
- Fix, Section 4.3: "The registered check without the three Claude judges shows the same split. The rise from none to one is intact (0.94 points over the four remaining judges; p at most 0.0001). The fall from one to six is weak (0.36 points; p = 0.028, 0.056 after correction), so the registered peak is not supported in that panel. For those four judges error with six queries was 0.57 points higher than with none (p = 0.006, not adjusted). The pooled 'no difference at six' is therefore a balance of opposite movements, lower error for Haiku and higher error for the GPT-5.6 judges."
- Fix, Section 4.6: "... without the Claude judges the fall from one query to six is no longer supported, although the rise from none to one is (Section 4.3) ...". Add: "On the 806 questions the one-query rise is smaller (0.55 against 0.77 points) and still supported."
- Fix, Section 6: "The rise at one query appears in six of seven judges and survives leaving out the Claude judges. The fall by six does not: about two thirds of it is Haiku 4.5 and most of the rest Qwen3.8 (82 and 36 of the 122 net sessions)."
- Fix, Table 5: add the two estimates per row, rescaled to the judges that produced data, and a companion table of the registered secondary contrasts for each sensitivity panel.

## The defender's own round-2 remarks on this finding (Member A)

- A correction that strengthens finding 1, not a withdrawal: my round-1 counterargument called the six-against-none contrast without the Claude judges "unadjusted or descriptive". Preregistration 7.5 registers it (two-sided, unadjusted) for that panel, so Table 5 omits a registered output.

## Opening refutation(s) from round 2

Member E:

Member A, finding 1(b): REFUTE: The existing statement does not reverse the registered result: “peak” explicitly requires both comparisons, and that conjunction fails the corrected test. The problem is potentially misleading shorthand, not a false statistical conclusion; neither p = 0.056 nor its change establishes that the decline is absent or that model families differ significantly.
