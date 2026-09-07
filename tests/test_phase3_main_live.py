"""Production-boundary tests for the Phase 3 main driver."""
from __future__ import annotations

import inspect
import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from rejudge import phase3_main_live, phase3_main_runner, phase3_owner_signing
from rejudge import phase3_main_runtime_policies
from scripts import phase3_preseed_transcripts


ROOT = Path(__file__).resolve().parents[1]
MODEL_PROVIDER_PROFILE = {
    "id": "openai-http",
    "name": "OpenAI",
    "wire_api": "responses",
    "requires_openai_auth": True,
    "supports_websockets": False,
    "http_headers": {"version": "0.149.0"},
}


def _authenticated_billing_fields() -> dict[str, Any]:
    account = "a" * 64
    return {
        "evidence_kind": (
            phase3_main_live.phase3_main_billing_reconciliation
            .AUTHENTICATED_EVIDENCE_KIND
        ),
        "billing_scope": {
            "account_identity_sha256": account,
            "window_start_utc": "2026-08-29T18:00:00Z",
            "window_end_utc": "2026-08-29T21:00:00Z",
        },
        "provider_settlement": {
            "status": (
                phase3_main_live.phase3_main_billing_reconciliation
                .PROVIDER_SETTLEMENT_STATUS
            ),
            "account_identity_sha256": account,
            "finalized_through_utc": "2026-08-29T20:00:00Z",
        },
        "prior_spend_upper_bound_usd": "10.25",
    }


@pytest.fixture(scope="module")
def inventory():
    return phase3_main_runner.build_canonical_main_inventory(ROOT)


def test_capacity_validation_uses_the_plan_derivation_tag(tmp_path, monkeypatch):
    capacity = phase3_main_live.phase3_main_review_capacity_preflight
    observed = {}
    workload = object()
    monkeypatch.setattr(capacity, "collect_source_snapshot", lambda **_kwargs: object())
    monkeypatch.setattr(
        capacity,
        "derive_workload",
        lambda _snapshot, **kwargs: observed.setdefault("tag", kwargs["derivation_tag"])
        and workload,
    )
    monkeypatch.setattr(capacity, "validate_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        capacity,
        "load_bound_dispatch_history",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        capacity,
        "validate_result",
        lambda *_args, **_kwargs: {
            "validation": "pass",
            "evidence_expires_at_utc": "2026-09-02T13:00:00Z",
        },
    )
    plan = {
        "source_locations": {
            "sealed_archive": str(tmp_path),
            "finalization_record": str(tmp_path / "finalization.json"),
        },
        "workload": {"derivation_tag": capacity.DERIVATION_TAG_V5},
    }
    result = {"completed_at_utc": "2026-09-02T12:17:04Z"}
    phase3_main_live._validate_capacity(
        plan=plan,
        result=result,
        history_path=tmp_path / "history.jsonl",
        as_of=datetime(2026, 9, 2, 12, 30, tzinfo=timezone.utc),
    )
    assert observed["tag"] == capacity.DERIVATION_TAG_V5


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
        "capacity_execution_manifest", "capacity_execution_authorization",
        "context_blocklist", "analysis_pins", "billing_reconciliation",
        "price_snapshot", "raw_provider_catalog", "raw_serverless_endpoints",
        "price_change_policy", "reviewer_usage_policy",
    ):
        path = inputs / f"{name}.json"
        path.write_text("{}\n", encoding="utf-8")
        input_paths[name] = path.resolve()
    capacity_authorization_signature = input_paths[
        "capacity_execution_authorization"
    ].with_name("capacity_execution_authorization.json.sig")
    capacity_authorization_signature.write_text(
        "test capacity signature\n", encoding="utf-8"
    )
    input_paths["capacity_execution_authorization_signature"] = (
        capacity_authorization_signature.resolve()
    )
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
        "restart": {"mode": "initial", "predecessor": None},
        "runtime": {
            "provider_account_identity_sha256": "a" * 64,
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
    input_paths["price_change_policy"].write_text(
        json.dumps(
            phase3_main_runtime_policies.EXPECTED_PRICE_CHANGE_POLICY,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    input_paths["reviewer_usage_policy"].write_text(
        json.dumps(
            phase3_main_runtime_policies.EXPECTED_REVIEWER_USAGE_POLICY,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for name in ("price_change_policy", "reviewer_usage_policy"):
        manifest["input_bindings"][name]["sha256"] = (
            phase3_main_live.hashlib.sha256(input_paths[name].read_bytes()).hexdigest()
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
            "provider_account_identity_sha256": "a" * 64,
            "stage_cap_usd": "40.00",
            "approved_at_utc": "2026-08-29T20:15:00Z",
            "valid_until_utc": "2026-10-13T20:15:00Z",
        },
        manifest_validation={
            "run_id": manifest["run_id"],
            "manifest_canonical_sha256": manifest_sha,
            "recorded_at_utc": datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc),
            "artifact_root": artifact,
            "identity_registry_root": registry,
            "input_paths": input_paths,
            "output_paths": {},
            "restart": {"mode": "initial", "predecessor": None},
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
        price_change_policy_validation={
            "schema_version": phase3_main_runtime_policies.PRICE_CHANGE_POLICY_SCHEMA,
            "operator_signal_filename": (
                phase3_main_runtime_policies.PRICE_CHANGE_SIGNAL_FILENAME
            ),
            "execution_authorized": False,
        },
        reviewer_usage_policy_validation={
            "schema_version": phase3_main_runtime_policies.REVIEWER_USAGE_POLICY_SCHEMA,
            "usage_unit": phase3_main_runtime_policies.REVIEWER_USAGE_UNIT,
            "maximum_reviewer_dispatches": 59_040,
            "execution_authorized": False,
        },
        harness_validation={},
        inventory=inventory,
        context_excluded_cell_keys=(),
        uncertain_spend_policy_validation=(
            phase3_main_runtime_policies.load_and_validate_uncertain_spend_policy(ROOT)),
        # Amendment 15: a synthetic voided predecessor so the wiring is exercised without
        # the archived ledger; 0.50 USD on top of the 10.25 prior below.
        predecessor_void_accounting_validation={
            "schema_version": phase3_main_runtime_policies.PREDECESSOR_VOID_ACCOUNTING_SCHEMA,
            "record_id": "phase3-main-predecessor-void-accounting-fixture",
            "record_raw_sha256": "e" * 64,
            "predecessor_run_id": "phase3-main-fixture-predecessor",
            "predecessor_manifest_canonical_sha256": "f" * 64,
            "predecessor_artifact_root": "E:/fixture/predecessor",
            "accounted_spend_usd": "0.50",
            "maximum_successor_expenditure_usd": "29.25",
            "expected_stage_total_usd": "10.75",
            "ledger_verified": False,
            "execution_authorized": False,
        },
    )


def _seed_started_identity(
    prepared: phase3_main_live.PreparedMainRun,
) -> tuple[Path, Path]:
    prepared.identity.artifact_root.mkdir(parents=True, exist_ok=True)
    registry_root = prepared.identity.identity_registry_root
    assert registry_root is not None
    phase3_main_live._durably_create_directory_tree(registry_root / "identities")
    ledger_path = prepared.identity.paths.usage_ledger
    snapshot, _ = phase3_main_live.phase3_main_runner._fresh_ledger_snapshot(
        ledger_path)
    start_path = phase3_main_live._start_identity(prepared, snapshot)
    return start_path, ledger_path


def _seed_price_signal_identity(
    prepared: phase3_main_live.PreparedMainRun,
) -> tuple[Path, Path]:
    prepared.identity.artifact_root.mkdir(parents=True, exist_ok=True)
    registry_root = prepared.identity.identity_registry_root
    assert registry_root is not None
    phase3_main_live._durably_create_directory_tree(registry_root / "identities")
    phase3_main_live.phase3_main_runner._write_active_marker(  # noqa: SLF001
        prepared.identity, prepared.identity.paths)
    phase3_main_live.phase3_main_runner._write_identity_binding(  # noqa: SLF001
        prepared.identity, prepared.identity.paths)
    ledger_path = prepared.identity.paths.usage_ledger
    snapshot, _ = phase3_main_live.phase3_main_runner._fresh_ledger_snapshot(  # noqa: SLF001
        ledger_path)
    start_path = phase3_main_live._start_identity(prepared, snapshot)
    return start_path, ledger_path


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
    run_source = inspect.getsource(phase3_main_live.run_main)
    assert run_source.index("_durably_create_directory_tree(paths.root)") < (
        run_source.index("_write_active_marker"))


def test_exclusive_json_publication_recovers_orphan_temp_and_reopens_exact(tmp_path):
    target = tmp_path / "registry" / "identity.started.json"
    target.parent.mkdir(parents=True)
    temp = phase3_main_live._exclusive_publish_temp_path(target)
    temp.write_bytes(b'{"partial":')
    payload = {"schema_version": "fixture", "status": "complete"}
    expected = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")

    phase3_main_live._write_exclusive_json(
        target, payload, label="fixture identity record")

    assert target.read_bytes() == expected
    assert not temp.exists()
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="already exists"):
        phase3_main_live._write_exclusive_json(
            target, {"status": "replacement"}, label="fixture identity record")
    assert target.read_bytes() == expected


def test_exclusive_json_publication_failure_leaves_no_partial_final(
    tmp_path, monkeypatch,
):
    target = tmp_path / "registry" / "identity.voided.json"
    target.parent.mkdir(parents=True)

    def fail_publish(_source, _target):
        raise OSError("simulated interrupted publication")

    monkeypatch.setattr(
        phase3_main_live, "_publish_staged_no_replace", fail_publish)
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="could not atomically publish",
    ):
        phase3_main_live._write_exclusive_json(
            target, {"status": "voided"}, label="fixture void record")
    assert not target.exists()
    assert not phase3_main_live._exclusive_publish_temp_path(target).exists()


def test_exclusive_json_publication_cleans_only_same_file_post_link_alias(tmp_path):
    target = tmp_path / "registry" / "identity.started.json"
    target.parent.mkdir(parents=True)
    payload = {"schema_version": "fixture", "status": "started"}
    expected = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    target.write_bytes(expected)
    temp = phase3_main_live._exclusive_publish_temp_path(target)
    phase3_main_live.os.link(target, temp)

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="already exists"):
        phase3_main_live._write_exclusive_json(
            target, payload, label="fixture identity record")
    assert target.read_bytes() == expected
    assert not temp.exists()

    temp.write_bytes(b"different inode")
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="different file",
    ):
        phase3_main_live._write_exclusive_json(
            target, payload, label="fixture identity record")
    assert target.read_bytes() == expected
    assert temp.read_bytes() == b"different inode"


