"""Amendment 4 (2026-08-19): context-only estimator, visible-history byte caps, and the
validation/replay gates -- rejudge/phase3_amendment4_context_guard_2026-08-19.json.

Self-contained: unlike tests/test_rejudge_judge_loop.py and tests/test_rejudge_judge_loop_gate.py
(which skip without the local research corpus data/transcripts.jsonl), the judge_loop tests here
use a small hand-built synthetic transcript so the amendment's safety-critical byte-cap
enforcement is actually exercised in every environment, not only where the corpus happens to be
present.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from rejudge import api_client as ac
from rejudge import config, judge_loop
from rejudge.phase2_canary_gate import CanaryQueryGate
from rejudge.phase2_dual_gate import DualGate, DualGateDecisionStore, payload_hash
from scripts import phase3_estimator_validation as ev
from scripts import phase3_history_cap_replay as hcr

# ---------------------------------------------------------------------------------------------
# item 1: the context-only estimator (rejudge.api_client.estimate_context_tokens)
# ---------------------------------------------------------------------------------------------


def test_estimate_context_tokens_golden_values():
    # role "user" (4 bytes) + content "hello" (5 bytes) = 9 bytes.
    # E_prompt = ceil(9 / 3) + 512 = 3 + 512 = 515. E_total = 515 + 100 = 615.
    msgs = [{"role": "user", "content": "hello"}]
    assert ac.estimate_context_tokens(msgs, 100) == (515, 615)

    # Two messages: role+content bytes = len("system")+len("hi")+len("user")+len("bye")
    # = 6+2+4+3 = 15 bytes. E_prompt = ceil(15 / 3) + 512 = 5 + 512 = 517.
    # E_total = 517 + 256 = 773.
    msgs2 = [{"role": "system", "content": "hi"}, {"role": "user", "content": "bye"}]
    assert ac.estimate_context_tokens(msgs2, 256) == (517, 773)

    # Empty messages: 0 bytes. E_prompt = ceil(0/3) + 512 = 512.
    assert ac.estimate_context_tokens([], 0) == (512, 512)


def test_estimate_usage_is_byte_identical_after_amendment4():
    """Regression proof that _estimate_usage (spend/reservation accounting) is untouched.

    Golden values computed from the documented formula
    (``prompt_bound = 64 + sum(32 + role_bytes + content_bytes)``), asserted directly rather
    than re-deriving it here -- if a future edit ever changes ``_estimate_usage``'s output for
    these fixed inputs, this test fails regardless of whether the change was deliberate.
    """
    msgs = [{"role": "user", "content": "hello"}]
    assert ac._estimate_usage(msgs, 100) == (105, 100)

    msgs2 = [{"role": "system", "content": "hi"}, {"role": "user", "content": "bye"}]
    # 64 + (32+6+2) + (32+4+3) = 64 + 40 + 39 = 143
    assert ac._estimate_usage(msgs2, 256) == (143, 256)

    assert ac._estimate_usage([], 0) == (64, 0)


def test_guard_uses_e_total_a_call_old_arithmetic_rejected_now_passes():
    # 3000-byte content: OLD prompt_bound = 64 + 32 + 4("user") + 3000 = 3100; + max_tokens 64
    # = 3164. NEW E_total = ceil(3004/3) + 512 + 64 = 1002 + 512 + 64 = 1578. A ceiling of 2000
    # sits strictly between them: the old guard would have refused this call, the new one must
    # not.
    big = [{"role": "user", "content": "x" * 3000}]
    assert ac._estimate_usage(big, 64)[0] + 64 == 3164
    assert ac.estimate_context_tokens(big, 64) == (1514, 1578)

    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=StubSDKGuard(),
                         max_context_tokens=2000)
    # Must NOT raise: 1578 <= 2000.
    c.complete(big, "m", 0.1, 1, 64)


def test_guard_still_refuses_a_genuinely_over_ceiling_call():
    big = [{"role": "user", "content": "x" * 3000}]
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=StubSDKGuard(),
                         max_context_tokens=1000)
    with pytest.raises(ac.ContextGuardError):
        c.complete(big, "m", 0.1, 1, 64)


class _GuardUsage:
    prompt_tokens = 10
    completion_tokens = 5


class _GuardChoice:
    class message:
        content = "ok"


class _GuardResp:
    usage = _GuardUsage()
    choices = [_GuardChoice()]


class StubSDKGuard:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                return _GuardResp()


# ---------------------------------------------------------------------------------------------
# item 2: mechanically enforced visible-history byte caps (rejudge.judge_loop)
# ---------------------------------------------------------------------------------------------


def test_visible_history_cap_bytes_classification_is_value_based():
    assert judge_loop.visible_history_cap_bytes(256) == 1024
    assert judge_loop.visible_history_cap_bytes(4096) == 6144
    with pytest.raises(judge_loop.VisibleHistoryCapClassificationError):
        judge_loop.visible_history_cap_bytes(512)


ROLE_LIMITS_FIXTURE = {
    "model_role_limits": {
        "test-judge-base": {"judge_query": {"effective_request_max_tokens": 256}},
        "test-judge-reasoning": {"judge_query": {"effective_request_max_tokens": 4096}},
    },
}


def test_judge_query_effective_max_tokens_reads_the_role_limits_artifact():
    assert judge_loop.judge_query_effective_max_tokens(
        ROLE_LIMITS_FIXTURE, "test-judge-base") == 256
    assert judge_loop.judge_query_effective_max_tokens(
        ROLE_LIMITS_FIXTURE, "test-judge-reasoning") == 4096
    with pytest.raises(judge_loop.VisibleHistoryCapClassificationError):
        judge_loop.judge_query_effective_max_tokens(ROLE_LIMITS_FIXTURE, "unknown-model")


VERDICT = "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: ok"
REJECTED = "REJECTED: response exceeded the visible-history byte cap"
BLOCKED = "BLOCKED: no more attempts remain for this slot"

SYNTHETIC_TRANSCRIPT = {
    "question_id": "SYN-001",
    "transcript_index": 0,
    "question": "Was the treaty signed in Year 38?",
    "correct_answer": "Yes, the treaty was signed in Year 38.",
    "wrong_answer": "No, the treaty was signed in Year 40.",
    "debate_transcript": [
        {"speaker": "honest", "text": "The treaty was signed in Year 38."},
        {"speaker": "dishonest", "text": "The treaty was signed in Year 40."},
    ],
}


class ScriptedClient:
    """Returns scripted responses by kind; records every call (matches the pattern already
    used by tests/test_rejudge_judge_loop.py and tests/test_rejudge_judge_loop_gate.py)."""

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", **kwargs):
        self.calls.append({"kind": kind, "messages": [dict(m) for m in messages], **kwargs})
        v = self.script[kind]
        return v.pop(0) if isinstance(v, list) else v


def _protocol():
    return config.load_protocol()


def _all_shown_content(client):
    return " ".join(m["content"] for call in client.calls for m in call["messages"])


def test_over_cap_response_retries_without_ever_entering_history():
    over_cap = "CLAIM: " + ("x" * 1100)          # 1107 bytes > the 1024 base-class cap
    under_cap = "CLAIM: the treaty was signed in Year 38"
    client = ScriptedClient({"query": [over_cap, under_cap], "oracle": "YES", "verdict": VERDICT})

    rec = judge_loop.run_judgment(
        SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
        judge_model="test-judge-base", role_limits=ROLE_LIMITS_FIXTURE,
        rejection_payload=REJECTED, no_query_payload=BLOCKED)

    shown = _all_shown_content(client)
    assert over_cap not in shown, "an over-cap response must never enter the judge-facing history"
    assert REJECTED in shown
    # The retry consumed no slot: attempt 2's under-cap claim was allowed through normally.
    assert rec["queries_used"] == 1
    assert rec["exchanges"][0]["normalized"] == "YES"
    assert rec["exchanges"][0].get("context_guard_over_cap") is None  # not a blocked exchange


def test_over_cap_response_blocks_on_the_second_attempt_never_truncated():
    over_cap_1 = "CLAIM: " + ("x" * 1100)
    over_cap_2 = "CLAIM: " + ("y" * 1100)
    client = ScriptedClient({"query": [over_cap_1, over_cap_2], "verdict": VERDICT})

    rec = judge_loop.run_judgment(
        SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
        judge_model="test-judge-base", role_limits=ROLE_LIMITS_FIXTURE,
        rejection_payload=REJECTED, no_query_payload=BLOCKED)

    assert all(c["kind"] != "oracle" for c in client.calls), "a blocked slot dispatches nothing"
    ex = rec["exchanges"][0]
    assert ex["blocked"] is True
    assert ex["blocked_feedback"] == BLOCKED
    assert ex["context_guard_over_cap"] is True
    # The FULL raw over-cap text is still recorded in the audit-trail exchange (never truncated),
    # even though it never entered the judge-facing `messages`/`judge_messages`.
    assert ex["raw_query_response"] == over_cap_2
    shown = _all_shown_content(client)
    assert over_cap_1 not in shown and over_cap_2 not in shown
    assert BLOCKED in shown


def test_under_cap_response_is_unaffected_by_role_limits():
    claim = "CLAIM: the treaty was signed in Year 38"
    client = ScriptedClient({"query": [claim], "oracle": "YES", "verdict": VERDICT})

    rec = judge_loop.run_judgment(
        SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
        judge_model="test-judge-base", role_limits=ROLE_LIMITS_FIXTURE,
        rejection_payload=REJECTED, no_query_payload=BLOCKED)

    assert rec["exchanges"][0]["normalized"] == "YES"
    verdict_call = [c for c in client.calls if c["kind"] == "verdict"][0]
    # The claim response DOES appear verbatim in later history, exactly as the legacy
    # (role_limits=None) path already behaves -- an under-cap response is untouched.
    assert any(m["content"] == claim for m in verdict_call["messages"])


def test_reasoning_class_cap_allows_a_response_that_would_be_over_cap_for_base_class():
    long_but_within_reasoning_cap = "CLAIM: " + ("z" * 5000)  # 5007 bytes: >1024, but <6144
    client = ScriptedClient(
        {"query": [long_but_within_reasoning_cap], "oracle": "YES", "verdict": VERDICT})

    rec = judge_loop.run_judgment(
        SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
        judge_model="test-judge-reasoning", role_limits=ROLE_LIMITS_FIXTURE,
        rejection_payload=REJECTED, no_query_payload=BLOCKED)

    assert rec["exchanges"][0]["normalized"] == "YES"
    assert rec["exchanges"][0].get("blocked") is None


def test_role_limits_without_payloads_raises_rather_than_guessing_a_transition():
    client = ScriptedClient({"query": ["CLAIM: x"]})
    with pytest.raises(ValueError, match="rejection_payload/no_query_payload"):
        judge_loop.run_judgment(
            SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
            judge_model="test-judge-base", role_limits=ROLE_LIMITS_FIXTURE)


def test_role_limits_none_is_byte_identical_to_the_legacy_path():
    """The additive default: role_limits=None (every phase-2 call site) never enforces a cap,
    even for a response that WOULD be over-cap under an explicit role_limits."""
    over_cap = "CLAIM: " + ("x" * 1100)
    client = ScriptedClient({"query": [over_cap], "oracle": "YES", "verdict": VERDICT})
    rec = judge_loop.run_judgment(
        SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol())
    assert rec["exchanges"][0]["normalized"] == "YES"
    assert over_cap in _all_shown_content(client)


def test_phase2_shaped_blocked_exchange_is_byte_identical_pre_and_post_amendment():
    """Second re-review (2026-08-19): a real phase-2 caller (rejudge.phase2_canary_live's
    CanaryQueryGate construction site, phase2_canary_live.py:1199) never passes role_limits at
    all. Its blocked-exchange record must be byte-identical to the pre-amendment shape -- no
    ``context_guard_over_cap`` key at all, regardless of value; adding it unconditionally
    changed phase-2's own record shape and serialized bytes even though the amendment never
    touched that call path.

    Proven against the ACTUAL pre-amendment ``rejudge/judge_loop.py`` (git commit 51aadc8,
    "Amendment 4 (owner-signed): context-guard estimator package per the Codex consult" -- the
    commit immediately before any amendment-4 implementation landed in this worktree), loaded as
    an isolated module, rather than a hand-typed golden dict that could itself encode a wrong
    assumption about the pre-amendment shape.
    """
    import importlib.util
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    old_source = subprocess.run(
        ["git", "show", "51aadc8:rejudge/judge_loop.py"], cwd=str(repo_root),
        capture_output=True, text=True, check=True).stdout

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(old_source)
        old_path = handle.name
    try:
        spec = importlib.util.spec_from_file_location("_pre_amendment4_judge_loop", old_path)
        old_judge_loop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old_judge_loop)
    finally:
        Path(old_path).unlink()

    def gate(raw_query, claim, slot, attempt):
        return "block", BLOCKED

    old_rec = old_judge_loop.run_judgment(
        SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0,
        ScriptedClient({"query": ["CLAIM: the treaty was signed in Year 38"], "verdict": VERDICT}),
        _protocol(), query_gate=gate)
    new_rec = judge_loop.run_judgment(
        SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0,
        ScriptedClient({"query": ["CLAIM: the treaty was signed in Year 38"], "verdict": VERDICT}),
        _protocol(), query_gate=gate)  # role_limits/rejection_payload/no_query_payload omitted

    # created_at (wall clock) and harness_version (git sha) are provenance metadata never
    # claimed identical across two separately-loaded module instances; everything else --
    # the exchange shape this re-review is actually about included -- must match exactly.
    def _stable(record):
        return {k: v for k, v in record.items() if k not in ("created_at", "harness_version")}

    assert json.dumps(_stable(old_rec), sort_keys=True) == json.dumps(
        _stable(new_rec), sort_keys=True)
    assert "context_guard_over_cap" not in new_rec["exchanges"][0]


def test_over_cap_response_flows_through_a_production_query_gate_not_around_it(tmp_path):
    """Codex re-review repro (blocker 1, 2026-08-19): an over-cap response used to bypass
    query_gate entirely by locally synthesizing its own retry/block decision. That desynced
    CanaryQueryGate's internal Phase2QueryGate attempt counter from this loop's own local
    counter -- reproduced as ``QueryRetryPolicyError: query gate asked for attempt 3`` on an
    over-cap attempt 1 followed by a mechanically rejected attempt 2 (the gate still believed
    it was on attempt 1) -- and silently skipped the "every raw query gets a reviewer
    decision" invariant for the discarded over-cap payload.

    Fix: Phase2QueryGate itself now owns the over-cap classification (an additive
    ``max_response_bytes`` constructor param, checked in ``submit()`` before ``query_screen``
    and before the checker, exactly like any other mechanical rejection); judge_loop no longer
    synthesizes anything when a query_gate is present, it just calls the gate, unconditionally,
    like any other candidate query.

    Run against a PRODUCTION ``CanaryQueryGate`` -- real ``Phase2QueryGate``, real
    ``DualGate``/``DualGateDecisionStore`` -- never a stub.
    """
    candidate_a, candidate_b = "Candidate A text", "Candidate B text"
    over_cap_response = "CLAIM: " + ("x" * 1100)              # over the 1024-byte base-class cap
    mechanically_rejected_response = "CLAIM: Position A is the correct answer"  # query_screen hit
    client = ScriptedClient(
        {"query": [over_cap_response, mechanically_rejected_response], "verdict": VERDICT})

    def checker(request):
        # Both attempts in this repro are rejected BEFORE the checker (the byte cap, then the
        # mechanical screen) -- if this fires, the gate stopped short-circuiting correctly.
        raise AssertionError("the checker must not be consulted for either attempt here")

    def reviewer_call(raw_query, candidate_a, candidate_b):
        return "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fine"

    store = DualGateDecisionStore(tmp_path / "decisions.jsonl")
    gate = CanaryQueryGate(
        candidate_a=candidate_a, candidate_b=candidate_b, total_slots=1,
        checker=checker, dual_gate=DualGate(store, reviewer_call),
        rejection_payload=REJECTED, no_query_payload=BLOCKED,
        max_response_bytes=1024)

    rec = judge_loop.run_judgment(
        SYNTHETIC_TRANSCRIPT, "WORLD DOC", config.ARMS["clean"], 1, 0, client, _protocol(),
        judge_model="test-judge-base", role_limits=ROLE_LIMITS_FIXTURE,
        rejection_payload=REJECTED, no_query_payload=BLOCKED, query_gate=gate)

    # No QueryRetryPolicyError -- the gate's own attempt counter tracked both submissions.
    ex = rec["exchanges"][0]
    assert ex["blocked"] is True
    assert ex["blocked_feedback"] == BLOCKED

    # Gate state is consistent: exactly one slot, now exhausted (no phantom third attempt).
    assert gate._gate.exhausted
    assert gate._gate._slot == 2
    assert gate._gate._attempt == 1

    # The decision trail carries BOTH raw queries, in order -- the over-cap payload received a
    # reviewer decision exactly like every other raw query (the gate's own invariant).
    assert [event["raw_query"] for event in gate.events] == [
        over_cap_response, mechanically_rejected_response]
    for query in (over_cap_response, mechanically_rejected_response):
        assert store.get(payload_hash(query, candidate_a, candidate_b)) is not None

    # And still never truncated / never entered the judge-facing history.
    assert over_cap_response not in _all_shown_content(client)


def test_phase2_query_gate_max_response_bytes_is_additive_default_off():
    """Every pre-amendment Phase2QueryGate caller omits max_response_bytes; construction and
    submit() must behave exactly as before (no over-cap classification ever fires)."""
    from rejudge.phase2_query_gate import CheckerDecision, Phase2QueryGate

    gate = Phase2QueryGate(
        candidate_a="a", candidate_b="b", total_slots=1, checker=lambda request: "allow")
    huge_but_unconfigured = "x" * 100_000
    event = gate.submit(huge_but_unconfigured)
    assert event.final_decision is CheckerDecision.ALLOW


def test_phase2_query_gate_max_response_bytes_rejects_before_the_checker():
    from rejudge.phase2_query_gate import (
        VISIBLE_HISTORY_CAP_EXCEEDED, CheckerDecision, Phase2QueryGate)

    def checker(request):
        raise AssertionError("must not be consulted for an over-cap query")

    gate = Phase2QueryGate(
        candidate_a="a", candidate_b="b", total_slots=1, checker=checker,
        max_response_bytes=10)
    event = gate.submit("x" * 11)
    assert event.final_decision is CheckerDecision.REJECT
    assert event.mechanical_reasons == (VISIBLE_HISTORY_CAP_EXCEEDED,)
    assert event.decision_source == "mechanical"


# ---------------------------------------------------------------------------------------------
# item 3: the validation gate (scripts/phase3_estimator_validation.py) -- synthetic miss
# ---------------------------------------------------------------------------------------------


class _HugeUsage:
    prompt_tokens = 100000
    completion_tokens = 10


class _HugeChoice:
    class message:
        content = "ok"


class _HugeResp:
    usage = _HugeUsage()
    choices = [_HugeChoice()]


class HugePromptStubSDK:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                return _HugeResp()


def test_validation_gate_catches_a_synthetic_miss(tmp_path):
    ledger_path = tmp_path / "usage.jsonl"
    ac.prepare_usage_ledger(ledger_path, allow_create=True)
    snapshot = ac.load_chained_usage_ledger(ledger_path)
    # price_per_mtok=0.0: a provider-reported prompt_tokens of 100,000 against a tiny real
    # message would otherwise trip the (unrelated) cost-reservation invariant before a
    # terminal event is ever written; zero pricing isolates the estimator check this test
    # actually targets.
    client = ac.RejudgeClient(approved_cap_usd=1000.0, _sdk_client=HugePromptStubSDK(),
                              price_per_mtok=0.0, usage_log_path=str(ledger_path),
                              _ledger_snapshot=snapshot)
    client.complete([{"role": "user", "content": "hi"}], "m", 0.1, 1, 16, kind="verdict",
                    request_metadata={"call_role": "judge_verdict", "cell_key": "c1"})

    role_limits = {
        "model_role_limits": {"m": {"judge_verdict": {"effective_request_max_tokens": 16}}},
        "reasoning_models": {"model_ids": []},
    }
    protocol = {"debate_grid": {"conditions": [{"query_budget": 0}]}}

    events, prefix_binding = ev.freeze_ledger_prefix(ledger_path)
    assert prefix_binding["raw_sha256"] == __import__("hashlib").sha256(
        ledger_path.read_bytes()).hexdigest()
    result = ev.validate(
        events, role_limits=role_limits, max_messages=ev.max_plausible_messages(protocol))

    assert result["calls_checked"] == 1
    assert result["misses"], (
        "a provider-reported prompt_tokens wildly larger than the tiny real message must be "
        "caught as a miss, never silently passed")
    assert result["misses"][0]["actual_prompt_tokens"] == 100000


def test_validation_gate_reports_zero_misses_on_a_realistic_call():
    """Companion to the synthetic-miss test: a call whose provider-reported prompt_tokens is
    plausible for its estimated bytes must NOT be flagged.

    Needs a message with some real bulk (not the tiny "hi" fixture used elsewhere in this file):
    the conservative lower-bound estimator (see phase3_estimator_validation.py's module
    docstring) subtracts a fixed, PROVEN-upper-bound message-count padding term before it ever
    divides by 3, so a near-empty message floors at E_prompt=512 regardless of its true content
    -- correct (never a false PASS), but too pessimistic to model a "plausible" real call.
    """
    ledger_path = Path(tempfile.mkdtemp()) / "usage.jsonl"
    ac.prepare_usage_ledger(ledger_path, allow_create=True)
    snapshot = ac.load_chained_usage_ledger(ledger_path)

    class PlausibleUsage:
        prompt_tokens = 50
        completion_tokens = 5

    class PlausibleChoice:
        class message:
            content = "ok"

    class PlausibleResp:
        usage = PlausibleUsage()
        choices = [PlausibleChoice()]

    class PlausibleStubSDK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return PlausibleResp()

    client = ac.RejudgeClient(approved_cap_usd=1000.0, _sdk_client=PlausibleStubSDK(),
                              price_per_mtok=0.0, usage_log_path=str(ledger_path),
                              _ledger_snapshot=snapshot)
    client.complete([{"role": "user", "content": "x" * 300}], "m", 0.1, 1, 16, kind="verdict",
                    request_metadata={"call_role": "judge_verdict", "cell_key": "c1"})

    role_limits = {
        "model_role_limits": {"m": {"judge_verdict": {"effective_request_max_tokens": 16}}},
        "reasoning_models": {"model_ids": []},
    }
    protocol = {"debate_grid": {"conditions": [{"query_budget": 0}]}}
    events, _binding = ev.freeze_ledger_prefix(ledger_path)
    result = ev.validate(
        events, role_limits=role_limits, max_messages=ev.max_plausible_messages(protocol))
    assert result["calls_checked"] == 1
    assert result["misses"] == []


# ---------------------------------------------------------------------------------------------
# item 4: the carry-forward replay (scripts/phase3_history_cap_replay.py) -- synthetic violation
# ---------------------------------------------------------------------------------------------


def test_replay_catches_a_synthetic_over_cap_completed_response(tmp_path):
    usage_ledger = tmp_path / "usage.jsonl"
    usage_ledger.write_text(
        json.dumps({"status": "success", "model": "reasoning-judge",
                   "metadata": {"call_role": "judge_query", "cell_key": "c1"}}) + "\n",
        encoding="utf-8")

    call_cache = tmp_path / "call_cache.jsonl"
    over_cap_response = "x" * 6200          # > the 6144 reasoning-class cap
    call_cache.write_text(
        json.dumps({"call_role": "judge_query", "cell_key": "c1", "slot": 1, "attempt": 1,
                   "response": over_cap_response}) + "\n",
        encoding="utf-8")

    role_limits = {"model_role_limits": {
        "reasoning-judge": {"judge_query": {"effective_request_max_tokens": 4096}}}}

    result = hcr.replay(call_cache_path=call_cache, usage_ledger_path=usage_ledger,
                        role_limits=role_limits)
    assert result["responses_checked"] == 1
    assert result["violations"], "an over-cap completed response must be caught, never silent"
    assert result["violations"][0]["cell_key"] == "c1"
    assert result["violations"][0]["observed_bytes"] == 6200
    assert result["violations"][0]["cap_bytes"] == 6144


def test_replay_passes_a_within_cap_completed_response():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        usage_ledger = tmp_path / "usage.jsonl"
        usage_ledger.write_text(
            json.dumps({"status": "success", "model": "base-judge",
                       "metadata": {"call_role": "judge_query", "cell_key": "c2"}}) + "\n",
            encoding="utf-8")
        call_cache = tmp_path / "call_cache.jsonl"
        call_cache.write_text(
            json.dumps({"call_role": "judge_query", "cell_key": "c2", "slot": 1, "attempt": 1,
                       "response": "CLAIM: a short well-formed claim"}) + "\n",
            encoding="utf-8")
        role_limits = {"model_role_limits": {
            "base-judge": {"judge_query": {"effective_request_max_tokens": 256}}}}
        result = hcr.replay(call_cache_path=call_cache, usage_ledger_path=usage_ledger,
                            role_limits=role_limits)
        assert result["responses_checked"] == 1
        assert result["violations"] == []
