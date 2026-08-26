from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from rejudge import api_client, phase3_plan, phase3_v3_live
from rejudge.phase2_canary_fixtures import DeterministicCanaryClient
from rejudge.phase2_canary_order import CellResultStore


ROOT = Path(__file__).resolve().parents[1]


def _protocol():
    return phase3_plan.load_protocol(ROOT / phase3_v3_live.PROTOCOL_RELATIVE_PATH)


def _output_paths(archive: Path) -> dict[str, str]:
    names = {
        "harness_1_results": "h1-results.jsonl",
        "harness_1_cache": "h1-cache.jsonl",
        "harness_2_results": "h2-results.jsonl",
        "harness_2_cache": "h2-cache.jsonl",
        "formal_results": "formal-results.jsonl",
        "formal_decisions": "formal-decisions.jsonl",
        "formal_cache": "formal-cache.jsonl",
        "usage_ledger": "usage.jsonl",
        "usage_state": "usage.jsonl.state.json",
        "formal_error_log": "errors.jsonl",
        "run_log": "run.jsonl",
        "reviewer_worklist": "reviewer_worklist.json",
        "reviewer_index": "reviewer-index.jsonl",
        "harness_verified_manifest": "harness-verified.json",
        "final_manifest": "final-manifest.json",
        "final_report": "final-report.json",
        "run_lock": "run.lock",
    }
    return {"archive_dir": archive.as_posix(),
            **{key: (archive / name).as_posix() for key, name in names.items()}}


def _authorization(manifest: dict, manifest_path: Path, protocol: dict) -> dict:
    return {
        "schema_version": phase3_v3_live.AUTHORIZATION_SCHEMA,
        "execution_authorized": True,
        "main_run_spend_authorized": False,
        "recorded_at_utc": "2026-08-24T13:00:00Z",
        "binds": {
            "run_id": manifest["run_id"],
            "run_manifest_canonical_sha256": phase3_v3_live.canonical_sha256(manifest),
            "run_manifest_tracked_path": manifest_path.name,
            "protocol_tracked_path": phase3_v3_live.PROTOCOL_RELATIVE_PATH,
            "protocol_canonical_sha256": phase3_v3_live.canonical_sha256(protocol),
            "harness_seed_name": "harness",
            "harness_seed": 7,
        },
        "scope": {
            "incremental_cap_usd": 60,
            "harness_execution_count": 2,
            "formal_successor_canary_execution_count": 1,
            "successor_canary_fresh_gate_slots": 960,
            "main_run_spend_authorized": False,
            "gpu_ordinal_or_not_used": "not_used",
        },
        "owner_authorization": {
            "approver": "Jack Maiorino",
            "exact_text": (
                f"Approved: {manifest['run_id']} harness and successor canary, $60 USD "
                "incremental cap, no main spend"),
        },
    }


def test_real_v3_role_limits_cover_every_billed_model_role():
    protocol = _protocol()
    role_limits = json.loads(
        (ROOT / phase3_v3_live.ROLE_LIMITS_RELATIVE_PATH).read_text(encoding="utf-8"))
    phase3_v3_live._validate_role_limits(role_limits, protocol)


def test_role_limits_refuse_reasoning_roster_drift():
    protocol = _protocol()
    role_limits = json.loads(
        (ROOT / phase3_v3_live.ROLE_LIMITS_RELATIVE_PATH).read_text(encoding="utf-8"))
    drifted = copy.deepcopy(role_limits)
    drifted["reasoning_models"]["model_ids"].remove("Qwen/Qwen3.5-397B-A17B")
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="reasoning-model roster"):
        phase3_v3_live._validate_role_limits(drifted, protocol)


def test_authorization_rejects_any_main_scope(tmp_path: Path):
    protocol = _protocol()
    manifest = {
        "run_id": "phase3-v3-test",
        "harness_check": {"seed_name": "harness"},
        "seeds": {"harness": 7},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    authorization = _authorization(manifest, manifest_path, protocol)
    authorization["scope"]["main_run_spend_authorized"] = True
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="prohibit main"):
        phase3_v3_live.validate_authorization(
            authorization, manifest, manifest_path=manifest_path, protocol=protocol)


