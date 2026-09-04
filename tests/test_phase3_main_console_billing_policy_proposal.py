"""Static integrity checks for the Phase 3 console-billing policy proposal."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rejudge import phase3_main_billing_reconciliation as reconciliation
from rejudge import phase3_main_together_console_billing as console_billing


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "rejudge/phase3_main_console_billing_policy_proposal_2026-09-04.json"


def _raw_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def test_console_billing_policy_binds_exact_non_authorizing_candidates() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    assert policy["schema_version"] == "phase3_main_console_billing_policy_proposal_v1"
    assert policy["proposal_status"] == "owner_decision_required"
    assert policy["provider"] == "Together"
    assert all(
        policy[field] is False
        for field in (
            "execution_authorized",
            "reviewer_dispatch_authorized",
            "provider_calls_authorized",
            "main_run_spend_authorized",
        )
    )

    bindings = policy["evidence_bindings"]
    for name in (
        "support_disposition",
        "console_evidence",
        "ledger_coverage_candidate",
        "reconciliation_candidate",
    ):
        binding = bindings[name]
        path = ROOT / binding["path"]
        assert _raw_sha(path) == binding["raw_sha256"]
        assert _canonical_sha(path) == binding["canonical_sha256"]
    owner_path = ROOT / bindings["owner_ratification"]["path"]
    assert _raw_sha(owner_path) == bindings["owner_ratification"]["raw_sha256"]

    evidence = json.loads(
        (ROOT / bindings["console_evidence"]["path"]).read_text(encoding="utf-8")
    )
    assert evidence["schema_version"] == console_billing.SCHEMA_VERSION
    assert evidence["provider_settlement"]["status"] == console_billing.SETTLEMENT_STATUS
    assert evidence["dashboard"]["reported_delta_usd"] == "94.07"

    candidate = json.loads(
        (ROOT / bindings["reconciliation_candidate"]["path"]).read_text(encoding="utf-8")
    )
    assert candidate["schema_version"] == reconciliation.SCHEMA_VERSION
    assert candidate["run_id"] == "phase3-main-candidate-2026-09-04"
    assert len(candidate["ledgers"]) == 18
    assert candidate["reconciliation"]["disposition"] == (
        reconciliation.DISPOSITION_CLOSED_PROVIDER_FINAL_BELOW_LOCAL_ACTUAL
    )
    assert candidate["ledger_totals"]["accounted_spend_usd"] == (
        "119.27238489999999805639"
    )

