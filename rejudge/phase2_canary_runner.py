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
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rejudge import phase2_canary_cells as cells_mod
from rejudge import phase2_plan
from rejudge.api_client import CapExceededError, UnknownChargeHalt
from rejudge.phase3_main_retry_backoff import ProviderRetryDeferred
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
    retry_deferred: int = 0
    # Cells whose call had an ambiguous billing outcome. Left unrecorded so a later pass
    # retries them, exactly like a paused cell, rather than stopping the whole run.
    abandoned: int = 0
    # Additive (amendment 14, 2026-09-06): how many not-yet-complete cells this pass
    # actually attempted, the explicit denominator of the per-pass abandonment guard.
    attempted: int = 0
    halted_reason: str | None = None
    halted_cell_key: str | None = None
    halted_detail: str | None = None
    pending_payloads: list[dict[str, str]] = field(default_factory=list)
    paused_cell_keys: list[str] = field(default_factory=list)
    # Additive, phase-3-only (rejudge.phase3_runner's --context-blocklist): how many cells this
    # invocation excluded ex-ante per a context precheck, and that blocklist file's own sha256.
    # Every phase-2 call site leaves both at their defaults, so phase-2 behavior is unchanged.
    context_blocked: int = 0
    context_blocklist_sha256: str | None = None
    # Additive, phase-3-only (rejudge.phase3_runner's --deferral-list, v2 amendment 1's Qwen
    # carve-out): how many cells this invocation excluded ex-ante per an amendment-bound
    # deferral list, and that amendment record's own canonical sha256. Distinct from `deferred`
    # above (ambiguous-billing retries) -- this counts cells excluded by owner-authorized
    # amendment, never attempted at all. Every phase-2 call site leaves both at their defaults,
    # so phase-2 behavior is unchanged.
    deferred_by_amendment: int = 0
    anchors_carried: int = 0
    deferral_amendment_sha256: str | None = None

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
    by_condition: dict[str, deque] = {}
    for cell in ready:
        by_condition.setdefault(str(cell.condition), deque()).append(cell)
    block: list = []
    while len(block) < size and any(by_condition.values()):
        for condition in sorted(by_condition):
            if len(block) >= size:
                break
            queue = by_condition[condition]
            if queue:
                block.append(queue.popleft())
    return block


