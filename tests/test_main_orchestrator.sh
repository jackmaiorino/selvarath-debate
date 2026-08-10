#!/usr/bin/env bash
# Self-test for scripts/main_orchestrator.sh.
#
# The orchestrator's whole reason for existing is that it must never relaunch the
# supervisor after a STOP, so this drives it end to end against stub codex/review-daemon/
# supervisor substitutes (all script paths are environment-overridable in the orchestrator
# itself) and checks the exit code and log for each case. No real pipeline, network, or
# E:\ archive is touched: everything lives under a fresh mktemp directory per case.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCH="$HERE/../scripts/main_orchestrator.sh"

# Any python3 on PATH stands in for the phase2 venv interpreter; the stub scripts are
# stdlib-only, so no project environment is required.
STUB_PY="$(command -v python3 || command -v python)"
if [ -z "$STUB_PY" ]; then
  echo "FAIL: no python interpreter found on PATH; cannot run stub-based self-test"
  exit 1
fi

pass=0
fail=0

note() { echo "  $*"; }
ok()   { pass=$((pass + 1)); echo "PASS: $*"; }
bad()  { fail=$((fail + 1)); echo "FAIL: $*"; }

# Everything a case needs, freshly made per case: a fake REPO to cd into, a fake ARCHIVE
# for the log and results file, and a fake CODEX that always answers the quota probe OK.
new_case_dir() {
  local d
  d=$(mktemp -d)
  mkdir -p "$d/repo" "$d/archive"

  cat > "$d/codex_up" <<'EOF'
#!/usr/bin/env bash
# Stands in for the codex CLI's reviewer-quota probe: reads the prompt off stdin and
# discards it, then answers exactly what a live reviewer would.
cat >/dev/null
echo "OK"
EOF
  chmod +x "$d/codex_up"

  cat > "$d/codex_down" <<'EOF'
#!/usr/bin/env bash
cat >/dev/null
echo "usage limit reached; try again later"
EOF
  chmod +x "$d/codex_down"

  echo "$d"
}

run_orchestrator() {
  # Runs the orchestrator with $ORCH_ENV already exported by the caller; captures combined
  # output and exit code without letting a nonzero exit abort this test script.
  local d="$1"
  OUT=$("$ORCH" 2>&1)
  RC=$?
  LOGFILE="$d/archive/orchestrator.log"
}

# ---------------------------------------------------------------------------
# Case 1: supervisor STOPs (uncertain spend over ceiling). Orchestrator must exit 2
# after exactly one round, and must not print a "round 2" line anywhere.
# ---------------------------------------------------------------------------
case1() {
  local d; d=$(new_case_dir)

  cat > "$d/review_daemon_noop.py" <<'EOF'
import sys
print("[wave 1] 0 undecided payload(s)")
sys.exit(0)
EOF

  cat > "$d/supervisor_stop.py" <<'EOF'
import sys
print("supervisor: attempt 1 starting")
print("driver: halted_reason=UnknownChargeHalt")
print("supervisor: STOP uncertain spend $4.937 exceeds ceiling")
sys.exit(7)
EOF

  export REPO="$d/repo" ARCHIVE="$d/archive" LOG="$d/archive/orchestrator.log"
  export MANIFEST="fake_manifest.json" AUTH="fake_auth.json" TOTAL_CELLS=5 ROUND_CAP=5
  export VENV="$STUB_PY" CODEX="$d/codex_up"
  export REVIEW_DAEMON="$d/review_daemon_noop.py" SUPERVISOR="$d/supervisor_stop.py"

  run_orchestrator "$d"

  if [ "$RC" = "2" ]; then ok "case1: exits 2 on supervisor STOP"; else bad "case1: expected exit 2, got $RC"; note "$OUT"; fi
  if grep -q "supervisor: STOP uncertain spend \$4.937 exceeds ceiling" <<<"$OUT"; then
    ok "case1: logs the exact STOP line"
  else
    bad "case1: STOP line missing from output"; note "$OUT"
  fi
  if grep -q "TERMINAL: supervisor refused to continue; a human or the session orchestrator must review before any relaunch" <<<"$OUT"; then
    ok "case1: logs the required TERMINAL message"
  else
    bad "case1: TERMINAL message missing"; note "$OUT"
  fi
  if grep -q "round 2" <<<"$OUT"; then
    bad "case1: a second round ran after a STOP"; note "$OUT"
  else
    ok "case1: no second round after STOP"
  fi
  if [ -f "$LOGFILE" ] && grep -q "supervisor: STOP" "$LOGFILE"; then
    ok "case1: STOP line landed in the log file, not just stdout"
  else
    bad "case1: log file missing the STOP line"
  fi

  unset REPO ARCHIVE LOG MANIFEST AUTH TOTAL_CELLS ROUND_CAP VENV CODEX REVIEW_DAEMON SUPERVISOR
  rm -rf "$d"
}

