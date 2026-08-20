import json
from pathlib import Path

import pytest

from rejudge import config, judge_loop


TRANSCRIPTS_PATH = Path("data/transcripts.jsonl")
MISSING_TRANSCRIPTS_REASON = (
    "requires local research corpus data/transcripts.jsonl (not included in clean clones)"
)


class ScriptedClient:
    """Returns scripted responses by kind; records every call."""

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", **kwargs):
        self.calls.append({"kind": kind, "messages": [dict(m) for m in messages], **kwargs})
        v = self.script[kind]
        return v.pop(0) if isinstance(v, list) else v


def _tr():
    if not TRANSCRIPTS_PATH.is_file():
        pytest.skip(MISSING_TRANSCRIPTS_REASON)
    rows = [json.loads(l) for l in TRANSCRIPTS_PATH.open(encoding="utf-8")]
    return rows[0]


def _protocol():
    return config.load_protocol()


JUDGE_Q = "Is it stated in the text that the treaty was signed in Year 38?"


def test_both_arm_reproduces_doubling_and_na_to_no():
    client = ScriptedClient({"query": [JUDGE_Q], "oracle": "NOT ADDRESSED",
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok"})
    rec = judge_loop.run_judgment(_tr(), "WORLD DOC", config.ARMS["both"], 1, 0,
                                  client, _protocol())
    ex = rec["exchanges"][0]
    assert "Is it supported by the text that Is it stated in the text that" in ex["oracle_prompt"]
    assert ex["raw_oracle_reply"] == "NOT ADDRESSED"
    assert ex["normalized"] == "NO"                     # the NA->NO bug, replayed
    oracle_call = [c for c in client.calls if c["kind"] == "oracle"][0]
    assert ex["oracle_prompt"] == oracle_call["messages"][-1]["content"]  # literal text logged


def test_clean_arm_single_wrap_and_na_preserved():
    client = ScriptedClient({"query": ["CLAIM: the treaty was signed in Year 38"],
                             "oracle": "NOT ADDRESSED",
                             "verdict": "VERDICT: Position B\nCONFIDENCE: 3\nREASONING: x"})
    rec = judge_loop.run_judgment(_tr(), "WORLD DOC", config.ARMS["clean"], 1, 0,
                                  client, _protocol())
    ex = rec["exchanges"][0]
    assert ex["oracle_prompt"].count("Is it supported by the text that") == 1
    assert ex["normalized"] == "NOT ADDRESSED"
    assert ex["well_formed_claim"] is True
    for call in client.calls:
        metadata = call["request_metadata"]
        assert metadata["budget"] == 1
        assert metadata["judge_model"] == config.JUDGE_MODEL


def test_placebo_arm_no_oracle_call_fixed_feedback():
    client = ScriptedClient({"query": ["CLAIM: something"],
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec = judge_loop.run_judgment(_tr(), "WORLD DOC", config.ARMS["placebo"], 1, 0,
                                  client, _protocol())
    assert all(c["kind"] != "oracle" for c in client.calls)
    ex = rec["exchanges"][0]
    assert ex["placebo"] is True and ex["oracle_prompt"] is None
    feedback = [m for c in client.calls if c["kind"] == "verdict"
                for m in c["messages"] if m["role"] == "user" and "ORACLE PLACEBO" in m["content"]]
    assert feedback, "placebo text must be fed back to the judge"


def test_placebo_turn_count_parity_full_budget_no_oracle_calls():
    # Backs the deliberation gate (|PLACEBO - CLEAN| turn-count check): when the judge
    # never signals DONE, placebo must consume the full budget turn-for-turn, exactly
    # like the clean arm would, while making zero oracle calls -- the placebo controls
    # for deliberation/turn-count without controlling for verification content.
    client = ScriptedClient({
        "query": ["CLAIM: fact one", "CLAIM: fact two", "CLAIM: fact three",
                  "CLAIM: fact four", "CLAIM: fact five"],
        "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x",
    })
    rec = judge_loop.run_judgment(_tr(), "WORLD DOC", config.ARMS["placebo"], 5, 0,
                                  client, _protocol())
    assert rec["queries_used"] == 5
    assert len(rec["exchanges"]) == 5
    assert all(e["placebo"] is True for e in rec["exchanges"])
    assert all(c["kind"] != "oracle" for c in client.calls)


def test_done_handling_differs_by_arm():
    protocol = _protocol()
    # robust arm stops on "I'm done."
    c1 = ScriptedClient({"query": ["I'm done."],
                         "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec1 = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 2, 0, c1, protocol)
    assert rec1["queries_used"] == 0
    # pilot arm treats "I'm done." as a query (the pilot brittleness)
    c2 = ScriptedClient({"query": ["I'm done.", "DONE"], "oracle": "YES",
                         "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec2 = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["both"], 2, 0, c2, protocol)
    assert rec2["queries_used"] == 1


def test_position_override_true_forces_position_a():
    client = ScriptedClient({"query": ["CLAIM: x"], "oracle": "YES",
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
                                  position_override=True)
    assert rec["position_a_is_correct"] is True


def test_position_override_false_forces_position_b():
    client = ScriptedClient({"query": ["CLAIM: x"], "oracle": "YES",
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
                                  position_override=False)
    assert rec["position_a_is_correct"] is False


def test_position_override_none_leaves_default_position_for_unchanged():
    tr = _tr()
    client = ScriptedClient({"query": ["CLAIM: x"], "oracle": "YES",
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec = judge_loop.run_judgment(tr, "DOC", config.ARMS["clean"], 1, 0, client, _protocol())
    expected = config.position_for(config.ARMS["clean"], tr["question_id"],
                                   tr["transcript_index"], config.JUDGE_MODEL, 1)
    assert rec["position_a_is_correct"] == expected
    # omitting the kwarg entirely must behave identically to passing None explicitly
    client2 = ScriptedClient({"query": ["CLAIM: x"], "oracle": "YES",
                              "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
    rec2 = judge_loop.run_judgment(tr, "DOC", config.ARMS["clean"], 1, 0, client2, _protocol(),
                                   position_override=None)
    assert rec2["position_a_is_correct"] == expected


def test_ab_fixed_across_budgets_and_replay_stability():
    protocol = _protocol()

    def run(budget):
        # "query" must be a bare string (like "oracle": "YES"), not a 1-item list: budget=5
        # drives 5 query-kind calls against the clean arm (never "done" on "CLAIM: x"), and a
        # list would be exhausted after round 1, raising IndexError. See task-11-report.md.
        c = ScriptedClient({"query": "CLAIM: x", "oracle": "YES",
                            "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: x"})
        return judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], budget, 0, c, protocol)

    r1, r5 = run(1), run(5)
    assert r1["position_a_is_correct"] == r5["position_a_is_correct"]
    # byte-stability modulo created_at
    a, b = run(1), run(1)
    a.pop("created_at"); b.pop("created_at")
    assert a == b


# --- Amendment 4 (2026-08-19), package item 2: visible-history byte caps ----------------------
#
# Synthetic transcript/protocol inputs (not _tr()/data/transcripts.jsonl) so these run without
# the local research corpus.

_SYNTHETIC_TRANSCRIPT = {
    "question_id": "SYN-001", "transcript_index": 0, "world": "synthetic_world",
    "question": "Is the claim true?", "correct_answer": "Yes.", "wrong_answer": "No.",
    "honest_first": True,
    "debate_transcript": [
        {"speaker": "honest", "text": "Argues yes.", "round": 1},
        {"speaker": "dishonest", "text": "Argues no.", "round": 1},
    ],
}

_REJECTION_PAYLOAD = "REJECTED: malformed query, please retry."
_NO_QUERY_PAYLOAD = "No query was submitted for this slot."


def _role_limits_for(model: str, effective_judge_query_max_tokens: int) -> dict:
    return {"model_role_limits": {model: {
        "judge_query": {"effective_request_max_tokens": effective_judge_query_max_tokens}}}}


def test_visible_history_cap_bytes_classifies_by_value_not_model_name():
    assert judge_loop.visible_history_cap_bytes(256) == judge_loop.VISIBLE_HISTORY_CAP_BASE_BYTES
    assert judge_loop.visible_history_cap_bytes(4096) == (
        judge_loop.VISIBLE_HISTORY_CAP_REASONING_BYTES)
    with pytest.raises(judge_loop.VisibleHistoryCapClassificationError):
        judge_loop.visible_history_cap_bytes(512)


def test_judge_query_effective_max_tokens_reads_role_limits_for_any_model_name():
    # A model name never mentioned anywhere in judge_loop's own source, classified purely by
    # the role-limits artifact's recorded value -- proving the classification is value-based,
    # never a hard-coded model list.
    role_limits = _role_limits_for("totally-fake/not-a-real-model-9000", 4096)
    assert judge_loop.judge_query_effective_max_tokens(
        role_limits, "totally-fake/not-a-real-model-9000") == 4096
    assert judge_loop.visible_history_cap_bytes(4096) == (
        judge_loop.VISIBLE_HISTORY_CAP_REASONING_BYTES)


def test_judge_query_effective_max_tokens_refuses_unknown_judge():
    with pytest.raises(judge_loop.VisibleHistoryCapClassificationError,
                       match="no 'judge_query' entry"):
        judge_loop.judge_query_effective_max_tokens({"model_role_limits": {}}, "unknown-model")


def test_over_cap_query_response_never_enters_history_and_uses_existing_retry_block_transition():
    over_cap_text = "X" * (judge_loop.VISIBLE_HISTORY_CAP_BASE_BYTES + 1)
    client = ScriptedClient({
        "query": [over_cap_text, over_cap_text],
        "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok",
    })
    role_limits = _role_limits_for(config.JUDGE_MODEL, 256)
    rec = judge_loop.run_judgment(
        _SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
        role_limits=role_limits, rejection_payload=_REJECTION_PAYLOAD,
        no_query_payload=_NO_QUERY_PAYLOAD)

    assert client.calls[0]["kind"] == "query"
    assert client.calls[1]["kind"] == "query"
    # Attempt 1 -> retry (does not consume the slot); attempt 2 -> block (consumes it). Exactly
    # two query-kind calls were made -- the frozen one-retry contract, not truncation or a
    # third attempt.
    assert sum(1 for c in client.calls if c["kind"] == "query") == 2

    # The over-cap text must never appear anywhere in ANY call's message history -- not
    # truncated, not partially admitted.
    for call in client.calls:
        for message in call["messages"]:
            assert over_cap_text not in message["content"]

    ex = rec["exchanges"][0]
    assert ex["blocked"] is True
    assert ex["blocked_feedback"] == _NO_QUERY_PAYLOAD
    assert ex["context_guard_over_cap"] is True
    assert rec["queries_used"] == 1


def test_under_cap_query_response_is_unaffected_by_role_limits():
    small_text = "CLAIM: a short well-formed claim"
    client = ScriptedClient({"query": [small_text], "oracle": "YES",
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok"})
    role_limits = _role_limits_for(config.JUDGE_MODEL, 256)
    rec = judge_loop.run_judgment(
        _SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
        role_limits=role_limits, rejection_payload=_REJECTION_PAYLOAD,
        no_query_payload=_NO_QUERY_PAYLOAD)
    ex = rec["exchanges"][0]
    assert ex.get("blocked") is not True
    assert ex["normalized"] == "YES"
    assert any(small_text in m["content"] for c in client.calls for m in c["messages"])


def test_role_limits_none_leaves_the_query_loop_byte_for_byte_unchanged():
    # Legacy default: no role_limits/rejection_payload/no_query_payload supplied, matching
    # every phase-2 call site. A response that would be over-cap under the reasoning class is
    # admitted exactly as before amendment 4.
    long_text = "X" * (judge_loop.VISIBLE_HISTORY_CAP_REASONING_BYTES + 1)
    client = ScriptedClient({"query": [long_text], "oracle": "YES",
                             "verdict": "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok"})
    rec = judge_loop.run_judgment(
        _SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol())
    ex = rec["exchanges"][0]
    assert ex.get("blocked") is not True
    assert any(long_text in m["content"] for c in client.calls for m in c["messages"])


def test_role_limits_supplied_without_payloads_raises():
    with pytest.raises(ValueError, match="rejection_payload/no_query_payload"):
        judge_loop.run_judgment(
            _SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0,
            ScriptedClient({"query": ["x"], "verdict": "v"}), _protocol(),
            role_limits=_role_limits_for(config.JUDGE_MODEL, 256))
