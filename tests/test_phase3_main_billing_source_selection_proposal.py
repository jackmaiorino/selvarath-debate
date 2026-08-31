"""Offline checks for the non-authorizing Phase 3 billing source proposal."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from rejudge import phase3_owner_signing


REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = (
    REPO_ROOT / "rejudge/phase3_main_billing_source_selection_proposal_2026-08-30.json"
)
RATIFICATION_PATH = (
    REPO_ROOT / "rejudge/phase3_main_owner_ratification_2026-08-31.json"
)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        assert key not in value, f"duplicate JSON key: {key}"
        value[key] = item
    return value


def _load(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    return raw, json.loads(raw, object_pairs_hook=_unique_object)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_proposal_binds_exact_candidate_and_cannot_authorize() -> None:
    _proposal_raw, proposal = _load(PROPOSAL_PATH)
    candidate_claim = proposal["candidate_inventory"]
    candidate_path = REPO_ROOT / candidate_claim["tracked_path"]
    candidate_raw, candidate = _load(candidate_path)

    assert set(proposal) == {
        "schema_version",
        "stage",
        "provider",
        "proposal_only",
        "proposal_status",
        "owner_ratified",
        "authoritative_completeness",
        "semantic_disjointness",
        "execution_authorized",
        "provider_calls_authorized",
        "main_run_spend_authorized",
        "candidate_inventory",
        "source_groups",
        "auxiliary_replica_observations",
        "mechanical_observations",
        "owner_decisions_required",
    }
    assert proposal["schema_version"] == (
        "phase3_main_billing_source_selection_proposal_v1"
    )
    assert proposal["proposal_only"] is True
    assert proposal["proposal_status"] == "owner_decision_required"
    assert proposal["owner_ratified"] is False
    assert proposal["authoritative_completeness"] == "not_established"
    assert proposal["semantic_disjointness"] == "not_established"
    assert proposal["execution_authorized"] is False
    assert proposal["provider_calls_authorized"] is False
    assert proposal["main_run_spend_authorized"] is False
    assert all(
        set(group) == {
            "group_id",
            "source_ids",
            "recommended_disposition",
            "owner_disposition",
        }
        and group["recommended_disposition"] == (
            "include_if_owner_confirms_complete_and_disjoint"
        )
        and group["owner_disposition"] == "pending"
        for group in proposal["source_groups"]
    )
    assert len(proposal["owner_decisions_required"]) == 4

    assert hashlib.sha256(candidate_raw).hexdigest() == candidate_claim["raw_sha256"]
    assert candidate["schema_version"] == candidate_claim["schema_version"]
    assert candidate["billing_window"] == candidate_claim["billing_window"]
    assert candidate["source_count"] == candidate_claim["source_count"]
    source_ids = [source["source_id"] for source in candidate["sources"]]
    assert source_ids == candidate_claim["source_ids"]

    claimed_totals = candidate_claim["totals"]
    for field in (
        "row_count",
        "first_row_utc",
        "last_row_utc",
        "actual_spend_usd",
        "uncertain_spend_usd",
        "accounted_spend_usd",
    ):
        assert claimed_totals[field] == candidate["totals"][field]
    assert claimed_totals["uncertain_line_ref_count"] == len(
        candidate["totals"]["uncertain_line_refs"]
    )

    grouped = [
        source_id
        for group in proposal["source_groups"]
        for source_id in group["source_ids"]
    ]
    assert len(grouped) == len(set(grouped))
    assert sorted(grouped) == source_ids


def test_replica_and_time_interval_observations_match_candidate() -> None:
    _proposal_raw, proposal = _load(PROPOSAL_PATH)
    candidate_path = REPO_ROOT / proposal["candidate_inventory"]["tracked_path"]
    _candidate_raw, candidate = _load(candidate_path)
    sources = {source["source_id"]: source for source in candidate["sources"]}

    replicas = proposal["auxiliary_replica_observations"]
    assert len(replicas) == 2
    for observation in replicas:
        source = sources[observation["source_id"]]
        assert source["source_kind"] == "auxiliary_screen_usage_jsonl"
        assert observation["raw_sha256"] == source["raw_sha256"]
        assert observation["selected_path"] == source["path"]
        assert len(observation["byte_identical_replica_paths"]) == 3
        assert len(set(observation["byte_identical_replica_paths"])) == 3
        assert observation["selected_path"] in observation[
            "byte_identical_replica_paths"
        ]

    ledgers = [
        source
        for source in candidate["sources"]
        if source["source_kind"] == "immutable_usage_ledger"
    ]
    auxiliaries = [
        source
        for source in candidate["sources"]
        if source["source_kind"] == "auxiliary_screen_usage_jsonl"
    ]
    overlap_count = sum(
        _utc(auxiliary["first_row_utc"]) <= _utc(ledger["last_row_utc"])
        and _utc(ledger["first_row_utc"]) <= _utc(auxiliary["last_row_utc"])
        for auxiliary in auxiliaries
        for ledger in ledgers
    )
    observations = proposal["mechanical_observations"]
    assert overlap_count == observations[
        "auxiliary_ledger_closed_interval_overlap_count"
    ] == 0
    assert observations["cross_format_durable_call_identity_present"] is False
    assert proposal["semantic_disjointness"] == "not_established"


def test_owner_ratification_binds_exact_selection_without_execution_authority() -> None:
    _ratification_raw, ratification = _load(RATIFICATION_PATH)
    decision_brief = ratification["decision_brief"]
    brief_raw = (REPO_ROOT / decision_brief["tracked_path"]).read_bytes()
    assert hashlib.sha256(brief_raw).hexdigest() == decision_brief["raw_sha256"]

    policies = ratification["runtime_policy_decisions"]
    assert len(policies) == 2
    for policy in policies:
        policy_raw = (REPO_ROOT / policy["tracked_path"]).read_bytes()
        assert hashlib.sha256(policy_raw).hexdigest() == policy["raw_sha256"]
        assert policy["disposition"] == "ratified_unchanged"
    reviewer_policy = next(
        policy for policy in policies if "reviewer-usage" in policy["policy_id"]
    )
    assert reviewer_policy["external_reviewer_dispatch_ceiling"] == 59040
    assert reviewer_policy["usage_treatment"] == "separate_non_usd_accounting"

    selection = ratification["predecessor_billing_selection"]
    proposal_claim = selection["proposal"]
    proposal_raw, proposal = _load(REPO_ROOT / proposal_claim["tracked_path"])
    assert hashlib.sha256(proposal_raw).hexdigest() == proposal_claim["raw_sha256"]
    inventory_claim = selection["candidate_inventory"]
    inventory_raw, inventory = _load(REPO_ROOT / inventory_claim["tracked_path"])
    assert hashlib.sha256(inventory_raw).hexdigest() == inventory_claim["raw_sha256"]
    assert selection["billing_window"] == {
        **proposal["candidate_inventory"]["billing_window"],
        "interval": "half_open",
    }
    assert selection["authoritative_completeness"] == (
        "owner_confirmed_complete_for_bound_window"
    )
    assert selection["included_ledger_semantic_disjointness"] == (
        "owner_confirmed_by_exact_source_selection"
    )
    assert selection["auxiliary_cross_format_disjointness"] == (
        "not_asserted_and_not_required_for_excluded_sources"
    )

    groups = {group["group_id"]: group for group in selection["source_groups"]}
    assert groups["measurement-ledgers"]["disposition"] == "include_authoritative"
    assert len(groups["measurement-ledgers"]["source_ids"]) == 17
    assert groups["journal-validation-ledger"] == {
        "group_id": "journal-validation-ledger",
        "disposition": "include_authoritative",
        "source_ids": ["phase3-v3-journal-validation"],
    }
    assert groups["auxiliary-screens"]["disposition"] == (
        "exclude_authoritative_retain_explanatory"
    )
    assert len(groups["auxiliary-screens"]["source_ids"]) == 2

    included_ids = {
        source_id
        for group in groups.values()
        if group["disposition"] == "include_authoritative"
        for source_id in group["source_ids"]
    }
    excluded_ids = set(groups["auxiliary-screens"]["source_ids"])
    sources = {source["source_id"]: source for source in inventory["sources"]}
    assert included_ids.isdisjoint(excluded_ids)
    assert included_ids | excluded_ids == set(sources)

    def aggregate(source_ids: set[str]) -> dict[str, Any]:
        selected = [sources[source_id] for source_id in source_ids]
        return {
            "source_count": len(selected),
            "row_count": sum(source["row_count"] for source in selected),
            "actual_spend_usd": str(sum(
                Decimal(source["actual_spend_usd"]) for source in selected
            )),
            "uncertain_spend_usd": str(sum(
                Decimal(source["uncertain_spend_usd"]) for source in selected
            )),
            "accounted_spend_usd": str(sum(
                Decimal(source["accounted_spend_usd"]) for source in selected
            )),
            "uncertain_line_ref_count": sum(
                len(source["uncertain_line_refs"]) for source in selected
            ),
        }

    assert aggregate(included_ids) == selection["authoritative_union"]
    assert aggregate(excluded_ids) == selection["excluded_explanatory_evidence"]
    assert ratification["together_account_scope"]["account_identity_sha256"] == (
        "8a54a740aa7098d51327ed0ab1c40b533a005ff5cf20566fff0b4fe44e9d2c3e"
    )
    assert ratification["owner_signing_key"]["public_key_fingerprint"] == (
        phase3_owner_signing.OWNER_SIGNING_KEY_FINGERPRINT
    )
    assert phase3_owner_signing.OWNER_SIGNING_PUBLIC_KEY is not None
    assert set(ratification["authority_limits"].values()) == {False}
