import hashlib
import json

import pytest

from test_finalphase_cli import fake
from test_finalphase_probe import _bench_canary_from_probe, _canary_baseline

from finalphase import asym, ladder, probe, store

JUDGES = ("luna", "haiku", "llama70")


def _offline(monkeypatch, sent=None):
    for p in ("anthropic", "openai", "together"):
        monkeypatch.setitem(store.LIVE, p, (lambda r, m: sent.append(r) or fake(r, m)) if sent is not None else fake)
    monkeypatch.setattr(probe, "PROBE_CAP", 100.0)


def test_wilson_and_trend_test():
    lo, hi = ladder.wilson(13, 120)
    assert lo == pytest.approx(0.0645, abs=5e-4) and hi == pytest.approx(0.1766, abs=5e-4)
    qs = [f"W001-Q{i:02d}" for i in range(20)]
    falling = {q: [3, 1, 0] if i < 8 else [0, 0, 0] for i, q in enumerate(qs)}
    rising = {q: [0, 1, 3] if i < 8 else [0, 0, 0] for i, q in enumerate(qs)}
    flat = {q: [1, 1, 1] for q in qs}
    assert ladder.trend_test(falling, "t", b=20000)["p_one_sided"] < 0.001
    assert ladder.trend_test(rising, "t", b=20000)["p_one_sided"] > 0.99
    assert ladder.trend_test(flat, "t", b=2000)["p_one_sided"] == 1.0
    # Fixed seed: the same counts give the same p.
    assert ladder.trend_test(falling, "t", b=5000) == ladder.trend_test(falling, "t", b=5000)
    per_q = {q: (c[0], 6) for q, c in falling.items()}
    blo, bhi = ladder.cluster_bootstrap(per_q, "x", b=2000)
    assert 0 <= blo < 24 / 120 < bhi <= 1


def _fam(reading, robust):
    return {"trend": {"reading": reading}, "overlap_free_trend": {"endpoint_direction_retained": robust}}


def test_decision_rules_cover_every_case():
    ok = {"anthropic": _fam("supported", True), "openai": _fam("uninformative (floor)", False)}
    assert ladder.decide(ok, {"anthropic": ["haiku"], "openai": []}, False)["rule"] == "R0"
    both = ladder.decide(ok, {"anthropic": ["haiku", "sonnet"], "openai": ["luna"]}, True)
    assert both["rule"] == "R1" and "sonnet~fable" in both["recommendation"] and "luna~astra" in both["recommendation"]
    one = ladder.decide(ok, {"anthropic": ["haiku"], "openai": []}, True)
    assert one["rule"] == "R2" and "haiku~fable" in one["recommendation"]
    weak = {"anthropic": _fam("supported", False), "openai": _fam("not supported", False)}
    assert ladder.decide(weak, {"anthropic": ["haiku"], "openai": []}, True)["rule"] == "R3"
    flat = {"anthropic": _fam("not supported", True), "openai": _fam("not supported", True)}
    assert ladder.decide(flat, {"anthropic": ["haiku"], "openai": []}, True)["rule"] == "R3"
    assert ladder.decide(flat, {"anthropic": [], "openai": []}, True)["rule"] == "R4"


def test_asym_run_refuses_to_overwrite_a_different_run(tmp_path, monkeypatch):
    sent = []
    _offline(monkeypatch, sent)
    qs, worlds = _bench_canary_from_probe(tmp_path)
    sent.clear()
    _canary_baseline(tmp_path, qs)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "summary.json").write_text(json.dumps({"pairings": ["haiku~fable"]}))
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        asym.run(tmp_path, worlds, JUDGES, (("sonnet", "fable"),), out_dir="other", stage="x", cap=50.0)
    assert not sent and not (tmp_path / "other" / "probe.db").exists()


def test_ladder_reuses_v1_rungs_and_writes_its_own_reading(tmp_path, monkeypatch):
    sent = []
    _offline(monkeypatch, sent)
    monkeypatch.setattr(ladder, "PERMUTATIONS", 2000)
    monkeypatch.setattr(ladder, "BOOTSTRAPS", 500)
    qs, worlds = _bench_canary_from_probe(tmp_path)
    _canary_baseline(tmp_path, qs)
    # Without a finished probe-asym-v1 the ladder refuses before any request.
    sent.clear()
    with pytest.raises(RuntimeError, match="no finished run"):
        ladder.run(tmp_path, worlds, JUDGES)
    assert not sent
    asym.run(tmp_path, worlds, JUDGES)
    v1 = tmp_path / asym.ASYM_DIR
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in v1.glob("*.jsonl")}
    sent.clear()
    summary = ladder.run(tmp_path, worlds, JUDGES)
    assert summary["reused_pairings"] == ["haiku~fable", "luna~astra"]
    assert summary["new_pairings"] == ["sonnet~fable", "opus~fable", "terra~astra", "sol~astra"]
    # Only the four new rungs were sent: 80 debates x 6 turns, their compliance checks and 480 judgments.
    assert not any(":haiku~fable:" in r.custom_id or ":luna~astra:" in r.custom_id for r in sent)
    assert {r.model for r in sent if r.custom_id.startswith("debate:")} == {"sonnet", "opus", "terra", "sol", "fable", "astra"}
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in v1.glob("*.jsonl")}
    for fam, honests in (("anthropic", ["haiku", "sonnet", "opus"]), ("openai", ["luna", "terra", "sol"])):
        f = summary["families"][fam]
        assert [r["honest"] for r in f["rungs"]] == honests and all(r["n"] == 120 and r["complete"] for r in f["rungs"])
        assert f["overlap_free_trend"]["judges"] == list(ladder.OVERLAP_FREE_JUDGES[fam])
        assert f["rungs"][0]["overlap_free_judges"]["n"] == 80
    assert summary["decision"]["rule"] in ("R1", "R2", "R3", "R4")
    assert json.loads((tmp_path / ladder.LADDER_DIR / "ladder_summary.json").read_text())["decision"] == summary["decision"]
    # A rerun replays from the ladder store without sending anything.
    sent.clear()
    assert ladder.run(tmp_path, worlds, JUDGES)["families"] == summary["families"] and not sent


