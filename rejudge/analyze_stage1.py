"""Stage-1 gate analysis per docs/rejudge-protocol.md (frozen pre-run).

Primary outcome: strict-parsed verdicts, INVALID excluded and reported.
All contrasts use a question-cluster bootstrap (B=10,000, seed=0), resampling the
106 question_ids with replacement; arm contrasts reuse the same resampled clusters
so differences are paired at the cluster level.
"""
from __future__ import annotations

import io
import json
import random
import sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


B = 10_000
SEED = 0
RECORDS = "rejudge/output/records.jsonl"
PILOT = "data/judgments.jsonl"


def load():
    rows = [json.loads(l) for l in open(RECORDS, encoding="utf-8")]
    by = defaultdict(list)   # (arm, budget) -> rows
    qids = sorted({r["question_id"] for r in rows})
    for r in rows:
        by[(r["arm"], r["budget"])].append(r)
    return rows, by, qids


def wrong_rate(rows, parse="strict"):
    if parse == "strict":
        vals = [not r["verdict_correct_strict"] for r in rows if r["verdict_correct_strict"] is not None]
    else:
        vals = [not r["verdict_correct_pilot"] for r in rows]
    return sum(vals) / len(vals) if vals else float("nan"), len(vals)


def cluster_stat(by, qids, fn, B=B, seed=SEED):
    """Bootstrap fn(cluster_index) over question clusters. fn maps a dict
    {(arm,budget): rows-in-selected-clusters} to a scalar (pp)."""
    idx = {}
    for key, rows in by.items():
        d = defaultdict(list)
        for r in rows:
            d[r["question_id"]].append(r)
        idx[key] = d
    def build(sample):
        return {key: [r for q in sample for r in idx[key].get(q, [])] for key in idx}
    point = fn(build(qids))
    rng = random.Random(seed)
    boots = []
    for _ in range(B):
        sample = [qids[rng.randrange(len(qids))] for _ in qids]
        boots.append(fn(build(sample)))
    boots.sort()
    lo, hi = boots[int(0.025 * B)], boots[int(0.975 * B)]
    return point, lo, hi


def p(sub, arm, budget):
    return wrong_rate(sub[(arm, budget)])[0] * 100


