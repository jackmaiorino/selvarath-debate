"""Concurrent main-cell scheduling must preserve accounting and durable progress."""
from __future__ import annotations

import json
import threading

from rejudge import phase2_canary_runner as runner
from rejudge.api_client import UnknownChargeHalt
from rejudge.phase2_canary_cells import ResolvedCell
from rejudge.phase2_canary_gate import CanaryCellHalted, PendingReviewerDecision


def _cells(count):
    return [ResolvedCell(
        cell_key=f"cell-{index:03d}", kind="debate_judgment", condition="clean_b1",
        question_id=f"question-{index:03d}", judge_model="fixture", debater_model=None,
        transcript_index=0, replicate_index=0, query_budget=1, dependency_keys=(),
        composition={}, transcript_protocol_name=None, oracle_mode="clean",
        arm_name="clean",
    ) for index in range(count)]


def _run(tmp_path, **kwargs):
    return runner.run_canary(
        results_path=tmp_path / "results.jsonl",
        decisions_path=tmp_path / "decisions.jsonl",
        client=None, reviewer=None, anchor_judge_model="", protocol={}, bundle={},
        cells=_cells(20), max_workers=4, block_size=4, **kwargs,
    )


def _recorded_keys(tmp_path):
    path = tmp_path / "results.jsonl"
    return [json.loads(line)["cell_key"] for line in path.read_text().splitlines()]


def test_parallel_attempt_count_includes_abandoned_and_paused_cells(tmp_path, monkeypatch):
    def execute(cell, context, **kwargs):
        if cell.cell_key == "cell-000":
            raise UnknownChargeHalt("fixture interruption")
        if cell.cell_key == "cell-001":
            raise PendingReviewerDecision("fixture-payload")
        return {"cell_key": cell.cell_key}

    monkeypatch.setattr(runner, "execute_cell", execute)
    result = _run(tmp_path, limit=4)
    assert result.attempted == 4
    assert result.completed == 2
    assert result.abandoned == 1
    assert result.paused == 1
    assert result.halted_reason is None


def test_parallel_pending_wave_never_exceeds_remaining_slots(tmp_path, monkeypatch):
    attempted = []

    def execute(cell, context, **kwargs):
        attempted.append(cell.cell_key)
        raise PendingReviewerDecision(cell.cell_key)

    monkeypatch.setattr(runner, "execute_cell", execute)
    result = _run(tmp_path, pending_payload_limit=6)
    assert result.attempted == 6
    assert result.paused == len(result.pending_payloads) == 6
    assert len(attempted) == len(set(attempted)) == 6


def test_block_halt_keeps_other_completed_cells_and_chooses_frozen_order(
    tmp_path, monkeypatch,
):
    second_failed = threading.Event()
    attempted = []

    def execute(cell, context, **kwargs):
        attempted.append(cell.cell_key)
        if cell.cell_key == "cell-000":
            assert second_failed.wait(2)
            raise CanaryCellHalted("checker_unresolved")
        if cell.cell_key == "cell-001":
            second_failed.set()
            raise CanaryCellHalted("checker_malformed")
        return {"cell_key": cell.cell_key}

    monkeypatch.setattr(runner, "execute_cell", execute)
    result = _run(tmp_path)
    assert result.halted_reason == "checker_unresolved"
    assert result.halted_cell_key == "cell-000"
    assert result.attempted == 4
    assert result.completed == 2
    assert _recorded_keys(tmp_path) == ["cell-002", "cell-003"]
    assert set(attempted) == {f"cell-{i:03d}" for i in range(4)}


def test_fatal_unknown_charge_counts_the_ambiguous_attempt(tmp_path, monkeypatch):
    def execute(cell, context, **kwargs):
        raise UnknownChargeHalt("fixture interruption")

    monkeypatch.setattr(runner, "execute_cell", execute)
    result = _run(tmp_path, fatal_unknown_charge=True)
    assert result.halted_reason == "unknown_charge"
    assert result.attempted == result.abandoned == 4


def test_parallel_resume_never_reexecutes_completed_cells(tmp_path, monkeypatch):
    attempts = []

    def execute(cell, context, **kwargs):
        attempts.append(cell.cell_key)
        return {"cell_key": cell.cell_key}

    monkeypatch.setattr(runner, "execute_cell", execute)
    first = _run(tmp_path, limit=6)
    second = _run(tmp_path, limit=7)
    assert first.completed == first.attempted == 6
    assert second.completed == second.attempted == 7
    assert second.skipped == 6
    assert len(attempts) == len(set(attempts)) == 13
    assert len(_recorded_keys(tmp_path)) == 13
