# Codex consult: empty-verdict discovery and remedy (2026-08-27)

One-shot consult, gpt-5.6-sol via codex-cli 0.149.0, run by the successor orchestrator session 3cf5f3a5 after the r21 discovery. Brief and verdict verbatim.

## Brief

```
One-shot consult. Self-contained; same selvarath phase-3 v3 canary. URGENT
reframing discovery, made minutes before launching the owner-approved final
four-judge attempt; the launch is canceled pending this consult and the owner.
Blockers first.

DISCOVERY (from the three sealed result stores, never inspected at verdict-text
level until a wedged harness call forced it)
Empty-verdict rates on CANARY JUDGMENT cells (thinking exhausts the frozen
4,096-token reasoning-model effective cap on long transcript prompts and emits
nothing; ledger shows finish=length, full billing, empty content):

b0 condition, complete 96-slot coverage (r6-archive run):
- Qwen/Qwen3.5-9B: 91/96 empty (95%)
- Qwen/Qwen3.8-2.4T-A95B: 7/96 empty (7%)
- google/gemma-4-31B-it: 1/96 empty
- meta-llama/Llama-3.3-70B (non-thinking): 0/96
Corroborated in the two other runs (Qwen3.5-9B 80/82 and 52/91 overall;
Qwen3.8 8/118, 3/71; gemma-4 1-3 per run). Higher-budget conditions similar.

CONSEQUENCE: the frozen strict INVALID gate (per judge, b0, invalid <= 1 of 96)
fails Qwen3.5-9B by ~90 and Qwen3.8 by ~6 in EVERY configuration attempted this
week. Provider weather dominated attention, but no attempt could ever have
passed its gates. The r13 checker truncation was the same mechanism (gemma-4
checker role). Capability anchors (short prompts) fit within 4,096, which is
why weakness qualification and my screening probes (short and mid-size prompts)
never caught it. That screening gap is my design failure, and the three stores
contained the evidence for ~24 hours before I looked.

FROZEN SURFACES INVOLVED
- role limits: reasoning floor_max_tokens 4096 applied to all roles for
  reasoning models; base judge_verdict 512.
- per_model_extra_fields frozen {}; base request fields frozen (no
  chat_template_kwargs / enable_thinking route exists in the frozen contract,
  and Together renders server-side with its defaults).
- the INVALID gate itself: loosening it to pass a failing judge is forbidden.

REMEDY SPACE AS I SEE IT (all owner-level)
1. Raise the reasoning-model completion budget for verdict/judgment roles
   (e.g., 8192 or 16384). Evidence needed: judgment-SHAPED screening probes at
   candidate budgets to measure empty rates before committing (my amendment-4
   screening will be extended to judgment-shaped calls as a frozen admission
   gate regardless). Cost: verdict reservations scale ~2-4x; uncertain-ceiling
   math changes; Qwen3.5-9B at 95% overrun at 4k may want far more than 8k.
2. Re-roster judgment duty to judges that fit the budget: Llama (0/96) +
   gemma-4 (1/96) are viable today; both Qwens are not, at 4,096. N=2 is thin.
3. Both: raise the budget AND re-screen all candidates judgment-shaped,
   readmitting whichever Qwens pass at the new budget.
4. Explicitly NOT on the table: loosening the INVALID gate; regenerating
   invalid verdicts.

QUESTIONS
1. Rank the remedies. Is there an option I am missing (e.g., a protocol-shape
   change like transcript truncation is presumably science-off-limits)?
2. Specify the judgment-shaped screening protocol you would freeze: probe
   construction (real preseeded transcripts? synthetic at matched token
   length?), sample size per judge per budget, empty-rate acceptance threshold
   consistent with the invalid <= 1/96 gate (e.g., requires ~<=0.3% true rate;
   binomial), and spend authorization shape.
3. Does 4,096 vs a raised budget change the SCIENCE (verdict quality
   conditioned on completing) or only completion rates, for reporting purposes?
   What must the canary/main reports say about the budget choice?
4. The owner approved a now-moot final attempt this morning. Draft the choice
   set I should put to him, with honest framing of my screening and inspection
   failures.
5. Main-run implications: with verdict budgets raised, Qwen3.8 serverless
   instability (yesterday's consult) PLUS bigger reservations compound; does
   that push the main run further toward dedicated capacity or a non-thinking
   roster?
6. Spend state: $30.53 accounted of $60; a screening pass costs perhaps
   $0.50-2; a rebuilt canary ~$6-12 depending on budgets. Sanity-check the
   envelope for finishing the canary under the remaining ~$29.5.
```

## Verdict

## Blockers

