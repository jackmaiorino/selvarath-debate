"""The live canary driver: authorization cross-checks, path mapping, the Claude reviewer.

Offline throughout: the reviewer is exercised through an injected transport, refusal paths
trigger before any client construction, and the real tracked artifacts are only read, never
executed. The runner's own behaviour is covered by test_phase2_canary_runner.py.
"""
import json
import os
import urllib.error
from pathlib import Path

import pytest

from rejudge.phase2_canary_live import (
    CanaryLiveError, ClaudeReviewer, load_frozen_reviewer_prompt, local_path,
    render_reviewer_payload, run_live, validate_canary_authorization)

MANIFEST_PATH = Path("rejudge/phase2_canary_manifest_2026-07-28.json")
AUTHORIZATION_PATH = Path("rejudge/phase2_canary_authorization_2026-07-28.json")


def _live_pair(tmp_path):
    """A manifest and matching authorization written to tmp, valid against the real root.

    These used to be the tracked 2026-07-28 files. Two supersessions later that pair was
    stale, and it kept passing only because neither change touched a validated section --
    until the transport amendment moved a key inside frozen_inputs and it failed as drift.
    A test about run_live's refusal ORDER should not also be a test of which manifest
    happens to be current, so it builds its own valid pair instead.
    """
    from rejudge.phase2_canary_manifest import build_canary_manifest
    from rejudge.phase2_execution import canonical_sha256
    manifest = build_canary_manifest(
        project_root=".", recorded_at_utc="2026-08-01T00:00:00Z",
        archive_dir="E:/selvarath-archive/canary-2026-07-28")
    basis = "rejudge/phase2_canary_transport_amendment_2026-08-01.json"
    authorization = {
        "stage": manifest["stage"],
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "stage_cap_usd": manifest["caps"]["stage_cap_usd"],
        "cumulative_cap_usd": manifest["caps"]["cumulative_cap_usd"],
        "approver": "test fixture, not an owner authorization",
        "approved_at_utc": "2026-08-01T00:00:00Z",
        "recorded_at_utc": "2026-08-01T00:00:00Z",
        "approval_basis_tracked_path": basis,
        "approval_basis_sha256": canonical_sha256(
            json.loads(Path(basis).read_text(encoding="utf-8"))),
    }
    mp = tmp_path / "manifest.json"
    ap = tmp_path / "authorization.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    ap.write_text(json.dumps(authorization), encoding="utf-8")
    return mp, ap


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
        run_live(MANIFEST_PATH, None)  # ty: ignore[invalid-argument-type]


def test_run_live_refuses_without_the_reviewer_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    manifest_path, authorization_path = _live_pair(tmp_path)
    # The stub client proves refusal happens before any client or archive path is touched.
    with pytest.raises(CanaryLiveError, match="ANTHROPIC_API_KEY"):
        run_live(manifest_path, authorization_path, client=object())


# --- path mapping ---------------------------------------------------------------------------


# The archive lock is fcntl and local_path translates drive paths only on a POSIX host, both
# by design (the run executes under WSL). These tests exercise that machinery and are
# meaningless on Windows, where the suite otherwise passes in full.
needs_posix = pytest.mark.skipif(os.name != "posix",
                                 reason="archive lock and WSL path mapping are POSIX-only")


@needs_posix
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
        raise urllib.error.HTTPError("url", 401, "unauthorized", None, None)  # ty: ignore[invalid-argument-type]

    with pytest.raises(urllib.error.HTTPError):
        _reviewer(transport)("q", "a", "b")
    assert len(attempts) == 1


def test_a_missing_api_key_is_refused_at_construction():
    with pytest.raises(CanaryLiveError, match="API key"):
        ClaudeReviewer(prompt="P", model="m", api_key="")


# --- the substituted GPT reviewer ---------------------------------------------------------

def _gpt(transport, **kw):
    from rejudge.phase2_canary_live import GPTReviewer
    return GPTReviewer(prompt="PROMPT", model="test-model", api_key="k",
                       transport=transport, sleep=lambda s: None, **kw)


