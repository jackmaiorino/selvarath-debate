"""Phase-3 orchestrator gate 7: kill a silently-wedged driver.

The 2026-08-10 five-hour ssl.read hang was invisible to every phase-2 control that already
existed: ``canary_supervisor.py`` only inspects a driver invocation's OWN outcome AFTER
``subprocess.run`` returns, and ``main_orchestrator.sh`` only greps a completed run pass's
combined output for the literal text "supervisor: STOP". Neither has any notion of "the driver
process is still alive but has produced no forward progress in N minutes" -- there is no
phase-2 equivalent of this watchdog to reuse; it does not exist anywhere in this codebase (a
survey of every ``*.py``/``*.sh`` under ``rejudge/`` and ``scripts/`` for a stall/process-tree
watchdog pattern before writing this module turned up nothing).

The rule this module enforces is exactly the one gate 7 specifies: if the result store's
newest row is older than ``DEFAULT_STALL_THRESHOLD_SECONDS`` (30 minutes) while the driver
process is still alive, kill its process tree and exit nonzero with a STOP-style message,
matching ``canary_supervisor.py``'s own "supervisor: STOP ..." convention (here: "watchdog:
STOP ..."). Staleness is measured from the result store's file mtime, not a parsed timestamp
field: every append path in this codebase flushes (and the usage ledger fsyncs) on write, so
mtime is exactly "when did the driver last make forward progress" and needs no schema
knowledge of whichever result-row shape a given stage writes.

Every dependency :func:`watch_for_stall` needs (the clock, sleep, liveness check, and the kill
itself) is injectable, so its tests never launch or kill a real process; the module-level
functions are the real, production implementations used when nothing is injected.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_STALL_THRESHOLD_SECONDS = 30 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 30.0
DEFAULT_KILL_TIMEOUT_SECONDS = 30.0


def newest_row_epoch(results_path) -> float | None:
    """Epoch seconds of the result store's newest write, or ``None`` with no rows yet."""
    path = Path(results_path)
    try:
        if path.stat().st_size == 0:
            return None
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def is_alive(pid: int) -> bool:
    """True if a process with this pid currently exists."""
    if os.name == "nt":
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=DEFAULT_KILL_TIMEOUT_SECONDS)
        return str(pid) in proc.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                       # exists, just not ours to signal
    return True


def kill_process_tree(pid: int, *, timeout: float = DEFAULT_KILL_TIMEOUT_SECONDS) -> None:
    """Force-terminate ``pid`` and every descendant.

    POSIX (the live run's actual environment; see the canary-run-environment note): the
    driver must be launched with ``start_new_session=True`` (its own process group) for this
    to reach its children -- SIGKILL the whole group. Windows (dev/CI only): ``taskkill``'s
    own ``/T`` (tree) does the descendant enumeration stdlib has no portable equivalent for.
    Silently returns if the process is already gone: killing something already dead is not a
    failure of this watchdog.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=timeout)
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def is_stalled(results_path, *, driver_started_at: float, now: float | None = None,
               stall_threshold_seconds: float = DEFAULT_STALL_THRESHOLD_SECONDS,
               liveness_paths=()) -> bool:
    """True when the driver appears to be making no forward progress.

    Measured from whichever is most recent: the result store's newest-row mtime, any extra
    liveness file's mtime, or the driver's own start time -- so a driver that has
    legitimately not finished its first cell yet is not immediately flagged just because the
    results file does not exist.

    ``liveness_paths`` exists because result rows alone misread the high-budget endgame
    (2026-08-19, round 3): a healthy driver spent over 30 minutes making continuous provider
    calls (queries, checker, oracle) on cells that then PAUSE for gate review, so the result
    store stayed quiet while the usage ledger advanced every few seconds, and the watchdog
    killed a healthy run. The usage ledger is the right liveness signal for that phase; a
    genuinely hung call still goes quiet everywhere after its reservation row, so the
    2026-08-10 silent-hang class is still caught one row later.
    """
    now = time.time() if now is None else now
    stamps = [driver_started_at]
    for path in (results_path, *liveness_paths):
        newest = newest_row_epoch(path)
        if newest is not None:
            stamps.append(newest)
    return (now - max(stamps)) > stall_threshold_seconds


def watch_for_stall(
    results_path, pid: int, *, driver_started_at: float | None = None,
    stall_threshold_seconds: float = DEFAULT_STALL_THRESHOLD_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    liveness_paths=(),
    now_fn=time.time, sleep_fn=time.sleep, is_alive_fn=is_alive, kill_fn=kill_process_tree,
) -> int:
    """Block, polling, until the driver exits on its own (return 0) or is judged stalled.

    On a stall: kill the driver's process tree, print a "watchdog: STOP ..." line to
    stderr, and return nonzero -- usable by the phase-3 orchestrator exactly as
    ``canary_supervisor.py``'s own STOP lines are: grepped for and treated as terminal.
    """
    started = driver_started_at if driver_started_at is not None else now_fn()
    while True:
        if not is_alive_fn(pid):
            return 0
        now = now_fn()
        if is_stalled(results_path, driver_started_at=started, now=now,
                      stall_threshold_seconds=stall_threshold_seconds,
                      liveness_paths=liveness_paths):
            stamps = [s for s in (newest_row_epoch(p) for p in (results_path, *liveness_paths))
                      if s is not None]
            age = (now - max(stamps)) if stamps else (now - started)
            print(f"watchdog: STOP driver pid {pid} produced no new result row or liveness "
                  f"activity for {age:.0f}s (threshold {stall_threshold_seconds:.0f}s); "
                  "killing its process tree", file=sys.stderr, flush=True)
            kill_fn(pid)
            return 9
        sleep_fn(poll_interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, help="the result store to watch for staleness")
    parser.add_argument("--pid", type=int, required=True, help="the driver process id")
    parser.add_argument("--liveness", action="append", default=[],
                        help="extra file(s) whose mtime also counts as forward progress "
                             "(e.g. the usage ledger); repeatable")
    parser.add_argument("--stall-threshold-seconds", type=float,
                        default=DEFAULT_STALL_THRESHOLD_SECONDS)
    parser.add_argument("--poll-interval-seconds", type=float,
                        default=DEFAULT_POLL_INTERVAL_SECONDS)
    args = parser.parse_args(argv)
    return watch_for_stall(
        args.results, args.pid, stall_threshold_seconds=args.stall_threshold_seconds,
        poll_interval_seconds=args.poll_interval_seconds, liveness_paths=args.liveness)


if __name__ == "__main__":
    raise SystemExit(main())
