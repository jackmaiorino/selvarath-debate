"""Evaluate the approved phase-3 successor design without provider calls.

The output is a design and materialization readiness report. It is never an execution
authorization. The v2 archive is read only and is used only to evaluate the newly explicit pace
formula and to show whether its historical ledger can satisfy the corrected forecast contract.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rejudge import phase3_plan, phase3_v3_inputs, phase3_v3_run_manifest  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402
from phase3_canary_closeout_v2 import (  # noqa: E402
    collect_packet_commits,
    parse_timestamp,
    stream_jsonl,
    validate_decision_store,
)


DESIGN_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_v3_successor_design_2026-08-23.json"
CLOSEOUT_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_canary_closeout_v2_2026-08-23.json"
ARCHIVE_DIR_DEFAULT = Path("E:/selvarath-archive/phase3-v2-2026-08-21")
PROTOCOL_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_protocol_v3_r2.json"
PROTOCOL_PIN_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_protocol_v3_pin_r2.json"
OUTPUT_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_v3_successor_preflight_2026-08-23.json"

EXPECTED_DESIGN_CANONICAL_SHA256 = (
    "75c1790a54d7a6ca780839f8a1efe4ca5a5db9aee075d4c473be516004cc0479")
EXPECTED_PROVISIONAL_ROSTER = (
    "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "google/gemma-3n-E4B-it",
    "Qwen/Qwen3.7-Max",
)


def load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def validate_design(path: Path) -> dict[str, Any]:
    design = load_json_object(path)
    actual_hash = canonical_sha256(design)
    if actual_hash != EXPECTED_DESIGN_CANONICAL_SHA256:
        raise ValueError(
            "successor design hash mismatch: "
            f"expected {EXPECTED_DESIGN_CANONICAL_SHA256}, got {actual_hash}")
    if design.get("schema_version") != "phase3_v3_successor_design_v1":
        raise ValueError("unexpected successor design schema")
    if design.get("status") != "owner_approved_offline_design_pending_materialization":
        raise ValueError("successor design is not in the approved offline status")
    if design.get("execution_authorized") is not False:
        raise ValueError("successor design must not authorize execution")
    if design.get("main_run_authorized") is not False:
        raise ValueError("successor design must not authorize main spend")
    roster = tuple(design.get("roster", {}).get("provisional_judges", []))
    if roster != EXPECTED_PROVISIONAL_ROSTER:
        raise ValueError("successor provisional roster changed")
    pace = design.get("continuous_rate_l90", {})
    if pace.get("block_seconds") != 3600 or pace.get("minimum_full_blocks") != 8:
        raise ValueError("successor pace pins changed")
    forecast = design.get("forecast_contract", {})
    if forecast.get("required_main_transcript_count") != 492:
        raise ValueError("successor forecast transcript count changed")
    if forecast.get("transport_multiplier") != 1.15:
        raise ValueError("successor transport multiplier changed")
    return design


def _betacf(a: float, b: float, x: float, max_iter: int = 200, eps: float = 1e-12) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((qam + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    factor = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return factor * _betacf(a, b, x) / a
    return 1.0 - factor * _betacf(b, a, 1 - x) / b


def _t_cdf(value: float, df: int) -> float:
    x = df / (df + value * value)
    probability = _betai(df / 2.0, 0.5, x)
    return 1.0 - 0.5 * probability if value > 0 else 0.5 * probability


def t_ppf(probability: float, df: int) -> float:
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must be strictly between zero and one")
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    low, high = -1000.0, 1000.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if _t_cdf(middle, df) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def compute_continuous_rate_l90(
    *, ruling_times: Sequence[datetime], opens_at: datetime, closes_at: datetime,
    block_seconds: int = 3600, minimum_full_blocks: int = 8,
) -> dict[str, Any]:
    """Apply the frozen one-hour, zero-filled central-90-percent t construction."""
    start = opens_at.astimezone(timezone.utc)
    end = closes_at.astimezone(timezone.utc)
    if end < start:
        raise ValueError("pace window closes before it opens")
    if block_seconds <= 0 or minimum_full_blocks < 2:
        raise ValueError("invalid pace block pins")
    times = sorted(value.astimezone(timezone.utc) for value in ruling_times)
    if any(value < start or value > end for value in times):
        raise ValueError("ruling timestamp lies outside the supplied pace window")

    elapsed_seconds = (end - start).total_seconds()
    full_blocks = int(elapsed_seconds // block_seconds)
    counts = [0] * full_blocks
    full_window_end = start + timedelta(seconds=full_blocks * block_seconds)
    for value in times:
        if value >= full_window_end:
            continue
        index = int((value - start).total_seconds() // block_seconds)
        counts[index] += 1

    rates = [count * 86400.0 / block_seconds for count in counts]
    result: dict[str, Any] = {
        "block_seconds": block_seconds,
        "minimum_full_blocks": minimum_full_blocks,
        "full_block_count": full_blocks,
        "full_window_closes_at_utc": full_window_end.isoformat().replace("+00:00", "Z"),
        "discarded_partial_seconds": elapsed_seconds - full_blocks * block_seconds,
        "unique_rulings_in_supplied_window": len(times),
        "unique_rulings_in_full_blocks": sum(counts),
        "unique_rulings_in_discarded_tail": len(times) - sum(counts),
        "counts_per_full_block": counts,
        "rates_per_24_elapsed_hours": rates,
        "interval": "central 90 percent Student t interval over one-hour block rates",
    }
    if full_blocks < minimum_full_blocks:
        return {
            **result,
            "estimable": False,
            "reason": f"requires at least {minimum_full_blocks} full one-hour blocks",
            "mean_rate": None,
            "sample_sd": None,
            "standard_error": None,
            "t_critical_0_95": None,
            "L90_rulings_per_24_elapsed_hours": None,
        }

    mean_rate = statistics.fmean(rates)
    sample_sd = statistics.stdev(rates)
    standard_error = sample_sd / math.sqrt(full_blocks)
    critical = t_ppf(0.95, full_blocks - 1)
    lower = max(0.0, mean_rate - critical * standard_error)
    return {
        **result,
        "estimable": True,
        "reason": None,
        "mean_rate": mean_rate,
        "sample_sd": sample_sd,
        "standard_error": standard_error,
        "degrees_of_freedom": full_blocks - 1,
        "t_critical_0_95": critical,
        "L90_rulings_per_24_elapsed_hours": lower,
    }


def earliest_required_packet_commits(
    required_payloads: Iterable[str], packet_commits: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, datetime], list[str]]:
    required = set(str(value) for value in required_payloads)
    earliest: dict[str, datetime] = {}
    for packet in sorted(packet_commits, key=lambda item: item["committed_at"]):
        committed_at = packet["committed_at"].astimezone(timezone.utc)
        for raw_payload in packet["payloads"]:
            payload = str(raw_payload)
            if payload in required:
                earliest.setdefault(payload, committed_at)
    return earliest, sorted(required - set(earliest))


def audit_unknown_charge_token_splits(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Check token fields needed to account for every charged terminal attempt."""
    reservations: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if event.get("status") == "reserved" and event.get("attempt_id") is not None:
            reservations[str(event["attempt_id"])] = event

    unknown = [event for event in events if event.get("status") == "unknown_charge"]
    missing: list[str] = []
    invalid: list[str] = []
    total_mismatch: list[str] = []
    for event in unknown:
        attempt_id = str(event.get("attempt_id"))
        reservation = reservations.get(attempt_id, {})
        prompt = event.get("reserved_prompt_tokens", reservation.get("reserved_prompt_tokens"))
        completion = event.get(
            "reserved_completion_tokens", reservation.get("reserved_completion_tokens"))
        if prompt is None or completion is None:
            missing.append(attempt_id)
            continue
        if (not isinstance(prompt, int) or isinstance(prompt, bool) or prompt < 0
                or not isinstance(completion, int) or isinstance(completion, bool)
                or completion < 0):
            invalid.append(attempt_id)
            continue
        combined = event.get("estimated_tokens", reservation.get("estimated_tokens"))
        if isinstance(combined, int) and not isinstance(combined, bool):
            if prompt + completion != combined:
                total_mismatch.append(attempt_id)

    actual_token_statuses = {"success", "charged_malformed"}
    missing_actual = [
        str(event.get("attempt_id"))
        for event in events
        if event.get("status") in actual_token_statuses
        and (not isinstance(event.get("prompt_tokens"), int)
             or isinstance(event.get("prompt_tokens"), bool)
             or event["prompt_tokens"] < 0
             or not isinstance(event.get("completion_tokens"), int)
             or isinstance(event.get("completion_tokens"), bool)
             or event["completion_tokens"] < 0)
    ]

    return {
        "actual_token_terminal_count": sum(
            event.get("status") in actual_token_statuses for event in events),
        "missing_actual_token_count": len(missing_actual),
        "missing_actual_token_attempt_ids": sorted(missing_actual),
        "unknown_charge_count": len(unknown),
        "unknown_charges_with_explicit_split": (
            len(unknown) - len(missing) - len(invalid) - len(total_mismatch)),
        "missing_split_count": len(missing),
        "invalid_split_count": len(invalid),
        "split_total_mismatch_count": len(total_mismatch),
        "missing_split_attempt_ids": sorted(missing),
        "invalid_split_attempt_ids": sorted(invalid),
        "split_total_mismatch_attempt_ids": sorted(total_mismatch),
        "compatible_with_successor_forecast": (
            not missing and not invalid and not total_mismatch and not missing_actual),
    }


