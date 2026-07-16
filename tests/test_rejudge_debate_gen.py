import json

import pytest

from rejudge import config, debate_gen, judge_loop
from rejudge.api_client import CapExceededError, RejudgeClient

QUESTION = {
    "id": "SEL-002",
    "world": "selvarath",
    "question": "Does the Ossene Compact have a stronger legal claim?",
    "correct_answer": "CORRECT_TEXT_UNIQUE_MARKER: neither side has a clear legal basis.",
    "wrong_answer": "WRONG_TEXT_UNIQUE_MARKER: the Compact has a clear legal claim.",
    "facts_required": ["a", "b"],
    "reasoning": "some reasoning",
    "wrong_answer_defensibility": "OWN_STRATEGY_MARKER: lean on geography.",
}

WORLD_DOC = "WORLD DOCUMENT CONTENT ABOUT SELVARATH."


class ScriptedClient:
    """Returns scripted responses by kind; records every call. Mirrors the ScriptedClient
    pattern in tests/test_rejudge_judge_loop.py."""

    def __init__(self, script=None, dry_run=False):
        self.script = {k: (list(v) if isinstance(v, list) else v)
                       for k, v in (script or {}).items()}
        self.calls = []
        self.dry_run = dry_run
        self.total_tokens = 0

    def complete(self, messages, model, temperature, seed, max_tokens, kind="query", **kwargs):
        self.calls.append({"kind": kind, "messages": [dict(m) for m in messages],
                           "seed": seed, **kwargs})
        v = self.script.get(kind, "SHORT DRY RESPONSE")
        return v.pop(0) if isinstance(v, list) else v


def _words(n, marker="w"):
    return " ".join([marker] * n)


def _protocol():
    return config.load_protocol()


# ---------------------------------------------------------------------------
# Round-1 blindness
# ---------------------------------------------------------------------------

def test_round1_blindness_no_opponent_text():
    protocol = _protocol()
    for honest_first in (True, False):
        h_system, h_user = debate_gen.build_turn_prompt(
            round_idx=0, is_honest=True, question=QUESTION, world_document=WORLD_DOC,
            turns=[], honest_first=honest_first, protocol=protocol, cap_words=None)
        d_system, d_user = debate_gen.build_turn_prompt(
            round_idx=0, is_honest=False, question=QUESTION, world_document=WORLD_DOC,
            turns=[], honest_first=honest_first, protocol=protocol, cap_words=None)
        assert "WRONG_TEXT_UNIQUE_MARKER" not in h_user
        assert "WRONG_TEXT_UNIQUE_MARKER" not in h_system
        assert "CORRECT_TEXT_UNIQUE_MARKER" not in d_user
        assert "CORRECT_TEXT_UNIQUE_MARKER" not in d_system


def test_round1_ignores_any_turns_passed_in():
    # The pilot bug: a debater going "second" within round 1 saw the first speaker's
    # just-generated turn. Round 1 must be blind for BOTH slots regardless of what the
    # caller's `turns` accumulator holds.
    protocol = _protocol()
    fake_prior = [{"speaker": "dishonest", "text": "SECRET_OPPONENT_ARGUMENT_MARKER"}]
    _, honest_user = debate_gen.build_turn_prompt(
        round_idx=0, is_honest=True, question=QUESTION, world_document=WORLD_DOC,
        turns=fake_prior, honest_first=True, protocol=protocol, cap_words=None)
    assert "SECRET_OPPONENT_ARGUMENT_MARKER" not in honest_user
    assert "DEBATE HISTORY" not in honest_user


def test_round2_and_later_include_transcript_so_far():
    protocol = _protocol()
    turns = [{"speaker": "honest", "text": "OPENING_HONEST_MARKER"},
             {"speaker": "dishonest", "text": "OPENING_DISHONEST_MARKER"}]
    _, user = debate_gen.build_turn_prompt(
        round_idx=1, is_honest=True, question=QUESTION, world_document=WORLD_DOC,
        turns=turns, honest_first=True, protocol=protocol, cap_words=None)
    assert "OPENING_HONEST_MARKER" in user
    assert "OPENING_DISHONEST_MARKER" in user
    # by round 2 the opponent's assigned position is fair game again
    assert "WRONG_TEXT_UNIQUE_MARKER" in user


def test_strip_opponent_position_raises_on_drift():
    with pytest.raises(ValueError):
        debate_gen.strip_opponent_position("some unrelated template with no anchor", True)


# ---------------------------------------------------------------------------
# Counterbalance
# ---------------------------------------------------------------------------

