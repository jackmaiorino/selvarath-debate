"""Production-boundary tests for the Phase 3 main driver."""
from __future__ import annotations

import inspect
import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from rejudge import phase3_main_live, phase3_main_runner
from scripts import phase3_preseed_transcripts


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inventory():
    return phase3_main_runner.build_canonical_main_inventory(ROOT)


def _prepared(tmp_path: Path, inventory) -> phase3_main_live.PreparedMainRun:
    artifact = (tmp_path / "formal").resolve()
    registry = (tmp_path / "registry").resolve()
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)
    input_paths = {}
    for name in (
        "protocol", "prompt_bundle", "reviewer_prompt",
        "reviewer_failure_policy", "role_limits",
        "main_transcript_bundle", "transcript_verification",
        "capacity_plan", "capacity_result", "capacity_dispatch_history",
        "context_blocklist", "analysis_pins", "billing_reconciliation",
        "price_snapshot", "raw_provider_catalog", "raw_serverless_endpoints",
    ):
        path = inputs / f"{name}.json"
        path.write_text("{}\n", encoding="utf-8")
        input_paths[name] = path.resolve()
    manifest_sha = "a" * 64
    manifest = {
        "run_id": "phase3-main-live-test",
        "manifest_identity_sha256": "b" * 64,
        "source_commit": "c" * 40,
        "spend": {
            "prior_reconciled_usd": "10.25",
            "forecast_main_usd": "20.00",
            "stage_cap_usd": "40.00",
        },
        "runtime": {
            "reviewer_cli_binary": "codex.cmd",
            "reviewer_model": "gpt-5.6-sol",
            "reviewer_reasoning_effort": "high",
            "reviewer_concurrency": 12,
        },
        "input_bindings": {
            name: {
                "path": str(path),
                "sha256": phase3_main_live.hashlib.sha256(path.read_bytes()).hexdigest(),
                "hash_kind": "raw_sha256",
            }
            for name, path in input_paths.items()
        },
    }
    role_limits = {
        "request_settings": {
            "transport": {
                "ledger_max_retries": 2,
                "http_timeout": {"connect": 1, "read": 2, "write": 3, "pool": 4},
                "sdk_internal_max_retries": 0,
                "per_call_wall_clock_ceiling_seconds": 60,
            },
            "streaming_pinned_models": {},
            "per_model_extra_fields": {},
        },
        "model_role_limits": {},
        "reasoning_models": {"model_ids": []},
        "context_ceilings": {
            model: {"context_length_tokens": 100_000}
            for model in phase3_main_runner.CONFIRMED_MAIN_JUDGES
        },
    }
    price = {
        "models": {
            model: {"input_usd_per_million": 1.0, "output_usd_per_million": 2.0}
            for model in phase3_main_runner.CONFIRMED_MAIN_JUDGES
        }
    }
    input_paths["price_snapshot"].write_text(
        json.dumps(price) + "\n", encoding="utf-8")
    manifest["input_bindings"]["price_snapshot"]["sha256"] = (
        phase3_main_live.hashlib.sha256(
            input_paths["price_snapshot"].read_bytes()).hexdigest()
    )
    authorization_path = (inputs / "authorization.json").resolve()
    authorization_path.write_text("{}\n", encoding="utf-8")
    authorization_signature_path = authorization_path.with_name(
        f"{authorization_path.name}.sig")
    authorization_signature_path.write_text("test signature\n", encoding="utf-8")
    return phase3_main_live.PreparedMainRun(
        project_root=ROOT,
        manifest_path=(inputs / "manifest.json").resolve(),
        authorization_path=authorization_path,
        authorization_raw_sha256=phase3_main_live.hashlib.sha256(
            authorization_path.read_bytes()).hexdigest(),
        authorization_signature_raw_sha256=phase3_main_live.hashlib.sha256(
            authorization_signature_path.read_bytes()).hexdigest(),
        manifest=manifest,
        authorization={
            "authorization_id": "owner-test",
            "stage_cap_usd": "40.00",
            "approved_at_utc": "2026-08-29T20:15:00Z",
            "valid_until_utc": "2026-10-13T20:15:00Z",
        },
        manifest_validation={
            "manifest_canonical_sha256": manifest_sha,
            "artifact_root": artifact,
            "identity_registry_root": registry,
            "input_paths": input_paths,
            "output_paths": {},
        },
        authorization_validation={},
        input_paths=input_paths,
        protocol={
            "cell_key_namespace": "phase3-main-live-test",
            "roster": {"query_checker": phase3_main_runner.CONFIRMED_MAIN_JUDGES[0]},
        },
        prompt_bundle={},
        reviewer_prompt={"prompt": "review exactly this payload"},
        role_limits=role_limits,
        price_snapshot=price,
        billing_reconciliation={},
        cost_forecast={},
        capacity_validation={},
        harness_validation={},
        inventory=inventory,
        context_excluded_cell_keys=(),
    )


def _with_current_boundary_files(
    prepared: phase3_main_live.PreparedMainRun,
) -> phase3_main_live.PreparedMainRun:
    manifest_sha256 = phase3_main_live.phase3_main_manifest.manifest_canonical_sha256(
        prepared.manifest)
    prepared.manifest_path.write_text(
        json.dumps(prepared.manifest, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    prepared.authorization_path.write_text(
        json.dumps(prepared.authorization, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    signature_path = prepared.authorization_path.with_name(
        f"{prepared.authorization_path.name}.sig")
    validation = {
        **prepared.manifest_validation,
        "manifest_canonical_sha256": manifest_sha256,
    }
    return replace(
        prepared,
        manifest_validation=validation,
        authorization_raw_sha256=hashlib.sha256(
            prepared.authorization_path.read_bytes()).hexdigest(),
        authorization_signature_raw_sha256=hashlib.sha256(
            signature_path.read_bytes()).hexdigest(),
    )


def _write_valid_analysis_snapshot(
    prepared: phase3_main_live.PreparedMainRun,
    monkeypatch,
    *,
    finalization_sha256: str,
) -> dict:
    prepared.identity.artifact_root.mkdir(parents=True, exist_ok=True)
    prepared.identity.paths.results.write_text(
        '{"fixture":"result"}\n', encoding="utf-8", newline="\n")
    pins = {
        "bootstrap": {
            "B": 2000,
            "seed": 20260829,
            "precomputed_full_draw_matrix_sha256": "d" * 64,
        },
    }
    pins_path = prepared.input_paths["analysis_pins"]
    pins_path.write_text(
        json.dumps(pins, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    manifest = cast(dict[str, Any], prepared.manifest)
    protocol = cast(dict[str, Any], prepared.protocol)
    manifest["input_bindings"]["analysis_pins"]["sha256"] = hashlib.sha256(
        pins_path.read_bytes()).hexdigest()
    manifest["toolchain"] = {"python_version": phase3_main_live.platform.python_version()}
    protocol["planning_cell_identity"] = {
        "question_bank_bundle_sha256": "e" * 64,
    }
    protocol["source_bindings"] = {
        "canonical_json_sha256": {
            "rejudge/phase2_protocol.json": "f" * 64,
        },
    }
    protocol["roster"]["judges_final"] = ["fixture-judge"]
    question_bank = {
        "fixture-question": {
            "question": "Which fixture is bound?",
            "world": "fixture-world",
        },
    }
    monkeypatch.setattr(
        phase3_main_live.phase3_main_analysis,
        "validate_analysis_pins",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_analysis,
        "_snapshot_protocol_bound_question_bank",
        lambda *_args, **_kwargs: SimpleNamespace(
            main_ids=("fixture-question",),
            bank=question_bank,
            stable_inputs=(),
        ),
    )
    result: dict[str, Any] = {
        field: {}
        for field in phase3_main_live.ANALYSIS_RESULT_FIELDS
    }
    primary_ids = frozenset(phase3_main_live.phase3_main_analysis.PRIMARY_IDS)
    estimand_ids = primary_ids | {"S1"}
    result["domains"] = {estimand: ["fixture-question"] for estimand in estimand_ids}
    result["primary"] = {estimand: {} for estimand in primary_ids}
    result["valid_only_sensitivity"] = {estimand: {} for estimand in estimand_ids}
    result["strict_support"] = {estimand: {} for estimand in estimand_ids}
    result["per_judge_descriptive"] = {
        "fixture-judge": {estimand: {} for estimand in estimand_ids},
    }
    result["claims"] = {"pooled": "fixture", "per_judge": "fixture", "prohibited": "fixture"}
    result["all_invalid_scenarios"] = {
        "role": "fixture",
        "common_draw_matrix_sha256": "d" * 64,
        "all_invalid_correct": {estimand: {} for estimand in estimand_ids},
        "all_invalid_wrong": {estimand: {} for estimand in estimand_ids},
    }
    count_keys = {
        f"fixture-judge|{condition}"
        for condition in phase3_main_live.phase3_main_analysis.ALL_CONDITIONS
    }
    result["invalid_counts_by_judge_condition"] = {
        key: 0 for key in count_keys
    }
    result["terminal_invalid_counts_by_judge_condition"] = {
        key: 0 for key in count_keys
    }
    result["invalid_counts_by_budget_judge_replicate_block"] = []
    result["schema_version"] = phase3_main_live.ANALYSIS_RESULT_SCHEMA
    result["bootstrap"] = {
        "B": 2000,
        "seed": 20260829,
        "prng": "CPython random.Random MT19937",
        "draw_matrix_sha256": "d" * 64,
        "strata": {"fixture-world": ["fixture-question"]},
    }
    result["integrity"] = {
        "repository_head_at_analysis": prepared.manifest["source_commit"],
        "engine_git_commit": prepared.manifest["source_commit"],
        "engine_git_state": "clean_tracked_at_head",
        "engine_git_status_porcelain": None,
        "engine_raw_sha256": phase3_main_live._raw_sha256(
            Path(phase3_main_live.phase3_main_analysis.__file__).resolve()),
        "python_version": phase3_main_live.platform.python_version(),
        "protocol_canonical_sha256": phase3_main_live.canonical_sha256(
            prepared.protocol),
        "protocol_question_bank_bundle_sha256": "e" * 64,
        "protocol_phase2_question_source_canonical_sha256": "f" * 64,
        "main_question_ids_canonical_sha256": phase3_main_live.canonical_sha256(
            ["fixture-question"]),
        "main_question_rows_canonical_sha256": phase3_main_live.canonical_sha256(
            question_bank),
        "pins_raw_sha256": hashlib.sha256(pins_path.read_bytes()).hexdigest(),
        "results_raw_sha256": hashlib.sha256(
            prepared.identity.paths.results.read_bytes()).hexdigest(),
        "finalization_raw_sha256": finalization_sha256,
        "terminal_cell_keys_raw_sha256": None,
        "context_ineligible_cell_keys_raw_sha256": None,
        "record_count": len(prepared.inventory.judgment_cells),
    }
    prepared.identity.paths.analysis_results.write_text(
        json.dumps(result, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return result


def test_public_live_entry_has_no_client_factory_path_cap_or_resume_injection():
    assert set(inspect.signature(phase3_main_live.run_main).parameters) == {
        "manifest_path", "authorization_path"
    }
    assert set(inspect.signature(phase3_main_live.load_prepared_main).parameters) == {
        "manifest_path", "authorization_path", "verify_git"
    }
    source = inspect.getsource(phase3_main_live)
    assert "CallCache" not in source
    assert "CachingClient" not in source


def test_public_paid_path_is_blocked_before_any_formal_state_mutation(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    monkeypatch.setattr(
        phase3_main_live, "load_prepared_main", lambda *args, **kwargs: prepared)
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="intentionally blocked",
    ):
        phase3_main_live.run_main("manifest", "authorization")
    assert not prepared.identity.artifact_root.exists()
    registry_root = prepared.identity.identity_registry_root
    assert registry_root is not None
    assert not registry_root.exists()


def test_production_blockers_exclude_closed_provenance_work():
    blockers = phase3_main_live.PRODUCTION_EXECUTION_BLOCKERS
    assert blockers
    assert not any(
        "non-verdict provider request fingerprints" in item for item in blockers)
    assert not any(
        "reviewer packet and worklist provenance" in item for item in blockers)
    assert any("owner signing key" in item for item in blockers)
    assert any("provider-authenticated billing" in item for item in blockers)
    assert any("reviewer capacity evidence" in item for item in blockers)
    assert not any("wave-index closeout" in item for item in blockers)


def test_launch_freshness_failure_precedes_identity_consumption(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)

    class ReadOnlyLease:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        phase3_main_live, "load_prepared_main", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        phase3_main_live, "_require_production_execution_unblocked", lambda: None)
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization",
        lambda _prepared: prepared.authorization,
    )
    monkeypatch.setattr(phase3_main_live.phase3_v3_live, "RunLease", ReadOnlyLease)
    monkeypatch.setattr(
        phase3_main_live,
        "_validate_launch_freshness",
        lambda _prepared: (_ for _ in ()).throw(
            phase3_main_live.Phase3MainLiveError("launch evidence expired")),
    )

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="expired"):
        phase3_main_live.run_main("manifest", "authorization")
    assert not prepared.identity.artifact_root.exists()
    assert not phase3_main_live._authorization_consumed_path(prepared).exists()
    assert not phase3_main_live._identity_start_path(prepared.identity).exists()


def test_authorization_is_rechecked_after_launch_freshness_before_consumption(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    events = []

    class ReadOnlyLease:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        phase3_main_live, "load_prepared_main", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        phase3_main_live, "_require_production_execution_unblocked", lambda: None)
    monkeypatch.setattr(phase3_main_live.phase3_v3_live, "RunLease", ReadOnlyLease)
    monkeypatch.setattr(
        phase3_main_live,
        "_validate_launch_freshness",
        lambda _prepared: events.append("freshness"),
    )

    def expired_authorization(_prepared):
        events.append("authorization")
        raise phase3_main_live.Phase3MainLiveError("authorization expired")

    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization",
        expired_authorization,
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_consume_identity",
        lambda _prepared: pytest.fail("expired authorization consumed identity"),
    )

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="expired"):
        phase3_main_live.run_main("manifest", "authorization")
    assert events == ["freshness", "authorization"]
    assert not phase3_main_live._authorization_consumed_path(prepared).exists()


def test_strict_json_loader_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"run_id":"first","run_id":"second"}\n', encoding="utf-8")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="repeats key"):
        phase3_main_live._load_strict_object(path, "manifest")


def test_strict_json_loader_rejects_numeric_overflow(tmp_path):
    path = tmp_path / "overflow.json"
    path.write_text('{"estimate":1e999}\n', encoding="utf-8")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="strict JSON"):
        phase3_main_live._load_strict_object(path, "analysis")


def test_bound_input_is_hashed_and_parsed_from_the_same_bytes(tmp_path):
    path = tmp_path / "bound.json"
    path.write_text('{"value":"original"}\n', encoding="utf-8")
    manifest = {
        "input_bindings": {
            "bound": {
                "sha256": phase3_main_live.hashlib.sha256(path.read_bytes()).hexdigest(),
            },
        },
    }
    assert phase3_main_live._load_bound_input_object(
        manifest, {"bound": path}, "bound", "test input") == {"value": "original"}
    path.write_text('{"value":"replacement"}\n', encoding="utf-8")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="raw SHA-256 drifted"):
        phase3_main_live._load_bound_input_object(
            manifest, {"bound": path}, "bound", "test input")


