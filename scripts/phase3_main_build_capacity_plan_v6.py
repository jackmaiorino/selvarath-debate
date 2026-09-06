"""Materialize the v6 capacity preflight plan from the ratified proposal and sealed archive.

Offline only. Reads the sealed canary archive, derives the v6 workload deterministically,
cross-checks it against the ratified proposal, and writes the tracked plan JSON. Nothing
here dispatches a reviewer, calls a provider, or grants authority; every authority flag in
the written plan is false. After writing, pin the plan hash with
``scripts/phase3_main_pin_capacity_plan_v6.py --write`` and run the v6 contract tests.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.phase2_execution import canonical_sha256  # noqa: E402
from scripts import phase3_main_review_capacity_preflight as capacity  # noqa: E402

PINNED_CLI = capacity.REVIEWER_CLI_RESOLVED_PATH_V6
V6_ROOT = "E:/selvarath-archive/phase3-main-review-capacity-preflight-v6-2026-09-04"
PROPOSAL_REL = "rejudge/phase3_main_review_capacity_v6_successor_proposal_2026-09-04.json"
RATIFICATION_REL = (
    "rejudge/phase3_main_review_capacity_v6_successor_ratification_2026-09-06.json"
)
V5_PLAN_REL = "rejudge/phase3_main_review_capacity_preflight_plan_v5_2026-09-01.json"
OUTPUT_REL = "rejudge/phase3_main_review_capacity_preflight_plan_v6_2026-09-06.json"


def _load(rel: str) -> tuple[bytes, dict]:
    raw = (REPO_ROOT / rel).read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{rel} must be a JSON object")
    return raw, value


def main() -> int:
    output = REPO_ROOT / OUTPUT_REL
    if output.exists():
        raise SystemExit(f"refusing to overwrite the tracked v6 plan: {output}")
    proposal_raw, proposal = _load(PROPOSAL_REL)
    ratification_raw, ratification = _load(RATIFICATION_REL)
    _, v5_plan = _load(V5_PLAN_REL)
    if ratification["ratification_text"] != proposal["exact_non_execution_ratification_text"]:
        raise SystemExit("ratification text differs from the proposal's exact text")
    if ratification["proposal"]["raw_sha256"] != hashlib.sha256(proposal_raw).hexdigest():
        raise SystemExit("ratification binds a different proposal raw hash")
    if ratification["proposal"]["canonical_sha256"] != canonical_sha256(proposal):
        raise SystemExit("ratification binds a different proposal canonical hash")

    snapshot = capacity.collect_source_snapshot(
        archive_dir=capacity.ARCHIVE_DIR_DEFAULT,
        finalization_path=capacity.FINALIZATION_PATH_DEFAULT,
    )
    workload = capacity.derive_workload(
        snapshot, derivation_tag=capacity.DERIVATION_TAG_V6
    )
    design = proposal["successor_v6_design"]
    summary = dict(workload.summary)
    checks = {
        "transformation": (summary["transformation"], design["transformation"]),
        "eligible": (summary["byte_new_eligible_count"], design["eligible_variant_count"]),
        "unused": (summary["unused_byte_new_reserve_count"], design["unused_variant_count"]),
        "cohorts": (
            summary["cohort_records_canonical_sha256s"],
            design["cohort_records_canonical_sha256s"],
        ),
        "prompts": (
            summary["cohort_variant_prompts_canonical_sha256s"],
            design["cohort_variant_prompts_canonical_sha256s"],
        ),
        "collisions": (summary["historical_collision_count"], design["collision_count"]),
    }
    for label, (observed, expected) in checks.items():
        if observed != expected:
            raise SystemExit(f"derived workload {label} differs from the ratified proposal")

    cli_bytes = Path(PINNED_CLI).read_bytes()
    v5_reviewer = v5_plan["reviewer_configuration"]
    if (
        hashlib.sha256(cli_bytes).hexdigest() != v5_reviewer["reviewer_cli_wrapper_raw_sha256"]
        or len(cli_bytes) != v5_reviewer["reviewer_cli_wrapper_byte_count"]
    ):
        raise SystemExit("pinned reviewer CLI wrapper differs from the v5 wrapper bytes")
    reviewer = copy.deepcopy(v5_reviewer)
    reviewer["reviewer_cli_resolved_path"] = PINNED_CLI
    if reviewer["model_provider_profile"] != design["reviewer_transport"]["model_provider_profile"]:
        raise SystemExit("reviewer profile differs from the ratified proposal")

    predecessor = proposal["predecessor_v5_attempt"]
    accounting = predecessor["accounting"]
    plan = {
        "schema_version": capacity.SCHEMA_VERSION_V6,
        "plan_id": "phase3_main_review_capacity_preflight_v6_2026_09_06",
        "recorded_on_date": "2026-09-06",
        "status": "owner_confirmed_offline_plan_pending_separate_execution",
        "owner_decision": {
            "approver": "Jack Maiorino",
            "channel": (
                "owner standing delegation (2026-09-04 Codex-task grant and 2026-09-06 "
                "global standing authorization); exact proposal text adopted verbatim and "
                "recorded by the Claude Code orchestrator"
            ),
            "decision": (
                "ratify the exact non-execution v6 successor proposal and authorize offline "
                "plan and workload materialization only"
            ),
        },
        "execution_authorized": False,
        "external_reviewer_dispatch_authorized": False,
        "provider_calls_authorized": False,
        "together_calls_authorized": False,
        "main_run_authorized": False,
        "main_spend_authorized": False,
        "spend_authorized": False,
        "source_locations": {
            "finalization_record": "rejudge/phase3_v3_finalization_record_2026-08-29.json",
            "sealed_archive": "E:/selvarath-archive/phase3-v3r15-clean-2026-08-29",
            "decision_store": "phase3_v3_reviewer_decisions.jsonl",
            "reviewer_index": "reviewer_index.jsonl",
            "successor_proposal": PROPOSAL_REL,
            "successor_ratification": RATIFICATION_REL,
        },
        "source_bindings": {
            "finalization_record_raw_sha256": snapshot.finalization_record_sha256,
            "reviewer_decision_store_raw_sha256": snapshot.decision_store_sha256,
            "reviewer_index_raw_sha256": snapshot.reviewer_index_sha256,
            "reviewer_prompt_prefix_sha256": snapshot.prompt_prefix_sha256,
            "packet_directories_canonical_sha256": canonical_sha256(
                list(snapshot.packet_directories)
            ),
            "successor_proposal_raw_sha256": hashlib.sha256(proposal_raw).hexdigest(),
            "successor_proposal_canonical_sha256": canonical_sha256(proposal),
            "successor_ratification_raw_sha256": hashlib.sha256(ratification_raw).hexdigest(),
            "successor_ratification_canonical_sha256": canonical_sha256(ratification),
        },
        "predecessor_usage_accounting": {
            "proposal_bound_completed_v5_pass": True,
            "v5_disposition": predecessor["disposition"],
            "v5_result_raw_sha256": predecessor["result"]["raw_sha256"],
            "v5_completed_at_utc": predecessor["result"]["completed_at_utc"],
            "v5_evidence_expired_at_utc": predecessor["result"]["evidence_expired_at_utc"],
            "counted_reservations_before_v5": accounting["counted_reservations_before_v5"],
            "v5_counted_reservations": accounting["v5_counted_reservations"],
            "cumulative_counted_reviewer_reservations": accounting[
                "cumulative_counted_reservations"
            ],
            "cumulative_durable_invocation_bundle_count": (
                180 + predecessor["result"]["durable_invocation_receipt_count"]
            ),
            "v5_durable_invocation_bundle_count": predecessor["result"][
                "durable_invocation_receipt_count"
            ],
            "v5_fully_valid_result_count": predecessor["result"]["fully_valid_result_count"],
            "v5_failed_result_count": 0,
            "ratified_reviewer_ceiling": accounting["ratified_reviewer_ceiling"],
            "remaining_reviewer_ceiling_after_counted_reservations": accounting[
                "remaining_reviewer_ceiling_after_counted_reservations"
            ],
            "predecessor_cohorts_reused": False,
            "accounting_treatment": accounting["treatment"],
        },
        "workload": summary,
        "reviewer_configuration": reviewer,
        "capacity_thresholds": capacity._expected_thresholds(),  # noqa: SLF001
        "dispatch_history_contract": {
            **copy.deepcopy(v5_plan["dispatch_history_contract"]),
            "required_path": f"{V6_ROOT}/dispatch_history.jsonl",
            "anchor_directory": f"{V6_ROOT}/dispatch_history_anchors",
            "interruption_evidence_root": f"{V6_ROOT}/interruption_evidence",
        },
        "measurement_contract": copy.deepcopy(v5_plan["measurement_contract"]),
        "validity": {
            "valid_for_hours": design["validity"]["valid_for_hours"],
            "basis": design["validity"]["basis"],
            "invalidated_by": list(design["validity"]["invalidated_immediately_by"]),
        },
        "non_claims": [
            "This plan authorizes offline plan and workload materialization only.",
            (
                "This plan does not authorize reviewer dispatch, provider calls, Together "
                "calls, a main run, spend, or formal measurement."
            ),
            "The 420 predecessor reservations remain counted once as external reviewer usage.",
            "No sealed source, v1, v2, v3, v4, v5, or predecessor cohort prompt is reused.",
            "The 211 unused eligible variants do not authorize another attempt.",
            (
                "The pinned reviewer CLI copy at E:/selvarath-tools/codex-0.149.0 has a "
                "wrapper byte-identical to the v5 measurement; the wrapper hash alone is not "
                "an attestation of the underlying CLI package, which the execution manifest "
                "binds by its reported version."
            ),
        ],
    }
    if plan["validity"]["valid_for_hours"] != capacity.CAPACITY_VALIDITY_HOURS_V6:
        raise SystemExit("proposal validity window differs from the validator constant")
    output.write_text(
        json.dumps(plan, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "plan_path": output.as_posix(),
        "plan_canonical_sha256": canonical_sha256(plan),
        "plan_raw_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "byte_new_eligible_count": summary["byte_new_eligible_count"],
        "unused_byte_new_reserve_count": summary["unused_byte_new_reserve_count"],
        "execution_authorized": False,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
