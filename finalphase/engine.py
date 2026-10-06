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
from .store import Store

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
        out = store.run(list(uniq.values()), mode=mode, workers=workers)
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