1. Keep the launch canceled. The frozen four-judge configuration is structurally incapable of passing, not merely vulnerable to provider weather.
2. The prior approval is moot, not consumed. Any budget, thinking-mode, roster, or aggregation change needs a fresh manifest identity and owner authorization.
3. Llama plus Gemma is not presently a clean fallback. Llama is clean; Gemma's 1 to 3 invalids per run makes it marginal against `<=1/96`. If the true rate were 1%, the gate would still fail about 25% of runs.
4. A $0.50 to $2 screen cannot establish a true invalid rate near 0.3%. It can only detect gross failure modes.
5. Do not pool old judgments, regenerate invalid cells, truncate transcripts, or continue capped generations. The three stores remain diagnostic evidence only.

## 1. Remedy ranking

| Rank | Remedy | Assessment |
|---|---|---|
| 1 | Option 3, expanded to screen, select, and rebuild | Best choice. Screen complete model-role configurations, then admit only those that pass. Raising a cap does not guarantee readmission. |
| 2 | Replace failed Qwens with qualified non-thinking judges while preserving roster size | Better than silently collapsing to two judges. New candidates need the same capability anchors and judgment-shaped screen. |
| 3 | Llama plus Gemma only | Fastest fallback, but Gemma is marginal and N=2 may change power, diversity, tie handling, or the aggregate estimand. |
| 4 | Raise all reasoning budgets without prior screening | Do not do this. It increases liability while Qwen3.5 may still exhaust 8,192 or 16,384. |

