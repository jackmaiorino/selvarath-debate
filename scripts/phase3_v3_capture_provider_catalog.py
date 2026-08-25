"""Capture the authenticated Together model catalog without making inference calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class CatalogCaptureError(RuntimeError):
    """Raised when a provider catalog cannot be captured safely."""


def _serialize_sdk_objects(
    values: Iterable[Any], *, label: str,
) -> list[dict[str, Any]]:
    """Convert Together SDK objects into their complete JSON representation."""
    serialized: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        to_dict = getattr(value, "to_dict", None)
        if not callable(to_dict):
            raise CatalogCaptureError(
                f"{label} entry {index} does not expose Together's to_dict method")
        payload = to_dict(
            mode="json",
            use_api_names=True,
            exclude_unset=False,
            exclude_none=False,
        )
        if not isinstance(payload, dict):
            raise CatalogCaptureError(
                f"{label} entry {index} did not serialize to an object")
        serialized.append(payload)
    if not serialized:
        raise CatalogCaptureError(f"Together returned an empty {label}")
    return serialized


def serialize_catalog(models: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert Together SDK model objects into their complete JSON representation."""
    return _serialize_sdk_objects(models, label="model catalog")


def serialize_serverless_endpoints(endpoints: Iterable[Any]) -> list[dict[str, Any]]:
    """Serialize the live serverless endpoint inventory used for dispatchability."""
    return _serialize_sdk_objects(endpoints, label="serverless endpoint inventory")


def compact_json_bytes(payload: Any) -> bytes:
    return (json.dumps(
        payload,
        sort_keys=False,
        separators=(",", ":"),
        ensure_ascii=True,
    ) + "\n").encode("utf-8")


def capture_catalog(client: Any) -> list[dict[str, Any]]:
    """Use only Together's model-list endpoint, never a completion endpoint."""
    return serialize_catalog(client.models.list())


def capture_serverless_endpoints(client: Any) -> list[dict[str, Any]]:
    """Use Together's read-only endpoint list, filtered to the shared serverless fleet."""
    response = client.endpoints.list(type="serverless")
    values = getattr(response, "data", response)
    return serialize_serverless_endpoints(values)


def write_catalog(path: Path, payload: list[dict[str, Any]]) -> str:
    """Create a catalog artifact and return its raw-byte SHA-256."""
    raw = compact_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError as exc:
        raise CatalogCaptureError(f"refusing to overwrite existing catalog: {path}") from exc
    return hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--serverless-endpoints-out", type=Path)
    args = parser.parse_args(argv)

    if not os.environ.get("TOGETHER_API_KEY"):
        raise CatalogCaptureError("TOGETHER_API_KEY is not set")

    from together import Together

    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    client = Together()
    catalog = capture_catalog(client)
    output = args.out.resolve()
    raw_sha256 = write_catalog(output, catalog)
    report = {
        "path": output.as_posix(),
        "retrieved_at_utc": retrieved_at,
        "model_count": len(catalog),
        "raw_sha256": raw_sha256,
        "inference_calls": 0,
        "execution_authorized": False,
    }
    if args.serverless_endpoints_out is not None:
        endpoints = capture_serverless_endpoints(client)
        endpoints_output = args.serverless_endpoints_out.resolve()
        report.update({
            "serverless_endpoints_path": endpoints_output.as_posix(),
            "serverless_endpoints_count": len(endpoints),
            "serverless_endpoints_raw_sha256": write_catalog(
                endpoints_output, endpoints),
        })
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
