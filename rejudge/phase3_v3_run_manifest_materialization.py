"""Environment capture for the small, non-authorizing Phase 3 v3 run manifest."""
from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from rejudge import (
    phase3_plan,
    phase3_v3_inputs,
    phase3_v3_run_manifest,
)
from rejudge.phase2_execution import canonical_sha256


class RunManifestMaterializationError(ValueError):
    """Raised when local inputs or repository state cannot support a run manifest."""


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunManifestMaterializationError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunManifestMaterializationError(f"{label} must be a JSON object")
    return value


def _relative_path(path: Path, root: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RunManifestMaterializationError(
            f"{label} must be inside the project root: {path}") from exc


def clean_git_commit(project_root: str | Path) -> str:
    """Return HEAD only when every tracked and untracked workspace path is clean."""
    root = Path(project_root)
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise RunManifestMaterializationError(
            "run-manifest materialization requires a clean git worktree")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise RunManifestMaterializationError("git HEAD is not a lowercase 40-character commit")
    return commit


def materialize_run_manifest(
    *,
    protocol_path: str | Path,
    protocol_pin_path: str | Path,
    tokenizer_manifest_path: str | Path,
    price_snapshot_path: str | Path,
    dependency_lock_path: str | Path,
    seeds: Mapping[str, int],
    planned_output_paths: Sequence[str],
    gpu_ordinal_or_not_used: int | str,
    harness_seed_name: str,
    project_root: str | Path,
    recorded_at_utc: str | None = None,
    linker_version_or_not_applicable: str = "not_applicable",
    require_clean_git: bool = True,
    git_commit: str | None = None,
    python_version: str | None = None,
    verify_external_files: bool = True,
    extra_input_paths: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Load bound artifacts, capture the environment, and build the preflight manifest."""
    root = Path(project_root)
    paths = {
        "protocol": Path(protocol_path),
        "protocol_pin": Path(protocol_pin_path),
        "tokenizer_manifest": Path(tokenizer_manifest_path),
        "price_snapshot": Path(price_snapshot_path),
        "dependency_lock": Path(dependency_lock_path),
    }
    for label, path in list(paths.items()):
        paths[label] = path if path.is_absolute() else root / path
        if not paths[label].is_file():
            raise RunManifestMaterializationError(
                f"{label} file does not exist: {paths[label]}")

    protocol = phase3_plan.load_protocol(paths["protocol"])
    pin = _load_json_object(paths["protocol_pin"], "protocol pin")
    tokenizer = _load_json_object(paths["tokenizer_manifest"], "tokenizer manifest")
    prices = _load_json_object(paths["price_snapshot"], "price snapshot")
    input_sha256s = {
        _relative_path(paths["protocol"], root, "protocol"): canonical_sha256(protocol),
        _relative_path(paths["protocol_pin"], root, "protocol pin"): canonical_sha256(pin),
        _relative_path(
            paths["tokenizer_manifest"], root, "tokenizer manifest",
        ): canonical_sha256(tokenizer),
        _relative_path(
            paths["price_snapshot"], root, "price snapshot",
        ): canonical_sha256(prices),
    }
    for raw_extra_path in extra_input_paths:
        extra_path = Path(raw_extra_path)
        extra_path = extra_path if extra_path.is_absolute() else root / extra_path
        if not extra_path.is_file():
            raise RunManifestMaterializationError(
                f"extra input file does not exist: {extra_path}")
        relative = _relative_path(extra_path, root, "extra input")
        if relative in input_sha256s:
            raise RunManifestMaterializationError(
                f"duplicate run-manifest input path: {relative}")
        input_sha256s[relative] = canonical_sha256(
            _load_json_object(extra_path, "extra input"))
    observed_commit = git_commit
    if require_clean_git:
        if git_commit is not None:
            raise RunManifestMaterializationError(
                "git_commit cannot be injected when require_clean_git is true")
        try:
            observed_commit = clean_git_commit(root)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RunManifestMaterializationError(f"could not inspect git state: {exc}") from exc
    if observed_commit is None:
        raise RunManifestMaterializationError("git_commit is required when git checks are disabled")
    recorded = recorded_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        return phase3_v3_run_manifest.build_run_manifest(
            protocol=protocol,
            protocol_pin=pin,
            tokenizer_manifest=tokenizer,
            price_snapshot=prices,
            recorded_at_utc=recorded,
            git_commit=observed_commit,
            python_version=python_version or platform.python_version(),
            dependency_lock_sha256=phase3_v3_inputs.sha256_file(paths["dependency_lock"]),
            linker_version_or_not_applicable=linker_version_or_not_applicable,
            seeds=seeds,
            input_sha256s=input_sha256s,
            planned_output_paths=planned_output_paths,
            gpu_ordinal_or_not_used=gpu_ordinal_or_not_used,
            harness_seed_name=harness_seed_name,
            project_root=root,
            verify_external_files=verify_external_files,
        )
    except (
        phase3_v3_run_manifest.RunManifestError,
        phase3_plan.ProtocolValidationError,
        phase3_plan.PlanValidationError,
    ) as exc:
        raise RunManifestMaterializationError(str(exc)) from exc
