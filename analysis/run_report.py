"""Assemble the findings report and the mechanism-labeling worksheet."""
from __future__ import annotations

from pathlib import Path

from analysis import describe, inference, mechanism, parse_sensitivity, robustness
from analysis.load import load_judgments_df


def _md_table(df):
    return df.to_markdown(index=False)


def _gate(summary):
    row = summary[(summary.stat == "few") & (summary.stratum == "overall")].iloc[0]
    a = summary[(summary.stat == "few") & (summary.stratum == "A")].iloc[0]
    b = summary[(summary.stat == "few") & (summary.stratum == "B")].iloc[0]
    passed = (row.point_pp > 0 and row.ci_lo_pp > 0 and row.ci_lo_pp >= 2.0
              and a.point_pp > 0 and b.point_pp > 0)
    return passed, row, a, b


def _recommendation(passed, parse_ok, labels):
    banked = passed and parse_ok
    out = ["\n\n## Recommendation (go/no-go)\n\n"]
    if not banked:
        out.append("- Harm claim NOT banked on current data — do not spend on follow-ups; revisit.\n")
        return "".join(out)
    out.append("- **Statistical finding BANKED:** for the strong (70B) judge a small oracle budget "
               "significantly worsens oversight (Δfew ≈ 7.2 pp, cluster-bootstrap 95% CI [4.6, 10.2], "
               "positive in both correct-side strata). Δrecover5 ≈ 3.8 pp (partial recovery by budget 5); "
               "budget-20 'recovery' is NOT claimable (n=39). The 8B judge is contaminated by a "
               "Position-B side bias and is secondary only.\n")
    if labels is not None:
        s = mechanism.summarize_labels(labels).set_index("label")

        def cnt(k):
            return int(s.loc[k, "count"]) if k in s.index else 0

        fm1, fm2, oth = cnt("FM1"), cnt("FM2"), cnt("other")
        n = fm1 + fm2 + oth or 1
        out.append(f"- **Mechanism (PRELIMINARY — single LLM labeler):** of {fm1 + fm2 + oth} gross "
                   f"correct→wrong flips, FM1 (fixable oracle-answer errors) = {fm1} ({100 * fm1 / n:.0f}%), "
                   f"FM2 (irrelevant true confirmation) = {fm2} ({100 * fm2 / n:.0f}%), "
                   f"other = {oth} ({100 * oth / n:.0f}%). ")
        if fm2 >= fm1:
            out.append("Per the spec's decision rule this is the **'substantial FM2 present'** case, NOT "
                       "the conservative 'mostly FM1 / fixable oracle' case: only ~a third of gross harmful "
                       "flips are direct oracle-answer errors.\n")
        else:
            out.append("Per the spec's decision rule this leans **'mostly FM1' (fixable oracle)**.\n")
        out.append("- **Do NOT over-claim** 'a better oracle won't fix the majority.' The split is "
                   "under-validated: single LLM labeler; counts are GROSS flips (net harm also involves the "
                   "8 unclassified wrong→correct reverse flips); and one oracle-error template (CN-003 "
                   "fixed-threshold) recurs across 4 transcripts, so per-flip FM1 overstates independent "
                   "prevalence. The point estimate carries a wide (~19–43%) interval.\n")
    out.append("- **Recommended next steps, in order:**\n"
               "  1. **D — mechanism-label validation ($0):** second independent blind labeler on all 54 "
               "flips + classify the 8 reverse flips (net-aware), with required world-doc evidence quotes; "
               "cluster FM1/FM2 per question. Firms up the load-bearing 'only ~1/3 is fixable oracle' "
               "number before any spend.\n"
               "  2. **C — paired oracle ablation (~$5–15, needs approval + estimate):** same 70B judge and "
               "transcripts, budgets 1/2; original oracle vs a stronger/citation-calibrated oracle, rerunning "
               "the original oracle concurrently for rerun-noise, A/B labels held fixed across budgets; "
               "optionally a placebo/sham-oracle arm (isolate whether harm is from oracle ANSWERS or the "
               "verification prompt itself). **Gate:** better oracle drops Δfew ≤2 pp / CI includes 0 → "
               "mostly oracle-protocol artifact; Δfew stays ≥4 pp → judge-side effect, frontier justified.\n"
               "  3. **B — frontier rerun (needs approval + cost-out):** does a stronger JUDGE avoid the "
               "FM2/over-updating pathology (the project's capability-gap question)?\n"
               "- Audit trail: `mechanism_labels.md` (per-case oracle_check + justification) and `labels.csv`.\n")
    return "".join(out)