def test_fresh_identity_registry_tree_publishes_each_created_ancestor(
    tmp_path, monkeypatch,
):
    existing = tmp_path / "existing"
    existing.mkdir()
    target = existing / "registry" / "identities"
    published = []

    def publish(directory):
        published.append(Path(directory).resolve())
        Path(directory).mkdir()

    monkeypatch.setattr(phase3_main_live, "_publish_missing_directory", publish)

    phase3_main_live._durably_create_directory_tree(target)

    assert target.is_dir()
    assert published == [target.parent.resolve(), target.resolve()]


def test_fresh_nested_artifact_tree_publishes_parent_before_child(
    tmp_path, monkeypatch,
):
    existing = tmp_path / "existing"
    existing.mkdir()
    nested = existing / "nested"
    artifact_root = nested / "formal"
    published = []

    def publish(directory):
        published.append(Path(directory).resolve())
        Path(directory).mkdir()

    monkeypatch.setattr(phase3_main_live, "_publish_missing_directory", publish)

    phase3_main_live._durably_create_directory_tree(artifact_root)

    assert artifact_root.is_dir()
    assert published == [
        nested.resolve(),
        artifact_root.resolve(),
    ]


def test_registry_bootstrap_lease_is_stable_and_outside_registry(tmp_path):
    target = (tmp_path / "registry" / "identities").resolve()

    first = phase3_main_live._registry_bootstrap_lease_path(target)
    second = phase3_main_live._registry_bootstrap_lease_path(target)
    different = phase3_main_live._registry_bootstrap_lease_path(
        tmp_path / "other-registry" / "identities")

    assert first == second
    assert first != different
    assert first.parent == Path(phase3_main_live.tempfile.gettempdir()).resolve()
    assert target not in first.parents


def test_registry_bootstrap_holds_lease_while_publishing_tree(tmp_path, monkeypatch):
    registry_root = (tmp_path / "registry").resolve()
    events = []

    class FakeLease:
        def __init__(self, path):
            events.append(("lease-created", Path(path)))

        def __enter__(self):
            events.append(("lease-entered", None))
            return self

        def __exit__(self, *_exc_info):
            events.append(("lease-exited", None))
            return False

    def publish(path):
        events.append(("published", Path(path).resolve()))

    monkeypatch.setattr(phase3_main_live.phase3_v3_live, "RunLease", FakeLease)
    monkeypatch.setattr(phase3_main_live, "_durably_create_directory_tree", publish)

    phase3_main_live._durably_prepare_identity_registry(registry_root)

    assert events[0][0] == "lease-created"
    assert events[1:] == [
        ("lease-entered", None),
        ("published", registry_root / "identities"),
        ("lease-exited", None),
    ]


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_write_through_move_is_exact_and_never_replaces(tmp_path):
    stage = tmp_path / ".record.publish.tmp"
    target = tmp_path / "record.json"
    stage.write_bytes(b"first\n")

    phase3_main_live.phase3_main_runner._windows_move_no_replace_write_through(
        stage, target)

    assert not stage.exists()
    assert target.read_bytes() == b"first\n"
    stage.write_bytes(b"second\n")
    with pytest.raises(FileExistsError):
        phase3_main_live.phase3_main_runner._windows_move_no_replace_write_through(
            stage, target)
    assert stage.read_bytes() == b"second\n"
    assert target.read_bytes() == b"first\n"


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_write_through_move_supports_extended_length_paths(tmp_path):
    parent = tmp_path
    while len(str(parent)) < 280:
        parent /= "extended-length-segment"
    parent.mkdir(parents=True)
    stage = parent / ".record.publish.tmp"
    target = parent / "record.json"
    stage.write_bytes(b"long path\n")

    phase3_main_live.phase3_main_runner._windows_move_no_replace_write_through(
        stage, target)

    assert not stage.exists()
    assert target.read_bytes() == b"long path\n"


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_no_replace_move_does_not_follow_dangling_destination_symlink(tmp_path):
    stage = tmp_path / ".record.publish.tmp"
    destination = tmp_path / "record.json"
    missing_target = tmp_path / "outside" / "missing.json"
    stage.write_bytes(b"new bytes\n")
    try:
        destination.symlink_to(missing_target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable on this Windows host: {exc}")

    with pytest.raises(FileExistsError):
        phase3_main_live.phase3_main_runner._windows_move_no_replace_write_through(
            stage, destination)

    assert stage.read_bytes() == b"new bytes\n"
    assert destination.is_symlink()
    assert not missing_target.exists()


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_no_replace_move_uses_lexical_existence_check(tmp_path, monkeypatch):
    stage = tmp_path / ".record.publish.tmp"
    destination = tmp_path / "dangling-link.json"
    stage.write_bytes(b"new bytes\n")
    real_lexists = phase3_main_live.api_client.durable_fs.os.path.lexists

    def simulated_dangling_link(path):
        if Path(path) == destination:
            return True
        return real_lexists(path)

    monkeypatch.setattr(
        phase3_main_live.api_client.durable_fs.os.path,
        "lexists",
        simulated_dangling_link,
    )

    with pytest.raises(FileExistsError):
        phase3_main_live.phase3_main_runner._windows_move_no_replace_write_through(
            stage, destination)
    assert stage.read_bytes() == b"new bytes\n"
    assert not destination.exists()


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_replace_move_refuses_symlink_and_preserves_its_target(tmp_path):
    stage = tmp_path / ".state.publish.tmp"
    destination = tmp_path / "state.json"
    protected_target = tmp_path / "protected.json"
    stage.write_bytes(b"new state\n")
    protected_target.write_bytes(b"protected\n")
    try:
        destination.symlink_to(protected_target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable on this Windows host: {exc}")

    with pytest.raises(OSError, match="reparse-point"):
        phase3_main_live.phase3_main_runner._windows_move_write_through(
            stage, destination, replace_existing=True)

    assert stage.read_bytes() == b"new state\n"
    assert destination.is_symlink()
    assert protected_target.read_bytes() == b"protected\n"


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_replace_move_refuses_reparse_destination(tmp_path, monkeypatch):
    stage = tmp_path / ".state.publish.tmp"
    destination = tmp_path / "state.json"
    stage.write_bytes(b"new state\n")
    destination.write_bytes(b"protected\n")
    monkeypatch.setattr(
        phase3_main_live.api_client.durable_fs,
        "_windows_is_reparse_point",
        lambda path: Path(path) == destination,
    )

    with pytest.raises(OSError, match="reparse-point"):
        phase3_main_live.phase3_main_runner._windows_move_write_through(
            stage, destination, replace_existing=True)
    assert stage.read_bytes() == b"new state\n"
    assert destination.read_bytes() == b"protected\n"


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_usage_ledger_and_state_use_write_through_publication(
    tmp_path, monkeypatch,
):
    ledger = tmp_path / "usage.jsonl"
    calls = []
    real_move = phase3_main_live.api_client.durable_fs.windows_move_write_through

    def observed_move(source, destination, *, replace_existing):
        calls.append((Path(destination).name, replace_existing))
        return real_move(
            source, destination, replace_existing=replace_existing)

    monkeypatch.setattr(
        phase3_main_live.api_client.durable_fs,
        "windows_move_write_through",
        observed_move,
    )

    identity = phase3_main_live.api_client.prepare_usage_ledger(
        ledger, allow_create=True)

    state_path = phase3_main_live.api_client.usage_ledger_state_path(ledger)
    assert ledger.is_file()
    assert state_path.is_file()
    assert identity["ledger_path"] == ledger.resolve().as_posix()
    assert calls == [
        (ledger.name, False),
        (state_path.name, True),
    ]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    phase3_main_live.api_client._atomic_write_json(state_path, state)  # noqa: SLF001
    assert calls[-1] == (state_path.name, True)
    assert len(calls) == 3


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_directory_publication_uses_write_through_move(tmp_path):
    target = tmp_path / "durable-directory"

    phase3_main_live._publish_missing_directory(target)

    assert target.is_dir()
    assert not list(tmp_path.glob(".durable-directory.mkdir-*.tmp"))


@pytest.mark.skipif(phase3_main_live.os.name != "nt", reason="Windows durability primitive")
def test_windows_directory_publication_fails_closed_on_concurrent_target(
    tmp_path, monkeypatch,
):
    target = tmp_path / "durable-directory"

    def race(_stage, destination):
        Path(destination).mkdir()
        raise FileExistsError("simulated concurrent publisher")

    monkeypatch.setattr(
        phase3_main_live.phase3_main_runner,
        "_windows_move_no_replace_write_through",
        race,
    )

    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="appeared concurrently",
    ):
        phase3_main_live._publish_missing_directory(target)
    assert target.is_dir()
    assert not list(tmp_path.glob(".durable-directory.mkdir-*.tmp"))


def test_artifact_publication_probe_preserves_preexisting_path(tmp_path, monkeypatch):
    artifact_root = tmp_path / "formal"
    artifact_root.mkdir()
    probe = artifact_root / ".phase3-main-publication-probe-fixed"
    original = b"user-owned preexisting bytes"
    probe.write_bytes(original)
    monkeypatch.setattr(
        phase3_main_live,
        "_new_artifact_publication_probe_path",
        lambda _root: probe,
    )

    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="probe path already exists",
    ):
        phase3_main_live._probe_artifact_publication(artifact_root)
    assert probe.read_bytes() == original


