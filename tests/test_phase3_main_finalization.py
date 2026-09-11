"""Focused fail-closed tests for Phase 3 main finalization."""
from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from rejudge import api_client, judge_loop
from rejudge import phase2_canary_execute, phase2_canary_live
from rejudge import phase3_main_finalization as finalization
from rejudge import phase3_main_provider_provenance as provider_provenance
from rejudge import phase3_main_reviewer_commit as reviewer_commit
from rejudge import phase3_main_reviewer_provenance as reviewer_provenance
from rejudge import phase3_main_manifest, phase3_main_runner
from rejudge import phase3_main_runtime_policies
from rejudge import phase3_main_transcript_provenance, phase3_runner, phase3_v3_live
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_call_cache import request_fingerprint
from rejudge.phase2_canary_execute import CellContext, execute_cell
from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
from rejudge.parsers import parse_both
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD,
    RequestJournal,
    journal_key,
)
from scripts import codex_reviewer_batch
from scripts import phase3_main_review_capacity_preflight as capacity_preflight


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SHA256 = "a" * 64
RUN_ID = "phase3-main-finalization-test"
JOURNAL_IDENTITY = f"{RUN_ID}:{MANIFEST_SHA256}:test-root"
PRIOR_RECONCILED_USD = "0.30985289"
STAGE_CAP_USD = "60.00"
PROTOCOL_PATH = REPO_ROOT / "rejudge" / "phase3_protocol_v3_r6.json"
PROMPT_BUNDLE_PATH = REPO_ROOT / "rejudge" / "phase2_prompt_bundle.json"
ROLE_LIMITS_PATH = (
    REPO_ROOT / "rejudge" / "phase3_v3_role_limits_r10_2026-08-28.json")
PROTOCOL = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
PROMPT_BUNDLE = json.loads(PROMPT_BUNDLE_PATH.read_text(encoding="utf-8"))
ROLE_LIMITS = json.loads(ROLE_LIMITS_PATH.read_text(encoding="utf-8"))
ORACLE_MODEL = str(PROTOCOL["roster"]["oracle"])
REVIEWER_MODEL = "gpt-5.6-sol"
REVIEWER_REASONING_EFFORT = "high"
REVIEWER_CONCURRENCY = 12
FIXTURE_REVIEWER_CLI_VERSION = "fixture-version"
FIXTURE_HOST_IDENTITY = codex_reviewer_batch._host_identity()  # noqa: SLF001
AUTHORIZATION_APPROVED_AT_UTC = "2026-08-29T12:00:00Z"
AUTHORIZATION_VALID_UNTIL_UTC = "2026-08-30T12:00:00Z"
FIXTURE_AUTHORIZATION = {
    "approved_at_utc": AUTHORIZATION_APPROVED_AT_UTC,
    "valid_until_utc": AUTHORIZATION_VALID_UNTIL_UTC,
}
FIXTURE_AUTHORIZATION_RAW = (
    json.dumps(FIXTURE_AUTHORIZATION, sort_keys=True) + "\n").encode("utf-8")
FIXTURE_AUTHORIZATION_SIGNATURE_RAW = b"fixture authorization signature\n"
AUTHORIZATION_SHA256 = codex_reviewer_batch._canonical_sha256(
    FIXTURE_AUTHORIZATION)
AUTHORIZATION_RAW_SHA256 = hashlib.sha256(
    FIXTURE_AUTHORIZATION_RAW).hexdigest()
AUTHORIZATION_SIGNATURE_RAW_SHA256 = hashlib.sha256(
    FIXTURE_AUTHORIZATION_SIGNATURE_RAW).hexdigest()
RAW_VERDICT = "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: provider row"


class _CaptureClient:
    dry_run = False

    def __init__(
        self,
        *,
        query_responses: tuple[str, ...] = (),
        checker_response: str = "allow",
        verdict_response: str = RAW_VERDICT,
    ) -> None:
        self.calls: list[dict] = []
        self.query_responses = list(query_responses)
        self.checker_response = checker_response
        self.verdict_response = verdict_response

    def complete(
        self,
        messages,
        model,
        temperature,
        seed,
        max_tokens,
        kind="verdict",
        *,
        request_metadata=None,
    ) -> str:
        role = dict(request_metadata or {}).get("call_role")
        if role == finalization.JUDGE_QUERY_ROLE:
            if not self.query_responses:
                raise AssertionError("fixture exhausted its scripted query responses")
            response = self.query_responses.pop(0)
        elif role == finalization.QUERY_CHECKER_ROLE:
            response = self.checker_response
        elif role == finalization.ORACLE_VERIFICATION_ROLE:
            response = "YES"
        elif role == finalization.JUDGE_VERDICT_ROLE:
            response = self.verdict_response
        else:
            raise AssertionError(f"unexpected fixture call role {role!r}")
        self.calls.append({
            "messages": copy.deepcopy(messages),
            "model": model,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_tokens,
            "kind": kind,
            "request_metadata": dict(request_metadata or {}),
            "response": response,
        })
        return response


def _unexpected_reviewer(_query: str, _candidate_a: str, _candidate_b: str) -> str:
    raise AssertionError("fixture must resolve reviewer evidence from its decision store")


@pytest.fixture(scope="module")
def inventory() -> phase3_main_runner.MainInventory:
    return phase3_main_runner.build_canonical_main_inventory(REPO_ROOT)


def _context_blocklist(
    inventory: phase3_main_runner.MainInventory,
    excluded_cells=(),
) -> dict:
    excluded = []
    counts: Counter[tuple[str, str]] = Counter()
    for cell in excluded_cells:
        excluded.append({
            field: cell[field]
            for field in (
                "cell_key",
                "judge_model",
                "question_id",
                "debater_model",
                "transcript_index",
                "condition",
            )
        })
        counts[(str(cell["judge_model"]), str(cell["condition"]))] += 1
    namespace = str(inventory.cells[0]["cell_key"]).split(":", 1)[0]
    return {
        "scope": "main",
        "cell_key_namespace": namespace,
        "excluded": excluded,
        "excluded_count": len(excluded),
        "counts_by_judge_and_condition": [
            {"judge_model": judge, "condition": condition, "count": count}
            for (judge, condition), count in sorted(counts.items())
        ],
    }


def _terminal_record(cell_key: str, reason: str = finalization.TERMINAL_REASON) -> dict:
    return {"cell_key": cell_key, "reason": reason}


def _write_result_store(path: Path, rows_to_write) -> None:
    previous = "genesis"
    rows: list[str] = []
    for sequence, (cell_key, result) in enumerate(rows_to_write):
        row = {
            "cell_key": cell_key,
            "result": result,
            "sequence": sequence,
            "prev_event_hash": previous,
        }
        row["event_hash"] = CellResultStore._row_hash(row)
        previous = row["event_hash"]
        rows.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def _main_journal_identity(root: Path) -> str:
    canonical = root.resolve().as_posix()
    root_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{RUN_ID}:{MANIFEST_SHA256}:{root_sha256}"


def _transcript_result(cell: dict) -> dict:
    return {
        "cell_key": cell["cell_key"],
        "question_id": cell["question_id"],
        "transcript_index": cell["transcript_index"],
        "debater_model": cell["debater_model"],
        "world": "fixture-world",
        "question": "Fixture question?",
        "correct_answer": "fixture correct",
        "wrong_answer": "fixture wrong",
        "debate_transcript": [],
        "dry_run": False,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capacity_workload() -> capacity_preflight.DerivedWorkload:
    variants = tuple(
        capacity_preflight.VariantPacket(
            rank_sha256=hashlib.sha256(f"rank-{index}".encode()).hexdigest(),
            source_payload_sha256=hashlib.sha256(
                f"source-{index}".encode()).hexdigest(),
            source_prompt_sha256=hashlib.sha256(
                f"old-{index}".encode()).hexdigest(),
            variant_payload_sha256=hashlib.sha256(
                f"payload-{index}".encode()).hexdigest(),
            variant_prompt_sha256=hashlib.sha256(
                f"prompt-{index}".encode()).hexdigest(),
            prompt=f"capacity packet {index}",
        )
        for index in range(360)
    )
    cohorts = (variants[:180], variants[180:])
    summary = {
        "wave_size": 60,
        "cohort_records_canonical_sha256s": [
            capacity_preflight.canonical_sha256(
                [item.variant_payload_sha256 for item in cohort])
            for cohort in cohorts
        ],
        "cohort_variant_prompts_canonical_sha256s": [
            capacity_preflight.canonical_sha256(
                [item.variant_prompt_sha256 for item in cohort])
            for cohort in cohorts
        ],
    }
    return capacity_preflight.DerivedWorkload(
        selected=cohorts[0],
        retry_selected=cohorts[1],
        eligible=variants,
        collision_source_payloads=(),
        summary=summary,
    )


@pytest.fixture(autouse=True)
def _stub_capacity_source_validation(monkeypatch):
    workload = _capacity_workload()
    monkeypatch.setattr(
        capacity_preflight,
        "collect_source_snapshot",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        capacity_preflight,
        "derive_workload",
        lambda _snapshot: workload,
    )
    monkeypatch.setattr(
        capacity_preflight,
        "validate_plan",
        lambda _plan, **_kwargs: {"validation": "pass"},
    )


def _capacity_history_event(
    *,
    plan: dict,
    workload: capacity_preflight.DerivedWorkload,
    sequence: int,
    event: str,
    previous_hash: str,
) -> dict:
    row = {
        "schema_version": capacity_preflight.DISPATCH_HISTORY_SCHEMA_VERSION,
        "sequence": sequence,
        "event": event,
        "attempt_number": 1,
        "cohort_number": 1,
        "attempt_id": "attempt-0001",
        "plan_canonical_sha256": capacity_preflight.canonical_sha256(plan),
        "cohort_records_canonical_sha256": workload.summary[
            "cohort_records_canonical_sha256s"][0],
        "cohort_variant_prompts_canonical_sha256": workload.summary[
            "cohort_variant_prompts_canonical_sha256s"][0],
        "environmental_interruption_evidence": None,
        "recorded_at_utc": f"2026-08-29T12:{sequence:02d}:00Z",
        "prev_event_hash": previous_hash,
        "event_hash": "",
    }
    row["event_hash"] = capacity_preflight.dispatch_history_event_hash(row)
    return row


def _write_capacity_evidence(tmp_path: Path, plan: dict) -> dict:
    workload = _capacity_workload()
    history_rows = []
    previous_hash = "genesis"
    for sequence, event in enumerate(("dispatch_started", "attempt_completed_pass")):
        row = _capacity_history_event(
            plan=plan,
            workload=workload,
            sequence=sequence,
            event=event,
            previous_hash=previous_hash,
        )
        history_rows.append(row)
        previous_hash = str(row["event_hash"])
    history_raw = "".join(
        json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        for row in history_rows
    ).encode("utf-8")
    history_path = (tmp_path / "fixture_capacity_dispatch_history.jsonl").resolve()
    history_path.write_bytes(history_raw)

    configuration = plan["reviewer_configuration"]
    environment = {
        "reviewer_cli_resolved_path": configuration["reviewer_cli_resolved_path"],
        "reviewer_cli_wrapper_raw_sha256": configuration[
            "reviewer_cli_wrapper_raw_sha256"],
        "reviewer_cli_wrapper_byte_count": configuration[
            "reviewer_cli_wrapper_byte_count"],
        "reviewer_cli_version": FIXTURE_REVIEWER_CLI_VERSION,
        "host_identity": FIXTURE_HOST_IDENTITY,
    }
    waves = []
    for wave_offset in range(3):
        items = workload.selected[wave_offset * 60:(wave_offset + 1) * 60]
        start = 1000.0 + wave_offset * 120.0
        waves.append({
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
            "monotonic_completed_seconds": start + 100.0,
            "elapsed_monotonic_seconds": 100.0,
            "expected_payload_sha256s": [
                item.variant_payload_sha256 for item in items],
            "results": [{
                "payload_sha256": item.variant_payload_sha256,
                "prompt_sha256": item.variant_prompt_sha256,
                "ok": True,
                "error": None,
                "commands": [],
                "tool_uses": 0,
                "raw_output": (
                    "LABEL: ALLOW\nCLAUSE: Allowed\n"
                    "RATIONALE: The claim is atomic."),
            } for item in items],
        })
    result = {
        "schema_version": capacity_preflight.RESULT_SCHEMA_VERSION,
        "attempt_number": 1,
        "cohort_number": 1,
        "attempt_id": "attempt-0001",
        "attempt_status": "complete",
        "interrupted": False,
        "dispatch_history_raw_sha256": hashlib.sha256(history_raw).hexdigest(),
        "plan_canonical_sha256": capacity_preflight.canonical_sha256(plan),
        "cohort_records_canonical_sha256": workload.summary[
            "cohort_records_canonical_sha256s"][0],
        "reviewer_configuration": configuration,
        "reviewer_configuration_canonical_sha256": (
            capacity_preflight.canonical_sha256(configuration)),
        "measurement_environment": environment,
        "measurement_environment_canonical_sha256": (
            capacity_preflight.canonical_sha256(environment)),
        "started_at_utc": "2026-08-29T12:10:00Z",
        "completed_at_utc": "2026-08-29T12:30:00Z",
        "monotonic_started_seconds": 1000.0,
        "monotonic_completed_seconds": 1360.0,
        "elapsed_monotonic_seconds": 360.0,
        "waves": waves,
    }
    result_path = (tmp_path / "fixture_capacity_result.json").resolve()
    _write_json(result_path, result)
    return {
        "capacity_result_path": result_path,
        "expected_capacity_result_raw_sha256": _raw_sha256(result_path),
        "capacity_dispatch_history_path": history_path,
        "expected_capacity_dispatch_history_raw_sha256": _raw_sha256(history_path),
    }


def _write_provider_inputs(
    tmp_path: Path,
    inventory: phase3_main_runner.MainInventory,
) -> tuple[dict[str, Path], dict[str, str]]:
    entries = []
    for cell_value in inventory.transcript_cells:
        cell = dict(cell_value)
        payload = _transcript_result(cell)
        payload.pop("cell_key")
        entries.append({
            "debater_model": cell["debater_model"],
            "question_id": cell["question_id"],
            "transcript_index": cell["transcript_index"],
            "transcript_payload": payload,
            "transcript_sha256": finalization.canonical_sha256(payload),
        })
    bundle = {
        "schema_version": "phase3_transcript_bundle_v1",
        "bundle": "main",
        "expected_transcript_count": 492,
        "actual_transcript_count": 492,
        "transcripts": entries,
    }
    bundle_path = tmp_path / "fixture_main_transcript_bundle.json"
    _write_json(bundle_path, bundle)
    verification = {
        "schema_version": "phase3_transcript_verification_v1",
        "bundle_canonical_sha256": {
            "main_bundle": finalization.canonical_sha256(bundle),
        },
    }
    verification_path = tmp_path / "fixture_transcript_verification.json"
    _write_json(verification_path, verification)
    paths = {
        "protocol": PROTOCOL_PATH.resolve(),
        "prompt_bundle": PROMPT_BUNDLE_PATH.resolve(),
        "role_limits": ROLE_LIMITS_PATH.resolve(),
        "main_transcript_bundle": bundle_path.resolve(),
        "transcript_verification": verification_path.resolve(),
    }
    return paths, {name: _raw_sha256(path) for name, path in paths.items()}


def _write_reviewer_inputs(
    tmp_path: Path,
) -> tuple[dict[str, Path], dict[str, str], str, dict]:
    frozen_prompt = "Classify this isolated fixture payload under the frozen contract."
    reviewer_prompt = {
        "prompt": frozen_prompt,
        "prompt_sha256": hashlib.sha256(
            frozen_prompt.encode("utf-8")).hexdigest(),
    }
    reviewer_prompt_path = tmp_path / "fixture_reviewer_prompt.json"
    _write_json(reviewer_prompt_path, reviewer_prompt)
    reviewer_failure_policy = {
        "schema_version": "phase3_main_reviewer_failure_policy_v1",
        "stage": "main",
        "status": "offline_contract_pending_exact_manifest_authorization",
        "reviewer_prompt_sha256": reviewer_prompt["prompt_sha256"],
        "overrides_reviewer_prompt_field": "failure_rule",
        "parse_failure": "commit_malformed_non_allow",
        "tool_use": "commit_evidenced_reviewer_error_non_allow",
        "timeout_or_unavailability": (
            "abort_wave_without_decision_and_void_no_resume_identity"),
        "execution_authorized": False,
        "provider_calls_authorized": False,
    }
    reviewer_failure_policy_path = (
        tmp_path / "fixture_reviewer_failure_policy.json")
    _write_json(reviewer_failure_policy_path, reviewer_failure_policy)
    reviewer_cli_path = (tmp_path / "fixture_codex.cmd").resolve()
    reviewer_cli_path.write_bytes(
        f"@echo {FIXTURE_REVIEWER_CLI_VERSION}\r\n".encode("utf-8"))
    reviewer_cli_raw = reviewer_cli_path.read_bytes()
    workload = _capacity_workload()
    capacity_plan = {
        "schema_version": "phase3_main_review_capacity_preflight_plan_v1",
        "reviewer_configuration": {
            "model": REVIEWER_MODEL,
            "reasoning_effort": REVIEWER_REASONING_EFFORT,
            "reviewer_cli_binary": "codex.cmd",
            "concurrency": REVIEWER_CONCURRENCY,
            "reviewer_cli_resolved_path": reviewer_cli_path.as_posix(),
            "reviewer_cli_wrapper_raw_sha256": hashlib.sha256(
                reviewer_cli_raw).hexdigest(),
            "reviewer_cli_wrapper_byte_count": len(reviewer_cli_raw),
            "fresh_ephemeral_context_per_packet": True,
            "tool_use_permitted": False,
            "actual_capacity_wave_size": 60,
            "wave_pending_payload_limit": 64,
        },
        "workload": dict(workload.summary),
        "capacity_thresholds": capacity_preflight._expected_thresholds(),
        "dispatch_history_contract": {
            "required_path": str(
                (tmp_path / "fixture_capacity_dispatch_history.jsonl").resolve()),
            "anchor_directory": str((tmp_path / "fixture_capacity_anchors").resolve()),
            "interruption_evidence_root": str(
                (tmp_path / "fixture_capacity_interruptions").resolve()),
            "required_initial_raw_sha256": hashlib.sha256(b"").hexdigest(),
            "anchor_validation_required": False,
        },
        "source_locations": {
            "sealed_archive": str((tmp_path / "fixture_capacity_source").resolve()),
            "finalization_record": str(
                (tmp_path / "fixture_capacity_source_finalization.json").resolve()),
        },
        "validity": {"valid_for_hours": 24},
    }
    capacity_plan_path = tmp_path / "fixture_reviewer_capacity_plan.json"
    _write_json(capacity_plan_path, capacity_plan)
    paths = {
        "reviewer_prompt": reviewer_prompt_path.resolve(),
        "reviewer_failure_policy": reviewer_failure_policy_path.resolve(),
        "capacity_plan": capacity_plan_path.resolve(),
    }
    return (
        paths,
        {name: _raw_sha256(path) for name, path in paths.items()},
        frozen_prompt,
        _write_capacity_evidence(tmp_path, capacity_plan),
    )


def _reviewer_event_stream() -> bytes:
    events = [
        {"type": "thread.started", "thread_id": "fixture-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message"}},
        {"type": "turn.completed"},
    ]
    return "".join(json.dumps(event) + "\n" for event in events).encode("utf-8")


