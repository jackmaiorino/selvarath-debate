import json

from rejudge import config, judge_loop


class ScriptedClient:
    """Returns scripted responses by kind; records every call."""

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict"):
        self.calls.append({"kind": kind, "messages": [dict(m) for m in messages]})
        v = self.script[kind]
        return v.pop(0) if isinstance(v, list) else v


def _tr():
    rows = [json.loads(l) for l in open("data/transcripts.jsonl", encoding="utf-8")]
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
