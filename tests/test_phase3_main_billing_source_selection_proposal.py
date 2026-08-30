"""Offline checks for the non-authorizing Phase 3 billing source proposal."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATH = (
    REPO_ROOT / "rejudge/phase3_main_billing_source_selection_proposal_2026-08-30.json"
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
