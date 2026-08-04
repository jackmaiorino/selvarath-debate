"""Execution order and the durable record of completed cells.

Two things the canary runner must get right before it makes a single call.

Order: a cell may only run once every cell it depends on has produced a result. Transcripts
gate their judgments, and batch_same_qa additionally gates on the paired sequential judgment
whose question-and-answer pairs it replays. Beyond correctness the order is also operational:
the query-producing arms are the only ones that pause for reviewer labelling, so running
everything else first maximises the work banked before the first pause and clusters the
payloads into batches worth labelling.

Resume: the run will be interrupted, repeatedly and by design. A completed cell must never be
re-executed, because re-execution re-spends it.
"""
import dataclasses
import json
from pathlib import Path

import pytest

from rejudge import phase2_canary_cells as cells_mod
from rejudge import phase2_plan
from rejudge.phase2_canary_order import (
    CellResultStore, DependencyCycle, MissingDependency, execution_order)


ANCHOR = "Qwen/Qwen2.5-7B-Instruct-Turbo"


def _protocol():
    return json.loads(Path("rejudge/phase2_protocol.json").read_text(encoding="utf-8"))


def _bundle():
    return json.loads(Path("rejudge/phase2_prompt_bundle.json").read_text(encoding="utf-8"))


def _resolved():
    protocol, bundle = _protocol(), _bundle()
    return [cells_mod.resolve_cell(cell, protocol, bundle, anchor_judge_model=ANCHOR)
            for cell in phase2_plan.enumerate_canary_cells(protocol)]


# --- ordering ------------------------------------------------------------------------------

def test_every_cell_appears_exactly_once():
    ordered = execution_order(_resolved())
    assert len(ordered) == 945
    assert len({cell.cell_key for cell in ordered}) == 945


def test_no_cell_runs_before_a_cell_it_depends_on():
    ordered = execution_order(_resolved())
    position = {cell.cell_key: i for i, cell in enumerate(ordered)}
    for cell in ordered:
        for dependency in cell.dependency_keys:
            assert position[dependency] < position[cell.cell_key], cell.cell_key


def test_batch_replay_runs_after_the_sequential_judgment_it_replays():
    ordered = execution_order(_resolved())
    position = {cell.cell_key: i for i, cell in enumerate(ordered)}
    batch = [c for c in ordered if c.condition == "batch_same_qa_b2"]
    assert len(batch) == 96
    for cell in batch:
        # Two dependencies: the transcript, and the paired sequential_b2 judgment.
        assert len(cell.dependency_keys) == 2
        assert all(position[d] < position[cell.cell_key] for d in cell.dependency_keys)


def test_the_order_is_deterministic():
    assert [c.cell_key for c in execution_order(_resolved())] == [
        c.cell_key for c in execution_order(_resolved())]


def test_work_that_never_pauses_is_banked_before_the_first_pause():
    # Query-producing cells are the only ones that pause for labelling, so scheduling them
    # last means a pause interrupts as little unbanked work as possible.
    ordered = execution_order(_resolved())
    first_producing = next(i for i, c in enumerate(ordered) if c.produces_queries)
    assert not any(c.produces_queries for c in ordered[:first_producing])

    # 513, not 945-336: of the 609 non-producing cells, the 96 batch_same_qa_b2 replays are
    # gated behind the sequential judgments they replay, so they cannot precede them.
    # 513 is exactly the "gates-first" set the canary manifest proposal priced separately:
    # 50 transcripts + 456 b0 + 7 specials.
    assert first_producing == 513
    banked = ordered[:first_producing]
    assert sum(1 for c in banked if c.is_transcript) == 50
    assert sum(1 for c in banked if c.condition == "b0") == 456
    assert len(banked) - 50 - 456 == 7


def test_transcripts_come_first():
    ordered = execution_order(_resolved())
    assert all(c.is_transcript for c in ordered[:50])


def test_a_missing_dependency_is_refused():
    resolved = _resolved()
    orphan = next(c for c in resolved if c.dependency_keys)
    with pytest.raises(MissingDependency):
        execution_order([orphan])


def test_a_dependency_cycle_is_refused():
    a, b = _resolved()[:2]
    looped_a = dataclasses.replace(a, dependency_keys=(b.cell_key,))
    looped_b = dataclasses.replace(b, dependency_keys=(a.cell_key,))
    with pytest.raises(DependencyCycle):
        execution_order([looped_a, looped_b])


# --- the completed-cell record ---------------------------------------------------------------

def test_a_recorded_cell_is_reported_complete(tmp_path):
    store = CellResultStore(tmp_path / "results.jsonl")
    assert store.is_complete("cell-a") is False
    store.record("cell-a", {"verdict": "A"})
    assert store.is_complete("cell-a") is True


def test_results_survive_reopening(tmp_path):
    path = tmp_path / "results.jsonl"
    CellResultStore(path).record("cell-a", {"verdict": "A"})
    reopened = CellResultStore(path)
    assert reopened.is_complete("cell-a") is True
    assert reopened.get("cell-a")["verdict"] == "A"


def test_recording_a_cell_twice_is_refused(tmp_path):
    store = CellResultStore(tmp_path / "results.jsonl")
    store.record("cell-a", {"verdict": "A"})
    with pytest.raises(ValueError):
        store.record("cell-a", {"verdict": "B"})
    assert store.get("cell-a")["verdict"] == "A"


def test_a_tampered_result_row_is_refused_on_load(tmp_path):
    path = tmp_path / "results.jsonl"
    CellResultStore(path).record("cell-a", {"verdict": "A"})
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["result"] = {"verdict": "B"}
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        CellResultStore(path)


def test_pending_cells_are_the_ones_not_yet_recorded(tmp_path):
    store = CellResultStore(tmp_path / "results.jsonl")
    ordered = execution_order(_resolved())
    store.record(ordered[0].cell_key, {"ok": True})
    remaining = store.pending(ordered)
    assert len(remaining) == 944
    assert ordered[0].cell_key not in {c.cell_key for c in remaining}
    # Order is preserved, so a resumed run continues where it left off.
    assert [c.cell_key for c in remaining] == [c.cell_key for c in ordered[1:]]


def test_concurrent_records_leave_a_verifiable_chain_and_claim_each_cell_once(tmp_path):
    """The result store is both the durability record and the completion claim.

    Chain integrity: every record reads the tail hash and appends against it, so two
    interleaved writes produce a file that will not reload. Claim integrity: a cell must be
    executable by exactly one worker, and the store refusing a second record is what makes
    the claim atomic rather than advisory.
    """
    import threading

    path = tmp_path / "results.jsonl"
    store = CellResultStore(path)
    started = threading.Barrier(12)
    errors, duplicate_claims = [], []

    def worker(index):
        started.wait(timeout=10)
        try:
            store.record(f"cell-{index}", {"index": index})
        except Exception as exc:  # noqa: BLE001 - the test is what escapes
            errors.append(exc)
        # Every worker also races for one shared cell; exactly one may win.
        try:
            store.record("contended-cell", {"winner": index})
        except ValueError:
            duplicate_claims.append(index)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"distinct cells should all record: {errors}"
    assert len(duplicate_claims) == 11, "exactly one worker may claim the contended cell"
    reloaded = CellResultStore(path)  # re-verifies sequence, chain and row hashes
    assert len(reloaded._results) == 13
