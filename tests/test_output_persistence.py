import json
import threading
from io import StringIO
from pathlib import Path

import pytest

from rejudge import batch_replay, calibrate, debate_gen, runner


L70 = "meta-llama/Llama-3.3-70B-Instruct-Turbo"


def _output_failure(*args, **kwargs):
    raise runner.OutputPersistenceError("simulated durable append failure")


def test_preflight_repairs_boundary_and_append_fsyncs(tmp_path, monkeypatch):
    output = tmp_path / "records.jsonl"
    prefix = b'{"cell_key":"old"}\n{"cell_key":"truncated"'
    output.write_bytes(prefix)
    fsync_calls = []
    monkeypatch.setattr(runner.os, "fsync", lambda descriptor: fsync_calls.append(descriptor))

    runner.prepare_jsonl_output(output)
    runner.append_jsonl_record(output, {"cell_key": "new", "value": 1})

    assert output.read_bytes() == prefix + b'\n{"cell_key": "new", "value": 1}\n'
    assert len(fsync_calls) >= 3  # repaired boundary, preflight, charged-result append


@pytest.mark.parametrize(
    "payload, expected_fragment",
    [
        (b'{"cell_key":"expected"}\n{"cell_key":', "malformed JSON"),
        (b'{"cell_key":"expected"}\n{"cell_key":"expected"}\n', "duplicates"),
        (b'{"cell_key":"other"}\n', "unexpected cell_key"),
        (b'[]\n', "not a JSON object"),
        (b'{"cell_key":"expected","value":NaN}\n', "non-finite JSON number"),
    ],
)
def test_strict_completion_audit_rejects_unsafe_rows(
        tmp_path, payload, expected_fragment):
    output = tmp_path / "records.jsonl"
    output.write_bytes(payload)
    runner.prepare_jsonl_output(output)

    with pytest.raises(runner.OutputPersistenceError, match=expected_fragment):
        runner.audit_jsonl_completion(output, {"expected"})


def test_source_snapshot_refuses_parse_time_mutation(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"value": 1}', encoding="utf-8")
    source_files = {"source": source}
    before = runner.capture_source_hashes(source_files)
    source.write_text('{"value": 2}', encoding="utf-8")

    with pytest.raises(runner.RunManifestError, match="changed while runtime inputs"):
        runner.require_unchanged_source_snapshot(before, source_files)


def test_manifest_snapshot_must_match_loaded_bytes(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"value": 1}', encoding="utf-8")
    loaded = runner.capture_source_hashes({"source": source})
    manifest = {"identity": {"source_files": {
        "source": {"sha256": "0" * 64},
    }}}

    with pytest.raises(runner.RunManifestError, match="parsed runtime snapshot"):
        runner.require_manifest_source_snapshot(manifest, loaded)


def _patch_runner_grid(monkeypatch):
    transcript = {"question_id": "Q1", "transcript_index": 0, "world": "w"}
    monkeypatch.setattr(runner, "_load_jsonl", lambda path: [transcript])
    monkeypatch.setattr(runner, "_world_documents", lambda *args: {"w": "world"})
    monkeypatch.setattr(runner, "capture_source_hashes", lambda files: {})
    monkeypatch.setattr(
        runner, "require_unchanged_source_snapshot", lambda before, files: {})
    monkeypatch.setattr(
        runner, "ensure_run_manifest",
        lambda *args, **kwargs: {"identity": {"source_files": {}}})


def test_runner_output_failure_stops_queued_cells_and_returns_unsafe(
        tmp_path, monkeypatch, capsys):
    _patch_runner_grid(monkeypatch)
    calls = []

    def successful_cell(tr, world, arm, budget, replicate, client, protocol):
        calls.append(budget)
        return {"cell_key": f"{arm.name}|Q1|0|{budget}|{replicate}"}

    monkeypatch.setattr(runner.judge_loop, "run_judgment", successful_cell)
    monkeypatch.setattr(runner, "append_jsonl_record", _output_failure)

    rc = runner.main([
        "--arms", "clean", "--replicates", "1", "--limit", "1",
        "--dry-run", "--workers", "1", "--out", str(tmp_path / "out.jsonl"),
    ])

    assert rc == runner.OUTPUT_PERSISTENCE_EXIT
    assert calls == [0]
    assert "OUTPUT UNSAFE" in capsys.readouterr().err


def test_accounting_failure_has_priority_over_simultaneous_output_failure(
        tmp_path, monkeypatch, capsys):
    _patch_runner_grid(monkeypatch)
    rendezvous = threading.Barrier(2)

    def two_failures(tr, world, arm, budget, replicate, client, protocol):
        if budget not in {0, 1}:
            raise AssertionError("queued work should stop after unsafe failures")
        rendezvous.wait(timeout=5)
        if budget == 0:
            raise runner.AccountingInvariantError("reservation overrun")
        return {"cell_key": f"{arm.name}|Q1|0|{budget}|{replicate}"}

    monkeypatch.setattr(runner.judge_loop, "run_judgment", two_failures)
    monkeypatch.setattr(runner, "append_jsonl_record", _output_failure)

    rc = runner.main([
        "--arms", "clean", "--replicates", "1", "--limit", "1",
        "--dry-run", "--workers", "2", "--out", str(tmp_path / "out.jsonl"),
    ])

    assert rc == 4
    stderr = capsys.readouterr().err
    assert "ACCOUNTING UNSAFE" in stderr
    assert "OUTPUT UNSAFE" in stderr