def _guard_artifact_binding(path: Path) -> dict:
    resolved = path.resolve()
    raw = resolved.read_bytes()
    return {
        "path": resolved.as_posix(),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
    }


def _fixture_reviewer_receipt(
    *,
    packet: Path,
    raw_output: str,
    capacity_plan: dict,
    dispatch_guard_path: Path,
    dispatch_guard_raw_sha256: str,
) -> dict:
    configuration = capacity_plan["reviewer_configuration"]
    cli_path = str(configuration["reviewer_cli_resolved_path"])
    workdir = packet.parent / "deleted-isolated-workdir"
    output_file = workdir / "ruling.txt"
    runner_path, runner_sha, runner_bytes = (
        codex_reviewer_batch._batch_runner_identity())
    invocation = {
        "argv": [
            cli_path,
            "exec",
            "-m",
            REVIEWER_MODEL,
            "-c",
            f'model_reasoning_effort="{REVIEWER_REASONING_EFFORT}"',
            "-C",
            str(workdir),
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "-s",
            "read-only",
            "--json",
            "-o",
            str(output_file),
            "-",
        ],
        "model_requested": REVIEWER_MODEL,
        "reasoning_effort_requested": REVIEWER_REASONING_EFFORT,
        "batch_concurrency": REVIEWER_CONCURRENCY,
        "codex_cli_argument": cli_path,
        "codex_cli_resolved_path": cli_path,
        "codex_cli_wrapper_raw_sha256": configuration[
            "reviewer_cli_wrapper_raw_sha256"],
        "codex_cli_wrapper_byte_count": configuration[
            "reviewer_cli_wrapper_byte_count"],
        "codex_cli_version": FIXTURE_REVIEWER_CLI_VERSION,
        "working_directory": str(workdir),
        "output_file": str(output_file),
        "sandbox_mode": "read-only",
        "ephemeral": True,
        "ignore_user_config": True,
        "ignore_rules": True,
        "json_event_stream": True,
        "python_executable": "python.exe",
        "python_implementation": "CPython",
        "python_version": "3.12.0",
        "host_identity": FIXTURE_HOST_IDENTITY,
        "batch_runner_path": runner_path,
        "batch_runner_raw_sha256": runner_sha,
        "batch_runner_byte_count": runner_bytes,
    }
    normalized = raw_output.strip().encode("utf-8")
    outcome = {
        "authorization_deadline_utc": "2026-08-30T12:00:00+00:00",
        "deadline_active_before_dispatch": True,
        "dispatch_attempted": True,
        "started_at_utc": "2026-08-29T13:00:00+00:00",
        "completed_at_utc": "2026-08-29T13:00:01+00:00",
        "timed_out": False,
        "process_exit_code": 0,
        "result_ok": True,
        "error": None,
        "commands": [],
        "event_stream_errors": [],
        "normalized_ruling_raw_sha256": hashlib.sha256(normalized).hexdigest(),
        "normalized_ruling_byte_count": len(normalized),
    }
    checked_at = phase3_main_manifest._utc(  # noqa: SLF001
        outcome["started_at_utc"], "fixture reviewer dispatch")
    dispatch_guard_evidence, guard, verified_prompt = (
        codex_reviewer_batch._evaluate_dispatch_guard_snapshot(  # noqa: SLF001
            dispatch_guard_path,
            dispatch_guard_raw_sha256,
            codex=cli_path,
            model=REVIEWER_MODEL,
            effort=REVIEWER_REASONING_EFFORT,
            concurrency=REVIEWER_CONCURRENCY,
            packet_directory=packet.parent,
            output_path=packet.parent / "rulings.jsonl",
            selected_packet=packet,
            checked_at=checked_at,
        )
    )
    assert dispatch_guard_evidence["verified"] is True
    assert guard is not None
    assert verified_prompt == packet.read_bytes()
    dispatch_guard_evidence["reservation"] = (
        codex_reviewer_batch._reserve_guarded_dispatch(  # noqa: SLF001
            packet=packet,
            prompt_bytes=verified_prompt,
            guard=guard,
            guard_raw_sha256=dispatch_guard_raw_sha256,
            checked_at_utc=checked_at.isoformat(),
        )
    )
    dispatch_guard_evidence["released_at_utc"] = checked_at.isoformat()
    return codex_reviewer_batch._persist_invocation_evidence(
        packet=packet,
        prompt_bytes=packet.read_bytes(),
        invocation=invocation,
        outcome=outcome,
        event_stream_raw=_reviewer_event_stream(),
        stderr_raw=b"",
        ruling_raw=(raw_output + "\n").encode("utf-8"),
        dispatch_guard=dispatch_guard_evidence,
    )


