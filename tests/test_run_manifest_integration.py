"""CLI-level coverage for fail-closed run manifests and safe resume."""
from __future__ import annotations

import json
from pathlib import Path

from rejudge import debate_gen
from rejudge import run_manifest
from rejudge.run_manifest import manifest_path_for


def _question_selection(tmp_path: Path) -> Path:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(["SEL-002"]), encoding="utf-8")
    return path


def _dry_args(question_path: Path, output_path: Path) -> list[str]:
    return [
        "--questions", str(question_path),
        "--transcripts-per-question", "1",
        "--protocols", "uncapped3",
        "--dry-run",
        "--workers", "1",
        "--out", str(output_path),
    ]


def test_cli_refuses_dry_output_as_live_resume(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        run_manifest, "_git_code_state",
        lambda repo_root, excluded_paths: (
            Path(repo_root).resolve(), {"sha": "a" * 40, "dirty": False}))
    questions = _question_selection(tmp_path)
    output = tmp_path / "transcripts.jsonl"
    dry_args = _dry_args(questions, output)

    assert debate_gen.main(dry_args) == 0
    before = output.read_bytes()

    live_args = [arg for arg in dry_args if arg != "--dry-run"]
    live_args.extend(["--approved-cap", "1"])
    assert debate_gen.main(live_args) == 2
    assert "REFUSED: required usage ledger is missing" in capsys.readouterr().err
    assert output.read_bytes() == before
    manifest = json.loads(manifest_path_for(output).read_text(encoding="utf-8"))
    assert manifest["identity"]["mode"] == "dry-run"


def test_cli_refuses_existing_output_without_manifest(tmp_path, capsys):
    questions = _question_selection(tmp_path)
    output = tmp_path / "transcripts.jsonl"
    original = b'{"cell_key":"historical|unknown"}\n'
    output.write_bytes(original)

    assert debate_gen.main(_dry_args(questions, output)) == 2
    assert "retroactive adoption" in capsys.readouterr().err
    assert output.read_bytes() == original
    assert not manifest_path_for(output).exists()


def test_cli_resume_with_matching_identity_adds_no_rows(tmp_path):
    questions = _question_selection(tmp_path)
    output = tmp_path / "transcripts.jsonl"
    args = _dry_args(questions, output)

    assert debate_gen.main(args) == 0
    output_before = output.read_bytes()
    manifest_before = manifest_path_for(output).read_bytes()
    manifest = json.loads(manifest_before)
    identity = manifest["identity"]
    assert identity["output"] == output.resolve().as_posix()
    assert identity["prices"]["mode"] == "strict_per_model"
    assert identity["prices"]["strict_model_pricing"] is True
    assert "price_schedule" in identity["source_files"]
    assert identity["cli_params"]["usage_log"].endswith(
        "transcripts.jsonl.usage.jsonl")
    assert not output.with_name("transcripts.jsonl.usage.jsonl").exists()

    assert debate_gen.main(args) == 0
    assert output.read_bytes() == output_before
    assert manifest_path_for(output).read_bytes() == manifest_before
