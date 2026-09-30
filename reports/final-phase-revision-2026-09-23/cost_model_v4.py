"""Illustrative cost model for the 26 September revision (list batch rates; world cached for oracle calls only)."""
import json
from pathlib import Path

from cost_model import P, call

WORLD = 2000       # authored worlds in the style of the originals (~1,500 words)
DEBATE = 4000      # question, both answers, instructions and a ~2,500-word uncapped debate
Q_TURN_OUT = 600   # judge output per query turn, reasoning included
VERDICT_OUT = 1000
GROW = 400         # context added per query/answer exchange

J7 = ["luna", "terra", "sol", "haiku", "sonnet", "opus", "llama70"]
J6 = ["luna", "terra", "sol", "haiku", "sonnet", "opus"]


def debate_cost(m):
    # 3 rounds x 2 sides, debater sees world plus transcript so far; 1,500 output incl. reasoning
    return sum(call(m, WORLD + 800 + 700 * t, 1500) for t in range(6))


def oracle_call(m):
    return call(m, WORLD + 600, 300, cached_in=WORLD)


def budget_arm(m, k):
    """Announced budget k, judge uses all k (Phase 3 judges used 7.2-7.8 of 8)."""
    c = sum(call(m, DEBATE + GROW * t, Q_TURN_OUT) for t in range(k))
    return c + call(m, DEBATE + GROW * k, VERDICT_OUT), k


def design(name, judges, n, budgets, oracle="opus", yoked_k=None):
    orders = 2
    judging, q = 0.0, 0
    for j in judges:
        per = call(j, WORLD + 500, VERDICT_OUT)          # world alone
        per += call(j, DEBATE, VERDICT_OUT)              # debate only (0 queries)
        per += call(j, DEBATE + WORLD, VERDICT_OUT)      # debate + whole world
        for k in budgets:
            c, used = budget_arm(j, k)
            per += c
            q += orders * n * used
        if yoked_k:
            per += call(j, DEBATE + GROW * yoked_k, VERDICT_OUT)   # Fable-chosen top-k packet
        judging += orders * n * per
    shared = 0.0
    if yoked_k:
        shared += n * call("fable", DEBATE + 300, 1500)      # Fable ranks queries from the debate alone
        q += n * yoked_k                                      # shared oracle answers for those queries
    oracle_cost = q * oracle_call(oracle)
    debates = n * debate_cost("fable")
    total = debates + oracle_cost + judging + shared
    return dict(design=name, judges=len(judges), questions=n, budgets=list(budgets),
                debates=round(debates), oracle=round(oracle_cost), judging=round(judging),
                yoked=round(shared + (0 if not yoked_k else sum(
                    orders * n * call(j, DEBATE + GROW * yoked_k, VERDICT_OUT) for j in judges))),
                total=round(total), oracle_calls=int(q),
                verdicts=orders * n * len(judges) * (3 + len(budgets) + (1 if yoked_k else 0)))


def prep(n_candidates, worlds):
    author = worlds * call("fable", 1500, 18000)                          # world + ~15 questions with facts_required
    validate = n_candidates * 2 * 2 * call("fable", WORLD + 600, 1000, cached_in=WORLD)  # 2 validators x 2 orders
    canary = 40 * debate_cost("fable") + 40 * 3 * call("sol", DEBATE, VERDICT_OUT)
    return dict(authoring=round(author), validation=round(validate), debater_canary=round(canary))


rows = [
    design("Joey's set: 0/2/6, 7 judges", J7, 1068, (2, 6)),
    design("Recommended: 0/1/2/6, 7 judges", J7, 1068, (1, 2, 6)),
    design("0/1/2/6, 6 judges (no Llama)", J6, 1068, (1, 2, 6)),
    design("0/1/2/6, 7 judges, Fable oracle", J7, 1068, (1, 2, 6), oracle="fable"),
    design("0/1/2/6, 7 judges + Fable top-2 arm", J7, 1068, (1, 2, 6), yoked_k=2),
    design("0/1/2/6, 7 judges, 800 questions", J7, 800, (1, 2, 6)),
]
for r in rows:
    print(r)
llama_share = rows[1]["total"] - rows[2]["total"]
print("Llama's share of the recommended design:", llama_share)
print("prep:", prep(1300, 85))
Path(__file__).with_suffix(".json").write_text(json.dumps({"rows": rows, "prep": prep(1300, 85)}, indent=1))
