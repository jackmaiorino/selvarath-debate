@echo off
rem Launches the phase-3 canary orchestrator inside WSL, detached from any Claude Code
rem session. Run via Task Scheduler (task: SelvarathPhase3Canary) so the process tree
rem survives session restarts; the API key passes through WSLENV from the Windows user
rem environment and never touches a file or command line.
set WSLENV=TOGETHER_API_KEY/u:BLOCKLIST/u
set BLOCKLIST=rejudge/phase3_context_blocklist_canary_2026-08-19.json
wsl.exe -e bash -c "cd /mnt/c/Users/Jack/Dev/FailureModeExperiment/selvarath-debate && bash scripts/phase3_canary_orchestrator.sh"