def test_the_gpt_reviewer_sends_only_the_frozen_prompt_payload_and_pinned_effort():
    bodies = []

    def transport(body):
        bodies.append(body)
        return {"output_text": "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine."}

    assert _gpt(transport)("q?", "a", "b").startswith("LABEL: ALLOW")
    (body,) = bodies
    assert set(body) == {"model", "reasoning", "input"}
    assert body["reasoning"] == {"effort": "high"}
    assert body["input"][0] == {"role": "developer", "content": "PROMPT"}
    assert body["input"][1] == {
        "role": "user", "content": "QUERY: q?\nCANDIDATE A: a\nCANDIDATE B: b"}


def test_the_gpt_reviewer_extracts_text_from_the_structured_shape():
    from rejudge.phase2_canary_live import GPTReviewer
    data = {"output": [{"type": "reasoning", "content": [{"text": "ignore me"}]},
                       {"type": "message", "content": [{"text": "LABEL: REJECT"}]}]}
    assert GPTReviewer.extract_text(data) == "LABEL: REJECT"


def test_the_gpt_reviewer_refuses_a_response_with_no_text():
    from rejudge.phase2_canary_live import GPTReviewer
    with pytest.raises(CanaryLiveError, match="no assistant text"):
        GPTReviewer.extract_text({"output": [{"type": "reasoning", "content": []}]})


def test_the_gpt_reviewer_requires_an_explicit_model():
    from rejudge.phase2_canary_live import GPTReviewer
    with pytest.raises(CanaryLiveError, match="explicit model identifier"):
        GPTReviewer(prompt="P", model="", api_key="k")


def test_the_gpt_reviewer_fails_closed_after_retries():
    def transport(body):
        raise TimeoutError("slow")
    with pytest.raises(CanaryLiveError, match="reviewer unavailable"):
        _gpt(transport)("q", "a", "b")


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
    worklist = {"items": [{"payload_sha256": good_sha, "subagent_prompt_sha256": "p1"},
                          {"payload_sha256": bad_sha, "subagent_prompt_sha256": "p2"}]}
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    counts = commit_decisions_into(store, worklist, [
        {"payload_sha256": good_sha, "prompt_sha256": "p1",
         "raw_output": "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine."},
        {"payload_sha256": bad_sha, "prompt_sha256": "p2",
         "raw_output": "I think probably yes?"},
    ])
    assert counts == {"parsed": 1, "malformed": 1, "reviewer_error": 0}
    good = store.get(good_sha)
    assert good is not None and good.effective_allow
    resolved = store.get(bad_sha)
    assert resolved is not None
    assert resolved.status == "malformed" and not resolved.effective_allow


def test_contract_violating_rulings_commit_as_malformed_without_aborting_the_wave(tmp_path):
    # The 2026-08-09/10/11 contract-gap incidents: LABEL REJECT paired with CLAUSE Allowed
    # because the frozen clause taxonomy (P1-P4) has no entry for an empty query (asserts
    # nothing) or an open question (requests information rather than asserting a claim).
    # Before the fix, parse_reviewer_output called this combination 'parsed', and the store's
    # field validation then raised ValueError on commit, aborting the rest of the wave -- one
    # occurrence left 158 of 216 rulings committed and the other 58 undone. commit_decisions_
    # into must now get all the way through a wave containing both observed shapes, committing
    # each as malformed, while a normal ALLOW in the same wave is unaffected.
    from rejudge.phase2_canary_live import commit_decisions_into
    from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
    empty_query_sha = payload_hash("", "a", "b")
    open_question_sha = payload_hash("What is the toll rate?", "a", "b")
    good_sha = payload_hash("q3", "a", "b")
    worklist = {"items": [
        {"payload_sha256": empty_query_sha, "subagent_prompt_sha256": "p1"},
        {"payload_sha256": open_question_sha, "subagent_prompt_sha256": "p2"},
        {"payload_sha256": good_sha, "subagent_prompt_sha256": "p3"},
    ]}
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    counts = commit_decisions_into(store, worklist, [
        {"payload_sha256": empty_query_sha, "prompt_sha256": "p1",
         "raw_output": "LABEL: REJECT\nCLAUSE: Allowed\nRATIONALE: The empty query contains "
                       "no atomic factual claim checkable against the world document."},
        {"payload_sha256": open_question_sha, "prompt_sha256": "p2",
         "raw_output": "LABEL: REJECT\nCLAUSE: Allowed\nRATIONALE: The query asks the oracle "
                       "to supply a toll rate, an open question rather than an atomic claim."},
        {"payload_sha256": good_sha, "prompt_sha256": "p3",
         "raw_output": "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine."},
    ])
    assert counts == {"parsed": 1, "malformed": 2, "reviewer_error": 0}
    for sha in (empty_query_sha, open_question_sha):
        decision = store.get(sha)
        assert decision is not None
        assert decision.status == "malformed" and not decision.effective_allow
        assert decision.label is None and decision.clause is None
    good = store.get(good_sha)
    assert good is not None and good.effective_allow


