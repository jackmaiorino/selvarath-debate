import json

from test_finalphase_cli import fake

from finalphase import authoring as A
from finalphase import probe, store
from finalphase import prompts_authoring as P


def test_hardness_requests_use_fresh_ids_and_keep_the_length_check():
    plan = probe.world_plan()
    assert [w for w, _, _ in plan] == [f"H{i:03d}" for i in range(1, 9)]
    assert [a for _, a, _ in plan].count("fable") == 4
    fable = probe.author_request(*plan[0], attempt=0)
    astra = probe.author_request(*plan[1], attempt=0)
    assert fable.custom_id.endswith(":hardness-v1") and astra.custom_id.endswith(":hardness-v1")
    assert P.AUTHOR_LENGTH_AMENDMENT in fable.messages[0][1] and P.AUTHOR_LENGTH_AMENDMENT not in astra.messages[0][1]
    assert all(P.AUTHOR_HARDNESS_AMENDMENT in r.messages[0][1] for r in (fable, astra))
    assert fable.max_tokens == 128000 and not A.in_frozen_cohort("H001")
    # Benchmark requests are unchanged by the probe revision.
    assert P.AUTHOR_HARDNESS_AMENDMENT not in A.author_request("W009", "fable", "x").messages[0][1]


def test_probe_authors_and_validates_in_its_own_store(tmp_path, monkeypatch):
    for p in ("anthropic", "openai", "together"):
        monkeypatch.setitem(store.LIVE, p, fake)
    # Live mode has no batch discount; the real probe runs in batch, worst case about $16 per author attempt.
    monkeypatch.setattr(probe, "PROBE_CAP", 100.0)
    assert probe.author(tmp_path, mode="live")["authored"] == 8
    summary = probe.validate(tmp_path, mode="live")
    assert summary["candidates"] == 96 and summary["retained"] == 96
    assert (tmp_path / "probe-hardness-v1" / "probe.db").exists()
    assert not (tmp_path / "author.db").exists() and not (tmp_path / "validate.db").exists()
    rows = [json.loads(x) for x in (tmp_path / "probe-hardness-v1" / "validation.jsonl").read_text().splitlines()]
    assert {r["world_id"] for r in rows} == {f"H{i:03d}" for i in range(1, 9)}


def test_probe_canary_debates_balanced_questions_and_reports_k0_error(tmp_path, monkeypatch):
    for p in ("anthropic", "openai", "together"):
        monkeypatch.setitem(store.LIVE, p, fake)
    monkeypatch.setattr(probe, "PROBE_CAP", 100.0)
    probe.author(tmp_path, mode="live")
    probe.validate(tmp_path, mode="live")
    qs = probe.canary_questions(tmp_path)
    assert [q["author"] for q in qs].count("fable") == 10 and len(qs) == 20
    summary = probe.canary(tmp_path, ("luna", "haiku", "llama70"), ("fable", "astra"))
    assert summary["debates"] == 40 and summary["judgments"] == 240
    assert summary["k0_error"]["pooled"]["n"] == 240
    assert (tmp_path / "probe-hardness-v1" / "canary" / "judgments.jsonl").exists()


def test_round_two_uses_fresh_worlds_and_adds_the_prior_trap_block(tmp_path, monkeypatch):
    v2 = P.AUTHOR_HARDNESS_V2_REVISION
    assert [w for w, _, _ in probe.world_plan(v2)] == [f"H{i:03d}" for i in range(9, 17)]
    assert probe.probe_root(tmp_path, v2) != probe.probe_root(tmp_path)
    req = probe.author_request(*probe.world_plan(v2)[0], attempt=0, rev=v2)
    assert req.custom_id.endswith(":hardness-v2")
    text = req.messages[0][1]
    assert P.AUTHOR_HARDNESS_AMENDMENT in text and P.AUTHOR_HARDNESS_V2_AMENDMENT in text
    # Round 1 requests keep the identities already sent.
    v1 = probe.author_request(*probe.world_plan()[0], attempt=0)
    assert v1.custom_id.endswith(":hardness-v1") and P.AUTHOR_HARDNESS_V2_AMENDMENT not in v1.messages[0][1]
    for p in ("anthropic", "openai", "together"):
        monkeypatch.setitem(store.LIVE, p, fake)
    monkeypatch.setattr(probe, "PROBE_CAP", 100.0)
    probe.author(tmp_path, mode="live", rev=v2)
    assert probe.validate(tmp_path, mode="live", rev=v2)["prompt_revision"] == v2
    assert probe.canary(tmp_path, ("luna",), ("fable",), rev=v2)["debates"] == 20
