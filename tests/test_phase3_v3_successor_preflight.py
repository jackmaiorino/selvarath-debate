"""Focused tests for the phase-3 successor design preflight."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import phase3_v3_successor_preflight as preflight  # ty: ignore[unresolved-import]  # noqa: E402
from rejudge import phase3_v3_materialization as materialization  # noqa: E402
from tests.test_phase3_v3_inputs import (  # noqa: E402
    _price_snapshot,
    _tokenizer_manifest,
)
from tests.test_phase3_v3_materialization import AMENDMENT, _resolution  # noqa: E402


V2 = json.loads((REPO_ROOT / materialization.V2_PROTOCOL_PATH).read_text(encoding="utf-8"))
DESIGN = json.loads((REPO_ROOT / materialization.DESIGN_PATH).read_text(encoding="utf-8"))


def _protocol():
    return materialization.materialize_protocol(
        V2, DESIGN, _resolution(materialization.PROVIDER_UNAVAILABLE_OUTCOME),
        amendment=AMENDMENT)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)


def test_tracked_successor_design_is_hash_bound_and_execution_disabled():
    design = preflight.validate_design(preflight.DESIGN_PATH_DEFAULT)
    assert design["execution_authorized"] is False
    assert design["main_run_authorized"] is False
    assert tuple(design["roster"]["provisional_judges"]) == (
        preflight.EXPECTED_PROVISIONAL_ROSTER)


def test_continuous_l90_uses_all_full_hour_blocks_and_discards_partial_tail():
    start = _utc(0)
    times = [
        start + timedelta(hours=block, minutes=minute)
        for block in range(10)
        for minute in range(1, 11)
    ]
    times.append(start + timedelta(hours=10, minutes=15))
    report = preflight.compute_continuous_rate_l90(
        ruling_times=times,
        opens_at=start,
        closes_at=start + timedelta(hours=10, minutes=30),
    )
    assert report["estimable"] is True
    assert report["full_block_count"] == 10
    assert report["counts_per_full_block"] == [10] * 10
    assert report["unique_rulings_in_discarded_tail"] == 1
    assert report["mean_rate"] == pytest.approx(240.0)
    assert report["sample_sd"] == pytest.approx(0.0)
    assert report["L90_rulings_per_24_elapsed_hours"] == pytest.approx(240.0)


def test_continuous_l90_keeps_zero_ruling_blocks():
    start = _utc(0)
    report = preflight.compute_continuous_rate_l90(
        ruling_times=[start + timedelta(minutes=1)],
        opens_at=start,
        closes_at=start + timedelta(hours=8),
    )
    assert report["counts_per_full_block"] == [1, 0, 0, 0, 0, 0, 0, 0]
    assert report["estimable"] is True
    assert report["L90_rulings_per_24_elapsed_hours"] == pytest.approx(0.0)


def test_continuous_l90_is_undefined_below_minimum_exposure():
    start = _utc(0)
    report = preflight.compute_continuous_rate_l90(
        ruling_times=[start],
        opens_at=start,
        closes_at=start + timedelta(hours=7, minutes=59),
    )
    assert report["full_block_count"] == 7
    assert report["estimable"] is False
    assert report["L90_rulings_per_24_elapsed_hours"] is None


def test_continuous_l90_rejects_out_of_window_timestamps():
    with pytest.raises(ValueError, match="outside"):
        preflight.compute_continuous_rate_l90(
            ruling_times=[_utc(9)], opens_at=_utc(10), closes_at=_utc(20))


def test_earliest_packet_commit_deduplicates_echoed_payloads():
    packets = [
        {"committed_at": _utc(1), "payloads": ["a", "b"]},
        {"committed_at": _utc(2), "payloads": ["a", "c"]},
    ]
    earliest, missing = preflight.earliest_required_packet_commits(
        ["a", "b", "c", "missing"], packets)
    assert earliest == {"a": _utc(1), "b": _utc(1), "c": _utc(2)}
    assert missing == ["missing"]


def test_unknown_charge_requires_separate_reserved_input_and_output_tokens():
    without_split = [
        {"status": "reserved", "attempt_id": "a", "estimated_tokens": 10},
        {"status": "unknown_charge", "attempt_id": "a", "estimated_tokens": 10},
    ]
    report = preflight.audit_unknown_charge_token_splits(without_split)
    assert report["missing_split_count"] == 1
    assert report["compatible_with_successor_forecast"] is False

    with_split: list[dict[str, object]] = [
        {
            "status": "reserved",
            "attempt_id": "a",
            "estimated_tokens": 10,
            "reserved_prompt_tokens": 7,
            "reserved_completion_tokens": 3,
        },
        {"status": "unknown_charge", "attempt_id": "a"},
    ]
    report = preflight.audit_unknown_charge_token_splits(with_split)
    assert report["unknown_charges_with_explicit_split"] == 1
    assert report["compatible_with_successor_forecast"] is True

    with_split[0]["estimated_tokens"] = 11
    report = preflight.audit_unknown_charge_token_splits(with_split)
    assert report["split_total_mismatch_count"] == 1
    assert report["compatible_with_successor_forecast"] is False


def test_charged_terminal_attempt_requires_actual_input_and_output_tokens():
    report = preflight.audit_unknown_charge_token_splits([
        {"status": "success", "attempt_id": "a", "prompt_tokens": 7,
         "completion_tokens": 3},
        {"status": "charged_malformed", "attempt_id": "b", "prompt_tokens": None,
         "completion_tokens": None},
    ])
    assert report["actual_token_terminal_count"] == 2
    assert report["missing_actual_token_count"] == 1
    assert report["compatible_with_successor_forecast"] is False


def test_exact_tokenizer_manifest_checks_full_role_shape_and_rejects_proxy():
    protocol = _protocol()
    manifest = _tokenizer_manifest(protocol)
    report = preflight.validate_exact_tokenizer_manifest(
        manifest, protocol=protocol, verify_files=False)
    assert report["verified_model_count"] == 4
    assert report["verified_file_count"] == 4

    manifest["models"][protocol["roster"]["judges_final"][-1]][
        "classification"] = "proxy_tokenizer_estimate"
    with pytest.raises(ValueError, match="not exact"):
        preflight.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)


def test_price_snapshot_requires_all_models_serverless_and_younger_than_24_hours(
    tmp_path: Path,
):
    protocol = _protocol()
    as_of = datetime(2026, 8, 29, 2, tzinfo=timezone.utc)
    snapshot = _price_snapshot(
        protocol, tmp_path / "catalog.json", verified_at="2026-08-29T01:00:00Z")
    report = preflight.validate_price_snapshot(
        snapshot, protocol=protocol, as_of=as_of)
    assert report["age_seconds"] == 3600.0

    stale_as_of = as_of + timedelta(hours=24, seconds=1)
    with pytest.raises(ValueError, match="24-hour"):
        preflight.validate_price_snapshot(
            snapshot, protocol=protocol, as_of=stale_as_of)


def test_live_v2_archive_diagnostic_is_read_only_and_not_paid_ready():
    if not preflight.ARCHIVE_DIR_DEFAULT.is_dir():
        pytest.skip("bound v2 archive is not mounted")
    artifact = preflight.build_preflight(
        design_path=preflight.DESIGN_PATH_DEFAULT,
        closeout_path=preflight.CLOSEOUT_PATH_DEFAULT,
        archive_dir=preflight.ARCHIVE_DIR_DEFAULT,
    )
    pace = artifact["historical_v2_pace_diagnostic_only"]
    assert pace["full_block_count"] == 11
    assert pace["unique_rulings_in_supplied_window"] == 1352
    assert pace["L90_rulings_per_24_elapsed_hours"] == pytest.approx(2287.2477463754103)
    assert artifact["historical_v2_usage_forecast_compatibility"][
        "missing_split_count"] == 149
    assert artifact["successor_design_ready"] is True
    assert artifact["paid_preflight_ready"] is False
    assert artifact["main_authorization_ready"] is False