def test_an_explicit_reviewer_error_commits_as_non_allow(tmp_path):
    from rejudge.phase2_canary_live import commit_decisions_into
    from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
    sha = payload_hash("q9", "a", "b")
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    counts = commit_decisions_into(store, {"items": [{"payload_sha256": sha}]}, [
        {"payload_sha256": sha, "status": "reviewer_error",
         "raw_output": "DISPATCH_ERROR: transport could not reproduce prompt bytes"}])
    assert counts["reviewer_error"] == 1
    decision = store.get(sha)
    assert decision is not None
    assert decision.status == "reviewer_error" and not decision.effective_allow


def test_a_ruling_without_prompt_proof_is_refused(tmp_path):
    from rejudge.phase2_canary_live import commit_decisions_into
    from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
    sha = payload_hash("q", "a", "b")
    store = DualGateDecisionStore(tmp_path / "d.jsonl")
    wl = {"items": [{"payload_sha256": sha, "subagent_prompt_sha256": "correct"}]}
    with pytest.raises(CanaryLiveError, match="no prompt_sha256"):
        commit_decisions_into(store, wl, [
            {"payload_sha256": sha, "raw_output": "LABEL: ALLOW\nCLAUSE: Allowed\n"
                                                  "RATIONALE: x."}])


def test_a_ruling_from_the_wrong_prompt_bytes_is_refused(tmp_path):
    from rejudge.phase2_canary_live import commit_decisions_into
    from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
    sha = payload_hash("q", "a", "b")
    store = DualGateDecisionStore(tmp_path / "d.jsonl")
    wl = {"items": [{"payload_sha256": sha, "subagent_prompt_sha256": "correct"}]}
    with pytest.raises(CanaryLiveError, match="wrong prompt bytes"):
        commit_decisions_into(store, wl, [
            {"payload_sha256": sha, "prompt_sha256": "tampered",
             "raw_output": "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: x."}])


def test_a_decision_for_an_unlisted_payload_is_refused(tmp_path):
    from rejudge.phase2_canary_live import commit_decisions_into
    from rejudge.phase2_dual_gate import DualGateDecisionStore
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    with pytest.raises(CanaryLiveError, match="not in the current worklist"):
        commit_decisions_into(store, {"items": []}, [
            {"payload_sha256": "f" * 64, "prompt_sha256": "p",
             "raw_output": "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: x."}])


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


def test_role_limit_wrapper_preserves_durable_marker_frontier(tmp_path, monkeypatch):
    from rejudge import api_client
    from rejudge.phase2_canary_live import RoleLimitResolvingClient
    from rejudge.request_journal import JournalingClient, RequestJournal

    ledger = tmp_path / "usage.jsonl"
    api_client.prepare_usage_ledger(ledger, allow_create=True)
    inner = api_client.RejudgeClient(
        approved_cap_usd=5, usage_log_path=ledger,
        _ledger_snapshot=api_client.load_chained_usage_ledger(ledger),
        _sdk_client=object())
    boundary = inner.journal_dispatch_boundary()
    before = ledger.read_bytes()

    def refuse(*args, **kwargs):
        raise api_client.UncertainCeilingHalt("reservation exceeds uncertainty cap")

    monkeypatch.setattr(inner, "complete", refuse)
    journal = RequestJournal(tmp_path / "journal.jsonl", execution_identity="frontier")
    client = JournalingClient(RoleLimitResolvingClient(inner, {}), journal)
    with pytest.raises(api_client.UncertainCeilingHalt):
        client.complete([], "model", 0, 7, 32, request_metadata={
            "cell_key": "cell", "call_role": "judge_query", "query_index": 0})
    marker = json.loads(journal.dispatch_marker_paths()[0].read_bytes())
    assert marker["ledger_boundary"] == boundary
    assert ledger.read_bytes() == before


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


