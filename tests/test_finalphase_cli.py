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
    return {"world_name": f"World {n}", "world_text": "word " * 1200, "questions": qs}


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
    # validate was trimmed to its closed spend; a fresh offline run plans at the pre-closure cap
    monkeypatch.setitem(cli.STAGE_CAPS, "validate", 893.0)
    ns = types.SimpleNamespace
    cli.cmd_author(ns(worlds=24, only=None, mode="live"))
    assert len(list((tmp_path / "bench" / "worlds").glob("*.json"))) == 24
    from finalphase import validation as v
    import platform
    (tmp_path / "run_manifest.json").write_text(json.dumps({"validation_execution": {
        "authorization": {"source": "offline test fixture"},
        "worlds_sha256": {p.stem: v.sha(p.read_bytes()) for p in (tmp_path / "bench/worlds").glob("*.json")},
        "account_balances_at_stage_start": {p: {"available_usd": 1000, "reference": "offline fixture"}
                                            for p in ("openai", "anthropic", "together")},
        "openai_account_limits": {"monthly_remaining_usd": 1000, "project_hard_limit_enabled": False,
                                  "queue_confirmed": True, "reference": "fixture"},
        "batch_limits": {p: {"max_input_tokens": 1500000} for p in ("astra", "fable")},
        "input_envelope_usd": {f"{a}:{role}": 1 for a in ("fable", "astra") for role in ("frontier_key", "dspro_key", "fact")},
        "placements": {"host": platform.node(), "checked": ["Jack's PC", "HaleysPC", "RunPod"],
                       "reference": "offline fixture"}}}))
    cli.cmd_validate(ns(mode="live", workers=8))
    rows = [json.loads(x) for x in open(tmp_path / "bench" / "validation.jsonl")]
    assert len(rows) == 288 and all(r["retained"] for r in rows)
    cli.cmd_split(ns(main_questions=1068))
    canary = [json.loads(x) for x in open(tmp_path / "bench" / "canary.jsonl")]
    pilot = [json.loads(x) for x in open(tmp_path / "bench" / "pilot.jsonl")]
    main = [json.loads(x) for x in open(tmp_path / "bench" / "main.jsonl")]
    assert {q["world_id"] for q in canary}.isdisjoint({q["world_id"] for q in main})
    assert len(canary) == 48 and len(pilot) == 16 * 12 and len(main) == 4 * 9
    assert all(q["debater"] in ("fable", "astra") for q in main)
    from finalphase import canary as C
    receipt = C.qualify(tmp_path, {w["world_id"]: w for w in cli._worlds()}, cli.STAGE_CAPS["canary"])
    assert receipt["semantics_preserved"] and receipt["serial"]["input_sha256"] == receipt["parallel"]["input_sha256"]
    assert json.loads((tmp_path / "run_manifest.json").read_text())["throughput"]["canary"] == receipt
    sent = []
    monkeypatch.setitem(store.LIVE, "anthropic", lambda r, m: sent.append(r.custom_id) or fake(r, m))
    monkeypatch.setitem(store.LIVE, "openai", lambda r, m: sent.append(r.custom_id) or fake(r, m))
    cli.cmd_canary(ns(n=4))
    assert sent and not set(receipt["parallel"]["request_ids"]) & set(sent)
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

    original = author_request("W001", "fable", "river delta", max_tokens=32000)
    probe = author_request("W001", "fable", "river delta", max_tokens=64000)
    assert original.custom_id == "author:W001:fable:length-v2" and probe.custom_id != original.custom_id
    assert (original.system, original.messages, original.model, original.effort) == (
        probe.system, probe.messages, probe.model, probe.effort)
    assert probe.max_tokens == 64000


def test_author_defaults_use_the_measured_allowance_only_for_fable():
    from finalphase.authoring import author_request

    fable = author_request("W001", "fable", "river delta")
    astra = author_request("W002", "astra", "river delta")
    assert fable.max_tokens == 64000 and fable.custom_id.endswith(":t64000:length-v2")
    assert astra.max_tokens == 32000 and astra.custom_id == "author:W002:astra"


def test_expansion_worlds_author_fable_at_the_amended_allowance():
    from finalphase.authoring import author_request

    cohort = author_request("W007", "fable", "river delta")
    later = author_request("W009", "fable", "river delta")
    astra = author_request("W010", "astra", "river delta")
    assert cohort.max_tokens == 64000 and cohort.custom_id.endswith(":t64000:length-v2")
    assert later.max_tokens == 128000 and later.custom_id == "author:W009:fable:t128000:length-v2"
    assert astra.max_tokens == 32000 and astra.custom_id == "author:W010:astra"
    assert (cohort.system, cohort.model, cohort.effort) == (later.system, later.model, later.effort)


@pytest.mark.parametrize("words,ok", [(999, False), (1000, True), (1500, True), (1501, False)])
def test_world_admission_word_boundaries(words, ok):
    from finalphase.authoring import world_check
    world = _world(1)
    world["world_text"] = "word " * words
    assert world_check(world).ok is ok


