import re
import weakref
from types import SimpleNamespace

import pytest

from finalphase import providers, store
from finalphase.providers import Request, Response, TransportError
from finalphase.store import CapExceeded, Store


def _req(i, model="luna", text="hello"):
    return Request(f"id{i}", model, "sys", (("user", text),), 100)


@pytest.fixture
def fake_live(monkeypatch):
    calls = []

    def live(req, m):
        calls.append(req.custom_id)
        return Response(req.custom_id, m.model_id, "ok", "answer", input_tokens=10, output_tokens=5, cost=0.01)

    monkeypatch.setitem(store.LIVE, "openai", live)
    return calls


def test_finished_call_is_not_resent(tmp_path, fake_live):
    s = Store(tmp_path / "s.db", "t", 10)
    out = s.run([_req(1), _req(2)])
    assert set(out) == {"id1", "id2"} and len(fake_live) == 2
    s2 = Store(tmp_path / "s.db", "t", 10)
    s2.run([_req(1), _req(2)])
    assert len(fake_live) == 2
    assert s2.spent("t") == pytest.approx(0.02)


def test_changed_request_under_same_id_is_refused(tmp_path, fake_live):
    s = Store(tmp_path / "s.db", "t", 10)
    s.run([_req(1)])
    with pytest.raises(ValueError):
        s.run([_req(1, text="different")])


def test_cap_blocks_dispatch(tmp_path, fake_live):
    s = Store(tmp_path / "s.db", "t", 0.0000001)
    with pytest.raises(CapExceeded):
        s.run([_req(1)])
    assert fake_live == []


def test_transport_errors_retry_then_succeed(tmp_path, monkeypatch):
    n = {"k": 0}

    def flaky(req, m):
        n["k"] += 1
        if n["k"] < 3:
            raise TransportError("boom")
        return Response(req.custom_id, m.model_id, "ok", "x")

    monkeypatch.setitem(store.LIVE, "openai", flaky)
    monkeypatch.setattr(store.time, "sleep", lambda s: None)
    s = Store(tmp_path / "s.db", "t", 10)
    assert s.call_one(_req(1)).text == "x"
    assert s._attempts("id1") == 3


def test_measured_failures_are_final(tmp_path, monkeypatch):
    seen = []

    def refuse(req, m):
        seen.append(1)
        return Response(req.custom_id, m.model_id, "refusal", "")

    monkeypatch.setitem(store.LIVE, "openai", refuse)
    s = Store(tmp_path / "s.db", "t", 10)
    s.run([_req(1)])
    s.run([_req(1)])
    assert len(seen) == 1 and s.get("id1").status == "refusal"


def test_batch_reconnects_after_restart(tmp_path, monkeypatch):
    submitted, polls = [], {"n": 0}

    def submit(reqs, m, label):
        submitted.append([r.custom_id for r in reqs])
        return "b1"

    def poll(bid):
        polls["n"] += 1
        return ("ended" if polls["n"] > 1 else "in_progress"), {}

    def collect(bid, m):
        return {"id1": Response("id1", m.model_id, "ok", "a", batch=True), "id2": TransportError("expired")}

    monkeypatch.setitem(store.BATCH, "openai", (submit, poll, collect))
    monkeypatch.setitem(store.LIVE, "openai", lambda r, m: Response(r.custom_id, m.model_id, "ok", "live"))
    s = Store(tmp_path / "s.db", "t", 10)
    out = s.run([_req(1), _req(2)], mode="batch", poll_s=0)
    assert submitted == [["id1", "id2"]]
    assert out["id1"].text == "a" and out["id2"].text == "live"


