import json
import threading
import time

import pytest

from rejudge import api_client as ac

MSGS = [{"role": "user", "content": "hello"}]


class _Usage:
    prompt_tokens = 1000
    completion_tokens = 100


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


def test_accounting_and_cap_abort():
    # cap chosen so the SECOND call's projected spend (actual 1100 spent + ~65 estimated)
    # crosses it: (1100+65)/1e6*1.04 ≈ $0.00121 > $0.0012
    c = ac.RejudgeClient(approved_cap_usd=0.0012, _sdk_client=StubSDK())
    c.complete(MSGS, "m", 0.1, 1, 64)          # 1100 tokens -> $0.001144
    assert c.total_tokens == 1100
    assert 0.001 < c.spent_usd < 0.0013
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
    # est(MSGS, max_tokens=64) = len("hello")//4 + 64 = 1 + 64 = 65 tokens, for EVERY call
    # (same messages/max_tokens both threads use). At price_per_mtok=1.04 (default):
    #   one reservation:  65 tok  -> $0.0000676
    #   two reservations: 130 tok -> $0.0001352
    # cap=0.0001 sits strictly between those two, so exactly one reservation fits under the
    # cap and a second concurrent one cannot. Because the projection check and the reservation
    # increment happen inside the SAME locked critical section, whichever thread acquires the
    # lock first reserves 65 tokens and proceeds; the other then sees total_tokens=65 already
    # reserved, computes projected=130 tok > cap, and aborts -- deterministically exactly one
    # "ok" and one "cap" regardless of scheduling order.
    c = ac.RejudgeClient(approved_cap_usd=0.0001, _sdk_client=SlowStubSDK(delay=0.05),
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


def test_reservation_rolled_back_on_terminal_failure():
    sdk = StubSDK(fail_times=99)
    c = ac.RejudgeClient(approved_cap_usd=1.0, _sdk_client=sdk, max_retries=1,
                         _sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        c.complete(MSGS, "m", 0.1, 1, 64)
    assert c.total_tokens == 0        # reservation rolled back, not left dangling
