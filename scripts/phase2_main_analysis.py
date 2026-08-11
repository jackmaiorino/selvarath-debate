"""Pre-registered analysis of the phase-2 main run.

Implements exactly the frozen specification in rejudge/phase2_protocol.json
decisions.primary_tests and decisions.secondary_analyses, under the adopted
missing-data policy (rejudge/phase2_missing_data_policy_proposal_2026-08-04.json):

  Primary family (Holm, two-sided):
    H = error(sequential_b2) - error(b0)                total limited-verification harm
    P = error(sequential_b2) - error(batch_same_qa_b2)  packaging harm
    R = error(batch_same_qa_b2) - error(b0)             residual content harm
  with the identity H = P + R holding exactly in the point estimate and inside
  every bootstrap replicate, because all three are computed from the same
  per-replicate condition means on the same support.

  Weighting: average the K = 2 mirrored sides and 3 transcripts within question
  per (judge, debater); equal question, judge and debater weights above that.
  Inference: common world-stratified question-bootstrap draws, shared across the
  family so the identity and the Holm ordering see the same resamples.
  Invalid policy: strict INVALID counts wrong in the primary; a valid-only
  sensitivity is computed on common support.

Run AFTER convergence verification only. Reads the archive read-only.
"""
import argparse
import hashlib
import json
import platform
import random
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PRIMARY_CONDITIONS = ("b0", "sequential_b2", "batch_same_qa_b2")
PRIMARY_IDS = ("H", "P", "R")
B_DEFAULT = 10000
SEED_DEFAULT = 20260811  # fixed and recorded; the date the run converged


def load_plan_meta(project_root: Path, manifest_path: Path) -> dict:
    from rejudge.phase2_canary_live import plan_for
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {c["cell_key"]: c for c in plan_for(manifest, project_root=str(project_root))}


def load_records(results_path: Path, plan_meta: dict) -> list[dict]:
    """One record per judgment row, joined with the plan for debater attribution.

    correct is True/False/None; None is a strict-INVALID verdict, whose primary
    treatment (counts wrong) and sensitivity treatment (dropped on common
    support) are applied at aggregation time, never here.
    """
    records = []
    for line in results_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        kind = row["cell_key"].split(":")[1]
        if kind not in ("debate_judgment", "no_debate_judgment", "cap_protection_judgment"):
            continue
        meta = plan_meta.get(row["cell_key"])
        if meta is None:
            raise SystemExit(f"result row {row['cell_key']} is not in the plan; refusing")
        res = row["result"]
        records.append({
            "kind": kind,
            "question_id": res["question_id"],
            "world": res["world"],
            "condition": res["condition"],
            "judge": res["judge_model"],
            "debater": meta.get("debater_model"),  # None for no-debate kinds
            "slot": (res.get("replicate"), res.get("transcript_index")),
            "correct": res["verdict_correct_strict"],
        })
    return records


def error_by_cell(records: list[dict], conditions: tuple, *, invalid_wrong: bool,
                  common_support: bool) -> dict:
    """Per (question, judge, debater) per condition: mean wrong-rate over slots.

    invalid_wrong=True is the primary rule (INVALID counts wrong). With
    invalid_wrong=False the valid-only sensitivity applies: when common_support
    is True, a slot invalid in ANY of the conditions is dropped from ALL of
    them, so every condition mean inside one (q, j, d) cell is computed on an
    identical slot set and the H = P + R identity survives the sensitivity.
    """
    by_cell = defaultdict(dict)   # (q, j, d) -> condition -> {slot: correct}
    for r in records:
        if r["condition"] not in conditions:
            continue
        by_cell[(r["question_id"], r["judge"], r["debater"])].setdefault(
            r["condition"], {})[r["slot"]] = r["correct"]

    out = {}
    for cell, per_cond in by_cell.items():
        if set(per_cond) != set(conditions):
            raise SystemExit(f"cell {cell} lacks conditions {set(conditions) - set(per_cond)}")
        if invalid_wrong:
            support = {c: sorted(per_cond[c]) for c in conditions}
        elif common_support:
            keep = [s for s in per_cond[conditions[0]]
                    if all(per_cond[c].get(s) is not None for c in conditions)]
            support = {c: keep for c in conditions}
        else:
            support = {c: [s for s, v in per_cond[c].items() if v is not None]
                       for c in conditions}
        rates = {}
        for c in conditions:
            slots = support[c]
            if not slots:
                rates[c] = None
            else:
                wrong = sum(1 for s in slots if per_cond[c][s] is not True)
                rates[c] = wrong / len(slots)
        out[cell] = rates
    return out


