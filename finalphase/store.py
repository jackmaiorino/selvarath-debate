"""Durable call store and dispatcher.

Every request is keyed by custom_id and bound to the hash of its exact content. A
finished call is never sent again, and a changed request under an old id is refused.
Batches are recorded before polling, so a restart reconnects instead of resubmitting.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable

from .models import spec
from .providers import BATCH, LIVE, Request, Response, TransportError

MEASURED = ("ok", "truncated", "refusal", "error")
MAX_TRANSPORT_ATTEMPTS = 6


class CapExceeded(RuntimeError):
    pass


def _hash(req: Request) -> str:
    return hashlib.sha256(req.to_json().encode("utf-8")).hexdigest()


def estimate_max_cost(req: Request, batch: bool) -> float:
    m = spec(req.model)
    chars = len(req.system) + sum(len(c) for _, c in req.messages)
    tin = chars / 3.0 + 50
    c = (tin * m.price_in + req.max_tokens * m.price_out) / 1e6
    return c * (0.5 if batch and m.batch else 1.0)


class Store:
    def __init__(self, path: str | Path, stage: str, cap_usd: float):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stage = stage
        self.cap = cap_usd
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS calls(
              custom_id TEXT PRIMARY KEY, stage TEXT, model TEXT, req_hash TEXT, request TEXT,
              status TEXT, response TEXT, cost REAL DEFAULT 0, batch_id TEXT, attempts INTEGER DEFAULT 0,
              updated REAL);
            CREATE TABLE IF NOT EXISTS batches(
              batch_id TEXT PRIMARY KEY, provider TEXT, model TEXT, stage TEXT, n INTEGER,
              status TEXT, submitted REAL, collected INTEGER DEFAULT 0, info TEXT);
            CREATE TABLE IF NOT EXISTS events(t REAL, kind TEXT, detail TEXT);
            """
        )

    # ------------------------------------------------------------ bookkeeping
    def _q(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self.db.execute(sql, params).fetchall()

    def log(self, kind: str, detail: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (time.time(), kind, detail[:4000]))

    def spent(self, stage: str | None = None) -> float:
        q = "SELECT COALESCE(SUM(cost),0) FROM calls" + (" WHERE stage=?" if stage else "")
        return float(self._q(q, (stage,) if stage else ())[0][0])

    def get(self, custom_id: str) -> Response | None:
        rows = self._q("SELECT status, response FROM calls WHERE custom_id=?", (custom_id,))
        row = rows[0] if rows else None
        if not row or row[0] not in MEASURED:
            return None
        return Response(**json.loads(row[1]))

    def _register(self, req: Request) -> str:
        h = _hash(req)
        with self._lock:
            row = self.db.execute("SELECT req_hash, status FROM calls WHERE custom_id=?", (req.custom_id,)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO calls(custom_id, stage, model, req_hash, request, status, updated) VALUES(?,?,?,?,?,?,?)",
                    (req.custom_id, self.stage, req.model, h, req.to_json(), "pending", time.time()),
                )
                return "pending"
            if row[0] != h:
                raise ValueError(f"request content changed for existing id {req.custom_id}")
            return row[1]

    def _save(self, r: Response, attempts_inc: int = 1) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE calls SET status=?, response=?, cost=cost+?, attempts=attempts+?, updated=? WHERE custom_id=?",
                (r.status, json.dumps(asdict(r), ensure_ascii=False), r.cost, attempts_inc, time.time(), r.custom_id),
            )

    def _mark(self, custom_id: str, status: str, batch_id: str | None = None, attempts_inc: int = 0) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE calls SET status=?, batch_id=COALESCE(?, batch_id), attempts=attempts+?, updated=? WHERE custom_id=?",
                (status, batch_id, attempts_inc, time.time(), custom_id),
            )

    def _attempts(self, custom_id: str) -> int:
        return int(self._q("SELECT attempts FROM calls WHERE custom_id=?", (custom_id,))[0][0])

    def _check_cap(self, todo: list[Request], batch: bool) -> None:
        committed = self.spent(self.stage) + sum(estimate_max_cost(r, batch) for r in todo)
        if committed > self.cap:
            raise CapExceeded(f"stage {self.stage}: spent {self.spent(self.stage):.2f} + max estimate of {len(todo)} calls exceeds cap {self.cap:.2f}")

    # ------------------------------------------------------------ dispatch
    def run(self, reqs: Iterable[Request], mode: str = "live", workers: int = 8,
            on_done: Callable[[Response], None] | None = None, poll_s: int = 60) -> dict[str, Response]:
        reqs = list(reqs)
        for r in reqs:
            self._register(r)
        todo = [r for r in reqs if self.get(r.custom_id) is None]
        if todo:
            self._check_cap([r for r in todo if not self._in_flight(r.custom_id)], mode == "batch")
        if mode == "batch":
            self._run_batch(todo, poll_s)
        else:
            self._run_live(todo, workers, on_done)
        return {r.custom_id: x for r in reqs if (x := self.get(r.custom_id)) is not None}

    def _in_flight(self, custom_id: str) -> bool:
        rows = self._q("SELECT status FROM calls WHERE custom_id=?", (custom_id,))
        return bool(rows and rows[0][0] == "submitted")

    def call_one(self, req: Request) -> Response:
        """Synchronous single call with transport retries (used inside multi-turn loops)."""
        self._register(req)
        got = self.get(req.custom_id)
        if got is not None:
            return got
        self._check_cap([req], False)
        m = spec(req.model)
        while True:
            try:
                r = LIVE[m.provider](req, m)
                self._save(r)
                return r
            except TransportError as e:
                self._mark(req.custom_id, "pending", attempts_inc=1)
                n = self._attempts(req.custom_id)
                self.log("transport", f"{req.custom_id} attempt {n}: {e}")
                if n >= MAX_TRANSPORT_ATTEMPTS:
                    self._mark(req.custom_id, "transport_failed")
                    raise
                time.sleep(min(2 ** n * 5, 300))

    def _run_live(self, todo: list[Request], workers: int, on_done) -> None:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {ex.submit(self.call_one, r): r for r in todo}
            for f in as_completed(futs):
                try:
                    r = f.result()
                    if on_done:
                        on_done(r)
                except TransportError:
                    pass

    def _run_batch(self, todo: list[Request], poll_s: int) -> None:
        # reconnect to batches already submitted for these ids
        pending_batches = {
            row[0] for row in self._q(
                "SELECT DISTINCT batch_id FROM calls WHERE status='submitted' AND batch_id IS NOT NULL")
        }
        fresh = [r for r in todo if not self._in_flight(r.custom_id)]
        by_model: dict[str, list[Request]] = {}
        for r in fresh:
            by_model.setdefault(r.model, []).append(r)
        for key, rs in by_model.items():
            m = spec(key)
            if m.provider not in BATCH:
                self._run_live(rs, 8, None)
                continue
            submit = BATCH[m.provider][0]
            for i in range(0, len(rs), 5000):
                chunk = rs[i:i + 5000]
                bid = submit(chunk, m, f"{self.stage}:{key}")
                with self._lock:
                    self.db.execute("INSERT INTO batches VALUES(?,?,?,?,?,?,?,0,?)",
                                    (bid, m.provider, key, self.stage, len(chunk), "submitted", time.time(), ""))
                for r in chunk:
                    self._mark(r.custom_id, "submitted", batch_id=bid, attempts_inc=1)
                pending_batches.add(bid)
                self.log("batch_submit", f"{bid} {key} n={len(chunk)}")
        while pending_batches:
            for bid in list(pending_batches):
                prov, key = self._q("SELECT provider, model FROM batches WHERE batch_id=?", (bid,))[0]
                _, poll, collect = BATCH[prov]
                status, info = poll(bid)
                if status != "ended":
                    continue
                results = collect(bid, spec(key))
                ids = [row[0] for row in self._q("SELECT custom_id FROM calls WHERE batch_id=? AND status='submitted'", (bid,))]
                for cid in ids:
                    res = results.get(cid)
                    if isinstance(res, Response):
                        self._save(res, attempts_inc=0)
                    else:
                        self._mark(cid, "pending")
                        self.log("batch_item_retry", f"{bid} {cid} {res}")
                with self._lock:
                    self.db.execute("UPDATE batches SET status='ended', collected=1, info=? WHERE batch_id=?",
                                    (json.dumps(info, default=str)[:20000], bid))
                pending_batches.discard(bid)
            if pending_batches:
                time.sleep(poll_s)
        # anything that came back as a transport failure is finished live
        retry = [Request(**_req_fields(json.loads(row[0]))) for row in self._q(
            "SELECT request FROM calls WHERE status='pending' AND stage=?", (self.stage,))]
        retry = [r for r in retry if r.custom_id in {t.custom_id for t in todo}]
        if retry:
            self._run_live(retry, 8, None)


def _req_fields(d: dict) -> dict:
    d["messages"] = tuple(tuple(x) for x in d["messages"])
    return d
