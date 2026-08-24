"""Tests for local Phase 3 v3 run-manifest environment capture."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rejudge import phase3_v3_inputs
from rejudge import phase3_v3_run_manifest_materialization as materialization

from tests.test_phase3_v3_run_manifest import _artifacts


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_materializer_captures_bound_files_and_local_environment(tmp_path: Path):
    protocol, pin, tokenizer, prices = _artifacts()
    protocol_path = _write(tmp_path / "rejudge/phase3_protocol_v3.json", protocol)
    pin_path = _write(tmp_path / "rejudge/phase3_protocol_v3_pin.json", pin)
    tokenizer_path = _write(
        tmp_path / "rejudge/phase3_v3_exact_tokenizer_manifest.json", tokenizer)
    prices_path = _write(tmp_path / "rejudge/phase3_v3_price_snapshot.json", prices)
    lock_path = tmp_path / "uv.lock"
    lock_path.write_text("version = 1\n", encoding="utf-8")

    manifest = materialization.materialize_run_manifest(
        protocol_path=protocol_path,
        protocol_pin_path=pin_path,
        tokenizer_manifest_path=tokenizer_path,
        price_snapshot_path=prices_path,
        dependency_lock_path=lock_path,
        seeds={"harness": 11, "analysis_bootstrap": 12},
        planned_output_paths=["E:/archive/phase3-v3/results.jsonl"],
        gpu_ordinal_or_not_used="not_used",
        harness_seed_name="harness",
        project_root=tmp_path,
        recorded_at_utc="2026-08-29T02:00:00Z",
        require_clean_git=False,
        git_commit="1" * 40,
        python_version="3.12.11",
        verify_external_files=False,
    )
    assert manifest["git_commit"] == "1" * 40
    assert manifest["python_version"] == "3.12.11"
    assert manifest["dependency_lock_sha256"] == phase3_v3_inputs.sha256_file(lock_path)
    assert set(manifest["input_sha256s"]) == {
        "rejudge/phase3_protocol_v3.json",
        "rejudge/phase3_protocol_v3_pin.json",
        "rejudge/phase3_v3_exact_tokenizer_manifest.json",
        "rejudge/phase3_v3_price_snapshot.json",
    }
    assert manifest["execution_authorized"] is False


def test_materializer_requires_every_input_file(tmp_path: Path):
    with pytest.raises(materialization.RunManifestMaterializationError, match="does not exist"):
        materialization.materialize_run_manifest(
            protocol_path="missing.json",
            protocol_pin_path="missing-pin.json",
            tokenizer_manifest_path="missing-tokenizer.json",
            price_snapshot_path="missing-price.json",
            dependency_lock_path="missing-lock",
            seeds={"harness": 1},
            planned_output_paths=["result.jsonl"],
            gpu_ordinal_or_not_used="not_used",
            harness_seed_name="harness",
            project_root=tmp_path,
            require_clean_git=False,
        )
