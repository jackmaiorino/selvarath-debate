"""Final-experiment pilot under the Sonnet-honest amendment (2026-10-21).

Every pilot question is debated by Sonnet 5.5 (honest) against Fable 5.1 (dishonest) with the
frozen prompts, round structure and regeneration rule; every turn gets the blind compliance check;
all eight judges run all six arms in both orders with the incumbent Opus oracle and the DeepSeek
gate. The 40 pilot questions debated in the confirmation reuse its saved debate turns, compliance
checks and k0 Luna, Haiku and Llama-70B verdicts: their requests are rebuilt identically (the store
refuses any content change under an existing id) and copied at zero pilot cost with provenance.
The pilot reports engineering and cost quantities, plus a descriptive k0 error by judge (amendment
of 2026-10-21, Astra Ultra consults 20 and 21); every other verdict outcome stays sealed, including
in the pilot store. Main is not run here.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import platform
import sqlite3
import statistics
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import asym as AS
from . import confirm as CF
from . import preflight
from .debate import debate_task, honest_first
from .ladder import wilson
from .providers import Request
from .store import Store

HONEST, DISHONEST = "sonnet", "fable"
LABEL = f"{HONEST}~{DISHONEST}"
PILOT_DIR = "pilot"
STAGE = "pilot"
WORKERS = 16
ORACLE, GATE = "opus", "dsflash"
REUSED_JUDGES = ("luna", "haiku", "llama70")
QUALIFICATION_VERSION = "v2"
QUALIFICATION_QUESTIONS = 8  # 16 blind opening turns, so the parallel arm can fill 16 workers
PLACEMENT_MAX_AGE_S = 24 * 3600
UNFLAGGED_REVIEW_SAMPLE = 20
RETURNED = ("ok", "truncated", "refusal")  # responses the model returned; "error" is a provider error
# Kept out of the pilot run's own cap (consult 21): oracle-selection challenger calls and the bounded
# batch-path check before main both spend from the same $1,275 pilot allocation.
ORACLE_SELECTION_RESERVE_USD = 40.0
BATCH_CHECK_RESERVE_USD = 20.0
RESERVE_USD = ORACLE_SELECTION_RESERVE_USD + BATCH_CHECK_RESERVE_USD
BATCH_PROVIDERS = ("anthropic", "openai")


def pilot_questions(bench: Path) -> list[dict]:
    qs = [json.loads(x) for x in (bench / "pilot.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = [q["question_id"] for q in qs]
    if len(set(ids)) != len(ids):
        raise RuntimeError("pilot split has duplicate question ids")
    return sorted(qs, key=lambda q: q["question_id"])


def item_id(qid: str) -> str:
    return f"{qid}:{LABEL}:uncapped"


def debate(q: dict, world: str):
    """The frozen debate procedure (regenerates empty or failed turns, at most twice)."""
    return debate_task(q, world, HONEST, None, "uncapped", dishonest=DISHONEST)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _exposed(root: Path) -> set[str]:
    return set(json.loads((root / CF.CONFIRM_DIR / "sample.json").read_text(encoding="utf-8"))["questions"])


# ------------------------------------------------------------------ reuse of the confirmation's cells

def _reused_ids(con: sqlite3.Connection, exposed: set[str]) -> list[str]:
    out = []
    for (cid,) in con.execute("SELECT custom_id FROM calls WHERE status='ok'"):
        p = cid.split(":")
        if p[0] in ("debate", "compliance") and p[2] == LABEL and p[3] in exposed:
            out.append(cid)
        elif (p[0] == "judge" and p[1] in exposed and p[2] == LABEL and p[4] in REUSED_JUDGES
              and p[5] == "debate_k0"):
            out.append(cid)
    return sorted(out)


def seed_from_confirmation(root: Path, db: Path, questions: set[str]) -> dict[str, Any]:
    """Copy the confirmation's saved sonnet~fable cells for the exposed pilot questions into the pilot
    store at zero pilot cost. Idempotent; an existing different row under the same id is refused. The
    receipt (source hash, ids, original cost of every reused cell) does not depend on what was inserted."""
    src = root / CF.CONFIRM_DIR / "confirm.db"
    summary = json.loads((root / CF.CONFIRM_DIR / "confirm_summary.json").read_text(encoding="utf-8"))
    if summary["decision"]["rule"] not in ("C1", "C2", "C3"):
        raise RuntimeError("the confirmation has no completed reading to reuse")
    exposed = _exposed(root)
    if not exposed <= questions:
        raise RuntimeError("confirmation questions are not all in the pilot split")
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    try:
        ids = _reused_ids(s, exposed)
        rows = {r[0]: r for r in s.execute(
            "SELECT custom_id, model, req_hash, request, status, response, cost, attempts FROM calls WHERE custom_id IN (%s)"
            % ",".join("?" * len(ids)), ids)}
    finally:
        s.close()
    d = sqlite3.connect(str(db), isolation_level=None)
    inserted = 0
    try:
        for cid in ids:
            _, model, rh, req, status, resp, cost, attempts = rows[cid]
            have = d.execute("SELECT req_hash, status, response FROM calls WHERE custom_id=?", (cid,)).fetchone()
            if have is not None:
                if have != (rh, status, resp):
                    raise RuntimeError(f"pilot store already holds a different {cid}")
                continue
            d.execute("INSERT INTO calls(custom_id, stage, model, req_hash, request, status, response, cost, attempts, updated)"
                      " VALUES(?,?,?,?,?,?,?,0,?,?)", (cid, STAGE, model, rh, req, status, resp, attempts, time.time()))
            inserted += 1
        if inserted:
            d.execute("INSERT INTO events VALUES(?,?,?)", (time.time(), "seeded_from_confirmation",
                                                             f"{inserted} cells from {src.name}"))
    finally:
        d.close()
    kinds = collections.Counter(cid.split(":")[0] for cid in ids)
    return {"source": str(src), "exposed_questions": sorted(exposed), "cells": len(ids), "by_kind": dict(kinds),
            "original_cost_usd": round(sum(rows[c][6] or 0.0 for c in ids), 6),
            "ids_sha256": _sha("\n".join(ids).encode()),
            "cells_sha256": _sha(json.dumps([rows[c][2:6] for c in ids]).encode())}


def _reuse_receipt(base: Path, reuse: dict) -> dict:
    """Keep the first receipt; a later run must describe exactly the same reused cells."""
    path = base / "reused_from_confirmation.json"
    if path.exists():
        first = json.loads(path.read_text(encoding="utf-8"))
        if {k: first[k] for k in ("ids_sha256", "cells_sha256", "cells")} != {k: reuse[k] for k in ("ids_sha256", "cells_sha256", "cells")}:
            raise RuntimeError("reused cells differ from the first reuse receipt")
        return first
    reuse = dict(reuse, seeded_utc=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(reuse, indent=2), encoding="utf-8")
    return reuse


# ------------------------------------------------------------------ launch: manifest, placements, qualification

def bump_manifest_commit(root: Path, reason: str) -> dict:
    """Point the run manifest at the current source commit, keeping the prior one in commit_history.
    Changes nothing else; every other launcher check (receipts by execution fingerprint, credentials,
    audit, ceiling) still applies."""
    from .validation import atomic_json

    path = root / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    commit = preflight.source_commit()
    if manifest.get("git_commit") != commit:
        manifest.setdefault("commit_history", []).append(
            {"prior": manifest.get("git_commit"), "new": commit, "utc": datetime.now(timezone.utc).isoformat(), "reason": reason})
        manifest["git_commit"] = commit
        atomic_json(path, manifest)
    return {"git_commit": commit}


def latest_placements(root: Path) -> Path:
    """The newest pilot placement inventory (scripts/finalphase_placements.py), checked on this host
    within the last day, covering all three placements."""
    files = sorted(root.glob("pilot_placements_*.json"))
    if not files:
        raise RuntimeError("no pilot placement inventory; run scripts/finalphase_placements.py")
    path = files[-1]
    rec = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - datetime.fromisoformat(rec["checked_utc"]).timestamp()
    if rec.get("host") != platform.node() or age > PLACEMENT_MAX_AGE_S or \
            not all(k in rec for k in ("Jack's PC", "HaleysPC", "RunPod")):
        raise RuntimeError(f"placement inventory {path.name} is stale, from another host, or incomplete")
    return path


def qualification_sample(root: Path, worlds: dict[str, dict]) -> list[Request]:
    """Blind opening-round requests of the first QUALIFICATION_QUESTIONS fresh pilot questions (fixed hash order)."""
    exposed = _exposed(root)
    fresh = sorted((q for q in pilot_questions(root / "bench") if q["question_id"] not in exposed),
                   key=lambda q: _sha(f"pilot-qualification-v1:{q['question_id']}".encode()))[:QUALIFICATION_QUESTIONS]
    sample = [r for q in fresh for r in next(debate(q, worlds[q["world_id"]]["world_text"]))]
    if len(sample) != 2 * QUALIFICATION_QUESTIONS:
        raise ValueError("pilot qualification needs both blind opening turns of every sampled question")
    return sample


def qualify(root: Path, worlds: dict[str, dict], cap: float) -> dict:
    """Serial control (fresh ids, never used in the pilot) against the parallel canonical opening turns,
    which then replay into the pilot. A cached response can never be timed: an arm that has started is
    either finished in the receipt or refused (an interrupted qualification needs a new version)."""
    from .validation import atomic_json, input_sha256, sha

    manifest_path = root / "run_manifest.json"
    placements = latest_placements(root)
    sample = qualification_sample(root, worlds)
    controls = [replace(r, custom_id=f"qualification:pilot:{QUALIFICATION_VERSION}:serial:{r.custom_id}") for r in sample]
    path = root / f"pilot_qualification_{QUALIFICATION_VERSION}.json"
    fingerprint = preflight.execution_sha256()
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "execution_sha256": fingerprint, "input_sha256": input_sha256(sample), "git_commit": preflight.source_commit(),
        "host": platform.node(), "mode": "live",
        "coverage": (f"{len(sample)} blind opening debate turns ({QUALIFICATION_QUESTIONS} Sonnet honest, "
                     f"{QUALIFICATION_QUESTIONS} Fable dishonest) of {QUALIFICATION_QUESTIONS} fresh pilot questions; "
                     f"serial 1 worker against {WORKERS} workers; judging, oracle and gate calls not covered"),
        "parallel_first_responses_used_in_pilot": True, "control_used_in_pilot": False}
    if record["execution_sha256"] != fingerprint or record["host"] != platform.node() \
            or record["input_sha256"] != input_sha256(sample):
        raise RuntimeError("existing pilot qualification is incompatible; preserve it before a fresh version")
    store = Store(root / "pilot.db", STAGE, cap - RESERVE_USD, max_transport_attempts=6)
    try:
        for name, reqs, workers in (("serial", controls, 1), ("parallel", sample, WORKERS)):
            done = record.get(name, {})
            if done.get("semantics_preserved") is True:
                continue
            if done.get("semantics_preserved") is False:
                raise RuntimeError(f"{name} pilot qualification failed; paid retries need a new version")
            if "started_utc" in done or any(store.get(r.custom_id) is not None for r in reqs):
                raise RuntimeError(f"{name} qualification was interrupted or has cached calls; a cached replay "
                                   f"cannot be timed as inference, so start a new qualification version")
            record[name] = {"started_utc": datetime.now(timezone.utc).isoformat(), "workers": workers,
                            "request_ids": [r.custom_id for r in reqs]}
            atomic_json(path, record)
            start = time.perf_counter()
            out = store.run(reqs, mode="live", workers=workers)
            elapsed = time.perf_counter() - start
            complete = len(out) == len(reqs) and all(out[r.custom_id].status == "ok" and out[r.custom_id].text.strip()
                                                     for r in reqs)
            record[name] = {"workers": workers, "completed": len(out), "elapsed_seconds": elapsed,
                            "input_sha256": input_sha256(reqs),
                            "output_sha256": sha(json.dumps([asdict(out[r.custom_id]) for r in reqs if r.custom_id in out],
                                                            sort_keys=True).encode()),
                            "semantics_preserved": complete, "request_ids": [r.custom_id for r in reqs]}
            atomic_json(path, record)
            if not complete:
                raise RuntimeError(f"{name} pilot qualification did not complete valid opening turns")
        serial, parallel = record["serial"], record["parallel"]
        selected = WORKERS if parallel["elapsed_seconds"] < serial["elapsed_seconds"] else 1
        record.update({"selected_workers": selected, "semantics_preserved": True,
                       "placements_checked": ["Jack's PC", "HaleysPC", "RunPod"],
                       "placement_reference": str(placements), "placement_sha256": _sha(placements.read_bytes()),
                       "completed_utc": datetime.now(timezone.utc).isoformat(), "stage_spend_usd": store.spent(STAGE)})
        atomic_json(path, record)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("throughput", {})["pilot"] = record
        atomic_json(manifest_path, manifest)
        return record
    finally:
        store.db.close()


def selected_workers(root: Path) -> int:
    """The worker count the pilot receipt selected (the launcher refuses any other)."""
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    return int(manifest.get("throughput", {}).get("pilot", {}).get("selected_workers", WORKERS))


# ------------------------------------------------------------------ the pilot run

def _config(qs: list[dict], worlds: dict[str, dict], judges: tuple[str, ...], cap: float, workers: int) -> dict:
    from .judging import ARMS, ORDERS
    from .models import MODELS

    inputs = CF.frozen_inputs(qs, worlds)
    inputs["models"] = {m: asdict(MODELS[m]) for m in sorted(set(judges) | {HONEST, DISHONEST, ORACLE, GATE, "dspro"})}
    inputs["source_sha256"]["pilot.py"] = _sha(Path(__file__).read_bytes())
    return {"pairings": [LABEL], "stage": STAGE, "cap": cap, "run_cap": cap - RESERVE_USD, "reserve_usd": RESERVE_USD,
            "judges": list(judges), "arms": list(ARMS), "orders": list(ORDERS), "questions": [q["question_id"] for q in qs],
            "oracle": ORACLE, "gate": GATE, "workers": workers, "frozen_inputs": inputs}


def run(root: Path, worlds: dict[str, dict], judges: tuple[str, ...], cap: float, workers: int = WORKERS) -> dict:
    """Debates, compliance and judging under cap - RESERVE_USD, so the oracle-selection and batch-check
    allowances survive whatever judging costs."""
    from .engine import _run_live_chunks, drive
    from .judging import ARMS, ORDERS, judge_task

    qs = pilot_questions(root / "bench")
    qmap = {q["question_id"]: q for q in qs}
    base = root / PILOT_DIR
    AS.claim_dir(base, _config(qs, worlds, judges, cap, workers))
    Store(root / "pilot.db", STAGE, cap - RESERVE_USD, max_transport_attempts=6).db.close()  # schema first
    reuse = _reuse_receipt(base, seed_from_confirmation(root, root / "pilot.db", set(qmap)))
    times: dict[str, float] = {}
    s = Store(root / "pilot.db", STAGE, cap - RESERVE_USD, max_transport_attempts=6)
    try:
        t = time.perf_counter()
        debates, failed = drive(s, {qid: debate(q, worlds[q["world_id"]]["world_text"]) for qid, q in qmap.items()},
                                workers=workers)
        times["debates_s"] = time.perf_counter() - t
        comp = [AS.compliance_request(LABEL, qmap[qid], turn) for qid, d in debates.items() for turn in d["turns"]]
        t = time.perf_counter()
        cout = _run_live_chunks(s, comp, workers)
        times["compliance_s"] = time.perf_counter() - t
        jt = {}
        for qid, d in debates.items():
            q = qmap[qid]
            item = dict(q, world_text=worlds[q["world_id"]]["world_text"], turns=d["turns"], question_id=item_id(qid))
            for j in judges:
                for arm in ARMS:
                    for o in ORDERS:
                        jt[(qid, j, arm, o)] = judge_task(item, j, arm, o, ORACLE, GATE)
        t = time.perf_counter()
        judged, jfailed = drive(s, jt, workers=workers)
        times["judging_s"] = time.perf_counter() - t
        spend = s.spent(STAGE)
    finally:
        s.db.close()
    flags = []
    for r in comp:
        resp = cout.get(r.custom_id)
        text = resp.text if resp is not None and resp.status == "ok" else None
        _, _, _, qid, rnd, role = r.custom_id.split(":")
        flags.append({"custom_id": r.custom_id, "question_id": qid, "round": rnd, "role": role,
                      "verdict": AS.compliance_verdict(text), "text": text, "returned": resp is not None})
    _export(base, debates, flags, judged)
    readout = engineering_readout(root, qs, debates, flags, judged, judges, reuse, failed, jfailed, spend, times, cap)
    (base / "pilot_readout.json").write_text(json.dumps(readout, indent=2), encoding="utf-8")
    print(json.dumps({k: readout[k] for k in ("completion", "spend", "main_forecast")}, indent=2), flush=True)
    return readout


# Fields of a non-k0 judgment that carry no verdict outcome (explicit projection, consult 21).
OPEN_FIELDS = ("question_id", "world_id", "judge", "arm", "order", "verdict_status", "parse_ok", "n_queries_used",
               "cost", "input_tokens", "output_tokens")
OPEN_QUERY_FIELDS = ("slot", "claim", "well_formed", "screen", "gate", "gate_reason", "retry", "oracle")


def open_projection(r: dict) -> dict:
    """A judgment as it may appear in an open file: k0 in full; any other arm without its verdict,
    confidence, correctness, reasoning or the judge's raw query-step text."""
    if r["arm"] == "debate_k0":
        return r
    out = {k: r[k] for k in OPEN_FIELDS if k in r}
    out["queries"] = [{k: q[k] for k in OPEN_QUERY_FIELDS if k in q} for q in r.get("queries", [])]
    out["events"] = [{k: e[k] for k in ("step", "event", "status", "used") if k in e} for e in r.get("events", [])]
    return out


