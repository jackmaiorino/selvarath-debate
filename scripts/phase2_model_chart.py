"""Plot descriptive Phase 2 judge effects using the saved post-hoc analysis method."""
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "reports/phase2-model-charts-2026-09-13"
SAVED = ROOT / "analysis_out/phase2_mirror_reanalysis.json"
LABELS = {
    "Qwen/Qwen2.5-7B-Instruct-Turbo": "Qwen 2.5 7B",
    "google/gemma-4-31B-it": "Gemma 4 31B",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": "Llama 3.3 70B",
    "openai/gpt-oss-120b": "GPT-OSS 120B",
}


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def compute():
    from scripts import phase2_mirror_reanalysis as m
    saved = json.loads(SAVED.read_bytes())
    source = Path(r"E:\selvarath-archive\main-2026-08-06\main_results.jsonl")
    source_sha = sha(source)
    assert source_sha == saved["integrity"]["main_results_input_sha256"]
    assert sha(Path(m.__file__)) == saved["integrity"]["engine_script_sha256"]
    plan = m.load_plan_meta(ROOT, ROOT/"rejudge/phase2_main_manifest_2026-08-06c.json")
    records = m.load_records_with_side(source, plan)
    debate = [r for r in records if r["kind"] == "debate_judgment"]
    worlds = m.world_of_questions(debate)
    draws = m.stratified_question_draws(debate, saved["B"], saved["seed"])
    draw_sha = hashlib.sha256(json.dumps(draws, sort_keys=True).encode()).hexdigest()
    assert draw_sha == saved["integrity"]["draw_matrix_sha256"]
    rates = m.error_by_unit(debate, m.PRIMARY_CONDITIONS, invalid_wrong=True, common_support=False)
    pooled = saved["H_P_R"]["strict"]["standardized"]["fifty_fifty"]["H"]["estimate"]
    loo = saved["H_P_R"]["strict"]["diagnostics"]["leave_one_judge_out_fifty_fifty_H"]
    rows = []
    for model, label in LABELS.items():
        pqs = m.question_side_means(rates, m.PRIMARY_CONDITIONS, judge_filter=frozenset({model}))
        point = m.standardized_family(pqs, m.PRIMARY_CONDITIONS, worlds)["fifty_fifty"]["H"]
        # Equal judge weighting provides an independent check from saved outputs.
        from_saved = 4*pooled - 3*loo[model]["estimate"]
        assert abs(point-from_saved) < 1e-12, (model, point, from_saved)
        reps = m.bootstrap_standardized(pqs, worlds, draws, m.PRIMARY_CONDITIONS, m.PRIMARY_IDS)
        ci = m.percentile_ci(reps[("fifty_fifty", "H")])
        rows.append({"model":model, "label":label, "estimate":point, "ci95":ci})
        print(label, point, ci, flush=True)
    assert abs(sum(r["estimate"] for r in rows)/4-pooled) < 1e-12
    data = {
        "stage":"Phase 2", "contrast":"sequential_b2 minus b0 error rate against original key",
        "method":"Descriptive per-judge 50/50 side-standardized, equal-world post-hoc reanalysis; strict invalid-as-wrong scoring",
        "rows":rows, "bootstrap_draws":saved["B"], "seed":saved["seed"],
        "ci_scope":saved["integrity"]["estimand_scope_note"],
        "source_results_sha256":source_sha, "saved_analysis_sha256":sha(SAVED),
        "engine_sha256":sha(Path(m.__file__)), "draw_matrix_sha256":draw_sha,
        "checks":"All per-model points match 4*saved pooled mean - 3*saved leave-one-model-out mean; average matches saved pooled point.",
        "limitations":["Earlier experiment, not Phase 3-5 or the proposed clean benchmark.",
                       "Original mirroring was defective. Reweighting does not recover missing mirrored outcomes or restore eligibility claims.",
                       "Descriptive 95% intervals, no per-model confirmatory significance claims.",
                       "Question-cluster uncertainty within three fixed worlds; no model-population or world-population inference.",
                       "Original answer keys retained, including the source-ambiguity limitations found by the later audit."],
        "new_provider_calls":0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/"effects.json").write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8",newline="\n")


