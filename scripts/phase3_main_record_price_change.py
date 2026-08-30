"""Record a fail-safe provider price-change stop signal for Phase 3 main."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_main_live, phase3_main_runtime_policies


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="phase3_main_record_price_change",
        description=(
            "Bind local evidence to one already-started Phase 3 main identity and "
            "durably stop new logical provider calls. This command grants no authority."
        ),
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--trigger-kind",
        choices=sorted(phase3_main_runtime_policies.PRICE_CHANGE_SIGNAL_TRIGGERS),
        required=True,
    )
    parser.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    try:
        result = phase3_main_live.record_provider_price_change(
            args.manifest,
            trigger_kind=args.trigger_kind,
            evidence_path=args.evidence,
            note=args.note,
        )
    except (
        phase3_main_live.Phase3MainLiveError,
        phase3_main_runtime_policies.MainRuntimePolicyError,
    ) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
