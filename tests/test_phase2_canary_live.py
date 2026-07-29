"""The live canary driver: authorization cross-checks, path mapping, the Claude reviewer.

Offline throughout: the reviewer is exercised through an injected transport, refusal paths
trigger before any client construction, and the real tracked artifacts are only read, never
executed. The runner's own behaviour is covered by test_phase2_canary_runner.py.
"""
import json
import urllib.error
from pathlib import Path

import pytest

from rejudge.phase2_canary_live import (
    CanaryLiveError, ClaudeReviewer, load_frozen_reviewer_prompt, local_path,
    render_reviewer_payload, run_live, validate_canary_authorization)

MANIFEST_PATH = Path("rejudge/phase2_canary_manifest_2026-07-28.json")
AUTHORIZATION_PATH = Path("rejudge/phase2_canary_authorization_2026-07-28.json")


def _manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _authorization(**overrides):
    record = json.loads(AUTHORIZATION_PATH.read_text(encoding="utf-8"))
    record.update(overrides)
    return record


# --- the tracked records themselves ---------------------------------------------------------

def test_the_tracked_authorization_matches_the_tracked_manifest():
    validated = validate_canary_authorization(_authorization(), _manifest())
    assert validated["approver"] == "Jack Maiorino"
    assert validated["stage_cap_usd"] == 40.0
    assert validated["cumulative_cap_usd"] == 1709.24


def test_the_authorization_names_the_successor_identity_not_the_superseded_one():
    assert _authorization()["execution_identity_sha256"] == (
        "c8ca24380cb14479cff75abade68b0aedcb0dd9c5485096a7870089a98dd248f")


# --- refusal paths --------------------------------------------------------------------------

def test_a_wrong_identity_is_refused():
    with pytest.raises(CanaryLiveError, match="different execution identity"):
        validate_canary_authorization(
            _authorization(execution_identity_sha256="0" * 64), _manifest())


def test_a_wrong_stage_is_refused():
    with pytest.raises(CanaryLiveError, match="stage"):
        validate_canary_authorization(_authorization(stage="capability_preflight"),
                                      _manifest())


def test_a_wrong_stage_cap_is_refused():
    with pytest.raises(CanaryLiveError, match="stage cap"):
        validate_canary_authorization(_authorization(stage_cap_usd=41.0), _manifest())


def test_a_wrong_cumulative_cap_is_refused():
    with pytest.raises(CanaryLiveError, match="cumulative cap"):
        validate_canary_authorization(_authorization(cumulative_cap_usd=9999.0), _manifest())


def test_field_drift_is_refused():
    with pytest.raises(CanaryLiveError, match="fields drifted"):
        validate_canary_authorization(_authorization(surprise="value"), _manifest())


def test_a_tampered_approval_basis_is_refused(tmp_path):
    basis = json.loads(Path(
        "rejudge/phase2_canary_spend_confirmation_2026-07-28.json").read_text(
            encoding="utf-8"))
    basis["owner_statements_verbatim"] = ["forged"]
    target = tmp_path / "rejudge" / "phase2_canary_spend_confirmation_2026-07-28.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(basis), encoding="utf-8")
    with pytest.raises(CanaryLiveError, match="hash mismatch"):
        validate_canary_authorization(_authorization(), _manifest(), project_root=tmp_path)


def test_run_live_requires_an_authorization_path():
    with pytest.raises(CanaryLiveError, match="no bypass"):
        run_live(MANIFEST_PATH, None)


def test_run_live_refuses_without_the_reviewer_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # The stub client proves refusal happens before any client or archive path is touched.
    with pytest.raises(CanaryLiveError, match="ANTHROPIC_API_KEY"):
        run_live(MANIFEST_PATH, AUTHORIZATION_PATH, client=object())


# --- path mapping ---------------------------------------------------------------------------

def test_a_drive_path_maps_onto_the_wsl_mount():
    assert local_path("E:/selvarath-archive/canary-2026-07-28/canary_usage.jsonl") == Path(
        "/mnt/e/selvarath-archive/canary-2026-07-28/canary_usage.jsonl")


def test_a_posix_path_is_untouched():
    assert local_path("/tmp/somewhere/file.jsonl") == Path("/tmp/somewhere/file.jsonl")


