"""Capture and validate authenticated Together billing evidence for Phase 3 main.

This module is deliberately separate from the live runner. It performs only the fixed
read-only ``GET /v1/whoami`` and ``GET /v1/billing/usage`` requests. It never constructs an
inference client, retries a request, authorizes execution, or spends provider credit.

Together's hourly billing endpoint describes returned windows as finalized through the last
completed UTC hour, while documenting up to one hour of current-month lag and up to 24 hours
of prior-month lag. A capture therefore issues a settlement watermark only when the provider
HTTP Date is strictly beyond the requested whole-hour watermark plus the applicable lag.
``latest_window_end`` remains only the end of the latest window containing usage and is never
treated as a completeness watermark.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import ssl
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, localcontext
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from rejudge import durable_fs


SCHEMA_VERSION = "together_authenticated_billing_capture_v1"
PROVIDER = "Together"
STAGE = "main"
CAPTURE_METHOD = "together_authenticated_read_only_api_v1"
SETTLEMENT_STATUS = "provider_authenticated_finalized"
SETTLEMENT_BASIS = "together_hourly_finalization_with_documented_lag_v1"
ACCOUNT_IDENTITY_SCHEMA = "together_runtime_account_identity_v1"
API_BASE_URL = "https://api.together.ai/v1"
WHOAMI_URL = f"{API_BASE_URL}/whoami"
BILLING_USAGE_URL = f"{API_BASE_URL}/billing/usage"
CURRENT_MONTH_LAG = timedelta(hours=1)
PRIOR_MONTH_LAG = timedelta(hours=24)
MAX_RESPONSE_BYTES = 64 * 1024 * 1024

CAPTURE_FIELDS = frozenset({
    "schema_version",
    "stage",
    "provider",
    "capture_method",
    "currency",
    "observed_at_utc",
    "evidence_only",
    "execution_authorized",
    "provider_calls_authorized",
    "main_run_spend_authorized",
    "billing_scope",
    "identity",
    "settlement",
    "whoami",
    "billing_usage",
    "dashboard",
})
BILLING_SCOPE_FIELDS = frozenset({
    "account_identity_sha256", "window_start_utc", "window_end_utc",
})
IDENTITY_FIELDS = frozenset({
    "schema_version",
    "account_identity_sha256",
    "api_key_id_sha256",
    "project_id_sha256",
    "organization_id_sha256",
})
SETTLEMENT_FIELDS = frozenset({"status", "basis", "finalized_through_utc"})
RESPONSE_BINDING_FIELDS = frozenset({
    "method",
    "url",
    "http_status",
    "response_date_http",
    "response_path",
    "response_raw_sha256",
})
BILLING_BINDING_FIELDS = RESPONSE_BINDING_FIELDS | frozenset({"query"})
QUERY_FIELDS = frozenset({
    "month", "organization_id", "granularity", "limit", "after",
})
DASHBOARD_FIELDS = frozenset({
    "mode", "before_total_usd", "after_total_usd", "reported_delta_usd",
})
WHOAMI_REQUIRED_FIELDS = frozenset({
    "api_key_id",
    "project_id",
    "project_name",
    "project_slug",
    "organization_id",
    "organization_name",
})
WHOAMI_OPTIONAL_FIELDS = frozenset({"user_id"})
REPORT_FIELDS = frozenset({
    "object",
    "organization_id",
    "billing_period",
    "earliest_window_start",
    "latest_window_end",
    "currency",
    "data",
    "next_cursor",
})
WINDOW_FIELDS = frozenset({"date", "start_time", "end_time", "line_items"})
LINE_ITEM_FIELDS = frozenset({
    "product_name", "quantity", "unit_price", "cost", "pricing_dimensions", "attributes",
})

_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class TogetherBillingCaptureError(ValueError):
    """The capture request, evidence, or publication failed closed."""


class TogetherBillingApiUnavailableError(TogetherBillingCaptureError):
    """The beta billing endpoint is not enabled for the authenticated organization."""


@dataclass(frozen=True)
class HttpGetResponse:
    """The only transport result accepted by the capture builder."""

    status: int
    headers: Mapping[str, str]
    body: bytes


Transport = Callable[[str, Mapping[str, str], float], HttpGetResponse]
BodyLoader = Callable[[Path, str, str], bytes]


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TogetherBillingCaptureError(f"{label} must be an object")
    observed = set(value)
    if observed != expected:
        raise TogetherBillingCaptureError(
            f"{label} fields drifted: missing={sorted(expected - observed)!r}, "
            f"unexpected={sorted(observed - expected)!r}"
        )
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise TogetherBillingCaptureError(f"{label} must be a non-empty exact string")
    return value


def _sha256_text(value: Any, label: str) -> str:
    digest = _text(value, label)
    if _SHA256.fullmatch(digest) is None:
        raise TogetherBillingCaptureError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _utc(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise TogetherBillingCaptureError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TogetherBillingCaptureError(f"{label} must use UTC")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _http_date(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError) as exc:
        raise TogetherBillingCaptureError(f"{label} must be an RFC 7231 HTTP Date") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TogetherBillingCaptureError(f"{label} must use GMT")
    return parsed.astimezone(timezone.utc)


def _whole_hour(value: datetime, label: str) -> None:
    if value.minute or value.second or value.microsecond:
        raise TogetherBillingCaptureError(f"{label} must be aligned to a whole UTC hour")


def _month_start(month: str) -> datetime:
    if _MONTH.fullmatch(month) is None:
        raise TogetherBillingCaptureError("billing month must use YYYY-MM")
    return datetime(int(month[:4]), int(month[5:7]), 1, tzinfo=timezone.utc)


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(value.year, value.month + 1, 1, tzinfo=timezone.utc)


def _months_intersecting(start: datetime, end: datetime) -> tuple[str, ...]:
    if end <= start:
        raise TogetherBillingCaptureError("billing settlement interval must be non-empty")
    cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    last = end - timedelta(microseconds=1)
    terminal = datetime(last.year, last.month, 1, tzinfo=timezone.utc)
    months: list[str] = []
    while cursor <= terminal:
        months.append(cursor.strftime("%Y-%m"))
        cursor = _next_month(cursor)
    return tuple(months)


def _decimal_text(value: Any, label: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise TogetherBillingCaptureError(
            f"{label} must be an exact non-negative fixed-point decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise TogetherBillingCaptureError(f"{label} is not a valid decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise TogetherBillingCaptureError(f"{label} is not a valid decimal")
    return amount


def _decimal_string(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _exact_sum(values: Sequence[Decimal]) -> Decimal:
    """Add finite Decimals without inheriting ambient precision."""
    if not values:
        return Decimal("0")
    minimum_exponent = min(int(value.as_tuple().exponent) for value in values)
    maximum_adjusted = max(value.adjusted() for value in values)
    carry_digits = len(str(len(values))) + 1
    precision = max(1, maximum_adjusted - minimum_exponent + 1 + carry_digits)
    with localcontext() as context:
        context.prec = precision
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        context.clamp = 0
        return sum(values, Decimal("0"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TogetherBillingCaptureError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise TogetherBillingCaptureError(
        f"JSON uses forbidden non-finite numeric constant {value!r}")


def _json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TogetherBillingCaptureError(f"{label} is not strict UTF-8 JSON") from exc


def _json_output_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value), ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TogetherBillingCaptureError("capture index is not strict JSON") from exc


def _reject_secret_in_payload(value: Any, *, secret: str, label: str) -> None:
    if isinstance(value, str):
        if secret in value:
            raise TogetherBillingCaptureError(
                f"{label} unexpectedly contains the API-key secret")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_secret_in_payload(key, secret=secret, label=label)
            _reject_secret_in_payload(item, secret=secret, label=label)
        return
    if isinstance(value, list):
        for item in value:
            _reject_secret_in_payload(item, secret=secret, label=label)


def account_identity_sha256(
    *, api_key_id: str, project_id: str, organization_id: str,
) -> str:
    """Hash the versioned credential, project, and organization identity tuple."""
    payload = {
        "api_key_id": _text(api_key_id, "api_key_id"),
        "organization_id": _text(organization_id, "organization_id"),
        "project_id": _text(project_id, "project_id"),
        "provider": PROVIDER,
        "schema_version": ACCOUNT_IDENTITY_SCHEMA,
    }
    raw = json.dumps(
        payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _raw_sha256(raw)


def _identity_from_whoami(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise TogetherBillingCaptureError("whoami response must be an object")
    observed = set(payload)
    if not WHOAMI_REQUIRED_FIELDS <= observed or not observed <= (
        WHOAMI_REQUIRED_FIELDS | WHOAMI_OPTIONAL_FIELDS
    ):
        raise TogetherBillingCaptureError(
            "whoami response fields drifted: "
            f"missing={sorted(WHOAMI_REQUIRED_FIELDS - observed)!r}, "
            f"unexpected={sorted(observed - WHOAMI_REQUIRED_FIELDS - WHOAMI_OPTIONAL_FIELDS)!r}"
        )
    for name in WHOAMI_REQUIRED_FIELDS:
        _text(payload[name], f"whoami.{name}")
    if "user_id" in payload and payload["user_id"] is not None:
        _text(payload["user_id"], "whoami.user_id")
    api_key_id = str(payload["api_key_id"])
    project_id = str(payload["project_id"])
    organization_id = str(payload["organization_id"])
    account = account_identity_sha256(
        api_key_id=api_key_id,
        project_id=project_id,
        organization_id=organization_id,
    )
    return {
        "api_key_id": api_key_id,
        "project_id": project_id,
        "organization_id": organization_id,
        "account_identity_sha256": account,
        "api_key_id_sha256": _raw_sha256(api_key_id.encode("utf-8")),
        "project_id_sha256": _raw_sha256(project_id.encode("utf-8")),
        "organization_id_sha256": _raw_sha256(organization_id.encode("utf-8")),
    }


def _scope(value: Any) -> dict[str, Any]:
    scope = _exact_keys(value, BILLING_SCOPE_FIELDS, "billing_scope")
    account = _sha256_text(
        scope["account_identity_sha256"], "billing_scope.account_identity_sha256")
    start = _utc(scope["window_start_utc"], "billing_scope.window_start_utc")
    end = _utc(scope["window_end_utc"], "billing_scope.window_end_utc")
    _whole_hour(start, "billing_scope.window_start_utc")
    if end <= start:
        raise TogetherBillingCaptureError(
            "billing_scope.window_end_utc must be after window_start_utc")
    return {"raw": dict(scope), "account": account, "start": start, "end": end}


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TogetherBillingCaptureError("HTTP response headers must be text")
        lowered = key.lower()
        if lowered in normalized:
            raise TogetherBillingCaptureError(f"HTTP response repeats header {lowered!r}")
        normalized[lowered] = value
    return normalized


def _checked_response(
    response: HttpGetResponse, *, label: str, unavailable_on_404: bool,
) -> tuple[datetime, str, bytes]:
    if not isinstance(response, HttpGetResponse):
        raise TogetherBillingCaptureError(f"{label} transport returned the wrong type")
    if response.status == 404 and unavailable_on_404:
        raise TogetherBillingApiUnavailableError(
            "Together billing usage API is not enabled for this organization")
    if response.status != 200:
        raise TogetherBillingCaptureError(
            f"{label} returned HTTP {response.status}; no evidence was published")
    if not isinstance(response.body, bytes) or len(response.body) > MAX_RESPONSE_BYTES:
        raise TogetherBillingCaptureError(f"{label} response body is invalid or too large")
    headers = _response_headers(response.headers)
    if "date" not in headers:
        raise TogetherBillingCaptureError(f"{label} response omits the provider HTTP Date")
    response_date = _http_date(headers["date"], f"{label} response Date")
    content_type = headers.get("content-type", "")
    if content_type and "application/json" not in content_type.lower():
        raise TogetherBillingCaptureError(f"{label} response is not JSON")
    return response_date, headers["date"], response.body


def _validate_report(
    payload: Any,
    *,
    month: str,
    organization_id: str,
    api_key_id: str,
    project_id: str,
    scope_start: datetime,
    finalized_through: datetime,
) -> Decimal:
    report = _exact_keys(payload, REPORT_FIELDS, f"billing report {month}")
    if (
        report["object"] != "list"
        or report["organization_id"] != organization_id
        or report["billing_period"] != month
        or report["currency"] != "USD"
    ):
        raise TogetherBillingCaptureError(
            f"billing report {month} identity, period, object, or currency drifted")
    if report["next_cursor"] is not None:
        raise TogetherBillingCaptureError(
            f"billing report {month} unexpectedly requires moving-snapshot pagination")
    raw_windows = report["data"]
    if not isinstance(raw_windows, list):
        raise TogetherBillingCaptureError(f"billing report {month}.data must be a list")
    month_start = _month_start(month)
    month_end = _next_month(month_start)
    starts: list[datetime] = []
    ends: list[datetime] = []
    included_costs: list[Decimal] = []
    for index, raw_window in enumerate(raw_windows):
        label = f"billing report {month}.data[{index}]"
        window = _exact_keys(raw_window, WINDOW_FIELDS, label)
        start = _utc(window["start_time"], f"{label}.start_time")
        end = _utc(window["end_time"], f"{label}.end_time")
        _whole_hour(start, f"{label}.start_time")
        _whole_hour(end, f"{label}.end_time")
        if end - start != timedelta(hours=1):
            raise TogetherBillingCaptureError(f"{label} must cover exactly one UTC hour")
        if not month_start <= start < end <= month_end:
            raise TogetherBillingCaptureError(f"{label} falls outside its billing month")
        if window["date"] != start.date().isoformat():
            raise TogetherBillingCaptureError(f"{label}.date differs from start_time")
        if starts and start <= starts[-1]:
            raise TogetherBillingCaptureError(
                f"billing report {month} windows must be sorted, unique, and nonoverlapping")
        starts.append(start)
        ends.append(end)
        line_items = window["line_items"]
        if not isinstance(line_items, list) or not line_items:
            raise TogetherBillingCaptureError(f"{label}.line_items must be a non-empty list")
        included = scope_start <= start and end <= finalized_through
        overlaps = start < finalized_through and scope_start < end
        if overlaps and not included:
            raise TogetherBillingCaptureError(
                "billing scope and settlement watermark must not partially overlap an hour")
        for item_index, raw_item in enumerate(line_items):
            item_label = f"{label}.line_items[{item_index}]"
            item = _exact_keys(raw_item, LINE_ITEM_FIELDS, item_label)
            _text(item["product_name"], f"{item_label}.product_name")
            _decimal_text(item["quantity"], f"{item_label}.quantity")
            _decimal_text(item["unit_price"], f"{item_label}.unit_price")
            cost = _decimal_text(item["cost"], f"{item_label}.cost")
            if not isinstance(item["pricing_dimensions"], Mapping):
                raise TogetherBillingCaptureError(
                    f"{item_label}.pricing_dimensions must be an object")
            attributes = item["attributes"]
            if not isinstance(attributes, Mapping):
                raise TogetherBillingCaptureError(f"{item_label}.attributes must be an object")
            if included:
                has_key = "api_key_id" in attributes
                has_project = "project_id" in attributes
                if not has_key or not has_project:
                    raise TogetherBillingCaptureError(
                        f"{item_label} lacks exact API-key and project attribution")
                if (
                    attributes["api_key_id"] != api_key_id
                    or attributes["project_id"] != project_id
                ):
                    raise TogetherBillingCaptureError(
                        f"{item_label} belongs to another API key or project")
                included_costs.append(cost)
    earliest = report["earliest_window_start"]
    latest = report["latest_window_end"]
    if not starts:
        if earliest is not None or latest is not None:
            raise TogetherBillingCaptureError(
                f"empty billing report {month} must use null earliest and latest windows")
    else:
        if (
            _utc(earliest, f"billing report {month}.earliest_window_start") != starts[0]
            or _utc(latest, f"billing report {month}.latest_window_end") != ends[-1]
        ):
            raise TogetherBillingCaptureError(
                f"billing report {month} earliest or latest usage window drifted")
    return _exact_sum(included_costs)


def _resolve_response_path(value: Any, *, root: Path, label: str) -> Path:
    text = _text(value, label)
    path = Path(text)
    if not path.is_absolute() and ".." in path.parts:
        raise TogetherBillingCaptureError(f"{label} escapes the project root")
    resolved_root = root.resolve()
    resolved = (path if path.is_absolute() else resolved_root / path).resolve()
    if resolved.parent != resolved_root:
        raise TogetherBillingCaptureError(
            f"{label} must be a direct child of the capture root")
    return resolved


def _file_loader(path: Path, expected_sha256: str, label: str) -> bytes:
    try:
        first = path.read_bytes()
        second = path.read_bytes()
    except OSError as exc:
        raise TogetherBillingCaptureError(f"could not read bound {label}: {path}") from exc
    if first != second:
        raise TogetherBillingCaptureError(f"bound {label} changed while being read: {path}")
    observed = _raw_sha256(first)
    if observed != expected_sha256:
        raise TogetherBillingCaptureError(
            f"bound {label} raw SHA-256 drifted: {observed} != {expected_sha256}")
    return first


def _binding(
    value: Any,
    *,
    expected_fields: frozenset[str],
    expected_url: str,
    root: Path,
    label: str,
    body_loader: BodyLoader,
) -> tuple[Mapping[str, Any], datetime, bytes]:
    binding = _exact_keys(value, expected_fields, label)
    if (
        binding["method"] != "GET"
        or binding["url"] != expected_url
        or binding["http_status"] != 200
    ):
        raise TogetherBillingCaptureError(f"{label} is not the frozen successful GET")
    response_date = _http_date(binding["response_date_http"], f"{label}.response_date_http")
    path = _resolve_response_path(binding["response_path"], root=root, label=f"{label}.response_path")
    digest = _sha256_text(
        binding["response_raw_sha256"], f"{label}.response_raw_sha256")
    body = body_loader(path, digest, f"{label} response")
    return binding, response_date, body


def _lag_deadline(month: str, finalized_through: datetime, response_date: datetime) -> datetime:
    month_start = _month_start(month)
    month_end = _next_month(month_start)
    if response_date < month_start:
        raise TogetherBillingCaptureError(
            f"billing report {month} has a provider Date before its billing month")
    if response_date < month_end:
        if not month_start < finalized_through <= month_end:
            raise TogetherBillingCaptureError(
                f"billing report {month} was captured before the month closed")
        return finalized_through + CURRENT_MONTH_LAG
    return month_end + PRIOR_MONTH_LAG


def _validate_capture(
    record: Mapping[str, Any],
    *,
    project_root: Path,
    body_loader: BodyLoader,
    as_of: datetime | None,
) -> dict[str, Any]:
    _exact_keys(record, CAPTURE_FIELDS, "capture")
    if (
        record["schema_version"] != SCHEMA_VERSION
        or record["stage"] != STAGE
        or record["provider"] != PROVIDER
        or record["capture_method"] != CAPTURE_METHOD
        or record["currency"] != "USD"
    ):
        raise TogetherBillingCaptureError("capture schema, stage, provider, method, or currency drifted")
    if record["evidence_only"] is not True or any(
        record[field] is not False
        for field in (
            "execution_authorized", "provider_calls_authorized", "main_run_spend_authorized"
        )
    ):
        raise TogetherBillingCaptureError(
            "billing capture is evidence only and cannot authorize execution or spend")
    observed_at = _utc(record["observed_at_utc"], "observed_at_utc")
    scope = _scope(record["billing_scope"])
    identity_claim = _exact_keys(record["identity"], IDENTITY_FIELDS, "identity")
    if identity_claim["schema_version"] != ACCOUNT_IDENTITY_SCHEMA:
        raise TogetherBillingCaptureError("identity schema drifted")
    for name in IDENTITY_FIELDS - {"schema_version"}:
        _sha256_text(identity_claim[name], f"identity.{name}")
    settlement = _exact_keys(record["settlement"], SETTLEMENT_FIELDS, "settlement")
    if settlement["status"] != SETTLEMENT_STATUS or settlement["basis"] != SETTLEMENT_BASIS:
        raise TogetherBillingCaptureError("settlement status or derivation basis drifted")
    finalized_through = _utc(
        settlement["finalized_through_utc"], "settlement.finalized_through_utc")
    _whole_hour(finalized_through, "settlement.finalized_through_utc")
    if not scope["start"] < finalized_through < scope["end"] <= observed_at:
        raise TogetherBillingCaptureError(
            "settlement must be strictly inside a completed, observed billing scope")

    _whoami_binding, whoami_date, whoami_raw = _binding(
        record["whoami"],
        expected_fields=RESPONSE_BINDING_FIELDS,
        expected_url=WHOAMI_URL,
        root=project_root,
        label="whoami",
        body_loader=body_loader,
    )
    identity = _identity_from_whoami(_json_bytes(whoami_raw, "whoami response"))
    expected_identity = {
        "schema_version": ACCOUNT_IDENTITY_SCHEMA,
        "account_identity_sha256": identity["account_identity_sha256"],
        "api_key_id_sha256": identity["api_key_id_sha256"],
        "project_id_sha256": identity["project_id_sha256"],
        "organization_id_sha256": identity["organization_id_sha256"],
    }
    if dict(identity_claim) != expected_identity or scope["account"] != identity[
        "account_identity_sha256"
    ]:
        raise TogetherBillingCaptureError(
            "capture identity or billing account hash differs from authenticated whoami")

    raw_reports = record["billing_usage"]
    if not isinstance(raw_reports, list) or not raw_reports:
        raise TogetherBillingCaptureError("billing_usage must be a non-empty list")
    expected_months = _months_intersecting(scope["start"], finalized_through)
    observed_months: list[str] = []
    response_dates = [whoami_date]
    billing_response_dates: list[datetime] = []
    report_deltas: list[Decimal] = []
    for index, raw_binding in enumerate(raw_reports):
        label = f"billing_usage[{index}]"
        binding, response_date, body = _binding(
            raw_binding,
            expected_fields=BILLING_BINDING_FIELDS,
            expected_url=BILLING_USAGE_URL,
            root=project_root,
            label=label,
            body_loader=body_loader,
        )
        query = _exact_keys(binding["query"], QUERY_FIELDS, f"{label}.query")
        month = _text(query["month"], f"{label}.query.month")
        _month_start(month)
        if (
            query["organization_id"] != identity["organization_id"]
            or query["granularity"] != "hour"
            or query["limit"] != 1000
            or isinstance(query["limit"], bool)
            or query["after"] is not None
        ):
            raise TogetherBillingCaptureError(f"{label} query drifted from one-page hourly scope")
        deadline = _lag_deadline(month, finalized_through, response_date)
        if response_date <= deadline:
            raise TogetherBillingCaptureError(
                f"billing report {month} was captured before the documented lag elapsed")
        observed_months.append(month)
        response_dates.append(response_date)
        billing_response_dates.append(response_date)
        report_deltas.append(_validate_report(
            _json_bytes(body, f"billing report {month} response"),
            month=month,
            organization_id=identity["organization_id"],
            api_key_id=identity["api_key_id"],
            project_id=identity["project_id"],
            scope_start=scope["start"],
            finalized_through=finalized_through,
        ))
    if tuple(observed_months) != expected_months:
        raise TogetherBillingCaptureError(
            "billing reports must cover every intersecting UTC month exactly once in order")
    if observed_at != max(response_dates) or any(value > observed_at for value in response_dates):
        raise TogetherBillingCaptureError(
            "observed_at_utc must equal the latest provider HTTP Date")

    provider_delta = _exact_sum(report_deltas)
    dashboard = _exact_keys(record["dashboard"], DASHBOARD_FIELDS, "dashboard")
    if (
        dashboard["mode"] != "direct_delta"
        or dashboard["before_total_usd"] is not None
        or dashboard["after_total_usd"] is not None
    ):
        raise TogetherBillingCaptureError("authenticated dashboard must use direct_delta mode")
    claimed_delta = _decimal_text(
        dashboard["reported_delta_usd"], "dashboard.reported_delta_usd")
    if claimed_delta != provider_delta:
        raise TogetherBillingCaptureError(
            "dashboard delta differs from authenticated key-scoped billing line items")

    if as_of is not None:
        if as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise TogetherBillingCaptureError("as_of must be timezone-aware UTC")
        current = as_of.astimezone(timezone.utc)
        if observed_at > current:
            raise TogetherBillingCaptureError("billing capture lies in the future")

    return {
        "provider": PROVIDER,
        "capture_method": CAPTURE_METHOD,
        "observed_at_utc": _utc_text(observed_at),
        "billing_observed_at_utc": _utc_text(min(billing_response_dates)),
        "billing_scope": scope["raw"],
        "account_identity_sha256": identity["account_identity_sha256"],
        "api_key_id_sha256": identity["api_key_id_sha256"],
        "project_id_sha256": identity["project_id_sha256"],
        "organization_id_sha256": identity["organization_id_sha256"],
        "provider_delta_usd": _decimal_string(provider_delta),
        "dashboard": dict(dashboard),
        "provider_settlement": {
            "status": SETTLEMENT_STATUS,
            "account_identity_sha256": identity["account_identity_sha256"],
            "finalized_through_utc": _utc_text(finalized_through),
        },
        "execution_authorized": False,
    }


def validate_capture(
    record: Mapping[str, Any],
    *,
    project_root: str | Path = ".",
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Validate an index and reopen every exact provider response body."""
    return _validate_capture(
        record,
        project_root=Path(project_root).resolve(),
        body_loader=_file_loader,
        as_of=as_of,
    )