def test_live_authorization_is_blocked_until_owner_signing_key_is_pinned(tmp_path):
    authorization = tmp_path / "authorization.json"
    authorization.write_text("{}\n", encoding="utf-8")
    assert phase3_main_live.OWNER_SIGNING_PUBLIC_KEY is None
    assert phase3_main_live.OWNER_SIGNING_KEY_FINGERPRINT is None
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="signing key is not pinned"):
        phase3_main_live._load_authenticated_owner_authorization(authorization)


def test_owner_authorization_requires_valid_signature_over_exact_bytes(
    tmp_path, monkeypatch,
):
    if not phase3_main_live.SSH_KEYGEN_PATH.is_file():
        pytest.skip("the production OpenSSH verifier is unavailable")
    private_key = tmp_path / "ephemeral-test-owner"
    generated = subprocess.run(
        [
            str(phase3_main_live.SSH_KEYGEN_PATH),
            "-q", "-t", "ed25519", "-N", "", "-f", str(private_key),
        ],
        capture_output=True,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr.decode(errors="replace")
    authorization = tmp_path / "authorization.json"
    authorization.write_text(
        '{"authorization_id":"signed-test"}\n', encoding="utf-8", newline="\n")
    signed = subprocess.run(
        [
            str(phase3_main_live.SSH_KEYGEN_PATH),
            "-Y", "sign", "-f", str(private_key),
            "-n", phase3_main_live.OWNER_SIGNATURE_NAMESPACE,
            str(authorization),
        ],
        capture_output=True,
        timeout=30,
    )
    assert signed.returncode == 0, signed.stderr.decode(errors="replace")
    public_key = private_key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    fingerprint = subprocess.run(
        [str(phase3_main_live.SSH_KEYGEN_PATH), "-lf", str(private_key.with_suffix(".pub"))],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.split()[1]
    monkeypatch.setattr(phase3_main_live, "OWNER_SIGNING_PUBLIC_KEY", public_key)
    monkeypatch.setattr(
        phase3_main_live, "OWNER_SIGNING_KEY_FINGERPRINT", fingerprint)

    assert phase3_main_live._load_authenticated_owner_authorization(authorization) == {
        "authorization_id": "signed-test",
    }
    authorization.write_text(
        '{"authorization_id":"tampered"}\n', encoding="utf-8", newline="\n")
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError, match="signature is invalid",
    ):
        phase3_main_live._load_authenticated_owner_authorization(authorization)


def test_runtime_revalidation_rejects_same_semantics_with_different_signed_bytes(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    monkeypatch.setattr(
        phase3_main_live,
        "_load_authenticated_owner_authorization",
        lambda path: dict(prepared.authorization),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        lambda *args, **kwargs: {},
    )
    assert phase3_main_live._revalidate_authenticated_authorization(prepared) == (
        prepared.authorization)
    prepared.authorization_path.write_text("{ }\n", encoding="utf-8")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="bytes changed"):
        phase3_main_live._revalidate_authenticated_authorization(prepared)


@pytest.mark.parametrize("mutated", ["manifest", "authorization", "signature"])
def test_final_boundary_reopens_manifest_authorization_and_signature(
    tmp_path, inventory, monkeypatch, mutated,
):
    prepared = _with_current_boundary_files(_prepared(tmp_path, inventory))
    monkeypatch.setattr(
        phase3_main_live,
        "_load_authenticated_owner_authorization",
        lambda _path: dict(prepared.authorization),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *_args, **_kwargs: {
            "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        },
    )
    monkeypatch.setattr(
        phase3_main_live, "_verify_clean_git_identity", lambda *_args: None)
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        lambda *_args, **_kwargs: {},
    )
    if mutated == "manifest":
        changed = dict(prepared.manifest)
        changed["run_id"] = "mutated-after-analysis"
        prepared.manifest_path.write_text(
            json.dumps(changed) + "\n", encoding="utf-8", newline="\n")
    elif mutated == "authorization":
        prepared.authorization_path.write_bytes(
            prepared.authorization_path.read_bytes() + b" ")
    else:
        signature_path = prepared.authorization_path.with_name(
            f"{prepared.authorization_path.name}.sig")
        signature_path.write_bytes(signature_path.read_bytes() + b"x")

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="changed before completion"):
        phase3_main_live._revalidate_final_boundary_inputs(
            prepared,
            {"recorded_at_utc": "2026-08-29T20:30:00Z"},
        )