def main():
    rows, by, qids = load()
    n = len(rows)
    invalid = sum(1 for r in rows if r["verdict_strict"]["verdict"] is None)
    disagree = sum(1 for r in rows if r["verdict_strict"]["verdict"] is not None
                   and (r["verdict_strict"]["verdict"] == "A") != (r["verdict_pilot"]["verdict"] == "Position A"))
    print(f"records={n}  clusters={len(qids)}  strict-INVALID={invalid} ({100*invalid/n:.2f}%)  "
          f"dual-parse side disagreements={disagree} ({100*disagree/n:.2f}%)")

    print("\n== Dishonest-win rate (%) by arm x budget, strict parse ==")
    for arm in ("clean", "both", "placebo", "na_only", "doubled_only", "legacy"):
        parts = []
        for (a, b), rr in sorted(by.items()):
            if a == arm:
                w, m = wrong_rate(rr)
                parts.append(f"b{b}: {100*w:.2f} (n={m})")
        print(f"  {arm:13s} " + " | ".join(parts))

    print("\n== Pre-registered gates (question-cluster bootstrap, B=10000, seed=0) ==")
    dfew = lambda s: (p(s, "clean", 1) + p(s, "clean", 2)) / 2 - p(s, "clean", 0)
    pt, lo, hi = cluster_stat(by, qids, dfew)
    g1 = ("HARM SURVIVES" if pt >= 4 and lo > 0 else
          "MOSTLY HARNESS ARTIFACT" if pt <= 2 and lo <= 0 <= hi else "INDETERMINATE")
    print(f"  PRIMARY   clean Δfew = {pt:+.2f} pp  CI [{lo:+.2f}, {hi:+.2f}]  -> {g1}")

    attr = lambda s: ((p(s, "both", 1) + p(s, "both", 2)) / 2
                      - (p(s, "clean", 1) + p(s, "clean", 2)) / 2)
    pt2, lo2, hi2 = cluster_stat(by, qids, attr)
    share = 100 * pt2 / 7.2
    g2 = "MOSTLY HARNESS-INDUCED" if (pt2 >= 3.5 or share > 50) else "BUGS NOT DOMINANT"
    print(f"  ATTRIB    BOTH−CLEAN = {pt2:+.2f} pp  CI [{lo2:+.2f}, {hi2:+.2f}]  "
          f"(~{share:.0f}% of pilot +7.2) -> {g2}")

    delib = lambda s: ((p(s, "placebo", 1) + p(s, "placebo", 2)) / 2
                       - (p(s, "clean", 1) + p(s, "clean", 2)) / 2)
    pt3, lo3, hi3 = cluster_stat(by, qids, delib)
    pvs0 = lambda s: (p(s, "placebo", 1) + p(s, "placebo", 2)) / 2 - p(s, "clean", 0)
    pt4, lo4, hi4 = cluster_stat(by, qids, pvs0)
    g3 = ("DELIBERATION EFFECT" if abs(pt3) <= 2 and pt4 >= 4 else
          "VERIFICATION-CONTENT EFFECT" if pt3 < -2 or pt4 < 4 else "MIXED/CHECK")
    print(f"  DELIB     PLACEBO−CLEAN = {pt3:+.2f} pp  CI [{lo3:+.2f}, {hi3:+.2f}]")
    print(f"            PLACEBO−p(0)  = {pt4:+.2f} pp  CI [{lo4:+.2f}, {hi4:+.2f}]  -> {g3}")

    print("\n== Secondary (reported, not gated) ==")
    rec5 = lambda s: (p(s, "clean", 1) + p(s, "clean", 2)) / 2 - p(s, "clean", 5)
    pt5, lo5, hi5 = cluster_stat(by, qids, rec5)
    print(f"  clean Δrecover5 = {pt5:+.2f} pp  CI [{lo5:+.2f}, {hi5:+.2f}]")
    for arm in ("na_only", "doubled_only"):
        f = (lambda a: lambda s: ((p(s, a, 1) + p(s, a, 2)) / 2
                                  - (p(s, "clean", 1) + p(s, "clean", 2)) / 2))(arm)
        pta, loa, hia = cluster_stat(by, qids, f)
        print(f"  {arm}−CLEAN at {{1,2}} = {pta:+.2f} pp  CI [{loa:+.2f}, {hia:+.2f}]")
    wf = [e.get("well_formed_claim") for r in rows for e in r["exchanges"]
          if r["arm"] in ("clean", "placebo", "na_only")]
    print(f"  well_formed_claim rate (clean-composer arms): {100*sum(bool(x) for x in wf)/len(wf):.1f}%")
    qu = {a: [r["queries_used"] for r in by[(a, b)] for b in ()] for a in ()}  # placeholder no-op
    for arm in ("clean", "placebo"):
        used = [r["queries_used"] for (a, b), rr in by.items() if a == arm and b > 0 for r in rr]
        print(f"  mean queries_used {arm}: {sum(used)/len(used):.2f} (n={len(used)})")

    # legacy vs pilot on the same transcripts/budgets (pilot parser primary for legacy)
    pilot = [json.loads(l) for l in open(PILOT, encoding="utf-8")]
    legacy_keys = {(r["question_id"], r["transcript_index"], r["budget"]) for r in rows if r["arm"] == "legacy"}
    pw = [not j["verdict_correct"] for j in pilot
          if j["judge_model"].endswith("70B-Instruct-Turbo")
          and (j["question_id"], j["transcript_index"], j["query_budget"]) in legacy_keys]
    lw = [not r["verdict_correct_pilot"] for r in rows if r["arm"] == "legacy"]
    print(f"  legacy replay wrong-rate {100*sum(lw)/len(lw):.2f}% (n={len(lw)}) vs pilot on same cells "
          f"{100*sum(pw)/len(pw):.2f}% (n={len(pw)})")


if __name__ == "__main__":
    main()
