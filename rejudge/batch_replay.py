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
from contextlib import ExitStack
from dataclasses import asdict
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rejudge import records
from rejudge.api_client import (AccountingInvariantError, CapExceededError,
                                UsageLedgerError)
from rejudge.config import ArmSpec, JUDGE_MODEL, judgment_seed, load_protocol
from rejudge.judge_loop import _format_transcript
from rejudge.parsers import parse_both
from rejudge.run_accounting import (DEFAULT_PRICE_SCHEDULE, PriceScheduleError,
                                    create_accounted_client, load_price_schedule,
                                    prepare_usage_ledger, pricing_identity,
                                    select_model_prices,
                                    usage_ledger_generated_paths,
                                    usage_log_path_for)
from rejudge.run_manifest import (RunManifestError, ensure_run_manifest,
                                  manifest_path_for)
from rejudge.runner import (OUTPUT_PERSISTENCE_EXIT, REPO_ROOT,
                            OutputPersistenceError, acquire_output_locks,
                            append_jsonl_record, audit_jsonl_completion,
                            capture_source_hashes, prepare_jsonl_output,
                            require_manifest_source_snapshot,
                            require_unique_planned_cell_keys,
                            require_unchanged_source_snapshot)

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
    cell_key = (f"{arm.name}|{source['question_id']}|{source['transcript_index']}|"
                f"{source['budget']}|{source['replicate']}")
    raw = client.complete(messages, JUDGE_MODEL,
                          protocol["protocol"]["temperature"]["judge"],
                          seed + 99999, 512, kind="verdict", request_metadata={
                              "stage": "batch_replay",
                              "cell_key": cell_key,
                              "source_cell_key": source["cell_key"],
                              "call_role": "judge_verdict",
                          })
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
    ap.add_argument(
        "--out", default=str(REPO_ROOT / "rejudge" / "output" / "batch_replay.jsonl"))
    args = ap.parse_args(argv)
    if args.limit == 0:
        print("REFUSED: --limit 0 is ambiguous.", file=sys.stderr)
        return 2
    if not args.dry_run and args.approved_cap is None:
        print("REFUSED: live runs require --approved-cap USD.", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path = usage_log_path_for(out_path)
    source_files = {
        "experiment_protocol": REPO_ROOT / "experiment_protocol.json",
        "transcripts": REPO_ROOT / "data" / "transcripts.jsonl",
        "source_judgments": REPO_ROOT / "rejudge" / "output" / "records.jsonl",
        "price_schedule": DEFAULT_PRICE_SCHEDULE,
    }
    try:
        before_load = capture_source_hashes(source_files)
        protocol = load_protocol(source_files["experiment_protocol"])
        transcripts = {(t["question_id"], t["transcript_index"]): t
                       for t in (json.loads(line) for line in open(
                           source_files["transcripts"], encoding="utf-8"))}
        sources = [record for record in (json.loads(line) for line in open(
            source_files["source_judgments"], encoding="utf-8"))
                   if record["arm"] == "clean" and record["budget"] > 0]
        if args.limit is not None:
            sources = sources[:args.limit]
        price_schedule = load_price_schedule()
        model_prices = select_model_prices(price_schedule, (JUDGE_MODEL,))
        loaded_source_hashes = require_unchanged_source_snapshot(before_load, source_files)
    except (PriceScheduleError, RunManifestError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    jobs = [(source, BATCH) for source in sources]
    jobs += [(source, BATCH_SHUFFLED) for source in sources if source["budget"] == 2]
    try:
        expected = require_unique_planned_cell_keys(
            (f"{arm.name}|{source['question_id']}|{source['transcript_index']}|"
             f"{source['budget']}|{source['replicate']}" for source, arm in jobs),
            label="batch replay grid",
        )
    except RunManifestError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    stack = ExitStack()
    try:
        acquire_output_locks(
            stack, [out_path] if args.dry_run else [out_path, usage_path])
        manifest_preexists = manifest_path_for(out_path).exists()
        ledger_identity = (None if args.dry_run else prepare_usage_ledger(
            usage_path, allow_create=not manifest_preexists))
        manifest = ensure_run_manifest(
            out_path,
            repo_root=REPO_ROOT,
            run_kind="batch-replay",
            dry_run=args.dry_run,
            models={"judge": JUDGE_MODEL},
            prices=pricing_identity(price_schedule, model_prices),
            protocol_content={
                "experiment_protocol": protocol,
                "arms": {arm.name: asdict(arm) for arm in (BATCH, BATCH_SHUFFLED)},
                "batch_budgets": [1, 2, 5],
                "shuffled_budgets": [2],
                "evidence_presentation": "fresh_context_neutral_table",
            },
            source_files=source_files,
            generated_paths=[
                *usage_ledger_generated_paths(usage_path),
                out_path.parent / "batch_failed.jsonl",
                out_path.parent / "batch_errors.jsonl",
            ],
            cli_params={
                "approved_cap_usd": args.approved_cap,
                "dry_run": args.dry_run,
                "limit": args.limit,
                "workers": args.workers,
                "out": str(out_path.resolve()),
                "usage_log": str(usage_path.resolve()),
                "usage_ledger_identity": ledger_identity,
            },
        )
        require_manifest_source_snapshot(manifest, loaded_source_hashes)
    except (RunManifestError, UsageLedgerError) as exc:
        stack.close()
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    try:
        try:
            prepare_jsonl_output(out_path)
            done = audit_jsonl_completion(out_path, expected)
        except OutputPersistenceError as exc:
            print(f"OUTPUT UNSAFE: {exc}", file=sys.stderr)
            return OUTPUT_PERSISTENCE_EXIT
        todo = [(s, a) for s, a in jobs
                if f"{a.name}|{s['question_id']}|{s['transcript_index']}|"
                   f"{s['budget']}|{s['replicate']}" not in done]
        print(f"{len(jobs)} replays, {len(done)} done, {len(todo)} to run "
              f"({'DRY RUN' if args.dry_run else f'cap ${args.approved_cap}'})")

        try:
            client, prior_usage = create_accounted_client(
                approved_cap_usd=args.approved_cap or 0.0,
                dry_run=args.dry_run,
                model_prices=model_prices,
                usage_log_path=usage_path,
                error_log_path=out_path.parent / "batch_errors.jsonl",
                ledger_identity=ledger_identity,
            )
        except (OSError, ValueError, UsageLedgerError) as exc:
            print(f"REFUSED: could not establish cumulative usage ledger: {exc}",
                  file=sys.stderr)
            return 2
        if prior_usage["events"]:
            print(f"prior accounted spend: ${prior_usage['accounted_spend_usd']:.4f} "
                  f"across {prior_usage['events']} ledger events")
        lock = threading.Lock()
        cap_hit = threading.Event()
        accounting_failed = threading.Event()
        output_failed = threading.Event()

        def run(job):
            if cap_hit.is_set() or accounting_failed.is_set() or output_failed.is_set():
                return
            s, arm = job
            try:
                rec = replay_one(s, transcripts[(s["question_id"], s["transcript_index"])],
                                 arm, protocol, client)
            except CapExceededError:
                cap_hit.set()
                return
            except (UsageLedgerError, AccountingInvariantError) as exc:
                accounting_failed.set()
                print(f"ACCOUNTING UNSAFE: {exc}", file=sys.stderr)
                return
            except Exception as exc:
                with lock:
                    with open(out_path.parent / "batch_failed.jsonl", "a",
                              encoding="utf-8") as f:
                        f.write(json.dumps({"source": s["cell_key"], "arm": arm.name,
                                            "error": str(exc)}) + "\n")
                print(f"WARN: {arm.name} {s['cell_key']}: {exc}", file=sys.stderr)
                return
            try:
                with lock:
                    append_jsonl_record(out_path, rec)
            except OutputPersistenceError as exc:
                output_failed.set()
                print(f"OUTPUT UNSAFE: {exc}", file=sys.stderr)
                return

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run, todo))

        try:
            completed = audit_jsonl_completion(out_path, expected)
        except OutputPersistenceError as exc:
            output_failed.set()
            print(f"OUTPUT UNSAFE: {exc}", file=sys.stderr)
            completed = set()
        missing = expected - completed
        print(f"completion check: {len(missing)} missing")

        if accounting_failed.is_set():
            print("ACCOUNTING UNSAFE -- reconcile the usage ledger and provider billing "
                  "before resume.", file=sys.stderr)
            return 4
        if output_failed.is_set():
            print("OUTPUT UNSAFE -- repair the manifested output before resume.",
                  file=sys.stderr)
            return OUTPUT_PERSISTENCE_EXIT
        if cap_hit.is_set():
            print("CAP REACHED: run halted. The manifested ceiling is immutable; reconcile "
                  "spend before authorizing a supplemental run.", file=sys.stderr)
            return 3
        if missing:
            return 1
        print(f"done. total tokens={client.total_tokens} spent=${client.spent_usd:.2f}")
        return 0
    finally:
        stack.close()


if __name__ == "__main__":
    raise SystemExit(main())
