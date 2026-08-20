@echo off
rem Launches the phase-3 canary orchestrator inside WSL, detached from any Claude Code
rem session. Run via Task Scheduler (task: SelvarathPhase3Canary) so the process tree
rem survives session restarts; the API key passes through WSLENV from the Windows user
rem environment and never touches a file or command line.
rem
rem Codex re-review (2026-08-19), blocker 2c: no BLOCKLIST env var here any more. It used to
rem hard-code the pre-amendment 72-exclusion file (rejudge/phase3_context_blocklist_canary_
rem 2026-08-19.json, no "b" suffix) and would have kept running it even after the manifest
rem moved on to the regenerated zero-exclusion one -- a second, silently stale source of truth.
rem scripts/phase3_canary_orchestrator.sh now resolves the eligibility blocklist path from the
rem manifest itself (frozen_inputs.context_blocklist_canary_report_tracked_path), so this
rem launcher has nothing left to set for it.
set WSLENV=TOGETHER_API_KEY/u
wsl.exe -e bash -c "cd /mnt/c/Users/Jack/Dev/FailureModeExperiment/selvarath-debate && bash scripts/phase3_canary_orchestrator.sh"
