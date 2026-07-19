import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from rejudge import phase2_capability_corpus as capability_corpus
from rejudge import phase2_execution as pe
from rejudge import phase2_plan
from rejudge import phase2_preflight_forecast as forecast
from rejudge import phase2_prompt_bundle as prompt_bundle
from rejudge import phase2_provider_price_snapshot as price_snapshot
from rejudge import phase2_role_limits as role_limits
from rejudge import phase2_stage_family


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"
COMBINED_AI_AUDIT_PATH = ROOT / "rejudge" / "phase2_resolvability_ai_review.json"
A1_AMENDMENT_PATH = ROOT / "rejudge" / "phase2_resolvability_review_amendment_2026-07-16.json"
PROMPT_BUNDLE_PATH = ROOT / "rejudge" / "phase2_prompt_bundle.json"
PROMPT_BUNDLE_APPROVAL_PATH = (
    ROOT / "rejudge" / "phase2_prompt_bundle_approval_2026-07-18.json")
PRICE_SNAPSHOT_PATH = ROOT / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json"
UV_LOCK_PATH = ROOT / "uv.lock"
DELEGATION_PATH = ROOT / "rejudge" / "phase2_preflight_delegation_2026-07-19.json"
# The authorization's approval_basis is now PINNED to the frozen preflight delegation record
# (see pe.DEFAULT_PREFLIGHT_DELEGATION_RELATIVE_PATH), not any resolvable-from-anywhere doc.
APPROVAL_BASIS_PATH = DELEGATION_PATH
PROVIDER_REFRESH_PATH = ROOT / "rejudge" / "phase2_provider_refresh_2026-07-19.json"
GEMMA_CLOSURE_PATH = ROOT / "rejudge" / "gemma_recovery_closure_2026-07-19.json"
PRIOR_ATTEMPT_CLOSURE_PATH = ROOT / "rejudge" / "phase2_preflight_abort_closure_2026-07-19.json"
READY_ARTIFACT_V2_PATH = ROOT / "rejudge" / "phase2_preflight_forecast_2026-07-19.json"
PROVIDER_RECONCILIATION_2026_07_19_PATH = (
    ROOT / "rejudge" / "phase2_provider_reconciliation_2026-07-19.json")
R2_CLOSURE_PATH = ROOT / "rejudge" / "phase2_preflight_r2_closure_2026-07-19.json"
CARRYFORWARD_PATH = ROOT / "rejudge" / "phase2_preflight_carryforward_2026-07-19.json"
STAGE_FAMILY_LEDGER_PATH = ROOT / "rejudge" / "phase2_stage_family_ledger_2026-07-19.json"
CEILING_CORRECTION_PATH = ROOT / "rejudge" / "phase2_ceiling_correction_2026-07-19.json"
STANDING_DELEGATION_PATH = ROOT / "rejudge" / "phase2_standing_delegation_2026-07-19.json"
STORAGE_POLICY_PATH = ROOT / "rejudge" / "phase2_storage_policy_2026-07-18.json"

STAGE_CAP_USD = 15.0
CUMULATIVE_CAP_USD = 1500.0
LEDGER_BINDING = {
    "path": "rejudge/output/phase2_capability_preflight_ledger.jsonl",
    "ledger_identity": "phase2-project-wide-ledger-v1",
}
APPROVAL_BASIS_TRACKED_PATH = str(APPROVAL_BASIS_PATH.relative_to(ROOT).as_posix())
APPROVAL_BASIS_SHA256 = hashlib.sha256(APPROVAL_BASIS_PATH.read_bytes()).hexdigest()
IMPLEMENTATION_PROVENANCE_BINDING = {
    "git_commit": "a" * 40,
    "code_bundle_sha256": pe.compute_code_bundle_sha256(ROOT),
}


def _canon_sha(path: Path) -> str:
    return phase2_plan.canonical_sha256(json.loads(path.read_text(encoding="utf-8")))


def _flip_hex_digest(value: str) -> str:
    """Return a still-well-formed 64-hex digest that differs from *value*."""
    last = value[-1]
    replacement = "0" if last != "0" else "1"
    return value[:-1] + replacement


def _write_json(path: Path, name: str, payload) -> Path:
    target = path / name
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


# ``cost_forecast``, ``storage_policy``, and ``provider_reconciliation_evidence`` are bound
# via ``_bind_json_artifact_checked``, which -- unlike the older, deliberately-anywhere
# per_model_role_limits/provider_request_fields/gemma-waiver bindings -- freezes the path as
# always relative to project_root (an absolute path, even one that happens to point at a
# byte-identical file, is rejected). So every ``root`` a test validates against (the real
# repo root, or a tmp copy of its tracked files) needs its own copy of these three
# placeholders at this same frozen relative path and content, or the bound hash won't match.
SYNTHETIC_ROOT_RELATIVE_DIR = "rejudge/output/_test_phase2_execution_artifacts"
SYNTHETIC_ROOT_RELATIVE_ARTIFACTS: dict[str, tuple[str, dict]] = {
    "cost_forecast": (
        f"{SYNTHETIC_ROOT_RELATIVE_DIR}/phase2_cost_forecast.json", {"forecast": "placeholder"}),
    "storage_policy": (
        f"{SYNTHETIC_ROOT_RELATIVE_DIR}/phase2_storage_policy.json", {"policy": "placeholder"}),
}


def _write_synthetic_root_relative_artifacts(root: Path) -> dict[str, Path]:
    """Write the root-contained placeholder artifacts at their frozen relative paths."""
    paths: dict[str, Path] = {}
    for name, (relative, payload) in SYNTHETIC_ROOT_RELATIVE_ARTIFACTS.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = target
    return paths


# Every top-level rejudge/*.json artifact (including the new 2026-07-19 sealed artifacts:
# the preflight delegation, provider refresh, its raw response, the gemma recovery closure and
# its run record, and the 2026-07-19 provider reconciliation), every question bank, every world
# spec, uv.lock, and the historical decision-proposal doc. Deliberately globs rather than
# hand-listing so a new tracked rejudge/*.json artifact is automatically included; deliberately
# EXCLUDES rejudge/output/ (hundreds of MB of run data, except the one small tracked file it
# needs) and does not glob subdirectories.
DATA_GLOB_PATTERNS: tuple[str, ...] = (
    "questions/*.json", "world_specs/*.txt", "rejudge/*.json",
    "rejudge/output/calibration_models.json",
)
DATA_EXTRA_FILES: tuple[str, ...] = ("uv.lock", "docs/phase2-decision-proposal.md")
# The code-provenance frozen file list, needed so implementation_provenance's recomputed
# code_bundle_sha256 matches under any tmp-copied root a full manifest validates against.
DATA_CODE_FILES: tuple[str, ...] = pe.CODE_PROVENANCE_FROZEN_FILES


def _copy_tracked_data_files(destination: Path) -> Path:
    """Copy every small tracked data file (and the frozen code-provenance files) a full
    validation needs into ``destination``, preserving each source's relative path.
    """
    for pattern in DATA_GLOB_PATTERNS:
        for source in ROOT.glob(pattern):
            relative = source.relative_to(ROOT)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    for relative in DATA_EXTRA_FILES + DATA_CODE_FILES:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    # cost_forecast/storage_policy are frozen as always relative to project_root, so any root a
    # manifest built from `synthetic_artifacts` is validated against needs its own copy at the
    # same relative path (placeholder content here; callers that need the real, semantically
    # valid content -- see `_build_green_root` -- overwrite these two files afterward).
    _write_synthetic_root_relative_artifacts(destination)
    return destination


def _build_green_root() -> Path:
    """Build a disposable, fully self-contained, byte-for-byte copy of every tracked data and
    code file this suite's happy-path tests validate a manifest against.

    Deliberately byte-identical to ROOT (no content is ever mutated here): several already-
    frozen modules pin hardcoded canonical hashes of the REAL, unmodified protocol
    (``phase2_resolvability_ai_review.BASE_PROTOCOL_CANONICAL_SHA256`` and its analogues), so
    this cannot be a "cheap prices" root the way test_phase2_preflight_forecast.py's own
    ``ready_context`` fixture is -- that fixture calls ``phase2_preflight_forecast.
    validate_forecast`` directly (bypassing ``price_snapshot.load_and_validate``'s cross-check
    against the frozen protocol prices entirely); this module instead goes through the full
    ``phase2_execution.validate_execution_manifest``, which loads and re-validates the real
    protocol/snapshot pair as part of every call. This fixture exists only so tests never write
    into the live repository tree and so the new 2026-07-19 sealed artifacts and the frozen
    code-provenance files are present at a stable location every test can share.

    Because no genuinely "ready" (gate-clearing) capability-preflight forecast can exist against
    the real, frozen prices right now (four_attempt_stress currently exceeds halt_cap_usd; see
    the tracked ``rejudge/phase2_preflight_forecast_conflict_2026-07-18.json`` -- rebuilding the
    real, price-accurate "green" forecast is an explicitly separate, later task), the ``manifest``
    fixture below stubs out the deep economics check inside cost_forecast's semantic gate for
    every test that uses it; the REAL, unpatched gate is exercised directly by the dedicated
    cost_forecast tests further down, which build their own fully offline, self-consistent
    "ready" forecast the same way test_phase2_preflight_forecast.py's ``ready_context`` does.
    """
    green = Path(tempfile.mkdtemp(prefix="phase2_execution_green_root_"))
    _copy_tracked_data_files(green)
    return green


GREEN_ROOT = _build_green_root()
GREEN_PROTOCOL_PATH = GREEN_ROOT / "rejudge" / "phase2_protocol.json"
GREEN_PRICE_SNAPSHOT_PATH = GREEN_ROOT / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json"
GREEN_PROMPT_BUNDLE_APPROVAL_PATH = (
    GREEN_ROOT / "rejudge" / "phase2_prompt_bundle_approval_2026-07-18.json")
GREEN_UV_LOCK_PATH = GREEN_ROOT / "uv.lock"


def _build_ready_forecast_payload_v2(
    *, root: Path, protocol: Mapping, role_limits_v3: Mapping, snapshot: Mapping,
    bundle: Mapping, provider_refresh: Mapping,
) -> dict:
    """Build a genuinely-passing v2 "ready" capability-preflight forecast, offline, from the
    validator's own requirements -- no network, no real tokenizer download.

    Reuses the REAL per-model token stats / tokenizer pins / rendered-corpus binding / byte-
    reservation-bound-per-prompt from the tracked conflict-report artifact unchanged (those
    sections are price-independent, and the corpus/protocol/bundle content is byte-identical to
    what produced them), and recomputes every price-sensitive field (the four scenarios AND the
    new UTF-8 reservation-envelope disclosure) fresh against ``role_limits_v3``'s retry-attempt
    count and ``snapshot``'s prices -- mirrors ``scripts/build_phase2_preflight_forecast.py``'s
    own ``build_forecast_v2`` and ``tests/test_phase2_preflight_forecast.py``'s ``ready_context``
    fixture. Raises ``AssertionError`` (not a production exception) if the given
    ``role_limits_v3``/``snapshot`` combination is too expensive to clear ``halt_cap_usd`` --
    this helper never fabricates a passing artifact.
    """
    # Loaded as raw JSON, deliberately NOT re-validated here: the tracked conflict artifact's
    # own ``bindings`` section is bound to the REAL protocol/snapshot, which may disagree with a
    # caller-supplied synthetic snapshot. Only its price-INDEPENDENT sections (per-model token
    # stats, tokenizer pins, corpus binding, byte-reservation bound) are reused below; every
    # price-sensitive field is freshly recomputed and the resulting artifact IS fully validated
    # (via ``forecast.validate_forecast_v2`` at the end of this function) before being returned.
    conflict_artifact = json.loads(
        forecast.DEFAULT_CONFLICT_ARTIFACT_PATH.read_text(encoding="utf-8"))
    calls = capability_corpus.EXPECTED_ENTRY_COUNT
    output_ceilings = {
        model_id: role_limits.resolve_request_parameters(
            role_limits_v3, protocol, model_id, "capability_qa").effective_max_tokens
        for model_id in forecast.MODEL_IDS
    }

    def price_for(model_id):
        entry = snapshot["models"][model_id]
        return (
            Decimal(str(entry["input_usd_per_million_tokens"])),
            Decimal(str(entry["output_usd_per_million_tokens"])),
        )

    theo_per_model, no_retry_per_model = {}, {}
    theo_total = no_retry_total = Decimal(0)
    no_retry_component_usd = {}
    for model_id in forecast.MODEL_IDS:
        total_input_tokens = conflict_artifact["per_model_token_stats"][model_id][
            "input_tokens"]["total"]
        input_price, output_price = price_for(model_id)
        theo = forecast.compute_scenario_component(
            total_input_tokens=total_input_tokens, calls=calls,
            output_tokens_per_call=forecast.THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL,
            input_price=input_price, output_price=output_price,
        )
        theo_per_model[model_id] = theo
        theo_total += Decimal(theo["total_usd"])
        no_retry = forecast.compute_scenario_component(
            total_input_tokens=total_input_tokens, calls=calls,
            output_tokens_per_call=output_ceilings[model_id],
            input_price=input_price, output_price=output_price,
        )
        no_retry_per_model[model_id] = no_retry
        no_retry_component_usd[model_id] = Decimal(no_retry["total_usd"])
        no_retry_total += Decimal(no_retry["total_usd"])

    max_attempts = int(role_limits_v3["request_settings"]["transport"]["max_attempts"])
    max_retries = int(role_limits_v3["request_settings"]["transport"]["max_retries"])

    def derived(multiplier):
        per_model, total = {}, Decimal(0)
        for model_id in forecast.MODEL_IDS:
            value = no_retry_component_usd[model_id] * multiplier
            per_model[model_id] = {"total_usd": str(value)}
            total += value
        return per_model, total

    planning_per_model, planning_total = derived(forecast.PLANNING_RETRY_MULTIPLIER)
    stress_per_model, stress_total = derived(Decimal(max_attempts))

    byte_bound_per_prompt = conflict_artifact["corpus_utf8_byte_reservation_bound_per_prompt"]
    byte_bound_total = sum(byte_bound_per_prompt)
    envelope_per_model, envelope_total = {}, Decimal(0)
    for model_id in forecast.MODEL_IDS:
        input_price, output_price = price_for(model_id)
        byte_component = forecast.compute_scenario_component(
            total_input_tokens=byte_bound_total, calls=calls,
            output_tokens_per_call=output_ceilings[model_id],
            input_price=input_price, output_price=output_price,
        )
        value = Decimal(byte_component["total_usd"]) * Decimal(max_attempts)
        envelope_per_model[model_id] = {"total_usd": str(value)}
        envelope_total += value

    halt_cap = Decimal(str(
        protocol["materialization_requirements"]["capability_preflight"]["proposed_cap_usd"]))
    if not stress_total < halt_cap:
        raise AssertionError(
            "fixture prices must be cheap enough to clear the gate: "
            f"stress_total={stress_total}, halt_cap={halt_cap}")

    artifact = deepcopy(conflict_artifact)
    artifact["schema_version"] = forecast.SCHEMA_VERSION_V2
    artifact["artifact_id"] = forecast.ARTIFACT_ID_V2
    artifact["status"] = forecast.STATUS_V2
    del artifact["resolution"]
    artifact["bindings"]["protocol"]["canonical_sha256"] = phase2_plan.canonical_sha256(protocol)
    del artifact["bindings"]["role_limits_v2"]
    artifact["bindings"]["role_limits_v3"] = {
        "tracked_path": forecast.EXPECTED_BINDING_PATHS_V2["role_limits_v3"],
        "canonical_sha256": phase2_plan.canonical_sha256(role_limits_v3),
    }
    artifact["bindings"]["price_snapshot"]["canonical_sha256"] = (
        phase2_plan.canonical_sha256(snapshot))
    artifact["bindings"]["provider_refresh"] = {
        "tracked_path": forecast.EXPECTED_BINDING_PATHS_V2["provider_refresh"],
        "canonical_sha256": phase2_plan.canonical_sha256(provider_refresh),
    }
    artifact["retry_policy"]["max_attempts"] = max_attempts
    artifact["retry_policy"]["max_retries"] = max_retries
    artifact["scenarios"]["theoretical_minimum"]["per_model"] = theo_per_model
    artifact["scenarios"]["theoretical_minimum"]["total_usd"] = str(theo_total)
    artifact["scenarios"]["no_retry_maximum"]["per_model"] = no_retry_per_model
    artifact["scenarios"]["no_retry_maximum"]["total_usd"] = str(no_retry_total)
    artifact["scenarios"]["planning_retry_scenario"]["per_model"] = planning_per_model
    artifact["scenarios"]["planning_retry_scenario"]["total_usd"] = str(planning_total)
    stress_entry = artifact["scenarios"].pop("four_attempt_stress")
    del stress_entry["qwen_3_7_plus_byte_bound_stress_usd"]
    del stress_entry["qwen_3_7_plus_byte_bound_note"]
    stress_entry["multiplier"] = str(max_attempts)
    stress_entry["per_model"] = stress_per_model
    stress_entry["total_usd"] = str(stress_total)
    artifact["scenarios"][forecast.ATTEMPT_CEILING_STRESS_SCENARIO] = stress_entry
    artifact["halt_cap_usd"] = str(halt_cap)
    artifact["stress_margin_usd"] = str(halt_cap - stress_total)
    artifact["disclosures"] = {
        "utf8_reservation_envelope_3_attempts": {
            "formula": "test-fixture UTF-8 reservation envelope formula",
            "attempts": max_attempts,
            "per_model": envelope_per_model,
            "total_usd": str(envelope_total),
            "relationship_to_halt_cap": forecast.FROZEN_RESERVATION_ENVELOPE_SENTENCE,
        },
    }
    artifact["supersedes"] = {
        "tracked_path": forecast.SUPERSEDED_ARTIFACT_TRACKED_PATH,
        "canonical_sha256": phase2_plan.canonical_sha256(conflict_artifact),
        "note": forecast.SUPERSEDES_CONFLICT_NOTE,
    }

    forecast.validate_forecast_v2(
        artifact, root=root, protocol=protocol, role_limits_v3=role_limits_v3,
        snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh,
    )
    return artifact


