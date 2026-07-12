"""Fresh-context (batch) replay of Stage-1 clean-arm Q&A.

For every clean-arm judgment with budget > 0, rebuild the judging conversation as a
FRESH context: system prompt + debate presentation + one verdict prompt containing the
judge's own Q&A as a neutral evidence table. No query-phase turns, no oracle calls.
Contrast with the sequential original isolates packaging/commitment effects (consult
#10b): the information is identical, only its presentation differs.

Arms written (to rejudge/output/batch_replay.jsonl, separate from the main records):
  batch          budgets {1,2,5}, all replicates
  batch_shuffled budget 2 only, exchange order deterministically shuffled (sensitivity)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rejudge import records
from rejudge.api_client import CapExceededError, RejudgeClient
from rejudge.config import ArmSpec, JUDGE_MODEL, judgment_seed, load_protocol
from rejudge.judge_loop import _format_transcript
from rejudge.parsers import parse_both
from rejudge.runner import load_done_keys

BATCH = ArmSpec("batch", "strict", "clean", "robust")
BATCH_SHUFFLED = ArmSpec("batch_shuffled", "strict", "clean", "robust")


def evidence_table(exchanges) -> str:
    lines = [f"Claim {i}: {e['extracted_claim']}\nResult: {e['normalized']}"
             for i, e in enumerate(exchanges, 1)]
    return ("VERIFICATION RESULTS:\n\nThe following factual claims were checked against "
            "the source document:\n\n" + "\n\n".join(lines))


def build_messages(source, transcript, protocol, shuffle_seed=None):
    """Fresh 3-message conversation: system, debate presentation, verdict ask."""
    judge_cfg = protocol["judge"]
    pos_a, pos_b, debate_text = _format_transcript(transcript, source["position_a_is_correct"])
    exchanges = list(source["exchanges"])
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(exchanges)
    return [
        {"role": "system", "content": judge_cfg["system_prompt"]},
        {"role": "user", "content": judge_cfg["user_prompt_template"].format(
            question=transcript["question"], position_a=pos_a, position_b=pos_b,
            debate_transcript=debate_text)},
        {"role": "user", "content": judge_cfg["verdict_prompt"].format(
            query_results=evidence_table(exchanges))},
    ]


def replay_one(source, transcript, arm, protocol, client):
    shuffle_seed = None
    seed = judgment_seed(source["question_id"], source["transcript_index"], JUDGE_MODEL,
                         source["budget"], arm.name, source["replicate"])
    if arm.name == "batch_shuffled":
        shuffle_seed = seed
    messages = build_messages(source, transcript, protocol, shuffle_seed)
    raw = client.complete(messages, JUDGE_MODEL,
                          protocol["protocol"]["temperature"]["judge"],
                          seed + 99999, 512, kind="verdict")
    return records.build_record(
        transcript=transcript, arm=arm, budget=source["budget"],
        replicate=source["replicate"],
        position_a_is_correct=source["position_a_is_correct"],
        exchanges=[{"replayed_from": source["cell_key"], "shuffled": shuffle_seed is not None}],
        raw_verdict_text=raw, parses=parse_both(raw),
        judge_messages=messages + [{"role": "assistant", "content": raw}],
        seed=seed, judge_model=JUDGE_MODEL,
        oracle_model="none (replayed evidence)", dry_run=getattr(client, "dry_run", False),
        queries_used=len(source["exchanges"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approved-cap", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="rejudge/output/batch_replay.jsonl")
    args = ap.parse_args(argv)
    if args.limit == 0:
        print("REFUSED: --limit 0 is ambiguous.", file=sys.stderr)
        return 2
    if not args.dry_run and args.approved_cap is None:
        print("REFUSED: live runs require --approved-cap USD.", file=sys.stderr)
        return 2

    protocol = load_protocol()
    transcripts = {(t["question_id"], t["transcript_index"]): t
                   for t in (json.loads(l) for l in open("data/transcripts.jsonl", encoding="utf-8"))}
    sources = [r for r in (json.loads(l) for l in open("rejudge/output/records.jsonl", encoding="utf-8"))
               if r["arm"] == "clean" and r["budget"] > 0]
    if args.limit is not None:
        sources = sources[:args.limit]

    jobs = [(s, BATCH) for s in sources]
    jobs += [(s, BATCH_SHUFFLED) for s in sources if s["budget"] == 2]

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done_keys(out_path)
    todo = [(s, a) for s, a in jobs
            if f"{a.name}|{s['question_id']}|{s['transcript_index']}|{s['budget']}|{s['replicate']}" not in done]
    print(f"{len(jobs)} replays, {len(done)} done, {len(todo)} to run "
          f"({'DRY RUN' if args.dry_run else f'cap ${args.approved_cap}'})")

    client = RejudgeClient(approved_cap_usd=args.approved_cap or 0.0, dry_run=args.dry_run,
                           error_log_path=str(out_path.parent / "batch_errors.jsonl"))
    lock = threading.Lock()
    cap_hit = threading.Event()

    def run(job):
        if cap_hit.is_set():
            return
        s, arm = job
        try:
            rec = replay_one(s, transcripts[(s["question_id"], s["transcript_index"])],
                             arm, protocol, client)
        except CapExceededError:
            cap_hit.set()
            return
        except Exception as exc:
            with lock:
                with open(out_path.parent / "batch_failed.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps({"source": s["cell_key"], "arm": arm.name,
                                        "error": str(exc)}) + "\n")
            print(f"WARN: {arm.name} {s['cell_key']}: {exc}", file=sys.stderr)
            return
        with lock:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(run, todo))
    if cap_hit.is_set():
        print("CAP REACHED: run halted; resume after raising cap.", file=sys.stderr)
        return 3
    print(f"done. total tokens={client.total_tokens} spent=${client.spent_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