def test_authorization_rejects_cap_other_than_exact_owner_limit(tmp_path: Path):
    protocol = _protocol()
    manifest = {
        "run_id": "phase3-v3-test",
        "harness_check": {"seed_name": "harness"},
        "seeds": {"harness": 7},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    authorization = _authorization(manifest, manifest_path, protocol)
    authorization["scope"]["incremental_cap_usd"] = 61
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="must equal.*60.00"):
        phase3_v3_live.validate_authorization(
            authorization, manifest, manifest_path=manifest_path, protocol=protocol)


def test_authorization_requires_exact_owner_text(tmp_path: Path):
    protocol = _protocol()
    manifest = {
        "run_id": "phase3-v3-test",
        "harness_check": {"seed_name": "harness"},
        "seeds": {"harness": 7},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    authorization = _authorization(manifest, manifest_path, protocol)
    authorization["owner_authorization"]["exact_text"] = "approved generally"
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="exact approval"):
        phase3_v3_live.validate_authorization(
            authorization, manifest, manifest_path=manifest_path, protocol=protocol)


def test_usage_ledger_alias_drift_blocks_resume(tmp_path: Path):
    usage = tmp_path / "usage.jsonl"
    identity = api_client.prepare_usage_ledger(usage, allow_create=True)
    snapshot = api_client.load_chained_usage_ledger(usage, expected_identity=identity)
    client = api_client.RejudgeClient(
        approved_cap_usd=1.0, _sdk_client=object(),
        model_prices={"requested": {"in": 1.0, "out": 1.0}},
        strict_model_pricing=True, usage_log_path=str(usage),
        _ledger_snapshot=snapshot)
    stable = {
        "attempt_id": "a", "model": "requested", "kind": "verdict", "seed": 1,
        "attempt": 0, "reserved_prompt_tokens": 1, "reserved_completion_tokens": 1,
        "estimated_tokens": 2, "cost_usd": 0.000002, "metadata": {},
    }
    client._record_usage_event({
        "status": "reserved", "prompt_tokens": None, "completion_tokens": None, **stable})
    client._record_usage_event({
        "status": "success", "prompt_tokens": 1, "completion_tokens": 1, **stable,
        "response_metadata": {"returned_model_id": "provider-alias"},
    })
    context = {
        "binding": {"usage_ledger_identity": identity},
        "paths": {"usage_ledger": usage},
    }
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="returned-model drift"):
        phase3_v3_live.validate_ledger(context)