def _export(base: Path, debates: dict, flags: list, judged: dict) -> None:
    """Open files use open_projection; full records go to the sealed file. The pilot store also holds
    the raw non-k0 responses and is sealed in the same way."""
    sealed = base / "sealed"
    sealed.mkdir(parents=True, exist_ok=True)
    rows = [dict(r, judge_key=list(k)) for k, r in judged.items()]
    for path, data in ((base / "debates.jsonl", debates.values()), (base / "compliance.jsonl", flags),
                       (base / "judgments_open.jsonl", [dict(open_projection(r), judge_key=r["judge_key"]) for r in rows]),
                       (sealed / "judgments_full.jsonl", rows)):
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in data)
    (sealed / "README.txt").write_text(
        "Sealed until main's preregistered analysis: judgments_full.jsonl and ../../pilot.db hold non-k0 verdict "
        "outcomes. Engineering readouts are computed without reading them.\n", encoding="utf-8")


def _store_rows(db: Path) -> list[tuple]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT custom_id, model, status, response, cost, attempts FROM calls").fetchall()
    finally:
        con.close()


def _events(db: Path) -> collections.Counter:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return collections.Counter(kind for (kind,) in con.execute("SELECT kind FROM events"))
    finally:
        con.close()


def _kind(cid: str, model: str) -> tuple[str, str]:
    """(cost component, call kind with model, arm or role)."""
    p = cid.split(":")
    if p[0] == "judge":
        return f"judge:{p[4]}:{p[5]}", f"judge:{model}:{p[5]}:{'verdict' if p[-1] == 'verdict' else 'query_step'}"
    if p[0] == "debate":
        return "debate", f"debate:{model}:{p[5]}"
    if p[0] == "compliance":
        return "compliance", f"compliance:{model}:{p[-1]}"
    return p[0], f"{p[0]}:{model}"


