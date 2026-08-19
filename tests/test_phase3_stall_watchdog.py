"""Gate 7: the 30-minute stall watchdog usable from the phase-3 orchestrator's first canary
block.

The staleness logic (:func:`is_stalled`, :func:`newest_row_epoch`) and the polling loop
(:func:`watch_for_stall`) are tested with every real-world dependency (clock, sleep, liveness,
kill) injected -- no test here launches or kills a real process. :func:`kill_process_tree`'s
own real implementation is exercised against a genuine child process only on POSIX, matching
the codebase's existing ``needs_posix`` convention (the live run's actual environment; a
Windows dev host cannot exercise the process-group SIGKILL path at all).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from scripts.phase3_stall_watchdog import (
    DEFAULT_STALL_THRESHOLD_SECONDS,
    is_stalled,
    kill_process_tree,
    newest_row_epoch,
    watch_for_stall,
)

needs_posix = pytest.mark.skipif(
    os.name != "posix",
    reason="process-group SIGKILL is POSIX-only; the live run's actual environment (see "
           "canary-run-environment note) is POSIX (WSL)")


# --- newest_row_epoch / is_stalled ------------------------------------------------------------


def test_newest_row_epoch_is_none_for_a_missing_file(tmp_path):
    assert newest_row_epoch(tmp_path / "no-such-results.jsonl") is None


def test_newest_row_epoch_is_none_for_an_empty_file(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text("", encoding="utf-8")
    assert newest_row_epoch(path) is None


def test_newest_row_epoch_tracks_the_last_write(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text('{"cell_key": "a"}\n', encoding="utf-8")
    before = time.time()
    assert newest_row_epoch(path) == pytest.approx(before, abs=5)


def test_not_stalled_when_no_rows_yet_and_the_driver_just_started(tmp_path):
    now = time.time()
    assert not is_stalled(
        tmp_path / "results.jsonl", driver_started_at=now, now=now,
        stall_threshold_seconds=1800)


def test_stalled_when_no_rows_ever_appear_past_the_threshold(tmp_path):
    started = time.time() - 3600
    assert is_stalled(
        tmp_path / "results.jsonl", driver_started_at=started, now=time.time(),
        stall_threshold_seconds=1800)


def test_not_stalled_when_rows_are_still_arriving(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text('{"cell_key": "a"}\n', encoding="utf-8")
    started = time.time() - 3600           # the driver started an hour ago...
    assert not is_stalled(                 # ...but just wrote a row, so it is still alive
        path, driver_started_at=started, now=time.time(), stall_threshold_seconds=1800)


def test_stalled_when_the_newest_row_predates_the_threshold(tmp_path):
    path = tmp_path / "results.jsonl"
    path.write_text('{"cell_key": "a"}\n', encoding="utf-8")
    stale_mtime = time.time() - 3600
    os.utime(path, (stale_mtime, stale_mtime))
    assert is_stalled(
        path, driver_started_at=stale_mtime, now=time.time(), stall_threshold_seconds=1800)


def test_default_threshold_is_thirty_minutes():
    assert DEFAULT_STALL_THRESHOLD_SECONDS == 30 * 60


# --- watch_for_stall: the polling loop, every dependency injected -----------------------------


def test_watch_for_stall_returns_zero_when_the_driver_exits_on_its_own(tmp_path):
    calls = {"kill": 0}
    rc = watch_for_stall(
        tmp_path / "results.jsonl", pid=1234, driver_started_at=time.time(),
        now_fn=time.time, sleep_fn=lambda s: None, is_alive_fn=lambda pid: False,
        kill_fn=lambda pid: calls.__setitem__("kill", calls["kill"] + 1))
    assert rc == 0
    assert calls["kill"] == 0


def test_watch_for_stall_kills_and_stops_when_the_result_store_goes_stale(tmp_path, capsys):
    results = tmp_path / "results.jsonl"
    results.write_text('{"cell_key": "a"}\n', encoding="utf-8")
    stale_mtime = time.time() - 3600
    os.utime(results, (stale_mtime, stale_mtime))
    killed = []

    rc = watch_for_stall(
        results, pid=4321, driver_started_at=stale_mtime,
        stall_threshold_seconds=1800, now_fn=time.time, sleep_fn=lambda s: None,
        is_alive_fn=lambda pid: True, kill_fn=lambda pid: killed.append(pid))
    assert rc == 9
    assert killed == [4321]
    err = capsys.readouterr().err
    assert "watchdog: STOP" in err
    assert "4321" in err


def test_watch_for_stall_polls_until_progress_resumes_then_exits_clean(tmp_path):
    """A driver that is briefly slow but still alive and still writing must never be killed;
    the loop keeps polling until it exits on its own."""
    results = tmp_path / "results.jsonl"
    results.write_text('{"cell_key": "a"}\n', encoding="utf-8")
    clock = {"t": 0.0}
    alive_calls = {"n": 0}

    def _now():
        return clock["t"]

    def _sleep(seconds):
        clock["t"] += seconds

    def _is_alive(pid):
        alive_calls["n"] += 1
        return alive_calls["n"] < 3          # exits on the 3rd poll

    killed = []
    rc = watch_for_stall(
        results, pid=1, driver_started_at=0.0, stall_threshold_seconds=1800,
        poll_interval_seconds=10.0, now_fn=_now, sleep_fn=_sleep, is_alive_fn=_is_alive,
        kill_fn=lambda pid: killed.append(pid))
    assert rc == 0
    assert killed == []
    assert alive_calls["n"] == 3


# --- kill_process_tree: the real implementation, against a genuine child process -------------


@needs_posix
def test_kill_process_tree_terminates_a_real_child_process():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
    try:
        deadline = time.time() + 5
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert proc.poll() is None, "the child exited before the kill under test"
        kill_process_tree(proc.pid)
        assert proc.wait(timeout=5) is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_kill_process_tree_is_a_noop_on_an_already_dead_pid():
    # A pid this large cannot correspond to a real process on either platform; the kill must
    # not raise just because its target is already gone.
    kill_process_tree(2**30 - 1)


def test_liveness_file_activity_prevents_a_stall_verdict(tmp_path):
    """2026-08-19 round-3 false kill: a healthy driver's provider calls advance the usage
    ledger while paused-for-review cells leave the result store quiet. Ledger activity must
    count as forward progress."""
    import time
    from scripts.phase3_stall_watchdog import is_stalled

    results = tmp_path / "results.jsonl"
    results.write_text("row\n", encoding="utf-8")
    ledger = tmp_path / "usage.jsonl"
    ledger.write_text("event\n", encoding="utf-8")
    now = time.time()
    old = now - 4000
    import os
    os.utime(results, (old, old))     # results quiet for 4000s
    os.utime(ledger, (now - 5, now - 5))   # ledger active seconds ago

    assert is_stalled(results, driver_started_at=old, now=now,
                      stall_threshold_seconds=1800) is True
    assert is_stalled(results, driver_started_at=old, now=now,
                      stall_threshold_seconds=1800, liveness_paths=[ledger]) is False
