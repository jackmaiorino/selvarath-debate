"""Build the report's figures from the frozen analysis outputs in paper/data.

Writes a vector PDF (for LaTeX) and a PNG preview of each figure to paper/figures.
Reads only the JSON files in paper/data; sends nothing and computes no new statistic
(the seven-judge pooled rates are the exact linear map of the eight-judge ones, as in
the 2026-10-29 results report: Llama is a constant 100% in every arm and draw).

Run: python paper/scripts/make_figures.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "figures"
J = lambda name: json.loads((DATA / name).read_text(encoding="utf-8"))  # noqa: E731
MAIN, A1, AUDIT = J("main_analysis.json"), J("amendment1_analysis.json"), J("gate_audit.json")
EXPL, MECH, COUNTS = J("explanations_summary.json"), J("explanations_mechanical.json"), J("explanations_counts.json")

# Palette: validated with the dataviz validator (blue, orange: all checks pass on white).
BLUE, ORANGE = "#2a78d6", "#eb6834"
BLUE_LIGHT, BLUE_DARK = "#9ec5f4", "#184f95"
INK, INK2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"

plt.rcParams.update({
    "font.family": "Arial", "font.size": 8, "pdf.fonttype": 42, "ps.fonttype": 42,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK2, "axes.titlesize": 8,
    "axes.spines.top": False, "axes.spines.right": False,
    "xtick.color": AXIS, "ytick.color": AXIS, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5, "text.color": INK,
    "savefig.facecolor": "white", "figure.facecolor": "white",
})

JUDGE_NAME = {"luna": "GPT-5.6 Luna", "terra": "GPT-5.6 Terra", "sol": "GPT-5.6 Sol", "haiku": "Claude Haiku 4.5",
              "sonnet": "Claude Sonnet 5.5", "opus": "Claude Opus 5.5", "qwen38": "Qwen3.8"}
BUDGET_ARMS = ["debate_k0", "debate_k1", "debate_k2", "debate_k6"]
BUDGET_LABELS = ["0", "1", "2", "6"]


def save(fig, name: str) -> None:
    OUT.mkdir(exist_ok=True)
    fig.savefig(OUT / f"{name}.pdf")
    fig.savefig(OUT / f"{name}.png", dpi=220)
    plt.close(fig)
    print("wrote", name)


def seven(arm: str) -> tuple[float, float, float]:
    """Seven-judge pooled error (percent) with its 95% interval, from the eight-judge pooled curve."""
    p = MAIN["curves"]["pooled"][arm]
    f = lambda x: (8 * x - 1) / 7 * 100  # noqa: E731
    return f(p["estimate"]), f(p["ci95"][0]), f(p["ci95"][1])


# ---------------------------------------------------------------- figure: protocol schematic
def fig_protocol() -> None:
    fig = plt.figure(figsize=(5.5, 2.35))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 110)
    ax.set_ylim(2, 48)
    ax.axis("off")

    def box(x, y, w, h, title, body="", fc="white", ec=AXIS, tc=INK):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.2,rounding_size=1.2", fc=fc, ec=ec, lw=0.9))
        if body:
            ax.text(x + w / 2, y + h - 2.6, title, ha="center", va="top", fontsize=7.6, fontweight="bold", color=tc)
            ax.text(x + w / 2, y + h - 6.6, body, ha="center", va="top", fontsize=6.6, color=INK2, linespacing=1.25)
        else:
            ax.text(x + w / 2, y + h / 2, title, ha="center", va="center", fontsize=7.6, fontweight="bold", color=tc)

    def arrow(p, q, label="", color=INK2, style="-|>", ls="-", rad=0.0, lx=0.0, ly=0.0, ha="center"):
        ax.add_patch(FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=7, lw=0.9, color=color, linestyle=ls,
                                     connectionstyle=f"arc3,rad={rad}", shrinkA=1.5, shrinkB=1.5))
        if label:
            ax.text((p[0] + q[0]) / 2 + lx, (p[1] + q[1]) / 2 + ly, label, ha=ha, va="center", fontsize=6.3, color=INK2,
                    linespacing=1.2)

    # hidden side: world document
    box(2, 18, 19, 13, "World document", "fictional; the judge\nnever sees it\n(except in two arms)", fc="#f4f3ef")
    # debaters
    box(29, 31, 23, 13, "Honest debater", "Claude Sonnet 5.5\nargues the correct answer")
    box(29, 4, 23, 13, "Dishonest debater", "Claude Fable 5.1\nargues the wrong answer")
    arrow((21.5, 28), (28.5, 36))
    arrow((21.5, 21), (28.5, 12))
    # transcript
    box(58, 18, 17, 13, "Debate", "three rounds each;\nfirst round blind")
    arrow((52.5, 35), (60, 31.5))
    arrow((52.5, 13), (60, 17.5))
    # judge
    box(81, 18, 16, 13, "Judge", "one of seven\nmodels", ec=BLUE)
    arrow((75.5, 24.5), (80.5, 24.5))
    # verdict
    ax.text(103.5, 24.5, "Verdict:\nA or B", ha="center", va="center", fontsize=7.2, fontweight="bold")
    arrow((97.5, 24.5), (100, 24.5))
    # oracle loop
    box(76, 36.5, 26, 9.5, "Query gate, then oracle", "", fc="#eef4fc", ec=BLUE)
    arrow((85, 31.5), (85, 36), "one factual claim,\nup to k times", color=BLUE, lx=-1.2, ha="right")
    arrow((93, 36), (93, 31.5), "YES / NO /\nNOT ADDRESSED", color=BLUE, lx=1.2, ha="left")
    # oracle reads the world (dashed, routed over the top)
    ax.add_patch(FancyArrowPatch((11.5, 31.5), (75.5, 44.2), arrowstyle="-|>", mutation_scale=7, lw=0.8, color=MUTED,
                                 linestyle=(0, (3, 2)), connectionstyle="angle,angleA=90,angleB=180,rad=3",
                                 shrinkA=1.5, shrinkB=1.5))
    ax.text(40, 45.6, "the oracle (Claude Opus 5.5) reads the world document", ha="center", va="center", fontsize=6.3,
            color=MUTED)
    save(fig, "fig_protocol")


# ---------------------------------------------------------------- figure: pooled error by arm
def fig_error_by_arm() -> None:
    fig, ax = plt.subplots(figsize=(5.5, 2.55))
    fig.subplots_adjust(left=0.085, right=0.985, top=0.95, bottom=0.3)
    xs = [0, 1, 2, 3]
    vals = [seven(a) for a in BUDGET_ARMS]
    base = vals[0][0]
    ax.plot([-0.35, 3.35], [base, base], color=MUTED, lw=0.7, ls=(0, (3, 2)), zorder=1)
    ax.plot(xs, [v[0] for v in vals], color=BLUE, lw=1.2, zorder=2)
    for x, (m, lo, hi) in zip(xs, vals):
        ax.plot([x, x], [lo, hi], color=BLUE, lw=1.4, zorder=3, solid_capstyle="round")
        ax.plot([x], [m], "o", ms=5.5, mfc=BLUE, mec="white", mew=1.0, zorder=4)
        ax.text(x, hi + 0.16, f"{m:.2f}%", ha="center", va="bottom", fontsize=7.6, color=INK)
    refs = [("debate_world", 4.5), ("world_alone", 5.75)]
    for arm, x in refs:
        m, lo, hi = seven(arm)
        ax.plot([x, x], [lo, hi], color=INK2, lw=1.4, zorder=3, solid_capstyle="round")
        ax.plot([x], [m], "s", ms=5, mfc=INK2, mec="white", mew=1.0, zorder=4)
        ax.text(x, hi + 0.16, f"{m:.2f}%", ha="center", va="bottom", fontsize=7.6, color=INK)
    ax.axvline(3.72, color=GRID, lw=0.8)
    ax.set_xlim(-0.5, 6.35)
    ax.set_ylim(0, 6.6)
    ax.set_yticks([0, 1, 2, 3, 4, 5, 6])
    ax.yaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.set_ylabel("Judge error (%)")
    ax.set_xticks(xs + [x for _, x in refs])
    ax.set_xticklabels(BUDGET_LABELS + ["debate and\nworld document", "world document,\nno debate"])
    ax.tick_params(axis="x", length=0, pad=4)
    ax.text(1.5, -0.3, "Debate only: oracle queries offered to the judge", transform=ax.get_xaxis_transform(),
            ha="center", va="top", fontsize=8, color=INK2)
    ax.text(5.125, -0.3, "Judge reads the world itself", transform=ax.get_xaxis_transform(), ha="center", va="top",
            fontsize=8, color=INK2)
    ax.text(0.5, base - 0.1, "no-query level", ha="center", va="top", fontsize=6.6, color=MUTED)
    save(fig, "fig_error_by_arm")


# ---------------------------------------------------------------- figure: error by judge
def fig_error_by_judge() -> None:
    pp_in = 0.205  # inches per percentage point, the same in every panel
    W, H = 5.5, 3.95
    fig = plt.figure(figsize=(W, H))
    bottom, low_h, gap_y = 0.47, 6.5 * pp_in, 0.44
    cols_x, col_w, gap_x = 1.98, 0.93, 0.16
    haiku_h = 10 * pp_in

    def panel(judge, x0, y0, w, h, ylo, yhi, yticks, show_x, show_y):
        ax = fig.add_axes([x0 / W, y0 / H, w / W, h / H])
        cur = MAIN["curves"][judge]
        xs = [0, 1, 2, 3]
        est = [cur[a]["estimate"] * 100 for a in BUDGET_ARMS]
        ax.plot([-0.4, 3.4], [est[0], est[0]], color=MUTED, lw=0.7, ls=(0, (3, 2)), zorder=1)
        ax.plot(xs, est, color=BLUE, lw=1.1, zorder=2)
        for x, a in zip(xs, BUDGET_ARMS):
            lo, hi = (v * 100 for v in cur[a]["ci95"])
            open_top = math.isinf(hi)  # too few errors for an upper limit: draw the lower part and mark it
            top = cur[a]["estimate"] * 100 if open_top else hi
            ax.plot([x, x], [lo, top], color=BLUE, lw=1.3, zorder=3, solid_capstyle="round")
            if open_top:
                ax.text(x + 0.02, top + 0.35, "*", ha="center", va="bottom", fontsize=9, color=INK2)
            ax.plot([x], [cur[a]["estimate"] * 100], "o", ms=4.6, mfc=BLUE, mec="white", mew=0.9, zorder=4)
        ax.set_xlim(-0.5, 3.5)
        ax.set_ylim(ylo, yhi)
        ax.set_yticks(yticks)
        ax.yaxis.grid(True, color=GRID, lw=0.6)
        ax.set_axisbelow(True)
        ax.set_xticks(xs)
        ax.set_xticklabels(BUDGET_LABELS if show_x else [])
        ax.tick_params(axis="x", length=0, pad=3)
        if not show_y:
            ax.set_yticklabels([])
            ax.tick_params(axis="y", length=0)
        ax.set_title(JUDGE_NAME[judge], fontsize=7.8, color=INK, pad=4, loc="left", fontweight="bold")
        return ax

    hx = 0.5
    axh = panel("haiku", hx, bottom, 1.08, haiku_h, 16, 26, [16, 18, 20, 22, 24, 26], True, True)
    axh.set_ylabel("Judge error (%)")
    grid = [["luna", "terra", "sol"], ["qwen38", "sonnet", "opus"]]
    for r, row in enumerate(grid):
        y0 = bottom + (1 - r) * (low_h + gap_y)
        for c, judge in enumerate(row):
            ax = panel(judge, cols_x + c * (col_w + gap_x) + 0.25, y0, col_w, low_h, 0, 6.5, [0, 2, 4, 6],
                       r == 1, c == 0)
            if c == 0 and r == 0:
                ax.set_ylabel("Judge error (%)")
                ax.yaxis.set_label_coords(-0.24, -0.17)
    fig.text((cols_x + 0.25 + 1.5 * col_w + gap_x) / W, 0.085 / H * 1.2, "Oracle queries offered to the judge", ha="center",
             va="bottom", fontsize=8, color=INK2)
    fig.text((hx + 0.54) / W, 0.085 / H * 1.2, "Oracle queries offered", ha="center", va="bottom", fontsize=8, color=INK2)
    fig.text((hx - 0.38) / W, (bottom + haiku_h + 0.62) / H,
             "Every panel uses the same\nvertical scale: one point of\nerror is the same height.", ha="left",
             va="bottom", fontsize=6.8, color=INK2, linespacing=1.3)
    save(fig, "fig_error_by_judge")


# ---------------------------------------------------------------- figure: gate audit
def fig_gate_audit() -> None:
    fig, ax = plt.subplots(figsize=(3.6, 2.2))
    fig.subplots_adjust(left=0.135, right=0.99, top=0.78, bottom=0.2)
    arms = ["debate_k1", "debate_k2", "debate_k6"]
    w = 0.36
    for i, arm in enumerate(arms):
        e = AUDIT["arms"][arm]["exposed"]["estimate"] * 100
        lk = AUDIT["arms"][arm]["leak_exposed"]["estimate"] * 100
        ax.bar(i - w / 2 - 0.02, e, w, color=BLUE_LIGHT, zorder=2)
        ax.bar(i + w / 2 + 0.02, lk, w, color=BLUE_DARK, zorder=2)
        ax.text(i - w / 2 - 0.02, e + 0.8, f"{e:.1f}%", ha="center", va="bottom", fontsize=7.6)
        ax.text(i + w / 2 + 0.02, lk + 0.8, f"{lk:.1f}%", ha="center", va="bottom", fontsize=7.6)
    ax.set_xticks(range(3))
    ax.set_xticklabels(["1", "2", "6"])
    ax.tick_params(axis="x", length=0, pad=3)
    ax.set_xlabel("Oracle queries offered to the judge")
    ax.set_ylabel("Sampled sessions (%)")
    ax.set_ylim(0, 40)
    ax.set_yticks([0, 10, 20, 30, 40])
    ax.yaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE_LIGHT), plt.Rectangle((0, 0), 1, 1, color=BLUE_DARK)]
    ax.legend(handles, ["at least one admitted query flagged by the audit",
                        "answer-revealing or unclassified flag"],
              loc="lower left", bbox_to_anchor=(-0.12, 1.04), frameon=False, fontsize=7, handlelength=1.0,
              handleheight=0.8, borderaxespad=0.2, labelcolor=INK2)
    save(fig, "fig_gate_audit")


# ---------------------------------------------------------------- figure: what the judges wrote
def fig_explanations() -> None:
    fig = plt.figure(figsize=(5.5, 2.45))
    axa = fig.add_axes([0.335, 0.2, 0.245, 0.6])
    axb = fig.add_axes([0.69, 0.2, 0.295, 0.47])
    mech = EXPL["weighted_counts_of_331"]["mechanism"]
    total = COUNTS["kinds"]["harm"]
    unparsed_with_answer = COUNTS["unparsed"]["harm"] - 1  # 7 unparsed harmful flips, one of them without an answer
    rows = [
        ("Correct oracle answer,\nunsupported conclusion", mech["M2_MISREAD_CORRECT_ANSWER"]),
        ("Answer unused, peripheral,\noverridden or reinterpreted", mech["M3_NOT_THE_ANSWER"]),
        ("Oracle answered wrongly", mech["M1_ORACLE_ERROR"]),
        ("No oracle answer received", [EXPL["weighted_counts_of_331"]["no_answer"]] * 3),
        ("Unreadable verdict that\nhad an oracle result", [unparsed_with_answer] * 3),
        ("Unclear", mech["M4_UNCLEAR"]),
    ]
    ys = list(range(len(rows)))[::-1]
    for y, (label, (m, lo, hi)) in zip(ys, rows):
        p, plo, phi = (v / total * 100 for v in (m, lo, hi))
        axa.barh(y, p, 0.62, color=BLUE, zorder=2)
        if phi - plo > 0.01:
            axa.plot([plo, phi], [y, y], color=INK, lw=0.9, zorder=3)
        axa.text(max(p, phi) + 2.5, y, f"{p:.0f}%", ha="left", va="center", fontsize=7.6)
    axa.set_yticks(ys)
    axa.set_yticklabels([r[0] for r in rows], fontsize=7.4, color=INK, linespacing=1.15)
    axa.tick_params(axis="y", length=0, pad=4)
    axa.set_xlim(0, 100)
    axa.set_xticks([0, 25, 50, 75, 100])
    axa.xaxis.grid(True, color=GRID, lw=0.6)
    axa.set_axisbelow(True)
    axa.spines["left"].set_visible(False)
    axa.set_xlabel("Share of the 331 harmful flips (%)")
    fig.text(0.012, 0.93, "(a) Labels given to the harmful flips", fontweight="bold", fontsize=8, va="center")

    groups = [("Dishonest\ndebater's", "claim source: dishonest"), ("Both\ndebaters'", "claim source: shared"),
              ("Honest\ndebater's", "claim source: honest")]
    w = 0.36
    for i, (_, key) in enumerate(groups):
        h, g = MECH[key]["harm"], MECH[key]["help"]
        axb.bar(i - w / 2 - 0.02, h, w, color=ORANGE, zorder=2)
        axb.bar(i + w / 2 + 0.02, g, w, color=BLUE, zorder=2)
        axb.text(i - w / 2 - 0.02, h + 3, str(h), ha="center", va="bottom", fontsize=7.6)
        axb.text(i + w / 2 + 0.02, g + 3, str(g), ha="center", va="bottom", fontsize=7.6)
    axb.set_xticks(range(3))
    axb.set_xticklabels([g[0] for g in groups], fontsize=7.4, color=INK)
    axb.tick_params(axis="x", length=0, pad=3)
    axb.set_ylim(0, 150)
    axb.set_yticks([0, 50, 100, 150])
    axb.yaxis.grid(True, color=GRID, lw=0.6)
    axb.set_axisbelow(True)
    axb.set_ylabel("Verdict flips, budget 0 to 1")
    fig.text(0.615, 0.93, "(b) Whose wording the judge checked", fontweight="bold", fontsize=8, va="center")
    handles = [plt.Rectangle((0, 0), 1, 1, color=ORANGE), plt.Rectangle((0, 0), 1, 1, color=BLUE)]
    axb.legend(handles, ["right to wrong (harmful)", "wrong to right (helpful)"], loc="lower left",
               bbox_to_anchor=(-0.02, 1.02), frameon=False, fontsize=7, handlelength=1.0, handleheight=0.8,
               labelcolor=INK2, borderaxespad=0.1)
    save(fig, "fig_explanations")


# ---------------------------------------------------------------- figure: the estimate, phase by phase
def fig_phases() -> None:
    phases = J("earlier_phases.json")["phases"]
    d10 = A1["seven_judge"]["primary"]["components"]["D10"]
    rows = [(p["label"], p["when"], p["estimate"], p["lo"], p["hi"],
             (p["label_note"] + "\n" if p.get("label_note") else "")
             + f"{p['judges'] if len(p['judges']) < 30 else 'four open-weight judges'}; {p['questions']} questions")
            for p in phases]
    rows.append(("Final phase", "October 2026", d10["estimate"] * 100, d10["ci95"][0] * 100, d10["ci95"][1] * 100,
                 "seven judges; 1,068 questions"))
    fig = plt.figure(figsize=(5.5, 3.0))
    ax = fig.add_axes([0.43, 0.17, 0.55, 0.8])
    ys = list(range(len(rows)))[::-1]
    ax.axvline(0, color=AXIS, lw=0.9, zorder=1)
    for y, (label, when, est, lo, hi, desc) in zip(ys, rows):
        ax.plot([lo, hi], [y, y], color=BLUE, lw=1.5, zorder=3, solid_capstyle="round")
        ax.plot([est], [y], "o", ms=5.5, mfc=BLUE, mec="white", mew=1.0, zorder=4)
        ax.text(hi + 0.3, y, f"+{est:.1f}", ha="left", va="center", fontsize=7.6)
        ax.text(-0.03, y + 0.16, f"{label} ({when})", transform=ax.get_yaxis_transform(), ha="right", va="center",
                fontsize=7.8, fontweight="bold", color=INK)
        ax.text(-0.03, y - 0.06, desc, transform=ax.get_yaxis_transform(), ha="right", va="top", fontsize=6.8,
                color=INK2, linespacing=1.2)  # va="top" so a two-line note grows downward and never clips at the left
    ax.set_yticks([])
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(-1, 11.5)
    ax.set_xticks([0, 2, 4, 6, 8, 10])
    ax.xaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.spines["left"].set_visible(False)
    ax.set_xlabel("Rise in judge error with a small query budget (percentage points)")
    save(fig, "fig_phases")


if __name__ == "__main__":
    fig_phases()
    fig_protocol()
    fig_error_by_arm()
    fig_error_by_judge()
    fig_gate_audit()
    fig_explanations()
