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
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rejudge import phase2_plan
from rejudge import phase2_provider_price_snapshot as price_snapshot


DEFAULT_ARTIFACT_PATH = Path(__file__).with_name("phase2_role_limits_2026-07-18.json")
DEFAULT_V2_ARTIFACT_PATH = Path(__file__).with_name("phase2_role_limits_v2_2026-07-18.json")
DEFAULT_V3_ARTIFACT_PATH = Path(__file__).with_name("phase2_role_limits_v3_2026-07-19.json")
DEFAULT_PROTOCOL_PATH = phase2_plan.DEFAULT_PROTOCOL_PATH
DEFAULT_SNAPSHOT_PATH = price_snapshot.DEFAULT_SNAPSHOT_PATH
# project_root for the v3 approval_basis raw-file check only; every other v3 check stays a
# pure in-memory validation, matching v1/v2. Computed once, from this file's own location,
# rather than threaded through as a required argument on every caller.
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = "phase2_role_limits_v1"
ARTIFACT_ID = "phase2_role_limits_request_settings_2026-07-18_v1"
STATUS = "frozen_pending_manifest_binding"

# --- v2: role_taxonomy + supersedes binding on top of the frozen v1 content -------------------
#
# v2 is additive, never a rewrite: every v1-checked section (base_role_max_tokens,
# reasoning_models, model_role_limits, context_ceilings, request_settings) is validated with
# the EXACT SAME private helpers used for v1 below (``_validate_base_role_max_tokens`` etc.),
# so v1 and v2 can never silently diverge on what they consider "the frozen limits". v2 adds
# exactly two things: a ``supersedes`` block binding the real v1 artifact's own canonical hash
# (recomputed from disk, never trusted from the v2 artifact alone), and a ``role_taxonomy``
# section mapping each of the seven limits roles onto the frozen protocol's
# ``temperature_by_call_role`` keys, so a caller can resolve token limit and temperature from
# one place and can never wire them independently.
SCHEMA_VERSION_V2 = "phase2_role_limits_v2"
ARTIFACT_ID_V2 = "phase2_role_limits_request_settings_2026-07-18_v2"

SUPERSEDES_V1_TRACKED_PATH = "rejudge/phase2_role_limits_2026-07-18.json"
SUPERSEDES_KEYS: frozenset[str] = frozenset({"tracked_path", "canonical_sha256"})

# The frozen mapping from each of the seven limits roles (FROZEN_BASE_ROLES, defined below)
# onto the frozen protocol's decisions.execution_semantics.temperature_by_call_role keys.
# judge_verdict and batch_verdict are the ONLY two limits roles that deliberately share a
# single protocol call-role target (batch verdicts are judged with the same verdict
# temperature as sequential verdicts); every other limits role maps to its own distinct target.
ROLE_TAXONOMY: dict[str, str] = {
    "debater_turn": "debater",
    "judge_query": "judge_query",
    "oracle": "oracle",
    "judge_verdict": "judge_verdict",
    "batch_verdict": "judge_verdict",
    "query_checker": "query_checker",
    "capability_qa": "capability_qa",
}
_ALLOWED_MANY_TO_ONE_ROLES: frozenset[str] = frozenset({"judge_verdict", "batch_verdict"})

TOP_LEVEL_KEYS_V2: frozenset[str] = frozenset({
    "schema_version", "artifact_id", "protocol_id", "status", "execution_authorized",
    "supersedes", "base_role_max_tokens", "reasoning_models", "model_role_limits",
    "context_ceilings", "request_settings", "role_taxonomy",
})