def test_artifact_publication_probe_preserves_racing_writer_path(
    tmp_path, monkeypatch,
):
    artifact_root = tmp_path / "formal"
    artifact_root.mkdir()
    probe = artifact_root / ".phase3-main-publication-probe-fixed"
    raced = b"racing writer bytes"
    monkeypatch.setattr(
        phase3_main_live,
        "_new_artifact_publication_probe_path",
        lambda _root: probe,
    )

    def racing_publish(path, _raw, *, label):
        assert label == "artifact-volume publication probe"
        path.write_bytes(raced)
        raise phase3_main_live.Phase3MainLiveError("simulated publication race")

    monkeypatch.setattr(
        phase3_main_live, "_publish_exclusive_bytes", racing_publish)
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="simulated publication race",
    ):
        phase3_main_live._probe_artifact_publication(artifact_root)
    assert probe.read_bytes() == raced


def test_public_paid_path_rejects_invalid_launch_before_any_formal_state_mutation(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)

    def invalid_launch(*_args, **_kwargs):
        raise phase3_main_live.Phase3MainLiveError("invalid launch package")

    monkeypatch.setattr(
        phase3_main_live, "load_prepared_main", invalid_launch)
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="invalid launch package",
    ):
        phase3_main_live.run_main("manifest", "authorization")
    assert not prepared.identity.artifact_root.exists()
    registry_root = prepared.identity.identity_registry_root
    assert registry_root is not None
    assert not registry_root.exists()


def test_static_production_blockers_are_closed():
    assert phase3_main_live.PRODUCTION_EXECUTION_BLOCKERS == ()


def test_forecast_recomputation_uses_original_certification_clock():
    price = {"verified_at_utc": "2026-09-04T21:18:47.663553Z"}
    forecast = {
        "price_validation": {
            "validation": "pass",
            "canonical_sha256": "a" * 64,
            "verified_at_utc": "2026-09-04T21:18:47.663553Z",
            "age_seconds": 57.83706,
            "required_models": ["model-a", "model-b"],
            "raw_catalog_checked": True,
            "raw_serverless_endpoints_checked": True,
        }
    }
    recovered = phase3_main_live._forecast_recomputation_as_of(
        forecast,
        price,
        current_as_of=datetime(2026, 9, 4, 22, tzinfo=timezone.utc),
    )
    assert recovered == datetime(
        2026, 9, 4, 21, 19, 45, 500613, tzinfo=timezone.utc
    )


def test_forecast_recomputation_rejects_stale_original_certification():
    price = {"verified_at_utc": "2026-09-04T21:18:47.663553Z"}
    forecast = {
        "price_validation": {
            "validation": "pass",
            "canonical_sha256": "a" * 64,
            "verified_at_utc": "2026-09-04T21:18:47.663553Z",
            "age_seconds": 86_401,
            "required_models": ["model-a", "model-b"],
            "raw_catalog_checked": True,
            "raw_serverless_endpoints_checked": True,
        }
    }
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="certified with stale price evidence",
    ):
        phase3_main_live._forecast_recomputation_as_of(
            forecast,
            price,
            current_as_of=datetime(2026, 9, 6, tzinfo=timezone.utc),
        )


def test_main_inventory_thaws_to_the_forecast_plan_shape(inventory):
    cells = [
        phase3_main_live.phase3_main_runner._thaw_value(cell)
        for cell in inventory.cells
    ]
    assert all(isinstance(cell["dependency_keys"], list) for cell in cells)
    phase3_main_live.phase3_plan.validate_cells(
        cells,
        str(json.loads(
            (ROOT / "rejudge" / "phase3_protocol_v3_r6.json").read_text(
                encoding="utf-8"
            )
        )["cell_key_namespace"]),
    )


def test_launch_freshness_failure_precedes_identity_start(
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
    assert not phase3_main_live._identity_start_path(prepared.identity).exists()


def test_authorization_is_rechecked_after_launch_freshness_before_identity_start(
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
        "_start_identity",
        lambda _prepared: pytest.fail("expired authorization started identity"),
    )

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="expired"):
        phase3_main_live.run_main("manifest", "authorization")
    assert events == ["freshness", "authorization"]
    assert not phase3_main_live._identity_start_path(prepared.identity).exists()


def test_runtime_account_failure_follows_local_gates_and_precedes_all_formal_state(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    events = []

    monkeypatch.setattr(
        phase3_main_live, "load_prepared_main", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        phase3_main_live, "_require_production_execution_unblocked", lambda: None)
    monkeypatch.setattr(
        phase3_main_live,
        "_validate_launch_freshness",
        lambda _prepared: events.append("freshness"),
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization",
        lambda _prepared: events.append("authorization"),
    )

    def reject_runtime_identity(_prepared):
        events.append("whoami")
        raise phase3_main_live.Phase3MainLiveError("runtime account rejected")

    monkeypatch.setattr(
        phase3_main_live, "_construct_verified_runtime_provider_sdk", reject_runtime_identity)
    monkeypatch.setattr(
        phase3_main_live,
        "_durably_prepare_identity_registry",
        lambda _root: pytest.fail("runtime identity failure mutated the registry"),
    )

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="runtime account rejected"):
        phase3_main_live.run_main("manifest", "authorization")
    assert events == ["freshness", "authorization", "whoami"]
    assert not prepared.identity.artifact_root.exists()
    registry_root = prepared.identity.identity_registry_root
    assert registry_root is not None
    assert not registry_root.exists()


def test_child_environment_scrubs_together_credentials_case_insensitively(monkeypatch):
    monkeypatch.setenv("together_api_key", "secret")
    monkeypatch.setenv("TOGETHER_BASE_URL", "https://override.invalid/v1")
    monkeypatch.setenv("PHASE3_UNRELATED_TEST_VALUE", "retained")

    child = phase3_main_live._subprocess_environment_without_together_credentials()

    assert "TOGETHER_API_KEY" not in {name.upper() for name in child}
    assert "TOGETHER_BASE_URL" not in {name.upper() for name in child}
    assert child["PHASE3_UNRELATED_TEST_VALUE"] == "retained"


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


def test_live_authorization_still_requires_signature_with_pinned_owner_key(tmp_path):
    authorization = tmp_path / "authorization.json"
    authorization.write_text("{}\n", encoding="utf-8")
    assert phase3_owner_signing.OWNER_SIGNING_PUBLIC_KEY is not None
    assert phase3_owner_signing.OWNER_SIGNING_KEY_FINGERPRINT == (
        "SHA256:e3z7s2CQDDLg2nx/XDI93Tm+JerqZSo8H0GRL88Szmk"
    )
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="could not load signed main authorization and sidecar",
    ):
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
    monkeypatch.setattr(phase3_owner_signing, "OWNER_SIGNING_PUBLIC_KEY", public_key)
    monkeypatch.setattr(
        phase3_owner_signing, "OWNER_SIGNING_KEY_FINGERPRINT", fingerprint)

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
            assert _logical_dispatch_authorization_hook is not None
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