# --- the frozen reviewer prompt -------------------------------------------------------------

def test_the_tracked_reviewer_prompt_loads_and_matches_the_manifest():
    frozen = load_frozen_reviewer_prompt(_manifest())
    assert frozen["model"] == "claude-fable-5"
    assert "blinded contract reviewer" in frozen["prompt"]


def test_a_tampered_reviewer_prompt_is_refused(tmp_path):
    manifest = _manifest()
    tracked = manifest["reviewer"]["prompt_tracked_path"]
    artifact = json.loads(Path(tracked).read_text(encoding="utf-8"))
    artifact["prompt"] += " "
    target = tmp_path / tracked
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(CanaryLiveError, match="drifted"):
        load_frozen_reviewer_prompt(manifest, project_root=tmp_path)


def test_the_payload_render_matches_the_frozen_payload_format():
    artifact = json.loads(Path(
        "rejudge/phase2_reviewer_prompt_2026-07-23.json").read_text(encoding="utf-8"))
    documented = artifact["payload_format"].replace("\\n", "\n")
    expected = (documented.replace("<raw_query>", "Q").replace("<candidate_a>", "A")
                .replace("<candidate_b>", "B"))
    assert render_reviewer_payload("Q", "A", "B") == expected


# --- the reviewer callable ------------------------------------------------------------------

def _reviewer(transport, **kwargs):
    return ClaudeReviewer(prompt="PROMPT", model="claude-fable-5", api_key="k",
                          transport=transport, sleep=lambda seconds: None, **kwargs)


def test_the_reviewer_sends_only_the_frozen_prompt_and_the_payload():
    bodies = []

    def transport(body):
        bodies.append(body)
        return {"content": [{"type": "text", "text": "LABEL: ALLOW\nCLAUSE: Allowed\n"
                                                     "RATIONALE: fine."}]}

    raw = _reviewer(transport)("q?", "a", "b")
    assert raw.startswith("LABEL: ALLOW")
    (body,) = bodies
    assert set(body) == {"model", "max_tokens", "temperature", "system", "messages"}
    assert body["model"] == "claude-fable-5"
    assert body["temperature"] == 0
    assert body["system"][0]["text"] == "PROMPT"
    assert body["messages"] == [
        {"role": "user", "content": "QUERY: q?\nCANDIDATE A: a\nCANDIDATE B: b"}]


def test_a_retryable_failure_is_retried_then_succeeds():
    attempts = []

    def transport(body):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.URLError("transient")
        return {"content": [{"type": "text", "text": "ok"}]}

    assert _reviewer(transport)("q", "a", "b") == "ok"
    assert len(attempts) == 3


def test_exhausted_retries_raise_so_the_gate_fails_closed():
    def transport(body):
        raise TimeoutError("slow")

    with pytest.raises(CanaryLiveError, match="reviewer unavailable"):
        _reviewer(transport)("q", "a", "b")


def test_a_non_retryable_http_error_raises_immediately():
    attempts = []

    def transport(body):
        attempts.append(1)
        raise urllib.error.HTTPError("url", 401, "unauthorized", None, None)

    with pytest.raises(urllib.error.HTTPError):
        _reviewer(transport)("q", "a", "b")
    assert len(attempts) == 1


def test_a_missing_api_key_is_refused_at_construction():
    with pytest.raises(CanaryLiveError, match="API key"):
        ClaudeReviewer(prompt="P", model="m", api_key="")


# --- the subagent-batch workflow ------------------------------------------------------------

def test_the_subagent_prompt_is_preamble_frozen_prompt_separator_payload():
    from rejudge.phase2_canary_live import (
        SUBAGENT_PAYLOAD_SEPARATOR, SUBAGENT_PREAMBLE, compose_subagent_prompt)
    prompt = compose_subagent_prompt("FROZEN", query="q?", candidate_a="a", candidate_b="b")
    assert prompt == (SUBAGENT_PREAMBLE + "\n\n" + "FROZEN" + SUBAGENT_PAYLOAD_SEPARATOR
                      + "QUERY: q?\nCANDIDATE A: a\nCANDIDATE B: b")
    # The preamble is content-neutral: output discipline and tool prohibition only.
    for banned in ("arm", "placebo", "judge", "checker", "outcome", "stage"):
        assert banned not in SUBAGENT_PREAMBLE.lower()