def test_two_isolated_deterministic_harness_stores_hash_identically(
    tmp_path: Path, monkeypatch,
):
    protocol = _protocol()
    _main, held_out = phase3_plan.load_reference_question_ids(protocol, ROOT)
    plan = phase3_plan.enumerate_canary_cells(
        protocol, protocol["roster"]["judges_final"], held_out)
    capability = sorted(
        (cell for cell in plan if cell["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND),
        key=lambda cell: cell["cell_key"])
    selected = phase3_v3_live.random.Random(20260829).choice(capability)
    paths = {key: Path(value) for key, value in _output_paths(tmp_path).items()
             if key != "archive_dir"}
    identity = api_client.prepare_usage_ledger(paths["usage_ledger"], allow_create=True)
    role_limits = json.loads(
        (ROOT / phase3_v3_live.ROLE_LIMITS_RELATIVE_PATH).read_text(encoding="utf-8"))
    context = {
        "root": ROOT,
        "protocol": protocol,
        "manifest": {
            "run_id": "phase3-v3-test", "git_commit": "1" * 40,
            "harness_check": {"seed_name": "harness"},
            "seeds": {"harness": 20260829},
            "final_roster": list(protocol["roster"]["judges_final"]),
        },
        "authorization": {"scope": {"incremental_cap_usd": 60}},
        "binding": {
            "usage_ledger_identity": identity,
            "harness": {"selected_capability_cell_key": selected["cell_key"]},
        },
        "paths": paths,
        "role_limits": role_limits,
    }

    def deterministic_client(_context, *, cache_path, phase):
        cache_path.write_text(f"deterministic cache for {phase.split('-')[0]}\n", encoding="utf-8")
        return DeterministicCanaryClient()

    monkeypatch.setattr(phase3_v3_live, "build_client", deterministic_client)
    first = phase3_v3_live.run_harness(context, 1)
    second = phase3_v3_live.run_harness(context, 2)
    assert first["cell_key"] == second["cell_key"] == selected["cell_key"]
    assert first["result_store_sha256"] == second["result_store_sha256"]
    resumed = phase3_v3_live.run_harness(context, 1)
    assert resumed["resumed"] is True
    assert resumed["result_store_sha256"] == first["result_store_sha256"]


def test_capability_anchor_must_finish_before_any_judgment_row(tmp_path: Path):
    formal_results = tmp_path / "formal.jsonl"
    CellResultStore(formal_results).record("judgment", {"cell_key": "judgment"})
    context = {"paths": {"formal_results": formal_results}}
    plan = [
        {"cell_key": "capability", "kind": phase3_plan.CAPABILITY_ANCHOR_KIND},
        {"cell_key": "judgment", "kind": phase3_plan.CANARY_JUDGMENT_KIND},
    ]
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="before the capability"):
        phase3_v3_live._run_capability_anchor_before_judgments(
            context, plan=plan, capabilities=[], client=None, bundle={})


def _recovery_protocol():
    return phase3_plan.load_protocol(ROOT / "rejudge/phase3_protocol_v3_r3.json")


def _recovery_binding():
    return json.loads((
        ROOT / "rejudge/phase3_v3_execution_binding_r2_2026-08-24.json"
    ).read_text(encoding="utf-8"))


def test_recovery_role_limits_cover_qwen38_and_exact_context():
    protocol = _recovery_protocol()
    role_limits = json.loads((
        ROOT / "rejudge/phase3_v3_role_limits_r2_2026-08-24.json"
    ).read_text(encoding="utf-8"))
    phase3_v3_live._validate_role_limits(role_limits, protocol)
    assert role_limits["reasoning_models"]["model_ids"][-1] == (
        "Qwen/Qwen3.8-2.4T-A95B")
    assert role_limits["context_ceilings"]["Qwen/Qwen3.8-2.4T-A95B"][
        "context_length_tokens"] == 1_010_000


def test_manifest_input_resolution_selects_recovery_artifacts():
    paths = [
        "rejudge/phase3_protocol_v3_r3.json",
        "rejudge/phase3_protocol_v3_pin_r3.json",
        "rejudge/phase3_v3_exact_tokenizer_manifest_r4_2026-08-24.json",
        "rejudge/phase3_v3_price_snapshot_r4_2026-08-24.json",
        "rejudge/phase3_v3_role_limits_r2_2026-08-24.json",
        "rejudge/phase3_v3_execution_binding_r2_2026-08-24.json",
    ]
    inputs = {path: str(index) * 64 for index, path in enumerate(paths, 1)}
    resolved = phase3_v3_live._resolve_manifest_input_paths({
        "input_sha256s": inputs,
        "protocol_sha256": inputs[paths[0]],
        "tokenizer_manifest_sha256": inputs[paths[2]],
        "price_snapshot_sha256": inputs[paths[3]],
    })
    assert resolved["protocol"].endswith("phase3_protocol_v3_r3.json")
    assert resolved["execution_binding"].endswith(
        "phase3_v3_execution_binding_r2_2026-08-24.json")


def test_manifest_input_resolution_uses_identity_hash_when_prior_protocol_is_bound():
    successor = "rejudge/phase3_protocol_v3_r3.json"
    prior = "rejudge/phase3_protocol_v3_r2.json"
    inputs = {
        successor: "a" * 64,
        prior: "b" * 64,
        "rejudge/phase3_protocol_v3_pin_r3.json": "c" * 64,
        "rejudge/phase3_v3_exact_tokenizer_manifest_r4_2026-08-24.json": "d" * 64,
        "rejudge/phase3_v3_price_snapshot_r4_2026-08-24.json": "e" * 64,
        "rejudge/phase3_v3_role_limits_r2_2026-08-24.json": "f" * 64,
        "rejudge/phase3_v3_execution_binding_r2_2026-08-24.json": "1" * 64,
    }
    resolved = phase3_v3_live._resolve_manifest_input_paths({
        "input_sha256s": inputs,
        "protocol_sha256": inputs[successor],
        "tokenizer_manifest_sha256": "d" * 64,
        "price_snapshot_sha256": "e" * 64,
    })
    assert resolved["protocol"] == successor


def test_recovery_binding_reports_the_halted_r2_ledger_without_rewriting_it():
    protocol = _recovery_protocol()
    binding = _recovery_binding()
    file_paths = [
        str(value).replace("\\", "/")
        for key, value in binding["paths"].items()
        if key not in phase3_v3_live.NON_MANIFEST_OUTPUT_PATH_KEYS
    ]
    manifest = {
        "planned_output_paths": file_paths,
        "harness_check": {"seed_name": "harness"},
        "seeds": {"harness": 20260829},
        "final_roster": list(protocol["roster"]["judges_final"]),
        "input_sha256s": {
            phase3_v3_live.TRANSCRIPT_REPORT_RELATIVE_PATH:
                binding["transcript_verification_report"]["canonical_sha256"],
        },
    }
    phase3_v3_live._validate_execution_binding(
        binding, manifest, protocol, root=ROOT)
    current = api_client.load_chained_usage_ledger(
        binding["paths"]["usage_ledger"],
        expected_identity=binding["usage_ledger_identity"],
    )
    accounting = phase3_v3_live.aggregate_accounting_summary({
        "root": ROOT,
        "binding": binding,
        "paths": {"usage_ledger": Path(binding["paths"]["usage_ledger"])},
    }, current)
    assert accounting["successor_accounted_spend_usd"] == pytest.approx(0.09274)
    assert accounting["aggregate_accounted_spend_usd"] == pytest.approx(
        phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD_R3)


def test_nonstream_successor_binding_verifies_both_halted_ledgers_and_sealed_r3_ledger():
    protocol = _recovery_protocol()
    binding = json.loads((
        ROOT / "rejudge/phase3_v3_execution_binding_r3_2026-08-25.json"
    ).read_text(encoding="utf-8"))
    file_paths = [
        str(value).replace("\\", "/")
        for key, value in binding["paths"].items()
        if key not in phase3_v3_live.NON_MANIFEST_OUTPUT_PATH_KEYS
    ]
    manifest = {
        "planned_output_paths": file_paths,
        "harness_check": {"seed_name": "harness"},
        "seeds": {"harness": 20260829},
        "final_roster": list(protocol["roster"]["judges_final"]),
        "input_sha256s": {
            phase3_v3_live.TRANSCRIPT_REPORT_RELATIVE_PATH:
                binding["transcript_verification_report"]["canonical_sha256"],
        },
    }
    phase3_v3_live._validate_execution_binding(
        binding, manifest, protocol, root=ROOT)
    current = api_client.load_chained_usage_ledger(
        binding["paths"]["usage_ledger"],
        expected_identity=binding["usage_ledger_identity"],
    )
    accounting = phase3_v3_live.aggregate_accounting_summary({
        "root": ROOT,
        "binding": binding,
        "paths": {"usage_ledger": Path(binding["paths"]["usage_ledger"])},
    }, current)
    assert accounting["prior_accounted_spend_usd"] == pytest.approx(
        phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD_R3)
    # The r3 attempt ran and halted on 2026-08-25 (r9 halt record); its ledger is sealed
    # with these totals and is carried as the third prior attempt of the v4 binding chain.
    assert accounting["successor_accounted_spend_usd"] == pytest.approx(
        4.6673578199999985)
    assert accounting["aggregate_accounted_spend_usd"] == pytest.approx(
        phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD_R4)


def test_qwen38_nonstream_policy_keeps_reasoning_floor_but_removes_stream_pin():
    protocol = _recovery_protocol()
    role_limits = json.loads((
        ROOT / "rejudge/phase3_v3_role_limits_r3_2026-08-25.json"
    ).read_text(encoding="utf-8"))
    phase3_v3_live._validate_role_limits(role_limits, protocol)
    qwen = "Qwen/Qwen3.8-2.4T-A95B"
    assert qwen in role_limits["reasoning_models"]["model_ids"]
    assert qwen not in role_limits["request_settings"]["streaming_pinned_models"]
    for role in role_limits["model_role_limits"][qwen].values():
        assert role["effective_request_max_tokens"] == 4096

    drifted = copy.deepcopy(role_limits)
    drifted["request_settings"]["streaming_pinned_models"][qwen] = {"stream": True}
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="transport policy"):
        phase3_v3_live._validate_role_limits(drifted, protocol)


