#!/usr/bin/env bash
# Main-run driver: alternate review waves and run passes until the manifest converges,
# the reviewer goes unreachable, or the supervisor STOPs.
#
# This replaces the earlier scratchpad driver, whose fatal flaw was checking only the
# review wave's exit code and then unconditionally relaunching the supervisor every round:
# a supervisor STOP ("STOP for manual review", "STOP uncertain spend $X exceeds ceiling",
# "STOP resume budget exhausted", "STOP call ... halted N consecutive", "STOP no outcome
# line") was silently cycled past instead of ending the run.
#
# This script propagates supervisor STOPs; a STOP terminates the orchestrator; it never
# relaunches past one. Concretely:
#  - the reviewer's quota is probed with a live call before every review wave, and the
#    orchestrator stops cleanly (exit 0) rather than dispatching into a dead quota;
#  - a review wave that exits nonzero stops the orchestrator (exit 3) instead of
#    proceeding to a run pass. The daemon wraps every failure, including an unreachable
#    reviewer, as an ABORT with exit 4, so no specific code is trusted to mean "benign":
#    any wave failure gets reviewed before another round runs;
#  - the run pass's combined stdout+stderr is scanned for any line matching the literal
#    text "supervisor: STOP". If found, the orchestrator logs the exact line(s) and exits
#    2 immediately. No further rounds run. This is the entire point of the rewrite;
#  - only when the run pass produced no STOP does the orchestrator check convergence
#    (results file line count >= TOTAL_CELLS) and, short of that, loop to the next round;
#  - a hard cap of ROUND_CAP rounds is a runaway backstop, not a safety control -- the
#    real safety controls are the ones enforced inside canary_supervisor.py itself.
#
# Every action is logged with a UTC timestamp so the record is a file, not a guess.
#
# All external command paths below are overridable via environment variables so a test
# harness can inject stubs; the defaults are the production values and are unchanged by
# sourcing this file, only by exporting the variable before invocation.
set -uo pipefail

: "${REPO:=/mnt/c/Users/Jack/Dev/FailureModeExperiment/selvarath-debate}"
: "${VENV:=/home/jack/.venvs/selvarath-phase2/bin/python}"
: "${CODEX:=/home/jack/.local/bin/codex}"
: "${MANIFEST:=rejudge/phase2_main_manifest_2026-08-06c.json}"
: "${AUTH:=rejudge/phase2_main_authorization_2026-08-06b.json}"
: "${ARCHIVE:=/mnt/e/selvarath-archive/main-2026-08-06}"
: "${LOG:=$ARCHIVE/orchestrator.log}"
: "${TOTAL_CELLS:=22140}"

# Script paths, also overridable so a test can point them at stubs without touching VENV
# (VENV stays a real interpreter; only the script it runs changes).
: "${REVIEW_DAEMON:=scripts/review_daemon.py}"
: "${SUPERVISOR:=scripts/canary_supervisor.py}"

# Where convergence is measured. Derived from ARCHIVE by default, same as the file the
# old script counted lines in; overridable directly if a test wants a different name.
: "${RESULTS_FILE:=$ARCHIVE/main_results.jsonl}"

# Runaway backstop, not a safety control. Overridable so a test is not forced to spend 60
# iterations proving the cap itself works; production always gets 60 unless told otherwise.
: "${ROUND_CAP:=60}"

cd "$REPO"
mkdir -p "$(dirname "$LOG")"

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

reviewer_up() {
  # A live probe, not a clock. The reset time Codex quoted is advisory and local; the only
  # thing that settles it is a call that comes back.
  local out
  out=$(echo "Reply with exactly: OK" | timeout 120 "$CODEX" exec --skip-git-repo-check - 2>&1)
  if grep -qi "usage limit\|quota" <<<"$out"; then return 1; fi
  grep -q "OK" <<<"$out"
}

say "main orchestrator armed; TOTAL_CELLS=$TOTAL_CELLS, round cap $ROUND_CAP"

for round in $(seq 1 "$ROUND_CAP"); do
  if ! reviewer_up; then
    say "round $round: reviewer unreachable or quota exhausted; stopping cleanly"
    exit 0
  fi

  say "round $round: review wave"
  "$VENV" "$REVIEW_DAEMON" --manifest "$MANIFEST" --authorization "$AUTH" \
    --archive "$ARCHIVE" --codex "$CODEX" --concurrency 8 --max-waves 1 2>&1 | tee -a "$LOG"
  review_rc=${PIPESTATUS[0]}
  if [ "$review_rc" != "0" ]; then
    say "round $round: review wave failed (exit $review_rc); stopping for review before any relaunch"
    exit 3
  fi

  say "round $round: run pass"
  sup_output=$("$VENV" "$SUPERVISOR" "$VENV" "$MANIFEST" "$AUTH" "$ARCHIVE" 2>&1 | tee -a "$LOG")
  sup_rc=${PIPESTATUS[0]}

  stop_lines=$(grep "supervisor: STOP" <<<"$sup_output" || true)
  if [ -n "$stop_lines" ]; then
    say "round $round: supervisor STOP detected (exit $sup_rc):"
    while IFS= read -r line; do
      [ -n "$line" ] && say "  $line"
    done <<<"$stop_lines"
    say "TERMINAL: supervisor refused to continue; a human or the session orchestrator must review before any relaunch"
    exit 2
  fi

  cells=$(grep -c . "$RESULTS_FILE" 2>/dev/null || echo 0)
  say "round $round complete: $cells / $TOTAL_CELLS cells (supervisor exit $sup_rc, no STOP)"
  if [ "$cells" -ge "$TOTAL_CELLS" ]; then
    say "CONVERGED"
    exit 0
  fi
done

say "round cap ($ROUND_CAP) reached without convergence or a STOP; stopping"
exit 1
