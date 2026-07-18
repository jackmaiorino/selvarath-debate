"""Tests for rejudge.phase2_preflight_runner: the executable capability_preflight orchestrator.

Every client_factory used here returns a stub -- never the real Together SDK (see
test_module_purity_no_sdk_import_at_module_load). Manifest-building helpers mirror
tests/test_phase2_execution.py's own conventions (build_execution_identity /
derive_execution_call_key), but are rebuilt here so every artifact -- including this module's
OWN cost_forecast/storage_policy content requirements -- can be freely varied per test and
validated against a private scratch project_root rather than the real repo root.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from rejudge import phase2_capability_corpus as capability_corpus
from rejudge import phase2_execution as pe
from rejudge import phase2_plan
from rejudge import phase2_preflight_runner as runner_mod
from rejudge import phase2_prompt_bundle as prompt_bundle
from rejudge import phase2_provider_price_snapshot as price_snapshot
from rejudge import phase2_role_limits as role_limits
from rejudge import run_manifest


ROOT = Path(__file__).resolve().parents[1]

REL_PROTOCOL = "rejudge/phase2_protocol.json"
REL_COMBINED = "rejudge/phase2_resolvability_ai_review.json"
REL_AMENDMENT = "rejudge/phase2_resolvability_review_amendment_2026-07-16.json"
REL_BUNDLE = "rejudge/phase2_prompt_bundle.json"
REL_APPROVAL = "rejudge/phase2_prompt_bundle_approval_2026-07-18.json"
REL_SNAPSHOT = "rejudge/phase2_provider_price_snapshot_2026-07-18.json"
REL_UV_LOCK = "uv.lock"
REL_APPROVAL_BASIS = "docs/phase2-decision-proposal.md"
REL_ROLE_LIMITS_V1 = "rejudge/phase2_role_limits_2026-07-18.json"
REL_ROLE_LIMITS_V2 = "rejudge/phase2_role_limits_v2_2026-07-18.json"
REL_LEDGER = "rejudge/output/phase2_capability_preflight_ledger.jsonl"

_TRACKED_DATA_FILES = (
    REL_PROTOCOL,
    "rejudge/output/calibration_models.json",
    "rejudge/calibration_questions_2026-07-14.json",
    "rejudge/oracle_shortcut_audit_2026-07-12.json",
    "rejudge/calibration_recovery_gemma_2026-07-15.json",
    "rejudge/phase2_resolvability_review.json",
    REL_COMBINED,
    "rejudge/phase2_resolvability_ai_review_carath_norn.json",
    "rejudge/phase2_resolvability_ai_review_selvarath.json",
    "rejudge/phase2_resolvability_ai_review_vethun_sarak.json",
    REL_AMENDMENT,
    REL_BUNDLE,
    REL_APPROVAL,
    REL_SNAPSHOT,
    REL_ROLE_LIMITS_V1,
    REL_ROLE_LIMITS_V2,
    "questions/carath_norn_questions.json",
    "questions/selvarath_questions.json",
    "questions/vethun_sarak_questions.json",
    REL_UV_LOCK,
    REL_APPROVAL_BASIS,
)
_WORLD_SPECS = ("carath_norn", "selvarath", "vethun_sarak")


def _copy_project_root_sources(destination: Path) -> None:
    """Populate a scratch project_root with byte-identical copies of every tracked source
    this module's manifest-building and corpus-rendering paths touch."""
    for relative in _TRACKED_DATA_FILES:
        source = ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    for name in _WORLD_SPECS:
        source = ROOT / "world_specs" / f"{name}.txt"
        target = destination / "world_specs" / f"{name}.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def _baseline(project_root: Path) -> dict:
    protocol = phase2_plan.load_protocol(project_root / REL_PROTOCOL)
    main_ids = phase2_plan.load_main_question_ids(protocol, project_root)
    cells = phase2_plan.enumerate_cells(protocol, main_ids)
    capability_cells = [c for c in cells if c["kind"] == "capability_qa"]
    planning_keys = sorted(c["cell_key"] for c in capability_cells)
    cells_by_key = {c["cell_key"]: c for c in capability_cells}

    combined = json.loads((project_root / REL_COMBINED).read_text(encoding="utf-8"))
    amendment = json.loads((project_root / REL_AMENDMENT).read_text(encoding="utf-8"))
    bundle, _bundle_protocol = prompt_bundle.load_and_validate(
        project_root / REL_BUNDLE, project_root / REL_PROTOCOL)
    approval = json.loads((project_root / REL_APPROVAL).read_text(encoding="utf-8"))
    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(
        project_root / REL_SNAPSHOT, project_root / REL_PROTOCOL)
    uv_lock_sha256 = hashlib.sha256((project_root / REL_UV_LOCK).read_bytes()).hexdigest()
    approval_basis_sha256 = hashlib.sha256(
        (project_root / REL_APPROVAL_BASIS).read_bytes()).hexdigest()

    entries_without_key = []
    for index, key in enumerate(planning_keys):
        cell = cells_by_key[key]
        side = "A" if cell["replicate_index"] == 0 else "B"
        entries_without_key.append({
            "planning_cell_key": key, "call_role": "capability_qa", "call_index": index,
            "model": cell["judge_model"], "seed": index, "side": side,
            "request_fields_sha256": "a" * 64,
        })
    return {
        "protocol": protocol, "planning_keys": planning_keys,
        "entries_without_key": entries_without_key, "combined": combined,
        "amendment": amendment, "bundle": bundle, "approval": approval,
        "snapshot": snapshot, "uv_lock_sha256": uv_lock_sha256,
        "approval_basis_sha256": approval_basis_sha256,
    }


def _write_artifacts(
    project_root: Path, tmp_path_factory, corpus_entries: list, *,
    corpus_sha_override: str | None = None, corpus_count_override: int | None = None,
    archive_destination: str | None = None,
) -> dict:
    directory = tmp_path_factory.mktemp("preflight_artifacts")
    role_limits_and_request_settings = directory / "role_limits_and_request_settings.json"
    role_limits_and_request_settings.write_text(
        (project_root / REL_ROLE_LIMITS_V2).read_text(encoding="utf-8"), encoding="utf-8")
    gemma_waiver = directory / "gemma_recovery_waiver.json"
    gemma_waiver.write_text(json.dumps({"waiver": "placeholder"}), encoding="utf-8")

    corpus_sha = corpus_sha_override or capability_corpus.corpus_canonical_sha256(corpus_entries)
    corpus_count = (
        len(corpus_entries) if corpus_count_override is None else corpus_count_override)
    artifacts_dir = project_root / "rejudge/output/_test_phase2_preflight_runner_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    cost_forecast_path = artifacts_dir / "cost_forecast.json"
    cost_forecast_path.write_text(json.dumps({
        "bindings": {"rendered_corpus": {"canonical_sha256": corpus_sha, "entry_count": corpus_count}},
    }), encoding="utf-8")

    if archive_destination is None:
        archive_destination = str(tmp_path_factory.mktemp("preflight_archive_dest"))
    storage_policy_path = artifacts_dir / "storage_policy.json"
    storage_policy_path.write_text(
        json.dumps({"archive_destination": archive_destination}), encoding="utf-8")

    reconciliation_path = artifacts_dir / "provider_reconciliation_evidence.json"
    reconciliation_path.write_text(json.dumps({"evidence": "placeholder"}), encoding="utf-8")

    return {
        "role_limits_and_request_settings": role_limits_and_request_settings,
        "gemma_waiver": gemma_waiver,
        "cost_forecast": cost_forecast_path,
        "storage_policy": storage_policy_path,
        "provider_reconciliation_evidence": reconciliation_path,
        "archive_destination": archive_destination,
    }


