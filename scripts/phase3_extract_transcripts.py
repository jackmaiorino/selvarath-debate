"""Phase-3 engineering gate 3: extract, verify, and freeze the reused phase-2 transcripts.

Phase 3 makes ZERO debater calls (rejudge/phase3_protocol.json decisions.transcript_reuse):
every judged debate replays a phase-2 uncapped blind transcript byte-identically. This script
is the one-time extraction of those transcripts from the phase-2 hash-chained result stores
into two frozen bundle files, plus a verification report giving 100% per-transcript coverage.

Two source stores, both in the same row format as
``rejudge.phase2_canary_order.CellResultStore`` (append-only, hash-chained, one JSON object
per line: cell_key, result, sequence, prev_event_hash, event_hash):

  main   -- E:/selvarath-archive/main-2026-08-06/main_results.jsonl, the phase-2 main run,
            execution identity ba017fe3... . 492 ``debate_transcript`` rows (82 main questions
            x 2 debaters x 3 transcripts each), exactly.
  canary -- E:/selvarath-archive/canary-bridge-2026-08-04/canary_results.jsonl, the bridge
            canary for the 24 held-out questions. 48 ``canary_debate_transcript`` rows
            expected (24 questions x 2 debaters x 1 transcript), but this store is READ, not
            authored by us: if the archive is short, we record what is missing rather than
            fail closed, per the protocol's canary-only-regeneration carve-out.

The event-hash chain is re-verified over the FULL file for both stores (every row, not only
the transcript ones -- a break anywhere invalidates the whole store's provenance) before any
row is trusted. A chain break in EITHER store is a hard refusal: nothing is written.

A main-run shortfall from exactly 492 is also a hard refusal: main transcripts are the ones
that can never be regenerated (transcript_reuse.canary_bundle.materialization only permits
canary-only regeneration), so an incomplete main extraction must never freeze silently.

Deterministic by construction: given the same source files, protocol, and
--derivation-timestamp (defaulted to a fixed value, never wall-clock), two runs produce
byte-identical bundle files. Transcript text is carried through unmodified -- no
normalization, no whitespace changes, no re-encoding (files are written ensure_ascii=False,
matching how the source stores themselves encode non-ASCII transcript text; re-escaping it
through ensure_ascii=True would itself be a re-encoding).

Run from the repo root: python scripts/phase3_extract_transcripts.py
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.phase2_canary_order import CellResultStore  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402
from rejudge.phase2_main_manifest import main_question_ids  # noqa: E402

DEFAULT_MAIN_ARCHIVE = r"E:\selvarath-archive\main-2026-08-06"
DEFAULT_MAIN_STORE = DEFAULT_MAIN_ARCHIVE + r"\main_results.jsonl"
DEFAULT_MAIN_EXECUTION_IDENTITY = (
    "ba017fe3c6753a79236917b932b5434a8b99b9e2de37f0d32d4aca38f6613c1c")

DEFAULT_CANARY_ARCHIVE = r"E:\selvarath-archive\canary-bridge-2026-08-04"
DEFAULT_CANARY_STORE = DEFAULT_CANARY_ARCHIVE + r"\canary_results.jsonl"
# The bridge canary's final execution identity (rejudge/phase2_canary_bridge_completion_
# 2026-08-06.json identity_chain): the caching fix on 2026-08-05 moved the identity mid-run,
# so the store this script reads was written under this later hash, not the launch one.
DEFAULT_CANARY_EXECUTION_IDENTITY = (
    "b5cf274dbf65f1a89dca1302fdb34f58da40a840783ad8480d06650add095180")

MAIN_TRANSCRIPT_KIND = "debate_transcript"
CANARY_TRANSCRIPT_KIND = "canary_debate_transcript"

MAIN_TRANSCRIPTS_PER_PAIR = 3
CANARY_TRANSCRIPTS_PER_PAIR = 1
EXPECTED_MAIN_COUNT = 82 * 2 * MAIN_TRANSCRIPTS_PER_PAIR  # 492
EXPECTED_CANARY_COUNT = 24 * 2 * CANARY_TRANSCRIPTS_PER_PAIR  # 48

DEFAULT_DERIVATION_TIMESTAMP = "2026-08-18T00:00:00Z"

BUNDLE_SCHEMA = "phase3_transcript_bundle_v1"
VERIFICATION_SCHEMA = "phase3_transcript_verification_v1"


class ChainVerificationError(ValueError):
    """Raised when a source store's event-hash chain does not verify."""


