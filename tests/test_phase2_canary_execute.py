"""Executing one resolved canary cell, for each of the shapes the plan contains.

Three shapes, and each is a different call pattern rather than a variation on one:

- a transcript cell drives debate_gen;
- a loop-shaped judgment drives judge_loop, with the dual gate attached only when the
  condition actually produces queries;
- a single-call judgment composes one prompt and takes one verdict.

The tests run the genuine modules with only the provider replaced, because the point of this
layer is the wiring between them, and wiring is exactly what a mocked test would not check.
"""
import json
from pathlib import Path

import pytest

from rejudge import phase2_canary_cells as cells_mod
from rejudge import phase2_plan
from rejudge.phase2_canary_execute import CellContext, MissingTranscript, execute_cell
from rejudge.phase2_canary_fixtures import DeterministicCanaryClient, StubReviewer, scripted
from rejudge.phase2_dual_gate import DualGateDecisionStore


ANCHOR = "Qwen/Qwen2.5-7B-Instruct-Turbo"


def _protocol():
    return json.loads(Path("rejudge/phase2_protocol.json").read_text(encoding="utf-8"))


def _bundle():
    return json.loads(Path("rejudge/phase2_prompt_bundle.json").read_text(encoding="utf-8"))


def _cell(kind, condition, index=0):
    protocol, bundle = _protocol(), _bundle()
    matches = [c for c in phase2_plan.enumerate_canary_cells(protocol)
               if c["kind"] == kind and c["condition"] == condition]
    return cells_mod.resolve_cell(matches[index], protocol, bundle,
                                  anchor_judge_model=ANCHOR), matches[index]


def _context(tmp_path, client=None, reviewer=None, results=None):
    return CellContext(
        client=client or DeterministicCanaryClient(),
        protocol=_protocol(), bundle=_bundle(),
        decision_store=DualGateDecisionStore(tmp_path / "decisions.jsonl"),
        reviewer=reviewer or StubReviewer(),
        anchor_judge_model=ANCHOR,
        results=results if results is not None else {},
    )


def _transcript_for(tmp_path, condition="canary_blind_uncapped_3_round",
                    kind="canary_debate_transcript", index=0, context=None):
    resolved, _raw = _cell(kind, condition, index)
    context = context or _context(tmp_path)
    return resolved, execute_cell(resolved, context)


# --- transcripts ------------------------------------------------------------------------

def test_a_transcript_cell_generates_a_three_round_debate(tmp_path):
    _resolved, transcript = _transcript_for(tmp_path)
    assert len(transcript["debate_transcript"]) == 6
    assert {t["speaker"] for t in transcript["debate_transcript"]} == {"honest", "dishonest"}


def test_a_transcript_carries_the_plan_cell_key_not_debate_gens(tmp_path):
    resolved, transcript = _transcript_for(tmp_path)
    assert transcript["cell_key"] == resolved.cell_key


def test_a_capped_transcript_uses_the_150_word_protocol(tmp_path):
    _resolved, transcript = _transcript_for(
        tmp_path, condition="canary_blind_capped150_3_round",
        kind="canary_capped_debate_transcript")
    assert transcript["protocol"] == "capped3"


# --- loop-shaped judgments ----------------------------------------------------------------

def test_a_b0_judgment_makes_no_query_calls(tmp_path):
    context = _context(tmp_path)
    transcript_cell, transcript = _transcript_for(tmp_path, context=context)
    context.results[transcript_cell.cell_key] = transcript

    resolved, _raw = _cell("canary_debate_judgment", "b0")
    resolved = _rebind_dependency(resolved, transcript_cell.cell_key)
    record = execute_cell(resolved, context)
    assert record["queries_used"] == 0
    assert record["verdict_strict"]["parse_ok"] is True
    assert all(c["call_role"] != "judge_query" for c in context.client.calls)


def test_a_sequential_judgment_runs_the_gated_query_loop(tmp_path):
    context = _context(tmp_path)
    transcript_cell, transcript = _transcript_for(tmp_path, context=context)
    context.results[transcript_cell.cell_key] = transcript

    resolved, _raw = _cell("canary_debate_judgment", "sequential_b2")
    resolved = _rebind_dependency(resolved, transcript_cell.cell_key)
    record = execute_cell(resolved, context)
    assert record["queries_used"] == 2
    assert all(e["normalized"] == "YES" for e in record["exchanges"])
    roles = [c["call_role"] for c in context.client.calls]
    assert "query_checker" in roles and "oracle_verification" in roles