def _write_reviewer_artifacts(
    *,
    tmp_path: Path,
    reviewer_payloads: list[dict],
    decision_store_path: Path,
    reviewer_input_paths: dict[str, Path],
    reviewer_input_raw_sha256s: dict[str, str],
    capacity_evidence_inputs: dict,
    frozen_prompt: str,
) -> tuple[Path, Path, Path]:
    reviewer_index_path = (
        tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["reviewer_index"])
    reviewer_worklist_path = (
        tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["reviewer_worklist"])
    review_packets_root_path = (
        tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["review_packets_root"])
    review_packets_root_path.mkdir()
    reviewer_index_path.write_bytes(b"")
    if not reviewer_payloads:
        phase2_canary_live.export_reviewer_worklist(
            [], frozen_prompt, reviewer_worklist_path)
        return reviewer_index_path, reviewer_worklist_path, review_packets_root_path

    packet_dir = review_packets_root_path / "wave-001"
    packet_dir.mkdir()
    worklist = phase2_canary_live.export_reviewer_worklist(
        reviewer_payloads, frozen_prompt, packet_dir / "WORKLIST.json")
    index_items = []
    ruling_rows = []
    capacity_plan = json.loads(
        reviewer_input_paths["capacity_plan"].read_text(encoding="utf-8"))
    authorization_path = (tmp_path / "fixture_authorization.json").resolve()
    authorization_path.write_bytes(FIXTURE_AUTHORIZATION_RAW)
    authorization_signature_path = authorization_path.with_name(
        f"{authorization_path.name}.sig")
    authorization_signature_path.write_bytes(FIXTURE_AUTHORIZATION_SIGNATURE_RAW)
    capacity_result_path = Path(
        capacity_evidence_inputs["capacity_result_path"]).resolve()
    capacity_dispatch_history_path = Path(
        capacity_evidence_inputs["capacity_dispatch_history_path"]).resolve()
    reviewer_cli_path = Path(
        capacity_plan["reviewer_configuration"][
            "reviewer_cli_resolved_path"]
    ).resolve()
    packet_bindings = []
    for position, item in enumerate(worklist["items"], 1):
        packet_name = f"{position:05d}_{item['payload_sha256'][:12]}.txt"
        packet_path = packet_dir / packet_name
        prompt_raw = item["subagent_prompt"].encode("utf-8")
        packet_path.write_bytes(prompt_raw)
        index_items.append({
            "n": position,
            "file": packet_name,
            "payload_sha256": item["payload_sha256"],
            "prompt_sha256": item["subagent_prompt_sha256"],
        })
        packet_bindings.append({
            "file": packet_name,
            "payload_sha256": item["payload_sha256"],
            "prompt_sha256": item["subagent_prompt_sha256"],
            "byte_count": len(prompt_raw),
        })
    index_path = packet_dir / "INDEX.json"
    _write_json(index_path, {"count": len(index_items), "items": index_items})
    rulings_path = packet_dir / "rulings.jsonl"
    rulings_path.write_bytes(b"")
    batch_runner_path = Path(
        codex_reviewer_batch._batch_runner_identity()[0])  # noqa: SLF001
    dispatch_guard = {
        "schema_version": codex_reviewer_batch.DISPATCH_GUARD_SCHEMA,
        "run_id": RUN_ID,
        "manifest_canonical_sha256": MANIFEST_SHA256,
        "authorization_canonical_sha256": AUTHORIZATION_SHA256,
        "authorization_approved_at_utc": "2026-08-29T12:00:00+00:00",
        "authorization_valid_until_utc": "2026-08-30T12:00:00+00:00",
        "capacity_completed_at_utc": "2026-08-29T12:30:00+00:00",
        "capacity_expires_at_utc": "2026-08-30T12:30:00+00:00",
        "reviewer_model": REVIEWER_MODEL,
        "reviewer_reasoning_effort": REVIEWER_REASONING_EFFORT,
        "reviewer_concurrency": REVIEWER_CONCURRENCY,
        "reviewer_cli_version": FIXTURE_REVIEWER_CLI_VERSION,
        "capacity_host_identity": FIXTURE_HOST_IDENTITY,
        "packet_directory": packet_dir.resolve().as_posix(),
        "output_path": rulings_path.resolve().as_posix(),
        "packet_bindings": packet_bindings,
        "artifact_bindings": {
            "authorization": _guard_artifact_binding(authorization_path),
            "authorization_signature": _guard_artifact_binding(
                authorization_signature_path),
            "capacity_plan": _guard_artifact_binding(
                reviewer_input_paths["capacity_plan"]),
            "capacity_result": _guard_artifact_binding(capacity_result_path),
            "capacity_dispatch_history": _guard_artifact_binding(
                capacity_dispatch_history_path),
            "reviewer_cli_wrapper": _guard_artifact_binding(reviewer_cli_path),
            "packet_index": _guard_artifact_binding(index_path),
            "worklist_snapshot": _guard_artifact_binding(
                packet_dir / "WORKLIST.json"),
            "batch_runner": _guard_artifact_binding(batch_runner_path),
        },
    }
    dispatch_guard_path = (
        packet_dir / codex_reviewer_batch.DISPATCH_GUARD_FILENAME)
    dispatch_guard_raw = (
        json.dumps(
            dispatch_guard,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            indent=1,
        )
        + "\n"
    ).encode("utf-8")
    dispatch_guard_path.write_bytes(dispatch_guard_raw)
    dispatch_guard_raw_sha256 = hashlib.sha256(
        dispatch_guard_raw).hexdigest()
    for payload, item, index_item in zip(
        reviewer_payloads, worklist["items"], index_items,
    ):
        packet_path = packet_dir / str(index_item["file"])
        raw_output = str(payload["raw_output"])
        ruling_rows.append({
            "payload_sha256": item["payload_sha256"],
            "raw_output": raw_output,
            "prompt_sha256": item["subagent_prompt_sha256"],
            "tool_uses": 0,
            "evidence": _fixture_reviewer_receipt(
                packet=packet_path,
                raw_output=raw_output,
                capacity_plan=capacity_plan,
                dispatch_guard_path=dispatch_guard_path,
                dispatch_guard_raw_sha256=dispatch_guard_raw_sha256,
            ),
        })
    rulings_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ruling_rows),
        encoding="utf-8",
    )
    phase2_canary_live.export_reviewer_worklist(
        reviewer_payloads, frozen_prompt, reviewer_worklist_path)
    evidence_bindings = {
        "authorization_canonical_sha256": AUTHORIZATION_SHA256,
        "authorization_raw_sha256": AUTHORIZATION_RAW_SHA256,
        "authorization_signature_raw_sha256": AUTHORIZATION_SIGNATURE_RAW_SHA256,
        "capacity_plan_raw_sha256": reviewer_input_raw_sha256s["capacity_plan"],
        "worklist_snapshot_raw_sha256": _raw_sha256(
            packet_dir / "WORKLIST.json"),
        "packet_index_raw_sha256": _raw_sha256(index_path),
        "dispatch_guard_raw_sha256": dispatch_guard_raw_sha256,
        "rulings_raw_sha256": _raw_sha256(rulings_path),
    }
    run_lease_path = (tmp_path / "fixture_main_run.lock").resolve()
    transaction_id = reviewer_commit.derive_wave_commit_transaction_id(
        run_id=RUN_ID,
        manifest_canonical_sha256=MANIFEST_SHA256,
        wave=1,
        run_lease_path=run_lease_path,
        evidence_bindings=evidence_bindings,
    )
    wave_row = {
        "schema_version": reviewer_commit.REVIEWER_WAVE_SCHEMA,
        "run_id": RUN_ID,
        "manifest_canonical_sha256": MANIFEST_SHA256,
        **evidence_bindings,
        "wave": 1,
        "recorded_at_utc": "2026-08-29T13:00:02Z",
        "payload_count": len(reviewer_payloads),
        "packet_directory": packet_dir.resolve().as_posix(),
        "reviewer_model": REVIEWER_MODEL,
        "reviewer_reasoning_effort": REVIEWER_REASONING_EFFORT,
        "reviewer_concurrency": REVIEWER_CONCURRENCY,
        "reviewer_cli_resolved_path": capacity_plan[
            "reviewer_configuration"]["reviewer_cli_resolved_path"],
        "commit_counts": {
            "parsed": len(reviewer_payloads),
            "malformed": 0,
            "reviewer_error": 0,
        },
        "run_lease_path": run_lease_path.as_posix(),
        "wave_commit_transaction_id": transaction_id,
    }
    commit_entries = [
        {
            "payload_sha256": row["payload_sha256"],
            "raw_output": row["raw_output"],
            "prompt_sha256": row["prompt_sha256"],
        }
        for row in ruling_rows
    ]
    with phase3_v3_live.RunLease(run_lease_path) as held_run_lease:
        committed = reviewer_commit.commit_reviewer_wave(
            transaction_directory=packet_dir,
            decision_store_path=decision_store_path,
            reviewer_index_path=reviewer_index_path,
            run_id=RUN_ID,
            manifest_canonical_sha256=MANIFEST_SHA256,
            wave=1,
            run_lease_path=run_lease_path,
            held_run_lease=held_run_lease,
            worklist=worklist,
            entries=commit_entries,
            reviewer_index_row=wave_row,
        )
    assert committed.wave_commit_transaction_id == transaction_id
    assert committed.commit_counts == wave_row["commit_counts"]
    return reviewer_index_path, reviewer_worklist_path, review_packets_root_path


def _resolved_observed_cell(
    inventory: phase3_main_runner.MainInventory,
    observed_cell: dict,
):
    dependency_key = str(observed_cell["dependency_keys"][0])
    transcript_cell = next(
        dict(cell) for cell in inventory.transcript_cells
        if str(cell["cell_key"]) == dependency_key
    )
    resolved = phase3_runner.resolve_main_cells(
        [transcript_cell, observed_cell],
        protocol=PROTOCOL,
        bundle=PROMPT_BUNDLE,
    )
    return next(cell for cell in resolved if not cell.is_transcript)


def _execute_fixture_judgment(
    *,
    inventory: phase3_main_runner.MainInventory,
    observed_cell: dict,
    transcript: dict,
    decisions_path: Path,
    query_responses: tuple[str, ...],
    checker_response: str,
) -> tuple[dict, list[dict], list[dict]]:
    resolved_cell = _resolved_observed_cell(inventory, observed_cell)
    decision_store = DualGateDecisionStore(decisions_path)
    position_a_is_correct = phase2_canary_execute._polarity(resolved_cell)
    candidate_a, candidate_b, _debate = judge_loop._format_transcript(
        transcript, position_a_is_correct)
    reviewer_raw = (
        "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: focused fixture query")
    reviewer_payloads = []
    for raw_query in query_responses:
        if raw_query.strip() == "DONE":
            continue
        reviewer_payload = payload_hash(raw_query, candidate_a, candidate_b)
        reviewer_payloads.append({
            "payload_sha256": reviewer_payload,
            "query": raw_query,
            "candidate_a": candidate_a,
            "candidate_b": candidate_b,
            "raw_output": reviewer_raw,
        })
        if decision_store.get(reviewer_payload) is None:
            decision_store.commit(
                reviewer_payload,
                "ALLOW",
                "Allowed",
                "focused fixture query",
                reviewer_raw,
                "parsed",
            )
    client = _CaptureClient(
        query_responses=query_responses,
        checker_response=checker_response,
    )
    context = CellContext(
        client=client,
        protocol=copy.deepcopy(PROTOCOL),
        bundle=copy.deepcopy(PROMPT_BUNDLE),
        decision_store=decision_store,
        reviewer=_unexpected_reviewer,
        anchor_judge_model="",
        results={str(transcript["cell_key"]): copy.deepcopy(transcript)},
        transcript_generation_forbidden=True,
        role_limits=copy.deepcopy(ROLE_LIMITS),
    )
    record = execute_cell(
        resolved_cell,
        context,
        debater_model=resolved_cell.debater_model,
        namespace=str(PROTOCOL["cell_key_namespace"]),
    )
    return record, client.calls, reviewer_payloads


def _provider_request_sha256(call: dict) -> str:
    role = str(call["request_metadata"]["call_role"])
    role_key = "oracle" if role == finalization.ORACLE_VERIFICATION_ROLE else role
    role_entry = ROLE_LIMITS["model_role_limits"][call["model"]][role_key]
    settings = ROLE_LIMITS["request_settings"]
    kwargs = provider_provenance.build_provider_request_kwargs(
        model=call["model"],
        messages=call["messages"],
        temperature=call["temperature"],
        max_tokens=int(role_entry["effective_request_max_tokens"]),
        seed=call["seed"],
        streaming=call["model"] in settings["streaming_pinned_models"],
        extra_request_fields=settings["per_model_extra_fields"].get(call["model"]),
    )
    return provider_provenance.compute_request_fields_sha256(kwargs)


def _persist_captured_calls(
    *,
    ledger_path: Path,
    journal_path: Path,
    journal_identity: str,
    calls: list[dict],
) -> None:
    journal = RequestJournal(journal_path, execution_identity=journal_identity)
    for call_number, call in enumerate(calls, 1):
        logical_sha256 = request_fingerprint(
            messages=call["messages"],
            model=call["model"],
            temperature=call["temperature"],
            seed=call["seed"],
            max_tokens=call["max_tokens"],
        )
        metadata = dict(call["request_metadata"])
        journal.put(journal_key(metadata), logical_sha256, call["response"])
        metadata[JOURNAL_REQUEST_SHA256_FIELD] = logical_sha256
        metadata[api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD] = (
            AUTHORIZATION_APPROVED_AT_UTC)
        attempt_id = f"captured-attempt-{call_number}"
        stable = {
            "attempt_id": attempt_id,
            "model": call["model"],
            "kind": call["kind"],
            "seed": call["seed"],
            "attempt": 0,
            "reserved_prompt_tokens": 10,
            "reserved_completion_tokens": 10,
            "estimated_tokens": 20,
            "metadata": metadata,
        }
        _append_ledger_events(ledger_path, [
            {
                **stable,
                "status": "reserved",
                "prompt_tokens": None,
                "completion_tokens": None,
                "cost_usd": 0.000020,
            },
            {
                **stable,
                "status": "success",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "cost_usd": 0.000015,
                "response_metadata": {
                    "finish_reason": "stop",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "request_fields_sha256": _provider_request_sha256(call),
                    "returned_model_id": call["model"],
                },
            },
        ])


def _append_ledger_events(path: Path, events: list[dict]) -> None:
    snapshot = api_client.load_chained_usage_ledger(path)
    identity = snapshot.identity
    sequence = snapshot.last_sequence
    previous = snapshot.last_event_hash
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for raw_event in events:
            sequence += 1
            event = {
                **raw_event,
                "ts": raw_event.get("ts", AUTHORIZATION_APPROVED_AT_UTC),
                "ledger_id": identity["ledger_id"],
                "sequence": sequence,
                "prev_event_hash": previous,
            }
            event["event_hash"] = api_client._usage_event_hash(event)
            previous = event["event_hash"]
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    api_client._atomic_write_json(
        api_client.usage_ledger_state_path(path),
        api_client._usage_state_payload(identity, sequence, previous),
    )


