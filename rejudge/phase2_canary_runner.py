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


def run_canary(*, results_path: str | Path, decisions_path: str | Path, client,
               reviewer, anchor_judge_model: str,
               protocol: dict | None = None, bundle: dict | None = None,
               pause_when_unlabeled: bool = False,
               limit: int | None = None,
               cell_filter: Callable[[Any], bool] | None = None) -> RunOutcome:
    """Run one pass over the frozen canary plan, resuming from whatever is already recorded.

    Returns rather than raises on a halt: the caller needs the partial outcome, and everything
    completed before the halt is already durable.
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
    context = CellContext(
        client=client, protocol=protocol, bundle=bundle,
        decision_store=DualGateDecisionStore(decisions_path), reviewer=reviewer,
        anchor_judge_model=anchor_judge_model,
        results=dict(results._results), pause_when_unlabeled=pause_when_unlabeled)

    outcome = RunOutcome()
    seen_payloads: set[str] = set()
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