def validate_exact_tokenizer_manifest(
    manifest: Mapping[str, Any], *, protocol: Mapping[str, Any],
    verify_files: bool = True, project_root: Path | None = None,
) -> dict[str, Any]:
    """Apply the full provider-tokenizer and role-corpus gate."""
    return phase3_v3_inputs.validate_exact_tokenizer_manifest(
        manifest,
        protocol=protocol,
        verify_files=verify_files,
        project_root=project_root,
    )


def validate_price_snapshot(
    snapshot: Mapping[str, Any], *, protocol: Mapping[str, Any], as_of: datetime,
    max_age: timedelta = timedelta(hours=24), project_root: Path | None = None,
    verify_catalog: bool = True,
) -> dict[str, Any]:
    return phase3_v3_inputs.validate_price_snapshot(
        snapshot,
        protocol=protocol,
        as_of=as_of,
        max_age=max_age,
        project_root=project_root,
        verify_catalog=verify_catalog,
    )


def validate_run_manifest(
    manifest: Mapping[str, Any], *, protocol: Mapping[str, Any],
    protocol_pin: Mapping[str, Any], tokenizer_manifest: Mapping[str, Any],
    price_snapshot: Mapping[str, Any], project_root: Path | None = None,
    verify_external_files: bool = True,
) -> dict[str, Any]:
    phase3_v3_run_manifest.validate_run_manifest(
        manifest,
        protocol=protocol,
        protocol_pin=protocol_pin,
        tokenizer_manifest=tokenizer_manifest,
        price_snapshot=price_snapshot,
        project_root=project_root,
        verify_external_files=verify_external_files,
    )
    return {
        "validation": "pass",
        "canonical_sha256": canonical_sha256(manifest),
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "git_commit": manifest["git_commit"],
        "harness_status": manifest["harness_check"]["status"],
        "execution_authorized": False,
    }