# --- v3: retry-pin reduction (self-contained forecast-resolution choice) plus a bound ----------
# --- delegation approval_basis, on top of the frozen v2 content --------------------------------
#
# v3 is additive over v2 exactly the way v2 was additive over v1: every v2-checked section is
# still validated with the EXACT SAME per-section helpers (including a re-parameterized
# ``_validate_request_settings`` so v1/v2/v3 can never silently diverge on anything except the
# one deliberate transport change). v3 changes exactly two things relative to v2: the transport
# retry pin (max_retries 3->2, max_attempts 4->3 -- the self-contained forecast-resolution
# choice recorded in the 2026-07-19 preflight delegation, chosen because the alternative would
# require the owner's own HuggingFace account action) and a new ``approval_basis`` block binding
# that same delegation record by path and RAW file sha256 (never canonical-JSON: the delegation
# is a governance record, hashed the same way every other approval-basis binding in this project
# is hashed). Its own ``supersedes`` block moves one link down the chain to bind v2 (by path and
# v2's own recomputed canonical hash) instead of v1.
SCHEMA_VERSION_V3 = "phase2_role_limits_v3"
ARTIFACT_ID_V3 = "phase2_role_limits_request_settings_2026-07-19_v3"

SUPERSEDES_V2_TRACKED_PATH = "rejudge/phase2_role_limits_v2_2026-07-18.json"

# The frozen preflight delegation record: bound into v3's approval_basis block, and also the
# pinned authorization approval_basis in phase2_execution.py (the same governance record is
# deliberately checked independently at both call sites).
APPROVAL_BASIS_V3_TRACKED_PATH = "rejudge/phase2_preflight_delegation_2026-07-19.json"
APPROVAL_BASIS_KEYS: frozenset[str] = frozenset({"tracked_path", "sha256"})

TRANSPORT_MAX_RETRIES_V3 = 2
TRANSPORT_MAX_ATTEMPTS_V3 = TRANSPORT_MAX_RETRIES_V3 + 1

TOP_LEVEL_KEYS_V3: frozenset[str] = TOP_LEVEL_KEYS_V2 | frozenset({"approval_basis"})

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


def _validate_request_settings(
    section_raw: Any, protocol: Mapping[str, Any], *,
    expected_max_retries: int = TRANSPORT_MAX_RETRIES,
    expected_max_attempts: int = TRANSPORT_MAX_ATTEMPTS,
) -> None:
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
    if max_retries != expected_max_retries:
        raise RoleLimitsError(
            f"request_settings.transport.max_retries must be pinned to {expected_max_retries}")
    if max_attempts != expected_max_attempts or max_attempts != max_retries + 1:
        raise RoleLimitsError(
            "request_settings.transport.max_attempts must be exactly max_retries + 1 "
            f"({expected_max_attempts})")

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


# --- v2 validation ------------------------------------------------------------------------------


def _sha256_hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise RoleLimitsError(f"{label} must be a SHA-256 hex digest")
    return value


def _validate_supersedes(section_raw: Any, v1_artifact: Mapping[str, Any]) -> None:
    section = _mapping(section_raw, "supersedes")
    _exact_keys(section, SUPERSEDES_KEYS, "supersedes")
    tracked_path = section.get("tracked_path")
    if tracked_path != SUPERSEDES_V1_TRACKED_PATH:
        raise RoleLimitsError(
            f"supersedes.tracked_path must be exactly {SUPERSEDES_V1_TRACKED_PATH!r}, "
            f"got {tracked_path!r}")
    declared_sha = _sha256_hex(section.get("canonical_sha256"), "supersedes.canonical_sha256")
    observed_sha = phase2_plan.canonical_sha256(dict(v1_artifact))
    if declared_sha != observed_sha:
        raise RoleLimitsError(
            "supersedes.canonical_sha256 disagrees with the real v1 artifact on disk: "
            f"v2 bound {declared_sha}, observed {observed_sha}")