def test_final_boundary_uses_approved_time_for_post_expiry_closeout(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    authorization = {
        **prepared.authorization,
        "valid_until_utc": "2026-08-29T21:00:00Z",
    }
    prepared = _with_current_boundary_files(
        replace(prepared, authorization=authorization))
    observed_as_of = []
    monkeypatch.setattr(
        phase3_main_live,
        "_load_authenticated_owner_authorization",
        lambda _path: dict(prepared.authorization),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *_args, **_kwargs: {
            "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        },
    )
    monkeypatch.setattr(
        phase3_main_live, "_verify_clean_git_identity", lambda *_args: None)

    def validate_authorization(_authorization, _manifest, *, as_of):
        observed_as_of.append(as_of)
        assert as_of.isoformat() == "2026-08-29T20:15:00+00:00"
        return {}

    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        validate_authorization,
    )

    assert phase3_main_live._revalidate_final_boundary_inputs(
        prepared,
        {"recorded_at_utc": "2026-08-29T21:00:00.000001Z"},
    ) == prepared.authorization
    assert len(observed_as_of) == 1


def test_analysis_result_snapshot_binds_current_formal_inputs(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    finalization_sha256 = "9" * 64
    _write_valid_analysis_snapshot(
        prepared,
        monkeypatch,
        finalization_sha256=finalization_sha256,
    )
    expected_results_sha256 = hashlib.sha256(
        prepared.identity.paths.results.read_bytes()).hexdigest()

    raw, digest = phase3_main_live._validate_analysis_result_snapshot(
        prepared,
        expected_finalization_raw_sha256=finalization_sha256,
        expected_results_raw_sha256=expected_results_sha256,
        expected_analysis_raw=(
            prepared.identity.paths.analysis_results.read_bytes()),
    )

    assert digest == hashlib.sha256(raw).hexdigest()
    assert raw == prepared.identity.paths.analysis_results.read_bytes()


@pytest.mark.parametrize("tamper", ["malformed", "integrity", "strata", "science"])
def test_analysis_result_snapshot_rejects_unvalidated_or_drifted_output(
    tmp_path, inventory, monkeypatch, tamper,
):
    prepared = _prepared(tmp_path, inventory)
    finalization_sha256 = "9" * 64
    result = _write_valid_analysis_snapshot(
        prepared,
        monkeypatch,
        finalization_sha256=finalization_sha256,
    )
    if tamper == "malformed":
        changed = {}
    elif tamper == "integrity":
        changed = json.loads(json.dumps(result))
        changed["integrity"]["results_raw_sha256"] = "0" * 64
    elif tamper == "strata":
        changed = json.loads(json.dumps(result))
        changed["bootstrap"]["strata"] = {}
    else:
        changed = json.loads(json.dumps(result))
        changed["primary"] = "not-an-object"
    prepared.identity.paths.analysis_results.write_text(
        json.dumps(changed, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="analysis"):
        phase3_main_live._validate_analysis_result_snapshot(
            prepared,
            expected_finalization_raw_sha256=finalization_sha256,
            expected_results_raw_sha256=hashlib.sha256(
                prepared.identity.paths.results.read_bytes()).hexdigest(),
            expected_analysis_raw=(
                prepared.identity.paths.analysis_results.read_bytes()),
        )


def test_analysis_result_snapshot_rejects_shape_preserving_replacement(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    finalization_sha256 = "9" * 64
    result = _write_valid_analysis_snapshot(
        prepared,
        monkeypatch,
        finalization_sha256=finalization_sha256,
    )
    trusted_raw = prepared.identity.paths.analysis_results.read_bytes()
    changed = json.loads(json.dumps(result))
    changed["primary"]["D1"] = {"estimate": 0.123}
    prepared.identity.paths.analysis_results.write_text(
        json.dumps(changed, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="trusted in-process output",
    ):
        phase3_main_live._validate_analysis_result_snapshot(
            prepared,
            expected_finalization_raw_sha256=finalization_sha256,
            expected_results_raw_sha256=hashlib.sha256(
                prepared.identity.paths.results.read_bytes()).hexdigest(),
            expected_analysis_raw=trusted_raw,
        )


def test_paid_call_revalidates_authorization_and_price_before_dispatch(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    events = []

    monkeypatch.setattr(
        phase3_main_live,
        "_authorize_provider_logical_dispatch",
        lambda _prepared: (
            events.append("authorization")
            or "2026-08-30T12:00:00+00:00"),
    )

    class Inner:
        dry_run = False

        def complete(
            self, *args, _logical_dispatch_authorization_hook=None, **kwargs,
        ):
            events.append("raw")
            assert _logical_dispatch_authorization_hook() == (
                "2026-08-30T12:00:00+00:00")
            events.append("provider")
            return "response"

    client = phase3_main_live._AuthorizationDeadlineClient(prepared, Inner())
    assert client.complete(messages=[], model="judge") == "response"
    assert events == ["raw", "authorization", "provider"]


def test_provider_authorization_hook_runs_price_before_exact_timed_authority(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    events = []
    authorized_at = phase3_main_live.datetime.now(
        phase3_main_live.timezone.utc)
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_price_snapshot",
        lambda candidate: events.append(("price", candidate)),
    )

    def reload_authorization(candidate):
        events.append(("signature", candidate))
        return prepared.authorization

    monkeypatch.setattr(
        phase3_main_live,
        "_load_unchanged_authenticated_authorization",
        reload_authorization,
    )

    def validate(authorization, manifest, *, as_of=None):
        events.append(("authorization", authorization, manifest, as_of))

    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        validate,
    )

    class FrozenDateTime:
        @staticmethod
        def now(_timezone):
            return authorized_at

    monkeypatch.setattr(phase3_main_live, "datetime", FrozenDateTime)

    assert phase3_main_live._authorize_provider_logical_dispatch(prepared) == (
        authorized_at.isoformat())
    assert events == [
        ("price", prepared),
        ("signature", prepared),
        (
            "authorization",
            prepared.authorization,
            prepared.manifest,
            authorized_at,
        ),
    ]


def test_provider_authorization_expiry_during_signature_check_blocks_before_reservation(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    signature_verified = False
    expired_at = phase3_main_live.datetime.fromisoformat(
        "2026-10-13T20:15:00.000001+00:00")
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_price_snapshot",
        lambda candidate: None,
    )

    def reload_authorization(candidate):
        nonlocal signature_verified
        assert candidate is prepared
        signature_verified = True
        return prepared.authorization

    monkeypatch.setattr(
        phase3_main_live,
        "_load_unchanged_authenticated_authorization",
        reload_authorization,
    )

    class ExpiredAfterSignatureDateTime:
        @staticmethod
        def now(_timezone):
            assert signature_verified
            return expired_at

    monkeypatch.setattr(
        phase3_main_live, "datetime", ExpiredAfterSignatureDateTime)

    def reject_expired(authorization, manifest, *, as_of=None):
        assert authorization is prepared.authorization
        assert manifest is prepared.manifest
        assert as_of == expired_at
        raise phase3_main_live.phase3_main_manifest.MainManifestError(
            "main authorization is not active at the validation time")

    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        reject_expired,
    )

    class SDK:
        def __init__(self):
            self.calls = 0
            outer = self

            class Completions:
                @staticmethod
                def create(**_kwargs):
                    outer.calls += 1
                    raise AssertionError("expired authorization reached provider")

            self.chat = SimpleNamespace(completions=Completions())

    sdk = SDK()
    raw = phase3_main_live.api_client.RejudgeClient(
        approved_cap_usd=1.0,
        _sdk_client=sdk,
        max_retries=0,
    )
    client = phase3_main_live._AuthorizationDeadlineClient(prepared, raw)

    with pytest.raises(
        phase3_main_live.phase3_main_manifest.MainManifestError,
        match="not active",
    ):
        client.complete(
            messages=[{"role": "user", "content": "question"}],
            model="fixture-model",
            temperature=0.0,
            seed=1,
            max_tokens=8,
        )

    assert raw.usage_events == []
    assert sdk.calls == 0


def test_full_provider_stack_replays_after_expiry_but_blocks_a_new_journal_miss(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    authorization_calls = []
    expired = False

    def authorize(candidate):
        assert candidate is prepared
        authorization_calls.append("checked")
        if expired:
            raise phase3_main_live.Phase3MainLiveError("authorization expired")
        return "2026-08-30T12:00:00Z"

    monkeypatch.setattr(
        phase3_main_live, "_authorize_provider_logical_dispatch", authorize)

    class Sdk:
        def __init__(self):
            self.calls = []
            outer = self

            class Completions:
                @staticmethod
                def create(**kwargs):
                    outer.calls.append(dict(kwargs))
                    return SimpleNamespace(
                        usage=SimpleNamespace(
                            prompt_tokens=2, completion_tokens=1),
                        choices=[SimpleNamespace(
                            message=SimpleNamespace(content="response"),
                            finish_reason="stop",
                        )],
                        model="fixture-model",
                        id="fixture-response",
                        system_fingerprint=None,
                    )

            self.chat = SimpleNamespace(completions=Completions())

    sdk = Sdk()
    raw = phase3_main_live.api_client.RejudgeClient(
        approved_cap_usd=1.0,
        _sdk_client=sdk,
        max_retries=0,
    )
    authorized = phase3_main_live._AuthorizationDeadlineClient(prepared, raw)
    resolved = phase3_main_live.RoleLimitResolvingClient(authorized, {})
    client = phase3_main_live.JournalingClient(
        resolved,
        phase3_main_live.RequestJournal(
            tmp_path / "provider-stack-journal.jsonl",
            execution_identity="provider-stack-expiry-test",
        ),
    )
    common = dict(
        messages=[{"role": "user", "content": "question"}],
        model="fixture-model",
        temperature=0.0,
        seed=1,
        max_tokens=8,
        kind="verdict",
    )
    first_metadata = {
        "cell_key": "cell-1",
        "call_role": "judge_verdict",
        "slot": 0,
        "attempt": 0,
    }
    assert client.complete(
        **common, request_metadata=first_metadata) == "response"
    assert authorization_calls == ["checked"]
    assert len(sdk.calls) == 1

    expired = True
    assert client.complete(
        **common, request_metadata=first_metadata) == "response"
    assert authorization_calls == ["checked"]
    assert len(sdk.calls) == 1

    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="authorization expired",
    ):
        client.complete(
            **common,
            request_metadata={
                **first_metadata,
                "cell_key": "cell-2",
            },
        )
    assert authorization_calls == ["checked", "checked"]
    assert len(sdk.calls) == 1


def test_finalization_revalidates_exact_authorization_and_binds_stored_raw_hashes(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared = replace(
        prepared,
        protocol={
            **prepared.protocol,
            "roster": {
                **prepared.protocol["roster"],
                "oracle": "fixture oracle",
            },
        },
    )
    events = []
    captured = {}

    def revalidate(candidate):
        assert candidate is prepared
        events.append("authorization")
        return prepared.authorization

    class FinalizationReached(Exception):
        pass

    def build(**kwargs):
        events.append("build")
        captured.update(kwargs)
        raise FinalizationReached

    monkeypatch.setattr(
        phase3_main_live, "_revalidate_authenticated_authorization_scope", revalidate)
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "build_finalization_admission",
        build,
    )
    terminal_store = (
        phase3_main_live.phase3_main_finalization.MainTerminalDispositionStore(
            tmp_path / "terminal.jsonl",
            run_id=prepared.identity.run_id,
            manifest_canonical_sha256=prepared.identity.manifest_sha256,
            inventory=prepared.inventory,
        )
    )

    with pytest.raises(FinalizationReached):
        phase3_main_live._finalize_main(prepared, terminal_store)
    assert events == ["authorization", "build"]
    assert captured["authorization_raw_sha256"] == prepared.authorization_raw_sha256
    assert captured["authorization_signature_raw_sha256"] == (
        prepared.authorization_signature_raw_sha256)


def test_finalization_completion_timestamp_and_write_follow_final_authority_check(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared = replace(
        prepared,
        protocol={
            **prepared.protocol,
            "roster": {
                **prepared.protocol["roster"],
                "oracle": "fixture oracle",
            },
        },
    )
    events = []
    captured = {}

    def revalidate(_prepared):
        events.append("authorization")
        return prepared.authorization

    monkeypatch.setattr(
        phase3_main_live, "_revalidate_authenticated_authorization_scope", revalidate)
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "build_finalization_admission",
        lambda **kwargs: events.append("build") or {
            "recorded_at_utc": "provisional",
        },
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "validate_finalization_admission",
        lambda *args, **kwargs: events.append("validate") or {},
    )
    class FinalizationWritten(Exception):
        pass

    def write(_path, record):
        events.append("write")
        captured.update(record)
        raise FinalizationWritten

    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "write_finalization_admission",
        write,
    )
    terminal_store = (
        phase3_main_live.phase3_main_finalization.MainTerminalDispositionStore(
            tmp_path / "terminal-completion.jsonl",
            run_id=prepared.identity.run_id,
            manifest_canonical_sha256=prepared.identity.manifest_sha256,
            inventory=prepared.inventory,
        )
    )

    with pytest.raises(FinalizationWritten):
        phase3_main_live._finalize_main(prepared, terminal_store)
    assert events == [
        "authorization",
        "build",
        "validate",
        "authorization",
        "write",
    ]
    assert captured["recorded_at_utc"] != "provisional"


def test_completion_revalidates_capacity_evidence_after_analysis(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared = replace(
        prepared,
        protocol={
            **prepared.protocol,
            "roster": {
                **prepared.protocol["roster"],
                "oracle": "fixture oracle",
            },
        },
    )
    prepared.identity.artifact_root.mkdir(parents=True)
    capacity_result_path = prepared.input_paths["capacity_result"]
    expected_capacity_sha = hashlib.sha256(
        capacity_result_path.read_bytes()).hexdigest()
    validate_calls = []

    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization_scope",
        lambda _prepared: prepared.authorization,
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "build_finalization_admission",
        lambda **_kwargs: {"recorded_at_utc": "provisional"},
    )

    def validate(_record, **kwargs):
        validate_calls.append(kwargs)
        if len(validate_calls) == 2:
            observed = hashlib.sha256(capacity_result_path.read_bytes()).hexdigest()
            if observed != kwargs["expected_capacity_result_raw_sha256"]:
                raise phase3_main_live.phase3_main_finalization.MainFinalizationError(
                    "capacity evidence drifted after analysis")
        return {}

    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "validate_finalization_admission",
        validate,
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_final_boundary_inputs",
        lambda *_args, **_kwargs: prepared.authorization,
    )

    def write_finalization(path, record):
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "write_finalization_admission",
        write_finalization,
    )

    def analysis(_argv):
        capacity_result_path.write_text('{"tampered":true}\n', encoding="utf-8")
        prepared.identity.paths.analysis_results.write_text(
            "{}\n", encoding="utf-8")
        return phase3_main_live.phase3_main_analysis.AnalysisRunReceipt(
            returncode=0,
            output_raw=prepared.identity.paths.analysis_results.read_bytes(),
        )

    monkeypatch.setattr(
        phase3_main_live.phase3_main_analysis,
        "run_analysis",
        analysis,
    )
    terminal_store = (
        phase3_main_live.phase3_main_finalization.MainTerminalDispositionStore(
            tmp_path / "terminal-post-analysis.jsonl",
            run_id=prepared.identity.run_id,
            manifest_canonical_sha256=prepared.identity.manifest_sha256,
            inventory=prepared.inventory,
        )
    )

    with pytest.raises(
        phase3_main_live.phase3_main_finalization.MainFinalizationError,
        match="capacity evidence drifted after analysis",
    ):
        phase3_main_live._finalize_main(prepared, terminal_store)

    assert len(validate_calls) == 2
    assert validate_calls[0]["expected_capacity_result_raw_sha256"] == (
        expected_capacity_sha)
    assert not prepared.identity.paths.completion.exists()


def test_completion_rejects_zero_return_with_unvalidated_analysis_output(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared = replace(
        prepared,
        protocol={
            **prepared.protocol,
            "roster": {
                **prepared.protocol["roster"],
                "oracle": "fixture oracle",
            },
        },
    )
    prepared.identity.artifact_root.mkdir(parents=True)
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization_scope",
        lambda _prepared: prepared.authorization,
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_final_boundary_inputs",
        lambda *_args, **_kwargs: prepared.authorization,
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "build_finalization_admission",
        lambda **_kwargs: {
            "recorded_at_utc": "provisional",
            "artifact_hashes": {
                "result_store": {"raw_sha256": "7" * 64},
            },
        },
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "validate_finalization_admission",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "write_finalization_admission",
        lambda path, record: path.write_text(
            json.dumps(record) + "\n", encoding="utf-8"),
    )

    def analysis(_argv):
        prepared.identity.paths.analysis_results.write_text(
            "{}\n", encoding="utf-8", newline="\n")
        return phase3_main_live.phase3_main_analysis.AnalysisRunReceipt(
            returncode=0,
            output_raw=prepared.identity.paths.analysis_results.read_bytes(),
        )

    monkeypatch.setattr(
        phase3_main_live.phase3_main_analysis, "run_analysis", analysis)
    terminal_store = (
        phase3_main_live.phase3_main_finalization.MainTerminalDispositionStore(
            tmp_path / "terminal-malformed-analysis.jsonl",
            run_id=prepared.identity.run_id,
            manifest_canonical_sha256=prepared.identity.manifest_sha256,
            inventory=prepared.inventory,
        )
    )

    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="analysis result fields drifted",
    ):
        phase3_main_live._finalize_main(prepared, terminal_store)

    assert not prepared.identity.paths.completion.exists()
    assert not phase3_main_live._identity_complete_path(prepared.identity).exists()


