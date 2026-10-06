"""Verify settled eight-world validation, saved replay and final accounting.

This receipt command performs no inference. Provider entry points are poisoned
during replay, and the original database and validation rows must stay identical.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from finalphase import cli, preflight, store, validation as V
from finalphase.models import spec
from finalphase.providers import Request


def ledger(path: Path) -> dict[str, list]:
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        db.row_factory = sqlite3.Row
        return {table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("calls", "batches", "events")}


def main() -> None:
    root, bench = cli.RUN_ROOT, cli.BENCH
    original = ledger(root / "validate.db")
    calls = original["calls"]
    assert len(calls) == 767 and all(row["status"] in store.MEASURED for row in calls)
    assert all(row["collected"] for row in original["batches"])
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    _, canonical = V.workload(cli._worlds())
    plan = V.plan(root, cli._worlds())
    assert plan["ready"] and plan["validation_input_sha256"] == "075f348038e8e808a48d98fdf30e6c9e0b1d37c6b687414e738c02b7645d4959"
    assert preflight.execution_sha256() == manifest["throughput"]["validate"]["execution_sha256"]
    by_id = {row["custom_id"]: row for row in calls}
    retry = json.loads((root / "validation_retry_reservation.json").read_text())
    assert retry["status"] == "collected" and retry["authorization"]["source"]
    old = ledger(root / "preserved/pre-transport-retry/validate.db")
    changed = {item["canonical_id"] for item in retry["requests"]}
    for row in old["calls"]:
        current = by_id[row["custom_id"]]
        if row["custom_id"] not in changed:
            assert row == current
        else:
            assert row["status"] == "transport_failed"
            for field in ("request", "req_hash", "attempts", "cost"):
                assert row[field] == current[field]
    assert original["events"][:len(old["events"])] == old["events"]
    for item in retry["requests"]:
        canonical_row, transport = by_id[item["canonical_id"]], by_id[item["retry_id"]]
        a, b = json.loads(canonical_row["request"]), json.loads(transport["request"])
        a.pop("custom_id"); b.pop("custom_id")
        assert a == b and canonical_row["cost"] == 0 and transport["attempts"] == 1
    assert len(canonical) == 757
    assert all(json.loads(row["response"])["extra"].get("raw_response") for row in calls)
    before = V.sha((root / "validate.db").read_bytes())
    results_before = (bench / "validation.jsonl").read_bytes()
    provider_dispatches = []
    def forbidden(*args, **kwargs):
        provider_dispatches.append(True)
        raise RuntimeError("provider call during saved replay")
    live, batch = store.LIVE.copy(), store.BATCH.copy()
    with tempfile.TemporaryDirectory() as tmp:
        replay_root = Path(tmp)
        shutil.copytree(bench, replay_root / "bench")
        shutil.copy2(root / "run_manifest.json", replay_root / "run_manifest.json")
        with closing(sqlite3.connect((root / "validate.db").as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(replay_root / "validate.db")) as target:
                source.backup(target)
        cli.RUN_ROOT, cli.BENCH = replay_root, replay_root / "bench"
        store.LIVE.update({key: forbidden for key in live})
        store.BATCH.update({key: (forbidden, forbidden, forbidden) for key in batch})
        try:
            saved = store.Store(replay_root / "validate.db", "validate", 200, max_transport_attempts=1)
            try:
                requests = [Request(**store._req_fields(json.loads(row["request"]))) for row in calls]
                responses = saved.run(requests, mode="batch", allow_live_fallback=False)
                assert len(responses) == 767
                assert all(asdict(responses[row["custom_id"]]) == json.loads(row["response"]) for row in calls)
            finally:
                saved.db.close()
            preflight.require("validate", replay_root, 8, "batch")
            cli.cmd_validate(SimpleNamespace(mode="batch", workers=8))
            assert results_before == (replay_root / "bench/validation.jsonl").read_bytes()
            assert original == ledger(replay_root / "validate.db")
        finally:
            cli.RUN_ROOT, cli.BENCH = root, bench
            store.LIVE.update(live); store.BATCH.update(batch)
    after = V.sha((root / "validate.db").read_bytes())
    assert before == after and not provider_dispatches
    assert original == ledger(root / "preserved/validation/validate.db")
    rows = [json.loads(line) for line in results_before.decode().splitlines()]
    rejected = []
    for row in rows:
        if row["retained"]:
            continue
        related = [call for call in calls if call["custom_id"].split(":")[1:2] == [row["question_id"]]]
        rejected.append({**row, "response_statuses": {call["custom_id"]: call["status"] for call in related}})
    V.atomic_json(root / "validation_rejections_detail.json", {"questions": rejected,
        "note": "Frozen no_response labels include saved truncated responses; statuses show the actual transport outcome."})
    observed = {provider: sum(row["cost"] for row in calls if spec(row["model"]).provider == provider)
                for provider in ("anthropic", "openai", "together")}
    unknown = retry["original_unobserved_charge_reserve_usd"]
    spend = sum(observed.values())
    known_campaign = manifest["quality_review"]["accounting"]["authoring_spend_usd"] + spend + manifest["access_check_cost_usd"]
    assert spend + unknown < 200 and known_campaign + unknown < 6000
    receipt = {"recorded_utc": datetime.now(timezone.utc).isoformat(), "canonical_responses": 757,
        "qualification_controls": 8, "separately_authorized_retry_rows": 2, "paid_transport_attempts": 767,
        "cached_replay_responses": 767, "provider_dispatches_during_replay": 0,
        "primary_store_sha256_before": before, "primary_store_sha256_after": after,
        "saved_results_sha256": V.sha(results_before), "results_bit_identical": True,
        "observed_provider_costs_usd": observed, "observed_stage_cost_usd": spend,
        "unobserved_original_503_charge_reserve_usd": unknown,
        "conservative_stage_cost_bound_usd": spend + unknown,
        "known_campaign_cost_usd": known_campaign, "conservative_campaign_cost_bound_usd": known_campaign + unknown,
        "billing_note": "Ledger costs use frozen rates and provider token receipts; original 503 billing is unconfirmed. No invoice or cross-project balance reconciliation is claimed.",
        "original_rows_and_events_preserved": True, "wal_backup_verified": True,
        "audit_complete": False, "later_execution_authorized": False}
    V.atomic_json(root / "validation_completion_receipt.json", receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
