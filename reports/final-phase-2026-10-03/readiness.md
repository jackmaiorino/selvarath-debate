# Final-phase continuation, 2026-10-03

The eight-world authoring precheck is incomplete: Astra produced four usable worlds, while all eight Fable attempts exhausted the 32,000-token output allowance. Original batches are fully collected. Jack authorized continuation and confirmed 0/1/2/6, with query-budget coverage ahead of extra judge tiers. The Fable-selected arm is removed. Strong debaters and all eight judges remain in the prepared configuration; a measured pilot forecast determines the affordable main question count before measurement.

## Prepared and verified

- Six arms, both answer orders: world alone, debate k0/k1/k2/k6, debate plus world. No selector requests are dispatched.
- The question-retention code now matches the approved design: reversal flags are descriptive, while equal defensibility, key disagreement and failed fact checks reject a question.
- `split --main-questions N` produces a reproducible reduced sample and refuses to rewrite the split once main requests are registered.
- The supported `python -m finalphase.cli` entrypoint refuses paid dispatch without credentials, successful provider smoke checks and a current run manifest. Substantial stages also require serial/parallel completed-work throughput and placement checks. Main additionally requires the canary, oracle, pre-registration, measured forecast and funding checks. A bounded authoring quality check requests at most eight worlds.
- The $200 author, $200 validation, $60 canary, $650 pilot and $4,300 main stage caps plus the $590 reserve total $6,000.
- Affected final-phase tests: 27 passed. The focused end-to-end test covers authoring, validation, splits, capped and uncapped canary debates, all six judge arms, query rejection accounting and replay. Batch regressions cover Anthropic's ID constraints, out-of-order collection, restart without resubmission and keeping the HTTP client alive until result streaming finishes. Authoring now exits unsuccessfully if requested worlds remain unusable. A bounded token-budget probe preserves the original prompts and high effort, uses distinct request IDs, and can enforce one attempt and a lower cumulative stage cap. No live frontier study result is implied.

## Cost, before measurement

The forecast uses September 30 rates, cached oracle worlds, live canary calls, batch frontier main calls, up to 192 pilot questions and all eight judges. It is illustrative rather than measured. Main effort remains medium; debater effort remains high.

| Main questions | Low whole-phase cost | High whole-phase cost | Reserve |
|---|---:|---:|---:|
| 1,068 | $5,373 | $9,671 | $590 |
| 450 | $2,951 | $5,349 | $590 |

At these assumptions, all eight judges and all query comparisons fit with 1,068 questions in the low scenario or at most 458 in the high scenario, including the reserve. Neither count is selected yet. Fewer questions widen uncertainty; the study must not promise the original curve-shape precision after a reduction. Exact calculations are in `cost_forecast.json` and `cost_forecast_450.json`.

## Observed blockers and placement

- Jack reported adding $10 to each frontier account for the eight-world quality check. At 15:52 UTC on October 3, the planned Astra endpoint completed a real access request for $0.00041. The earlier Luna/Astra credit failures are resolved for the Astra authoring role; no claim of main-phase funding reconciliation follows from this top-up.
- Jack supplied the intended Anthropic workspace ID. It is saved in the Windows user environment and passed as a default header for live and batch requests. At 16:27 UTC, the planned Fable endpoint completed its access request for $0.00042. All three providers now pass the recorded access checks. See [Anthropic authentication](https://platform.claude.com/docs/en/manage-claude/authentication).
- The first authoring submission was rejected because Anthropic forbids colons in `custom_id`. Anthropic batch IDs now use stable SHA-256 strings, and collection restores the original study IDs before saving. Fable collection also required holding the SDK client until its lazy result stream finished. Both repairs reused the original requests and completed batches. See [Anthropic batch request constraints](https://platform.claude.com/docs/en/api/messages/batches/create).
- Astra produced four parseable worlds and 48 questions, with 48/48 mechanical checks passing, for $1.85179. Fable's first four requests and four allowed retries all exhausted 32,000 output tokens, for $6.47839 and zero usable worlds. The original precheck cost $8.33018 in total. No partial Fable output was salvaged, and independent answer-key validation has not started.
- One live Fable probe is prepared for W001 with a 64,000-token allowance, high effort, one attempt, and an $11.75 cumulative author-stage cap. Its maximum estimated cost is about $3.22, within the remaining user-reported Anthropic credit. The design permits calibrating output limits before main; this probe does not change prompts, selection rules or formal measurement gates.
- Together DeepSeek Pro completed its smoke request successfully at an observed token cost of $0.00009636. The external run root contains `access_checks.json` and `anthropic_workspace_lookup.json`, and the small manifest records the observed checks. No key values were printed or saved in the repository.
- Funding reconciliation remains outstanding before main. The September 13 reconstructed remainder is historical, not a current all-provider balance.
- Jack's PC has 24 logical CPUs and 128 GiB RAM. The current MTG Stage1 reservation and other owners' work are preserved; no heavy local compute or GPU allocation was started.
- Both Git OpenSSH and Windows OpenSSH read-only HaleysPC probes stopped at host-key verification. SSH trust was not weakened, and remote hardware availability is not claimed.
- A read-only RunPod inventory found one running Spellbench allocation and five exited Pods. None was changed, and no paid compute was allocated for this study.
- Representative serial/parallel frontier throughput is not yet qualified. Fake-provider tests are correctness evidence, not a throughput qualification. The substantial stage launcher rejects missing evidence.
- The illustrative low forecast already exceeds the validation ($275.23 versus $200) and pilot ($752.65 versus $650) stage caps. Allocation must be reconciled using measured costs before substantial dispatch while preserving the $6,000 ceiling and reserve. Stage caps have not been increased.

## Resume

1. Run the bounded W001 token-budget probe through the supported launcher and record the observed result and cost in the existing small `D:/finalphase-runs/final-phase-2026-10-03/run_manifest.json`.
2. Resolve the remaining Fable output/funding constraint and review the complete eight-world quality check before full authoring.
3. Qualify representative serial/parallel work and placements; record the chosen execution allocation in that same manifest before substantial stages.
4. Validate the benchmark and conduct the debater canary, then the engineering/cost pilot and oracle qualification. Freeze question count, roster and pre-registration before main. Record funding reconciliation and actual measured forecast; do not inspect pilot verification effects to choose the cuts.

The original Claude worktree was left unchanged. Continuation changes are on `codex/final-phase-next-stage` in `C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-final-phase-codex`.