def historical_pace_diagnostic(
    *, closeout: Mapping[str, Any], archive_dir: Path, block_seconds: int,
    minimum_full_blocks: int,
) -> dict[str, Any]:
    decision_rows, integrity = validate_decision_store(
        archive_dir / "phase3_reviewer_decisions.jsonl")
    expected_integrity = closeout["archive_integrity"]["decision_store"]
    for field in ("row_count", "last_sequence", "last_event_hash"):
        if integrity[field] != expected_integrity[field]:
            raise ValueError(f"v2 decision store changed after close-out: {field}")
    earliest, missing = earliest_required_packet_commits(
        (str(row["payload_sha256"]) for row in decision_rows),
        collect_packet_commits(archive_dir),
    )
    if missing:
        raise ValueError(f"v2 packet commits miss {len(missing)} required payloads")
    window = closeout["review_pace"]["final_window"]
    opens_at = parse_timestamp(str(window["opens_at_utc"]))
    closes_at = parse_timestamp(str(window["closes_at_utc"]))
    ruling_times = sorted(
        value for value in earliest.values() if opens_at <= value <= closes_at)
    if len(ruling_times) != window["unique_rulings"]:
        raise ValueError("v2 final-window ruling count changed after close-out")
    return compute_continuous_rate_l90(
        ruling_times=ruling_times,
        opens_at=opens_at,
        closes_at=closes_at,
        block_seconds=block_seconds,
        minimum_full_blocks=minimum_full_blocks,
    )


