"""Tests for offline Phase 3 main launch-package materialization."""
from __future__ import annotations

import json
import platform
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rejudge import phase3_main_launch_materialization as materialization
from rejudge import phase3_main_manifest
from rejudge import phase3_main_runtime_policies as runtime_policies
from rejudge import phase3_main_stage_cap


NOW = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8", newline="\n")
    else:
        path.write_text(
            json.dumps(value, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return path


def _inputs(tmp_path: Path, monkeypatch) -> tuple[Path, dict[str, Path]]:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root / "uv.lock", "version = 1\n")
    monkeypatch.setattr(phase3_main_stage_cap, "RATIFICATION_PATH", root / "ratify.json")
    ratification = json.loads(
        Path(__file__).parents[1]
        .joinpath(
            "rejudge",
            "phase3_main_console_billing_and_stage_cap_ratification_2026-09-04.json",
        )
        .read_text(encoding="utf-8")
    )
    paths: dict[str, Path] = {}
    for name in sorted(phase3_main_manifest.REQUIRED_INPUT_BINDINGS):
        if name == "capacity_execution_authorization_signature":
            path = root / "inputs" / "capacity_execution_authorization.json.sig"
            value = "fixture signature\n"
        else:
            path = root / "inputs" / f"{name}.json"
            value = {"name": name}
        if name == "price_change_policy":
            value = runtime_policies.EXPECTED_PRICE_CHANGE_POLICY
        elif name == "reviewer_usage_policy":
            value = runtime_policies.EXPECTED_REVIEWER_USAGE_POLICY
        elif name == "stage_cap_ratification":
            value = ratification
        elif name == "capacity_plan":
            value = {
                "reviewer_configuration": {
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                    "concurrency": 12,
                    "reviewer_cli_binary": "codex.cmd",
                }
            }
        elif name == "billing_reconciliation":
            value = {
                "billing_scope": {
                    "account_identity_sha256": materialization.ACCOUNT_IDENTITY_SHA256,
                }
            }
        elif name == "certified_cost_forecast":
            value = {
                "certification": "pass",
                "within_stage_cap": True,
                "cumulative_spend_usd": 119.2723849,
                "projected_main_usd": 908.82,
                "stage_cap_usd": 1100.0,
            }
        elif name == "harness_receipt":
            value = {
                "schema_version": "phase3_main_harness_receipt_v2",
                "status": "bit_identical_pass",
                "harness_seed": 20260829,
                "first_output_store_sha256": "b" * 64,
                "rerun_output_store_sha256": "b" * 64,
            }
        paths[name] = _write(path, value)
    return root, paths


def _manifest(tmp_path: Path, monkeypatch) -> tuple[Path, dict, dict[str, Path]]:
    root, paths = _inputs(tmp_path, monkeypatch)
    manifest = materialization.build_launch_manifest(
        project_root=root,
        input_paths=paths,
        artifact_root=tmp_path / "formal",
        identity_registry_root=tmp_path / "registry",
        source_commit="a" * 40,
        recorded_at_utc=NOW,
    )
    return root, manifest, paths


def test_build_launch_manifest_is_exact_non_authorizing_and_valid(
    tmp_path, monkeypatch,
):
    root, manifest, _paths = _manifest(tmp_path, monkeypatch)

    validated = phase3_main_manifest.validate_main_manifest(
        manifest,
        project_root=root,
        verify_files=True,
        verify_runtime=True,
    )
    assert set(manifest) == phase3_main_manifest.MANIFEST_FIELDS
    assert manifest["execution_authorized"] is False
    assert manifest["source_commit"] == "a" * 40
    assert manifest["toolchain"]["python_implementation"] == platform.python_implementation()
    assert manifest["toolchain"]["python_version"] == platform.python_version()
    assert manifest["runtime"]["provider_account_identity_sha256"] == (
        materialization.ACCOUNT_IDENTITY_SHA256
    )
    assert manifest["spend"] == {
        "prior_reconciled_usd": "119.27238490",
        "forecast_main_usd": "908.82",
        "stage_cap_usd": "1100.00",
    }
    assert manifest["seeds"]["harness_seed"] == 20260829
    assert validated["run_id"] == manifest["run_id"]


def test_manifest_identity_changes_when_a_bound_input_changes(tmp_path, monkeypatch):
    root, first, paths = _manifest(tmp_path, monkeypatch)
    _write(paths["prompt_bundle"], {"name": "prompt_bundle", "revision": 2})
    second = materialization.build_launch_manifest(
        project_root=root,
        input_paths=paths,
        artifact_root=tmp_path / "formal-2",
        identity_registry_root=tmp_path / "registry",
        source_commit="a" * 40,
        recorded_at_utc=NOW,
    )
    assert first["manifest_identity_sha256"] != second["manifest_identity_sha256"]
    assert first["run_id"] != second["run_id"]


def test_unsigned_authorization_exactly_binds_manifest(tmp_path, monkeypatch):
    root, manifest, _paths = _manifest(tmp_path, monkeypatch)
    authorization = materialization.build_unsigned_main_authorization(
        manifest=manifest,
        authorization_id="phase3-main-owner-auth-test",
        approved_at_utc=NOW + timedelta(minutes=1),
        valid_until_utc=NOW + timedelta(days=45),
    )
    phase3_main_manifest.validate_main_manifest(manifest, project_root=root)
    validated = phase3_main_manifest.validate_main_authorization(
        authorization,
        manifest,
        as_of=NOW + timedelta(minutes=1),
    )
    assert authorization["exact_text"] == (
        phase3_main_manifest.expected_authorization_text(manifest)
    )
    assert authorization["maximum_reviewer_dispatches"] == 59_040
    assert validated["authorization_id"] == "phase3-main-owner-auth-test"


def test_builder_rejects_wrong_account_and_nonidentical_harness(tmp_path, monkeypatch):
    root, paths = _inputs(tmp_path, monkeypatch)
    billing = json.loads(paths["billing_reconciliation"].read_text(encoding="utf-8"))
    billing["billing_scope"]["account_identity_sha256"] = "c" * 64
    _write(paths["billing_reconciliation"], billing)
    with pytest.raises(
        materialization.MainLaunchMaterializationError,
        match="owner-selected account",
    ):
        materialization.build_launch_manifest(
            project_root=root,
            input_paths=paths,
            artifact_root=tmp_path / "formal",
            identity_registry_root=tmp_path / "registry",
            source_commit="a" * 40,
            recorded_at_utc=NOW,
        )

    billing["billing_scope"]["account_identity_sha256"] = (
        materialization.ACCOUNT_IDENTITY_SHA256
    )
    _write(paths["billing_reconciliation"], billing)
    receipt = json.loads(paths["harness_receipt"].read_text(encoding="utf-8"))
    receipt["rerun_output_store_sha256"] = "c" * 64
    _write(paths["harness_receipt"], receipt)
    with pytest.raises(
        materialization.MainLaunchMaterializationError,
        match="not one bit-identical",
    ):
        materialization.build_launch_manifest(
            project_root=root,
            input_paths=paths,
            artifact_root=tmp_path / "formal",
            identity_registry_root=tmp_path / "registry",
            source_commit="a" * 40,
            recorded_at_utc=NOW,
        )


def test_load_input_paths_requires_exact_capacity_signature_sidecar(
    tmp_path, monkeypatch,
):
    root, paths = _inputs(tmp_path, monkeypatch)
    mapping = {
        name: path.relative_to(root).as_posix()
        for name, path in paths.items()
    }
    source = _write(root / "input-paths.json", mapping)
    loaded = materialization.load_input_paths(source, project_root=root)
    assert loaded == {name: path.resolve() for name, path in paths.items()}

    changed = deepcopy(mapping)
    changed["capacity_execution_authorization_signature"] = (
        "inputs/not-the-sidecar.sig"
    )
    _write(root / "inputs" / "not-the-sidecar.sig", "fixture signature\n")
    changed_source = _write(root / "changed-input-paths.json", changed)
    with pytest.raises(
        materialization.MainLaunchMaterializationError,
        match="exact detached sidecar",
    ):
        materialization.load_input_paths(changed_source, project_root=root)


def test_write_json_exclusive_refuses_overwrite(tmp_path):
    path = tmp_path / "launch.json"
    raw = materialization.write_json_exclusive(path, {"b": 2, "a": 1})
    assert raw == b'{"a":1,"b":2}\n'
    with pytest.raises(
        materialization.MainLaunchMaterializationError,
        match="refusing to overwrite",
    ):
        materialization.write_json_exclusive(path, {"a": 1})
