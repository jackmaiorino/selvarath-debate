"""Saved-analysis closeout: preserve outputs, authenticate scope, and validate once."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from rejudge import phase3_main_live as live
from test_phase3_main_live import ROOT, _prepared, _write_valid_analysis_snapshot, inventory


SAVED_SOURCE = "eff7f94ee379a993b9a052cc3d1270ce17ce4f83"


@pytest.fixture
def saved(tmp_path, inventory, monkeypatch):
    prepared = _prepared(tmp_path, inventory)
    prepared = replace(prepared, project_root=ROOT, protocol={
        **prepared.protocol, "roster": {**prepared.protocol["roster"], "oracle": "fixture oracle"}})
    live._identity_complete_path(prepared.identity).parent.mkdir(parents=True)
    analysis = _write_valid_analysis_snapshot(prepared, monkeypatch, finalization_sha256="1" * 64)
    paths = prepared.identity.paths
    finalization = {"recorded_at_utc": "2026-09-11T16:00:00Z", "artifact_hashes": {
        "result_store": {"raw_sha256": live._raw_sha256(paths.results)}}}
    paths.finalization.write_text(json.dumps(finalization, indent=2) + "\n", encoding="utf-8")
    analysis["integrity"]["finalization_raw_sha256"] = live._raw_sha256(paths.finalization)
    for field in ("repository_head_at_analysis", "engine_git_commit"):
        analysis["integrity"][field] = SAVED_SOURCE
    paths.analysis_results.write_text(json.dumps(analysis, indent=2) + "\n", encoding="utf-8")
    def binding(path):
        return {"path": path.as_posix(), "raw_sha256": live._raw_sha256(path),
                "byte_count": path.stat().st_size}
    validation = {"saved_analysis_continuation": {
        "schema_version": "phase3_saved_analysis_continuation_v1",
        "run_id": prepared.identity.run_id,
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        "admission": binding(paths.finalization), "analysis": binding(paths.analysis_results),
        "analysis_source_commit": SAVED_SOURCE,
        "analysis_engine_raw_sha256": live._raw_sha256(Path(live.phase3_main_analysis.__file__))}}
    validation_path = tmp_path / "validation.json"
    current = {"execution_source_commit": "a" * 40, "recovery_raw_sha256": "b" * 64,
               "recovery_signature_raw_sha256": "c" * 64}
    def bind_validation():
        validation_path.write_text(json.dumps(validation), encoding="utf-8")
        current["validation_record"] = live.phase3_main_recovery._file_binding(validation_path)
    bind_validation()
    prepared = replace(prepared, recovery_path=tmp_path / "recovery.json", recovery_validation=current)
    events = []
    # Only the signature/full-archive boundaries are isolated. The actual validation
    # record bytes, historical Git engine, analysis envelope, and output publication run.
    def revalidate(_prepared, **_kwargs):
        events.append("signed-recovery")
        return current
    monkeypatch.setattr(live, "_revalidate_recovery", revalidate)
    monkeypatch.setattr(live, "_revalidate_authenticated_authorization_scope",
                        lambda _prepared: prepared.authorization)
    monkeypatch.setattr(live, "_revalidate_final_boundary_inputs",
                        lambda *_args: events.append("boundary"))
    def validate(record, **inputs):
        assert record == finalization
        assert inputs["result_store_path"] == paths.results
        assert inputs["manifest_canonical_sha256"] == prepared.identity.manifest_sha256
        assert inputs["authorization_raw_sha256"] == prepared.authorization_raw_sha256
        events.append("full-validation")
    monkeypatch.setattr(live.phase3_main_finalization, "validate_finalization_admission", validate)
    def forbidden(*_args, **_kwargs):
        pytest.fail("saved closeout attempted analysis or fresh admission work")
    monkeypatch.setattr(live.phase3_main_analysis, "run_analysis", forbidden)
    monkeypatch.setattr(live.phase3_main_finalization, "build_finalization_admission", forbidden)
    monkeypatch.setattr(live.phase3_main_finalization, "write_finalization_admission", forbidden)
    def hashes(_prepared):
        events.append("hash")
        return {name: live._raw_sha256(getattr(paths, name))
                for name in ("results", "finalization", "analysis_results")}
    monkeypatch.setattr(live, "_completion_output_hashes", hashes)
    monkeypatch.setattr(live, "_require_completion_hashes_match_finalization", lambda *_args: None)
    terminal = live.phase3_main_finalization.MainTerminalDispositionStore(
        tmp_path / "terminal.jsonl", run_id=prepared.identity.run_id,
        manifest_canonical_sha256=prepared.identity.manifest_sha256, inventory=prepared.inventory)
    kwargs = {"resume_existing_admission": True, "resume_existing_analysis": True,
              "expected_existing_admission_raw_sha256": live._raw_sha256(paths.finalization),
              "expected_existing_analysis_raw_sha256": live._raw_sha256(paths.analysis_results),
              "expected_analysis_source_commit": SAVED_SOURCE}
    return SimpleNamespace(prepared=prepared, paths=paths, terminal=terminal, kwargs=kwargs,
        current=current, validation=validation, validation_path=validation_path,
        bind_validation=bind_validation, events=events)


def test_saved_analysis_completes_once_without_changing_saved_bytes_or_time(saved):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in (saved.paths.finalization, saved.paths.analysis_results)}
    result = live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert result["status"] == "complete"
    assert result["execution_source_commit"] == "a" * 40
    assert saved.events == ["signed-recovery", "boundary", "full-validation", "signed-recovery", "hash", "hash"]
    assert all((p.read_bytes(), p.stat().st_mtime_ns) == value for p, value in before.items())
    identity = json.loads(live._identity_complete_path(saved.prepared.identity).read_bytes())
    assert identity["completion_raw_sha256"] == live._raw_sha256(saved.paths.completion)
    assert result["analysis_results_raw_sha256"] == saved.kwargs["expected_existing_analysis_raw_sha256"]


@pytest.mark.parametrize("field,value", [
    ("resume_existing_admission", False),
    ("expected_existing_analysis_raw_sha256", None),
    ("expected_existing_admission_raw_sha256", "0" * 64),
    ("expected_existing_analysis_raw_sha256", "A" * 64),
    ("expected_analysis_source_commit", None),
    ("expected_analysis_source_commit", "b" * 40),
])
def test_saved_analysis_requires_exact_external_scope(saved, field, value):
    with pytest.raises(live.Phase3MainLiveError):
        live._finalize_main(saved.prepared, saved.terminal, **{**saved.kwargs, field: value})
    assert "full-validation" not in saved.events
    assert not saved.paths.completion.exists()


@pytest.mark.parametrize("field", ["run_id", "manifest_canonical_sha256", "analysis_source_commit",
                                   "analysis_engine_raw_sha256", "admission", "analysis"])
def test_saved_analysis_rejects_other_signed_binding(saved, field):
    saved.validation["saved_analysis_continuation"][field] = "wrong"
    saved.bind_validation()
    with pytest.raises(live.Phase3MainLiveError, match="signed continuation scope"):
        live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert "full-validation" not in saved.events


@pytest.mark.parametrize("failure", ["missing-authority", "signature", "validation-bytes", "engine"])
def test_saved_analysis_rejects_untrusted_authority_or_changed_engine(saved, monkeypatch, failure):
    if failure == "missing-authority":
        monkeypatch.setattr(live, "_revalidate_recovery", lambda *_args: None)
    elif failure == "signature":
        def bad_signature(*_args):
            raise live.Phase3MainLiveError("signature changed")
        monkeypatch.setattr(live, "_revalidate_recovery", bad_signature)
    elif failure == "validation-bytes":
        saved.validation_path.write_bytes(saved.validation_path.read_bytes() + b" ")
    else:
        monkeypatch.setattr(live.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=b"changed engine"))
    with pytest.raises(live.Phase3MainLiveError):
        live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert "full-validation" not in saved.events
    assert not saved.paths.completion.exists()


@pytest.mark.parametrize("target", ["analysis_results", "finalization", "completion", "identity",
                                   "analysis-temp", "finalization-temp", "completion-temp", "identity-temp"])
def test_saved_analysis_rejects_partial_or_changed_outputs(saved, target):
    paths = {"identity": live._identity_complete_path(saved.prepared.identity),
             "analysis": saved.paths.analysis_results, "finalization": saved.paths.finalization,
             "completion": saved.paths.completion, "analysis_results": saved.paths.analysis_results}
    path = (live._exclusive_publish_temp_path(paths[target.removesuffix("-temp")])
            if target.endswith("-temp") else paths[target])
    original = path.read_bytes() if path.exists() else b""
    path.write_bytes(original + b"untrusted")
    with pytest.raises(live.Phase3MainLiveError):
        live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert "full-validation" not in saved.events
    assert path.read_bytes() == original + b"untrusted"


@pytest.mark.parametrize("mutation", ["analysis", "admission", "validation", "completion", "identity-temp", "inputs"])
def test_saved_analysis_rejects_drift_during_long_validation(saved, monkeypatch, mutation):
    def validate(*_args, **_kwargs):
        saved.events.append("full-validation")
        path = {"analysis": saved.paths.analysis_results, "admission": saved.paths.finalization,
                "validation": saved.validation_path, "completion": saved.paths.completion,
                "identity-temp": live._exclusive_publish_temp_path(live._identity_complete_path(saved.prepared.identity))}.get(mutation)
        if path is None:
            raise live.phase3_main_finalization.MainFinalizationError("bound input changed")
        path.write_bytes((path.read_bytes() if path.exists() else b"") + b"changed")
    monkeypatch.setattr(live.phase3_main_finalization, "validate_finalization_admission", validate)
    with pytest.raises((live.Phase3MainLiveError, live.phase3_main_finalization.MainFinalizationError)):
        live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert saved.events.count("full-validation") == 1
    assert not live._identity_complete_path(saved.prepared.identity).exists()
    if mutation != "completion":
        assert not saved.paths.completion.exists()


def test_saved_analysis_keeps_default_current_source_enforcement(saved):
    with pytest.raises(live.Phase3MainLiveError, match="integrity"):
        live._validate_analysis_result_snapshot(saved.prepared,
            expected_finalization_raw_sha256=saved.kwargs["expected_existing_admission_raw_sha256"],
            expected_results_raw_sha256=live._raw_sha256(saved.paths.results),
            expected_analysis_raw=saved.paths.analysis_results.read_bytes())


def test_saved_analysis_keeps_final_boundary_authority_check(saved, monkeypatch):
    def reject(*_args):
        raise live.Phase3MainLiveError("final authorization changed")
    monkeypatch.setattr(live, "_revalidate_final_boundary_inputs", reject)
    with pytest.raises(live.Phase3MainLiveError, match="final authorization"):
        live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert "full-validation" not in saved.events
    assert not saved.paths.completion.exists()


def test_saved_analysis_signature_does_not_override_scientific_pins(saved):
    value = json.loads(saved.paths.analysis_results.read_bytes())
    value["bootstrap"]["B"] += 1
    saved.paths.analysis_results.write_text(json.dumps(value), encoding="utf-8")
    binding = saved.validation["saved_analysis_continuation"]["analysis"]
    binding.update(raw_sha256=live._raw_sha256(saved.paths.analysis_results),
                   byte_count=saved.paths.analysis_results.stat().st_size)
    saved.kwargs["expected_existing_analysis_raw_sha256"] = binding["raw_sha256"]
    saved.bind_validation()
    with pytest.raises(live.Phase3MainLiveError, match="bootstrap"):
        live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert saved.events.count("full-validation") == 1
    assert not saved.paths.completion.exists()


def test_saved_analysis_refuses_identity_publish_stage_appearing_after_hashes(saved, monkeypatch):
    original = live._completion_output_hashes
    count = 0
    stage = live._exclusive_publish_temp_path(live._identity_complete_path(saved.prepared.identity))
    def hashes(prepared):
        nonlocal count
        count += 1
        value = original(prepared)
        if count == 2:
            stage.write_bytes(b"concurrent publish")
        return value
    monkeypatch.setattr(live, "_completion_output_hashes", hashes)
    with pytest.raises(live.Phase3MainLiveError, match="partial publish"):
        live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert count == 2
    assert not saved.paths.completion.exists()
    assert stage.read_bytes() == b"concurrent publish"


def test_saved_analysis_rejects_symlink(saved, tmp_path):
    target = tmp_path / "saved-analysis-copy.json"
    target.write_bytes(saved.paths.analysis_results.read_bytes())
    saved.paths.analysis_results.unlink()
    try:
        saved.paths.analysis_results.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(live.Phase3MainLiveError, match="redirected"):
        live._finalize_main(saved.prepared, saved.terminal, **saved.kwargs)
    assert target.read_bytes() == saved.paths.analysis_results.read_bytes()
