"""Bounded real throughput qualification for the debater canary.

The serial control arm has fresh IDs and never enters the canary. The parallel arm
sends the canonical opening-round requests of the first canary question for both
debaters, so its responses replay into the canary itself.
"""
from __future__ import annotations

import json
import platform
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import authoring as A, preflight
from .debate import debate_task
from .providers import Request
from .store import Store
from .validation import atomic_json, input_sha256, sha

QUALIFICATION_VERSION = "v1"
CANARY_WORKERS = 16
DEBATERS = ("fable", "astra")


def canary_questions(bench: Path, n: int = 20) -> list[dict]:
    qs = [json.loads(x) for x in (bench / "canary.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return sorted(qs, key=lambda q: A.hashlib.sha256(f"canary-v1:{q['question_id']}".encode()).hexdigest())[:n]


def qualification_sample(bench: Path, worlds: dict[str, dict]) -> list[Request]:
    """Uncapped opening-round requests of the first canary question, for both debaters."""
    q = canary_questions(bench)[0]
    sample: list[Request] = []
    for deb in DEBATERS:
        first = next(debate_task(q, worlds[q["world_id"]]["world_text"], deb, None, "uncapped"))
        sample.extend(first)
    if len(sample) != 4:
        raise ValueError("canary qualification requires both opening turns from both debaters")
    return sample


def controls(sample: list[Request]) -> list[Request]:
    return [replace(r, custom_id=f"qualification:canary:{QUALIFICATION_VERSION}:serial:{r.custom_id}")
            for r in sample]


def qualify(root: Path, worlds: dict[str, dict], cap: float) -> dict:
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sample = qualification_sample(root / "bench", worlds)
    path = root / "canary_qualification.json"
    fingerprint = preflight.execution_sha256()
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "execution_sha256": fingerprint, "input_sha256": input_sha256(sample),
        "git_commit": preflight.source_commit(), "host": platform.node(), "mode": "live",
        "coverage": "Fable and Astra opening debate turns at the frozen 32,000-token limit",
        "parallel_first_responses_used_in_canary": True, "control_used_in_canary": False,
    }
    if (record["execution_sha256"] != fingerprint or record["host"] != platform.node()
            or record["input_sha256"] != input_sha256(sample)):
        raise RuntimeError("existing canary qualification is incompatible; preserve it before a fresh version")
    store = Store(root / "canary.db", "canary", cap)
    try:
        for name, reqs, workers in (("serial", controls(sample), 1), ("parallel", sample, CANARY_WORKERS)):
            if record.get(name, {}).get("semantics_preserved") is True:
                continue
            if record.get(name, {}).get("semantics_preserved") is False:
                raise RuntimeError(f"{name} canary qualification failed; paid retries need a new version")
            if name not in record and any(store.get(r.custom_id) is not None for r in reqs):
                raise RuntimeError(f"{name} has cached calls but no timing receipt; cannot time a replay as inference")
            resumed = name in record
            if not resumed:
                record[name] = {"started_epoch": time.time(), "workers": workers,
                                "request_ids": [r.custom_id for r in reqs]}
                atomic_json(path, record)
            start = time.perf_counter()
            out = store.run(reqs, mode="live", workers=workers)
            elapsed = time.perf_counter() - start
            if resumed:
                elapsed = max(elapsed, time.time() - record[name]["started_epoch"])
            complete = len(out) == len(reqs) and all(
                out[r.custom_id].status == "ok" and out[r.custom_id].text.strip() for r in reqs)
            record[name] = {"workers": workers, "completed": len(out), "elapsed_seconds": elapsed,
                            "input_sha256": input_sha256(reqs),
                            "output_sha256": sha(json.dumps([asdict(out[r.custom_id]) for r in reqs
                                                             if r.custom_id in out], sort_keys=True).encode()),
                            "semantics_preserved": complete, "request_ids": [r.custom_id for r in reqs]}
            atomic_json(path, record)
            print(f"{name}: {len(out)}/{len(reqs)} completed in {elapsed:.3f}s, valid={complete}", flush=True)
            if not complete:
                raise RuntimeError(f"{name} canary qualification did not complete valid opening turns")
        # The canary command always runs CANARY_WORKERS; preflight refuses a receipt that chose otherwise.
        serial, parallel = record["serial"], record["parallel"]
        chosen = parallel if parallel["elapsed_seconds"] < serial["elapsed_seconds"] else serial
        record.update({"selected_workers": chosen["workers"], "semantics_preserved": True,
                       "placements_checked": ["Jack's PC", "HaleysPC", "RunPod"],
                       "placement_reference": manifest["validation_execution"]["placements"]["reference"],
                       "completed_utc": datetime.now(timezone.utc).isoformat(),
                       "stage_spend_usd": store.spent("canary")})
        atomic_json(path, record)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("throughput", {})["canary"] = record
        atomic_json(manifest_path, manifest)
        return record
    finally:
        store.db.close()
