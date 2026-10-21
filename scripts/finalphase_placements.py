"""Read-only placement inventory for the final-phase pilot (COMPUTE-POLICY item 1).

Records Jack's PC (GPUs, free disk, Python processes), HaleysPC (a strict, batch-mode SSH probe that
changes no trust settings) and RunPod (an authenticated pod listing; nothing is started). Writes
<run root>/pilot_placements_<UTC>.json. The pilot is provider-hosted inference, so the local collector
runs on this host; the record states that and what was observed.

    uv run --locked python scripts/finalphase_placements.py
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("FINALPHASE_ROOT", "D:/finalphase-runs/final-phase-2026-10-03"))


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)


def main() -> None:
    now = datetime.now(timezone.utc)
    rc, gpus = _run(["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader"])
    rc_py, procs = _run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/NH"]) if os.name == "nt" else _run(["pgrep", "-c", "python"])
    jack = {"status": "Available", "gpu_inventory": gpus.splitlines() if rc == 0 else [f"nvidia-smi failed: {gpus[:200]}"],
            "drive_free_bytes": {d: shutil.disk_usage(f"{d}:/").free for d in ("C", "D", "E") if Path(f"{d}:/").exists()},
            "python_processes": sum("python" in line.lower() for line in procs.splitlines()) if rc_py == 0 else None,
            "gpu_use": "Provider-hosted inference; no local GPU requested"}
    rc_h, out_h = _run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
                        "haleyspc", "exit"], timeout=30)
    haley = {"status": "Reachable through existing trusted SSH" if rc_h == 0 else "Unavailable through existing trusted SSH",
             "result": out_h[:300] or f"exit {rc_h}", "trust_changed": False}
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if key:
        try:
            req = urllib.request.Request("https://rest.runpod.io/v1/pods", headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                pods = json.loads(r.read().decode())
            pods = pods if isinstance(pods, list) else pods.get("pods", [])
            runpod = {"status": "Authenticated inventory read (GET /v1/pods, 200)", "pods": len(pods),
                      "running": sum(1 for p in pods if str(p.get("desiredStatus", "")).upper() == "RUNNING"),
                      "resources": "No pod started"}
        except Exception as e:  # noqa: BLE001 - recorded, not raised
            runpod = {"status": f"Inventory read failed: {type(e).__name__}", "detail": str(e)[:200], "resources": "No pod started"}
    else:
        runpod = {"status": "No RUNPOD_API_KEY in this process; inventory not read", "resources": "No pod started"}
    record = {"checked_utc": now.isoformat(), "host": platform.node(), "mutations": [], "trust_changed": False, "pods_started": 0,
              "Jack's PC": jack, "HaleysPC": haley, "RunPod": runpod,
              "selected": "Jack's PC as local collector: remote provider inference is the bottleneck, so no other placement "
                          "shortens completion; subject to the pilot's serial/parallel qualification"}
    path = ROOT / f"pilot_placements_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    print(path)
    print(json.dumps(record, indent=1))


if __name__ == "__main__":
    sys.exit(main())
