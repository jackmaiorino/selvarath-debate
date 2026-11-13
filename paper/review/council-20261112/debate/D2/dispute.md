## The dispute

Finding 1 of Member A and finding 1 of Member C (severity given: major, the same point in both): the collection-order split and the causal wording of the headline results.

Defender(s): Member A, Member C (author(s) of the finding). Challenger(s): Member D.

What is in dispute: NOT whether the main text should state that comparisons between query and non-query conditions assume stable judge behaviour across the two collection periods: all five members agree that it should. In dispute is the further step both findings ask for: replacing intervention wording with descriptive wording in the 4.1 and 4.2 headings, the Figure 2 caption, Section 3.5 and the title ("raised", "the effect of offering", "cost", "beat" becoming "was higher with", "had lower error than", "error increase"). Member D holds that this goes too far for an assigned, paired intervention whose one-against-six leg does not cross the split, and that stating the assumption is enough.

## The finding as written in round 1 (Member A)

1. **Major: the collection-order disclosure does not adequately qualify the causal wording.**  
   Section 3.4 discloses that the conditions without queries finished before the outage, while most query-condition verdicts came later. Nevertheless, Section 3.5 says the comparisons “measure … the effect of offering a query budget,” Figure 2 says offering a query “raised error,” and the title calls the difference a “cost.”

   The chronology is an existing, acknowledged limitation, not a newly discovered defect. What remains missing is its implication: attributing comparisons between query and non-query conditions to the intervention assumes that judge behaviour stayed stable across collection periods. Pairing questions and bootstrapping worlds do not remove a change shared across a collection period. This particularly affects comparisons against no queries and against the document conditions. It is **not evidence that drift occurred**, or that drift explains the results.

   **Required repair:** replace the final two sentences of Section 3.5 with:

   > “These comparisons concern budgets offered, including judges’ decisions about using them, rather than individual oracle answers. Interpreting the differences causally assumes stable judge behaviour across collection periods (Section 3.4).”

   This is shorter than the existing passage. Prefer descriptive wording in Figure 2: “Error was slightly higher with one query offered; with six, it was close to the no-query rate.” A suitable resolution of the open title is:

   > “Limited verification in AI debate: a small error increase under an imperfect query gate”

   The observed differences and registered significance results can remain unchanged.

## The finding as written in round 1 (Member C)

1. **Major: the collection timing needs an explicit inferential qualification in the main text.** Section 3.5 says the comparisons measure “the effect of offering a query budget.” Section 3.4 discloses that the conditions without queries finished before the outage and most query-condition verdicts followed the relaunch. Appendix I acknowledges that stability across this interval was not tested. Together, these support a causal interpretation only under an assumption of stable judge behaviour. World-level pairing and bootstrapping do not remove a change shared across collection periods.

   This is a residual problem with the earlier council’s timing finding: the chronology survives in the main text, but its implication lives only in the supplement. There is no evidence here that drift occurred.

   Replace the last two sentences of Section 3.4 with this shorter wording:

   > The conditions without queries finished before the outage; most query-condition verdicts came after the next day’s relaunch. A causal reading assumes stable judge behaviour across these periods, which we did not test.

   Make the result headings descriptive as well:

   - **4.1:** “Pooled error was higher with one query and similar with six”
   - **4.2:** “Document access had lower error than six queries”

   The first also avoids making “no average gain” sound like an established zero effect.

## Opening refutation(s) from round 2

Member D:

  - *Endorse:* Mapping *which* contrasts cross the ~1-day gate-outage gap (`1 vs. 0`, `6 vs. 0`, `document vs. 6`) vs. which do not (`1 vs. 6`, `1 vs. 2`), and stating in Section 3.4 that cross-gap comparisons assume untested day-to-day judge stability (joining **B3** and **E2**).
  - *Push back:* **A1** and **C1** go too far in scrubbing intervention wording ("raised", "cost") into purely observational phrasing everywhere. First, this is a paired controlled intervention, not an observational study. Second, the primary peak test requires **both** $e_1 - e_0 > 0$ **and** $e_1 - e_6 > 0$; the $e_1 - e_6$ leg (`+0.82` points, $p = 0.0001$) is **contemporaneous** (post-relaunch) and cannot be explained by day-to-day drift. Third, on $e_1 - e_0$, six of seven judges across **three independent providers** (OpenAI, Anthropic, Alibaba/Qwen) moved upward simultaneously, while Sonnet 5.5 did not. Stating the stability assumption in Section 3.4 is accurate; writing as if query budgets were merely observed covariates is not. Likewise, in **B3**, inserting a post-hoc three-judge GPT-5.6 subgroup average (`1.72%` vs. `2.61/2.39/2.68%`) into Appendix I risks post-hoc cherry-picking when persistent confirmation of true dishonest facts (Section 4.5, pattern 1) predicts the same step shape.
