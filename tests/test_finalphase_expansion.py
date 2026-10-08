import json
from dataclasses import replace
from decimal import Decimal

import pytest

from finalphase import authoring as A, expansion as E, preflight
from finalphase.providers import BillingError, _billing_check
from finalphase.store import estimate_max_cost
from test_finalphase_cli import _world


def audit_fixture(root):
    hashes = {}
    for wid, author, hint in A.world_ids(2):
        obj = _world(1)
        obj.update(world_id=wid, author=author, seed_hint=hint)
        path = root / "bench/worlds" / f"{wid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))
        hashes[wid] = E.digest(path.read_bytes())
    rows = [{"question_id": "W001-Q01", "world_id": "W001", "retained": True},
            {"question_id": "W001-Q02", "world_id": "W001", "retained": True}]
    path = root / "bench/validation.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    packet = {"random_retained_sample_ids": ["W001-Q01"], "split_validator_ids": [], "worlds_sha256": hashes}
    packet_path = root / "validation_audit_packet.json"
    packet_path.write_text(json.dumps(packet))
    review = {"reviewer": "Claude coordinating session", "session_id": E.COORDINATOR_SESSION,
              "implementer_review": False, "audit_complete": True,
              "packet_sha256": E.digest(packet_path.read_bytes()), "validation_rows_sha256": E.digest(path.read_bytes()),
              "retained_source_defect_worlds": [],
              "questions": [{"question_id": "W001-Q01", "evidence": "source quote", "conclusion": "pass"}]}
    (root / "coordinator_audit_review.json").write_text(json.dumps(review))
    return hashes, review


def test_implementer_review_cannot_clear_audit_and_defect_requires_whole_world(tmp_path):
    _, review = audit_fixture(tmp_path)
    assert not E.audit_reasons(tmp_path)
    review["implementer_review"] = True
    path = tmp_path / "coordinator_audit_review.json"
    path.write_text(json.dumps(review))
    assert E.audit_reasons(tmp_path)
    review["implementer_review"] = False
    review["retained_source_defect_worlds"] = ["W001"]
    review["source_defects_resolved"] = True
    path.write_text(json.dumps(review))
    assert "defect-world" in E.audit_reasons(tmp_path)[0]
    review["questions"].append({"question_id": "W001-Q02", "evidence": "second source", "conclusion": "pass"})
    path.write_text(json.dumps(review))
    assert not E.audit_reasons(tmp_path)
    (tmp_path / "bench/worlds/W001.json").write_text("changed")
    assert "world hashes" in E.audit_reasons(tmp_path)[0]


def test_author_reservation_requires_org_capacity_and_exact_scope_before_network(tmp_path):
    baseline, _ = audit_fixture(tmp_path)
    requests = E.author_requests(tmp_path, worlds=4, baseline=baseline)
    approval = {"approved": True, "source": "offline owner fixture", "worlds": 4, "attempts": 2, "mode": "batch",
                "baseline_worlds_sha256": baseline, "requests_sha256": E.digest(json.dumps([r.to_json() for r in requests]).encode()),
                "provider_capacity": {p: {"available_usd": 100, "reconciled": True, "reference": "fixture"} for p in ("anthropic", "openai")},
                "openai_capacity": {"organization_approved_remaining_usd": 100,
                    "organization_hard_limit_enabled": False, "project_hard_limit_enabled": False, "reference": "fixture"}}
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps({"author_expansion_authorization": approval}))
    assert E.author_plan(tmp_path, 4)["ready"]
    approval["openai_capacity"]["organization_approved_remaining_usd"] = 0
    path.write_text(json.dumps({"author_expansion_authorization": approval}))
    report = E.author_plan(tmp_path, 4)
    assert not report["ready"] and any("organization_approved" in r for r in report["reasons"])
    assert not (tmp_path / "author.db").exists() and not report["provider_client_created"]
    assert not E.author_plan(tmp_path, 6)["ready"]