def _build_ready_forecast_payload_v3(
    *, root: Path, protocol: Mapping, role_limits_v4: Mapping, snapshot: Mapping,
    bundle: Mapping, provider_refresh: Mapping,
) -> dict:
    """Build a genuinely-passing v3 "ready" capability-preflight forecast, offline, from the
    validator's own requirements -- the v4-role-limits sibling of
    :func:`_build_ready_forecast_payload_v2`.

    Reuses the REAL per-model token stats / tokenizer pins / rendered-corpus binding / byte-
    reservation-bound-per-prompt / corpus / caveats from the tracked, real v2 "ready" artifact
    unchanged (those sections are price-independent, and the corpus/protocol/bundle content is
    byte-identical to what produced them), and recomputes every price-sensitive field fresh
    against ``role_limits_v4``'s retry-attempt count and ``snapshot``'s prices -- mirrors
    ``scripts/build_phase2_preflight_forecast.py``'s own ``build_forecast_v3``. Raises
    ``AssertionError`` (not a production exception) if the given ``role_limits_v4``/``snapshot``
    combination is too expensive to clear ``halt_cap_usd`` -- this helper never fabricates a
    passing artifact.
    """
    v2_artifact = json.loads(READY_ARTIFACT_V2_PATH.read_text(encoding="utf-8"))
    calls = capability_corpus.EXPECTED_ENTRY_COUNT
    output_ceilings = {
        model_id: role_limits.resolve_request_parameters(
            role_limits_v4, protocol, model_id, "capability_qa").effective_max_tokens
        for model_id in forecast.MODEL_IDS
    }

    def price_for(model_id):
        entry = snapshot["models"][model_id]
        return (
            Decimal(str(entry["input_usd_per_million_tokens"])),
            Decimal(str(entry["output_usd_per_million_tokens"])),
        )

    theo_per_model, no_retry_per_model = {}, {}
    theo_total = no_retry_total = Decimal(0)
    no_retry_component_usd = {}
    for model_id in forecast.MODEL_IDS:
        total_input_tokens = v2_artifact["per_model_token_stats"][model_id][
            "input_tokens"]["total"]
        input_price, output_price = price_for(model_id)
        theo = forecast.compute_scenario_component(
            total_input_tokens=total_input_tokens, calls=calls,
            output_tokens_per_call=forecast.THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL,
            input_price=input_price, output_price=output_price,
        )
        theo_per_model[model_id] = theo
        theo_total += Decimal(theo["total_usd"])
        no_retry = forecast.compute_scenario_component(
            total_input_tokens=total_input_tokens, calls=calls,
            output_tokens_per_call=output_ceilings[model_id],
            input_price=input_price, output_price=output_price,
        )
        no_retry_per_model[model_id] = no_retry
        no_retry_component_usd[model_id] = Decimal(no_retry["total_usd"])
        no_retry_total += Decimal(no_retry["total_usd"])

    max_attempts = int(role_limits_v4["request_settings"]["transport"]["max_attempts"])
    max_retries = int(role_limits_v4["request_settings"]["transport"]["max_retries"])

    def derived(multiplier):
        per_model, total = {}, Decimal(0)
        for model_id in forecast.MODEL_IDS:
            value = no_retry_component_usd[model_id] * multiplier
            per_model[model_id] = {"total_usd": str(value)}
            total += value
        return per_model, total

    planning_per_model, planning_total = derived(forecast.PLANNING_RETRY_MULTIPLIER)
    stress_per_model, stress_total = derived(Decimal(max_attempts))

    byte_bound_per_prompt = v2_artifact["corpus_utf8_byte_reservation_bound_per_prompt"]
    byte_bound_total = sum(byte_bound_per_prompt)
    envelope_per_model, envelope_total = {}, Decimal(0)
    for model_id in forecast.MODEL_IDS:
        input_price, output_price = price_for(model_id)
        byte_component = forecast.compute_scenario_component(
            total_input_tokens=byte_bound_total, calls=calls,
            output_tokens_per_call=output_ceilings[model_id],
            input_price=input_price, output_price=output_price,
        )
        value = Decimal(byte_component["total_usd"]) * Decimal(max_attempts)
        envelope_per_model[model_id] = {"total_usd": str(value)}
        envelope_total += value

    halt_cap = Decimal(str(
        protocol["materialization_requirements"]["capability_preflight"]["proposed_cap_usd"]))
    if not stress_total < halt_cap:
        raise AssertionError(
            "fixture prices must be cheap enough to clear the gate: "
            f"stress_total={stress_total}, halt_cap={halt_cap}")

    artifact = deepcopy(v2_artifact)
    artifact["schema_version"] = forecast.SCHEMA_VERSION_V3
    artifact["artifact_id"] = forecast.ARTIFACT_ID_V3
    artifact["status"] = forecast.STATUS_V3
    artifact["bindings"]["protocol"]["canonical_sha256"] = phase2_plan.canonical_sha256(protocol)
    del artifact["bindings"]["role_limits_v3"]
    artifact["bindings"]["role_limits_v4"] = {
        "tracked_path": forecast.EXPECTED_BINDING_PATHS_V3["role_limits_v4"],
        "canonical_sha256": phase2_plan.canonical_sha256(role_limits_v4),
    }
    artifact["bindings"]["price_snapshot"]["canonical_sha256"] = (
        phase2_plan.canonical_sha256(snapshot))
    artifact["bindings"]["provider_refresh"] = {
        "tracked_path": forecast.EXPECTED_BINDING_PATHS_V3["provider_refresh"],
        "canonical_sha256": phase2_plan.canonical_sha256(provider_refresh),
    }
    artifact["retry_policy"]["max_attempts"] = max_attempts
    artifact["retry_policy"]["max_retries"] = max_retries
    artifact["scenarios"]["theoretical_minimum"]["per_model"] = theo_per_model
    artifact["scenarios"]["theoretical_minimum"]["total_usd"] = str(theo_total)
    artifact["scenarios"]["no_retry_maximum"]["per_model"] = no_retry_per_model
    artifact["scenarios"]["no_retry_maximum"]["total_usd"] = str(no_retry_total)
    artifact["scenarios"]["planning_retry_scenario"]["per_model"] = planning_per_model
    artifact["scenarios"]["planning_retry_scenario"]["total_usd"] = str(planning_total)
    stress_entry = artifact["scenarios"][forecast.ATTEMPT_CEILING_STRESS_SCENARIO]
    stress_entry["multiplier"] = str(max_attempts)
    stress_entry["per_model"] = stress_per_model
    stress_entry["total_usd"] = str(stress_total)
    artifact["halt_cap_usd"] = str(halt_cap)
    artifact["stress_margin_usd"] = str(halt_cap - stress_total)
    artifact["disclosures"] = {
        "utf8_reservation_envelope_3_attempts": {
            "formula": "test-fixture UTF-8 reservation envelope formula",
            "attempts": max_attempts,
            "per_model": envelope_per_model,
            "total_usd": str(envelope_total),
            "relationship_to_halt_cap": forecast.FROZEN_RESERVATION_ENVELOPE_SENTENCE,
        },
    }
    artifact["supersedes"] = {
        "tracked_path": forecast.SUPERSEDED_ARTIFACT_TRACKED_PATH_V3,
        "canonical_sha256": phase2_plan.canonical_sha256(v2_artifact),
        "note": forecast.SUPERSEDES_V2_FORECAST_NOTE,
    }

    forecast.validate_forecast_v3(
        artifact, root=root, protocol=protocol, role_limits_v4=role_limits_v4,
        snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh,
    )
    return artifact


# --- shared fixtures: build the real, valid pieces once per module -----------------------------


ROLE_LIMITS_V1_PATH = GREEN_ROOT / "rejudge" / "phase2_role_limits_2026-07-18.json"
ROLE_LIMITS_V2_PATH = GREEN_ROOT / "rejudge" / "phase2_role_limits_v2_2026-07-18.json"
ROLE_LIMITS_V3_PATH = ROOT / "rejudge" / "phase2_role_limits_v3_2026-07-19.json"
ROLE_LIMITS_V4_PATH = ROOT / "rejudge" / "phase2_role_limits_v4_2026-07-19.json"
ROLE_LIMITS_V5_PATH = ROOT / "rejudge" / "phase2_role_limits_v5_2026-07-19.json"


@pytest.fixture(scope="module")
def synthetic_artifacts(tmp_path_factory):
    """Paths for the artifacts that materialization has not produced yet, plus the pinned
    2026-07-19 sealed artifacts (already real, tracked files inside GREEN_ROOT).

    ``role_limits_and_request_settings`` is an absolute tmp path so it can be bound from any
    ``project_root`` without writing anything under GREEN_ROOT itself -- the
    deliberately-anywhere pattern ``_resolve_bound_path`` documents for that binding. It is a
    real, byte-identical copy of the tracked v5 role-limits artifact (not a placeholder): the
    merged manifest slot must VALIDATE as a v5 role-limits artifact, not just hash-match.

    ``cost_forecast`` is a structurally-valid-JSON placeholder: the ``manifest`` fixture stubs
    out cost_forecast's deep economics check (see ``_build_green_root``'s docstring), so this
    only needs to be a real, hash-matchable JSON object here, not a genuine ready forecast.
    ``storage_policy`` is frozen as always relative to project_root (see
    ``_bind_json_artifact_checked``), so it is written directly into GREEN_ROOT (a throwaway
    directory, safe to mutate) with real, semantically valid content: a byte-identical copy of
    the real tracked storage policy.

    ``gemma_closure``, ``provider_refresh``, and ``provider_reconciliation_evidence`` are PINNED
    slots: GREEN_ROOT already carries real, byte-identical copies of their one frozen,
    git-tracked location, so no further materialization is needed for those three.
    """
    directory = tmp_path_factory.mktemp("phase2_execution_artifacts")
    role_limits_and_request_settings = _write_json(
        directory, "role_limits_and_request_settings.json",
        json.loads(ROLE_LIMITS_V5_PATH.read_text(encoding="utf-8")))

    root_relative = _write_synthetic_root_relative_artifacts(GREEN_ROOT)
    root_relative["storage_policy"].write_text(
        STORAGE_POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    yield {
        "role_limits_and_request_settings": role_limits_and_request_settings,
        "gemma_closure": GREEN_ROOT / "rejudge" / "gemma_recovery_closure_2026-07-19.json",
        "provider_refresh": GREEN_ROOT / "rejudge" / "phase2_provider_refresh_2026-07-19.json",
        "provider_reconciliation_evidence": (
            GREEN_ROOT / "rejudge" / "phase2_provider_reconciliation_2026-07-19.json"),
        "prior_attempt_closure": (
            GREEN_ROOT / "rejudge" / "phase2_preflight_abort_closure_2026-07-19.json"),
        # r3 stage-family bindings: the real, committed r2-incident triple + the r2 closure
        # itself (the second prior_attempt_closure list entry) -- all real, git-tracked
        # artifacts that DATA_GLOB_PATTERNS' "rejudge/*.json" glob already mirrors byte-for-byte
        # into GREEN_ROOT.
        "r2_closure": GREEN_ROOT / "rejudge" / "phase2_preflight_r2_closure_2026-07-19.json",
        "carryforward_artifact": (
            GREEN_ROOT / "rejudge" / "phase2_preflight_carryforward_2026-07-19.json"),
        "stage_family_ledger_artifact": (
            GREEN_ROOT / "rejudge" / "phase2_stage_family_ledger_2026-07-19.json"),
        "ceiling_correction_artifact": (
            GREEN_ROOT / "rejudge" / "phase2_ceiling_correction_2026-07-19.json"),
        **root_relative,
    }


@pytest.fixture(scope="module")
def baseline(synthetic_artifacts):
    protocol = phase2_plan.load_protocol(GREEN_PROTOCOL_PATH)
    main_ids = phase2_plan.load_main_question_ids(protocol, GREEN_ROOT)
    cells = phase2_plan.enumerate_cells(protocol, main_ids)
    capability_cells = [cell for cell in cells if cell["kind"] == "capability_qa"]
    planning_keys = sorted(cell["cell_key"] for cell in capability_cells)
    cells_by_key = {cell["cell_key"]: cell for cell in capability_cells}

    combined = json.loads(COMBINED_AI_AUDIT_PATH.read_text(encoding="utf-8"))
    amendment = json.loads(A1_AMENDMENT_PATH.read_text(encoding="utf-8"))
    bundle, _bundle_protocol = prompt_bundle.load_and_validate(
        GREEN_ROOT / "rejudge" / "phase2_prompt_bundle.json", GREEN_PROTOCOL_PATH)
    approval = json.loads(GREEN_PROMPT_BUNDLE_APPROVAL_PATH.read_text(encoding="utf-8"))
    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(
        GREEN_PRICE_SNAPSHOT_PATH, GREEN_PROTOCOL_PATH)
    uv_lock_sha256 = hashlib.sha256(GREEN_UV_LOCK_PATH.read_bytes()).hexdigest()
    approval_basis_sha256 = hashlib.sha256(APPROVAL_BASIS_PATH.read_bytes()).hexdigest()

    # r3 shape: provider_call_inventory excludes the one carried-forward planning cell (the
    # successful r2 Qwen call) and marks the one replacement planning cell (the r2 closure's
    # closed-ambiguous Gemma cell) with CALL_ENTRY_REPLACEMENT_MARKER_KEY=True.
    carried_forward_key = phase2_stage_family.QWEN_PLANNING_CELL_KEY
    replacement_key = phase2_stage_family.GEMMA_PLANNING_CELL_KEY
    assert carried_forward_key in cells_by_key, "fixture drift: qwen planning cell not found"
    assert replacement_key in cells_by_key, "fixture drift: gemma planning cell not found"
    provider_call_planning_keys = [k for k in planning_keys if k != carried_forward_key]

    entries_without_key = []
    for index, key in enumerate(provider_call_planning_keys):
        cell = cells_by_key[key]
        side = "A" if cell["replicate_index"] == 0 else "B"
        entry = {
            "planning_cell_key": key,
            "call_role": "capability_qa",
            "call_index": index,
            "model": cell["judge_model"],
            "seed": index,
            "side": side,
            "request_fields_sha256": "a" * 64,
        }
        if key == replacement_key:
            entry[pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY] = True
        entries_without_key.append(entry)

    return {
        "protocol": protocol,
        "planning_keys": planning_keys,
        "entries_without_key": entries_without_key,
        "combined": combined,
        "amendment": amendment,
        "bundle": bundle,
        "approval": approval,
        "snapshot": snapshot,
        "uv_lock_sha256": uv_lock_sha256,
        "approval_basis_sha256": approval_basis_sha256,
    }


def _artifact_binding(artifacts, name: str) -> dict:
    path = artifacts[name]
    for base in (GREEN_ROOT, ROOT):
        try:
            path_value = str(path.relative_to(base).as_posix())
            break
        except ValueError:
            continue
    else:
        path_value = str(path)
    return {"path": path_value, "sha256": _canon_sha(path)}


def _shared_manifest_fields(baseline, artifacts, *, stage, stage_cap, cumulative_cap):
    protocol = baseline["protocol"]
    schema_version = protocol["materialization_requirements"]["transition_model"][
        "manifest_schema_version"]
    execution_semantics = protocol["decisions"]["execution_semantics"]
    return {
        "schema_version": schema_version,
        "stage": stage,
        "protocol_canonical_sha256": phase2_plan.canonical_sha256(protocol),
        "a1_amendment_canonical_sha256": phase2_plan.canonical_sha256(baseline["amendment"]),
        "combined_ai_audit_canonical_sha256": phase2_plan.canonical_sha256(baseline["combined"]),
        "question_bank_bundle_sha256": protocol["source_bindings"]["question_bank_bundle_sha256"],
        "prompt_bundle_canonical_sha256": phase2_plan.canonical_sha256(baseline["bundle"]),
        "prompt_bundle_declared_status": baseline["bundle"]["status"],
        "prompt_bundle_approval_tracked_path": str(
            GREEN_PROMPT_BUNDLE_APPROVAL_PATH.relative_to(GREEN_ROOT).as_posix()),
        "prompt_bundle_approval_canonical_sha256": phase2_plan.canonical_sha256(
            baseline["approval"]),
        "role_limits_and_request_settings_artifact": _artifact_binding(
            artifacts, "role_limits_and_request_settings"),
        "provider_price_snapshot_canonical_sha256": phase2_plan.canonical_sha256(
            baseline["snapshot"]),
        "uv_lock_sha256": baseline["uv_lock_sha256"],
        "seed_policy": execution_semantics["seed_policy"],
        "side_assignment_policy": execution_semantics["side_assignment_policy"],
        "satisfied_prerequisites": {
            "gemma_recovery_or_waiver": _artifact_binding(artifacts, "gemma_closure"),
        },
        "ledger": dict(LEDGER_BINDING),
        "stage_cap_usd": stage_cap,
        "cumulative_cap_usd": cumulative_cap,
        "cost_forecast": _artifact_binding(artifacts, "cost_forecast"),
        "storage_policy": _artifact_binding(artifacts, "storage_policy"),
        "provider_reconciliation_evidence": _artifact_binding(
            artifacts, "provider_reconciliation_evidence"),
        "provider_refresh": _artifact_binding(artifacts, "provider_refresh"),
        "prior_attempt_closure": [
            _artifact_binding(artifacts, "prior_attempt_closure"),
            _artifact_binding(artifacts, "r2_closure"),
        ],
        "implementation_provenance": dict(IMPLEMENTATION_PROVENANCE_BINDING),
        "carryforward_artifact": _artifact_binding(artifacts, "carryforward_artifact"),
        "stage_family_ledger_artifact": _artifact_binding(
            artifacts, "stage_family_ledger_artifact"),
        "ceiling_correction_artifact": _artifact_binding(
            artifacts, "ceiling_correction_artifact"),
        "attempt_available_cap_usd": phase2_stage_family.R3_AVAILABLE_CAP_USD,
        "expected_provider_call_count": pe.EXPECTED_PROVIDER_CALL_COUNT,
    }


def build_manifest(
    baseline, artifacts, *, stage="capability_preflight", stage_cap=STAGE_CAP_USD,
    cumulative_cap=CUMULATIVE_CAP_USD,
):
    """Build a fresh, fully self-consistent execution manifest and its identity hash.

    Uses the module's own :func:`build_execution_identity` / :func:`derive_execution_call_key`
    to compute the identity and every call key, so this is exactly the derivation
    :func:`validate_execution_manifest` will independently repeat and compare against.
    """
    shared = _shared_manifest_fields(
        baseline, artifacts, stage=stage, stage_cap=stage_cap, cumulative_cap=cumulative_cap)
    identity = pe.build_execution_identity(
        schema_version=shared["schema_version"],
        stage=shared["stage"],
        protocol_canonical_sha256=shared["protocol_canonical_sha256"],
        a1_amendment_canonical_sha256=shared["a1_amendment_canonical_sha256"],
        combined_ai_audit_canonical_sha256=shared["combined_ai_audit_canonical_sha256"],
        question_bank_bundle_sha256=shared["question_bank_bundle_sha256"],
        prompt_bundle_canonical_sha256=shared["prompt_bundle_canonical_sha256"],
        prompt_bundle_declared_status=shared["prompt_bundle_declared_status"],
        prompt_bundle_approval_artifact={
            "tracked_path": shared["prompt_bundle_approval_tracked_path"],
            "sha256": shared["prompt_bundle_approval_canonical_sha256"],
        },
        role_limits_and_request_settings_artifact=shared[
            "role_limits_and_request_settings_artifact"],
        provider_price_snapshot_canonical_sha256=shared[
            "provider_price_snapshot_canonical_sha256"],
        uv_lock_sha256=shared["uv_lock_sha256"],
        seed_policy=shared["seed_policy"],
        side_assignment_policy=shared["side_assignment_policy"],
        satisfied_prerequisites=shared["satisfied_prerequisites"],
        ledger=shared["ledger"],
        planning_cell_keys=baseline["planning_keys"],
        provider_call_inventory_entries=baseline["entries_without_key"],
        stage_cap_usd=shared["stage_cap_usd"],
        cumulative_cap_usd=shared["cumulative_cap_usd"],
        cost_forecast=shared["cost_forecast"],
        storage_policy=shared["storage_policy"],
        provider_reconciliation_evidence=shared["provider_reconciliation_evidence"],
        provider_refresh=shared["provider_refresh"],
        prior_attempt_closure=shared["prior_attempt_closure"],
        implementation_provenance=shared["implementation_provenance"],
        carryforward_artifact=shared["carryforward_artifact"],
        stage_family_ledger_artifact=shared["stage_family_ledger_artifact"],
        ceiling_correction_artifact=shared["ceiling_correction_artifact"],
        attempt_available_cap_usd=shared["attempt_available_cap_usd"],
    )
    identity_sha256 = pe.derive_execution_identity_sha256(identity)
    entries = [
        {
            **entry,
            "execution_call_key": pe.derive_execution_call_key(
                identity_sha256, planning_cell_key=entry["planning_cell_key"],
                call_role=entry["call_role"], call_index=entry["call_index"],
            ),
        }
        for entry in baseline["entries_without_key"]
    ]
    manifest = {
        **shared,
        "planning_cell_keys": list(baseline["planning_keys"]),
        "provider_call_inventory": entries,
    }
    return manifest, identity_sha256


def matching_authorization(
    identity_sha256, *, stage="capability_preflight",
    stage_cap=STAGE_CAP_USD, cumulative_cap=CUMULATIVE_CAP_USD,
    approval_basis_tracked_path=APPROVAL_BASIS_TRACKED_PATH,
    approval_basis_sha256=APPROVAL_BASIS_SHA256,
    approved_at_utc=pe.PREFLIGHT_DELEGATION_APPROVED_AT_UTC,
    recorded_at_utc="2026-07-19T00:55:00Z",
    approver="Jack Maiorino",
):
    return {
        "execution_identity_sha256": identity_sha256,
        "stage": stage,
        "stage_cap_usd": stage_cap,
        "cumulative_cap_usd": cumulative_cap,
        "approver": approver,
        "approved_at_utc": approved_at_utc,
        "recorded_at_utc": recorded_at_utc,
        "approval_basis_tracked_path": approval_basis_tracked_path,
        "approval_basis_sha256": approval_basis_sha256,
    }


@pytest.fixture(scope="module")
def corrupted_ai_audit_root(tmp_path_factory):
    destination = tmp_path_factory.mktemp("corrupted_ai_audit_root")
    _copy_tracked_data_files(destination)
    path = destination / "rejudge" / "phase2_resolvability_ai_review.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["summary"]["question_count"] = payload["summary"]["question_count"] + 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    return destination


@pytest.fixture(scope="module")
def corrupted_amendment_root(tmp_path_factory):
    destination = tmp_path_factory.mktemp("corrupted_amendment_root")
    _copy_tracked_data_files(destination)
    path = destination / "rejudge" / "phase2_resolvability_review_amendment_2026-07-16.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["amendment_id"] = "phase2_pooled_hpr_2026_07_16_v1_a2_tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return destination


@pytest.fixture(scope="module")
def corrupted_protocol_root(tmp_path_factory):
    destination = tmp_path_factory.mktemp("corrupted_protocol_root")
    _copy_tracked_data_files(destination)
    path = destination / "rejudge" / "phase2_protocol.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = "tampered_status"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return destination


@pytest.fixture(scope="module")
def corrupted_source_binding_root(tmp_path_factory):
    """Tamper a tracked source file that ``validate_source_bindings`` re-hashes.

    ``phase2_protocol.json`` itself is left untouched, so ``load_protocol`` (and its
    internal ``validate_protocol``) succeeds; only the independent
    ``phase2_plan.validate_source_bindings`` recompute-and-compare catches the drift.
    """
    destination = tmp_path_factory.mktemp("corrupted_source_binding_root")
    _copy_tracked_data_files(destination)
    path = destination / "rejudge" / "oracle_shortcut_audit_2026-07-12.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[0]["wrong_answer"] = "TAMPERED"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return destination


@pytest.fixture(scope="module")
def corrupted_prompt_bundle_root(tmp_path_factory):
    destination = tmp_path_factory.mktemp("corrupted_prompt_bundle_root")
    _copy_tracked_data_files(destination)
    path = destination / "rejudge" / "phase2_prompt_bundle.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["bundle_id"] = "tampered_bundle_id"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return destination


@pytest.fixture(scope="module")
def corrupted_price_snapshot_root(tmp_path_factory):
    destination = tmp_path_factory.mktemp("corrupted_price_snapshot_root")
    _copy_tracked_data_files(destination)
    path = destination / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["provider"] = "Tampered Provider"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return destination


@pytest.fixture(scope="module")
def missing_approval_root(tmp_path_factory):
    """A tmp copy of the tracked data files with the approval artifact itself removed."""
    destination = tmp_path_factory.mktemp("missing_approval_root")
    _copy_tracked_data_files(destination)
    path = destination / "rejudge" / "phase2_prompt_bundle_approval_2026-07-18.json"
    path.unlink()
    return destination


@pytest.fixture(scope="module")
def missing_v4_role_limits_root(tmp_path_factory):
    """A tmp copy of the tracked data files with the frozen v4 role-limits file removed.

    The manifest's role_limits_and_request_settings_artifact binding itself (an absolute tmp
    path via ``synthetic_artifacts``) is untouched; only the separate, always-project-root-
    relative v4 "supersedes" source that role_limits.validate_role_limits_v5 independently
    re-reads from disk is missing here.
    """
    destination = tmp_path_factory.mktemp("missing_v4_role_limits_root")
    _copy_tracked_data_files(destination)
    path = destination / "rejudge" / "phase2_role_limits_v4_2026-07-19.json"
    path.unlink()
    return destination


@pytest.fixture(scope="module")
def valid_manifest(baseline, synthetic_artifacts):
    return build_manifest(baseline, synthetic_artifacts)


@pytest.fixture
def manifest(valid_manifest, monkeypatch):
    """A fresh mutable deep copy of the valid baseline manifest for each test.

    Stubs cost_forecast's deep economics check (``phase2_preflight_forecast.validate_forecast_v3``,
    called from ``pe._validate_cost_forecast_gate``) to a no-op, so every OTHER manifest-field
    test below stays decoupled from the forecast's own economics and doesn't need to construct a
    real "ready" forecast. Every other check on cost_forecast (the generic path/hash binding) and
    on storage_policy/provider_refresh/gemma/provider_reconciliation/prior_attempt_closure/
    role-limits v4/authorization/etc. still runs for real, unpatched. The REAL, unpatched
    ``validate_forecast_v3`` behavior (READY acceptance, gate-direction rejection, the v4
    role-limits + provider-refresh binding) is exercised directly by the dedicated cost_forecast
    gate tests further down.
    """
    monkeypatch.setattr(pe.preflight_forecast, "validate_forecast_v3", lambda *a, **k: None)
    manifest, identity_sha256 = valid_manifest
    return deepcopy(manifest), identity_sha256


# --- happy path (structural validation; require_authorized defaults to False) ------------------


def test_valid_manifest_validates_and_derives_the_expected_identity(manifest):
    manifest_dict, identity_sha256 = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)
    assert validated.stage == "capability_preflight"
    assert validated.execution_identity_sha256 == identity_sha256
    assert len(validated.provider_call_inventory) == pe.EXPECTED_PROVIDER_CALL_COUNT
    assert len(validated.planning_cell_keys) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert validated.authorized is False
    assert validated.authorization is None
    assert validated.stage_cap_usd == STAGE_CAP_USD
    assert validated.cumulative_cap_usd == CUMULATIVE_CAP_USD
    assert validated.attempt_available_cap_usd == Decimal(phase2_stage_family.R3_AVAILABLE_CAP_USD)