def test_recovery_authorization_requires_aggregate_carry_text(tmp_path: Path):
    protocol = _recovery_protocol()
    manifest = {
        "run_id": "phase3-v3-recovery-test",
        "harness_check": {"seed_name": "harness"},
        "seeds": {"harness": 7},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    prior = phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD
    authorization = {
        "schema_version": phase3_v3_live.AUTHORIZATION_SCHEMA_V2,
        "execution_authorized": True,
        "main_run_spend_authorized": False,
        "recorded_at_utc": "2026-08-25T00:00:00Z",
        "binds": {
            "run_id": manifest["run_id"],
            "run_manifest_canonical_sha256": phase3_v3_live.canonical_sha256(manifest),
            "run_manifest_tracked_path": manifest_path.name,
            "protocol_tracked_path": "rejudge/phase3_protocol_v3_r3.json",
            "protocol_canonical_sha256": phase3_v3_live.canonical_sha256(protocol),
            "harness_seed_name": "harness",
            "harness_seed": 7,
        },
        "scope": {
            "aggregate_cap_usd": 60,
            "prior_accounted_spend_usd": prior,
            "harness_execution_count": 2,
            "formal_successor_canary_execution_count": 1,
            "successor_canary_fresh_gate_slots": 960,
            "main_run_spend_authorized": False,
            "gpu_ordinal_or_not_used": "not_used",
        },
        "owner_authorization": {
            "approver": "Jack Maiorino",
            "exact_text": (
                f"Approved: {manifest['run_id']} harness and successor canary, $60 USD "
                f"aggregate cap including ${prior:.8f} prior accounted spend, no main spend"),
        },
    }
    phase3_v3_live.validate_authorization(
        authorization,
        manifest,
        manifest_path=manifest_path,
        protocol=protocol,
        protocol_relative_path="rejudge/phase3_protocol_v3_r3.json",
        prior_accounted_spend_usd=prior,
    )
    authorization["scope"]["prior_accounted_spend_usd"] = 0
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="prior accounted spend"):
        phase3_v3_live.validate_authorization(
            authorization,
            manifest,
            manifest_path=manifest_path,
            protocol=protocol,
            protocol_relative_path="rejudge/phase3_protocol_v3_r3.json",
            prior_accounted_spend_usd=prior,
        )


