import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from rejudge import phase2_execution as pe
from rejudge import phase2_plan
from rejudge import phase2_prompt_bundle as prompt_bundle
from rejudge import phase2_provider_price_snapshot as price_snapshot


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"
COMBINED_AI_AUDIT_PATH = ROOT / "rejudge" / "phase2_resolvability_ai_review.json"
A1_AMENDMENT_PATH = ROOT / "rejudge" / "phase2_resolvability_review_amendment_2026-07-16.json"
PROMPT_BUNDLE_PATH = ROOT / "rejudge" / "phase2_prompt_bundle.json"
PRICE_SNAPSHOT_PATH = ROOT / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json"
UV_LOCK_PATH = ROOT / "uv.lock"

STAGE_CAP_USD = 15.0
CUMULATIVE_CAP_USD = 1500.0
LEDGER_BINDING = {
    "path": "rejudge/output/phase2_capability_preflight_ledger.jsonl",
    "ledger_identity": "phase2-project-wide-ledger-v1",
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


# --- shared fixtures: build the real, valid pieces once per module -----------------------------


@pytest.fixture(scope="module")
def synthetic_artifacts(tmp_path_factory):
    """Paths for the three artifacts that materialization has not produced yet.

    These are absolute tmp paths so they can be bound from any ``project_root`` (including
    the real repo root) without writing anything under the tracked repository.
    """
    directory = tmp_path_factory.mktemp("phase2_execution_artifacts")
    role_limits = _write_json(directory, "per_model_role_limits.json", {"limits": "placeholder"})
    request_fields = _write_json(
        directory, "provider_request_fields.json", {"fields": "placeholder"})
    gemma_waiver = _write_json(
        directory, "gemma_recovery_waiver.json", {"waiver": "placeholder"})
    return {
        "role_limits": role_limits,
        "request_fields": request_fields,
        "gemma_waiver": gemma_waiver,
    }


@pytest.fixture(scope="module")
def baseline(synthetic_artifacts):
    protocol = phase2_plan.load_protocol(PROTOCOL_PATH)
    main_ids = phase2_plan.load_main_question_ids(protocol, ROOT)
    cells = phase2_plan.enumerate_cells(protocol, main_ids)
    capability_cells = [cell for cell in cells if cell["kind"] == "capability_qa"]
    planning_keys = sorted(cell["cell_key"] for cell in capability_cells)
    cells_by_key = {cell["cell_key"]: cell for cell in capability_cells}

    combined = json.loads(COMBINED_AI_AUDIT_PATH.read_text(encoding="utf-8"))
    amendment = json.loads(A1_AMENDMENT_PATH.read_text(encoding="utf-8"))
    bundle, _bundle_protocol = prompt_bundle.load_and_validate(PROMPT_BUNDLE_PATH, PROTOCOL_PATH)
    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(
        PRICE_SNAPSHOT_PATH, PROTOCOL_PATH)
    uv_lock_sha256 = hashlib.sha256(UV_LOCK_PATH.read_bytes()).hexdigest()

    entries_without_key = []
    for index, key in enumerate(planning_keys):
        cell = cells_by_key[key]
        side = "A" if cell["replicate_index"] == 0 else "B"
        entries_without_key.append({
            "planning_cell_key": key,
            "call_role": "capability_qa",
            "call_index": index,
            "model": cell["judge_model"],
            "seed": index,
            "side": side,
            "request_fields_sha256": "a" * 64,
        })

    return {
        "protocol": protocol,
        "planning_keys": planning_keys,
        "entries_without_key": entries_without_key,
        "combined": combined,
        "amendment": amendment,
        "bundle": bundle,
        "snapshot": snapshot,
        "uv_lock_sha256": uv_lock_sha256,
    }


def _artifact_binding(artifacts, name: str) -> dict:
    path = artifacts[name]
    return {"path": str(path), "sha256": _canon_sha(path)}


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
        "prompt_bundle_approval_status": baseline["bundle"]["status"],
        "per_model_role_limits_artifact": _artifact_binding(artifacts, "role_limits"),
        "provider_request_fields_artifact": _artifact_binding(artifacts, "request_fields"),
        "provider_price_snapshot_canonical_sha256": phase2_plan.canonical_sha256(
            baseline["snapshot"]),
        "uv_lock_sha256": baseline["uv_lock_sha256"],
        "seed_policy": execution_semantics["seed_policy"],
        "side_assignment_policy": execution_semantics["side_assignment_policy"],
        "satisfied_prerequisites": {
            "gemma_recovery_or_waiver": _artifact_binding(artifacts, "gemma_waiver"),
        },
        "ledger": dict(LEDGER_BINDING),
        "stage_cap_usd": stage_cap,
        "cumulative_cap_usd": cumulative_cap,
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
        prompt_bundle_approval_status=shared["prompt_bundle_approval_status"],
        per_model_role_limits_artifact=shared["per_model_role_limits_artifact"],
        provider_request_fields_artifact=shared["provider_request_fields_artifact"],
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


def matching_authorization(identity_sha256, *, stage="capability_preflight",
                           stage_cap=STAGE_CAP_USD, cumulative_cap=CUMULATIVE_CAP_USD):
    return {
        "execution_identity_sha256": identity_sha256,
        "stage": stage,
        "stage_cap_usd": stage_cap,
        "cumulative_cap_usd": cumulative_cap,
        "approver": "Jack Maiorino",
        "approved_at_utc": "2026-07-18T00:00:00Z",
    }


DATA_FILES_FOR_ROOT_COPY = (
    "rejudge/phase2_protocol.json",
    "rejudge/output/calibration_models.json",
    "rejudge/calibration_questions_2026-07-14.json",
    "rejudge/oracle_shortcut_audit_2026-07-12.json",
    "rejudge/calibration_recovery_gemma_2026-07-15.json",
    "rejudge/phase2_resolvability_review.json",
    "rejudge/phase2_resolvability_ai_review.json",
    "rejudge/phase2_resolvability_ai_review_carath_norn.json",
    "rejudge/phase2_resolvability_ai_review_selvarath.json",
    "rejudge/phase2_resolvability_ai_review_vethun_sarak.json",
    "rejudge/phase2_resolvability_review_amendment_2026-07-16.json",
    "rejudge/phase2_prompt_bundle.json",
    "rejudge/phase2_provider_price_snapshot_2026-07-18.json",
    "questions/carath_norn_questions.json",
    "questions/selvarath_questions.json",
    "questions/vethun_sarak_questions.json",
    "uv.lock",
)


def _copy_tracked_data_files(destination: Path) -> Path:
    """Copy only the small tracked JSON/lock data files a full validation needs.

    Deliberately excludes rejudge/output/ (hundreds of MB of run data) and every .py
    module: project_root is a data lookup root, not a code root, so no source files need
    copying at all.
    """
    for relative in DATA_FILES_FOR_ROOT_COPY:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return destination


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
def valid_manifest(baseline, synthetic_artifacts):
    return build_manifest(baseline, synthetic_artifacts)


@pytest.fixture
def manifest(valid_manifest):
    """A fresh mutable deep copy of the valid baseline manifest for each test."""
    manifest, identity_sha256 = valid_manifest
    return deepcopy(manifest), identity_sha256


# --- happy path (structural validation; require_authorized defaults to False) ------------------


def test_valid_manifest_validates_and_derives_the_expected_identity(manifest):
    manifest_dict, identity_sha256 = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=ROOT)
    assert validated.stage == "capability_preflight"
    assert validated.execution_identity_sha256 == identity_sha256
    assert len(validated.provider_call_inventory) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert len(validated.planning_cell_keys) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert validated.authorized is False
    assert validated.authorization is None
    assert validated.stage_cap_usd == STAGE_CAP_USD
    assert validated.cumulative_cap_usd == CUMULATIVE_CAP_USD


