"""Preserve a wholly unanswered quota-failed wave and authorize one fresh allocation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from rejudge.phase2_execution import canonical_sha256

POLICY = "quota_unavailable_zero_output_fresh_wave_v1"
FIELDS = {"policy", "wave", "replacement_wave", "packet_directory", "payload_sha256s",
          "attempted_payload_sha256s", "reserved_dispatches", "attempted_dispatches",
          "original_reservation", "wave_tree"}
_QUOTA_MESSAGE = re.compile(
    r"You've hit your usage limit\. Visit https://chatgpt\.com/codex/settings/usage "
    r"to purchase more credits or try again at (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"[0-9]{1,2}(?:st|nd|rd|th), [0-9]{4} [0-9]{1,2}:[0-9]{2} (?:AM|PM)\.")


def _json(path: Path) -> dict[str, Any]:
    from rejudge.phase3_main_recovery import _read_json
    return _read_json(path)


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()] if path.exists() else []


def validate_shape(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != FIELDS or value["policy"] != POLICY:
        raise ValueError("quota-abandoned reviewer wave fields or policy changed")
    if (type(value["wave"]) is not int or value["wave"] < 1
            or type(value["replacement_wave"]) is not int or value["replacement_wave"] <= value["wave"]):
        raise ValueError("quota replacement must advance to one fresh wave")
    all_payloads, attempted = value["payload_sha256s"], value["attempted_payload_sha256s"]
    if (not isinstance(all_payloads, list) or not isinstance(attempted, list) or not attempted
            or len(set(all_payloads)) != len(all_payloads) or len(set(attempted)) != len(attempted)
            or not set(attempted) <= set(all_payloads)
            or value["reserved_dispatches"] != len(all_payloads)
            or value["attempted_dispatches"] != len(attempted)
            or type(value["reserved_dispatches"]) is not int or type(value["attempted_dispatches"]) is not int):
        raise ValueError("quota-abandoned reviewer wave dispatch partition changed")


def build_quota_abandoned_wave(manifest: Mapping[str, Any], *, directory: str | Path,
                              wave: int, replacement_wave: int,
                              accepted_batch_runner_bindings: list[dict[str, Any]]) -> dict[str, Any]:
    from rejudge.phase3_main_recovery import _tree_binding
    from scripts import codex_reviewer_batch as batch
    root = Path(directory).resolve()
    paths = manifest["output_contract"]["paths"]
    if root.parent != Path(paths["review_packets_root"]).resolve():
        raise ValueError("quota-abandoned reviewer wave escaped the original packet root")
    if any(path.is_symlink() for path in (root, *root.rglob("*"))):
        raise ValueError("quota-abandoned reviewer evidence contains a link")
    if (root / "WAVE_COMMIT_INTENT.json").exists() or (root / "WAVE_COMMIT_RECEIPT.json").exists():
        raise ValueError("quota abandonment cannot discard a prepared or completed reviewer commit")
    if (root / "rulings.jsonl").read_bytes():
        raise ValueError("quota abandonment cannot discard any retained reviewer ruling")
    guard_path = root / "DISPATCH_GUARD.json"
    guard = _json(guard_path)
    guard_sha = hashlib.sha256(guard_path.read_bytes()).hexdigest()
    if guard.get("run_id") != manifest["run_id"]:
        raise ValueError("quota-abandoned reviewer guard belongs to another run")
    indexed = _rows(Path(paths["reviewer_index"]))
    if any(row.get("wave") == wave or row.get("packet_directory") == root.as_posix() for row in indexed):
        raise ValueError("quota abandonment cannot discard an indexed reviewer wave")
    log = _rows(Path(paths["run_log"]))
    reservations = [row for row in log if row.get("event") == "reviewer_usage_reserved" and row.get("wave") == wave]
    if len(reservations) != 1:
        raise ValueError("quota abandonment requires the original unique wave allocation")
    reservation = reservations[0]
    if any(row.get("event") in {"reviewer_usage_wave_completed", "reviewer_usage_wave_recovered"}
           and row.get("wave") == wave for row in log):
        raise ValueError("quota abandonment cannot discard a completed reviewer wave")
    payloads = [item["payload_sha256"] for item in guard["packet_bindings"]]
    if reservation["dispatches_this_wave"] != len(payloads):
        raise ValueError("quota wave allocation differs from its exact packets")
    attempted = []
    expected_reservations, expected_evidence = set(), set()
    runtime = manifest["runtime"]
    for item in guard["packet_bindings"]:
        packet = root / item["file"]
        evidence = batch._evidence_directory(packet)
        reserve = root / batch.DISPATCH_RESERVATION_DIRECTORY_NAME / f"{guard_sha}_{item['payload_sha256']}.json"
        if not reserve.exists() and not evidence.exists():
            continue
        if not reserve.is_file() or not evidence.is_dir():
            raise ValueError("quota-abandoned reviewer attempt lacks exact reservation/evidence")
        expected_reservations.add(reserve)
        expected_evidence.add(evidence)
        if {path.name for path in evidence.iterdir()} != {
                "invocation_receipt.json", "codex_events.jsonl", "codex_stderr.bin", "ruling.txt"}:
            raise ValueError("quota-abandoned reviewer attempt has unclassified evidence")
        receipt_path = evidence / "invocation_receipt.json"
        receipt_raw = receipt_path.read_bytes()
        reference = {"schema_version": batch.RULING_EVIDENCE_REFERENCE_SCHEMA,
                     "receipt_path": receipt_path.relative_to(root).as_posix(),
                     "receipt_raw_sha256": hashlib.sha256(receipt_raw).hexdigest(),
                     "receipt_byte_count": len(receipt_raw)}
        receipt = batch.validate_invocation_evidence(packet, reference,
            expected_model=runtime["reviewer_model"], expected_effort=runtime["reviewer_reasoning_effort"],
            expected_concurrency=runtime["reviewer_concurrency"],
            accepted_batch_runner_bindings=accepted_batch_runner_bindings)
        outcome = receipt["outcome"]
        events = _rows(evidence / "codex_events.jsonl")
        if (outcome["result_ok"] is not False or outcome["process_exit_code"] != 1
                or outcome["timed_out"] is not False or outcome["dispatch_attempted"] is not True
                or outcome["commands"] or (evidence / "ruling.txt").read_bytes()
                or [event.get("type") for event in events] != ["thread.started", "turn.started", "error", "turn.failed"]
                or set(events[0]) != {"type", "thread_id"} or set(events[1]) != {"type"}
                or set(events[2]) != {"type", "message"} or set(events[3]) != {"type", "error"}
                or not isinstance(events[2].get("message"), str)
                or _QUOTA_MESSAGE.fullmatch(events[2]["message"]) is None
                or events[3]["error"] != {"message": events[2]["message"]}):
            raise ValueError("reviewer attempt is not an exact zero-output quota refusal")
        attempted.append(item["payload_sha256"])
    if (set((root / batch.DISPATCH_RESERVATION_DIRECTORY_NAME).glob("*")) != expected_reservations
            or set((root / batch.EVIDENCE_DIRECTORY_NAME).glob("*")) != expected_evidence):
        raise ValueError("quota-abandoned wave contains unbound reservation or evidence")
    value = {"policy": POLICY, "wave": wave, "replacement_wave": replacement_wave,
             "packet_directory": root.as_posix(), "payload_sha256s": payloads,
             "attempted_payload_sha256s": attempted, "reserved_dispatches": len(payloads),
             "attempted_dispatches": len(attempted), "original_reservation": reservation,
             "wave_tree": _tree_binding(root)}
    validate_shape(value)
    return value


def validate_quota_abandoned_wave(value: Mapping[str, Any], manifest: Mapping[str, Any], *,
                                  accepted_batch_runner_bindings: list[dict[str, Any]]) -> None:
    validate_shape(value)
    rebuilt = build_quota_abandoned_wave(manifest, directory=value["packet_directory"], wave=value["wave"],
        replacement_wave=value["replacement_wave"], accepted_batch_runner_bindings=accepted_batch_runner_bindings)
    if rebuilt != dict(value):
        raise ValueError("quota-abandoned reviewer wave differs from its signed immutable proof")


def authenticated_quota_abandoned_waves(reviewer_recovery: Mapping[str, Any] | None, *,
                                       expected_run_id: str, expected_manifest_sha256: str) -> list[dict[str, Any]]:
    from rejudge.phase3_main_recovery import load_authenticated_recovery
    from rejudge.phase3_main_reviewer_recovery import accepted_batch_runner_bindings
    if not reviewer_recovery or not (reviewer_recovery.get("reviewer_transport_repair") or {}).get("quota_abandoned_waves"):
        return []
    path = Path(reviewer_recovery["recovery_path"])
    raw = _json(path)
    verified = load_authenticated_recovery(path, manifest_path=raw["original_manifest"]["path"],
        authorization_path=raw["original_authorization"]["path"], verify_artifacts=False,
        require_recoverable=False)
    if (verified["run_id"] != expected_run_id or verified["original_manifest_canonical_sha256"] != expected_manifest_sha256
            or any(verified[key] != reviewer_recovery[key] for key in (
                "recovery_manifest_sha256", "recovery_raw_sha256", "recovery_signature_raw_sha256"))):
        raise ValueError("quota-abandoned wave authority differs from its signed run")
    manifest = _json(Path(raw["original_manifest"]["path"]))
    records = verified["reviewer_transport_repair"]["quota_abandoned_waves"]
    for value in records:
        validate_quota_abandoned_wave(value, manifest,
            accepted_batch_runner_bindings=accepted_batch_runner_bindings(verified))
    return records