# --- the pass loop exports without a live reviewer ------------------------------------------

def test_a_paused_pass_exports_the_worklist(tmp_path, monkeypatch):
    import rejudge.phase2_canary_live as live
    from rejudge.phase2_canary_runner import RunOutcome

    outcome = RunOutcome(paused=1, pending_payloads=[
        {"payload_sha256": "a" * 64, "query": "q?", "candidate_a": "a",
         "candidate_b": "b"}])
    monkeypatch.setattr(live, "run_canary", lambda **kwargs: outcome)
    manifest = _manifest()
    manifest["ledger"] = dict(manifest["ledger"])
    manifest["ledger"]["archive_dir"] = str(tmp_path)
    result = live._run_passes(
        manifest, client=object(), reviewer=live._PauseModeReviewer(),
        results_path=tmp_path / "r.jsonl", decisions_path=tmp_path / "d.jsonl",
        limit=None, max_passes=3, pause_when_unlabeled=True)
    assert result.needs_labelling
    exported = json.loads((tmp_path / live.WORKLIST_FILENAME).read_text(encoding="utf-8"))
    assert exported["items"][0]["payload_sha256"] == "a" * 64


# --- terminal halts (Consult 28 scope) -------------------------------------------------------

def test_no_terminal_halt_record_means_no_filter(tmp_path):
    from rejudge.phase2_canary_live import load_terminal_halt_cells
    assert load_terminal_halt_cells(tmp_path, tmp_path / "r.jsonl") == frozenset()


def test_the_real_record_loads_and_names_the_malformed_cell(tmp_path):
    from rejudge.phase2_canary_live import load_terminal_halt_cells
    # The list is designed to grow: the frozen checker's temp-0 nondeterminism produces a
    # malformed response roughly 0.3-0.6% of the time, and each one is unfinishable because
    # the cache memoises it. So the invariant is that every listed cell is present and
    # reasoned, not that there is exactly one; a second was appended on 2026-08-02.
    cells = load_terminal_halt_cells(".", tmp_path / "absent_results.jsonl")
    record = json.loads(Path(
        "rejudge/phase2_canary_terminal_halts_2026-07-29.json").read_text(encoding="utf-8"))
    assert cells == {str(entry["cell_key"]) for entry in record["cells"]}
    assert any(c.endswith("f67c1db73446400785cedb0e261d6ad3ecfdc8ed978f9d754f8f7650a9d12581")
               for c in cells)
    for entry in record["cells"]:
        assert entry["reason"] and entry["evidence"] and entry["reporting"]


def test_a_completed_terminal_cell_is_refused(tmp_path):
    from rejudge.phase2_canary_live import load_terminal_halt_cells
    results = tmp_path / "results.jsonl"
    cells = load_terminal_halt_cells(".", tmp_path / "absent.jsonl")
    cell = sorted(cells)[0]   # any listed cell; the refusal is per-cell, not per-list
    results.write_text(json.dumps({"cell_key": cell}) + "\n", encoding="utf-8")
    with pytest.raises(CanaryLiveError, match="stale"):
        load_terminal_halt_cells(".", results)


# --- the archive lock and incident migration ------------------------------------------------

@needs_posix
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


@needs_posix
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


