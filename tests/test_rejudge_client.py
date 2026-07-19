import json
import threading
import time

import pytest

from rejudge import api_client as ac

MSGS = [{"role": "user", "content": "hello"}]


class _Usage:
    prompt_tokens = 100
    completion_tokens = 50


class _Choice:
    class message:
        content = "YES"


class _Resp:
    usage = _Usage()
    choices = [_Choice()]


class StubSDK:
    def __init__(self, fail_times=0):
        self.calls = 0
        self.fail_times = fail_times

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                if outer.calls <= outer.fail_times:
                    raise RuntimeError("transient API error")
                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class _MalformedChoicesResp:
    usage = _Usage()
    choices = []                # triggers IndexError on resp.choices[0]


class MalformedChoicesStubSDK:
    """usage is always present (the call is genuinely billed) but choices is empty, so
    content extraction raises -- reproduces a real paid call with a malformed response."""

    def __init__(self):
        self.calls = 0

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                return _MalformedChoicesResp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class SlowStubSDK:
    """Like StubSDK but create() sleeps briefly, so two concurrent complete() calls are
    both genuinely in-flight (post-reservation) at the same time."""

    def __init__(self, delay=0.05):
        self.calls = 0
        self.delay = delay

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                time.sleep(outer.delay)
                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_dry_run_is_tagged_and_free():
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    out = c.complete(MSGS, "m", 0.1, 1, 64, kind="oracle")
    assert "DRY RUN" in out or out in ("YES", "NO", "NOT ADDRESSED")
    v = c.complete(MSGS, "m", 0.1, 1, 64, kind="verdict")
    assert "DRY RUN" in v and "VERDICT:" in v
    q = c.complete(MSGS, "m", 0.1, 1, 64, kind="query")
    assert q.startswith("CLAIM:")


def test_dry_run_invalid_kind_raises():
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    with pytest.raises(ValueError):
        c.complete(MSGS, "m", 0.1, 1, 64, kind="bogus")


def test_real_provider_client_is_factory_only_even_with_a_ledger(tmp_path):
    with pytest.raises(ValueError, match="create_accounted_client"):
        ac.RejudgeClient(approved_cap_usd=1.0)
    with pytest.raises(ValueError, match="create_accounted_client"):
        ac.RejudgeClient(
            approved_cap_usd=1.0, strict_model_pricing=True,
            model_prices={"m": {"in": 1.0, "out": 1.0}},
            usage_log_path=tmp_path / "usage.jsonl")


def test_live_client_construction_fails_fast_without_together_api_key(monkeypatch):
    # A live (non-dry-run) client must refuse to construct the real SDK client before any
    # network attempt when TOGETHER_API_KEY is missing. dry_run=True is used only to get a
    # cheaply-constructible instance (its constructor invariants are the least demanding);
    # _client() itself does not gate on dry_run, so this exercises the same fail-fast guard
    # a live client would hit.
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    with pytest.raises(ValueError, match="TOGETHER_API_KEY"):
        c._client()


def test_live_client_construction_fails_fast_with_blank_together_api_key(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "   ")
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    with pytest.raises(ValueError, match="TOGETHER_API_KEY"):
        c._client()


def test_dry_run_paths_never_hit_the_together_api_key_guard(monkeypatch):
    # dry_run paths must remain unaffected: complete() returns canned output without ever
    # calling _client(), so a missing/blank key never surfaces.
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    out = c.complete(MSGS, "m", 0.1, 1, 64, kind="oracle")
    assert "DRY RUN" in out or out in ("YES", "NO", "NOT ADDRESSED")


def test_accounting_and_cap_abort():
    reservation = ac._estimate_tokens(MSGS, 64) / 1_000_000 * 1.04
    actual = 150 / 1_000_000 * 1.04
    cap = actual + reservation / 2
    c = ac.RejudgeClient(approved_cap_usd=cap, _sdk_client=StubSDK())
    c.complete(MSGS, "m", 0.1, 1, 64)
    assert c.total_tokens == 150
    assert c.spent_usd == pytest.approx(actual)
    with pytest.raises(ac.CapExceededError):
        c.complete(MSGS, "m", 0.1, 1, 64)      # projected spend crosses the cap BEFORE calling


