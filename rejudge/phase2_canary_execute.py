"""Execute one resolved canary cell.

The plan contains three genuinely different call patterns, not variations on one:

- a **transcript** cell drives ``debate_gen.generate_transcript``;
- a **loop-shaped judgment** drives ``judge_loop.run_judgment``, with the dual gate attached
  only when the condition actually produces queries;
- a **single-call judgment** (batch replay, empty evidence, full document) composes one prompt
  and takes one verdict.

Which pattern a cell takes is decided by the frozen bundle via
:mod:`rejudge.phase2_canary_compose`, never by a hardcoded table here.

Every dependency this module needs must already be present in ``CellContext.results``. A
missing one is refused rather than worked around: a judgment without its transcript, or a
batch replay without the sequential exchanges it replays, is not the cell the plan describes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rejudge import debate_gen, judge_loop, records
from rejudge.config import ARMS, PLACEBO_TEXT, make_seed
from rejudge.parsers import parse_both
from rejudge.phase2_canary_cells import ResolvedCell
from rejudge.phase2_canary_compose import (
    debater_protocol_for, is_single_call, judge_protocol_for, single_call_prompt,
    turn_templates_for)
from rejudge.phase2_canary_gate import CanaryQueryGate, FrozenCheckerAdapter
from rejudge.phase2_dual_gate import DualGate, DualGateDecisionStore


class MissingTranscript(KeyError):
    """Raised when a cell's dependency has not been executed yet."""


@dataclass
class CellContext:
    """Everything a cell needs beyond its own resolution."""

    client: Any
    protocol: dict
    bundle: dict
    decision_store: DualGateDecisionStore
    reviewer: Any
    anchor_judge_model: str
    results: dict[str, Any] = field(default_factory=dict)
    pause_when_unlabeled: bool = False
    _questions: dict | None = None
    _worlds: dict | None = None

    def question(self, question_id: str) -> dict:
        if self._questions is None:
            self._questions = debate_gen._load_question_bank()
        return self._questions[question_id]

    def world_document(self, question_id: str) -> str:
        if self._worlds is None:
            from rejudge.runner import _world_documents
            self._worlds = _world_documents()
        return self._worlds[self.question(question_id)["world"]]


def _dependency(cell: ResolvedCell, context: CellContext, index: int = 0) -> Any:
    if len(cell.dependency_keys) <= index:
        raise MissingTranscript(
            f"cell {cell.cell_key} declares no dependency at position {index}")
    key = cell.dependency_keys[index]
    if key not in context.results:
        raise MissingTranscript(
            f"cell {cell.cell_key} depends on {key}, which has no result yet")
    return context.results[key]


def _transcript_for(cell: ResolvedCell, context: CellContext) -> dict:
    """The debate a judgment is judging.

    No-debate conditions have none, so they get a transcript-shaped stand-in carrying the
    candidates and an empty debate. The frozen no_debate template declares no
    debate_transcript placeholder, so the empty text is never rendered; it exists only
    because judge_loop reads the candidates off this structure.
    """
    if cell.kind == "canary_no_debate_judgment":
        question = context.question(cell.question_id)
        return {
            "question_id": cell.question_id, "transcript_index": 0,
            "world": question["world"], "question": question["question"],
            "correct_answer": question["correct_answer"],
            "wrong_answer": question["wrong_answer"], "debate_transcript": [],
        }
    return _dependency(cell, context)


def _run_transcript(cell: ResolvedCell, context: CellContext) -> dict:
    question = context.question(cell.question_id)
    honest_first = debate_gen.counterbalance_assignment(
        [(cell.question_id, cell.transcript_index or 0)])[
            (cell.question_id, cell.transcript_index or 0)]
    transcript = debate_gen.generate_transcript(
        question, context.world_document(cell.question_id), cell.transcript_index or 0,
        honest_first, debater_protocol_for(context.protocol), context.client,
        debater_model=str(cell.debater_model),
        protocol_name=str(cell.transcript_protocol_name),
        turn_templates=turn_templates_for(cell, context.bundle))
    # debate_gen stamps its own key in a different scheme; the plan's is the one that links
    # to the manifest, so it wins.
    transcript["cell_key"] = cell.cell_key
    return transcript


