import json

from rejudge import calibrate, config
from analysis.infra.design import position_a_is_correct

L70 = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
QPLUS = "Qwen/Qwen3.7-Plus"
JUDGES = {"low_primary": "Qwen/Qwen3.5-9B", "low_fallback": "Qwen/Qwen2.5-7B-Instruct-Turbo",
          "anchor": L70, "top": QPLUS}


class ScriptedClient:
    """Mirrors the ScriptedClient pattern in tests/test_rejudge_judge_loop.py."""

    def __init__(self, script=None):
        self.script = {k: (list(v) if isinstance(v, list) else v)
                       for k, v in (script or {}).items()}
        self.calls = []
        self.dry_run = False

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict"):
        self.calls.append({"kind": kind, "messages": [dict(m) for m in messages]})
        v = self.script.get(kind, "SHORT DRY RESPONSE")
        return v.pop(0) if isinstance(v, list) else v


def _transcript(qid, tidx, protocol, debater_model, world="selvarath"):
    return {
        "question_id": qid, "transcript_index": tidx, "world": world,
        "question": f"Is a fact about {qid} true?", "correct_answer": "CORRECT ANSWER TEXT",
        "wrong_answer": "WRONG ANSWER TEXT", "honest_first": True,
        "debate_transcript": [{"speaker": "honest", "text": "opening honest turn", "round": 1},
                              {"speaker": "dishonest", "text": "opening dishonest turn", "round": 1}],
        "debater_model": debater_model, "protocol": protocol, "temperature": 0.7,
        "seed": 12345, "harness_version": "test", "created_at": "2026-01-01T00:00:00+00:00",
        "dry_run": False, "cell_key": f"{protocol}|{debater_model}|{qid}|{tidx}",
    }


def _protocol():
    return config.load_protocol()


def _write_transcripts(tmp_path, transcripts, name="transcripts.jsonl"):
    p = tmp_path / name
    with open(p, "w", encoding="utf-8") as f:
        for tr in transcripts:
            f.write(json.dumps(tr) + "\n")
    return p


