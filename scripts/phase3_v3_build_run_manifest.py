"""Build the small Phase 3 v3 run manifest from committed local artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_v3_run_manifest_materialization as materialization  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402


def _seeds(values: list[str]) -> dict[str, int]:
    seeds: dict[str, int] = {}
    for value in values:
        name, separator, raw_seed = value.partition("=")
        if not separator or not name or name in seeds:
            raise materialization.RunManifestMaterializationError(
                f"invalid or duplicate --seed value: {value!r}")
        try:
            seed = int(raw_seed)
        except ValueError as exc:
            raise materialization.RunManifestMaterializationError(
                f"seed must be an integer: {value!r}") from exc
        if seed < 0:
            raise materialization.RunManifestMaterializationError(
                f"seed must be non-negative: {value!r}")
        seeds[name] = seed
    return seeds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--protocol-pin", type=Path, required=True)
    parser.add_argument("--tokenizer-manifest", type=Path, required=True)
    parser.add_argument("--price-snapshot", type=Path, required=True)
    parser.add_argument("--dependency-lock", type=Path, default=REPO_ROOT / "uv.lock")
    parser.add_argument("--seed", action="append", required=True)
    parser.add_argument("--planned-output", action="append", required=True)
    parser.add_argument("--input", action="append", type=Path, default=[],
                        help="additional repo-local canonical-JSON execution input")
    parser.add_argument("--gpu-ordinal", type=int)
    parser.add_argument("--harness-seed", required=True)
    parser.add_argument("--recorded-at-utc")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    output = args.out.resolve()
    if output.exists():
        raise materialization.RunManifestMaterializationError(
            f"refusing to overwrite existing run manifest: {output}")
    manifest = materialization.materialize_run_manifest(
        protocol_path=args.protocol.resolve(),
        protocol_pin_path=args.protocol_pin.resolve(),
        tokenizer_manifest_path=args.tokenizer_manifest.resolve(),
        price_snapshot_path=args.price_snapshot.resolve(),
        dependency_lock_path=args.dependency_lock.resolve(),
        seeds=_seeds(args.seed),
        planned_output_paths=args.planned_output,
        gpu_ordinal_or_not_used=(
            "not_used" if args.gpu_ordinal is None else args.gpu_ordinal),
        harness_seed_name=args.harness_seed,
        project_root=REPO_ROOT,
        recorded_at_utc=args.recorded_at_utc,
        extra_input_paths=args.input,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    print(json.dumps({
        "path": output.as_posix(),
        "run_id": manifest["run_id"],
        "canonical_sha256": canonical_sha256(manifest),
        "execution_authorized": False,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