A missing configuration is reasoning-off Qwen. Together currently documents thinking controls for Qwen3.5 through `chat_template_kwargs`. Treat reasoning-off as a distinct judge configuration requiring fresh qualification, not as the same judge with a cheaper transport setting. Exact Qwen3.8 support must be verified rather than inferred. [Together reasoning documentation](https://docs.together.ai/docs/inference/chat/reasoning)

Transcript truncation is science-off-limits because it changes the evidence available to the judge. Prompt compression, verdict-first prompting, or continuation after truncation also create new protocols and should not rescue this canary.

## 2. Screening protocol to freeze

Use the configuration, not merely the model, as the admission unit:

`model Ã— endpoint/serving mode Ã— role Ã— thinking mode Ã— max_tokens`

Screen judgment and checker roles separately. The r13 checker failure proves that a judgment-role pass cannot qualify the checker role.

**Probe construction**

- Use real protocol-generated preseeded transcripts from an independent screening seed bank.
- Match the exact system prompt, judgment template, output schema, parser, decoding fields, condition mix, and rendered-token distribution.
- Do not use final canary seeds.
- Freeze transcript and rendered-request hashes before any calls.
- Use synthetic length-matched prompts only as supplemental stress tests. They cannot replace real transcripts because semantic difficulty affects reasoning length.
- Let automation inspect only finish reason, token use, empty status, and final-parser validity. Keep verdict direction and text blinded during configuration selection.

**Budget routing**

For each candidate budget:

1. Run 24 independent upper-tail probes representing the longest real prompts and checker shapes.
2. Stop and reject that budget on the first canary-invalid output. Do not retry semantic invalids.
3. Advance only with 0 of 24 invalid and no completion using more than 80% of the proposed cap.
4. At the selected budget, run 96 new distribution-matched confirmation probes.
5. Admit only with 0 of 96 invalid. Count everything the final parser would count as INVALID, not merely empty content.
6. A transport interruption does not become a pass. A configuration is not admitted until it has one complete screen batch within the authorized cap.

I would test 8,192 before 16,384. If 8,192 fails, move to 16,384 only if that branch was explicitly costed and authorized. For Qwen3.8 under the present aggregate cap, exclusion after an 8,192 failure is likely preferable to automatically escalating.

**Statistical interpretation**

Your 0.3% intuition is sound as a reliability target: at true `p=0.003`, the probability of satisfying `X<=1` over 96 calls is about 96.6%.

But:

- Observing 0 invalids in 96 gives a one-sided 95% upper bound of about 3.07%, not 0.3%.
- Establishing a 95% upper bound below 0.3% requires 0 invalids in approximately 998 independent probes.
- A 0-of-96 rule nevertheless rejects a true 7% configuration with about 99.91% probability.

Therefore label 0-of-96 honestly as an operational blocker screen. It catches the observed catastrophic mechanisms but does not certify `p<=0.3%`. The 998-call standard is statistically defensible but economically disproportionate here. Length-driven heterogeneity also weakens a simple IID binomial interpretation, which is why the separate upper-tail stress set matters.

The screen authorization should bind candidate configurations, call maxima, conditional escalation, per-stage dollar ceilings, and the aggregate $60 cap. A compact manifest is sufficient.

## 3. Science and reporting

The cap changes more than completion rate.

At 4,096, completion is strongly selected by prompt difficulty and required reasoning length. Qwen3.5's completed 5 of 96 are not representative of its assigned cells. Comparing their verdict quality with another judge's 96 completed verdicts would condition on a post-treatment event and produce severe selection bias.

Raising the cap:

- adds judgments for previously censored cells;
- may allow longer reasoning and different verdicts;
- changes the configured measurement instrument;
- changes cost and operational exposure.

Under deterministic decoding, outputs that already reached EOS below 4,096 might remain identical when the cap is raised. That narrow fact would not rescue roster-level comparability because the set of observed judgments changes radically.

The canary and main reports should state:

- exact model, endpoint, thinking mode, and cap for every role;
- every screened configuration, including rejected ones;
- invalid and `finish=length` counts plus completion-token distributions;
- that budget selection used validity telemetry only, not verdict direction;
- that earlier 4,096 runs are diagnostic failures and are not pooled;
- that verdict-quality summaries conditioned on 4,096 completion are descriptive only;
- that conclusions apply to the admitted configured judges, not the model families in general.

If any actual verdict directions were exposed during discovery, disclose that separately and keep those transcripts out of confirmatory screening.

## 4. Owner-ready choice set

> I canceled the approved final launch because the current four-judge manifest cannot pass its frozen b0 invalid gate. In complete 96-cell coverage, Qwen3.5-9B produced 91 empty judgments and Qwen3.8 produced 7, both from exhausting the 4,096-token completion cap. Gemma is marginal at 1 to 3 invalids per run; Llama is the only clean current configuration. Provider instability was real but secondary.
>
> This was my design and inspection failure. My qualification probes were short or medium-sized rather than exact long judgment-shaped calls, and I did not surface finish-reason and empty-content counts from the sealed stores for about 24 hours. The canceled launch prevented further spend, but none of this week's attempted configurations could have passed.
>
> Please choose:
>
> 1. Recommended: authorize a bounded judgment-shaped screen of predeclared model-role configurations at candidate budgets and modes, then rebuild the full canary using only passing configurations. Replace failed models where necessary to preserve roster size. Do not pool old outputs.
> 2. Authorize a smaller Llama/Gemma canary, accepting reduced judge diversity and any required aggregation or power amendment. Gemma must still pass the screen.
> 3. Preserve the original model families by screening 8,192 and potentially 16,384 reasoning caps, plus a separately qualified reasoning-off Qwen configuration. This has the highest cost and operational risk.
> 4. Stop Phase 3 without another canary.
>
> The prior approval does not carry over. Any successor will have a fresh manifest identity, exact screen and canary dollar ceilings, and the same $60 aggregate cap including the $30.53 already accounted.

## 5. Main-run implications

Choose the scientific roster first, then the transport.

If Qwen3.8 is scientifically indispensable and passes only with a larger cap, its long outputs plus yesterday's serverless instability strengthen the case for provisioned or dedicated capacity. Together says serverless limits vary with live model capacity and recent successful traffic, while dedicated inference provides reserved hardware and more predictable throughput. Dedicated is billed per GPU-minute and does not itself solve reasoning truncation or guarantee equivalent judgments. [Serverless rate limits](https://docs.together.ai/docs/serverless/rate-limits), [dedicated inference overview](https://docs.together.ai/docs/dedicated-endpoints/overview)

A serverless-qualified configuration cannot automatically qualify a dedicated endpoint. Freeze and screen the exact serving configuration because endpoint engine, quantization, or template behavior may differ.

If equally qualified non-thinking replacements exist, that roster is operationally cleaner for the main run. It avoids large reasoning reservations and reduces exposure to token-cap and token-throughput failures. Do not make that substitution solely for convenience if it materially weakens judge diversity or validity.

## 6. Spend envelope

Arithmetic using your estimates is comfortable:

- Remaining cap: `$60.00 - $30.53 = $29.47`.
- At `$0.50 + $6`, project total becomes `$37.03`, leaving `$22.97`.
- At `$2 + $12`, project total becomes `$44.53`, leaving `$15.47`.

The screening estimate is optimistic if it includes Qwen3.8 confirmation. Its current published prices are $2.50/M input tokens and $6.25/M output tokens. For 96 calls, output-only maximum liability is:

- 8,192 tokens: about `$4.92`
- 16,384 tokens: about `$9.83`

An average 10,000-token prompt adds another `$2.40` across 96 calls. Thus $0.50 to $2 is plausible for a roughly 24-call 8,192-token triage, not for a 96-call admission screen. A 16,384-token Qwen3.8 b0 block alone could consume most of a nominal $12 entire-canary estimate. [Together pricing](https://www.together.ai/pricing)

Before authorization, compute:

`maximum liability = sum(prompt tokens Ã— uncached input price + max_tokens Ã— output price)`

Include every screen, judgment, checker, and permitted transport restart. Reconcile the $30.53 against any unsettled or unknown-charge exposure first.

Bottom line: finishing under $60 is credible with an 8,192 screen-and-select path or a qualified replacement roster. A full 16,384 Qwen3.8 screen plus rebuilt canary is not safely covered by the rough $0.50 to $2 and $6 to $12 estimates without an exact liability table.

Prior project memory was used only to cross-check the historical 4,096 floor, Qwen3.8 transport incident, and prior canary-only authority. Your current report controls.


