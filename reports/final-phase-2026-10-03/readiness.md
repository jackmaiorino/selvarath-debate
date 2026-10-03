# Final-phase continuation, 2026-10-03

The frontier authoring quality check has not started. Jack authorized continuation and confirmed 0/1/2/6, with query-budget coverage ahead of extra judge tiers. The Fable-selected arm is removed. Strong debaters and all eight judges remain in the prepared configuration; a measured pilot forecast determines the affordable main question count before measurement.

## Prepared and verified

- Six arms, both answer orders: world alone, debate k0/k1/k2/k6, debate plus world. No selector requests are dispatched.
- The question-retention code now matches the approved design: reversal flags are descriptive, while equal defensibility, key disagreement and failed fact checks reject a question.
- `split --main-questions N` produces a reproducible reduced sample and refuses to rewrite the split once main requests are registered.
- The supported `python -m finalphase.cli` entrypoint refuses paid dispatch without credentials, successful provider smoke checks and a current run manifest. Substantial stages also require serial/parallel completed-work throughput and placement checks. Main additionally requires the canary, oracle, pre-registration, measured forecast and funding checks. A bounded authoring quality check requests at most eight worlds.
- The $200 author, $200 validation, $60 canary, $650 pilot and $4,300 main stage caps plus the $590 reserve total $6,000.
- Affected final-phase tests: 22 passed. The focused end-to-end test covers authoring, validation, splits, capped and uncapped canary debates, all six judge arms, query rejection accounting and replay. No live frontier study result is implied.

## Cost, before measurement

The forecast uses September 30 rates, cached oracle worlds, live canary calls, batch frontier main calls, up to 192 pilot questions and all eight judges. It is illustrative rather than measured. Main effort remains medium; debater effort remains high.

| Main questions | Low whole-phase cost | High whole-phase cost | Reserve |
|---|---:|---:|---:|
| 1,068 | $5,373 | $9,671 | $590 |
| 450 | $2,951 | $5,349 | $590 |

At these assumptions, all eight judges and all query comparisons fit with 1,068 questions in the low scenario or at most 458 in the high scenario, including the reserve. Neither count is selected yet. Fewer questions widen uncertainty; the study must not promise the original curve-shape precision after a reduction. Exact calculations are in `cost_forecast.json` and `cost_forecast_450.json`.

## Observed blockers and placement

- At 14:31 UTC on October 3, all three API keys were present in the Windows user environment. Real requests to the planned Luna and Astra endpoints returned HTTP 429 `credit_balance_exhausted`. OpenAI funding remains necessary before the authoring check.
- The Anthropic Fable request returned HTTP 400 because the key is not scoped to a workspace and requires an `anthropic-workspace-id` header. `ANTHROPIC_WORKSPACE_ID` was not configured; a read-only List Workspaces request returned HTTP 403 `permission_error`. The provider adapter now passes that environment variable as a default header for both live and batch requests. Jack must supply the intended workspace ID or use a workspace-scoped key before access can be verified. See [Anthropic authentication](https://platform.claude.com/docs/en/manage-claude/authentication).
- Together DeepSeek Pro completed its smoke request successfully at an observed token cost of $0.00009636. The external run root contains `access_checks.json` and `anthropic_workspace_lookup.json`, and the small manifest records the observed checks. No key values were printed or saved in the repository.
- Funding reconciliation remains outstanding before main. The September 13 reconstructed remainder is historical, not a current all-provider balance.
- Jack's PC has 24 logical CPUs and 128 GiB RAM. The current MTG Stage1 reservation and other owners' work are preserved; no heavy local compute or GPU allocation was started.
- Both Git OpenSSH and Windows OpenSSH read-only HaleysPC probes stopped at host-key verification. SSH trust was not weakened, and remote hardware availability is not claimed.
- A read-only RunPod inventory found one running Spellbench allocation and five exited Pods. None was changed, and no paid compute was allocated for this study.
- Representative serial/parallel frontier throughput is not qualified while account access is unavailable. Fake-provider tests are correctness evidence, not a throughput qualification. The substantial stage launcher rejects missing evidence.

## Resume

1. Load Windows user API variables into the experiment subprocess and rerun small provider access/billing checks after Jack confirms the account fixes. Record their observed outcomes in the existing small `D:/finalphase-runs/final-phase-2026-10-03/run_manifest.json`.
2. Through the supported launcher, author at most eight initial worlds with `author --worlds 8 --quality-check --mode batch`. Review this quality check before full authoring.
3. Qualify representative serial/parallel work and placements; record the chosen execution allocation in that same manifest before substantial stages.
4. Validate the benchmark and conduct the debater canary, then the engineering/cost pilot and oracle qualification. Freeze question count, roster and pre-registration before main. Record funding reconciliation and actual measured forecast; do not inspect pilot verification effects to choose the cuts.

The original Claude worktree was left unchanged. Continuation changes are on `codex/final-phase-next-stage` in `C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-final-phase-codex`.
