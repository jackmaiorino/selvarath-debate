"""No-output preflight crashes preserve their allocation and reconstruct only saved calls."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rejudge import phase2_canary_runner as runner, phase3_main_live as live
from rejudge import phase3_main_missing_reviewer_wave as recovery
from rejudge.phase2_call_cache import CallKey, request_fingerprint
from rejudge.phase2_canary_cells import ResolvedCell
from rejudge.phase2_canary_gate import PendingReviewerDecision
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase3_main_runner import MainRunIdentity
from rejudge.phase3_v3_live import RunLease
from rejudge.request_journal import RequestJournal


def _write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _request(i):
    return {"messages": [{"role": "user", "content": f"fixed-{i}"}], "model": "model",
            "temperature": 0, "seed": i, "max_tokens": 32}


def _fixture(tmp_path, monkeypatch, *, missing=False, changed_result=False):
    root = tmp_path / "archive"
    root.mkdir()
    identity = MainRunIdentity("run", "a" * 64, root)
    paths = identity.paths
    paths.review_packets_root.mkdir()
    for key in ("decisions", "reviewer_index", "usage_ledger", "terminal_dispositions"):
        getattr(paths, key).write_bytes(b"")
    paths.reviewer_worklist.write_bytes(b"stale previous wave, never an input")
    cells = [ResolvedCell(cell_key=f"cell-{i:03d}", kind="no_debate_judgment",
        condition="condition", question_id="question", judge_model="model", debater_model=None,
        transcript_index=0, replicate_index=0, query_budget=1, dependency_keys=(), composition={},
        transcript_protocol_name=None, oracle_mode="clean", arm_name="clean") for i in range(76)]
    journal = RequestJournal(paths.request_journal, execution_identity=identity.journal_execution_identity)
    results = CellResultStore(paths.results)
    results.record("baseline", {"unchanged": True})
    for i, cell in enumerate(cells):
        if not (missing and i == 75):
            journal.put(CallKey(cell.cell_key, "judge_query", 0, 1),
                        request_fingerprint(**_request(i)), f"saved-{i}")
        if i < 16:
            results.record(cell.cell_key, {"saved_value": "changed" if changed_result and i == 0 else f"saved-{i}",
                "created_at": f"original-time-{i}", "harness_version": "original-commit"})
    _write(paths.run_log, [
        {"event": "formal_main_pass_started", "pass_index": 286,
         "provider_worker_concurrency": 8, "provider_model_limits": {"model": 4}},
        {"event": "formal_main_pass_complete", "pass_index": 286, "completed_this_pass": 16,
         "attempted_this_pass": 76, "abandoned_this_pass": 0, "retry_deferred_this_pass": 0,
         "pending_payloads": 60, "rows_complete": 17},
        {"event": "reviewer_usage_reserved", "wave": 286, "dispatches_this_wave": 60,
         "cumulative_reviewer_dispatches": 60}])
    context = recovery.OfflinePreparationContext(identity=identity, project_root=tmp_path / "source",
        inventory=SimpleNamespace(cells=cells), protocol={"cell_key_namespace": "run"},
        prompt_bundle={}, role_limits={}, reviewer_prompt={"prompt": "frozen review"},
        context_excluded_cell_keys=(), prior_recovery={"block_size": 16}, proposed_code_bindings=[])
    monkeypatch.setattr(live.phase3_runner, "resolve_main_cells", lambda cells, **kwargs: cells)
    def execute(cell, ctx, **kwargs):
        i = int(cell.cell_key.split("-")[-1])
        value = ctx.client.complete(**_request(i), request_metadata={
            "cell_key": cell.cell_key, "call_role": "judge_query", "slot": 0, "attempt": 1})
        if i < 16:
            return {"saved_value": value, "created_at": f"replay-time-{i}", "harness_version": "new-commit"}
        payload = {"payload_sha256": hashlib.sha256(value.encode()).hexdigest(),
                   "query": value, "candidate_a": "first", "candidate_b": "second"}
        raise PendingReviewerDecision(payload["payload_sha256"], payload)
    monkeypatch.setattr(runner, "execute_cell", execute)
    return context


def test_replays_same_16_results_and_60_pending_with_no_original_writes(tmp_path, monkeypatch):
    context = _fixture(tmp_path, monkeypatch)
    paths = context.identity.paths
    originals = {path: path.read_bytes() for path in paths.root.iterdir() if path.is_file()}
    with RunLease(paths.lease) as lease:
        first = recovery.replay_missing_reviewer_wave(context, wave=286,
            scratch_root=tmp_path / "replay-one", held_run_lease=lease)
        second = recovery.replay_missing_reviewer_wave(context, wave=286,
            scratch_root=tmp_path / "replay-two", held_run_lease=lease)
    assert first.payloads == second.payloads
    reloaded = recovery.load_reconstruction(first.receipt_path)
    assert reloaded.payloads == first.payloads
    assert reloaded.receipt == first.receipt
    assert len(first.payloads) == 60
    assert first.receipt["journal_requests_replayed"] == 76
    assert len(first.receipt["completed_result_comparison"]) == 16
    assert first.receipt["completed_result_comparison"][0]["operational_metadata"] == {
        "created_at": {"original": "original-time-0", "replay": "replay-time-0"},
        "harness_version": {"original": "original-commit", "replay": "new-commit"}}
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert not tuple(paths.review_packets_root.iterdir())
    assert first.receipt["new_reviewer_allocations"] == 0
    Path(first.receipt["worklist"]["path"]).write_bytes(b"changed")
    with pytest.raises(recovery.MissingWaveError, match="worklist changed"):
        recovery.load_reconstruction(first.receipt_path)


@pytest.mark.parametrize("case", ["missing", "scientific_result", "duplicate_allocation", "partial_packet", "dirty_pass"])
def test_refuses_unproven_reconstruction(tmp_path, monkeypatch, case):
    context = _fixture(tmp_path, monkeypatch, missing=case == "missing", changed_result=case == "scientific_result")
    paths = context.identity.paths
    if case == "duplicate_allocation":
        _, rows = recovery._rows(paths.run_log)
        _write(paths.run_log, rows + [rows[-1]])
    elif case == "partial_packet":
        (paths.review_packets_root / "wave-286-existing").mkdir()
    elif case == "dirty_pass":
        _, rows = recovery._rows(paths.run_log)
        rows[1]["retry_deferred_this_pass"] = 1
        _write(paths.run_log, rows)
    before = paths.run_log.read_bytes()
    with RunLease(paths.lease) as lease, pytest.raises(recovery.MissingWaveError):
        recovery.replay_missing_reviewer_wave(context, wave=286,
            scratch_root=tmp_path / "replay", held_run_lease=lease)
    assert paths.run_log.read_bytes() == before


@pytest.mark.parametrize("response", ["", "saved"])
def test_journal_only_client_empty_is_saved_and_mismatch_latches(tmp_path, response):
    journal = RequestJournal(tmp_path / "journal.jsonl")
    key = CallKey("cell", "judge_query", 0, 1)
    journal.put(key, request_fingerprint(**_request(1)), response)
    original = journal.path.read_bytes()
    client = recovery.JournalOnlyClient(journal)
    metadata = {"cell_key": "cell", "call_role": "judge_query", "slot": 0, "attempt": 1}
    assert client.complete(**_request(1), request_metadata=metadata) == response
    with pytest.raises(recovery.JournalOnlyReplayError):
        client.complete(**_request(2), request_metadata=metadata)
    with pytest.raises(recovery.JournalOnlyReplayError):
        client.complete(**_request(1), request_metadata=metadata)
    assert journal.path.read_bytes() == original
    assert not journal.dispatch_marker_paths()


def test_prepare_seam_has_no_execution_or_second_allocation(tmp_path, monkeypatch):
    context = _fixture(tmp_path, monkeypatch)
    paths = context.identity.paths
    context.manifest = {"runtime": {}}
    context.authorization = {"signed": "original"}
    context.authorization_path = tmp_path / "auth.json"
    context.input_paths = {key: tmp_path / key for key in ("capacity_plan", "capacity_result",
        "capacity_dispatch_history", "capacity_execution_manifest", "capacity_execution_authorization",
        "capacity_execution_authorization_signature")}
    monkeypatch.setattr(live, "_load_authenticated_owner_authorization", lambda path: context.authorization)
    monkeypatch.setattr(live.phase3_main_manifest, "validate_main_authorization", lambda *a, **k: None)
    monkeypatch.setattr(live, "_load_bound_input_object", lambda *a, **k: {"reviewer_configuration": {}})
    monkeypatch.setattr(live, "_reopen_capacity_execution_provenance_inputs", lambda *a: None)
    monkeypatch.setattr(live, "_validate_capacity", lambda **kwargs: {})
    monkeypatch.setattr(live, "_validate_capacity_runtime_binding", lambda **kwargs: {})
    monkeypatch.setattr(live, "_build_reviewer_dispatch_guard", lambda *a, **kwargs: {
        "packet_bindings": kwargs["packet_bindings"], "offline_fixture": True})
    def forbidden(*args, **kwargs):
        raise AssertionError("offline preparation must not dispatch or allocate")
    monkeypatch.setattr(live, "_execute_and_commit_reviewer_wave", forbidden)
    monkeypatch.setattr(live, "_construct_provider_client", forbidden)
    monkeypatch.setattr(live, "_admit_reviewer_wave_quantity", forbidden)
    before = paths.run_log.read_bytes()
    with RunLease(paths.lease) as lease:
        replay = recovery.replay_missing_reviewer_wave(context, wave=286,
            scratch_root=tmp_path / "replay", held_run_lease=lease)
        packet_dir = recovery.prepare_missing_reviewer_wave(context, replay, held_run_lease=lease)
        with pytest.raises(recovery.MissingWaveError, match="packet evidence"):
            recovery.prepare_missing_reviewer_wave(context, replay, held_run_lease=lease)
    assert paths.run_log.read_bytes() == before
    assert len(list(packet_dir.glob("*.txt"))) == 60
    assert (packet_dir / "rulings.jsonl").read_bytes() == b""
    assert not (packet_dir / ".reviewer_dispatch_reservations").exists()
    assert (packet_dir / "WORKLIST.json").read_bytes() == (tmp_path / "replay" / "WORKLIST.json").read_bytes()


def test_preflight_failure_precedes_packet_writer_and_execution(tmp_path, monkeypatch):
    context = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(live, "_revalidate_authenticated_authorization", lambda value: {})
    def fail(value):
        raise live.Phase3MainLiveError("local version timeout")
    monkeypatch.setattr(live, "_revalidate_capacity_snapshot", fail)
    before = context.identity.paths.reviewer_worklist.read_bytes()
    with RunLease(context.identity.paths.lease) as lease, pytest.raises(live.Phase3MainLiveError, match="timeout"):
        live._review_wave_same_process(context, [], wave=286, held_run_lease=lease)
    assert context.identity.paths.reviewer_worklist.read_bytes() == before
    assert not tuple(context.identity.paths.review_packets_root.iterdir())
