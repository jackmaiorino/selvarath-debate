"""Checks for the non-authorizing Phase 3 capacity successor proposal."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_main_capacity_execution as execution
from scripts import phase3_main_review_capacity_preflight as capacity


REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_successor_proposal_2026-09-01.json"
)
RATIFICATION_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_successor_ratification_2026-09-01.json"
)
PLAN_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_preflight_plan_v2_2026-09-01.json"
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        assert key not in value, f"duplicate JSON key: {key}"
        value[key] = item
    return value


def _load(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    return raw, json.loads(raw, object_pairs_hook=_unique_object)


def _git_blob(commit: str, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def test_successor_proposal_binds_repair_and_grants_no_authority() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)

    assert proposal["schema_version"] == (
        "phase3_main_review_capacity_successor_proposal_v1"
    )
    assert proposal["proposal_only"] is True
    assert proposal["proposal_status"] == "owner_decision_required"
    assert proposal["execution_authorized"] is False
    assert proposal["external_reviewer_dispatch_authorized"] is False
    assert proposal["provider_calls_authorized"] is False
    assert proposal["main_run_authorized"] is False

    predecessor = proposal["predecessor_attempt"]
    assert predecessor["disposition"] == "terminal_completed_failure_retained"
    assert predecessor["failure_receipt"]["attempted_dispatches"] == 60
    assert predecessor["failure_receipt"]["dispatch_reservation_count"] == 60
    assert predecessor["failure_receipt"]["validated_invocation_receipts"] == 0
    assert predecessor["failure_receipt"]["wave_output_count"] == 0
    assert predecessor["failure_receipt"]["error_type"] == "TypeError"
    assert "not reused" in predecessor["accounting"]

    repair = proposal["repair"]
    executor_raw = _git_blob(
        repair["commit"], "rejudge/phase3_main_capacity_execution.py"
    )
    test_raw = _git_blob(
        repair["commit"], "tests/test_phase3_main_capacity_execution.py"
    )
    assert hashlib.sha256(executor_raw).hexdigest() == repair["executor_raw_sha256"]
    assert hashlib.sha256(test_raw).hexdigest() == repair["capacity_test_raw_sha256"]


def test_successor_ratification_and_plan_grant_offline_materialization_only() -> None:
    proposal_raw, proposal = _load(PROPOSAL_PATH)
    ratification_raw, ratification = _load(RATIFICATION_PATH)
    _plan_raw, plan = _load(PLAN_PATH)

    assert ratification["ratification_text"] == proposal[
        "exact_non_execution_ratification_text"
    ]
    assert ratification["proposal"] == {
        "path": PROPOSAL_PATH.relative_to(REPO_ROOT).as_posix(),
        "raw_sha256": hashlib.sha256(proposal_raw).hexdigest(),
        "canonical_sha256": capacity.canonical_sha256(proposal),
    }
    assert ratification["authority"] == {
        "offline_plan_materialization_authorized": True,
        "offline_workload_materialization_authorized": True,
        "external_reviewer_dispatch_authorized": False,
        "provider_calls_authorized": False,
        "main_run_authorized": False,
        "spend_authorized": False,
    }
    source = plan["source_bindings"]
    assert source["successor_ratification_raw_sha256"] == hashlib.sha256(
        ratification_raw
    ).hexdigest()
    assert source["successor_ratification_canonical_sha256"] == (
        capacity.canonical_sha256(ratification)
    )
    assert plan["execution_authorized"] is False
    assert plan["provider_calls_authorized"] is False
    assert plan["main_spend_authorized"] is False


def test_v2_plan_recomputes_exact_ratified_workload() -> None:
    _proposal_raw, proposal = _load(PROPOSAL_PATH)
    _plan_raw, plan = _load(PLAN_PATH)
    if not capacity.ARCHIVE_DIR_DEFAULT.is_dir():
        pytest.skip("sealed external source archive is unavailable")

    snapshot = capacity.collect_source_snapshot(
        archive_dir=capacity.ARCHIVE_DIR_DEFAULT,
        finalization_path=capacity.FINALIZATION_PATH_DEFAULT,
    )
    workload = capacity.derive_workload(
        snapshot,
        derivation_tag=capacity.DERIVATION_TAG_V2,
    )
    validation = capacity.validate_plan(
        plan,
        snapshot=snapshot,
        workload=workload,
    )
    context = execution.load_capacity_context(
        PLAN_PATH,
        project_root=REPO_ROOT,
    )

    assert validation["plan_canonical_sha256"] == (
        capacity.EXPECTED_PLAN_CANONICAL_SHA256_V2
    )
    assert context.workload == workload
    assert plan["workload"] == dict(workload.summary)
    design = proposal["successor_design"]
    assert workload.summary["cohort_records_canonical_sha256s"] == design[
        "cohort_records_canonical_sha256s"
    ]
    assert workload.summary["cohort_variant_prompts_canonical_sha256s"] == design[
        "cohort_variant_prompts_canonical_sha256s"
    ]
    assert workload.summary["unused_byte_new_reserve_count"] == design[
        "unused_variant_count"
    ]


def test_bound_terminal_failure_evidence_matches_when_available() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    predecessor = proposal["predecessor_attempt"]
    manifest_path = Path(predecessor["manifest"]["path"])
    if not manifest_path.is_file():
        pytest.skip("bound external capacity archive is unavailable")

    bindings = [
        predecessor["manifest"],
        predecessor["authorization"],
        predecessor["attempt_reservation"],
        predecessor["failure_receipt"],
    ]
    for binding in bindings:
        path = Path(binding["path"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["raw_sha256"]
    signature = manifest_path.parent / (
        Path(predecessor["authorization"]["path"]).name + ".sig"
    )
    assert hashlib.sha256(signature.read_bytes()).hexdigest() == (
        predecessor["authorization"]["detached_signature_raw_sha256"]
    )

    history_raw, history = _load_history(Path(predecessor["dispatch_history"]["path"]))
    assert hashlib.sha256(history_raw).hexdigest() == predecessor[
        "dispatch_history"
    ]["raw_sha256"]
    assert len(history) == predecessor["dispatch_history"]["event_count"] == 2
    assert history[0]["event_hash"] == predecessor["dispatch_history"][
        "dispatch_started_event_hash"
    ]
    assert history[1]["event_hash"] == predecessor["dispatch_history"][
        "attempt_completed_fail_event_hash"
    ]


def _load_history(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    raw = path.read_bytes()
    rows = [
        json.loads(line, object_pairs_hook=_unique_object)
        for line in raw.decode("utf-8").splitlines()
    ]
    return raw, rows


def test_candidate_line_order_derivation_recomputes_exact_hashes() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    archive = capacity.ARCHIVE_DIR_DEFAULT
    finalization = capacity.FINALIZATION_PATH_DEFAULT
    if not archive.is_dir() or not finalization.is_file():
        pytest.skip("sealed external source archive is unavailable")

    snapshot = capacity.collect_source_snapshot(
        archive_dir=archive,
        finalization_path=finalization,
    )
    predecessor_workload = capacity.derive_workload(snapshot)
    historical = sorted(snapshot.historical_prompt_hashes)
    predecessor_prompts = sorted(
        item.variant_prompt_sha256 for item in predecessor_workload.eligible
    )
    excluded = set(historical) | set(predecessor_prompts)
    variants: list[tuple[str, str, str, str, str]] = []
    collisions: list[str] = []
    for source in snapshot.source_packets:
        rendered = (
            source.prompt_prefix
            + f"QUERY: {source.query}\n"
            + f"CANDIDATE B: {source.candidate_b}\n"
            + f"CANDIDATE A: {source.candidate_a}"
        )
        prompt_sha = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        if prompt_sha in excluded:
            collisions.append(source.source_payload_sha256)
            continue
        payload_sha = capacity.payload_sha256(
            source.query,
            source.candidate_a,
            source.candidate_b,
        )
        rank_sha = hashlib.sha256(
            (
                "phase3-main-review-capacity-v2|"
                + source.source_payload_sha256
            ).encode("utf-8")
        ).hexdigest()
        variants.append((
            rank_sha,
            source.source_payload_sha256,
            source.source_prompt_sha256,
            payload_sha,
            prompt_sha,
        ))
    variants.sort()

    design = proposal["successor_design"]
    excluded_claim = design["excluded_prompt_sets"]
    assert capacity.canonical_sha256(historical) == excluded_claim[
        "sealed_source_prompt_set_canonical_sha256"
    ]
    assert capacity.canonical_sha256(predecessor_prompts) == excluded_claim[
        "v1_candidate_swap_prompt_set_canonical_sha256"
    ]
    assert capacity.canonical_sha256(sorted(excluded)) == excluded_claim[
        "combined_excluded_prompt_set_canonical_sha256"
    ]
    assert len(variants) == design["eligible_variant_count"] == 576
    assert len(collisions) == design["collision_count"] == 0
    assert len(variants) - 360 == design["unused_variant_count"] == 216
    assert capacity.canonical_sha256([item[4] for item in variants]) == design[
        "all_ranked_prompt_list_canonical_sha256"
    ]
    assert capacity.canonical_sha256([item[1] for item in variants]) == design[
        "all_ranked_source_payload_list_canonical_sha256"
    ]

    def records(items: list[tuple[str, str, str, str, str]]) -> list[dict[str, str]]:
        return [
            {
                "rank_sha256": item[0],
                "source_payload_sha256": item[1],
                "source_prompt_sha256": item[2],
                "variant_payload_sha256": item[3],
                "variant_prompt_sha256": item[4],
            }
            for item in items
        ]

    cohorts = [variants[:180], variants[180:360]]
    assert [capacity.canonical_sha256(records(cohort)) for cohort in cohorts] == (
        design["cohort_records_canonical_sha256s"]
    )
    assert [
        capacity.canonical_sha256([item[1] for item in cohort])
        for cohort in cohorts
    ] == design["cohort_source_payloads_canonical_sha256s"]
    assert [
        capacity.canonical_sha256([item[3] for item in cohort])
        for cohort in cohorts
    ] == design["cohort_variant_payloads_canonical_sha256s"]
    assert [
        capacity.canonical_sha256([item[4] for item in cohort])
        for cohort in cohorts
    ] == design["cohort_variant_prompts_canonical_sha256s"]
