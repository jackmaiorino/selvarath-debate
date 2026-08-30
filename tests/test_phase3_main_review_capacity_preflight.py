from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts import phase3_main_review_capacity_preflight as capacity


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def _prompt(query: str, candidate_a: str, candidate_b: str) -> str:
    return (
        "offline frozen reviewer prefix"
        + capacity.PAYLOAD_SEPARATOR
        + f"QUERY: {query}\nCANDIDATE A: {candidate_a}\nCANDIDATE B: {candidate_b}"
    )


def _synthetic_archive(
    tmp_path: Path, payloads: list[tuple[str, str, str]]
) -> tuple[Path, Path]:
    archive = tmp_path / "archive"
    packet_dir = archive / "review_packets_auto_test"
    packet_dir.mkdir(parents=True)
    index_items = []
    decision_rows = []
    for position, (query, candidate_a, candidate_b) in enumerate(payloads, 1):
        payload_hash = capacity.payload_sha256(query, candidate_a, candidate_b)
        prompt = _prompt(query, candidate_a, candidate_b)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        file_name = f"{position:03d}_{payload_hash[:12]}.txt"
        _write_text(packet_dir / file_name, prompt)
        index_items.append(
            {
                "n": position,
                "file": file_name,
                "payload_sha256": payload_hash,
                "prompt_sha256": prompt_hash,
            }
        )
        decision_rows.append({"payload_sha256": payload_hash})
    _write_text(
        packet_dir / "INDEX.json",
        json.dumps({"count": len(index_items), "items": index_items}, indent=1) + "\n",
    )
    decisions = archive / "phase3_v3_reviewer_decisions.jsonl"
    reviewer_index = archive / "reviewer_index.jsonl"
    _write_text(decisions, "".join(json.dumps(row) + "\n" for row in decision_rows))
    _write_text(
        reviewer_index,
        json.dumps(
            {
                "wave": 1,
                "payload_count": len(payloads),
                "new_packet_directories": [packet_dir.name],
            }
        )
        + "\n",
    )
    finalization = tmp_path / "finalization.json"
    _write_text(
        finalization,
        json.dumps(
            {
                "archive_path": str(archive.resolve()),
                "archive_file_manifest": {
                    decisions.name: {"sha256": capacity.raw_sha256(decisions)},
                    reviewer_index.name: {"sha256": capacity.raw_sha256(reviewer_index)},
                },
            },
            indent=1,
        )
        + "\n",
    )
    return archive, finalization


def test_swapped_materialization_excludes_historical_mirrors_and_is_deterministic(
    tmp_path: Path,
) -> None:
    archive, finalization = _synthetic_archive(
        tmp_path,
        [
            ("query one", "answer A1", "answer B1"),
            ("query one", "answer B1", "answer A1"),
            ("query two", "answer A2", "answer B2"),
            ("query three", "answer A3", "answer B3"),
        ],
    )
    snapshot = capacity.collect_source_snapshot(
        archive_dir=archive, finalization_path=finalization
    )
    first = capacity.derive_workload(
        snapshot, selection_count=2, wave_size=1, cohort_count=1
    )
    second = capacity.derive_workload(
        snapshot, selection_count=2, wave_size=1, cohort_count=1
    )

    assert first.summary == second.summary
    assert first.summary["source_unique_packet_count"] == 4
    assert first.summary["historical_collision_count"] == 2
    assert first.summary["byte_new_eligible_count"] == 2
    assert first.summary["unused_byte_new_reserve_count"] == 0
    assert len(first.selected) == 2
    assert all(
        item.variant_prompt_sha256 not in snapshot.historical_prompt_hashes
        for item in first.selected
    )


def test_source_collection_rejects_packet_byte_drift(tmp_path: Path) -> None:
    archive, finalization = _synthetic_archive(
        tmp_path, [("query", "answer A", "answer B")]
    )
    packet = next((archive / "review_packets_auto_test").glob("*.txt"))
    _write_text(packet, packet.read_text(encoding="utf-8") + "tamper")

    with pytest.raises(capacity.CapacityPreflightError, match="packet prompt hash mismatch"):
        capacity.collect_source_snapshot(
            archive_dir=archive, finalization_path=finalization
        )


@pytest.mark.parametrize("component", [".", "..", "a/b", "a\\b", ""])
def test_source_path_components_fail_closed(component: str) -> None:
    with pytest.raises(capacity.CapacityPreflightError, match="safe path component"):
        capacity._require_safe_path_component(component, field="test component")


