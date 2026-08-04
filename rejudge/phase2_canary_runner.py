"""The canary run driver: ordering, pausing, resuming, halting.

One decision shapes everything else here: **a pause does not stop the run.**

A cell pauses the moment it proposes a query that has no committed reviewer decision. If that
ended the run, every labelling batch would hold a single payload and the canary would need
hundreds of round trips through a human-paced workflow. Continuing past a paused cell instead
accumulates payloads across every cell that pauses, so one batch covers hundreds of them and
the whole canary needs only a handful of passes, roughly one per query slot: slot two is only
proposed once slot one has been labelled and dispatched.

Halts are the opposite, and deliberately so. A checker outage or a malformed checker verdict
is not a per-cell inconvenience; it is evidence the frozen gate is not behaving as frozen. The
run stops, keeps everything already recorded, and resumes from there once the cause is
understood.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rejudge import phase2_canary_cells as cells_mod
from rejudge import phase2_plan
from rejudge.api_client import CapExceededError
from rejudge.phase2_canary_execute import CellContext, MissingTranscript, execute_cell
from rejudge.phase2_canary_gate import CanaryCellHalted, PendingReviewerDecision
from rejudge.phase2_canary_order import CellResultStore, execution_order
from rejudge.phase2_dual_gate import DualGateDecisionStore

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class RunOutcome:
    """What one pass over the plan achieved."""

    completed: int = 0
    skipped: int = 0
    paused: int = 0
    deferred: int = 0
    halted_reason: str | None = None
    halted_cell_key: str | None = None
    pending_payloads: list[dict[str, str]] = field(default_factory=list)
    paused_cell_keys: list[str] = field(default_factory=list)

    @property
    def needs_labelling(self) -> bool:
        return bool(self.pending_payloads)


def _load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ModelCappedClient:
    """Bounds how many calls may be in flight against each model at once.

    A single global worker count is the wrong instrument here. Measured over the 2026-07-28
    canary, gemma-4-31B was 95% of all call time (12.80 h of 13.69 h successful, and 9.06 h of
    the 9.09 h burned on abandoned calls), because it serves both the frozen checker and one
    judge role. It abandoned 189 of 1,702 calls at concurrency ONE. Eight workers would
    therefore put roughly eight calls on the one model already failing, while the other three
    sit idle. Caps belong per provider quota domain, chosen by measurement.
    """

    def __init__(self, inner: Any, caps: dict[str, int]) -> None:
        self._inner = inner
        self._semaphores = {model: threading.Semaphore(limit)
                            for model, limit in caps.items() if limit > 0}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, messages, model, *args, **kwargs):
        semaphore = self._semaphores.get(str(model))
        if semaphore is None:
            return self._inner.complete(messages, model, *args, **kwargs)
        with semaphore:
            return self._inner.complete(messages, model, *args, **kwargs)


def _balanced_block(ready: list, size: int) -> list:
    """Take the next block of cells, round-robin across conditions.

    Batch cells depend on their sequential parents, so batch necessarily executes later. A
    work-conserving queue would drain each condition in turn and hand every batch cell a
    systematically later slice of the run than its sequential counterpart, which correlates
    time with condition. Any time-varying provider degradation would then land preferentially
    on one of the two co-primary components (P and R), turning an operational nuisance into a
    confound. Round-robin keeps every block's condition mix similar, and recomputing readiness
    each block keeps a batch cell within one block of its parent.

    Deterministic: ``ready`` arrives in the frozen execution order and conditions are taken in
    sorted order, so the same inputs always produce the same block.
    """
    by_condition: dict[str, list] = {}
    for cell in ready:
        by_condition.setdefault(str(cell.condition), []).append(cell)
    block: list = []
    while len(block) < size and any(by_condition.values()):
        for condition in sorted(by_condition):
            if len(block) >= size:
                break
            queue = by_condition[condition]
            if queue:
                block.append(queue.pop(0))
    return block


def run_canary(*, results_path: str | Path, decisions_path: str | Path, client,
               reviewer, anchor_judge_model: str,
               protocol: dict | None = None, bundle: dict | None = None,
               pause_when_unlabeled: bool = False,
               limit: int | None = None,
               cell_filter: Callable[[Any], bool] | None = None,
               max_workers: int = 1,
               block_size: int | None = None,
               model_caps: dict[str, int] | None = None) -> RunOutcome:
    """Run one pass over the frozen canary plan, resuming from whatever is already recorded.

    Returns rather than raises on a halt: the caller needs the partial outcome, and everything
    completed before the halt is already durable.

    ``max_workers`` above 1 runs cells concurrently in deterministic condition-balanced blocks
    (see :func:`_balanced_block`). Going wide may change the schedule and nothing else: seeds
    are derived per cell, results are recorded in block order rather than completion order, and
    the shared stores enforce one ruling per payload and one record per cell. ``model_caps``
    bounds in-flight calls per model, which is the control that actually matters given one
    model carries almost all the load.
    """
    protocol = protocol if protocol is not None else _load(
        REPO_ROOT / "rejudge" / "phase2_protocol.json")
    bundle = bundle if bundle is not None else _load(
        REPO_ROOT / "rejudge" / "phase2_prompt_bundle.json")

    resolved = [cells_mod.resolve_cell(cell, protocol, bundle,
                                       anchor_judge_model=anchor_judge_model)
                for cell in phase2_plan.enumerate_canary_cells(protocol)]
    if cell_filter is not None:
        resolved = [cell for cell in resolved if cell_filter(cell)]
    ordered = execution_order(resolved)

    results = CellResultStore(results_path)
    if model_caps:
        client = ModelCappedClient(client, model_caps)
    context = CellContext(
        client=client, protocol=protocol, bundle=bundle,
        decision_store=DualGateDecisionStore(decisions_path), reviewer=reviewer,
        anchor_judge_model=anchor_judge_model,
        results=dict(results._results), pause_when_unlabeled=pause_when_unlabeled)

    outcome = RunOutcome()
    seen_payloads: set[str] = set()
    if max_workers > 1:
        return _run_concurrent(
            ordered=ordered, results=results, context=context, outcome=outcome,
            seen_payloads=seen_payloads, limit=limit, max_workers=max_workers,
            block_size=block_size or max_workers * 2)

    attempted = 0
    for cell in ordered:
        if results.is_complete(cell.cell_key):
            outcome.skipped += 1
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            record = execute_cell(cell, context)
        except PendingReviewerDecision as pending:
            # Not a failure. The cell is left unrecorded so a later pass re-runs it, replaying
            # its earlier calls from the cache rather than re-spending them.
            outcome.paused += 1
            outcome.paused_cell_keys.append(cell.cell_key)
            # Deduplicated by payload hash: different cells routinely propose identical query
            # text, and one committed decision serves all of them by inheritance. Handing a
            # reviewer the same payload twice would be wasted labour and, worse, would invite
            # two different answers to a question the store can only record once.
            if pending.payload_sha256 not in seen_payloads:
                seen_payloads.add(pending.payload_sha256)
                outcome.pending_payloads.append(dict(pending.payload))
            continue
        except MissingTranscript:
            # Not a halt: a dependency that paused for labelling has simply not produced its
            # result yet. The batch replays are the usual case, since they consume the
            # sequential judgments' exchanges. Defer and let a later pass pick it up once the
            # dependency completes.
            outcome.deferred += 1
            continue
        except CanaryCellHalted as halt:
            outcome.halted_reason = halt.reason
            outcome.halted_cell_key = cell.cell_key
            break
        except CapExceededError:
            outcome.halted_reason = "cap_exceeded"
            outcome.halted_cell_key = cell.cell_key
            break
        except Exception as exc:  # noqa: BLE001 - halt on anything unmodelled, never swallow
            outcome.halted_reason = type(exc).__name__
            outcome.halted_cell_key = cell.cell_key
            break
        results.record(cell.cell_key, record)
        context.results[cell.cell_key] = record
        outcome.completed += 1

    return outcome



def _attempt(cell, context) -> tuple[str, Any]:
    """Run one cell and classify the outcome, never raising into the worker pool.

    Same taxonomy as the serial loop: a pause is not a failure, a missing dependency is not a
    halt, and anything unmodelled halts rather than being swallowed. Classifying here rather
    than in the pool keeps the halt decision on the main thread, where it can stop dispatching.
    """
    try:
        return "ok", execute_cell(cell, context)
    except PendingReviewerDecision as pending:
        return "paused", pending
    except MissingTranscript:
        return "deferred", None
    except CanaryCellHalted as halt:
        return "halt", halt.reason
    except CapExceededError:
        return "halt", "cap_exceeded"
    except Exception as exc:  # noqa: BLE001 - halt on anything unmodelled, never swallow
        return "halt", type(exc).__name__


def _run_concurrent(*, ordered, results, context, outcome, seen_payloads, limit,
                    max_workers: int, block_size: int) -> RunOutcome:
    """Blocked concurrent execution of one pass.

    Two properties are load bearing and neither is about speed.

    Readiness is recomputed before every block, so a cell enters the very next block after its
    dependencies land. That is what bounds the parent-to-batch lag.

    Results are applied in BLOCK order, not completion order, once the whole block has
    finished. A cell's seed is derived from its own key, so completion order cannot change what
    a cell produces, but it would otherwise decide the order rows land in the hash-chained
    store, and any analysis that reads row position would then see a scheduler artifact.
    """
    attempted: set[str] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while True:
            ready = [cell for cell in ordered
                     if cell.cell_key not in attempted
                     and not results.is_complete(cell.cell_key)
                     and all(key in context.results for key in cell.dependency_keys)]
            if limit is not None:
                ready = ready[:max(0, limit - len(attempted))]
            if not ready:
                break
            block = _balanced_block(ready, block_size)
            attempted.update(cell.cell_key for cell in block)

            futures = [pool.submit(_attempt, cell, context) for cell in block]
            settled = [future.result() for future in futures]

            halted = False
            for cell, (kind, payload) in zip(block, settled):
                if kind == "ok":
                    results.record(cell.cell_key, payload)
                    context.results[cell.cell_key] = payload
                    outcome.completed += 1
                elif kind == "paused":
                    outcome.paused += 1
                    outcome.paused_cell_keys.append(cell.cell_key)
                    if payload.payload_sha256 not in seen_payloads:
                        seen_payloads.add(payload.payload_sha256)
                        outcome.pending_payloads.append(dict(payload.payload))
                elif kind == "deferred":
                    outcome.deferred += 1
                elif not halted:
                    # First halt in block order wins, so the reported cause is deterministic
                    # rather than whichever worker happened to finish first.
                    halted = True
                    outcome.halted_reason = payload
                    outcome.halted_cell_key = cell.cell_key
            if halted:
                break

    outcome.skipped = sum(1 for cell in ordered if cell.cell_key not in attempted
                          and results.is_complete(cell.cell_key))
    return outcome
