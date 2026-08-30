"""An unreachable reviewer must abort the wave, not rule on it.

On 2026-08-07 the Codex quota ran out mid-wave. Every one of 329 calls returned exit=1 with
empty output, and each was committed as reviewer_error, which the frozen failure rule makes
non-ALLOW. So 329 oracle queries are permanently blocked by a reviewer that never read them,
in an append-only store.

The distinction the batch runner was missing:

- the reviewer RULED and the output was unusable (unparseable, or tool use detected) -> that
  is evidence about this payload, and committing it as non-ALLOW is the frozen failure rule
  working exactly as designed;
- the reviewer was never REACHED at all -> that is evidence about the reviewer, not the
  payload. Committing it writes a permanent verdict from nothing.

Fail-closed means refusing to proceed, not manufacturing a refusal for every payload in the
queue.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import codex_reviewer_batch
from scripts.codex_reviewer_batch import ReviewerUnavailable, classify_result


def _meta(sha="a" * 64):
    return {"payload_sha256": sha}


def _event_stream(*events) -> bytes:
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode("utf-8")


def _clean_events(*items):
    if not items:
        items = ({"type": "item.completed", "item": {"type": "agent_message"}},)
    return (
        {"type": "thread.started", "thread_id": "thread-test"},
        {"type": "turn.started"},
        *items,
        {"type": "turn.completed", "usage": {}},
    )


def _run_with_evidence(tmp_path, monkeypatch, *, events, ruling=None, stderr=b""):
    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    packet = packet_root / "00001_payload.txt"
    packet.write_text("review this exact packet", encoding="utf-8")
    cli = tmp_path / "codex.cmd"
    cli.write_bytes(b"@echo off\r\nnode codex.js %*\r\n")
    event_raw = _event_stream(*events)
    ruling_text = (
        "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: valid\n"
        if ruling is None else ruling
    )

    def fake_run(command, **kwargs):
        assert command[0] == str(cli)
        assert command[command.index("-m") + 1] == "reviewer-model"
        assert command[command.index("-c") + 1] == 'model_reasoning_effort="high"'
        assert kwargs["input"] == packet.read_bytes()
        out_file = Path(command[command.index("-o") + 1])
        out_file.write_text(ruling_text, encoding="utf-8", newline="")
        return SimpleNamespace(
            returncode=0,
            stdout=event_raw,
            stderr=stderr,
        )

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", fake_run)
    result = codex_reviewer_batch.run_one(
        packet, "reviewer-model", "high", str(cli))
    return packet, cli, event_raw, result


def _guard_binding(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    return {
        "path": path.resolve().as_posix(),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
    }


def _guarded_two_packet_batch(
    tmp_path, monkeypatch, *, capacity_completed: datetime,
):
    packets = tmp_path / "packets"
    packets.mkdir()
    items = []
    worklist_items = []
    packet_bindings = []
    for number in range(1, 3):
        query = f"query {number}"
        candidate_a = f"candidate a {number}"
        candidate_b = f"candidate b {number}"
        payload_sha = codex_reviewer_batch._payload_sha256(
            query, candidate_a, candidate_b)
        packet = packets / f"{number:05d}_{payload_sha[:12]}.txt"
        raw = f"review exact packet {number}".encode("utf-8")
        packet.write_bytes(raw)
        prompt_sha = hashlib.sha256(raw).hexdigest()
        items.append({
            "n": number,
            "file": packet.name,
            "payload_sha256": payload_sha,
            "prompt_sha256": prompt_sha,
        })
        worklist_items.append({
            "payload_sha256": payload_sha,
            "query": query,
            "candidate_a": candidate_a,
            "candidate_b": candidate_b,
            "subagent_prompt": raw.decode("utf-8"),
            "subagent_prompt_sha256": prompt_sha,
        })
        packet_bindings.append({
            "file": packet.name,
            "payload_sha256": payload_sha,
            "prompt_sha256": prompt_sha,
            "byte_count": len(raw),
        })
    index_path = packets / "INDEX.json"
    index_path.write_text(
        json.dumps({"count": 2, "items": items}, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    worklist_path = packets / "WORKLIST.json"
    worklist_path.write_text(
        json.dumps({
            "frozen_prompt_sha256": "d" * 64,
            "separator": "\n\n---PAYLOAD---\n",
            "items": worklist_items,
        }, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )

    authorization = {
        "approved_at_utc": (capacity_completed - timedelta(hours=1)).isoformat(),
        "valid_until_utc": (capacity_completed + timedelta(days=1)).isoformat(),
    }
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text(
        json.dumps(authorization, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    signature_path = tmp_path / "authorization.json.sig"
    signature_path.write_bytes(b"bound signature\n")
    cli_path = tmp_path / "codex.cmd"
    cli_path.write_bytes(b"@echo off\r\nnode codex.js %*\r\n")
    cli_raw = cli_path.read_bytes()
    reviewer_configuration = {
        "model": "reviewer-model",
        "reasoning_effort": "high",
        "concurrency": 1,
        "reviewer_cli_resolved_path": cli_path.resolve().as_posix(),
        "reviewer_cli_wrapper_raw_sha256": hashlib.sha256(cli_raw).hexdigest(),
        "reviewer_cli_wrapper_byte_count": len(cli_raw),
    }
    measurement_environment = {
        "reviewer_cli_resolved_path": cli_path.resolve().as_posix(),
        "reviewer_cli_wrapper_raw_sha256": hashlib.sha256(cli_raw).hexdigest(),
        "reviewer_cli_wrapper_byte_count": len(cli_raw),
        "reviewer_cli_version": "codex-cli test",
        "host_identity": codex_reviewer_batch._host_identity(),
    }
    capacity_plan_path = tmp_path / "capacity-plan.json"
    capacity_plan_path.write_text(
        json.dumps({
            "reviewer_configuration": reviewer_configuration,
            "validity": {"valid_for_hours": 1},
        }, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    capacity_result_path = tmp_path / "capacity-result.json"
    capacity_result_path.write_text(
        json.dumps({
            "completed_at_utc": capacity_completed.isoformat(),
            "reviewer_configuration": reviewer_configuration,
            "measurement_environment": measurement_environment,
        }, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    capacity_history_path = tmp_path / "capacity-history.jsonl"
    capacity_history_path.write_bytes(b'{"attempt":1}\n')
    monkeypatch.setattr(
        codex_reviewer_batch, "_reviewer_cli_version", lambda _codex: "codex-cli test")

    capacity_expires = capacity_completed + timedelta(hours=1)
    output_path = packets / "rulings.jsonl"
    output_path.write_bytes(b"")
    runner_path = Path(codex_reviewer_batch.__file__).resolve()
    guard = {
        "schema_version": codex_reviewer_batch.DISPATCH_GUARD_SCHEMA,
        "run_id": "guarded-two-packet-run",
        "manifest_canonical_sha256": "c" * 64,
        "authorization_canonical_sha256": (
            codex_reviewer_batch._canonical_sha256(authorization)
        ),
        "authorization_approved_at_utc": authorization["approved_at_utc"],
        "authorization_valid_until_utc": authorization["valid_until_utc"],
        "capacity_completed_at_utc": capacity_completed.isoformat(),
        "capacity_expires_at_utc": capacity_expires.isoformat(),
        "reviewer_model": "reviewer-model",
        "reviewer_reasoning_effort": "high",
        "reviewer_concurrency": 1,
        "reviewer_cli_version": "codex-cli test",
        "capacity_host_identity": codex_reviewer_batch._host_identity(),
        "packet_directory": packets.resolve().as_posix(),
        "output_path": output_path.resolve().as_posix(),
        "packet_bindings": packet_bindings,
        "artifact_bindings": {
            "authorization": _guard_binding(authorization_path),
            "authorization_signature": _guard_binding(signature_path),
            "capacity_plan": _guard_binding(capacity_plan_path),
            "capacity_result": _guard_binding(capacity_result_path),
            "capacity_dispatch_history": _guard_binding(capacity_history_path),
            "reviewer_cli_wrapper": _guard_binding(cli_path),
            "packet_index": _guard_binding(index_path),
            "worklist_snapshot": _guard_binding(worklist_path),
            "batch_runner": _guard_binding(runner_path),
        },
    }
    guard_path = packets / codex_reviewer_batch.DISPATCH_GUARD_FILENAME
    guard_raw = (
        json.dumps(guard, ensure_ascii=True, sort_keys=True, indent=1) + "\n"
    ).encode("utf-8")
    guard_path.write_bytes(guard_raw)
    argv = [
        "--packets", str(packets),
        "--out", str(output_path),
        "--codex", str(cli_path),
        "--model", "reviewer-model",
        "--effort", "high",
        "--concurrency", "1",
        "--not-after-utc", authorization["valid_until_utc"],
        "--dispatch-guard", str(guard_path),
        "--dispatch-guard-raw-sha256", hashlib.sha256(guard_raw).hexdigest(),
    ]
    return {
        "argv": argv,
        "authorization_path": authorization_path,
        "capacity_expires": capacity_expires,
        "guard": guard,
        "guard_path": guard_path,
        "guard_raw": guard_raw,
        "index_path": index_path,
        "output_path": output_path,
        "packets": packets,
    }


def _successful_reviewer_process(command):
    output_path = Path(command[command.index("-o") + 1])
    output_path.write_text(
        "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: valid\n",
        encoding="utf-8",
        newline="",
    )
    return SimpleNamespace(
        returncode=0,
        stdout=_event_stream(*_clean_events()),
        stderr=b"",
    )


def _second_packet_receipt(fixture) -> dict:
    second_packet = sorted(fixture["packets"].glob("*.txt"))[1]
    receipt_path = (
        fixture["packets"]
        / codex_reviewer_batch.EVIDENCE_DIRECTORY_NAME
        / f"{second_packet.name}.evidence"
        / "invocation_receipt.json"
    )
    return json.loads(receipt_path.read_text(encoding="utf-8"))


def _replace_cli_value(argv: list[str], flag: str, value: str) -> list[str]:
    changed = list(argv)
    changed[changed.index(flag) + 1] = value
    return changed


def test_an_unreachable_reviewer_raises_rather_than_ruling():
    with pytest.raises(ReviewerUnavailable):
        classify_result(_meta(), {"ok": False, "error": "exit=1, ruling_empty=True"},
                        packet_ok=True)


def test_a_quota_message_is_recognised_as_unreachable():
    with pytest.raises(ReviewerUnavailable):
        classify_result(_meta(), {"ok": False, "error": "You've hit your usage limit."},
                        packet_ok=True)


def test_tool_use_is_still_a_ruling_and_still_refused():
    """The reviewer WAS reached; its ruling is discarded as non-blind. That is a fact about
    the payload's review and must still be committed."""
    row = classify_result(_meta(), {"ok": True, "commands": ["ls"], "ruling": "x"},
                          packet_ok=True)
    assert row["status"] == "reviewer_error"
    assert "TOOL_USE_DETECTED" in row["raw_output"]


