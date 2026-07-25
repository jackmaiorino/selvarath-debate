"""The client wrapper that puts the call cache in front of the provider.

The canary runner drives the ordinary ``complete()`` interface, so the cache is applied by
wrapping the client rather than by threading a cache argument through debate_gen, judge_loop
and the gate. A cached call never reaches the provider and never touches the spend ledger,
which is what makes a resumed cell free.
"""
import pytest

from rejudge.phase2_call_cache import CallCache, CallReplayMismatch
from rejudge.phase2_caching_client import CachingClient, UncacheableCall


class RecordingClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or ["provider response"])
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict",
                 **kwargs):
        self.calls.append({"messages": messages, "model": model, "kind": kind, **kwargs})
        return self.responses.pop(0) if self.responses else "provider response"


META = {"cell_key": "canary|sequential_b2|CN-001", "call_role": "judge_query",
        "query_index": 0, "attempt": 1}


def _complete(client, content="ask", metadata=None):
    return client.complete([{"role": "user", "content": content}], "m", 0.3, 7, 256,
                           kind="query", request_metadata=dict(metadata or META))


def test_a_first_call_reaches_the_provider_and_is_cached(tmp_path):
    inner = RecordingClient(["CLAIM: one"])
    client = CachingClient(inner, CallCache(tmp_path / "calls.jsonl"))
    assert _complete(client) == "CLAIM: one"
    assert len(inner.calls) == 1


def test_a_repeated_call_is_served_from_cache_without_touching_the_provider(tmp_path):
    inner = RecordingClient(["CLAIM: one"])
    cache = CallCache(tmp_path / "calls.jsonl")
    assert _complete(CachingClient(inner, cache)) == "CLAIM: one"
    # A fresh wrapper over a fresh cache instance, as a resumed process would build.
    resumed = CachingClient(RecordingClient(["SHOULD NOT BE CALLED"]),
                            CallCache(tmp_path / "calls.jsonl"))
    assert _complete(resumed) == "CLAIM: one"
    assert resumed.inner.calls == []


def test_distinct_slots_are_cached_separately(tmp_path):
    inner = RecordingClient(["one", "two"])
    client = CachingClient(inner, CallCache(tmp_path / "calls.jsonl"))
    assert _complete(client, "a", {**META, "query_index": 0}) == "one"
    assert _complete(client, "b", {**META, "query_index": 1}) == "two"
    assert len(inner.calls) == 2


def test_a_retry_attempt_is_a_distinct_call(tmp_path):
    inner = RecordingClient(["rejected one", "accepted one"])
    client = CachingClient(inner, CallCache(tmp_path / "calls.jsonl"))
    assert _complete(client, "a", {**META, "attempt": 1}) == "rejected one"
    assert _complete(client, "b", {**META, "attempt": 2}) == "accepted one"
    assert len(inner.calls) == 2


def test_an_edited_prompt_for_a_cached_call_halts(tmp_path):
    inner = RecordingClient(["one"])
    client = CachingClient(inner, CallCache(tmp_path / "calls.jsonl"))
    _complete(client, "original")
    with pytest.raises(CallReplayMismatch):
        _complete(client, "edited")


def test_a_call_without_a_cell_key_is_refused(tmp_path):
    # An uncacheable call in a resumable run is a silent double-spend on every resume.
    client = CachingClient(RecordingClient(), CallCache(tmp_path / "calls.jsonl"))
    with pytest.raises(UncacheableCall):
        _complete(client, metadata={"call_role": "judge_query"})


def test_a_call_without_a_call_role_is_refused(tmp_path):
    client = CachingClient(RecordingClient(), CallCache(tmp_path / "calls.jsonl"))
    with pytest.raises(UncacheableCall):
        _complete(client, metadata={"cell_key": "c"})


def test_missing_request_metadata_is_refused(tmp_path):
    client = CachingClient(RecordingClient(), CallCache(tmp_path / "calls.jsonl"))
    with pytest.raises(UncacheableCall):
        client.complete([{"role": "user", "content": "x"}], "m", 0.3, 7, 256, kind="query")


def test_the_wrapper_proxies_dry_run(tmp_path):
    inner = RecordingClient()
    inner.dry_run = True
    assert CachingClient(inner, CallCache(tmp_path / "calls.jsonl")).dry_run is True


def test_a_provider_failure_caches_nothing(tmp_path):
    class Failing(RecordingClient):
        def complete(self, *args, **kwargs):
            raise TimeoutError("provider timeout")

    path = tmp_path / "calls.jsonl"
    client = CachingClient(Failing(), CallCache(path))
    with pytest.raises(TimeoutError):
        _complete(client)
    # Nothing committed, so a later attempt is free to go live rather than replaying a
    # response that never existed.
    assert not path.exists() or path.read_text(encoding="utf-8").strip() == ""


def test_the_wrapper_reports_replay_accounting(tmp_path):
    cache = CallCache(tmp_path / "calls.jsonl")
    client = CachingClient(RecordingClient(["one"]), cache)
    _complete(client)
    _complete(client)
    assert (cache.replayed, cache.missed) == (1, 1)
