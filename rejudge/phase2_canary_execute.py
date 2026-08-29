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

**Phase-3 transcript-generation guard, additive.** Phase 3 reuses phase-2 transcripts
byte-identically and makes ZERO debater calls
(``rejudge/phase3_protocol.json`` ``decisions.transcript_reuse.ingestion_mechanics``): a
phase-3 manifest carries ``transcript_generation_forbidden: true``, and a caller building a
:class:`CellContext` for a phase-3 run sets ``CellContext.transcript_generation_forbidden``
from that flag. :func:`execute_cell` checks it before dispatching to any of the three call
patterns above, so a transcript cell that somehow reaches this module unseeded halts with
:class:`GenerationForbiddenError` before any provider call rather than silently generating a
transcript. The field defaults to ``False`` and every phase-2 call site constructs
``CellContext`` without it, so phase-2 behavior is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rejudge import debate_gen, judge_loop, records
from rejudge.config import ARMS, PLACEBO_TEXT, make_seed, position_for
from rejudge.parsers import parse_both
from rejudge.phase2_canary_cells import ResolvedCell, base_kind
from rejudge.phase2_canary_compose import (
    debater_protocol_for, is_single_call, judge_protocol_for, single_call_prompt,
    turn_templates_for)
from rejudge.phase2_canary_gate import CanaryQueryGate, FrozenCheckerAdapter
from rejudge.phase2_dual_gate import DualGate, DualGateDecisionStore


class MissingTranscript(KeyError):
    """Raised when a cell's dependency has not been executed yet."""


class GenerationForbiddenError(RuntimeError):
    """Raised when a transcript-generation cell would run under a manifest that forbids it.

    Phase 3 pre-seeds every transcript-reference cell key into the result store
    (``scripts/phase3_preseed_transcripts.py``) before a run ever starts, so a cell reaching
    this error means the pre-seeding did not cover it -- a bug in the seeding, not a case to
    silently fall back to live generation for. Always raised before any provider call.
    """


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
    # Additive, default-off: see the module docstring's "Phase-3 transcript-generation guard"
    # section. Phase-2 call sites never pass this, so phase-2 behavior is unchanged.
    transcript_generation_forbidden: bool = False
    # Additive, default-off (amendment 4, 2026-08-19): the bound role-limits artifact, threaded
    # into judge_loop.run_judgment to activate the mechanically enforced visible-history byte
    # cap. Phase-2 call sites never pass this, so phase-2 behavior is unchanged.
    role_limits: dict | None = None
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


class PolarityMismatch(ValueError):
    """Raised when a batch replay and the sequential it replays disagree on A/B order."""


def _polarity(cell: ResolvedCell) -> bool:
    """Which side of this cell's presentation carries the correct answer.

    The single source of truth for A/B order, computed exactly as judge_loop.run_judgment
    computes it. It exists because both other call sites used to pass a hardcoded ``True``
    instead, which the canary showed costs two distinct things: the single-call conditions
    were never counterbalanced at all, and the query gate was handed the two candidate
    answers under labels swapped from the ones the judge actually read (224 of 332 gated
    cells). Deriving both from one function is what stops them drifting apart again.

    **Mirroring fix (2026-08-21), additive.** ``position_for`` (via
    ``analysis.infra.design.position_a_is_correct``) is a pure function of
    ``(question_id, transcript_index)`` -- the per-question BASE draw, held fixed by design. It
    carries no side input, so before this fix every K2 slot of a cell rendered the IDENTICAL
    label order: duplication, not the mirroring the frozen protocols always claimed (see
    ``rejudge/phase3_incident1_mirroring_2026-08-21.json``). ``cell.judgment_replicates_per_side``
    is the ADDITIVE unfold key: when a resolver supplies it (currently only
    ``rejudge.phase3_runner._resolve_judgment_cell``, from the frozen protocol's
    ``judgment_replicates_per_transcript_side``), ``cell.replicate_index`` is known to be
    ``side * replicates_per_side + within_side_replicate`` (see ``phase3_plan.enumerate_cells``/
    ``enumerate_canary_cells``'s docstrings), so the side bit is recovered by integer division
    and XORed against the base draw: side 0 keeps the base polarity, side 1 flips it. When it is
    ``None`` (every phase-2 cell, resolved by this module's own :func:`resolve_cell`, whose
    ``replicate_index`` is a plain K2 index with no side folded into it at all), this is a no-op
    and the return value is byte-identical to the pre-fix function.
    """
    base = position_for(ARMS[cell.arm_name or "clean"], cell.question_id,
                        cell.transcript_index or 0, str(cell.judge_model),
                        cell.query_budget)
    replicates_per_side = cell.judgment_replicates_per_side
    if not replicates_per_side:
        return base
    side = (cell.replicate_index or 0) // replicates_per_side
    return base if side % 2 == 0 else not base


