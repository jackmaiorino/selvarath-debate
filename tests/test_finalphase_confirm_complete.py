import json
import sqlite3

import pytest

from test_finalphase_cli import fake
from test_finalphase_confirm import _pilot_fixture

from finalphase import confirm, confirm_complete as CC, store
from finalphase.providers import TransportError


def _flaky(monkeypatch, failing: set, probe_ok=True):
    """Provider: the listed ids and (optionally) probes raise transport errors; everything else is the fake."""
    sent = []

    def provider(r, m):
        sent.append(r.custom_id)
        if r.custom_id in failing or (r.custom_id.startswith("probe:") and not probe_ok):
            raise TransportError("Error code: 503 - Service unavailable")
        return fake(r, m)

    for p in ("anthropic", "openai", "together"):
        monkeypatch.setitem(store.LIVE, p, provider)
    return sent


def _three_failed(tmp_path, monkeypatch):
    worlds = _pilot_fixture(tmp_path, monkeypatch)
    qs, _ = confirm.sample(tmp_path / "bench")
    ids = tuple(f"judge:{q['question_id']}:{lab}:uncapped:llama70:debate_k0:key_a:verdict"
                for q, lab in ((qs[0], "astra"), (qs[5], "sonnet~astra"), (qs[9], "fable")))
    _flaky(monkeypatch, set(ids))
    with pytest.raises(confirm.ConfirmIncomplete):
        confirm.run(tmp_path, worlds)
    return worlds, ids


def _attempts(tmp_path, ids):
    con = sqlite3.connect(tmp_path / confirm.CONFIRM_DIR / "confirm.db")
    try:
        return {cid: con.execute("SELECT status, attempts FROM calls WHERE custom_id=?", (cid,)).fetchone() for cid in ids}
    finally:
        con.close()


def test_completion_sends_exactly_the_listed_ids_once_and_the_run_then_reads(tmp_path, monkeypatch):
    worlds, ids = _three_failed(tmp_path, monkeypatch)
    assert all(v == ("transport_failed", 2) for v in _attempts(tmp_path, ids).values())
    sent = _flaky(monkeypatch, set())
    waits = []
    rec = CC.complete_transport_failures(tmp_path, worlds, sleep=waits.append, ids=ids)
    assert [x for x in sent if not x.startswith("probe:")] == list(ids)
    assert sum(x.startswith("probe:") for x in sent) == 2
    assert waits == [CC.PROBE_GAP_S, CC.SETTLE_S, CC.DISPATCH_GAP_S, CC.DISPATCH_GAP_S]
    assert rec["unchanged_other_calls"] and rec["disposition"].startswith("complete")
    assert all(v == ("ok", 3) for v in _attempts(tmp_path, ids).values())
    base = tmp_path / confirm.CONFIRM_DIR
    assert json.loads((base / "confirm_summary_r0_original.json").read_text())["decision"]["rule"] == "R0"
    # The unchanged confirmation then replays every k0 call and runs only the checks.
    sent.clear()
    summary = confirm.run(tmp_path, worlds)
    assert summary["decision"]["rule"] == "C1" and summary["technical_checks"]["pass"]
    assert not [x for x in sent if ":debate_k0:" in x or x.startswith(("debate:", "compliance:"))]


def test_a_still_failing_id_makes_r0_final_after_exactly_one_more_attempt(tmp_path, monkeypatch):
    worlds, ids = _three_failed(tmp_path, monkeypatch)
    _flaky(monkeypatch, {ids[1]})
    with pytest.raises(CC.CompletionFailed):
        CC.complete_transport_failures(tmp_path, worlds, sleep=lambda s: None, ids=ids)
    att = _attempts(tmp_path, ids)
    assert att[ids[1]] == ("transport_failed", 3) and att[ids[0]] == ("ok", 3)
    rec = json.loads((tmp_path / confirm.CONFIRM_DIR / "completion_record.json").read_text())
    assert rec["disposition"] == "R0 final"
    # No second extension: a repeat refuses because the id is no longer eligible.
    with pytest.raises(RuntimeError, match="not eligible|not exactly"):
        CC.complete_transport_failures(tmp_path, worlds, sleep=lambda s: None, ids=ids)


def test_failed_probes_dispatch_nothing(tmp_path, monkeypatch):
    worlds, ids = _three_failed(tmp_path, monkeypatch)
    sent = _flaky(monkeypatch, set(), probe_ok=False)
    with pytest.raises(RuntimeError, match="recovery probes"):
        CC.complete_transport_failures(tmp_path, worlds, sleep=lambda s: None, ids=ids)
    assert all(x.startswith("probe:") for x in sent)
    assert all(v == ("transport_failed", 2) for v in _attempts(tmp_path, ids).values())


def test_completion_refuses_ids_that_are_not_exactly_the_unanswered_set(tmp_path, monkeypatch):
    worlds, ids = _three_failed(tmp_path, monkeypatch)
    sent = _flaky(monkeypatch, set())
    with pytest.raises(RuntimeError, match="not exactly the listed ids"):
        CC.complete_transport_failures(tmp_path, worlds, sleep=lambda s: None, ids=ids[:2])
    assert not sent


def test_an_exception_mid_dispatch_is_recorded(tmp_path, monkeypatch):
    worlds, ids = _three_failed(tmp_path, monkeypatch)
    _flaky(monkeypatch, set())
    calls = {"n": 0}
    real = store.Store.call_one

    def boom(self, req):
        if not req.custom_id.startswith("probe:"):
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyboardInterrupt
        return real(self, req)

    monkeypatch.setattr(store.Store, "call_one", boom)
    with pytest.raises(KeyboardInterrupt):
        CC.complete_transport_failures(tmp_path, worlds, sleep=lambda s: None, ids=ids)
    rec = json.loads((tmp_path / confirm.CONFIRM_DIR / "completion_record.json").read_text())
    assert rec["disposition"].startswith("interrupted") and len(rec["dispatch"]) == 1
    assert rec["dispatch"][0]["status"] == "ok"


def test_completion_k0_cap_keeps_the_total_within_140():
    assert CC.COMPLETION_K0_CAP + CC.PROBE_CAP + confirm.CHECK_CAP == 140.0
