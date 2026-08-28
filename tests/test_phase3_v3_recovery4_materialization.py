"""Focused tests for the Llama-checker recovery4 protocol (r25 concentration stop)."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase3_plan
from rejudge import phase3_v3_materialization as legacy
from rejudge import phase3_v3_recovery4_materialization as recovery4
from rejudge.phase2_execution import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
PRIOR, AMENDMENT, STOP = recovery4.load_materialization_inputs(ROOT)


def _protocol():
    return recovery4.materialize_recovery4_protocol(
        PRIOR, AMENDMENT, STOP, project_root=ROOT)


def test_recovery4_protocol_substitutes_only_the_checker():
    protocol = _protocol()
    phase3_plan.validate_protocol(protocol)
    assert protocol["roster"]["judges_final"] == list(recovery4.FINAL_ROSTER)
    assert protocol["roster"]["query_checker"] == recovery4.CHECKER_MODEL
    assert recovery4.REMOVED_CHECKER not in protocol["model_registry"]["models"]
    assert "query_checker" in protocol["model_registry"]["models"][
        recovery4.CHECKER_MODEL]["billed_roles"]
    replacement = protocol["roster_resolution"]["replacement"]
    assert replacement["checker_only_model"] is None
    assert replacement["checker_substitution"] == {
        "removed_checker": recovery4.REMOVED_CHECKER,
        "added_checker": recovery4.CHECKER_MODEL,
    }
    assert protocol["authorization"]["canary_spend_authorized"] is False
    assert protocol["authorization"]["main_run_spend_authorized"] is False
    assert protocol["supersedes"]["canonical_sha256"] == canonical_sha256(PRIOR)
    assert protocol["authorization"]["prior_accounted_spend_usd"] == pytest.approx(
        recovery4.PRIOR_ACCOUNTED_SPEND_USD)


def test_recovery4_protocol_matches_the_committed_r6_document():
    import json

    committed = json.loads(
        (ROOT / "rejudge/phase3_protocol_v3_r6.json").read_text(encoding="utf-8"))
    assert _protocol() == committed


def test_recovery4_keeps_the_two_judge_inventory():
    protocol = _protocol()
    inventory = protocol["decisions"]["launch_gates"]["canary_slot_inventory"]
    assert inventory["fresh_judgment_slots"] == 384
    assert inventory["fresh_capability_anchor_slots"] == 96
    assert protocol["debate_grid"]["slot_arithmetic"]["final_roster_size"] == 2


def test_recovery4_protocol_pin_binds_full_new_protocol():
    protocol = _protocol()
    pin = recovery4.build_protocol_pin(
        protocol, protocol_tracked_path="rejudge/phase3_protocol_v3_r6.json")
    legacy.validate_protocol_pin(pin, protocol)
    assert pin["protocol_canonical_sha256"] == canonical_sha256(protocol)


def test_recovery4_rejects_a_checker_swap_tamper():
    tampered = deepcopy(dict(AMENDMENT))
    tampered["checker_substitution"]["added_checker"] = "Qwen/Qwen3.8-2.4T-A95B"
    with pytest.raises(recovery4.Recovery4MaterializationError):
        recovery4.materialize_recovery4_protocol(
            PRIOR, tampered, STOP, project_root=ROOT)


def test_recovery4_rejects_a_widened_token_budget():
    tampered = deepcopy(dict(AMENDMENT))
    tampered["checker_substitution"]["max_tokens"] = 4096
    with pytest.raises(recovery4.Recovery4MaterializationError):
        recovery4.materialize_recovery4_protocol(
            PRIOR, tampered, STOP, project_root=ROOT)


def test_recovery4_rejects_source_binding_tamper():
    protocol = _protocol()
    changed = deepcopy(protocol)
    changed["source_bindings"]["canonical_json_sha256"][
        "rejudge/phase3_v3_llama_checker_screen_results_record_2026-08-28.json"] = "0" * 64
    changed.pop("protocol_content_sha256", None)
    changed["protocol_content_sha256"] = canonical_sha256(changed)
    with pytest.raises(phase3_plan.ProtocolValidationError):
        phase3_plan.validate_protocol(changed)


def test_disparity_diagnostic_blocks_only_on_the_frozen_rule():
    from rejudge.phase3_v3_live import _checker_origin_disparity

    def rows(a_reject, a_allow, b_reject, b_allow):
        events_a = ([{"checker_decision": "reject"}] * a_reject
                    + [{"checker_decision": "allow"}] * a_allow)
        events_b = ([{"checker_decision": "reject"}] * b_reject
                    + [{"checker_decision": "allow"}] * b_allow)
        return [
            {"judge_model": "judge-A", "gate_events": events_a},
            {"judge_model": "judge-B", "gate_events": events_b},
        ]

    balanced = _checker_origin_disparity(rows(10, 40, 10, 40))
    assert balanced["treatment_effect_interpretation_blocked"] is False
    # Large and significant disparity: 40/50 vs 5/50 rejections.
    skewed = _checker_origin_disparity(rows(40, 10, 5, 45))
    assert skewed["treatment_effect_interpretation_blocked"] is True
    # Significant but under the 10-point floor cannot block by the frozen rule; a
    # 9-point spread at huge n would be p<0.05 yet stays unblocked.
    screening = _checker_origin_disparity(rows(9, 39, 14, 34))
    assert screening["treatment_effect_interpretation_blocked"] is False
    assert screening["fisher_exact_two_sided_p"] == pytest.approx(0.339, abs=0.001)
