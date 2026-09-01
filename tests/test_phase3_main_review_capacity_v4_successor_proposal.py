"""Checks for the non-authorizing Phase 3 capacity v4 successor proposal."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from rejudge.phase2_dual_gate import parse_reviewer_output
from scripts import phase3_main_review_capacity_preflight as capacity


REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_v4_successor_proposal_2026-09-01.json"
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        assert key not in value, f"duplicate JSON key: {key}"
        value[key] = item
    return value


def _load(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=_unique_object)
    assert isinstance(value, dict)
    return raw, value


def _git_blob(commit: str, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return completed.stdout


def test_v4_successor_proposal_records_terminal_failure_and_no_authority() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)

    assert proposal["schema_version"] == (
        "phase3_main_review_capacity_v4_successor_proposal_v1"
    )
    assert proposal["proposal_only"] is True
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

    predecessor = proposal["predecessor_v3_attempt"]
    assert predecessor["disposition"] == "terminal_completed_failure_retained"
    assert predecessor["failure_receipt"]["attempted_dispatches"] == 60
    assert predecessor["failure_receipt"]["dispatch_reservation_count"] == 60
    assert predecessor["failure_receipt"]["validated_invocation_receipts"] == 58
    audit = predecessor["full_offline_audit"]
    assert audit["durable_invocation_bundle_count"] == 60
    assert audit["fully_valid_result_count"] == 58
    assert audit["failed_result_count"] == 2
    assert audit["result_file_present"] is False
    assert audit["wave_output_file_count"] == 0
    accounting = predecessor["accounting"]
    assert accounting["cumulative_counted_reservations"] == 180
    assert accounting["cumulative_durable_invocation_bundles"] == 120
    assert accounting["remaining_reviewer_ceiling_after_counted_reservations"] == 58860
    assert "not reused" in predecessor["non_reuse"]


def test_transport_repair_is_commit_bound_and_preserves_the_event_gate() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    repair = proposal["transport_repair"]

    assert repair["configuration_key"] == (
        "model_providers.openai.supports_websockets"
    )
    assert repair["configuration_value"] is False
    assert repair["codex_exec_arguments"] == [
        "-c",
        "model_providers.openai.supports_websockets=false",
    ]
    assert "unchanged" in repair["event_stream_policy"]
    assert repair["offline_validation"]["provider_calls_made"] is False
    for binding in repair["files"]:
        raw = _git_blob(repair["commit"], binding["path"])
        assert len(raw) == binding["byte_count"]
        assert hashlib.sha256(raw).hexdigest() == binding["raw_sha256"]


def test_bound_v3_failure_evidence_matches_when_available() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    predecessor = proposal["predecessor_v3_attempt"]
    manifest_path = Path(predecessor["execution_manifest"]["path"])
    if not manifest_path.is_file():
        pytest.skip("bound external v3 capacity archive is unavailable")

    bindings = (
        predecessor["workload_manifest"],
        predecessor["execution_manifest"],
        predecessor["authorization"],
        predecessor["dispatch_history"],
        predecessor["attempt_reservation"],
        predecessor["failure_receipt"],
    )
    for binding in bindings:
        path = Path(binding["path"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["raw_sha256"]
    signature_path = Path(predecessor["authorization"]["path"] + ".sig")
    assert hashlib.sha256(signature_path.read_bytes()).hexdigest() == predecessor[
        "authorization"
    ]["detached_signature_raw_sha256"]

    history_rows = [
        json.loads(line, object_pairs_hook=_unique_object)
        for line in Path(predecessor["dispatch_history"]["path"])
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(history_rows) == predecessor["dispatch_history"]["event_count"] == 2
    assert history_rows[0]["event_hash"] == predecessor["dispatch_history"][
        "dispatch_started_event_hash"
    ]
    assert history_rows[1]["event_hash"] == predecessor["dispatch_history"][
        "attempt_completed_fail_event_hash"
    ]

    _failure_raw, failure = _load(Path(predecessor["failure_receipt"]["path"]))
    reservations = failure["dispatch_reservations"]
    assert len(reservations) == 60
    assert capacity.canonical_sha256(reservations) == predecessor[
        "full_offline_audit"
    ]["dispatch_reservation_bindings_canonical_sha256"]
    for binding in reservations:
        raw = Path(binding["path"]).read_bytes()
        assert len(raw) == binding["byte_count"]
        assert hashlib.sha256(raw).hexdigest() == binding["raw_sha256"]

    _manifest_raw, manifest = _load(manifest_path)
    receipt_hashes: list[str] = []
    ruling_hashes: list[str] = []
    fully_valid = 0
    failures: list[str] = []
    for wave in manifest["workload"]["waves"]:
        wave_dir = Path(wave["directory"])
        for packet_binding in wave["packet_bindings"]:
            packet_name = packet_binding["file"]
            evidence_dir = wave_dir / "reviewer_evidence" / f"{packet_name}.evidence"
            receipt_path = evidence_dir / "invocation_receipt.json"
            if not receipt_path.is_file():
                continue
            receipt_raw, receipt = _load(receipt_path)
            ruling_raw = (evidence_dir / "ruling.txt").read_bytes()
            ruling = ruling_raw.decode("utf-8").strip()
            receipt_hashes.append(hashlib.sha256(receipt_raw).hexdigest())
            ruling_hashes.append(hashlib.sha256(ruling_raw).hexdigest())
            outcome = receipt["outcome"]
            parse_valid = parse_reviewer_output(ruling) != (None, None, None)
            evidence_valid = (
                outcome["result_ok"] is True
                and outcome["error"] is None
                and outcome["event_stream_errors"] == []
                and outcome["commands"] == []
                and outcome["timed_out"] is False
                and outcome["process_exit_code"] == 0
            )
            if parse_valid and evidence_valid:
                fully_valid += 1
            else:
                failures.append(packet_name)

    audit = predecessor["full_offline_audit"]
    assert len(receipt_hashes) == audit["durable_invocation_bundle_count"] == 60
    assert capacity.canonical_sha256(receipt_hashes) == audit[
        "durable_invocation_receipt_hashes_canonical_sha256"
    ]
    assert capacity.canonical_sha256(ruling_hashes) == audit[
        "durable_ruling_hashes_canonical_sha256"
    ]
    assert fully_valid == audit["fully_valid_result_count"] == 58
    assert failures == [item["packet"] for item in predecessor["failed_results"]]

    for failure_binding in predecessor["failed_results"]:
        packet_name = failure_binding["packet"]
        wave_dir = Path(manifest["workload"]["waves"][0]["directory"])
        evidence_dir = wave_dir / "reviewer_evidence" / f"{packet_name}.evidence"
        assert hashlib.sha256((wave_dir / packet_name).read_bytes()).hexdigest() == (
            failure_binding["packet_raw_sha256"]
        )
        artifact_names = {
            "ruling_raw_sha256": "ruling.txt",
            "invocation_receipt_raw_sha256": "invocation_receipt.json",
            "event_stream_raw_sha256": "codex_events.jsonl",
            "stderr_raw_sha256": "codex_stderr.bin",
        }
        for binding_field, artifact_name in artifact_names.items():
            assert hashlib.sha256(
                (evidence_dir / artifact_name).read_bytes()
            ).hexdigest() == failure_binding[binding_field]


def test_v4_field_order_derivation_recomputes_exact_hashes() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    if not capacity.ARCHIVE_DIR_DEFAULT.is_dir():
        pytest.skip("sealed external source archive is unavailable")

    snapshot = capacity.collect_source_snapshot(
        archive_dir=capacity.ARCHIVE_DIR_DEFAULT,
        finalization_path=capacity.FINALIZATION_PATH_DEFAULT,
    )
    v1 = capacity.derive_workload(
        snapshot,
        derivation_tag=capacity.DERIVATION_TAG_V1,
    )
    v2 = capacity.derive_workload(
        snapshot,
        derivation_tag=capacity.DERIVATION_TAG_V2,
    )
    v3 = capacity.derive_workload(
        snapshot,
        derivation_tag=capacity.DERIVATION_TAG_V3,
    )
    prompt_sets = {
        "sealed_source": set(snapshot.historical_prompt_hashes),
        "v1_candidate_swap": {item.variant_prompt_sha256 for item in v1.eligible},
        "v2_candidate_line_order": {
            item.variant_prompt_sha256 for item in v2.eligible
        },
        "v3_candidate_line_order": {
            item.variant_prompt_sha256 for item in v3.eligible
        },
    }
    excluded = set().union(*prompt_sets.values())
    empty_sources = sorted(
        item.source_payload_sha256 for item in snapshot.source_packets if not item.query
    )

    variants: list[tuple[str, str, str, str, str]] = []
    collisions: list[str] = []
    for source in snapshot.source_packets:
        if not source.query:
            continue
        rendered = (
            source.prompt_prefix
            + f"CANDIDATE A: {source.candidate_a}\n"
            + f"QUERY: {source.query}\n"
            + f"CANDIDATE B: {source.candidate_b}"
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
                "phase3-main-review-capacity-v4|"
                + source.source_payload_sha256
            ).encode("utf-8")
        ).hexdigest()
        variants.append(
            (
                rank_sha,
                source.source_payload_sha256,
                source.source_prompt_sha256,
                payload_sha,
                prompt_sha,
            )
        )
    variants.sort()

    design = proposal["successor_v4_design"]
    excluded_claim = design["excluded_prompt_sets"]
    assert len(empty_sources) == design["empty_query_source_count"] == 5
    assert capacity.canonical_sha256(empty_sources) == design[
        "empty_query_source_payloads_canonical_sha256"
    ]
    for name, values in prompt_sets.items():
        assert len(values) == excluded_claim[f"{name}_prompt_count"]
        assert capacity.canonical_sha256(sorted(values)) == excluded_claim[
            f"{name}_prompt_set_canonical_sha256"
        ]
    assert len(excluded) == excluded_claim["combined_excluded_prompt_count"]
    assert capacity.canonical_sha256(sorted(excluded)) == excluded_claim[
        "combined_excluded_prompt_set_canonical_sha256"
    ]
    assert len(variants) == design["eligible_variant_count"] == 571
    assert len(collisions) == design["collision_count"] == 0
    assert len(variants) - 360 == design["unused_variant_count"] == 211

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
    assert capacity.canonical_sha256([item[4] for item in variants]) == design[
        "all_ranked_prompt_list_canonical_sha256"
    ]
    assert capacity.canonical_sha256([item[1] for item in variants]) == design[
        "all_ranked_source_payload_list_canonical_sha256"
    ]
    assert [capacity.canonical_sha256(records(cohort)) for cohort in cohorts] == design[
        "cohort_records_canonical_sha256s"
    ]
    assert [
        capacity.canonical_sha256([item[1] for item in cohort]) for cohort in cohorts
    ] == design["cohort_source_payloads_canonical_sha256s"]
    assert [
        capacity.canonical_sha256([item[3] for item in cohort]) for cohort in cohorts
    ] == design["cohort_variant_payloads_canonical_sha256s"]
    assert [
        capacity.canonical_sha256([item[4] for item in cohort]) for cohort in cohorts
    ] == design["cohort_variant_prompts_canonical_sha256s"]
    assert design["empty_query_count_per_cohort"] == [0, 0]
