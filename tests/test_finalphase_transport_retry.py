import json
import platform
import sqlite3

import pytest

from finalphase import cli, preflight, store, validation as v
from finalphase.providers import Response
from scripts import finalphase_validation_retry as retry
from test_finalphase_validation import prepared


def fixture(tmp_path, monkeypatch):
    worlds, manifest = prepared(tmp_path)
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    monkeypatch.setattr(preflight, "source_commit", lambda: "commit")
    monkeypatch.setattr(preflight, "execution_sha256", lambda: "fingerprint")
    for name in preflight.PROVIDER_KEYS:
        monkeypatch.setenv(name, "fixture")
    manifest.update(git_commit="commit", ceiling_usd=6000,
                    provider_checks={p: "passed" for p in ("openai", "anthropic", "together")})
    manifest["throughput"] = {"validate": {
        "execution_sha256": "fingerprint", "host": platform.node(), "mode": "batch",
        "selected_workers": 8, "semantics_preserved": True,
        "placements_checked": ["Jack's PC", "HaleysPC", "RunPod"],
        "serial": {"workers": 1, "completed": 8, "elapsed_seconds": 8,
                   "input_sha256": "a" * 64, "output_sha256": "b" * 64},
        "parallel": {"workers": 8, "completed": 8, "elapsed_seconds": 2,
                     "input_sha256": "a" * 64, "output_sha256": "c" * 64}}}
    manifest["validation_execution"]["supervisor_exited"] = True
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    _, requests = v.workload(worlds)
    failed = next(request for request in requests if request.model == "dspro" and request.custom_id.startswith("factcheck:"))
    ledger = store.Store(tmp_path / "validate.db", "validate", 200, max_transport_attempts=1)
    for request in requests + v.controls(v.qualification_sample(worlds, requests)):
        ledger._register(request)
        if request.custom_id == failed.custom_id:
            ledger._mark(request.custom_id, "transport_failed", attempts_inc=1)
            ledger.log("transport", request.custom_id + " provider 503 preserved")
        else:
            ledger._save(Response(request.custom_id, request.model, "ok", "YES"))
    ledger.db.close()
    plan = retry.prepare(tmp_path)
    return manifest, failed, plan


def authorize(tmp_path, plan):
    plan["authorization"] = {"source": "explicit offline test authorization"}
    v.atomic_json(tmp_path / "validation_retry_reservation.json", plan)


def test_retry_requires_authorization_and_preserves_provider_attempts(tmp_path, monkeypatch):
    _, _, plan = fixture(tmp_path, monkeypatch)
    monkeypatch.setitem(store.LIVE, "together", lambda *args: pytest.fail("unauthorized provider call"))
    with pytest.raises(RuntimeError, match="authorization is missing"):
        retry.execute(tmp_path)
    db = sqlite3.connect(tmp_path / "validate.db")
    assert db.execute("SELECT COUNT(*) FROM calls WHERE custom_id LIKE 'transport-retry:%'").fetchone()[0] == 0
    db.close()


def test_approved_retry_keeps_bodies_errors_charges_and_scientific_identity(tmp_path, monkeypatch):
    _, failed, plan = fixture(tmp_path, monkeypatch)
    authorize(tmp_path, plan)
    called = []

    def live(request, model):
        called.append(request)
        assert v.input_sha256([request]) == v.input_sha256([failed])
        return Response(request.custom_id, model.model_id, "ok", "YES", cost=.001)

    monkeypatch.setitem(store.LIVE, "together", live)
    report = retry.execute(tmp_path)
    assert report["status"] == "collected" and len(called) == 1
    ledger = store.Store(tmp_path / "validate.db", "validate", 200, max_transport_attempts=1)
    result = ledger.get(failed.custom_id)
    assert result is not None and result.text == "YES" and result.cost == 0
    assert ledger.spent("validate") == pytest.approx(.001)
    assert ledger._q("SELECT request,req_hash,attempts FROM calls WHERE custom_id=?", (failed.custom_id,)) == [
        (failed.to_json(), store._hash(failed), 1)]
    assert ledger._q("SELECT detail FROM events WHERE kind='transport'")
    ledger.db.close()
    original = sqlite3.connect(tmp_path / "preserved/pre-transport-retry/validate.db")
    assert original.execute("SELECT status FROM calls WHERE custom_id=?", (failed.custom_id,)).fetchone()[0] == "transport_failed"
    original.close()
    assert preflight.check("validate", tmp_path, 8, "batch")["ready"]
    with pytest.raises(RuntimeError, match="settled transport failures"):
        retry.execute(tmp_path)
    assert len(called) == 1


def test_proven_unsent_reserved_retry_resumes_without_resetting_history(tmp_path, monkeypatch):
    _, failed, plan = fixture(tmp_path, monkeypatch)
    authorize(tmp_path, plan)
    from dataclasses import replace
    request = replace(failed, custom_id=plan["requests"][0]["retry_id"])
    ledger = store.Store(tmp_path / "validate.db", "validate", 200, max_transport_attempts=1)
    ledger._register(request)
    ledger.db.close()
    called = []

    def live(request, model):
        called.append(request.custom_id)
        return Response(request.custom_id, model.model_id, "ok", "YES")

    monkeypatch.setitem(store.LIVE, "together", live)
    assert retry.execute(tmp_path)["status"] == "collected"
    assert called == [request.custom_id]


@pytest.mark.parametrize("change", ("funding", "world", "throughput", "inflight", "measured"))
def test_retry_cannot_replace_other_launch_guards(tmp_path, monkeypatch, change):
    manifest, failed, plan = fixture(tmp_path, monkeypatch)
    authorize(tmp_path, plan)
    if change == "funding":
        manifest["validation_execution"]["account_balances_at_stage_start"]["together"]["available_usd"] = 0
    elif change == "world":
        manifest["validation_execution"]["worlds_sha256"] = {}
    elif change == "throughput":
        manifest["throughput"]["validate"]["semantics_preserved"] = False
    else:
        db = sqlite3.connect(tmp_path / "validate.db")
        db.execute("UPDATE calls SET status=? WHERE custom_id=?",
                   ("live_in_flight" if change == "inflight" else "truncated", failed.custom_id))
        db.commit()
        db.close()
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setitem(store.LIVE, "together", lambda *args: pytest.fail("guard failure must not dispatch"))
    with pytest.raises(RuntimeError):
        retry.execute(tmp_path)
