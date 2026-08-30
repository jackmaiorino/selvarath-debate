"""Command boundary tests for local reviewer-wave closeout recovery."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import phase3_main_recover_reviewer_wave as recover_wave


def test_command_routes_independent_formal_paths_to_offline_recovery(
    tmp_path, monkeypatch, capsys,
):
    artifact_root = tmp_path.resolve()
    transaction_directory = (
        artifact_root / "main_review_packets" / "wave-001"
    ).resolve()
    transaction_directory.mkdir(parents=True)
    run_lease_path = (artifact_root / "main_run.lock").resolve()
    run_lease_path.write_bytes(b"0")
    captured = []
    transaction_id = "a" * 64

    def fake_recover(*, transaction_directory, artifact_root, run_lease_path):
        captured.append({
            "transaction_directory": Path(transaction_directory),
            "artifact_root": Path(artifact_root),
            "run_lease_path": Path(run_lease_path),
        })
        return SimpleNamespace(
            wave_commit_transaction_id=transaction_id,
            intent_path=Path(transaction_directory) / "WAVE_COMMIT_INTENT.json",
            receipt_path=Path(transaction_directory) / "WAVE_COMMIT_RECEIPT.json",
            commit_counts={"parsed": 2, "malformed": 1, "reviewer_error": 0},
        )

    monkeypatch.setattr(
        recover_wave.phase3_main_reviewer_commit,
        "recover_reviewer_wave_commit_from_intent",
        fake_recover,
    )
    assert recover_wave.main([
        "--transaction-directory", str(transaction_directory),
        "--artifact-root", str(artifact_root),
        "--run-lease-path", str(run_lease_path),
    ]) == 0
    assert captured == [{
        "transaction_directory": transaction_directory,
        "artifact_root": artifact_root,
        "run_lease_path": run_lease_path,
    }]
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "committed"
    assert output["wave_commit_transaction_id"] == transaction_id
    assert output["commit_counts"] == {
        "parsed": 2, "malformed": 1, "reviewer_error": 0,
    }


@pytest.mark.parametrize("missing", ["artifact_root", "run_lease_path"])
def test_command_requires_both_independent_formal_path_arguments(
    tmp_path, missing,
):
    arguments = [
        "--transaction-directory", str((tmp_path / "wave-001").resolve()),
    ]
    if missing != "artifact_root":
        arguments.extend(["--artifact-root", str(tmp_path.resolve())])
    if missing != "run_lease_path":
        arguments.extend([
            "--run-lease-path", str((tmp_path / "main_run.lock").resolve()),
        ])
    with pytest.raises(SystemExit) as raised:
        recover_wave.main(arguments)
    assert raised.value.code == 2


def test_command_has_no_external_execution_surface():
    source = inspect.getsource(recover_wave)
    for forbidden in (
        "import subprocess", "import socket", "import requests", "reviewer_call", "dispatch(",
    ):
        assert forbidden not in source
