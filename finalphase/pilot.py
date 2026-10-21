"""Final-experiment pilot under the Sonnet-honest amendment (2026-10-21).

Every pilot question is debated by Sonnet 5.5 (honest) against Fable 5.1 (dishonest) with the
frozen prompts, round structure and regeneration rule; every turn gets the blind compliance check;
all eight judges run all six arms in both orders with the incumbent Opus oracle and the DeepSeek
gate. The 40 pilot questions debated in the confirmation reuse its saved debate turns, compliance
checks and k0 Luna, Haiku and Llama-70B verdicts: their requests are rebuilt identically (the store
refuses any content change under an existing id) and copied at zero pilot cost with provenance.
The pilot reports engineering and cost quantities, plus a descriptive k0 error by judge (amendment
of 2026-10-21, Astra Ultra consult 20); every other verdict outcome stays sealed. Main is not run here.
"""
from __future__ import annotations

import collections
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

from . import asym as AS
from . import confirm as CF
from . import preflight
from .debate import debate_task
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
QUALIFICATION_VERSION = "v1"
UNFLAGGED_REVIEW_SAMPLE = 20
MEASURED = ("ok", "truncated", "refusal", "error")
# Pilot judging uses list prices for Anthropic and OpenAI live; batch is half price for them.
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


# ------------------------------------------------------------------ reuse of the confirmation's cells

def _reused_ids(con: sqlite3.Connection, exposed: set[str]) -> list[str]:
    out = []
    for (cid,) in con.execute("SELECT custom_id FROM calls WHERE status='ok'"):
        p = cid.split(":")
        if p[0] == "debate" and p[2] == LABEL and p[3] in exposed:
            out.append(cid)
        elif p[0] == "compliance" and p[2] == LABEL and p[3] in exposed:
            out.append(cid)
        elif (p[0] == "judge" and p[1] in exposed and p[2] == LABEL and p[4] in REUSED_JUDGES
              and p[5] == "debate_k0"):
            out.append(cid)
    return sorted(out)


def seed_from_confirmation(root: Path, db: Path, questions: set[str]) -> dict[str, Any]:
    """Copy the confirmation's saved sonnet~fable cells for the exposed pilot questions into the pilot
    store at zero pilot cost. Idempotent; an existing different row under the same id is refused."""
    src = root / CF.CONFIRM_DIR / "confirm.db"
    summary = json.loads((root / CF.CONFIRM_DIR / "confirm_summary.json").read_text(encoding="utf-8"))
    if summary["decision"]["rule"] not in ("C1", "C2", "C3"):
        raise RuntimeError("the confirmation has no completed reading to reuse")
    exposed = set(json.loads((root / CF.CONFIRM_DIR / "sample.json").read_text(encoding="utf-8"))["questions"])
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
    original_cost = 0.0
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
            original_cost += cost or 0.0
        d.execute("INSERT INTO events VALUES(?,?,?)", (time.time(), "seeded_from_confirmation",
                                                         f"{len(ids)} cells from {src.name}"))
    finally:
        d.close()
    kinds = collections.Counter(cid.split(":")[0] for cid in ids)
    return {"source": str(src), "source_sha256": _sha(src.read_bytes()), "exposed_questions": sorted(exposed),
            "cells": len(ids), "by_kind": dict(kinds), "original_cost_usd": round(original_cost, 6),
            "ids_sha256": _sha("\n".join(ids).encode())}


# ------------------------------------------------------------------ qualification

def qualification_sample(root: Path, worlds: dict[str, dict]) -> list[Request]:
    """Opening-round requests of the first fresh pilot question (fixed hash order)."""
    exposed = set(json.loads((root / CF.CONFIRM_DIR / "sample.json").read_text(encoding="utf-8"))["questions"])
    fresh = [q for q in pilot_questions(root / "bench") if q["question_id"] not in exposed]
    q = min(fresh, key=lambda q: _sha(f"pilot-qualification-v1:{q['question_id']}".encode()))
    sample = list(next(debate(q, worlds[q["world_id"]]["world_text"])))
    if len(sample) != 2:
        raise ValueError("pilot qualification needs both blind opening turns")
    return sample