def _write_models(tmp_path, name="models.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"judges": JUDGES, "debaters": [L70, QPLUS], "oracle": L70}),
                 encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# ArmSpec / short-code helpers
# ---------------------------------------------------------------------------

def test_debater_short_codes():
    assert calibrate.debater_short(L70) == "l70"
    assert calibrate.debater_short(QPLUS) == "qplus"


def test_debater_short_raises_on_unknown():
    try:
        calibrate.debater_short("some/unknown-model")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_cal_arm_naming_and_semantics():
    arm = calibrate.cal_arm("capped3", L70)
    assert arm.name == "cal-capped3-l70"
    assert (arm.oracle_normalizer, arm.composer, arm.done_detector) == ("strict", "clean", "robust")
    assert arm.placebo is False
    arm2 = calibrate.cal_arm("uncapped3", QPLUS)
    assert arm2.name == "cal-uncapped3-qplus"


def test_judge_short_map_has_roster_keys():
    assert calibrate.JUDGE_SHORT == {"low_primary": "low9", "low_fallback": "low7",
                                     "anchor": "a70", "top": "top",
                                     "mid_gemma": "g31", "top_oss": "oss120"}


# ---------------------------------------------------------------------------
# Cell enumeration counts (synthetic 4-transcript file: 2 protocols x 2 debaters,
# one question)
# ---------------------------------------------------------------------------

def _synthetic_four():
    return [
        _transcript("Q1", 0, "capped3", L70),
        _transcript("Q1", 0, "capped3", QPLUS),
        _transcript("Q1", 0, "uncapped3", L70),
        _transcript("Q1", 0, "uncapped3", QPLUS),
    ]


def test_b0_cell_count_all_judges_every_transcript_k2():
    cells = calibrate.enumerate_b0_cells(_synthetic_four(), JUDGES)
    assert len(cells) == 4 * 4 * 2                    # judges x transcripts x K=2
    assert all(c["budget"] == 0 for c in cells)
    # cell_key uniqueness holds WITHIN a judge's output file (judge is disambiguated by
    # which per-judge file it lands in), not globally across judges.
    for judge_key in JUDGES:
        per_judge = [c["cell_key"] for c in cells if c["judge_key"] == judge_key]
        assert len(set(per_judge)) == len(per_judge)
    replicate1 = [c for c in cells if c["replicate"] == 1]
    assert all(c["mirrored"] is True for c in replicate1)
    replicate0 = [c for c in cells if c["replicate"] == 0]
    assert all(c["mirrored"] is False for c in replicate0)


def test_b2smoke_cell_count_nonanchor_judges_only_l70_capped3():
    cells = calibrate.enumerate_b2smoke_cells(_synthetic_four(), JUDGES, L70)
    assert len(cells) == 3 * 1                        # 3 non-anchor judges x 1 matching transcript
    assert all(c["budget"] == 2 for c in cells)
    assert all(c["replicate"] == 0 for c in cells)
    assert all(c["judge_key"] != "anchor" for c in cells)
    assert all(c["transcript"]["protocol"] == "capped3" for c in cells)
    assert all(c["transcript"]["debater_model"] == L70 for c in cells)


def test_b2sat_cell_count_anchor_only_both_debaters_capped3():
    cells = calibrate.enumerate_b2sat_cells(_synthetic_four(), JUDGES)
    assert len(cells) == 1 * 2                        # anchor only x 2 capped3 transcripts
    assert all(c["judge_key"] == "anchor" for c in cells)
    assert all(c["budget"] == 2 for c in cells)
    assert {c["transcript"]["debater_model"] for c in cells} == {L70, QPLUS}


def test_b2smoke_takes_first_12_sorted_by_qid_tidx():
    transcripts = [_transcript(f"Q{i:02d}", t, "capped3", L70)
                   for i in range(20) for t in range(2)]
    cells = calibrate.enumerate_b2smoke_cells(transcripts, {"low_primary": JUDGES["low_primary"]},
                                              L70)
    assert len(cells) == 12
    picked = sorted((c["transcript"]["question_id"], c["transcript"]["transcript_index"])
                    for c in cells)
    expected = sorted((t["question_id"], t["transcript_index"]) for t in transcripts)[:12]
    assert picked == expected


# ---------------------------------------------------------------------------
# Mirrored replicate-1 negation
# ---------------------------------------------------------------------------

def test_mirrored_replicate_gets_exact_negation_of_design_assignment():
    tr = _transcript("SEL-002", 1, "capped3", L70)
    cells = calibrate.enumerate_b0_cells([tr], {"anchor": L70})
    c0 = next(c for c in cells if c["replicate"] == 0)
    c1 = next(c for c in cells if c["replicate"] == 1)
    client = ScriptedClient({"verdict": "VERDICT: Position A\nCONFIDENCE: 3\nREASONING: x"})
    exp_protocol = _protocol()
    rec0 = calibrate.judge_cell(c0, client, exp_protocol, "WORLD DOC")
    rec1 = calibrate.judge_cell(c1, client, exp_protocol, "WORLD DOC")

    design = position_a_is_correct(tr["question_id"], tr["transcript_index"])
    assert rec0["position_a_is_correct"] == design
    assert rec1["position_a_is_correct"] == (not design)
    assert rec0["position_a_is_correct"] == (not rec1["position_a_is_correct"])
    assert rec0["mirrored"] is False and rec1["mirrored"] is True


def test_judge_cell_injects_protocol_debater_mirrored():
    tr = _transcript("SEL-002", 0, "uncapped3", QPLUS)
    cells = calibrate.enumerate_b0_cells([tr], {"top": QPLUS})
    c0 = cells[0]
    client = ScriptedClient({"verdict": "VERDICT: Position A\nCONFIDENCE: 3\nREASONING: x"})
    rec = calibrate.judge_cell(c0, client, _protocol(), "WORLD DOC")
    assert rec["protocol"] == "uncapped3"
    assert rec["debater_model"] == QPLUS
    assert rec["mirrored"] is c0["mirrored"]
    assert rec["judge_model"] == QPLUS


# ---------------------------------------------------------------------------
# CLI: refusals, resume, per-judge output routing, failure handling
# ---------------------------------------------------------------------------

def test_live_requires_cap(tmp_path):
    tpath = _write_transcripts(tmp_path, [])
    mpath = _write_models(tmp_path)
    rc = calibrate.main(["--transcripts", str(tpath), "--models", str(mpath),
                         "--out-dir", str(tmp_path / "out")])
    assert rc == 2


def test_unknown_cells_refused(tmp_path):
    tpath = _write_transcripts(tmp_path, [])
    mpath = _write_models(tmp_path)
    rc = calibrate.main(["--dry-run", "--cells", "bogus", "--transcripts", str(tpath),
                         "--models", str(mpath), "--out-dir", str(tmp_path / "out")])
    assert rc == 2


def test_unknown_judges_refused(tmp_path):
    tpath = _write_transcripts(tmp_path, [])
    mpath = _write_models(tmp_path)
    rc = calibrate.main(["--dry-run", "--judges", "bogus", "--transcripts", str(tpath),
                         "--models", str(mpath), "--out-dir", str(tmp_path / "out")])
    assert rc == 2


def test_resume_adds_zero_lines(tmp_path):
    transcripts = [_transcript("SEL-002", 0, "capped3", L70),
                  _transcript("SEL-002", 1, "capped3", L70)]
    tpath = _write_transcripts(tmp_path, transcripts)
    mpath = _write_models(tmp_path)
    out_dir = tmp_path / "out"
    args = ["--dry-run", "--workers", "1", "--cells", "b0",
            "--transcripts", str(tpath), "--models", str(mpath), "--out-dir", str(out_dir)]

    rc = calibrate.main(args)
    assert rc == 0
    files = sorted(out_dir.glob("calibration_judgments_*.jsonl"))
    assert len(files) == 4                             # all four judges wrote a file
    total1 = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in files)
    assert total1 == 4 * 2 * 2                          # judges x transcripts x K=2

    rc2 = calibrate.main(args)
    assert rc2 == 0
    total2 = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in files)
    assert total2 == total1


