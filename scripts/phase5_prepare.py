"""Offline Phase 5 evidence-scope panel; changes only a fixed system-prompt suffix."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import phase4_analysis as base
from scripts import phase4b_recipient_analysis as prior

PROTOCOL_PATH = ROOT / "rejudge/phase5_protocol_v1.json"
PROTOCOL_SHA = "c611aa62d7b5290cc240e82a881488c3ce309b26ce33ab3979c3e09838043f07"
QWEN, LLAMA = base.QWEN, base.LLAMA
MODELS = (QWEN, LLAMA)
CONTEXTS = ("empty", "qwen_history", "llama_history")
PROMPTS = ("ordinary", "scope")
ARMS = tuple(p + "_" + h for p in PROMPTS for h in CONTEXTS)
SEED_NAMESPACE = "phase5-evidence-scope-verdict-v1"
ORDER_SEED, ANALYSIS_SEED = 2026091209, 2026091208
MODEL_SETTINGS = {QWEN: {"temperature": .3, "max_tokens": 16384, "stream": False},
                  LLAMA: {"temperature": .3, "max_tokens": 512, "stream": False}}
OUTPUT_NAMES = ("packets.jsonl", "calls.jsonl", "units_private.jsonl", "prompt_edits_private.jsonl")


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


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def unique(rows, key):
    result = {row[key]: row for row in rows}
    require(len(result) == len(rows), f"Duplicate {key}")
    return result


def load_protocol():
    data = PROTOCOL_PATH.read_bytes()
    require(digest(data) == PROTOCOL_SHA, "Frozen Phase5 protocol differs")
    return json.loads(data)


def verdict_seed(unit_id, judge):
    return int(digest((SEED_NAMESPACE + "|" + unit_id + "|" + judge).encode())[:8], 16) % 2147483647


def build_panel(units, empty_packets, repaired_packets, protocol):
    """Pure construction seam; source hash and complete-frame checks are in prepare."""
    by_unit = unique(units, "unit_id")
    empty, repaired = unique(empty_packets, "packet_id"), unique(repaired_packets, "packet_id")
    require(set(empty) == {uid + ":empty" for uid in by_unit}, "Empty source panel incomplete or extra")
    require(set(repaired) == {uid + ":" + h + "_repaired" for uid in by_unit for h in CONTEXTS[1:]},
            "Reviewed history panel incomplete or extra")
    suffix = protocol["intervention"]["scope_suffix"]
    require(isinstance(suffix, str) and suffix.startswith("\n\n") and bool(suffix.strip()), "Missing fixed scope suffix")
    packets, calls, edits = [], [], []
    for unit in sorted(units, key=lambda u: (u["question_id"], u["debater"], u["transcript_index"], u["side"])):
        uid = unit["unit_id"]
        shared = None
        source_by_context = {}
        for context in CONTEXTS:
            source = empty[uid + ":empty"] if context == "empty" else repaired[uid + ":" + context + "_repaired"]
            messages = source["messages"]
            require(len(messages) >= 3 and messages[0]["role"] == "system" and messages[1]["role"] == "user"
                    and messages[-1]["role"] == "user", "Source is not a pre-verdict packet")
            require(all(set(m) == {"role", "content"} and isinstance(m["content"], str)
                        and m["role"] in ("system", "user", "assistant") for m in messages), "Invalid source message shape")
            require(message_sha(messages) == source["messages_sha256"], "Source messages hash mismatch")
            if shared is None:
                shared = messages[:2]
            require(messages[:2] == shared, "Empty and reviewed donors differ in shared debate context")
            require(suffix not in messages[0]["content"], "Scope instruction already present in source")
            source_by_context[context] = source
        for prompt in PROMPTS:
            for context in CONTEXTS:
                source = source_by_context[context]
                messages = copy.deepcopy(source["messages"])
                if prompt == "scope":
                    messages[0]["content"] += suffix
                require(messages[1:] == source["messages"][1:], "Non-system message changed")
                expected_system = source["messages"][0]["content"] + (suffix if prompt == "scope" else "")
                require(messages[0] == {"role": "system", "content": expected_system}, "Unexpected system edit")
                arm = prompt + "_" + context
                pid = uid + ":" + arm
                hashed = message_sha(messages)
                packets.append({"packet_id": pid, "messages": messages, "messages_sha256": hashed,
                                "prompt_variant": prompt, "context_arm": context,
                                "source_packet_id": source["packet_id"], "source_messages_sha256": source["messages_sha256"]})
                for judge in MODELS:
                    calls.append({"cell_id": uid + ":" + judge + ":" + arm, "unit_id": uid, "judge": judge,
                                  "arm": arm, "packet_id": pid, "messages_sha256": hashed,
                                  "prompt_variant": prompt, "context_arm": context,
                                  "seed": verdict_seed(uid, judge), **MODEL_SETTINGS[judge]})
        for context, source in source_by_context.items():
            original = source["messages"]
            scoped = copy.deepcopy(original)
            scoped[0]["content"] += suffix
            edits.append({"unit_id": uid, "context_arm": context, "source_packet_id": source["packet_id"],
                          "ordinary_packet_id": uid + ":ordinary_" + context, "scope_packet_id": uid + ":scope_" + context,
                          "ordinary_messages_sha256": source["messages_sha256"], "scope_messages_sha256": message_sha(scoped),
                          "message_index": 0, "operation": "append", "unicode_start": len(original[0]["content"]),
                          "unicode_end": len(scoped[0]["content"]), "suffix_sha256": digest(suffix.encode("utf-8")),
                          "other_messages_unchanged": True})
    grouped = defaultdict(list)
    for call in calls:
        grouped[by_unit[call["unit_id"]]["question_id"]].append(call)
    rng = random.Random(ORDER_SEED)
    questions = sorted(grouped)
    rng.shuffle(questions)
    scheduled = []
    for qid in questions:
        rng.shuffle(grouped[qid])
        scheduled.extend(grouped[qid])
    return packets, scheduled, edits


def prepare(phase4a_inputs: Path, phase4b_inputs: Path):
    phase4a_inputs, phase4b_inputs = phase4a_inputs.resolve(), phase4b_inputs.resolve()
    protocol = load_protocol()
    source_hashes = {str(PROTOCOL_PATH): PROTOCOL_SHA}
    for directory, key in ((phase4a_inputs, "phase4a_manifest_sha256"), (phase4b_inputs, "phase4b_manifest_sha256")):
        require(digest((directory / "manifest.json").read_bytes()) == protocol["sources"][key], "Source manifest changed")
        m = json.loads((directory / "manifest.json").read_bytes())
        source_hashes[str(directory / "manifest.json")] = protocol["sources"][key]
        for name, binding in m["outputs"].items():
            path = directory / name
            require(Path(name).name == name and path.is_file() and not path.is_symlink(), "Invalid source path")
            require(path.stat().st_size == binding["bytes"] and digest(path.read_bytes()) == binding["sha256"],
                    "Source output hash mismatch: " + name)
            source_hashes[str(path)] = binding["sha256"]
    _, units, old_a = base.load_inputs(phase4a_inputs)
    _, repaired_units, old_b = prior.load_inputs(phase4b_inputs)
    require(units == repaired_units and (phase4a_inputs / "units_private.jsonl").read_bytes()
            == (phase4b_inputs / "units_private.jsonl").read_bytes(), "Source unit keys differ")
    require(old_b["provenance"]["repair_labels_sha256"] == protocol["sources"]["repair_labels_sha256"], "Wrong reviewed labels")
    empty = [p for p in read_rows(phase4a_inputs / "packets.jsonl") if p["packet_id"].endswith(":empty")]
    repaired = [p for p in read_rows(phase4b_inputs / "packets.jsonl") if p["packet_id"].endswith("_repaired")]
    packets, calls, edits = build_panel(units, empty, repaired, protocol)
    require(len(packets) == 3936 and len(calls) == 7872 and len(edits) == 1968, "Wrong complete panel size")
    artifacts = {"packets.jsonl": jsonl(packets), "calls.jsonl": jsonl(calls),
                 "units_private.jsonl": (phase4a_inputs / "units_private.jsonl").read_bytes(),
                 "prompt_edits_private.jsonl": jsonl(edits)}
    versions = {}
    for name in ("together", "httpx"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    code = ("scripts/phase5_prepare.py", "scripts/phase4_analysis.py", "scripts/phase4b_recipient_analysis.py")
    manifest = {"schema_version": "phase5_evidence_scope_prepared_panel_v1", "status": "prepared_not_paid_authority",
                "paid_execution_authorized": False, "approved_cap_usd": None,
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "python": sys.version, "library_versions": versions, "gpu_ordinal": None,
                "linker": "not applicable; Python/API study", "source_code_versions": {p: digest((ROOT / p).read_bytes()) for p in code},
                "protocol_sha256": PROTOCOL_SHA, "source_hashes": source_hashes,
                "questions": 82, "units": 656, "packets": 3936, "main_verdict_calls": 7872, "requests": 7872,
                "world_question_counts": old_a["world_question_counts"], "models": list(MODELS), "judges": list(MODELS),
                "arms": list(ARMS), "context_arms": list(CONTEXTS), "prompt_variants": list(PROMPTS),
                "model_settings": MODEL_SETTINGS, "call_seed_namespace": SEED_NAMESPACE,
                "order_seed": ORDER_SEED, "analysis_seed": ANALYSIS_SEED, "schedule": protocol["request_profile"]["schedule"],
                "scope_suffix_sha256": digest(protocol["intervention"]["scope_suffix"].encode("utf-8")),
                "scope_suffix": protocol["intervention"]["scope_suffix"],
                "ordinary_system_sha256": digest(empty[0]["messages"][0]["content"].encode("utf-8")),
                "prompt_edits": {"scope_packets_changed": 1968, "message_index": 0, "operation": "fixed_suffix_append",
                                 "all_other_message_bytes_preserved": True, "both_recipients_share_packets": True},
                "review_mode": "ai_source_review", "independent_human_validation": False,
                "repair_coverage": old_b["repair_coverage"],
                "provenance": {**protocol["sources"], "review_amendment_sha256": old_b["provenance"]["review_amendment_sha256"]},
                "outputs": {name: {"sha256": digest(data), "bytes": len(data)} for name, data in artifacts.items()},
                "input_contract": protocol["intervention"]["information_boundary"], "limitations": protocol["limitations"]}
    require(dict(Counter(c["arm"] for c in calls)) == {arm: 1312 for arm in ARMS}, "Unbalanced arms")
    for path, hashed in source_hashes.items():
        require(digest(Path(path).read_bytes()) == hashed, "Source changed during preparation")
    return artifacts, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase4a-inputs", required=True, type=Path)
    parser.add_argument("--phase4b-inputs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    for source in (ROOT, args.phase4a_inputs.resolve(), args.phase4b_inputs.resolve()):
        require(not out.is_relative_to(source) and not source.is_relative_to(out), "Output must be separate from source archives and Git")
    require(not out.exists(), "Output directory must be new")
    artifacts, manifest = prepare(args.phase4a_inputs, args.phase4b_inputs)
    out.mkdir(parents=True, exist_ok=False)
    for name, data in artifacts.items():
        (out / name).write_bytes(data)
    data = (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    (out / "manifest.json").write_bytes(data)
    print(canonical({"status": manifest["status"], "out": str(out), "calls": len(read_rows(out / "calls.jsonl")),
                     "manifest_sha256": digest(data), "paid_execution_authorized": False}))


if __name__ == "__main__":
    main()
