"""Validate the frozen Phase 2 per-role/per-model output-token limits and request settings.

This artifact freezes two related, previously-implicit pieces of policy so they cannot drift
silently between the design record and the live client:

* ``base_role_max_tokens`` -- the requested ``max_tokens`` for each call role before any
  reasoning-model floor is applied.
* ``model_role_limits`` -- the APPLICABLE (model, role) pairs actually used by the frozen
  Phase 2 roster (never a full Cartesian product), each recording both the base value and the
  effective value actually sent to the provider once the reasoning-model floor is applied.
* ``context_ceilings`` -- per-model context-window ceilings, copied from and cross-checked
  against the frozen public price snapshot.
* ``request_settings`` -- the frozen provider request-field policy: the base OpenAI-style
  request fields, which models are pinned to the streaming transport from the first attempt,
  which models get extra per-model request fields, the transport retry pin, and which
  response-metadata fields must be persisted.

Like its sibling Phase 2 artifacts, this module is read-only, offline, and deliberately cannot
establish or claim execution authority: ``execution_authorized`` is always exactly ``false``.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from rejudge import phase2_plan
from rejudge import phase2_provider_price_snapshot as price_snapshot


DEFAULT_ARTIFACT_PATH = Path(__file__).with_name("phase2_role_limits_2026-07-18.json")
DEFAULT_PROTOCOL_PATH = phase2_plan.DEFAULT_PROTOCOL_PATH
DEFAULT_SNAPSHOT_PATH = price_snapshot.DEFAULT_SNAPSHOT_PATH

SCHEMA_VERSION = "phase2_role_limits_v1"
ARTIFACT_ID = "phase2_role_limits_request_settings_2026-07-18_v1"
STATUS = "frozen_pending_manifest_binding"

# --- frozen base role limits (pre-reasoning-floor) --------------------------------------------
BASE_ROLE_MAX_TOKENS: dict[str, int] = {
    "debater_turn": 512,
    "judge_query": 256,
    "oracle": 32,
    "judge_verdict": 512,
    "batch_verdict": 512,
    "query_checker": 16,
    "capability_qa": 32,
}
FROZEN_BASE_ROLES: frozenset[str] = frozenset(BASE_ROLE_MAX_TOKENS)

# --- frozen reasoning-model set (EXACT model IDs; deliberately no prefix inference) ------------
REASONING_MODEL_IDS: tuple[str, ...] = (
    "google/gemma-4-31B-it", "openai/gpt-oss-120b", "Qwen/Qwen3.7-Plus",
)
REASONING_MODEL_ID_SET: frozenset[str] = frozenset(REASONING_MODEL_IDS)
REASONING_FLOOR_MAX_TOKENS = 4096


def effective_max_tokens(model_id: str, base: int) -> int:
    """Return the effective per-request max_tokens for ``model_id`` given its base role limit."""
    if model_id in REASONING_MODEL_ID_SET:
        return max(base, REASONING_FLOOR_MAX_TOKENS)
    return base


# --- frozen APPLICABLE (model, role) pairs -- never a full Cartesian matrix --------------------
_JUDGE_ROLES: frozenset[str] = frozenset(
    {"judge_query", "judge_verdict", "batch_verdict", "query_checker", "capability_qa"})

MODEL_ROLE_SETS: dict[str, frozenset[str]] = {
    "Qwen/Qwen2.5-7B-Instruct-Turbo": _JUDGE_ROLES,
    "google/gemma-4-31B-it": _JUDGE_ROLES,
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": frozenset(
        {"debater_turn", "judge_query", "judge_verdict", "batch_verdict", "oracle",
         "query_checker", "capability_qa"}),
    "openai/gpt-oss-120b": _JUDGE_ROLES,
    "Qwen/Qwen3.7-Plus": frozenset({"debater_turn", "capability_qa"}),
}
FROZEN_ROLE_LIMIT_MODEL_IDS: frozenset[str] = frozenset(MODEL_ROLE_SETS)

CONTEXT_CEILING_SOURCE_PATH = "rejudge/phase2_provider_price_snapshot_2026-07-18.json"
_CONTEXT_CEILING_NOTE_TEMPLATE = (
    f"Context ceiling copied from and cross-checked against {CONTEXT_CEILING_SOURCE_PATH}; "
    "that snapshot is the single source of truth for this value."
)

# --- frozen request-settings policy -------------------------------------------------------------
BASE_REQUEST_FIELDS: tuple[str, ...] = ("model", "messages", "temperature", "max_tokens", "seed")
STREAMING_PINNED_MODELS: dict[str, dict[str, Any]] = {
    "Qwen/Qwen3.7-Plus": {"stream": True, "stream_options": {"include_usage": True}},
}
PER_MODEL_EXTRA_FIELDS: dict[str, dict[str, Any]] = {
    "openai/gpt-oss-120b": {"reasoning_effort": "medium"},
}
REASONING_CONTROL_NOTE = (
    "Reasoning-effort/thinking-control request fields are DELIBERATELY OMITTED for "
    "google/gemma-4-31B-it and Qwen/Qwen3.7-Plus: exact provider support for a reasoning-control "
    "field on these two endpoints is unverified at freeze time. This is a deliberate choice under "
    "uncertainty, not a claim that no such field exists."
)
TRANSPORT_MAX_RETRIES = 3
TRANSPORT_MAX_ATTEMPTS = TRANSPORT_MAX_RETRIES + 1
RESPONSE_METADATA_TO_PERSIST: tuple[str, ...] = (
    "request_fields_sha256", "returned_model_id", "response_id", "finish_reason",
    "system_fingerprint_if_present", "prompt_tokens", "completion_tokens",
    "reasoning_tokens_if_returned",
)

TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "schema_version", "artifact_id", "protocol_id", "status", "execution_authorized",
    "base_role_max_tokens", "reasoning_models", "model_role_limits", "context_ceilings",
    "request_settings",
})
REASONING_MODELS_KEYS: frozenset[str] = frozenset({"model_ids", "floor_max_tokens"})
ROLE_ENTRY_KEYS: frozenset[str] = frozenset(
    {"base_role_max_tokens", "effective_request_max_tokens"})
CONTEXT_ENTRY_KEYS: frozenset[str] = frozenset({"context_length_tokens", "source", "note"})
REQUEST_SETTINGS_KEYS: frozenset[str] = frozenset({
    "base_fields", "streaming_pinned_models", "per_model_extra_fields",
    "reasoning_control_note", "transport", "response_metadata_to_persist",
})
TRANSPORT_KEYS: frozenset[str] = frozenset({"max_retries", "max_attempts"})


class RoleLimitsError(ValueError):
    """The frozen role-limits/request-settings artifact is malformed or disagrees with policy."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RoleLimitsError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RoleLimitsError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str] | set[str], label: str) -> None:
    if set(value) != set(expected):
        raise RoleLimitsError(f"{label} fields drifted")


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoleLimitsError(f"{label} must be an integer")
    return value