def test_packet_drift_aborts_without_manufacturing_a_decision():
    with pytest.raises(ReviewerUnavailable, match="packet bytes differ"):
        classify_result(_meta(), {"ok": True, "commands": [], "ruling": "x"},
                        packet_ok=False)


def test_a_clean_ruling_passes_through():
    row = classify_result(
        _meta(), {"ok": True, "commands": [],
                  "ruling": "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine"},
        packet_ok=True)
    # A clean ruling carries no "status": the commit path derives it from the parsed output.
    assert "status" not in row and row["tool_uses"] == 0


def test_empty_completed_ruling_is_retained_as_a_parse_failure(
    tmp_path, monkeypatch,
):
    packet, _cli, _events, result = _run_with_evidence(
        tmp_path,
        monkeypatch,
        events=_clean_events(
            {"type": "item.completed", "item": {"type": "reasoning"}},
            {"type": "item.completed", "item": {"type": "agent_message"}},
        ),
        ruling="",
    )
    assert result["ok"] is True
    assert result["raw_output"] == ""
    receipt = codex_reviewer_batch.validate_invocation_evidence(
        packet,
        result["evidence"],
        expected_model="reviewer-model",
        expected_effort="high",
        expected_concurrency=1,
    )
    assert receipt["outcome"]["normalized_ruling_byte_count"] == 0
    row = classify_result(_meta(), result, packet_ok=True)
    assert row["raw_output"] == ""
    assert "status" not in row