def test_price_change_signal_blocks_before_price_or_authorization_revalidation(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared.identity.artifact_root.mkdir(parents=True)
    prepared.identity.paths.price_change_signal.write_text(
        "{}\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_price_snapshot",
        lambda *_args: pytest.fail("price-change signal reached price revalidation"),
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_load_unchanged_authenticated_authorization",
        lambda *_args: pytest.fail("price-change signal reached authorization"),
    )

    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="price-change signal or publish stage is present",
    ):
        phase3_main_live._authorize_provider_logical_dispatch(prepared)


def test_price_change_signal_publish_stage_is_also_fail_safe(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared.identity.artifact_root.mkdir(parents=True)
    prepared.identity.paths.price_change_signal_publish_temp.write_text(
        '{"partial":', encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_price_snapshot",
        lambda *_args: pytest.fail("price-change publish stage reached price revalidation"),
    )
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="price-change signal or publish stage is present",
    ):
        phase3_main_live._authorize_provider_logical_dispatch(prepared)


def test_price_change_signal_writer_binds_started_identity_and_evidence(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared.manifest_path.write_text(
        json.dumps(prepared.manifest, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *_args, **_kwargs: prepared.manifest_validation,
    )
    _seed_price_signal_identity(prepared)
    evidence = (tmp_path / "provider-price-notice.json").resolve()
    evidence.write_text(
        '{"model":"fixture","price_changed":true}\n',
        encoding="utf-8",
        newline="\n",
    )

    result = phase3_main_live.record_provider_price_change(
        prepared.manifest_path,
        trigger_kind="provider_notification",
        evidence_path=evidence,
        note="Provider notice reports a changed model price.",
    )

    signal_path = prepared.identity.paths.price_change_signal
    signal = json.loads(signal_path.read_text(encoding="utf-8"))
    assert result["signal_path"] == signal_path.as_posix()
    assert result["signal_raw_sha256"] == hashlib.sha256(
        signal_path.read_bytes()).hexdigest()
    assert result["stop_new_logical_provider_calls"] is True
    assert result["execution_authorized"] is False
    assert signal["manifest_canonical_sha256"] == prepared.identity.manifest_sha256
    assert signal["evidence"] == {
        "path": evidence.as_posix(),
        "raw_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "byte_count": len(evidence.read_bytes()),
    }
    assert phase3_main_runtime_policies.load_and_validate_price_change_signal(
        signal_path,
        run_id=prepared.identity.run_id,
        manifest_canonical_sha256=prepared.identity.manifest_sha256,
        artifact_root=prepared.identity.artifact_root,
        journal_execution_identity=prepared.identity.journal_execution_identity,
    )["stop_new_logical_provider_calls"] is True
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="price-change signal or publish stage is present",
    ):
        phase3_main_live._authorize_provider_logical_dispatch(prepared)
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="already exists",
    ):
        phase3_main_live.record_provider_price_change(
            prepared.manifest_path,
            trigger_kind="provider_notification",
            evidence_path=evidence,
            note="A duplicate signal must not replace the first record.",
        )


def test_price_change_signal_writer_rejects_evidence_drift_before_publication(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared.manifest_path.write_text(
        json.dumps(prepared.manifest, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *_args, **_kwargs: prepared.manifest_validation,
    )
    _seed_price_signal_identity(prepared)
    evidence = (tmp_path / "provider-price-notice.json").resolve()
    evidence.write_text("first observation\n", encoding="utf-8", newline="\n")
    real_stable_read = phase3_main_live._stable_regular_file_bytes
    changed = False

    def change_after_first_read(path, label):
        nonlocal changed
        raw = real_stable_read(path, label)
        if label == "provider price-change evidence" and not changed:
            changed = True
            evidence.write_text("changed observation\n", encoding="utf-8", newline="\n")
        return raw

    monkeypatch.setattr(
        phase3_main_live, "_stable_regular_file_bytes", change_after_first_read)
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="signal is invalid|evidence changed",
    ):
        phase3_main_live.record_provider_price_change(
            prepared.manifest_path,
            trigger_kind="operator_observation",
            evidence_path=evidence,
            note="The evidence changed during stop-signal preparation.",
        )
    assert changed is True
    assert not prepared.identity.paths.price_change_signal.exists()


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
            **_authenticated_billing_fields(),
            "run_id": prepared.manifest["run_id"],
            "disposition": "closed",
            "closed": True,
            "accounted_spend_usd": "10.25",
            "provider_delta_usd": "10.25",
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
    assert calls[1][1]["execution_manifest_path"] == prepared.input_paths[
        "capacity_execution_manifest"
    ]
    assert calls[1][1]["execution_authorization_path"] == prepared.input_paths[
        "capacity_execution_authorization"
    ]
    assert calls[1][1][
        "execution_authorization_signature_path"
    ] == prepared.input_paths["capacity_execution_authorization_signature"]
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


def test_capacity_validation_authenticates_and_reopens_execution_provenance(
    tmp_path,
    monkeypatch,
):
    now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    completed = now - timedelta(minutes=5)
    expires = now + timedelta(hours=23)
    capacity = phase3_main_live.phase3_main_review_capacity_preflight
    plan = {
        "schema_version": capacity.SCHEMA_VERSION,
        "source_locations": {
            "sealed_archive": str(tmp_path / "archive"),
            "finalization_record": str(tmp_path / "finalization.json"),
        },
        "workload": {"derivation_tag": capacity.DERIVATION_TAG_V1},
    }
    result = {"completed_at_utc": completed.isoformat()}
    plan_path = (tmp_path / "capacity-plan.json").resolve()
    result_path = (tmp_path / "capacity-result.json").resolve()
    history_path = (tmp_path / "capacity-history.jsonl").resolve()
    execution_manifest_path = (tmp_path / "capacity-execution.json").resolve()
    execution_authorization_path = (tmp_path / "capacity-authorization.json").resolve()
    execution_signature_path = execution_authorization_path.with_name(
        f"{execution_authorization_path.name}.sig"
    )
    execution_signature_path.write_text("fixture signature\n", encoding="utf-8")
    context = SimpleNamespace(plan=plan)
    execution_manifest_raw = b'{"manifest":"exact"}\n'
    execution_manifest = {
        "run_id": "capacity-run-test",
        "attempt_id": "capacity-attempt-test",
        "result_path": result_path.as_posix(),
        "dispatch_history": {"path": history_path.as_posix()},
    }
    authorization_raw = b'{"authorization":"exact"}\n'
    authorization = {"authorization_id": "capacity-owner-test"}
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        phase3_main_live.phase3_main_review_capacity_preflight,
        "collect_source_snapshot",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_review_capacity_preflight,
        "derive_workload",
        lambda _snapshot: object(),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_review_capacity_preflight,
        "validate_plan",
        lambda *_args, **_kwargs: {"validation": "plan-pass"},
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_review_capacity_preflight,
        "load_bound_dispatch_history",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_review_capacity_preflight,
        "validate_result",
        lambda *_args, **_kwargs: {
            "validation": "measurement-pass",
            "evidence_expires_at_utc": expires.isoformat(),
        },
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_capacity_execution,
        "load_capacity_context",
        lambda path, **kwargs: (
            observed.update(context_path=path, context_kwargs=kwargs) or context
        ),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_capacity_execution,
        "load_execution_manifest",
        lambda path, **kwargs: (
            observed.update(manifest_path=path, manifest_kwargs=kwargs)
            or (execution_manifest_raw, execution_manifest)
        ),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_capacity_execution,
        "load_authenticated_capacity_authorization",
        lambda path: (
            observed.update(authorization_path=path)
            or (authorization_raw, authorization)
        ),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_capacity_execution,
        "validate_authorization",
        lambda value, **kwargs: (
            observed.update(validated_authorization=value, authorization_kwargs=kwargs)
            or dict(authorization)
        ),
    )
    monkeypatch.setattr(
        phase3_main_live.phase3_main_capacity_execution,
        "validate_execution_result",
        lambda value, **kwargs: (
            observed.update(validated_result=value, result_kwargs=kwargs)
            or {
                "reopened_dispatch_reservations": 180,
                "reopened_invocation_receipts": 180,
            }
        ),
    )

    validation = phase3_main_live._validate_capacity(
        plan=plan,
        result=result,
        history_path=history_path,
        as_of=now,
        require_current_freshness=False,
        plan_path=plan_path,
        result_path=result_path,
        execution_manifest_path=execution_manifest_path,
        execution_authorization_path=execution_authorization_path,
        execution_authorization_signature_path=execution_signature_path,
    )

    provenance = validation["execution_provenance"]
    assert provenance["authorization_id"] == "capacity-owner-test"
    assert provenance["validation"]["reopened_dispatch_reservations"] == 180
    assert provenance["validation"]["reopened_invocation_receipts"] == 180
    assert observed["context_path"] == plan_path
    assert observed["manifest_path"] == execution_manifest_path
    assert observed["authorization_path"] == execution_authorization_path
    assert observed["authorization_kwargs"]["observed_at"] == completed
    assert observed["result_kwargs"]["as_of_utc"] == now
    assert observed["result_kwargs"]["require_current_freshness"] is False


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


