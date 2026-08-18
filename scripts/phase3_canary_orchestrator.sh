#!/usr/bin/env bash
# Phase-3 canary driver: alternate review waves and run passes until the manifest converges,
# the reviewer goes unreachable, or a STOP is detected.
#
# Structurally the phase-3 sibling of scripts/main_orchestrator.sh (the phase-2 main-run
# orchestrator): the same round loop (reviewer probe -> review wave -> run pass -> convergence
# check) and the same STOP-propagation discipline (a STOP terminates the orchestrator; it never
# relaunches past one). Two things are additive on top of that established pattern, not a
# redesign of it:
#
#  - the run pass is launched under BOTH scripts/canary_supervisor.py's own auto-resume STOP
#    discipline AND scripts/phase3_stall_watchdog.py. Phase 2 had no equivalent of the
#    2026-08-10 five-hour ssl.read hang caught anywhere until the driver process itself
#    returned (or never did, which is exactly what happened); the watchdog kills a driver that
#    has produced no new result-store row for 30 minutes, independent of whether the driver's
#    own transport pins are wired correctly that particular run. This orchestrator watches for
#    BOTH "supervisor: STOP ..." and "watchdog: STOP ..." lines; either is terminal;
#  - scripts/canary_supervisor.py and scripts/review_daemon.py are invoked with the additive
#    --driver-module/--driver-mode/--results-key flags those two scripts gained alongside
#    rejudge/phase3_runner.py, pointed at rejudge.phase3_runner and the phase-3 manifest's
#    canary_results_path (its ledger block has no flat results_path the way phase-2's
#    canary/main manifests do -- see rejudge.phase3_manifest -- so the phase-2 default would
#    silently KeyError against a phase-3 manifest without this flag).
#
# Target runtime is WSL (the live run's result-store locks use POSIX fcntl, per the
# canary-run-environment note), so this is POSIX sh/bash, matching main_orchestrator.sh. NOT
# exercised end-to-end as part of building it (no WSL session, no live manifest/authorization
# to launch against here) -- reviewed by inspection against canary_supervisor.py's actual CLI
# contract and phase3_stall_watchdog.py's actual process-group-kill contract instead.
#
# All external command paths and tunables below are overridable via environment variables so a
# test harness can inject stubs; the defaults are the production values and are unchanged by
# sourcing this file, only by exporting a variable before invocation.
set -uo pipefail

: "${REPO:=/mnt/c/Users/Jack/Dev/FailureModeExperiment/selvarath-debate}"
: "${VENV:=/home/jack/phase3-venv/bin/python}"
: "${CODEX:=/home/jack/.local/bin/codex}"
: "${MANIFEST:=rejudge/phase3_manifest_2026-08-18.json}"
: "${AUTH:=rejudge/phase3_canary_authorization_2026-08-18.json}"
: "${ARCHIVE:=/mnt/e/selvarath-archive/phase3-2026-08-18}"
: "${LOG:=$ARCHIVE/orchestrator.log}"
# 540 pre-seeded transcript rows (492 main + 48 canary; the 492 are inert for the canary plan,
# pre-satisfying the later main-stage dependency -- see the canary authorization record's
# scope.store note) + 1,680 canary judgment/capability slots this stage actually executes.
: "${TOTAL_CELLS:=2220}"

# Script paths, overridable so a test can point them at stubs without touching VENV (VENV
# stays a real interpreter; only the script it runs changes).
: "${REVIEW_DAEMON:=scripts/review_daemon.py}"
: "${SUPERVISOR:=scripts/canary_supervisor.py}"
: "${WATCHDOG:=scripts/phase3_stall_watchdog.py}"
: "${DRIVER_MODULE:=rejudge.phase3_runner}"
: "${RESULTS_KEY:=canary_results_path}"

# Where convergence is measured, and what the watchdog watches for staleness. Derived from
# ARCHIVE by default; overridable directly if a test wants a different name.
: "${RESULTS_FILE:=$ARCHIVE/phase3_canary_results.jsonl}"
: "${STALL_THRESHOLD_SECONDS:=1800}"

# Runaway backstops, not safety controls -- the real safety controls are the ones enforced
# inside canary_supervisor.py (uncertain-spend ceiling, same-call limit) and
# rejudge.phase3_runner (the canary spend cap itself) directly.
: "${ROUND_CAP:=60}"
# How long past the watchdog's own poll interval (30s in phase3_stall_watchdog.py's default)
# this script waits for it to notice the driver is gone on its own before moving on, once the
# driver has already exited. Generous on purpose: a round should never hang indefinitely on
# this, but should also never race the watchdog's own poll loop under normal load.
: "${WATCHDOG_DRAIN_SECONDS:=35}"

