"""Signed transport changes preserve historical source and exact partial-wave bytes."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from rejudge import phase3_main_recovery as recovery
from rejudge import phase3_main_authorization as authorization
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


@pytest.fixture
def completed_reconnect_origin(repair_run, monkeypatch, tmp_path):
    run = repair_run
    original = build(run)
    original_path = tmp_path / "original-recovery.json"
    write_json(original_path, original)
    Path(str(original_path) + ".sig").write_bytes(b"valid test signature")
    signature_checks = []

    def authenticate(path, *, signature_namespace):
        signature_checks.append((Path(path), signature_namespace))
        if Path(str(path) + ".sig").read_bytes() != b"valid test signature":
            raise authorization.MainAuthorizationSignatureError("invalid signature")
        return json.loads(Path(path).read_bytes())

    monkeypatch.setattr(authorization, "load_authenticated_owner_authorization", authenticate)
    sidecar = run.directory / "reviewer_evidence" / "one.txt.evidence" / "reconnect_recovery.json"
    write_json(sidecar, {"recovery_manifest_sha256": reviewer.canonical_sha256(original),
                        "original_receipt": {"receipt_path": "reviewer_evidence/one.txt.evidence/invocation_receipt.json"}})
    receipt = {"schema_version": "phase3_main_reviewer_wave_commit_receipt_v1",
               "status": "committed", "wave": 105, "run_id": run.manifest["run_id"],
               "manifest_canonical_sha256": reviewer.canonical_sha256(run.manifest),
               "evidence_bindings": {"dispatch_guard_raw_sha256": reviewer.code_binding(
                   run.directory / "DISPATCH_GUARD.json")["raw_sha256"]}}
    for path_key, manifest_key, target_key in (
        ("decision_store_path", "decisions", "decisions_target"),
        ("reviewer_index_path", "reviewer_index", "reviewer_index_target"),
    ):
        path = Path(run.paths[manifest_key])
        path.write_bytes(b'{"retained_commit":105}\n')
        receipt[path_key] = path.resolve().as_posix()
        receipt[target_key] = reviewer.code_binding(path)
    write_json(run.directory / "WAVE_COMMIT_RECEIPT.json", receipt)
    run.kwargs["reviewer_transport_repair"] = reviewer.build_transport_repair(
        run.manifest, project_root=run.code_root, partial_wave_directories={},
        historical_recovery_paths=[original_path])
    current = build(run)
    current_path = tmp_path / "current-recovery.json"
    write_json(current_path, current)
    Path(str(current_path) + ".sig").write_bytes(b"valid test signature")
    run.original_path, run.current_path = original_path, current_path
    run.original, run.current, run.sidecar = original, current, sidecar
    run.signature_checks = signature_checks
    return run


def _load_origin_context(run, **kwargs):
    return reviewer.load_reviewer_recovery_context(run.current_path,
        packet_directory=run.directory, output_path=run.directory / "rulings.jsonl",
        guard_path=run.directory / "DISPATCH_GUARD.json",
        guard_raw_sha256=reviewer.code_binding(run.directory / "DISPATCH_GUARD.json")["raw_sha256"],
        **kwargs)


def test_fresh_signed_recovery_preserves_completed_reconnect_origin(completed_reconnect_origin):
    run = completed_reconnect_origin
    before = {p: p.read_bytes() for p in (run.original_path, Path(str(run.original_path) + ".sig"), run.sidecar)}
    context = _load_origin_context(run)
    assert context["recovery_manifest_sha256"] == reviewer.canonical_sha256(run.original)
    assert context["recovery_manifest_sha256"] != reviewer.canonical_sha256(run.current)
    assert Path(context["recovery_path"]) == run.original_path
    assert context["retained_payload_sha256s"] == context["never_started_payload_sha256s"] == []
    assert context["accepted_batch_runner_bindings"] == reviewer.accepted_batch_runner_bindings(run.current)
    assert all(namespace == recovery.RECOVERY_SIGNATURE_NAMESPACE for _, namespace in run.signature_checks)
    assert {p: p.read_bytes() for p in before} == before
    # Later unrelated store growth is allowed; the original committed prefixes remain exact.
    with Path(run.paths["decisions"]).open("ab") as handle:
        handle.write(b'{"later":true}\n')
    assert _load_origin_context(run, verify_artifacts=False)["recovery_manifest_sha256"] == context["recovery_manifest_sha256"]


@pytest.mark.parametrize("damage", ["signature", "origin_bytes", "sidecar", "guard", "unfinished",
                                     "cross_run", "wrong_scope", "recursive", "commit_prefix"])
def test_reconnect_origin_rejects_unproven_or_rebound_history(completed_reconnect_origin, damage):
    run = completed_reconnect_origin
    origin = run.current["reviewer_transport_repair"]["reconnect_origins"][0]
    if damage == "signature":
        path = Path(str(run.original_path) + ".sig")
        path.write_bytes(b"forged")
        origin["recovery_signature"] = reviewer.code_binding(path)
    elif damage == "origin_bytes":
        run.original_path.write_bytes(run.original_path.read_bytes() + b" ")
    elif damage == "sidecar":
        run.sidecar.write_bytes(b"{}\n")
    elif damage == "guard":
        origin["dispatch_guard"]["raw_sha256"] = "f" * 64
    elif damage == "unfinished":
        path = run.directory / "WAVE_COMMIT_RECEIPT.json"
        value = json.loads(path.read_bytes())
        value["status"] = "pending"
        write_json(path, value)
        origin["commit_receipt"] = reviewer.code_binding(path)
    elif damage == "commit_prefix":
        Path(run.paths["decisions"]).write_bytes(b"damaged prefix\n")
    else:
        old = copy.deepcopy(run.original)
        if damage == "cross_run":
            old["run_id"] = "another-run"
        elif damage == "wrong_scope":
            old["reviewer_transport_repair"]["partial_waves"][0]["retained_payload_sha256s"] = []
        else:
            old["reviewer_transport_repair"]["reconnect_origins"] = [origin]
        write_json(run.original_path, old)
        origin["recovery_manifest"] = reviewer.code_binding(run.original_path)
        origin["recovery_manifest_sha256"] = reviewer.canonical_sha256(old)
    with pytest.raises((ValueError, recovery.RecoveryError)):
        reviewer.validate_transport_repair(run.current["reviewer_transport_repair"], run.manifest)


def test_reconnect_origin_cannot_also_authorize_partial_wave_dispatch(completed_reconnect_origin):
    run = completed_reconnect_origin
    repair = run.current["reviewer_transport_repair"]
    repair["partial_waves"] = run.original["reviewer_transport_repair"]["partial_waves"]
    with pytest.raises(ValueError, match="completed historical"):
        reviewer.validate_transport_repair(repair, run.manifest, verify_artifacts=False)


def test_origin_loader_revalidates_original_failed_receipt_through_batch(tmp_path, monkeypatch):
    from scripts import codex_reviewer_batch as batch
    from test_reviewer_partial_batch_recovery import _partial_wave

    real_loader = reviewer.load_reviewer_recovery_context
    fixture = _partial_wave(tmp_path, monkeypatch, count=3, retained_count=3)
    directory = fixture["packets"]
    manifest = {"run_id": fixture["guard"]["run_id"], "output_contract": {
        "artifact_root": tmp_path.as_posix(), "paths": {
            "review_packets_root": directory.parent.as_posix(),
            "decisions": (tmp_path / "decisions.jsonl").as_posix(),
            "reviewer_index": (tmp_path / "reviewer-index.jsonl").as_posix()}}}
    manifest_path = tmp_path / "original-manifest.json"
    write_json(manifest_path, manifest)
    binding = fixture["context"]["accepted_batch_runner_bindings"][0]
    partial = {"wave": 105, "packet_directory": directory.as_posix(),
        "dispatch_guard": reviewer.code_binding(fixture["guard_path"]),
        "retained_payload_sha256s": fixture["context"]["retained_payload_sha256s"],
        "never_started_payload_sha256s": []}
    original = {"run_id": manifest["run_id"], "artifact_root": tmp_path.as_posix(),
        "original_manifest": {"path": manifest_path.as_posix()},
        "original_authorization": {"path": fixture["authorization_path"].as_posix()},
        "original_manifest_canonical_sha256": reviewer.canonical_sha256(manifest),
        "reviewer_transport_repair": {"partial_waves": [partial],
            "historical_guards": [partial["dispatch_guard"]],
            "code_replacements": {relative: {"historical": binding, "replacement": binding}
                                  for relative in reviewer.REPAIR_CODE_KEYS}}}
    original_path = tmp_path / "origin.json"
    write_json(original_path, original)
    Path(str(original_path) + ".sig").write_bytes(b"signed fixture")
    original_sha = reviewer.canonical_sha256(original)
    fixture["context"].update(recovery_path=original_path.as_posix(), recovery_manifest_sha256=original_sha)
    batch.recover_retained_batch_results(directory, fixture["output_path"],
        expected_model="reviewer-model", expected_effort="high", expected_concurrency=1,
        reviewer_recovery_context=fixture["context"])
    packet = directory / fixture["items"][2]["file"]
    sidecar = batch._evidence_directory(packet) / batch.RECONNECT_RECOVERY_FILENAME
    sidecar_bytes = sidecar.read_bytes()
    original_receipt = batch._evidence_directory(packet) / "invocation_receipt.json"
    original_receipt_bytes = original_receipt.read_bytes()
    committed = {"schema_version": "phase3_main_reviewer_wave_commit_receipt_v1", "status": "committed",
        "wave": 105, "run_id": manifest["run_id"],
        "manifest_canonical_sha256": reviewer.canonical_sha256(manifest),
        "evidence_bindings": {"dispatch_guard_raw_sha256": partial["dispatch_guard"]["raw_sha256"]}}
    write_json(directory / "WAVE_COMMIT_RECEIPT.json", committed)
    current = copy.deepcopy(original)
    current["reviewer_transport_repair"]["partial_waves"] = []
    current["reviewer_transport_repair"]["reconnect_origins"] = [{
        "packet_directory": directory.as_posix(), "recovery_manifest": reviewer.code_binding(original_path),
        "recovery_signature": reviewer.code_binding(str(original_path) + ".sig"),
        "recovery_manifest_sha256": original_sha, "dispatch_guard": partial["dispatch_guard"],
        "commit_receipt": reviewer.code_binding(directory / "WAVE_COMMIT_RECEIPT.json"),
        "attestations": [reviewer.code_binding(sidecar)]}]
    current_path = tmp_path / "next.json"
    write_json(current_path, current)
    Path(str(current_path) + ".sig").write_bytes(b"signed fixture")

    def signed_fixture(path, **kwargs):
        assert Path(str(path) + ".sig").read_bytes() == b"signed fixture"
        value = json.loads(Path(path).read_bytes())
        return value | {"recovery_manifest_sha256": reviewer.canonical_sha256(value)}

    monkeypatch.setattr(recovery, "load_authenticated_recovery", signed_fixture)
    monkeypatch.setattr(reviewer, "load_reviewer_recovery_context", real_loader)
    arguments = dict(packet_directory=directory, output_path=fixture["output_path"],
        guard_path=fixture["guard_path"], guard_raw_sha256=partial["dispatch_guard"]["raw_sha256"],
        verify_artifacts=False)
    context = real_loader(current_path, **arguments)
    receipt = batch.validate_invocation_evidence(packet, fixture["results"][2]["evidence"],
        reviewer_recovery_context=context)
    assert receipt["outcome"]["result_ok"] is True
    assert receipt["transport_recovery"]["original_outcome"]["result_ok"] is False
    assert sidecar.read_bytes() == sidecar_bytes
    assert original_receipt.read_bytes() == original_receipt_bytes
    assert fixture["calls"] == []
    del current["reviewer_transport_repair"]["reconnect_origins"]
    write_json(current_path, current)
    rebound = real_loader(current_path, **arguments)
    with pytest.raises(ValueError, match="attestation differs"):
        batch.validate_invocation_evidence(packet, fixture["results"][2]["evidence"],
            reviewer_recovery_context=rebound)
