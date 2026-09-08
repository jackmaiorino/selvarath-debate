"""Retained transport completion is recovered once; never-started work alone is dispatched."""
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import codex_reviewer_batch as batch
from rejudge import phase3_main_reviewer_recovery as recovery
from test_reviewer_unavailable_aborts import (
    _clean_events, _event_stream, _guard_binding, _guarded_two_packet_batch,
    _run_with_evidence,
)

RULING = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fixture only\n"
WARNING = ("Reconnecting... 1/5 (stream disconnected before completion: "
           "Transport error: network error: error decoding response body)")


def _reconnect_events(message=WARNING, *, text=RULING):
    return _clean_events(
        {"type": "error", "message": message},
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}},
    )


def test_successful_reconnect_is_bound_to_exact_retained_message(tmp_path, monkeypatch):
    packet, _, _, result = _run_with_evidence(
        tmp_path, monkeypatch, events=_reconnect_events(), ruling=RULING)
    assert result["ok"] is True
    receipt = batch.validate_invocation_evidence(packet, result["evidence"])
    assert receipt["outcome"]["result_ok"] is True
    assert "reconnect_recovery" not in receipt


@pytest.mark.parametrize("warning", [
    "prefix " + WARNING, WARNING + " trailing", WARNING.replace("1/5", "0/5"),
    WARNING.replace("1/5", "6/5"), WARNING.replace("1/5", "2/5"),
    "unrelated error mentions Reconnecting...", None, 1,
])
def test_other_errors_cannot_be_reclassified_as_reconnect(warning):
    _, errors = batch._inspect_reviewer_execution(_event_stream(*_reconnect_events(warning)), RULING.encode())
    assert errors


@pytest.mark.parametrize("text", [None, "", "other retained message", RULING.lower()])
def test_reconnect_requires_nonempty_exact_message(text):
    _, errors = batch._inspect_reviewer_execution(_event_stream(*_reconnect_events(text=text)), RULING.encode())
    assert errors


def test_reconnect_failure_and_multiple_messages_remain_fatal():
    events = list(_reconnect_events())
    events[-1] = {"type": "turn.failed", "error": {"message": "failed"}}
    assert batch._inspect_reviewer_execution(_event_stream(*events), RULING.encode())[1]


def test_reconnect_accepts_numbered_attempts_only_inside_the_completed_turn():
    events = list(_reconnect_events())
    for number in range(2, 6):
        events.insert(number + 1, {"type": "error", "message": WARNING.replace("1/5", f"{number}/5")})
    assert batch._inspect_reviewer_execution(_event_stream(*events), RULING.encode()) == ([], [])
    events.append({"type": "error", "message": WARNING})
    assert batch._inspect_reviewer_execution(_event_stream(*events), RULING.encode())[1]
    events = list(_reconnect_events())
    events.insert(-1, {"type": "item.completed", "item": {
        "type": "agent_message", "id": "item_1", "text": RULING}})
    assert batch._inspect_reviewer_execution(_event_stream(*events), RULING.encode())[1]