def test_call_inventory_is_unique_and_bijective_with_planning_cells(manifest):
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)
    call_keys = {entry["execution_call_key"] for entry in validated.provider_call_inventory}
    planning_keys = {
        entry["planning_cell_key"] for entry in validated.provider_call_inventory
    }
    assert len(call_keys) == pe.EXPECTED_PROVIDER_CALL_COUNT
    assert planning_keys == (
        set(validated.planning_cell_keys) - {phase2_stage_family.QWEN_PLANNING_CELL_KEY})


def test_dataclasses_are_frozen(manifest):
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)
    with pytest.raises(FrozenInstanceError):
        setattr(validated, "stage", "canary")
    audit = pe.audit_resume(validated, output_rows=[], usage_events=[])
    with pytest.raises(FrozenInstanceError):
        setattr(audit, "stage", "canary")


# --- top-level manifest structure ---------------------------------------------------------------


def test_non_dict_manifest_is_rejected():
    with pytest.raises(pe.ManifestValidationError, match="must be an object"):
        pe.validate_execution_manifest(cast(Any, []), project_root=GREEN_ROOT)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_top_level_key_drift_is_rejected(manifest, mutation):
    manifest_dict, _identity = manifest
    if mutation == "missing":
        del manifest_dict["stage_cap_usd"]
    else:
        manifest_dict["unexpected_field"] = "x"
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_wrong_schema_version_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["schema_version"] = "phase2_execution_manifest_v0"
    with pytest.raises(pe.ManifestValidationError, match="unsupported execution manifest"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_unrecognized_stage_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["stage"] = "not_a_real_stage"
    with pytest.raises(pe.ManifestValidationError, match="unrecognized execution stage"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("stage", ["gemma_recovery_or_waiver", "canary", "main"])
def test_unsupported_stages_raise_unconditionally(baseline, synthetic_artifacts, stage):
    manifest_dict, _identity = build_manifest(baseline, synthetic_artifacts, stage=stage)
    with pytest.raises(pe.UnsupportedStageError, match=stage):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- hash drift for every bound artifact ---------------------------------------------------------


@pytest.mark.parametrize("field", [
    "protocol_canonical_sha256",
    "a1_amendment_canonical_sha256",
    "combined_ai_audit_canonical_sha256",
    "prompt_bundle_canonical_sha256",
    "provider_price_snapshot_canonical_sha256",
    "uv_lock_sha256",
])
def test_top_level_hash_drift_is_rejected(manifest, field):
    manifest_dict, _identity = manifest
    manifest_dict[field] = _flip_hex_digest(manifest_dict[field])
    with pytest.raises(pe.ManifestValidationError, match="hash drift"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("field", [
    "role_limits_and_request_settings_artifact",
    "cost_forecast", "storage_policy", "provider_reconciliation_evidence",
])
def test_artifact_binding_hash_drift_is_rejected(manifest, field):
    manifest_dict, _identity = manifest
    manifest_dict[field]["sha256"] = _flip_hex_digest(manifest_dict[field]["sha256"])
    with pytest.raises(pe.ManifestValidationError, match="hash drift"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_artifact_binding_missing_file_fails_closed(manifest, tmp_path):
    # This binding predates the repo-relative containment check and is deliberately bindable
    # from an absolute path anywhere (e.g. a materialization-pending tmp copy).
    manifest_dict, _identity = manifest
    missing = tmp_path / "does_not_exist.json"
    manifest_dict["role_limits_and_request_settings_artifact"] = {
        "path": str(missing), "sha256": "a" * 64}
    with pytest.raises(pe.ManifestValidationError, match="artifact is missing"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- role_limits_and_request_settings_artifact: merged-slot-specific behavior ------------------


def test_role_limits_and_request_settings_artifact_v1_in_slot_is_rejected(manifest, tmp_path):
    # A v1 artifact byte-identical to the real, tracked v1 file is a real, hash-matchable JSON
    # object -- it must still fail because it lacks the v5-only sections (approval_basis,
    # sdk_compatibility_basis, role_taxonomy, the restructured transport section) and its
    # schema_version is phase2_role_limits_v1, not v5.
    manifest_dict, _identity = manifest
    v1_payload = json.loads(ROLE_LIMITS_V1_PATH.read_text(encoding="utf-8"))
    v1_copy = tmp_path / "v1_in_v5_slot.json"
    v1_copy.write_text(json.dumps(v1_payload), encoding="utf-8")
    manifest_dict["role_limits_and_request_settings_artifact"] = {
        "path": str(v1_copy), "sha256": phase2_plan.canonical_sha256(v1_payload),
    }
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not validate as a v5 role-limits artifact",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_and_request_settings_artifact_v2_in_slot_is_rejected(manifest, tmp_path):
    # Same guarantee one link up the chain: a real, byte-identical v2 artifact still fails
    # closed in the merged slot, since v2 lacks v5's sections and its schema_version is
    # phase2_role_limits_v2, not v5.
    manifest_dict, _identity = manifest
    v2_payload = json.loads(ROLE_LIMITS_V2_PATH.read_text(encoding="utf-8"))
    v2_copy = tmp_path / "v2_in_v5_slot.json"
    v2_copy.write_text(json.dumps(v2_payload), encoding="utf-8")
    manifest_dict["role_limits_and_request_settings_artifact"] = {
        "path": str(v2_copy), "sha256": phase2_plan.canonical_sha256(v2_payload),
    }
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not validate as a v5 role-limits artifact",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_and_request_settings_artifact_v3_in_slot_is_rejected(manifest, tmp_path):
    # v3 is now retired from the merged slot exactly like v2/v1: it lacks v5's sections and its
    # schema_version is phase2_role_limits_v3, not v5.
    manifest_dict, _identity = manifest
    v3_payload = json.loads(ROLE_LIMITS_V3_PATH.read_text(encoding="utf-8"))
    v3_copy = tmp_path / "v3_in_v5_slot.json"
    v3_copy.write_text(json.dumps(v3_payload), encoding="utf-8")
    manifest_dict["role_limits_and_request_settings_artifact"] = {
        "path": str(v3_copy), "sha256": phase2_plan.canonical_sha256(v3_payload),
    }
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not validate as a v5 role-limits artifact",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_and_request_settings_artifact_v4_in_slot_is_rejected(manifest, tmp_path):
    # v4 is now retired from the merged slot too: it lacks v5's restructured
    # request_settings.transport section (sdk_internal_max_retries/ledger_max_retries/
    # ledger_max_attempts/http_timeout/per_call_wall_clock_ceiling_seconds) and its
    # schema_version is phase2_role_limits_v4, not v5 -- this is the v5 transport-hardening
    # fix's own regression guard, proving the slot can never silently fall back to the
    # artifact that lacked the pinned SDK timeout/retry settings behind the real 2026-07-19 r2
    # relaunch's ambiguous-charge halt.
    manifest_dict, _identity = manifest
    v4_payload = json.loads(ROLE_LIMITS_V4_PATH.read_text(encoding="utf-8"))
    v4_copy = tmp_path / "v4_in_v5_slot.json"
    v4_copy.write_text(json.dumps(v4_payload), encoding="utf-8")
    manifest_dict["role_limits_and_request_settings_artifact"] = {
        "path": str(v4_copy), "sha256": phase2_plan.canonical_sha256(v4_payload),
    }
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not validate as a v5 role-limits artifact",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_and_request_settings_artifact_supersedes_drift_is_rejected(
    manifest, tmp_path,
):
    manifest_dict, _identity = manifest
    payload = json.loads(ROLE_LIMITS_V5_PATH.read_text(encoding="utf-8"))
    payload["supersedes"]["canonical_sha256"] = _flip_hex_digest(
        payload["supersedes"]["canonical_sha256"])
    tampered = tmp_path / "tampered_v5.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    manifest_dict["role_limits_and_request_settings_artifact"] = {
        "path": str(tampered), "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not validate as a v5 role-limits artifact",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("stray_key", [
    "per_model_role_limits_artifact", "provider_request_fields_artifact",
])
def test_old_two_slot_keys_are_rejected_even_alongside_the_new_key(manifest, stray_key):
    # Exact key-set checks make the manifest fail closed whether an old key is a leftover
    # extra field alongside the new merged key, or (in the next test) a full reversion.
    manifest_dict, _identity = manifest
    manifest_dict[stray_key] = dict(manifest_dict["role_limits_and_request_settings_artifact"])
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_reverting_to_the_old_two_slot_manifest_shape_is_rejected(manifest):
    manifest_dict, _identity = manifest
    binding = manifest_dict.pop("role_limits_and_request_settings_artifact")
    manifest_dict["per_model_role_limits_artifact"] = binding
    manifest_dict["provider_request_fields_artifact"] = dict(binding)
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_and_request_settings_artifact_key_set_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["role_limits_and_request_settings_artifact"]["extra"] = "x"
    with pytest.raises(
        pe.ManifestValidationError, match="role_limits_and_request_settings_artifact",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_and_request_settings_artifact_missing_key_is_rejected(manifest):
    # Distinct from the extra-key test above: this exercises the missing-key side of the
    # same _exact_keys guard at this call site (deleting "sha256" rather than adding a key).
    manifest_dict, _identity = manifest
    del manifest_dict["role_limits_and_request_settings_artifact"]["sha256"]
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_and_request_settings_artifact_non_string_path_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["role_limits_and_request_settings_artifact"]["path"] = 12345
    with pytest.raises(
        pe.ManifestValidationError,
        match=r"role_limits_and_request_settings_artifact\.path",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_and_request_settings_artifact_malformed_sha_format_is_rejected(manifest):
    # Distinct from test_artifact_binding_hash_drift_is_rejected, which keeps the sha
    # well-formed (still 64 hex chars) and only exercises the later hash-mismatch branch:
    # this exercises the earlier format guard (_sha256_hex) at this specific call site.
    manifest_dict, _identity = manifest
    manifest_dict["role_limits_and_request_settings_artifact"]["sha256"] = "not-64-hex"
    with pytest.raises(
        pe.ManifestValidationError,
        match=r"role_limits_and_request_settings_artifact\.sha256",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_role_limits_v4_supersedes_source_missing_fails_closed(
    manifest, missing_v4_role_limits_root,
):
    manifest_dict, _identity = manifest
    with pytest.raises(
        pe.ManifestValidationError, match="supersedes source is missing",
    ):
        pe.validate_execution_manifest(
            manifest_dict, project_root=missing_v4_role_limits_root)


@pytest.mark.parametrize("field", ["cost_forecast", "storage_policy"])
def test_new_binding_missing_file_fails_closed(manifest, field):
    # Unlike the pair above, these are frozen as always relative to project_root, so the
    # missing-file probe must itself be a non-escaping relative path. (Unlike these two,
    # provider_reconciliation_evidence is now PINNED to one exact location -- see
    # test_provider_reconciliation_evidence_missing_file_fails_closed below -- so a
    # syntactically-fine-but-unpinned path fails the pinning check first, not this one.)
    manifest_dict, _identity = manifest
    manifest_dict[field] = {
        "path": f"{SYNTHETIC_ROOT_RELATIVE_DIR}/does_not_exist.json", "sha256": "a" * 64,
    }
    with pytest.raises(pe.ManifestValidationError, match="artifact is missing"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("field", ["cost_forecast", "storage_policy",
                                    "provider_reconciliation_evidence"])
def test_new_binding_relative_path_escape_fails_closed(manifest, field):
    manifest_dict, _identity = manifest
    manifest_dict[field] = {
        "path": "../outside_the_repo_root.json", "sha256": "a" * 64,
    }
    with pytest.raises(pe.ManifestValidationError, match="escapes the repository root"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("field", ["cost_forecast", "storage_policy",
                                    "provider_reconciliation_evidence"])
def test_new_binding_absolute_path_outside_root_fails_closed(manifest, tmp_path, field):
    # Regression for the finding: an absolute path to a real, byte-identical, but untracked
    # file outside project_root must not be silently accepted just because its content and
    # declared hash agree.
    manifest_dict, _identity = manifest
    payload = {"x": "an untracked, self-authored copy"}
    outside = tmp_path / "outside_artifact.json"
    outside.write_text(json.dumps(payload), encoding="utf-8")
    manifest_dict[field] = {
        "path": str(outside), "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(pe.ManifestValidationError, match="must be a path relative to"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("field", ["cost_forecast", "storage_policy",
                                    "provider_reconciliation_evidence",
                                    "role_limits_and_request_settings_artifact"])
def test_new_binding_non_mapping_value_is_rejected(manifest, field):
    manifest_dict, _identity = manifest
    manifest_dict[field] = "not a mapping"
    with pytest.raises(pe.ManifestValidationError, match="must be an object"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_gemma_recovery_closure_hash_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    binding = manifest_dict["satisfied_prerequisites"]["gemma_recovery_or_waiver"]
    binding["sha256"] = _flip_hex_digest(binding["sha256"])
    with pytest.raises(pe.ManifestValidationError, match="hash drift"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_gemma_recovery_closure_unpinned_absolute_path_fails_closed(manifest, tmp_path):
    # The gemma_recovery_or_waiver slot is now PINNED to the real 2026-07-19 closure record
    # (unlike its predecessor, an absolute path anywhere is no longer accepted at all).
    manifest_dict, _identity = manifest
    missing = tmp_path / "no_closure.json"
    manifest_dict["satisfied_prerequisites"]["gemma_recovery_or_waiver"] = {
        "path": str(missing), "sha256": "b" * 64,
    }
    with pytest.raises(pe.ManifestValidationError, match="must be a path relative to"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_gemma_recovery_closure_unpinned_relative_path_fails_closed(manifest):
    # A syntactically-fine, non-escaping relative path that simply names the wrong file must
    # still be rejected: the location is pinned, not manifest-controlled.
    manifest_dict, _identity = manifest
    manifest_dict["satisfied_prerequisites"]["gemma_recovery_or_waiver"] = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json", "sha256": "b" * 64,
    }
    with pytest.raises(
        pe.ManifestValidationError, match="must resolve to the frozen, git-tracked artifact",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_satisfied_prerequisites_key_set_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["satisfied_prerequisites"]["unexpected_stage"] = {
        "path": "x", "sha256": "c" * 64,
    }
    with pytest.raises(pe.ManifestValidationError, match="satisfied_prerequisites"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- A1 / combined AI audit binding mismatches ----------------------------------------------------


def test_missing_a1_amendment_binding_is_rejected(manifest):
    manifest_dict, _identity = manifest
    del manifest_dict["a1_amendment_canonical_sha256"]
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_combined_ai_audit_that_fails_its_own_validator_is_rejected(
    manifest, corrupted_ai_audit_root,
):
    # Reuse the real, valid manifest unchanged; only project_root points at a copy of the
    # tracked data files with the combined AI-audit artifact corrupted. Everything else
    # (protocol, prompt bundle, price snapshot, uv.lock, the synthetic not-yet-existing
    # artifacts under absolute tmp paths) is untouched, so this isolates the
    # ai_review.validate_combined() catch branch specifically.
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ManifestValidationError, match="bound combined AI audit is invalid"):
        pe.validate_execution_manifest(manifest_dict, project_root=corrupted_ai_audit_root)


def test_a1_amendment_that_fails_its_own_validator_is_rejected(
    manifest, corrupted_amendment_root,
):
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ManifestValidationError, match="bound A1 amendment is invalid"):
        pe.validate_execution_manifest(manifest_dict, project_root=corrupted_amendment_root)


def test_base_protocol_that_fails_its_own_validator_is_rejected(
    manifest, corrupted_protocol_root,
):
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ManifestValidationError, match="bound base protocol is invalid"):
        pe.validate_execution_manifest(manifest_dict, project_root=corrupted_protocol_root)


def test_question_bank_source_bindings_that_fail_their_own_validator_are_rejected(
    manifest, corrupted_source_binding_root,
):
    manifest_dict, _identity = manifest
    with pytest.raises(
        pe.ManifestValidationError, match="question-bank source bindings are invalid",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=corrupted_source_binding_root)


def test_prompt_bundle_that_fails_its_own_validator_is_rejected(
    manifest, corrupted_prompt_bundle_root,
):
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ManifestValidationError, match="bound prompt bundle is invalid"):
        pe.validate_execution_manifest(manifest_dict, project_root=corrupted_prompt_bundle_root)


def test_price_snapshot_that_fails_its_own_validator_is_rejected(
    manifest, corrupted_price_snapshot_root,
):
    manifest_dict, _identity = manifest
    with pytest.raises(
        pe.ManifestValidationError, match="bound provider price snapshot is invalid",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=corrupted_price_snapshot_root)


# --- question bank bundle binding ------------------------------------------------------------------


def test_question_bank_bundle_hash_disagreement_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["question_bank_bundle_sha256"] = _flip_hex_digest(
        manifest_dict["question_bank_bundle_sha256"])
    with pytest.raises(pe.ManifestValidationError, match="question_bank_bundle_sha256"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- prompt bundle: hash + declared status -----------------------------------------------------


def test_prompt_bundle_declared_status_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prompt_bundle_declared_status"] = "owner_approved"
    with pytest.raises(pe.ManifestValidationError, match="prompt_bundle_declared_status"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_candidate_prompt_bundle_with_valid_approval_is_authorized(manifest):
    # Per the frozen governance the bundle file itself never leaves
    # candidate_pending_owner_methods_review; a bound, valid, append-only approval artifact
    # (never itself an execution_authorized=true claim) plus a matching stage authorization
    # (with its own resolvable approval_basis) is what lets this candidate-status manifest
    # actually be authorized now.
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    validated = pe.validate_execution_manifest(
        manifest_dict, project_root=GREEN_ROOT, authorization=authorization, require_authorized=True)
    assert validated.authorized is True
    assert validated.authorization is not None


def test_candidate_prompt_bundle_does_not_block_unauthorized_validation(manifest):
    # require_authorized=False must still succeed: draft manifests must be reviewable even
    # though the tracked bundle stays a permanent candidate, as long as the approval-artifact
    # binding is itself valid.
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)
    assert validated.authorized is False


# --- prompt-bundle owner-methods-approval artifact ----------------------------------------------


def _root_with_mutated_approval(tmp_path: Path, mutate) -> tuple[Path, dict]:
    """Copy the tracked data files into ``tmp_path`` and mutate only the approval JSON."""
    _copy_tracked_data_files(tmp_path)
    path = tmp_path / "rejudge" / "phase2_prompt_bundle_approval_2026-07-18.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path, payload


def _manifest_with_mutated_approval(manifest_dict, tmp_path, mutate):
    """Rebind the manifest's declared approval hash to the mutated artifact's real hash.

    Isolates the *content* check under test: without this rebind, every mutation would
    just trip the final approval-artifact self-consistency hash check instead of the
    specific field check the mutation targets.
    """
    manifest_dict = deepcopy(manifest_dict)
    root, payload = _root_with_mutated_approval(tmp_path, mutate)
    manifest_dict["prompt_bundle_approval_canonical_sha256"] = phase2_plan.canonical_sha256(
        payload)
    return manifest_dict, root


def test_approval_artifact_hash_missing_from_identity_is_impossible_without_a_binding(manifest):
    # The approval artifact's own canonical sha is bound into the execution identity via
    # prompt_bundle_approval_artifact; removing the manifest's declared-hash field entirely
    # is caught by ordinary top-level key-set drift, well before approval content is read.
    manifest_dict, _identity = manifest
    del manifest_dict["prompt_bundle_approval_canonical_sha256"]
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_approval_artifact_binds_into_the_execution_identity(manifest):
    manifest_dict, identity_sha256 = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)
    assert validated.execution_identity_sha256 == identity_sha256
    approval_binding = validated.execution_identity["prompt_bundle_approval_artifact"]
    assert approval_binding["sha256"] == manifest_dict["prompt_bundle_approval_canonical_sha256"]
    assert approval_binding["tracked_path"] == manifest_dict["prompt_bundle_approval_tracked_path"]


def test_approval_declared_hash_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prompt_bundle_approval_canonical_sha256"] = _flip_hex_digest(
        manifest_dict["prompt_bundle_approval_canonical_sha256"])
    with pytest.raises(pe.ManifestValidationError, match="hash drift"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_approval_tracked_path_blank_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prompt_bundle_approval_tracked_path"] = ""
    with pytest.raises(
        pe.ManifestValidationError, match="prompt_bundle_approval_tracked_path",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_approval_tracked_path_non_string_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prompt_bundle_approval_tracked_path"] = 12345
    with pytest.raises(
        pe.ManifestValidationError, match="prompt_bundle_approval_tracked_path",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_approval_canonical_sha256_malformed_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prompt_bundle_approval_canonical_sha256"] = "not-a-64-hex-digest"
    with pytest.raises(
        pe.ManifestValidationError, match="prompt_bundle_approval_canonical_sha256",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_approval_artifact_missing_file_fails_closed(manifest, missing_approval_root):
    # The tracked_path stays the real, frozen, pinned location (see
    # test_approval_tracked_path_must_be_the_frozen_location below); it is project_root itself
    # that lacks the file here, isolating the is_file() check from the pinning check.
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ManifestValidationError, match="artifact is missing"):
        pe.validate_execution_manifest(manifest_dict, project_root=missing_approval_root)


def test_approval_tracked_path_escape_fails_closed(manifest):
    manifest_dict, _identity = manifest
    manifest_dict = deepcopy(manifest_dict)
    manifest_dict["prompt_bundle_approval_tracked_path"] = "../outside_the_repo_root.json"
    with pytest.raises(pe.ManifestValidationError, match="escapes the repository root"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_approval_tracked_path_must_be_the_frozen_location(manifest):
    # A syntactically fine, non-escaping relative path that simply names the wrong file must
    # still be rejected: the approval artifact's location is pinned, not manifest-controlled.
    manifest_dict, _identity = manifest
    manifest_dict = deepcopy(manifest_dict)
    manifest_dict["prompt_bundle_approval_tracked_path"] = (
        "rejudge/phase2_resolvability_ai_review.json")
    with pytest.raises(pe.ManifestValidationError, match="frozen, git-tracked approval"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_approval_tracked_path_absolute_untracked_copy_is_rejected(manifest, tmp_path):
    # Regression for the finding: a byte-identical, self-authored copy of the real approval
    # artifact at an attacker-chosen absolute path, never committed to git, must not be
    # accepted merely because its content satisfies every literal/hash check below.
    manifest_dict, _identity = manifest
    manifest_dict = deepcopy(manifest_dict)
    untracked_copy = tmp_path / "approval_copy.json"
    untracked_copy.write_bytes(PROMPT_BUNDLE_APPROVAL_PATH.read_bytes())
    manifest_dict["prompt_bundle_approval_tracked_path"] = str(untracked_copy)
    with pytest.raises(pe.ManifestValidationError, match="frozen, git-tracked approval"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("mutation", ["missing_key", "extra_key"])
def test_approval_key_set_drift_is_rejected(manifest, tmp_path, mutation):
    def mutate(a):
        if mutation == "missing_key":
            del a["note"]
        else:
            a["unexpected_field"] = "x"

    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(manifest_dict, tmp_path, mutate)
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_schema_version_drift_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("schema_version", "wrong_version"))
    with pytest.raises(pe.ManifestValidationError, match="schema_version"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_id_drift_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("approval_id", "wrong_id"))
    with pytest.raises(pe.ManifestValidationError, match="approval_id"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_protocol_id_drift_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("protocol_id", "wrong_protocol_id"))
    with pytest.raises(pe.ManifestValidationError, match="protocol_id"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_approved_bundle_tracked_path_drift_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path,
        lambda a: a.__setitem__("approved_bundle_tracked_path", "rejudge/some_other_bundle.json"),
    )
    with pytest.raises(
        pe.ManifestValidationError, match="approved_bundle_tracked_path",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_approved_bundle_sha_drift_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path,
        lambda a: a.__setitem__(
            "approved_bundle_canonical_sha256", _flip_hex_digest(
                a["approved_bundle_canonical_sha256"])),
    )
    with pytest.raises(
        pe.ManifestValidationError, match="approved_bundle_canonical_sha256",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


@pytest.mark.parametrize("bad_commit", [
    "abc123",            # 6 chars: too short
    "a" * 41,            # 41 chars: too long
    "ABCDEF1",           # uppercase hex
    "13bd6g3",           # non-hex character
    None,                # JSON null: not a string at all (exercises the isinstance guard)
    123456789,           # int: not a string at all
    ["a", "b", "c", "d", "e", "f", "g"],  # list: not a string at all
])
def test_approval_bundle_commit_must_be_lowercase_hex_in_range(manifest, tmp_path, bad_commit):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path,
        lambda a: a.__setitem__("approved_bundle_commit", bad_commit),
    )
    with pytest.raises(pe.ManifestValidationError, match="approved_bundle_commit"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


@pytest.mark.parametrize("mutation", [
    "reorder", "duplicate", "extra", "missing",
    "not_a_list",       # exercises the isinstance(scope, list) guard
    "null",             # exercises the isinstance(scope, list) guard (JSON null)
    "non_string_item",  # exercises the all(isinstance(item, str) ...) guard
])
def test_approval_scope_drift_is_rejected(manifest, tmp_path, mutation):
    def mutate(a):
        if mutation == "not_a_list":
            a["scope"] = "not a list at all"
            return
        if mutation == "null":
            a["scope"] = None
            return
        scope = list(a["scope"])
        if mutation == "reorder":
            scope[0], scope[1] = scope[1], scope[0]
        elif mutation == "duplicate":
            scope.append(scope[0])
        elif mutation == "extra":
            scope.append("an extra scope item never in the real approval")
        elif mutation == "missing":
            scope.pop()
        elif mutation == "non_string_item":
            scope[0] = 12345
        a["scope"] = scope

    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(manifest_dict, tmp_path, mutate)
    with pytest.raises(pe.ManifestValidationError, match="scope"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_approver_drift_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("approver", "Someone Else"))
    with pytest.raises(pe.ManifestValidationError, match="approver"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_bad_timestamp_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("approved_at_utc", "2026-07-18"))
    with pytest.raises(pe.ManifestValidationError, match="approved_at_utc"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_empty_channel_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("approval_channel", ""))
    with pytest.raises(pe.ManifestValidationError, match="approval_channel"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_channel_wording_drift_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("approval_channel", "a different channel"))
    with pytest.raises(pe.ManifestValidationError, match="approval_channel"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_execution_authorized_true_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("execution_authorized", True))
    with pytest.raises(pe.ManifestValidationError, match="execution_authorized"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_note_drift_is_rejected(manifest, tmp_path):
    manifest_dict, _identity = manifest
    manifest_dict, root = _manifest_with_mutated_approval(
        manifest_dict, tmp_path, lambda a: a.__setitem__("note", "a different note entirely"))
    with pytest.raises(pe.ManifestValidationError, match="note"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


# --- _load_strict_json_object, reachable through the approval artifact's real call site --------
#
# _root_with_mutated_approval always round-trips through json.loads/json.dumps of a real dict,
# so it can never produce duplicate keys, malformed JSON text, a non-object root, or a
# NaN/Infinity constant. These tests write the approval file's raw bytes directly instead, to
# prove _load_strict_json_object's raise branches are actually reachable through
# _validate_prompt_bundle_approval, not just through load_execution_manifest.


def _root_with_raw_approval_text(tmp_path: Path, raw_text: str) -> Path:
    root = _copy_tracked_data_files(tmp_path)
    path = root / "rejudge" / "phase2_prompt_bundle_approval_2026-07-18.json"
    path.write_text(raw_text, encoding="utf-8")
    return root


def test_approval_artifact_malformed_json_fails_closed(manifest, tmp_path):
    manifest_dict, _identity = manifest
    root = _root_with_raw_approval_text(tmp_path, "{not valid json")
    with pytest.raises(pe.ManifestValidationError, match="not valid JSON"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_artifact_non_object_root_fails_closed(manifest, tmp_path):
    manifest_dict, _identity = manifest
    root = _root_with_raw_approval_text(tmp_path, "[1, 2, 3]")
    with pytest.raises(pe.ManifestValidationError, match="must contain a JSON object"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_artifact_duplicate_keys_fails_closed(manifest, tmp_path):
    manifest_dict, _identity = manifest
    root = _root_with_raw_approval_text(
        tmp_path, '{"schema_version": "a", "schema_version": "b"}')
    with pytest.raises(pe.ManifestValidationError, match="duplicate key"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_approval_artifact_non_finite_constant_fails_closed(manifest, tmp_path):
    manifest_dict, _identity = manifest
    root = _root_with_raw_approval_text(tmp_path, '{"schema_version": NaN}')
    with pytest.raises(pe.ManifestValidationError, match="non-finite constant"):
        pe.validate_execution_manifest(manifest_dict, project_root=root)


def test_load_strict_json_object_rejects_unreadable_path(tmp_path):
    with pytest.raises(pe.ManifestValidationError, match="could not read"):
        pe._load_strict_json_object(tmp_path / "does-not-exist.json")


def test_load_strict_json_object_rejects_non_object_payload(tmp_path):
    path = tmp_path / "array.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(pe.ManifestValidationError, match="must contain a JSON object"):
        pe._load_strict_json_object(path)


# --- seed / side policy strings ----------------------------------------------------------------------


def test_seed_policy_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["seed_policy"] = "some other policy"
    with pytest.raises(pe.ManifestValidationError, match="seed_policy"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_side_assignment_policy_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["side_assignment_policy"] = "some other policy"
    with pytest.raises(pe.ManifestValidationError, match="side_assignment_policy"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- ledger binding (structure only) -----------------------------------------------------------------


def test_ledger_key_set_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["ledger"] = {"path": "x"}
    with pytest.raises(pe.ManifestValidationError, match="ledger"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_ledger_blank_identity_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["ledger"]["ledger_identity"] = ""
    with pytest.raises(pe.ManifestValidationError, match="ledger.ledger_identity"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- planning cell inventory: exact 1060, no duplicates, matches the frozen protocol -----------------


def test_planning_cell_count_1059_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"].pop()
    with pytest.raises(pe.ManifestValidationError, match="exactly 1060"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_planning_cell_count_1061_via_duplicate_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"].append(manifest_dict["planning_cell_keys"][0])
    with pytest.raises(pe.ManifestValidationError, match="duplicate cell keys"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_planning_cell_set_mismatch_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"][0] = "bogus-planning-cell-key"
    with pytest.raises(pe.ManifestValidationError, match="does not match the frozen"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_planning_cell_keys_not_a_list_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"] = {"not": "a list"}
    with pytest.raises(pe.ManifestValidationError, match="list of non-empty strings"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_planning_cell_keys_with_non_string_element_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"][0] = 12345
    with pytest.raises(pe.ManifestValidationError, match="list of non-empty strings"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_planning_cell_keys_with_blank_element_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"][0] = ""
    with pytest.raises(pe.ManifestValidationError, match="list of non-empty strings"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- provider-call inventory: exact 1059 (r3), structure, cross-checks, duplicates -------------


def test_call_inventory_count_1058_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"].pop()
    with pytest.raises(pe.ManifestValidationError, match="exactly 1059"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_count_1060_is_rejected(manifest):
    manifest_dict, _identity = manifest
    extra = deepcopy(manifest_dict["provider_call_inventory"][-1])
    manifest_dict["provider_call_inventory"].append(extra)
    with pytest.raises(pe.ManifestValidationError, match="exactly 1059"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_duplicate_planning_cell_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[1]["planning_cell_key"] = entries[0]["planning_cell_key"]
    with pytest.raises(pe.ManifestValidationError, match="duplicate planning cell"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_duplicate_execution_call_key_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[1]["execution_call_key"] = entries[0]["execution_call_key"]
    with pytest.raises(pe.ManifestValidationError, match="duplicate execution_call_key"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_entry_key_set_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    del manifest_dict["provider_call_inventory"][0]["seed"]
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_wrong_call_role_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][0]["call_role"] = "judge_verdict"
    with pytest.raises(pe.ManifestValidationError, match="call_role"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_wrong_call_index_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][5]["call_index"] = 999
    with pytest.raises(pe.ManifestValidationError, match="call_index"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_unknown_planning_cell_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][0]["planning_cell_key"] = "not-a-real-cell"
    with pytest.raises(pe.ManifestValidationError, match="known capability_qa planning cell"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_model_disagreeing_with_cell_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[0]["model"] = "not-the-real-model"
    with pytest.raises(pe.ManifestValidationError, match="model disagrees"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_side_disagreeing_with_replicate_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[0]["side"] = "B" if entries[0]["side"] == "A" else "A"
    with pytest.raises(pe.ManifestValidationError, match="side disagrees"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("bad_seed", [-1, 1.5, True, "0"])
def test_call_inventory_seed_must_be_a_nonnegative_int(manifest, bad_seed):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][0]["seed"] = bad_seed
    with pytest.raises(pe.ManifestValidationError, match="seed"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_request_fields_hash_must_be_sha256(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][0]["request_fields_sha256"] = "not-a-hash"
    with pytest.raises(pe.ManifestValidationError, match="request_fields_sha256"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_execution_call_key_mismatch_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[0]["execution_call_key"] = _flip_hex_digest(entries[0]["execution_call_key"])
    with pytest.raises(pe.ManifestValidationError, match="does not match its derived value"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_call_inventory_not_a_list_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"] = {}
    with pytest.raises(pe.ManifestValidationError, match="must be a list"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- immutable stage cap / cumulative cap -------------------------------------------------------------


def test_stage_cap_escalation_over_protocol_ceiling_is_rejected(
    baseline, synthetic_artifacts, monkeypatch,
):
    monkeypatch.setattr(pe.preflight_forecast, "validate_forecast_v3", lambda *a, **k: None)
    manifest_dict, _identity = build_manifest(
        baseline, synthetic_artifacts, stage_cap=15.01, cumulative_cap=1500.0)
    with pytest.raises(pe.ManifestValidationError, match="exceeds the protocol"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_cumulative_cap_below_stage_cap_is_rejected(baseline, synthetic_artifacts, monkeypatch):
    monkeypatch.setattr(pe.preflight_forecast, "validate_forecast_v3", lambda *a, **k: None)
    manifest_dict, _identity = build_manifest(
        baseline, synthetic_artifacts, stage_cap=15.0, cumulative_cap=10.0)
    with pytest.raises(pe.ManifestValidationError, match="cumulative_cap_usd"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("field,bad_value", [
    ("stage_cap_usd", 0), ("stage_cap_usd", -1), ("stage_cap_usd", "15"), ("stage_cap_usd", True),
    ("cumulative_cap_usd", 0), ("cumulative_cap_usd", float("nan")),
])
def test_cap_fields_must_be_finite_positive_numbers(manifest, field, bad_value):
    manifest_dict, _identity = manifest
    manifest_dict[field] = bad_value
    with pytest.raises(pe.ManifestValidationError, match="finite, positive number|must be a number"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


@pytest.mark.parametrize("field", ["stage_cap_usd", "cumulative_cap_usd"])
def test_arbitrary_precision_json_integer_cap_is_rejected_not_a_crash(manifest, tmp_path, field):
    # Regression: JSON integers are unbounded-precision Python ints; float(huge_int) raises
    # the builtin OverflowError rather than being caught. That must fail closed through
    # ManifestValidationError, not escape as a raw OverflowError.
    manifest_dict, _identity = manifest
    manifest_dict[field] = 10 ** 400
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest_dict), encoding="utf-8")
    loaded = pe.load_execution_manifest(path)
    with pytest.raises(pe.ManifestValidationError, match="finite, positive number"):
        pe.validate_execution_manifest(loaded, project_root=GREEN_ROOT)


@pytest.mark.parametrize("field", ["stage_cap_usd", "cumulative_cap_usd"])
def test_arbitrary_precision_authorization_cap_is_rejected_not_a_crash(manifest, field):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization[field] = 10 ** 400
    with pytest.raises(pe.ExecutionAuthorityError, match="finite, positive number"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_capability_cell_count_drift_from_the_frozen_protocol_is_rejected(manifest, monkeypatch):
    # phase2_plan.validate_protocol's own strict equality checks make it impossible to
    # corrupt the tracked protocol.json into producing a wrong capability_qa cell count
    # without validate_protocol itself rejecting the corruption first (and thus tripping
    # the "bound base protocol is invalid" wrapper instead of this check). Monkeypatching
    # enumerate_cells's return value is the only way to exercise this defense-in-depth
    # invariant directly.
    manifest_dict, _identity = manifest
    real_enumerate_cells = phase2_plan.enumerate_cells

    def _drop_one_capability_cell(protocol, main_question_ids):
        cells = real_enumerate_cells(protocol, main_question_ids)
        for index, cell in enumerate(cells):
            if cell["kind"] == "capability_qa":
                del cells[index]
                break
        return cells

    monkeypatch.setattr(pe.phase2_plan, "enumerate_cells", _drop_one_capability_cell)
    with pytest.raises(pe.ManifestValidationError, match="exactly 1060"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_capability_preflight_proposed_cap_usd_must_be_a_number(manifest, monkeypatch):
    # Same rationale as above: validate_protocol requires proposed_cap_usd == 15 exactly,
    # so this branch can only be reached by bypassing validate_protocol via monkeypatch
    # (both phase2_plan.load_protocol's own internal call and enumerate_cells's).
    manifest_dict, _identity = manifest
    real_protocol = phase2_plan.load_protocol(PROTOCOL_PATH)
    corrupted_protocol = deepcopy(real_protocol)
    corrupted_protocol["materialization_requirements"]["capability_preflight"][
        "proposed_cap_usd"] = "15"
    manifest_dict["protocol_canonical_sha256"] = phase2_plan.canonical_sha256(corrupted_protocol)

    monkeypatch.setattr(pe.phase2_plan, "load_protocol", lambda path: corrupted_protocol)
    monkeypatch.setattr(pe.phase2_plan, "validate_protocol", lambda protocol: None)
    with pytest.raises(
        pe.ManifestValidationError, match="proposed_cap_usd must be a number",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- authorization --------------------------------------------------------------------------------------


def test_no_authorization_record_is_rejected_when_required(manifest):
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ExecutionAuthorityError, match="none was provided"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, require_authorized=True)


def test_wrong_identity_hash_authorization_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization["execution_identity_sha256"] = _flip_hex_digest(identity_sha256)
    with pytest.raises(pe.ExecutionAuthorityError, match="does not match this manifest"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_wrong_stage_authorization_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256, stage="canary")
    with pytest.raises(pe.ExecutionAuthorityError, match="authorization.stage"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


@pytest.mark.parametrize("field", ["stage_cap_usd", "cumulative_cap_usd"])
def test_wrong_caps_authorization_is_rejected(manifest, field):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization[field] = authorization[field] + 1
    with pytest.raises(pe.ExecutionAuthorityError, match="caps do not match"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_key_set_drift_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    del authorization["approver"]
    with pytest.raises(pe.ExecutionAuthorityError, match="fields drifted"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_non_utc_timestamp_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization["approved_at_utc"] = "2026-07-18"
    with pytest.raises(pe.ExecutionAuthorityError, match="UTC timestamp"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_blank_approver_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization["approver"] = "   "
    with pytest.raises(pe.ExecutionAuthorityError, match="approver"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_not_a_mapping_is_rejected(manifest):
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ExecutionAuthorityError, match="must be an object"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=cast(Any, ["not", "a", "dict"]),
            require_authorized=True,
        )


# --- authorization: approval_basis ---------------------------------------------------------------


def test_authorization_missing_approval_basis_key_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    del authorization["approval_basis_tracked_path"]
    with pytest.raises(pe.ExecutionAuthorityError, match="fields drifted"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_approval_basis_unpinned_path_fails_closed(manifest, tmp_path):
    # approval_basis_tracked_path is now PINNED to the frozen preflight delegation record; a
    # syntactically-fine-but-different absolute path fails the pinning check before any
    # existence check is even reached.
    manifest_dict, identity_sha256 = manifest
    missing = tmp_path / "no_such_basis.md"
    authorization = matching_authorization(
        identity_sha256, approval_basis_tracked_path=str(missing),
        approval_basis_sha256="a" * 64,
    )
    with pytest.raises(
        pe.ExecutionAuthorityError,
        match="must resolve to either the frozen, git-tracked preflight",
    ):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


@pytest.fixture(scope="module")
def missing_delegation_root(tmp_path_factory):
    destination = tmp_path_factory.mktemp("missing_delegation_root")
    _copy_tracked_data_files(destination)
    (destination / "rejudge" / "phase2_preflight_delegation_2026-07-19.json").unlink()
    return destination


def test_missing_shared_delegation_record_fails_closed(manifest, missing_delegation_root):
    # The frozen preflight delegation record is now depended on by TWO independent checks: v5
    # role-limits' own approval_basis (reused unmodified from v3/v4, validated earlier, as part
    # of every manifest validation, authorized or not) and the authorization record's
    # approval_basis (validated only when require_authorized=True). Deleting the shared file is
    # caught by whichever check runs first -- here, role-limits v5's -- so the authorization
    # block's own "artifact is missing" branch (identical in shape) is defensive/unreachable
    # given this coupling, not dead code from a design flaw: the SAME missing file still fails
    # closed, just earlier.
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not validate as a v5 role-limits artifact.*approval_basis artifact is missing",
    ):
        pe.validate_execution_manifest(
            manifest_dict, project_root=missing_delegation_root, authorization=authorization,
            require_authorized=True,
        )


# =================================================================================================
# SEMANTIC ARTIFACT GATES: the new private validators, exercised directly (unit-style, matching
# this codebase's own precedent -- e.g. rl._load_json direct calls in test_phase2_role_limits.py)
# so each gate's REAL, unpatched logic is covered without needing the full manifest/execution-
# identity/role-limits-v3-chain plumbing every one of them is also wired into above.
# =================================================================================================


# --- cost_forecast: the real, unpatched READY/CONFLICT gate -------------------------------------


@pytest.fixture(scope="module")
def cost_forecast_gate_context(tmp_path_factory):
    """Isolated root for testing ``pe._validate_cost_forecast_gate``'s REAL, unpatched behavior
    directly against the v3 forecast schema: real protocol/bundle/role-limits-v4/provider-refresh
    (byte-identical to ROOT). ``validate_forecast_v3`` binds v4 for real, so the real, tracked v4
    artifact is used directly and the real v2/v3 files at their own frozen paths are left
    completely untouched.
    """
    root = tmp_path_factory.mktemp("cost_forecast_gate_root")
    _copy_tracked_data_files(root)

    protocol_path = root / "rejudge" / "phase2_protocol.json"
    protocol = phase2_plan.load_protocol(protocol_path)
    role_limits_v4, _p, snapshot = role_limits.load_and_validate_v4(
        root / "rejudge" / "phase2_role_limits_v4_2026-07-19.json", protocol_path,
        root / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json",
        root / "rejudge" / "phase2_role_limits_v3_2026-07-19.json", project_root=root)
    bundle, _bp = prompt_bundle.load_and_validate(
        root / "rejudge" / "phase2_prompt_bundle.json", protocol_path)
    provider_refresh = json.loads(
        (root / "rejudge" / "phase2_provider_refresh_2026-07-19.json").read_text(
            encoding="utf-8"))

    ready = _build_ready_forecast_payload_v3(
        root=root, protocol=protocol, role_limits_v4=role_limits_v4, snapshot=snapshot,
        bundle=bundle, provider_refresh=provider_refresh)
    forecast_path = root / "rejudge" / "output" / "_cost_forecast_gate_ready.json"
    forecast_path.parent.mkdir(parents=True, exist_ok=True)
    forecast_path.write_text(
        json.dumps(ready, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    return {
        "root": root, "protocol": protocol, "role_limits_v4": role_limits_v4,
        "snapshot": snapshot, "bundle": bundle, "provider_refresh": provider_refresh,
        "ready_forecast": ready, "forecast_path": forecast_path,
    }


def test_cost_forecast_gate_accepts_a_genuine_ready_forecast(cost_forecast_gate_context):
    ctx = cost_forecast_gate_context
    declared_sha = phase2_plan.canonical_sha256(ctx["ready_forecast"])
    binding = {
        "path": ctx["forecast_path"].relative_to(ctx["root"]).as_posix(), "sha256": declared_sha,
    }
    observed = pe._validate_cost_forecast_gate(
        binding, root=ctx["root"], protocol=ctx["protocol"],
        role_limits_v4_payload=ctx["role_limits_v4"], snapshot=ctx["snapshot"],
        bundle=ctx["bundle"], provider_refresh_payload=ctx["provider_refresh"])
    assert observed == declared_sha


def test_cost_forecast_gate_accepts_the_real_tracked_2026_07_19_r2_artifact(
    cost_forecast_gate_context,
):
    """The actual, real ``rejudge/phase2_preflight_forecast_2026-07-19-r2.json`` this task builds
    -- not a synthetic fixture reconstruction of it -- must independently clear the same gate."""
    ctx = cost_forecast_gate_context
    real_path = ROOT / "rejudge" / "phase2_preflight_forecast_2026-07-19-r2.json"
    real_payload = json.loads(real_path.read_text(encoding="utf-8"))
    declared_sha = phase2_plan.canonical_sha256(real_payload)
    binding = {
        "path": real_path.relative_to(ROOT).as_posix(),
        "sha256": declared_sha,
    }
    observed = pe._validate_cost_forecast_gate(
        binding, root=ROOT, protocol=ctx["protocol"],
        role_limits_v4_payload=ctx["role_limits_v4"], snapshot=ctx["snapshot"],
        bundle=ctx["bundle"], provider_refresh_payload=ctx["provider_refresh"])
    assert observed == declared_sha


def test_cost_forecast_gate_rejects_a_wrong_schema_artifact(cost_forecast_gate_context):
    ctx = cost_forecast_gate_context
    conflict_payload = json.loads(
        forecast.DEFAULT_CONFLICT_ARTIFACT_PATH.read_text(encoding="utf-8"))
    target = ctx["root"] / "rejudge" / "output" / "_cost_forecast_gate_conflict.json"
    target.write_text(
        json.dumps(conflict_payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")
    binding = {
        "path": target.relative_to(ctx["root"]).as_posix(),
        "sha256": phase2_plan.canonical_sha256(conflict_payload),
    }
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not validate as a ready capability-preflight forecast",
    ):
        pe._validate_cost_forecast_gate(
            binding, root=ctx["root"], protocol=ctx["protocol"],
            role_limits_v4_payload=ctx["role_limits_v4"], snapshot=ctx["snapshot"],
            bundle=ctx["bundle"], provider_refresh_payload=ctx["provider_refresh"])


def test_cost_forecast_gate_rejects_the_v2_shaped_artifact(cost_forecast_gate_context):
    """The real, tracked v2 forecast (bound to role-limits v3) must fail the manifest's cost_
    forecast gate now that it requires v3 (bound to role-limits v4) -- this is the r2 relaunch's
    own regression guard, proving the gate can never silently fall back to the superseded v2
    schema."""
    ctx = cost_forecast_gate_context
    real_v2_payload = json.loads(READY_ARTIFACT_V2_PATH.read_text(encoding="utf-8"))
    declared_sha = phase2_plan.canonical_sha256(real_v2_payload)
    binding = {
        "path": READY_ARTIFACT_V2_PATH.relative_to(ROOT).as_posix(), "sha256": declared_sha,
    }
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not validate as a ready capability-preflight forecast",
    ):
        pe._validate_cost_forecast_gate(
            binding, root=ROOT, protocol=ctx["protocol"],
            role_limits_v4_payload=ctx["role_limits_v4"], snapshot=ctx["snapshot"],
            bundle=ctx["bundle"], provider_refresh_payload=ctx["provider_refresh"])


def test_cost_forecast_gate_rejects_stress_at_or_above_halt_cap(cost_forecast_gate_context):
    # Real, frozen 2026-07-19 prices/role-limits-v4 genuinely clear the gate now (that's this
    # task's whole point), so there is no longer a "real inputs" combination that fails to clear
    # it the way v1's real 4-attempt transport did. This constructs an otherwise fully honest v3
    # forecast against a synthetic, deliberately 10x-inflated price snapshot instead, proving the
    # positive-margin requirement is still a live, price-sensitive gate, not a fixed pass.
    ctx = cost_forecast_gate_context
    expensive_snapshot = deepcopy(ctx["snapshot"])
    for entry in expensive_snapshot["models"].values():
        entry["input_usd_per_million_tokens"] = float(entry["input_usd_per_million_tokens"]) * 10
        entry["output_usd_per_million_tokens"] = (
            float(entry["output_usd_per_million_tokens"]) * 10)
    with pytest.raises(AssertionError, match="fixture prices must be cheap enough"):
        _build_ready_forecast_payload_v3(
            root=ctx["root"], protocol=ctx["protocol"], role_limits_v4=ctx["role_limits_v4"],
            snapshot=expensive_snapshot, bundle=ctx["bundle"],
            provider_refresh=ctx["provider_refresh"])


def test_cost_forecast_gate_hash_drift_is_rejected(cost_forecast_gate_context):
    ctx = cost_forecast_gate_context
    declared_sha = phase2_plan.canonical_sha256(ctx["ready_forecast"])
    binding = {
        "path": ctx["forecast_path"].relative_to(ctx["root"]).as_posix(),
        "sha256": _flip_hex_digest(declared_sha),
    }
    with pytest.raises(pe.ManifestValidationError, match="cost_forecast hash drift"):
        pe._validate_cost_forecast_gate(
            binding, root=ctx["root"], protocol=ctx["protocol"],
            role_limits_v4_payload=ctx["role_limits_v4"], snapshot=ctx["snapshot"],
            bundle=ctx["bundle"], provider_refresh_payload=ctx["provider_refresh"])


# --- storage_policy: the real schema -------------------------------------------------------------


def test_storage_policy_gate_accepts_the_real_tracked_artifact():
    binding = {
        "path": "rejudge/phase2_storage_policy_2026-07-18.json", "sha256": _canon_sha(
            STORAGE_POLICY_PATH),
    }
    pe._validate_storage_policy_gate(binding, root=ROOT)  # must not raise


def test_storage_policy_gate_rejects_schema_version_drift(tmp_path):
    payload = json.loads(STORAGE_POLICY_PATH.read_text(encoding="utf-8"))
    payload["schema_version"] = "wrong_version"
    (tmp_path / "policy.json").write_text(json.dumps(payload), encoding="utf-8")
    binding = {"path": "policy.json", "sha256": phase2_plan.canonical_sha256(payload)}
    with pytest.raises(pe.ManifestValidationError, match="storage_policy schema_version drifted"):
        pe._validate_storage_policy_gate(binding, root=tmp_path)


def test_storage_policy_gate_rejects_blank_versioned_destination(tmp_path):
    payload = json.loads(STORAGE_POLICY_PATH.read_text(encoding="utf-8"))
    payload["versioned_destination"] = ""
    (tmp_path / "policy.json").write_text(json.dumps(payload), encoding="utf-8")
    binding = {"path": "policy.json", "sha256": phase2_plan.canonical_sha256(payload)}
    with pytest.raises(pe.ManifestValidationError, match="versioned_destination"):
        pe._validate_storage_policy_gate(binding, root=tmp_path)


def test_storage_policy_gate_rejects_key_set_drift(tmp_path):
    payload = json.loads(STORAGE_POLICY_PATH.read_text(encoding="utf-8"))
    payload["unexpected_field"] = "x"
    (tmp_path / "policy.json").write_text(json.dumps(payload), encoding="utf-8")
    binding = {"path": "policy.json", "sha256": phase2_plan.canonical_sha256(payload)}
    with pytest.raises(pe.ManifestValidationError, match="storage_policy"):
        pe._validate_storage_policy_gate(binding, root=tmp_path)


def test_storage_policy_gate_rejects_execution_authorized_true(tmp_path):
    payload = json.loads(STORAGE_POLICY_PATH.read_text(encoding="utf-8"))
    payload["execution_authorized"] = True
    (tmp_path / "policy.json").write_text(json.dumps(payload), encoding="utf-8")
    binding = {"path": "policy.json", "sha256": phase2_plan.canonical_sha256(payload)}
    with pytest.raises(pe.ManifestValidationError, match="execution_authorized"):
        pe._validate_storage_policy_gate(binding, root=tmp_path)


# --- provider_refresh: pinned, schema + verdict + raw-response hash -----------------------------


def test_provider_refresh_gate_accepts_the_real_tracked_artifact():
    binding = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json",
        "sha256": _canon_sha(PROVIDER_REFRESH_PATH),
    }
    snapshot, _p = price_snapshot.load_and_validate(PRICE_SNAPSHOT_PATH, PROTOCOL_PATH)
    pe._validate_provider_refresh_gate(binding, root=ROOT, snapshot=snapshot)  # must not raise


def _root_with_mutated_provider_refresh(tmp_path: Path, mutate) -> tuple[Path, dict]:
    root = tmp_path
    (root / "rejudge").mkdir(parents=True, exist_ok=True)
    (root / "rejudge" / "phase2_provider_models_raw_2026-07-19.json").write_bytes(
        (ROOT / "rejudge" / "phase2_provider_models_raw_2026-07-19.json").read_bytes())
    payload = json.loads(PROVIDER_REFRESH_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    path = root / "rejudge" / "phase2_provider_refresh_2026-07-19.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return root, payload


def test_provider_refresh_gate_rejects_price_above_frozen(tmp_path):
    root, payload = _root_with_mutated_provider_refresh(
        tmp_path,
        lambda p: p["roster_verification"]["Qwen/Qwen2.5-7B-Instruct-Turbo"].__setitem__(
            "input_usd_per_million_tokens", 999.0),
    )
    binding = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    snapshot, _p = price_snapshot.load_and_validate(PRICE_SNAPSHOT_PATH, PROTOCOL_PATH)
    with pytest.raises(pe.ManifestValidationError, match="is above the frozen price"):
        pe._validate_provider_refresh_gate(binding, root=root, snapshot=snapshot)


def test_provider_refresh_gate_rejects_absent_model(tmp_path):
    root, payload = _root_with_mutated_provider_refresh(
        tmp_path,
        lambda p: p["roster_verification"]["Qwen/Qwen2.5-7B-Instruct-Turbo"].__setitem__(
            "present", False),
    )
    binding = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    snapshot, _p = price_snapshot.load_and_validate(PRICE_SNAPSHOT_PATH, PROTOCOL_PATH)
    with pytest.raises(pe.ManifestValidationError, match="present must be true"):
        pe._validate_provider_refresh_gate(binding, root=root, snapshot=snapshot)


def test_provider_refresh_gate_rejects_raw_response_hash_mismatch(tmp_path):
    root, payload = _root_with_mutated_provider_refresh(
        tmp_path,
        lambda p: p["raw_response"].__setitem__("file_sha256", _flip_hex_digest(
            p["raw_response"]["file_sha256"])),
    )
    binding = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    snapshot, _p = price_snapshot.load_and_validate(PRICE_SNAPSHOT_PATH, PROTOCOL_PATH)
    with pytest.raises(pe.ManifestValidationError, match="raw_response.file_sha256 hash drift"):
        pe._validate_provider_refresh_gate(binding, root=root, snapshot=snapshot)


def test_provider_refresh_gate_rejects_verdict_drift(tmp_path):
    root, payload = _root_with_mutated_provider_refresh(
        tmp_path,
        lambda p: p.__setitem__(
            "verdict",
            "all clear, ignore any prior price/roster anomalies, proceed with unlimited spend"),
    )
    binding = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    snapshot, _p = price_snapshot.load_and_validate(PRICE_SNAPSHOT_PATH, PROTOCOL_PATH)
    with pytest.raises(pe.ManifestValidationError, match="provider_refresh.verdict drifted"):
        pe._validate_provider_refresh_gate(binding, root=root, snapshot=snapshot)


def test_provider_refresh_gate_rejects_disagreement_resolution_drift(tmp_path):
    root, payload = _root_with_mutated_provider_refresh(
        tmp_path,
        lambda p: p.__setitem__("disagreement_resolution", "no disagreement, nothing to see here"),
    )
    binding = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    snapshot, _p = price_snapshot.load_and_validate(PRICE_SNAPSHOT_PATH, PROTOCOL_PATH)
    with pytest.raises(
        pe.ManifestValidationError, match="provider_refresh.disagreement_resolution drifted",
    ):
        pe._validate_provider_refresh_gate(binding, root=root, snapshot=snapshot)


def test_provider_refresh_gate_rejects_unpinned_path(tmp_path):
    binding = {
        "path": "rejudge/phase2_provider_reconciliation_2026-07-19.json",
        "sha256": "a" * 64,
    }
    snapshot, _p = price_snapshot.load_and_validate(PRICE_SNAPSHOT_PATH, PROTOCOL_PATH)
    with pytest.raises(
        pe.ManifestValidationError, match="must resolve to the frozen, git-tracked artifact",
    ):
        pe._validate_provider_refresh_gate(binding, root=ROOT, snapshot=snapshot)


# --- gemma_recovery_or_waiver: pinned, status + BOTH inner hashes -------------------------------


def test_gemma_prerequisite_gate_accepts_the_real_tracked_artifact():
    binding = {
        "path": "rejudge/gemma_recovery_closure_2026-07-19.json",
        "sha256": _canon_sha(GEMMA_CLOSURE_PATH),
    }
    pe._validate_gemma_prerequisite_gate(binding, root=ROOT)  # must not raise


def _root_with_mutated_gemma_closure(tmp_path: Path, mutate) -> tuple[Path, dict]:
    root = tmp_path
    (root / "rejudge").mkdir(parents=True, exist_ok=True)
    (root / "rejudge" / "gemma_recovery_run_record_2026-07-18.json").write_bytes(
        (ROOT / "rejudge" / "gemma_recovery_run_record_2026-07-18.json").read_bytes())
    (root / "rejudge" / "phase2_provider_reconciliation_2026-07-19.json").write_bytes(
        PROVIDER_RECONCILIATION_2026_07_19_PATH.read_bytes())
    payload = json.loads(GEMMA_CLOSURE_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    path = root / "rejudge" / "gemma_recovery_closure_2026-07-19.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return root, payload


def test_gemma_prerequisite_gate_rejects_status_drift(tmp_path):
    root, payload = _root_with_mutated_gemma_closure(
        tmp_path, lambda p: p.__setitem__("status", "open"))
    binding = {
        "path": "rejudge/gemma_recovery_closure_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(pe.ManifestValidationError, match="status must be exactly"):
        pe._validate_gemma_prerequisite_gate(binding, root=root)


def test_gemma_prerequisite_gate_rejects_run_record_hash_mismatch(tmp_path):
    root, payload = _root_with_mutated_gemma_closure(
        tmp_path,
        lambda p: p["run_record"].__setitem__(
            "file_sha256", _flip_hex_digest(p["run_record"]["file_sha256"])),
    )
    binding = {
        "path": "rejudge/gemma_recovery_closure_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(pe.ManifestValidationError, match="run_record.file_sha256 hash drift"):
        pe._validate_gemma_prerequisite_gate(binding, root=root)


def test_gemma_prerequisite_gate_rejects_reconciliation_hash_mismatch(tmp_path):
    root, payload = _root_with_mutated_gemma_closure(
        tmp_path,
        lambda p: p["reconciliation"].__setitem__(
            "file_sha256", _flip_hex_digest(p["reconciliation"]["file_sha256"])),
    )
    binding = {
        "path": "rejudge/gemma_recovery_closure_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(pe.ManifestValidationError, match="reconciliation.file_sha256 hash drift"):
        pe._validate_gemma_prerequisite_gate(binding, root=root)


def test_gemma_prerequisite_gate_rejects_unpinned_path():
    binding = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json", "sha256": "a" * 64,
    }
    with pytest.raises(
        pe.ManifestValidationError, match="must resolve to the frozen, git-tracked artifact",
    ):
        pe._validate_gemma_prerequisite_gate(binding, root=ROOT)


# --- prior_attempt_closure: pinned, schema + classification + ledger_retired --------------------


def test_prior_attempt_closure_gate_accepts_the_real_tracked_artifact():
    binding = {
        "path": "rejudge/phase2_preflight_abort_closure_2026-07-19.json",
        "sha256": _canon_sha(PRIOR_ATTEMPT_CLOSURE_PATH),
    }
    pe._validate_prior_attempt_closure_gate(binding, root=ROOT)  # must not raise


def _root_with_mutated_prior_attempt_closure(tmp_path: Path, mutate) -> tuple[Path, dict]:
    root = tmp_path
    (root / "rejudge").mkdir(parents=True, exist_ok=True)
    payload = json.loads(PRIOR_ATTEMPT_CLOSURE_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    path = root / "rejudge" / "phase2_preflight_abort_closure_2026-07-19.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return root, payload


def test_prior_attempt_closure_gate_rejects_schema_version_drift(tmp_path):
    root, payload = _root_with_mutated_prior_attempt_closure(
        tmp_path, lambda p: p.__setitem__("schema_version", "wrong_version"))
    binding = {
        "path": "rejudge/phase2_preflight_abort_closure_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(
        pe.ManifestValidationError, match="prior_attempt_closure schema_version drifted",
    ):
        pe._validate_prior_attempt_closure_gate(binding, root=root)


def test_prior_attempt_closure_gate_rejects_wrong_classification(tmp_path):
    root, payload = _root_with_mutated_prior_attempt_closure(
        tmp_path, lambda p: p.__setitem__("classification", "some_other_classification"))
    binding = {
        "path": "rejudge/phase2_preflight_abort_closure_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(
        pe.ManifestValidationError, match="prior_attempt_closure.classification must be exactly",
    ):
        pe._validate_prior_attempt_closure_gate(binding, root=root)


def test_prior_attempt_closure_gate_rejects_ledger_retired_false(tmp_path):
    root, payload = _root_with_mutated_prior_attempt_closure(
        tmp_path, lambda p: p.__setitem__("ledger_retired", False))
    binding = {
        "path": "rejudge/phase2_preflight_abort_closure_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(
        pe.ManifestValidationError, match="prior_attempt_closure.ledger_retired must be exactly true",
    ):
        pe._validate_prior_attempt_closure_gate(binding, root=root)


def test_prior_attempt_closure_gate_rejects_wrong_stage(tmp_path):
    root, payload = _root_with_mutated_prior_attempt_closure(
        tmp_path, lambda p: p.__setitem__("stage", "main"))
    binding = {
        "path": "rejudge/phase2_preflight_abort_closure_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(
        pe.ManifestValidationError, match="prior_attempt_closure.stage must be exactly",
    ):
        pe._validate_prior_attempt_closure_gate(binding, root=root)


def test_prior_attempt_closure_gate_rejects_hash_drift(tmp_path):
    root, payload = _root_with_mutated_prior_attempt_closure(tmp_path, lambda p: None)
    binding = {
        "path": "rejudge/phase2_preflight_abort_closure_2026-07-19.json",
        "sha256": _flip_hex_digest(phase2_plan.canonical_sha256(payload)),
    }
    with pytest.raises(pe.ManifestValidationError, match="prior_attempt_closure hash drift"):
        pe._validate_prior_attempt_closure_gate(binding, root=root)


def test_prior_attempt_closure_gate_rejects_unpinned_path():
    binding = {
        "path": "rejudge/phase2_provider_refresh_2026-07-19.json", "sha256": "a" * 64,
    }
    with pytest.raises(
        pe.ManifestValidationError, match="must resolve to the frozen, git-tracked artifact",
    ):
        pe._validate_prior_attempt_closure_gate(binding, root=ROOT)


def test_manifest_missing_prior_attempt_closure_fails_closed(manifest):
    manifest_dict, _identity = manifest
    del manifest_dict["prior_attempt_closure"]
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- provider_reconciliation_evidence: pinned, the 2026-07-19 record ----------------------------


def test_provider_reconciliation_gate_accepts_the_real_tracked_artifact():
    binding = {
        "path": "rejudge/phase2_provider_reconciliation_2026-07-19.json",
        "sha256": _canon_sha(PROVIDER_RECONCILIATION_2026_07_19_PATH),
    }
    pe._validate_provider_reconciliation_gate(binding, root=ROOT)  # must not raise


def test_provider_reconciliation_gate_rejects_schema_version_drift(tmp_path):
    payload = json.loads(PROVIDER_RECONCILIATION_2026_07_19_PATH.read_text(encoding="utf-8"))
    payload["schema_version"] = "wrong"
    (tmp_path / "rejudge").mkdir(parents=True, exist_ok=True)
    (tmp_path / "rejudge" / "phase2_provider_reconciliation_2026-07-19.json").write_text(
        json.dumps(payload), encoding="utf-8")
    binding = {
        "path": "rejudge/phase2_provider_reconciliation_2026-07-19.json",
        "sha256": phase2_plan.canonical_sha256(payload),
    }
    with pytest.raises(
        pe.ManifestValidationError, match="provider_reconciliation_evidence schema_version drifted",
    ):
        pe._validate_provider_reconciliation_gate(binding, root=tmp_path)


def test_provider_reconciliation_gate_rejects_unpinned_path():
    binding = {"path": "rejudge/gemma_recovery_closure_2026-07-19.json", "sha256": "a" * 64}
    with pytest.raises(
        pe.ManifestValidationError, match="must resolve to the frozen, git-tracked artifact",
    ):
        pe._validate_provider_reconciliation_gate(binding, root=ROOT)


# --- implementation_provenance: CODE-PROVENANCE BINDING -------------------------------------------


def test_implementation_provenance_accepts_the_real_code_bundle():
    binding = dict(IMPLEMENTATION_PROVENANCE_BINDING)
    result = pe._validate_implementation_provenance(binding, root=ROOT)
    assert result == binding


@pytest.mark.parametrize("bad_commit", ["abc123", "A" * 40, "g" * 40, "a" * 39, None, 12345])
def test_implementation_provenance_rejects_malformed_git_commit(bad_commit):
    binding = {"git_commit": bad_commit, "code_bundle_sha256": pe.compute_code_bundle_sha256(ROOT)}
    with pytest.raises(pe.ManifestValidationError, match="git_commit"):
        pe._validate_implementation_provenance(binding, root=ROOT)


def test_implementation_provenance_rejects_code_bundle_hash_drift(tmp_path):
    for relative in pe.CODE_PROVENANCE_FROZEN_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    # Tamper exactly one frozen file.
    tampered = tmp_path / pe.CODE_PROVENANCE_FROZEN_FILES[0]
    tampered.write_bytes(tampered.read_bytes() + b"\n# tampered\n")
    binding = {"git_commit": "a" * 40, "code_bundle_sha256": pe.compute_code_bundle_sha256(ROOT)}
    with pytest.raises(pe.ManifestValidationError, match="code_bundle_sha256 hash drift"):
        pe._validate_implementation_provenance(binding, root=tmp_path)


def test_implementation_provenance_rejects_missing_code_file(tmp_path):
    binding = {"git_commit": "a" * 40, "code_bundle_sha256": pe.compute_code_bundle_sha256(ROOT)}
    with pytest.raises(pe.ManifestValidationError, match="could not read"):
        pe._validate_implementation_provenance(binding, root=tmp_path)


def test_implementation_provenance_key_set_drift_is_rejected():
    binding = {**IMPLEMENTATION_PROVENANCE_BINDING, "extra": "x"}
    with pytest.raises(pe.ManifestValidationError, match="implementation_provenance"):
        pe._validate_implementation_provenance(binding, root=ROOT)


def test_implementation_provenance_field_is_bound_into_the_manifest_and_rejected_on_drift(
    manifest,
):
    manifest_dict, _identity = manifest
    manifest_dict["implementation_provenance"]["code_bundle_sha256"] = _flip_hex_digest(
        manifest_dict["implementation_provenance"]["code_bundle_sha256"])
    with pytest.raises(pe.ManifestValidationError, match="code_bundle_sha256 hash drift"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_implementation_provenance_missing_key_is_rejected(manifest):
    manifest_dict, _identity = manifest
    del manifest_dict["implementation_provenance"]
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- PINNED DELEGATION AUTHORIZATION BASIS: the delegation record's own literal content ---------


def _validate_delegation_record(payload, tmp_path, *, stage="capability_preflight",
                                stage_cap_usd=15.0):
    path = tmp_path / "delegation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return pe._validate_preflight_delegation_record(
        path, stage=stage, stage_cap_usd=stage_cap_usd)


def test_delegation_record_accepts_the_real_tracked_record(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    result = _validate_delegation_record(payload, tmp_path)
    assert result["delegation_id"] == "capability_preflight_delegation_2026-07-19"


def test_delegation_record_rejects_schema_version_drift(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["schema_version"] = "wrong"
    with pytest.raises(pe.ExecutionAuthorityError, match="schema_version"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_delegation_id_drift(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["delegation_id"] = "wrong"
    with pytest.raises(pe.ExecutionAuthorityError, match="delegation_id"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_approver_drift(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["approver"] = "Someone Else"
    with pytest.raises(pe.ExecutionAuthorityError, match="approver"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_exact_quote_drift(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["exact_quote"] = "a different quote entirely"
    with pytest.raises(pe.ExecutionAuthorityError, match="exact_quote"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_approved_at_utc_drift(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["approved_at_utc"] = "2026-07-19T00:51:43Z"
    with pytest.raises(pe.ExecutionAuthorityError, match="approved_at_utc drifted"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_non_utc_recorded_at(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["recorded_at_utc"] = "2026-07-19"
    with pytest.raises(pe.ExecutionAuthorityError, match="recorded_at_utc"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_scope_stage_mismatch(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    with pytest.raises(pe.ExecutionAuthorityError, match="scope.stage"):
        _validate_delegation_record(payload, tmp_path, stage="canary")


def test_delegation_record_rejects_scope_stage_cap_mismatch(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    with pytest.raises(pe.ExecutionAuthorityError, match="scope.stage_cap_usd"):
        _validate_delegation_record(payload, tmp_path, stage_cap_usd=999.0)


def test_delegation_record_rejects_empty_predicates(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["scope"]["predicates"] = []
    with pytest.raises(pe.ExecutionAuthorityError, match="predicates"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_exclusions_missing_canary(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["exclusions"] = "This does NOT extend to main-run spend."
    with pytest.raises(pe.ExecutionAuthorityError, match="canary and main"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_exclusions_missing_main(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["exclusions"] = "This does NOT extend to canary spend."
    with pytest.raises(pe.ExecutionAuthorityError, match="canary and main"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_execution_authorized_true(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["execution_authorized"] = True
    with pytest.raises(pe.ExecutionAuthorityError, match="execution_authorized"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_key_set_drift(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["unexpected_field"] = "x"
    with pytest.raises(pe.ExecutionAuthorityError, match="fields drifted"):
        _validate_delegation_record(payload, tmp_path)


def test_delegation_record_rejects_scope_key_set_drift(tmp_path):
    payload = json.loads(DELEGATION_PATH.read_text(encoding="utf-8"))
    payload["scope"]["extra"] = "x"
    with pytest.raises(pe.ExecutionAuthorityError, match="fields drifted"):
        _validate_delegation_record(payload, tmp_path)


# --- authorization: pinned to and cross-checked against the delegation record -------------------


def test_authorization_approver_must_be_jack_maiorino(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256, approver="Someone Else")
    with pytest.raises(pe.ExecutionAuthorityError, match="authorization.approver must be exactly"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_approved_at_utc_must_equal_the_delegations(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(
        identity_sha256, approved_at_utc="2026-07-19T00:51:43Z")
    with pytest.raises(
        pe.ExecutionAuthorityError, match="must equal the cited delegation",
    ):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_recorded_at_utc_must_be_a_utc_timestamp(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256, recorded_at_utc="2026-07-19")
    with pytest.raises(pe.ExecutionAuthorityError, match="recorded_at_utc"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_missing_recorded_at_utc_key_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    del authorization["recorded_at_utc"]
    with pytest.raises(pe.ExecutionAuthorityError, match="fields drifted"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_approval_basis_relative_path_escape_fails_closed(manifest):
    # Every other "escapes the repository root" regression exercises the default
    # ManifestValidationError branch of _resolve_bound_path; this is the one caller that
    # passes ExecutionAuthorityError instead, and it had no direct coverage.
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(
        identity_sha256, approval_basis_tracked_path="../outside_the_repo_root.md",
        approval_basis_sha256="a" * 64,
    )
    with pytest.raises(pe.ExecutionAuthorityError, match="escapes the repository root"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_approval_basis_hash_mismatch_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(
        identity_sha256, approval_basis_sha256=_flip_hex_digest(APPROVAL_BASIS_SHA256))
    with pytest.raises(pe.ExecutionAuthorityError, match="approval_basis_sha256 hash drift"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_approval_basis_uses_raw_not_canonical_hashing(manifest):
    # The approval basis may be markdown (not JSON), so it must be hashed as raw bytes, not
    # canonical JSON; binding the canonical-JSON sha of an unrelated JSON artifact here must
    # not accidentally validate.
    manifest_dict, identity_sha256 = manifest
    wrong_kind_of_hash = phase2_plan.canonical_sha256(
        json.loads(PROMPT_BUNDLE_APPROVAL_PATH.read_text(encoding="utf-8")))
    authorization = matching_authorization(
        identity_sha256, approval_basis_sha256=wrong_kind_of_hash)
    with pytest.raises(pe.ExecutionAuthorityError, match="approval_basis_sha256 hash drift"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_manifest_field_cannot_claim_authorization():
    # There is no field in MANIFEST_TOP_LEVEL_KEYS that could claim authorization; this is
    # a structural guarantee, not something a manifest author can add.
    assert "authorized" not in pe.MANIFEST_TOP_LEVEL_KEYS
    assert "execution_authorized" not in pe.MANIFEST_TOP_LEVEL_KEYS


# --- load_execution_manifest: strict JSON ---------------------------------------------------------------


def test_load_rejects_non_dict_root(tmp_path):
    path = _write_json(tmp_path, "manifest.json", [1, 2, 3])
    with pytest.raises(pe.ManifestValidationError, match="must be an object"):
        pe.load_execution_manifest(path)


def test_load_rejects_malformed_json(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(pe.ManifestValidationError, match="not valid JSON"):
        pe.load_execution_manifest(path)


def test_load_rejects_unreadable_path(tmp_path):
    with pytest.raises(pe.ManifestValidationError, match="could not read"):
        pe.load_execution_manifest(tmp_path)


def test_load_rejects_top_level_duplicate_keys(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"stage": "a", "stage": "b"}', encoding="utf-8")
    with pytest.raises(pe.ManifestValidationError, match="duplicate key"):
        pe.load_execution_manifest(path)


def test_load_rejects_nested_duplicate_keys(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"ledger": {"path": "a", "path": "b"}}', encoding="utf-8")
    with pytest.raises(pe.ManifestValidationError, match="duplicate key"):
        pe.load_execution_manifest(path)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_load_rejects_non_finite_constants(tmp_path, literal):
    path = tmp_path / "manifest.json"
    path.write_text(f'{{"stage_cap_usd": {literal}}}', encoding="utf-8")
    with pytest.raises(pe.ManifestValidationError, match="non-finite constant"):
        pe.load_execution_manifest(path)


def test_load_accepts_the_valid_manifest_round_trip(manifest, tmp_path):
    manifest_dict, _identity = manifest
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest_dict), encoding="utf-8")
    loaded = pe.load_execution_manifest(path)
    validated = pe.validate_execution_manifest(loaded, project_root=GREEN_ROOT)
    assert validated.stage == "capability_preflight"


# --- _load_json_object / _raw_file_sha256 / _parse_utc_timestamp: direct unit coverage --------


def test_load_json_object_rejects_unreadable_path(tmp_path):
    with pytest.raises(pe.ManifestValidationError, match="could not read"):
        pe._load_json_object(tmp_path / "does-not-exist.json")


def test_load_json_object_rejects_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(pe.ManifestValidationError, match="could not read"):
        pe._load_json_object(path)


def test_load_json_object_rejects_non_object_payload(tmp_path):
    path = tmp_path / "array.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(pe.ManifestValidationError, match="must contain a JSON object"):
        pe._load_json_object(path)


def test_raw_file_sha256_rejects_unreadable_path(tmp_path):
    with pytest.raises(pe.ManifestValidationError, match="could not read"):
        pe._raw_file_sha256(tmp_path / "does-not-exist")


def test_parse_utc_timestamp_rejects_invalid_date_after_z_strip():
    # "not-a-date-Z" passes the endswith("Z") check but fails datetime.fromisoformat once
    # the Z is stripped and "+00:00" appended.
    with pytest.raises(pe.ManifestValidationError, match="is invalid"):
        pe._parse_utc_timestamp("not-a-date-Z", "label")


# --- call-key / identity derivation: deterministic, order-stable, and input-sensitive -------------------


def test_derive_execution_call_key_is_deterministic():
    key_a = pe.derive_execution_call_key(
        "x" * 64, planning_cell_key="cell-1", call_role="capability_qa", call_index=0)
    key_b = pe.derive_execution_call_key(
        "x" * 64, planning_cell_key="cell-1", call_role="capability_qa", call_index=0)
    assert key_a == key_b
    assert len(key_a) == 64


@pytest.mark.parametrize("changed_field", [
    "execution_identity_sha256", "planning_cell_key", "call_role", "call_index",
])
def test_derive_execution_call_key_changes_with_any_bound_input(changed_field):
    identity_sha256 = "x" * 64
    planning_cell_key = "cell-1"
    call_role = "capability_qa"
    call_index = 0
    key_base = pe.derive_execution_call_key(
        identity_sha256, planning_cell_key=planning_cell_key, call_role=call_role,
        call_index=call_index,
    )
    if changed_field == "execution_identity_sha256":
        identity_sha256 = "y" * 64
    elif changed_field == "planning_cell_key":
        planning_cell_key = "cell-2"
    elif changed_field == "call_role":
        call_role = "judge_verdict"
    else:
        call_index = 1
    key_changed = pe.derive_execution_call_key(
        identity_sha256, planning_cell_key=planning_cell_key, call_role=call_role,
        call_index=call_index,
    )
    assert key_base != key_changed


def test_derive_execution_identity_sha256_is_order_stable_over_key_order():
    identity_a = {"a": 1, "b": {"x": 1, "y": 2}}
    identity_b = {"b": {"y": 2, "x": 1}, "a": 1}
    assert pe.derive_execution_identity_sha256(identity_a) == (
        pe.derive_execution_identity_sha256(identity_b))


def test_new_and_renamed_identity_fields_change_the_execution_identity(
    baseline, synthetic_artifacts,
):
    shared = _shared_manifest_fields(
        baseline, synthetic_artifacts, stage="capability_preflight",
        stage_cap=STAGE_CAP_USD, cumulative_cap=CUMULATIVE_CAP_USD)
    base_kwargs: dict[str, Any] = dict(
        schema_version=shared["schema_version"],
        stage=shared["stage"],
        protocol_canonical_sha256=shared["protocol_canonical_sha256"],
        a1_amendment_canonical_sha256=shared["a1_amendment_canonical_sha256"],
        combined_ai_audit_canonical_sha256=shared["combined_ai_audit_canonical_sha256"],
        question_bank_bundle_sha256=shared["question_bank_bundle_sha256"],
        prompt_bundle_canonical_sha256=shared["prompt_bundle_canonical_sha256"],
        prompt_bundle_declared_status=shared["prompt_bundle_declared_status"],
        prompt_bundle_approval_artifact={
            "tracked_path": shared["prompt_bundle_approval_tracked_path"],
            "sha256": shared["prompt_bundle_approval_canonical_sha256"],
        },
        role_limits_and_request_settings_artifact=shared[
            "role_limits_and_request_settings_artifact"],
        provider_price_snapshot_canonical_sha256=shared[
            "provider_price_snapshot_canonical_sha256"],
        uv_lock_sha256=shared["uv_lock_sha256"],
        seed_policy=shared["seed_policy"],
        side_assignment_policy=shared["side_assignment_policy"],
        satisfied_prerequisites=shared["satisfied_prerequisites"],
        ledger=shared["ledger"],
        planning_cell_keys=baseline["planning_keys"],
        provider_call_inventory_entries=baseline["entries_without_key"],
        stage_cap_usd=shared["stage_cap_usd"],
        cumulative_cap_usd=shared["cumulative_cap_usd"],
        cost_forecast=shared["cost_forecast"],
        storage_policy=shared["storage_policy"],
        provider_reconciliation_evidence=shared["provider_reconciliation_evidence"],
        provider_refresh=shared["provider_refresh"],
        prior_attempt_closure=shared["prior_attempt_closure"],
        implementation_provenance=shared["implementation_provenance"],
    )
    base_identity_sha256 = pe.derive_execution_identity_sha256(
        pe.build_execution_identity(**base_kwargs))
    base_call_key = pe.derive_execution_call_key(
        base_identity_sha256, planning_cell_key=baseline["planning_keys"][0],
        call_role="capability_qa", call_index=0,
    )

    variants = {
        "prompt_bundle_declared_status": "owner_approved",
        "prompt_bundle_approval_artifact": {"tracked_path": "x", "sha256": "f" * 64},
        "role_limits_and_request_settings_artifact": {"path": "x", "sha256": "f" * 64},
        "cost_forecast": {"path": "x", "sha256": "f" * 64},
        "storage_policy": {"path": "x", "sha256": "f" * 64},
        "provider_reconciliation_evidence": {"path": "x", "sha256": "f" * 64},
        "provider_refresh": {"path": "x", "sha256": "f" * 64},
        "prior_attempt_closure": {"path": "x", "sha256": "f" * 64},
        "implementation_provenance": {"git_commit": "f" * 40, "code_bundle_sha256": "e" * 64},
    }
    for field, new_value in variants.items():
        changed_kwargs: dict[str, Any] = {**base_kwargs, field: new_value}
        changed_identity_sha256 = pe.derive_execution_identity_sha256(
            pe.build_execution_identity(**changed_kwargs))
        assert changed_identity_sha256 != base_identity_sha256, (
            f"{field} did not change the execution identity")
        changed_call_key = pe.derive_execution_call_key(
            changed_identity_sha256, planning_cell_key=baseline["planning_keys"][0],
            call_role="capability_qa", call_index=0,
        )
        assert changed_call_key != base_call_key, f"{field} did not change its execution_call_key"


# --- resume audit ------------------------------------------------------------------------------------------


REQUEST_METADATA_KEY = "request_fields_sha256"


def _reservation_event(attempt_id: str, entry, *, attempt: int = 0) -> dict:
    return {
        "status": "reserved", "attempt_id": attempt_id, "model": entry["model"],
        "kind": "capability_qa", "seed": entry["seed"], "attempt": attempt,
        "prompt_tokens": None, "completion_tokens": None, "estimated_tokens": 128,
        "cost_usd": 0.01,
        "metadata": {
            "execution_call_key": entry["execution_call_key"],
            REQUEST_METADATA_KEY: entry["request_fields_sha256"],
        },
    }


def _terminal_event(
    attempt_id: str, entry, *, status="success", attempt: int = 0, model=None, seed=None,
    request_fields_sha256=None, call_key=None,
) -> dict:
    return {
        "status": status, "attempt_id": attempt_id,
        "model": entry["model"] if model is None else model,
        "kind": "capability_qa", "seed": entry["seed"] if seed is None else seed,
        "attempt": attempt, "prompt_tokens": 64, "completion_tokens": 8,
        "estimated_tokens": 128, "cost_usd": 0.005 if status == "success" else 0.01,
        "metadata": {
            "execution_call_key": (
                entry["execution_call_key"] if call_key is None else call_key),
            REQUEST_METADATA_KEY: (
                entry["request_fields_sha256"]
                if request_fields_sha256 is None else request_fields_sha256),
        },
    }


def _output_row(entry, *, call_key=None) -> dict:
    return {
        "execution_call_key": entry["execution_call_key"] if call_key is None else call_key,
        "answer": "A", "raw_response": "CANDIDATE_A",
    }


@pytest.fixture(scope="module")
def validated_manifest(baseline, synthetic_artifacts):
    manifest_dict, _identity = build_manifest(baseline, synthetic_artifacts)
    # See the `manifest` fixture's docstring: cost_forecast's deep economics check is stubbed
    # here too (a module-scoped fixture can't use the function-scoped `monkeypatch` fixture, so
    # this uses pytest.MonkeyPatch.context() directly).
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pe.preflight_forecast, "validate_forecast_v3", lambda *a, **k: None)
        return pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_clean_resume_with_no_activity_is_all_todo(validated_manifest):
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=[])
    assert audit.disposition is pe.ResumeDisposition.TODO
    assert audit.counts == {
        "total": pe.EXPECTED_PROVIDER_CALL_COUNT, "todo": pe.EXPECTED_PROVIDER_CALL_COUNT,
        "complete": 0, "blocked_reconciliation": 0,
    }
    assert len(audit.todo_call_keys) == pe.EXPECTED_PROVIDER_CALL_COUNT
    assert audit.blockers == ()


def test_exact_completion_of_all_1059_calls_is_complete(validated_manifest):
    output_rows = []
    usage_events = []
    for entry in validated_manifest.provider_call_inventory:
        attempt_id = f"attempt-{entry['call_index']}"
        usage_events.append(_reservation_event(attempt_id, entry))
        usage_events.append(_terminal_event(attempt_id, entry, status="success"))
        output_rows.append(_output_row(entry))

    audit = pe.audit_resume(
        validated_manifest, output_rows=output_rows, usage_events=usage_events)
    assert audit.disposition is pe.ResumeDisposition.COMPLETE
    assert audit.counts == {
        "total": pe.EXPECTED_PROVIDER_CALL_COUNT, "todo": 0,
        "complete": pe.EXPECTED_PROVIDER_CALL_COUNT, "blocked_reconciliation": 0,
    }
    assert audit.todo_call_keys == ()
    assert audit.blockers == ()


def test_partial_resume_leaves_untouched_calls_as_todo(validated_manifest):
    entries = list(validated_manifest.provider_call_inventory)[:5]
    output_rows = []
    usage_events = []
    for entry in entries:
        attempt_id = f"attempt-{entry['call_index']}"
        usage_events.append(_reservation_event(attempt_id, entry))
        usage_events.append(_terminal_event(attempt_id, entry, status="success"))
        output_rows.append(_output_row(entry))

    audit = pe.audit_resume(
        validated_manifest, output_rows=output_rows, usage_events=usage_events)
    assert audit.disposition is pe.ResumeDisposition.TODO
    assert audit.counts["complete"] == 5
    assert audit.counts["todo"] == pe.EXPECTED_PROVIDER_CALL_COUNT - 5
    for entry in entries:
        assert audit.per_call[entry["execution_call_key"]] is pe.ResumeDisposition.COMPLETE


def test_unmatched_reservation_blocks_its_call(validated_manifest):
    entry = validated_manifest.provider_call_inventory[0]
    events = [_reservation_event("crash-attempt", entry)]
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert any("unmatched reservation" in blocker for blocker in audit.blockers)


def test_success_without_output_row_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[1]
    events = [
        _reservation_event("charged-attempt", entry),
        _terminal_event("charged-attempt", entry, status="success"),
    ]
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert any("no durable output row" in blocker for blocker in audit.blockers)


@pytest.mark.parametrize("status", ["unknown_charge", "charged_malformed"])
def test_unknown_or_malformed_charge_blocks(validated_manifest, status):
    entry = validated_manifest.provider_call_inventory[2]
    events = [
        _reservation_event("bad-attempt", entry),
        _terminal_event("bad-attempt", entry, status=status),
    ]
    audit = pe.audit_resume(
        validated_manifest, output_rows=[_output_row(entry)], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert any(status in blocker for blocker in audit.blockers)


def test_released_no_charge_stays_todo(validated_manifest):
    entry = validated_manifest.provider_call_inventory[3]
    events = [
        _reservation_event("released-attempt", entry),
        _terminal_event("released-attempt", entry, status="released_no_charge"),
    ]
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.TODO
    assert audit.per_call[entry["execution_call_key"]] is pe.ResumeDisposition.TODO
    assert audit.blockers == ()


def test_released_then_success_completes_the_call(validated_manifest):
    entry = validated_manifest.provider_call_inventory[4]
    events = [
        _reservation_event("first-attempt", entry, attempt=0),
        _terminal_event("first-attempt", entry, status="released_no_charge", attempt=0),
        _reservation_event("second-attempt", entry, attempt=1),
        _terminal_event("second-attempt", entry, status="success", attempt=1),
    ]
    audit = pe.audit_resume(
        validated_manifest, output_rows=[_output_row(entry)], usage_events=events)
    assert audit.per_call[entry["execution_call_key"]] is pe.ResumeDisposition.COMPLETE
    assert audit.disposition is pe.ResumeDisposition.TODO  # only one of 1060 calls resolved


def test_duplicate_output_rows_block(validated_manifest):
    entry = validated_manifest.provider_call_inventory[5]
    events = [
        _reservation_event("dup-attempt", entry),
        _terminal_event("dup-attempt", entry, status="success"),
    ]
    rows = [_output_row(entry), _output_row(entry)]
    audit = pe.audit_resume(validated_manifest, output_rows=rows, usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any("duplicate output row" in blocker for blocker in audit.blockers)


def test_output_row_for_unknown_call_key_blocks(validated_manifest):
    rows = [{"execution_call_key": "not-a-real-call-key", "answer": "A"}]
    audit = pe.audit_resume(validated_manifest, output_rows=rows, usage_events=[])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any("references unknown call key" in blocker for blocker in audit.blockers)


@pytest.mark.parametrize("bad_row", [
    "not-a-mapping", {"answer": "A"}, {"execution_call_key": ""}, {"execution_call_key": 5},
])
def test_malformed_output_rows_block(validated_manifest, bad_row):
    audit = pe.audit_resume(validated_manifest, output_rows=[bad_row], usage_events=[])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.blockers


def test_output_row_without_ledger_lifecycle_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[6]
    audit = pe.audit_resume(
        validated_manifest, output_rows=[_output_row(entry)], usage_events=[])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert any("no successful ledger lifecycle" in blocker for blocker in audit.blockers)


def test_ledger_event_with_unknown_call_key_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[7]
    events = [
        _reservation_event("phantom-attempt", entry, ),
    ]
    events[0]["metadata"] = {
        "execution_call_key": "not-in-the-manifest-inventory",
        REQUEST_METADATA_KEY: entry["request_fields_sha256"],
    }
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any("unmatched reservation" in blocker for blocker in audit.blockers)


def test_ledger_request_identity_model_mismatch_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[8]
    reservation = _reservation_event("mismatched-attempt", entry)
    reservation["model"] = "some/other-model"
    events = [reservation, _terminal_event("mismatched-attempt", entry, status="success")]
    audit = pe.audit_resume(
        validated_manifest, output_rows=[_output_row(entry)], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert any("request identity mismatches" in blocker for blocker in audit.blockers)


def test_ledger_request_identity_hash_mismatch_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[9]
    reservation = _reservation_event("wrong-request-hash", entry)
    reservation["metadata"][REQUEST_METADATA_KEY] = "b" * 64
    events = [reservation, _terminal_event("wrong-request-hash", entry, status="success")]
    audit = pe.audit_resume(
        validated_manifest, output_rows=[_output_row(entry)], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any("request identity mismatches" in blocker for blocker in audit.blockers)


def test_multiple_successful_charges_for_one_call_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[10]
    events = [
        _reservation_event("attempt-a", entry, attempt=0),
        _terminal_event("attempt-a", entry, status="success", attempt=0),
        _reservation_event("attempt-b", entry, attempt=1),
        _terminal_event("attempt-b", entry, status="success", attempt=1),
    ]
    audit = pe.audit_resume(
        validated_manifest, output_rows=[_output_row(entry)], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any("multiple successful charges" in blocker for blocker in audit.blockers)


@pytest.mark.parametrize("bad_event", [
    "not-a-mapping",
    {"status": "reserved", "metadata": {"execution_call_key": "x"}},  # missing attempt_id
    {
        "status": "totally_unknown", "attempt_id": "a", "model": "m", "seed": 1,
        "metadata": {"execution_call_key": "x"},
    },
])
def test_malformed_usage_events_block(validated_manifest, bad_event):
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=[bad_event])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.blockers


# --- regression: malformed/unrecognized events for a KNOWN call_key must block that call, ------
# --- not just the overall disposition (per_call / todo_call_keys / counts must agree) ----------


def test_usage_event_missing_attempt_id_blocks_its_own_call(validated_manifest):
    entry = validated_manifest.provider_call_inventory[11]
    event = {
        "status": "reserved", "model": entry["model"], "seed": entry["seed"],
        "metadata": {
            "execution_call_key": entry["execution_call_key"],
            REQUEST_METADATA_KEY: entry["request_fields_sha256"],
        },
    }
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=[event])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert entry["execution_call_key"] not in audit.todo_call_keys
    assert audit.counts["todo"] == pe.EXPECTED_PROVIDER_CALL_COUNT - 1
    assert any("missing attempt_id" in blocker for blocker in audit.blockers)


def test_usage_event_unknown_status_blocks_its_own_call(validated_manifest):
    entry = validated_manifest.provider_call_inventory[12]
    event = _terminal_event("weird-attempt", entry, status="mystery_status")
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=[event])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert entry["execution_call_key"] not in audit.todo_call_keys
    assert any("unknown status" in blocker for blocker in audit.blockers)


def test_usage_event_unknown_status_is_not_overwritten_by_a_later_legitimate_success(
    validated_manifest,
):
    # The bug this guards against: per_call defaulted to TODO for the malformed event, then
    # a later, separate, legitimate attempt for the SAME call_key succeeded cleanly and
    # flipped per_call all the way to COMPLETE, even though the anomalous event was still
    # sitting unresolved in blockers.
    entry = validated_manifest.provider_call_inventory[13]
    bad_event = _terminal_event("weird-attempt", entry, status="mystery_status")
    good_reservation = _reservation_event("good-attempt", entry, attempt=1)
    good_terminal = _terminal_event("good-attempt", entry, status="success", attempt=1)
    audit = pe.audit_resume(
        validated_manifest, output_rows=[_output_row(entry)],
        usage_events=[bad_event, good_reservation, good_terminal],
    )
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert entry["execution_call_key"] not in audit.todo_call_keys


# --- the five/six untested fail-closed ledger-reconciliation branches ---------------------------


def test_usage_event_malformed_execution_call_key_blocks(validated_manifest):
    event = {
        "status": "reserved", "attempt_id": "attempt-x", "model": "m", "seed": 0,
        "metadata": {"execution_call_key": "", REQUEST_METADATA_KEY: "a" * 64},
    }
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=[event])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any("malformed execution_call_key" in blocker for blocker in audit.blockers)


def test_duplicate_reservation_for_attempt_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[14]
    events = [
        _reservation_event("dup-reservation-attempt", entry),
        _reservation_event("dup-reservation-attempt", entry),
    ]
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any("duplicate reservation" in blocker for blocker in audit.blockers)


def test_terminal_event_without_matching_reservation_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[15]
    events = [_terminal_event("no-reservation-attempt", entry, status="success")]
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert any("no matching reservation" in blocker for blocker in audit.blockers)


def test_duplicate_terminal_event_for_attempt_blocks(validated_manifest):
    entry = validated_manifest.provider_call_inventory[16]
    events = [
        _reservation_event("dup-terminal-attempt", entry),
        _terminal_event("dup-terminal-attempt", entry, status="success"),
        _terminal_event("dup-terminal-attempt", entry, status="success"),
    ]
    audit = pe.audit_resume(
        validated_manifest, output_rows=[_output_row(entry)], usage_events=events)
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any("duplicate terminal event" in blocker for blocker in audit.blockers)


def test_terminal_event_call_key_disagreeing_with_its_reservation_blocks(validated_manifest):
    entry_a = validated_manifest.provider_call_inventory[17]
    entry_b = validated_manifest.provider_call_inventory[18]
    reservation = _reservation_event("cross-wired-attempt", entry_a)
    terminal = _terminal_event(
        "cross-wired-attempt", entry_a, status="success",
        call_key=entry_b["execution_call_key"],
    )
    audit = pe.audit_resume(
        validated_manifest, output_rows=[], usage_events=[reservation, terminal])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert audit.per_call[entry_a["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert audit.per_call[entry_b["execution_call_key"]] is (
        pe.ResumeDisposition.BLOCKED_RECONCILIATION)
    assert any("disagrees with its reservation" in blocker for blocker in audit.blockers)


def test_terminal_success_for_unknown_call_key_with_a_matching_reservation_blocks(
    validated_manifest,
):
    # Distinct from test_ledger_event_with_unknown_call_key_blocks, which supplies only a
    # reservation (no terminal) and so exercises the "unmatched reservation" branch instead.
    # This supplies both a reservation and a terminal for the same unknown call_key so the
    # event pair clears reconciliation and reaches the "unknown call key" check itself.
    fake_entry = {
        "execution_call_key": "not-in-the-manifest-inventory-f", "model": "m", "seed": 0,
        "request_fields_sha256": "c" * 64,
    }
    reservation = _reservation_event("unknown-call-key-attempt", fake_entry)
    terminal = _terminal_event("unknown-call-key-attempt", fake_entry, status="success")
    audit = pe.audit_resume(
        validated_manifest, output_rows=[], usage_events=[reservation, terminal])
    assert audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION
    assert any(
        "ledger event references unknown call key" in blocker for blocker in audit.blockers)


def test_ledger_events_without_execution_call_key_metadata_are_out_of_scope(validated_manifest):
    other_run_events = [
        {"status": "ledger_genesis", "schema_version": 1, "ledger_id": "x", "sequence": 0,
         "prev_event_hash": None},
        {"status": "success", "attempt_id": "unrelated-attempt", "model": "m", "seed": 1,
         "kind": "verdict", "attempt": 0, "prompt_tokens": 1, "completion_tokens": 1,
         "estimated_tokens": 2, "cost_usd": 0.001, "metadata": {}},
        {"status": "reserved", "attempt_id": "no-metadata-at-all", "model": "m", "seed": 1,
         "kind": "verdict", "attempt": 0, "cost_usd": 0.001},
    ]
    audit = pe.audit_resume(
        validated_manifest, output_rows=[], usage_events=other_run_events)
    assert audit.disposition is pe.ResumeDisposition.TODO
    assert audit.blockers == ()
    assert audit.counts["todo"] == pe.EXPECTED_PROVIDER_CALL_COUNT


@pytest.mark.parametrize("bad_rows,bad_events", [
    (None, []), ("rows", []), ([], None), ([], "events"),
])
def test_audit_resume_rejects_non_iterable_inputs(validated_manifest, bad_rows, bad_events):
    with pytest.raises(pe.ResumeAuditError):
        pe.audit_resume(validated_manifest, output_rows=bad_rows, usage_events=bad_events)


# --- module purity: no provider SDK import, no CLI entry point ------------------------------------------


def test_module_purity_no_provider_import_and_no_cli():
    script = (
        "import sys\n"
        "from rejudge import phase2_execution\n"
        "assert 'together' not in sys.modules, 'together SDK must not be imported'\n"
        "from rejudge import api_client\n"  # importing api_client separately is fine
        "assert 'together' not in sys.modules, 'together SDK must not be imported by api_client import alone'\n"
        "assert not hasattr(phase2_execution, 'main'), 'module must not define a CLI entry point'\n"
        "print('PURITY_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "PURITY_OK" in result.stdout


def test_module_defines_no_file_writing_helpers():
    import inspect

    source = inspect.getsource(pe)
    assert '"w"' not in source and "'w'" not in source
    assert "write_text" not in source
    assert "write_bytes" not in source


# =================================================================================================
# r3 stage-family bindings: carryforward_artifact / stage_family_ledger_artifact /
# ceiling_correction_artifact / list-shaped prior_attempt_closure / standing delegation /
# provider_call_inventory carried-forward exclusion + replacement marker.
# =================================================================================================


# --- ceiling_correction_artifact --------------------------------------------------------------


def test_ceiling_correction_gate_accepts_the_real_tracked_artifact():
    binding = {
        "path": str(CEILING_CORRECTION_PATH.relative_to(ROOT).as_posix()),
        "sha256": _canon_sha(CEILING_CORRECTION_PATH),
    }
    pe._validate_ceiling_correction_gate(binding, root=ROOT)  # must not raise


def _root_with_mutated_ceiling_correction(tmp_path: Path, mutate) -> tuple[Path, dict]:
    root = tmp_path
    _copy_tracked_data_files(root)
    path = root / "rejudge" / "phase2_ceiling_correction_2026-07-19.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return root, payload


def test_ceiling_correction_gate_rejects_wrong_ceiling_value(tmp_path):
    root, payload = _root_with_mutated_ceiling_correction(
        tmp_path, lambda p: p.__setitem__("correct_cumulative_project_ceiling_usd", 1709.25))
    binding = {"path": "rejudge/phase2_ceiling_correction_2026-07-19.json",
               "sha256": pe.canonical_sha256(payload)}
    with pytest.raises(
        pe.ManifestValidationError,
        match="correct_cumulative_project_ceiling_usd must be exactly",
    ):
        pe._validate_ceiling_correction_gate(binding, root=root)


def test_ceiling_correction_gate_rejects_wrong_corrects_tracked_path(tmp_path):
    root, payload = _root_with_mutated_ceiling_correction(
        tmp_path, lambda p: p["corrects"].__setitem__("tracked_path", "somewhere/else.json"))
    binding = {"path": "rejudge/phase2_ceiling_correction_2026-07-19.json",
               "sha256": pe.canonical_sha256(payload)}
    with pytest.raises(
        pe.ManifestValidationError, match="corrects.tracked_path must be exactly",
    ):
        pe._validate_ceiling_correction_gate(binding, root=root)


def test_ceiling_correction_gate_rejects_hash_drift(tmp_path):
    root, payload = _root_with_mutated_ceiling_correction(tmp_path, lambda p: None)
    binding = {"path": "rejudge/phase2_ceiling_correction_2026-07-19.json",
               "sha256": _flip_hex_digest(pe.canonical_sha256(payload))}
    with pytest.raises(pe.ManifestValidationError, match="ceiling_correction_artifact hash drift"):
        pe._validate_ceiling_correction_gate(binding, root=root)


def test_ceiling_correction_gate_rejects_unpinned_path():
    binding = {"path": "docs/phase2-decision-proposal.md", "sha256": "a" * 64}
    with pytest.raises(
        pe.ManifestValidationError,
        match="must resolve to the frozen, git-tracked artifact",
    ):
        pe._validate_ceiling_correction_gate(binding, root=ROOT)


# --- standing delegation record ------------------------------------------------------------------


def test_standing_delegation_record_accepts_the_real_tracked_artifact():
    pe._validate_standing_delegation_record(
        STANDING_DELEGATION_PATH, stage="capability_preflight", stage_cap_usd=15.0,
        cumulative_cap_usd=1500.0)  # must not raise


def _standing_delegation_with(tmp_path: Path, mutate) -> Path:
    payload = json.loads(STANDING_DELEGATION_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    path = tmp_path / "standing_delegation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_standing_delegation_record_rejects_tampered_exact_quote(tmp_path):
    path = _standing_delegation_with(
        tmp_path, lambda p: p.__setitem__("exact_quote", "Sure, go ahead"))
    with pytest.raises(pe.ExecutionAuthorityError, match="exact_quote drifted"):
        pe._validate_standing_delegation_record(
            path, stage="capability_preflight", stage_cap_usd=15.0, cumulative_cap_usd=1500.0)


def test_standing_delegation_record_rejects_wrong_approver(tmp_path):
    path = _standing_delegation_with(tmp_path, lambda p: p.__setitem__("approver", "Someone Else"))
    with pytest.raises(pe.ExecutionAuthorityError, match="approver drifted"):
        pe._validate_standing_delegation_record(
            path, stage="capability_preflight", stage_cap_usd=15.0, cumulative_cap_usd=1500.0)


def test_standing_delegation_record_rejects_missing_canary_and_main_exclusions(tmp_path):
    path = _standing_delegation_with(
        tmp_path,
        lambda p: p.__setitem__("still_requires_explicit_owner_approval", ["nothing in particular"]))
    with pytest.raises(pe.ExecutionAuthorityError, match="must mention both canary and main"):
        pe._validate_standing_delegation_record(
            path, stage="capability_preflight", stage_cap_usd=15.0, cumulative_cap_usd=1500.0)


def test_standing_delegation_record_rejects_missing_transport_relaunch_grant(tmp_path):
    path = _standing_delegation_with(
        tmp_path,
        lambda p: p["scope"].__setitem__("granted", ["something unrelated entirely"]))
    with pytest.raises(pe.ExecutionAuthorityError, match="transport-only relaunch grant"):
        pe._validate_standing_delegation_record(
            path, stage="capability_preflight", stage_cap_usd=15.0, cumulative_cap_usd=1500.0)


def test_standing_delegation_record_rejects_stage_cap_mismatch(tmp_path):
    with pytest.raises(pe.ExecutionAuthorityError, match="does not match this manifest's"):
        pe._validate_standing_delegation_record(
            STANDING_DELEGATION_PATH, stage="capability_preflight", stage_cap_usd=999.0,
            cumulative_cap_usd=1500.0)


def test_standing_delegation_record_rejects_cumulative_cap_mismatch(tmp_path):
    with pytest.raises(pe.ExecutionAuthorityError, match="does not match this manifest's"):
        pe._validate_standing_delegation_record(
            STANDING_DELEGATION_PATH, stage="capability_preflight", stage_cap_usd=15.0,
            cumulative_cap_usd=999.0)


# --- authorization: standing delegation as an ALTERNATIVE approval_basis -----------------------


def test_authorization_accepts_the_standing_delegation_as_approval_basis(manifest):
    manifest_dict, identity_sha256 = manifest
    standing_sha256 = hashlib.sha256(STANDING_DELEGATION_PATH.read_bytes()).hexdigest()
    authorization = matching_authorization(
        identity_sha256,
        approval_basis_tracked_path=str(STANDING_DELEGATION_PATH.relative_to(ROOT).as_posix()),
        approval_basis_sha256=standing_sha256,
        approved_at_utc=pe.STANDING_DELEGATION_APPROVED_AT_UTC,
    )
    validated = pe.validate_execution_manifest(
        manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
        require_authorized=True,
    )
    assert validated.authorized is True


def test_authorization_rejects_standing_basis_with_original_approved_at_utc(manifest):
    """The two delegation bases each pin their OWN approved_at_utc; swapping one basis's
    timestamp onto the other must fail closed, never silently accepted as "close enough"."""
    manifest_dict, identity_sha256 = manifest
    standing_sha256 = hashlib.sha256(STANDING_DELEGATION_PATH.read_bytes()).hexdigest()
    authorization = matching_authorization(
        identity_sha256,
        approval_basis_tracked_path=str(STANDING_DELEGATION_PATH.relative_to(ROOT).as_posix()),
        approval_basis_sha256=standing_sha256,
        approved_at_utc=pe.PREFLIGHT_DELEGATION_APPROVED_AT_UTC,  # wrong basis's timestamp
    )
    with pytest.raises(
        pe.ExecutionAuthorityError, match="must equal the cited standing delegation's own",
    ):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_still_rejects_a_third_unpinned_delegation_path(manifest, tmp_path):
    manifest_dict, identity_sha256 = manifest
    missing = tmp_path / "not_a_real_delegation.json"
    missing.write_text("{}", encoding="utf-8")
    authorization = matching_authorization(
        identity_sha256, approval_basis_tracked_path=str(missing), approval_basis_sha256="a" * 64,
    )
    with pytest.raises(
        pe.ExecutionAuthorityError,
        match="must resolve to either the frozen, git-tracked preflight",
    ):
        pe.validate_execution_manifest(
            manifest_dict, project_root=GREEN_ROOT, authorization=authorization,
            require_authorized=True,
        )


# --- carried-forward exclusion + replacement marker in provider_call_inventory -----------------


def test_provider_call_inventory_excludes_the_carried_forward_planning_cell(manifest):
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)
    planning_keys = {e["planning_cell_key"] for e in validated.provider_call_inventory}
    assert phase2_stage_family.QWEN_PLANNING_CELL_KEY not in planning_keys


def test_provider_call_inventory_marks_exactly_the_gemma_replacement_entry(manifest):
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)
    marked = [
        e for e in validated.provider_call_inventory
        if e.get(pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY) is True
    ]
    assert len(marked) == 1
    assert marked[0]["planning_cell_key"] == phase2_stage_family.GEMMA_PLANNING_CELL_KEY


def test_reintroducing_the_carried_forward_planning_cell_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    # Overwrite the replacement (gemma) entry's planning_cell_key with the carried-forward
    # (qwen) key -- an inventory that tries to re-issue the already-complete carried-forward
    # cell must fail closed, never silently accepted.
    for entry in entries:
        if entry.get(pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY) is True:
            entry["planning_cell_key"] = phase2_stage_family.QWEN_PLANNING_CELL_KEY
            del entry[pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY]
            break
    with pytest.raises(
        pe.ManifestValidationError, match="must never be re-issued",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_replacement_marker_on_the_wrong_cell_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[0][pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY] = True
    if entries[0]["planning_cell_key"] == phase2_stage_family.GEMMA_PLANNING_CELL_KEY:
        pytest.skip("entries[0] happens to already be the real replacement cell")
    with pytest.raises(
        pe.ManifestValidationError,
        match="carries replacement_for_closed_ambiguous=true but its planning cell is not",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_replacement_marker_set_to_false_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    for entry in entries:
        if entry.get(pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY) is True:
            entry[pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY] = False
            break
    with pytest.raises(
        pe.ManifestValidationError, match="must be exactly true when present",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_missing_replacement_marker_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    for entry in entries:
        if entry.get(pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY) is True:
            del entry[pe.CALL_ENTRY_REPLACEMENT_MARKER_KEY]
            break
    with pytest.raises(
        pe.ManifestValidationError, match="is missing the required replacement entry",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_expected_provider_call_count_mismatch_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["expected_provider_call_count"] = pe.EXPECTED_PROVIDER_CALL_COUNT - 1
    with pytest.raises(
        pe.ManifestValidationError, match="expected_provider_call_count must be exactly",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- attempt_available_cap_usd cross-check against the stage-family ledger ---------------------


def test_attempt_available_cap_usd_mismatch_against_ledger_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["attempt_available_cap_usd"] = "1.00000000"  # disagrees with the real ledger
    with pytest.raises(
        pe.ManifestValidationError,
        match="does not equal the bound stage-family ledger's r3_available_cap_usd exactly",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_attempt_available_cap_usd_malformed_decimal_string_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["attempt_available_cap_usd"] = "14.97"  # not exactly 8 fractional digits
    with pytest.raises(
        pe.ManifestValidationError,
        match="attempt_available_cap_usd must be a decimal string with exactly 8",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_validated_manifest_exposes_attempt_available_cap_usd_as_decimal(manifest):
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)
    assert validated.attempt_available_cap_usd == Decimal(phase2_stage_family.R3_AVAILABLE_CAP_USD)
    assert isinstance(validated.attempt_available_cap_usd, Decimal)


# --- prior_attempt_closure: now a list (r1 abort closure, r2 closure) --------------------------


def test_prior_attempt_closure_must_be_a_two_entry_list(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prior_attempt_closure"] = manifest_dict["prior_attempt_closure"][:1]
    with pytest.raises(
        pe.ManifestValidationError, match="must contain exactly 2 entries",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_prior_attempt_closure_r2_entry_hash_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prior_attempt_closure"][1]["sha256"] = _flip_hex_digest(
        manifest_dict["prior_attempt_closure"][1]["sha256"])
    with pytest.raises(
        pe.ManifestValidationError, match=r"prior_attempt_closure\[1\] \(r2 closure\) hash drift",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_prior_attempt_closure_r2_entry_unpinned_path_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prior_attempt_closure"][1] = {
        "path": "rejudge/phase2_preflight_abort_closure_2026-07-19.json",  # a real file, wrong one
        "sha256": "a" * 64,
    }
    with pytest.raises(
        pe.ManifestValidationError,
        match=r"prior_attempt_closure\[1\].path must resolve to the frozen",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- carryforward_artifact / stage_family_ledger_artifact ---------------------------------------


def test_carryforward_artifact_hash_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["carryforward_artifact"]["sha256"] = _flip_hex_digest(
        manifest_dict["carryforward_artifact"]["sha256"])
    with pytest.raises(pe.ManifestValidationError, match="carryforward_artifact hash drift"):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


def test_stage_family_ledger_artifact_hash_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["stage_family_ledger_artifact"]["sha256"] = _flip_hex_digest(
        manifest_dict["stage_family_ledger_artifact"]["sha256"])
    with pytest.raises(
        pe.ManifestValidationError, match="stage_family_ledger_artifact hash drift",
    ):
        pe.validate_execution_manifest(manifest_dict, project_root=GREEN_ROOT)


# --- build_execution_identity: backward-compatible optional r3 fields --------------------------


def _minimal_identity_kwargs(**overrides) -> dict:
    base = dict(
        schema_version="v1", stage="capability_preflight", protocol_canonical_sha256="a" * 64,
        a1_amendment_canonical_sha256="a" * 64, combined_ai_audit_canonical_sha256="a" * 64,
        question_bank_bundle_sha256="a" * 64, prompt_bundle_canonical_sha256="a" * 64,
        prompt_bundle_declared_status="candidate_pending_owner_methods_review",
        prompt_bundle_approval_artifact={"tracked_path": "x", "sha256": "a" * 64},
        role_limits_and_request_settings_artifact={"path": "x", "sha256": "a" * 64},
        provider_price_snapshot_canonical_sha256="a" * 64, uv_lock_sha256="a" * 64,
        seed_policy="p", side_assignment_policy="s", satisfied_prerequisites={},
        ledger={"path": "x", "ledger_identity": "y"}, planning_cell_keys=["k1", "k2"],
        provider_call_inventory_entries=[], stage_cap_usd=15.0, cumulative_cap_usd=1500.0,
        cost_forecast={"path": "x", "sha256": "a" * 64},
        storage_policy={"path": "x", "sha256": "a" * 64},
        provider_reconciliation_evidence={"path": "x", "sha256": "a" * 64},
        provider_refresh={"path": "x", "sha256": "a" * 64},
        prior_attempt_closure={"path": "x", "sha256": "a" * 64},
        implementation_provenance={"git_commit": "a" * 40, "code_bundle_sha256": "b" * 64},
    )
    base.update(overrides)
    return base


def test_build_execution_identity_omits_r3_fields_when_not_passed():
    """A historical (r1/r2) caller that never passes the new r3-only kwargs gets EXACTLY the old
    identity dict shape: execution_identity_sha256 for those already-committed, frozen manifests
    must never be retroactively changed by this function gaining r3-only fields."""
    identity = pe.build_execution_identity(**_minimal_identity_kwargs())
    assert "carryforward_artifact" not in identity
    assert "stage_family_ledger_artifact" not in identity
    assert "ceiling_correction_artifact" not in identity
    assert "attempt_available_cap_usd" not in identity
    assert isinstance(identity["prior_attempt_closure"], dict)


def test_build_execution_identity_includes_r3_fields_when_passed():
    identity = pe.build_execution_identity(
        **_minimal_identity_kwargs(
            prior_attempt_closure=[{"path": "r1", "sha256": "a" * 64},
                                    {"path": "r2", "sha256": "b" * 64}],
        ),
        carryforward_artifact={"path": "cf", "sha256": "c" * 64},
        stage_family_ledger_artifact={"path": "ledger", "sha256": "d" * 64},
        ceiling_correction_artifact={"path": "ceiling", "sha256": "e" * 64},
        attempt_available_cap_usd="14.97676869",
    )
    assert identity["carryforward_artifact"] == {"path": "cf", "sha256": "c" * 64}
    assert identity["stage_family_ledger_artifact"] == {"path": "ledger", "sha256": "d" * 64}
    assert identity["ceiling_correction_artifact"] == {"path": "ceiling", "sha256": "e" * 64}
    assert identity["attempt_available_cap_usd"] == "14.97676869"
    assert isinstance(identity["prior_attempt_closure"], list)
    assert identity["prior_attempt_closure"] == [
        {"path": "r1", "sha256": "a" * 64}, {"path": "r2", "sha256": "b" * 64},
    ]


def test_build_execution_identity_r3_fields_change_the_hash():
    old_identity = pe.build_execution_identity(**_minimal_identity_kwargs())
    new_identity = pe.build_execution_identity(
        **_minimal_identity_kwargs(), carryforward_artifact={"path": "cf", "sha256": "c" * 64})
    assert (pe.derive_execution_identity_sha256(old_identity)
            != pe.derive_execution_identity_sha256(new_identity))
