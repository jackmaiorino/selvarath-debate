import json, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
rows = json.load(open("cost_model_revised.json"))
lab = {"Announced arms, Opus oracle":"Recommended: 5 judges, ±3 pp",
       "Announced arms, Fable oracle":"Same, Fable 5.1 as oracle",
       "Truncation forks, Opus oracle":"Fallback: one trajectory, forked verdicts",
       "Announced arms, Opus oracle, 4 judges":"4 judges, ±3 pp",
       "Announced arms, Opus oracle, \u00b12 pp":"5 judges, ±2 pp (original precision)"}
rows = sorted(rows, key=lambda r: -r["total"])
fig, ax = plt.subplots(figsize=(8.6, 3.9), dpi=160)
bg = "#fcfcfb"; fig.patch.set_facecolor(bg); ax.set_facecolor(bg)
seg = [("debates","Frontier debates","#2a78d6"),("selector","Strong-selector queries","#4a3aa7"),
       ("oracle","Oracle answers","#eb6834"),("judging","Judging","#1baf7a")]
for i, r in enumerate(rows):
    left = 0
    for k, name, c in seg:
        ax.barh(i, r[k], left=left, height=0.6, color=c, edgecolor=bg, linewidth=2, label=name if i == 0 else None)
        left += r[k]
    ax.text(left + 90, i, f"${r['total']:,}", va="center", fontsize=9, color="#0b0b0b")
ax.set_yticks(range(len(rows))); ax.set_yticklabels([lab[r["design"]] for r in rows], fontsize=9, color="#0b0b0b")
ax.axvline(4800, color="#52514e", lw=1, ls="--")
ax.set_ylim(-0.6, len(rows) + 0.2)
ax.text(4860, len(rows) - 0.25, "9/20 main + reserve: $4,800", fontsize=8, color="#52514e", va="center")
for s in ("top", "right", "left"): ax.spines[s].set_visible(False)
ax.tick_params(axis="x", colors="#52514e", labelsize=8); ax.tick_params(axis="y", length=0)
ax.grid(axis="x", color="#e6e5e0", lw=0.6); ax.set_axisbelow(True); ax.set_xlim(0, 11000)
ax.set_xlabel("Illustrative main-run cost, USD (list batch rates, no caching)", fontsize=9, color="#52514e")
ax.legend(loc="upper center", bbox_to_anchor=(0.45, -0.2), ncol=4, frameon=False, fontsize=8)
ax.set_title("Budgets 0/1/2/4/8 + whole world, judge-chosen queries, frontier debaters", fontsize=10, loc="left", color="#0b0b0b")
plt.tight_layout(); plt.savefig("revised-cost.png")
