"""Production-boundary tests for the Phase 3 main driver."""
from __future__ import annotations

import inspect
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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
        "protocol", "main_transcript_bundle", "transcript_verification",
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


def test_paid_call_revalidates_authorization_and_price_before_dispatch(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
    events = []

    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_authenticated_authorization",
        lambda _prepared: events.append("authorization"),
    )
    monkeypatch.setattr(
        phase3_main_live,
        "_revalidate_price_snapshot",
        lambda _prepared: events.append("price"),
    )

    class Inner:
        dry_run = False

        def complete(self, *args, **kwargs):
            events.append("provider")
            return "response"

    client = phase3_main_live._AuthorizationDeadlineClient(prepared, Inner())
    assert client.complete(messages=[], model="judge") == "response"
    assert events == ["authorization", "price", "provider"]


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
        phase3_main_live, "_revalidate_authenticated_authorization", revalidate)
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
        phase3_main_live, "_revalidate_authenticated_authorization", revalidate)
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
    monkeypatch.setattr(
        phase3_main_live.phase3_main_manifest,
        "validate_main_authorization",
        lambda *args, **kwargs: events.append("completion-window") or {},
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
        "completion-window",
        "write",
    ]
    assert captured["recorded_at_utc"] != "provisional"


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


def test_reviewer_wave_uses_the_capacity_bound_cli_path(
    tmp_path, inventory, monkeypatch,
):
    prepared = _prepared(tmp_path, inventory)
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
    capacity_plan = {
        "reviewer_configuration": {
            "reviewer_cli_resolved_path": "C:/measured/codex.cmd",
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
        lambda _prepared: (capacity_plan, {"validation": "pass"}),
    )
    monkeypatch.setattr(
        phase3_main_live,
        "export_reviewer_worklist",
        lambda *args, **kwargs: worklist,
    )
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
        out_path.write_text(
            json.dumps({
                "payload_sha256": payload_sha,
                "prompt_sha256": prompt_sha,
                "raw_output": "LABEL: ACCEPT\nCLAUSE: Allowed\nRATIONALE: Valid.",
                "tool_uses": 0,
            }) + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(phase3_main_live.subprocess, "run", fake_run)
    monkeypatch.setattr(
        phase3_main_live,
        "commit_decisions_into",
        lambda *args, **kwargs: {"committed": 1},
    )

    phase3_main_live._review_wave_same_process(
        prepared, [{"payload_sha256": payload_sha}], wave=1)
    command = captured["command"]
    assert command[command.index("--codex") + 1] == "C:/measured/codex.cmd"
    assert command[command.index("--not-after-utc") + 1] == (
        prepared.authorization["valid_until_utc"])


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
        assert inner is raw
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
        lambda _prepared, _client: {"status": "test-complete"})

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