def _empty_history() -> capacity.DispatchHistorySnapshot:
    return capacity.parse_dispatch_history_bytes(b"")


def test_materializer_writes_only_bound_offline_packet_artifacts(tmp_path: Path) -> None:
    archive, finalization = _synthetic_archive(
        tmp_path,
        [
            ("query one", "answer A1", "answer B1"),
            ("query two", "answer A2", "answer B2"),
        ],
    )
    snapshot = capacity.collect_source_snapshot(
        archive_dir=archive, finalization_path=finalization
    )
    workload = capacity.derive_workload(
        snapshot, selection_count=2, wave_size=1, cohort_count=1
    )
    plan = {
        "reviewer_configuration": {"model": "never-dispatched"},
        "source_locations": {"sealed_archive": str(archive.resolve())},
        "dispatch_history_contract": {
            "required_initial_raw_sha256": hashlib.sha256(b"").hexdigest(),
            "anchor_validation_required": False,
        },
    }
    target = tmp_path / "materialized"

    manifest = capacity.materialize_workload(
        target,
        plan=plan,
        workload=workload,
        dispatch_history=_empty_history(),
    )

    assert manifest["execution_authorized"] is False
    assert manifest["provider_calls_authorized"] is False
    assert manifest["cohort_number"] == 1
    assert manifest["dispatch_history_raw_sha256_before_materialization"] == hashlib.sha256(
        b""
    ).hexdigest()
    assert [wave["count"] for wave in manifest["waves"]] == [1, 1]
    for wave_number, item in enumerate(workload.selected, 1):
        wave_dir = target / f"wave_{wave_number:02d}"
        index = json.loads((wave_dir / "INDEX.json").read_text(encoding="utf-8"))
        assert index["items"][0]["payload_sha256"] == item.variant_payload_sha256
        packet = wave_dir / index["items"][0]["file"]
        assert hashlib.sha256(packet.read_bytes()).hexdigest() == item.variant_prompt_sha256
    with pytest.raises(capacity.CapacityPreflightError, match="sealed source archive"):
        capacity.materialize_workload(
            archive / "forbidden-capacity-output",
            plan=plan,
            workload=workload,
            dispatch_history=_empty_history(),
        )


def _production_workload() -> capacity.DerivedWorkload:
    variants = []
    for index in range(360):
        variants.append(
            capacity.VariantPacket(
                rank_sha256=hashlib.sha256(f"rank-{index}".encode()).hexdigest(),
                source_payload_sha256=hashlib.sha256(
                    f"source-{index}".encode()
                ).hexdigest(),
                source_prompt_sha256=hashlib.sha256(f"old-{index}".encode()).hexdigest(),
                variant_payload_sha256=hashlib.sha256(
                    f"payload-{index}".encode()
                ).hexdigest(),
                variant_prompt_sha256=hashlib.sha256(
                    f"prompt-{index}".encode()
                ).hexdigest(),
                prompt=f"packet {index}",
            )
        )
    cohorts = [variants[:180], variants[180:]]
    summary = {
        "wave_size": 60,
        "cohort_records_canonical_sha256s": [
            capacity.canonical_sha256(
                [item.variant_payload_sha256 for item in cohort]
            )
            for cohort in cohorts
        ],
        "cohort_variant_prompts_canonical_sha256s": [
            capacity.canonical_sha256(
                [item.variant_prompt_sha256 for item in cohort]
            )
            for cohort in cohorts
        ],
    }
    return capacity.DerivedWorkload(
        selected=tuple(cohorts[0]),
        retry_selected=tuple(cohorts[1]),
        eligible=tuple(variants),
        collision_source_payloads=(),
        summary=summary,
    )


