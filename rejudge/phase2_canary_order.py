"""Execution order for the canary, and the durable record of which cells are done.

Two concerns the runner must settle before it makes a single call.

**Order.** A cell may only run once every cell it depends on has produced a result:
transcripts gate their judgments, and ``batch_same_qa_b2`` additionally gates on the paired
``sequential_b2`` judgment whose question-and-answer pairs it replays. Beyond correctness the
order is operational. The query-producing arms are the only cells that pause for reviewer
labelling, so scheduling every other cell first banks as much work as possible before the
first pause and clusters the payloads into batches worth labelling.

**Resume.** The run will be interrupted repeatedly, by design: every pause for labelling is an
interruption. A completed cell must never run twice, because re-running it re-spends it. The
record is append-only, hash-chained and fsynced, matching the durability contract the rest of
the phase-2 machinery uses.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from rejudge.phase2_canary_cells import ResolvedCell


class MissingDependency(ValueError):
    """Raised when a cell depends on a cell that is not part of the run."""


class DependencyCycle(ValueError):
    """Raised when the dependency graph cannot be linearised."""


def _priority(cell: ResolvedCell) -> tuple:
    """Deterministic tie-break among cells that are ready to run.

    Cells that cannot pause come first, so a pause for reviewer labelling interrupts as
    little unbanked work as possible. Transcripts lead, since everything else waits on them.
    The trailing cell_key makes the order total, and therefore reproducible.
    """
    return (cell.produces_queries, not cell.is_transcript, cell.kind, cell.condition,
            cell.cell_key)


def execution_order(cells: Sequence[ResolvedCell]) -> list[ResolvedCell]:
    """Linearise the cells so every dependency precedes its dependents.

    Refuses rather than reorders when the graph is unsatisfiable: a missing dependency means
    the run is not the frozen plan, and a cycle means it cannot be executed at all.
    """
    by_key = {cell.cell_key: cell for cell in cells}
    outstanding = {key: set(cell.dependency_keys) for key, cell in by_key.items()}
    for key, dependencies in outstanding.items():
        missing = dependencies - by_key.keys()
        if missing:
            raise MissingDependency(
                f"cell {key} depends on {sorted(missing)!r}, which is not part of this run")

    dependents: dict[str, list[str]] = {key: [] for key in by_key}
    for key, dependencies in outstanding.items():
        for dependency in dependencies:
            dependents[dependency].append(key)

    ready = sorted((by_key[k] for k, d in outstanding.items() if not d), key=_priority)
    ordered: list[ResolvedCell] = []
    while ready:
        cell = ready.pop(0)
        ordered.append(cell)
        newly_ready = []
        for dependent in dependents[cell.cell_key]:
            outstanding[dependent].discard(cell.cell_key)
            if not outstanding[dependent]:
                newly_ready.append(by_key[dependent])
        if newly_ready:
            ready = sorted(ready + newly_ready, key=_priority)

    if len(ordered) != len(by_key):
        stuck = sorted(set(by_key) - {cell.cell_key for cell in ordered})
        raise DependencyCycle(
            f"{len(stuck)} cells cannot be scheduled; a dependency cycle involves {stuck[:5]!r}")
    return ordered


class CellResultStore:
    """Append-only, hash-chained, fsynced record of completed cells."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._results: dict[str, Any] = {}
        # This store is both the durability record and the completion CLAIM, so the lock
        # carries two jobs. It keeps the hash chain intact against interleaved appends, and
        # it makes "has this cell already been recorded?" atomic with recording it, which is
        # what stops two workers executing and billing the same cell. Excluding a second
        # process remains the archive lease's job.
        self._lock = threading.Lock()
        self._last_hash = "genesis"
        self._sequence = -1
        if self.path.exists():
            self._load()

    @staticmethod
    def _row_hash(row: dict) -> str:
        material = json.dumps(
            {k: row[k] for k in ("cell_key", "result", "sequence", "prev_event_hash")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row["prev_event_hash"] != self._last_hash:
                raise ValueError(
                    f"cell result chain broken at sequence {row['sequence']}")
            if self._row_hash(row) != row["event_hash"]:
                raise ValueError(f"cell result row tampered at sequence {row['sequence']}")
            self._last_hash = row["event_hash"]
            self._sequence = row["sequence"]
            self._results[row["cell_key"]] = row["result"]

    def is_complete(self, cell_key: str) -> bool:
        return cell_key in self._results

    def get(self, cell_key: str) -> Any:
        return self._results.get(cell_key)

    def pending(self, ordered: Iterable[ResolvedCell]) -> list[ResolvedCell]:
        """The cells still to run, in the order given, so a resume continues in place."""
        return [cell for cell in ordered if not self.is_complete(cell.cell_key)]

    def record(self, cell_key: str, result: Any) -> None:
        """Durably record one completed cell. Refuses to overwrite: re-running re-spends."""
        with self._lock:
            if cell_key in self._results:
                raise ValueError(f"cell already recorded: {cell_key}")
            row = {
                "cell_key": cell_key, "result": result, "sequence": self._sequence + 1,
                "prev_event_hash": self._last_hash,
            }
            row["event_hash"] = self._row_hash(row)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._sequence = row["sequence"]
            self._last_hash = row["event_hash"]
            self._results[cell_key] = result