def test_expired_authorization_blocks_before_reviewer_subprocess(tmp_path, monkeypatch):
    packet = tmp_path / "packet.txt"
    packet.write_text("review this", encoding="utf-8")
    monkeypatch.setattr(
        codex_reviewer_batch.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("expired authority reached reviewer subprocess"),
    )
    result = codex_reviewer_batch.run_one(
        packet,
        "reviewer-model",
        "high",
        "codex",
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert result["ok"] is False
    assert "authorization deadline expired" in result["error"]


def test_capacity_expiry_after_first_packet_blocks_second_dispatch(
    tmp_path, monkeypatch,
):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    moments = iter((
        capacity_completed + timedelta(minutes=30),
        capacity_completed + timedelta(minutes=31),
        capacity_completed + timedelta(minutes=32),
        capacity_completed + timedelta(minutes=33),
        fixture["capacity_expires"] + timedelta(seconds=1),
        fixture["capacity_expires"] + timedelta(seconds=2),
    ))
    monkeypatch.setattr(codex_reviewer_batch, "_utc_now", lambda: next(moments))
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return _successful_reviewer_process(command)

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", fake_run)

    assert codex_reviewer_batch.main(fixture["argv"]) == 3
    assert len(calls) == 1
    assert len(fixture["output_path"].read_text(encoding="utf-8").splitlines()) == 1
    receipt = _second_packet_receipt(fixture)
    assert receipt["outcome"]["dispatch_attempted"] is False
    assert receipt["dispatch_guard"]["verified"] is False
    assert "capacity evidence is inactive" in receipt["dispatch_guard"]["error"]


def test_authority_mutation_during_first_packet_blocks_second_dispatch(
    tmp_path, monkeypatch,
):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    moments = iter(
        capacity_completed + timedelta(minutes=number)
        for number in (10, 11, 12, 13, 14, 15)
    )
    monkeypatch.setattr(codex_reviewer_batch, "_utc_now", lambda: next(moments))
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        result = _successful_reviewer_process(command)
        fixture["authorization_path"].write_bytes(b'{"mutated":true}\n')
        return result

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", fake_run)

    assert codex_reviewer_batch.main(fixture["argv"]) == 3
    assert len(calls) == 1
    assert len(fixture["output_path"].read_text(encoding="utf-8").splitlines()) == 1
    receipt = _second_packet_receipt(fixture)
    assert receipt["outcome"]["dispatch_attempted"] is False
    assert receipt["dispatch_guard"]["verified"] is False
    assert "authorization bytes drifted" in receipt["dispatch_guard"]["error"]


@pytest.mark.parametrize(
    "flag,value,error",
    [
        ("--model", "other-model", "model differs from runtime"),
        ("--effort", "medium", "effort differs from runtime"),
        ("--concurrency", "2", "concurrency differs from runtime"),
        ("--out", "other-rulings.jsonl", "output path differs from runtime"),
    ],
)
def test_guarded_batch_rejects_runtime_or_output_drift_without_reviewer_dispatch(
    tmp_path, monkeypatch, capsys, flag, value, error,
):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    monkeypatch.setattr(
        codex_reviewer_batch,
        "_utc_now",
        lambda: capacity_completed + timedelta(minutes=10),
    )
    calls = []

    def forbidden_reviewer(command, **_kwargs):
        calls.append(command)
        pytest.fail("drifted guarded runtime reached reviewer subprocess")

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", forbidden_reviewer)
    argv = _replace_cli_value(fixture["argv"], flag, str(tmp_path / value) if flag == "--out" else value)

    assert codex_reviewer_batch.main(argv) == 4
    assert calls == []
    assert error in capsys.readouterr().out


def test_guarded_batch_forbids_limit_before_reviewer_dispatch(tmp_path, monkeypatch):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    monkeypatch.setattr(
        codex_reviewer_batch.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("guarded --limit reached reviewer subprocess"),
    )

    with pytest.raises(SystemExit):
        codex_reviewer_batch.main([*fixture["argv"], "--limit", "1"])


@pytest.mark.parametrize("mutated_artifact", ["index", "packet"])
def test_guarded_batch_rechecks_queued_index_and_packet_before_second_dispatch(
    tmp_path, monkeypatch, mutated_artifact,
):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    monkeypatch.setattr(
        codex_reviewer_batch,
        "_utc_now",
        lambda: capacity_completed + timedelta(minutes=10),
    )
    calls = []

    def first_only(command, **_kwargs):
        calls.append(command)
        result = _successful_reviewer_process(command)
        if mutated_artifact == "index":
            fixture["index_path"].write_bytes(b'{"mutated":true}\n')
        else:
            second_packet = sorted(fixture["packets"].glob("*.txt"))[1]
            second_packet.write_bytes(b"mutated queued packet")
        return result

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", first_only)

    assert codex_reviewer_batch.main(fixture["argv"]) == 3
    assert len(calls) == 1
    receipt = _second_packet_receipt(fixture)
    assert receipt["outcome"]["dispatch_attempted"] is False
    assert receipt["dispatch_guard"]["reservation"] is None
    assert "drifted" in receipt["dispatch_guard"]["error"]


def test_guarded_batch_replay_to_second_output_makes_no_reviewer_call(
    tmp_path, monkeypatch, capsys,
):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    now = capacity_completed + timedelta(minutes=10)
    monkeypatch.setattr(codex_reviewer_batch, "_utc_now", lambda: now)
    calls = []

    def successful(command, **_kwargs):
        calls.append(command)
        return _successful_reviewer_process(command)

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", successful)
    assert codex_reviewer_batch.main(fixture["argv"]) == 0
    assert len(calls) == 2

    calls.clear()
    replay_argv = _replace_cli_value(
        fixture["argv"], "--out", str(tmp_path / "replayed-rulings.jsonl"))
    assert codex_reviewer_batch.main(replay_argv) == 4
    assert calls == []
    assert "output path differs from runtime" in capsys.readouterr().out


def test_existing_dispatch_reservation_aborts_cleanly_without_overwriting_evidence(
    tmp_path, monkeypatch,
):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    now = capacity_completed + timedelta(minutes=10)
    monkeypatch.setattr(codex_reviewer_batch, "_utc_now", lambda: now)
    first_packet = sorted(fixture["packets"].glob("*.txt"))[0]
    calls = []
    def successful(command, **_kwargs):
        calls.append(command)
        return _successful_reviewer_process(command)

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", successful)
    result = codex_reviewer_batch.run_one(
        first_packet,
        "reviewer-model",
        "high",
        fixture["argv"][fixture["argv"].index("--codex") + 1],
        datetime.fromisoformat(
            fixture["guard"]["authorization_valid_until_utc"]),
        1,
        fixture["guard_path"],
        hashlib.sha256(fixture["guard_raw"]).hexdigest(),
        fixture["output_path"],
    )
    assert result["ok"] is True
    assert len(calls) == 1

    calls.clear()
    assert codex_reviewer_batch.main(fixture["argv"]) == 3
    assert calls == []
    receipt_path = (
        fixture["packets"]
        / codex_reviewer_batch.EVIDENCE_DIRECTORY_NAME
        / f"{first_packet.name}.evidence"
        / "invocation_receipt.json"
    )
    first_receipt_raw = receipt_path.read_bytes()

    assert codex_reviewer_batch.main(fixture["argv"]) == 3
    assert calls == []
    assert receipt_path.read_bytes() == first_receipt_raw


def test_expiry_during_reservation_blocks_subprocess_after_consuming_reservation(
    tmp_path, monkeypatch,
):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    moments = iter((
        capacity_completed + timedelta(minutes=30),
        fixture["capacity_expires"] - timedelta(microseconds=1),
        fixture["capacity_expires"] + timedelta(microseconds=1),
        fixture["capacity_expires"] + timedelta(seconds=1),
    ))
    monkeypatch.setattr(codex_reviewer_batch, "_utc_now", lambda: next(moments))
    calls = []
    monkeypatch.setattr(
        codex_reviewer_batch.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command),
    )

    assert codex_reviewer_batch.main(fixture["argv"]) == 3
    assert calls == []
    first_packet = sorted(fixture["packets"].glob("*.txt"))[0]
    receipt_path = (
        fixture["packets"]
        / codex_reviewer_batch.EVIDENCE_DIRECTORY_NAME
        / f"{first_packet.name}.evidence"
        / "invocation_receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["outcome"]["dispatch_attempted"] is False
    assert receipt["dispatch_guard"]["reservation"] is not None
    assert "expired before subprocess release" in receipt["dispatch_guard"]["error"]


def test_successful_guarded_evidence_proves_reservation_and_cli_version(
    tmp_path, monkeypatch,
):
    capacity_completed = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(
        tmp_path, monkeypatch, capacity_completed=capacity_completed)
    now = capacity_completed + timedelta(minutes=10)
    monkeypatch.setattr(codex_reviewer_batch, "_utc_now", lambda: now)
    monkeypatch.setattr(
        codex_reviewer_batch.subprocess,
        "run",
        lambda command, **_kwargs: _successful_reviewer_process(command),
    )

    assert codex_reviewer_batch.main(fixture["argv"]) == 0
    first_row = json.loads(
        fixture["output_path"].read_text(encoding="utf-8").splitlines()[0])
    first_packet = sorted(fixture["packets"].glob("*.txt"))[0]
    receipt = codex_reviewer_batch.validate_invocation_evidence(
        first_packet,
        first_row["evidence"],
        expected_model="reviewer-model",
        expected_effort="high",
        expected_concurrency=1,
    )
    guard_evidence = receipt["dispatch_guard"]
    assert receipt["invocation"]["codex_cli_version"] == "codex-cli test"
    assert guard_evidence["reviewer_cli_version"] == "codex-cli test"
    reservation = guard_evidence["reservation"]
    reservation_path = fixture["packets"] / reservation["path"]
    assert reservation_path.is_file()
    reservation_value = json.loads(reservation_path.read_text(encoding="utf-8"))
    assert reservation_value["reserved_at_utc"] == guard_evidence["checked_at_utc"]
    assert reservation_value["guard_raw_sha256"] == hashlib.sha256(
        fixture["guard_raw"]).hexdigest()


def test_json_event_stream_accepts_only_complete_zero_tool_lifecycle():
    commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(*_clean_events(
            {"type": "item.completed", "item": {"type": "reasoning"}},
            {"type": "item.completed", "item": {"type": "agent_message"}},
        )))
    assert commands == []
    assert errors == []