def run_canary(*, results_path: str | Path, decisions_path: str | Path, client,
               reviewer, anchor_judge_model: str,
               protocol: dict | None = None, bundle: dict | None = None,
               pause_when_unlabeled: bool = False,
               limit: int | None = None,
               cell_filter: Callable[[Any], bool] | None = None,
               cells: list | None = None,
               max_workers: int = 1,
               block_size: int | None = None,
               model_caps: dict[str, int] | None = None,
               transcript_generation_forbidden: bool = False,
               namespace: str | None = None,
               pending_payload_limit: int | None = None,
               role_limits: dict | None = None,
               fatal_unknown_charge: bool = False) -> RunOutcome:
    """Run one pass over the frozen canary plan, resuming from whatever is already recorded.

    Returns rather than raises on a halt: the caller needs the partial outcome, and everything
    completed before the halt is already durable.

    ``max_workers`` above 1 runs cells concurrently in deterministic condition-balanced blocks
    (see :func:`_balanced_block`). Going wide may change the schedule and nothing else: seeds
    are derived per cell, results are recorded in block order rather than completion order, and
    the shared stores enforce one ruling per payload and one record per cell. ``model_caps``
    bounds in-flight calls per model, which is the control that actually matters given one
    model carries almost all the load.

    ``pending_payload_limit`` (additive, default ``None`` = unbounded, unchanged phase-2
    behavior) bounds how many NEW pending payloads (never-before-seen queries this pass must
    make one real, uncached judge call to even discover) one pass will accumulate before it
    stops attempting further not-yet-complete judgment/transcript cells and returns with
    whatever it has. Pausing produces no result row, so a pass that reaches a long run of
    cells that have never been attempted before -- all destined to pause on their first query
    once no committed decision exists yet -- otherwise has no bound on how much real,
    provider-latency-exposed wall-clock time it can spend before returning, and every one of
    those calls is spent with nothing durable to show for it if the pass is killed mid-way
    (the 2026-08-18 canary stall: the watchdog fired at 1,808s of silence while a serial pass
    was working through hundreds of never-before-attempted budget-smoke cells). A phase-2
    caller that never passes it is completely unaffected: the check below is skipped entirely
    when the limit is ``None``.

    ``transcript_generation_forbidden``/``namespace``/``role_limits`` are additive, default-off
    phase-3 hooks; every phase-2 call site omits all three, so phase-2 behavior (and its
    byte-for-byte seed identity) is unchanged. Set ``transcript_generation_forbidden=True`` to
    thread the manifest's own flag onto the :class:`~rejudge.phase2_canary_execute.CellContext`
    this function builds (see that module's ``GenerationForbiddenError``). ``namespace`` (left
    ``None``, the phase-2 default) is forwarded to :func:`~rejudge.phase2_canary_execute.
    execute_cell` as ``debater_model=cell.debater_model, namespace=namespace`` -- the phase-3
    ``decisions.execution_semantics.seed_policy`` extension -- ONLY when non-``None``; a
    phase-2 caller that never passes it gets ``execute_cell(cell, context)`` exactly as before.
    ``role_limits`` (amendment 4, 2026-08-19) is the bound role-limits artifact, threaded onto
    ``CellContext`` and from there into ``judge_loop.run_judgment`` to activate the mechanically
    enforced visible-history byte cap (see ``judge_loop.visible_history_cap_bytes``); left
    ``None`` the cap never activates and behavior is unchanged.

    ``fatal_unknown_charge`` is an additive, default-off main-run hook. When true, the first
    ambiguous provider charge halts the pass immediately instead of entering the historical
    canary abandonment tolerance. Existing phase-2 and canary callers retain their original
    behavior because the default is false.
    """
    protocol = protocol if protocol is not None else _load(
        REPO_ROOT / "rejudge" / "phase2_protocol.json")
    bundle = bundle if bundle is not None else _load(
        REPO_ROOT / "rejudge" / "phase2_prompt_bundle.json")

    # The plan is injectable, and defaults to the canary's. It was enumerated unconditionally
    # here, so the main grid had no execution path at all: its manifest validated cleanly
    # while nothing could run it. Both plans describe the same cell shapes, and resolve_cell
    # normalises the namespace prefix, so one executor serves both rather than a second copy
    # that would drift from this one.
    #
    # An injected cell that is ALREADY a ResolvedCell passes through unresolved. Additive:
    # every phase-2 call site injects raw plan-cell dicts (or nothing), which still go through
    # cells_mod.resolve_cell exactly as before. Phase 3 (rejudge.phase3_runner) resolves its
    # own cell kinds -- a vocabulary cells_mod.resolve_cell does not know at all -- and hands
    # ResolvedCell instances straight to this function to reuse the shared execution/ordering
    # machinery below without forcing them back through a resolver that would reject them.
    plan = list(cells) if cells is not None else phase2_plan.enumerate_canary_cells(protocol)
    resolved = [cell if isinstance(cell, cells_mod.ResolvedCell)
               else cells_mod.resolve_cell(cell, protocol, bundle,
                                           anchor_judge_model=anchor_judge_model)
               for cell in plan]
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
        results=dict(results._results), pause_when_unlabeled=pause_when_unlabeled,
        transcript_generation_forbidden=transcript_generation_forbidden,
        role_limits=role_limits)

    outcome = RunOutcome()
    seen_payloads: set[str] = set()
    if max_workers > 1:
        return _run_concurrent(
            ordered=ordered, results=results, context=context, outcome=outcome,
            seen_payloads=seen_payloads, limit=limit, max_workers=max_workers,
            block_size=block_size or max_workers * 2, namespace=namespace,
            pending_payload_limit=pending_payload_limit,
            fatal_unknown_charge=fatal_unknown_charge)

    attempted = 0
    for cell in ordered:
        if results.is_complete(cell.cell_key):
            outcome.skipped += 1
            continue
        if limit is not None and attempted >= limit:
            break
        if (pending_payload_limit is not None
                and len(outcome.pending_payloads) >= pending_payload_limit):
            break
        attempted += 1
        outcome.attempted = attempted
        try:
            execute_kwargs = ({"debater_model": cell.debater_model, "namespace": namespace}
                              if namespace is not None else {})
            record = execute_cell(cell, context, **execute_kwargs)
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
        except ProviderRetryDeferred:
            outcome.retry_deferred += 1
            outcome.deferred += 1
            attempted -= 1
            outcome.attempted = attempted
            continue
        except MissingTranscript:
            # Not a halt: a dependency that paused for labelling has simply not produced its
            # result yet. The batch replays are the usual case, since they consume the
            # sequential judgments' exchanges. Defer and let a later pass pick it up once the
            # dependency completes.
            outcome.deferred += 1
            continue
        except UnknownChargeHalt:
            outcome.abandoned += 1
            if fatal_unknown_charge:
                outcome.halted_reason = "unknown_charge"
                outcome.halted_cell_key = cell.cell_key
                break
            if _too_many_abandoned(outcome, attempted):
                outcome.halted_reason = "abandoned_cell_rate"
                outcome.halted_cell_key = cell.cell_key
                break
            continue
        except CanaryCellHalted as halt:
            outcome.halted_reason = halt.reason
            outcome.halted_cell_key = cell.cell_key
            outcome.halted_detail = _bounded_halt_detail(halt.detail)
            break
        except CapExceededError:
            outcome.halted_reason = "cap_exceeded"
            outcome.halted_cell_key = cell.cell_key
            break
        except Exception as exc:  # noqa: BLE001 - halt on anything unmodelled, never swallow
            outcome.halted_reason = type(exc).__name__
            outcome.halted_cell_key = cell.cell_key
            outcome.halted_detail = _bounded_halt_detail(str(exc))
            break
        results.record(cell.cell_key, record)
        context.results[cell.cell_key] = record
        outcome.completed += 1

    return outcome



