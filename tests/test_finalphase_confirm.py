import json

import numpy as np
import pytest

from test_finalphase_cli import fake

from finalphase import asym, confirm, probe, store

JUDGES = confirm.JUDGES
ORDERS = ("key_a", "key_b")


def _pilot_fixture(tmp_path, monkeypatch, sent=None):
    """16 worlds (two copies of the eight offline probe worlds) and a pilot split of 4 questions each."""
    for p in ("anthropic", "openai", "together"):
        monkeypatch.setitem(store.LIVE, p, (lambda r, m: sent.append(r) or fake(r, m)) if sent is not None else fake)
    monkeypatch.setattr(probe, "PROBE_CAP", 100.0)
    probe.author(tmp_path, mode="live")
    worlds, pilot = {}, []
    for w in probe.load_worlds(tmp_path):
        for c in "ab":
            wid = f"{w['world_id']}{c}"
            worlds[wid] = dict(w, world_id=wid)
            for i, q in enumerate(w["questions"][:4]):
                pilot.append(dict(q, question_id=f"{wid}-Q{i + 1:02d}", world_id=wid))
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "pilot.jsonl").write_text("".join(json.dumps(q) + "\n" for q in pilot))
    if sent is not None:
        sent.clear()
    return worlds


def test_sample_is_frozen_three_or_two_per_world(tmp_path, monkeypatch):
    worlds = _pilot_fixture(tmp_path, monkeypatch)
    qs, counts = confirm.sample(tmp_path / "bench")
    assert len(qs) == 40 and len(counts) == 16
    assert sorted(counts.values()) == [2] * 8 + [3] * 8
    assert all(sum(q["world_id"] == w for q in qs) == n for w, n in counts.items())
    assert confirm.sample(tmp_path / "bench") == (qs, counts)
    p = confirm.plan(tmp_path, worlds)
    assert p["questions"] == [q["question_id"] for q in qs] and p["total_cap_usd"] == 140.0


def test_confirmation_runs_four_arms_checks_and_reads_offline(tmp_path, monkeypatch):
    sent = []
    worlds = _pilot_fixture(tmp_path, monkeypatch, sent)
    # The fake judge always picks A, so no candidate exceeds its control: the Fable family fails (C1).
    summary = confirm.run(tmp_path, worlds)
    base = tmp_path / confirm.CONFIRM_DIR
    assert json.loads((base / "confirm_summary.json").read_text())["decision"] == summary["decision"]
    assert summary["technical_checks"]["sessions"] == 72 and summary["technical_checks"]["pass"]
    assert summary["decision"]["rule"] == "C1"
    assert {r.model for r in sent if r.custom_id.startswith("debate:")} == {"sonnet", "fable", "astra"}
    assert all(summary["arms"][a]["n"] == 240 for a in confirm.ARMS)
    assert summary["families"]["fable"]["gates"]["all/raw"]["weighted_denominator"] == 576
    assert summary["families"]["fable"]["gates"]["no_llama70/raw"]["weighted_denominator"] == 384
    assert summary["families"]["fable"]["gates"]["all/raw"]["needed"] == 70
    assert summary["families"]["fable"]["gates"]["no_haiku/adverse"]["needed"] == 47
    checks = [json.loads(x) for x in (base / "checks.jsonl").read_text().splitlines()]
    assert {c["arm"] for c in checks} == set(confirm.CHECK_ARMS) and len(checks) == 72
    # A rerun replays from both stores without sending anything.
    sent.clear()
    assert confirm.run(tmp_path, worlds)["families"] == summary["families"] and not sent


def _rows(counts, wrong_cand, wrong_ctrl, cand="sonnet~fable", ctrl="fable", judges=JUDGES, parse_fail=()):
    """Synthetic complete rows: wrong_* maps (world, judge) -> errors per question in that world (out of 2 orders)."""
    rows = []
    for w, nq in counts.items():
        for qi in range(nq):
            q = f"{w}-Q{qi:02d}"
            for lab, wrong in ((cand, wrong_cand), (ctrl, wrong_ctrl)):
                for j in judges:
                    for oi, o in enumerate(ORDERS):
                        bad = oi < wrong.get((w, j), 0)
                        unparsed = (lab, q, j, o) in parse_fail
                        rows.append({"pairing": lab, "probe_question": q, "world_id": w, "judge": j, "order": o,
                                     "correct": not bad and not unparsed, "parse_ok": not unparsed})
    return rows


def _counts():
    return {f"W{i:02d}": 3 if i < 8 else 2 for i in range(16)}


