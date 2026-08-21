from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase3_plan


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "rejudge" / "phase3_protocol.json"


@pytest.fixture(scope="module")
def protocol():
    return phase3_plan.load_protocol(PROTOCOL_PATH)


@pytest.fixture(scope="module")
def question_ids(protocol):
    return phase3_plan.load_reference_question_ids(protocol, ROOT)


@pytest.fixture(scope="module")
def main_question_ids(question_ids):
    return question_ids[0]


@pytest.fixture(scope="module")
def held_out_question_ids(question_ids):
    return question_ids[1]


# ---------------------------------------------------------------------------
# validate_protocol
# ---------------------------------------------------------------------------


def test_validate_protocol_accepts_the_frozen_protocol(protocol):
    assert protocol["schema_version"] == "phase3_plan_v1"
    assert protocol["offline_planning_only"] is True
    assert protocol["execution_authorized"] is False
    assert protocol["authorization"]["canary_spend_authorized"] is False
    assert protocol["authorization"]["main_run_spend_authorized"] is False
    assert protocol["cell_key_namespace"] == (
        "phase3-budget-knob-2026-08-18-v1.qb-d9e52c3339ab")


def test_validate_protocol_rejects_wrong_schema_version(protocol):
    mutated = deepcopy(protocol)
    mutated["schema_version"] = "phase3_plan_v3_does_not_exist"
    with pytest.raises(phase3_plan.ProtocolValidationError, match="unsupported schema_version"):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_dispatches_a_v1_body_tagged_v2_into_v2_checks(protocol):
    # schema_version alone selects the check-set (never "try v1, fall back to v2"): a v1-shaped
    # body relabelled as v2 must be evaluated under v2's OWN structural rules (e.g. exactly 2
    # roster.judges_new candidates, a supersedes block, decisions.context_guard) -- not silently
    # accepted, and not rejected with the generic "unsupported schema_version" message either,
    # since phase3_plan_v2 is itself a supported schema.
    mutated = deepcopy(protocol)
    mutated["schema_version"] = "phase3_plan_v2"
    with pytest.raises(phase3_plan.ProtocolValidationError,
                       match="ratified_design_pending_materialization"):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_a_tampered_hash(protocol):
    mutated = deepcopy(protocol)
    # Not a field any structural check inspects: only the whole-document hash pin can catch this.
    mutated["freeze_record"]["resolution"] += " (quietly edited)"
    with pytest.raises(
        phase3_plan.ProtocolValidationError, match="frozen phase-3 protocol hash drift"
    ):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_a_condition_list_missing_b8(protocol):
    mutated = deepcopy(protocol)
    mutated["debate_grid"]["conditions"] = [
        condition for condition in mutated["debate_grid"]["conditions"]
        if condition["query_budget"] != 8
    ]
    with pytest.raises(
        phase3_plan.ProtocolValidationError, match=r"query budgets \{0, 1, 2, 4, 8\}"
    ):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_a_changed_replicate_count(protocol):
    mutated = deepcopy(protocol)
    b8 = next(c for c in mutated["debate_grid"]["conditions"] if c["query_budget"] == 8)
    b8["judgment_replicates_per_transcript_side"] = 2
    with pytest.raises(
        phase3_plan.ProtocolValidationError, match="disagrees with the selected tail configuration"
    ):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_roster_shape_drift(protocol):
    mutated = deepcopy(protocol)
    mutated["roster"]["judges_continuing"].pop()
    with pytest.raises(
        phase3_plan.ProtocolValidationError, match="judges_continuing must list exactly 4"
    ):
        phase3_plan.validate_protocol(mutated)


def test_validate_protocol_rejects_execution_authorized_flip(protocol):
    mutated = deepcopy(protocol)
    mutated["execution_authorized"] = True
    with pytest.raises(phase3_plan.ProtocolValidationError, match="execution_authorized"):
        phase3_plan.validate_protocol(mutated)


# ---------------------------------------------------------------------------
# question IDs
# ---------------------------------------------------------------------------


def test_reference_question_ids_are_82_main_and_24_held_out_and_disjoint(
    main_question_ids, held_out_question_ids
):
    assert len(main_question_ids) == 82
    assert len(held_out_question_ids) == 24
    assert set(main_question_ids).isdisjoint(held_out_question_ids)


# ---------------------------------------------------------------------------
# candidate_roster_judges
# ---------------------------------------------------------------------------


def test_candidate_roster_judges_spans_4_to_7(protocol):
    assert len(phase3_plan.candidate_roster_judges(protocol, 4)) == 4
    assert len(phase3_plan.candidate_roster_judges(protocol, 7)) == 7
    with pytest.raises(phase3_plan.PlanValidationError):
        phase3_plan.candidate_roster_judges(protocol, 3)
    with pytest.raises(phase3_plan.PlanValidationError):
        phase3_plan.candidate_roster_judges(protocol, 8)


# ---------------------------------------------------------------------------
# main-grid slot counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "expected_slots"),
    [(4, 19_680), (5, 24_600), (6, 29_520), (7, 34_440)],
)
def test_main_slot_counts_match_slot_arithmetic_by_roster(
    protocol, main_question_ids, n, expected_slots
):
    judges = phase3_plan.candidate_roster_judges(protocol, n)
    cells = phase3_plan.enumerate_cells(protocol, judges, main_question_ids)
    summary = phase3_plan.summarize_cells(cells)
    assert summary["slot_count"] == expected_slots
    assert summary["transcript_cells"] == 492
    assert summary["all_cells"] == expected_slots + 492
    assert phase3_plan.duplicate_cell_keys(cells) == ()