def test_retry_then_success(tmp_path):
    log = tmp_path / "errors.jsonl"
    sdk = StubSDK(fail_times=2)
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk,
                         error_log_path=str(log), _sleep=lambda s: None)
    assert c.complete(MSGS, "m", 0.1, 1, 64) == "YES"
    assert sdk.calls == 3
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["error"].startswith("transient")


def test_retries_exhausted_raises(tmp_path):
    sdk = StubSDK(fail_times=99)
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, max_retries=2,
                         error_log_path=str(tmp_path / "e.jsonl"), _sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        c.complete(MSGS, "m", 0.1, 1, 64)


def test_context_guard():
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=StubSDK(), max_context_tokens=50)
    big = [{"role": "user", "content": "x" * 4000}]      # ~1000 tokens est.
    with pytest.raises(ac.ContextGuardError):
        c.complete(big, "m", 0.1, 1, 64)


def test_cap_reservation_is_atomic_across_threads():
    reservation = ac._estimate_tokens(MSGS, 64) / 1_000_000 * 1.04
    # The cap is strictly between one and two simultaneous conservative reservations.
    c = ac.RejudgeClient(approved_cap_usd=reservation * 1.5,
                         _sdk_client=SlowStubSDK(delay=0.05),
                         _sleep=lambda s: None)
    results = []

    def call():
        try:
            c.complete(MSGS, "m", 0.1, 1, 64)
            results.append("ok")
        except ac.CapExceededError:
            results.append("cap")

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["cap", "ok"]


def test_failed_attempts_remain_reserved_as_unknown_charges():
    sdk = StubSDK(fail_times=99)
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, max_retries=1,
                         _sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        c.complete(MSGS, "m", 0.1, 1, 64)
    # A timeout/transport exception does not prove the provider did no inference. Keep one
    # conservative reservation per attempt until provider billing can reconcile it.
    estimated = ac._estimate_tokens(MSGS, 64)
    assert c.total_tokens == estimated * 2
    assert c.uncertain_tokens == estimated * 2
    assert c.uncertain_spend_usd == pytest.approx(estimated * 2 / 1_000_000 * 1.04)
    assert [e["status"] for e in c.usage_events] == [
        "reserved", "unknown_charge", "reserved", "unknown_charge"]


def test_malformed_choices_after_charge_is_terminal_not_retried():
    # usage is present (150 tokens, genuinely billed) but choices=[] makes content
    # extraction raise. This must NOT retry -- retrying a malformed-response condition
    # would fire another paid call -- and the real spend must be reconciled, not rolled
    # back, since the money was actually spent.
    sdk = MalformedChoicesStubSDK()
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, max_retries=3,
                         _sleep=lambda s: None)
    with pytest.raises(RuntimeError) as exc_info:
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert not isinstance(exc_info.value, ac.CapExceededError)
    assert "malformed API response after successful charge" in str(exc_info.value)
    assert sdk.calls == 1             # exactly one SDK call -- no retry
    assert c.total_tokens == 150      # actual charged usage, reconciled not rolled back


def test_retry_rechecks_cap_and_raises_cap_exceeded_not_runtime_error():
    # Simulate a concurrent call (sharing the same client, as the runner's thread pool
    # does) reconciling real spend that pushes total_tokens past the cap while THIS call
    # is sleeping between retries. The retry loop must re-check accounted spend before
    # the next attempt and raise CapExceededError -- not blindly keep retrying into a
    # generic RuntimeError once max_retries is exhausted (the original cap blow-through).
    sdk = StubSDK(fail_times=99)       # every create() call raises transiently
    c = ac.RejudgeClient(approved_cap_usd=0.002, _sdk_client=sdk, max_retries=3)

    def fake_sleep(seconds):
        # Pretend another in-flight call just reconciled actual spend past the cap.
        with c._lock:
            c._actual_spend_usd = 0.005

    c._sleep = fake_sleep
    with pytest.raises(ac.CapExceededError):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert sdk.calls == 1             # only the first attempt hit the network; the cap
                                       # check aborts before a second network call


