## The dispute

Finding 2 of Member C (severity given: blocker): the pooled result at six queries and the wording about judges.

Defender: Member C (author of the finding). Challenger(s): Member E.

What is in dispute: two sub-claims: that the pooled pattern is an "aggregation cancellation" or artifact that the headings and Figure 2 obscure, and the proposed heading "Six judges erred slightly more at one query, but only two recovered by six". (The defender has withdrawn the part about the working title.)

## The finding as written in round 1 (Member C)

2. **[Blocker] Title, headings, and summary claims obscure an aggregation cancellation at six queries and misstate what fails when Claude judges are dropped (Working title, Sections 4.1, 4.3, 4.6, Figure 2).**
   - **Concrete failure / evidence:**
     1. *Aggregation cancellation in "no gain at six" and "the peak":* The working title (*"a small and fragile cost of offering one oracle query"*), Section 4.1’s heading (*"One query made judges slightly worse; no gain was detected at six"*), and Figure 2’s caption imply that 1 query hurts slightly while 6 queries returns to baseline (`-0.05` points). In Table 7, the pooled near-zero difference at 6 queries cancels two opposite regimes: `Claude Haiku 4.5`—which contributes **70% of all baseline errors** (`21.21%` vs. `0.23%–2.86%` for the other six judges)—*improves* by `-2.48` points at 6 queries vs. 0 (and `-3.84` points vs. 1 query, contributing **67% of the entire pooled `e1 - e6` drop**, or **97%** together with `Qwen3.8`’s `-1.69`), while all three `GPT-5.6` judges remain **worse at 6 queries than at 0** (`+1.17`, `+1.03`, and `+0.70` points—as high as or higher than at 1 query), and `Sonnet 5.5` / `Opus 5.5` sit near an error floor (`0.23%–1.12%`) where little improvement is possible. Thus, for `GPT-5.6` the cost is not specific to *one* query, while for the sole high-error judge (`Haiku 4.5`) 6 queries helped substantially.
     2. *Misleading shorthand when dropping Claude judges:* Section 4.3’s heading (*"the peak leans on two of them"*) and Section 4.6 (*"Among the checks that could be evaluated, two do not retain support for the finding: it is not supported without the Claude judges"*) invite a reader to infer that 1 query did not hurt non-Claude judges. In fact, across the four non-Claude judges (`Luna`, `Terra`, `Sol`, `Qwen3.8`), mean error rose **more** from 0 to 1 query than in the full pool (`1.96%` to `2.89%`, `+0.94` points vs. `+0.77`, positive in all four); the intersection-union peak test missed (`Holm p = 0.0560`) solely because error **stayed elevated at 6 queries** (`2.53%`, `+0.57` points above 0 queries), shrinking `e1 - e6` to `+0.37` points.
     3. *Unqualified causal headline in 4.1 paragraph 2:* Section 4.1 paragraph 2 reports $p = 0.0002$ without noting (until paragraph 3 and Section 4.4) that it holds only under the imperfect gate.
   - **What would resolve it:**
     - Revise the working title so it does not imply a cost unique to *one* query and reflects both the lack of average gain up to 6 queries and the sub-threshold, gate-fragile nature of the 1-query rise (e.g., *"Limited fact-checking in AI debate: no average gain up to six queries and a small, fragile rise at one"*).
     - Revise Section 4.1’s heading to e.g. *"4.1 Under this gate, one query raised error slightly—below our practical threshold—with no average gain at six,"* add *"under the gate as it ran"* to paragraph 2, and note in Section 4.1 / Figure 2’s caption that the near-zero 6-query mean cancels opposite shifts across judge families (with 6 of 7 judges already near an accuracy ceiling after `Llama 3.3 70B` was lost).
     - Revise Section 4.3’s heading to *"4.3 Six judges erred slightly more at one query, but only two recovered by six,"* and clarify Section 4.6 so readers see that dropping the Claude judges removes the *recovery at six queries*, not the *rise at one query*.

## The defender's own round-2 remarks on this finding (Member C)

WITHDRAWN: Finding 2 (working-title sub-claim only): we withdraw treating the working title itself ("Limited verification in AI debate: a small and fragile cost of offering one oracle query") as a blocker, since it is already marked open and explicitly qualifies the single statistically supported primary contrast with "small," "fragile," and "offering"; all remaining parts of Finding 2 (revising Sections 4.1, 4.3, and 4.6 and Figure 2's caption so they do not obscure the 6-query cancellation across judges or imply that dropping Claude judges removes the 1-query rise) and Findings 1 and 3–8 stand.

## Opening refutation(s) from round 2

Member E:

Member C, finding 2(b): REFUTE: Heterogeneous component curves do not make the prespecified pooled peak an “aggregation artifact.” The working title also does not claim that harm occurs *only* at one query, so that alleged implication does not require replacing it.
Member C, finding 2(c): REFUTE: “Only two recovered by six” is numerically misleading. Four judges decline from one to six queries, and three finish below their no-query rates; Haiku and Qwen supply most of the pooled decline, which is the accurate distinction.
Member C, finding 2(d): REFUTE: The gate caveat does not invalidate the reported p-value for the implemented protocol merely because it appears in the following paragraph and Section 4.4. Adding implementation scope to the heading is useful, but the statistical statement is not unqualified evidence about a compliant gate.