def test_json_event_stream_reports_every_recognized_tool_item():
    commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(*_clean_events(
            {
                "type": "item.started",
                "item": {
                    "id": "tool-1",
                    "type": "command_execution",
                    "command": "Get-Content world.txt",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "tool-1",
                    "type": "command_execution",
                    "command": "Get-Content world.txt",
                },
            },
            {
                "type": "item.completed",
                "item": {"id": "tool-2", "type": "web_search"},
            },
        )))
    assert commands == ["Get-Content world.txt", "web_search"]
    assert errors == []


def test_json_event_stream_rejects_reordered_lifecycle():
    _commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(
            {"type": "thread.started", "thread_id": "thread-test"},
            {"type": "item.completed", "item": {"type": "agent_message"}},
            {"type": "turn.started"},
            {"type": "turn.completed", "usage": {}},
        ))

    assert any("outside the active turn" in error for error in errors)


@pytest.mark.parametrize(
    "drift_field, drift_value, match",
    [
        ("thread_id", "thread-other", "crosses thread association"),
        ("turn_id", "turn-other", "crosses turn association"),
    ],
)
def test_json_event_stream_rejects_cross_thread_or_turn_association(
    drift_field, drift_value, match,
):
    associated_item = {
        "type": "item.completed",
        "thread_id": "thread-test",
        "turn_id": "turn-test",
        "item": {"id": "message-1", "type": "agent_message"},
    }
    associated_item[drift_field] = drift_value
    _commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(
            {"type": "thread.started", "thread_id": "thread-test"},
            {"type": "turn.started", "turn_id": "turn-test"},
            associated_item,
            {"type": "turn.completed", "turn_id": "turn-test", "usage": {}},
        ))

    assert any(match in error for error in errors)


