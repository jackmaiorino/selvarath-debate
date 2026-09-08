"""Exact signed transport repair and continuation of an already reserved reviewer wave."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


TRANSPORT_REPAIR_POLICY = "retain_completed_reconnect_resume_unstarted_v1"
REPAIR_CODE_KEYS = {
    "scripts/codex_reviewer_batch.py": "reviewer_batch",
    "rejudge/phase3_main_capacity_execution.py": "capacity_execution",
}


def code_binding(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    raw = path.read_bytes()
    return {"path": path.as_posix(), "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "byte_count": len(raw)}


def _json(path: str | Path) -> dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate reviewer recovery field {key}")
            result[key] = value
        return result
    value = json.loads(Path(path).read_bytes(), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("reviewer recovery input must be an object")
    return value


def _binding(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"path", "raw_sha256", "byte_count"}:
        raise ValueError("reviewer code binding fields drifted")
    if (not isinstance(value["path"], str) or not Path(value["path"]).is_absolute()
            or Path(value["path"]).resolve().as_posix() != value["path"]
            or not isinstance(value["raw_sha256"], str) or len(value["raw_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in value["raw_sha256"])
            or type(value["byte_count"]) is not int or value["byte_count"] < 1):
        raise ValueError("reviewer code binding identity is invalid")
    return dict(value)


def build_transport_repair(manifest: Mapping[str, Any], *, project_root: str | Path,
                           partial_wave_directories: Mapping[int, str | Path]) -> dict[str, Any]:
    """Build metadata only; source and reviewer outputs are never modified or selected."""
    capacity = _json(manifest["input_bindings"]["capacity_execution_manifest"]["path"])
    root = Path(project_root).resolve()
    partial = []
    for wave, directory in partial_wave_directories.items():
        directory = Path(directory).resolve()
        guard = _json(directory / "DISPATCH_GUARD.json")
        guard_sha = code_binding(directory / "DISPATCH_GUARD.json")["raw_sha256"]
        retained, never = [], []
        for item in guard["packet_bindings"]:
            payload = item["payload_sha256"]
            reservation = directory / ".reviewer_dispatch_reservations" / f"{guard_sha}_{payload}.json"
            # The batch implementation verifies complete retained evidence before release.
            (retained if reservation.exists() else never).append(payload)
        partial.append({"wave": wave, "packet_directory": directory.as_posix(),
                        "retained_payload_sha256s": retained,
                        "never_started_payload_sha256s": never})
    return {"policy": TRANSPORT_REPAIR_POLICY, "code_replacements": {
        relative: {"historical": dict(capacity["code_bindings"][key]),
                   "replacement": code_binding(root / relative)}
        for relative, key in REPAIR_CODE_KEYS.items()}, "partial_waves": partial}


def prepare_transport_repair(value: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Add exact immutable wave bindings before the supplement is signed."""
    result = json.loads(json.dumps(value))
    packet_root = Path(manifest["output_contract"]["paths"]["review_packets_root"])
    result["historical_guards"] = [code_binding(path) for path in sorted(
        packet_root.glob("*/DISPATCH_GUARD.json"))]
    for wave in result["partial_waves"]:
        directory = Path(wave["packet_directory"])
        wave["dispatch_guard"] = code_binding(directory / "DISPATCH_GUARD.json")
        wave["worklist"] = code_binding(directory / "WORKLIST.json")
        wave["packet_index"] = code_binding(directory / "INDEX.json")
        wave["rulings_prefix"] = code_binding(directory / "rulings.jsonl")
        guard = _json(directory / "DISPATCH_GUARD.json")
        for item in guard["packet_bindings"]:
            payload = item["payload_sha256"]
            reservation = directory / ".reviewer_dispatch_reservations" / (
                f"{wave['dispatch_guard']['raw_sha256']}_{payload}.json")
            retained = payload in wave["retained_payload_sha256s"]
            if reservation.exists() != retained:
                raise ValueError("partial reviewer reservation partition changed")
            if not retained and (directory / "reviewer_evidence" / f"{item['file']}.evidence").exists():
                raise ValueError("never-started reviewer packet already has invocation evidence")
    validate_transport_repair(result, manifest)
    return result


