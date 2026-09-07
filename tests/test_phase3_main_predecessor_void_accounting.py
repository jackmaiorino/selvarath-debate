"""Amendment 15: the pinned voided-predecessor accounting record and its enforcement."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from rejudge import phase3_main_runtime_policies as policies
from rejudge import phase3_main_stage_cap as stage_cap

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECORD_PATH = PROJECT_ROOT / policies.PREDECESSOR_VOID_ACCOUNTING_RELATIVE_PATH


def _record() -> tuple[dict, bytes]:
    raw = RECORD_PATH.read_bytes()
    return json.loads(raw.decode("utf-8")), raw


def test_pinned_record_validates_without_touching_the_ledger():
    record, raw = _record()
    result = policies.validate_predecessor_void_accounting(record, raw=raw)
    assert result["record_raw_sha256"] == policies.PREDECESSOR_VOID_ACCOUNTING_RAW_SHA256
    assert result["predecessor_run_id"] == "phase3-main-34010df12dee7c38"
    assert result["ledger_verified"] is False
    assert result["execution_authorized"] is False
    accounted = Decimal(result["accounted_spend_usd"])
    ceiling = Decimal(result["maximum_successor_expenditure_usd"])
    assert accounted == Decimal("24.89969640")
    assert ceiling == stage_cap.STAGE_CAP_USD - stage_cap.PREDECESSOR_UPPER_BOUND_USD - accounted
    assert ceiling >= stage_cap.PROJECTED_MAIN_USD
    assert Decimal(result["expected_stage_total_usd"]) == (
        stage_cap.PREDECESSOR_UPPER_BOUND_USD + accounted + stage_cap.PROJECTED_MAIN_USD)


def test_pinned_hash_matches_the_tracked_bytes():
    _unused, raw = _record()
    assert hashlib.sha256(raw).hexdigest() == policies.PREDECESSOR_VOID_ACCOUNTING_RAW_SHA256


def test_tampered_bytes_are_rejected():
    record, raw = _record()
    with pytest.raises(policies.MainRuntimePolicyError, match="pinned raw SHA-256"):
        policies.validate_predecessor_void_accounting(record, raw=raw + b" ")


def _with_pinned_sha(monkeypatch, record: dict) -> bytes:
    raw = json.dumps(record).encode("utf-8")
    monkeypatch.setattr(
        policies, "PREDECESSOR_VOID_ACCOUNTING_RAW_SHA256", hashlib.sha256(raw).hexdigest())
    return raw


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r["accounting"].__setitem__("maximum_successor_expenditure_usd", "999.00"),
         "arithmetic drifted"),
        (lambda r: r["accounting"].__setitem__("accounted_spend_usd", "1.00"),
         "settled plus uncertain"),
        (lambda r: r["accounting"].__setitem__("inside_reconciled_segments", True),
         "treatment drifted"),
        (lambda r: r["accounting"].__setitem__("unmatched_reservations", 1),
         "no open reservations"),
        (lambda r: r["accounting"].__setitem__("stage_cap_usd", "1200.00"),
         "owner ratification"),
        (lambda r: r["scientific_use"].__setitem__("verdict_content_inspected", True),
         "scientific-use exclusion"),
        (lambda r: r.__setitem__("creates_additional_spending_authority", True),
         "must be false"),
        (lambda r: r["predecessor"].__setitem__("status", "voided_environmental_interruption"),
         "status drifted"),
    ],
)
def test_semantic_drift_is_rejected(monkeypatch, mutate, message):
    record, _raw = _record()
    mutate(record)
    raw = _with_pinned_sha(monkeypatch, record)
    with pytest.raises(policies.MainRuntimePolicyError, match=message):
        policies.validate_predecessor_void_accounting(record, raw=raw)


def test_ceiling_must_still_fit_the_certified_forecast(monkeypatch):
    record, _raw = _record()
    accounting = record["accounting"]
    prior = Decimal(accounting["prior_reconciled_usd"])
    cap = Decimal(accounting["stage_cap_usd"])
    forecast = Decimal(accounting["successor_forecast_main_usd"])
    huge = cap - prior - forecast + Decimal("0.01")
    accounting["settled_spend_usd"] = format(huge, "f")
    accounting["accounted_spend_usd"] = format(huge, "f")
    accounting["expected_stage_total_usd"] = format(prior + huge + forecast, "f")
    accounting["maximum_successor_expenditure_usd"] = format(cap - prior - huge, "f")
    raw = _with_pinned_sha(monkeypatch, record)
    with pytest.raises(policies.MainRuntimePolicyError, match="no longer fits"):
        policies.validate_predecessor_void_accounting(record, raw=raw)


def test_ledger_verification_against_the_archived_predecessor():
    record, raw = _record()
    ledger = Path(record["predecessor"]["usage_ledger_path"])
    if not ledger.is_file():
        pytest.skip("archived predecessor ledger is not mounted on this host")
    result = policies.validate_predecessor_void_accounting(record, raw=raw, verify_ledger=True)
    assert result["ledger_verified"] is True


def test_ledger_drift_is_rejected(monkeypatch, tmp_path):
    record, _raw = _record()
    root = tmp_path / "predecessor-root"
    root.mkdir()
    ledger = root / "main_usage.jsonl"
    ledger.write_bytes(b'{"status": "ledger_genesis"}\n')
    record["predecessor"]["artifact_root"] = str(root)
    record["predecessor"]["usage_ledger_path"] = str(ledger)
    record["predecessor"]["usage_ledger_raw_sha256"] = "0" * 64
    raw = _with_pinned_sha(monkeypatch, record)
    with pytest.raises(policies.MainRuntimePolicyError, match="ledger bytes drifted"):
        policies.validate_predecessor_void_accounting(record, raw=raw, verify_ledger=True)
