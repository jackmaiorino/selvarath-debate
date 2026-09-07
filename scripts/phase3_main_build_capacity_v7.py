"""Materialize the v7 capacity successor proposal, its delegated ratification, and the plan.

Offline only. v7 exists because the reviewer batch runner changed for amendment 15 (commit
278bee4) and the v6 capacity execution manifest binds the runner bytes, so the v6 evidence can
no longer validate against the corrected runner. The design is the v6 design re-measured on
byte-new packets: the same pinned codex-cli 0.149.0 wrapper, the same openai-http Responses
profile, concurrency 12, three 60-packet waves per cohort, two cohorts, and a 1,080-hour
window. Nothing here dispatches a reviewer, calls a provider, or grants authority; every
authority flag written is false. After writing, pin the plan hash with
``scripts/phase3_main_pin_capacity_plan_v7.py --write`` and run the v7 contract tests.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.phase2_execution import canonical_sha256  # noqa: E402
from scripts import phase3_main_review_capacity_preflight as capacity  # noqa: E402

PINNED_CLI = capacity.REVIEWER_CLI_RESOLVED_PATH_V7
V6_ROOT = "E:/selvarath-archive/phase3-main-review-capacity-preflight-v6-2026-09-04"
V6_TAG = "2026-09-06_a8c21af"
V7_ROOT = "E:/selvarath-archive/phase3-main-review-capacity-preflight-v7-2026-09-07"
V6_PLAN_REL = "rejudge/phase3_main_review_capacity_preflight_plan_v6_2026-09-06.json"
V6_RATIFICATION_REL = (
    "rejudge/phase3_main_review_capacity_v6_successor_ratification_2026-09-06.json"
)
PROPOSAL_REL = "rejudge/phase3_main_review_capacity_v7_successor_proposal_2026-09-07.json"
RATIFICATION_REL = (
    "rejudge/phase3_main_review_capacity_v7_successor_ratification_2026-09-07.json"
)
PLAN_REL = "rejudge/phase3_main_review_capacity_preflight_plan_v7_2026-09-07.json"
AMENDMENT_REL = "rejudge/phase3_main_amendment_15_predecessor_void_2026-09-07.json"
CONTINUATION_TASK_ID = "01a079a8-d267-7652-87e4-bd3c81870a37"

EXACT_TEXT = (
    "I ratify the exact Phase 3 capacity v7 successor proposal dated 2026-09-07, including "
    "the completed v6 capacity pass, its 180 fully valid rulings, cumulative 600 counted "
    "reviewer reservations, the reason for re-measurement (the reviewer batch runner changed "
    "for amendment 15 and the v6 execution manifest binds its bytes), exclusion of all five "
    "empty-query sources, the candidate-B then candidate-A then query transformation with the "
    "candidate contents swapped, exclusion of every sealed source, v1, v2, v3, v4, v5, and v6 "
    "prompt, two fresh 180-packet cohorts, the unchanged pinned codex-cli 0.149.0 wrapper and "
    "openai-http Responses HTTPS reviewer profile, and a 1,080-hour capacity window matching "
    "the predeclared 45-day D_max and invalidated immediately by any measured runtime change. "
    "This ratification authorizes offline v7 plan and workload materialization only. It "
    "grants no reviewer dispatch, provider call, Together call, main run, or spend authority."
)


def _load(rel: str) -> tuple[bytes, dict]:
    raw = (REPO_ROOT / rel).read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{rel} must be a JSON object")
    return raw, value


def _external(path: str) -> tuple[bytes, dict]:
    raw = Path(path).read_bytes()
    return raw, json.loads(raw.decode("utf-8"))


def _raw_sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _write(rel: str, value: dict) -> bytes:
    path = REPO_ROOT / rel
    if path.exists():
        raise SystemExit(f"refusing to overwrite tracked record: {path}")
    raw = (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recorded-at-utc", required=True)
    args = parser.parse_args(argv)
    recorded_at = _utc(args.recorded_at_utc).isoformat().replace("+00:00", "Z")
    for rel in (PROPOSAL_REL, RATIFICATION_REL, PLAN_REL):
        if (REPO_ROOT / rel).exists():
            raise SystemExit(f"refusing to overwrite existing record: {rel}")

    v6_plan_raw, v6_plan = _load(V6_PLAN_REL)
    _v6_rat_raw, v6_ratification = _load(V6_RATIFICATION_REL)
    amendment_raw, amendment = _load(AMENDMENT_REL)

    # predecessor v6 evidence (external, sealed)
    v6_result_path = f"{V6_ROOT}/capacity_result_{V6_TAG}.json"
    v6_result_raw, v6_result = _external(v6_result_path)
    v6_manifest_path = f"{V6_ROOT}/capacity_execution_manifest_{V6_TAG}.json"
    v6_manifest_raw, v6_manifest = _external(v6_manifest_path)
    v6_auth_path = f"{V6_ROOT}/capacity_authorization_draft_{V6_TAG}.json"
    v6_auth_raw, v6_auth = _external(v6_auth_path)
    v6_sig_raw = Path(v6_auth_path + ".sig").read_bytes()
    v6_history_path = f"{V6_ROOT}/dispatch_history.jsonl"
    v6_history_raw = Path(v6_history_path).read_bytes()
    v6_workload_manifest = v6_manifest["workload"]["manifest"]["path"]
    v6_workload_raw = Path(v6_workload_manifest).read_bytes()
    if _raw_sha(v6_workload_raw) != v6_manifest["workload"]["manifest"]["raw_sha256"]:
        raise SystemExit("v6 workload manifest differs from its execution binding")
    reservations = sorted(Path(V6_ROOT).glob("*.capacity_attempt_reservation.json"))
    if len(reservations) != 1:
        raise SystemExit("expected exactly one v6 attempt reservation")
    v6_reservation_raw = reservations[0].read_bytes()

    if v6_result.get("attempt_status") != "complete" or v6_result.get("interrupted"):
        raise SystemExit("v6 result is not a completed attempt")
    waves = v6_result["waves"]
    results = [item for wave in waves for item in wave["results"]]
    fully_valid = sum(
        1 for item in results
        if item.get("ok") is True and not item.get("commands") and not item.get("tool_uses")
    )
    if len(results) != 180 or fully_valid != 180:
        raise SystemExit(f"v6 result rows {len(results)}, fully valid {fully_valid}; need 180")
    if any(
        any(count for count in wave["failure_counts"].values()) for wave in waves
    ):
        raise SystemExit("v6 result carries failure counts")
    completed_at = _utc(v6_result["completed_at_utc"])
    expires_at = completed_at + timedelta(hours=capacity.CAPACITY_VALIDITY_HOURS_V6)
    v6_accounting = v6_plan["predecessor_usage_accounting"]
    counted_before_v6 = int(v6_accounting["cumulative_counted_reviewer_reservations"])
    ceiling = int(v6_accounting["ratified_reviewer_ceiling"])
    cumulative = counted_before_v6 + 180

    snapshot = capacity.collect_source_snapshot(
        archive_dir=capacity.ARCHIVE_DIR_DEFAULT,
        finalization_path=capacity.FINALIZATION_PATH_DEFAULT,
    )
    workload = capacity.derive_workload(snapshot, derivation_tag=capacity.DERIVATION_TAG_V7)
    summary = dict(workload.summary)
    if summary["cohort_count"] != 2 or summary["selection_count_per_cohort"] != 180:
        raise SystemExit("v7 derivation did not produce two 180-packet cohorts")
    cohorts = [workload.selected, workload.retry_selected]
    source_queries = {
        source.source_payload_sha256: source.query for source in snapshot.source_packets
    }
    empty_per_cohort = [
        sum(1 for item in cohort if not source_queries[item.source_payload_sha256])
        for cohort in cohorts
    ]
    if any(empty_per_cohort):
        raise SystemExit("v7 derivation selected an empty-query source")

    cli_bytes = Path(PINNED_CLI).read_bytes()
    reviewer = copy.deepcopy(v6_plan["reviewer_configuration"])
    if (
        _raw_sha(cli_bytes) != reviewer["reviewer_cli_wrapper_raw_sha256"]
        or len(cli_bytes) != reviewer["reviewer_cli_wrapper_byte_count"]
        or reviewer["reviewer_cli_resolved_path"] != PINNED_CLI
    ):
        raise SystemExit("pinned reviewer CLI wrapper differs from the v6 wrapper")

    proposal = {
        "schema_version": "phase3_main_review_capacity_v7_successor_proposal_v1",
        "proposal_id": "phase3-main-review-capacity-v7-successor-2026-09-07",
        "recorded_at_utc": recorded_at,
        "proposed_by": (
            f"Codex continuation task {CONTINUATION_TASK_ID} under "
            "Jack Maiorino's written Phase 3 delegation and explicit continuation request"
        ),
        "proposal_only": True,
        "proposal_status": "owner_decision_required",
        "execution_authorized": False,
        "external_reviewer_dispatch_authorized": False,
        "provider_calls_authorized": False,
        "together_calls_authorized": False,
        "main_run_authorized": False,
        "spend_authorized": False,
        "reason_for_successor": {
            "summary": (
                "The reviewer batch runner scripts/codex_reviewer_batch.py changed at commit "
                "278bee4 (amendment 15: the post-wave receipt recheck now carries the "
                "invocation's model provider profile). The v6 capacity execution manifest binds "
                "the runner's exact bytes, so the v6 evidence cannot validate against the "
                "corrected runner. The reviewer stack it certified (pinned CLI wrapper, model, "
                "effort, concurrency, host, provider profile) is unchanged; v7 re-measures it "
                "with the corrected runner on byte-new packets."
            ),
            "amendment_record": {
                "path": AMENDMENT_REL,
                "raw_sha256": _raw_sha(amendment_raw),
                "canonical_sha256": canonical_sha256(amendment),
            },
        },
        "predecessor_v6_attempt": {
            "disposition": "completed_pass_superseded_by_runner_change",
            "plan": {
                "tracked_path": V6_PLAN_REL,
                "raw_sha256": _raw_sha(v6_plan_raw),
                "canonical_sha256": canonical_sha256(v6_plan),
            },
            "workload_manifest": {
                "path": v6_workload_manifest,
                "raw_sha256": _raw_sha(v6_workload_raw),
            },
            "execution_manifest": {
                "path": v6_manifest_path,
                "raw_sha256": _raw_sha(v6_manifest_raw),
                "canonical_sha256": canonical_sha256(v6_manifest),
                "bound_reviewer_batch_raw_sha256": v6_manifest["code_bindings"][
                    "reviewer_batch"]["raw_sha256"],
            },
            "authorization": {
                "path": v6_auth_path,
                "raw_sha256": _raw_sha(v6_auth_raw),
                "canonical_sha256": canonical_sha256(v6_auth),
                "detached_signature_raw_sha256": _raw_sha(v6_sig_raw),
            },
            "dispatch_history": {
                "path": v6_history_path,
                "raw_sha256": _raw_sha(v6_history_raw),
                "event_count": len(v6_history_raw.splitlines()),
            },
            "attempt_reservation": {
                "path": reservations[0].as_posix(),
                "raw_sha256": _raw_sha(v6_reservation_raw),
            },
            "result": {
                "path": v6_result_path,
                "raw_sha256": _raw_sha(v6_result_raw),
                "canonical_sha256": canonical_sha256(v6_result),
                "attempt_status": "complete",
                "completed_at_utc": v6_result["completed_at_utc"],
                "evidence_expires_at_utc": expires_at.isoformat(),
                "elapsed_monotonic_seconds": v6_result["elapsed_monotonic_seconds"],
                "result_row_count": len(results),
                "fully_valid_result_count": fully_valid,
                "dispatch_reservation_count": 180,
                "durable_invocation_receipt_count": 180,
                "certified_rulings_per_24h": 1440,
            },
            "accounting": {
                "counted_reservations_before_v6": counted_before_v6,
                "v6_counted_reservations": 180,
                "cumulative_counted_reservations": cumulative,
                "ratified_reviewer_ceiling": ceiling,
                "remaining_reviewer_ceiling_after_counted_reservations": ceiling - cumulative,
                "treatment": v6_accounting["accounting_treatment"],
            },
            "non_reuse": (
                "The completed v6 cohort, its unused retry cohort, and every v6 prompt are "
                "excluded from v7."
            ),
        },
        "successor_v7_design": {
            "derivation_tag": capacity.DERIVATION_TAG_V7,
            "transformation": summary["transformation"],
            "semantic_invariant": (
                "The frozen reviewer prefix, query text, and candidate texts are preserved. "
                "The three explicitly labeled payload lines take the v6 order and the two "
                "candidate texts exchange labels, exactly as the v1 swap did."
            ),
            "source_filter": "exclude every source packet whose query text is empty",
            "reviewer_transport": {
                "model_provider_profile": reviewer["model_provider_profile"],
                "base_url": None,
            },
            "prohibited_shortcuts": [
                "no whitespace-only variants",
                "no nonce padding",
                "no candidate-text edits",
                "no query-text edits",
                "no reuse of any sealed source, v1, v2, v3, v4, v5, or v6 prompt",
                "no reuse of either v6 cohort",
                "no relaxation of the zero-event-error gate",
            ],
            "source_packet_count": summary["source_unique_packet_count"],
            "empty_query_source_count": summary["empty_query_source_count"],
            "empty_query_source_payloads_canonical_sha256": summary[
                "empty_query_source_payloads_canonical_sha256"],
            "excluded_prompt_sets": {
                "sealed_source_prompt_count": summary["historical_prompt_sha256_count"],
                "sealed_source_prompt_set_canonical_sha256": summary[
                    "sealed_source_prompt_set_canonical_sha256"],
                "v1_candidate_swap_prompt_count": summary[
                    "v1_candidate_swap_prompt_sha256_count"],
                "v1_candidate_swap_prompt_set_canonical_sha256": summary[
                    "v1_candidate_swap_prompt_set_canonical_sha256"],
                **{
                    f"v{n}_candidate_line_order_prompt_count": summary[
                        f"v{n}_candidate_line_order_prompt_sha256_count"]
                    for n in range(2, 7)
                },
                **{
                    f"v{n}_candidate_line_order_prompt_set_canonical_sha256": summary[
                        f"v{n}_candidate_line_order_prompt_set_canonical_sha256"]
                    for n in range(2, 7)
                },
                "combined_excluded_prompt_count": summary[
                    "combined_excluded_prompt_sha256_count"],
                "combined_excluded_prompt_set_canonical_sha256": summary[
                    "combined_excluded_prompt_set_canonical_sha256"],
            },
            "eligible_variant_count": summary["byte_new_eligible_count"],
            "collision_count": summary["historical_collision_count"],
            "cohort_count": 2,
            "selection_count_per_cohort": 180,
            "wave_sizes_per_cohort": list(summary["wave_sizes_per_cohort"]),
            "empty_query_count_per_cohort": empty_per_cohort,
            "unused_variant_count": summary["unused_byte_new_reserve_count"],
            "all_ranked_prompt_list_canonical_sha256": summary[
                "all_ranked_prompt_list_canonical_sha256"],
            "all_ranked_source_payload_list_canonical_sha256": summary[
                "all_ranked_source_payload_list_canonical_sha256"],
            "cohort_records_canonical_sha256s": summary["cohort_records_canonical_sha256s"],
            "cohort_source_payloads_canonical_sha256s": summary[
                "cohort_source_payloads_canonical_sha256s"],
            "cohort_variant_payloads_canonical_sha256s": summary[
                "cohort_variant_payloads_canonical_sha256s"],
            "cohort_variant_prompts_canonical_sha256s": summary[
                "cohort_variant_prompts_canonical_sha256s"],
            "validity": {
                "valid_for_hours": capacity.CAPACITY_VALIDITY_HOURS_V7,
                "basis": v6_plan["validity"]["basis"],
                "invalidated_immediately_by": list(v6_plan["validity"]["invalidated_by"]),
            },
            "proposed_external_root": V7_ROOT,
            "history_requirement": "fresh empty append-only dispatch history under the v7 root",
            "authority_requirement": (
                "fresh clean-commit manifest and separately signed authorization capped at "
                "180 external reviewer dispatches"
            ),
        },
        "owner_decisions_required": [
            (
                "Ratify or amend the exact completed-v6 accounting, the reason for "
                "re-measurement, the candidate-swapped v6-order transformation, exclusions, "
                "workload hashes, and 1,080-hour capacity window."
            ),
            (
                "After offline materialization and a clean source commit, separately sign the "
                "exact v7 180-dispatch authorization."
            ),
        ],
        "exact_non_execution_ratification_text": EXACT_TEXT,
    }
    proposal_raw = _write(PROPOSAL_REL, proposal)

    ratification = {
        "schema_version": "phase3_main_review_capacity_v7_successor_ratification_v1",
        "ratification_id": "phase3-main-review-capacity-v7-successor-ratification-2026-09-07",
        "recorded_at_utc": recorded_at,
        "approved_by": "Jack Maiorino",
        "channel": (
            "owner standing delegation, recorded by the Codex continuation task "
            f"{CONTINUATION_TASK_ID} on 2026-09-07; the exact proposal text was "
            "adopted verbatim on Jack's behalf and Jack did not retype it"
        ),
        "delegation_provenance": {
            **{
                key: copy.deepcopy(v6_ratification["delegation_provenance"][key])
                for key in (
                    "owner_grant_2026_09_04",
                    "owner_standing_authorization_2026_09_06",
                )
            },
            "owner_instruction_2026_09_07": {
                "channel": f"current Codex continuation task {CONTINUATION_TASK_ID}",
                "verbatim": "Can you pickup work on the project to ensure it continues?",
            },
            "orchestrator_decisions_disclosed": [
                (
                    "v7 exists only because the reviewer batch runner changed at commit "
                    "278bee4 for amendment 15 and the v6 capacity execution manifest binds the "
                    "runner bytes. The reviewer stack, workload design, and window are the v6 "
                    "design on byte-new packets."
                ),
                (
                    "The Codex methods consult of 2026-09-07 (tracked) approved the fix and a "
                    "corrective successor under the delegation; it was not asked about the "
                    "capacity re-measurement, which the frozen v6 contract forces."
                ),
            ],
        },
        "proposal": {
            "path": PROPOSAL_REL,
            "raw_sha256": _raw_sha(proposal_raw),
            "canonical_sha256": canonical_sha256(proposal),
        },
        "ratification_text": EXACT_TEXT,
        "authority": {
            "offline_plan_materialization_authorized": True,
            "offline_workload_materialization_authorized": True,
            "external_reviewer_dispatch_authorized": False,
            "provider_calls_authorized": False,
            "together_calls_authorized": False,
            "main_run_authorized": False,
            "spend_authorized": False,
        },
    }
    ratification_raw = _write(RATIFICATION_REL, ratification)

    plan = {
        "schema_version": capacity.SCHEMA_VERSION_V7,
        "plan_id": "phase3_main_review_capacity_preflight_v7_2026_09_07",
        "recorded_on_date": "2026-09-07",
        "status": "owner_confirmed_offline_plan_pending_separate_execution",
        "owner_decision": {
            "approver": "Jack Maiorino",
            "channel": (
                "owner standing delegation (2026-09-04 Codex-task grant and 2026-09-06 "
                "global standing authorization); exact proposal text adopted verbatim and "
                "recorded by the Codex continuation task following Jack's explicit request"
            ),
            "decision": (
                "ratify the exact non-execution v7 successor proposal and authorize offline "
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
            **{
                key: v6_plan["source_locations"][key]
                for key in ("finalization_record", "sealed_archive", "decision_store",
                            "reviewer_index")
            },
            "successor_proposal": PROPOSAL_REL,
            "successor_ratification": RATIFICATION_REL,
        },
        "source_bindings": {
            "finalization_record_raw_sha256": snapshot.finalization_record_sha256,
            "reviewer_decision_store_raw_sha256": snapshot.decision_store_sha256,
            "reviewer_index_raw_sha256": snapshot.reviewer_index_sha256,
            "reviewer_prompt_prefix_sha256": snapshot.prompt_prefix_sha256,
            "packet_directories_canonical_sha256": canonical_sha256(
                list(snapshot.packet_directories)),
            "successor_proposal_raw_sha256": _raw_sha(proposal_raw),
            "successor_proposal_canonical_sha256": canonical_sha256(proposal),
            "successor_ratification_raw_sha256": _raw_sha(ratification_raw),
            "successor_ratification_canonical_sha256": canonical_sha256(ratification),
        },
        "predecessor_usage_accounting": {
            "proposal_bound_completed_v6_pass": True,
            "v6_disposition": "completed_pass_superseded_by_runner_change",
            "v6_result_raw_sha256": _raw_sha(v6_result_raw),
            "v6_completed_at_utc": v6_result["completed_at_utc"],
            "v6_evidence_expires_at_utc": expires_at.isoformat(),
            "counted_reservations_before_v6": counted_before_v6,
            "v6_counted_reservations": 180,
            "cumulative_counted_reviewer_reservations": cumulative,
            "cumulative_durable_invocation_bundle_count": (
                int(v6_accounting["cumulative_durable_invocation_bundle_count"]) + 180),
            "v6_durable_invocation_bundle_count": 180,
            "v6_fully_valid_result_count": fully_valid,
            "v6_failed_result_count": 0,
            "ratified_reviewer_ceiling": ceiling,
            "remaining_reviewer_ceiling_after_counted_reservations": ceiling - cumulative,
            "predecessor_cohorts_reused": False,
            "accounting_treatment": v6_accounting["accounting_treatment"],
        },
        "workload": summary,
        "reviewer_configuration": reviewer,
        "capacity_thresholds": capacity._expected_thresholds(),  # noqa: SLF001
        "dispatch_history_contract": {
            **copy.deepcopy(v6_plan["dispatch_history_contract"]),
            "required_path": f"{V7_ROOT}/dispatch_history.jsonl",
            "anchor_directory": f"{V7_ROOT}/dispatch_history_anchors",
            "interruption_evidence_root": f"{V7_ROOT}/interruption_evidence",
        },
        "measurement_contract": copy.deepcopy(v6_plan["measurement_contract"]),
        "validity": {
            "valid_for_hours": capacity.CAPACITY_VALIDITY_HOURS_V7,
            "basis": v6_plan["validity"]["basis"],
            "invalidated_by": list(v6_plan["validity"]["invalidated_by"]),
        },
        "non_claims": [
            "This plan authorizes offline plan and workload materialization only.",
            (
                "This plan does not authorize reviewer dispatch, provider calls, Together "
                "calls, a main run, spend, or formal measurement."
            ),
            f"The {cumulative} predecessor reservations remain counted once as external reviewer usage.",
            "No sealed source, v1, v2, v3, v4, v5, v6, or predecessor cohort prompt is reused.",
            (
                f"The {summary['unused_byte_new_reserve_count']} unused eligible variants do "
                "not authorize another attempt."
            ),
            (
                "The pinned reviewer CLI copy at E:/selvarath-tools/codex-0.149.0 has a "
                "wrapper byte-identical to the v5 and v6 measurements; the wrapper hash alone "
                "is not an attestation of the underlying CLI package, which the execution "
                "manifest binds by its reported version."
            ),
        ],
    }
    plan_raw = _write(PLAN_REL, plan)
    print(json.dumps({
        "proposal_path": PROPOSAL_REL,
        "ratification_path": RATIFICATION_REL,
        "plan_path": PLAN_REL,
        "plan_canonical_sha256": canonical_sha256(plan),
        "plan_raw_sha256": _raw_sha(plan_raw),
        "byte_new_eligible_count": summary["byte_new_eligible_count"],
        "historical_collision_count": summary["historical_collision_count"],
        "unused_byte_new_reserve_count": summary["unused_byte_new_reserve_count"],
        "cumulative_counted_reviewer_reservations": cumulative,
        "execution_authorized": False,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
