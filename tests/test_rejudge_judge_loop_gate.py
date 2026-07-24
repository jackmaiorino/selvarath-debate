"""Phase-2 seam on the Stage-1 judge loop: query_template_override and query_gate.

Both parameters default to None. With both omitted the loop must be byte-identical to the
Stage-1 path, because the frozen Stage-1 records are replayed through this same function.

The gate callback owns no policy of its own. It receives one proposed query and returns the
action the frozen policy dictates, plus the exact text the judge is shown:

    ("allow", None)          -> dispatch to the oracle (or the placebo responder)
    ("retry", feedback)      -> slot NOT consumed, judge re-asks within the same slot
    ("block", feedback)      -> slot consumed, nothing dispatched, judge moves on

"retry" is the frozen contract rejection (one free retry per slot). "block" is the
no-query transition used for a second rejection and for a reviewer intercept, where the
governance artifacts require the slot to be consumed with no dispatch.
"""
import json
from pathlib import Path

import pytest

from rejudge import config, judge_loop


TRANSCRIPTS_PATH = Path("data/transcripts.jsonl")
MISSING_TRANSCRIPTS_REASON = (
    "requires local research corpus data/transcripts.jsonl (not included in clean clones)"
)

VERDICT = "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok"
REJECTION = "Query rejected: ask a single specific factual claim"
BLOCKED = "No verification result is available for this query."


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
    rows = [json.loads(line) for line in TRANSCRIPTS_PATH.open(encoding="utf-8")]
    return rows[0]


def _protocol():
    return config.load_protocol()


def _query_prompts(client):
    """The query-phase user message shown to the judge on each query-kind call."""
    return [call["messages"][-1]["content"] for call in client.calls if call["kind"] == "query"]


# --- query_template_override --------------------------------------------------------------

PHASE2_QUERY_TEMPLATE = (
    "You have {remaining_budget} verification queries remaining out of {total_budget}.\n\n"
    "Submit at most one single atomic factual claim.\n\n{previous_queries}\n\n"
    "Respond with exactly one bare claim prefixed by `CLAIM: `, or respond with exactly `DONE`."
)


def test_query_template_override_is_used_verbatim_without_the_pilot_rewrite():
    # The phase-2 bundle template contains no pilot anchor line, so the Stage-1 code path
    # (clean_query_phase_prompt) would raise on it. The override must bypass that rewrite.
    client = ScriptedClient({"query": ["CLAIM: the treaty was signed in Year 38"],
                             "oracle": "YES", "verdict": VERDICT})
    judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
                            query_template_override=PHASE2_QUERY_TEMPLATE)
    shown = _query_prompts(client)[0]
    assert "Submit at most one single atomic factual claim." in shown
    assert "Is it stated in the text that" not in shown
    assert shown.startswith("You have 1 verification queries remaining out of 1.")


def test_query_template_override_none_keeps_the_stage1_rewrite():
    client = ScriptedClient({"query": ["CLAIM: x"], "oracle": "YES", "verdict": VERDICT})
    judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
                            query_template_override=None)
    shown = _query_prompts(client)[0]
    expected = config.clean_query_phase_prompt(
        _protocol()["judge"]["query_phase_prompt"]).format(
            remaining_budget=1, total_budget=1, previous_queries="No queries submitted yet.")
    assert shown == expected


# --- query_gate ---------------------------------------------------------------------------

def test_gate_allow_dispatches_the_oracle_exactly_as_the_ungated_loop_does():
    calls = []

    def gate(raw_query, claim, slot, attempt):
        calls.append((raw_query, claim, slot, attempt))
        return "allow", None

    client = ScriptedClient({"query": ["CLAIM: the treaty was signed in Year 38"],
                             "oracle": "YES", "verdict": VERDICT})
    rec = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client,
                                  _protocol(), query_gate=gate)
    assert calls == [("CLAIM: the treaty was signed in Year 38",
                      "the treaty was signed in Year 38", 1, 1)]
    assert rec["queries_used"] == 1
    assert rec["exchanges"][0]["normalized"] == "YES"
    assert sum(1 for c in client.calls if c["kind"] == "oracle") == 1


