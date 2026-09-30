from cost_model import *
import json
def design3(name, judges, n, oracle="opus", curve="separate", selector="fable", diag_frac=1/3):
    orders = 2; nd = n * diag_frac
    debates = n * debate_cost("fable")
    sel_c, sel_q = judge_separate_arms(selector, budgets=(8,))
    selector_cost = orders * nd * sel_c
    judge = 0.0; q_total = orders * nd * sel_q
    for j in judges:
        base = judge_fixed(j, WORLD - DEBATE + 500) + judge_fixed(j) + judge_fixed(j, WORLD)
        c, q = judge_fork_trajectory(j) if curve == "fork" else judge_separate_arms(j)
        judge += orders * n * (base + c) + orders * nd * 2 * judge_fixed(j, 8 * GROW)
        q_total += orders * n * q
    oc = q_total * oracle_cost(oracle)
    return dict(design=name, debates=round(debates), selector=round(selector_cost), oracle=round(oc), judging=round(judge),
                total=round(debates + selector_cost + oc + judge), oracle_calls=int(q_total))
rows = [design3("Announced arms, Opus oracle", J5, 1068),
        design3("Announced arms, Fable oracle", J5, 1068, oracle="fable"),
        design3("Truncation forks, Opus oracle", J5, 1068, curve="fork"),
        design3("Announced arms, Opus oracle, 4 judges", J4, 1068),
        design3("Announced arms, Opus oracle, ±2 pp", J5, 2406)]
for r in rows: print(r)
json.dump(rows, open("cost_model_revised.json", "w"), indent=1)