@needs_posix
def test_the_rebuild_keeps_verified_rulings_and_drops_excluded(tmp_path):
    import shutil
    from rejudge.phase2_canary_live import INCIDENT2_RELATIVE_PATH, rebuild_decision_store
    from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
    root = tmp_path / "root"
    (root / INCIDENT2_RELATIVE_PATH).parent.mkdir(parents=True)
    shutil.copyfile(INCIDENT2_RELATIVE_PATH, root / INCIDENT2_RELATIVE_PATH)
    archive = tmp_path / "archive"; archive.mkdir()
    dpath = archive / "canary_reviewer_decisions.jsonl"

    keep, drop = payload_hash("q1", "a", "b"), payload_hash("q2", "a", "b")
    seed = DualGateDecisionStore(dpath)
    good = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: ok."
    seed.commit(keep, "ALLOW", "Allowed", "ok.", good, "parsed")
    seed.commit(drop, "ALLOW", "Allowed", "ok.", good, "parsed")

    verified = tmp_path / "verified.json"
    verified.write_text(json.dumps({
        "verified": [{"payload_sha256": keep, "label": "ALLOW", "clause": "Allowed",
                      "rationale": "ok.", "raw_output": good, "status": "parsed",
                      "prompt_sha256": "proof"}],
        "excluded": [{"payload_sha256": drop}]}), encoding="utf-8")

    manifest = {"ledger": {"archive_dir": str(archive), "decisions_path": str(dpath)}}
    out = rebuild_decision_store(manifest, project_root=root, verified_path=verified)
    assert out["rebuilt_rulings"] == 1
    assert Path(out["retired_to"]).exists()
    rebuilt = DualGateDecisionStore(dpath)
    assert rebuilt.get(keep) is not None and rebuilt.get(drop) is None
    with pytest.raises(CanaryLiveError, match="already ran"):
        rebuild_decision_store(manifest, project_root=root, verified_path=verified)


def test_the_rebuild_refuses_a_ruling_without_prompt_proof(tmp_path):
    import shutil
    from rejudge.phase2_canary_live import INCIDENT2_RELATIVE_PATH, rebuild_decision_store
    from rejudge.phase2_dual_gate import payload_hash
    root = tmp_path / "root"
    (root / INCIDENT2_RELATIVE_PATH).parent.mkdir(parents=True)
    shutil.copyfile(INCIDENT2_RELATIVE_PATH, root / INCIDENT2_RELATIVE_PATH)
    archive = tmp_path / "archive"; archive.mkdir()
    verified = tmp_path / "verified.json"
    verified.write_text(json.dumps({
        "verified": [{"payload_sha256": payload_hash("q", "a", "b"), "label": "ALLOW",
                      "clause": "Allowed", "rationale": "x", "raw_output": "r",
                      "status": "parsed"}], "excluded": []}), encoding="utf-8")
    manifest = {"ledger": {"archive_dir": str(archive),
                           "decisions_path": str(archive / "d.jsonl")}}
    with pytest.raises(CanaryLiveError, match="no prompt proof"):
        rebuild_decision_store(manifest, project_root=root, verified_path=verified)


def test_superseding_a_binding_records_the_prior_identity(tmp_path):
    import shutil
    from rejudge.phase2_canary_live import (LEDGER_BINDING_FILENAME, supersede_ledger_binding)
    root = tmp_path / "root"; (root / "rejudge").mkdir(parents=True)
    reason = "rejudge/reason.json"
    (root / reason).write_text("{}", encoding="utf-8")
    archive = tmp_path / "arch"; archive.mkdir()
    (archive / LEDGER_BINDING_FILENAME).write_text(json.dumps(
        {"schema_version": "phase2_canary_ledger_binding_v2",
         "execution_identity_sha256": "a" * 64, "ledger_identity": {"ledger_id": "x"}}),
        encoding="utf-8")
    manifest = {"execution_identity_sha256": "b" * 64,
                "ledger": {"archive_dir": str(archive)}}
    out = supersede_ledger_binding(manifest, project_root=root, reason_tracked_path=reason)
    assert out["previous"] == "a" * 64 and out["now"] == "b" * 64
    binding = json.loads((archive / LEDGER_BINDING_FILENAME).read_text(encoding="utf-8"))
    assert binding["superseded_identities"][0]["execution_identity_sha256"] == "a" * 64
    # idempotent
    assert supersede_ledger_binding(manifest, project_root=root,
                                    reason_tracked_path=reason)["unchanged"] is True


