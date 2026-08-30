"""Exact offline validation of the Phase 3 main context blocklist."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rejudge import phase3_main_runner
from scripts import phase3_context_precheck


class MainContextBlocklistError(ValueError):
    """The bound blocklist is not the deterministic report for its frozen inputs."""


def validate_main_context_blocklist(
    value: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any],
    inventory: phase3_main_runner.MainInventory,
    prompt_bundle: Mapping[str, Any],
    role_limits: Mapping[str, Any],
    transcript_bundle: Mapping[str, Any],
) -> tuple[str, ...]:
    """Rebuild the report and return its exact sorted ineligible cell keys."""
    if not isinstance(value, Mapping):
        raise MainContextBlocklistError("context blocklist must be an object")
    generated_at = value.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        raise MainContextBlocklistError(
            "context blocklist must retain its generated_at input")
    try:
        transcript_index = phase3_context_precheck.load_transcript_index(
            transcript_bundle)
        expected = phase3_context_precheck.build_report(
            protocol=protocol,
            bundle=prompt_bundle,
            role_limits=role_limits,
            plan_cells=inventory.cells,
            transcript_index=transcript_index,
            scope="main",
            generated_at=generated_at,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MainContextBlocklistError(
            f"context blocklist could not be recomputed: {exc}") from exc
    if dict(value) != expected:
        raise MainContextBlocklistError(
            "context blocklist differs from the deterministic frozen-input recomputation")
    return tuple(str(row["cell_key"]) for row in expected["excluded"])


__all__ = ["MainContextBlocklistError", "validate_main_context_blocklist"]