def test_anthropic_batch_roundtrip_preserves_ids_after_restart(tmp_path, monkeypatch):
    submitted, polls = [], {"n": 0}

    def create(*, requests):
        assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", r["custom_id"]) for r in requests)
        assert len({r["custom_id"] for r in requests}) == len(requests)
        submitted.append(requests)
        return SimpleNamespace(id="b1")

    def retrieve(bid):
        assert bid == "b1"
        polls["n"] += 1
        if polls["n"] == 1:
            raise KeyboardInterrupt
        return SimpleNamespace(processing_status="ended", model_dump=lambda **kw: {"processing_status": "ended"})

    def results(bid):
        for i, request in reversed(list(enumerate(submitted[0]))):
            body = {"content": [{"type": "text", "text": f"answer {i}"}], "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 5}}
            message = SimpleNamespace(model_dump=lambda body=body: body)
            yield SimpleNamespace(custom_id=request["custom_id"],
                                  result=SimpleNamespace(type="succeeded", message=message))

    class Client(SimpleNamespace):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    client = Client(messages=SimpleNamespace(batches=SimpleNamespace(
        create=create, retrieve=retrieve, results=results)))
    monkeypatch.setattr(providers, "_anthropic_client", lambda: client)
    monkeypatch.setitem(store.LIVE, "anthropic", lambda *args: pytest.fail("completed batch must not fall back to live"))
    ids = ["author:W001:fable", "judge:" + "x" * 90 + ":a", "judge:" + "x" * 90 + ":b"]
    requests = [Request(cid, "fable", "sys", (("user", "hello"),), 100) for cid in ids]
    s = Store(tmp_path / "s.db", "author", 10)
    with pytest.raises(KeyboardInterrupt):
        s.run(requests, mode="batch", poll_s=0)
    s.db.close()
    resumed = Store(tmp_path / "s.db", "author", 10)
    out = resumed.run(requests, mode="batch", poll_s=0)
    assert len(submitted) == 1
    assert set(out) == set(ids)
    assert all(out[cid].custom_id == cid and out[cid].text == f"answer {i}" for i, cid in enumerate(ids))
    resumed.run(requests, mode="batch", poll_s=0)
    assert len(submitted) == 1


def test_anthropic_collection_keeps_client_alive_until_stream_is_consumed(monkeypatch):
    closed = []

    class Client:
        def __init__(self):
            ref = weakref.ref(self)

            def results(bid):
                assert ref() is not None and not closed
                body = {"content": [{"type": "text", "text": "answer"}], "stop_reason": "end_turn",
                        "usage": {"input_tokens": 10, "output_tokens": 5}}
                yield SimpleNamespace(custom_id="request1", result=SimpleNamespace(type="succeeded",
                    message=SimpleNamespace(model_dump=lambda: body)))

            self.messages = SimpleNamespace(batches=SimpleNamespace(results=results))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def __del__(self):
            if not closed:
                closed.append(True)

    monkeypatch.setattr(providers, "_anthropic_client", Client)
    out = providers.anthropic_batch_collect("b1", store.spec("fable"))
    assert out["request1"].text == "answer" and closed


def test_openai_parse_incomplete_is_truncated():
    m = store.spec("luna")
    r = providers._openai_parse("x", m, {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                                         "output": [], "usage": {"input_tokens": 100, "output_tokens": 50}}, batch=True)
    assert r.status == "truncated" and r.cost == pytest.approx((100 * 0.2 + 50 * 1.2) / 1e6 / 2)


def test_anthropic_parse_counts_cache_tokens():
    m = store.spec("opus")
    r = providers._anthropic_parse("x", m, {"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "YES"}],
                                            "stop_reason": "end_turn",
                                            "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 3}}, False)
    assert r.text == "YES" and r.input_tokens == 1010 and r.cached_tokens == 1000


def test_anthropic_cache_split_keeps_text_identical():
    m = store.spec("opus")
    req = Request("x", "opus", "sys", (("user", "WORLD DOCUMENT:\nabc\n\nQUERY: q"),), 100, cache_first=len("WORLD DOCUMENT:\nabc\n\n"))
    p = providers._anthropic_params(req, m)
    blocks = p["messages"][0]["content"]
    assert "".join(b["text"] for b in blocks) == req.messages[0][1]
    assert "cache_control" in blocks[0] and "cache_control" not in blocks[1]
    assert p["output_config"] == {"effort": "medium"}


def test_billing_error_halts(tmp_path, monkeypatch):
    def broke(req, m):
        raise providers.BillingError("no credits")

    monkeypatch.setitem(store.LIVE, "openai", broke)
    s = Store(tmp_path / "s.db", "t", 10)
    with pytest.raises(providers.BillingError):
        s.run([_req(1)])
