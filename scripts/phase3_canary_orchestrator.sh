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
#    silently KeyError against a phase-3 manifest without this flag);
#  - the eligibility blocklist (scripts/phase3_context_precheck.py's output: a deterministic
#    ex-ante exclusion list, never mid-run discretion) is READ FROM THE MANIFEST ITSELF, never
#    from an operator-set path -- a stale $BLOCKLIST env var previously ran the pre-amendment
#    72-exclusion file against a manifest that had already moved on to the regenerated
#    zero-exclusion one (Codex re-review, 2026-08-19, blocker 2c). One source of truth: this
#    script resolves frozen_inputs.context_blocklist_canary_report_tracked_path off $MANIFEST
#    and forwards THAT to the driver as --context-blocklist; rejudge.phase3_runner also
#    independently refuses outright if the resolved file's canonical sha256 ever disagreed with
#    what the manifest bound at build time, so this is defense in depth, not the only check.
#    Amendment 4 (2026-08-19), package item 8, replaced the OLD blocklist-count-subtraction
#    convergence arithmetic
#    (TOTAL_CELLS - excluded_count, compared against a raw `grep -c .` line count) with a
#    manifested-minus-completed computation: rejudge.phase3_orchestrator_support.
#    remaining_canary_cells re-enumerates the SAME frozen plan the driver itself would
#    (rejudge.phase3_plan.enumerate_canary_cells against the manifest's own roster -- transcript
#    + judgment cells) and diffs its cell_key set against the DISTINCT cell_key values actually
#    present in $RESULTS_FILE. This is exact by construction, not merely a closer approximation:
#    rejudge.phase2_canary_order.CellResultStore.record refuses to overwrite an existing
#    cell_key, so every row in $RESULTS_FILE is exactly one genuinely completed cell, and a
#    context-guard-blocked cell is NEVER attempted and so never appears as a row there at all --
#    "blocked-record rows never count" falls out automatically rather than needing a special
#    case.
#  - v2 (2026-08-21): the 288 capability-anchor cells are EXCLUDED from the manifested/remaining
#    arithmetic above (decisions.launch_gates.canary_scope: they CARRY from the v1 identity by
#    exact cell-key set and store/hash binding, never preseeded or executed against the v2
#    store). Convergence alone no longer implies "done": two post-convergence, STOP-style hard
#    gates run once, before this orchestrator is allowed to report success --
#    verify_manifest_and_anchor_carry (re-validates the full manifest, re-deriving the
#    anchor-carry binding from the real, read-only v1 archive store) and run_polarity_gate
#    (scripts/phase3_polarity_verify.py, requiring 100% mirrored with an exact 48/48 per-judge
#    core-b0 split -- the corrected-mirroring property v2 exists to restore). All of the
#    convergence/gate arithmetic itself now lives in rejudge.phase3_orchestrator_support, a real
#    unit-tested Python module, rather than as logic embedded directly in bash one-liners.
#    Review-daemon concurrency and waves-per-round are likewise no longer independent operator
#    tunables: decisions.launch_gates.pace_measurement_window.crank_settings_freeze requires
#    them FROZEN in the manifest (frozen_inputs.crank_settings), resolved from there instead of
#    the old hardcoded --concurrency 8 literal and env-overridable MAX_WAVES default.
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
# v2 defaults (2026-08-21 materialization): the corrected-mirroring canary rerun. AUTH names a
# record the ORCHESTRATING SESSION writes separately (not this materialization task) -- the
# default points at where it will land, so a fresh checkout needs no env override to pick it up
# once that record exists.
: "${MANIFEST:=rejudge/phase3_manifest_v2_2026-08-21.json}"
: "${AUTH:=rejudge/phase3_canary_authorization_v2_2026-08-21.json}"
: "${ARCHIVE:=/mnt/e/selvarath-archive/phase3-v2-2026-08-21}"
: "${LOG:=$ARCHIVE/orchestrator.log}"
# Amendment 4 (2026-08-19), package item 8: convergence no longer hand-encodes a cell count
# here at all (the OLD TOTAL_CELLS=1980, "540 pre-seeded transcript rows [492 main + 48 canary]
# + 1,440 canary judgment/capability slots at the 6-judge roster" -- a number that silently drops
# out of sync the moment the roster or the transcript-row arithmetic changes). remaining_cells()
# below re-derives the SAME total every round, from the SAME frozen plan enumeration the driver
# itself runs (transcript + judgment cells together), so it can never drift.
#
# v2 (2026-08-21): the 288 capability-anchor cells are EXCLUDED from that re-derived total. They
# CARRY from the v1 identity by exact cell-key set and store/hash binding
# (decisions.launch_gates.canary_scope) -- they are never preseeded and never executed against
# the v2 store at all, so counting them as "manifested" work would make convergence permanently
# unreachable. Their integrity is verified separately, by manifest (re-)validation
# (verify_manifest_and_anchor_carry, below), not by a row appearing in $RESULTS_FILE.

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
# The usage ledger is the second liveness signal: in the high-budget endgame a healthy driver
# makes continuous provider calls on cells that then pause for gate review, so the results
# file goes quiet while the ledger advances every few seconds (2026-08-19 round-3 false kill).
: "${LIVENESS_FILE:=$ARCHIVE/phase3_usage.jsonl}"
: "${STALL_THRESHOLD_SECONDS:=1800}"
# v2 (2026-08-21): review-daemon concurrency and waves-per-round are no longer independent
# operator tunables here -- decisions.launch_gates.pace_measurement_window.crank_settings_freeze
# requires them FROZEN in the manifest before the pace-measurement window opens and untouched
# inside it. Resolved once, below (after $REPO/$MANIFEST are usable), from $MANIFEST's own
# frozen_inputs.crank_settings (bound and pinned by rejudge.phase3_manifest.
# _crank_settings_binding at build time: exactly review_daemon_concurrency=12,
# max_waves_per_round=4) -- replacing the old hardcoded --concurrency 8 literal and the old
# env-overridable MAX_WAVES default (no longer meaningful once the settings are frozen).

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

