# Phase 4A result

**Complete at 09:20 Eastern on 2026-09-12. All 3,936 verdicts are valid, with no administrative failures or uncertain charges. Total recorded usage: $51.99527168 of the approved $250 cap. Measurement and analysis took approximately 39 minutes. The monitor has been removed.**

The receiving judge contributes to the verification effect. Both recipients read the same captured histories, but Qwen became less accurate on average while Llama became more accurate in point estimates. A blanket explanation that Qwen-origin histories are simply worse for every recipient is not supported by the prespecified source test.

| Recipient | Empty history | Qwen history | Llama history |
|---|---:|---:|---:|
| Qwen 3.8 | 38/656 (5.79%) | 93/656 (14.18%) | 54/656 (8.23%) |
| Llama 3.3 70B | 79/656 (12.04%) | 63/656 (9.60%) | 70/656 (10.67%) |

Entries are strict errors, so lower is better. Every cell uses the same 656 matched units across 82 questions, two debater sources, two transcripts and both mirrored sides. All recipient verdicts were generated fresh.

**Prespecified tests.** The recipient difference in the average history-minus-empty effect is **+7.317 percentage points** (96/1312), with 95% question-cluster interval **[4.040, 10.747] pp**, conservative 97.5% interval **[3.582, 11.204] pp**, and Holm-adjusted **p=0.000800**. It passes both the significance and the integer practical-effect gate. Qwen's average error change is +5.412 pp; Llama's is -1.905 pp. These model-specific means explain the contrast and are not additional confirmatory tests.

The average history-source difference is **+2.439 pp** (32/1312), 95% interval **[0.000, 5.030] pp**, 97.5% interval **[-0.381, 5.412] pp**, Holm-adjusted **p=0.054595**. It fails both the significance and practical-effect gates. This is not an equivalence result or proof that history source does not matter.

**Descriptive pattern.** Qwen history raises Qwen's errors by 8.384 pp but lowers Llama's by 2.439 pp in point estimates. Llama history raises Qwen's errors by 2.439 pp and lowers Llama's by 1.372 pp. The recipient-by-source interaction is +7.012 pp, with descriptive 95% interval [3.659, 10.518] pp. It was not a co-primary test. Qwen is more accurate without history. The models have different baseline error rates; this experiment does not separately identify capability, calibration or baseline headroom as explanations.

**Validity and interpretation.** There were no invalid verdicts, so all 656 units remain in the common-valid sensitivity and the recipient result is unchanged. Fresh own-history replay closely matches saved Phase 3 counts: Qwen 95 to 93 errors, Llama 69 to 70. This descriptive check does not prove absence of provider drift. The experiment tests final readout of captured histories, not live adaptive query selection. History source still combines query content, reply quality, length, order and operational feedback. No internal neural mechanism, universal model law, Llama safety pass, or oracle-label error rate has been established.

**Next stage.** The prespecified statistical and semantic entry condition for 4B is satisfied through the recipient contrast. Before 4B recipient evaluation, outcome-blind adjudication and human source checks must identify at least 20 clear label corrections across 10 questions and at least 40 changed donor/unit packets. Adjudication and evaluation require their separately scoped spending decisions. Neither 4B nor 4C has been launched.

**Verification.** Completion output hashes, all 3,936 durable SQLite results, source-code hashes and the successful process exit were checked. An independent recomputation from raw responses reproduced all six error counts and both 10,000-draw bootstrap results exactly. There were exactly 3,936 main attempts and six synthetic preflight attempts, all successful. The implementation passed 51 focused tests and a full offline interruption/replay simulation before spending. Source commit: `030fee57d045431638e986ad4fd601447ad6c9e7`.

Machine-readable aggregates: [summary.json](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/reports/phase4-results-2026-09-12/summary.json). Frozen private analysis: [phase4_analysis.json](E:/selvarath-archive/phase4a-history-crossover-2026-09-12/analysis/phase4_analysis.json). No raw prompts, gold answer keys or responses are copied into this public report.