# --- amendment 3 (2026-08-25): bounded uncertain-spend tolerance ------------------------------


def _uncertain_binding():
    return json.loads((
        ROOT / "rejudge/phase3_v3_execution_binding_r4_2026-08-25.json"
    ).read_text(encoding="utf-8"))


def _chained_ledger(tmp_path: Path, events: list[dict]) -> tuple[Path, dict]:
    path = tmp_path / "usage.jsonl"
    identity = api_client.prepare_usage_ledger(path, allow_create=True)
    snapshot = api_client.load_chained_usage_ledger(path)
    sequence = snapshot.last_sequence
    prev = snapshot.last_event_hash
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for event in events:
            sequence += 1
            event = {**event, "ledger_id": identity["ledger_id"],
                     "sequence": sequence, "prev_event_hash": prev}
            event["event_hash"] = api_client._usage_event_hash(event)
            prev = event["event_hash"]
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    api_client._atomic_write_json(
        api_client.usage_ledger_state_path(path),
        api_client._usage_state_payload(identity, sequence, prev))
    return path, dict(identity)


def _reserved_event(attempt_id: str, cost: float, **overrides) -> dict:
    return {"ts": "2026-08-25T20:00:00+00:00", "status": "reserved",
            "attempt_id": attempt_id, "model": "m", "kind": "verdict", "seed": 1,
            "attempt": 0, "prompt_tokens": None, "completion_tokens": None,
            "reserved_prompt_tokens": 10, "reserved_completion_tokens": 10,
            "estimated_tokens": 20, "cost_usd": cost, "metadata": {}, **overrides}


def _unknown_event(attempt_id: str, cost: float, **overrides) -> dict:
    return {**_reserved_event(attempt_id, cost), "status": "unknown_charge",
            "error": "Request timed out.", **overrides}


def _success_event(attempt_id: str, reserved: float, actual: float, **overrides) -> dict:
    return {**_reserved_event(attempt_id, reserved), "status": "success",
            "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": actual,
            "response_metadata": {"returned_model_id": "m"}, **overrides}


def _ledger_context(tmp_path: Path, events: list[dict], binding: dict) -> dict:
    path, identity = _chained_ledger(tmp_path, events)
    binding = copy.deepcopy(binding)
    binding["usage_ledger_identity"] = identity
    return {"root": ROOT, "binding": binding, "paths": {"usage_ledger": path}}


