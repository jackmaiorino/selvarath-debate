from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_main_reviewer_commit as reviewer_commit
from rejudge import phase3_main_reviewer_provenance as reviewer_provenance
from rejudge.phase2_canary_live import (
    SUBAGENT_PAYLOAD_SEPARATOR,
    compose_subagent_prompt,
)
from rejudge.phase2_dual_gate import parse_reviewer_output, payload_hash
from rejudge.phase3_main_reviewer_provenance import (
    MainReviewerProvenanceError,
    verify_main_reviewer_provenance,
)
from rejudge.phase3_v3_live import RunLease
from scripts import codex_reviewer_batch
from scripts import phase3_main_review_capacity_preflight as capacity_preflight


RUN_ID = "phase3-main-test-run"
MANIFEST_SHA = "a" * 64
AUTHORIZATION_SHA = "b" * 64
AUTHORIZATION_RAW_SHA = "c" * 64
AUTHORIZATION_SIGNATURE_SHA = "d" * 64
APPROVED_AT = "2028-12-31T23:59:59+00:00"
DEADLINE = "2030-01-01T00:00:00+00:00"
FINALIZED_AT = "2029-01-01T01:00:00+00:00"
MODEL = "gpt-5.6-sol"
EFFORT = "high"
CONCURRENCY = 12
CLI_VERSION = "test-version"
HOST_IDENTITY = codex_reviewer_batch._host_identity()  # noqa: SLF001
FROZEN_PROMPT = "Review the payload under the frozen contract."
CLEAN_OUTPUT = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: Atomic factual claim."
MALFORMED_OUTPUT = "not a three-line ruling"


def _raw_sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, value: Any, *, indent: int | None = None) -> bytes:
    raw = (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=indent) + "\n"
    ).encode("utf-8")
    path.write_bytes(raw)
    return raw


def _capacity_workload() -> capacity_preflight.DerivedWorkload:
    variants = tuple(
        capacity_preflight.VariantPacket(
            rank_sha256=_raw_sha(f"rank-{index}".encode()),
            source_payload_sha256=_raw_sha(f"source-{index}".encode()),
            source_prompt_sha256=_raw_sha(f"old-{index}".encode()),
            variant_payload_sha256=_raw_sha(f"payload-{index}".encode()),
            variant_prompt_sha256=_raw_sha(f"prompt-{index}".encode()),
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
    plan: dict[str, Any],
    workload: capacity_preflight.DerivedWorkload,
    sequence: int,
    event: str,
    previous_hash: str,
) -> dict[str, Any]:
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
        "recorded_at_utc": f"2028-12-31T23:{40 + sequence:02d}:00Z",
        "prev_event_hash": previous_hash,
        "event_hash": "",
    }
    row["event_hash"] = capacity_preflight.dispatch_history_event_hash(row)
    return row


def _write_capacity_evidence(
    tmp_path: Path,
    *,
    plan: dict[str, Any],
    workload: capacity_preflight.DerivedWorkload,
) -> tuple[Path, bytes, Path, bytes]:
    history_rows: list[dict[str, Any]] = []
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
    history_path = tmp_path / "capacity_dispatch_history.jsonl"
    history_path.write_bytes(history_raw)

    environment = {
        "reviewer_cli_resolved_path": plan["reviewer_configuration"][
            "reviewer_cli_resolved_path"],
        "reviewer_cli_wrapper_raw_sha256": plan["reviewer_configuration"][
            "reviewer_cli_wrapper_raw_sha256"],
        "reviewer_cli_wrapper_byte_count": plan["reviewer_configuration"][
            "reviewer_cli_wrapper_byte_count"],
        "reviewer_cli_version": CLI_VERSION,
        "host_identity": HOST_IDENTITY,
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
                "raw_output": CLEAN_OUTPUT,
            } for item in items],
        })
    result = {
        "schema_version": capacity_preflight.RESULT_SCHEMA_VERSION,
        "attempt_number": 1,
        "cohort_number": 1,
        "attempt_id": "attempt-0001",
        "attempt_status": "complete",
        "interrupted": False,
        "dispatch_history_raw_sha256": _raw_sha(history_raw),
        "plan_canonical_sha256": capacity_preflight.canonical_sha256(plan),
        "cohort_records_canonical_sha256": workload.summary[
            "cohort_records_canonical_sha256s"][0],
        "reviewer_configuration": plan["reviewer_configuration"],
        "reviewer_configuration_canonical_sha256": (
            capacity_preflight.canonical_sha256(plan["reviewer_configuration"])),
        "measurement_environment": environment,
        "measurement_environment_canonical_sha256": (
            capacity_preflight.canonical_sha256(environment)),
        "started_at_utc": "2028-12-31T23:40:00Z",
        "completed_at_utc": "2029-01-01T00:00:00Z",
        "monotonic_started_seconds": 1000.0,
        "monotonic_completed_seconds": 1360.0,
        "elapsed_monotonic_seconds": 360.0,
        "waves": waves,
    }
    result_path = tmp_path / "capacity_result.json"
    result_raw = _write_json(result_path, result, indent=1)
    return result_path, result_raw, history_path, history_raw


def _events(commands: list[str]) -> bytes:
    rows: list[dict[str, Any]] = [
        {"type": "thread.started", "thread_id": "test-thread"},
        {"type": "turn.started"},
    ]
    rows.extend({
        "type": "item.completed",
        "item": {"type": "command_execution", "command": command},
    } for command in commands)
    rows.extend([
        {"type": "item.completed", "item": {"type": "agent_message"}},
        {"type": "turn.completed"},
    ])
    return "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")


def _worklist(items: list[dict[str, Any]]) -> dict[str, Any]:
    output = []
    for item in items:
        query = item["query"]
        candidate_a = item["candidate_a"]
        candidate_b = item["candidate_b"]
        payload = payload_hash(query, candidate_a, candidate_b)
        prompt = compose_subagent_prompt(
            FROZEN_PROMPT,
            query=query,
            candidate_a=candidate_a,
            candidate_b=candidate_b,
        )
        output.append({
            "payload_sha256": payload,
            "query": query,
            "candidate_a": candidate_a,
            "candidate_b": candidate_b,
            "subagent_prompt": prompt,
            "subagent_prompt_sha256": _raw_sha(prompt.encode("utf-8")),
        })
    return {
        "frozen_prompt_sha256": _raw_sha(FROZEN_PROMPT.encode("utf-8")),
        "separator": SUBAGENT_PAYLOAD_SEPARATOR,
        "items": output,
    }


