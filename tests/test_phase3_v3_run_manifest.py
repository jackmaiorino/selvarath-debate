"""Tests for the small, non-authorizing Phase 3 v3 run manifest."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_v3_materialization as materialization
from rejudge import phase3_v3_run_manifest as run_manifest
from rejudge.phase2_execution import canonical_sha256

from tests.test_phase3_v3_inputs import _tokenizer_manifest
from tests.test_phase3_v3_materialization import _resolution


ROOT = Path(__file__).resolve().parents[1]
V2 = json.loads((ROOT / materialization.V2_PROTOCOL_PATH).read_text(encoding="utf-8"))
DESIGN = json.loads((ROOT / materialization.DESIGN_PATH).read_text(encoding="utf-8"))


def _artifacts():
    protocol = materialization.materialize_protocol(
        V2, DESIGN, _resolution("excluded_deadline"))
    pin = materialization.build_protocol_pin(
        protocol, protocol_tracked_path="rejudge/phase3_protocol_v3.json")
    tokenizer = _tokenizer_manifest(protocol)
    raw_catalog: list[dict[str, Any]] = [
        {
            "id": model,
            "type": "chat",
            "pricing": {"input": float(index + 1), "output": float(index + 2)},
        }
        for index, model in enumerate(protocol["roster"]["judges_final"])
    ]
    prices = {
        "schema_version": "phase3_v3_price_snapshot_v1",
        "provider": "Together",
        "verified_at_utc": "2026-08-29T01:00:00Z",
        "execution_authorized": False,
        "raw_catalog": {
            "path": "raw_catalog.json",
            "canonical_sha256": canonical_sha256(raw_catalog),
            "model_count": len(raw_catalog),
        },
        "models": {
            entry["id"]: {
                "serverless_available": True,
                "input_usd_per_million": entry["pricing"]["input"],
                "output_usd_per_million": entry["pricing"]["output"],
                "catalog_entry_sha256": canonical_sha256(entry),
            }
            for entry in raw_catalog
        },
    }
    return protocol, pin, tokenizer, prices


def _build_manifest(protocol, pin, tokenizer, prices):
    return run_manifest.build_run_manifest(
        protocol=protocol,
        protocol_pin=pin,
        tokenizer_manifest=tokenizer,
        price_snapshot=prices,
        recorded_at_utc="2026-08-29T01:00:00Z",
        git_commit="1" * 40,
        python_version="3.12.11",
        dependency_lock_sha256="2" * 64,
        linker_version_or_not_applicable="not_applicable",
        seeds={"harness": 20260829, "analysis_bootstrap": 20260830},
        input_sha256s={
            "rejudge/phase3_protocol_v3.json": canonical_sha256(protocol),
            "rejudge/phase3_protocol_v3_pin.json": canonical_sha256(pin),
            "rejudge/phase3_v3_exact_tokenizer_manifest.json": canonical_sha256(tokenizer),
            "rejudge/phase3_v3_price_snapshot.json": canonical_sha256(prices),
        },
        planned_output_paths=[
            "E:/selvarath-archive/phase3-v3/phase3_canary_results.jsonl",
            "E:/selvarath-archive/phase3-v3/phase3_usage.jsonl",
        ],
        gpu_ordinal_or_not_used="not_used",
        harness_seed_name="harness",
        verify_external_files=False,
    )


def _manifest():
    protocol, pin, tokenizer, prices = _artifacts()
    manifest = _build_manifest(protocol, pin, tokenizer, prices)
    return manifest, protocol, pin, tokenizer, prices


def test_small_manifest_has_exact_required_fields_and_cannot_authorize():
    manifest, protocol, pin, tokenizer, prices = _manifest()
    assert set(manifest) == run_manifest.MANIFEST_FIELDS
    assert manifest["status"] == "preflight"
    assert manifest["execution_authorized"] is False
    assert manifest["output_sha256s"] == {
        path: None for path in manifest["planned_output_paths"]}
    run_manifest.validate_run_manifest(
        manifest,
        protocol=protocol,
        protocol_pin=pin,
        tokenizer_manifest=tokenizer,
        price_snapshot=prices,
        verify_external_files=False,
    )


def test_manifest_run_id_changes_when_an_immutable_input_changes():
    manifest, *_ = _manifest()
    changed = deepcopy(manifest)
    changed["seeds"]["analysis_bootstrap"] += 1
    with pytest.raises(run_manifest.RunManifestError, match="run_id"):
        run_manifest.validate_run_manifest(changed)


def test_manifest_rejects_authorization_and_roster_drift():
    manifest, *_ = _manifest()
    changed = deepcopy(manifest)
    changed["execution_authorized"] = True
    with pytest.raises(run_manifest.RunManifestError, match="never authorize"):
        run_manifest.validate_run_manifest(changed)

    changed = deepcopy(manifest)
    changed["final_roster"] = changed["final_roster"][:-1]
    with pytest.raises(run_manifest.RunManifestError):
        run_manifest.validate_run_manifest(changed)


def test_manifest_requires_protocol_path_binding():
    manifest, protocol, pin, tokenizer, prices = _manifest()
    changed = deepcopy(manifest)
    del changed["input_sha256s"][pin["protocol_tracked_path"]]
    changed["run_id"] = (
        f"phase3-v3-{canonical_sha256(run_manifest._identity_payload(changed))[:16]}")
    with pytest.raises(run_manifest.RunManifestError, match="pinned protocol path"):
        run_manifest.validate_run_manifest(
            changed,
            protocol=protocol,
            protocol_pin=pin,
            tokenizer_manifest=tokenizer,
            price_snapshot=prices,
            verify_external_files=False,
        )


def test_manifest_builder_rejects_stale_prices_even_without_local_file_checks():
    protocol, pin, tokenizer, prices = _artifacts()
    prices["verified_at_utc"] = "2026-08-28T00:59:59Z"
    with pytest.raises(run_manifest.RunManifestError, match="fresh-price gate failed"):
        _build_manifest(protocol, pin, tokenizer, prices)


def test_finalize_requires_bit_identical_harness_hashes_and_all_output_hashes():
    manifest, *_ = _manifest()
    output_hashes = {path: "a" * 64 for path in manifest["planned_output_paths"]}
    completed = run_manifest.finalize_run_manifest(
        manifest,
        output_sha256s=output_hashes,
        first_output_store_sha256="b" * 64,
        rerun_output_store_sha256="b" * 64,
    )
    assert completed["status"] == "complete"
    assert completed["run_id"] == manifest["run_id"]

    with pytest.raises(run_manifest.RunManifestError, match="not bit-identical"):
        run_manifest.finalize_run_manifest(
            manifest,
            output_sha256s=output_hashes,
            first_output_store_sha256="b" * 64,
            rerun_output_store_sha256="c" * 64,
        )

    with pytest.raises(run_manifest.RunManifestError, match="keys"):
        run_manifest.finalize_run_manifest(
            manifest,
            output_sha256s={},
            first_output_store_sha256="b" * 64,
            rerun_output_store_sha256="b" * 64,
        )