class ExtractionError(ValueError):
    """Raised when extracted transcripts fail a completion or uniqueness invariant."""


def iter_chain_verified_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Stream ``path`` as CellResultStore-format JSONL, verifying the chain as it goes.

    Mirrors ``CellResultStore._load`` exactly (same row-hash material, same
    prev_event_hash linkage: see ``rejudge/phase2_canary_order.py``), but yields every row
    instead of only building a cell_key -> result mapping, so callers can also keep a row's
    event_hash and process kinds other than the ones the runtime store cares about. Refuses
    on the first row whose prev_event_hash does not match the running tail, or whose
    recomputed hash does not match the stored event_hash -- the same two tamper signatures
    ``CellResultStore`` itself rejects on load.
    """
    last_hash = "genesis"
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row["prev_event_hash"] != last_hash:
                raise ChainVerificationError(
                    f"{path}: chain broken at sequence {row.get('sequence')} "
                    f"(line {line_no}): prev_event_hash {row['prev_event_hash']!r} does not "
                    f"match the running tail {last_hash!r}")
            if CellResultStore._row_hash(row) != row["event_hash"]:
                raise ChainVerificationError(
                    f"{path}: row tampered at sequence {row.get('sequence')} "
                    f"(line {line_no}): recomputed hash does not match the stored event_hash")
            last_hash = row["event_hash"]
            yield row


def extract_transcripts(path: Path, *, kind: str) -> tuple[list[dict[str, Any]], int, str]:
    """Verify the store's full hash chain, then pull out every row of ``kind``.

    Every row in the file is chain-verified, not only the ones of ``kind``: a break
    anywhere in the store invalidates the whole file's provenance, not just the rows we
    happen to want. Returns (matching_rows, total_rows_verified, final_chain_event_hash).
    """
    matching: list[dict[str, Any]] = []
    total = 0
    last_hash = "genesis"
    for row in iter_chain_verified_rows(path):
        total += 1
        last_hash = row["event_hash"]
        # cell_key is "namespace:kind:hash"; the namespace itself never contains a colon.
        _, row_kind, _ = row["cell_key"].split(":", 2)
        if row_kind == kind:
            matching.append(row)
    return matching, total, last_hash


def _transcript_record(row: dict[str, Any]) -> dict[str, Any]:
    """Build one bundle record from a chain-verified store row.

    ``transcript_payload`` is the row's ``result`` object exactly as parsed -- no field is
    added, removed, or re-typed, and no string inside it is touched.
    """
    payload = row["result"]
    return {
        "question_id": payload["question_id"],
        "world": payload["world"],
        "debater_model": payload["debater_model"],
        "transcript_index": payload["transcript_index"],
        "source_cell_key": row["cell_key"],
        "source_event_hash": row["event_hash"],
        "transcript_sha256": canonical_sha256(payload),
        "transcript_payload": payload,
    }


def _require_unique_triples(records: list[dict[str, Any]], *, label: str) -> None:
    seen: dict[tuple, int] = {}
    for r in records:
        key = (r["question_id"], r["debater_model"], r["transcript_index"])
        if key in seen:
            raise ExtractionError(
                f"{label}: duplicate transcript for (question_id, debater_model, "
                f"transcript_index)={key!r}; the store should never contain this twice")
        seen[key] = 1


def build_bundle(*, name: str, source_archive: str, source_store_path: Path,
                 source_execution_identity: str, source_cell_key_namespace: str | None,
                 source_row_kind: str, records: list[dict[str, Any]], expected_count: int,
                 rows_verified: int, chain_final_event_hash: str, derivation_timestamp: str,
                 missing: list[dict[str, str]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda r: (
        r["question_id"], r["debater_model"], r["transcript_index"]))
    counts_by_world = Counter(r["world"] for r in ordered)
    counts_by_debater = Counter(r["debater_model"] for r in ordered)
    counts_by_world_and_debater = Counter(
        (r["world"], r["debater_model"]) for r in ordered)
    return {
        "schema_version": BUNDLE_SCHEMA,
        "bundle": name,
        "derivation_timestamp_utc": derivation_timestamp,
        "source": {
            "archive_path": source_archive,
            "store_path": str(source_store_path),
            "cell_key_namespace": source_cell_key_namespace,
            "execution_identity_sha256": source_execution_identity,
            "row_kind": source_row_kind,
            "store_rows_chain_verified": rows_verified,
            "store_chain_final_event_hash": chain_final_event_hash,
        },
        "expected_transcript_count": expected_count,
        "actual_transcript_count": len(ordered),
        "counts_by_world": dict(sorted(counts_by_world.items())),
        "counts_by_debater_model": dict(sorted(counts_by_debater.items())),
        "counts_by_world_and_debater_model": {
            f"{world}|{debater}": n
            for (world, debater), n in sorted(counts_by_world_and_debater.items())
        },
        "missing": missing,
        "transcripts": ordered,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write deterministic, human-readable JSON, transcript text untouched.

    ensure_ascii=False so non-ASCII transcript text is written as the same UTF-8 bytes the
    source store itself used (re-escaping it through ensure_ascii=True would itself be a
    re-encoding, which the extraction must not do). Key order is the dict's own
    (deterministic) construction order, not resorted, so the file is a stable function of
    the source stores and CLI arguments alone.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8")


def run(*, main_store: Path, main_archive: str, main_execution_identity: str,
       canary_store: Path, canary_archive: str, canary_execution_identity: str,
       protocol_path: Path, out_dir: Path, derivation_timestamp: str,
       project_root: Path) -> dict[str, Any]:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    cell_key_namespace = protocol.get("cell_key_namespace")
    debaters = sorted(protocol["roster"]["debaters"])
    excluded_qids = sorted(protocol["question_set"]["calibration_excluded_question_ids"])
    main_qids = sorted(main_question_ids(project_root))

    print(f"[1/4] verifying and extracting main store: {main_store}")
    main_rows, main_rows_verified, main_final_hash = extract_transcripts(
        main_store, kind=MAIN_TRANSCRIPT_KIND)
    main_records = [_transcript_record(row) for row in main_rows]
    _require_unique_triples(main_records, label="main")
    print(f"      {main_rows_verified} rows chain-verified, "
          f"{len(main_records)} {MAIN_TRANSCRIPT_KIND!r} transcripts extracted")

    expected_main_triples = {
        (qid, debater, tidx)
        for qid in main_qids for debater in debaters
        for tidx in range(MAIN_TRANSCRIPTS_PER_PAIR)
    }
    found_main_triples = {
        (r["question_id"], r["debater_model"], r["transcript_index"]) for r in main_records}
    missing_main_triples = sorted(expected_main_triples - found_main_triples)
    unexpected_main_triples = sorted(found_main_triples - expected_main_triples)
    if len(main_records) != EXPECTED_MAIN_COUNT:
        raise ExtractionError(
            f"main bundle shortfall: expected exactly {EXPECTED_MAIN_COUNT} "
            f"{MAIN_TRANSCRIPT_KIND!r} transcripts (82 questions x {len(debaters)} debaters "
            f"x {MAIN_TRANSCRIPTS_PER_PAIR}), found {len(main_records)}. Missing "
            f"(question_id, debater_model, transcript_index): {missing_main_triples[:20]}"
            f"{'...' if len(missing_main_triples) > 20 else ''}. Unexpected: "
            f"{unexpected_main_triples[:20]}. Main-question transcripts must never be "
            "regenerated, so this is a hard refusal, not a partial bundle.")
    if missing_main_triples or unexpected_main_triples:
        # Same count, wrong membership -- still a hard refusal, and still diagnosable.
        raise ExtractionError(
            f"main bundle has {EXPECTED_MAIN_COUNT} rows but the wrong (question_id, "
            f"debater_model, transcript_index) set: missing {missing_main_triples}, "
            f"unexpected {unexpected_main_triples}")

    print(f"[2/4] verifying and extracting canary store: {canary_store}")
    canary_rows, canary_rows_verified, canary_final_hash = extract_transcripts(
        canary_store, kind=CANARY_TRANSCRIPT_KIND)
    canary_records = [_transcript_record(row) for row in canary_rows]
    _require_unique_triples(canary_records, label="canary")
    print(f"      {canary_rows_verified} rows chain-verified, "
          f"{len(canary_records)} {CANARY_TRANSCRIPT_KIND!r} transcripts extracted "
          f"(of {EXPECTED_CANARY_COUNT} expected)")

    expected_canary_pairs = {(qid, debater) for qid in excluded_qids for debater in debaters}
    found_canary_pairs = {(r["question_id"], r["debater_model"]) for r in canary_records}
    missing_canary_pairs = sorted(expected_canary_pairs - found_canary_pairs)
    unexpected_canary_pairs = sorted(found_canary_pairs - expected_canary_pairs)
    if unexpected_canary_pairs:
        # Not a completeness gap: transcripts for pairs outside the frozen 24-question x
        # 2-debater grid should never exist in this store at all.
        raise ExtractionError(
            f"canary store contains {CANARY_TRANSCRIPT_KIND!r} rows outside the 24 held-out "
            f"questions x {len(debaters)} debaters grid: {unexpected_canary_pairs}")
    missing_canary = [{"question_id": qid, "debater_model": debater}
                      for qid, debater in missing_canary_pairs]

    print("[3/4] assembling bundles")
    main_bundle = build_bundle(
        name="main", source_archive=main_archive, source_store_path=main_store,
        source_execution_identity=main_execution_identity,
        source_cell_key_namespace=cell_key_namespace, source_row_kind=MAIN_TRANSCRIPT_KIND,
        records=main_records, expected_count=EXPECTED_MAIN_COUNT,
        rows_verified=main_rows_verified, chain_final_event_hash=main_final_hash,
        derivation_timestamp=derivation_timestamp, missing=[])
    canary_bundle = build_bundle(
        name="canary", source_archive=canary_archive, source_store_path=canary_store,
        source_execution_identity=canary_execution_identity,
        source_cell_key_namespace=cell_key_namespace, source_row_kind=CANARY_TRANSCRIPT_KIND,
        records=canary_records, expected_count=EXPECTED_CANARY_COUNT,
        rows_verified=canary_rows_verified, chain_final_event_hash=canary_final_hash,
        derivation_timestamp=derivation_timestamp, missing=missing_canary)

    main_bundle_path = out_dir / "phase3_transcript_bundle_main_2026-08-18.json"
    canary_bundle_path = out_dir / "phase3_transcript_bundle_canary_2026-08-18.json"
    _write_json(main_bundle_path, main_bundle)
    _write_json(canary_bundle_path, canary_bundle)
    main_bundle_sha256 = canonical_sha256(main_bundle)
    canary_bundle_sha256 = canonical_sha256(canary_bundle)

    print("[4/4] writing verification report")
    transcript_entries = []
    for source_name, records in (("main", main_records), ("canary", canary_records)):
        for r in sorted(records, key=lambda r: (
                r["question_id"], r["debater_model"], r["transcript_index"])):
            transcript_entries.append({
                "source": source_name,
                "cell_key_or_row_id": r["source_cell_key"],
                "question_id": r["question_id"],
                "world": r["world"],
                "debater_model": r["debater_model"],
                "transcript_index": r["transcript_index"],
                "source_event_hash": r["source_event_hash"],
                "transcript_sha256": r["transcript_sha256"],
                "verified": True,
            })

    main_coverage_pct = 100.0 * len(main_records) / EXPECTED_MAIN_COUNT
    canary_coverage_pct = (
        100.0 * len(canary_records) / EXPECTED_CANARY_COUNT if EXPECTED_CANARY_COUNT else 0.0)
    verification_report = {
        "schema_version": VERIFICATION_SCHEMA,
        "derivation_timestamp_utc": derivation_timestamp,
        "stores": {
            "main": {
                "store_path": str(main_store),
                "archive_path": main_archive,
                "execution_identity_sha256": main_execution_identity,
                "transcript_kind": MAIN_TRANSCRIPT_KIND,
                "rows_chain_verified": main_rows_verified,
                "chain_verified": True,
                "chain_final_event_hash": main_final_hash,
                "expected_transcript_count": EXPECTED_MAIN_COUNT,
                "actual_transcript_count": len(main_records),
                "coverage_pct": main_coverage_pct,
                "missing": [],
            },
            "canary": {
                "store_path": str(canary_store),
                "archive_path": canary_archive,
                "execution_identity_sha256": canary_execution_identity,
                "transcript_kind": CANARY_TRANSCRIPT_KIND,
                "rows_chain_verified": canary_rows_verified,
                "chain_verified": True,
                "chain_final_event_hash": canary_final_hash,
                "expected_transcript_count": EXPECTED_CANARY_COUNT,
                "actual_transcript_count": len(canary_records),
                "coverage_pct": canary_coverage_pct,
                "missing": missing_canary,
            },
        },
        "coverage": {
            "main": {"expected": EXPECTED_MAIN_COUNT, "actual": len(main_records),
                     "pct": main_coverage_pct},
            "canary": {"expected": EXPECTED_CANARY_COUNT, "actual": len(canary_records),
                      "pct": canary_coverage_pct},
        },
        "transcripts": transcript_entries,
        "bundle_canonical_sha256": {
            "main_bundle": main_bundle_sha256,
            "canary_bundle": canary_bundle_sha256,
        },
        "bundle_paths": {
            "main_bundle": str(main_bundle_path),
            "canary_bundle": str(canary_bundle_path),
        },
    }
    verification_path = out_dir / "phase3_transcript_verification_2026-08-18.json"
    _write_json(verification_path, verification_report)

    return {
        "main_bundle_path": main_bundle_path,
        "canary_bundle_path": canary_bundle_path,
        "verification_path": verification_path,
        "main_bundle_sha256": main_bundle_sha256,
        "canary_bundle_sha256": canary_bundle_sha256,
        "main_count": len(main_records),
        "canary_count": len(canary_records),
        "missing_canary": missing_canary,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--main-store", default=DEFAULT_MAIN_STORE)
    ap.add_argument("--main-archive", default=DEFAULT_MAIN_ARCHIVE)
    ap.add_argument("--main-execution-identity", default=DEFAULT_MAIN_EXECUTION_IDENTITY)
    ap.add_argument("--canary-store", default=DEFAULT_CANARY_STORE)
    ap.add_argument("--canary-archive", default=DEFAULT_CANARY_ARCHIVE)
    ap.add_argument("--canary-execution-identity", default=DEFAULT_CANARY_EXECUTION_IDENTITY)
    ap.add_argument("--protocol", default=str(REPO_ROOT / "rejudge" / "phase2_protocol.json"))
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "rejudge"))
    ap.add_argument("--derivation-timestamp", default=DEFAULT_DERIVATION_TIMESTAMP)
    ap.add_argument("--project-root", default=str(REPO_ROOT))
    args = ap.parse_args(argv)

    try:
        result = run(
            main_store=Path(args.main_store), main_archive=args.main_archive,
            main_execution_identity=args.main_execution_identity,
            canary_store=Path(args.canary_store), canary_archive=args.canary_archive,
            canary_execution_identity=args.canary_execution_identity,
            protocol_path=Path(args.protocol), out_dir=Path(args.out_dir),
            derivation_timestamp=args.derivation_timestamp,
            project_root=Path(args.project_root))
    except (ChainVerificationError, ExtractionError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    print()
    print("=== summary ===")
    print(f"main transcripts:   {result['main_count']} / {EXPECTED_MAIN_COUNT}")
    print(f"canary transcripts: {result['canary_count']} / {EXPECTED_CANARY_COUNT}")
    if result["missing_canary"]:
        print(f"canary missing (question_id, debater_model): {result['missing_canary']}")
    print(f"main bundle:   {result['main_bundle_path']}")
    print(f"  canonical_sha256: {result['main_bundle_sha256']}")
    print(f"canary bundle: {result['canary_bundle_path']}")
    print(f"  canonical_sha256: {result['canary_bundle_sha256']}")
    print(f"verification report: {result['verification_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
