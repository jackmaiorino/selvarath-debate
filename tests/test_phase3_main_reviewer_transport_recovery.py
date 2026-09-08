"""Signed transport changes preserve historical source and exact partial-wave bytes."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from rejudge import phase3_main_recovery as recovery
from rejudge import phase3_main_reviewer_recovery as reviewer
from test_phase3_main_recovery import stopped_run, write_json


@pytest.fixture
def repair_run(stopped_run, tmp_path):
    run = stopped_run
    code_root = tmp_path / "source"
    historical = {}
    for relative, key in reviewer.REPAIR_CODE_KEYS.items():
        path = code_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"historical source\r\n")
        historical[key] = reviewer.code_binding(path)
        path.write_bytes(b"replacement source\r\n")
    capacity_path = tmp_path / "capacity.json"
    write_json(capacity_path, {"repository": {"project_root": code_root.as_posix()},
                               "code_bindings": historical})
    run.manifest["input_bindings"]["capacity_execution_manifest"] = {
        "path": capacity_path.as_posix(), "sha256": reviewer.code_binding(capacity_path)["raw_sha256"]}
    write_json(run.manifest_path, run.manifest)
    start, _, _ = recovery._identity_paths(run.manifest)
    write_json(start, {"run_id": run.manifest["run_id"]})
    directory = Path(run.paths["review_packets_root"]) / "wave-105"
    directory.mkdir(parents=True)
    guard = {"run_id": run.manifest["run_id"], "packet_directory": directory.as_posix(),
             "output_path": (directory / "rulings.jsonl").as_posix(),
             "artifact_bindings": {"batch_runner": historical["reviewer_batch"]},
             "packet_bindings": [{"payload_sha256": "a" * 64, "file": "one.txt"},
                                 {"payload_sha256": "b" * 64, "file": "two.txt"}]}
    write_json(directory / "DISPATCH_GUARD.json", guard)
    write_json(directory / "WORKLIST.json", {"items": []})
    write_json(directory / "INDEX.json", {"items": []})
    (directory / "rulings.jsonl").write_bytes(b'{"saved":true}\n')
    guard_sha = reviewer.code_binding(directory / "DISPATCH_GUARD.json")["raw_sha256"]
    write_json(directory / ".reviewer_dispatch_reservations" / f"{guard_sha}_{'a' * 64}.json", {})
    value = reviewer.build_transport_repair(run.manifest, project_root=code_root,
                                             partial_wave_directories={105: directory})
    run.kwargs["reviewer_transport_repair"] = value
    run.directory, run.code_root = directory, code_root
    return run


def build(run):
    return recovery.build_recovery_manifest(run.manifest_path, run.auth_path, **run.kwargs)


def validate(run, value, **kwargs):
    return recovery.validate_recovery_manifest(value, manifest_path=run.manifest_path,
                                               authorization_path=run.auth_path, **kwargs)


def test_signed_repair_binds_historical_capacity_and_only_never_started_partition(repair_run):
    run = repair_run
    value = build(run)
    repair = value["reviewer_transport_repair"]
    wave, = repair["partial_waves"]
    assert wave["retained_payload_sha256s"] == ["a" * 64]
    assert wave["never_started_payload_sha256s"] == ["b" * 64]
    old, new = reviewer.accepted_batch_runner_bindings(value)
    assert old["raw_sha256"] != new["raw_sha256"]
    assert reviewer.historical_capacity_code_bindings(value)["reviewer_batch"] == old
    validate(run, value)


def test_partial_rulings_allow_append_but_not_overwriting_retained_rows(repair_run):
    run = repair_run
    value = build(run)
    path = run.directory / "rulings.jsonl"
    old = path.read_bytes()
    path.write_bytes(old + b'{"next":true}\n')
    validate(run, value)
    path.write_bytes(b'X' + path.read_bytes()[1:])
    with pytest.raises(recovery.RecoveryError, match="prefix changed"):
        validate(run, value)


@pytest.mark.parametrize("change", ["historical", "replacement", "third_file", "partition", "guard"])
def test_repair_refuses_source_or_wave_drift(repair_run, change):
    run = repair_run
    value = build(run)
    repair = value["reviewer_transport_repair"]
    if change in {"historical", "replacement"}:
        repair["code_replacements"]["scripts/codex_reviewer_batch.py"][change]["raw_sha256"] = "f" * 64
    elif change == "third_file":
        repair["code_replacements"]["unrelated.py"] = {}
    elif change == "partition":
        repair["partial_waves"][0]["never_started_payload_sha256s"] = ["a" * 64]
    else:
        (run.directory / "DISPATCH_GUARD.json").write_bytes(b"{}\n")
    with pytest.raises(recovery.RecoveryError):
        validate(run, value)


def test_builder_rejects_never_started_packet_with_retained_evidence(repair_run):
    run = repair_run
    evidence = run.directory / "reviewer_evidence" / "two.txt.evidence"
    evidence.mkdir(parents=True)
    with pytest.raises(ValueError, match="already has invocation evidence"):
        build(run)


def test_lightweight_repair_scope_does_not_read_artifacts(repair_run, monkeypatch):
    value = build(repair_run)["reviewer_transport_repair"]
    def denied(*args, **kwargs):
        raise AssertionError("lightweight signed-scope validation read an artifact")
    monkeypatch.setattr(Path, "read_bytes", denied)
    reviewer.validate_transport_repair(value, repair_run.manifest, verify_artifacts=False)


def test_new_snapshot_retains_prior_recovery_and_terminal_history(repair_run):
    run = repair_run
    with Path(run.paths["run_log"]).open("a") as handle:
        handle.write(json.dumps({"event": "formal_main_resumed", "recovery_path": "prior"}) + "\n")
    Path(run.paths["terminal_dispositions"]).write_bytes(b'{"existing_terminal":true}\n')
    value = build(run)
    assert value["preserved_prefixes"]["terminal_dispositions"]["size_bytes"] > 0
    validate(run, value)
