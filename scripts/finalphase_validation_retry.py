"""Guarded, separately reserved transport retries for the existing validation cohort.

Prepare is read-only with respect to the ledger and providers. Execute requires
an explicit authorization in the reservation, settled original collection,
unchanged request hashes, funded full-cohort bounds and real qualification.
Measured truncations, refusals and scientific rejections cannot be retried.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from finalphase import cli, preflight, store, validation as V
from finalphase.models import spec
from finalphase.providers import Request, Response


def state(root: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    with closing(sqlite3.connect((root / "validate.db").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM calls")]
    return manifest, rows


def prepare(root: Path) -> dict:
    manifest, rows = state(root)
    _, canonical = V.workload(cli._worlds())
    ids = {r.custom_id for r in canonical}
    failed = [row for row in rows if row["custom_id"] in ids and row["status"] == "transport_failed"]
    requests = []
    for row in failed:
        request = Request(**store._req_fields(json.loads(row["request"])))
        requests.append({"canonical_id": request.custom_id, "request_sha256": row["req_hash"],
            "retry_id": "transport-retry:v1:" + request.custom_id,
            "model": request.model, "original_attempts": row["attempts"],
            "additional_attempts": 1, "maximum_additional_cost_usd": store.estimate_max_cost(request, True)})
    plan = {"status": "prepared_not_authorized", "authorization": {},
            "worlds_sha256": manifest["validation_execution"]["worlds_sha256"],
            "qualification_execution_sha256": manifest["throughput"]["validate"]["execution_sha256"],
            "requests": requests, "maximum_additional_cost_usd": sum(r["maximum_additional_cost_usd"] for r in requests),
            "original_stage_reservation_max_usd": 41.75890856,
            "original_unobserved_charge_reserve_usd": sum(r["maximum_additional_cost_usd"] for r in requests),
            "stage_cap_usd": 200, "campaign_ceiling_usd": 6000,
            "paid_transport_attempts_per_retry_id": 1, "automatic_live_fallback": False}
    path = root / "validation_retry_reservation.json"
    if path.exists() and json.loads(path.read_text()).get("authorization", {}).get("source"):
        raise RuntimeError("preserve the already authorized reservation; do not replace it")
    V.atomic_json(path, plan)
    return plan


def require(root: Path, plan: dict, manifest: dict, rows: list[dict]) -> list[Request]:
    if not plan.get("authorization", {}).get("source"):
        raise RuntimeError("separate paid transport retry authorization is missing")
    if plan.get("status") == "collected":
        raise RuntimeError("only the exact reserved, settled transport failures may be retried; reservation is already collected")
    if (plan["worlds_sha256"] != manifest["validation_execution"]["worlds_sha256"]
            or plan["qualification_execution_sha256"] != preflight.execution_sha256()
            or plan.get("stage_cap_usd") != 200 or plan.get("campaign_ceiling_usd") != 6000):
        raise RuntimeError("retry reservation scope, execution or spending limits changed")
    if manifest["validation_execution"].get("supervisor_exited") is not True:
        raise RuntimeError("original collector has not settled")
    reserved_ids = {item["retry_id"] for item in plan["requests"]}
    if any(row["status"] in ("pending", "submitted", "submitting", "submission_unknown", "live_in_flight")
           and not (row["custom_id"] in reserved_ids and row["status"] == "pending" and row["attempts"] == 0)
           for row in rows):
        raise RuntimeError("in-flight or uncertain calls must settle before transport retry")
    by_id = {row["custom_id"]: row for row in rows}
    requests = []
    expected_reasons = []
    maxima = {}
    for item in plan["requests"]:
        row = by_id[item["canonical_id"]]
        promoted = False
        transport = by_id.get(item["retry_id"])
        if row["status"] in store.MEASURED and transport and transport["status"] in store.MEASURED:
            response = Response(**json.loads(transport["response"]))
            expected_alias = replace(response, custom_id=item["canonical_id"], cost=0,
                extra={**response.extra, "retry_transport_id": item["retry_id"],
                       "charge_recorded_on_retry_row": True})
            promoted = (transport["attempts"] == 1 and row["cost"] == 0
                        and json.loads(row["response"]) == asdict(expected_alias))
        if ((row["status"] != "transport_failed" and not promoted) or row["req_hash"] != item["request_sha256"]
                or row["attempts"] != item["original_attempts"] or item["additional_attempts"] != 1
                or item["retry_id"] != "transport-retry:v1:" + item["canonical_id"]):
            raise RuntimeError("only the exact reserved, settled transport failures may be retried")
        request = Request(**store._req_fields(json.loads(row["request"])))
        maximum = store.estimate_max_cost(request, True)
        if maximum != item["maximum_additional_cost_usd"]:
            raise RuntimeError("retry cost reservation changed")
        provider = spec(request.model).provider
        maxima[provider] = maxima.get(provider, 0) + maximum
        requests.append(replace(request, custom_id=item["retry_id"]))
        if not promoted:
            expected_reasons.append("fresh transport retry reservation required: " + request.custom_id)
    if sum(maxima.values()) != plan["maximum_additional_cost_usd"]:
        raise RuntimeError("retry reservation total changed")
    workers = manifest["throughput"]["validate"]["selected_workers"]
    check = preflight.check("validate", root, workers, "batch")
    # A separately verified reservation satisfies precisely the exhausted-attempt
    # condition. Every other supported launch guard remains binding.
    if sorted(check["reasons"]) != sorted(expected_reasons):
        raise RuntimeError("validation launch guard refused: " + "; ".join(check["reasons"]))
    cohort = V.plan(root, cli._worlds())
    for provider, extra in maxima.items():
        bound = cohort["maximum_cumulative_stage_cost_by_provider_usd"][provider] + extra
        balance = manifest["validation_execution"]["account_balances_at_stage_start"][provider]["available_usd"]
        if bound > balance:
            raise RuntimeError(f"full-cohort {provider} funding does not cover retry")
    if (cohort["maximum_cumulative_stage_cost_usd"] + sum(maxima.values()) > 200
            or plan["original_stage_reservation_max_usd"] + sum(maxima.values()) > 200):
        raise RuntimeError("retry exceeds unchanged validation stage cap")
    return requests


def execute(root: Path) -> dict:
    path = root / "validation_retry_reservation.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    manifest, rows = state(root)
    requests = require(root, plan, manifest, rows)
    preserved = root / "preserved" / "pre-transport-retry"
    preserved.mkdir(parents=True, exist_ok=True)
    if not (preserved / "validate.db").exists():
        with closing(sqlite3.connect((root / "validate.db").resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(preserved / "validate.db")) as destination:
                source.backup(destination)
        V.atomic_json(preserved / "original_failed_rows.json", {"rows": [row for row in rows
            if row["custom_id"] in {item["canonical_id"] for item in plan["requests"]}]})
    plan["status"] = "reserved_before_dispatch"
    V.atomic_json(path, plan)
    workers = manifest["throughput"]["validate"]["selected_workers"]
    ledger = store.Store(root / "validate.db", "validate", 200, max_transport_attempts=1,
                         batch_limits=V.dispatch_limits(root))
    try:
        ledger.log("authorized_transport_retry", json.dumps(plan))
        responses = ledger.run(requests, mode="batch", workers=workers, allow_live_fallback=False)
        if len(responses) != len(requests):
            raise RuntimeError("reserved retry did not complete; preserve it without another dispatch")
        # Atomic promotion avoids a half-updated scientific cohort. A crash
        # after commit but before the JSON receipt can resume from exact aliases
        # and cached transport responses without another paid attempt.
        with ledger._lock, ledger.db:
            ledger.db.execute("BEGIN IMMEDIATE")
            for item in plan["requests"]:
                response = responses[item["retry_id"]]
                alias = replace(response, custom_id=item["canonical_id"], cost=0,
                                extra={**response.extra, "retry_transport_id": item["retry_id"],
                                       "charge_recorded_on_retry_row": True})
                if ledger.get(item["canonical_id"]) == alias:
                    continue
                ledger._save(alias, attempts_inc=0)
                ledger.log("canonical_retry_alias", f"{item['canonical_id']} -> {item['retry_id']}")
        plan.update(status="collected", response_statuses={cid: response.status for cid, response in responses.items()},
                    observed_retry_cost_usd=sum(response.cost for response in responses.values()))
        V.atomic_json(path, plan)
        return plan
    finally:
        ledger.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute"))
    args = parser.parse_args()
    report = prepare(cli.RUN_ROOT) if args.action == "prepare" else execute(cli.RUN_ROOT)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
