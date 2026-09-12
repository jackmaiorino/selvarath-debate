"""Offline Phase 4B consensus and blinded source review. No provider calls.

CLI: python scripts/phase4b_consensus.py --inputs DIR --results FILE --out DIR
     [--human-review FILE | --source-review FILE --review-amendment FILE]

Only a complete adjudication panel and the required review can release a repair
map. An explicit amendment may substitute AI source review for human review.
Release is not authorization for paid recipient evaluation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rejudge.phase4b_labels import LABELS, assess_response, call_seed, consensus, human_rank, messages_for
from scripts.phase4b_prepare import repair_coverage

MODELS = frozenset({"openai/gpt-oss-120b", "deepseek-ai/DeepSeek-V4-Flash-0731"})
DONORS = frozenset({"qwen_history", "llama_history"})
TOTAL_PACKETS = 1312
SOURCE_REVIEW_PROTOCOL_SHA256 = "f76a1ce7e10a9c961d983f31fa701882fb47111be57876587098c0f00ee10a12"
REQUIRED_FILES = frozenset({"manifest.json", "blind_claims.jsonl", "worlds_private.jsonl",
                            "claim_occurrences_private.jsonl", "packet_repair_map_private.jsonl",
                            "calls.jsonl", "claim_packets.jsonl"})


class ConsensusError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ConsensusError(message)


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ConsensusError("Nonstandard JSON constant: " + value)


def _json(text):
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, TypeError) as exc:
        raise ConsensusError("Invalid strict JSON") from exc


def _jsonl(path):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        _require(bool(line.strip()), f"Blank JSONL row {number} in {Path(path).name}")
        row = _json(line)
        _require(isinstance(row, dict), f"Non-object row {number} in {Path(path).name}")
        rows.append(row)
    return rows


def _index(rows, key):
    indexed = {}
    for row in rows:
        _require(isinstance(row, dict), "Expected object rows")
        value = row.get(key)
        _require(isinstance(value, str) and bool(value) and value not in indexed, f"Invalid or duplicate {key}")
        indexed[value] = row
    return indexed


def validate_panel(claims, worlds, calls, packets, occurrences, packet_maps, manifest):
    """Check exact claims, blinded messages, model settings, and the occurrence join."""
    claim_by_id, world_by_id = _index(claims, "claim_id"), _index(worlds, "world_sha256")
    call_by_id, packet_by_id = _index(calls, "cell_id"), _index(packets, "packet_id")
    maps = _index(packet_maps, "packet_id")
    _require(set(manifest.get("models", [])) == MODELS and len(manifest["models"]) == 2,
             "Adjudication requires the two frozen endpoints")
    _require(len(maps) == TOTAL_PACKETS, "Expected 1312 donor/unit packets")
    _require(len(claims) > 0 and len(packets) == len(claims) and len(calls) == 2 * len(claims),
             "Expected exactly two planned adjudications per claim")
    _require(manifest.get("distinct_claims") == len(claims) and manifest.get("adjudication_calls") == len(calls),
             "Adjudication manifest counts disagree with the panel")
    for world in worlds:
        _require(isinstance(world.get("world_document"), str) and bool(world["world_document"])
                 and _digest(world["world_document"]) == world["world_sha256"], "World bytes disagree with their hash")
    for claim in claims:
        _require(set(claim) == {"claim_id", "world_sha256", "exact_claim"}, "Blind claim schema mismatch")
        _require(claim["world_sha256"] in world_by_id and isinstance(claim["exact_claim"], str)
                 and bool(claim["exact_claim"].strip()), "Claim has no exact text or known world")
        _require(_digest(claim["world_sha256"] + "|" + claim["exact_claim"]) == claim["claim_id"],
                 "Claim identity does not match its world and exact text")
        packet = packet_by_id.get(claim["claim_id"])
        _require(packet is not None and packet.get("claim_id") == claim["claim_id"], "Missing blinded packet")
        expected = messages_for(world_by_id[claim["world_sha256"]]["world_document"],
                                claim["exact_claim"], manifest["oracle_contract"])
        _require(packet.get("messages") == expected and packet.get("messages_sha256") == _digest(_canonical(expected)),
                 "Blinded packet differs from the source-only prompt")
    cells = defaultdict(set)
    for call in calls:
        cid, judge = call.get("claim_id"), call.get("judge")
        _require(cid in claim_by_id and judge in MODELS and judge not in cells[cid], "Invalid or duplicate claim/model cell")
        _require(call["cell_id"] == cid + ":" + judge and call.get("packet_id") == cid,
                 "Call identity does not match the prepared claim")
        _require(call.get("messages_sha256") == packet_by_id[cid]["messages_sha256"], "Call messages hash mismatch")
        settings = manifest["model_settings"][judge]
        _require(all(call.get(key) == settings[key] for key in ("temperature", "top_p", "max_tokens", "reasoning_effort"))
                 and call.get("stream") is False and call.get("seed") == call_seed(cid, judge), "Call configuration mismatch")
        cells[cid].add(judge)
    _require(set(cells) == set(claim_by_id) and all(judges == MODELS for judges in cells.values()),
             "Every claim must have exactly both independent adjudicators")
    for packet in maps.values():
        _require(packet.get("arm") in DONORS and packet.get("world_sha256") in world_by_id
                 and isinstance(packet.get("question_id"), str) and bool(packet["question_id"]), "Invalid donor packet identity")
    seen_occurrences, observed_claims = set(), set()
    for occurrence in occurrences:
        cid, pid, exchange = (occurrence.get(key) for key in ("claim_id", "packet_id", "exchange_index"))
        _require(cid in claim_by_id and pid in maps and type(exchange) is int and 0 <= exchange < 8,
                 "Unknown claim/packet or invalid exchange in occurrence map")
        _require((pid, exchange) not in seen_occurrences, "Duplicate answered-exchange occurrence")
        seen_occurrences.add((pid, exchange))
        observed_claims.add(cid)
        packet = maps[pid]
        _require(occurrence.get("world_sha256") == claim_by_id[cid]["world_sha256"] == packet["world_sha256"]
                 and occurrence.get("question_id") == packet["question_id"]
                 and occurrence.get("donor_arm") == packet["arm"], "Occurrence provenance mismatch")
        _require(occurrence.get("original_label") in LABELS and isinstance(occurrence.get("spans"), list)
                 and bool(occurrence["spans"]), "Answered occurrence lacks its original label or repair spans")
    _require(observed_claims == set(claim_by_id), "Prepared claims and answered occurrences are not a complete join")
    return {"claims": claim_by_id, "worlds": world_by_id, "calls": call_by_id,
            "packets": packet_by_id, "occurrences": occurrences, "packet_maps": maps, "manifest": manifest}


def load_inputs(inputs):
    inputs = Path(inputs)
    manifest = _json((inputs / "adjudication_manifest.json").read_text(encoding="utf-8"))
    _require(manifest.get("schema_version") == "phase4b_blinded_adjudication_panel_v1", "Unsupported adjudication manifest")
    outputs = manifest.get("outputs", {})
    _require(REQUIRED_FILES <= set(outputs), "Adjudication manifest lacks required source bindings")
    for name, binding in outputs.items():
        _require(Path(name).name == name and isinstance(binding, dict), "Invalid prepared file binding")
        path = inputs / name
        _require(path.stat().st_size == binding.get("bytes") and _sha(path) == binding.get("sha256"),
                 "Prepared source changed: " + name)
    _require(_sha(ROOT / "rejudge/phase4b_labels.py") == manifest.get("prompt_script_sha256"),
             "Frozen adjudication prompt/parser source changed")
    return validate_panel(*[_jsonl(inputs / name) for name in
                            ("blind_claims.jsonl", "worlds_private.jsonl", "calls.jsonl", "claim_packets.jsonl",
                             "claim_occurrences_private.jsonl", "packet_repair_map_private.jsonl")], manifest)


def assess_completed(panel, rows):
    """Administrative absence is separate from a completed, excluded adjudication."""
    by_cell = _index(rows, "cell_id")
    _require(set(by_cell) <= set(panel["calls"]), "Unplanned completed adjudication")
    assessed = {}
    for cell, row in by_cell.items():
        call = panel["calls"][cell]
        _require(all(row.get(key) == call[key] for key in ("claim_id", "judge", "packet_id", "messages_sha256")),
                 "Completed adjudication identity mismatch: " + cell)
        _require(row.get("configuration_valid") is True and row.get("returned_model_id") == call["judge"],
                 "Completed adjudication used a different model/configuration: " + cell)
        _require(all(isinstance(row.get(key), str) and bool(row[key]) for key in ("completed_at", "attempt_id")),
                 "Completed adjudication lacks durable attempt identity")
        text = row.get("raw_adjudication_text", row.get("raw_verdict_text"))
        _require(isinstance(text, str), "Completed adjudication lacks raw text")
        if "raw_adjudication_text" in row and "raw_verdict_text" in row:
            _require(row["raw_adjudication_text"] == row["raw_verdict_text"], "Conflicting raw response fields")
        world_sha = panel["claims"][call["claim_id"]]["world_sha256"]
        assessed[cell] = assess_response(text, panel["worlds"][world_sha]["world_document"])
    return assessed


def _coverage(panel, labels):
    result = repair_coverage(panel["occurrences"], labels, total_packets=TOTAL_PACKETS)
    result["descriptive_counts_meet_minima"] = result.pop("minimum_feasibility_pass")
    result["total_donor_packets"] = TOTAL_PACKETS
    return result


def _human_template(panel, sample_ids, sample_id):
    return {"schema_version": "phase4b_human_review_v1", "sample_id": sample_id,
            "reviewer": {"name": "", "role": None, "provenance": "", "reviewed_at": ""},
            "entries": [{**{key: panel["claims"][cid][key] for key in ("claim_id", "world_sha256", "exact_claim")},
                         "decision": "pending", "comment": ""} for cid in sample_ids]}


def _human_decisions(document, template, *, reviewer_role="human"):
    if document is None:
        return {"status": "pending", "reviewed": 0, "pending": len(template["entries"]),
                "disagreed_claim_ids": [], "complete": False}
    _require(isinstance(document, dict) and set(document) == set(template), "Human review schema mismatch")
    _require(document["schema_version"] == template["schema_version"] and document["sample_id"] == template["sample_id"],
             "Human review is not bound to this predetermined sample and result snapshot")
    _require(isinstance(document["entries"], list), "Human review entries must be a list")
    entries = _index(document["entries"], "claim_id")
    expected = {row["claim_id"]: row for row in template["entries"]}
    _require(set(entries) == set(expected), "Human review entries must contain exactly the predetermined sample")
    reviewed, disagreed = 0, []
    for cid, row in entries.items():
        _require(set(row) == set(expected[cid]), "Human review entry schema mismatch")
        _require(all(row[key] == expected[cid][key] for key in ("world_sha256", "exact_claim")), "Human review claim identity changed")
        _require(isinstance(row["decision"], str) and row["decision"] in {"pending", "agree", "disagree"}
                 and isinstance(row["comment"], str), "Invalid human review decision")
        if row["decision"] != "pending":
            reviewed += 1
            if reviewer_role == "ai":
                _require(bool(row["comment"].strip()), "Every AI source-review decision needs its source-based reason")
            if row["decision"] == "disagree":
                _require(bool(row["comment"].strip()), "A human disagreement needs its source-based reason")
                disagreed.append(cid)
    reviewer = document["reviewer"]
    _require(isinstance(reviewer, dict) and set(reviewer) == set(template["reviewer"]), "Human reviewer provenance schema mismatch")
    if reviewer_role == "ai":
        _require(reviewer["role"] == "ai", "AI source review must identify the reviewer role as ai")
    if reviewed:
        _require(reviewer["role"] == reviewer_role and all(isinstance(reviewer[key], str) and bool(reviewer[key].strip())
                                                     for key in ("name", "provenance", "reviewed_at")),
                 "Reviewed entries require the explicit reviewer role, identity and provenance")
        try:
            date = datetime.fromisoformat(reviewer["reviewed_at"].replace("Z", "+00:00"))
            _require(date.tzinfo is not None, "Human review time needs an explicit timezone")
        except ValueError as exc:
            raise ConsensusError("Invalid human review timestamp") from exc
    complete = reviewed == len(expected) and bool(expected)
    return {"status": "complete" if complete else "pending", "reviewed": reviewed,
            "pending": len(expected) - reviewed, "disagreed_claim_ids": sorted(disagreed), "complete": complete}


def _source_review_amendment(document, *, input_manifest_sha256, results_sha256, sample_id):
    required = {"schema_version", "authorized", "authorization_quote", "protocol_sha256", "input_manifest_sha256",
                "results_sha256", "sample_id", "reviewer_role", "independent_human_validation", "recipient_evaluation_authorized"}
    _require(isinstance(document, dict) and required <= set(document)
             and set(document) <= required | {"recorded_at_utc", "note"}, "AI source-review amendment schema mismatch")
    _require(document["schema_version"] == "phase4b_ai_source_review_amendment_v1" and document["authorized"] is True,
             "AI source review requires explicit amendment authorization")
    _require(document["authorization_quote"] == "I approve an amendment of you doing that review instead",
             "AI source-review amendment lacks the approved substitution")
    _require(document["reviewer_role"] == "ai" and document["independent_human_validation"] is False
             and document["recipient_evaluation_authorized"] is False, "AI review cannot claim human validation or authorize recipient calls")
    expected = {"protocol_sha256": SOURCE_REVIEW_PROTOCOL_SHA256, "input_manifest_sha256": input_manifest_sha256,
                "results_sha256": results_sha256, "sample_id": sample_id}
    _require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) and document[key] == value
                 for key, value in expected.items()), "AI source-review amendment does not match the frozen protocol, inputs, results and sample")
    _require(_sha(ROOT / "rejudge/phase4_protocol_v1.json") == SOURCE_REVIEW_PROTOCOL_SHA256,
             "Original scientific protocol changed")
    if "recorded_at_utc" in document:
        try:
            date = datetime.fromisoformat(document["recorded_at_utc"].replace("Z", "+00:00"))
            _require(date.tzinfo is not None, "Amendment time needs an explicit timezone")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ConsensusError("Invalid amendment timestamp") from exc
    if "note" in document:
        _require(isinstance(document["note"], str), "Invalid amendment note")
    return document


def _breakdowns(panel, decisions, final_labels, human_disagreements, *, source_review=False):
    result = {}
    for dimension, packet_key, occurrence_key in (("world", "world", "world_sha256"),
                                                   ("donor", "arm", "donor_arm"),
                                                   ("question", "question_id", "question_id")):
        groups = defaultdict(lambda: {"packets": set(), "occurrences": [], "claims": set()})
        world_names = {p["world_sha256"]: p.get("world", p["world_sha256"]) for p in panel["packet_maps"].values()}
        for packet in panel["packet_maps"].values():
            key = packet.get(packet_key, packet["world_sha256"])
            groups[key]["packets"].add(packet["packet_id"])
        for occurrence in panel["occurrences"]:
            key = occurrence[occurrence_key]
            if dimension == "world":
                key = world_names[key]
            groups[key]["occurrences"].append(occurrence)
            groups[key]["claims"].add(occurrence["claim_id"])
        output = []
        for key, group in sorted(groups.items()):
            claims = group["claims"]
            administrative = {c for c in claims if decisions[c]["reason"] == "administrative_missing"}
            excluded = {c for c in claims if (not decisions[c]["eligible"] and c not in administrative)
                        or c in human_disagreements}
            changed = [o for o in group["occurrences"] if final_labels.get(o["claim_id"]) in LABELS
                       and final_labels[o["claim_id"]] != o["original_label"]]
            output.append({dimension: key, "donor_packets": len(group["packets"]), "distinct_claims": len(claims),
                           "answered_exchanges": len(group["occurrences"]),
                           "administratively_missing_claims": sum(decisions[c]["reason"] == "administrative_missing" for c in claims),
                           "adjudication_excluded_claims": sum(not decisions[c]["eligible"] and decisions[c]["reason"] != "administrative_missing" for c in claims),
                           ("source_review_disagreement_excluded_claims" if source_review else "human_disagreement_excluded_claims"):
                               len(claims & human_disagreements),
                           "ambiguous_or_excluded_claims": len(excluded),
                           "ambiguous_or_excluded_fraction": len(excluded) / len(claims) if claims else None,
                           "administratively_missing_fraction": len(administrative) / len(claims) if claims else None,
                           "changed_claims": len({o["claim_id"] for o in changed}),
                           "changed_packets": len({o["packet_id"] for o in changed})})
        result[dimension] = output
    return result


def analyze(panel, rows, *, input_manifest_sha256, results_sha256, human_review=None, source_review=None, review_amendment=None):
    source_mode = source_review is not None or review_amendment is not None
    _require(not source_mode or human_review is None, "Human review and amended AI source review cannot be combined")
    assessed = assess_completed(panel, rows)
    missing = sorted(set(panel["calls"]) - set(assessed))
    complete = not missing
    decisions, claim_rows = {}, []
    for cid, claim in sorted(panel["claims"].items()):
        judgments = {judge: assessed.get(cid + ":" + judge) for judge in sorted(MODELS)}
        if any(value is None for value in judgments.values()):
            decision = {"eligible": False, "label": None, "reason": "administrative_missing"}
        else:
            decision = consensus(*judgments.values())
        decisions[cid] = decision
        claim_rows.append({**claim, "adjudicators": judgments, "consensus": decision})
    preliminary_labels = {cid: decision["label"] if decision["eligible"] else "AMBIGUOUS"
                          for cid, decision in decisions.items() if decision["reason"] != "administrative_missing"}
    initial_coverage = _coverage(panel, preliminary_labels)
    sample_ids = sorted(initial_coverage["changed_claim_ids"],
                        key=lambda cid: (human_rank(panel["claims"][cid]["world_sha256"], panel["claims"][cid]["exact_claim"]), cid))[:20] if complete else []
    sample_id = _digest(_canonical({"input_manifest_sha256": input_manifest_sha256, "results_sha256": results_sha256,
                                    "claim_ids": sample_ids}))
    template = _human_template(panel, sample_ids, sample_id)
    if source_mode:
        _require(complete and len(sample_ids) == 20, "Amended AI source review requires the same complete 20-case sample")
        _source_review_amendment(review_amendment, input_manifest_sha256=input_manifest_sha256,
                                 results_sha256=results_sha256, sample_id=sample_id)
        template["schema_version"] = "phase4b_source_review_v1"
        template["reviewer"]["role"] = "ai"
    submitted_review = source_review if source_mode else human_review
    _require(complete or submitted_review is None, "Source review cannot be applied to incomplete adjudications")
    human = _human_decisions(submitted_review, template, reviewer_role="ai" if source_mode else "human") if complete else {
        "status": "unavailable_incomplete_adjudication", "reviewed": 0, "pending": 0, "disagreed_claim_ids": [], "complete": False}
    final_labels = {cid: "AMBIGUOUS" if cid in human["disagreed_claim_ids"] else label
                    for cid, label in preliminary_labels.items()}
    final_coverage = _coverage(panel, final_labels)
    released = complete and human["complete"] and final_coverage["descriptive_counts_meet_minima"]
    for row in claim_rows:
        cid = row["claim_id"]
        row["source_review_disagreed" if source_mode else "human_disagreed"] = cid in human["disagreed_claim_ids"]
        row["released_label"] = final_labels.get(cid) if released else None
    if not complete:
        status = "incomplete"
    elif not final_coverage["descriptive_counts_meet_minima"]:
        status = "insufficient_repair_coverage"
    elif not human["complete"]:
        status = "awaiting_source_review" if source_mode else "awaiting_human_review"
    else:
        status = "repair_labels_released"
    excluded = {judge: dict(Counter(value["reason"] for cell, value in assessed.items()
                                    if panel["calls"][cell]["judge"] == judge and not value["eligible"])) for judge in sorted(MODELS)}
    summary = {"schema_version": "phase4b_consensus_v1", "status": status,
               "administrative_coverage": {"complete": complete, "expected_calls": len(panel["calls"]),
                                           "completed_calls": len(assessed), "missing_calls": len(missing), "missing_cell_ids": missing},
               "consensus_label_counts": dict(Counter(d["label"] for d in decisions.values() if d["eligible"])),
               "consensus_exclusion_reasons": dict(Counter(d["reason"] for d in decisions.values() if not d["eligible"])),
               "adjudicator_exclusion_reasons": excluded, "coverage_before_human_review": initial_coverage,
               "coverage_after_human_exclusions": final_coverage,
               "pre_human_feasibility_pass": complete and initial_coverage["descriptive_counts_meet_minima"],
               "human_review": {**human, "sample_id": sample_id, "sample_claim_ids": sample_ids,
                                "selection": "First up to20 changed claims by the frozen human_rank; exclusions never trigger replacements."},
               "minimum_feasibility_and_human_review_pass": released, "repair_labels_released": released,
               "recipient_evaluation_authorized": False,
               "exclusions_and_coverage_by": _breakdowns(panel, decisions, final_labels, set(human["disagreed_claim_ids"]), source_review=source_mode),
               "provenance": {"input_manifest_sha256": input_manifest_sha256, "results_sha256": results_sha256},
               "limitations": ["Exact quotations establish source-reference integrity, not semantic correctness.",
                               "Coverage thresholds are necessary feasibility checks, not power guarantees.",
                               "Human role/provenance is an explicit submitted attestation, not software proof of a person's identity.",
                               "Changed claims may affect several questions and donors; subgroup claim counts are not additive.",
                               "This file authorizes no provider calls; any recipient evaluation requires its separate approval."]}
    if source_mode:
        for previous, current in (("coverage_before_human_review", "coverage_before_source_review"),
                                  ("coverage_after_human_exclusions", "coverage_after_source_review_exclusions"),
                                  ("pre_human_feasibility_pass", "pre_source_review_feasibility_pass"),
                                  ("human_review", "source_review"),
                                  ("minimum_feasibility_and_human_review_pass", "minimum_feasibility_and_source_review_pass")):
            summary[current] = summary.pop(previous)
        summary["independent_human_validation"] = False
        summary["review_mode"] = "ai_source_review"
        summary["source_review"]["reviewer_role"] = "ai"
        summary["source_review"]["selection"] = "The same fixed 20-case sample; disagreements are excluded without replacements."
        summary["provenance"]["review_amendment_canonical_sha256"] = _digest(_canonical(review_amendment))
        summary["provenance"]["original_protocol_sha256"] = SOURCE_REVIEW_PROTOCOL_SHA256
        summary["limitations"][2] = "The substituted source reviewer is AI. This is not independent human validation; AI semantic errors can remain."
    released_map = {cid: final_labels.get(cid, "AMBIGUOUS") for cid in sorted(panel["claims"])} if released else {}
    repair = {"schema_version": "phase4b_repair_labels_v1", "status": "released" if released else "not_released",
              "labels": released_map, "sample_id": sample_id, "recipient_evaluation_authorized": False,
              "provenance": summary["provenance"]}
    if source_mode:
        repair.update(review_mode="ai_source_review", independent_human_validation=False,
                      source_review_complete=human["complete"])
    return summary, claim_rows, template, repair


def _fenced(text):
    longest = max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return fence + "\n" + text + "\n" + fence


def human_markdown(panel, claim_rows, template, out, *, source_review=False):
    by_id = {row["claim_id"]: row for row in claim_rows}
    review_name = "source_review" if source_review else "human_review"
    world_prefix = "source-world-" if source_review else "human-world-"
    lines = ["# Phase 4B AI source review" if source_review else "# Phase 4B human source review", "",
             f"Check each exact claim against the full frozen world and cited passages. Record agree or disagree and your source-based explanation in {review_name}.json. Do not infer that two agreeing models must be correct.", "",
             "Only the predetermined sample appears here. This packet contains no original oracle labels, debate answers, donor identities, or original outcomes.", ""]
    if source_review:
        lines += ["The user authorized AI review of this fixed sample. Independent human validation has not been performed.", ""]
    for index, entry in enumerate(template["entries"], 1):
        row = by_id[entry["claim_id"]]
        world_file = (Path(out) / (world_prefix + row["world_sha256"] + ".txt")).resolve()
        lines += [f"## Claim {index}", "", f"Claim ID: `{row['claim_id']}`", "", _fenced(row["exact_claim"]), "",
                  f"[Full frozen world](<{world_file.as_posix()}>)", ""]
        if source_review:
            lines += [f"AI source-review decision: **{entry['decision']}**", ""]
            if entry["comment"].strip():
                lines += ["Source-based review reason:", "", _fenced(entry["comment"]), ""]
        for number, (_, assessment) in enumerate(sorted(row["adjudicators"].items()), 1):
            judgment = assessment["judgment"]
            lines += [f"Adjudicator {number}: **{judgment['label']}**", "", _fenced(judgment["rationale"]), ""]
            if judgment["missing_information"]:
                lines += ["Missing information:", "", _fenced(judgment["missing_information"]), ""]
            for quote in judgment["quotes"]:
                lines += ["Exact source quotation:", "", _fenced(quote), ""]
    return "\n".join(lines)


def _atomic(path, text):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    review_args = parser.add_mutually_exclusive_group()
    review_args.add_argument("--human-review", type=Path)
    review_args.add_argument("--source-review", type=Path)
    parser.add_argument("--review-amendment", type=Path)
    args = parser.parse_args()
    inputs, results, out = args.inputs.resolve(), args.results.resolve(), args.out.resolve()
    source_mode = args.source_review is not None or args.review_amendment is not None
    _require(not source_mode or args.human_review is None, "Human review and amended AI source review cannot be combined")
    _require(not args.source_review or args.review_amendment is not None, "AI source review requires an explicit review amendment")
    _require(not out.is_relative_to(inputs) and not out.is_relative_to(ROOT), "Private consensus output must be outside Git and frozen inputs")
    review_name = "source_review" if source_mode else "human_review"
    output_names = {"consensus_summary.json", "claim_consensus_private.jsonl", "repair_labels.json", review_name + ".json", review_name + ".md"}
    _require(results not in {out / name for name in output_names}, "Consensus output would overwrite completed results")
    review_path = args.source_review if source_mode else args.human_review
    if review_path:
        _require(review_path.resolve() not in {out / name for name in output_names - {review_name + ".json"}},
                 "Consensus output would overwrite submitted review")
    if source_mode:
        _require(not (out / "human_review.json").exists() and not (out / "human_review.md").exists(),
                 "AI source review must use a new analysis output folder, preserving the original human-review artifacts")
    if args.review_amendment:
        _require(args.review_amendment.resolve() not in {out / name for name in output_names},
                 "Consensus output would overwrite the review amendment")
    input_digest, results_digest = _sha(inputs / "adjudication_manifest.json"), _sha(results)
    panel, rows = load_inputs(inputs), _jsonl(results)
    review_bytes = review_path.read_bytes() if review_path else None
    review = _json(review_bytes.decode("utf-8")) if review_bytes is not None else None
    amendment_bytes = args.review_amendment.read_bytes() if args.review_amendment else None
    amendment = _json(amendment_bytes.decode("utf-8")) if amendment_bytes is not None else None
    summary, claim_rows, template, repair = analyze(panel, rows, input_manifest_sha256=input_digest,
                                                  results_sha256=results_digest, human_review=None if source_mode else review,
                                                  source_review=review if source_mode else None, review_amendment=amendment)
    _require(_sha(inputs / "adjudication_manifest.json") == input_digest and _sha(results) == results_digest,
             "Inputs/results changed during analysis; retry against a stable snapshot")
    if review_path:
        _require(review_path.read_bytes() == review_bytes, "Submitted review changed during analysis")
        summary["provenance"][review_name + "_sha256"] = hashlib.sha256(review_bytes).hexdigest()
    if args.review_amendment:
        _require(args.review_amendment.read_bytes() == amendment_bytes, "Review amendment changed during analysis")
        summary["provenance"]["review_amendment_sha256"] = hashlib.sha256(amendment_bytes).hexdigest()
    summary["provenance"]["consensus_script_sha256"] = _sha(Path(__file__))
    out.mkdir(parents=True, exist_ok=True)
    template_path = out / (review_name + ".json")
    if summary["administrative_coverage"]["complete"]:
        if template_path.exists():
            existing = _json(template_path.read_text(encoding="utf-8"))
            _require(existing.get("sample_id") == template["sample_id"] and existing.get("schema_version") == template["schema_version"],
                     "Existing review belongs to another sample or mode; use a fresh output directory")
            if source_mode and review_bytes is not None:
                _require(template_path.read_bytes() == review_bytes,
                         "Existing AI review copy differs from the submitted review; use a fresh output directory")
        else:
            with template_path.open("xb") as stream:
                stream.write(review_bytes if source_mode and review_bytes is not None else
                             (json.dumps(template, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8"))
        for world_sha in {entry["world_sha256"] for entry in template["entries"]}:
            target = out / (("source-world-" if source_mode else "human-world-") + world_sha + ".txt")
            document = panel["worlds"][world_sha]["world_document"]
            if target.exists():
                _require(target.read_bytes() == document.encode("utf-8"), "Frozen review source copy changed")
            else:
                with target.open("xb") as stream:
                    stream.write(document.encode("utf-8"))
        _atomic(out / (review_name + ".md"), human_markdown(panel, claim_rows,
                review if source_mode and review is not None else template, out, source_review=source_mode))
    _atomic(out / "claim_consensus_private.jsonl", "".join(_canonical(row) + "\n" for row in claim_rows))
    _atomic(out / "repair_labels.json", json.dumps(repair, indent=2, ensure_ascii=True, allow_nan=False) + "\n")
    _atomic(out / "consensus_summary.json", json.dumps(summary, indent=2, ensure_ascii=True, allow_nan=False) + "\n")
    print(_canonical({"status": summary["status"], "completed": summary["administrative_coverage"]["completed_calls"],
                      "expected": len(panel["calls"]), "repair_labels_released": repair["status"] == "released",
                      "out": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