@pytest.mark.parametrize("defect", ["questions", "task_type", "reasoning", "ledger"])
def test_world_admission_preserves_question_schema_and_types(defect):
    from finalphase.authoring import world_check
    world = _world(1)
    if defect == "questions":
        world["questions"].pop()
    elif defect == "task_type":
        for q in world["questions"]:
            q["task_type"] = TYPES[0]
    else:
        del world["questions"][0][defect]
    assert not world_check(world).ok


def test_amendment_versions_fable_and_preserves_astra_and_legacy_requests():
    from finalphase.authoring import author_request
    old = author_request("W001", "fable", "river delta", prompt_revision="v1")
    new = author_request("W001", "fable", "river delta")
    assert old.custom_id == "author:W001:fable:t64000"
    assert new.custom_id == old.custom_id + ":length-v2"
    assert new.messages[0][1].startswith(old.messages[0][1])
    assert new.system == old.system and new.effort == old.effort
    assert author_request("W002", "astra", "river delta").to_json() == author_request(
        "W002", "astra", "river delta", prompt_revision="v1").to_json()


def test_invalid_saved_world_requires_explicit_replacement_and_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    path = tmp_path / "bench/worlds/W001.json"
    path.parent.mkdir(parents=True)
    original = _world(1)
    original["world_text"] = "word " * 1600
    data = json.dumps(original).encode()
    path.write_bytes(data)
    calls = []
    def live(req, model):
        calls.append(req)
        return Response(req.custom_id, model.model_id, "ok", json.dumps(_world(1)))
    monkeypatch.setitem(store.LIVE, "anthropic", live)
    ns = types.SimpleNamespace(worlds=1, only=None, mode="live", attempts=1)
    with pytest.raises(RuntimeError, match="invalid saved world"):
        cli.cmd_author(ns)
    assert calls == [] and path.read_bytes() == data
    ns.replace_invalid = True
    cli.cmd_author(ns)
    assert len(calls) == 1 and json.loads(path.read_bytes())["world_text"] == _world(1)["world_text"]
    assert data in [p.read_bytes() for p in (tmp_path / "bench/versions").rglob("W001.json")]
    cli.cmd_author(ns)
    assert len(calls) == 1


def test_rejected_generation_is_recorded_and_does_not_replace_original(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    world = _world(1)
    world["world_text"] = "word " * 1501
    monkeypatch.setitem(store.LIVE, "anthropic", lambda r, m: Response(r.custom_id, m.model_id, "ok", json.dumps(world)))
    with pytest.raises(RuntimeError, match="incomplete"):
        cli.cmd_author(types.SimpleNamespace(worlds=1, only=None, mode="live", attempts=1))
    assert not list((tmp_path / "bench/worlds").glob("*.json"))
    assert "world_word_count:1501" in (tmp_path / "author_rejections.jsonl").read_text()
    with pytest.raises(ValueError, match="invalid saved world"):
        (tmp_path / "bench/worlds/W001.json").write_text(json.dumps(world))
        cli._worlds()


def test_author_reserves_all_generation_retries_before_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    calls = []
    monkeypatch.setitem(store.LIVE, "anthropic", lambda *args: calls.append(args))
    with pytest.raises(store.CapExceeded):
        cli.cmd_author(types.SimpleNamespace(worlds=1, only=None, mode="live", attempts=2, spend_cap=4))
    assert calls == []


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


def test_sensitivity_only_questions_ride_outside_the_main_cap(tmp_path, monkeypatch):
    from finalphase import authoring as A

    worlds = [{"world_id": f"W{w:03d}", "author": ("fable", "astra")[w % 2],
               "questions": [{"task_type": TYPES[i % 6]} for i in range(12)]} for w in range(1, 41)]
    rows = [{"question_id": f"{w['world_id']}-Q{i + 1:02d}", "world_id": w["world_id"], "author": w["author"],
             "index": i, "retained": True, "determinacy_flags": 0} for w in worlds for i in range(12)]
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "validation.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    monkeypatch.setattr(cli, "_worlds", lambda: worlds)

    def split(flagged):
        monkeypatch.setattr(A, "SENSITIVITY_ONLY", frozenset(flagged))
        cli.cmd_split(types.SimpleNamespace(main_questions=100))
        return {n: [json.loads(x) for x in open(tmp_path / "bench" / f"{n}.jsonl")] for n in ("canary", "pilot", "main")}

    base = split(set())
    base_ids = [q["question_id"] for q in base["main"]]
    sampled, pilot_q = base_ids[0], base["pilot"][0]["question_id"]
    flagged = split({sampled, pilot_q})
    counted = [q["question_id"] for q in flagged["main"] if not q["sensitivity_only"]]
    assert len(counted) == 100 and sampled not in counted
    assert [q["question_id"] for q in flagged["main"] if q["sensitivity_only"]] == [sampled]
    world = sampled.split("-Q")[0]
    assert [q for q in counted if not q.startswith(world)] == [q for q in base_ids if not q.startswith(world)]
    assert [q["question_id"] for q in flagged["pilot"] if q["sensitivity_only"]] == [pilot_q]