def validate_transport_repair(value: Mapping[str, Any], manifest: Mapping[str, Any], *,
                             verify_artifacts: bool = True) -> None:
    if (not isinstance(value, Mapping)
            or set(value) != {"policy", "code_replacements", "partial_waves", "historical_guards"}
            or value["policy"] != TRANSPORT_REPAIR_POLICY):
        raise ValueError("unsupported reviewer transport repair scope")
    replacements = value["code_replacements"]
    if not isinstance(replacements, Mapping) or set(replacements) != set(REPAIR_CODE_KEYS):
        raise ValueError("reviewer transport repair may replace only its two declared code files")
    for relative, item in replacements.items():
        if not isinstance(item, Mapping) or set(item) != {"historical", "replacement"}:
            raise ValueError("reviewer transport replacement fields drifted")
        historical, replacement = _binding(item["historical"]), _binding(item["replacement"])
        if historical["path"] != replacement["path"] or not replacement["path"].endswith("/" + relative):
            raise ValueError("reviewer transport repair source path changed")
    if not isinstance(value["partial_waves"], list) or not isinstance(value["historical_guards"], list):
        raise ValueError("reviewer transport repair wave/guard catalogs must be lists")
    if not verify_artifacts:
        return
    capacity_input = manifest["input_bindings"]["capacity_execution_manifest"]
    capacity_raw = Path(capacity_input["path"]).read_bytes()
    if hashlib.sha256(capacity_raw).hexdigest() != capacity_input["sha256"]:
        raise ValueError("reviewer transport repair capacity manifest bytes changed")
    capacity = _json(capacity_input["path"])
    root = Path(capacity["repository"]["project_root"]).resolve()
    for relative, key in REPAIR_CODE_KEYS.items():
        item = replacements[relative]
        if not isinstance(item, Mapping) or set(item) != {"historical", "replacement"}:
            raise ValueError("reviewer transport replacement fields drifted")
        historical, replacement = _binding(item["historical"]), _binding(item["replacement"])
        if historical != capacity["code_bindings"][key]:
            raise ValueError("reviewer transport repair must preserve original capacity source identity")
        expected = (root / relative).as_posix()
        if historical["path"] != expected or replacement["path"] != expected:
            raise ValueError("reviewer transport repair source path changed")
        if code_binding(expected) != replacement:
            raise ValueError("reviewer transport replacement code bytes drifted")
    waves = value["partial_waves"]
    if not isinstance(waves, list):
        raise ValueError("partial reviewer waves must be a list")
    seen = set()
    packet_root = Path(manifest["output_contract"]["paths"]["review_packets_root"]).resolve()
    guards = value["historical_guards"]
    if not isinstance(guards, list) or len({item["path"] for item in guards}) != len(guards):
        raise ValueError("historical reviewer guards must be uniquely bound")
    for binding in guards:
        path = Path(binding["path"]).resolve()
        if path.name != "DISPATCH_GUARD.json" or path.parent.parent != packet_root:
            raise ValueError("historical reviewer guard path escaped the packet root")
        if code_binding(path) != binding:
            raise ValueError("historical reviewer guard bytes changed")
    for wave in waves:
        if set(wave) != {"wave", "packet_directory", "retained_payload_sha256s",
                         "never_started_payload_sha256s", "dispatch_guard", "worklist",
                         "packet_index", "rulings_prefix"}:
            raise ValueError("partial reviewer wave fields drifted")
        number = wave["wave"]
        directory = Path(wave["packet_directory"]).resolve()
        if type(number) is not int or number < 1 or number in seen or directory.parent != packet_root:
            raise ValueError("partial reviewer wave identity drifted")
        seen.add(number)
        for key, filename in (("dispatch_guard", "DISPATCH_GUARD.json"),
                              ("worklist", "WORKLIST.json"), ("packet_index", "INDEX.json")):
            if wave[key] != code_binding(directory / filename):
                raise ValueError(f"partial reviewer {key} bytes changed")
        guard = _json(directory / "DISPATCH_GUARD.json")
        if (guard["run_id"] != manifest["run_id"]
                or Path(guard["packet_directory"]).resolve() != directory
                or Path(guard["output_path"]).resolve() != directory / "rulings.jsonl"
                or guard["artifact_bindings"]["batch_runner"] not in (
                    replacements["scripts/codex_reviewer_batch.py"]["historical"],
                    replacements["scripts/codex_reviewer_batch.py"]["replacement"])):
            raise ValueError("partial reviewer guard differs from its historical execution")
        expected = {item["payload_sha256"] for item in guard["packet_bindings"]}
        retained, never = wave["retained_payload_sha256s"], wave["never_started_payload_sha256s"]
        if (not isinstance(retained, list) or not isinstance(never, list)
                or len(retained) != len(set(retained)) or len(never) != len(set(never))
                or set(retained) & set(never) or set(retained) | set(never) != expected):
            raise ValueError("partial reviewer payload partition is not exact")
        prefix = wave["rulings_prefix"]
        if Path(prefix["path"]).resolve() != directory / "rulings.jsonl":
            raise ValueError("partial reviewer ruling path changed")
        raw = Path(prefix["path"]).read_bytes()[:prefix["byte_count"]]
        if len(raw) != prefix["byte_count"] or hashlib.sha256(raw).hexdigest() != prefix["raw_sha256"]:
            raise ValueError("partial reviewer ruling prefix changed")