@pytest.mark.parametrize(
    "mutated", ["results", "analysis", "decisions", "review_packets"])
def test_completion_rejects_post_validation_output_mutation(
    tmp_path, inventory, monkeypatch, mutated,
):
    prepared = _prepared(tmp_path, inventory)
    prepared = replace(
        prepared,
        protocol={
            **prepared.protocol,
            "roster": {
                **prepared.protocol["roster"],
                "oracle": "fixture oracle",
            },
        },
    )
    prepared.identity.artifact_root.mkdir(parents=True)
    prepared.identity.paths.results.write_bytes(b"stable-results\n")
    expected_results_sha256 = hashlib.sha256(
        prepared.identity.paths.results.read_bytes()).hexdigest()
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization_scope",
        lambda _prepared: prepared.authorization,
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_final_boundary_inputs",
        lambda *_args, **_kwargs: prepared.authorization,
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "build_finalization_admission",
        lambda **_kwargs: {
            "recorded_at_utc": "provisional",
            "artifact_hashes": {
                "result_store": {"raw_sha256": expected_results_sha256},
            },
        },
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "validate_finalization_admission",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization,
        "write_finalization_admission",
        lambda path, record: path.write_text(
            json.dumps(record) + "\n", encoding="utf-8"),
    )

    def analysis(_argv):
        prepared.identity.paths.analysis_results.write_bytes(b"stable-analysis\n")
        return phase3_main_live.phase3_main_analysis.AnalysisRunReceipt(
            returncode=0,
            output_raw=prepared.identity.paths.analysis_results.read_bytes(),
        )

    monkeypatch.setattr(
        phase3_main_live.phase3_main_analysis, "run_analysis", analysis)

    def validate_analysis(_prepared, **_kwargs):
        raw = prepared.identity.paths.analysis_results.read_bytes()
        return raw, hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(
        phase3_main_live, "_validate_analysis_result_snapshot", validate_analysis)
    monkeypatch.setattr(
        phase3_main_live,
        "_require_completion_hashes_match_finalization",
        lambda *_args, **_kwargs: None,
    )

    hash_calls = 0
    def output_hashes(_prepared, *, analysis_results_raw_sha256=None):
        nonlocal hash_calls
        hash_calls += 1
        if mutated == "results":
            prepared.identity.paths.results.write_bytes(b"changed-results\n")
        else:
            if mutated == "analysis" and hash_calls > 1:
                prepared.identity.paths.analysis_results.write_bytes(
                    b"changed-analysis\n")
        return {
            "results": hashlib.sha256(
                prepared.identity.paths.results.read_bytes()).hexdigest(),
            "finalization": hashlib.sha256(
                prepared.identity.paths.finalization.read_bytes()).hexdigest(),
            "analysis_results": (
                analysis_results_raw_sha256
                or hashlib.sha256(
                    prepared.identity.paths.analysis_results.read_bytes()).hexdigest()),
            "decisions": (
                "b" * 64 if mutated == "decisions" and hash_calls > 1 else "a" * 64),
            "review_packets_root": (
                "d" * 64
                if mutated == "review_packets" and hash_calls > 1
                else "c" * 64),
        }

    monkeypatch.setattr(
        phase3_main_live, "_completion_output_hashes", output_hashes)
    terminal_store = (
        phase3_main_live.phase3_main_finalization.MainTerminalDispositionStore(
            tmp_path / f"terminal-{mutated}-mutation.jsonl",
            run_id=prepared.identity.run_id,
            manifest_canonical_sha256=prepared.identity.manifest_sha256,
            inventory=prepared.inventory,
        )
    )

    with pytest.raises(phase3_main_live.Phase3MainLiveError):
        phase3_main_live._finalize_main(prepared, terminal_store)

    assert not prepared.identity.paths.completion.exists()
    assert not phase3_main_live._identity_complete_path(prepared.identity).exists()