def _receipt(
    *,
    packet: Path,
    cli_path: str,
    guard_cli_path: str,
    cli_raw_sha: str,
    cli_byte_count: int,
    ruling: str,
    commands: list[str],
    deadline: str = DEADLINE,
    batch_concurrency: int = CONCURRENCY,
    host_identity: str = HOST_IDENTITY,
    started_at_utc: str = "2029-01-01T00:00:00+00:00",
    completed_at_utc: str = "2029-01-01T00:00:01+00:00",
    dispatch_guard_path: Path,
    dispatch_guard_raw_sha256: str,
) -> dict[str, Any]:
    event_raw = _events(commands)
    normalized = ruling.strip().encode("utf-8")
    runner_path, runner_sha, runner_bytes = codex_reviewer_batch._batch_runner_identity()
    workdir = packet.parent / "deleted-isolated-workdir"
    output_file = workdir / "ruling.txt"
    invocation = {
        "argv": [
            cli_path,
            "exec",
            "-m",
            MODEL,
            "-c",
            f'model_reasoning_effort="{EFFORT}"',
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
        "model_requested": MODEL,
        "reasoning_effort_requested": EFFORT,
        "batch_concurrency": batch_concurrency,
        "codex_cli_argument": cli_path,
        "codex_cli_resolved_path": cli_path,
        "codex_cli_wrapper_raw_sha256": cli_raw_sha,
        "codex_cli_wrapper_byte_count": cli_byte_count,
        "codex_cli_version": CLI_VERSION,
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
        "host_identity": host_identity,
        "batch_runner_path": runner_path,
        "batch_runner_raw_sha256": runner_sha,
        "batch_runner_byte_count": runner_bytes,
    }
    outcome = {
        "authorization_deadline_utc": deadline,
        "deadline_active_before_dispatch": True,
        "dispatch_attempted": True,
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "timed_out": False,
        "process_exit_code": 0,
        "result_ok": True,
        "error": None,
        "commands": commands,
        "event_stream_errors": [],
        "normalized_ruling_raw_sha256": _raw_sha(normalized),
        "normalized_ruling_byte_count": len(normalized),
    }
    checked_at = datetime.fromisoformat(started_at_utc)
    dispatch_guard, guard, verified_prompt = (
        codex_reviewer_batch._evaluate_dispatch_guard_snapshot(  # noqa: SLF001
        dispatch_guard_path,
        dispatch_guard_raw_sha256,
        codex=guard_cli_path,
        model=MODEL,
        effort=EFFORT,
        concurrency=CONCURRENCY,
        packet_directory=packet.parent,
        output_path=packet.parent / "rulings.jsonl",
        selected_packet=packet,
        checked_at=checked_at,
    ))
    if dispatch_guard["verified"] is not True:
        fixture_valid_at = datetime(2029, 1, 1, tzinfo=timezone.utc)
        dispatch_guard, guard, verified_prompt = (
            codex_reviewer_batch._evaluate_dispatch_guard_snapshot(  # noqa: SLF001
                dispatch_guard_path,
                dispatch_guard_raw_sha256,
                codex=guard_cli_path,
                model=MODEL,
                effort=EFFORT,
                concurrency=CONCURRENCY,
                packet_directory=packet.parent,
                output_path=packet.parent / "rulings.jsonl",
                selected_packet=packet,
                checked_at=fixture_valid_at,
            )
        )
        assert dispatch_guard["verified"] is True
        dispatch_guard["checked_at_utc"] = checked_at.isoformat()
    assert guard is not None
    assert verified_prompt == packet.read_bytes()
    dispatch_guard["reservation"] = codex_reviewer_batch._reserve_guarded_dispatch(  # noqa: SLF001
        packet=packet,
        prompt_bytes=verified_prompt,
        guard=guard,
        guard_raw_sha256=dispatch_guard_raw_sha256,
        checked_at_utc=checked_at.isoformat(),
    )
    if "released_at_utc" in codex_reviewer_batch._DISPATCH_GUARD_EVIDENCE_FIELDS:  # noqa: SLF001
        dispatch_guard["released_at_utc"] = checked_at.isoformat()
    return codex_reviewer_batch._persist_invocation_evidence(
        packet=packet,
        prompt_bytes=packet.read_bytes(),
        invocation=invocation,
        outcome=outcome,
        event_stream_raw=event_raw,
        stderr_raw=b"",
        ruling_raw=(ruling + "\n").encode("utf-8"),
        dispatch_guard=dispatch_guard,
    )


def _build_fixture(
    tmp_path: Path,
    *,
    waves: list[tuple[int, list[dict[str, Any]]]] | None = None,
    reverse_decisions: bool = False,
    invocation_cli_path: str | None = None,
    invocation_deadline: str = DEADLINE,
    invocation_concurrency: int = CONCURRENCY,
    invocation_host_identity: str = HOST_IDENTITY,
    invocation_started_at_utc: str = "2029-01-01T00:00:00+00:00",
    invocation_completed_at_utc: str = "2029-01-01T00:00:01+00:00",
    wave_recorded_at_utcs: list[str] | None = None,
) -> dict[str, Any]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    waves = waves if waves is not None else [
        (1, [
            {
                "query": "The threshold is 24 votes.",
                "candidate_a": "It is 24.",
                "candidate_b": "It is 30.",
                "raw_output": CLEAN_OUTPUT,
                "commands": [],
            },
            {
                "query": "The meeting occurs in Month 3.",
                "candidate_a": "Month 3.",
                "candidate_b": "Month 6.",
                "raw_output": MALFORMED_OUTPUT,
                "commands": [],
            },
        ]),
    ]
    prompt_path = tmp_path / "reviewer_prompt.json"
    prompt_raw = _write_json(prompt_path, {
        "schema_version": "phase2_reviewer_prompt_v1",
        "prompt": FROZEN_PROMPT,
        "prompt_sha256": _raw_sha(FROZEN_PROMPT.encode("utf-8")),
    }, indent=1)
    failure_policy_path = tmp_path / "reviewer_failure_policy.json"
    failure_policy_raw = _write_json(failure_policy_path, {
        "schema_version": "phase3_main_reviewer_failure_policy_v1",
        "stage": "main",
        "status": "offline_contract_pending_exact_manifest_authorization",
        "reviewer_prompt_sha256": _raw_sha(FROZEN_PROMPT.encode("utf-8")),
        "overrides_reviewer_prompt_field": "failure_rule",
        "parse_failure": "commit_malformed_non_allow",
        "tool_use": "commit_evidenced_reviewer_error_non_allow",
        "timeout_or_unavailability": (
            "abort_wave_without_decision_and_void_no_resume_identity"),
        "execution_authorized": False,
        "provider_calls_authorized": False,
    }, indent=1)
    cli = tmp_path / "codex.cmd"
    cli.write_bytes(f"@echo {CLI_VERSION}\r\n".encode("utf-8"))
    cli_path = cli.resolve().as_posix()
    cli_raw_sha = _raw_sha(cli.read_bytes())
    capacity_path = tmp_path / "capacity.json"
    workload = _capacity_workload()
    capacity_plan = {
        "schema_version": "phase3_main_review_capacity_preflight_plan_v1",
        "reviewer_configuration": {
            "model": MODEL,
            "reasoning_effort": EFFORT,
            "reviewer_cli_binary": "codex.cmd",
            "concurrency": CONCURRENCY,
            "reviewer_cli_resolved_path": cli_path,
            "reviewer_cli_wrapper_raw_sha256": cli_raw_sha,
            "reviewer_cli_wrapper_byte_count": len(cli.read_bytes()),
            "fresh_ephemeral_context_per_packet": True,
            "tool_use_permitted": False,
            "actual_capacity_wave_size": 60,
            "wave_pending_payload_limit": 64,
        },
        "workload": dict(workload.summary),
        "capacity_thresholds": capacity_preflight._expected_thresholds(),
        "dispatch_history_contract": {
            "required_path": str(
                (tmp_path / "capacity_dispatch_history.jsonl").resolve()),
            "anchor_directory": str((tmp_path / "capacity_anchors").resolve()),
            "interruption_evidence_root": str(
                (tmp_path / "capacity_interruptions").resolve()),
            "required_initial_raw_sha256": _raw_sha(b""),
            "anchor_validation_required": False,
        },
        "source_locations": {
            "sealed_archive": str((tmp_path / "capacity_source").resolve()),
            "finalization_record": str(
                (tmp_path / "capacity_source_finalization.json").resolve()),
        },
        "validity": {"valid_for_hours": 24},
    }
    capacity_raw = _write_json(capacity_path, capacity_plan, indent=1)
    (
        capacity_result_path,
        capacity_result_raw,
        capacity_history_path,
        capacity_history_raw,
    ) = _write_capacity_evidence(
        tmp_path,
        plan=capacity_plan,
        workload=workload,
    )
    authorization_path = tmp_path / "authorization.json"
    authorization_raw = _write_json(authorization_path, {
        "approved_at_utc": APPROVED_AT,
        "valid_until_utc": invocation_deadline,
    }, indent=1)
    authorization_signature_path = tmp_path / "authorization.json.sig"
    authorization_signature_raw = b"fixture-owner-signature\n"
    authorization_signature_path.write_bytes(authorization_signature_raw)
    authorization_sha = capacity_preflight.canonical_sha256(
        json.loads(authorization_raw.decode("utf-8")))
    authorization_raw_sha = _raw_sha(authorization_raw)
    authorization_signature_sha = _raw_sha(authorization_signature_raw)
    root = (tmp_path / "packets").resolve()
    root.mkdir()
    reviewer_index_path = tmp_path / "reviewer_index.jsonl"
    reviewer_worklist_path = tmp_path / "reviewer_worklist.json"
    decisions_path = tmp_path / "decisions.jsonl"
    reviewer_index_path.write_bytes(b"")
    decisions_path.write_bytes(b"")
    run_lease_path = (tmp_path / "reviewer-run.lock").resolve()
    expected_payloads: list[str] = []
    last_worklist: dict[str, Any] | None = None
    for wave_position, (wave, specs) in enumerate(waves):
        worklist = _worklist(specs)
        last_worklist = worklist
        items = worklist["items"]
        packet_dir = root / f"wave-{wave:03d}"
        packet_dir.mkdir()
        worklist_raw = _write_json(packet_dir / "WORKLIST.json", worklist, indent=1)
        def guard_binding(path: Path) -> dict[str, Any]:
            raw = path.read_bytes()
            return {
                "path": path.resolve().as_posix(),
                "raw_sha256": _raw_sha(raw),
                "byte_count": len(raw),
            }

        index_items = []
        packet_bindings = []
        for position, item in enumerate(items, 1):
            payload = item["payload_sha256"]
            name = f"{position:05d}_{payload[:12]}.txt"
            packet = packet_dir / name
            packet_prompt_raw = item["subagent_prompt"].encode("utf-8")
            packet.write_bytes(packet_prompt_raw)
            index_items.append({
                "n": position,
                "file": name,
                "payload_sha256": payload,
                "prompt_sha256": item["subagent_prompt_sha256"],
            })
            packet_bindings.append({
                "file": name,
                "payload_sha256": payload,
                "prompt_sha256": item["subagent_prompt_sha256"],
                "byte_count": len(packet_prompt_raw),
            })
        index_raw = _write_json(packet_dir / "INDEX.json", {
            "count": len(index_items),
            "items": index_items,
        })
        rulings_path = packet_dir / "rulings.jsonl"
        rulings_path.write_bytes(b"")
        runner_path = Path(codex_reviewer_batch._batch_runner_identity()[0])  # noqa: SLF001
        dispatch_guard = {
            "schema_version": codex_reviewer_batch.DISPATCH_GUARD_SCHEMA,
            "run_id": RUN_ID,
            "manifest_canonical_sha256": MANIFEST_SHA,
            "authorization_canonical_sha256": authorization_sha,
            "authorization_approved_at_utc": datetime.fromisoformat(
                APPROVED_AT).isoformat(),
            "authorization_valid_until_utc": datetime.fromisoformat(
                invocation_deadline).isoformat(),
            "capacity_completed_at_utc": "2029-01-01T00:00:00+00:00",
            "capacity_expires_at_utc": "2029-01-02T00:00:00+00:00",
            "reviewer_model": MODEL,
            "reviewer_reasoning_effort": EFFORT,
            "reviewer_concurrency": CONCURRENCY,
            "reviewer_cli_version": CLI_VERSION,
            "capacity_host_identity": HOST_IDENTITY,
            "packet_directory": packet_dir.resolve().as_posix(),
            "output_path": rulings_path.resolve().as_posix(),
            "packet_bindings": packet_bindings,
            "artifact_bindings": {
                "authorization": guard_binding(authorization_path),
                "authorization_signature": guard_binding(
                    authorization_signature_path),
                "capacity_plan": guard_binding(capacity_path),
                "capacity_result": guard_binding(capacity_result_path),
                "capacity_dispatch_history": guard_binding(capacity_history_path),
                "reviewer_cli_wrapper": guard_binding(cli),
                "packet_index": guard_binding(packet_dir / "INDEX.json"),
                "worklist_snapshot": guard_binding(packet_dir / "WORKLIST.json"),
                "batch_runner": guard_binding(runner_path),
            },
        }
        dispatch_guard_raw = _write_json(
            packet_dir / codex_reviewer_batch.DISPATCH_GUARD_FILENAME,
            dispatch_guard,
            indent=1,
        )
        dispatch_guard_raw_sha = _raw_sha(dispatch_guard_raw)
        rulings = []
        commit_entries = []
        counts = {"parsed": 0, "malformed": 0, "reviewer_error": 0}
        for position, (spec, item, index_item) in enumerate(
            zip(specs, items, index_items), 1
        ):
            payload = item["payload_sha256"]
            expected_payloads.append(payload)
            packet = packet_dir / str(index_item["file"])
            commands = list(spec.get("commands", []))
            raw_output = str(spec["raw_output"])
            evidence = _receipt(
                packet=packet,
                cli_path=invocation_cli_path or cli_path,
                guard_cli_path=cli_path,
                cli_raw_sha=cli_raw_sha,
                cli_byte_count=len(cli.read_bytes()),
                ruling=raw_output,
                commands=commands,
                deadline=invocation_deadline,
                batch_concurrency=invocation_concurrency,
                host_identity=invocation_host_identity,
                started_at_utc=invocation_started_at_utc,
                completed_at_utc=invocation_completed_at_utc,
                dispatch_guard_path=(
                    packet_dir / codex_reviewer_batch.DISPATCH_GUARD_FILENAME),
                dispatch_guard_raw_sha256=dispatch_guard_raw_sha,
            )
            if spec.get("as_reviewer_error"):
                synthetic = spec.get("synthetic_output") or (
                    "TOOL_USE_DETECTED: reviewer issued "
                    f"{len(commands)} command(s); ruling discarded as non-blind. "
                    f"first={commands[0]!r}"
                    if commands else "TOOL_USE_DETECTED: no command evidence"
                )
                ruling_row = {
                    "payload_sha256": payload,
                    "status": "reviewer_error",
                    "raw_output": synthetic,
                    "evidence": evidence,
                }
                commit_entry = {
                    "payload_sha256": payload,
                    "raw_output": synthetic,
                    "status": "reviewer_error",
                }
                counts["reviewer_error"] += 1
            else:
                ruling_row = {
                    "payload_sha256": payload,
                    "raw_output": raw_output,
                    "prompt_sha256": item["subagent_prompt_sha256"],
                    "tool_uses": spec.get("tool_uses", 0),
                    "evidence": evidence,
                }
                label, _clause, _rationale = parse_reviewer_output(raw_output)
                status = "parsed" if label is not None else "malformed"
                commit_entry = {
                    "payload_sha256": payload,
                    "raw_output": raw_output,
                    "prompt_sha256": item["subagent_prompt_sha256"],
                }
                counts[status] += 1
            rulings.append(ruling_row)
            commit_entries.append(commit_entry)
        rulings_raw = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rulings
        ).encode("utf-8")
        rulings_path.write_bytes(rulings_raw)
        default_wave_recorded_at = (
            datetime(2029, 1, 1, tzinfo=timezone.utc)
            + timedelta(minutes=wave_position + 1)
        ).isoformat()
        wave_row = {
            "schema_version": reviewer_commit.REVIEWER_WAVE_SCHEMA,
            "run_id": RUN_ID,
            "manifest_canonical_sha256": MANIFEST_SHA,
            "authorization_canonical_sha256": authorization_sha,
            "authorization_raw_sha256": authorization_raw_sha,
            "authorization_signature_raw_sha256": authorization_signature_sha,
            "capacity_plan_raw_sha256": _raw_sha(capacity_raw),
            "wave": wave,
            "recorded_at_utc": (
                wave_recorded_at_utcs[wave_position]
                if wave_recorded_at_utcs is not None
                else default_wave_recorded_at
            ),
            "payload_count": len(items),
            "packet_directory": packet_dir.as_posix(),
            "worklist_snapshot_raw_sha256": _raw_sha(worklist_raw),
            "packet_index_raw_sha256": _raw_sha(index_raw),
            "dispatch_guard_raw_sha256": dispatch_guard_raw_sha,
            "rulings_raw_sha256": _raw_sha(rulings_raw),
            "reviewer_model": MODEL,
            "reviewer_reasoning_effort": EFFORT,
            "reviewer_concurrency": CONCURRENCY,
            "reviewer_cli_resolved_path": cli_path,
            "commit_counts": counts,
            "run_lease_path": run_lease_path.as_posix(),
        }
        wave_row["wave_commit_transaction_id"] = (
            reviewer_commit.derive_wave_commit_transaction_id(
                run_id=RUN_ID,
                manifest_canonical_sha256=MANIFEST_SHA,
                wave=wave,
                run_lease_path=run_lease_path,
                evidence_bindings=(
                    reviewer_commit.evidence_bindings_from_wave_row(wave_row)),
            )
        )
        ordered_entries = (
            list(reversed(commit_entries)) if reverse_decisions else commit_entries)
        with RunLease(run_lease_path) as held_lease:
            committed = reviewer_commit.commit_reviewer_wave(
                transaction_directory=packet_dir,
                decision_store_path=decisions_path,
                reviewer_index_path=reviewer_index_path,
                run_id=RUN_ID,
                manifest_canonical_sha256=MANIFEST_SHA,
                wave=wave,
                run_lease_path=run_lease_path,
                held_run_lease=held_lease,
                worklist=worklist,
                entries=ordered_entries,
                reviewer_index_row=wave_row,
            )
        assert committed.commit_counts == counts
    if last_worklist is None:
        last_worklist = _worklist([])
    _write_json(reviewer_worklist_path, last_worklist, indent=1)
    return {
        "reviewer_index_path": reviewer_index_path,
        "reviewer_worklist_path": reviewer_worklist_path,
        "review_packets_root": root,
        "reviewer_prompt_path": prompt_path,
        "expected_reviewer_prompt_raw_sha256": _raw_sha(prompt_raw),
        "reviewer_failure_policy_path": failure_policy_path,
        "expected_reviewer_failure_policy_raw_sha256": _raw_sha(
            failure_policy_raw),
        "capacity_plan_path": capacity_path,
        "expected_capacity_plan_raw_sha256": _raw_sha(capacity_raw),
        "capacity_result_path": capacity_result_path,
        "expected_capacity_result_raw_sha256": _raw_sha(capacity_result_raw),
        "capacity_dispatch_history_path": capacity_history_path,
        "expected_capacity_dispatch_history_raw_sha256": _raw_sha(
            capacity_history_raw),
        "expected_run_id": RUN_ID,
        "expected_manifest_canonical_sha256": MANIFEST_SHA,
        "expected_authorization_canonical_sha256": authorization_sha,
        "expected_authorization_raw_sha256": authorization_raw_sha,
        "expected_authorization_signature_raw_sha256": authorization_signature_sha,
        "expected_reviewer_model": MODEL,
        "expected_reviewer_reasoning_effort": EFFORT,
        "expected_reviewer_concurrency": CONCURRENCY,
        "expected_reviewer_cli_resolved_path": cli_path,
        "expected_authorization_approved_at_utc": APPROVED_AT,
        "expected_authorization_deadline_utc": DEADLINE,
        "expected_finalization_recorded_at_utc": FINALIZED_AT,
        "max_passes": 5,
        "decisions_path": decisions_path,
        "expected_reviewed_payload_sha256s": expected_payloads,
    }


