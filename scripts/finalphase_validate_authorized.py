"""Finite supervised chain: guarded qualification, then only this validation cohort."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from finalphase import cli, validation as V


def main() -> None:
    root = cli.RUN_ROOT
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    if len(manifest["validation_execution"]["worlds_sha256"]) != 8:
        raise RuntimeError("this chain is authorized only for the existing eight worlds")
    prefix = [sys.executable, "-m", "finalphase.cli"]
    V.execution_status(root, "qualification_starting", supervisor_pid=os.getpid())
    try:
        subprocess.run(prefix + ["qualify-validate", "--mode", "batch"], cwd=REPO, check=True)
        receipt = json.loads((root / "validation_qualification.json").read_text(encoding="utf-8"))
        workers = str(receipt["selected_workers"])
        subprocess.run(prefix + ["preflight", "--stage", "validate", "--workers", workers, "--mode", "batch"], cwd=REPO, check=True)
        subprocess.run(prefix + ["validate", "--workers", workers, "--mode", "batch"], cwd=REPO, check=True)
    except BaseException as error:
        current = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))["validation_execution"]
        V.execution_status(root, current["status"] if "attention" in current["status"] else "validation_needs_attention",
                           supervisor_error=f"{type(error).__name__}: {error}", supervisor_exited=True)
        raise
    V.execution_status(root, "validation_complete", supervisor_exited=True,
                       next_action="Unpaid audit packet awaits the Claude coordinating reviewer; later execution is unauthorized")


if __name__ == "__main__":
    main()
