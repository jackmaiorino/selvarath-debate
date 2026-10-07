"""Read-only expansion reservations and independent-audit launch guards.

The token amendment is a proposal. No production request uses its allowance.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from . import authoring as A
from .limits import BatchLimits
from .models import spec
from .providers import Request
from .store import MEASURED, estimate_max_cost_decimal

TOKEN_PROPOSAL = "validation-token-v2-proposed"
PROPOSED_DSPRO_KEY_TOKENS = 12000
COORDINATOR_SESSION = "cd916c75-3534-4496-8864-d4b4f53f3c36"
# v1 failed acceptance on 2026-10-06 (Fable reached 64,000 output tokens); its receipt,
# store and logs are preserved. v2 controls use the amended expansion author allowance.
AUTHOR_QUALIFICATION_SCOPE = "author-expansion-v2"
QUALIFICATION_VERSION = "benchmark-expansion-qualification-v2"
QUALIFICATION_RECEIPT = "expansion_qualification_v2_receipt.json"
QUALIFICATION_DB = "expansion_qualification_v2.db"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def maximum(request: Request, batch: bool) -> Decimal:
    """Same UTF-8 input bound as Store, using exact decimal frozen rates."""
    return estimate_max_cost_decimal(request, batch)


def audit_reasons(root: Path) -> list[str]:
    try:
        packet_path = root / "validation_audit_packet.json"
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        review = json.loads((root / "coordinator_audit_review.json").read_text(encoding="utf-8"))
        rows_path = root / "bench/validation.jsonl"
        if (review.get("audit_complete") is not True or review.get("reviewer") != "Claude coordinating session"
                or review.get("session_id") != COORDINATOR_SESSION
                or review.get("implementer_review") is not False
                or review.get("packet_sha256") != digest(packet_path.read_bytes())
                or review.get("validation_rows_sha256") != digest(rows_path.read_bytes())):
            return ["independent Claude audit is incomplete or belongs to different evidence"]
        rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
        required = set(packet["random_retained_sample_ids"] + packet["split_validator_ids"])
        defects = review.get("retained_source_defect_worlds", [])
        required.update(row["question_id"] for row in rows if row["retained"] and row["world_id"] in defects)
        reviews = {item["question_id"]: item for item in review["questions"]}
        if not required or not required.issubset(reviews):
            return ["Claude audit lacks sampled, split or defect-world question reviews"]
        for world_id, expected in packet["worlds_sha256"].items():
            if digest((root / "bench/worlds" / f"{world_id}.json").read_bytes()) != expected:
                return ["Claude audit world hashes differ from current benchmark"]
        if any(not reviews[q].get("evidence") or not reviews[q].get("conclusion") for q in required):
            return ["Claude audit lacks per-question evidence and conclusions"]
        if defects and review.get("source_defects_resolved") is not True:
            return ["retained source defect requires coordinator disposition before expansion"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["independent Claude audit receipt is missing or invalid"]
    return []


def author_qualification_controls() -> list[Request]:
    """Matched serial/parallel scratch author controls at the expansion allowance."""
    from dataclasses import replace
    return [replace(A.author_request(wid, author, hint, max_tokens=A.EXPANSION_AUTHOR_MAX_TOKENS_BY_MODEL[author]),
                    custom_id=f"qualification:{AUTHOR_QUALIFICATION_SCOPE}:{arm}:{wid}:{author}")
            for arm in ("serial", "parallel") for wid, author, hint in A.world_ids(2)]


def author_requests(root: Path, worlds: int = 160, attempts: int = 2, baseline: dict | None = None) -> list[Request]:
    if worlds <= 0 or attempts not in (1, 2):
        raise ValueError("positive world scope and one or two author attempts required")
    requests = []
    for wid, author, hint in A.world_ids(worlds):
        path = root / "bench/worlds" / f"{wid}.json"
        if path.exists():
            obj = json.loads(path.read_text(encoding="utf-8"))
            if not A.world_check(obj).ok or (obj.get("world_id"), obj.get("author"), obj.get("seed_hint")) != (wid, author, hint):
                raise ValueError(f"invalid saved world must be separately resolved: {wid}")
            if baseline is None or wid in baseline:
                if baseline is not None and digest(path.read_bytes()) != baseline[wid]:
                    raise ValueError(f"saved baseline world changed: {wid}")
                continue
        elif baseline is not None and wid in baseline:
            raise ValueError(f"saved baseline world missing: {wid}")
        requests.extend(A.author_request(wid, author, hint, attempt=i) for i in range(attempts))
    return requests


def author_plan(root: Path, worlds: int = 160, attempts: int = 2, mode: str = "batch") -> dict:
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    approval = manifest.get("author_expansion_authorization", {})
    requests = author_requests(root, worlds, attempts, approval.get("baseline_worlds_sha256"))
    reasons = audit_reasons(root)
    recorded = {p: Decimal(0) for p in ("anthropic", "openai")}
    states = {}
    outstanding = []
    ledger = root / "author.db"
    if ledger.exists():
        with closing(sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            for model, cost in db.execute("SELECT model,cost FROM calls"):
                recorded[spec(model).provider] += Decimal(str(cost))
            states = {cid: (status, sha) for cid, status, sha in db.execute("SELECT custom_id,status,req_hash FROM calls")}
            outstanding = [Request(**json.loads(raw)) for raw, status in db.execute("SELECT request,status FROM calls") if status not in MEASURED]
    planned_ids = {r.custom_id for r in requests}
    for request in outstanding:
        if request.custom_id not in planned_ids:
            reasons.append(f"outstanding author request outside approved scope: {request.custom_id}")
    remaining = {p: Decimal(0) for p in recorded}
    for request in requests:
        state = states.get(request.custom_id)
        if state and state[1] != digest(request.to_json().encode("utf-8")):
            reasons.append(f"request changed under existing ID: {request.custom_id}")
        if state and state[0] in MEASURED:
            continue
        world_path = root / "bench/worlds" / (request.custom_id.split(":")[1] + ".json")
        if world_path.exists() and not state:
            continue  # a permitted earlier generation already admitted this world
        if state and state[0] not in ("pending", "submitted"):
            reasons.append(f"author identity needs reconciliation: {request.custom_id}")
        remaining[spec(request.model).provider] += maximum(request, mode == "batch")
    body_sha = digest(json.dumps([r.to_json() for r in requests]).encode())
    cumulative = sum(recorded.values()) + sum(remaining.values())
    if cumulative > Decimal(200):
        reasons.append(f"author full-cohort reservation ${cumulative} exceeds unchanged $200 stage cap")
    if (approval.get("approved") is not True or not approval.get("source")
            or approval.get("worlds") != worlds or approval.get("attempts") != attempts
            or approval.get("mode") != mode or approval.get("requests_sha256") != body_sha
            or not approval.get("baseline_worlds_sha256")):
        reasons.append("exact expanded author scope is not approved")
    for provider, bound in remaining.items():
        account = approval.get("provider_capacity", {}).get(provider, {})
        amount = account.get("available_usd")
        if bound and not (type(amount) in (int, float) and math.isfinite(amount)
                         and amount >= float(bound) and account.get("reference") and account.get("reconciled") is True):
            reasons.append(f"{provider}: reconciled full-cohort credit capacity is missing or insufficient")
    prior_stage = manifest.get("validation_execution", {}).get("conservative_stage_cost_bound_usd", 0)
    if cumulative + Decimal(str(prior_stage)) + Decimal(str(manifest.get("access_check_cost_usd", 0))) > 6000:
        reasons.append("expanded authoring exceeds unchanged campaign ceiling")
    limits = approval.get("openai_capacity", {})
    if remaining["openai"]:
        for field in ("organization_approved_remaining_usd", "organization_hard_limit_remaining_usd", "project_hard_limit_remaining_usd"):
            value = limits.get(field)
            disabled = field != "organization_approved_remaining_usd" and limits.get(field.replace("remaining_usd", "enabled")) is False
            if not disabled and not (type(value) in (int, float) and math.isfinite(value) and value >= float(remaining["openai"])):
                reasons.append(f"OpenAI {field} is unconfirmed or insufficient")
        if not limits.get("reference"):
            reasons.append("OpenAI capacity reference is missing")
    limits = BatchLimits(max_input_tokens=1500000, max_requests=100)
    waves = {model: len(limits.waves([r for r in requests if r.model == model])) for model in ("fable", "astra")}
    return {"ready": not reasons, "reasons": reasons, "worlds": worlds, "attempts": attempts,
            "request_count": len(requests), "requests_sha256": body_sha,
            "remaining_maximum_by_provider_usd": {p: float(n) for p, n in remaining.items()},
            "cumulative_author_maximum_usd": float(cumulative), "batch_waves": waves,
            "provider_client_created": False, "execution_started": False}


def require_author(root: Path, args) -> None:
    if getattr(args, "only", None) or getattr(args, "max_tokens", None) or getattr(args, "replace_invalid", False):
        raise RuntimeError("expanded authoring must match the approved complete scope and frozen author defaults")
    plan = author_plan(root, args.worlds, getattr(args, "attempts", 2), args.mode)
    if not plan["ready"]:
        raise RuntimeError("author expansion refused: " + "; ".join(plan["reasons"]))