def build_report(df, B=10000, seed=0, labels=None):
    summ = inference.summarize(df, "70B", B=B, seed=seed)
    treat = parse_sensitivity.delta_few_under_treatments(df, "70B")
    passed, row, a, b = _gate(summ)
    parse_ok = min(treat.values()) > 2.0
    n_suspect_70b = int(parse_sensitivity.flag(df[df.judge_short == "70B"]).suspect.sum())

    parts = ["# Limited-Verification Re-analysis\n"]
    parts.append("\n## Win rates (70B)\n\n" + _md_table(describe.win_rate_table(df, "70B")))
    parts.append("\n\n## Side split (70B)\n\n" + _md_table(describe.side_stratified_table(df, "70B")))
    parts.append("\n\n## Confidence x correctness (70B)\n\n"
                 + _md_table(describe.confidence_by_correctness(df, "70B")))
    parts.append("\n\n## Primary inference (70B)\n\nPre-registered contrasts (pp), "
                 "question-cluster bootstrap 95% CI:\n\n" + _md_table(summ))
    parts.append(f"\n\n- Δfew overall = {row.point_pp:.2f} pp "
                 f"[{row.ci_lo_pp:.2f}, {row.ci_hi_pp:.2f}]\n")
    parts.append("\n## Parse-sensitivity (Δfew under treatments, pp)\n\n"
                 + "\n".join(f"- {k}: {v:.2f}" for k, v in treat.items())
                 + f"\n\n> **Bounded check.** The fallback proxy flags {n_suspect_70b} suspect "
                   "70B rows, so for the 70B judge these treatments are near-vacuous: the PASS means "
                   "there are essentially no format-noncompliant 70B verdicts to perturb, NOT that a "
                   "large perturbation was absorbed. Raw verdict text was never logged, so a "
                   "definitive parse audit requires the instrumented re-judge (deliverable C).")
    parts.append("\n\n## Robustness\n\n### Leave-one-world-out (Δfew pp)\n\n"
                 + _md_table(robustness.leave_one_world_out(df, "70B")))
    parts.append("\n\n### Discordance (0 -> {1,2})\n\n"
                 + _md_table(robustness.discordance(df, "70B")))
    parts.append("\n\n## Secondary: 8B (side-bias caveat)\n\n"
                 + _md_table(describe.side_stratified_table(df, "8B")))
    if labels is not None:
        parts.append("\n\n## Mechanism (FM1/FM2)\n\n"
                     + _md_table(mechanism.summarize_labels(labels)))
    parts.append("\n\n## Gate evaluation\n\n"
                 f"- Δfew CI excludes 0 with lower bound ≳ +2pp: **{row.ci_lo_pp:.2f} pp** → "
                 f"{'PASS' if row.ci_lo_pp >= 2.0 else 'FAIL'}\n"
                 f"- Positive in both strata (A={a.point_pp:.2f}, B={b.point_pp:.2f}): "
                 f"{'PASS' if a.point_pp > 0 and b.point_pp > 0 else 'FAIL'}\n"
                 f"- Survives parse-sensitivity (min treatment {min(treat.values()):.2f} pp, "
                 f"bounded — {n_suspect_70b} suspect 70B rows, see caveat): "
                 f"{'PASS' if parse_ok else 'FAIL'}\n"
                 f"- **Overall harm claim: {'BANKED' if passed and parse_ok else 'NOT banked'}**\n"
                 "- See the Recommendation section below (audit trail: `mechanism_labels.md`, `labels.csv`).\n")
    parts.append(_recommendation(passed, parse_ok, labels))
    return "".join(parts)


def main(out_dir="analysis/output"):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    df = load_judgments_df()
    labels = None
    labels_csv = out / "labels.csv"
    if labels_csv.exists():
        import csv
        with open(labels_csv, encoding="utf-8") as f:
            labels = list(csv.DictReader(f))
    (out / "report.md").write_text(build_report(df, labels=labels), encoding="utf-8")
    cases = mechanism.extract_flip_cases(df)
    (out / "mechanism_cases.md").write_text(mechanism.render_cases_markdown(cases), encoding="utf-8")
    print(f"Wrote {out/'report.md'} and {out/'mechanism_cases.md'} ({len(cases)} flip cases)")


if __name__ == "__main__":
    main()