def test_model_specific_input_output_pricing_and_usage_log(tmp_path):
    usage_log = tmp_path / "usage.jsonl"
    prices = {"mixed": {"in": 0.2, "out": 1.0}}
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=StubSDK(), model_prices=prices,
        strict_model_pricing=True, usage_log_path=str(usage_log))

    assert c.complete(MSGS, "mixed", 0.1, 7, 64, kind="oracle",
                      request_metadata={"cell_key": "c1"}) == "YES"

    expected = (100 * 0.2 + 50 * 1.0) / 1_000_000
    assert c.actual_spent_usd == pytest.approx(expected)
    assert c.spent_usd == pytest.approx(expected)
    assert c.actual_prompt_tokens == 100
    assert c.actual_completion_tokens == 50
    events = [json.loads(line) for line in usage_log.read_text().splitlines()]
    assert [event["status"] for event in events] == ["reserved", "success"]
    event = events[-1]
    assert event["status"] == "success"
    assert event["model"] == "mixed"
    assert event["cost_usd"] == pytest.approx(expected)
    assert event["metadata"] == {"cell_key": "c1"}


def test_strict_model_pricing_refuses_unfrozen_model_before_call():
    sdk = StubSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, model_prices={},
        strict_model_pricing=True)
    with pytest.raises(ac.UnknownModelPriceError):
        c.complete(MSGS, "unpriced", 0.1, 1, 64)
    assert sdk.calls == 0


@pytest.mark.parametrize("bad_price", [-1, float("nan"), float("inf")])
def test_invalid_model_prices_are_refused_before_call(bad_price):
    sdk = StubSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk,
        model_prices={"bad": {"in": bad_price, "out": 1.0}},
        strict_model_pricing=True)
    with pytest.raises(ac.UnknownModelPriceError, match="finite and non-negative"):
        c.complete(MSGS, "bad", 0.1, 1, 64)
    assert sdk.calls == 0


def test_initial_spend_makes_cap_cumulative_across_invocations():
    sdk = StubSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=0.01, initial_spend_usd=0.00999, _sdk_client=sdk)
    with pytest.raises(ac.CapExceededError):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert sdk.calls == 0


def test_usage_log_summary_is_conservative_and_rejects_corruption(tmp_path):
    log = tmp_path / "usage.jsonl"
    log.write_text("\n".join((
        json.dumps({"status": "success", "cost_usd": 0.2}),
        json.dumps({"status": "charged_malformed", "cost_usd": 0.1}),
        json.dumps({"status": "unknown_charge", "cost_usd": 0.4}),
    )) + "\n")
    summary = ac.summarize_usage_log(log)
    assert summary == {
        "events": 3,
        "actual_spend_usd": pytest.approx(0.3),
        "uncertain_spend_usd": pytest.approx(0.4),
        "accounted_spend_usd": pytest.approx(0.7),
        "unmatched_reservations": 0,
    }

    log.write_text(log.read_text() + "{broken\n")
    with pytest.raises(ValueError, match="invalid usage ledger event"):
        ac.summarize_usage_log(log)


def test_unmatched_pre_call_reservation_survives_a_crash_as_uncertain(tmp_path):
    log = tmp_path / "usage.jsonl"
    log.write_text(json.dumps({
        "status": "reserved", "attempt_id": "crashed", "cost_usd": 0.25,
    }) + "\n", encoding="utf-8")
    summary = ac.summarize_usage_log(log)
    assert summary["actual_spend_usd"] == 0
    assert summary["uncertain_spend_usd"] == pytest.approx(0.25)
    assert summary["accounted_spend_usd"] == pytest.approx(0.25)
    assert summary["unmatched_reservations"] == 1


@pytest.mark.parametrize("status,cost,match", [
    ("success", 0.2, "exceeds reservation"),
    ("unknown_charge", 0.05, "does not equal reservation"),
])
def test_ledger_replay_rejects_terminal_cost_invariant_breaks(
        tmp_path, status, cost, match):
    log = tmp_path / "usage.jsonl"
    log.write_text("\n".join((
        json.dumps({"status": "reserved", "attempt_id": "a", "cost_usd": 0.1}),
        json.dumps({"status": status, "attempt_id": "a", "cost_usd": cost}),
    )) + "\n", encoding="utf-8")
    with pytest.raises(ac.UsageLedgerError, match=match):
        ac.summarize_usage_log(log)


def test_fractional_provider_usage_is_unknown_not_truncated():
    class FractionalUsage:
        prompt_tokens = 100.5
        completion_tokens = 2

    class FractionalResponse:
        usage = FractionalUsage()
        choices = _Resp.choices

    class FractionalSDK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return FractionalResponse()

    client = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=FractionalSDK(), max_retries=0)
    with pytest.raises(RuntimeError, match="API call failed"):
        client.complete(MSGS, "m", 0.0, 1, 8)
    assert client.actual_spent_usd == 0
    assert client.uncertain_spend_usd > 0
    assert client.usage_events[-1]["status"] == "unknown_charge"


