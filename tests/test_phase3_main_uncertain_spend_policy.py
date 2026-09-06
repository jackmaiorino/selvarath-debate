"""Amendment 14: the pinned uncertain-spend policy loads exactly and fails closed on drift."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rejudge import phase2_canary_runner as runner
from rejudge import phase3_main_runtime_policies as policies

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / policies.UNCERTAIN_SPEND_POLICY_RELATIVE_PATH


def _load():
    raw = POLICY_PATH.read_bytes()
    return raw, json.loads(raw.decode("utf-8"))


def test_tracked_policy_is_pinned_exact_and_non_authorizing() -> None:
    raw, policy = _load()
    assert hashlib.sha256(raw).hexdigest() == policies.UNCERTAIN_SPEND_POLICY_RAW_SHA256
    validation = policies.validate_uncertain_spend_policy(policy, raw=raw)
    assert validation == {
        "schema_version": policies.UNCERTAIN_SPEND_POLICY_SCHEMA,
        "policy_id": policies.UNCERTAIN_SPEND_POLICY_ID,
        "policy_raw_sha256": policies.UNCERTAIN_SPEND_POLICY_RAW_SHA256,
        "run_uncertain_ceiling_usd": 100.0,
        "initial_run_uncertain_spend_usd": 0.0,
        "abandoned_rate_cooldown_seconds": 1800,
        "abandoned_rate_consecutive_pass_allowance": 8,
        "unknown_charge_pass_allowance": 50,
        "read_timeout_seconds": 600,
        "tolerated_finding": "unknown_charge",
        "execution_authorized": False,
    }
    for flag in ("execution_authorized", "provider_calls_authorized",
                 "main_run_spend_authorized"):
        assert policy[flag] is False
    assert policy["abandonment_guard"]["absolute_abandoned_cells"] == runner.ABANDONED_ABSOLUTE
    assert policy["abandonment_guard"]["abandoned_fraction"] == runner.ABANDONED_FRACTION
    assert policy["abandonment_guard"]["abandoned_fraction_floor_attempted"] == (
        runner.ABANDONED_FRACTION_FLOOR)
    assert set(policy["finalization"]["reconciliation_fatal_findings"]) == (
        policies.UNCERTAIN_SPEND_POLICY_FATAL_FINDINGS)


def test_loader_binds_the_role_limits_read_timeout() -> None:
    validation = policies.load_and_validate_uncertain_spend_policy(REPO_ROOT)
    assert validation["read_timeout_seconds"] == 600
    stale = {"request_settings": {"transport": {"http_timeout": {"read": 120}}}}
    with pytest.raises(policies.MainRuntimePolicyError, match="read timeout differs"):
        policies.load_and_validate_uncertain_spend_policy(REPO_ROOT, role_limits=stale)
    current = {"request_settings": {"transport": {"http_timeout": {"read": 600}}}}
    assert policies.load_and_validate_uncertain_spend_policy(
        REPO_ROOT, role_limits=current)["run_uncertain_ceiling_usd"] == 100.0


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda p: p.__setitem__("execution_authorized", True), "raw SHA-256"),
        (lambda p: p["ceiling"].__setitem__("run_uncertain_ceiling_usd", 250.0), "raw SHA-256"),
    ],
)
def test_any_byte_drift_is_rejected_by_the_pin(mutate, match) -> None:
    _raw, policy = _load()
    mutate(policy)
    raw = (json.dumps(policy, indent=1) + "\n").encode("utf-8")
    with pytest.raises(policies.MainRuntimePolicyError, match=match):
        policies.validate_uncertain_spend_policy(policy, raw=raw)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda p: p.__setitem__("execution_authorized", True), "must be false"),
        (lambda p: p["ceiling"].__setitem__("run_uncertain_ceiling_usd", 0), "positive number"),
        (lambda p: p["ceiling"].__setitem__("initial_run_uncertain_spend_usd", 1.0), "zero"),
        (lambda p: p["ceiling"].__setitem__("creates_additional_spending_authority", True),
         "treatment drifted"),
        (lambda p: p["abandonment_guard"].__setitem__("absolute_abandoned_cells", 5),
         "pass runner constants"),
        (lambda p: p["abandonment_guard"].__setitem__("scope", "lifetime"), "per pass"),
        (lambda p: p["finalization"].__setitem__(
            "reconciliation_tolerated_finding", "charged_malformed_response"),
         "tolerated reconciliation finding"),
        (lambda p: p["finalization"]["reconciliation_fatal_findings"].pop(), "fatal"),
        (lambda p: p["transport"].__setitem__("read_timeout_seconds", 0), "positive integer"),
    ],
)
def test_semantic_drift_is_rejected_even_when_the_pin_is_bypassed(
    mutate, match, monkeypatch,
) -> None:
    _raw, policy = _load()
    mutate(policy)
    raw = (json.dumps(policy, indent=1) + "\n").encode("utf-8")
    monkeypatch.setattr(
        policies, "UNCERTAIN_SPEND_POLICY_RAW_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(policies.MainRuntimePolicyError, match=match):
        policies.validate_uncertain_spend_policy(policy, raw=raw)
