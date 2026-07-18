"""Validate the frozen Phase 2 capability-preflight token/cost forecast.

This artifact PUBLICLY REPLACES the provisional manual dollar band in
``rejudge/phase2_cost_model.py`` for the capability-preflight stage only. Unlike that manual
band, every dollar figure here is derived from actually-counted tokens over the real,
hash-bound rendered corpus (``rejudge.phase2_capability_corpus``), pinned tokenizer
revisions, the frozen role-limits/request-settings artifact (v2), and the frozen public price
snapshot -- never a hand-picked estimate.

Token counting itself requires downloading pinned Hugging Face tokenizers (see
``scripts/build_phase2_preflight_forecast.py``), which this module deliberately does NOT do:
this module is the offline, network-free, fail-closed validator, matching the sibling-artifact
convention used throughout ``rejudge/phase2_*.py``. It recomputes every bound hash from
in-repo sources, recomputes every per-model token statistic from the recorded per-prompt token
counts, and recomputes every scenario dollar figure with ``decimal.Decimal`` from the recorded
inputs -- it never trusts a recorded number it can independently rederive.

CURRENT STATUS (2026-07-18): at the frozen prices, roster, corpus, and the role-limits-v2
reasoning-model output floor, the honestly-computed ``four_attempt_stress`` scenario does NOT
clear the frozen ``halt_cap_usd`` (15) -- see ``rejudge/phase2_preflight_forecast_conflict_2026-07-18.json``
and ``validate_conflict_report`` below. This is a genuine conflict between frozen inputs, not a
bug in this module; ``scripts/build_phase2_preflight_forecast.py`` refuses to write the
canonical "ready" artifact (``validate_forecast`` / ``rejudge/phase2_preflight_forecast_2026-07-18.json``)
until an owner resolves it (see the conflict artifact's ``resolution.options``).

Like its sibling Phase 2 artifacts, this module cannot establish or claim execution
authority: ``execution_authorized`` is always exactly ``false``.
"""
from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from rejudge import phase2_capability_corpus as capability_corpus
from rejudge import phase2_plan
from rejudge import phase2_prompt_bundle
from rejudge import phase2_provider_price_snapshot as price_snapshot
from rejudge import phase2_role_limits


DEFAULT_ARTIFACT_PATH = Path(__file__).with_name("phase2_preflight_forecast_2026-07-18.json")
DEFAULT_CONFLICT_ARTIFACT_PATH = Path(__file__).with_name(
    "phase2_preflight_forecast_conflict_2026-07-18.json")
DEFAULT_PROTOCOL_PATH = phase2_plan.DEFAULT_PROTOCOL_PATH
DEFAULT_ROLE_LIMITS_V2_PATH = phase2_role_limits.DEFAULT_V2_ARTIFACT_PATH
DEFAULT_ROLE_LIMITS_V1_PATH = phase2_role_limits.DEFAULT_ARTIFACT_PATH
DEFAULT_SNAPSHOT_PATH = price_snapshot.DEFAULT_SNAPSHOT_PATH
DEFAULT_BUNDLE_PATH = phase2_prompt_bundle.DEFAULT_BUNDLE_PATH

SCHEMA_VERSION = "phase2_preflight_forecast_v1"
ARTIFACT_ID = "phase2_preflight_forecast_2026-07-18"
STATUS = "frozen_pending_execution_authorization"

# --- the diagnostic conflict-report shape (see the module docstring above "top-level
# --- validation"): a distinctly-named, distinctly-schemaed sibling artifact for exactly the
# --- case where the honestly-computed four_attempt_stress does NOT clear halt_cap_usd. ---------
CONFLICT_SCHEMA_VERSION = "phase2_preflight_forecast_conflict_v1"
CONFLICT_ARTIFACT_ID = "phase2_preflight_forecast_conflict_2026-07-18"
CONFLICT_STATUS = "blocked_stress_exceeds_halt_cap_pending_owner_resolution"
CAPABILITY_LIMITS_ROLE = "capability_qa"

# --- frozen roster (order-independent set; corpus/role-limits/price-snapshot all cross-check
# --- this against the live protocol so a roster amendment fails closed here too) -------------
MODEL_IDS: frozenset[str] = frozenset({
    "Qwen/Qwen2.5-7B-Instruct-Turbo",
    "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "openai/gpt-oss-120b",
    "Qwen/Qwen3.7-Plus",
})

EXACT_TOKENIZER_MODEL_IDS: frozenset[str] = frozenset({
    "Qwen/Qwen2.5-7B-Instruct-Turbo", "google/gemma-4-31B-it", "openai/gpt-oss-120b",
})
PROXY_TOKENIZER_MODEL_IDS: frozenset[str] = frozenset({"Qwen/Qwen3.7-Plus"})
BYTE_BOUND_ONLY_MODEL_IDS: frozenset[str] = frozenset({"meta-llama/Llama-3.3-70B-Instruct-Turbo"})

CLASSIFICATION_EXACT = "exact_model_tokenizer_estimate"
CLASSIFICATION_PROXY = "proxy_tokenizer_estimate"
CLASSIFICATION_BYTE_BOUND = "utf8_byte_reservation_bound"
CLASSIFICATIONS: frozenset[str] = frozenset(
    {CLASSIFICATION_EXACT, CLASSIFICATION_PROXY, CLASSIFICATION_BYTE_BOUND})
EXPECTED_CLASSIFICATION_BY_MODEL: dict[str, str] = {
    **{model_id: CLASSIFICATION_EXACT for model_id in EXACT_TOKENIZER_MODEL_IDS},
    **{model_id: CLASSIFICATION_PROXY for model_id in PROXY_TOKENIZER_MODEL_IDS},
    **{model_id: CLASSIFICATION_BYTE_BOUND for model_id in BYTE_BOUND_ONLY_MODEL_IDS},
}

# --- scenario constants -------------------------------------------------------------------------
THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL = 10
PLANNING_RETRY_MULTIPLIER = Decimal("1.05")
FOUR_ATTEMPT_STRESS_MULTIPLIER = Decimal("4")
MILLION = Decimal(1_000_000)
MEAN_QUANTIZE = Decimal("0.000001")

# --- byte-reservation-bound convention, mirroring (never importing, since it is a private
# --- symbol) rejudge/api_client.py's _estimate_usage: a fixed 64-token base allowance plus,
# --- per message, a fixed 32-token framing allowance plus the UTF-8 byte length of its role
# --- and content. This over-reserves relative to a token count on purpose -- it is a safety
# --- bound, never the expected count (see CLASSIFICATION_BYTE_BOUND's use, which is the sole
# --- deliberate exception: for a model with no reachable tokenizer, this bound is promoted to
# --- fill the missing expected-count slot, and that promotion is recorded, never silent).
BYTE_BOUND_BASE_TOKENS = 64
BYTE_BOUND_PER_MESSAGE_TOKENS = 32
BYTE_BOUND_FORMULA_NOTE = (
    "64 + sum over messages of (32 + utf8_byte_len(role) + utf8_byte_len(content)); mirrors "
    "rejudge/api_client.py's _estimate_usage prompt-bound convention (source function is "
    "private, so the formula is reproduced here rather than imported)."
)


class PreflightForecastError(ValueError):
    """The frozen capability-preflight forecast artifact is malformed or internally inconsistent."""


# --- generic JSON / type helpers, matching the sibling-validator convention --------------------


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PreflightForecastError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PreflightForecastError(f"{label} must be an array")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: Sequence[str] | set[str] | frozenset[str], label: str,
) -> None:
    if set(value) != set(expected):
        raise PreflightForecastError(
            f"{label} fields drifted: observed={sorted(value)}, expected={sorted(expected)}")


