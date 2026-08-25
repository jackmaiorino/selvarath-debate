"""Focused tests for the Qwen 3.8 recovery protocol."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase3_plan
from rejudge import phase3_v3_materialization as legacy
from rejudge import phase3_v3_recovery_materialization as recovery
from rejudge.phase2_execution import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
PRIOR, AMENDMENT, HALT = recovery.load_materialization_inputs(ROOT)


def _protocol():
    return recovery.materialize_recovery_protocol(
        PRIOR, AMENDMENT, HALT, project_root=ROOT, verify_archive=False)


def test_recovery_protocol_replaces_only_qwen_and_preserves_offline_boundary():
    protocol = _protocol()
    phase3_plan.validate_protocol(protocol)
    assert protocol["roster"]["judges_final"] == list(recovery.FINAL_ROSTER)
    assert recovery.REMOVED_MODEL not in protocol["model_registry"]["models"]
    assert recovery.ADDED_MODEL in protocol["model_registry"]["models"]
    assert protocol["authorization"]["canary_spend_authorized"] is False
    assert protocol["authorization"]["main_run_spend_authorized"] is False
    assert protocol["supersedes"]["canonical_sha256"] == canonical_sha256(PRIOR)


def test_recovery_protocol_enumerates_fresh_four_judge_canary():
    protocol = _protocol()
    main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, ROOT)
    main = phase3_plan.enumerate_cells(protocol, list(recovery.FINAL_ROSTER), main_ids)
    canary = phase3_plan.enumerate_canary_cells(
        protocol, list(recovery.FINAL_ROSTER), held_out_ids)
    assert phase3_plan.summarize_cells(main)["slot_count"] == 19_680
    summary = phase3_plan.summarize_cells(canary)
    assert summary["slot_count"] == 960
    assert summary["by_kind"][phase3_plan.CAPABILITY_ANCHOR_KIND] == 192


def test_recovery_protocol_pin_binds_full_new_protocol():
    protocol = _protocol()
    pin = recovery.build_protocol_pin(
        protocol, protocol_tracked_path="rejudge/phase3_protocol_v3_r3.json")
    legacy.validate_protocol_pin(pin, protocol)
    assert pin["protocol_canonical_sha256"] == canonical_sha256(protocol)
    assert pin["roster_resolution_canonical_sha256"] == canonical_sha256(HALT)


def test_recovery_protocol_rejects_source_binding_tamper():
    protocol = _protocol()
    changed = deepcopy(protocol)
    changed["source_bindings"]["canonical_json_sha256"][
        recovery.AMENDMENT_PATH.as_posix()] = "0" * 64
    changed["protocol_content_sha256"] = canonical_sha256({
        key: value for key, value in changed.items()
        if key != "protocol_content_sha256"
    })
    with pytest.raises(phase3_plan.ProtocolValidationError, match="recovery source binding"):
        phase3_plan.validate_protocol(changed)


def test_recovery_amendment_and_halt_are_non_authorizing():
    recovery.validate_amendment(AMENDMENT, project_root=ROOT)
    recovery.validate_halt_observation(HALT, verify_archive=False)
    changed = deepcopy(AMENDMENT)
    changed["execution_authorized"] = True
    with pytest.raises(
        recovery.RecoveryMaterializationError, match="canonical hash drifted"
    ):
        recovery.validate_amendment(changed)