def test_reviewer_usage_admission_enforces_exact_aggregate_ceiling(
    tmp_path, inventory,
):
    prepared = _prepared(tmp_path, inventory)
    assert phase3_main_live._admit_reviewer_wave_quantity(
        prepared,
        previously_admitted=59_000,
        incoming=40,
    ) == 59_040
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="exceed the exact authorized usage ceiling",
    ):
        phase3_main_live._admit_reviewer_wave_quantity(
            prepared,
            previously_admitted=59_000,
            incoming=41,
        )


def test_reviewer_usage_reservation_is_durable_before_child_release() -> None:
    source = inspect.getsource(phase3_main_live._drive_and_finalize)
    reservation = source.index('"event": "reviewer_usage_reserved"')
    release = source.index("_review_wave_same_process(")
    completion = source.index('"event": "reviewer_usage_wave_completed"')
    assert reservation < release < completion
    assert '"failed_or_ambiguous_dispatches_count": True' in source


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
            "model_provider_profile": MODEL_PROVIDER_PROFILE,
        },
    }
    captured = {}
    monkeypatch.setenv("TOGETHER_API_KEY", "must-not-reach-reviewer")
    monkeypatch.setenv("together_base_url", "https://must-not-reach.invalid/v1")

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
        assert "TOGETHER_API_KEY" not in {
            name.upper() for name in kwargs["env"]}
        assert "TOGETHER_BASE_URL" not in {
            name.upper() for name in kwargs["env"]}
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
            "expected_model_provider_profile": MODEL_PROVIDER_PROFILE,
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
    assert json.loads(command[
        command.index("--model-provider-profile-json") + 1
    ]) == MODEL_PROVIDER_PROFILE


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
    sdk = object()
    result = phase3_main_live._construct_provider_client(
        prepared, snapshot, sdk_client=sdk)
    assert result == "resolved"
    assert captured["approved_cap_usd"] == 40.0
    # prior 10.25 plus the fixture's voided predecessor 0.50 (amendment 15)
    assert captured["initial_spend_usd"] == 10.75
    assert captured["initial_uncertain_spend_usd"] == 0.0
    assert captured["strict_model_pricing"] is True
    assert captured["halt_on_unknown_charge"] is True
    assert captured["require_returned_model_match"] is True
    assert captured["_sdk_client"] is sdk
    assert captured["_accounting_factory_token"] is (
        phase3_main_live.api_client._LIVE_ACCOUNTING_FACTORY_TOKEN)


def test_runtime_sdk_factory_uses_one_verified_key_snapshot(monkeypatch, tmp_path, inventory):
    prepared = _prepared(tmp_path, inventory)
    monkeypatch.setenv("TOGETHER_API_KEY", "verified-key-A")
    monkeypatch.setenv("TOGETHER_BASE_URL", "https://unapproved.invalid/v1")
    events = []
    sdk = object()

    def fake_verify(**kwargs):
        events.append(("whoami", kwargs.copy()))
        assert kwargs["api_key"] == "verified-key-A"
        assert kwargs["expected_account_identity_sha256"] == "a" * 64
        monkeypatch.setenv("TOGETHER_API_KEY", "changed-key-B")
        return {"account_identity_sha256": "a" * 64}

    def fake_build(**kwargs):
        events.append(("sdk", kwargs.copy()))
        return sdk

    monkeypatch.setattr(
        phase3_main_live.phase3_main_together_billing_capture,
        "verify_runtime_account_identity",
        fake_verify,
    )
    monkeypatch.setattr(
        phase3_main_live.api_client, "build_pinned_together_client", fake_build)

    assert phase3_main_live._construct_verified_runtime_provider_sdk(prepared) is sdk
    assert [event[0] for event in events] == ["whoami", "sdk"]
    sdk_args = events[1][1]
    assert sdk_args["api_key"] == "verified-key-A"
    assert sdk_args["base_url"] == (
        phase3_main_live.api_client.PINNED_TOGETHER_INFERENCE_BASE_URL)
    assert sdk_args["follow_redirects"] is False
    assert sdk_args["sdk_internal_max_retries"] == 0


def test_runtime_sdk_factory_rejects_identity_failure_before_sdk_construction(
    monkeypatch, tmp_path, inventory,
):
    prepared = _prepared(tmp_path, inventory)
    monkeypatch.setenv("TOGETHER_API_KEY", "wrong-key")
    monkeypatch.setattr(
        phase3_main_live.phase3_main_together_billing_capture,
        "verify_runtime_account_identity",
        lambda **_kwargs: (_ for _ in ()).throw(
            phase3_main_live.phase3_main_together_billing_capture
            .TogetherBillingCaptureError("account differs")
        ),
    )
    monkeypatch.setattr(
        phase3_main_live.api_client,
        "build_pinned_together_client",
        lambda **_kwargs: pytest.fail("identity failure constructed an inference SDK"),
    )

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="account differs"):
        phase3_main_live._construct_verified_runtime_provider_sdk(prepared)


def test_launch_accepts_closed_conservative_billing_at_the_manifest_upper_bound(
    tmp_path, inventory,
):
    prepared = _prepared(tmp_path, inventory)
    validation = {
        **_authenticated_billing_fields(),
        "run_id": prepared.manifest["run_id"],
        "disposition": "closed_conservative_envelope",
        "closed": True,
        "within_conservative_envelope": True,
        "accounted_spend_usd": "10.25",
        "prior_spend_upper_bound_usd": "10.25",
        "provider_delta_usd": "10.21",
        "uncertain_spend_usd": "0.04",
        "unresolved_attempt_ids": ("attempt-unknown",),
    }
    phase3_main_live._require_clean_pre_main_billing(
        validation, prepared.manifest)

    legacy = {
        **validation,
        "evidence_kind": (
            phase3_main_live.phase3_main_billing_reconciliation.LEGACY_EVIDENCE_KIND
        ),
        "provider_settlement": None,
    }
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="not provider-verified and finalized",
    ):
        phase3_main_live._require_clean_pre_main_billing(
            legacy, prepared.manifest)

    validation["run_id"] = "another-main-run"
    phase3_main_live._require_clean_pre_main_billing(
        validation, prepared.manifest)
    validation["run_id"] = prepared.manifest["run_id"]

    other_account = {
        **validation,
        "billing_scope": {
            **validation["billing_scope"],
            "account_identity_sha256": "b" * 64,
        },
        "provider_settlement": {
            **validation["provider_settlement"],
            "account_identity_sha256": "b" * 64,
        },
    }
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="not provider-verified and finalized",
    ):
        phase3_main_live._require_clean_pre_main_billing(
            other_account, prepared.manifest)

    validation["prior_spend_upper_bound_usd"] = "10.24"
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="not closed at the manifest upper bound",
    ):
        phase3_main_live._require_clean_pre_main_billing(
            validation, prepared.manifest)
    validation["prior_spend_upper_bound_usd"] = "10.25"
    validation["provider_delta_usd"] = "10.26"
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="not closed at the manifest upper bound",
    ):
        phase3_main_live._require_clean_pre_main_billing(
            validation, prepared.manifest)


