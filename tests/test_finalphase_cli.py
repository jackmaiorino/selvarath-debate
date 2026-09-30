import json
import types

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
    cli.cmd_split(ns())
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
                     no_top2=False, limit=3, workers=4))
    judged = [json.loads(x) for x in open(tmp_path / "main" / "judgments.jsonl")]
    assert len(judged) == 3 * 2 * 7 * 2
    assert all(j["parse_ok"] for j in judged)
    k2 = [j for j in judged if j["arm"] == "debate_k2"]
    assert all(j["n_queries_used"] == 1 for j in k2)