def test_json_event_stream_rejects_duplicate_item_completion():
    item = {"id": "message-1", "type": "agent_message"}
    _commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(*_clean_events(
            {"type": "item.completed", "item": item},
            {"type": "item.completed", "item": item},
        )))

    assert any("duplicates item.completed" in error for error in errors)


def test_json_event_stream_rejects_lifecycle_without_completed_item():
    _commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(
            {"type": "thread.started", "thread_id": "thread-test"},
            {"type": "turn.started"},
            {"type": "turn.completed", "usage": {}},
        ))

    assert "event stream completed without a completed item" in errors


def test_json_event_stream_rejects_reasoning_only_clean_lifecycle():
    commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(*_clean_events(
            {"type": "item.completed", "item": {"type": "reasoning"}},
        )))

    assert commands == []
    assert "event stream completed without a completed agent message" in errors


def test_json_event_stream_rejects_non_finite_json():
    _commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(
            {"type": "thread.started", "thread_id": "thread-test"},
            {"type": "turn.started", "score": float("nan")},
            {"type": "item.completed", "item": {"type": "agent_message"}},
            {"type": "turn.completed", "usage": {}},
        ))

    assert any("non-finite JSON number" in error for error in errors)


@pytest.mark.parametrize(
    "stream, match",
    [
        (b"not-json\n", "not unique-key JSON"),
        (
            _event_stream(*_clean_events({"type": "future.event"})),
            "unknown event type",
        ),
        (
            _event_stream(*_clean_events({
                "type": "item.completed",
                "item": {"type": "future_item"},
            })),
            "unknown item type",
        ),
        (
            _event_stream(
                {"type": "thread.started", "thread_id": "thread-test"},
                {"type": "turn.started"},
                {"type": "turn.failed", "error": {"message": "failure"}},
            ),
            "turn.failed",
        ),
    ],
)
def test_json_event_stream_rejects_unknown_malformed_or_failed_shapes(stream, match):
    _commands, errors = codex_reviewer_batch._inspect_json_event_stream(stream)
    assert any(match in error for error in errors)