def condition_means(cell_rates: dict, conditions: tuple,
                    question_multiplicity: dict | None = None) -> dict:
    """Equal question, judge and debater weights: the grand mean over (q, j, d)
    cells, with bootstrap question multiplicity applied as a question weight."""
    sums = {c: 0.0 for c in conditions}
    wsum = {c: 0.0 for c in conditions}
    for (q, _j, _d), rates in cell_rates.items():
        w = 1.0 if question_multiplicity is None else float(question_multiplicity.get(q, 0))
        if w == 0.0:
            continue
        for c in conditions:
            if rates[c] is None:
                continue
            sums[c] += w * rates[c]
            wsum[c] += w
    return {c: (sums[c] / wsum[c] if wsum[c] else None) for c in conditions}


def estimands(means: dict) -> dict:
    e = means
    if any(e[c] is None for c in PRIMARY_CONDITIONS):
        return {i: None for i in PRIMARY_IDS}
    return {
        "H": e["sequential_b2"] - e["b0"],
        "P": e["sequential_b2"] - e["batch_same_qa_b2"],
        "R": e["batch_same_qa_b2"] - e["b0"],
    }


def stratified_question_draws(records: list[dict], b: int, seed: int) -> list[dict]:
    """B multisets of question ids, resampled with replacement within each world,
    preserving each world's question count. One common sequence for the family."""
    world_questions = defaultdict(set)
    for r in records:
        world_questions[r["world"]].add(r["question_id"])
    strata = {w: sorted(qs) for w, qs in sorted(world_questions.items())}
    rng = random.Random(seed)
    draws = []
    for _ in range(b):
        mult = defaultdict(int)
        for _w, qs in strata.items():
            for _ in qs:
                mult[qs[rng.randrange(len(qs))]] += 1
        draws.append(dict(mult))
    return draws


def bootstrap_family(cell_rates: dict, conditions: tuple, draws: list[dict],
                     compute) -> dict:
    reps = defaultdict(list)
    for mult in draws:
        means = condition_means(cell_rates, conditions, question_multiplicity=mult)
        for key, val in compute(means).items():
            reps[key].append(val)
    return dict(reps)


def percentile_ci(samples: list, lo=2.5, hi=97.5) -> tuple:
    xs = sorted(s for s in samples if s is not None)
    if not xs:
        return (None, None)
    def q(p):
        i = (len(xs) - 1) * p / 100.0
        f, c = int(i), min(int(i) + 1, len(xs) - 1)
        return xs[f] + (xs[c] - xs[f]) * (i - f)
    return (q(lo), q(hi))


def bootstrap_p_two_sided(samples: list) -> float:
    """Uncentered percentile-bootstrap tail p-value (naming per the pre-unblinding
    consult): p = min(1, 2*min[(1+#{T<=0})/(B+1), (1+#{T>=0})/(B+1)]), with exact
    zeros counted inclusively in both tails. Pinned; never to be replaced by a
    centered or studentized variant after unblinding."""
    xs = [s for s in samples if s is not None]
    n = len(xs)
    le = sum(1 for x in xs if x <= 0.0)
    ge = sum(1 for x in xs if x >= 0.0)
    p = 2.0 * min((le + 1) / (n + 1), (ge + 1) / (n + 1))
    return min(p, 1.0)


