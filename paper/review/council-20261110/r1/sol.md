VERDICT: revise before public sharing; the conditional primary result is defensible, but several interpretations and disclosures need tightening.
CONFIDENCE: 91

FINDINGS:

1. **Major: the exploratory section understates the consequences of models evaluating their own model family, especially the oracle evaluating itself.**  
   Locations: Sections 3.1, 3.4, 4.5 and 6; Appendices E and G. Checked against [file: paper/main.pdf].

   Fable wrote half the benchmark and validated the other half, then supplied every dishonest argument. Sonnet supplied every honest argument and also judged. Opus served as oracle, judge, gate-audit labeller and explanation labeller. Separate sessions prevent conversational carryover; they do not make these models’ interpretations independent.

   This matters most for “the oracle was rarely the problem” and Figure 5’s categorical “judge misread a correct oracle answer.” Opus is assessing whether Opus’s answers were correct and attributing the apparent failure elsewhere. That does not establish that the labels are wrong, but it creates a specific common-error threat that “labels assigned by a model” does not adequately communicate. Cross-model benchmark validation and blinded development reference labels are useful safeguards, but do not remove this threat in the exploratory coding.

   **Required change:** add an explicit role-overlap limitation and qualify the attribution where it appears:

   > “Under the Opus-assigned labels, an estimated 74% of harmful flips contained a correct oracle answer that the judge’s written explanation used to support an unwarranted conclusion; about 6% were labelled oracle errors. Because Opus supplied both the oracle answers and these labels, shared interpretation errors could affect that division.”

   Identify the “separate reviewer model” in Appendix E. Retain the existing warning that this coding does not explain the net increase. No new labelling is required for sharing a properly qualified draft.

2. **Major: specify the population and oversight problem to which the inference applies.**  
   Locations: Sections 3, 3.1, 3.2, 3.5 and 6; Appendix A.

   The world bootstrap supports uncertainty over worlds under this benchmark-generation process, for this fixed panel. It does not turn seven selected judges, including several related models, into a representative sample of AI judges. Section 4.3 describes heterogeneity well, but the statistical target should be explicit:

   > “The pooled estimate describes these seven judges, weighted equally. Confidence intervals reflect variation across worlds, conditional on the selected models and protocol.”

   Two further scope qualifications are needed:

   - **Restricted access versus limited competence.** Judges given the document alone achieved 0.59% error. This tests oversight when access to readily usable evidence is restricted. It does not establish how verification behaves when a fully informed judge lacks the competence to solve the task.
   - **Benchmark cues remain possible.** Invented worlds reduce reliance on memorized scenario facts, but do not justify “we control exactly what the judge knows.” Models still bring prior conventions and may exploit cues in questions, answer wording or argument style. Without a question-and-candidate-answers-only condition, this study cannot establish how much no-query accuracy depends on the debate.

   Also soften “the dishonest debater is the stronger model.” The development comparison supports the choice of a pairing that produced more judge errors; it does not separately identify capability, role, effort and verbosity.

   **Required change:** disclose these boundaries, replace “control exactly” with “control which study materials the judge receives,” and describe the intended capability asymmetry as a design choice rather than an experimentally established ranking. These are interpretation changes, not requests for new controls.

3. **Major: the causal interpretation has an undisclosed acquisition-time assumption.**  
   Locations: Methods and limitations.

   The driver gathers requests in rounds. Direct verdicts complete in the initial judging round, while query-condition verdicts require later rounds. Consequently, conditions were not interleaved at the point of final judgment. The implementation makes this visible in [file: finalphase/engine.py] and [file: finalphase/judging.py].

   This does **not** demonstrate provider drift, and ordinary stochastic variation does not by itself invalidate the aggregate contrast. However, the causal reading assumes that the relevant provider behavior remained stable across acquisition rounds. Freezing request code and model identifiers does not independently verify that assumption. The service disruptions make acquisition chronology particularly relevant.

   **Required change:** disclose the scheduling and add a bounded limitation:

   > “Direct verdicts were obtained before query-condition verdicts completed their later acquisition rounds. We therefore cannot separately exclude changes in provider behavior across rounds as a contributor to the small condition differences.”

   Do not imply that such changes were observed. Resolving this empirically would require additional controls or analysis; disclosure suffices within the closed study.

4. **Minor: Appendix E needs enough detail to interpret and reproduce the exploratory estimates, and “the split is stable” needs narrowing.**  
   Locations: Figure 5, Section 4.5 and Appendix E.

   The draft supplies the sample strata and weights, but does not identify how its exploratory sampling intervals were calculated. The frozen explanation note specifies **approximate normal intervals with finite-population correction**, treating hash selection as sampling within strata. State that in Appendix E and distinguish these intervals from the main world bootstrap. [file: paper/reference/explanation_note_20261030.md]

   Figure 5b should also give the underlying group sizes, perhaps in its caption: dishonest wording, 873 pairs; both, 5,171; honest wording, 7,838. Those frozen denominators make clear that the bars are counts of discordant pairs, rather than comparable frequencies of choosing each source. [file: paper/data/explanations_mechanical.json]

   Finally, category membership and counts change across the nine threshold settings. What remains stable is the **direction of the descriptive association**, not an invariant split. Replace:

   > “The direction of the association was unchanged across the nine threshold settings examined, although category membership and counts varied.”

   **Required change:** add the interval method, define the selected matching thresholds and overlap measure, identify the reviewer, and make the rubric available or disclose its availability. Replace “A mechanical check points the same way” with “A separate word-overlap analysis showed a descriptive association.” The existing post-selection, reverse-causality and stated-reasoning cautions are otherwise good.