def test_run_one_refuses_a_ruling_with_an_unrecognized_json_event(
    tmp_path, monkeypatch,
):
    packet = tmp_path / "packet.txt"
    packet.write_text("review this", encoding="utf-8")

    def fake_run(command, **_kwargs):
        out_file = command[command.index("-o") + 1]
        with open(out_file, "w", encoding="utf-8") as handle:
            handle.write("LABEL: ACCEPT\nCLAUSE: Allowed\nRATIONALE: valid\n")
        return SimpleNamespace(
            returncode=0,
            stdout=_event_stream(*_clean_events({"type": "future.event"})),
        )

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", fake_run)
    result = codex_reviewer_batch.run_one(
        packet, "reviewer-model", "high", "codex")
    assert result["ok"] is False
    assert "unknown event type" in result["error"]


def test_run_one_persists_exact_event_stream_and_runtime_receipt(
    tmp_path, monkeypatch,
):
    packet, cli, event_raw, result = _run_with_evidence(
        tmp_path,
        monkeypatch,
        events=_clean_events(
            {"type": "item.completed", "item": {"type": "reasoning"}},
            {"type": "item.completed", "item": {"type": "agent_message"}},
        ),
        stderr=b"diagnostic stderr\n",
    )

    assert result["ok"] is True
    reference = result["evidence"]
    receipt = codex_reviewer_batch.validate_invocation_evidence(
        packet,
        reference,
        expected_model="reviewer-model",
        expected_effort="high",
    )
    invocation = receipt["invocation"]
    outcome = receipt["outcome"]
    assert invocation["codex_cli_resolved_path"] == cli.resolve().as_posix()
    assert invocation["codex_cli_wrapper_raw_sha256"] == hashlib.sha256(
        cli.read_bytes()).hexdigest()
    assert invocation["model_requested"] == "reviewer-model"
    assert invocation["reasoning_effort_requested"] == "high"
    assert invocation["sandbox_mode"] == "read-only"
    assert invocation["ephemeral"] is True
    assert invocation["ignore_user_config"] is True
    assert invocation["ignore_rules"] is True
    assert outcome["dispatch_attempted"] is True
    assert outcome["result_ok"] is True
    assert outcome["commands"] == []
    assert outcome["event_stream_errors"] == []
    event_binding = receipt["artifacts"]["event_stream"]
    assert (packet.parent / event_binding["path"]).read_bytes() == event_raw

    row = classify_result(_meta(), result, packet_ok=True)
    assert row["tool_uses"] == 0
    assert row["evidence"] == reference


