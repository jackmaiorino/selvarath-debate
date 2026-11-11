ACCURATE WITH CORRECTIONS

1. **Must fix: absence of a detected difference is presented as equivalence.**  
   `paper/sections/results.tex:7–33`: “six did not help”, “offering six brought it back”, “back at the no-query level”, and “Six queries were no better than none”.

   The registered comparison did not establish equality or a return to baseline. Its estimate is −0.05 percentage points, with a 95% interval of −0.37 to +0.30. The sentence also gives a positive improvement magnitude alongside an interval using the opposite sign convention. Sources: `amendment1_analysis.json`, secondary `pooled_k6_minus_k0`; preregistration §7.2.

   Replace the heading with:
   > One query raised error slightly; no improvement was detected at six

   Replace the final paragraph with:
   > We detected no average difference between six queries and none: error with six queries was 0.05 percentage points lower, with a 95% interval ranging from 0.37 points lower to 0.30 points higher.

   Replace the caption/body’s return-to-baseline wording with:
   > offering six produced an error rate close to the observed no-query rate

2. **Must fix: specify what the gate sensitivity actually bounds and the denominator of its tipping point.**  
   `results.tex:121–126`, `appendix.tex:191–205`, and `generated/tab_tipping.tex`: “assume that every affected session counts against the finding” and “would lose its statistical support if 0.25% of sessions were affected”.

   The calculation shifts contrasts while retaining their standard errors and bootstrap reference distributions. It does not change individual session scores and rerun the analysis. Furthermore, 0.25% is a common panel-average exposure **in each query condition**, not a fraction of all experiment sessions. Sources: amendment 3 §5; `finalphase/main_analysis_amendment3.py`; `amendment3_analysis.json.tipping`.

   Replace the main-text description with:
   > We applied a conservative bound that lets rule-breaking admissions shift each comparison against the finding. Under this bound, support for the one-query peak disappears at an affected share of about 0.25% in each query condition, averaged over judges. This is far below the audited rates, but does not estimate how many verdicts the gate actually changed.

   Add in Appendix D:
   > The calculation shifts each contrast while keeping its standard error and bootstrap reference distribution unchanged; it does not reanalyse changed session scores. The tipping points use the same panel-average affected share in each query condition.

   Replace the tipping-table column heading with:
   > Common affected share in each query condition (%)

3. **Must fix: the document-versus-queries result needs its own gate caveat.**  
   `results.tex:53–55`: “Up to six verified facts were no substitute for the document.”

   The experiment offered query slots, some consumed by rejections, under a permissive gate and an imperfect oracle. This wording turns that intervention into six valid verified facts. The document-versus-six comparison also loses support under the prescribed gate sensitivity, at 1.40% common exposure. That qualification appears in the supplement but is missing beside this main-text conclusion. Sources: amendment 3 §§3–5; `amendment3_analysis.json`; reviewed narrative §§1 and 13.

   Replace with:
   > Under this protocol, offering up to six queries produced higher error than providing the document. Like the one-query finding, this comparison is not robust to the gate sensitivity analysis.

4. **Must fix: oracle qualification is overstated as accuracy against fully adjudicated truth.**  
   `paper/sections/methods.tex:106–107`: “On 300 development queries with adjudicated answers it was right on 279 (93%).”

   The reference came from model readers, with a third model replacing the planned human resolver. Eleven reference labels remained unresolved and counted as wrong. Source: `difficulty-next-steps.md:792–800,846–848,882–885`.

   Replace with:
   > On 300 development queries, it agreed with reference answers established by independent model readers in 279 cases (93%), counting the 11 unresolved reference cases as wrong.

5. **Must fix: “no length limit” conceals unequal output ceilings.**  
   `methods.tex:56`: “no length limit”; `limits.tex:15`: “Model, role and effort settings are tied together”.

   There was no word cap, but Sonnet had a 16,000-token output ceiling and Fable a 32,000-token ceiling. The preregistration explicitly includes ceiling among the pairing’s confounds. Source: main preregistration §§2 and 4.

   Replace “no length limit” with:
   > no word cap

   Replace the Appendix G debater settings with:
   > medium effort; 16,000-token output ceiling  
   > high effort; 32,000-token output ceiling

   Replace the limitations sentence with:
   > Model, role, effort settings and output ceilings are tied together in that choice, so the results do not rank models or separate the effect of role from the effect of capability.

6. **Must fix: the explanation figure restores a mechanism label already corrected in the reviewed addendum.**  
   `paper/scripts/make_figures.py:252`: “Oracle answer was not the judge’s reason”.

   M3 also includes correct-favouring answers that judges overrode or reinterpreted, sometimes explicitly relying on them. The reviewed note specifically identifies this overstatement. Source: `explanation_note_20261030.md`, §2’s category definition and classification sensitivity.

   Replace the label with:
   > Answer unused, peripheral, overridden or reinterpreted