def test_a_placebo_judgment_dispatches_no_oracle(tmp_path):
    context = _context(tmp_path)
    transcript_cell, transcript = _transcript_for(tmp_path, context=context)
    context.results[transcript_cell.cell_key] = transcript

    resolved, _raw = _cell("canary_debate_judgment", "placebo_b2")
    resolved = _rebind_dependency(resolved, transcript_cell.cell_key)
    record = execute_cell(resolved, context)
    assert record["queries_used"] == 2
    assert all(c["call_role"] != "oracle_verification" for c in context.client.calls)
    # The gate still ran: placebo is gated identically.
    assert any(c["call_role"] == "query_checker" for c in context.client.calls)


def test_a_no_debate_judgment_needs_no_transcript(tmp_path):
    resolved, _raw = _cell("canary_no_debate_judgment", "clean_b2")
    context = _context(tmp_path)
    record = execute_cell(resolved, context)
    assert record["queries_used"] == 2
    assert record["verdict_strict"]["parse_ok"] is True


def test_a_reviewer_intercept_blocks_dispatch_in_a_real_cell(tmp_path):
    context = _context(tmp_path, reviewer=StubReviewer(scripted([("REJECT", "P3")])))
    resolved, _raw = _cell("canary_no_debate_judgment", "clean_b2")
    record = execute_cell(resolved, context)
    assert all(e["blocked"] for e in record["exchanges"])
    assert all(c["call_role"] != "oracle_verification" for c in context.client.calls)


# --- single-call judgments -----------------------------------------------------------------

def test_a_batch_replay_reuses_the_paired_sequential_exchanges(tmp_path):
    context = _context(tmp_path)
    transcript_cell, transcript = _transcript_for(tmp_path, context=context)
    context.results[transcript_cell.cell_key] = transcript

    sequential, _raw = _cell("canary_debate_judgment", "sequential_b2")
    sequential = _rebind_dependency(sequential, transcript_cell.cell_key)
    sequential_record = execute_cell(sequential, context)
    context.results[sequential.cell_key] = sequential_record

    batch, _raw = _cell("canary_debate_judgment", "batch_same_qa_b2")
    batch = _rebind_dependency(batch, transcript_cell.cell_key, sequential.cell_key)
    before = len(context.client.calls)
    record = execute_cell(batch, context)

    assert len(context.client.calls) - before == 1, "a batch replay is exactly one call"
    assert context.client.calls[-1]["call_role"] == "batch_verdict"
    shown = context.client.calls[-1]["messages"][-1]["content"]
    for exchange in sequential_record["exchanges"]:
        assert exchange["extracted_claim"] in shown


def test_a_batch_replay_without_its_sequential_result_is_refused(tmp_path):
    context = _context(tmp_path)
    transcript_cell, transcript = _transcript_for(tmp_path, context=context)
    context.results[transcript_cell.cell_key] = transcript
    batch, _raw = _cell("canary_debate_judgment", "batch_same_qa_b2")
    batch = _rebind_dependency(batch, transcript_cell.cell_key, "absent-sequential")
    with pytest.raises(MissingTranscript):
        execute_cell(batch, context)


def test_a_full_document_judgment_shows_the_judge_the_world(tmp_path):
    context = _context(tmp_path)
    transcript_cell, transcript = _transcript_for(tmp_path, context=context)
    context.results[transcript_cell.cell_key] = transcript

    resolved, _raw = _cell("canary_full_document_judgment", "full_document_ceiling")
    resolved = _rebind_dependency(resolved, transcript_cell.cell_key)
    execute_cell(resolved, context)
    shown = context.client.calls[-1]["messages"][-1]["content"]
    assert len(shown) > 2000, "the full world document should be in the prompt"


def test_an_empty_evidence_judgment_is_a_single_call(tmp_path):
    context = _context(tmp_path)
    transcript_cell, transcript = _transcript_for(tmp_path, context=context)
    context.results[transcript_cell.cell_key] = transcript

    resolved, _raw = _cell("canary_empty_evidence_judgment", "empty_evidence_table")
    resolved = _rebind_dependency(resolved, transcript_cell.cell_key)
    before = len(context.client.calls)
    record = execute_cell(resolved, context)
    assert len(context.client.calls) - before == 1
    assert record["verdict_strict"]["parse_ok"] is True


# --- dependencies --------------------------------------------------------------------------

def test_a_judgment_missing_its_transcript_is_refused(tmp_path):
    resolved, _raw = _cell("canary_debate_judgment", "b0")
    with pytest.raises(MissingTranscript):
        execute_cell(resolved, _context(tmp_path))


def _rebind_dependency(resolved, *dependency_keys):
    """Point a cell at the transcript this test actually generated."""
    import dataclasses
    return dataclasses.replace(resolved, dependency_keys=tuple(dependency_keys))