def test_the_pause_mode_reviewer_refuses_live_consultation():
    from rejudge.phase2_canary_live import _PauseModeReviewer
    with pytest.raises(CanaryLiveError, match="never consult a live reviewer"):
        _PauseModeReviewer()("q", "a", "b")


def test_the_worklist_export_binds_prompt_hashes(tmp_path):
    import hashlib
    from rejudge.phase2_canary_live import export_reviewer_worklist
    pending = [{"payload_sha256": "s" * 64, "query": "q?", "candidate_a": "a",
                "candidate_b": "b"}]
    path = tmp_path / "worklist.json"
    worklist = export_reviewer_worklist(pending, "FROZEN", path)
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == worklist
    (item,) = worklist["items"]
    assert item["subagent_prompt_sha256"] == hashlib.sha256(
        item["subagent_prompt"].encode("utf-8")).hexdigest()
    assert worklist["frozen_prompt_sha256"] == hashlib.sha256(b"FROZEN").hexdigest()


def test_out_of_band_decisions_commit_parsed_and_malformed(tmp_path):
    from rejudge.phase2_canary_live import commit_decisions_into
    from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
    good_sha = payload_hash("q1", "a", "b")
    bad_sha = payload_hash("q2", "a", "b")
    worklist = {"items": [{"payload_sha256": good_sha}, {"payload_sha256": bad_sha}]}
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    counts = commit_decisions_into(store, worklist, [
        {"payload_sha256": good_sha,
         "raw_output": "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine."},
        {"payload_sha256": bad_sha, "raw_output": "I think probably yes?"},
    ])
    assert counts == {"parsed": 1, "malformed": 1}
    assert store.get(good_sha).effective_allow
    resolved = store.get(bad_sha)
    assert resolved.status == "malformed" and not resolved.effective_allow


def test_a_decision_for_an_unlisted_payload_is_refused(tmp_path):
    from rejudge.phase2_canary_live import commit_decisions_into
    from rejudge.phase2_dual_gate import DualGateDecisionStore
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    with pytest.raises(CanaryLiveError, match="not in the current worklist"):
        commit_decisions_into(store, {"items": []}, [
            {"payload_sha256": "f" * 64, "raw_output": "LABEL: ALLOW\nCLAUSE: Allowed\n"
                                                       "RATIONALE: x."}])


# --- role-limit resolution ------------------------------------------------------------------

LIMITS = {"m/reasoning": {
    "debater_turn": {"base_role_max_tokens": 512, "effective_request_max_tokens": 4096},
    "oracle": {"base_role_max_tokens": 32, "effective_request_max_tokens": 32}}}


class _CapturingInner:
    def __init__(self):
        self.calls = []

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata=None):
        self.calls.append(max_tokens)
        return "ok"


def _resolving():
    from rejudge.phase2_canary_live import RoleLimitResolvingClient
    inner = _CapturingInner()
    return RoleLimitResolvingClient(inner, LIMITS), inner


def test_a_base_request_is_sent_at_the_frozen_effective_value():
    client, inner = _resolving()
    client.complete([], "m/reasoning", 0, 1, 512,
                    request_metadata={"call_role": "debater_turn"})
    assert inner.calls == [4096]


def test_an_already_effective_request_passes_unchanged():
    client, inner = _resolving()
    client.complete([], "m/reasoning", 0, 1, 4096,
                    request_metadata={"call_role": "debater_turn"})
    assert inner.calls == [4096]


def test_the_oracle_verification_alias_maps_to_the_oracle_role():
    client, inner = _resolving()
    client.complete([], "m/reasoning", 0, 1, 32,
                    request_metadata={"call_role": "oracle_verification"})
    assert inner.calls == [32]


def test_an_unanticipated_value_is_refused():
    client, _ = _resolving()
    with pytest.raises(CanaryLiveError, match="neither the frozen base"):
        client.complete([], "m/reasoning", 0, 1, 300,
                        request_metadata={"call_role": "debater_turn"})


def test_an_unlisted_role_is_refused():
    client, _ = _resolving()
    with pytest.raises(CanaryLiveError, match="not in the frozen role-limits"):
        client.complete([], "m/reasoning", 0, 1, 512,
                        request_metadata={"call_role": "mystery_role"})