cd "$REPO"
mkdir -p "$(dirname "$LOG")"

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

reviewer_up() {
  # A live probe, not a clock -- unchanged from main_orchestrator.sh's own check: the phase-3
  # gate reviewer (gpt-5.6-sol) is the same external codex reviewer phase 2's review_daemon.py
  # already dispatches through, so the SAME probe applies.
  local out
  out=$(echo "Reply with exactly: OK" | timeout 120 "$CODEX" exec --skip-git-repo-check - 2>&1)
  if grep -qi "usage limit\|quota" <<<"$out"; then return 1; fi
  grep -q "OK" <<<"$out"
}

say "phase-3 canary orchestrator armed; TOTAL_CELLS=$TOTAL_CELLS, round cap $ROUND_CAP, " \
    "stall threshold ${STALL_THRESHOLD_SECONDS}s"

for round in $(seq 1 "$ROUND_CAP"); do
  if ! reviewer_up; then
    say "round $round: reviewer unreachable or quota exhausted; stopping cleanly"
    exit 0
  fi

  say "round $round: review wave"
  "$VENV" "$REVIEW_DAEMON" --manifest "$MANIFEST" --authorization "$AUTH" \
    --archive "$ARCHIVE" --codex "$CODEX" --driver-module "$DRIVER_MODULE" \
    --concurrency 8 --max-waves 1 --exit-when-empty 2>&1 | tee -a "$LOG"
  review_rc=${PIPESTATUS[0]}
  if [ "$review_rc" != "0" ]; then
    say "round $round: review wave failed (exit $review_rc); stopping for review before any relaunch"
    exit 3
  fi

  say "round $round: run pass"
  sup_log="$ARCHIVE/orchestrator_supervisor_round${round}.log"
  wd_log="$ARCHIVE/orchestrator_watchdog_round${round}.log"

  # setsid: give the supervisor (and whatever driver subprocess it launches) a NEW process
  # group, with a leader that is NOT this orchestrator script. phase3_stall_watchdog.py's
  # kill_process_tree does `os.killpg(os.getpgid(pid), SIGKILL)` on POSIX -- the whole group
  # the given pid belongs to -- and canary_supervisor.py launches its driver subprocess WITHOUT
  # its own new session, so that driver joins whatever group the supervisor itself started in.
  # Without setsid here, that group would be THIS script's own, and a stall-kill would take the
  # orchestrator down with it.
  setsid "$VENV" "$SUPERVISOR" --driver-module "$DRIVER_MODULE" --driver-mode subagent-batch \
    --results-key "$RESULTS_KEY" "$VENV" "$MANIFEST" "$AUTH" "$ARCHIVE" \
    >"$sup_log" 2>&1 &
  sup_pid=$!

  "$VENV" "$WATCHDOG" --results "$RESULTS_FILE" --pid "$sup_pid" \
    --stall-threshold-seconds "$STALL_THRESHOLD_SECONDS" >"$wd_log" 2>&1 &
  wd_pid=$!

  wait "$sup_pid"
  sup_rc=$?
  cat "$sup_log" >>"$LOG"

  # The watchdog notices the driver is gone on its own next poll and exits 0 (see
  # phase3_stall_watchdog.watch_for_stall); it is not force-stopped, only bounded, so a STOP it
  # already recorded is never lost to a race with this drain.
  if kill -0 "$wd_pid" 2>/dev/null; then
    ( sleep "$WATCHDOG_DRAIN_SECONDS" && kill "$wd_pid" 2>/dev/null ) &
    reaper=$!
    wait "$wd_pid" 2>/dev/null
    kill "$reaper" 2>/dev/null || true
  fi
  cat "$wd_log" >>"$LOG"

  stop_lines=$( { grep -h "supervisor: STOP" "$sup_log" "$wd_log" 2>/dev/null
                  grep -h "watchdog: STOP" "$sup_log" "$wd_log" 2>/dev/null; } || true)
  if [ -n "$stop_lines" ]; then
    say "round $round: a STOP was detected (supervisor exit $sup_rc):"
    while IFS= read -r line; do
      [ -n "$line" ] && say "  $line"
    done <<<"$stop_lines"
    say "TERMINAL: refusing to continue; a human or the session orchestrator must review before any relaunch"
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
