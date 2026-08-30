"""Focused exact-provenance tests for the Phase 3 main transcript partition."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from rejudge import phase3_main_runner
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_execution import canonical_sha256
from rejudge.phase3_main_transcript_provenance import (
    MainTranscriptProvenanceError,
    load_manifest_bound_main_transcript_provenance,
    verify_main_transcript_store,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inventory() -> phase3_main_runner.MainInventory:
    return phase3_main_runner.build_canonical_main_inventory(REPO_ROOT)


@pytest.fixture(scope="module")
def frozen_bundle(inventory: phase3_main_runner.MainInventory) -> dict:
    entries = []
    for cell in inventory.transcript_cells:
        cell_key = str(cell["cell_key"])
        question_id = str(cell["question_id"])
        debater = str(cell["debater_model"])
        transcript_index = int(cell["transcript_index"])
        payload = {
            "cell_key": f"source:{question_id}:{debater}:{transcript_index}",
            "question_id": question_id,
            "transcript_index": transcript_index,
            "debater_model": debater,
            "world": "fixture-world",
            "question": f"Fixture question {question_id}?",
            "correct_answer": "fixture correct",
            "wrong_answer": "fixture wrong",
            "honest_first": True,
            "debate_transcript": [
                {"speaker": "honest", "text": cell_key, "round": 1},
            ],
            "dry_run": False,
        }
        entries.append({
            "question_id": question_id,
            "world": payload["world"],
            "debater_model": debater,
            "transcript_index": transcript_index,
            "source_cell_key": payload["cell_key"],
            "source_event_hash": hashlib.sha256(cell_key.encode("utf-8")).hexdigest(),
            "transcript_sha256": canonical_sha256(payload),
            "transcript_payload": payload,
        })
    assert len(entries) == phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT
    return {
        "schema_version": "phase3_transcript_bundle_v1",
        "bundle": "main",
        "expected_transcript_count": len(entries),
        "actual_transcript_count": len(entries),
        "transcripts": entries,
    }


def _write_bound_material(
    tmp_path: Path,
    inventory: phase3_main_runner.MainInventory,
    bundle: dict,
):
    bundle_path = tmp_path / "main-transcript-bundle.json"
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    verification = {
        "schema_version": "phase3_transcript_verification_v1",
        "bundle_canonical_sha256": {
            "main_bundle": canonical_sha256(bundle),
            "canary_bundle": "0" * 64,
        },
    }
    verification_path = tmp_path / "transcript-verification.json"
    verification_path.write_text(
        json.dumps(verification, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    provenance = load_manifest_bound_main_transcript_provenance(
        inventory=inventory,
        main_bundle_path=bundle_path,
        transcript_verification_path=verification_path,
        expected_main_bundle_raw_sha256=hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        expected_transcript_verification_raw_sha256=(
            hashlib.sha256(verification_path.read_bytes()).hexdigest()),
    )
    return provenance


def _expected_results(provenance) -> list[tuple[str, dict]]:
    results = []
    for cell_key in provenance.ordered_cell_keys:
        result = provenance.expected_result(cell_key)
        assert result is not None
        results.append((cell_key, result))
    return results


def _write_result_store(path: Path, results: list[tuple[str, dict]]) -> None:
    previous = "genesis"
    rows = []
    for sequence, (cell_key, result) in enumerate(results):
        row = {
            "cell_key": cell_key,
            "result": result,
            "sequence": sequence,
            "prev_event_hash": previous,
        }
        row["event_hash"] = CellResultStore._row_hash(row)
        previous = row["event_hash"]
        rows.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_exact_492_transcript_results_are_reconstructed_and_store_verified(
    tmp_path: Path,
    inventory: phase3_main_runner.MainInventory,
    frozen_bundle: dict,
):
    provenance = _write_bound_material(tmp_path, inventory, copy.deepcopy(frozen_bundle))
    assert len(provenance.ordered_cell_keys) == 492
    assert len(set(provenance.ordered_cell_keys)) == 492

    results = _expected_results(provenance)
    judgment = inventory.judgment_cells[0]
    judgment_key = str(judgment["cell_key"])
    results.append((judgment_key, {"cell_key": judgment_key, "verdict": "A"}))
    result_path = tmp_path / "main-results.jsonl"
    _write_result_store(result_path, results)

    verified = verify_main_transcript_store(
        result_store_path=result_path, provenance=provenance)
    assert verified.status == "exact_main_transcript_partition"
    assert verified.expected_transcript_count == 492
    assert verified.observed_transcript_count == 492
    assert verified.result_store_raw_sha256 == hashlib.sha256(
        result_path.read_bytes()).hexdigest()


def test_bundle_payload_drift_is_rejected_even_when_the_bundle_binding_is_updated(
    tmp_path: Path,
    inventory: phase3_main_runner.MainInventory,
    frozen_bundle: dict,
):
    drifted = copy.deepcopy(frozen_bundle)
    drifted["transcripts"][0]["transcript_payload"]["question"] = "drifted"
    with pytest.raises(MainTranscriptProvenanceError, match="payload drifted"):
        _write_bound_material(tmp_path, inventory, drifted)


def test_bundle_coverage_drift_is_rejected(
    tmp_path: Path,
    inventory: phase3_main_runner.MainInventory,
    frozen_bundle: dict,
):
    incomplete = copy.deepcopy(frozen_bundle)
    incomplete["transcripts"].pop()
    incomplete["actual_transcript_count"] = 491
    with pytest.raises(MainTranscriptProvenanceError, match="count fields must both equal 492"):
        _write_bound_material(tmp_path, inventory, incomplete)


def test_duplicate_bundle_mapping_is_rejected_before_result_comparison(
    tmp_path: Path,
    inventory: phase3_main_runner.MainInventory,
    frozen_bundle: dict,
):
    duplicated = copy.deepcopy(frozen_bundle)
    duplicated["transcripts"][-1] = copy.deepcopy(duplicated["transcripts"][0])
    with pytest.raises(MainTranscriptProvenanceError, match="repeats enumerator mapping"):
        _write_bound_material(tmp_path, inventory, duplicated)


def test_result_payload_drift_is_rejected(
    tmp_path: Path,
    inventory: phase3_main_runner.MainInventory,
    frozen_bundle: dict,
):
    provenance = _write_bound_material(tmp_path, inventory, copy.deepcopy(frozen_bundle))
    results = _expected_results(provenance)
    results[0][1]["question"] = "stored payload drift"
    result_path = tmp_path / "drifted-results.jsonl"
    _write_result_store(result_path, results)
    with pytest.raises(MainTranscriptProvenanceError, match="exact frozen payload"):
        verify_main_transcript_store(
            result_store_path=result_path, provenance=provenance)


def test_result_extra_field_is_rejected(
    tmp_path: Path,
    inventory: phase3_main_runner.MainInventory,
    frozen_bundle: dict,
):
    provenance = _write_bound_material(tmp_path, inventory, copy.deepcopy(frozen_bundle))
    results = _expected_results(provenance)
    results[0][1]["unexpected_result_field"] = "not in the frozen payload"
    result_path = tmp_path / "extra-field-results.jsonl"
    _write_result_store(result_path, results)
    with pytest.raises(
        MainTranscriptProvenanceError,
        match="including its field set",
    ):
        verify_main_transcript_store(
            result_store_path=result_path, provenance=provenance)