def _binding(project_root: Path, path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sha = pe.canonical_sha256(payload)
    try:
        path_value = str(path.relative_to(project_root).as_posix())
    except ValueError:
        path_value = str(path)
    return {"path": path_value, "sha256": sha}


def _shared_fields(project_root, baseline, artifacts, *, stage, stage_cap, cumulative_cap):
    protocol = baseline["protocol"]
    schema_version = protocol["materialization_requirements"]["transition_model"][
        "manifest_schema_version"]
    execution_semantics = protocol["decisions"]["execution_semantics"]
    return {
        "schema_version": schema_version,
        "stage": stage,
        "protocol_canonical_sha256": pe.canonical_sha256(protocol),
        "a1_amendment_canonical_sha256": pe.canonical_sha256(baseline["amendment"]),
        "combined_ai_audit_canonical_sha256": pe.canonical_sha256(baseline["combined"]),
        "question_bank_bundle_sha256": protocol["source_bindings"]["question_bank_bundle_sha256"],
        "prompt_bundle_canonical_sha256": pe.canonical_sha256(baseline["bundle"]),
        "prompt_bundle_declared_status": baseline["bundle"]["status"],
        "prompt_bundle_approval_tracked_path": REL_APPROVAL,
        "prompt_bundle_approval_canonical_sha256": pe.canonical_sha256(baseline["approval"]),
        "role_limits_and_request_settings_artifact": _binding(
            project_root, artifacts["role_limits_and_request_settings"]),
        "provider_price_snapshot_canonical_sha256": pe.canonical_sha256(baseline["snapshot"]),
        "uv_lock_sha256": baseline["uv_lock_sha256"],
        "seed_policy": execution_semantics["seed_policy"],
        "side_assignment_policy": execution_semantics["side_assignment_policy"],
        "satisfied_prerequisites": {
            "gemma_recovery_or_waiver": _binding(project_root, artifacts["gemma_waiver"]),
        },
        "ledger": {"path": REL_LEDGER, "ledger_identity": "phase2-project-wide-ledger-v1"},
        "stage_cap_usd": stage_cap,
        "cumulative_cap_usd": cumulative_cap,
        "cost_forecast": _binding(project_root, artifacts["cost_forecast"]),
        "storage_policy": _binding(project_root, artifacts["storage_policy"]),
        "provider_reconciliation_evidence": _binding(
            project_root, artifacts["provider_reconciliation_evidence"]),
    }


def _build_manifest(
    project_root, baseline, artifacts, *, stage="capability_preflight",
    stage_cap=15.0, cumulative_cap=1500.0,
):
    shared = _shared_fields(
        project_root, baseline, artifacts, stage=stage, stage_cap=stage_cap,
        cumulative_cap=cumulative_cap)
    identity = pe.build_execution_identity(
        schema_version=shared["schema_version"], stage=shared["stage"],
        protocol_canonical_sha256=shared["protocol_canonical_sha256"],
        a1_amendment_canonical_sha256=shared["a1_amendment_canonical_sha256"],
        combined_ai_audit_canonical_sha256=shared["combined_ai_audit_canonical_sha256"],
        question_bank_bundle_sha256=shared["question_bank_bundle_sha256"],
        prompt_bundle_canonical_sha256=shared["prompt_bundle_canonical_sha256"],
        prompt_bundle_declared_status=shared["prompt_bundle_declared_status"],
        prompt_bundle_approval_artifact={
            "tracked_path": shared["prompt_bundle_approval_tracked_path"],
            "sha256": shared["prompt_bundle_approval_canonical_sha256"],
        },
        role_limits_and_request_settings_artifact=shared[
            "role_limits_and_request_settings_artifact"],
        provider_price_snapshot_canonical_sha256=shared[
            "provider_price_snapshot_canonical_sha256"],
        uv_lock_sha256=shared["uv_lock_sha256"],
        seed_policy=shared["seed_policy"], side_assignment_policy=shared["side_assignment_policy"],
        satisfied_prerequisites=shared["satisfied_prerequisites"], ledger=shared["ledger"],
        planning_cell_keys=baseline["planning_keys"],
        provider_call_inventory_entries=baseline["entries_without_key"],
        stage_cap_usd=shared["stage_cap_usd"], cumulative_cap_usd=shared["cumulative_cap_usd"],
        cost_forecast=shared["cost_forecast"], storage_policy=shared["storage_policy"],
        provider_reconciliation_evidence=shared["provider_reconciliation_evidence"],
    )
    identity_sha256 = pe.derive_execution_identity_sha256(identity)
    entries = [
        {**entry, "execution_call_key": pe.derive_execution_call_key(
            identity_sha256, planning_cell_key=entry["planning_cell_key"],
            call_role=entry["call_role"], call_index=entry["call_index"])}
        for entry in baseline["entries_without_key"]
    ]
    manifest = {
        **shared, "planning_cell_keys": list(baseline["planning_keys"]),
        "provider_call_inventory": entries,
    }
    return manifest, identity_sha256


def _build_full_manifest(
    project_root: Path, tmp_path_factory, *, stage_cap: float = 15.0,
    cumulative_cap: float = 1500.0, corpus_sha_override: str | None = None,
    corpus_count_override: int | None = None, archive_destination: str | None = None,
):
    baseline = _baseline(project_root)
    corpus_entries = capability_corpus.render_capability_corpus(
        baseline["bundle"], baseline["protocol"], project_root)
    artifacts = _write_artifacts(
        project_root, tmp_path_factory, corpus_entries,
        corpus_sha_override=corpus_sha_override, corpus_count_override=corpus_count_override,
        archive_destination=archive_destination,
    )
    manifest, identity_sha256 = _build_manifest(
        project_root, baseline, artifacts, stage_cap=stage_cap, cumulative_cap=cumulative_cap)
    return manifest, identity_sha256, artifacts


def _write_manifest(project_root: Path, manifest: dict) -> Path:
    manifest_path = project_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _validated_for(manifest_path: Path, project_root: Path) -> pe.ValidatedExecutionManifest:
    return pe.validate_execution_manifest(
        pe.load_execution_manifest(manifest_path), project_root=project_root)


def _authorization_for(
    project_root: Path, identity_sha256: str, *, stage_cap: float, cumulative_cap: float,
) -> dict:
    """Build a matching, resolvable authorization record for a manifest built by
    _build_full_manifest against the same project_root/stage_cap/cumulative_cap."""
    baseline = _baseline(project_root)
    return {
        "execution_identity_sha256": identity_sha256,
        "stage": "capability_preflight",
        "stage_cap_usd": stage_cap,
        "cumulative_cap_usd": cumulative_cap,
        "approver": "test-approver",
        "approved_at_utc": "2026-07-18T00:00:00Z",
        "approval_basis_tracked_path": REL_APPROVAL_BASIS,
        "approval_basis_sha256": baseline["approval_basis_sha256"],
    }


def _write_authorization(project_root: Path, authorization: dict) -> Path:
    path = project_root / "authorization.json"
    path.write_text(json.dumps(authorization), encoding="utf-8")
    return path


def _stub_audit_sequence(monkeypatch, *audits: "pe.ResumeAudit") -> None:
    """Monkeypatch phase2_execution.audit_resume (as seen by phase2_preflight_runner) to return
    each of ``audits`` in turn, once per call, ignoring its real arguments.

    Used only to directly isolate a handful of _run_locked branches that are correct-by-
    construction whenever audit_resume itself behaves correctly -- the resume audit and the
    completion gate are independent, redundant checks, so a handful of the completion gate's
    own raise sites are not reachable through any externally tamperable-but-audit-consistent
    input; see the tests that use this helper for exactly which ones and why.
    """
    remaining = list(audits)

    def _fake(*_args, **_kwargs):
        if not remaining:
            raise AssertionError("audit_resume stubbed sequence exhausted")
        return remaining.pop(0)

    monkeypatch.setattr(runner_mod.pe, "audit_resume", _fake)


def _poison_factory(_params):
    raise AssertionError(
        "client_factory must not be invoked when the run refuses before any provider call")


class _SimulatedCrash(Exception):
    """Raised by ScriptedClient to simulate a hard process crash mid-run."""


class ScriptedClient:
    """A fully test-controlled stub satisfying rejudge.phase2_preflight_runner.PreflightClient.

    Never imports or touches a provider SDK. Durably appends {reserved, success} lifecycle
    events (fsynced before complete() returns) so resume/completion auditing works exactly as
    it would against a real ledger. ``overrides`` maps an execution_call_key to a custom raw
    answer text (default: "ANSWER: A" for every call); ``fail_after``/``fail_with`` simulate a
    crash after N calls.
    """

    def __init__(
        self, usage_log_path, *, overrides: dict[str, str] | None = None,
        fail_after: int | None = None, fail_with: BaseException | None = None,
    ):
        self._usage_log_path = Path(usage_log_path)
        self._usage_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self._overrides = overrides or {}
        self._fail_after = fail_after
        self._fail_with = fail_with
        self.calls: list[dict] = []

    def _append(self, event: dict) -> None:
        payload = {"ts": "2026-07-18T00:00:00Z", **event}
        with self._usage_log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def complete(
        self, *, messages: Sequence[Mapping[str, str]], model: str, temperature: float,
        seed: int, max_tokens: int, request_metadata: Mapping[str, Any],
    ) -> "runner_mod.PreflightCallResult":
        self._counter += 1
        metadata: dict[str, str] = dict(request_metadata)
        self.calls.append({
            "messages": messages, "model": model, "temperature": temperature, "seed": seed,
            "max_tokens": max_tokens, "request_metadata": metadata,
        })
        if self._fail_after is not None and self._counter > self._fail_after:
            assert self._fail_with is not None
            raise self._fail_with

        attempt_id = f"scripted-{self._counter:06d}-{uuid.uuid4().hex}"
        self._append({
            "status": "reserved", "attempt_id": attempt_id, "model": model, "seed": seed,
            "metadata": metadata,
        })
        call_key = metadata.get("execution_call_key", "")
        raw_output = self._overrides.get(call_key, "ANSWER: A")
        response_metadata = {
            "request_fields_sha256": metadata.get("request_fields_sha256"),
            "returned_model_id": model, "response_id": attempt_id, "finish_reason": "stop",
            "system_fingerprint_if_present": None, "prompt_tokens": 0, "completion_tokens": 3,
            "reasoning_tokens_if_returned": None,
        }
        self._append({
            "status": "success", "attempt_id": attempt_id, "model": model, "seed": seed,
            "metadata": metadata, "response_metadata": response_metadata,
        })
        return runner_mod.PreflightCallResult(raw_output=raw_output, response_metadata=response_metadata)


class OrderCheckingClient(ScriptedClient):
    """A ScriptedClient that actively re-verifies persist-before-advance ordering on every call.

    Reads results_path's on-disk row count immediately before each complete() call and asserts
    it equals exactly the number of PRIOR successful calls -- catching any regression that
    buffers/batches result rows before flushing them, a failure mode a single round crash-
    boundary end-state row count (as in
    test_crash_and_resume_mid_run_completes_only_the_remaining_todo_cells, which crashes at a
    suspiciously round 500) could fail to distinguish from correct per-call persistence.
    """

    def __init__(self, usage_log_path, results_path, **kwargs):
        super().__init__(usage_log_path, **kwargs)
        self._results_path = Path(results_path)

    def complete(self, **kwargs):
        prior_rows = 0
        if self._results_path.exists():
            prior_rows = sum(
                1 for line in self._results_path.read_text(encoding="utf-8").splitlines()
                if line.strip())
        assert prior_rows == self._counter, (
            f"persist-before-advance violated before call {self._counter + 1}: "
            f"{prior_rows} durable row(s) on disk, expected exactly {self._counter}")
        return super().complete(**kwargs)


# --- fixtures ------------------------------------------------------------------------------------


@pytest.fixture
def dry_run_manifest(tmp_path_factory):
    """A scratch project_root with real artifact copies and a valid, unauthorized manifest."""
    project_root = tmp_path_factory.mktemp("preflight_scratch_root")
    _copy_project_root_sources(project_root)
    manifest, identity_sha256, artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    return {
        "project_root": project_root, "manifest_path": manifest_path, "manifest": manifest,
        "identity_sha256": identity_sha256, "artifacts": artifacts,
    }


@pytest.fixture(scope="module")
def completed_dry_run(tmp_path_factory):
    """A full, successfully completed 1,060-cell dry run (built once for the whole module)."""
    project_root = tmp_path_factory.mktemp("preflight_completed_root")
    _copy_project_root_sources(project_root)
    manifest, identity_sha256, artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)

    created: list[ScriptedClient] = []

    def factory(params: runner_mod.ClientConstructionParams) -> ScriptedClient:
        client = ScriptedClient(params.usage_log_path)
        created.append(client)
        return client

    completion = runner_mod.run_preflight(
        manifest_path, project_root, None, client_factory=factory, dry_run=True)
    return {
        "project_root": project_root, "manifest_path": manifest_path, "manifest": manifest,
        "identity_sha256": identity_sha256, "artifacts": artifacts, "completion": completion,
        "client": created[0],
    }


