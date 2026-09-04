"""Validate the non-execution Phase 3 main stage-cap ratification.

The owner record amends only the cumulative USD cap applied to the otherwise unchanged r6
protocol. It is an input to forecasts and manifests, never execution authority.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from rejudge.phase2_execution import canonical_sha256


SCHEMA_VERSION = "phase3_main_console_billing_and_stage_cap_ratification_v1"
RATIFICATION_PATH = Path(__file__).with_name(
    "phase3_main_console_billing_and_stage_cap_ratification_2026-09-04.json"
)
POLICY_PATH = "rejudge/phase3_main_console_billing_policy_proposal_2026-09-04.json"
POLICY_RAW_SHA256 = "5c2a4997cf9ee64d457e855bcb869e2b53ae47e9ce146ff9f9c2330499da73a3"
POLICY_CANONICAL_SHA256 = (
    "51e1bdaa687dbc8969b00819e89ad4475b025be8275b328f841cbcfe19f86c71"
)
CAP_BRIEF_PATH = "reports/2026-09-03-phase3-main-stage-cap-decision-brief.md"
CAP_BRIEF_RAW_SHA256 = "17ecafec8a9a91eb18ae40c74a59f7abf34e9a6db17a194508b43be79531c88c"
PROTOCOL_PATH = "rejudge/phase3_protocol_v3_r6.json"
PROTOCOL_RAW_SHA256 = "3571b195b6ac806bbdeb3a629878bb2c01cc7cd6f6979baefb2c5276c7a8f484"
PROTOCOL_CANONICAL_SHA256 = (
    "e366147d54f65d9c033f94fc7f8dfd2803c3d147a326212a8dd36c5f84b7ef85"
)
STAGE_CAP_USD = Decimal("1100.00")
PREDECESSOR_UPPER_BOUND_USD = Decimal("119.27238490")
PROJECTED_MAIN_USD = Decimal("908.82")
PROJECTED_STAGE_TOTAL_USD = Decimal("1028.09238490")
HEADROOM_USD = Decimal("71.90761510")


class StageCapRatificationError(ValueError):
    """The owner-ratified stage-cap record drifted or granted authority."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StageCapRatificationError(f"{label} must be an object")
    return value