def test_call_inventory_is_unique_and_bijective_with_planning_cells(manifest):
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=ROOT)
    call_keys = {entry["execution_call_key"] for entry in validated.provider_call_inventory}
    planning_keys = {
        entry["planning_cell_key"] for entry in validated.provider_call_inventory
    }
    assert len(call_keys) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert planning_keys == set(validated.planning_cell_keys)


def test_dataclasses_are_frozen(manifest):
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=ROOT)
    with pytest.raises(FrozenInstanceError):
        setattr(validated, "stage", "canary")
    audit = pe.audit_resume(validated, output_rows=[], usage_events=[])
    with pytest.raises(FrozenInstanceError):
        setattr(audit, "stage", "canary")


# --- top-level manifest structure ---------------------------------------------------------------


def test_non_dict_manifest_is_rejected():
    with pytest.raises(pe.ManifestValidationError, match="must be an object"):
        pe.validate_execution_manifest(cast(Any, []), project_root=ROOT)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_top_level_key_drift_is_rejected(manifest, mutation):
    manifest_dict, _identity = manifest
    if mutation == "missing":
        del manifest_dict["stage_cap_usd"]
    else:
        manifest_dict["unexpected_field"] = "x"
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_wrong_schema_version_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["schema_version"] = "phase2_execution_manifest_v0"
    with pytest.raises(pe.ManifestValidationError, match="unsupported execution manifest"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_unrecognized_stage_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["stage"] = "not_a_real_stage"
    with pytest.raises(pe.ManifestValidationError, match="unrecognized execution stage"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


@pytest.mark.parametrize("stage", ["gemma_recovery_or_waiver", "canary", "main"])
def test_unsupported_stages_raise_unconditionally(baseline, synthetic_artifacts, stage):
    manifest_dict, _identity = build_manifest(baseline, synthetic_artifacts, stage=stage)
    with pytest.raises(pe.UnsupportedStageError, match=stage):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


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
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


@pytest.mark.parametrize("field", [
    "per_model_role_limits_artifact", "provider_request_fields_artifact",
])
def test_artifact_binding_hash_drift_is_rejected(manifest, field):
    manifest_dict, _identity = manifest
    manifest_dict[field]["sha256"] = _flip_hex_digest(manifest_dict[field]["sha256"])
    with pytest.raises(pe.ManifestValidationError, match="hash drift"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


@pytest.mark.parametrize("field", [
    "per_model_role_limits_artifact", "provider_request_fields_artifact",
])
def test_artifact_binding_missing_file_fails_closed(manifest, tmp_path, field):
    manifest_dict, _identity = manifest
    missing = tmp_path / "does_not_exist.json"
    manifest_dict[field] = {"path": str(missing), "sha256": "a" * 64}
    with pytest.raises(pe.ManifestValidationError, match="artifact is missing"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_gemma_recovery_waiver_hash_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    binding = manifest_dict["satisfied_prerequisites"]["gemma_recovery_or_waiver"]
    binding["sha256"] = _flip_hex_digest(binding["sha256"])
    with pytest.raises(pe.ManifestValidationError, match="hash drift"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_gemma_recovery_waiver_missing_file_fails_closed(manifest, tmp_path):
    manifest_dict, _identity = manifest
    missing = tmp_path / "no_waiver.json"
    manifest_dict["satisfied_prerequisites"]["gemma_recovery_or_waiver"] = {
        "path": str(missing), "sha256": "b" * 64,
    }
    with pytest.raises(pe.ManifestValidationError, match="artifact is missing"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_satisfied_prerequisites_key_set_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["satisfied_prerequisites"]["unexpected_stage"] = {
        "path": "x", "sha256": "c" * 64,
    }
    with pytest.raises(pe.ManifestValidationError, match="satisfied_prerequisites"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


# --- A1 / combined AI audit binding mismatches ----------------------------------------------------


def test_missing_a1_amendment_binding_is_rejected(manifest):
    manifest_dict, _identity = manifest
    del manifest_dict["a1_amendment_canonical_sha256"]
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


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
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


# --- prompt bundle: hash + candidate refusal --------------------------------------------------------


def test_prompt_bundle_approval_status_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["prompt_bundle_approval_status"] = "owner_approved"
    with pytest.raises(pe.ManifestValidationError, match="prompt_bundle_approval_status"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_candidate_prompt_bundle_is_refused_when_authorization_required(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    with pytest.raises(pe.ExecutionAuthorityError, match="candidate"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_candidate_prompt_bundle_does_not_block_unauthorized_validation(manifest):
    # require_authorized=False must still succeed even though the tracked bundle is a
    # candidate: draft manifests must be reviewable before owner approval exists.
    manifest_dict, _identity = manifest
    validated = pe.validate_execution_manifest(manifest_dict, project_root=ROOT)
    assert validated.authorized is False


# --- seed / side policy strings ----------------------------------------------------------------------


def test_seed_policy_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["seed_policy"] = "some other policy"
    with pytest.raises(pe.ManifestValidationError, match="seed_policy"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_side_assignment_policy_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["side_assignment_policy"] = "some other policy"
    with pytest.raises(pe.ManifestValidationError, match="side_assignment_policy"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


# --- ledger binding (structure only) -----------------------------------------------------------------


def test_ledger_key_set_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["ledger"] = {"path": "x"}
    with pytest.raises(pe.ManifestValidationError, match="ledger"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_ledger_blank_identity_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["ledger"]["ledger_identity"] = ""
    with pytest.raises(pe.ManifestValidationError, match="ledger.ledger_identity"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


# --- planning cell inventory: exact 1060, no duplicates, matches the frozen protocol -----------------


def test_planning_cell_count_1059_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"].pop()
    with pytest.raises(pe.ManifestValidationError, match="exactly 1060"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_planning_cell_count_1061_via_duplicate_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"].append(manifest_dict["planning_cell_keys"][0])
    with pytest.raises(pe.ManifestValidationError, match="duplicate cell keys"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_planning_cell_set_mismatch_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"][0] = "bogus-planning-cell-key"
    with pytest.raises(pe.ManifestValidationError, match="does not match the frozen"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_planning_cell_keys_not_a_list_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"] = {"not": "a list"}
    with pytest.raises(pe.ManifestValidationError, match="list of non-empty strings"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_planning_cell_keys_with_non_string_element_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"][0] = 12345
    with pytest.raises(pe.ManifestValidationError, match="list of non-empty strings"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_planning_cell_keys_with_blank_element_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["planning_cell_keys"][0] = ""
    with pytest.raises(pe.ManifestValidationError, match="list of non-empty strings"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


# --- provider-call inventory: exact 1060, structure, cross-checks, duplicates ------------------------


def test_call_inventory_count_1059_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"].pop()
    with pytest.raises(pe.ManifestValidationError, match="exactly 1060"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_count_1061_is_rejected(manifest):
    manifest_dict, _identity = manifest
    extra = deepcopy(manifest_dict["provider_call_inventory"][-1])
    manifest_dict["provider_call_inventory"].append(extra)
    with pytest.raises(pe.ManifestValidationError, match="exactly 1060"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_duplicate_planning_cell_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[1]["planning_cell_key"] = entries[0]["planning_cell_key"]
    with pytest.raises(pe.ManifestValidationError, match="duplicate planning cell"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_duplicate_execution_call_key_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[1]["execution_call_key"] = entries[0]["execution_call_key"]
    with pytest.raises(pe.ManifestValidationError, match="duplicate execution_call_key"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_entry_key_set_drift_is_rejected(manifest):
    manifest_dict, _identity = manifest
    del manifest_dict["provider_call_inventory"][0]["seed"]
    with pytest.raises(pe.ManifestValidationError, match="fields drifted"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_wrong_call_role_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][0]["call_role"] = "judge_verdict"
    with pytest.raises(pe.ManifestValidationError, match="call_role"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_wrong_call_index_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][5]["call_index"] = 999
    with pytest.raises(pe.ManifestValidationError, match="call_index"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_unknown_planning_cell_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][0]["planning_cell_key"] = "not-a-real-cell"
    with pytest.raises(pe.ManifestValidationError, match="known capability_qa planning cell"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_model_disagreeing_with_cell_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[0]["model"] = "not-the-real-model"
    with pytest.raises(pe.ManifestValidationError, match="model disagrees"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_side_disagreeing_with_replicate_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[0]["side"] = "B" if entries[0]["side"] == "A" else "A"
    with pytest.raises(pe.ManifestValidationError, match="side disagrees"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


@pytest.mark.parametrize("bad_seed", [-1, 1.5, True, "0"])
def test_call_inventory_seed_must_be_a_nonnegative_int(manifest, bad_seed):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][0]["seed"] = bad_seed
    with pytest.raises(pe.ManifestValidationError, match="seed"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_request_fields_hash_must_be_sha256(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"][0]["request_fields_sha256"] = "not-a-hash"
    with pytest.raises(pe.ManifestValidationError, match="request_fields_sha256"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_execution_call_key_mismatch_is_rejected(manifest):
    manifest_dict, _identity = manifest
    entries = manifest_dict["provider_call_inventory"]
    entries[0]["execution_call_key"] = _flip_hex_digest(entries[0]["execution_call_key"])
    with pytest.raises(pe.ManifestValidationError, match="does not match its derived value"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_call_inventory_not_a_list_is_rejected(manifest):
    manifest_dict, _identity = manifest
    manifest_dict["provider_call_inventory"] = {}
    with pytest.raises(pe.ManifestValidationError, match="must be a list"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


# --- immutable stage cap / cumulative cap -------------------------------------------------------------


def test_stage_cap_escalation_over_protocol_ceiling_is_rejected(baseline, synthetic_artifacts):
    manifest_dict, _identity = build_manifest(
        baseline, synthetic_artifacts, stage_cap=15.01, cumulative_cap=1500.0)
    with pytest.raises(pe.ManifestValidationError, match="exceeds the protocol"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_cumulative_cap_below_stage_cap_is_rejected(baseline, synthetic_artifacts):
    manifest_dict, _identity = build_manifest(
        baseline, synthetic_artifacts, stage_cap=15.0, cumulative_cap=10.0)
    with pytest.raises(pe.ManifestValidationError, match="cumulative_cap_usd"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


@pytest.mark.parametrize("field,bad_value", [
    ("stage_cap_usd", 0), ("stage_cap_usd", -1), ("stage_cap_usd", "15"), ("stage_cap_usd", True),
    ("cumulative_cap_usd", 0), ("cumulative_cap_usd", float("nan")),
])
def test_cap_fields_must_be_finite_positive_numbers(manifest, field, bad_value):
    manifest_dict, _identity = manifest
    manifest_dict[field] = bad_value
    with pytest.raises(pe.ManifestValidationError, match="finite, positive number|must be a number"):
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


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
        pe.validate_execution_manifest(loaded, project_root=ROOT)


@pytest.mark.parametrize("field", ["stage_cap_usd", "cumulative_cap_usd"])
def test_arbitrary_precision_authorization_cap_is_rejected_not_a_crash(manifest, field):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization[field] = 10 ** 400
    with pytest.raises(pe.ExecutionAuthorityError, match="finite, positive number"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=authorization,
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
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


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
        pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


# --- authorization --------------------------------------------------------------------------------------


def test_no_authorization_record_is_rejected_when_required(manifest):
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ExecutionAuthorityError, match="none was provided"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, require_authorized=True)


def test_wrong_identity_hash_authorization_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization["execution_identity_sha256"] = _flip_hex_digest(identity_sha256)
    with pytest.raises(pe.ExecutionAuthorityError, match="does not match this manifest"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_wrong_stage_authorization_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256, stage="canary")
    with pytest.raises(pe.ExecutionAuthorityError, match="authorization.stage"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=authorization,
            require_authorized=True,
        )


@pytest.mark.parametrize("field", ["stage_cap_usd", "cumulative_cap_usd"])
def test_wrong_caps_authorization_is_rejected(manifest, field):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization[field] = authorization[field] + 1
    with pytest.raises(pe.ExecutionAuthorityError, match="caps do not match"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_key_set_drift_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    del authorization["approver"]
    with pytest.raises(pe.ExecutionAuthorityError, match="fields drifted"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_non_utc_timestamp_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization["approved_at_utc"] = "2026-07-18"
    with pytest.raises(pe.ExecutionAuthorityError, match="UTC timestamp"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_blank_approver_is_rejected(manifest):
    manifest_dict, identity_sha256 = manifest
    authorization = matching_authorization(identity_sha256)
    authorization["approver"] = "   "
    with pytest.raises(pe.ExecutionAuthorityError, match="approver"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=authorization,
            require_authorized=True,
        )


def test_authorization_not_a_mapping_is_rejected(manifest):
    manifest_dict, _identity = manifest
    with pytest.raises(pe.ExecutionAuthorityError, match="must be an object"):
        pe.validate_execution_manifest(
            manifest_dict, project_root=ROOT, authorization=cast(Any, ["not", "a", "dict"]),
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
    validated = pe.validate_execution_manifest(loaded, project_root=ROOT)
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
    return pe.validate_execution_manifest(manifest_dict, project_root=ROOT)


def test_clean_resume_with_no_activity_is_all_todo(validated_manifest):
    audit = pe.audit_resume(validated_manifest, output_rows=[], usage_events=[])
    assert audit.disposition is pe.ResumeDisposition.TODO
    assert audit.counts == {
        "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": pe.EXPECTED_CAPABILITY_CELL_COUNT,
        "complete": 0, "blocked_reconciliation": 0,
    }
    assert len(audit.todo_call_keys) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert audit.blockers == ()


def test_exact_completion_of_all_1060_calls_is_complete(validated_manifest):
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
        "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 0,
        "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT, "blocked_reconciliation": 0,
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
    assert audit.counts["todo"] == pe.EXPECTED_CAPABILITY_CELL_COUNT - 5
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
    assert audit.counts["todo"] == pe.EXPECTED_CAPABILITY_CELL_COUNT - 1
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
    assert audit.counts["todo"] == pe.EXPECTED_CAPABILITY_CELL_COUNT


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
