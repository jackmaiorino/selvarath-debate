"""Plan or execute separately approved scratch qualification, never expansion.

Default is read-only planning. Execution requires exact scope, funding, capacity,
placement inventory and the independent coordinator audit. No production token
allowance or retained decision is changed.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from finalphase import authoring as A, cli, expansion as E, preflight, store, validation as V
from finalphase.limits import BatchLimits
from finalphase.models import spec
from finalphase.providers import Request


def plan(root: Path) -> dict:
    costs = json.loads((root / "benchmark_expansion_costs.json").read_text(encoding="utf-8"))
    tokens = json.loads((root / "validation_token_v2_proposal.json").read_text(encoding="utf-8"))
    raw = costs["author_qualification_requests"] + tokens["requests"]
    requests = [Request(**store._req_fields(item)) for item in raw]
    from scripts.finalphase_expansion_prepare import token_controls
    expected_authors = E.author_qualification_controls()
    expected = expected_authors + token_controls(root, cli._worlds())
    if [r.to_json() for r in requests] != [r.to_json() for r in expected]:
        raise RuntimeError("scratch proposal differs from the versioned frozen control specification")
    maxima = {p: sum((E.maximum(r, True) for r in requests if spec(r.model).provider == p), Decimal(0))
              for p in ("anthropic", "openai", "together")}
    packet = json.loads((root / "validation_audit_packet.json").read_text(encoding="utf-8"))
    return {"version": E.QUALIFICATION_VERSION, "request_count": len(requests),
            "requests_sha256": E.digest(json.dumps([r.to_json() for r in requests]).encode()),
            "maximum_cost_by_provider_usd": {p: str(n) for p, n in maxima.items()},
            "maximum_cost_usd": str(sum(maxima.values())), "requests": raw,
            "worlds_sha256": packet["worlds_sha256"], "validation_rows_sha256": packet["validation_rows_sha256"],
            "execution_sha256": E.digest((preflight.execution_sha256()
                + E.digest(Path(__file__).read_bytes())
                + E.digest((REPO / "scripts/finalphase_expansion_prepare.py").read_bytes())).encode()), "paid_execution_authorized": False,
            "production_protocol_changed": False, "retention_contribution": False,
            "provider_client_created": False}


def guard(root: Path, proposal: dict, approval: dict) -> None:
    reasons = E.audit_reasons(root)
    if any((p / "STOP").exists() for p in (root, REPO)):
        reasons.append("STOP file is present")
    if (approval.get("approved") is not True or not approval.get("source")
            or approval.get("scope") != proposal["version"]
            or any(approval.get(k) != proposal[k] for k in ("requests_sha256", "execution_sha256", "worlds_sha256", "validation_rows_sha256"))
            or approval.get("maximum_cost_usd") != proposal["maximum_cost_usd"]):
        reasons.append("exact unpaid-prepared qualification scope lacks separate paid approval")
    for provider, bound in proposal["maximum_cost_by_provider_usd"].items():
        capacity = approval.get("provider_capacity", {}).get(provider, {})
        amount = capacity.get("available_usd")
        if not (type(amount) in (int, float) and math.isfinite(amount) and amount >= float(bound)
                and capacity.get("reference") and capacity.get("reconciled") is True):
            reasons.append(f"{provider}: full qualification-cohort credit is unconfirmed or insufficient")
    limits = approval.get("openai_capacity", {})
    for key in ("organization_approved_remaining_usd", "organization_hard_limit_remaining_usd", "project_hard_limit_remaining_usd"):
        amount = limits.get(key)
        disabled = key != "organization_approved_remaining_usd" and limits.get(key.replace("remaining_usd", "enabled")) is False
        if not disabled and not (type(amount) in (int, float) and math.isfinite(amount)
                                 and amount >= float(proposal["maximum_cost_by_provider_usd"]["openai"])):
            reasons.append(f"OpenAI {key} is unconfirmed or insufficient")
    if not limits.get("reference") or limits.get("queue_confirmed") is not True:
        reasons.append("OpenAI capacity and queue reference missing")
    placement = approval.get("placements", {})
    if placement.get("host") != platform.node() or placement.get("checked") != ["Jack's PC", "HaleysPC", "RunPod"] or not placement.get("reference"):
        reasons.append("current three-placement inventory missing")
    completion = json.loads((root / "validation_completion_receipt.json").read_text(encoding="utf-8"))
    if Decimal(str(completion["conservative_campaign_cost_bound_usd"])) + Decimal(proposal["maximum_cost_usd"]) > 6000:
        reasons.append("qualification exceeds unchanged campaign ceiling")
    author_spend = Decimal(str(completion["known_campaign_cost_usd"])) - Decimal(str(completion["observed_stage_cost_usd"]))
    if author_spend + sum(Decimal(proposal["maximum_cost_by_provider_usd"][p]) for p in ("anthropic", "openai")) > Decimal(str(cli.STAGE_CAPS["author"])):
        reasons.append("qualification exceeds the author cap")
    if Decimal(str(completion["conservative_stage_cost_bound_usd"])) + Decimal(proposal["maximum_cost_by_provider_usd"]["together"]) > Decimal(str(cli.STAGE_CAPS["validate"])):
        reasons.append("qualification exceeds the validation cap")
    # Scratch input and version are rechecked against the actual frozen source, not an editable proposal list.
    worlds = cli._worlds()
    hashes = {w["world_id"]: E.digest((root / "bench/worlds" / f"{w['world_id']}.json").read_bytes()) for w in worlds}
    if hashes != proposal["worlds_sha256"] or E.digest((root / "bench/validation.jsonl").read_bytes()) != proposal["validation_rows_sha256"]:
        reasons.append("qualification baseline differs from frozen worlds or validation rows")
    if reasons:
        raise RuntimeError("qualification refused: " + "; ".join(reasons))


def execute(root: Path, proposal: dict, approval: dict) -> dict:
    guard(root, proposal, approval)  # before Store creation or networking
    path = root / E.QUALIFICATION_RECEIPT
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "requests_sha256": proposal["requests_sha256"], "execution_sha256": proposal["execution_sha256"],
        "production_protocol_changed": False, "retention_contribution": False}
    if any(record.get(k) != proposal[k] for k in ("requests_sha256", "execution_sha256")):
        raise RuntimeError("existing qualification identity is incompatible; preserve it")
    requests = [Request(**store._req_fields(r)) for r in proposal["requests"]]
    s = store.Store(root / E.QUALIFICATION_DB, "qualification", float(proposal["maximum_cost_usd"]),
                    max_transport_attempts=1, batch_limits={m: BatchLimits(1500000, 100) for m in ("astra", "fable")})
    try:
        # Reserve all author and token controls before the first provider dispatch, including on resume.
        s._check_cap([r for r in requests if s.get(r.custom_id) is None], True)
        for role in ("author", "token"):
            for arm, workers in (("serial", 1), ("parallel", 8)):
                name = f"{role}_{arm}"
                subset = [r for r in requests if (f":{E.AUTHOR_QUALIFICATION_SCOPE}:" in r.custom_id) == (role == "author") and f":{arm}:" in r.custom_id]
                if name in record and "valid" in record[name]:
                    if record[name]["valid"] is not True:
                        raise RuntimeError("qualification failed; no paid retry is authorized")
                    continue
                if name not in record and any(s.get(r.custom_id) is not None for r in subset):
                    raise RuntimeError("cached calls lack timing receipt; replay cannot qualify throughput")
                started = record.setdefault(name, {"started_epoch": time.time()})["started_epoch"]
                V.atomic_json(path, record)
                if arm == "serial":
                    out = {}
                    for request in subset:
                        out.update(s.run([request], mode="batch", workers=1, poll_s=30, allow_live_fallback=False))
                else:
                    out = s.run(subset, mode="batch", workers=8, poll_s=30, allow_live_fallback=False)
                valid = len(out) == len(subset) and all(out[r.custom_id].status == "ok" and out[r.custom_id].model_id == spec(r.model).model_id for r in subset)
                if valid:
                    valid = all(A.world_check(A.parse_json_object(out[r.custom_id].text)).ok for r in subset) if role == "author" else all(V._valid_response(r, out[r.custom_id]) for r in subset)
                record[name] = {"workers": workers, "completed": len(out), "elapsed_seconds": time.time() - started,
                                "input_sha256": V.input_sha256(subset), "output_sha256": E.digest(json.dumps([asdict(out[r.custom_id]) for r in subset if r.custom_id in out], sort_keys=True).encode()),
                                "valid": valid, "parallelism": "remote frontier batches" if role == "author" else "Together live workers"}
                V.atomic_json(path, record)
                if not valid:
                    raise RuntimeError("qualification failed acceptance; no paid retry or token escalation authorized")
        record["complete"] = True
        record["selected_token_workers"] = 8 if record["token_parallel"]["elapsed_seconds"] < record["token_serial"]["elapsed_seconds"] else 1
        record["selected_author_parallel_requests"] = 2 if record["author_parallel"]["elapsed_seconds"] < record["author_serial"]["elapsed_seconds"] else 1
        record["observed_cost_usd"] = s.spent()
        record["next_action"] = "Assess qualification acceptance, then obtain explicit expansion/protocol/allocation approval; do not launch automatically"
        V.atomic_json(path, record)
        return record
    finally:
        s.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "execute"), default="plan", nargs="?")
    parser.add_argument("--approval", type=Path)
    args = parser.parse_args()
    proposal = plan(cli.RUN_ROOT)
    if args.action == "plan":
        print(json.dumps({k: v for k, v in proposal.items() if k != "requests"}, indent=2))
    else:
        if args.approval is None:
            parser.error("execution requires --approval with separate explicit paid qualification authorization")
        print(json.dumps(execute(cli.RUN_ROOT, proposal, json.loads(args.approval.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
