import json

from rejudge import batch_replay


SOURCE = {
    "question_id": "CN-001", "transcript_index": 1, "budget": 2, "replicate": 0,
    "position_a_is_correct": True, "cell_key": "clean|CN-001|1|2|0",
    "exchanges": [
        {"extracted_claim": "the treaty was signed in Year 38", "normalized": "YES"},
        {"extracted_claim": "the river was dammed", "normalized": "NOT ADDRESSED"},
    ],
}


def _tr():
    rows = [json.loads(l) for l in open("data/transcripts.jsonl", encoding="utf-8")]
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
        def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict"):
            return "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: batch"

    rec = batch_replay.replay_one(SOURCE, _tr(), batch_replay.BATCH, p, Stub())
    assert rec["arm"] == "batch"
    assert rec["exchanges"][0]["replayed_from"] == "clean|CN-001|1|2|0"
    assert rec["queries_used"] == 2
    assert rec["verdict_correct_strict"] is True
