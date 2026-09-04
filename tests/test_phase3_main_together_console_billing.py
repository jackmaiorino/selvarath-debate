"""Focused tests for offline Together Cost Analytics and invoice evidence."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_main_together_console_billing as console_billing


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _record(tmp_path: Path) -> dict[str, Any]:
    api_key_id = "key-test"
    project_id = "project-test"
    organization_id = "organization-test"
    api_hash = hashlib.sha256(api_key_id.encode()).hexdigest()
    project_hash = hashlib.sha256(project_id.encode()).hexdigest()
    organization_hash = hashlib.sha256(organization_id.encode()).hexdigest()
    account_hash = "a" * 64

    ratification_path = tmp_path / "ratification.json"
    _write_json(ratification_path, {
        "schema_version": console_billing.OWNER_RATIFICATION_SCHEMA_VERSION,
        "authority_limits": {
            "reviewer_dispatch_authorized": False,
            "provider_calls_authorized": False,
            "main_run_authorized": False,
            "spend_authorized": False,
        },
        "together_account_scope": {
            "account_identity_sha256": account_hash,
            "api_key_id_sha256": api_hash,
            "project_id_sha256": project_hash,
            "organization_id_sha256": organization_hash,
        },
    })

    support_path = tmp_path / "support.json"
    _write_json(support_path, {
        "schema_version": console_billing.SUPPORT_SCHEMA_VERSION,
        "provider": console_billing.PROVIDER,
        "endpoint_status": "billing_usage_api_not_individually_enableable",
        "supported_reconciliation_route": console_billing.SUPPORTED_ROUTE,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
    })

    csv_path = tmp_path / "cost-analytics.csv"
    csv_path.write_text(
        "Date,Line Item,Dimensions,Quantity,Unit Price (USD),Amount (USD)\n"
        f'Aug 18 2026,{console_billing.LINE_ITEM},'
        f'"api_key_id:{api_key_id},endpoint_name:model/a,is_lora:false,'
        f'is_reserved_ptu:false,project_id:{project_id}",1.25,$2.00,$2.50\n'
        f'Aug 19 2026,{console_billing.LINE_ITEM},'
        f'"endpoint_name:model/a,is_lora:false,is_reserved_ptu:false,'
        f'project_id:{project_id}",2.00,$1.00,$2.00\n',
        encoding="utf-8",
    )
    invoice_path = tmp_path / "invoice.pdf"
    invoice_path.write_bytes(b"synthetic invoice bytes")

    scope = {
        "account_identity_sha256": account_hash,
        "window_start_utc": "2026-08-18T00:00:00Z",
        "window_end_utc": "2026-08-20T00:00:00Z",
    }
    return {
        "schema_version": console_billing.SCHEMA_VERSION,
        "provider": console_billing.PROVIDER,
        "currency": console_billing.CURRENCY,
        "capture_method": console_billing.CAPTURE_METHOD,
        "observed_at_utc": "2026-09-03T12:00:00Z",
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
        "billing_scope": scope,
        "account_binding": {
            "owner_ratification": {
                "path": ratification_path.as_posix(),
                "raw_sha256": _sha(ratification_path),
            },
            "api_key_id_sha256": api_hash,
            "project_id_sha256": project_hash,
            "organization_id_sha256": organization_hash,
        },
        "support_disposition": {
            "path": support_path.as_posix(),
            "raw_sha256": _sha(support_path),
            "endpoint_status": "billing_usage_api_not_individually_enableable",
            "supported_reconciliation_route": console_billing.SUPPORTED_ROUTE,
        },
        "cost_analytics": {
            "path": csv_path.as_posix(),
            "raw_sha256": _sha(csv_path),
            "ui_date_start_utc": "2026-08-18",
            "ui_date_end_inclusive_utc": "2026-08-19",
            "row_count": 2,
            "line_item_amount_sum_usd": "4.50",
            "derived_cost_sum_usd": "4.5000",
            "display_total_usd": "4.50",
            "csv_rounding_gap_usd": "0.00",
        },
        "invoice": {
            "path": invoice_path.as_posix(),
            "raw_sha256": _sha(invoice_path),
            "issue_date": "2026-09-01",
            "service_period_start": "2026-08-01",
            "service_period_end_inclusive": "2026-08-31",
            "subtotal_usd": "-0.06",
            "total_usd": "-0.06",
            "applied_balance_usd": "0.06",
            "amount_due_usd": "0.00",
            "usage_offset_by_prepaid_commit": True,
            "scope_role": console_billing.INVOICE_SCOPE_ROLE,
        },
        "dashboard": {
            "mode": "direct_delta",
            "before_total_usd": None,
            "after_total_usd": None,
            "reported_delta_usd": "4.50",
        },
        "provider_settlement": {
            "status": console_billing.SETTLEMENT_STATUS,
            "account_identity_sha256": account_hash,
            "finalized_through_utc": "2026-08-20T00:00:00Z",
        },
        "limitations": list(console_billing.FINALITY_LIMITATIONS),
    }


def test_valid_console_evidence_recomputes_csv_and_finality(tmp_path: Path) -> None:
    record = _record(tmp_path)

    validated = console_billing.validate_capture(
        record,
        project_root=tmp_path,
        as_of=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )

    assert validated["provider_delta_usd"] == "4.50"
    assert validated["provider_settlement"]["status"] == console_billing.SETTLEMENT_STATUS
    assert validated["billing_scope"] == record["billing_scope"]


def test_cost_analytics_byte_tamper_fails_closed(tmp_path: Path) -> None:
    record = _record(tmp_path)
    path = Path(record["cost_analytics"]["path"])
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(console_billing.TogetherConsoleBillingError, match="raw SHA-256"):
        console_billing.validate_capture(record, project_root=tmp_path)


def test_cost_analytics_project_scope_mismatch_fails_closed(tmp_path: Path) -> None:
    record = _record(tmp_path)
    record["account_binding"]["project_id_sha256"] = "b" * 64

    with pytest.raises(console_billing.TogetherConsoleBillingError, match="owner ratification"):
        console_billing.validate_capture(record, project_root=tmp_path)


def test_cost_analytics_display_total_is_recomputed(tmp_path: Path) -> None:
    record = _record(tmp_path)
    record["cost_analytics"]["display_total_usd"] = "4.51"

    with pytest.raises(console_billing.TogetherConsoleBillingError, match="display_total"):
        console_billing.validate_capture(record, project_root=tmp_path)


def test_invoice_cannot_be_used_as_exact_window_total(tmp_path: Path) -> None:
    record = _record(tmp_path)
    record["invoice"]["scope_role"] = "exact_window_total"

    with pytest.raises(console_billing.TogetherConsoleBillingError, match="exact-window role"):
        console_billing.validate_capture(record, project_root=tmp_path)


def test_support_route_is_hash_bound(tmp_path: Path) -> None:
    record = _record(tmp_path)
    support_path = Path(record["support_disposition"]["path"])
    payload = json.loads(support_path.read_text(encoding="utf-8"))
    payload["supported_reconciliation_route"] = "enterprise_hourly_api"
    _write_json(support_path, payload)
    record["support_disposition"]["raw_sha256"] = _sha(support_path)

    with pytest.raises(console_billing.TogetherConsoleBillingError, match="support disposition"):
        console_billing.validate_capture(record, project_root=tmp_path)


def test_future_console_observation_fails_closed(tmp_path: Path) -> None:
    record = _record(tmp_path)

    with pytest.raises(console_billing.TogetherConsoleBillingError, match="future"):
        console_billing.validate_capture(
            record,
            project_root=tmp_path,
            as_of=datetime(2026, 9, 3, 11, 59, 59, tzinfo=timezone.utc),
        )


def test_input_is_not_mutated(tmp_path: Path) -> None:
    record = _record(tmp_path)
    before = deepcopy(record)

    console_billing.validate_capture(record, project_root=tmp_path)

    assert record == before
