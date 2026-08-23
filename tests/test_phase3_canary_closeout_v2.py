"""Focused synthetic tests for the phase-3 v2 close-out derivation."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import phase3_canary_closeout_v2 as closeout  # noqa: E402
from rejudge import phase3_plan  # noqa: E402


def _cell(key: str, kind: str) -> dict:
    return {"cell_key": key, "kind": kind}


def test_completion_distinguishes_deferral_anchor_carry_and_preseeded_transcripts():
    canary = [
        _cell("j1", phase3_plan.CANARY_JUDGMENT_KIND),
        _cell("j2", phase3_plan.CANARY_JUDGMENT_KIND),
        _cell("ct", phase3_plan.CANARY_TRANSCRIPT_KIND),
        _cell("anchor", phase3_plan.CAPABILITY_ANCHOR_KIND),
    ]
    main = [_cell("mt", phase3_plan.MAIN_TRANSCRIPT_KIND)]
    rows = [{"cell_key": key} for key in ("j1", "ct", "mt")]
    report = closeout.build_completion_report(
        canary_cells=canary,
        main_cells=main,
        result_rows=rows,
        deferred_keys={"j2"},
        carried_anchor_count=1,
    )
    assert report["fresh_store_rows"] == 3
    assert report["fresh_judgments_completed_active"] == 1
    assert report["fresh_judgments_deferred_by_amendment"] == 1
    assert report["carried_anchor_bindings_verified"] == 1
    assert report["combined_gate_inventory_accounted"] == 3
    assert report["provisional_completion_gate_pass"] is True


def test_completion_fails_if_deferred_cell_was_executed_or_anchor_was_copied():
    canary = [
        _cell("j1", phase3_plan.CANARY_JUDGMENT_KIND),
        _cell("j2", phase3_plan.CANARY_JUDGMENT_KIND),
        _cell("anchor", phase3_plan.CAPABILITY_ANCHOR_KIND),
    ]
    rows = [{"cell_key": key} for key in ("j1", "j2", "anchor")]
    report = closeout.build_completion_report(
        canary_cells=canary,
        main_cells=[],
        result_rows=rows,
        deferred_keys={"j2"},
        carried_anchor_count=1,
    )
    assert report["completed_deferred_count"] == 1
    assert report["anchor_rows_erroneously_copied_into_v2_count"] == 1
    assert report["provisional_completion_gate_pass"] is False


def _observation(correct: bool, *, parse_ok: bool = True) -> dict:
    return {
        "parse_ok": parse_ok,
        "selected_correct": correct if parse_ok else False,
        "selected_semantic": ("correct" if correct else "wrong") if parse_ok else None,
    }


def test_paired_diagnostic_detects_position_preference_and_semantic_inconsistency():
    pairs = [
        {"A_correct": _observation(True), "B_correct": _observation(False)},
        {"A_correct": _observation(True), "B_correct": _observation(False)},
    ]
    report = closeout.compute_paired_position_diagnostic(pairs)
    assert report["signed_position_effect_pp"] == pytest.approx(-100.0)
    assert report["semantic_answer_consistency_fraction"] == pytest.approx(0.0)
    assert report["pair_mean_error_sample_variance"] == pytest.approx(0.0)
    assert report["variance_ratio_vs_independent_rows"] == pytest.approx(0.0)


def test_paired_diagnostic_counts_invalid_as_wrong_but_excludes_it_from_consistency():
    pairs = [
        {"A_correct": _observation(False, parse_ok=False),
         "B_correct": _observation(True)},
        {"A_correct": _observation(True), "B_correct": _observation(True)},
    ]
    report = closeout.compute_paired_position_diagnostic(pairs)
    assert report["invalid_row_count"] == 1
    assert report["invalid_pair_count"] == 1
    assert report["valid_pair_count"] == 1
    assert report["semantic_answer_consistency_fraction"] == pytest.approx(1.0)
    assert report["signed_position_effect_pp"] == pytest.approx(50.0)


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=timezone.utc)


def test_pace_window_restarts_after_latest_stop_and_deduplicates_packet_echoes():
    packets = [
        {"committed_at": _utc(1), "payloads": ["old"]},
        {"committed_at": _utc(3), "payloads": ["a", "b"]},
        {"committed_at": _utc(4), "payloads": ["b", "c"]},
    ]
    report = closeout.compute_pace_window(
        decision_payloads=["old", "a", "b", "c"],
        packet_commits=packets,
        stop_times=[_utc(2)],
        usage_event_times=[_utc(2, 50), _utc(3, 10), _utc(3, 30), _utc(3, 50)],
    )
    window = report["final_window"]
    assert report["attributed_unique_rulings"] == 4
    assert report["latest_interruption"]["type"] == "STOP"
    assert window["unique_rulings"] == 3
    assert window["opens_at_utc"] == "2026-08-23T03:00:00Z"
    assert window["closes_at_utc"] == "2026-08-23T04:00:00Z"
    assert window["point_rate_rulings_per_24_elapsed_hours"] == pytest.approx(72.0)
    assert report["configuration_selection_L90"] is None


def test_pace_window_restarts_after_ledger_gap_even_without_stop():
    packets = [
        {"committed_at": _utc(1), "payloads": ["old"]},
        {"committed_at": _utc(2), "payloads": ["new"]},
        {"committed_at": _utc(3), "payloads": ["last"]},
    ]
    report = closeout.compute_pace_window(
        decision_payloads=["old", "new", "last"],
        packet_commits=packets,
        stop_times=[],
        usage_event_times=[_utc(0), _utc(2), _utc(2, 20), _utc(2, 40)],
        gap=timedelta(minutes=30),
    )
    assert report["latest_interruption"]["type"] == "ledger_gap"
    assert report["final_window"]["opens_at_utc"] == "2026-08-23T02:00:00Z"
    assert report["final_window"]["unique_rulings"] == 2


def test_pace_reports_unattributed_decisions_instead_of_silently_counting_them():
    report = closeout.compute_pace_window(
        decision_payloads=["a", "missing"],
        packet_commits=[{"committed_at": _utc(1), "payloads": ["a"]}],
        stop_times=[],
        usage_event_times=[_utc(0), _utc(0, 10)],
    )
    assert report["unattributed_unique_ruling_count"] == 1
    assert report["unattributed_payload_sha256"] == ["missing"]


def test_stop_parser_ignores_rounds_that_explicitly_say_no_stop():
    text = "\n".join([
        "[2026-08-23T09:41:16Z] round complete (supervisor exit 0, no STOP)",
        "[2026-08-22T21:34:55Z] round 1: a STOP was detected (supervisor exit 5):",
        "[2026-08-22T21:34:55Z]   supervisor: STOP call deterministic",
    ])
    assert closeout.parse_stop_times(text) == [
        datetime(2026, 8, 22, 21, 34, 55, tzinfo=timezone.utc)]