def test_supersession_refuses_without_the_justifying_record(tmp_path):
    from rejudge.phase2_canary_live import supersede_ledger_binding
    archive = tmp_path / "arch"; archive.mkdir()
    manifest = {"execution_identity_sha256": "b" * 64,
                "ledger": {"archive_dir": str(archive)}}
    with pytest.raises(CanaryLiveError, match="justifying record"):
        supersede_ledger_binding(manifest, project_root=tmp_path,
                                 reason_tracked_path="rejudge/missing.json")


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


def test_the_client_loads_the_role_limits_artifact_the_manifest_names(tmp_path):
    # The driver used to open rejudge/phase2_role_limits_v5_2026-07-19.json by constant while
    # the manifest bound that file's hash separately. The two could not disagree until the
    # 2026-08-01 transport amendment introduced a successor artifact; then a constant path
    # would have run v5's transport under an identity that claims v6's. Proving the manifest
    # drives the load needs no provider: a path the manifest names but disk lacks must fail
    # naming that path, before any client is constructed.
    from rejudge.phase2_canary_live import build_live_client
    manifest = {"frozen_inputs": {"role_limits_tracked_path": "rejudge/absent_pin.json"}}
    with pytest.raises(FileNotFoundError) as excinfo:
        build_live_client(manifest, project_root=tmp_path,
                          usage_log_path=tmp_path / "u.jsonl",
                          error_log_path=tmp_path / "e.jsonl",
                          call_cache_path=tmp_path / "c.jsonl")
    assert "absent_pin.json" in str(excinfo.value)


def test_the_bound_transport_is_the_one_the_amendment_describes():
    # End to end over the real artifacts: whatever the manifest resolves to must carry the
    # amended read timeout. A successor manifest that still pointed at v5 would pass every
    # hash check and quietly keep the ten-minute waits.
    from rejudge.phase2_canary_manifest import build_canary_manifest
    manifest = build_canary_manifest(project_root=".", recorded_at_utc="2026-08-01T00:00:00Z",
                                     archive_dir="E:/selvarath-archive/canary-2026-07-28")
    pinned = json.loads(Path(
        manifest["frozen_inputs"]["role_limits_tracked_path"]).read_text(encoding="utf-8"))
    assert pinned["request_settings"]["transport"]["http_timeout"]["read"] == 120


# --- incident 3: rebuilding the result store -------------------------------------------------

@needs_posix
def test_the_result_rebuild_drops_named_cells_and_keeps_the_rest(tmp_path):
    # Incident 3 left 141 cells recorded whose gate rulings were never actually reviewed.
    # The result store refuses to overwrite a cell, by design, so a contaminated cell cannot
    # be corrected in place: it has to be dropped so the runner will execute it again.
    from rejudge.phase2_canary_live import INCIDENT3_SUFFIX, rebuild_result_store
    from rejudge.phase2_canary_order import CellResultStore
    archive = tmp_path / "arch"
    archive.mkdir()
    results = archive / "results.jsonl"
    store = CellResultStore(results)
    for n in range(5):
        store.record(f"cell{n}", {"n": n})

    manifest = {"ledger": {"results_path": str(results), "archive_dir": str(archive)}}
    out = rebuild_result_store(manifest, project_root=".", drop_cells={"cell1", "cell3"})

    assert out["dropped"] == ["cell1", "cell3"] and out["kept"] == 3
    rebuilt = CellResultStore(results)          # re-read: proves the fresh chain loads
    assert [rebuilt.is_complete(f"cell{n}") for n in range(5)] == [
        True, False, True, False, True]
    assert rebuilt.get("cell4") == {"n": 4}     # surviving payloads are untouched
    retired = Path(str(results) + INCIDENT3_SUFFIX)
    assert retired.exists(), "the contaminated store must be preserved as evidence"
    assert len(retired.read_text(encoding="utf-8").strip().splitlines()) == 5


