"""Exact static prompt construction for the Phase 3 v3 spend forecast.

The live judge loop adds model responses, gate feedback, and oracle results over time. This
module renders only the deterministic lower context shared by a possible call. The successor
forecast subtracts that exact static context from canary usage and treats the remainder as a
dynamic-history residual. No function here can call a provider.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from rejudge import composer, judge_loop, phase2_canary_gate, phase3_plan
from rejudge.config import ARMS, position_for
from rejudge.phase2_execution import canonical_sha256


SIDE_COUNT = 2
FORECAST_ROLES = frozenset({
    "judge_query", "judge_verdict", "query_checker", "oracle_verification",
})
CHECKER_SYSTEM_PROMPT_SHA256 = (
    "ecb22b55af091a2dc35c3f46e145db9c6796b2517a83dfec8682b75eb58e7428")
CHECKER_USER_TEMPLATE_SHA256 = (
    "72e588cdf325c14193b421d4a4b2a2e2a3957b1afe04e510ab54201fab08d168")


class StaticPromptError(ValueError):
    """Raised when a static prompt cannot be derived from frozen runtime inputs."""


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise StaticPromptError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise StaticPromptError(f"{label} must be a non-empty string")
    return value


def transcript_identity(entry: Mapping[str, Any]) -> dict[str, Any]:
    payload = _object(entry.get("transcript_payload"), "transcript.transcript_payload")
    identity = {
        "debater_model": _text(entry.get("debater_model"), "transcript.debater_model"),
        "question_id": _text(entry.get("question_id"), "transcript.question_id"),
        "transcript_index": entry.get("transcript_index"),
        "transcript_sha256": _text(
            entry.get("transcript_sha256"), "transcript.transcript_sha256"),
    }
    transcript_index = identity["transcript_index"]
    if (isinstance(transcript_index, bool) or not isinstance(transcript_index, int)
            or transcript_index < 0):
        raise StaticPromptError("transcript.transcript_index must be a non-negative integer")
    if len(identity["transcript_sha256"]) != 64 or any(
            character not in "0123456789abcdef"
            for character in identity["transcript_sha256"]):
        raise StaticPromptError("transcript.transcript_sha256 must be a lowercase SHA-256")
    for field in ("debater_model", "question_id", "transcript_index"):
        if payload.get(field) != identity[field]:
            raise StaticPromptError(f"transcript payload identity differs at {field}")
    if canonical_sha256(payload) != identity["transcript_sha256"]:
        raise StaticPromptError("transcript payload canonical hash mismatch")
    return identity


def transcript_key(entry: Mapping[str, Any]) -> str:
    return canonical_sha256(transcript_identity(entry))


def expected_role_variants(
    protocol: Mapping[str, Any], model: str,
) -> dict[str, list[str]]:
    """Return every possible static context variant billed to ``model``.

    Judge prompts vary by condition and mirrored replicate. Checker prompts additionally vary
    by source judge because the deterministic A/B assignment includes the source judge model.
    The oracle lower context is side-independent and uses an empty dynamic claim.
    """
    phase3_plan.validate_protocol(protocol)
    registry = _object(protocol.get("model_registry"), "protocol.model_registry")
    models = _object(registry.get("models"), "protocol.model_registry.models")
    entry = _object(models.get(model), f"protocol.model_registry.models[{model!r}]")
    billed_roles = entry.get("billed_roles")
    if (not isinstance(billed_roles, list)
            or not all(isinstance(role, str) and role for role in billed_roles)):
        raise StaticPromptError(f"protocol billed roles are invalid for {model}")

    raw_conditions = protocol["debate_grid"]["conditions"]
    conditions = [(str(condition["id"]), int(condition["query_budget"]))
                  for condition in raw_conditions]
    query_conditions = [(condition_id, budget) for condition_id, budget in conditions
                        if budget > 0]
    variants_by_role: dict[str, list[str]] = {
        "judge_query": [
            f"judge_query::{condition_id}::side{side}"
            for condition_id, _budget in query_conditions
            for side in range(SIDE_COUNT)
        ],
        "judge_verdict": [
            f"judge_verdict::{condition_id}::side{side}"
            for condition_id, _budget in conditions
            for side in range(SIDE_COUNT)
        ],
        "query_checker": [
            f"query_checker::{source_judge}::{condition_id}::side{side}"
            for source_judge in protocol["roster"]["judges_final"]
            for condition_id, _budget in query_conditions
            for side in range(SIDE_COUNT)
        ],
        "oracle_verification": ["oracle_verification"],
    }
    return {
        role: variants_by_role[role]
        for role in billed_roles
        if role in FORECAST_ROLES
    }


def _condition(protocol: Mapping[str, Any], condition_id: str) -> Mapping[str, Any]:
    matches = [condition for condition in protocol["debate_grid"]["conditions"]
               if condition.get("id") == condition_id]
    if len(matches) != 1:
        raise StaticPromptError(f"unknown or duplicate condition {condition_id!r}")
    return matches[0]


def _side_from_token(token: str) -> int:
    if not token.startswith("side") or token[4:] not in {"0", "1"}:
        raise StaticPromptError(f"invalid mirrored-side token {token!r}")
    return int(token[4:])


def _variant_context(
    protocol: Mapping[str, Any], billed_model: str, role: str, variant_id: str,
) -> tuple[str, Mapping[str, Any] | None, int | None]:
    parts = variant_id.split("::")
    if role in {"judge_query", "judge_verdict"}:
        if len(parts) != 3 or parts[0] != role:
            raise StaticPromptError(f"invalid {role} variant {variant_id!r}")
        condition = _condition(protocol, parts[1])
        if role == "judge_query" and int(condition["query_budget"]) == 0:
            raise StaticPromptError("judge_query has no budget-zero static variant")
        return billed_model, condition, _side_from_token(parts[2])
    if role == "query_checker":
        if len(parts) != 4 or parts[0] != role:
            raise StaticPromptError(f"invalid query_checker variant {variant_id!r}")
        source_judge = parts[1]
        if source_judge not in protocol["roster"]["judges_final"]:
            raise StaticPromptError("query_checker variant names a non-roster source judge")
        condition = _condition(protocol, parts[2])
        if int(condition["query_budget"]) == 0:
            raise StaticPromptError("query_checker has no budget-zero static variant")
        return source_judge, condition, _side_from_token(parts[3])
    if role == "oracle_verification":
        if variant_id != role:
            raise StaticPromptError(f"invalid oracle variant {variant_id!r}")
        return billed_model, None, None
    raise StaticPromptError(f"unsupported forecast role {role!r}")


def _position_a_is_correct(
    transcript: Mapping[str, Any], source_judge: str,
    condition: Mapping[str, Any], side: int,
) -> bool:
    base = position_for(
        ARMS["clean"], transcript["question_id"], transcript["transcript_index"],
        source_judge, int(condition["query_budget"]),
    )
    return base if side == 0 else not base


def render_static_messages(
    *,
    protocol: Mapping[str, Any],
    prompt_bundle: Mapping[str, Any],
    world_documents: Mapping[str, str],
    transcript_entry: Mapping[str, Any],
    billed_model: str,
    role: str,
    variant_id: str,
) -> list[dict[str, str]]:
    """Render the exact deterministic lower context for one possible billed call."""
    registry = _object(protocol.get("model_registry"), "protocol.model_registry")
    models = _object(registry.get("models"), "protocol.model_registry.models")
    model_entry = _object(
        models.get(billed_model), f"protocol.model_registry.models[{billed_model!r}]")
    if role not in (model_entry.get("billed_roles") or []):
        raise StaticPromptError(f"{role} is not billed to {billed_model}")
    transcript_identity(transcript_entry)
    transcript = _object(
        transcript_entry.get("transcript_payload"), "transcript.transcript_payload")
    source_judge, condition, side = _variant_context(
        protocol, billed_model, role, variant_id)
    templates = _object(prompt_bundle.get("templates"), "prompt_bundle.templates")

    if role == "oracle_verification":
        world = _text(transcript.get("world"), "transcript_payload.world")
        world_document = _text(world_documents.get(world), f"world document {world!r}")
        template = _object(templates.get("oracle"), "templates.oracle")
        user = composer.compose_oracle_prompt(
            _text(template.get("user_prompt_template"), "oracle.user_prompt_template"),
            world_document,
            "",
        )
        return [
            {"role": "system", "content": _text(
                template.get("system_prompt"), "oracle.system_prompt")},
            {"role": "user", "content": user},
        ]

    assert condition is not None and side is not None
    position_a_is_correct = _position_a_is_correct(
        transcript, source_judge, condition, side)
    position_a, position_b, debate_text = judge_loop._format_transcript(
        dict(transcript), position_a_is_correct)

    if role == "query_checker":
        system = phase2_canary_gate.load_frozen_checker_prompt()
        user_template = phase2_canary_gate.load_frozen_checker_user_template()
        if hashlib.sha256(system.encode("utf-8")).hexdigest() != CHECKER_SYSTEM_PROMPT_SHA256:
            raise StaticPromptError("frozen query-checker system prompt drifted")
        if (hashlib.sha256(user_template.encode("utf-8")).hexdigest()
                != CHECKER_USER_TEMPLATE_SHA256):
            raise StaticPromptError("frozen query-checker user template drifted")
        user = user_template.format(candidate_a=position_a, candidate_b=position_b, query="")
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    composition_key = (
        "b0" if int(condition["query_budget"]) == 0 else "sequential_b2")
    compositions = _object(
        _object(prompt_bundle.get("condition_composition"),
                "prompt_bundle.condition_composition").get("debate_grid"),
        "prompt_bundle.condition_composition.debate_grid",
    )
    composition = _object(
        compositions.get(composition_key), f"debate_grid.{composition_key}")
    presentation = _object(
        templates.get(composition["presentation"]), "judge presentation template")
    messages = [
        {"role": "system", "content": _text(
            presentation.get("system_prompt"), "presentation.system_prompt")},
        {"role": "user", "content": _text(
            presentation.get("user_prompt_template"),
            "presentation.user_prompt_template",
        ).format(
            question=transcript["question"],
            position_a=position_a,
            position_b=position_b,
            debate_transcript=debate_text,
        )},
    ]
    if role == "judge_query":
        query_template = _object(
            templates.get(composition["query"]), "judge query template")
        budget = int(condition["query_budget"])
        messages.append({
            "role": "user",
            "content": _text(
                query_template.get("user_prompt_template"),
                "query.user_prompt_template",
            ).format(
                remaining_budget=budget,
                total_budget=budget,
                previous_queries=judge_loop._format_previous([]),
            ),
        })
        return messages
    if role == "judge_verdict":
        verdict_template = _object(
            templates.get(composition["verdict"]), "judge verdict template")
        messages.append({
            "role": "user",
            "content": _text(
                verdict_template.get("user_prompt_template"),
                "verdict.user_prompt_template",
            ).format(query_results=""),
        })
        return messages
    raise StaticPromptError(f"unsupported forecast role {role!r}")


def render_chat_prompt(tokenizer: Any, messages: Sequence[Mapping[str, str]]) -> tuple[str, int]:
    """Apply one local tokenizer's own chat template and return text plus exact token count."""
    try:
        rendered = tokenizer.apply_chat_template(
            list(messages), tokenize=False, add_generation_prompt=True)
        token_ids = tokenizer.apply_chat_template(
            list(messages), tokenize=True, add_generation_prompt=True)
    except Exception as exc:  # noqa: BLE001 - normalize tokenizer-specific failures
        raise StaticPromptError(f"tokenizer chat-template rendering failed: {exc}") from exc
    if not isinstance(rendered, str) or not rendered:
        raise StaticPromptError("tokenizer chat template did not return rendered text")
    if isinstance(token_ids, Mapping):
        token_ids = token_ids.get("input_ids")
    if not isinstance(token_ids, (list, tuple)) or not token_ids:
        raise StaticPromptError("tokenizer chat template did not return token IDs")
    if token_ids and isinstance(token_ids[0], (list, tuple)):
        if len(token_ids) != 1:
            raise StaticPromptError("tokenizer returned an unexpected batched prompt")
        token_ids = token_ids[0]
    return rendered, len(token_ids)
