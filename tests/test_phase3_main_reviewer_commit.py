"""Crash-consistent local transaction tests for Phase 3 main reviewer waves."""
from __future__ import annotations

import base64
import hashlib
import inspect
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from rejudge import phase3_main_reviewer_commit as commit
from rejudge.phase2_dual_gate import DualGateDecisionStore
from rejudge.phase3_v3_live import RunLease


RUN_ID = "phase3-main-reviewer-commit-fixture"
MANIFEST_SHA = "a" * 64
GOOD = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: One atomic fact."


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _wave_fixture(
    root: Path,
    *,
    wave: int = 1,
    decisions: Path | None = None,
    reviewer_index: Path | None = None,
    lease_path: Path | None = None,
) -> dict[str, object]:
    decisions = decisions or (root / "main_reviewer_decisions.jsonl")
    reviewer_index = reviewer_index or (root / "main_reviewer_index.jsonl")
    lease_path = lease_path or (root / "main_run.lock")
    decisions.parent.mkdir(parents=True, exist_ok=True)
    decisions.touch(exist_ok=True)
    reviewer_index.touch(exist_ok=True)
    transaction_directory = (
        root / commit.FORMAL_REVIEW_PACKETS_DIRECTORY / f"wave-{wave:04d}"
    )
    transaction_directory.mkdir(parents=True, exist_ok=True)
    evidence = {
        name: _sha(f"{name}:{wave}")
        for name in commit.EVIDENCE_BINDING_FIELDS
    }
    payloads = tuple(_sha(f"payload:{wave}:{position}") for position in range(3))
    prompts = tuple(_sha(f"prompt:{wave}:{position}") for position in range(3))
    worklist = {
        "frozen_prompt_sha256": _sha("frozen reviewer prompt"),
        "separator": "fixture separator",
        "items": [
            {
                "payload_sha256": payload,
                "query": f"query {position}",
                "candidate_a": "A",
                "candidate_b": "B",
                "subagent_prompt": f"prompt {position}",
                "subagent_prompt_sha256": prompts[position],
            }
            for position, payload in enumerate(payloads)
        ],
    }
    entries = [
        {
            "payload_sha256": payloads[0],
            "raw_output": GOOD,
            "prompt_sha256": prompts[0],
        },
        {
            "payload_sha256": payloads[1],
            "raw_output": "not the frozen three-line protocol",
            "prompt_sha256": prompts[1],
        },
        {
            "payload_sha256": payloads[2],
            "status": "reviewer_error",
            "raw_output": "TOOL_USE_DETECTED: fixture",
        },
    ]
    transaction_id = commit.derive_wave_commit_transaction_id(
        run_id=RUN_ID,
        manifest_canonical_sha256=MANIFEST_SHA,
        wave=wave,
        run_lease_path=lease_path.resolve(),
        evidence_bindings=evidence,
    )
    index_row = {
        "schema_version": commit.REVIEWER_WAVE_SCHEMA,
        "run_id": RUN_ID,
        "manifest_canonical_sha256": MANIFEST_SHA,
        "authorization_canonical_sha256": evidence[
            "authorization_canonical_sha256"],
        "authorization_raw_sha256": evidence["authorization_raw_sha256"],
        "authorization_signature_raw_sha256": evidence[
            "authorization_signature_raw_sha256"],
        "capacity_plan_raw_sha256": evidence["capacity_plan_raw_sha256"],
        "wave": wave,
        "recorded_at_utc": f"2026-08-30T00:{wave:02d}:00+00:00",
        "payload_count": 3,
        "packet_directory": transaction_directory.resolve().as_posix(),
        "worklist_snapshot_raw_sha256": evidence[
            "worklist_snapshot_raw_sha256"],
        "packet_index_raw_sha256": evidence["packet_index_raw_sha256"],
        "dispatch_guard_raw_sha256": evidence["dispatch_guard_raw_sha256"],
        "rulings_raw_sha256": evidence["rulings_raw_sha256"],
        "reviewer_model": "gpt-5.6-sol",
        "reviewer_reasoning_effort": "xhigh",
        "reviewer_concurrency": 12,
        "reviewer_cli_resolved_path": (root / "codex.exe").resolve().as_posix(),
        "commit_counts": {"parsed": 1, "malformed": 1, "reviewer_error": 1},
        "run_lease_path": lease_path.resolve().as_posix(),
        "wave_commit_transaction_id": transaction_id,
    }
    return {
        "artifact_root": root.resolve(),
        "transaction_directory": transaction_directory.resolve(),
        "decision_store_path": decisions.resolve(),
        "reviewer_index_path": reviewer_index.resolve(),
        "run_id": RUN_ID,
        "manifest_canonical_sha256": MANIFEST_SHA,
        "wave": wave,
        "run_lease_path": lease_path.resolve(),
        "worklist": worklist,
        "entries": entries,
        "reviewer_index_row": index_row,
        "evidence_bindings": evidence,
        "transaction_id": transaction_id,
        "payloads": payloads,
    }


