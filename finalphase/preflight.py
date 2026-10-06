"""Read-only checks used by the supported final-phase stage launcher."""
from __future__ import annotations

import json
import hashlib
import math
import os
import platform
import subprocess
from pathlib import Path
from typing import TypeGuard

PROVIDER_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "TOGETHER_API_KEY")
CEILING_USD = 6000.0


def source_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True
    ).strip()


def execution_sha256() -> str:
    """Allow compatible qualification reuse across documentation-only commits."""
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / "finalphase").glob("*.py")) + [root / "pyproject.toml", root / "uv.lock"]
    hashes = {str(p.relative_to(root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in paths}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def _positive(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and type(value) in (int, float) and math.isfinite(value) and value > 0


def check(stage: str, root: Path, workers: int, mode: str, quality_check: bool = False,
          qualification: bool = False) -> dict:
    reasons: list[str] = []
    if (root / "STOP").exists() or (Path(__file__).resolve().parents[1] / "STOP").exists():
        reasons.append("STOP file is present")
    credentials = {name: bool(os.environ.get(name, "").strip()) for name in PROVIDER_KEYS}
    reasons.extend(f"missing {name}" for name, configured in credentials.items() if not configured)
    path = root / "run_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
    except (OSError, ValueError):
        manifest = {}
        reasons.append(f"missing or invalid run manifest: {path}")
    commit = source_commit()
    if manifest.get("git_commit") != commit:
        reasons.append("run manifest must name the current source commit")
    checks = manifest.get("provider_checks", {})
    if not isinstance(checks, dict):
        checks = {}
    if any(checks.get(provider) != "passed" for provider in ("anthropic", "openai", "together")):
        reasons.append("provider access and billing smoke checks have not all passed")
    funding = manifest.get("funding", {})
    if not isinstance(funding, dict):
        funding = {}
    available = funding.get("available_usd")
    if stage == "main" and (funding.get("reconciled") is not True or not _positive(available)
            or available < CEILING_USD or not funding.get("reference")):
        reasons.append("reconciled funding for the $6,000 allocation is missing")
    if stage == "main":
        closure = manifest.get("before_main", {})
        if not isinstance(closure, dict):
            closure = {}
        if any(closure.get(name) is not True for name in
               ("canary_passed", "oracle_qualified", "preregistered", "pilot_forecast_fits")):
            reasons.append("canary, oracle, pre-registration and measured pilot forecast must pass before main")
    if manifest.get("ceiling_usd") != CEILING_USD:
        reasons.append("run manifest must preserve the $6,000 ceiling")
    if stage in ("canary", "pilot", "main") or (stage == "author" and not quality_check):
        from .expansion import audit_reasons
        reasons.extend(audit_reasons(root))
    if stage == "author" and not quality_check:
        from .expansion import author_plan
        try:
            approved = manifest.get("author_expansion_authorization", {})
            reasons.extend(author_plan(root, approved.get("worlds", 160), approved.get("attempts", 2), mode)["reasons"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            reasons.append(f"expanded author reservation cannot be verified: {error}")
    if qualification:
        if stage != "validate" or quality_check:
            reasons.append("bounded throughput qualification is only available for validation")
    elif not quality_check:
        receipts = manifest.get("throughput", {})
        receipt = receipts.get(stage, {}) if isinstance(receipts, dict) else {}
        if not isinstance(receipt, dict):
            receipt = {}
        serial, parallel = receipt.get("serial", {}), receipt.get("parallel", {})
        if not isinstance(serial, dict):
            serial = {}
        if not isinstance(parallel, dict):
            parallel = {}
        compatible = (receipt.get("execution_sha256") == execution_sha256()
                      if "execution_sha256" in receipt else receipt.get("git_commit") == commit)
        valid = (compatible
                 and receipt.get("host") == platform.node()
                 and receipt.get("mode") == mode
                 and receipt.get("selected_workers") == workers
                 and receipt.get("semantics_preserved") is True
                 and receipt.get("placements_checked") == ["Jack's PC", "HaleysPC", "RunPod"]
                 and serial.get("workers") == 1
                 and _positive(parallel.get("workers")) and parallel["workers"] > 1
                 and all(_positive(r.get(k)) for r in (serial, parallel)
                         for k in ("completed", "elapsed_seconds"))
                 and serial.get("input_sha256") == parallel.get("input_sha256")
                 and isinstance(serial.get("input_sha256"), str)
                 and len(serial["input_sha256"]) == 64
                 and all(isinstance(r.get("output_sha256"), str) and len(r["output_sha256"]) == 64
                         for r in (serial, parallel)))
        if not valid:
            reasons.append(f"compatible serial/parallel throughput receipt and placement checks are missing for {stage}")
    elif stage != "author":
        reasons.append("the bounded quality check is only available for authoring up to eight worlds")
    if stage == "validate":
        from . import validation
        try:
            worlds = [json.loads(p.read_text(encoding="utf-8"))
                      for p in sorted((root / "bench/worlds").glob("W*.json"))]
            reasons.extend(validation.plan(root, worlds, mode)["reasons"])
        except (OSError, ValueError, KeyError, TypeError) as e:
            reasons.append(f"validation reservation cannot be verified: {e}")
    return {"ready": not reasons, "stage": stage, "git_commit": commit,
            "manifest": str(path), "credentials_configured": credentials,
            "quality_check": quality_check, "reasons": reasons,
            "qualification": qualification,
            "provider_client_created": False, "execution_started": False}


def require(stage: str, root: Path, workers: int, mode: str, quality_check: bool = False,
            qualification: bool = False) -> None:
    report = check(stage, root, workers, mode, quality_check, qualification)
    if not report["ready"]:
        raise RuntimeError("final-phase launch refused: " + "; ".join(report["reasons"]))