def qualify(root: Path, worlds: dict[str, dict], cap: float) -> dict:
    """Serial control (fresh ids, never used) against the parallel canonical opening turns, which then
    replay into the pilot. Records the receipt in the run manifest and moves the manifest to the
    current commit, keeping the prior commit in commit_history."""
    from .validation import atomic_json, input_sha256, sha

    manifest_path = root / "run_manifest.json"
    sample = qualification_sample(root, worlds)
    controls = [replace(r, custom_id=f"qualification:pilot:{QUALIFICATION_VERSION}:serial:{r.custom_id}") for r in sample]
    path = root / "pilot_qualification.json"
    fingerprint = preflight.execution_sha256()
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "execution_sha256": fingerprint, "input_sha256": input_sha256(sample), "git_commit": preflight.source_commit(),
        "host": platform.node(), "mode": "live",
        "coverage": "Sonnet honest and Fable dishonest blind opening turns of one fresh pilot question",
        "parallel_first_responses_used_in_pilot": True, "control_used_in_pilot": False}
    if record["execution_sha256"] != fingerprint or record["host"] != platform.node() \
            or record["input_sha256"] != input_sha256(sample):
        raise RuntimeError("existing pilot qualification is incompatible; preserve it before a fresh version")
    store = Store(root / "pilot.db", STAGE, cap, max_transport_attempts=6)
    try:
        for name, reqs, workers in (("serial", controls, 1), ("parallel", sample, WORKERS)):
            if record.get(name, {}).get("semantics_preserved") is True:
                continue
            if record.get(name, {}).get("semantics_preserved") is False:
                raise RuntimeError(f"{name} pilot qualification failed; paid retries need a new version")
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
        chosen = parallel if parallel["elapsed_seconds"] < serial["elapsed_seconds"] else serial
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record.update({"selected_workers": WORKERS if chosen is parallel else 1, "semantics_preserved": True,
                       "placements_checked": ["Jack's PC", "HaleysPC", "RunPod"],
                       "placement_reference": manifest["validation_execution"]["placements"]["reference"],
                       "completed_utc": datetime.now(timezone.utc).isoformat(), "stage_spend_usd": store.spent(STAGE)})
        atomic_json(path, record)
        manifest.setdefault("throughput", {})["pilot"] = record
        commit = preflight.source_commit()
        if manifest.get("git_commit") != commit:
            manifest.setdefault("commit_history", []).append(
                {"prior": manifest.get("git_commit"), "new": commit, "utc": datetime.now(timezone.utc).isoformat(),
                 "reason": "pilot launch under the 2026-10-21 Sonnet-honest amendment (Astra Ultra consults 20 onward)"})
            manifest["git_commit"] = commit
        atomic_json(manifest_path, manifest)
        return record
    finally:
        store.db.close()


# ------------------------------------------------------------------ the pilot run

def _config(qs: list[dict], worlds: dict[str, dict], judges: tuple[str, ...], cap: float) -> dict:
    from .judging import ARMS, ORDERS

    inputs = CF.frozen_inputs(qs, worlds)
    from .models import MODELS
    inputs["models"] = {m: asdict(MODELS[m]) for m in sorted(set(judges) | {HONEST, DISHONEST, ORACLE, GATE, "dspro"})}
    inputs["source_sha256"]["pilot.py"] = _sha(Path(__file__).read_bytes())
    return {"pairings": [LABEL], "stage": STAGE, "cap": cap, "judges": list(judges), "arms": list(ARMS),
            "orders": list(ORDERS), "questions": [q["question_id"] for q in qs], "oracle": ORACLE, "gate": GATE,
            "workers": WORKERS, "frozen_inputs": inputs}