def test_main_cell_keys_are_unique_and_stable(protocol, main_question_ids):
    judges = phase3_plan.candidate_roster_judges(protocol, 7)
    cells = phase3_plan.enumerate_cells(protocol, judges, main_question_ids)
    assert phase3_plan.duplicate_cell_keys(cells) == ()

    # Enumeration order must not affect the resulting key set (mirrors phase2_plan's
    # determinism guarantee).
    again = phase3_plan.enumerate_cells(protocol, list(reversed(judges)), reversed(main_question_ids))
    assert {c["cell_key"] for c in again} == {c["cell_key"] for c in cells}

    first = cells[0]
    expected_key = phase3_plan.make_cell_key(
        protocol["cell_key_namespace"],
        kind=first["kind"],
        condition=first["condition"],
        question_id=first["question_id"],
        judge_model=first["judge_model"],
        debater_model=first["debater_model"],
        transcript_index=first["transcript_index"],
        replicate_index=first["replicate_index"],
        query_budget=first["query_budget"],
    )
    assert first["cell_key"] == expected_key


# ---------------------------------------------------------------------------
# canary inventory
# ---------------------------------------------------------------------------


def test_canary_inventory_matches_the_frozen_slot_count_at_n7(protocol, held_out_question_ids):
    judges = phase3_plan.candidate_roster_judges(protocol, 7)
    cells = phase3_plan.enumerate_canary_cells(protocol, judges, held_out_question_ids)
    summary = phase3_plan.summarize_cells(cells)
    assert summary["slot_count"] == 1_680
    assert summary["transcript_cells"] == 48
    assert summary["by_kind"] == {
        "phase3_canary_debate_judgment": 1_344,  # 7 judges x (96 core b0 + 96 budget smoke)
        "phase3_canary_transcript_reference": 48,
        "phase3_capability_qa": 336,  # 7 judges x 48
    }
    assert phase3_plan.duplicate_cell_keys(cells) == ()

    canary_question_ids = {c["question_id"] for c in cells if "canary" in c["kind"]
                            or c["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND}
    assert canary_question_ids == set(held_out_question_ids)


def test_main_and_canary_cell_keys_are_mutually_unique(
    protocol, main_question_ids, held_out_question_ids
):
    judges = phase3_plan.candidate_roster_judges(protocol, 7)
    main_cells = phase3_plan.enumerate_cells(protocol, judges, main_question_ids)
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, judges, held_out_question_ids)
    assert phase3_plan.duplicate_cell_keys(main_cells + canary_cells) == ()


# ---------------------------------------------------------------------------
# b8 spot check
# ---------------------------------------------------------------------------


def test_b8_cell_carries_query_budget_8_and_phase2_would_not_produce_it(
    protocol, main_question_ids
):
    judges = phase3_plan.candidate_roster_judges(protocol, 4)
    cells = phase3_plan.enumerate_cells(protocol, judges, main_question_ids)
    b8_condition_id = next(
        c["id"] for c in protocol["debate_grid"]["conditions"] if c["query_budget"] == 8)
    b8_cells = [
        c for c in cells
        if c["kind"] == phase3_plan.MAIN_JUDGMENT_KIND and c["condition"] == b8_condition_id
    ]
    assert b8_cells
    assert all(c["query_budget"] == 8 for c in b8_cells)

    from rejudge import phase2_plan
    phase2_protocol = phase2_plan.load_protocol()
    phase2_budgets = {
        int(c["query_budget"]) for c in phase2_protocol["debate_grid"]["conditions"]
    }
    assert 8 not in phase2_budgets, (
        "the phase-2 frozen protocol's debate conditions must not already offer budget 8, "
        "or this cell would not be new to phase 3"
    )


# ---------------------------------------------------------------------------
# validate_cells fail-closed behavior
# ---------------------------------------------------------------------------


def test_validate_cells_is_fail_closed(protocol, main_question_ids):
    judges = phase3_plan.candidate_roster_judges(protocol, 4)
    cells = phase3_plan.enumerate_cells(protocol, judges, main_question_ids)
    namespace = protocol["cell_key_namespace"]

    first = cells[0]
    with pytest.raises(phase3_plan.PlanValidationError, match="duplicate cell keys"):
        phase3_plan.validate_cells([first, dict(first)], namespace)

    judgment = next(c for c in cells if c["kind"] == phase3_plan.MAIN_JUDGMENT_KIND)
    broken = dict(judgment)
    broken["dependency_keys"] = ["missing"]
    with pytest.raises(phase3_plan.PlanValidationError, match="missing dependencies"):
        phase3_plan.validate_cells([broken], namespace)

    by_key = {c["cell_key"]: c for c in cells}
    transcript = by_key[judgment["dependency_keys"][0]]
    other_transcript = next(
        c for c in cells
        if c["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND and c["cell_key"] != transcript["cell_key"]
    )
    extra = dict(judgment)
    extra["dependency_keys"] = [*judgment["dependency_keys"], other_transcript["cell_key"]]
    with pytest.raises(phase3_plan.PlanValidationError, match="exactly one transcript dependency"):
        phase3_plan.validate_cells([transcript, other_transcript, extra], namespace)


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def test_selftest_matches_the_frozen_slot_arithmetic():
    result = phase3_plan.selftest()
    assert result["main_slots_by_n"] == {4: 19_680, 5: 24_600, 6: 29_520, 7: 34_440}
    assert result["canary_slots_n7"] == 1_680
