"""Reproduce illustrative precision/cost arithmetic; no model calls.

Normal approximations for planning, not a fitted power analysis or run budget.
All counts are effective independent questions, not repeated verdicts.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist


MODELS = [
    ("gpt-5.6-luna", 0.20, 1.20),
    ("gpt-5.6-terra", 2.00, 12.00),
    ("gpt-5.6-sol", 4.00, 20.00),
    ("gpt-6-astra", 10.00, 50.00),
    ("claude-haiku-4-5-20251001", 1.00, 5.00),
    ("claude-sonnet-5", 2.00, 10.00),
    ("claude-opus-5", 5.00, 25.00),
    ("claude-fable-5-1", 10.00, 50.00),
]


def calculate():
    normal = NormalDist()
    z = normal.inv_cdf(0.975)
    zpower = normal.inv_cdf(0.80)
    zfour = normal.inv_cdf(1 - 0.05 / (2 * 4))
    # Three conditions, each in two answer orders, for every model.
    calls_per_model_question = 6
    price = sum((4000 * inp + 1000 * out) / 1e6 for _, inp, out in MODELS) * calls_per_model_question

    def row(n, **fields):
        return dict(fields, effective_independent_questions=n,
                    judge_calls=n * calls_per_model_question * len(MODELS),
                    standard_usd=round(n * price, 2),
                    double_tokens_usd=round(n * price * 2, 2),
                    batch_usd=round(n * price / 2, 2))

    precision = []
    for margin in (0.05, 0.03, 0.02, 0.01):
        n = math.ceil(z * z * 0.1 * 0.9 / margin ** 2)
        precision.append(row(n, margin_pp=margin * 100,
                             worst_case_p50_questions=math.ceil(z*z*0.25/margin**2)))
    power = []
    for delta in (0.05, 0.03, 0.02, 0.01):
        # A paired binary difference D in {-1,0,1}, E[D]=delta,
        # P(D != 0)=discordance. Var(D)=discordance-delta**2.
        variance = 0.1 - delta ** 2
        n = math.ceil((z + zpower) ** 2 * variance / delta ** 2)
        nf = math.ceil((zfour + zpower) ** 2 * variance / delta ** 2)
        power.append(row(n, detectable_difference_pp=delta * 100,
                         four_contrast_bonferroni_questions=nf,
                         four_contrast_standard_usd=round(nf * price, 2)))
    return {
        "kind": "ILLUSTRATIVE_PLANNING_SCENARIOS_NOT_RUN_BUDGET",
        "date": "2026-09-13",
        "assumptions": {
            "baseline_error_probability": 0.10,
            "paired_discordance_probability": 0.10,
            "two_sided_alpha": 0.05,
            "power": 0.80,
            "power_scope": "Per contrast, not joint probability all contrasts succeed",
            "interval_scope": "Marginal per rate, not simultaneous across models and conditions",
            "score_approximation": "One binary score per effective question; both answer orders priced without independent-sample gain",
            "input_tokens_per_call": 4000,
            "billed_output_tokens_per_call_including_reasoning": 1000,
            "calls_per_model_question": calls_per_model_question,
            "conditions": ["full-source capability anchor", "debate without verification", "same debate with verification"],
            "answer_orders": 2,
            "usd_per_question_all_eight_models": round(price, 6),
            "rates": "Standard uncached short-context rates; sources in model-options.md",
            "exclusions": ["benchmark authoring and validation", "debate and evidence construction", "reviewer charges", "retries", "subscription fees", "taxes"],
            "limitations": ["Normal approximation, not exact coverage or guaranteed power", "World dependence requires inflation or hierarchical planning", "Repeated positions are not independent questions", "Token doubling is a sensitivity scenario, not an upper bound", "No guaranteed nonoverlap when models tie"],
        },
        "models": [dict(model=m, input_usd_per_million=i, output_usd_per_million=o) for m,i,o in MODELS],
        "single_rate_precision": precision,
        "paired_difference_power": power,
    }


def plot(data, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8), layout="constrained")
    colors = ("#2667a5", "#157f72")
    panels = [
        ("single_rate_precision", "margin_pp", "95% error-rate interval", "Target half-width (percentage points)"),
        ("paired_difference_power", "detectable_difference_pp", "80% power for a paired difference", "True difference to detect (percentage points)"),
    ]
    for ax, (key, field, title, xlabel), color in zip(axes, panels, colors):
        rows = data[key]
        xs = list(range(len(rows)))
        low = [r["standard_usd"] for r in rows]
        high = [r["double_tokens_usd"] for r in rows]
        ax.fill_between(xs, low, high, color=color, alpha=.17, label="Twice the tokens")
        ax.plot(xs, low, "o-", color=color, linewidth=2, label="4k input + 1k output / call")
        ax.set_xticks(xs, [f'{r[field]:g}' for r in rows])
        ax.set_yscale("log")
        ax.set_ylim(150, 42000)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"${value:,.0f}"))
        ax.set_title(title, fontweight="bold", pad=16)
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Judging cost across all 8 models (USD)")
        ax.grid(axis="y", alpha=.2)
        for x, r, y in zip(xs, rows, low):
            ax.annotate(f'{r["effective_independent_questions"]:,} questions', (x,y), xytext=(0,-23), textcoords="offset points", ha="center", fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(loc="upper left", fontsize=8)
    fig.suptitle("Precision and cost: illustrative scenarios, not observed results", fontsize=16, fontweight="bold")
    fig.supxlabel("8 models x 3 conditions x 2 answer orders. Independent-question approximation.\nAssumes 10% errors (left) and 10% paired discordance (right). Excludes benchmark preparation and retries.\nSingle contrasts; no multiplicity adjustment. Batch eligibility would halve the displayed judging costs.", fontsize=9)
    fig.savefig(output, dpi=180, facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    data = calculate()
    (args.out / "precision-cost.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    if args.plot:
        plot(data, args.out / "precision-cost.png")
    print(json.dumps({k:v for k,v in data.items() if k in ("single_rate_precision", "paired_difference_power")}, indent=2))


if __name__ == "__main__":
    main()