def _non_empty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PreflightForecastError(f"{label} must be a non-empty string")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise PreflightForecastError(f"{label} must be a boolean")
    return value


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PreflightForecastError(f"{label} must be an integer")
    return value


def _non_negative_int(value: Any, label: str) -> int:
    parsed = _int(value, label)
    if parsed < 0:
        raise PreflightForecastError(f"{label} must be a non-negative integer")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    parsed = _int(value, label)
    if parsed <= 0:
        raise PreflightForecastError(f"{label} must be a positive integer")
    return parsed


def _sha256_hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise PreflightForecastError(f"{label} must be a SHA-256 hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise PreflightForecastError(f"{label} must be a hex string") from exc
    return value


def _decimal_string(value: Any, label: str) -> Decimal:
    """Parse a JSON string as an exact ``Decimal``, fail-closed on any non-string/malformed value."""
    if not isinstance(value, str) or not value:
        raise PreflightForecastError(f"{label} must be a non-empty Decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PreflightForecastError(f"{label} is not a valid Decimal string: {value!r}") from exc
    if not parsed.is_finite():
        raise PreflightForecastError(f"{label} must be finite")
    return parsed


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreflightForecastError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(token: str) -> Any:
    raise PreflightForecastError(f"JSON must not contain the non-finite literal: {token}")


def _load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PreflightForecastError(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PreflightForecastError(f"{path} must contain a JSON object")
    return payload


# --- pure computation helpers, shared verbatim between the builder script and this validator ---


def byte_reservation_bound(system_prompt: str, user_prompt: str) -> int:
    """The UTF-8-byte input-token reservation bound for one rendered (system, user) prompt pair.

    See ``BYTE_BOUND_FORMULA_NOTE``. Deliberately a loose over-reservation; a safety figure,
    never used as an expected token count except for the one documented fallback model.
    """
    bound = BYTE_BOUND_BASE_TOKENS
    for role, content in (("system", system_prompt), ("user", user_prompt)):
        bound += BYTE_BOUND_PER_MESSAGE_TOKENS
        bound += len(role.encode("utf-8"))
        bound += len(content.encode("utf-8"))
    return bound


def percentile(sorted_values: Sequence[int], fraction: Decimal) -> int:
    """Nearest-rank percentile: index = ceil(fraction * n) - 1 (0-indexed), clamped to [0, n-1].

    ``sorted_values`` must already be ascending. Deterministic and dependency-free.
    """
    n = len(sorted_values)
    if n == 0:
        raise PreflightForecastError("percentile of an empty sequence is undefined")
    raw_index = math.ceil(fraction * n) - 1
    index = min(max(raw_index, 0), n - 1)
    return sorted_values[index]


def compute_token_stats(per_prompt: Sequence[int]) -> dict[str, Any]:
    """Compute {total, mean, max, p50, p95, per_prompt} from a list of per-prompt token counts."""
    if not per_prompt or any(isinstance(v, bool) or not isinstance(v, int) or v < 0
                              for v in per_prompt):
        raise PreflightForecastError("per_prompt token counts must be a non-empty list of ints")
    ordered = sorted(per_prompt)
    total = sum(ordered)
    mean = (Decimal(total) / Decimal(len(ordered))).quantize(
        MEAN_QUANTIZE, rounding=ROUND_HALF_EVEN)
    return {
        "total": total,
        "mean": str(mean),
        "max": ordered[-1],
        "p50": percentile(ordered, Decimal("0.50")),
        "p95": percentile(ordered, Decimal("0.95")),
        "per_prompt": list(per_prompt),
    }


def _validate_per_prompt_list(value: Any, label: str) -> list[int]:
    raw = _list(value, label)
    if len(raw) != capability_corpus.EXPECTED_ENTRY_COUNT:
        raise PreflightForecastError(
            f"{label} must have exactly {capability_corpus.EXPECTED_ENTRY_COUNT} entries")
    return [_non_negative_int(v, f"{label}[{i}]") for i, v in enumerate(raw)]


def _validate_token_stats(section_raw: Any, label: str) -> dict[str, Any]:
    """Validate a ``{total, mean, max, p50, p95, per_prompt}`` block, returning the recomputed
    (and, by this point, confirmed byte-for-byte equal) stats dict for reuse by the caller."""
    section = _mapping(section_raw, label)
    _exact_keys(section, {"total", "mean", "max", "p50", "p95", "per_prompt"}, label)
    per_prompt = _validate_per_prompt_list(section.get("per_prompt"), f"{label}.per_prompt")
    expected = compute_token_stats(per_prompt)
    for field in ("total", "mean", "max", "p50", "p95"):
        if section.get(field) != expected[field]:
            raise PreflightForecastError(
                f"{label}.{field} disagrees with the recomputed value: "
                f"observed {section.get(field)!r}, expected {expected[field]!r}")
    return expected


def cost_component(tokens: int, price_per_million: Decimal) -> Decimal:
    return (Decimal(tokens) * price_per_million) / MILLION


def compute_scenario_component(
    *, total_input_tokens: int, calls: int, output_tokens_per_call: int,
    input_price: Decimal, output_price: Decimal,
) -> dict[str, Any]:
    output_tokens_total = calls * output_tokens_per_call
    input_cost = cost_component(total_input_tokens, input_price)
    output_cost = cost_component(output_tokens_total, output_price)
    total = input_cost + output_cost
    return {
        "input_tokens": total_input_tokens,
        "output_tokens_total": output_tokens_total,
        "input_cost_usd": str(input_cost),
        "output_cost_usd": str(output_cost),
        "total_usd": str(total),
    }


def _validate_scenario_component(
    section_raw: Any, label: str, *, total_input_tokens: int, calls: int,
    output_tokens_per_call: int, input_price: Decimal, output_price: Decimal,
) -> Decimal:
    section = _mapping(section_raw, label)
    _exact_keys(
        section,
        {"input_tokens", "output_tokens_total", "input_cost_usd", "output_cost_usd", "total_usd"},
        label,
    )
    expected = compute_scenario_component(
        total_input_tokens=total_input_tokens, calls=calls,
        output_tokens_per_call=output_tokens_per_call,
        input_price=input_price, output_price=output_price,
    )
    if section.get("input_tokens") != expected["input_tokens"]:
        raise PreflightForecastError(f"{label}.input_tokens disagrees with the recomputed value")
    if section.get("output_tokens_total") != expected["output_tokens_total"]:
        raise PreflightForecastError(
            f"{label}.output_tokens_total disagrees with the recomputed value")
    for field in ("input_cost_usd", "output_cost_usd", "total_usd"):
        observed = _decimal_string(section.get(field), f"{label}.{field}")
        if observed != Decimal(expected[field]):
            raise PreflightForecastError(
                f"{label}.{field} disagrees with the recomputed value: "
                f"observed {observed}, expected {expected[field]}")
    return Decimal(expected["total_usd"])


# --- top-level key sets --------------------------------------------------------------------------

TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "schema_version", "artifact_id", "protocol_id", "status", "execution_authorized",
    "generated_at_utc", "dependency_versions", "bindings", "corpus", "tokenizer_pins",
    "per_model_token_stats", "corpus_utf8_byte_reservation_bound_per_prompt",
    "output_token_policy", "retry_policy", "replace_with_actuals_requirement",
    "scenarios", "halt_cap_usd", "stress_margin_usd", "caveats",
})
CONFLICT_TOP_LEVEL_KEYS: frozenset[str] = TOP_LEVEL_KEYS | frozenset({"resolution"})
RESOLUTION_KEYS: frozenset[str] = frozenset(
    {"required", "stress_below_halt_cap", "blocking_scenario", "options"})
DEPENDENCY_VERSIONS_KEYS: frozenset[str] = frozenset({"transformers", "tokenizers", "jinja2"})
BINDINGS_KEYS: frozenset[str] = frozenset(
    {"protocol", "role_limits_v2", "price_snapshot", "prompt_bundle", "rendered_corpus"})
ARTIFACT_BINDING_KEYS: frozenset[str] = frozenset({"tracked_path", "canonical_sha256"})
RENDERED_CORPUS_BINDING_KEYS: frozenset[str] = frozenset({"canonical_sha256", "entry_count"})
CORPUS_KEYS: frozenset[str] = frozenset({
    "question_count", "mirrored_replicates", "total_rendered_message_sets",
    "question_sources", "world_spec_sources", "template_name", "rendering_note",
})
OUTPUT_TOKEN_POLICY_KEYS: frozenset[str] = frozenset({
    "theoretical_minimum_output_tokens_per_call", "effective_output_ceiling_per_model", "source",
})
RETRY_POLICY_KEYS: frozenset[str] = frozenset({"max_retries", "max_attempts", "source"})
SCENARIO_NAMES: tuple[str, ...] = (
    "theoretical_minimum", "no_retry_maximum", "planning_retry_scenario", "four_attempt_stress",
)
CEILING_SCENARIO_KEYS: frozenset[str] = frozenset(
    {"formula", "calls_per_model", "output_tokens_per_call_by_model", "per_model", "total_usd"})
DERIVED_SCENARIO_KEYS: frozenset[str] = frozenset(
    {"formula", "multiplier", "per_model", "total_usd"})
FOUR_ATTEMPT_EXTRA_KEYS: frozenset[str] = frozenset(
    {"qwen_3_7_plus_byte_bound_stress_usd", "qwen_3_7_plus_byte_bound_note"})
DERIVED_PER_MODEL_KEYS: frozenset[str] = frozenset({"total_usd"})
EXACT_TOKENIZER_PIN_KEYS: frozenset[str] = frozenset({
    "classification", "tokenizer_repository", "revision", "tokenizer_file_hashes",
    "chat_template_sha256", "generation_prompt_included", "counting_method",
    "fallback_used", "notes",
})
PROXY_TOKENIZER_PIN_KEYS: frozenset[str] = frozenset({
    "classification", "proxies", "convergence", "pricing_rule", "generation_prompt_included",
    "counting_method", "fallback_used", "notes",
})
PROXY_ENTRY_KEYS: frozenset[str] = frozenset({
    "repository", "revision", "tokenizer_file_hashes", "chat_template_sha256",
    "generation_prompt_included", "counting_method", "per_prompt",
})
CONVERGENCE_KEYS: frozenset[str] = frozenset(
    {"all_212_identical", "identical_count", "divergent_count"})
BYTE_BOUND_PIN_KEYS: frozenset[str] = frozenset(
    {"classification", "attempted_tokenizers", "fallback_used", "notes"})
ATTEMPTED_TOKENIZER_KEYS: frozenset[str] = frozenset({"repository", "revision", "outcome", "error"})
PER_MODEL_TOKEN_STATS_KEYS: frozenset[str] = frozenset(
    {"classification", "input_tokens", "utf8_byte_reservation_bound"})
CAVEAT_KEYS: frozenset[str] = frozenset({"id", "text"})
REQUIRED_CAVEAT_IDS: frozenset[str] = frozenset({
    "provider_catalog_observation_disagreement", "hosted_model_tokenizer_proxy_status",
    "reasoning_token_wildcard",
})


# --- section validators ---------------------------------------------------------------------


EXPECTED_BINDING_PATHS: dict[str, str] = {
    "protocol": "rejudge/phase2_protocol.json",
    "role_limits_v2": "rejudge/phase2_role_limits_v2_2026-07-18.json",
    "price_snapshot": "rejudge/phase2_provider_price_snapshot_2026-07-18.json",
    "prompt_bundle": "rejudge/phase2_prompt_bundle.json",
}


def _validate_artifact_binding(
    section_raw: Any, label: str, root: Path, *, expected_tracked_path: str,
) -> str:
    section = _mapping(section_raw, label)
    _exact_keys(section, ARTIFACT_BINDING_KEYS, label)
    tracked_path = _non_empty_str(section.get("tracked_path"), f"{label}.tracked_path")
    if tracked_path != expected_tracked_path:
        raise PreflightForecastError(
            f"{label}.tracked_path must be exactly {expected_tracked_path!r}, "
            f"got {tracked_path!r}")
    declared_sha = _sha256_hex(section.get("canonical_sha256"), f"{label}.canonical_sha256")
    payload = json.loads((root / tracked_path).read_text(encoding="utf-8"))
    observed_sha = phase2_plan.canonical_sha256(payload)
    if declared_sha != observed_sha:
        raise PreflightForecastError(
            f"{label}.canonical_sha256 disagrees with {tracked_path} on disk: "
            f"bound {declared_sha}, observed {observed_sha}")
    return tracked_path


def _validate_bindings(
    section_raw: Any, root: Path, protocol: Mapping[str, Any],
    role_limits_v2: Mapping[str, Any], snapshot: Mapping[str, Any], bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    section = _mapping(section_raw, "bindings")
    _exact_keys(section, BINDINGS_KEYS, "bindings")

    for key, label in (
        ("protocol", "bindings.protocol"), ("role_limits_v2", "bindings.role_limits_v2"),
        ("price_snapshot", "bindings.price_snapshot"), ("prompt_bundle", "bindings.prompt_bundle"),
    ):
        _validate_artifact_binding(
            section.get(key), label, root, expected_tracked_path=EXPECTED_BINDING_PATHS[key])

    if _sha256_hex(
        section["protocol"]["canonical_sha256"], "bindings.protocol.canonical_sha256"
    ) != phase2_plan.canonical_sha256(protocol):
        raise PreflightForecastError("bindings.protocol disagrees with the loaded protocol")
    if _sha256_hex(
        section["role_limits_v2"]["canonical_sha256"], "bindings.role_limits_v2.canonical_sha256"
    ) != phase2_plan.canonical_sha256(role_limits_v2):
        raise PreflightForecastError(
            "bindings.role_limits_v2 disagrees with the loaded role-limits v2 artifact")
    if _sha256_hex(
        section["price_snapshot"]["canonical_sha256"], "bindings.price_snapshot.canonical_sha256"
    ) != phase2_plan.canonical_sha256(snapshot):
        raise PreflightForecastError(
            "bindings.price_snapshot disagrees with the loaded price snapshot")
    if _sha256_hex(
        section["prompt_bundle"]["canonical_sha256"], "bindings.prompt_bundle.canonical_sha256"
    ) != phase2_plan.canonical_sha256(bundle):
        raise PreflightForecastError("bindings.prompt_bundle disagrees with the loaded bundle")

    rendered_corpus = _mapping(section.get("rendered_corpus"), "bindings.rendered_corpus")
    _exact_keys(rendered_corpus, RENDERED_CORPUS_BINDING_KEYS, "bindings.rendered_corpus")
    declared_corpus_sha = _sha256_hex(
        rendered_corpus.get("canonical_sha256"), "bindings.rendered_corpus.canonical_sha256")
    declared_entry_count = _positive_int(
        rendered_corpus.get("entry_count"), "bindings.rendered_corpus.entry_count")
    if declared_entry_count != capability_corpus.EXPECTED_ENTRY_COUNT:
        raise PreflightForecastError(
            "bindings.rendered_corpus.entry_count disagrees with the frozen corpus size")

    entries = capability_corpus.render_capability_corpus(bundle, protocol, root)
    observed_corpus_sha = capability_corpus.corpus_canonical_sha256(entries)
    if declared_corpus_sha != observed_corpus_sha:
        raise PreflightForecastError(
            "bindings.rendered_corpus.canonical_sha256 disagrees with the corpus freshly "
            f"rendered from tracked sources: bound {declared_corpus_sha}, "
            f"observed {observed_corpus_sha}")
    return entries


def _validate_corpus_section(
    section_raw: Any, protocol: Mapping[str, Any],
) -> None:
    section = _mapping(section_raw, "corpus")
    _exact_keys(section, CORPUS_KEYS, "corpus")
    if _positive_int(section.get("question_count"), "corpus.question_count") != (
            capability_corpus.EXPECTED_QUESTION_COUNT):
        raise PreflightForecastError("corpus.question_count disagrees with the frozen total")
    if _positive_int(section.get("mirrored_replicates"), "corpus.mirrored_replicates") != (
            capability_corpus.EXPECTED_REPLICATE_COUNT):
        raise PreflightForecastError("corpus.mirrored_replicates must be exactly K=2")
    if _positive_int(
        section.get("total_rendered_message_sets"), "corpus.total_rendered_message_sets"
    ) != capability_corpus.EXPECTED_ENTRY_COUNT:
        raise PreflightForecastError("corpus.total_rendered_message_sets disagrees with 106 x K2")

    question_set = _mapping(protocol.get("question_set"), "protocol question_set")
    expected_sources = list(question_set.get("question_sources") or [])
    if _list(section.get("question_sources"), "corpus.question_sources") != expected_sources:
        raise PreflightForecastError("corpus.question_sources disagrees with the frozen protocol")

    world_sources = _list(section.get("world_spec_sources"), "corpus.world_spec_sources")
    if not world_sources or any(not isinstance(p, str) or not p for p in world_sources):
        raise PreflightForecastError("corpus.world_spec_sources must be non-empty path strings")

    if section.get("template_name") != capability_corpus.CAPABILITY_TEMPLATE_NAME:
        raise PreflightForecastError("corpus.template_name must be 'capability_qa'")
    _non_empty_str(section.get("rendering_note"), "corpus.rendering_note")


def _validate_tokenizer_file_hashes(value: Any, label: str) -> None:
    mapping = _mapping(value, label)
    if not mapping:
        raise PreflightForecastError(f"{label} must be a non-empty object")
    for filename, sha in mapping.items():
        if not isinstance(filename, str) or not filename:
            raise PreflightForecastError(f"{label} has a non-string/empty filename key")
        _sha256_hex(sha, f"{label}.{filename}")


def _validate_exact_tokenizer_pin(entry: Mapping[str, Any], label: str) -> None:
    _exact_keys(entry, EXACT_TOKENIZER_PIN_KEYS, label)
    if entry.get("classification") != CLASSIFICATION_EXACT:
        raise PreflightForecastError(f"{label}.classification must be {CLASSIFICATION_EXACT!r}")
    _non_empty_str(entry.get("tokenizer_repository"), f"{label}.tokenizer_repository")
    _non_empty_str(entry.get("revision"), f"{label}.revision")
    _validate_tokenizer_file_hashes(
        entry.get("tokenizer_file_hashes"), f"{label}.tokenizer_file_hashes")
    chat_template_sha = entry.get("chat_template_sha256")
    if chat_template_sha is not None:
        _sha256_hex(chat_template_sha, f"{label}.chat_template_sha256")
    if entry.get("generation_prompt_included") is not True:
        raise PreflightForecastError(
            f"{label}.generation_prompt_included must be exactly true for a chat-template count")
    _non_empty_str(entry.get("counting_method"), f"{label}.counting_method")
    if entry.get("fallback_used") is not False:
        raise PreflightForecastError(f"{label}.fallback_used must be exactly false")
    _non_empty_str(entry.get("notes"), f"{label}.notes")


def _validate_proxy_tokenizer_pin(entry: Mapping[str, Any], label: str) -> list[list[int]]:
    """Validate a proxy tokenizer_pins entry, returning the two proxies' per-prompt lists."""
    _exact_keys(entry, PROXY_TOKENIZER_PIN_KEYS, label)
    if entry.get("classification") != CLASSIFICATION_PROXY:
        raise PreflightForecastError(f"{label}.classification must be {CLASSIFICATION_PROXY!r}")
    proxies = _list(entry.get("proxies"), f"{label}.proxies")
    if len(proxies) != 2:
        raise PreflightForecastError(f"{label}.proxies must have exactly 2 entries")
    repos: list[str] = []
    per_prompt_lists: list[list[int]] = []
    for index, raw_proxy in enumerate(proxies):
        proxy_label = f"{label}.proxies[{index}]"
        proxy = _mapping(raw_proxy, proxy_label)
        _exact_keys(proxy, PROXY_ENTRY_KEYS, proxy_label)
        repos.append(_non_empty_str(proxy.get("repository"), f"{proxy_label}.repository"))
        _non_empty_str(proxy.get("revision"), f"{proxy_label}.revision")
        _validate_tokenizer_file_hashes(
            proxy.get("tokenizer_file_hashes"), f"{proxy_label}.tokenizer_file_hashes")
        chat_template_sha = proxy.get("chat_template_sha256")
        if chat_template_sha is not None:
            _sha256_hex(chat_template_sha, f"{proxy_label}.chat_template_sha256")
        if proxy.get("generation_prompt_included") is not True:
            raise PreflightForecastError(
                f"{proxy_label}.generation_prompt_included must be exactly true")
        _non_empty_str(proxy.get("counting_method"), f"{proxy_label}.counting_method")
        per_prompt_lists.append(
            _validate_per_prompt_list(proxy.get("per_prompt"), f"{proxy_label}.per_prompt"))
    if len(set(repos)) != 2:
        raise PreflightForecastError(f"{label}.proxies must name two distinct repositories")

    divergent_count = sum(
        1 for a, b in zip(per_prompt_lists[0], per_prompt_lists[1]) if a != b)
    identical_count = capability_corpus.EXPECTED_ENTRY_COUNT - divergent_count
    convergence = _mapping(entry.get("convergence"), f"{label}.convergence")
    _exact_keys(convergence, CONVERGENCE_KEYS, f"{label}.convergence")
    if convergence.get("all_212_identical") is not (divergent_count == 0):
        raise PreflightForecastError(
            f"{label}.convergence.all_212_identical disagrees with the recomputed per-prompt "
            "comparison of the two proxy tokenizers")
    if _non_negative_int(
        convergence.get("identical_count"), f"{label}.convergence.identical_count"
    ) != identical_count:
        raise PreflightForecastError(f"{label}.convergence.identical_count disagrees")
    if _non_negative_int(
        convergence.get("divergent_count"), f"{label}.convergence.divergent_count"
    ) != divergent_count:
        raise PreflightForecastError(f"{label}.convergence.divergent_count disagrees")

    _non_empty_str(entry.get("pricing_rule"), f"{label}.pricing_rule")
    if entry.get("generation_prompt_included") is not True:
        raise PreflightForecastError(f"{label}.generation_prompt_included must be exactly true")
    _non_empty_str(entry.get("counting_method"), f"{label}.counting_method")
    if entry.get("fallback_used") is not False:
        raise PreflightForecastError(f"{label}.fallback_used must be exactly false")
    _non_empty_str(entry.get("notes"), f"{label}.notes")
    return per_prompt_lists


def _validate_byte_bound_pin(entry: Mapping[str, Any], label: str) -> None:
    _exact_keys(entry, BYTE_BOUND_PIN_KEYS, label)
    if entry.get("classification") != CLASSIFICATION_BYTE_BOUND:
        raise PreflightForecastError(
            f"{label}.classification must be {CLASSIFICATION_BYTE_BOUND!r}")
    attempted = _list(entry.get("attempted_tokenizers"), f"{label}.attempted_tokenizers")
    if not attempted:
        raise PreflightForecastError(
            f"{label}.attempted_tokenizers must record at least one attempt")
    for index, raw_attempt in enumerate(attempted):
        attempt_label = f"{label}.attempted_tokenizers[{index}]"
        attempt = _mapping(raw_attempt, attempt_label)
        _exact_keys(attempt, ATTEMPTED_TOKENIZER_KEYS, attempt_label)
        _non_empty_str(attempt.get("repository"), f"{attempt_label}.repository")
        _non_empty_str(attempt.get("revision"), f"{attempt_label}.revision")
        _non_empty_str(attempt.get("outcome"), f"{attempt_label}.outcome")
        if attempt.get("outcome") == "loaded":
            raise PreflightForecastError(
                f"{attempt_label}.outcome is 'loaded' but the model is classified "
                f"{CLASSIFICATION_BYTE_BOUND!r}; a successful attempt must never be discarded")
        _non_empty_str(attempt.get("error"), f"{attempt_label}.error")
    if entry.get("fallback_used") is not True:
        raise PreflightForecastError(f"{label}.fallback_used must be exactly true")
    _non_empty_str(entry.get("notes"), f"{label}.notes")


def _validate_tokenizer_pins(section_raw: Any) -> dict[str, list[list[int]]]:
    section = _mapping(section_raw, "tokenizer_pins")
    _exact_keys(section, MODEL_IDS, "tokenizer_pins")
    for model_id in EXACT_TOKENIZER_MODEL_IDS:
        _validate_exact_tokenizer_pin(
            _mapping(section[model_id], f"tokenizer_pins.{model_id}"),
            f"tokenizer_pins.{model_id}")
    proxy_per_prompt_by_model: dict[str, list[list[int]]] = {}
    for model_id in PROXY_TOKENIZER_MODEL_IDS:
        proxy_per_prompt_by_model[model_id] = _validate_proxy_tokenizer_pin(
            _mapping(section[model_id], f"tokenizer_pins.{model_id}"),
            f"tokenizer_pins.{model_id}")
    for model_id in BYTE_BOUND_ONLY_MODEL_IDS:
        _validate_byte_bound_pin(
            _mapping(section[model_id], f"tokenizer_pins.{model_id}"),
            f"tokenizer_pins.{model_id}")
    return proxy_per_prompt_by_model


def _validate_per_model_token_stats(
    section_raw: Any, expected_byte_bound_stats: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    section = _mapping(section_raw, "per_model_token_stats")
    _exact_keys(section, MODEL_IDS, "per_model_token_stats")
    resolved: dict[str, dict[str, Any]] = {}
    for model_id in MODEL_IDS:
        label = f"per_model_token_stats.{model_id}"
        entry = _mapping(section[model_id], label)
        _exact_keys(entry, PER_MODEL_TOKEN_STATS_KEYS, label)
        classification = entry.get("classification")
        if classification != EXPECTED_CLASSIFICATION_BY_MODEL[model_id]:
            raise PreflightForecastError(
                f"{label}.classification must be "
                f"{EXPECTED_CLASSIFICATION_BY_MODEL[model_id]!r}")
        if classification not in CLASSIFICATIONS:
            raise PreflightForecastError(f"{label}.classification is not a known classification")

        input_tokens = _validate_token_stats(
            entry.get("input_tokens"), f"{label}.input_tokens")
        byte_bound_stats = _validate_token_stats(
            entry.get("utf8_byte_reservation_bound"), f"{label}.utf8_byte_reservation_bound")
        if byte_bound_stats != expected_byte_bound_stats:
            raise PreflightForecastError(
                f"{label}.utf8_byte_reservation_bound disagrees with the shared corpus-level "
                "byte-reservation-bound stats")
        if classification == CLASSIFICATION_BYTE_BOUND and input_tokens != byte_bound_stats:
            raise PreflightForecastError(
                f"{label}.input_tokens must exactly mirror utf8_byte_reservation_bound for a "
                f"{CLASSIFICATION_BYTE_BOUND!r}-classified model")
        resolved[model_id] = {"classification": classification, "input_tokens": input_tokens}
    return resolved


def _validate_output_token_policy(
    section_raw: Any, role_limits_v2: Mapping[str, Any], protocol: Mapping[str, Any],
) -> dict[str, int]:
    section = _mapping(section_raw, "output_token_policy")
    _exact_keys(section, OUTPUT_TOKEN_POLICY_KEYS, "output_token_policy")
    if _positive_int(
        section.get("theoretical_minimum_output_tokens_per_call"),
        "output_token_policy.theoretical_minimum_output_tokens_per_call",
    ) != THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL:
        raise PreflightForecastError(
            "output_token_policy.theoretical_minimum_output_tokens_per_call must be exactly "
            f"{THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL}")
    _non_empty_str(section.get("source"), "output_token_policy.source")

    ceilings_raw = _mapping(
        section.get("effective_output_ceiling_per_model"),
        "output_token_policy.effective_output_ceiling_per_model")
    _exact_keys(
        ceilings_raw, MODEL_IDS, "output_token_policy.effective_output_ceiling_per_model")
    ceilings: dict[str, int] = {}
    for model_id in MODEL_IDS:
        label = f"output_token_policy.effective_output_ceiling_per_model.{model_id}"
        declared = _positive_int(ceilings_raw[model_id], label)
        resolved = phase2_role_limits.resolve_request_parameters(
            role_limits_v2, protocol, model_id, CAPABILITY_LIMITS_ROLE)
        if declared != resolved.effective_max_tokens:
            raise PreflightForecastError(
                f"{label} disagrees with the frozen role-limits v2 resolution: "
                f"declared {declared}, resolved {resolved.effective_max_tokens}")
        ceilings[model_id] = declared
    return ceilings


def _validate_retry_policy(section_raw: Any, role_limits_v2: Mapping[str, Any]) -> int:
    section = _mapping(section_raw, "retry_policy")
    _exact_keys(section, RETRY_POLICY_KEYS, "retry_policy")
    transport = _mapping(
        role_limits_v2["request_settings"]["transport"], "role-limits transport")
    max_retries = _positive_int(section.get("max_retries"), "retry_policy.max_retries")
    max_attempts = _positive_int(section.get("max_attempts"), "retry_policy.max_attempts")
    if max_retries != int(transport["max_retries"]):
        raise PreflightForecastError("retry_policy.max_retries disagrees with role-limits v2")
    if max_attempts != int(transport["max_attempts"]):
        raise PreflightForecastError("retry_policy.max_attempts disagrees with role-limits v2")
    _non_empty_str(section.get("source"), "retry_policy.source")
    return max_attempts


def _prices_for_model(snapshot: Mapping[str, Any], model_id: str) -> tuple[Decimal, Decimal]:
    entry = _mapping(snapshot["models"][model_id], f"snapshot.models.{model_id}")
    input_price = Decimal(str(entry["input_usd_per_million_tokens"]))
    output_price = Decimal(str(entry["output_usd_per_million_tokens"]))
    return input_price, output_price


def _validate_scenarios(
    section_raw: Any, *, per_model_stats: Mapping[str, Mapping[str, Any]],
    output_ceilings: Mapping[str, int], snapshot: Mapping[str, Any], max_attempts: int,
    byte_bound_stats_by_model: Mapping[str, dict[str, Any]],
) -> Decimal:
    section = _mapping(section_raw, "scenarios")
    _exact_keys(section, SCENARIO_NAMES, "scenarios")
    calls_per_model = capability_corpus.EXPECTED_ENTRY_COUNT

    def per_model_total(model_id: str) -> int:
        return int(per_model_stats[model_id]["input_tokens"]["total"])

    # theoretical_minimum: expected input tokens + a fixed 10 output tokens/call, no retries.
    theo = _mapping(section["theoretical_minimum"], "scenarios.theoretical_minimum")
    _exact_keys(theo, CEILING_SCENARIO_KEYS, "scenarios.theoretical_minimum")
    _non_empty_str(theo.get("formula"), "scenarios.theoretical_minimum.formula")
    theo_per_model = _mapping(theo.get("per_model"), "scenarios.theoretical_minimum.per_model")
    _exact_keys(theo_per_model, MODEL_IDS, "scenarios.theoretical_minimum.per_model")
    theo_total = Decimal(0)
    for model_id in MODEL_IDS:
        input_price, output_price = _prices_for_model(snapshot, model_id)
        theo_total += _validate_scenario_component(
            theo_per_model[model_id], f"scenarios.theoretical_minimum.per_model.{model_id}",
            total_input_tokens=per_model_total(model_id), calls=calls_per_model,
            output_tokens_per_call=THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL,
            input_price=input_price, output_price=output_price,
        )
    theo_total_declared = _decimal_string(
        theo.get("total_usd"), "scenarios.theoretical_minimum.total_usd")
    if theo_total_declared != theo_total:
        raise PreflightForecastError("scenarios.theoretical_minimum.total_usd disagrees")

    # no_retry_maximum: input + the full effective output ceiling per model-role, no retries.
    ceiling = _mapping(section["no_retry_maximum"], "scenarios.no_retry_maximum")
    _exact_keys(ceiling, CEILING_SCENARIO_KEYS, "scenarios.no_retry_maximum")
    _non_empty_str(ceiling.get("formula"), "scenarios.no_retry_maximum.formula")
    ceiling_per_model = _mapping(
        ceiling.get("per_model"), "scenarios.no_retry_maximum.per_model")
    _exact_keys(ceiling_per_model, MODEL_IDS, "scenarios.no_retry_maximum.per_model")
    ceiling_total = Decimal(0)
    per_model_ceiling_usd: dict[str, Decimal] = {}
    for model_id in MODEL_IDS:
        input_price, output_price = _prices_for_model(snapshot, model_id)
        component_total = _validate_scenario_component(
            ceiling_per_model[model_id], f"scenarios.no_retry_maximum.per_model.{model_id}",
            total_input_tokens=per_model_total(model_id), calls=calls_per_model,
            output_tokens_per_call=output_ceilings[model_id],
            input_price=input_price, output_price=output_price,
        )
        per_model_ceiling_usd[model_id] = component_total
        ceiling_total += component_total
    ceiling_total_declared = _decimal_string(
        ceiling.get("total_usd"), "scenarios.no_retry_maximum.total_usd")
    if ceiling_total_declared != ceiling_total:
        raise PreflightForecastError("scenarios.no_retry_maximum.total_usd disagrees")

    # planning_retry_scenario / four_attempt_stress: pure Decimal multiples of no_retry_maximum.
    def _validate_derived(
        name: str, multiplier: Decimal, extra_keys: frozenset[str] = frozenset(),
    ) -> tuple[Mapping[str, Any], Decimal]:
        entry = _mapping(section[name], f"scenarios.{name}")
        _exact_keys(entry, DERIVED_SCENARIO_KEYS | extra_keys, f"scenarios.{name}")
        _non_empty_str(entry.get("formula"), f"scenarios.{name}.formula")
        declared_multiplier = _decimal_string(
            entry.get("multiplier"), f"scenarios.{name}.multiplier")
        if declared_multiplier != multiplier:
            raise PreflightForecastError(f"scenarios.{name}.multiplier disagrees")
        entry_per_model = _mapping(entry.get("per_model"), f"scenarios.{name}.per_model")
        _exact_keys(entry_per_model, MODEL_IDS, f"scenarios.{name}.per_model")
        running_total = Decimal(0)
        for model_id in MODEL_IDS:
            model_entry = _mapping(
                entry_per_model[model_id], f"scenarios.{name}.per_model.{model_id}")
            _exact_keys(
                model_entry, DERIVED_PER_MODEL_KEYS, f"scenarios.{name}.per_model.{model_id}")
            expected_value = per_model_ceiling_usd[model_id] * multiplier
            observed_value = _decimal_string(
                model_entry.get("total_usd"), f"scenarios.{name}.per_model.{model_id}.total_usd")
            if observed_value != expected_value:
                raise PreflightForecastError(
                    f"scenarios.{name}.per_model.{model_id}.total_usd disagrees with the "
                    f"recomputed value: observed {observed_value}, expected {expected_value}")
            running_total += observed_value
        declared_total = _decimal_string(entry.get("total_usd"), f"scenarios.{name}.total_usd")
        if declared_total != running_total:
            raise PreflightForecastError(f"scenarios.{name}.total_usd disagrees")
        return entry, declared_total

    _validate_derived("planning_retry_scenario", PLANNING_RETRY_MULTIPLIER)

    stress_entry, stress_total = _validate_derived(
        "four_attempt_stress", Decimal(max_attempts), FOUR_ATTEMPT_EXTRA_KEYS)

    qwen_id = next(iter(PROXY_TOKENIZER_MODEL_IDS))
    qwen_input_price, qwen_output_price = _prices_for_model(snapshot, qwen_id)
    qwen_byte_total = int(byte_bound_stats_by_model[qwen_id]["total"])
    qwen_byte_component = compute_scenario_component(
        total_input_tokens=qwen_byte_total, calls=calls_per_model,
        output_tokens_per_call=output_ceilings[qwen_id],
        input_price=qwen_input_price, output_price=qwen_output_price,
    )
    expected_byte_stress = Decimal(qwen_byte_component["total_usd"]) * Decimal(max_attempts)
    observed_byte_stress = _decimal_string(
        stress_entry.get("qwen_3_7_plus_byte_bound_stress_usd"),
        "scenarios.four_attempt_stress.qwen_3_7_plus_byte_bound_stress_usd")
    if observed_byte_stress != expected_byte_stress:
        raise PreflightForecastError(
            "scenarios.four_attempt_stress.qwen_3_7_plus_byte_bound_stress_usd disagrees with "
            f"the recomputed value: observed {observed_byte_stress}, "
            f"expected {expected_byte_stress}")
    _non_empty_str(
        stress_entry.get("qwen_3_7_plus_byte_bound_note"),
        "scenarios.four_attempt_stress.qwen_3_7_plus_byte_bound_note")

    return stress_total


def _validate_caveats(section_raw: Any) -> None:
    section = _list(section_raw, "caveats")
    seen_ids: set[str] = set()
    for index, raw_entry in enumerate(section):
        label = f"caveats[{index}]"
        entry = _mapping(raw_entry, label)
        _exact_keys(entry, CAVEAT_KEYS, label)
        caveat_id = _non_empty_str(entry.get("id"), f"{label}.id")
        _non_empty_str(entry.get("text"), f"{label}.text")
        if caveat_id in seen_ids:
            raise PreflightForecastError(f"duplicate caveat id: {caveat_id!r}")
        seen_ids.add(caveat_id)
    missing = REQUIRED_CAVEAT_IDS - seen_ids
    if missing:
        raise PreflightForecastError(f"caveats missing required entries: {sorted(missing)!r}")


# --- top-level validation ------------------------------------------------------------------------
#
# Two artifact shapes share every section validator below (bindings, corpus, tokenizer pins,
# per-model token stats, output/retry policy, scenario arithmetic, caveats) via
# ``_validate_shared_body``, and differ only in their header fields and in how they are allowed
# to relate to ``halt_cap_usd``:
#
# * ``validate_forecast`` / STATUS: the canonical, "ready" forecast. Hard-gates
#   four_attempt_stress strictly below halt_cap_usd, per the frozen spec ("the stress scenario
#   must remain below it"). If the honestly-computed numbers do not clear that gate, THIS
#   function must refuse the artifact -- it must never be relaxed to make a conflicted forecast
#   look ready.
# * ``validate_conflict_report`` / CONFLICT_STATUS: a distinctly-named, distinctly-schemaed
#   diagnostic record for exactly the situation where the gate does NOT clear. It requires the
#   opposite inequality (stress_total >= halt_cap) plus a non-empty, honest ``resolution``
#   section, so it can never be produced for -- or mistaken for -- a forecast that actually
#   passes the gate.


def _validate_shared_body(
    artifact: Mapping[str, Any], *, root: Path,
    protocol: Mapping[str, Any], role_limits_v2: Mapping[str, Any],
    snapshot: Mapping[str, Any], bundle: Mapping[str, Any],
) -> tuple[Decimal, Decimal]:
    """Validate every section common to both artifact shapes; return (stress_total, halt_cap)."""
    timestamp = artifact.get("generated_at_utc")
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise PreflightForecastError("generated_at_utc must be an explicit UTC timestamp")

    dependency_versions = _mapping(
        artifact.get("dependency_versions"), "dependency_versions")
    _exact_keys(dependency_versions, DEPENDENCY_VERSIONS_KEYS, "dependency_versions")
    for name in DEPENDENCY_VERSIONS_KEYS:
        _non_empty_str(dependency_versions.get(name), f"dependency_versions.{name}")

    entries = _validate_bindings(
        artifact.get("bindings"), root, protocol, role_limits_v2, snapshot, bundle)
    _validate_corpus_section(artifact.get("corpus"), protocol)
    proxy_per_prompt_by_model = _validate_tokenizer_pins(artifact.get("tokenizer_pins"))

    byte_bound_per_prompt_raw = _list(
        artifact.get("corpus_utf8_byte_reservation_bound_per_prompt"),
        "corpus_utf8_byte_reservation_bound_per_prompt")
    if len(byte_bound_per_prompt_raw) != capability_corpus.EXPECTED_ENTRY_COUNT:
        raise PreflightForecastError(
            "corpus_utf8_byte_reservation_bound_per_prompt must have exactly "
            f"{capability_corpus.EXPECTED_ENTRY_COUNT} entries")
    byte_bound_per_prompt = [
        _positive_int(v, f"corpus_utf8_byte_reservation_bound_per_prompt[{i}]")
        for i, v in enumerate(byte_bound_per_prompt_raw)
    ]
    expected_byte_bound_per_prompt = [
        byte_reservation_bound(entry["system_prompt"], entry["user_prompt"]) for entry in entries
    ]
    if byte_bound_per_prompt != expected_byte_bound_per_prompt:
        raise PreflightForecastError(
            "corpus_utf8_byte_reservation_bound_per_prompt disagrees with the value freshly "
            "recomputed from the rendered corpus")
    byte_bound_stats = compute_token_stats(byte_bound_per_prompt)

    per_model_resolved = _validate_per_model_token_stats(
        artifact.get("per_model_token_stats"), byte_bound_stats)
    byte_bound_stats_by_model = {model_id: byte_bound_stats for model_id in MODEL_IDS}

    # Qwen3.7-Plus's declared input_tokens.per_prompt must be exactly the elementwise max of
    # its two proxies' own recorded per-prompt series (both independently validated above by
    # _validate_tokenizer_pins), per the frozen "conservative (larger) proxy count" pricing rule.
    for model_id, proxy_lists in proxy_per_prompt_by_model.items():
        expected_max = [max(a, b) for a, b in zip(proxy_lists[0], proxy_lists[1])]
        observed = per_model_resolved[model_id]["input_tokens"]["per_prompt"]
        if observed != expected_max:
            raise PreflightForecastError(
                f"per_model_token_stats.{model_id}.input_tokens.per_prompt is not the "
                "elementwise max of its two recorded proxy tokenizer series")

    output_ceilings = _validate_output_token_policy(
        artifact.get("output_token_policy"), role_limits_v2, protocol)
    max_attempts = _validate_retry_policy(artifact.get("retry_policy"), role_limits_v2)
    _non_empty_str(
        artifact.get("replace_with_actuals_requirement"), "replace_with_actuals_requirement")

    stress_total = _validate_scenarios(
        artifact.get("scenarios"), per_model_stats=per_model_resolved,
        output_ceilings=output_ceilings, snapshot=snapshot, max_attempts=max_attempts,
        byte_bound_stats_by_model=byte_bound_stats_by_model,
    )

    capability_preflight = _mapping(
        protocol["materialization_requirements"]["capability_preflight"],
        "protocol materialization_requirements.capability_preflight")
    expected_halt_cap = Decimal(str(capability_preflight["proposed_cap_usd"]))
    halt_cap = _decimal_string(artifact.get("halt_cap_usd"), "halt_cap_usd")
    if halt_cap != expected_halt_cap:
        raise PreflightForecastError(
            "halt_cap_usd disagrees with the frozen protocol's proposed_cap_usd")
    declared_margin = _decimal_string(artifact.get("stress_margin_usd"), "stress_margin_usd")
    if declared_margin != halt_cap - stress_total:
        raise PreflightForecastError("stress_margin_usd disagrees with the recomputed margin")

    _validate_caveats(artifact.get("caveats"))
    return stress_total, halt_cap


def validate_forecast(
    artifact: Mapping[str, Any], *, root: str | Path,
    protocol: Mapping[str, Any], role_limits_v2: Mapping[str, Any],
    snapshot: Mapping[str, Any], bundle: Mapping[str, Any],
) -> None:
    """Validate the frozen, "ready" capability-preflight forecast artifact, fail-closed.

    Hard-gates ``four_attempt_stress`` strictly below ``halt_cap_usd``. If the honestly
    counted, hash-bound inputs do not clear that gate, this raises rather than accepting a
    forecast that only appears to satisfy the frozen spec's "must remain below" requirement --
    see ``validate_conflict_report`` for the artifact shape that situation must use instead.
    """
    root = Path(root)
    artifact = _mapping(artifact, "artifact")
    _exact_keys(artifact, TOP_LEVEL_KEYS, "artifact")

    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise PreflightForecastError("unsupported forecast schema_version")
    if artifact.get("artifact_id") != ARTIFACT_ID:
        raise PreflightForecastError("forecast artifact_id drifted")
    protocol_id = _non_empty_str(protocol.get("protocol_id"), "protocol protocol_id")
    if artifact.get("protocol_id") != protocol_id:
        raise PreflightForecastError("forecast protocol_id disagrees with the frozen protocol")
    if artifact.get("status") != STATUS:
        raise PreflightForecastError("forecast status drifted")
    if artifact.get("execution_authorized") is not False:
        raise PreflightForecastError("execution_authorized must be exactly false")

    stress_total, halt_cap = _validate_shared_body(
        artifact, root=root, protocol=protocol, role_limits_v2=role_limits_v2,
        snapshot=snapshot, bundle=bundle,
    )
    if stress_total >= halt_cap:
        raise PreflightForecastError(
            f"four_attempt_stress total ({stress_total}) does not remain below halt_cap_usd "
            f"({halt_cap}); this artifact must be a conflict report (see "
            "validate_conflict_report), not a 'ready' forecast")


def validate_conflict_report(
    artifact: Mapping[str, Any], *, root: str | Path,
    protocol: Mapping[str, Any], role_limits_v2: Mapping[str, Any],
    snapshot: Mapping[str, Any], bundle: Mapping[str, Any],
) -> None:
    """Validate the diagnostic conflict-report artifact, fail-closed.

    The mirror image of ``validate_forecast``'s gate: requires ``four_attempt_stress`` to
    actually be at or above ``halt_cap_usd`` (so a conflict report can never be produced for,
    or silently kept around after, a forecast that in fact clears the gate) and requires a
    non-empty, honest ``resolution`` section naming the blocking scenario and the owner's
    concrete resolution options -- never a silent auto-resolution.
    """
    root = Path(root)
    artifact = _mapping(artifact, "artifact")
    _exact_keys(artifact, CONFLICT_TOP_LEVEL_KEYS, "artifact")

    if artifact.get("schema_version") != CONFLICT_SCHEMA_VERSION:
        raise PreflightForecastError("unsupported conflict-report schema_version")
    if artifact.get("artifact_id") != CONFLICT_ARTIFACT_ID:
        raise PreflightForecastError("conflict-report artifact_id drifted")
    protocol_id = _non_empty_str(protocol.get("protocol_id"), "protocol protocol_id")
    if artifact.get("protocol_id") != protocol_id:
        raise PreflightForecastError(
            "conflict-report protocol_id disagrees with the frozen protocol")
    if artifact.get("status") != CONFLICT_STATUS:
        raise PreflightForecastError("conflict-report status drifted")
    if artifact.get("execution_authorized") is not False:
        raise PreflightForecastError("execution_authorized must be exactly false")

    stress_total, halt_cap = _validate_shared_body(
        artifact, root=root, protocol=protocol, role_limits_v2=role_limits_v2,
        snapshot=snapshot, bundle=bundle,
    )
    if stress_total < halt_cap:
        raise PreflightForecastError(
            f"four_attempt_stress total ({stress_total}) already clears halt_cap_usd "
            f"({halt_cap}); this is no longer a genuine conflict and must be reissued as a "
            "'ready' forecast (see validate_forecast), not a conflict report")

    resolution = _mapping(artifact.get("resolution"), "resolution")
    _exact_keys(resolution, RESOLUTION_KEYS, "resolution")
    if resolution.get("required") is not True:
        raise PreflightForecastError("resolution.required must be exactly true")
    if resolution.get("stress_below_halt_cap") is not False:
        raise PreflightForecastError("resolution.stress_below_halt_cap must be exactly false")
    _non_empty_str(resolution.get("blocking_scenario"), "resolution.blocking_scenario")
    options = _list(resolution.get("options"), "resolution.options")
    if not options:
        raise PreflightForecastError("resolution.options must be a non-empty list")
    for index, option in enumerate(options):
        _non_empty_str(option, f"resolution.options[{index}]")


def load_and_validate(
    artifact_path: str | Path = DEFAULT_ARTIFACT_PATH,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    role_limits_v2_path: str | Path = DEFAULT_ROLE_LIMITS_V2_PATH,
    role_limits_v1_path: str | Path = DEFAULT_ROLE_LIMITS_V1_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    bundle_path: str | Path = DEFAULT_BUNDLE_PATH,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    protocol_path = Path(protocol_path)
    root = Path(project_root) if project_root is not None else protocol_path.resolve().parent.parent
    protocol = phase2_plan.load_protocol(protocol_path)
    role_limits_v2, _protocol, snapshot = phase2_role_limits.load_and_validate_v2(
        role_limits_v2_path, protocol_path, snapshot_path, role_limits_v1_path)
    bundle, _protocol2 = phase2_prompt_bundle.load_and_validate(
        bundle_path=bundle_path, protocol_path=protocol_path)
    artifact = _load_json(artifact_path)
    validate_forecast(
        artifact, root=root, protocol=protocol, role_limits_v2=role_limits_v2,
        snapshot=snapshot, bundle=bundle,
    )
    return artifact


def load_and_validate_conflict_report(
    artifact_path: str | Path = DEFAULT_CONFLICT_ARTIFACT_PATH,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    role_limits_v2_path: str | Path = DEFAULT_ROLE_LIMITS_V2_PATH,
    role_limits_v1_path: str | Path = DEFAULT_ROLE_LIMITS_V1_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    bundle_path: str | Path = DEFAULT_BUNDLE_PATH,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    protocol_path = Path(protocol_path)
    root = Path(project_root) if project_root is not None else protocol_path.resolve().parent.parent
    protocol = phase2_plan.load_protocol(protocol_path)
    role_limits_v2, _protocol, snapshot = phase2_role_limits.load_and_validate_v2(
        role_limits_v2_path, protocol_path, snapshot_path, role_limits_v1_path)
    bundle, _protocol2 = phase2_prompt_bundle.load_and_validate(
        bundle_path=bundle_path, protocol_path=protocol_path)
    artifact = _load_json(artifact_path)
    validate_conflict_report(
        artifact, root=root, protocol=protocol, role_limits_v2=role_limits_v2,
        snapshot=snapshot, bundle=bundle,
    )
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--conflict", action="store_true",
        help="validate the diagnostic conflict-report artifact instead of the ready forecast")
    parser.add_argument("--artifact", default=None)
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL_PATH))
    parser.add_argument("--role-limits-v2", default=str(DEFAULT_ROLE_LIMITS_V2_PATH))
    parser.add_argument("--role-limits-v1", default=str(DEFAULT_ROLE_LIMITS_V1_PATH))
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT_PATH))
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE_PATH))
    parser.add_argument("--project-root", default=None)
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("only --check is supported")
    if args.conflict:
        artifact_path = args.artifact if args.artifact is not None else str(
            DEFAULT_CONFLICT_ARTIFACT_PATH)
        artifact = load_and_validate_conflict_report(
            artifact_path, args.protocol, args.role_limits_v2, args.role_limits_v1,
            args.snapshot, args.bundle, args.project_root,
        )
        print(
            "verified Phase 2 capability-preflight token/cost forecast CONFLICT REPORT "
            "(stress exceeds halt_cap_usd; owner resolution required); "
            f"canonical_sha256={phase2_plan.canonical_sha256(artifact)}; "
            "execution_authorized=NO"
        )
        return 0
    artifact_path = args.artifact if args.artifact is not None else str(DEFAULT_ARTIFACT_PATH)
    artifact = load_and_validate(
        artifact_path, args.protocol, args.role_limits_v2, args.role_limits_v1,
        args.snapshot, args.bundle, args.project_root,
    )
    print(
        "verified frozen Phase 2 capability-preflight token/cost forecast; "
        f"canonical_sha256={phase2_plan.canonical_sha256(artifact)}; "
        "execution_authorized=NO"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