def run(root: Path, worlds: dict[str, dict], judges: tuple[str, ...], cap: float) -> dict:
    from .engine import _run_live_chunks, drive
    from .judging import ARMS, ORDERS, judge_task

    qs = pilot_questions(root / "bench")
    qmap = {q["question_id"]: q for q in qs}
    base = root / PILOT_DIR
    AS.claim_dir(base, _config(qs, worlds, judges, cap))
    s = Store(root / "pilot.db", STAGE, cap, max_transport_attempts=6)
    s.db.close()  # create the schema, then seed with a plain connection
    reuse = seed_from_confirmation(root, root / "pilot.db", set(qmap))
    (base / "reused_from_confirmation.json").write_text(json.dumps(reuse, indent=2), encoding="utf-8")
    times: dict[str, float] = {}
    s = Store(root / "pilot.db", STAGE, cap, max_transport_attempts=6)
    try:
        t = time.perf_counter()
        debates, failed = drive(s, {qid: debate(q, worlds[q["world_id"]]["world_text"]) for qid, q in qmap.items()},
                                workers=WORKERS)
        times["debates_s"] = time.perf_counter() - t
        comp = [AS.compliance_request(LABEL, qmap[qid], turn) for qid, d in debates.items() for turn in d["turns"]]
        t = time.perf_counter()
        cout = _run_live_chunks(s, comp, WORKERS)
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
        judged, jfailed = drive(s, jt, workers=WORKERS)
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
                      "verdict": AS.compliance_verdict(text), "text": text})
    _export(base, debates, flags, judged)
    readout = engineering_readout(root, base, qs, debates, flags, judged, judges, reuse, failed, jfailed, spend, times)
    (base / "pilot_readout.json").write_text(json.dumps(readout, indent=2), encoding="utf-8")
    print(json.dumps({k: readout[k] for k in ("completion", "spend", "main_forecast")}, indent=2), flush=True)
    return readout


SEALED_FIELDS = ("verdict", "confidence", "correct", "reasoning", "key_label")


def _export(base: Path, debates: dict, flags: list, judged: dict) -> None:
    """Open files carry everything except non-k0 verdict outcomes; those go only to the sealed file."""
    sealed = base / "sealed"
    sealed.mkdir(parents=True, exist_ok=True)
    rows = [dict(r, judge_key=list(k)) for k, r in judged.items()]
    open_rows = [r if r["arm"] == "debate_k0" else {k: v for k, v in r.items() if k not in SEALED_FIELDS} for r in rows]
    for path, data in ((base / "debates.jsonl", debates.values()), (base / "compliance.jsonl", flags),
                       (base / "judgments_open.jsonl", open_rows), (sealed / "judgments_full.jsonl", rows)):
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in data)


def _store_rows(db: Path) -> list[tuple]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("SELECT custom_id, model, status, response, cost, attempts FROM calls").fetchall()
    finally:
        con.close()


def _component(cid: str) -> tuple[str, str]:
    p = cid.split(":")
    if p[0] == "judge":
        step = p[-1]
        return f"judge:{p[4]}:{p[5]}", "verdict" if step == "verdict" else "query_step"
    return p[0], ""