def test_launch_accepts_final_console_billing_below_local_actual(
    tmp_path, inventory,
):
    prepared = _prepared(tmp_path, inventory)
    account = prepared.manifest["runtime"]["provider_account_identity_sha256"]
    validation = {
        "evidence_kind": (
            phase3_main_live.phase3_main_billing_reconciliation.CONSOLE_EVIDENCE_KIND
        ),
        "billing_scope": {
            "account_identity_sha256": account,
            "window_start_utc": "2026-08-18T00:00:00Z",
            "window_end_utc": "2026-08-30T00:00:00Z",
        },
        "provider_settlement": {
            "status": (
                phase3_main_live.phase3_main_billing_reconciliation
                .CONSOLE_SETTLEMENT_STATUS
            ),
            "account_identity_sha256": account,
            "finalized_through_utc": "2026-08-30T00:00:00Z",
        },
        "run_id": prepared.manifest["run_id"],
        "disposition": "closed_provider_final_below_local_actual",
        "closed": True,
        "within_conservative_envelope": False,
        "accounted_spend_usd": "10.249999999",
        "prior_spend_upper_bound_usd": "10.25",
        "provider_delta_usd": "9.75",
        "uncertain_spend_usd": "0.50",
        "unresolved_attempt_ids": ("attempt-unknown",),
    }

    phase3_main_live._require_clean_pre_main_billing(
        validation, prepared.manifest)

    validation["provider_settlement"]["status"] = (
        phase3_main_live.phase3_main_billing_reconciliation.PROVIDER_SETTLEMENT_STATUS
    )
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="not provider-verified and finalized",
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
        phase3_main_live, "_construct_verified_runtime_provider_sdk", lambda _prepared: object())
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
            **_authenticated_billing_fields(),
            "run_id": prepared.manifest["run_id"],
            "disposition": "closed",
            "closed": True,
            "accounted_spend_usd": "10.25",
            "provider_delta_usd": "10.25",
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

    def fake_factory(_prepared, _snapshot, *, sdk_client):
        assert sdk_client is not None
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

    real_publish = phase3_main_live._publish_staged_no_replace

    def fail_artifact_publish(source, target):
        if Path(target).parent.resolve() == prepared.identity.artifact_root:
            raise OSError("simulated artifact-volume publication failure")
        return real_publish(source, target)

    monkeypatch.setattr(
        phase3_main_live, "_publish_staged_no_replace", fail_artifact_publish)
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="could not atomically publish artifact-volume publication probe",
    ):
        phase3_main_live.run_main("manifest", "authorization")
    assert events == ["preseed"]
    assert not phase3_main_live._identity_start_path(prepared.identity).exists()
    assert not list(prepared.identity.artifact_root.glob(
        ".phase3-main-publication-probe-*"))
    shutil.rmtree(prepared.identity.artifact_root)
    events.clear()
    monkeypatch.setattr(
        phase3_main_live, "_publish_staged_no_replace", real_publish)

    assert phase3_main_live.run_main("manifest", "authorization") == {
        "status": "test-complete"}
    assert events == ["preseed", "factory"]
    start_path = phase3_main_live._identity_start_path(prepared.identity)
    assert start_path.is_file()
    start = json.loads(start_path.read_text(encoding="utf-8"))
    usage_snapshot = phase3_main_live.api_client.load_chained_usage_ledger(
        prepared.identity.paths.usage_ledger)
    usage_state_path = phase3_main_live.api_client.usage_ledger_state_path(
        prepared.identity.paths.usage_ledger).resolve()
    assert start == {
        "authorization_canonical_sha256": phase3_main_live.canonical_sha256(
            prepared.authorization),
        "authorization_id": prepared.authorization["authorization_id"],
        "authorization_raw_sha256": prepared.authorization_raw_sha256,
        "authorization_signature_raw_sha256": (
            prepared.authorization_signature_raw_sha256),
        "manifest_canonical_sha256": prepared.identity.manifest_sha256,
        "manifest_identity_sha256": prepared.manifest["manifest_identity_sha256"],
        "artifact_root": prepared.identity.artifact_root.as_posix(),
        "usage_ledger_schema_version": (
            phase3_main_live.api_client.USAGE_LEDGER_SCHEMA_VERSION),
        "usage_ledger_id": usage_snapshot.identity["ledger_id"],
        "usage_ledger_path": prepared.identity.paths.usage_ledger.resolve().as_posix(),
        "usage_ledger_state_path": usage_state_path.as_posix(),
        "usage_ledger_identity_canonical_sha256": (
            phase3_main_live.canonical_sha256(usage_snapshot.identity)),
        "usage_ledger_genesis_event_hash": usage_snapshot.last_event_hash,
        "usage_ledger_genesis_raw_sha256": hashlib.sha256(
            prepared.identity.paths.usage_ledger.read_bytes()).hexdigest(),
        "usage_ledger_genesis_state_raw_sha256": hashlib.sha256(
            usage_state_path.read_bytes()).hexdigest(),
        "recorded_at_utc": start["recorded_at_utc"],
        "run_id": prepared.identity.run_id,
        "schema_version": phase3_main_live.IDENTITY_START_SCHEMA,
        "status": "started_single_shot",
    }
    assert prepared.identity.paths.active_marker.is_file()

    shutil.rmtree(prepared.identity.artifact_root)
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="single-shot state"):
        phase3_main_live.run_main("manifest", "authorization")
    assert events == ["preseed", "factory"]
    assert not prepared.identity.paths.active_marker.exists()


def test_environmental_interruption_record_is_one_line_singleton_and_non_authorizing(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared.manifest_path.write_text(
        json.dumps(prepared.manifest) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *args, **kwargs: prepared.manifest_validation,
    )
    start_path, ledger_path = _seed_started_identity(prepared)

    result = phase3_main_live.record_environmental_interruption(
        prepared.manifest_path,
        reason_code="network_outage",
    )
    void_path = phase3_main_live._identity_void_path(prepared.identity)
    raw = void_path.read_bytes()
    void = json.loads(raw)
    assert raw.count(b"\n") == 1 and raw.endswith(b"\n")
    assert result == {
        "status": "voided_environmental_interruption",
        "run_id": prepared.identity.run_id,
        "reason_code": "network_outage",
        "record_path": void_path.as_posix(),
        "replacement_authorized": False,
    }
    assert set(void) == phase3_main_live.IDENTITY_VOID_FIELDS
    assert void["start_record_path"] == start_path.as_posix()
    assert void["start_record_raw_sha256"] == hashlib.sha256(
        start_path.read_bytes()).hexdigest()
    assert void["usage_ledger_path"] == ledger_path.as_posix()
    assert void["usage_ledger_raw_sha256"] == hashlib.sha256(
        ledger_path.read_bytes()).hexdigest()
    ledger_state_path = phase3_main_live.api_client.usage_ledger_state_path(
        ledger_path)
    assert void["usage_ledger_state_path"] == ledger_state_path.resolve().as_posix()
    assert void["usage_ledger_state_raw_sha256"] == hashlib.sha256(
        ledger_state_path.read_bytes()).hexdigest()

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="already exists"):
        phase3_main_live.record_environmental_interruption(
            prepared.manifest_path,
            reason_code="network_outage",
        )
    prepared.identity.paths.completion.write_text("{}\n", encoding="utf-8")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="completed"):
        phase3_main_live.record_environmental_interruption(
            prepared.manifest_path,
            reason_code="network_outage",
        )


def test_environmental_interruption_rejects_replacement_usage_ledger(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared.manifest_path.write_text(
        json.dumps(prepared.manifest) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *args, **kwargs: prepared.manifest_validation,
    )
    _, ledger_path = _seed_started_identity(prepared)
    state_path = phase3_main_live.api_client.usage_ledger_state_path(ledger_path)
    ledger_path.unlink()
    state_path.unlink()
    phase3_main_live.api_client.prepare_usage_ledger(
        ledger_path, allow_create=True)

    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="does not validate against its start binding",
    ):
        phase3_main_live.record_environmental_interruption(
            prepared.manifest_path,
            reason_code="power_loss",
        )
    assert not phase3_main_live._identity_void_path(prepared.identity).exists()