def _result_plan(tmp_path: Path | None = None) -> dict[str, Any]:
    authority_root = (tmp_path or Path.cwd()).resolve()
    return {
        "reviewer_configuration": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "reviewer_cli_binary": "codex.cmd",
            "reviewer_cli_resolved_path": (
                "C:/Users/Jack/AppData/Roaming/npm/codex.cmd"
            ),
            "reviewer_cli_wrapper_raw_sha256": (
                "c54db6755e710c39703f7c37512f9e35ed41042d8080558d2b84b8d2694323c3"
            ),
            "reviewer_cli_wrapper_byte_count": 341,
            "concurrency": 12,
            "fresh_ephemeral_context_per_packet": True,
            "tool_use_permitted": False,
            "wave_pending_payload_limit": 64,
            "actual_capacity_wave_size": 60,
        },
        "capacity_thresholds": capacity._expected_thresholds(),
        "dispatch_history_contract": {
            "required_path": str(authority_root / "dispatch_history.jsonl"),
            "anchor_directory": str(authority_root / "dispatch_history_anchors"),
            "interruption_evidence_root": str(authority_root / "interruption_evidence"),
            "required_initial_raw_sha256": hashlib.sha256(b"").hexdigest(),
            "anchor_validation_required": False,
        },
        "source_locations": {"sealed_archive": str(authority_root / "sealed-source")},
        "validity": {"valid_for_hours": 24},
    }


def _history_event(
    *,
    plan: dict[str, Any],
    workload: capacity.DerivedWorkload,
    sequence: int,
    event: str,
    attempt_number: int,
    attempt_id: str,
    previous_hash: str,
) -> dict[str, Any]:
    interruption_evidence = None
    if event == "attempt_interrupted":
        evidence_root = Path(
            plan["dispatch_history_contract"]["interruption_evidence_root"]
        )
        evidence_root.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_root / "test-interruption.log"
        evidence_bytes = b"test interruption log"
        evidence_path.write_bytes(evidence_bytes)
        interruption_evidence = {
            "classification": "environmental_interruption",
            "summary": "test host process was externally interrupted",
            "verified_by": "test operator",
            "evidence_path": str(evidence_path.resolve()),
            "evidence_raw_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "evidence_byte_count": len(evidence_bytes),
        }
    row = {
        "schema_version": capacity.DISPATCH_HISTORY_SCHEMA_VERSION,
        "sequence": sequence,
        "event": event,
        "attempt_number": attempt_number,
        "cohort_number": attempt_number,
        "attempt_id": attempt_id,
        "plan_canonical_sha256": capacity.canonical_sha256(plan),
        "cohort_records_canonical_sha256": workload.summary[
            "cohort_records_canonical_sha256s"
        ][attempt_number - 1],
        "cohort_variant_prompts_canonical_sha256": workload.summary[
            "cohort_variant_prompts_canonical_sha256s"
        ][attempt_number - 1],
        "environmental_interruption_evidence": interruption_evidence,
        "recorded_at_utc": f"2026-08-29T12:0{sequence}:00Z",
        "prev_event_hash": previous_hash,
        "event_hash": "",
    }
    row["event_hash"] = capacity.dispatch_history_event_hash(row)
    return row


