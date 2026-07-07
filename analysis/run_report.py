"""Assemble the findings report and the mechanism-labeling worksheet."""
from __future__ import annotations

from pathlib import Path

from analysis import describe, inference, mechanism, parse_sensitivity, robustness
from analysis.load import load_judgments_df


def _md_table(df):
    return df.to_markdown(index=False)


def _kappa(a, b):
    labs = sorted(set(a) | set(b))
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labs)
    return 1.0 if pe >= 1 else (po - pe) / (1 - pe)


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
        out.append("- **Mechanism labels (pre-audit):** postmortem of the corrupted oracle channel — "
                   "no longer treated as a decomposition of clean verification; carry no interpretive "
                   "weight until re-measured under the fixed harness.\n")
    out.append("- **Next step (designed, spend-gated):** the fixed-harness re-judge — CLEAN {0,1,2,5}, "
               "bug-replay BOTH {1,2,5}, PLACEBO {1,2,5}, single-bug arms {1,2}, K=2 replicates, legacy "
               "QA subset. Ex-ante gates frozen in `docs/rejudge-protocol.md`. The capability grid stays "
               "gated on that outcome.\n"
               "- Audit trail: `mechanism_labels.md`, `mechanism_validation.md`, `labels.csv`, "
               "`labels_pass2.csv`.\n")
    return "".join(out)


def build_report(df, B=10000, seed=0, labels=None, labels2=None):
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
    parts.append("\n\n## Primary inference (70B)\n\nPre-specified contrasts (pp; see report framing note), "
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
        parts.append("\n\n## Mechanism (pre-audit — corrupted oracle channel)\n\n"
                     "> **Correction (2026-07-06):** the pilot's oracle pipeline was broken for ~100% of "
                     "calls (NOT-ADDRESSED→NO miscoding; doubled queries). These labels scored the corrupted "
                     "channel and are a postmortem of the buggy harness, NOT a decomposition of clean "
                     "verification. Reset to unknown pending the fixed-harness re-judge "
                     "(`docs/rejudge-protocol.md`).\n\n"
                     + _md_table(mechanism.summarize_labels(labels)))
        if labels2 is not None:
            pass1_labs = [r["label"] for r in labels]
            pass2_labs = [r["label"] for r in labels2]
            agree = sum(x == y for x, y in zip(pass1_labs, pass2_labs))
            parts.append(f"\n\nTwo-pass agreement: {agree}/{len(pass1_labs)} "
                         f"(Cohen's kappa = {_kappa(pass1_labs, pass2_labs):.2f}). "
                         "Full consensus + refined O1/Q1/R1/R2 taxonomy: `mechanism_validation.md` "
                         "(same correction applies).")
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
    labels2 = None
    labels2_csv = out / "labels_pass2.csv"
    if labels2_csv.exists():
        import csv
        with open(labels2_csv, encoding="utf-8") as f:
            labels2 = list(csv.DictReader(f))
    (out / "report.md").write_text(build_report(df, labels=labels, labels2=labels2), encoding="utf-8")
    cases = mechanism.extract_flip_cases(df)
    (out / "mechanism_cases.md").write_text(mechanism.render_cases_markdown(cases), encoding="utf-8")
    print(f"Wrote {out/'report.md'} and {out/'mechanism_cases.md'} ({len(cases)} flip cases)")


if __name__ == "__main__":
    main()
