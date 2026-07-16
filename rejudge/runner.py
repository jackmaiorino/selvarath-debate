"""Re-judge runner CLI. Dry-run by default refuses nothing; live runs REQUIRE --approved-cap."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
import os
import sys
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rejudge import judge_loop
from rejudge.api_client import (AccountingInvariantError, CapExceededError,
                                UsageLedgerError)
from rejudge.config import ARMS, DEFAULT_BUDGETS, DEFAULT_REPLICATES, JUDGE_MODEL, load_protocol
from rejudge.records import utc_now_iso
from rejudge.run_accounting import (DEFAULT_PRICE_SCHEDULE, PriceScheduleError,
                                    create_accounted_client, load_price_schedule,
                                    prepare_usage_ledger, pricing_identity,
                                    select_model_prices,
                                    usage_ledger_generated_paths,
                                    usage_log_path_for)
from rejudge.run_manifest import (RunManifestError, ensure_run_manifest,
                                  manifest_path_for, output_lock)

DEFAULT_ARMS = "clean,both,placebo,na_only,doubled_only"
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PERSISTENCE_EXIT = 5


class OutputPersistenceError(RuntimeError):
    """A charged result cannot be durably appended to its manifested output."""


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


def require_unique_planned_cell_keys(keys, *, label: str = "planned grid") -> set[str]:
    """Validate that a run plan has one non-empty string key per charged result."""
    key_list = list(keys)
    invalid = [index for index, key in enumerate(key_list)
               if not isinstance(key, str) or not key]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for key in key_list:
        if isinstance(key, str) and key:
            if key in seen:
                duplicates.add(key)
            seen.add(key)
    if invalid or duplicates:
        details = []
        if invalid:
            details.append(f"invalid key index(es) {invalid[:10]}")
        if duplicates:
            details.append(f"duplicate key(s) {sorted(duplicates)[:10]}")
        raise RunManifestError(f"{label} is not exactly keyed: {'; '.join(details)}")
    return seen


def audit_jsonl_completion(out_path, expected_keys) -> set[str]:
    """Strictly audit an output before resume or an exact-completion claim.

    Unlike :func:`load_done_keys`, this rejects malformed rows, duplicate keys, and rows
    outside the manifested grid. A crash-truncated tail therefore fails closed before any
    new paid call; its bytes remain in place for operator inspection and repair.
    """
    path = Path(out_path)
    expected = set(expected_keys)
    if not path.exists():
        return set()

    completed: set[str] = set()
    issues: list[str] = []
    issue_count = 0

    def record_issue(detail: str) -> None:
        nonlocal issue_count
        issue_count += 1
        if len(issues) < 10:
            issues.append(detail)

    def reject_nonfinite(constant: str):
        raise ValueError(f"non-finite JSON number {constant}")

    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    record_issue(f"line {line_number} is blank")
                    continue
                try:
                    payload = json.loads(line, parse_constant=reject_nonfinite)
                except (json.JSONDecodeError, ValueError) as exc:
                    record_issue(
                        f"line {line_number} is malformed JSON "
                        f"({getattr(exc, 'msg', str(exc))})")
                    continue
                if not isinstance(payload, dict):
                    record_issue(f"line {line_number} is not a JSON object")
                    continue
                cell_key = payload.get("cell_key")
                if not isinstance(cell_key, str) or not cell_key:
                    record_issue(f"line {line_number} has no non-empty string cell_key")
                    continue
                if cell_key in completed:
                    record_issue(f"line {line_number} duplicates cell_key {cell_key!r}")
                    continue
                completed.add(cell_key)
                if cell_key not in expected:
                    record_issue(f"line {line_number} has unexpected cell_key {cell_key!r}")
    except (OSError, UnicodeError) as exc:
        raise OutputPersistenceError(
            f"could not audit manifested output {path}: {exc}") from exc

    if issue_count:
        suffix = (f"; plus {issue_count - len(issues)} more issue(s)"
                  if issue_count > len(issues) else "")
        raise OutputPersistenceError(
            f"strict completion audit failed for {path}: {'; '.join(issues)}{suffix}")
    return completed


def ensure_jsonl_append_boundary(out_path) -> None:
    """Add a record separator after a crash-truncated JSONL tail.

    The old bytes are preserved for audit.  Without this separator, the next paid result
    would be concatenated to the tail, making both rows unreadable and forcing repeat spend
    on every resume.
    """
    path = Path(out_path)
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r+b") as stream:
        stream.seek(-1, 2)
        if stream.read(1) != b"\n":
            stream.seek(0, 2)
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())


def prepare_jsonl_output(out_path) -> None:
    """Repair the append boundary and prove the output is durably writable.

    This runs before the client can admit a paid request.  Creating an empty output is
    intentional: an adjacent manifest proves its identity, while this preflight proves
    that charged results have somewhere writable to land.
    """
    path = Path(out_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ensure_jsonl_append_boundary(path)
        with path.open("ab") as stream:
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise OutputPersistenceError(
            f"could not preflight manifested output {path}: {exc}") from exc


def append_jsonl_record(out_path, record: dict) -> None:
    """Append one complete JSONL row and fsync it before reporting success."""
    path = Path(out_path)
    try:
        payload = (json.dumps(record, allow_nan=False) + "\n").encode("utf-8")
        with path.open("ab") as stream:
            written = stream.write(payload)
            if written != len(payload):
                raise OSError(
                    f"short write: wrote {written} of {len(payload)} bytes")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, TypeError, ValueError) as exc:
        raise OutputPersistenceError(
            f"could not durably append charged result to {path}: {exc}") from exc


def _world_documents(source_files: dict[str, Path] | None = None):
    """Load the exact world-file set supplied by the caller.

    Main runners enumerate the set once, then both hash and parse those paths. This avoids
    a newly added glob match being parsed without appearing in the source manifest.
    """
    docs = {}
    paths = ((REPO_ROOT / "world_specs").glob("*.txt")
             if source_files is None else source_files.values())
    for f in sorted(Path(path) for path in paths):
        docs[f.stem] = f.read_text(encoding="utf-8")
    return docs


def _world_source_files() -> dict[str, Path]:
    """Return the exact world-spec files consumed by :func:`_world_documents`."""
    return {f"world_spec:{path.stem}": path
            for path in sorted((REPO_ROOT / "world_specs").glob("*.txt"))}


def capture_source_hashes(source_files: Mapping[str, Path | str]) -> dict[str, str]:
    """Hash a stable snapshot of every source named in a run manifest."""
    snapshots = {}
    for name, raw_path in sorted(source_files.items()):
        path = Path(raw_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        try:
            before = path.stat()
            payload = path.read_bytes()
            after = path.stat()
        except OSError as exc:
            raise RunManifestError(f"could not snapshot source {name!r} at {path}: {exc}") from exc
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RunManifestError(f"source changed while being read: {path}")
        snapshots[name] = hashlib.sha256(payload).hexdigest()
    return snapshots


def require_unchanged_source_snapshot(
        before: dict[str, str], source_files: Mapping[str, Path | str]) -> dict[str, str]:
    """Return post-load hashes, refusing if any parsed source changed."""
    after = capture_source_hashes(source_files)
    changed = sorted(name for name in set(before) | set(after)
                     if before.get(name) != after.get(name))
    if changed:
        raise RunManifestError(
            f"source files changed while runtime inputs were loaded: {', '.join(changed)}")
    return after


def require_manifest_source_snapshot(manifest: dict, loaded_source_hashes: dict[str, str]) -> None:
    """Refuse when the manifest hashed bytes other than the parsed runtime snapshot."""
    manifested = manifest["identity"]["source_files"]
    changed = sorted(
        name for name, digest in loaded_source_hashes.items()
        if name not in manifested or manifested[name]["sha256"] != digest)
    if changed:
        raise RunManifestError(
            "manifested source bytes differ from the parsed runtime snapshot: "
            + ", ".join(changed))


def acquire_output_locks(stack: ExitStack, paths) -> None:
    """Acquire a globally ordered set of output/ledger locks without deadlock."""
    unique = {Path(path).resolve() for path in paths}
    for path in sorted(unique, key=lambda item: str(item).casefold()):
        stack.enter_context(output_lock(path))


def _lock_and_manifest(
        stack: ExitStack, out_path: Path, *, loaded_source_hashes=None, **identity) -> dict:
    """Acquire *out_path*'s writer lock, then create/validate its manifest."""
    acquire_output_locks(stack, [out_path])
    manifest = ensure_run_manifest(out_path, repo_root=REPO_ROOT, **identity)
    if loaded_source_hashes is not None:
        require_manifest_source_snapshot(manifest, loaded_source_hashes)
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=DEFAULT_ARMS)
    ap.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--approved-cap", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=str(REPO_ROOT / "rejudge" / "output" / "records.jsonl"))
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

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path = out_path.parent / "failed_cells.jsonl"
    usage_path = usage_log_path_for(out_path)
    world_source_files = _world_source_files()
    source_files = {
        "experiment_protocol": REPO_ROOT / "experiment_protocol.json",
        "transcripts": REPO_ROOT / "data" / "transcripts.jsonl",
        "price_schedule": DEFAULT_PRICE_SCHEDULE,
        **world_source_files,
    }
    try:
        before_load = capture_source_hashes(source_files)
        transcripts = _load_jsonl(source_files["transcripts"])
        if args.limit is not None:
            transcripts = transcripts[:args.limit]
        protocol = load_protocol(source_files["experiment_protocol"])
        worlds = _world_documents(world_source_files)
        price_schedule = load_price_schedule()
        model_prices = select_model_prices(
            price_schedule,
            (JUDGE_MODEL, protocol["protocol"]["models"]["oracle"]),
        )
        loaded_source_hashes = require_unchanged_source_snapshot(before_load, source_files)
    except (PriceScheduleError, RunManifestError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    cells = iter_cells(arm_names, DEFAULT_BUDGETS, transcripts, args.replicates,
                       args.legacy_subset)
    try:
        expected = require_unique_planned_cell_keys(
            (cell["cell_key"] for cell in cells), label="rejudge cell grid")
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
            run_kind="rejudge",
            dry_run=args.dry_run,
            models={
                "judge": JUDGE_MODEL,
                "oracle": protocol["protocol"]["models"]["oracle"],
            },
            prices=pricing_identity(price_schedule, model_prices),
            protocol_content={
                "experiment_protocol": protocol,
                "arms": {name: asdict(ARMS[name]) for name in arm_names},
                "budgets": {name: DEFAULT_BUDGETS[name] for name in arm_names},
            },
            source_files=source_files,
            generated_paths=[
                *usage_ledger_generated_paths(usage_path),
                failed_path,
                out_path.parent / "errors.jsonl",
            ],
            cli_params={
                "arms": arm_names,
                "replicates": args.replicates,
                "limit": args.limit,
                "approved_cap_usd": args.approved_cap,
                "dry_run": args.dry_run,
                "workers": args.workers,
                "out": str(out_path.resolve()),
                "usage_log": str(usage_path.resolve()),
                "usage_ledger_identity": ledger_identity,
                "legacy_subset": args.legacy_subset,
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
        todo = [c for c in cells if c["cell_key"] not in done]
        print(f"{len(cells)} cells, {len(done)} done, {len(todo)} to run "
              f"({'DRY RUN' if args.dry_run else f'cap ${args.approved_cap}'})")

        try:
            client, prior_usage = create_accounted_client(
                approved_cap_usd=args.approved_cap or 0.0,
                dry_run=args.dry_run,
                model_prices=model_prices,
                usage_log_path=usage_path,
                error_log_path=out_path.parent / "errors.jsonl",
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

        def run_cell(cell):
            # Fail-fast on a spend-cap breach: stop taking new work and let in-flight cells
            # drain; any other exception (transient API failure after retries, context-guard
            # trip, unexpected bug) is isolated to this cell so the batch keeps going -- the
            # cell stays absent from out_path, so a later resume retries it.
            if cap_hit.is_set() or accounting_failed.is_set() or output_failed.is_set():
                return
            tr = cell["transcript"]
            try:
                rec = judge_loop.run_judgment(
                    tr, worlds[tr["world"]], ARMS[cell["arm"]], cell["budget"],
                    cell["replicate"], client, protocol)
            except CapExceededError:
                cap_hit.set()
                return
            except (UsageLedgerError, AccountingInvariantError) as exc:
                accounting_failed.set()
                print(f"ACCOUNTING UNSAFE: {exc}", file=sys.stderr)
                return
            except Exception as exc:
                with lock:
                    with open(failed_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps({"cell_key": cell["cell_key"], "error": str(exc),
                                            "ts": utc_now_iso()}) + "\n")
                print(f"WARN: cell {cell['cell_key']} failed: {exc}", file=sys.stderr)
                return
            try:
                with lock:
                    append_jsonl_record(out_path, rec)
            except OutputPersistenceError as exc:
                output_failed.set()
                print(f"OUTPUT UNSAFE: {exc}", file=sys.stderr)
                return

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(run_cell, todo))

        try:
            completed = audit_jsonl_completion(out_path, expected)
        except OutputPersistenceError as exc:
            output_failed.set()
            print(f"OUTPUT UNSAFE: {exc}", file=sys.stderr)
            completed = set()
        missing = expected - completed
        print(f"completion check: {len(missing)} missing")

        if accounting_failed.is_set():
            print("ACCOUNTING UNSAFE -- no further calls were admitted; reconcile the "
                  "usage ledger and provider billing before resume.", file=sys.stderr)
            return 4
        if output_failed.is_set():
            print("OUTPUT UNSAFE -- no further calls were admitted; repair the manifested "
                  "output before resume.", file=sys.stderr)
            return OUTPUT_PERSISTENCE_EXIT
        if cap_hit.is_set():
            print("CAP REACHED — run halted. The manifested ceiling is immutable; do not "
                  "raise it in place. Reconcile spend before authorizing a supplemental run.",
                  file=sys.stderr)
            return 3
        if missing:
            return 1

        print(f"done. total tokens={client.total_tokens} spent=${client.spent_usd:.2f}")
        return 0
    finally:
        stack.close()


if __name__ == "__main__":
    raise SystemExit(main())