def test_environmental_successor_requires_exact_void_start_and_billing_ledger(
    tmp_path, inventory, monkeypatch,
):
    predecessor = _prepared(tmp_path / "predecessor", inventory)
    predecessor.manifest_path.write_text(
        json.dumps(predecessor.manifest) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *args, **kwargs: predecessor.manifest_validation,
    )
    start_path, ledger_path = _seed_started_identity(predecessor)
    state_path = phase3_main_live.api_client.usage_ledger_state_path(ledger_path)
    genesis_ledger_raw = ledger_path.read_bytes()
    genesis_state_raw = state_path.read_bytes()
    genesis_snapshot = phase3_main_live.api_client.load_chained_usage_ledger(
        ledger_path)
    lost_reservation = {
        "status": "reserved",
        "attempt_id": "simulated-paid-call",
        "model": "fixture-model",
        "kind": "judge",
        "seed": 1,
        "attempt": 1,
        "prompt_tokens": None,
        "completion_tokens": None,
        "reserved_prompt_tokens": 1,
        "reserved_completion_tokens": 1,
        "estimated_tokens": 2,
        "cost_usd": 0.01,
        "metadata": {},
        "ts": datetime.now(timezone.utc).isoformat(),
        "ledger_id": genesis_snapshot.identity["ledger_id"],
        "sequence": 1,
        "prev_event_hash": genesis_snapshot.last_event_hash,
    }
    lost_reservation["event_hash"] = (
        phase3_main_live.api_client._usage_event_hash(lost_reservation))  # noqa: SLF001
    with ledger_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(lost_reservation, sort_keys=True) + "\n")
    state_path.write_text(
        json.dumps(
            phase3_main_live.api_client._usage_state_payload(  # noqa: SLF001
                genesis_snapshot.identity, 1, lost_reservation["event_hash"]),
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    assert phase3_main_live.api_client.load_chained_usage_ledger(
        ledger_path).last_sequence == 1
    ledger_path.write_bytes(genesis_ledger_raw)
    state_path.write_bytes(genesis_state_raw)
    phase3_main_live.record_environmental_interruption(
        predecessor.manifest_path,
        reason_code="provider_outage",
    )
    void_path = phase3_main_live._identity_void_path(predecessor.identity)
    void = json.loads(void_path.read_text(encoding="utf-8"))
    start = json.loads(start_path.read_text(encoding="utf-8"))
    start_time = datetime.fromisoformat(start["recorded_at_utc"])
    void_time = datetime.fromisoformat(void["recorded_at_utc"])
    predecessor_binding = {
        "run_id": predecessor.identity.run_id,
        "manifest_canonical_sha256": predecessor.identity.manifest_sha256,
        "manifest_identity_sha256": predecessor.manifest["manifest_identity_sha256"],
        "authorization_id": start["authorization_id"],
        "authorization_canonical_sha256": start["authorization_canonical_sha256"],
        "authorization_raw_sha256": start["authorization_raw_sha256"],
        "authorization_signature_raw_sha256": (
            start["authorization_signature_raw_sha256"]),
        "artifact_root": predecessor.identity.artifact_root,
        "void_record_path": void_path,
        "void_record_raw_sha256": hashlib.sha256(void_path.read_bytes()).hexdigest(),
        "usage_ledger_path": ledger_path,
        "usage_ledger_raw_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
    }
    successor_validation = {
        "recorded_at_utc": void_time + timedelta(seconds=1),
        "restart": {
            "mode": phase3_main_live.phase3_main_manifest.RESTART_MODE_ENVIRONMENTAL_SUCCESSOR,
            "predecessor": predecessor_binding,
        },
    }
    billing_record = {
        "ledgers": [{
            "path": ledger_path.as_posix(),
            "raw_sha256": predecessor_binding["usage_ledger_raw_sha256"],
        }],
    }
    billing_validation = {
        "billing_scope": {
            "account_identity_sha256": "a" * 64,
            "window_start_utc": (start_time - timedelta(seconds=1)).isoformat(),
            "window_end_utc": (void_time + timedelta(seconds=1)).isoformat(),
        },
        "provider_settlement": {
            "status": "provider_authenticated_finalized",
            "account_identity_sha256": "a" * 64,
            "finalized_through_utc": (
                void_time + timedelta(milliseconds=500)).isoformat(),
        },
    }
    phase3_main_live._validate_environmental_restart(
        manifest_validation=successor_validation,
        billing_record=billing_record,
        billing_validation=billing_validation,
        project_root=tmp_path,
    )
    out_of_window_validation = {
        "billing_scope": {
            "account_identity_sha256": "a" * 64,
            "window_start_utc": (void_time + timedelta(seconds=1)).isoformat(),
            "window_end_utc": (void_time + timedelta(seconds=2)).isoformat(),
        },
        "provider_settlement": dict(billing_validation["provider_settlement"]),
    }
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="billing window does not cover",
    ):
        phase3_main_live._validate_environmental_restart(
            manifest_validation=successor_validation,
            billing_record=billing_record,
            billing_validation=out_of_window_validation,
            project_root=tmp_path,
        )
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="settlement finality",
    ):
        phase3_main_live._validate_environmental_restart(
            manifest_validation=successor_validation,
            billing_record=billing_record,
            billing_validation={"billing_scope": billing_validation["billing_scope"]},
            project_root=tmp_path,
        )
    equality_settlement_validation = {
        "billing_scope": dict(billing_validation["billing_scope"]),
        "provider_settlement": {
            **billing_validation["provider_settlement"],
            "finalized_through_utc": billing_validation[
                "billing_scope"]["window_end_utc"],
        },
    }
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError,
        match="post-void billing window",
    ):
        phase3_main_live._validate_environmental_restart(
            manifest_validation=successor_validation,
            billing_record=billing_record,
            billing_validation=equality_settlement_validation,
            project_root=tmp_path,
        )

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="does not cover"):
        phase3_main_live._validate_environmental_restart(
            manifest_validation=successor_validation,
            billing_record={"ledgers": []},
            billing_validation=billing_validation,
            project_root=tmp_path,
        )
    changed = {
        **successor_validation,
        "restart": {
            **successor_validation["restart"],
            "predecessor": {
                **predecessor_binding,
                "authorization_canonical_sha256": "0" * 64,
            },
        },
    }
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="semantics drifted"):
        phase3_main_live._validate_environmental_restart(
            manifest_validation=changed,
            billing_record=billing_record,
            billing_validation=billing_validation,
            project_root=tmp_path,
        )
    changed = {
        **successor_validation,
        "recorded_at_utc": void_time - timedelta(microseconds=1),
    }
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="timestamps"):
        phase3_main_live._validate_environmental_restart(
            manifest_validation=changed,
            billing_record=billing_record,
            billing_validation=billing_validation,
            project_root=tmp_path,
        )

    original_void_raw = void_path.read_bytes()
    tampered_void = dict(void)
    tampered_void["run_id"] = "phase3-main-tampered"
    void_path.write_text(
        json.dumps(tampered_void, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    changed_predecessor = {
        **predecessor_binding,
        "void_record_raw_sha256": hashlib.sha256(void_path.read_bytes()).hexdigest(),
    }
    changed = {
        **successor_validation,
        "restart": {
            **successor_validation["restart"],
            "predecessor": changed_predecessor,
        },
    }
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="binding drifted"):
        phase3_main_live._validate_environmental_restart(
            manifest_validation=changed,
            billing_record=billing_record,
            billing_validation=billing_validation,
            project_root=tmp_path,
        )
    void_path.write_bytes(original_void_raw)


def test_environmental_interruption_record_respects_main_run_lease(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    prepared.manifest_path.write_text(
        json.dumps(prepared.manifest) + "\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_manifest",
        lambda *args, **kwargs: prepared.manifest_validation,
    )
    _seed_started_identity(prepared)
    with phase3_main_live.phase3_v3_live.RunLease(prepared.identity.paths.lease):
        with pytest.raises(phase3_main_live.Phase3MainLiveError, match="run lease"):
            phase3_main_live.record_environmental_interruption(
                prepared.manifest_path,
                reason_code="host_failure",
            )


def test_completion_rejects_persistently_voided_identity(tmp_path, inventory):
    prepared = _prepared(tmp_path, inventory)
    void_path = phase3_main_live._identity_void_path(prepared.identity)
    void_path.parent.mkdir(parents=True)
    void_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="cannot be completed"):
        phase3_main_live._require_identity_not_voided(prepared.identity)


def test_uncertain_ceiling_halt_is_identity_fatal_in_the_production_loop(
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
        phase3_main_live, "_construct_verified_runtime_provider_sdk", lambda _prepared: object())
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
            **_authenticated_billing_fields(),
            "run_id": prepared.manifest["run_id"],
            "disposition": "closed",
            "closed": True,
            "accounted_spend_usd": "10.25",
            "provider_delta_usd": "10.25",
            "uncertain_spend_usd": "0",
            "unresolved_attempt_ids": (),
        },
    )

    def fake_preseed(**kwargs):
        assert not phase3_main_live._identity_start_path(prepared.identity).exists()
        Path(kwargs["target_store_path"]).touch()
        return {"main_bundle_count": 492, "written": 492, "skipped": 0}

    def fake_factory(_prepared, _snapshot, *, sdk_client):
        assert sdk_client is not None
        factory_calls.append(True)
        assert not phase3_main_live._identity_start_path(prepared.identity).exists()
        return object()

    def fake_run_canary(**kwargs):
        loop_calls.append(kwargs)
        assert phase3_main_live._identity_start_path(prepared.identity).is_file()
        assert kwargs["fatal_unknown_charge"] is False
        return SimpleNamespace(
            completed=0,
            skipped=0,
            pending_payloads=[],
            halted_reason="UncertainCeilingHalt",
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

    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="frozen ceiling"):
        phase3_main_live.run_main("manifest", "authorization")
    assert len(factory_calls) == 1
    assert len(loop_calls) == 1
    start_path = phase3_main_live._identity_start_path(prepared.identity)
    assert start_path.is_file()
    assert not prepared.identity.paths.completion.exists()

    shutil.rmtree(prepared.identity.artifact_root)
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="single-shot state"):
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


def test_cli_environmental_interruption_mode_never_reaches_run_main(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        phase3_main_live,
        "record_environmental_interruption",
        lambda manifest, *, reason_code: calls.append((manifest, reason_code)) or {
            "status": "voided_environmental_interruption",
            "run_id": "phase3-main-interrupted",
            "reason_code": reason_code,
            "record_path": "C:/registry/interrupted.voided.json",
            "replacement_authorized": False,
        },
    )
    monkeypatch.setattr(
        phase3_main_live,
        "run_main",
        lambda *args, **kwargs: pytest.fail("interruption mode reached run_main"),
    )
    rc = phase3_main_live.main([
        "--manifest", "manifest.json",
        "--record-environmental-interruption",
        "--reason-code", "power_loss",
    ])
    captured = capsys.readouterr()
    assert rc == 0 and captured.err == ""
    assert calls == [("manifest.json", "power_loss")]
    assert json.loads(captured.out)["replacement_authorized"] is False


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


# --- amendment 14: bounded uncertain-spend tolerance ---


