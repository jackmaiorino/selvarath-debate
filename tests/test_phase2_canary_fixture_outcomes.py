"""The five offline checker outcomes the frozen plan requires, driven end to end.

``build_canary_plan`` names these as ``offline_checker_fixture_outcomes_required``:
accept, reject_then_accept, reject_twice_consumes_slot, malformed_halts, outage_halts. The
governance artifacts make passing them a protocol prerequisite for the canary, not merely a
nice-to-have.

Each runs through the genuine stack -- judge_loop's query loop, the frozen checker adapter,
Phase2QueryGate's slot machine and DualGate's join -- with only the provider replaced. A test
that stubbed the gate instead would prove nothing about the path that will actually run.
"""
import json
from pathlib import Path

import pytest

from rejudge import config, judge_loop
from rejudge import phase2_canary_gate as gate_mod
from rejudge.phase2_canary_fixtures import DeterministicCanaryClient, StubReviewer, scripted
from rejudge.phase2_dual_gate import DualGate, DualGateDecisionStore
from rejudge.phase2_plan import build_canary_plan


TRANSCRIPTS_PATH = Path("data/transcripts.jsonl")
MISSING_TRANSCRIPTS_REASON = (
    "requires local research corpus data/transcripts.jsonl (not included in clean clones)"
)

REJECTION = "Query rejected: ask a single specific factual claim"
NO_QUERY = "No verification result is available for this query."


def _tr():
    if not TRANSCRIPTS_PATH.is_file():
        pytest.skip(MISSING_TRANSCRIPTS_REASON)
    return json.loads(TRANSCRIPTS_PATH.open(encoding="utf-8").readline())


def _run(tmp_path, *, checker_script, budget=2, reviewer=None, arm="clean"):
    transcript = _tr()
    client = DeterministicCanaryClient(query_checker=scripted(checker_script))
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    canary_gate = gate_mod.CanaryQueryGate(
        candidate_a=transcript["correct_answer"], candidate_b=transcript["wrong_answer"],
        total_slots=budget,
        checker=gate_mod.FrozenCheckerAdapter(client, request_metadata={"cell_key": "cell"}),
        dual_gate=DualGate(store, reviewer or StubReviewer()),
        rejection_payload=REJECTION, no_query_payload=NO_QUERY)
    record = judge_loop.run_judgment(
        transcript, "WORLD DOC", config.ARMS[arm], budget, 0, client,
        config.load_protocol(), query_gate=canary_gate, position_override=True)
    return record, canary_gate, client


def test_the_frozen_plan_still_demands_exactly_these_five_outcomes():
    required = build_canary_plan(
        json.loads(Path("rejudge/phase2_protocol.json").read_text(encoding="utf-8"))
    )["summary"]["offline_checker_fixture_outcomes_required"]
    assert required == ["accept", "reject_then_accept", "reject_twice_consumes_slot",
                        "malformed_halts", "outage_halts"]


def test_accept(tmp_path):
    record, gate, _client = _run(tmp_path, checker_script=["allow"])
    assert record["queries_used"] == 2
    assert all(e["normalized"] == "YES" for e in record["exchanges"])
    assert [e["action"] for e in gate.events] == ["allow", "allow"]


def test_reject_then_accept(tmp_path):
    # A first rejection inside a slot is free: the judge re-asks and the slot survives.
    record, gate, _client = _run(tmp_path, checker_script=["reject", "allow"], budget=1)
    assert [e["action"] for e in gate.events] == ["retry", "allow"]
    assert record["queries_used"] == 1
    assert record["exchanges"][0]["normalized"] == "YES"


def test_reject_twice_consumes_slot(tmp_path):
    record, gate, _client = _run(tmp_path, checker_script=["reject", "reject"], budget=1)
    assert [e["action"] for e in gate.events] == ["retry", "block"]
    exchange = record["exchanges"][0]
    assert exchange["blocked"] is True
    assert exchange["oracle_prompt"] is None
    # The judge still saw a turn, so clean and placebo stay structurally comparable.
    verdict_messages = record["judge_messages"]
    assert any(NO_QUERY in m["content"] for m in verdict_messages if m["role"] == "user")


