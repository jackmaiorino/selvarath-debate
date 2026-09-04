"""Read-only provider-billing reconciliation for Phase 3 main.

The record validated here is evidence, never execution authority. It binds one provider
dashboard capture, one exact ledger-coverage inventory, and every immutable usage ledger
included in the reconciliation. Ledger bytes, chain identity, durable tail, exact spend
totals, and unresolved attempt IDs are all recomputed from the bound files.

All record-level dollar values are fixed-point decimal strings. No binary floating-point
arithmetic is used. ``discrepancy_usd`` is defined as provider delta minus locally settled
(``actual``) spend. Conservative uncertain spend remains visible in ``accounted`` spend.
An unresolved historical charge may close only under the distinct conservative-envelope
disposition when provider spend is inside the inclusive ``[actual, accounted]`` interval.

This module never writes, repairs a ledger tail, constructs a client, or calls a provider.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, ROUND_CEILING, localcontext
from pathlib import Path
from typing import Any

from rejudge import api_client
from rejudge import phase3_main_together_billing_capture as together_billing
from rejudge import phase3_main_together_console_billing as console_billing


LEGACY_SCHEMA_VERSION = "phase3_main_billing_reconciliation_v2"
AUTHENTICATED_SCHEMA_VERSION = "phase3_main_billing_reconciliation_v3"
SCHEMA_VERSION = "phase3_main_billing_reconciliation_v4"
PROVIDER = "Together"
LEGACY_BILLING_EVIDENCE_SCHEMA = "together_billing_dashboard_export_v2"
LEGACY_BILLING_EVIDENCE_CAPTURE_METHOD = "together_billing_dashboard_export"
BILLING_EVIDENCE_SCHEMA = together_billing.SCHEMA_VERSION
BILLING_EVIDENCE_CAPTURE_METHOD = together_billing.CAPTURE_METHOD
AUTHENTICATED_EVIDENCE_KIND = "together_authenticated_billing_api"
CONSOLE_EVIDENCE_KIND = "together_console_cost_analytics_and_invoice"
LEGACY_EVIDENCE_KIND = "legacy_dashboard_export"
PROVIDER_SETTLEMENT_STATUS = together_billing.SETTLEMENT_STATUS
CONSOLE_SETTLEMENT_STATUS = console_billing.SETTLEMENT_STATUS
FINALIZED_EVIDENCE_KINDS = frozenset(
    {AUTHENTICATED_EVIDENCE_KIND, CONSOLE_EVIDENCE_KIND}
)
FINALIZED_SETTLEMENT_STATUSES = frozenset(
    {PROVIDER_SETTLEMENT_STATUS, CONSOLE_SETTLEMENT_STATUS}
)
LEDGER_COVERAGE_SCHEMA = "phase3_main_billing_ledger_coverage_v1"
FROZEN_TOLERANCE_USD = "0.01"
PRIOR_SPEND_PRECISION_USD = Decimal("0.00000001")
MAX_PROVIDER_EVIDENCE_AGE = timedelta(hours=1)

RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "stage",
        "run_id",
        "recorded_at_utc",
        "provider",
        "execution_authorized",
        "provider_calls_authorized",
        "main_run_spend_authorized",
        "billing_scope",
        "provider_evidence",
        "ledger_coverage",
        "dashboard",
        "ledgers",
        "ledger_totals",
        "reconciliation",
    }
)
PROVIDER_EVIDENCE_FIELDS = frozenset(
    {"observed_at_utc", "path", "raw_sha256"}
)
ARTIFACT_BINDING_FIELDS = frozenset({"path", "raw_sha256"})
BILLING_SCOPE_FIELDS = frozenset(
    {"account_identity_sha256", "window_start_utc", "window_end_utc"}
)
PROVIDER_EXPORT_FIELDS = frozenset({
    "schema_version",
    "provider",
    "billing_scope",
    "currency",
    "observed_at_utc",
    "capture_method",
    "dashboard",
})
LEDGER_COVERAGE_FIELDS = frozenset(
    {
        "schema_version",
        "stage",
        "provider",
        "execution_authorized",
        "provider_calls_authorized",
        "main_run_spend_authorized",
        "billing_scope",
        "ledger_count",
        "ledgers",
    }
)
COVERED_LEDGER_FIELDS = frozenset({"ledger_id", "raw_sha256"})
DASHBOARD_FIELDS = frozenset(
    {"mode", "before_total_usd", "after_total_usd", "reported_delta_usd"}
)
LEDGER_FIELDS = frozenset(
    {
        "path",
        "raw_sha256",
        "state_raw_sha256",
        "identity",
        "tail_sequence",
        "tail_event_hash",
        "actual_spend_usd",
        "uncertain_spend_usd",
        "accounted_spend_usd",
        "unresolved_attempt_ids",
    }
)
LEDGER_IDENTITY_FIELDS = frozenset(
    {"schema_version", "ledger_id", "ledger_path", "state_path"}
)
LEDGER_TOTAL_FIELDS = frozenset(
    {
        "actual_spend_usd",
        "uncertain_spend_usd",
        "accounted_spend_usd",
        "unresolved_attempt_ids",
    }
)
RECONCILIATION_FIELDS = frozenset(
    {
        "provider_delta_usd",
        "discrepancy_usd",
        "absolute_discrepancy_usd",
        "within_conservative_envelope",
        "tolerance_usd",
        "disposition",
    }
)
STATE_FIELDS = frozenset(
    {"schema_version", "ledger_id", "last_sequence", "last_event_hash"}
)

DASHBOARD_MODE_BEFORE_AFTER = "before_after_totals"
DASHBOARD_MODE_DIRECT_DELTA = "direct_delta"

DISPOSITION_CLOSED = "closed"
DISPOSITION_CLOSED_CONSERVATIVE = "closed_conservative_envelope"
DISPOSITION_CLOSED_PROVIDER_FINAL_BELOW_LOCAL_ACTUAL = (
    "closed_provider_final_below_local_actual"
)
DISPOSITION_OPEN_UNRESOLVED = "open_unresolved_attempts"
DISPOSITION_OPEN_DISCREPANCY = "open_discrepancy"
DISPOSITION_OPEN_BOTH = "open_unresolved_attempts_and_discrepancy"
CLOSED_DISPOSITIONS = frozenset(
    {
        DISPOSITION_CLOSED,
        DISPOSITION_CLOSED_CONSERVATIVE,
        DISPOSITION_CLOSED_PROVIDER_FINAL_BELOW_LOCAL_ACTUAL,
    }
)

_FIXED_NON_NEGATIVE_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_FIXED_SIGNED_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


class BillingReconciliationError(ValueError):
    """The reconciliation record or one of its bound artifacts failed closed."""


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BillingReconciliationError(f"{label} must be an object")
    observed = set(value)
    if observed != expected:
        raise BillingReconciliationError(
            f"{label} fields drifted: missing={sorted(expected - observed)!r}, "
            f"unexpected={sorted(observed - expected)!r}"
        )
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise BillingReconciliationError(f"{label} must be a non-empty exact string")
    return value


def _sha256(value: Any, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise BillingReconciliationError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _utc(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise BillingReconciliationError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise BillingReconciliationError(f"{label} must use UTC")
    return parsed.astimezone(timezone.utc)


def _billing_scope(value: Any, label: str) -> dict[str, Any]:
    scope = _exact_keys(value, BILLING_SCOPE_FIELDS, label)
    account_identity = _sha256(
        scope["account_identity_sha256"], f"{label}.account_identity_sha256"
    )
    window_start = _utc(scope["window_start_utc"], f"{label}.window_start_utc")
    window_end = _utc(scope["window_end_utc"], f"{label}.window_end_utc")
    if window_end <= window_start:
        raise BillingReconciliationError(
            f"{label} window_end_utc must be after window_start_utc"
        )
    return {
        "raw": dict(scope),
        "account_identity_sha256": account_identity,
        "window_start": window_start,
        "window_end": window_end,
    }


def _require_non_authorizing(value: Mapping[str, Any], label: str) -> None:
    if any(
        value[field] is not False
        for field in (
            "execution_authorized",
            "provider_calls_authorized",
            "main_run_spend_authorized",
        )
    ):
        raise BillingReconciliationError(
            f"{label} is evidence only and must not authorize execution or spend"
        )


def _decimal(value: Any, label: str, *, signed: bool = False) -> Decimal:
    pattern = _FIXED_SIGNED_DECIMAL if signed else _FIXED_NON_NEGATIVE_DECIMAL
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        kind = "signed " if signed else "non-negative "
        raise BillingReconciliationError(
            f"{label} must be an exact {kind}fixed-point decimal string"
        )
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:  # defensive; the fixed-point grammar already excludes it
        raise BillingReconciliationError(f"{label} is not a valid decimal") from exc
    if not amount.is_finite() or (not signed and amount < 0):
        raise BillingReconciliationError(f"{label} is not a valid decimal")
    if signed and amount == 0 and value.startswith("-"):
        raise BillingReconciliationError(f"{label} must not use negative zero")
    return amount


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


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BillingReconciliationError(f"{label} must be a non-negative integer")
    return value


def _resolve_artifact(path_text: Any, *, project_root: Path, label: str) -> Path:
    text = _text(path_text, label)
    path = Path(text)
    if not path.is_absolute() and ".." in path.parts:
        raise BillingReconciliationError(f"{label} escapes the project root")
    return (path if path.is_absolute() else project_root / path).resolve()


def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_bound_bytes(path: Path, expected_sha256: str, label: str) -> bytes:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BillingReconciliationError(f"could not read bound {label}: {path}") from exc
    observed = _raw_sha256(raw)
    if observed != expected_sha256:
        raise BillingReconciliationError(
            f"bound {label} raw SHA-256 drifted: {observed} != {expected_sha256}"
        )
    return raw


def _validate_coverage_artifact(
    raw_binding: Any, *, project_root: Path
) -> dict[str, Any]:
    binding = _exact_keys(raw_binding, ARTIFACT_BINDING_FIELDS, "ledger_coverage")
    path = _resolve_artifact(
        binding["path"], project_root=project_root, label="ledger_coverage.path"
    )
    raw_sha256 = _sha256(binding["raw_sha256"], "ledger_coverage.raw_sha256")
    raw = _read_bound_bytes(path, raw_sha256, "ledger coverage")
    payload = _exact_keys(
        _json_bytes(raw, "ledger coverage"),
        LEDGER_COVERAGE_FIELDS,
        "ledger coverage payload",
    )
    if (
        payload["schema_version"] != LEDGER_COVERAGE_SCHEMA
        or payload["stage"] != "main"
        or payload["provider"] != PROVIDER
    ):
        raise BillingReconciliationError(
            "ledger coverage schema, stage, or provider drifted"
        )
    _require_non_authorizing(payload, "ledger coverage")
    scope = _billing_scope(payload["billing_scope"], "ledger coverage billing_scope")
    raw_ledgers = payload["ledgers"]
    if not isinstance(raw_ledgers, list) or not raw_ledgers:
        raise BillingReconciliationError(
            "ledger coverage ledgers must be a non-empty list"
        )
    ledger_count = _non_negative_int(
        payload["ledger_count"], "ledger coverage ledger_count"
    )
    if ledger_count != len(raw_ledgers):
        raise BillingReconciliationError(
            "ledger coverage ledger_count does not match its ledger inventory"
        )
    covered: list[tuple[str, str]] = []
    for index, raw_entry in enumerate(raw_ledgers):
        entry = _exact_keys(
            raw_entry, COVERED_LEDGER_FIELDS, f"ledger coverage ledgers[{index}]"
        )
        covered.append(
            (
                _text(entry["ledger_id"], f"ledger coverage ledgers[{index}].ledger_id"),
                _sha256(
                    entry["raw_sha256"],
                    f"ledger coverage ledgers[{index}].raw_sha256",
                ),
            )
        )
    if covered != sorted(covered):
        raise BillingReconciliationError(
            "ledger coverage ledgers must be sorted by ledger ID and raw hash"
        )
    ledger_ids = [ledger_id for ledger_id, _ in covered]
    ledger_hashes = [raw_hash for _, raw_hash in covered]
    if len(ledger_ids) != len(set(ledger_ids)):
        raise BillingReconciliationError("ledger coverage repeats a ledger ID")
    if len(ledger_hashes) != len(set(ledger_hashes)):
        raise BillingReconciliationError("ledger coverage repeats a ledger raw hash")
    return {
        "path": path,
        "raw_sha256": raw_sha256,
        "scope": scope,
        "ledger_count": ledger_count,
        "ledgers": tuple(covered),
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BillingReconciliationError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _json_bytes(raw: bytes, label: str, *, exact_numbers: bool = False) -> Any:
    try:
        text = raw.decode("utf-8")
        kwargs: dict[str, Any] = {"object_pairs_hook": _unique_object}
        if exact_numbers:
            kwargs["parse_float"] = Decimal
        return json.loads(text, **kwargs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BillingReconciliationError(f"{label} is not valid UTF-8 JSON") from exc


def _jsonl_bytes(raw: bytes, label: str, *, exact_numbers: bool = False) -> list[Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise BillingReconciliationError(f"{label} is not valid UTF-8") from exc
    rows: list[Any] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            kwargs: dict[str, Any] = {"object_pairs_hook": _unique_object}
            if exact_numbers:
                kwargs["parse_float"] = Decimal
            row = json.loads(line, **kwargs)
        except json.JSONDecodeError as exc:
            raise BillingReconciliationError(
                f"{label} has invalid JSON at line {line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise BillingReconciliationError(
                f"{label} row {line_number} must be an object"
            )
        rows.append(row)
    return rows


def _event_cost(event: Mapping[str, Any], label: str) -> Decimal:
    value = event.get("cost_usd")
    if isinstance(value, bool):
        raise BillingReconciliationError(f"{label}.cost_usd is invalid")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, str) and _FIXED_NON_NEGATIVE_DECIMAL.fullmatch(value):
        amount = Decimal(value)
    else:
        raise BillingReconciliationError(f"{label}.cost_usd is invalid")
    if not amount.is_finite() or amount < 0:
        raise BillingReconciliationError(f"{label}.cost_usd is invalid")
    return amount


def _exact_ledger_summary(
    events: Sequence[Mapping[str, Any]], *, label: str
) -> tuple[Decimal, Decimal, Decimal, tuple[str, ...]]:
    """Recompute exact totals and ambiguous attempts after lifecycle validation."""
    reservations: dict[str, Decimal] = {}
    unresolved: set[str] = set()
    actual_costs: list[Decimal] = []
    uncertain_costs: list[Decimal] = []
    for event_number, event in enumerate(events, 1):
        status = event.get("status")
        attempt_id = event.get("attempt_id")
        cost = _event_cost(event, f"{label} event {event_number}")
        if status == "reserved":
            assert isinstance(attempt_id, str)  # checked by api_client lifecycle validation
            reservations[attempt_id] = cost
        elif status in {"success", "charged_malformed"}:
            actual_costs.append(cost)
            assert isinstance(attempt_id, str)
            reservations.pop(attempt_id)
        elif status == "unknown_charge":
            uncertain_costs.append(cost)
            assert isinstance(attempt_id, str)
            reservations.pop(attempt_id)
            unresolved.add(attempt_id)
        elif status == "released_no_charge":
            assert isinstance(attempt_id, str)
            reservations.pop(attempt_id)
        else:  # pragma: no cover - rejected by api_client before this helper runs
            raise BillingReconciliationError(f"{label} has unknown usage status")
    uncertain_costs.extend(reservations.values())
    unresolved.update(reservations)
    actual = _exact_sum(actual_costs)
    uncertain = _exact_sum(uncertain_costs)
    accounted = _exact_sum((actual, uncertain))
    return actual, uncertain, accounted, tuple(sorted(unresolved))


def _attempt_ids(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BillingReconciliationError(f"{label} must be a list")
    items = tuple(_text(item, f"{label} item") for item in value)
    if items != tuple(sorted(set(items))):
        raise BillingReconciliationError(f"{label} must be sorted and unique")
    return items


def _validate_state(
    payload: Any, *, path: Path, identity: Mapping[str, Any], tail_sequence: int,
    tail_event_hash: str,
) -> None:
    state = _exact_keys(payload, STATE_FIELDS, f"ledger state {path}")
    if (
        isinstance(state["schema_version"], bool)
        or not isinstance(state["schema_version"], int)
        or state["schema_version"] != api_client.USAGE_LEDGER_SCHEMA_VERSION
    ):
        raise BillingReconciliationError(f"ledger state schema drifted: {path}")
    if not isinstance(state["ledger_id"], str) or state["ledger_id"] != identity["ledger_id"]:
        raise BillingReconciliationError(f"ledger/state identity mismatch: {path}")
    if (
        isinstance(state["last_sequence"], bool)
        or not isinstance(state["last_sequence"], int)
        or state["last_sequence"] != tail_sequence
    ):
        raise BillingReconciliationError(
            f"ledger state is not at the immutable ledger tail: {path}"
        )
    if (
        not isinstance(state["last_event_hash"], str)
        or state["last_event_hash"] != tail_event_hash
    ):
        raise BillingReconciliationError(f"ledger state tail hash mismatch: {path}")


def _validate_ledger(
    raw_entry: Any, *, project_root: Path, index: int,
) -> dict[str, Any]:
    label = f"ledgers[{index}]"
    entry = _exact_keys(raw_entry, LEDGER_FIELDS, label)
    path = _resolve_artifact(entry["path"], project_root=project_root, label=f"{label}.path")
    ledger_sha = _sha256(entry["raw_sha256"], f"{label}.raw_sha256")
    raw = _read_bound_bytes(path, ledger_sha, f"ledger {index}")
    ordinary_events = _jsonl_bytes(raw, f"ledger {index}")
    exact_events = _jsonl_bytes(raw, f"ledger {index}", exact_numbers=True)
    if not ordinary_events or len(ordinary_events) != len(exact_events):
        raise BillingReconciliationError(f"ledger {index} is empty or inconsistently parsed")

    try:
        identity, event_hashes = api_client._validate_usage_chain(ordinary_events, path)
        api_client._summarize_usage_events(
            ordinary_events[1:], path, strict_lifecycle=True
        )
    except (api_client.UsageLedgerError, TypeError, ValueError) as exc:
        raise BillingReconciliationError(f"ledger {index} failed chain/lifecycle validation") from exc

    claimed_identity = _exact_keys(entry["identity"], LEDGER_IDENTITY_FIELDS, f"{label}.identity")
    if (
        isinstance(claimed_identity["schema_version"], bool)
        or not isinstance(claimed_identity["schema_version"], int)
        or not isinstance(claimed_identity["ledger_id"], str)
        or not claimed_identity["ledger_id"]
        or not isinstance(claimed_identity["ledger_path"], str)
        or not claimed_identity["ledger_path"]
        or not isinstance(claimed_identity["state_path"], str)
        or not claimed_identity["state_path"]
    ):
        raise BillingReconciliationError(f"{label}.identity fields are invalid")
    if dict(claimed_identity) != identity:
        raise BillingReconciliationError(f"{label}.identity does not match ledger genesis")
    tail_sequence = _non_negative_int(entry["tail_sequence"], f"{label}.tail_sequence")
    tail_hash = _sha256(entry["tail_event_hash"], f"{label}.tail_event_hash")
    if tail_sequence != len(event_hashes) - 1 or tail_hash != event_hashes[-1]:
        raise BillingReconciliationError(f"{label} tail binding does not match ledger bytes")

    state_path = Path(str(identity["state_path"]))
    state_sha = _sha256(entry["state_raw_sha256"], f"{label}.state_raw_sha256")
    state_raw = _read_bound_bytes(state_path, state_sha, f"ledger state {index}")
    state_payload = _json_bytes(state_raw, f"ledger state {index}")
    _validate_state(
        state_payload,
        path=state_path,
        identity=identity,
        tail_sequence=tail_sequence,
        tail_event_hash=tail_hash,
    )

    actual, uncertain, accounted, unresolved = _exact_ledger_summary(
        exact_events[1:], label=label
    )
    claims = {
        "actual_spend_usd": actual,
        "uncertain_spend_usd": uncertain,
        "accounted_spend_usd": accounted,
    }
    for field, expected in claims.items():
        observed = _decimal(entry[field], f"{label}.{field}")
        if observed != expected:
            raise BillingReconciliationError(
                f"{label}.{field} does not match recomputed ledger total"
            )
    claimed_unresolved = _attempt_ids(
        entry["unresolved_attempt_ids"], f"{label}.unresolved_attempt_ids"
    )
    if claimed_unresolved != unresolved:
        raise BillingReconciliationError(
            f"{label}.unresolved_attempt_ids do not match ledger lifecycle"
        )
    event_times = tuple(
        _utc(event.get("ts"), f"{label} event {event_number}.ts")
        for event_number, event in enumerate(ordinary_events[1:], 1)
    )
    return {
        "path": path,
        "ledger_id": identity["ledger_id"],
        "raw_sha256": ledger_sha,
        "actual": actual,
        "uncertain": uncertain,
        "accounted": accounted,
        "unresolved": unresolved,
        "event_times": event_times,
    }


def _dashboard_delta(value: Any) -> Decimal:
    dashboard = _exact_keys(value, DASHBOARD_FIELDS, "dashboard")
    mode = dashboard["mode"]
    if mode == DASHBOARD_MODE_BEFORE_AFTER:
        before = _decimal(dashboard["before_total_usd"], "dashboard.before_total_usd")
        after = _decimal(dashboard["after_total_usd"], "dashboard.after_total_usd")
        if dashboard["reported_delta_usd"] is not None:
            raise BillingReconciliationError(
                "dashboard.reported_delta_usd must be null in before/after mode"
            )
        if after < before:
            raise BillingReconciliationError("dashboard total decreased across evidence window")
        return _exact_sum((after, before.copy_negate()))
    if mode == DASHBOARD_MODE_DIRECT_DELTA:
        if dashboard["before_total_usd"] is not None or dashboard["after_total_usd"] is not None:
            raise BillingReconciliationError(
                "dashboard before/after totals must be null in direct-delta mode"
            )
        return _decimal(dashboard["reported_delta_usd"], "dashboard.reported_delta_usd")
    raise BillingReconciliationError("dashboard.mode is unsupported")


def _expected_disposition(
    *, unresolved: bool, discrepant: bool, within_conservative_envelope: bool,
    provider_final: bool, provider_delta: Decimal, actual: Decimal,
) -> str:
    if provider_final and provider_delta < actual:
        return DISPOSITION_CLOSED_PROVIDER_FINAL_BELOW_LOCAL_ACTUAL
    if unresolved and within_conservative_envelope:
        return DISPOSITION_CLOSED_CONSERVATIVE
    if unresolved and discrepant:
        return DISPOSITION_OPEN_BOTH
    if unresolved:
        return DISPOSITION_OPEN_UNRESOLVED
    if discrepant:
        return DISPOSITION_OPEN_DISCREPANCY
    return DISPOSITION_CLOSED


def validate_billing_reconciliation(
    record: Mapping[str, Any], *, project_root: str | Path = ".",
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Validate one complete reconciliation against immutable local evidence."""
    root = Path(project_root).resolve()
    _exact_keys(record, RECORD_FIELDS, "record")
    schema_version = record["schema_version"]
    if (
        schema_version not in {
            LEGACY_SCHEMA_VERSION,
            AUTHENTICATED_SCHEMA_VERSION,
            SCHEMA_VERSION,
        }
        or record["stage"] != "main"
    ):
        raise BillingReconciliationError("unsupported reconciliation schema or stage")
    run_id = _text(record["run_id"], "run_id")
    recorded_at = _utc(record["recorded_at_utc"], "recorded_at_utc")
    if record["provider"] != PROVIDER:
        raise BillingReconciliationError(f"provider must be exactly {PROVIDER!r}")
    _require_non_authorizing(record, "billing reconciliation")
    record_scope = _billing_scope(record["billing_scope"], "billing_scope")

    evidence = _exact_keys(
        record["provider_evidence"], PROVIDER_EVIDENCE_FIELDS, "provider_evidence"
    )
    observed_at = _utc(evidence["observed_at_utc"], "provider_evidence.observed_at_utc")
    if recorded_at < observed_at:
        raise BillingReconciliationError("reconciliation record predates provider evidence")
    evidence_path = _resolve_artifact(
        evidence["path"], project_root=root, label="provider_evidence.path"
    )
    evidence_sha = _sha256(evidence["raw_sha256"], "provider_evidence.raw_sha256")
    evidence_raw = _read_bound_bytes(evidence_path, evidence_sha, "provider evidence")
    provider_settlement: Mapping[str, Any] | None = None
    billing_observed_at: datetime | None = None
    if schema_version == LEGACY_SCHEMA_VERSION:
        evidence_payload = _exact_keys(
            _json_bytes(evidence_raw, "provider evidence", exact_numbers=True),
            PROVIDER_EXPORT_FIELDS,
            "provider evidence payload",
        )
        if (
            evidence_payload["schema_version"] != LEGACY_BILLING_EVIDENCE_SCHEMA
            or evidence_payload["provider"] != PROVIDER
            or evidence_payload["currency"] != "USD"
            or evidence_payload["capture_method"]
            != LEGACY_BILLING_EVIDENCE_CAPTURE_METHOD
        ):
            raise BillingReconciliationError(
                "provider evidence is not the frozen legacy Together USD dashboard export")
        evidence_scope = _billing_scope(
            evidence_payload["billing_scope"], "provider evidence billing_scope"
        )
        payload_observed_at = _utc(
            evidence_payload["observed_at_utc"],
            "provider evidence payload observed_at_utc",
        )
        dashboard_payload = _exact_keys(
            evidence_payload["dashboard"],
            DASHBOARD_FIELDS,
            "provider evidence payload dashboard",
        )
        provider_delta = _dashboard_delta(dashboard_payload)
        evidence_kind = LEGACY_EVIDENCE_KIND
    elif schema_version == AUTHENTICATED_SCHEMA_VERSION:
        authenticated_payload = _json_bytes(evidence_raw, "provider evidence")
        if not isinstance(authenticated_payload, Mapping):
            raise BillingReconciliationError(
                "authenticated provider evidence must be an object")
        try:
            authenticated = together_billing.validate_capture(
                authenticated_payload,
                project_root=evidence_path.parent,
                as_of=as_of,
            )
        except together_billing.TogetherBillingCaptureError as exc:
            raise BillingReconciliationError(
                f"authenticated Together billing capture failed: {exc}") from exc
        evidence_scope = _billing_scope(
            authenticated["billing_scope"], "provider evidence billing_scope")
        payload_observed_at = _utc(
            authenticated["observed_at_utc"],
            "provider evidence payload observed_at_utc",
        )
        dashboard_payload = _exact_keys(
            authenticated["dashboard"],
            DASHBOARD_FIELDS,
            "provider evidence payload dashboard",
        )
        provider_delta = _decimal(
            authenticated["provider_delta_usd"],
            "authenticated provider evidence delta",
        )
        provider_settlement = authenticated["provider_settlement"]
        billing_observed_at = _utc(
            authenticated["billing_observed_at_utc"],
            "authenticated billing response observed_at_utc",
        )
        evidence_kind = AUTHENTICATED_EVIDENCE_KIND
    else:
        console_payload = _json_bytes(evidence_raw, "provider evidence")
        if not isinstance(console_payload, Mapping):
            raise BillingReconciliationError(
                "Together console provider evidence must be an object")
        try:
            console = console_billing.validate_capture(
                console_payload,
                project_root=evidence_path.parent,
                as_of=as_of,
            )
        except console_billing.TogetherConsoleBillingError as exc:
            raise BillingReconciliationError(
                f"Together console billing evidence failed: {exc}") from exc
        evidence_scope = _billing_scope(
            console["billing_scope"], "provider evidence billing_scope")
        payload_observed_at = _utc(
            console["observed_at_utc"],
            "provider evidence payload observed_at_utc",
        )
        dashboard_payload = _exact_keys(
            console["dashboard"],
            DASHBOARD_FIELDS,
            "provider evidence payload dashboard",
        )
        provider_delta = _decimal(
            console["provider_delta_usd"],
            "Together console provider evidence delta",
        )
        provider_settlement = console["provider_settlement"]
        billing_observed_at = _utc(
            console["billing_observed_at_utc"],
            "Together console evidence observed_at_utc",
        )
        evidence_kind = CONSOLE_EVIDENCE_KIND
    if evidence_scope["raw"] != record_scope["raw"]:
        raise BillingReconciliationError(
            "provider evidence billing scope differs from the reconciliation"
        )
    if payload_observed_at != observed_at:
        raise BillingReconciliationError(
            "provider evidence timestamp differs from its bound payload")
    if observed_at < record_scope["window_end"]:
        raise BillingReconciliationError(
            "provider evidence predates the end of its billing window"
        )
    dashboard = _exact_keys(record["dashboard"], DASHBOARD_FIELDS, "dashboard")
    if dict(dashboard) != dict(dashboard_payload):
        raise BillingReconciliationError(
            "dashboard claims differ from the machine-readable provider evidence")

    coverage = _validate_coverage_artifact(
        record["ledger_coverage"], project_root=root
    )
    if coverage["scope"]["raw"] != record_scope["raw"]:
        raise BillingReconciliationError(
            "ledger coverage billing scope differs from the reconciliation"
        )

    if as_of is not None:
        if as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise BillingReconciliationError("as_of must be timezone-aware UTC")
        current = as_of.astimezone(timezone.utc)
        if recorded_at > current or observed_at > current:
            raise BillingReconciliationError("billing evidence or reconciliation lies in the future")
        if evidence_kind != CONSOLE_EVIDENCE_KIND:
            freshness_time = billing_observed_at or observed_at
            if current - freshness_time > MAX_PROVIDER_EVIDENCE_AGE:
                raise BillingReconciliationError("provider billing evidence is stale")

    raw_ledgers = record["ledgers"]
    if not isinstance(raw_ledgers, list):
        raise BillingReconciliationError("ledgers must be a non-empty list")
    if not raw_ledgers:
        raise BillingReconciliationError("ledgers must be a non-empty list")
    ledgers = [
        _validate_ledger(entry, project_root=root, index=index)
        for index, entry in enumerate(raw_ledgers)
    ]
    if provider_settlement is not None:
        finalized_through = _utc(
            provider_settlement["finalized_through_utc"],
            "provider settlement finalized_through_utc",
        )
        for index, item in enumerate(ledgers):
            for event_time in item["event_times"]:
                if not record_scope["window_start"] <= event_time < finalized_through:
                    raise BillingReconciliationError(
                        f"ledger {index} event lies outside authenticated settlement coverage")
    canonical_paths = [item["path"].as_posix() for item in ledgers]
    if canonical_paths != sorted(set(canonical_paths)):
        raise BillingReconciliationError("ledger paths must be sorted and unique")
    ledger_ids = [item["ledger_id"] for item in ledgers]
    if len(ledger_ids) != len(set(ledger_ids)):
        raise BillingReconciliationError("ledger identities must be unique")
    reconciled_coverage = tuple(
        sorted((item["ledger_id"], item["raw_sha256"]) for item in ledgers)
    )
    if (
        coverage["ledger_count"] != len(ledgers)
        or coverage["ledgers"] != reconciled_coverage
    ):
        raise BillingReconciliationError(
            "reconciliation ledgers do not exactly match the bound coverage inventory"
        )

    actual = _exact_sum(tuple(item["actual"] for item in ledgers))
    uncertain = _exact_sum(tuple(item["uncertain"] for item in ledgers))
    accounted = _exact_sum((actual, uncertain))
    unresolved_items = [
        attempt_id for item in ledgers for attempt_id in item["unresolved"]
    ]
    if len(unresolved_items) != len(set(unresolved_items)):
        raise BillingReconciliationError("attempt IDs must be unique across ledgers")
    unresolved = tuple(sorted(unresolved_items))

    totals = _exact_keys(record["ledger_totals"], LEDGER_TOTAL_FIELDS, "ledger_totals")
    expected_totals = {
        "actual_spend_usd": actual,
        "uncertain_spend_usd": uncertain,
        "accounted_spend_usd": accounted,
    }
    for field, expected in expected_totals.items():
        observed = _decimal(totals[field], f"ledger_totals.{field}")
        if observed != expected:
            raise BillingReconciliationError(
                f"ledger_totals.{field} does not equal the sum of bound ledgers"
            )
    claimed_unresolved = _attempt_ids(
        totals["unresolved_attempt_ids"], "ledger_totals.unresolved_attempt_ids"
    )
    if claimed_unresolved != unresolved:
        raise BillingReconciliationError(
            "ledger_totals.unresolved_attempt_ids do not equal the bound-ledger union"
        )

    reconciliation = _exact_keys(
        record["reconciliation"], RECONCILIATION_FIELDS, "reconciliation"
    )
    claimed_provider_delta = _decimal(
        reconciliation["provider_delta_usd"], "reconciliation.provider_delta_usd"
    )
    if claimed_provider_delta != provider_delta:
        raise BillingReconciliationError(
            "reconciliation.provider_delta_usd does not match dashboard evidence arithmetic"
        )
    discrepancy = _exact_sum((provider_delta, actual.copy_negate()))
    claimed_discrepancy = _decimal(
        reconciliation["discrepancy_usd"], "reconciliation.discrepancy_usd", signed=True
    )
    if claimed_discrepancy != discrepancy:
        raise BillingReconciliationError(
            "reconciliation.discrepancy_usd must equal provider delta minus actual spend"
        )
    absolute_discrepancy = abs(discrepancy)
    claimed_absolute = _decimal(
        reconciliation["absolute_discrepancy_usd"],
        "reconciliation.absolute_discrepancy_usd",
    )
    if claimed_absolute != absolute_discrepancy:
        raise BillingReconciliationError(
            "reconciliation.absolute_discrepancy_usd arithmetic drifted"
        )
    tolerance = _decimal(reconciliation["tolerance_usd"], "reconciliation.tolerance_usd")
    if reconciliation["tolerance_usd"] != FROZEN_TOLERANCE_USD:
        raise BillingReconciliationError(
            f"reconciliation tolerance must remain exactly {FROZEN_TOLERANCE_USD} USD")
    within_conservative_envelope = actual <= provider_delta <= accounted
    if (
        not isinstance(reconciliation["within_conservative_envelope"], bool)
        or reconciliation["within_conservative_envelope"]
        is not within_conservative_envelope
    ):
        raise BillingReconciliationError(
            "reconciliation.within_conservative_envelope arithmetic drifted"
        )
    expected_disposition = _expected_disposition(
        unresolved=bool(unresolved),
        discrepant=absolute_discrepancy > tolerance,
        within_conservative_envelope=within_conservative_envelope,
        provider_final=evidence_kind in FINALIZED_EVIDENCE_KINDS,
        provider_delta=provider_delta,
        actual=actual,
    )
    with localcontext() as context:
        context.prec = max(100, len(accounted.as_tuple().digits) + 10)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        prior_spend_upper_bound = accounted.quantize(
            PRIOR_SPEND_PRECISION_USD,
            rounding=ROUND_CEILING,
        )
    if reconciliation["disposition"] != expected_disposition:
        raise BillingReconciliationError(
            f"reconciliation.disposition must be {expected_disposition!r}"
        )

    return {
        "run_id": run_id,
        "provider": PROVIDER,
        "evidence_kind": evidence_kind,
        "evidence_path": evidence_path,
        "coverage_path": coverage["path"],
        "billing_scope": record_scope["raw"],
        "ledger_paths": tuple(item["path"] for item in ledgers),
        "actual_spend_usd": record["ledger_totals"]["actual_spend_usd"],
        "uncertain_spend_usd": record["ledger_totals"]["uncertain_spend_usd"],
        "accounted_spend_usd": record["ledger_totals"]["accounted_spend_usd"],
        "prior_spend_upper_bound_usd": format(prior_spend_upper_bound, "f"),
        "provider_delta_usd": reconciliation["provider_delta_usd"],
        "discrepancy_usd": reconciliation["discrepancy_usd"],
        "unresolved_attempt_ids": unresolved,
        "within_conservative_envelope": within_conservative_envelope,
        "closed": expected_disposition in CLOSED_DISPOSITIONS,
        "disposition": expected_disposition,
        "provider_settlement": (
            dict(provider_settlement) if provider_settlement is not None else None
        ),
    }