def load_and_validate_capture(
    path: str | Path,
    *,
    project_root: str | Path = ".",
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Load one strict capture index and validate all bound responses."""
    root = Path(project_root).resolve()
    index = Path(path)
    if not index.is_absolute():
        index = root / index
    index = index.resolve()
    try:
        first = index.read_bytes()
        raw = index.read_bytes()
    except OSError as exc:
        raise TogetherBillingCaptureError(
            f"could not read capture index: {index}") from exc
    if first != raw:
        raise TogetherBillingCaptureError(
            f"capture index changed while being read: {index}")
    payload = _json_bytes(raw, "capture index")
    if not isinstance(payload, Mapping):
        raise TogetherBillingCaptureError("capture index must be an object")
    return validate_capture(payload, project_root=index.parent, as_of=as_of)


def _default_transport(
    url: str, headers: Mapping[str, str], timeout_seconds: float,
) -> HttpGetResponse:
    """Perform one fixed HTTPS GET with no redirect or retry behavior."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.together.ai"
        or parsed.port is not None
        or parsed.path not in {"/v1/whoami", "/v1/billing/usage"}
    ):
        raise TogetherBillingCaptureError("refusing an unfrozen Together capture URL")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise TogetherBillingCaptureError("timeout_seconds must be numeric")
    if timeout_seconds <= 0 or timeout_seconds > 120:
        raise TogetherBillingCaptureError("timeout_seconds must be in (0, 120]")
    connection = http.client.HTTPSConnection(
        "api.together.ai",
        timeout=float(timeout_seconds),
        context=ssl.create_default_context(),
    )
    target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    try:
        connection.request("GET", target, headers=dict(headers))
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        response_headers: dict[str, str] = {}
        for key, value in response.getheaders():
            lowered = key.lower()
            if lowered in response_headers:
                raise TogetherBillingCaptureError(
                    f"Together response repeats header {lowered!r}")
            response_headers[lowered] = value
        return HttpGetResponse(
            status=response.status,
            headers=response_headers,
            body=body,
        )
    except (OSError, http.client.HTTPException) as exc:
        raise TogetherBillingCaptureError("Together read-only capture request failed") from exc
    finally:
        connection.close()