def test_author_reservation_is_bounded_by_the_amended_author_stage_cap(tmp_path, monkeypatch):
    from finalphase import cli
    assert cli.STAGE_CAPS == {"author": 173.0, "validate": 893.0, "canary": 60.0, "pilot": 650.0, "main": 3634.0}
    baseline, _ = audit_fixture(tmp_path)
    requests = E.author_requests(tmp_path, worlds=4, baseline=baseline)
    approval = {"approved": True, "source": "offline owner fixture", "worlds": 4, "attempts": 2, "mode": "batch",
                "baseline_worlds_sha256": baseline, "requests_sha256": E.digest(json.dumps([r.to_json() for r in requests]).encode()),
                "provider_capacity": {p: {"available_usd": 100, "reconciled": True, "reference": "fixture"} for p in ("anthropic", "openai")},
                "openai_capacity": {"organization_approved_remaining_usd": 100,
                    "organization_hard_limit_enabled": False, "project_hard_limit_enabled": False, "reference": "fixture"}}
    (tmp_path / "run_manifest.json").write_text(json.dumps({"author_expansion_authorization": approval}))
    assert E.author_plan(tmp_path, 4)["ready"]
    monkeypatch.setitem(cli.STAGE_CAPS, "author", 1.0)
    report = E.author_plan(tmp_path, 4)
    assert not report["ready"] and any("author stage cap" in r for r in report["reasons"])

def test_decimal_maximum_matches_store_bound_and_proposed_token_cap_is_dormant():
    req = A.author_request("W009", "fable", "river delta")
    assert float(E.maximum(req, True)) == pytest.approx(estimate_max_cost(req, True))
    original = A.validation_requests("W001", "fable", "world", _world(1)["questions"])
    assert all(r.max_tokens == (2000 if r.custom_id.startswith("factcheck:") else 4000) for r in original)
    r = next(r for r in original if r.model == "dspro" and r.custom_id.startswith("validate:"))
    proposed = replace(r, max_tokens=E.PROPOSED_DSPRO_KEY_TOKENS)
    assert E.maximum(proposed, False) - E.maximum(r, False) == Decimal("0.03168")



def test_expansion_worlds_check_dspro_answer_keys_at_the_v2_allowance():
    world = _world(1)
    cohort = A.validation_requests("W008", "fable", "world", world["questions"])
    expansion = A.validation_requests("W009", "fable", "world", world["questions"])
    assert [r.custom_id.replace("W008", "W009") for r in cohort] == [r.custom_id for r in expansion]
    for r in expansion:
        if r.custom_id.startswith("factcheck:"):
            assert r.max_tokens == 2000
        else:
            assert r.max_tokens == (12000 if r.model == "dspro" else 4000)
    assert all(r.max_tokens in (2000, 4000) for r in cohort)


def test_exact_authorized_request_set_replaces_the_estimated_envelope(tmp_path):
    from finalphase import validation as V
    from test_finalphase_validation import prepared
    worlds, manifest = prepared(tmp_path)
    for i in range(3, 10):
        world = {**worlds[i % 2], "world_id": f"W{i:03}"}
        (tmp_path / "bench/worlds" / f"W{i:03}.json").write_text(json.dumps(world))
        worlds.append(world)
    execution = manifest["validation_execution"]
    execution["worlds_sha256"] = {p.stem: E.digest(p.read_bytes()) for p in (tmp_path / "bench/worlds").glob("*.json")}
    _, requests = V.workload(worlds)
    execution["authorization"]["validation_input_sha256"] = V.input_sha256(requests)
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    assert not any("byte/token envelope" in r for r in V.plan(tmp_path, worlds)["reasons"])
    execution["authorization"]["validation_input_sha256"] = "0" * 64
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    assert any("byte/token envelope" in r for r in V.plan(tmp_path, worlds)["reasons"])
    report = V.plan(tmp_path, worlds, cap=0.0001)
    assert any("$0.00 stage cap" in r for r in report["reasons"])

@pytest.mark.parametrize("code", ["organization_spend_limit_exceeded", "project_spend_limit_exceeded", "organization_usage_limit_exceeded"])
def test_provider_capacity_429_is_a_billing_stop_not_transport_retry(code):
    error = RuntimeError(code)
    error.status_code = 429
    with pytest.raises(BillingError):
        _billing_check("openai", error)


