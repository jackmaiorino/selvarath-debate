"""Regression: the drive-time terminal-cell exclusion must filter ResolvedCell objects.

The first live exercise of the amendment-5 exclusion path (run phase3-v3-5cc134ec5730dfaf,
2026-08-28, r23) wedged its identity on `TypeError: 'ResolvedCell' object is not
subscriptable`: the filter indexed resolved cells like the plan's dict cells. Every prior
run had zero terminal cells, so no test had ever driven the filter with the real resolved
type. This test replays the exact filtering expression drive_formal uses, against real
ResolvedCell objects.
"""
from __future__ import annotations

from rejudge.phase2_canary_cells import ResolvedCell


def _resolved_cell(cell_key: str) -> ResolvedCell:
    return ResolvedCell(
        cell_key=cell_key, kind="debate_judgment", condition="sequential_b2",
        question_id="CN-021", judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        debater_model="Qwen/Qwen3.7-Plus", transcript_index=1, replicate_index=1,
        query_budget=2, dependency_keys=(), composition={}, transcript_protocol_name=None,
        oracle_mode="clean", arm_name="clean",
    )


def test_terminal_exclusion_filters_resolved_cells_by_attribute():
    judgments = [_resolved_cell("keep-1"), _resolved_cell("drop-me"), _resolved_cell("keep-2")]
    terminal_cells = frozenset({"drop-me"})
    # The exact expression from drive_formal's exclusion branch.
    judgments = [
        cell for cell in judgments
        if str(cell.cell_key) not in terminal_cells]
    assert [cell.cell_key for cell in judgments] == ["keep-1", "keep-2"]


def test_resolved_cells_are_not_subscriptable_so_dict_indexing_would_wedge():
    cell = _resolved_cell("k")
    try:
        cell["cell_key"]
    except TypeError:
        return
    raise AssertionError(
        "ResolvedCell became subscriptable; the drive filter regression no longer guards "
        "what it was written for")
