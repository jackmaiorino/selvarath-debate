"""Strict per-model pricing and durable per-run spend-ledger helpers."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

from rejudge.api_client import (
    _LIVE_ACCOUNTING_FACTORY_TOKEN,
    RejudgeClient,
    UsageLedgerError,
    load_chained_usage_ledger,
    prepare_usage_ledger as _prepare_usage_ledger,
    usage_ledger_state_path,
)


DEFAULT_PRICE_SCHEDULE = Path(__file__).with_name("output") / "calibration_models.json"


class PriceScheduleError(ValueError):
    """A frozen price schedule is absent, malformed, or incomplete."""


def load_price_schedule(path: str | Path = DEFAULT_PRICE_SCHEDULE) -> dict:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PriceScheduleError(f"could not read frozen price schedule {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("prices_per_mtok"), dict):
        raise PriceScheduleError(f"{path} has no prices_per_mtok object")
    for field in ("provider", "price_verified_at", "price_source_url"):
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise PriceScheduleError(f"{path} is missing non-empty {field}")
    return payload


def select_model_prices(schedule: dict, model_ids: Iterable[str]) -> dict[str, dict[str, float]]:
    registry = schedule["prices_per_mtok"]
    selected: dict[str, dict[str, float]] = {}
    for model in sorted(set(model_ids)):
        entry = registry.get(model)
        if not isinstance(entry, dict) or set(entry) != {"in", "out"}:
            raise PriceScheduleError(f"no frozen input/output price for model {model!r}")
        try:
            input_price = float(entry["in"])
            output_price = float(entry["out"])
        except (TypeError, ValueError) as exc:
            raise PriceScheduleError(f"invalid frozen price for model {model!r}") from exc
        if any(not math.isfinite(price) or price < 0
               for price in (input_price, output_price)):
            raise PriceScheduleError(
                f"frozen prices must be finite and non-negative for model {model!r}")
        selected[model] = {"in": input_price, "out": output_price}
    return selected


def pricing_identity(schedule: dict, model_prices: dict[str, dict[str, float]]) -> dict:
    """Return the exact price provenance embedded in a run manifest."""
    return {
        "mode": "strict_per_model",
        "provider": schedule["provider"],
        "verified_at": schedule["price_verified_at"],
        "source_url": schedule["price_source_url"],
        "prices_per_mtok": model_prices,
        "strict_model_pricing": True,
    }


def usage_log_path_for(output_path: str | Path) -> Path:
    output = Path(output_path)
    return output.with_name(f"{output.name}.usage.jsonl")


def usage_ledger_generated_paths(usage_log_path: str | Path) -> tuple[Path, Path, Path]:
    """Return every runtime file owned by one live usage ledger.

    The third path is the persistent OS-lock carrier used while a runner owns the
    ledger.  Manifest creation excludes these generated files from Git dirtiness,
    while the manifest still binds the ledger path and random identity explicitly.
    """
    ledger = Path(usage_log_path)
    lock = ledger.with_name(f"{ledger.name}.lock")
    return ledger, usage_ledger_state_path(ledger), lock


def prepare_usage_ledger(
    usage_log_path: str | Path, *, allow_create: bool,
) -> dict[str, object]:
    """Prepare/validate the live ledger whose identity must be bound into a manifest."""
    return _prepare_usage_ledger(usage_log_path, allow_create=allow_create)


def create_accounted_client(
    *,
    approved_cap_usd: float,
    dry_run: bool,
    model_prices: dict[str, dict[str, float]],
    usage_log_path: str | Path,
    error_log_path: str | Path,
    ledger_identity: dict[str, object] | None = None,
) -> tuple[RejudgeClient, dict[str, float | int]]:
    """Create a strict client whose cap includes every prior ledger event.

    An unmatched pre-call reservation is treated as an uncertain charge after a crash.
    Live callers must pass the prepared identity already frozen into their run manifest;
    dry runs never create or read a spend ledger.
    """
    snapshot = None
    if dry_run:
        summary: dict[str, float | int] = {
            "events": 0,
            "actual_spend_usd": 0.0,
            "uncertain_spend_usd": 0.0,
            "accounted_spend_usd": 0.0,
            "unmatched_reservations": 0,
        }
        ledger = None
    else:
        ledger = str(Path(usage_log_path))
        if ledger_identity is None:
            raise UsageLedgerError(
                "live clients require a prepared ledger identity bound into the run manifest")
        snapshot = load_chained_usage_ledger(
            ledger, expected_identity=ledger_identity)
        summary = snapshot.summary
    client = RejudgeClient(
        approved_cap_usd=approved_cap_usd,
        dry_run=dry_run,
        error_log_path=str(error_log_path),
        model_prices=model_prices,
        strict_model_pricing=True,
        initial_spend_usd=float(summary["actual_spend_usd"]),
        initial_uncertain_spend_usd=float(summary["uncertain_spend_usd"]),
        usage_log_path=ledger,
        _ledger_snapshot=(snapshot if not dry_run else None),
        _accounting_factory_token=_LIVE_ACCOUNTING_FACTORY_TOKEN,
    )
    return client, summary


__all__ = [
    "DEFAULT_PRICE_SCHEDULE",
    "PriceScheduleError",
    "create_accounted_client",
    "load_price_schedule",
    "pricing_identity",
    "prepare_usage_ledger",
    "select_model_prices",
    "usage_ledger_generated_paths",
    "usage_log_path_for",
]