def _partial_wave(tmp_path, monkeypatch, *, count=60, retained_count=20):
    started = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    fixture = _guarded_two_packet_batch(tmp_path, monkeypatch, capacity_completed=started)
    monkeypatch.setattr(batch, "_utc_now", lambda: started + timedelta(minutes=20))
    items, worklist_items, bindings = [], [], []
    for number in range(1, count + 1):
        query, a, b = f"query {number}", f"candidate a {number}", f"candidate b {number}"
        payload = batch._payload_sha256(query, a, b)
        packet = fixture["packets"] / f"{number:05d}_{payload[:12]}.txt"
        raw = f"review exact packet {number}".encode()
        packet.write_bytes(raw)
        prompt = hashlib.sha256(raw).hexdigest()
        items.append({"n": number, "file": packet.name, "payload_sha256": payload, "prompt_sha256": prompt})
        worklist_items.append({"payload_sha256": payload, "query": query, "candidate_a": a,
                               "candidate_b": b, "subagent_prompt": raw.decode(), "subagent_prompt_sha256": prompt})
        bindings.append({"file": packet.name, "payload_sha256": payload, "prompt_sha256": prompt, "byte_count": len(raw)})
    fixture["index_path"].write_text(json.dumps({"count": count, "items": items}) + "\n", encoding="utf-8")
    worklist_path = fixture["packets"] / "WORKLIST.json"
    worklist_path.write_text(json.dumps({"frozen_prompt_sha256": "d" * 64,
        "separator": "\n\n---PAYLOAD---\n", "items": worklist_items}) + "\n", encoding="utf-8")
    guard = fixture["guard"]
    guard["packet_bindings"] = bindings
    guard["artifact_bindings"]["packet_index"] = _guard_binding(fixture["index_path"])
    guard["artifact_bindings"]["worklist_snapshot"] = _guard_binding(worklist_path)
    fixture["guard_path"].write_text(json.dumps(guard, sort_keys=True) + "\n", encoding="utf-8")
    guard_sha = _guard_binding(fixture["guard_path"])["raw_sha256"]
    fixture["argv"][-1] = guard_sha
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(kwargs["input"])
        number = int(kwargs["input"].decode().rsplit(" ", 1)[1])
        Path(argv[argv.index("-o") + 1]).write_bytes(RULING.encode())
        events = _reconnect_events() if number == 3 else _clean_events(
            {"type": "item.completed", "item": {"type": "agent_message", "text": RULING}})
        return SimpleNamespace(returncode=0, stdout=_event_stream(*events), stderr=b"")

    monkeypatch.setattr(batch.subprocess, "run", fake_run)
    modern = batch._inspect_reviewer_execution
    monkeypatch.setattr(batch, "_inspect_reviewer_execution", lambda e, r:
                        batch._inspect_json_event_stream(e, allow_reconnect_warnings=False))
    results = []
    for item in items[:retained_count]:
        results.append(batch.run_one(fixture["packets"] / item["file"], "reviewer-model", "high",
            fixture["argv"][fixture["argv"].index("--codex") + 1],
            batch._active_deadline(guard["authorization_valid_until_utc"]), 1,
            fixture["guard_path"], guard_sha, fixture["output_path"]))
    monkeypatch.setattr(batch, "_inspect_reviewer_execution", modern)
    successful = [(i, r) for i, r in zip(items, results) if r["ok"]]
    with fixture["output_path"].open("ab") as handle:
        for item, result in successful[:7]:
            row = batch.classify_result(item, result, packet_ok=True)
            handle.write((json.dumps(row) + "\n").encode())
    runner = guard["artifact_bindings"]["batch_runner"]
    context = {"recovery_path": str(tmp_path / "signed-recovery.json"),
        "recovery_manifest_sha256": "f" * 64,
        "accepted_batch_runner_bindings": [runner], "replacement_batch_runner": runner,
        "dispatch_guard": _guard_binding(fixture["guard_path"]),
        "retained_payload_sha256s": [i["payload_sha256"] for i in items[:retained_count]],
        "never_started_payload_sha256s": [i["payload_sha256"] for i in items[retained_count:]]}
    monkeypatch.setattr(recovery, "load_reviewer_recovery_context", lambda *a, **kw: context)
    fixture.update(items=items, results=results, calls=calls, context=context)
    calls.clear()
    return fixture


