"""Outcome-blind technical completion of the Sonnet-honest confirmation (amendment of 2026-10-21).

Kept outside confirm.py so the confirmation's bound source hash is unchanged: the run configuration
binds confirm.py, and the completed run must replay under exactly the reviewed code.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import asym as AS
from .confirm import (ARMS, CHECK_ARMS, CHECK_CAP, CHECK_STAGE, CONFIRM_DIR, GATE, JUDGES, K0_CAP, K0_STAGE,
                      MAX_ATTEMPTS, ORACLE, frozen_inputs, sample)
from .store import Store

# Amendment of 2026-10-21 (Astra Ultra consult 16, approved by the coordinator within the owner's
# $140 approval): the original run ended R0 because these three Llama-70B k0 verdict calls got only
# Together 503s on both permitted attempts and never returned. Exactly one additional attempt each,
# identical stored requests, after two neutral recovery probes; if any still fails, R0 is final.
# Nothing else changes; the original R0 report is preserved.
COMPLETION_IDS = ("judge:W038-Q02:astra:uncapped:llama70:debate_k0:key_a:verdict",
                  "judge:W135-Q07:sonnet~astra:uncapped:llama70:debate_k0:key_a:verdict",
                  "judge:W135-Q07:astra:uncapped:llama70:debate_k0:key_b:verdict")
COMPLETION_ATTEMPTS = 3  # lifetime attempts per listed id: the original two plus one
PROBE_GAP_S, SETTLE_S, DISPATCH_GAP_S = 60, 300, 60
# The probes' cap comes out of the k0 cap, so k0 + probes + checks stays within $140 (consult 17).
PROBE_CAP = 0.05
COMPLETION_K0_CAP = K0_CAP - PROBE_CAP


class CompletionFailed(RuntimeError):
    """The bounded completion did not obtain every listed response; R0 stands as final."""


def _history(db: Path, ids: tuple[str, ...]) -> dict[str, Any]:
    """Append-only evidence per id: attempts, status and the transport events logged so far."""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for cid in ids:
            status, attempts = con.execute("SELECT status, attempts FROM calls WHERE custom_id=?", (cid,)).fetchone()
            events = con.execute("SELECT COUNT(*) FROM events WHERE kind='transport' AND detail LIKE ?", (cid + " %",)).fetchone()[0]
            out[cid] = {"status": status, "attempts": attempts, "transport_events": events}
        return out
    finally:
        con.close()


def _rows_digest(db: Path, exclude: tuple[str, ...]) -> dict[str, Any]:
    """Hash of every saved call except the listed ids (id, request hash, status, response, cost)."""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT custom_id, req_hash, status, response, cost FROM calls ORDER BY custom_id").fetchall()
    finally:
        con.close()
    kept = [r for r in rows if r[0] not in exclude]
    h = hashlib.sha256()
    for r in kept:
        h.update(json.dumps(r, ensure_ascii=False).encode("utf-8") + b"\n")
    return {"calls": len(kept), "sha256": h.hexdigest(),
            "k0_judgments_ok": sum(1 for r in kept if r[0].startswith("judge:") and r[2] == "ok")}


def complete_transport_failures(root: Path, worlds: dict[str, dict], sleep=None, now=None,
                                ids: tuple[str, ...] = COMPLETION_IDS) -> dict:
    """Outcome-blind completion of exactly COMPLETION_IDS; reads no verdict text or error count."""
    import time

    from .judging import ORDERS
    from .providers import Request, TransportError
    from .confirm import _stage_spend as _spend
    from .store import _hash, _req_fields, estimate_max_cost

    def _stage_spend(path: Path) -> float | None:
        return _spend(path, K0_STAGE)

    sleep = sleep or time.sleep
    now = now or time.time
    qs, _ = sample(root / "bench")
    base = root / CONFIRM_DIR
    db = base / "confirm.db"
    config = {"pairings": list(ARMS), "stage": K0_STAGE, "cap": K0_CAP, "check_stage": CHECK_STAGE, "check_cap": CHECK_CAP,
              "judges": list(JUDGES), "orders": list(ORDERS), "questions": sorted(q["question_id"] for q in qs),
              "max_attempts": MAX_ATTEMPTS, "check_arms": list(CHECK_ARMS), "oracle": ORACLE, "gate": GATE,
              "frozen_inputs": frozen_inputs(qs, worlds)}
    AS.claim_dir(base, config)  # the same frozen configuration, or refuse
    original = base / "confirm_summary_r0_original.json"
    if not original.exists():
        original.write_bytes((base / "confirm_summary.json").read_bytes())
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        stored = {cid: (status, attempts, req, rh) for cid, status, attempts, req, rh in con.execute(
            "SELECT custom_id, status, attempts, request, req_hash FROM calls WHERE custom_id IN (%s)" % ",".join("?" * len(ids)), ids)}
        unanswered = [cid for (cid,) in con.execute(
            "SELECT custom_id FROM calls WHERE status NOT IN ('ok','truncated','refusal','error')")]
    finally:
        con.close()
    if sorted(unanswered) != sorted(ids):
        raise RuntimeError(f"unanswered calls are not exactly the listed ids: {sorted(unanswered)}")
    for cid in ids:
        status, attempts, _, _ = stored[cid]
        if status != "transport_failed" or attempts != MAX_ATTEMPTS:
            raise RuntimeError(f"{cid} is {status} after {attempts} attempts; not eligible")
    # Prevalidate every request (identity and cap) before anything is sent.
    reqs = {}
    for cid in ids:
        req = Request(**_req_fields(json.loads(stored[cid][2])))
        if _hash(req) != stored[cid][3]:
            raise RuntimeError(f"rebuilt request differs from the stored one: {cid}")
        reqs[cid] = req
    settled = sum(v for v in [_stage_spend(db)] if v is not None)
    if settled + sum(estimate_max_cost(r, False) for r in reqs.values()) > COMPLETION_K0_CAP:
        raise RuntimeError(f"k0 spend {settled:.2f} plus the listed requests' worst case exceeds {COMPLETION_K0_CAP}")
    record: dict[str, Any] = {"amendment": "Astra Ultra consult 16, 2026-10-21", "ids": list(ids),
                              "lifetime_attempts": COMPLETION_ATTEMPTS, "k0_spend_before_usd": settled,
                              "completion_k0_cap_usd": COMPLETION_K0_CAP, "probe_cap_usd": PROBE_CAP,
                              "history_before": _history(db, ids), "before": _rows_digest(db, ids)}
    out = base / "completion_record.json"

    def save(disposition: str | None = None) -> None:
        if disposition:
            record["disposition"] = disposition
        out.write_text(json.dumps(record, indent=2), encoding="utf-8")

    # Recovery: two tiny neutral, unscored requests to the same model, at least PROBE_GAP_S apart.
    probes = Store(base / "completion_probe.db", "completion_probe", PROBE_CAP, max_transport_attempts=1)
    try:
        record["probes"] = []
        for i in range(2):
            if i:
                sleep(PROBE_GAP_S)
            pr = Request(f"probe:llama70:{int(now())}:{i}", "llama70", "You are a helpful assistant.",
                         (("user", "Reply with the single word: ready"),), 8)
            try:
                r = probes.call_one(pr)
                record["probes"].append({"id": pr.custom_id, "status": r.status, "model_id": r.model_id, "t": now()})
            except TransportError as e:
                record["probes"].append({"id": pr.custom_id, "status": "transport_failed", "detail": str(e)[:200]})
        record["probe_spend_usd"] = probes.spent("completion_probe")
    finally:
        probes.db.close()
    if any(p["status"] != "ok" for p in record["probes"]):
        save("not dispatched: recovery probes failed (probe again later)")
        raise RuntimeError("recovery probes did not both succeed; nothing dispatched (probe again later)")
    save("probes passed; dispatch pending")

    sleep(SETTLE_S)
    s = Store(db, K0_STAGE, COMPLETION_K0_CAP, max_transport_attempts=COMPLETION_ATTEMPTS)
    record["dispatch"] = []
    try:
        for i, cid in enumerate(ids):
            if i:
                sleep(DISPATCH_GAP_S)
            try:
                r = s.call_one(reqs[cid])
                record["dispatch"].append({"id": cid, "status": r.status, "model_id": r.model_id, "cost": r.cost, "t": now()})
            except TransportError as e:
                record["dispatch"].append({"id": cid, "status": "transport_failed", "detail": str(e)[:200], "t": now()})
            save("dispatch in progress")
    except BaseException as e:
        record["error"] = f"{type(e).__name__}: {e}"[:300]
        save("interrupted: reconcile the listed ids from the store before any further action")
        raise
    finally:
        s.db.close()
    record["history_after"] = _history(db, ids)
    record["after"] = _rows_digest(db, ids)
    record["k0_spend_after_usd"] = _stage_spend(db)
    record["unchanged_other_calls"] = record["after"] == record["before"]
    done = all(d["status"] == "ok" for d in record["dispatch"]) and record["unchanged_other_calls"]
    save("complete: run confirm-sonnet" if done else "R0 final")
    print(json.dumps(record, indent=2), flush=True)
    if not done:
        raise CompletionFailed("bounded completion did not obtain every listed response, or other calls changed; R0 is final")
    return record