def render():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    data = json.loads((OUT/"effects.json").read_bytes())
    fig, ax = plt.subplots(figsize=(10,5.5),dpi=180)
    fig.patch.set_facecolor("#FAFAF8")
    ax.set_facecolor("#FAFAF8")
    ax.axvline(0,color="#6A7280",lw=1)
    for i,r in enumerate(data["rows"]):
        x=r["estimate"]*100; lo,hi=[v*100 for v in r["ci95"]]
        ax.errorbar(x,i,xerr=[[x-lo],[hi-x]],fmt="o",color="#355D80",capsize=5,markersize=8,lw=2)
        ax.text(19.3,i,f"{x:+.2f} pp  [{lo:+.2f}, {hi:+.2f}]",va="center",fontsize=10)
    ax.set_yticks(range(4),[r["label"] for r in data["rows"]],fontsize=12)
    ax.invert_yaxis()
    ax.set_ylim(3.6,-.6)
    ax.set_xlim(-10,30)
    ax.set_xticks([-10,-5,0,5,10,15])
    ax.set_xlabel("Change in error, percentage points   |   positive = worse",fontsize=10)
    for s in ax.spines.values(): s.set_visible(False)
    ax.tick_params(axis="both",length=0)
    fig.suptitle("Phase 2: all four judge models",x=.035,ha="left",fontsize=18,fontweight="bold")
    fig.text(.035,.875,"Two verification calls versus none | descriptive post-hoc 50/50 side-standardized effects",fontsize=10)
    fig.text(.035,.11,"Bars: 95% question-cluster bootstrap intervals under observed assignments and reweighting.",fontsize=9,color="#525A66")
    fig.text(.035,.067,"Original mirroring was defective; these intervals do not recover missing mirrored outcomes.",fontsize=9,color="#525A66")
    fig.text(.035,.025,"Earlier models and protocol. Original answer-key ambiguity remains. No new model calls.",fontsize=9,color="#525A66")
    fig.subplots_adjust(left=.20,right=.97,top=.79,bottom=.27)
    fig.savefig(OUT/"four-model-effects.png",facecolor=fig.get_facecolor())
    plt.close(fig)
    lines=["# Phase 2 model charts", "", "These are the four judge models in the earlier Phase 2 experiment. Phases 3-5 evaluated Qwen 3.8 and Llama 3.3 as recipient judges. GPT-OSS also served as an oracle-label adjudicator in Phase 4B, a different role.", "", "![All four Phase 2 judges](four-model-effects.png)", "", "Effect = error with two sequential verification calls minus error with no verification. Positive values mean worse performance against the original answer key.", "", "| Judge | Effect (pp) | Descriptive 95% interval (pp) |", "|---|---:|---:|"]
    for r in data["rows"]:
        lines.append(f"| {r['label']} | {100*r['estimate']:+.2f} | [{100*r['ci95'][0]:+.2f}, {100*r['ci95'][1]:+.2f}] |")
    lines += ["", "The points use the saved post-hoc 50/50 side-standardization method. Per-judge intervals were computed using the same 10,000 world-stratified question-bootstrap draws and frozen engine. Every point is independently checked against the saved pooled and leave-one-judge-out estimates. Original results and scoring are unchanged.", "", "The original Phase 2 mirroring was defective. Reweighting does not recover the missing opposite-side outcomes or restore the withdrawn eligibility claims. These are descriptive intervals conditional on observed assignments and the reweighting assumption, not a new confirmatory comparison. The later answer-key audit also applies to this reused question set.", "", "Sources: [Phase 2 results and erratum](../2026-08-11-phase2-main-results.md), [source audit](../question-evidence-audit-2026-09-12/audit.md), [chart data and provenance](effects.json).", "", "Newer charts: [Phase 3](../phase3-results-2026-09-11/phase3-results.png), [Phase 4B](../phase4b-recipient-results-2026-09-12/recipient-effects.png), [Phase 5](../phase5-results-2026-09-12/phase5-effects.png).", ""]
    (OUT/"README.md").write_text("\n".join(lines),encoding="utf-8",newline="\n")


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--render-only",action="store_true");args=p.parse_args()
    if args.render_only: render()
    else: compute()