def test_uncertain_role_limits_pin_tolerance_policy():
    protocol = _recovery_protocol()
    role_limits = json.loads((
        ROOT / "rejudge/phase3_v3_role_limits_r4_2026-08-25.json"
    ).read_text(encoding="utf-8"))
    phase3_v3_live._validate_role_limits(role_limits, protocol)
    drifted = copy.deepcopy(role_limits)
    drifted["uncertain_spend_tolerance"]["run_uncertain_ceiling_usd"] = 5.0
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="tolerance policy drifted"):
        phase3_v3_live._validate_role_limits(drifted, protocol)


def test_v2_role_limits_refuse_a_smuggled_tolerance_block():
    protocol = _recovery_protocol()
    role_limits = json.loads((
        ROOT / "rejudge/phase3_v3_role_limits_r3_2026-08-25.json"
    ).read_text(encoding="utf-8"))
    smuggled = copy.deepcopy(role_limits)
    smuggled["uncertain_spend_tolerance"] = {"run_uncertain_ceiling_usd": 1.0}
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="v3 role-limits schema"):
        phase3_v3_live._validate_role_limits(smuggled, protocol)


def test_v4_binding_verifies_three_sealed_ledgers_and_frozen_carry():
    binding = _uncertain_binding()
    totals = phase3_v3_live._validate_prior_attempt_accounting(binding, ROOT)
    assert totals["accounted_spend_usd"] == pytest.approx(
        phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD_R4)
    assert totals["uncertain_spend_usd"] == pytest.approx(0.23350102)


def test_validate_ledger_v4_tolerates_bounded_unknown_charges(tmp_path: Path):
    context = _ledger_context(tmp_path, [
        _reserved_event("a1", 0.4), _unknown_event("a1", 0.4),
        _reserved_event("a2", 0.3), _success_event("a2", 0.3, 0.2),
    ], _uncertain_binding())
    snapshot = phase3_v3_live.validate_ledger(context)
    assert float(snapshot.summary["uncertain_spend_usd"]) == pytest.approx(0.4)


def test_validate_ledger_v4_refuses_uncertain_above_ceiling(tmp_path: Path):
    context = _ledger_context(tmp_path, [
        _reserved_event("a1", 0.6), _unknown_event("a1", 0.6),
        _reserved_event("a2", 0.5), _unknown_event("a2", 0.5),
    ], _uncertain_binding())
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="exceeds the frozen"):
        phase3_v3_live.validate_ledger(context)


def test_validate_ledger_v4_refuses_open_reservation(tmp_path: Path):
    context = _ledger_context(tmp_path, [
        _reserved_event("a1", 0.3), _success_event("a1", 0.3, 0.2),
        _reserved_event("a2", 0.3),
    ], _uncertain_binding())
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="open reservation"):
        phase3_v3_live.validate_ledger(context)


def test_validate_ledger_v3_still_refuses_any_unknown_charge(tmp_path: Path):
    binding = json.loads((
        ROOT / "rejudge/phase3_v3_execution_binding_r3_2026-08-25.json"
    ).read_text(encoding="utf-8"))
    context = _ledger_context(tmp_path, [
        _reserved_event("a1", 0.01), _unknown_event("a1", 0.01),
    ], binding)
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="unresolved uncertain"):
        phase3_v3_live.validate_ledger(context)


def test_uncertain_event_report_names_completing_attempts():
    cell = "plan:kind:cell-1"
    events = [
        _reserved_event("a1", 0.4, metadata={"cell_key": cell, "condition": "b2"}),
        _unknown_event("a1", 0.4, metadata={"cell_key": cell, "condition": "b2"}),
        _reserved_event("a2", 0.4, metadata={"cell_key": cell, "condition": "b2"}),
        _success_event("a2", 0.4, 0.2, metadata={"cell_key": cell, "condition": "b2"}),
    ]
    entries, by_condition = phase3_v3_live._uncertain_event_report(events)
    assert len(entries) == 1
    assert entries[0]["completing_success_attempt_id"] == "a2"
    assert by_condition == {"b2": 1}


# --- amendment 4 (2026-08-25): Qwen3.5-9B weak-slot substitution ------------------------------


