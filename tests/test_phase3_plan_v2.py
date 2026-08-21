"""Loader/validator coverage for the ratified phase-3 v2 protocol (rejudge/phase3_protocol_v2.json).

Sibling of tests/test_phase3_plan.py's v1 coverage: this file exercises schema_version_v2
selection, the v2-specific structural checks _validate_protocol_v2 adds (supersedes block,
ratified status, 6-judge roster, the v2 canary_slot_inventory shape, decisions.context_guard),
and the v2 slot self-test (selftest_v2).
"""
from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase3_plan

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_V2_PATH = ROOT / "rejudge" / "phase3_protocol_v2.json"


@pytest.fixture(scope="module")
def protocol_v2():
    return phase3_plan.load_protocol(PROTOCOL_V2_PATH)


@pytest.fixture(scope="module")
def question_ids_v2(protocol_v2):
    return phase3_plan.load_reference_question_ids(protocol_v2, ROOT)


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------


def test_validate_protocol_accepts_the_ratified_v2_protocol(protocol_v2):
    assert protocol_v2["schema_version"] == "phase3_plan_v2"
    assert protocol_v2["status"] == "ratified_design_pending_materialization"
    assert protocol_v2["offline_planning_only"] is True
    assert protocol_v2["execution_authorized"] is False
    assert protocol_v2["authorization"]["canary_spend_authorized"] is False
    assert protocol_v2["authorization"]["main_run_spend_authorized"] is False


def test_v2_pin_matches_the_real_file_on_disk():
    from rejudge.phase2_execution import canonical_sha256
    assert canonical_sha256(phase3_plan.load_protocol(PROTOCOL_V2_PATH)) == (
        phase3_plan.FROZEN_PROTOCOL_V2_CANONICAL_SHA256)
    # The two pins are genuinely distinct constants -- v1 stays immutable and separately pinned.
    assert phase3_plan.FROZEN_PROTOCOL_V2_CANONICAL_SHA256 != (
        phase3_plan.FROZEN_PROTOCOL_CANONICAL_SHA256)


def test_v2_supersedes_names_the_immutable_v1_pin(protocol_v2):
    assert protocol_v2["supersedes"]["canonical_sha256"] == (
        phase3_plan.FROZEN_PROTOCOL_CANONICAL_SHA256)


def test_v2_roster_is_six_judges(protocol_v2):
    roster = protocol_v2["roster"]
    assert len(roster["judges_continuing"]) == 4
    assert len(roster["judges_new"]) == 2


def test_v2_decisions_include_context_guard(protocol_v2):
    assert set(protocol_v2["decisions"]) == phase3_plan.EXPECTED_DECISION_KEYS_V2
    assert "context_guard" in protocol_v2["decisions"]


# ---------------------------------------------------------------------------
# selftest_v2
# ---------------------------------------------------------------------------


def test_selftest_v2_matches_the_ratified_slot_arithmetic():
    result = phase3_plan.selftest_v2()
    assert result == {
        "main_slots_n6": 29520,
        "canary_fresh_judgment_slots": 1152,
        "canary_anchor_cells": 288,
        "canary_combined_slots": 1440,
    }


def test_candidate_roster_judges_v2_gives_exactly_six(protocol_v2):
    judges = phase3_plan.candidate_roster_judges(protocol_v2, 6)
    assert len(judges) == 6
    assert len(set(judges)) == 6


def test_enumerate_cells_v2_matches_main_slot_count(protocol_v2, question_ids_v2):
    main_ids, _held_out = question_ids_v2
    judges = phase3_plan.candidate_roster_judges(protocol_v2, 6)
    cells = phase3_plan.enumerate_cells(protocol_v2, judges, main_ids)
    assert phase3_plan.summarize_cells(cells)["slot_count"] == 29520


def test_enumerate_canary_cells_v2_matches_combined_gate_inventory(protocol_v2, question_ids_v2):
    _main_ids, held_out_ids = question_ids_v2
    judges = phase3_plan.candidate_roster_judges(protocol_v2, 6)
    cells = phase3_plan.enumerate_canary_cells(protocol_v2, judges, held_out_ids)
    summary = phase3_plan.summarize_cells(cells)
    assert summary["by_kind"][phase3_plan.CANARY_JUDGMENT_KIND] == 1152
    assert summary["by_kind"][phase3_plan.CAPABILITY_ANCHOR_KIND] == 288
    assert summary["slot_count"] == 1440


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


def test_validate_protocol_rejects_a_tampered_v2_hash(protocol_v2):
    mutated = deepcopy(protocol_v2)
    mutated["decisions"]["spend"]["estimate_note"] += " (quietly edited)"
    with pytest.raises(phase3_plan.ProtocolValidationError,
                       match="frozen phase-3 v2 protocol hash drift"):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_v2_body_tagged_v1(protocol_v2):
    # schema_version alone selects the check-set: a v2-shaped body relabelled as v1 must fail v1's
    # OWN structural rules (e.g. roster.judges_continuing/judges_new decisions-key-set checks),
    # never be silently accepted or misrouted.
    mutated = deepcopy(protocol_v2)
    mutated["schema_version"] = "phase3_plan_v1"
    with pytest.raises(phase3_plan.ProtocolValidationError):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_v2_with_wrong_judges_new_count(protocol_v2):
    mutated = deepcopy(protocol_v2)
    mutated["roster"]["judges_new"] = mutated["roster"]["judges_new"][:1]
    with pytest.raises(phase3_plan.ProtocolValidationError, match="exactly 2 new candidates"):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_v2_missing_context_guard(protocol_v2):
    mutated = deepcopy(protocol_v2)
    del mutated["decisions"]["context_guard"]
    with pytest.raises(phase3_plan.ProtocolValidationError, match="v2 decisions must be exactly"):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_v2_with_wrong_canary_slot_totals(protocol_v2):
    mutated = deepcopy(protocol_v2)
    mutated["decisions"]["launch_gates"]["canary_slot_inventory"]["six_judge_totals"][
        "fresh_v2_judgment_slots"] = 999
    with pytest.raises(phase3_plan.ProtocolValidationError,
                       match="six_judge_totals must be exactly"):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_v2_not_ratified_status(protocol_v2):
    mutated = deepcopy(protocol_v2)
    mutated["status"] = "approved_design_pending_materialization"
    with pytest.raises(phase3_plan.ProtocolValidationError,
                       match="ratified_design_pending_materialization"):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_v2_missing_supersedes(protocol_v2):
    mutated = deepcopy(protocol_v2)
    del mutated["supersedes"]
    with pytest.raises(phase3_plan.ProtocolValidationError):
        phase3_plan.validate_protocol(mutated)
