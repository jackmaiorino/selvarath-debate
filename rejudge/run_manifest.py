"""Fail-closed run identity manifests and cross-process output locking.

The public API is intentionally small:

``manifest_path_for(output)``
    Return the adjacent ``<output>.manifest.json`` path.

``ensure_run_manifest(...)``
    Build the complete run identity, atomically create its manifest if absent,
    or validate an existing manifest byte-semantically.  A changed input raises
    :class:`ManifestMismatchError`; it is never silently accepted or replaced.

``output_lock(output)``
    A non-blocking context manager backed by an operating-system file lock.
    Only one process may write a given output at a time.

Callers should pass effective CLI values after defaults have been applied and
must not include credentials in ``cli_params``.  The manifest is provenance,
not secret storage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, Iterable, Iterator


SCHEMA_VERSION = 1


class RunManifestError(RuntimeError):
    """Base class for fail-closed manifest and output-lock failures."""


class ManifestMismatchError(RunManifestError):
    """An existing manifest is invalid or describes a different run."""


class OutputLockedError(RunManifestError):
    """Another thread or process currently owns the output writer lock."""


def manifest_path_for(output_path: str | os.PathLike[str]) -> Path:
    """Return the JSON manifest path adjacent to *output_path*."""
    output = Path(output_path)
    return output.with_name(f"{output.name}.manifest.json")


def _lock_path_for(output_path: str | os.PathLike[str]) -> Path:
    output = Path(output_path)
    return output.with_name(f"{output.name}.lock")


def _normalize(value: Any, *, location: str) -> Any:
    """Convert a value to deterministic, strict JSON data."""
    if isinstance(value, argparse.Namespace):
        value = vars(value)
    if isinstance(value, Enum):
        return _normalize(value.value, location=location)
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{location} contains a non-finite Decimal")
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{location} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{location} has non-string mapping key {key!r}")
            normalized[key] = _normalize(item, location=f"{location}.{key}")
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item, location=f"{location}[{i}]")
                for i, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        items = [_normalize(item, location=f"{location}[]") for item in value]
        return sorted(items, key=_canonical_json)
    raise TypeError(f"{location} contains unsupported value {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> tuple[str, int]:
    before = path.stat()
    if not path.is_file():
        raise RunManifestError(f"source is not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RunManifestError(f"source changed while it was being hashed: {path}")
    return digest.hexdigest(), after.st_size


def _run_git(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=repo_root, check=True, capture_output=True,
            text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise RunManifestError(f"could not capture git provenance: {detail.strip()}") from exc
    return completed.stdout


def _git_code_state(repo_root: Path, excluded_paths: tuple[Path, ...]) -> tuple[Path, dict]:
    git_root = Path(_run_git(repo_root, "rev-parse", "--show-toplevel").strip()).resolve()
    sha = _run_git(git_root, "rev-parse", "HEAD").strip()

    # Generated output, manifest, and lock files must not make the code state change
    # between initial creation and resume.  All other tracked and untracked changes
    # remain part of the dirty flag.
    pathspecs = ["."]
    for excluded in excluded_paths:
        try:
            relative = excluded.resolve().relative_to(git_root)
        except ValueError:
            continue
        pathspecs.append(f":(top,exclude,literal){relative.as_posix()}")
    status = _run_git(
        git_root, "status", "--porcelain=v1", "--untracked-files=all", "--", *pathspecs)
    return git_root, {"sha": sha, "dirty": bool(status)}


def _source_display_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _build_identity(
    output_path: Path,
    *,
    run_kind: str,
    dry_run: bool,
    models: Any,
    prices: Any,
    protocol_content: Any,
    source_files: Mapping[str, str | os.PathLike[str]],
    cli_params: Mapping[str, Any] | argparse.Namespace,
    repo_root: Path,
    generated_paths: Iterable[str | os.PathLike[str]] = (),
) -> dict[str, Any]:
    if not isinstance(run_kind, str) or not run_kind.strip():
        raise ValueError("run_kind must be a non-empty string")
    if type(dry_run) is not bool:
        raise TypeError("dry_run must be a bool")
    if not isinstance(source_files, Mapping):
        raise TypeError("source_files must map stable logical names to file paths")
    if not isinstance(cli_params, (Mapping, argparse.Namespace)):
        raise TypeError("cli_params must be a mapping or argparse.Namespace")

    manifest_path = manifest_path_for(output_path)
    lock_path = _lock_path_for(output_path)
    generated = tuple(Path(path) for path in generated_paths)
    git_root, code = _git_code_state(
        repo_root, (output_path, manifest_path, lock_path, *generated))

    sources: dict[str, dict[str, Any]] = {}
    for name, raw_path in sorted(source_files.items()):
        if not isinstance(name, str) or not name:
            raise TypeError("source_files keys must be non-empty strings")
        path = Path(raw_path)
        if not path.is_absolute():
            path = repo_root / path
        digest, size = _hash_file(path)
        sources[name] = {
            "path": _source_display_path(path, git_root),
            "sha256": digest,
            "bytes": size,
        }

    protocol = _normalize(protocol_content, location="protocol_content")
    identity = {
        "schema_version": SCHEMA_VERSION,
        "run_kind": run_kind.strip(),
        "output": _source_display_path(output_path, git_root),
        "mode": "dry-run" if dry_run else "live",
        "dry_run": dry_run,
        "models": _normalize(models, location="models"),
        "prices": _normalize(prices, location="prices"),
        "protocol": {
            "content": protocol,
            "sha256": _canonical_sha256(protocol),
        },
        "source_files": sources,
        "code": code,
        "cli_params": _normalize(cli_params, location="cli_params"),
    }
    return identity


def _envelope(identity: dict[str, Any]) -> dict[str, Any]:
    return {"manifest_sha256": _canonical_sha256(identity), "identity": identity}


def _atomic_create(path: Path, payload: bytes) -> bool:
    """Publish complete *payload* without replacing an existing path.

    A same-directory temporary file is fully flushed first.  Hard-link creation
    is then the atomic, exclusive commit: it either installs the complete file or
    reports that another creator won the race.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _changed_paths(expected: Any, actual: Any, prefix: str = "identity") -> list[str]:
    if type(expected) is not type(actual):
        return [prefix]
    if isinstance(expected, dict):
        changes: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{prefix}.{key}"
            if key not in expected or key not in actual:
                changes.append(child)
            else:
                changes.extend(_changed_paths(expected[key], actual[key], child))
            if len(changes) >= 8:
                break
        return changes
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return [prefix]
        changes = []
        for index, (left, right) in enumerate(zip(expected, actual)):
            changes.extend(_changed_paths(left, right, f"{prefix}[{index}]"))
            if len(changes) >= 8:
                break
        return changes
    return [] if expected == actual else [prefix]


