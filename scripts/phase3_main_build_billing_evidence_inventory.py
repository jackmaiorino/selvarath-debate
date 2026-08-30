"""Build a non-authorizing Phase 3 billing evidence inventory from explicit files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from rejudge import phase3_main_billing_evidence_inventory as inventory


REPO_ROOT = Path(__file__).resolve().parents[1]


def _source(value: str) -> tuple[str, Path]:
    source_id, separator, raw_path = value.partition("=")
    if not separator or not source_id or not raw_path:
        raise argparse.ArgumentTypeError("source must use exact ID=PATH syntax")
    return source_id, Path(raw_path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="phase3_main_build_billing_evidence_inventory",
        description=(
            "Inventory only explicitly named local billing evidence. This does not "
            "establish authoritative completeness or authorize execution."
        ),
    )
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--window-start-utc", required=True)
    parser.add_argument("--window-end-utc", required=True)
    parser.add_argument("--ledger", action="append", type=_source, default=[], metavar="ID=PATH")
    parser.add_argument("--aux", action="append", type=_source, default=[], metavar="ID=PATH")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        record = inventory.build_inventory(
            window_start_utc=args.window_start_utc,
            window_end_utc=args.window_end_utc,
            ledger_sources=args.ledger,
            auxiliary_sources=args.aux,
            project_root=args.project_root,
        )
        output = (
            args.output.resolve()
            if args.output.is_absolute()
            else (args.project_root / args.output).resolve()
        )
        inventory.write_inventory_exclusive(output, record)
        validated = inventory.load_and_validate_inventory(
            output, project_root=args.project_root
        )
    except inventory.BillingEvidenceInventoryError as exc:
        parser.error(str(exc))
    print(json.dumps({
        "inventory_path": output.as_posix(),
        **validated,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