@pytest.fixture(scope="module")
def completed_live_run(tmp_path_factory):
    """A full, successfully completed 1,060-cell LIVE run (dry_run=False, real authorization
    record), built once for the whole module.

    Exercises every dry_run=False-specific branch that the dry-run-only ``completed_dry_run``
    fixture cannot: the unsuffixed (non-.dry_run) results/completion/usage-log paths, the
    populated (non-None) ledger_identity branch of _build_client_params, row['dry_run'] is
    False, the 'live' archive-subdir suffix, and (implicitly, by completing without error) the
    live-only ledger-reconciliation completion gate on a genuinely reconciled ledger.
    """
    project_root = tmp_path_factory.mktemp("preflight_completed_live_root")
    _copy_project_root_sources(project_root)
    manifest, identity_sha256, artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    authorization = _authorization_for(
        project_root, identity_sha256, stage_cap=15.0, cumulative_cap=1500.0)
    authorization_path = _write_authorization(project_root, authorization)

    created: list[ScriptedClient] = []
    captured_params: list[runner_mod.ClientConstructionParams] = []

    def factory(params: runner_mod.ClientConstructionParams) -> ScriptedClient:
        captured_params.append(params)
        client = ScriptedClient(params.usage_log_path)
        created.append(client)
        return client

    completion = runner_mod.run_preflight(
        manifest_path, project_root, authorization_path, client_factory=factory, dry_run=False)
    return {
        "project_root": project_root, "manifest_path": manifest_path, "manifest": manifest,
        "identity_sha256": identity_sha256, "artifacts": artifacts, "completion": completion,
        "client": created[0], "client_params": captured_params[0],
        "authorization_path": authorization_path,
    }


# --- contract item 8: strict verdict parsing ------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("ANSWER: A", "A"),
    ("ANSWER: B", "B"),
    ("  ANSWER: A  ", "A"),
    ("\nANSWER: B\n", "B"),
    ("\t ANSWER: A \t", "A"),
    ("ANSWER: C", "INVALID"),
    ("answer: a", "INVALID"),
    ("ANSWER: A extra text", "INVALID"),
    ("ANSWER: A\nANSWER: B", "INVALID"),
    ("The answer is A", "INVALID"),
    ("", "INVALID"),
    ("   ", "INVALID"),
    (None, "INVALID"),
    (123, "INVALID"),
    (["ANSWER: A"], "INVALID"),
])
def test_parse_capability_verdict(raw, expected):
    assert runner_mod.parse_capability_verdict(raw) == expected


# --- contract item 7: call-inventory recomputation ------------------------------------------------


def _fake_inventory(identity_sha256: str, count: int) -> tuple[dict, ...]:
    return tuple(
        {
            "execution_call_key": pe.derive_execution_call_key(
                identity_sha256, planning_cell_key=f"cell-{i}", call_role="capability_qa",
                call_index=i),
            "planning_cell_key": f"cell-{i}", "call_role": "capability_qa", "call_index": i,
        }
        for i in range(count)
    )


def test_verify_call_inventory_accepts_self_consistent_inventory():
    identity_sha256 = "a" * 64
    entries = _fake_inventory(identity_sha256, pe.EXPECTED_CAPABILITY_CELL_COUNT)
    validated = SimpleNamespace(
        execution_identity_sha256=identity_sha256, provider_call_inventory=entries,
        planning_cell_keys=tuple(e["planning_cell_key"] for e in entries),
    )
    runner_mod._verify_call_inventory(cast(Any, validated))  # must not raise


def test_verify_call_inventory_rejects_wrong_count():
    validated = SimpleNamespace(
        execution_identity_sha256="a" * 64, provider_call_inventory=(), planning_cell_keys=())
    with pytest.raises(runner_mod.InventoryMismatchError, match="expected exactly"):
        runner_mod._verify_call_inventory(cast(Any, validated))


def test_verify_call_inventory_rejects_tampered_call_key():
    identity_sha256 = "a" * 64
    entries = list(_fake_inventory(identity_sha256, pe.EXPECTED_CAPABILITY_CELL_COUNT))
    entries[5] = {**entries[5], "execution_call_key": "0" * 64}
    validated = SimpleNamespace(
        execution_identity_sha256=identity_sha256, provider_call_inventory=tuple(entries),
        planning_cell_keys=tuple(e["planning_cell_key"] for e in entries),
    )
    with pytest.raises(
        runner_mod.InventoryMismatchError, match="does not match its freshly recomputed value",
    ):
        runner_mod._verify_call_inventory(cast(Any, validated))


