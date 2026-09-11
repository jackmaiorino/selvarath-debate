from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from rejudge import reviewer_cli_probe as probe


def test_transient_timeout_then_success_preserves_argv_env_and_deadline(monkeypatch):
    calls = []
    delays = []

    def run(argv, *, env, timeout):
        calls.append((argv, env, timeout))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, 0, "codex-cli pinned\n", "")

    monkeypatch.setattr(probe, "_run_probe", run)
    monkeypatch.setattr(probe.time, "sleep", delays.append)
    supplied_env = {"PATH": "unchanged", "TOGETHER_API_KEY": "test-only"}
    assert probe.read_reviewer_cli_version("C:/pinned/codex.cmd", env=supplied_env) == "codex-cli pinned"
    assert calls == [(["C:/pinned/codex.cmd", "--version"], {"PATH": "unchanged"}, 60)] * 2
    assert delays == [2]
    assert supplied_env["TOGETHER_API_KEY"] == "test-only"


def test_persistent_environment_failure_has_three_attempts_and_all_causes(monkeypatch):
    calls = []
    delays = []

    def run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 2:
            raise OSError("fixture spawn unavailable")
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(probe, "_run_probe", run)
    monkeypatch.setattr(probe.time, "sleep", delays.append)
    with pytest.raises(probe.ReviewerCliProbeError) as caught:
        probe.read_reviewer_cli_version("pinned", env={})
    assert len(calls) == 3
    assert delays == [2, 5]
    message = str(caught.value)
    assert "attempt 1: TimeoutExpired after 60s" in message
    assert "attempt 2: OSError: fixture spawn unavailable" in message
    assert "attempt 3: TimeoutExpired after 60s" in message
    assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)


@pytest.mark.parametrize("stdout,stderr", [
    ("codex-cli WRONG", ""), ("", "codex-cli WRONG"),
    ("codex-cli WRONG", "codex-cli pinned"),
])
def test_wrong_returned_version_is_not_retried(monkeypatch, stdout, stderr):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout, stderr)

    monkeypatch.setattr(probe, "_run_probe", run)
    assert probe.read_reviewer_cli_version("pinned", env={}) == "codex-cli WRONG"
    assert len(calls) == 1


@pytest.mark.parametrize("returncode,stdout", [(0, ""), (1, "codex-cli pinned")])
def test_empty_or_nonzero_result_fails_without_retry(monkeypatch, returncode, stdout):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr(probe, "_run_probe", run)
    with pytest.raises(probe.ReviewerCliProbeError, match="did not return a version"):
        probe.read_reviewer_cli_version("pinned", env={})
    assert len(calls) == 1


def test_unconfirmed_cleanup_never_retries(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        raise probe._ProbeCleanupError("fixture cleanup unconfirmed")

    monkeypatch.setattr(probe, "_run_probe", run)
    with pytest.raises(probe.ReviewerCliProbeError, match="cleanup unconfirmed"):
        probe.read_reviewer_cli_version("pinned", env={})
    assert len(calls) == 1


@pytest.mark.parametrize("use_cmd_wrapper", [False, pytest.param(
    True, marks=pytest.mark.skipif(os.name != "nt", reason="Windows command wrapper"))])
def test_real_local_probe_captures_output(tmp_path, use_cmd_wrapper):
    argv = [sys.executable, "-B", "-c", "print('local fixture version')"]
    if use_cmd_wrapper:
        wrapper = tmp_path / "local version fixture.cmd"
        wrapper.write_text(
            f'@"{sys.executable}" -B -c "print(\'local fixture version\')"\n',
            encoding="utf-8",
        )
        argv = [str(wrapper), "--version"]
    result = probe._run_probe(
        argv,
        env=dict(os.environ), timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "local fixture version"
    assert result.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows job containment")
@pytest.mark.parametrize("failure_stage", ["spawn", "assign", "resume"])
def test_windows_failure_before_run_never_leaves_an_uncontained_child(monkeypatch, failure_stage):
    events = []

    class Job:
        def assign(self, process):
            events.append("assign")
            if failure_stage == "assign":
                raise OSError("fixture assignment failure")

        def resume(self, process):
            events.append("resume")
            raise OSError("fixture resume failure")

        def terminate_and_wait(self):
            events.append("terminate_job")

        def close(self):
            events.append("close_job")

    class Process:
        def __init__(self, argv, **kwargs):
            events.append("spawn")
            assert kwargs["creationflags"] & 0x00000004
            assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
            if failure_stage == "spawn":
                raise OSError("fixture spawn failure")

        def kill(self):
            events.append("kill_suspended_child")

        def wait(self, **kwargs):
            events.append("wait_child")
            return 1

    monkeypatch.setattr(probe, "_WindowsJob", Job)
    monkeypatch.setattr(probe.subprocess, "Popen", Process)
    with pytest.raises(OSError, match=f"fixture {failure_stage}"):
        probe._run_probe(["fixture"], env={}, timeout=1)
    expected = {
        "spawn": ["spawn", "close_job"],
        "assign": ["spawn", "assign", "kill_suspended_child", "wait_child", "close_job"],
        "resume": ["spawn", "assign", "resume", "terminate_job", "wait_child", "close_job"],
    }
    assert events == expected[failure_stage]


@pytest.mark.skipif(os.name != "nt", reason="Windows job containment")
def test_real_windows_timeout_terminates_owned_descendants(tmp_path: Path):
    from ctypes import wintypes

    pid_file = tmp_path / "pids.json"
    fixture = tmp_path / "spawn_child.py"
    fixture.write_text(
        "import json,os,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(60)'])\n"
        "with open(sys.argv[1],'w') as f: json.dump([os.getpid(),child.pid],f)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    failures = []

    def run():
        try:
            probe._run_probe(
                [sys.executable, "-B", str(fixture), str(pid_file)],
                env=dict(os.environ), timeout=4,
            )
        except BaseException as exc:
            failures.append(exc)

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handles = []
    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        pids = None
        while time.monotonic() < deadline:
            try:
                pids = json.loads(pid_file.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError):
                time.sleep(0.01)
        assert pids is not None, "local fixture did not start before its probe deadline"
        for pid in pids:
            handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
            assert handle, ctypes.get_last_error()
            handles.append(handle)
        thread.join(timeout=12)
        assert not thread.is_alive()
        assert len(failures) == 1
        assert isinstance(failures[0], subprocess.TimeoutExpired)
        # Open handles bind exact process instances, avoiding PID reuse ambiguity.
        # Windows can finish final process signaling just after job active count is zero.
        assert all(kernel.WaitForSingleObject(handle, 1000) == 0 for handle in handles)
    finally:
        thread.join(timeout=12)
        for handle in handles:
            kernel.CloseHandle(handle)
