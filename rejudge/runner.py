"""Re-judge runner CLI. Dry-run by default refuses nothing; live runs REQUIRE --approved-cap."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rejudge import judge_loop
from rejudge.api_client import RejudgeClient
from rejudge.config import ARMS, DEFAULT_BUDGETS, DEFAULT_REPLICATES, load_protocol

DEFAULT_ARMS = "clean,both,placebo,na_only,doubled_only"


def _load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def iter_cells(arm_names, budgets, transcripts, replicates, legacy_subset=100):
    cells = []
    for name in arm_names:
        arm_transcripts = transcripts[:legacy_subset] if name == "legacy" else transcripts
        arm_reps = 1 if name == "legacy" else replicates
        for tr in arm_transcripts:
            for b in budgets[name]:
                for k in range(arm_reps):
                    cells.append({"arm": name, "budget": b, "transcript": tr, "replicate": k,
                                  "cell_key": f"{name}|{tr['question_id']}|"
                                              f"{tr['transcript_index']}|{b}|{k}"})
    return cells


def load_done_keys(out_path) -> set:
    p = Path(out_path)
    if not p.exists():
        return set()
    return {json.loads(l)["cell_key"] for l in p.read_text(encoding="utf-8").splitlines() if l}


def _world_documents():
    docs = {}
    for f in Path("world_specs").glob("*.txt"):
        docs[f.stem] = f.read_text(encoding="utf-8")
    return docs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=DEFAULT_ARMS)
    ap.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--approved-cap", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="rejudge/output/records.jsonl")
    ap.add_argument("--legacy-subset", type=int, default=100)
    args = ap.parse_args(argv)

    if not args.dry_run and args.approved_cap is None:
        print("REFUSED: live runs require --approved-cap USD (spend policy).", file=sys.stderr)
        return 2

    arm_names = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arm_names if a not in ARMS]
    if unknown:
        print(f"unknown arms: {unknown}", file=sys.stderr)
        return 2

    transcripts = _load_jsonl("data/transcripts.jsonl")
    if args.limit:
        transcripts = transcripts[:args.limit]
    protocol = load_protocol()
    worlds = _world_documents()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cells = iter_cells(arm_names, DEFAULT_BUDGETS, transcripts, args.replicates,
                       args.legacy_subset)
    done = load_done_keys(out_path)
    todo = [c for c in cells if c["cell_key"] not in done]
    print(f"{len(cells)} cells, {len(done)} done, {len(todo)} to run "
          f"({'DRY RUN' if args.dry_run else f'cap ${args.approved_cap}'})")

    client = RejudgeClient(approved_cap_usd=args.approved_cap or 0.0, dry_run=args.dry_run,
                           error_log_path=str(out_path.parent / "errors.jsonl"))
    lock = threading.Lock()

    def run_cell(cell):
        tr = cell["transcript"]
        rec = judge_loop.run_judgment(tr, worlds[tr["world"]], ARMS[cell["arm"]],
                                      cell["budget"], cell["replicate"], client, protocol)
        with lock:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(run_cell, todo))
    print(f"done. total tokens={client.total_tokens} spent=${client.spent_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