def _validate_role_taxonomy(section_raw: Any, protocol: Mapping[str, Any]) -> None:
    section = _mapping(section_raw, "role_taxonomy")
    _exact_keys(section, FROZEN_BASE_ROLES, "role_taxonomy")

    execution_semantics = _mapping(
        protocol["decisions"]["execution_semantics"], "protocol execution_semantics")
    temperature_by_call_role = _mapping(
        execution_semantics.get("temperature_by_call_role"),
        "protocol execution_semantics.temperature_by_call_role")
    valid_targets = set(temperature_by_call_role)

    reverse: dict[str, list[str]] = {}
    for role in FROZEN_BASE_ROLES:
        target = section.get(role)
        if not isinstance(target, str) or not target:
            raise RoleLimitsError(f"role_taxonomy.{role} must be a non-empty string")
        if target not in valid_targets:
            raise RoleLimitsError(
                f"role_taxonomy.{role} target {target!r} is not a known protocol call role "
                "in temperature_by_call_role")
        reverse.setdefault(target, []).append(role)

    for target, roles in reverse.items():
        if len(roles) > 1 and set(roles) != _ALLOWED_MANY_TO_ONE_ROLES:
            raise RoleLimitsError(
                f"role_taxonomy illegal many-to-one mapping onto {target!r}: {sorted(roles)}; "
                "only judge_verdict and batch_verdict may share a protocol call-role target")

    if dict(section) != ROLE_TAXONOMY:
        raise RoleLimitsError("role_taxonomy disagrees with the frozen taxonomy mapping")


@dataclass(frozen=True, slots=True)
class ResolvedRequestParameters:
    """The single resolved output of :func:`resolve_request_parameters`.

    Binds the effective output-token limit and the request temperature for one (model,
    limits_role) pair into a single immutable value, so a call site can never wire the two
    independently: both always come from exactly one resolution call.
    """

    effective_max_tokens: int
    temperature: float
    protocol_role: str


def resolve_request_parameters(
    v2_artifact: Mapping[str, Any], protocol: Mapping[str, Any], model_id: str, limits_role: str,
) -> ResolvedRequestParameters:
    """Resolve the effective max_tokens and temperature for one (model, limits_role) pair.

    Fails closed (:class:`RoleLimitsError`) on an unknown model, an unknown limits role, or a
    (model, limits_role) pair that is not among the frozen APPLICABLE pairs -- never silently
    defaults or infers.
    """
    model_role_limits = _mapping(
        v2_artifact.get("model_role_limits"), "v2 artifact model_role_limits")
    role_taxonomy = _mapping(v2_artifact.get("role_taxonomy"), "v2 artifact role_taxonomy")

    if limits_role not in role_taxonomy:
        raise RoleLimitsError(f"unknown limits_role: {limits_role!r}")
    if model_id not in model_role_limits:
        raise RoleLimitsError(f"unknown model_id: {model_id!r}")
    model_entry = _mapping(model_role_limits[model_id], f"model_role_limits.{model_id}")
    if limits_role not in model_entry:
        raise RoleLimitsError(
            f"(model_id={model_id!r}, limits_role={limits_role!r}) is not an applicable "
            "(model, role) pair in the frozen role-limits artifact")

    role_entry = _mapping(
        model_entry[limits_role], f"model_role_limits.{model_id}.{limits_role}")
    effective_max_tokens = _int(
        role_entry.get("effective_request_max_tokens"),
        f"model_role_limits.{model_id}.{limits_role}.effective_request_max_tokens")

    protocol_role = role_taxonomy[limits_role]
    execution_semantics = _mapping(
        protocol["decisions"]["execution_semantics"], "protocol execution_semantics")
    temperature_by_call_role = _mapping(
        execution_semantics.get("temperature_by_call_role"),
        "protocol execution_semantics.temperature_by_call_role")
    if protocol_role not in temperature_by_call_role:
        raise RoleLimitsError(
            f"role_taxonomy target {protocol_role!r} is not a known protocol call role")
    temperature = temperature_by_call_role[protocol_role]
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise RoleLimitsError(
            f"protocol temperature_by_call_role.{protocol_role} must be a number")

    return ResolvedRequestParameters(
        effective_max_tokens=effective_max_tokens,
        temperature=float(temperature),
        protocol_role=protocol_role,
    )


