"""Checks for the non-authorizing Phase 3 capacity v5 successor proposal."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import codex_reviewer_batch
from scripts import phase3_main_review_capacity_preflight as capacity


REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = (
    REPO_ROOT
    / "rejudge"
    / "phase3_main_review_capacity_v5_successor_proposal_2026-09-01.json"
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


def test_v5_successor_proposal_records_terminal_v4_failure_and_no_authority() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)

    assert proposal["schema_version"] == (
        "phase3_main_review_capacity_v5_successor_proposal_v1"
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

    predecessor = proposal["predecessor_v4_attempt"]
    assert predecessor["disposition"] == "terminal_completed_failure_retained"
    assert predecessor["failure_receipt"]["attempted_dispatches"] == 60
    assert predecessor["failure_receipt"]["dispatch_reservation_count"] == 60
    assert predecessor["failure_receipt"]["validated_invocation_receipts"] == 0
    audit = predecessor["full_offline_audit"]
    assert audit["durable_invocation_bundle_count"] == 60
    assert audit["fully_valid_result_count"] == 0
    assert audit["failed_result_count"] == 60
    assert audit["empty_event_stream_count"] == 60
    assert audit["empty_ruling_count"] == 60
    assert audit["result_file_present"] is False
    assert audit["wave_output_file_count"] == 0
    deterministic = predecessor["deterministic_failure"]
    assert deterministic["failed_invocation_count"] == 60
    assert deterministic["unique_stderr_count"] == 1
    assert deterministic["stderr_byte_count"] == 217
    assert "reserved built-in provider IDs" in deterministic["stderr_text"]
    accounting = predecessor["accounting"]
    assert accounting["cumulative_counted_reservations"] == 240
    assert accounting["cumulative_durable_invocation_bundles"] == 180
    assert accounting["remaining_reviewer_ceiling_after_counted_reservations"] == 58800
    assert "not reused" in predecessor["non_reuse"]


def test_custom_provider_repair_is_commit_bound_and_exact() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    repair = proposal["transport_repair"]
    profile = repair["model_provider_profile"]

    assert codex_reviewer_batch.normalize_model_provider_profile(profile) == profile
    assert repair["codex_exec_arguments"] == codex_reviewer_batch._model_provider_argv(  # noqa: SLF001
        profile
    )
    assert repair["base_url_treatment"].startswith("omit base_url")
    assert "unchanged" in repair["event_stream_policy"]
    assert repair["offline_validation"] == {
        "codex_cli_version": "codex-cli 0.149.0",
        "custom_profile_config_parse": "pass",
        "focused_reviewer_capacity_and_main_live_tests": "180 passed, 2 skipped",
        "capacity_derivation_and_successor_tests": "75 passed",
        "full_test_suite": "3012 passed, 67 skipped",
        "provider_calls_made": False,
    }
    for binding in repair["files"]:
        raw = _git_blob(repair["commit"], binding["path"])
        assert len(raw) == binding["byte_count"]
        assert hashlib.sha256(raw).hexdigest() == binding["raw_sha256"]


def test_v4_predecessor_archive_recomputes_exact_failure_when_available() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    predecessor = proposal["predecessor_v4_attempt"]
    manifest_path = Path(predecessor["execution_manifest"]["path"])
    if not manifest_path.is_file():
        pytest.skip("bound external v4 capacity archive is unavailable")

    plan = predecessor["plan"]
    plan_raw = _git_blob(predecessor["source_commit"], plan["tracked_path"])
    assert hashlib.sha256(plan_raw).hexdigest() == plan["raw_sha256"]
    assert capacity.canonical_sha256(json.loads(plan_raw)) == plan["canonical_sha256"]
    for binding in (
        predecessor["workload_manifest"],
        predecessor["execution_manifest"],
        predecessor["authorization"],
        predecessor["dispatch_history"],
        predecessor["attempt_reservation"],
        predecessor["failure_receipt"],
    ):
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
    audit = predecessor["full_offline_audit"]
    assert len(reservations) == audit["dispatch_reservation_count"] == 60
    assert capacity.canonical_sha256(reservations) == audit[
        "dispatch_reservation_bindings_canonical_sha256"
    ]
    for binding in reservations:
        raw = Path(binding["path"]).read_bytes()
        assert len(raw) == binding["byte_count"]
        assert hashlib.sha256(raw).hexdigest() == binding["raw_sha256"]

    _manifest_raw, manifest = _load(manifest_path)
    receipt_hashes: list[str] = []
    ruling_hashes: list[str] = []
    event_hashes: list[str] = []
    stderr_hashes: list[str] = []
    stderr_values: set[bytes] = set()
    for wave in manifest["workload"]["waves"]:
        wave_dir = Path(wave["directory"])
        for packet_binding in wave["packet_bindings"]:
            evidence_dir = (
                wave_dir
                / "reviewer_evidence"
                / f"{packet_binding['file']}.evidence"
            )
            receipt_path = evidence_dir / "invocation_receipt.json"
            if not receipt_path.is_file():
                continue
            receipt_raw, receipt = _load(receipt_path)
            ruling_raw = (evidence_dir / "ruling.txt").read_bytes()
            event_raw = (evidence_dir / "codex_events.jsonl").read_bytes()
            stderr_raw = (evidence_dir / "codex_stderr.bin").read_bytes()
            assert receipt["schema_version"] == audit["invocation_evidence_schema"]
            assert receipt["outcome"]["result_ok"] is False
            assert event_raw == b""
            assert ruling_raw == b""
            receipt_hashes.append(hashlib.sha256(receipt_raw).hexdigest())
            ruling_hashes.append(hashlib.sha256(ruling_raw).hexdigest())
            event_hashes.append(hashlib.sha256(event_raw).hexdigest())
            stderr_hashes.append(hashlib.sha256(stderr_raw).hexdigest())
            stderr_values.add(stderr_raw)

    assert len(receipt_hashes) == audit["durable_invocation_bundle_count"] == 60
    assert capacity.canonical_sha256(receipt_hashes) == audit[
        "durable_invocation_receipt_hashes_canonical_sha256"
    ]
    assert capacity.canonical_sha256(ruling_hashes) == audit[
        "durable_ruling_hashes_canonical_sha256"
    ]
    assert capacity.canonical_sha256(event_hashes) == audit[
        "durable_event_stream_hashes_canonical_sha256"
    ]
    assert capacity.canonical_sha256(stderr_hashes) == audit[
        "durable_stderr_hashes_canonical_sha256"
    ]
    assert len(stderr_values) == 1
    stderr_raw = next(iter(stderr_values))
    deterministic = predecessor["deterministic_failure"]
    assert len(stderr_raw) == deterministic["stderr_byte_count"]
    assert hashlib.sha256(stderr_raw).hexdigest() == deterministic[
        "stderr_raw_sha256"
    ]
    assert stderr_raw.decode("utf-8") == deterministic["stderr_text"]
    assert not Path(manifest["result_path"]).exists()
    assert not any(
        Path(wave["output_path"]).exists() for wave in manifest["workload"]["waves"]
    )


def test_v5_field_order_derivation_recomputes_exact_hashes() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    if not capacity.ARCHIVE_DIR_DEFAULT.is_dir():
        pytest.skip("sealed external source archive is unavailable")

    snapshot = capacity.collect_source_snapshot(
        archive_dir=capacity.ARCHIVE_DIR_DEFAULT,
        finalization_path=capacity.FINALIZATION_PATH_DEFAULT,
    )
    workloads = {
        "v1_candidate_swap": capacity.derive_workload(
            snapshot, derivation_tag=capacity.DERIVATION_TAG_V1
        ),
        "v2_candidate_line_order": capacity.derive_workload(
            snapshot, derivation_tag=capacity.DERIVATION_TAG_V2
        ),
        "v3_candidate_line_order": capacity.derive_workload(
            snapshot, derivation_tag=capacity.DERIVATION_TAG_V3
        ),
        "v4_candidate_line_order": capacity.derive_workload(
            snapshot, derivation_tag=capacity.DERIVATION_TAG_V4
        ),
    }
    prompt_sets = {
        "sealed_source": set(snapshot.historical_prompt_hashes),
        **{
            name: {item.variant_prompt_sha256 for item in workload.eligible}
            for name, workload in workloads.items()
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
            + f"CANDIDATE B: {source.candidate_b}\n"
            + f"QUERY: {source.query}\n"
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
                "phase3-main-review-capacity-v5|"
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

    design = proposal["successor_v5_design"]
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


def test_exact_ratification_text_grants_offline_materialization_only() -> None:
    _raw, proposal = _load(PROPOSAL_PATH)
    text = proposal["exact_non_execution_ratification_text"]

    assert "model_provider=openai-http" in text
    assert "supports_websockets=false" in text
    assert "candidate-B then query then candidate-A" in text
    assert "offline v5 plan and workload materialization only" in text
    assert text.endswith(
        "It grants no reviewer dispatch, provider call, Together call, main run, or spend authority."
    )