def test_an_unlisted_model_passes_through():
    client, inner = _resolving()
    client.complete([], "other/model", 0, 1, 77, request_metadata={"call_role": "anything"})
    assert inner.calls == [77]


# --- the recorded streaming deviation -------------------------------------------------------

def test_the_deviation_unpins_exactly_gpt_oss_when_the_record_exists():
    from rejudge.phase2_canary_live import _apply_streaming_deviation
    pinned = frozenset({"openai/gpt-oss-120b", "google/gemma-4-31B-it"})
    result = _apply_streaming_deviation(pinned, Path("."))
    assert result == frozenset({"google/gemma-4-31B-it"})


def test_no_record_means_no_override(tmp_path):
    from rejudge.phase2_canary_live import _apply_streaming_deviation
    pinned = frozenset({"openai/gpt-oss-120b"})
    assert _apply_streaming_deviation(pinned, tmp_path) == pinned


def test_a_record_without_a_matching_pin_is_refused(tmp_path):
    from rejudge.phase2_canary_live import (
        STREAMING_DEVIATION_RELATIVE_PATH, _apply_streaming_deviation)
    target = tmp_path / STREAMING_DEVIATION_RELATIVE_PATH
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(
        {"schema_version": "phase2_canary_streaming_deviation_v1"}), encoding="utf-8")
    with pytest.raises(CanaryLiveError, match="drifted apart"):
        _apply_streaming_deviation(frozenset({"google/gemma-4-31B-it"}), tmp_path)


# --- the archive lock and incident migration ------------------------------------------------

def test_the_archive_lock_refuses_a_second_holder(tmp_path):
    from rejudge.phase2_canary_live import AnotherProcessHoldsTheLock, _ArchiveLock
    lock_path = tmp_path / "canary.lock"
    with _ArchiveLock(lock_path, "canary"):
        with pytest.raises(AnotherProcessHoldsTheLock, match="refusing to run concurrently"):
            _ArchiveLock(lock_path, "canary").__enter__()
    with _ArchiveLock(lock_path, "canary"):
        pass


def test_carried_forward_defaults_to_zero_for_v1_bindings():
    from rejudge.phase2_canary_live import carried_forward_spend
    assert carried_forward_spend({}) == (0.0, 0.0)
    assert carried_forward_spend({"carried_forward": {
        "actual_spend_usd": 2.354, "uncertain_spend_usd": 0.171}}) == (2.354, 0.171)


