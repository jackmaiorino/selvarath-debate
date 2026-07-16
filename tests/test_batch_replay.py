import json
from pathlib import Path

import pytest

from rejudge import batch_replay


TRANSCRIPTS_PATH = Path("data/transcripts.jsonl")
MISSING_TRANSCRIPTS_REASON = (
    "requires local research corpus data/transcripts.jsonl (not included in clean clones)"
)


SOURCE = {
    "question_id": "CN-001", "transcript_index": 1, "budget": 2, "replicate": 0,
    "position_a_is_correct": True, "cell_key": "clean|CN-001|1|2|0",
    "exchanges": [
        {"extracted_claim": "the treaty was signed in Year 38", "normalized": "YES"},
        {"extracted_claim": "the river was dammed", "normalized": "NOT ADDRESSED"},
    ],
}


def _tr():
    if not TRANSCRIPTS_PATH.is_file():
        pytest.skip(MISSING_TRANSCRIPTS_REASON)
    rows = [json.loads(l) for l in TRANSCRIPTS_PATH.open(encoding="utf-8")]
    return rows[0]


def test_fresh_context_has_no_query_turns():
    p = batch_replay.load_protocol()
    msgs = batch_replay.build_messages(SOURCE, _tr(), p)
    assert len(msgs) == 3
    assert [m["role"] for m in msgs] == ["system", "user", "user"]
    # the evidence is present as a table, the judge's raw phrasing machinery is not
    assert "the treaty was signed in Year 38" in msgs[2]["content"]
    assert "NOT ADDRESSED" in msgs[2]["content"]
    assert "remaining" not in msgs[2]["content"].lower()   # no query-phase scaffolding


def test_shuffle_is_deterministic_and_reorders_only():
    p = batch_replay.load_protocol()
    a = batch_replay.build_messages(SOURCE, _tr(), p, shuffle_seed=42)
    b = batch_replay.build_messages(SOURCE, _tr(), p, shuffle_seed=42)
    assert a == b
    # same claims present regardless of order
    for claim in ("treaty was signed", "river was dammed"):
        assert claim in a[2]["content"]


def test_replay_record_provenance():
    p = batch_replay.load_protocol()

    class Stub:
        dry_run = True
        def complete(self, messages, model, temperature, seed, max_tokens,
                     kind="verdict", **kwargs):
            return "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: batch"

    rec = batch_replay.replay_one(SOURCE, _tr(), batch_replay.BATCH, p, Stub())
    assert rec["arm"] == "batch"
    assert rec["exchanges"][0]["replayed_from"] == "clean|CN-001|1|2|0"
    assert rec["queries_used"] == 2
    assert rec["verdict_correct_strict"] is True


def test_transient_failure_returns_incomplete(tmp_path, monkeypatch, capsys):
    # This completion-path test is intentionally synthetic so it still runs in clean clones.
    transcript = {"question_id": "CN-001", "transcript_index": 1}
    source = {**SOURCE, "arm": "clean"}
    real_open = open

    def fixture_open(path, *args, **kwargs):
        normalized = Path(path).as_posix()
        if normalized.endswith("data/transcripts.jsonl"):
            from io import StringIO
            return StringIO(json.dumps(transcript) + "\n")
        if normalized.endswith("rejudge/output/records.jsonl"):
            from io import StringIO
            return StringIO(json.dumps(source) + "\n")
        return real_open(path, *args, **kwargs)

    def fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(batch_replay, "open", fixture_open, raising=False)
    monkeypatch.setattr(batch_replay, "capture_source_hashes", lambda files: {})
    monkeypatch.setattr(
        batch_replay, "require_unchanged_source_snapshot", lambda before, files: {})
    monkeypatch.setattr(
        batch_replay, "ensure_run_manifest",
        lambda *args, **kwargs: {"identity": {"source_files": {}}})
    monkeypatch.setattr(
        batch_replay, "require_manifest_source_snapshot", lambda *args: None)
    monkeypatch.setattr(batch_replay, "replay_one", fail)
    rc = batch_replay.main(["--dry-run", "--workers", "1", "--limit", "1",
                            "--out", str(tmp_path / "batch.jsonl")])

    assert rc == 1
    assert "completion check: 2 missing" in capsys.readouterr().out
    assert "boom" in (tmp_path / "batch_failed.jsonl").read_text(encoding="utf-8")
