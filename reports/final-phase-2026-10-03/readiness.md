# Final-phase continuation, 2026-10-03

All eight authoring precheck worlds are complete, with 94/96 mechanical question checks passing. All four Fable worlds exceed the requested 1,000 to 1,500 words (observed 1,628 to 1,782), so the quality review records a protocol deviation requiring resolution before broader benchmark generation. Independent answer-key validation and main have not started. Jack confirmed 0/1/2/6, prioritizing query-budget coverage over extra judge tiers. The Fable-selected arm is removed; strong debaters and all eight judges remain in the prepared configuration. A measured pilot forecast determines the affordable main question count before measurement.

## Prepared and verified

- Six arms, both answer orders: world alone, debate k0/k1/k2/k6, debate plus world. No selector requests are dispatched.
- The question-retention code now matches the approved design: reversal flags are descriptive, while equal defensibility, key disagreement and failed fact checks reject a question.
- `split --main-questions N` produces a reproducible reduced sample and refuses to rewrite the split once main requests are registered.
- The supported `python -m finalphase.cli` entrypoint refuses paid dispatch without credentials, successful provider smoke checks and a current run manifest. Substantial stages also require serial/parallel completed-work throughput and placement checks. Main additionally requires the canary, oracle, pre-registration, measured forecast and funding checks. A bounded authoring quality check requests at most eight worlds.
- The $200 author, $200 validation, $60 canary, $650 pilot and $4,300 main stage caps plus the $590 reserve total $6,000.
- Affected final-phase tests: 29 passed. The focused end-to-end test covers authoring, validation, splits, capped and uncapped canary debates, all six judge arms, query rejection accounting and replay. Batch regressions cover Anthropic's ID constraints, out-of-order collection, restart without resubmission and keeping the HTTP client alive until result streaming finishes. Authoring now exits unsuccessfully if requested worlds remain unusable. A bounded token-budget probe preserves the original prompts and high effort, uses distinct request IDs, and can enforce one attempt and a lower cumulative stage cap. Author defaults are now Fable 64,000 and Astra 32,000; final measurement limits remain subject to pilot calibration. No live frontier study result is implied.
- Replayed all 16 completed author requests from the actual store with provider dispatch disabled: zero provider requests, all cached responses recovered, and the primary SQLite SHA-256 remained bit-identical (`97754fe02037dbcf23f27c990834028bb88a2d6372e9af2956385148b26405ec`).

## Cost, before measurement

The forecast uses September 30 rates, cached oracle worlds, live canary calls, batch frontier main calls, up to 192 pilot questions and all eight judges. Fable author output is now 41,000 tokens in the low scenario and 64,000 in the high, informed by the single completed probe. The $7.5128 of failed-call cost and live-probe premium is included. Other stages remain illustrative rather than measured. Main effort remains medium; debater effort remains high.

| Main questions | Low whole-phase cost | High whole-phase cost | Reserve |
|---|---:|---:|---:|
| 1,068 | $5,419 | $9,743 | $590 |
| 450 | $2,996 | $5,420 | $590 |

At these assumptions, all eight judges and all query comparisons fit with at most 1,065 questions in the low scenario or 448 in the high scenario, including the reserve. Neither count is selected yet. Fewer questions widen uncertainty; the study must not promise the original curve-shape precision after a reduction. Exact calculations are in `cost_forecast.json` and `cost_forecast_450.json`. Regeneration was checked against the prior forecast: main costs are unchanged, and only authoring increases by $45.5128/$71.5128 in the low/high scenarios.

## Initial precheck and placement, 2026-10-03