class StreamingOnlySDK:
    """Rejects non-streaming calls like Qwen3.7-Plus; streams three chunks otherwise."""

    def __init__(self):
        self.calls = []

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if not kwargs.get("stream"):
                    raise RuntimeError(
                        'Error code: 400 - {"error": {"code": "streaming_required", '
                        '"message": "This model only supports streaming."}}')

                class _Delta:
                    def __init__(self, c):
                        self.content = c

                class _Chunk:
                    def __init__(self, c, usage=None):
                        class _C:
                            delta = _Delta(c)
                        self.choices = [_C()] if c is not None else []
                        self.usage = usage

                class _Usage:
                    prompt_tokens = 100
                    completion_tokens = 50

                return iter([_Chunk("YE"), _Chunk("S"), _Chunk(None, usage=_Usage())])

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_streaming_only_endpoint_auto_fallback():
    sdk = StreamingOnlySDK()
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, _sleep=lambda s: None)
    out = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out == "YES"
    assert c.total_tokens == 150                       # usage from final chunk
    assert sdk.calls[0].get("stream") is None or sdk.calls[0].get("stream") is False or "stream" not in sdk.calls[0]
    assert sdk.calls[1]["stream"] is True
    assert sdk.calls[1]["stream_options"] == {"include_usage": True}
    # second call skips the failed non-streaming probe entirely
    out2 = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out2 == "YES"
    assert len(sdk.calls) == 3                         # 1 failed + 1 stream + 1 stream


def test_reasoning_models_get_max_tokens_floor():
    captured = {}

    class CaptureSDK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    captured.update(kwargs)
                    return _Resp()

    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=CaptureSDK())
    c.complete(MSGS, "Qwen/Qwen3.5-9B", 0.1, 1, 256)
    assert captured["max_tokens"] == 4096          # floored for reasoning models
    c.complete(MSGS, "meta-llama/Llama-3.3-70B-Instruct-Turbo", 0.1, 1, 256)
    assert captured["max_tokens"] == 256           # unchanged for standard models
    c.complete(MSGS, "openai/gpt-oss-120b", 0.1, 1, 8192)
    assert captured["max_tokens"] == 8192          # floor never lowers an explicit larger value


class CaptureSDK:
    """Records every kwargs dict passed to create() and returns a canned response."""

    def __init__(self):
        self.calls: list[dict] = []

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


# --- 1. SILENT FLOOR -> FAIL-CLOSED ASSERTION ---------------------------------------------------

def test_strict_reasoning_mode_raises_instead_of_flooring():
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, require_explicit_reasoning_max_tokens=True)
    with pytest.raises(ac.ReasoningMaxTokensError, match="google/gemma-4-31B-it"):
        c.complete(MSGS, "google/gemma-4-31B-it", 0.1, 1, 256)
    assert sdk.calls == []           # refused before any provider call


def test_strict_reasoning_mode_allows_explicit_max_tokens_at_or_above_floor():
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, require_explicit_reasoning_max_tokens=True)
    out = c.complete(MSGS, "openai/gpt-oss-120b", 0.1, 1, 4096)
    assert out == "YES"
    assert sdk.calls[0]["max_tokens"] == 4096       # sent exactly as requested, never floored


def test_strict_reasoning_mode_never_floors_non_frozen_models():
    # A model that matches the legacy prefix set but is NOT one of the exact three frozen
    # Phase-2 reasoning models must neither be floored nor rejected in strict mode.
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, require_explicit_reasoning_max_tokens=True)
    out = c.complete(MSGS, "Qwen/Qwen3.5-9B", 0.1, 1, 128)
    assert out == "YES"
    assert sdk.calls[0]["max_tokens"] == 128


def test_legacy_reasoning_floor_is_unchanged_by_default():
    sdk = CaptureSDK()
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk)
    assert c.require_explicit_reasoning_max_tokens is False
    out = c.complete(MSGS, "google/gemma-4-31B-it", 0.1, 1, 256)
    assert out == "YES"
    assert sdk.calls[0]["max_tokens"] == 4096       # still silently floored


# --- 2. STRICT PER-MODEL CONTEXT GUARD -----------------------------------------------------------

