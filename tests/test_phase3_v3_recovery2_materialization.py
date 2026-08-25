"""Focused tests for the Qwen3.5-9B recovery2 protocol (gemma-3n delisting)."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase3_plan
from rejudge import phase3_v3_materialization as legacy
from rejudge import phase3_v3_recovery2_materialization as recovery2
from rejudge.phase2_execution import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
PRIOR, AMENDMENT, HALT = recovery2.load_materialization_inputs(ROOT)


def _protocol():
    return recovery2.materialize_recovery2_protocol(
        PRIOR, AMENDMENT, HALT, project_root=ROOT, verify_archive=False)


def test_recovery2_protocol_replaces_only_gemma3n_and_preserves_offline_boundary():
    protocol = _protocol()
    phase3_plan.validate_protocol(protocol)
    assert protocol["roster"]["judges_final"] == list(recovery2.FINAL_ROSTER)
    assert recovery2.REMOVED_MODEL not in protocol["model_registry"]["models"]
    assert recovery2.ADDED_MODEL in protocol["model_registry"]["models"]
    assert protocol["authorization"]["canary_spend_authorized"] is False
    assert protocol["authorization"]["main_run_spend_authorized"] is False
    assert protocol["supersedes"]["canonical_sha256"] == canonical_sha256(PRIOR)
    assert protocol["authorization"]["prior_accounted_spend_usd"] == pytest.approx(
        recovery2.PRIOR_ACCOUNTED_SPEND_USD)


def test_recovery2_protocol_matches_the_committed_r4_document():
    import json

    committed = json.loads(
        (ROOT / "rejudge/phase3_protocol_v3_r4.json").read_text(encoding="utf-8"))
    assert _protocol() == committed


def test_recovery2_protocol_enumerates_fresh_four_judge_canary():
    protocol = _protocol()
    _main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, ROOT)
    canary = phase3_plan.enumerate_canary_cells(
        protocol, list(recovery2.FINAL_ROSTER), held_out_ids)
    summary = phase3_plan.summarize_cells(canary)
    assert summary["slot_count"] == 960
    assert summary["by_kind"][phase3_plan.CAPABILITY_ANCHOR_KIND] == 192


def test_recovery2_protocol_pin_binds_full_new_protocol():
    protocol = _protocol()
    pin = recovery2.build_protocol_pin(
        protocol, protocol_tracked_path="rejudge/phase3_protocol_v3_r4.json")
    legacy.validate_protocol_pin(pin, protocol)
    assert pin["protocol_canonical_sha256"] == canonical_sha256(protocol)
    assert pin["roster_resolution_canonical_sha256"] == canonical_sha256(HALT)


def test_recovery2_protocol_rejects_amendment_tamper():
    tampered = deepcopy(dict(AMENDMENT))
    tampered["replacement"]["ordered_candidates"][0]["model_id"] = "google/gemma-4-E4B-it"
    with pytest.raises(recovery2.Recovery2MaterializationError):
        recovery2.materialize_recovery2_protocol(
            PRIOR, tampered, HALT, project_root=ROOT, verify_archive=False)


def test_recovery2_protocol_rejects_source_binding_tamper():
    protocol = _protocol()
    changed = deepcopy(protocol)
    changed["source_bindings"]["canonical_json_sha256"][
        "rejudge/phase3_v3_qwen35_9b_screening_2026-08-25.json"] = "0" * 64
    changed.pop("protocol_content_sha256", None)
    changed["protocol_content_sha256"] = canonical_sha256(changed)
    with pytest.raises(phase3_plan.ProtocolValidationError):
        phase3_plan.validate_protocol(changed)