def test_records_land_in_right_per_judge_file_with_required_fields(tmp_path):
    tr = _transcript("SEL-002", 0, "capped3", L70)
    tpath = _write_transcripts(tmp_path, [tr])
    mpath = _write_models(tmp_path)
    out_dir = tmp_path / "out"
    rc = calibrate.main(["--dry-run", "--workers", "1", "--cells", "b0", "--judges", "anchor",
                         "--transcripts", str(tpath), "--models", str(mpath),
                         "--out-dir", str(out_dir)])
    assert rc == 0
    anchor_path = out_dir / "calibration_judgments_a70.jsonl"
    rows = [json.loads(l) for l in anchor_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2                               # K=2 mirrored
    for r in rows:
        assert r["protocol"] == "capped3"
        assert r["debater_model"] == L70
        assert r["judge_model"] == L70
        assert r["arm"] == "cal-capped3-l70"
    assert {r["mirrored"] for r in rows} == {True, False}
    # only the requested judge got a file
    assert not (out_dir / "calibration_judgments_low9.jsonl").exists()


def test_transient_cell_failure_logged_and_continues(tmp_path, monkeypatch):
    tr = _transcript("SEL-002", 0, "capped3", L70)
    tpath = _write_transcripts(tmp_path, [tr])
    mpath = _write_models(tmp_path)
    out_dir = tmp_path / "out"
    calls = {"n": 0}

    def flaky(cell, client, exp_protocol, world_document):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"cell_key": cell["cell_key"], "protocol": cell["transcript"]["protocol"],
                "debater_model": cell["transcript"]["debater_model"], "mirrored": cell["mirrored"],
                "judge_model": cell["judge_model"]}

    monkeypatch.setattr(calibrate, "judge_cell", flaky)
    rc = calibrate.main(["--dry-run", "--workers", "1", "--cells", "b0", "--judges", "anchor",
                         "--transcripts", str(tpath), "--models", str(mpath),
                         "--out-dir", str(out_dir)])
    assert rc == 0
    failed = (out_dir / "calibrate_failed_cells.jsonl").read_text(encoding="utf-8")
    assert "boom" in failed
    rows = (out_dir / "calibration_judgments_a70.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1                               # 2 cells total, 1 failed, 1 written


def test_cap_exceeded_halts_run(tmp_path, monkeypatch):
    from rejudge.api_client import CapExceededError

    transcripts = [_transcript("SEL-002", 0, "capped3", L70),
                  _transcript("SEL-002", 1, "capped3", L70)]
    tpath = _write_transcripts(tmp_path, transcripts)
    mpath = _write_models(tmp_path)
    out_dir = tmp_path / "out"

    def capped(*a, **k):
        raise CapExceededError("cap")

    monkeypatch.setattr(calibrate, "judge_cell", capped)
    rc = calibrate.main(["--dry-run", "--workers", "2", "--cells", "b0", "--judges", "anchor",
                         "--transcripts", str(tpath), "--models", str(mpath),
                         "--out-dir", str(out_dir)])
    assert rc == 3