@needs_posix
def test_the_result_rebuild_refuses_to_run_twice(tmp_path):
    from rejudge.phase2_canary_live import CanaryLiveError, rebuild_result_store
    from rejudge.phase2_canary_order import CellResultStore
    archive = tmp_path / "arch"
    archive.mkdir()
    results = archive / "results.jsonl"
    CellResultStore(results).record("cell0", {})
    manifest = {"ledger": {"results_path": str(results), "archive_dir": str(archive)}}
    rebuild_result_store(manifest, project_root=".", drop_cells=set())
    with pytest.raises(CanaryLiveError, match="already"):
        rebuild_result_store(manifest, project_root=".", drop_cells=set())


def test_the_result_rebuild_refuses_without_the_incident_record(tmp_path):
    # Same precondition the decision-store rebuild enforces: no record, no rebuild.
    from rejudge.phase2_canary_live import CanaryLiveError, rebuild_result_store
    archive = tmp_path / "arch"
    archive.mkdir()
    manifest = {"ledger": {"results_path": str(archive / "r.jsonl"),
                           "archive_dir": str(archive)}}
    with pytest.raises(CanaryLiveError, match="incident record"):
        rebuild_result_store(manifest, project_root=tmp_path, drop_cells=set())


def test_the_result_rebuild_refuses_to_drop_a_cell_that_is_not_there(tmp_path):
    # A drop list naming an absent cell means the caller's idea of the contamination and the
    # store's contents disagree, which is exactly when not to rewrite the store.
    from rejudge.phase2_canary_live import CanaryLiveError, rebuild_result_store
    from rejudge.phase2_canary_order import CellResultStore
    archive = tmp_path / "arch"
    archive.mkdir()
    results = archive / "results.jsonl"
    CellResultStore(results).record("cell0", {})
    manifest = {"ledger": {"results_path": str(results), "archive_dir": str(archive)}}
    with pytest.raises(CanaryLiveError, match="not present"):
        rebuild_result_store(manifest, project_root=".", drop_cells={"ghost"})


@needs_posix
def test_the_unreviewed_rebuild_drops_only_self_identifying_rulings(tmp_path):
    # Incident 3's rule differs from incident 2's on purpose. Incident 2 contaminated the
    # PROMPTS, so affected rulings were indistinguishable without proof and "keep only what
    # is provable" was the only safe rule. Incident 3 broke the TRANSPORT: the reviewer never
    # ran, and every affected ruling carries an explicit marker. Applying incident 2's rule
    # here would have discarded 220 genuine rulings to remove 160 bad ones, because only 29
    # of the genuine rulings have recoverable prompt proof.
    from rejudge.phase2_canary_live import (
        INCIDENT3_SUFFIX, rebuild_decision_store_dropping_unreviewed)
    from rejudge.phase2_dual_gate import DualGateDecisionStore
    archive = tmp_path / "arch"
    archive.mkdir()
    decisions = archive / "decisions.jsonl"
    store = DualGateDecisionStore(decisions)
    store.commit("a" * 64, "ALLOW", "Allowed", "fine",
                 "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine", "parsed")
    store.commit("b" * 64, None, None, None,
                 "REVIEWER_UNAVAILABLE: exit=127, ruling_empty=True", "reviewer_error")
    store.commit("c" * 64, "REJECT", "P2", "restates",
                 "LABEL: REJECT\nCLAUSE: P2\nRATIONALE: restates", "parsed")
    store.commit("d" * 64, None, None, None,
                 "DISPATCH_ERROR: prompt contains U+202F characters", "reviewer_error")

    manifest = {"ledger": {"decisions_path": str(decisions), "archive_dir": str(archive)}}
    out = rebuild_decision_store_dropping_unreviewed(manifest, project_root=".")

    assert out["dropped"] == ["b" * 64] and out["kept"] == 3
    rebuilt = DualGateDecisionStore(decisions)
    assert rebuilt.get("b" * 64) is None, "the unreviewed rejection must be gone"
    # A genuine reviewer_error from a real cause is NOT swept up with it.
    assert rebuilt.get("d" * 64) is not None
    kept_a = rebuilt.get("a" * 64)
    kept_c = rebuilt.get("c" * 64)
    assert kept_a is not None and kept_a.label == "ALLOW"
    assert kept_c is not None and kept_c.label == "REJECT"
    assert Path(str(decisions) + INCIDENT3_SUFFIX).exists()


