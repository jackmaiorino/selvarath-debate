"""Write the exact 28-name input-path map consumed by the Phase 3 main launch builder.

Offline only. The stable bindings are the ones audited in
``reports/2026-09-04-phase3-main-real-input-launch-audit.md``; the launch-time bindings
(capacity v6 evidence, fresh price capture, fresh certified forecast, harness receipt) are
supplied as arguments. Every path must already exist. The output must sit outside the
clean source checkout because ``scripts/phase3_main_build_launch_package.py`` requires a
clean tree including untracked files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_main_manifest  # noqa: E402

STABLE_INPUTS = {
    "analysis_pins": "rejudge/phase3_main_analysis_pins_2026-08-29.json",
    "billing_reconciliation": (
        "rejudge/phase3_main_billing_reconciliation_candidate_2026-09-04.json"
    ),
    "canary_finalization": "rejudge/phase3_v3_finalization_record_2026-08-29.json",
    "context_blocklist": "rejudge/phase3_main_context_blocklist_r6_2026-09-04.json",
    "dynamic_residual_frame": (
        "E:/selvarath-archive/phase3-main-forecast-2026-09-03/dynamic_residual_frame.json"
    ),
    "main_transcript_bundle": (
        "E:/selvarath-archive/phase3-materialization-2026-08-18/"
        "phase3_transcript_bundle_main_2026-08-18.json"
    ),
    "price_change_policy": "rejudge/phase3_main_price_change_policy_2026-08-30.json",
    "prompt_bundle": "rejudge/phase2_prompt_bundle.json",
    "protocol": "rejudge/phase3_protocol_v3_r6.json",
    "reviewer_failure_policy": (
        "rejudge/phase3_main_reviewer_failure_policy_2026-08-30.json"
    ),
    "reviewer_prompt": "rejudge/phase2_reviewer_prompt_2026-07-23.json",
    "reviewer_usage_policy": "rejudge/phase3_main_reviewer_usage_policy_2026-08-30.json",
    "role_limits": "rejudge/phase3_v3_role_limits_r10_2026-08-28.json",
    "scope_decision": "rejudge/phase3_main_scope_capacity_decision_2026-08-29.json",
    "stage_cap_ratification": (
        "rejudge/phase3_main_console_billing_and_stage_cap_ratification_2026-09-04.json"
    ),
    "tokenizer_manifest": "rejudge/phase3_v3_exact_tokenizer_manifest_r7_2026-08-28.json",
    "transcript_verification": "rejudge/phase3_transcript_verification_2026-08-18.json",
}
CAPACITY_PLAN = "rejudge/phase3_main_review_capacity_preflight_plan_v6_2026-09-06.json"


def _posix(path: Path) -> str:
    return path.resolve().as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity-root", type=Path, required=True)
    parser.add_argument("--capacity-commit", required=True, help="7-hex short commit tag")
    parser.add_argument("--capacity-date", default="2026-09-06")
    parser.add_argument("--price-root", type=Path, required=True)
    parser.add_argument("--forecast", type=Path, required=True)
    parser.add_argument("--harness-receipt", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if re.fullmatch(r"[0-9a-f]{7}", args.capacity_commit) is None:
        parser.error("--capacity-commit must be a 7-hex lowercase short commit")
    out = args.out.resolve()
    try:
        out.relative_to(REPO_ROOT)
    except ValueError:
        pass
    else:
        parser.error("--out must be outside the clean source checkout")
    if out.exists():
        parser.error(f"refusing to overwrite {out}")

    root = args.capacity_root.resolve()
    tag = f"{args.capacity_date}_{args.capacity_commit}"
    authorization = root / f"capacity_authorization_draft_{tag}.json"
    inputs = {
        **STABLE_INPUTS,
        "capacity_plan": CAPACITY_PLAN,
        "capacity_dispatch_history": _posix(root / "dispatch_history.jsonl"),
        "capacity_result": _posix(root / f"capacity_result_{tag}.json"),
        "capacity_execution_manifest": _posix(root / f"capacity_execution_manifest_{tag}.json"),
        "capacity_execution_authorization": _posix(authorization),
        "capacity_execution_authorization_signature": _posix(
            authorization.with_name(f"{authorization.name}.sig")
        ),
        "price_snapshot": _posix(args.price_root / "price-snapshot-v2.json"),
        "raw_provider_catalog": _posix(args.price_root / "raw-provider-catalog.json"),
        "raw_serverless_endpoints": _posix(args.price_root / "raw-serverless-endpoints.json"),
        "certified_cost_forecast": _posix(args.forecast),
        "harness_receipt": _posix(args.harness_receipt),
    }
    expected = set(phase3_main_manifest.REQUIRED_INPUT_BINDINGS)
    if set(inputs) != expected:
        parser.error(
            "input names drifted: "
            f"missing={sorted(expected - set(inputs))!r} "
            f"unexpected={sorted(set(inputs) - expected)!r}"
        )
    missing = [
        name for name, value in sorted(inputs.items())
        if not (Path(value) if Path(value).is_absolute() else REPO_ROOT / value).is_file()
    ]
    if missing:
        parser.error(f"bound inputs are missing on disk: {missing!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(sorted(inputs.items())), indent=1) + "\n")
    print(json.dumps({"input_paths": out.as_posix(), "count": len(inputs)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
