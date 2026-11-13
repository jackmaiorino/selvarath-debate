POSITION: MAINTAIN

ARGUMENT: A and C now accept retaining causal language (“effect of offering”) in Section 3.5 once the stability assumption is stated, narrowing the dispute to whether the 4.1/4.2 headings, Figure 2 caption, and title must replace intervention verbs (“raised”, “beat”, “cost”) with descriptive comparatives based on an illustrative $+0.90$ to $+1.00$ percentage-point overnight period shift.

That counterexample fails quantitatively and editorially:

1. **A’s Haiku localization is mathematically impossible in the frozen data.** A argues that a $+1.00$ point pooled period shift need not be synchronized across providers because “it could be concentrated in Haiku.” In Table 8, Haiku’s observed $e_1 - e_0$ change is only $+1.36$ points ($21.21\%$ to $22.57\%$), which contributes just $+1.36 / 7 = +0.19$ points to the $+0.77$ pooled rise. Moreover, in the registered sensitivity check excluding all three Claude judges (Table 5), the pooled $e_1 - e_0$ rise across the four non-Claude judges (GPT-5.6 Luna/Terra/Sol and Qwen3.8) is $+0.94$ points ($p = 0.0001$), *larger* than the seven-judge estimate. Reversing the sign of $e_1 - e_0$ via temporal drift therefore requires an unannounced, simultaneous $\sim 1$-point overnight degradation across independent non-Claude API providers on pinned model endpoints, while Haiku’s 6-query error ($18.73\%$) simultaneously fell $2.48$ points *below* its pre-outage no-query rate.
2. **Even under A and C’s counterexample, offering one query incurs a causal cost.** Because the post-relaunch contrast $e_1 - e_6 = +0.82$ ($p = 0.0001$) is contemporaneous, A and C’s own model leaves intact that offering one query *causes* $+0.82$ points more error than offering six. A causal cost of restricting verification to one query remains established either way.
3. **Proportionality across caveats.** The paper’s primary, empirically measured threat is the query gate (11.4%–33.2% of sessions flagged against a 0.25% tipping point). Yet the draft rightly keeps active verbs in 4.1, 4.2, and Figure 2 and qualifies them via Section 4.4 and “fragile” in the title. Scrubbing intervention verbs from headlines for a zero-evidence 24-hour timing gap treats a speculative hypothetical more strictly than an observed compliance failure.

IF CONCEDING: Not applicable.

WHAT WOULD SETTLE IT: A timestamped comparison of the early-finishing query-condition sessions collected before the 2026-10-27 outage against post-relaunch sessions on matched questions, or provider logs showing model-weight invariance across the 24-hour pause. Without new analysis, the agreed Section 3.4/3.5 stability qualification suffices.

NOTHING NEW: NO