def _transcript_for(cell: ResolvedCell, context: CellContext) -> dict:
    """The debate a judgment is judging.

    No-debate conditions have none, so they get a transcript-shaped stand-in carrying the
    candidates and an empty debate. The frozen no_debate template declares no
    debate_transcript placeholder, so the empty text is never rendered; it exists only
    because judge_loop reads the candidates off this structure.
    """
    if base_kind(cell.kind) == "no_debate_judgment":
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
    pos_a_correct = _polarity(cell)
    position_a, position_b, debate_text = judge_loop._format_transcript(
        transcript, pos_a_correct)

    fields: dict[str, Any] = {
        "question": transcript["question"], "position_a": position_a,
        "position_b": position_b, "debate_transcript": debate_text,
    }
    if cell.composition["judge"] == "full_document":
        fields["world_document"] = context.world_document(cell.question_id)
    if cell.composition["judge"] == "batch_same_qa":
        replayed = _dependency(cell, context, index=1)
        # A batch replay is only "the same information, differently packaged" if both halves
        # put the same answer at Position A. Refuse rather than silently produce a cell whose
        # contrast with its sequential pair is meaningless.
        replayed_polarity = replayed.get("position_a_is_correct")
        if replayed_polarity is not None and bool(replayed_polarity) != pos_a_correct:
            raise PolarityMismatch(
                f"cell {cell.cell_key} would replay {cell.dependency_keys[1]} at "
                f"position_a_is_correct={pos_a_correct}, but that cell judged at "
                f"{bool(replayed_polarity)}")
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
    # Built through records.build_record like every other judgment, so a single-call row is
    # self-describing: it used to omit position_a_is_correct, transcript_index and
    # verdict_correct_*, which left the batch condition's correctness unreadable from the row.
    # queries_used stays 0 because this cell issues no query of its own; the plan's budget is
    # recorded separately and is what the analysis groups on.
    record = records.build_record(
        transcript=transcript, arm=ARMS[cell.arm_name or "clean"], budget=cell.query_budget,
        replicate=cell.replicate_index or 0, position_a_is_correct=pos_a_correct,
        exchanges=[], raw_verdict_text=raw, parses=parse_both(raw),
        judge_messages=[{"role": "system", "content": system},
                        {"role": "user", "content": user},
                        {"role": "assistant", "content": raw}],
        seed=seed, judge_model=str(cell.judge_model),
        oracle_model="none (single call)",
        dry_run=getattr(context.client, "dry_run", False), queries_used=0)
    # The plan's identity wins over the transcript's, exactly as it does for the cell key.
    record["cell_key"] = cell.cell_key
    record["condition"] = cell.condition
    record["question_id"] = cell.question_id
    record["transcript_index"] = cell.transcript_index or 0
    return record


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