def test_price_revalidation_reloads_the_bound_snapshot(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    price_path = (tmp_path / "inputs" / "price_snapshot.json").resolve()
    price_path.write_text(json.dumps(prepared.price_snapshot) + "\n", encoding="utf-8")
    prepared = replace(
        prepared,
        input_paths={**prepared.input_paths, "price_snapshot": price_path},
    )
    prepared.manifest["input_bindings"]["price_snapshot"] = {
        "path": str(price_path),
        "sha256": phase3_main_live.hashlib.sha256(price_path.read_bytes()).hexdigest(),
        "hash_kind": "raw_sha256",
    }
    checks = []
    monkeypatch.setattr(
        phase3_main_live,
        "_validate_price_bindings",
        lambda value, **kwargs: checks.append((value, kwargs)),
    )
    assert phase3_main_live._revalidate_price_snapshot(prepared) == prepared.price_snapshot
    assert checks[0][0] == prepared.price_snapshot
    assert checks[0][1]["require_current_freshness"] is False
    price_path.write_text('{"models":{}}\n', encoding="utf-8")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="raw SHA-256 drifted"):
        phase3_main_live._revalidate_price_snapshot(prepared)


def test_launch_freshness_and_in_run_capacity_integrity_are_separate(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    calls = []
    monkeypatch.setattr(
        phase3_main_live,
        "_validate_price_bindings",
        lambda value, **kwargs: calls.append(("launch-price", kwargs)),
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_validate_capacity",
        lambda **kwargs: calls.append(("capacity", kwargs)) or {"validation": "pass"},
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_validate_capacity_runtime_binding",
        lambda **kwargs: {"runtime": "pass"},
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_billing_reconciliation,
        "validate_billing_reconciliation",
        lambda *args, **kwargs: {
            "run_id": prepared.manifest["run_id"],
            "disposition": "closed",
            "closed": True,
            "accounted_spend_usd": "10.25",
        },
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *args, **kwargs: calls.append(("manifest", kwargs)) or {},
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_verify_execution_code_root",
        lambda *args: calls.append(("code-root", {})),
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_verify_clean_git_identity",
        lambda *args: calls.append(("git-identity", {})),
    )

    phase3_main_live._validate_launch_freshness(prepared)
    assert calls[0][0] == "launch-price"
    assert calls[0][1].get("require_current_freshness", True) is True
    assert calls[1][0] == "capacity"
    assert calls[1][1].get("require_current_freshness", True) is True
    assert calls[2][0] == "manifest"
    assert calls[2][1]["verify_files"] is True
    assert [call[0] for call in calls[3:]] == ["code-root", "git-identity"]

    calls.clear()
    capacity_plan, validation = phase3_main_live._revalidate_capacity_snapshot(prepared)
    assert capacity_plan == {}
    assert validation["runtime"] == {"runtime": "pass"}
    assert calls[0][1]["require_current_freshness"] is False

    prepared.input_paths["capacity_dispatch_history"].write_text(
        "drift\n", encoding="utf-8")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="raw SHA-256 drifted"):
        phase3_main_live._revalidate_capacity_snapshot(prepared)


def test_exact_context_index_is_recomputed_from_the_bound_tokenizer_manifest(
    tmp_path, monkeypatch,
):
    tokenizer_path = (tmp_path / "tokenizer.json").resolve()
    tokenizer = {"schema_version": "fixture"}
    tokenizer_path.write_text(json.dumps(tokenizer) + "\n", encoding="utf-8")
    manifest = {
        "input_bindings": {
            "tokenizer_manifest": {
                "path": str(tokenizer_path),
                "sha256": phase3_main_live.hashlib.sha256(
                    tokenizer_path.read_bytes()).hexdigest(),
                "hash_kind": "raw_sha256",
            },
        },
    }
    expected = {"prompt_tokens": {("main", "model", "role", "variant", "t"): 7}}

    def fake_load(value, *, protocol, project_root):
        assert value == tokenizer
        assert protocol == {"protocol_id": "fixture"}
        assert project_root == ROOT
        return expected

    monkeypatch.setattr(
        phase3_main_live.phase3_v3_inputs, "load_exact_context_index", fake_load)
    assert phase3_main_live._load_verified_exact_context_index(
        manifest,
        input_paths={"tokenizer_manifest": tokenizer_path},
        protocol={"protocol_id": "fixture"},
        root=ROOT,
    ) is expected


def test_loaded_project_code_must_come_from_the_verified_checkout(
    tmp_path, monkeypatch,
):
    phase3_main_live._verify_execution_code_root(ROOT)
    monkeypatch.setattr(
        phase3_main_live.api_client, "__file__", str(tmp_path / "injected" / "api_client.py"))
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="verified project root"):
        phase3_main_live._verify_execution_code_root(ROOT)


def test_loaded_project_code_cannot_be_redirected_inside_verified_checkout(monkeypatch):
    monkeypatch.setattr(
        phase3_main_live.api_client, "__file__", str(ROOT / "rejudge" / "phase3_plan.py"))
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="verified project root"):
        phase3_main_live._verify_execution_code_root(ROOT)


def test_capacity_measurement_is_bound_to_actual_reviewer_runtime_and_host(
    tmp_path, monkeypatch,
):
    cli = (tmp_path / "codex.cmd").resolve()
    cli.write_bytes(b"reviewer-wrapper\n")
    reviewer = {
        "reviewer_cli_binary": "codex.cmd",
        "reviewer_cli_resolved_path": str(cli),
        "reviewer_cli_wrapper_raw_sha256": phase3_main_live.hashlib.sha256(
            cli.read_bytes()).hexdigest(),
        "reviewer_cli_wrapper_byte_count": len(cli.read_bytes()),
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "concurrency": 12,
    }
    plan = {"reviewer_configuration": reviewer}
    result = {
        "measurement_environment": {
            "host_identity": phase3_main_live.platform.node(),
            "reviewer_cli_version": "codex-cli 1.2.3",
        }
    }
    runtime = {
        "reviewer_cli_binary": "codex.cmd",
        "reviewer_model": "gpt-5.6-sol",
        "reviewer_reasoning_effort": "high",
        "reviewer_concurrency": 12,
    }
    monkeypatch.setattr(
        phase3_main_live, "_reviewer_cli_version", lambda path: "codex-cli 1.2.3")
    validated = phase3_main_live._validate_capacity_runtime_binding(
        plan=plan, result=result, runtime=runtime)
    assert validated["reviewer_cli_resolved_path"] == cli

    wrong_model = dict(runtime, reviewer_model="gpt-5.6-terra")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="reviewer runtime"):
        phase3_main_live._validate_capacity_runtime_binding(
            plan=plan, result=result, runtime=wrong_model)
    wrong_host = {
        "measurement_environment": {
            **result["measurement_environment"],
            "host_identity": "another-host",
        }
    }
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="host identity"):
        phase3_main_live._validate_capacity_runtime_binding(
            plan=plan, result=wrong_host, runtime=runtime)
    monkeypatch.setattr(
        phase3_main_live, "_reviewer_cli_version", lambda path: "codex-cli changed")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="CLI version"):
        phase3_main_live._validate_capacity_runtime_binding(
            plan=plan, result=result, runtime=runtime)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("codex_cli_wrapper_raw_sha256", "b" * 64, "wrapper hash"),
        ("codex_cli_wrapper_byte_count", 124, "wrapper size"),
        ("host_identity", "other-host", "capacity-measured host"),
    ],
)
def test_live_reviewer_invocation_must_match_capacity_wrapper_and_host(
    field, replacement, message,
):
    configuration = {
        "reviewer_cli_resolved_path": "C:/measured/codex.cmd",
        "reviewer_cli_wrapper_raw_sha256": "a" * 64,
        "reviewer_cli_wrapper_byte_count": 123,
    }
    invocation = {
        "codex_cli_resolved_path": Path(
            configuration["reviewer_cli_resolved_path"]).resolve().as_posix(),
        "codex_cli_wrapper_raw_sha256": "a" * 64,
        "codex_cli_wrapper_byte_count": 123,
        "codex_cli_version": "codex-cli fixture",
        "host_identity": "fixture-host",
    }
    capacity_validation = {"runtime": {
        "host_identity": "fixture-host",
        "reviewer_cli_version": "codex-cli fixture",
    }}
    assert phase3_main_live._validate_reviewer_invocation_capacity_binding(
        invocation,
        reviewer_configuration=configuration,
        capacity_validation=capacity_validation,
    ) == invocation["codex_cli_resolved_path"]

    changed = dict(invocation)
    changed[field] = replacement
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match=message):
        phase3_main_live._validate_reviewer_invocation_capacity_binding(
            changed,
            reviewer_configuration=configuration,
            capacity_validation=capacity_validation,
        )


