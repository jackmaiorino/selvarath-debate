# Model hierarchy and billing options

Read-only documentation review, 2026-09-13. No inference, deployment, credentials, or billing settings were changed. Prices are published USD rates, not an account-specific quote. Actual account access, quotas, negotiated rates, and credit balances were not checked.

## What hierarchy would mean

Measure two different outcomes on the same source-determined questions: baseline accuracy with no extra evidence, and the paired change in error after evidence. A model can have higher baseline capability and still use a particular evidence protocol worse. A single total ordering would conceal that distinction. Use matched questions and treatment packets, balanced answer positions, frozen parser and scoring, and a declared inference configuration. Permit ties and uncertainty rather than forcing a rank.

For a parameter-size study, compare the same model generation, dense architecture class, instruction tuning, quantization and hosting configuration. Llama 3.1 8B/70B/405B is an interpretable candidate ladder. Meta released those three sizes together, but the former Together serverless versions were retired: 405B on February 6, 70B on February 25, and 8B on March 6, 2026. A complete currently callable and priced replacement host was not established in this review. Do not silently substitute Llama 3.3 70B into a 3.1 scaling curve. [Meta release](https://ai.meta.com/blog/meta-llama-3-1/), [Together retirements](https://docs.together.ai/docs/deprecations).

A lower-cost dense ladder is Qwen3-8B/14B/32B, with a common declared thinking mode. These models were released as dense models; Qwen3-30B-A3B and 235B-A22B are mixture-of-experts models. The A-number describes active parameters. Mixing total MoE parameters with dense parameters does not measure one common size axis. Current per-token hosting of all three dense endpoints was not established, so they are design candidates rather than priced launch options. [Qwen release and model list](https://qwenlm.github.io/blog/qwen3/).

There is no rule that 405B must beat 70B on every task. Training generation, post-training, inference budget, quantization, prompt handling, and the benchmark's answer key can all change the outcome. Meta itself describes the later Llama 3.3 70B as offering performance similar to Llama 3.1 405B. A reversed result across those generations would not isolate a harmful effect of parameter count. These are possible confounds, not explanations established by our experiment. [Meta on Llama 3.3](https://ai.meta.com/blog/future-of-ai-built-with-llama/).

## Immediately priceable product-tier roster

This eight-endpoint roster offers three tiers within two provider families plus two frontier anchors. It tests marketed capability tiers, not parameter scaling. The Claude tiers span generations. Published availability does not establish that our account can call every endpoint.

| Family and role | Exact API model ID | Input / 1M tokens | Output / 1M tokens | Illustrative 1,000-call cost |
|---|---|---:|---:|---:|
| OpenAI small tier | `gpt-5.6-luna` | $0.20 | $1.20 | $2.00 |
| OpenAI middle tier | `gpt-5.6-terra` | $2.00 | $12.00 | $20.00 |
| OpenAI high tier | `gpt-5.6-sol` | $4.00 | $20.00 | $36.00 |
| OpenAI frontier anchor | `gpt-6-astra` | $10.00 | $50.00 | $90.00 |
| Claude small tier | `claude-haiku-4-5-20251001` | $1.00 | $5.00 | $9.00 |
| Claude middle tier | `claude-sonnet-5` | $2.00 | $10.00 | $18.00 |
| Claude high tier | `claude-opus-5` | $5.00 | $25.00 | $45.00 |
| Claude frontier anchor | `claude-fable-5-1` | $10.00 | $50.00 | $90.00 |

OpenAI rows use Standard, short-context, uncached pricing. Batch rates are half these input/output rates. Long-context and fast-mode rates differ. Sol's current promotion runs at least through November 21, 2026. [Official OpenAI pricing](https://developers.openai.com/api/docs/pricing).

Claude model IDs and current lineup come from the [official model overview](https://platform.claude.com/docs/en/models/overview). Prices and 50% Batch reductions come from [official pricing](https://platform.claude.com/docs/en/about-claude/pricing). Sonnet 5's $2/$10 launch price is now its standard price; the previously announced September increase was cancelled. Newer Claude tokenization can use more tokens for the same text, so calibrate costs per endpoint.

The last column is arithmetic, not a measured forecast: 4,000 uncached input tokens plus 1,000 billed output tokens per call, no tools or extra attempts. Thus 1,000 calls cost `4 * input_rate + output_rate`. Across all eight endpoints that is $310 for 8,000 calls, or $155 at the listed Batch rates, assuming eligibility and those same token counts. Reasoning tokens and longer responses can materially increase the bill. Use real token measurements for the project forecast.

For recent Claude releases, the dateless canonical ID is a fixed model snapshot, although serving infrastructure can still change. The older Haiku alias should be replaced by its dated ID as shown. [Claude model versioning](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions).

## Continuity endpoints from our existing provider

Together's fetched catalog currently lists the following standard uncached rates. These are useful replication anchors, not a controlled size ladder:

| API ID | Input / 1M | Output / 1M |
|---|---:|---:|
| `meta-llama/Llama-3.3-70B-Instruct-Turbo` | $1.04 | $1.04 |
| `Qwen/Qwen3.8-2.4T-A95B` | $2.00 | $6.00 |
| `deepseek-ai/DeepSeek-V4-Flash-0731` | $0.14 | $0.28 |
| `deepseek-ai/DeepSeek-V4-Pro-0813` | $1.32 | $3.96 |
| `openai/gpt-oss-120b` | $0.15 | $0.60 |

[Together model catalog](https://docs.together.ai/docs/serverless/models). Llama's current $1.04 rate is higher than the $0.88 shown in older documentation and previous project estimates. The Together pricing page and catalog disagree about some Qwen cache prices and Qwen3.7-Max prices, so those disputed rates were excluded. [Together pricing](https://www.together.ai/pricing).

## Subscription billing

Subscriptions can support some native automation. They are not a blanket allowance for arbitrary raw API requests, and they are not guaranteed unlimited benchmark capacity.

- Codex's official documentation includes SDK and `codex exec` workflows with plan usage limits. API-key usage is separately billed at API rates. A native Codex pilot may have no incremental cash charge while included quota remains; it still consumes shared subscription capacity. [OpenAI plan pricing](https://learn.chatgpt.com/docs/pricing), [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk).
- Claude Code documents `claude -p` for programmatic work and subscription authentication for CI/scripts. This supports personal native-CLI automation rather than a blanket prohibition. Actual plan allowance and availability must be checked. [Headless Claude Code](https://code.claude.com/docs/en/headless), [subscription authentication](https://code.claude.com/docs/en/authentication).
- Claude API calls use separately billed Console credits. Claude's terms distinguish ordinary native subscription use from routing other users' requests through subscription credentials. [API billing](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage), [authentication and permitted use](https://code.claude.com/docs/en/legal-and-compliance).

For formal comparisons, API endpoints are easier to match and account for. If native subscription CLIs are used, treat the CLI agent configuration as the experimental system: record its version, model, system instructions, tool availability, memory and repository context. Mixing an agent wrapper with direct API judges can otherwise introduce the very model confounds we want to remove. Subscription-native access could be economical for drafting and a small diagnostic pilot, subject to actual quota. No credential extraction, rerouting, or rate-limit workaround is needed or proposed.

Search cache note: several search snippets contained obsolete prices or an old Claude Agent SDK credit announcement that was absent from the freshly fetched official pages. Conclusions above use fetched pages. A specific monthly Agent SDK credit amount or entitlement was not established.