def _recovery2_protocol():
    return phase3_plan.load_protocol(ROOT / "rejudge/phase3_protocol_v3_r4.json")


def _recovery2_binding():
    return json.loads((
        ROOT / "rejudge/phase3_v3_execution_binding_r5_2026-08-25.json"
    ).read_text(encoding="utf-8"))


def test_recovery2_role_limits_classify_qwen35_9b_as_reasoning():
    protocol = _recovery2_protocol()
    role_limits = json.loads((
        ROOT / "rejudge/phase3_v3_role_limits_r5_2026-08-25.json"
    ).read_text(encoding="utf-8"))
    phase3_v3_live._validate_role_limits(role_limits, protocol)
    assert "Qwen/Qwen3.5-9B" in role_limits["reasoning_models"]["model_ids"]
    assert role_limits["context_ceilings"]["Qwen/Qwen3.5-9B"][
        "context_length_tokens"] == 262_144
    assert role_limits["model_role_limits"]["Qwen/Qwen3.5-9B"]["judge_verdict"][
        "effective_request_max_tokens"] == 4096


def test_v5_binding_verifies_four_sealed_ledgers_and_frozen_carry():
    binding = _recovery2_binding()
    totals = phase3_v3_live._validate_prior_attempt_accounting(binding, ROOT)
    assert totals["accounted_spend_usd"] == pytest.approx(
        phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD_R5)


def test_validate_ledger_v5_keeps_the_bounded_uncertain_tolerance(tmp_path: Path):
    context = _ledger_context(tmp_path, [
        _reserved_event("a1", 0.4), _unknown_event("a1", 0.4),
        _reserved_event("a2", 0.3), _success_event("a2", 0.3, 0.2),
    ], _recovery2_binding())
    snapshot = phase3_v3_live.validate_ledger(context)
    assert float(snapshot.summary["uncertain_spend_usd"]) == pytest.approx(0.4)
    context = _ledger_context(tmp_path / "over", [
        _reserved_event("a1", 0.6), _unknown_event("a1", 0.6),
        _reserved_event("a2", 0.5), _unknown_event("a2", 0.5),
    ], _recovery2_binding())
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="exceeds the frozen"):
        phase3_v3_live.validate_ledger(context)


# --- amendment 5 (2026-08-26): terminal-halt disposition machinery ----------------------------


def _r4_context():
    protocol = _recovery2_protocol()
    return {
        "root": ROOT,
        "protocol": protocol,
        "manifest": {"run_id": "phase3-v3-test-terminal",
                     "final_roster": list(protocol["roster"]["judges_final"])},
    }


def _judgment_cell(context):
    plan = phase3_v3_live._canary_plan(context)
    return next(cell for cell in plan
                if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND)


def _terminal_record(cell_key: str, run_id: str = "phase3-v3-test-terminal") -> dict:
    return {
        "schema_version": phase3_v3_live.TERMINAL_HALTS_SCHEMA,
        "run_id": run_id,
        "cell_key": cell_key,
        "reason": "checker_malformed",
        "evidence": {
            "ledger_attempt_id": "a" * 32,
            "ledger_event_sha256": "b" * 64,
            "finish_reason": "length",
            "completion_tokens": 4096,
            "parse_failure": "no verdict token in a length-truncated thinking response",
        },
        "reviewer": "orchestrator session test",
        "recorded_at_utc": "2026-08-26T08:00:00Z",
        "frozen_policy_citation": (
            "phase-2 missing-data policy: terminal exclusion, counts INVALID, "
            "reported at close-out"),
    }


def _write_record(directory: Path, name: str, record: dict) -> None:
    (directory / name).write_text(json.dumps(record), encoding="utf-8")


def _empty_store(tmp_path: Path) -> CellResultStore:
    return CellResultStore(tmp_path / "results.jsonl")


def test_terminal_halt_loader_accepts_an_evidence_bound_record(tmp_path: Path):
    context = _r4_context()
    cell = _judgment_cell(context)
    _write_record(tmp_path, "phase3_v3_terminal_halts_001.json",
                  _terminal_record(str(cell["cell_key"])))
    records = phase3_v3_live.load_terminal_halt_records(
        context, _empty_store(tmp_path), records_directory=tmp_path)
    assert len(records) == 1
    partition = phase3_v3_live.terminal_partition(context, records)
    assert str(cell["cell_key"]) in partition["terminal_cells"]
    # The mirror overlay covers the whole unit: the terminal cell plus its partner side.
    assert len(partition["affected_unit_cells"]) >= 2
    assert partition["terminal_cells"] <= partition["affected_unit_cells"]


