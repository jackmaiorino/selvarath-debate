"""Checks for the v6 capacity ratification record and frozen v6 preflight plan."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_main_capacity_execution as execution
from scripts import phase3_main_review_capacity_preflight as capacity


REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_v6_successor_proposal_2026-09-04.json"
)
RATIFICATION_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_v6_successor_ratification_2026-09-06.json"
)
PLAN_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_preflight_plan_v6_2026-09-06.json"
)
V5_PLAN_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_preflight_plan_v5_2026-09-01.json"
)


def _load(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    assert isinstance(value, dict)
    return raw, value


# The v6 plan is materialized by an owner-side offline build (see
# reports/2026-09-06-phase3-main-launch-runbook.md). Until it is tracked, the plan-bound
# checks skip; the ratification and legacy checks always run.
requires_v6_plan = pytest.mark.skipif(
    not PLAN_PATH.is_file(),
    reason="v6 capacity plan is not materialized yet",
)


def test_v6_ratification_binds_exact_proposal_and_grants_offline_only() -> None:
    proposal_raw, proposal = _load(PROPOSAL_PATH)
    _ratification_raw, ratification = _load(RATIFICATION_PATH)

    assert ratification["schema_version"] == (
        "phase3_main_review_capacity_v6_successor_ratification_v1"
    )
    assert ratification["approved_by"] == "Jack Maiorino"
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
        "together_calls_authorized": False,
        "main_run_authorized": False,
        "spend_authorized": False,
    }
    provenance = ratification["delegation_provenance"]
    assert "full spend and execute auth for the entire phase 3" in provenance[
        "owner_grant_2026_09_04"
    ]["verbatim"]
    assert "full future decision making" in provenance[
        "owner_standing_authorization_2026_09_06"
    ]["verbatim"]
    assert "recorded by the Claude Code orchestrator" in ratification["channel"]


@requires_v6_plan
def test_v6_plan_pins_dedicated_cli_profile_and_1080h_validity() -> None:
    _proposal_raw, proposal = _load(PROPOSAL_PATH)
    ratification_raw, ratification = _load(RATIFICATION_PATH)
    _plan_raw, plan = _load(PLAN_PATH)
    design = proposal["successor_v6_design"]

    assert plan["schema_version"] == capacity.SCHEMA_VERSION_V6
    assert plan["status"] == "owner_confirmed_offline_plan_pending_separate_execution"
    for field in (
        "execution_authorized",
        "external_reviewer_dispatch_authorized",
        "provider_calls_authorized",
        "together_calls_authorized",
        "main_run_authorized",
        "main_spend_authorized",
        "spend_authorized",
    ):
        assert plan[field] is False
    reviewer = plan["reviewer_configuration"]
    assert reviewer["reviewer_cli_resolved_path"] == capacity.REVIEWER_CLI_RESOLVED_PATH_V6
    assert reviewer["reviewer_cli_resolved_path"] != (
        capacity.REVIEWER_CLI_RESOLVED_PATH_SHARED_NPM
    )
    assert reviewer["model_provider_profile"] == design["reviewer_transport"][
        "model_provider_profile"
    ]
    assert reviewer["model_provider_profile"]["http_headers"] == {"version": "0.149.0"}
    assert plan["validity"]["valid_for_hours"] == capacity.CAPACITY_VALIDITY_HOURS_V6 == 1080
    assert plan["validity"]["valid_for_hours"] == design["validity"]["valid_for_hours"]
    assert plan["validity"]["invalidated_by"] == design["validity"][
        "invalidated_immediately_by"
    ]
    accounting = plan["predecessor_usage_accounting"]
    assert accounting["cumulative_counted_reviewer_reservations"] == 420
    assert accounting["remaining_reviewer_ceiling_after_counted_reservations"] == 58620
    assert accounting["v5_fully_valid_result_count"] == 180
    assert accounting["predecessor_cohorts_reused"] is False
    workload = plan["workload"]
    assert workload["derivation_tag"] == capacity.DERIVATION_TAG_V6
    assert workload["transformation"] == design["transformation"]
    assert workload["byte_new_eligible_count"] == design["eligible_variant_count"]
    assert workload["unused_byte_new_reserve_count"] == design["unused_variant_count"]
    assert workload["cohort_records_canonical_sha256s"] == design[
        "cohort_records_canonical_sha256s"
    ]
    source = plan["source_bindings"]
    assert source["successor_ratification_raw_sha256"] == hashlib.sha256(
        ratification_raw
    ).hexdigest()
    assert source["successor_ratification_canonical_sha256"] == (
        capacity.canonical_sha256(ratification)
    )
    assert plan["capacity_thresholds"] == capacity._expected_thresholds()  # noqa: SLF001
    history = plan["dispatch_history_contract"]
    assert history["required_path"].startswith(design["proposed_external_root"])
    assert history["maximum_attempts"] == 2
    capacity._validate_frozen_plan(plan)  # noqa: SLF001


@requires_v6_plan
def test_v6_pinned_cli_wrapper_matches_the_frozen_bytes() -> None:
    _plan_raw, plan = _load(PLAN_PATH)
    reviewer = plan["reviewer_configuration"]
    cli = Path(reviewer["reviewer_cli_resolved_path"])
    if not cli.is_file():
        pytest.skip("pinned reviewer CLI wrapper is unavailable on this host")
    wrapper = cli.read_bytes()
    assert len(wrapper) == reviewer["reviewer_cli_wrapper_byte_count"] == 341
    assert hashlib.sha256(wrapper).hexdigest() == reviewer[
        "reviewer_cli_wrapper_raw_sha256"
    ]


@requires_v6_plan
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("validity", {"valid_for_hours": 24}),
        ("reviewer_configuration", {}),
        ("execution_authorized", True),
        ("external_reviewer_dispatch_authorized", True),
    ],
)
def test_v6_canonical_plan_hash_freezes_operative_fields(field: str, value: Any) -> None:
    _plan_raw, plan = _load(PLAN_PATH)
    plan[field] = value

    with pytest.raises(capacity.CapacityPreflightError, match="canonical hash mismatch"):
        capacity._validate_frozen_plan(plan)  # noqa: SLF001


def test_legacy_v5_plan_keeps_shared_npm_path_and_24h_validity() -> None:
    _plan_raw, plan = _load(V5_PLAN_PATH)

    assert plan["reviewer_configuration"]["reviewer_cli_resolved_path"] == (
        capacity.REVIEWER_CLI_RESOLVED_PATH_SHARED_NPM
    )
    assert plan["validity"]["valid_for_hours"] == 24
    capacity._validate_frozen_plan(plan)  # noqa: SLF001


def test_v6_placeholder_hash_fails_closed_until_the_plan_is_pinned() -> None:
    _plan_raw, v5_plan = _load(V5_PLAN_PATH)
    pretend_v6 = dict(v5_plan)
    pretend_v6["schema_version"] = capacity.SCHEMA_VERSION_V6

    with pytest.raises(capacity.CapacityPreflightError, match="canonical hash mismatch"):
        capacity._validate_frozen_plan(pretend_v6)  # noqa: SLF001


@requires_v6_plan
def test_v6_plan_recomputes_exact_ratified_workload() -> None:
    _plan_raw, plan = _load(PLAN_PATH)
    if not capacity.ARCHIVE_DIR_DEFAULT.is_dir():
        pytest.skip("sealed external source archive is unavailable")
    if not Path(capacity.REVIEWER_CLI_RESOLVED_PATH_V6).is_file():
        pytest.skip("pinned reviewer CLI wrapper is unavailable on this host")

    snapshot = capacity.collect_source_snapshot(
        archive_dir=capacity.ARCHIVE_DIR_DEFAULT,
        finalization_path=capacity.FINALIZATION_PATH_DEFAULT,
    )
    workload = capacity.derive_workload(
        snapshot,
        derivation_tag=capacity.DERIVATION_TAG_V6,
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
        capacity.EXPECTED_PLAN_CANONICAL_SHA256_V6
    )
    assert context.workload == workload
    assert plan["workload"] == dict(workload.summary)
