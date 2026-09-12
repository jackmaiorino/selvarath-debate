# Phase 4 preparation record

**Prepared offline on 2026-09-12. No paid calls, execution approval or monitor.**

Subsequent update: Jack approved Phase 4A under the proposed $250 cap. The preparation record below describes the original offline snapshot; current launch validation and operations are recorded in the [execution runbook](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/docs/phase4a-operations.md). The staged scientific protocol and prepared input bytes are unchanged.

The [one-page protocol](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/docs/phase4-protocol.md) defines the scientific question. The [protocol JSON](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/rejudge/phase4_protocol_v1.json) specifies selection, contrasts, decision rules, decoding, recovery and conditional follow-ups. This is a proposed design for review; analysis rules become fixed before measurement.

**Prepared 4A panel.** Each row below receives the same 656 matched units: 82 questions, two debater sources, two transcripts and both mirrored sides. All verdicts are fresh. Only saved final verdicts are removed from the history inputs.

| Recipient | Empty history | Qwen history | Llama history |
|---|---:|---:|---:|
| Qwen/Qwen3.8-2.4T-A95B | 656 | 656 | 656 |
| meta-llama/Llama-3.3-70B-Instruct-Turbo | 656 | 656 | 656 |

Total: **3,936 calls and 1,968 distinct message packets**. Two incomplete source mirror pairs are excluded before outcome-independent selection. Every question/debater stratum remains represented. Gold answers are stored separately from message packets, and the future executor must send only the packet's messages, never its metadata.

Private inputs are at `E:\selvarath-archive\phase4-preparation-2026-09-12`: `packets.jsonl`, `units_private.jsonl`, `calls.jsonl` and `manifest.json`. The [public preparation manifest](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/reports/phase4-preparation-2026-09-12/prepared-panel.json) records their hashes, source hashes, exact preparation-script and protocol hashes, runtime and seeds. Its Git commit identifies the base checkout; the new preparation files are not yet committed. The completed Phase 3 archive remains unchanged.

**Independent review and verification.** A separate reconstruction matched all 656 selected units and every packet message hash. Review verified the contrast arithmetic and checked for final-verdict/gold leakage. The design now requires enough edited histories to make the proposed repair effect possible, explicit handling of invalid outputs, and a positive supported repair benefit before the planned transfer study. The offline builder is rerun into a separate directory and all four output files compared byte for byte; the verification receipt is [validation.json](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/reports/phase4-preparation-2026-09-12/validation.json).

**Cost and timing.** The [feasibility inventory](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/reports/phase4-preparation-2026-09-12/feasibility.json) uses historical measured usage where available and recipient-specific token estimates for crossed histories. It forecasts **$49.87 for completed verdicts plus $6.62 for uncertain deliveries**, approximately **$57**, excluding up to $5 preflight. Together's posted input/output rates are $2/$6 per million tokens for Qwen and $1.04/$1.04 for Llama, checked on 2026-09-12. [Official pricing](https://www.together.ai/pricing).

The protocol proposes a **$250 incremental cap**, with $5 preflight and $25 uncertain-delivery subcaps inside it. These are limits, not expected spending or a completion guarantee. The independent inventory also records a cheaper $100 option and a different $250 allocation; the proposed protocol's allocation is authoritative for review. If all responses reach their nominal output limits, estimated charges are $224.95 before preflight and uncertain deliveries, so that scenario could exhaust the proposed cap. No automatic cap increase is permitted.

With healthy endpoints, plan **2–4 hours after launch**, starting at four concurrent requests per endpoint and increasing only when measured throughput improves. Outages and cooldowns are additional. Public endpoint listings do not establish account-specific availability or unchanged backend defaults. [Together model catalog](https://docs.together.ai/docs/serverless/models).

**Conditional work.** 4B would compare original and corrected oracle labels in captured histories, requiring 5,248 fresh verdicts plus separately scoped adjudication. 4C would test a supported repair on two new families, both original anchors and 48 new questions, requiring 3,840 recipient verdicts plus source generation and adjudication. Their actual edit maps, adjudicator/endpoints, new corpus, forecasts and caps remain future stage inputs. Approval of 4A would not approve either later stage.

**Before launch.** Implement the production replay runner and the specified analysis, validate them together end to end with synthetic responses and a simulated interruption, record a clean source commit, refresh endpoint settings/prices, and obtain the stage's spending decision. A 10-minute monitor is configured when execution starts. Completed cells must survive interruption; only missing requests are resumed under the cap. Preparation has not created any experiment results.

Rebuild the offline panel from the active repository using a fresh output directory outside Git and outside the completed Phase 3 archive:

```powershell
.\.venv\Scripts\python.exe -B scripts/phase4_prepare.py `
  --archive E:\selvarath-archive\phase3-main-7172776-2026-09-07 `
  --out E:\selvarath-archive\phase4-preparation-2026-09-12 `
  --summary reports/phase4-preparation-2026-09-12/prepared-panel.json
```