def test_verify_call_inventory_rejects_out_of_order_call_index():
    identity_sha256 = "a" * 64
    entries = list(_fake_inventory(identity_sha256, pe.EXPECTED_CAPABILITY_CELL_COUNT))
    entries[0], entries[1] = entries[1], entries[0]
    validated = SimpleNamespace(
        execution_identity_sha256=identity_sha256, provider_call_inventory=tuple(entries),
        planning_cell_keys=tuple(e["planning_cell_key"] for e in entries),
    )
    with pytest.raises(runner_mod.InventoryMismatchError, match="not in manifest order"):
        runner_mod._verify_call_inventory(cast(Any, validated))


def test_verify_call_inventory_rejects_wrong_call_role():
    identity_sha256 = "a" * 64
    entries = list(_fake_inventory(identity_sha256, pe.EXPECTED_CAPABILITY_CELL_COUNT))
    entries[3] = {**entries[3], "call_role": "not_capability_qa"}
    validated = SimpleNamespace(
        execution_identity_sha256=identity_sha256, provider_call_inventory=tuple(entries),
        planning_cell_keys=tuple(e["planning_cell_key"] for e in entries),
    )
    with pytest.raises(runner_mod.InventoryMismatchError, match="unexpected call_role"):
        runner_mod._verify_call_inventory(cast(Any, validated))


def test_verify_call_inventory_rejects_missing_planning_cell_key():
    identity_sha256 = "a" * 64
    entries = list(_fake_inventory(identity_sha256, pe.EXPECTED_CAPABILITY_CELL_COUNT))
    entries[3] = {**entries[3], "planning_cell_key": ""}
    validated = SimpleNamespace(
        execution_identity_sha256=identity_sha256, provider_call_inventory=tuple(entries),
        planning_cell_keys=tuple(e["planning_cell_key"] for e in entries),
    )
    with pytest.raises(runner_mod.InventoryMismatchError, match="has no planning_cell_key"):
        runner_mod._verify_call_inventory(cast(Any, validated))


def test_verify_call_inventory_rejects_duplicate_planning_cell_key():
    identity_sha256 = "a" * 64
    entries = list(_fake_inventory(identity_sha256, pe.EXPECTED_CAPABILITY_CELL_COUNT))
    # Two DIFFERENT call_index positions (5 and 6) sharing the same planning_cell_key: unlike a
    # duplicate execution_call_key (which call_index makes structurally unreachable here, since
    # call_index is baked into the hash and is already forced unique-per-position by the
    # preceding "not in manifest order" check), two distinct positions genuinely can reference
    # the same planning cell.
    reused_planning_key = entries[5]["planning_cell_key"]
    recomputed_call_key = pe.derive_execution_call_key(
        identity_sha256, planning_cell_key=reused_planning_key, call_role="capability_qa",
        call_index=6)
    entries[6] = {
        **entries[6], "planning_cell_key": reused_planning_key,
        "execution_call_key": recomputed_call_key,
    }
    validated = SimpleNamespace(
        execution_identity_sha256=identity_sha256, provider_call_inventory=tuple(entries),
        planning_cell_keys=tuple(e["planning_cell_key"] for e in entries),
    )
    with pytest.raises(runner_mod.InventoryMismatchError, match="duplicate planning_cell_key"):
        runner_mod._verify_call_inventory(cast(Any, validated))


def test_verify_call_inventory_rejects_planning_cell_keys_disagreement():
    identity_sha256 = "a" * 64
    entries = _fake_inventory(identity_sha256, pe.EXPECTED_CAPABILITY_CELL_COUNT)
    real_planning_keys = tuple(e["planning_cell_key"] for e in entries)
    # The manifest's own planning_cell_keys inventory disagrees with what the call inventory
    # actually references (last real key swapped for one no entry references).
    mismatched_planning_keys = real_planning_keys[:-1] + ("cell-not-referenced-by-any-entry",)
    validated = SimpleNamespace(
        execution_identity_sha256=identity_sha256, provider_call_inventory=entries,
        planning_cell_keys=mismatched_planning_keys,
    )
    with pytest.raises(runner_mod.InventoryMismatchError, match="planning cells disagree"):
        runner_mod._verify_call_inventory(cast(Any, validated))


# --- defense-in-depth: _run_locked must itself enforce what run_preflight's wiring documents ------


def test_run_locked_refuses_live_run_on_unauthorized_manifest_even_when_called_directly(
    tmp_path_factory,
):
    """_run_locked is the ONLY function that actually dispatches provider calls, so it must
    refuse a live (dry_run=False) run on an unauthorized manifest itself -- not rely solely on
    run_preflight's require_authorized=not dry_run wiring into validate_execution_manifest."""
    project_root = tmp_path_factory.mktemp("preflight_run_locked_unauth_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    validated = pe.validate_execution_manifest(
        pe.load_execution_manifest(manifest_path), project_root=project_root,
        authorization=None, require_authorized=False)
    assert validated.authorized is False

    with pytest.raises(runner_mod.ManifestRejectedError, match="not authorized"):
        runner_mod._run_locked(
            validated=validated, project_root=project_root, manifest_path=manifest_path,
            client_factory=_poison_factory, dry_run=False)


