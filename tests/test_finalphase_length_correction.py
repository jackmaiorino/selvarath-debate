import hashlib
import json
import subprocess

import pytest

from scripts import finalphase_length_correction as correction
from finalphase.authoring import author_request, world_ids


def test_resume_reserves_unsent_request_but_refuses_unknown_provider_state(tmp_path, monkeypatch):
    monkeypatch.setattr(correction, "ROOT", tmp_path)
    (tmp_path / "bench/worlds").mkdir(parents=True)
    for w, a, h in world_ids(8):
        (tmp_path / "bench/worlds" / f"{w}.json").write_text(json.dumps({"world_text": "word " * 1600}))
    req = author_request(*world_ids(8)[0])
    call = {"custom_id": req.custom_id, "req_hash": hashlib.sha256(req.to_json().encode()).hexdigest(),
            "status": "pending", "attempts": 0, "batch_id": None, "response": None, "cost": 0}
    monkeypatch.setattr(correction, "ledger", lambda: ([call], []))
    monkeypatch.setattr(correction, "accounting", lambda *args: {
        "authoring_spend_usd": 13.743585, "estimated_credit_remaining_usd": {"anthropic": 8.107785}})
    result = correction.reserve({})
    assert len(result["requests"]) == 4 and result["maximum_cumulative_authoring_usd"] < 21.75
    call["status"] = "submitted"
    call["attempts"] = 1
    call["batch_id"] = "accepted-batch"
    with pytest.raises(RuntimeError, match="unsettled"):
        correction.reserve({})


def test_failed_probe_never_dispatches_remaining_worlds(tmp_path, monkeypatch):
    manifest = tmp_path / "run_manifest.json"
    manifest.write_text(json.dumps({"authoring_length_correction": {"prompt_revision": correction.REVISION}}))
    monkeypatch.setattr(correction, "MANIFEST", manifest)
    monkeypatch.setattr(correction, "reserve", lambda *args: {})
    monkeypatch.setattr(correction.preflight, "require", lambda *args: None)
    reports, commands = [], []
    monkeypatch.setattr(correction, "report", lambda: reports.append(True))
    def fail(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(correction.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        correction.execute()
    assert len(commands) == 1 and commands[0][commands[0].index("--only") + 1] == "W001"
    assert reports == [True]
