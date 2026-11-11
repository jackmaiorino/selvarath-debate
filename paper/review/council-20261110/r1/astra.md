VERDICT: revise
CONFIDENCE: 93

FINDINGS:

1. **Major: the strongest exploratory conclusion relies on the oracle model judging its own model’s correctness.**  
   Section 4.5 says “the oracle was rarely the problem,” and Figure 5 labels cases “Judge misread a correct oracle answer.” But Opus 5.5 supplied the oracle answers and assigned the principal explanatory labels. Separate sessions prevent conversational contamination; they do not remove shared interpretive errors. Opus was also a judge. Agreement among these sessions therefore provides weaker validation than the prose suggests.

   The development oracle check used other model readers, which is useful, but it does not independently validate the main-run explanatory labels. Appendix G should identify the oracle-reference readers, explanatory labellers, coordinating model and reviewer, alongside their existing roles.

   **Required replacement:**
   > Under the model-assigned labels, most sampled harmful flips involved an oracle answer judged correct and a verdict explanation that drew an unsupported conclusion from it. The labellers used the same model as the oracle, so shared interpretive errors could understate oracle mistakes.

   Keep the existing warning that this does not explain the net increase. Independent adjudication would require additional work; disclosure is sufficient for this frozen report.

2. **Major: the benchmark limitation does not explain the consequential selection and model-role overlap.**  
   Sections 3.1 and 6 disclose model-generated questions and model validation, but “results may differ on other tasks” is too generic. Fable participated in constructing **every retained question**, either as its world’s author or as a validator, and then served as the dishonest debater. Sonnet served as both honest debater and judge. Opus supplied verification, judged debates and evaluated explanatory evidence.

   Moreover, retention explicitly selected questions that strong model validators answered correctly from the complete document. The very low full-document error therefore concerns a benchmark selected for model solvability under full access. That does not invalidate the within-benchmark comparisons, but it limits their interpretation as evidence about difficult oversight generally.

   **Required addition:**
   > The benchmark was selected for agreement among models given the complete document. Its low full-document error and its treatment effects may depend on that selection. Several models or model families also occupied multiple construction, debate, judging and evaluation roles, allowing shared reasoning patterns and errors that this experiment cannot separate.

   Replace “no model can answer from memory, and we control exactly what the judge knows” with:
   > Newly generated worlds reduce reliance on memorized source facts and let us control the supplied evidence. They do not eliminate learned priors or cues in the questions and candidate answers.

   This is a validity limitation, not evidence of actual contamination or favoritism.

3. **Major: Figure 5b’s measurement limitations are materially underdisclosed, and “the split is stable” is too strong.**  
   Appendix E acknowledges imperfect word overlap, but the frozen explanatory report documents additional facts: the matcher discards **numbers and short negations**, including “no” and “not”; comparison labels used five overlap-selected excerpts rather than complete debates. Those omissions matter particularly in a study whose disputes turn on dates, exceptions and negation. Partial agreement with those labels does not validate attribution against full transcripts. These details appear in the frozen source: [file: paper/reference/explanation_note_20261030.md]

   The threshold sweep preserves the qualitative harmful/helpful imbalance, but not stable classifications or counts: the dishonest-wording harmful count ranges from 80 to 137. Therefore:

   - Replace “A mechanical check points the same way” with “A separate descriptive word-overlap analysis found a related association.”
   - Replace “The split is stable” with “The direction of the harmful/helpful imbalance persists across the nine thresholds; group membership and counts change.”
   - Restore the tokenizer and excerpt limitations in Appendix E.
   - Add the already-frozen group sizes to Figure 5b or its caption: 873 dishonest-wording pairs, 5,171 shared and 7,838 honest-wording pairs. The bars are counts, not comparable group error rates.

   No new analysis is needed.

4. **Minor: the exploratory intervals lack their actual method and population of inference.**  
   Appendix E calls them sampling intervals conditional on labels, but omits that they are **approximate normal intervals with finite-population correction**, treating hash selection as sampling within oracle-answer strata. The frozen source also warns that sparse categories can have poor coverage. Merely saying the “unclear” category lacks a reliable interval understates that broader limitation.

   **Required supplement wording:**
   > These approximate normal intervals use stratified sampling with finite-population correction and describe the fixed population of 331 observed harmful flips. They condition on the labels and do not include labelling uncertainty. Coverage can be poor for sparse categories, particularly where a sampled stratum contains no examples.

   Also clarify that the percentages describe the observed mixture of harmful flips, not an equally weighted average of per-judge mechanism percentages. Figure 5a’s title should be **“Labels assigned to harmful-flip explanations”** rather than “What went wrong.” The existing warnings about harmful-only selection, run variation and unfaithful explanations are good and should remain.

5. **Minor: uncertainty and missing-judge robustness need a sharper scope statement.**  
   The world bootstrap appropriately preserves dependence among questions, judges and answer orders within a world. It does not make the selected seven judges a representative sample of models. Appendix A should explicitly say the inference concerns this fixed panel and protocol, under the world-sampling assumptions.

   The lost-Llama calculation is mathematically correct: assigning it constant error contributes zero to every contrast and scales the remaining contrasts by seven-eighths. Consequently, identical p-values are algebraically expected. This is a check on the original scoring convention, **not evidence that actual Llama judgments would preserve the result**.

   Add:
   > Counting the unavailable judge as always wrong checks the original coding rule; it does not recover the planned eight-judge estimand or establish robustness to that judge’s unknown responses.

   Likewise, losing significance after removing Claude judges does not itself establish a model-family interaction. The present descriptive account of Haiku and Qwen is stronger evidence for the stated heterogeneity than the significance threshold crossing.

