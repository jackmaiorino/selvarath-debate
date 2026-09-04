"""Recompute the Phase 3 main cost forecast with an owner-ratified cap, offline only."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_plan, phase3_v3_forecast, phase3_v3_inputs  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402


class RatifiedForecastMaterializationError(ValueError):
    """An offline forecast input or exclusive output failed validation."""


def _load_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RatifiedForecastMaterializationError(
            f"could not read {label} at {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RatifiedForecastMaterializationError(f"{label} must be a JSON object")
    return value


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as exc:
        raise RatifiedForecastMaterializationError(
            "price-as-of-utc must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RatifiedForecastMaterializationError("price-as-of-utc must use UTC")
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--tokenizer-manifest", type=Path, required=True)
    parser.add_argument("--dynamic-residual-frame", type=Path, required=True)
    parser.add_argument("--price-snapshot", type=Path, required=True)
    parser.add_argument("--price-as-of-utc", required=True)
    parser.add_argument("--cumulative-spend", type=Path, required=True)
    parser.add_argument("--stage-cap-ratification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    output = args.output.resolve()
    if output.exists():
        raise RatifiedForecastMaterializationError(
            f"refusing to overwrite existing forecast: {output}"
        )
    protocol = phase3_plan.load_protocol(args.protocol.resolve())
    tokenizer_manifest = _load_object(
        args.tokenizer_manifest.resolve(), "tokenizer manifest"
    )
    dynamic_frame = _load_object(
        args.dynamic_residual_frame.resolve(), "dynamic residual frame"
    )
    price_snapshot = _load_object(args.price_snapshot.resolve(), "price snapshot")
    spend = _load_object(args.cumulative_spend.resolve(), "cumulative spend")
    ratification = _load_object(
        args.stage_cap_ratification.resolve(), "stage-cap ratification"
    )
    segments = spend.get("segments")
    if not isinstance(segments, list):
        raise RatifiedForecastMaterializationError(
            "cumulative spend must contain a segments array"
        )

    main_questions, _ = phase3_plan.load_reference_question_ids(protocol, REPO_ROOT)
    cells = phase3_plan.enumerate_cells(
        protocol, protocol["roster"]["judges_final"], main_questions
    )
    exact_context = phase3_v3_inputs.load_exact_context_index(
        tokenizer_manifest,
        protocol=protocol,
        project_root=REPO_ROOT,
    )
    forecast = phase3_v3_forecast.build_cost_forecast(
        protocol=protocol,
        planned_main_cells=cells,
        dynamic_residual_frame=dynamic_frame,
        exact_context_index=exact_context,
        price_snapshot=price_snapshot,
        price_as_of=_timestamp(args.price_as_of_utc),
        cumulative_spend_segments=segments,
        stage_cap_ratification=ratification,
        project_root=str(REPO_ROOT),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(forecast, handle, indent=1, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    print(json.dumps({
        "output": output.as_posix(),
        "canonical_sha256": canonical_sha256(forecast),
        "certification": forecast["certification"],
        "within_stage_cap": forecast["within_stage_cap"],
        "projected_main_usd": forecast["projected_main_usd"],
        "projected_stage_total_usd": forecast["projected_stage_total_usd"],
        "stage_cap_usd": forecast["stage_cap_usd"],
        "execution_authorized": False,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
