"""Final-phase cost forecast by stage and provider (2026-09-30), low and high token scenarios.

Replaces cost_model_v5 for funding purposes. Adds gate calls, the 80-debate canary, validation
and fact checks, per-provider totals, and a high scenario for reasoning-token use. List rates
from finalphase/models.py; OpenAI and Anthropic at batch (half) rates, Together live. No
caching credit is assumed. These are forecasts; the canary and pilot replace them with
measured tokens.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from finalphase.models import MODELS  # noqa: E402

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


def c(key, tin, tout, cached=0):
    m = MODELS[key]
    f = 0.5 if m.batch else 1.0
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


def debate(tot, key, s, n, cap=False):
    text = 200 if cap else 700
    for t in range(6):
        add(tot, key, n * c(key, WORLD + 600 + t * text * 1.3, text + s["deb_reason"]))


def judging(tot, s, n, judges, arms=("world_alone", "k0", "k1", "k2", "k6", "debate_world", "top2"), oracle="opus"):
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
            elif arm == "top2":
                per += c(j, debate_in + 200, vo)
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
    if "top2" in arms:
        add(tot, "fable", n * c("fable", debate_in + 300, 3000))
        add(tot, oracle, n * 2 * oracle_call(oracle, s))


def forecast(name):
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
        debate(t, a, s, 20)
        debate(t, a, s, 20, cap=True)
    for j in ("luna", "haiku", "llama70"):
        add(t, j, 80 * 2 * c(j, QPOS + TRANSCRIPT, s["v_out"] * LIGHT.get(j, 1.0)))
    add(t, "dspro", 480 * c("dspro", 1500, 800))
    stages["debater canary"] = t
    for label, n in (("pilot", 140), ("main", 1068)):
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


out = {}
for name in SC:
    st = forecast(name)
    out[name] = {k: {p: round(v, 0) for p, v in d.items()} for k, d in st.items()}
    tot = {}
    for d in st.values():
        for p, v in d.items():
            tot[p] = tot.get(p, 0) + v
    out[name]["TOTAL by provider"] = {p: round(v, 0) for p, v in tot.items()}
    out[name]["TOTAL"] = round(sum(tot.values()), 0)
    print(f"== {name}")
    for k, d in out[name].items():
        print(f"  {k:26s} {d}")
Path(__file__).with_suffix(".json").write_text(json.dumps(out, indent=1))