def test_live_reviewer_invocation_starts_within_capacity_window():
    capacity_expires_at = phase3_main_live.phase3_main_manifest._utc(
        "2026-08-30T19:00:00Z", "fixture capacity expiry")
    capacity_validation = {
        "capacity_completed_at": phase3_main_live.phase3_main_manifest._utc(
            "2026-08-29T19:00:00Z", "fixture capacity completion"),
        "capacity_expires_at": capacity_expires_at,
    }
    authorization = {
        "approved_at_utc": "2026-08-29T18:00:00Z",
        "valid_until_utc": "2026-08-31T19:00:00Z",
    }
    outcome = {
        "started_at_utc": capacity_expires_at.isoformat(),
        "completed_at_utc": "2026-08-30T19:00:01Z",
    }
    assert phase3_main_live._validate_reviewer_invocation_time_binding(
        outcome,
        authorization=authorization,
        capacity_validation=capacity_validation,
        observed_at=phase3_main_live.phase3_main_manifest._utc(
            "2026-08-30T19:00:02Z", "fixture observation"),
    )[0] == capacity_expires_at

    outcome["started_at_utc"] = "2026-08-30T19:00:00.000001Z"
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="outside the capacity validity window",
    ):
        phase3_main_live._validate_reviewer_invocation_time_binding(
            outcome,
            authorization=authorization,
            capacity_validation=capacity_validation,
            observed_at=phase3_main_live.phase3_main_manifest._utc(
                "2026-08-30T19:00:02Z", "fixture observation"),
        )


def test_reviewer_loop_uses_measured_wave_and_covers_worst_case_payloads():
    plan = {
        "reviewer_configuration": {
            "wave_pending_payload_limit": 64,
            "actual_capacity_wave_size": 60,
        },
        "workload": {"wave_size": 60},
        "capacity_thresholds": {
            "maximum_unique_review_payloads_zero_dedup": 59_040,
        },
    }
    pending, passes = phase3_main_live._reviewer_loop_contract(plan)
    assert pending == 60
    assert passes == (
        984 + phase3_main_live.phase3_main_finalization.MAX_TERMINAL_JUDGMENT_CELLS + 2
    )

    plan["reviewer_configuration"]["actual_capacity_wave_size"] = 64
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="measured workload"):
        phase3_main_live._reviewer_loop_contract(plan)


def test_reviewer_wave_rejects_an_unheld_lease_before_any_boundary_work(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    paths = prepared.identity.paths
    unopened = phase3_main_live.phase3_v3_live.RunLease(paths.lease)
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization",
        lambda *_args: pytest.fail("unheld lease reached authorization revalidation"),
    )
    monkeypatch.setattr(
        phase3_main_live.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("unheld lease reached reviewer subprocess"),
    )

    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="exact held formal run lease",
    ):
        phase3_main_live._review_wave_same_process(
            prepared,
            [{"payload_sha256": "d" * 64}],
            wave=1,
            held_run_lease=unopened,
        )

    assert not paths.review_packets_root.exists()
    assert not paths.lease.exists()