def _finish(resp: dict) -> str | None:
    raw = (resp.get("extra") or {}).get("raw_response") or {}
    if "stop_reason" in raw:
        return raw["stop_reason"]
    if "status" in raw:
        inc = raw.get("incomplete_details") or {}
        return raw["status"] + (f"/{inc.get('reason')}" if inc else "")
    choices = raw.get("choices") or [{}]
    return choices[0].get("finish_reason")


def _dist(xs: list[int]) -> dict | None:
    if not xs:
        return None
    xs = sorted(xs)
    q = lambda f: xs[min(len(xs) - 1, int(f * len(xs)))]
    return {"mean": round(statistics.fmean(xs)), "p50": q(0.5), "p90": q(0.9), "max": xs[-1]}


def engineering_readout(root: Path, qs: list[dict], debates: dict, flags: list, judged: dict, judges: tuple[str, ...],
                        reuse: dict, failed: list, jfailed: list, spend: float, times: dict, cap: float) -> dict[str, Any]:
    from .judging import ARMS, ORDERS
    from .models import MODELS

    exposed = set(reuse["exposed_questions"])
    n_fresh = sum(q["question_id"] not in exposed for q in qs)
    con = sqlite3.connect(f"file:{(root / CF.CONFIRM_DIR / 'confirm.db').as_posix()}?mode=ro", uri=True)
    try:
        reused = set(_reused_ids(con, exposed))
    finally:
        con.close()
    calls: dict[str, dict] = collections.defaultdict(lambda: {"calls": 0, "status": collections.Counter(), "attempts": collections.Counter(),
                                                              "finish": collections.Counter(), "out": [], "reasoning": [], "in": [],
                                                              "cost": 0.0})
    fresh_cost = collections.defaultdict(float)
    for cid, model, status, resp, cost, attempts in _store_rows(root / "pilot.db"):
        comp, kind = _kind(cid, model)
        c = calls[kind]
        c["calls"] += 1
        c["status"][status] += 1
        c["attempts"][attempts or 0] += 1
        if resp and status in RETURNED:
            d = json.loads(resp)
            c["finish"][str(_finish(d))] += 1
            c["out"].append(d.get("output_tokens") or 0)
            c["reasoning"].append(d.get("reasoning_tokens") or 0)
            c["in"].append(d.get("input_tokens") or 0)
        c["cost"] += cost or 0
        if cid not in reused and not cid.startswith("qualification:"):
            fresh_cost[comp] += cost or 0
    call_report = {}
    for kind, c in sorted(calls.items()):
        returned = sum(c["status"].get(s, 0) for s in RETURNED)
        trunc = c["status"].get("truncated", 0)
        call_report[kind] = {"calls": c["calls"], "status": dict(c["status"]), "attempts": dict(c["attempts"]),
                             "finish_reasons": dict(c["finish"]), "input_tokens": _dist(c["in"]),
                             "output_tokens": _dist(c["out"]), "reasoning_tokens": _dist(c["reasoning"]),
                             "truncation": {"truncated": trunc, "returned": returned,
                                            "wilson95": wilson(trunc, returned) if returned else None,
                                            "provider_errors": c["status"].get("error", 0),
                                            "transport_failed": c["status"].get("transport_failed", 0)},
                             "cost_usd": round(c["cost"], 4)}
    sessions: dict[str, dict] = {}
    for (qid, j, arm, o), r in judged.items():
        e = sessions.setdefault(f"{j}:{arm}:{o}", {"sessions": 0, "verdict_status": collections.Counter(), "unparsed": 0,
                                                    "queries_used": collections.Counter(), "done_early": 0, "replacements": 0,
                                                    "duplicates": 0, "gate": collections.Counter(), "oracle": collections.Counter(),
                                                    "failure_events": collections.Counter()})
        e["sessions"] += 1
        e["verdict_status"][r.get("verdict_status")] += 1
        e["unparsed"] += not r["parse_ok"]
        e["queries_used"][r.get("n_queries_used", 0)] += 1
        seen = set()
        for qr in r.get("queries", []):
            e["gate"][qr.get("gate")] += 1
            e["replacements"] += bool(qr.get("retry"))
            if "oracle" in qr:
                e["oracle"][qr["oracle"]] += 1
                claim = (qr.get("claim") or "").strip().lower()
                e["duplicates"] += claim in seen
                seen.add(claim)
        for ev in r.get("events", []):
            if ev.get("event") == "done":
                e["done_early"] += 1
            elif ev.get("event") in ("query_step_failure", "gate_failure", "oracle_failure"):
                e["failure_events"][ev["event"]] += 1
    sessions = {k: {kk: (dict(vv) if isinstance(vv, collections.Counter) else vv) for kk, vv in v.items()}
                for k, v in sorted(sessions.items())}
    # Descriptive k0 error by judge (amendment of 2026-10-21): questions weigh equally, orders averaged; an
    # unparsed or refused verdict counts wrong and is listed; a missing cell is listed, never scored, and the
    # figure is marked incomplete. A verdict counts correct only if it parsed. No other arm is scored.
    k0: dict[str, dict] = {}
    for j in judges:
        out = {}
        for name, keep in (("exposed_40", lambda q: q in exposed), ("fresh_135", lambda q: q not in exposed),
                           ("combined", lambda q: True)):
            per_q, invalid, missing = [], [], []
            for q in qs:
                qid = q["question_id"]
                if not keep(qid):
                    continue
                rs = [judged.get((qid, j, "debate_k0", o)) for o in ORDERS]
                for o, r in zip(ORDERS, rs):
                    if r is None:
                        missing.append(f"{qid}:{o}")
                    elif not r["parse_ok"]:
                        invalid.append(f"{qid}:{o}:{r.get('verdict_status')}")
                if any(r is None for r in rs):
                    continue
                per_q.append(sum(not (r["parse_ok"] and r["correct"]) for r in rs) / len(rs))
            out[name] = {"questions": len(per_q), "error": sum(per_q) / len(per_q) if per_q else None,
                         "complete": not missing, "missing_cells": missing, "invalid_verdicts": invalid}
        k0[j] = out
    flagged = [f["custom_id"] for f in flags if f["verdict"] == "yes"]
    unknown = [f["custom_id"] for f in flags if f["verdict"] == "unknown"]
    unflagged = sorted((f["custom_id"] for f in flags if f["verdict"] == "no"),
                       key=lambda c: _sha(f"pilot-compliance-review-v1:{c}".encode()))[:UNFLAGGED_REVIEW_SAMPLE]
    words, regenerated = collections.defaultdict(list), 0
    for d in debates.values():
        for t in d["turns"]:
            words[t["role"]].append(t["words"])
            regenerated += t["attempts"] > 1
    by_author = collections.defaultdict(lambda: [0, 0])
    for q in qs:
        a = q.get("author", "unrecorded")
        by_author[a][0] += honest_first(q["question_id"])
        by_author[a][1] += 1
    per_q = {}
    for comp, c in fresh_cost.items():
        denom = n_fresh if comp in ("debate", "compliance") or any(
            comp == f"judge:{j}:debate_k0" for j in REUSED_JUDGES) else len(qs)
        per_q[comp] = c / denom if denom else 0.0
    live = sum(per_q.values())

    def batch_factor(comp: str) -> float:
        if comp == "debate":
            return 0.5  # Sonnet and Fable are both Anthropic
        model = {"compliance": "dspro", "oracle": ORACLE, "gate": GATE}.get(comp) or (comp.split(":")[1] if comp.startswith("judge:") else None)
        return 0.5 if model and MODELS[model].provider in BATCH_PROVIDERS else 1.0
    batch = sum(v * batch_factor(k) for k, v in per_q.items())
    main_n = sum(1 for x in (root / "bench" / "main.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())
    main_cap = 3634.0
    return {
        "amendment": "Sonnet-honest pilot, 2026-10-21 (Astra Ultra consults 20 and 21)",
        "completion": {"questions": len(qs), "fresh_questions": n_fresh, "debates": len(debates),
                       "failed_debates": len(failed), "judgments": len(judged), "failed_judgments": len(jfailed),
                       "expected_judgments": len(qs) * len(judges) * len(ARMS) * len(ORDERS),
                       "compliance_returned": sum(f["returned"] for f in flags), "compliance_expected": len(flags)},
        "spend": {"pilot_stage_usd": spend, "run_cap_usd": cap - RESERVE_USD, "reserved_usd": RESERVE_USD,
                  "reused_original_cost_usd": reuse["original_cost_usd"],
                  "fresh_by_component_usd": {k: round(v, 4) for k, v in sorted(fresh_cost.items())}},
        "times_s": times, "store_events": dict(_events(root / "pilot.db")),
        "calls": call_report, "judge_sessions": sessions, "k0_error_descriptive": k0,
        "compliance": {"turns": len(flags), "flagged": len(flagged), "unknown": len(unknown),
                       "flagged_ids": flagged, "unknown_ids": unknown, "unflagged_review_sample": unflagged},
        "debate_turns": {"words": {r: _dist(v) for r, v in words.items()}, "regenerated_turns": regenerated},
        "order_balance": {"honest_first": sum(v[0] for v in by_author.values()), "questions": len(qs),
                          "by_author": {a: {"honest_first": v[0], "questions": v[1]} for a, v in sorted(by_author.items())},
                          "note": "deterministic hash parity, not an exact half or an exact within-author balance"},
        "not_measured": ["per-call latency (the store records no per-call timing; stage times are reported)"],
        "main_forecast": {"provisional": True, "per_question_live_usd": round(live, 4), "per_question_batch_usd": round(batch, 4),
                          "per_question_by_component_usd": {k: round(v, 4) for k, v in sorted(per_q.items())},
                          "main_questions": main_n, "main_live_usd": round(live * main_n, 2),
                          "main_batch_usd": round(batch * main_n, 2), "main_cap_usd": main_cap,
                          "affordable_questions_live": math.floor(main_cap / live) if live else None,
                          "affordable_questions_batch": math.floor(main_cap / batch) if batch else None,
                          "note": "fresh-work list prices from the live pilot; batch halves Anthropic and OpenAI only and "
                                  "ignores batch-cache differences; incomplete work would depress it"},
    }