def test_malformed_halts(tmp_path):
    # The frozen parser accepts an exact lower-case token; anything else halts the cell
    # rather than choosing a side.
    with pytest.raises(gate_mod.CanaryCellHalted) as excinfo:
        _run(tmp_path, checker_script=["Allow"])
    assert excinfo.value.reason == "checker_malformed"


def test_a_trailing_newline_is_malformed_by_design(tmp_path):
    with pytest.raises(gate_mod.CanaryCellHalted) as excinfo:
        _run(tmp_path, checker_script=["allow\n"])
    assert excinfo.value.reason == "checker_malformed"


def test_outage_halts(tmp_path):
    with pytest.raises(gate_mod.CanaryCellHalted) as excinfo:
        _run(tmp_path, checker_script=[TimeoutError("provider timeout")])
    assert excinfo.value.reason == "checker_outage"


def test_unresolved_halts(tmp_path):
    # Not one of the five, but the third frozen token must not silently pass either.
    with pytest.raises(gate_mod.CanaryCellHalted) as excinfo:
        _run(tmp_path, checker_script=["unresolved"])
    assert excinfo.value.reason == "checker_unresolved"


# --- the dual-gate paths the five outcomes do not reach -------------------------------------

def test_a_reviewer_intercept_blocks_a_checker_allowed_query(tmp_path):
    reviewer = StubReviewer(scripted([("REJECT", "P3")]))
    record, gate, _client = _run(
        tmp_path, checker_script=["allow"], budget=1, reviewer=reviewer)
    assert [e["action"] for e in gate.events] == ["block"]
    assert len(gate.intercepts) == 1
    assert record["exchanges"][0]["blocked"] is True


def test_an_unparseable_reviewer_reply_fails_closed(tmp_path):
    reviewer = StubReviewer(scripted(["Sure, here is my review:\nLABEL: ALLOW"]))
    _record, gate, _client = _run(
        tmp_path, checker_script=["allow"], budget=1, reviewer=reviewer)
    assert [e["action"] for e in gate.events] == ["block"]
    assert gate.events[0]["reviewer_status"] == "malformed"


def test_placebo_takes_the_identical_blocked_transition(tmp_path):
    reviewer = StubReviewer(scripted([("REJECT", "P3")]))
    record, gate, client = _run(
        tmp_path, checker_script=["allow"], budget=1, reviewer=reviewer, arm="placebo")
    assert [e["action"] for e in gate.events] == ["block"]
    assert record["exchanges"][0]["blocked"] is True
    # Blocked means blocked in both modes: the placebo responder must not have fired.
    assert config.PLACEBO_TEXT not in json.dumps(record["exchanges"])
    assert all(c["call_role"] != "oracle_verification" for c in client.calls)


def test_an_identical_query_across_cells_shares_one_reviewer_decision(tmp_path):
    asked = []

    class CountingReviewer(StubReviewer):
        def __call__(self, raw_query, candidate_a, candidate_b):
            asked.append(raw_query)
            return super().__call__(raw_query, candidate_a, candidate_b)

    store_path = tmp_path / "decisions.jsonl"
    for _ in range(2):
        transcript = _tr()
        client = DeterministicCanaryClient(query_checker=scripted(["allow"]))
        canary_gate = gate_mod.CanaryQueryGate(
            candidate_a=transcript["correct_answer"], candidate_b=transcript["wrong_answer"],
            total_slots=1,
            checker=gate_mod.FrozenCheckerAdapter(client, request_metadata={"cell_key": "c"}),
            dual_gate=DualGate(DualGateDecisionStore(store_path), CountingReviewer()),
            rejection_payload=REJECTION, no_query_payload=NO_QUERY)
        judge_loop.run_judgment(
            transcript, "WORLD DOC", config.ARMS["clean"], 1, 0, client,
            config.load_protocol(), query_gate=canary_gate, position_override=True)

    assert len(asked) == 1, "an identical payload must be reviewed exactly once"
