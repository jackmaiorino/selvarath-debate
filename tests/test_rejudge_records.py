from rejudge import records
from rejudge.config import ARMS
from rejudge.parsers import parse_both

TR = {"question_id": "CN-001", "transcript_index": 1, "world": "carath_norn"}


def _rec(verdict_text, pos_a=True):
    return records.build_record(
        transcript=TR, arm=ARMS["clean"], budget=2, replicate=0,
        position_a_is_correct=pos_a, exchanges=[], raw_verdict_text=verdict_text,
        parses=parse_both(verdict_text), judge_messages=[{"role": "user", "content": "x"}],
        seed=123, judge_model="j", oracle_model="o", dry_run=True, queries_used=0)


def test_provenance_fields_present():
    r = _rec("VERDICT: Position A\nCONFIDENCE: 4")
    for f in ["harness_version", "arm", "dry_run", "created_at", "parser_version",
              "seed", "judge_model", "oracle_model", "budget", "replicate", "cell_key"]:
        assert f in r, f
    assert r["dry_run"] is True
    assert r["cell_key"] == "clean|CN-001|1|2|0"


def test_verdict_correct_both_parses():
    r = _rec("VERDICT: Position A\nCONFIDENCE: 4", pos_a=True)
    assert r["verdict_correct_strict"] is True and r["verdict_correct_pilot"] is True
    r2 = _rec("total gibberish", pos_a=True)
    assert r2["verdict_correct_strict"] is None            # INVALID, never coerced
    assert r2["verdict_correct_pilot"] is False            # pilot coerces to B -> wrong


def test_raw_text_persisted():
    r = _rec("VERDICT: Position B")
    assert r["raw_verdict_text"] == "VERDICT: Position B"
    assert r["judge_messages"][0]["content"] == "x"
