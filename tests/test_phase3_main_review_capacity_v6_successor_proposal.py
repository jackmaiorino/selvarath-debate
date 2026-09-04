"""Checks for the non-authorizing Phase 3 capacity v6 successor proposal."""
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
    / "phase3_main_review_capacity_v6_successor_proposal_2026-09-04.json"
)


def _proposal() -> dict:
    value = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v6_proposal_records_v5_pass_and_grants_no_authority() -> None:
    proposal = _proposal()
    assert proposal["schema_version"] == (
        "phase3_main_review_capacity_v6_successor_proposal_v1"
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
    predecessor = proposal["predecessor_v5_attempt"]
    assert predecessor["disposition"] == "completed_pass_historical_evidence_expired"
    assert predecessor["result"]["fully_valid_result_count"] == 180
    assert predecessor["accounting"]["cumulative_counted_reservations"] == 420
    assert predecessor["accounting"][
        "remaining_reviewer_ceiling_after_counted_reservations"
    ] == 58620


def test_v6_derivation_recomputes_exact_proposed_workload() -> None:
    if not capacity.ARCHIVE_DIR_DEFAULT.is_dir():
        pytest.skip("sealed external source archive is unavailable")
    proposal = _proposal()
    snapshot = capacity.collect_source_snapshot(
        archive_dir=capacity.ARCHIVE_DIR_DEFAULT,
        finalization_path=capacity.FINALIZATION_PATH_DEFAULT,
    )
    workload = capacity.derive_workload(
        snapshot,
        derivation_tag=capacity.DERIVATION_TAG_V6,
    )
    design = proposal["successor_v6_design"]
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
    for version in range(2, 6):
        assert workload.summary[
            f"v{version}_candidate_line_order_prompt_sha256_count"
        ] == excluded[f"v{version}_candidate_line_order_prompt_count"]
    assert workload.summary["combined_excluded_prompt_sha256_count"] == excluded[
        "combined_excluded_prompt_count"
    ]
    assert design["validity"]["valid_for_hours"] == 45 * 24


def test_v6_predecessor_bindings_remain_exact_when_archive_is_available() -> None:
    proposal = _proposal()
    predecessor = proposal["predecessor_v5_attempt"]
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
            pytest.skip("v5 external capacity archive is unavailable")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["raw_sha256"]
    signature = Path(predecessor["authorization"]["path"] + ".sig")
    assert hashlib.sha256(signature.read_bytes()).hexdigest() == predecessor[
        "authorization"
    ]["detached_signature_raw_sha256"]


def test_v6_exact_text_preserves_the_separate_authority_boundary() -> None:
    text = _proposal()["exact_non_execution_ratification_text"]
    assert "cumulative 420 counted reviewer reservations" in text
    assert "candidate-B then candidate-A then query" in text
    assert "1,080-hour capacity window" in text
    assert "offline v6 plan and workload materialization only" in text
    assert text.endswith(
        "It grants no reviewer dispatch, provider call, Together call, main run, or spend authority."
    )