def _load_existing(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestMismatchError(f"existing run manifest is unreadable: {path}: {exc}") from exc
    if not isinstance(existing, dict) or set(existing) != {"manifest_sha256", "identity"}:
        raise ManifestMismatchError(f"existing run manifest has an invalid envelope: {path}")
    identity = existing["identity"]
    if not isinstance(identity, dict) or not isinstance(existing["manifest_sha256"], str):
        raise ManifestMismatchError(f"existing run manifest has invalid field types: {path}")
    actual_hash = _canonical_sha256(identity)
    if existing["manifest_sha256"] != actual_hash:
        raise ManifestMismatchError(f"existing run manifest failed its own hash check: {path}")
    if existing["manifest_sha256"] != expected["manifest_sha256"]:
        changes = _changed_paths(expected["identity"], identity)
        changed = ", ".join(changes[:8]) or "identity"
        raise ManifestMismatchError(
            f"run identity does not match {path}; changed fields: {changed}")
    # Hash equality is sufficient cryptographically, but exact equality makes the
    # intended invariant explicit and protects against serialization surprises.
    if identity != expected["identity"]:
        raise ManifestMismatchError(f"run identity hash collision or normalization error: {path}")
    return existing


def ensure_run_manifest(
    output_path: str | os.PathLike[str],
    *,
    run_kind: str,
    dry_run: bool,
    models: Any,
    prices: Any,
    protocol_content: Any,
    source_files: Mapping[str, str | os.PathLike[str]],
    cli_params: Mapping[str, Any] | argparse.Namespace,
    repo_root: str | os.PathLike[str] = ".",
    generated_paths: Iterable[str | os.PathLike[str]] = (),
) -> dict[str, Any]:
    """Create or validate the immutable identity manifest for *output_path*.

    The canonical identity hash covers the run kind and dry/live mode, model and
    price configuration, normalized protocol content plus its own hash, every
    named source file's SHA-256, Git ``HEAD`` and dirty flag, and normalized
    effective CLI parameters.  Mapping order and set order do not affect it.

    Existing manifests are never updated.  Any malformed manifest, changed file,
    changed option, or changed code state raises :class:`ManifestMismatchError`.
    An output that already exists without a manifest is also refused: silently
    attaching current provenance to historical rows would make unsafe resumes
    look valid.  Integrations should call this while holding :func:`output_lock`
    and before opening a new output for writing. ``generated_paths`` names other
    runtime-owned artifacts (for example a usage ledger, its tail checkpoint,
    and diagnostic logs) that must not make the code worktree look dirty. Callers
    enumerate them explicitly; they are excluded only from Git status and remain
    bound through normal identity fields such as ``cli_params`` when appropriate.
    The returned dictionary is the complete on-disk manifest envelope.
    """
    output = Path(output_path)
    path = manifest_path_for(output)
    if output.exists() and not path.exists():
        raise ManifestMismatchError(
            f"output exists without a run manifest; refusing retroactive adoption: {output}")
    root = Path(repo_root).resolve()
    identity = _build_identity(
        output, run_kind=run_kind, dry_run=dry_run, models=models, prices=prices,
        protocol_content=protocol_content, source_files=source_files,
        cli_params=cli_params, repo_root=root, generated_paths=generated_paths)
    if not dry_run and identity["code"]["dirty"]:
        raise RunManifestError(
            "live runs require a clean committed worktree; commit or otherwise clear "
            "all tracked and untracked code/config changes before spending")
    expected = _envelope(identity)
    serialized = (json.dumps(expected, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, indent=2) + "\n").encode("utf-8")
    if _atomic_create(path, serialized):
        return expected
    return _load_existing(path, expected)


_HELD_LOCKS: set[str] = set()
_HELD_LOCKS_GUARD = threading.Lock()


def _acquire_os_lock(stream) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_lock(stream) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def output_lock(output_path: str | os.PathLike[str]) -> Iterator[Path]:
    """Acquire the sole non-blocking writer lock for *output_path*.

    The lock is enforced by the OS across processes and is automatically released
    if the process exits.  A persistent adjacent ``<output>.lock`` file contains
    owner diagnostics but its mere existence does not mean the output is locked.
    Concurrent acquisition raises :class:`OutputLockedError` immediately.
    """
    lock_path = _lock_path_for(output_path).resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(lock_path))
    with _HELD_LOCKS_GUARD:
        if key in _HELD_LOCKS:
            raise OutputLockedError(f"output writer lock is already held: {lock_path}")
        _HELD_LOCKS.add(key)

    stream = None
    acquired = False
    try:
        # O_CREAT without O_EXCL makes every contender open the same persistent
        # inode.  Deliberately omit O_APPEND: after acquiring the byte-range lock
        # the owner metadata must replace, rather than accumulate behind, the
        # previous owner's diagnostics.
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(lock_path, flags, 0o600)
        stream = os.fdopen(descriptor, "r+b")
        try:
            _acquire_os_lock(stream)
            acquired = True
        except OSError as exc:
            raise OutputLockedError(
                f"another process is writing the output (lock: {lock_path})") from exc

        owner = {
            "pid": os.getpid(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "output": str(Path(output_path).resolve()),
        }
        encoded = (json.dumps(owner, sort_keys=True) + "\n").encode("utf-8")
        stream.seek(0)
        stream.write(encoded)
        stream.truncate(stream.tell())
        stream.flush()
        os.fsync(stream.fileno())
        yield lock_path
    finally:
        if stream is not None:
            if acquired:
                try:
                    _release_os_lock(stream)
                except OSError:
                    # Closing the descriptor below also releases the operating-system lock.
                    pass
            stream.close()
        with _HELD_LOCKS_GUARD:
            _HELD_LOCKS.discard(key)


__all__ = [
    "ManifestMismatchError",
    "OutputLockedError",
    "RunManifestError",
    "ensure_run_manifest",
    "manifest_path_for",
    "output_lock",
]
