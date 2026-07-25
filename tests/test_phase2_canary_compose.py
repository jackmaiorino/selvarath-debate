"""Composing a resolved canary cell into the exact prompts and parameters it runs with.

The frozen bundle's condition_composition map already says which template families make up
each condition, so this module reads that map rather than hardcoding per-kind logic. It
divides cleanly in two, and the map itself is what tells them apart:

- an entry with a ``judge`` key is a single-call judgment (batch replay and the diagnostics);
- an entry with ``presentation`` and ``verdict`` takes judge_loop's presentation, optional
  query loop, and verdict shape.

judge_loop reads its prompts out of a protocol-shaped dict, so the second shape is served by
building that dict from the bundle rather than by forking judge_loop.
"""
import json
from pathlib import Path

import pytest

from rejudge import phase2_canary_cells as cells_mod
from rejudge import phase2_plan
from rejudge.phase2_canary_compose import (
    UncomposableCell, is_single_call, judge_protocol_for, single_call_prompt)


ANCHOR = "Qwen/Qwen2.5-7B-Instruct-Turbo"
ORACLE = "meta-llama/Llama-3.3-70B-Instruct-Turbo"


def _protocol():
    return json.loads(Path("rejudge/phase2_protocol.json").read_text(encoding="utf-8"))


def _bundle():
    return json.loads(Path("rejudge/phase2_prompt_bundle.json").read_text(encoding="utf-8"))


def _cell(kind, condition):
    protocol, bundle = _protocol(), _bundle()
    for cell in phase2_plan.enumerate_canary_cells(protocol):
        if cell["kind"] == kind and cell["condition"] == condition:
            return cells_mod.resolve_cell(cell, protocol, bundle, anchor_judge_model=ANCHOR)
    raise AssertionError(f"no cell for {kind}/{condition}")


# --- which shape a cell takes ----------------------------------------------------------------

def test_the_loop_shaped_conditions_are_not_single_call():
    for kind, condition in [("canary_debate_judgment", "b0"),
                            ("canary_debate_judgment", "sequential_b2"),
                            ("canary_debate_judgment", "placebo_b2"),
                            ("canary_no_debate_judgment", "b0"),
                            ("canary_no_debate_judgment", "clean_b2"),
                            ("canary_no_debate_judgment", "placebo_b2"),
                            ("canary_cap_protection_judgment", "capped150_b0")]:
        assert is_single_call(_cell(kind, condition)) is False, condition


def test_the_replay_and_diagnostic_conditions_are_single_call():
    for kind, condition in [("canary_debate_judgment", "batch_same_qa_b2"),
                            ("canary_empty_evidence_judgment", "empty_evidence_table"),
                            ("canary_full_document_judgment", "full_document_ceiling")]:
        assert is_single_call(_cell(kind, condition)) is True, condition


def test_a_transcript_cell_is_neither_shape():
    with pytest.raises(UncomposableCell):
        is_single_call(_cell("canary_debate_transcript", "canary_blind_uncapped_3_round"))


# --- the protocol-shaped dict judge_loop reads -----------------------------------------------

def test_the_composed_protocol_carries_every_field_judge_loop_reads():
    composed = judge_protocol_for(_cell("canary_debate_judgment", "sequential_b2"),
                                  _protocol(), _bundle())
    judge = composed["judge"]
    for field in ("system_prompt", "user_prompt_template", "query_phase_prompt",
                  "verdict_prompt"):
        assert judge[field], field
    assert composed["oracle"]["system_prompt"]
    assert composed["oracle"]["user_prompt_template"]
    assert composed["protocol"]["models"]["oracle"] == ORACLE


def test_the_frozen_per_role_temperatures_are_carried_through():
    composed = judge_protocol_for(_cell("canary_debate_judgment", "sequential_b2"),
                                  _protocol(), _bundle())
    # execution_semantics.temperature_by_call_role: judge_query and judge_verdict 0.3,
    # oracle 0. judge_loop uses one judge temperature for both of its judge calls.
    assert composed["protocol"]["temperature"]["judge"] == 0.3
    assert composed["protocol"]["temperature"]["oracle"] == 0


def test_a_no_debate_condition_composes_the_no_debate_presentation():
    composed = judge_protocol_for(_cell("canary_no_debate_judgment", "clean_b2"),
                                  _protocol(), _bundle())
    assert "NO-DEBATE CONDITION" in composed["judge"]["user_prompt_template"]
    assert "{debate_transcript}" not in composed["judge"]["user_prompt_template"]


def test_a_debate_condition_composes_the_sequential_presentation():
    composed = judge_protocol_for(_cell("canary_debate_judgment", "b0"),
                                  _protocol(), _bundle())
    assert "{debate_transcript}" in composed["judge"]["user_prompt_template"]


def test_the_query_template_is_the_frozen_phase2_one_not_the_pilot_rewrite():
    composed = judge_protocol_for(_cell("canary_debate_judgment", "sequential_b2"),
                                  _protocol(), _bundle())
    query = composed["judge"]["query_phase_prompt"]
    assert "prefixed by `CLAIM: `" in query
    assert "Is it stated in the text that" not in query


def test_a_budget_zero_condition_composes_no_query_template():
    composed = judge_protocol_for(_cell("canary_debate_judgment", "b0"),
                                  _protocol(), _bundle())
    assert composed["judge"]["query_phase_prompt"] is None


def test_the_rejection_and_no_query_payloads_are_carried():
    composed = judge_protocol_for(_cell("canary_debate_judgment", "sequential_b2"),
                                  _protocol(), _bundle())
    assert composed["gate"]["rejection_payload"] == (
        "Query rejected: ask a single specific factual claim")
    # Still provisional pending oracle review, so it is carried rather than hardcoded deep
    # in the gate.
    assert composed["gate"]["no_query_payload"]


# --- the single-call shape ---------------------------------------------------------------------

def test_a_batch_replay_prompt_carries_the_replayed_verification_results():
    cell = _cell("canary_debate_judgment", "batch_same_qa_b2")
    system, user = single_call_prompt(
        cell, _bundle(), question="Q?", position_a="A", position_b="B",
        debate_transcript="TURNS", verification_results="Query 1: x\nResult: YES")
    assert system
    assert "Query 1: x" in user and "TURNS" in user and "Q?" in user


def test_a_full_document_prompt_carries_the_world_document():
    cell = _cell("canary_full_document_judgment", "full_document_ceiling")
    _system, user = single_call_prompt(
        cell, _bundle(), question="Q?", position_a="A", position_b="B",
        debate_transcript="TURNS", world_document="THE WORLD")
    assert "THE WORLD" in user


def test_a_missing_placeholder_is_refused_rather_than_silently_blank():
    cell = _cell("canary_full_document_judgment", "full_document_ceiling")
    with pytest.raises(UncomposableCell):
        single_call_prompt(cell, _bundle(), question="Q?", position_a="A", position_b="B",
                           debate_transcript="TURNS")  # world_document omitted


def test_every_judgment_cell_in_the_frozen_plan_composes():
    protocol, bundle = _protocol(), _bundle()
    composed = 0
    for cell in phase2_plan.enumerate_canary_cells(protocol):
        resolved = cells_mod.resolve_cell(cell, protocol, bundle,
                                          anchor_judge_model=ANCHOR)
        if resolved.is_transcript:
            continue
        if is_single_call(resolved):
            assert bundle["templates"][resolved.composition["judge"]]["system_prompt"]
        else:
            assert judge_protocol_for(resolved, protocol, bundle)["judge"]["verdict_prompt"]
        composed += 1
    assert composed == 895