def _history(
    plan: dict[str, Any],
    workload: capacity.DerivedWorkload,
    event_specs: list[tuple[str, int, str]],
) -> capacity.DispatchHistorySnapshot:
    rows = []
    previous_hash = "genesis"
    for sequence, (event, attempt_number, attempt_id) in enumerate(event_specs):
        row = _history_event(
            plan=plan,
            workload=workload,
            sequence=sequence,
            event=event,
            attempt_number=attempt_number,
            attempt_id=attempt_id,
            previous_hash=previous_hash,
        )
        rows.append(row)
        previous_hash = row["event_hash"]
    raw = "".join(
        json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    return capacity.parse_dispatch_history_bytes(raw)


def _passing_history(
    plan: dict[str, Any],
    workload: capacity.DerivedWorkload,
    *,
    cohort_number: int = 1,
) -> capacity.DispatchHistorySnapshot:
    if cohort_number == 1:
        specs = [
            ("dispatch_started", 1, "attempt-0001"),
            ("attempt_completed_pass", 1, "attempt-0001"),
        ]
    else:
        specs = [
            ("dispatch_started", 1, "attempt-0001"),
            ("attempt_interrupted", 1, "attempt-0001"),
            ("dispatch_started", 2, "attempt-0002"),
            ("attempt_completed_pass", 2, "attempt-0002"),
        ]
    return _history(plan, workload, specs)


def _result_fixture(
    plan: dict[str, Any],
    workload: capacity.DerivedWorkload,
    history: capacity.DispatchHistorySnapshot,
    *,
    cohort_number: int = 1,
) -> dict[str, Any]:
    environment = {
        "reviewer_cli_resolved_path": plan["reviewer_configuration"][
            "reviewer_cli_resolved_path"
        ],
        "reviewer_cli_wrapper_raw_sha256": plan["reviewer_configuration"][
            "reviewer_cli_wrapper_raw_sha256"
        ],
        "reviewer_cli_wrapper_byte_count": plan["reviewer_configuration"][
            "reviewer_cli_wrapper_byte_count"
        ],
        "reviewer_cli_version": "test-version",
        "host_identity": "test-host",
    }
    items = workload.selected if cohort_number == 1 else workload.retry_selected
    waves = []
    for wave_offset in range(3):
        wave_items = items[wave_offset * 60 : (wave_offset + 1) * 60]
        start = 1000.0 + wave_offset * 120.0
        end = start + 100.0
        waves.append(
            {
                "wave": wave_offset + 1,
                "attempt_status": "complete",
                "interrupted": False,
                "process_exit_code": 0,
                "failure_counts": {
                    "timeouts": 0,
                    "reviewer_errors": 0,
                    "tool_uses": 0,
                    "prompt_hash_mismatches": 0,
                    "empty_outputs": 0,
                    "unexpected_rows": 0,
                    "duplicate_rows": 0,
                },
                "monotonic_started_seconds": start,
                "monotonic_completed_seconds": end,
                "elapsed_monotonic_seconds": 100.0,
                "expected_payload_sha256s": [
                    item.variant_payload_sha256 for item in wave_items
                ],
                "results": [
                    {
                        "payload_sha256": item.variant_payload_sha256,
                        "prompt_sha256": item.variant_prompt_sha256,
                        "ok": True,
                        "error": None,
                        "commands": [],
                        "tool_uses": 0,
                        "raw_output": (
                            "LABEL: ALLOW\nCLAUSE: Allowed\n"
                            "RATIONALE: The claim is atomic."
                        ),
                    }
                    for item in wave_items
                ],
            }
        )
    attempt_id = "attempt-0001" if cohort_number == 1 else "attempt-0002"
    return {
        "schema_version": capacity.RESULT_SCHEMA_VERSION,
        "attempt_number": cohort_number,
        "cohort_number": cohort_number,
        "attempt_id": attempt_id,
        "attempt_status": "complete",
        "interrupted": False,
        "dispatch_history_raw_sha256": history.raw_sha256,
        "plan_canonical_sha256": capacity.canonical_sha256(plan),
        "cohort_records_canonical_sha256": workload.summary[
            "cohort_records_canonical_sha256s"
        ][cohort_number - 1],
        "reviewer_configuration": plan["reviewer_configuration"],
        "reviewer_configuration_canonical_sha256": capacity.canonical_sha256(
            plan["reviewer_configuration"]
        ),
        "measurement_environment": environment,
        "measurement_environment_canonical_sha256": capacity.canonical_sha256(environment),
        "started_at_utc": "2026-08-29T12:00:00Z",
        "completed_at_utc": "2026-08-29T12:10:00Z",
        "monotonic_started_seconds": 1000.0,
        "monotonic_completed_seconds": 1360.0,
        "elapsed_monotonic_seconds": 360.0,
        "waves": waves,
    }


def _validate_fixture(
    result: dict[str, Any],
    plan: dict[str, Any],
    workload: capacity.DerivedWorkload,
    history: capacity.DispatchHistorySnapshot,
) -> dict[str, Any]:
    return capacity.validate_result(
        result,
        plan=plan,
        workload=workload,
        dispatch_history=history,
        as_of_utc=datetime(2026, 8, 29, 13, tzinfo=timezone.utc),
    )


def _make_second_wave_too_slow(result: dict[str, Any]) -> None:
    result.update(
        monotonic_completed_seconds=5000.0,
        elapsed_monotonic_seconds=4000.0,
    )
    result["waves"][1].update(
        monotonic_completed_seconds=4721.0,
        elapsed_monotonic_seconds=3601.0,
    )


@pytest.mark.parametrize("cohort_number", [1, 2])
def test_result_validator_accepts_only_complete_fresh_three_wave_evidence(
    cohort_number: int, tmp_path: Path,
) -> None:
    plan = _result_plan(tmp_path)
    workload = _production_workload()
    history = _passing_history(plan, workload, cohort_number=cohort_number)
    result = _result_fixture(plan, workload, history, cohort_number=cohort_number)

    report = _validate_fixture(result, plan, workload, history)

    assert report["validation"] == "pass"
    assert report["cohort_number"] == cohort_number
    assert report["result_rows"] == 180
    assert report["certified_rulings_per_24h"] == 1440


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda result: result.update(interrupted=True), "partial or interrupted"),
        (
            lambda result: result["waves"][0]["results"][0].update(ok=False),
            "reviewer error",
        ),
        (
            lambda result: result["waves"][0]["results"][0].update(commands=["rg"]),
            "used a tool",
        ),
        (
            lambda result: result["waves"][0]["results"][0].update(raw_output="ALLOW"),
            "frozen ruling parser",
        ),
        (
            lambda result: result["waves"][0]["failure_counts"].update(timeouts=1),
            "failed dispatch",
        ),
        (
            _make_second_wave_too_slow,
            "exceeds the time limit",
        ),
        (
            lambda result: result.update(dispatch_history_raw_sha256="0" * 64),
            "dispatch-history hash mismatch",
        ),
        (
            lambda result: result.update(plan_canonical_sha256="0" * 64),
            "different plan",
        ),
        (
            lambda result: result.update(cohort_records_canonical_sha256="0" * 64),
            "different workload",
        ),
        (
            lambda result: result["measurement_environment"].update(
                reviewer_cli_resolved_path="C:/other/codex.cmd"
            ),
            "CLI path differs",
        ),
        (
            lambda result: result["measurement_environment"].update(
                reviewer_cli_version=""
            ),
            "reviewer_cli_version must be nonempty",
        ),
        (
            lambda result: result["waves"][0].update(
                expected_payload_sha256s=[]
            ),
            "expected-payload binding mismatch",
        ),
        (
            lambda result: result["waves"][0]["results"].__setitem__(
                1, result["waves"][0]["results"][0]
            ),
            "duplicate result",
        ),
        (
            lambda result: result["waves"].pop(),
            "exactly three waves",
        ),
    ],
)
def test_result_validator_rejects_failed_or_partial_evidence(mutation, match) -> None:
    plan = _result_plan()
    workload = _production_workload()
    history = _passing_history(plan, workload)
    result = _result_fixture(plan, workload, history)
    mutation(result)

    with pytest.raises(capacity.CapacityPreflightError, match=match):
        _validate_fixture(result, plan, workload, history)


