"""Phase 3 per-judge change in error vs no queries, with paired 95% question-cluster intervals."""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

s = json.load(open("../phase3-results-2026-09-11/results-summary.json"))["per_judge_descriptive"]
series = [("Llama 3.3 70B", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "#2a78d6", -0.09),
          ("Qwen3.8", "Qwen/Qwen3.8-2.4T-A95B", "#eb6834", 0.09)]
ks = [1, 2, 4, 8]
bg, ink, muted, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"
fig, ax = plt.subplots(figsize=(7.2, 3.9), dpi=170)
fig.patch.set_facecolor(bg); ax.set_facecolor(bg)
ax.axhline(0, color=muted, lw=1)
for label, key, color, dx in series:
    est = [0] + [100 * s[key][f"D{k}"]["estimate"] for k in ks]
    lo = [0] + [100 * s[key][f"D{k}"]["ci95_percentile"][0] for k in ks]
    hi = [0] + [100 * s[key][f"D{k}"]["ci95_percentile"][1] for k in ks]
    xs = [0] + [k + dx for k in ks]
    ax.plot(xs, est, color=color, lw=2, zorder=3)
    ax.errorbar(xs[1:], est[1:], yerr=[[e - l for e, l in zip(est[1:], lo[1:])], [h - e for e, h in zip(est[1:], hi[1:])]],
                fmt="o", ms=7, color=color, ecolor=color, elinewidth=1.4, capsize=0, mec=bg, mew=1.5, zorder=4, label=label)
    ax.text(8.35, est[-1], label, color=ink, fontsize=9, va="center")
ax.set_xticks([0, 1, 2, 4, 6, 8])
ax.set_xlim(-0.4, 10.2); ax.set_ylim(-5, 13)
ax.set_xlabel("Oracle queries allowed", fontsize=9, color=muted)
ax.set_ylabel("Change in error vs 0 queries (pp)", fontsize=9, color=muted)
for sp in ("top", "right"): ax.spines[sp].set_visible(False)
for sp in ("left", "bottom"): ax.spines[sp].set_color(grid)
ax.tick_params(colors=muted, labelsize=9); ax.grid(axis="y", color=grid, lw=0.6); ax.set_axisbelow(True)
ax.legend(loc="upper left", frameon=False, fontsize=8.5)
ax.set_title("Phase 3: Llama's bump is at 1 query and gone by 2; Qwen's harm keeps growing", fontsize=10.5, loc="left", color=ink)
fig.text(0.01, 0.01, "Paired 95% question-cluster intervals, 82 questions. Judges used 7.2-7.8 of 8 queries on average.",
         fontsize=7.5, color=muted)
plt.tight_layout(rect=(0, 0.03, 1, 1)); plt.savefig("phase3-curves.png")
print("ok")
