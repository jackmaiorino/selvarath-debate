"""Phase 3 main launch-manifest and exact-authorization tests."""
from __future__ import annotations

import hashlib
import json
import platform
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rejudge import phase3_main_manifest as main_manifest
from rejudge import phase3_main_runtime_policies as runtime_policies
from rejudge.phase3_main_runner import (
    CONFIRMED_MAIN_JUDGES,
    EXPECTED_MAIN_CELL_COUNT,
    EXPECTED_MAIN_INVENTORY_SHA256,
    EXPECTED_MAIN_JUDGMENT_COUNT,
    EXPECTED_MAIN_QUESTION_COUNT,
    EXPECTED_MAIN_TRANSCRIPT_COUNT,
)


NOW = datetime(2026, 8, 29, 20, 30, tzinfo=timezone.utc)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(tmp_path: Path) -> dict:
    input_root = tmp_path / "inputs"
    input_root.mkdir(parents=True)
    bindings = {}
    for name in sorted(main_manifest.REQUIRED_INPUT_BINDINGS):
        path = input_root / f"{name}.json"
        if name == "price_change_policy":
            value = runtime_policies.EXPECTED_PRICE_CHANGE_POLICY
        elif name == "reviewer_usage_policy":
            value = runtime_policies.EXPECTED_REVIEWER_USAGE_POLICY
        else:
            value = {"name": name}
        path.write_text(
            json.dumps(value, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        bindings[name] = {
            "path": str(path.resolve()),
            "sha256": _sha(path),
            "hash_kind": "raw_sha256",
        }
    lock = input_root / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    artifact_root = (tmp_path / "formal").resolve()
    registry_root = (tmp_path / "identity-registry").resolve()
    paths = {
        name: str(artifact_root / filename)
        for name, filename in main_manifest.OUTPUT_FILENAMES.items()
    }
    manifest = {
        "schema_version": main_manifest.MANIFEST_SCHEMA,
        "stage": "main",
        "run_id": "pending",
        "recorded_at_utc": "2026-08-29T20:00:00Z",
        "execution_authorized": False,
        "source_commit": "a" * 40,
        "toolchain": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "dependency_lock_path": str(lock.resolve()),
            "dependency_lock_raw_sha256": _sha(lock),
            "linker_version_or_not_applicable": "not_applicable",
        },
        "seeds": {
            "harness_seed": 2026082901,
            "analysis_bootstrap_seed": 20260829,
            "subsample_seed": 20260818,
        },
        "input_bindings": bindings,
        "inventory": {
            "question_count": EXPECTED_MAIN_QUESTION_COUNT,
            "transcript_count": EXPECTED_MAIN_TRANSCRIPT_COUNT,
            "judgment_count": EXPECTED_MAIN_JUDGMENT_COUNT,
            "cell_count": EXPECTED_MAIN_CELL_COUNT,
            "canonical_sha256": EXPECTED_MAIN_INVENTORY_SHA256,
        },
        "output_contract": {
            "artifact_root": str(artifact_root),
            "identity_registry_root": str(registry_root),
            "paths": paths,
            "sha256s": {name: None for name in paths},
        },
        "restart": {"mode": "initial", "predecessor": None},
        "runtime": {
            "provider": "Together",
            "provider_account_identity_sha256": "d" * 64,
            "model_ids": list(CONFIRMED_MAIN_JUDGES),
            "provider_worker_concurrency": 1,
            "reviewer_cli_binary": "codex.cmd",
            "reviewer_model": "gpt-5.6-sol",
            "reviewer_reasoning_effort": "high",
            "reviewer_concurrency": 12,
            "gpu_ordinal": "not_applicable",
        },
        "spend": {
            "prior_reconciled_usd": "84.38130485",
            "forecast_main_usd": "640.27",
            "stage_cap_usd": "875.00",
        },
        "harness_check": {
            "receipt_input_name": "harness_receipt",
            "seed_name": "harness_seed",
            "status": "bit_identical_pass",
            "first_output_store_sha256": "b" * 64,
            "rerun_output_store_sha256": "b" * 64,
        },
        "manifest_identity_sha256": "0" * 64,
    }
    manifest["manifest_identity_sha256"] = main_manifest.manifest_identity_sha256(manifest)
    manifest["run_id"] = main_manifest.expected_run_id(manifest)
    return manifest


def _authorization(manifest: dict) -> dict:
    inputs = manifest["input_bindings"]
    return {
        "schema_version": main_manifest.AUTHORIZATION_SCHEMA,
        "authorization_id": "phase3-main-owner-auth-test",
        "stage": "main",
        "run_id": manifest["run_id"],
        "manifest_canonical_sha256": main_manifest.manifest_canonical_sha256(manifest),
        "manifest_identity_sha256": manifest["manifest_identity_sha256"],
        "provider": "Together",
        "provider_account_identity_sha256": (
            manifest["runtime"]["provider_account_identity_sha256"]),
        "exact_model_ids": list(CONFIRMED_MAIN_JUDGES),
        "reviewer_model": manifest["runtime"]["reviewer_model"],
        "reviewer_reasoning_effort": manifest["runtime"]["reviewer_reasoning_effort"],
        "reviewer_concurrency": manifest["runtime"]["reviewer_concurrency"],
        "stage_cap_usd": manifest["spend"]["stage_cap_usd"],
        "price_snapshot_sha256": inputs["price_snapshot"]["sha256"],
        "price_change_policy_sha256": inputs["price_change_policy"]["sha256"],
        "prior_reconciliation_sha256": inputs["billing_reconciliation"]["sha256"],
        "forecast_sha256": inputs["certified_cost_forecast"]["sha256"],
        "reviewer_usage_policy_sha256": inputs["reviewer_usage_policy"]["sha256"],
        "maximum_reviewer_dispatches": 59_040,
        "harness_execution_count": 2,
        "formal_main_attempt_count": 1,
        "approver": "Jack Maiorino",
        "approved_at_utc": "2026-08-29T20:15:00Z",
        "valid_until_utc": "2026-08-30T20:15:00Z",
        "exact_text": main_manifest.expected_authorization_text(manifest),
        "execution_authorized": True,
        "provider_calls_authorized": True,
        "main_run_spend_authorized": True,
        "no_resume": True,
    }


def test_manifest_is_small_exact_non_authorizing_and_file_bound(tmp_path):
    manifest = _manifest(tmp_path)
    result = main_manifest.validate_main_manifest(manifest, project_root=tmp_path)
    assert set(manifest) == main_manifest.MANIFEST_FIELDS
    assert manifest["schema_version"] == "phase3_main_launch_manifest_v5"
    assert "price_change_policy" in main_manifest.REQUIRED_INPUT_BINDINGS
    assert "reviewer_usage_policy" in main_manifest.REQUIRED_INPUT_BINDINGS
    assert "tokenizer_manifest" in main_manifest.REQUIRED_INPUT_BINDINGS
    assert "exact_context_index" not in main_manifest.REQUIRED_INPUT_BINDINGS
    assert manifest["execution_authorized"] is False
    assert result["run_id"] == manifest["run_id"]
    assert result["stage_cap_usd"] == "875.00"
    assert result["artifact_root"] == (tmp_path / "formal").resolve()


def test_environmental_successor_binds_fresh_predecessor_evidence(tmp_path):
    predecessor_run_id = "phase3-main-predecessor"
    predecessor_manifest_sha = "c" * 64
    predecessor_root = (tmp_path / "predecessor-formal").resolve()
    predecessor_root.mkdir()
    predecessor_ledger = predecessor_root / main_manifest.OUTPUT_FILENAMES["usage_ledger"]
    predecessor_ledger.write_text('{"event":"genesis"}\n', encoding="utf-8")
    registry = (tmp_path / "predecessor-registry" / "identities").resolve()
    registry.mkdir(parents=True)
    void_record = registry / (
        f"{predecessor_run_id}-{predecessor_manifest_sha}.voided.json")
    void_record.write_text('{"status":"voided_environmental_interruption"}\n', encoding="utf-8")

    manifest = _manifest(tmp_path / "successor")
    manifest["restart"] = {
        "mode": main_manifest.RESTART_MODE_ENVIRONMENTAL_SUCCESSOR,
        "predecessor": {
            "run_id": predecessor_run_id,
            "manifest_canonical_sha256": predecessor_manifest_sha,
            "manifest_identity_sha256": "d" * 64,
            "authorization_id": "predecessor-authorization",
            "authorization_canonical_sha256": "e" * 64,
            "authorization_raw_sha256": "f" * 64,
            "authorization_signature_raw_sha256": "1" * 64,
            "artifact_root": predecessor_root.as_posix(),
            "void_record_path": void_record.as_posix(),
            "void_record_raw_sha256": _sha(void_record),
            "usage_ledger_path": predecessor_ledger.as_posix(),
            "usage_ledger_raw_sha256": _sha(predecessor_ledger),
        },
    }
    manifest["manifest_identity_sha256"] = main_manifest.manifest_identity_sha256(manifest)
    manifest["run_id"] = main_manifest.expected_run_id(manifest)

    validated = main_manifest.validate_main_manifest(manifest, project_root=tmp_path)
    assert validated["restart"]["mode"] == (
        main_manifest.RESTART_MODE_ENVIRONMENTAL_SUCCESSOR)
    assert validated["restart"]["predecessor"]["usage_ledger_path"] == predecessor_ledger
    authorization = _authorization(manifest)
    assert predecessor_run_id in authorization["exact_text"]
    main_manifest.validate_main_authorization(authorization, manifest, as_of=NOW)

    changed = deepcopy(manifest)
    changed["restart"]["predecessor"]["usage_ledger_raw_sha256"] = "2" * 64
    changed["manifest_identity_sha256"] = main_manifest.manifest_identity_sha256(changed)
    changed["run_id"] = main_manifest.expected_run_id(changed)
    with pytest.raises(main_manifest.MainManifestError, match="usage ledger"):
        main_manifest.validate_main_manifest(changed, project_root=tmp_path)

    changed = deepcopy(manifest)
    changed["restart"]["predecessor"]["artifact_root"] = (
        changed["output_contract"]["artifact_root"])
    changed["manifest_identity_sha256"] = main_manifest.manifest_identity_sha256(changed)
    changed["run_id"] = main_manifest.expected_run_id(changed)
    with pytest.raises(main_manifest.MainManifestError, match="new artifact root"):
        main_manifest.validate_main_manifest(changed, project_root=tmp_path)


def test_manifest_rejects_self_authority_input_drift_and_wrong_hash_kind(tmp_path):
    manifest = _manifest(tmp_path)
    changed = deepcopy(manifest)
    changed["execution_authorized"] = True
    with pytest.raises(main_manifest.MainManifestError, match="never authorize"):
        main_manifest.validate_main_manifest(changed, project_root=tmp_path)

    bound = Path(manifest["input_bindings"]["protocol"]["path"])
    bound.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(main_manifest.MainManifestError, match="raw SHA-256 drifted"):
        main_manifest.validate_main_manifest(manifest, project_root=tmp_path)

    manifest = _manifest(tmp_path / "new")
    manifest["input_bindings"]["protocol"]["hash_kind"] = "canonical_json_sha256"
    manifest["manifest_identity_sha256"] = main_manifest.manifest_identity_sha256(manifest)
    manifest["run_id"] = main_manifest.expected_run_id(manifest)
    with pytest.raises(main_manifest.MainManifestError, match="hash_kind"):
        main_manifest.validate_main_manifest(manifest, project_root=tmp_path)


def test_manifest_rejects_inventory_output_concurrency_runtime_and_forecast_drift(tmp_path):
    manifest = _manifest(tmp_path)
    cases = [
        ("inventory", "judgment_count", 9_839, "inventory"),
        ("runtime", "provider_worker_concurrency", 2, "concurrency"),
        ("runtime", "provider_worker_concurrency", True, "non-negative integer"),
        ("runtime", "gpu_ordinal", 1, "GPU ordinal"),
        ("spend", "forecast_main_usd", "900.00", "fit inside"),
    ]
    for section, field, value, message in cases:
        changed = deepcopy(manifest)
        changed[section][field] = value
        changed["manifest_identity_sha256"] = main_manifest.manifest_identity_sha256(changed)
        changed["run_id"] = main_manifest.expected_run_id(changed)
        with pytest.raises(main_manifest.MainManifestError, match=message):
            main_manifest.validate_main_manifest(changed, project_root=tmp_path)

    changed = deepcopy(manifest)
    changed["output_contract"]["paths"]["results"] = str(tmp_path / "elsewhere.jsonl")
    changed["manifest_identity_sha256"] = main_manifest.manifest_identity_sha256(changed)
    changed["run_id"] = main_manifest.expected_run_id(changed)
    with pytest.raises(main_manifest.MainManifestError, match="output path"):
        main_manifest.validate_main_manifest(changed, project_root=tmp_path)


def test_manifest_identity_covers_every_operational_field(tmp_path):
    manifest = _manifest(tmp_path)
    changed = deepcopy(manifest)
    changed["runtime"]["reviewer_model"] = "different-reviewer"
    with pytest.raises(main_manifest.MainManifestError, match="identity digest"):
        main_manifest.validate_main_manifest(changed, project_root=tmp_path)


def test_manifest_runtime_account_hash_is_strict_and_identity_bound(tmp_path):
    manifest = _manifest(tmp_path)

    malformed = deepcopy(manifest)
    malformed["runtime"]["provider_account_identity_sha256"] = "D" * 64
    with pytest.raises(main_manifest.MainManifestError, match="lowercase SHA-256"):
        main_manifest.validate_main_manifest(malformed, project_root=tmp_path)

    changed = deepcopy(manifest)
    changed["runtime"]["provider_account_identity_sha256"] = "e" * 64
    with pytest.raises(main_manifest.MainManifestError, match="identity digest"):
        main_manifest.validate_main_manifest(changed, project_root=tmp_path)


def test_exact_authorization_binds_identity_cap_models_forecast_and_reconciliation(tmp_path):
    manifest = _manifest(tmp_path)
    main_manifest.validate_main_manifest(manifest, project_root=tmp_path)
    authorization = _authorization(manifest)
    result = main_manifest.validate_main_authorization(
        authorization, manifest, as_of=NOW)
    assert result["approved_cap_usd"] == "875.00"
    assert authorization["schema_version"] == "phase3_main_exact_authorization_v5"
    assert "Together account identity SHA-256" in authorization["exact_text"]
    assert manifest["runtime"]["provider_account_identity_sha256"] in (
        authorization["exact_text"])
    assert "latest start of a new logical provider call" in authorization["exact_text"]
    assert "local evidence closeout may finish later" in authorization["exact_text"]
    assert "detected provider price change stops" in authorization["exact_text"]
    assert "separately capped at 59040 dispatches" in authorization["exact_text"]
    assert "neither USD nor token accounting" in authorization["exact_text"]

    mutations = [
        ("run_id", "phase3-main-other", "run ID"),
        ("stage_cap_usd", "875.0", "exactly equal"),
        ("provider_account_identity_sha256", "e" * 64, "account identity"),
        ("exact_model_ids", list(reversed(CONFIRMED_MAIN_JUDGES)), "endpoint roster"),
        ("reviewer_model", "different-reviewer", "reviewer runtime"),
        ("reviewer_concurrency", 11, "reviewer runtime"),
        ("forecast_sha256", "0" * 64, "forecast binding"),
        ("price_change_policy_sha256", "0" * 64, "price-change policy binding"),
        ("reviewer_usage_policy_sha256", "0" * 64, "reviewer-usage policy binding"),
        ("maximum_reviewer_dispatches", 59_041, "reviewer dispatch ceiling"),
        ("prior_reconciliation_sha256", "0" * 64, "reconciliation binding"),
        ("formal_main_attempt_count", 2, "one formal main attempt"),
        ("formal_main_attempt_count", True, "non-negative integer"),
        ("exact_text", "Approved, generically.", "exact owner approval text"),
    ]
    for field, value, message in mutations:
        changed = deepcopy(authorization)
        changed[field] = value
        with pytest.raises(main_manifest.MainManifestError, match=message):
            main_manifest.validate_main_authorization(changed, manifest, as_of=NOW)


def test_authorization_must_be_explicit_active_and_owner_signed(tmp_path):
    manifest = _manifest(tmp_path)
    authorization = _authorization(manifest)
    changed = deepcopy(authorization)
    changed["provider_calls_authorized"] = False
    with pytest.raises(main_manifest.MainManifestError, match="explicitly authorize"):
        main_manifest.validate_main_authorization(changed, manifest, as_of=NOW)

    changed = deepcopy(authorization)
    changed["approver"] = "Someone Else"
    with pytest.raises(main_manifest.MainManifestError, match="Jack Maiorino"):
        main_manifest.validate_main_authorization(changed, manifest, as_of=NOW)

    with pytest.raises(main_manifest.MainManifestError, match="not active"):
        main_manifest.validate_main_authorization(
            authorization,
            manifest,
            as_of=datetime(2026, 8, 31, tzinfo=timezone.utc),
        )
