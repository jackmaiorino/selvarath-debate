"""Fake-only tests for the capacity execution authority and receipt join."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import platform
import stat
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_main_capacity_execution as execution
from scripts import codex_reviewer_batch
from scripts import phase3_main_review_capacity_preflight as capacity
from scripts import phase3_main_run_capacity_preflight as capacity_cli


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
HOST = "capacity-test-host"
CLI_VERSION = "codex-cli capacity-test"


class IncrementingClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        observed = self.value
        self.value += 1.0
        return observed


def _variant(number: int) -> capacity.VariantPacket:
    prompt = (
        "Review this exact capacity packet.\n"
        f"QUERY: q-{number:03d}\n"
        f"CANDIDATE A: a-{number:03d}\n"
        f"CANDIDATE B: b-{number:03d}"
    )
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    payload_sha = hashlib.sha256(f"payload-{number:03d}".encode()).hexdigest()
    source_sha = hashlib.sha256(f"source-{number:03d}".encode()).hexdigest()
    return capacity.VariantPacket(
        rank_sha256=hashlib.sha256(f"rank-{number:03d}".encode()).hexdigest(),
        source_payload_sha256=source_sha,
        source_prompt_sha256=hashlib.sha256(
            f"source-prompt-{number:03d}".encode()
        ).hexdigest(),
        variant_payload_sha256=payload_sha,
        variant_prompt_sha256=prompt_sha,
        prompt=prompt,
    )


def _workload() -> capacity.DerivedWorkload:
    items = tuple(_variant(number) for number in range(360))
    first = items[:180]
    second = items[180:]

    def records(cohort: tuple[capacity.VariantPacket, ...]) -> list[dict[str, str]]:
        return [
            {
                "rank_sha256": item.rank_sha256,
                "source_payload_sha256": item.source_payload_sha256,
                "source_prompt_sha256": item.source_prompt_sha256,
                "variant_payload_sha256": item.variant_payload_sha256,
                "variant_prompt_sha256": item.variant_prompt_sha256,
            }
            for item in cohort
        ]

    summary = {
        "derivation_tag": capacity.DERIVATION_TAG,
        "transformation": "swap_candidate_a_and_candidate_b_only",
        "historical_exclusion": "exclude_every_variant_prompt_sha256_seen_in_source_packets",
        "source_unique_packet_count": 360,
        "historical_prompt_sha256_count": 360,
        "byte_new_eligible_count": 360,
        "historical_collision_count": 0,
        "cohort_count": 2,
        "selection_count_per_cohort": 180,
        "wave_size": 60,
        "wave_sizes_per_cohort": [60, 60, 60],
        "unused_byte_new_reserve_count": 0,
        "cohort_records_canonical_sha256s": [
            capacity.canonical_sha256(records(cohort)) for cohort in (first, second)
        ],
        "cohort_source_payloads_canonical_sha256s": [
            capacity.canonical_sha256(
                [item.source_payload_sha256 for item in cohort]
            )
            for cohort in (first, second)
        ],
        "cohort_variant_payloads_canonical_sha256s": [
            capacity.canonical_sha256(
                [item.variant_payload_sha256 for item in cohort]
            )
            for cohort in (first, second)
        ],
        "cohort_variant_prompts_canonical_sha256s": [
            capacity.canonical_sha256(
                [item.variant_prompt_sha256 for item in cohort]
            )
            for cohort in (first, second)
        ],
        "historical_collision_sources_canonical_sha256": capacity.canonical_sha256([]),
    }
    return capacity.DerivedWorkload(
        selected=first,
        eligible=items,
        collision_source_payloads=(),
        summary=summary,
        retry_selected=second,
    )


def _write_json(path: Path, value: dict[str, Any]) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _fixture(tmp_path: Path) -> dict[str, Any]:
    capacity_root = (tmp_path / "capacity-only").resolve()
    history_path = capacity_root / "dispatch_history.jsonl"
    cli_path = (tmp_path / "codex.cmd").resolve()
    cli_raw = b"@echo off\r\nrem deterministic fake only\r\n"
    cli_path.write_bytes(cli_raw)
    sealed_source = (tmp_path / "sealed-source").resolve()
    sealed_source.mkdir()
    plan = {
        "schema_version": capacity.SCHEMA_VERSION,
        "source_locations": {"sealed_archive": sealed_source.as_posix()},
        "reviewer_configuration": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "reviewer_cli_binary": "codex.cmd",
            "reviewer_cli_resolved_path": cli_path.as_posix(),
            "reviewer_cli_wrapper_raw_sha256": hashlib.sha256(cli_raw).hexdigest(),
            "reviewer_cli_wrapper_byte_count": len(cli_raw),
            "concurrency": 12,
            "fresh_ephemeral_context_per_packet": True,
            "tool_use_permitted": False,
            "wave_pending_payload_limit": 64,
            "actual_capacity_wave_size": 60,
        },
        "capacity_thresholds": {
            "maximum_seconds_per_60_packet_wave": 3600,
            "maximum_seconds_for_three_waves": 10800,
            "certified_rulings_per_24h": 1440,
        },
        "dispatch_history_contract": {
            "schema_version": capacity.DISPATCH_HISTORY_SCHEMA_VERSION,
            "required_path": history_path.as_posix(),
            "anchor_directory": (capacity_root / "dispatch_history_anchors").as_posix(),
            "interruption_evidence_root": (
                capacity_root / "interruption_evidence"
            ).as_posix(),
            "required_initial_raw_sha256": hashlib.sha256(b"").hexdigest(),
            "append_only": True,
            "anchor_validation_required": True,
            "initialization_receipt_required": True,
            "maximum_attempts": 2,
        },
        "validity": {"valid_for_hours": 24},
    }
    plan_path = (tmp_path / "capacity_plan.json").resolve()
    plan_raw = _write_json(plan_path, plan)
    workload = _workload()
    context = execution.CapacityContext(
        plan_path=plan_path,
        plan_raw=plan_raw,
        plan=plan,
        workload=workload,
    )
    capacity.initialize_dispatch_history(history_path, plan=plan)
    workload_root = capacity_root / "cohort_01_workload"
    history = capacity.load_bound_dispatch_history(history_path, plan=plan)
    capacity.materialize_workload(
        workload_root,
        plan=plan,
        workload=workload,
        dispatch_history=history,
    )
    result_path = capacity_root / "capacity_result.json"
    project_root = Path(execution.__file__).resolve().parents[1]
    runner_script = project_root / "scripts" / "phase3_main_run_capacity_preflight.py"
    manifest = execution.build_execution_manifest(
        context=context,
        project_root=project_root,
        workload_root=workload_root,
        result_path=result_path,
        run_id="capacity-run-0001",
        attempt_id="capacity-attempt-0001",
        repository_head=HEAD,
        reviewer_cli_version=CLI_VERSION,
        host_identity=HOST,
        runner_script_path=runner_script,
    )
    manifest_path = capacity_root / "execution_manifest.json"
    manifest_raw = _write_json(manifest_path, manifest)
    authorization = {
        "schema_version": execution.AUTHORIZATION_SCHEMA,
        "authorization_id": "capacity-authorization-0001",
        "scope": execution.SCOPE,
        "approved_by": "Jack Maiorino",
        "approved_at_utc": (NOW - timedelta(hours=1)).isoformat(),
        "valid_until_utc": (NOW + timedelta(hours=1)).isoformat(),
        "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "manifest_canonical_sha256": execution.canonical_sha256(manifest),
        "run_id": manifest["run_id"],
        "attempt_id": manifest["attempt_id"],
        "cohort_number": 1,
        "authority": {
            "capacity_execution_authorized": True,
            "external_reviewer_dispatch_authorized": True,
            "provider_calls_authorized": False,
            "together_calls_authorized": False,
            "main_run_authorized": False,
            "main_artifact_mutation_authorized": False,
        },
        "maximum_reviewer_dispatches": 180,
        "reviewer_usage_limit": {
            "schema_version": execution.USAGE_LIMIT_SCHEMA,
            "unit": execution.USAGE_UNIT,
            "maximum": 180,
            "accounting_treatment": "owner-authorized capacity invocation count only",
        },
    }
    authorization_path = capacity_root / "capacity_authorization.json"
    _write_json(authorization_path, authorization)
    return {
        "root": capacity_root,
        "context": context,
        "history_path": history_path,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "authorization": authorization,
        "authorization_path": authorization_path,
        "result_path": result_path,
        "cli_path": cli_path,
    }


def _rebuild_manifest(
    fixture: dict[str, Any],
    *,
    result_path: Path,
) -> dict[str, Any]:
    manifest = fixture["manifest"]
    return execution.build_execution_manifest(
        context=fixture["context"],
        project_root=Path(manifest["repository"]["project_root"]),
        workload_root=Path(manifest["workload"]["root"]),
        result_path=result_path,
        run_id=manifest["run_id"],
        attempt_id=manifest["attempt_id"],
        repository_head=manifest["repository"]["head_commit"],
        reviewer_cli_version=manifest["reviewer"]["cli"]["version"],
        host_identity=manifest["reviewer"]["host_identity"],
        runner_script_path=Path(
            manifest["code_bindings"]["capacity_cli"]["path"]
        ),
    )


class FakeReviewer:
    def __init__(self, *, tamper_receipt: bool = False, cross_wave_reuse: bool = False):
        self.calls: list[Path] = []
        self.lock = threading.Lock()
        self.tamper_receipt = tamper_receipt
        self.cross_wave_reuse = cross_wave_reuse
        self.first_reference: dict[str, Any] | None = None

    def __call__(
        self,
        *,
        packet: Path,
        model: str,
        effort: str,
        codex: str,
        not_after_utc: datetime,
        concurrency: int,
    ) -> dict[str, Any]:
        with self.lock:
            call_number = len(self.calls)
            self.calls.append(packet)
            reused = self.cross_wave_reuse and call_number == 60
            first_reference = self.first_reference
        ruling = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: deterministic fake"
        prompt_raw = packet.read_bytes()
        cli_raw = Path(codex).read_bytes()
        runner_path, runner_sha, runner_size = (
            codex_reviewer_batch._batch_runner_identity()  # noqa: SLF001
        )
        working = packet.parent / f"fake-work-{packet.stem}"
        event_stream = (
            '{"type":"thread.started","thread_id":"fake"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"type":"agent_message"}}\n'
            '{"type":"turn.completed","usage":{}}\n'
        ).encode("utf-8")
        normalized = ruling.encode("utf-8")
        invocation = {
            "argv": [
                codex,
                "exec",
                "-m",
                model,
                "-c",
                f'model_reasoning_effort="{effort}"',
                "-C",
                str(working),
                "--skip-git-repo-check",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "-s",
                "read-only",
                "--json",
                "-o",
                str(working / "ruling.txt"),
                "-",
            ],
            "model_requested": model,
            "reasoning_effort_requested": effort,
            "batch_concurrency": concurrency,
            "codex_cli_argument": codex,
            "codex_cli_resolved_path": Path(codex).resolve().as_posix(),
            "codex_cli_wrapper_raw_sha256": hashlib.sha256(cli_raw).hexdigest(),
            "codex_cli_wrapper_byte_count": len(cli_raw),
            "codex_cli_version": None,
            "working_directory": str(working),
            "output_file": str(working / "ruling.txt"),
            "sandbox_mode": "read-only",
            "ephemeral": True,
            "ignore_user_config": True,
            "ignore_rules": True,
            "json_event_stream": True,
            "python_executable": sys.executable,
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "host_identity": HOST,
            "batch_runner_path": runner_path,
            "batch_runner_raw_sha256": runner_sha,
            "batch_runner_byte_count": runner_size,
        }
        outcome = {
            "authorization_deadline_utc": not_after_utc.isoformat(),
            "deadline_active_before_dispatch": True,
            "dispatch_attempted": True,
            "started_at_utc": NOW.isoformat(),
            "completed_at_utc": NOW.isoformat(),
            "timed_out": False,
            "process_exit_code": 0,
            "result_ok": True,
            "error": None,
            "commands": [],
            "event_stream_errors": [],
            "normalized_ruling_raw_sha256": hashlib.sha256(normalized).hexdigest(),
            "normalized_ruling_byte_count": len(normalized),
        }
        reference = codex_reviewer_batch._persist_invocation_evidence(  # noqa: SLF001
            packet=packet,
            prompt_bytes=prompt_raw,
            invocation=invocation,
            outcome=outcome,
            event_stream_raw=event_stream,
            stderr_raw=b"",
            ruling_raw=normalized,
        )
        if call_number == 0:
            with self.lock:
                self.first_reference = dict(reference)
        if reused and first_reference is not None:
            reference = first_reference
        if self.tamper_receipt and call_number == 0:
            receipt_path = packet.parent / str(reference["receipt_path"])
            receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
        return {
            "ok": True,
            "error": None,
            "commands": [],
            "prompt_sha256": hashlib.sha256(prompt_raw).hexdigest(),
            "raw_output": ruling,
            "evidence": reference,
        }


def _execute(fixture: dict[str, Any], reviewer: FakeReviewer, **overrides: Any):
    kwargs = {
        "manifest_path": fixture["manifest_path"],
        "authorization_path": fixture["authorization_path"],
        "context": fixture["context"],
        "reviewer_runner": reviewer,
        "monotonic": IncrementingClock(),
        "utc_now": lambda: NOW,
        "repository_probe": lambda _root: (HEAD, True),
        "cli_version_reader": lambda _cli: CLI_VERSION,
        "host_reader": lambda: HOST,
        "authorization_verifier": lambda _path, _raw: None,
    }
    kwargs.update(overrides)
    return execution._execute_capacity_preflight(**kwargs)  # noqa: SLF001


def _history_events(fixture: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    snapshot = capacity.load_bound_dispatch_history(
        fixture["history_path"], plan=fixture["context"].plan
    )
    return tuple(dict(event) for event in snapshot.events)


def test_fake_only_capacity_execution_reopens_exactly_180_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    reviewer = FakeReviewer()
    anchor_validation_modes: list[bool] = []
    original_private_validator = capacity._validate_result  # noqa: SLF001

    def observe_private_validation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        anchor_validation_modes.append(kwargs["_validate_history_anchors"])
        return original_private_validator(*args, **kwargs)

    monkeypatch.setattr(capacity, "_validate_result", observe_private_validation)

    outcome = _execute(fixture, reviewer)

    assert outcome["execution"] == "pass"
    assert len(reviewer.calls) == 180
    assert [event["event"] for event in _history_events(fixture)] == [
        "dispatch_started",
        "attempt_completed_pass",
    ]
    result = json.loads(fixture["result_path"].read_text(encoding="utf-8"))
    assert result["reviewer_usage_receipt"]["observed_quantity"] == 180
    assert result["reviewer_usage_receipt"]["non_claim"] == (
        "dispatch count is not USD or token accounting"
    )
    assert outcome["validation"]["reopened_invocation_receipts"] == 180
    assert all(
        Path(wave["durable_output"]["path"]).is_file() for wave in result["waves"]
    )
    fresh_manifest_raw, fresh_manifest = execution.load_execution_manifest(
        fixture["manifest_path"], context=fixture["context"]
    )
    fresh_authorization_raw = fixture["authorization_path"].read_bytes()
    fresh_validation = execution.validate_execution_result(
        result,
        manifest=fresh_manifest,
        manifest_raw=fresh_manifest_raw,
        authorization=json.loads(fresh_authorization_raw.decode("utf-8")),
        authorization_raw=fresh_authorization_raw,
        context=fixture["context"],
        as_of_utc=NOW,
    )
    assert fresh_validation["reopened_invocation_receipts"] == 180
    assert anchor_validation_modes == [False, True, True]


@pytest.mark.parametrize("failure", ["missing", "false", "expired", "mismatch"])
def test_invalid_authority_leaves_all_execution_state_untouched(
    tmp_path: Path, failure: str
) -> None:
    fixture = _fixture(tmp_path)
    reviewer = FakeReviewer()
    authorization = dict(fixture["authorization"])
    if failure == "missing":
        fixture["authorization_path"] = fixture["root"] / "missing.json"
    elif failure == "false":
        authorization["authority"] = {
            **authorization["authority"],
            "external_reviewer_dispatch_authorized": False,
        }
        _write_json(fixture["authorization_path"], authorization)
    elif failure == "expired":
        authorization["valid_until_utc"] = (NOW - timedelta(minutes=1)).isoformat()
        _write_json(fixture["authorization_path"], authorization)
    else:
        authorization["manifest_raw_sha256"] = "f" * 64
        _write_json(fixture["authorization_path"], authorization)
    probes: list[str] = []

    with pytest.raises(execution.CapacityExecutionError):
        _execute(
            fixture,
            reviewer,
            repository_probe=lambda _root: probes.append("git") or (HEAD, True),
            cli_version_reader=lambda _cli: probes.append("cli") or CLI_VERSION,
            authorization_verifier=lambda _path, _raw: probes.append("signature"),
        )

    assert reviewer.calls == []
    assert probes == []
    assert _history_events(fixture) == ()
    assert not fixture["result_path"].exists()
    history_contract = fixture["manifest"]["dispatch_history"]
    assert not Path(history_contract["attempt_reservation_path"]).exists()


def test_public_execution_surface_is_noninjectable() -> None:
    signature = inspect.signature(execution.execute_capacity_preflight)

    assert tuple(signature.parameters) == (
        "manifest_path",
        "authorization_path",
        "context",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )


def test_public_result_validators_do_not_expose_anchor_bypasses() -> None:
    assert "validate_history_anchors" not in inspect.signature(
        capacity.validate_result
    ).parameters
    assert "prepublication" not in inspect.signature(
        execution.validate_execution_result
    ).parameters


def test_cli_authority_validation_rejects_manifest_before_loading_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "manifest.json"
    authorization_path = tmp_path / "authorization.json"
    manifest_path.write_text("{}", encoding="utf-8")
    authorization_path.write_text("{}", encoding="utf-8")
    authorization_loads: list[Path] = []
    monkeypatch.setattr(capacity_cli, "_context", lambda _args: object())

    def reject_manifest(_path: Path, *, context: Any) -> tuple[bytes, dict[str, Any]]:
        assert context is not None
        raise execution.CapacityExecutionError("strict manifest rejection")

    monkeypatch.setattr(execution, "load_execution_manifest", reject_manifest)
    monkeypatch.setattr(
        execution,
        "load_authenticated_capacity_authorization",
        lambda path: authorization_loads.append(path),
    )

    with pytest.raises(execution.CapacityExecutionError, match="strict manifest rejection"):
        capacity_cli.main(
            [
                "--validate-authority",
                "--manifest",
                str(manifest_path),
                "--authorization",
                str(authorization_path),
            ]
        )

    assert authorization_loads == []


def test_cli_run_is_unconditionally_blocked_before_context_or_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_calls: list[str] = []
    execution_calls: list[str] = []
    monkeypatch.setattr(
        capacity_cli,
        "_context",
        lambda _args: context_calls.append("context"),
    )
    monkeypatch.setattr(
        execution,
        "execute_capacity_preflight",
        lambda **_kwargs: execution_calls.append("execution"),
    )

    with pytest.raises(
        execution.CapacityExecutionError,
        match="real capacity dispatch is disabled",
    ):
        capacity_cli.main(["--run"])

    assert context_calls == []
    assert execution_calls == []


def test_public_execution_is_unconditionally_blocked_before_reads_or_subprocesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)

    def forbidden_subprocess(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("disabled real dispatch reached a subprocess")

    def forbidden_read(*_args: Any, **_kwargs: Any) -> bytes:
        raise AssertionError("disabled real dispatch read an artifact")

    monkeypatch.setattr(execution.subprocess, "run", forbidden_subprocess)
    monkeypatch.setattr(execution, "_stable_read", forbidden_read)

    with pytest.raises(
        execution.CapacityExecutionError,
        match="real capacity dispatch is disabled",
    ):
        execution.execute_capacity_preflight(
            manifest_path=fixture["manifest_path"],
            authorization_path=fixture["authorization_path"],
            context=fixture["context"],
        )

    assert _history_events(fixture) == ()
    assert not Path(
        fixture["manifest"]["dispatch_history"]["attempt_reservation_path"]
    ).exists()
    assert not fixture["result_path"].exists()


@pytest.mark.parametrize(
    "alias_name",
    [
        "history",
        "initialization_pending",
        "initialization_receipt",
        "append_intent",
        "writer_lock",
        "anchor_directory",
        "interruption_evidence_root",
        "attempt_reservation",
        "failure_receipt",
        "wave_1_output",
        "wave_2_output",
        "wave_3_output",
    ],
)
def test_result_path_alias_is_rejected_before_authority_or_reviewer_calls(
    tmp_path: Path, alias_name: str
) -> None:
    fixture = _fixture(tmp_path)
    reviewer = FakeReviewer()
    history = fixture["manifest"]["dispatch_history"]
    history_path = Path(history["path"])
    waves = fixture["manifest"]["workload"]["waves"]
    aliases = {
        "history": history["path"],
        "initialization_pending": history_path.with_name(
            f"{history_path.name}.initialize.pending.json"
        ).as_posix(),
        "initialization_receipt": history["initialization_receipt"]["path"],
        "append_intent": history_path.with_name(
            f"{history_path.name}.append.pending.json"
        ).as_posix(),
        "writer_lock": history_path.with_name(f"{history_path.name}.lock").as_posix(),
        "anchor_directory": history["anchor_directory"],
        "interruption_evidence_root": history["interruption_evidence_root"],
        "attempt_reservation": history["attempt_reservation_path"],
        "failure_receipt": history["failure_receipt_path"],
        "wave_1_output": waves[0]["output_path"],
        "wave_2_output": waves[1]["output_path"],
        "wave_3_output": waves[2]["output_path"],
    }
    manifest = {**fixture["manifest"], "result_path": aliases[alias_name]}
    manifest_raw = _write_json(fixture["manifest_path"], manifest)
    authorization = {
        **fixture["authorization"],
        "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "manifest_canonical_sha256": execution.canonical_sha256(manifest),
    }
    _write_json(fixture["authorization_path"], authorization)
    signature_checks: list[str] = []

    with pytest.raises(execution.CapacityExecutionError, match="runtime paths alias"):
        _execute(
            fixture,
            reviewer,
            authorization_verifier=lambda _path, _raw: signature_checks.append(
                "signature"
            ),
        )

    assert signature_checks == []
    assert reviewer.calls == []
    assert _history_events(fixture) == ()
    assert not Path(history["attempt_reservation_path"]).exists()
    assert not fixture["result_path"].exists()


@pytest.mark.parametrize(
    "contained_name",
    [
        "history_descendant",
        "anchor_descendant",
        "interruption_descendant",
        "wave_output_descendant",
        "workload_descendant",
    ],
)
def test_result_path_containment_is_rejected_before_authority_or_reviewer_calls(
    tmp_path: Path, contained_name: str
) -> None:
    fixture = _fixture(tmp_path)
    reviewer = FakeReviewer()
    history = fixture["manifest"]["dispatch_history"]
    waves = fixture["manifest"]["workload"]["waves"]
    contained = {
        "history_descendant": Path(history["path"]) / "nested-result.json",
        "anchor_descendant": Path(history["anchor_directory"]) / "nested-result.json",
        "interruption_descendant": (
            Path(history["interruption_evidence_root"]) / "nested-result.json"
        ),
        "wave_output_descendant": (
            Path(waves[0]["output_path"]) / "nested-result.json"
        ),
        "workload_descendant": (
            Path(waves[0]["directory"])
            / codex_reviewer_batch.EVIDENCE_DIRECTORY_NAME
            / "nested-result.json"
        ),
    }
    manifest = {
        **fixture["manifest"],
        "result_path": contained[contained_name].resolve().as_posix(),
    }
    manifest_raw = _write_json(fixture["manifest_path"], manifest)
    authorization = {
        **fixture["authorization"],
        "manifest_raw_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "manifest_canonical_sha256": execution.canonical_sha256(manifest),
    }
    _write_json(fixture["authorization_path"], authorization)
    signature_checks: list[str] = []

    with pytest.raises(
        execution.CapacityExecutionError,
        match="alias|contain|non-directory ancestor",
    ):
        _execute(
            fixture,
            reviewer,
            authorization_verifier=lambda _path, _raw: signature_checks.append(
                "signature"
            ),
        )

    assert signature_checks == []
    assert reviewer.calls == []
    assert _history_events(fixture) == ()
    assert not Path(history["attempt_reservation_path"]).exists()


def test_existing_critical_hard_link_is_rejected_before_authority(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    alias_path = fixture["root"] / "dispatch_history_hard_link.jsonl"
    try:
        os.link(fixture["history_path"], alias_path)
    except OSError as exc:
        pytest.skip(f"filesystem does not support hard links: {exc}")
    reviewer = FakeReviewer()
    signature_checks: list[str] = []

    with pytest.raises(execution.CapacityExecutionError, match="hard-link alias"):
        _execute(
            fixture,
            reviewer,
            authorization_verifier=lambda _path, _raw: signature_checks.append(
                "signature"
            ),
        )

    assert signature_checks == []
    assert reviewer.calls == []
    assert _history_events(fixture) == ()


@pytest.mark.parametrize(
    ("link_mode", "file_attributes"),
    [
        (stat.S_IFLNK, 0),
        (stat.S_IFDIR, getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)),
    ],
)
def test_result_parent_symlink_or_junction_is_rejected_during_manifest_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_mode: int,
    file_attributes: int,
) -> None:
    fixture = _fixture(tmp_path)
    linked_parent = fixture["root"] / "linked-runtime-parent"
    original_lstat = Path.lstat

    class LinkMetadata:
        st_mode = link_mode
        st_nlink = 1
        st_file_attributes = file_attributes

    def link_aware_lstat(path: Path) -> Any:
        if execution._lexical_absolute(path) == linked_parent:  # noqa: SLF001
            return LinkMetadata()
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", link_aware_lstat)

    with pytest.raises(
        execution.CapacityExecutionError,
        match="symlink, junction, or reparse point",
    ):
        _rebuild_manifest(
            fixture,
            result_path=linked_parent / "capacity-result.json",
        )


def test_tampered_invocation_receipt_fails_terminally_with_usage_count(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    reviewer = FakeReviewer(tamper_receipt=True)

    with pytest.raises(execution.CapacityExecutionError, match="evidence failed"):
        _execute(fixture, reviewer)

    assert len(reviewer.calls) == 60
    assert [event["event"] for event in _history_events(fixture)] == [
        "dispatch_started",
        "attempt_completed_fail",
    ]
    failure_path = Path(
        fixture["manifest"]["dispatch_history"]["failure_receipt_path"]
    )
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["attempted_dispatches"] == 60
    assert failure["usage_unit"] == execution.USAGE_UNIT
    assert not fixture["result_path"].exists()


def test_cross_wave_receipt_reuse_fails_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    reviewer = FakeReviewer(cross_wave_reuse=True)

    with pytest.raises(execution.CapacityExecutionError, match="evidence failed"):
        _execute(fixture, reviewer)

    assert len(reviewer.calls) == 120
    assert [event["event"] for event in _history_events(fixture)] == [
        "dispatch_started",
        "attempt_completed_fail",
    ]
    assert not fixture["result_path"].exists()


def test_cohort_2_manifest_is_rejected_before_authority_or_mutation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    reviewer = FakeReviewer()
    manifest = dict(fixture["manifest"])
    manifest["cohort"] = {**manifest["cohort"], "cohort_number": 2}
    manifest_raw = _write_json(fixture["manifest_path"], manifest)
    authorization = dict(fixture["authorization"])
    authorization["manifest_raw_sha256"] = hashlib.sha256(manifest_raw).hexdigest()
    authorization["manifest_canonical_sha256"] = execution.canonical_sha256(manifest)
    _write_json(fixture["authorization_path"], authorization)
    verifications: list[str] = []

    with pytest.raises(execution.CapacityExecutionError, match="cohort 1"):
        _execute(
            fixture,
            reviewer,
            authorization_verifier=lambda _path, _raw: verifications.append("signature"),
        )

    assert verifications == []
    assert reviewer.calls == []
    assert _history_events(fixture) == ()


def test_predicted_pass_is_prevalidated_before_result_write_or_terminal_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    reviewer = FakeReviewer()
    original = execution._validate_execution_result  # noqa: SLF001
    observed: list[str] = []

    def reject_predicted(*args: Any, **kwargs: Any):
        if kwargs.get("_validate_history_anchors") is False:
            predicted = kwargs["dispatch_history"]
            assert [event["event"] for event in predicted.events] == [
                "dispatch_started",
                "attempt_completed_pass",
            ]
            assert [event["event"] for event in _history_events(fixture)] == [
                "dispatch_started"
            ]
            assert not fixture["result_path"].exists()
            observed.append("predicted")
            raise execution.CapacityExecutionError("synthetic prepublication rejection")
        return original(*args, **kwargs)

    monkeypatch.setattr(execution, "_validate_execution_result", reject_predicted)
    with pytest.raises(execution.CapacityExecutionError, match="prepublication rejection"):
        _execute(fixture, reviewer)

    assert observed == ["predicted"]
    assert [event["event"] for event in _history_events(fixture)] == [
        "dispatch_started",
        "attempt_completed_fail",
    ]
    assert not fixture["result_path"].exists()