def test_verifies_exact_waves_with_gaps_and_decision_semantics(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[
        (1, [{
            "query": "The threshold is 24 votes.",
            "candidate_a": "24.",
            "candidate_b": "30.",
            "raw_output": CLEAN_OUTPUT,
            "commands": [],
        }]),
        (3, [{
            "query": "The council meets in Month 3.",
            "candidate_a": "Month 3.",
            "candidate_b": "Month 6.",
            "raw_output": MALFORMED_OUTPUT,
            "commands": [],
        }]),
    ])

    result = verify_main_reviewer_provenance(**inputs)

    assert result == {
        "reviewer_provenance_status": "reviewer_provenance_verified",
        "reviewer_wave_count": 2,
        "reviewed_payload_count": 2,
        "parsed_decision_count": 1,
        "malformed_decision_count": 1,
        "reviewer_error_decision_count": 0,
        "review_packets_root": Path(inputs["review_packets_root"]).as_posix(),
        "review_packets_tree_canonical_sha256": result[
            "review_packets_tree_canonical_sha256"],
        "reviewer_index_raw_sha256": _raw_sha(
            Path(inputs["reviewer_index_path"]).read_bytes()),
        "reviewer_worklist_raw_sha256": _raw_sha(
            Path(inputs["reviewer_worklist_path"]).read_bytes()),
        "reviewer_prompt_raw_sha256": inputs[
            "expected_reviewer_prompt_raw_sha256"],
        "reviewer_failure_policy_raw_sha256": inputs[
            "expected_reviewer_failure_policy_raw_sha256"],
        "capacity_plan_raw_sha256": inputs[
            "expected_capacity_plan_raw_sha256"],
        "capacity_result_raw_sha256": inputs[
            "expected_capacity_result_raw_sha256"],
        "capacity_dispatch_history_raw_sha256": inputs[
            "expected_capacity_dispatch_history_raw_sha256"],
    }
    assert len(result["review_packets_tree_canonical_sha256"]) == 64


