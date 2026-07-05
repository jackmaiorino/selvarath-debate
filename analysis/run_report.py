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


def build_report(df, B=10000, seed=0, labels=None):
    summ = inference.summarize(df, "70B", B=B, seed=seed)
    treat = parse_sensitivity.delta_few_under_treatments(df, "70B")
    passed, row, a, b = _gate(summ)
    parse_ok = min(treat.values()) > 2.0

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
                 + "\n".join(f"- {k}: {v:.2f}" for k, v in treat.items()))
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
                 f"- Survives parse-sensitivity (min treatment {min(treat.values()):.2f} pp): "
                 f"{'PASS' if parse_ok else 'FAIL'}\n"
                 f"- **Overall harm claim: {'BANKED' if passed and parse_ok else 'NOT banked'}**\n"
                 "- Next step gated on FM1/FM2 split (see mechanism_cases.md).\n")
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
