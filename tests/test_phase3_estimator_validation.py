"""Amendment 4 (2026-08-19), package item 3: the estimator-validation gate.

Builds small, real, hash-chained usage ledgers (via ``rejudge.api_client.RejudgeClient`` itself,
never hand-typed JSON) and checks that ``scripts.phase3_estimator_validation`` freezes them
correctly and enforces the never-underestimate rule.
"""
import json
from pathlib import Path

import pytest

from rejudge import api_client as ac
from scripts.phase3_estimator_validation import (
    EstimatorValidationError,
    build_report,
    conservative_e_prompt,
    conservative_prompt_bytes_lower_bound,
    freeze_ledger_prefix,
    max_plausible_messages,
    reserved_completion_for,
    resolve_effective_max_tokens,
)

MODEL = "m"
ROLE_LIMITS = {
    "model_role_limits": {MODEL: {
        "judge_query": {"effective_request_max_tokens": 64},
    }},
    "reasoning_models": {"model_ids": []},
}
PROTOCOL = {"debate_grid": {"conditions": [
    {"id": "b0", "query_budget": 0}, {"id": "sequential_b8", "query_budget": 8},
]}}


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens=10):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeChoiceMessage:
    content = "YES"


class _FakeChoice:
    message = _FakeChoiceMessage()


class _FakeResp:
    def __init__(self, prompt_tokens):
        self.usage = _FakeUsage(prompt_tokens)
        self.choices = [_FakeChoice()]


class _FakeSDK:
    """Returns a caller-controlled ``prompt_tokens`` on every call, real or absurd."""

    def __init__(self, prompt_tokens: int):
        outer = self

        class _Completions:
            def create(self, **kwargs):
                return _FakeResp(outer.prompt_tokens)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()
        self.prompt_tokens = prompt_tokens


def _chained_ledger_client(ledger_path: Path, sdk) -> ac.RejudgeClient:
    ac.prepare_usage_ledger(ledger_path, allow_create=True)
    snapshot = ac.load_chained_usage_ledger(ledger_path)
    return ac.RejudgeClient(
        approved_cap_usd=1_000_000.0, _sdk_client=sdk, usage_log_path=str(ledger_path),
        _ledger_snapshot=snapshot)


def test_max_plausible_messages_matches_the_frozen_protocol_arithmetic():
    # 2 (presentation) + MAX_ATTEMPTS_PER_SLOT(2) * max_query_budget(8) * 3 + 1 (verdict) = 51.
    assert max_plausible_messages(PROTOCOL) == 51


