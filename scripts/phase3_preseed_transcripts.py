"""Phase-3 engineering gate 3 (runtime half): pre-seed the hash-chained cell-result store.

Phase 3 makes ZERO debater calls (``rejudge/phase3_protocol.json``
``decisions.transcript_reuse``): every judged transcript is a phase-2 uncapped blind debate
reused byte-identical, already extracted and frozen into the two bundles
``phase3_transcript_bundle_main_2026-08-18.json`` and
``phase3_transcript_bundle_canary_2026-08-18.json`` by
``scripts/phase3_extract_transcripts.py``. Both bundles live in the archive directory
:data:`DEFAULT_BUNDLE_DIR` (out of the repo -- they carry eval-world debate text that must never
be committed publicly), not under ``rejudge/``. This script is the other half of
``transcript_reuse.ingestion_mechanics``: it writes one hash-chained
:class:`rejudge.phase2_canary_order.CellResultStore` row per transcript, keyed under the exact
phase-3 cell key the plan enumerator (:mod:`rejudge.phase3_plan`) computes for it, so a real
run's completed-cell skip logic (``store.is_complete(cell_key)``) is already satisfied for
every transcript dependency before the first judgment cell is attempted. Combined with the
:class:`rejudge.phase2_canary_execute.GenerationForbiddenError` guard, a transcript cell can
never reach a live provider call in a phase-3 run: either it is already recorded here, or the
executor refuses before dispatch.

Cell keys are derived through :func:`rejudge.phase3_plan.enumerate_cells` /
``enumerate_canary_cells`` -- the SAME enumerator-side code path the plan (and therefore any
execution manifest bound to it) uses -- never re-derived by calling ``make_cell_key`` by hand.
Transcript-reference cells are independent of the judge roster (they are built in a loop over
``(debater_model, question_id, transcript_index)`` alone, before the roster loop even starts),
so any protocol-legitimate roster of the minimum candidate size is sufficient to enumerate them;
:data:`_PROBE_ROSTER_SIZE` exists only to keep that enumeration cheap, not because the roster
choice affects which transcript cell keys come out.

Three fail-closed properties, all enforced before a single row is written:

* **Bundle integrity.** Each bundle's own canonical hash is re-verified against
  ``rejudge/phase3_transcript_verification_2026-08-18.json``'s ``bundle_canonical_sha256``
  before anything else happens; a mismatch refuses outright.
* **Coverage.** Every transcript-reference cell the enumerator produces must have a matching
  bundle record, and vice versa; either direction of drift refuses.
* **Target chain integrity.** ``CellResultStore``'s own constructor re-verifies the existing
  chain (row hashes, sequence linkage) before this script appends anything; a target whose
  existing chain does not verify raises there, reused rather than reimplemented.

Idempotent: a cell key already recorded in the target store is skipped, never re-recorded (the
store itself also refuses a second ``record()`` for the same key), so re-running against a
fully-seeded store writes nothing and leaves the file byte-for-byte unchanged.

Run from the repo root: python scripts/phase3_preseed_transcripts.py --target-store PATH
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_plan  # noqa: E402
from rejudge.phase2_canary_order import CellResultStore  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402

DEFAULT_PROTOCOL_PATH = REPO_ROOT / "rejudge" / "phase3_protocol.json"
# Out of the repo: the two bundles carry eval-world debate text that must never be committed
# publicly, so they live in this archive directory rather than under rejudge/.
DEFAULT_BUNDLE_DIR = Path("E:/selvarath-archive/phase3-materialization-2026-08-18")
DEFAULT_MAIN_BUNDLE_PATH = DEFAULT_BUNDLE_DIR / "phase3_transcript_bundle_main_2026-08-18.json"
DEFAULT_CANARY_BUNDLE_PATH = DEFAULT_BUNDLE_DIR / "phase3_transcript_bundle_canary_2026-08-18.json"
DEFAULT_VERIFICATION_REPORT_PATH = (
    REPO_ROOT / "rejudge" / "phase3_transcript_verification_2026-08-18.json")

KEY_FIELDS: tuple[str, ...] = ("debater_model", "question_id", "transcript_index")

# See the module docstring: transcript-reference cell keys never depend on the judge roster, so
# the minimum protocol-legitimate roster (the 4 continuing judges) is enough to derive them
# through the real enumerator as cheaply as possible.
_PROBE_ROSTER_SIZE = 4


class PreseedError(ValueError):
    """Raised when a bundle or the plan it is checked against fail to agree, before any write."""


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_bundle_hash(bundle: Any, *, expected: str, label: str) -> None:
    observed = canonical_sha256(bundle)
    if observed != expected:
        raise PreseedError(
            f"{label} does not match the verification report's pinned canonical hash: "
            f"observed {observed}, expected {expected}")


def _transcript_cell_index(cells: Sequence[dict[str, Any]], *, kind: str) -> dict[tuple, str]:
    return {
        tuple(cell[field] for field in KEY_FIELDS): str(cell["cell_key"])
        for cell in cells if cell["kind"] == kind
    }


def _rows_for_bundle(
    bundle: Any, *, kind: str, cells: Sequence[dict[str, Any]], label: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Verify each bundle record and pair it with its enumerator-derived cell key.

    Refuses (never silently drops or pads) on either direction of drift between the bundle and
    the plan: a bundle record with no matching enumerated cell, or an enumerated transcript cell
    with no matching bundle record. Returns rows in the bundle's own (already sorted) order, so
    the store ends up written in a stable, deterministic order across runs.
    """
    index = _transcript_cell_index(cells, kind=kind)
    rows: list[tuple[str, dict[str, Any]]] = []
    covered: set[str] = set()
    for entry in bundle["transcripts"]:
        payload = entry["transcript_payload"]
        observed = canonical_sha256(payload)
        if observed != entry["transcript_sha256"]:
            raise PreseedError(
                f"{label}: transcript payload for "
                f"{tuple(entry[f] for f in KEY_FIELDS)!r} does not match its own recorded "
                f"hash: observed {observed}, expected {entry['transcript_sha256']}")
        key = tuple(entry[field] for field in KEY_FIELDS)
        cell_key = index.get(key)
        if cell_key is None:
            raise PreseedError(
                f"{label}: transcript record {key!r} has no matching {kind!r} cell in the "
                "phase3_plan enumeration; the bundle and the frozen protocol have drifted")
        result = dict(payload)
        result["cell_key"] = cell_key
        rows.append((cell_key, result))
        covered.add(cell_key)

    missing = sorted(set(index.values()) - covered)
    if missing:
        raise PreseedError(
            f"{label}: {len(missing)} enumerated {kind!r} cell(s) have no matching bundle "
            f"record, e.g. {missing[:5]!r}; pre-seeding would leave a transcript dependency "
            "unsatisfied")
    return rows