def _prepare(fixture: dict[str, object], lease: RunLease):
    return commit.prepare_reviewer_wave_commit(
        transaction_directory=fixture["transaction_directory"],
        decision_store_path=fixture["decision_store_path"],
        reviewer_index_path=fixture["reviewer_index_path"],
        run_id=fixture["run_id"],
        manifest_canonical_sha256=fixture["manifest_canonical_sha256"],
        wave=fixture["wave"],
        run_lease_path=fixture["run_lease_path"],
        held_run_lease=lease,
        worklist=fixture["worklist"],
        entries=fixture["entries"],
        reviewer_index_row=fixture["reviewer_index_row"],
    )


def _recover(fixture: dict[str, object], lease: RunLease):
    return commit.recover_reviewer_wave_commit(
        transaction_directory=fixture["transaction_directory"],
        decision_store_path=fixture["decision_store_path"],
        reviewer_index_path=fixture["reviewer_index_path"],
        run_id=fixture["run_id"],
        manifest_canonical_sha256=fixture["manifest_canonical_sha256"],
        wave=fixture["wave"],
        run_lease_path=fixture["run_lease_path"],
        held_run_lease=lease,
        evidence_bindings=fixture["evidence_bindings"],
    )


def _recover_from_intent(fixture: dict[str, object]):
    return commit.recover_reviewer_wave_commit_from_intent(
        transaction_directory=fixture["transaction_directory"],
        artifact_root=fixture["artifact_root"],
        run_lease_path=fixture["run_lease_path"],
    )


def _commit(fixture: dict[str, object], lease: RunLease):
    return commit.commit_reviewer_wave(
        transaction_directory=fixture["transaction_directory"],
        decision_store_path=fixture["decision_store_path"],
        reviewer_index_path=fixture["reviewer_index_path"],
        run_id=fixture["run_id"],
        manifest_canonical_sha256=fixture["manifest_canonical_sha256"],
        wave=fixture["wave"],
        run_lease_path=fixture["run_lease_path"],
        held_run_lease=lease,
        worklist=fixture["worklist"],
        entries=fixture["entries"],
        reviewer_index_row=fixture["reviewer_index_row"],
    )


def _validate(fixture: dict[str, object]):
    return commit.validate_reviewer_wave_commit(
        transaction_directory=fixture["transaction_directory"],
        decision_store_path=fixture["decision_store_path"],
        reviewer_index_path=fixture["reviewer_index_path"],
        run_id=fixture["run_id"],
        manifest_canonical_sha256=fixture["manifest_canonical_sha256"],
        wave=fixture["wave"],
        run_lease_path=fixture["run_lease_path"],
        evidence_bindings=fixture["evidence_bindings"],
    )


def _intent(fixture: dict[str, object]) -> dict[str, object]:
    path = Path(fixture["transaction_directory"]) / commit.WAVE_COMMIT_INTENT
    return json.loads(path.read_text(encoding="utf-8"))