def load_and_validate_billing_reconciliation(
    path: str | Path, *, project_root: str | Path = "."
) -> dict[str, Any]:
    """Load a strict JSON record and validate every bound file without mutation."""
    record_path = Path(path)
    if not record_path.is_absolute():
        record_path = Path(project_root).resolve() / record_path
    try:
        raw = record_path.resolve().read_bytes()
    except OSError as exc:
        raise BillingReconciliationError(
            f"could not read reconciliation record: {record_path}"
        ) from exc
    payload = _json_bytes(raw, "billing reconciliation record")
    if not isinstance(payload, Mapping):
        raise BillingReconciliationError("billing reconciliation record must be an object")
    return validate_billing_reconciliation(payload, project_root=project_root)


__all__ = [
    "AUTHENTICATED_EVIDENCE_KIND",
    "BILLING_EVIDENCE_CAPTURE_METHOD",
    "BILLING_EVIDENCE_SCHEMA",
    "BillingReconciliationError",
    "CLOSED_DISPOSITIONS",
    "DASHBOARD_MODE_BEFORE_AFTER",
    "DASHBOARD_MODE_DIRECT_DELTA",
    "DISPOSITION_CLOSED",
    "DISPOSITION_CLOSED_CONSERVATIVE",
    "DISPOSITION_OPEN_BOTH",
    "DISPOSITION_OPEN_DISCREPANCY",
    "DISPOSITION_OPEN_UNRESOLVED",
    "FROZEN_TOLERANCE_USD",
    "LEDGER_COVERAGE_SCHEMA",
    "LEGACY_BILLING_EVIDENCE_CAPTURE_METHOD",
    "LEGACY_BILLING_EVIDENCE_SCHEMA",
    "LEGACY_EVIDENCE_KIND",
    "LEGACY_SCHEMA_VERSION",
    "PROVIDER",
    "PROVIDER_SETTLEMENT_STATUS",
    "SCHEMA_VERSION",
    "load_and_validate_billing_reconciliation",
    "validate_billing_reconciliation",
]
