"""Recovery preserves finished work while allowing explicit operational changes."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rejudge import phase3_main_authorization as authorization
from rejudge import phase3_main_recovery as recovery
from rejudge import api_client
from rejudge.phase2_execution import canonical_sha256


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value) + "\n", encoding="utf-8")


@pytest.fixture
def stopped_run(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    keys = set(recovery.APPEND_ONLY_OUTPUTS) | {
        "completion", "identity_binding", "review_packets_root", "reviewer_worklist", "active_marker"}
    paths = {key: str(root / (key + (".jsonl" if key in recovery.APPEND_ONLY_OUTPUTS else ".json")))
             for key in keys}
    paths["review_packets_root"] = str(root / "packets")
    manifest = {
        "run_id": "run-original", "source_commit": "a" * 40,
        "runtime": {"model_ids": ["model-a", "model-b"], "provider_worker_concurrency": 1,
                    "reviewer_concurrency": 12},
        "input_bindings": {"analysis_pins": {"sha256": "e" * 64}},
        "seeds": {"analysis": 123}, "inventory": {"count": 9840},
        "spend": {"stage_cap_usd": "1100.00"},
        "output_contract": {"artifact_root": str(root), "paths": paths,
                            "identity_registry_root": str(tmp_path / "registry")}}
    auth = {"run_id": "run-original", "stage_cap_usd": "1100.00", "no_resume": True}
    manifest_path, auth_path = tmp_path / "manifest.json", tmp_path / "authorization.json"
    write_json(manifest_path, manifest)
    write_json(auth_path, auth)
    Path(str(auth_path) + ".sig").write_text("test signature", encoding="utf-8")
    write_json(paths["identity_binding"], {"run_id": manifest["run_id"]})
    start, _, _ = recovery._identity_paths(manifest)
    write_json(start, {"run_id": manifest["run_id"]})
    for key in recovery.APPEND_ONLY_OUTPUTS:
        Path(paths[key]).write_bytes(b"{\"completed\":true}\n" if key == "results" else b"")
    kwargs = dict(execution_source_commit="b" * 40, provider_worker_concurrency=8,
                  per_model_limits={"model-a": 2, "model-b": 4}, block_size=8,
                  recorded_at_utc="2026-09-08T02:00:00Z", reason="Host process interrupted",
                  owner_instruction="Preserve completed work and resume within the existing cap")
    return SimpleNamespace(root=root, manifest=manifest, paths=paths,
                           manifest_path=manifest_path, auth_path=auth_path, kwargs=kwargs)


def build(run):
    return recovery.build_recovery_manifest(run.manifest_path, run.auth_path, **run.kwargs)


def validate(run, value, **kwargs):
    return recovery.validate_recovery_manifest(value, manifest_path=run.manifest_path,
                                               authorization_path=run.auth_path, **kwargs)


def add_interruption(run, attempt_id="attempt-1"):
    marker = {"schema_version": "request_journal_dispatch_marker_v1", "cell_key": "cell-1",
              "call_role": "judge_query", "slot": 0, "attempt": 1, "request_sha256": "f" * 64}
    reservation = {"status": "reserved", "attempt_id": attempt_id, "event_hash": "c" * 64,
                   "cost_usd": 0.11049, "metadata": {"cell_key": "cell-1", "call_role": "judge_query",
                   "query_index": 0, "attempt": 1, "journal_request_sha256": "f" * 64}}
    marker_path = Path(run.paths["request_journal"] + ".unresolved.json")
    write_json(marker_path, marker)
    write_json(run.paths["usage_ledger"], reservation)
    return marker_path, reservation


def append_event(run, event):
    with Path(run.paths["usage_ledger"]).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def test_preserves_original_science_and_allows_new_operational_commit(stopped_run):
    run = stopped_run
    before = run.manifest_path.read_bytes(), run.auth_path.read_bytes()
    value = build(run)
    result = validate(run, value, current_source_commit="b" * 40)
    assert result["run_id"] == "run-original"
    assert result["provider_worker_concurrency"] == 8
    assert result["recovery_manifest_sha256"] == canonical_sha256(value)
    assert before == (run.manifest_path.read_bytes(), run.auth_path.read_bytes())


def test_append_growth_is_allowed_but_completed_bytes_cannot_change(stopped_run):
    value = build(stopped_run)
    path = Path(stopped_run.paths["results"])
    old = path.read_bytes()
    path.write_bytes(old + b'{"next":true}\n')
    validate(stopped_run, value)
    with pytest.raises(recovery.RecoveryError, match="changed"):
        validate(stopped_run, value, allow_growth=False)
    path.write_bytes(b'X' + path.read_bytes()[1:])
    with pytest.raises(recovery.RecoveryError, match="preserved recovery bytes changed"):
        validate(stopped_run, value)


def test_truncated_preserved_artifact_is_rejected(stopped_run):
    value = build(stopped_run)
    Path(stopped_run.paths["results"]).write_bytes(b"")
    with pytest.raises(recovery.RecoveryError, match="truncated"):
        validate(stopped_run, value)


@pytest.mark.parametrize("status", ["completed", "voided"])
def test_completed_and_voided_identities_cannot_resume(stopped_run, status):
    value = build(stopped_run)
    _, completed, voided = recovery._identity_paths(stopped_run.manifest)
    write_json(completed if status == "completed" else voided, {"status": status})
    with pytest.raises(recovery.RecoveryError, match=status):
        validate(stopped_run, value)


@pytest.mark.parametrize("key,value", [("stage_cap_usd", "1150"),
                                       ("scientific_contract_sha256", "d" * 64),
                                       ("provider_worker_concurrency", 0),
                                       ("per_model_limits", {"different-model": 4})])
def test_scope_changes_are_rejected(stopped_run, key, value):
    manifest = build(stopped_run)
    manifest[key] = value
    with pytest.raises(recovery.RecoveryError):
        validate(stopped_run, manifest)


def test_current_execution_commit_must_match(stopped_run):
    with pytest.raises(recovery.RecoveryError, match="checkout differs"):
        validate(stopped_run, build(stopped_run), current_source_commit="c" * 40)


def test_unknown_request_can_be_resolved_at_full_cost_without_rewriting_history(stopped_run):
    run = stopped_run
    marker, reservation = add_interruption(run)
    value = build(run)
    before = Path(run.paths["usage_ledger"]).read_bytes()
    terminal = dict(reservation, status="unknown_charge", event_hash="d" * 64,
                    recovery_manifest_sha256=canonical_sha256(value))
    append_event(run, terminal)
    validate(run, value)  # Durable charge followed by a crash before marker removal is safe.
    marker.unlink()
    validate(run, value)
    assert Path(run.paths["usage_ledger"]).read_bytes().startswith(before)


def test_marker_cannot_disappear_without_its_full_conservative_charge(stopped_run):
    marker, _ = add_interruption(stopped_run)
    value = build(stopped_run)
    marker.unlink()
    with pytest.raises(recovery.RecoveryError, match="before conservative accounting"):
        validate(stopped_run, value)


@pytest.mark.parametrize("response", ["", "CLAIM: retained exact response"])
def test_successful_journaled_request_with_leftover_marker_needs_no_new_charge(stopped_run, response):
    marker, reservation = add_interruption(stopped_run)
    terminal = dict(reservation, status="success", cost_usd=0.01, event_hash="d" * 64)
    append_event(stopped_run, terminal)
    row = {**json.loads(marker.read_bytes()), "sequence": 0, "event_hash": "e" * 64,
           "response": response}
    write_json(stopped_run.paths["request_journal"], row)
    value = build(stopped_run)
    item, = value["interrupted_dispatches"]
    assert item["disposition"] == "completed_journaled_response"
    assert "response" not in item["journal_entry"]
    before = Path(stopped_run.paths["usage_ledger"]).read_bytes()
    marker.unlink()
    validate(stopped_run, value)
    assert Path(stopped_run.paths["usage_ledger"]).read_bytes() == before


def test_marker_before_first_reservation_can_be_cleared_without_charging(stopped_run):
    marker_path, _ = add_interruption(stopped_run)
    Path(stopped_run.paths["usage_ledger"]).unlink()
    api_client.prepare_usage_ledger(stopped_run.paths["usage_ledger"], allow_create=True)
    original = Path(stopped_run.paths["usage_ledger"]).read_bytes()
    value = build(stopped_run)
    item, = value["interrupted_dispatches"]
    assert item["disposition"] == "unreserved_dispatch"
    assert "reservation" not in item
    marker_path.unlink()
    validate(stopped_run, value)
    assert Path(stopped_run.paths["usage_ledger"]).read_bytes() == original


def test_unreserved_retry_uses_causal_ledger_frontier_and_never_timing_alone(stopped_run):
    marker_path, reservation = add_interruption(stopped_run)
    Path(stopped_run.paths["usage_ledger"]).unlink()
    api_client.prepare_usage_ledger(stopped_run.paths["usage_ledger"], allow_create=True)
    genesis = json.loads(Path(stopped_run.paths["usage_ledger"]).read_bytes())
    reservation.update(ledger_id=genesis["ledger_id"], sequence=1, prev_event_hash=genesis["event_hash"],
                       model="fixture", kind="query", seed=1, attempt=0, estimated_tokens=3,
                       reserved_prompt_tokens=2, reserved_completion_tokens=1,
                       prompt_tokens=None, completion_tokens=None)
    reservation["event_hash"] = api_client._usage_event_hash(reservation)
    terminal = dict(reservation, status="unknown_charge", sequence=2, prev_event_hash=reservation["event_hash"])
    terminal["event_hash"] = api_client._usage_event_hash(terminal)
    append_event(stopped_run, reservation)
    append_event(stopped_run, terminal)
    with pytest.raises(recovery.RecoveryError, match="unreserved marker"):
        build(stopped_run)
    marker = json.loads(marker_path.read_bytes())
    marker["ledger_boundary"] = {key: terminal[key] for key in ("ledger_id", "sequence", "event_hash")}
    write_json(marker_path, marker)
    value = build(stopped_run)
    assert value["interrupted_dispatches"][0]["disposition"] == "unreserved_dispatch"


@pytest.mark.parametrize("change", ["cheaper", "success", "other_recovery"])
def test_interrupted_request_cannot_be_laundered_into_cheaper_or_selected_success(stopped_run, change):
    run = stopped_run
    _, reservation = add_interruption(run)
    value = build(run)
    terminal = dict(reservation, status="unknown_charge", event_hash="d" * 64,
                    recovery_manifest_sha256=canonical_sha256(value))
    if change == "cheaper":
        terminal["cost_usd"] = 0
    elif change == "success":
        terminal["status"] = "success"
    else:
        terminal["recovery_manifest_sha256"] = "e" * 64
    append_event(run, terminal)
    with pytest.raises(recovery.RecoveryError, match="exact conservative recovery charge"):
        validate(run, value)


def test_future_interruption_requires_a_fresh_snapshot(stopped_run):
    value = build(stopped_run)
    add_interruption(stopped_run)
    with pytest.raises(recovery.RecoveryError, match="fresh recovery snapshot"):
        validate(stopped_run, value)
    validate(stopped_run, build(stopped_run))


def test_lightweight_scope_check_skips_growing_artifact_reads(stopped_run, monkeypatch):
    value = build(stopped_run)
    def forbidden(*args, **kwargs):
        raise AssertionError("must not rescan growing ledger")
    monkeypatch.setattr(recovery, "_ledger_events", forbidden)
    validate(stopped_run, value, verify_artifacts=False)


def test_existing_review_packet_bytes_are_preserved_and_new_packets_allowed(stopped_run):
    packet = Path(stopped_run.paths["review_packets_root"]) / "wave-1" / "result.json"
    write_json(packet, {"decision": "existing"})
    write_json(packet.parent / "WAVE_COMMIT_RECEIPT.json", {"committed": True})
    value = build(stopped_run)
    write_json(packet.parent.parent / "wave-2" / "result.json", {"decision": "new"})
    validate(stopped_run, value)
    write_json(packet, {"decision": "changed"})
    with pytest.raises(recovery.RecoveryError, match="changed"):
        validate(stopped_run, value)


def test_recovery_uses_distinct_signature_namespace_and_reports_exact_hashes(stopped_run, monkeypatch):
    run = stopped_run
    value = build(run)
    path = run.root / "recovery.json"
    write_json(path, value)
    Path(str(path) + ".sig").write_text("recovery signature", encoding="utf-8")
    def authenticated(source, **kwargs):
        assert Path(source) == path
        assert kwargs == {"signature_namespace": recovery.RECOVERY_SIGNATURE_NAMESPACE}
        return copy.deepcopy(value)
    monkeypatch.setattr(authorization, "load_authenticated_owner_authorization", authenticated)
    result = recovery.load_authenticated_recovery(path, manifest_path=run.manifest_path,
                                                  authorization_path=run.auth_path)
    assert result["recovery_path"] == path.resolve().as_posix()
    assert len(result["recovery_raw_sha256"]) == 64
    assert len(result["recovery_signature_raw_sha256"]) == 64


def test_adaptive_concurrency_is_bound_to_initial_and_maximum_limits(stopped_run):
    stopped_run.kwargs["initial_per_model_limits"] = {"model-a": 1, "model-b": 2}
    value = build(stopped_run)
    assert value["initial_per_model_limits"] == {"model-a": 1, "model-b": 2}
    value["initial_per_model_limits"]["model-a"] = 3
    with pytest.raises(recovery.RecoveryError, match="within the signed"):
        validate(stopped_run, value)


def _approve_ceiling_amendment(run):
    path = run.root / "approved-validation.json"
    write_json(path, {"owner_instruction": "Raise uncertainty to $150, keep total cap $1100",
                      "preparation_actor": "Codex", "passed": True})
    run.kwargs.update(validation_record=path, run_uncertain_ceiling_usd="150.00")
    return path


def test_ceiling_amendment_changes_only_approved_runtime_ceiling(stopped_run):
    from rejudge.phase3_main_runtime_policies import UNCERTAIN_SPEND_POLICY_RAW_SHA256
    run = stopped_run
    before = run.manifest_path.read_bytes(), run.auth_path.read_bytes()
    original = {"policy_raw_sha256": UNCERTAIN_SPEND_POLICY_RAW_SHA256,
                "run_uncertain_ceiling_usd": 100.0,
                "initial_run_uncertain_spend_usd": 0.0, "unknown_charge_pass_allowance": 50}
    assert recovery.apply_uncertain_spend_amendment(original, build(run)) == original
    _approve_ceiling_amendment(run)
    value = build(run)
    result = recovery.apply_uncertain_spend_amendment(original, validate(run, value))
    assert result == dict(original, run_uncertain_ceiling_usd=150.0)
    assert original["run_uncertain_ceiling_usd"] == 100.0
    assert value["stage_cap_usd"] == "1100.00"
    assert value["uncertain_spend_amendment"]["preserve_cumulative_accounting"] is True
    assert before == (run.manifest_path.read_bytes(), run.auth_path.read_bytes())


@pytest.mark.parametrize("amount", [True, "100", "149", "151", "1101", "NaN", "bad"])
def test_ceiling_builder_refuses_amounts_outside_explicit_approval(stopped_run, amount):
    _approve_ceiling_amendment(stopped_run)
    stopped_run.kwargs["run_uncertain_ceiling_usd"] = amount
    with pytest.raises(recovery.RecoveryError, match="uncertainty"):
        build(stopped_run)


def test_ceiling_amendment_requires_bound_approval_evidence(stopped_run):
    stopped_run.kwargs["run_uncertain_ceiling_usd"] = "150.00"
    with pytest.raises(recovery.RecoveryError, match="approval validation record"):
        build(stopped_run)


@pytest.mark.parametrize("field,value", [
    ("original_policy_raw_sha256", "0" * 64),
    ("original_run_uncertain_ceiling_usd", "0.00"),
    ("run_uncertain_ceiling_usd", "200.00"),
    ("preserve_cumulative_accounting", False),
    ("approval_validation_record_raw_sha256", "0" * 64),
])
def test_ceiling_amendment_scope_rechecked_without_archive_rescan(stopped_run, field, value):
    _approve_ceiling_amendment(stopped_run)
    amendment = build(stopped_run)
    amendment["uncertain_spend_amendment"][field] = value
    with pytest.raises(recovery.RecoveryError, match="approved scope"):
        validate(stopped_run, amendment, verify_artifacts=False)


def _settled_unknown_run(run):
    from rejudge.request_journal import RequestJournal, CallKey
    ledger = Path(run.paths["usage_ledger"])
    ledger.unlink()
    identity = api_client.prepare_usage_ledger(ledger, allow_create=True)
    genesis = json.loads(ledger.read_bytes())
    previous = genesis
    metadata = {"cell_key": "cell-1", "call_role": "judge_query", "query_index": 0,
                "attempt": 1, "journal_request_sha256": "f" * 64}
    for attempt in range(3):
        reservation = {"status": "reserved", "attempt_id": f"closed-{attempt}",
                       "ledger_id": genesis["ledger_id"], "sequence": previous["sequence"] + 1,
                       "prev_event_hash": previous["event_hash"], "metadata": metadata,
                       "model": "fixture", "kind": "query", "seed": 1, "attempt": 0,
                       "estimated_tokens": 3, "reserved_prompt_tokens": 2,
                       "reserved_completion_tokens": 1, "prompt_tokens": None,
                       "completion_tokens": None, "cost_usd": 0.109876}
        reservation["event_hash"] = api_client._usage_event_hash(reservation)
        terminal = dict(reservation, status="unknown_charge", sequence=reservation["sequence"] + 1,
                        prev_event_hash=reservation["event_hash"])
        terminal["event_hash"] = api_client._usage_event_hash(terminal)
        append_event(run, reservation)
        append_event(run, terminal)
        previous = terminal
    write_json(api_client.usage_ledger_state_path(ledger),
               api_client._usage_state_payload(identity, previous["sequence"], previous["event_hash"]))
    journal = RequestJournal(run.paths["request_journal"], execution_identity="fixture-run")
    key = CallKey("cell-1", "judge_query", 0, 1)
    with journal.dispatch_guard():
        journal._begin_parallel_dispatch(key, "f" * 64)
    return journal, journal.parallel_marker_path(key)


def test_settled_unknown_marker_retirement_is_opt_in_and_never_recharges(stopped_run):
    from rejudge.request_journal import recover_interrupted_dispatches
    run = stopped_run
    journal, marker = _settled_unknown_run(run)
    original_marker = marker.read_bytes()
    original_ledger = Path(run.paths["usage_ledger"]).read_bytes()
    with pytest.raises(recovery.RecoveryError, match="unreserved marker"):
        build(run)
    run.kwargs["settled_unknown_marker_paths"] = [marker]
    _approve_ceiling_amendment(run)
    value = build(run)
    item, = value["interrupted_dispatches"]
    assert item["disposition"] == recovery.SETTLED_UNKNOWN_DISPOSITION
    assert len(item["settled_attempts"]) == 3
    assert "reservation_attempt_id" not in item
    result = recover_interrupted_dispatches(journal, run.paths["usage_ledger"], [item],
                                            recovery_manifest_sha256=canonical_sha256(value))
    assert result[0]["uncertain_cost_usd"] == 0
    assert not marker.exists()
    receipt = json.loads(Path(item["retirement_receipt_path"]).read_bytes())
    import base64
    assert base64.b64decode(receipt["marker_raw_base64"]) == original_marker
    assert Path(run.paths["usage_ledger"]).read_bytes() == original_ledger
    validate(run, value)
    recover_interrupted_dispatches(journal, run.paths["usage_ledger"], [item],
                                  recovery_manifest_sha256=canonical_sha256(value))
    assert Path(run.paths["usage_ledger"]).read_bytes() == original_ledger
    run.kwargs.pop("settled_unknown_marker_paths")
    later_recovery = build(run)
    assert later_recovery["retired_dispatch_receipts"] == [recovery._file_binding(item["retirement_receipt_path"])]
    Path(item["retirement_receipt_path"]).write_bytes(b"changed")
    with pytest.raises(recovery.RecoveryError, match="changed"):
        validate(run, later_recovery)


def test_settled_unknown_marker_cannot_disappear_without_immutable_retirement_receipt(stopped_run):
    _, marker = _settled_unknown_run(stopped_run)
    stopped_run.kwargs["settled_unknown_marker_paths"] = [marker]
    value = build(stopped_run)
    marker.unlink()
    with pytest.raises(recovery.RecoveryError, match="retirement receipt"):
        validate(stopped_run, value)


def test_settled_unknown_retirement_allows_later_success_without_rewriting_prior_proof(stopped_run):
    from rejudge.request_journal import CallKey, recover_interrupted_dispatches
    run = stopped_run
    journal, marker = _settled_unknown_run(run)
    run.kwargs["settled_unknown_marker_paths"] = [marker]
    value = build(run)
    item, = value["interrupted_dispatches"]
    recover_interrupted_dispatches(journal, run.paths["usage_ledger"], [item],
                                  recovery_manifest_sha256=canonical_sha256(value))
    last = item["settled_attempts"][-1]["terminal"]
    reservation = dict(item["settled_attempts"][-1]["reservation"], attempt_id="later-success",
                       sequence=last["sequence"] + 1, prev_event_hash=last["event_hash"])
    reservation["event_hash"] = api_client._usage_event_hash(reservation)
    terminal = dict(reservation, status="success", sequence=reservation["sequence"] + 1,
                    prev_event_hash=reservation["event_hash"], prompt_tokens=2,
                    completion_tokens=0, cost_usd=0.000004)
    terminal["event_hash"] = api_client._usage_event_hash(terminal)
    append_event(run, reservation)
    append_event(run, terminal)
    journal.put(CallKey("cell-1", "judge_query", 0, 1), "f" * 64, "")
    validate(run, value)
    receipt = Path(item["retirement_receipt_path"])
    saved = receipt.read_bytes()
    receipt.write_bytes(saved.replace(b'"new_uncertain_cost_usd": 0', b'"new_uncertain_cost_usd": 1'))
    with pytest.raises(recovery.RecoveryError, match="retirement receipt"):
        validate(run, value)


@pytest.mark.parametrize("namespace", [authorization.OWNER_SIGNATURE_NAMESPACE,
                                       recovery.RECOVERY_SIGNATURE_NAMESPACE])
def test_signature_verifier_uses_requested_namespace_for_allowed_signers_and_verification(
    tmp_path, monkeypatch, namespace,
):
    path = tmp_path / "authorization.json"
    write_json(path, {"test": True})
    Path(str(path) + ".sig").write_text("signature", encoding="utf-8")
    verifier = tmp_path / "ssh-keygen.exe"
    verifier.write_bytes(b"test verifier")
    monkeypatch.setattr(authorization.tempfile, "tempdir", str(tmp_path))
    def run(command, **kwargs):
        if "-lf" in command:
            return SimpleNamespace(returncode=0, stdout="256 SHA256:test key")
        assert command[command.index("-n") + 1] == namespace
        allowed = Path(command[command.index("-f") + 1]).read_text()
        assert f'namespaces="{namespace}"' in allowed
        return SimpleNamespace(returncode=0,
                               stdout=f'Good "{namespace}" signature for jack-maiorino'.encode())
    monkeypatch.setattr(authorization.subprocess, "run", run)
    assert authorization.load_authenticated_owner_authorization(
        path, public_key="ssh-ed25519 test", key_fingerprint="SHA256:test",
        ssh_keygen_path=verifier, signature_namespace=namespace) == {"test": True}