def build_preflight(
    *, design_path: Path, closeout_path: Path, archive_dir: Path,
    protocol_path: Path | None = None,
    tokenizer_manifest_path: Path | None = None, price_snapshot_path: Path | None = None,
    price_as_of: datetime | None = None, protocol_pin_path: Path | None = None,
    run_manifest_path: Path | None = None,
) -> dict[str, Any]:
    design = validate_design(design_path)
    closeout = load_json_object(closeout_path)
    closeout_binding = design["source_bindings"][
        "rejudge/phase3_canary_closeout_v2_2026-08-23.json"]
    if canonical_sha256(closeout) != closeout_binding:
        raise ValueError("v2 close-out does not match the successor binding")
    if Path(str(closeout["archive_dir"])) != archive_dir:
        raise ValueError("v2 archive path does not match the bound close-out")

    pace_pins = design["continuous_rate_l90"]
    historical_pace = historical_pace_diagnostic(
        closeout=closeout,
        archive_dir=archive_dir,
        block_seconds=int(pace_pins["block_seconds"]),
        minimum_full_blocks=int(pace_pins["minimum_full_blocks"]),
    )
    usage_events = stream_jsonl(archive_dir / "phase3_usage.jsonl")
    historical_usage_compatibility = audit_unknown_charge_token_splits(usage_events)

    protocol: dict[str, Any] | None = None
    protocol_validation: dict[str, Any]
    if protocol_path is None:
        required_models = list(EXPECTED_PROVISIONAL_ROSTER)
        protocol_validation = {
            "validation": "pending",
            "reason": "final roster resolution and v3 protocol are absent",
        }
    else:
        protocol = phase3_plan.load_protocol(protocol_path)
        if protocol.get("schema_version") != "phase3_plan_v3":
            raise ValueError("successor protocol must use phase3_plan_v3")
        required_models = list(protocol["roster"]["judges_final"])
        protocol_validation = {
            "validation": "pass",
            "path": protocol_path.as_posix(),
            "canonical_sha256": canonical_sha256(protocol),
            "final_roster": required_models,
            "execution_authorized": False,
        }

    tokenizer_manifest: dict[str, Any] | None = None
    tokenizer_validation: dict[str, Any]
    if tokenizer_manifest_path is None:
        tokenizer_validation = {
            "validation": "pending",
            "reason": "no exact-tokenizer manifest supplied",
        }
    else:
        if protocol is None:
            raise ValueError("a resolved v3 protocol is required to validate exact tokenizers")
        tokenizer_manifest = load_json_object(tokenizer_manifest_path)
        tokenizer_validation = validate_exact_tokenizer_manifest(
            tokenizer_manifest, protocol=protocol, project_root=REPO_ROOT)

    price_snapshot: dict[str, Any] | None = None
    price_validation: dict[str, Any]
    if price_snapshot_path is None:
        price_validation = {
            "validation": "pending",
            "reason": "no fresh price snapshot supplied",
        }
    else:
        if protocol is None:
            raise ValueError("a resolved v3 protocol is required to validate a price snapshot")
        if price_as_of is None:
            raise ValueError("price-as-of is required when validating a price snapshot")
        price_snapshot = load_json_object(price_snapshot_path)
        price_validation = validate_price_snapshot(
            price_snapshot, protocol=protocol, as_of=price_as_of, project_root=REPO_ROOT)

    run_manifest_validation: dict[str, Any]
    if run_manifest_path is None:
        run_manifest_validation = {
            "validation": "pending",
            "reason": "no successor run manifest supplied",
        }
    else:
        if protocol is None or tokenizer_manifest is None or price_snapshot is None:
            raise ValueError(
                "protocol, exact tokenizers, and prices are required to validate a run manifest")
        if protocol_pin_path is None:
            raise ValueError("protocol pin is required to validate a run manifest")
        run_manifest = load_json_object(run_manifest_path)
        protocol_pin = load_json_object(protocol_pin_path)
        run_manifest_validation = validate_run_manifest(
            run_manifest,
            protocol=protocol,
            protocol_pin=protocol_pin,
            tokenizer_manifest=tokenizer_manifest,
            price_snapshot=price_snapshot,
            project_root=REPO_ROOT,
            verify_external_files=False,
        )
        run_manifest_validation["path"] = run_manifest_path.as_posix()

    blockers = ["fresh complete successor canary has not run"]
    if protocol is None:
        blockers[:0] = [
            "the fixed successor protocol and namespace were not supplied",
        ]
    if tokenizer_validation["validation"] != "pass":
        blockers.append("exact provider-matched tokenizer corpus is absent")
    if price_validation["validation"] != "pass":
        blockers.append("fresh serverless availability and price snapshot is absent")
    if run_manifest_validation["validation"] != "pass":
        blockers.append("successor run manifest is not materialized")
    elif run_manifest_validation["harness_status"] != "bit_identical_pass":
        blockers.append("bit-identical one-seed harness check has not run")

    offline_canary_materialization_ready = all([
        protocol is not None,
        tokenizer_validation["validation"] == "pass",
        price_validation["validation"] == "pass",
        run_manifest_validation["validation"] == "pass",
        historical_pace["estimable"],
    ])
    if offline_canary_materialization_ready:
        blockers.append("separate owner canary spend authorization is absent")

    return {
        "schema_version": "phase3_v3_successor_preflight_v1",
        "generated_by": "scripts/phase3_v3_successor_preflight.py",
        "read_only_derivation": True,
        "provider_calls_made": 0,
        "design": {
            "path": design_path.as_posix(),
            "canonical_sha256": canonical_sha256(design),
            "validation": "pass",
            "execution_authorized": False,
        },
        "successor_protocol": protocol_validation,
        "provisional_roster": list(EXPECTED_PROVISIONAL_ROSTER),
        "resolved_final_roster": (required_models if protocol is not None else None),
        "historical_v2_pace_diagnostic_only": historical_pace,
        "historical_v2_usage_forecast_compatibility": historical_usage_compatibility,
        "exact_tokenizer_manifest": tokenizer_validation,
        "price_snapshot": price_validation,
        "run_manifest": run_manifest_validation,
        "successor_design_ready": historical_pace["estimable"],
        "offline_canary_materialization_ready": offline_canary_materialization_ready,
        "paid_preflight_ready": offline_canary_materialization_ready,
        "canary_spend_authorized": False,
        "main_authorization_ready": False,
        "blockers": blockers,
        "non_claims": [
            "The historical v2 L90 is not a successor configuration selection.",
            "The historical v2 usage ledger is not repaired or rewritten.",
            "No main forecast is computed from missing or approximate tokenizer inputs.",
            "This artifact authorizes no provider call, canary, main run, or GPU work.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=DESIGN_PATH_DEFAULT)
    parser.add_argument("--closeout", type=Path, default=CLOSEOUT_PATH_DEFAULT)
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR_DEFAULT)
    parser.add_argument("--protocol", type=Path, default=PROTOCOL_PATH_DEFAULT)
    parser.add_argument("--tokenizer-manifest", type=Path)
    parser.add_argument("--price-snapshot", type=Path)
    parser.add_argument("--price-as-of", type=str)
    parser.add_argument("--protocol-pin", type=Path, default=PROTOCOL_PIN_PATH_DEFAULT)
    parser.add_argument("--run-manifest", type=Path)
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH_DEFAULT)
    args = parser.parse_args(argv)
    price_as_of = parse_timestamp(args.price_as_of) if args.price_as_of else None
    artifact = build_preflight(
        design_path=args.design.resolve(),
        closeout_path=args.closeout.resolve(),
        archive_dir=args.archive_dir,
        protocol_path=args.protocol.resolve() if args.protocol else None,
        tokenizer_manifest_path=(
            args.tokenizer_manifest.resolve() if args.tokenizer_manifest else None),
        price_snapshot_path=args.price_snapshot.resolve() if args.price_snapshot else None,
        price_as_of=price_as_of,
        protocol_pin_path=args.protocol_pin.resolve() if args.protocol_pin else None,
        run_manifest_path=args.run_manifest.resolve() if args.run_manifest else None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(artifact, indent=1, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    pace = artifact["historical_v2_pace_diagnostic_only"]
    print(
        "successor_design_ready="
        f"{str(artifact['successor_design_ready']).lower()} "
        f"historical_L90={pace['L90_rulings_per_24_elapsed_hours']:.6f} "
        f"paid_preflight_ready={str(artifact['paid_preflight_ready']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