def _run_single_call(cell: ResolvedCell, context: CellContext) -> dict:
    transcript = _transcript_for(cell, context)
    position_a, position_b, debate_text = judge_loop._format_transcript(transcript, True)

    fields: dict[str, Any] = {
        "question": transcript["question"], "position_a": position_a,
        "position_b": position_b, "debate_transcript": debate_text,
    }
    if cell.composition["judge"] == "full_document":
        fields["world_document"] = context.world_document(cell.question_id)
    if cell.composition["judge"] == "batch_same_qa":
        replayed = _dependency(cell, context, index=1)
        fields["verification_results"] = _format_replay(replayed)

    system, user = single_call_prompt(cell, context.bundle, **fields)
    seed = make_seed(cell.cell_key)
    raw = context.client.complete(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        str(cell.judge_model),
        context.protocol["decisions"]["execution_semantics"][
            "temperature_by_call_role"]["judge_verdict"],
        seed, 512, kind="verdict",
        request_metadata={"cell_key": cell.cell_key, "call_role": "batch_verdict",
                          "condition": cell.condition})
    parses = parse_both(raw)
    return {
        "cell_key": cell.cell_key, "condition": cell.condition,
        "question_id": cell.question_id, "judge_model": cell.judge_model,
        "raw_verdict_text": raw, "verdict_strict": parses["strict"],
        "verdict_pilot": parses["pilot"], "parser_version": parses["parser_version"],
        "queries_used": 0, "exchanges": [], "seed": seed,
        "harness_version": records.get_git_sha(), "created_at": records.utc_now_iso(),
    }


def _format_replay(sequential_record: dict) -> str:
    """Render the paired sequential judgment's exchanges as the batch evidence table."""
    lines = []
    for index, exchange in enumerate(sequential_record.get("exchanges", []), 1):
        if exchange.get("blocked"):
            result = exchange["blocked_feedback"]
        elif exchange.get("placebo"):
            result = PLACEBO_TEXT
        else:
            result = exchange.get("normalized")
        lines.append(f"Query {index}: {exchange['extracted_claim']}\nResult: {result}")
    return "\n\n".join(lines) if lines else "No verification results."


def _run_judgment_loop(cell: ResolvedCell, context: CellContext) -> dict:
    transcript = _transcript_for(cell, context)
    composed = judge_protocol_for(cell, context.protocol, context.bundle)

    query_gate = None
    if cell.produces_queries:
        position_a, position_b, _text = judge_loop._format_transcript(transcript, True)
        query_gate = CanaryQueryGate(
            candidate_a=position_a, candidate_b=position_b, total_slots=cell.query_budget,
            checker=FrozenCheckerAdapter(
                context.client,
                request_metadata={"cell_key": cell.cell_key, "condition": cell.condition}),
            dual_gate=DualGate(context.decision_store, context.reviewer),
            rejection_payload=composed["gate"]["rejection_payload"],
            no_query_payload=composed["gate"]["no_query_payload"],
            pause_when_unlabeled=context.pause_when_unlabeled)

    record = judge_loop.run_judgment(
        transcript, context.world_document(cell.question_id),
        ARMS[cell.arm_name or "clean"], cell.query_budget, cell.replicate_index or 0,
        context.client, composed, judge_model=str(cell.judge_model),
        query_template_override=composed["judge"]["query_phase_prompt"],
        cell_key_override=cell.cell_key, query_gate=query_gate)
    record["cell_key"] = cell.cell_key
    record["condition"] = cell.condition
    if query_gate is not None:
        record["gate_events"] = query_gate.events
        record["checker_false_allow_intercepts"] = query_gate.intercepts
    return record


def execute_cell(cell: ResolvedCell, context: CellContext) -> dict:
    """Run one cell and return its result record."""
    if cell.is_transcript:
        return _run_transcript(cell, context)
    if is_single_call(cell):
        return _run_single_call(cell, context)
    return _run_judgment_loop(cell, context)