def test_gate_retry_shows_feedback_and_re_asks_without_consuming_the_slot():
    actions = iter([("retry", REJECTION), ("allow", None)])

    def gate(raw_query, claim, slot, attempt):
        return next(actions)

    client = ScriptedClient({"query": ["CLAIM: bad one", "CLAIM: good one"],
                             "oracle": "YES", "verdict": VERDICT})
    rec = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client,
                                  _protocol(), query_gate=gate)
    # Two query-kind calls inside a single budget slot.
    assert sum(1 for c in client.calls if c["kind"] == "query") == 2
    # The rejection text was shown to the judge before the retry.
    assert any(REJECTION in prompt for prompt in _query_prompts(client))
    # Only the accepted query reached the oracle and was recorded.
    assert sum(1 for c in client.calls if c["kind"] == "oracle") == 1
    assert rec["queries_used"] == 1
    assert rec["exchanges"][0]["extracted_claim"] == "good one"


def test_gate_block_consumes_the_slot_and_dispatches_nothing():
    def gate(raw_query, claim, slot, attempt):
        return "block", BLOCKED

    client = ScriptedClient({"query": ["CLAIM: bad one"], "verdict": VERDICT})
    rec = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client,
                                  _protocol(), query_gate=gate)
    assert all(c["kind"] != "oracle" for c in client.calls)
    assert sum(1 for c in client.calls if c["kind"] == "query") == 1
    exchange = rec["exchanges"][0]
    assert exchange["blocked"] is True
    assert exchange["oracle_prompt"] is None
    assert exchange["normalized"] is None
    # The judge was shown the no-query transition, so turn structure is preserved.
    verdict_call = [c for c in client.calls if c["kind"] == "verdict"][0]
    assert any(BLOCKED in m["content"] for m in verdict_call["messages"] if m["role"] == "user")


def test_gate_block_on_placebo_takes_the_identical_transition():
    def gate(raw_query, claim, slot, attempt):
        return "block", BLOCKED

    client = ScriptedClient({"query": ["CLAIM: bad one"], "verdict": VERDICT})
    rec = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["placebo"], 1, 0, client,
                                  _protocol(), query_gate=gate)
    exchange = rec["exchanges"][0]
    assert exchange["blocked"] is True
    # A blocked placebo slot must not emit the placebo payload: the gate blocked the
    # dispatch, and consult #27 requires the same blocked transition in both modes.
    assert config.PLACEBO_TEXT not in json.dumps(rec["exchanges"])
    verdict_call = [c for c in client.calls if c["kind"] == "verdict"][0]
    assert any(BLOCKED in m["content"] for m in verdict_call["messages"] if m["role"] == "user")


def test_gate_sees_incrementing_slot_and_attempt_numbers():
    seen = []

    def gate(raw_query, claim, slot, attempt):
        seen.append((slot, attempt))
        return ("retry", REJECTION) if attempt == 1 else ("allow", None)

    client = ScriptedClient({"query": "CLAIM: x", "oracle": "YES", "verdict": VERDICT})
    judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 2, 0, client,
                            _protocol(), query_gate=gate)
    assert seen == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_a_third_attempt_in_one_slot_is_refused_rather_than_spun():
    # The frozen contract allows exactly one free retry per slot. A gate asking for a third
    # attempt is a policy violation, and spinning on it would burn paid judge calls until the
    # spend cap tripped. Fail closed instead.
    def gate(raw_query, claim, slot, attempt):
        return "retry", REJECTION

    client = ScriptedClient({"query": "CLAIM: x", "oracle": "YES", "verdict": VERDICT})
    with pytest.raises(judge_loop.QueryRetryPolicyError):
        judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client,
                                _protocol(), query_gate=gate)
    assert sum(1 for c in client.calls if c["kind"] == "query") == 2


def test_gate_none_leaves_every_recorded_field_unchanged():
    def run(**kwargs):
        client = ScriptedClient({"query": ["CLAIM: x"], "oracle": "YES", "verdict": VERDICT})
        record = judge_loop.run_judgment(_tr(), "DOC", config.ARMS["clean"], 1, 0, client,
                                         _protocol(), **kwargs)
        record.pop("created_at")
        return record

    assert run() == run(query_gate=None, query_template_override=None)
