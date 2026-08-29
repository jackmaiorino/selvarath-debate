"""Outcome-blind cost checkpoints for the fifteenth attempt (amendment 12).

Polls the run's results store and usage ledger, and when a pre-declared boundary is
reached (sequential_b2 complete, sequential_b4 complete, 8 b8 cells, 24 b8 cells),
appends one checkpoint line with cumulative in-ledger spend and a revised bottom-up
finish forecast. Reads ONLY condition labels and spend: never verdicts, decisions, or
any outcome field. Checkpoints alter nothing; the authorized ceiling remains the only
administrative stop rule. Exits when the final report appears or the process is stopped.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ARCHIVE = Path("E:/selvarath-archive/phase3-v3r15-clean-2026-08-29")
RESULTS = ARCHIVE / "phase3_v3_canary_results.jsonl"
LEDGER = ARCHIVE / "phase3_v3_usage.jsonl"
REPORT = ARCHIVE / "phase3_v3_successor_canary_report.json"
OUT = ARCHIVE / "phase3_v3_cost_checkpoints.jsonl"
POLL_SECONDS = 120

# (name, condition, threshold count)
BOUNDARIES = [
    ("b2_complete", "sequential_b2", 96),
    ("b4_complete", "sequential_b4", 96),
    ("b8_early", "sequential_b8", 8),
    ("b8_midpoint", "sequential_b8", 24),
]
PLANNED = {"b0": 192, "sequential_b1": 96, "sequential_b2": 96,
           "sequential_b4": 96, "sequential_b8": 96}


def census() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not RESULTS.exists():
        return counts
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        condition = (row.get("result") or {}).get("condition")
        if condition:
            counts[condition] = counts.get(condition, 0) + 1
    return counts


def spend() -> float:
    total = 0.0
    if not LEDGER.exists():
        return total
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("status") == "success" and event.get("cost_usd") is not None:
            total += float(event["cost_usd"])
    return total


def forecast(counts: dict[str, int], spent: float) -> float:
    """Crude outcome-blind linear forecast: remaining rows priced at the observed
    per-row average, weighted by query budget relative to the observed mix."""
    weights = {"b0": 1.0, "sequential_b1": 1.5, "sequential_b2": 2.0,
               "sequential_b4": 3.5, "sequential_b8": 6.5}
    done_weight = sum(weights[c] * counts.get(c, 0) for c in PLANNED)
    if done_weight <= 0:
        return -1.0
    per_weight = spent / done_weight
    remaining_weight = sum(
        weights[c] * max(0, PLANNED[c] - counts.get(c, 0)) for c in PLANNED)
    return round(spent + per_weight * remaining_weight, 2)


fired: set[str] = set()
while True:
    if REPORT.exists():
        break
    counts = census()
    for name, condition, threshold in BOUNDARIES:
        if name in fired or counts.get(condition, 0) < threshold:
            continue
        fired.add(name)
        spent = spend()
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "checkpoint": name,
            "rows_by_condition": counts,
            "judgment_actual_spend_usd": round(spent, 4),
            "revised_judgment_finish_forecast_usd": forecast(counts, spent),
            "note": "outcome-blind operational checkpoint (amendment 12); alters nothing",
        }
        with OUT.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        print(json.dumps(row), flush=True)
    if len(fired) == len(BOUNDARIES):
        break
    time.sleep(POLL_SECONDS)
print("checkpoint watcher done", flush=True)