def test_strict_context_mode_refuses_unknown_model():
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, strict_context_mode=True,
        model_context_limits={"known-model": 131072})
    with pytest.raises(ac.ContextGuardError, match="no context ceiling configured"):
        c.complete(MSGS, "unknown-model", 0.1, 1, 64)
    assert sdk.calls == []


def test_strict_context_mode_refuses_model_missing_from_mapping():
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, strict_context_mode=True,
        model_context_limits={})
    with pytest.raises(ac.ContextGuardError, match="no context ceiling configured"):
        c.complete(MSGS, "m", 0.1, 1, 64)


@pytest.mark.parametrize("bad_ceiling", [0, -1, 3.5, True])
def test_strict_context_mode_refuses_nonpositive_or_noninteger_ceiling(bad_ceiling):
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, strict_context_mode=True,
        model_context_limits={"m": bad_ceiling})
    with pytest.raises(ac.ContextGuardError, match="invalid context ceiling"):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert sdk.calls == []


def test_strict_context_mode_has_no_fallback_to_flat_ceiling():
    # max_context_tokens=1 would normally be the flat ceiling; strict mode must never fall
    # back to it even though it is explicitly set.
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, strict_context_mode=True,
        max_context_tokens=1, model_context_limits={"m": 131072})
    out = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out == "YES"                              # guarded by 131072, not the flat 1


def test_strict_context_guard_applies_after_reasoning_floor_resolution():
    # The reasoning floor raises max_tokens to 4096 for a frozen reasoning model. The context
    # check must see that *resolved* value, not the small caller-supplied one.
    # _estimate_usage's fixed per-message/prompt overhead means the true estimate for MSGS at
    # max_tokens=4096 is a bit above 4096 (roughly 4096 + ~105 of framing overhead) -- assert
    # against the client's own estimator rather than hardcoding that overhead here.
    estimated_at_floor = ac._estimate_tokens(MSGS, 4096)

    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, require_explicit_reasoning_max_tokens=False,
        strict_context_mode=True,
        model_context_limits={"google/gemma-4-31B-it": estimated_at_floor})
    out = c.complete(MSGS, "google/gemma-4-31B-it", 0.1, 1, 64)
    assert out == "YES"

    c2 = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=CaptureSDK(), require_explicit_reasoning_max_tokens=False,
        strict_context_mode=True,
        model_context_limits={"google/gemma-4-31B-it": estimated_at_floor - 1})
    with pytest.raises(ac.ContextGuardError):
        c2.complete(MSGS, "google/gemma-4-31B-it", 0.1, 1, 64)  # floored to 4096, 1 over ceiling


def test_legacy_context_mode_is_unchanged_flat_131072_default():
    sdk = CaptureSDK()
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk)
    assert c.strict_context_mode is False
    assert c.max_context_tokens == 131072


# --- 3. TRANSPORT RETRY PIN -----------------------------------------------------------------------

def test_retry_count_can_be_pinned_to_three_at_most_four_attempts():
    sdk = StubSDK(fail_times=99)
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, max_retries=3, _sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert sdk.calls == 4            # 1 initial attempt + 3 retries, never more


def test_default_retry_count_is_unchanged():
    c = ac.RejudgeClient(approved_cap_usd=1.0, dry_run=True)
    assert c.max_retries == 4


# --- 4. STREAMING PIN ------------------------------------------------------------------------------

def test_streaming_pinned_model_uses_streaming_from_first_attempt():
    sdk = StreamingOnlySDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, _sleep=lambda s: None,
        streaming_pinned_models=frozenset({"m"}))
    out = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out == "YES"
    assert len(sdk.calls) == 1                       # no wasted non-streaming probe
    assert sdk.calls[0]["stream"] is True
    assert sdk.calls[0]["stream_options"] == {"include_usage": True}


def test_unpinned_model_still_uses_reactive_streaming_discovery():
    sdk = StreamingOnlySDK()
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, _sleep=lambda s: None)
    out = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out == "YES"
    assert len(sdk.calls) == 2                        # failed probe + successful stream
    assert "stream" not in sdk.calls[0]


# --- 5. PER-MODEL EXTRA REQUEST FIELDS -------------------------------------------------------------

