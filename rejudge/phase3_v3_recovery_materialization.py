"""Deterministic offline materialization for the Qwen 3.8 successor identity."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase3_plan, phase3_v3_inputs, phase3_v3_materialization
from rejudge.phase2_execution import canonical_sha256


PRIOR_PROTOCOL_PATH = Path("rejudge/phase3_protocol_v3_r2.json")
AMENDMENT_PATH = Path("rejudge/phase3_v3_amendment2_qwen3_8_replacement_2026-08-24.json")
HALT_OBSERVATION_PATH = Path("rejudge/phase3_v3_r5_provider_halt_2026-08-24.json")
PROVIDER_CATALOG_PATH = Path(
    "rejudge/output/phase3_v3r2_provider_models_2026-08-24T2321Z.json")
SERVERLESS_ENDPOINTS_PATH = Path(
    "rejudge/output/phase3_v3r2_serverless_endpoints_2026-08-24T2321Z.json")
PROVIDER_TEMPLATE_PATH = Path(
    "rejudge/phase3_v3r2_qwen38_provider_chat_template_2026-08-24.json")
DEFAULT_PROTOCOL_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_r3.json")
DEFAULT_PROTOCOL_PIN_OUTPUT_PATH = Path("rejudge/phase3_protocol_v3_pin_r3.json")

PRIOR_PROTOCOL_CANONICAL_SHA256 = phase3_plan.FROZEN_PROTOCOL_V3_R2_CANONICAL_SHA256
AMENDMENT_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_V3_RECOVERY_AMENDMENT_CANONICAL_SHA256)
HALT_OBSERVATION_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_V3_R5_HALT_OBSERVATION_CANONICAL_SHA256)
PROVIDER_CATALOG_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_V3R2_PROVIDER_CATALOG_CANONICAL_SHA256)
SERVERLESS_ENDPOINTS_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_V3R2_SERVERLESS_ENDPOINTS_CANONICAL_SHA256)
PROVIDER_TEMPLATE_CANONICAL_SHA256 = (
    phase3_plan.FROZEN_V3R2_QWEN38_PROVIDER_TEMPLATE_CANONICAL_SHA256)

REMOVED_MODEL = phase3_plan.PHASE3_V3_RECOVERY_REPLACED_JUDGE
ADDED_MODEL = phase3_plan.PHASE3_V3_RECOVERY_REPLACEMENT_JUDGE
FINAL_ROSTER = phase3_plan.PHASE3_V3_RECOVERY_BASE_JUDGES
APPROVAL_QUESTION = (
    "Approve replacing Qwen 3.5 with Qwen 3.8 and minting a fresh successor identity "
    "under the same $60 cap with no main spend?")


class RecoveryMaterializationError(ValueError):
    """Raised when recovery evidence cannot support a fresh successor protocol."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryMaterializationError(f"could not load {path}: {exc}") from exc


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RecoveryMaterializationError(f"{label} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bound_json(
    root: Path, path: str | Path, expected_sha: str, label: str,
) -> Any:
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    payload = _load_json(target)
    if canonical_sha256(payload) != expected_sha:
        raise RecoveryMaterializationError(f"{label} canonical hash drifted")
    return payload


def validate_halt_observation(
    observation: Mapping[str, Any], *, verify_archive: bool = False,
) -> None:
    if canonical_sha256(observation) != HALT_OBSERVATION_CANONICAL_SHA256:
        raise RecoveryMaterializationError("halt observation canonical hash drifted")
    if observation.get("status") != "formal_measurement_voided_by_environmental_interruption":
        raise RecoveryMaterializationError("halt observation status drifted")
    if (observation.get("execution_authorized") is not False
            or observation.get("main_run_spend_authorized") is not False):
        raise RecoveryMaterializationError("halt observation cannot authorize spend")
    attempt = _object(observation.get("attempt"), "halt observation attempt")
    if attempt.get("run_id") != "phase3-v3-07501cfa62bf55e5":
        raise RecoveryMaterializationError("halt observation binds the wrong prior run")
    measurement = _object(
        observation.get("formal_measurement"), "halt observation measurement")
    if (measurement.get("halted_model") != REMOVED_MODEL
            or measurement.get("provider_error_code") != "model_not_available"
            or measurement.get("completed_judgment_rows") != 0
            or measurement.get("measurement_reuse_authorized") is not False):
        raise RecoveryMaterializationError("halted measurement facts drifted")
    accounting = _object(
        observation.get("aggregate_cap_accounting"), "aggregate cap accounting")
    if (float(accounting.get("approved_cap_usd", -1)) != 60.0
            or not math.isclose(
                float(accounting.get("prior_accounted_spend_usd", -1)),
                0.21711289000000006, rel_tol=0.0, abs_tol=1e-15)
            or accounting.get("reset_on_successor") is not False):
        raise RecoveryMaterializationError("halt aggregate cap accounting drifted")
    if verify_archive:
        archive = _object(observation.get("archive_evidence"), "archive evidence")
        for name in (
                "usage_ledger", "run_log", "formal_results", "formal_decisions",
                "formal_cache"):
            binding = _object(archive.get(name), f"archive evidence {name}")
            path = Path(str(binding.get("path")))
            if not path.is_file():
                raise RecoveryMaterializationError(
                    f"halted archive evidence is missing: {path}")
            if _file_sha256(path) != binding.get("raw_sha256"):
                raise RecoveryMaterializationError(
                    f"halted archive evidence hash drifted: {name}")


