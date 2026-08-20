"""scripts/phase3_context_precheck.py: the deterministic ex-ante context-exclusion blocklist.

Hermetic and offline throughout: every template comes from the real, frozen
``rejudge/phase2_prompt_bundle.json`` (reused byte-identical, so composing against it is
composing against the exact templates a live run would), but the PROTOCOL and PLAN CELLS in
most tests here are small, hand-built fixtures -- ``compute_blocklist`` itself needs only a
``debate_grid.conditions`` shape and a list of cell dicts, never the frozen protocol's own
whole-document hash, so a synthetic protocol exercises the real estimator arithmetic without
paying for the full 1,728/34,932-cell enumeration in every test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rejudge import phase3_plan
from rejudge.api_client import _estimate_usage, estimate_context_tokens
from scripts.phase3_context_precheck import (
    ContextPrecheckError,
    base_presentation_messages,
    build_report,
    compute_blocklist,
    load_no_query_payload,
    worst_case_query_history_and_verdict_messages,
)

ROOT = Path(__file__).resolve().parents[1]
JUDGMENT_KIND = phase3_plan.CANARY_JUDGMENT_KIND


def _bundle() -> dict:
    return json.loads((ROOT / "rejudge" / "phase2_prompt_bundle.json").read_text(encoding="utf-8"))


def _synthetic_protocol() -> dict:
    return {
        "cell_key_namespace": "synthetic-precheck-test.qb-deadbeef0000",
        "debate_grid": {"conditions": [
            {"id": "b0", "query_budget": 0, "oracle_mode": "none"},
            {"id": "sequential_b1", "query_budget": 1, "oracle_mode": "clean"},
            {"id": "sequential_b8", "query_budget": 8, "oracle_mode": "clean"},
        ]},
    }


def _synthetic_transcript(question_id: str = "Q1", n_turns: int = 6, turn_len: int = 400) -> dict:
    return {
        "question_id": question_id, "world": "w", "question": "Is the claim true?",
        "correct_answer": "Yes, per the document.", "wrong_answer": "No, per the document.",
        "honest_first": True,
        "debate_transcript": [
            {"speaker": "honest" if i % 2 == 0 else "dishonest",
             "text": ("argument " + str(i)) * (turn_len // 10), "round": i // 2 + 1}
            for i in range(n_turns)
        ],
    }


def _cell(cell_key: str, condition: str, *, judge_model: str, debater_model: str = "debater-a",
         question_id: str = "Q1", transcript_index: int = 0) -> dict:
    return {"cell_key": cell_key, "kind": JUDGMENT_KIND, "condition": condition,
           "judge_model": judge_model, "debater_model": debater_model,
           "question_id": question_id, "transcript_index": transcript_index}


def _role_limits(judge_model: str, *, ceiling: int, judge_query_max: int = 256,
                 judge_verdict_max: int = 512) -> dict:
    return {
        "model_role_limits": {judge_model: {
            "judge_query": {"effective_request_max_tokens": judge_query_max},
            "judge_verdict": {"effective_request_max_tokens": judge_verdict_max},
        }},
        "context_ceilings": {judge_model: {"context_length_tokens": ceiling}},
    }


# ---------------------------------------------------------------------------
# worst-case message construction: real templates, checked shape
# ---------------------------------------------------------------------------


def test_base_presentation_is_side_independent_and_uses_the_real_templates():
    bundle = _bundle()
    presentation_template = bundle["templates"]["sequential_judge_presentation"]
    transcript = _synthetic_transcript()
    messages = base_presentation_messages(transcript, presentation_template)
    assert messages[0] == {"role": "system", "content": presentation_template["system_prompt"]}
    assert messages[1]["role"] == "user"
    assert transcript["question"] in messages[1]["content"]
    assert transcript["correct_answer"] in messages[1]["content"]
    assert transcript["wrong_answer"] in messages[1]["content"]


def test_worst_case_query_history_scales_with_budget_and_uses_frozen_payloads():
    bundle = _bundle()
    templates = bundle["templates"]
    no_query_payload = load_no_query_payload(ROOT)
    kwargs = dict(
        judge_query_role_max_tokens=256,
        query_template=templates["sequential_judge_query"]["user_prompt_template"],
        verdict_template=templates["sequential_judge_verdict"]["user_prompt_template"],
        rejection_payload=templates["sequential_judge_rejection"]["payload"],
        no_query_payload=no_query_payload)
    zero = worst_case_query_history_and_verdict_messages(query_budget=0, **kwargs)
    one = worst_case_query_history_and_verdict_messages(query_budget=1, **kwargs)
    eight = worst_case_query_history_and_verdict_messages(query_budget=8, **kwargs)
    # budget 0: only the verdict message, query_results empty (matches judge_loop.run_judgment's
    # own "" empty case, not _format_previous's "No queries submitted yet.").
    assert len(zero) == 1
    assert "query_results" not in zero[0]["content"]  # placeholder substituted, literal name gone
    assert "VERIFICATION RESULTS" not in zero[0]["content"]
    assert "VERIFICATION RESULTS" in eight[-1]["content"]
    # Every free retry (2 attempts/slot) contributes 5 messages/round + 1 verdict message.
    assert len(one) == 5 + 1
    assert len(eight) == 8 * 5 + 1
    tokens_one = sum(_estimate_usage([m], 0)[0] for m in one)
    tokens_eight = sum(_estimate_usage([m], 0)[0] for m in eight)
    assert tokens_eight > tokens_one  # quadratic growth (previous_queries repeats every round)


# ---------------------------------------------------------------------------
# exclusion: a tiny-ceiling model excludes the long-transcript b8 cell, never b0
# ---------------------------------------------------------------------------


def test_tiny_ceiling_excludes_long_b8_but_not_b0():
    bundle = _bundle()
    templates = bundle["templates"]
    transcript = _synthetic_transcript(n_turns=8, turn_len=600)
    presentation_template = templates["sequential_judge_presentation"]
    base_messages = base_presentation_messages(transcript, presentation_template)
    verdict_max = 512

    _, b0_tokens = estimate_context_tokens(base_messages, verdict_max)

    history = worst_case_query_history_and_verdict_messages(
        query_budget=8, judge_query_role_max_tokens=256,
        query_template=templates["sequential_judge_query"]["user_prompt_template"],
        verdict_template=templates["sequential_judge_verdict"]["user_prompt_template"],
        rejection_payload=templates["sequential_judge_rejection"]["payload"],
        no_query_payload=load_no_query_payload(ROOT))
    _, b8_tokens = estimate_context_tokens(base_messages + history, verdict_max)
    assert b8_tokens > b0_tokens, "the b8 fixture must be worse-case than b0 for this test to mean anything"

    judge = "tiny-ceiling-judge"
    ceiling = (b0_tokens + b8_tokens) // 2  # strictly between the two -- b0 survives, b8 doesn't
    role_limits = _role_limits(judge, ceiling=ceiling, judge_verdict_max=verdict_max)
    transcript_index = {("debater-a", "Q1", 0): transcript}
    plan_cells = [
        _cell("b0-key", "b0", judge_model=judge),
        _cell("b8-key", "sequential_b8", judge_model=judge),
    ]

    result = compute_blocklist(
        protocol=_synthetic_protocol(), bundle=bundle, role_limits=role_limits,
        plan_cells=plan_cells, transcript_index=transcript_index, scope="canary")

    excluded_keys = {e["cell_key"] for e in result["excluded"]}
    assert "b8-key" in excluded_keys
    assert "b0-key" not in excluded_keys
    assert result["counts"] == [{"judge_model": judge, "condition": "sequential_b8", "count": 1}]
    assert result["ceilings_used"] == {judge: ceiling}


def test_a_generous_ceiling_excludes_nothing():
    bundle = _bundle()
    transcript = _synthetic_transcript()
    judge = "roomy-judge"
    role_limits = _role_limits(judge, ceiling=10**9)
    result = compute_blocklist(
        protocol=_synthetic_protocol(), bundle=bundle, role_limits=role_limits,
        plan_cells=[_cell("b8-key", "sequential_b8", judge_model=judge)],
        transcript_index={("debater-a", "Q1", 0): transcript}, scope="canary")
    assert result["excluded"] == []


def test_an_unknown_condition_refuses():
    bundle = _bundle()
    with pytest.raises(ContextPrecheckError, match="not in the frozen protocol"):
        compute_blocklist(
            protocol=_synthetic_protocol(), bundle=bundle,
            role_limits=_role_limits("j", ceiling=10**9),
            plan_cells=[_cell("k", "no-such-condition", judge_model="j")],
            transcript_index={}, scope="canary")


def test_a_missing_transcript_refuses():
    bundle = _bundle()
    with pytest.raises(ContextPrecheckError, match="not in the frozen transcript bundle"):
        compute_blocklist(
            protocol=_synthetic_protocol(), bundle=bundle,
            role_limits=_role_limits("j", ceiling=10**9),
            plan_cells=[_cell("k", "b0", judge_model="j")],
            transcript_index={}, scope="canary")


# ---------------------------------------------------------------------------
# determinism: same inputs -> byte-identical report
# ---------------------------------------------------------------------------


def test_build_report_is_deterministic():
    bundle = _bundle()
    transcript = _synthetic_transcript()
    judge = "det-judge"
    kwargs = dict(
        protocol=_synthetic_protocol(), bundle=bundle,
        role_limits=_role_limits(judge, ceiling=1500),
        plan_cells=[_cell("b0-key", "b0", judge_model=judge),
                   _cell("b1-key", "sequential_b1", judge_model=judge),
                   _cell("b8-key", "sequential_b8", judge_model=judge)],
        transcript_index={("debater-a", "Q1", 0): transcript}, scope="canary",
        generated_at="2026-08-19T00:00:00Z")
    first = build_report(**kwargs)
    second = build_report(**kwargs)
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["cell_key_namespace"] == "synthetic-precheck-test.qb-deadbeef0000"
    assert first["estimator_provenance"] == {
        "module": "rejudge.api_client", "function": "estimate_context_tokens"}
