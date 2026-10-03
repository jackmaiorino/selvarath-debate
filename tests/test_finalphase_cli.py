import json
import types
import pytest

from finalphase import cli, store
from finalphase.providers import Response

TYPES = ["causal_assessment", "institutional_capacity", "relative_position", "counterfactual", "policy_outcome", "interest_alignment"]


def _world(n):
    qs = []
    for i in range(12):
        qs.append({"question": f"Did policy {i} of world {n} achieve its aim of calm?",
                   "correct_answer": f"No, policy {i} raised tolls and the ferry guild left the port soon after.",
                   "wrong_answer": f"Yes, policy {i} ended the riots and the council praised it that year.",
                   "facts_required": [f"Policy {i} raised tolls", "The ferry guild left"], "rubric": "calm means no riots",
                   "ledger": {"supporting": ["a"], "countervailing": ["b"], "decisive_inference": "c"},
                   "reasoning": "r", "wrong_answer_defensibility": "d", "task_type": TYPES[i % 6]})
    return {"world_name": f"World {n}", "world_text": "word " * 900, "questions": qs}


def fake(req, m):
    cid = req.custom_id
    if cid.startswith("author:"):
        return Response(cid, m.model_id, "ok", json.dumps(_world(cid.split(":")[1])))
    if cid.startswith("validate:"):
        key = "A" if cid.endswith("key_a") else "B"
        return Response(cid, m.model_id, "ok", f"ANSWER: {key}\nEVIDENCE: x\nREVERSING_READING_EXISTS: no\nOTHER_EQUALLY_DEFENSIBLE: no\nREASON: y")
    if cid.startswith(("factcheck:", "oracle:")):
        return Response(cid, m.model_id, "ok", "YES")
    if cid.startswith("gate:"):
        return Response(cid, m.model_id, "ok", "ADMIT")
    if cid.startswith("compliance:"):
        return Response(cid, m.model_id, "ok", "VIOLATION: no\nTYPE: none\nQUOTE: none")
    if cid.startswith("select:"):
        return Response(cid, m.model_id, "ok", "CLAIM: The ferry guild left\nCLAIM: Tolls rose")
    if cid.startswith("debate:"):
        return Response(cid, m.model_id, "ok", "argument " * 120, cost=0.001)
    if ":verdict" in cid or ":debate_k0:" in cid or ":world" in cid:
        return Response(cid, m.model_id, "ok", "VERDICT: Position A\nCONFIDENCE: 3\nREASONING: z")
    return Response(cid, m.model_id, "ok", "CLAIM: The ferry guild left the port" if ":s0" in cid else "DONE")


def test_all_stages_offline(tmp_path, monkeypatch):
    for p in ("openai", "anthropic", "together"):
        monkeypatch.setitem(store.LIVE, p, fake)
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    ns = types.SimpleNamespace
    cli.cmd_author(ns(worlds=24, only=None, mode="live"))
    assert len(list((tmp_path / "bench" / "worlds").glob("*.json"))) == 24
    cli.cmd_validate(ns(mode="live"))
    rows = [json.loads(x) for x in open(tmp_path / "bench" / "validation.jsonl")]
    assert len(rows) == 288 and all(r["retained"] for r in rows)
    cli.cmd_split(ns(main_questions=1068))
    canary = [json.loads(x) for x in open(tmp_path / "bench" / "canary.jsonl")]
    pilot = [json.loads(x) for x in open(tmp_path / "bench" / "pilot.jsonl")]
    main = [json.loads(x) for x in open(tmp_path / "bench" / "main.jsonl")]
    assert {q["world_id"] for q in canary}.isdisjoint({q["world_id"] for q in main})
    assert len(canary) == 48 and len(pilot) == 16 * 12 and len(main) == 4 * 9
    assert all(q["debater"] in ("fable", "astra") for q in main)
    cli.cmd_canary(ns(n=4))
    assert sum(1 for _ in open(tmp_path / "canary" / "debates.jsonl")) == 16
    assert sum(1 for _ in open(tmp_path / "canary" / "judgments.jsonl")) == 16 * 3 * 2
    cli.cmd_judge(ns(split="main", mode="live", judges="luna,llama70", oracle="opus", gate="dsflash",
                     limit=3, workers=4))
    judged = [json.loads(x) for x in open(tmp_path / "main" / "judgments.jsonl")]
    assert len(judged) == 3 * 2 * 6 * 2
    assert not any(j["arm"] == "top2" for j in judged)
    with pytest.raises(ValueError, match="after main requests"):
        cli.cmd_split(ns(main_questions=20))
    assert all(j["parse_ok"] for j in judged)
    k2 = [j for j in judged if j["arm"] == "debate_k2"]
    assert all(j["n_queries_used"] == 1 for j in k2)


