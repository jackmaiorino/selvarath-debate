"""Focused tests for the N=2 recovery3 protocol (empty-verdict discovery)."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase3_plan
from rejudge import phase3_v3_materialization as legacy
from rejudge import phase3_v3_recovery3_materialization as recovery3
from rejudge.phase2_execution import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
PRIOR, AMENDMENT, DISCOVERY = recovery3.load_materialization_inputs(ROOT)


def _protocol():
    return recovery3.materialize_recovery3_protocol(
        PRIOR, AMENDMENT, DISCOVERY, project_root=ROOT, verify_archive=False)


def test_recovery3_protocol_removes_both_judges_without_replacement():
    protocol = _protocol()
    phase3_plan.validate_protocol(protocol)
    assert protocol["roster"]["judges_final"] == list(recovery3.FINAL_ROSTER)
    assert protocol["roster"]["final_size"] == 2
    assert "Qwen/Qwen3.5-9B" not in protocol["model_registry"]["models"]
    assert protocol["model_registry"]["models"][recovery3.CHECKER_ONLY_MODEL] == {
        "billed_roles": ["query_checker"]}
    replacement = protocol["roster_resolution"]["replacement"]
    assert replacement["removed_models"] == list(recovery3.REMOVED_MODELS)
    assert replacement["added_model"] is None
    assert replacement["checker_only_model"] == recovery3.CHECKER_ONLY_MODEL
    assert protocol["roster_resolution"]["outcome"] == "excluded_completion_infeasible"
    assert protocol["authorization"]["canary_spend_authorized"] is False
    assert protocol["authorization"]["main_run_spend_authorized"] is False
    assert protocol["supersedes"]["canonical_sha256"] == canonical_sha256(PRIOR)
    assert protocol["authorization"]["prior_accounted_spend_usd"] == pytest.approx(
        recovery3.PRIOR_ACCOUNTED_SPEND_USD)


def test_recovery3_protocol_matches_the_committed_r5_document():
    import json

    committed = json.loads(
        (ROOT / "rejudge/phase3_protocol_v3_r5.json").read_text(encoding="utf-8"))
    assert _protocol() == committed


def test_recovery3_protocol_enumerates_fresh_two_judge_canary():
    protocol = _protocol()
    _main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, ROOT)
    canary = phase3_plan.enumerate_canary_cells(
        protocol, list(recovery3.FINAL_ROSTER), held_out_ids)
    summary = phase3_plan.summarize_cells(canary)
    assert summary["slot_count"] == 480
    assert summary["by_kind"][phase3_plan.CAPABILITY_ANCHOR_KIND] == 96


def test_recovery3_protocol_pin_binds_full_new_protocol():
    protocol = _protocol()
    pin = recovery3.build_protocol_pin(
        protocol, protocol_tracked_path="rejudge/phase3_protocol_v3_r5.json")
    legacy.validate_protocol_pin(pin, protocol)
    assert pin["protocol_canonical_sha256"] == canonical_sha256(protocol)
    assert pin["roster_resolution_canonical_sha256"] == canonical_sha256(DISCOVERY)


def test_recovery3_protocol_rejects_a_verdict_budget_tamper():
    tampered = deepcopy(dict(AMENDMENT))
    tampered["verdict_budget_raise"]["model_effective_request_max_tokens"][
        "Qwen/Qwen3.8-2.4T-A95B"] = 8192
    with pytest.raises(recovery3.Recovery3MaterializationError):
        recovery3.materialize_recovery3_protocol(
            PRIOR, tampered, DISCOVERY, project_root=ROOT, verify_archive=False)


def test_recovery3_protocol_rejects_a_smuggled_gemma_judge_seat():
    tampered = deepcopy(dict(AMENDMENT))
    tampered["roster_change"]["final_roster"] = [
        "google/gemma-4-31B-it",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "Qwen/Qwen3.8-2.4T-A95B",
    ]
    tampered["roster_change"]["final_size"] = 3
    with pytest.raises(recovery3.Recovery3MaterializationError):
        recovery3.materialize_recovery3_protocol(
            PRIOR, tampered, DISCOVERY, project_root=ROOT, verify_archive=False)


def test_recovery3_protocol_rejects_source_binding_tamper():
    protocol = _protocol()
    changed = deepcopy(protocol)
    changed["source_bindings"]["canonical_json_sha256"][
        "rejudge/phase3_v3_judgment_screen_results_record_2026-08-27.json"] = "0" * 64
    changed.pop("protocol_content_sha256", None)
    changed["protocol_content_sha256"] = canonical_sha256(changed)
    with pytest.raises(phase3_plan.ProtocolValidationError):
        phase3_plan.validate_protocol(changed)


def test_recovery3_slot_arithmetic_scales_to_two_judges():
    protocol = _protocol()
    arithmetic = protocol["debate_grid"]["slot_arithmetic"]
    assert arithmetic["final_roster_size"] == 2
    assert arithmetic["total_judgment_slots"] == 4920 * 2
    inventory = protocol["decisions"]["launch_gates"]["canary_slot_inventory"]
    assert inventory["fresh_judgment_slots"] == 384
    assert inventory["fresh_capability_anchor_slots"] == 96
    assert inventory["combined_fresh_gate_slots"] == 480
