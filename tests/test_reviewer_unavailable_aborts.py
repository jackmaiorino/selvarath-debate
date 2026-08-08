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
import pytest

from scripts.codex_reviewer_batch import ReviewerUnavailable, classify_result


def _meta(sha="a" * 64):
    return {"payload_sha256": sha}


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
