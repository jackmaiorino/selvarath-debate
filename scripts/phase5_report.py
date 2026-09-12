"""Render aggregate Phase5 results without scoring responses or changing gates.

Requires matplotlib (available in C:\\Python314\\python.exe on the experiment host).
Formal: --analysis FILE --manifest FILE --completion FILE [--status FILE] --out DIR
Fixture: --analysis FILE --manifest FILE --status FILE --validation FILE --synthetic --out DIR
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODELS = ("Qwen/Qwen3.8-2.4T-A95B", "meta-llama/Llama-3.3-70B-Instruct-Turbo")
ARMS = tuple(f"{p}_{c}" for p in ("ordinary", "scope") for c in ("empty", "qwen_history", "llama_history"))
EFFECTS = [("S_pooled", "Pooled S", "primary"),
           *[(f"{metric}_{recipient}", f"{recipient} {metric}", recipient)
             for metric in ("S", "B", "E", "H_scope") for recipient in ("Qwen", "Llama")]]
EFFECTS.insert(3, ("B_pooled", "Pooled B", "primary"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes()) if path else None


def pp(value):
    return f"{100 * value:+.3f}"


def interval(values):
    return f"[{pp(values[0])}, {pp(values[1])}]"


def passed(value):
    return "PASS" if value else "DOES NOT PASS"


def amount(value):
    return f"${Decimal(str(value)):,.8f}" if value is not None else "unavailable"


def effect(a, name):
    return a["primary"][name] if name == "S_pooled" else a["descriptive_effects"][name]


def aggregate(a, manifest, completion, status, validation, paths, synthetic):
    require(a.get("schema_version") == "phase5_analysis_v1" and a.get("status") == "complete"
            and a.get("formal_gates_evaluated") is True, "A complete frozen Phase5 analysis is required")
    require(manifest.get("schema_version") == "phase5_evidence_scope_prepared_panel_v1"
            and manifest.get("models") == list(MODELS) and manifest.get("arms") == list(ARMS), "Wrong prepared panel")
    require(a["provenance"]["prepared_manifest_sha256"] == sha(paths["manifest"])
            and a["provenance"]["protocol_sha256"] == manifest["protocol_sha256"], "Analysis/manifest hash mismatch")
    require(a["coverage"]["completed"] == a["coverage"]["planned"] == 7872
            and a["coverage"]["administrative_missing"] == 0, "Incomplete analysis coverage")
    require(a.get("next_stage_paid_execution_authorized") is False and a.get("source_review_mode") == "ai_source_review"
            and a.get("independent_human_validation") is False, "Unexpected authority or review provenance")
    require([(r["judge"], r["arm"]) for r in a["arm_counts"]] == [(j, arm) for j in MODELS for arm in ARMS]
            and all(r["completed"] == r["expected"] == 656 for r in a["arm_counts"]), "Wrong arm inventory")
    if synthetic:
        require(validation and validation.get("kind") == "OFFLINE_SYNTHETIC_VALIDATION_NOT_EXPERIMENT_RESULTS"
                and validation.get("provider_calls") == 0 and validation.get("status") == "passed", "Fixture validation metadata required")
        require(validation["analysis_sha256"] == sha(paths["analysis"])
                and validation["results_sha256"] == a["provenance"]["results_sha256"]
                and validation["input_manifest_sha256"] == sha(paths["manifest"]), "Fixture provenance mismatch")
    else:
        require(completion and completion.get("status") == "requests_complete"
                and completion.get("scope") == "transport_collection_only" and completion.get("completed") == 7872,
                "Formal report requires transport completion record")
        require(completion.get("results_sha256") == a["provenance"]["results_sha256"]
                and completion.get("next_stage_paid_authorized") is False, "Completion/result hash or authority mismatch")
    if status:
        require(status.get("state") in ("requests_complete", "complete") and status.get("completed") == 7872
                and status.get("expected") == 7872 and not status.get("halt_reason") and not status.get("active"), "Status is not complete and idle")
    spend = (completion or status or {}).get("spend")
    require(spend and all(key in spend for key in ("actual_usd", "uncertain_usd", "inflight_usd", "exposure_usd")), "Complete ledger accounting required")
    ledger = {key: Decimal(str(spend[key])) for key in ("actual_usd", "uncertain_usd", "inflight_usd", "exposure_usd")}
    require(all(v.is_finite() and v >= 0 for v in ledger.values()) and ledger["inflight_usd"] == 0
            and abs(ledger["actual_usd"] + ledger["uncertain_usd"] - ledger["exposure_usd"]) <= Decimal("0.00000001"), "Ledger exposure is inconsistent")
    if completion and status:
        require(all(Decimal(str(status["spend"][k])) == v for k, v in ledger.items()), "Completion/status ledger mismatch")
    cap = status.get("cap_usd") if status else None
    if cap is not None:
        require(Decimal(str(cap)).is_finite() and ledger["exposure_usd"] <= Decimal(str(cap)), "Exposure exceeds stated cap")
    copied = ("parser_version", "formal_gates_evaluated", "definitions", "bootstrap", "primary", "primary_supported",
              "semantic_primary_supported", "candidate_readiness", "descriptive_effects", "arm_counts",
              "strict_missing_outcome_bounds", "invalid_and_missing_outcome_bounds", "uniform_invalid_as_correct_sensitivity",
              "paired_transitions_descriptive", "source_review_mode", "independent_human_validation", "limitations")
    summary = {"schema_version": "phase5_public_summary_v1", "kind": "OFFLINE_SYNTHETIC_FIXTURE" if synthetic else "EXPERIMENT_RESULTS",
               "status": "complete", **{k: a[k] for k in copied}, "next_stage_paid_execution_authorized": False}
    summary["coverage"] = {k: v for k, v in a["coverage"].items() if k != "missing_cell_ids"}
    common = a["common_valid_sensitivity"]
    summary["common_valid_sensitivity"] = {k: v for k, v in common.items() if k != "support"}
    summary["common_valid_sensitivity"]["support"] = {
        "retained_units": common["support"]["retained_units"], "dropped_units": common["support"]["dropped_units"],
        "empty_question_debater_strata_count": len(common["support"]["empty_question_debater_strata"])}
    summary["accounting"] = {**{k: str(v) for k, v in ledger.items()}, "approved_cap_usd": str(cap) if cap is not None else None,
                              "scope": "Runner ledger including failed-attempt uncertainty and preflight; fixture amounts are simulated.",
                              "completed_response_cost_usd": a["completed_response_cost_usd"],
                              "completed_response_uncertain_usd": a["completed_response_uncertain_usd"],
                              "completed_responses_missing_usage": a["completed_responses_missing_usage"]}
    summary["collection"] = {"completed_at": (completion or {}).get("completed_at", (status or {}).get("last_completion_at")),
                              "scope": "transport_collection_only", "completed": 7872}
    summary["provenance"] = {k: v for k, v in a["provenance"].items() if k.endswith("sha256") or k == "input_hashes"}
    summary["provenance"]["reviewed_source_hashes"] = {k: v for k, v in a["provenance"].get("reviewed_sources", {}).items() if k.endswith("sha256")}
    summary["provenance"]["report_source_sha256"] = sha(__file__)
    summary["provenance"]["report_input_hashes"] = {k: sha(p) for k, p in paths.items() if p}
    return summary


def markdown(s):
    primary, accounting = s["primary"]["S_pooled"], s["accounting"]
    synthetic = s["kind"] == "OFFLINE_SYNTHETIC_FIXTURE"
    title = "OFFLINE SYNTHETIC FIXTURE: Phase 5 report preview" if synthetic else "Phase 5 evidence-scope result"
    intro = "**Synthetic preview only. These are simulated outcomes and costs, not experiment findings.**\n\n" if synthetic else ""
    if not s["primary_supported"]:
        decision = "**The fixed scope instruction did not demonstrate the planned history-specific improvement.**"
    elif not s["candidate_readiness"]["passed"]:
        decision = "**The history-specific improvement is supported, but the candidate did not meet the readiness screens.**"
    else:
        decision = "**The instruction met the frozen primary and candidate-readiness screens on this panel.**"
    lookup = {(r["judge"], r["arm"]): r for r in s["arm_counts"]}
    overview = ["With the two reviewed history sources weighted equally, the observed error rates were:", "",
                "| Recipient | Ordinary instruction | Scope instruction | Change in error |",
                "|---|---:|---:|---:|"]
    for judge, recipient in zip(MODELS, ("Qwen", "Llama")):
        rates = [sum(lookup[(judge, f"{prompt}_{context}")]["strict_errors"] for context in ("qwen_history", "llama_history")) / 1312
                 for prompt in ("ordinary", "scope")]
        overview.append(f"| {recipient} | {100*rates[0]:.2f}% | {100*rates[1]:.2f}% | {pp(rates[1]-rates[0])} pp |")
    overview += ["", "Positive change means more errors. These recipient comparisons are descriptive. The B intervals below use the opposite sign: positive B means fewer errors."]
    if effect(s, "B_Qwen")["estimate"] < 0 < effect(s, "B_Llama")["estimate"]:
        overview += ["", "The observed changes go in opposite directions: the appendix worsened Qwen's history judgments while improving Llama's. "
                     "Llama also improved without history, leaving a smaller history-specific estimate. This candidate therefore does not provide a shared repair. "
                     "It weakens this specific instruction-based remedy; it does not rule out every evidence-interpretation mechanism or other protocol design."]
    lines = [f"# {title}\n", intro + decision + " This does not establish a protocol that works across models.\n",
             "\n".join(overview) + "\n",
             f"Pooled S is **{pp(primary['estimate'])} percentage points**, with 95% interval **{interval(primary['ci95'])}**. "
             f"Primary support: **{passed(s['primary_supported'])}**. Semantic support: **{passed(s['semantic_primary_supported'])}**. "
             f"Candidate readiness: **{passed(s['candidate_readiness']['passed'])}**. No subsequent paid work is authorized.\n",
             f"All **7,872/7,872** verdicts completed: **{s['coverage']['completed_invalid']} invalid**, zero administratively missing. "
             f"Ledger usage is **{amount(accounting['actual_usd'])}**, retained uncertainty **{amount(accounting['uncertain_usd'])}**, "
             f"and total exposure **{amount(accounting['exposure_usd'])}** against a cap of **{amount(accounting['approved_cap_usd'])}**. "
             f"Completed-response cost alone is {amount(accounting['completed_response_cost_usd'])}; it excludes other ledger entries.\n",
             "![Phase 5 effects with 95% intervals and all twelve arm error rates](phase5-effects.png)\n",
             "Positive **S = B - E** means the instruction reduces the history-minus-empty error gap. "
             "Positive **B** means direct benefit on histories; positive **E** means benefit on empty context. "
             "Positive **H_scope** means histories are worse than **scoped empty**. S can improve when scoped empty worsens, "
             "so S alone does not establish direct benefit or readiness.\n",
             "| Effect | Estimate (pp) | Central 95% interval (pp) | One-sided 95% lower | One-sided 95% upper |",
             "|---|---:|---:|---:|---:|"]
    for name, label, _ in EFFECTS:
        v = effect(s, name)
        lines.append(f"| {label} | {pp(v['estimate'])} | {interval(v['ci95'])} | {pp(v['lower_one_sided95'])} | {pp(v['upper_one_sided95'])} |")
    lines += ["", f"The sole confirmatory test has two-sided p = **{primary['p_two_sided']:.6f}**; no Holm correction applies. "
              f"The exact pooled contrast is **{primary['integer_numerator']}/{primary['denominator']:,}**, "
              f"requiring numerator **at least {primary['practical_integer_threshold']}**. Statistical screen: {passed(primary['statistical_pass'])}; "
              f"positive practical screen: {passed(primary['positive_practical_pass'])}. "
              f"The supplemental 97.5% interval is {interval(primary['ci97_5'])} pp. "
              "Intervals use 10,000 paired question-cluster bootstrap draws, stratified by world, with equal question and debater weights.\n",
              "Readiness additionally requires the pooled direct-history benefit's central 95% lower bound above zero, nonnegative benefit for each recipient, "
              "one-sided 95% lower bounds above -1 pp for each recipient's B and E, and one-sided upper bounds below +1 pp for H_scope. "
              "The frozen common-valid and coefficient-specific outcome-bound screens must also pass. These are conjunctive screens, not additional discoveries.\n"]
    lines.append("Exact outcomes for every readiness screen are in [summary.json](summary.json); a positive result for one recipient cannot override failure of the combined candidate decision.\n")
    common = s["common_valid_sensitivity"]
    lines.append(f"Common-valid support: **{common['support']['retained_units']}/656 units**; "
                 f"undefined question/debater strata: **{common['support']['empty_question_debater_strata_count']}**. "
                 "Outcome bounds describe possible invalid/missing labels; they are not confidence intervals.\n")
    lines += ["| Recipient | Scoped history minus ordinary empty (pp) | 95% interval (pp) |", "|---|---:|---:|"]
    for recipient in ("Qwen", "Llama"):
        v = s["descriptive_effects"][f"history_scope_minus_empty_ordinary_{recipient}"]
        lines.append(f"| {recipient} | {pp(v['estimate'])} | {interval(v['ci95'])} |")
    lines += ["", "This separate comparator prevents confusing H_scope's scoped-empty baseline with ordinary empty. "
              "The permitted 1 pp margins do not guarantee at most 1 pp total harm relative to ordinary empty.\n",
              "| Recipient | Context | Ordinary errors / 656 | Scope errors / 656 |", "|---|---|---:|---:|"]
    for judge, recipient in zip(MODELS, ("Qwen", "Llama")):
        for context, label in (("empty", "Empty"), ("qwen_history", "Qwen history"), ("llama_history", "Llama history")):
            x, y = (lookup[(judge, f"{p}_{context}")] for p in ("ordinary", "scope"))
            lines.append(f"| {recipient} | {label} | {x['strict_errors']} ({100*x['error_rate']:.2f}%) | {y['strict_errors']} ({100*y['error_rate']:.2f}%) |")
    lines += ["", "Strict errors include completed invalid verdicts. All twelve cell counts and intervals, common-valid results, "
              "outcome bounds and exact readiness checks are retained in [summary.json](summary.json).\n",
              "The instruction package was designed after earlier Phase 4 outcomes and tested prospectively on the same studied question frame. "
              "It combines wording, length and attention cues; it does not identify a particular sentence or internal mechanism. "
              "The saved labels use amended AI source review, not independent human validation. "
              "The fixed 82 questions, three worlds, histories and two endpoints do not establish untouched-task or live-query-policy generalization. "
              "A failed screen may remain inconclusive at 82 clusters; it does not establish equivalence.\n",
              f"Analysis SHA-256: `{s['provenance']['report_input_hashes']['analysis']}`. "
              "The report contains aggregates only. Raw prompts, source content, answers and responses remain in the private archive.\n"]
    return "\n".join(lines)


def figure(s, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none", "svg.hashsalt": "phase5-report-v1"})
    fig, (left, right) = plt.subplots(1, 2, figsize=(13.6, 7.8), gridspec_kw={"width_ratios": [1.17, 1]})
    colors = {"primary": "#213a55", "Qwen": "#177c94", "Llama": "#b36821"}
    for i, (name, label, recipient) in enumerate(EFFECTS):
        v = effect(s, name)
        y = len(EFFECTS) - 1 - i
        lo, hi, value = *[100*x for x in v["ci95"]], 100*v["estimate"]
        left.hlines(y, lo, hi, color=colors[recipient], linewidth=2)
        left.plot(value, y, "D" if recipient == "primary" else "o", color=colors[recipient], markersize=7)
    left.axvline(0, color="#8793a0", lw=1)
    left.set_yticks(range(len(EFFECTS)), [label.replace("H_scope", "H scoped") for _, label, _ in EFFECTS][::-1])
    left.set_title("Primary and recipient effects", loc="left", weight="bold", pad=17)
    left.set_xlabel("Effect (percentage points), central 95% interval")
    left.set_ylim(-.7, len(EFFECTS) - .3)
    left.grid(axis="x", color="#e9edf1")
    left.text(0, -.15, "Positive S: gap reduction; positive B/E: direct benefit\nPositive H scoped: history harm vs scoped empty", transform=left.transAxes, fontsize=8.6, color="#4e5864")
    lookup = {(r["judge"], r["arm"]): r for r in s["arm_counts"]}
    labels = []
    for j, (judge, recipient) in enumerate(zip(MODELS, ("Qwen", "Llama"))):
        for k, (context, label) in enumerate((("empty", "Empty"), ("qwen_history", "Qwen history"), ("llama_history", "Llama history"))):
            y = 5 - (j*3 + k)
            labels.append(f"{recipient}: {label}")
            for prompt, offset, color in (("ordinary", .12, "#213a55"), ("scope", -.12, "#b36821")):
                r = lookup[(judge, f"{prompt}_{context}")]
                right.hlines(y+offset, 100*r["ci95"][0], 100*r["ci95"][1], color=color, linewidth=1.8)
                right.plot(100*r["error_rate"], y+offset, "o", color=color, markersize=6)
    right.set_yticks(range(6), labels[::-1])
    right.set_title("All twelve arm error rates", loc="left", weight="bold", pad=17)
    right.set_xlabel("Strict error rate (%), central 95% interval")
    right.set_ylim(-.6, 5.6)
    right.set_xlim(left=0)
    right.grid(axis="x", color="#e9edf1")
    right.legend(handles=[Line2D([0], [0], color=c, marker="o", label=p) for p,c in (("Ordinary", "#213a55"), ("Scope", "#b36821"))],
                 loc="upper center", bbox_to_anchor=(.5, -.13), ncol=2, frameon=False)
    for ax in (left, right):
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0)
        ax.spines["bottom"].set_color("#b9c2cc")
    fixture = s["kind"] == "OFFLINE_SYNTHETIC_FIXTURE"
    title = "OFFLINE SYNTHETIC FIXTURE: Phase 5 report preview" if fixture else "Phase 5: evidence-scope instruction"
    fig.suptitle(title, x=.04, y=.97, ha="left", fontsize=17, weight="bold", color="#213a55")
    fig.text(.04, .91, f"Primary: {passed(s['primary_supported'])}     Semantic: {passed(s['semantic_primary_supported'])}     "
             f"Readiness: {passed(s['candidate_readiness']['passed'])}", fontsize=10, weight="bold")
    fig.text(.04, .075, "95% intervals shown here. Readiness also uses one-sided limits and invalid-outcome bounds; see the report.", fontsize=9, color="#4e5864")
    fig.text(.04, .045, "Simulated outcomes only; no experiment findings or costs." if fixture else
             "Fixed 82-question frame, two recipients, saved AI-reviewed histories. No subsequent paid work is authorized.", fontsize=9, color="#4e5864")
    fig.subplots_adjust(left=.135, right=.975, bottom=.25, top=.83, wspace=.72)
    fig.savefig(out / "phase5-effects.png", dpi=180, facecolor="white", metadata={"Software": "phase5_report"})
    svg = out / "phase5-effects.svg"
    fig.savefig(svg, facecolor="white", metadata={"Date": None, "Creator": "phase5_report"})
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("analysis", "manifest", "completion", "status", "validation"):
        parser.add_argument("--" + name, type=Path, required=name in ("analysis", "manifest"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args()
    paths = {name: getattr(args, name).resolve() if getattr(args, name) else None for name in ("analysis", "manifest", "completion", "status", "validation")}
    out = args.out.resolve()
    if args.synthetic:
        require(out.is_relative_to(ROOT.parent / ".tmp"), "Synthetic previews must stay in private workspace .tmp")
    outputs = [out / name for name in ("results.md", "summary.json", "phase5-effects.png", "phase5-effects.svg")]
    require(not any(p in outputs for p in paths.values() if p), "Output would overwrite an input")
    initial = {name: sha(p) for name, p in paths.items() if p}
    summary = aggregate(*(read(paths[name]) for name in ("analysis", "manifest", "completion", "status", "validation")), paths, args.synthetic)
    require(initial == {name: sha(p) for name, p in paths.items() if p}, "Report input changed during rendering")
    out.mkdir(parents=True, exist_ok=True)
    figure(summary, out)
    (out / "results.md").write_text(markdown(summary), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"kind": summary["kind"], "out": str(out), "outputs": {p.name: sha(p) for p in outputs}}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(f"Phase5 report failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