def test_debate_generation_output_failure_returns_unsafe(tmp_path, monkeypatch):
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps(["SEL-002"]), encoding="utf-8")
    calls = []

    def generated(question, world, transcript_index, honest_first, protocol, client, *,
                  debater_model, protocol_name):
        calls.append(question["id"])
        return {"cell_key": f"{protocol_name}|{debater_model}|{question['id']}|0"}

    monkeypatch.setattr(
        debate_gen, "ensure_run_manifest",
        lambda *args, **kwargs: {"identity": {"source_files": {}}})
    monkeypatch.setattr(debate_gen, "require_manifest_source_snapshot", lambda *args: None)
    monkeypatch.setattr(debate_gen, "generate_transcript", generated)
    monkeypatch.setattr(debate_gen, "append_jsonl_record", _output_failure)

    rc = debate_gen.main([
        "--questions", str(questions), "--transcripts-per-question", "1",
        "--protocols", "uncapped3", "--dry-run", "--workers", "1",
        "--out", str(tmp_path / "debates.jsonl"),
    ])

    assert rc == runner.OUTPUT_PERSISTENCE_EXIT
    assert calls == ["SEL-002"]


def test_calibration_output_failure_returns_unsafe(tmp_path, monkeypatch):
    transcript_path = tmp_path / "transcripts.jsonl"
    transcript_path.write_text(json.dumps({
        "question_id": "SEL-002", "transcript_index": 0, "world": "selvarath",
        "protocol": "capped3", "debater_model": L70,
    }) + "\n", encoding="utf-8")
    models_path = tmp_path / "models.json"
    models_path.write_text(json.dumps({
        "provider": "test", "price_verified_at": "2026-07-15",
        "price_source_url": "https://example.test/prices",
        "judges": {"anchor": L70}, "debaters": [L70], "oracle": L70,
        "prices_per_mtok": {L70: {"in": 1.04, "out": 1.04}},
    }), encoding="utf-8")
    calls = []

    def judged(cell, client, protocol, world):
        calls.append(cell["cell_key"])
        return {"cell_key": cell["cell_key"]}

    monkeypatch.setattr(calibrate, "ensure_run_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(calibrate, "require_manifest_source_snapshot", lambda *args: None)
    monkeypatch.setattr(calibrate, "acquire_output_locks", lambda *args, **kwargs: None)
    monkeypatch.setattr(calibrate, "judge_cell", judged)
    monkeypatch.setattr(calibrate, "append_jsonl_record", _output_failure)

    rc = calibrate.main([
        "--dry-run", "--workers", "1", "--cells", "b0", "--judges", "anchor",
        "--transcripts", str(transcript_path), "--models", str(models_path),
        "--out-dir", str(tmp_path / "calibration"),
    ])

    assert rc == runner.OUTPUT_PERSISTENCE_EXIT
    assert len(calls) == 1


def test_batch_replay_output_failure_returns_unsafe(tmp_path, monkeypatch):
    transcript = {"question_id": "Q1", "transcript_index": 0}
    source = {
        "question_id": "Q1", "transcript_index": 0, "arm": "clean", "budget": 2,
        "replicate": 0, "cell_key": "clean|Q1|0|2|0",
    }
    real_open = open

    def fixture_open(path, *args, **kwargs):
        normalized = Path(path).as_posix()
        if normalized.endswith("data/transcripts.jsonl"):
            return StringIO(json.dumps(transcript) + "\n")
        if normalized.endswith("rejudge/output/records.jsonl"):
            return StringIO(json.dumps(source) + "\n")
        return real_open(path, *args, **kwargs)

    calls = []

    def replayed(source_record, transcript_record, arm, protocol, client):
        calls.append(arm.name)
        return {"cell_key": f"{arm.name}|Q1|0|2|0"}

    monkeypatch.setattr(batch_replay, "open", fixture_open, raising=False)
    monkeypatch.setattr(
        batch_replay, "ensure_run_manifest",
        lambda *args, **kwargs: {"identity": {"source_files": {}}})
    monkeypatch.setattr(batch_replay, "require_manifest_source_snapshot", lambda *args: None)
    monkeypatch.setattr(batch_replay, "capture_source_hashes", lambda files: {})
    monkeypatch.setattr(
        batch_replay, "require_unchanged_source_snapshot", lambda before, files: {})
    monkeypatch.setattr(batch_replay, "replay_one", replayed)
    monkeypatch.setattr(batch_replay, "append_jsonl_record", _output_failure)

    rc = batch_replay.main([
        "--dry-run", "--workers", "1", "--limit", "1",
        "--out", str(tmp_path / "batch.jsonl"),
    ])

    assert rc == runner.OUTPUT_PERSISTENCE_EXIT
    assert calls == ["batch"]