def test_tool_use_event_is_preserved_and_still_refused(tmp_path, monkeypatch):
    packet, _cli, event_raw, result = _run_with_evidence(
        tmp_path,
        monkeypatch,
        events=_clean_events({
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "Get-Content world.txt"},
        }),
    )

    assert result["ok"] is True
    assert result["commands"] == ["Get-Content world.txt"]
    row = classify_result(_meta(), result, packet_ok=True)
    assert row["status"] == "reviewer_error"
    assert "TOOL_USE_DETECTED" in row["raw_output"]
    receipt = codex_reviewer_batch.validate_invocation_evidence(
        packet, row["evidence"], expected_model="reviewer-model", expected_effort="high")
    assert receipt["outcome"]["commands"] == ["Get-Content world.txt"]
    binding = receipt["artifacts"]["event_stream"]
    assert (packet.parent / binding["path"]).read_bytes() == event_raw


def test_evidence_validation_rejects_event_stream_and_runtime_drift(
    tmp_path, monkeypatch,
):
    packet, _cli, _event_raw, result = _run_with_evidence(
        tmp_path, monkeypatch, events=_clean_events())
    reference = result["evidence"]
    with pytest.raises(ValueError, match="model differs"):
        codex_reviewer_batch.validate_invocation_evidence(
            packet, reference, expected_model="another-model")

    receipt = codex_reviewer_batch.validate_invocation_evidence(packet, reference)
    event_path = packet.parent / receipt["artifacts"]["event_stream"]["path"]
    event_path.write_bytes(event_path.read_bytes() + b"tamper\n")
    with pytest.raises(ValueError, match="bytes differ"):
        codex_reviewer_batch.validate_invocation_evidence(packet, reference)


