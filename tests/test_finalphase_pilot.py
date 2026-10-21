import json
import platform
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

from test_finalphase_cli import fake
from test_finalphase_confirm import _pilot_fixture

from finalphase import cli, confirm, pilot, preflight, store
from finalphase.providers import TransportError

JUDGES = ("luna", "haiku", "llama70", "sonnet")


def _confirmed(tmp_path, monkeypatch, sent):
    worlds = _pilot_fixture(tmp_path, monkeypatch)
    (tmp_path / "bench" / "main.jsonl").write_bytes((tmp_path / "bench" / "pilot.jsonl").read_bytes())
    confirm.run(tmp_path, worlds)
    for p in ("anthropic", "openai", "together"):
        monkeypatch.setitem(store.LIVE, p, lambda r, m: sent.append(r.custom_id) or fake(r, m))
    return worlds


def test_pilot_reuses_the_confirmation_cells_and_seals_non_k0_outcomes(tmp_path, monkeypatch):
    sent = []
    worlds = _confirmed(tmp_path, monkeypatch, sent)
    exposed = set(json.loads((tmp_path / confirm.CONFIRM_DIR / "sample.json").read_text())["questions"])
    readout = pilot.run(tmp_path, worlds, JUDGES, 1275.0)
    qs = pilot.pilot_questions(tmp_path / "bench")
    assert readout["completion"]["judgments"] == len(qs) * len(JUDGES) * 6 * 2 == readout["completion"]["expected_judgments"]
    for cid in sent:
        p = cid.split(":")
        if p[0] in ("debate", "compliance") and p[3] in exposed:
            pytest.fail(f"re-sent reused cell {cid}")
        if p[0] == "judge" and p[1] in exposed and p[4] in pilot.REUSED_JUDGES and p[5] == "debate_k0":
            pytest.fail(f"re-sent reused verdict {cid}")
    assert any(c.startswith("judge:") and c.split(":")[1] in exposed and ":sonnet:debate_k0:" in c for c in sent)
    reuse = json.loads((tmp_path / "pilot" / "reused_from_confirmation.json").read_text())
    assert reuse["cells"] == 720 and reuse["by_kind"] == {"debate": 240, "compliance": 240, "judge": 240}
    assert reuse["original_cost_usd"] > 0 and readout["spend"]["reused_original_cost_usd"] == reuse["original_cost_usd"]
    assert readout["spend"]["run_cap_usd"] == 1275.0 - pilot.RESERVE_USD
    k0 = readout["k0_error_descriptive"]["luna"]
    assert k0["exposed_40"]["questions"] == 40 and k0["fresh_135"]["questions"] == len(qs) - 40 and k0["combined"]["complete"]
    # Open export: an explicit projection with no verdict outcome or raw step text for non-k0 arms.
    open_rows = [json.loads(x) for x in (tmp_path / "pilot" / "judgments_open.jsonl").read_text().splitlines()]
    for r in open_rows:
        if r["arm"] != "debate_k0":
            assert not {"verdict", "confidence", "correct", "reasoning", "key_label"} & set(r)
            assert all("raw" not in q for q in r["queries"])
    assert all("correct" in r for r in open_rows if r["arm"] == "debate_k0")
    full = [json.loads(x) for x in (tmp_path / "pilot" / "sealed" / "judgments_full.jsonl").read_text().splitlines()]
    assert all("correct" in r for r in full) and len(full) == len(open_rows)
    assert (tmp_path / "pilot" / "sealed" / "README.txt").exists()
    assert readout["compliance"]["turns"] == len(qs) * 6 and readout["compliance"]["unknown"] == 0
    assert set(readout["judge_sessions"]) == {f"{j}:{a}:{o}" for j in JUDGES for a in ("world_alone", "debate_k0", "debate_k1",
                                             "debate_k2", "debate_k6", "debate_world") for o in ("key_a", "key_b")}
    assert any(k.startswith("judge:haiku:debate_k6:query_step") for k in readout["calls"])
    assert readout["order_balance"]["questions"] == len(qs)
    # A rerun replays without sending and keeps the original reuse receipt (original cost not zeroed).
    sent.clear()
    again = pilot.run(tmp_path, worlds, JUDGES, 1275.0)
    assert again["completion"] == readout["completion"] and not sent
    assert again["spend"]["reused_original_cost_usd"] == reuse["original_cost_usd"]


