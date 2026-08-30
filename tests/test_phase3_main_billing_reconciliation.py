"""Focused tests for the read-only Phase 3 main billing reconciliation."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from rejudge import api_client
from rejudge import phase3_main_billing_reconciliation as billing


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_bound_json(
    record: dict[str, Any], binding_name: str, payload: dict[str, Any]
) -> None:
    binding = record[binding_name]
    path = Path(binding["path"])
    path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    binding["raw_sha256"] = _sha(path)


def _usage_fields(*, cost: float, attempt_id: str) -> dict[str, Any]:
    return {
        "attempt_id": attempt_id,
        "model": "model/test",
        "kind": "judge",
        "seed": 7,
        "attempt": 1,
        "prompt_tokens": None,
        "completion_tokens": None,
        "reserved_prompt_tokens": 10,
        "reserved_completion_tokens": 10,
        "estimated_tokens": 20,
        "cost_usd": cost,
        "metadata": {"cell": attempt_id},
    }


def _write_ledger(
    path: Path, *, ledger_id: str, events: list[dict[str, Any]],
    actual: str, uncertain: str, unresolved: list[str],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = [
        {
            "status": "ledger_genesis",
            "schema_version": api_client.USAGE_LEDGER_SCHEMA_VERSION,
            "ledger_id": ledger_id,
            "sequence": 0,
            "prev_event_hash": None,
            "ts": "2026-08-29T19:00:00+00:00",
        }
    ]
    rows[0]["event_hash"] = api_client._usage_event_hash(rows[0])
    for event in events:
        chained = {
            "ts": "2026-08-29T19:01:00+00:00",
            **event,
            "ledger_id": ledger_id,
            "sequence": len(rows),
            "prev_event_hash": rows[-1]["event_hash"],
        }
        chained["event_hash"] = api_client._usage_event_hash(chained)
        rows.append(chained)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    identity = api_client._ledger_identity(path, ledger_id)
    state_path = api_client.usage_ledger_state_path(path)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": api_client.USAGE_LEDGER_SCHEMA_VERSION,
                "ledger_id": ledger_id,
                "last_sequence": len(rows) - 1,
                "last_event_hash": rows[-1]["event_hash"],
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    accounted = Decimal(actual) + Decimal(uncertain)
    return {
        "path": str(path.resolve()),
        "raw_sha256": _sha(path),
        "state_raw_sha256": _sha(state_path),
        "identity": identity,
        "tail_sequence": len(rows) - 1,
        "tail_event_hash": rows[-1]["event_hash"],
        "actual_spend_usd": actual,
        "uncertain_spend_usd": uncertain,
        "accounted_spend_usd": format(accounted, "f"),
        "unresolved_attempt_ids": unresolved,
    }


def _settled_ledger(tmp_path: Path) -> dict[str, Any]:
    reserved = {"status": "reserved", **_usage_fields(cost=0.12, attempt_id="a-settled")}
    success = {
        "status": "success",
        **_usage_fields(cost=0.10, attempt_id="a-settled"),
        "prompt_tokens": 8,
        "completion_tokens": 9,
    }
    return _write_ledger(
        tmp_path / "ledger-a.jsonl",
        ledger_id="ledger-a",
        events=[reserved, success],
        actual="0.10",
        uncertain="0",
        unresolved=[],
    )


def _unresolved_ledger(tmp_path: Path) -> dict[str, Any]:
    reserved = {"status": "reserved", **_usage_fields(cost=0.04, attempt_id="a-unknown")}
    unknown = {
        "status": "unknown_charge",
        **_usage_fields(cost=0.04, attempt_id="a-unknown"),
        "error": "transport outcome unknown",
    }
    return _write_ledger(
        tmp_path / "ledger-b.jsonl",
        ledger_id="ledger-b",
        events=[reserved, unknown],
        actual="0",
        uncertain="0.04",
        unresolved=["a-unknown"],
    )


def _unmatched_reservation_ledger(tmp_path: Path) -> dict[str, Any]:
    reserved = {"status": "reserved", **_usage_fields(cost=0.03, attempt_id="a-inflight")}
    return _write_ledger(
        tmp_path / "ledger-c.jsonl",
        ledger_id="ledger-c",
        events=[reserved],
        actual="0",
        uncertain="0.03",
        unresolved=["a-inflight"],
    )


def _record(
    tmp_path: Path, ledgers: list[dict[str, Any]], *, direct_delta: bool = False,
    provider_delta: str = "0.10", tolerance: str = "0.01",
) -> dict[str, Any]:
    evidence = tmp_path / "together-dashboard-evidence.txt"
    coverage = tmp_path / "ledger-coverage.json"
    actual = sum((Decimal(item["actual_spend_usd"]) for item in ledgers), Decimal("0"))
    uncertain = sum(
        (Decimal(item["uncertain_spend_usd"]) for item in ledgers), Decimal("0")
    )
    unresolved = sorted(
        attempt_id
        for item in ledgers
        for attempt_id in item["unresolved_attempt_ids"]
    )
    delta = Decimal(provider_delta)
    discrepancy = delta - actual
    absolute = abs(discrepancy)
    discrepant = absolute > Decimal(tolerance)
    accounted = actual + uncertain
    within_envelope = actual <= delta <= accounted
    if unresolved and within_envelope:
        disposition = billing.DISPOSITION_CLOSED_CONSERVATIVE
    elif unresolved and discrepant:
        disposition = billing.DISPOSITION_OPEN_BOTH
    elif unresolved:
        disposition = billing.DISPOSITION_OPEN_UNRESOLVED
    elif discrepant:
        disposition = billing.DISPOSITION_OPEN_DISCREPANCY
    else:
        disposition = billing.DISPOSITION_CLOSED
    dashboard = (
        {
            "mode": billing.DASHBOARD_MODE_DIRECT_DELTA,
            "before_total_usd": None,
            "after_total_usd": None,
            "reported_delta_usd": provider_delta,
        }
        if direct_delta
        else {
            "mode": billing.DASHBOARD_MODE_BEFORE_AFTER,
            "before_total_usd": "100.00",
            "after_total_usd": format(Decimal("100.00") + delta, "f"),
            "reported_delta_usd": None,
        }
    )
    billing_scope = {
        "account_identity_sha256": "a" * 64,
        "window_start_utc": "2026-08-29T18:00:00Z",
        "window_end_utc": "2026-08-29T20:00:00Z",
    }
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        json.dumps({
            "schema_version": billing.BILLING_EVIDENCE_SCHEMA,
            "provider": billing.PROVIDER,
            "billing_scope": billing_scope,
            "currency": "USD",
            "observed_at_utc": "2026-08-29T20:00:00Z",
            "capture_method": billing.BILLING_EVIDENCE_CAPTURE_METHOD,
            "dashboard": dashboard,
        }, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    coverage.write_text(
        json.dumps(
            {
                "schema_version": billing.LEDGER_COVERAGE_SCHEMA,
                "stage": "main",
                "provider": billing.PROVIDER,
                "execution_authorized": False,
                "provider_calls_authorized": False,
                "main_run_spend_authorized": False,
                "billing_scope": billing_scope,
                "ledger_count": len(ledgers),
                "ledgers": sorted(
                    (
                        {
                            "ledger_id": item["identity"]["ledger_id"],
                            "raw_sha256": item["raw_sha256"],
                        }
                        for item in ledgers
                    ),
                    key=lambda item: (item["ledger_id"], item["raw_sha256"]),
                ),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "schema_version": billing.SCHEMA_VERSION,
        "stage": "main",
        "run_id": "phase3-main-test",
        "recorded_at_utc": "2026-08-29T20:01:00Z",
        "provider": billing.PROVIDER,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
        "billing_scope": billing_scope,
        "provider_evidence": {
            "observed_at_utc": "2026-08-29T20:00:00Z",
            "path": str(evidence.resolve()),
            "raw_sha256": _sha(evidence),
        },
        "ledger_coverage": {
            "path": str(coverage.resolve()),
            "raw_sha256": _sha(coverage),
        },
        "dashboard": dashboard,
        "ledgers": ledgers,
        "ledger_totals": {
            "actual_spend_usd": format(actual, "f"),
            "uncertain_spend_usd": format(uncertain, "f"),
            "accounted_spend_usd": format(accounted, "f"),
            "unresolved_attempt_ids": unresolved,
        },
        "reconciliation": {
            "provider_delta_usd": provider_delta,
            "discrepancy_usd": format(discrepancy, "f"),
            "absolute_discrepancy_usd": format(absolute, "f"),
            "within_conservative_envelope": within_envelope,
            "tolerance_usd": tolerance,
            "disposition": disposition,
        },
    }


@pytest.mark.parametrize("direct_delta", [False, True])
def test_valid_reconciliation_recomputes_dashboard_ledger_and_disposition(
    tmp_path: Path, direct_delta: bool,
) -> None:
    record = _record(tmp_path, [_settled_ledger(tmp_path)], direct_delta=direct_delta)

    validated = billing.validate_billing_reconciliation(record, project_root=tmp_path)

    assert validated["actual_spend_usd"] == "0.10"
    assert validated["accounted_spend_usd"] == "0.10"
    assert validated["provider_delta_usd"] == "0.10"
    assert validated["unresolved_attempt_ids"] == ()
    assert validated["disposition"] == billing.DISPOSITION_CLOSED


def test_record_is_strict_and_cannot_authorize_or_use_json_numbers(tmp_path: Path) -> None:
    record = _record(tmp_path, [_settled_ledger(tmp_path)])
    extra = deepcopy(record)
    extra["note"] = "not part of the schema"
    with pytest.raises(billing.BillingReconciliationError, match="fields drifted"):
        billing.validate_billing_reconciliation(extra, project_root=tmp_path)

    authorized = deepcopy(record)
    authorized["main_run_spend_authorized"] = True
    with pytest.raises(billing.BillingReconciliationError, match="must not authorize"):
        billing.validate_billing_reconciliation(authorized, project_root=tmp_path)

    floating = deepcopy(record)
    floating["reconciliation"]["provider_delta_usd"] = 0.10
    with pytest.raises(billing.BillingReconciliationError, match="decimal string"):
        billing.validate_billing_reconciliation(floating, project_root=tmp_path)


def test_provider_evidence_and_ledger_bytes_are_raw_hash_bound(tmp_path: Path) -> None:
    record = _record(tmp_path, [_settled_ledger(tmp_path)])
    evidence = Path(record["provider_evidence"]["path"])
    evidence.write_text("changed dashboard evidence\n", encoding="utf-8")
    with pytest.raises(billing.BillingReconciliationError, match="provider evidence raw"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)

    record = _record(tmp_path / "fresh", [_settled_ledger(tmp_path / "fresh")])
    ledger = Path(record["ledgers"][0]["path"])
    ledger.write_bytes(ledger.read_bytes() + b"\n")
    with pytest.raises(billing.BillingReconciliationError, match="ledger 0 raw"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)

    record = _record(tmp_path / "coverage", [_settled_ledger(tmp_path / "coverage")])
    coverage = Path(record["ledger_coverage"]["path"])
    coverage.write_bytes(coverage.read_bytes() + b"\n")
    with pytest.raises(billing.BillingReconciliationError, match="coverage raw"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)


def test_bound_coverage_rejects_omitted_reconciliation_ledger(tmp_path: Path) -> None:
    record = _record(
        tmp_path, [_settled_ledger(tmp_path), _unresolved_ledger(tmp_path)]
    )
    omitted = deepcopy(record)
    omitted["ledgers"] = omitted["ledgers"][:1]

    with pytest.raises(billing.BillingReconciliationError, match="coverage inventory"):
        billing.validate_billing_reconciliation(omitted, project_root=tmp_path)


def test_coverage_rejects_duplicate_ledger_inventory_entry(tmp_path: Path) -> None:
    record = _record(tmp_path, [_settled_ledger(tmp_path)])
    coverage_path = Path(record["ledger_coverage"]["path"])
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["ledgers"].append(deepcopy(coverage["ledgers"][0]))
    coverage["ledger_count"] = 2
    _rewrite_bound_json(record, "ledger_coverage", coverage)

    with pytest.raises(billing.BillingReconciliationError, match="repeats a ledger ID"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)


def test_coverage_count_and_non_authorizing_fields_are_strict(tmp_path: Path) -> None:
    record = _record(tmp_path / "count", [_settled_ledger(tmp_path / "count")])
    coverage_path = Path(record["ledger_coverage"]["path"])
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["ledger_count"] = 2
    _rewrite_bound_json(record, "ledger_coverage", coverage)
    with pytest.raises(billing.BillingReconciliationError, match="ledger_count"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)

    record = _record(tmp_path / "authority", [_settled_ledger(tmp_path / "authority")])
    coverage_path = Path(record["ledger_coverage"]["path"])
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["provider_calls_authorized"] = True
    _rewrite_bound_json(record, "ledger_coverage", coverage)
    with pytest.raises(billing.BillingReconciliationError, match="must not authorize"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)


def test_account_and_window_scope_match_all_three_bound_records(tmp_path: Path) -> None:
    record = _record(tmp_path / "account", [_settled_ledger(tmp_path / "account")])
    evidence_path = Path(record["provider_evidence"]["path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["billing_scope"]["account_identity_sha256"] = "b" * 64
    _rewrite_bound_json(record, "provider_evidence", evidence)
    with pytest.raises(billing.BillingReconciliationError, match="provider evidence billing scope"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)

    record = _record(tmp_path / "window", [_settled_ledger(tmp_path / "window")])
    coverage_path = Path(record["ledger_coverage"]["path"])
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["billing_scope"]["window_start_utc"] = "2026-08-29T18:01:00Z"
    _rewrite_bound_json(record, "ledger_coverage", coverage)
    with pytest.raises(billing.BillingReconciliationError, match="coverage billing scope"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)


def test_successful_validation_does_not_mutate_bound_artifacts(tmp_path: Path) -> None:
    record = _record(tmp_path, [_settled_ledger(tmp_path)])
    entry = record["ledgers"][0]
    paths = (
        Path(record["provider_evidence"]["path"]),
        Path(record["ledger_coverage"]["path"]),
        Path(entry["path"]),
        Path(entry["identity"]["state_path"]),
    )
    before = {path: path.read_bytes() for path in paths}

    billing.validate_billing_reconciliation(record, project_root=tmp_path)

    assert {path: path.read_bytes() for path in paths} == before


def test_identity_tail_and_state_are_verified_without_repair(tmp_path: Path) -> None:
    record = _record(tmp_path, [_settled_ledger(tmp_path)])
    wrong_identity = deepcopy(record)
    wrong_identity["ledgers"][0]["identity"]["ledger_id"] = "replacement-ledger"
    with pytest.raises(billing.BillingReconciliationError, match="ledger genesis"):
        billing.validate_billing_reconciliation(wrong_identity, project_root=tmp_path)

    entry = record["ledgers"][0]
    state_path = Path(entry["identity"]["state_path"])
    ledger_rows = [
        json.loads(line)
        for line in Path(entry["path"]).read_text(encoding="utf-8").splitlines()
        if line
    ]
    state_path.write_text(
        json.dumps(
            {
                "schema_version": api_client.USAGE_LEDGER_SCHEMA_VERSION,
                "ledger_id": entry["identity"]["ledger_id"],
                "last_sequence": 0,
                "last_event_hash": ledger_rows[0]["event_hash"],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    entry["state_raw_sha256"] = _sha(state_path)
    before = state_path.read_bytes()
    with pytest.raises(billing.BillingReconciliationError, match="immutable ledger tail"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)
    assert state_path.read_bytes() == before


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("ledger", "actual_spend_usd", "0.11", "recomputed ledger total"),
        ("totals", "accounted_spend_usd", "0.11", "sum of bound ledgers"),
        ("reconciliation", "provider_delta_usd", "0.11", "dashboard evidence"),
        ("reconciliation", "discrepancy_usd", "0.01", "provider delta minus"),
        ("reconciliation", "absolute_discrepancy_usd", "0.01", "arithmetic drifted"),
        ("reconciliation", "within_conservative_envelope", False, "envelope arithmetic"),
        ("reconciliation", "disposition", billing.DISPOSITION_OPEN_DISCREPANCY,
         "disposition must be"),
    ],
)
def test_all_claimed_arithmetic_and_disposition_are_recomputed(
    tmp_path: Path, section: str, field: str, value: Any, message: str,
) -> None:
    record = _record(tmp_path, [_settled_ledger(tmp_path)])
    if section == "ledger":
        record["ledgers"][0][field] = value
    elif section == "totals":
        record["ledger_totals"][field] = value
    else:
        record["reconciliation"][field] = value

    with pytest.raises(billing.BillingReconciliationError, match=message):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)


def test_uncertain_spend_closes_only_inside_conservative_envelope(tmp_path: Path) -> None:
    ledgers = [_settled_ledger(tmp_path), _unresolved_ledger(tmp_path)]
    record = _record(tmp_path, ledgers, provider_delta="0.12")

    validated = billing.validate_billing_reconciliation(record, project_root=tmp_path)

    assert validated["uncertain_spend_usd"] == "0.04"
    assert validated["accounted_spend_usd"] == "0.14"
    assert validated["prior_spend_upper_bound_usd"] == "0.14"
    assert validated["unresolved_attempt_ids"] == ("a-unknown",)
    assert validated["within_conservative_envelope"] is True
    assert validated["closed"] is True
    assert validated["disposition"] == billing.DISPOSITION_CLOSED_CONSERVATIVE

    omitted = deepcopy(record)
    omitted["ledgers"][1]["unresolved_attempt_ids"] = []
    with pytest.raises(billing.BillingReconciliationError, match="ledger lifecycle"):
        billing.validate_billing_reconciliation(omitted, project_root=tmp_path)


@pytest.mark.parametrize(
    ("provider_delta", "expected_disposition"),
    [
        ("0.09", billing.DISPOSITION_OPEN_UNRESOLVED),
        ("0.15", billing.DISPOSITION_OPEN_BOTH),
    ],
)
def test_uncertain_spend_remains_open_outside_conservative_envelope(
    tmp_path: Path, provider_delta: str, expected_disposition: str
) -> None:
    record = _record(
        tmp_path,
        [_settled_ledger(tmp_path), _unresolved_ledger(tmp_path)],
        provider_delta=provider_delta,
    )

    validated = billing.validate_billing_reconciliation(record, project_root=tmp_path)

    assert validated["within_conservative_envelope"] is False
    assert validated["closed"] is False
    assert validated["unresolved_attempt_ids"] == ("a-unknown",)
    assert validated["disposition"] == expected_disposition

    forged = deepcopy(record)
    forged["reconciliation"]["disposition"] = billing.DISPOSITION_CLOSED_CONSERVATIVE
    with pytest.raises(billing.BillingReconciliationError, match="disposition must be"):
        billing.validate_billing_reconciliation(forged, project_root=tmp_path)


def test_unmatched_reservation_is_conservatively_unresolved(tmp_path: Path) -> None:
    record = _record(
        tmp_path, [_settled_ledger(tmp_path), _unmatched_reservation_ledger(tmp_path)]
    )

    validated = billing.validate_billing_reconciliation(record, project_root=tmp_path)

    assert validated["uncertain_spend_usd"] == "0.03"
    assert validated["unresolved_attempt_ids"] == ("a-inflight",)
    assert validated["disposition"] == billing.DISPOSITION_CLOSED_CONSERVATIVE


@pytest.mark.parametrize(
    ("include_unresolved", "provider_delta", "expected"),
    [
        (False, "0.12", billing.DISPOSITION_OPEN_DISCREPANCY),
        (True, "0.15", billing.DISPOSITION_OPEN_BOTH),
    ],
)
def test_tolerance_and_unresolved_state_determine_open_disposition(
    tmp_path: Path, include_unresolved: bool, provider_delta: str, expected: str,
) -> None:
    ledgers = [_settled_ledger(tmp_path)]
    if include_unresolved:
        ledgers.append(_unresolved_ledger(tmp_path))
    record = _record(
        tmp_path, ledgers, provider_delta=provider_delta, tolerance="0.01"
    )

    validated = billing.validate_billing_reconciliation(record, project_root=tmp_path)

    assert validated["disposition"] == expected


def test_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":"phase3_main_billing_reconciliation_v1",'
        '"schema_version":"duplicate"}\n',
        encoding="utf-8",
    )
    with pytest.raises(billing.BillingReconciliationError, match="repeats key"):
        billing.load_and_validate_billing_reconciliation(path, project_root=tmp_path)


def test_provider_evidence_is_parsed_and_must_be_fresh(tmp_path: Path) -> None:
    record = _record(tmp_path, [_settled_ledger(tmp_path)])
    changed_dashboard = deepcopy(record)
    changed_dashboard["dashboard"]["after_total_usd"] = "999.00"
    with pytest.raises(billing.BillingReconciliationError, match="machine-readable"):
        billing.validate_billing_reconciliation(changed_dashboard, project_root=tmp_path)

    with pytest.raises(billing.BillingReconciliationError, match="stale"):
        billing.validate_billing_reconciliation(
            record,
            project_root=tmp_path,
            as_of=datetime(2026, 8, 29, 22, 0, tzinfo=timezone.utc),
        )
    validated = billing.validate_billing_reconciliation(
        record,
        project_root=tmp_path,
        as_of=datetime(2026, 8, 29, 20, 30, tzinfo=timezone.utc),
    )
    assert validated["disposition"] == billing.DISPOSITION_CLOSED


def test_reconciliation_tolerance_is_frozen(tmp_path: Path) -> None:
    record = _record(
        tmp_path,
        [_settled_ledger(tmp_path)],
        provider_delta="100.00",
        tolerance="1000.00",
    )
    with pytest.raises(billing.BillingReconciliationError, match="tolerance must remain"):
        billing.validate_billing_reconciliation(record, project_root=tmp_path)
