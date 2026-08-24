"""Build Phase 3 v3 slot, residual, and cost artifacts entirely offline."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_plan, phase3_v3_forecast, phase3_v3_inputs  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402


class ForecastMaterializationError(ValueError):
    """Raised when offline forecast inputs or output paths are incomplete."""


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ForecastMaterializationError(f"could not read JSON {path}: {exc}") from exc


def _load_json_object(path: Path, label: str) -> dict:
    value = _load_json(path)
    if not isinstance(value, dict):
        raise ForecastMaterializationError(f"{label} must be a JSON object")
    return value


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ForecastMaterializationError(
                        f"{path}:{line_number} is not a JSON object")
                rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ForecastMaterializationError(f"could not read JSONL {path}: {exc}") from exc
    return rows


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ForecastMaterializationError(
            "price-as-of-utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ForecastMaterializationError("price-as-of-utc must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _segments(path: Path) -> list[dict]:
    value = _load_json(path)
    if isinstance(value, dict):
        value = value.get("segments")
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ForecastMaterializationError(
            "cumulative-spend must be an array or an object with a segments array")
    return value


def _write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=1, sort_keys=True, ensure_ascii=True)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--tokenizer-manifest", type=Path, required=True)
    parser.add_argument("--price-snapshot", type=Path, required=True)
    parser.add_argument("--price-as-of-utc", required=True)
    parser.add_argument("--canary-results", type=Path, required=True)
    parser.add_argument("--canary-usage-ledger", type=Path, required=True)
    parser.add_argument("--cumulative-spend", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    output = args.out_dir.resolve()
    if output.exists():
        raise ForecastMaterializationError(
            f"refusing to overwrite existing forecast output directory: {output}")
    protocol = phase3_plan.load_protocol(args.protocol.resolve())
    tokenizer_manifest = _load_json_object(
        args.tokenizer_manifest.resolve(), "tokenizer manifest")
    price_snapshot = _load_json_object(args.price_snapshot.resolve(), "price snapshot")
    main_questions, held_out_questions = phase3_plan.load_reference_question_ids(
        protocol, REPO_ROOT)
    judges = protocol["roster"]["judges_final"]
    main_cells = phase3_plan.enumerate_cells(protocol, judges, main_questions)
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, judges, held_out_questions)
    canary_judgment_keys = {
        str(cell["cell_key"])
        for cell in canary_cells
        if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND
    }
    result_rows = _load_jsonl(args.canary_results.resolve())
    completed_keys = [
        str(row["cell_key"])
        for row in result_rows
        if row.get("cell_key") in canary_judgment_keys
    ]
    usage_events = _load_jsonl(args.canary_usage_ledger.resolve())
    exact_context = phase3_v3_inputs.load_exact_context_index(
        tokenizer_manifest,
        protocol=protocol,
        project_root=REPO_ROOT,
    )
    slot_frame = phase3_v3_forecast.build_slot_role_frame(
        protocol=protocol,
        planned_cells=canary_cells,
        completed_cell_keys=completed_keys,
        completed_results_sha256=phase3_v3_inputs.sha256_file(args.canary_results.resolve()),
        usage_events=usage_events,
        usage_ledger_sha256=phase3_v3_inputs.sha256_file(
            args.canary_usage_ledger.resolve()),
    )
    residual_frame = phase3_v3_forecast.build_dynamic_residual_frame(
        protocol=protocol,
        slot_role_frame=slot_frame,
        exact_context_index=exact_context,
    )
    cost_forecast = phase3_v3_forecast.build_cost_forecast(
        protocol=protocol,
        planned_main_cells=main_cells,
        dynamic_residual_frame=residual_frame,
        exact_context_index=exact_context,
        price_snapshot=price_snapshot,
        price_as_of=_timestamp(args.price_as_of_utc),
        cumulative_spend_segments=_segments(args.cumulative_spend.resolve()),
        project_root=str(REPO_ROOT),
    )

    output.mkdir(parents=True)
    try:
        artifacts = {
            "slot_role_frame.json": slot_frame,
            "dynamic_residual_frame.json": residual_frame,
            "cost_forecast.json": cost_forecast,
        }
        for name, artifact in artifacts.items():
            _write_json(output / name, artifact)
        print(json.dumps({
            "output_directory": output.as_posix(),
            "artifact_canonical_sha256s": {
                name: canonical_sha256(artifact) for name, artifact in artifacts.items()
            },
            "certification": cost_forecast["certification"],
            "within_stage_cap": cost_forecast["within_stage_cap"],
            "execution_authorized": False,
        }, indent=1))
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
