"""Capture read-only authenticated Together billing evidence for Phase 3 main."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_main_together_billing_capture as capture


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="phase3_main_capture_together_billing",
        description=(
            "Make only authenticated Together identity and billing-usage GET requests. "
            "This command cannot authorize inference, execution, or spend."
        ),
    )
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--window-start-utc", required=True)
    parser.add_argument("--window-end-utc", required=True)
    parser.add_argument("--finalized-through-utc", required=True)
    parser.add_argument("--expected-account-identity-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    api_key = os.environ.get("TOGETHER_API_KEY", "")
    if not api_key.strip():
        parser.error("TOGETHER_API_KEY is missing or blank")
    output = (
        args.output.resolve()
        if args.output.is_absolute()
        else (args.project_root / args.output).resolve()
    )
    try:
        result = capture.capture_and_write(
            output_path=output,
            window_start_utc=args.window_start_utc,
            window_end_utc=args.window_end_utc,
            finalized_through_utc=args.finalized_through_utc,
            expected_account_identity_sha256=args.expected_account_identity_sha256,
            api_key=api_key,
            timeout_seconds=args.timeout_seconds,
        )
    except capture.TogetherBillingCaptureError as exc:
        parser.error(str(exc))
    summary = {
        "capture_path": result["capture_path"].as_posix(),
        "capture_raw_sha256": result["capture_raw_sha256"],
        "observed_at_utc": result["observed_at_utc"],
        "account_identity_sha256": result["account_identity_sha256"],
        "provider_delta_usd": result["provider_delta_usd"],
        "provider_settlement": result["provider_settlement"],
        "inference_calls": 0,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
