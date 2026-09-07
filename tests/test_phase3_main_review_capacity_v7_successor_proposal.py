"""Checks for the non-authorizing Phase 3 capacity v7 successor proposal."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import phase3_main_review_capacity_preflight as capacity


REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_v7_successor_proposal_2026-09-07.json"
)


def _proposal() -> dict:
    value = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v7_proposal_records_v6_pass_and_grants_no_authority() -> None:
    proposal = _proposal()
    assert proposal["schema_version"] == (
        "phase3_main_review_capacity_v7_successor_proposal_v1"
    )
    assert proposal["proposal_status"] == "owner_decision_required"
    for field in (
        "execution_authorized",
        "external_reviewer_dispatch_authorized",
        "provider_calls_authorized",
        "together_calls_authorized",
        "main_run_authorized",
        "spend_authorized",
    ):
        assert proposal[field] is False
    predecessor = proposal["predecessor_v6_attempt"]
    assert predecessor["disposition"] == "completed_pass_superseded_by_runner_change"
    assert predecessor["result"]["fully_valid_result_count"] == 180
    assert predecessor["accounting"]["counted_reservations_before_v6"] == 420
    assert predecessor["accounting"]["v6_counted_reservations"] == 180
    assert predecessor["accounting"]["cumulative_counted_reservations"] == 600
    assert predecessor["accounting"][
        "remaining_reviewer_ceiling_after_counted_reservations"
    ] == 58440


def test_v7_derivation_recomputes_exact_proposed_workload() -> None:
    if not capacity.ARCHIVE_DIR_DEFAULT.is_dir():
        pytest.skip("sealed external source archive is unavailable")
    proposal = _proposal()
    snapshot = capacity.collect_source_snapshot(
        archive_dir=capacity.ARCHIVE_DIR_DEFAULT,
        finalization_path=capacity.FINALIZATION_PATH_DEFAULT,
    )
    workload = capacity.derive_workload(
        snapshot,
        derivation_tag=capacity.DERIVATION_TAG_V7,
    )
    design = proposal["successor_v7_design"]
    assert workload.summary["transformation"] == design["transformation"]
    assert workload.summary["byte_new_eligible_count"] == design[
        "eligible_variant_count"
    ]
    assert workload.summary["unused_byte_new_reserve_count"] == design[
        "unused_variant_count"
    ]
    assert workload.summary["cohort_records_canonical_sha256s"] == design[
        "cohort_records_canonical_sha256s"
    ]
    assert workload.summary["cohort_variant_prompts_canonical_sha256s"] == design[
        "cohort_variant_prompts_canonical_sha256s"
    ]
    excluded = design["excluded_prompt_sets"]
    for version in range(2, 7):
        assert workload.summary[
            f"v{version}_candidate_line_order_prompt_sha256_count"
        ] == excluded[f"v{version}_candidate_line_order_prompt_count"]
        assert workload.summary[
            f"v{version}_candidate_line_order_prompt_set_canonical_sha256"
        ] == excluded[f"v{version}_candidate_line_order_prompt_set_canonical_sha256"]
    assert workload.summary["combined_excluded_prompt_sha256_count"] == excluded[
        "combined_excluded_prompt_count"
    ]
    assert design["validity"]["valid_for_hours"] == 45 * 24
    source_by_payload = {
        item.source_payload_sha256: item for item in snapshot.source_packets
    }
    cohorts = (workload.selected, workload.retry_selected)
    empty_query_counts = [
        sum(not source_by_payload[item.source_payload_sha256].query for item in cohort)
        for cohort in cohorts
    ]
    assert design["empty_query_count_per_cohort"] == empty_query_counts == [0, 0]
    assert [len(cohort) for cohort in cohorts] == [180, 180]
    assert len({item.variant_prompt_sha256 for cohort in cohorts for item in cohort}) == 360
    for item in workload.eligible:
        source = source_by_payload[item.source_payload_sha256]
        assert source.query
        assert item.prompt == (
            source.prompt_prefix
            + f"CANDIDATE B: {source.candidate_a}\n"
            + f"CANDIDATE A: {source.candidate_b}\n"
            + f"QUERY: {source.query}"
        )
        assert item.variant_payload_sha256 == capacity.payload_sha256(
            source.query, source.candidate_b, source.candidate_a
        )
    historical_prompts = set(snapshot.historical_prompt_hashes)
    for version in range(1, 7):
        predecessor_workload = capacity.derive_workload(
            snapshot,
            derivation_tag=getattr(capacity, f"DERIVATION_TAG_V{version}"),
        )
        historical_prompts.update(
            item.variant_prompt_sha256 for item in predecessor_workload.eligible
        )
    assert historical_prompts.isdisjoint(
        item.variant_prompt_sha256 for item in workload.eligible
    )


def test_v7_predecessor_bindings_remain_exact_when_archive_is_available() -> None:
    proposal = _proposal()
    predecessor = proposal["predecessor_v6_attempt"]
    for label in (
        "workload_manifest",
        "execution_manifest",
        "authorization",
        "dispatch_history",
        "attempt_reservation",
        "result",
    ):
        binding = predecessor[label]
        path = Path(binding["path"])
        if not path.is_file():
            pytest.skip("v6 external capacity archive is unavailable")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["raw_sha256"]
    signature = Path(predecessor["authorization"]["path"] + ".sig")
    assert hashlib.sha256(signature.read_bytes()).hexdigest() == predecessor[
        "authorization"
    ]["detached_signature_raw_sha256"]
    execution_manifest = json.loads(
        Path(predecessor["execution_manifest"]["path"]).read_text(encoding="utf-8")
    )
    assert predecessor["execution_manifest"]["bound_reviewer_batch_raw_sha256"] == (
        execution_manifest["code_bindings"]["reviewer_batch"]["raw_sha256"]
    )
    result = json.loads(Path(predecessor["result"]["path"]).read_text(encoding="utf-8"))
    assert result["execution_manifest_raw_sha256"] == predecessor[
        "execution_manifest"
    ]["raw_sha256"]
    assert result["authorization_raw_sha256"] == predecessor["authorization"]["raw_sha256"]
    assert result["attempt_status"] == "complete"
    assert result["interrupted"] is False
    rows = [row for wave in result["waves"] for row in wave["results"]]
    assert len(rows) == predecessor["result"]["result_row_count"] == 180
    assert sum(row["ok"] is True for row in rows) == 180
    assert all(not row["commands"] and not row["tool_uses"] for row in rows)


def test_v7_exact_text_preserves_the_separate_authority_boundary() -> None:
    text = _proposal()["exact_non_execution_ratification_text"]
    assert "cumulative 600 counted reviewer reservations" in text
    assert "candidate contents swapped" in text
    assert "1,080-hour capacity window" in text
    assert "offline v7 plan and workload materialization only" in text
    assert text.endswith(
        "It grants no reviewer dispatch, provider call, Together call, main run, or spend authority."
    )
