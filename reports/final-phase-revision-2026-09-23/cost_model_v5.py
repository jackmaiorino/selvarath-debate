"""26 September options with Qwen restored (Qwen billed output doubled for its heavier reasoning)."""
import json
from pathlib import Path
import cost_model_v4 as v4
from cost_model import call as base_call

OUT_MULT = {"qwen38": 2.0}

def call(m, tin, tout, cached_in=0):
    return base_call(m, tin, tout * OUT_MULT.get(m, 1.0), cached_in)

v4.call = call  # judge calls in v4 use this multiplier from here on
J8 = ["luna", "terra", "sol", "haiku", "sonnet", "opus", "llama70", "qwen38"]
rows = [
    v4.design("8 judges, 0/1/2/6", J8, 1068, (1, 2, 6)),
    v4.design("8 judges, 0/1/2/8", J8, 1068, (1, 2, 8)),
    v4.design("8 judges, 0/1/2/6 + top-2 arm", J8, 1068, (1, 2, 6), yoked_k=2),
    v4.design("8 judges, 0/1/2/8 + top-2 arm", J8, 1068, (1, 2, 8), yoked_k=2),
    v4.design("8 judges, 0/1/2/8 + top-2, Fable oracle", J8, 1068, (1, 2, 8), oracle="fable", yoked_k=2),
    v4.design("7 judges (no Qwen), 0/1/2/6", v4.J7, 1068, (1, 2, 6)),
]
for r in rows:
    print(f"{r['design']:44s} total ${r['total']:,}  (debates {r['debates']}, oracle {r['oracle']}, judging {r['judging']}, verdicts {r['verdicts']:,}, oracle calls {r['oracle_calls']:,})")
prep = v4.prep(1300, 150)
print("prep (150 worlds x ~9 questions):", prep, "sum", sum(prep.values()))
Path(__file__).with_suffix(".json").write_text(json.dumps({"rows": rows, "prep": prep}, indent=1))