def test_commit_writes_strict_v1_intent_receipt_and_unchanged_decision_rows(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        result = _commit(fixture, lease)

    paths = commit.reviewer_wave_transaction_paths(fixture["transaction_directory"])
    assert paths.intent.name == "WAVE_COMMIT_INTENT.json"
    assert paths.receipt.name == "WAVE_COMMIT_RECEIPT.json"
    assert result.wave_commit_transaction_id == fixture["transaction_id"]
    assert result.commit_counts == {"parsed": 1, "malformed": 1, "reviewer_error": 1}
    intent = json.loads(paths.intent.read_text(encoding="utf-8"))
    receipt = json.loads(paths.receipt.read_text(encoding="utf-8"))
    assert intent["schema_version"] == commit.INTENT_SCHEMA
    assert receipt["schema_version"] == commit.RECEIPT_SCHEMA
    assert intent["wave_commit_transaction_id"] == fixture["transaction_id"]
    assert receipt["wave_commit_transaction_id"] == fixture["transaction_id"]
    assert intent["run_lease_path"] == Path(fixture["run_lease_path"]).as_posix()
    assert receipt["run_lease_path"] == Path(fixture["run_lease_path"]).as_posix()
    assert set(DualGateDecisionStore(fixture["decision_store_path"])._by_payload) == set(  # noqa: SLF001
        fixture["payloads"])
    index_rows = [
        json.loads(line)
        for line in Path(fixture["reviewer_index_path"]).read_text(
            encoding="utf-8").splitlines()
    ]
    assert index_rows == [fixture["reviewer_index_row"]]
    assert not paths.intent.with_name(f".{paths.intent.name}.publish.tmp").exists()
    assert not paths.receipt.with_name(f".{paths.receipt.name}.publish.tmp").exists()
    assert _validate(fixture).wave_commit_transaction_id == fixture["transaction_id"]


@pytest.mark.parametrize("remaining", [1, 2, 17])
def test_recovery_appends_a_torn_decision_suffix_including_final_newline(
    tmp_path, remaining,
):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    intent = _intent(fixture)
    suffix = base64.b64decode(intent["decisions"]["append_base64"])
    cut = max(1, len(suffix) - remaining)
    Path(fixture["decision_store_path"]).write_bytes(suffix[:cut])

    with RunLease(fixture["run_lease_path"]) as lease:
        _recover(fixture, lease)

    decisions = Path(fixture["decision_store_path"]).read_bytes()
    assert decisions.endswith(b"\n")
    assert hashlib.sha256(decisions).hexdigest() == intent["decisions"]["target_raw_sha256"]
    assert _validate(fixture).commit_counts == intent["commit_counts"]


def test_recovery_appends_torn_index_only_after_decisions_are_complete(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    intent = _intent(fixture)
    decision_suffix = base64.b64decode(intent["decisions"]["append_base64"])
    index_suffix = base64.b64decode(intent["reviewer_index"]["append_base64"])
    Path(fixture["decision_store_path"]).write_bytes(decision_suffix)
    Path(fixture["reviewer_index_path"]).write_bytes(index_suffix[:-1])

    with RunLease(fixture["run_lease_path"]) as lease:
        _recover(fixture, lease)

    assert Path(fixture["reviewer_index_path"]).read_bytes() == index_suffix
    assert _validate(fixture).wave_commit_transaction_id == fixture["transaction_id"]


def test_recovery_rejects_index_ahead_without_touching_either_store(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    intent = _intent(fixture)
    index_prefix = base64.b64decode(intent["reviewer_index"]["append_base64"])[:7]
    Path(fixture["reviewer_index_path"]).write_bytes(index_prefix)

    with RunLease(fixture["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="index is ahead"):
            _recover(fixture, lease)

    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == index_prefix


@pytest.mark.parametrize("target", ["decisions", "reviewer_index"])
def test_recovery_rejects_divergent_append_bytes(tmp_path, target):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    path = Path(
        fixture["decision_store_path"]
        if target == "decisions"
        else fixture["reviewer_index_path"])
    path.write_bytes(b"not-an-exact-suffix")

    with RunLease(fixture["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="diverged"):
            _recover(fixture, lease)


def test_commit_and_recovery_are_idempotent_without_duplicate_rows(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        first = _commit(fixture, lease)
    paths = commit.reviewer_wave_transaction_paths(fixture["transaction_directory"])
    snapshot = (
        Path(fixture["decision_store_path"]).read_bytes(),
        Path(fixture["reviewer_index_path"]).read_bytes(),
        paths.intent.read_bytes(),
        paths.receipt.read_bytes(),
    )

    with RunLease(fixture["run_lease_path"]) as lease:
        second = _commit(fixture, lease)

    assert second == first
    assert snapshot == (
        Path(fixture["decision_store_path"]).read_bytes(),
        Path(fixture["reviewer_index_path"]).read_bytes(),
        paths.intent.read_bytes(),
        paths.receipt.read_bytes(),
    )
    assert len(Path(fixture["reviewer_index_path"]).read_text(
        encoding="utf-8").splitlines()) == 1


def test_read_only_validator_accepts_exact_later_wave_suffixes(tmp_path):
    first = _wave_fixture(tmp_path / "first", wave=1)
    with RunLease(first["run_lease_path"]) as lease:
        _commit(first, lease)
    second = _wave_fixture(
        tmp_path / "second",
        wave=2,
        decisions=Path(first["decision_store_path"]),
        reviewer_index=Path(first["reviewer_index_path"]),
        lease_path=Path(first["run_lease_path"]),
    )
    with RunLease(first["run_lease_path"]) as lease:
        _commit(second, lease)

    first_audit = _validate(first)
    assert first_audit.wave_commit_transaction_id == first["transaction_id"]
    assert first_audit.reviewer_index_row == first["reviewer_index_row"]
    assert first_audit.reviewer_index.append_bytes == (
        json.dumps(
            first["reviewer_index_row"],
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
        ) + "\n"
    ).encode("utf-8")
    first_intent = _intent(first)
    assert first_audit.decisions.prior_tail == commit.ReviewerWaveDecisionTail(
        **first_intent["decisions"]["prior_tail"])
    assert first_audit.decisions.target_tail == commit.ReviewerWaveDecisionTail(
        **first_intent["decisions"]["target_tail"])
    assert _validate(second).wave_commit_transaction_id == second["transaction_id"]
    assert len(Path(first["reviewer_index_path"]).read_text(
        encoding="utf-8").splitlines()) == 2


def test_whole_batch_prompt_failure_is_atomic(tmp_path):
    fixture = _wave_fixture(tmp_path)
    entries = deepcopy(fixture["entries"])
    entries[1]["prompt_sha256"] = "f" * 64
    fixture["entries"] = entries

    with RunLease(fixture["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="wrong prompt"):
            _prepare(fixture, lease)

    paths = commit.reviewer_wave_transaction_paths(fixture["transaction_directory"])
    assert not paths.intent.exists()
    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == b""


@pytest.mark.parametrize("case", ["duplicate", "unknown", "bad_raw", "bad_shape"])
def test_batch_prevalidation_rejects_invalid_or_duplicate_entries(tmp_path, case):
    fixture = _wave_fixture(tmp_path)
    entries = deepcopy(fixture["entries"])
    if case == "duplicate":
        entries[1] = deepcopy(entries[0])
    elif case == "unknown":
        entries[1]["payload_sha256"] = "f" * 64
    elif case == "bad_raw":
        entries[1]["raw_output"] = 7
    else:
        entries[1]["unexpected"] = True
    fixture["entries"] = entries

    with RunLease(fixture["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError):
            _prepare(fixture, lease)

    assert not commit.reviewer_wave_transaction_paths(
        fixture["transaction_directory"]).intent.exists()


def test_batch_prevalidation_rejects_duplicate_worklist_payload(tmp_path):
    fixture = _wave_fixture(tmp_path)
    worklist = deepcopy(fixture["worklist"])
    worklist["items"][1]["payload_sha256"] = worklist["items"][0]["payload_sha256"]
    fixture["worklist"] = worklist

    with RunLease(fixture["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="worklist repeats"):
            _prepare(fixture, lease)


def test_new_wave_rejects_a_payload_already_committed_by_prior_wave(tmp_path):
    first = _wave_fixture(tmp_path / "first", wave=1)
    with RunLease(first["run_lease_path"]) as lease:
        _commit(first, lease)
    second = _wave_fixture(
        tmp_path / "second",
        wave=2,
        decisions=Path(first["decision_store_path"]),
        reviewer_index=Path(first["reviewer_index_path"]),
        lease_path=Path(first["run_lease_path"]),
    )
    worklist = deepcopy(second["worklist"])
    entries = deepcopy(second["entries"])
    prior_payload = first["payloads"][0]
    worklist["items"][0]["payload_sha256"] = prior_payload
    entries[0]["payload_sha256"] = prior_payload
    second["worklist"] = worklist
    second["entries"] = entries

    with RunLease(first["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="already committed"):
            _prepare(second, lease)


def test_prepare_rejects_preexisting_index_ahead_and_decision_ahead(tmp_path):
    index_ahead = _wave_fixture(tmp_path / "index-ahead")
    row = index_ahead["reviewer_index_row"]
    Path(index_ahead["reviewer_index_path"]).write_bytes(
        (
            json.dumps(row, ensure_ascii=True, allow_nan=False, sort_keys=True)
            + "\n"
        ).encode("utf-8")
    )
    with RunLease(index_ahead["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="index is ahead"):
            _prepare(index_ahead, lease)

    decision_ahead = _wave_fixture(tmp_path / "decision-ahead")
    payload = decision_ahead["payloads"][0]
    DualGateDecisionStore(decision_ahead["decision_store_path"]).commit(
        payload, "ALLOW", "Allowed", "One atomic fact.", GOOD, "parsed")
    with RunLease(decision_ahead["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="unindexed prefix"):
            _prepare(decision_ahead, lease)


def test_valid_canonical_intent_and_receipt_corruption_fail_closed(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    paths = commit.reviewer_wave_transaction_paths(fixture["transaction_directory"])
    intent = json.loads(paths.intent.read_text(encoding="utf-8"))
    intent["wave"] = 2
    _canonical_write(paths.intent, intent)
    with RunLease(fixture["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="transaction ID"):
            _recover(fixture, lease)

    second = _wave_fixture(tmp_path / "receipt")
    with RunLease(second["run_lease_path"]) as lease:
        _commit(second, lease)
    second_paths = commit.reviewer_wave_transaction_paths(second["transaction_directory"])
    receipt = json.loads(second_paths.receipt.read_text(encoding="utf-8"))
    receipt["commit_counts"]["parsed"] = 2
    _canonical_write(second_paths.receipt, receipt)
    with pytest.raises(commit.ReviewerWaveCommitError, match="differs from its intent"):
        _validate(second)


def test_receipted_prefix_corruption_is_rejected(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _commit(fixture, lease)
    decisions = Path(fixture["decision_store_path"])
    raw = bytearray(decisions.read_bytes())
    raw[10] ^= 1
    decisions.write_bytes(raw)

    with pytest.raises(commit.ReviewerWaveCommitError, match="prefix diverged"):
        _validate(fixture)


def test_held_exact_run_lease_is_mandatory(tmp_path):
    fixture = _wave_fixture(tmp_path)
    unopened = RunLease(fixture["run_lease_path"])
    with pytest.raises(commit.ReviewerWaveCommitError, match="not currently held"):
        _prepare(fixture, unopened)
    other_path = (tmp_path / "other.lock").resolve()
    with RunLease(other_path) as other:
        with pytest.raises(commit.ReviewerWaveCommitError, match="path differs"):
            _prepare(fixture, other)

    class DerivedRunLease(RunLease):
        pass

    with DerivedRunLease(fixture["run_lease_path"]) as derived:
        with pytest.raises(commit.ReviewerWaveCommitError, match="RunLease instance"):
            _prepare(fixture, derived)
    assert not commit.reviewer_wave_transaction_paths(
        fixture["transaction_directory"]).intent.exists()


def test_public_require_held_run_lease_is_read_only_and_exact(tmp_path):
    expected = (tmp_path / "main_run.lock").resolve()
    unopened = RunLease(expected)
    with pytest.raises(commit.ReviewerWaveCommitError, match="not currently held"):
        commit.require_held_run_lease(unopened, expected_path=expected)
    assert not expected.exists()

    other_path = (tmp_path / "other.lock").resolve()
    with RunLease(other_path) as other:
        with pytest.raises(commit.ReviewerWaveCommitError, match="path differs"):
            commit.require_held_run_lease(other, expected_path=expected)
    assert not expected.exists()

    with RunLease(expected) as held:
        assert commit.require_held_run_lease(
            held, expected_path=expected) is None


def test_deterministic_module_temp_is_rewritten_and_cleaned(tmp_path):
    fixture = _wave_fixture(tmp_path)
    paths = commit.reviewer_wave_transaction_paths(fixture["transaction_directory"])
    intent_temp = paths.intent.with_name(f".{paths.intent.name}.publish.tmp")
    receipt_temp = paths.receipt.with_name(f".{paths.receipt.name}.publish.tmp")
    intent_temp.write_bytes(b"torn intent temp")
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    assert paths.intent.exists()
    assert not intent_temp.exists()
    receipt_temp.write_bytes(b"torn receipt temp")
    with RunLease(fixture["run_lease_path"]) as lease:
        _recover(fixture, lease)
    assert paths.receipt.exists()
    assert not receipt_temp.exists()


def test_offline_recovery_loads_intent_context_and_acquires_exact_lease(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    intent = _intent(fixture)
    suffix = base64.b64decode(intent["decisions"]["append_base64"])
    Path(fixture["decision_store_path"]).write_bytes(suffix[:-5])

    context = commit.load_reviewer_wave_recovery_context(
        transaction_directory=fixture["transaction_directory"],
        artifact_root=fixture["artifact_root"],
        run_lease_path=fixture["run_lease_path"],
    )
    assert context.wave_commit_transaction_id == fixture["transaction_id"]
    assert context.intent_raw_sha256 == hashlib.sha256(
        commit.reviewer_wave_transaction_paths(
            fixture["transaction_directory"]).intent.read_bytes()
    ).hexdigest()
    assert context.run_lease_path == Path(fixture["run_lease_path"])
    result = _recover_from_intent(fixture)

    assert result.wave_commit_transaction_id == fixture["transaction_id"]
    assert _validate(fixture) == result
    with RunLease(fixture["run_lease_path"]):
        pass


def test_transaction_directory_symlink_fails_closed(tmp_path, monkeypatch):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    transaction_directory = Path(fixture["transaction_directory"])
    original_is_symlink = Path.is_symlink

    def is_transaction_symlink(path):
        if path == transaction_directory:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_transaction_symlink)
    with pytest.raises(commit.ReviewerWaveCommitError, match="symbolic link"):
        commit.load_reviewer_wave_recovery_context(
            transaction_directory=transaction_directory,
            artifact_root=fixture["artifact_root"],
            run_lease_path=fixture["run_lease_path"],
        )


def test_offline_recovery_rechecks_exact_intent_after_acquiring_lease(
    tmp_path, monkeypatch,
):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    original_loader = commit._load_reviewer_wave_recovery_context  # noqa: SLF001
    calls = 0

    def drifting_loader(**kwargs):
        nonlocal calls
        calls += 1
        loaded = original_loader(**kwargs)
        if calls == 2:
            return replace(
                loaded,
                context=replace(
                    loaded.context,
                    intent_raw_sha256="f" * 64,
                ),
            )
        return loaded

    monkeypatch.setattr(
        commit, "_load_reviewer_wave_recovery_context", drifting_loader)
    with pytest.raises(commit.ReviewerWaveCommitError, match="changed while"):
        _recover_from_intent(fixture)

    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == b""
    with RunLease(fixture["run_lease_path"]):
        pass


@pytest.mark.parametrize(
    "field", ["decision_store_path", "reviewer_index_path"])
def test_intent_driven_recovery_rejects_forged_external_store_path(
    tmp_path, field,
):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    external = (
        tmp_path.parent / f"{tmp_path.name}-external-{field}.jsonl"
    ).resolve()
    external.write_bytes(b"outside formal artifact root\n")
    before = external.read_bytes()
    intent_path = commit.reviewer_wave_transaction_paths(
        fixture["transaction_directory"]).intent
    intent = _intent(fixture)
    intent[field] = external.as_posix()
    _canonical_write(intent_path, intent)

    with pytest.raises(
        commit.ReviewerWaveCommitError,
        match=f"{field} is not the independently bound formal path",
    ):
        _recover_from_intent(fixture)

    assert external.read_bytes() == before
    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == b""


def test_intent_driven_recovery_rejects_missing_independent_lease_without_creating_it(
    tmp_path,
):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    missing = (tmp_path / "missing-independent.lock").resolve()

    with pytest.raises(
        commit.ReviewerWaveCommitError,
        match="formal run lease must already exist",
    ):
        commit.recover_reviewer_wave_commit_from_intent(
            transaction_directory=fixture["transaction_directory"],
            artifact_root=fixture["artifact_root"],
            run_lease_path=missing,
        )

    assert not missing.exists()
    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == b""


def test_intent_driven_recovery_rejects_wrong_preexisting_independent_lease(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    wrong = (tmp_path / "wrong-independent.lock").resolve()
    wrong.write_bytes(b"0")

    with pytest.raises(
        commit.ReviewerWaveCommitError,
        match="run_lease_path is not the independently bound formal path",
    ):
        commit.recover_reviewer_wave_commit_from_intent(
            transaction_directory=fixture["transaction_directory"],
            artifact_root=fixture["artifact_root"],
            run_lease_path=wrong,
        )

    assert wrong.read_bytes() == b"0"
    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == b""


def test_intent_driven_recovery_rejects_symlinked_artifact_ancestor(
    tmp_path, monkeypatch,
):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    linked_ancestor = Path(fixture["artifact_root"]).parent
    original = commit._is_link_like  # noqa: SLF001

    def report_link(path):
        return path == linked_ancestor or original(path)

    monkeypatch.setattr(commit, "_is_link_like", report_link)
    with pytest.raises(
        commit.ReviewerWaveCommitError,
        match="formal artifact root must not contain",
    ):
        _recover_from_intent(fixture)

    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == b""


def test_intent_driven_recovery_requires_direct_packet_root_child(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    nested_parent = (
        Path(fixture["artifact_root"])
        / commit.FORMAL_REVIEW_PACKETS_DIRECTORY
        / "nested"
    )
    nested_parent.mkdir()
    nested_transaction = nested_parent / "wave-0001"
    Path(fixture["transaction_directory"]).rename(nested_transaction)

    with pytest.raises(
        commit.ReviewerWaveCommitError,
        match="must be a direct child",
    ):
        commit.recover_reviewer_wave_commit_from_intent(
            transaction_directory=nested_transaction,
            artifact_root=fixture["artifact_root"],
            run_lease_path=fixture["run_lease_path"],
        )

    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == b""


def test_intent_driven_recovery_never_third_loads_a_swappable_intent(
    tmp_path, monkeypatch,
):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    original = commit._load_intent  # noqa: SLF001
    calls = 0

    def fail_on_third_load(path):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise AssertionError("recovery reloaded mutable intent after lock validation")
        return original(path)

    monkeypatch.setattr(commit, "_load_intent", fail_on_third_load)
    result = _recover_from_intent(fixture)

    assert calls == 2
    assert result.wave_commit_transaction_id == fixture["transaction_id"]
    monkeypatch.setattr(commit, "_load_intent", original)
    assert _validate(fixture) == result


def test_intent_parent_fsync_failure_is_not_misread_as_publish_conflict(
    tmp_path, monkeypatch,
):
    fixture = _wave_fixture(tmp_path)

    def fail_parent_fsync(_path):
        raise OSError("fixture parent fsync failure")

    monkeypatch.setattr(commit, "_fsync_parent", fail_parent_fsync)
    with RunLease(fixture["run_lease_path"]) as lease:
        with pytest.raises(
            commit.ReviewerWaveCommitError, match="durably verify published"
        ):
            _prepare(fixture, lease)

    assert Path(fixture["decision_store_path"]).read_bytes() == b""
    assert Path(fixture["reviewer_index_path"]).read_bytes() == b""


def test_retry_with_changed_evidence_or_entries_refuses_existing_intent(tmp_path):
    fixture = _wave_fixture(tmp_path)
    with RunLease(fixture["run_lease_path"]) as lease:
        _prepare(fixture, lease)
    changed = deepcopy(fixture)
    entries = deepcopy(fixture["entries"])
    entries[1]["raw_output"] = "different malformed output"
    changed["entries"] = entries

    with RunLease(fixture["run_lease_path"]) as lease:
        with pytest.raises(commit.ReviewerWaveCommitError, match="entries_canonical_sha256"):
            _prepare(changed, lease)


def test_public_api_has_no_callback_or_external_dispatch_surface():
    for function in (
        commit.prepare_reviewer_wave_commit,
        commit.recover_reviewer_wave_commit,
        commit.commit_reviewer_wave,
        commit.validate_reviewer_wave_commit,
        commit.load_reviewer_wave_recovery_context,
        commit.recover_reviewer_wave_commit_from_intent,
        commit.require_held_run_lease,
    ):
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & {
            "callback", "client", "provider", "reviewer", "dispatch", "command",
        }
    source = inspect.getsource(commit)
    for forbidden in (
        "import subprocess", "import socket", "import requests", "RejudgeClient", "Callable",
    ):
        assert forbidden not in source
