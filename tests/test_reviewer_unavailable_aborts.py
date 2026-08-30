"""An unreachable reviewer must abort the wave, not rule on it.

On 2026-08-07 the Codex quota ran out mid-wave. Every one of 329 calls returned exit=1 with
empty output, and each was committed as reviewer_error, which the frozen failure rule makes
non-ALLOW. So 329 oracle queries are permanently blocked by a reviewer that never read them,
in an append-only store.

The distinction the batch runner was missing:

- the reviewer RULED and the output was unusable (unparseable, or tool use detected) -> that
  is evidence about this payload, and committing it as non-ALLOW is the frozen failure rule
  working exactly as designed;
- the reviewer was never REACHED at all -> that is evidence about the reviewer, not the
  payload. Committing it writes a permanent verdict from nothing.

Fail-closed means refusing to proceed, not manufacturing a refusal for every payload in the
queue.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts import codex_reviewer_batch
from scripts.codex_reviewer_batch import ReviewerUnavailable, classify_result


def _meta(sha="a" * 64):
    return {"payload_sha256": sha}


def _event_stream(*events) -> bytes:
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode("utf-8")


def _clean_events(*items):
    return (
        {"type": "thread.started", "thread_id": "thread-test"},
        {"type": "turn.started"},
        *items,
        {"type": "turn.completed", "usage": {}},
    )


def test_an_unreachable_reviewer_raises_rather_than_ruling():
    with pytest.raises(ReviewerUnavailable):
        classify_result(_meta(), {"ok": False, "error": "exit=1, ruling_empty=True"},
                        packet_ok=True)


def test_a_quota_message_is_recognised_as_unreachable():
    with pytest.raises(ReviewerUnavailable):
        classify_result(_meta(), {"ok": False, "error": "You've hit your usage limit."},
                        packet_ok=True)


def test_tool_use_is_still_a_ruling_and_still_refused():
    """The reviewer WAS reached; its ruling is discarded as non-blind. That is a fact about
    the payload's review and must still be committed."""
    row = classify_result(_meta(), {"ok": True, "commands": ["ls"], "ruling": "x"},
                          packet_ok=True)
    assert row["status"] == "reviewer_error"
    assert "TOOL_USE_DETECTED" in row["raw_output"]


def test_packet_drift_is_still_committed():
    """Also a fact about this payload: its bytes no longer match the frozen prompt hash."""
    row = classify_result(_meta(), {"ok": True, "commands": [], "ruling": "x"},
                          packet_ok=False)
    assert row["status"] == "reviewer_error"
    assert "PACKET_DRIFT" in row["raw_output"]


def test_a_clean_ruling_passes_through():
    row = classify_result(
        _meta(), {"ok": True, "commands": [],
                  "ruling": "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine"},
        packet_ok=True)
    # A clean ruling carries no "status": the commit path derives it from the parsed output.
    assert "status" not in row and row["tool_uses"] == 0


def test_expired_authorization_blocks_before_reviewer_subprocess(tmp_path, monkeypatch):
    packet = tmp_path / "packet.txt"
    packet.write_text("review this", encoding="utf-8")
    monkeypatch.setattr(
        codex_reviewer_batch.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("expired authority reached reviewer subprocess"),
    )
    result = codex_reviewer_batch.run_one(
        packet,
        "reviewer-model",
        "high",
        "codex",
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert result["ok"] is False
    assert "authorization deadline expired" in result["error"]


def test_json_event_stream_accepts_only_complete_zero_tool_lifecycle():
    commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(*_clean_events(
            {"type": "item.completed", "item": {"type": "reasoning"}},
            {"type": "item.completed", "item": {"type": "agent_message"}},
        )))
    assert commands == []
    assert errors == []


def test_json_event_stream_reports_every_recognized_tool_item():
    commands, errors = codex_reviewer_batch._inspect_json_event_stream(
        _event_stream(*_clean_events(
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": "Get-Content world.txt"},
            },
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "Get-Content world.txt"},
            },
            {"type": "item.completed", "item": {"type": "web_search"}},
        )))
    assert commands == ["Get-Content world.txt", "web_search"]
    assert errors == []


@pytest.mark.parametrize(
    "stream, match",
    [
        (b"not-json\n", "not unique-key JSON"),
        (
            _event_stream(*_clean_events({"type": "future.event"})),
            "unknown event type",
        ),
        (
            _event_stream(*_clean_events({
                "type": "item.completed",
                "item": {"type": "future_item"},
            })),
            "unknown item type",
        ),
        (
            _event_stream(
                {"type": "thread.started", "thread_id": "thread-test"},
                {"type": "turn.started"},
                {"type": "turn.failed", "error": {"message": "failure"}},
            ),
            "turn.failed",
        ),
    ],
)
def test_json_event_stream_rejects_unknown_malformed_or_failed_shapes(stream, match):
    _commands, errors = codex_reviewer_batch._inspect_json_event_stream(stream)
    assert any(match in error for error in errors)


def test_run_one_refuses_a_ruling_with_an_unrecognized_json_event(
    tmp_path, monkeypatch,
):
    packet = tmp_path / "packet.txt"
    packet.write_text("review this", encoding="utf-8")

    def fake_run(command, **_kwargs):
        out_file = command[command.index("-o") + 1]
        with open(out_file, "w", encoding="utf-8") as handle:
            handle.write("LABEL: ACCEPT\nCLAUSE: Allowed\nRATIONALE: valid\n")
        return SimpleNamespace(
            returncode=0,
            stdout=_event_stream(*_clean_events({"type": "future.event"})),
        )

    monkeypatch.setattr(codex_reviewer_batch.subprocess, "run", fake_run)
    result = codex_reviewer_batch.run_one(
        packet, "reviewer-model", "high", "codex")
    assert result["ok"] is False
    assert "unknown event type" in result["error"]
