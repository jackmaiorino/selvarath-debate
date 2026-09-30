"""Chart: final-phase forecast by provider, low vs high token scenario, against the $6,000 ceiling."""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["text.parse_math"] = False

here = Path(__file__).parent
data = json.loads((here / "cost_forecast.json").read_text())
providers = [("anthropic", "Anthropic", "#2a78d6"), ("openai", "OpenAI", "#eb6834"), ("together", "Together", "#1baf7a")]
rows = [("High tokens", data["high"]), ("Low tokens", data["low"])]

fig, ax = plt.subplots(figsize=(8, 2.8), dpi=150)
fig.patch.set_facecolor("#fcfcfb")
ax.set_facecolor("#fcfcfb")
for y, (label, d) in enumerate(rows):
    left = 0
    for key, name, color in providers:
        v = d["TOTAL by provider"].get(key, 0)
        ax.barh(y, v, left=left, height=0.5, color=color, edgecolor="#fcfcfb", linewidth=2, label=name if y == 0 else None)
        if v > 700:
            ax.text(left + v / 2, y, f"${v / 1000:.1f}k", ha="center", va="center", fontsize=8, color="#0b0b0b")
        left += v
    ax.text(10150, y, f"${left / 1000:.1f}k total (main ${sum(d['main'].values()) / 1000:.1f}k)",
            va="center", fontsize=8.5, color="#0b0b0b")
ax.axvline(6000, color="#52514e", linestyle="--", linewidth=1.2)
ax.text(6000, 1.45, "$6.0k ceiling", ha="center", fontsize=8, color="#52514e")
ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=9, color="#0b0b0b")
ax.set_xlim(0, 13000)
ax.set_ylim(-0.5, 1.6)
ax.set_xticks([0, 2000, 4000, 6000, 8000, 10000])
ax.set_xticklabels(["$0", "$2k", "$4k", "$6k", "$8k", "$10k"], fontsize=8, color="#52514e")
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.spines["bottom"].set_color("#c3c2b7")
ax.tick_params(axis="y", length=0)
ax.set_title("Final phase forecast by provider (all stages)", fontsize=10, loc="left", color="#0b0b0b")
ax.legend(loc="lower right", fontsize=8, frameon=False, ncol=3, bbox_to_anchor=(1.0, -0.42))
fig.tight_layout()
fig.savefig(here / "cost_forecast.png", facecolor=fig.get_facecolor())
