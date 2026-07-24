"""The canary dual-key gate: frozen checker + metadata-blinded reviewer, as one callable.

This is the glue the canary runner hands to judge_loop.run_judgment as ``query_gate``. It
composes three already-tested pieces that nothing in the repo had yet composed:

- rejudge.query_screen / Phase2QueryGate: the mechanical screen and the frozen one-free-retry
  slot machine, driven by an injected checker;
- the frozen gemma checker configuration (phase2_checker_frozen_config_2026-07-23.json);
- DualGate: the AND-join with the metadata-blinded reviewer, over a hash-chained store.

Two protocol properties drive the design and are asserted here.

Every raw query gets a reviewer decision, including ones the mechanical screen or the checker
rejected. The ratified governance says the queue holds all raw candidates so that "inclusion
reveals nothing"; labelling only checker-allowed queries would make the reviewer's inclusion
set the checker's allow set, leaking the checker verdict by construction.

An unlabelled payload must never reach DualGate. DualGate.review commits any reviewer_call
exception permanently as non-ALLOW, and the store refuses a second commit for the same payload,
so signalling "not labelled yet" through the gate would irreversibly burn that query.
"""
import json

import pytest

from rejudge import phase2_canary_gate as gate_mod
from rejudge.phase2_dual_gate import DualGate, DualGateDecisionStore, payload_hash


CANDIDATE_A = "The council seats seven guilds."
CANDIDATE_B = "The council seats nine guilds."
REJECTION = "Query rejected: ask a single specific factual claim"
NO_QUERY = "No verification result is available for this query."

GOOD_QUERY = "CLAIM: the council was established in Year 31"
SECOND_QUERY = "CLAIM: the removal threshold is 24 votes"


def _reviewer(label="ALLOW", clause="Allowed", rationale="single atomic claim"):
    return f"LABEL: {label}\nCLAUSE: {clause}\nRATIONALE: {rationale}"


def _build(tmp_path, *, checker, reviewer_call=None, pause_when_unlabeled=False, slots=2):
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    calls = []

    def recording_reviewer(raw_query, candidate_a, candidate_b):
        calls.append(raw_query)
        return (reviewer_call(raw_query) if reviewer_call is not None else _reviewer())

    dual = DualGate(store, recording_reviewer)
    canary_gate = gate_mod.CanaryQueryGate(
        candidate_a=CANDIDATE_A, candidate_b=CANDIDATE_B, total_slots=slots,
        checker=checker, dual_gate=dual, rejection_payload=REJECTION,
        no_query_payload=NO_QUERY, pause_when_unlabeled=pause_when_unlabeled)
    return canary_gate, store, calls


def _allowing_checker(request):
    return "allow"


def _rejecting_checker(request):
    return "reject"


# --- the three actions -----------------------------------------------------------------

def test_checker_allow_and_reviewer_allow_dispatches(tmp_path):
    canary_gate, _store, _calls = _build(tmp_path, checker=_allowing_checker)
    assert canary_gate(GOOD_QUERY, "claim", 1, 1) == ("allow", None)


def test_first_checker_reject_retries_without_consuming_the_slot(tmp_path):
    canary_gate, _store, _calls = _build(tmp_path, checker=_rejecting_checker)
    assert canary_gate(GOOD_QUERY, "claim", 1, 1) == ("retry", REJECTION)


def test_second_checker_reject_consumes_the_slot(tmp_path):
    canary_gate, _store, _calls = _build(tmp_path, checker=_rejecting_checker)
    canary_gate(GOOD_QUERY, "claim", 1, 1)
    assert canary_gate(SECOND_QUERY, "claim", 1, 2) == ("block", NO_QUERY)


def test_reviewer_non_allow_over_checker_allow_blocks_and_records_an_intercept(tmp_path):
    canary_gate, _store, _calls = _build(
        tmp_path, checker=_allowing_checker,
        reviewer_call=lambda q: _reviewer("REJECT", "P3", "two checkable facts"))
    assert canary_gate(GOOD_QUERY, "claim", 1, 1) == ("block", NO_QUERY)
    assert canary_gate.intercepts == [payload_hash(GOOD_QUERY, CANDIDATE_A, CANDIDATE_B)]