@pytest.mark.parametrize("mutate_invocation_evidence", [False, True])
def test_reviewer_wave_uses_the_capacity_bound_cli_path(
    tmp_path, inventory, monkeypatch, mutate_invocation_evidence,
):
    prepared = _prepared(tmp_path, inventory)
    now = phase3_main_live.datetime.now(phase3_main_live.timezone.utc)
    prepared = replace(prepared, authorization={
        **prepared.authorization,
        "approved_at_utc": (now - timedelta(hours=2)).isoformat(),
        "valid_until_utc": (now + timedelta(hours=2)).isoformat(),
    })
    prepared = _with_current_boundary_files(prepared)
    paths = prepared.identity.paths
    paths.review_packets_root.mkdir(parents=True)
    payload_sha = "d" * 64
    prompt = "review this exact packet"
    prompt_sha = phase3_main_live.hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    worklist = {
        "items": [{
            "payload_sha256": payload_sha,
            "subagent_prompt": prompt,
            "subagent_prompt_sha256": prompt_sha,
        }],
    }
    cli_path = (tmp_path / "measured-codex.cmd").resolve()
    cli_raw = b"@echo fixture\r\n"
    cli_path.write_bytes(cli_raw)
    capacity_plan: dict[str, Any] = {
        "reviewer_configuration": {
            "reviewer_cli_resolved_path": cli_path.as_posix(),
            "reviewer_cli_wrapper_raw_sha256": hashlib.sha256(cli_raw).hexdigest(),
            "reviewer_cli_wrapper_byte_count": len(cli_raw),
        },
    }
    captured = {}

    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization",
        lambda _prepared: prepared.authorization,
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_capacity_snapshot",
        lambda _prepared: (
            capacity_plan,
            {
                "runtime": {
                    "host_identity": "fixture-host",
                    "reviewer_cli_version": "codex-cli fixture",
                },
                "capacity_completed_at": now - timedelta(hours=1),
                "capacity_expires_at": now + timedelta(hours=23),
            },
        ),
    )
    def fake_export(_pending, _prompt, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(worklist, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
            newline="",
        )
        return worklist

    monkeypatch.setattr(
        phase3_main_live, "export_reviewer_worklist", fake_export)
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(phase3_main_live, "_verify_execution_code_root", lambda *args: None)
    monkeypatch.setattr(phase3_main_live, "_verify_clean_git_identity", lambda *args: None)

    def fake_run(command, **kwargs):
        captured["command"] = command
        out_path = Path(command[command.index("--out") + 1])
        guard_path = Path(command[command.index("--dispatch-guard") + 1])
        guard = json.loads(guard_path.read_text(encoding="utf-8"))
        assert guard["schema_version"] == (
            phase3_main_live.codex_reviewer_batch.DISPATCH_GUARD_SCHEMA)
        assert guard["reviewer_model"] == prepared.manifest["runtime"][
            "reviewer_model"]
        assert guard["reviewer_reasoning_effort"] == prepared.manifest["runtime"][
            "reviewer_reasoning_effort"]
        assert guard["reviewer_concurrency"] == prepared.manifest["runtime"][
            "reviewer_concurrency"]
        assert guard["reviewer_cli_version"] == "codex-cli fixture"
        assert guard["capacity_host_identity"] == "fixture-host"
        assert guard["packet_directory"] == guard_path.parent.resolve().as_posix()
        assert guard["output_path"] == out_path.resolve().as_posix()
        assert out_path.read_bytes() == b""
        assert guard["packet_bindings"] == [{
            "file": f"00001_{payload_sha[:12]}.txt",
            "payload_sha256": payload_sha,
            "prompt_sha256": prompt_sha,
            "byte_count": len(prompt.encode("utf-8")),
        }]
        assert set(guard["artifact_bindings"]) == {
            "authorization",
            "authorization_signature",
            "capacity_plan",
            "capacity_result",
            "capacity_dispatch_history",
            "reviewer_cli_wrapper",
            "worklist_snapshot",
            "packet_index",
            "batch_runner",
        }
        raw_output = "LABEL: ACCEPT\nCLAUSE: Allowed\nRATIONALE: Valid."
        out_path.write_text(
            json.dumps({
                "payload_sha256": payload_sha,
                "prompt_sha256": prompt_sha,
                "raw_output": raw_output,
                "tool_uses": 0,
                "evidence": {"fixture": True},
            }) + "\n",
            encoding="utf-8",
        )
        packet = next(
            path for path in guard_path.parent.glob("*.txt")
            if path.name.startswith("00001_"))
        captured["receipt"] = make_receipt(packet)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(phase3_main_live.subprocess, "run", fake_run)

    def make_receipt(packet):
        retained = packet.parent / "retained-ruling.txt"
        retained.write_text(
            "LABEL: ACCEPT\nCLAUSE: Allowed\nRATIONALE: Valid.",
            encoding="utf-8",
            newline="",
        )
        guard_sha = captured["command"][
            captured["command"].index("--dispatch-guard-raw-sha256") + 1]
        started_at = (now - timedelta(minutes=5)).isoformat()
        out_path = Path(captured["command"][
            captured["command"].index("--out") + 1])
        reservation_dir_name = (
            phase3_main_live.codex_reviewer_batch.
            DISPATCH_RESERVATION_DIRECTORY_NAME)
        reservation_relative = (
            f"{reservation_dir_name}/"
            f"{guard_sha}_{payload_sha}.json"
        )
        reservation_path = packet.parent / reservation_relative
        reservation_path.parent.mkdir()
        reservation_raw = (json.dumps({
            "schema_version": (
                phase3_main_live.codex_reviewer_batch.DISPATCH_RESERVATION_SCHEMA),
            "guard_raw_sha256": guard_sha,
            "payload_sha256": payload_sha,
            "prompt_sha256": prompt_sha,
            "packet_file": packet.name,
            "packet_directory": packet.parent.resolve().as_posix(),
            "output_path": out_path.resolve().as_posix(),
            "reviewer_model": prepared.manifest["runtime"]["reviewer_model"],
            "reviewer_reasoning_effort": prepared.manifest["runtime"][
                "reviewer_reasoning_effort"],
            "reviewer_concurrency": prepared.manifest["runtime"][
                "reviewer_concurrency"],
            "reviewer_cli_version": "codex-cli fixture",
            "capacity_host_identity": "fixture-host",
            "reserved_at_utc": started_at,
        }, ensure_ascii=True, sort_keys=True) + "\n").encode("utf-8")
        reservation_path.write_bytes(reservation_raw)
        return {
            "invocation": {
                "codex_cli_resolved_path": Path(
                    capacity_plan["reviewer_configuration"][
                        "reviewer_cli_resolved_path"]
                ).resolve().as_posix(),
                "codex_cli_wrapper_raw_sha256": hashlib.sha256(cli_raw).hexdigest(),
                "codex_cli_wrapper_byte_count": len(cli_raw),
                "codex_cli_version": "codex-cli fixture",
                "host_identity": "fixture-host",
            },
            "outcome": {
                "authorization_deadline_utc": prepared.authorization[
                    "valid_until_utc"],
                "started_at_utc": started_at,
                "completed_at_utc": (now - timedelta(minutes=1)).isoformat(),
                "result_ok": True,
                "event_stream_errors": [],
                "commands": [],
            },
            "artifacts": {"ruling": {"path": retained.name}},
            "dispatch_guard": {
                "verified": True,
                "checked_at_utc": started_at,
                "released_at_utc": started_at,
                "authorization_approved_at_utc": prepared.authorization[
                    "approved_at_utc"],
                "authorization_valid_until_utc": prepared.authorization[
                    "valid_until_utc"],
                "capacity_completed_at_utc": (
                    now - timedelta(hours=1)).isoformat(),
                "capacity_expires_at_utc": (
                    now + timedelta(hours=23)).isoformat(),
                "reviewer_cli_version": "codex-cli fixture",
                "snapshot_raw_sha256": guard_sha,
                "reservation": {
                    "path": reservation_relative,
                    "raw_sha256": hashlib.sha256(reservation_raw).hexdigest(),
                    "byte_count": len(reservation_raw),
                },
            },
        }

    def fake_validate(packet, _evidence, **kwargs):
        assert kwargs == {
            "expected_model": prepared.manifest["runtime"]["reviewer_model"],
            "expected_effort": prepared.manifest["runtime"][
                "reviewer_reasoning_effort"],
            "expected_concurrency": prepared.manifest["runtime"][
                "reviewer_concurrency"],
        }
        assert packet.name == f"00001_{payload_sha[:12]}.txt"
        if mutate_invocation_evidence:
            retained = packet.parent / "retained-ruling.txt"
            retained.write_bytes(retained.read_bytes() + b"\n")
        return captured["receipt"]

    monkeypatch.setattr(
        phase3_main_live.codex_reviewer_batch,
        "validate_invocation_evidence",
        fake_validate,
    )
    paths.decisions.touch()
    paths.reviewer_index.touch()

    with phase3_main_live.phase3_v3_live.RunLease(paths.lease) as held_run_lease:
        if mutate_invocation_evidence:
            with pytest.raises(
                phase3_main_live.Phase3MainLiveError,
                match="reviewer wave evidence tree changed before decision commit",
            ):
                phase3_main_live._review_wave_same_process(
                    prepared,
                    [{"payload_sha256": payload_sha}],
                    wave=1,
                    held_run_lease=held_run_lease,
                )
        else:
            phase3_main_live._review_wave_same_process(
                prepared,
                [{"payload_sha256": payload_sha}],
                wave=1,
                held_run_lease=held_run_lease,
            )
    packet_dir = next(paths.review_packets_root.iterdir())
    if mutate_invocation_evidence:
        assert paths.decisions.read_bytes() == b""
        assert paths.reviewer_index.read_bytes() == b""
        assert not (
            packet_dir / phase3_main_live.phase3_main_reviewer_commit.WAVE_COMMIT_INTENT
        ).exists()
        assert not (
            packet_dir / phase3_main_live.phase3_main_reviewer_commit.WAVE_COMMIT_RECEIPT
        ).exists()
    else:
        decisions = [
            json.loads(line)
            for line in paths.decisions.read_text(encoding="utf-8").splitlines()
        ]
        reviewer_index = [
            json.loads(line)
            for line in paths.reviewer_index.read_text(encoding="utf-8").splitlines()
        ]
        assert len(decisions) == 1
        assert decisions[0]["payload_sha256"] == payload_sha
        assert len(reviewer_index) == 1
        assert reviewer_index[0]["schema_version"] == (
            phase3_main_live.phase3_main_reviewer_commit.REVIEWER_WAVE_SCHEMA)
        assert (
            packet_dir / phase3_main_live.phase3_main_reviewer_commit.WAVE_COMMIT_INTENT
        ).is_file()
        assert (
            packet_dir / phase3_main_live.phase3_main_reviewer_commit.WAVE_COMMIT_RECEIPT
        ).is_file()
    command = captured["command"]
    assert command[command.index("--codex") + 1] == cli_path.as_posix()
    assert command[command.index("--not-after-utc") + 1] == (
        prepared.authorization["valid_until_utc"])


def test_two_packet_guard_abort_cannot_commit_main_decisions_or_wave(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    now = phase3_main_live.datetime.now(phase3_main_live.timezone.utc)
    prepared = replace(prepared, authorization={
        **prepared.authorization,
        "approved_at_utc": (now - timedelta(hours=2)).isoformat(),
        "valid_until_utc": (now + timedelta(hours=2)).isoformat(),
    })
    prepared = _with_current_boundary_files(prepared)
    paths = prepared.identity.paths
    paths.review_packets_root.mkdir(parents=True)
    cli_path = (tmp_path / "guarded-codex.cmd").resolve()
    cli_raw = b"@echo fixture\r\n"
    cli_path.write_bytes(cli_raw)
    capacity_plan = {
        "reviewer_configuration": {
            "reviewer_cli_resolved_path": cli_path.as_posix(),
            "reviewer_cli_wrapper_raw_sha256": hashlib.sha256(cli_raw).hexdigest(),
            "reviewer_cli_wrapper_byte_count": len(cli_raw),
        },
    }
    payloads = ("d" * 64, "e" * 64)
    worklist_items = []
    for number, payload_sha in enumerate(payloads, 1):
        prompt = f"review exact packet {number}"
        worklist_items.append({
            "payload_sha256": payload_sha,
            "subagent_prompt": prompt,
            "subagent_prompt_sha256": hashlib.sha256(
                prompt.encode("utf-8")).hexdigest(),
        })
    worklist = {"items": worklist_items}

    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization",
        lambda _prepared: prepared.authorization,
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_capacity_snapshot",
        lambda _prepared: (
            capacity_plan,
                {
                    "runtime": {
                        "host_identity": "fixture-host",
                        "reviewer_cli_version": "codex-cli fixture",
                    },
                "capacity_completed_at": now - timedelta(hours=1),
                "capacity_expires_at": now + timedelta(hours=23),
            },
        ),
    )

    def fake_export(_pending, _prompt, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(worklist, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
            newline="",
        )
        return worklist

    monkeypatch.setattr(phase3_main_live, "export_reviewer_worklist", fake_export)
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(phase3_main_live, "_verify_execution_code_root", lambda *args: None)
    monkeypatch.setattr(phase3_main_live, "_verify_clean_git_identity", lambda *args: None)

    def fake_batch(command, **_kwargs):
        packet_dir = Path(command[command.index("--packets") + 1])
        packet_index = json.loads(
            (packet_dir / "INDEX.json").read_text(encoding="utf-8"))
        assert packet_index["count"] == 2
        assert command[command.index("--concurrency") + 1] == "12"
        return SimpleNamespace(
            returncode=3,
            stdout="",
            stderr="dispatch guard rejected the second invocation",
        )

    monkeypatch.setattr(phase3_main_live.subprocess, "run", fake_batch)
    with phase3_main_live.phase3_v3_live.RunLease(paths.lease) as held_run_lease:
        with pytest.raises(
            phase3_main_live.Phase3MainLiveError,
            match="reviewer wave 1 failed with exit 3",
        ):
            phase3_main_live._review_wave_same_process(
                prepared,
                [{"payload_sha256": payload_sha} for payload_sha in payloads],
                wave=1,
                held_run_lease=held_run_lease,
            )

    assert not paths.decisions.exists()
    assert not paths.reviewer_index.exists()
    assert not list(paths.review_packets_root.rglob("WAVE_COMMIT_*.json"))


def test_private_factory_enforces_strict_accounting_and_unknown_charge_halt(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    captured = {}
    raw = object()

    def fake_client(**kwargs):
        captured.update(kwargs)
        return raw

    def fake_resolver(inner, limits):
        assert isinstance(inner, phase3_main_live._AuthorizationDeadlineClient)
        assert inner._inner is raw
        assert inner._prepared is prepared
        assert limits == {}
        return "resolved"

    monkeypatch.setattr(phase3_main_live.api_client, "RejudgeClient", fake_client)
    monkeypatch.setattr(phase3_main_live, "RoleLimitResolvingClient", fake_resolver)
    usage_path = prepared.identity.paths.usage_ledger
    snapshot = phase3_main_live.api_client.UsageLedgerSnapshot(
        path=usage_path,
        state_path=phase3_main_live.api_client.usage_ledger_state_path(usage_path),
        identity={},
        summary={},
        last_sequence=0,
        last_event_hash="genesis",
    )
    result = phase3_main_live._construct_provider_client(
        prepared, snapshot)
    assert result == "resolved"
    assert captured["approved_cap_usd"] == 40.0
    assert captured["initial_spend_usd"] == 10.25
    assert captured["initial_uncertain_spend_usd"] == 0.0
    assert captured["strict_model_pricing"] is True
    assert captured["halt_on_unknown_charge"] is True
    assert captured["require_returned_model_match"] is True
    assert captured["_accounting_factory_token"] is (
        phase3_main_live.api_client._LIVE_ACCOUNTING_FACTORY_TOKEN)


def test_launch_accepts_closed_conservative_billing_at_the_manifest_upper_bound(
    tmp_path, inventory,
):
    prepared = _prepared(tmp_path, inventory)
    validation = {
        "run_id": prepared.manifest["run_id"],
        "disposition": "closed_conservative_envelope",
        "closed": True,
        "within_conservative_envelope": True,
        "accounted_spend_usd": "10.25",
        "uncertain_spend_usd": "0.04",
        "unresolved_attempt_ids": ("attempt-unknown",),
    }
    phase3_main_live._require_clean_pre_main_billing(
        validation, prepared.manifest)

    validation["run_id"] = "another-main-run"
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="run ID differs from the manifest",
    ):
        phase3_main_live._require_clean_pre_main_billing(
            validation, prepared.manifest)
    validation["run_id"] = prepared.manifest["run_id"]

    validation["accounted_spend_usd"] = "10.24"
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="not closed at the manifest upper bound",
    ):
        phase3_main_live._require_clean_pre_main_billing(
            validation, prepared.manifest)


def test_identity_registry_survives_artifact_root_loss_and_blocks_reuse(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    events = []

    monkeypatch.setattr(
        phase3_main_live, "load_prepared_main", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        phase3_main_live, "_require_production_execution_unblocked", lambda: None)
    monkeypatch.setattr(
        phase3_main_live, "_revalidate_authenticated_authorization",
        lambda _prepared: prepared.authorization)
    monkeypatch.setattr(phase3_main_live, "_verify_clean_git_identity", lambda *args: None)
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest, "validate_main_manifest",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest, "validate_main_authorization",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(phase3_main_live, "_validate_price_bindings", lambda *args, **kwargs: {})
    monkeypatch.setattr(phase3_main_live, "_validate_capacity", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        phase3_main_live, "_validate_capacity_runtime_binding",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(
        phase3_main_live.phase3_main_billing_reconciliation,
        "validate_billing_reconciliation",
        lambda *args, **kwargs: {
            "run_id": prepared.manifest["run_id"],
            "disposition": "closed",
            "closed": True,
            "accounted_spend_usd": "10.25",
            "uncertain_spend_usd": "0",
            "unresolved_attempt_ids": (),
        },
    )

    def fake_preseed(**kwargs):
        Path(kwargs["target_store_path"]).touch()
        events.append("preseed")
        return {"main_bundle_count": 492, "written": 492, "skipped": 0}

    class FakeProvider:
        def complete(self, *args, **kwargs):
            return "unused"

    def fake_factory(_prepared, _snapshot):
        events.append("factory")
        assert prepared.identity.paths.active_marker.is_file()
        assert prepared.identity.paths.identity_binding.is_file()
        assert prepared.identity.paths.usage_ledger.is_file()
        return FakeProvider()

    monkeypatch.setattr(phase3_preseed_transcripts, "preseed_main", fake_preseed)
    monkeypatch.setattr(phase3_main_live, "_construct_provider_client", fake_factory)
    monkeypatch.setattr(
        phase3_main_live, "_drive_and_finalize",
        lambda _prepared, _client, **_kwargs: {"status": "test-complete"})

    assert phase3_main_live.run_main("manifest", "authorization") == {
        "status": "test-complete"}
    assert events == ["preseed", "factory"]
    start_path = phase3_main_live._identity_start_path(prepared.identity)
    consumed_path = phase3_main_live._authorization_consumed_path(prepared)
    assert start_path.is_file()
    assert json.loads(consumed_path.read_text(encoding="utf-8")) == {
        "authorization_canonical_sha256": phase3_main_live.canonical_sha256(
            prepared.authorization),
        "authorization_id": prepared.authorization["authorization_id"],
        "authorization_raw_sha256": prepared.authorization_raw_sha256,
        "authorization_signature_raw_sha256": (
            prepared.authorization_signature_raw_sha256),
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        "recorded_at_utc": json.loads(
            consumed_path.read_text(encoding="utf-8"))["recorded_at_utc"],
        "run_id": prepared.identity.run_id,
        "schema_version": phase3_main_live.AUTHORIZATION_CONSUMED_SCHEMA,
        "status": "consumed_no_reuse",
    }
    assert prepared.identity.paths.active_marker.is_file()

    shutil.rmtree(prepared.identity.artifact_root)
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="already consumed"):
        phase3_main_live.run_main("manifest", "authorization")
    assert events == ["preseed", "factory"]
    assert not prepared.identity.paths.active_marker.exists()


def test_unknown_charge_is_identity_fatal_in_the_production_loop(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared.input_paths["capacity_plan"].write_text(
        json.dumps({
            "reviewer_configuration": {
                "wave_pending_payload_limit": 64,
                "actual_capacity_wave_size": 60,
            },
            "workload": {"wave_size": 60},
            "capacity_thresholds": {
                "maximum_unique_review_payloads_zero_dedup": 59_040,
            },
        }) + "\n",
        encoding="utf-8",
    )
    prepared.manifest["input_bindings"]["capacity_plan"]["sha256"] = (
        phase3_main_live.hashlib.sha256(
            prepared.input_paths["capacity_plan"].read_bytes()).hexdigest()
    )
    factory_calls = []
    loop_calls = []

    monkeypatch.setattr(
        phase3_main_live, "load_prepared_main", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        phase3_main_live, "_require_production_execution_unblocked", lambda: None)
    monkeypatch.setattr(
        phase3_main_live, "_revalidate_authenticated_authorization",
        lambda _prepared: prepared.authorization)
    monkeypatch.setattr(phase3_main_live, "_verify_execution_code_root", lambda *args: None)
    monkeypatch.setattr(phase3_main_live, "_verify_clean_git_identity", lambda *args: None)
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest, "validate_main_manifest",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest, "validate_main_authorization",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(phase3_main_live, "_validate_price_bindings", lambda *args, **kwargs: {})
    monkeypatch.setattr(phase3_main_live, "_validate_capacity", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        phase3_main_live, "_validate_capacity_runtime_binding",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(
        phase3_main_live.phase3_main_billing_reconciliation,
        "validate_billing_reconciliation",
        lambda *args, **kwargs: {
            "run_id": prepared.manifest["run_id"],
            "disposition": "closed",
            "closed": True,
            "accounted_spend_usd": "10.25",
            "uncertain_spend_usd": "0",
            "unresolved_attempt_ids": (),
        },
    )

    def fake_preseed(**kwargs):
        assert phase3_main_live._identity_start_path(prepared.identity).is_file()
        Path(kwargs["target_store_path"]).touch()
        return {"main_bundle_count": 492, "written": 492, "skipped": 0}

    def fake_factory(_prepared, _snapshot):
        factory_calls.append(True)
        assert phase3_main_live._identity_start_path(prepared.identity).is_file()
        return object()

    def fake_run_canary(**kwargs):
        loop_calls.append(kwargs)
        assert kwargs["fatal_unknown_charge"] is True
        return SimpleNamespace(
            completed=0,
            skipped=0,
            pending_payloads=[],
            halted_reason="unknown_charge",
            halted_cell_key="main-judgment-cell",
        )

    monkeypatch.setattr(phase3_preseed_transcripts, "preseed_main", fake_preseed)
    monkeypatch.setattr(phase3_main_live, "_construct_provider_client", fake_factory)
    monkeypatch.setattr(
        phase3_main_live.phase3_runner,
        "resolve_main_cells",
        lambda *args, **kwargs: [SimpleNamespace(cell_key="main-judgment-cell")],
    )
    monkeypatch.setattr(phase3_main_live, "run_canary", fake_run_canary)
    monkeypatch.setattr(
        phase3_main_live, "_review_wave_same_process",
        lambda *args, **kwargs: pytest.fail("unknown charge reached reviewer dispatch"),
    )
    monkeypatch.setattr(
        phase3_main_live, "_finalize_main",
        lambda *args, **kwargs: pytest.fail("unknown charge reached finalization"),
    )

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="unknown_charge"):
        phase3_main_live.run_main("manifest", "authorization")
    assert len(factory_calls) == 1
    assert len(loop_calls) == 1
    start_path = phase3_main_live._identity_start_path(prepared.identity)
    assert start_path.is_file()
    assert phase3_main_live._authorization_consumed_path(prepared).is_file()
    assert not prepared.identity.paths.completion.exists()

    shutil.rmtree(prepared.identity.artifact_root)
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="already consumed"):
        phase3_main_live.run_main("manifest", "authorization")
    assert len(factory_calls) == 1
    assert len(loop_calls) == 1


def test_completion_hashes_bind_directory_tree_and_leave_only_self_null(
    tmp_path, inventory,
):
    prepared = _prepared(tmp_path, inventory)
    output_paths = {}
    for name, filename in phase3_main_live.phase3_main_manifest.OUTPUT_FILENAMES.items():
        path = prepared.identity.artifact_root / filename
        output_paths[name] = path
        if name == "completion":
            continue
        if name == "review_packets_root":
            path.mkdir(parents=True)
            (path / "packet.txt").write_text("bound\n", encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{name}\n", encoding="utf-8")
    validation = dict(prepared.manifest_validation)
    validation["output_paths"] = output_paths
    prepared = replace(prepared, manifest_validation=validation)
    hashes = phase3_main_live._completion_output_hashes(prepared)
    assert hashes["completion"] is None
    assert all(value is not None for name, value in hashes.items() if name != "completion")


def test_completion_hashes_must_match_finalization_artifacts_and_reviewer_tree():
    mapped = (
        phase3_main_live.phase3_main_finalization
        .FINALIZATION_ARTIFACT_TO_MANIFEST_OUTPUT
    )
    output_hashes = {output_name: "a" * 64 for output_name in mapped.values()}
    output_hashes["review_packets_root"] = "b" * 64
    finalization = {
        "artifact_hashes": {
            artifact_name: {"raw_sha256": "a" * 64}
            for artifact_name in mapped
        },
        "reviewer_provenance": {
            "review_packets_tree_canonical_sha256": "b" * 64,
        },
    }

    phase3_main_live._require_completion_hashes_match_finalization(
        output_hashes, finalization)

    changed = dict(output_hashes)
    changed["decisions"] = "c" * 64
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="decisions"):
        phase3_main_live._require_completion_hashes_match_finalization(
            changed, finalization)

    changed = dict(output_hashes)
    changed["review_packets_root"] = "d" * 64
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="packet tree",
    ):
        phase3_main_live._require_completion_hashes_match_finalization(
            changed, finalization)


def test_cli_requires_an_explicit_mode():
    with pytest.raises(SystemExit) as exc:
        phase3_main_live.main([
            "--manifest", "manifest.json",
            "--authorization", "authorization.json",
        ])
    assert exc.value.code == 2


def test_cli_validate_only_cannot_construct_or_run_provider(
    tmp_path, inventory, monkeypatch, capsys,
):
    prepared = _prepared(tmp_path, inventory)
    calls = []

    def fake_load(*args, **kwargs):
        calls.append((args, kwargs))
        return prepared

    monkeypatch.setattr(phase3_main_live, "load_prepared_main", fake_load)
    monkeypatch.setattr(
        phase3_main_live, "run_main",
        lambda *args, **kwargs: pytest.fail("validate-only reached run_main"),
    )
    monkeypatch.setattr(
        phase3_main_live, "_construct_provider_client",
        lambda *args, **kwargs: pytest.fail("validate-only constructed a provider client"),
    )

    rc = phase3_main_live.main([
        "--manifest", "manifest.json",
        "--authorization", "authorization.json",
        "--validate-only",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "execution_started": False,
        "manifest_canonical_sha256": "a" * 64,
        "provider_client_created": False,
        "run_id": "phase3-main-live-test",
        "status": "validated_only",
    }
    assert len(calls) == 1
    assert calls[0][1]["verify_git"] is True


def test_cli_run_is_separate_and_failures_refuse(
    monkeypatch, capsys,
):
    monkeypatch.setattr(
        phase3_main_live, "run_main",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            phase3_main_live.Phase3MainLiveError("authorization expired")
        ),
    )
    rc = phase3_main_live.main([
        "--manifest", "manifest.json",
        "--authorization", "authorization.json",
        "--run",
    ])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert captured.err == (
        "REFUSED: Phase3MainLiveError: authorization expired\n"
    )
