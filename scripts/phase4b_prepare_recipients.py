"""Build the frozen Phase 4B recipient panel offline, without provider calls.

All original and repaired histories receive fresh verdict calls, even where their
messages are identical. Only the reviewed oracle-label spans may change.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.phase4_analysis import load_inputs
from scripts import phase4b_prepare as repair

QWEN = "Qwen/Qwen3.8-2.4T-A95B"
LLAMA = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
JUDGES = (QWEN, LLAMA)
DONORS = ("qwen_history", "llama_history")
ARMS = tuple(donor + "_" + status for donor in DONORS for status in ("original", "repaired"))
MODEL_SETTINGS = {QWEN: {"temperature": 0.3, "max_tokens": 16384, "stream": False},
                  LLAMA: {"temperature": 0.3, "max_tokens": 512, "stream": False}}
ORDER_SEED = 2026091207
CALL_SEED_NAMESPACE = "phase4b-recipient-verdict-v1"
LABELS_SHA = "37d69f4ec7be6e714883fc14a3c0fd2eb9ec0476802d9efed932a06070400f01"
INPUTS_SHA = "a27de63adc882983bcd5784c1812f5ada430943e78b981334db1f82b4c676ca1"
CLAIMS_SHA = "28bd360ef22c1f9429768e42c737c5cf7e2be66cb5416e04f9cf3658e07fcfad"
PROTOCOL_SHA = "f76a1ce7e10a9c961d983f31fa701882fb47111be57876587098c0f00ee10a12"
OUTPUT_NAMES = ("packets.jsonl", "calls.jsonl", "units_private.jsonl", "edit_map_private.jsonl")


class PreparationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PreparationError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def message_sha(messages):
    return digest(canonical(messages).encode("utf-8"))


def jsonl(rows):
    return ("".join(canonical(row) + "\n" for row in rows)).encode("utf-8")


def read_rows(data):
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def unique(rows, key):
    indexed = {row[key]: row for row in rows}
    require(len(indexed) == len(rows), f"Duplicate {key}")
    return indexed


def verdict_seed(unit_id, judge):
    return int(digest((CALL_SEED_NAMESPACE + "|" + unit_id + "|" + judge).encode())[:8], 16) % 2147483647


def repair_packet(packet, mapping, labels):
    """Apply pinned spans and independently check the untouched intervening text.

    The edit map records offsets in both original and repaired Unicode strings.
    Equal labels and excluded/missing labels are not recorded as edits.
    """
    require(packet["packet_id"] == mapping["packet_id"]
            and packet["source_cell_key"] == mapping["source_cell_key"]
            and packet["messages_sha256"] == mapping["messages_sha256"], "Packet/repair identity mismatch")
    messages = packet["messages"]
    require(message_sha(messages) == packet["messages_sha256"], "Packet messages hash mismatch")
    require(messages[-1]["role"] == "user", "Saved final verdict must not enter a packet")
    repaired = repair.apply_repair_map(messages, mapping, labels)
    grouped, edits = defaultdict(list), []
    for span in mapping["spans"]:
        require(span["message_index"] >= 2, "Repair must not edit the shared debate context")
        if labels.get(span["claim_id"]) in repair.LABELS and labels[span["claim_id"]] != span["original_label"]:
            grouped[span["message_index"]].append(span)
    for index, original in enumerate(messages):
        text, cursor, rebuilt = original["content"], 0, ""
        for span in sorted(grouped[index], key=lambda item: item["start"]):
            rebuilt += text[cursor:span["start"]]
            new_label = labels[span["claim_id"]]
            edits.append({"message_index": index, "original_start": span["start"], "original_end": span["end"],
                          "repaired_start": len(rebuilt), "repaired_end": len(rebuilt) + len(new_label),
                          "original_label": span["original_label"], "repaired_label": new_label,
                          **{key: span[key] for key in ("claim_id", "exchange_index", "occurrence_kind")}})
            rebuilt += new_label
            cursor = span["end"]
        rebuilt += text[cursor:]
        require(repaired[index] == {"role": original["role"], "content": rebuilt}, "Repair changed non-label content")
    require(repaired[:2] == messages[:2], "Shared context changed")
    return repaired, edits


def build_panel(units, source_packets, repair_maps, labels):
    """Pure construction seam; full frozen-frame eligibility is checked by prepare."""
    by_unit = unique(units, "unit_id")
    sources = unique(source_packets, "packet_id")
    maps = unique(repair_maps, "packet_id")
    expected = {uid + ":" + donor for uid in by_unit for donor in DONORS}
    require(set(maps) == expected and set(sources) == expected, "Source donor panel is incomplete or contains extra packets")
    packets, calls, edit_rows = [], [], []
    changed_claims, changed_questions, changed_exchanges = set(), set(), set()
    changed_packets, changed_spans, blocked_count = 0, 0, 0
    for unit in sorted(units, key=lambda row: (row["question_id"], row["debater"], row["transcript_index"], row["side"])):
        uid = unit["unit_id"]
        shared_context = None
        for donor in DONORS:
            source_id = uid + ":" + donor
            original, mapping = sources[source_id], maps[source_id]
            require(all(mapping[key] == unit[key] for key in ("unit_id", "question_id", "world", "debater"))
                    and mapping["arm"] == donor, "Source unit provenance mismatch")
            if shared_context is None:
                shared_context = original["messages"][:2]
            require(original["messages"][:2] == shared_context, "Donor shared contexts differ")
            repaired, edits = repair_packet(original, mapping, labels)
            blocked_count += len(mapping["blocked_exchanges"])
            changed_packets += bool(edits)
            changed_spans += len(edits)
            if edits:
                changed_questions.add(unit["question_id"])
            for edit in edits:
                changed_claims.add(edit["claim_id"])
                changed_exchanges.add((source_id, edit["exchange_index"]))
            hashes = {}
            for status, messages in (("original", original["messages"]), ("repaired", repaired)):
                arm = donor + "_" + status
                packet_id, hashed = uid + ":" + arm, message_sha(messages)
                hashes[status] = hashed
                packets.append({"packet_id": packet_id, "messages": messages, "messages_sha256": hashed,
                                "source_packet_id": source_id, "source_cell_key": original["source_cell_key"],
                                "donor_arm": donor, "repair_status": status,
                                "changed_label_spans": len(edits) if status == "repaired" else 0})
                for judge in JUDGES:
                    calls.append({"cell_id": uid + ":" + judge + ":" + arm, "unit_id": uid,
                                  "judge": judge, "arm": arm, "packet_id": packet_id, "messages_sha256": hashed,
                                  "donor_arm": donor, "repair_status": status, "seed": verdict_seed(uid, judge),
                                  **MODEL_SETTINGS[judge]})
            edit_rows.append({"unit_id": uid, "question_id": unit["question_id"], "world": unit["world"],
                              "donor_arm": donor, "source_packet_id": source_id,
                              "source_cell_key": original["source_cell_key"],
                              "original_packet_id": source_id + "_original", "repaired_packet_id": source_id + "_repaired",
                              "original_messages_sha256": hashes["original"], "repaired_messages_sha256": hashes["repaired"],
                              "changed": bool(edits), "changed_label_spans": len(edits), "edits": edits,
                              "blocked_exchanges_preserved": len(mapping["blocked_exchanges"]),
                              "offset_convention": "Zero-based Unicode character indices [start,end) within message.content"})
    rng, grouped = random.Random(ORDER_SEED), defaultdict(list)
    for call in calls:
        grouped[by_unit[call["unit_id"]]["question_id"]].append(call)
    order = sorted(grouped)
    rng.shuffle(order)
    schedule = []
    for question in order:
        rng.shuffle(grouped[question])
        schedule.extend(grouped[question])
    coverage = {"changed_claims": len(changed_claims), "changed_packets": changed_packets,
                "changed_questions": len(changed_questions), "changed_answered_exchanges": len(changed_exchanges),
                "changed_label_spans": changed_spans, "blocked_exchanges_preserved": blocked_count,
                "donor_packets": len(expected), "unchanged_packets": len(expected) - changed_packets}
    return packets, schedule, edit_rows, coverage


def prepare(inputs: Path, adjudication_inputs: Path, labels_path: Path):
    """Return artifact bytes and manifest; read all source files without mutation."""
    inputs, adjudication_inputs, labels_path = inputs.resolve(), adjudication_inputs.resolve(), labels_path.resolve()
    source_hashes = {}

    def bound(path, expected=None):
        data = path.read_bytes()
        hashed = digest(data)
        require(expected is None or hashed == expected, f"Source hash mismatch: {path.name}")
        source_hashes[str(path)] = hashed
        return data

    prepared = json.loads(bound(inputs / "manifest.json", INPUTS_SHA))
    claims = json.loads(bound(adjudication_inputs / "manifest.json", CLAIMS_SHA))
    labels_doc = json.loads(bound(labels_path, LABELS_SHA))
    protocol_path = ROOT / "rejudge/phase4_protocol_v1.json"
    protocol = json.loads(bound(protocol_path, PROTOCOL_SHA))
    require(protocol["stage_4b"]["main_verdict_calls"] == 5248, "Frozen stage count mismatch")
    require(claims["prepared_4a_manifest_sha256"] == INPUTS_SHA
            and claims["phase4_protocol_sha256"] == PROTOCOL_SHA, "Claim preparation binding mismatch")
    _, units, _ = load_inputs(inputs)
    data_4a = {name: bound(inputs / name, rec["sha256"]) for name, rec in prepared["outputs"].items()}
    data_claims = {name: bound(adjudication_inputs / name, rec["sha256"]) for name, rec in claims["outputs"].items()}
    for path, hashed in prepared["source_hashes"].items():
        bound(Path(path), hashed)
    require(labels_doc.get("status") == "released" and labels_doc.get("source_review_complete") is True
            and labels_doc.get("review_mode") == "ai_source_review"
            and labels_doc.get("independent_human_validation") is False, "Reviewed labels are not released under the AI amendment")
    provenance = labels_doc["provenance"]
    review_dir = labels_path.parent
    review = json.loads(bound(review_dir / "source_review.json", provenance["source_review_sha256"]))
    amendment_path = ROOT / "rejudge/phase4b_ai_review_amendment_2026-09-12.json"
    amendment = json.loads(bound(amendment_path, provenance["review_amendment_sha256"]))
    require(message_sha(amendment) == provenance["review_amendment_canonical_sha256"]
            and amendment["authorized"] is True and amendment["reviewer_role"] == "ai"
            and amendment["independent_human_validation"] is False, "AI review amendment mismatch")
    require(amendment["protocol_sha256"] == PROTOCOL_SHA
            and all(amendment[key] == provenance[key] for key in ("input_manifest_sha256", "results_sha256"))
            and amendment["sample_id"] == labels_doc["sample_id"] == review["sample_id"], "Review sample/provenance mismatch")
    bound(adjudication_inputs / "adjudication_manifest.json", provenance["input_manifest_sha256"])
    bound(review_dir.parent / "results.jsonl", provenance["results_sha256"])
    summary = json.loads(bound(review_dir / "consensus_summary.json"))
    consensus_rows = read_rows(bound(review_dir / "claim_consensus_private.jsonl"))
    consensus = unique(consensus_rows, "claim_id")
    labels = labels_doc["labels"]
    require(summary["provenance"] == provenance and summary["minimum_feasibility_and_source_review_pass"] is True
            and summary["administrative_coverage"]["complete"] is True, "Consensus was not fully reviewed and released")
    require(set(labels) == set(consensus) == {row["claim_id"] for row in read_rows(data_claims["blind_claims.jsonl"])},
            "Reviewed label coverage differs from the frozen claim panel")
    for cid, label in labels.items():
        row = consensus[cid]
        require(label in repair.LABELS | {"AMBIGUOUS"} and label == row["released_label"], "Released label disagrees with consensus")
        if label in repair.LABELS:
            require(row["consensus"]["eligible"] is True and row["consensus"]["label"] == label
                    and not row["source_review_disagreed"], "Ineligible correction in reviewed map")
    require(len(review["entries"]) == 20 and len({row["claim_id"] for row in review["entries"]}) == 20,
            "The fixed review sample is incomplete")
    for row in review["entries"]:
        require(row["decision"] in ("agree", "disagree"), "Unfinished source review")
        if row["decision"] == "disagree":
            require(labels[row["claim_id"]] == "AMBIGUOUS", "Source-review disagreement was not excluded")
    maps = read_rows(data_claims["packet_repair_map_private.jsonl"])
    source_packets = [row for row in read_rows(data_4a["packets.jsonl"]) if row["packet_id"].split(":")[-1] in DONORS]
    packets, calls, edit_rows, coverage = build_panel(units, source_packets, maps, labels)
    expected = {"changed_claims": 549, "changed_packets": 588, "changed_questions": 80,
                "changed_label_spans": 3896, "blocked_exchanges_preserved": 1704}
    require(all(coverage[key] == value for key, value in expected.items()), "Reviewed repair coverage changed")
    require(all(coverage[key] == summary["coverage_after_source_review_exclusions"][key]
                for key in expected if key != "blocked_exchanges_preserved"), "Applied repair coverage disagrees with consensus")
    require(len(units) == 656 and len(packets) == 2624 and len(calls) == 5248 and len(edit_rows) == 1312,
            "Frozen recipient panel size mismatch")
    artifacts = {"packets.jsonl": jsonl(packets), "calls.jsonl": jsonl(calls),
                 "units_private.jsonl": data_4a["units_private.jsonl"], "edit_map_private.jsonl": jsonl(edit_rows)}
    code_paths = ("scripts/phase4b_prepare_recipients.py", "scripts/phase4b_prepare.py", "scripts/phase4_analysis.py")
    versions = {}
    for name in ("together", "httpx", "transformers", "tokenizers"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    manifest = {"schema_version": "phase4b_recipient_prepared_panel_v1", "status": "prepared_not_executable",
                "paid_execution_authorized": False, "approved_cap_usd": None,
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "python": sys.version, "library_versions": versions, "gpu_ordinal": None,
                "linker": "not applicable; Python/API study", "source_code_versions": {name: digest((ROOT / name).read_bytes()) for name in code_paths},
                "source_hashes": source_hashes, "phase4_protocol_path": str(protocol_path),
                "phase4_protocol_sha256": PROTOCOL_SHA, "protocol_sha256": PROTOCOL_SHA,
                "questions": 82, "units": 656, "packets": 2624, "requests": 5248, "main_verdict_calls": 5248,
                "requests_per_judge_arm": 656, "world_question_counts": prepared["world_question_counts"],
                "judges": list(JUDGES), "models": list(JUDGES), "arms": list(ARMS), "model_settings": MODEL_SETTINGS,
                "call_seed_namespace": CALL_SEED_NAMESPACE, "order_seed": ORDER_SEED, "analysis_seed": 2026091203,
                "schedule": "CPython random.Random: shuffle sorted questions, then each 64-call block; base order question/debater/transcript/side, donor, original/repaired, Qwen/Llama",
                "repair_coverage": coverage, "review_mode": "ai_source_review", "independent_human_validation": False,
                "provenance": {"prepared_4a_manifest_sha256": INPUTS_SHA, "prepared_claims_manifest_sha256": CLAIMS_SHA,
                               "repair_labels_sha256": LABELS_SHA, "adjudication_manifest_sha256": provenance["input_manifest_sha256"],
                               "adjudication_results_sha256": provenance["results_sha256"],
                               "review_amendment_sha256": provenance["review_amendment_sha256"],
                               "source_review_sha256": provenance["source_review_sha256"],
                               "consensus_summary_sha256": source_hashes[str(review_dir / "consensus_summary.json")],
                               "claim_consensus_sha256": source_hashes[str(review_dir / "claim_consensus_private.jsonl")]},
                "outputs": {name: {"sha256": digest(data), "bytes": len(data)} for name, data in artifacts.items()},
                "input_contract": "Send only packet.messages and matching model/seed/temperature/max_tokens/stream. Never send metadata, private maps, gold, source worlds or saved final verdicts. Both arms always require fresh calls even with equal messages.",
                "limitations": ["Final-readout label repair of captured b8 histories, not corrected live adaptive querying.",
                                "Source review is AI, not independent human or independent model-family validation.",
                                "All 656 units remain; no outcome filtering, fresh empty arm, new claims or new questions.",
                                "Only approved label spans change; prior queries, blocked feedback, order and repetitions remain."]}
    for path, hashed in source_hashes.items():
        require(repair.digest_file(Path(path)) == hashed, f"Source changed during preparation: {path}")
    return artifacts, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--adjudication-inputs", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    for source in (ROOT, args.inputs.resolve(), args.adjudication_inputs.resolve(), args.labels.resolve().parent.parent):
        require(not out.is_relative_to(source), "Output must be outside source archives and Git")
    require(not out.exists(), "Output directory must be new; existing artifacts are never overwritten")
    artifacts, manifest = prepare(args.inputs, args.adjudication_inputs, args.labels)
    out.mkdir(parents=True, exist_ok=False)
    for name, data in artifacts.items():
        with (out / name).open("xb") as stream:
            stream.write(data)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=True, indent=2) + "\n").encode()
    with (out / "manifest.json").open("xb") as stream:
        stream.write(manifest_bytes)
    print(json.dumps({"out": str(out), "manifest_sha256": digest(manifest_bytes), "requests": 5248,
                      "repair_coverage": manifest["repair_coverage"], "paid_calls": 0}))


if __name__ == "__main__":
    main()
