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


def test_nonstream_successor_binding_verifies_both_halted_ledgers_and_zero_fresh_ledger():
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
    assert accounting["successor_accounted_spend_usd"] == 0
    assert accounting["aggregate_accounted_spend_usd"] == pytest.approx(
        phase3_v3_live.PRIOR_ACCOUNTED_SPEND_USD_R3)


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
