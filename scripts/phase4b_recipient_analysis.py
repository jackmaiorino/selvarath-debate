"""Offline frozen Phase 4B recipient analysis. No provider calls or launch authority.

CLI: python scripts/phase4b_recipient_analysis.py --inputs DIR --results FILE --out DIR
Only completed responses belong in results; administrative attempts stay in the
runner journal. Existing Phase 4A helpers and archived artifacts are unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import phase4_analysis as base

AnalysisError = base.AnalysisError
QWEN, LLAMA = base.QWEN, base.LLAMA
JUDGES, DEBATERS, WORLD_COUNTS = base.JUDGES, base.DEBATERS, base.WORLD_COUNTS
ARMS = ("qwen_history_original", "qwen_history_repaired", "llama_history_original", "llama_history_repaired")
CELLS = tuple((judge, arm) for judge in JUDGES for arm in ARMS)
BOOTSTRAP_REPLICATES, BOOTSTRAP_SEED = 10000, 2026091203
PROTOCOL_SHA256 = "f76a1ce7e10a9c961d983f31fa701882fb47111be57876587098c0f00ee10a12"
PRIMARY = {
    "R_pooled": ((1, -1, 1, -1, 1, -1, 1, -1), 4, 79),
    "R_recipient_difference": ((1, -1, 1, -1, -1, 1, -1, 1), 2, 40),
}
METRICS = {
    **{f"arm_{i}": (tuple(int(i == j) for j in range(8)), 1) for i in range(8)},
    **{name: (coef, divisor) for name, (coef, divisor, _) in PRIMARY.items()},
    "R_Qwen": ((1, -1, 1, -1, 0, 0, 0, 0), 2),
    "R_Llama": ((0, 0, 0, 0, 1, -1, 1, -1), 2),
    "R_qwen_donor": ((1, -1, 0, 0, 1, -1, 0, 0), 2),
    "R_llama_donor": ((0, 0, 1, -1, 0, 0, 1, -1), 2),
    **{f"R_{recipient}_{donor}": (tuple((1 if k == i else -1 if k == i + 1 else 0) for k in range(8)), 1)
       for i, recipient, donor in ((0, "Qwen", "qwen"), (2, "Qwen", "llama"), (4, "Llama", "qwen"), (6, "Llama", "llama"))},
}
INPUT_FILES = ("calls.jsonl", "units_private.jsonl", "packets.jsonl", "edit_map_private.jsonl")


def _require(condition, message):
    if not condition:
        raise AnalysisError(message)


def validate_panel(calls, units):
    """Validate all 656 units, mirrored question/debater strata and eight cells."""
    call_by_id, unit_by_id = base._unique(calls, "cell_id"), base._unique(units, "unit_id")
    _require(len(units) == 656 and len(calls) == 5248, "Frozen Phase 4B requires 656 units and 5248 calls")
    strata, worlds, identities = defaultdict(list), {}, set()
    for unit in units:
        qid, world, debater = (unit.get(k) for k in ("question_id", "world", "debater"))
        _require(isinstance(qid, str) and bool(qid) and world in WORLD_COUNTS and debater in DEBATERS,
                 "Invalid question/world/debater in unit panel")
        _require(qid not in worlds or worlds[qid] == world, "A question cannot belong to multiple worlds")
        worlds[qid] = world
        _require(type(unit.get("side")) is int and unit["side"] in (0, 1), "Invalid mirrored side")
        _require(type(unit.get("transcript_index")) is int and unit["transcript_index"] in (0, 1, 2),
                 "Invalid source transcript index")
        _require(unit.get("correct_position") in ("A", "B"), "Unit lacks its frozen answer key")
        identity = (qid, debater, unit["transcript_index"], unit["side"])
        _require(identity not in identities, "Duplicate semantic unit identity")
        identities.add(identity)
        strata[(qid, debater)].append(unit)
    _require(len(worlds) == 82 and dict(Counter(worlds.values())) == WORLD_COUNTS, "Wrong frozen question/world allocation")
    for qid in worlds:
        for debater in DEBATERS:
            stratum, pairs = strata[(qid, debater)], defaultdict(list)
            for unit in stratum:
                pairs[unit["transcript_index"]].append(unit)
            _require(len(stratum) == 4 and len(pairs) == 2, "Each question/debater needs two mirrored transcript pairs")
            _require(all({u["side"] for u in p} == {0, 1} and {u["correct_position"] for u in p} == {"A", "B"}
                         for p in pairs.values()), "Mirrored answer keys are inconsistent")
    observed = defaultdict(set)
    for call in calls:
        uid, cell = call.get("unit_id"), (call.get("judge"), call.get("arm"))
        _require(uid in unit_by_id and cell in CELLS and cell not in observed[uid], "Unknown or duplicated unit/recipient/arm")
        arm = call["arm"]
        donor, repair = arm.rsplit("_", 1)
        _require(call.get("packet_id") == f"{uid}:{arm}" and call["cell_id"] == f"{uid}:{call['judge']}:{arm}",
                 "Call or packet identity differs from the frozen encoding")
        _require(call.get("donor_arm") == donor and call.get("repair_status") == repair, "Call donor/repair identity disagrees")
        _require(isinstance(call.get("messages_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", call["messages_sha256"]),
                 "Call lacks a messages hash")
        observed[uid].add(cell)
    _require(set(observed) == set(unit_by_id) and all(v == set(CELLS) for v in observed.values()),
             "Every unit must contain exactly all eight planned cells")
    return call_by_id, unit_by_id


def load_inputs(inputs):
    inputs = Path(inputs)
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    _require(manifest.get("schema_version") == "phase4b_recipient_prepared_panel_v1", "Unsupported recipient manifest")
    _require(manifest.get("phase4_protocol_sha256") == PROTOCOL_SHA256
             and base._sha(ROOT / "rejudge/phase4_protocol_v1.json") == PROTOCOL_SHA256, "Frozen protocol mismatch")
    _require(manifest.get("analysis_seed") == BOOTSTRAP_SEED and manifest.get("arms") == list(ARMS),
             "Manifest analysis seed or arm order differs from the frozen design")
    _require(manifest.get("review_mode") == "ai_source_review" and manifest.get("independent_human_validation") is False,
             "Prepared review mode differs from the approved AI-review amendment")
    for name in INPUT_FILES:
        record, path = manifest.get("outputs", {}).get(name, {}), inputs / name
        _require(path.stat().st_size == record.get("bytes") and base._sha(path) == record.get("sha256"),
                 "Prepared input hash mismatch: " + name)
    calls, units = base._read_jsonl(inputs / "calls.jsonl"), base._read_jsonl(inputs / "units_private.jsonl")
    validate_panel(calls, units)
    packets = base._unique(base._read_jsonl(inputs / "packets.jsonl"), "packet_id")
    planned = defaultdict(list)
    for call in calls:
        planned[call["packet_id"]].append(call)
    _require(len(packets) == 2624 and set(packets) == set(planned), "Prepared packet roster differs")
    for pid, packet in packets.items():
        group = planned[pid]
        _require(len(group) == 2 and {c["judge"] for c in group} == set(JUDGES), "Each packet must serve both recipients")
        _require(isinstance(packet.get("messages"), list) and bool(packet["messages"]), "Packet lacks messages")
        digest = base._message_sha(packet["messages"])
        _require(packet.get("messages_sha256") == digest and all(c["messages_sha256"] == digest for c in group),
                 "Prepared packet message hash mismatch")
        _require(all(packet.get(k) == group[0][k] for k in ("donor_arm", "repair_status")), "Packet donor/repair mismatch")
    return calls, units, manifest


def score_rows(calls, units, rows):
    for row in rows:
        call = calls.get(row.get("cell_id"))
        _require(call is not None, "Unplanned result cell")
        _require(row.get("configuration_valid") is True and row.get("returned_model_id") == call["judge"],
                 "Completed result model/configuration mismatch")
        _require(all(row.get(k) == call[k] for k in ("packet_id", "messages_sha256")), "Completed result packet/hash mismatch")
        _require(isinstance(row.get("request_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", row["request_sha256"]),
                 "Completed result lacks request hash")
    return base.score_rows(calls, units, rows)


def question_means(units, scored, *, common_valid=False, invalid_as_correct=False):
    strata, kept = defaultdict(list), set()
    for uid, unit in units.items():
        strata[(unit["question_id"], unit["debater"])].append(uid)
        if not common_valid or all(scored[(uid, *cell)]["valid"] for cell in CELLS):
            kept.add(uid)
    empty = [list(key) for key, ids in sorted(strata.items()) if not any(uid in kept for uid in ids)]
    support = {"retained_units": len(kept), "dropped_units": len(units) - len(kept), "empty_question_debater_strata": empty}
    if empty:
        return None, support
    per_question = defaultdict(list)
    for (qid, _), ids in sorted(strata.items()):
        ids = [uid for uid in ids if uid in kept]
        means = [Fraction(sum(0 if invalid_as_correct and not scored[(uid, *cell)]["valid"]
                              else scored[(uid, *cell)]["strict_error"] for uid in ids), len(ids)) for cell in CELLS]
        per_question[qid].append(means)
    return {qid: [sum((d[i] for d in debaters), Fraction()) / 2 for i in range(8)]
            for qid, debaters in per_question.items()}, support


def contrast_bounds(units, scored, *, invalid_unknown):
    result = {}
    for name, (coefficients, divisor, _) in PRIMARY.items():
        lower = upper = 0
        for uid in units:
            for cell, coefficient in zip(CELLS, coefficients):
                row = scored.get((uid, *cell))
                if row is None or (invalid_unknown and not row["valid"]):
                    lower += min(0, coefficient)
                    upper += max(0, coefficient)
                else:
                    lower += coefficient * row["strict_error"]
                    upper += coefficient * row["strict_error"]
        denominator = divisor * len(units)
        result[name] = {"lower_numerator": lower, "upper_numerator": upper, "denominator": denominator,
                        "lower": lower / denominator, "upper": upper / denominator,
                        "kind": "observed_panel_outcome_bounds_not_confidence_intervals"}
    return result


def arm_counts(units, scored):
    rows = []
    for judge, arm in CELLS:
        observed = [scored[(uid, judge, arm)] for uid in units if (uid, judge, arm) in scored]
        errors, invalids = sum(r["strict_error"] for r in observed), sum(not r["valid"] for r in observed)
        rows.append({"judge": judge, "arm": arm, "expected": len(units), "completed": len(observed),
                     "administrative_missing": len(units) - len(observed), "correct": len(observed) - errors,
                     "wrong_valid": errors - invalids, "completed_invalid": invalids, "strict_errors": errors,
                     "observed_error_rate": errors / len(observed) if observed else None,
                     "full_panel_error_bounds": [errors / len(units), (errors + len(units) - len(observed)) / len(units)]})
    return rows


def paired_transitions(units, scored):
    rows = []
    for judge in JUDGES:
        for donor in ("qwen_history", "llama_history"):
            matched = [(scored[(uid, judge, donor + "_original")], scored[(uid, judge, donor + "_repaired")])
                       for uid in units if all((uid, judge, donor + suffix) in scored for suffix in ("_original", "_repaired"))]
            counts = Counter((o["strict_error"], r["strict_error"]) for o, r in matched)
            net = counts[(1, 0)] - counts[(0, 1)]
            rows.append({"judge": judge, "donor_arm": donor, "planned_pairs": len(units), "completed_pairs": len(matched),
                         "administrative_missing_pairs": len(units) - len(matched), "error_to_correct": counts[(1, 0)],
                         "correct_to_error": counts[(0, 1)], "both_correct": counts[(0, 0)], "both_error": counts[(1, 1)],
                         "net_repairs": net, "observed_repair_benefit": net / len(matched) if matched else None,
                         "original_invalid": sum(not o["valid"] for o, _ in matched),
                         "repaired_invalid": sum(not r["valid"] for _, r in matched),
                         "interpretation": "Strict outcome transitions on completed pairs; error includes completed invalid responses. Incomplete matched rates are descriptive only."})
    return rows


def primary_summary(inference, prefix=""):
    adjusted = base.holm({name: inference[prefix + name]["p_two_sided"] for name in PRIMARY})
    return {name: {**inference[prefix + name], "p_holm": adjusted[name]} for name in PRIMARY}


def analyze_rows(calls, units, result_rows, *, bootstrap_replicates=BOOTSTRAP_REPLICATES, bootstrap_seed=BOOTSTRAP_SEED):
    call_by_id, unit_by_id = validate_panel(calls, units)
    scored, scored_rows, duplicates, cost = score_rows(call_by_id, unit_by_id, result_rows)
    missing = sorted(set(call_by_id) - {r["cell_id"] for r in scored_rows})
    complete = not missing
    frozen = bootstrap_replicates == BOOTSTRAP_REPLICATES and bootstrap_seed == BOOTSTRAP_SEED
    coverage = arm_counts(unit_by_id, scored)
    result = {
        "schema_version": "phase4b_recipient_analysis_v1", "status": "complete" if complete else "incomplete",
        "parser_version": base.PARSER_VERSION, "formal_gates_evaluated": complete and frozen,
        "cell_order": [{"judge": judge, "arm": arm} for judge, arm in CELLS],
        "contrast_definitions": {"R_pooled": "Mean across recipients and donor histories of error(original)-error(repaired).",
                                 "R_recipient_difference": "R_Qwen-R_Llama, where each recipient is averaged equally over both donors."},
        "bootstrap": {"replicates": bootstrap_replicates, "seed": bootstrap_seed,
                      "prng": "CPython random.Random Mersenne Twister", "stratification": WORLD_COUNTS,
                      "p_value": "finite-corrected two-sided sign-tail", "percentile_type": 7,
                      "primary_family": "Holm two; 95% and Bonferroni 97.5% marginal intervals",
                      "weighting": "Equal questions and equal debaters within each question; fixed world proportions 27/82, 28/82, 27/82."},
        "coverage": {"planned": len(calls), "completed": len(scored), "administrative_missing": len(missing),
                     "missing_cell_ids": missing, "identical_duplicate_rows_ignored": duplicates,
                     "completed_invalid": sum(not r["valid"] for r in scored_rows)},
        "completed_response_cost_usd": cost,
        "completed_response_uncertain_usd": str(sum((Decimal(r["uncertain_usd"]) for r in scored_rows), Decimal())),
        "completed_responses_missing_usage": sum(not r["usage_complete"] for r in scored_rows),
        "arm_counts": coverage, "paired_transitions_descriptive": paired_transitions(unit_by_id, scored),
        "primary": None, "descriptive_effects": None, "common_valid_sensitivity": None,
        "uniform_invalid_as_correct_sensitivity": None,
        "strict_missing_outcome_bounds": contrast_bounds(unit_by_id, scored, invalid_unknown=False),
        "invalid_and_missing_outcome_bounds": contrast_bounds(unit_by_id, scored, invalid_unknown=True),
        "stage_4c_semantic_entry_eligible": False, "stage_4c_paid_execution_authorized": False,
        "source_review_mode": "ai_source_review", "independent_human_validation": False,
        "limitations": [
            "Two fixed recipients and donor histories on 82 selected questions in three fixed worlds; no model-population or equivalence claim.",
            "This estimates final-readout repair of captured histories. Earlier adaptive queries remain fixed; it does not identify a corrected live-oracle policy effect.",
            "Labels passed amended Codex AI source review, not independent human validation. No oracle-truth or oracle-error-prevalence estimate is produced.",
            "Question-cluster uncertainty is conditional on the fixed worlds and endpoints. Failure to reject is not equivalence.",
            "Completed invalid verdicts count as wrong; administrative missingness is not an error and prevents formal gates.",
            "Recorded cost covers completed responses. The runner ledger retains failed attempts, unknown delivery and cap exposure.",
            "Only positive pooled benefit with matching statistical, practical and semantic support qualifies for the planned Phase 4C branch; qualification grants no paid authority.",
        ],
    }
    if not complete:
        result["limitations"].append("Administrative missingness remains: no complete-panel point estimates, formal tests or continuation gates.")
        return result, sorted(scored_rows, key=lambda r: r["cell_id"])
    strict, _ = question_means(unit_by_id, scored)
    common, support = question_means(unit_by_id, scored, common_valid=True)
    recoded, _ = question_means(unit_by_id, scored, invalid_as_correct=True)
    questions = base._metric_questions(strict, METRICS)
    primaries = {name: METRICS[name] for name in PRIMARY}
    if common is not None:
        questions.update({"valid_" + n: q for n, q in base._metric_questions(common, primaries).items()})
    questions.update({"recoded_" + n: q for n, q in base._metric_questions(recoded, primaries).items()})
    worlds = {u["question_id"]: u["world"] for u in units}
    inference = base.bootstrap_joint(questions, worlds, replicates=bootstrap_replicates, seed=bootstrap_seed)
    primary = primary_summary(inference)
    valid_primary = primary_summary(inference, "valid_") if common is not None else None
    result["common_valid_sensitivity"] = {"defined": common is not None, "support": support, "primary": valid_primary,
                                           "selection": "A unit is retained only when all eight recipient/donor/repair responses are valid."}
    result["uniform_invalid_as_correct_sensitivity"] = {"primary": primary_summary(inference, "recoded_"),
                                                        "kind": "uniform_recoding_sensitivity_not_bounds"}
    counts = [r["strict_errors"] for r in coverage]
    for name, item in primary.items():
        coefficients, divisor, threshold = PRIMARY[name]
        numerator = sum(c * count for c, count in zip(coefficients, counts))
        item.update({"integer_numerator": numerator, "denominator": divisor * 656, "practical_integer_threshold": threshold,
                     "statistical_pass": frozen and item["p_holm"] <= .05, "practical_pass": abs(numerator) >= threshold,
                     "decision_gate_pass": frozen and item["p_holm"] <= .05 and abs(numerator) >= threshold})
        valid = valid_primary[name] if valid_primary else None
        bounds = result["invalid_and_missing_outcome_bounds"][name]
        direction = bounds["lower"] > 0 if numerator > 0 else bounds["upper"] < 0 if numerator < 0 else False
        valid_pass = bool(valid and valid["estimate"] * numerator > 0 and abs(valid["estimate"]) >= .03 and valid["p_holm"] <= .05)
        item["semantic_followup_gate_pass"] = bool(item["decision_gate_pass"] and valid_pass and direction)
        item["semantic_gate_components"] = {"common_valid_same_direction_practical_statistical": valid_pass,
                                             "invalid_bounds_exclude_zero_in_primary_direction": direction}
    result["primary"] = primary
    pooled = primary["R_pooled"]
    result["stage_4c_semantic_entry_eligible"] = pooled["integer_numerator"] >= 79 and pooled["semantic_followup_gate_pass"]
    for i, row in enumerate(coverage):
        row.update(error_rate=row["strict_errors"] / 656, ci95=inference[f"arm_{i}"]["ci95"])
    result["descriptive_effects"] = {n: {"estimate": inference[n]["estimate"], "ci95": inference[n]["ci95"]}
                                     for n in METRICS if not n.startswith("arm_") and n not in PRIMARY}
    result["question_values"] = [{"question_id": qid, "world": worlds[qid], **{n: float(q[qid]) for n, q in questions.items()}}
                                 for qid in sorted(worlds)]
    result["subgroups_descriptive"] = {
        field: {str(value): {"arm_counts": arm_counts({uid: u for uid, u in unit_by_id.items() if u[field] == value}, scored),
                             "paired_transitions": paired_transitions({uid: u for uid, u in unit_by_id.items() if u[field] == value}, scored)}
                for value in sorted({u[field] for u in units})} for field in ("world", "debater", "side")}
    if not frozen:
        result["limitations"].append("Nonfrozen diagnostic bootstrap settings: all formal gates are disabled.")
    return result, sorted(scored_rows, key=lambda r: r["cell_id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    inputs, results, out = args.inputs.resolve(), args.results.resolve(), args.out.resolve()
    _require(not out.is_relative_to(inputs) and not out.is_relative_to(ROOT), "Private analysis output must be outside Git and frozen inputs")
    paths = [out / "phase4b_recipient_analysis.json", out / "scored_cells.jsonl"]
    _require(results not in paths, "Analysis output would overwrite completed results")
    manifest_hash, result_hash = base._sha(inputs / "manifest.json"), base._sha(results)
    calls, units, manifest = load_inputs(inputs)
    analysis, scored = analyze_rows(calls, units, base._read_jsonl(results))
    _require(base._sha(inputs / "manifest.json") == manifest_hash and base._sha(results) == result_hash,
             "Inputs or results changed during analysis")
    _require(all(base._sha(inputs / name) == manifest["outputs"][name]["sha256"] for name in INPUT_FILES),
             "Prepared input changed during analysis")
    analysis["frozen_repair_coverage"] = manifest.get("repair_coverage")
    analysis["provenance"] = {"prepared_manifest_sha256": manifest_hash, "prepared_protocol_sha256": manifest["phase4_protocol_sha256"],
                              "results_sha256": result_hash, "analysis_script_sha256": base._sha(Path(__file__)),
                              "reused_phase4_analysis_sha256": base._sha(Path(base.__file__)), "python": sys.version,
                              "input_hashes": {name: manifest["outputs"][name]["sha256"] for name in INPUT_FILES}}
    out.mkdir(parents=True, exist_ok=True)
    base._atomic_write(paths[1], "".join(json.dumps(r, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n" for r in scored))
    base._atomic_write(paths[0], json.dumps(analysis, indent=2, ensure_ascii=True, allow_nan=False) + "\n")
    print(json.dumps({"status": analysis["status"], "completed": analysis["coverage"]["completed"],
                      "administrative_missing": analysis["coverage"]["administrative_missing"],
                      "stage_4c_semantic_entry_eligible": analysis["stage_4c_semantic_entry_eligible"],
                      "analysis_path": str(paths[0]), "analysis_sha256": base._sha(paths[0])}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnalysisError, OSError, json.JSONDecodeError) as exc:
        print(f"Phase 4B recipient analysis failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
