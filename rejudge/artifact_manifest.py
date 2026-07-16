"""Build and verify checksum manifests for large, intentionally untracked artifacts."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
CHUNK_SIZE = 1024 * 1024


class ArtifactVerificationError(RuntimeError):
    pass


def _sha256_and_rows(path: Path) -> tuple[str, int | None]:
    digest = hashlib.sha256()
    count_rows = path.suffix.lower() == ".jsonl"
    newlines = 0
    last = b""
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
            if count_rows:
                newlines += chunk.count(b"\n")
                last = chunk[-1:]
    rows = None
    if count_rows:
        rows = newlines + (1 if path.stat().st_size and last != b"\n" else 0)
    return digest.hexdigest(), rows


def _tracked_paths(root: Path) -> set[str]:
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=root, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {raw.decode("utf-8").replace("\\", "/")
            for raw in output.split(b"\0") if raw}


def expand_artifacts(root: Path, patterns: list[str]) -> list[Path]:
    """Expand repository-relative glob patterns into a stable, duplicate-free file list."""
    found: set[Path] = set()
    for pattern in patterns:
        absolute_pattern = str(root / pattern)
        for raw in glob.glob(absolute_pattern, recursive=True):
            path = Path(raw).resolve()
            if path.is_file():
                try:
                    path.relative_to(root)
                except ValueError as exc:
                    raise ValueError(f"artifact escapes repository root: {path}") from exc
                found.add(path)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def build_manifest(root: Path, patterns: list[str], *,
                   availability: str = "local-only") -> dict:
    root = root.resolve()
    tracked = _tracked_paths(root)
    entries = []
    for path in expand_artifacts(root, patterns):
        relative = path.relative_to(root).as_posix()
        digest, rows = _sha256_and_rows(path)
        entry = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": digest,
            "tracked_by_git": relative in tracked,
            "availability": availability,
            "retrieval_uri": None,
        }
        if rows is not None:
            entry["jsonl_rows"] = rows
        entries.append(entry)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "patterns": patterns,
        "entries": entries,
    }


def write_manifest(output: Path, manifest: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")


def verify_manifest(root: Path, manifest: dict) -> None:
    root = root.resolve()
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ArtifactVerificationError("unsupported artifact manifest schema")
    failures = []
    for entry in manifest.get("entries", []):
        path = (root / entry["path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            failures.append(f"path escapes repository: {entry['path']}")
            continue
        if not path.is_file():
            failures.append(f"missing: {entry['path']}")
            continue
        digest, rows = _sha256_and_rows(path)
        if path.stat().st_size != entry.get("bytes"):
            failures.append(f"size mismatch: {entry['path']}")
        if digest != entry.get("sha256"):
            failures.append(f"sha256 mismatch: {entry['path']}")
        if "jsonl_rows" in entry and rows != entry["jsonl_rows"]:
            failures.append(f"row-count mismatch: {entry['path']}")
    if failures:
        raise ArtifactVerificationError("; ".join(failures))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--root", default=".")
    build.add_argument("--out", required=True)
    build.add_argument("--availability", default="local-only")
    build.add_argument("patterns", nargs="+")
    verify = sub.add_parser("verify")
    verify.add_argument("--root", default=".")
    verify.add_argument("manifest")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.command == "build":
        manifest = build_manifest(root, args.patterns, availability=args.availability)
        write_manifest(Path(args.out), manifest)
        print(f"wrote {len(manifest['entries'])} entries to {args.out}")
        return 0
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    verify_manifest(root, manifest)
    print(f"verified {len(manifest['entries'])} artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
