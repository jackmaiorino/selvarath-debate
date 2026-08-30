"""Build and validate an explicit, non-authorizing billing evidence inventory.

The inventory is deliberately narrower than an authoritative billing reconciliation. It
accepts only caller-named local files, records exact local spend evidence, and permanently
states that authoritative completeness has not been established. It never scans a
directory, reads credentials, constructs a provider client, or grants execution authority.

The billing window is half-open: ``window_start_utc <= row timestamp < window_end_utc``.
Usage-ledger genesis rows are integrity metadata and are not billing rows. Every other
ledger row and every auxiliary screen row must fall inside the declared window.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from rejudge import api_client
from rejudge import phase3_main_billing_reconciliation as billing


SCHEMA_VERSION = "phase3_main_billing_evidence_inventory_v2"
PROVIDER = "Together"
SELECTION_BASIS = "explicit_local_source_list"
AUTHORITATIVE_COMPLETENESS = "not_established"
LEDGER_KIND = "immutable_usage_ledger"
AUXILIARY_KIND = "auxiliary_screen_usage_jsonl"

INVENTORY_FIELDS = frozenset({
    "schema_version",
    "stage",
    "provider",
    "evidence_only",
    "execution_authorized",
    "provider_calls_authorized",
    "main_run_spend_authorized",
    "selection_basis",
    "authoritative_completeness",
    "billing_window",
    "source_count",
    "sources",
    "totals",
})
WINDOW_FIELDS = frozenset({"window_start_utc", "window_end_utc"})
LINE_REF_FIELDS = frozenset({"source_id", "line_number"})
TOTAL_FIELDS = frozenset({
    "row_count",
    "first_row_utc",
    "last_row_utc",
    "actual_spend_usd",
    "uncertain_spend_usd",
    "accounted_spend_usd",
    "uncertain_line_refs",
})
COMMON_SOURCE_FIELDS = frozenset({
    "source_id",
    "source_kind",
    "path",
    "raw_sha256",
    "row_count",
    "first_row_utc",
    "last_row_utc",
    "actual_spend_usd",
    "uncertain_spend_usd",
    "accounted_spend_usd",
    "uncertain_line_refs",
})
LEDGER_SOURCE_FIELDS = COMMON_SOURCE_FIELDS | frozenset({
    "state_path",
    "state_raw_sha256",
    "ledger_id",
    "tail_sequence",
    "tail_event_hash",
})
AUXILIARY_SOURCE_FIELDS = COMMON_SOURCE_FIELDS

_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_DECIMAL_TEXT = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")

_CHAIN_FIELDS = frozenset({
    "ledger_id", "sequence", "prev_event_hash", "event_hash", "ts",
})
_USAGE_FIELDS = frozenset({
    "status", "attempt_id", "model", "kind", "seed", "attempt",
    "prompt_tokens", "completion_tokens", "reserved_prompt_tokens",
    "reserved_completion_tokens", "estimated_tokens", "cost_usd", "metadata",
})
_GENESIS_FIELDS = _CHAIN_FIELDS | frozenset({"status", "schema_version"})
_LEDGER_EVENT_FIELDS = {
    "reserved": _CHAIN_FIELDS | _USAGE_FIELDS,
    "released_no_charge": _CHAIN_FIELDS | _USAGE_FIELDS,
    "unknown_charge": _CHAIN_FIELDS | _USAGE_FIELDS | frozenset({"error"}),
    "success": _CHAIN_FIELDS | _USAGE_FIELDS,
    "charged_malformed": _CHAIN_FIELDS | _USAGE_FIELDS,
}

_AUX_BASE = frozenset({"ts", "reserved_usd", "actual_usd"})
_AUX_IDENTITIES = (
    frozenset({"config", "prompt_key"}),
    frozenset({"probe_id"}),
)
_AUX_OUTCOMES = (
    frozenset({"attempt", "error"}),
    frozenset({"prompt_tokens", "completion_tokens", "finish_reason"}),
)
_AUX_ALLOWED_FIELD_SETS = frozenset(
    _AUX_BASE | identity | outcome
    for identity in _AUX_IDENTITIES
    for outcome in _AUX_OUTCOMES
)


class BillingEvidenceInventoryError(ValueError):
    """An inventory or one of its explicitly bound sources failed validation."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BillingEvidenceInventoryError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BillingEvidenceInventoryError(f"{label} must be an object")
    observed = set(value)
    if observed != expected:
        raise BillingEvidenceInventoryError(
            f"{label} fields drifted: missing={sorted(expected - observed)!r}, "
            f"unexpected={sorted(observed - expected)!r}"
        )
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise BillingEvidenceInventoryError(f"{label} must be a non-empty exact string")
    return value


