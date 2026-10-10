"""Round-based driver for generator tasks.

A task is a generator that yields a Request (or a list of Requests) and receives the
matching Response (or list) back, then finally returns a record. The driver gathers the
pending requests of every task, dispatches them together through the Store (live or
batch), and feeds the answers back. Requests are deterministic functions of earlier
answers, so rerunning a task replays from the store without new provider calls.
"""
from __future__ import annotations

from typing import Any, Callable, Generator, Hashable

from .providers import Request, Response
from .store import CapExceeded, Store

Task = Generator[Any, Any, dict]


def drive(store: Store, tasks: dict[Hashable, Task], mode: str = "live", workers: int = 16,
          progress: Callable[[int, int, int], None] | None = None) -> tuple[dict[Hashable, dict], list[Hashable]]:
    """Returns (records by task key, keys of tasks stopped by unrecoverable transport failure)."""
    pending: dict[Hashable, tuple[bool, list[Request]]] = {}
    results: dict[Hashable, dict] = {}
    failed: list[Hashable] = []

    def advance(k, g, value):
        try:
            y = g.send(value)
        except StopIteration as stop:
            results[k] = stop.value
            return
        pending[k] = (isinstance(y, list), y if isinstance(y, list) else [y])

    for k, g in tasks.items():
        advance(k, g, None)
    rnd = 0
    while pending:
        rnd += 1
        uniq: dict[str, Request] = {}
        for _, rs in pending.values():
            for r in rs:
                if r.custom_id in uniq and uniq[r.custom_id] != r:
                    raise ValueError(f"two tasks built different requests under {r.custom_id}")
                uniq[r.custom_id] = r
        out = _run_live_chunks(store, list(uniq.values()), workers) if mode == "live" else \
            store.run(list(uniq.values()), mode=mode, workers=workers)
        cur, pending = pending, {}
        for k, (is_list, rs) in cur.items():
            resps: list[Response | None] = [out.get(r.custom_id) for r in rs]
            if any(x is None for x in resps):
                failed.append(k)
                continue
            advance(k, tasks[k], resps if is_list else resps[0])
        if progress:
            progress(rnd, len(pending), len(results))
    return results, failed


def _run_live_chunks(store: Store, reqs: list[Request], workers: int) -> dict[str, Response]:
    """Reserve and send a live round a worker-width chunk at a time.

    The store reserves each request's worst case before sending, so a whole round of
    long-output turns can exceed a cap that real spend never approaches. A refused
    chunk is retried smaller (the cap check runs before any dispatch); a single
    request that still does not fit is a real cap stop.
    """
    out: dict[str, Response] = {}
    i, size = 0, workers
    while i < len(reqs):
        chunk = reqs[i:i + size]
        try:
            out.update(store.run(chunk, mode="live", workers=workers))
        except CapExceeded:
            if size == 1:
                raise
            size = max(1, size // 2)
            continue
        i += len(chunk)
    return out
