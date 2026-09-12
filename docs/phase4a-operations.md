# Phase 4A execution

Jack approved launch on 2026-09-12 after reviewing the staged protocol. The [execution record](C:/Users/Jack/Dev/FailureModeExperiment/selvarath-debate-v3-codex/rejudge/phase4a_execution_2026-09-12.json) authorizes only 4A: $250 total, including $5 synthetic preflight and $25 uncertain-delivery limits. Stages 4B and 4C remain separate decisions. Scientific inputs and gates remain those in the original prepared protocol; the execution record adds spending authority without rewriting that preparation artifact.

Inputs: `E:\selvarath-archive\phase4-preparation-2026-09-12`. Run directory: `E:\selvarath-archive\phase4a-history-crossover-2026-09-12`.

From `C:\Users\Jack\Dev\FailureModeExperiment\selvarath-debate-v3-codex`, launch the synthetic preflight, then measurement after `preflight.json` says `passed`:

```powershell
& .\scripts\phase4_launch.ps1 -Authorization .\rejudge\phase4a_execution_2026-09-12.json -Mode preflight
& .\scripts\phase4_launch.ps1 -Authorization .\rejudge\phase4a_execution_2026-09-12.json -Mode run
```

The launcher starts a hidden WMI-brokered process outside Codex's process job. Credentials travel only in its environment. Each launch writes a private receipt and stdout/stderr under the run directory's `launches` folder. Confirm progress in `status.json`, plus the actual Python process, before describing the experiment as running.

`state.sqlite3` is the durable source of truth. Full-synchronous SQLite transactions save a pre-call reservation, then atomically save the complete response, completed cell and charge. `results.jsonl` and `status.json` are replaceable views. Unknown deliveries retain their full conservative reservation. A completed malformed verdict is retained and never regenerated. A completed response without usage is retained with reserved uncertainty. Startup books interrupted in-flight attempts as uncertain once, then retries only missing cells.

Begin with four workers per endpoint. The runner records completed verdicts/hour and probes higher concurrency up to 16 per endpoint only when throughput supports it. Failed endpoints pause and retry an existing failed cell before admitting new work. Every physical attempt is charged or conservatively reserved; SDK retries are disabled. The same command resumes after an environmental interruption. Never delete or reset the database, completed responses, manifest or spending history.

A single-process lock prevents duplicate runners. `pause.request` in the run directory stops new dispatch while active requests settle. Cap stops, persistent model/configuration mismatches and accounting inconsistencies require inspection; the monitor must not clear them automatically. Routine provider cooldowns require no manual restart. If analysis fails after all requests complete, rerunning the measurement command reuses the saved cells and retries analysis without further verdict calls.

At completion the runner executes the frozen analysis and writes `completion.json` with output hashes and spending. A requests-complete counter alone does not establish analysis completion. The 10-minute task monitor checks durable progress, process identity, cooldowns and remaining cap; it resumes routine interrupted work, reports meaningful failures or completion, and removes itself when finished. It does not launch later stages.