def test_extra_request_fields_are_merged_into_the_request():
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk,
        extra_request_fields={"openai/gpt-oss-120b": {"reasoning_effort": "medium"}})
    out = c.complete(MSGS, "openai/gpt-oss-120b", 0.1, 1, 64)
    assert out == "YES"
    assert sdk.calls[0]["reasoning_effort"] == "medium"


def test_extra_request_fields_do_not_leak_to_other_models():
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk,
        extra_request_fields={"openai/gpt-oss-120b": {"reasoning_effort": "medium"}})
    c.complete(MSGS, "some-other-model", 0.1, 1, 64)
    assert "reasoning_effort" not in sdk.calls[0]


def test_extra_request_fields_are_included_in_the_request_fields_hash(tmp_path):
    usage_log = tmp_path / "usage.jsonl"
    sdk = CaptureSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, usage_log_path=str(usage_log),
        extra_request_fields={"openai/gpt-oss-120b": {"reasoning_effort": "medium"}})
    c.complete(MSGS, "openai/gpt-oss-120b", 0.1, 1, 64)

    c2 = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=CaptureSDK())
    c2.complete(MSGS, "openai/gpt-oss-120b", 0.1, 1, 64)

    events = [json.loads(line) for line in usage_log.read_text().splitlines()]
    success = [e for e in events if e["status"] == "success"][0]
    with_extra_hash = success["response_metadata"]["request_fields_sha256"]

    # The hash for an otherwise-identical call WITHOUT the extra field must differ -- the
    # extra field is part of what gets hashed, not silently dropped from the request identity.
    without_extra_hash = c2.usage_events[-1]["response_metadata"]["request_fields_sha256"]
    assert with_extra_hash != without_extra_hash


def test_extra_request_fields_colliding_with_a_reserved_field_is_rejected_at_construction():
    # A per-model extra field that reuses a base or transport kwarg name must never reach
    # _build_request_kwargs's unconditional merge: it would silently override a value that
    # already passed the reasoning-floor guard, the context-ceiling guard, or the cost-cap
    # reservation. Reject it eagerly, at client construction, before any provider call.
    for reserved_field, value in (
        ("max_tokens", 10), ("model", "expensive-model"),
        ("messages", [{"role": "user", "content": "INJECTED"}]),
        ("temperature", 0.0), ("seed", 999),
        ("stream", True), ("stream_options", {"include_usage": True}),
    ):
        with pytest.raises(ValueError, match=reserved_field):
            ac.RejudgeClient(
                approved_cap_usd=1.0, dry_run=True,
                extra_request_fields={"some-model": {reserved_field: value}})


def test_extra_request_fields_collision_check_reports_all_offending_keys():
    with pytest.raises(ValueError, match="max_tokens.*model|model.*max_tokens"):
        ac.RejudgeClient(
            approved_cap_usd=1.0, dry_run=True,
            extra_request_fields={
                "some-model": {"max_tokens": 1, "model": "other", "reasoning_effort": "medium"}})


def test_extra_request_fields_without_collisions_still_construct_fine():
    # Non-colliding extra fields (the only legacy/current usage) are unaffected by the guard.
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, dry_run=True,
        extra_request_fields={"openai/gpt-oss-120b": {"reasoning_effort": "medium"}})
    assert c.extra_request_fields == {"openai/gpt-oss-120b": {"reasoning_effort": "medium"}}


# --- 6. RESPONSE METADATA ---------------------------------------------------------------------------

def test_response_metadata_is_persisted_when_available():
    class _RichUsage:
        prompt_tokens = 100
        completion_tokens = 50

        class completion_tokens_details:
            reasoning_tokens = 12

    class _RichChoice:
        finish_reason = "stop"

        class message:
            content = "YES"

    class _RichResp:
        usage = _RichUsage()
        choices = [_RichChoice()]
        id = "resp-123"
        model = "returned-model-id"
        system_fingerprint = "fp-abc"

    class RichSDK:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return _RichResp()

    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=RichSDK())
    c.complete(MSGS, "m", 0.1, 1, 64)
    event = c.usage_events[-1]
    meta = event["response_metadata"]
    assert meta["returned_model_id"] == "returned-model-id"
    assert meta["response_id"] == "resp-123"
    assert meta["finish_reason"] == "stop"
    assert meta["system_fingerprint_if_present"] == "fp-abc"
    assert meta["prompt_tokens"] == 100
    assert meta["completion_tokens"] == 50
    assert meta["reasoning_tokens_if_returned"] == 12
    assert isinstance(meta["request_fields_sha256"], str) and len(meta["request_fields_sha256"]) == 64


