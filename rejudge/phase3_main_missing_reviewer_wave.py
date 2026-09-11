"""Reconstruct a clean, allocated reviewer wave lost before packet preparation.

This is offline preparation, not launch authority. Every model completion comes from
the existing request journal. The reconstructed pass writes only scratch stores;
the separate publication step writes ordinary guarded packets under the run lease.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rejudge.phase2_call_cache import request_fingerprint
from rejudge.phase2_canary_live import export_reviewer_worklist
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_canary_runner import run_canary
from rejudge.phase2_execution import canonical_sha256
from rejudge.request_journal import RequestJournal, journal_key


class MissingWaveError(ValueError):
    """The saved frontier does not prove the exact unanswered reviewer wave."""


class JournalOnlyReplayError(RuntimeError):
    """Offline replay requested something not saved under the exact fingerprint."""


class JournalOnlyClient:
    """No inner transport, SDK, marker writer, or usage writer exists on this client."""

    dry_run = False

    def __init__(self, journal: RequestJournal):
        self.journal = journal
        self.used: dict[tuple, dict[str, Any]] = {}
        self.failure: str | None = None
        self._lock = threading.Lock()

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                 request_metadata=None):
        with self._lock:
            if self.failure is not None:
                raise JournalOnlyReplayError(self.failure)
            try:
                key = journal_key(request_metadata)
                fingerprint = request_fingerprint(messages=messages, model=model,
                    temperature=temperature, seed=seed, max_tokens=max_tokens)
                response = self.journal.get(key, fingerprint)
                if response is None:
                    raise JournalOnlyReplayError("offline replay requires an unsaved request")
                binding = self.journal.entry_binding(key)
                self.used[(key.cell_key, key.call_role, key.slot, key.attempt)] = binding
                return response
            except Exception as exc:
                self.failure = f"{type(exc).__name__}: exact saved request unavailable"
                raise JournalOnlyReplayError(self.failure) from exc


class OfflinePreparationContext(SimpleNamespace):
    """Verified original inputs and proposed code, deliberately not PreparedMainRun."""

    dispatch_capable = False


def _json(path):
    from rejudge.phase3_main_live import _load_strict_json
    return _load_strict_json(path)


def _binding(path):
    path = Path(path).resolve()
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": path.as_posix(), "raw_sha256": digest.hexdigest(), "byte_count": size}


def load_offline_context(manifest_path, authorization_path, prior_recovery_path, *, project_root):
    """Authenticate old scope without claiming that proposed code is signed for execution."""
    from rejudge import phase3_main_live as live
    root = Path(project_root).resolve()
    live._verify_execution_code_root(root)
    manifest_path, authorization_path = Path(manifest_path).resolve(), Path(authorization_path).resolve()
    manifest = live._load_strict_object(manifest_path, "main manifest")
    validated = live.phase3_main_manifest.validate_main_manifest(
        manifest, project_root=root, verify_files=True, verify_runtime=True)
    prior = live.phase3_main_recovery.load_authenticated_recovery(
        prior_recovery_path, manifest_path=manifest_path, authorization_path=authorization_path,
        allow_growth=True, verify_artifacts=False)
    authorization = live._load_authenticated_owner_authorization(authorization_path)
    live.phase3_main_manifest.validate_main_authorization(
        authorization, manifest, as_of=datetime.now(timezone.utc))
    inputs = dict(validated["input_paths"])
    def read(name):
        return live._load_bound_input_object(manifest, inputs, name, name)
    protocol, bundle, prompt, limits = (read(name) for name in
        ("protocol", "prompt_bundle", "reviewer_prompt", "role_limits"))
    live.phase3_plan.validate_protocol(protocol)
    live._validate_prompt_bundle(bundle)
    live._validate_reviewer_prompt(prompt, read("reviewer_failure_policy"))
    live.phase3_v3_live._validate_role_limits(limits, protocol)
    inventory = live.phase3_main_runner.build_main_inventory(protocol, root)
    transcripts = read("main_transcript_bundle")
    live._validate_transcript_inputs(protocol=protocol, inventory=inventory,
        bundle=transcripts, verification=read("transcript_verification"))
    excluded = live._validate_context_blocklist(read("context_blocklist"), protocol=protocol,
        inventory=inventory, prompt_bundle=bundle, role_limits=limits, transcript_bundle=transcripts)
    identity = live.phase3_main_runner.MainRunIdentity(run_id=manifest["run_id"],
        manifest_sha256=validated["manifest_canonical_sha256"],
        artifact_root=Path(validated["artifact_root"]),
        identity_registry_root=Path(validated["identity_registry_root"]))
    context = OfflinePreparationContext(project_root=root, manifest_path=manifest_path,
        authorization_path=authorization_path, manifest=manifest, authorization=authorization,
        authorization_raw_sha256=live._raw_sha256(authorization_path),
        authorization_signature_raw_sha256=live._raw_sha256(str(authorization_path) + ".sig"),
        input_paths=inputs, identity=identity, protocol=protocol, prompt_bundle=bundle,
        reviewer_prompt=prompt, role_limits=limits, inventory=inventory,
        context_excluded_cell_keys=excluded, prior_recovery=prior,
        prior_recovery_path=Path(prior_recovery_path).resolve())
    context.proposed_code_bindings = [_binding(root / name) for name in
        ("rejudge/phase3_main_live.py", "rejudge/phase3_main_missing_reviewer_wave.py",
         "rejudge/reviewer_cli_probe.py", "scripts/codex_reviewer_batch.py",
         "rejudge/phase3_main_capacity_execution.py")]
    return context


def _rows(path):
    from rejudge.phase3_main_live import _parse_strict_json
    raw = Path(path).read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise MissingWaveError("saved store ends with an incomplete line")
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            raise MissingWaveError("saved store contains a blank line")
        row = _parse_strict_json(line, Path(path))
        if not isinstance(row, dict):
            raise MissingWaveError("saved store contains a non-object row")
        rows.append(row)
    return raw, rows


def _frontier(paths, wave):
    _, logs = _rows(paths.run_log)
    reservations = [row for row in logs if row.get("event") == "reviewer_usage_reserved"]
    seen, cumulative = set(), 0
    for row in reservations:
        number, quantity = row.get("wave"), row.get("dispatches_this_wave")
        if number in seen or type(quantity) is not int or quantity <= 0:
            raise MissingWaveError("duplicate or invalid reviewer allocation")
        seen.add(number)
        cumulative += quantity
        if row.get("cumulative_reviewer_dispatches") != cumulative:
            raise MissingWaveError("cumulative reviewer allocation changed")
    if (not reservations or reservations[-1].get("wave") != wave
            or logs[-1] != reservations[-1]):
        raise MissingWaveError("missing wave must be the latest untouched allocation")
    starts = [row for row in logs if row.get("event") == "formal_main_pass_started"
              and row.get("pass_index") == wave]
    completed = [row for row in logs if row.get("event") == "formal_main_pass_complete"
                 and row.get("pass_index") == wave]
    if len(starts) != 1 or len(completed) != 1:
        raise MissingWaveError("missing wave lacks one completed provider pass")
    start, result = starts[0], completed[0]
    if logs.index(start) >= logs.index(result) or logs.index(result) >= len(logs) - 1:
        raise MissingWaveError("provider pass and allocation order changed")
    quantity = reservations[-1]["dispatches_this_wave"]
    if (result.get("pending_payloads") != quantity
            or result.get("abandoned_this_pass") != 0
            or result.get("retry_deferred_this_pass") != 0
            or type(result.get("completed_this_pass")) is not int
            or result["completed_this_pass"] < 0
            or result.get("attempted_this_pass") != quantity + result["completed_this_pass"]):
        raise MissingWaveError("only a clean, fully saved provider pass can reconstruct this wave")
    _, index = _rows(paths.reviewer_index)
    if any(row.get("wave") == wave for row in index):
        raise MissingWaveError("reviewer wave is already indexed")
    if any(Path(paths.review_packets_root).glob(f"wave-{wave:03d}-*")):
        raise MissingWaveError("reviewer wave already has packet evidence")
    return start, result, reservations[-1]


@dataclass(frozen=True)
class ReconstructedWave:
    wave: int
    payloads: tuple[dict[str, Any], ...]
    receipt: dict[str, Any]
    receipt_path: Path


def load_reconstruction(receipt_path):
    """Reload an offline proof after review, requiring its exact exported worklist."""
    receipt_path = Path(receipt_path).resolve()
    receipt = _json(receipt_path)
    if (receipt.get("schema_version") != "phase3_missing_reviewer_wave_replay_v1"
            or receipt.get("passed") is not True
            or _binding(receipt["worklist"]["path"]) != receipt["worklist"]):
        raise MissingWaveError("saved replay proof or worklist changed")
    payloads = tuple({key: item[key] for key in
        ("payload_sha256", "query", "candidate_a", "candidate_b")}
        for item in _json(receipt["worklist"]["path"])["items"])
    if canonical_sha256(payloads) != receipt["pending_payloads_canonical_sha256"]:
        raise MissingWaveError("saved pending payloads differ from replay proof")
    return ReconstructedWave(receipt["wave"], payloads, receipt, receipt_path)


def replay_missing_reviewer_wave(context, *, wave, scratch_root, held_run_lease):
    """Replay one clean pass into scratch, preserving every original artifact byte."""
    from rejudge import phase3_main_live as live
    paths = context.identity.paths
    live.phase3_main_reviewer_commit.require_held_run_lease(held_run_lease, expected_path=paths.lease)
    start, complete, reservation = _frontier(paths, wave)
    scratch = Path(scratch_root).resolve()
    if scratch.is_relative_to(paths.root.resolve()) or scratch.is_relative_to(context.project_root.resolve()):
        raise MissingWaveError("offline replay scratch must be outside source and formal artifacts")
    scratch.mkdir(parents=True, exist_ok=False)
    keys = ("results", "decisions", "reviewer_index", "run_log", "request_journal",
            "usage_ledger", "terminal_dispositions")
    bindings = {key: _binding(getattr(paths, key)) for key in keys}
    original, rows = _rows(paths.results)
    count = complete["completed_this_pass"]
    if complete.get("rows_complete") != len(rows) or count > len(rows):
        raise MissingWaveError("saved result suffix does not match the completed pass")
    before_count = len(rows) - count
    original_lines = original.splitlines(keepends=True)
    replay_results, replay_decisions = scratch / "results.jsonl", scratch / "decisions.jsonl"
    replay_results.write_bytes(b"".join(original_lines[:before_count]))
    replay_decisions.write_bytes(Path(paths.decisions).read_bytes())
    CellResultStore(paths.results)  # Validate the preserved result hash chain without writing it.
    journal = RequestJournal(paths.request_journal,
        execution_identity=context.identity.journal_execution_identity)
    if journal.dispatch_marker_paths():
        raise MissingWaveError("offline missing-wave reconstruction requires no active dispatch markers")
    client = JournalOnlyClient(journal)
    terminal = {row["cell_key"] for row in _rows(paths.terminal_dispositions)[1]}
    omitted = set(context.context_excluded_cell_keys) | terminal
    cells = live.phase3_runner.resolve_main_cells(context.inventory.cells,
        protocol=context.protocol, bundle=context.prompt_bundle)
    outcome = run_canary(results_path=replay_results, decisions_path=replay_decisions,
        client=client, reviewer=live._PauseModeReviewer(), anchor_judge_model="",
        protocol=dict(context.protocol), bundle=dict(context.prompt_bundle),
        pause_when_unlabeled=True, cells=[cell for cell in cells if cell.cell_key not in omitted],
        max_workers=start["provider_worker_concurrency"],
        block_size=context.prior_recovery["block_size"], model_caps=start["provider_model_limits"],
        transcript_generation_forbidden=True, namespace=context.protocol["cell_key_namespace"],
        pending_payload_limit=reservation["dispatches_this_wave"],
        role_limits=dict(context.role_limits), fatal_unknown_charge=False)
    if (client.failure is not None or outcome.halted_reason is not None or outcome.abandoned
            or outcome.retry_deferred or outcome.completed != count
            or outcome.attempted != complete["attempted_this_pass"]
            or len(outcome.pending_payloads) != reservation["dispatches_this_wave"]):
        raise MissingWaveError("journal-only replay differs from the durable clean pass")
    replay_raw, replay_rows = _rows(replay_results)
    if len(replay_rows) != len(rows) or not replay_raw.startswith(b"".join(original_lines[:before_count])):
        raise MissingWaveError("offline replay changed the original result prefix or suffix size")
    differences = []
    for original_row, replay_row in zip(rows[before_count:], replay_rows[before_count:]):
        if original_row["cell_key"] != replay_row["cell_key"]:
            raise MissingWaveError("offline replay completed another cell or changed row order")
        expected, observed = dict(original_row["result"]), dict(replay_row["result"])
        metadata = {key: {"original": expected.pop(key), "replay": observed.pop(key)}
                    for key in ("created_at", "harness_version")}
        if expected != observed:
            raise MissingWaveError("offline replay changed a saved scientific result field")
        differences.append({"cell_key": original_row["cell_key"], "operational_metadata": metadata,
                            "result_without_operational_metadata_sha256": canonical_sha256(expected)})
    if replay_decisions.read_bytes() != Path(paths.decisions).read_bytes():
        raise MissingWaveError("offline replay changed reviewer decisions")
    payloads = tuple(dict(item) for item in outcome.pending_payloads)
    if len({item["payload_sha256"] for item in payloads}) != len(payloads):
        raise MissingWaveError("offline replay repeated a pending payload")
    worklist_path = scratch / "WORKLIST.json"
    worklist = export_reviewer_worklist(payloads, context.reviewer_prompt["prompt"], worklist_path)
    if any(_binding(value["path"]) != value for value in bindings.values()):
        raise MissingWaveError("a saved store changed during offline replay")
    receipt = {"schema_version": "phase3_missing_reviewer_wave_replay_v1", "passed": True,
        "run_id": context.identity.run_id, "manifest_sha256": context.identity.manifest_sha256,
        "wave": wave, "reservation": reservation, "provider_pass_started": start,
        "provider_pass_completed": complete, "preserved_stores": bindings,
        "payload_sha256s": [item["payload_sha256"] for item in payloads],
        "pending_payloads_canonical_sha256": canonical_sha256(payloads),
        "worklist": _binding(worklist_path), "completed_result_comparison": differences,
        "journal_requests_replayed": len(client.used),
        "journal_bindings_canonical_sha256": canonical_sha256([client.used[key] for key in sorted(client.used)]),
        "proposed_code_bindings": context.proposed_code_bindings,
        "provider_dispatches": 0, "reviewer_dispatches": 0, "new_reviewer_allocations": 0,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat()}
    receipt_path = scratch / "replay-validation.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ReconstructedWave(wave, payloads, receipt, receipt_path)


def prepare_missing_reviewer_wave(context, reconstruction, *, held_run_lease):
    """Publish ordinary packets only after a reviewed replay; never dispatch or reserve again."""
    from rejudge import phase3_main_live as live
    paths = context.identity.paths
    live.phase3_main_reviewer_commit.require_held_run_lease(held_run_lease, expected_path=paths.lease)
    receipt = reconstruction.receipt
    if (_json(reconstruction.receipt_path) != receipt or receipt.get("passed") is not True
            or receipt["run_id"] != context.identity.run_id
            or receipt["manifest_sha256"] != context.identity.manifest_sha256
            or receipt["wave"] != reconstruction.wave
            or receipt["pending_payloads_canonical_sha256"] != canonical_sha256(reconstruction.payloads)):
        raise MissingWaveError("reviewed replay receipt or pending payloads changed")
    if any(_binding(value["path"]) != value for value in
           [*receipt["preserved_stores"].values(), *receipt["proposed_code_bindings"], receipt["worklist"]]):
        raise MissingWaveError("reviewed replay inputs or proposed code changed")
    _, _, reservation = _frontier(paths, reconstruction.wave)
    if reservation != receipt["reservation"]:
        raise MissingWaveError("reviewer allocation changed after replay")
    authority = live._load_authenticated_owner_authorization(context.authorization_path)
    if authority != context.authorization:
        raise MissingWaveError("original reviewer authorization changed")
    live.phase3_main_manifest.validate_main_authorization(
        authority, context.manifest, as_of=datetime.now(timezone.utc))
    read = lambda name: live._load_bound_input_object(context.manifest, context.input_paths, name, name)
    plan, result = read("capacity_plan"), read("capacity_result")
    live._reopen_capacity_execution_provenance_inputs(context.manifest, context.input_paths)
    validation = live._validate_capacity(plan=plan, result=result,
        history_path=context.input_paths["capacity_dispatch_history"], as_of=datetime.now(timezone.utc),
        require_current_freshness=False, plan_path=context.input_paths["capacity_plan"],
        result_path=context.input_paths["capacity_result"],
        execution_manifest_path=context.input_paths["capacity_execution_manifest"],
        execution_authorization_path=context.input_paths["capacity_execution_authorization"],
        execution_authorization_signature_path=context.input_paths["capacity_execution_authorization_signature"],
        historical_code_bindings=live.phase3_main_reviewer_recovery.historical_capacity_code_bindings(
            context.prior_recovery))
    validation = {**validation, "runtime": live._validate_capacity_runtime_binding(
        plan=plan, result=result, runtime=context.manifest["runtime"])}
    packet_dir = live._write_reviewer_wave_packets(context, reconstruction.payloads,
        wave=reconstruction.wave, held_run_lease=held_run_lease,
        validated_boundary=(authority, plan, validation))
    if (packet_dir / "WORKLIST.json").read_bytes() != Path(receipt["worklist"]["path"]).read_bytes():
        raise MissingWaveError("ordinary packet writer changed the reconstructed worklist")
    return packet_dir