def test_result_validator_rejects_contradictory_label_clause_pair() -> None:
    plan = _result_plan()
    workload = _production_workload()
    history = _passing_history(plan, workload)
    result = _result_fixture(plan, workload, history)
    result["waves"][0]["results"][0]["raw_output"] = (
        "LABEL: REJECT\nCLAUSE: Allowed\nRATIONALE: No valid clause exists."
    )

    with pytest.raises(capacity.CapacityPreflightError, match="frozen ruling parser"):
        _validate_fixture(result, plan, workload, history)


def test_result_validator_rejects_expired_evidence() -> None:
    plan = _result_plan()
    workload = _production_workload()
    history = _passing_history(plan, workload)
    result = _result_fixture(plan, workload, history)

    with pytest.raises(capacity.CapacityPreflightError, match="expired"):
        capacity.validate_result(
            result,
            plan=plan,
            workload=workload,
            dispatch_history=history,
            as_of_utc=datetime(2026, 8, 30, 13, tzinfo=timezone.utc),
        )


def test_result_validator_rejects_naive_as_of_timestamp() -> None:
    plan = _result_plan()
    workload = _production_workload()
    history = _passing_history(plan, workload)
    result = _result_fixture(plan, workload, history)

    with pytest.raises(capacity.CapacityPreflightError, match="timezone-aware UTC"):
        capacity.validate_result(
            result,
            plan=plan,
            workload=workload,
            dispatch_history=history,
            as_of_utc=datetime(2026, 8, 29, 13),
        )


def test_completed_failure_and_unverified_retry_cannot_be_erased() -> None:
    plan = _result_plan()
    workload = _production_workload()
    failed_then_retry = _history(
        plan,
        workload,
        [
            ("dispatch_started", 1, "attempt-0001"),
            ("attempt_completed_fail", 1, "attempt-0001"),
            ("dispatch_started", 2, "attempt-0002"),
            ("attempt_completed_pass", 2, "attempt-0002"),
        ],
    )
    result = _result_fixture(plan, workload, failed_then_retry, cohort_number=2)

    with pytest.raises(capacity.CapacityPreflightError, match="completed failure"):
        _validate_fixture(result, plan, workload, failed_then_retry)