def test_reversal_flags_are_descriptive_and_equal_defensibility_rejects():
    from finalphase import authoring as a

    qid = "W001-Q01"
    responses = {}
    for v in a.validators_for("fable"):
        for order, key in (("key_a", "A"), ("key_b", "B")):
            cid = f"validate:{qid}:{v}:{order}"
            responses[cid] = Response(cid, v, "ok",
                f"ANSWER: {key}\nREVERSING_READING_EXISTS: yes\nOTHER_EQUALLY_DEFENSIBLE: no")
    cid = f"factcheck:{qid}:0"
    responses[cid] = Response(cid, "dspro", "ok", "YES")
    assert a.retain_decision(qid, "fable", responses, 1)["retained"]
    cid = f"validate:{qid}:astra:key_a"
    responses[cid].text = "ANSWER: A\nREVERSING_READING_EXISTS: no\nOTHER_EQUALLY_DEFENSIBLE: yes"
    assert not a.retain_decision(qid, "fable", responses, 1)["retained"]


def test_authoring_reports_failure_if_worlds_remain_unusable(tmp_path, monkeypatch):
    for provider in ("anthropic", "openai"):
        monkeypatch.setitem(store.LIVE, provider,
            lambda req, m: Response(req.custom_id, m.model_id, "truncated", '{"world_text":'))
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    with pytest.raises(RuntimeError, match="incomplete after 2 attempts"):
        cli.cmd_author(types.SimpleNamespace(worlds=2, only=None, mode="live"))
    s = store.Store(tmp_path / "author.db", "author", cli.STAGE_CAPS["author"])
    assert s._q("SELECT COUNT(*) FROM calls")[0][0] == 4
    assert not list((tmp_path / "bench/worlds").glob("*.json"))


def test_author_budget_probe_keeps_prompts_and_uses_distinct_ids():
    from finalphase.authoring import author_request

    original = author_request("W001", "fable", "river delta")
    probe = author_request("W001", "fable", "river delta", max_tokens=64000)
    assert original.custom_id == "author:W001:fable" and probe.custom_id != original.custom_id
    assert (original.system, original.messages, original.model, original.effort) == (
        probe.system, probe.messages, probe.model, probe.effort)
    assert probe.max_tokens == 64000


def test_author_probe_respects_one_attempt_and_lower_spend_cap(tmp_path, monkeypatch):
    calls = []

    def truncate(req, model):
        calls.append(req)
        return Response(req.custom_id, model.model_id, "truncated", "", cost=0.01)

    monkeypatch.setitem(store.LIVE, "anthropic", truncate)
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    ns = types.SimpleNamespace(worlds=1, only=None, mode="live", max_tokens=64000, attempts=1, spend_cap=4)
    with pytest.raises(RuntimeError, match="incomplete after 1 attempts"):
        cli.cmd_author(ns)
    assert len(calls) == 1 and calls[0].max_tokens == 64000
    ns.spend_cap = 0.02
    ns.max_tokens = 32000
    with pytest.raises(store.CapExceeded):
        cli.cmd_author(ns)
    assert len(calls) == 1


def test_cost_reduction_is_a_deterministic_prefix_without_changing_world_cap():
    from finalphase.authoring import sample_main

    qs = [{"question_id": f"W{w:03d}-Q{i:02d}", "world_id": f"W{w:03d}"}
          for w in range(1, 6) for i in range(12)]
    full = sample_main(qs)
    reduced = sample_main(qs, cap=20)
    assert len(reduced) == 20
    assert {q["question_id"] for q in reduced} <= {q["question_id"] for q in full}
    assert reduced == sample_main(list(reversed(qs)), cap=20)
    assert all(sum(q["world_id"] == f"W{w:03d}" for q in reduced) <= 9 for w in range(1, 6))
    with pytest.raises(ValueError, match="between 1 and 1068"):
        sample_main(qs, cap=0)