def test_reviewer_ambiguous_is_non_allow(tmp_path):
    canary_gate, _store, _calls = _build(
        tmp_path, checker=_allowing_checker,
        reviewer_call=lambda q: _reviewer("CONTRACT_AMBIGUOUS", "P4", "undecided"))
    assert canary_gate(GOOD_QUERY, "claim", 1, 1) == ("block", NO_QUERY)


def test_a_mechanically_screened_query_is_rejected_without_reaching_the_checker(tmp_path):
    def exploding_checker(request):
        raise AssertionError("the mechanical screen must short-circuit the checker")

    canary_gate, _store, _calls = _build(tmp_path, checker=exploding_checker)
    # Naming Position A is prohibited pattern P1 and is caught mechanically.
    action, feedback = canary_gate("CLAIM: Position A is correct", "claim", 1, 1)
    assert (action, feedback) == ("retry", REJECTION)


# --- reviewer coverage: every raw query, not only the allowed ones ----------------------

def test_a_checker_rejected_query_is_still_sent_to_the_reviewer(tmp_path):
    # Labelling only checker-allowed queries would make the reviewer's inclusion set the
    # checker's allow set, which leaks the checker verdict to a gate required to be blind to it.
    canary_gate, store, calls = _build(tmp_path, checker=_rejecting_checker)
    canary_gate(GOOD_QUERY, "claim", 1, 1)
    assert calls == [GOOD_QUERY]
    assert store.get(payload_hash(GOOD_QUERY, CANDIDATE_A, CANDIDATE_B)) is not None


def test_a_mechanically_screened_query_is_still_sent_to_the_reviewer(tmp_path):
    def exploding_checker(request):
        raise AssertionError("the mechanical screen must short-circuit the checker")

    canary_gate, store, calls = _build(tmp_path, checker=exploding_checker)
    screened = "CLAIM: Position A is correct"
    canary_gate(screened, "claim", 1, 1)
    assert calls == [screened]
    assert store.get(payload_hash(screened, CANDIDATE_A, CANDIDATE_B)) is not None


# --- the pause protocol ----------------------------------------------------------------

def test_an_unlabelled_payload_pauses_without_committing_anything(tmp_path):
    def exploding_reviewer(raw_query):
        raise AssertionError("pause mode must not invoke the reviewer")

    canary_gate, store, _calls = _build(
        tmp_path, checker=_allowing_checker, reviewer_call=exploding_reviewer,
        pause_when_unlabeled=True)
    with pytest.raises(gate_mod.PendingReviewerDecision) as excinfo:
        canary_gate(GOOD_QUERY, "claim", 1, 1)
    sha = payload_hash(GOOD_QUERY, CANDIDATE_A, CANDIDATE_B)
    assert excinfo.value.payload_sha256 == sha
    # Nothing committed: the payload can still receive a real decision later.
    assert store.get(sha) is None
    assert canary_gate.pending == [
        {"payload_sha256": sha, "query": GOOD_QUERY,
         "candidate_a": CANDIDATE_A, "candidate_b": CANDIDATE_B}]


def test_a_previously_committed_decision_is_reused_in_pause_mode(tmp_path):
    def exploding_reviewer(raw_query):
        raise AssertionError("a committed decision must be inherited, never re-asked")

    canary_gate, store, _calls = _build(
        tmp_path, checker=_allowing_checker, reviewer_call=exploding_reviewer,
        pause_when_unlabeled=True)
    store.commit(payload_hash(GOOD_QUERY, CANDIDATE_A, CANDIDATE_B),
                 "ALLOW", "Allowed", "fine", _reviewer(), "parsed")
    assert canary_gate(GOOD_QUERY, "claim", 1, 1) == ("allow", None)


def test_the_checker_is_not_called_for_a_payload_that_pauses(tmp_path):
    def exploding_checker(request):
        raise AssertionError("no checker spend before the reviewer decision exists")

    canary_gate, _store, _calls = _build(
        tmp_path, checker=exploding_checker, pause_when_unlabeled=True)
    with pytest.raises(gate_mod.PendingReviewerDecision):
        canary_gate(GOOD_QUERY, "claim", 1, 1)


# --- fail-closed checker paths ---------------------------------------------------------

