"""Finite, authoring-only correction through the supported launcher; no validation dispatch."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))
from finalphase import authoring as A, preflight, store
from finalphase.models import spec
from finalphase.providers import Request

ROOT = Path(os.environ.get("FINALPHASE_ROOT", "D:/finalphase-runs/final-phase-2026-10-03"))
MANIFEST = ROOT / "run_manifest.json"
CAP = 21.75
REVISION = A.P.AUTHOR_LENGTH_REVISION
FABLE = ("W001", "W003", "W005", "W007")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def ledger() -> tuple[list[dict], list[dict]]:
    with sqlite3.connect((ROOT / "author.db").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return ([dict(r) for r in db.execute("SELECT * FROM calls ORDER BY custom_id")],
                [dict(r) for r in db.execute("SELECT * FROM batches ORDER BY batch_id")])


def accounting(manifest: dict, calls: list[dict]) -> dict:
    costs = {provider: sum(c["cost"] for c in calls if spec(c["model"]).provider == provider)
             for provider in ("anthropic", "openai")}
    checks = {provider: 0.0 for provider in costs}
    for filename in ("access_recheck_20261003T155242Z.json", "access_recheck_20261003T162745Z.json"):
        for c in json.loads((ROOT / filename).read_text(encoding="utf-8"))["checks"]:
            if c["provider"] in checks:
                checks[c["provider"]] += c.get("cost_usd", 0)
    funded = manifest["quality_check_funding"]["user_reported_credit_usd"]
    return {"authoring_spend_usd": sum(c["cost"] for c in calls), "provider_authoring_usd": costs,
            "access_check_usd": checks,
            "estimated_credit_remaining_usd": {p: funded[p] - costs[p] - checks[p] for p in costs},
            "balance_source": "reported top-ups minus recorded author and access-check usage; no provider balance query"}


def reserve(manifest: dict) -> dict:
    calls, batches = ledger()
    if any(c["status"] not in store.MEASURED for c in calls) or any(not b["collected"] for b in batches):
        raise RuntimeError("unsettled author calls or batches require reconciliation before correction")
    account = accounting(manifest, calls)
    requests = []
    for w, a, h in A.world_ids(8):
        if w not in FABLE:
            continue
        obj = json.loads((ROOT / "bench/worlds" / f"{w}.json").read_text(encoding="utf-8"))
        if A.world_check(obj).ok:
            continue
        req = A.author_request(w, a, h)
        cached = next((c for c in calls if c["custom_id"] == req.custom_id), None)
        if cached and cached["req_hash"] != hashlib.sha256(req.to_json().encode()).hexdigest():
            raise RuntimeError("correction request changed under an existing ID")
        if cached:
            raise RuntimeError(f"cached correction for {w} failed admission; a newly reserved retry is required")
        requests.append({"world_id": w, "custom_id": req.custom_id,
                         "maximum_batch_cost_usd": store.estimate_max_cost(req, True),
                         "request_sha256": hashlib.sha256(req.to_json().encode()).hexdigest()})
    maximum = sum(r["maximum_batch_cost_usd"] for r in requests)
    gap = max(0.0, account["authoring_spend_usd"] + maximum - CAP,
              maximum - account["estimated_credit_remaining_usd"]["anthropic"])
    if gap > 0:
        raise RuntimeError(f"correction funding gap ${gap:.6f}; no request dispatched")
    return {**account, "recorded_at_utc": now(), "requests": requests, "mode": "batch",
            "maximum_attempts_per_world": 1, "paid_retries_reserved": 0,
            "automatic_live_fallback": False, "maximum_additional_cost_usd": maximum,
            "maximum_cumulative_authoring_usd": account["authoring_spend_usd"] + maximum,
            "cumulative_cap_usd": CAP, "funding_gap_usd": gap}


def prepare() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    reservation = reserve(manifest)
    preserved = ROOT / "preserved/pre-length-v2"
    if not preserved.exists():
        preserved.mkdir(parents=True)
        for name in ("bench", "author.db", "run_manifest.json", "author_quality_summary.json"):
            source = ROOT / name
            if source.is_dir():
                shutil.copytree(source, preserved / name)
            else:
                shutil.copy2(source, preserved / name)
        hashes = {str(p.relative_to(preserved)).replace("\\", "/"): digest(p)
                  for p in sorted(preserved.rglob("*")) if p.is_file()}
        save(preserved / "hashes.json", hashes)
    hashes = json.loads((preserved / "hashes.json").read_text(encoding="utf-8"))
    if any(digest(preserved / p) != h for p, h in hashes.items()):
        raise RuntimeError("preserved original artifact hash mismatch")
    correction = manifest.setdefault("authoring_length_correction", {})
    correction.update({"status": "prepared", "prompt_revision": REVISION,
                       "authorization": "Jack, 2026-10-05: prospective authoring-only prompt amendment to enforce existing length; first corrected Fable generation, then remaining noncompliant Fable worlds",
                       "amendment_recorded_at_utc": correction.get("amendment_recorded_at_utc", now()),
                       "amendment_source": "reports/final-phase-2026-10-03/authoring-length-amendment.md",
                       "originals_path": str(preserved), "originals_sha256": hashes,
                       "reservation": reservation, "reasoning_effort": "high", "max_tokens": 64000,
                       "astra_unchanged": True, "launch_git_commit": preflight.source_commit()})
    manifest["git_commit"] = preflight.source_commit()
    for name in ("finalphase/authoring.py", "finalphase/cli.py", "finalphase/store.py",
                 "finalphase/prompts_authoring.py", "scripts/finalphase_length_correction.py"):
        manifest["inputs_sha256"][name] = digest(WORKSPACE / name)
    save(MANIFEST, manifest)
    print(json.dumps(reservation, indent=2), flush=True)


def replay(calls: list[dict]) -> dict:
    primary = ROOT / "author.db"
    before = digest(primary)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "author.db"
        shutil.copy2(primary, path)
        s = store.Store(path, "author", CAP)
        def forbidden(*args, **kwargs):
            raise RuntimeError("provider dispatch during replay")
        live, batch = store.LIVE.copy(), store.BATCH.copy()
        try:
            store.LIVE.update({p: forbidden for p in live})
            store.BATCH.update({p: (forbidden, forbidden, forbidden) for p in batch})
            reqs = [Request(**store._req_fields(json.loads(c["request"]))) for c in calls]
            responses = s.run(reqs, mode="batch", allow_live_fallback=False)
            for c in calls:
                if asdict(responses[c["custom_id"]]) != json.loads(c["response"]):
                    raise RuntimeError("cached response changed during replay")
        finally:
            store.LIVE.update(live)
            store.BATCH.update(batch)
            s.db.close()
    after = digest(primary)
    if before != after:
        raise RuntimeError("primary ledger changed during replay")
    return {"cached_responses": len(responses), "provider_dispatches": 0,
            "primary_store_sha256_before": before, "primary_store_sha256_after": after, "bit_identical": True}


def report() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    calls, batches = ledger()
    correction = manifest["authoring_length_correction"]
    preserved = Path(correction["originals_path"])
    hashes = correction["originals_sha256"]
    if any(digest(preserved / p) != h for p, h in hashes.items()):
        raise RuntimeError("original provenance changed")
    old_calls = sqlite3.connect((preserved / "author.db").as_uri() + "?mode=ro", uri=True)
    try:
        columns = [d[1] for d in old_calls.execute("PRAGMA table_info(calls)")]
        current = {c["custom_id"]: c for c in calls}
        for row in old_calls.execute("SELECT * FROM calls"):
            old = dict(zip(columns, row))
            if current[old["custom_id"]] != old:
                raise RuntimeError("original response, request or spending entry changed")
    finally:
        old_calls.close()
    worlds = []
    for path in sorted((ROOT / "bench/worlds").glob("W*.json")):
        w = json.loads(path.read_text(encoding="utf-8"))
        admission = A.world_check(w)
        checks = [A.mechanical_check(q, w["world_text"], w["questions"]) for q in w["questions"]]
        if w["author"] == "astra" and digest(path) != hashes[f"bench/worlds/{path.name}"]:
            raise RuntimeError("Astra artifact changed")
        worlds.append({"world_id": w["world_id"], "author": w["author"], "author_call": w["author_call"],
                       "world_words": len(w["world_text"].split()), "world_admission_ok": admission.ok,
                       "world_admission_reasons": admission.reasons, "questions": len(checks),
                       "mechanical_pass": sum(c.ok for c in checks),
                       "mechanical_failures": [{"question_id": A.qid(w["world_id"], i), "reasons": c.reasons}
                                               for i, c in enumerate(checks) if not c.ok], "sha256": digest(path)})
    account = accounting(manifest, calls)
    active = sum(c["status"] not in store.MEASURED for c in calls)
    passed = len(worlds) == 8 and all(w["world_admission_ok"] for w in worlds) and not active
    summary = {"recorded_at_utc": now(), "status": "eight_compliant_worlds" if passed else "correction_incomplete",
               "worlds": worlds, "complete_worlds": len(worlds),
               "compliant_worlds": sum(w["world_admission_ok"] for w in worlds),
               "candidate_questions": sum(w["questions"] for w in worlds),
               "mechanical_pass": sum(w["mechanical_pass"] for w in worlds), "active_calls": active,
               "independent_answer_validation_started": False, "main_started": False,
               "mechanical_pass_is_answer_key_validation": False, "original_provenance_verified": True,
               "astra_hashes_unchanged": True, "accounting": account}
    rejection_path = ROOT / "author_rejections.jsonl"
    summary["author_rejections"] = [json.loads(line) for line in rejection_path.read_text().splitlines()] if rejection_path.exists() else []
    if not active and all(b["collected"] for b in batches):
        manifest["verification"]["cached_replay"] = replay(calls)
    save(ROOT / "author_quality_summary.json", summary)
    correction.update({"status": summary["status"], "last_reported_at_utc": now(), "accounting": account})
    manifest["quality_review"] = summary
    manifest["quality_check"].update({"status": summary["status"], "complete_worlds": len(worlds),
                                     "candidate_questions": summary["candidate_questions"],
                                     "mechanical_question_pass": summary["mechanical_pass"],
                                     "current_authoring_cost_usd": account["authoring_spend_usd"],
                                     "resume_requires": "No broader authoring or paid validation authorized; preserve remaining launch gates"})
    manifest["execution_status"] = summary["status"]
    for path in (ROOT / "author.db", ROOT / "author_quality_summary.json", *sorted((ROOT / "bench/worlds").glob("*.json"))):
        manifest["outputs_sha256"][str(path.relative_to(ROOT)).replace("\\", "/")] = digest(path)
    save(MANIFEST, manifest)
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def execute() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["authoring_length_correction"]["prompt_revision"] != REVISION:
        raise RuntimeError("prospective amendment is missing")
    preflight.require("author", ROOT, 8, "batch", True)
    manifest["authoring_length_correction"].update({"status": "running", "pid": os.getpid(), "started_at_utc": now(),
                                                 "reservation": reserve(manifest)})
    save(MANIFEST, manifest)
    try:
        for ids in ("W001", "W003,W005,W007"):
            command = [sys.executable, "-m", "finalphase.cli", "author", "--worlds", "8", "--only", ids,
                       "--quality-check", "--mode", "batch", "--attempts", "1", "--spend-cap", str(CAP), "--replace-invalid"]
            print("supported command: " + subprocess.list2cmdline(command), flush=True)
            subprocess.run(command, cwd=WORKSPACE, check=True)
            if ids == "W001":
                obj = json.loads((ROOT / "bench/worlds/W001.json").read_text(encoding="utf-8"))
                if not A.world_check(obj).ok or not obj["author_call"].endswith(REVISION):
                    raise RuntimeError("corrected probe did not comply; remaining Fable worlds were not dispatched")
                manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
                manifest["authoring_length_correction"]["probe"] = {"world_id": "W001", "complied": True,
                    "words": len(obj["world_text"].split()), "sha256": digest(ROOT / "bench/worlds/W001.json")}
                manifest["authoring_length_correction"]["remaining_reservation"] = reserve(manifest)
                save(MANIFEST, manifest)
    finally:
        report()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute", "report"))
    args = parser.parse_args()
    {"prepare": prepare, "execute": execute, "report": report}[args.action]()


if __name__ == "__main__":
    main()
