"""Blinded power simulation for the phase-3 budget-knob design.

Uses the phase-2 result store only for its question-level ERROR STRUCTURE (per-cell
b0 rates and the observed cell-level treatment heterogeneity between b0 and
sequential_b2). No phase-3 outcome exists; scenario curves are fixed ex ante here,
before the phase-3 protocol freezes, per the 2026-08-14 design consult.

Simulates the proposed grid (82 questions x 7 judges x 2 debaters x 6 slots x
budgets {0,1,2,4,8}), runs the actual estimator shape (four Delta(b) contrasts vs
b0, world-stratified question bootstrap, Holm-4), and reports rejection rates.

The three new judges have no phase-2 profile; they are cloned from the nearest
existing judge (8B-Lite and 3n-E4B from Qwen2.5-7B, the weakest; Qwen3.7-Max from
gpt-oss-120b, the strongest solo-QA anchor) with independent cell noise. This is
recorded as an assumption, conservative in the sense that clones add no new
question-level information.
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.phase2_main_analysis import holm  # same step-down, same tie rule

BUDGETS = (1, 2, 4, 8)
SLOTS = 6
ALPHA = 0.05
SCENARIOS = {
    "flat_harm":      {1: 0.04, 2: 0.04, 4: 0.04, 8: 0.04},
    "monotone_worse": {1: 0.02, 2: 0.04, 4: 0.06, 8: 0.08},
    "u_recovery":     {1: 0.03, 2: 0.04, 4: 0.01, 8: -0.02},
    "small_effects":  {1: 0.01, 2: 0.02, 4: 0.02, 8: 0.01},
    "null":           {1: 0.0, 2: 0.0, 4: 0.0, 8: 0.0},
}
CLONES = {"llama8b_lite": "Qwen/Qwen2.5-7B-Instruct-Turbo",
          "gemma3n_e4b": "Qwen/Qwen2.5-7B-Instruct-Turbo",
          "qwen37_max": "openai/gpt-oss-120b"}


def load_cells(archive: Path) -> tuple[dict, dict, float]:
    """Per (question, judge, debater): observed b0 error and b2-b0 shift, plus the
    heterogeneity SD of that shift around its mean (the realism knob)."""
    rates = defaultdict(dict)
    world_of = {}
    for line in (archive / "main_results.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["cell_key"].split(":")[1] != "debate_judgment":
            continue
        res = row["result"]
        if res["condition"] not in ("b0", "sequential_b2"):
            continue
        key = (res["question_id"], res["judge_model"])
        world_of[res["question_id"]] = res["world"]
        bucket = rates[key].setdefault(res["condition"], [])
        bucket.append(res["verdict_correct_strict"] is not True)
    cells = {}
    shifts = []
    binom_var = []
    for key, conds in rates.items():
        n0, n2 = len(conds["b0"]), len(conds["sequential_b2"])
        e0 = sum(conds["b0"]) / n0
        e2 = sum(conds["sequential_b2"]) / n2
        cells[key] = e0
        shifts.append(e2 - e0)
        binom_var.append(e0 * (1 - e0) / n0 + e2 * (1 - e2) / n2)
    mean_shift = sum(shifts) / len(shifts)
    raw_var = sum((s - mean_shift) ** 2 for s in shifts) / (len(shifts) - 1)
    # The raw shift variance is mostly binomial measurement noise from 6-12 row
    # estimates; the TRUE cell-level treatment heterogeneity is what remains.
    true_var = max(0.0, raw_var - sum(binom_var) / len(binom_var))
    return cells, world_of, true_var ** 0.5


def simulate_once(rng, cells, judges, deltas, het_sd):
    """One synthetic phase-3 dataset: per (q, judge) per budget, slot outcomes."""
    per_q_delta = defaultdict(lambda: defaultdict(list))
    for (q, base_judge), e0 in cells.items():
        for judge in judges:
            if judge != base_judge and CLONES.get(judge) != base_judge:
                continue
            base = min(0.98, max(0.02, e0 + (rng.gauss(0, 0.03) if judge != base_judge else 0.0)))
            wrong0 = sum(rng.random() < base for _ in range(SLOTS)) / SLOTS
            for b in BUDGETS:
                # Treatment-effect heterogeneity scales with the effect: a cell's
                # response varies around the scenario delta with sd proportional to
                # that delta, calibrated so a 4pp mean effect (phase 2's observed H)
                # carries the de-noised phase-2 heterogeneity. A zero effect has
                # zero heterogeneity, so the null is exactly p = base and the
                # procedure's type-I calibration is testable rather than assumed.
                sd = het_sd * abs(deltas[b]) / 0.04
                shift = deltas[b] + (rng.gauss(0, sd) if sd > 0 else 0.0)
                p = min(1.0, max(0.0, base + shift))
                wrongb = sum(rng.random() < p for _ in range(SLOTS)) / SLOTS
                per_q_delta[b][q].append(wrongb - wrong0)
    return {b: {q: sum(v) / len(v) for q, v in qs.items()}
            for b, qs in per_q_delta.items()}


def contrast_p(per_q, strata, rng, inner_b=800):
    """Bootstrap p for one contrast from its per-question values, stratified."""
    reps = []
    for _ in range(inner_b):
        tot = n = 0.0
        for _w, qs in strata.items():
            for _ in qs:
                q = qs[rng.randrange(len(qs))]
                if q in per_q:
                    tot += per_q[q]
                    n += 1
        reps.append(tot / n if n else 0.0)
    le = sum(1 for x in reps if x <= 0)
    ge = sum(1 for x in reps if x >= 0)
    return min(1.0, 2 * min((le + 1) / (len(reps) + 1), (ge + 1) / (len(reps) + 1)))


def main():
    archive = Path("E:/selvarath-archive/main-2026-08-06")
    cells, world_of, het_sd = load_cells(archive)
    judges = sorted({j for (_q, j) in cells}) + list(CLONES)
    strata = defaultdict(list)
    for q, w in sorted(world_of.items()):
        strata[w].append(q)
    strata = dict(strata)
    print(f"cells={len(cells)} judges={len(judges)} het_sd={het_sd:.4f}")

    rng = random.Random(20260814)
    sims = 400
    out = {}
    for name, deltas in SCENARIOS.items():
        rejections = {b: 0 for b in BUDGETS}
        for _ in range(sims):
            per_q_by_b = simulate_once(rng, cells, judges, deltas, het_sd)
            pvals = {str(b): contrast_p(per_q_by_b[b], strata, rng) for b in BUDGETS}
            adj = holm(pvals)
            for b in BUDGETS:
                if adj[str(b)] <= ALPHA:
                    rejections[b] += 1
        out[name] = {f"delta_{b}": round(rejections[b] / sims, 3) for b in BUDGETS}
        print(name, out[name])

    result = {"sims": sims, "inner_bootstrap": 800, "seed": 20260814,
              "heterogeneity_sd": round(het_sd, 4),
              "clone_assumption": CLONES, "scenarios": SCENARIOS,
              "power_holm4_alpha05": out}
    Path("analysis_out/phase3_power_sim.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8")
    print("written to analysis_out/phase3_power_sim.json")


if __name__ == "__main__":
    main()
