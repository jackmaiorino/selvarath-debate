"""Frozen offline Phase5 analysis; no provider calls or execution authority.

CLI: python scripts/phase5_analysis.py --inputs DIR --results FILE --out DIR
Primary support, semantic support and candidate readiness are distinct outputs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import phase4_analysis as base

AnalysisError = base.AnalysisError
QWEN, LLAMA = base.QWEN, base.LLAMA
JUDGES, DEBATERS, WORLD_COUNTS = base.JUDGES, base.DEBATERS, base.WORLD_COUNTS
RECIPIENT_NAMES = ("Qwen", "Llama")
CONTEXTS = ("empty", "qwen_history", "llama_history")
PROMPTS = ("ordinary", "scope")
ARMS = tuple(f"{prompt}_{context}" for prompt in PROMPTS for context in CONTEXTS)
CELLS = tuple((judge, arm) for judge in JUDGES for arm in ARMS)
PROTOCOL_SHA256 = "c611aa62d7b5290cc240e82a881488c3ce309b26ce33ab3979c3e09838043f07"
BOOTSTRAP_REPLICATES, BOOTSTRAP_SEED, ORDER_SEED = 10000, 2026091208, 2026091209
CALL_SEED_NAMESPACE = "phase5-evidence-scope-verdict-v1"
INPUT_FILES = ("packets.jsonl", "calls.jsonl", "units_private.jsonl", "prompt_edits_private.jsonl")
PRIMARY_COEFFICIENTS = (-2, 1, 1, 2, -1, -1) * 2
PRIMARY_DENOMINATOR, PRIMARY_THRESHOLD = 2624, 79


def _require(condition, message):
    if not condition:
        raise AnalysisError(message)


def _embed(values, recipient):
    return tuple(values if recipient == 0 else (0,) * 6) + tuple(values if recipient == 1 else (0,) * 6)


METRICS = {**{f"arm_{i}": (tuple(int(i == j) for j in range(12)), 1) for i in range(12)},
           "S_pooled": (PRIMARY_COEFFICIENTS, 4),
           "S_recipient_difference": ((-2, 1, 1, 2, -1, -1, 2, -1, -1, -2, 1, 1), 2)}
for _name, _coefs, _divisor in (
    ("S", (-2, 1, 1, 2, -1, -1), 2),
    ("B", (0, 1, 1, 0, -1, -1), 2),
    ("E", (1, 0, 0, -1, 0, 0), 1),
    ("H_ordinary", (-2, 1, 1, 0, 0, 0), 2),
    ("H_scope", (0, 0, 0, -2, 1, 1), 2),
    ("history_scope_minus_empty_ordinary", (-2, 0, 0, 0, 1, 1), 2),
    ("B_qwen_history", (0, 1, 0, 0, -1, 0), 1),
    ("B_llama_history", (0, 0, 1, 0, 0, -1), 1),
    ("S_qwen_history", (-1, 1, 0, 1, -1, 0), 1),
    ("S_llama_history", (-1, 0, 1, 1, 0, -1), 1),
):
    for _recipient, _label in enumerate(RECIPIENT_NAMES):
        METRICS[f"{_name}_{_label}"] = (_embed(_coefs, _recipient), _divisor)
    METRICS[f"{_name}_pooled"] = (_coefs * 2, _divisor * 2)
GUARD_METRICS = ("S_pooled", "B_pooled", *(f"{name}_{recipient}" for recipient in RECIPIENT_NAMES for name in ("B", "E", "H_scope")))


def validate_panel(calls, units):
    call_by_id, unit_by_id = base._unique(calls, "cell_id"), base._unique(units, "unit_id")
    _require(len(units) == 656 and len(calls) == 7872, "Frozen Phase5 requires 656 units and 7872 calls")
    strata, worlds, identities = defaultdict(list), {}, set()
    for unit in units:
        qid, world, debater = (unit.get(k) for k in ("question_id", "world", "debater"))
        _require(isinstance(qid, str) and bool(qid) and world in WORLD_COUNTS and debater in DEBATERS,
                 "Invalid question/world/debater")
        _require(qid not in worlds or worlds[qid] == world, "Question belongs to multiple worlds")
        worlds[qid] = world
        _require(type(unit.get("side")) is int and unit["side"] in (0, 1), "Invalid mirrored side")
        _require(type(unit.get("transcript_index")) is int and unit["transcript_index"] in (0, 1, 2), "Invalid transcript index")
        _require(unit.get("correct_position") in ("A", "B"), "Missing frozen answer key")
        key = (qid, debater, unit["transcript_index"], unit["side"])
        _require(key not in identities, "Duplicate semantic unit identity")
        identities.add(key)
        strata[(qid, debater)].append(unit)
    _require(len(worlds) == 82 and dict(Counter(worlds.values())) == WORLD_COUNTS, "Wrong question/world allocation")
    for qid in worlds:
        for debater in DEBATERS:
            group, pairs = strata[(qid, debater)], defaultdict(list)
            for unit in group:
                pairs[unit["transcript_index"]].append(unit)
            _require(len(group) == 4 and len(pairs) == 2, "Each question/debater requires two mirrored transcript pairs")
            _require(all({u["side"] for u in p} == {0, 1} and {u["correct_position"] for u in p} == {"A", "B"}
                         for p in pairs.values()), "Mirrored answer keys are inconsistent")
    seen = defaultdict(set)
    for call in calls:
        uid, cell = call.get("unit_id"), (call.get("judge"), call.get("arm"))
        _require(uid in unit_by_id and cell in CELLS and cell not in seen[uid], "Unknown or duplicated unit/recipient/arm")
        prompt, context = call["arm"].split("_", 1)
        _require(call.get("prompt_variant") == prompt and call.get("context_arm") == context, "Prompt/context identity mismatch")
        _require(call["cell_id"] == f"{uid}:{call['judge']}:{call['arm']}" and call.get("packet_id") == f"{uid}:{call['arm']}",
                 "Frozen call/packet identity mismatch")
        _require(isinstance(call.get("messages_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", call["messages_sha256"]),
                 "Missing messages hash")
        expected_seed = int(hashlib.sha256(f"{CALL_SEED_NAMESPACE}|{uid}|{call['judge']}".encode()).hexdigest()[:8], 16) % 2147483647
        _require(type(call.get("seed")) is int and call["seed"] == expected_seed, "Frozen matched call seed differs")
        _require(call.get("temperature") == .3 and not isinstance(call["temperature"], bool)
                 and type(call.get("max_tokens")) is int and call["max_tokens"] == (16384 if call["judge"] == QWEN else 512)
                 and call.get("stream") is False and not {"top_p", "reasoning_effort"} & set(call), "Request profile changed")
        seen[uid].add(cell)
    _require(set(seen) == set(unit_by_id) and all(s == set(CELLS) for s in seen.values()), "Incomplete twelve-cell panel")
    return call_by_id, unit_by_id


def load_inputs(inputs):
    inputs = Path(inputs)
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    _require(manifest.get("schema_version") == "phase5_evidence_scope_prepared_panel_v1", "Unsupported Phase5 manifest")
    protocol_path = ROOT / "rejudge/phase5_protocol_v1.json"
    _require(manifest.get("protocol_sha256") == PROTOCOL_SHA256 and base._sha(protocol_path) == PROTOCOL_SHA256,
             "Frozen Phase5 protocol mismatch")
    _require(manifest.get("models") == list(JUDGES) and manifest.get("arms") == list(ARMS)
             and manifest.get("analysis_seed") == BOOTSTRAP_SEED and manifest.get("order_seed") == ORDER_SEED
             and manifest.get("call_seed_namespace") == CALL_SEED_NAMESPACE, "Manifest design or seed mismatch")
    protocol = json.loads(protocol_path.read_bytes())
    provenance = manifest.get("provenance", {})
    _require(manifest.get("review_mode") == "ai_source_review" and manifest.get("independent_human_validation") is False,
             "Prepared review provenance must identify AI review without human validation")
    _require(provenance.get("repair_labels_sha256") == protocol["sources"]["repair_labels_sha256"]
             and isinstance(provenance.get("review_amendment_sha256"), str)
             and re.fullmatch(r"[0-9a-f]{64}", provenance["review_amendment_sha256"]), "Reviewed label provenance mismatch")
    for name in INPUT_FILES:
        record, path = manifest.get("outputs", {}).get(name, {}), inputs / name
        _require(path.stat().st_size == record.get("bytes") and base._sha(path) == record.get("sha256"), "Prepared input hash mismatch: " + name)
    calls, units = base._read_jsonl(inputs / "calls.jsonl"), base._read_jsonl(inputs / "units_private.jsonl")
    validate_panel(calls, units)
    packets = base._unique(base._read_jsonl(inputs / "packets.jsonl"), "packet_id")
    planned = defaultdict(list)
    for call in calls:
        planned[call["packet_id"]].append(call)
    _require(len(packets) == 3936 and set(packets) == set(planned), "Prepared packet roster mismatch")
    for pid, packet in packets.items():
        group = planned[pid]
        _require(len(group) == 2 and {c["judge"] for c in group} == set(JUDGES), "Both recipients must share each packet")
        messages = packet.get("messages")
        _require(isinstance(messages, list) and bool(messages) and all(isinstance(m, dict) and set(m) == {"role", "content"}
                 and m["role"] in ("system", "user", "assistant") and isinstance(m["content"], str) for m in messages), "Invalid message shape")
        digest = base._message_sha(messages)
        _require(packet.get("messages_sha256") == digest and all(c["messages_sha256"] == digest for c in group), "Packet message hash mismatch")
        _require(all(packet.get(k) == group[0][k] for k in ("prompt_variant", "context_arm")), "Packet prompt/context mismatch")
    suffix = protocol["intervention"]["scope_suffix"]
    for unit in units:
        for context in CONTEXTS:
            original = packets[f"{unit['unit_id']}:ordinary_{context}"]["messages"]
            scoped = packets[f"{unit['unit_id']}:scope_{context}"]["messages"]
            _require(original[0]["role"] == "system", "Scope requires message0 system content")
            expected = [{**original[0], "content": original[0]["content"] + suffix}, *original[1:]]
            _require(scoped == expected, "Scope packet changes more than the frozen system suffix")
    return calls, units, manifest


def score_rows(calls, units, rows):
    for row in rows:
        call = calls.get(row.get("cell_id"))
        _require(call is not None, "Unplanned result cell")
        _require(row.get("configuration_valid") is True and row.get("returned_model_id") == call["judge"], "Returned model/configuration mismatch")
        _require(all(row.get(k) == call[k] for k in ("packet_id", "messages_sha256")), "Result packet/hash mismatch")
        _require(isinstance(row.get("request_sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", row["request_sha256"]), "Missing request hash")
    scored, scored_rows, duplicates, cost = base.score_rows(calls, units, rows)
    for row in scored_rows:
        row.update({k: calls[row["cell_id"]][k] for k in ("context_arm", "prompt_variant")})
    return scored, scored_rows, duplicates, cost


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
    questions = defaultdict(list)
    for (qid, _), ids in sorted(strata.items()):
        ids = [uid for uid in ids if uid in kept]
        questions[qid].append([Fraction(sum(0 if invalid_as_correct and not scored[(uid, *cell)]["valid"]
                                            else scored[(uid, *cell)]["strict_error"] for uid in ids), len(ids)) for cell in CELLS])
    return {qid: [sum((d[i] for d in ds), Fraction()) / 2 for i in range(12)] for qid, ds in questions.items()}, support


def bootstrap_joint(question_values, worlds, *, replicates=BOOTSTRAP_REPLICATES, seed=BOOTSTRAP_SEED):
    """Same MT question draws as Phase4, extended with 5th/95th percentiles."""
    _require(type(replicates) is int and replicates > 0, "Bootstrap replicate count must be positive")
    qids = sorted(worlds)
    _require(bool(qids) and all(set(q) == set(qids) for q in question_values.values()), "Bootstrap metrics must share every question")
    groups = [[i for i, q in enumerate(qids) if worlds[q] == world] for world in sorted(set(worlds.values()))]
    vectors, denominators = {}, {}
    for name, values in question_values.items():
        fractions = [Fraction(values[q]) for q in qids]
        scale = math.lcm(*(f.denominator for f in fractions))
        vectors[name] = [int(f * scale) for f in fractions]
        denominators[name] = scale * len(qids)
    draws = {name: [] for name in vectors}
    rng = random.Random(seed)
    for _ in range(replicates):
        selected = [rng.choice(group) for group in groups for _ in group]
        for name, vector in vectors.items():
            draws[name].append(sum(vector[i] for i in selected) / denominators[name])
    return {name: {"estimate": float(sum(question_values[name].values(), Fraction()) / len(qids)),
                   "ci95": [base.type7(values, .025), base.type7(values, .975)],
                   "ci97_5": [base.type7(values, .0125), base.type7(values, .9875)],
                   "lower_one_sided95": base.type7(values, .05), "upper_one_sided95": base.type7(values, .95),
                   "p_two_sided": base.sign_tail_p(values)} for name, values in draws.items()}


def contrast_bounds(units, scored, *, invalid_unknown):
    result = {}
    for name in GUARD_METRICS:
        coefficients, divisor = METRICS[name]
        lower = upper = 0
        for uid in units:
            for coefficient, cell in zip(coefficients, CELLS):
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


def readiness_interval_checks(inference):
    checks = {"B_pooled_ci95_lower_positive": inference["B_pooled"]["ci95"][0] > 0}
    for recipient in RECIPIENT_NAMES:
        b, e, h = (inference[f"{name}_{recipient}"] for name in ("B", "E", "H_scope"))
        checks.update({f"{recipient}_B_point_nonnegative": b["estimate"] >= 0,
                       f"{recipient}_B_lower95_above_minus_1pp": b["lower_one_sided95"] > -.01,
                       f"{recipient}_E_lower95_above_minus_1pp": e["lower_one_sided95"] > -.01,
                       f"{recipient}_H_scope_upper95_below_1pp": h["upper_one_sided95"] < .01})
    return checks


def readiness_bound_checks(bounds):
    checks = {"S_lower_positive": bounds["S_pooled"]["lower"] > 0, "B_pooled_lower_positive": bounds["B_pooled"]["lower"] > 0}
    for recipient in RECIPIENT_NAMES:
        checks.update({f"{recipient}_B_lower_nonnegative": bounds[f"B_{recipient}"]["lower"] >= 0,
                       f"{recipient}_E_lower_above_minus_1pp": bounds[f"E_{recipient}"]["lower"] > -.01,
                       f"{recipient}_H_scope_upper_below_1pp": bounds[f"H_scope_{recipient}"]["upper"] < .01})
    return checks


def arm_counts(units, scored):
    result = []
    for judge, arm in CELLS:
        observed = [scored[(uid, judge, arm)] for uid in units if (uid, judge, arm) in scored]
        errors, invalid = sum(r["strict_error"] for r in observed), sum(not r["valid"] for r in observed)
        result.append({"judge": judge, "arm": arm, "expected": len(units), "completed": len(observed),
                       "administrative_missing": len(units) - len(observed), "correct": len(observed) - errors,
                       "wrong_valid": errors - invalid, "completed_invalid": invalid, "strict_errors": errors,
                       "observed_error_rate": errors / len(observed) if observed else None,
                       "full_panel_error_bounds": [errors / len(units), (errors + len(units) - len(observed)) / len(units)]})
    return result


def paired_transitions(units, scored):
    result = []
    for judge in JUDGES:
        for context in CONTEXTS:
            pairs = [(scored[(uid, judge, "ordinary_" + context)], scored[(uid, judge, "scope_" + context)]) for uid in units
                     if all((uid, judge, prompt + "_" + context) in scored for prompt in PROMPTS)]
            counts = Counter((a["strict_error"], b["strict_error"]) for a, b in pairs)
            result.append({"judge": judge, "context_arm": context, "planned_pairs": len(units), "completed_pairs": len(pairs),
                           "administrative_missing_pairs": len(units) - len(pairs), "error_to_correct": counts[(1, 0)],
                           "correct_to_error": counts[(0, 1)], "both_correct": counts[(0, 0)], "both_error": counts[(1, 1)],
                           "net_improvements": counts[(1, 0)] - counts[(0, 1)],
                           "ordinary_invalid": sum(not a["valid"] for a, _ in pairs), "scope_invalid": sum(not b["valid"] for _, b in pairs),
                           "interpretation": "Descriptive transitions on completed pairs; strict errors include completed invalid verdicts."})
    return result


def _descriptive(inference):
    return {key: value for key, value in inference.items() if key != "p_two_sided"}


def analyze_rows(calls, units, result_rows, *, bootstrap_replicates=BOOTSTRAP_REPLICATES, bootstrap_seed=BOOTSTRAP_SEED):
    call_by_id, unit_by_id = validate_panel(calls, units)
    scored, scored_rows, duplicates, cost = score_rows(call_by_id, unit_by_id, result_rows)
    missing = sorted(set(call_by_id) - {r["cell_id"] for r in scored_rows})
    complete = not missing
    frozen = type(bootstrap_replicates) is int and bootstrap_replicates == BOOTSTRAP_REPLICATES and type(bootstrap_seed) is int and bootstrap_seed == BOOTSTRAP_SEED
    counts = arm_counts(unit_by_id, scored)
    result = {"schema_version": "phase5_analysis_v1", "status": "complete" if complete else "incomplete",
              "parser_version": base.PARSER_VERSION, "formal_gates_evaluated": complete and frozen,
              "cell_order": [{"judge": j, "arm": a} for j, a in CELLS],
              "definitions": {"S": "B minus E: ordinary history-minus-empty error minus scoped history-minus-empty error.",
                              "B": "Mean donor-history error ordinary minus scope; positive means direct history benefit.",
                              "E": "Empty-context error ordinary minus scope; negative means scoped-empty harm.",
                              "H_scope": "Scoped mean history error minus scoped empty error; positive means scoped history harm."},
              "bootstrap": {"replicates": bootstrap_replicates, "seed": bootstrap_seed, "prng": "CPython random.Random Mersenne Twister",
                            "stratification": WORLD_COUNTS, "equal_question_and_debater_weights": True, "percentile_type": 7,
                            "primary_family": "One two-sided confirmatory S_pooled test; no Holm adjustment.",
                            "one_sided95_percentiles": [.05, .95], "ci97_5_role": "Supplemental; not a family correction."},
              "coverage": {"planned": len(calls), "completed": len(scored), "administrative_missing": len(missing),
                           "missing_cell_ids": missing, "identical_duplicate_rows_ignored": duplicates,
                           "completed_invalid": sum(not r["valid"] for r in scored_rows)},
              "completed_response_cost_usd": cost,
              "completed_response_uncertain_usd": str(sum((Decimal(r["uncertain_usd"]) for r in scored_rows), Decimal())),
              "completed_responses_missing_usage": sum(not r["usage_complete"] for r in scored_rows),
              "arm_counts": counts, "paired_transitions_descriptive": paired_transitions(unit_by_id, scored),
              "primary": None, "primary_supported": False, "semantic_primary_supported": False,
              "support_levels": {"primary_supported": "Complete frozen panel, positive numerator>=79, two-sided p<=0.05.",
                                 "semantic_primary_supported": "Primary support plus the frozen common-valid and coefficient-bound checks."},
              "descriptive_effects": None, "common_valid_sensitivity": None, "uniform_invalid_as_correct_sensitivity": None,
              "strict_missing_outcome_bounds": contrast_bounds(unit_by_id, scored, invalid_unknown=False),
              "invalid_and_missing_outcome_bounds": contrast_bounds(unit_by_id, scored, invalid_unknown=True),
              "candidate_readiness": {"evaluated": False, "passed": False, "strict_checks": None, "common_valid_checks": None, "coefficient_bound_checks": None},
              "next_stage_paid_execution_authorized": False, "source_review_mode": "ai_source_review", "independent_human_validation": False,
              "limitations": [
                  "The prompt was designed after earlier Phase4 outcomes; this is a new prospective comparison on a previously studied question frame.",
                  "The fixed instruction package includes added length and attention cues; it does not identify an individual sentence or internal neural mechanism.",
                  "Reviewed labels came from amended AI source review, not independent human validation; retained labels need not all be true.",
                  "Fixed questions, histories and endpoints do not establish untouched-task, live-query-policy, world-population or universal-model generalization.",
                  "Candidate readiness is a conjunction of prospective screens, not extra discoveries or separate model-population significance claims.",
                  "The 1pp tolerances allow some deterioration. Scoped-history versus scoped-empty plus tolerated empty deterioration does not guarantee 1pp total harm versus ordinary empty.",
                  "Failure of a margin screen may be unresolved at 82 question clusters; it does not negate a separately supported primary or establish equivalence.",
                  "Cost here covers completed responses; the runner ledger accounts for all failures, uncertainty and exposure. No subsequent paid work is authorized.",
              ]}
    if not complete:
        result["limitations"].append("Administrative missingness prevents complete-panel estimates, formal inference and all gates.")
        return result, sorted(scored_rows, key=lambda r: r["cell_id"])
    means, _ = question_means(unit_by_id, scored)
    common, support = question_means(unit_by_id, scored, common_valid=True)
    recoded, _ = question_means(unit_by_id, scored, invalid_as_correct=True)
    questions = base._metric_questions(means, METRICS)
    guard_metrics = {n: METRICS[n] for n in GUARD_METRICS}
    if common is not None:
        questions.update({"valid_" + n: q for n, q in base._metric_questions(common, guard_metrics).items()})
    questions.update({"recoded_" + n: q for n, q in base._metric_questions(recoded, guard_metrics).items()})
    worlds = {u["question_id"]: u["world"] for u in units}
    inference = bootstrap_joint(questions, worlds, replicates=bootstrap_replicates, seed=bootstrap_seed)
    primary = dict(inference["S_pooled"])
    numerator = sum(c * row["strict_errors"] for c, row in zip(PRIMARY_COEFFICIENTS, counts))
    supported = frozen and primary["p_two_sided"] <= .05 and numerator >= PRIMARY_THRESHOLD
    valid = {n: inference["valid_" + n] for n in GUARD_METRICS} if common is not None else None
    primary_valid = bool(valid and valid["S_pooled"]["estimate"] > 0 and valid["S_pooled"]["estimate"] >= .03
                         and valid["S_pooled"]["p_two_sided"] <= .05)
    bound_pass = result["invalid_and_missing_outcome_bounds"]["S_pooled"]["lower"] > 0
    semantic = bool(supported and primary_valid and bound_pass)
    primary.update(integer_numerator=numerator, denominator=PRIMARY_DENOMINATOR, practical_integer_threshold=PRIMARY_THRESHOLD,
                   statistical_pass=frozen and primary["p_two_sided"] <= .05, positive_practical_pass=numerator >= PRIMARY_THRESHOLD,
                   primary_gate_pass=supported, semantic_primary_gate_pass=semantic,
                   semantic_gate_components={"common_valid_positive_practical_statistical": primary_valid,
                                             "coefficient_lower_bound_positive": bound_pass})
    result.update(primary={"S_pooled": primary}, primary_supported=supported, semantic_primary_supported=semantic)
    strict_checks = readiness_interval_checks(inference)
    common_checks = readiness_interval_checks(valid) if valid is not None else None
    bound_checks = readiness_bound_checks(result["invalid_and_missing_outcome_bounds"])
    ready = bool(semantic and all(strict_checks.values()) and common_checks is not None and all(common_checks.values()) and all(bound_checks.values()))
    result["candidate_readiness"] = {"evaluated": frozen, "passed": ready, "requires_semantic_primary_support": True,
                                     "strict_checks": strict_checks, "common_valid_checks": common_checks,
                                     "coefficient_bound_checks": bound_checks, "margin": .01,
                                     "interpretation": "Conjunctive readiness for this one candidate; a failed screen can be inconclusive, not proof of harm or no primary effect."}
    result["common_valid_sensitivity"] = {"defined": common is not None, "support": support,
                                           "primary": {"S_pooled": valid["S_pooled"]} if valid else None,
                                           "guard_metrics": {n: _descriptive(v) for n, v in valid.items() if n != "S_pooled"} if valid else None,
                                           "selection": "All twelve responses must be valid per retained unit; every question/debater stratum must retain support."}
    result["uniform_invalid_as_correct_sensitivity"] = {"primary": {"S_pooled": inference["recoded_S_pooled"]},
                                                         "guard_metrics": {n: _descriptive(inference["recoded_" + n]) for n in GUARD_METRICS if n != "S_pooled"},
                                                         "kind": "uniform_recoding_sensitivity_not_bounds"}
    result["descriptive_effects"] = {n: _descriptive(inference[n]) for n in METRICS if n != "S_pooled" and not n.startswith("arm_")}
    for i, row in enumerate(counts):
        row.update(error_rate=row["strict_errors"] / 656, ci95=inference[f"arm_{i}"]["ci95"])
    result["question_values"] = [{"question_id": q, "world": worlds[q], **{n: float(v[q]) for n, v in questions.items()}} for q in sorted(worlds)]
    result["subgroups_descriptive"] = {field: {str(value): {
        "arm_counts": arm_counts({uid: u for uid, u in unit_by_id.items() if u[field] == value}, scored),
        "paired_transitions": paired_transitions({uid: u for uid, u in unit_by_id.items() if u[field] == value}, scored)}
        for value in sorted({u[field] for u in units})} for field in ("world", "debater", "side")}
    if not frozen:
        result["limitations"].append("Diagnostic bootstrap settings differ from the freeze; primary support and readiness are disabled.")
    return result, sorted(scored_rows, key=lambda r: r["cell_id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    inputs, results, out = args.inputs.resolve(), args.results.resolve(), args.out.resolve()
    _require(not out.is_relative_to(inputs) and not out.is_relative_to(ROOT), "Private output must be outside Git and frozen inputs")
    paths = [out / "phase5_analysis.json", out / "scored_cells.jsonl"]
    _require(results not in paths, "Analysis output would overwrite results")
    manifest_hash, result_hash = base._sha(inputs / "manifest.json"), base._sha(results)
    calls, units, manifest = load_inputs(inputs)
    result, scored = analyze_rows(calls, units, base._read_jsonl(results))
    _require(base._sha(inputs / "manifest.json") == manifest_hash and base._sha(results) == result_hash
             and all(base._sha(inputs / n) == manifest["outputs"][n]["sha256"] for n in INPUT_FILES), "Inputs/results changed during analysis")
    result["provenance"] = {"prepared_manifest_sha256": manifest_hash, "protocol_sha256": PROTOCOL_SHA256,
                            "results_sha256": result_hash, "analysis_script_sha256": base._sha(Path(__file__)),
                            "reused_phase4_analysis_sha256": base._sha(Path(base.__file__)), "python": sys.version,
                            "reviewed_sources": manifest["provenance"],
                            "input_hashes": {n: manifest["outputs"][n]["sha256"] for n in INPUT_FILES}}
    out.mkdir(parents=True, exist_ok=True)
    base._atomic_write(paths[1], "".join(json.dumps(r, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n" for r in scored))
    base._atomic_write(paths[0], json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "completed": result["coverage"]["completed"],
                      "primary_supported": result["primary_supported"], "semantic_primary_supported": result["semantic_primary_supported"],
                      "candidate_readiness": result["candidate_readiness"]["passed"], "analysis_path": str(paths[0]), "analysis_sha256": base._sha(paths[0])}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnalysisError, OSError, json.JSONDecodeError) as exc:
        print(f"Phase5 analysis failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
