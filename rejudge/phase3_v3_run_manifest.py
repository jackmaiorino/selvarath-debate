"""Small, non-authorizing run manifest for the Phase 3 v3 successor."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from rejudge import phase3_plan, phase3_v3_inputs, phase3_v3_materialization
from rejudge.phase2_execution import canonical_sha256


SCHEMA_VERSION = "phase3_v3_run_manifest_v1"
MANIFEST_FIELDS = frozenset({
    "schema_version",
    "status",
    "run_id",
    "recorded_at_utc",
    "execution_authorized",
    "git_commit",
    "python_version",
    "dependency_lock_sha256",
    "linker_version_or_not_applicable",
    "seeds",
    "input_sha256s",
    "planned_output_paths",
    "output_sha256s",
    "gpu_ordinal_or_not_used",
    "final_roster",
    "protocol_sha256",
    "tokenizer_manifest_sha256",
    "price_snapshot_sha256",
    "harness_check",
})


class RunManifestError(ValueError):
    """Raised when the small successor run manifest is incomplete or inconsistent."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RunManifestError(f"{label} must be a non-empty string")
    return value


def _sha256(value: Any, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise RunManifestError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _utc_text(value: Any, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise RunManifestError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RunManifestError(f"{label} must have a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _paths(values: Any, label: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise RunManifestError(f"{label} must be a non-empty array")
    paths: list[str] = []
    for value in values:
        path = _text(value, label).replace("\\", "/")
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise RunManifestError(f"{label} contains duplicates")
    return paths


def _validate_seeds(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or not value:
        raise RunManifestError("seeds must be a non-empty object")
    seeds: dict[str, int] = {}
    for name, seed in value.items():
        if not isinstance(name, str) or not name:
            raise RunManifestError("seed names must be non-empty strings")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise RunManifestError(f"seed {name!r} must be a non-negative integer")
        seeds[name] = seed
    return seeds


def _validate_inputs(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise RunManifestError("input_sha256s must be a non-empty object")
    inputs: dict[str, str] = {}
    for raw_path, raw_digest in value.items():
        if not isinstance(raw_path, str) or not raw_path:
            raise RunManifestError("input_sha256s paths must be non-empty strings")
        path = raw_path.replace("\\", "/")
        if path.startswith("/") or (len(path) >= 2 and path[1] == ":"):
            raise RunManifestError("input_sha256s paths must be relative")
        inputs[path] = _sha256(raw_digest, f"input_sha256s[{path!r}]")
    return inputs


def _identity_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "git_commit": manifest["git_commit"],
        "python_version": manifest["python_version"],
        "dependency_lock_sha256": manifest["dependency_lock_sha256"],
        "linker_version_or_not_applicable": manifest["linker_version_or_not_applicable"],
        "seeds": manifest["seeds"],
        "input_sha256s": manifest["input_sha256s"],
        "planned_output_paths": manifest["planned_output_paths"],
        "gpu_ordinal_or_not_used": manifest["gpu_ordinal_or_not_used"],
        "final_roster": manifest["final_roster"],
        "protocol_sha256": manifest["protocol_sha256"],
        "tokenizer_manifest_sha256": manifest["tokenizer_manifest_sha256"],
        "price_snapshot_sha256": manifest["price_snapshot_sha256"],
    }


def build_run_manifest(
    *,
    protocol: Mapping[str, Any],
    protocol_pin: Mapping[str, Any],
    tokenizer_manifest: Mapping[str, Any],
    price_snapshot: Mapping[str, Any],
    recorded_at_utc: str,
    git_commit: str,
    python_version: str,
    dependency_lock_sha256: str,
    linker_version_or_not_applicable: str,
    seeds: Mapping[str, int],
    input_sha256s: Mapping[str, str],
    planned_output_paths: Sequence[str],
    gpu_ordinal_or_not_used: int | str,
    harness_seed_name: str,
    project_root: str | Path | None = None,
    verify_external_files: bool = True,
) -> dict[str, Any]:
    """Build an offline preflight manifest. This function can never authorize execution."""
    phase3_plan.validate_protocol(protocol)
    phase3_v3_materialization.validate_protocol_pin(protocol_pin, protocol)
    final_roster = list(protocol["roster"]["judges_final"])
    tokenizer_sha = canonical_sha256(tokenizer_manifest)
    price_sha = canonical_sha256(price_snapshot)
    protocol_sha = canonical_sha256(protocol)
    normalized_outputs = _paths(list(planned_output_paths), "planned_output_paths")
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "preflight",
        "run_id": "pending",
        "recorded_at_utc": _utc_text(recorded_at_utc, "recorded_at_utc"),
        "execution_authorized": False,
        "git_commit": git_commit,
        "python_version": python_version,
        "dependency_lock_sha256": dependency_lock_sha256,
        "linker_version_or_not_applicable": linker_version_or_not_applicable,
        "seeds": dict(seeds),
        "input_sha256s": dict(input_sha256s),
        "planned_output_paths": normalized_outputs,
        "output_sha256s": {path: None for path in normalized_outputs},
        "gpu_ordinal_or_not_used": gpu_ordinal_or_not_used,
        "final_roster": final_roster,
        "protocol_sha256": protocol_sha,
        "tokenizer_manifest_sha256": tokenizer_sha,
        "price_snapshot_sha256": price_sha,
        "harness_check": {
            "seed_name": harness_seed_name,
            "status": "pending",
            "first_output_store_sha256": None,
            "rerun_output_store_sha256": None,
        },
    }
    identity_sha = canonical_sha256(_identity_payload(manifest))
    manifest["run_id"] = f"phase3-v3-{identity_sha[:16]}"
    validate_run_manifest(
        manifest,
        protocol=protocol,
        protocol_pin=protocol_pin,
        tokenizer_manifest=tokenizer_manifest,
        price_snapshot=price_snapshot,
        project_root=project_root,
        verify_external_files=verify_external_files,
    )
    return manifest


def validate_run_manifest(
    manifest: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any] | None = None,
    protocol_pin: Mapping[str, Any] | None = None,
    tokenizer_manifest: Mapping[str, Any] | None = None,
    price_snapshot: Mapping[str, Any] | None = None,
    project_root: str | Path | None = None,
    verify_external_files: bool = True,
) -> None:
    if set(manifest) != MANIFEST_FIELDS:
        raise RunManifestError(f"run manifest fields must be exactly {sorted(MANIFEST_FIELDS)!r}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RunManifestError("unsupported v3 run-manifest schema")
    status = manifest.get("status")
    if status not in {"preflight", "harness_verified", "complete"}:
        raise RunManifestError(
            "run manifest status must be preflight, harness_verified, or complete")
    if manifest.get("execution_authorized") is not False:
        raise RunManifestError("the run manifest can never authorize execution")
    _utc_text(manifest.get("recorded_at_utc"), "recorded_at_utc")
    git_commit = _text(manifest.get("git_commit"), "git_commit")
    if len(git_commit) != 40 or any(character not in "0123456789abcdef" for character in git_commit):
        raise RunManifestError("git_commit must be a lowercase 40-character commit")
    _text(manifest.get("python_version"), "python_version")
    _sha256(manifest.get("dependency_lock_sha256"), "dependency_lock_sha256")
    _text(
        manifest.get("linker_version_or_not_applicable"),
        "linker_version_or_not_applicable",
    )
    seeds = _validate_seeds(manifest.get("seeds"))
    inputs = _validate_inputs(manifest.get("input_sha256s"))
    outputs = _paths(manifest.get("planned_output_paths"), "planned_output_paths")
    output_hashes = manifest.get("output_sha256s")
    if not isinstance(output_hashes, dict) or set(output_hashes) != set(outputs):
        raise RunManifestError("output_sha256s keys must equal planned_output_paths")
    if status in {"preflight", "harness_verified"}:
        if any(value is not None for value in output_hashes.values()):
            raise RunManifestError("pre-formal output hashes must remain null")
    else:
        for path, digest in output_hashes.items():
            _sha256(digest, f"output_sha256s[{path!r}]")

    gpu = manifest.get("gpu_ordinal_or_not_used")
    if gpu != "not_used" and (isinstance(gpu, bool) or gpu != 1):
        raise RunManifestError(
            "gpu_ordinal_or_not_used must be not_used or exclusive headless GPU ordinal 1")
    roster = manifest.get("final_roster")
    if (not isinstance(roster, list) or len(roster) not in {4, 5}
            or not all(isinstance(model, str) and model for model in roster)
            or len(roster) != len(set(roster))):
        raise RunManifestError("final_roster must contain four or five unique model IDs")
    protocol_sha = _sha256(manifest.get("protocol_sha256"), "protocol_sha256")
    tokenizer_sha = _sha256(
        manifest.get("tokenizer_manifest_sha256"), "tokenizer_manifest_sha256")
    price_sha = _sha256(manifest.get("price_snapshot_sha256"), "price_snapshot_sha256")
    harness = manifest.get("harness_check")
    if not isinstance(harness, dict) or set(harness) != {
        "seed_name", "status", "first_output_store_sha256", "rerun_output_store_sha256",
    }:
        raise RunManifestError("harness_check fields drifted")
    seed_name = _text(harness.get("seed_name"), "harness_check.seed_name")
    if seed_name not in seeds:
        raise RunManifestError("harness-check seed must name a manifest seed")
    if status == "preflight":
        if harness.get("status") != "pending":
            raise RunManifestError("preflight harness check must be pending")
        if (harness.get("first_output_store_sha256") is not None
                or harness.get("rerun_output_store_sha256") is not None):
            raise RunManifestError("pending harness hashes must remain null")
    else:
        if harness.get("status") != "bit_identical_pass":
            raise RunManifestError("complete manifest requires a bit-identical harness pass")
        first = _sha256(
            harness.get("first_output_store_sha256"),
            "harness_check.first_output_store_sha256",
        )
        rerun = _sha256(
            harness.get("rerun_output_store_sha256"),
            "harness_check.rerun_output_store_sha256",
        )
        if first != rerun:
            raise RunManifestError("harness rerun output store is not bit-identical")

    expected_run_id = f"phase3-v3-{canonical_sha256(_identity_payload(manifest))[:16]}"
    if manifest.get("run_id") != expected_run_id:
        raise RunManifestError("run_id does not match the immutable manifest identity")

    if protocol is not None:
        phase3_plan.validate_protocol(protocol)
        if canonical_sha256(protocol) != protocol_sha:
            raise RunManifestError("manifest protocol hash drifted")
        if protocol["roster"]["judges_final"] != roster:
            raise RunManifestError("manifest final roster differs from protocol")
    if protocol_pin is not None:
        if protocol is None:
            raise RunManifestError("protocol is required when validating its pin")
        phase3_v3_materialization.validate_protocol_pin(protocol_pin, protocol)
        pin_path = str(protocol_pin["protocol_tracked_path"]).replace("\\", "/")
        if inputs.get(pin_path) != protocol_sha:
            raise RunManifestError("input_sha256s must bind the pinned protocol path")
    if tokenizer_manifest is not None:
        if canonical_sha256(tokenizer_manifest) != tokenizer_sha:
            raise RunManifestError("manifest tokenizer hash drifted")
        if tokenizer_sha not in inputs.values():
            raise RunManifestError("input_sha256s must bind the exact-tokenizer manifest")
        if protocol is None:
            raise RunManifestError("protocol is required when validating exact tokenizers")
        try:
            phase3_v3_inputs.validate_exact_tokenizer_manifest(
                tokenizer_manifest,
                protocol=protocol,
                project_root=project_root,
                verify_files=verify_external_files,
            )
        except phase3_v3_inputs.InputGateError as exc:
            raise RunManifestError(f"exact-tokenizer gate failed: {exc}") from exc
    if price_snapshot is not None:
        if canonical_sha256(price_snapshot) != price_sha:
            raise RunManifestError("manifest price snapshot hash drifted")
        if price_sha not in inputs.values():
            raise RunManifestError("input_sha256s must bind the fresh price snapshot")
        if protocol is None:
            raise RunManifestError("protocol is required when validating prices")
        recorded_at = datetime.fromisoformat(
            str(manifest["recorded_at_utc"]).replace("Z", "+00:00"))
        try:
            phase3_v3_inputs.validate_price_snapshot(
                price_snapshot,
                protocol=protocol,
                as_of=recorded_at,
                project_root=project_root,
                verify_catalog=verify_external_files,
            )
        except phase3_v3_inputs.InputGateError as exc:
            raise RunManifestError(f"fresh-price gate failed: {exc}") from exc


def record_harness_check(
    manifest: Mapping[str, Any],
    *,
    first_output_store_sha256: str,
    rerun_output_store_sha256: str,
) -> dict[str, Any]:
    """Record the required bit-identical rerun before formal measurement begins."""
    validate_run_manifest(manifest)
    if manifest.get("status") != "preflight":
        raise RunManifestError("only a preflight manifest can record its harness check")
    verified = deepcopy(dict(manifest))
    verified["status"] = "harness_verified"
    verified["harness_check"] = {
        "seed_name": manifest["harness_check"]["seed_name"],
        "status": "bit_identical_pass",
        "first_output_store_sha256": first_output_store_sha256,
        "rerun_output_store_sha256": rerun_output_store_sha256,
    }
    validate_run_manifest(verified)
    return verified


def finalize_run_manifest(
    manifest: Mapping[str, Any],
    *,
    output_sha256s: Mapping[str, str],
) -> dict[str, Any]:
    """Bind completed formal outputs after the pre-formal harness check has passed."""
    validate_run_manifest(manifest)
    if manifest.get("status") != "harness_verified":
        raise RunManifestError("only a harness-verified manifest can be finalized")
    completed = deepcopy(dict(manifest))
    completed["status"] = "complete"
    completed["output_sha256s"] = dict(output_sha256s)
    validate_run_manifest(completed)
    return completed