def validate_amendment(
    amendment: Mapping[str, Any], *, project_root: str | Path | None = None,
) -> None:
    if canonical_sha256(amendment) != AMENDMENT_CANONICAL_SHA256:
        raise RecoveryMaterializationError("recovery amendment canonical hash drifted")
    if amendment.get("schema_version") != "phase3_v3_roster_amendment_v2":
        raise RecoveryMaterializationError("unsupported recovery amendment schema")
    owner = _object(amendment.get("owner_approval"), "amendment owner approval")
    if (owner.get("approver") != "Jack Maiorino"
            or owner.get("question") != APPROVAL_QUESTION
            or owner.get("response") != "approved"):
        raise RecoveryMaterializationError("recovery owner approval drifted")
    replacement = _object(amendment.get("replacement"), "amendment replacement")
    if (replacement.get("removed_model") != REMOVED_MODEL
            or replacement.get("added_model") != ADDED_MODEL
            or replacement.get("roster_size_change") != 0):
        raise RecoveryMaterializationError("recovery replacement drifted")
    if (amendment.get("execution_authorized") is not False
            or amendment.get("canary_spend_authorized") is not False
            or amendment.get("main_spend_authorized") is not False):
        raise RecoveryMaterializationError("recovery amendment cannot authorize paid execution")
    aggregate = _object(amendment.get("aggregate_cap"), "amendment aggregate cap")
    if (float(aggregate.get("approved_cap_usd", -1)) != 60.0
            or not math.isclose(
                float(aggregate.get("prior_accounted_spend_usd", -1)),
                0.21711289000000006, rel_tol=0.0, abs_tol=1e-15)
            or aggregate.get("reset_on_successor") is not False
            or aggregate.get("main_run_spend_authorized") is not False):
        raise RecoveryMaterializationError("recovery aggregate cap drifted")
    if project_root is not None:
        root = Path(project_root)
        evidence = _object(amendment.get("evidence"), "amendment evidence")
        expected = {
            "halted_attempt": (HALT_OBSERVATION_PATH, HALT_OBSERVATION_CANONICAL_SHA256),
            "provider_catalog": (PROVIDER_CATALOG_PATH, PROVIDER_CATALOG_CANONICAL_SHA256),
            "serverless_endpoints": (
                SERVERLESS_ENDPOINTS_PATH, SERVERLESS_ENDPOINTS_CANONICAL_SHA256),
            "provider_chat_template": (
                PROVIDER_TEMPLATE_PATH, PROVIDER_TEMPLATE_CANONICAL_SHA256),
        }
        for name, (path, expected_sha) in expected.items():
            binding = _object(evidence.get(name), f"amendment evidence {name}")
            if binding.get("path") != path.as_posix():
                raise RecoveryMaterializationError(
                    f"recovery evidence path drifted for {name}")
            if binding.get("canonical_sha256") != expected_sha:
                raise RecoveryMaterializationError(
                    f"recovery evidence hash binding drifted for {name}")
            _bound_json(root, path, expected_sha, f"recovery evidence {name}")
        provider_template = _bound_json(
            root, PROVIDER_TEMPLATE_PATH, PROVIDER_TEMPLATE_CANONICAL_SHA256,
            "provider chat template",
        )
        try:
            phase3_v3_inputs.validate_provider_chat_template_artifact(
                provider_template, model=ADDED_MODEL, project_root=root)
        except phase3_v3_inputs.InputGateError as exc:
            raise RecoveryMaterializationError(str(exc)) from exc


