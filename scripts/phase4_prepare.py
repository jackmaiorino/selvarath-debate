"""Materialize a proposed Phase 4A replay panel offline. No provider client or dispatch.

Raw prompts and answer keys are written outside Git. Source Phase 3 files are read-only.
Repeated builds with the same inputs produce identical output bytes.
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
from rejudge import phase3_plan
from scripts import phase3_main_analysis as frozen

RESULT_SHA = "8ab79a6cd0987e4bdbdf1054f0729c80c72b4fabf38791c65a5bd528901ebdc3"
ENGINE_SHA = "9cc05c1cc5b5974360ec431d76536fb47017e7ddaeaeb957c595480485df1297"
SAMPLE_SEED = "phase4-history-crossover-v1-2026-09-12"
ORDER_SEED = 2026091201
CALL_SEED_NAMESPACE = "phase4a-verdict-v1"
QWEN = "Qwen/Qwen3.8-2.4T-A95B"
LLAMA = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
JUDGES = (QWEN, LLAMA)
ARMS = ("empty", "qwen_history", "llama_history")


def canonical(obj):
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(canonical(row) + "\n")


def history(result):
    messages = result["judge_messages"]
    assert messages[-1] == {"role": "assistant", "content": result["raw_verdict_text"]}
    assert messages[-2]["role"] == "user"
    return messages[:-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    assert not out.is_relative_to(ROOT), "Raw experiment inputs must stay outside Git"
    assert not out.is_relative_to(args.archive.resolve()), "Keep formal Phase 3 archive unchanged"
    out.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)

    phase4_path = ROOT / "rejudge/phase4_protocol_v1.json"
    phase4 = json.loads(phase4_path.read_bytes())
    assert phase4["paid_execution_authorized"] is False
    assert phase4["stage_4a"]["panel"]["sample_seed"] == SAMPLE_SEED
    assert phase4["stage_4a"]["panel"]["units"] == 656
    assert phase4["stage_4a"]["schedule"]["seed"] == ORDER_SEED
    assert phase4["stage_4a"]["judges"] == list(JUDGES)
    assert phase4["stage_4a"]["arms"] == list(ARMS)
    decoding = phase4["stage_4a"]["decoding"]
    assert decoding["seed_namespace"] == CALL_SEED_NAMESPACE

    source_paths = [args.archive / n for n in ("main_results.jsonl", "main_finalization.json", "main_completion.json")]
    source_hashes = {str(p): sha(p) for p in source_paths}
    assert source_hashes[str(source_paths[0])] == RESULT_SHA
    assert sha(Path(frozen.__file__)) == ENGINE_SHA
    completion = json.loads(source_paths[2].read_bytes())
    assert completion["status"] == "complete"
    admission = json.loads(source_paths[1].read_bytes())
    protocol_path = ROOT / "rejudge/phase3_protocol_v3_r6.json"
    protocol = phase3_plan.load_protocol(protocol_path)
    qids, _ = phase3_plan.load_reference_question_ids(protocol, ROOT)
    plan = phase3_plan.enumerate_cells(protocol, list(JUDGES), qids)
    rows = frozen._read_jsonl(source_paths[0])
    bank = frozen._load_protocol_bound_question_bank(protocol, ROOT)
    records = frozen.build_analysis_records(rows=rows, plan_cells=plan, protocol=protocol,
        question_bank=bank, terminal_cell_keys=admission["partition"]["terminal_cell_keys"],
        context_ineligible_cell_keys=admission["partition"]["context_ineligible_cell_keys"])
    raw = {r["cell_key"]: r["result"] for r in rows}
    by_unit = defaultdict(dict)
    for r in records:
        if r.condition in ("b0", "sequential_b8"):
            unit = (r.question_id, r.debater, r.transcript_index, r.side, r.within_side_replicate)
            by_unit[unit][(r.judge, r.condition)] = r

    eligible, excluded = defaultdict(list), []
    mirror_keys = sorted({u[:3] for u in by_unit})
    for pair in mirror_keys:
        missing = []
        for side in (0, 1):
            arms = by_unit[(*pair, side, 0)]
            for judge in JUDGES:
                for condition in ("b0", "sequential_b8"):
                    rec = arms[(judge, condition)]
                    if rec.origin == "terminal_invalid":
                        missing.append(rec.cell_key)
        if missing:
            excluded.append({"question_id": pair[0], "debater": pair[1], "transcript_index": pair[2],
                             "reason": "A source history is absent; both mirrored sides excluded", "missing_source_cells": missing})
        else:
            eligible[pair[:2]].append(pair)

    def rank(pair):
        encoded = json.dumps(list(pair), ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256((SAMPLE_SEED + "|" + encoded).encode()).hexdigest()

    selected = []
    for stratum, pairs in sorted(eligible.items()):
        assert len(pairs) >= 2, (stratum, len(pairs))
        selected.extend(sorted(pairs, key=rank)[:2])
    assert len(qids) == 82 and len(selected) == 328 and len(excluded) == 2
    packets, units, calls = [], [], []
    for pair in sorted(selected):
        for side in (0, 1):
            unit = (*pair, side, 0)
            unit_id = hashlib.sha256(canonical(list(unit)).encode()).hexdigest()[:24]
            frame = by_unit[unit]
            baseline = history(raw[frame[(QWEN, "b0")].cell_key])
            assert baseline == history(raw[frame[(LLAMA, "b0")].cell_key])
            positions = {r.correct_position for r in frame.values()}
            assert len(positions) == 1 and next(iter(positions)) in ("A", "B")
            units.append({"unit_id": unit_id, "question_id": pair[0], "world": frame[(QWEN,"b0")].world,
                          "debater": pair[1], "transcript_index": pair[2], "side": side,
                          "correct_position": next(iter(positions))})
            for arm in ARMS:
                source = QWEN if arm != "llama_history" else LLAMA
                condition = "b0" if arm == "empty" else "sequential_b8"
                source_record = frame[(source, condition)]
                messages = history(raw[source_record.cell_key])
                assert messages[:2] == baseline[:2]
                message_sha = hashlib.sha256(canonical(messages).encode()).hexdigest()
                packet_id = unit_id + ":" + arm
                packets.append({"packet_id": packet_id, "messages": messages,
                                "messages_sha256": message_sha, "source_cell_key": source_record.cell_key})
                for judge in JUDGES:
                    # Same recorded seed across arms within a unit/recipient; exact provider pairing is not assumed.
                    seed_bytes = (CALL_SEED_NAMESPACE + "|" + unit_id + "|" + judge).encode()
                    seed = int(hashlib.sha256(seed_bytes).hexdigest()[:8], 16) % 2147483647
                    calls.append({"cell_id": unit_id + ":" + judge + ":" + arm,
                                  "unit_id": unit_id, "judge": judge, "arm": arm, "packet_id": packet_id,
                                  "messages_sha256": message_sha, "seed": seed,
                                  "temperature": decoding["temperature"],
                                  "max_tokens": decoding["max_tokens"][judge], "stream": decoding["stream"]})
    assert len(units) == 656 and len(packets) == 1968 and len(calls) == 3936
    # Interleave questions and endpoints; each question's arms are shuffled to reduce calendar-time confounding.
    calls_by_question = defaultdict(list)
    q_by_unit = {u["unit_id"]: u["question_id"] for u in units}
    for call in calls:
        calls_by_question[q_by_unit[call["unit_id"]]].append(call)
    rng = random.Random(ORDER_SEED)
    question_order = sorted(calls_by_question)
    rng.shuffle(question_order)
    schedule = []
    for q in question_order:
        block = calls_by_question[q]
        rng.shuffle(block)
        schedule.extend(block)
    write_jsonl(out / "packets.jsonl", packets)
    write_jsonl(out / "units_private.jsonl", units)
    write_jsonl(out / "calls.jsonl", schedule)
    versions = {}
    for distribution in ("together", "transformers", "tokenizers", "httpx"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    manifest = {"schema_version": "phase4a_prepared_panel_v1", "status": "prepared_not_executable",
        "paid_execution_authorized": False, "approved_cap_usd": None,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "preparation_script_sha256": sha(Path(__file__)), "frozen_engine_sha256": ENGINE_SHA,
        "python": sys.version, "library_versions": versions, "linker": "not applicable; Python/API study", "gpu_ordinal": None,
        "sample_seed": SAMPLE_SEED, "order_seed": ORDER_SEED, "call_seed_namespace": CALL_SEED_NAMESPACE,
        "phase3_run_id": completion["run_id"], "source_hashes": source_hashes,
        "phase4_protocol_path": str(phase4_path), "phase4_protocol_sha256": sha(phase4_path),
        "source_protocol_sha256": sha(protocol_path), "questions": 82, "units": len(units),
        "world_question_counts": dict(Counter(bank[q]["world"] for q in qids)),
        "judges": list(JUDGES), "arms": list(ARMS), "requests": len(calls),
        "requests_per_judge_arm": 656, "source_mirror_pairs_excluded": excluded,
        "sampling": "Two eligible complete source mirror pairs per question/debater by fixed SHA256 rank; no correctness filtering",
        "outputs": {p.name: {"sha256": sha(p), "bytes": p.stat().st_size} for p in
                    (out / "packets.jsonl", out / "units_private.jsonl", out / "calls.jsonl")},
        "input_contract": "Send only packet.messages with the matching call settings. Never send metadata, gold, or saved final verdicts.",
        "limitations": ["Replay of captured history, not rerunning live adaptive query selection",
                        "Packet origin includes content, length, order, retries, blocked slots and repeated summaries",
                        "Original endpoint versions may drift; refresh availability and pin run settings before paid execution"]}
    assert all(sha(p) == digest for p, digest in source_hashes.items())
    write_json(out / "manifest.json", manifest)
    write_json(args.summary, manifest)
    print(json.dumps({"units": len(units), "packets": len(packets), "requests": len(calls),
                      "manifest_sha256": sha(out / "manifest.json"), "formal_sources_unchanged": True,
                      "out": str(out)}))


if __name__ == "__main__":
    main()
