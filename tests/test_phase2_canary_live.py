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
