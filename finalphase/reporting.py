"""Validation receipts and the unpaid, pending coordinator audit packet."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

from . import authoring as A
from .providers import Response


def write_report(root: Path, rows: list[dict], worlds: list[dict], responses: dict[str, Response],
                 complete: bool, spend: float) -> dict:
    from .validation import atomic_json, sha

    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    retained = [row for row in rows if row["retained"]]
    groups = {}
    for dimension in ("world_id", "author", "task_type"):
        groups[dimension] = {}
        for value in sorted({row[dimension] for row in rows}):
            subset = [row for row in rows if row[dimension] == value]
            groups[dimension][value] = {"candidates": len(subset),
                "mechanical_rejected": sum(not row["mechanical_ok"] for row in subset),
                "retained": sum(row["retained"] for row in subset),
                "validation_rejected": sum(row["mechanical_ok"] and not row["retained"] for row in subset),
                "rejection_reasons": dict(Counter(reason for row in subset if not row["retained"]
                                                   for reason in row["reasons"]))}
    report = {"independent_answer_validation_complete": complete, "audit_complete": False,
              "stage_spend_usd": spend, "retained": len(retained), "groups": groups,
              "mechanical_rejections": [row for row in rows if not row["mechanical_ok"]],
              "validation_rejections": [row for row in rows if row["mechanical_ok"] and not row["retained"]]}
    atomic_json(root / "validation_summary.json", report)
    # The fixed split seed provides reproducible random selection without looking
    # at any later judge outcomes. Controls are absent from rows and selection.
    seed = manifest.get("seeds", {}).get("split", "final-phase-split-v1") + ":coordinator-audit"
    selected = sorted(retained, key=lambda row: hashlib.sha256(
        (seed + ":" + row["question_id"]).encode()).digest())[:math.ceil(len(retained) * .05)]
    audit_ids = {row["question_id"] for row in selected}
    disagreement_ids = set()
    for row in rows:
        decisions = set()
        for validator in A.validators_for(row["author"]):
            for order in ("key_a", "key_b"):
                response = responses.get(f"validate:{row['question_id']}:{validator}:{order}")
                if response and response.status == "ok":
                    answer = A.parse_validation(response.text)["answer"]
                    if answer in ("A", "B"):
                        decisions.add(answer == ("A" if order == "key_a" else "B"))
        if len(decisions) > 1:
            disagreement_ids.add(row["question_id"])
    audit_ids.update(disagreement_ids)
    packet = {"status": "pending_independent_coordinator_review" if complete else "awaiting_complete_validation",
              "owner": "Claude coordinating session, reports/final-phase-2026-09-30/design.md:55",
              "audit_complete": False, "paid_audit_authorized": False, "seed": seed,
              "random_retained_sample_ids": [row["question_id"] for row in selected],
              "split_validator_ids": sorted(disagreement_ids),
              "required_followup": "Record a per-question review; any retained source defect requires review of every retained question from that world.",
              "worlds_sha256": manifest["validation_execution"]["worlds_sha256"],
              "questions": [row for row in rows if row["question_id"] in audit_ids],
              "world_artifacts": {world["world_id"]: str(root / "bench/worlds" / f"{world['world_id']}.json") for world in worlds},
              "response_ledger": str(root / "validate.db"),
              "validation_rows_sha256": sha((root / "bench/validation.jsonl").read_bytes())}
    atomic_json(root / "validation_audit_packet.json", packet)
    # SQLite's online backup includes committed WAL contents.
    target = root / "preserved" / "validation" / "validate.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect((root / "validate.db").resolve().as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return report