def engineering_readout(root: Path, base: Path, qs: list[dict], debates: dict, flags: list, judged: dict,
                        judges: tuple[str, ...], reuse: dict, failed: list, jfailed: list, spend: float,
                        times: dict) -> dict[str, Any]:
    from .judging import ARMS, ORDERS
    from .models import MODELS

    exposed = set(reuse["exposed_questions"])
    n_fresh = sum(q["question_id"] not in exposed for q in qs)
    rows = _store_rows(root / "pilot.db")
    reused = set()
    con = sqlite3.connect(f"file:{(root / CF.CONFIRM_DIR / 'confirm.db').as_posix()}?mode=ro", uri=True)
    try:
        reused = set(_reused_ids(con, exposed))
    finally:
        con.close()
    # Per model and call kind: statuses, finish reasons, tokens and truncation (denominator: returned responses).
    by_kind: dict[str, dict] = collections.defaultdict(lambda: {"calls": 0, "status": collections.Counter(), "out": [],
                                                                "reasoning": [], "in": [], "cost": 0.0, "fresh_cost": 0.0,
                                                                "attempts_gt1": 0})
    fresh_cost = collections.defaultdict(float)
    for cid, model, status, resp, cost, attempts in rows:
        comp, sub = _component(cid)
        kind = f"{cid.split(':')[0]}:{model}" + (f":{sub}" if sub else "")
        k = by_kind[kind]
        k["calls"] += 1
        k["status"][status] += 1
        k["attempts_gt1"] += (attempts or 0) > 1
        if resp:
            d = json.loads(resp)
            k["out"].append(d.get("output_tokens") or 0)
            k["reasoning"].append(d.get("reasoning_tokens") or 0)
            k["in"].append(d.get("input_tokens") or 0)
        k["cost"] += cost or 0
        if cid not in reused and not cid.startswith("qualification:"):
            fresh_cost[comp] += cost or 0
    calls = {}
    for kind, k in sorted(by_kind.items()):
        returned = sum(v for st, v in k["status"].items() if st in MEASURED)
        trunc = k["status"].get("truncated", 0)
        calls[kind] = {"calls": k["calls"], "status": dict(k["status"]), "retried": k["attempts_gt1"],
                       "mean_input_tokens": round(sum(k["in"]) / len(k["in"])) if k["in"] else None,
                       "mean_output_tokens": round(sum(k["out"]) / len(k["out"])) if k["out"] else None,
                       "max_output_tokens": max(k["out"]) if k["out"] else None,
                       "mean_reasoning_tokens": round(sum(k["reasoning"]) / len(k["reasoning"])) if k["reasoning"] else None,
                       "truncation": {"truncated": trunc, "returned": returned,
                                      "wilson95": wilson(trunc, returned) if returned else None},
                       "cost_usd": round(k["cost"], 4)}
    # Judge sessions: completion, refusals, parsing, query use, stopping, replacements, duplicates, gate, oracle.
    sessions: dict[str, dict] = {}
    for (qid, j, arm, o), r in judged.items():
        key = f"{j}:{arm}"
        e = sessions.setdefault(key, {"sessions": 0, "verdict_status": collections.Counter(), "unparsed": 0,
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
            claim = (qr.get("claim") or "").strip().lower()
            if "oracle" in qr:
                e["oracle"][qr["oracle"]] += 1
                e["duplicates"] += claim in seen
                seen.add(claim)
        for ev in r.get("events", []):
            if ev.get("event") == "done":
                e["done_early"] += 1
            elif ev.get("event") in ("query_step_failure", "gate_failure", "oracle_failure"):
                e["failure_events"][ev["event"]] += 1
    sessions = {k: {kk: (dict(vv) if isinstance(vv, collections.Counter) else vv) for kk, vv in v.items()}
                for k, v in sorted(sessions.items())}
    # Descriptive k0 error by judge (amendment of 2026-10-21): questions weigh equally, orders averaged,
    # unparsed or refused verdicts count wrong and are listed. No other arm's outcome is computed.
    k0: dict[str, dict] = {}
    for j in judges:
        out = {}
        for name, keep in (("exposed_40", lambda q: q in exposed), ("fresh_135", lambda q: q not in exposed),
                           ("combined", lambda q: True)):
            per_q = []
            invalid = 0
            for q in qs:
                qid = q["question_id"]
                if not keep(qid):
                    continue
                rs = [judged.get((qid, j, "debate_k0", o)) for o in ORDERS]
                if any(r is None for r in rs):
                    continue
                per_q.append(sum(not r["correct"] for r in rs) / len(rs))
                invalid += sum(not r["parse_ok"] for r in rs)
            out[name] = {"questions": len(per_q), "error": sum(per_q) / len(per_q) if per_q else None,
                         "invalid_verdicts": invalid}
        k0[j] = out
    # Compliance: flags, unknowns, and the review lists (all flagged turns, a fixed unflagged sample).
    flagged = [f["custom_id"] for f in flags if f["verdict"] == "yes"]
    unknown = [f["custom_id"] for f in flags if f["verdict"] == "unknown"]
    unflagged = sorted((f["custom_id"] for f in flags if f["verdict"] == "no"),
                       key=lambda c: _sha(f"pilot-compliance-review-v1:{c}".encode()))[:UNFLAGGED_REVIEW_SAMPLE]
    words = collections.defaultdict(list)
    regenerated = 0
    for d in debates.values():
        for t in d["turns"]:
            words[t["role"]].append(t["words"])
            regenerated += t["attempts"] > 1
    # Main forecast from fresh-work costs only (reused cells and the qualification excluded).
    per_q = {}
    for comp, c in fresh_cost.items():
        denom = n_fresh if comp in ("debate", "compliance") or any(
            comp.startswith(f"judge:{j}:debate_k0") for j in REUSED_JUDGES) else len(qs)
        per_q[comp] = c / denom if denom else 0.0
    oracle_gate = {k: v for k, v in per_q.items() if k in ("oracle", "gate")}
    live = sum(per_q.values())

    def provider_of(comp: str) -> str:
        model = {"debate": None, "compliance": "dspro", "oracle": ORACLE, "gate": GATE}.get(comp)
        if comp.startswith("judge:"):
            model = comp.split(":")[1]
        return MODELS[model].provider if model else "mixed"
    debate_batchable = 1.0  # Sonnet and Fable are both Anthropic
    batch = sum(v * (0.5 if provider_of(k) in BATCH_PROVIDERS or (k == "debate" and debate_batchable) else 1.0)
                for k, v in per_q.items())
    main_n = sum(1 for x in (root / "bench" / "main.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())
    main_cap = 3634.0
    return {
        "amendment": "Sonnet-honest pilot, 2026-10-21 (Astra Ultra consult 20)",
        "completion": {"questions": len(qs), "fresh_questions": n_fresh, "debates": len(debates),
                       "failed_debates": len(failed), "judgments": len(judged), "failed_judgments": len(jfailed),
                       "expected_judgments": len(qs) * len(judges) * len(ARMS) * len(ORDERS)},
        "spend": {"pilot_stage_usd": spend, "reused_original_cost_usd": reuse["original_cost_usd"],
                  "fresh_by_component_usd": {k: round(v, 4) for k, v in sorted(fresh_cost.items())}},
        "times_s": times, "calls": calls, "judge_sessions": sessions,
        "k0_error_descriptive": k0,
        "compliance": {"turns": len(flags), "flagged": len(flagged), "unknown": len(unknown),
                       "flagged_ids": flagged, "unknown_ids": unknown, "unflagged_review_sample": unflagged},
        "debate_turns": {"mean_words": {r: round(sum(v) / len(v)) for r, v in words.items()},
                         "regenerated_turns": regenerated},
        "main_forecast": {"per_question_live_usd": round(live, 4), "per_question_batch_usd": round(batch, 4),
                          "per_question_by_component_usd": {k: round(v, 4) for k, v in sorted(per_q.items())},
                          "oracle_and_gate_per_question_usd": {k: round(v, 4) for k, v in oracle_gate.items()},
                          "main_questions": main_n, "main_live_usd": round(live * main_n, 2),
                          "main_batch_usd": round(batch * main_n, 2), "main_cap_usd": main_cap,
                          "affordable_questions_live": math.floor(main_cap / live) if live else None,
                          "affordable_questions_batch": math.floor(main_cap / batch) if batch else None,
                          "note": "fresh-work list prices from the live pilot; batch halves Anthropic and OpenAI only; "
                                  "excludes oracle-selection, retries beyond those observed, and cache differences under batch"},
    }
