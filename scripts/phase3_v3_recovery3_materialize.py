"""Materialize the non-authorizing N=3 recovery3 protocol and full-document pin."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_v3_recovery3_materialization as recovery3  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402


def _load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise recovery3.Recovery3MaterializationError(f"{path} must contain a JSON object")
    return value


def _write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=1, ensure_ascii=True)
        handle.write("\n")


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol-output", type=Path,
        default=REPO_ROOT / recovery3.DEFAULT_PROTOCOL_OUTPUT_PATH)
    parser.add_argument(
        "--pin-output", type=Path,
        default=REPO_ROOT / recovery3.DEFAULT_PROTOCOL_PIN_OUTPUT_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    protocol_output = args.protocol_output.resolve()
    pin_output = args.pin_output.resolve()
    prior, amendment, discovery = recovery3.load_materialization_inputs(REPO_ROOT)
    protocol = recovery3.materialize_recovery3_protocol(
        prior, amendment, discovery, project_root=REPO_ROOT, verify_archive=True)
    pin = recovery3.build_protocol_pin(
        protocol, protocol_tracked_path=_relative(protocol_output))
    if args.check:
        if _load_object(protocol_output) != protocol or _load_object(pin_output) != pin:
            raise recovery3.Recovery3MaterializationError(
                "existing recovery3 protocol or pin differs from deterministic output")
    else:
        if protocol_output.exists() or pin_output.exists():
            raise recovery3.Recovery3MaterializationError(
                "refusing to overwrite an existing recovery3 protocol or pin")
        _write_new(protocol_output, protocol)
        _write_new(pin_output, pin)
    print(json.dumps({
        "status": "verified" if args.check else "materialized",
        "protocol_path": _relative(protocol_output),
        "protocol_canonical_sha256": canonical_sha256(protocol),
        "pin_path": _relative(pin_output),
        "pin_canonical_sha256": canonical_sha256(pin),
        "final_roster": protocol["roster"]["judges_final"],
        "execution_authorized": False,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
