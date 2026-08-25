"""Tests for offline Phase 3 v3 price snapshot materialization."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rejudge import phase3_v3_inputs as inputs
from rejudge import phase3_v3_materialization as protocol_materialization
from rejudge import phase3_v3_price_materialization as prices

from tests.test_phase3_v3_materialization import AMENDMENT, _resolution


ROOT = Path(__file__).resolve().parents[1]
V2 = json.loads(
    (ROOT / protocol_materialization.V2_PROTOCOL_PATH).read_text(encoding="utf-8"))
DESIGN = json.loads(
    (ROOT / protocol_materialization.DESIGN_PATH).read_text(encoding="utf-8"))


def _protocol():
    return protocol_materialization.materialize_protocol(
        V2, DESIGN,
        _resolution(protocol_materialization.PROVIDER_UNAVAILABLE_OUTCOME),
        amendment=AMENDMENT,
    )


def _catalog(protocol):
    return [
        {
            "id": model,
            "type": "chat",
            "pricing": {"input": float(index + 1), "output": float(index + 2)},
        }
        for index, model in enumerate(protocol["roster"]["judges_final"])
    ]


def _endpoints(protocol):
    return [
        {
            "model": model,
            "name": model,
            "type": "serverless",
            "state": "STARTED",
        }
        for model in protocol["roster"]["judges_final"]
    ]


def test_builder_binds_saved_catalog_and_final_roster(tmp_path: Path):
    protocol = _protocol()
    catalog = _catalog(protocol)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    snapshot = prices.build_price_snapshot(
        protocol=protocol,
        raw_catalog=catalog,
        raw_catalog_path=catalog_path,
        verified_at_utc="2026-08-29T01:00:00Z",
        project_root=tmp_path,
    )
    assert set(snapshot["models"]) == set(protocol["roster"]["judges_final"])
    assert snapshot["execution_authorized"] is False
    assert snapshot["raw_catalog"]["path"] == "catalog.json"
    report = inputs.validate_price_snapshot(
        snapshot,
        protocol=protocol,
        as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
        project_root=tmp_path,
    )
    assert report["age_seconds"] == 3600


def test_builder_rejects_missing_model_and_file_argument_drift(tmp_path: Path):
    protocol = _protocol()
    catalog = _catalog(protocol)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(prices.PriceMaterializationError, match="differs"):
        prices.build_price_snapshot(
            protocol=protocol,
            raw_catalog=catalog[:-1],
            raw_catalog_path=catalog_path,
            verified_at_utc="2026-08-29T01:00:00Z",
            project_root=tmp_path,
        )

    catalog = catalog[:-1]
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(prices.PriceMaterializationError, match="absent"):
        prices.build_price_snapshot(
            protocol=protocol,
            raw_catalog=catalog,
            raw_catalog_path=catalog_path,
            verified_at_utc="2026-08-29T01:00:00Z",
            project_root=tmp_path,
        )


def test_builder_rejects_zero_catalog_prices(tmp_path: Path):
    protocol = _protocol()
    catalog = _catalog(protocol)
    catalog[0]["pricing"]["input"] = 0
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(prices.PriceMaterializationError, match="positive"):
        prices.build_price_snapshot(
            protocol=protocol,
            raw_catalog=catalog,
            raw_catalog_path=catalog_path,
            verified_at_utc="2026-08-29T01:00:00Z",
            project_root=tmp_path,
        )


def test_v2_builder_binds_exact_started_serverless_endpoint_inventory(tmp_path: Path):
    protocol = _protocol()
    catalog = _catalog(protocol)
    endpoints = _endpoints(protocol)
    catalog_path = tmp_path / "catalog.json"
    endpoint_path = tmp_path / "endpoints.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    endpoint_path.write_text(json.dumps(endpoints), encoding="utf-8")
    snapshot = prices.build_price_snapshot(
        protocol=protocol,
        raw_catalog=catalog,
        raw_catalog_path=catalog_path,
        raw_serverless_endpoints=endpoints,
        raw_serverless_endpoints_path=endpoint_path,
        verified_at_utc="2026-08-29T01:00:00Z",
        project_root=tmp_path,
    )
    assert snapshot["schema_version"] == inputs.PRICE_SCHEMA_VERSION_V2
    assert snapshot["raw_serverless_endpoints"]["endpoint_count"] == 4
    report = inputs.validate_price_snapshot(
        snapshot,
        protocol=protocol,
        as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
        project_root=tmp_path,
    )
    assert report["raw_serverless_endpoints_checked"] is True

    endpoints[0]["state"] = "STOPPED"
    endpoint_path.write_text(json.dumps(endpoints), encoding="utf-8")
    with pytest.raises(prices.PriceMaterializationError, match="not STARTED"):
        prices.build_price_snapshot(
            protocol=protocol,
            raw_catalog=catalog,
            raw_catalog_path=catalog_path,
            raw_serverless_endpoints=endpoints,
            raw_serverless_endpoints_path=endpoint_path,
            verified_at_utc="2026-08-29T01:00:00Z",
            project_root=tmp_path,
        )