@pytest.mark.parametrize(
    ("artifact", "match"),
    [
        ("intent", "wave commit intent is unavailable"),
        ("receipt", "wave commit receipt is unavailable"),
    ],
)
def test_rejects_a_missing_wave_commit_artifact(tmp_path, artifact, match):
    inputs = _build_fixture(tmp_path)
    packet_dir = next(Path(inputs["review_packets_root"]).glob("wave-*"))
    transaction_paths = reviewer_commit.reviewer_wave_transaction_paths(packet_dir)
    getattr(transaction_paths, artifact).unlink()

    with pytest.raises(MainReviewerProvenanceError, match=match):
        verify_main_reviewer_provenance(**inputs)


@pytest.mark.parametrize(
    ("store_name", "match"),
    [
        ("decisions", "decision prior state is discontinuous"),
        ("reviewer_index", "index prior state is discontinuous"),
    ],
)
def test_rejects_cross_wave_transaction_discontinuity(
    tmp_path, monkeypatch, store_name, match,
):
    inputs = _build_fixture(tmp_path, waves=[
        (1, [{
            "query": "The threshold is 24 votes.",
            "candidate_a": "24.",
            "candidate_b": "30.",
            "raw_output": CLEAN_OUTPUT,
            "commands": [],
        }]),
        (3, [{
            "query": "The council meets in Month 3.",
            "candidate_a": "Month 3.",
            "candidate_b": "Month 6.",
            "raw_output": MALFORMED_OUTPUT,
            "commands": [],
        }]),
    ])
    original = reviewer_commit.validate_reviewer_wave_commit

    def discontinuous(**kwargs):
        result = original(**kwargs)
        if kwargs["wave"] != 3:
            return result
        binding = replace(
            getattr(result, store_name),
            prior_raw_sha256="0" * 64,
        )
        return replace(result, **{store_name: binding})

    monkeypatch.setattr(
        reviewer_commit, "validate_reviewer_wave_commit", discontinuous)

    with pytest.raises(MainReviewerProvenanceError, match=match):
        verify_main_reviewer_provenance(**inputs)