def _sidecar_paths(output: Path, months: Sequence[str]) -> tuple[Path, dict[str, Path]]:
    whoami = output.with_name(f"{output.name}.whoami.response.json")
    reports = {
        month: output.with_name(f"{output.name}.billing-usage-{month}.response.json")
        for month in months
    }
    paths = [whoami, *reports.values(), output]
    if len(paths) != len(set(paths)):
        raise TogetherBillingCaptureError("capture output paths collide")
    return whoami, reports


def build_capture(
    *,
    output_path: str | Path,
    window_start_utc: str,
    window_end_utc: str,
    finalized_through_utc: str,
    expected_account_identity_sha256: str,
    api_key: str,
    transport: Transport = _default_transport,
    timeout_seconds: float = 30.0,
) -> tuple[dict[str, Any], dict[Path, bytes]]:
    """Fetch and validate complete evidence in memory without publishing files."""
    output = Path(output_path).resolve()
    key = _text(api_key, "api_key")
    expected_account = _sha256_text(
        expected_account_identity_sha256, "expected_account_identity_sha256")
    start = _utc(window_start_utc, "window_start_utc")
    end = _utc(window_end_utc, "window_end_utc")
    finalized = _utc(finalized_through_utc, "finalized_through_utc")
    _whole_hour(start, "window_start_utc")
    _whole_hour(finalized, "finalized_through_utc")
    if not start < finalized < end:
        raise TogetherBillingCaptureError(
            "finalized_through_utc must be strictly inside the billing window")
    months = _months_intersecting(start, finalized)
    whoami_path, report_paths = _sidecar_paths(output, months)
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "selvarath-phase3-billing-capture/1",
    }
    whoami_response = transport(WHOAMI_URL, headers, timeout_seconds)
    whoami_date, whoami_date_http, whoami_raw = _checked_response(
        whoami_response, label="whoami", unavailable_on_404=False)
    whoami_payload = _json_bytes(whoami_raw, "whoami response")
    _reject_secret_in_payload(
        whoami_payload, secret=key, label="whoami response")
    identity = _identity_from_whoami(whoami_payload)
    if identity["account_identity_sha256"] != expected_account:
        raise TogetherBillingCaptureError(
            "authenticated Together account differs from the selected account identity")

    report_material: dict[str, tuple[datetime, str, bytes, dict[str, Any]]] = {}
    report_deltas: list[Decimal] = []
    for month in months:
        query = {
            "month": month,
            "organization_id": identity["organization_id"],
            "granularity": "hour",
            "limit": 1000,
            "after": None,
        }
        encoded_query = urlencode(
            [(name, value) for name, value in query.items() if value is not None])
        response = transport(f"{BILLING_USAGE_URL}?{encoded_query}", headers, timeout_seconds)
        response_date, response_date_http, raw = _checked_response(
            response, label=f"billing report {month}", unavailable_on_404=True)
        deadline = _lag_deadline(month, finalized, response_date)
        if response_date <= deadline:
            raise TogetherBillingCaptureError(
                f"billing report {month} was captured before the documented lag elapsed")
        report_payload = _json_bytes(raw, f"billing report {month} response")
        _reject_secret_in_payload(
            report_payload, secret=key, label=f"billing report {month} response")
        report_deltas.append(_validate_report(
            report_payload,
            month=month,
            organization_id=identity["organization_id"],
            api_key_id=identity["api_key_id"],
            project_id=identity["project_id"],
            scope_start=start,
            finalized_through=finalized,
        ))
        report_material[month] = (response_date, response_date_http, raw, query)

    provider_delta = _exact_sum(report_deltas)
    observed_at = max([whoami_date, *(item[0] for item in report_material.values())])
    if end > observed_at:
        raise TogetherBillingCaptureError(
            "window_end_utc must not be after the latest provider HTTP Date")
    materials: dict[Path, bytes] = {whoami_path: whoami_raw}
    billing_bindings: list[dict[str, Any]] = []
    for month in months:
        response_date, response_date_http, raw, query = report_material[month]
        del response_date
        path = report_paths[month]
        materials[path] = raw
        billing_bindings.append({
            "method": "GET",
            "url": BILLING_USAGE_URL,
            "http_status": 200,
            "response_date_http": response_date_http,
            "response_path": path.as_posix(),
            "response_raw_sha256": _raw_sha256(raw),
            "query": query,
        })
    identity_claim = {
        "schema_version": ACCOUNT_IDENTITY_SCHEMA,
        "account_identity_sha256": identity["account_identity_sha256"],
        "api_key_id_sha256": identity["api_key_id_sha256"],
        "project_id_sha256": identity["project_id_sha256"],
        "organization_id_sha256": identity["organization_id_sha256"],
    }
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "provider": PROVIDER,
        "capture_method": CAPTURE_METHOD,
        "currency": "USD",
        "observed_at_utc": _utc_text(observed_at),
        "evidence_only": True,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
        "billing_scope": {
            "account_identity_sha256": identity["account_identity_sha256"],
            "window_start_utc": _utc_text(start),
            "window_end_utc": _utc_text(end),
        },
        "identity": identity_claim,
        "settlement": {
            "status": SETTLEMENT_STATUS,
            "basis": SETTLEMENT_BASIS,
            "finalized_through_utc": _utc_text(finalized),
        },
        "whoami": {
            "method": "GET",
            "url": WHOAMI_URL,
            "http_status": 200,
            "response_date_http": whoami_date_http,
            "response_path": whoami_path.as_posix(),
            "response_raw_sha256": _raw_sha256(whoami_raw),
        },
        "billing_usage": billing_bindings,
        "dashboard": {
            "mode": "direct_delta",
            "before_total_usd": None,
            "after_total_usd": None,
            "reported_delta_usd": _decimal_string(provider_delta),
        },
    }

    def memory_loader(path: Path, expected_sha256: str, label: str) -> bytes:
        try:
            raw = materials[path]
        except KeyError as exc:
            raise TogetherBillingCaptureError(
                f"capture material omits {label}: {path}") from exc
        if _raw_sha256(raw) != expected_sha256:
            raise TogetherBillingCaptureError(f"capture material hash drifted: {path}")
        return raw

    _validate_capture(
        record,
        project_root=output.parent,
        body_loader=memory_loader,
        as_of=None,
    )
    serialized = _json_output_bytes(record)
    if key.encode("utf-8") in serialized or any(
        key.encode("utf-8") in raw for raw in materials.values()
    ):
        raise TogetherBillingCaptureError(
            "provider response unexpectedly contains the API-key secret")
    return record, materials