def historical_capacity_code_bindings(recovery: Mapping[str, Any] | None) -> dict[str, Any] | None:
    repair = recovery.get("reviewer_transport_repair") if recovery else None
    if not repair:
        return None
    return {key: dict(repair["code_replacements"][relative]["historical"])
            for relative, key in REPAIR_CODE_KEYS.items()}


def accepted_batch_runner_bindings(recovery: Mapping[str, Any] | None) -> list[dict[str, Any]] | None:
    repair = recovery.get("reviewer_transport_repair") if recovery else None
    if not repair:
        return None
    item = repair["code_replacements"]["scripts/codex_reviewer_batch.py"]
    return [dict(item["historical"]), dict(item["replacement"])]


def load_reviewer_recovery_context(recovery_path: str | Path, *, packet_directory: str | Path,
                                   output_path: str | Path, guard_path: str | Path,
                                   guard_raw_sha256: str, verify_artifacts: bool = True,
                                   require_recoverable: bool = True) -> dict[str, Any]:
    from rejudge.phase3_main_recovery import load_authenticated_recovery
    raw = _json(recovery_path)
    recovery = load_authenticated_recovery(recovery_path,
        manifest_path=raw["original_manifest"]["path"],
        authorization_path=raw["original_authorization"]["path"], allow_growth=True,
        verify_artifacts=verify_artifacts, require_recoverable=require_recoverable)
    repair = recovery.get("reviewer_transport_repair")
    if not repair:
        raise ValueError("signed recovery does not authorize reviewer transport continuation")
    directory = Path(packet_directory).resolve()
    matches = [wave for wave in repair["partial_waves"]
               if Path(wave["packet_directory"]).resolve() == directory]
    if len(matches) > 1:
        raise ValueError("signed recovery repeats this partial reviewer wave")
    if matches:
        wave = matches[0]
    else:
        guards = [binding for binding in repair["historical_guards"]
                  if Path(binding["path"]).resolve() == directory / "DISPATCH_GUARD.json"]
        if len(guards) != 1:
            raise ValueError("signed recovery does not bind this historical reviewer guard")
        wave = {"packet_directory": directory.as_posix(), "dispatch_guard": guards[0],
                "retained_payload_sha256s": [], "never_started_payload_sha256s": []}
    if (Path(output_path).resolve() != directory / "rulings.jsonl"
            or Path(guard_path).resolve() != directory / "DISPATCH_GUARD.json"
            or guard_raw_sha256 != wave["dispatch_guard"]["raw_sha256"]):
        raise ValueError("reviewer recovery guard/output identity drifted")
    return {**wave, "accepted_batch_runner_bindings": accepted_batch_runner_bindings(recovery),
            "replacement_batch_runner": repair["code_replacements"][
                "scripts/codex_reviewer_batch.py"]["replacement"],
            "recovery_path": str(Path(recovery_path).resolve()),
            "require_recoverable": require_recoverable,
            "recovery_manifest_sha256": recovery["recovery_manifest_sha256"]}


def recovery_from_run_log(path: str | Path, *, expected_run_id: str,
                          expected_manifest_sha256: str) -> dict[str, Any] | None:
    """Reopen exact signed source authority retained in the final append-only run log."""
    from rejudge.phase3_main_recovery import load_authenticated_recovery
    rows = [json.loads(line) for line in Path(path).read_bytes().splitlines() if line.strip()]
    resumes = [row for row in rows if row.get("event") == "formal_main_resumed"]
    if not resumes:
        return None
    event = resumes[-1]
    recovery_path = event["recovery_path"]
    raw = _json(recovery_path)
    if not raw.get("reviewer_transport_repair"):
        return None
    recovery = load_authenticated_recovery(recovery_path,
        manifest_path=raw["original_manifest"]["path"],
        authorization_path=raw["original_authorization"]["path"],
        allow_growth=True, require_recoverable=False)
    if (recovery["run_id"] != expected_run_id
            or recovery["original_manifest_canonical_sha256"] != expected_manifest_sha256
            or any(recovery[key] != event[key] for key in (
                "recovery_manifest_sha256", "recovery_raw_sha256", "recovery_signature_raw_sha256"))):
        raise ValueError("final reviewer source authority differs from its durable resume event")
    return recovery