7. **Must fix: disclose the explanation figure’s mixture of estimates and census counts.**  
   `appendix.tex:231–232`: “Shares … weight each group by its size”; figure labels “Verdict could not be parsed” and “Unclear”.

   Using six unparsed cases is correct. However, six is a census count while the mechanism shares are estimates, so the displayed shares total approximately 98.13%, not 100%. A seventh unparsed case belongs to the 17 no-answer cases. The missing interval for “Unclear” reflects unreliable estimation, not certainty. Sources: `explanations_counts.json`, `explanations_summary.json`, reviewed explanation note §2.

   Replace the unparsed label with:
   > Unparsed verdict with an oracle result

   Replace the appendix explanation with:
   > Sampled groups are weighted by their population sizes. The no-answer row counts 17 cases, and the unparsed-with-result row counts six; one further unparsed verdict is included in the no-answer row. Other shares are sample estimates, so the displayed shares need not sum to 100%. Intervals describe sampling uncertainty conditional on the original labels, not labelling uncertainty. The sparse unclear category has no reliably estimated interval.

8. **Must fix: the gate categories are described too narrowly.**  
   `results.tex:116–119`: “In most of those sessions the flags concern the form of the query”; `make_figures.py:236`: “flagged for naming or asserting a candidate answer”.

   The numerical majority is correct, but the complementary restrictions include factuality and inference, not merely formatting. The narrower `leak_exposed` category also includes unclassified or unreadable flags, and condition 2 covers references to debaters, positions, the debate and the oracle. Sources: amendment 3 §4; `gate_audit.py`; `gate_audit.json`.

   Replace the main-text passage with:
   > Most flagged sessions did not receive an answer-revealing flag. The audit’s broader answer-revealing category includes prohibited references to the debate or candidate answers, assertions of an answer’s conclusion, and flags whose type could not be determined. It covered 1.8%, 3.2% and 5.0% of sessions.

   Replace the figure legend with:
   > answer-revealing or unclassified flag

9. **Must fix: the 2,653 audit items are not all queries from the sampled main sessions.**  
   `appendix.tex:172–174`: “label every query in them … 2,653 items”.

   The total comprises 2,036 distinct main-run requests reaching the classifier and 617 pilot calibration items. It excludes mechanically rejected queries and is not a count of query occurrences. Sources: amendment 3 §4; `gate_audit.json.labels_by_source`.

   Replace with:
   > After data collection closed and before the analysis ran, we sampled 40 sessions for each judge and query budget (840 sessions). Claude Opus 5.5 labelled every distinct claim sent to the classifier in those sessions, plus pilot claims for calibration, blind to verdicts: 2,036 main items and 617 pilot items, all answered.

10. **Must fix: identify the confirmation rates’ different weighting.**  
    `methods.tex:60–63`: “erred 5.0% of the time … against 22.9%”; corresponding passage in Appendix B.

    These are world-weighted rates. The ordinary session frequencies are 13/240 = 5.42% and 56/240 = 23.33%. Main uses equal question weighting. Source: `difficulty-next-steps.md:629–643`.

    Replace with:
    > In a development run on 40 held-out questions, the three judges’ error rates, weighting worlds equally, were 5.0% without queries when Fable debated itself and 22.9% with Sonnet on the honest side.

    In Appendix B, replace “The 22.9% and 5.0%” with:
    > The world-weighted rates of 22.9% and 5.0%

    Keep the warning against comparing these rates directly with main.

11. **Must fix: distinguish confidence bounds from absolute limits.**  
    `results.tex:28–30`: “the differences at one query are at most 1.19 and 1.24”.

    The numbers are correct simultaneous 95% upper confidence bounds, not deterministic maxima. Sources: `amendment1_analysis.json`, primary simultaneous bounds; preregistration §7.2.

    Replace with:
    > The simultaneous 95% upper bounds are 1.19 and 1.24 percentage points, both below the prespecified 1.5-point threshold. At that confidence level, the data rule out a peak of the prespecified practical size.

12. **Must fix: the robustness account omits an undefined registered check.**  
    `appendix.tex:66–74`: “lists the registered sensitivity analyses”; `results.tex:181–183`: “The two checks the result does not pass”.

    The registered unparsed-as-missing sensitivity is undefined under eight-judge scoring because Llama has no parsed verdicts. Its seven-judge counterpart was not computed. The supplement also omits the missing-cell codings, which made no difference because no cells were missing. Sources: `main_analysis.json.sensitivity`; reviewed narrative §8.

    Add to Appendix A:
    > The missing-cell codings give the same results because no cells were missing. Treating unparsed verdicts as missing makes the registered eight-judge analysis undefined, because Llama has no parsed verdicts; a seven-judge version of that check was not computed. The sensitivity rows above use the registered eight-judge scoring, with the stated exclusions; Llama’s constant error leaves their p-values unchanged.

    Replace the main-text lead-in with:
    > Among the checks that could be evaluated, two do not retain support for the finding:

13. **Must fix: the analysis cross-checks’ independence is overstated.**  
    `appendix.tex:77–78`: “two separate sessions re-implemented the analysis from the preregistration text alone, without the code”.

    The second session received the first check’s core implementation and independently implemented the revised missing-cell and compliance rules. The total of 235 compared quantities is correct. Source: main preregistration’s independent-check record.

    Replace with:
    > One independent session re-implemented the core analysis from the preregistration. A second independently implemented the revised missing-cell and compliance rules using the first check’s core code. Across the two checks, 235 compared quantities agreed with the analysis module.

14. **Must fix: complete the gate reporting promised by amendment 3.**  
    `appendix.tex:162–180` summarizes the equivalence check, but amendment 3 §8 explicitly requires the full criteria/results table, deployment settings, protected-admission results and reset counts, dates and costs.

    Reproduce the existing equivalence-check table from `report_tables_20261029.md`, §G, and add:
    > The gate used DeepSeek-V4-Flash-0731 on one Together-certified replica with two NVIDIA B200 GPUs, NVFP4 and tensor parallelism 2, model revision `rv_CdgV5GFj8H9uwcbVDGTs7`. Requests retained the 2,000-token output limit and set no sampling parameter. On 2026-10-28 at 01:28 UTC, 25,987 access-refusal records were archived and removed; they incurred no API cost. Of 488 pilot rejections confirmed by hand, the deployment admitted 154 on the first pass and 60 on the repeat. It admitted all seven pilot false admissions and all 93 other sampled admissions, four of five false rejections, and 14 of 24 borderline rejections.

    These details belong entirely in the supplement.

15. **Must fix: low baseline error limits improvement, not deterioration.**  
    `results.tex:77–78`: “there was little room for queries to help or hurt most judges”.

    Low error leaves little room to improve but substantial room to worsen. Source: the rates in `main_analysis.json.curves` and `tab_by_judge.tex`.

    Replace with:
    > With so few errors, there was little room for queries to improve most judges’ accuracy.

16. **Suggested: make the restricted meaning of “peak” explicit in the main text.**  
    `results.tex:24`: “the registered test supports a peak at one query”.

    This is faithful to the definition in Methods and Appendix A, but does not establish that one query is worse than two. Source: preregistration §7.2.

    Replace with:
    > The registered test supports higher error at one query than at zero and six (corrected p = 0.0002). It does not directly compare one query with two.

    “Corrected p” is an acceptable plain-language label. In Appendix A, narrow its definition to “For this primary test, ‘corrected p’ means…”, since the secondary tests use a separate Holm correction.

17. **Suggested: distinguish an unbounded interval from an unavailable interval.**  
    `results.tex:72`: “too few errors to estimate an upper limit”; `tab_by_judge.tex` uses “n/a” for both infinite endpoints and absent intervals.

    Opus at budget zero has a finite lower endpoint and an infinite upper endpoint. The starred plotting treatment is transparent, but the wording can be exact. Source: `main_analysis.json.curves.opus.debate_k0.ci95`.

    Replace the caption note with:
    > The registered interval has an unbounded upper endpoint; only its lower portion is drawn.

    In the table, use “unbounded” for infinite endpoints and retain “n/a” for genuinely unavailable intervals.

18. **Suggested: correct several smaller descriptions whose numbers are otherwise sound.**

    - `methods.tex:56–57`, “balanced across questions”: assignment used hash parity, yielding 520/1,068 honest-first debates. Source: `main_analysis.json.order_balance`. Replace with:
      > A fixed hash assigned which side spoke first; the honest side opened 520 of the 1,068 analysed debates.

    - `methods.tex:126`, “missing verdict counts as an error”: absent session records are distinct from empty replies. Source: preregistration §7.1. Replace with:
      > an unreadable or empty verdict reply counts as an error

    - `results.tex:94`, “109,090 oracle answers”: this counts recorded outcomes, including two failures and shared-request occurrences. Source: `main_analysis.json.query_use`. Replace with:
      > Of 109,090 recorded oracle outcomes, 86.4% were YES, 11.2% NOT ADDRESSED and 2.4% NO; two were failed oracle calls.

    - `appendix.tex:215`, “the pairs with no answer are even”: they contain 17 harmful and 19 helpful flips. Source: `explanations_counts.json`. Replace with:
      > the pairs with no answer are nearly balanced, with 17 harmful and 19 helpful flips

The numerical checks otherwise pass: all **91 macros and ten generated tables** reproduce from the supplied JSON. The seven-judge transformation `(8p − 1)/7` is valid for estimates and interval endpoints because Llama contributes constant error in every draw. The 115-flip excess exactly matches the unrounded 0.7691279-point contrast. Rounded cost figures agree with the reviewed report. The retention rule, core gate state machine, and confirmation run’s amended-retry disclosure are accurately described. No misleading finite-axis clipping was found in the five figures.

Could not independently verify from the available files:

- The new benchmark summaries against the underlying main-split world documents.
- Provider billing against account statements, including the unresolved OpenAI discrepancy.
- Individual explanation classifications and quotations against the original case files.
- Acquisition chronology and deployment records beyond the supplied protocol and reviewed reports.