def test_transaction_binds_the_exact_raw_reviewer_index_row(
    tmp_path, monkeypatch,
):
    inputs = _build_fixture(tmp_path)
    original = reviewer_commit.validate_reviewer_wave_commit

    def wrong_row_bytes(**kwargs):
        result = original(**kwargs)
        binding = replace(
            result.reviewer_index,
            append_bytes=result.reviewer_index.append_bytes + b" ",
        )
        return replace(result, reviewer_index=binding)

    monkeypatch.setattr(
        reviewer_commit, "validate_reviewer_wave_commit", wrong_row_bytes)

    with pytest.raises(MainReviewerProvenanceError, match="exact index row bytes"):
        verify_main_reviewer_provenance(**inputs)


def _rewrite_single_wave_guard(inputs: dict[str, Any], mutate) -> None:
    packet_dir = next(Path(inputs["review_packets_root"]).glob("wave-*"))
    guard_path = packet_dir / codex_reviewer_batch.DISPATCH_GUARD_FILENAME
    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    mutate(guard, packet_dir)
    guard_raw = _write_json(guard_path, guard, indent=1)
    index_path = Path(inputs["reviewer_index_path"])
    row = json.loads(index_path.read_text(encoding="utf-8"))
    row["dispatch_guard_raw_sha256"] = _raw_sha(guard_raw)
    index_path.write_text(
        json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("reviewer_model", "reviewer_model drifted"),
        ("reviewer_reasoning_effort", "reviewer_reasoning_effort drifted"),
        ("reviewer_concurrency", "reviewer_concurrency drifted"),
        ("reviewer_cli_version", "reviewer_cli_version drifted"),
        ("capacity_host_identity", "capacity_host_identity drifted"),
        ("packet_directory", "packet_directory drifted"),
        ("output_path", "output_path drifted"),
        ("packet_bindings", "packet binding 1 drifted"),
        ("worklist_snapshot", "worklist_snapshot hash drifted"),
        ("packet_index", "packet_index hash drifted"),
        ("batch_runner", "batch_runner hash drifted"),
    ],
)
def test_rejects_every_new_guard_v2_binding(tmp_path, target, message):
    inputs = _build_fixture(tmp_path)

    def mutate(guard, packet_dir):
        if target in {
            "reviewer_model",
            "reviewer_reasoning_effort",
            "reviewer_cli_version",
            "capacity_host_identity",
        }:
            guard[target] = f"wrong-{target}"
        elif target == "reviewer_concurrency":
            guard[target] += 1
        elif target == "packet_directory":
            guard[target] = packet_dir.parent.resolve().as_posix()
        elif target == "output_path":
            guard[target] = (packet_dir / "WORKLIST.json").resolve().as_posix()
        elif target == "packet_bindings":
            guard[target][0]["byte_count"] += 1
        else:
            guard["artifact_bindings"][target]["raw_sha256"] = "0" * 64

    _rewrite_single_wave_guard(inputs, mutate)
    with pytest.raises(MainReviewerProvenanceError, match=message):
        verify_main_reviewer_provenance(**inputs)


