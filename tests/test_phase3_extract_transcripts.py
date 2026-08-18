"""scripts/phase3_extract_transcripts.py, proven on synthetic fixture stores.

No test here touches E:/selvarath-archive: every store is a small, hand-built
CellResultStore-format file under tmp_path, so the suite is self-contained and portable.
The main-shortfall test reads the real (repo-tracked, read-only) phase2_protocol.json and
data/transcripts.jsonl to get real question/debater identities, but writes its own tiny
fake main store, never the real 573MB archive.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_execution import canonical_sha256
from rejudge.phase2_main_manifest import main_question_ids
from scripts.phase3_extract_transcripts import (
    ChainVerificationError,
    ExtractionError,
    _require_unique_triples,
    _transcript_record,
    _write_json,
    build_bundle,
    extract_transcripts,
    iter_chain_verified_rows,
    run,
)

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "phase3-test-fixture-v1.qb-deadbeef"


def _payload(question_id: str, debater_model: str, transcript_index: int, *,
            world: str = "testworld", text: str = "hello world") -> dict:
    return {
        "question_id": question_id,
        "transcript_index": transcript_index,
        "world": world,
        "question": "Did the thing happen?",
        "correct_answer": "Yes",
        "wrong_answer": "No",
        "honest_first": True,
        "debate_transcript": [{"speaker": "honest", "text": text, "round": 1}],
        "debater_model": debater_model,
        "protocol": "uncapped3",
        "temperature": 0.7,
        "seed": 1,
        "harness_version": "abc123",
        "created_at": "2026-08-18T00:00:00+00:00",
        "dry_run": False,
        "cell_key": f"uncapped3|{debater_model}|{question_id}|{transcript_index}",
    }


def _cell_key(kind: str, suffix: str) -> str:
    return f"{NAMESPACE}:{kind}:{suffix}"


def _build_store(path: Path, rows: list[tuple[str, dict]]) -> CellResultStore:
    store = CellResultStore(path)
    for cell_key, result in rows:
        store.record(cell_key, result)
    return store


def _mixed_rows() -> list[tuple[str, dict]]:
    """A few transcript rows plus a non-transcript row, so extraction proves it filters by
    kind rather than by coincidence of being the only kind present."""
    return [
        (_cell_key("debate_transcript", "a1"),
         _payload("Q-001", "debater-x", 0, text="opening argument one")),
        (_cell_key("no_debate_judgment", "j1"), {"verdict": "A", "correct": True}),
        (_cell_key("debate_transcript", "a2"),
         _payload("Q-001", "debater-y", 0, text="opening argument two")),
        (_cell_key("debate_transcript", "a3"),
         _payload("Q-002", "debater-x", 1, text="third transcript")),
    ]


# --- chain verification -----------------------------------------------------------------

def test_chain_verification_passes_on_an_untampered_store(tmp_path):
    path = tmp_path / "store.jsonl"
    _build_store(path, _mixed_rows())
    rows = list(iter_chain_verified_rows(path))
    assert len(rows) == 4
    transcripts, total, final_hash = extract_transcripts(path, kind="debate_transcript")
    assert total == 4
    assert len(transcripts) == 3
    assert final_hash == rows[-1]["event_hash"]


def test_a_tampered_row_is_refused(tmp_path):
    """Mutating one row's result without recomputing its hash breaks the chain: both the
    tampered row's own hash and every row after it that chained from the old hash."""
    path = tmp_path / "store.jsonl"
    _build_store(path, _mixed_rows())
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["result"]["debate_transcript"][0]["text"] = "TAMPERED"
    lines[0] = json.dumps(row, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ChainVerificationError):
        list(iter_chain_verified_rows(path))
    with pytest.raises(ChainVerificationError):
        extract_transcripts(path, kind="debate_transcript")


def test_a_broken_prev_hash_link_is_refused(tmp_path):
    """Deleting a middle row breaks the prev_event_hash link even though every remaining
    row's own event_hash still recomputes correctly."""
    path = tmp_path / "store.jsonl"
    _build_store(path, _mixed_rows())
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ChainVerificationError):
        list(iter_chain_verified_rows(path))


