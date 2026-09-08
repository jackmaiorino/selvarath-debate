"""Recovery integration across the run lease, accounting journal, and driver state."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from rejudge import api_client, phase3_main_live as live
from rejudge.phase2_canary_order import CellResultStore
from rejudge.request_journal import JournalingClient, RequestJournal
from test_phase3_main_live import (
    _prepared, _seed_price_signal_identity, _with_current_boundary_files, inventory,
)


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _stopped(prepared):
    prepared = _with_current_boundary_files(prepared)
    _seed_price_signal_identity(prepared)
    paths = prepared.identity.paths
    CellResultStore(paths.results).record("completed-original", {"cell_key": "completed-original"})
    paths.decisions.write_bytes(b"")
    paths.review_packets_root.mkdir()
    logs, indexes = [], []
    for wave in range(1, 4):
        row = {"wave": wave, "dispatches_this_wave": 60, "cumulative_reviewer_dispatches": wave * 60}
        logs.extend([{"event": "formal_main_pass_complete", "pass_index": wave},
                     {**row, "event": "reviewer_usage_reserved"},
                     {**row, "event": "reviewer_usage_wave_completed"}])
        indexes.append({"wave": wave, "payload_count": 60, "run_id": prepared.identity.run_id,
                        "manifest_canonical_sha256": prepared.identity.manifest_sha256})
    _write_rows(paths.run_log, logs)
    _write_rows(paths.reviewer_index, indexes)
    models = list(prepared.price_snapshot["models"])
    amendment = {"execution_source_commit": "d" * 40, "provider_worker_concurrency": 8,
                 "per_model_limits": dict.fromkeys(models, 4),
                 "initial_per_model_limits": {models[0]: 4, models[1]: 2}, "block_size": 8,
                 "recovery_manifest_sha256": "e" * 64, "recovery_raw_sha256": "f" * 64,
                 "recovery_signature_raw_sha256": "a" * 64, "interrupted_dispatches": []}
    return replace(prepared, recovery_validation=amendment,
                   recovery_path=prepared.manifest_path.with_name("recovery.json"))


def _interrupt_one_request(prepared):
    paths = prepared.identity.paths
    snapshot = api_client.load_chained_usage_ledger(paths.usage_ledger)
    def interrupted(**kwargs):
        raise KeyboardInterrupt("simulated host exit with request outstanding")
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=interrupted)))
    raw = api_client.RejudgeClient(approved_cap_usd=5, max_retries=0, _sdk_client=sdk,
                                  usage_log_path=paths.usage_ledger, _ledger_snapshot=snapshot)
    journal = RequestJournal(paths.request_journal,
                             execution_identity=prepared.identity.journal_execution_identity)
    client = JournalingClient(raw, journal)
    model = next(iter(prepared.price_snapshot["models"]))
    with pytest.raises(KeyboardInterrupt):
        client.complete([{"role": "user", "content": "fixed original request"}], model,
                        0.0, 123, 256, kind="query", request_metadata={
                            "cell_key": "unfinished-original", "call_role": "judge_query", "query_index": 0})
    marker_path, = journal.dispatch_marker_paths()
    marker_raw = marker_path.read_bytes()
    marker = json.loads(marker_raw)
    reservation = api_client._read_usage_events(paths.usage_ledger)[-1]
    prepared.recovery_validation["interrupted_dispatches"] = [{
        "marker_path": str(marker_path), "marker_raw_sha256": hashlib.sha256(marker_raw).hexdigest(),
        "marker": marker, "reservation": reservation, "reservation_attempt_id": reservation["attempt_id"],
        "request_sha256": marker["request_sha256"]}]
    return sdk, marker_path, reservation


def test_run_resume_preserves_original_work_reconciles_once_and_restores_used_limits(
    tmp_path, inventory, monkeypatch,
):
    prepared = _stopped(_prepared(tmp_path, inventory))
    sdk, marker, reservation = _interrupt_one_request(prepared)
    paths = prepared.identity.paths
    preserved_paths = [prepared.manifest_path, prepared.authorization_path, paths.results,
                       paths.decisions, paths.reviewer_index, paths.identity_binding,
                       live._identity_start_path(prepared.identity)]
    original = {path: path.read_bytes() for path in preserved_paths}
    usage_prefix = paths.usage_ledger.read_bytes()
    observed = []
    monkeypatch.setattr(live, "load_prepared_main", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(live, "_validate_launch_freshness", lambda candidate: None)
    monkeypatch.setattr(live, "_revalidate_authenticated_authorization", lambda candidate: prepared.authorization)
    monkeypatch.setattr(live, "_revalidate_recovery", lambda candidate, **kwargs: prepared.recovery_validation)
    monkeypatch.setattr(live, "_construct_verified_runtime_provider_sdk", lambda candidate: sdk)
    verified_commits = []
    monkeypatch.setattr(live, "_verify_clean_git_identity",
                        lambda manifest, root: verified_commits.append(manifest["source_commit"]))
    def forbidden(*args, **kwargs):
        raise AssertionError("resume must never preseed completed work")
    monkeypatch.setattr(live.phase3_main_runner, "_assert_fresh_identity", forbidden)
    import scripts.phase3_preseed_transcripts as preseed
    monkeypatch.setattr(preseed, "preseed_main", forbidden)
    def drive(candidate, client, *, held_run_lease, resume_state):
        live.phase3_main_reviewer_commit.require_held_run_lease(
            held_run_lease, expected_path=paths.lease)
        assert resume_state.first_pass_index == 4
        assert resume_state.reviewer_dispatches == 180
        assert resume_state.existing_unknown_charge_count == 1
        assert client.max_concurrent_requests == 8
        assert client.model_caps == prepared.recovery_validation["per_model_limits"]
        accounted = client.inner.inner._inner
        summary = api_client.load_chained_usage_ledger(paths.usage_ledger).summary
        assert accounted.spent_usd == pytest.approx(10.75 + summary["accounted_spend_usd"])
        observed.append(resume_state)
        return {"status": "driver_reached"}
    monkeypatch.setattr(live, "_drive_and_finalize", drive)
    for _ in range(2):
        assert live.run_main(prepared.manifest_path, prepared.authorization_path,
                             recovery_path=prepared.recovery_path) == {"status": "driver_reached"}
    assert len(observed) == 2
    assert verified_commits == ["d" * 40, "d" * 40]
    assert all(path.read_bytes() == raw for path, raw in original.items())
    assert paths.usage_ledger.read_bytes().startswith(usage_prefix)
    events = api_client._read_usage_events(paths.usage_ledger)
    recovered = [row for row in events if row.get("status") == "unknown_charge"]
    assert len(recovered) == 1
    assert recovered[0]["cost_usd"] == reservation["cost_usd"]
    assert not marker.exists()


def test_resume_refuses_missing_run_lease_before_reconciliation(tmp_path, inventory, monkeypatch):
    prepared = _stopped(_prepared(tmp_path, inventory))
    calls = []
    monkeypatch.setattr(live, "_revalidate_recovery", lambda *args, **kwargs: calls.append("recovery"))
    with pytest.raises(live.phase3_main_reviewer_commit.ReviewerWaveCommitError, match="RunLease"):
        live._resume_main(prepared, object(), held_run_lease=None)
    assert calls == []


def test_resumed_driver_uses_signed_parallel_limits_and_continues_review_accounting(
    tmp_path, inventory, monkeypatch,
):
    prepared = _stopped(_prepared(tmp_path, inventory))
    paths = prepared.identity.paths
    state = live.phase3_main_recovery_driver.restore_driver_state(
        paths, expected_run_id=prepared.identity.run_id,
        expected_manifest_sha256=prepared.identity.manifest_sha256)
    captured = {}
    monkeypatch.setattr(live.phase3_runner, "resolve_main_cells", lambda *args, **kwargs: [])
    monkeypatch.setattr(live, "_load_bound_input_object", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "_reviewer_loop_contract", lambda plan: (60, 10))
    def canary(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(halted_reason=None, completed=1, attempted=1, abandoned=0,
                               pending_payloads=["new-payload"])
    monkeypatch.setattr(live, "run_canary", canary)
    class ReviewReached(Exception):
        pass
    def review(candidate, payloads, *, wave, held_run_lease):
        assert wave == 4
        assert payloads == ["new-payload"]
        raise ReviewReached
    monkeypatch.setattr(live, "_review_wave_same_process", review)
    with live.phase3_v3_live.RunLease(paths.lease) as lease:
        with pytest.raises(ReviewReached):
            live._drive_and_finalize(prepared, object(), held_run_lease=lease, resume_state=state)
    assert captured["max_workers"] == captured["block_size"] == 8
    assert captured["model_caps"] == prepared.recovery_validation["initial_per_model_limits"]
    assert captured["transcript_generation_forbidden"] is True
    assert captured["protocol"] == prepared.protocol
    assert captured["bundle"] == prepared.prompt_bundle
    rows = [json.loads(line) for line in paths.run_log.read_text().splitlines()]
    reserved = [row for row in rows if row["event"] == "reviewer_usage_reserved"]
    assert reserved[-1]["cumulative_reviewer_dispatches"] == 181


def test_validate_only_recovery_never_creates_a_provider_client(tmp_path, inventory, monkeypatch, capsys):
    prepared = _stopped(_prepared(tmp_path, inventory))
    seen = []
    def load(*args, **kwargs):
        seen.append(kwargs)
        return prepared
    def forbidden(*args, **kwargs):
        raise AssertionError("read-only recovery validation created a provider client")
    monkeypatch.setattr(live, "load_prepared_main", load)
    monkeypatch.setattr(live, "_construct_provider_client", forbidden)
    monkeypatch.setattr(live, "_construct_verified_runtime_provider_sdk", forbidden)
    assert live.main(["--manifest", str(prepared.manifest_path), "--authorization", str(prepared.authorization_path),
                      "--recovery", str(prepared.recovery_path), "--validate-only"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["execution_started"] is output["provider_client_created"] is False
    assert seen == [{"verify_git": True, "recovery_path": str(prepared.recovery_path)}]
