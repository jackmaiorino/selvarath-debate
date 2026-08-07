"""The review daemon reading the decisions file the MANIFEST names.

It hardcoded the canary's filename. The main run's manifest names a different one, so the
daemon would have found no decisions at all, treated every payload as undecided, and
re-reviewed the entire worklist on every wave.
"""
import json

from scripts.review_daemon import undecided


def _archive(tmp_path, decisions_filename):
    (tmp_path / "reviewer_worklist.json").write_text(json.dumps({
        "frozen_prompt_sha256": "x" * 64, "separator": "\n---\n",
        "items": [{"payload_sha256": "a" * 64, "subagent_prompt": "p",
                   "subagent_prompt_sha256": "y" * 64},
                  {"payload_sha256": "b" * 64, "subagent_prompt": "q",
                   "subagent_prompt_sha256": "z" * 64}]}), encoding="utf-8")
    (tmp_path / decisions_filename).write_text(
        json.dumps({"payload_sha256": "a" * 64}) + "\n", encoding="utf-8")
    return tmp_path


def test_already_decided_payloads_are_skipped_for_a_canary_archive(tmp_path):
    archive = _archive(tmp_path, "canary_reviewer_decisions.jsonl")
    left = undecided(archive, decisions_filename="canary_reviewer_decisions.jsonl")
    assert [i["payload_sha256"] for i in left] == ["b" * 64]


def test_already_decided_payloads_are_skipped_for_a_main_archive(tmp_path):
    archive = _archive(tmp_path, "main_reviewer_decisions.jsonl")
    left = undecided(archive, decisions_filename="main_reviewer_decisions.jsonl")
    assert [i["payload_sha256"] for i in left] == ["b" * 64], (
        "the daemon must read the decisions file this run actually writes, or it re-reviews "
        "everything every wave"
    )