- Jack reported adding $10 to each frontier account for the eight-world quality check. At 15:52 UTC on October 3, the planned Astra endpoint completed a real access request for $0.00041. The earlier Luna/Astra credit failures are resolved for the Astra authoring role; no claim of main-phase funding reconciliation follows from this top-up.
- Jack supplied the intended Anthropic workspace ID. It is saved in the Windows user environment and passed as a default header for live and batch requests. At 16:27 UTC, the planned Fable endpoint completed its access request for $0.00042. All three providers now pass the recorded access checks. See [Anthropic authentication](https://platform.claude.com/docs/en/manage-claude/authentication).
- The first authoring submission was rejected because Anthropic forbids colons in `custom_id`. Anthropic batch IDs now use stable SHA-256 strings, and collection restores the original study IDs before saving. Fable collection also required holding the SDK client until its lazy result stream finished. Both repairs reused the original requests and completed batches. See [Anthropic batch request constraints](https://platform.claude.com/docs/en/api/messages/batches/create).
- Astra produced four parseable worlds and 48 questions, with 48/48 mechanical checks passing, for $1.85179. Fable's first four requests and four allowed retries all exhausted 32,000 output tokens, for $6.47839 and zero usable worlds. The original precheck cost $8.33018 in total. No partial Fable output was salvaged, and independent answer-key validation has not started.
- One live Fable probe completed W001 with a 64,000-token allowance, high effort, one attempt, and an $11.75 cumulative author-stage cap. It used 40,985 output tokens and cost $2.06882. Its JSON contains all 12 questions and all task types; 10/12 mechanical checks pass. Q02 has unequal signal-word counts; Q04 has imbalanced answer lengths. The world has 1,782 words against the requested 1,000 to 1,500. It has not been edited or independently validated. The design permits calibrating output limits before main; prompts, selection rules and formal measurement gates are unchanged.
- At the October 3 checkpoint, authoring cost was $10.399: $8.54721 Anthropic and $1.85179 OpenAI. Subtracting those costs and the access checks from Jack's reported $10 top-ups leaves an estimated $1.45237 Anthropic and $8.1478 OpenAI. These are calculated balances, not provider balance queries. One further 64,000-token Fable batch call has a $1.610175 maximum estimate, above the remaining Anthropic credit. A $10 Anthropic top-up covers the three outstanding worlds and their one allowed retry each at these limits. Authoring paused at that checkpoint until the October 5 top-up described below.
- Together DeepSeek Pro completed its smoke request successfully at an observed token cost of $0.00009636. The external run root contains `access_checks.json` and `anthropic_workspace_lookup.json`, and the small manifest records the observed checks. No key values were printed or saved in the repository.
- Funding reconciliation remains outstanding before main. The September 13 reconstructed remainder is historical, not a current all-provider balance.
- Jack's PC has 24 logical CPUs and 128 GiB RAM. The current MTG Stage1 reservation and other owners' work are preserved; no heavy local compute or GPU allocation was started.
- Both Git OpenSSH and Windows OpenSSH read-only HaleysPC probes stopped at host-key verification. SSH trust was not weakened, and remote hardware availability is not claimed.
- A read-only RunPod inventory found one running Spellbench allocation and five exited Pods. None was changed, and no paid compute was allocated for this study.
- Representative serial/parallel frontier throughput is not yet qualified. Fake-provider tests are correctness evidence, not a throughput qualification. The substantial stage launcher rejects missing evidence.
- The illustrative low forecast already exceeds the validation ($275.23 versus $200) and pilot ($752.65 versus $650) stage caps; high authoring also exceeds its cap ($201.51 versus $200). Allocation must be reconciled using measured costs before substantial dispatch while preserving the $6,000 ceiling and reserve. Stage caps have not been increased.

## Completed authoring resume and review, 2026-10-05

The remaining three Fable worlds completed on their first attempt, for $3.344585. The finite collector exited successfully; all batches are collected and no calls remain active. Total authoring cost is $13.743585, including the earlier truncated attempts. Against reported funding of $20 Anthropic and $10 OpenAI, calculated remaining credit is $8.107785/$8.1478 respectively, after access checks. These figures are derived from the call ledger rather than a provider balance query. The cumulative authoring cap was $21.75; overall phase caps and the $6,000 ceiling are unchanged.

| Author | Complete worlds | Mechanical question checks | World words |
|---|---:|---:|---:|
| Astra | 4 | 48/48 | 1,160 to 1,279 |
| Fable | 4 | 46/48 | 1,628 to 1,782 |

All worlds contain twelve questions, required fields and all six task types. W001-Q02 fails signal-word balance and W001-Q04 fails the 1.25 answer-length ratio. These remain subject to the existing mechanical rejection rule. The other 94 candidates still require independent answer-key validation; mechanical passes are not scientific validation.

The length finding applies to every Fable world. The authoring specification requests 1,000 to 1,500 words (`../final-phase-2026-09-30/design.md`, section 5), while the mechanical checker only enforces a 700-word minimum. The review records this discrepancy without changing the frozen authoring prompt, trimming prose or silently accepting a new upper bound. Resolve the length deviation prospectively before broader generation. Current launch guards also require throughput qualification for substantial stages; funding reconciliation, canary, oracle, pre-registration and measured costs remain outstanding before main.

Batch checks run in the finite collector at five minutes, backing off to fifteen after two unchanged checks and resetting on completed-work changes. The collector has finished; there is no pending watcher or recurring automation to restart. The current result and review are recorded in the existing run manifest and author_quality_summary.json.

## Resume

1. Resolve the systematic Fable word-length deviation while preserving frozen prompts and authored bytes. Current evidence and the review finding are in `D:/finalphase-runs/final-phase-2026-10-03/run_manifest.json` and `author_quality_summary.json`.
2. Close the quality review before full authoring. Apply the existing mechanical rejection rule; independent answer-key validation has not yet run. No benchmark prose or candidate answers are edited by hand.
3. Qualify representative serial/parallel work and placements; record the chosen execution allocation in that same manifest before substantial stages.
4. Validate the benchmark and conduct the debater canary, then the engineering/cost pilot and oracle qualification. Freeze question count, roster and pre-registration before main. Record funding reconciliation and actual measured forecast; do not inspect pilot verification effects to choose the cuts.

The original Claude worktree was left unchanged. Continuation changes are on `codex/final-phase-next-stage` in `C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-final-phase-codex`.