def _publish_bytes_exclusive(
    path: Path,
    raw: bytes,
    *,
    publication_log: list[tuple[Path, bytes]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor, temp_text = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    except OSError as exc:
        raise TogetherBillingCaptureError(
            f"could not create publication stage for {path}") from exc
    temp = Path(temp_text)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if os.name == "nt":
                durable_fs.windows_move_write_through(
                    temp, path, replace_existing=False)
                published = True
            else:
                os.link(temp, path)
                published = True
                parent_descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(parent_descriptor)
                finally:
                    os.close(parent_descriptor)
        except FileExistsError as exc:
            raise TogetherBillingCaptureError(
                f"refusing to overwrite existing capture artifact: {path}") from exc
        except OSError as exc:
            raise TogetherBillingCaptureError(
                f"could not durably publish capture artifact: {path}") from exc
        if publication_log is not None:
            publication_log.append((path, raw))
        if path.read_bytes() != raw:
            raise TogetherBillingCaptureError(
                f"published capture bytes differ from complete payload: {path}")
    except BaseException:
        if published:
            try:
                if path.read_bytes() == raw:
                    path.unlink()
            except OSError:
                pass
        raise
    finally:
        if not published or os.name != "nt":
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


def write_capture_exclusive(
    output_path: str | Path,
    record: Mapping[str, Any],
    materials: Mapping[Path, bytes],
) -> dict[str, Any]:
    """Publish response bodies first and the validated capture index last."""
    output = Path(output_path).resolve()
    normalized_materials = {Path(path).resolve(): raw for path, raw in materials.items()}

    def memory_loader(path: Path, expected_sha256: str, label: str) -> bytes:
        try:
            raw = normalized_materials[path]
        except KeyError as exc:
            raise TogetherBillingCaptureError(
                f"capture publication omits {label}: {path}") from exc
        if _raw_sha256(raw) != expected_sha256:
            raise TogetherBillingCaptureError(f"capture publication hash drifted: {path}")
        return raw

    _validate_capture(
        record,
        project_root=output.parent,
        body_loader=memory_loader,
        as_of=None,
    )
    index_raw = _json_output_bytes(record)
    scope = _scope(record["billing_scope"])
    settlement = _exact_keys(record["settlement"], SETTLEMENT_FIELDS, "settlement")
    finalized = _utc(
        settlement["finalized_through_utc"], "settlement.finalized_through_utc")
    months = _months_intersecting(scope["start"], finalized)
    expected_whoami, expected_reports = _sidecar_paths(output, months)
    expected_material_paths = {expected_whoami, *expected_reports.values()}
    if set(normalized_materials) != expected_material_paths:
        raise TogetherBillingCaptureError(
            "capture publication materials differ from the exact derived sidecar set")
    all_paths = [*normalized_materials, output]
    if len(all_paths) != len(set(all_paths)):
        raise TogetherBillingCaptureError("capture publication paths collide")
    if any(os.path.lexists(path) for path in all_paths):
        existing = next(path for path in all_paths if os.path.lexists(path))
        raise TogetherBillingCaptureError(
            f"refusing to overwrite existing capture artifact: {existing}")
    published: list[tuple[Path, bytes]] = []
    try:
        for path in sorted(normalized_materials, key=lambda item: item.as_posix()):
            _publish_bytes_exclusive(
                path,
                normalized_materials[path],
                publication_log=published,
            )
        _publish_bytes_exclusive(
            output, index_raw, publication_log=published)
        validated = load_and_validate_capture(output, project_root=output.parent)
    except BaseException:
        for path, expected_raw in reversed(published):
            try:
                if path.read_bytes() == expected_raw:
                    path.unlink()
            except OSError:
                pass
        raise
    return {
        "capture_path": output,
        "capture_raw_sha256": _raw_sha256(index_raw),
        **validated,
    }


def capture_and_write(
    *,
    output_path: str | Path,
    window_start_utc: str,
    window_end_utc: str,
    finalized_through_utc: str,
    expected_account_identity_sha256: str,
    api_key: str,
    transport: Transport = _default_transport,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Capture complete evidence, then publish it without replacement."""
    output = Path(output_path).resolve()
    if os.path.lexists(output):
        raise TogetherBillingCaptureError(
            f"refusing to overwrite existing capture index: {output}")
    record, materials = build_capture(
        output_path=output,
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        finalized_through_utc=finalized_through_utc,
        expected_account_identity_sha256=expected_account_identity_sha256,
        api_key=api_key,
        transport=transport,
        timeout_seconds=timeout_seconds,
    )
    return write_capture_exclusive(output, record, materials)


__all__ = [
    "ACCOUNT_IDENTITY_SCHEMA",
    "API_BASE_URL",
    "BILLING_USAGE_URL",
    "CAPTURE_METHOD",
    "CURRENT_MONTH_LAG",
    "HttpGetResponse",
    "PRIOR_MONTH_LAG",
    "PROVIDER",
    "SCHEMA_VERSION",
    "SETTLEMENT_BASIS",
    "SETTLEMENT_STATUS",
    "TogetherBillingApiUnavailableError",
    "TogetherBillingCaptureError",
    "WHOAMI_URL",
    "account_identity_sha256",
    "build_capture",
    "capture_and_write",
    "load_and_validate_capture",
    "validate_capture",
    "write_capture_exclusive",
]