def test_terminal_halt_loader_ignores_records_for_other_runs(tmp_path: Path):
    context = _r4_context()
    cell = _judgment_cell(context)
    _write_record(tmp_path, "phase3_v3_terminal_halts_001.json",
                  _terminal_record(str(cell["cell_key"]), run_id="phase3-v3-other"))
    records = phase3_v3_live.load_terminal_halt_records(
        context, _empty_store(tmp_path), records_directory=tmp_path)
    assert records == []


def test_terminal_halt_loader_rejects_unplanned_cells(tmp_path: Path):
    context = _r4_context()
    _write_record(tmp_path, "phase3_v3_terminal_halts_001.json",
                  _terminal_record("not-a-planned-cell"))
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="unplanned"):
        phase3_v3_live.load_terminal_halt_records(
            context, _empty_store(tmp_path), records_directory=tmp_path)


def test_terminal_halt_loader_rejects_missing_evidence(tmp_path: Path):
    context = _r4_context()
    cell = _judgment_cell(context)
    record = _terminal_record(str(cell["cell_key"]))
    record["evidence"].pop("finish_reason")
    _write_record(tmp_path, "phase3_v3_terminal_halts_001.json", record)
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="evidence field"):
        phase3_v3_live.load_terminal_halt_records(
            context, _empty_store(tmp_path), records_directory=tmp_path)


def test_terminal_halt_loader_rejects_a_cell_with_a_result_row(tmp_path: Path):
    context = _r4_context()
    cell = _judgment_cell(context)
    store = _empty_store(tmp_path)
    store.record(str(cell["cell_key"]), {"placeholder": True})
    _write_record(tmp_path, "phase3_v3_terminal_halts_001.json",
                  _terminal_record(str(cell["cell_key"])))
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="stale"):
        phase3_v3_live.load_terminal_halt_records(
            context, store, records_directory=tmp_path)


def test_terminal_halt_loader_rejects_duplicates_and_enforces_the_bound(tmp_path: Path):
    context = _r4_context()
    cell = _judgment_cell(context)
    _write_record(tmp_path, "phase3_v3_terminal_halts_001.json",
                  _terminal_record(str(cell["cell_key"])))
    _write_record(tmp_path, "phase3_v3_terminal_halts_002.json",
                  _terminal_record(str(cell["cell_key"])))
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="duplicate"):
        phase3_v3_live.load_terminal_halt_records(
            context, _empty_store(tmp_path), records_directory=tmp_path)
    plan = phase3_v3_live._canary_plan(context)
    judgment_keys = [str(entry["cell_key"]) for entry in plan
                     if entry["kind"] == phase3_plan.CANARY_JUDGMENT_KIND]
    bound_dir = tmp_path / "bound"
    bound_dir.mkdir()
    for index, key in enumerate(
            judgment_keys[:phase3_v3_live.MAX_TERMINAL_JUDGMENT_CELLS + 1]):
        _write_record(bound_dir, f"phase3_v3_terminal_halts_{index:03d}.json",
                      _terminal_record(key))
    with pytest.raises(phase3_v3_live.Phase3V3LiveError, match="frozen bound"):
        phase3_v3_live.load_terminal_halt_records(
            context, _empty_store(tmp_path), records_directory=bound_dir)


def test_invalid_gate_counts_terminal_cells_as_invalid(tmp_path: Path):
    context = _r4_context()
    plan = phase3_v3_live._canary_plan(context)
    roster = context["manifest"]["final_roster"]
    b0 = [cell for cell in plan if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND
          and cell["condition"] == "b0"]
    store = _empty_store(tmp_path)
    terminal_cell = b0[0]
    for cell in b0:
        if cell is terminal_cell:
            continue
        store.record(str(cell["cell_key"]),
                     {"verdict_strict": {"verdict": "A"}})
    report = phase3_v3_live._invalid_gate(
        store, b0, roster, terminal_cells=frozenset({str(terminal_cell["cell_key"])}))
    judge = str(terminal_cell["judge_model"])
    assert report[judge]["terminal_invalid"] == 1
    assert report[judge]["invalid"] >= 1

