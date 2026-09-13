# Proposed study: 2-point precision across model tiers

2026-09-13. Proposal only; no paid calls authorized or launched by this document. Recommended cash cap: **$5,000**, including new paid preparation, reviewer calls, pilot, main measurement and retries. The target means approximately **+/-2 percentage points for each model-condition error rate at 95% confidence**. It does not mean 2% relative error, 80% power to detect a 2-point model difference, simultaneous coverage of every interval, or guaranteed nonoverlap.

**Roster: 13 recipient models, five families.** Eleven recipient endpoints are added to the Qwen and Llama recipients from Phases 3-5. DeepSeek previously served as an oracle adjudicator; this gives it a comparable recipient-judge result. Tiers are ordered hypotheses to test, not an assumed ranking of our results.

| Family | Proposed models, in nominal tier order | Purpose |
|---|---|---|
| OpenAI | GPT-5.6 Luna, Terra, Sol; GPT-6 Astra | Three same-generation product tiers plus a frontier reference |
| Anthropic | Claude Haiku 4.5, Sonnet 5, Opus 5, Fable 5.1 | Four product tiers including frontier; generations differ |
| DeepSeek | V4 Flash 0731, V4 Pro 0813 | Two-tier replication outside OpenAI and Anthropic |
| Qwen | Qwen3.8 Flash, Qwen3.8-2.4T-A95B | Within-Qwen comparison, retaining the prior recipient that was harmed |
| Meta | Llama 3.3 70B Instruct Turbo | Retain the prior recipient that benefited |

Exact API identifiers, prices and arithmetic are in [the proposal data](two-point-proposal.json). Current catalogs list these endpoints, but account access is not yet verified. Prices were freshly checked in [OpenAI pricing](https://developers.openai.com/api/docs/pricing), [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing), and [Together's catalog](https://docs.together.ai/docs/serverless/models). Qwen3.8 Flash adds only $15.45 to the baseline main-call scenario, making a within-Qwen comparison inexpensive. This is a product-tier study, including serving and reasoning configurations, not a parameter-scaling law. In particular, the single Llama endpoint does not answer 70B versus 405B; that needs a separately hosted, same-generation ladder.

**Budget allocation:**

| Work | Allocation |
|---|---:|
| Build and validate clean questions, debate packets and verification labels; explanation audit | $650 |
| Separate 120-question pilot across all 13 models | $400 |
| Main measurement | $3,250 |
| Additional token usage, retries and accounting reserve | $700 |
| **Total cap** | **$5,000** |

Use OpenAI and Claude Batch pricing for the main run; conservatively price Together calls at standard rates. At 4,000 input and 1,000 billed output tokens per call, the main measurement costs **$2,675.71**. The pilot at standard rates costs **$245.05**. Output token assumptions include reasoning and are forecasting inputs, not proposed truncation limits. If both token counts double, the main cost becomes **$5,351.43 before preparation**, so the cap is conditional on a pilot-based forecast, not a completion guarantee. Subscription quota is not assumed to fund formal verdicts.

The cap would leave **$1,529.81** against the previously reconstructed $6,529.81 remainder, before unreconciled historical reviewer/subscription expenses. Reconcile those expenses and available provider funds before committing the main budget. All new paid reviewer and provider calls belong inside this cap rather than outside the experiment ledger.

**Plan:**

1. Define six task strata: quantitative rules, temporal state, explicit causal rules, scope and exceptions, multi-step evidence/provenance, and source insufficiency. Generate distinct fictional scenarios from structured ground truth, with one primary question per scenario. Source-insufficiency items must have an unambiguously correct answer about what can be inferred. Validate source completeness and every answer alternative, including independent reviewer checks; balance answer length and position. Do not select formal questions because a recipient model gets them wrong. Claims will be conditional on this declared task mixture.
2. Use **120 pilot scenarios**, 20 per stratum, disjoint from the formal sample. Check account access, source validity, completion rates, supported output formats, provider-specific token counts, error rates and paired covariance. Establish appropriate reasoning settings and output limits before measurement; do not force all models into a 1,000-token ceiling. Record the configuration rather than treating unequal default compute as parameter-only capability. Finalize the parser, rationale format and analysis using the pilot. Validate Batch behavior as well as standard pilot requests.
3. Prepare **2,406 new formal scenarios**, 401 per stratum. This covers the worst-case normal planning requirement of 2,401 independent binary questions for a marginal +/-2-point interval, rather than relying on a 10% error rate. Each model receives three conditions: full-source answering, fixed debate without verification, and the identical debate with verification. Run each in both answer orders. That is **187,668 main judge calls**, plus **9,360 pilot calls**. The two orders remain one scenario cluster, not two independent observations.
4. Fix debate packets independently of the recipient models and balance donor origin. Verify claims against the structured ground truth and supplied source, retaining clear scope and evidence references. This measures recipient use of fixed verification evidence. It does not yet test every model generating its own adaptive debate or verification queries. Request a short decision rationale and evidence references consistently across arms; validate references and audit matched successes and failures. Full evidence-perturbation mechanism experiments would be a separate extension.
5. Report full-source accuracy, debate-only error, verification error, and the paired effect of verification for every model. Bootstrap independent scenarios within strata, keeping all models, conditions and orders paired. Prespecify eight adjacent-tier comparisons across the four multi-model families for baseline capability and eight for verification benefit, with Holm correction across all 16 tests. Confidence intervals for rates are marginal; verification-effect and tier-interaction intervals have their actual observed widths. This sample size does not guarantee +/-2-point interaction precision or a complete ranking.
6. Run provider queues concurrently within measured rate limits, using resumable storage and retrying only unresolved or failed work with conservative charge accounting. If approved, routine preparation, pilot and main measurement can proceed under the one cap once the pilot shows a conservative completion forecast fits. If access, benchmark validity or forecasted cost fails, revise the proposal before formal dispatch. Do not drop models after inspecting formal outcomes, silently weaken precision, or increase the cap.

The deliverable is a clean benchmark, comparable charts for every recipient, within-family tier comparisons, and a coded explanation sample. It can reveal whether protocol response improves with product capability and whether the Qwen/Llama contrast survives cleaner evidence. It cannot establish a universal protocol, a parameter-count law, or truthful internal reasoning from self-reports.