def test_materialization_state_allows_only_pristine_first_or_verified_retry(
    tmp_path: Path,
) -> None:
    plan = _result_plan(tmp_path)
    workload = _production_workload()
    interrupted = _history(
        plan,
        workload,
        [
            ("dispatch_started", 1, "attempt-0001"),
            ("attempt_interrupted", 1, "attempt-0001"),
        ],
    )
    completed = _passing_history(plan, workload)

    assert (
        capacity._permitted_materialization_cohort(
            _empty_history(), plan=plan, workload=workload
        )
        == 1
    )
    assert (
        capacity._permitted_materialization_cohort(
            interrupted, plan=plan, workload=workload
        )
        == 2
    )
    with pytest.raises(capacity.CapacityPreflightError, match="does not permit"):
        capacity._permitted_materialization_cohort(
            completed, plan=plan, workload=workload
        )


def test_second_interruption_blocks_any_third_materialization(tmp_path: Path) -> None:
    plan = _result_plan(tmp_path)
    workload = _production_workload()
    twice_interrupted = _history(
        plan,
        workload,
        [
            ("dispatch_started", 1, "attempt-0001"),
            ("attempt_interrupted", 1, "attempt-0001"),
            ("dispatch_started", 2, "attempt-0002"),
            ("attempt_interrupted", 2, "attempt-0002"),
        ],
    )

    with pytest.raises(capacity.CapacityPreflightError, match="does not permit"):
        capacity._permitted_materialization_cohort(
            twice_interrupted, plan=plan, workload=workload
        )


def test_dispatch_history_rejects_torn_rows_and_duplicate_keys() -> None:
    with pytest.raises(capacity.CapacityPreflightError, match="torn final row"):
        capacity.parse_dispatch_history_bytes(b'{"event":"dispatch_started"}')
    with pytest.raises(capacity.CapacityPreflightError, match="duplicate JSON key"):
        capacity.parse_dispatch_history_bytes(b'{"event":1,"event":2}\n')


def _anchored_plan(tmp_path: Path) -> dict[str, Any]:
    plan = _result_plan(tmp_path)
    plan["dispatch_history_contract"].update(
        {
            "schema_version": capacity.DISPATCH_HISTORY_SCHEMA_VERSION,
            "append_only": True,
            "anchor_validation_required": True,
            "initialization_receipt_required": True,
            "maximum_attempts": 2,
        }
    )
    return plan


@pytest.mark.parametrize(
    "fault_at",
    [
        "after_initialization_intent",
        "after_evidence_root",
        "after_anchor_directory",
        "after_initial_history",
        "after_initialization_receipt",
    ],
)
def test_initialization_is_retry_safe_at_every_durable_boundary(
    tmp_path: Path, fault_at: str
) -> None:
    plan = _anchored_plan(tmp_path)
    history_path = Path(plan["dispatch_history_contract"]["required_path"])

    with pytest.raises(capacity.InjectedCapacityFault, match=fault_at):
        capacity.initialize_dispatch_history(
            history_path, plan=plan, _fault_at=fault_at
        )
    recovered = capacity.initialize_dispatch_history(history_path, plan=plan)

    assert recovered["initialization"] in {"pass", "already_initialized_pristine"}
    history = capacity.load_bound_dispatch_history(history_path, plan=plan)
    capacity._validate_dispatch_history_chain(
        history, plan=plan, workload=_production_workload()
    )
    assert history.events == ()


def test_initialization_rejects_evidence_root_without_durable_intent(
    tmp_path: Path,
) -> None:
    plan = _anchored_plan(tmp_path)
    contract = plan["dispatch_history_contract"]
    Path(contract["interruption_evidence_root"]).mkdir(parents=True)

    with pytest.raises(capacity.CapacityPreflightError, match="without its intent"):
        capacity.initialize_dispatch_history(
            Path(contract["required_path"]), plan=plan
        )


