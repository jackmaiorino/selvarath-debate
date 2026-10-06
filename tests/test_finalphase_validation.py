import json
import platform

import pytest

from finalphase import preflight, store, validation as v
from finalphase.providers import Response
from test_finalphase_cli import _world


def prepared(tmp_path):
    worlds = []
    for i, author in ((1, "fable"), (2, "astra")):
        world = _world(i)
        world.update(world_id=f"W{i:03}", author=author)
        path = tmp_path / "bench/worlds" / f"W{i:03}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(world))
        worlds.append(world)
    manifest = {"validation_execution": {
        "authorization": {"source": "offline fixture"},
        "worlds_sha256": {p.stem: v.sha(p.read_bytes()) for p in (tmp_path / "bench/worlds").glob("*.json")},
        "account_balances_at_stage_start": {p: {"available_usd": 100, "reference": "fixture"}
                                            for p in ("anthropic", "openai", "together")},
        "placements": {"host": platform.node(), "checked": ["Jack's PC", "HaleysPC", "RunPod"],
                       "reference": "fixture"}}}
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    return worlds, manifest


def test_reservation_blocks_missing_credit_or_changed_world_without_creating_store(tmp_path):
    worlds, manifest = prepared(tmp_path)
    del manifest["validation_execution"]["account_balances_at_stage_start"]["together"]
    manifest["validation_execution"]["account_balances_at_stage_start"]["openai"]["available_usd"] = 0
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    report = v.plan(tmp_path, worlds)
    assert not report["ready"] and report["credit_gap_by_provider_usd"]["together"] is None
    assert report["credit_gap_by_provider_usd"]["openai"] > 0
    with pytest.raises(RuntimeError, match="reservation refused"):
        v.require_plan(tmp_path, worlds, "batch")
    assert not (tmp_path / "validate.db").exists()
    (tmp_path / "bench/worlds/W001.json").write_text("changed")
    assert any("authorized validation scope" in reason for reason in v.plan(tmp_path, worlds)["reasons"])


def test_read_only_preflight_checks_credit_even_for_bounded_qualification(tmp_path, monkeypatch):
    _, manifest = prepared(tmp_path)
    manifest.update(git_commit="commit", ceiling_usd=6000,
                    provider_checks={p: "passed" for p in ("anthropic", "openai", "together")})
    manifest["validation_execution"]["account_balances_at_stage_start"]["openai"]["available_usd"] = 0
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(preflight, "source_commit", lambda: "commit")
    for key in preflight.PROVIDER_KEYS:
        monkeypatch.setenv(key, "fake")
    report = preflight.check("validate", tmp_path, 8, "batch", qualification=True)
    assert not report["ready"] and any("openai: credit gap" in r for r in report["reasons"])
    assert not report["provider_client_created"] and not (tmp_path / "validate.db").exists()


def test_real_qualification_keeps_controls_separate_and_reuses_canonical_responses(tmp_path, monkeypatch):
    worlds, _ = prepared(tmp_path)
    calls = []

    def live(req, model):
        calls.append(req)
        text = "YES" if "factcheck:" in req.custom_id else (
            "ANSWER: A\nOTHER_EQUALLY_DEFENSIBLE: no\nREVERSING_READING_EXISTS: yes")
        return Response(req.custom_id, model.model_id, "ok", text, cost=0.001)

    monkeypatch.setitem(store.LIVE, "together", live)
    batches = {}

    def submit(reqs, model, label):
        batches[model.provider] = reqs
        return model.provider

    def collect(bid, model):
        return {req.custom_id: live(req, model) for req in batches[bid]}

    for provider in ("openai", "anthropic"):
        monkeypatch.setitem(store.BATCH, provider, (submit, lambda bid: ("ended", {}), collect))
    monkeypatch.setattr(preflight, "source_commit", lambda: "commit")
    monkeypatch.setattr(preflight, "execution_sha256", lambda: "fingerprint")
    ticks = iter((0, 8, 8, 10, 10, 12))
    monkeypatch.setattr(v.time, "perf_counter", lambda: next(ticks))
    receipt = v.qualify(tmp_path, worlds)
    assert receipt["selected_workers"] == 8 and receipt["semantics_preserved"]
    assert receipt["serial"]["input_sha256"] == receipt["parallel"]["input_sha256"]
    assert receipt["control_used_for_retention"] is False and len(calls) == 20
    assert receipt["frontier_probe"]["completed"] == 4
    _, requests = v.workload(worlds)
    sample = v.qualification_sample(worlds, requests)
    s = store.Store(tmp_path / "validate.db", "validate", 200, max_transport_attempts=1)
    assert len(s.run(sample, mode="batch", allow_live_fallback=False)) == 8
    s.db.close()
    v.qualify(tmp_path, worlds)
    assert len(calls) == 20
    assert all(r.max_tokens == (2000 if "factcheck:" in r.custom_id else 4000) for r in calls)
    assert all(not r.effort for r in calls)


def test_invalid_qualification_outputs_do_not_admit_paid_campaign(tmp_path, monkeypatch):
    worlds, _ = prepared(tmp_path)
    monkeypatch.setitem(store.LIVE, "together", lambda req, model:
        Response(req.custom_id, model.model_id, "ok", "malformed"))
    monkeypatch.setattr(preflight, "source_commit", lambda: "commit")
    with pytest.raises(RuntimeError, match="did not complete valid"):
        v.qualify(tmp_path, worlds)
    manifest = json.loads((tmp_path / "run_manifest.json").read_text())
    assert manifest["validation_execution"]["status"] == "qualification_needs_attention"
    assert "throughput" not in manifest
