"""Offline tests for the proposed Phase 3 main runtime policies."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase3_main_runtime_policies as policies


ROOT = Path(__file__).resolve().parents[1]
PRICE_POLICY = ROOT / "rejudge/phase3_main_price_change_policy_2026-08-30.json"
REVIEWER_POLICY = ROOT / "rejudge/phase3_main_reviewer_usage_policy_2026-08-30.json"


def _capacity_plan(maximum: int = policies.MAXIMUM_REVIEWER_DISPATCHES) -> dict:
    return {
        "capacity_thresholds": {
            "maximum_unique_review_payloads_zero_dedup": maximum,
        }
    }


def test_tracked_price_policy_is_exact_and_non_authorizing() -> None:
    assert policies.load_and_validate_price_change_policy(PRICE_POLICY) == {
        "schema_version": policies.PRICE_CHANGE_POLICY_SCHEMA,
        "operator_signal_filename": policies.PRICE_CHANGE_SIGNAL_FILENAME,
        "execution_authorized": False,
    }


def test_tracked_reviewer_policy_matches_capacity_bound_and_cannot_authorize() -> None:
    assert policies.load_and_validate_reviewer_usage_policy(
        REVIEWER_POLICY,
        capacity_plan=_capacity_plan(),
    ) == {
        "schema_version": policies.REVIEWER_USAGE_POLICY_SCHEMA,
        "usage_unit": policies.REVIEWER_USAGE_UNIT,
        "maximum_reviewer_dispatches": 59_040,
        "execution_authorized": False,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "ratified"),
        ("execution_authorized", True),
        ("provider_calls_authorized", True),
        ("main_run_spend_authorized", True),
    ],
)
def test_price_policy_rejects_authority_or_status_drift(field: str, value: object) -> None:
    candidate = deepcopy(policies.EXPECTED_PRICE_CHANGE_POLICY)
    candidate[field] = value
    with pytest.raises(policies.MainRuntimePolicyError, match="frozen candidate"):
        policies.validate_price_change_policy(candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "ratified"),
        ("execution_authorized", True),
        ("external_reviewer_dispatch_authorized", True),
        ("main_run_spend_authorized", True),
        ("maximum_reviewer_dispatches", 59_041),
    ],
)
def test_reviewer_policy_rejects_authority_or_ceiling_drift(
    field: str, value: object,
) -> None:
    candidate = deepcopy(policies.EXPECTED_REVIEWER_USAGE_POLICY)
    candidate[field] = value
    with pytest.raises(policies.MainRuntimePolicyError, match="frozen candidate"):
        policies.validate_reviewer_usage_policy(candidate)


def test_reviewer_policy_rejects_capacity_plan_mismatch() -> None:
    with pytest.raises(
        policies.MainRuntimePolicyError,
        match="differs from the bound capacity plan",
    ):
        policies.validate_reviewer_usage_policy(
            policies.EXPECTED_REVIEWER_USAGE_POLICY,
            capacity_plan=_capacity_plan(59_039),
        )


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":"first","schema_version":"second"}\n',
        encoding="utf-8",
    )
    with pytest.raises(policies.MainRuntimePolicyError, match="repeats key"):
        policies.load_and_validate_price_change_policy(path)


def _price_signal(root: Path, evidence: Path) -> dict:
    root = root.resolve()
    evidence = evidence.resolve()
    return {
        "schema_version": policies.PRICE_CHANGE_SIGNAL_SCHEMA,
        "status": "provider_price_change_observed",
        "run_id": "phase3-main-signal-test",
        "manifest_canonical_sha256": "a" * 64,
        "artifact_root": root.as_posix(),
        "artifact_root_sha256": hashlib.sha256(
            root.as_posix().encode("utf-8")).hexdigest(),
        "journal_execution_identity": "phase3-main-signal-test:" + "a" * 64,
        "observed_at_utc": "2026-08-30T20:00:00+00:00",
        "trigger_kind": "provider_catalog_observation",
        "evidence": {
            "path": evidence.as_posix(),
            "raw_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            "byte_count": len(evidence.read_bytes()),
        },
        "note": "A fresh provider catalog reports a changed price.",
        "stop_new_logical_provider_calls": True,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
    }


def test_price_change_signal_is_exact_evidence_bound_and_non_authorizing(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "provider-catalog.json"
    evidence.write_text('{"price_changed":true}\n', encoding="utf-8")
    signal = _price_signal(tmp_path, evidence)
    assert policies.validate_price_change_signal(
        signal,
        run_id="phase3-main-signal-test",
        manifest_canonical_sha256="a" * 64,
        artifact_root=tmp_path,
        journal_execution_identity="phase3-main-signal-test:" + "a" * 64,
    )["execution_authorized"] is False
    signal["provider_calls_authorized"] = True
    with pytest.raises(
        policies.MainRuntimePolicyError,
        match="differs from the started identity",
    ):
        policies.validate_price_change_signal(
            signal,
            run_id="phase3-main-signal-test",
            manifest_canonical_sha256="a" * 64,
            artifact_root=tmp_path,
            journal_execution_identity="phase3-main-signal-test:" + "a" * 64,
        )


def test_price_change_signal_rejects_drifted_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "provider-catalog.json"
    evidence.write_text("first\n", encoding="utf-8")
    signal = _price_signal(tmp_path, evidence)
    evidence.write_text("second\n", encoding="utf-8")
    with pytest.raises(policies.MainRuntimePolicyError, match="evidence bytes drifted"):
        policies.validate_price_change_signal(
            signal,
            run_id="phase3-main-signal-test",
            manifest_canonical_sha256="a" * 64,
            artifact_root=tmp_path,
            journal_execution_identity="phase3-main-signal-test:" + "a" * 64,
        )


def test_non_finite_policy_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "non-finite.json"
    path.write_text('{"value":NaN}\n', encoding="utf-8")
    with pytest.raises(policies.MainRuntimePolicyError, match="strict price-change policy"):
        policies.load_and_validate_price_change_policy(path)