def test_conservative_bound_never_exceeds_the_true_prompt_bytes():
    # estimated_prompt_old = 64 + 32*n + prompt_bytes for the TRUE n; subtracting an n at least
    # as large as the true one can only produce a result <= the true prompt_bytes.
    true_n, true_prompt_bytes = 5, 3000
    estimated_prompt_old = 64 + 32 * true_n + true_prompt_bytes
    bound = conservative_prompt_bytes_lower_bound(estimated_prompt_old, max_messages=51)
    assert bound <= true_prompt_bytes
    assert conservative_e_prompt(estimated_prompt_old, 51) <= (
        (true_prompt_bytes // 3 + 1) + 512 + 1)  # loose sanity ceiling, not exact


def test_resolve_effective_max_tokens_and_reserved_completion_for_non_reasoning():
    effective = resolve_effective_max_tokens(ROLE_LIMITS, MODEL, "judge_query")
    assert effective == 64
    assert reserved_completion_for(ROLE_LIMITS, MODEL, effective) == 64


def test_resolve_effective_max_tokens_honors_the_oracle_verification_alias():
    role_limits = {
        "model_role_limits": {MODEL: {"oracle": {"effective_request_max_tokens": 32}}},
        "reasoning_models": {"model_ids": []},
    }
    assert resolve_effective_max_tokens(role_limits, MODEL, "oracle_verification") == 32


def test_reserved_completion_scales_for_reasoning_models():
    role_limits = {
        "model_role_limits": {MODEL: {"judge_query": {"effective_request_max_tokens": 4096}}},
        "reasoning_models": {"model_ids": [MODEL]},
    }
    assert reserved_completion_for(role_limits, MODEL, 4096) == (
        4096 * ac.COMPLETION_RESERVE_MULTIPLIER)


def test_freeze_ledger_prefix_binding_is_exact(tmp_path):
    ledger_path = tmp_path / "usage.jsonl"
    client = _chained_ledger_client(ledger_path, _FakeSDK(prompt_tokens=100))
    client.complete([{"role": "user", "content": "hi"}], MODEL, 0.1, 1, 64, kind="query",
                    request_metadata={"call_role": "judge_query"})

    events, prefix = freeze_ledger_prefix(ledger_path)
    raw = ledger_path.read_bytes()
    import hashlib
    assert prefix["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert prefix["raw_byte_count"] == len(raw)
    assert prefix["last_sequence"] == max(int(e["sequence"]) for e in
                                         [json.loads(l) for l in raw.decode("utf-8").splitlines()
                                          if l.strip()])
    assert prefix["event_count"] == len(events) + 1  # +1 for the genesis event this excludes
    assert events[-1]["status"] == "success"


def test_validation_passes_for_a_small_real_call(tmp_path):
    # A genuinely sized prompt (not a 2-byte "hi"): max_plausible_messages(PROTOCOL) is 51 (its
    # b8 query_budget), so conservative_prompt_bytes_lower_bound subtracts a 51-message padding
    # term from the reservation before this call's E_prompt is ever computed -- for a 2-byte
    # prompt that clamps to 0 and floors the conservative E_prompt at exactly 512, which no
    # nonzero actual_prompt_tokens can clear (see test_validation_catches_a_synthetic_miss,
    # which relies on exactly that). A real content payload survives the same subtraction with
    # margin to spare, matching what every real (non-toy) phase-3 call looks like.
    ledger_path = tmp_path / "usage.jsonl"
    client = _chained_ledger_client(ledger_path, _FakeSDK(prompt_tokens=50))
    client.complete([{"role": "user", "content": "x" * 5000}], MODEL, 0.1, 1, 64, kind="query",
                    request_metadata={"call_role": "judge_query"})

    report = build_report(ledger_path, protocol=PROTOCOL, role_limits=ROLE_LIMITS,
                          generated_at="2026-01-01T00:00:00Z")
    assert report["calls_checked"] == 1
    assert report["misses"] == []
    assert report["min_absolute_headroom_tokens"] is not None
    assert report["min_absolute_headroom_tokens"] >= 0


def test_validation_catches_a_synthetic_miss(tmp_path):
    # A tiny real prompt: max_plausible_messages(PROTOCOL)'s 51-message padding term swamps a
    # 2-byte prompt's reservation, clamping the conservative E_prompt at exactly 512 (see
    # conservative_prompt_bytes_lower_bound) -- which no nonzero genuine actual_prompt_tokens can
    # ever clear (required_e_prompt_floor(n) == n + max(512, ceil(0.25*n)) > 512 for any n > 0).
    # A realistic (not absurd) actual_prompt_tokens is enough to demonstrate the gate catching
    # this, and avoids RejudgeClient's own unrelated accounting-invariant guard (which fires
    # first, before this gate ever runs, if actual usage were billed far above what the
    # reservation covers).
    ledger_path = tmp_path / "usage.jsonl"
    client = _chained_ledger_client(ledger_path, _FakeSDK(prompt_tokens=50))
    client.complete([{"role": "user", "content": "hi"}], MODEL, 0.1, 1, 64, kind="query",
                    request_metadata={"call_role": "judge_query"})

    report = build_report(ledger_path, protocol=PROTOCOL, role_limits=ROLE_LIMITS,
                          generated_at="2026-01-01T00:00:00Z")
    assert report["calls_checked"] == 1
    assert len(report["misses"]) == 1
    miss = report["misses"][0]
    assert miss["actual_prompt_tokens"] == 50
    assert miss["e_prompt_conservative"] == 512
    assert miss["required_floor"] == 50 + 512
    assert miss["headroom_tokens"] < 0


def test_unknown_charge_calls_are_reported_separately_never_checked(tmp_path):
    ledger_path = tmp_path / "usage.jsonl"

    class _FailingSDK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("boom")

    client = _chained_ledger_client(ledger_path, _FailingSDK())
    client.max_retries = 0
    client._sleep = lambda s: None
    with pytest.raises(RuntimeError):
        client.complete([{"role": "user", "content": "hi"}], MODEL, 0.1, 1, 64, kind="query",
                        request_metadata={"call_role": "judge_query"})

    report = build_report(ledger_path, protocol=PROTOCOL, role_limits=ROLE_LIMITS,
                          generated_at="2026-01-01T00:00:00Z")
    assert report["calls_checked"] == 0
    assert report["misses"] == []
    assert report["unknown_charge_calls_reported_separately"] == 1


def test_unknown_model_role_pair_refuses(tmp_path):
    ledger_path = tmp_path / "usage.jsonl"
    client = _chained_ledger_client(ledger_path, _FakeSDK(prompt_tokens=10))
    client.complete([{"role": "user", "content": "hi"}], "unmapped-model", 0.1, 1, 64,
                    kind="query", request_metadata={"call_role": "judge_query"})
    with pytest.raises(EstimatorValidationError):
        build_report(ledger_path, protocol=PROTOCOL, role_limits=ROLE_LIMITS,
                     generated_at="2026-01-01T00:00:00Z")