def test_the_unreviewed_rebuild_refuses_when_nothing_is_marked(tmp_path):
    # If no ruling self-identifies as unreviewed there is nothing to remediate, and
    # rewriting a hash-chained store for no reason is not a safe no-op.
    from rejudge.phase2_canary_live import (
        CanaryLiveError, rebuild_decision_store_dropping_unreviewed)
    from rejudge.phase2_dual_gate import DualGateDecisionStore
    archive = tmp_path / "arch"
    archive.mkdir()
    decisions = archive / "decisions.jsonl"
    DualGateDecisionStore(decisions).commit(
        "a" * 64, "ALLOW", "Allowed", "fine",
        "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine", "parsed")
    manifest = {"ledger": {"decisions_path": str(decisions), "archive_dir": str(archive)}}
    with pytest.raises(CanaryLiveError, match="nothing to drop"):
        rebuild_decision_store_dropping_unreviewed(manifest, project_root=".")


def test_the_unreviewed_rebuild_refuses_without_the_incident_record(tmp_path):
    from rejudge.phase2_canary_live import (
        CanaryLiveError, rebuild_decision_store_dropping_unreviewed)
    archive = tmp_path / "arch"
    archive.mkdir()
    manifest = {"ledger": {"decisions_path": str(archive / "d.jsonl"),
                           "archive_dir": str(archive)}}
    with pytest.raises(CanaryLiveError, match="incident record"):
        rebuild_decision_store_dropping_unreviewed(manifest, project_root=tmp_path)


@needs_posix
def test_the_cache_rebuild_drops_a_cells_calls_so_it_can_rerun(tmp_path):
    # The last piece of incident 3. Dropping a contaminated cell's result row makes the runner
    # execute it again, but its provider calls are still memoised against the OLD conversation.
    # With the gate rulings corrected, the judge's prompts differ, so the cache's own guard
    # fires CallReplayMismatch rather than replaying a response generated for a different
    # prompt. That guard is right; the fix is to drop those entries, not to weaken it.
    from rejudge.phase2_canary_live import INCIDENT3_SUFFIX, rebuild_call_cache
    from rejudge.phase2_call_cache import CallCache, CallKey
    archive = tmp_path / "arch"
    archive.mkdir()
    cache_path = archive / "cache.jsonl"
    cache = CallCache(cache_path)
    for cell in ("keep", "drop"):
        for role in ("judge_query", "query_checker"):
            cache.put(CallKey(cell_key=cell, call_role=role, slot=0, attempt=1),
                      f"fp-{cell}-{role}", f"response-{cell}-{role}")

    manifest = {"ledger": {"call_cache_path": str(cache_path), "archive_dir": str(archive)}}
    out = rebuild_call_cache(manifest, project_root=".", drop_cells={"drop"})

    assert out["dropped_rows"] == 2 and out["kept"] == 2
    rebuilt = CallCache(cache_path)
    assert rebuilt.get(CallKey(cell_key="keep", call_role="judge_query", slot=0, attempt=1),
                       "fp-keep-judge_query") == "response-keep-judge_query"
    assert rebuilt.get(CallKey(cell_key="drop", call_role="judge_query", slot=0, attempt=1),
                       "fp-drop-judge_query") is None
    assert Path(str(cache_path) + INCIDENT3_SUFFIX).exists()


def test_the_cache_rebuild_refuses_without_the_incident_record(tmp_path):
    from rejudge.phase2_canary_live import CanaryLiveError, rebuild_call_cache
    archive = tmp_path / "arch"
    archive.mkdir()
    manifest = {"ledger": {"call_cache_path": str(archive / "c.jsonl"),
                           "archive_dir": str(archive)}}
    with pytest.raises(CanaryLiveError, match="incident record"):
        rebuild_call_cache(manifest, project_root=tmp_path, drop_cells=set())
