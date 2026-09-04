"""Build an offline Phase 3 main manifest or unsigned exact authorization."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_main_launch_materialization as materialization  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402


def _utc_argument(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise argparse.ArgumentTypeError("must use UTC")
    return parsed.astimezone(timezone.utc)


def _outside_project(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return
    raise materialization.MainLaunchMaterializationError(
        f"{label} must remain outside the clean source checkout"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--write-manifest", type=Path)
    actions.add_argument("--write-unsigned-authorization", type=Path)
    parser.add_argument("--input-paths", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--identity-registry-root", type=Path)
    parser.add_argument("--recorded-at-utc", type=_utc_argument)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--authorization-id")
    parser.add_argument("--approved-at-utc", type=_utc_argument)
    parser.add_argument("--valid-until-utc", type=_utc_argument)
    args = parser.parse_args(argv)

    root = args.project_root.resolve()
    destination = (
        args.write_manifest or args.write_unsigned_authorization
    ).resolve()
    _outside_project(destination, root, "launch-package output")
    _outside_project(destination.parent, root, "launch-package output directory")
    head, clean = materialization.repository_probe(root)
    if not clean:
        parser.error("tracked and untracked repository state must be clean")

    if args.write_manifest is not None:
        required = {
            "--input-paths": args.input_paths,
            "--artifact-root": args.artifact_root,
            "--identity-registry-root": args.identity_registry_root,
            "--recorded-at-utc": args.recorded_at_utc,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error(f"{', '.join(missing)} are required with --write-manifest")
        inputs = materialization.load_input_paths(
            args.input_paths,
            project_root=root,
        )
        manifest = materialization.build_launch_manifest(
            project_root=root,
            input_paths=inputs,
            artifact_root=args.artifact_root,
            identity_registry_root=args.identity_registry_root,
            source_commit=head,
            recorded_at_utc=args.recorded_at_utc,
        )
        raw = materialization.write_json_exclusive(destination, manifest)
        print(json.dumps({
            "manifest_path": destination.as_posix(),
            "manifest_raw_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_canonical_sha256": canonical_sha256(manifest),
            "manifest_identity_sha256": manifest["manifest_identity_sha256"],
            "run_id": manifest["run_id"],
            "execution_authorized": False,
        }, indent=1))
        return 0

    required = {
        "--manifest": args.manifest,
        "--authorization-id": args.authorization_id,
        "--approved-at-utc": args.approved_at_utc,
        "--valid-until-utc": args.valid_until_utc,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error(
            f"{', '.join(missing)} are required with --write-unsigned-authorization"
        )
    signature = destination.with_name(f"{destination.name}.sig")
    if signature.exists() or signature.is_symlink():
        raise materialization.MainLaunchMaterializationError(
            "unsigned authorization destination already has a detached signature"
        )
    manifest = materialization.load_main_manifest(
        args.manifest.resolve(),
        project_root=root,
    )
    if manifest["source_commit"] != head:
        parser.error("main manifest source commit differs from the clean checkout")
    authorization = materialization.build_unsigned_main_authorization(
        manifest=manifest,
        authorization_id=args.authorization_id,
        approved_at_utc=args.approved_at_utc,
        valid_until_utc=args.valid_until_utc,
    )
    raw = materialization.write_json_exclusive(destination, authorization)
    if signature.exists() or signature.is_symlink():
        raise materialization.MainLaunchMaterializationError(
            "detached signature appeared during unsigned authorization publication"
        )
    print(json.dumps({
        "authorization_path": destination.as_posix(),
        "authorization_raw_sha256": hashlib.sha256(raw).hexdigest(),
        "authorization_canonical_sha256": canonical_sha256(authorization),
        "artifact_status": "unsigned_non_authorizing_draft",
        "detached_signature_present": False,
        "signature_namespace": "selvarath-phase3-main-authorization-v1",
        "signature_principal": "jack-maiorino",
        "execution_authorized": False,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
