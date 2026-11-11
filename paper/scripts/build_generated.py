"""Generate the report's number macros and LaTeX tables from the frozen analysis outputs in paper/data,
so that no result in the text or tables is typed by hand.

Writes paper/generated/numbers.tex and paper/generated/tab_*.tex. Reads only paper/data; computes no new
inferential statistic. The seven-judge pooled error rates are the exact linear map of the eight-judge
ones (Llama-70B is a constant 100% in every arm and bootstrap draw), as in the 2026-10-29 results report.

Run: python paper/scripts/build_generated.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, GEN = ROOT / "data", ROOT / "generated"
J = lambda name: json.loads((DATA / name).read_text(encoding="utf-8"))  # noqa: E731
MAIN, A1, A3, AUDIT = J("main_analysis.json"), J("amendment1_analysis.json"), J("amendment3_analysis.json"), J("gate_audit.json")
COUNTS, EXPL, MECH = J("explanations_counts.json"), J("explanations_summary.json"), J("explanations_mechanical.json")
BENCH, READ = J("benchmark_facts.json"), J("main_readout.json")
S7 = A1["seven_judge"]
SEVEN = S7["judges"]
ARMS = ["world_alone", "debate_k0", "debate_k1", "debate_k2", "debate_k6", "debate_world"]
JUDGE_NAME = {"luna": "GPT-5.6 Luna", "terra": "GPT-5.6 Terra", "sol": "GPT-5.6 Sol", "haiku": "Claude Haiku 4.5",
              "sonnet": "Claude Sonnet 5.5", "opus": "Claude Opus 5.5", "qwen38": "Qwen3.8"}
JUDGE_ORDER = ["luna", "terra", "sol", "haiku", "sonnet", "opus", "qwen38"]
assert sorted(SEVEN) == sorted(JUDGE_ORDER)

macros: list[str] = []


def macro(name: str, value) -> None:
    assert name.isalpha(), name
    macros.append(f"\\newcommand{{\\{name}}}{{{value}}}")


def seven_rate(arm: str) -> tuple[float, float, float]:
    p = MAIN["curves"]["pooled"][arm]
    f = lambda x: (8 * x - 1) / 7 * 100  # noqa: E731
    return f(p["estimate"]), f(p["ci95"][0]), f(p["ci95"][1])


def signed(x: float, d: int = 2) -> str:
    """A signed number for math mode."""
    return f"{x:+.{d}f}".replace("-", "-")


def ci_math(lo: float, hi: float, d: int = 2) -> str:
    return f"$[{lo:+.{d}f},\\ {hi:+.{d}f}]$"


def pval(p: float) -> str:
    return f"{p:.4f}" if p >= 0.0001 else "0.0001"


def write(name: str, lines: list[str]) -> None:
    GEN.mkdir(exist_ok=True)
    (GEN / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", name)


# ------------------------------------------------------------------ pooled rates (seven judges)
ARM_MACRO = {"world_alone": "errWorld", "debate_k0": "errKzero", "debate_k1": "errKone", "debate_k2": "errKtwo",
             "debate_k6": "errKsix", "debate_world": "errDebWorld"}
for arm, name in ARM_MACRO.items():
    m, lo, hi = seven_rate(arm)
    macro(name, f"{m:.2f}")
    macro(name + "CI", f"{lo:.2f} to {hi:.2f}")

# ------------------------------------------------------------------ primary and secondary contrasts
comp = S7["primary"]["components"]
bounds = S7["primary"]["simultaneous95"]["bounds"]
COMP_MACRO = {"D10": "dOneZero", "D16": "dOneSix", "D20": "dTwoZero", "D26": "dTwoSix"}
for k, name in COMP_MACRO.items():
    c = comp[k]
    macro(name, f"{c['estimate'] * 100:.2f}")
    macro(name + "CI", f"{c['ci95'][0] * 100:.2f} to {c['ci95'][1] * 100:.2f}".replace("-", "$-$"))
    macro(name + "SimHi", f"{bounds[k][1] * 100:.2f}")
macro("pHolmOne", f"{S7['primary']['p_holm']['b1']:.4f}")
macro("pHolmTwo", f"{S7['primary']['p_holm']['b2']:.3f}")
sec = S7["secondary"]
s60, sw6 = sec["pooled_k6_minus_k0"], sec["pooled_debate_world_minus_k6"]
macro("dSixZero", f"{abs(s60['estimate']) * 100:.2f}")
macro("dSixZeroCI", f"$-${abs(s60['ci95'][0]) * 100:.2f} to {s60['ci95'][1] * 100:.2f}")
macro("dSixZeroLo", f"{abs(s60['ci95'][0]) * 100:.2f}")
macro("dSixZeroHi", f"{s60['ci95'][1] * 100:.2f}")
macro("nHonestFirst", str(MAIN["order_balance"]["honest_first"]))
assert MAIN["order_balance"]["questions"] == MAIN["questions"]["analysed"]
assert s60["estimate"] < 0 < s60["ci95"][1] and s60["ci95"][0] < 0
macro("dWorldSix", f"{abs(sw6['estimate']) * 100:.2f}")
macro("dWorldSixCI", f"{abs(sw6['ci95'][1]) * 100:.2f} to {abs(sw6['ci95'][0]) * 100:.2f}")
assert sw6["ci95"][1] < 0
macro("pHolmWorldSix", f"{sw6['p_holm']:.4f}")
q10, q60 = sec["qwen38_k1_minus_k0"], sec["qwen38_k6_minus_k0"]
macro("qwenOneZero", f"{q10['estimate'] * 100:.2f}")
macro("qwenOneZeroCI", f"{q10['ci95'][0] * 100:.2f} to {q10['ci95'][1] * 100:.2f}")
macro("qwenOneZeroHolm", f"{q10['p_holm']:.3f}")
macro("qwenSixZero", f"{abs(q60['estimate']) * 100:.2f}")
macro("qwenSixZeroHolm", f"{q60['p_holm']:.2f}")

# ------------------------------------------------------------------ per-judge descriptives
cur = MAIN["curves"]
k0 = {j: cur[j]["debate_k0"]["estimate"] * 100 for j in JUDGE_ORDER}
macro("errHaikuKzero", f"{k0['haiku']:.2f}")
others = [v for j, v in k0.items() if j != "haiku"]
macro("errOthersKzeroLo", f"{min(others):.2f}")
macro("errOthersKzeroHi", f"{max(others):.2f}")
higher = [j for j in JUDGE_ORDER if cur[j]["debate_k1"]["estimate"] > cur[j]["debate_k0"]["estimate"]]
macro("nJudgesHigherAtOne", {6: "six", 7: "seven", 5: "five"}[len(higher)])
assert [j for j in JUDGE_ORDER if j not in higher] == ["sonnet"]
for j, nm in (("haiku", "Haiku"), ("qwen38", "Qwen")):
    d16 = (cur[j]["debate_k1"]["estimate"] - cur[j]["debate_k6"]["estimate"]) * 100
    macro(f"d{nm}OneSix", f"{d16:.2f}")
macro("errHaikuDebWorld", f"{cur['haiku']['debate_world']['estimate'] * 100:.2f}")
macro("errHaikuWorld", f"{cur['haiku']['world_alone']['estimate'] * 100:.2f}")

# ------------------------------------------------------------------ query use
qu = MAIN["query_use"]
mean6 = {j: qu[f"{j}:debate_k6"]["mean_queries_used"] for j in JUDGE_ORDER}
macro("meanUsedSixLo", f"{min(mean6.values()):.1f}")
macro("meanUsedSixHi", f"{max(mean6.values()):.1f}")
assert min(mean6, key=mean6.get) == "sonnet" and max(mean6, key=mean6.get) == "haiku"
labels = {"YES": 0, "NO": 0, "NOT ADDRESSED": 0, "INVALID": 0}
for j in JUDGE_ORDER:
    for b in (1, 2, 6):
        for lab, n in qu[f"{j}:debate_k{b}"]["oracle_labels"].items():
            labels[lab] += n
tot = sum(labels.values())
macro("nOracleAnswers", f"{tot:,}")
macro("oracleYesShare", f"{labels['YES'] / tot * 100:.1f}")
macro("oracleNaShare", f"{labels['NOT ADDRESSED'] / tot * 100:.1f}")
macro("oracleNoShare", f"{labels['NO'] / tot * 100:.1f}")
macro("haikuMalformedLo", f"{min(qu[f'haiku:debate_k{b}']['sessions_with_malformed_step_share'] for b in (1, 2, 6)) * 100:.0f}")
macro("haikuMalformedHi", f"{max(qu[f'haiku:debate_k{b}']['sessions_with_malformed_step_share'] for b in (1, 2, 6)) * 100:.0f}")
macro("sonnetEarlyDoneSix", f"{qu['sonnet:debate_k6']['done_early_share'] * 100:.0f}")
macro("lunaEarlyDoneSix", f"{qu['luna:debate_k6']['done_early_share'] * 100:.0f}")
macro("sonnetEarlyDoneTwo", f"{qu['sonnet:debate_k2']['done_early_share'] * 100:.0f}")
macro("meanUsedOneLo", f"{min(qu[f'{j}:debate_k1']['mean_queries_used'] for j in JUDGE_ORDER):.2f}")
macro("pHolmOneNoClaude", f"{MAIN['sensitivity']['without_claude_judges']['primary']['p_holm']['b1']:.3f}")
macro("pHolmTwoDeterminate", f"{MAIN['sensitivity']['determinacy_0_or_1']['primary']['p_holm']['b2']:.3f}")

# ------------------------------------------------------------------ gate audit and sensitivity
for b, nm in ((1, "One"), (2, "Two"), (6, "Six")):
    a = AUDIT["arms"][f"debate_k{b}"]
    macro(f"exposed{nm}", f"{a['exposed']['estimate'] * 100:.1f}")
    macro(f"leak{nm}", f"{a['leak_exposed']['estimate'] * 100:.1f}")
tip = A3["tipping"]
macro("tipPeak", f"{tip['b1.peak_supported']['rho'] * 100:.2f}")
macro("tipRuledOutOne", f"{tip['b1.meaningful_peak_ruled_out']['rho'] * 100:.2f}")
macro("tipRuledOutTwo", f"{tip['b2.meaningful_peak_ruled_out']['rho'] * 100:.2f}")
macro("tipWorldSix", f"{tip['secondary.pooled_debate_world_minus_k6']['rho'] * 100:.2f}")
dr = AUDIT["distinct_requests_by_gate_decision"]["admitted"]
macro("nAuditAdmitted", f"{sum(dr.values()):,}")
macro("auditAdmittedFlaggedShare", f"{(dr['VIOLATES'] + dr['BORDERLINE']) / sum(dr.values()) * 100:.1f}")
macro("nAuditSessions", f"{sum(c['sessions'] for c in AUDIT['cells'].values()) if isinstance(AUDIT['cells'], dict) else sum(c['sessions'] for c in AUDIT['cells'])}")

# ------------------------------------------------------------------ flips and explanations
kinds = COUNTS["kinds"]
macro("nPairs", f"{COUNTS['pairs']:,}")
macro("nHarm", str(kinds["harm"]))
macro("nHelp", str(kinds["help"]))
macro("nNetFlips", str(kinds["harm"] - kinds["help"]))
assert abs((kinds["harm"] - kinds["help"]) / COUNTS["pairs"] - comp["D10"]["estimate"]) < 1e-9
mech = EXPL["weighted_counts_of_331"]["mechanism"]
macro("misreadShare", f"{mech['M2_MISREAD_CORRECT_ANSWER'][0] / kinds['harm'] * 100:.0f}")
macro("misreadShareCI", f"{mech['M2_MISREAD_CORRECT_ANSWER'][1] / kinds['harm'] * 100:.0f} to {mech['M2_MISREAD_CORRECT_ANSWER'][2] / kinds['harm'] * 100:.0f}")
macro("oracleErrorShare", f"{mech['M1_ORACLE_ERROR'][0] / kinds['harm'] * 100:.0f}")
macro("nExplRead", str(EXPL["labeled"]))
for key, nm in (("dishonest", "Dis"), ("honest", "Hon"), ("shared", "Both")):
    macro(f"harm{nm}", str(MECH[f"claim source: {key}"]["harm"]))
    macro(f"help{nm}", str(MECH[f"claim source: {key}"]["help"]))

# ------------------------------------------------------------------ benchmark and run facts
macro("nQuestions", f"{BENCH['main_questions']:,}")
macro("nCounted", f"{MAIN['questions']['analysed']:,}")
macro("nWorlds", str(MAIN["questions"]["worlds"]))
macro("worldWordsMedian", f"{BENCH['world_words']['median']:,.0f}")
macro("worldWordsMin", f"{BENCH['world_words']['min']:,}")
macro("worldWordsMax", f"{BENCH['world_words']['max']:,}")
dt = READ["debates"]["turns"] if "debates" in READ and "turns" in READ["debates"] else None
write("numbers.tex", ["% Generated by scripts/build_generated.py from paper/data. Do not edit by hand."] + macros)

# ------------------------------------------------------------------ table: error by arm (main text)
ARM_ROWS = [
    ("debate_k0", "Debate, no queries"), ("debate_k1", "Debate, 1 query offered"), ("debate_k2", "Debate, 2 queries offered"),
    ("debate_k6", "Debate, 6 queries offered"), ("debate_world", "Debate and the world document"),
    ("world_alone", "World document only, no debate"),
]
rows = []
for arm, label in ARM_ROWS:
    m, lo, hi = seven_rate(arm)
    rows.append(f"{label} & {m:.2f} & {lo:.2f} to {hi:.2f} \\\\")
    if arm == "debate_k6":
        rows.append("\\midrule")
write("tab_arms.tex", [
    "\\begin{tabular}{@{}lrr@{}}", "\\toprule", "What the judge gets & Error (\\%) & 95\\% interval \\\\", "\\midrule", *rows,
    "\\bottomrule", "\\end{tabular}"])

# ------------------------------------------------------------------ table: comparisons (main text)
CMP = [("D10", "1 query vs.\\ none"), ("D16", "1 query vs.\\ 6"), ("D20", "2 queries vs.\\ none"), ("D26", "2 queries vs.\\ 6")]
rows = [f"{label} & ${comp[k]['estimate'] * 100:+.2f}$ & {ci_math(comp[k]['ci95'][0] * 100, comp[k]['ci95'][1] * 100)} \\\\"
        for k, label in CMP]
rows.append("\\midrule")
rows.append(f"6 queries vs.\\ none & ${s60['estimate'] * 100:+.2f}$ & {ci_math(s60['ci95'][0] * 100, s60['ci95'][1] * 100)} \\\\")
rows.append(f"World added vs.\\ 6 queries & ${sw6['estimate'] * 100:+.2f}$ & {ci_math(sw6['ci95'][0] * 100, sw6['ci95'][1] * 100)} \\\\")
write("tab_contrasts.tex", [
    "\\begin{tabular}{@{}lrr@{}}", "\\toprule", "Comparison & Difference & 95\\% interval \\\\", "\\midrule",
    *rows, "\\bottomrule", "\\end{tabular}"])

# ------------------------------------------------------------------ table: error by judge and arm (supplement)
def cell(c: dict) -> str:
    est = c["estimate"] * 100
    ci = c.get("ci95")
    if not ci:
        return f"{est:.2f} (n/a)"
    lo, hi = ci[0] * 100, ci[1] * 100
    return f"{est:.2f} ({lo:.2f}, {'$' + chr(92) + 'infty$' if math.isinf(hi) else f'{hi:.2f}'})"


ORDER_SUPP = ["world_alone", "debate_k0", "debate_k1", "debate_k2", "debate_k6", "debate_world"]
rows = [f"{JUDGE_NAME[j]} & " + " & ".join(cell(cur[j][a]) for a in ORDER_SUPP) + " \\\\" for j in JUDGE_ORDER]
pooled = []
for a in ORDER_SUPP:
    m, lo, hi = seven_rate(a)
    pooled.append(f"{m:.2f} ({lo:.2f}, {hi:.2f})")
rows += ["\\midrule", "Seven-judge mean & " + " & ".join(pooled) + " \\\\"]
write("tab_by_judge.tex", [
    "\\begin{tabular}{lrrrrrr}", "\\toprule",
    " & World only & \\multicolumn{4}{c}{Debate, queries offered} & Debate and \\\\",
    "Judge & (no debate) & 0 & 1 & 2 & 6 & world \\\\", "\\midrule", *rows, "\\bottomrule", "\\end{tabular}"])

# ------------------------------------------------------------------ table: full primary (supplement)
NAME = {"D10": "$e_1-e_0$", "D16": "$e_1-e_6$", "D20": "$e_2-e_0$", "D26": "$e_2-e_6$"}
rows = []
for k in ("D10", "D16", "D20", "D26"):
    c = comp[k]
    rows.append(f"{NAME[k]} & ${c['estimate'] * 100:+.2f}$ & {c['se'] * 100:.2f} & "
                f"{ci_math(c['ci95'][0] * 100, c['ci95'][1] * 100)} & {ci_math(bounds[k][0] * 100, bounds[k][1] * 100)} & "
                f"{pval(c['p'])} \\\\")
write("tab_primary_full.tex", [
    "\\begin{tabular}{lrrrrr}", "\\toprule",
    "Contrast & Estimate & SE & 95\\% interval & Simultaneous 95\\% & One-sided $p$ \\\\", "\\midrule", *rows,
    "\\bottomrule", "\\end{tabular}"])
macro_check = (S7["primary"]["p_iut"], S7["primary"]["p_holm"])

SEC_NAME = {"pooled_k6_minus_k0": "Pooled, 6 queries vs.\\ none", "pooled_debate_world_minus_k6": "Pooled, world added vs.\\ 6 queries",
            "llama70_k1_minus_k0": "Llama-70B, 1 query vs.\\ none", "llama70_k6_minus_k0": "Llama-70B, 6 queries vs.\\ none",
            "qwen38_k1_minus_k0": "Qwen3.8, 1 query vs.\\ none", "qwen38_k6_minus_k0": "Qwen3.8, 6 queries vs.\\ none"}
rows = []
for k, label in SEC_NAME.items():
    s = sec[k]
    if "estimate" in s:
        rows.append(f"{label} & ${s['estimate'] * 100:+.2f}$ & {ci_math(s['ci95'][0] * 100, s['ci95'][1] * 100)} & "
                    f"{pval(s['p'])} & {pval(s['p_holm'])} \\\\")
    else:
        rows.append(f"{label} & \\multicolumn{{2}}{{c}}{{not run (model withdrawn)}} & 1 & 1 \\\\")
write("tab_secondary.tex", [
    "\\begin{tabular}{lrrrr}", "\\toprule", "Contrast (two-sided) & Estimate & 95\\% interval & $p$ & Holm $p$ \\\\",
    "\\midrule", *rows, "\\bottomrule", "\\end{tabular}"])

# ------------------------------------------------------------------ table: query use (supplement)
rows = []
for j in JUDGE_ORDER:
    for b in (1, 2, 6):
        q = qu[f"{j}:debate_k{b}"]
        rows.append(f"{JUDGE_NAME[j] if b == 1 else ''} & {b} & {q['mean_queries_used']:.2f} & {q['done_early_share'] * 100:.0f} & "
                    f"{q['gate_rejections_per_session']:.2f} & {q['sessions_with_malformed_step_share'] * 100:.0f} & "
                    f"{q['sessions_touching_share'] * 100:.0f} & {q['error_when_touching'] * 100:.2f} & "
                    f"{q['error_when_not_touching'] * 100:.2f} \\\\")
    if j != JUDGE_ORDER[-1]:
        rows.append("\\addlinespace")
write("tab_query_use.tex", [
    "\\begin{tabular}{lrrrrrrrr}", "\\toprule",
    " & Queries & Mean & Stopped & Gate rejections & Malformed & Touched a & \\multicolumn{2}{c}{Error (\\%) when a query} \\\\",
    "Judge & offered & used & early (\\%) & per session & step (\\%) & key fact (\\%) & touched one & touched none \\\\",
    "\\midrule", *rows, "\\bottomrule", "\\end{tabular}"])

# ------------------------------------------------------------------ table: gate audit and tipping points (supplement)
rows = []
for b in (1, 2, 6):
    a = AUDIT["arms"][f"debate_k{b}"]
    rows.append(f"{b} & 280 & {a['exposed']['estimate'] * 100:.1f} & {a['exposed']['upper'] * 100:.1f} & "
                f"{a['leak_exposed']['estimate'] * 100:.1f} & {a['leak_exposed']['upper'] * 100:.1f} \\\\")
write("tab_gate_audit.tex", [
    "\\begin{tabular}{rrrrrr}", "\\toprule",
    "Queries & Sessions & \\multicolumn{2}{c}{Any flagged query (\\%)} & \\multicolumn{2}{c}{Answer-revealing flag (\\%)} \\\\",
    "offered & sampled & Estimate & Upper bound & Estimate & Upper bound \\\\", "\\midrule", *rows, "\\bottomrule",
    "\\end{tabular}"])
TIP = [("b1.peak_supported", "Error is higher with 1 query than with none and with 6"),
       ("b1.meaningful_peak_ruled_out", "A 1.5-point peak at 1 query is ruled out"),
       ("b2.meaningful_peak_ruled_out", "A 1.5-point peak at 2 queries is ruled out"),
       ("secondary.pooled_debate_world_minus_k6", "Adding the world document beats 6 queries")]
rows = [f"{label} & {tip[k]['rho'] * 100:.2f} \\\\" for k, label in TIP]
write("tab_tipping.tex", [
    "\\begin{tabular}{lr}", "\\toprule", " & Common affected share in \\\\", "Finding & each query condition (\\%) \\\\", "\\midrule",
    *rows, "\\bottomrule", "\\end{tabular}"])

# ------------------------------------------------------------------ table: sensitivity panels (supplement)
SENS_LABELS = SENS = [("without_sonnet", "Without Sonnet 5.5 as a judge"), ("without_claude_judges", "Without the three Claude judges"),
        ("determinacy_0_or_1", "Only the 806 questions with at most one validator flag"),
        ("with_sensitivity_only_questions", "Adding the two set-aside questions"),
        ("without_flagged_debates", "Without the two flagged debates")]
rows = []
for k, label in SENS:
    p = MAIN["sensitivity"][k]["primary"]
    yes = lambda b: "yes" if p["peaks"][b]["supported"] else "no"  # noqa: E731
    rows.append(f"{label} & {yes('b1')} & {pval(p['p_holm']['b1'])} & {yes('b2')} & {pval(p['p_holm']['b2'])} \\\\")
p = S7["primary"]
rows = [f"Main analysis (seven judges) & {'yes' if p['peaks']['b1']['supported'] else 'no'} & {pval(p['p_holm']['b1'])} & "
        f"{'yes' if p['peaks']['b2']['supported'] else 'no'} & {pval(p['p_holm']['b2'])} \\\\", "\\midrule"] + rows
write("tab_sensitivity.tex", [
    "\\begin{tabular}{lrrrr}", "\\toprule",
    " & \\multicolumn{2}{c}{Peak at 1 query} & \\multicolumn{2}{c}{Peak at 2 queries} \\\\",
    "Analysis & Supported & Holm $p$ & Supported & Holm $p$ \\\\", "\\midrule", *rows, "\\bottomrule", "\\end{tabular}"])

# ------------------------------------------------------------------ table: flips by oracle answer (supplement)
rows = []
for key, label in (("YES", "YES"), ("NOT ADDRESSED", "NOT ADDRESSED"), ("NO", "NO"), ("none", "No answer received")):
    c = COUNTS["by_k1_answer"][key]
    n = sum(c.values())
    rows.append(f"{label} & {n:,} & {c.get('harm', 0)} & {c.get('help', 0)} & ${c.get('harm', 0) - c.get('help', 0):+d}$ \\\\")
rows += ["\\midrule", f"All pairs & {COUNTS['pairs']:,} & {kinds['harm']} & {kinds['help']} & ${kinds['harm'] - kinds['help']:+d}$ \\\\"]
write("tab_flips.tex", [
    "\\begin{tabular}{lrrrr}", "\\toprule", "Oracle's answer to the one query & Pairs & Harmful flips & Helpful flips & Net \\\\",
    "\\midrule", *rows, "\\bottomrule", "\\end{tabular}"])
# ------------------------------------------------------------------ table: the estimate, phase by phase (supplement)
PH = J("earlier_phases.json")["phases"]
rows = []
for p in PH:
    rows.append(f"{p['label']}{' (' + p['label_note'] + ')' if p.get('label_note') else ''}, {p['when']} & "
                f"{p['judges']} & {p['debaters']} & {p['questions']} & "
                f"{p['comparison'].replace('vs. ', 'vs.' + chr(92) + ' ')} & "
                f"${p['estimate']:+.2f}$ {ci_math(p['lo'], p['hi'])} \\\\")
    rows.append("\\addlinespace")
d10 = comp["D10"]
rows.append(f"Final phase, October 2026 & the seven judges of this paper & Sonnet 5.5 (honest) vs.\\ Fable 5.1 & "
            f"{MAIN['questions']['analysed']:,} & 1 query vs.\\ none & ${d10['estimate'] * 100:+.2f}$ "
            f"{ci_math(d10['ci95'][0] * 100, d10['ci95'][1] * 100)} \\\\")
write("tab_phases.tex", [
    "\\begin{tabular}{@{}p{0.11\\linewidth}p{0.19\\linewidth}p{0.17\\linewidth}rp{0.13\\linewidth}r@{}}", "\\toprule",
    "Phase & Judges & Debaters & Questions & Comparison & Rise in error \\\\", "\\midrule", *rows, "\\bottomrule",
    "\\end{tabular}"])
# ------------------------------------------------------------------ gate: equivalence check and audit labels (supplement)
EQ = J("gate_endpoint_comparison.json")
t = EQ["table_pilot_by_first"]
cell = lambda a, b: t.get(f"{a}->{b}", 0)  # noqa: E731
rows = []
for key, label in (("ADMIT", "Admitted"), ("REJECT", "Rejected"), ("non-decision", "No decision")):
    vals = [cell(key, c) for c in ("ADMIT", "REJECT", "non-decision")]
    rows.append(f"{label} & {vals[0]:,} & {vals[1]:,} & {vals[2]:,} & {sum(vals):,} \\\\")
assert sum(t.values()) == EQ["requests"]
write("tab_equiv_decisions.tex", [
    "\\begin{tabular}{lrrrr}", "\\toprule", " & \\multicolumn{3}{c}{Dedicated deployment, first pass} & \\\\",
    "Development gate & Admitted & Rejected & No decision & Total \\\\", "\\midrule", *rows, "\\bottomrule",
    "\\end{tabular}"])
dis = EQ["disagreements_by"]
macro("equivDisagreements", f"{cell('ADMIT', 'REJECT') + cell('REJECT', 'ADMIT'):,}")
assert sum(dis["world"].values()) == cell("ADMIT", "REJECT") + cell("REJECT", "ADMIT")
names = dict(JUDGE_NAME, llama70="Llama 3.3 70B")
macro("equivByJudge", ", ".join(f"{names[j].replace('GPT-5.6 ', '').replace('Claude ', '')} {n}"
                                for j, n in sorted(dis["judge"].items(), key=lambda kv: -kv[1])))
macro("equivByBudget", "{}, {} and {}".format(*(dis["arm"][f"debate_k{b}"] for b in (1, 2, 6))))
macro("equivWorlds", str(len(dis["world"])))
macro("equivByWorldLo", str(min(dis["world"].values())))
macro("equivByWorldHi", str(max(dis["world"].values())))
for th, nm in zip(EQ["throughput"], ("First", "Repeat")):
    macro(f"equiv{nm}Sent", f"{th['sent']:,}")
    macro(f"equiv{nm}Seconds", f"{th['seconds']:,.0f}")
    macro(f"equiv{nm}Rate", f"{th['calls_per_s']:.1f}")
gd = AUDIT["distinct_requests_by_gate_decision"]
macro("auditAdmViol", str(gd["admitted"]["VIOLATES"]))
macro("auditAdmBord", str(gd["admitted"]["BORDERLINE"]))
macro("auditRejected", str(sum(gd["rejected"].values())))
macro("auditRejViol", str(gd["rejected"]["VIOLATES"]))
macro("auditRejAdm", str(gd["rejected"]["ADMISSIBLE"]))
macro("auditRejBord", str(gd["rejected"]["BORDERLINE"]))
pa = AUDIT["labels_by_source"]["pilot:admitted"]
macro("pilotAdmSampled", str(sum(pa.values())))
macro("pilotAdmViol", str(pa["VIOLATES"]))
macro("pilotAdmBord", str(pa["BORDERLINE"]))
macro("pilotAdmFlaggedShare", f"{(pa['VIOLATES'] + pa['BORDERLINE']) / sum(pa.values()) * 100:.0f}")

# ------------------------------------------------------------------ context numbers added after the council review
# Every value below is read from the frozen files or is arithmetic on values already reported; nothing is re-analysed.
e_zero = seven_rate("debate_k0")[0]
macro("relRise", f"{comp['D10']['estimate'] * 100 / e_zero * 100:.0f}")
macro("haikuErrShare", f"{k0['haiku'] / sum(k0.values()) * 100:.0f}")
assert sum(v < 3 for v in k0.values()) == 6
gap = {j: (cur[j]["debate_world"]["estimate"] - cur[j]["world_alone"]["estimate"]) * 100 for j in JUDGE_ORDER}
macro("haikuWorldGapShare", f"{gap['haiku'] / sum(gap.values()) * 100:.0f}")
macro("errSonnetKzero", f"{k0['sonnet']:.2f}")
macro("errSonnetKone", f"{cur['sonnet']['debate_k1']['estimate'] * 100:.2f}")
n_counted = MAIN["questions"]["analysed"]
macro("nUnreadable", str(MAIN["sensitivity"]["unparsed_as_missing"]["unparsed"] - 6 * 2 * n_counted))  # minus Llama's
macro("nSevenSessions", f"{7 * 6 * 2 * n_counted:,}")
cut = lambda j, arm: READ["calls"][f"judge:{j}:{arm}:verdict"]["truncation"]["truncated"]  # noqa: E731
CUT_ARMS = (("debate_k0", "Zero"), ("debate_k1", "One"), ("debate_k2", "Two"), ("debate_k6", "Six"),
            ("debate_world", "DebWorld"), ("world_alone", "World"))
for arm, nm in CUT_ARMS:
    macro(f"qwenCut{nm}", str(cut("qwen38", arm)))
macro("haikuCutZero", str(cut("haiku", "debate_k0")))
assert sum(cut(j, arm) for j in JUDGE_ORDER for arm, _ in CUT_ARMS) == sum(cut("qwen38", arm) for arm, _ in CUT_ARMS) + cut("haiku", "debate_k0")
macro("nCutVerdicts", str(sum(cut(j, arm) for j in JUDGE_ORDER for arm, _ in CUT_ARMS)))
macro("nCutSessionsPerCell", f"{READ['judge_sessions']['qwen38:debate_k0']:,}")

SENS = MAIN["sensitivity"]
nc = SENS["without_claude_judges"]
assert "llama70" in nc["primary"]["judges"] and len(nc["primary"]["judges"]) == 5
f_nc = 5 / 4  # the registered panel counts Llama as a constant; rescale to the four judges with data
macro("ncRise", f"{nc['primary']['components']['D10']['estimate'] * 100 * f_nc:.2f}")
macro("ncFall", f"{nc['primary']['components']['D16']['estimate'] * 100 * f_nc:.2f}")
macro("ncPJoint", f"{nc['primary']['p_iut']['b1']:.3f}")
macro("ncSixZero", f"{nc['secondary_pooled']['pooled_k6_minus_k0']['estimate'] * 100 * f_nc:.2f}")
macro("ncSixZeroP", f"{nc['secondary_pooled']['pooled_k6_minus_k0']['p']:.3f}")
assert nc["secondary_pooled"]["pooled_k6_minus_k0"]["ci95"][0] > 0
macro("detRise", f"{SENS['determinacy_0_or_1']['primary']['components']['D10']['estimate'] * 100 * 8 / 7:.2f}")

macro("flipHelpUnread", str(COUNTS["unparsed"]["help"]))
macro("flipHarmUnread", str(COUNTS["unparsed"]["harm"]))
macro("haikuHarm", str(COUNTS["by_judge"]["haiku"]["harm"]))
for key, nm in (("dishonest", "Dis"), ("honest", "Hon"), ("shared", "Both")):
    macro(f"pairs{nm}", f"{MECH[f'claim source: {key}']['pairs']:,}")
for b, nm in ((1, "One"), (2, "Two"), (6, "Six")):
    adm = flagged = 0
    for cell, v in AUDIT["cells"].items():
        if cell.endswith(f"debate_k{b}"):
            adm += sum(v["admitted_labels"].values())
            flagged += sum(n for lab, n in v["admitted_labels"].items() if lab != "ADMISSIBLE")
    macro(f"flagRate{nm}", f"{flagged / adm * 100:.1f}")
macro("auditViolTotal", str(AUDIT["labels_by_source"]["main"]["VIOLATES"]))
hk = lambda b: AUDIT["cells"][f"haiku:debate_k{b}"]  # noqa: E731
macro("haikuFlagTwo", f"{hk(2)['exposed'] / hk(2)['sessions'] * 100:.1f}")
macro("haikuFlagSix", f"{hk(6)['exposed'] / hk(6)['sessions'] * 100:.1f}")
n_arm = sum(v["sessions"] for cell, v in AUDIT["cells"].items() if cell.endswith("debate_k1"))
macro("auditZeroFlagBound", f"{(1 - 0.05 ** (1 / n_arm)) * 100:.2f}")
macro("nAuditPerBudget", str(n_arm))
assert n_arm == 280
FINDINGS = (("b1", "peak_supported"), ("b1", "meaningful_peak_ruled_out"), ("b2", "meaningful_peak_ruled_out"),
            ("secondary", "pooled_debate_world_minus_k6"))
for reading, a in A3["audit"].items():   # the text says each finding is lost under each audit reading
    assert not any(a["claims"][grp][name] for grp, name in FINDINGS), reading
macro("oracleCalls", f"{READ['calls']['oracle:opus']['status']['ok']:,}")
sp = READ["spend"]["by_component_usd"]
macro("spendOther", f"{sp['compliance'] + sp['gate']:.0f}")

# ------------------------------------------------------------------ table: registered contrasts in each sensitivity panel
def _sens_row(label, prim, sec, factor):
    c = prim["components"]
    cells = [label, f"${c['D10']['estimate'] * 100 * factor:+.2f}$", pval(c["D10"]["p"]),
             f"${c['D16']['estimate'] * 100 * factor:+.2f}$", pval(c["D16"]["p"]),
             pval(prim["p_iut"]["b1"]), pval(prim["p_holm"]["b1"])]
    for key in ("pooled_k6_minus_k0", "pooled_debate_world_minus_k6"):
        if sec and key in sec:
            cells += [f"${sec[key]['estimate'] * 100 * factor:+.2f}$", pval(sec[key]["p"])]
        else:
            cells += ["n/r", ""]
    return " & ".join(cells) + " \\\\"

rows = [_sens_row("Main analysis (seven judges)", S7["primary"], S7["secondary"], 1.0), "\\midrule"]
for key, label in SENS_LABELS:
    s = SENS[key]
    n = len(s["primary"]["judges"])
    assert "llama70" in s["primary"]["judges"]
    rows.append(_sens_row(label, s["primary"], s.get("secondary_pooled") or s.get("secondary"), n / (n - 1)))
write("tab_sens_contrasts.tex", [
    "\\begin{tabular}{@{}>{\\raggedright\\arraybackslash}p{0.25\\linewidth}rrrrrrrrrr@{}}", "\\toprule",
    " & \\multicolumn{2}{c}{1 query vs.\\ none} & \\multicolumn{2}{c}{1 query vs.\\ 6} & \\multicolumn{2}{c}{Joint peak test} & "
    "\\multicolumn{2}{c}{6 queries vs.\\ none} & \\multicolumn{2}{c}{World added vs.\\ 6} \\\\",
    "Analysis & Est. & $p$ & Est. & $p$ & $p$ & corrected & Est. & $p$ & Est. & $p$ \\\\", "\\midrule", *rows,
    "\\bottomrule", "\\end{tabular}"])

# ------------------------------------------------------------------ table: all registered secondary contrasts in the two
# sensitivity panels that registered them with a Holm correction (preregistration 7.5)
SEC_ROWS = (("pooled_k6_minus_k0", "Pooled, 6 queries vs.\\ none", 8 / 7), ("pooled_debate_world_minus_k6", "Pooled, world added vs.\\ 6 queries", 8 / 7),
            ("qwen38_k1_minus_k0", "Qwen3.8, 1 query vs.\\ none", 1.0), ("qwen38_k6_minus_k0", "Qwen3.8, 6 queries vs.\\ none", 1.0))
rows = []
for key, label in (("determinacy_0_or_1", "806 questions with at most one validator flag"),
                   ("with_sensitivity_only_questions", "1,070 questions (two set-aside questions added)")):
    sec_p = SENS[key]["secondary"]
    assert len(sec_p) == 6 and all(sec_p[f"llama70_k{b}_minus_k0"].get("p_holm", 1) == 1 for b in (1, 6))
    rows.append(f"\\multicolumn{{5}}{{@{{}}l}}{{\\emph{{{label}}}}} \\\\")
    for ckey, clabel, f in SEC_ROWS:
        c = sec_p[ckey]
        rows.append(f"\\quad {clabel} & ${c['estimate'] * 100 * f:+.2f}$ & {ci_math(c['ci95'][0] * 100 * f, c['ci95'][1] * 100 * f)} & "
                    f"{pval(c['p'])} & {pval(c['p_holm'])} \\\\")
    rows.append("\\addlinespace")
write("tab_sens_secondary.tex", [
    "\\begin{tabular}{@{}lrrrr@{}}", "\\toprule", "Contrast (two-sided) & Estimate & 95\\% interval & $p$ & Holm $p$ \\\\", "\\midrule",
    *rows[:-1], "\\bottomrule", "\\end{tabular}"])
macro("detQwenHolm", pval(SENS["determinacy_0_or_1"]["secondary"]["qwen38_k1_minus_k0"]["p_holm"]))
macro("nOracleErrCases", str(sum(v.get("M1_ORACLE_ERROR", 0) for v in EXPL["mechanism_by_stratum_raw"].values())))
macro("oracleErrorCI", f"{mech['M1_ORACLE_ERROR'][1] / kinds['harm'] * 100:.1f} to {mech['M1_ORACLE_ERROR'][2] / kinds['harm'] * 100:.0f}")
macro("nAnsweredFlips", str(kinds["harm"] - EXPL["weighted_counts_of_331"]["no_answer"]))
for author, nm in (("astra", "Astra"), ("fable", "Fable")):   # speaking order by world author (preregistration 7.6)
    ob = MAIN["order_balance"]["by_author"][author]
    macro(f"order{nm}First", str(ob["honest_first"]))
    macro(f"order{nm}N", str(ob["questions"]))

p2 = next(p for p in PH if p["key"] == "phase2")
macro("phaseTwoAsRun", f"{p2['as_run']['estimate']:.2f}")
macro("phaseTwoAsRunCI", f"{p2['as_run']['lo']:.2f} to {p2['as_run']['hi']:.2f}")
write("numbers.tex", ["% Generated by scripts/build_generated.py from paper/data. Do not edit by hand."] + macros)
print("oracle labels", labels, tot, "| judges higher at one:", higher)