@pytest.mark.parametrize(
    "fault_at",
    [
        "after_append_intent",
        "after_partial_history",
        "after_history_fsync",
        "after_anchor_receipt",
        "after_append_intent_clear",
    ],
)
def test_append_fault_recovery_commits_once_and_forbids_redispatch(
    tmp_path: Path, fault_at: str
) -> None:
    plan = _anchored_plan(tmp_path)
    workload = _production_workload()
    contract = plan["dispatch_history_contract"]
    history_path = Path(contract["required_path"])
    capacity.initialize_dispatch_history(history_path, plan=plan)

    with pytest.raises(capacity.InjectedCapacityFault, match=fault_at):
        capacity.append_dispatch_history_event(
            history_path,
            event_type="dispatch_started",
            attempt_id="attempt-0001",
            plan=plan,
            workload=workload,
            recorded_at_utc="2026-08-29T12:00:00Z",
            _fault_at=fault_at,
        )
    with pytest.raises(
        capacity.CapacityPreflightError,
        match="recovered a committed|state does not permit",
    ):
        capacity.append_dispatch_history_event(
            history_path,
            event_type="dispatch_started",
            attempt_id="attempt-0001",
            plan=plan,
            workload=workload,
            recorded_at_utc="2026-08-29T12:00:00Z",
        )

    recovered_history = capacity.load_bound_dispatch_history(history_path, plan=plan)
    capacity._validate_dispatch_history_chain(
        recovered_history, plan=plan, workload=workload
    )
    assert [event["event"] for event in recovered_history.events] == [
        "dispatch_started"
    ]
    evidence_path = Path(contract["interruption_evidence_root"]) / "writer-crash.log"
    evidence_path.write_bytes(b"writer process ended before append acknowledgement")
    capacity.append_dispatch_history_event(
        history_path,
        event_type="attempt_interrupted",
        attempt_id="attempt-0001",
        plan=plan,
        workload=workload,
        recorded_at_utc="2026-08-29T12:01:00Z",
        interruption_evidence_path=evidence_path,
        interruption_summary="append process ended before acknowledgement",
        verified_by="test operator",
    )
    terminal = capacity.load_bound_dispatch_history(history_path, plan=plan)
    assert [event["event"] for event in terminal.events] == [
        "dispatch_started",
        "attempt_interrupted",
    ]


def test_append_api_binds_interruption_evidence_bytes_and_retained_receipts(
    tmp_path: Path,
) -> None:
    plan = _anchored_plan(tmp_path)
    workload = _production_workload()
    history_path = Path(plan["dispatch_history_contract"]["required_path"])
    capacity.initialize_dispatch_history(history_path, plan=plan)
    capacity.append_dispatch_history_event(
        history_path,
        event_type="dispatch_started",
        attempt_id="attempt-0001",
        plan=plan,
        workload=workload,
        recorded_at_utc="2026-08-29T12:00:00Z",
    )
    evidence_path = (
        Path(plan["dispatch_history_contract"]["interruption_evidence_root"])
        / "interruption.log"
    )
    evidence_path.write_bytes(b"verified host interruption evidence")
    capacity.append_dispatch_history_event(
        history_path,
        event_type="attempt_interrupted",
        attempt_id="attempt-0001",
        plan=plan,
        workload=workload,
        recorded_at_utc="2026-08-29T12:01:00Z",
        interruption_evidence_path=evidence_path,
        interruption_summary="host process was externally terminated",
        verified_by="test operator",
    )
    history = capacity.load_bound_dispatch_history(history_path, plan=plan)

    assert (
        capacity._permitted_materialization_cohort(
            history, plan=plan, workload=workload
        )
        == 2
    )
    assert len(list(Path(plan["dispatch_history_contract"]["anchor_directory"]).iterdir())) == 2

    evidence_path.write_bytes(b"x" * len(b"verified host interruption evidence"))
    replaced = capacity.load_bound_dispatch_history(history_path, plan=plan)
    with pytest.raises(capacity.CapacityPreflightError, match="evidence hash mismatch"):
        capacity._validate_dispatch_history_chain(
            replaced, plan=plan, workload=workload
        )


def test_retained_receipt_rejects_fabricated_history_replacement(tmp_path: Path) -> None:
    plan = _anchored_plan(tmp_path)
    workload = _production_workload()
    history_path = Path(plan["dispatch_history_contract"]["required_path"])
    capacity.initialize_dispatch_history(history_path, plan=plan)
    capacity.append_dispatch_history_event(
        history_path,
        event_type="dispatch_started",
        attempt_id="attempt-0001",
        plan=plan,
        workload=workload,
        recorded_at_utc="2026-08-29T12:00:00Z",
    )
    original = capacity.load_bound_dispatch_history(history_path, plan=plan)
    fabricated = dict(original.events[0])
    fabricated["attempt_id"] = "attempt-fabricated"
    fabricated["event_hash"] = capacity.dispatch_history_event_hash(fabricated)
    _write_text(history_path, capacity.canonical_json(fabricated) + "\n")

    replacement = capacity.load_bound_dispatch_history(history_path, plan=plan)
    with pytest.raises(capacity.CapacityPreflightError, match="anchor is unavailable"):
        capacity._validate_dispatch_history_chain(
            replacement, plan=plan, workload=workload
        )


