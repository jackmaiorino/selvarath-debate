"""Amendment 15: the pinned voided-predecessor accounting record and its enforcement."""
from __future__ import annotations

import hashlib
import json
import shutil
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


STOPPED_RECORD_PATH = PROJECT_ROOT / policies.STOPPED_PREDECESSOR_ACCOUNTING_RELATIVE_PATH


def _stopped_record() -> tuple[dict, bytes]:
    raw = STOPPED_RECORD_PATH.read_bytes()
    return json.loads(raw), raw


def _earlier_validation() -> dict:
    record, raw = _record()
    return policies.validate_predecessor_void_accounting(record, raw=raw)


def _pin_stopped(monkeypatch, record: dict) -> bytes:
    raw = json.dumps(record).encode("utf-8")
    monkeypatch.setattr(
        policies, "STOPPED_PREDECESSOR_ACCOUNTING_RAW_SHA256", hashlib.sha256(raw).hexdigest())
    return raw


def test_cumulative_loader_counts_both_predecessors_and_all_uncertain_spend():
    result = policies.load_and_validate_predecessor_void_accounting(
        PROJECT_ROOT, verify_ledger=False)
    assert result["predecessor_run_ids"] == [
        "phase3-main-34010df12dee7c38", "phase3-main-afc24ecd607773c0"]
    assert result["predecessor_run_id"] == "phase3-main-afc24ecd607773c0"
    assert len(set(result["predecessor_manifest_canonical_sha256s"])) == 2
    assert [r["accounted_spend_usd"] for r in result["records"]] == [
        "24.89969640", "36.15647432"]
    assert result["settled_spend_usd"] == "60.97524832"
    assert result["uncertain_spend_usd"] == "0.08092240"
    assert result["accounted_spend_usd"] == "61.05617072"
    assert result["maximum_successor_expenditure_usd"] == "919.67144438"
    assert result["expected_stage_total_usd"] == "1089.14855562"
    assert result["ledger_verified"] is False
    assert result["execution_authorized"] is False
    initial = stage_cap.PREDECESSOR_UPPER_BOUND_USD + Decimal(result["accounted_spend_usd"])
    assert initial == Decimal("180.32855562")
    assert stage_cap.STAGE_CAP_USD - initial == Decimal("919.67144438")


@pytest.mark.parametrize("omit_latest", [False, True])
def test_loader_cannot_omit_either_record(tmp_path, omit_latest):
    present = RECORD_PATH if omit_latest else STOPPED_RECORD_PATH
    target = tmp_path / "rejudge" / present.name
    target.parent.mkdir()
    shutil.copyfile(present, target)
    with pytest.raises(policies.MainRuntimePolicyError, match="regular file"):
        policies.load_and_validate_predecessor_void_accounting(tmp_path, verify_ledger=False)


@pytest.mark.parametrize("mutate", [
    lambda r: r.pop("carried_forward_predecessors"),
    lambda r: r.__setitem__("carried_forward_predecessors", []),
    lambda r: r["carried_forward_predecessors"].append(r["carried_forward_predecessors"][0]),
    lambda r: r["carried_forward_predecessors"][0].__setitem__("accounted_spend_usd", "0.00"),
    lambda r: r["carried_forward_predecessors"][0].__setitem__("record_raw_sha256", "f" * 64),
])
def test_carry_cannot_omit_duplicate_or_change_prior_accounting(monkeypatch, mutate):
    record, _raw = _stopped_record()
    mutate(record)
    raw = _pin_stopped(monkeypatch, record)
    with pytest.raises(policies.MainRuntimePolicyError, match="omitted, duplicated, or drifted"):
        policies.validate_stopped_predecessor_accounting(
            record, raw=raw, earlier=_earlier_validation())


@pytest.mark.parametrize("field", ["run_id", "manifest_canonical_sha256", "artifact_root"])
def test_same_predecessor_identity_cannot_be_counted_twice(monkeypatch, field):
    record, _raw = _stopped_record()
    earlier_record, _ = _record()
    record["predecessor"][field] = earlier_record["predecessor"][field]
    if field == "artifact_root":
        record["predecessor"]["usage_ledger_path"] = earlier_record["predecessor"]["usage_ledger_path"]
    raw = _pin_stopped(monkeypatch, record)
    with pytest.raises(policies.MainRuntimePolicyError, match="already counted identity"):
        policies.validate_stopped_predecessor_accounting(
            record, raw=raw, earlier=_earlier_validation())