def test_response_metadata_missing_attributes_are_null_not_guessed():
    # _Resp (used throughout this file) exposes only .usage and .choices -- no id, model,
    # system_fingerprint, finish_reason, or reasoning-token breakdown.
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=StubSDK())
    c.complete(MSGS, "m", 0.1, 1, 64)
    meta = c.usage_events[-1]["response_metadata"]
    assert meta["returned_model_id"] is None
    assert meta["response_id"] is None
    assert meta["finish_reason"] is None
    assert meta["system_fingerprint_if_present"] is None
    assert meta["reasoning_tokens_if_returned"] is None
    # usage-derived fields ARE available on the stub and must still be populated.
    assert meta["prompt_tokens"] == 100
    assert meta["completion_tokens"] == 50


def test_response_metadata_is_persisted_on_malformed_response_too():
    sdk = MalformedChoicesStubSDK()
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, max_retries=0)
    with pytest.raises(RuntimeError, match="malformed API response"):
        c.complete(MSGS, "m", 0.1, 1, 64)
    event = c.usage_events[-1]
    assert event["status"] == "charged_malformed"
    meta = event["response_metadata"]
    assert meta["prompt_tokens"] == 100          # usage was readable even though content was not
    assert isinstance(meta["request_fields_sha256"], str)


# --- 7. halt_on_unknown_charge -----------------------------------------------------------------

def test_unknown_charge_on_attempt_one_halts_with_no_attempt_two():
    sdk = StubSDK(fail_times=99)          # every attempt raises a generic transient error
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, max_retries=3, _sleep=lambda s: None,
        halt_on_unknown_charge=True)
    with pytest.raises(ac.UnknownChargeHalt):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert sdk.calls == 1                 # halted immediately; no second attempt was made
    event = c.usage_events[-1]
    assert event["status"] == "unknown_charge"


def test_unknown_charge_halt_latches_the_unknown_charge_before_raising():
    sdk = StubSDK(fail_times=99)
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, max_retries=3, _sleep=lambda s: None,
        halt_on_unknown_charge=True)
    with pytest.raises(ac.UnknownChargeHalt):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert c.uncertain_spend_usd > 0      # the uncertain charge is durably accounted, not lost


def test_unknown_charge_on_malformed_usage_also_halts():
    # The second _mark_unknown call site (usage itself unreadable) must halt too.
    class _BadUsageResp:
        usage = None
        choices = []

    class BadUsageSDK:
        def __init__(self):
            self.calls = 0
            outer = self

            class _Completions:
                def create(self, **kwargs):
                    outer.calls += 1
                    return _BadUsageResp()

            class _Chat:
                completions = _Completions()

            self.chat = _Chat()

    sdk = BadUsageSDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, max_retries=3, _sleep=lambda s: None,
        halt_on_unknown_charge=True)
    with pytest.raises(ac.UnknownChargeHalt):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert sdk.calls == 1


def test_streaming_required_probe_release_still_retries_under_halt_on_unknown_charge():
    # A streaming_required capability-negotiation probe is released_no_charge, never
    # unknown_charge, so it must keep retrying through the required transport even with
    # halt_on_unknown_charge=True.
    sdk = StreamingOnlySDK()
    c = ac.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=sdk, _sleep=lambda s: None,
        halt_on_unknown_charge=True)
    out = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out == "YES"
    assert len(sdk.calls) == 2                        # failed probe (released) + successful stream
    statuses = [event["status"] for event in c.usage_events]
    assert "released_no_charge" in statuses
    assert "unknown_charge" not in statuses


def test_halt_on_unknown_charge_default_is_legacy_retry_behavior():
    sdk = StubSDK(fail_times=99)
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, max_retries=3,
                          _sleep=lambda s: None)
    assert c.halt_on_unknown_charge is False
    with pytest.raises(RuntimeError, match="API call failed after"):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert sdk.calls == 4                 # 1 initial attempt + 3 retries, unchanged from before


def test_halt_on_unknown_charge_true_still_succeeds_when_the_first_attempt_succeeds():
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=StubSDK(), halt_on_unknown_charge=True)
    out = c.complete(MSGS, "m", 0.1, 1, 64)
    assert out == "YES"
