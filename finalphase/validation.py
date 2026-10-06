"""Frozen validation workload, funding reservation and bounded real qualification.

The control arm has fresh IDs and never contributes to retention. The parallel
arm uses canonical IDs, so its first responses are reused by validation.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import sqlite3
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import authoring as A, preflight
from .models import spec
from .providers import Request, Response
from .store import MEASURED, Store, estimate_max_cost

QUALIFICATION_VERSION = "v1"
QUALIFICATION_WORKERS = 8


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_json(path: Path, obj: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def execution_status(root: Path, status: str, **fields) -> None:
    path = root / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["validation_execution"].update({"status": status,
        "updated_utc": datetime.now(timezone.utc).isoformat(), **fields})
    atomic_json(path, manifest)


def workload(worlds: list[dict]) -> tuple[list[dict], list[Request]]:
    rows: list[dict] = []
    requests: list[Request] = []
    for w in worlds:
        eligible = set()
        for i, q in enumerate(w["questions"]):
            chk = A.mechanical_check(q, w["world_text"], w["questions"])
            rows.append({"question_id": A.qid(w["world_id"], i), "world_id": w["world_id"],
                         "author": w["author"], "index": i,
                         "mechanical_ok": chk.ok, "mechanical_reasons": chk.reasons})
            if chk.ok:
                eligible.add(A.qid(w["world_id"], i))
        requests.extend(r for r in A.validation_requests(w["world_id"], w["author"],
                        w["world_text"], w["questions"]) if r.custom_id.split(":")[1] in eligible)
    return rows, requests


def qualification_sample(worlds: list[dict], requests: list[Request]) -> list[Request]:
    """Two key orders and two facts, from the first eligible question per author."""
    sample: list[Request] = []
    for author in ("fable", "astra"):
        ids = {w["world_id"] for w in worlds if w["author"] == author}
        candidates = [r for r in requests if r.model == "dspro"
                      and r.custom_id.split(":")[1].split("-Q")[0] in ids]
        if not candidates:
            raise ValueError(f"no eligible qualification question for {author}")
        qid = candidates[0].custom_id.split(":")[1]
        sample.extend(r for r in candidates if r.custom_id.startswith(f"validate:{qid}:"))
        sample.extend([r for r in candidates if r.custom_id.startswith(f"factcheck:{qid}:")][:2])
    if len(sample) != 8:
        raise ValueError("qualification requires exactly four key checks and four fact checks")
    return sample


def controls(sample: list[Request]) -> list[Request]:
    return [replace(r, custom_id=f"qualification:validate:{QUALIFICATION_VERSION}:serial:{r.custom_id}")
            for r in sample]


def input_sha256(requests: list[Request]) -> str:
    bodies = [{k: v for k, v in asdict(r).items() if k != "custom_id"} for r in requests]
    return sha(json.dumps(bodies, sort_keys=True, ensure_ascii=False).encode("utf-8"))


def plan(root: Path, worlds: list[dict], mode: str = "batch", cap: float = 200.0) -> dict:
    """Read only: no store creation, provider client or request dispatch."""
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    execution = manifest.get("validation_execution", {})
    rows, requests = workload(worlds)
    sample = qualification_sample(worlds, requests)
    all_requests = requests + controls(sample)
    states: dict[str, tuple[str, str, int]] = {}
    costs: dict[str, float] = {}
    ledger = root / "validate.db"
    if ledger.exists():
        db = sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            states = {cid: (status, digest, attempts) for cid, status, digest, attempts in db.execute(
                "SELECT custom_id,status,req_hash,attempts FROM calls")}
            for model, cost in db.execute("SELECT model,SUM(cost) FROM calls GROUP BY model"):
                provider = spec(model).provider
                costs[provider] = costs.get(provider, 0.0) + cost
        finally:
            db.close()
    remaining: dict[str, float] = {}
    counts: dict[str, int] = {}
    reasons: list[str] = []
    for r in all_requests:
        state = states.get(r.custom_id)
        if state and state[1] != sha(r.to_json().encode("utf-8")):
            reasons.append(f"request changed under existing ID: {r.custom_id}")
        if state and state[0] in MEASURED:
            continue
        if state and state[0] != "submitted" and state[2] >= 1:
            reasons.append(f"fresh transport retry reservation required: {r.custom_id}")
        provider = spec(r.model).provider
        remaining[provider] = remaining.get(provider, 0.0) + estimate_max_cost(r, mode == "batch")
        counts[provider] = counts.get(provider, 0) + 1
    bound = {p: round(costs.get(p, 0.0) + remaining.get(p, 0.0), 9)
             for p in ("anthropic", "openai", "together")}
    balances = execution.get("account_balances_at_stage_start", {})
    gaps: dict[str, float | None] = {}
    for p, maximum in bound.items():
        account = balances.get(p, {})
        available = account.get("available_usd")
        valid = (type(available) in (int, float) and math.isfinite(available)
                 and available >= 0 and account.get("reference"))
        gap = round(max(0, maximum - available), 9) if valid and available is not None else None
        gaps[p] = gap
        if remaining.get(p, 0.0) and (gap is None or gap > 0):
            reasons.append(f"{p}: " + (f"credit gap ${gap:.9f}" if gap is not None else "available credit unconfirmed"))
    if sum(bound.values()) > cap:
        reasons.append(f"validation and qualification exceed unchanged ${cap:.2f} stage cap")
    hashes = {w["world_id"]: sha((root / "bench" / "worlds" / f"{w['world_id']}.json").read_bytes())
              for w in worlds}
    if not execution.get("authorization", {}).get("source"):
        reasons.append("validation authorization is missing")
    if execution.get("worlds_sha256") != hashes:
        reasons.append("saved worlds differ from the authorized validation scope")
    placements = execution.get("placements", {})
    if (placements.get("host") != platform.node()
            or placements.get("checked") != ["Jack's PC", "HaleysPC", "RunPod"]
            or not placements.get("reference")):
        reasons.append("current placement inventory is missing")
    return {"ready": not reasons, "reasons": reasons, "mode": mode,
            "worlds": len(worlds), "candidates": len(rows),
            "mechanical_passes": sum(r["mechanical_ok"] for r in rows),
            "validation_requests": len(requests), "qualification_extra_requests": len(sample),
            "remaining_requests_by_provider": counts,
            "maximum_cumulative_stage_cost_by_provider_usd": bound,
            "maximum_cumulative_stage_cost_usd": round(sum(bound.values()), 9),
            "credit_gap_by_provider_usd": gaps,
            "qualification_sample_ids": [r.custom_id for r in sample],
            "qualification_input_sha256": input_sha256(sample),
            "validation_input_sha256": input_sha256(requests),
            "worlds_sha256": hashes, "automatic_live_fallback": False,
            "maximum_transport_attempts": 1, "provider_client_created": False,
            "execution_started": False}


def require_plan(root: Path, worlds: list[dict], mode: str) -> dict:
    report = plan(root, worlds, mode)
    if not report["ready"]:
        raise RuntimeError("validation reservation refused: " + "; ".join(report["reasons"]))
    return report


def _valid_response(request: Request, response: Response) -> bool:
    if response.status != "ok":
        return False
    if "factcheck:" in request.custom_id:
        return A.parse_oracle(response.text) != "INVALID"
    parsed = A.parse_validation(response.text)
    return (parsed["answer"] in ("A", "B")
            and parsed["other_equally_defensible"] in ("yes", "no")
            and parsed["reversing_reading"] in ("yes", "no"))


def qualify(root: Path, worlds: list[dict], mode: str = "batch") -> dict:
    """Time real useful calls and collection on the worker-sensitive Together path.

    Frontier batch computation runs at the provider and does not use local worker
    slots. Submit ordering and caching are checked separately in affected tests.
    """
    reservation = require_plan(root, worlds, mode)
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = root / "validation_qualification.json"
    fingerprint = preflight.execution_sha256()
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "execution_sha256": fingerprint, "input_sha256": reservation["qualification_input_sha256"],
        "git_commit": preflight.source_commit(), "host": platform.node(), "mode": mode,
        "coverage": "Together inference, collection and SQLite serialization; frontier batch backends do not use local worker slots",
        "control_used_for_retention": False, "parallel_first_responses_used_for_retention": True,
    }
    if (record["execution_sha256"] != fingerprint or record["host"] != platform.node()
            or record["mode"] != mode or record["input_sha256"] != reservation["qualification_input_sha256"]):
        raise RuntimeError("existing qualification is incompatible; preserve it before a fresh qualification version")
    atomic_json(root / "validation_reservation.json", reservation)
    execution_status(root, "qualification_running", execution_started=True)
    _, requests = workload(worlds)
    sample = qualification_sample(worlds, requests)
    store = Store(root / "validate.db", "validate", 200.0, max_transport_attempts=1)
    try:
        for name, reqs, workers in (("serial", controls(sample), 1),
                                    ("parallel", sample, QUALIFICATION_WORKERS)):
            if name in record:
                if not record[name]["semantics_preserved"]:
                    raise RuntimeError(f"{name} qualification failed; paid retries need a new reservation")
                continue
            if any(store.get(r.custom_id) is not None for r in reqs):
                raise RuntimeError(f"{name} has cached calls but no timing receipt; cannot time a replay as inference")
            start = time.perf_counter()
            out = store.run(reqs, mode=mode, workers=workers, allow_live_fallback=False)
            elapsed = time.perf_counter() - start
            complete = len(out) == len(reqs) and all(_valid_response(r, out[r.custom_id]) for r in reqs)
            bodies = [asdict(out[r.custom_id]) for r in reqs if r.custom_id in out]
            record[name] = {"workers": workers, "completed": len(out), "elapsed_seconds": elapsed,
                            "input_sha256": input_sha256(reqs),
                            "output_sha256": sha(json.dumps(bodies, sort_keys=True).encode()),
                            "semantics_preserved": complete,
                            "request_ids": [r.custom_id for r in reqs]}
            atomic_json(path, record)
            print(f"{name}: {len(out)}/{len(reqs)} completed in {elapsed:.3f}s, valid={complete}", flush=True)
            if not complete:
                raise RuntimeError(f"{name} qualification did not complete valid required outputs")
        serial, parallel = record["serial"], record["parallel"]
        chosen = parallel if parallel["elapsed_seconds"] < serial["elapsed_seconds"] else serial
        # Check both frontier validators at the frozen 4,000-token limit before
        # submitting the rest of the cohort. These canonical calls also replay.
        qids = {r.custom_id.split(":")[1] for r in sample}
        probe = [r for r in requests if r.model != "dspro" and r.custom_id.split(":")[1] in qids]
        if "frontier_probe" not in record:
            if any(store.get(r.custom_id) is not None for r in probe):
                raise RuntimeError("frontier probe has cached calls without its receipt")
            start = time.perf_counter()
            out = store.run(probe, mode=mode, workers=chosen["workers"], allow_live_fallback=False)
            record["frontier_probe"] = {
                "completed": len(out), "requests": len(probe),
                "elapsed_seconds": time.perf_counter() - start,
                "semantics_preserved": len(out) == len(probe)
                    and all(_valid_response(r, out[r.custom_id]) for r in probe),
                "input_sha256": input_sha256(probe),
                "output_sha256": sha(json.dumps([asdict(out[r.custom_id]) for r in probe
                                                 if r.custom_id in out], sort_keys=True).encode()),
                "request_ids": [r.custom_id for r in probe],
                "statuses": {cid: r.status for cid, r in out.items()},
            }
            atomic_json(path, record)
        if not record["frontier_probe"]["semantics_preserved"]:
            raise RuntimeError("frontier validation probe failed at frozen limits; no remaining cohort dispatch")
        record.update({"selected_workers": chosen["workers"], "semantics_preserved": True,
                       "placements_checked": ["Jack's PC", "HaleysPC", "RunPod"],
                       "placement_reference": manifest["validation_execution"]["placements"]["reference"],
                       "completed_utc": datetime.now(timezone.utc).isoformat(),
                       "stage_spend_usd": store.spent("validate")})
        atomic_json(path, record)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("throughput", {})["validate"] = record
        manifest["validation_execution"]["status"] = "qualified"
        atomic_json(manifest_path, manifest)
        return record
    except BaseException as e:
        execution_status(root, "qualification_needs_attention", error=f"{type(e).__name__}: {e}",
                         stage_spend_usd=store.spent("validate"))
        raise
    finally:
        store.db.close()