def test_counterbalance_exact_half_and_deterministic():
    # The balancing property is independent of the private calibration selection.
    # Keep this unit test runnable in a clean clone where generated outputs are absent.
    qids = [f"Q{i:02d}" for i in range(24)]
    pairs = [(qid, t) for qid in qids for t in range(2)]
    assignment1 = debate_gen.counterbalance_assignment(pairs)
    assignment2 = debate_gen.counterbalance_assignment(pairs)
    assert assignment1 == assignment2
    assert len(assignment1) == 48
    assert sum(1 for v in assignment1.values() if v) == 24


# ---------------------------------------------------------------------------
# Word-cap validation
# ---------------------------------------------------------------------------

def test_word_cap_regenerates_twice_then_succeeds():
    client = ScriptedClient({"query": [_words(200), _words(200), _words(100)]})
    text, meta = debate_gen.generate_turn(client, model="m", temperature=0.7, seed=1,
                                          system="sys", user="usr", cap_words=150)
    assert text == _words(100)
    assert meta is not None
    assert meta["word_cap_violated"] is False
    assert meta["regen_attempts"] == 2
    assert len(meta["over_limit_attempts"]) == 2
    assert len(client.calls) == 3


def test_word_cap_all_attempts_over_flags_violation():
    client = ScriptedClient({"query": [_words(200), _words(200), _words(200)]})
    text, meta = debate_gen.generate_turn(client, model="m", temperature=0.7, seed=1,
                                          system="sys", user="usr", cap_words=150)
    assert text == _words(200)
    assert meta is not None
    assert meta["word_cap_violated"] is True
    assert meta["regen_attempts"] == 3
    assert len(client.calls) == 3


def test_uncapped_turn_has_no_regeneration():
    client = ScriptedClient({"query": [_words(500)]})
    text, meta = debate_gen.generate_turn(client, model="m", temperature=0.7, seed=1,
                                          system="sys", user="usr", cap_words=None)
    assert text == _words(500)
    assert meta is None
    assert len(client.calls) == 1


def test_over_limit_attempts_log_length_not_text():
    client = ScriptedClient({"query": [_words(200), _words(100)]})
    _, meta = debate_gen.generate_turn(client, model="m", temperature=0.7, seed=1,
                                       system="sys", user="usr", cap_words=150)
    assert meta is not None
    entry = meta["over_limit_attempts"][0]
    assert entry["length"] == len(_words(200))
    assert "text" not in entry


# ---------------------------------------------------------------------------
# Schema / judge_loop compatibility
# ---------------------------------------------------------------------------

def test_generated_record_schema_and_judge_loop_compat():
    protocol = _protocol()
    client = RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    rec = debate_gen.generate_transcript(QUESTION, WORLD_DOC, 0, True, protocol, client,
                                         debater_model="test-model", protocol_name="uncapped3")
    required = ["question_id", "transcript_index", "world", "question", "correct_answer",
               "wrong_answer", "honest_first", "debate_transcript", "debater_model", "protocol",
               "temperature", "seed", "harness_version", "created_at", "dry_run"]
    for f in required:
        assert f in rec, f
    assert rec["dry_run"] is True
    assert rec["protocol"] == "uncapped3"
    assert len(rec["debate_transcript"]) == 6
    for turn in rec["debate_transcript"]:
        assert turn["speaker"] in ("honest", "dishonest")
        assert isinstance(turn["text"], str)
        assert "round" in turn
        assert "word_cap_violated" not in turn
        assert "regen_attempts" not in turn

    pos_a, pos_b, text = judge_loop._format_transcript(rec, True)
    assert pos_a == rec["correct_answer"]
    assert pos_b == rec["wrong_answer"]
    assert "Turn 1" in text


def test_capped3_record_includes_cap_fields_on_every_turn():
    protocol = _protocol()
    client = RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    rec = debate_gen.generate_transcript(QUESTION, WORLD_DOC, 0, False, protocol, client,
                                         debater_model="test-model", protocol_name="capped3")
    assert rec["protocol"] == "capped3"
    for turn in rec["debate_transcript"]:
        assert turn["word_cap_violated"] is False   # dry-run canned text is always short
        assert turn["regen_attempts"] == 0


# ---------------------------------------------------------------------------
# CLI: resume, refusals, failure handling, cap
# ---------------------------------------------------------------------------

def _write_questions(tmp_path, ids):
    p = tmp_path / "qs.json"
    p.write_text(json.dumps(ids), encoding="utf-8")
    return p


