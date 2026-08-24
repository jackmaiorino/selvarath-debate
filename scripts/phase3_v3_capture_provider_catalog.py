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


def serialize_catalog(models: Iterable[Any]) -> list[dict[str, Any]]:
    """Convert Together SDK model objects into their complete JSON representation."""
    serialized: list[dict[str, Any]] = []
    for index, model in enumerate(models):
        to_dict = getattr(model, "to_dict", None)
        if not callable(to_dict):
            raise CatalogCaptureError(
                f"catalog entry {index} does not expose Together's to_dict method")
        value = to_dict(
            mode="json",
            use_api_names=True,
            exclude_unset=False,
            exclude_none=False,
        )
        if not isinstance(value, dict):
            raise CatalogCaptureError(f"catalog entry {index} did not serialize to an object")
        serialized.append(value)
    if not serialized:
        raise CatalogCaptureError("Together returned an empty model catalog")
    return serialized


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
    args = parser.parse_args(argv)

    if not os.environ.get("TOGETHER_API_KEY"):
        raise CatalogCaptureError("TOGETHER_API_KEY is not set")

    from together import Together

    retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    catalog = capture_catalog(Together())
    output = args.out.resolve()
    raw_sha256 = write_catalog(output, catalog)
    print(json.dumps({
        "path": output.as_posix(),
        "retrieved_at_utc": retrieved_at,
        "model_count": len(catalog),
        "raw_sha256": raw_sha256,
        "inference_calls": 0,
        "execution_authorized": False,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