def test_conservative_ledger_spend_counts_success_and_unresolved(tmp_path):
    from rejudge.phase2_canary_live import conservative_ledger_spend
    ledger = tmp_path / "usage.jsonl"
    rows = [
        {"status": "ledger_genesis"},
        {"status": "reserved", "attempt_id": "a", "cost_usd": 0.5},
        {"status": "success", "attempt_id": "a", "cost_usd": 0.4},
        {"status": "reserved", "attempt_id": "b", "cost_usd": 0.2},
        {"status": "unknown_charge", "attempt_id": "b", "cost_usd": 0.2},
        {"status": "reserved", "attempt_id": "c", "cost_usd": 0.1},
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    actual, uncertain = conservative_ledger_spend(ledger)
    assert actual == pytest.approx(0.4)
    assert uncertain == pytest.approx(0.3)


def test_the_migration_preserves_rebuilds_and_carries_forward(tmp_path, monkeypatch):
    import shutil
    from rejudge import api_client
    from rejudge.phase2_call_cache import CallCache, CallKey
    from rejudge.phase2_canary_live import (
        INCIDENT1_RELATIVE_PATH, LEDGER_BINDING_FILENAME, migrate_interleaved_ledger)

    root = tmp_path / "root"
    (root / INCIDENT1_RELATIVE_PATH).parent.mkdir(parents=True)
    shutil.copyfile(INCIDENT1_RELATIVE_PATH, root / INCIDENT1_RELATIVE_PATH)
    archive = tmp_path / "archive"
    archive.mkdir()

    usage = archive / "canary_usage.jsonl"
    identity = api_client.prepare_usage_ledger(usage, allow_create=True)
    genesis = json.loads(usage.read_text(encoding="utf-8").splitlines()[0])
    extra = [
        {"status": "reserved", "attempt_id": "x", "cost_usd": 1.5,
         "prev_event_hash": genesis["event_hash"], "event_hash": "broken1", "sequence": 1},
        {"status": "success", "attempt_id": "x", "cost_usd": 1.25,
         "prev_event_hash": "not-the-previous", "event_hash": "broken2", "sequence": 2},
    ]
    with usage.open("a", encoding="utf-8") as fh:
        for row in extra:
            fh.write(json.dumps(row) + "\n")

    cache_path = archive / "canary_call_cache.jsonl"
    cache = CallCache(cache_path)
    cache.put(CallKey(cell_key="cell1", call_role="oracle", slot=0, attempt=1), "f" * 64, "YES")
    cache.put(CallKey(cell_key="cell2", call_role="oracle", slot=0, attempt=1), "e" * 64, "NO")

    manifest = {"execution_identity_sha256": "e" * 64,
                "ledger": {"archive_dir": str(archive),
                           "usage_log_path": str(usage),
                           "call_cache_path": str(cache_path)}}
    summary = migrate_interleaved_ledger(manifest, project_root=root)

    assert summary["cache_rows_rebuilt"] == 2
    assert summary["carried_forward"]["actual_spend_usd"] == pytest.approx(1.25)
    preserved = Path(str(usage) + ".incident1-2026-07-28")
    assert preserved.exists()
    binding = json.loads((archive / LEDGER_BINDING_FILENAME).read_text(encoding="utf-8"))
    assert binding["schema_version"] == "phase2_canary_ledger_binding_v2"
    fresh = api_client.load_chained_usage_ledger(usage)
    assert fresh.summary["actual_spend_usd"] == 0.0
    rebuilt = CallCache(cache_path)
    assert rebuilt.get(CallKey(cell_key="cell1", call_role="oracle", slot=0, attempt=1),
                       "f" * 64) == "YES"
    with pytest.raises(Exception, match="already exists; migration already ran"):
        migrate_interleaved_ledger(manifest, project_root=root)


# --- ledger binding -------------------------------------------------------------------------

def test_the_first_run_binds_the_ledger_and_a_resume_verifies_it(tmp_path):
    from rejudge.phase2_canary_live import bind_or_verify_ledger
    manifest = {"execution_identity_sha256": "e" * 64}
    ledger = tmp_path / "usage.jsonl"
    binding = tmp_path / "binding.json"
    first = bind_or_verify_ledger(manifest, ledger, binding)
    assert binding.exists()
    resumed = bind_or_verify_ledger(manifest, ledger, binding)
    assert resumed == first


def test_a_binding_for_a_different_manifest_is_refused(tmp_path):
    from rejudge.phase2_canary_live import bind_or_verify_ledger
    ledger = tmp_path / "usage.jsonl"
    binding = tmp_path / "binding.json"
    bind_or_verify_ledger({"execution_identity_sha256": "e" * 64}, ledger, binding)
    with pytest.raises(CanaryLiveError, match="different execution identity"):
        bind_or_verify_ledger({"execution_identity_sha256": "f" * 64}, ledger, binding)


def test_a_paid_unbound_ledger_is_never_adopted_silently(tmp_path):
    from rejudge.api_client import UsageLedgerError
    from rejudge.phase2_canary_live import bind_or_verify_ledger
    from rejudge import api_client
    ledger = tmp_path / "usage.jsonl"
    identity = api_client.prepare_usage_ledger(ledger, allow_create=True)
    # Simulate a paid event beyond genesis, then a binding-less restart.
    import json as _json
    events = [_json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    event = {"status": "reserved", "attempt_id": "a1", "cost_usd": 0.01,
             "schema_version": events[0]["schema_version"], "ledger_id": identity["ledger_id"],
             "sequence": 1, "prev_event_hash": events[0]["event_hash"],
             "ts": events[0]["ts"], "model": "m", "kind": "verdict", "seed": 1,
             "attempt": 0, "estimated_tokens": 10, "metadata": {}}
    event["event_hash"] = api_client._usage_event_hash(event)
    with ledger.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(_json.dumps(event, sort_keys=True) + "\n")
    with pytest.raises(UsageLedgerError, match="without a bound run manifest"):
        bind_or_verify_ledger({"execution_identity_sha256": "e" * 64}, ledger,
                              tmp_path / "missing_binding.json")
