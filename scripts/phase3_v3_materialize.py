"""Materialize the Phase 3 v3 protocol after the roster-resolution artifact exists."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_v3_materialization as materialization  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise materialization.MaterializationError(f"{path} must contain a JSON object")
    return value


def _write_new_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=1, ensure_ascii=True, sort_keys=False)
        handle.write("\n")


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT.resolve())).replace("\\", "/")


def materialize(
    *,
    resolution_path: Path,
    protocol_output_path: Path,
    pin_output_path: Path,
    check_only: bool,
) -> dict:
    v2, design, amendment, resolution = materialization.load_materialization_inputs(
        REPO_ROOT, resolution_path)
    protocol = materialization.materialize_protocol(
        v2, design, resolution, amendment=amendment, project_root=REPO_ROOT)
    pin = materialization.build_protocol_pin(
        protocol, protocol_tracked_path=_relative(protocol_output_path))

    if check_only:
        if _load_json(protocol_output_path) != protocol:
            raise materialization.MaterializationError(
                "existing v3 protocol is not the deterministic materializer output")
        observed_pin = _load_json(pin_output_path)
        if observed_pin != pin:
            raise materialization.MaterializationError(
                "existing v3 protocol pin is not the deterministic materializer output")
        materialization.validate_protocol_pin(observed_pin, protocol)
    else:
        if protocol_output_path.exists() or pin_output_path.exists():
            raise materialization.MaterializationError(
                "refusing to overwrite an existing protocol or pin; use --check to verify them")
        _write_new_json(protocol_output_path, protocol)
        _write_new_json(pin_output_path, pin)

    return {
        "status": "verified" if check_only else "materialized",
        "protocol_path": _relative(protocol_output_path),
        "protocol_canonical_sha256": canonical_sha256(protocol),
        "pin_path": _relative(pin_output_path),
        "pin_canonical_sha256": canonical_sha256(pin),
        "final_roster": protocol["roster"]["judges_final"],
        "execution_authorized": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=Path, required=True)
    parser.add_argument(
        "--protocol-output", type=Path,
        default=REPO_ROOT / materialization.DEFAULT_PROTOCOL_OUTPUT_PATH)
    parser.add_argument(
        "--pin-output", type=Path,
        default=REPO_ROOT / materialization.DEFAULT_PROTOCOL_PIN_OUTPUT_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    resolution_path = args.resolution
    if not resolution_path.is_absolute():
        resolution_path = REPO_ROOT / resolution_path
    protocol_output = args.protocol_output
    if not protocol_output.is_absolute():
        protocol_output = REPO_ROOT / protocol_output
    pin_output = args.pin_output
    if not pin_output.is_absolute():
        pin_output = REPO_ROOT / pin_output
    result = materialize(
        resolution_path=resolution_path,
        protocol_output_path=protocol_output,
        pin_output_path=pin_output,
        check_only=bool(args.check),
    )
    print(json.dumps(result, indent=1, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
