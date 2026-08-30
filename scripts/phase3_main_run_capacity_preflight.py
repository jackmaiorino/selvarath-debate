"""Build, validate, or execute the capacity-only Phase 3 reviewer preflight."""
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

from rejudge import phase3_main_capacity_execution as execution  # noqa: E402


DEFAULT_PLAN = REPO_ROOT / "rejudge" / "phase3_main_review_capacity_preflight_plan_2026-08-29.json"


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


def _context(args: argparse.Namespace) -> execution.CapacityContext:
    return execution.load_capacity_context(
        args.plan,
        project_root=args.project_root,
        archive_path=args.archive,
        finalization_path=args.finalization_record,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase3_main_run_capacity_preflight")
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--finalization-record", type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--write-manifest", type=Path)
    actions.add_argument("--write-unsigned-authorization", type=Path)
    actions.add_argument("--validate-authority", action="store_true")
    actions.add_argument("--run", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--workload-root", type=Path)
    parser.add_argument("--result-path", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--attempt-id")
    parser.add_argument("--authorization-id")
    parser.add_argument("--approved-at-utc", type=_utc_argument)
    parser.add_argument("--valid-until-utc", type=_utc_argument)
    args = parser.parse_args(argv)

    context = _context(args)
    if args.write_manifest is not None:
        required = {
            "--workload-root": args.workload_root,
            "--result-path": args.result_path,
            "--run-id": args.run_id,
            "--attempt-id": args.attempt_id,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error(f"{', '.join(missing)} are required with --write-manifest")
        head, clean = execution.repository_probe(args.project_root)
        if not clean:
            parser.error("tracked repository state must be clean before manifest construction")
        reviewer = context.plan["reviewer_configuration"]
        cli_path = str(reviewer["reviewer_cli_resolved_path"])
        manifest = execution.build_execution_manifest(
            context=context,
            project_root=args.project_root,
            workload_root=args.workload_root,
            result_path=args.result_path,
            run_id=args.run_id,
            attempt_id=args.attempt_id,
            repository_head=head,
            reviewer_cli_version=execution.reviewer_cli_version(cli_path),
            host_identity=execution.host_identity(),
            runner_script_path=Path(__file__).resolve(),
        )
        execution.write_json_exclusive(args.write_manifest.resolve(), manifest)
        print(json.dumps({
            "manifest_path": args.write_manifest.resolve().as_posix(),
            "manifest_canonical_sha256": execution.canonical_sha256(manifest),
            "execution_authorized": False,
        }, indent=1))
        return 0

    if args.write_unsigned_authorization is not None:
        required = {
            "--manifest": args.manifest,
            "--authorization-id": args.authorization_id,
            "--approved-at-utc": args.approved_at_utc,
            "--valid-until-utc": args.valid_until_utc,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error(
                f"{', '.join(missing)} are required with "
                "--write-unsigned-authorization"
            )
        destination = args.write_unsigned_authorization.resolve()
        signature_path = destination.with_name(f"{destination.name}.sig")
        if signature_path.exists() or signature_path.is_symlink():
            raise execution.CapacityExecutionError(
                "unsigned authorization destination already has a detached signature"
            )
        manifest_raw, manifest = execution.load_execution_manifest(
            args.manifest.resolve(), context=context
        )
        repository = manifest["repository"]
        observed_head, clean = execution.repository_probe(
            Path(repository["project_root"])
        )
        if observed_head != repository["head_commit"] or not clean:
            parser.error(
                "tracked repository state must match the manifest before unsigned "
                "authorization construction"
            )
        authorization = execution.build_unsigned_capacity_authorization(
            manifest=manifest,
            manifest_raw=manifest_raw,
            authorization_id=args.authorization_id,
            approved_at_utc=args.approved_at_utc,
            valid_until_utc=args.valid_until_utc,
        )
        execution.write_json_exclusive(destination, authorization)
        authorization_raw = destination.read_bytes()
        if (
            destination.read_bytes() != authorization_raw
            or signature_path.exists()
            or signature_path.is_symlink()
        ):
            raise execution.CapacityExecutionError(
                "unsigned authorization or detached-signature state changed during publication"
            )
        print(json.dumps({
            "authorization_path": destination.as_posix(),
            "authorization_raw_sha256": hashlib.sha256(
                authorization_raw
            ).hexdigest(),
            "authorization_canonical_sha256": execution.canonical_sha256(
                authorization
            ),
            "artifact_status": "unsigned_non_authorizing_draft",
            "detached_signature_present": False,
            "signature_namespace": execution.CAPACITY_SIGNATURE_NAMESPACE,
            "signature_principal": execution.CAPACITY_SIGNATURE_PRINCIPAL,
            "capacity_execution_authorized": False,
            "external_reviewer_dispatch_authorized": False,
        }, indent=1))
        return 0

    if args.manifest is None or args.authorization is None:
        parser.error("--manifest and --authorization are required")
    if args.run:
        outcome = execution.execute_capacity_preflight(
            manifest_path=args.manifest.resolve(),
            authorization_path=args.authorization.resolve(),
            context=context,
        )
        print(json.dumps(outcome, indent=1))
        return 0
    if args.validate_authority:
        manifest_raw, manifest = execution.load_execution_manifest(
            args.manifest.resolve(), context=context
        )
        _authorization_raw, authorization = (
            execution.load_authenticated_capacity_authorization(
                args.authorization.resolve()
            )
        )
        validated = execution.validate_authorization(
            authorization,
            manifest=manifest,
            manifest_raw=manifest_raw,
            observed_at=datetime.now(timezone.utc),
        )
        print(json.dumps({
            "validation": "pass",
            "authorization_id": validated["authorization_id"],
            "maximum_reviewer_dispatches": validated["maximum_reviewer_dispatches"],
        }, indent=1))
        return 0

    raise execution.CapacityExecutionError("capacity action routing failed closed")


if __name__ == "__main__":
    raise SystemExit(main())