def test_rejects_tampered_durable_dispatch_reservation(tmp_path):
    inputs = _build_fixture(tmp_path)
    reservation = next(Path(inputs["review_packets_root"]).glob(
        "wave-*/.reviewer_dispatch_reservations/*.json"))
    reservation.write_bytes(reservation.read_bytes() + b" ")

    with pytest.raises(
        MainReviewerProvenanceError,
        match="invocation evidence is invalid: reviewer dispatch reservation bytes drifted",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_rejects_reviewer_receipt_started_before_authorization(tmp_path):
    inputs = _build_fixture(tmp_path)
    inputs["expected_authorization_approved_at_utc"] = (
        "2029-01-01T00:00:00.500000+00:00")

    with pytest.raises(
        MainReviewerProvenanceError,
        match="dispatch guard authorization_approved_at_utc drifted",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_rejects_reviewer_receipt_completed_after_wave_record(tmp_path):
    inputs = _build_fixture(
        tmp_path,
        wave_recorded_at_utcs=["2029-01-01T00:00:00+00:00"],
    )

    with pytest.raises(
        MainReviewerProvenanceError,
        match="reviewer invocation falls outside the authorized wave timeline",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_reviewer_wave_recorded_times_are_strictly_increasing(tmp_path):
    inputs = _build_fixture(
        tmp_path,
        waves=[
            (1, [{
                "query": "The threshold is 24 votes.",
                "candidate_a": "24.",
                "candidate_b": "30.",
                "raw_output": CLEAN_OUTPUT,
                "commands": [],
            }]),
            (2, [{
                "query": "The council meets in Month 3.",
                "candidate_a": "Month 3.",
                "candidate_b": "Month 6.",
                "raw_output": CLEAN_OUTPUT,
                "commands": [],
            }]),
        ],
    )
    index_path = Path(inputs["reviewer_index_path"])
    rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[1]["recorded_at_utc"] = rows[0]["recorded_at_utc"]
    index_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        MainReviewerProvenanceError,
        match="reviewer wave recorded times must be strictly increasing",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_reviewer_wave_must_not_postdate_finalization(tmp_path):
    inputs = _build_fixture(tmp_path)
    inputs["expected_finalization_recorded_at_utc"] = (
        "2029-01-01T00:00:30+00:00")

    with pytest.raises(
        MainReviewerProvenanceError,
        match="reviewer wave was recorded after finalization",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_zero_wave_provenance_requires_empty_root_worklist_and_store(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[])

    result = verify_main_reviewer_provenance(**inputs)

    assert result["reviewer_wave_count"] == 0
    assert result["reviewed_payload_count"] == 0
    assert result["review_packets_tree_canonical_sha256"] == _raw_sha(b"[]")


def test_zero_wave_provenance_rejects_an_unindexed_root_child(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[])
    (Path(inputs["review_packets_root"]) / "unindexed").mkdir()

    with pytest.raises(MainReviewerProvenanceError, match="unindexed child"):
        verify_main_reviewer_provenance(**inputs)


@pytest.mark.parametrize(
    ("target", "match"),
    [
        ("reviewer_index", "reviewer index changed while reviewer provenance"),
        (
            "reviewer_worklist",
            "final reviewer worklist changed while reviewer provenance",
        ),
        ("review_packets_tree", "review packet tree changed while reviewer provenance"),
    ],
)
def test_rechecks_reviewer_snapshots_after_semantic_validation(
    tmp_path, monkeypatch, target, match,
):
    inputs = _build_fixture(tmp_path, waves=[])
    original = reviewer_provenance._tree_digest
    calls = 0

    def digest_then_mutate(root):
        nonlocal calls
        digest = original(root)
        if calls == 0:
            calls += 1
            if target == "reviewer_index":
                path = Path(inputs["reviewer_index_path"])
                path.write_bytes(path.read_bytes() + b" ")
            elif target == "reviewer_worklist":
                path = Path(inputs["reviewer_worklist_path"])
                path.write_bytes(path.read_bytes() + b" ")
            else:
                (Path(inputs["review_packets_root"]) / "late-tamper.txt").write_text(
                    "tamper", encoding="utf-8")
        return digest

    monkeypatch.setattr(reviewer_provenance, "_tree_digest", digest_then_mutate)
    with pytest.raises(MainReviewerProvenanceError, match=match):
        verify_main_reviewer_provenance(**inputs)


def test_reviewer_failure_policy_semantic_drift_is_rejected(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[])
    policy_path = Path(inputs["reviewer_failure_policy_path"])
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["timeout_or_unavailability"] = "commit_reviewer_error"
    policy_raw = _write_json(policy_path, policy, indent=1)
    inputs["expected_reviewer_failure_policy_raw_sha256"] = _raw_sha(policy_raw)

    with pytest.raises(
        MainReviewerProvenanceError,
        match="failure policy differs from the exact Phase 3 contract",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_reviewer_concurrency_is_bound_across_capacity_wave_and_receipt(tmp_path):
    capacity_inputs = _build_fixture(tmp_path / "capacity", waves=[])
    capacity_path = Path(capacity_inputs["capacity_plan_path"])
    capacity = json.loads(capacity_path.read_text(encoding="utf-8"))
    capacity["reviewer_configuration"]["concurrency"] = CONCURRENCY - 1
    capacity_raw = _write_json(capacity_path, capacity, indent=1)
    capacity_inputs["expected_capacity_plan_raw_sha256"] = _raw_sha(capacity_raw)
    with pytest.raises(
        MainReviewerProvenanceError,
        match="capacity plan reviewer concurrency drifted",
    ):
        verify_main_reviewer_provenance(**capacity_inputs)

    wave_inputs = _build_fixture(tmp_path / "wave")
    index_path = Path(wave_inputs["reviewer_index_path"])
    wave_row = json.loads(index_path.read_text(encoding="utf-8"))
    wave_row["reviewer_concurrency"] = CONCURRENCY - 1
    index_path.write_text(
        json.dumps(wave_row, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        MainReviewerProvenanceError,
        match="reviewer index row 1 reviewer_concurrency drifted",
    ):
        verify_main_reviewer_provenance(**wave_inputs)

    receipt_inputs = _build_fixture(
        tmp_path / "receipt", invocation_concurrency=CONCURRENCY - 1)
    with pytest.raises(
        MainReviewerProvenanceError,
        match="reviewer evidence concurrency differs from the expected runtime",
    ):
        verify_main_reviewer_provenance(**receipt_inputs)


def test_capacity_result_semantic_forgery_is_rejected(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[])
    result_path = Path(inputs["capacity_result_path"])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["waves"][0]["failure_counts"]["timeouts"] = 1
    result_raw = _write_json(result_path, result, indent=1)
    inputs["expected_capacity_result_raw_sha256"] = _raw_sha(result_raw)

    with pytest.raises(
        MainReviewerProvenanceError,
        match="capacity evidence failed semantic validation: wave 1 records a failed dispatch",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_capacity_dispatch_history_completed_failure_is_rejected(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[])
    history_path = Path(inputs["capacity_dispatch_history_path"])
    rows = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[-1]["event"] = "attempt_completed_fail"
    rows[-1]["event_hash"] = ""
    rows[-1]["event_hash"] = capacity_preflight.dispatch_history_event_hash(rows[-1])
    history_raw = "".join(
        json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    history_path.write_bytes(history_raw)
    inputs["expected_capacity_dispatch_history_raw_sha256"] = _raw_sha(history_raw)

    with pytest.raises(
        MainReviewerProvenanceError,
        match="completed failure, replay, or unverified interruption",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_reviewer_receipt_host_must_equal_capacity_host(tmp_path):
    inputs = _build_fixture(
        tmp_path,
        invocation_host_identity="different-host",
    )

    with pytest.raises(
        MainReviewerProvenanceError,
        match="reviewer dispatch reservation semantics drifted",
    ):
        verify_main_reviewer_provenance(**inputs)


@pytest.mark.parametrize(
    (
        "started_at_utc",
        "completed_at_utc",
        "wave_recorded_at_utc",
        "finalized_at_utc",
    ),
    [
        (
            "2028-12-31T23:59:59+00:00",
            "2029-01-01T00:00:00+00:00",
            "2029-01-01T00:01:00+00:00",
            "2029-01-01T01:00:00+00:00",
        ),
        (
            "2029-01-02T00:00:01+00:00",
            "2029-01-02T00:00:02+00:00",
            "2029-01-02T00:01:00+00:00",
            "2029-01-02T01:00:00+00:00",
        ),
    ],
)
def test_reviewer_receipt_must_start_within_capacity_validity(
    tmp_path,
    started_at_utc,
    completed_at_utc,
    wave_recorded_at_utc,
    finalized_at_utc,
):
    inputs = _build_fixture(
        tmp_path,
        invocation_started_at_utc=started_at_utc,
        invocation_completed_at_utc=completed_at_utc,
        wave_recorded_at_utcs=[wave_recorded_at_utc],
    )
    inputs["expected_finalization_recorded_at_utc"] = finalized_at_utc

    with pytest.raises(
        MainReviewerProvenanceError,
        match="reviewer dispatch guard check falls outside its retained window",
    ):
        verify_main_reviewer_provenance(**inputs)


def test_reviewer_dispatch_deadline_is_inclusive_but_later_start_is_rejected(
    tmp_path, monkeypatch,
):
    deadline = "2029-01-01T00:00:00+00:00"
    inputs = _build_fixture(
        tmp_path / "at-boundary",
        invocation_deadline=deadline,
        invocation_started_at_utc=deadline,
        invocation_completed_at_utc="2029-01-01T00:00:01+00:00",
        wave_recorded_at_utcs=["2029-01-01T00:00:02+00:00"],
    )
    inputs["expected_authorization_deadline_utc"] = deadline
    inputs["expected_finalization_recorded_at_utc"] = (
        "2029-01-01T00:00:03+00:00")
    assert verify_main_reviewer_provenance(**inputs)[
        "reviewer_wave_count"] == 1

    late = _build_fixture(
        tmp_path / "after-boundary",
        invocation_deadline=deadline,
        invocation_started_at_utc="2029-01-01T00:00:00.000001+00:00",
        invocation_completed_at_utc="2029-01-01T00:00:01+00:00",
        wave_recorded_at_utcs=["2029-01-01T00:00:02+00:00"],
    )
    late["expected_authorization_deadline_utc"] = deadline
    late["expected_finalization_recorded_at_utc"] = (
        "2029-01-01T00:00:03+00:00")

    def reopen_receipt(packet, reference, **_kwargs):
        receipt_path = packet.parent / str(reference["receipt_path"])
        return json.loads(receipt_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(
        reviewer_provenance, "validate_invocation_evidence", reopen_receipt)
    with pytest.raises(
        MainReviewerProvenanceError,
        match="dispatch reservation was not actively released",
    ):
        verify_main_reviewer_provenance(**late)


def test_exact_tool_use_refusal_joins_to_reviewer_error(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[(1, [{
        "query": "The threshold is 24 votes.",
        "candidate_a": "24.",
        "candidate_b": "30.",
        "raw_output": CLEAN_OUTPUT,
        "commands": ["Get-Content world.txt"],
        "as_reviewer_error": True,
    }])])

    result = verify_main_reviewer_provenance(**inputs)

    assert result["reviewer_error_decision_count"] == 1
    assert result["parsed_decision_count"] == 0


def test_clean_ruling_requires_integer_zero_tool_uses(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[(1, [{
        "query": "The threshold is 24 votes.",
        "candidate_a": "24.",
        "candidate_b": "30.",
        "raw_output": CLEAN_OUTPUT,
        "commands": [],
        "tool_uses": False,
    }])])

    with pytest.raises(MainReviewerProvenanceError, match="integer zero"):
        verify_main_reviewer_provenance(**inputs)


def test_reviewer_error_requires_nonempty_retained_commands(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[(1, [{
        "query": "The threshold is 24 votes.",
        "candidate_a": "24.",
        "candidate_b": "30.",
        "raw_output": CLEAN_OUTPUT,
        "commands": [],
        "as_reviewer_error": True,
    }])])

    with pytest.raises(MainReviewerProvenanceError, match="lacks retained tool-use"):
        verify_main_reviewer_provenance(**inputs)


def test_tool_use_refusal_bytes_are_exact(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[(1, [{
        "query": "The threshold is 24 votes.",
        "candidate_a": "24.",
        "candidate_b": "30.",
        "raw_output": CLEAN_OUTPUT,
        "commands": ["Get-Content world.txt"],
        "as_reviewer_error": True,
        "synthetic_output": "TOOL_USE_DETECTED: altered",
    }])])

    with pytest.raises(MainReviewerProvenanceError, match="synthetic output"):
        verify_main_reviewer_provenance(**inputs)


def test_packet_byte_tamper_is_rejected(tmp_path):
    inputs = _build_fixture(tmp_path)
    packet = next(Path(inputs["review_packets_root"]).glob("wave-*/*.txt"))
    packet.write_bytes(packet.read_bytes() + b"tamper")

    with pytest.raises(MainReviewerProvenanceError, match="exact composed prompt"):
        verify_main_reviewer_provenance(**inputs)


def test_evidence_event_stream_tamper_is_rejected(tmp_path):
    inputs = _build_fixture(tmp_path)
    event_stream = next(Path(inputs["review_packets_root"]).glob(
        "wave-*/reviewer_evidence/*.evidence/codex_events.jsonl"))
    event_stream.write_bytes(event_stream.read_bytes() + b"tamper\n")

    with pytest.raises(MainReviewerProvenanceError, match="invocation evidence is invalid"):
        verify_main_reviewer_provenance(**inputs)


def test_decision_store_order_must_equal_ruling_order(tmp_path):
    inputs = _build_fixture(tmp_path, reverse_decisions=True)

    with pytest.raises(MainReviewerProvenanceError, match="order or content drifted"):
        verify_main_reviewer_provenance(**inputs)


def test_final_worklist_must_equal_last_snapshot_bytes(tmp_path):
    inputs = _build_fixture(tmp_path)
    worklist_path = Path(inputs["reviewer_worklist_path"])
    worklist_path.write_bytes(worklist_path.read_bytes() + b" ")

    with pytest.raises(MainReviewerProvenanceError, match="last wave snapshot"):
        verify_main_reviewer_provenance(**inputs)


def test_invocation_cli_and_deadline_are_bound(tmp_path):
    cli_inputs = _build_fixture(
        tmp_path / "cli", invocation_cli_path="C:/wrong/codex.cmd")
    with pytest.raises(
        MainReviewerProvenanceError,
        match="dispatch guard evidence no longer verifies exactly",
    ):
        verify_main_reviewer_provenance(**cli_inputs)

    deadline_inputs = _build_fixture(
        tmp_path / "deadline", invocation_deadline="2031-01-01T00:00:00+00:00")
    with pytest.raises(
        MainReviewerProvenanceError,
        match="authorization_valid_until_utc drifted",
    ):
        verify_main_reviewer_provenance(**deadline_inputs)


def test_wave_numbers_may_skip_but_may_not_repeat_or_descend(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[
        (1, [{
            "query": "The threshold is 24 votes.",
            "candidate_a": "24.",
            "candidate_b": "30.",
            "raw_output": CLEAN_OUTPUT,
            "commands": [],
        }]),
        (2, [{
            "query": "The council meets in Month 3.",
            "candidate_a": "Month 3.",
            "candidate_b": "Month 6.",
            "raw_output": CLEAN_OUTPUT,
            "commands": [],
        }]),
    ])
    index_path = Path(inputs["reviewer_index_path"])
    rows = index_path.read_bytes().splitlines(keepends=True)
    index_path.write_bytes(b"".join(reversed(rows)))

    with pytest.raises(MainReviewerProvenanceError, match="strictly increasing"):
        verify_main_reviewer_provenance(**inputs)


def test_empty_query_payload_and_z_deadline_normalization_are_valid(tmp_path):
    inputs = _build_fixture(tmp_path, waves=[(1, [{
        "query": "",
        "candidate_a": "24.",
        "candidate_b": "30.",
        "raw_output": MALFORMED_OUTPUT,
        "commands": [],
    }])])
    inputs["expected_authorization_deadline_utc"] = "2030-01-01T00:00:00Z"

    result = verify_main_reviewer_provenance(**inputs)

    assert result["malformed_decision_count"] == 1