def holm(pvals: dict) -> dict:
    # Ties broken by estimand id so the step-down order is deterministic.
    items = sorted(pvals.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(items)
    adjusted, running = {}, 0.0
    for rank, (key, p) in enumerate(items):
        running = max(running, min(1.0, (m - rank) * p))
        adjusted[key] = running
    return adjusted


def _cell_error(rows: list[dict], valid_only: bool = False) -> float | None:
    """Mean wrong-rate over a row list. Primary rule: INVALID counts wrong.
    valid_only drops INVALID rows per arm (the pinned secondary sensitivity)."""
    if valid_only:
        rows = [r for r in rows if r["correct"] is not None]
    if not rows:
        return None
    return sum(1 for r in rows if r["correct"] is not True) / len(rows)


def per_question_D(records: list[dict], debate_cond: str, nodebate_cond: str,
                   valid_only: bool = False) -> dict:
    """Per-question D value: mean over judges of (debate error, averaged over the
    two debaters after averaging K2 x 3 transcripts, minus the no-debate K3 error).
    Equal judge weights inside each question; the question weights are applied by
    the caller, so the bootstrap only reweights this precomputed dict."""
    seq = defaultdict(list)
    nod = defaultdict(list)
    for r in records:
        if r["kind"] == "debate_judgment" and r["condition"] == debate_cond:
            seq[(r["question_id"], r["judge"], r["debater"])].append(r)
        elif r["kind"] == "no_debate_judgment" and r["condition"] == nodebate_cond:
            nod[(r["question_id"], r["judge"])].append(r)
    per_qj_deb = defaultdict(list)
    for (q, j, _d), rows in seq.items():
        e = _cell_error(rows, valid_only=valid_only)
        if e is not None:
            per_qj_deb[(q, j)].append(e)
    per_q = defaultdict(list)
    for (q, j), deb_errs in sorted(per_qj_deb.items()):
        if (q, j) not in nod:
            raise SystemExit(f"no {nodebate_cond} comparator for {(q, j)}")
        ne = _cell_error(nod[(q, j)], valid_only=valid_only)
        if ne is None:
            continue
        per_q[q].append((sum(deb_errs) / len(deb_errs)) - ne)
    return {q: sum(v) / len(v) for q, v in per_q.items()}


def per_question_C(records: list[dict], cap_judge: str,
                   debater_a: str, debater_b: str, valid_only: bool = False) -> dict:
    """Per-question cap-protection interaction at the cap judge only:
    (uncapped - capped150) for debater A minus the same for debater B."""
    unc = defaultdict(list)
    cap = defaultdict(list)
    for r in records:
        if r["judge"] != cap_judge:
            continue
        if r["kind"] == "debate_judgment" and r["condition"] == "b0":
            unc[(r["question_id"], r["debater"])].append(r)
        elif r["kind"] == "cap_protection_judgment" and r["condition"] == "capped150_b0":
            cap[(r["question_id"], r["debater"])].append(r)
    out = {}
    for q in sorted({q for q, _d in unc}):
        parts = {}
        for deb in (debater_a, debater_b):
            u = _cell_error(unc.get((q, deb), []), valid_only=valid_only)
            c = _cell_error(cap.get((q, deb), []), valid_only=valid_only)
            if u is None or c is None:
                if valid_only:
                    parts = None
                    break
                raise SystemExit(f"cap-protection support missing for {(q, deb)}")
            parts[deb] = u - c
        if parts is not None:
            out[q] = parts[debater_a] - parts[debater_b]
    return out


def weighted_question_mean(per_q: dict, question_multiplicity: dict | None = None) -> float | None:
    total = wsum = 0.0
    for q, v in per_q.items():
        w = 1.0 if question_multiplicity is None else float(question_multiplicity.get(q, 0))
        total += w * v
        wsum += w
    return total / wsum if wsum else None


SELFTEST_RATES = {"b0": 0.30, "sequential_b2": 0.40, "batch_same_qa_b2": 0.34,
                  "placebo_b2": 0.32, "clean_b2": 0.25, "capped150_b0": 0.28}
SELFTEST_INVALID = 0.006


def synthesize_outcomes(records: list[dict], seed: int) -> list[dict]:
    """The blinded full-size dry run: keep the real plan structure, replace every
    outcome with a draw at the known per-condition rates. Expected estimands:
    H = 0.10, P = 0.06, R = 0.04, D_clean = 0.15, C = 0. Recovery within
    bootstrap noise, plus every structural assert, is required before the real
    data run; nothing in this path reads a real verdict."""
    rng = random.Random(seed ^ 0x5E1F7E57)
    out = []
    for r in records:
        rate = SELFTEST_RATES[r["condition"]]
        correct = None if rng.random() < SELFTEST_INVALID else (rng.random() >= rate)
        out.append(dict(r, correct=correct))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="phase2_main_analysis")
    ap.add_argument("--archive", default="E:/selvarath-archive/main-2026-08-06")
    ap.add_argument("--manifest", default="rejudge/phase2_main_manifest_2026-08-06c.json")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--out", default="analysis_out/phase2_main_results.json")
    ap.add_argument("-B", type=int, default=B_DEFAULT)
    ap.add_argument("--seed", type=int, default=SEED_DEFAULT)
    ap.add_argument("--selftest", action="store_true",
                    help="blinded full-size dry run: real plan structure, synthetic "
                         "outcomes at known per-condition rates, recovery asserted")
    args = ap.parse_args(argv)

    root = Path(args.project_root)
    plan_meta = load_plan_meta(root, root / args.manifest)
    results_path = Path(args.archive) / "main_results.jsonl"
    records = load_records(results_path, plan_meta)
    if getattr(args, "selftest", False):
        records = synthesize_outcomes(records, seed=args.seed)
    debate = [r for r in records if r["kind"] == "debate_judgment"]

    primary_rates = error_by_cell(debate, PRIMARY_CONDITIONS,
                                  invalid_wrong=True, common_support=False)
    valid_rates = error_by_cell(debate, PRIMARY_CONDITIONS,
                                invalid_wrong=False, common_support=True)

    draws = stratified_question_draws(debate, args.B, args.seed)

    point_means = condition_means(primary_rates, PRIMARY_CONDITIONS)
    point = estimands(point_means)
    reps = bootstrap_family(primary_rates, PRIMARY_CONDITIONS, draws, estimands)
    sens_point = estimands(condition_means(valid_rates, PRIMARY_CONDITIONS))
    sens_reps = bootstrap_family(valid_rates, PRIMARY_CONDITIONS, draws, estimands)

    pvals = {i: bootstrap_p_two_sided(reps[i]) for i in PRIMARY_IDS}
    adj = holm(pvals)

    identity_gap = max(abs((reps["P"][k] + reps["R"][k]) - reps["H"][k])
                       for k in range(len(reps["H"]))
                       if None not in (reps["P"][k], reps["R"][k], reps["H"][k]))
    assert identity_gap < 1e-12, f"H = P + R violated across replicates: {identity_gap}"
    point_gap = abs((point["P"] + point["R"]) - point["H"])
    assert point_gap < 1e-12, f"H = P + R violated in the point estimate: {point_gap}"

    world_of = {r["question_id"]: r["world"] for r in debate}
    expected_counts = defaultdict(int)
    for q, w in world_of.items():
        expected_counts[w] += 1
    for mult in draws:
        got = defaultdict(int)
        for q, n in mult.items():
            got[world_of[q]] += n
        assert dict(got) == dict(expected_counts), "draw violated world stratification"

    # Secondary family: C and D_clean, its own two-test Holm family, on the same
    # common draw sequence (the spec requires sharing within the family; reusing
    # the primary's draws across families is recorded as an analysis pin).
    CAP_JUDGE = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    DEBATER_A = "Qwen/Qwen3.7-Plus"
    DEBATER_B = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    d_clean_q = per_question_D(records, "sequential_b2", "clean_b2")
    c_q = per_question_C(records, CAP_JUDGE, DEBATER_A, DEBATER_B)
    sec_point = {"C": weighted_question_mean(c_q),
                 "D_clean": weighted_question_mean(d_clean_q)}
    sec_reps = {"C": [weighted_question_mean(c_q, m) for m in draws],
                "D_clean": [weighted_question_mean(d_clean_q, m) for m in draws]}
    sec_p = {k: bootstrap_p_two_sided(sec_reps[k]) for k in ("C", "D_clean")}
    sec_adj = holm(sec_p)

    descriptive = {
        "matched_b0_D": weighted_question_mean(per_question_D(records, "b0", "b0")),
        "matched_placebo_D": weighted_question_mean(
            per_question_D(records, "placebo_b2", "placebo_b2")),
        "placebo_b2_debate_error": condition_means(
            error_by_cell(debate, ("placebo_b2",), invalid_wrong=True,
                          common_support=False), ("placebo_b2",))["placebo_b2"],
    }

    het = {}
    for (q, j, d), rates in primary_rates.items():
        het.setdefault((j, d), []).append(rates)
    heterogeneity = {}
    for (j, d), cells in sorted(het.items()):
        n = len(cells)
        m = {c: sum(r[c] for r in cells) / n for c in PRIMARY_CONDITIONS}
        heterogeneity[f"{j}|{d}"] = {
            "n_questions": n,
            "H": m["sequential_b2"] - m["b0"],
            "P": m["sequential_b2"] - m["batch_same_qa_b2"],
            "R": m["batch_same_qa_b2"] - m["b0"],
        }

    try:
        engine_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, check=True).stdout.strip()
    except Exception:
        engine_commit = "unavailable"
    manifest_doc = json.loads((root / args.manifest).read_text(encoding="utf-8"))
    strata = defaultdict(list)
    for q, w in sorted(world_of.items()):
        strata[w].append(q)

    out = {
        "spec": "rejudge/phase2_protocol.json decisions.primary_tests, frozen 2026-07-16",
        "pins": "rejudge/phase2_analysis_pins_2026-08-11.json",
        "selftest": bool(getattr(args, "selftest", False)),
        "B": args.B, "seed": args.seed,
        "alpha_familywise": 0.05,
        "integrity": {
            "engine_commit": engine_commit,
            "python": platform.python_version(),
            "input_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
            "execution_identity_sha256": manifest_doc.get("execution_identity_sha256"),
            "draw_matrix_sha256": hashlib.sha256(
                json.dumps(draws, sort_keys=True).encode()).hexdigest(),
            "question_strata": {w: qs for w, qs in sorted(strata.items())},
        },
        "population": {"questions": len({r['question_id'] for r in debate}),
                       "judges": len({r['judge'] for r in debate}),
                       "debaters": len({r['debater'] for r in debate}),
                       "judgment_rows": len(debate)},
        "condition_error_rates": point_means,
        "primary": {
            i: {
                "estimate": point[i],
                "ci95": percentile_ci(reps[i]),
                "p_two_sided": pvals[i],
                "p_holm": adj[i],
            } for i in PRIMARY_IDS
        },
        "identity_max_abs_gap_across_replicates": identity_gap,
        "valid_only_sensitivity": {
            i: {"estimate": sens_point[i], "ci95": percentile_ci(sens_reps[i])}
            for i in PRIMARY_IDS
        },
        "invalid_counts": {
            "strict_invalid_rows": sum(1 for r in debate if r["correct"] is None),
            "total_rows": len(debate),
        },
        "secondary": {
            k: {"estimate": sec_point[k],
                "ci95": percentile_ci(sec_reps[k]),
                "p_two_sided": sec_p[k],
                "p_holm": sec_adj[k]}
            for k in ("C", "D_clean")
        },
        "secondary_valid_only_sensitivity": {
            "C": weighted_question_mean(per_question_C(
                records, CAP_JUDGE, DEBATER_A, DEBATER_B, valid_only=True)),
            "D_clean": weighted_question_mean(per_question_D(
                records, "sequential_b2", "clean_b2", valid_only=True)),
        },
        "descriptive_sensitivities": descriptive,
        "heterogeneity_judge_x_debater_descriptive": heterogeneity,
        "limitation": "82 questions sit across only three worlds (27/28/27); the "
                      "question bootstrap cannot express world-level dependence, so "
                      "claims stay conditional on these three worlds.",
    }
    if getattr(args, "selftest", False):
        expect = {"H": 0.10, "P": 0.06, "R": 0.04}
        for i, want in expect.items():
            got = point[i]
            assert abs(got - want) < 0.03, f"selftest recovery failed for {i}: {got} vs {want}"
        assert abs(sec_point["D_clean"] - 0.15) < 0.03, sec_point
        # C's interaction of four 6-slot cell means has sd near 0.04 under the
        # synthetic rates, so the recovery band is 3 sigma, unlike the tighter
        # bands above whose estimators average hundreds of cells.
        assert abs(sec_point["C"] - 0.0) < 0.12, sec_point
        print("SELFTEST PASS: known estimands recovered, identity and stratification "
              "asserts held on the full-size synthetic run")

    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({"primary": out["primary"],
                      "condition_error_rates": point_means,
                      "identity_max_abs_gap": identity_gap}, indent=1))
    print(f"full results written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
