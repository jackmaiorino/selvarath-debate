"""Compute the exact drop set for the REVIEWER_UNAVAILABLE contamination incident.

READ-ONLY forensic tool. It never writes to the archive directory; it only reads
main_reviewer_decisions.jsonl and main_results.jsonl there and writes one JSON
report under analysis_out/ in the repo.

Background: main_reviewer_decisions.jsonl contains reviewer rulings whose raw_output
carries the marker string REVIEWER_UNAVAILABLE (see
rejudge.phase2_canary_live.UNREVIEWED_MARKER and
rebuild_decision_store_dropping_unreviewed for the precedent from an earlier,
canary-scoped incident of the same bug class). Those rulings were fabricated during a
reviewer outage and were never actually reviewed; the dual gate commits them with
status "reviewer_error" and label None, which the gate's effective_allow property
(rejudge/phase2_dual_gate.py) always treats as non-ALLOW. So every query gated by one
of these rulings was denied (blocked) rather than actually adjudicated.

This script finds:
  1. every marked ruling in the decision store (payload_sha256, sequence, row digest);
  2. every main-run result-store cell whose recorded gate events consumed one of those
     rulings ("directly exposed"), found by parsing the result structure (not by
     substring scanning), plus an independent substring cross-check for discrepancies;
  3. the transitive closure of cells that consume a directly-exposed cell's OUTPUT
     (currently: batch_same_qa_b2 cells, which replay their paired sequential_b2
     judgment's exchanges -- see rejudge/phase2_plan.py's depends_on_condition wiring
     and rejudge/phase2_canary_execute.py's _run_single_call/_format_replay), computed
     generically over the plan's dependency graph so any future dependency shape is
     covered without code changes here;
  4. a broader scan of the decision store for any other provenance-suspect rulings.

Binding note (see the task's linkage question): a result row's gate_events entries
record the ruling by reviewer_payload_sha256 ONLY (rejudge/phase2_canary_gate.py,
CanaryQueryGate._record). The decision store's own row digest (event_hash) and
sequence number are never copied into the result. So a result cannot be tied to one
particular row of a hash-chained decision store that has since been rebuilt with a
new ruling under the same payload hash; the safe remediation is to drop every result
that references the payload hash at all, which is what this script computes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.phase2_dual_gate import (  # noqa: E402
    DECISION_STATUSES, DualGateDecisionStore, parse_reviewer_output)
from rejudge.phase2_main_manifest import billable_cells, enumerate_main_cells  # noqa: E402

DEFAULT_MARKER = "REVIEWER_UNAVAILABLE"
HASH_TOKEN_RE = re.compile(r"[0-9a-f]{64}")

# The two operator-acknowledged malformed/label-None dispositions, named in the incident
# brief as known and expected (a contract gap, not contamination). Anything else in this
# shape is reported as a new finding rather than silently absorbed.
KNOWN_MALFORMED_NONE_PREFIXES = ("e2a1dbc57b5b", "4ef8b152aa71")

SUSPICIOUS_KEYWORDS = (
    "timeout", "TimeoutError", "Traceback", "exception", "Exception",
    "exit=", "errno", "ECONNRESET", "ECONNREFUSED", "connection refused",
    "rate limit", "ratelimit", " 429", " 502", " 503", " 529", "5xx",
    "unavailable", "UNAVAILABLE",
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def scan_decision_store(decisions_path: Path, marker: str) -> dict[str, Any]:
    """Read the decision store as plain JSON lines and classify every row.

    Deliberately independent of DualGateDecisionStore's own loader for the per-row
    classification (so a bug in that loader cannot hide an anomaly from this scan),
    but the loader is also run separately below as an integrity cross-check.
    """
    rows: list[dict[str, Any]] = []
    with decisions_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))

    status_counts: Counter[str] = Counter(row.get("status") for row in rows)

    marked_rulings = []
    for row in rows:
        raw_output = str(row.get("raw_output", ""))
        if marker in raw_output:
            marked_rulings.append({
                "payload_sha256": row["payload_sha256"],
                "sequence": row["sequence"],
                "row_digest_event_hash": row["event_hash"],
                "status": row.get("status"),
                "label": row.get("label"),
                "raw_output": raw_output,
            })
    marked_hashes = {r["payload_sha256"] for r in marked_rulings}

    # Anomalous status values (should never occur: the store only ever commits one of
    # DECISION_STATUSES, but this is a plain-JSON re-check, not trust in that fact).
    unknown_status_rows = [
        row["payload_sha256"] for row in rows if row.get("status") not in DECISION_STATUSES
    ]

    # malformed / label-None rows: partition into the two known-expected ones and any others.
    malformed_none_rows = [
        row for row in rows if row.get("status") == "malformed" and row.get("label") is None
    ]
    known_malformed = [
        row["payload_sha256"] for row in malformed_none_rows
        if str(row["payload_sha256"]).startswith(KNOWN_MALFORMED_NONE_PREFIXES)
    ]
    unexpected_malformed = [
        row["payload_sha256"] for row in malformed_none_rows
        if not str(row["payload_sha256"]).startswith(KNOWN_MALFORMED_NONE_PREFIXES)
    ]

    # Label/clause internal-consistency re-check, independent of the store's own
    # commit-time validation: re-parse raw_output and compare against the stored
    # label/clause/rationale, and re-apply the ALLOW-must-cite-Allowed /
    # REJECT-must-cite-P1..P4 rule directly.
    inconsistent = []
    for row in rows:
        label, clause, rationale = row.get("label"), row.get("clause"), row.get("rationale")
        raw_output = str(row.get("raw_output", ""))
        if row.get("status") == "parsed":
            reparsed = parse_reviewer_output(raw_output)
            if reparsed != (label, clause, rationale):
                inconsistent.append({
                    "payload_sha256": row["payload_sha256"], "sequence": row["sequence"],
                    "reason": "stored label/clause/rationale do not match a fresh re-parse "
                              "of raw_output",
                })
                continue
            if label == "ALLOW" and clause != "Allowed":
                inconsistent.append({
                    "payload_sha256": row["payload_sha256"], "sequence": row["sequence"],
                    "reason": f"label ALLOW with clause {clause!r} (expected 'Allowed')",
                })
            elif label == "REJECT" and clause not in {"P1", "P2", "P3", "P4"}:
                inconsistent.append({
                    "payload_sha256": row["payload_sha256"], "sequence": row["sequence"],
                    "reason": f"label REJECT with clause {clause!r} (expected P1..P4)",
                })
        else:
            if label is not None or clause is not None or rationale is not None:
                inconsistent.append({
                    "payload_sha256": row["payload_sha256"], "sequence": row["sequence"],
                    "reason": f"status {row.get('status')!r} but parsed fields are not all null",
                })

    # Suspicious keywords in raw_output, excluding rows already accounted for by the
    # marker (329) and the two known malformed/label-None dispositions.
    excluded_shas = marked_hashes | set(known_malformed)
    suspicious = []
    for row in rows:
        if row["payload_sha256"] in excluded_shas:
            continue
        raw_output = str(row.get("raw_output", ""))
        hits = [kw for kw in SUSPICIOUS_KEYWORDS if kw in raw_output]
        if hits:
            suspicious.append({
                "payload_sha256": row["payload_sha256"], "sequence": row["sequence"],
                "status": row.get("status"), "keywords_matched": hits,
            })

    # Independent chain-integrity check using the production loader itself: sequence
    # contiguity, hash-chain linkage, per-row event_hash, no duplicate payloads, and the
    # same field-consistency rules re-checked above.
    chain_valid, chain_error = True, None
    try:
        DualGateDecisionStore(decisions_path)
    except Exception as exc:  # noqa: BLE001 - report, do not raise; this is forensics
        chain_valid, chain_error = False, f"{type(exc).__name__}: {exc}"

    return {
        "total_rows": len(rows),
        "status_counts": dict(status_counts),
        "marker": marker,
        "marked_rulings": marked_rulings,
        "unknown_status_rows": {"count": len(unknown_status_rows), "payload_sha256": sorted(unknown_status_rows)},
        "malformed_label_none": {
            "known_expected": {"count": len(known_malformed), "payload_sha256": sorted(known_malformed)},
            "unexpected_new": {"count": len(unexpected_malformed), "payload_sha256": sorted(unexpected_malformed)},
        },
        "label_clause_internally_inconsistent": {
            "count": len(inconsistent), "details": inconsistent,
        },
        "suspicious_keyword_rows_excluding_known": {
            "count": len(suspicious), "details": suspicious,
        },
        "chain_integrity": {"valid": chain_valid, "error": chain_error},
    }


def build_dependency_maps(project_root: Path) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """The main plan's cell_key -> cell record, and the reverse dependency edges.

    Reverse edges are built from EVERY dependency_keys entry, not just index >= 1. This
    is deliberately generic rather than hardcoding "batch_same_qa_b2 depends on
    sequential_b2": transcript cells never carry gate_events (they are never in the
    directly-exposed set), so including the transcript edge is harmless, and this stays
    correct if the protocol ever adds another depends_on_condition wiring.
    """
    cells = billable_cells(enumerate_main_cells(str(project_root)))
    by_key = {c["cell_key"]: c for c in cells}
    reverse: dict[str, list[str]] = defaultdict(list)
    for cell in cells:
        for dep in (cell.get("dependency_keys") or ()):
            reverse[dep].append(cell["cell_key"])
    return by_key, reverse


def scan_results(results_path: Path, marked_hashes: set[str]) -> dict[str, Any]:
    """One pass over the result store: structural exposure, substring cross-check,
    and the set of every cell_key currently present (for closing the drop set against
    cells that have actually run)."""
    directly_exposed: dict[str, list[dict[str, Any]]] = {}
    exposed_substring: set[str] = set()
    all_cell_keys: set[str] = set()
    seen_marked_hashes: set[str] = set()
    total_rows = 0

    with results_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            total_rows += 1
            row = json.loads(line)
            cell_key = row["cell_key"]
            all_cell_keys.add(cell_key)
            result = row.get("result") or {}

            hits = []
            for event in (result.get("gate_events") or []):
                payload_sha = event.get("reviewer_payload_sha256")
                if payload_sha in marked_hashes:
                    hits.append({
                        "payload_sha256": payload_sha,
                        "gate_event_sequence": event.get("sequence"),
                        "slot": event.get("slot"), "attempt": event.get("attempt"),
                        "action": event.get("action"),
                        "reviewer_status": event.get("reviewer_status"),
                        "reviewer_label": event.get("reviewer_label"),
                    })
                    seen_marked_hashes.add(payload_sha)
            if hits:
                directly_exposed[cell_key] = hits

            tokens = set(HASH_TOKEN_RE.findall(line))
            hit_tokens = tokens & marked_hashes
            if hit_tokens:
                exposed_substring.add(cell_key)
                seen_marked_hashes.update(hit_tokens)

    structural_set = set(directly_exposed)
    return {
        "total_result_rows": total_rows,
        "all_cell_keys": all_cell_keys,
        "directly_exposed": directly_exposed,
        "structural_set": structural_set,
        "substring_set": exposed_substring,
        "seen_marked_hashes": seen_marked_hashes,
    }


def compute_closure(structural_set: set[str], all_cell_keys: set[str],
                    by_key: dict[str, dict], reverse: dict[str, list[str]]) -> dict[str, Any]:
    """BFS outward from the directly-exposed set over the plan's dependency graph,
    keeping only cells that have actually produced a result row (an unrun cell has
    nothing to drop)."""
    visited = set(structural_set)
    frontier = set(structural_set)
    dependent_edges: dict[str, dict[str, Any]] = {}

    while frontier:
        next_frontier: set[str] = set()
        for parent in frontier:
            for child in reverse.get(parent, ()):
                if child in visited:
                    continue
                visited.add(child)
                if child not in all_cell_keys:
                    # Plan-eligible but not yet executed: nothing recorded to drop for
                    # it, and it will simply never see the stale ruling because the
                    # decision store will be corrected before it runs.
                    continue
                child_cell = by_key.get(child, {})
                dep_keys = list(child_cell.get("dependency_keys") or ())
                dep_index = dep_keys.index(parent) if parent in dep_keys else None
                dependent_edges[child] = {
                    "cell_key": child, "kind": child_cell.get("kind"),
                    "condition": child_cell.get("condition"),
                    "depends_on_cell_key": parent,
                    "depends_on_kind": by_key.get(parent, {}).get("kind"),
                    "depends_on_condition": by_key.get(parent, {}).get("condition"),
                    "dependency_index": dep_index,
                    "reason": (
                        "consumes the contaminated cell's output directly (index "
                        f"{dep_index} of its dependency_keys); see rejudge/phase2_plan.py "
                        "depends_on_condition wiring and phase2_canary_execute.py's "
                        "_run_single_call/_format_replay for the batch_same_qa_b2 case"
                    ),
                }
                next_frontier.add(child)
        frontier = next_frontier

    dependent_cells = set(dependent_edges)
    return {
        "dependent_cells": dependent_cells,
        "dependent_edges": dependent_edges,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", default=r"E:\selvarath-archive\main-2026-08-06")
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    parser.add_argument(
        "--output", default=str(REPO_ROOT / "analysis_out" / "contamination_closure.json"))
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)
    decisions_path = archive_dir / "main_reviewer_decisions.jsonl"
    results_path = archive_dir / "main_results.jsonl"
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print(f"[1/4] scanning decision store: {decisions_path}")
    store_scan = scan_decision_store(decisions_path, args.marker)
    marked_hashes = {r["payload_sha256"] for r in store_scan["marked_rulings"]}
    print(f"      {store_scan['total_rows']} rows, {len(marked_hashes)} marked by "
          f"{args.marker!r}, chain_valid={store_scan['chain_integrity']['valid']}")

    print("[2/4] building main-plan dependency graph")
    by_key, reverse = build_dependency_maps(REPO_ROOT)
    print(f"      {len(by_key)} billable plan cells, "
          f"{sum(len(v) for v in reverse.values())} dependency edges")

    print(f"[3/4] scanning result store: {results_path}")
    result_scan = scan_results(results_path, marked_hashes)
    structural_set = result_scan["structural_set"]
    substring_set = result_scan["substring_set"]
    print(f"      {result_scan['total_result_rows']} result rows, "
          f"{len(structural_set)} structurally exposed, "
          f"{len(substring_set)} substring-matched")

    print("[4/4] computing transitive closure")
    closure = compute_closure(
        structural_set, result_scan["all_cell_keys"], by_key, reverse)
    dependent_cells = closure["dependent_cells"]
    total_drop_set = structural_set | dependent_cells
    print(f"      {len(dependent_cells)} dependent cells; total drop set "
          f"{len(total_drop_set)}")

    missing_marked = marked_hashes - result_scan["seen_marked_hashes"]

    discrepancy = {
        "structural_count": len(structural_set),
        "substring_count": len(substring_set),
        "agree": structural_set == substring_set,
        "structural_only": sorted(structural_set - substring_set),
        "substring_only": sorted(substring_set - structural_set),
    }

    directly_exposed_cells = []
    for cell_key, hits in sorted(result_scan["directly_exposed"].items()):
        cell = by_key.get(cell_key, {})
        directly_exposed_cells.append({
            "cell_key": cell_key, "kind": cell.get("kind"), "condition": cell.get("condition"),
            "question_id": cell.get("question_id"), "judge_model": cell.get("judge_model"),
            "gate_hits": hits,
        })

    dependent_cells_out = [
        closure["dependent_edges"][key] for key in sorted(dependent_cells)
    ]

    report = {
        "schema_version": "contamination_closure_v1",
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "archive_dir": str(archive_dir),
        "marker": args.marker,
        "linkage_note": (
            "Result rows bind a consumed reviewer ruling by payload_sha256 only, via "
            "result.gate_events[].reviewer_payload_sha256 "
            "(rejudge/phase2_canary_gate.py CanaryQueryGate._record). The decision "
            "store's own row digest (event_hash) and sequence number are not copied "
            "into the result, so a result cannot be tied to a specific chain position "
            "of a rebuilt decision store; dropping by payload_sha256 alone is the "
            "unambiguous remediation."
        ),
        "counts": {
            "marked_rulings": len(marked_hashes),
            "directly_exposed_cells": len(structural_set),
            "dependent_cells": len(dependent_cells),
            "total_drop_set": len(total_drop_set),
            "total_result_rows": result_scan["total_result_rows"],
            "total_decision_rows": store_scan["total_rows"],
        },
        "marked_rulings": [
            {k: v for k, v in r.items() if k != "raw_output"}
            for r in store_scan["marked_rulings"]
        ],
        "directly_exposed_cells": directly_exposed_cells,
        "dependent_cells": dependent_cells_out,
        "total_drop_set": sorted(total_drop_set),
        "store_scan": {k: v for k, v in store_scan.items() if k != "marked_rulings"},
        "structural_vs_substring_discrepancy": discrepancy,
        "sanity_checks": {
            "total_decision_store_rows": store_scan["total_rows"],
            "total_result_store_rows": result_scan["total_result_rows"],
            "result_rows_referencing_marked_hash_by_substring": len(substring_set),
            "result_rows_referencing_marked_hash_structurally": len(structural_set),
            "every_marked_hash_seen_in_at_least_one_result_row": len(missing_marked) == 0,
            "marked_hashes_not_seen_in_any_result_row": sorted(missing_marked),
            "plan_billable_cell_count": len(by_key),
            "plan_cells_with_a_result_row": len(result_scan["all_cell_keys"] & set(by_key)),
            "result_rows_with_cell_key_not_in_current_plan": sorted(
                result_scan["all_cell_keys"] - set(by_key)),
        },
        "elapsed_seconds": round(time.time() - t0, 2),
    }

    output_path.write_text(
        json.dumps(report, ensure_ascii=True, indent=1, sort_keys=False) + "\n",
        encoding="utf-8")

    print()
    print("=== summary ===")
    print(f"marked rulings (REVIEWER_UNAVAILABLE): {len(marked_hashes)}")
    print(f"directly exposed cells: {len(structural_set)}")
    print(f"dependent cells (transitive closure): {len(dependent_cells)}")
    print(f"total drop set: {len(total_drop_set)}")
    print(f"structural vs substring agree: {discrepancy['agree']} "
          f"(structural {discrepancy['structural_count']}, "
          f"substring {discrepancy['substring_count']})")
    print(f"every marked hash seen in results: {len(missing_marked) == 0} "
          f"(missing: {len(missing_marked)})")
    print(f"decision store chain_valid: {store_scan['chain_integrity']['valid']}")
    print(f"store_scan anomalies beyond the known two: "
          f"unexpected_malformed={store_scan['malformed_label_none']['unexpected_new']['count']}, "
          f"label_clause_inconsistent={store_scan['label_clause_internally_inconsistent']['count']}, "
          f"suspicious_keyword_rows={store_scan['suspicious_keyword_rows_excluding_known']['count']}, "
          f"unknown_status_rows={store_scan['unknown_status_rows']['count']}")
    print(f"output written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
