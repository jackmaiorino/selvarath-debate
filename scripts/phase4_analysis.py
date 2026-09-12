"""Frozen, offline Phase 4A analysis. This module never dispatches model calls.

CLI: python scripts/phase4_analysis.py --inputs DIR --results FILE --out DIR
Only completed result rows belong in FILE. Administrative failures stay in the
runner's attempt journal and appear here as absent planned cells.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
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
from rejudge.parsers import PARSER_VERSION, parse_both

QWEN = "Qwen/Qwen3.8-2.4T-A95B"
LLAMA = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
JUDGES = (QWEN, LLAMA)
DEBATERS = ("Qwen/Qwen3.7-Plus", LLAMA)
ARMS = ("empty", "qwen_history", "llama_history")
CELLS = tuple((judge, arm) for judge in JUDGES for arm in ARMS)
WORLD_COUNTS = {"carath_norn": 27, "selvarath": 28, "vethun_sarak": 27}
BOOTSTRAP_REPLICATES = 10000
BOOTSTRAP_SEED = 2026091202
PRIMARY_COEFFICIENTS = {
    "D_recipient": (-2, 1, 1, 2, -1, -1),
    "D_source": (0, 1, -1, 0, 1, -1),
}
METRICS = {
    **{f"arm_{i}": (tuple(int(i == j) for j in range(6)), 1) for i in range(6)},
    **{name: (coefs, 2) for name, coefs in PRIMARY_COEFFICIENTS.items()},
    "Q_qwen_vs_empty": ((-1, 1, 0, 0, 0, 0), 1),
    "Q_llama_vs_empty": ((-1, 0, 1, 0, 0, 0), 1),
    "L_qwen_vs_empty": ((0, 0, 0, -1, 1, 0), 1),
    "L_llama_vs_empty": ((0, 0, 0, -1, 0, 1), 1),
    "recipient_source_interaction": ((0, 1, -1, 0, -1, 1), 1),
}


class AnalysisError(ValueError):
    """The completed-row or frozen-panel contract was not satisfied."""


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                raise AnalysisError(f"Blank JSONL row at {path}:{number}")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AnalysisError(f"Invalid JSONL at {path}:{number}") from exc
            if not isinstance(row, dict):
                raise AnalysisError(f"Non-object JSONL row at {path}:{number}")
            rows.append(row)
    return rows


def _unique(rows: list[dict], field: str) -> dict[str, dict]:
    indexed = {}
    for row in rows:
        key = row.get(field)
        if not isinstance(key, str) or not key or key in indexed:
            raise AnalysisError(f"Missing or duplicate {field}: {key!r}")
        indexed[key] = row
    return indexed


def validate_panel(calls: list[dict], units: list[dict]) -> tuple[dict, dict]:
    """Validate the actual fixed 656-unit design, including every mirrored stratum."""
    call_by_id = _unique(calls, "cell_id")
    unit_by_id = _unique(units, "unit_id")
    if len(units) != 656 or len(calls) != 3936:
        raise AnalysisError("Frozen Phase 4A requires 656 units and 3936 calls")
    strata = defaultdict(list)
    worlds = {}
    identities = set()
    for unit in units:
        qid, world, debater = (unit.get(k) for k in ("question_id", "world", "debater"))
        if not isinstance(qid, str) or not qid or world not in WORLD_COUNTS or debater not in DEBATERS:
            raise AnalysisError("Invalid question/world/debater in unit panel")
        if qid in worlds and worlds[qid] != world:
            raise AnalysisError("A question cannot belong to multiple worlds")
        worlds[qid] = world
        if type(unit.get("side")) is not int or unit["side"] not in (0, 1):
            raise AnalysisError("Unit side must be integer 0 or 1")
        if type(unit.get("transcript_index")) is not int or unit["transcript_index"] not in (0, 1, 2):
            raise AnalysisError("Invalid source transcript index")
        if unit.get("correct_position") not in ("A", "B"):
            raise AnalysisError("Unit has no frozen A/B answer key")
        identity = (qid, debater, unit["transcript_index"], unit["side"])
        if identity in identities:
            raise AnalysisError("Duplicate semantic unit identity")
        identities.add(identity)
        strata[(qid, debater)].append(unit)
    if len(worlds) != 82 or dict(Counter(worlds.values())) != WORLD_COUNTS:
        raise AnalysisError("Frozen question/world allocation does not match")
    for qid in worlds:
        for debater in DEBATERS:
            stratum = strata[(qid, debater)]
            pairs = defaultdict(list)
            for unit in stratum:
                pairs[unit["transcript_index"]].append(unit)
            if len(stratum) != 4 or len(pairs) != 2:
                raise AnalysisError("Each question/debater requires two mirrored transcript pairs")
            for pair in pairs.values():
                if {u["side"] for u in pair} != {0, 1} or {u["correct_position"] for u in pair} != {"A", "B"}:
                    raise AnalysisError("Incomplete or inconsistent mirrored answer key")
    observed = defaultdict(set)
    for call in calls:
        uid = call.get("unit_id")
        cell = (call.get("judge"), call.get("arm"))
        if uid not in unit_by_id or cell not in CELLS or cell in observed[uid]:
            raise AnalysisError("Unknown or duplicated unit/recipient/arm in calls")
        observed[uid].add(cell)
    if set(observed) != set(unit_by_id) or any(value != set(CELLS) for value in observed.values()):
        raise AnalysisError("Every unit must have exactly the same six planned arms")
    return call_by_id, unit_by_id


def load_inputs(inputs: Path) -> tuple[list[dict], list[dict], dict]:
    """Read only the prepared manifest, call plan and private answer-key file."""
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "phase4a_prepared_panel_v1":
        raise AnalysisError("Unsupported prepared-panel manifest")
    for name in ("calls.jsonl", "units_private.jsonl", "packets.jsonl"):
        path = inputs / name
        record = manifest.get("outputs", {}).get(name, {})
        if path.stat().st_size != record.get("bytes") or _sha(path) != record.get("sha256"):
            raise AnalysisError(f"Prepared input hash mismatch: {name}")
    calls, units = _read_jsonl(inputs / "calls.jsonl"), _read_jsonl(inputs / "units_private.jsonl")
    validate_panel(calls, units)
    return calls, units, manifest


def score_rows(calls: dict, units: dict, rows: list[dict]) -> tuple[dict, list[dict], int, str]:
    """Keep first completed response; exact duplicate rows are idempotent only."""
    completed, scored, duplicates = {}, [], 0
    total_cost = Decimal(0)
    for row in rows:
        cell_id = row.get("cell_id")
        if cell_id not in calls:
            raise AnalysisError(f"Unplanned result cell: {cell_id!r}")
        if cell_id in completed:
            if row != completed[cell_id]:
                raise AnalysisError(f"Conflicting completed duplicate: {cell_id}")
            duplicates += 1
            continue
        call = calls[cell_id]
        if any(row.get(field) != call[field] for field in ("unit_id", "judge", "arm")):
            raise AnalysisError(f"Result identity disagrees with call: {cell_id}")
        if row.get("configuration_valid") is False:
            raise AnalysisError(f"Invalid provider configuration: {cell_id}")
        if "returned_model_id" in row and row["returned_model_id"] != call["judge"]:
            raise AnalysisError(f"Returned model disagrees with planned endpoint: {cell_id}")
        if "messages_sha256" in row and row["messages_sha256"] != call.get("messages_sha256"):
            raise AnalysisError(f"Result messages hash disagrees with prepared call: {cell_id}")
        if not isinstance(row.get("raw_verdict_text"), str):
            raise AnalysisError(f"Completed result must contain raw text, including empty text: {cell_id}")
        if any(not isinstance(row.get(k), str) or not row[k] for k in ("completed_at", "attempt_id")):
            raise AnalysisError(f"Completed result lacks completion/attempt identity: {cell_id}")
        usage = row.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise AnalysisError(f"Invalid completed usage: {cell_id}")
        usage = usage or {}
        token_counts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens")]
        if any(value is not None and (type(value) is not int or value < 0) for value in token_counts):
            raise AnalysisError(f"Invalid completed usage: {cell_id}")
        try:
            cost = Decimal(str(row["cost_usd"]))
            uncertain = Decimal(str(row.get("uncertain_usd", 0)))
        except (KeyError, InvalidOperation) as exc:
            raise AnalysisError(f"Invalid completed cost: {cell_id}") from exc
        if not cost.is_finite() or cost < 0 or not uncertain.is_finite() or uncertain < 0:
            raise AnalysisError(f"Nonfinite or negative completed cost: {cell_id}")
        if any(value is None for value in token_counts) and uncertain <= 0:
            raise AnalysisError(f"Completed response with missing usage needs uncertain exposure: {cell_id}")
        unit = units[call["unit_id"]]
        strict = parse_both(row["raw_verdict_text"])["strict"]
        valid = strict.get("parse_ok") is True and strict.get("verdict") in ("A", "B")
        scored.append({
            "cell_id": cell_id, "unit_id": call["unit_id"], "judge": call["judge"], "arm": call["arm"],
            **{key: unit[key] for key in ("question_id", "world", "debater", "transcript_index", "side", "correct_position")},
            "valid": valid, "strict_error": int(not valid or strict["verdict"] != unit["correct_position"]),
            "strict_verdict": strict, "attempt_id": row["attempt_id"], "completed_at": row["completed_at"],
            "usage_complete": all(value is not None for value in token_counts), "uncertain_usd": str(uncertain),
        })
        completed[cell_id] = row
        total_cost += cost
    return {(r["unit_id"], r["judge"], r["arm"]): r for r in scored}, scored, duplicates, str(total_cost)


def type7(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered or not 0 <= probability <= 1:
        raise AnalysisError("Invalid percentile inputs")
    position = (len(ordered) - 1) * probability
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def sign_tail_p(draws: list[float]) -> float:
    b = len(draws)
    if not b:
        raise AnalysisError("Bootstrap needs at least one draw")
    return min(1.0, 2 * min((1 + sum(x <= 0 for x in draws)) / (b + 1),
                           (1 + sum(x >= 0 for x in draws)) / (b + 1)))


def holm(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values, key=lambda name: (p_values[name], name))
    adjusted, previous = {}, 0.0
    for i, name in enumerate(ordered):
        previous = max(previous, min(1.0, (len(ordered) - i) * p_values[name]))
        adjusted[name] = previous
    return adjusted


def bootstrap_joint(question_values: dict[str, dict[str, Fraction]], worlds: dict[str, str],
                    *, replicates: int = BOOTSTRAP_REPLICATES, seed: int = BOOTSTRAP_SEED) -> dict:
    """Joint stratified MT draws. Integer scaling preserves exact zero-tail ties."""
    if type(replicates) is not int or replicates <= 0:
        raise AnalysisError("Bootstrap replicate count must be positive")
    qids = sorted(worlds)
    if not qids or any(set(values) != set(qids) for values in question_values.values()):
        raise AnalysisError("Bootstrap metrics must share the complete question domain")
    by_world = [[i for i, qid in enumerate(qids) if worlds[qid] == world] for world in sorted(set(worlds.values()))]
    vectors, denominators = {}, {}
    for name, values in question_values.items():
        fractions = [Fraction(values[qid]) for qid in qids]
        scale = math.lcm(*(x.denominator for x in fractions))
        vectors[name] = [int(x * scale) for x in fractions]
        denominators[name] = scale * len(qids)
    draws = {name: [] for name in vectors}
    rng = random.Random(seed)
    for _ in range(replicates):
        selected = [rng.choice(group) for group in by_world for _ in group]
        for name, vector in vectors.items():
            draws[name].append(sum(vector[i] for i in selected) / denominators[name])
    return {
        name: {"estimate": float(sum(question_values[name].values(), Fraction()) / len(qids)),
               "ci95": [type7(values, .025), type7(values, .975)],
               "ci97_5": [type7(values, .0125), type7(values, .9875)],
               "p_two_sided": sign_tail_p(values)}
        for name, values in draws.items()
    }


def _question_means(units: dict, scored: dict, *, common_valid: bool = False,
                    invalid_as_correct: bool = False) -> tuple[dict | None, dict]:
    strata = defaultdict(list)
    retained = []
    for uid, unit in units.items():
        strata[(unit["question_id"], unit["debater"])].append(uid)
        if not common_valid or all(scored[(uid, *cell)]["valid"] for cell in CELLS):
            retained.append(uid)
    kept = set(retained)
    absent = [list(key) for key, ids in sorted(strata.items()) if not any(uid in kept for uid in ids)]
    support = {"retained_units": len(kept), "dropped_units": len(units) - len(kept),
               "empty_question_debater_strata": absent}
    if absent:
        return None, support
    per_question = defaultdict(list)
    for (qid, _), ids in sorted(strata.items()):
        ids = [uid for uid in ids if uid in kept]
        means = []
        for cell in CELLS:
            errors = [0 if invalid_as_correct and not scored[(uid, *cell)]["valid"]
                      else scored[(uid, *cell)]["strict_error"] for uid in ids]
            means.append(Fraction(sum(errors), len(ids)))
        per_question[qid].append(means)
    return {qid: [sum((m[i] for m in debater_means), Fraction()) / 2 for i in range(6)]
            for qid, debater_means in per_question.items()}, support


def _metric_questions(means: dict, metrics: dict = METRICS) -> dict:
    return {name: {qid: sum((c * v for c, v in zip(coefficients, values)), Fraction()) / divisor
                   for qid, values in means.items()}
            for name, (coefficients, divisor) in metrics.items()}


def contrast_bounds(units: dict, scored: dict, *, invalid_unknown: bool) -> dict:
    """Missing cells are unknown; optionally also vary completed invalid errors."""
    result = {}
    for name, coefficients in PRIMARY_COEFFICIENTS.items():
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
        denominator = 2 * len(units)
        result[name] = {"lower_numerator": lower, "upper_numerator": upper, "denominator": denominator,
                        "lower": lower / denominator, "upper": upper / denominator,
                        "kind": "observed_panel_outcome_bounds_not_confidence_intervals"}
    return result


def _coverage(units: dict, scored: dict) -> list[dict]:
    rows = []
    for judge, arm in CELLS:
        observed = [scored[(uid, judge, arm)] for uid in units if (uid, judge, arm) in scored]
        errors = sum(r["strict_error"] for r in observed)
        invalids = sum(not r["valid"] for r in observed)
        rows.append({"judge": judge, "arm": arm, "expected": len(units), "completed": len(observed),
                     "administrative_missing": len(units) - len(observed), "correct": len(observed) - errors,
                     "wrong_valid": errors - invalids, "completed_invalid": invalids, "strict_errors": errors,
                     "observed_error_rate": errors / len(observed) if observed else None,
                     "full_panel_error_bounds": [errors / len(units), (errors + len(units) - len(observed)) / len(units)]})
    return rows


def _primary_summary(inference: dict, *, prefix: str = "") -> dict:
    family = {name: inference[prefix + name]["p_two_sided"] for name in PRIMARY_COEFFICIENTS}
    adjusted = holm(family)
    return {name: {**inference[prefix + name], "p_holm": adjusted[name]} for name in family}


def analyze_rows(calls: list[dict], units: list[dict], result_rows: list[dict],
                 *, bootstrap_replicates: int = BOOTSTRAP_REPLICATES,
                 bootstrap_seed: int = BOOTSTRAP_SEED) -> tuple[dict, list[dict]]:
    """Return (JSON-serializable analysis, scored rows), without writing files.

    Nonfrozen bootstrap parameters support diagnostic tests only and suppress
    formal gates. The CLI always uses the frozen parameters.
    """
    call_by_id, unit_by_id = validate_panel(calls, units)
    scored, scored_rows, duplicates, completed_cost = score_rows(call_by_id, unit_by_id, result_rows)
    missing = sorted(set(call_by_id) - {r["cell_id"] for r in scored_rows})
    complete = not missing
    frozen = bootstrap_replicates == BOOTSTRAP_REPLICATES and bootstrap_seed == BOOTSTRAP_SEED
    coverage = _coverage(unit_by_id, scored)
    result = {
        "schema_version": "phase4a_analysis_v1", "status": "complete" if complete else "incomplete",
        "parser_version": PARSER_VERSION, "formal_gates_evaluated": complete and frozen,
        "bootstrap": {"replicates": bootstrap_replicates, "seed": bootstrap_seed,
                      "prng": "CPython random.Random Mersenne Twister", "stratification": WORLD_COUNTS,
                      "p_value": "finite-corrected two-sided sign-tail", "percentile_type": 7},
        "coverage": {"planned": len(calls), "completed": len(scored), "administrative_missing": len(missing),
                     "missing_cell_ids": missing, "identical_duplicate_rows_ignored": duplicates,
                     "completed_invalid": sum(not r["valid"] for r in scored_rows)},
        "completed_response_cost_usd": completed_cost,
        "completed_response_uncertain_usd": str(sum((Decimal(r["uncertain_usd"]) for r in scored_rows), Decimal())),
        "completed_responses_missing_usage": sum(not r["usage_complete"] for r in scored_rows),
        "arm_counts": coverage,
        "primary": None, "descriptive_effects": None, "common_valid_sensitivity": None,
        "uniform_invalid_as_correct_sensitivity": None,
        "strict_missing_outcome_bounds": contrast_bounds(unit_by_id, scored, invalid_unknown=False),
        "invalid_and_missing_outcome_bounds": contrast_bounds(unit_by_id, scored, invalid_unknown=True),
        "stage_4b_semantic_entry_eligible": False,
        "limitations": [
            "Fixed two endpoints, selected 82 questions, and three authored worlds; no model-population inference or equivalence claim.",
            "Captured-history readout intervention; source packages combine query content, quantity, order, stopping and feedback.",
            "Question-cluster intervals are conditional on fixed worlds and endpoints, not exact randomization intervals.",
            "Cost covers first completed responses only; the runner attempt journal governs retries, uncertain deliveries and total cap accounting.",
            "Query/reply/block exposure and historical-versus-fresh own-history comparisons require a separate source-trace join and are not computed here.",
        ],
    }
    if not complete:
        result["limitations"].append("Administrative missingness remains: no formal tests, complete-panel point estimates or continuation gates are evaluated.")
        return result, sorted(scored_rows, key=lambda row: row["cell_id"])

    strict_means, _ = _question_means(unit_by_id, scored)
    common_means, support = _question_means(unit_by_id, scored, common_valid=True)
    recoded_means, _ = _question_means(unit_by_id, scored, invalid_as_correct=True)
    questions = _metric_questions(strict_means)
    if common_means is not None:
        questions.update({"valid_" + name: value for name, value in _metric_questions(common_means, {n: METRICS[n] for n in PRIMARY_COEFFICIENTS}).items()})
    questions.update({"recoded_" + name: value for name, value in _metric_questions(recoded_means, {n: METRICS[n] for n in PRIMARY_COEFFICIENTS}).items()})
    worlds = {unit["question_id"]: unit["world"] for unit in units}
    inference = bootstrap_joint(questions, worlds, replicates=bootstrap_replicates, seed=bootstrap_seed)
    primary = _primary_summary(inference)
    valid_primary = _primary_summary(inference, prefix="valid_") if common_means is not None else None
    result["common_valid_sensitivity"] = {"defined": common_means is not None, "support": support, "primary": valid_primary}
    result["uniform_invalid_as_correct_sensitivity"] = {"primary": _primary_summary(inference, prefix="recoded_"),
                                                        "kind": "uniform_recoding_sensitivity_not_bounds"}
    counts = [row["strict_errors"] for row in coverage]
    for name, item in primary.items():
        numerator = sum(c * count for c, count in zip(PRIMARY_COEFFICIENTS[name], counts))
        item.update({"integer_numerator": numerator, "denominator": 1312,
                     "statistical_pass": frozen and item["p_holm"] <= .05,
                     "practical_pass": abs(numerator) >= 40,
                     "decision_gate_pass": frozen and item["p_holm"] <= .05 and abs(numerator) >= 40})
        valid = valid_primary[name] if valid_primary else None
        bounds = result["invalid_and_missing_outcome_bounds"][name]
        bound_direction = bounds["lower"] > 0 if numerator > 0 else bounds["upper"] < 0
        valid_pass = bool(valid and valid["estimate"] * numerator > 0 and abs(valid["estimate"]) >= .03 and valid["p_holm"] <= .05)
        item["semantic_followup_gate_pass"] = bool(item["decision_gate_pass"] and valid_pass and bound_direction)
        item["semantic_gate_components"] = {"common_valid_same_direction_practical_statistical": valid_pass,
                                             "invalid_bounds_exclude_zero_in_primary_direction": bound_direction}
    result["primary"] = primary
    result["stage_4b_semantic_entry_eligible"] = any(value["semantic_followup_gate_pass"] for value in primary.values())
    for i, row in enumerate(coverage):
        row.update({"error_rate": row["strict_errors"] / 656, "ci95": inference[f"arm_{i}"]["ci95"]})
    result["descriptive_effects"] = {name: {"estimate": inference[name]["estimate"], "ci95": inference[name]["ci95"]}
                                     for name in METRICS if not name.startswith("arm_") and name not in PRIMARY_COEFFICIENTS}
    result["question_values"] = [{"question_id": qid, "world": worlds[qid],
                                   **{name: float(values[qid]) for name, values in questions.items()}}
                                  for qid in sorted(worlds)]
    result["subgroups_descriptive"] = {
        field: {str(value): _coverage({uid: unit for uid, unit in unit_by_id.items() if unit[field] == value}, scored)
                for value in sorted({unit[field] for unit in units})}
        for field in ("world", "debater", "side")
    }
    if not frozen:
        result["limitations"].append("Nonfrozen diagnostic bootstrap settings: all formal gates are disabled.")
    return result, sorted(scored_rows, key=lambda row: row["cell_id"])


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _message_sha(messages: list[dict]) -> str:
    raw = json.dumps(messages, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def source_exposure(source: dict) -> dict:
    """Phase 3 consumed-exchange definitions, without parsing human feedback text."""
    exchanges = source.get("exchanges")
    if not isinstance(exchanges, list) or type(source.get("queries_used")) is not int or source["queries_used"] != len(exchanges):
        raise AnalysisError("Source query count disagrees with consumed exchanges")
    if not 0 <= len(exchanges) <= source["budget"]:
        raise AnalysisError("Source exchanges exceed the recorded budget")
    blocked = sum(e.get("blocked") is True for e in exchanges)
    answered = [e for e in exchanges if e.get("raw_oracle_reply") is not None]
    if any((e.get("blocked") is True) == (e.get("raw_oracle_reply") is not None) for e in exchanges):
        raise AnalysisError("Consumed source exchange is neither exclusively blocked nor answered")
    replies = Counter(e.get("normalized") for e in answered)
    if set(replies) - {"YES", "NO", "NOT ADDRESSED"}:
        raise AnalysisError("Unsupported source oracle normalization")
    claims = [re.sub(r"\s+", " ", e.get("extracted_claim", "").casefold()).strip().rstrip(".?!") for e in exchanges]
    events = source.get("gate_events", [])
    if not isinstance(events, list) or any(e.get("action") not in {"allow", "block", "retry"} for e in events):
        raise AnalysisError("Unsupported source gate event metadata")
    retries = sum(e["action"] == "retry" for e in events)
    if any(e.get("slot_consumed") is not False for e in events if e["action"] == "retry"):
        raise AnalysisError("A source free retry unexpectedly consumed a slot")
    if len(events) != len(exchanges) + retries:
        raise AnalysisError("Source gate attempts do not match consumed slots plus free retries")
    return {"consumed_slots": len(exchanges), "blocked_slots": blocked, "oracle_replies": len(answered),
            "yes": replies["YES"], "no": replies["NO"], "not_addressed": replies["NOT ADDRESSED"],
            "gate_attempts": len(events), "free_retry_events": retries,
            "repeated_exact_claims": len(claims) - len(set(claims)),
            "exposure": "zero_replies" if not answered else "mixed_blocked_and_answered" if blocked else "answered_no_blocks"}


def load_source_packets(inputs: Path, manifest: dict, calls: list[dict], units: list[dict]) -> tuple[list[dict], dict]:
    """Hash-bound offline join of each prepared packet to its exact Phase 3 row."""
    sources = [(Path(path), digest) for path, digest in manifest.get("source_hashes", {}).items()
               if Path(path).name == "main_results.jsonl"]
    if len(sources) != 1:
        raise AnalysisError("Prepared manifest must bind exactly one main_results.jsonl")
    source_path, expected_digest = sources[0]
    if _sha(source_path) != expected_digest:
        raise AnalysisError("Historical source result hash mismatch")
    packet_digest = manifest["outputs"]["packets.jsonl"]["sha256"]
    if _sha(inputs / "packets.jsonl") != packet_digest:
        raise AnalysisError("Prepared packet hash mismatch during source join")
    unit_by_id = _unique(units, "unit_id")
    call_packets = defaultdict(list)
    for call in calls:
        call_packets[call["packet_id"]].append(call)
    requested = {}
    seen_packets = set()
    with (inputs / "packets.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            packet = json.loads(line)
            packet_id = packet["packet_id"]
            if packet_id in seen_packets or packet_id not in call_packets:
                raise AnalysisError("Duplicate or unplanned source packet")
            seen_packets.add(packet_id)
            planned = call_packets[packet_id]
            if len(planned) != 2 or {c["judge"] for c in planned} != set(JUDGES):
                raise AnalysisError("Source packet must be shared by the two recipients")
            if len({(c["unit_id"], c["arm"], c["messages_sha256"]) for c in planned}) != 1:
                raise AnalysisError("Source packet recipients do not share identical context")
            call = planned[0]
            if _message_sha(packet["messages"]) != packet["messages_sha256"] or packet["messages_sha256"] != call["messages_sha256"]:
                raise AnalysisError("Source packet message hash mismatch")
            key = packet.get("source_cell_key")
            if not isinstance(key, str) or not key or key in requested:
                raise AnalysisError("Missing or duplicate historical source cell")
            requested[key] = {"packet_id": packet_id, "source_cell_key": key, "arm": call["arm"],
                              "messages_sha256": call["messages_sha256"], **unit_by_id[call["unit_id"]]}
    if seen_packets != set(call_packets):
        raise AnalysisError("Prepared call references a missing source packet")
    joined, seen_sources = [], set()
    # Stream the 477 MB source archive, retaining only compact matched metadata.
    with source_path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            key = row.get("cell_key")
            if key not in requested:
                continue
            if key in seen_sources:
                raise AnalysisError("Duplicate historical source result")
            seen_sources.add(key)
            packet, source = requested[key], row["result"]
            expected_judge = LLAMA if packet["arm"] == "llama_history" else QWEN
            expected_condition = "b0" if packet["arm"] == "empty" else "sequential_b8"
            expected = {"question_id": packet["question_id"], "world": packet["world"],
                        "transcript_index": packet["transcript_index"], "judge_model": expected_judge,
                        "condition": expected_condition, "budget": 0 if expected_condition == "b0" else 8,
                        "replicate": packet["side"], "cell_key": key,
                        "position_a_is_correct": packet["correct_position"] == "A"}
            if any(source.get(field) != value for field, value in expected.items()):
                raise AnalysisError("Historical source identity or answer key disagrees with prepared unit")
            messages = source.get("judge_messages")
            if not isinstance(messages, list) or not messages or messages[-1] != {"role": "assistant", "content": source.get("raw_verdict_text")}:
                raise AnalysisError("Historical source final verdict boundary is invalid")
            if _message_sha(messages[:-1]) != packet["messages_sha256"]:
                raise AnalysisError("Prepared history differs from historical pre-verdict messages")
            strict = parse_both(source["raw_verdict_text"])["strict"]
            valid = strict.get("parse_ok") is True and strict.get("verdict") in {"A", "B"}
            error = int(not valid or strict["verdict"] != packet["correct_position"])
            stored = source.get("verdict_strict", {})
            if stored.get("parse_ok") != strict["parse_ok"] or stored.get("verdict") != strict["verdict"]:
                raise AnalysisError("Historical strict parser output does not reproduce")
            joined.append({**packet, "source_judge": expected_judge, "source_condition": expected_condition,
                           "old_valid": valid, "old_strict_error": error, **source_exposure(source)})
    if seen_sources != set(requested):
        raise AnalysisError("One or more prepared histories have no historical source row")
    if _sha(source_path) != expected_digest or _sha(inputs / "packets.jsonl") != packet_digest:
        raise AnalysisError("Source data changed during descriptive join")
    return sorted(joined, key=lambda row: row["packet_id"]), {
        "source_results_path": str(source_path), "source_results_sha256": expected_digest,
        "packets_sha256": packet_digest, "matched_packets": len(joined)}


def source_descriptives(source_packets: list[dict], scored_rows: list[dict]) -> dict:
    """Fixed-packet exposure and matched old/fresh own-history outcome counts."""
    fields = ("consumed_slots", "blocked_slots", "oracle_replies", "yes", "no", "not_addressed",
              "gate_attempts", "free_retry_events", "repeated_exact_claims")

    def exposure_totals(group):
        return {"packets": len(group), "source_judges": sorted({row["source_judge"] for row in group}),
                **{field: sum(row[field] for row in group) for field in fields},
                "any_blocked_packets": sum(row["blocked_slots"] > 0 for row in group),
                "zero_reply_packets": sum(row["oracle_replies"] == 0 for row in group),
                "packets_with_free_retry": sum(row["free_retry_events"] > 0 for row in group),
                "old_strict_errors": sum(row["old_strict_error"] for row in group),
                "old_completed_invalid": sum(not row["old_valid"] for row in group)}

    scored = {(row["unit_id"], row["judge"], row["arm"]): row for row in scored_rows}
    by_arm = {arm: [row for row in source_packets if row["arm"] == arm] for arm in ARMS}
    comparisons = []
    for judge, arm in ((QWEN, "qwen_history"), (LLAMA, "llama_history")):
        source = by_arm[arm]
        matched = [(row, scored[(row["unit_id"], judge, arm)]) for row in source if (row["unit_id"], judge, arm) in scored]
        old_errors = sum(old["old_strict_error"] for old, _ in matched)
        fresh_errors = sum(fresh["strict_error"] for _, fresh in matched)
        transitions = Counter((old["old_strict_error"], fresh["strict_error"]) for old, fresh in matched)
        item = {"judge": judge, "arm": arm, "planned": len(source), "matched": len(matched),
                "administrative_missing_fresh": len(source) - len(matched),
                "old_strict_errors_on_matched": old_errors, "fresh_strict_errors_on_matched": fresh_errors,
                "old_invalid_on_matched": sum(not old["old_valid"] for old, _ in matched),
                "fresh_invalid_on_matched": sum(not fresh["valid"] for _, fresh in matched),
                "old_correct_to_fresh_error": transitions[(0, 1)], "old_error_to_fresh_correct": transitions[(1, 0)],
                "both_correct": transitions[(0, 0)], "both_error": transitions[(1, 1)],
                "net_extra_fresh_errors": fresh_errors - old_errors,
                "fresh_minus_old_on_matched": (fresh_errors - old_errors) / len(matched) if matched else None,
                "complete_planned_comparison": len(matched) == len(source), "ci95": None,
                "interpretation": "Descriptive same-recipient saved-b8 versus fresh-own-history replay; calendar time, stochastic variation and fresh continuation differ. No randomized live-versus-replay causal claim."}
        if matched and len(matched) == len(source):
            by_question = defaultdict(list)
            worlds = {}
            for old, fresh in matched:
                by_question[old["question_id"]].append(fresh["strict_error"] - old["old_strict_error"])
                worlds[old["question_id"]] = old["world"]
            question_delta = {qid: Fraction(sum(values), len(values)) for qid, values in by_question.items()}
            summary = bootstrap_joint({"own_history_delta": question_delta}, worlds)["own_history_delta"]
            item["ci95"] = summary["ci95"]
            item["question_values"] = [{"question_id": qid, "world": worlds[qid], "delta": float(value)}
                                       for qid, value in sorted(question_delta.items())]
        comparisons.append(item)
    return {
        "status": "joined", "unit_of_exposure": "One captured packet, counted once rather than once per recipient; includes the fixed full selected source panel regardless of fresh completion.",
        "definitions": {"consumed_slots": "Length of exchanges, equal to queries_used; blocked slots count toward the budget.",
                        "oracle_replies": "Consumed exchanges with non-null raw_oracle_reply; normalized YES/NO/NOT ADDRESSED are reported separately.",
                        "free_retry_events": "gate_events action=retry; these consume no query slot and may occur even in packets with no blocked slots.",
                        "repeated_exact_claims": "Duplicate consumed claims after casefold, whitespace collapse, and trailing .?! removal; matches the saved Phase 3 audit definition."},
        "by_arm": {arm: exposure_totals(group) for arm, group in by_arm.items()},
        "subgroups": {field: {str(value): {arm: exposure_totals([row for row in group if row[field] == value])
                                            for arm, group in by_arm.items()}
                               for value in sorted({row[field] for row in source_packets})}
                      for field in ("world", "debater")},
        "own_history_old_vs_fresh": comparisons, "packet_metadata": source_packets,
        "limitations": ["Exposure is generated by the earlier donor policy and is not randomized; no causal mechanism or oracle-error rate is inferred from reply/block frequencies.",
                        "The empty packet is sourced from the archived Qwen b0 row. Identical baseline messages across recipients do not imply identical historical baseline verdicts.",
                        "Incomplete own-history comparisons describe only matched fresh completions and have no complete-panel interval.",
                        "Own-history confidence intervals are descriptive only; there are no added significance tests or continuation gates."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.resolve().is_relative_to(args.inputs.resolve()):
        raise AnalysisError("Analysis output must not alter the frozen prepared-input directory")
    output_paths = [args.out / "phase4_analysis.json", args.out / "scored_cells.jsonl"]
    if args.results.resolve() in [p.resolve() for p in output_paths]:
        raise AnalysisError("Analysis output must not overwrite source results")
    calls, units, manifest = load_inputs(args.inputs)
    result_digest = _sha(args.results)
    analysis, scored = analyze_rows(calls, units, _read_jsonl(args.results))
    source_packets, source_provenance = load_source_packets(args.inputs, manifest, calls, units)
    analysis["source_history_descriptive"] = {**source_descriptives(source_packets, scored), "provenance": source_provenance}
    analysis["limitations"] = [text for text in analysis["limitations"] if not text.startswith("Query/reply/block exposure")]
    if _sha(args.results) != result_digest:
        raise AnalysisError("Results changed during analysis; retry against a stable snapshot")
    analysis["provenance"] = {"prepared_manifest_sha256": _sha(args.inputs / "manifest.json"),
                              "prepared_protocol_sha256": manifest["phase4_protocol_sha256"],
                              "results_sha256": result_digest, "analysis_script_sha256": _sha(Path(__file__)),
                              "python": sys.version,
                              "input_hashes": {name: manifest["outputs"][name]["sha256"] for name in ("calls.jsonl", "units_private.jsonl", "packets.jsonl")}}
    args.out.mkdir(parents=True, exist_ok=True)
    _atomic_write(output_paths[1], "".join(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n" for row in scored))
    _atomic_write(output_paths[0], json.dumps(analysis, ensure_ascii=True, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": analysis["status"], "completed": analysis["coverage"]["completed"],
                      "administrative_missing": analysis["coverage"]["administrative_missing"],
                      "analysis_path": str(output_paths[0]), "analysis_sha256": _sha(output_paths[0]),
                      "stage_4b_semantic_entry_eligible": analysis["stage_4b_semantic_entry_eligible"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnalysisError, OSError, json.JSONDecodeError) as exc:
        print(f"Phase 4A analysis failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