@pytest.mark.parametrize(
    "argv",
    [
        ["--check", "--initialize-dispatch-history"],
        ["--check", "--append-dispatch-event", "dispatch_started"],
        ["--check", "--materialize-dir", "unused"],
        ["--check", "--result", "unused.json"],
    ],
)
def test_check_mode_is_mutually_exclusive_with_every_other_action(
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        capacity.main(argv)

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--check", "--dispatch-history", "ignored.jsonl"],
        ["--check", "--attempt-id", "attempt-0001"],
        ["--check", "--as-of-utc", "2026-08-29T12:00:00Z"],
        [
            "--initialize-dispatch-history",
            "--dispatch-history",
            "history.jsonl",
            "--verified-by",
            "ignored operator",
        ],
        [
            "--materialize-dir",
            "unused",
            "--dispatch-history",
            "history.jsonl",
            "--recorded-at-utc",
            "2026-08-29T12:00:00Z",
        ],
    ],
)
def test_cli_rejects_options_that_are_incompatible_with_the_selected_mode(
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        capacity.main(argv)

    assert exc_info.value.code == 2


def test_tracked_plan_freezes_two_disjoint_cohorts_and_no_authority() -> None:
    plan = json.loads(capacity.PLAN_PATH_DEFAULT.read_text(encoding="utf-8"))

    assert plan["execution_authorized"] is False
    assert plan["provider_calls_authorized"] is False
    assert plan["main_spend_authorized"] is False
    assert plan["workload"]["cohort_count"] == 2
    assert plan["workload"]["selection_count_per_cohort"] == 180
    assert plan["workload"]["wave_size"] == 60
    assert plan["workload"]["wave_sizes_per_cohort"] == [60, 60, 60]
    assert plan["workload"]["byte_new_eligible_count"] == 368
    assert plan["workload"]["unused_byte_new_reserve_count"] == 8
    assert plan["reviewer_configuration"]["wave_pending_payload_limit"] == 64
    assert plan["reviewer_configuration"]["actual_capacity_wave_size"] == 60
    assert Path(plan["reviewer_configuration"]["reviewer_cli_resolved_path"]).is_absolute()
    assert plan["dispatch_history_contract"]["anchor_validation_required"] is True
    assert "two disjoint 180-packet cohorts" in plan["measurement_contract"][
        "cohort_scope"
    ]
    assert "completed failure is terminal" in plan["retry_policy"][
        "completed_failure"
    ]
    assert plan["capacity_thresholds"] == capacity._expected_thresholds()
    assert any(
        "not tamper-proof" in claim for claim in plan["non_claims"]
    )
    assert any(
        "wrapper hash alone is not an attestation" in claim
        for claim in plan["non_claims"]
    )
    capacity._validate_frozen_plan(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("measurement_contract", {}),
        ("retry_policy", {}),
        ("execution_authorized", True),
        ("provider_calls_authorized", True),
    ],
)
def test_canonical_plan_hash_freezes_all_operative_fields(field: str, value: Any) -> None:
    plan = json.loads(capacity.PLAN_PATH_DEFAULT.read_text(encoding="utf-8"))
    plan[field] = value

    with pytest.raises(capacity.CapacityPreflightError, match="canonical hash mismatch"):
        capacity._validate_frozen_plan(plan)


def test_main_spend_authority_is_explicitly_rejected(monkeypatch) -> None:
    plan = json.loads(capacity.PLAN_PATH_DEFAULT.read_text(encoding="utf-8"))
    plan["main_spend_authorized"] = True
    monkeypatch.setattr(
        capacity, "EXPECTED_PLAN_CANONICAL_SHA256", capacity.canonical_sha256(plan)
    )

    with pytest.raises(capacity.CapacityPreflightError, match="must not authorize main spend"):
        capacity._validate_frozen_plan(plan)
