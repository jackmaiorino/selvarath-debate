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
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable

from .models import spec
from .providers import BATCH, LIVE, BatchRejected, Request, Response, TransportError, batch_request_id, openai_queued_input_tokens
from .limits import BatchLimits, input_token_bound

MEASURED = ("ok", "truncated", "refusal", "error")
MAX_TRANSPORT_ATTEMPTS = 6


class CapExceeded(RuntimeError):
    pass


def _hash(req: Request) -> str:
    return hashlib.sha256(req.to_json().encode("utf-8")).hexdigest()


def estimate_max_cost(req: Request, batch: bool) -> float:
    return float(estimate_max_cost_decimal(req, batch))


def estimate_max_cost_decimal(req: Request, batch: bool) -> Decimal:
    m = spec(req.model)
    # A byte bound also covers unusually dense text; average chars/token is not a maximum.
    tin = len(req.system.encode("utf-8")) + sum(len(c.encode("utf-8")) for _, c in req.messages) + 100
    c = (Decimal(tin) * Decimal(str(m.price_in)) + Decimal(req.max_tokens) * Decimal(str(m.price_out))) / 1000000
    return c / 2 if batch and m.batch else c


class Store:
    def __init__(self, path: str | Path, stage: str, cap_usd: float,
                 max_transport_attempts: int = MAX_TRANSPORT_ATTEMPTS,
                 batch_limits: dict[str, BatchLimits] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stage = stage
        self.cap = cap_usd
        if not 1 <= max_transport_attempts <= MAX_TRANSPORT_ATTEMPTS:
            raise ValueError("invalid maximum transport attempts")
        self.max_transport_attempts = max_transport_attempts
        self.batch_limits = batch_limits or {}
        self._reserved_ids: set[str] = set()
        self._live_backpressure: dict[str, float] = {}
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
            self.db.execute("INSERT INTO events VALUES(?,?,?)", (time.time(), kind, detail))

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
        outstanding = {r.custom_id: r for r in todo}
        # A pending row never attempted was registered but not sent (e.g. a refused round),
        # so it cannot carry a charge; anything attempted or in flight stays reserved.
        for (raw,) in self._q("SELECT request FROM calls WHERE stage=? AND status NOT IN (?,?,?,?)"
                              " AND NOT (status='pending' AND attempts=0)", (self.stage, *MEASURED)):
            r = Request(**_req_fields(json.loads(raw)))
            outstanding[r.custom_id] = r
        # Frozen rates have at most nanodollar precision. Discard only binary
        # float noise in saved charges; sum request reservations exactly.
        settled = Decimal(str(self.spent(self.stage))).quantize(Decimal("0.000000001"))
        committed = settled + sum((estimate_max_cost_decimal(r, batch) for r in outstanding.values()), Decimal(0))
        if committed > Decimal(str(self.cap)):
            raise CapExceeded(f"stage {self.stage}: spent {self.spent(self.stage):.2f} + max estimate of {len(todo)} calls exceeds cap {self.cap:.2f}")

    # ------------------------------------------------------------ dispatch
    def run(self, reqs: Iterable[Request], mode: str = "live", workers: int = 8,
            on_done: Callable[[Response], None] | None = None, poll_s: int = 300,
            allow_live_fallback: bool = True) -> dict[str, Response]:
        reqs = list(reqs)
        for r in reqs:
            self._register(r)
        todo = [r for r in reqs if self.get(r.custom_id) is None]
        if todo:
            self._check_cap(todo, mode == "batch")
        reserved = {r.custom_id for r in todo}
        self._reserved_ids.update(reserved)
        try:
            if mode == "batch":
                self._run_batch(todo, poll_s, allow_live_fallback, workers)
            else:
                self._run_live(todo, workers, on_done)
        finally:
            self._reserved_ids.difference_update(reserved)
        return {r.custom_id: x for r in reqs if (x := self.get(r.custom_id)) is not None}

    def _in_flight(self, custom_id: str) -> bool:
        rows = self._q("SELECT status FROM calls WHERE custom_id=?", (custom_id,))
        return bool(rows and rows[0][0] in ("submitted", "submitting", "submission_unknown", "live_in_flight"))

    def call_one(self, req: Request) -> Response:
        """Synchronous single call with transport retries (used inside multi-turn loops)."""
        self._register(req)
        got = self.get(req.custom_id)
        if got is not None:
            return got
        if self._in_flight(req.custom_id):
            raise TransportError(f"possibly billable live call needs reconciliation: {req.custom_id}")
        if self._attempts(req.custom_id) >= self.max_transport_attempts:
            raise TransportError(f"transport attempt allowance exhausted for {req.custom_id}")
        if req.custom_id not in self._reserved_ids:
            self._check_cap([req], False)
        m = spec(req.model)
        while True:
            try:
                delay = self._live_backpressure.get(m.provider, 0) - time.time()
                if delay > 0:
                    time.sleep(delay)
                self._mark(req.custom_id, "live_in_flight", attempts_inc=1)
                r = LIVE[m.provider](req, m)
                self._save(r, attempts_inc=0)
                return r
            except BatchRejected as error:
                with self._lock:
                    self.db.execute("UPDATE calls SET status='pending',attempts=attempts-1,updated=? WHERE custom_id=?",
                                    (time.time(), req.custom_id))
                    self._live_backpressure[m.provider] = time.time() + error.retry_after
                self.log("live_rejected_unsent", f"{req.custom_id}: {error}")
                if not error.retryable:
                    raise
            except TransportError as e:
                n = self._attempts(req.custom_id)
                self.log("transport", f"{req.custom_id} attempt {n}: {e}")
                if n >= self.max_transport_attempts:
                    self._mark(req.custom_id, "transport_failed")
                    raise
                self._mark(req.custom_id, "pending")
                time.sleep(min(2 ** n * 5, 300))
            except BaseException:
                # Even a process interruption after dispatch is possibly billable.
                self._mark(req.custom_id, "submission_unknown")
                raise

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

    def _run_batch(self, todo: list[Request], poll_s: int, allow_live_fallback: bool = True,
                   workers: int = 8) -> None:
        ambiguous = self._q("SELECT custom_id FROM calls WHERE stage=? AND status IN (?,?,?)",
                            (self.stage, "submitting", "submission_unknown", "live_in_flight"))
        if ambiguous:
            raise TransportError(f"possibly billable requests need reconciliation: {ambiguous}")
        # reconnect to batches already submitted for these ids
        pending_batches = {
            row[0] for row in self._q(
                "SELECT DISTINCT batch_id FROM calls WHERE status='submitted' AND batch_id IS NOT NULL")
        }
        fresh = [r for r in todo if not self._in_flight(r.custom_id)]
        if any(self._attempts(r.custom_id) >= self.max_transport_attempts for r in fresh):
            raise TransportError("batch attempt allowance exhausted; reserve a retry before resubmission")
        by_model: dict[str, list[Request]] = {}
        for r in fresh:
            by_model.setdefault(r.model, []).append(r)
        live_only = []
        waves: dict[str, list[list[Request]]] = {}
        for key, rs in by_model.items():
            m = spec(key)
            if m.provider not in BATCH:
                live_only.extend(rs)
                continue
            limits = self.batch_limits.get(key)
            waves[key] = limits.waves(rs) if limits else [rs[i:i + 5000] for i in range(0, len(rs), 5000)]
        deferred_until: dict[str, float] = {}

        def submit_ready() -> None:
            for key, remaining in waves.items():
                m = spec(key)
                submit = BATCH[m.provider][0]
                limits = self.batch_limits.get(key)
                while remaining:
                    active = self._q("SELECT batch_id FROM batches WHERE model=? AND collected=0", (key,))
                    if limits and len(active) >= limits.max_in_flight:
                        break
                    if time.time() < deferred_until.get(key, 0):
                        break
                    chunk = remaining[0]
                    tokens = sum(input_token_bound(r) for r in chunk)
                    if limits:
                        occupied = sum(input_token_bound(Request(**_req_fields(json.loads(raw))))
                                       for (raw,) in self._q("SELECT request FROM calls WHERE model=? AND status='submitted'", (key,)))
                        external = openai_queued_input_tokens(m, pending_batches) if m.provider == "openai" else 0
                        if occupied + external + tokens > limits.max_input_tokens:
                            self.log("queue_backpressure", f"{key} own={occupied} external_bound={external} next={tokens}")
                            deferred_until[key] = time.time() + max(poll_s, 300)
                            break
                    # One durable transaction before networking closes the
                    # crash window between accepted submission and receipt.
                    with self._lock, self.db:
                        self.db.execute("BEGIN IMMEDIATE")
                        for r in chunk:
                            self._mark(r.custom_id, "submitting", attempts_inc=1)
                    try:
                        bid = submit(chunk, m, f"{self.stage}:{key}")
                    except BatchRejected as error:
                        with self._lock, self.db:
                            self.db.execute("BEGIN IMMEDIATE")
                            for r in chunk:
                                self.db.execute("UPDATE calls SET status='pending',attempts=attempts-1,updated=? WHERE custom_id=?",
                                                (time.time(), r.custom_id))
                        self.log("batch_rejected_unsent", f"{key}: {error}")
                        if not error.retryable:
                            raise
                        deferred_until[key] = time.time() + max(error.retry_after, poll_s)
                        break
                    except BaseException as error:
                        for r in chunk:
                            self._mark(r.custom_id, "submission_unknown")
                        self.log("batch_submission_unknown", f"{key}: {type(error).__name__}: {error} notes={getattr(error, '__notes__', [])}")
                        raise
                    with self._lock, self.db:
                        self.db.execute("BEGIN IMMEDIATE")
                        self.db.execute("INSERT INTO batches VALUES(?,?,?,?,?,?,?,0,?)",
                                        (bid, m.provider, key, self.stage, len(chunk), "submitted", time.time(), ""))
                        for r in chunk:
                            self._mark(r.custom_id, "submitted", batch_id=bid)
                    remaining.pop(0)
                    pending_batches.add(bid)
                    self.log("batch_submit", f"{bid} {key} n={len(chunk)} input_token_bound={tokens}")
        submit_ready()
        # Start every asynchronous provider batch before doing live-only work.
        # Together can now finish concurrently with both frontier backends.
        if live_only:
            self._run_live(live_only, workers, None)
        snapshots: dict[str, tuple[str, str]] = {}
        unchanged = 0
        while pending_batches or any(waves.values()):
            changed = False
            for bid in list(pending_batches):
                prov, key = self._q("SELECT provider, model FROM batches WHERE batch_id=?", (bid,))[0]
                _, poll, collect = BATCH[prov]
                status, info = poll(bid)
                snapshot = (status, json.dumps(info.get("request_counts", {}), sort_keys=True, default=str))
                if snapshots.get(bid) != snapshot:
                    snapshots[bid] = snapshot
                    changed = True
                    with self._lock:
                        self.db.execute("UPDATE batches SET status=?, info=? WHERE batch_id=?",
                                        (status, json.dumps(info, default=str), bid))
                if status != "ended":
                    continue
                results = collect(bid, spec(key))
                ids = [row[0] for row in self._q("SELECT custom_id FROM calls WHERE batch_id=? AND status='submitted'", (bid,))]
                for cid in ids:
                    res = results.get(cid, results.get(batch_request_id(cid, prov)))
                    if isinstance(res, Response):
                        res.custom_id = cid
                        self._save(res, attempts_inc=0)
                    else:
                        self._mark(cid, "pending")
                        self.log("batch_item_retry", f"{bid} {cid} {res}")
                with self._lock:
                    self.db.execute("UPDATE batches SET status='ended', collected=1, info=? WHERE batch_id=?",
                                    (json.dumps(info, default=str), bid))
                pending_batches.discard(bid)
            submit_ready()
            if pending_batches or any(waves.values()):
                unchanged = 0 if changed else unchanged + 1
                delay = min(poll_s * 3, max(poll_s, 900)) if unchanged >= 2 else poll_s
                time.sleep(delay)
        # anything that came back as a transport failure is finished live
        retry = [Request(**_req_fields(json.loads(row[0]))) for row in self._q(
            "SELECT request FROM calls WHERE status='pending' AND stage=?", (self.stage,))]
        retry = [r for r in retry if r.custom_id in {t.custom_id for t in todo}]
        if retry and allow_live_fallback:
            self._run_live(retry, workers, None)


def _req_fields(d: dict) -> dict:
    d["messages"] = tuple(tuple(x) for x in d["messages"])
    return d