def test_stop_file_blocks_even_bounded_launch_preflight(tmp_path):
    (tmp_path / "STOP").touch()
    assert "STOP file is present" in preflight.check("author", tmp_path, 8, "batch", quality_check=True)["reasons"]


def test_qualification_refusal_happens_before_store_or_network(tmp_path, monkeypatch):
    from finalphase import cli, store
    from scripts import finalphase_expansion_qualify as qualifier
    baseline, _ = audit_fixture(tmp_path)
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(cli, "BENCH", tmp_path / "bench")
    (tmp_path / "validation_completion_receipt.json").write_text(json.dumps({
        "conservative_campaign_cost_bound_usd": 30.8998584, "known_campaign_cost_usd": 30.85046532,
        "observed_stage_cost_usd": 8.76297396, "conservative_stage_cost_bound_usd": 8.81236704}))
    proposal = {"version": E.QUALIFICATION_VERSION, "maximum_cost_usd": "7.84778520",
                "maximum_cost_by_provider_usd": {"anthropic": "3.26758", "openai": "1.66055", "together": "2.91965520"},
                "worlds_sha256": baseline, "validation_rows_sha256": E.digest((tmp_path / "bench/validation.jsonl").read_bytes()),
                "requests_sha256": "a" * 64, "execution_sha256": "b" * 64}
    calls = []
    monkeypatch.setattr(store, "Store", lambda *args, **kwargs: calls.append(True))
    with pytest.raises(RuntimeError, match="lacks separate paid approval"):
        qualifier.execute(tmp_path, proposal, {})
    assert not calls and not (tmp_path / E.QUALIFICATION_DB).exists()


def test_v2_author_qualification_controls_use_the_amended_fable_allowance():
    controls = E.author_qualification_controls()
    assert [r.custom_id for r in controls] == [
        f"qualification:author-expansion-v2:{arm}:{wid}:{author}"
        for arm in ("serial", "parallel") for wid, author in (("W001", "fable"), ("W002", "astra"))]
    assert {r.model: r.max_tokens for r in controls} == {"fable": 128000, "astra": 32000}
    # The failed v1 receipt and store are never reused by the v2 rerun.
    assert E.QUALIFICATION_RECEIPT != "expansion_qualification_receipt.json"
    assert E.QUALIFICATION_DB != "expansion_qualification.db"
    fable = next(r for r in controls if r.model == "fable")
    assert E.maximum(fable, True) > E.maximum(replace(fable, max_tokens=64000), True)


def test_future_validation_envelope_refuses_oversized_request_before_registration(tmp_path):
    from finalphase import validation as V
    from test_finalphase_validation import prepared
    worlds, manifest = prepared(tmp_path)
    for i in range(3, 10):
        world = {**worlds[i % 2], "world_id": f"W{i:03}"}
        path = tmp_path / "bench/worlds" / f"W{i:03}.json"
        path.write_text(json.dumps(world))
        worlds.append(world)
    execution = manifest["validation_execution"]
    execution["worlds_sha256"] = {p.stem: E.digest(p.read_bytes()) for p in (tmp_path / "bench/worlds").glob("*.json")}
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    report = V.plan(tmp_path, worlds)
    assert any("byte/token envelope" in r for r in report["reasons"])
    execution["input_envelope_usd"] = {f"{a}:{role}": 1 for a in ("fable", "astra") for role in ("frontier_key", "dspro_key", "fact")}
    (tmp_path / "run_manifest.json").write_text(json.dumps(manifest))
    assert not any("byte/token envelope" in r for r in V.plan(tmp_path, worlds)["reasons"])
    assert not (tmp_path / "validate.db").exists()


def test_exact_reserved_cohort_is_not_refused_by_float_addition_noise(tmp_path):
    from finalphase.providers import Request
    from finalphase.store import Store
    requests = [Request(str(i), "dspro", "x" * 10000, (("user", "abc"),), 12000) for i in range(3)]
    cap = sum((E.maximum(r, False) for r in requests), Decimal(0))
    assert sum(estimate_max_cost(r, False) for r in requests) > float(cap)
    s = Store(tmp_path / "scratch.db", "qualification", float(cap), max_transport_attempts=1)
    try:
        s._check_cap(requests, False)
    finally:
        s.db.close()
