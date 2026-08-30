"""Complete one prepared Phase 3 reviewer-wave closeout using local artifacts only.

This command requires an independent formal artifact root and pre-existing run lease, then
requires the durable transaction intent to match them exactly. It can append only the missing
decision and reviewer-index bytes named by that intent. It has no provider, reviewer,
subprocess, or redispatch capability.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_main_reviewer_commit  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover one prepared reviewer-wave local closeout transaction.")
    parser.add_argument(
        "--transaction-directory",
        type=Path,
        required=True,
        help="Reviewer packet directory containing WAVE_COMMIT_INTENT.json.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        required=True,
        help="Absolute formal artifact root containing the reviewer stores.",
    )
    parser.add_argument(
        "--run-lease-path",
        type=Path,
        required=True,
        help="Absolute path of the pre-existing formal run lease.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = phase3_main_reviewer_commit.recover_reviewer_wave_commit_from_intent(
        transaction_directory=args.transaction_directory,
        artifact_root=args.artifact_root,
        run_lease_path=args.run_lease_path,
    )
    print(json.dumps({
        "status": "committed",
        "wave_commit_transaction_id": result.wave_commit_transaction_id,
        "intent_path": result.intent_path.resolve().as_posix(),
        "receipt_path": result.receipt_path.resolve().as_posix(),
        "commit_counts": result.commit_counts,
    }, ensure_ascii=True, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