def test_seed_refuses_a_different_existing_row(tmp_path, monkeypatch):
    sent = []
    _confirmed(tmp_path, monkeypatch, sent)
    store.Store(tmp_path / "pilot.db", "pilot", 1275.0).db.close()
    con = sqlite3.connect(tmp_path / "confirm-sonnet-v1" / "confirm.db")
    cid = con.execute("SELECT custom_id, model, request FROM calls WHERE custom_id LIKE 'debate:uncapped:sonnet~fable:%'"
                      " LIMIT 1").fetchone()
    con.close()
    d = sqlite3.connect(tmp_path / "pilot.db")
    d.execute("INSERT INTO calls(custom_id, stage, model, req_hash, request, status, response) VALUES(?,?,?,?,?,?,?)",
              (cid[0], "pilot", cid[1], "other", cid[2], "ok", "{}"))
    d.commit(); d.close()
    qs = {q["question_id"] for q in pilot.pilot_questions(tmp_path / "bench")}
    with pytest.raises(RuntimeError, match="already holds a different"):
        pilot.seed_from_confirmation(tmp_path, tmp_path / "pilot.db", qs)


def _launch_ready(tmp_path, monkeypatch):
    manifest = {"git_commit": "old", "ceiling_usd": 6000,
                "provider_checks": {p: "passed" for p in ("anthropic", "openai", "together")}}
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "pilot_placements_20261021T000000Z.json").write_text(json.dumps(
        {"checked_utc": datetime.now(timezone.utc).isoformat(), "host": platform.node(),
         "Jack's PC": {"status": "Available"}, "HaleysPC": {"status": "Unavailable through existing trusted SSH"},
         "RunPod": {"status": "Authenticated inventory read"}}))
    monkeypatch.setattr(preflight, "source_commit", lambda: "commit")
    monkeypatch.setattr("finalphase.expansion.audit_reasons", lambda root: [])
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    for key in preflight.PROVIDER_KEYS:
        monkeypatch.setenv(key, "fake")