def test_partial_wave_preserves_twenty_invocations_and_dispatches_only_forty(tmp_path, monkeypatch):
    fixture = _partial_wave(tmp_path, monkeypatch)
    root = fixture["packets"]
    prefix = fixture["output_path"].read_bytes()
    original = {p: p.read_bytes() for p in (root / batch.EVIDENCE_DIRECTORY_NAME).rglob("*") if p.is_file()}
    assert len(original) == 80
    assert len(prefix.splitlines()) == 7
    argv = fixture["argv"] + ["--resume-retained", "--recovery", fixture["context"]["recovery_path"]]
    assert batch.main(argv) == 0
    assert len(fixture["calls"]) == 40
    assert all(int(raw.decode().rsplit(" ", 1)[1]) > 20 for raw in fixture["calls"])
    after = fixture["output_path"].read_bytes()
    assert after.startswith(prefix)
    rows = [json.loads(line) for line in after.splitlines()]
    assert len(rows) == len({r["payload_sha256"] for r in rows}) == 60
    assert all(p.read_bytes() == raw for p, raw in original.items())
    packet = root / fixture["items"][2]["file"]
    receipt = batch.validate_invocation_evidence(packet, fixture["results"][2]["evidence"],
        reviewer_recovery_context=fixture["context"])
    assert receipt["outcome"]["result_ok"] is True
    assert receipt["transport_recovery"]["original_outcome"]["result_ok"] is False
    assert (batch._evidence_directory(packet) / batch.RECONNECT_RECOVERY_FILENAME).exists()
    fixture["calls"].clear()
    assert batch.main(argv) == 0
    assert fixture["calls"] == []
    assert fixture["output_path"].read_bytes() == after


@pytest.mark.parametrize("damage", ["receipt", "row", "missing_evidence", "extra_evidence", "attestation"])
def test_partial_wave_refuses_drift_before_import_or_dispatch(tmp_path, monkeypatch, damage):
    fixture = _partial_wave(tmp_path, monkeypatch)
    packet = fixture["packets"] / fixture["items"][2]["file"]
    evidence = batch._evidence_directory(packet)
    if damage == "receipt":
        receipt_path = evidence / "invocation_receipt.json"
        value = json.loads(receipt_path.read_bytes())
        value["outcome"]["process_exit_code"] = 1
        receipt_path.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "row":
        rows = [json.loads(line) for line in fixture["output_path"].read_bytes().splitlines()]
        rows[0]["raw_output"] = "altered"
        fixture["output_path"].write_bytes(b"".join((json.dumps(r) + "\n").encode() for r in rows))
    elif damage == "missing_evidence":
        (evidence / "ruling.txt").unlink()
    elif damage == "extra_evidence":
        (fixture["packets"] / batch.EVIDENCE_DIRECTORY_NAME / "unbound.evidence").mkdir()
    else:
        (evidence / batch.RECONNECT_RECOVERY_FILENAME).write_bytes(b"{}\n")
    prefix = fixture["output_path"].read_bytes()
    argv = fixture["argv"] + ["--resume-retained", "--recovery", fixture["context"]["recovery_path"]]
    assert batch.main(argv) == 3
    assert fixture["calls"] == []
    assert fixture["output_path"].read_bytes() == prefix


def test_unsigned_resume_cannot_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "run_one", lambda *a, **kw: pytest.fail("must not dispatch"))
    with pytest.raises(SystemExit):
        batch.main(["--packets", str(tmp_path), "--out", str(tmp_path / "out"), "--resume-retained"])


def test_failed_batch_drains_and_retains_already_running_success(tmp_path, monkeypatch):
    items = []
    for number in (1, 2):
        packet = tmp_path / f"packet{number}.txt"
        packet.write_bytes(str(number).encode())
        items.append({"file": packet.name, "payload_sha256": str(number) * 64,
                      "prompt_sha256": hashlib.sha256(packet.read_bytes()).hexdigest()})
    (tmp_path / "INDEX.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    barrier = threading.Barrier(2)

    def fake_run_one(packet, *args, **kwargs):
        barrier.wait(timeout=3)
        prompt_sha = hashlib.sha256(packet.read_bytes()).hexdigest()
        if packet.name == "packet1.txt":
            return {"ok": False, "error": "transport failure", "prompt_sha256": prompt_sha}
        time.sleep(0.05)
        return {"ok": True, "raw_output": RULING, "commands": [], "prompt_sha256": prompt_sha}

    monkeypatch.setattr(batch, "run_one", fake_run_one)
    out = tmp_path / "rulings.jsonl"
    assert batch.main(["--packets", str(tmp_path), "--out", str(out), "--concurrency", "2"]) == 3
    rows = [json.loads(line) for line in out.read_bytes().splitlines()]
    assert len(rows) == 1
    assert rows[0]["payload_sha256"] == "2" * 64