@pytest.mark.parametrize("output,reason", [
    ("unresolved", "checker_unresolved"),
    ("Allow", "checker_malformed"),
    ("allow\n", "checker_malformed"),
])
def test_a_non_token_or_unresolved_checker_output_halts_the_cell(tmp_path, output, reason):
    canary_gate, _store, _calls = _build(tmp_path, checker=lambda request: output)
    with pytest.raises(gate_mod.CanaryCellHalted) as excinfo:
        canary_gate(GOOD_QUERY, "claim", 1, 1)
    assert excinfo.value.reason == reason


def test_a_checker_outage_halts_the_cell(tmp_path):
    def failing_checker(request):
        raise TimeoutError("provider timeout")

    canary_gate, _store, _calls = _build(tmp_path, checker=failing_checker)
    with pytest.raises(gate_mod.CanaryCellHalted) as excinfo:
        canary_gate(GOOD_QUERY, "claim", 1, 1)
    assert excinfo.value.reason == "checker_outage"


# --- the frozen checker configuration --------------------------------------------------

def test_the_frozen_checker_prompt_matches_its_recorded_hash():
    prompt = gate_mod.load_frozen_checker_prompt()
    assert len(prompt) == 5612
    assert gate_mod.sha256_text(prompt) == (
        "ecb22b55af091a2dc35c3f46e145db9c6796b2517a83dfec8682b75eb58e7428")


def test_a_drifted_frozen_checker_prompt_is_refused(monkeypatch):
    monkeypatch.setattr(gate_mod, "_frozen_variant_prompt", lambda: "drifted")
    with pytest.raises(gate_mod.FrozenCheckerDrift):
        gate_mod.load_frozen_checker_prompt()


def test_the_checker_adapter_sends_the_frozen_request_and_returns_output_verbatim():
    sent = {}

    class Client:
        def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict",
                     **kwargs):
            sent.update(messages=messages, model=model, temperature=temperature, seed=seed,
                        max_tokens=max_tokens, metadata=kwargs.get("request_metadata"))
            return "allow\n"

    adapter = gate_mod.FrozenCheckerAdapter(Client(), request_metadata={"cell": "x"})
    request = gate_mod.CheckerRequest(raw_query=GOOD_QUERY, candidate_a=CANDIDATE_A,
                                      candidate_b=CANDIDATE_B, slot=1, attempt=1)
    # Returned verbatim: the frozen parser treats a trailing newline as malformed on purpose,
    # so the adapter must not normalise it away.
    assert adapter(request) == "allow\n"
    assert sent["model"] == "google/gemma-4-31B-it"
    assert sent["temperature"] == 0
    assert sent["seed"] == 0
    # Resolved from the frozen v5 role limits, not the literal 16, which the strict client
    # would reject for a reasoning model.
    assert sent["max_tokens"] == 4096
    assert sent["messages"][0]["role"] == "system"
    assert gate_mod.sha256_text(sent["messages"][0]["content"]) == (
        "ecb22b55af091a2dc35c3f46e145db9c6796b2517a83dfec8682b75eb58e7428")
    user = sent["messages"][1]["content"]
    assert CANDIDATE_A in user and CANDIDATE_B in user and GOOD_QUERY in user
    assert sent["metadata"]["cell"] == "x"


# --- cross-cell decision sharing -------------------------------------------------------

def test_two_cells_sharing_a_store_share_one_committed_decision(tmp_path):
    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    calls = []

    def reviewer(raw_query, candidate_a, candidate_b):
        calls.append(raw_query)
        return _reviewer()

    def build():
        return gate_mod.CanaryQueryGate(
            candidate_a=CANDIDATE_A, candidate_b=CANDIDATE_B, total_slots=2,
            checker=_allowing_checker, dual_gate=DualGate(store, reviewer),
            rejection_payload=REJECTION, no_query_payload=NO_QUERY)

    assert build()(GOOD_QUERY, "claim", 1, 1) == ("allow", None)
    assert build()(GOOD_QUERY, "claim", 1, 1) == ("allow", None)
    assert calls == [GOOD_QUERY], "an identical payload must be reviewed exactly once"
    rows = [json.loads(line) for line
            in (tmp_path / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