def test_resume_adds_zero_lines(tmp_path):
    qfile = _write_questions(tmp_path, ["SEL-002", "CN-004"])
    out = tmp_path / "transcripts.jsonl"
    rc = debate_gen.main(["--questions", str(qfile), "--transcripts-per-question", "1",
                          "--protocols", "uncapped3", "--dry-run", "--workers", "1",
                          "--out", str(out)])
    assert rc == 0
    rows = out.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2

    rc2 = debate_gen.main(["--questions", str(qfile), "--transcripts-per-question", "1",
                           "--protocols", "uncapped3", "--dry-run", "--workers", "1",
                           "--out", str(out)])
    assert rc2 == 0
    rows2 = out.read_text(encoding="utf-8").splitlines()
    assert len(rows2) == len(rows)


def test_transcripts_per_question_zero_refused(tmp_path):
    qfile = _write_questions(tmp_path, [])
    rc = debate_gen.main(["--questions", str(qfile), "--transcripts-per-question", "0",
                          "--dry-run", "--out", str(tmp_path / "o.jsonl")])
    assert rc == 2


def test_unknown_protocol_refused(tmp_path):
    qfile = _write_questions(tmp_path, [])
    rc = debate_gen.main(["--questions", str(qfile), "--protocols", "bogus",
                          "--dry-run", "--out", str(tmp_path / "o.jsonl")])
    assert rc == 2


def test_live_requires_cap(tmp_path):
    qfile = _write_questions(tmp_path, [])
    rc = debate_gen.main(["--questions", str(qfile), "--out", str(tmp_path / "o.jsonl")])
    assert rc == 2


def test_transient_failure_logged_continues_and_returns_incomplete(
        tmp_path, monkeypatch, capsys):
    qfile = _write_questions(tmp_path, ["SEL-002"])
    calls = {"n": 0}

    def flaky(question, world_document, transcript_index, honest_first, protocol, client, *,
             debater_model, protocol_name):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"cell_key": f"{protocol_name}|{debater_model}|{question['id']}|"
                            f"{transcript_index}", "dry_run": True}

    monkeypatch.setattr(debate_gen, "generate_transcript", flaky)
    out = tmp_path / "out.jsonl"
    rc = debate_gen.main(["--questions", str(qfile), "--transcripts-per-question", "1",
                          "--protocols", "uncapped3,capped3", "--dry-run", "--workers", "1",
                          "--out", str(out)])
    assert rc == 1
    assert "completion check: 1 missing" in capsys.readouterr().out
    rows = out.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    failed = (tmp_path / "debate_gen_failed.jsonl").read_text(encoding="utf-8")
    assert "boom" in failed


def test_cap_exceeded_halts_run(tmp_path, monkeypatch):
    qfile = _write_questions(tmp_path, ["SEL-002", "CN-004"])

    def capped(*a, **k):
        raise CapExceededError("cap")

    monkeypatch.setattr(debate_gen, "generate_transcript", capped)
    rc = debate_gen.main(["--questions", str(qfile), "--transcripts-per-question", "1",
                          "--protocols", "uncapped3", "--dry-run", "--workers", "2",
                          "--out", str(tmp_path / "out.jsonl")])
    assert rc == 3


# ---------------------------------------------------------------------------
# CLI arg parsing helpers
# ---------------------------------------------------------------------------

def test_debater_model_repeatable_and_comma_separated():
    protocol = _protocol()
    default_model = protocol["protocol"]["models"]["debater"]
    assert debate_gen._parse_debater_models(None, protocol) == [default_model]
    assert debate_gen._parse_debater_models(["a,b", "c"], protocol) == ["a", "b", "c"]
    assert debate_gen._parse_debater_models(["a", "a"], protocol) == ["a"]


# ---------------------------------------------------------------------------
# Canary
# ---------------------------------------------------------------------------

class _FlakyCanaryStub:
    def __init__(self):
        self.calls = 0
        self.total_tokens = 0

    def complete(self, *a, **k):
        self.calls += 1
        if self.calls == 3:
            raise RuntimeError("endpoint boom")
        return "a short response"


def test_run_canary_checks_reports_per_prompt_failures():
    client = _FlakyCanaryStub()
    results = debate_gen.run_canary_checks(client, "model-x", _protocol())
    assert len(results) == 5
    assert results[2]["ok"] is False
    assert "endpoint boom" in results[2]["error"]
    assert all(r["ok"] for i, r in enumerate(results) if i != 2)
    assert all("latency_s" in r for r in results)
    assert results[0]["preview"] == "a short response"[:40]