def validate_role_limits_v2(
    artifact: Mapping[str, Any],
    protocol: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    v1_artifact: Mapping[str, Any],
) -> None:
    """Validate the v2 role-limits/request-settings artifact, fail-closed throughout.

    Checks everything :func:`validate_role_limits` checks on v1 (reusing its exact same
    per-section helpers so v1 and v2 can never silently diverge on the frozen limits), plus:
    the ``supersedes`` block (exact v1 tracked path, and v1's canonical hash recomputed fresh
    from ``v1_artifact`` rather than trusted from the v2 artifact), the ``role_taxonomy``
    section, and that every applicable (model, role) pair in ``model_role_limits`` resolves
    cleanly through :func:`resolve_request_parameters`.
    """
    artifact = _mapping(artifact, "v2 artifact")
    protocol = _mapping(protocol, "protocol")
    snapshot = _mapping(snapshot, "snapshot")
    v1_artifact = _mapping(v1_artifact, "v1 artifact")
    _exact_keys(artifact, TOP_LEVEL_KEYS_V2, "v2 artifact")

    if artifact.get("schema_version") != SCHEMA_VERSION_V2:
        raise RoleLimitsError("unsupported v2 role-limits schema_version")
    if artifact.get("artifact_id") != ARTIFACT_ID_V2:
        raise RoleLimitsError("v2 role-limits artifact_id drifted")
    protocol_id = _non_empty_str(protocol.get("protocol_id"), "protocol protocol_id")
    if artifact.get("protocol_id") != protocol_id:
        raise RoleLimitsError("v2 role-limits protocol_id disagrees with the frozen protocol")
    if artifact.get("status") != STATUS:
        raise RoleLimitsError("v2 role-limits status drifted")
    if artifact.get("execution_authorized") is not False:
        raise RoleLimitsError("execution_authorized must be exactly false")

    _validate_supersedes(artifact.get("supersedes"), v1_artifact)

    _validate_base_role_max_tokens(artifact.get("base_role_max_tokens"))
    _validate_reasoning_models(artifact.get("reasoning_models"))
    _validate_request_settings(artifact.get("request_settings"), protocol)
    _validate_model_role_limits(artifact.get("model_role_limits"), protocol)
    _validate_context_ceilings(artifact.get("context_ceilings"), snapshot)
    _validate_role_taxonomy(artifact.get("role_taxonomy"), protocol)

    model_role_limits = _mapping(artifact["model_role_limits"], "model_role_limits")
    for model_id, roles in model_role_limits.items():
        for role in _mapping(roles, f"model_role_limits.{model_id}"):
            resolve_request_parameters(artifact, protocol, model_id, role)


