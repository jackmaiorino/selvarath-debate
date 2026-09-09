"""A proved quota refusal preserves all failed bytes and consumes a fresh bounded wave."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rejudge import phase3_main_live as live
from rejudge import phase3_main_reviewer_quota_recovery as quota
from rejudge import phase3_main_recovery_driver as driver
from rejudge.phase3_main_runner import MainRunPaths
from rejudge.phase3_v3_live import RunLease
from scripts import codex_reviewer_batch as batch
from test_reviewer_partial_batch_recovery import _partial_wave

QUOTA = ("You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage "
         "to purchase more credits or try again at Sep 14th, 2026 9:27 PM.")


def _write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value) + "\n", encoding="utf-8")


def _quota_fixture(tmp_path, monkeypatch, *, message=QUOTA, ruling=b"", exit_code=1, completed=False):
    f = _partial_wave(tmp_path, monkeypatch, count=60, retained_count=0)
    calls = []
    def refused(argv, **kwargs):
        calls.append(kwargs["input"])
        Path(argv[argv.index("-o") + 1]).write_bytes(ruling)
        events = [{"type": "thread.started", "thread_id": "fixture"}, {"type": "turn.started"},
                  {"type": "error", "message": message}, {"type": "turn.failed", "error": {"message": message}}]
        if completed:
            events = events[:2] + [{"type": "item.completed", "item": {"type": "agent_message", "text": "saved"}},
                                  {"type": "turn.completed"}]
        return SimpleNamespace(returncode=exit_code,
            stdout=b"".join((json.dumps(event) + "\n").encode() for event in events), stderr=b"")
    monkeypatch.setattr(batch.subprocess, "run", refused)
    for item in f["items"][:13]:
        batch.run_one(f["packets"] / item["file"], "reviewer-model", "high",
            f["argv"][f["argv"].index("--codex") + 1],
            batch._active_deadline(f["guard"]["authorization_valid_until_utc"]), 1,
            f["guard_path"], f["context"]["dispatch_guard"]["raw_sha256"], f["output_path"])
    paths = MainRunPaths.under(tmp_path / "run")
    paths.root.mkdir()
    paths.reviewer_index.write_bytes(b"")
    reservation = {"event": "reviewer_usage_reserved", "wave": 170,
                   "dispatches_this_wave": 60, "cumulative_reviewer_dispatches": 10080,
                   "maximum_reviewer_dispatches": 59040}
    _write(paths.run_log, reservation)
    manifest = {"run_id": f["guard"]["run_id"], "runtime": {"reviewer_model": "reviewer-model",
        "reviewer_reasoning_effort": "high", "reviewer_concurrency": 1}, "output_contract": {"paths": {
        "review_packets_root": f["packets"].parent.as_posix(), "reviewer_index": paths.reviewer_index.as_posix(),
        "run_log": paths.run_log.as_posix()}}}
    f.update(manifest=manifest, paths=paths, calls=calls)
    return f


def _build(f):
    return quota.build_quota_abandoned_wave(f["manifest"], directory=f["packets"], wave=170,
        replacement_wave=171, accepted_batch_runner_bindings=f["context"]["accepted_batch_runner_bindings"])


def test_quota_proof_preserves_13_failures_and_full_60_allocation(tmp_path, monkeypatch):
    f = _quota_fixture(tmp_path, monkeypatch)
    original = {path: path.read_bytes() for path in f["packets"].rglob("*") if path.is_file()}
    calls = len(f["calls"])
    proof = _build(f)
    assert proof["attempted_dispatches"] == 13
    assert proof["reserved_dispatches"] == 60
    assert proof["replacement_wave"] == 171
    quota.validate_quota_abandoned_wave(proof, f["manifest"],
        accepted_batch_runner_bindings=f["context"]["accepted_batch_runner_bindings"])
    assert len(f["calls"]) == calls
    assert all(path.read_bytes() == raw for path, raw in original.items())
    stderr = next((f["packets"] / "reviewer_evidence").glob("*/codex_stderr.bin"))
    stderr.write_bytes(b"changed retained metadata")
    with pytest.raises(ValueError):
        quota.validate_quota_abandoned_wave(proof, f["manifest"],
            accepted_batch_runner_bindings=f["context"]["accepted_batch_runner_bindings"])


@pytest.mark.parametrize("kwargs", [{"message": "unrelated network error"}, {"ruling": b"saved response"},
                                    {"exit_code": 0}, {"completed": True, "exit_code": 0, "ruling": b"saved"}])
def test_quota_proof_cannot_discard_nonquota_or_completed_outputs(tmp_path, monkeypatch, kwargs):
    f = _quota_fixture(tmp_path, monkeypatch, **kwargs)
    with pytest.raises(ValueError):
        _build(f)


@pytest.mark.parametrize("damage", ["rulings", "index", "intent", "extra_evidence", "cross_run", "allocation"])
def test_quota_proof_rejects_unbound_or_committed_work(tmp_path, monkeypatch, damage):
    f = _quota_fixture(tmp_path, monkeypatch)
    if damage == "rulings":
        f["output_path"].write_bytes(b'{"saved":true}\n')
    elif damage == "index":
        _write(f["paths"].reviewer_index, {"wave": 170})
    elif damage == "intent":
        _write(f["packets"] / "WAVE_COMMIT_INTENT.json", {})
    elif damage == "extra_evidence":
        _write(f["packets"] / "reviewer_evidence" / "unexpected", {})
    elif damage == "cross_run":
        f["manifest"]["run_id"] = "other-run"
    else:
        row = json.loads(f["paths"].run_log.read_bytes())
        row["dispatches_this_wave"] = 59
        _write(f["paths"].run_log, row)
    with pytest.raises(ValueError):
        _build(f)


def _driver_fixture(tmp_path):
    paths = MainRunPaths.under(tmp_path)
    first = {"event": "reviewer_usage_reserved", "wave": 169, "dispatches_this_wave": 10020,
             "cumulative_reviewer_dispatches": 10020}
    failed = {"event": "reviewer_usage_reserved", "wave": 170, "dispatches_this_wave": 60,
              "cumulative_reviewer_dispatches": 10080}
    paths.run_log.write_text("\n".join(json.dumps(row) for row in (
        first, dict(first, event="reviewer_usage_wave_completed"), failed)) + "\n", encoding="utf-8")
    _write(paths.reviewer_index, {"wave": 169, "payload_count": 10020, "run_id": "run",
                                "manifest_canonical_sha256": "a" * 64})
    proof = {"wave": 170, "replacement_wave": 171, "reserved_dispatches": 60,
             "attempted_dispatches": 13, "original_reservation": failed,
             "packet_directory": str(tmp_path / "failed"), "payload_sha256s": [str(i) for i in range(60)]}
    return paths, proof


def test_driver_keeps_failed_allocation_and_never_invents_completion(tmp_path):
    paths, proof = _driver_fixture(tmp_path)
    kwargs = dict(expected_run_id="run", expected_manifest_sha256="a" * 64, quota_abandoned_waves=[proof])
    state = driver.restore_driver_state(paths, **kwargs)
    assert state.reviewer_dispatches == 10080 and state.first_pass_index == 171
    assert state.recovered_reviewer_waves == ()
    assert state.quota_abandoned_waves == state.pending_quota_replacements == (170,)
    with paths.run_log.open("a") as handle:
        handle.write(json.dumps({"event": "reviewer_usage_wave_quota_abandoned", "wave": 170,
            "dispatches_this_wave": 60, "quota_proof_canonical_sha256": quota.canonical_sha256(proof)}) + "\n")
        for event in ("reviewer_usage_reserved", "reviewer_usage_wave_completed"):
            handle.write(json.dumps({"event": event, "wave": 171, "dispatches_this_wave": 60,
                                    "cumulative_reviewer_dispatches": 10140}) + "\n")
    with paths.reviewer_index.open("a") as handle:
        handle.write(json.dumps({"wave": 171, "payload_count": 60, "run_id": "run",
                                "manifest_canonical_sha256": "a" * 64}) + "\n")
    state = driver.restore_driver_state(paths, **kwargs)
    assert state.reviewer_dispatches == 10140 and state.first_pass_index == 172
    assert state.quota_abandoned_waves == state.pending_quota_replacements == ()
    # A crash after the replacement index committed but before its completion log
    # recovers that commit instead of scheduling another review.
    lines = paths.run_log.read_bytes().splitlines()
    paths.run_log.write_bytes(b"\n".join(lines[:-1]) + b"\n")
    state = driver.restore_driver_state(paths, **kwargs)
    assert state.recovered_reviewer_waves == (171,)
    assert state.pending_quota_replacements == () and state.reviewer_dispatches == 10140
    with pytest.raises(driver.RecoveryDriverError):
        driver.restore_driver_state(paths, expected_run_id="run", expected_manifest_sha256="a" * 64)


@pytest.mark.parametrize("at_cap", [False, True])
def test_live_replacement_uses_exact_60_prompts_and_adds_bounded_allocation(tmp_path, monkeypatch, at_cap):
    paths, proof = _driver_fixture(tmp_path)
    prompt = "Frozen reviewer fixture"
    items = []
    for number in range(60):
        item = {"payload_sha256": str(number), "query": str(number), "candidate_a": "fixture a", "candidate_b": "fixture b"}
        item["subagent_prompt"] = live.compose_subagent_prompt(prompt, query=item["query"],
            candidate_a=item["candidate_a"], candidate_b=item["candidate_b"])
        item["subagent_prompt_sha256"] = hashlib.sha256(item["subagent_prompt"].encode()).hexdigest()
        items.append(item)
    _write(Path(proof["packet_directory"]) / "WORKLIST.json", {"items": items})
    prepared = SimpleNamespace(identity=SimpleNamespace(paths=paths), reviewer_prompt={"prompt": prompt},
        reviewer_usage_policy_validation={"maximum_reviewer_dispatches": 59040},
        manifest={"runtime": {"reviewer_concurrency": 12}})
    state = driver.DriverState(first_pass_index=171, reviewer_dispatches=59040 if at_cap else 10080,
        consecutive_abandoned_rate=0, existing_unknown_charge_count=0, recovered_reviewer_waves=(), usage_event_count=0,
        quota_abandoned_waves=(170,), pending_quota_replacements=(170,))
    amendment = {"reviewer_transport_repair": {"quota_abandoned_waves": [proof]}, "recovery_manifest_sha256": "b" * 64}
    observed = []
    def admit(candidate, *, previously_admitted, incoming):
        assert incoming == 60
        if previously_admitted + incoming > 59040:
            raise live.Phase3MainLiveError("reviewer dispatch ceiling")
        return previously_admitted + incoming
    monkeypatch.setattr(live, "_admit_reviewer_wave_quantity", admit)
    def review(candidate, payloads, *, wave, held_run_lease):
        live.phase3_main_reviewer_commit.require_held_run_lease(held_run_lease, expected_path=paths.lease)
        assert wave == 171 and payloads == items
        observed.append(wave)
    monkeypatch.setattr(live, "_review_wave_same_process", review)
    with RunLease(paths.lease) as lease:
        if at_cap:
            with pytest.raises(live.Phase3MainLiveError, match="ceiling"):
                live._resume_quota_reviewer_waves(prepared, amendment, state, held_run_lease=lease)
            assert observed == []
        else:
            restored = live._resume_quota_reviewer_waves(prepared, amendment, state, held_run_lease=lease)
            assert restored.reviewer_dispatches == 10140 and restored.first_pass_index == 172
            assert observed == [171]
            assert restored.pending_quota_replacements == ()
    events = [json.loads(line) for line in paths.run_log.read_bytes().splitlines()]
    assert not any(event.get("event") == "reviewer_usage_wave_completed" and event.get("wave") == 170 for event in events)
    if at_cap:
        assert not any(event.get("event") == "reviewer_usage_reserved" and event.get("wave") == 171 for event in events)