def test_qualify_pilot_bootstraps_a_stale_manifest_through_the_cli(tmp_path, monkeypatch):
    sent = []
    worlds = _confirmed(tmp_path, monkeypatch, sent)
    _launch_ready(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_worlds", lambda: list(worlds.values()))
    assert any("current source commit" in r for r in preflight.check("pilot", tmp_path, 16, "live", qualification=True)["reasons"])
    sent.clear()
    monkeypatch.setattr(sys, "argv", ["finalphase.cli", "qualify-pilot"])
    cli.main()
    assert len(sent) == 32 and sum(c.startswith("qualification:pilot:v2:serial:") for c in sent) == 16
    m = json.loads((tmp_path / "run_manifest.json").read_text())
    assert m["git_commit"] == "commit" and m["commit_history"][0]["prior"] == "old"
    rec = m["throughput"]["pilot"]
    assert rec["placement_reference"].endswith("pilot_placements_20261021T000000Z.json")
    assert preflight.check("pilot", tmp_path, rec["selected_workers"], "live")["ready"]
    assert all(i.startswith("debate:uncapped:sonnet~fable:") for i in rec["parallel"]["request_ids"])
    # A second qualification of the same version cannot time cached calls.
    (tmp_path / "pilot_qualification_v2.json").write_text(json.dumps(
        {k: v for k, v in json.loads((tmp_path / "pilot_qualification_v2.json").read_text()).items() if k != "parallel"}))
    with pytest.raises(RuntimeError, match="cached"):
        pilot.qualify(tmp_path, worlds, 1275.0)


def test_a_serial_winning_receipt_runs_the_pilot_with_one_worker(tmp_path, monkeypatch):
    sent = []
    worlds = _confirmed(tmp_path, monkeypatch, sent)
    _launch_ready(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "_worlds", lambda: list(worlds.values()))
    ticks = iter([0.0, 1.0, 0.0, 5.0])  # serial 1s, parallel 5s
    monkeypatch.setattr(pilot.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(sys, "argv", ["finalphase.cli", "qualify-pilot"])
    cli.main()
    assert json.loads((tmp_path / "run_manifest.json").read_text())["throughput"]["pilot"]["selected_workers"] == 1
    used = {}
    monkeypatch.setattr(pilot, "run", lambda *a, **k: used.update(k))
    monkeypatch.setattr(sys, "argv", ["finalphase.cli", "pilot"])
    cli.main()
    assert used["workers"] == 1


def test_an_exhausted_gate_transport_failure_fails_the_session_without_spending_a_slot(tmp_path, monkeypatch):
    from finalphase.engine import drive
    from finalphase.judging import judge_task

    def provider(r, m):
        if r.custom_id.startswith("gate:"):
            raise TransportError("503")
        return fake(r, m)

    for p in ("anthropic", "openai", "together"):
        monkeypatch.setitem(store.LIVE, p, provider)
    monkeypatch.setattr(store.time, "sleep", lambda s: None)
    item = {"question_id": "W1-Q01:sonnet~fable:uncapped", "world_id": "W1", "world_text": "w", "question": "q?",
            "correct_answer": "a", "wrong_answer": "b", "turns": [{"role": "honest", "text": "x"}]}
    s = store.Store(tmp_path / "t.db", "pilot", 10.0, max_transport_attempts=2)
    judged, failed = drive(s, {"k": judge_task(item, "luna", "debate_k1", "key_a", "opus", "dsflash")}, workers=1)
    s.db.close()
    assert failed == ["k"] and not judged  # unscored, never a REJECT or a spent slot


def test_distribution_median_and_placement_validation(tmp_path):
    assert pilot._dist([1, 3]) == {"mean": 2, "median": 2, "p90": 3, "max": 3}
    rec = {"checked_utc": datetime.now(timezone.utc).isoformat(), "host": platform.node(),
           "Jack's PC": {"status": "Available"}, "HaleysPC": {}, "RunPod": {"status": "x"}}
    (tmp_path / "pilot_placements_1.json").write_text(json.dumps(rec))
    with pytest.raises(RuntimeError, match="stale, from another host, or incomplete"):
        pilot.latest_placements(tmp_path)
    future = dict(rec, HaleysPC={"status": "y"}, checked_utc="2099-01-01T00:00:00+00:00")
    (tmp_path / "pilot_placements_2.json").write_text(json.dumps(future))
    with pytest.raises(RuntimeError):
        pilot.latest_placements(tmp_path)


def test_a_new_qualification_version_samples_none_of_the_earlier_questions(tmp_path, monkeypatch):
    sent = []
    worlds = _confirmed(tmp_path, monkeypatch, sent)
    first = pilot.qualification_sample(tmp_path, worlds)
    (tmp_path / "pilot_qualification_v2.json").write_text(json.dumps(
        {"parallel": {"request_ids": [r.custom_id for r in first]}}))
    monkeypatch.setattr(pilot, "QUALIFICATION_VERSION", "v3")
    second = pilot.qualification_sample(tmp_path, worlds)
    qid = lambda r: r.custom_id.split(":")[-3]
    assert {qid(r) for r in first} and not {qid(r) for r in first} & {qid(r) for r in second}
    assert len(second) == 2 * pilot.QUALIFICATION_QUESTIONS
