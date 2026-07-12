"""Contrasts for the fresh-context (batch) replay vs sequential clean arm.

Question-cluster bootstrap (B=10,000, seed 0) over the 106 questions. Reported in
reports/2026-07-12-mechanism-and-packaging-memo.md.
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


def load():
    main = [json.loads(l) for l in open("rejudge/output/records.jsonl", encoding="utf-8")]
    batch = [json.loads(l) for l in open("rejudge/output/batch_replay.jsonl", encoding="utf-8")]
    rows = [r for r in main if r["arm"] == "clean"] + batch
    qids = sorted({r["question_id"] for r in rows})
    idx = defaultdict(lambda: defaultdict(list))
    for r in rows:
        idx[(r["arm"], r["budget"])][r["question_id"]].append(r)
    return idx, qids


def wrong(rs):
    v = [not r["verdict_correct_strict"] for r in rs if r["verdict_correct_strict"] is not None]
    return 100 * sum(v) / len(v) if v else float("nan")


def main():
    idx, qids = load()

    def stat(fn):
        def build(sample):
            return {k: [r for q in sample for r in d.get(q, [])] for k, d in idx.items()}
        pt = fn(build(qids))
        rng = random.Random(SEED)
        bs = sorted(fn(build([qids[rng.randrange(len(qids))] for _ in qids])) for _ in range(B))
        return pt, bs[int(.025 * B)], bs[int(.975 * B)]

    p = lambda s, a, b: wrong(s[(a, b)])
    print("wrong rate (%) by condition and budget:")
    for arm in ("clean", "batch", "batch_shuffled"):
        parts = [f"b{b}: {wrong([r for q in idx[(a, b)] for r in idx[(a, b)][q]]):.2f}"
                 for a, b in sorted(idx) if a == arm]
        print(f"  {arm:15s} " + " | ".join(parts))

    for b in (1, 2, 5):
        f = (lambda bb: lambda s: p(s, "clean", bb) - p(s, "batch", bb))(b)
        pt, lo, hi = stat(f)
        print(f"P (sequential - batch) at b{b}: {pt:+.2f} pp  CI [{lo:+.2f}, {hi:+.2f}]")
    seq12 = lambda s: (p(s, "clean", 1) + p(s, "clean", 2)) / 2
    bat12 = lambda s: (p(s, "batch", 1) + p(s, "batch", 2)) / 2
    for name, f in [
        ("P pooled {1,2}", lambda s: seq12(s) - bat12(s)),
        ("batch{1,2} - clean b0 (residual content harm)", lambda s: bat12(s) - p(s, "clean", 0)),
        ("shuffled - unshuffled batch at b2", lambda s: p(s, "batch_shuffled", 2) - p(s, "batch", 2)),
        ("packaging share of pooled harm (%)",
         lambda s: 100 * (seq12(s) - bat12(s)) / max(seq12(s) - p(s, "clean", 0), 1e-9)),
        ("packaging minus residual-content component",
         lambda s: (seq12(s) - bat12(s)) - (bat12(s) - p(s, "clean", 0))),
    ]:
        pt, lo, hi = stat(f)
        print(f"{name}: {pt:+.2f}  CI [{lo:+.2f}, {hi:+.2f}]")


if __name__ == "__main__":
    main()