def materialize_recovery_protocol(
    prior_protocol: Mapping[str, Any],
    amendment: Mapping[str, Any],
    halt_observation: Mapping[str, Any],
    *,
    project_root: str | Path | None = None,
    verify_archive: bool = False,
) -> dict[str, Any]:
    """Create a fresh non-authorizing protocol by replacing only the unavailable judge."""
    phase3_plan.validate_protocol(prior_protocol)
    if canonical_sha256(prior_protocol) != PRIOR_PROTOCOL_CANONICAL_SHA256:
        raise RecoveryMaterializationError("prior v3 protocol canonical hash drifted")
    validate_amendment(amendment, project_root=project_root)
    validate_halt_observation(halt_observation, verify_archive=verify_archive)

    amendment_sha = canonical_sha256(amendment)
    halt_sha = canonical_sha256(halt_observation)
    protocol = deepcopy(dict(prior_protocol))
    protocol["protocol_id"] = "phase3_budget_knob_2026_08_24_v3r2"
    question_bank_sha = protocol["source_bindings"]["question_bank_bundle_sha256"]
    protocol["cell_key_namespace"] = (
        "phase3-budget-knob-2026-08-24-v3r2."
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
        "approved_on_date": "2026-08-24",
        "aggregate_cap_usd": 60.0,
        "prior_accounted_spend_usd": 0.21711289000000006,
        "canary_spend_authorized": False,
        "main_run_spend_authorized": False,
    }
    protocol["sources"].update({
        "prior_protocol": PRIOR_PROTOCOL_PATH.as_posix(),
        "recovery_amendment": AMENDMENT_PATH.as_posix(),
        "recovery_halt_observation": HALT_OBSERVATION_PATH.as_posix(),
        "provider_catalog": PROVIDER_CATALOG_PATH.as_posix(),
        "serverless_endpoints": SERVERLESS_ENDPOINTS_PATH.as_posix(),
        "provider_chat_template": PROVIDER_TEMPLATE_PATH.as_posix(),
    })
    bindings = protocol["source_bindings"]["canonical_json_sha256"]
    bindings.update({
        PRIOR_PROTOCOL_PATH.as_posix(): PRIOR_PROTOCOL_CANONICAL_SHA256,
        AMENDMENT_PATH.as_posix(): AMENDMENT_CANONICAL_SHA256,
        HALT_OBSERVATION_PATH.as_posix(): HALT_OBSERVATION_CANONICAL_SHA256,
        PROVIDER_CATALOG_PATH.as_posix(): PROVIDER_CATALOG_CANONICAL_SHA256,
        SERVERLESS_ENDPOINTS_PATH.as_posix(): SERVERLESS_ENDPOINTS_CANONICAL_SHA256,
        PROVIDER_TEMPLATE_PATH.as_posix(): PROVIDER_TEMPLATE_CANONICAL_SHA256,
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
        "one owner-approved post-interruption serverless replacement is bound above; no "
        "further replacement is permitted after successor materialization")
    protocol["process_commitments"]["environmental_interruption"] = (
        "void the interrupted measurement and restart under a fresh successor identity; "
        "preserve the old archive only for evidence and aggregate cap accounting")
    protocol["supersedes"] = {
        "protocol_id": prior_protocol["protocol_id"],
        "canonical_sha256": PRIOR_PROTOCOL_CANONICAL_SHA256,
        "reason": (
            "the prior formal measurement halted when Qwen 3.5 required a dedicated endpoint; "
            "Qwen 3.8 is bound as the owner-approved STARTED serverless replacement")
    }
    protocol["non_claims"] = [
        "This protocol does not authorize provider calls, canary spend, main spend, or GPU work.",
        "No measurement row from the halted r5 attempt satisfies a recovery canary slot.",
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
        root, AMENDMENT_PATH, AMENDMENT_CANONICAL_SHA256, "recovery amendment")
    halt = _bound_json(
        root, HALT_OBSERVATION_PATH, HALT_OBSERVATION_CANONICAL_SHA256,
        "halt observation",
    )
    if not all(isinstance(item, dict) for item in (prior, amendment, halt)):
        raise RecoveryMaterializationError("recovery inputs must be JSON objects")
    return prior, amendment, halt


def build_protocol_pin(
    protocol: Mapping[str, Any], *, protocol_tracked_path: str | Path,
) -> dict[str, Any]:
    """Reuse the v3 full-document pin after recovery bindings enter the protocol hash."""
    return phase3_v3_materialization.build_protocol_pin(
        protocol, protocol_tracked_path=protocol_tracked_path)
