"""Derive the pre-registered phase-3 reviewer-throughput fallback question subset.

If the phase-3 canary projects total gate-review volume above the frozen ceiling,
the b4 and b8 arms are subsampled to exactly this subset (with their paired b0
observations analyzed on the same subset). The subset is fixed here, before any
phase-3 outcome exists, by a deterministic seeded draw stratified by
(world, operational resolvability class).

Inputs are frozen phase-2 artifacts: the protocol's calibration-exclusion list
defines the 82 main questions, and the resolvability review's deterministic
reply-pair classes define the strata. Allocation: floor(n_s/2) per stratum, then
largest-fractional-remainder to reach exactly half of the main set, ties broken
by lexicographic stratum key. Within a stratum, question IDs are sorted and drawn
without replacement from a single random.Random(SEED) stream, strata processed in
sorted key order.

Run from the repo root: python scripts/phase3_fallback_subset.py
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

SEED = 20260818

def main() -> None:
    root = Path(__file__).resolve().parent.parent
    protocol = json.loads((root / "rejudge/phase2_protocol.json").read_text(encoding="utf-8"))
    review = json.loads(
        (root / "rejudge/phase2_resolvability_ai_review.json").read_text(encoding="utf-8")
    )

    excluded = set(protocol["question_set"]["calibration_excluded_question_ids"])
    records = {r["question_id"]: r for r in review["records"]}
    main_ids = sorted(qid for qid in records if qid not in excluded)
    assert len(main_ids) == protocol["question_set"]["expected_main_question_count"]

    strata: dict[tuple[str, str], list[str]] = {}
    for qid in main_ids:
        rec = records[qid]
        strata.setdefault((rec["world"], rec["preliminary_class"]), []).append(qid)

    target_total = len(main_ids) // 2
    keys = sorted(strata)
    base = {k: math.floor(len(strata[k]) / 2) for k in keys}
    remainder_order = sorted(keys, key=lambda k: (-(len(strata[k]) / 2 - base[k]), k))
    take = dict(base)
    short = target_total - sum(base.values())
    for k in remainder_order[:short]:
        take[k] += 1

    rng = random.Random(SEED)
    subset: list[str] = []
    for k in keys:
        pool = sorted(strata[k])
        subset.extend(rng.sample(pool, take[k]))
    subset.sort()

    assert len(subset) == target_total
    print(json.dumps({
        "seed": SEED,
        "target_total": target_total,
        "strata": {f"{w}/{c}": {"n": len(strata[(w, c)]), "take": take[(w, c)]}
                   for (w, c) in keys},
        "subset_question_ids": subset,
    }, indent=1))

if __name__ == "__main__":
    main()