# Calibrated against what a DEGRADED provider actually looks like, not against intuition.
# gemma abandons about 16.8% of calls and a cell makes ~3.5 of them, so roughly 47% of cells
# abandon on their first attempt even when the run is healthy and converging. A threshold
# anywhere near that would halt every pass. These two catch the different thing: a provider
# that is failing essentially everything, and a pass that has run away regardless of rate.
ABANDONED_FRACTION = 0.90
ABANDONED_FRACTION_FLOOR = 10
# Also bounds uncertain spend within a single pass. At the observed ~$0.008 of uncertain
# reservation per abandoned call, 100 abandoned cells is roughly $1, and the supervisor
# re-checks the uncertain ceiling between passes.
ABANDONED_ABSOLUTE = 100


def _too_many_abandoned(outcome, attempted: int) -> bool:
    """Tolerating an abandoned cell must not become tolerating a dead provider.

    A runaway guard, not a safety control: the uncertain-spend ceiling bounds real financial
    exposure, and this only catches abandoning ceasing to be the exception.
    """
    if outcome.abandoned >= ABANDONED_ABSOLUTE:
        return True
    if attempted < ABANDONED_FRACTION_FLOOR:
        return False
    return outcome.abandoned >= max(1, attempted) * ABANDONED_FRACTION


def _bounded_halt_detail(detail: str | None) -> str | None:
    return str(detail).replace("\r", " ").replace("\n", " ")[:1000] if detail else None