def _positive_int(value: Any, label: str) -> int:
    parsed = _int(value, label)
    if parsed <= 0:
        raise RoleLimitsError(f"{label} must be a positive integer")
    return parsed


def _non_empty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RoleLimitsError(f"{label} must be a non-empty string")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RoleLimitsError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(token: str) -> Any:
    raise RoleLimitsError(f"JSON must not contain the non-finite literal: {token}")


def _load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RoleLimitsError(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RoleLimitsError(f"{path} must contain a JSON object")
    return payload


def _validate_base_role_max_tokens(section_raw: Any) -> None:
    section = _mapping(section_raw, "base_role_max_tokens")
    _exact_keys(section, FROZEN_BASE_ROLES, "base_role_max_tokens")
    for role, expected in BASE_ROLE_MAX_TOKENS.items():
        observed = _int(section.get(role), f"base_role_max_tokens.{role}")
        if observed != expected:
            raise RoleLimitsError(
                f"base_role_max_tokens.{role} disagrees with the frozen base limit: "
                f"observed {observed}, expected {expected}")


def _validate_reasoning_models(section_raw: Any) -> None:
    section = _mapping(section_raw, "reasoning_models")
    _exact_keys(section, REASONING_MODELS_KEYS, "reasoning_models")
    model_ids = _list(section.get("model_ids"), "reasoning_models.model_ids")
    if list(model_ids) != list(REASONING_MODEL_IDS):
        raise RoleLimitsError(
            "reasoning_models.model_ids must be exactly the frozen three-model set, in frozen "
            "order, with no prefix inference"
        )
    floor = _int(section.get("floor_max_tokens"), "reasoning_models.floor_max_tokens")
    if floor != REASONING_FLOOR_MAX_TOKENS:
        raise RoleLimitsError("reasoning_models.floor_max_tokens must be exactly 4096")


def _validate_model_role_limits(
    section_raw: Any, protocol: Mapping[str, Any],
) -> None:
    section = _mapping(section_raw, "model_role_limits")

    # These two checks are evaluated purely from the live protocol -- roster and
    # model_registry -- BEFORE the artifact's own model_role_limits section is compared to
    # anything. This ordering matters: if the section-vs-FROZEN and section-vs-roster
    # equality checks below ran first, they would already force roster_models to equal
    # FROZEN_ROLE_LIMIT_MODEL_IDS by the time execution reached here, making the
    # protocol-drift guard unreachable dead code (a protocol amendment that grows or shrinks
    # the roster, even with a matching hand-edited artifact, must still be caught here).
    registry = _mapping(protocol.get("model_registry"), "protocol model_registry")
    roster = _mapping(protocol.get("roster"), "protocol roster")
    judges = _list(roster.get("judges"), "protocol roster.judges")
    debaters = _list(roster.get("debaters"), "protocol roster.debaters")
    oracle = roster.get("oracle")
    roster_models = set(judges) | set(debaters) | ({oracle} if oracle is not None else set())
    if not roster_models.issubset(set(registry)):
        raise RoleLimitsError(
            "the frozen protocol roster contains a model absent from its own model_registry")
    if not roster_models.issubset(FROZEN_ROLE_LIMIT_MODEL_IDS):
        raise RoleLimitsError(
            "the frozen protocol roster no longer matches the hardcoded model_role_limits "
            "roster in phase2_role_limits.py; this module must be updated before revalidation"
        )

    _exact_keys(section, FROZEN_ROLE_LIMIT_MODEL_IDS, "model_role_limits")
    if set(section) != roster_models:
        raise RoleLimitsError(
            "model_role_limits model set disagrees with the frozen protocol roster")

    for model_id, expected_roles in MODEL_ROLE_SETS.items():
        label = f"model_role_limits.{model_id}"
        entry = _mapping(section[model_id], label)
        _exact_keys(entry, expected_roles, label)
        if not expected_roles.issubset(FROZEN_BASE_ROLES):
            raise RoleLimitsError(f"{label} names a role outside the frozen base role set")
        for role in expected_roles:
            role_label = f"{label}.{role}"
            role_entry = _mapping(entry[role], role_label)
            _exact_keys(role_entry, ROLE_ENTRY_KEYS, role_label)
            base = _int(
                role_entry.get("base_role_max_tokens"), f"{role_label}.base_role_max_tokens")
            expected_base = BASE_ROLE_MAX_TOKENS[role]
            if base != expected_base:
                raise RoleLimitsError(
                    f"{role_label}.base_role_max_tokens disagrees with the frozen base limit: "
                    f"observed {base}, expected {expected_base}")
            effective = _int(
                role_entry.get("effective_request_max_tokens"),
                f"{role_label}.effective_request_max_tokens")
            expected_effective = effective_max_tokens(model_id, expected_base)
            if effective != expected_effective:
                raise RoleLimitsError(
                    f"{role_label}.effective_request_max_tokens disagrees with the frozen "
                    f"reasoning-floor policy: observed {effective}, expected {expected_effective}")
            # No further "must be exactly base or exactly the floor" check is needed here:
            # effective_max_tokens() is provably always either `expected_base` or
            # REASONING_FLOOR_MAX_TOKENS (see its definition above), so once the equality
            # check above passes, membership in that pair is already guaranteed -- a
            # separate check would be tautological dead code.


def _validate_context_ceilings(
    section_raw: Any, snapshot: Mapping[str, Any],
) -> None:
    section = _mapping(section_raw, "context_ceilings")
    _exact_keys(section, FROZEN_ROLE_LIMIT_MODEL_IDS, "context_ceilings")
    snapshot_models = _mapping(snapshot.get("models"), "snapshot models")
    for model_id in FROZEN_ROLE_LIMIT_MODEL_IDS:
        label = f"context_ceilings.{model_id}"
        entry = _mapping(section[model_id], label)
        _exact_keys(entry, CONTEXT_ENTRY_KEYS, label)
        observed = _positive_int(
            entry.get("context_length_tokens"), f"{label}.context_length_tokens")
        snapshot_entry = _mapping(snapshot_models.get(model_id), f"snapshot models.{model_id}")
        snapshot_context = snapshot_entry.get("context_length_tokens")
        if not isinstance(snapshot_context, int) or isinstance(snapshot_context, bool):
            raise RoleLimitsError(f"snapshot models.{model_id}.context_length_tokens is invalid")
        if observed != snapshot_context:
            raise RoleLimitsError(
                f"{label}.context_length_tokens disagrees with the frozen price snapshot: "
                f"observed {observed}, snapshot has {snapshot_context}")
        if entry.get("source") != CONTEXT_CEILING_SOURCE_PATH:
            raise RoleLimitsError(f"{label}.source must be the frozen price-snapshot path")
        note = _non_empty_str(entry.get("note"), f"{label}.note")
        if note != _CONTEXT_CEILING_NOTE_TEMPLATE:
            raise RoleLimitsError(f"{label}.note wording drifted from the frozen template")


def _validate_request_settings(section_raw: Any, protocol: Mapping[str, Any]) -> None:
    section = _mapping(section_raw, "request_settings")
    _exact_keys(section, REQUEST_SETTINGS_KEYS, "request_settings")

    base_fields = _list(section.get("base_fields"), "request_settings.base_fields")
    if list(base_fields) != list(BASE_REQUEST_FIELDS):
        raise RoleLimitsError("request_settings.base_fields must be exactly the frozen field list")

    registry = _mapping(protocol.get("model_registry"), "protocol model_registry")

    streaming = _mapping(
        section.get("streaming_pinned_models"), "request_settings.streaming_pinned_models")
    if dict(streaming) != STREAMING_PINNED_MODELS:
        raise RoleLimitsError(
            "request_settings.streaming_pinned_models must be exactly the frozen mapping")
    if not set(streaming).issubset(set(registry)):
        raise RoleLimitsError(
            "request_settings.streaming_pinned_models names a model outside the frozen registry")

    extra_fields = _mapping(
        section.get("per_model_extra_fields"), "request_settings.per_model_extra_fields")
    if dict(extra_fields) != PER_MODEL_EXTRA_FIELDS:
        raise RoleLimitsError(
            "request_settings.per_model_extra_fields must be exactly the frozen mapping")
    if not set(extra_fields).issubset(set(registry)):
        raise RoleLimitsError(
            "request_settings.per_model_extra_fields names a model outside the frozen registry")

    note = _non_empty_str(
        section.get("reasoning_control_note"), "request_settings.reasoning_control_note")
    if note != REASONING_CONTROL_NOTE:
        raise RoleLimitsError("request_settings.reasoning_control_note wording drifted")

    transport = _mapping(section.get("transport"), "request_settings.transport")
    _exact_keys(transport, TRANSPORT_KEYS, "request_settings.transport")
    max_retries = _int(transport.get("max_retries"), "request_settings.transport.max_retries")
    max_attempts = _int(transport.get("max_attempts"), "request_settings.transport.max_attempts")
    if max_retries != TRANSPORT_MAX_RETRIES:
        raise RoleLimitsError("request_settings.transport.max_retries must be pinned to 3")
    if max_attempts != TRANSPORT_MAX_ATTEMPTS or max_attempts != max_retries + 1:
        raise RoleLimitsError(
            "request_settings.transport.max_attempts must be exactly max_retries + 1 (4)")

    metadata_fields = _list(
        section.get("response_metadata_to_persist"),
        "request_settings.response_metadata_to_persist")
    if list(metadata_fields) != list(RESPONSE_METADATA_TO_PERSIST):
        raise RoleLimitsError(
            "request_settings.response_metadata_to_persist must be exactly the frozen field list")


def validate_role_limits(
    artifact: Mapping[str, Any], protocol: Mapping[str, Any], snapshot: Mapping[str, Any],
) -> None:
    """Validate the frozen role-limits/request-settings artifact, fail-closed throughout."""
    artifact = _mapping(artifact, "artifact")
    protocol = _mapping(protocol, "protocol")
    snapshot = _mapping(snapshot, "snapshot")
    _exact_keys(artifact, TOP_LEVEL_KEYS, "artifact")

    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise RoleLimitsError("unsupported role-limits schema_version")
    if artifact.get("artifact_id") != ARTIFACT_ID:
        raise RoleLimitsError("role-limits artifact_id drifted")
    protocol_id = _non_empty_str(protocol.get("protocol_id"), "protocol protocol_id")
    if artifact.get("protocol_id") != protocol_id:
        raise RoleLimitsError("role-limits protocol_id disagrees with the frozen protocol")
    if artifact.get("status") != STATUS:
        raise RoleLimitsError("role-limits status drifted")
    if artifact.get("execution_authorized") is not False:
        raise RoleLimitsError("execution_authorized must be exactly false")

    _validate_base_role_max_tokens(artifact.get("base_role_max_tokens"))
    _validate_reasoning_models(artifact.get("reasoning_models"))
    # request_settings is validated before model_role_limits so that a protocol_registry
    # mutation which drops one of the two request_settings-pinned models (Qwen/Qwen3.7-Plus
    # for streaming, openai/gpt-oss-120b for its extra field) is caught here, by the
    # request_settings-specific message, rather than always being preempted by
    # _validate_model_role_limits's own (broader) roster/registry check -- both checks stay
    # individually reachable and exercised: this one for its two pinned models, the
    # model_role_limits one for the remaining roster models it alone covers.
    _validate_request_settings(artifact.get("request_settings"), protocol)
    _validate_model_role_limits(artifact.get("model_role_limits"), protocol)
    _validate_context_ceilings(artifact.get("context_ceilings"), snapshot)


def load_and_validate(
    artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol = phase2_plan.load_protocol(protocol_path)
    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(snapshot_path, protocol_path)
    artifact = _load_json(artifact_path)
    validate_role_limits(artifact, protocol, snapshot)
    return artifact, protocol, snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT_PATH))
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL_PATH))
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT_PATH))
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("only --check is supported")
    artifact, _protocol, _snapshot = load_and_validate(args.artifact, args.protocol, args.snapshot)
    print(
        "verified frozen Phase 2 role-limits/request-settings artifact; "
        f"models={len(artifact['model_role_limits'])}; "
        f"canonical_sha256={phase2_plan.canonical_sha256(artifact)}; "
        "execution_authorized=NO"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
