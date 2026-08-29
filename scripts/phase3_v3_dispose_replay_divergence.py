"""Write one amendment-12 terminal record for an ACTUALLY diverged cell, with guards.

Given the cell key from a CallReplayMismatch halt, extracts the mechanical evidence from
the run's hash-chained cache and ledger and writes the next terminal-halts record. It
REFUSES (exit 2) whenever the evidence does not exactly match the amendment-12 pattern:
an unmemoized truncated originating call, an old-generation cached downstream row, and a
later divergent re-dispatch. Exit 3 when the cumulative terminal count would exceed the
frozen bound (completion gate fails; no amendment may raise it).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ARCHIVE = Path("E:/selvarath-archive/phase3-v3r15-clean-2026-08-29")
RUN_ID = "phase3-v3-82c8f75feba42a9e"
AMENDMENT_SHA = "541d8b64c27a48284513b2837b5a73030a9e7445e7db953f6d6f1cb2f7d4955a"
QUERY_CAP = 4096
MAX_TERMINAL = 20


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", required=True)
    args = parser.parse_args()
    cell = args.cell

    rows = [json.loads(l) for l in (ARCHIVE / "phase3_v3_canary_results.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    if any(r.get("cell_key") == cell for r in rows):
        print("REFUSED: cell has a committed result row")
        return 2

    existing = sorted(REPO_ROOT.glob("rejudge/phase3_v3_terminal_halts_*.json"))
    run_records = [
        p for p in existing
        if json.loads(p.read_text(encoding="utf-8")).get("run_id") == RUN_ID]
    if len(run_records) + 1 > MAX_TERMINAL:
        print("BOUND: cumulative terminal cells would exceed the frozen bound of 20")
        return 3
    for p in run_records:
        if json.loads(p.read_text(encoding="utf-8")).get("cell_key") == cell:
            print("REFUSED: cell already has a terminal record")
            return 2

    cache = [json.loads(l) for l in (ARCHIVE / "phase3_v3_call_cache.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    mine = sorted((r for r in cache if r["cell_key"] == cell),
                  key=lambda r: r["sequence"])
    ledger = [json.loads(l) for l in (ARCHIVE / "phase3_v3_usage.jsonl")
              .read_text(encoding="utf-8").splitlines()]

    unmemoized = [
        e["sequence"] for e in ledger
        if e.get("status") == "success"
        and (e.get("metadata") or {}).get("cell_key") == cell
        and (e.get("metadata") or {}).get("call_role") == "judge_query"
        and e.get("completion_tokens") == QUERY_CAP]
    old_oracle = [r for r in mine if r["call_role"] == "oracle_verification"]
    old_retry_query = [r for r in mine
                       if r["call_role"] == "judge_query" and r["attempt"] >= 2]
    divergent_attempt1 = [
        r for r in mine
        if r["call_role"] == "judge_query" and r["attempt"] == 1 and r["slot"] == 0
        and old_retry_query and r["sequence"] > old_retry_query[0]["sequence"]]

    if not (unmemoized and old_retry_query and divergent_attempt1):
        print("REFUSED: evidence does not match the amendment-12 pattern; "
              "orchestrator review required")
        print(json.dumps({
            "unmemoized": unmemoized,
            "old_oracle": [r["sequence"] for r in old_oracle],
            "old_retry_query": [r["sequence"] for r in old_retry_query],
            "divergent_attempt1": [r["sequence"] for r in divergent_attempt1]}))
        return 2

    divergent = divergent_attempt1[0]
    # The collision point follows mechanically from the NEW generation's cached checker
    # token (orchestration state, not an outcome field): an allow proceeds to the oracle
    # and collides with the old-generation oracle row; a reject consumes the retry and
    # collides with the old-generation retry-query row. Anything else is refused for
    # orchestrator review rather than guessed.
    new_checkers = [r for r in mine
                    if r["call_role"] == "query_checker"
                    and r["sequence"] > divergent["sequence"]]
    checker_token = new_checkers[-1]["response"].strip() if new_checkers else None
    if checker_token == "allow" and old_oracle:
        mismatched = old_oracle[0]
    elif checker_token == "reject":
        mismatched = old_retry_query[0]
    else:
        print("REFUSED: new-generation checker state does not yield a mechanical "
              "collision point; orchestrator review required")
        print(json.dumps({"checker_token": checker_token,
                          "old_oracle": [r["sequence"] for r in old_oracle]}))
        return 2
    index = len(existing) + 1
    stamp = datetime.now(timezone.utc)
    out = REPO_ROOT / (
        f"rejudge/phase3_v3_terminal_halts_{index:03d}_{stamp:%Y-%m-%d}.json")
    record = {
        "schema_version": "phase3_v3_terminal_halts_v1",
        "record_id": out.stem,
        "run_id": RUN_ID,
        "cell_key": cell,
        "reason": "replay_divergence",
        "evidence": {
            "mismatched_cache_sequence": mismatched["sequence"],
            "mismatched_request_sha256": mismatched["request_sha256"],
            "divergent_cache_sequence": divergent["sequence"],
            "unmemoized_ledger_sequences": unmemoized,
            "provenance": (
                "mechanically extracted per amendment 12: judge query truncated at the "
                f"{QUERY_CAP} cap on {len(unmemoized)} unmemoized dispatch(es); the "
                "gate's cached retry generation includes the oracle row above; a "
                "post-relaunch re-dispatch completed non-deterministically (divergent "
                "cache row above) so the oracle recomposition permanently mismatches"),
            "result_row_absent_verified": True,
        },
        "reviewer": (
            "orchestrator session 3cf5f3a5-3285-4fbd-9cf1-16e43f5458c5 under the "
            "amendment-12 disposition; evidence mechanically extracted and pattern-"
            "gated by scripts/phase3_v3_dispose_replay_divergence.py; no retry-shopping"),
        "recorded_at_utc": stamp.isoformat().replace("+00:00", "Z"),
        "frozen_policy_citation": (
            "rejudge/phase3_v3_amendment12_replay_divergence_2026-08-29.json (canonical "
            f"{AMENDMENT_SHA}): whole-mirror-unit exclusion, INVALID in the primary, "
            "CUMULATIVE amendment-5 bounds never relaxable, no bound-raising amendment"),
        "disposition_notes": (
            f"terminal cell {len(run_records) + 1} of {MAX_TERMINAL} (cumulative bound) "
            "for this run; content-correlation disclosed per amendment 12"),
        "execution_authorized": False,
    }
    with out.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=1, ensure_ascii=True)
        handle.write("\n")
    print(json.dumps({"written": out.name, "cumulative": len(run_records) + 1}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
