"""Build outcome-free, key-masked source-review packets. Offline only."""
from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(r"E:\selvarath-archive\question-evidence-audit-2026-09-12")
SEED = "question-evidence-audit-2026-09-12-v1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    OUT = parser.parse_args().out.resolve()
    assert not OUT.is_relative_to(ROOT), "Keep key-masked packets and private keys outside Git"
    OUT.mkdir(exist_ok=False)
    protocol_path = ROOT / "rejudge/phase2_protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    qset = protocol["question_set"]
    excluded = set(qset["calibration_excluded_question_ids"])
    source_paths = [protocol_path]
    key = {}
    counts = {}
    for relative in qset["question_sources"]:
        bank_path = ROOT / relative
        bank = json.loads(bank_path.read_text(encoding="utf-8"))
        canonical_sha = hashlib.sha256(json.dumps(bank, sort_keys=True, ensure_ascii=True,
                                                  separators=(",", ":")).encode()).hexdigest()
        assert canonical_sha == protocol["source_bindings"]["canonical_json_sha256"][relative]
        rows = [q for q in bank if q["id"] not in excluded]
        world = rows[0]["world"]
        world_path = ROOT / "world_specs" / f"{world}.txt"
        source_paths.extend([bank_path, world_path])
        questions = []
        for q in sorted(rows, key=lambda q: q["id"]):
            flip = int(hashlib.sha256(f'{SEED}|{q["id"]}'.encode()).hexdigest(), 16) % 2
            choices = [q["correct_answer"], q["wrong_answer"]]
            if flip:
                choices.reverse()
            questions.append({"question_id": q["id"], "question": q["question"],
                              "A": choices[0], "B": choices[1]})
            key[q["id"]] = {"world": world, "gold_option": "B" if flip else "A"}
        lines = world_path.read_text(encoding="utf-8").splitlines()
        dump(OUT / f"{world}_packet.json", {
            "world": world, "source_sha256": sha(world_path),
            "source_lines": [{"line": i + 1, "text": t} for i, t in enumerate(lines)],
            "questions": questions,
        })
        counts[world] = len(questions)
    assert sum(counts.values()) == 82
    # Panel membership only; no verdict file is read.
    units_path = Path(r"E:\selvarath-archive\phase4-preparation-2026-09-12\units_private.jsonl")
    unit_qids = {json.loads(line)["question_id"] for line in units_path.read_text().splitlines()}
    assert unit_qids == set(key)
    source_paths.append(units_path)
    dump(OUT / "key_private.json", key)
    dump(OUT / "manifest.json", {
        "audit": SEED, "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": platform.python_version(), "linker": "not applicable; offline text review",
        "gpu_ordinal": None, "seed": SEED, "world_counts": counts,
        "source_hashes": {str(p): sha(p) for p in source_paths},
        "packet_hashes": {p.name: sha(p) for p in sorted(OUT.glob("*_packet.json"))},
        "key_sha256": sha(OUT / "key_private.json"), "builder_sha256": sha(Path(__file__)),
        "selection": "All 82 main question IDs, exact match to Phase 4/5 panel; no outcome selection",
        "blinding": "Review packets contain only source text, question and permuted candidate text. No key labels, author rationale, facts_required, verdicts, traces or model identifiers.",
        "limitations": ["Root has prior results context and saw some original key rows while preparing; two reviewers use fresh contexts with packets only and each review all three worlds independently.",
                        "Candidate writing style may reveal author preference despite masking labels.",
                        "AI source audit, not independent human validation or a new accuracy measurement."],
        "provider_calls": 0,
    })
    print(json.dumps({"out": str(OUT), "counts": counts, "provider_calls": 0}))


if __name__ == "__main__":
    main()