def test_ladder_refuses_an_incomplete_reused_rung(tmp_path, monkeypatch):
    sent = []
    _offline(monkeypatch, sent)
    qs, worlds = _bench_canary_from_probe(tmp_path)
    _canary_baseline(tmp_path, qs)
    asym.run(tmp_path, worlds, JUDGES)
    path = tmp_path / asym.ASYM_DIR / "judgments.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    sent.clear()
    with pytest.raises(RuntimeError, match="grid incomplete"):
        ladder.run(tmp_path, worlds, JUDGES)
    assert not sent and not (tmp_path / ladder.LADDER_DIR).exists()


def test_missing_compliance_is_unknown_and_duplicates_are_refused(tmp_path, monkeypatch):
    from finalphase.judging import ORDERS

    _offline(monkeypatch)
    qs, worlds = _bench_canary_from_probe(tmp_path)
    _canary_baseline(tmp_path, qs)
    asym.run(tmp_path, worlds, JUDGES)
    base = tmp_path / asym.ASYM_DIR
    qids = {q["question_id"] for q in qs}
    path = base / "compliance.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    drop = next(json.loads(x) for x in lines if json.loads(x)["role"] == "honest")
    kept = [x for x in lines if json.loads(x)["custom_id"] != drop["custom_id"]]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    _, honest, _ = ladder.load_rungs(base, ["haiku~fable", "luna~astra"], qids, JUDGES, ORDERS)
    st = honest[(drop["question_id"], drop["pairing"])]
    assert st["unknown"] and not st["compliant"]
    assert sum(v["compliant"] for v in honest.values()) == 39
    path.write_text("\n".join(lines + lines[:1]) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicated compliance"):
        ladder.load_rungs(base, ["haiku~fable", "luna~astra"], qids, JUDGES, ORDERS)


def test_output_dir_is_bound_to_one_configuration(tmp_path, monkeypatch):
    sent = []
    _offline(monkeypatch, sent)
    qs, worlds = _bench_canary_from_probe(tmp_path)
    _canary_baseline(tmp_path, qs)
    sent.clear()
    (tmp_path / "half").mkdir()
    (tmp_path / "half" / "probe.db").write_bytes(b"")
    with pytest.raises(RuntimeError, match="unidentified run"):
        asym.run(tmp_path, worlds, JUDGES, (("sonnet", "fable"),), out_dir="half", stage="x", cap=50.0)
    asym.run(tmp_path, worlds, JUDGES, (("sonnet", "fable"),), out_dir="mine", stage="x", cap=50.0)
    sent.clear()
    with pytest.raises(RuntimeError, match="different run configuration"):
        asym.run(tmp_path, worlds, JUDGES, (("sonnet", "fable"),), out_dir="mine", stage="x", cap=60.0)
    assert not sent
    asym.run(tmp_path, worlds, JUDGES, (("sonnet", "fable"),), out_dir="mine", stage="x", cap=50.0)
    assert not sent  # same configuration replays


def test_stopped_ladder_writes_an_incomplete_report(tmp_path, monkeypatch):
    _offline(monkeypatch)
    qs, worlds = _bench_canary_from_probe(tmp_path)
    _canary_baseline(tmp_path, qs)
    asym.run(tmp_path, worlds, JUDGES)

    def stopped(*args, **kw):
        (tmp_path / ladder.LADDER_DIR).mkdir()
        store.Store(tmp_path / ladder.LADDER_DIR / "probe.db", ladder.LADDER_STAGE, 1.0).db.close()
        raise store.CapExceeded("stage probe_ladder: cap")

    monkeypatch.setattr(asym, "run", stopped)
    with pytest.raises(ladder.LadderIncomplete, match="CapExceeded"):
        ladder.run(tmp_path, worlds, JUDGES)
    report = json.loads((tmp_path / ladder.LADDER_DIR / "ladder_summary.json").read_text())
    assert report["decision"]["rule"] == "R0" and report["probe_ladder_spend_usd"] == 0.0
    assert report["fingerprints"]["inputs"]["probe-asym-v1/judgments.jsonl"]
