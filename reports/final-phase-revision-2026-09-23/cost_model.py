"""Illustrative cost model for the revised final phase. Published list rates, no caching unless noted."""
import json

# (input $/M, output $/M, batch_eligible)
P = {
    "luna": (0.20, 1.20, True), "terra": (2.00, 12.00, True), "sol": (4.00, 20.00, True),
    "astra": (10.00, 50.00, True),
    "haiku": (1.00, 5.00, True), "sonnet": (2.00, 10.00, True), "opus": (5.00, 25.00, True),
    "fable": (10.00, 50.00, True),
    "llama70": (1.04, 1.04, False), "qwen38": (2.00, 6.00, False),
    "dsflash": (0.14, 0.28, False), "dspro": (1.32, 3.96, False),
}

def call(m, tin, tout, cached_in=0):
    i, o, b = P[m]
    f = 0.5 if b else 1.0
    return f * ((tin - cached_in) * i + cached_in * i * 0.1 + tout * o) / 1e6

WORLD = 4000       # world spec tokens
DEBATE = 4000      # judge context: question, candidates, debate (proposal assumption)
Q_TURN_OUT = 600   # judge output per query turn, reasoning included
VERDICT_OUT = 1000
GROW = 400         # context growth per Q&A exchange

def debate_cost(m):
    # 3 rounds x 2 sides; debaters see world + running transcript
    return sum(call(m, WORLD + 1500 + 1000 * t, 1500) for t in range(6))

def oracle_cost(m):
    # world cached across calls on the same scenario
    return call(m, WORLD + 600, 300, cached_in=WORLD)

def judge_fixed(m, extra_in=0):
    return call(m, DEBATE + extra_in, VERDICT_OUT)

def judge_fork_trajectory(m, kmax=8, checkpoints=(1, 2, 4, 8)):
    """One adaptive trajectory to kmax; a verdict is forked off at each checkpoint."""
    c = sum(call(m, DEBATE + GROW * t, Q_TURN_OUT) for t in range(kmax))
    c += sum(call(m, DEBATE + GROW * k, VERDICT_OUT) for k in checkpoints)
    return c, kmax

def judge_separate_arms(m, budgets=(1, 2, 4, 8)):
    c, q = 0.0, 0
    for k in budgets:
        c += sum(call(m, DEBATE + GROW * t, Q_TURN_OUT) for t in range(k))
        c += call(m, DEBATE + GROW * k, VERDICT_OUT)
        q += k
    return c, q

def design(name, judges, n, debaters=("fable", "astra"), oracle="opus", curve="fork", fixed8=True):
    per_scn_debate = sum(debate_cost(d) for d in debaters) / len(debaters)  # one transcript per scenario
    orders = 2
    judge = oracle_calls = 0.0
    for j in judges:
        base = judge_fixed(j, WORLD - DEBATE + 500)   # world alone
        base += judge_fixed(j)                          # debate only
        base += judge_fixed(j, WORLD)                   # debate + whole world
        if fixed8:
            base += judge_fixed(j, 8 * GROW)            # debate + fixed 8-exchange packet (diagnostic)
        c, q = judge_fork_trajectory(j) if curve == "fork" else judge_separate_arms(j)
        judge += orders * n * (base + c)
        oracle_calls += orders * n * q
    fixed_packet_oracle = n * 8 if fixed8 else 0
    oracle = (oracle_calls + fixed_packet_oracle) * oracle_cost(oracle)
    debates = n * per_scn_debate
    return {"design": name, "scenarios": n, "judges": len(judges), "debates": round(debates),
            "oracle": round(oracle), "judging": round(judge), "total": round(debates + oracle + judge),
            "oracle_calls": int(oracle_calls + fixed_packet_oracle)}

J7 = ["luna", "terra", "sol", "haiku", "sonnet", "opus", "llama70"]
J5 = ["luna", "sol", "haiku", "opus", "llama70"]
J4 = ["luna", "sol", "haiku", "opus"]

rows = [
    design("7 judges, +/-2pp", J7, 2406),
    design("7 judges, +/-3pp", J7, 1068),
    design("5 judges, +/-2pp", J5, 2406),
    design("5 judges, +/-3pp", J5, 1068),
    design("4 judges, +/-2pp", J4, 2406),
    design("5 judges, +/-3pp, separate budget arms", J5, 1068, curve="separate"),
    design("5 judges, +/-3pp, Fable oracle", J5, 1068, oracle="fable"),
]
print(f"{'design':44s} {'debates':>8s} {'oracle':>8s} {'judging':>8s} {'total':>8s} {'oracle calls':>12s}")
for r in rows:
    print(f"{r['design']:44s} {r['debates']:8d} {r['oracle']:8d} {r['judging']:8d} {r['total']:8d} {r['oracle_calls']:12d}")
print("per-transcript debate cost fable/astra:", round(debate_cost("fable"), 3), round(debate_cost("astra"), 3))
print("oracle per call opus/fable:", round(oracle_cost("opus"), 4), round(oracle_cost("fable"), 4))
json.dump(rows, open(__file__.replace(".py", ".json"), "w"), indent=1)
