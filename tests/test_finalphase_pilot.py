import json
import sqlite3

import pytest

from test_finalphase_cli import fake
from test_finalphase_confirm import _pilot_fixture

from finalphase import confirm, pilot, preflight, store

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
    # No exposed debate turn, compliance check or reused k0 verdict was sent again; Sonnet's k0 on them was.
    for cid in sent:
        p = cid.split(":")
        if p[0] in ("debate", "compliance") and p[3] in exposed:
            pytest.fail(f"re-sent reused cell {cid}")
        if p[0] == "judge" and p[1] in exposed and p[4] in pilot.REUSED_JUDGES and p[5] == "debate_k0":
            pytest.fail(f"re-sent reused verdict {cid}")
    assert any(c.startswith("judge:") and c.split(":")[1] in exposed and ":sonnet:debate_k0:" in c for c in sent)
    reuse = json.loads((tmp_path / "pilot" / "reused_from_confirmation.json").read_text())
    assert reuse["cells"] == 40 * (6 + 6 + 6) and reuse["by_kind"] == {"debate": 240, "compliance": 240, "judge": 240}
    # Reused cells cost nothing in the pilot stage.
    con = sqlite3.connect(tmp_path / "pilot.db")
    assert con.execute("SELECT SUM(cost) FROM calls WHERE custom_id LIKE 'debate:%' AND custom_id LIKE ?",
                       (f"%:{sorted(exposed)[0]}:%",)).fetchone()[0] == 0
    con.close()
    k0 = readout["k0_error_descriptive"]["luna"]
    assert k0["exposed_40"]["questions"] == 40 and k0["fresh_135"]["questions"] == len(qs) - 40
    open_rows = [json.loads(x) for x in (tmp_path / "pilot" / "judgments_open.jsonl").read_text().splitlines()]
    assert all("correct" not in r for r in open_rows if r["arm"] != "debate_k0")
    assert all("correct" in r for r in open_rows if r["arm"] == "debate_k0")
    full = [json.loads(x) for x in (tmp_path / "pilot" / "sealed" / "judgments_full.jsonl").read_text().splitlines()]
    assert all("correct" in r for r in full) and len(full) == len(open_rows)
    assert readout["compliance"]["turns"] == len(qs) * 6 and readout["compliance"]["unknown"] == 0
    assert readout["main_forecast"]["main_questions"] > 0
    # A rerun replays from the store without sending anything.
    sent.clear()
    assert pilot.run(tmp_path, worlds, JUDGES, 1275.0)["completion"] == readout["completion"] and not sent


def test_seed_refuses_a_different_existing_row(tmp_path, monkeypatch):
    sent = []
    worlds = _confirmed(tmp_path, monkeypatch, sent)
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


def test_pilot_qualification_writes_a_receipt_the_launcher_accepts(tmp_path, monkeypatch):
    sent = []
    worlds = _confirmed(tmp_path, monkeypatch, sent)
    manifest = {"git_commit": "old", "ceiling_usd": 6000,
                "provider_checks": {p: "passed" for p in ("anthropic", "openai", "together")},
                "validation_execution": {"placements": {"reference": "placements.json"}}}
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(preflight, "source_commit", lambda: "commit")
    monkeypatch.setattr("finalphase.expansion.audit_reasons", lambda root: [])
    for key in preflight.PROVIDER_KEYS:
        monkeypatch.setenv(key, "fake")
    assert any("throughput receipt" in r for r in preflight.check("pilot", tmp_path, 16, "live")["reasons"])
    sent.clear()
    rec = pilot.qualify(tmp_path, worlds, 1275.0)
    assert len(sent) == 4 and sum(c.startswith("qualification:pilot:v1:serial:") for c in sent) == 2
    m = json.loads((tmp_path / "run_manifest.json").read_text())
    assert m["git_commit"] == "commit" and m["commit_history"][0]["prior"] == "old"
    assert m["throughput"]["pilot"]["selected_workers"] in (1, 16) and rec["semantics_preserved"]
    report = preflight.check("pilot", tmp_path, m["throughput"]["pilot"]["selected_workers"], "live")
    assert report["ready"], report["reasons"]
    # The parallel opening turns are canonical pilot ids, so the pilot replays them.
    assert all(i.startswith("debate:uncapped:sonnet~fable:") for i in m["throughput"]["pilot"]["parallel"]["request_ids"])