def test_run_locked_rejects_tampered_call_inventory_even_when_called_directly(tmp_path_factory):
    """_run_locked must independently re-verify provider_call_inventory itself -- not rely
    solely on run_preflight calling _verify_call_inventory before the lock is acquired -- so a
    tampered ValidatedExecutionManifest handed directly to it is caught before any real call."""
    project_root = tmp_path_factory.mktemp("preflight_run_locked_tampered_inventory_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    validated = _validated_for(manifest_path, project_root)

    tampered = replace(
        validated,
        provider_call_inventory=validated.provider_call_inventory + (
            validated.provider_call_inventory[0],),
    )

    with pytest.raises(runner_mod.InventoryMismatchError, match="expected exactly"):
        runner_mod._run_locked(
            validated=tampered, project_root=project_root, manifest_path=manifest_path,
            client_factory=_poison_factory, dry_run=True)


def test_run_locked_rejects_inventory_entry_with_unknown_planning_cell(tmp_path_factory):
    """Covers the InventoryMismatchError raised inside _run_locked's own dispatch loop (not
    _verify_call_inventory) when an inventory entry's planning_cell_key is self-consistent
    (passes the independent recomputation) but does not correspond to any real capability_qa
    cell in the freshly loaded protocol."""
    project_root = tmp_path_factory.mktemp("preflight_unknown_planning_cell_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    validated = _validated_for(manifest_path, project_root)

    entries = list(validated.provider_call_inventory)
    bogus_planning_key = "nonexistent-planning-cell-key"
    bogus_call_key = pe.derive_execution_call_key(
        validated.execution_identity_sha256, planning_cell_key=bogus_planning_key,
        call_role=str(entries[0]["call_role"]), call_index=int(entries[0]["call_index"]))
    entries[0] = {
        **entries[0], "planning_cell_key": bogus_planning_key,
        "execution_call_key": bogus_call_key,
    }
    planning_keys = (bogus_planning_key,) + tuple(validated.planning_cell_keys[1:])
    tampered = replace(
        validated, provider_call_inventory=tuple(entries), planning_cell_keys=planning_keys)

    created: list[ScriptedClient] = []

    def factory(params):
        client = ScriptedClient(params.usage_log_path)
        created.append(client)
        return client

    with pytest.raises(runner_mod.InventoryMismatchError, match="unknown planning cell"):
        runner_mod._run_locked(
            validated=tampered, project_root=project_root, manifest_path=manifest_path,
            client_factory=factory, dry_run=True)
    assert created[0].calls == []  # caught before any real dispatch


def test_run_locked_rejects_inventory_entry_whose_side_has_no_rendered_corpus_entry(
    tmp_path_factory,
):
    """Covers the sibling InventoryMismatchError raised inside _run_locked's dispatch loop when
    an entry's (question_id, side) pair has no matching rendered corpus entry."""
    project_root = tmp_path_factory.mktemp("preflight_no_corpus_entry_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    validated = _validated_for(manifest_path, project_root)

    entries = list(validated.provider_call_inventory)
    entries[0] = {**entries[0], "side": "Z"}  # execution_call_key does not depend on side
    tampered = replace(validated, provider_call_inventory=tuple(entries))

    created: list[ScriptedClient] = []

    def factory(params):
        client = ScriptedClient(params.usage_log_path)
        created.append(client)
        return client

    with pytest.raises(runner_mod.InventoryMismatchError, match="no rendered corpus entry"):
        runner_mod._run_locked(
            validated=tampered, project_root=project_root, manifest_path=manifest_path,
            client_factory=factory, dry_run=True)
    assert created[0].calls == []  # caught before any real dispatch


# --- contract item 12: archival -------------------------------------------------------------------


def _dummy_validated(**overrides):
    defaults = dict(stage="capability_preflight", execution_identity_sha256="ab" * 32)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_archive_outputs_copies_files_and_writes_sha256sums(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"m": 1}', encoding="utf-8")
    results_path = tmp_path / "results.jsonl"
    results_path.write_text('{"r": 1}\n', encoding="utf-8")
    completion_path = tmp_path / "completion.json"
    completion_path.write_text('{"c": 1}', encoding="utf-8")
    usage_log_path = tmp_path / "usage.jsonl"
    usage_log_path.write_text('{"u": 1}\n', encoding="utf-8")
    archive_root = tmp_path / "archive"

    destination = runner_mod._archive_outputs(
        archive_root=archive_root, validated=_dummy_validated(), dry_run=True,
        manifest_path=manifest_path, results_path=results_path, completion_path=completion_path,
        usage_log_path=usage_log_path,
    )
    assert (destination / "manifest.json").read_text(encoding="utf-8") == '{"m": 1}'
    assert (destination / "results.jsonl").exists()
    assert (destination / "completion.json").exists()
    assert (destination / "usage_events.jsonl").exists()
    sums = (destination / "SHA256SUMS").read_text(encoding="utf-8")
    for name in ("manifest.json", "results.jsonl", "completion.json", "usage_events.jsonl"):
        assert name in sums
        expected_digest = hashlib.sha256((destination / name).read_bytes()).hexdigest()
        assert f"{expected_digest}  {name}" in sums


def test_archive_outputs_raises_on_unwritable_destination(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    results_path = tmp_path / "results.jsonl"
    results_path.write_text("{}\n", encoding="utf-8")
    completion_path = tmp_path / "completion.json"
    completion_path.write_text("{}", encoding="utf-8")
    usage_log_path = tmp_path / "usage.jsonl"
    usage_log_path.write_text("", encoding="utf-8")

    blocked = tmp_path / "blocked_archive_destination"
    blocked.write_text("occupied by a regular file, not a directory", encoding="utf-8")

    with pytest.raises(runner_mod.ArchiveError):
        runner_mod._archive_outputs(
            archive_root=blocked, validated=_dummy_validated(), dry_run=True,
            manifest_path=manifest_path, results_path=results_path,
            completion_path=completion_path, usage_log_path=usage_log_path,
        )


# --- OutputPersistenceError: durable-persistence failures at both raise sites ---------------------


def test_output_persistence_error_when_results_path_is_unwritable(tmp_path_factory):
    """Covers the prepare_jsonl_output wrap (the pre-loop raise site): a results_path occupied
    by a directory instead of a file cannot be opened for append. This runs before the client
    can be constructed, so client_factory must never be invoked."""
    project_root = tmp_path_factory.mktemp("preflight_output_persistence_prepare_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    validated = _validated_for(manifest_path, project_root)
    results_path = runner_mod._results_path(project_root, validated, dry_run=True)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.mkdir()  # occupy the results path with a directory, not a writable file

    with pytest.raises(runner_mod.OutputPersistenceError):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=_poison_factory, dry_run=True)


def test_output_persistence_error_when_append_jsonl_record_fails(tmp_path_factory, monkeypatch):
    """Covers the append_jsonl_record wrap (the in-loop raise site, after a real call has
    already returned) via a monkeypatched append_jsonl_record -- reliable fault injection for
    the exact call site, rather than OS-level tricks that would be unreliable across
    platforms for targeting one specific append rather than the initial prepare."""
    project_root = tmp_path_factory.mktemp("preflight_output_persistence_append_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)

    def _boom(_path, _record):
        raise runner_mod.RunnerOutputPersistenceError("simulated durable-append failure")

    monkeypatch.setattr(runner_mod, "append_jsonl_record", _boom)

    with pytest.raises(runner_mod.OutputPersistenceError):
        runner_mod.run_preflight(
            manifest_path, project_root, None,
            client_factory=lambda params: ScriptedClient(params.usage_log_path), dry_run=True)


# --- module purity ---------------------------------------------------------------------------


def test_module_purity_no_sdk_import_at_module_load():
    script = (
        "import sys\n"
        "from rejudge import phase2_preflight_runner\n"
        "assert 'together' not in sys.modules, 'together SDK must not be imported'\n"
        "print('PURITY_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "PURITY_OK" in result.stdout


# --- contract item 1: dry-run-only CLI --------------------------------------------------------


def test_cli_refuses_without_dry_run_flag(tmp_path, capsys):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    exit_code = runner_mod.main(
        ["--manifest", str(manifest_path), "--project-root", str(tmp_path)])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "--dry-run" in captured.err


def test_cli_dry_run_reruns_the_already_complete_manifest_idempotently(completed_dry_run):
    exit_code = runner_mod.main([
        "--manifest", str(completed_dry_run["manifest_path"]),
        "--project-root", str(completed_dry_run["project_root"]),
        "--dry-run",
    ])
    assert exit_code == 0


# --- contract item 2: dry run may be unauthorized; marks dry_run:true everywhere ------------------


def test_dry_run_uses_unauthorized_manifest_and_marks_every_row_dry_run_true(completed_dry_run):
    completion = completed_dry_run["completion"]
    assert completion.dry_run is True
    results_path = Path(completion.output_rows_path)
    rows = [
        json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert all(row["dry_run"] is True for row in rows)

    completion_payload = json.loads(
        runner_mod._completion_path(
            completed_dry_run["project_root"],
            _validated_for(completed_dry_run["manifest_path"], completed_dry_run["project_root"]),
            dry_run=True,
        ).read_text(encoding="utf-8"))
    assert completion_payload["dry_run"] is True


def test_live_run_requires_authorization(tmp_path_factory):
    project_root = tmp_path_factory.mktemp("preflight_live_no_auth_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    with pytest.raises(runner_mod.ManifestRejectedError):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=_poison_factory, dry_run=False)


# --- live (dry_run=False) run: the entire live-only code path, previously untested ----------------


def test_full_live_run_completes_all_1060_calls_and_marks_dry_run_false(completed_live_run):
    completion = completed_live_run["completion"]
    assert completion.dry_run is False
    assert completion.total_calls == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert dict(completion.counts) == {
        "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 0,
        "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT, "blocked_reconciliation": 0,
    }
    results_path = Path(completion.output_rows_path)
    rows = [
        json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert all(row["dry_run"] is False for row in rows)


def test_live_run_client_params_populate_ledger_identity_and_use_unsuffixed_paths(
    completed_live_run,
):
    params = completed_live_run["client_params"]
    assert params.dry_run is False
    assert params.ledger_identity == dict(completed_live_run["manifest"]["ledger"])
    # Never the .dry_run-suffixed sibling paths a dry run uses.
    assert not str(params.usage_log_path).endswith(".dry_run.jsonl")
    assert str(params.usage_log_path).endswith("phase2_capability_preflight_ledger.jsonl")
    completion = completed_live_run["completion"]
    assert not completion.output_rows_path.endswith(".dry_run.jsonl")


def test_live_run_archive_subdir_uses_live_not_dryrun_suffix(completed_live_run):
    completion = completed_live_run["completion"]
    destination = Path(completion.archive_destination)
    assert destination.name.endswith("_live")
    assert not destination.name.endswith("_dryrun")
    assert (destination / "results.jsonl").exists()
    assert (destination / "usage_events.jsonl").exists()


# --- contract item 3: exclusive project-wide lock --------------------------------------------------


def test_lock_contention_refuses_and_never_invokes_client(completed_dry_run, tmp_path_factory):
    destination = tmp_path_factory.mktemp("preflight_lock_copy") / "root"
    shutil.copytree(completed_dry_run["project_root"], destination)
    manifest_path = destination / "manifest.json"
    ledger_path = destination / REL_LEDGER

    with run_manifest.output_lock(ledger_path):
        with pytest.raises(runner_mod.LockHeldError):
            runner_mod.run_preflight(
                manifest_path, destination, None, client_factory=_poison_factory, dry_run=True)


def test_lock_is_released_after_a_successful_run(completed_dry_run):
    # If the lock were still held, a second acquisition attempt on the same fixed path would
    # itself raise OutputLockedError.
    ledger_path = completed_dry_run["project_root"] / REL_LEDGER
    with run_manifest.output_lock(ledger_path):
        pass


# --- contract item 5: corpus rendering + forecast cross-check -------------------------------------


def test_corpus_sha_mismatch_refuses_before_any_call(tmp_path_factory):
    project_root = tmp_path_factory.mktemp("preflight_corpus_mismatch_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(
        project_root, tmp_path_factory, corpus_sha_override="0" * 64)
    manifest_path = _write_manifest(project_root, manifest)
    with pytest.raises(
        runner_mod.CorpusMismatchError, match="does not match the manifest-bound forecast",
    ):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=_poison_factory, dry_run=True)


def test_corpus_entry_count_mismatch_refuses_before_any_call(tmp_path_factory):
    project_root = tmp_path_factory.mktemp("preflight_corpus_count_mismatch_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(
        project_root, tmp_path_factory, corpus_count_override=1)
    manifest_path = _write_manifest(project_root, manifest)
    with pytest.raises(
        runner_mod.CorpusMismatchError, match="entry_count does not match",
    ):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=_poison_factory, dry_run=True)


def test_corpus_rendering_failure_wraps_as_corpus_mismatch_error(tmp_path_factory):
    """Covers the capability_corpus.CapabilityCorpusError -> CorpusMismatchError wrap (a
    rendering failure, distinct from the sha/entry_count mismatch branches covered above)."""
    project_root = tmp_path_factory.mktemp("preflight_corpus_render_failure_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)

    # Corrupt a world document AFTER the manifest's corpus-sha binding was computed against the
    # intact corpus, so rendering now fails outright rather than merely mismatching a hash.
    (project_root / "world_specs" / "selvarath.txt").unlink()

    with pytest.raises(
        runner_mod.CorpusMismatchError, match="could not render the capability_qa corpus",
    ):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=_poison_factory, dry_run=True)


def test_corpus_forecast_missing_rendered_corpus_binding_wraps_as_corpus_mismatch_error(
    tmp_path_factory,
):
    """Covers the KeyError/TypeError path when the manifest-bound cost_forecast artifact is
    missing bindings.rendered_corpus entirely (as opposed to carrying a mismatched one)."""
    project_root = tmp_path_factory.mktemp("preflight_corpus_missing_binding_root")
    _copy_project_root_sources(project_root)
    baseline = _baseline(project_root)
    corpus_entries = capability_corpus.render_capability_corpus(
        baseline["bundle"], baseline["protocol"], project_root)
    artifacts = _write_artifacts(project_root, tmp_path_factory, corpus_entries)
    # Overwrite the cost_forecast artifact's content (before it is hash-bound into the
    # manifest, so no hash-drift error masks this) with a payload missing rendered_corpus.
    artifacts["cost_forecast"].write_text(json.dumps({"bindings": {}}), encoding="utf-8")
    manifest, _identity = _build_manifest(project_root, baseline, artifacts)
    manifest_path = _write_manifest(project_root, manifest)

    with pytest.raises(
        runner_mod.CorpusMismatchError, match="missing bindings.rendered_corpus",
    ):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=_poison_factory, dry_run=True)


def test_prompts_rendered_exclusively_via_capability_corpus(tmp_path_factory):
    project_root = tmp_path_factory.mktemp("preflight_prompt_render_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)

    captured: list[ScriptedClient] = []

    def factory(params):
        client = ScriptedClient(
            params.usage_log_path, fail_after=1, fail_with=_SimulatedCrash())
        captured.append(client)
        return client

    with pytest.raises(_SimulatedCrash):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=factory, dry_run=True)

    call = captured[0].calls[0]
    call_key = call["request_metadata"]["execution_call_key"]
    entry = next(
        e for e in manifest["provider_call_inventory"] if e["execution_call_key"] == call_key)

    protocol = phase2_plan.load_protocol(project_root / REL_PROTOCOL)
    bundle, _bundle_protocol = prompt_bundle.load_and_validate(
        project_root / REL_BUNDLE, project_root / REL_PROTOCOL)
    corpus_entries = capability_corpus.render_capability_corpus(bundle, protocol, project_root)
    cells_by_key = runner_mod._capability_cells_by_key(protocol, project_root)
    cell = cells_by_key[entry["planning_cell_key"]]
    expected = next(
        e for e in corpus_entries
        if e["question_id"] == cell["question_id"] and e["side"] == entry["side"])

    assert call["messages"] == [
        {"role": "system", "content": expected["system_prompt"]},
        {"role": "user", "content": expected["user_prompt"]},
    ]


# --- contract item 6: temperature/max_tokens resolved via role_limits -----------------------------


def test_temperature_and_max_tokens_resolved_via_role_limits(tmp_path_factory):
    project_root = tmp_path_factory.mktemp("preflight_role_limits_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)

    captured: list[ScriptedClient] = []

    def factory(params):
        client = ScriptedClient(
            params.usage_log_path, fail_after=5, fail_with=_SimulatedCrash())
        captured.append(client)
        return client

    with pytest.raises(_SimulatedCrash):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=factory, dry_run=True)

    protocol = phase2_plan.load_protocol(project_root / REL_PROTOCOL)
    role_limits_v2 = json.loads((project_root / REL_ROLE_LIMITS_V2).read_text(encoding="utf-8"))
    entries_by_key = {e["execution_call_key"]: e for e in manifest["provider_call_inventory"]}
    for call in captured[0].calls[:5]:
        entry = entries_by_key[call["request_metadata"]["execution_call_key"]]
        resolved = role_limits.resolve_request_parameters(
            role_limits_v2, protocol, entry["model"], "capability_qa")
        assert call["temperature"] == resolved.temperature
        assert call["max_tokens"] == resolved.effective_max_tokens


# --- contract item 4: strict client-construction parameters ---------------------------------------


def test_client_construction_params_carry_every_strict_setting(tmp_path_factory):
    project_root = tmp_path_factory.mktemp("preflight_client_params_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, artifacts = _build_full_manifest(
        project_root, tmp_path_factory, stage_cap=7.5)
    manifest_path = _write_manifest(project_root, manifest)
    role_limits_v2 = json.loads(
        artifacts["role_limits_and_request_settings"].read_text(encoding="utf-8"))

    captured_params: list[runner_mod.ClientConstructionParams] = []

    def factory(params):
        captured_params.append(params)
        return ScriptedClient(params.usage_log_path, fail_after=0, fail_with=_SimulatedCrash())

    with pytest.raises(_SimulatedCrash):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=factory, dry_run=True)

    params = captured_params[0]
    assert params.dry_run is True
    assert params.approved_cap_usd == 7.5
    assert params.require_explicit_reasoning_max_tokens is True
    assert params.strict_context_mode is True
    assert params.max_retries == 3
    assert set(params.streaming_pinned_models) == set(
        role_limits_v2["request_settings"]["streaming_pinned_models"])
    assert dict(params.extra_request_fields) == role_limits_v2["request_settings"][
        "per_model_extra_fields"]
    expected_context = {
        model_id: entry["context_length_tokens"]
        for model_id, entry in role_limits_v2["context_ceilings"].items()
    }
    assert dict(params.model_context_limits) == expected_context
    assert str(params.usage_log_path).endswith(".dry_run.jsonl")
    assert params.ledger_identity is None  # dry run: never the manifest-bound live ledger


def test_client_construction_params_use_stage_cap_not_cumulative_cap(tmp_path_factory):
    project_root = tmp_path_factory.mktemp("preflight_cap_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(
        project_root, tmp_path_factory, stage_cap=3.0, cumulative_cap=999.0)
    manifest_path = _write_manifest(project_root, manifest)

    captured_params: list[runner_mod.ClientConstructionParams] = []

    def factory(params):
        captured_params.append(params)
        return ScriptedClient(params.usage_log_path, fail_after=0, fail_with=_SimulatedCrash())

    with pytest.raises(_SimulatedCrash):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=factory, dry_run=True)

    assert captured_params[0].approved_cap_usd == 3.0


# --- contract item 9 + 11: persist-before-advance, completion gate --------------------------------


def test_full_dry_run_completes_all_1060_calls_exactly(completed_dry_run):
    completion = completed_dry_run["completion"]
    assert completion.total_calls == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert dict(completion.counts) == {
        "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 0,
        "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT, "blocked_reconciliation": 0,
    }
    results_path = Path(completion.output_rows_path)
    rows = [
        json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    call_keys = {row["execution_call_key"] for row in rows}
    assert len(call_keys) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert all(row["verdict"] == "A" for row in rows)
    assert all(row["raw_output"] == "ANSWER: A" for row in rows)
    for row in rows:
        assert set(row) == {
            "execution_call_key", "planning_cell_key", "call_index", "question_id", "model",
            "seed", "side", "raw_output", "verdict", "request_fields_sha256",
            "response_metadata", "dry_run",
        }

    recomputed_sha = hashlib.sha256(results_path.read_bytes()).hexdigest()
    assert completion.output_rows_sha256 == recomputed_sha


def test_completion_record_written_to_disk(completed_dry_run):
    validated = _validated_for(
        completed_dry_run["manifest_path"], completed_dry_run["project_root"])
    completion_path = runner_mod._completion_path(
        completed_dry_run["project_root"], validated, dry_run=True)
    assert completion_path.exists()
    payload = json.loads(completion_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == runner_mod.COMPLETION_SCHEMA_VERSION
    assert payload["stage"] == "capability_preflight"
    assert payload["total_calls"] == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert payload["archive_destination"]


# --- CompletionGateError: all 3 raise sites, isolated via a stubbed audit_resume ------------------
#
# audit_resume and the completion gate are independent, redundant checks (see the module
# docstring), so under any input that also satisfies a correctly functioning audit_resume, the
# gate can never actually disagree with it -- these three raise sites exist purely to catch a
# regression in that independence itself. Directly stubbing audit_resume's return value (rather
# than trying to construct real ledger/output-row tampering that would also fool the real
# audit_resume, which by design is not possible) is what makes each of them reachable, cheaply,
# without a full 1,060-call run.


def test_completion_gate_raises_when_final_audit_disposition_is_not_complete(
    tmp_path_factory, monkeypatch,
):
    project_root = tmp_path_factory.mktemp("preflight_gate_disposition_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    validated = _validated_for(manifest_path, project_root)
    one_key = str(validated.provider_call_inventory[0]["execution_call_key"])

    initial_audit = pe.ResumeAudit(
        stage=validated.stage, disposition=pe.ResumeDisposition.TODO, per_call={},
        todo_call_keys=(one_key,), blockers=(), counts={
            "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 1,
            "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT - 1, "blocked_reconciliation": 0},
    )
    # Simulates a post-loop inconsistency: the single TODO call was processed, but the
    # (stubbed) final audit still refuses to call the run COMPLETE.
    final_audit = pe.ResumeAudit(
        stage=validated.stage, disposition=pe.ResumeDisposition.TODO, per_call={},
        todo_call_keys=(), blockers=("simulated post-loop inconsistency",), counts={
            "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 1,
            "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT - 1, "blocked_reconciliation": 0},
    )
    _stub_audit_sequence(monkeypatch, initial_audit, final_audit)

    with pytest.raises(runner_mod.CompletionGateError, match="completion gate failed"):
        runner_mod.run_preflight(
            manifest_path, project_root, None,
            client_factory=lambda params: ScriptedClient(params.usage_log_path), dry_run=True)


def test_completion_gate_raises_on_row_count_mismatch(tmp_path_factory, monkeypatch):
    project_root = tmp_path_factory.mktemp("preflight_gate_row_count_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    validated = _validated_for(manifest_path, project_root)
    one_key = str(validated.provider_call_inventory[0]["execution_call_key"])

    initial_audit = pe.ResumeAudit(
        stage=validated.stage, disposition=pe.ResumeDisposition.TODO, per_call={},
        todo_call_keys=(one_key,), blockers=(), counts={
            "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 1,
            "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT - 1, "blocked_reconciliation": 0},
    )
    # The stubbed final audit claims COMPLETE, but only one real row was ever persisted (only
    # one call was in todo_call_keys above) -- the gate's own direct row-count check must catch
    # this even when the (stubbed) audit disposition alone would not.
    final_audit = pe.ResumeAudit(
        stage=validated.stage, disposition=pe.ResumeDisposition.COMPLETE, per_call={},
        todo_call_keys=(), blockers=(), counts={
            "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 0,
            "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT, "blocked_reconciliation": 0},
    )
    _stub_audit_sequence(monkeypatch, initial_audit, final_audit)

    with pytest.raises(runner_mod.CompletionGateError, match="expected exactly"):
        runner_mod.run_preflight(
            manifest_path, project_root, None,
            client_factory=lambda params: ScriptedClient(params.usage_log_path), dry_run=True)


def test_live_ledger_reconciliation_gate_raises_on_success_count_mismatch(
    tmp_path_factory, monkeypatch,
):
    """The live-only branch (contract item 11's ledger reconciliation): the stubbed audit
    reports COMPLETE with all 1,060 rows genuinely present on disk (fabricated directly, not
    via real calls), but the real usage-events ledger has zero matching success events --
    exactly the scenario a genuinely correct audit_resume could never itself produce, which is
    why this branch needs a stubbed audit to reach at all."""
    project_root = tmp_path_factory.mktemp("preflight_live_reconciliation_root")
    _copy_project_root_sources(project_root)
    manifest, identity_sha256, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    authorization = _authorization_for(
        project_root, identity_sha256, stage_cap=15.0, cumulative_cap=1500.0)
    authorization_path = _write_authorization(project_root, authorization)

    validated = pe.validate_execution_manifest(
        pe.load_execution_manifest(manifest_path), project_root=project_root,
        authorization=authorization, require_authorized=True)
    results_path = runner_mod._results_path(project_root, validated, dry_run=False)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        "\n".join(
            json.dumps({"synthetic_row": i})
            for i in range(pe.EXPECTED_CAPABILITY_CELL_COUNT)) + "\n",
        encoding="utf-8",
    )

    complete_audit = pe.ResumeAudit(
        stage=validated.stage, disposition=pe.ResumeDisposition.COMPLETE, per_call={},
        todo_call_keys=(), blockers=(), counts={
            "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 0,
            "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT, "blocked_reconciliation": 0},
    )
    _stub_audit_sequence(monkeypatch, complete_audit, complete_audit)

    with pytest.raises(
        runner_mod.CompletionGateError,
        match="live ledger success-event count does not reconcile",
    ):
        runner_mod.run_preflight(
            manifest_path, project_root, authorization_path,
            client_factory=lambda params: ScriptedClient(params.usage_log_path), dry_run=False)


# --- StoragePolicyError: missing/empty archive_destination ----------------------------------------


def test_storage_policy_error_when_archive_destination_is_empty(tmp_path_factory, monkeypatch):
    project_root = tmp_path_factory.mktemp("preflight_storage_policy_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(
        project_root, tmp_path_factory, archive_destination="")
    manifest_path = _write_manifest(project_root, manifest)
    validated = _validated_for(manifest_path, project_root)

    results_path = runner_mod._results_path(project_root, validated, dry_run=True)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        "\n".join(
            json.dumps({"synthetic_row": i})
            for i in range(pe.EXPECTED_CAPABILITY_CELL_COUNT)) + "\n",
        encoding="utf-8",
    )

    complete_audit = pe.ResumeAudit(
        stage=validated.stage, disposition=pe.ResumeDisposition.COMPLETE, per_call={},
        todo_call_keys=(), blockers=(), counts={
            "total": pe.EXPECTED_CAPABILITY_CELL_COUNT, "todo": 0,
            "complete": pe.EXPECTED_CAPABILITY_CELL_COUNT, "blocked_reconciliation": 0},
    )
    _stub_audit_sequence(monkeypatch, complete_audit, complete_audit)

    with pytest.raises(runner_mod.StoragePolicyError, match="archive_destination"):
        runner_mod.run_preflight(
            manifest_path, project_root, None,
            client_factory=lambda params: ScriptedClient(params.usage_log_path), dry_run=True)


# --- contract item 12: archival on a full run -------------------------------------------------


def test_archive_contains_manifest_results_completion_and_sha256sums(completed_dry_run):
    completion = completed_dry_run["completion"]
    destination = Path(completion.archive_destination)
    assert (destination / "manifest.json").exists()
    assert (destination / "results.jsonl").exists()
    assert (destination / "completion.json").exists()
    assert (destination / "usage_events.jsonl").exists()
    sums = (destination / "SHA256SUMS").read_text(encoding="utf-8")
    for name in ("manifest.json", "results.jsonl", "completion.json", "usage_events.jsonl"):
        assert name in sums


# --- contract item 8 + 9: malformed answer -> INVALID, never retried; archive failure -------------


def test_malformed_answer_recorded_invalid_never_retried_and_archive_failure_refuses(
    tmp_path_factory,
):
    project_root = tmp_path_factory.mktemp("preflight_malformed_root")
    _copy_project_root_sources(project_root)
    blocked_archive = tmp_path_factory.mktemp("preflight_blocked_archive_parent") / "blocked"
    blocked_archive.write_text("occupied", encoding="utf-8")

    manifest, _identity, _artifacts = _build_full_manifest(
        project_root, tmp_path_factory, archive_destination=str(blocked_archive))
    manifest_path = _write_manifest(project_root, manifest)

    target_entry = manifest["provider_call_inventory"][7]
    target_key = target_entry["execution_call_key"]
    overrides = {target_key: "the sky is blue today"}

    created: list[ScriptedClient] = []

    def factory(params):
        client = ScriptedClient(params.usage_log_path, overrides=overrides)
        created.append(client)
        return client

    with pytest.raises(runner_mod.ArchiveError):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=factory, dry_run=True)

    client = created[0]
    calls_for_target = [
        c for c in client.calls if c["request_metadata"]["execution_call_key"] == target_key]
    assert len(calls_for_target) == 1  # NEVER regenerated/retried for a semantic parse failure

    validated = _validated_for(manifest_path, project_root)
    results_path = runner_mod._results_path(project_root, validated, dry_run=True)
    rows = [
        json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    target_row = next(r for r in rows if r["execution_call_key"] == target_key)
    assert target_row["verdict"] == "INVALID"
    assert target_row["raw_output"] == "the sky is blue today"

    # The completion record is durable proof of the 1,060 results even though archival (a
    # separate, later step) failed and the run therefore refused to report success.
    completion_path = runner_mod._completion_path(project_root, validated, dry_run=True)
    assert completion_path.exists()


# --- contract item 10: startup resume audit; blocked reconciliation; duplicate output -------------


def test_blocked_reconciliation_unmatched_reservation_refuses_before_any_call(
    completed_dry_run, tmp_path_factory,
):
    destination = tmp_path_factory.mktemp("preflight_blocked_copy") / "root"
    shutil.copytree(completed_dry_run["project_root"], destination)
    manifest = completed_dry_run["manifest"]
    entry = manifest["provider_call_inventory"][0]
    validated = _validated_for(destination / "manifest.json", destination)
    usage_log_path = runner_mod._usage_log_path(destination, validated, dry_run=True)
    with usage_log_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "status": "reserved", "attempt_id": "orphan-attempt-never-terminated",
            "model": entry["model"], "seed": entry["seed"],
            "metadata": {
                "execution_call_key": entry["execution_call_key"],
                "request_fields_sha256": entry["request_fields_sha256"],
            },
        }) + "\n")
        stream.flush()
        os.fsync(stream.fileno())

    with pytest.raises(runner_mod.ResumeBlockedError):
        runner_mod.run_preflight(
            destination / "manifest.json", destination, None,
            client_factory=_poison_factory, dry_run=True)


def test_duplicate_output_row_blocks_resume(completed_dry_run, tmp_path_factory):
    destination = tmp_path_factory.mktemp("preflight_dup_copy") / "root"
    shutil.copytree(completed_dry_run["project_root"], destination)
    validated = _validated_for(destination / "manifest.json", destination)
    results_path = runner_mod._results_path(destination, validated, dry_run=True)
    lines = results_path.read_text(encoding="utf-8").splitlines()
    assert lines
    with results_path.open("a", encoding="utf-8") as stream:
        stream.write(lines[0] + "\n")
        stream.flush()
        os.fsync(stream.fileno())

    with pytest.raises(runner_mod.ResumeBlockedError):
        runner_mod.run_preflight(
            destination / "manifest.json", destination, None,
            client_factory=_poison_factory, dry_run=True)


def test_malformed_output_line_blocks_resume(completed_dry_run, tmp_path_factory):
    destination = tmp_path_factory.mktemp("preflight_malformed_line_copy") / "root"
    shutil.copytree(completed_dry_run["project_root"], destination)
    validated = _validated_for(destination / "manifest.json", destination)
    results_path = runner_mod._results_path(destination, validated, dry_run=True)
    with results_path.open("a", encoding="utf-8") as stream:
        stream.write("{not valid json\n")
        stream.flush()
        os.fsync(stream.fileno())

    with pytest.raises(runner_mod.ResumeBlockedError):
        runner_mod.run_preflight(
            destination / "manifest.json", destination, None,
            client_factory=_poison_factory, dry_run=True)


# --- crash-and-resume: resumes only TODO cells -----------------------------------------------


def test_crash_and_resume_mid_run_completes_only_the_remaining_todo_cells(tmp_path_factory):
    project_root = tmp_path_factory.mktemp("preflight_crash_resume_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)

    crashing_clients: list[ScriptedClient] = []

    def crashing_factory(params):
        client = ScriptedClient(
            params.usage_log_path, fail_after=500,
            fail_with=_SimulatedCrash("simulated crash at cell 500"))
        crashing_clients.append(client)
        return client

    with pytest.raises(_SimulatedCrash):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=crashing_factory, dry_run=True)

    validated = _validated_for(manifest_path, project_root)
    results_path = runner_mod._results_path(project_root, validated, dry_run=True)
    rows_after_crash = [
        json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows_after_crash) == 500
    assert len(crashing_clients[0].calls) == 501  # 500 succeeded, the 501st raised

    resumed_clients: list[ScriptedClient] = []

    def resumed_factory(params):
        client = ScriptedClient(params.usage_log_path)
        resumed_clients.append(client)
        return client

    completion = runner_mod.run_preflight(
        manifest_path, project_root, None, client_factory=resumed_factory, dry_run=True)

    assert completion.total_calls == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert dict(completion.counts)["complete"] == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert dict(completion.counts)["blocked_reconciliation"] == 0
    # Only the remaining TODO cells were (re-)executed -- the first 500 were never re-run.
    assert len(resumed_clients[0].calls) == pe.EXPECTED_CAPABILITY_CELL_COUNT - 500

    final_rows = [
        json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(final_rows) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert len({r["execution_call_key"] for r in final_rows}) == pe.EXPECTED_CAPABILITY_CELL_COUNT

    before_crash_keys = {r["execution_call_key"] for r in rows_after_crash}
    resumed_keys = {
        c["request_metadata"]["execution_call_key"] for c in resumed_clients[0].calls}
    assert before_crash_keys.isdisjoint(resumed_keys)


def test_persist_before_advance_holds_at_every_call_not_just_a_round_crash_boundary(
    tmp_path_factory,
):
    """test_crash_and_resume_mid_run_completes_only_the_remaining_todo_cells only checks the
    on-disk row count at ONE crash boundary (500), which coincidentally divides evenly by many
    plausible batch sizes -- a hypothetical regression that buffered N rows before flushing
    could still show exactly 500 persisted rows there and pass. This test instead actively
    checks ordering before EVERY call (via OrderCheckingClient) up to a non-round (prime)
    crash boundary, so a batched/lazy-flush regression cannot hide behind a coincidental count.
    """
    project_root = tmp_path_factory.mktemp("preflight_order_check_root")
    _copy_project_root_sources(project_root)
    manifest, _identity, _artifacts = _build_full_manifest(project_root, tmp_path_factory)
    manifest_path = _write_manifest(project_root, manifest)
    validated = _validated_for(manifest_path, project_root)
    results_path = runner_mod._results_path(project_root, validated, dry_run=True)

    def factory(params):
        return OrderCheckingClient(
            params.usage_log_path, results_path, fail_after=137,
            fail_with=_SimulatedCrash("simulated crash at cell 137"))

    with pytest.raises(_SimulatedCrash):
        runner_mod.run_preflight(
            manifest_path, project_root, None, client_factory=factory, dry_run=True)

    rows = [
        json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 137
