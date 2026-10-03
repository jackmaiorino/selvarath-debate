"""Final-phase cost forecast by stage and provider (2026-09-30), low and high token scenarios.

Replaces cost_model_v5 for funding purposes. Adds gate calls, the 80-debate canary, validation
and fact checks, per-provider totals, and a high scenario for reasoning-token use. List rates
from finalphase/models.py; OpenAI and Anthropic at batch (half) rates, Together live. No
caching credit is assumed. These are forecasts; the canary and pilot replace them with
measured tokens.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from finalphase.models import MODELS  # noqa: E402

CEILING_USD = 6000
RESERVE_USD = 590

WORLD = 1800          # tokens, ~1,200-word world
TRANSCRIPT = 4400     # tokens, 6 uncapped turns of ~550 words
QPOS = 250            # question and both positions
SC = {
    "low": dict(deb_reason=1000, q_out=600, v_out=1000, oracle_out=300, val_out=2000, author_out=22000, grow=350),
    "high": dict(deb_reason=4000, q_out=1500, v_out=2500, oracle_out=1000, val_out=5000, author_out=32000, grow=350),
}
QWEN_MULT = 2.0
LIGHT = {"haiku": 0.4, "llama70": 0.3}  # non-reasoning judges write much less
J8 = ["luna", "terra", "sol", "haiku", "sonnet", "opus", "llama70", "qwen38"]


ORACLE_CACHE_HIT = 0.8  # share of oracle calls whose world block is a cache read (Opus 5.5 reads at 0.05x)


def c(key, tin, tout, cached=0, batch=True):
    m = MODELS[key]
    f = 0.5 if batch and m.batch else 1.0
    if key == "qwen38":
        tout *= QWEN_MULT
    return f * ((tin - cached) * m.price_in + cached * m.price_cached_in + tout * m.price_out) / 1e6


def oracle_call(key, s):
    hit = c(key, WORLD + 350, s["oracle_out"], cached=WORLD)
    miss = c(key, WORLD + 350, s["oracle_out"]) + 0.5 * WORLD * MODELS[key].price_in * 0.25 / 1e6
    return ORACLE_CACHE_HIT * hit + (1 - ORACLE_CACHE_HIT) * miss


def add(tot, key, usd):
    p = MODELS[key].provider
    tot[p] = tot.get(p, 0.0) + usd


def debate(tot, key, s, n, cap=False, batch=True):
    text = 200 if cap else 700
    for t in range(6):
        add(tot, key, n * c(key, WORLD + 600 + t * text * 1.3, text + s["deb_reason"], batch=batch))


def judging(tot, s, n, judges, arms=("world_alone", "k0", "k1", "k2", "k6", "debate_world"), oracle="opus"):
    debate_in = QPOS + TRANSCRIPT + 300
    for j in judges:
        lt = LIGHT.get(j, 1.0)
        qo, vo = s["q_out"] * lt, s["v_out"] * lt
        per = 0.0
        queries = 0
        for arm in arms:
            if arm == "world_alone":
                per += c(j, WORLD + QPOS + 300, vo)
            elif arm == "k0":
                per += c(j, debate_in, vo)
            elif arm == "debate_world":
                per += c(j, debate_in + WORLD, vo)
            else:
                k = int(arm[1:])
                steps = int(round(k * 1.2))  # 20% extra steps for gate rejections
                per += sum(c(j, debate_in + s["grow"] * t, qo) for t in range(steps))
                per += c(j, debate_in + s["grow"] * steps, vo)
                queries += k
        add(tot, j, 2 * n * per)
        # oracle answers are shared across judges only when claims coincide; assume none
        add(tot, oracle, 2 * n * queries * oracle_call(oracle, s))
        add(tot, "dsflash", 2 * n * queries * 1.2 * c("dsflash", 700, 400))


def forecast(name, main_questions=1068):
    if not 1 <= main_questions <= 1068:
        raise ValueError("main question count must be between 1 and 1068")
    s = SC[name]
    stages = {}
    t = {}
    for a in ("fable", "astra"):
        add(t, a, 80 * c(a, 2500, s["author_out"]))
    stages["authoring (160 worlds)"] = t
    t = {}
    cands = 160 * 12 * 0.9
    for v in ("fable", "astra"):
        add(t, v, cands / 2 * 2 * c(v, WORLD + 400, s["val_out"]))
    add(t, "dspro", cands * 2 * c("dspro", WORLD + 400, s["val_out"]))
    add(t, "dspro", cands * 3.2 * c("dspro", WORLD + 100, 600))
    stages["validation"] = t
    t = {}
    for a in ("fable", "astra"):
        debate(t, a, s, 20, batch=False)
        debate(t, a, s, 20, cap=True, batch=False)
    for j in ("luna", "haiku", "llama70"):
        add(t, j, 80 * 2 * c(j, QPOS + TRANSCRIPT, s["v_out"] * LIGHT.get(j, 1.0), batch=False))
    add(t, "dspro", 480 * c("dspro", 1500, 800))
    stages["debater canary"] = t
    # Sixteen pilot worlds can retain all twelve questions each.
    for label, n in (("pilot", 192), ("main", main_questions)):
        t = {}
        for a in ("fable", "astra"):
            debate(t, a, s, n / 2)
        judging(t, s, n, J8)
        stages[label] = t
    t = {}
    add(t, "fable", 300 * c("fable", WORLD + 350, s["oracle_out"]))
    add(t, "astra", 300 * c("astra", WORLD + 350, s["oracle_out"]))
    stages["oracle selection"] = t
    return stages


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--main-questions", type=int, default=1068)
    ap.add_argument("--out", type=Path, default=Path(__file__).with_suffix(".json"))
    args = ap.parse_args()
    out = {}
    for name in SC:
        st = forecast(name, args.main_questions)
        out[name] = {k: {p: round(v, 0) for p, v in d.items()} for k, d in st.items()}
        tot = {}
        for d in st.values():
            for p, v in d.items():
                tot[p] = tot.get(p, 0) + v
        out[name]["TOTAL by provider"] = {p: round(v, 0) for p, v in tot.items()}
        out[name]["TOTAL"] = round(sum(tot.values()), 0)
        main_cost = sum(st["main"].values())
        preparation = sum(tot.values()) - main_cost
        out[name]["main_question_capacity_with_reserve"] = max(0, min(1068, int(
            min(4300, CEILING_USD - RESERVE_USD - preparation) / (main_cost / args.main_questions))))
        print(f"{name}: main ${sum(st['main'].values()):.0f}, whole phase ${sum(tot.values()):.0f}")
    out["assumptions"] = {"main_questions": args.main_questions, "pilot_questions": 192,
                          "judges": J8, "budgets": [0, 1, 2, 6], "top2": False,
                          "canary_mode": "live", "main_mode": "batch", "reserve_usd": RESERVE_USD,
                          "rates_checked": "2026-09-30", "measured_forecast": False}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