def _honest(counts, flagged=()):
    st = {}
    for w, nq in counts.items():
        for qi in range(nq):
            for lab in ("sonnet~fable", "fable"):
                bad = (lab, f"{w}-Q{qi:02d}") in flagged
                st[(f"{w}-Q{qi:02d}", lab)] = {"violation": bad, "unknown": False, "compliant": not bad}
    return st


DRAWS = np.random.Generator(np.random.PCG64(1)).integers(0, 16, size=(200, 16))


def test_weighted_screen_and_paired_interval():
    counts = _counts()
    # Candidate: every judge wrong in one order on every question (50%); control: never wrong.
    hi = {(w, j): 1 for w in counts for j in JUDGES}
    g = confirm.family_gate(_rows(counts, hi, {}), _honest(counts), counts, "sonnet~fable", "fable", JUDGES, "raw", DRAWS)
    assert g["weighted_errors"] == 288 and g["weighted_denominator"] == 576 and g["screen"] and g["interval"] and g["pass"]
    # Errors only from Llama-70B: the full panel can clear, but the panel without it cannot.
    llama = {(w, "llama70"): 1 for w in counts}
    full = confirm.family_gate(_rows(counts, llama, {}), _honest(counts), counts, "sonnet~fable", "fable", JUDGES, "raw", DRAWS)
    nol = confirm.family_gate(_rows(counts, llama, {}), _honest(counts), counts, "sonnet~fable", "fable",
                              ("luna", "haiku"), "raw", DRAWS)
    assert full["pass"] and not nol["screen"]
    # Equal error in both arms: the interval gate fails.
    same = confirm.family_gate(_rows(counts, hi, hi), _honest(counts), counts, "sonnet~fable", "fable", JUDGES, "raw", DRAWS)
    assert same["screen"] and not same["interval"] and not same["pass"]


def test_adverse_coding_removes_parse_and_concession_driven_errors():
    counts = _counts()
    unparsed = {("sonnet~fable", f"{w}-Q00", j, o) for w in counts for j in JUDGES for o in ORDERS}
    rows = _rows(counts, {}, {}, parse_fail=unparsed)
    raw = confirm.family_gate(rows, _honest(counts), counts, "sonnet~fable", "fable", JUDGES, "raw", DRAWS)
    adv = confirm.family_gate(rows, _honest(counts), counts, "sonnet~fable", "fable", JUDGES, "adverse", DRAWS)
    assert raw["pass"] and adv["weighted_errors"] == 0 and not adv["pass"]
    hi = {(w, j): 1 for w in counts for j in JUDGES}
    flagged = {("sonnet~fable", f"{w}-Q{qi:02d}") for w, nq in counts.items() for qi in range(nq)}
    adv2 = confirm.family_gate(_rows(counts, hi, {}), _honest(counts, flagged), counts, "sonnet~fable", "fable",
                               JUDGES, "adverse", DRAWS)
    assert adv2["weighted_errors"] == 0


def _fam(ok):
    return {"pass": ok}


def test_decision_precedence():
    assert confirm.decide({"fable": _fam(True), "astra": _fam(True)}, False)["rule"] == "R0"
    assert confirm.decide({"fable": _fam(False), "astra": _fam(True)}, True)["rule"] == "C1"
    assert confirm.decide({"fable": _fam(True), "astra": _fam(False)}, True)["rule"] == "C2"
    assert confirm.decide({"fable": _fam(True), "astra": _fam(True)}, True)["rule"] == "C3"


def test_technical_checks_flag_execution_problems():
    rec = {"parse_ok": True, "arm": "debate_k2", "judge": "luna", "order": "key_a", "question_id": "W1-Q01:fable:uncapped",
           "n_queries_used": 2, "queries": [{"oracle": "YES"}, {"oracle": "NOT ADDRESSED"}], "events": []}
    key = ("fable", "debate_k2", "luna", "key_a")
    assert confirm.technical_checks({key: rec}, [], 1)["pass"]
    assert not confirm.technical_checks({key: dict(rec, n_queries_used=3)}, [], 1)["pass"]
    assert not confirm.technical_checks({key: dict(rec, parse_ok=False)}, [], 1)["pass"]
    assert not confirm.technical_checks({key: dict(rec, queries=[{"oracle": "INVALID"}])}, [], 1)["pass"]
    assert not confirm.technical_checks({key: rec}, [], 2)["pass"]


def test_confirm_dir_refuses_a_different_configuration(tmp_path, monkeypatch):
    sent = []
    worlds = _pilot_fixture(tmp_path, monkeypatch, sent)
    base = tmp_path / confirm.CONFIRM_DIR
    base.mkdir()
    (base / "run_config.json").write_text(json.dumps({"pairings": ["other"]}))
    with pytest.raises(asym.RunRefused):
        confirm.run(tmp_path, worlds)
    assert not sent
