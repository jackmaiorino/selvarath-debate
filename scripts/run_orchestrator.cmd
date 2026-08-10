@echo off
rem Launches the main-run orchestrator inside WSL, detached from any Claude Code session.
rem Run via Task Scheduler (task: SelvarathOrchestrator) so the process tree survives
rem session restarts; the API key passes through WSLENV from the Windows user environment
rem and never touches a file or command line.
set WSLENV=TOGETHER_API_KEY/u
wsl.exe -e bash -c "cd /mnt/c/Users/Jack/Dev/FailureModeExperiment/selvarath-debate && bash scripts/main_orchestrator.sh"
