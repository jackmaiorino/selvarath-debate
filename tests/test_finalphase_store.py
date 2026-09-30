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