def test_new_record_bytes_are_pinned():
    record, raw = _stopped_record()
    assert hashlib.sha256(raw).hexdigest() == policies.STOPPED_PREDECESSOR_ACCOUNTING_RAW_SHA256
    with pytest.raises(policies.MainRuntimePolicyError, match="pinned raw SHA-256"):
        policies.validate_stopped_predecessor_accounting(
            record, raw=raw + b" ", earlier=_earlier_validation())


def test_new_record_cannot_use_the_old_ceiling(monkeypatch):
    record, _raw = _stopped_record()
    record["accounting"]["maximum_successor_expenditure_usd"] = "955.82791870"
    raw = _pin_stopped(monkeypatch, record)
    with pytest.raises(policies.MainRuntimePolicyError, match="arithmetic drifted"):
        policies.validate_stopped_predecessor_accounting(
            record, raw=raw, earlier=_earlier_validation())


@pytest.mark.parametrize("field,value,message", [
    ("unknown_charge_count", 3, "unknown charge count drifted"),
    ("successful_calls", 3682, "success count drifted"),
])
def test_archived_ledger_call_counts_are_verified(monkeypatch, field, value, message):
    record, _raw = _stopped_record()
    if not Path(record["predecessor"]["usage_ledger_path"]).is_file():
        pytest.skip("archived predecessor ledger is not mounted on this host")
    record["accounting"][field] = value
    raw = _pin_stopped(monkeypatch, record)
    with pytest.raises(policies.MainRuntimePolicyError, match=message):
        policies.validate_stopped_predecessor_accounting(
            record, raw=raw, earlier=_earlier_validation(), verify_ledger=True)


def test_uncertain_charges_cannot_be_dropped_even_with_consistent_new_arithmetic(monkeypatch):
    record, _raw = _stopped_record()
    if not Path(record["predecessor"]["usage_ledger_path"]).is_file():
        pytest.skip("archived predecessor ledger is not mounted on this host")
    accounting = record["accounting"]
    earlier = _earlier_validation()
    accounting["uncertain_spend_usd"] = "0.00000000"
    accounting["accounted_spend_usd"] = accounting["settled_spend_usd"]
    cumulative = Decimal(earlier["accounted_spend_usd"]) + Decimal(accounting["accounted_spend_usd"])
    prior = Decimal(accounting["prior_reconciled_usd"])
    accounting["expected_stage_total_usd"] = str(prior + cumulative + stage_cap.PROJECTED_MAIN_USD)
    accounting["maximum_successor_expenditure_usd"] = str(stage_cap.STAGE_CAP_USD - prior - cumulative)
    raw = _pin_stopped(monkeypatch, record)
    with pytest.raises(policies.MainRuntimePolicyError, match="totals differ"):
        policies.validate_stopped_predecessor_accounting(
            record, raw=raw, earlier=earlier, verify_ledger=True)


def test_archived_verification_does_not_repair_or_write_state(monkeypatch):
    from rejudge import api_client

    record, raw = _stopped_record()
    ledger = Path(record["predecessor"]["usage_ledger_path"])
    if not ledger.is_file():
        pytest.skip("archived predecessor ledger is not mounted on this host")
    read_state = api_client._read_usage_state

    def lagging_state(path):
        state = read_state(path)
        state["last_sequence"] -= 1
        return state

    def forbidden_write(*args, **kwargs):
        pytest.fail("archived accounting attempted to repair/write state")

    monkeypatch.setattr(api_client, "_read_usage_state", lagging_state)
    monkeypatch.setattr(api_client, "_atomic_write_json", forbidden_write)
    with pytest.raises(policies.MainRuntimePolicyError, match="durable state drifted"):
        policies.validate_stopped_predecessor_accounting(
            record, raw=raw, earlier=_earlier_validation(), verify_ledger=True)


def test_cumulative_archived_ledger_verification_preserves_evidence_bytes():
    from rejudge import api_client

    paths = []
    for read_record in (_record, _stopped_record):
        record, _ = read_record()
        ledger = Path(record["predecessor"]["usage_ledger_path"])
        if not ledger.is_file():
            pytest.skip("archived predecessor ledger is not mounted on this host")
        paths.extend([ledger, api_client.usage_ledger_state_path(ledger)])
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result = policies.load_and_validate_predecessor_void_accounting(PROJECT_ROOT, verify_ledger=True)
    assert result["ledger_verified"] is True
    assert result["accounted_spend_usd"] == "61.05617072"
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
