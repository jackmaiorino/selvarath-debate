"""Deterministic offline materialization for the Qwen3.5-9B weak-slot successor identity.

Second recovery generation (2026-08-25): Together delisted google/gemma-3n-E4B-it from its
serverless catalog mid-canary (r11 halt observation). The owner-approved amendment 4
substitutes Qwen/Qwen3.5-9B, the only genuinely small non-Gemma serverless chat model in the
authenticated catalog, with google/gemma-4-E4B-it pre-approved as the ordered fallback.
This module mirrors :mod:`rejudge.phase3_v3_recovery_materialization` for that event.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase3_plan, phase3_v3_materialization
from rejudge.phase2_execution import canonical_sha256


PRIOR_PROTOCOL_PATH = Path("rejudge/phase3_protocol_v3_r3.json")
AMENDMENT_PATH = Path("rejudge/phase3_v3_amendment4_gemma3n_replacement_2026-08-25.json")
HALT_OBSERVATION_PATH = Path("rejudge/phase3_v3_r11_gemma3n_delisting_halt_2026-08-25.json")
PROVIDER_CATALOG_PATH = Path(
    "rejudge/output/phase3_v3r4_provider_models_2026-08-25T2130Z.json")
SERVERLESS_ENDPOINTS_PATH = Path(
    "rejudge/output/phase3_v3r4_serverless_endpoints_2026-08-25T2130Z.json")
SCREENING_PATH = Path("rejudge/phase3_v3_qwen35_9b_screening_2026-08-25.json")
DEFAULT_PROTOCOL_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_r4.json")
DEFAULT_PROTOCOL_PIN_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_pin_r4.json")

PRIOR_PROTOCOL_CANONICAL_SHA256 = phase3_plan.FROZEN_PROTOCOL_V3_R3_CANONICAL_SHA256
AMENDMENT_CANONICAL_SHA256 = phase3_plan.FROZEN_V3_RECOVERY2_AMENDMENT_CANONICAL_SHA256
HALT_OBSERVATION_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_V3_R11_HALT_OBSERVATION_CANONICAL_SHA256)
PROVIDER_CATALOG_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_V3R4_PROVIDER_CATALOG_CANONICAL_SHA256)
SERVERLESS_ENDPOINTS_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_V3R4_SERVERLESS_ENDPOINTS_CANONICAL_SHA256)
SCREENING_CANONICAL_SHA256 = phase3_plan.FROZEN_QWEN35_9B_SCREENING_CANONICAL_SHA256

REMOVED_MODEL = phase3_plan.PHASE3_V3_RECOVERY2_REPLACED_JUDGE
ADDED_MODEL = phase3_plan.PHASE3_V3_RECOVERY2_REPLACEMENT_JUDGE
FINAL_ROSTER = phase3_plan.PHASE3_V3_RECOVERY2_BASE_JUDGES
PRIOR_ACCOUNTED_SPEND_USD = 8.167501109999998
APPROVAL_OPTION = "Substitute + fallbacks (Recommended)"


class Recovery2MaterializationError(ValueError):
    """Raised when delisting evidence cannot support a fresh successor protocol."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Recovery2MaterializationError(f"could not load {path}: {exc}") from exc


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise Recovery2MaterializationError(f"{label} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bound_json(root: Path, path: str | Path, expected_sha: str, label: str) -> Any:
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    payload = _load_json(target)
    if canonical_sha256(payload) != expected_sha:
        raise Recovery2MaterializationError(f"{label} canonical hash drifted")
    return payload


def validate_halt_observation(
    observation: Mapping[str, Any], *, verify_archive: bool = False,
) -> None:
    if canonical_sha256(observation) != HALT_OBSERVATION_CANONICAL_SHA256:
        raise Recovery2MaterializationError("halt observation canonical hash drifted")
    if observation.get("status") != "halted_by_orchestrator_after_roster_model_delisting":
        raise Recovery2MaterializationError("halt observation status drifted")
    if (observation.get("execution_authorized") is not False
            or observation.get("main_run_spend_authorized") is not False):
        raise Recovery2MaterializationError("halt observation cannot authorize spend")
    if observation.get("run_id") != "phase3-v3-476792b58e273b48":
        raise Recovery2MaterializationError("halt observation binds the wrong prior run")
    evidence = _object(observation.get("delisting_evidence"), "delisting evidence")
    without = _object(evidence.get("catalog_without_model"), "catalog without model")
    if (without.get("contains_gemma_3n") is not False
            or without.get("canonical_sha256") != PROVIDER_CATALOG_CANONICAL_SHA256):
        raise Recovery2MaterializationError("delisting catalog evidence drifted")
    accounting = _object(
        observation.get("aggregate_accounting"), "aggregate cap accounting")
    if (float(accounting.get("cap_usd", -1)) != 60.0
            or not math.isclose(
                float(accounting.get("aggregate_accounted_spend_usd", -1)),
                PRIOR_ACCOUNTED_SPEND_USD, rel_tol=0.0, abs_tol=1e-15)):
        raise Recovery2MaterializationError("halt aggregate cap accounting drifted")
    if verify_archive:
        ledger = _object(observation.get("usage_ledger"), "halt usage ledger")
        path = Path(str(ledger.get("path")))
        if not path.is_file():
            raise Recovery2MaterializationError(f"halted ledger is missing: {path}")
        if _file_sha256(path) != ledger.get("raw_sha256"):
            raise Recovery2MaterializationError("halted ledger raw hash drifted")


def validate_amendment(
    amendment: Mapping[str, Any], *, project_root: str | Path | None = None,
) -> None:
    if canonical_sha256(amendment) != AMENDMENT_CANONICAL_SHA256:
        raise Recovery2MaterializationError("recovery2 amendment canonical hash drifted")
    if amendment.get("schema_version") != "phase3_v3_roster_amendment_v3":
        raise Recovery2MaterializationError("unsupported recovery2 amendment schema")
    owner = _object(amendment.get("owner_approval"), "amendment owner approval")
    if (owner.get("approver") != "Jack Maiorino"
            or owner.get("selected_option") != APPROVAL_OPTION):
        raise Recovery2MaterializationError("recovery2 owner approval drifted")
    replacement = _object(amendment.get("replacement"), "amendment replacement")
    if replacement.get("removed_model") != REMOVED_MODEL:
        raise Recovery2MaterializationError("recovery2 removed model drifted")
    candidates = replacement.get("ordered_candidates")
    if (not isinstance(candidates, list) or not candidates
            or candidates[0].get("model_id") != ADDED_MODEL):
        raise Recovery2MaterializationError("recovery2 candidate order drifted")
    if (amendment.get("execution_authorized") is not False
            or amendment.get("canary_spend_authorized") is not False
            or amendment.get("main_spend_authorized") is not False):
        raise Recovery2MaterializationError(
            "recovery2 amendment cannot authorize paid execution")
    aggregate = _object(amendment.get("aggregate_cap"), "amendment aggregate cap")
    if (float(aggregate.get("approved_cap_usd", -1)) != 60.0
            or not math.isclose(
                float(aggregate.get("prior_accounted_spend_usd", -1)),
                PRIOR_ACCOUNTED_SPEND_USD, rel_tol=0.0, abs_tol=1e-15)
            or aggregate.get("reset_on_successor") is not False
            or aggregate.get("main_run_spend_authorized") is not False):
        raise Recovery2MaterializationError("recovery2 aggregate cap drifted")
    if project_root is not None:
        root = Path(project_root)
        _bound_json(root, PROVIDER_CATALOG_PATH, PROVIDER_CATALOG_CANONICAL_SHA256,
                    "recovery2 provider catalog")
        _bound_json(root, SERVERLESS_ENDPOINTS_PATH,
                    SERVERLESS_ENDPOINTS_CANONICAL_SHA256, "recovery2 endpoints")
        screening = _bound_json(
            root, SCREENING_PATH, SCREENING_CANONICAL_SHA256, "substitute screening")
        match = _object(screening.get("prompt_token_match"), "screening token match")
        if (screening.get("model_id") != ADDED_MODEL
                or match.get("exact_match_variant") != "default"):
            raise Recovery2MaterializationError(
                "substitute screening does not establish exact prompt-token equivalence")


def materialize_recovery2_protocol(
    prior_protocol: Mapping[str, Any],
    amendment: Mapping[str, Any],
    halt_observation: Mapping[str, Any],
    *,
    project_root: str | Path | None = None,
    verify_archive: bool = False,
) -> dict[str, Any]:
    """Create a fresh non-authorizing protocol by replacing only the delisted judge."""
    phase3_plan.validate_protocol(prior_protocol)
    if canonical_sha256(prior_protocol) != PRIOR_PROTOCOL_CANONICAL_SHA256:
        raise Recovery2MaterializationError("prior v3 r3 protocol canonical hash drifted")
    validate_amendment(amendment, project_root=project_root)
    validate_halt_observation(halt_observation, verify_archive=verify_archive)

    amendment_sha = canonical_sha256(amendment)
    halt_sha = canonical_sha256(halt_observation)
    protocol = deepcopy(dict(prior_protocol))
    protocol["protocol_id"] = "phase3_budget_knob_2026_08_25_v3r4"
    question_bank_sha = protocol["source_bindings"]["question_bank_bundle_sha256"]
    protocol["cell_key_namespace"] = (
        "phase3-budget-knob-2026-08-25-v3r4."
        f"rr-{halt_sha[:12]}.ra-{amendment_sha[:12]}.qb-{question_bank_sha[:12]}")
    protocol["planning_cell_identity"] = {
        "status": "planning_only_not_executable",
        "question_bank_bundle_sha256": question_bank_sha,
        "roster_resolution_sha256": halt_sha,
        "roster_amendment_sha256": amendment_sha,
        "execution_key_requirement": (
            "the run manifest must pin this recovery protocol, fresh roster, exact tokenizer "
            "manifest, live serverless price snapshot, prior aggregate spend, seeds, and outputs"
        ),
    }
    protocol["authorization"] = {
        "design_scope_approved": True,
        "approval_record": AMENDMENT_PATH.as_posix(),
        "amendment_record": AMENDMENT_PATH.as_posix(),
        "approver": "Jack Maiorino",
        "approved_on_date": "2026-08-25",
        "aggregate_cap_usd": 60.0,
        "prior_accounted_spend_usd": PRIOR_ACCOUNTED_SPEND_USD,
        "canary_spend_authorized": False,
        "main_run_spend_authorized": False,
    }
    protocol["sources"].update({
        "prior_protocol_r3": PRIOR_PROTOCOL_PATH.as_posix(),
        "recovery2_amendment": AMENDMENT_PATH.as_posix(),
        "recovery2_halt_observation": HALT_OBSERVATION_PATH.as_posix(),
        "recovery2_provider_catalog": PROVIDER_CATALOG_PATH.as_posix(),
        "recovery2_serverless_endpoints": SERVERLESS_ENDPOINTS_PATH.as_posix(),
        "recovery2_substitute_screening": SCREENING_PATH.as_posix(),
    })
    bindings = protocol["source_bindings"]["canonical_json_sha256"]
    bindings.update({
        PRIOR_PROTOCOL_PATH.as_posix(): PRIOR_PROTOCOL_CANONICAL_SHA256,
        AMENDMENT_PATH.as_posix(): AMENDMENT_CANONICAL_SHA256,
        HALT_OBSERVATION_PATH.as_posix(): HALT_OBSERVATION_CANONICAL_SHA256,
        PROVIDER_CATALOG_PATH.as_posix(): PROVIDER_CATALOG_CANONICAL_SHA256,
        SERVERLESS_ENDPOINTS_PATH.as_posix(): SERVERLESS_ENDPOINTS_CANONICAL_SHA256,
        SCREENING_PATH.as_posix(): SCREENING_CANONICAL_SHA256,
    })
    protocol["model_registry"]["models"].pop(REMOVED_MODEL)
    protocol["model_registry"]["models"][ADDED_MODEL] = {
        "billed_roles": ["judge_query", "judge_verdict", "capability_qa"]
    }
    protocol["roster_resolution"] = {
        "tracked_path": HALT_OBSERVATION_PATH.as_posix(),
        "canonical_sha256": halt_sha,
        "outcome": "excluded_provider_unavailable",
        "resolved_at_utc": amendment["decided_at_utc"],
        "amendment_tracked_path": AMENDMENT_PATH.as_posix(),
        "amendment_canonical_sha256": amendment_sha,
        "replacement": {
            "removed_model": REMOVED_MODEL,
            "added_model": ADDED_MODEL,
        },
    }
    protocol["roster"]["judges_final"] = list(FINAL_ROSTER)
    protocol["roster"]["final_size"] = len(FINAL_ROSTER)
    protocol["roster"]["replacement_policy"] = (
        "one owner-approved post-delisting serverless replacement is bound above, with "
        "google/gemma-4-E4B-it pre-approved as the ordered fallback under the amendment-4 "
        "admission gates; no other replacement is permitted after successor materialization")
    protocol["process_commitments"]["environmental_interruption"] = (
        "void the interrupted measurement and restart under a fresh successor identity; "
        "preserve the old archive only for evidence and aggregate cap accounting")
    protocol["supersedes"] = {
        "protocol_id": prior_protocol["protocol_id"],
        "canonical_sha256": PRIOR_PROTOCOL_CANONICAL_SHA256,
        "reason": (
            "the prior formal measurement halted when Together delisted gemma-3n-E4B from "
            "serverless; Qwen3.5-9B is bound as the owner-approved screened replacement")
    }
    protocol["non_claims"] = [
        "This protocol does not authorize provider calls, canary spend, main spend, or GPU work.",
        "No measurement row from any halted attempt satisfies a recovery canary slot.",
        "The prior accounted spend remains charged against the same 60 USD aggregate cap.",
    ]
    protocol.pop("protocol_content_sha256", None)
    protocol["protocol_content_sha256"] = canonical_sha256(protocol)
    phase3_plan.validate_protocol(protocol)
    return protocol


def load_materialization_inputs(
    project_root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(project_root)
    prior = _bound_json(
        root, PRIOR_PROTOCOL_PATH, PRIOR_PROTOCOL_CANONICAL_SHA256, "prior protocol")
    amendment = _bound_json(
        root, AMENDMENT_PATH, AMENDMENT_CANONICAL_SHA256, "recovery2 amendment")
    halt = _bound_json(
        root, HALT_OBSERVATION_PATH, HALT_OBSERVATION_CANONICAL_SHA256,
        "halt observation",
    )
    if not all(isinstance(item, dict) for item in (prior, amendment, halt)):
        raise Recovery2MaterializationError("recovery2 inputs must be JSON objects")
    return prior, amendment, halt


def build_protocol_pin(
    protocol: Mapping[str, Any], *, protocol_tracked_path: str | Path,
) -> dict[str, Any]:
    """Reuse the v3 full-document pin after recovery2 bindings enter the protocol hash."""
    return phase3_v3_materialization.build_protocol_pin(
        protocol, protocol_tracked_path=protocol_tracked_path)
