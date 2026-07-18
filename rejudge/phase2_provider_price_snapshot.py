"""Validate the public Together price/catalog snapshot used for Phase 2 materialization.

This artifact is public-catalog evidence only.  It deliberately cannot establish account
access, reconcile billing, or authorize a provider call.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any

from rejudge import phase2_plan


DEFAULT_SNAPSHOT_PATH = Path(__file__).with_name(
    "phase2_provider_price_snapshot_2026-07-18.json")
DEFAULT_PROTOCOL_PATH = phase2_plan.DEFAULT_PROTOCOL_PATH
SCHEMA_VERSION = "phase2_provider_price_snapshot_v1"
STATUS = "public_catalog_verified_pending_account_reconciliation"
SOURCE_URL = "https://docs.together.ai/docs/serverless/models"
DOES_NOT_ESTABLISH = (
    "account-specific access or capacity",
    "successful completion behavior",
    "provider backend stability",
    "account usage or credit reconciliation",
    "authorization to make a provider call",
)


class ProviderSnapshotError(ValueError):
    """The public provider snapshot is malformed or disagrees with the frozen design."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderSnapshotError(f"{label} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ProviderSnapshotError(f"{label} fields drifted")


def _load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderSnapshotError(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProviderSnapshotError(f"{path} must contain a JSON object")
    return payload


def validate_snapshot(
    snapshot: Mapping[str, Any], protocol: Mapping[str, Any],
) -> None:
    """Validate exact catalog scope, model IDs, and prices against the frozen protocol."""
    _exact_keys(
        snapshot,
        {
            "schema_version", "status", "provider", "verified_at_utc", "source",
            "scope", "models", "comparison_to_frozen_design",
        },
        "snapshot",
    )
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ProviderSnapshotError("unsupported provider snapshot schema")
    if snapshot.get("status") != STATUS:
        raise ProviderSnapshotError("provider snapshot status drifted")
    if snapshot.get("provider") != "Together AI":
        raise ProviderSnapshotError("provider must be Together AI")

    timestamp = snapshot.get("verified_at_utc")
    if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
        raise ProviderSnapshotError("verified_at_utc must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ProviderSnapshotError("verified_at_utc is invalid") from exc
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ProviderSnapshotError("verified_at_utc must be UTC")

    source = _mapping(snapshot.get("source"), "source")
    _exact_keys(source, {"catalog_section", "url"}, "source")
    if (source.get("catalog_section") != "Serverless models / Chat models"
            or source.get("url") != SOURCE_URL):
        raise ProviderSnapshotError("public catalog source drifted")

    scope = _mapping(snapshot.get("scope"), "scope")
    _exact_keys(scope, {"claim", "does_not_establish"}, "scope")
    claim = scope.get("claim")
    if not isinstance(claim, str) or "matched rejudge/phase2_protocol.json" not in claim:
        raise ProviderSnapshotError("snapshot claim does not bind the frozen protocol")
    limitations = scope.get("does_not_establish")
    if not isinstance(limitations, list) or tuple(limitations) != DOES_NOT_ESTABLISH:
        raise ProviderSnapshotError("public-catalog limitations drifted")

    registry = _mapping(protocol.get("model_registry"), "protocol model_registry")
    models = _mapping(snapshot.get("models"), "models")
    if set(models) != set(registry):
        raise ProviderSnapshotError("snapshot model roster disagrees with the frozen protocol")
    for model_id, raw_entry in models.items():
        entry = _mapping(raw_entry, f"models.{model_id}")
        _exact_keys(
            entry,
            {
                "context_length_tokens", "input_usd_per_million_tokens",
                "output_usd_per_million_tokens",
            },
            f"models.{model_id}",
        )
        context = entry.get("context_length_tokens")
        if not isinstance(context, int) or isinstance(context, bool) or context <= 0:
            raise ProviderSnapshotError(f"invalid context length for {model_id}")
        frozen_prices = _mapping(
            _mapping(registry[model_id], f"protocol model {model_id}").get(
                "price_usd_per_million_tokens"),
            f"protocol prices {model_id}",
        )
        for snapshot_field, protocol_field in (
            ("input_usd_per_million_tokens", "input"),
            ("output_usd_per_million_tokens", "output"),
        ):
            price = entry.get(snapshot_field)
            if (not isinstance(price, (int, float)) or isinstance(price, bool)
                    or not math.isfinite(float(price)) or float(price) < 0):
                raise ProviderSnapshotError(f"invalid {snapshot_field} for {model_id}")
            frozen_price = frozen_prices.get(protocol_field)
            if (not isinstance(frozen_price, (int, float)) or isinstance(frozen_price, bool)
                    or not math.isfinite(float(frozen_price))):
                raise ProviderSnapshotError(f"frozen protocol price missing for {model_id}")
            if float(price) != float(frozen_price):
                raise ProviderSnapshotError(f"public price mismatch for {model_id}")

    comparison = _mapping(
        snapshot.get("comparison_to_frozen_design"), "comparison_to_frozen_design")
    _exact_keys(
        comparison,
        {
            "base_protocol_path", "all_five_model_ids_listed",
            "all_standard_input_output_prices_match",
        },
        "comparison_to_frozen_design",
    )
    if comparison.get("base_protocol_path") != "rejudge/phase2_protocol.json":
        raise ProviderSnapshotError("base protocol path drifted")
    if (comparison.get("all_five_model_ids_listed") is not True
            or comparison.get("all_standard_input_output_prices_match") is not True):
        raise ProviderSnapshotError("catalog comparison is not complete")
    if len(models) != 5:
        raise ProviderSnapshotError("frozen roster must contain exactly five unique models")


def load_and_validate(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = phase2_plan.load_protocol(protocol_path)
    snapshot = _load_json(snapshot_path)
    validate_snapshot(snapshot, protocol)
    return snapshot, protocol


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT_PATH))
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL_PATH))
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("only --check is supported")
    snapshot, _protocol = load_and_validate(args.snapshot, args.protocol)
    print(
        "verified public Together catalog snapshot; "
        f"models={len(snapshot['models'])}; "
        f"canonical_sha256={phase2_plan.canonical_sha256(snapshot)}; "
        "account_reconciled=NO; execution_authorized=NO"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
