"""Option-D extension of the blinded phase-3 power simulation.

The 2026-08-14 simulation (scripts/phase3_power_sim.py, frozen output
analysis_out/phase3_power_sim.json) modeled the base grid: 6 judgment slots per
cell at every budget. Option D doubles the slots at b4/b8 only. Its claimed
power gain was a back-of-envelope estimate; this run verifies it on the same
error structure, same estimator, same clone assumptions, changing ONLY the
per-budget slot counts. b0 keeps 6 slots in both configurations, as designed.

Still blinded: no phase-3 outcome exists; scenario curves are the frozen
2026-08-14 set. Output goes to a separate artifact so the original frozen
simulation record is never rewritten.
"""
import json
import random
from collections import defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.phase2_main_analysis import holm
from scripts.phase3_power_sim import (
    ALPHA, BUDGETS, CLONES, SCENARIOS, contrast_p, load_cells,
)

SLOTS_B0 = 6
SLOTS_BY_BUDGET = {1: 6, 2: 6, 4: 12, 8: 12}
RUN_SCENARIOS = ("u_recovery", "small_effects", "null")


def simulate_once(rng, cells, judges, deltas, het_sd):
    per_q_delta = defaultdict(lambda: defaultdict(list))
    for (q, base_judge), e0 in cells.items():
        for judge in judges:
            if judge != base_judge and CLONES.get(judge) != base_judge:
                continue
            base = min(0.98, max(0.02, e0 + (rng.gauss(0, 0.03) if judge != base_judge else 0.0)))
            wrong0 = sum(rng.random() < base for _ in range(SLOTS_B0)) / SLOTS_B0
            for b in BUDGETS:
                sd = het_sd * abs(deltas[b]) / 0.04
                shift = deltas[b] + (rng.gauss(0, sd) if sd > 0 else 0.0)
                p = min(1.0, max(0.0, base + shift))
                k = SLOTS_BY_BUDGET[b]
                wrongb = sum(rng.random() < p for _ in range(k)) / k
                per_q_delta[b][q].append(wrongb - wrong0)
    return {b: {q: sum(v) / len(v) for q, v in qs.items()}
            for b, qs in per_q_delta.items()}


def main():
    archive = Path("E:/selvarath-archive/main-2026-08-06")
    cells, world_of, het_sd = load_cells(archive)
    judges = sorted({j for (_q, j) in cells}) + list(CLONES)
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
        rejections = {b: 0 for b in BUDGETS}
        for _ in range(sims):
            per_q_by_b = simulate_once(rng, cells, judges, deltas, het_sd)
            pvals = {str(b): contrast_p(per_q_by_b[b], strata, rng) for b in BUDGETS}
            adj = holm(pvals)
            for b in BUDGETS:
                if adj[str(b)] <= ALPHA:
                    rejections[b] += 1
        out[name] = {f"delta_{b}": round(rejections[b] / sims, 3) for b in BUDGETS}
        print(name, out[name], flush=True)

    result = {"sims": sims, "inner_bootstrap": 800, "seed": 20260818,
              "heterogeneity_sd": round(het_sd, 4),
              "slots_by_budget": SLOTS_BY_BUDGET, "slots_b0": SLOTS_B0,
              "clone_assumption": CLONES,
              "scenarios": {k: SCENARIOS[k] for k in RUN_SCENARIOS},
              "baseline_comparison": "analysis_out/phase3_power_sim.json (flat 6 slots, seed 20260814)",
              "power_holm4_alpha05": out}
    Path("analysis_out/phase3_power_sim_optiond.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8")
    print("written to analysis_out/phase3_power_sim_optiond.json", flush=True)


if __name__ == "__main__":
    main()
