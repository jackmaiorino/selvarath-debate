"""Pin the materialized v6 capacity plan's canonical hash into the preflight validator.

Offline only. Reads the tracked v6 plan, prints its canonical and raw SHA-256, and with
``--write`` replaces the fail-closed placeholder constant in
``scripts/phase3_main_review_capacity_preflight.py``. It refuses to overwrite a real hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.phase2_execution import canonical_sha256  # noqa: E402

PLAN_PATH = (
    REPO_ROOT / "rejudge" / "phase3_main_review_capacity_preflight_plan_v6_2026-09-06.json"
)
VALIDATOR_PATH = REPO_ROOT / "scripts" / "phase3_main_review_capacity_preflight.py"
PLACEHOLDER = 'EXPECTED_PLAN_CANONICAL_SHA256_V6 = "pending-v6-plan-materialization"'
_PINNED_RE = re.compile(
    r'^EXPECTED_PLAN_CANONICAL_SHA256_V6 = "([0-9a-f]{64})"$', flags=re.MULTILINE
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    raw = args.plan.read_bytes()
    plan = json.loads(raw.decode("utf-8"))
    if plan.get("schema_version") != "phase3_main_review_capacity_preflight_plan_v6":
        parser.error("plan is not a v6 capacity preflight plan")
    canonical = canonical_sha256(plan)
    print(json.dumps({
        "plan_path": args.plan.resolve().as_posix(),
        "plan_canonical_sha256": canonical,
        "plan_raw_sha256": hashlib.sha256(raw).hexdigest(),
    }, indent=1))
    if not args.write:
        return 0
    source = VALIDATOR_PATH.read_text(encoding="utf-8")
    pinned = _PINNED_RE.search(source)
    if pinned is not None:
        if pinned.group(1) == canonical:
            print("validator already pins this plan")
            return 0
        parser.error("validator already pins a different v6 hash; refusing to overwrite")
    if source.count(PLACEHOLDER) != 1:
        parser.error("placeholder constant not found exactly once in the validator")
    updated = source.replace(
        PLACEHOLDER, f'EXPECTED_PLAN_CANONICAL_SHA256_V6 = "{canonical}"'
    )
    VALIDATOR_PATH.write_text(updated, encoding="utf-8", newline="\n")
    print(f"pinned {canonical} into {VALIDATOR_PATH.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