# --- determinism and round-tripping -----------------------------------------------------

def test_extraction_is_deterministic(tmp_path):
    path = tmp_path / "store.jsonl"
    _build_store(path, _mixed_rows())

    rows_a, total_a, hash_a = extract_transcripts(path, kind="debate_transcript")
    rows_b, total_b, hash_b = extract_transcripts(path, kind="debate_transcript")
    assert total_a == total_b
    assert hash_a == hash_b
    assert [_transcript_record(r) for r in rows_a] == [_transcript_record(r) for r in rows_b]

    records = [_transcript_record(r) for r in rows_a]
    bundle_a = build_bundle(
        name="test", source_archive="X:/nowhere", source_store_path=path,
        source_execution_identity="deadbeef", source_cell_key_namespace=NAMESPACE,
        source_row_kind="debate_transcript", records=records, expected_count=3,
        rows_verified=total_a, chain_final_event_hash=hash_a,
        derivation_timestamp="2026-08-18T00:00:00Z", missing=[])
    bundle_b = build_bundle(
        name="test", source_archive="X:/nowhere", source_store_path=path,
        source_execution_identity="deadbeef", source_cell_key_namespace=NAMESPACE,
        source_row_kind="debate_transcript", records=records, expected_count=3,
        rows_verified=total_a, chain_final_event_hash=hash_a,
        derivation_timestamp="2026-08-18T00:00:00Z", missing=[])
    assert bundle_a == bundle_b
    assert canonical_sha256(bundle_a) == canonical_sha256(bundle_b)

    out_a, out_b = tmp_path / "bundle_a.json", tmp_path / "bundle_b.json"
    _write_json(out_a, bundle_a)
    _write_json(out_b, bundle_b)
    assert out_a.read_bytes() == out_b.read_bytes()


def test_a_synthetic_store_round_trips_byte_identically(tmp_path):
    """Non-ASCII, embedded quotes/backslashes and internal whitespace all survive the
    extract -> bundle -> write -> reload cycle with no normalization."""
    tricky_text = ('This has an em-dash \u2014 mid-sentence, a "quoted phrase", a backslash '
                   '\\ and\ttab plus  double  spaces, and trailing whitespace   \n')
    path = tmp_path / "store.jsonl"
    _build_store(path, [
        (_cell_key("debate_transcript", "tricky"),
         _payload("Q-TRICKY", "debater-x", 0, world="carath_norn", text=tricky_text)),
    ])
    source_row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    rows, total, final_hash = extract_transcripts(path, kind="debate_transcript")
    assert total == 1
    records = [_transcript_record(r) for r in rows]
    bundle = build_bundle(
        name="test", source_archive="X:/nowhere", source_store_path=path,
        source_execution_identity="deadbeef", source_cell_key_namespace=NAMESPACE,
        source_row_kind="debate_transcript", records=records, expected_count=1,
        rows_verified=total, chain_final_event_hash=final_hash,
        derivation_timestamp="2026-08-18T00:00:00Z", missing=[])

    out_path = tmp_path / "bundle.json"
    _write_json(out_path, bundle)

    raw = out_path.read_bytes()
    assert "\u2014".encode("utf-8") in raw  # written as real UTF-8, not \u2014
    assert rb"\u2014" not in raw

    reloaded = json.loads(out_path.read_text(encoding="utf-8"))
    rec = reloaded["transcripts"][0]
    assert rec["transcript_payload"]["debate_transcript"][0]["text"] == tricky_text
    assert rec["transcript_payload"] == source_row["result"]
    assert rec["source_event_hash"] == source_row["event_hash"]
    assert rec["transcript_sha256"] == canonical_sha256(source_row["result"])


def test_duplicate_transcript_triple_is_refused(tmp_path):
    path = tmp_path / "store.jsonl"
    _build_store(path, [
        (_cell_key("debate_transcript", "d1"), _payload("Q-001", "debater-x", 0)),
        (_cell_key("debate_transcript", "d2"), _payload("Q-001", "debater-x", 0)),
    ])
    rows, _, _ = extract_transcripts(path, kind="debate_transcript")
    records = [_transcript_record(r) for r in rows]
    with pytest.raises(ExtractionError):
        _require_unique_triples(records, label="test")