5. **Minor: two compressed descriptions overstate what was measured.**  
   Locations: Sections 3.4 and 4.3; Appendix F.

   - “Judges mostly asked about things that were true” treats oracle outputs as established truth. Given the acknowledged oracle error, write: **“Most recorded oracle outcomes were YES.”**
   - The main text’s “one checkable fact” and “no bundling” omit the gate prompt’s explicit allowance for a compound rule with its conditions and for a list treated as one proposition. Those exceptions matter when interpreting a budget as an amount of verification.

   **Required change:** add a brief sentence that the operational rule permitted those constructions. Continue describing budgets as query opportunities, rather than counts of independently verified facts.

6. **Minor: simplify the main statistical exposition and make the principal heading more precise.**  
   Locations: Sections 3.4, 4.1 and 4.4.

   Most of the main text is comfortably readable. The practical-size paragraph in 4.1 is the clearest exception: several kinds of bounds, multiple numerical thresholds and a counterfactual gate qualification arrive together. The deployment-agreement details in 3.4 can also be shortened without losing the deviation.

   **Required replacement for the core of 4.1:**

   > “Offering one query raised pooled error by 0.77 percentage points relative to none and by 0.82 points relative to six. Both comparisons passed the registered test; a peak at two queries was not established. Under the implemented gate, the confidence bounds also excluded the prespecified 1.5-point peak. Neither conclusion establishes what would happen under a fully compliant gate.”

   Keep the intervals in Table 2 and the simultaneous-bound details in Appendix A.

   **Suggested replacement for the sensitivity explanation in 4.4:**

   > “In the prespecified worst-case sensitivity calculation, support for the peak disappears if rule-breaking queries could change verdicts in about 0.25% of sessions in each query condition. The audit flagged substantially more sessions. This leaves the result under a fully compliant gate unresolved; it does not show that violations caused the observed peak.”

   A more precise 4.1 heading is **“Offering one query raised pooled error under the implemented gate.”** The existing working title is broadly fair because it says “small,” “fragile” and “offering.” Section 4.2 is defensible when read with its protocol qualification; “Document access outperformed up to six queries” would be more precise. Section 4.3’s heading is supported by the descriptive decomposition.

STRONGEST COUNTERARGUMENT TO MY OWN VERDICT: The draft is already unusually candid. It distinguishes offering queries from receiving facts, reports the failed gate check prominently, disclaims practical significance under the implemented gate, identifies judge dependence, and repeatedly limits the exploratory interpretation. The primary statistical procedure is coherent, and the reported numbers and rendered tables show no inconsistency I found. A reasonable reviewer could approve it explicitly as a provisional draft and regard my requested changes as editorial qualifications. My reason for requiring revision first is that the oracle attribution, inferential scope and acquisition-time assumption affect what readers may believe the experiment established, even though none requires changing the frozen results.

ANSWERS:

1. The main contrasts are defensible as conditional protocol comparisons. The clearest overstatement is “the oracle was rarely the problem”; broader capability, knowledge-control and generalization language also needs qualification.
2. I found no substantive numerical, denominator or pooling contradiction. The apparent table misalignments are PDF-extraction artifacts. Clarify the gate-rule exceptions and the meaning of threshold “stability.”
3. The title is mostly fair; 4.1 should specify offering, pooling and implementation. A referee’s attack on a general claim that permissible verification harms oversight would land; it would not invalidate the narrower implemented-protocol result.
4. The intersection-union test followed by Holm, stratified world bootstrap and simultaneous practical-size bounds are coherent. Constant-error Llama scoring correctly preserves contrast p-values, but supplies no evidence about Llama’s actual budget response. The gate calculation is a conditional, fixed-uncertainty sensitivity analysis, not a gate-corrected outcome estimate or evidence that violations caused the effect. State that bootstrap coverage is approximate.
5. Add explicit shared-model measurement dependence, the fixed-panel inference target, restricted-access task scope, possible benchmark cues and acquisition-time stability assumption.
6. Mostly appropriately hedged. Revise the oracle-exoneration language, distinguish model coding from established mechanism, specify the interval method, add wording-group denominators and narrow the threshold-stability claim.
7. Most passages pass the readability rule. Simplify the practical-size paragraph and gate-sensitivity explanation using finding 6; leave the heavier procedures in the supplement.
8. Revise, not reject. Require the disclosures and wording changes above before public sharing. No new outcome analysis is necessary. Earlier review opinions were not consulted, and no files were edited.