6. **Minor: the main-text headings and several passages need tighter scope and less statistical detail.**  
   The working title is broadly defensible because it says “small,” “fragile” and “offering.” Section 4.1 is less careful: “One query made judges slightly worse” can suggest a received oracle answer and a general model effect.

   **Suggested headings:**
   - **4.1:** “Offering one query slightly increased pooled error; six showed no average gain”
   - **4.2:** “Full-document conditions had lower pooled error”
   - Retain 4.3’s heterogeneity emphasis and 4.5’s explicit exploratory designation.

   The main readability problems are Section 3.4’s rejection-agreement arithmetic, Section 4.1’s simultaneous-bound explanation, and Section 4.4’s two numerical tipping thresholds. Keep their machinery in the supplement.

   **Section 3.4 replacement:**
   > The replacement deployment failed our predeclared agreement check and also varied across repeated evaluations. We therefore cannot treat it as equivalent to the development gate. Every main-run query used the replacement deployment, whose admissions we audited afterward.

   **Section 4.1 practical-size replacement:**
   > The increase was below our predeclared 1.5-point threshold for practical importance. Under the implemented gate, the simultaneous confidence bounds also exclude a peak that large. The gate sensitivity analysis prevents extending that conclusion to a protocol that consistently enforces the query rule.

   **Section 4.4 replacement:**
   > Our prespecified worst-case check cannot exclude the possibility that admitted rule-breaking queries account for the peak. It also prevents ruling out a practically important peak under a rule-compliant protocol. This calculation bounds possible effects; it does not estimate how many verdicts the gate actually changed.

   Keep the observed effect and its ordinary interval in the main text. The detailed thresholds and simultaneous bounds remain available in Tables 3 and 12.

7. **Minor: a consequential protocol detail is missing from Methods: repeated gate and oracle requests reuse stored responses.**  
   Request identifiers omit judge and query budget: identical gate requests are shared within a question, and identical oracle requests within a world. The store reuses completed responses. Thus repeated appearances of an oracle outcome are not necessarily independent oracle calls. This is visible in [file: finalphase/judging.py] and [file: finalphase/store.py]

   **Required addition:**
   > Identical gate and oracle requests reused stored responses across judge sessions. Reported oracle-outcome counts count appearances within sessions, not distinct provider calls.

   This does not undermine the world-level bootstrap, which retains those within-world dependencies. It matters for reproducing the implemented protocol and understanding its stochastic behavior.

8. **Minor: fix two precise reporting ambiguities.**  
   Appendix B’s “after every analysis had been recorded” contradicts the explicitly later exploratory analysis. Change it to **“after the prespecified main-run analyses had been recorded.”**

   Figure 3 and Table 7 should describe the infinite endpoint as a failure of the registered interval procedure to produce a finite upper bound. Error probability itself is bounded. A clearer caption is:
   > For this sparse cell, the bootstrap-t procedure produced no finite upper endpoint; the asterisk marks that computational result.

   The figure’s existing asterisk disclosure is honest, but a marker extending upward would communicate the missing upper uncertainty better than drawing only the lower half.

STRONGEST COUNTERARGUMENT TO MY OWN VERDICT: The draft already handles most obvious objections unusually carefully. It distinguishes offering queries from receiving answers, reports the failed gate check prominently, acknowledges judge dependence, separates statistical support from the practical-size threshold, and explicitly denies causal identification by the exploratory analysis. The registered primary calculation is coherent, and I found no numerical contradiction that threatens its narrow conclusion. One could therefore approve it as a public draft with editorial corrections. I recommend revision because the same-model oracle assessment and the omitted word-matcher limitations affect how readers interpret the proposed explanation, while several inexpensive wording changes would prevent broader claims from being inferred.

ANSWERS:

1. The narrow implemented-protocol result is supported; the strongest overstatement is “the oracle was rarely the problem,” and benchmark information-control language also needs qualification.
2. No consequential numerical inconsistency found; the differing session totals reflect the two exclusions. Correct the exploratory chronology and clarify occurrence counts versus distinct calls.
3. The title is defensible; 4.1 needs “offering” and “pooled.” A referee’s attack on generalization to rule-compliant verification would land; an attack claiming the failed gate check automatically invalidates the observed primary comparison would not.
4. The intersection-union test, Holm corrections, world bootstrap and simultaneous-bound logic are coherent. Llama’s constant coding is algebraic robustness only. Keeping uncertainty fixed is defensible for the stated bounded-displacement sensitivity, but its tipping points are not counterfactual reanalyses or estimates of gate-caused changes. Restore the exploratory interval qualifications.
5. Yes: benchmark selection for full-document solvability and overlapping author, validator, debater, judge, oracle and labeller roles need explicit consequences, not just a generic model-generated-benchmark caveat.
6. Mostly well hedged, but the oracle conclusion, Figure 5a title, matcher disclosure and threshold-stability wording require revision.
7. Sections 3.4, 4.1 and 4.4 are the main readability pressure points; replacements are provided above, with technical detail retained in the supplement.
8. Revise before public sharing. All required changes are disclosure, interpretation or presentation changes; none requires rerunning outcomes or altering frozen estimates.


