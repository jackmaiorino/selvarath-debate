"""Signed operational recovery of an unchanged Phase 3 scientific run.

The original manifest and authorization stay immutable. This small supplement replaces
their no-resume and serial-dispatch restrictions after an environmental interruption.
It preserves completed bytes, scientific inputs, and the original aggregate spend cap.
The live runner must hold its exclusive run lease before validating or applying recovery.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from rejudge.phase2_execution import canonical_sha256
from rejudge import phase3_main_authorization
from rejudge.request_journal import JournalDispatchUnresolved, journal_key, validate_unreserved_dispatch


RECOVERY_SCHEMA = "phase3_main_operational_recovery_v1"
RECOVERY_SIGNATURE_NAMESPACE = "selvarath-phase3-main-recovery-v1"
CONCURRENCY_POLICY = "promote_after_100_clean_calls_reduce_on_unknown_v1"
APPEND_ONLY_OUTPUTS = (
    "results", "usage_ledger", "request_journal", "decisions", "reviewer_index",
    "terminal_dispositions", "provider_error_log", "run_log",
)
RECOVERY_CONTROLS = {
    "preserve_completed_results": True,
    "replay_saved_responses_exactly": True,
    "charge_interrupted_requests_at_full_reservation": True,
    "scientific_inputs_and_analysis_gates_unchanged": True,
    "no_response_selection": True,
    "amends_original_no_resume": True,
    "amends_original_provider_worker_concurrency": True,
}


class RecoveryError(ValueError):
    """A recovery would change prior work or exceed its signed scope."""


def _read_json(path: Path) -> dict[str, Any]:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise RecoveryError(f"duplicate JSON key: {key}")
            value[key] = item
        return value
    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"cannot read recovery input: {path}") from exc
    if not isinstance(value, dict):
        raise RecoveryError(f"recovery input is not an object: {path}")
    return value


def _file_binding(path: str | Path, *, optional: bool = False) -> dict[str, Any]:
    source = Path(path).resolve()
    if optional and not source.exists():
        return {"path": source.as_posix(), "size_bytes": 0,
                "raw_sha256": hashlib.sha256(b"").hexdigest(), "exists": False}
    try:
        before = source.stat()
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        after = source.stat()
    except OSError as exc:
        raise RecoveryError(f"cannot snapshot recovery input: {source}") from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RecoveryError(f"recovery input changed during snapshot: {source}")
    return {"path": source.as_posix(), "size_bytes": after.st_size,
            "raw_sha256": digest.hexdigest(), "exists": True}


def _verify_binding(binding: Mapping[str, Any], *, allow_growth: bool) -> None:
    source = Path(binding["path"])
    length = binding.get("size_bytes")
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        raise RecoveryError(f"invalid preserved byte count: {source}")
    if not source.exists():
        if binding.get("exists") is False and length == 0:
            return
        raise RecoveryError(f"preserved recovery input disappeared: {source}")
    if not allow_growth and _file_binding(source) != dict(binding):
        raise RecoveryError(f"recovery input changed: {source}")
    try:
        digest = hashlib.sha256()
        remaining = length
        with source.open("rb") as handle:
            while remaining:
                chunk = handle.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise RecoveryError(f"preserved recovery input was truncated: {source}")
                digest.update(chunk)
                remaining -= len(chunk)
    except OSError as exc:
        raise RecoveryError(f"cannot validate recovery input: {source}") from exc
    if digest.hexdigest() != binding["raw_sha256"]:
        raise RecoveryError(f"preserved recovery bytes changed: {source}")


def _scientific_contract(manifest: Mapping[str, Any]) -> dict[str, Any]:
    runtime = dict(manifest["runtime"])
    runtime.pop("provider_worker_concurrency", None)
    return {key: manifest[key] for key in ("input_bindings", "seeds", "inventory")} | {
        "runtime": runtime, "stage_cap_usd": manifest["spend"]["stage_cap_usd"]}


def _tree_binding(path: Path) -> dict[str, Any]:
    root = path.resolve()
    files = sorted(item for item in root.rglob("*") if item.is_file())
    bindings = []
    for item in files:
        binding = _file_binding(item)
        bindings.append({"path": item.relative_to(root).as_posix(),
                         "size_bytes": binding["size_bytes"], "raw_sha256": binding["raw_sha256"]})
    if files != sorted(item for item in root.rglob("*") if item.is_file()):
        raise RecoveryError("review packet tree changed during recovery snapshot")
    return {"path": root.as_posix(), "file_count": len(files),
            "size_bytes": sum(item["size_bytes"] for item in bindings),
            "tree_sha256": canonical_sha256(bindings)}


def _packet_snapshots(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not root.exists():
        return [], []
    committed = [path for path in sorted(root.iterdir())
                 if path.is_dir() and (path / "WAVE_COMMIT_RECEIPT.json").is_file()]
    trees = [_tree_binding(path) for path in committed]
    files = [_file_binding(path) for path in sorted(root.rglob("*"))
             if path.is_file() and not any(path.is_relative_to(directory) for directory in committed)]
    return trees, files


def _identity_paths(manifest: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    registry = Path(manifest["output_contract"]["identity_registry_root"]) / "identities"
    stem = f"{manifest['run_id']}-{canonical_sha256(manifest)}"
    return tuple(registry / f"{stem}.{suffix}.json" for suffix in
                 ("started", "completed", "voided"))  # type: ignore[return-value]


def _require_recoverable(manifest: Mapping[str, Any]) -> Path:
    started, completed, voided = _identity_paths(manifest)
    if completed.exists() or Path(manifest["output_contract"]["paths"]["completion"]).exists():
        raise RecoveryError("completed runs cannot resume provider dispatch")
    if voided.exists():
        raise RecoveryError("voided runs cannot be recovered")
    if not started.is_file():
        raise RecoveryError("recovery requires an already started scientific run")
    return started


def _ledger_events(path: Path, size_bytes: int | None = None) -> list[dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            raw = handle.read() if size_bytes is None else handle.read(size_bytes)
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("recovery ledger has an incomplete or malformed row") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise RecoveryError("recovery ledger rows must be objects")
    return rows


def _interrupted_dispatches(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    paths = manifest["output_contract"]["paths"]
    journal = Path(paths["request_journal"])
    legacy = journal.with_name(journal.name + ".unresolved.json")
    marker_paths = ([legacy] if legacy.exists() else [])
    # Concurrent runners use one durable marker per call in a sibling directory.
    marker_paths.extend(sorted(journal.parent.glob(journal.name + ".dispatch-*.json")))
    rows = _ledger_events(Path(paths["usage_ledger"]))
    reservations = {row["attempt_id"]: row for row in rows if row.get("status") == "reserved"}
    terminated = {row["attempt_id"] for row in rows if row.get("status") in
                  {"success", "unknown_charge", "charged_malformed", "released_no_charge"}}
    successes = {row["attempt_id"]: row for row in rows if row.get("status") == "success"}
    journal_rows = _ledger_events(journal) if journal.exists() else []
    result = []
    for marker_path in marker_paths:
        marker = _read_json(marker_path)
        matching = []
        for attempt_id, reservation in reservations.items():
            metadata = reservation.get("metadata", {})
            key = journal_key(metadata)
            if ((attempt_id not in terminated or attempt_id in successes)
                    and all(getattr(key, field) == marker.get(field)
                            for field in ("cell_key", "call_role", "slot", "attempt"))
                    and metadata.get("journal_request_sha256") == marker.get("request_sha256")):
                matching.append(reservation)
        if not matching:
            _require_unreserved_marker(rows, journal_rows, marker)
            if not rows:
                raise RecoveryError("unreserved recovery requires its original ledger frontier")
            result.append({"marker_path": marker_path.resolve().as_posix(),
                           "marker_raw_sha256": _file_binding(marker_path)["raw_sha256"],
                           "marker": marker, "request_sha256": marker["request_sha256"],
                           "recovery_ledger_boundary": {key: rows[-1][key] for key in
                                                        ("ledger_id", "sequence", "event_hash")},
                           "disposition": "unreserved_dispatch"})
            continue
        if len(matching) != 1:
            raise RecoveryError("interrupted marker does not match exactly one recoverable reservation")
        reservation = matching[0]
        item = {"marker_path": marker_path.resolve().as_posix(),
                "marker_raw_sha256": _file_binding(marker_path)["raw_sha256"],
                "marker": marker, "reservation": reservation,
                "reservation_attempt_id": reservation["attempt_id"],
                "request_sha256": marker["request_sha256"],
                "disposition": "conservative_unknown_charge"}
        if reservation["attempt_id"] in successes:
            item.update(disposition="completed_journaled_response",
                        terminal=successes[reservation["attempt_id"]],
                        journal_entry=_journal_entry_binding(journal_rows, marker))
        result.append(item)
    covered = {item["reservation_attempt_id"] for item in result
               if item["disposition"] == "conservative_unknown_charge"}
    if set(reservations) - terminated != covered:
        raise RecoveryError("open reservations are not fully covered by interrupted markers")
    return result


def _journal_entry_binding(rows: list[dict[str, Any]], marker: Mapping[str, Any]) -> dict[str, Any]:
    matches = [row for row in rows if all(row.get(field) == marker.get(field) for field in
               ("cell_key", "call_role", "slot", "attempt", "request_sha256"))]
    if len(matches) != 1 or not isinstance(matches[0].get("response"), str):
        raise RecoveryError("completed marker has no exact durable journaled response")
    row = matches[0]
    return {"sequence": row["sequence"], "event_hash": row["event_hash"],
            "request_sha256": row["request_sha256"],
            "response_raw_sha256": hashlib.sha256(row["response"].encode("utf-8")).hexdigest()}


def _require_unreserved_marker(events, journal_rows, marker) -> None:
    try:
        validate_unreserved_dispatch(events, marker)
    except (ValueError, JournalDispatchUnresolved) as exc:
        raise RecoveryError(f"unreserved marker cannot be reconciled: {exc}") from exc
    if any(all(row.get(field) == marker.get(field) for field in
               ("cell_key", "call_role", "slot", "attempt")) for row in journal_rows):
        raise RecoveryError("unreserved marker already has a durable journal entry")


def build_recovery_manifest(
    manifest_path: str | Path, authorization_path: str | Path, *,
    execution_source_commit: str, provider_worker_concurrency: int,
    per_model_limits: Mapping[str, int], block_size: int, recorded_at_utc: str,
    reason: str, owner_instruction: str, validation_record: str | Path | None = None,
    initial_per_model_limits: Mapping[str, int] | None = None,
    concurrency_policy: str = CONCURRENCY_POLICY,
    reviewer_transport_repair: Mapping[str, Any] | None = None,
    provider_retry_backoff_policy: str | None = None,
) -> dict[str, Any]:
    """Prepare exact unsigned recovery bytes without modifying archived work."""
    manifest_file, authorization_file = Path(manifest_path).resolve(), Path(authorization_path).resolve()
    manifest, authorization = _read_json(manifest_file), _read_json(authorization_file)
    started = _require_recoverable(manifest)
    paths = manifest["output_contract"]["paths"]
    packet_trees, packet_files = _packet_snapshots(Path(paths["review_packets_root"]))
    recovery = {
        "schema_version": RECOVERY_SCHEMA, "run_id": manifest["run_id"],
        "recorded_at_utc": recorded_at_utc, "reason": reason,
        "owner_instruction": owner_instruction,
        "original_manifest": _file_binding(manifest_file),
        "original_manifest_canonical_sha256": canonical_sha256(manifest),
        "original_authorization": _file_binding(authorization_file),
        "original_authorization_signature": _file_binding(str(authorization_file) + ".sig"),
        "execution_source_commit": execution_source_commit,
        "scientific_contract_sha256": canonical_sha256(_scientific_contract(manifest)),
        "artifact_root": str(manifest["output_contract"]["artifact_root"]),
        "stage_cap_usd": authorization["stage_cap_usd"],
        "provider_worker_concurrency": provider_worker_concurrency,
        "per_model_limits": dict(per_model_limits), "block_size": block_size,
        "initial_per_model_limits": dict(initial_per_model_limits or per_model_limits),
        "concurrency_policy": concurrency_policy,
        "controls": dict(RECOVERY_CONTROLS),
        "immutable_artifacts": {"identity_binding": _file_binding(paths["identity_binding"]),
                                "identity_start": _file_binding(started)},
        "preserved_prefixes": {key: _file_binding(paths[key], optional=True)
                               for key in APPEND_ONLY_OUTPUTS},
        "review_packet_trees": packet_trees,
        "review_packet_files": packet_files,
        "checkpoint_snapshots": {key: _file_binding(path, optional=True) for key, path in {
            "usage_state": str(paths["usage_ledger"]) + ".state.json",
            "reviewer_worklist": paths["reviewer_worklist"],
            "active_marker": paths["active_marker"],
        }.items()},
        "interrupted_dispatches": _interrupted_dispatches(manifest),
        "validation_record": None if validation_record is None else _file_binding(validation_record),
    }
    if provider_retry_backoff_policy is not None:
        recovery["provider_retry_backoff_policy"] = provider_retry_backoff_policy
    if reviewer_transport_repair is not None:
        from rejudge.phase3_main_reviewer_recovery import prepare_transport_repair
        recovery["reviewer_transport_repair"] = prepare_transport_repair(
            reviewer_transport_repair, manifest)
    validate_recovery_manifest(recovery, manifest_path=manifest_file,
                              authorization_path=authorization_file, allow_growth=False)
    return recovery


def validate_recovery_manifest(
    recovery: Mapping[str, Any], *, manifest_path: str | Path,
    authorization_path: str | Path, current_source_commit: str | None = None,
    allow_growth: bool = True, verify_artifacts: bool = True,
    require_recoverable: bool = True,
) -> dict[str, Any]:
    """Validate signed scope and preserved prefixes; later appends are permitted.

    This does not replace the runner's ledger-chain, journal, result-store, or budget
    validators. It only verifies the operational amendment and original byte prefixes.
    """
    manifest = _read_json(Path(manifest_path))
    authorization = _read_json(Path(authorization_path))
    if recovery.get("schema_version") != RECOVERY_SCHEMA:
        raise RecoveryError("unsupported recovery schema")
    if require_recoverable:
        _require_recoverable(manifest)
    if (recovery.get("run_id") != manifest["run_id"]
            or authorization.get("run_id") != manifest["run_id"]
            or recovery.get("original_manifest_canonical_sha256") != canonical_sha256(manifest)
            or recovery.get("artifact_root") != manifest["output_contract"]["artifact_root"]):
        raise RecoveryError("recovery must preserve the original run and output identity")
    expected_paths = {"original_manifest": Path(manifest_path),
                      "original_authorization": Path(authorization_path),
                      "original_authorization_signature": Path(str(authorization_path) + ".sig")}
    for key, path in expected_paths.items():
        binding = recovery[key]
        if Path(binding["path"]).resolve() != path.resolve():
            raise RecoveryError(f"recovery binds another {key}")
        _verify_binding(binding, allow_growth=False)
    if recovery.get("scientific_contract_sha256") != canonical_sha256(_scientific_contract(manifest)):
        raise RecoveryError("recovery changes scientific inputs, seeds, models, or analysis gates")
    cap = Decimal(str(recovery.get("stage_cap_usd")))
    if (not cap.is_finite() or cap <= 0 or cap > Decimal("1100")
            or cap != Decimal(str(authorization["stage_cap_usd"]))
            or cap != Decimal(str(manifest["spend"]["stage_cap_usd"]))):
        raise RecoveryError("recovery may not increase the original aggregate spend cap")
    source_commit = recovery.get("execution_source_commit")
    if not isinstance(source_commit, str) or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise RecoveryError("recovery execution source commit is invalid")
    if current_source_commit is not None and source_commit != current_source_commit:
        raise RecoveryError("checkout differs from the signed recovery execution commit")
    workers, block = recovery.get("provider_worker_concurrency"), recovery.get("block_size")
    if (type(workers) is not int or not 1 <= workers <= 32
            or type(block) is not int or not workers <= block <= 128):
        raise RecoveryError("recovery concurrency or block size is invalid")
    limits = recovery.get("per_model_limits")
    if (not isinstance(limits, dict) or set(limits) != set(manifest["runtime"]["model_ids"])
            or any(type(value) is not int or not 1 <= value <= workers for value in limits.values())):
        raise RecoveryError("recovery per-model limits must bound the unchanged model roster")
    initial = recovery.get("initial_per_model_limits")
    if (not isinstance(initial, dict) or set(initial) != set(limits)
            or any(type(value) is not int or not 1 <= value <= limits[model]
                   for model, value in initial.items())
            or recovery.get("concurrency_policy") != CONCURRENCY_POLICY):
        raise RecoveryError("adaptive concurrency must stay within the signed per-model limits")
    if recovery.get("controls") != RECOVERY_CONTROLS:
        raise RecoveryError("recovery preservation controls changed")
    if "provider_retry_backoff_policy" in recovery:
        from rejudge.phase3_main_retry_backoff import POLICY
        if recovery["provider_retry_backoff_policy"] != POLICY:
            raise RecoveryError("unsupported provider retry backoff policy")
    if recovery.get("reviewer_transport_repair") is not None:
        from rejudge.phase3_main_reviewer_recovery import validate_transport_repair
        try:
            validate_transport_repair(recovery["reviewer_transport_repair"], manifest,
                                     verify_artifacts=verify_artifacts)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise RecoveryError(f"reviewer transport repair failed: {exc}") from exc
    if any(not isinstance(recovery.get(key), str) or not recovery[key].strip()
           for key in ("reason", "owner_instruction")):
        raise RecoveryError("recovery must record its reason and the owner's instruction")
    try:
        recorded = datetime.fromisoformat(recovery["recorded_at_utc"].replace("Z", "+00:00"))
        if recorded.utcoffset() is None:
            raise ValueError("timezone required")
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryError("recovery timestamp must include a timezone") from exc
    if not verify_artifacts:
        return dict(recovery) | {"recovery_manifest_sha256": canonical_sha256(recovery)}
    paths = manifest["output_contract"]["paths"]
    immutable = recovery.get("immutable_artifacts", {})
    if set(immutable) != {"identity_binding", "identity_start"}:
        raise RecoveryError("recovery must preserve the identity binding and start record")
    expected_immutable = {"identity_binding": Path(paths["identity_binding"]),
                          "identity_start": _identity_paths(manifest)[0]}
    for key, binding in immutable.items():
        if Path(binding["path"]).resolve() != expected_immutable[key].resolve():
            raise RecoveryError("recovery points at another identity artifact")
        _verify_binding(binding, allow_growth=False)
    prefixes = recovery.get("preserved_prefixes", {})
    if set(prefixes) != set(APPEND_ONLY_OUTPUTS):
        raise RecoveryError("recovery must preserve all append-only outputs")
    for key, binding in prefixes.items():
        if Path(binding["path"]).resolve() != Path(paths[key]).resolve():
            raise RecoveryError("recovery points at another output artifact")
        _verify_binding(binding, allow_growth=allow_growth)
    packet_root = Path(paths["review_packets_root"]).resolve()
    for binding in recovery.get("review_packet_trees", []):
        packet_path = Path(binding["path"]).resolve()
        if not packet_path.is_relative_to(packet_root) or _tree_binding(packet_path) != binding:
            raise RecoveryError("completed review packet tree changed")
    for binding in recovery.get("review_packet_files", []):
        if not Path(binding["path"]).resolve().is_relative_to(packet_root):
            raise RecoveryError("recovery packet snapshot lies outside the original packet root")
        partial_rulings = {
            Path(wave["rulings_prefix"]["path"]).resolve()
            for wave in (recovery.get("reviewer_transport_repair") or {}).get("partial_waves", [])}
        _verify_binding(binding, allow_growth=allow_growth and Path(binding["path"]).resolve() in partial_rulings)
    if not allow_growth:
        for binding in recovery.get("checkpoint_snapshots", {}).values():
            _verify_binding(binding, allow_growth=False)
    events = _ledger_events(Path(paths["usage_ledger"]))
    journal_rows = _ledger_events(Path(paths["request_journal"])) if Path(paths["request_journal"]).exists() else []
    recovery_sha = canonical_sha256(recovery)
    for interrupted in recovery.get("interrupted_dispatches", []):
        marker_path = Path(interrupted["marker_path"])
        journal_path = Path(paths["request_journal"]).resolve()
        if (marker_path.resolve().parent != journal_path.parent
                or not (marker_path.name == journal_path.name + ".unresolved.json"
                        or marker_path.name.startswith(journal_path.name + ".dispatch-"))):
            raise RecoveryError("interrupted marker belongs to another journal")
        if marker_path.exists() and (hashlib.sha256(marker_path.read_bytes()).hexdigest()
                                    != interrupted["marker_raw_sha256"]
                                    or _read_json(marker_path) != interrupted["marker"]):
            raise RecoveryError("interrupted marker changed before recovery")
        if interrupted.get("disposition") == "unreserved_dispatch":
            if ("reservation" in interrupted or "reservation_attempt_id" in interrupted
                    or interrupted.get("request_sha256") != interrupted["marker"].get("request_sha256")):
                raise RecoveryError("unreserved marker contains contradictory reservation evidence")
            original_events = _ledger_events(Path(paths["usage_ledger"]),
                                             prefixes["usage_ledger"]["size_bytes"])
            expected_boundary = ({key: original_events[-1][key] for key in
                                  ("ledger_id", "sequence", "event_hash")} if original_events else None)
            if interrupted.get("recovery_ledger_boundary") != expected_boundary or expected_boundary is None:
                raise RecoveryError("unreserved recovery frontier differs from its preserved ledger prefix")
            if marker_path.exists():
                _require_unreserved_marker(events, journal_rows, interrupted["marker"])
            else:
                original_journal = _ledger_events(Path(paths["request_journal"]),
                    prefixes["request_journal"]["size_bytes"]) if Path(paths["request_journal"]).exists() else []
                _require_unreserved_marker(original_events, original_journal, interrupted["marker"])
            continue
        reservation = interrupted["reservation"]
        key = journal_key(reservation.get("metadata"))
        if (reservation not in events or reservation.get("status") != "reserved"
                or interrupted["reservation_attempt_id"] != reservation.get("attempt_id")
                or interrupted["request_sha256"] != interrupted["marker"].get("request_sha256")
                or reservation["metadata"].get("journal_request_sha256") != interrupted["request_sha256"]
                or any(getattr(key, field) != interrupted["marker"].get(field)
                       for field in ("cell_key", "call_role", "slot", "attempt"))):
            raise RecoveryError("interrupted dispatch reservation differs from its signed snapshot")
        terminal = [event for event in events if event.get("attempt_id") == reservation["attempt_id"]
                    and event.get("status") != "reserved"]
        disposition = interrupted.get("disposition", "conservative_unknown_charge")
        if disposition == "completed_journaled_response":
            if (len(terminal) != 1 or terminal[0].get("status") != "success"
                    or terminal[0] != interrupted.get("terminal")
                    or terminal[0].get("metadata") != reservation.get("metadata")
                    or _journal_entry_binding(journal_rows, interrupted["marker"])
                    != interrupted.get("journal_entry")):
                raise RecoveryError("completed marker differs from its saved success and response")
        elif disposition != "conservative_unknown_charge":
            raise RecoveryError("unsupported interrupted dispatch recovery disposition")
        elif terminal:
            if (not allow_growth or len(terminal) != 1
                    or terminal[0].get("status") != "unknown_charge"
                    or terminal[0].get("recovery_manifest_sha256") != recovery_sha
                    or Decimal(str(terminal[0].get("cost_usd"))) != Decimal(str(reservation["cost_usd"]))
                    or terminal[0].get("metadata") != reservation.get("metadata")):
                raise RecoveryError("interrupted dispatch lacks its exact conservative recovery charge")
        elif not marker_path.exists():
            raise RecoveryError("interrupted marker disappeared before conservative accounting")
    open_ids = {event["attempt_id"] for event in events if event.get("status") == "reserved"}
    open_ids -= {event["attempt_id"] for event in events if event.get("status") in
                 {"success", "unknown_charge", "charged_malformed", "released_no_charge"}}
    covered = {item["reservation_attempt_id"] for item in recovery.get("interrupted_dispatches", [])
               if "reservation_attempt_id" in item}
    if open_ids - covered:
        raise RecoveryError("new interrupted requests require a fresh recovery snapshot")
    if recovery.get("validation_record") is not None:
        _verify_binding(recovery["validation_record"], allow_growth=False)
    return dict(recovery) | {"recovery_manifest_sha256": recovery_sha}


def load_authenticated_recovery(path: str | Path, **validation_kwargs: Any) -> dict[str, Any]:
    """Verify the separate recovery signature before trusting its operational scope."""
    try:
        recovery = phase3_main_authorization.load_authenticated_owner_authorization(
            path, signature_namespace=RECOVERY_SIGNATURE_NAMESPACE)
    except phase3_main_authorization.MainAuthorizationSignatureError as exc:
        raise RecoveryError(f"recovery signature failed: {exc}") from exc
    validated = validate_recovery_manifest(recovery, **validation_kwargs)
    source = Path(path).resolve()
    return validated | {"recovery_path": source.as_posix(),
                        "recovery_raw_sha256": _file_binding(source)["raw_sha256"],
                        "recovery_signature_raw_sha256": _file_binding(str(source) + ".sig")["raw_sha256"]}
