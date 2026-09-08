"""Read-only reopening of capacity evidence across an authenticated code repair."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_main_capacity_execution as execution
from scripts import codex_reviewer_batch
from test_phase3_main_capacity_execution import FakeReviewer, NOW, _execute, _fixture


def _historical_bindings(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        name: copy.deepcopy(fixture["manifest"]["code_bindings"][name])
        for name in ("reviewer_batch", "capacity_execution")
    }


def _upgrade_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model changed active files without touching repository or retained bytes."""
    original_binding = execution._artifact_binding
    module_path = Path(execution.__file__).resolve()

    def upgraded_binding(path: Path) -> dict[str, Any]:
        binding = original_binding(path)
        if Path(path).resolve() == module_path:
            return {**binding, "raw_sha256": "f" * 64, "byte_count": 999999}
        return binding

    runner_path, _, _ = codex_reviewer_batch._batch_runner_identity()
    monkeypatch.setattr(execution, "_artifact_binding", upgraded_binding)
    monkeypatch.setattr(
        codex_reviewer_batch,
        "_batch_runner_identity",
        lambda: (runner_path, "e" * 64, 999998),
    )


def test_historical_manifest_requires_explicit_exact_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    historical = _historical_bindings(fixture)
    original_raw = fixture["manifest_path"].read_bytes()
    _upgrade_runtime(monkeypatch)

    with pytest.raises(execution.CapacityExecutionError, match="exact rebuilt inputs"):
        execution.load_execution_manifest(
            fixture["manifest_path"], context=fixture["context"]
        )

    raw, reopened = execution.load_execution_manifest(
        fixture["manifest_path"],
        context=fixture["context"],
        historical_code_bindings=historical,
    )
    assert raw == original_raw == fixture["manifest_path"].read_bytes()
    assert reopened == fixture["manifest"]
    assert reopened["reviewer"]["concurrency"] == 12
    assert all(len(wave["packet_bindings"]) == 60 for wave in reopened["workload"]["waves"])
    assert historical == _historical_bindings(fixture)


@pytest.mark.parametrize(
    "mutation",
    ["missing_key", "extra_key", "extra_field", "wrong_path", "wrong_hash", "bad_hash", "bool_size"],
)
def test_historical_manifest_rejects_inexact_binding_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    fixture = _fixture(tmp_path)
    historical = _historical_bindings(fixture)
    _upgrade_runtime(monkeypatch)
    if mutation == "missing_key":
        del historical["capacity_execution"]
    elif mutation == "extra_key":
        historical["capacity_cli"] = fixture["manifest"]["code_bindings"]["capacity_cli"]
    elif mutation == "extra_field":
        historical["reviewer_batch"]["allowed"] = True
    elif mutation == "wrong_path":
        historical["reviewer_batch"]["path"] = (tmp_path / "other.py").as_posix()
    elif mutation == "wrong_hash":
        historical["reviewer_batch"]["raw_sha256"] = "d" * 64
    elif mutation == "bad_hash":
        historical["reviewer_batch"]["raw_sha256"] = "not-a-hash"
    else:
        historical["capacity_execution"]["byte_count"] = True
    with pytest.raises(execution.CapacityExecutionError):
        execution.load_execution_manifest(
            fixture["manifest_path"],
            context=fixture["context"],
            historical_code_bindings=historical,
        )


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("reviewer", "model", "other-model"),
        ("reviewer", "reasoning_effort", "low"),
        ("reviewer", "concurrency", 16),
        ("timing_limits", "maximum_seconds_per_wave", 7200),
        ("cohort", "packet_count", 181),
        ("cohort", "wave_size", 64),
        ("reviewer", "openai_provider_supports_websockets", True),
        ("reviewer", "cli", {"version": "different-runtime"}),
        ("code_bindings", "capacity_cli", {"path": "other"}),
    ],
)
def test_historical_bindings_never_relax_other_manifest_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    field: str,
    replacement: Any,
) -> None:
    fixture = _fixture(tmp_path, openai_provider_supports_websockets=False)
    historical = _historical_bindings(fixture)
    _upgrade_runtime(monkeypatch)
    altered = copy.deepcopy(fixture["manifest"])
    altered[section][field] = replacement
    with pytest.raises(execution.CapacityExecutionError):
        execution._validate_manifest(
            altered, context=fixture["context"], historical_code_bindings=historical
        )


def test_all_180_historical_receipts_reopen_without_mutation_or_redispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path, openai_provider_supports_websockets=False)
    reviewer = FakeReviewer()
    assert _execute(fixture, reviewer)["execution"] == "pass"
    assert len(reviewer.calls) == 180
    historical = _historical_bindings(fixture)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in fixture["root"].rglob("*") if path.is_file()
    }
    result = json.loads(fixture["result_path"].read_bytes())
    manifest_raw = fixture["manifest_path"].read_bytes()
    authorization_raw = fixture["authorization_path"].read_bytes()
    _upgrade_runtime(monkeypatch)
    kwargs = {
        "manifest": fixture["manifest"],
        "manifest_raw": manifest_raw,
        "authorization": fixture["authorization"],
        "authorization_raw": authorization_raw,
        "context": fixture["context"],
        "as_of_utc": NOW,
    }
    with pytest.raises(execution.CapacityExecutionError, match="active verifier"):
        execution.validate_execution_result(result, **kwargs)

    validation = execution.validate_execution_result(
        result, **kwargs, historical_code_bindings=historical
    )
    assert validation["reopened_invocation_receipts"] == 180
    assert validation["reopened_dispatch_reservations"] == 180
    assert validation["observed_usage"] == 180
    assert len(reviewer.calls) == 180
    assert before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in fixture["root"].rglob("*") if path.is_file()
    }

    changed_raw = json.loads(manifest_raw)
    changed_raw["reviewer"]["concurrency"] = 13
    with pytest.raises(execution.CapacityExecutionError, match="object differs from raw bytes"):
        execution.validate_execution_result(
            result,
            **{**kwargs, "manifest_raw": json.dumps(changed_raw).encode()},
            historical_code_bindings=historical,
        )


def test_historical_override_is_absent_from_execution_surfaces() -> None:
    for function in (
        execution.execute_capacity_preflight,
        execution._execute_capacity_preflight,
        execution._recheck_dispatch_inputs,
    ):
        assert "historical_code_bindings" not in inspect.signature(function).parameters
