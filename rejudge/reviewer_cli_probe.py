"""Bounded local CLI version probes, with owned-process cleanup before retry."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

PROBE_TIMEOUT_SECONDS = 60
PROBE_RETRY_DELAYS_SECONDS = (2, 5)
_CLEANUP_TIMEOUT_SECONDS = 5


class ReviewerCliProbeError(ValueError):
    """The local CLI could not provide a version within the bounded policy."""


class _ProbeCleanupError(ReviewerCliProbeError):
    """Process cleanup was not confirmed, so another probe must not start."""


class _WindowsJob:
    """Contain the probe before its suspended initial process can execute."""

    def __init__(self) -> None:
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class BasicAccounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        self._accounting_type = BasicAccounting
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._nt = ctypes.WinDLL("ntdll", use_last_error=True)
        self._kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self._kernel.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel.TerminateJobObject.restype = wintypes.BOOL
        self._kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.c_void_p]
        self._kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self._nt.NtResumeProcess.restype = ctypes.c_long
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if not self._kernel.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        status = self._nt.NtResumeProcess(int(process._handle))
        if status < 0:
            raise OSError(f"NtResumeProcess failed with status 0x{status & 0xffffffff:08x}")

    def terminate_and_wait(self) -> None:
        if not self._kernel.TerminateJobObject(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + _CLEANUP_TIMEOUT_SECONDS
        while True:
            accounting = self._accounting_type()
            if not self._kernel.QueryInformationJobObject(
                self._handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.ActiveProcesses == 0:
                return
            if time.monotonic() >= deadline:
                raise _ProbeCleanupError("reviewer CLI probe job cleanup timed out")
            time.sleep(0.01)

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def _run_probe(
    argv: Sequence[str], *, env: Mapping[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    """Run one local probe; inherited output handles never extend the deadline."""
    job = _WindowsJob() if os.name == "nt" else None
    process = None
    assigned = False
    try:
        # Descendants may inherit these files, but cannot keep communicate() waiting.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                kwargs = (
                    {"creationflags": 0x00000004 | subprocess.CREATE_NO_WINDOW}
                    if job is not None else {"start_new_session": True}
                )
                process = subprocess.Popen(
                    list(argv), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    env=dict(env), **kwargs,
                )
                if job is not None:
                    job.assign(process)
                    assigned = True
                    job.resume(process)
                returncode = process.wait(timeout=timeout)
            finally:
                if process is not None:
                    try:
                        if job is not None and assigned:
                            job.terminate_and_wait()
                        elif job is not None:
                            # Assignment failed. This exact child is still suspended.
                            process.kill()
                        else:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        process.wait(timeout=_CLEANUP_TIMEOUT_SECONDS)
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise _ProbeCleanupError(
                            f"reviewer CLI probe cleanup not confirmed: {type(exc).__name__}"
                        ) from exc
            stdout.seek(0)
            stderr.seek(0)
            return subprocess.CompletedProcess(
                list(argv), returncode,
                stdout.read(8192).decode("utf-8", errors="replace"),
                stderr.read(8192).decode("utf-8", errors="replace"),
            )
    finally:
        if job is not None:
            job.close()


def read_reviewer_cli_version(
    cli: str | Path, *, env: Mapping[str, str] | None = None
) -> str:
    """Retry local spawn/timeouts only; callers still compare the exact version."""
    probe_env = dict(os.environ if env is None else env)
    probe_env = {key: value for key, value in probe_env.items()
                 if key.upper() != "TOGETHER_API_KEY"}
    failures = []
    for attempt in range(len(PROBE_RETRY_DELAYS_SECONDS) + 1):
        try:
            completed = _run_probe(
                [str(cli), "--version"], env=probe_env, timeout=PROBE_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                detail = f"TimeoutExpired after {exc.timeout:g}s"
            else:
                detail = f"OSError: {str(exc)[:240]}"
            failures.append(f"attempt {attempt + 1}: {detail}")
            if attempt == len(PROBE_RETRY_DELAYS_SECONDS):
                raise ReviewerCliProbeError(
                    "reviewer CLI version probe failed; " + "; ".join(failures)
                ) from exc
            time.sleep(PROBE_RETRY_DELAYS_SECONDS[attempt])
            continue
        version = completed.stdout.strip() or completed.stderr.strip()
        if completed.returncode != 0 or not version:
            raise ReviewerCliProbeError(
                "reviewer CLI version probe did not return a version "
                f"(exit {completed.returncode}, attempt {attempt + 1})"
                + ("; " + "; ".join(failures) if failures else "")
            )
        # Do not retry a successful but wrong version into an apparently valid result.
        return version
    raise AssertionError("unreachable version probe retry state")