def preseed(*, protocol_path: str | Path, project_root: str | Path = ".",
           main_bundle_path: str | Path | None = None,
           canary_bundle_path: str | Path | None = None,
           verification_report_path: str | Path | None = None,
           target_store_path: str | Path) -> dict[str, Any]:
    """Pre-seed ``target_store_path`` with every phase-3 transcript-reference cell's row.

    ``protocol_path`` is injected, matching :mod:`rejudge.phase3_manifest`'s convention: it
    flows straight into :func:`rejudge.phase3_plan.load_protocol`, which fails closed on any
    schema or hash drift from the frozen document before anything else in this function runs.
    """
    root = Path(project_root)
    main_bundle_path = Path(main_bundle_path) if main_bundle_path else DEFAULT_MAIN_BUNDLE_PATH
    canary_bundle_path = (
        Path(canary_bundle_path) if canary_bundle_path else DEFAULT_CANARY_BUNDLE_PATH)
    verification_report_path = (
        Path(verification_report_path) if verification_report_path
        else DEFAULT_VERIFICATION_REPORT_PATH)

    protocol = phase3_plan.load_protocol(protocol_path)
    main_question_ids, held_out_question_ids = phase3_plan.load_reference_question_ids(
        protocol, root)
    probe_roster = phase3_plan.candidate_roster_judges(protocol, _PROBE_ROSTER_SIZE)

    verification_report = _json(verification_report_path)
    expected_hashes = verification_report["bundle_canonical_sha256"]

    main_bundle = _json(main_bundle_path)
    _verify_bundle_hash(
        main_bundle, expected=expected_hashes["main_bundle"], label=str(main_bundle_path))
    canary_bundle = _json(canary_bundle_path)
    _verify_bundle_hash(
        canary_bundle, expected=expected_hashes["canary_bundle"], label=str(canary_bundle_path))

    main_cells = phase3_plan.enumerate_cells(protocol, probe_roster, main_question_ids)
    canary_cells = phase3_plan.enumerate_canary_cells(
        protocol, probe_roster, held_out_question_ids)

    main_rows = _rows_for_bundle(
        main_bundle, kind=phase3_plan.MAIN_TRANSCRIPT_KIND, cells=main_cells,
        label=str(main_bundle_path))
    canary_rows = _rows_for_bundle(
        canary_bundle, kind=phase3_plan.CANARY_TRANSCRIPT_KIND, cells=canary_cells,
        label=str(canary_bundle_path))

    # Re-verifies the existing chain (row hashes, sequence linkage) if the target already
    # exists, raising ValueError before a single new row can be appended -- reused from
    # rejudge.phase2_canary_order, never reimplemented. Nothing has been written by this
    # function before this point.
    store = CellResultStore(target_store_path)

    written = {"main": 0, "canary": 0}
    skipped = {"main": 0, "canary": 0}
    for label, rows in (("main", main_rows), ("canary", canary_rows)):
        for cell_key, result in rows:
            if store.is_complete(cell_key):
                skipped[label] += 1
                continue
            store.record(cell_key, result)
            written[label] += 1

    return {
        "target_store_path": str(target_store_path),
        "main_bundle_count": len(main_rows), "canary_bundle_count": len(canary_rows),
        "written": written, "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--protocol", default=str(DEFAULT_PROTOCOL_PATH))
    ap.add_argument("--project-root", default=str(REPO_ROOT))
    ap.add_argument("--main-bundle", default=str(DEFAULT_MAIN_BUNDLE_PATH))
    ap.add_argument("--canary-bundle", default=str(DEFAULT_CANARY_BUNDLE_PATH))
    ap.add_argument("--verification-report", default=str(DEFAULT_VERIFICATION_REPORT_PATH))
    ap.add_argument("--target-store", required=True)
    args = ap.parse_args(argv)

    try:
        result = preseed(
            protocol_path=Path(args.protocol), project_root=Path(args.project_root),
            main_bundle_path=Path(args.main_bundle), canary_bundle_path=Path(args.canary_bundle),
            verification_report_path=Path(args.verification_report),
            target_store_path=Path(args.target_store))
    except (PreseedError, phase3_plan.ProtocolValidationError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    print(f"target store: {result['target_store_path']}")
    print(f"main:   {result['written']['main']} written, {result['skipped']['main']} "
          f"already present (of {result['main_bundle_count']})")
    print(f"canary: {result['written']['canary']} written, {result['skipped']['canary']} "
          f"already present (of {result['canary_bundle_count']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