def _run_judgment_loop(cell: ResolvedCell, context: CellContext, *,
                       debater_model=None, namespace=None) -> dict:
    """``debater_model``/``namespace`` are optional, keyword-only, and forwarded to
    ``judge_loop.run_judgment`` unchanged; left at ``None`` (every phase-2 call site) the
    computed seed is unchanged from phase-2 behavior. See judgment_seed's docstring.
    """
    transcript = _transcript_for(cell, context)
    composed = judge_protocol_for(cell, context.protocol, context.bundle)

    pos_a_correct = _polarity(cell)

    query_gate = None
    rejection_payload = no_query_payload = None
    if cell.produces_queries:
        position_a, position_b, _text = judge_loop._format_transcript(
            transcript, pos_a_correct)
        rejection_payload = composed["gate"]["rejection_payload"]
        no_query_payload = composed["gate"]["no_query_payload"]
        # Amendment 4 (2026-08-19), package item 2: additive, default-off (context.role_limits
        # is None on every phase-2 call site, so max_response_bytes stays None and
        # CanaryQueryGate/Phase2QueryGate behavior is unchanged). The gate -- never this loop --
        # must own the over-cap classification; see Phase2QueryGate's max_response_bytes.
        max_response_bytes = None
        if context.role_limits is not None:
            max_response_bytes = judge_loop.visible_history_cap_bytes(
                judge_loop.judge_query_effective_max_tokens(
                    context.role_limits, str(cell.judge_model)))
        # Amendment 9 fix (r29): the checker MODEL follows the protocol roster, not the
        # frozen phase-2 config, whenever a phase-3 role-limits artifact is present. For
        # every phase-2 call site (role_limits None) and every protocol through r5 the
        # resolved model equals the config model, so sealed history is byte-identical.
        checker_model_override = None
        checker_max_tokens_override = None
        if context.role_limits is not None:
            checker_model_override = str(context.protocol["roster"]["query_checker"])
            checker_entry = context.role_limits["model_role_limits"][
                checker_model_override]["query_checker"]
            checker_max_tokens_override = int(
                checker_entry["effective_request_max_tokens"])
        query_gate = CanaryQueryGate(
            candidate_a=position_a, candidate_b=position_b, total_slots=cell.query_budget,
            checker=FrozenCheckerAdapter(
                context.client,
                request_metadata={"cell_key": cell.cell_key, "condition": cell.condition},
                model_override=checker_model_override,
                max_tokens_override=checker_max_tokens_override),
            dual_gate=DualGate(context.decision_store, context.reviewer),
            rejection_payload=rejection_payload,
            no_query_payload=no_query_payload,
            pause_when_unlabeled=context.pause_when_unlabeled,
            max_response_bytes=max_response_bytes)

    record = judge_loop.run_judgment(
        transcript, context.world_document(cell.question_id),
        ARMS[cell.arm_name or "clean"], cell.query_budget, cell.replicate_index or 0,
        context.client, composed, judge_model=str(cell.judge_model),
        query_template_override=composed["judge"]["query_phase_prompt"],
        cell_key_override=cell.cell_key, query_gate=query_gate,
        # Passed explicitly rather than recomputed inside run_judgment: the gate above was
        # built from this same value, and the two must not be able to drift apart.
        position_override=pos_a_correct,
        debater_model=debater_model, namespace=namespace,
        # Amendment 4 (2026-08-19): additive, default-off (context.role_limits is None on every
        # phase-2 call site). rejection_payload/no_query_payload are the SAME texts just used to
        # build query_gate above, reused verbatim rather than duplicated.
        role_limits=context.role_limits,
        rejection_payload=rejection_payload, no_query_payload=no_query_payload)
    record["cell_key"] = cell.cell_key
    record["condition"] = cell.condition
    if query_gate is not None:
        record["gate_events"] = query_gate.events
        record["checker_false_allow_intercepts"] = query_gate.intercepts
    return record


def execute_cell(cell: ResolvedCell, context: CellContext, *,
                 debater_model=None, namespace=None) -> dict:
    """Run one cell and return its result record.

    ``debater_model``/``namespace`` are optional, keyword-only, and reach the judgment path
    only (the loop-shaped judgment case): they thread through to
    ``judge_loop.run_judgment``'s seed derivation. Left at ``None`` (every phase-2 call
    site), behavior is unchanged. Transcript and single-call cells do not use them; a
    single-call cell already derives its seed from ``cell.cell_key`` alone.

    ``context.transcript_generation_forbidden`` is checked first, before any other dispatch:
    see the module docstring's "Phase-3 transcript-generation guard" section.
    """
    if cell.is_transcript and context.transcript_generation_forbidden:
        raise GenerationForbiddenError(
            f"cell {cell.cell_key} would generate a transcript, but this run's manifest sets "
            "transcript_generation_forbidden=True; phase-3 reuses phase-2 transcripts "
            "byte-identically and must never make a debater call. Pre-seed this cell key via "
            "scripts/phase3_preseed_transcripts.py before running.")
    if cell.is_transcript:
        return _run_transcript(cell, context)
    if is_single_call(cell):
        return _run_single_call(cell, context)
    return _run_judgment_loop(cell, context, debater_model=debater_model, namespace=namespace)