# ---------------------------------------------------------------------------
# Case 2: clean supervisor output, results file reaches TOTAL_CELLS on the first round.
# Orchestrator must exit 0 and log CONVERGED.
# ---------------------------------------------------------------------------
case2() {
  local d; d=$(new_case_dir)

  cat > "$d/review_daemon_noop.py" <<'EOF'
import sys
print("[wave 1] 0 undecided payload(s)")
sys.exit(0)
EOF

  # Writes TOTAL_CELLS result rows and prints only clean supervisor lines (no "STOP").
  cat > "$d/supervisor_converge.py" <<'EOF'
import os
import sys
archive = sys.argv[4]
total = int(os.environ.get("TOTAL_CELLS", "5"))
with open(os.path.join(archive, "main_results.jsonl"), "a", encoding="utf-8") as fh:
    for i in range(total):
        fh.write('{"cell_key": "c%d"}\n' % i)
print("supervisor: attempt 1 starting")
print("supervisor: converged or worklist exported; done")
sys.exit(0)
EOF

  export REPO="$d/repo" ARCHIVE="$d/archive" LOG="$d/archive/orchestrator.log"
  export MANIFEST="fake_manifest.json" AUTH="fake_auth.json" TOTAL_CELLS=5 ROUND_CAP=5
  export VENV="$STUB_PY" CODEX="$d/codex_up"
  export REVIEW_DAEMON="$d/review_daemon_noop.py" SUPERVISOR="$d/supervisor_converge.py"

  run_orchestrator "$d"

  if [ "$RC" = "0" ]; then ok "case2: exits 0 on convergence"; else bad "case2: expected exit 0, got $RC"; note "$OUT"; fi
  if grep -q "CONVERGED" <<<"$OUT"; then ok "case2: logs CONVERGED"; else bad "case2: CONVERGED missing"; note "$OUT"; fi
  if grep -q "supervisor: STOP" <<<"$OUT"; then bad "case2: unexpected STOP text in clean output"; fi

  unset REPO ARCHIVE LOG MANIFEST AUTH TOTAL_CELLS ROUND_CAP VENV CODEX REVIEW_DAEMON SUPERVISOR
  rm -rf "$d"
}

# ---------------------------------------------------------------------------
# Case 3: review daemon fails (exit 4, how review_daemon.py wraps every ABORT, including
# an unreachable reviewer). Orchestrator must stop with exit 3 without ever invoking the
# supervisor.
# ---------------------------------------------------------------------------
case3() {
  local d; d=$(new_case_dir)

  cat > "$d/review_daemon_unreachable.py" <<'EOF'
import sys
print("[wave 1] 3 undecided payload(s)")
print("ABORT: reviewer unreachable (timeout after 600s); 0 ruling(s) written")
sys.exit(4)
EOF

  # If this is ever invoked, the case must fail: writes a marker file so the test can check.
  cat > "$d/supervisor_should_not_run.py" <<EOF
import sys
open(r"$d/supervisor_was_called", "w").close()
print("supervisor: attempt 1 starting")
print("supervisor: converged or worklist exported; done")
sys.exit(0)
EOF

  export REPO="$d/repo" ARCHIVE="$d/archive" LOG="$d/archive/orchestrator.log"
  export MANIFEST="fake_manifest.json" AUTH="fake_auth.json" TOTAL_CELLS=5 ROUND_CAP=5
  export VENV="$STUB_PY" CODEX="$d/codex_up"
  export REVIEW_DAEMON="$d/review_daemon_unreachable.py" SUPERVISOR="$d/supervisor_should_not_run.py"

  run_orchestrator "$d"

  if [ "$RC" = "3" ]; then ok "case3: exits 3 when review wave fails"; else bad "case3: expected exit 3, got $RC"; note "$OUT"; fi
  if grep -q "review wave failed (exit 4)" <<<"$OUT"; then
    ok "case3: logs the wave failure with its exit code"
  else
    bad "case3: wave failure not logged"; note "$OUT"
  fi
  if [ -f "$d/supervisor_was_called" ]; then
    bad "case3: supervisor ran even though the review wave failed"
  else
    ok "case3: supervisor never invoked after a failed review wave"
  fi

  unset REPO ARCHIVE LOG MANIFEST AUTH TOTAL_CELLS ROUND_CAP VENV CODEX REVIEW_DAEMON SUPERVISOR
  rm -rf "$d"
}

# ---------------------------------------------------------------------------
# Case 4 (bonus): reviewer quota is down at the top of the round. Orchestrator must stop
# cleanly (exit 0) without invoking either the review daemon or the supervisor.
# ---------------------------------------------------------------------------
case4() {
  local d; d=$(new_case_dir)

  cat > "$d/review_daemon_should_not_run.py" <<EOF
import sys
open(r"$d/review_daemon_was_called", "w").close()
sys.exit(0)
EOF
  cat > "$d/supervisor_should_not_run.py" <<EOF
import sys
open(r"$d/supervisor_was_called", "w").close()
sys.exit(0)
EOF

  export REPO="$d/repo" ARCHIVE="$d/archive" LOG="$d/archive/orchestrator.log"
  export MANIFEST="fake_manifest.json" AUTH="fake_auth.json" TOTAL_CELLS=5 ROUND_CAP=5
  export VENV="$STUB_PY" CODEX="$d/codex_down"
  export REVIEW_DAEMON="$d/review_daemon_should_not_run.py" SUPERVISOR="$d/supervisor_should_not_run.py"

  run_orchestrator "$d"

  if [ "$RC" = "0" ]; then ok "case4: exits 0 when reviewer quota is down"; else bad "case4: expected exit 0, got $RC"; note "$OUT"; fi
  if [ -f "$d/review_daemon_was_called" ] || [ -f "$d/supervisor_was_called" ]; then
    bad "case4: something ran despite a down reviewer probe"
  else
    ok "case4: neither review daemon nor supervisor ran"
  fi

  unset REPO ARCHIVE LOG MANIFEST AUTH TOTAL_CELLS ROUND_CAP VENV CODEX REVIEW_DAEMON SUPERVISOR
  rm -rf "$d"
}

echo "== main_orchestrator.sh self-test =="
case1
case2
case3
case4

echo
echo "== summary: $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
