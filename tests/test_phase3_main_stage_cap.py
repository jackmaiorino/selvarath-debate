"""Tests for the owner-ratified Phase 3 main stage-cap amendment."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from rejudge import phase3_main_stage_cap as stage_cap


def _record() -> dict:
    return json.loads(stage_cap.RATIFICATION_PATH.read_text(encoding="utf-8"))


def test_exact_ratification_validates_as_non_execution_cap_amendment():
    result = stage_cap.validate_stage_cap_ratification(_record())
    assert result["stage_cap_usd"] == stage_cap.STAGE_CAP_USD
    assert result["predecessor_spend_upper_bound_usd"] == (
        stage_cap.PREDECESSOR_UPPER_BOUND_USD
    )
    assert result["projected_stage_total_usd"] == stage_cap.PROJECTED_STAGE_TOTAL_USD
    assert result["execution_authorized"] is False


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("billing_decision", "selected_ledger_count", 17, "billing decision"),
        ("billing_decision", "unresolved_reservation_count", 826, "billing decision"),
        ("stage_cap_decision", "stage_cap_usd", "1099.99", "stage-cap decision"),
        ("authority", "provider_calls_authorized", True, "execution authority"),
    ],
)
def test_ratification_semantic_drift_is_rejected(section, field, value, message):
    record = _record()
    record[section][field] = value
    with pytest.raises(stage_cap.StageCapRatificationError, match=message):
        stage_cap.validate_stage_cap_ratification(record)


def test_ratification_rejects_protocol_substitution():
    with pytest.raises(stage_cap.StageCapRatificationError, match="amended protocol"):
        stage_cap.validate_stage_cap_ratification(
            deepcopy(_record()), protocol_canonical_sha256="0" * 64
        )
