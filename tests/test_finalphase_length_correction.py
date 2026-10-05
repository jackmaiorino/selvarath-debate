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


def test_replay_includes_committed_wal_responses_without_provider_dispatch(tmp_path, monkeypatch):
    from finalphase.providers import Request, Response
    from finalphase.store import Store

    monkeypatch.setattr(correction, "ROOT", tmp_path)
    writer = Store(tmp_path / "author.db", "author", 21.75)
    try:
        request = Request("author:W005:fable:t64000:length-v2", "fable", "system", (("user", "prompt"),), 64000)
        writer._register(request)
        writer._save(Response(request.custom_id, "claude-fable-5-1", "truncated", '{"world_text":', cost=1.610965))
        assert (tmp_path / "author.db-wal").exists()
        calls, _ = correction.ledger()
        result = correction.replay(calls)
        assert result["cached_responses"] == 1 and result["provider_dispatches"] == 0
        assert result["bit_identical"] is True
        cached = writer.get(request.custom_id)
        assert cached is not None and cached.status == "truncated"
    finally:
        writer.db.close()


def test_terminal_execute_error_does_not_leave_running_manifest(tmp_path, monkeypatch):
    manifest = tmp_path / "run_manifest.json"
    manifest.write_text(json.dumps({"authoring_length_correction": {"status": "running"}, "execution_status": "running"}))
    monkeypatch.setattr(correction, "MANIFEST", manifest)
    monkeypatch.setattr(correction.sys, "argv", ["correction", "execute"])
    def fail():
        raise RuntimeError("failed replay snapshot")
    monkeypatch.setattr(correction, "execute", fail)
    with pytest.raises(RuntimeError, match="failed replay snapshot"):
        correction.main()
    saved = json.loads(manifest.read_text())
    assert saved["execution_status"] == "authoring_length_correction_needs_attention"
    assert saved["authoring_length_correction"]["execution_error"]["message"] == "failed replay snapshot"