def _source_id(value: Any, label: str) -> str:
    source_id = _text(value, label)
    if _SOURCE_ID.fullmatch(source_id) is None:
        raise BillingEvidenceInventoryError(
            f"{label} must contain only letters, digits, dot, underscore, or hyphen"
        )
    return source_id


def _utc(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise BillingEvidenceInventoryError(
            f"{label} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise BillingEvidenceInventoryError(f"{label} must use UTC")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _window(value: Any) -> tuple[dict[str, str], datetime, datetime]:
    window = _exact_keys(value, WINDOW_FIELDS, "billing_window")
    start = _utc(window["window_start_utc"], "billing_window.window_start_utc")
    end = _utc(window["window_end_utc"], "billing_window.window_end_utc")
    if end <= start:
        raise BillingEvidenceInventoryError(
            "billing_window.window_end_utc must be after window_start_utc"
        )
    return {
        "window_start_utc": _utc_text(start),
        "window_end_utc": _utc_text(end),
    }, start, end


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BillingEvidenceInventoryError(f"{label} must be a non-negative integer")
    return value


def _non_negative_integral_lexeme(value: Any, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise BillingEvidenceInventoryError(
            f"{label} must use a non-negative JSON integer lexeme"
        )
    return value


def _json_number_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise BillingEvidenceInventoryError(
            f"{label} must be a non-negative JSON number"
        )
    return _decimal(value, label)


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BillingEvidenceInventoryError(f"{label} must be an exact non-negative number")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, str) and _DECIMAL_TEXT.fullmatch(value) is not None:
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:  # pragma: no cover - grammar excludes this
            raise BillingEvidenceInventoryError(f"{label} is invalid") from exc
    else:
        raise BillingEvidenceInventoryError(f"{label} must be an exact non-negative number")
    if not amount.is_finite() or amount < 0:
        raise BillingEvidenceInventoryError(f"{label} must be finite and non-negative")
    return amount


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _exact_sum(values: Sequence[Decimal]) -> Decimal:
    """Add finite Decimals with enough local precision to preserve every input digit."""
    if not values:
        return Decimal("0")
    exponents = [value.as_tuple().exponent for value in values]
    if any(not isinstance(exponent, int) for exponent in exponents):  # pragma: no cover
        raise BillingEvidenceInventoryError("exact sum received a non-finite Decimal")
    integer_exponents = [exponent for exponent in exponents if isinstance(exponent, int)]
    minimum_exponent = min(integer_exponents)
    maximum_adjusted = max(value.adjusted() for value in values)
    carry_digits = len(str(len(values))) + 1
    precision = max(1, maximum_adjusted - minimum_exponent + 1 + carry_digits)
    with localcontext() as context:
        context.prec = precision
        return sum(values, Decimal("0"))


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(value: Any, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise BillingEvidenceInventoryError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _stable_bytes(path: Path, label: str) -> bytes:
    try:
        first = path.read_bytes()
        second = path.read_bytes()
    except OSError as exc:
        raise BillingEvidenceInventoryError(f"could not read {label}: {path}") from exc
    if first != second:
        raise BillingEvidenceInventoryError(f"{label} changed while it was read: {path}")
    return first


def _require_unchanged(path: Path, expected: bytes, label: str) -> None:
    try:
        observed = path.read_bytes()
    except OSError as exc:
        raise BillingEvidenceInventoryError(
            f"could not reopen {label}: {path}"
        ) from exc
    if observed != expected:
        raise BillingEvidenceInventoryError(
            f"{label} changed across ledger/state snapshot acquisition: {path}"
        )


def _json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BillingEvidenceInventoryError(f"{label} is not valid UTF-8 JSON") from exc


def _jsonl_bytes(raw: bytes, label: str, *, exact_numbers: bool) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise BillingEvidenceInventoryError(f"{label} is not valid UTF-8") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise BillingEvidenceInventoryError(
                f"{label} has a blank physical line at line {line_number}"
            )
        options: dict[str, Any] = {"object_pairs_hook": _unique_object}
        if exact_numbers:
            # Keep integer lexemes distinguishable from decimal or exponent lexemes.
            # Decimal still parses every non-integer number without binary rounding.
            options["parse_float"] = Decimal
        try:
            row = json.loads(line, **options)
        except json.JSONDecodeError as exc:
            raise BillingEvidenceInventoryError(
                f"{label} has invalid JSON at line {line_number}"
            ) from exc
        if not isinstance(row, dict):
            raise BillingEvidenceInventoryError(
                f"{label} row {line_number} must be an object"
            )
        rows.append(row)
    return rows


def _source_path(value: str | Path, *, project_root: Path, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise BillingEvidenceInventoryError(f"{label} must be a path")
    path = Path(value)
    return (path if path.is_absolute() else project_root / path).resolve()


def _row_timestamp(
    row: Mapping[str, Any], *, label: str, start: datetime, end: datetime,
) -> datetime:
    timestamp = _utc(row.get("ts"), f"{label}.ts")
    if not start <= timestamp < end:
        raise BillingEvidenceInventoryError(
            f"{label} timestamp falls outside the half-open billing window"
        )
    return timestamp


def _line_ref(source_id: str, line_number: int) -> dict[str, Any]:
    return {"source_id": source_id, "line_number": line_number}


def _source_totals(
    timestamps: Sequence[datetime], *, actual: Decimal, uncertain: Decimal,
    uncertain_refs: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "row_count": len(timestamps),
        "first_row_utc": None if not timestamps else _utc_text(min(timestamps)),
        "last_row_utc": None if not timestamps else _utc_text(max(timestamps)),
        "actual_spend_usd": _decimal_text(actual),
        "uncertain_spend_usd": _decimal_text(uncertain),
        "accounted_spend_usd": _decimal_text(_exact_sum((actual, uncertain))),
        "uncertain_line_refs": list(uncertain_refs),
    }


def _validate_ledger_row_fields(rows: Sequence[Mapping[str, Any]], label: str) -> None:
    if not rows:
        raise BillingEvidenceInventoryError(f"{label} is empty")
    _exact_keys(rows[0], _GENESIS_FIELDS, f"{label} row 1")
    if rows[0].get("status") != "ledger_genesis":
        raise BillingEvidenceInventoryError(f"{label} row 1 is not ledger genesis")
    for line_number, row in enumerate(rows[1:], 2):
        status = row.get("status")
        if not isinstance(status, str):
            raise BillingEvidenceInventoryError(
                f"{label} row {line_number} has unsupported status"
            )
        expected = _LEDGER_EVENT_FIELDS.get(status)
        if expected is None:
            raise BillingEvidenceInventoryError(
                f"{label} row {line_number} has unsupported status"
            )
        observed = set(row)
        if status in {"success", "charged_malformed"}:
            accepted = (expected, expected | frozenset({"response_metadata"}))
            if observed not in accepted:
                raise BillingEvidenceInventoryError(
                    f"{label} row {line_number} fields drifted"
                )
        else:
            _exact_keys(row, expected, f"{label} row {line_number}")


def _ledger_material(
    source_id: str, path: Path, *, start: datetime, end: datetime,
) -> dict[str, Any]:
    label = f"ledger source {source_id!r}"
    ledger_raw = _stable_bytes(path, label)
    ordinary = _jsonl_bytes(ledger_raw, label, exact_numbers=False)
    exact = _jsonl_bytes(ledger_raw, label, exact_numbers=True)
    _validate_ledger_row_fields(ordinary, label)
    if len(ordinary) != len(exact) or [row.get("event_hash") for row in ordinary] != [
        row.get("event_hash") for row in exact
    ]:
        raise BillingEvidenceInventoryError(
            f"{label} exact numeric parse differs from the validated chain"
        )
    try:
        identity, event_hashes = api_client._validate_usage_chain(ordinary, path)  # noqa: SLF001
        api_client._summarize_usage_events(  # noqa: SLF001
            ordinary[1:], path, strict_lifecycle=True
        )
    except (api_client.UsageLedgerError, KeyError, TypeError, ValueError) as exc:
        raise BillingEvidenceInventoryError(
            f"{label} failed chain or lifecycle validation"
        ) from exc

    state_path = api_client.usage_ledger_state_path(path).resolve()
    state_raw = _stable_bytes(state_path, f"{label} state")
    state = _exact_keys(
        _json_bytes(state_raw, f"{label} state"),
        billing.STATE_FIELDS,
        f"{label} state",
    )
    tail_sequence = len(event_hashes) - 1
    tail_hash = event_hashes[-1]
    try:
        billing._validate_state(  # noqa: SLF001
            state,
            path=state_path,
            identity=identity,
            tail_sequence=tail_sequence,
            tail_event_hash=tail_hash,
        )
    except billing.BillingReconciliationError as exc:
        raise BillingEvidenceInventoryError(
            f"{label} state is not at the exact immutable tail"
        ) from exc

    # A pair can be individually stable yet still straddle a concurrent append and state
    # publication. Reopen both only after acquiring and validating the complete pair.
    _require_unchanged(path, ledger_raw, label)
    _require_unchanged(state_path, state_raw, f"{label} state")

    timestamps = [
        _row_timestamp(row, label=f"{label} line {line_number}", start=start, end=end)
        for line_number, row in enumerate(ordinary[1:], 2)
    ]
    reservations: dict[str, tuple[Decimal, int]] = {}
    uncertain_refs: list[dict[str, Any]] = []
    actual_costs: list[Decimal] = []
    uncertain_costs: list[Decimal] = []
    for line_number, row in enumerate(exact[1:], 2):
        attempt_id = row.get("attempt_id")
        assert isinstance(attempt_id, str)  # strict lifecycle validation already checked it
        cost = billing._event_cost(row, f"{label} line {line_number}")  # noqa: SLF001
        status = row.get("status")
        if status == "reserved":
            reservations[attempt_id] = (cost, line_number)
        elif status in {"success", "charged_malformed"}:
            reserved, _reserved_line = reservations.pop(attempt_id)
            if cost > reserved:
                raise BillingEvidenceInventoryError(
                    f"{label} line {line_number} actual cost exceeds reserved cost"
                )
            actual_costs.append(cost)
        elif status == "unknown_charge":
            reserved, _reserved_line = reservations.pop(attempt_id)
            if cost != reserved:
                raise BillingEvidenceInventoryError(
                    f"{label} line {line_number} unknown cost differs from reservation"
                )
            uncertain_costs.append(cost)
            uncertain_refs.append(_line_ref(source_id, line_number))
        elif status == "released_no_charge":
            reservations.pop(attempt_id)
            if cost != 0:
                raise BillingEvidenceInventoryError(
                    f"{label} line {line_number} released cost must be zero"
                )
    for _attempt_id, (cost, line_number) in reservations.items():
        uncertain_costs.append(cost)
        uncertain_refs.append(_line_ref(source_id, line_number))
    uncertain_refs.sort(key=lambda item: item["line_number"])
    actual = _exact_sum(actual_costs)
    uncertain = _exact_sum(uncertain_costs)

    return {
        "source_id": source_id,
        "source_kind": LEDGER_KIND,
        "path": path.as_posix(),
        "raw_sha256": _sha256(ledger_raw),
        "state_path": state_path.as_posix(),
        "state_raw_sha256": _sha256(state_raw),
        "ledger_id": identity["ledger_id"],
        "tail_sequence": tail_sequence,
        "tail_event_hash": tail_hash,
        "_attempt_ids": tuple(sorted({
            str(row["attempt_id"]) for row in ordinary[1:]
        })),
        **_source_totals(
            timestamps,
            actual=actual,
            uncertain=uncertain,
            uncertain_refs=uncertain_refs,
        ),
    }


def _auxiliary_material(
    source_id: str, path: Path, *, start: datetime, end: datetime,
) -> dict[str, Any]:
    label = f"auxiliary source {source_id!r}"
    raw = _stable_bytes(path, label)
    rows = _jsonl_bytes(raw, label, exact_numbers=True)
    if not rows:
        raise BillingEvidenceInventoryError(f"{label} is empty")
    timestamps: list[datetime] = []
    uncertain_refs: list[dict[str, Any]] = []
    actual_costs: list[Decimal] = []
    uncertain_costs: list[Decimal] = []
    for line_number, row in enumerate(rows, 1):
        if frozenset(row) not in _AUX_ALLOWED_FIELD_SETS:
            raise BillingEvidenceInventoryError(
                f"{label} row {line_number} fields drifted"
            )
        timestamps.append(
            _row_timestamp(row, label=f"{label} line {line_number}", start=start, end=end)
        )
        if "probe_id" in row:
            _text(row["probe_id"], f"{label} line {line_number}.probe_id")
        else:
            _text(row["config"], f"{label} line {line_number}.config")
            _text(row["prompt_key"], f"{label} line {line_number}.prompt_key")
        reserved = _json_number_decimal(
            row["reserved_usd"], f"{label} line {line_number}.reserved_usd"
        )
        actual_value = row["actual_usd"]
        if "error" in row:
            if actual_value is not None:
                raise BillingEvidenceInventoryError(
                    f"{label} line {line_number} error outcome requires null actual_usd"
                )
            _non_negative_integral_lexeme(
                row["attempt"], f"{label} line {line_number}.attempt"
            )
            _text(row["error"], f"{label} line {line_number}.error")
            uncertain_costs.append(reserved)
            uncertain_refs.append(_line_ref(source_id, line_number))
        else:
            if actual_value is None:
                raise BillingEvidenceInventoryError(
                    f"{label} line {line_number} success outcome requires non-null actual_usd"
                )
            _non_negative_integral_lexeme(
                row["prompt_tokens"], f"{label} line {line_number}.prompt_tokens"
            )
            _non_negative_integral_lexeme(
                row["completion_tokens"], f"{label} line {line_number}.completion_tokens"
            )
            _text(row["finish_reason"], f"{label} line {line_number}.finish_reason")
            actual = _json_number_decimal(
                actual_value, f"{label} line {line_number}.actual_usd"
            )
            if actual > reserved:
                raise BillingEvidenceInventoryError(
                    f"{label} line {line_number} actual_usd exceeds reserved_usd"
                )
            actual_costs.append(actual)
    actual_total = _exact_sum(actual_costs)
    uncertain_total = _exact_sum(uncertain_costs)
    return {
        "source_id": source_id,
        "source_kind": AUXILIARY_KIND,
        "path": path.as_posix(),
        "raw_sha256": _sha256(raw),
        "_row_sha256s": tuple(_sha256(line) for line in raw.splitlines()),
        **_source_totals(
            timestamps,
            actual=actual_total,
            uncertain=uncertain_total,
            uncertain_refs=uncertain_refs,
        ),
    }


def _validate_refs(value: Any, label: str) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, list):
        raise BillingEvidenceInventoryError(f"{label} must be a list")
    refs: list[tuple[str, int]] = []
    for index, raw_ref in enumerate(value):
        ref = _exact_keys(raw_ref, LINE_REF_FIELDS, f"{label}[{index}]")
        refs.append((
            _source_id(ref["source_id"], f"{label}[{index}].source_id"),
            _non_negative_int(ref["line_number"], f"{label}[{index}].line_number"),
        ))
        if refs[-1][1] == 0:
            raise BillingEvidenceInventoryError(
                f"{label}[{index}].line_number must be a positive physical line number"
            )
    if refs != sorted(set(refs)):
        raise BillingEvidenceInventoryError(f"{label} must be sorted and unique")
    return tuple(refs)


def _validate_claimed_totals(value: Mapping[str, Any], label: str) -> None:
    _non_negative_int(value["row_count"], f"{label}.row_count")
    for field in ("actual_spend_usd", "uncertain_spend_usd", "accounted_spend_usd"):
        _decimal(value[field], f"{label}.{field}")
    actual = Decimal(value["actual_spend_usd"])
    uncertain = Decimal(value["uncertain_spend_usd"])
    if Decimal(value["accounted_spend_usd"]) != _exact_sum((actual, uncertain)):
        raise BillingEvidenceInventoryError(f"{label}.accounted_spend_usd arithmetic drifted")
    first = value["first_row_utc"]
    last = value["last_row_utc"]
    if (first is None) != (last is None):
        raise BillingEvidenceInventoryError(f"{label} row timestamp bounds are incomplete")
    if first is not None and _utc(first, f"{label}.first_row_utc") > _utc(
        last, f"{label}.last_row_utc"
    ):
        raise BillingEvidenceInventoryError(f"{label} row timestamp bounds are reversed")
    _validate_refs(value["uncertain_line_refs"], f"{label}.uncertain_line_refs")


def _aggregate(sources: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    row_count = sum(int(source["row_count"]) for source in sources)
    actual = _exact_sum(tuple(
        Decimal(str(source["actual_spend_usd"])) for source in sources
    ))
    uncertain = _exact_sum(tuple(
        Decimal(str(source["uncertain_spend_usd"])) for source in sources
    ))
    first_values = [
        _utc(source["first_row_utc"], "source.first_row_utc")
        for source in sources
        if source["first_row_utc"] is not None
    ]
    last_values = [
        _utc(source["last_row_utc"], "source.last_row_utc")
        for source in sources
        if source["last_row_utc"] is not None
    ]
    refs = [
        dict(ref)
        for source in sources
        for ref in source["uncertain_line_refs"]
    ]
    refs.sort(key=lambda item: (item["source_id"], item["line_number"]))
    return {
        "row_count": row_count,
        "first_row_utc": None if not first_values else _utc_text(min(first_values)),
        "last_row_utc": None if not last_values else _utc_text(max(last_values)),
        "actual_spend_usd": _decimal_text(actual),
        "uncertain_spend_usd": _decimal_text(uncertain),
        "accounted_spend_usd": _decimal_text(_exact_sum((actual, uncertain))),
        "uncertain_line_refs": refs,
    }


def _check_unique_material(sources: Sequence[Mapping[str, Any]]) -> None:
    ids = [str(source["source_id"]) for source in sources]
    if len(ids) != len(set(ids)):
        raise BillingEvidenceInventoryError("source IDs must be unique across all source kinds")
    paths: list[str] = []
    hashes: list[str] = []
    ledger_ids: list[str] = []
    attempt_owner: dict[str, str] = {}
    auxiliary_rows: list[tuple[str, str]] = []
    for source in sources:
        paths.append(str(source["path"]))
        hashes.append(str(source["raw_sha256"]))
        if source["source_kind"] == LEDGER_KIND:
            paths.append(str(source["state_path"]))
            hashes.append(str(source["state_raw_sha256"]))
            ledger_ids.append(str(source["ledger_id"]))
            attempt_ids = source.get("_attempt_ids")
            if not isinstance(attempt_ids, tuple):
                raise BillingEvidenceInventoryError(
                    "validated ledger material omits its internal attempt identities"
                )
            for attempt_id in attempt_ids:
                if not isinstance(attempt_id, str) or not attempt_id:
                    raise BillingEvidenceInventoryError(
                        "validated ledger material has an invalid attempt identity"
                    )
                previous = attempt_owner.get(attempt_id)
                if previous is not None:
                    raise BillingEvidenceInventoryError(
                        "attempt IDs must be unique across immutable ledger sources: "
                        f"{attempt_id!r} appears in {previous!r} and "
                        f"{source['source_id']!r}"
                    )
                attempt_owner[attempt_id] = str(source["source_id"])
        elif source["source_kind"] == AUXILIARY_KIND:
            row_sha256s = source.get("_row_sha256s")
            if (
                not isinstance(row_sha256s, tuple)
                or len(row_sha256s) != source["row_count"]
                or any(not isinstance(digest, str) for digest in row_sha256s)
            ):
                raise BillingEvidenceInventoryError(
                    "validated auxiliary material omits its internal raw row digests"
                )
            auxiliary_rows.extend(
                (digest, str(source["source_id"])) for digest in row_sha256s
            )
    if len(paths) != len(set(paths)):
        raise BillingEvidenceInventoryError("artifact paths must be unique across all source kinds")
    if len(hashes) != len(set(hashes)):
        raise BillingEvidenceInventoryError("raw hashes must be unique across all source kinds")
    if len(ledger_ids) != len(set(ledger_ids)):
        raise BillingEvidenceInventoryError(
            "immutable ledger IDs must be unique across ledger sources"
        )
    row_owner: dict[str, str] = {}
    for digest, source_id in auxiliary_rows:
        previous = row_owner.get(digest)
        if previous is not None and previous != source_id:
            raise BillingEvidenceInventoryError(
                "exact raw auxiliary rows must be unique across auxiliary sources: "
                f"one row appears in {previous!r} and {source_id!r}"
            )
        row_owner[digest] = source_id


def _public_source(source: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in source.items() if not key.startswith("_")}


def build_inventory(
    *,
    window_start_utc: str,
    window_end_utc: str,
    ledger_sources: Sequence[tuple[str, str | Path]] = (),
    auxiliary_sources: Sequence[tuple[str, str | Path]] = (),
    project_root: str | Path = ".",
) -> dict[str, Any]:
    """Build an inventory only from the exact source tuples supplied by the caller."""
    root = Path(project_root).resolve()
    window, start, end = _window({
        "window_start_utc": window_start_utc,
        "window_end_utc": window_end_utc,
    })
    specs: list[tuple[str, str, Path]] = []
    for kind, supplied in ((LEDGER_KIND, ledger_sources), (AUXILIARY_KIND, auxiliary_sources)):
        for index, item in enumerate(supplied):
            if not isinstance(item, tuple) or len(item) != 2:
                raise BillingEvidenceInventoryError(
                    f"{kind} source {index} must be an exact (source_id, path) tuple"
                )
            raw_id, raw_path = item
            source_id = _source_id(raw_id, f"{kind} source {index} ID")
            specs.append((source_id, kind, _source_path(
                raw_path, project_root=root, label=f"{kind} source {source_id!r} path"
            )))
    if not specs:
        raise BillingEvidenceInventoryError("at least one explicit local source is required")
    specs.sort(key=lambda item: item[0])
    if len({source_id for source_id, _kind, _path in specs}) != len(specs):
        raise BillingEvidenceInventoryError("source IDs must be unique across all source kinds")

    material_sources = [
        (
            _ledger_material(source_id, path, start=start, end=end)
            if kind == LEDGER_KIND
            else _auxiliary_material(source_id, path, start=start, end=end)
        )
        for source_id, kind, path in specs
    ]
    _check_unique_material(material_sources)
    sources = [_public_source(source) for source in material_sources]
    record = {
        "schema_version": SCHEMA_VERSION,
        "stage": "main",
        "provider": PROVIDER,
        "evidence_only": True,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
        "selection_basis": SELECTION_BASIS,
        "authoritative_completeness": AUTHORITATIVE_COMPLETENESS,
        "billing_window": window,
        "source_count": len(sources),
        "sources": sources,
        "totals": _aggregate(sources),
    }
    validate_inventory(record, project_root=root)
    return record


def validate_inventory(
    record: Mapping[str, Any], *, project_root: str | Path = ".",
) -> dict[str, Any]:
    """Reopen every explicit source and validate all inventory claims without mutation."""
    root = Path(project_root).resolve()
    _exact_keys(record, INVENTORY_FIELDS, "inventory")
    if (
        record["schema_version"] != SCHEMA_VERSION
        or record["stage"] != "main"
        or record["provider"] != PROVIDER
    ):
        raise BillingEvidenceInventoryError("inventory schema, stage, or provider drifted")
    if record["evidence_only"] is not True or any(
        record[field] is not False
        for field in (
            "execution_authorized",
            "provider_calls_authorized",
            "main_run_spend_authorized",
        )
    ):
        raise BillingEvidenceInventoryError("inventory must remain strictly non-authorizing")
    if record["selection_basis"] != SELECTION_BASIS:
        raise BillingEvidenceInventoryError(
            f"selection_basis must be exactly {SELECTION_BASIS!r}"
        )
    if record["authoritative_completeness"] != AUTHORITATIVE_COMPLETENESS:
        raise BillingEvidenceInventoryError(
            "authoritative completeness must remain explicitly not established"
        )
    canonical_window, start, end = _window(record["billing_window"])
    if dict(record["billing_window"]) != canonical_window:
        raise BillingEvidenceInventoryError("billing_window timestamps must be canonical UTC")

    raw_sources = record["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise BillingEvidenceInventoryError("sources must be a non-empty list")
    source_count = _non_negative_int(record["source_count"], "source_count")
    if source_count != len(raw_sources):
        raise BillingEvidenceInventoryError("source_count does not match sources")
    observed_ids: list[str] = []
    for index, source in enumerate(raw_sources):
        source_mapping = dict(source) if isinstance(source, Mapping) else {}
        observed_ids.append(_source_id(
            source_mapping.get("source_id"), f"sources[{index}].source_id"
        ))
    if observed_ids != sorted(observed_ids):
        raise BillingEvidenceInventoryError("sources must be sorted by source ID")
    if len(observed_ids) != len(set(observed_ids)):
        raise BillingEvidenceInventoryError("source IDs must be unique across all source kinds")

    recomputed: list[dict[str, Any]] = []
    for index, raw_source in enumerate(raw_sources):
        if not isinstance(raw_source, Mapping):
            raise BillingEvidenceInventoryError(f"sources[{index}] must be an object")
        raw_source_mapping = dict(raw_source)
        kind = raw_source_mapping.get("source_kind")
        expected_fields = (
            LEDGER_SOURCE_FIELDS if kind == LEDGER_KIND
            else AUXILIARY_SOURCE_FIELDS if kind == AUXILIARY_KIND
            else None
        )
        if expected_fields is None:
            raise BillingEvidenceInventoryError(f"sources[{index}].source_kind is unsupported")
        source = _exact_keys(raw_source_mapping, expected_fields, f"sources[{index}]")
        _validate_claimed_totals(source, f"sources[{index}]")
        _sha256_text(source["raw_sha256"], f"sources[{index}].raw_sha256")
        path = _source_path(
            source["path"], project_root=root, label=f"sources[{index}].path"
        )
        source_id = observed_ids[index]
        if kind == LEDGER_KIND:
            _sha256_text(
                source["state_raw_sha256"], f"sources[{index}].state_raw_sha256"
            )
            _non_negative_int(source["tail_sequence"], f"sources[{index}].tail_sequence")
            _sha256_text(source["tail_event_hash"], f"sources[{index}].tail_event_hash")
            expected = _ledger_material(source_id, path, start=start, end=end)
        else:
            expected = _auxiliary_material(source_id, path, start=start, end=end)
        if dict(source) != _public_source(expected):
            raise BillingEvidenceInventoryError(
                f"sources[{index}] claims do not match the explicitly bound file"
            )
        recomputed.append(expected)
    _check_unique_material(recomputed)

    totals = _exact_keys(record["totals"], TOTAL_FIELDS, "totals")
    _validate_claimed_totals(totals, "totals")
    expected_totals = _aggregate(recomputed)
    if dict(totals) != expected_totals:
        raise BillingEvidenceInventoryError("totals do not match the explicit source union")
    return {
        "source_count": source_count,
        "row_count": expected_totals["row_count"],
        "actual_spend_usd": expected_totals["actual_spend_usd"],
        "uncertain_spend_usd": expected_totals["uncertain_spend_usd"],
        "accounted_spend_usd": expected_totals["accounted_spend_usd"],
        "authoritative_completeness": AUTHORITATIVE_COMPLETENESS,
        "execution_authorized": False,
    }


def load_and_validate_inventory(
    path: str | Path, *, project_root: str | Path = ".",
) -> dict[str, Any]:
    """Load one strict inventory JSON file and reopen all explicitly named sources."""
    inventory_path = _source_path(
        path,
        project_root=Path(project_root).resolve(),
        label="inventory path",
    )
    raw = _stable_bytes(inventory_path, "billing evidence inventory")
    payload = _json_bytes(raw, "billing evidence inventory")
    if not isinstance(payload, Mapping):
        raise BillingEvidenceInventoryError("billing evidence inventory must be an object")
    return validate_inventory(payload, project_root=project_root)


def write_inventory_exclusive(path: str | Path, record: Mapping[str, Any]) -> None:
    """Publish by same-filesystem hard link, failing closed where unsupported."""
    output = Path(path).resolve()
    validate_inventory(record, project_root=output.parent)
    try:
        raw = (
            json.dumps(
                dict(record),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:  # defensive after strict record validation
        raise BillingEvidenceInventoryError(
            "billing evidence inventory is not strict JSON"
        ) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor, raw_temp = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
    except OSError as exc:
        raise BillingEvidenceInventoryError(
            f"could not create inventory publication temp: {output.parent}"
        ) from exc
    temp = Path(raw_temp)
    try:
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise BillingEvidenceInventoryError(
                f"could not fully fsync inventory publication temp: {temp}"
            ) from exc
        try:
            os.link(temp, output)
        except FileExistsError as exc:
            raise BillingEvidenceInventoryError(
                f"refusing to overwrite existing inventory: {output}"
            ) from exc
        except OSError as exc:
            raise BillingEvidenceInventoryError(
                "could not atomically publish inventory; same-filesystem hard-link "
                f"support is required: {output}"
            ) from exc
        if os.name != "nt":
            try:
                parent_descriptor = os.open(output.parent, os.O_RDONLY)
                try:
                    os.fsync(parent_descriptor)
                finally:
                    os.close(parent_descriptor)
            except OSError as exc:
                raise BillingEvidenceInventoryError(
                    f"could not durably publish inventory: {output}"
                ) from exc
        try:
            published = output.read_bytes()
        except OSError as exc:
            raise BillingEvidenceInventoryError(
                f"could not reopen published inventory: {output}"
            ) from exc
        if published != raw:
            raise BillingEvidenceInventoryError(
                f"published inventory bytes differ from the complete payload: {output}"
            )
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "AUTHORITATIVE_COMPLETENESS",
    "AUXILIARY_KIND",
    "BillingEvidenceInventoryError",
    "LEDGER_KIND",
    "PROVIDER",
    "SCHEMA_VERSION",
    "SELECTION_BASIS",
    "build_inventory",
    "load_and_validate_inventory",
    "validate_inventory",
    "write_inventory_exclusive",
]