def _production_loop(tmp_path, inventory, monkeypatch, fake_run_canary, *, on_finalize=None):
    """Stand up run_main with every external boundary faked except the pass loop."""
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
    sleeps: list[int] = []
    monkeypatch.setattr(phase3_main_live, "_ABANDONED_RATE_SLEEP", sleeps.append)
    monkeypatch.setattr(
        phase3_main_live, "load_prepared_main", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(
        phase3_main_live, "_require_production_execution_unblocked", lambda: None)
    monkeypatch.setattr(
        phase3_main_live, "_construct_verified_runtime_provider_sdk", lambda _prepared: object())
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
            **_authenticated_billing_fields(),
            "run_id": prepared.manifest["run_id"],
            "disposition": "closed",
            "closed": True,
            "accounted_spend_usd": "10.25",
            "provider_delta_usd": "10.25",
            "uncertain_spend_usd": "0",
            "unresolved_attempt_ids": (),
        },
    )

    def fake_preseed(**kwargs):
        Path(kwargs["target_store_path"]).touch()
        return {"main_bundle_count": 492, "written": 492, "skipped": 0}

    monkeypatch.setattr(phase3_preseed_transcripts, "preseed_main", fake_preseed)
    monkeypatch.setattr(
        phase3_main_live, "_construct_provider_client",
        lambda _prepared, _snapshot, *, sdk_client: SimpleNamespace(
            resolved_unknown_charges=[]))
    monkeypatch.setattr(
        phase3_main_live.phase3_runner,
        "resolve_main_cells",
        lambda *args, **kwargs: [SimpleNamespace(cell_key="main-judgment-cell")],
    )
    monkeypatch.setattr(phase3_main_live, "run_canary", fake_run_canary)
    monkeypatch.setattr(
        phase3_main_live, "_review_wave_same_process",
        lambda *args, **kwargs: pytest.fail("test loop reached reviewer dispatch"),
    )
    monkeypatch.setattr(
        phase3_main_live, "_finalize_main",
        on_finalize or (lambda *args, **kwargs: pytest.fail("test loop reached finalization")),
    )
    return prepared, sleeps


def _run_log_events(prepared):
    path = prepared.identity.paths.run_log
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _abandoned_outcome(**overrides):
    base = dict(
        completed=0, skipped=0, attempted=10, abandoned=9, pending_payloads=[],
        halted_reason="abandoned_cell_rate", halted_cell_key="main-judgment-cell",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_start_event_records_the_uncertain_spend_policy_and_pass_allowance(
    tmp_path, inventory, monkeypatch,
):
    calls = []

    def fake_run_canary(**kwargs):
        calls.append(kwargs)
        assert kwargs["fatal_unknown_charge"] is False
        return SimpleNamespace(
            completed=0, skipped=0, attempted=0, abandoned=0, pending_payloads=[],
            halted_reason="GenerationForbiddenError", halted_cell_key="cell")

    prepared, sleeps = _production_loop(tmp_path, inventory, monkeypatch, fake_run_canary)
    with pytest.raises(phase3_main_live.GenerationForbiddenError):
        phase3_main_live.run_main("manifest", "authorization")
    started = next(e for e in _run_log_events(prepared) if e["event"] == "formal_main_started")
    policy = prepared.uncertain_spend_policy_validation
    assert started["uncertain_spend_policy_id"] == policy["policy_id"]
    assert started["uncertain_spend_policy_raw_sha256"] == policy["policy_raw_sha256"]
    assert started["run_uncertain_ceiling_usd"] == 100.0
    assert started["unknown_charge_pass_allowance"] == 50
    assert started["maximum_driver_passes"] == 984 + 20 + 2 + 50
    assert started["abandoned_rate_cooldown_seconds"] == 1800
    assert started["abandoned_rate_consecutive_pass_allowance"] == 8
    assert sleeps == []


def test_abandoned_rate_passes_cool_down_then_halt_past_the_allowance(
    tmp_path, inventory, monkeypatch,
):
    calls = []

    def fake_run_canary(**kwargs):
        calls.append(kwargs)
        return _abandoned_outcome()

    prepared, sleeps = _production_loop(tmp_path, inventory, monkeypatch, fake_run_canary)
    with pytest.raises(
        phase3_main_live.Phase3MainLiveError, match="8-pass allowance",
    ):
        phase3_main_live.run_main("manifest", "authorization")
    assert len(calls) == 9
    assert sleeps == [1800] * 8
    events = [e for e in _run_log_events(prepared) if e["event"] == "abandoned_cell_rate_pass"]
    assert [e["consecutive_abandoned_rate_passes"] for e in events] == list(range(1, 10))
    assert all(e["attempted_this_pass"] == 10 and e["abandoned_this_pass"] == 9 for e in events)
    assert events[-1]["reason"] == "abandoned_cell_rate"
    # The identity is single-shot: nothing resumes after the halt.
    assert phase3_main_live._identity_start_path(prepared.identity).is_file()
    assert not prepared.identity.paths.completion.exists()


def test_abandoned_rate_counter_resets_after_a_productive_pass(
    tmp_path, inventory, monkeypatch,
):
    calls = []
    target = phase3_main_runner.EXPECTED_MAIN_CELL_COUNT

    def fake_run_canary(**kwargs):
        calls.append(kwargs)
        if len(calls) in (1, 2, 4):
            return _abandoned_outcome()
        if len(calls) == 3:
            return SimpleNamespace(
                completed=1, skipped=0, attempted=1, abandoned=0, pending_payloads=[],
                halted_reason=None, halted_cell_key=None)
        return SimpleNamespace(
            completed=5, skipped=0, attempted=5, abandoned=0, pending_payloads=[],
            halted_reason=None, halted_cell_key=None)

    observed = {"count": 0}

    def fake_keys(_path):
        observed["count"] += 1
        return set(range(target)) if observed["count"] >= 2 else {"one"}

    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization, "load_result_cell_keys", fake_keys)
    prepared, sleeps = _production_loop(
        tmp_path, inventory, monkeypatch, fake_run_canary,
        on_finalize=lambda *args, **kwargs: {"finalized": True})
    assert phase3_main_live.run_main("manifest", "authorization") == {"finalized": True}
    assert len(calls) == 5
    assert sleeps == [1800, 1800, 1800]
    events = [e for e in _run_log_events(prepared) if e["event"] == "abandoned_cell_rate_pass"]
    assert [e["consecutive_abandoned_rate_passes"] for e in events] == [1, 2, 1]


def test_no_progress_with_abandonment_cools_down_instead_of_halting(
    tmp_path, inventory, monkeypatch,
):
    calls = []

    def fake_run_canary(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return SimpleNamespace(
                completed=0, skipped=0, attempted=3, abandoned=3, pending_payloads=[],
                halted_reason=None, halted_cell_key=None)
        return SimpleNamespace(
            completed=0, skipped=0, attempted=3, abandoned=0, pending_payloads=[],
            halted_reason=None, halted_cell_key=None)

    monkeypatch.setattr(
        phase3_main_live.phase3_main_finalization, "load_result_cell_keys",
        lambda _path: {"one"})
    prepared, sleeps = _production_loop(tmp_path, inventory, monkeypatch, fake_run_canary)
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="made no progress"):
        phase3_main_live.run_main("manifest", "authorization")
    assert len(calls) == 2
    assert sleeps == [1800]
    events = [e for e in _run_log_events(prepared) if e["event"] == "abandoned_cell_rate_pass"]
    assert [e["reason"] for e in events] == ["no_progress_with_abandonment"]
    pass_events = [e for e in _run_log_events(prepared) if e["event"] == "formal_main_pass_complete"]
    assert pass_events[0]["abandoned_this_pass"] == 3
    assert pass_events[0]["attempted_this_pass"] == 3


def test_private_factory_binds_the_policy_ceiling_to_the_client(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    captured = {}

    monkeypatch.setattr(
        phase3_main_live.api_client, "RejudgeClient",
        lambda **kwargs: captured.update(kwargs) or object())
    monkeypatch.setattr(
        phase3_main_live, "RoleLimitResolvingClient", lambda inner, limits: "resolved")
    usage_path = prepared.identity.paths.usage_ledger
    snapshot = phase3_main_live.api_client.UsageLedgerSnapshot(
        path=usage_path,
        state_path=phase3_main_live.api_client.usage_ledger_state_path(usage_path),
        identity={},
        summary={"events": 0},
        last_sequence=0,
        last_event_hash="genesis",
    )
    assert phase3_main_live._construct_provider_client(
        prepared, snapshot, sdk_client=object()) == "resolved"
    assert captured["run_uncertain_ceiling_usd"] == 100.0
    assert captured["initial_run_uncertain_spend_usd"] == 0.0
    assert captured["halt_on_unknown_charge"] is True

    used = replace(snapshot, summary={"events": 3})
    with pytest.raises(phase3_main_live.Phase3MainLiveError, match="fresh main ledger"):
        phase3_main_live._construct_provider_client(prepared, used, sdk_client=object())