# v2 (2026-08-21): crank settings, convergence arithmetic, the anchor-carry re-verification and
# the post-convergence polarity-gate decision all live in rejudge.phase3_orchestrator_support
# now (a real, unit-tested Python module -- see tests/test_phase3_orchestrator_support.py),
# never as ad hoc logic inline in a bash string. Each $VENV -c block below does the minimum
# possible: load JSON, call one function, print the result.
crank_settings=$("$VENV" -c '
import json, sys
sys.path.insert(0, ".")
from rejudge import phase3_orchestrator_support as support
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
crank = support.resolve_crank_settings(manifest)
print(crank["review_daemon_concurrency"], crank["max_waves_per_round"])
' "$MANIFEST")
read -r REVIEW_CONCURRENCY REVIEW_MAX_WAVES <<<"$crank_settings"
say "crank settings resolved from the manifest (frozen, not env-overridable): " \
    "concurrency=$REVIEW_CONCURRENCY waves=$REVIEW_MAX_WAVES"

# Codex re-review (2026-08-19), blocker 2c: the eligibility blocklist path is resolved from
# THE MANIFEST ITSELF, never from an operator-set env var -- one source of truth, so this
# script can never launch a stale blocklist (the old $BLOCKLIST default named the pre-amendment
# 72-exclusion file) against a manifest that has moved on. rejudge.phase3_runner independently
# refuses outright if the resolved file's canonical sha256 ever disagreed with what the
# manifest bound at build time; this is defense in depth on top of that, not the only check.
blocklist_path=$("$VENV" -c '
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(manifest["frozen_inputs"]["context_blocklist_canary_report_tracked_path"])
' "$MANIFEST")
blocklist_args=(--context-blocklist "$blocklist_path")
say "eligibility blocklist resolved from the manifest: $blocklist_path"

# Amendment 4 (2026-08-19), package item 8: manifested-minus-completed, replacing the old
# blocklist-count subtraction. v2 (2026-08-21): the 288 capability-anchor cells are EXCLUDED
# from "manifested" -- decisions.launch_gates.canary_scope: they CARRY from the v1 identity by
# exact cell-key set and store/hash binding and are never preseeded or executed against the v2
# store at all, so counting them would make convergence permanently unreachable (1,152 fresh
# judgment slots + 540 preseeded transcript rows = 1,692 is the real v2 denominator). Their
# integrity is verified separately, by manifest (re-)validation
# (verify_manifest_and_anchor_carry, below), never by a row appearing in $RESULTS_FILE. See
# rejudge.phase3_orchestrator_support.remaining_canary_cells for the exact-by-construction
# argument (CellResultStore.record refuses to overwrite an existing cell_key, and a
# context-guard-blocked cell is never attempted, so "blocked-record rows never count" falls out
# automatically).
remaining_cells() {
  "$VENV" -c '
import json, sys
sys.path.insert(0, ".")
from rejudge import phase3_orchestrator_support as support
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
counts = support.remaining_canary_cells(manifest, sys.argv[2], project_root=".")
print(counts["manifested"], counts["completed"], counts["remaining"])
' "$MANIFEST" "$RESULTS_FILE"
}

read -r manifested_count completed_count remaining_count < <(remaining_cells)
say "manifested-minus-completed: $manifested_count manifested, $completed_count completed, " \
    "$remaining_count remaining"

reviewer_up() {
  # A live probe, not a clock -- unchanged from main_orchestrator.sh's own check: the phase-3
  # gate reviewer (gpt-5.6-sol) is the same external codex reviewer phase 2's review_daemon.py
  # already dispatches through, so the SAME probe applies.
  local out
  out=$(echo "Reply with exactly: OK" | timeout 120 "$CODEX" exec --skip-git-repo-check - 2>&1)
  if grep -qi "usage limit\|quota" <<<"$out"; then return 1; fi
  grep -q "OK" <<<"$out"
}

# v2 (2026-08-21) post-convergence gates, run ONCE after remaining_cells() first reaches zero,
# before this orchestrator is allowed to report success. Convergence alone (1,692 fresh
# transcript+judgment rows present) says nothing about the two things v1's incident actually
# broke: whether the 288 carried anchor rows are still exactly what the manifest bound (they are
# never re-executed here, only re-verified), and whether the mirroring fix that motivated v2 in
# the first place actually produced true K2 mirroring end to end. Both are STOP-style hard gates
# -- a failure here means the run converged on the wrong thing, which is worse than not
# converging at all.

verify_manifest_and_anchor_carry() {
  # Re-validates the FULL manifest (rejudge.phase3_runner.load_and_validate_manifest), which
  # rebuilds frozen_inputs wholesale -- including re-deriving the anchor-carry binding from the
  # real, read-only v1 archive store (rejudge.phase3_manifest._anchor_carry_binding) and
  # comparing it against what this manifest bound at build time. This is how the 288 carried
  # anchor cells are verified: never by a row appearing in $RESULTS_FILE (they never will), but
  # by every one of their v1 event hashes still matching on re-read.
  "$VENV" -c '
import sys
sys.path.insert(0, ".")
from rejudge import phase3_runner
phase3_runner.load_and_validate_manifest(sys.argv[1], project_root=".")
print("MANIFEST_AND_ANCHOR_CARRY_OK")
' "$MANIFEST"
}

run_polarity_gate() {
  # scripts/phase3_polarity_verify.py, run twice against the SAME $RESULTS_FILE: once over every
  # judged row (must be 100% mirrored, zero duplicated/other-shape, zero incomplete/inconsistent
  # groups), and once restricted to the core b0 rows alone (must realize the frozen protocol's
  # exact 48/48 per-judge split -- decisions.launch_gates.calibration_gates_per_judge.
  # structural_mirroring_gates_zero_tolerance: "per-judge realized split exactly 48/48 on the
  # core b0 set"). The b0-only restriction is computed by
  # rejudge.phase3_orchestrator_support.condition_cell_keys/write_condition_filtered_results
  # (the verifier itself reports per-judge splits pooled across every condition, so the
  # restriction has to happen before it runs, not inside it); the pass/fail decision itself is
  # rejudge.phase3_orchestrator_support.evaluate_polarity_gate.
  local roster
  roster=$("$VENV" -c '
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(",".join(manifest["roster"]["judges"]))
' "$MANIFEST")
  local protocol_path
  protocol_path=$("$VENV" -c '
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(manifest["protocol_tracked_path"])
' "$MANIFEST")

  local full_report
  full_report=$("$VENV" scripts/phase3_polarity_verify.py --results "$RESULTS_FILE" \
    --protocol "$protocol_path" --project-root . --roster "$roster") || {
    say "STOP: polarity verifier failed to run against the full store"
    return 1
  }

  local b0_results
  b0_results="$ARCHIVE/phase3_canary_results_b0_only.jsonl"
  "$VENV" -c '
import json, sys
sys.path.insert(0, ".")
from rejudge import phase3_orchestrator_support as support
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
keys = support.condition_cell_keys(manifest, condition="b0", project_root=".")
n = support.write_condition_filtered_results(sys.argv[2], sys.argv[3], keys)
print(f"{n} core b0 row(s) filtered to {sys.argv[3]}", file=sys.stderr)
' "$MANIFEST" "$RESULTS_FILE" "$b0_results"

  local b0_report
  b0_report=$("$VENV" scripts/phase3_polarity_verify.py --results "$b0_results" \
    --protocol "$protocol_path" --project-root . --roster "$roster") || {
    say "STOP: polarity verifier failed to run against the core b0 subset"
    return 1
  }

  "$VENV" -c '
import json, sys
sys.path.insert(0, ".")
from rejudge import phase3_orchestrator_support as support
full_report, b0_report = json.loads(sys.argv[1]), json.loads(sys.argv[2])
problems = support.evaluate_polarity_gate(full_report, b0_report)
if problems:
    for p in problems:
        print("PROBLEM: " + p)
    sys.exit(1)
print("polarity gate PASS: 100% mirrored, exact 48/48 core b0 split per judge")
' "$full_report" "$b0_report" | tee -a "$LOG"
}

say "phase-3 canary orchestrator armed; $manifested_count manifested cell(s), " \
    "$remaining_count remaining, round cap $ROUND_CAP, stall threshold ${STALL_THRESHOLD_SECONDS}s"

for round in $(seq 1 "$ROUND_CAP"); do
  if ! reviewer_up; then
    say "round $round: reviewer unreachable or quota exhausted; stopping cleanly"
    exit 0
  fi

  say "round $round: review wave"
  "$VENV" "$REVIEW_DAEMON" --manifest "$MANIFEST" --authorization "$AUTH" \
    --archive "$ARCHIVE" --codex "$CODEX" --driver-module "$DRIVER_MODULE" \
    --concurrency "$REVIEW_CONCURRENCY" --max-waves "$REVIEW_MAX_WAVES" --exit-when-empty \
    2>&1 | tee -a "$LOG"
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
    "${blocklist_args[@]}" \
    >"$sup_log" 2>&1 &
  sup_pid=$!

  "$VENV" "$WATCHDOG" --results "$RESULTS_FILE" --liveness "$LIVENESS_FILE" --pid "$sup_pid" \
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

  read -r manifested_count completed_count remaining_count < <(remaining_cells)
  say "round $round complete: $completed_count / $manifested_count cells, $remaining_count " \
      "remaining (supervisor exit $sup_rc, no STOP)"
  if [ "$remaining_count" -le 0 ]; then
    say "CONVERGED: $completed_count / $manifested_count fresh cells; running post-convergence " \
        "gates (anchor-carry re-verification, polarity gate) before reporting success"

    if ! verify_manifest_and_anchor_carry; then
      say "STOP: manifest/anchor-carry re-verification FAILED after convergence -- the 288 " \
          "carried v1 anchor rows no longer match what this manifest bound; a human must review"
      exit 6
    fi
    say "anchor-carry re-verification: PASS (288 carried v1 rows still match)"

    if ! run_polarity_gate; then
      say "STOP: polarity gate FAILED after convergence -- not 100% mirrored and/or the core " \
          "b0 split is not exactly 48/48 per judge; a human must review before any relaunch"
      exit 7
    fi

    say "CONVERGED and PASSED all post-convergence gates"
    exit 0
  fi
done

say "round cap ($ROUND_CAP) reached without convergence or a STOP; stopping"
exit 1
