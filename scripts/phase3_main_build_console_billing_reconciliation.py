"""Build a non-authorizing Phase 3 console-billing reconciliation candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from rejudge import api_client
from rejudge import phase3_main_billing_evidence_inventory as evidence_inventory
from rejudge import phase3_main_billing_reconciliation as reconciliation
from rejudge import phase3_main_together_console_billing as console_billing


REPO_ROOT = Path(__file__).resolve().parents[1]


class ConsoleReconciliationBuildError(ValueError):
    """The frozen inputs cannot produce a valid reconciliation candidate."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConsoleReconciliationBuildError(f"could not load {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ConsoleReconciliationBuildError(f"{label} must be an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConsoleReconciliationBuildError(f"{label} must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ConsoleReconciliationBuildError(f"{label} must be UTC")
    return parsed.astimezone(timezone.utc)


def _selected_ids(ratification: Mapping[str, Any]) -> set[str]:
    selection = ratification.get("predecessor_billing_selection")
    if not isinstance(selection, Mapping):
        raise ConsoleReconciliationBuildError("owner billing selection is missing")
    if (
        selection.get("authoritative_completeness")
        != "owner_confirmed_complete_for_bound_window"
        or selection.get("included_ledger_semantic_disjointness")
        != "owner_confirmed_by_exact_source_selection"
    ):
        raise ConsoleReconciliationBuildError("owner billing selection is not complete")
    selected: set[str] = set()
    for group in selection.get("source_groups", []):
        if not isinstance(group, Mapping):
            raise ConsoleReconciliationBuildError("owner billing source group is invalid")
        if group.get("disposition") == "include_authoritative":
            ids = group.get("source_ids")
            if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
                raise ConsoleReconciliationBuildError("owner billing source IDs are invalid")
            if selected.intersection(ids):
                raise ConsoleReconciliationBuildError("owner billing source IDs repeat")
            selected.update(ids)
    if len(selected) != 18:
        raise ConsoleReconciliationBuildError("owner billing selection must contain 18 ledgers")
    return selected


def _ledger_entry(source: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(str(source["path"])).resolve()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["raw_sha256"]:
        raise ConsoleReconciliationBuildError(
            f"ledger raw hash drifted: {source['source_id']}"
        )
    ordinary = reconciliation._jsonl_bytes(raw, f"ledger {source['source_id']}")
    exact = reconciliation._jsonl_bytes(
        raw, f"ledger {source['source_id']}", exact_numbers=True
    )
    try:
        identity, event_hashes = api_client._validate_usage_chain(ordinary, path)
        api_client._summarize_usage_events(ordinary[1:], path, strict_lifecycle=True)
    except (api_client.UsageLedgerError, TypeError, ValueError) as exc:
        raise ConsoleReconciliationBuildError(
            f"ledger chain or lifecycle failed: {source['source_id']}"
        ) from exc
    actual, uncertain, accounted, unresolved = reconciliation._exact_ledger_summary(
        exact[1:], label=f"ledger {source['source_id']}"
    )
    expected_claims = {
        "actual_spend_usd": actual,
        "uncertain_spend_usd": uncertain,
        "accounted_spend_usd": accounted,
    }
    for field, expected in expected_claims.items():
        if Decimal(str(source[field])) != expected:
            raise ConsoleReconciliationBuildError(
                f"inventory {field} drifted: {source['source_id']}"
            )
    if source["tail_sequence"] != len(event_hashes) - 1:
        raise ConsoleReconciliationBuildError(
            f"inventory tail sequence drifted: {source['source_id']}"
        )
    if source["tail_event_hash"] != event_hashes[-1]:
        raise ConsoleReconciliationBuildError(
            f"inventory tail hash drifted: {source['source_id']}"
        )
    entry = {
        "path": path.as_posix(),
        "raw_sha256": source["raw_sha256"],
        "state_raw_sha256": source["state_raw_sha256"],
        "identity": identity,
        "tail_sequence": source["tail_sequence"],
        "tail_event_hash": source["tail_event_hash"],
        "actual_spend_usd": format(actual, "f"),
        "uncertain_spend_usd": format(uncertain, "f"),
        "accounted_spend_usd": format(accounted, "f"),
        "unresolved_attempt_ids": list(unresolved),
    }
    reconciliation._validate_ledger(entry, project_root=REPO_ROOT, index=0)
    return entry


def _write_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
        handle.write("\n")


def build(
    *, inventory_path: Path, ratification_path: Path, provider_evidence_path: Path,
    coverage_output: Path, reconciliation_output: Path, run_id: str,
    recorded_at_utc: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build and validate exact coverage and one candidate reconciliation."""
    if not run_id or any(character.isspace() for character in run_id):
        raise ConsoleReconciliationBuildError("run_id must be non-empty without whitespace")
    recorded_at = _utc(recorded_at_utc, "recorded_at_utc")
    inventory_path = inventory_path.resolve()
    ratification_path = ratification_path.resolve()
    provider_evidence_path = provider_evidence_path.resolve()
    coverage_output = coverage_output.resolve()
    reconciliation_output = reconciliation_output.resolve()
    if coverage_output.exists() or reconciliation_output.exists():
        raise ConsoleReconciliationBuildError("refusing to overwrite billing outputs")

    inventory = _load_json(inventory_path, "billing evidence inventory")
    evidence_inventory.validate_inventory(inventory, project_root=REPO_ROOT)
    ratification = _load_json(ratification_path, "owner ratification")
    provider_evidence = _load_json(provider_evidence_path, "console provider evidence")
    provider_validation = console_billing.validate_capture(
        provider_evidence,
        project_root=REPO_ROOT,
        as_of=recorded_at,
    )
    ratified_selection = ratification.get("predecessor_billing_selection")
    candidate_binding = (
        ratified_selection.get("candidate_inventory", {})
        if isinstance(ratified_selection, Mapping)
        else {}
    )
    if (
        not isinstance(candidate_binding, Mapping)
        or candidate_binding.get("raw_sha256") != _sha(inventory_path)
    ):
        raise ConsoleReconciliationBuildError(
            "owner ratification does not bind the exact candidate inventory"
        )
    selected = _selected_ids(ratification)
    sources = inventory.get("sources")
    if not isinstance(sources, list):
        raise ConsoleReconciliationBuildError("inventory sources are missing")
    source_map = {
        source.get("source_id"): source
        for source in sources
        if isinstance(source, Mapping)
    }
    if not selected <= set(source_map):
        raise ConsoleReconciliationBuildError("owner-selected ledger is absent from inventory")
    if any(
        source_map[source_id].get("source_kind") != evidence_inventory.LEDGER_KIND
        for source_id in selected
    ):
        raise ConsoleReconciliationBuildError("owner selection includes a non-ledger source")
    ledgers = sorted(
        (_ledger_entry(source_map[source_id]) for source_id in selected),
        key=lambda item: Path(item["path"]).resolve().as_posix(),
    )

    scope = dict(provider_validation["billing_scope"])
    coverage = {
        "schema_version": reconciliation.LEDGER_COVERAGE_SCHEMA,
        "stage": "main",
        "provider": reconciliation.PROVIDER,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
        "billing_scope": scope,
        "ledger_count": len(ledgers),
        "ledgers": sorted(
            (
                {
                    "ledger_id": entry["identity"]["ledger_id"],
                    "raw_sha256": entry["raw_sha256"],
                }
                for entry in ledgers
            ),
            key=lambda item: (item["ledger_id"], item["raw_sha256"]),
        ),
    }
    _write_exclusive(coverage_output, coverage)

    actual = reconciliation._exact_sum(
        tuple(Decimal(entry["actual_spend_usd"]) for entry in ledgers)
    )
    uncertain = reconciliation._exact_sum(
        tuple(Decimal(entry["uncertain_spend_usd"]) for entry in ledgers)
    )
    accounted = reconciliation._exact_sum((actual, uncertain))
    unresolved = sorted(
        attempt_id
        for entry in ledgers
        for attempt_id in entry["unresolved_attempt_ids"]
    )
    provider_delta = Decimal(provider_validation["provider_delta_usd"])
    discrepancy = reconciliation._exact_sum((provider_delta, actual.copy_negate()))
    tolerance = Decimal(reconciliation.FROZEN_TOLERANCE_USD)
    within_envelope = actual <= provider_delta <= accounted
    disposition = reconciliation._expected_disposition(
        unresolved=bool(unresolved),
        discrepant=abs(discrepancy) > tolerance,
        within_conservative_envelope=within_envelope,
        provider_final=True,
        provider_delta=provider_delta,
        actual=actual,
    )
    record = {
        "schema_version": reconciliation.SCHEMA_VERSION,
        "stage": "main",
        "run_id": run_id,
        "recorded_at_utc": recorded_at_utc,
        "provider": reconciliation.PROVIDER,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
        "billing_scope": scope,
        "provider_evidence": {
            "observed_at_utc": provider_validation["observed_at_utc"],
            "path": provider_evidence_path.as_posix(),
            "raw_sha256": _sha(provider_evidence_path),
        },
        "ledger_coverage": {
            "path": coverage_output.as_posix(),
            "raw_sha256": _sha(coverage_output),
        },
        "dashboard": dict(provider_validation["dashboard"]),
        "ledgers": ledgers,
        "ledger_totals": {
            "actual_spend_usd": format(actual, "f"),
            "uncertain_spend_usd": format(uncertain, "f"),
            "accounted_spend_usd": format(accounted, "f"),
            "unresolved_attempt_ids": unresolved,
        },
        "reconciliation": {
            "provider_delta_usd": format(provider_delta, "f"),
            "discrepancy_usd": format(discrepancy, "f"),
            "absolute_discrepancy_usd": format(abs(discrepancy), "f"),
            "within_conservative_envelope": within_envelope,
            "tolerance_usd": reconciliation.FROZEN_TOLERANCE_USD,
            "disposition": disposition,
        },
    }
    reconciliation.validate_billing_reconciliation(
        record, project_root=REPO_ROOT, as_of=recorded_at
    )
    _write_exclusive(reconciliation_output, record)
    return coverage, record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--owner-ratification", type=Path, required=True)
    parser.add_argument("--provider-evidence", type=Path, required=True)
    parser.add_argument("--coverage-output", type=Path, required=True)
    parser.add_argument("--reconciliation-output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--recorded-at-utc", required=True)
    args = parser.parse_args(argv)
    coverage, record = build(
        inventory_path=args.inventory,
        ratification_path=args.owner_ratification,
        provider_evidence_path=args.provider_evidence,
        coverage_output=args.coverage_output,
        reconciliation_output=args.reconciliation_output,
        run_id=args.run_id,
        recorded_at_utc=args.recorded_at_utc,
    )
    print(json.dumps({
        "coverage_raw_sha256": _sha(args.coverage_output.resolve()),
        "reconciliation_raw_sha256": _sha(args.reconciliation_output.resolve()),
        "ledger_count": coverage["ledger_count"],
        "provider_delta_usd": record["reconciliation"]["provider_delta_usd"],
        "disposition": record["reconciliation"]["disposition"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
