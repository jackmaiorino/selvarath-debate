"""Offline materialization of a roster-exact Phase 3 v3 price snapshot."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase3_plan, phase3_v3_inputs
from rejudge.phase2_execution import canonical_sha256


class PriceMaterializationError(ValueError):
    """Raised when a saved provider catalog cannot support a v3 price snapshot."""


def _portable_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _verified_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (TypeError, ValueError) as exc:
        raise PriceMaterializationError(
            "verified_at_utc must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PriceMaterializationError("verified_at_utc must have a UTC offset")
    return parsed.astimezone(timezone.utc)


def _numeric_price(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) <= 0):
        raise PriceMaterializationError(f"{label} must be a finite positive number")
    return float(value)


def build_price_snapshot(
    *,
    protocol: Mapping[str, Any],
    raw_catalog: Any,
    raw_catalog_path: str | Path,
    raw_serverless_endpoints: Any | None = None,
    raw_serverless_endpoints_path: str | Path | None = None,
    verified_at_utc: str,
    project_root: str | Path,
) -> dict[str, Any]:
    """Build and validate a snapshot from an already-saved Together model catalog."""
    phase3_plan.validate_protocol(protocol)
    verified_at = _verified_at(verified_at_utc)
    root = Path(project_root)
    catalog_path = Path(raw_catalog_path)
    if not catalog_path.is_absolute():
        catalog_path = root / catalog_path
    if not catalog_path.is_file():
        raise PriceMaterializationError(f"raw catalog file does not exist: {catalog_path}")
    try:
        catalog_on_disk = phase3_v3_inputs._load_json(catalog_path)
    except phase3_v3_inputs.InputGateError as exc:
        raise PriceMaterializationError(str(exc)) from exc
    if canonical_sha256(catalog_on_disk) != canonical_sha256(raw_catalog):
        raise PriceMaterializationError("raw catalog argument differs from the bound file")

    if ((raw_serverless_endpoints is None)
            != (raw_serverless_endpoints_path is None)):
        raise PriceMaterializationError(
            "serverless endpoint inventory and path must be supplied together")
    endpoint_path: Path | None = None
    endpoint_entries: list[Mapping[str, Any]] | None = None
    if raw_serverless_endpoints is not None:
        endpoint_path = Path(raw_serverless_endpoints_path)  # type: ignore[arg-type]
        if not endpoint_path.is_absolute():
            endpoint_path = root / endpoint_path
        if not endpoint_path.is_file():
            raise PriceMaterializationError(
                f"raw serverless endpoint file does not exist: {endpoint_path}")
        try:
            endpoints_on_disk = phase3_v3_inputs._load_json(endpoint_path)
            endpoint_entries = phase3_v3_inputs._catalog_entries(
                raw_serverless_endpoints)
        except phase3_v3_inputs.InputGateError as exc:
            raise PriceMaterializationError(str(exc)) from exc
        if canonical_sha256(endpoints_on_disk) != canonical_sha256(
                raw_serverless_endpoints):
            raise PriceMaterializationError(
                "raw serverless endpoint argument differs from the bound file")

    try:
        entries = phase3_v3_inputs._catalog_entries(raw_catalog)
    except phase3_v3_inputs.InputGateError as exc:
        raise PriceMaterializationError(str(exc)) from exc
    catalog_by_id: dict[str, Mapping[str, Any]] = {}
    for index, entry in enumerate(entries):
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            raise PriceMaterializationError(f"raw catalog entry {index} has no model id")
        if model_id in catalog_by_id:
            raise PriceMaterializationError(f"raw catalog has duplicate model id {model_id}")
        catalog_by_id[model_id] = entry

    models: dict[str, Any] = {}
    for model in phase3_v3_inputs.billed_model_registry(protocol):
        entry = catalog_by_id.get(model)
        if entry is None:
            raise PriceMaterializationError(f"required model is absent from raw catalog: {model}")
        if entry.get("type") != "chat":
            raise PriceMaterializationError(f"required model is not a chat model: {model}")
        pricing = entry.get("pricing")
        if not isinstance(pricing, Mapping):
            raise PriceMaterializationError(f"catalog pricing is absent for {model}")
        model_snapshot = {
            "serverless_available": True,
            "input_usd_per_million": _numeric_price(
                pricing.get("input"), f"{model} input price"),
            "output_usd_per_million": _numeric_price(
                pricing.get("output"), f"{model} output price"),
            "catalog_entry_sha256": canonical_sha256(entry),
        }
        if endpoint_entries is not None:
            matches = [
                endpoint for endpoint in endpoint_entries
                if endpoint.get("model") == model and endpoint.get("name") == model
            ]
            if len(matches) != 1:
                raise PriceMaterializationError(
                    f"required model must have exactly one exact serverless endpoint: {model}")
            endpoint = matches[0]
            if endpoint.get("type") != "serverless":
                raise PriceMaterializationError(
                    f"required model endpoint is not serverless: {model}")
            if endpoint.get("state") != "STARTED":
                raise PriceMaterializationError(
                    f"required model serverless endpoint is not STARTED: {model}")
            model_snapshot["serverless_endpoint_entry_sha256"] = canonical_sha256(endpoint)
        models[model] = model_snapshot

    snapshot = {
        "schema_version": (
            phase3_v3_inputs.PRICE_SCHEMA_VERSION_V2
            if endpoint_entries is not None else phase3_v3_inputs.PRICE_SCHEMA_VERSION),
        "provider": "Together",
        "verified_at_utc": verified_at.isoformat().replace("+00:00", "Z"),
        "execution_authorized": False,
        "raw_catalog": {
            "path": _portable_path(catalog_path, root),
            "canonical_sha256": canonical_sha256(raw_catalog),
            "model_count": len(entries),
        },
        "models": models,
    }
    if endpoint_entries is not None:
        assert endpoint_path is not None
        snapshot["raw_serverless_endpoints"] = {
            "path": _portable_path(endpoint_path, root),
            "canonical_sha256": canonical_sha256(raw_serverless_endpoints),
            "endpoint_count": len(endpoint_entries),
        }
    try:
        phase3_v3_inputs.validate_price_snapshot(
            snapshot,
            protocol=protocol,
            as_of=verified_at,
            project_root=root,
        )
    except phase3_v3_inputs.InputGateError as exc:
        raise PriceMaterializationError(str(exc)) from exc
    return snapshot