def load_and_validate_v2(
    artifact_path: str | Path = DEFAULT_V2_ARTIFACT_PATH,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    v1_artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol = phase2_plan.load_protocol(protocol_path)
    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(snapshot_path, protocol_path)
    artifact = _load_json(artifact_path)
    v1_artifact = _load_json(v1_artifact_path)
    validate_role_limits_v2(artifact, protocol, snapshot, v1_artifact)
    return artifact, protocol, snapshot


# --- v3 validation -------------------------------------------------------------------------------


def _validate_approval_basis(section_raw: Any, root: Path) -> None:
    """Validate v3's bound preflight-delegation approval_basis, fail-closed throughout.

    Deliberately RAW file hashing (the delegation record is JSON, but is hashed the same way
    every other approval-basis binding in this project is hashed -- see
    ``phase2_execution.py``'s authorization ``approval_basis_sha256``), never canonical-JSON.
    """
    section = _mapping(section_raw, "approval_basis")
    _exact_keys(section, APPROVAL_BASIS_KEYS, "approval_basis")
    tracked_path = section.get("tracked_path")
    if tracked_path != APPROVAL_BASIS_V3_TRACKED_PATH:
        raise RoleLimitsError(
            f"approval_basis.tracked_path must be exactly {APPROVAL_BASIS_V3_TRACKED_PATH!r}, "
            f"got {tracked_path!r}")
    declared_sha = _sha256_hex(section.get("sha256"), "approval_basis.sha256")
    basis_path = root / tracked_path
    try:
        raw = basis_path.read_bytes()
    except OSError as exc:
        raise RoleLimitsError(f"approval_basis artifact is missing: {basis_path}: {exc}") from exc
    observed_sha = hashlib.sha256(raw).hexdigest()
    if observed_sha != declared_sha:
        raise RoleLimitsError(
            "approval_basis.sha256 disagrees with the real delegation record on disk: "
            f"v3 bound {declared_sha}, observed {observed_sha}")


def validate_role_limits_v3(
    artifact: Mapping[str, Any],
    protocol: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    v2_artifact: Mapping[str, Any],
    *,
    project_root: str | Path = DEFAULT_PROJECT_ROOT,
) -> None:
    """Validate the v3 role-limits/request-settings artifact, fail-closed throughout.

    Checks everything :func:`validate_role_limits_v2` checks (reusing its exact same
    per-section helpers, so v1/v2/v3 can never silently diverge on the frozen limits) except
    that ``request_settings.transport`` is required to be the v3 retry-pin reduction
    (max_retries=2, max_attempts=3) instead of v2's (max_retries=3, max_attempts=4); plus the
    ``supersedes`` block (exact v2 tracked path, and v2's canonical hash recomputed fresh from
    ``v2_artifact`` rather than trusted from the v3 artifact alone) and the new
    ``approval_basis`` block (the frozen preflight delegation record, bound by path and raw
    file sha256, recomputed fresh from disk under ``project_root``).
    """
    artifact = _mapping(artifact, "v3 artifact")
    protocol = _mapping(protocol, "protocol")
    snapshot = _mapping(snapshot, "snapshot")
    v2_artifact = _mapping(v2_artifact, "v2 artifact")
    root = Path(project_root)
    _exact_keys(artifact, TOP_LEVEL_KEYS_V3, "v3 artifact")

    if artifact.get("schema_version") != SCHEMA_VERSION_V3:
        raise RoleLimitsError("unsupported v3 role-limits schema_version")
    if artifact.get("artifact_id") != ARTIFACT_ID_V3:
        raise RoleLimitsError("v3 role-limits artifact_id drifted")
    protocol_id = _non_empty_str(protocol.get("protocol_id"), "protocol protocol_id")
    if artifact.get("protocol_id") != protocol_id:
        raise RoleLimitsError("v3 role-limits protocol_id disagrees with the frozen protocol")
    if artifact.get("status") != STATUS:
        raise RoleLimitsError("v3 role-limits status drifted")
    if artifact.get("execution_authorized") is not False:
        raise RoleLimitsError("execution_authorized must be exactly false")

    supersedes = _mapping(artifact.get("supersedes"), "supersedes")
    _exact_keys(supersedes, SUPERSEDES_KEYS, "supersedes")
    tracked_path = supersedes.get("tracked_path")
    if tracked_path != SUPERSEDES_V2_TRACKED_PATH:
        raise RoleLimitsError(
            f"supersedes.tracked_path must be exactly {SUPERSEDES_V2_TRACKED_PATH!r}, "
            f"got {tracked_path!r}")
    declared_supersedes_sha = _sha256_hex(
        supersedes.get("canonical_sha256"), "supersedes.canonical_sha256")
    observed_v2_sha = phase2_plan.canonical_sha256(dict(v2_artifact))
    if declared_supersedes_sha != observed_v2_sha:
        raise RoleLimitsError(
            "supersedes.canonical_sha256 disagrees with the real v2 artifact on disk: "
            f"v3 bound {declared_supersedes_sha}, observed {observed_v2_sha}")

    _validate_approval_basis(artifact.get("approval_basis"), root)

    _validate_base_role_max_tokens(artifact.get("base_role_max_tokens"))
    _validate_reasoning_models(artifact.get("reasoning_models"))
    _validate_request_settings(
        artifact.get("request_settings"), protocol,
        expected_max_retries=TRANSPORT_MAX_RETRIES_V3,
        expected_max_attempts=TRANSPORT_MAX_ATTEMPTS_V3,
    )
    _validate_model_role_limits(artifact.get("model_role_limits"), protocol)
    _validate_context_ceilings(artifact.get("context_ceilings"), snapshot)
    _validate_role_taxonomy(artifact.get("role_taxonomy"), protocol)

    model_role_limits = _mapping(artifact["model_role_limits"], "model_role_limits")
    for model_id, roles in model_role_limits.items():
        for role in _mapping(roles, f"model_role_limits.{model_id}"):
            resolve_request_parameters(artifact, protocol, model_id, role)


def load_and_validate_v3(
    artifact_path: str | Path = DEFAULT_V3_ARTIFACT_PATH,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    v2_artifact_path: str | Path = DEFAULT_V2_ARTIFACT_PATH,
    project_root: str | Path = DEFAULT_PROJECT_ROOT,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol = phase2_plan.load_protocol(protocol_path)
    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(snapshot_path, protocol_path)
    artifact = _load_json(artifact_path)
    v2_artifact = _load_json(v2_artifact_path)
    validate_role_limits_v3(artifact, protocol, snapshot, v2_artifact, project_root=project_root)
    return artifact, protocol, snapshot


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--v2", action="store_true")
    parser.add_argument("--v3", action="store_true")
    parser.add_argument("--artifact", default=None)
    parser.add_argument("--v1-artifact", default=str(DEFAULT_ARTIFACT_PATH))
    parser.add_argument("--v2-artifact", default=str(DEFAULT_V2_ARTIFACT_PATH))
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL_PATH))
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT_PATH))
    parser.add_argument("--project-root", default=str(DEFAULT_PROJECT_ROOT))
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("only --check is supported")
    if args.v3:
        artifact_path = args.artifact if args.artifact is not None else str(DEFAULT_V3_ARTIFACT_PATH)
        artifact, _protocol, _snapshot = load_and_validate_v3(
            artifact_path, args.protocol, args.snapshot, args.v2_artifact, args.project_root)
        print(
            "verified frozen Phase 2 role-limits v3 artifact; "
            f"models={len(artifact['model_role_limits'])}; "
            f"canonical_sha256={phase2_plan.canonical_sha256(artifact)}; "
            "execution_authorized=NO"
        )
        return 0
    if args.v2:
        artifact_path = args.artifact if args.artifact is not None else str(DEFAULT_V2_ARTIFACT_PATH)
        artifact, _protocol, _snapshot = load_and_validate_v2(
            artifact_path, args.protocol, args.snapshot, args.v1_artifact)
        print(
            "verified frozen Phase 2 role-limits v2 artifact; "
            f"models={len(artifact['model_role_limits'])}; "
            f"canonical_sha256={phase2_plan.canonical_sha256(artifact)}; "
            "execution_authorized=NO"
        )
        return 0
    artifact_path = args.artifact if args.artifact is not None else str(DEFAULT_ARTIFACT_PATH)
    artifact, _protocol, _snapshot = load_and_validate(artifact_path, args.protocol, args.snapshot)
    print(
        "verified frozen Phase 2 role-limits/request-settings artifact; "
        f"models={len(artifact['model_role_limits'])}; "
        f"canonical_sha256={phase2_plan.canonical_sha256(artifact)}; "
        "execution_authorized=NO"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
