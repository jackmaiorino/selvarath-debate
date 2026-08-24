"""Build a Phase 3 v3 price snapshot from an already-saved Together catalog."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_plan  # noqa: E402
from rejudge import phase3_v3_price_materialization as materialization  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--raw-catalog", type=Path, required=True)
    parser.add_argument("--verified-at-utc", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    output = args.out.resolve()
    if output.exists():
        raise materialization.PriceMaterializationError(
            f"refusing to overwrite existing price snapshot: {output}")
    protocol = phase3_plan.load_protocol(args.protocol.resolve())
    raw_catalog_path = args.raw_catalog.resolve()
    snapshot = materialization.build_price_snapshot(
        protocol=protocol,
        raw_catalog=_load_json(raw_catalog_path),
        raw_catalog_path=raw_catalog_path,
        verified_at_utc=args.verified_at_utc,
        project_root=REPO_ROOT,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(snapshot, handle, indent=1, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    print(json.dumps({
        "path": output.as_posix(),
        "canonical_sha256": canonical_sha256(snapshot),
        "execution_authorized": False,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
