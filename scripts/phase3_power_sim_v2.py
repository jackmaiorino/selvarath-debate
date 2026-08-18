"""Decision-grade phase-3 power simulation (v2, supersedes the 08-14 and Option-D runs).

Corrections per the 2026-08-18 freeze consult:
- Cells are per (question, judge, DEBATER), matching the real design (two 6-slot
  debater cells per question x judge, not one pooled cell). Debater attribution
  comes from the same plan join the frozen analysis engine uses.
- All four tail-slot configurations are simulated with common random numbers:
  per iteration, 12 slot outcomes are drawn once per tail arm and each
  configuration reads its first k. Budgets 0/1/2 always have 6 slots, so their
  per-question contrasts (and p-values) are shared across configurations.
  Configs: A=(b4:6, b8:6) no Option D; B=(12,6) b4-only; C=(6,12) b8-only;
  D=(12,12) full Option D.

Still blinded: no phase-3 outcome exists; scenario curves are the frozen
2026-08-14 set. Output: analysis_out/phase3_power_sim_v2.json.
"""
import json
import random
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.phase2_main_analysis import holm, load_plan_meta, load_records
from scripts.phase3_power_sim import ALPHA, CLONES, SCENARIOS, contrast_p

BUDGETS = (1, 2, 4, 8)
BASE_SLOTS = 6
TAIL_MAX = 12
CONFIGS = {"A_no_d": (6, 6), "B_b4_only": (12, 6), "C_b8_only": (6, 12), "D_full": (12, 12)}
RUN_SCENARIOS = ("u_recovery", "small_effects", "null")


def load_debater_cells():
    root = Path(__file__).resolve().parents[1]
    manifest = root / "rejudge/phase2_main_manifest_2026-08-06c.json"
    results = Path("E:/selvarath-archive/main-2026-08-06/main_results.jsonl")
    plan_meta = load_plan_meta(root, manifest)
    records = load_records(results, plan_meta)
    rates = defaultdict(lambda: defaultdict(list))
    world_of = {}
    for r in records:
        if r["kind"] != "debate_judgment" or r["condition"] not in ("b0", "sequential_b2"):
            continue
        world_of[r["question_id"]] = r["world"]
        # primary rule: INVALID (None) counts wrong
        rates[(r["question_id"], r["judge"], r["debater"])][r["condition"]].append(
            r["correct"] is not True)
    cells = {}
    shifts, binom_var = [], []
    for key, conds in rates.items():
        n0, n2 = len(conds["b0"]), len(conds["sequential_b2"])
        e0 = sum(conds["b0"]) / n0
        e2 = sum(conds["sequential_b2"]) / n2
        cells[key] = e0
        shifts.append(e2 - e0)
        binom_var.append(e0 * (1 - e0) / n0 + e2 * (1 - e2) / n2)
    mean_shift = sum(shifts) / len(shifts)
    raw_var = sum((s - mean_shift) ** 2 for s in shifts) / (len(shifts) - 1)
    true_var = max(0.0, raw_var - sum(binom_var) / len(binom_var))
    return cells, world_of, true_var ** 0.5


def simulate_once(rng, cells, judges, deltas, het_sd):
    """Per-question per-arm contrast values, tail arms at BOTH 6 and 12 slots."""
    per_q = {("b", 1): defaultdict(list), ("b", 2): defaultdict(list),
             (4, 6): defaultdict(list), (4, 12): defaultdict(list),
             (8, 6): defaultdict(list), (8, 12): defaultdict(list)}
    for (q, base_judge, _deb), e0 in cells.items():
        for judge in judges:
            if judge != base_judge and CLONES.get(judge) != base_judge:
                continue
            base = min(0.98, max(0.02, e0 + (rng.gauss(0, 0.03) if judge != base_judge else 0.0)))
            wrong0 = sum(rng.random() < base for _ in range(BASE_SLOTS)) / BASE_SLOTS
            for b in BUDGETS:
                sd = het_sd * abs(deltas[b]) / 0.04
                shift = deltas[b] + (rng.gauss(0, sd) if sd > 0 else 0.0)
                p = min(1.0, max(0.0, base + shift))
                if b in (1, 2):
                    wrongb = sum(rng.random() < p for _ in range(BASE_SLOTS)) / BASE_SLOTS
                    per_q[("b", b)][q].append(wrongb - wrong0)
                else:
                    draws = [rng.random() < p for _ in range(TAIL_MAX)]
                    w6 = sum(draws[:6]) / 6
                    w12 = sum(draws) / 12
                    per_q[(b, 6)][q].append(w6 - wrong0)
                    per_q[(b, 12)][q].append(w12 - wrong0)
    return {k: {q: sum(v) / len(v) for q, v in qs.items()} for k, qs in per_q.items()}


def main():
    cells, world_of, het_sd = load_debater_cells()
    judges = sorted({j for (_q, j, _d) in cells}) + list(CLONES)
    strata = defaultdict(list)
    for q, w in sorted(world_of.items()):
        strata[w].append(q)
    strata = dict(strata)
    print(f"cells={len(cells)} judges={len(judges)} het_sd={het_sd:.4f}", flush=True)

    rng = random.Random(20260818)
    sims = 400
    out = {}
    for name in RUN_SCENARIOS:
        deltas = SCENARIOS[name]
        rej = {cfg: {b: 0 for b in BUDGETS} for cfg in CONFIGS}
        for _ in range(sims):
            per_q = simulate_once(rng, cells, judges, deltas, het_sd)
            pv = {k: contrast_p(v, strata, rng) for k, v in per_q.items()}
            for cfg, (s4, s8) in CONFIGS.items():
                pvals = {"1": pv[("b", 1)], "2": pv[("b", 2)],
                         "4": pv[(4, s4)], "8": pv[(8, s8)]}
                adj = holm(pvals)
                for b in BUDGETS:
                    if adj[str(b)] <= ALPHA:
                        rej[cfg][b] += 1
        out[name] = {cfg: {f"delta_{b}": round(rej[cfg][b] / sims, 3) for b in BUDGETS}
                     for cfg in CONFIGS}
        print(name, json.dumps(out[name]), flush=True)

    result = {"sims": sims, "inner_bootstrap": 800, "seed": 20260818,
              "heterogeneity_sd": round(het_sd, 4),
              "cell_structure": "per (question, judge, debater); 6 slots at b0/b1/b2; tail arms drawn at 12 with each config reading its first k (common random numbers)",
              "configs": CONFIGS, "clone_assumption": CLONES,
              "scenarios": {k: SCENARIOS[k] for k in RUN_SCENARIOS},
              "supersedes": ["analysis_out/phase3_power_sim.json", "analysis_out/phase3_power_sim_optiond.json"],
              "power_holm4_alpha05": out}
    Path("analysis_out/phase3_power_sim_v2.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8")
    print("written to analysis_out/phase3_power_sim_v2.json", flush=True)


if __name__ == "__main__":
    main()
