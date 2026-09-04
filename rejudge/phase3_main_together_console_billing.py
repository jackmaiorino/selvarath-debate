"""Validate Together console billing evidence for Phase 3 main.

Together support directed this account to use Cost Analytics and invoices instead of
``/v1/billing/usage``.  This module validates a hash-bound evidence bundle without
calling Together.  Cost Analytics supplies the exact UTC-window total.  The monthly
invoice supplies finality and prepaid-credit settlement evidence, but never supplies
the exact-window amount.

The validator intentionally stores only hashes of provider account identifiers.  It
parses the CSV from immutable bytes, recomputes its totals, and checks its project and
API-key hashes against the owner-ratified account components.  Invoice semantic fields
remain human-reviewed claims bound to the exact PDF bytes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "together_console_billing_evidence_v1"
CAPTURE_METHOD = "together_console_cost_analytics_and_invoice"
PROVIDER = "Together"
CURRENCY = "USD"
SETTLEMENT_STATUS = "finalized_by_cost_analytics_and_monthly_invoice"
SUPPORT_SCHEMA_VERSION = "together_billing_support_disposition_v1"
OWNER_RATIFICATION_SCHEMA_VERSION = "phase3_main_owner_ratification_v1"
SUPPORTED_ROUTE = "cost_analytics_and_invoices"
INVOICE_SCOPE_ROLE = "monthly_finality_only_not_exact_window_total"
CSV_HEADERS = (
    "Date",
    "Line Item",
    "Dimensions",
    "Quantity",
    "Unit Price (USD)",
    "Amount (USD)",
)
LINE_ITEM = "Token Based Inference (Per 1M Tokens)"
FINALITY_LIMITATIONS = (
    "Cost Analytics is the exact-window numeric source.",
    "The monthly invoice corroborates final settlement and prepaid-credit treatment only.",
    "The invoice amount due is not the exact-window usage total.",
)

RECORD_FIELDS = frozenset({
    "schema_version",
    "provider",
    "currency",
    "capture_method",
    "observed_at_utc",
    "execution_authorized",
    "provider_calls_authorized",
    "main_run_spend_authorized",
    "billing_scope",
    "account_binding",
    "support_disposition",
    "cost_analytics",
    "invoice",
    "dashboard",
    "provider_settlement",
    "limitations",
})
BILLING_SCOPE_FIELDS = frozenset(
    {"account_identity_sha256", "window_start_utc", "window_end_utc"}
)
ACCOUNT_BINDING_FIELDS = frozenset({
    "owner_ratification",
    "api_key_id_sha256",
    "project_id_sha256",
    "organization_id_sha256",
})
ARTIFACT_BINDING_FIELDS = frozenset({"path", "raw_sha256"})
SUPPORT_FIELDS = frozenset({
    "path",
    "raw_sha256",
    "endpoint_status",
    "supported_reconciliation_route",
})
COST_ANALYTICS_FIELDS = frozenset({
    "path",
    "raw_sha256",
    "ui_date_start_utc",
    "ui_date_end_inclusive_utc",
    "row_count",
    "line_item_amount_sum_usd",
    "derived_cost_sum_usd",
    "display_total_usd",
    "csv_rounding_gap_usd",
})
INVOICE_FIELDS = frozenset({
    "path",
    "raw_sha256",
    "issue_date",
    "service_period_start",
    "service_period_end_inclusive",
    "subtotal_usd",
    "total_usd",
    "applied_balance_usd",
    "amount_due_usd",
    "usage_offset_by_prepaid_commit",
    "scope_role",
})
DASHBOARD_FIELDS = frozenset(
    {"mode", "before_total_usd", "after_total_usd", "reported_delta_usd"}
)
SETTLEMENT_FIELDS = frozenset(
    {"status", "account_identity_sha256", "finalized_through_utc"}
)

_FIXED_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class TogetherConsoleBillingError(ValueError):
    """The console evidence record or a bound artifact failed validation."""


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TogetherConsoleBillingError(f"{label} must be an object")
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise TogetherConsoleBillingError(
            f"{label} fields drifted; missing={missing}, extra={extra}"
        )
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TogetherConsoleBillingError(f"{label} must be non-empty text")
    return value


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label)
    if not _SHA256.fullmatch(text):
        raise TogetherConsoleBillingError(f"{label} must be lowercase SHA-256")
    return text


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TogetherConsoleBillingError(f"{label} must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise TogetherConsoleBillingError(f"{label} must be UTC")
    return parsed.astimezone(timezone.utc)


def _day(value: Any, label: str) -> date:
    text = _text(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise TogetherConsoleBillingError(f"{label} must be YYYY-MM-DD") from exc


def _decimal(value: Any, label: str, *, allow_negative: bool = False) -> Decimal:
    if not isinstance(value, str) or not _FIXED_DECIMAL.fullmatch(value):
        raise TogetherConsoleBillingError(f"{label} must be fixed-point decimal text")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise TogetherConsoleBillingError(f"{label} is invalid") from exc
    if not amount.is_finite() or (amount < 0 and not allow_negative):
        raise TogetherConsoleBillingError(f"{label} is invalid")
    return amount


def _exact_sum(values: list[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = max(100, sum(len(value.as_tuple().digits) for value in values) + 10)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return sum(values, Decimal("0"))


def _resolve_artifact(value: Any, *, project_root: Path, label: str) -> Path:
    path = Path(_text(value, label))
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _read_bound_artifact(
    value: Mapping[str, Any], *, project_root: Path, label: str,
) -> tuple[Path, bytes]:
    binding = _exact_keys(value, ARTIFACT_BINDING_FIELDS, label)
    path = _resolve_artifact(binding["path"], project_root=project_root, label=f"{label}.path")
    expected = _sha256(binding["raw_sha256"], f"{label}.raw_sha256")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TogetherConsoleBillingError(f"could not read {label}: {path}") from exc
    if _raw_sha256(raw) != expected:
        raise TogetherConsoleBillingError(f"{label} raw SHA-256 mismatch")
    return path, raw


def _json_object(raw: bytes, label: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TogetherConsoleBillingError(f"{label} is not UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise TogetherConsoleBillingError(f"{label} must be an object")
    return payload


def _require_non_authorizing(value: Mapping[str, Any], label: str) -> None:
    if any(
        value[field] is not False
        for field in (
            "execution_authorized",
            "provider_calls_authorized",
            "main_run_spend_authorized",
        )
    ):
        raise TogetherConsoleBillingError(
            f"{label} is evidence only and cannot authorize execution or spend"
        )


def _billing_scope(value: Any) -> dict[str, Any]:
    scope = _exact_keys(value, BILLING_SCOPE_FIELDS, "billing_scope")
    account = _sha256(scope["account_identity_sha256"], "billing_scope.account_identity_sha256")
    start = _utc(scope["window_start_utc"], "billing_scope.window_start_utc")
    end = _utc(scope["window_end_utc"], "billing_scope.window_end_utc")
    if start.time() != datetime.min.time() or end.time() != datetime.min.time():
        raise TogetherConsoleBillingError("billing scope must use whole UTC days")
    if end <= start:
        raise TogetherConsoleBillingError("billing scope must be non-empty")
    return {"raw": dict(scope), "account": account, "start": start, "end": end}


def _validate_owner_ratification(
    binding_value: Any, *, project_root: Path, scope: Mapping[str, Any],
    account_binding: Mapping[str, Any],
) -> None:
    _path, raw = _read_bound_artifact(
        binding_value, project_root=project_root, label="account_binding.owner_ratification"
    )
    payload = _json_object(raw, "owner ratification")
    if payload.get("schema_version") != OWNER_RATIFICATION_SCHEMA_VERSION:
        raise TogetherConsoleBillingError("owner ratification schema drifted")
    authority = payload.get("authority_limits")
    if not isinstance(authority, Mapping) or any(authority.values()):
        raise TogetherConsoleBillingError("owner ratification must remain non-authorizing")
    ratified_scope = payload.get("together_account_scope")
    if not isinstance(ratified_scope, Mapping):
        raise TogetherConsoleBillingError("owner ratification account scope is missing")
    expected = {
        "account_identity_sha256": scope["account"],
        "api_key_id_sha256": account_binding["api_key_id_sha256"],
        "project_id_sha256": account_binding["project_id_sha256"],
        "organization_id_sha256": account_binding["organization_id_sha256"],
    }
    if any(ratified_scope.get(field) != value for field, value in expected.items()):
        raise TogetherConsoleBillingError("console account hashes differ from owner ratification")


def _validate_support_disposition(
    value: Any, *, project_root: Path,
) -> None:
    support = _exact_keys(value, SUPPORT_FIELDS, "support_disposition")
    path, raw = _read_bound_artifact(
        {"path": support["path"], "raw_sha256": support["raw_sha256"]},
        project_root=project_root,
        label="support disposition",
    )
    payload = _json_object(raw, f"support disposition {path}")
    if (
        payload.get("schema_version") != SUPPORT_SCHEMA_VERSION
        or payload.get("provider") != PROVIDER
        or payload.get("supported_reconciliation_route") != SUPPORTED_ROUTE
        or payload.get("endpoint_status") != support["endpoint_status"]
        or payload.get("supported_reconciliation_route")
        != support["supported_reconciliation_route"]
    ):
        raise TogetherConsoleBillingError("Together support disposition drifted")
    _require_non_authorizing(payload, "Together support disposition")


def _parse_money_cell(value: str, label: str) -> Decimal:
    if not isinstance(value, str) or not value.startswith("$"):
        raise TogetherConsoleBillingError(f"{label} must use a USD dollar prefix")
    return _decimal(value[1:], label)


def _parse_dimensions(value: str, label: str) -> dict[str, str]:
    if not value:
        raise TogetherConsoleBillingError(f"{label} is empty")
    result: dict[str, str] = {}
    for item in value.split(","):
        key, separator, field_value = item.partition(":")
        if not separator or not key or not field_value or key in result:
            raise TogetherConsoleBillingError(f"{label} is malformed")
        result[key] = field_value
    required = {"endpoint_name", "is_lora", "is_reserved_ptu", "project_id"}
    if not required <= set(result) or set(result) - required - {"api_key_id"}:
        raise TogetherConsoleBillingError(f"{label} fields drifted")
    if result["is_lora"] != "false" or result["is_reserved_ptu"] != "false":
        raise TogetherConsoleBillingError(f"{label} includes unsupported usage")
    return result


def _validate_cost_analytics(
    value: Any, *, project_root: Path, scope: Mapping[str, Any],
    account_binding: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _exact_keys(value, COST_ANALYTICS_FIELDS, "cost_analytics")
    path, raw = _read_bound_artifact(
        {"path": evidence["path"], "raw_sha256": evidence["raw_sha256"]},
        project_root=project_root,
        label="Cost Analytics CSV",
    )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TogetherConsoleBillingError("Cost Analytics CSV is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != CSV_HEADERS:
        raise TogetherConsoleBillingError("Cost Analytics CSV headers drifted")
    rows = list(reader)
    claimed_count = evidence["row_count"]
    if isinstance(claimed_count, bool) or not isinstance(claimed_count, int) or claimed_count <= 0:
        raise TogetherConsoleBillingError("cost_analytics.row_count must be positive")
    if len(rows) != claimed_count:
        raise TogetherConsoleBillingError("Cost Analytics row count drifted")

    dates: set[date] = set()
    amount_values: list[Decimal] = []
    derived_values: list[Decimal] = []
    observed_api_hashes: set[str] = set()
    observed_project_hashes: set[str] = set()
    for index, row in enumerate(rows, 1):
        label = f"Cost Analytics row {index}"
        if set(row) != set(CSV_HEADERS) or row["Line Item"] != LINE_ITEM:
            raise TogetherConsoleBillingError(f"{label} fields or line item drifted")
        try:
            row_date = datetime.strptime(row["Date"], "%b %d %Y").date()
        except ValueError as exc:
            raise TogetherConsoleBillingError(f"{label}.Date is invalid") from exc
        dates.add(row_date)
        dimensions = _parse_dimensions(row["Dimensions"], f"{label}.Dimensions")
        observed_project_hashes.add(_hash_text(dimensions["project_id"]))
        if "api_key_id" in dimensions:
            observed_api_hashes.add(_hash_text(dimensions["api_key_id"]))
        quantity = _decimal(row["Quantity"], f"{label}.Quantity")
        unit_price = _parse_money_cell(row["Unit Price (USD)"], f"{label}.Unit Price")
        amount = _parse_money_cell(row["Amount (USD)"], f"{label}.Amount")
        amount_values.append(amount)
        derived_values.append(quantity * unit_price)

    start_day = scope["start"].date()
    end_day = scope["end"].date()
    expected_dates = {
        start_day + timedelta(days=offset)
        for offset in range((end_day - start_day).days)
    }
    if dates != expected_dates:
        raise TogetherConsoleBillingError("Cost Analytics dates do not cover the billing scope")
    if (
        _day(evidence["ui_date_start_utc"], "cost_analytics.ui_date_start_utc")
        != start_day
        or _day(
            evidence["ui_date_end_inclusive_utc"],
            "cost_analytics.ui_date_end_inclusive_utc",
        )
        != end_day - timedelta(days=1)
    ):
        raise TogetherConsoleBillingError(
            "Cost Analytics UI date claims differ from billing scope")
    if observed_project_hashes != {account_binding["project_id_sha256"]}:
        raise TogetherConsoleBillingError(
            "Cost Analytics project hash differs from ratified account")
    if observed_api_hashes != {account_binding["api_key_id_sha256"]}:
        raise TogetherConsoleBillingError(
            "Cost Analytics API-key hash differs from ratified account")

    amount_sum = _exact_sum(amount_values)
    derived_sum = _exact_sum(derived_values)
    display_total = derived_sum.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rounding_gap = display_total - amount_sum
    claims = {
        "line_item_amount_sum_usd": amount_sum,
        "derived_cost_sum_usd": derived_sum,
        "display_total_usd": display_total,
        "csv_rounding_gap_usd": rounding_gap,
    }
    for field, expected in claims.items():
        if _decimal(evidence[field], f"cost_analytics.{field}") != expected:
            raise TogetherConsoleBillingError(f"cost_analytics.{field} drifted")
    return {"path": path, "display_total": display_total}


def _validate_invoice(
    value: Any, *, project_root: Path, scope: Mapping[str, Any],
) -> dict[str, Any]:
    invoice = _exact_keys(value, INVOICE_FIELDS, "invoice")
    path, _raw = _read_bound_artifact(
        {"path": invoice["path"], "raw_sha256": invoice["raw_sha256"]},
        project_root=project_root,
        label="Together invoice PDF",
    )
    issue_date = _day(invoice["issue_date"], "invoice.issue_date")
    service_start = _day(invoice["service_period_start"], "invoice.service_period_start")
    service_end = _day(
        invoice["service_period_end_inclusive"], "invoice.service_period_end_inclusive"
    )
    if (
        service_start > scope["start"].date()
        or service_end < scope["end"].date() - timedelta(days=1)
    ):
        raise TogetherConsoleBillingError("invoice service period does not contain billing scope")
    if issue_date <= service_end:
        raise TogetherConsoleBillingError("invoice was not issued after its service period")
    subtotal = _decimal(invoice["subtotal_usd"], "invoice.subtotal_usd", allow_negative=True)
    total = _decimal(invoice["total_usd"], "invoice.total_usd", allow_negative=True)
    applied = _decimal(invoice["applied_balance_usd"], "invoice.applied_balance_usd")
    due = _decimal(invoice["amount_due_usd"], "invoice.amount_due_usd")
    if total != subtotal or total + applied != due:
        raise TogetherConsoleBillingError("invoice settlement arithmetic drifted")
    if due != 0 or invoice["usage_offset_by_prepaid_commit"] is not True:
        raise TogetherConsoleBillingError("invoice does not establish prepaid settlement")
    if invoice["scope_role"] != INVOICE_SCOPE_ROLE:
        raise TogetherConsoleBillingError("invoice exact-window role drifted")
    return {"path": path, "issue_date": issue_date}


def validate_capture(
    record: Mapping[str, Any], *, project_root: str | Path = ".",
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Validate one offline Together console evidence bundle without mutation."""
    root = Path(project_root).resolve()
    _exact_keys(record, RECORD_FIELDS, "record")
    if (
        record["schema_version"] != SCHEMA_VERSION
        or record["provider"] != PROVIDER
        or record["currency"] != CURRENCY
        or record["capture_method"] != CAPTURE_METHOD
    ):
        raise TogetherConsoleBillingError("console evidence identity drifted")
    _require_non_authorizing(record, "Together console evidence")
    observed_at = _utc(record["observed_at_utc"], "observed_at_utc")
    scope = _billing_scope(record["billing_scope"])
    if observed_at < scope["end"]:
        raise TogetherConsoleBillingError("console evidence predates the billing window end")

    account_binding = _exact_keys(
        record["account_binding"], ACCOUNT_BINDING_FIELDS, "account_binding"
    )
    for field in ("api_key_id_sha256", "project_id_sha256", "organization_id_sha256"):
        _sha256(account_binding[field], f"account_binding.{field}")
    _validate_owner_ratification(
        account_binding["owner_ratification"],
        project_root=root,
        scope=scope,
        account_binding=account_binding,
    )
    _validate_support_disposition(record["support_disposition"], project_root=root)
    analytics = _validate_cost_analytics(
        record["cost_analytics"],
        project_root=root,
        scope=scope,
        account_binding=account_binding,
    )
    invoice = _validate_invoice(record["invoice"], project_root=root, scope=scope)

    dashboard = _exact_keys(record["dashboard"], DASHBOARD_FIELDS, "dashboard")
    expected_total_text = record["cost_analytics"]["display_total_usd"]
    if dashboard != {
        "mode": "direct_delta",
        "before_total_usd": None,
        "after_total_usd": None,
        "reported_delta_usd": expected_total_text,
    }:
        raise TogetherConsoleBillingError("dashboard claims differ from Cost Analytics")

    settlement = _exact_keys(
        record["provider_settlement"], SETTLEMENT_FIELDS, "provider_settlement"
    )
    if (
        settlement["status"] != SETTLEMENT_STATUS
        or settlement["account_identity_sha256"] != scope["account"]
        or _utc(
            settlement["finalized_through_utc"],
            "provider_settlement.finalized_through_utc",
        )
        != scope["end"]
        or invoice["issue_date"] <= scope["end"].date()
    ):
        raise TogetherConsoleBillingError("console settlement claims drifted")
    if tuple(record["limitations"]) != FINALITY_LIMITATIONS:
        raise TogetherConsoleBillingError("console evidence limitations drifted")

    if as_of is not None:
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise TogetherConsoleBillingError("as_of must be timezone-aware UTC")
        current = as_of.astimezone(timezone.utc)
        if observed_at > current:
            raise TogetherConsoleBillingError("console evidence lies in the future")
        # A completed monthly invoice is immutable historical settlement evidence.
        # It does not expire merely because launch occurs more than one hour later.

    return {
        "observed_at_utc": record["observed_at_utc"],
        "billing_observed_at_utc": record["observed_at_utc"],
        "billing_scope": dict(record["billing_scope"]),
        "dashboard": dict(dashboard),
        "provider_delta_usd": expected_total_text,
        "provider_settlement": dict(settlement),
        "cost_analytics_path": analytics["path"],
        "invoice_path": invoice["path"],
    }
