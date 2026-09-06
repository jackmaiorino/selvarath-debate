"""Run the two-execution fake-only Phase 3 main harness at the current source commit.

Offline only: the harness uses the module-owned deterministic client, makes no provider
call, and grants no authority. The receipt it writes must be bound by the exact main
manifest built from the same clean source commit and the same formal artifact root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_main_harness  # noqa: E402


def _load(rel: str) -> dict:
    value = json.loads((REPO_ROOT / rel).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{rel} must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--formal-artifact-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--protocol", default="rejudge/phase3_protocol_v3_r6.json")
    parser.add_argument("--prompt-bundle", default="rejudge/phase2_prompt_bundle.json")
    parser.add_argument(
        "--role-limits", default="rejudge/phase3_v3_role_limits_r11_2026-09-06.json"
    )
    args = parser.parse_args(argv)

    result = phase3_main_harness.run_isolated_harness(
        protocol=_load(args.protocol),
        prompt_bundle=_load(args.prompt_bundle),
        role_limits=_load(args.role_limits),
        project_root=REPO_ROOT,
        harness_root=args.harness_root.resolve(),
        receipt_path=args.receipt.resolve(),
        harness_seed=args.seed,
        formal_artifact_root=args.formal_artifact_root.resolve(),
    )
    receipt_raw = args.receipt.resolve().read_bytes()
    print(json.dumps({
        "status": result["status"],
        "receipt_path": args.receipt.resolve().as_posix(),
        "receipt_raw_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "first_output_store_sha256": result["first_output_store_sha256"],
        "rerun_output_store_sha256": result["rerun_output_store_sha256"],
        "provider_calls_authorized": result["provider_calls_authorized"],
        "main_run_spend_authorized": result["main_run_spend_authorized"],
    }, indent=1))
    return 0 if result["status"] == "bit_identical_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