# --- run(): the full pipeline -------------------------------------------------------------

def test_main_shortfall_hard_fails(tmp_path):
    """A main store with far fewer than 492 debate_transcript rows must refuse outright,
    and must not write any bundle or report file: main transcripts can never be
    regenerated, so a partial main bundle must never freeze."""
    main_store = tmp_path / "main_results.jsonl"
    _build_store(main_store, [
        (_cell_key("debate_transcript", "m1"),
         _payload("CN-001", "meta-llama/Llama-3.3-70B-Instruct-Turbo", 0)),
        (_cell_key("debate_transcript", "m2"),
         _payload("CN-002", "meta-llama/Llama-3.3-70B-Instruct-Turbo", 0)),
    ])
    out_dir = tmp_path / "out"

    with pytest.raises(ExtractionError, match="492"):
        run(main_store=main_store, main_archive="X:/fake-main",
            main_execution_identity="deadbeef",
            canary_store=tmp_path / "does_not_exist_canary.jsonl",
            canary_archive="X:/fake-canary", canary_execution_identity="deadbeef",
            protocol_path=ROOT / "rejudge" / "phase2_protocol.json", out_dir=out_dir,
            derivation_timestamp="2026-08-18T00:00:00Z", project_root=ROOT)

    assert not out_dir.exists()


def test_canary_missing_pairs_are_reported_not_hard_failed(tmp_path):
    """A canary shortfall is recorded in the bundle's `missing` list and the run still
    succeeds, per the protocol's canary-only-regeneration carve-out -- unlike main."""
    protocol = json.loads((ROOT / "rejudge" / "phase2_protocol.json").read_text(
        encoding="utf-8"))
    excluded_qids = sorted(protocol["question_set"]["calibration_excluded_question_ids"])
    debaters = sorted(protocol["roster"]["debaters"])
    main_qids = sorted(main_question_ids(ROOT))

    main_store = tmp_path / "main_results.jsonl"
    main_rows = [
        (_cell_key("debate_transcript", f"m{i}"), _payload(qid, debater, tidx))
        for i, (qid, debater, tidx) in enumerate(
            (qid, debater, tidx)
            for qid in main_qids for debater in debaters for tidx in range(3))
    ]
    _build_store(main_store, main_rows)

    # Drop exactly one (question, debater) pair from the canary store, out of the full
    # 24 x 2 grid, so the bundle should report a shortfall of 47/48 with one missing pair.
    all_pairs = [(qid, debater) for qid in excluded_qids for debater in debaters]
    dropped = all_pairs[0]
    canary_store = tmp_path / "canary_results.jsonl"
    canary_rows = [
        (_cell_key("canary_debate_transcript", f"c{i}"), _payload(qid, debater, 0))
        for i, (qid, debater) in enumerate(all_pairs) if (qid, debater) != dropped
    ]
    _build_store(canary_store, canary_rows)

    out_dir = tmp_path / "out"
    result = run(main_store=main_store, main_archive="X:/fake-main",
                main_execution_identity="deadbeef", canary_store=canary_store,
                canary_archive="X:/fake-canary", canary_execution_identity="deadbeef",
                protocol_path=ROOT / "rejudge" / "phase2_protocol.json", out_dir=out_dir,
                derivation_timestamp="2026-08-18T00:00:00Z", project_root=ROOT)

    assert result["main_count"] == 492
    assert result["canary_count"] == 47
    assert result["missing_canary"] == [
        {"question_id": dropped[0], "debater_model": dropped[1]}]

    report = json.loads(
        (out_dir / "phase3_transcript_verification_2026-08-18.json").read_text(
            encoding="utf-8"))
    assert report["stores"]["canary"]["actual_transcript_count"] == 47
    assert report["stores"]["canary"]["missing"] == [
        {"question_id": dropped[0], "debater_model": dropped[1]}]
    assert report["stores"]["main"]["coverage_pct"] == 100.0
