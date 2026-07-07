"""Re-judge runner CLI. Dry-run by default refuses nothing; live runs REQUIRE --approved-cap."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rejudge import judge_loop
from rejudge.api_client import CapExceededError, RejudgeClient
from rejudge.config import ARMS, DEFAULT_BUDGETS, DEFAULT_REPLICATES, load_protocol
from rejudge.records import utc_now_iso

DEFAULT_ARMS = "clean,both,placebo,na_only,doubled_only"


def _load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def stratified_subset(transcripts, n):
    """Deterministically select the first `n` transcripts, stratified by `world`.

    Groups transcripts by `world`, preserving each world's original file order, then
    round-robins one transcript per world (worlds visited in first-seen order) until `n`
    is reached. Plain `transcripts[:n]` silently degenerates into a single-world prefix
    whenever the source file happens to be sorted/grouped by world (as
    `data/transcripts.jsonl` is: its first 100 rows are 100% `carath_norn`) -- this makes
    the legacy subset representative of every world instead.
    """
    groups: dict = {}
    for tr in transcripts:
        groups.setdefault(tr["world"], []).append(tr)
    world_order = list(groups)          # first-seen order -> deterministic given input order
    result = []
    idx = 0
    while len(result) < n:
        took_any = False
        for w in world_order:
            bucket = groups[w]
            if idx < len(bucket):
                result.append(bucket[idx])
                took_any = True
                if len(result) == n:
                    break
        if not took_any:
            break                        # every world's transcripts exhausted before n
        idx += 1
    return result


def iter_cells(arm_names, budgets, transcripts, replicates, legacy_subset=100):
    cells = []
    for name in arm_names:
        arm_transcripts = (stratified_subset(transcripts, legacy_subset) if name == "legacy"
                           else transcripts)
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
    keys = set()
    skipped = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        try:
            keys.add(json.loads(line)["cell_key"])
        except (json.JSONDecodeError, KeyError):
            # A process killed mid-write can leave a truncated tail line; skip it rather
            # than blocking every future resume.
            skipped += 1
    if skipped:
        print(f"skipped {skipped} malformed line(s) in {out_path}")
    return keys


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

    if args.limit == 0:
        print("REFUSED: --limit 0 is ambiguous ('run nothing' vs 'no limit'); omit --limit "
              "for no limit, or pass a positive value.", file=sys.stderr)
        return 2

    if not args.dry_run and args.approved_cap is None:
        print("REFUSED: live runs require --approved-cap USD (spend policy).", file=sys.stderr)
        return 2

    arm_names = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arm_names if a not in ARMS]
    if unknown:
        print(f"unknown arms: {unknown}", file=sys.stderr)
        return 2

    transcripts = _load_jsonl("data/transcripts.jsonl")
    if args.limit is not None:
        transcripts = transcripts[:args.limit]
    protocol = load_protocol()
    worlds = _world_documents()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path = out_path.parent / "failed_cells.jsonl"

    cells = iter_cells(arm_names, DEFAULT_BUDGETS, transcripts, args.replicates,
                       args.legacy_subset)
    done = load_done_keys(out_path)
    todo = [c for c in cells if c["cell_key"] not in done]
    print(f"{len(cells)} cells, {len(done)} done, {len(todo)} to run "
          f"({'DRY RUN' if args.dry_run else f'cap ${args.approved_cap}'})")

    client = RejudgeClient(approved_cap_usd=args.approved_cap or 0.0, dry_run=args.dry_run,
                           error_log_path=str(out_path.parent / "errors.jsonl"))
    lock = threading.Lock()
    cap_hit = threading.Event()

    def run_cell(cell):
        # Fail-fast on a spend-cap breach: stop taking new work and let in-flight cells
        # drain; any other exception (transient API failure after retries, context-guard
        # trip, unexpected bug) is isolated to this cell so the batch keeps going -- the
        # cell stays absent from out_path, so a later resume retries it.
        if cap_hit.is_set():
            return
        tr = cell["transcript"]
        try:
            rec = judge_loop.run_judgment(tr, worlds[tr["world"]], ARMS[cell["arm"]],
                                          cell["budget"], cell["replicate"], client, protocol)
        except CapExceededError:
            cap_hit.set()
            return
        except Exception as exc:
            with lock:
                with open(failed_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"cell_key": cell["cell_key"], "error": str(exc),
                                        "ts": utc_now_iso()}) + "\n")
            print(f"WARN: cell {cell['cell_key']} failed: {exc}", file=sys.stderr)
            return
        with lock:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(run_cell, todo))

    if cap_hit.is_set():
        print("CAP REACHED — run halted; resume after raising cap.", file=sys.stderr)
        return 3

    print(f"done. total tokens={client.total_tokens} spent=${client.spent_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