def _decimal(value: Any, label: str) -> Decimal:
    if not isinstance(value, str) or value.strip() != value:
        raise StageCapRatificationError(f"{label} must be an exact decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise StageCapRatificationError(f"{label} must be an exact decimal string") from exc
    if not result.is_finite() or result < 0:
        raise StageCapRatificationError(f"{label} must be finite and non-negative")
    return result


def validate_stage_cap_ratification(
    record: Mapping[str, Any], *, protocol_canonical_sha256: str = PROTOCOL_CANONICAL_SHA256,
) -> dict[str, Any]:
    """Validate the exact decision semantics without reading or writing external state."""
    if record.get("schema_version") != SCHEMA_VERSION:
        raise StageCapRatificationError("unsupported stage-cap ratification schema")
    if record.get("approved_by") != "Jack Maiorino":
        raise StageCapRatificationError("stage-cap ratification approver drifted")
    exact_text = record.get("exact_text")
    if not isinstance(exact_text, str) or "It grants no reviewer dispatch" not in exact_text:
        raise StageCapRatificationError("stage-cap ratification text is absent or authorizing")

    policy = _mapping(record.get("console_billing_policy"), "console_billing_policy")
    if dict(policy) != {
        "path": POLICY_PATH,
        "raw_sha256": POLICY_RAW_SHA256,
        "canonical_sha256": POLICY_CANONICAL_SHA256,
        "disposition": "ratified_unchanged",
    }:
        raise StageCapRatificationError("console billing policy binding drifted")

    billing = _mapping(record.get("billing_decision"), "billing_decision")
    if (
        billing.get("numeric_settlement_usd") != "94.07"
        or billing.get("selected_ledger_count") != 18
        or billing.get("unresolved_reservation_count") != 827
        or billing.get("disposition") != "closed_provider_final_below_local_actual"
        or _decimal(
            billing.get("predecessor_spend_upper_bound_usd"),
            "billing_decision.predecessor_spend_upper_bound_usd",
        ) != PREDECESSOR_UPPER_BOUND_USD
    ):
        raise StageCapRatificationError("console billing decision drifted")

    decision = _mapping(record.get("stage_cap_decision"), "stage_cap_decision")
    if (
        decision.get("decision_brief_path") != CAP_BRIEF_PATH
        or decision.get("decision_brief_raw_sha256") != CAP_BRIEF_RAW_SHA256
        or _decimal(decision.get("stage_cap_usd"), "stage_cap_decision.stage_cap_usd")
        != STAGE_CAP_USD
    ):
        raise StageCapRatificationError("stage-cap decision binding drifted")
    amended = _mapping(decision.get("amends_protocol"), "stage_cap_decision.amends_protocol")
    if (
        amended.get("path") != PROTOCOL_PATH
        or amended.get("raw_sha256") != PROTOCOL_RAW_SHA256
        or amended.get("canonical_sha256") != protocol_canonical_sha256
        or amended.get("field") != "decisions.spend.stage_cap_usd"
        or amended.get("previous_value_usd") != "450.00"
    ):
        raise StageCapRatificationError("amended protocol binding drifted")
    forecast = _mapping(decision.get("forecast_basis"), "stage_cap_decision.forecast_basis")
    amounts = {
        "predecessor_spend_upper_bound_usd": PREDECESSOR_UPPER_BOUND_USD,
        "projected_main_spend_usd": PROJECTED_MAIN_USD,
        "projected_stage_total_usd": PROJECTED_STAGE_TOTAL_USD,
        "headroom_usd": HEADROOM_USD,
    }
    for field, expected in amounts.items():
        if _decimal(forecast.get(field), f"stage_cap_decision.forecast_basis.{field}") != expected:
            raise StageCapRatificationError(f"forecast basis {field} drifted")
    if PREDECESSOR_UPPER_BOUND_USD + PROJECTED_MAIN_USD != PROJECTED_STAGE_TOTAL_USD:
        raise StageCapRatificationError("ratified forecast arithmetic is inconsistent")
    if STAGE_CAP_USD - PROJECTED_STAGE_TOTAL_USD != HEADROOM_USD:
        raise StageCapRatificationError("ratified headroom arithmetic is inconsistent")

    authority = _mapping(record.get("authority"), "authority")
    offline_fields = {
        "offline_policy_materialization_authorized",
        "offline_protocol_materialization_authorized",
        "offline_reconciliation_materialization_authorized",
        "offline_forecast_materialization_authorized",
        "offline_manifest_materialization_authorized",
    }
    forbidden_fields = {
        "external_reviewer_dispatch_authorized",
        "provider_calls_authorized",
        "together_calls_authorized",
        "main_run_authorized",
        "spend_authorized",
    }
    if set(authority) != offline_fields | forbidden_fields:
        raise StageCapRatificationError("stage-cap authority fields drifted")
    if any(authority.get(field) is not True for field in offline_fields):
        raise StageCapRatificationError("offline materialization authority is incomplete")
    if any(authority.get(field) is not False for field in forbidden_fields):
        raise StageCapRatificationError("stage-cap ratification grants execution authority")
    return {
        "ratification_id": record.get("ratification_id"),
        "canonical_sha256": canonical_sha256(dict(record)),
        "stage_cap_usd": STAGE_CAP_USD,
        "predecessor_spend_upper_bound_usd": PREDECESSOR_UPPER_BOUND_USD,
        "projected_main_usd": PROJECTED_MAIN_USD,
        "projected_stage_total_usd": PROJECTED_STAGE_TOTAL_USD,
        "headroom_usd": HEADROOM_USD,
        "execution_authorized": False,
    }


__all__ = [
    "RATIFICATION_PATH",
    "SCHEMA_VERSION",
    "STAGE_CAP_USD",
    "StageCapRatificationError",
    "validate_stage_cap_ratification",
]
