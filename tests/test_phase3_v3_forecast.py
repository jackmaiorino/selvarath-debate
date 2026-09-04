"""Tests for zero-filled Phase 3 v3 slot-role forecast inputs."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rejudge import phase3_plan, phase3_v3_forecast as forecast
from rejudge import phase3_main_stage_cap
from rejudge import phase3_v3_materialization as materialization
from rejudge.phase2_execution import canonical_sha256

from tests.test_phase3_v3_inputs import _price_snapshot
from tests.test_phase3_v3_materialization import AMENDMENT, _resolution


ROOT = Path(__file__).resolve().parents[1]
V2 = json.loads((ROOT / materialization.V2_PROTOCOL_PATH).read_text(encoding="utf-8"))
DESIGN = json.loads((ROOT / materialization.DESIGN_PATH).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def protocol():
    return materialization.materialize_protocol(
        V2, DESIGN, _resolution(materialization.PROVIDER_UNAVAILABLE_OUTCOME),
        amendment=AMENDMENT)


@pytest.fixture
def planned_cells(protocol):
    judge = protocol["roster"]["judges_final"][0]
    return [
        {
            "cell_key": f"slot-{question}-{condition}-side{side}",
            "kind": phase3_plan.CANARY_JUDGMENT_KIND,
            "condition": condition,
            "question_id": question,
            "judge_model": judge,
            "debater_model": "debater",
            "transcript_index": 0,
            "replicate_index": side,
            "query_budget": 0 if condition == "b0" else 1,
        }
        for question in ("Q1", "Q2")
        for condition in ("b0", "sequential_b1")
        for side in (0, 1)
    ]


def _attempt(
    attempt_id: str,
    *,
    cell: dict,
    role: str,
    model: str,
    status: str,
    reserved_prompt: int = 100,
    reserved_completion: int = 50,
    prompt: int | None = 10,
    completion: int | None = 5,
) -> list[dict]:
    metadata = {
        "cell_key": cell["cell_key"],
        "call_role": role,
        "judge_model": cell["judge_model"],
        "condition": cell["condition"],
        "question_id": cell["question_id"],
    }
    stable = {
        "attempt_id": attempt_id,
        "model": model,
        "kind": "verdict" if role != "oracle_verification" else "oracle",
        "seed": 1,
        "attempt": 0,
        "metadata": metadata,
        "reserved_prompt_tokens": reserved_prompt,
        "reserved_completion_tokens": reserved_completion,
        "estimated_tokens": reserved_prompt + reserved_completion,
    }
    reservation = {**stable, "status": "reserved", "prompt_tokens": None,
                   "completion_tokens": None}
    terminal = {**stable, "status": status, "prompt_tokens": prompt,
                "completion_tokens": completion}
    return [reservation, terminal]


def _complete_usage(protocol, planned_cells):
    judge = protocol["roster"]["judges_final"][0]
    checker = protocol["roster"]["query_checker"]
    oracle = protocol["roster"]["oracle"]
    events: list[dict] = []
    for index, cell in enumerate(planned_cells):
        events.extend(_attempt(
            f"verdict-{index}", cell=cell, role="judge_verdict", model=judge,
            status="success", reserved_prompt=1000, reserved_completion=500,
            prompt=10 + index, completion=2 + index))
    q1_b1 = next(cell for cell in planned_cells
                 if cell["question_id"] == "Q1"
                 and cell["condition"] == "sequential_b1")
    events.extend(_attempt(
        "query-unknown", cell=q1_b1, role="judge_query", model=judge,
        status="unknown_charge", reserved_prompt=20, reserved_completion=30,
        prompt=None, completion=None))
    events.extend(_attempt(
        "query-success", cell=q1_b1, role="judge_query", model=judge,
        status="success", reserved_prompt=999, reserved_completion=999,
        prompt=5, completion=2))
    events.extend(_attempt(
        "checker-release", cell=q1_b1, role="query_checker", model=checker,
        status="released_no_charge", prompt=0, completion=0))
    events.extend(_attempt(
        "oracle-success", cell=q1_b1, role="oracle_verification", model=oracle,
        status="success", prompt=7, completion=1))
    return events


def _build(protocol, planned_cells, events):
    return forecast.build_slot_role_frame(
        protocol=protocol,
        planned_cells=planned_cells,
        completed_cell_keys=[cell["cell_key"] for cell in planned_cells],
        completed_results_sha256="e" * 64,
        usage_events=events,
        usage_ledger_sha256="f" * 64,
    )


def _exact_context(frame):
    transcript_keys = {}
    prompt_tokens = {}
    for record in frame["role_records"]:
        dimension = (
            "canary", record["debater_model"], record["question_id"],
            record["transcript_index"],
        )
        transcript_key = f"transcript-{record['question_id']}"
        transcript_keys[dimension] = transcript_key
        impossible = record["query_budget"] == 0 and record["role"] in {
            "judge_query", "query_checker", "oracle_verification",
        }
        if not impossible:
            variant = forecast._static_variant_id(record)
            prompt_tokens[(
                "canary", record["billed_model"], record["role"], variant, transcript_key,
            )] = 1
    return {
        "tokenizer_manifest_canonical_sha256": "a" * 64,
        "validation": {"validation": "pass", "local_files_checked": True},
        "transcript_keys": transcript_keys,
        "prompt_tokens": prompt_tokens,
    }


def test_frame_materializes_every_slot_role_and_includes_zero_rows(protocol, planned_cells):
    artifact = _build(protocol, planned_cells, _complete_usage(protocol, planned_cells))
    assert artifact["planned_judgment_slot_count"] == 8
    assert artifact["role_record_count"] == 32
    assert artifact["expected_role_record_count"] == 32
    assert artifact["execution_authorized"] is False

    q2_b1_query = next(
        record for record in artifact["role_records"]
        if record["question_id"] == "Q2" and record["condition"] == "sequential_b1"
        and record["mirrored_side"] == 0
        and record["role"] == "judge_query")
    assert q2_b1_query["zero_filled"] is True
    assert q2_b1_query["prompt_tokens"] == 0
    assert q2_b1_query["completion_tokens"] == 0
    assert q2_b1_query["attempt_count"] == 0


def test_unknown_charge_uses_full_split_and_success_uses_actual_tokens(protocol, planned_cells):
    artifact = _build(protocol, planned_cells, _complete_usage(protocol, planned_cells))
    q1_b1_query = next(
        record for record in artifact["role_records"]
        if record["question_id"] == "Q1" and record["condition"] == "sequential_b1"
        and record["mirrored_side"] == 0
        and record["role"] == "judge_query")
    assert q1_b1_query["prompt_tokens"] == 25
    assert q1_b1_query["completion_tokens"] == 32
    assert q1_b1_query["attempt_count"] == 2
    assert q1_b1_query["billed_attempt_count"] == 2
    assert q1_b1_query["unknown_charge_attempt_count"] == 1
    assert q1_b1_query["actual_token_attempt_count"] == 1


def test_checker_and_oracle_are_priced_by_actual_billed_model(protocol, planned_cells):
    artifact = _build(protocol, planned_cells, _complete_usage(protocol, planned_cells))
    q1_b1 = [record for record in artifact["role_records"]
             if record["question_id"] == "Q1" and record["condition"] == "sequential_b1"
             and record["mirrored_side"] == 0]
    by_role = {record["role"]: record for record in q1_b1}
    assert by_role["query_checker"]["billed_model"] == protocol["roster"]["query_checker"]
    assert by_role["oracle_verification"]["billed_model"] == protocol["roster"]["oracle"]
    assert by_role["judge_query"]["billed_model"] == by_role["judge_query"]["source_judge"]


def test_cluster_u90_keeps_all_zero_role_groups(protocol, planned_cells):
    artifact = _build(protocol, planned_cells, _complete_usage(protocol, planned_cells))
    checker_b1 = next(
        group for group in artifact["clustered_u90"]
        if group["role"] == "query_checker" and group["condition"] == "sequential_b1")
    assert checker_b1["question_count"] == 2
    assert checker_b1["metrics"]["prompt_tokens"]["mean"] == 0
    assert checker_b1["metrics"]["prompt_tokens"]["upper90"] == 0


def test_missing_unknown_charge_split_blocks_frame(protocol, planned_cells):
    events = _complete_usage(protocol, planned_cells)
    for event in events:
        if event.get("attempt_id") == "query-unknown":
            event.pop("reserved_prompt_tokens")
            event.pop("reserved_completion_tokens")
    with pytest.raises(forecast.ForecastInputError, match="reserved_prompt_tokens"):
        _build(protocol, planned_cells, events)


def test_wrong_billed_model_blocks_frame(protocol, planned_cells):
    events = _complete_usage(protocol, planned_cells)
    for event in events:
        if event.get("attempt_id") == "checker-release":
            event["model"] = protocol["roster"]["judges_final"][-1]
    with pytest.raises(forecast.ForecastInputError, match="billed model"):
        _build(protocol, planned_cells, events)


def test_terminal_attempt_cannot_change_reserved_token_split(protocol, planned_cells):
    events = _complete_usage(protocol, planned_cells)
    terminal = next(
        event for event in events
        if event.get("attempt_id") == "query-unknown"
        and event.get("status") == "unknown_charge"
    )
    terminal["reserved_prompt_tokens"] += 1
    with pytest.raises(forecast.ForecastInputError, match="reserved_prompt_tokens"):
        _build(protocol, planned_cells, events)


def test_incomplete_slot_set_and_unmatched_reservations_block(protocol, planned_cells):
    events = _complete_usage(protocol, planned_cells)
    with pytest.raises(forecast.ForecastInputError, match="resolved judgment set"):
        forecast.build_slot_role_frame(
            protocol=protocol,
            planned_cells=planned_cells,
            completed_cell_keys=[planned_cells[0]["cell_key"]],
            completed_results_sha256="e" * 64,
            usage_events=events,
            usage_ledger_sha256="f" * 64,
        )

    unmatched = deepcopy(events)
    unmatched.pop()
    with pytest.raises(forecast.ForecastInputError, match="unmatched reservation"):
        _build(protocol, planned_cells, unmatched)


def test_terminal_disposition_resolves_slot_without_charged_verdict(
    protocol, planned_cells,
):
    terminal_key = planned_cells[0]["cell_key"]
    events = [
        event for event in _complete_usage(protocol, planned_cells)
        if event.get("attempt_id") != "verdict-0"
    ]
    completed = [
        cell["cell_key"] for cell in planned_cells
        if cell["cell_key"] != terminal_key
    ]
    artifact = forecast.build_slot_role_frame(
        protocol=protocol,
        planned_cells=planned_cells,
        completed_cell_keys=completed,
        completed_results_sha256="e" * 64,
        usage_events=events,
        usage_ledger_sha256="f" * 64,
        terminal_cell_keys=[terminal_key],
        terminal_dispositions_sha256="d" * 64,
    )
    assert artifact["completed_judgment_slot_count"] == 7
    assert artifact["terminal_judgment_slot_count"] == 1
    assert artifact["resolved_judgment_slot_count"] == 8
    assert artifact["terminal_dispositions_sha256"] == "d" * 64
    terminal_verdict = next(
        record for record in artifact["role_records"]
        if record["cell_key"] == terminal_key and record["role"] == "judge_verdict"
    )
    assert terminal_verdict["zero_filled"] is True
    assert terminal_verdict["actual_token_attempt_count"] == 0


def test_terminal_disposition_binding_rejects_overlap_and_missing_hash(
    protocol, planned_cells,
):
    keys = [cell["cell_key"] for cell in planned_cells]
    events = _complete_usage(protocol, planned_cells)
    with pytest.raises(forecast.ForecastInputError, match="sets overlap"):
        forecast.build_slot_role_frame(
            protocol=protocol,
            planned_cells=planned_cells,
            completed_cell_keys=keys,
            completed_results_sha256="e" * 64,
            usage_events=events,
            usage_ledger_sha256="f" * 64,
            terminal_cell_keys=[keys[0]],
            terminal_dispositions_sha256="d" * 64,
        )
    with pytest.raises(forecast.ForecastInputError, match="terminal_dispositions_sha256"):
        forecast.build_slot_role_frame(
            protocol=protocol,
            planned_cells=planned_cells,
            completed_cell_keys=keys[1:],
            completed_results_sha256="e" * 64,
            usage_events=events,
            usage_ledger_sha256="f" * 64,
            terminal_cell_keys=[keys[0]],
        )


def test_dynamic_residual_subtracts_static_context_once_per_billed_attempt(
    protocol, planned_cells,
):
    frame = _build(protocol, planned_cells, _complete_usage(protocol, planned_cells))
    residual = forecast.build_dynamic_residual_frame(
        protocol=protocol,
        slot_role_frame=frame,
        exact_context_index=_exact_context(frame),
    )
    query = next(
        record for record in residual["role_records"]
        if record["question_id"] == "Q1"
        and record["condition"] == "sequential_b1"
        and record["mirrored_side"] == 0
        and record["role"] == "judge_query"
    )
    assert query["static_prompt_tokens"] == 2
    assert query["dynamic_prompt_tokens"] == 23
    assert residual["execution_authorized"] is False


def test_negative_dynamic_residual_blocks_forecast(protocol, planned_cells):
    frame = _build(protocol, planned_cells, _complete_usage(protocol, planned_cells))
    exact = _exact_context(frame)
    exact["prompt_tokens"] = {
        key: 100 for key in exact["prompt_tokens"]
    }
    with pytest.raises(forecast.ForecastInputError, match="negative dynamic-history"):
        forecast.build_dynamic_residual_frame(
            protocol=protocol,
            slot_role_frame=frame,
            exact_context_index=exact,
        )


def _full_projection_inputs(protocol, tmp_path: Path):
    main_question_ids, _held_out = phase3_plan.load_reference_question_ids(protocol, ROOT)
    cells = phase3_plan.enumerate_cells(
        protocol, protocol["roster"]["judges_final"], main_question_ids)
    transcript_keys = {}
    for cell in cells:
        if cell["kind"] != phase3_plan.MAIN_TRANSCRIPT_KIND:
            continue
        dimension = (
            "main", cell["debater_model"], cell["question_id"], cell["transcript_index"],
        )
        transcript_keys[dimension] = f"transcript-{cell['cell_key']}"

    tokenizer_sha = "a" * 64
    prompt_tokens = {}
    for cell in cells:
        if cell["kind"] != phase3_plan.MAIN_JUDGMENT_KIND:
            continue
        dimensions = forecast._cell_dimensions(protocol, cell, str(cell["cell_key"]))
        transcript_key = transcript_keys[(
            "main", cell["debater_model"], cell["question_id"], cell["transcript_index"],
        )]
        variant = (
            f"judge_verdict::{cell['condition']}::side{dimensions['mirrored_side']}")
        prompt_tokens[(
            "main", cell["judge_model"], "judge_verdict", variant, transcript_key,
        )] = 100
    exact = {
        "tokenizer_manifest_canonical_sha256": tokenizer_sha,
        "validation": {"validation": "pass", "local_files_checked": True},
        "transcript_keys": transcript_keys,
        "prompt_tokens": prompt_tokens,
    }

    estimators = []
    for source_judge in protocol["roster"]["judges_final"]:
        for condition in protocol["debate_grid"]["conditions"]:
            for role in forecast.CALL_ROLES:
                verdict = role == "judge_verdict"
                estimators.append({
                    "billed_model": forecast._billed_model(protocol, source_judge, role),
                    "source_judge": source_judge,
                    "role": role,
                    "condition": condition["id"],
                    "question_count": 2,
                    "metrics": {
                        "dynamic_prompt_tokens": {"upper90": 10.0 if verdict else 0.0},
                        "completion_tokens": {"upper90": 5.0 if verdict else 0.0},
                        "billed_attempt_count": {"upper90": 1.0 if verdict else 0.0},
                    },
                })
    dynamic = {
        "schema_version": forecast.DYNAMIC_SCHEMA_VERSION,
        "execution_authorized": False,
        "protocol_canonical_sha256": canonical_sha256(protocol),
        "tokenizer_manifest_canonical_sha256": tokenizer_sha,
        "clustered_u90": estimators,
    }
    prices = _price_snapshot(
        protocol, tmp_path / "catalog.json", verified_at="2026-08-29T01:00:00Z")
    return cells, exact, dynamic, prices


def test_cost_forecast_prices_complete_main_grid_and_cumulative_spend(protocol, tmp_path: Path):
    cells, exact, dynamic, prices = _full_projection_inputs(protocol, tmp_path)
    result = forecast.build_cost_forecast(
        protocol=protocol,
        planned_main_cells=cells,
        dynamic_residual_frame=dynamic,
        exact_context_index=exact,
        price_snapshot=prices,
        price_as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
        cumulative_spend_segments=[{
            "name": "v1-v2-and-successor-canary",
            "ledger_sha256": "b" * 64,
            "actual_spend_usd": 10.0,
            "uncertain_spend_usd": 2.0,
        }],
        project_root=str(tmp_path),
    )
    assert result["certification"] == "pass"
    assert result["execution_authorized"] is False
    assert result["planned_main_judgment_slot_count"] == 19_680
    assert len(result["line_items"]) == 80
    assert result["projected_main_usd"] > 0
    assert result["projected_stage_total_usd"] == pytest.approx(
        result["projected_main_usd"] + 12.0)
    assert all(
        item["rounded_line_cost_usd"] == round(item["rounded_line_cost_usd"], 2)
        for item in result["line_items"]
    )


def test_owner_ratification_supplies_the_main_stage_cap(tmp_path: Path):
    protocol = phase3_plan.load_protocol(
        ROOT / "rejudge" / "phase3_protocol_v3_r6.json"
    )
    cells, exact, dynamic, prices = _full_projection_inputs(protocol, tmp_path)
    ratification = json.loads(
        phase3_main_stage_cap.RATIFICATION_PATH.read_text(encoding="utf-8")
    )
    result = forecast.build_cost_forecast(
        protocol=protocol,
        planned_main_cells=cells,
        dynamic_residual_frame=dynamic,
        exact_context_index=exact,
        price_snapshot=prices,
        price_as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
        cumulative_spend_segments=[{
            "name": "owner-ratified-predecessor-upper-bound",
            "ledger_sha256": "b" * 64,
            "actual_spend_usd": 119.27238490,
            "uncertain_spend_usd": 0.0,
        }],
        stage_cap_ratification=ratification,
        project_root=str(tmp_path),
    )
    assert result["schema_version"] == "phase3_v3_cost_forecast_v2"
    assert result["stage_cap_usd"] == 1100.0
    assert result["stage_cap_binding"]["kind"] == "owner_ratification"
    assert result["within_stage_cap"] is True


def test_cumulative_spend_recovers_eight_decimal_ledger_values(
    protocol, tmp_path: Path,
):
    cells, exact, dynamic, prices = _full_projection_inputs(protocol, tmp_path)
    result = forecast.build_cost_forecast(
        protocol=protocol,
        planned_main_cells=cells,
        dynamic_residual_frame=dynamic,
        exact_context_index=exact,
        price_snapshot=prices,
        price_as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
        cumulative_spend_segments=[{
            "name": "binary-float-ledger-total",
            "ledger_sha256": "b" * 64,
            "actual_spend_usd": 119.27238489999999,
            "uncertain_spend_usd": 0.0,
        }],
        project_root=str(tmp_path),
    )
    assert result["cumulative_spend_usd"] == 119.2723849
    assert result["cumulative_spend_segments"] == [{
        "name": "binary-float-ledger-total",
        "ledger_sha256": "b" * 64,
        "actual_spend_usd": 119.2723849,
        "uncertain_spend_usd": 0.0,
    }]


def test_cost_forecast_blocks_missing_exact_main_context(protocol, tmp_path: Path):
    cells, exact, dynamic, prices = _full_projection_inputs(protocol, tmp_path)
    exact["prompt_tokens"].pop(next(iter(exact["prompt_tokens"])))
    with pytest.raises(forecast.ForecastInputError, match="exact main static context"):
        forecast.build_cost_forecast(
            protocol=protocol,
            planned_main_cells=cells,
            dynamic_residual_frame=dynamic,
            exact_context_index=exact,
            price_snapshot=prices,
            price_as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
            cumulative_spend_segments=[{
                "name": "all-prior-ledgers",
                "ledger_sha256": "b" * 64,
                "actual_spend_usd": 0.0,
                "uncertain_spend_usd": 0.0,
            }],
            project_root=str(tmp_path),
        )