def test_evidence_validation_rejects_receipt_schema_drift(tmp_path, monkeypatch):
    packet, _cli, _event_raw, result = _run_with_evidence(
        tmp_path, monkeypatch, events=_clean_events())
    reference = dict(result["evidence"])
    receipt_path = packet.parent / reference["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["unexpected"] = True
    raw = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(
        "utf-8")
    receipt_path.write_bytes(raw)
    reference["receipt_raw_sha256"] = hashlib.sha256(raw).hexdigest()
    reference["receipt_byte_count"] = len(raw)
    with pytest.raises(ValueError, match="receipt fields drifted"):
        codex_reviewer_batch.validate_invocation_evidence(packet, reference)


def test_evidence_validation_rejects_rebound_invocation_drift(tmp_path, monkeypatch):
    packet, _cli, _event_raw, result = _run_with_evidence(
        tmp_path, monkeypatch, events=_clean_events())
    reference = dict(result["evidence"])
    receipt_path = packet.parent / reference["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["invocation"]["model_requested"] = "tampered-model"
    raw = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(
        "utf-8")
    receipt_path.write_bytes(raw)
    reference["receipt_raw_sha256"] = hashlib.sha256(raw).hexdigest()
    reference["receipt_byte_count"] = len(raw)

    with pytest.raises(ValueError, match="argv differs"):
        codex_reviewer_batch.validate_invocation_evidence(packet, reference)


def test_evidence_validation_rejects_unanchored_batch_runner(tmp_path, monkeypatch):
    packet, _cli, _event_raw, result = _run_with_evidence(
        tmp_path, monkeypatch, events=_clean_events())
    reference = dict(result["evidence"])
    receipt_path = packet.parent / reference["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["invocation"]["batch_runner_raw_sha256"] = "0" * 64
    raw = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(
        "utf-8")
    receipt_path.write_bytes(raw)
    reference["receipt_raw_sha256"] = hashlib.sha256(raw).hexdigest()
    reference["receipt_byte_count"] = len(raw)

    with pytest.raises(ValueError, match="batch runner differs"):
        codex_reviewer_batch.validate_invocation_evidence(packet, reference)


def test_timeout_persists_partial_event_stream_before_aborting(tmp_path, monkeypatch):
    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    packet = packet_root / "00001_payload.txt"
    packet.write_text("review this exact packet", encoding="utf-8")
    partial = _event_stream(
        {"type": "thread.started", "thread_id": "partial-thread"},
        {"type": "turn.started"},
    )

    def fake_timeout(command, **_kwargs):
        raise codex_reviewer_batch.subprocess.TimeoutExpired(
            command, 600, output=partial, stderr=b"timeout stderr")

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", fake_timeout)
    result = codex_reviewer_batch.run_one(packet, "reviewer-model", "high", "codex")
    assert result["ok"] is False
    assert "timeout" in result["error"]
    receipt = codex_reviewer_batch.validate_invocation_evidence(
        packet,
        result["evidence"],
        expected_model="reviewer-model",
        expected_effort="high",
    )
    assert receipt["outcome"]["timed_out"] is True
    assert receipt["outcome"]["dispatch_attempted"] is True
    binding = receipt["artifacts"]["event_stream"]
    assert (packet.parent / binding["path"]).read_bytes() == partial
