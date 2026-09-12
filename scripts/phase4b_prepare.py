"""Prepare blinded Phase 4B claims and exact label-repair spans, entirely offline.

Only answered exchanges enter adjudication. Blocked exchanges and free retries
are preserved in packet histories. No provider clients or dispatch are imported.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rejudge.oracle_channel import is_done_robust
from scripts.phase4_analysis import load_inputs

LABELS = {"YES", "NO", "NOT ADDRESSED"}
OUTPUT_NAMES = ("blind_claims.jsonl", "worlds_private.jsonl", "claim_occurrences_private.jsonl",
                "packet_repair_map_private.jsonl", "manifest.json")


class PreparationError(ValueError):
    pass


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def claim_id(world_sha256: str, exact_claim: str) -> str:
    return digest_text(world_sha256 + "|" + exact_claim)


def _require(condition: bool, message: str):
    if not condition:
        raise PreparationError(message)


def _block(exchanges: list[dict]) -> tuple[str, list[dict]]:
    """Render a recap while calculating exact offsets, including repeated claims."""
    text, spans = "", []
    for index, exchange in enumerate(exchanges):
        if index:
            text += "\n\n"
        text += f"Query {index + 1}: {exchange['extracted_claim']}\nResult: "
        if exchange.get("blocked") is True:
            text += exchange["blocked_feedback"]
        else:
            label = exchange.get("normalized")
            _require(label in LABELS, "An answered exchange has no supported oracle label")
            spans.append({"exchange_index": index, "start": len(text), "end": len(text) + len(label),
                          "original_label": label})
            text += label
    return text, spans


def _render(template: str, marker: str, block: str, **fields) -> tuple[str, int]:
    _require(template.count("{" + marker + "}") == 1, "Frozen template has no unique evidence placeholder")
    prefix, suffix = template.split("{" + marker + "}")
    prefix, suffix = prefix.format(**fields), suffix.format(**fields)
    return prefix + block + suffix, len(prefix)


def extract_repair_spans(messages: list[dict], source: dict, templates: dict) -> list[dict]:
    """Replay the recorded query/gate sequence; never globally replace label text.

    Offsets are Python/Unicode character indices within one message's content.
    Only generated user-message oracle payloads and recap payloads are editable.
    Assistant-authored query prose, including quoted labels, remains unchanged.
    """
    exchanges = source.get("exchanges")
    events = source.get("gate_events")
    _require(isinstance(exchanges, list) and isinstance(events, list), "Missing source exchanges/gate events")
    _require(source.get("budget") == 8 and source.get("queries_used") == len(exchanges), "Source is not an eight-slot history")
    _require(len(messages) >= 3 and messages[0].get("role") == "system" and messages[1].get("role") == "user", "Bad initial message shape")
    spans, position, consumed, event_index = [], 2, 0, 0

    def check_message(index, role, content):
        _require(index < len(messages) and messages[index] == {"role": role, "content": content},
                 f"Unmatched {role} history message at index {index}")

    while position < len(messages) - 1:
        _require(consumed < 8, "Unexpected query prompt after all slots were consumed")
        previous, previous_spans = _block(exchanges[:consumed])
        previous = previous if consumed else "No queries submitted yet."
        expected, offset = _render(templates["query"], "previous_queries", previous,
                                   remaining_budget=8 - consumed, total_budget=8)
        check_message(position, "user", expected)
        spans.extend({**span, "message_index": position, "start": span["start"] + offset,
                      "end": span["end"] + offset, "occurrence_kind": "previous_recap"} for span in previous_spans)
        position += 1
        while True:
            _require(position < len(messages) - 1 and messages[position].get("role") == "assistant", "Expected raw assistant query or DONE")
            raw_query = messages[position].get("content")
            _require(isinstance(raw_query, str), "Raw assistant query is not text")
            if is_done_robust(raw_query):
                _require(event_index == len(events) and consumed == len(exchanges), "DONE precedes unused gate events/exchanges")
                position += 1
                break
            _require(event_index < len(events), "Raw query has no matching gate event")
            event = events[event_index]
            _require(event.get("raw_query") == raw_query, "Raw query disagrees with recorded gate event")
            event_index += 1
            action = event.get("action")
            if action == "retry":
                _require(event.get("slot_consumed") is False, "Free retry consumed a slot")
                check_message(position + 1, "user", templates["rejection"])
                position += 2
                continue
            _require(action in {"allow", "block"} and consumed < len(exchanges), "Unsupported gate action or missing consumed exchange")
            exchange = exchanges[consumed]
            _require(exchange.get("raw_query_response") == raw_query, "Consumed exchange disagrees with raw query")
            if action == "block":
                _require(exchange.get("blocked") is True and exchange.get("raw_oracle_reply") is None,
                         "Blocked exchange unexpectedly has an oracle reply")
                check_message(position + 1, "user", exchange["blocked_feedback"])
            else:
                _require(exchange.get("blocked") is not True and exchange.get("normalized") in LABELS
                         and exchange.get("raw_oracle_reply") is not None and exchange.get("placebo") is False,
                         "Allowed exchange lacks an actual oracle reply")
                label = exchange["normalized"]
                check_message(position + 1, "user", "Oracle result: " + label)
                spans.append({"exchange_index": consumed, "message_index": position + 1,
                              "start": len("Oracle result: "), "end": len("Oracle result: ") + len(label),
                              "original_label": label, "occurrence_kind": "immediate"})
            consumed += 1
            position += 2
            break
    _require(consumed == len(exchanges) and event_index == len(events), "Unmatched source exchanges/gate events remain")
    final, final_spans = _block(exchanges)
    prefix = "VERIFICATION RESULTS:\n\n" if exchanges else ""
    expected, offset = _render(templates["verdict"], "query_results", prefix + final)
    _require(position == len(messages) - 1, "Final verdict prompt boundary is ambiguous")
    check_message(position, "user", expected)
    spans.extend({**span, "message_index": position, "start": span["start"] + offset + len(prefix),
                  "end": span["end"] + offset + len(prefix), "occurrence_kind": "final_recap"} for span in final_spans)
    for index, exchange in enumerate(exchanges):
        occurrences = [s for s in spans if s["exchange_index"] == index]
        if exchange.get("blocked") is True:
            _require(not occurrences, "Blocked exchange received an editable label span")
        else:
            kinds = Counter(s["occurrence_kind"] for s in occurrences)
            _require(kinds["immediate"] == 1 and kinds["final_recap"] == 1,
                     "Answered exchange lacks exactly one immediate and final label")
    return sorted(spans, key=lambda span: (span["message_index"], span["start"]))


def apply_repair_map(messages: list[dict], repair_map: dict, newlabels: dict[str, str]) -> list[dict]:
    """Return a repaired copy; missing/AMBIGUOUS labels leave occurrences intact."""
    _require(digest_text(canonical(messages)) == repair_map["messages_sha256"], "Repair input history hash mismatch")
    _require(all(label in LABELS | {"AMBIGUOUS"} for label in newlabels.values()), "Unsupported consensus label")
    grouped = defaultdict(list)
    for span in repair_map["spans"]:
        index, start, end = span["message_index"], span["start"], span["end"]
        _require(all(type(value) is int for value in (index, start, end)) and 0 <= index < len(messages), "Invalid repair span index")
        _require(messages[index]["role"] == "user" and 0 <= start < end <= len(messages[index]["content"]), "Repair span falls outside a user payload")
        _require(messages[index]["content"][start:end] == span["original_label"] and span["original_label"] in LABELS,
                 "Repair span no longer contains its original oracle label")
        grouped[index].append(span)
    repaired = copy.deepcopy(messages)
    for index, spans in grouped.items():
        ordered = sorted(spans, key=lambda s: s["start"])
        _require(all(a["end"] <= b["start"] for a, b in zip(ordered, ordered[1:])), "Overlapping repair spans")
        for span in reversed(ordered):
            replacement = newlabels.get(span["claim_id"], span["original_label"])
            if replacement == "AMBIGUOUS":
                replacement = span["original_label"]
            content = repaired[index]["content"]
            repaired[index]["content"] = content[:span["start"]] + replacement + content[span["end"]:]
    return repaired


def repair_coverage(occurrences: list[dict], newlabels: dict[str, str], *, total_packets: int = 1312) -> dict:
    """Feasibility counts only; root owns adjudicator consensus and human review."""
    _require(all(value in LABELS | {"AMBIGUOUS"} for value in newlabels.values()), "Unsupported consensus label")
    known = {row["claim_id"] for row in occurrences}
    _require(set(newlabels) <= known, "Consensus map includes a claim outside the prepared frame")
    changed = [row for row in occurrences if newlabels.get(row["claim_id"]) in LABELS
               and newlabels[row["claim_id"]] != row["original_label"]]
    claims = {row["claim_id"] for row in changed}
    packets = {row["packet_id"] for row in changed}
    questions = {row["question_id"] for row in changed}
    return {"changed_claims": len(claims), "changed_packets": len(packets), "changed_questions": len(questions),
            "changed_answered_exchanges": len(changed), "changed_label_spans": sum(len(row["spans"]) for row in changed),
            "unadjudicated_claims": len(known - set(newlabels)),
            "ambiguous_claims": sum(value == "AMBIGUOUS" for value in newlabels.values()),
            "maximum_mean_causal_benefit": len(packets) / total_packets,
            "minimum_feasibility_pass": len(claims) >= 20 and len(questions) >= 10 and len(packets) >= 40,
            "changed_claim_ids": sorted(claims), "changed_packet_ids": sorted(packets),
            "changed_question_ids": sorted(questions),
            "interpretation": "Necessary coverage only, not power, consensus validation, human approval, or paid-execution authorization."}


def load_templates(prepared_manifest: dict) -> tuple[dict, dict]:
    protocol_path = ROOT / "rejudge/phase3_protocol_v3_r6.json"
    _require(digest_file(protocol_path) == prepared_manifest["source_protocol_sha256"], "Frozen Phase 3 protocol hash mismatch")
    protocol = json.loads(protocol_path.read_bytes())
    bundle_relative = protocol["sources"]["prompt_bundle"]
    bundle_path = ROOT / bundle_relative
    bundle = json.loads(bundle_path.read_bytes())
    expected = protocol["source_bindings"]["canonical_json_sha256"][bundle_relative]
    _require(digest_text(canonical(bundle)) == expected, "Frozen prompt bundle canonical hash mismatch")
    template = bundle["templates"]
    return {"query": template["sequential_judge_query"]["user_prompt_template"],
            "verdict": template["sequential_judge_verdict"]["user_prompt_template"],
            "rejection": template["sequential_judge_rejection"]["payload"],
            "oracle_system": template["oracle"]["system_prompt"],
            "oracle_user": template["oracle"]["user_prompt_template"]}, {
        "phase3_protocol_sha256": prepared_manifest["source_protocol_sha256"],
        "prompt_bundle_path": str(bundle_path), "prompt_bundle_canonical_sha256": expected,
        "oracle_system_prompt_sha256": digest_text(template["oracle"]["system_prompt"])}


def prepare(inputs: Path) -> tuple[dict[str, list[dict]], dict]:
    calls, units, manifest = load_inputs(inputs)
    templates, template_provenance = load_templates(manifest)
    unit_by_id = {unit["unit_id"]: unit for unit in units}
    calls_by_packet = defaultdict(list)
    for call in calls:
        if call["arm"] != "empty":
            calls_by_packet[call["packet_id"]].append(call)
    packets = {}
    with (inputs / "packets.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            packet = json.loads(line)
            if packet["packet_id"] not in calls_by_packet:
                continue
            pair = calls_by_packet[packet["packet_id"]]
            _require(len(pair) == 2 and len({(c["unit_id"], c["arm"], c["messages_sha256"]) for c in pair}) == 1,
                     "Prepared source packet is not identical across recipients")
            call = pair[0]
            _require(digest_text(canonical(packet["messages"])) == packet["messages_sha256"] == call["messages_sha256"],
                     "Prepared message hash mismatch")
            _require(packet["source_cell_key"] not in packets, "Duplicate source cell among nonempty packets")
            unit = unit_by_id[call["unit_id"]]
            packets[packet["source_cell_key"]] = {**packet, "unit_id": call["unit_id"], "arm": call["arm"],
                                                 **{key: unit[key] for key in ("question_id", "world", "debater", "transcript_index", "side")}}
    _require(len(packets) == 1312, "Expected 1312 nonempty donor/unit packets")
    source_paths = [(Path(path), sha) for path, sha in manifest["source_hashes"].items() if Path(path).name == "main_results.jsonl"]
    _require(len(source_paths) == 1, "Prepared manifest does not bind exactly one historical result source")
    source_path, source_sha = source_paths[0]
    _require(digest_file(source_path) == source_sha, "Historical source hash mismatch")
    world_documents, matched = {}, set()
    oracle_prefix, oracle_suffix = templates["oracle_user"].split("{world_document}")
    _require(oracle_prefix == "WORLD DOCUMENT:\n", "Unexpected frozen oracle world prefix")
    summaries = []
    with source_path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("cell_key") not in packets:
                continue
            key, source = row["cell_key"], row["result"]
            _require(key not in matched, "Duplicate historical source row")
            matched.add(key)
            packet = packets[key]
            _require(source.get("cell_key") == key and source.get("condition") == "sequential_b8"
                     and source.get("question_id") == packet["question_id"] and source.get("world") == packet["world"]
                     and source.get("transcript_index") == packet["transcript_index"] and source.get("replicate") == packet["side"],
                     "Source identity disagrees with the prepared packet")
            _require(source["judge_messages"][-1] == {"role": "assistant", "content": source["raw_verdict_text"]}
                     and source["judge_messages"][:-1] == packet["messages"], "Source pre-verdict messages do not match prepared packet")
            spans = extract_repair_spans(packet["messages"], source, templates)
            answered, blocked = [], []
            for index, exchange in enumerate(source["exchanges"]):
                if exchange.get("blocked") is True:
                    blocked.append({"exchange_index": index, "exact_claim": exchange["extracted_claim"],
                                    "blocked_feedback": exchange["blocked_feedback"]})
                    continue
                claim, prompt = exchange["extracted_claim"], exchange.get("oracle_prompt")
                suffix = oracle_suffix.format(query_claim=claim)
                _require(isinstance(prompt, str) and prompt.startswith(oracle_prefix) and prompt.endswith(suffix),
                         "Oracle prompt does not reproduce the exact extracted claim")
                world_document = prompt[len(oracle_prefix):-len(suffix)]
                _require(bool(world_document), "Empty oracle world document")
                if packet["world"] in world_documents:
                    _require(world_documents[packet["world"]] == world_document, "Multiple world bytes under one source world identity")
                world_documents[packet["world"]] = world_document
                answered.append({"exchange_index": index, "exact_claim": claim, "original_label": exchange["normalized"]})
            summaries.append({**{k: packet[k] for k in ("packet_id", "source_cell_key", "unit_id", "arm", "question_id", "world", "debater", "messages_sha256")},
                              "spans": spans, "answered": answered, "blocked_exchanges": blocked})
    _require(matched == set(packets), "Missing source histories")
    _require(len(world_documents) == 3, "Expected three exact world documents")
    _require(digest_file(source_path) == source_sha, "Historical source changed during preparation")
    claims, occurrences, maps = {}, [], []
    worlds = [{"world_sha256": digest_text(document), "world_document": document}
              for _, document in sorted(world_documents.items())]
    for packet in sorted(summaries, key=lambda row: row["packet_id"]):
        world_sha = digest_text(world_documents[packet["world"]])
        spans = []
        for answer in packet["answered"]:
            identity = claim_id(world_sha, answer["exact_claim"])
            blind = {"claim_id": identity, "world_sha256": world_sha, "exact_claim": answer["exact_claim"]}
            _require(identity not in claims or claims[identity] == blind, "Claim hash collision")
            claims[identity] = blind
            exchange_spans = [{**span, "claim_id": identity} for span in packet["spans"] if span["exchange_index"] == answer["exchange_index"]]
            spans.extend(exchange_spans)
            occurrences.append({"claim_id": identity, "packet_id": packet["packet_id"], "unit_id": packet["unit_id"],
                                "source_cell_key": packet["source_cell_key"], "question_id": packet["question_id"],
                                "world_sha256": world_sha, "donor_arm": packet["arm"],
                                "exchange_index": answer["exchange_index"], "original_label": answer["original_label"],
                                "spans": exchange_spans})
        maps.append({**{k: packet[k] for k in ("packet_id", "source_cell_key", "unit_id", "arm", "question_id", "world", "debater", "messages_sha256")},
                     "world_sha256": world_sha, "spans": sorted(spans, key=lambda s: (s["message_index"], s["start"])),
                     "blocked_exchanges": packet["blocked_exchanges"],
                     "offset_convention": "Zero-based Unicode character indices [start,end) in messages[message_index].content"})
    artifacts = {"blind_claims.jsonl": [claims[key] for key in sorted(claims)],
                 "worlds_private.jsonl": sorted(worlds, key=lambda row: row["world_sha256"]),
                 "claim_occurrences_private.jsonl": occurrences, "packet_repair_map_private.jsonl": maps}
    label_sets = defaultdict(set)
    for occurrence in occurrences:
        label_sets[occurrence["claim_id"]].add(occurrence["original_label"])
    prepared = {"schema_version": "phase4b_prepared_claims_v1", "status": "prepared_offline_no_calls",
                "prepared_4a_manifest_sha256": digest_file(inputs / "manifest.json"),
                "phase4_protocol_sha256": manifest["phase4_protocol_sha256"],
                "source_results_path": str(source_path), "source_results_sha256": source_sha,
                "input_hashes": manifest["outputs"], "template_provenance": template_provenance,
                "oracle_contract": {"system_prompt": templates["oracle_system"], "user_prompt_template": templates["oracle_user"]},
                "counts": {"nonempty_packets": len(maps), "unique_blind_claims": len(claims), "worlds": len(worlds),
                           "answered_exchanges": len(occurrences), "blocked_exchanges_preserved": sum(len(row["blocked_exchanges"]) for row in maps),
                           "label_spans": sum(len(row["spans"]) for row in maps),
                           "claims_with_disagreeing_original_labels": sum(len(labels) > 1 for labels in label_sets.values()),
                           "original_label_counts": dict(Counter(row["original_label"] for row in occurrences))},
                "blind_prompt_contract": "Resolve each blind claim's world_sha256 in worlds_private.jsonl. Supply only that world, exact_claim, oracle semantic contract and adjudication-format instructions. Do not send private occurrence/packet maps, donor labels, original labels, original outcomes or answer keys.",
                "repair_contract": "Apply consensus labels only at listed generated user-payload spans. AMBIGUOUS/missing labels remain unchanged. Preserve assistant queries, gate retries, consumed blocks and all other bytes.",
                "scope": "Only claims with actual oracle replies are adjudicated; blocked exchanges remain unchanged and are not paid adjudication requests. Exact claims are not normalized or merged by semantic similarity.",
                "limitations": ["Earlier adaptive queries were generated under original answers and remain fixed; this is captured-history readout repair.",
                                "Original labels may disagree across identical world/claim occurrences; a single consensus label applies to every occurrence.",
                                "The frozen oracle's label-only response format is recorded for provenance. Adjudication must separately request source quotations and an explicit underdetermination rationale without altering the three label meanings."],
                "preparation_script_sha256": digest_file(Path(__file__)), "python": sys.version}
    return artifacts, prepared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    _require(not out.is_relative_to(ROOT) and not out.is_relative_to(args.inputs.resolve()), "Private preparation output must be outside Git and original inputs")
    artifacts, manifest = prepare(args.inputs)
    source_root = Path(manifest["source_results_path"]).resolve().parent
    _require(not out.is_relative_to(source_root), "Preparation output must not alter the completed Phase 3 archive")
    out.mkdir(parents=True, exist_ok=True)
    serialized = {name: "".join(canonical(row) + "\n" for row in rows).encode("utf-8") for name, rows in artifacts.items()}
    manifest["outputs"] = {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in serialized.items()}
    serialized["manifest.json"] = (json.dumps(manifest, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    # Idempotent offline rebuilds may verify existing bytes but never overwrite a
    # differing artifact or a root-owned adjudication file.
    for name, raw in serialized.items():
        target = out / name
        _require(not target.exists() or target.read_bytes() == raw, f"Refusing to replace different existing preparation artifact: {name}")
    for name, raw in serialized.items():
        target = out / name
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(raw)
    print(json.dumps({"out": str(out), "manifest_sha256": digest_file(out / "manifest.json"), "counts": manifest["counts"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PreparationError, OSError, ValueError, KeyError) as exc:
        print(f"Phase 4B preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