def _append_provider_success(
    *,
    ledger_path: Path,
    journal_path: Path,
    journal_identity: str,
    cell: dict,
    response: str,
    call_role: str = finalization.JUDGE_VERDICT_ROLE,
    attempt_suffix: str = "1",
    request_sha256: str | None = None,
    seed: int = 7,
    query_index: int | None = None,
    query_attempt: int | None = None,
    slot: int | None = None,
    model: str | None = None,
) -> str:
    if query_index is not None and slot is not None:
        raise ValueError("provider fixture cannot specify both query_index and slot")
    request_sha256 = request_sha256 or hashlib.sha256(
        f"{cell['cell_key']}:{call_role}:{attempt_suffix}".encode("utf-8")
    ).hexdigest()
    metadata = {
        "stage": "judgment",
        "cell_key": cell["cell_key"],
        "call_role": call_role,
        "condition": cell["condition"],
        "question_id": cell["question_id"],
        "transcript_index": cell["transcript_index"],
        "budget": cell["query_budget"],
        "replicate": cell["replicate_index"],
        "judge_model": cell["judge_model"],
        JOURNAL_REQUEST_SHA256_FIELD: request_sha256,
    }
    if query_index is not None:
        metadata["query_index"] = query_index
    if slot is not None:
        metadata["slot"] = slot
    if query_attempt is not None:
        metadata["attempt"] = query_attempt
    journal = RequestJournal(journal_path, execution_identity=journal_identity)
    journal.put(journal_key(metadata), request_sha256, response)
    metadata[api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD] = (
        AUTHORIZATION_APPROVED_AT_UTC)
    attempt_id = f"attempt-{attempt_suffix}"
    stable = {
        "attempt_id": attempt_id,
        "model": model or cell["judge_model"],
        "kind": "verdict",
        "seed": seed,
        "attempt": 0,
        "reserved_prompt_tokens": 10,
        "reserved_completion_tokens": 10,
        "estimated_tokens": 20,
        "metadata": metadata,
    }
    _append_ledger_events(ledger_path, [
        {
            **stable,
            "status": "reserved",
            "prompt_tokens": None,
            "completion_tokens": None,
            "cost_usd": 0.000020,
        },
        {
            **stable,
            "status": "success",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_usd": 0.000015,
            "response_metadata": {
                "finish_reason": "stop",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        },
    ])
    return attempt_id


def _checker_artifacts(
    root: Path,
    *,
    cell_key: str,
    response: str,
    finish_reason: str = "length",
) -> tuple[Path, Path, str]:
    ledger_path = root / "usage.jsonl"
    journal_path = root / "journal.jsonl"
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    request_sha256 = "c" * 64
    metadata = {
        "cell_key": cell_key,
        "call_role": finalization.QUERY_CHECKER_ROLE,
        "slot": 0,
        "attempt": 1,
        JOURNAL_REQUEST_SHA256_FIELD: request_sha256,
    }
    journal = RequestJournal(journal_path, execution_identity=JOURNAL_IDENTITY)
    journal.put(journal_key(metadata), request_sha256, response)
    metadata[api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD] = (
        AUTHORIZATION_APPROVED_AT_UTC)
    attempt_id = "checker-attempt-1"
    stable = {
        "attempt_id": attempt_id,
        "model": finalization.DEFAULT_CHECKER_MODEL,
        "kind": "verdict",
        "seed": 7,
        "attempt": 0,
        "reserved_prompt_tokens": 10,
        "reserved_completion_tokens": 10,
        "estimated_tokens": 20,
        "metadata": metadata,
    }
    _append_ledger_events(ledger_path, [
        {
            **stable,
            "status": "reserved",
            "prompt_tokens": None,
            "completion_tokens": None,
            "cost_usd": 0.000020,
        },
        {
            **stable,
            "status": "success",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_usd": 0.000015,
            "response_metadata": {
                "finish_reason": finish_reason,
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        },
    ])
    return ledger_path, journal_path, attempt_id


@pytest.mark.parametrize("reason", sorted(finalization.TERMINAL_REASONS))
def test_exact_partition_exposes_terminal_and_context_keys(inventory, reason):
    terminal_cell = inventory.judgment_cells[0]
    context_cell = inventory.judgment_cells[1]
    excluded = {terminal_cell["cell_key"], context_cell["cell_key"]}
    results = [
        cell["cell_key"] for cell in inventory.cells
        if cell["cell_key"] not in excluded
    ]

    partition = finalization.validate_main_partition(
        inventory=inventory,
        result_cell_keys=results,
        terminal_records=[_terminal_record(str(terminal_cell["cell_key"]), reason)],
        context_blocklist=_context_blocklist(inventory, [context_cell]),
    )

    assert partition["observed_transcripts"] == 492
    assert partition["observed_judgments"] == 9_838
    assert partition["terminal_invalid_judgments"] == 1
    assert partition["context_ineligible_judgments"] == 1
    assert partition["terminal_cell_keys"] == [terminal_cell["cell_key"]]
    assert partition["context_ineligible_cell_keys"] == [context_cell["cell_key"]]


def test_partition_rejects_missing_overlap_and_non_checker_terminal(inventory):
    all_keys = [cell["cell_key"] for cell in inventory.cells]
    terminal_cell = inventory.judgment_cells[0]
    with pytest.raises(finalization.MainPartitionError, match="not exact"):
        finalization.validate_main_partition(
            inventory=inventory,
            result_cell_keys=all_keys[:-1],
            terminal_records=[],
            context_blocklist=_context_blocklist(inventory),
        )
    with pytest.raises(finalization.MainPartitionError, match="also has a result"):
        finalization.validate_main_partition(
            inventory=inventory,
            result_cell_keys=all_keys,
            terminal_records=[_terminal_record(str(terminal_cell["cell_key"]))],
            context_blocklist=_context_blocklist(inventory),
        )
    with pytest.raises(finalization.MainPartitionError, match="inadmissible reason"):
        finalization.validate_main_partition(
            inventory=inventory,
            result_cell_keys=all_keys[:-1],
            terminal_records=[
                {"cell_key": terminal_cell["cell_key"], "reason": "provider_error"}
            ],
            context_blocklist=_context_blocklist(inventory),
        )


@pytest.mark.parametrize("reason", ["checker_malformed", "checker_unresolved", "mixed"])
def test_terminal_bounds_enforce_count_concentration_and_mirror_fraction(
    inventory, monkeypatch, reason,
):
    judgments = list(inventory.judgment_cells)

    def records(cells):
        return [
            _terminal_record(
                str(cell["cell_key"]),
                ("checker_malformed" if index % 2 else "checker_unresolved")
                if reason == "mixed" else reason,
            )
            for index, cell in enumerate(cells)
        ]

    assert finalization.MAX_TERMINAL_JUDGMENT_CELLS == 20
    with pytest.raises(finalization.TerminalBoundError, match="exceed the frozen bound"):
        finalization.evaluate_terminal_bounds(
            inventory=inventory,
            terminal_records=records(judgments[:21]),
        )

    group = [
        cell for cell in judgments
        if (cell["judge_model"], cell["condition"])
        == (judgments[0]["judge_model"], judgments[0]["condition"])
    ]
    with pytest.raises(finalization.TerminalBoundError, match="concentration"):
        finalization.evaluate_terminal_bounds(
            inventory=inventory,
            terminal_records=records(group[:5]),
        )

    monkeypatch.setattr(finalization, "MAX_TERMINAL_JUDGMENT_CELLS", 500)
    unit_cells: dict[tuple, dict] = {}
    for cell in judgments:
        unit = (
            cell["question_id"],
            cell["judge_model"],
            cell["debater_model"],
            cell["transcript_index"],
            cell["condition"],
        )
        unit_cells.setdefault(unit, cell)
    spread = list(unit_cells.values())[:198]
    with pytest.raises(finalization.TerminalBoundError, match="4 percent"):
        finalization.evaluate_terminal_bounds(
            inventory=inventory,
            terminal_records=records(spread),
        )


def test_checker_malformed_is_reproved_from_ledger_and_journal(tmp_path, inventory):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    ledger, journal, attempt_id = _checker_artifacts(
        tmp_path, cell_key=cell_key, response="")

    evidence = finalization.mechanically_validate_checker_malformed(
        cell_key=cell_key,
        ledger_attempt_id=attempt_id,
        expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
        usage_ledger_path=ledger,
        request_journal_path=journal,
        journal_execution_identity=JOURNAL_IDENTITY,
    )

    assert evidence["ledger_reserved_sequence"] == 1
    assert evidence["ledger_terminal_sequence"] == 2
    assert evidence["journal_sequence"] == 0
    assert evidence["finish_reason"] == "length"
    assert evidence["checker_response_sha256"] == finalization._sha256_text("")
    assert "exactly allow" in evidence["parse_failure"]


@pytest.mark.parametrize("response", ["allow", "reject", "unresolved"])
def test_valid_checker_token_cannot_be_disposed_as_malformed(tmp_path, inventory, response):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    ledger, journal, attempt_id = _checker_artifacts(
        tmp_path, cell_key=cell_key, response=response, finish_reason="stop")
    with pytest.raises(
            finalization.TerminalDispositionError, match="parses successfully"):
        finalization.mechanically_validate_checker_malformed(
            cell_key=cell_key,
            ledger_attempt_id=attempt_id,
            expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
            usage_ledger_path=ledger,
            request_journal_path=journal,
            journal_execution_identity=JOURNAL_IDENTITY,
        )


@pytest.mark.parametrize("response", ["allow", "reject", "", "unresolved\n", "UNRESOLVED"])
def test_unresolved_disposition_rejects_mismatched_journal_response(
    tmp_path, inventory, response,
):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    ledger, journal, attempt_id = _checker_artifacts(
        tmp_path, cell_key=cell_key, response=response, finish_reason="stop")
    before = (ledger.read_bytes(), journal.read_bytes())
    with pytest.raises(finalization.TerminalDispositionError, match="exact unresolved"):
        finalization.mechanically_validate_checker_terminal(
            cell_key=cell_key,
            reason="checker_unresolved",
            ledger_attempt_id=attempt_id,
            expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
            usage_ledger_path=ledger,
            request_journal_path=journal,
            journal_execution_identity=JOURNAL_IDENTITY,
        )
    assert (ledger.read_bytes(), journal.read_bytes()) == before


@pytest.mark.parametrize("reason", sorted(finalization.TERMINAL_REASONS))
def test_terminal_store_is_identity_seeded_chained_and_evidence_bound(
    tmp_path, inventory, reason,
):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    response = "unresolved" if reason == "checker_unresolved" else "bad checker text"
    ledger, journal, attempt_id = _checker_artifacts(
        tmp_path, cell_key=cell_key, response=response, finish_reason="stop")
    before = (ledger.read_bytes(), journal.read_bytes())
    path = tmp_path / "terminal.jsonl"
    store = finalization.MainTerminalDispositionStore(
        path,
        run_id=RUN_ID,
        manifest_canonical_sha256=MANIFEST_SHA256,
        inventory=inventory,
    )
    row = store.record_checker_terminal(
        cell_key,
        reason=reason,
        ledger_attempt_id=attempt_id,
        usage_ledger_path=ledger,
        request_journal_path=journal,
        journal_execution_identity=JOURNAL_IDENTITY,
        result_cell_keys=(),
        recorded_at_utc="2026-08-29T12:00:00Z",
    )
    assert row["prev_event_hash"] == finalization._terminal_seed(
        run_id=RUN_ID,
        manifest_sha256=MANIFEST_SHA256,
        inventory_sha256=finalization.inventory_canonical_sha256(inventory),
    )
    reopened = finalization.MainTerminalDispositionStore(
        path,
        run_id=RUN_ID,
        manifest_canonical_sha256=MANIFEST_SHA256,
        inventory=inventory,
    )
    reopened.verify_all_evidence(
        usage_ledger_path=ledger,
        request_journal_path=journal,
        journal_execution_identity=JOURNAL_IDENTITY,
        result_cell_keys=(),
    )
    assert reopened.cell_keys == {cell_key}
    assert reopened.tail["last_sequence"] == 0
    assert row["reason"] == reason
    assert row["evidence"]["checker_response_sha256"] == finalization._sha256_text(response)
    if reason == "checker_unresolved":
        assert row["evidence"]["checker_decision"] == "unresolved"
        assert "parse_failure" not in row["evidence"]
    assert (ledger.read_bytes(), journal.read_bytes()) == before
    diagnostic = finalization.checker_truncation_diagnostic(
        inventory=inventory, ledger_events=api_client._read_usage_events(ledger),
        terminal_records=store.records)
    assert diagnostic["terminal_checker_malformed_count"] == int(reason == "checker_malformed")
    assert diagnostic["terminal_checker_unresolved_count"] == int(reason == "checker_unresolved")
    assert diagnostic["terminal_signatures"][0]["reason"] == reason
    with pytest.raises(finalization.TerminalDispositionError, match="already exists"):
        reopened.record_checker_terminal(
            cell_key,
            reason=reason,
            ledger_attempt_id=attempt_id,
            usage_ledger_path=ledger,
            request_journal_path=journal,
            journal_execution_identity=JOURNAL_IDENTITY,
            result_cell_keys=(),
            recorded_at_utc="2026-08-29T12:01:00Z",
        )
    with pytest.raises(finalization.TerminalDispositionError, match="another main identity"):
        finalization.MainTerminalDispositionStore(
            path,
            run_id=f"{RUN_ID}-other",
            manifest_canonical_sha256=MANIFEST_SHA256,
            inventory=inventory,
        )
    opposite_reason = next(iter(finalization.TERMINAL_REASONS - {reason}))
    wrong_reason_row = {**row, "reason": opposite_reason}
    wrong_reason_row["event_hash"] = finalization._terminal_row_hash(wrong_reason_row)
    path.write_text(json.dumps(wrong_reason_row) + "\n", encoding="utf-8")
    with pytest.raises(finalization.MainFinalizationError, match="terminal evidence"):
        finalization.MainTerminalDispositionStore(
            path, run_id=RUN_ID, manifest_canonical_sha256=MANIFEST_SHA256,
            inventory=inventory)
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["evidence"]["checker_response_sha256"] = "f" * 64
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(finalization.TerminalDispositionError, match="row hash"):
        finalization.MainTerminalDispositionStore(
            path,
            run_id=RUN_ID,
            manifest_canonical_sha256=MANIFEST_SHA256,
            inventory=inventory,
        )
    with pytest.raises(finalization.MainFinalizationError, match="frozen"):
        finalization.MainTerminalDispositionStore(
            tmp_path / "wrong-model.jsonl",
            run_id=RUN_ID,
            manifest_canonical_sha256=MANIFEST_SHA256,
            inventory=inventory,
            checker_model="another-model",
        )


def test_terminal_cell_provider_provenance_allows_only_pre_verdict_roles(
    tmp_path, inventory,
):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    decisions_path = tmp_path / "decisions.jsonl"
    decisions_path.write_text("", encoding="utf-8")
    provider_paths, provider_hashes = _write_provider_inputs(tmp_path, inventory)
    transcript_provenance = (
        phase3_main_transcript_provenance.
        load_manifest_bound_main_transcript_provenance(
            inventory=inventory,
            main_bundle_path=provider_paths["main_transcript_bundle"],
            transcript_verification_path=provider_paths["transcript_verification"],
            expected_main_bundle_raw_sha256=provider_hashes[
                "main_transcript_bundle"],
            expected_transcript_verification_raw_sha256=provider_hashes[
                "transcript_verification"],
        )
    )
    with pytest.raises(finalization.MainFinalizationError, match="post-checker verdict"):
        finalization._validate_main_provider_provenance(
            inventory=inventory,
            result_rows=[],
            terminal_records=[_terminal_record(cell_key)],
            context_ineligible_cell_keys=[],
            ledger_events=[{"status": "ledger_genesis"}],
            journal_rows=[{
                "cell_key": cell_key,
                "call_role": finalization.JUDGE_VERDICT_ROLE,
                "slot": 0,
                "attempt": 0,
                "request_sha256": "a" * 64,
                "response": "foreign verdict",
            }],
            reviewer_decisions={},
            review_decisions_path=decisions_path,
            protocol=PROTOCOL,
            prompt_bundle=PROMPT_BUNDLE,
            role_limits=ROLE_LIMITS,
            transcript_provenance=transcript_provenance,
            expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
            expected_oracle_model=ORACLE_MODEL,
            result_store_raw_sha256="b" * 64,
            request_journal_raw_sha256="c" * 64,
            usage_ledger_raw_sha256="d" * 64,
            authorization_approved_at_utc=AUTHORIZATION_APPROVED_AT_UTC,
            authorization_valid_until_utc=AUTHORIZATION_VALID_UNTIL_UTC,
            finalization_recorded_at_utc="2026-08-29T13:00:00Z",
        )


def test_checker_truncation_diagnostic_is_stratified(inventory):
    cell = inventory.judgment_cells[0]
    diagnostic = finalization.checker_truncation_diagnostic(
        inventory=inventory,
        ledger_events=[{
            "status": "success",
            "attempt_id": "attempt-length",
            "event_hash": "d" * 64,
            "metadata": {
                "cell_key": cell["cell_key"],
                "call_role": finalization.QUERY_CHECKER_ROLE,
            },
            "response_metadata": {"finish_reason": "length"},
            "prompt_tokens": 2_500,
            "completion_tokens": 16,
        }],
        terminal_records=[],
    )
    assert diagnostic["checker_calls_total"] == 1
    assert diagnostic["finish_length_count"] == 1
    assert diagnostic["by_judge"][cell["judge_model"]]["finish_length_rate"] == 1.0
    assert diagnostic["by_condition"][cell["condition"]]["finish_length_rate"] == 1.0
    assert diagnostic["by_prompt_token_band"]["2-4k"]["finish_length_rate"] == 1.0
    assert "not assumed missing completely at random" in diagnostic["non_claim"]


def _complete_finalization_inputs(
    tmp_path: Path,
    inventory,
    *,
    observed_cell=None,
    query_responses: tuple[str, ...] | None = None,
    checker_response: str = "allow",
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    observed_cell = dict(observed_cell or inventory.judgment_cells[0])
    if query_responses is None:
        query_responses = (
            ("DONE",) if int(observed_cell["query_budget"]) > 0 else ())
    context_cells = [
        dict(cell) for cell in inventory.judgment_cells
        if cell["cell_key"] != observed_cell["cell_key"]
    ]
    result_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["results"]
    result_rows = [
        (str(cell["cell_key"]), _transcript_result(dict(cell)))
        for cell in inventory.transcript_cells
    ]
    review_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["decisions"]
    review_path.write_text("", encoding="utf-8")
    dependency_key = str(observed_cell["dependency_keys"][0])
    transcript = next(
        result for cell_key, result in result_rows if cell_key == dependency_key)
    judgment_result, captured_calls, reviewer_payloads = _execute_fixture_judgment(
        inventory=inventory,
        observed_cell=observed_cell,
        transcript=transcript,
        decisions_path=review_path,
        query_responses=query_responses,
        checker_response=checker_response,
    )
    # The execution fixture needs the decisions to complete the judgment. The formal
    # output store is then rebuilt through the same crash-consistent wave transaction
    # used by the live path so finalization audits its intent and receipt as well.
    review_path.write_bytes(b"")
    result_rows.append((str(observed_cell["cell_key"]), judgment_result))
    _write_result_store(result_path, result_rows)
    ledger_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["usage_ledger"]
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    journal_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["request_journal"]
    journal_identity = _main_journal_identity(tmp_path)
    _persist_captured_calls(
        ledger_path=ledger_path,
        journal_path=journal_path,
        journal_identity=journal_identity,
        calls=captured_calls,
    )
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(_context_blocklist(inventory, context_cells), sort_keys=True),
        encoding="utf-8",
    )
    (
        reviewer_input_paths,
        reviewer_input_raw_sha256s,
        frozen_reviewer_prompt,
        capacity_evidence_inputs,
    ) = _write_reviewer_inputs(tmp_path)
    (
        reviewer_index_path,
        reviewer_worklist_path,
        review_packets_root_path,
    ) = _write_reviewer_artifacts(
        tmp_path=tmp_path,
        reviewer_payloads=reviewer_payloads,
        decision_store_path=review_path.resolve(),
        reviewer_input_paths=reviewer_input_paths,
        reviewer_input_raw_sha256s=reviewer_input_raw_sha256s,
        capacity_evidence_inputs=capacity_evidence_inputs,
        frozen_prompt=frozen_reviewer_prompt,
    )
    provider_error_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES[
        "provider_error_log"]
    provider_error_path.write_text("", encoding="utf-8")
    run_log_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["run_log"]
    run_log_path.write_text("", encoding="utf-8")
    terminal_store = finalization.MainTerminalDispositionStore(
        tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["terminal_dispositions"],
        run_id=RUN_ID,
        manifest_canonical_sha256=MANIFEST_SHA256,
        inventory=inventory,
    )
    pins_path = REPO_ROOT / "rejudge" / "phase3_main_analysis_pins_2026-08-29.json"
    provider_input_paths, provider_input_raw_sha256s = _write_provider_inputs(
        tmp_path, inventory)
    artifacts = {
        "result_store": result_path,
        "usage_ledger": ledger_path,
        "usage_ledger_state": api_client.usage_ledger_state_path(ledger_path),
        "request_journal": journal_path,
        "review_decisions": review_path,
        "reviewer_index": reviewer_index_path,
        "reviewer_worklist": reviewer_worklist_path,
        "provider_error_log": provider_error_path,
        "run_log": run_log_path,
        "context_blocklist": context_path,
        "terminal_dispositions": terminal_store.path,
        "analysis_pins": pins_path,
    }
    build_inputs = {
        "run_id": RUN_ID,
        "manifest_canonical_sha256": MANIFEST_SHA256,
        "authorization_canonical_sha256": AUTHORIZATION_SHA256,
        "authorization_raw_sha256": AUTHORIZATION_RAW_SHA256,
        "authorization_signature_raw_sha256": AUTHORIZATION_SIGNATURE_RAW_SHA256,
        "inventory": inventory,
        "context_blocklist_path": context_path,
        "terminal_store": terminal_store,
        "result_store_path": result_path,
        "usage_ledger_path": ledger_path,
        "request_journal_path": journal_path,
        "journal_execution_identity": journal_identity,
        "analysis_pins_path": pins_path,
        "provider_input_paths": provider_input_paths,
        "provider_input_raw_sha256s": provider_input_raw_sha256s,
        "reviewer_input_paths": reviewer_input_paths,
        "reviewer_input_raw_sha256s": reviewer_input_raw_sha256s,
        **capacity_evidence_inputs,
        "review_packets_root_path": review_packets_root_path.resolve(),
        "artifact_paths": artifacts,
        "expected_oracle_model": ORACLE_MODEL,
        "expected_reviewer_model": REVIEWER_MODEL,
        "expected_reviewer_reasoning_effort": REVIEWER_REASONING_EFFORT,
        "expected_reviewer_concurrency": REVIEWER_CONCURRENCY,
        "authorization_approved_at_utc": AUTHORIZATION_APPROVED_AT_UTC,
        "authorization_valid_until_utc": AUTHORIZATION_VALID_UNTIL_UTC,
        "prior_reconciled_usd": PRIOR_RECONCILED_USD,
        "stage_cap_usd": STAGE_CAP_USD,
    }
    return build_inputs


def _rewrite_result_payload(path: Path, cell_key: str, replacement: dict) -> None:
    rows = []
    for row in finalization.load_result_rows(path):
        result = dict(row["result"])
        if row["cell_key"] == cell_key:
            result = dict(replacement)
        rows.append((str(row["cell_key"]), result))
    _write_result_store(path, rows)


def _truncate_ledger_to_genesis(path: Path) -> None:
    genesis = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    path.write_text(json.dumps(genesis, sort_keys=True) + "\n", encoding="utf-8")
    identity = api_client._ledger_identity(path, str(genesis["ledger_id"]))
    api_client._atomic_write_json(
        api_client.usage_ledger_state_path(path),
        api_client._usage_state_payload(identity, 0, str(genesis["event_hash"])),
    )


def _rewrite_success_response_metadata(path: Path, field: str, value: str) -> None:
    events = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    success = next(event for event in events if event.get("status") == "success")
    success["response_metadata"][field] = value
    previous = str(events[0]["event_hash"])
    for event in events[1:]:
        event["prev_event_hash"] = previous
        event["event_hash"] = api_client._usage_event_hash(event)
        previous = str(event["event_hash"])
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    identity = api_client._ledger_identity(path, str(events[0]["ledger_id"]))
    api_client._atomic_write_json(
        api_client.usage_ledger_state_path(path),
        api_client._usage_state_payload(identity, len(events) - 1, previous),
    )


def _rewrite_provider_event_timestamps(path: Path, value: str) -> None:
    events = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    previous = str(events[0]["event_hash"])
    for event in events[1:]:
        event["ts"] = value
        metadata = dict(event["metadata"])
        metadata[api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD] = value
        event["metadata"] = metadata
        event["prev_event_hash"] = previous
        event["event_hash"] = api_client._usage_event_hash(event)
        previous = str(event["event_hash"])
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    identity = api_client._ledger_identity(path, str(events[0]["ledger_id"]))
    api_client._atomic_write_json(
        api_client.usage_ledger_state_path(path),
        api_client._usage_state_payload(identity, len(events) - 1, previous),
    )


def _rewrite_journal_for_foreign_identity(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    previous = "identity:foreign-run:foreign-manifest:foreign-root"
    for sequence, row in enumerate(rows):
        row["sequence"] = sequence
        row["prev_event_hash"] = previous
        row["event_hash"] = RequestJournal._row_hash(row)
        previous = row["event_hash"]
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_finalization_admits_exact_provider_bound_run_and_is_immutable(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    record = finalization.build_finalization_admission(
        **build_inputs,
        recorded_at_utc="2026-08-29T13:00:03Z",
    )
    assert record["status"] == finalization.FINALIZATION_STATUS
    assert record["partition"]["observed_transcripts"] == 492
    assert record["partition"]["observed_judgments"] == 1
    assert record["partition"]["context_ineligible_judgments"] == 9_839
    assert record["terminal_store"]["record_count"] == 0
    assert record["terminal_store"]["last_sequence"] == -1
    assert record["provider_provenance"]["observed_judgment_count"] == 1
    assert record["provider_provenance"]["verdict_journal_count"] == 1
    assert record["provider_provenance"]["verdict_ledger_success_count"] == 1
    assert record["provider_provenance"]["normal_execution_replay_status"] == (
        "exact_normal_execution_replay")
    assert record["provider_provenance"]["replayed_judgment_count"] == 1
    assert record["provider_provenance"]["replayed_terminal_count"] == 0
    assert record["provider_provenance"]["logical_request_count"] == 1
    assert record["provider_provenance"]["provider_request_count"] == 1
    assert record["provider_provenance"]["main_transcript_bundle_raw_sha256"] == (
        build_inputs["provider_input_raw_sha256s"]["main_transcript_bundle"])
    assert record["provider_provenance"]["transcript_verification_raw_sha256"] == (
        build_inputs["provider_input_raw_sha256s"]["transcript_verification"])
    assert record["reviewer_provenance"] == {
        "reviewer_provenance_status": "reviewer_provenance_verified",
        "reviewer_wave_count": 0,
        "reviewed_payload_count": 0,
        "parsed_decision_count": 0,
        "malformed_decision_count": 0,
        "reviewer_error_decision_count": 0,
        "review_packets_root": Path(
            build_inputs["review_packets_root_path"]).resolve().as_posix(),
        "review_packets_tree_canonical_sha256": hashlib.sha256(b"[]").hexdigest(),
        "reviewer_index_raw_sha256": _raw_sha256(
            Path(build_inputs["artifact_paths"]["reviewer_index"])),
        "reviewer_worklist_raw_sha256": _raw_sha256(
            Path(build_inputs["artifact_paths"]["reviewer_worklist"])),
        "reviewer_prompt_raw_sha256": build_inputs[
            "reviewer_input_raw_sha256s"]["reviewer_prompt"],
        "reviewer_failure_policy_raw_sha256": build_inputs[
            "reviewer_input_raw_sha256s"]["reviewer_failure_policy"],
        "capacity_plan_raw_sha256": build_inputs[
            "reviewer_input_raw_sha256s"]["capacity_plan"],
        "capacity_result_raw_sha256": build_inputs[
            "expected_capacity_result_raw_sha256"],
        "capacity_dispatch_history_raw_sha256": build_inputs[
            "expected_capacity_dispatch_history_raw_sha256"],
    }
    assert record["accounting"] == {
        "prior_reconciled_usd": PRIOR_RECONCILED_USD,
        "voided_predecessor_usd": "0",
        "current_settled_usd": "0.000015",
        "current_uncertain_usd": "0",
        "current_accounted_usd": "0.000015",
        "stage_total_usd": "0.30986789",
        "stage_cap_usd": STAGE_CAP_USD,
        "within_stage_cap": True,
        "completion_label": "PASS_CLEAN",
        "run_uncertain_ceiling_usd": None,
        "uncertain_within_ceiling": True,
        "unknown_charge_attempt_count": 0,
        "unknown_charge_by_model": {},
        "usage_ledger_raw_sha256": record["artifact_hashes"]["usage_ledger"][
            "raw_sha256"],
        "usage_ledger_state_raw_sha256": record["artifact_hashes"][
            "usage_ledger_state"]["raw_sha256"],
        "usage_ledger_id": record["accounting"]["usage_ledger_id"],
        "usage_ledger_tail_sequence": 2,
        "usage_ledger_tail_event_hash": record["accounting"][
            "usage_ledger_tail_event_hash"],
    }
    assert record["reconciliation"] == {
        "status": "clean",
        "ambiguous_dispatches": [],
        "unmatched_reservations": 0,
        "unknown_charge_attempt_ids": [],
        "charged_malformed_attempt_ids": [],
        "settled_success_without_journal_attempt_ids": [],
        "unresolved_dispatch_marker_present": False,
    }
    assert set(record["artifact_hashes"]) == finalization.REQUIRED_ARTIFACTS
    assert finalization.validate_finalization_admission(
        record, **build_inputs) == record
    manifest_output_paths = {
        name: (tmp_path / filename).resolve()
        for name, filename in phase3_main_manifest.OUTPUT_FILENAMES.items()
    }
    def validate_bound(
        authorization_raw_sha256: str = AUTHORIZATION_RAW_SHA256,
        authorization_signature_raw_sha256: str = (
            AUTHORIZATION_SIGNATURE_RAW_SHA256),
        candidate=record,
        expected_manifest_output_paths=manifest_output_paths,
        expected_review_packets_root_path=build_inputs[
            "review_packets_root_path"],
        expected_reviewer_model=REVIEWER_MODEL,
        expected_reviewer_concurrency=REVIEWER_CONCURRENCY,
    ):
        return finalization.validate_finalization_from_bound_artifacts(
            candidate,
            inventory=inventory,
            expected_run_id=RUN_ID,
            expected_manifest_canonical_sha256=MANIFEST_SHA256,
            expected_authorization_canonical_sha256=AUTHORIZATION_SHA256,
            expected_authorization_raw_sha256=authorization_raw_sha256,
            expected_authorization_signature_raw_sha256=(
                authorization_signature_raw_sha256),
            authorization_approved_at_utc="2026-08-29T12:00:00Z",
            authorization_valid_until_utc="2026-08-30T12:00:00Z",
            expected_result_store_path=build_inputs["result_store_path"],
            expected_analysis_pins_path=build_inputs["analysis_pins_path"],
            expected_context_blocklist_path=build_inputs["context_blocklist_path"],
            expected_manifest_output_paths=expected_manifest_output_paths,
            expected_provider_input_paths=build_inputs["provider_input_paths"],
            expected_provider_input_raw_sha256s=build_inputs[
                "provider_input_raw_sha256s"],
            expected_reviewer_input_paths=build_inputs["reviewer_input_paths"],
            expected_reviewer_input_raw_sha256s=build_inputs[
                "reviewer_input_raw_sha256s"],
            expected_capacity_result_path=build_inputs["capacity_result_path"],
            expected_capacity_result_raw_sha256=build_inputs[
                "expected_capacity_result_raw_sha256"],
            expected_capacity_dispatch_history_path=build_inputs[
                "capacity_dispatch_history_path"],
            expected_capacity_dispatch_history_raw_sha256=build_inputs[
                "expected_capacity_dispatch_history_raw_sha256"],
            expected_review_packets_root_path=expected_review_packets_root_path,
            expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
            expected_oracle_model=ORACLE_MODEL,
            expected_reviewer_model=expected_reviewer_model,
            expected_reviewer_reasoning_effort=REVIEWER_REASONING_EFFORT,
            expected_reviewer_concurrency=expected_reviewer_concurrency,
            prior_reconciled_usd=PRIOR_RECONCILED_USD,
            stage_cap_usd=STAGE_CAP_USD,
        )

    assert validate_bound() == record
    with pytest.raises(
        finalization.MainFinalizationError, match="authorization bytes differ",
    ):
        validate_bound(authorization_raw_sha256="e" * 64)
    with pytest.raises(
        finalization.MainFinalizationError,
        match="authorization signature bytes differ",
    ):
        validate_bound(authorization_signature_raw_sha256="f" * 64)
    incomplete_output_paths = dict(manifest_output_paths)
    incomplete_output_paths.pop("completion")
    with pytest.raises(
        finalization.MainFinalizationError,
        match="expected manifest output path fields drifted",
    ):
        validate_bound(expected_manifest_output_paths=incomplete_output_paths)
    with pytest.raises(
        finalization.MainFinalizationError,
        match="expected review packet root differs from the signed manifest output path",
    ):
        validate_bound(
            expected_review_packets_root_path=(tmp_path / "foreign-review-root").resolve())
    with pytest.raises(
        finalization.MainFinalizationError,
        match="capacity plan reviewer model drifted",
    ):
        validate_bound(expected_reviewer_model="gpt-5.6-terra")
    with pytest.raises(
        finalization.MainFinalizationError,
        match="capacity plan reviewer concurrency drifted",
    ):
        validate_bound(expected_reviewer_concurrency=REVIEWER_CONCURRENCY - 1)
    for artifact_label in ("usage_ledger", "request_journal"):
        shadow = copy.deepcopy(record)
        shadow["artifact_hashes"][artifact_label]["path"] = (
            tmp_path / f"shadow-{artifact_label}.jsonl").resolve().as_posix()
        with pytest.raises(
            finalization.MainFinalizationError,
            match=(
                rf"finalization artifact {artifact_label} does not equal the signed "
                "manifest output path"
            ),
        ):
            validate_bound(candidate=shadow)

    output = tmp_path / "finalization.json"
    finalization.write_finalization_admission(output, record)
    first = output.read_bytes()
    with pytest.raises(finalization.MainFinalizationError, match="immutable"):
        finalization.write_finalization_admission(output, record)
    assert output.read_bytes() == first


@pytest.mark.parametrize(
    "recorded_at_utc",
    [
        "2026-08-29T12:00:00+00:00",
        AUTHORIZATION_VALID_UNTIL_UTC,
        "2026-08-30T12:00:00.000001Z",
    ],
)
def test_zero_wave_finalization_accepts_dispatch_boundary_and_later_closeout(
    tmp_path, inventory, recorded_at_utc,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)

    record = finalization.build_finalization_admission(
        **build_inputs, recorded_at_utc=recorded_at_utc)

    assert record["reviewer_provenance"]["reviewer_wave_count"] == 0
    assert finalization.validate_finalization_admission(
        record, **build_inputs)["recorded_at_utc"] == recorded_at_utc


def test_zero_wave_finalization_rejects_closeout_before_authorization(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)

    with pytest.raises(
        finalization.MainFinalizationError,
        match="finalization predates the signed authorization",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T11:59:59Z")


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        (
            "request_fields_sha256",
            "f" * 64,
            "post-role-limit provider request hash differs",
        ),
        (
            "returned_model_id",
            "foreign/provider-model",
            "returned model identity differs",
        ),
    ],
)
def test_finalization_rejects_literal_provider_receipt_drift(
    tmp_path, inventory, field, value, expected,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    _rewrite_success_response_metadata(
        Path(build_inputs["usage_ledger_path"]), field, value)

    with pytest.raises(finalization.MainFinalizationError, match=expected):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:30Z")


def test_finalization_rejects_transcript_result_drift_from_bound_bundle(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    cell = dict(inventory.transcript_cells[0])
    rows = finalization.load_result_rows(Path(build_inputs["result_store_path"]))
    result = dict(next(
        row["result"] for row in rows if row["cell_key"] == cell["cell_key"]
    ))
    result["question"] = "Mutated transcript question?"
    _rewrite_result_payload(
        Path(build_inputs["result_store_path"]), str(cell["cell_key"]), result)

    with pytest.raises(
        finalization.MainFinalizationError,
        match="exact frozen payload",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:31Z")


def test_finalization_rejects_reviewer_prompt_bytes_drift(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    prompt_path = Path(build_inputs["reviewer_input_paths"]["reviewer_prompt"])
    prompt_path.write_bytes(prompt_path.read_bytes() + b"\n")

    with pytest.raises(
        finalization.MainFinalizationError,
        match=(
            "reviewer provenance input reviewer_prompt differs from its manifest binding"
        ),
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:32Z")


def test_finalization_rejects_reviewer_failure_policy_semantic_drift(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    policy_path = Path(
        build_inputs["reviewer_input_paths"]["reviewer_failure_policy"])
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["timeout_or_unavailability"] = "commit_reviewer_error"
    _write_json(policy_path, policy)
    build_inputs["reviewer_input_raw_sha256s"]["reviewer_failure_policy"] = (
        _raw_sha256(policy_path))

    with pytest.raises(
        finalization.MainFinalizationError,
        match="failure policy differs from the exact Phase 3 contract",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:33Z")


def test_finalization_rejects_capacity_result_semantic_forgery(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    result_path = Path(build_inputs["capacity_result_path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["waves"][0]["failure_counts"]["reviewer_errors"] = 1
    _write_json(result_path, result)
    build_inputs["expected_capacity_result_raw_sha256"] = _raw_sha256(result_path)

    with pytest.raises(
        finalization.MainFinalizationError,
        match="capacity evidence failed semantic validation: wave 1 records a failed dispatch",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:33Z")


def test_finalization_rejects_capacity_history_completed_failure(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    history_path = Path(build_inputs["capacity_dispatch_history_path"])
    rows = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[-1]["event"] = "attempt_completed_fail"
    rows[-1]["event_hash"] = ""
    rows[-1]["event_hash"] = capacity_preflight.dispatch_history_event_hash(rows[-1])
    history_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    build_inputs["expected_capacity_dispatch_history_raw_sha256"] = _raw_sha256(
        history_path)

    with pytest.raises(
        finalization.MainFinalizationError,
        match="completed failure, replay, or unverified interruption",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:33Z")


def test_finalization_rechecks_capacity_evidence_after_provenance(
    tmp_path, inventory, monkeypatch,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    result_path = Path(build_inputs["capacity_result_path"])
    original = finalization.phase3_main_reviewer_provenance.verify_main_reviewer_provenance

    def validate_then_mutate(**kwargs):
        result = original(**kwargs)
        result_path.write_bytes(result_path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(
        finalization.phase3_main_reviewer_provenance,
        "verify_main_reviewer_provenance",
        validate_then_mutate,
    )
    with pytest.raises(
        finalization.MainFinalizationError,
        match="capacity evidence input capacity_result changed during finalization",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:33Z")


@pytest.mark.parametrize(
    ("target", "match"),
    [
        ("reviewer_index", "reviewer index changed after reviewer provenance"),
        ("reviewer_worklist", "reviewer worklist changed after reviewer provenance"),
        ("review_packets_tree", "review packet tree changed after reviewer provenance"),
    ],
)
def test_finalization_rechecks_reviewer_outputs_after_provenance(
    tmp_path, inventory, monkeypatch, target, match,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    original = finalization.phase3_main_reviewer_provenance.verify_main_reviewer_provenance

    def validate_then_mutate(**kwargs):
        result = original(**kwargs)
        if target in {"reviewer_index", "reviewer_worklist"}:
            path = Path(build_inputs["artifact_paths"][target])
            path.write_bytes(path.read_bytes() + b" ")
        else:
            (Path(build_inputs["review_packets_root_path"]) / "late-tamper.txt").write_text(
                "tamper", encoding="utf-8")
        return result

    monkeypatch.setattr(
        finalization.phase3_main_reviewer_provenance,
        "verify_main_reviewer_provenance",
        validate_then_mutate,
    )
    with pytest.raises(finalization.MainFinalizationError, match=match):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:33Z")


def test_finalization_rejects_unindexed_reviewer_packet_root_child(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    (Path(build_inputs["review_packets_root_path"]) / "unindexed").mkdir()

    with pytest.raises(
        finalization.MainFinalizationError,
        match="review packet root has an unindexed child",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:34Z")


@pytest.mark.parametrize(
    "failure", ["fabricated", "cross_run", "dry_run", "oracle_model"])
def test_finalization_rejects_unproven_judgment_results(
    tmp_path, inventory, failure,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    cell = dict(inventory.judgment_cells[0])
    if failure == "fabricated":
        replacement = {
            "cell_key": cell["cell_key"],
            "status": "complete",
            "dry_run": False,
        }
        expected = "planned cell"
    else:
        replacement = dict(next(
            row["result"] for row in finalization.load_result_rows(
                Path(build_inputs["result_store_path"]))
            if row["cell_key"] == cell["cell_key"]
        ))
        if failure == "oracle_model":
            replacement["oracle_model"] = "foreign oracle"
            expected = "another oracle model"
        elif failure == "dry_run":
            replacement["dry_run"] = True
            expected = "dry-run"
        else:
            foreign = (
                "VERDICT: Position B\nCONFIDENCE: 4\nREASONING: foreign run")
            parses = parse_both(foreign)
            replacement["raw_verdict_text"] = foreign
            replacement["verdict_strict"] = parses["strict"]
            replacement["verdict_pilot"] = parses["pilot"]
            replacement["verdict_correct_strict"] = (
                (parses["strict"]["verdict"] == "A")
                == replacement["position_a_is_correct"])
            replacement["verdict_correct_pilot"] = (
                (parses["pilot"]["verdict"] == "Position A")
                == replacement["position_a_is_correct"])
            replacement["judge_messages"][-1]["content"] = foreign
            expected = "provider response evidence"
    _rewrite_result_payload(
        Path(build_inputs["result_store_path"]), str(cell["cell_key"]), replacement)

    with pytest.raises(finalization.MainFinalizationError, match=expected):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:00Z")


@pytest.mark.parametrize("mutation", ["strict_verdict", "judge_messages"])
def test_finalization_recomputes_outcome_and_verdict_request_semantics(
    tmp_path, inventory, mutation,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    cell = dict(inventory.judgment_cells[0])
    result = dict(next(
        row["result"] for row in finalization.load_result_rows(
            Path(build_inputs["result_store_path"]))
        if row["cell_key"] == cell["cell_key"]
    ))
    if mutation == "strict_verdict":
        result["verdict_strict"] = parse_both(
            "VERDICT: Position B\nCONFIDENCE: 4\nREASONING: replacement"
        )["strict"]
        expected = "strict verdict was not recomputed"
    else:
        result["judge_messages"][1]["content"] = "mutated verdict request"
        expected = "verdict request fingerprint drifted"
    _rewrite_result_payload(
        Path(build_inputs["result_store_path"]), str(cell["cell_key"]), result)

    with pytest.raises(finalization.MainFinalizationError, match=expected):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:30Z")


def test_finalization_admits_done_retry_after_first_query_rejection(
    tmp_path, inventory,
):
    cell = next(
        dict(candidate) for candidate in inventory.judgment_cells
        if candidate["query_budget"] == 1
    )
    raw_query = "Is the fixture fact explicitly stated?"
    build_inputs = _complete_finalization_inputs(
        tmp_path,
        inventory,
        observed_cell=cell,
        query_responses=(raw_query, "DONE"),
        checker_response="reject",
    )

    record = finalization.build_finalization_admission(
        **build_inputs, recorded_at_utc="2026-08-29T13:01:40Z")
    assert record["status"] == finalization.FINALIZATION_STATUS
    assert record["provider_provenance"]["journaled_call_count"] == 4
    assert record["provider_provenance"]["settled_success_call_count"] == 4
    assert record["reviewer_provenance"]["reviewer_wave_count"] == 1
    assert record["reviewer_provenance"]["reviewed_payload_count"] == 1
    assert record["reviewer_provenance"]["parsed_decision_count"] == 1
    reviewer_index_row = json.loads(
        Path(build_inputs["artifact_paths"]["reviewer_index"])
        .read_text(encoding="utf-8")
        .strip()
    )
    assert reviewer_index_row["schema_version"] == reviewer_commit.REVIEWER_WAVE_SCHEMA
    packet_dir = Path(reviewer_index_row["packet_directory"])
    transaction_paths = reviewer_commit.reviewer_wave_transaction_paths(packet_dir)
    intent = json.loads(transaction_paths.intent.read_text(encoding="utf-8"))
    receipt = json.loads(transaction_paths.receipt.read_text(encoding="utf-8"))
    assert intent["schema_version"] == reviewer_commit.INTENT_SCHEMA
    assert receipt["schema_version"] == reviewer_commit.RECEIPT_SCHEMA
    assert intent["wave_commit_transaction_id"] == reviewer_index_row[
        "wave_commit_transaction_id"
    ]
    assert receipt["wave_commit_transaction_id"] == reviewer_index_row[
        "wave_commit_transaction_id"
    ]
    review_packets_root = Path(build_inputs["review_packets_root_path"])
    assert record["reviewer_provenance"][
        "review_packets_tree_canonical_sha256"
    ] == reviewer_provenance.review_packets_tree_canonical_sha256(
        review_packets_root
    )


def test_finalization_rejects_reviewer_guard_from_earlier_authorization_window(
    tmp_path, inventory,
):
    cell = next(
        dict(candidate) for candidate in inventory.judgment_cells
        if candidate["query_budget"] == 1
    )
    raw_query = "Is the fixture fact explicitly stated?"
    build_inputs = _complete_finalization_inputs(
        tmp_path,
        inventory,
        observed_cell=cell,
        query_responses=(raw_query, "DONE"),
        checker_response="reject",
    )
    build_inputs["authorization_approved_at_utc"] = (
        "2026-08-29T13:00:00.500000Z")
    _rewrite_provider_event_timestamps(
        Path(build_inputs["usage_ledger_path"]),
        build_inputs["authorization_approved_at_utc"],
    )

    with pytest.raises(
        finalization.MainFinalizationError,
        match="reviewer dispatch guard authorization_approved_at_utc drifted",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:41Z")


def test_finalization_rejects_reviewer_wave_recorded_after_finalization(
    tmp_path, inventory,
):
    cell = next(
        dict(candidate) for candidate in inventory.judgment_cells
        if candidate["query_budget"] == 1
    )
    raw_query = "Is the fixture fact explicitly stated?"
    build_inputs = _complete_finalization_inputs(
        tmp_path,
        inventory,
        observed_cell=cell,
        query_responses=(raw_query, "DONE"),
        checker_response="reject",
    )

    with pytest.raises(
        finalization.MainFinalizationError,
        match="reviewer wave was recorded after finalization",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:01Z")


def test_finalization_rejects_done_attempt_two_without_first_query_retry(
    tmp_path, inventory,
):
    cell = next(
        dict(candidate) for candidate in inventory.judgment_cells
        if candidate["query_budget"] == 1
    )
    build_inputs = _complete_finalization_inputs(
        tmp_path, inventory, observed_cell=cell)
    _append_provider_success(
        ledger_path=Path(build_inputs["usage_ledger_path"]),
        journal_path=Path(build_inputs["request_journal_path"]),
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response="DONE",
        call_role=finalization.JUDGE_QUERY_ROLE,
        attempt_suffix="orphan-query-2-done",
        query_index=0,
        query_attempt=2,
    )

    with pytest.raises(
        finalization.MainFinalizationError, match="provider-call schedule drifted",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:41Z")


def test_budget_zero_result_rejects_an_extra_provider_call(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    cell = dict(inventory.judgment_cells[0])
    assert cell["query_budget"] == 0
    _append_provider_success(
        ledger_path=Path(build_inputs["usage_ledger_path"]),
        journal_path=Path(build_inputs["request_journal_path"]),
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response="unexpected query",
        call_role=finalization.JUDGE_QUERY_ROLE,
        attempt_suffix="extra-b0-query",
    )
    with pytest.raises(finalization.MainFinalizationError, match="provider-call schedule"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:45Z")


def test_finalization_rejects_a_foreign_reviewer_decision_store(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    store = DualGateDecisionStore(build_inputs["artifact_paths"]["review_decisions"])
    raw = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fixture"
    store.commit("f" * 64, "ALLOW", "Allowed", "fixture", raw, "parsed")
    with pytest.raises(
        finalization.MainFinalizationError,
        match="does not exactly cover",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:50Z")


def test_finalization_rejects_foreign_run_journal_identity(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    _rewrite_journal_for_foreign_identity(
        Path(build_inputs["request_journal_path"]))
    with pytest.raises(finalization.MainFinalizationError, match="identity or hash chain"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:02:00Z")


@pytest.mark.parametrize("missing", ["journal", "ledger"])
def test_finalization_rejects_result_without_verdict_journal_and_ledger_join(
    tmp_path, inventory, missing,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    if missing == "journal":
        Path(build_inputs["request_journal_path"]).write_text("", encoding="utf-8")
    else:
        _truncate_ledger_to_genesis(Path(build_inputs["usage_ledger_path"]))
    with pytest.raises(finalization.MainFinalizationError, match="not empty"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:03:00Z")


def test_finalization_rejects_exact_current_spend_over_stage_cap(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    build_inputs["prior_reconciled_usd"] = "0"
    build_inputs["stage_cap_usd"] = "0.000014"
    with pytest.raises(finalization.MainFinalizationError, match="exceeds the stage cap"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:04:00Z")
    build_inputs["stage_cap_usd"] = 60.0
    with pytest.raises(finalization.MainFinalizationError, match="Decimal string"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:04:01Z")


@pytest.mark.parametrize("target", ["context", "unplanned"])
def test_finalization_rejects_provider_and_journal_calls_outside_observed_plan(
    tmp_path, inventory, target,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    if target == "context":
        cell = dict(inventory.judgment_cells[1])
        expected = "context-ineligible"
    else:
        cell = {
            **dict(inventory.judgment_cells[0]),
            "cell_key": "not-in-the-main-inventory",
        }
        expected = "unplanned"
    _append_provider_success(
        ledger_path=Path(build_inputs["usage_ledger_path"]),
        journal_path=Path(build_inputs["request_journal_path"]),
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response="foreign call",
        call_role=finalization.JUDGE_QUERY_ROLE,
        attempt_suffix=target,
    )
    with pytest.raises(finalization.MainFinalizationError, match=expected):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:05:00Z")


def test_finalization_rejects_forbidden_main_call_role(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    _append_provider_success(
        ledger_path=Path(build_inputs["usage_ledger_path"]),
        journal_path=Path(build_inputs["request_journal_path"]),
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=dict(inventory.judgment_cells[0]),
        response="legacy batch verdict",
        call_role="batch_verdict",
        attempt_suffix="forbidden-role",
    )
    with pytest.raises(finalization.MainFinalizationError, match="forbidden main call role"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:06:00Z")


def test_finalization_rejects_nonempty_reconciliation_and_artifact_drift(
    tmp_path, inventory,
):
    ledger_path = tmp_path / "usage.jsonl"
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    journal_path = tmp_path / "journal.jsonl"
    journal = RequestJournal(journal_path, execution_identity=JOURNAL_IDENTITY)
    metadata = {"cell_key": "cell", "call_role": "judge_verdict"}
    journal.put(journal_key(metadata), "e" * 64, "response")
    with pytest.raises(finalization.MainFinalizationError, match="not empty"):
        finalization._derive_clean_reconciliation(
            usage_ledger_path=ledger_path,
            request_journal_path=journal_path,
            journal_execution_identity=JOURNAL_IDENTITY,
        )

    build_inputs = _complete_finalization_inputs(tmp_path / "complete", inventory)
    cell = dict(inventory.judgment_cells[0])
    dirty_metadata = {
        "cell_key": cell["cell_key"],
        "call_role": finalization.JUDGE_QUERY_ROLE,
        "condition": cell["condition"],
    }
    dirty_journal = RequestJournal(
        build_inputs["request_journal_path"],
        execution_identity=str(build_inputs["journal_execution_identity"]),
    )
    dirty_journal.put(journal_key(dirty_metadata), "d" * 64, "unsettled response")
    with pytest.raises(finalization.MainFinalizationError, match="not empty"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:07:00Z")

    with pytest.raises(finalization.MainFinalizationError, match="fields drifted"):
        finalization._artifact_hashes({
            **build_inputs["artifact_paths"],
            "unexpected": build_inputs["result_store_path"],
        })


# --- amendment 14: bounded uncertain-spend tolerance ---


def _inject_failed_verdict_episode(ledger_path: Path) -> tuple[str, str]:
    """Rebuild the fixture ledger with one durable unknown-charge episode for the verdict.

    The failed episode precedes the settled success of the same logical call, carries a
    distinct attempt id, no usage, no response metadata, and its reservation stays booked
    as uncertain spend. Returns the failed attempt id and the verdict model.
    """
    raw_events = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
    chain_fields = {"ts", "ledger_id", "sequence", "prev_event_hash", "event_hash"}
    provider = [
        {k: v for k, v in event.items() if k not in chain_fields}
        for event in raw_events if event.get("status") != "ledger_genesis"]
    verdict_reservation = next(
        event for event in provider
        if event["status"] == "reserved"
        and event["metadata"].get("call_role") == "judge_verdict")
    failed_id = "captured-attempt-failed"
    failed_reservation = {
        **copy.deepcopy(verdict_reservation), "attempt_id": failed_id}
    failed_unknown = {
        **copy.deepcopy(failed_reservation),
        "status": "unknown_charge",
        "error": "Request timed out.",
    }
    position = provider.index(verdict_reservation)
    rebuilt = provider[:position] + [failed_reservation, failed_unknown] + provider[position:]
    ledger_path.unlink()
    api_client.usage_ledger_state_path(ledger_path).unlink()
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    _append_ledger_events(ledger_path, rebuilt)
    return failed_id, str(verdict_reservation["model"])


def test_finalization_tolerates_a_durable_unknown_charge_episode_under_the_policy(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    failed_id, model = _inject_failed_verdict_episode(Path(build_inputs["usage_ledger_path"]))
    policy = phase3_main_runtime_policies.load_and_validate_uncertain_spend_policy(REPO_ROOT)
    observed = dict(inventory.judgment_cells[0])

    # Without the policy the same artifacts fail closed exactly as before.
    with pytest.raises(finalization.MainFinalizationError, match="not empty"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:00:03Z")

    record = finalization.build_finalization_admission(
        **build_inputs,
        uncertain_spend_policy=policy,
        recorded_at_utc="2026-08-29T13:00:03Z",
    )
    accounting = record["accounting"]
    assert accounting["completion_label"] == "PASS_CONSERVATIVE_UNCERTAIN"
    assert accounting["current_uncertain_usd"] == "0.00002"
    assert Decimal(accounting["run_uncertain_ceiling_usd"]) == Decimal("100")
    assert accounting["uncertain_within_ceiling"] is True
    assert accounting["unknown_charge_attempt_count"] == 1
    assert accounting["unknown_charge_by_model"] == {
        model: {"events": 1, "uncertain_usd": "0.00002"}}
    assert accounting["within_stage_cap"] is True
    reconciliation = record["reconciliation"]
    assert reconciliation["status"] == "clean_with_tolerated_unknown_charges"
    assert reconciliation["unknown_charge_attempt_ids"] == [failed_id]
    assert [item["problem"] for item in reconciliation["ambiguous_dispatches"]] == [
        "unknown_charge"]
    assert reconciliation["unmatched_reservations"] == 0
    provenance = record["provider_provenance"]
    assert provenance["redispatched_logical_call_count"] == 1
    assert provenance["unknown_charge_episode_count"] == 1
    assert provenance["unknown_charge_episodes_by_model"] == {model: 1}
    assert provenance["provider_request_count"] == 1
    report = record["retry_report"]
    assert report["unknown_charge_events"] == 1
    assert report["redispatched_logical_calls"] == 1
    assert report["by_model"] == {model: {"events": 1, "uncertain_usd": "0.00002"}}
    assert report["by_role"] == {"judge_verdict": 1}
    assert report["by_condition"] == {observed["condition"]: 1}
    assert report["by_question"] == {observed["question_id"]: 1}
    assert "600 s" in report["procedure"]
    assert record["uncertain_spend_policy"]["policy_raw_sha256"] == policy["policy_raw_sha256"]
    assert Decimal(record["uncertain_spend_policy"]["run_uncertain_ceiling_usd"]) == Decimal("100")

    validated = finalization.validate_finalization_admission(
        record, **build_inputs, uncertain_spend_policy=policy)
    assert validated == record

    # A record that claims the clean status while carrying the finding is rejected.
    tampered = copy.deepcopy(record)
    tampered["reconciliation"]["status"] = "clean"
    with pytest.raises(finalization.MainFinalizationError, match="ambiguous_dispatches"):
        finalization.validate_finalization_admission(
            tampered, **build_inputs, uncertain_spend_policy=policy)
    # And the tolerant record is refused when no policy is bound.
    with pytest.raises(
        finalization.MainFinalizationError, match="not empty|status must be clean",
    ):
        finalization.validate_finalization_admission(record, **build_inputs)


def test_finalization_rejects_unknown_charge_beyond_the_policy_ceiling(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    _inject_failed_verdict_episode(Path(build_inputs["usage_ledger_path"]))
    policy = dict(phase3_main_runtime_policies.load_and_validate_uncertain_spend_policy(
        REPO_ROOT))
    policy["run_uncertain_ceiling_usd"] = 0.00001

    with pytest.raises(finalization.MainFinalizationError, match="exceeds the frozen policy"):
        finalization.build_finalization_admission(
            **build_inputs,
            uncertain_spend_policy=policy,
            recorded_at_utc="2026-08-29T13:00:03Z",
        )


@pytest.fixture
def accounting_authority(tmp_path, monkeypatch, request):
    """Exercise real recovery scope validation with an isolated signature boundary."""
    from rejudge import phase3_main_authorization as authority, phase3_main_recovery as recovery
    paths = {name: tmp_path / name for name in ("manifest", "authorization", "recovery", "validation", "run_log")}
    policy_limits_path = REPO_ROOT / "rejudge" / "phase3_v3_role_limits_r11_2026-09-06.json"
    bound_limits_path = (policy_limits_path.relative_to(REPO_ROOT)
                         if getattr(request, "param", None) == "relative_limits" else policy_limits_path.resolve())
    manifest = {
        "run_id": RUN_ID, "source_commit": "a" * 40,
        "runtime": {"model_ids": ["model-a", "model-b"]}, "seeds": {}, "inventory": {},
        "input_bindings": {"role_limits": {"path": str(bound_limits_path),
                                           "sha256": _raw_sha256(policy_limits_path)}},
        "spend": {"stage_cap_usd": "1100.00", "prior_reconciled_usd": PRIOR_RECONCILED_USD},
        "output_contract": {"artifact_root": str(tmp_path), "paths": {"run_log": str(paths["run_log"])}}}
    authorization = {"run_id": RUN_ID, "stage_cap_usd": "1100.00"}
    for name, value in (("manifest", manifest), ("authorization", authorization), ("validation", {"approved": 150})):
        paths[name].write_text(json.dumps(value), encoding="utf-8")
    Path(str(paths["authorization"]) + ".sig").write_bytes(b"test-only signature")
    grant = {
        "schema_version": recovery.RECOVERY_SCHEMA, "run_id": RUN_ID,
        "original_manifest_canonical_sha256": codex_reviewer_batch._canonical_sha256(manifest),
        "artifact_root": str(tmp_path), "original_manifest": recovery._file_binding(paths["manifest"]),
        "original_authorization": recovery._file_binding(paths["authorization"]),
        "original_authorization_signature": recovery._file_binding(str(paths["authorization"]) + ".sig"),
        "scientific_contract_sha256": codex_reviewer_batch._canonical_sha256(recovery._scientific_contract(manifest)),
        "stage_cap_usd": "1100.00", "execution_source_commit": "b" * 40,
        "provider_worker_concurrency": 8, "block_size": 16,
        "per_model_limits": {"model-a": 4, "model-b": 4},
        "initial_per_model_limits": {"model-a": 2, "model-b": 4},
        "concurrency_policy": recovery.CONCURRENCY_POLICY, "controls": recovery.RECOVERY_CONTROLS,
        "reason": "saved local closeout", "owner_instruction": "preserve the saved run",
        "recorded_at_utc": "2026-09-11T12:00:00Z", "validation_record": recovery._file_binding(paths["validation"])}
    grant["uncertain_spend_amendment"] = recovery._uncertain_ceiling_amendment("150.00", grant["validation_record"])
    paths["recovery"].write_text(json.dumps(grant), encoding="utf-8")
    Path(str(paths["recovery"]) + ".sig").write_bytes(b"test-only signature")
    signed = {paths["authorization"]: (paths["authorization"].read_bytes(), authority.OWNER_SIGNATURE_NAMESPACE),
              paths["recovery"]: (paths["recovery"].read_bytes(), recovery.RECOVERY_SIGNATURE_NAMESPACE)}
    calls = []
    def authenticate(path, *, signature_namespace=authority.OWNER_SIGNATURE_NAMESPACE):
        path = Path(path)
        calls.append((path, signature_namespace))
        if (signed.get(path) != (path.read_bytes(), signature_namespace)
                or Path(str(path) + ".sig").read_bytes() != b"test-only signature"):
            raise authority.MainAuthorizationSignatureError("fixture signature rejected changed bytes or namespace")
        return json.loads(path.read_bytes())
    monkeypatch.setattr(authority, "load_authenticated_owner_authorization", authenticate)
    event = {"event": "formal_main_resumed", "run_id": RUN_ID, "recovery_path": str(paths["recovery"]),
             "execution_source_commit": grant["execution_source_commit"],
             "recovery_manifest_sha256": codex_reviewer_batch._canonical_sha256(grant),
             "recovery_raw_sha256": _raw_sha256(paths["recovery"]),
             "recovery_signature_raw_sha256": _raw_sha256(Path(str(paths["recovery"]) + ".sig"))}
    paths["run_log"].write_text(json.dumps(event) + "\n", encoding="utf-8")
    predecessor = {"predecessor_run_id": "other-run", "predecessor_manifest_canonical_sha256": "c" * 64,
                   "accounted_spend_usd": "61.05617072"}
    def predecessor_loader(root, *, verify_ledger):
        assert Path(root) == REPO_ROOT and verify_ledger is True
        return predecessor
    monkeypatch.setattr(phase3_main_runtime_policies, "load_and_validate_predecessor_void_accounting", predecessor_loader)
    kwargs = dict(run_log_path=paths["run_log"], role_limits_path=policy_limits_path,
        role_limits_raw_sha256=_raw_sha256(policy_limits_path), run_id=RUN_ID,
        manifest_sha256=grant["original_manifest_canonical_sha256"],
        authorization_sha256=codex_reviewer_batch._canonical_sha256(authorization),
        authorization_raw_sha256=grant["original_authorization"]["raw_sha256"],
        authorization_signature_raw_sha256=grant["original_authorization_signature"]["raw_sha256"],
        prior_reconciled_usd=PRIOR_RECONCILED_USD, stage_cap_usd="1100.00", policy_required=True)
    return {"paths": paths, "kwargs": kwargs, "calls": calls, "event": event, "predecessor": predecessor}


def test_accounting_adapter_reopens_signed_policy_and_predecessor(accounting_authority):
    fixture = accounting_authority
    policy, predecessor = finalization._authenticated_runtime_accounting(**fixture["kwargs"])
    assert policy["run_uncertain_ceiling_usd"] == 150.0
    assert policy["policy_raw_sha256"] == phase3_main_runtime_policies.UNCERTAIN_SPEND_POLICY_RAW_SHA256
    assert predecessor == "61.05617072"
    assert {path for path, _ in fixture["calls"]} == {
        fixture["paths"]["recovery"], fixture["paths"]["authorization"]}


@pytest.mark.parametrize("accounting_authority", ["relative_limits"], indirect=True)
def test_accounting_adapter_resolves_signed_relative_limits_outside_checkout(
        accounting_authority, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    policy, predecessor = finalization._authenticated_runtime_accounting(**accounting_authority["kwargs"])
    assert policy["run_uncertain_ceiling_usd"] == 150.0
    assert predecessor == "61.05617072"


@pytest.mark.parametrize("field,value", [
    ("run_id", "another-run"), ("manifest_sha256", "d" * 64),
    ("authorization_sha256", "d" * 64), ("authorization_raw_sha256", "d" * 64),
    ("authorization_signature_raw_sha256", "d" * 64),
    ("role_limits_raw_sha256", "d" * 64), ("stage_cap_usd", "1200"),
    ("prior_reconciled_usd", "0")])
def test_accounting_adapter_rejects_other_authority(accounting_authority, field, value):
    with pytest.raises(finalization.MainFinalizationError, match="runtime accounting"):
        finalization._authenticated_runtime_accounting(**{**accounting_authority["kwargs"], field: value})


@pytest.mark.parametrize("target", ["validation", "recovery", "authorization"])
def test_accounting_adapter_rejects_mutated_authority_bytes(accounting_authority, target):
    path = accounting_authority["paths"][target]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(finalization.MainFinalizationError, match="runtime accounting"):
        finalization._authenticated_runtime_accounting(**accounting_authority["kwargs"])


def test_accounting_adapter_rejects_unbound_policy_file(accounting_authority, tmp_path, monkeypatch):
    root = tmp_path / "altered-source"
    path = root / phase3_main_runtime_policies.UNCERTAIN_SPEND_POLICY_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes((REPO_ROOT / phase3_main_runtime_policies.UNCERTAIN_SPEND_POLICY_RELATIVE_PATH).read_bytes() + b" ")
    monkeypatch.setattr(finalization, "__file__", str(root / "rejudge" / "phase3_main_finalization.py"))
    with pytest.raises(finalization.MainFinalizationError, match="runtime accounting"):
        finalization._authenticated_runtime_accounting(**accounting_authority["kwargs"])


def test_accounting_adapter_missing_authority_preserves_legacy_default(accounting_authority):
    fixture = accounting_authority
    fixture["paths"]["run_log"].write_bytes(b"")
    with pytest.raises(finalization.MainFinalizationError, match="lacks signed recovery authority"):
        finalization._authenticated_runtime_accounting(**fixture["kwargs"])
    assert finalization._authenticated_runtime_accounting(
        **{**fixture["kwargs"], "policy_required": False}) == (None, "0")


def test_accounting_adapter_rejects_self_predecessor(accounting_authority):
    accounting_authority["predecessor"]["predecessor_run_id"] = RUN_ID
    with pytest.raises(finalization.MainFinalizationError, match="own predecessor"):
        finalization._authenticated_runtime_accounting(**accounting_authority["kwargs"])


def test_bound_admission_forwards_authoritative_policy_and_predecessor(tmp_path, inventory, monkeypatch):
    build = _complete_finalization_inputs(tmp_path, inventory)
    _inject_failed_verdict_episode(Path(build["usage_ledger_path"]))
    policy = phase3_main_runtime_policies.load_and_validate_uncertain_spend_policy(REPO_ROOT)
    policy["run_uncertain_ceiling_usd"] = 150.0
    predecessor = "61.05617072"
    build["stage_cap_usd"] = "1100.00"
    record = finalization.build_finalization_admission(**build, uncertain_spend_policy=policy,
        voided_predecessor_usd=predecessor, recorded_at_utc="2026-08-29T13:00:03Z")
    def resolved(**kwargs):
        assert kwargs["run_id"] == RUN_ID and kwargs["stage_cap_usd"] == "1100.00"
        assert kwargs["run_log_path"] == build["artifact_paths"]["run_log"]
        return policy, predecessor
    monkeypatch.setattr(finalization, "_authenticated_runtime_accounting", resolved)
    kwargs = dict(inventory=inventory, expected_run_id=RUN_ID,
        expected_manifest_canonical_sha256=MANIFEST_SHA256,
        expected_authorization_canonical_sha256=AUTHORIZATION_SHA256,
        expected_authorization_raw_sha256=AUTHORIZATION_RAW_SHA256,
        expected_authorization_signature_raw_sha256=AUTHORIZATION_SIGNATURE_RAW_SHA256,
        authorization_approved_at_utc=AUTHORIZATION_APPROVED_AT_UTC,
        authorization_valid_until_utc=AUTHORIZATION_VALID_UNTIL_UTC,
        expected_result_store_path=build["result_store_path"], expected_analysis_pins_path=build["analysis_pins_path"],
        expected_context_blocklist_path=build["context_blocklist_path"],
        expected_manifest_output_paths={name: (tmp_path / filename).resolve()
            for name, filename in phase3_main_manifest.OUTPUT_FILENAMES.items()},
        expected_provider_input_paths=build["provider_input_paths"],
        expected_provider_input_raw_sha256s=build["provider_input_raw_sha256s"],
        expected_reviewer_input_paths=build["reviewer_input_paths"],
        expected_reviewer_input_raw_sha256s=build["reviewer_input_raw_sha256s"],
        expected_capacity_result_path=build["capacity_result_path"],
        expected_capacity_result_raw_sha256=build["expected_capacity_result_raw_sha256"],
        expected_capacity_dispatch_history_path=build["capacity_dispatch_history_path"],
        expected_capacity_dispatch_history_raw_sha256=build["expected_capacity_dispatch_history_raw_sha256"],
        expected_review_packets_root_path=build["review_packets_root_path"],
        expected_checker_model=finalization.DEFAULT_CHECKER_MODEL, expected_oracle_model=ORACLE_MODEL,
        expected_reviewer_model=REVIEWER_MODEL, expected_reviewer_reasoning_effort=REVIEWER_REASONING_EFFORT,
        expected_reviewer_concurrency=REVIEWER_CONCURRENCY, prior_reconciled_usd=PRIOR_RECONCILED_USD,
        stage_cap_usd="1100.00")
    assert finalization.validate_finalization_from_bound_artifacts(record, **kwargs) == record
    tampered = copy.deepcopy(record)
    tampered["uncertain_spend_policy"]["run_uncertain_ceiling_usd"] = "200"
    with pytest.raises(finalization.MainFinalizationError):
        finalization.validate_finalization_from_bound_artifacts(tampered, **kwargs)