def _attempt(
    cell, context, *, namespace: str | None = None,
    fatal_unknown_charge: bool = False,
) -> tuple[str, Any]:
    """Run one cell and classify the outcome, never raising into the worker pool.

    Same taxonomy as the serial loop: a pause is not a failure, a missing dependency is not a
    halt, and anything unmodelled halts rather than being swallowed. Classifying here rather
    than in the pool keeps the halt decision on the main thread, where it can stop dispatching.

    ``namespace`` mirrors :func:`run_canary`'s own additive, default-``None`` hook: left at
    ``None`` (every phase-2 call site), ``execute_cell`` is invoked with no extra kwargs.
    """
    try:
        execute_kwargs = ({"debater_model": cell.debater_model, "namespace": namespace}
                          if namespace is not None else {})
        return "ok", execute_cell(cell, context, **execute_kwargs)
    except PendingReviewerDecision as pending:
        return "paused", pending
    except ProviderRetryDeferred as deferred:
        return "retry_deferred", deferred
    except MissingTranscript:
        return "deferred", None
    except UnknownChargeHalt as unknown:
        # Raised so the ambiguous call is not retried into a possible second unknown charge.
        # That is a statement about ONE call, not about the run: no other cell's billing is
        # implicated, and total exposure is bounded by the uncertain-spend ceiling. Halting
        # everything cost 124 driver restarts and 4.77h of backoff on the bridge canary.
        return ("halt", ("unknown_charge", None)) if fatal_unknown_charge else ("abandoned", unknown)
    except CanaryCellHalted as halt:
        return "halt", (halt.reason, _bounded_halt_detail(halt.detail))
    except CapExceededError:
        return "halt", ("cap_exceeded", None)
    except Exception as exc:  # noqa: BLE001 - halt on anything unmodelled, never swallow
        return "halt", (type(exc).__name__, _bounded_halt_detail(str(exc)))


def _run_concurrent(*, ordered, results, context, outcome, seen_payloads, limit,
                    max_workers: int, block_size: int,
                    namespace: str | None = None,
                    pending_payload_limit: int | None = None,
                    fatal_unknown_charge: bool = False) -> RunOutcome:
    """Blocked concurrent execution of one pass.

    Two properties are load bearing and neither is about speed.

    Readiness is recomputed before every block, so a cell enters the very next block after its
    dependencies land. That is what bounds the parent-to-batch lag.

    Results are applied in BLOCK order, not completion order, once the whole block has
    finished. A cell's seed is derived from its own key, so completion order cannot change what
    a cell produces, but it would otherwise decide the order rows land in the hash-chained
    store, and any analysis that reads row position would then see a scheduler artifact.

    ``pending_payload_limit`` mirrors the serial loop's own additive, default-``None`` bound
    (see :func:`run_canary`'s docstring). Each cell can yield at most one pending payload, so
    shrinking the final block to the remaining slots keeps the same hard review-wave bound
    without choosing cells according to their results or completion times.
    """
    attempted: set[str] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        while True:
            if (pending_payload_limit is not None
                    and len(outcome.pending_payloads) >= pending_payload_limit):
                break
            ready = [cell for cell in ordered
                     if cell.cell_key not in attempted
                     and not results.is_complete(cell.cell_key)
                     and all(key in context.results for key in cell.dependency_keys)]
            if limit is not None:
                ready = ready[:max(0, limit - outcome.attempted)]
            if not ready:
                break
            next_block_size = block_size
            if pending_payload_limit is not None:
                next_block_size = min(
                    next_block_size,
                    pending_payload_limit - len(outcome.pending_payloads),
                )
            block = _balanced_block(ready, next_block_size)
            attempted.update(cell.cell_key for cell in block)
            outcome.attempted = len(attempted) - outcome.retry_deferred

            futures = [pool.submit(
                _attempt, cell, context, namespace=namespace,
                fatal_unknown_charge=fatal_unknown_charge)
                      for cell in block]
            settled = [future.result() for future in futures]
            outcome.retry_deferred += sum(kind == "retry_deferred" for kind, _ in settled)
            outcome.attempted = len(attempted) - outcome.retry_deferred

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
                elif kind in {"deferred", "retry_deferred"}:
                    outcome.deferred += 1
                elif kind == "abandoned":
                    outcome.abandoned += 1
                    if not halted and _too_many_abandoned(outcome, outcome.attempted):
                        halted = True
                        outcome.halted_reason = "abandoned_cell_rate"
                        outcome.halted_cell_key = cell.cell_key
                else:
                    reason, detail = payload
                    if reason == "unknown_charge":
                        outcome.abandoned += 1
                    if not halted:
                        # First halt in block order wins, so the reported cause is
                        # deterministic rather than whichever worker finished first.
                        halted = True
                        outcome.halted_reason = reason
                        outcome.halted_cell_key = cell.cell_key
                        outcome.halted_detail = detail
            if halted:
                break

    outcome.skipped = sum(1 for cell in ordered if cell.cell_key not in attempted
                          and results.is_complete(cell.cell_key))
    return outcome
