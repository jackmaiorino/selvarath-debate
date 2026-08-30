"""scripts/phase3_preseed_transcripts.py: pre-seeding the hash-chained result store.

Most of this module runs against a synthetic, tmp_path-local fixture set -- a miniature
plan-enumeration-shaped protocol, two questions, and two tiny transcript bundles -- so nothing
here touches E:/ or needs the real (492+48, multi-megabyte) frozen bundles. The synthetic
protocol is still run through the REAL rejudge.phase3_plan.enumerate_cells /
enumerate_canary_cells enumerator (only ``validate_protocol``'s frozen-document checks, which
only the real ``rejudge/phase3_protocol.json`` can pass, are bypassed), so a passing test here
is a test of the real key-derivation path, not a hand-rolled stand-in for it.

The real bundles are exercised once, directly, by
``test_preseeding_the_real_bundles_is_idempotent_against_a_tmp_store`` -- deliberately reading
the archive (they no longer live in the repo: they were moved out to keep eval-world debate text
out of version control), so that test is skipped on any machine that doesn't have the archive
mounted.
"""
import json
from pathlib import Path

import pytest

from rejudge import phase3_plan
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_execution import canonical_sha256
from scripts.phase3_preseed_transcripts import (
    DEFAULT_MAIN_BUNDLE_PATH,
    PreseedError,
    preseed,
    preseed_canary,
    preseed_main,
)

ROOT = Path(__file__).resolve().parents[1]

MAIN_QUESTION_IDS = ["Q-001", "Q-002"]
HELD_OUT_QUESTION_IDS = ["Q-003"]
DEBATERS = ["debater-a", "debater-b"]
NAMESPACE = "synthetic-phase3.qb-abc123"


def _synthetic_protocol() -> dict:
    """A minimal document shaped enough for enumerate_cells/enumerate_canary_cells alone.

    Not a frozen-protocol fixture: ``validate_protocol`` (which both enumerators call first)
    checks dozens of fields and a whole-document hash only the real, frozen
    ``rejudge/phase3_protocol.json`` can satisfy. The ``fixtures`` fixture below monkeypatches
    ``validate_protocol`` to a no-op for this synthetic document; every field it reads is
    something the two enumerators actually consume.
    """
    return {
        "schema_version": "phase3_plan_v1",
        "cell_key_namespace": NAMESPACE,
        "question_set": {"expected_main_question_count": len(MAIN_QUESTION_IDS),
                         "held_out_question_count": len(HELD_OUT_QUESTION_IDS)},
        "roster": {"debaters": DEBATERS},
        "debate_grid": {
            "k": 1,
            "conditions": [
                {"id": "b0", "query_budget": 0, "oracle_mode": "none",
                 "presentation": "sequential", "judgment_replicates_per_transcript_side": 1},
            ],
        },
        "decisions": {"launch_gates": {"budget_smoke_subset": []}},
    }


def _transcript_payload(question_id: str, debater: str, transcript_index: int) -> dict:
    return {
        "question_id": question_id, "transcript_index": transcript_index,
        "world": "synthetic_world", "question": f"Is {question_id} true?",
        "correct_answer": "Yes.", "wrong_answer": "No.", "honest_first": True,
        "debate_transcript": [
            {"speaker": "honest", "text": f"{debater} argues yes.", "round": 1},
            {"speaker": "dishonest", "text": f"{debater} argues no.", "round": 1},
        ],
    }


def _bundle(name: str, question_ids: list[str], transcripts_per_pair: int) -> dict:
    records = []
    for debater in DEBATERS:
        for question_id in question_ids:
            for transcript_index in range(transcripts_per_pair):
                payload = _transcript_payload(question_id, debater, transcript_index)
                records.append({
                    "question_id": question_id, "world": payload["world"],
                    "debater_model": debater, "transcript_index": transcript_index,
                    "source_cell_key": f"synthetic:{name}:{question_id}:{debater}",
                    "source_event_hash": "0" * 64,
                    "transcript_sha256": canonical_sha256(payload),
                    "transcript_payload": payload,
                })
    return {
        "schema_version": "phase3_transcript_bundle_v1", "bundle": name,
        "expected_transcript_count": len(records), "actual_transcript_count": len(records),
        "transcripts": records,
    }


@pytest.fixture()
def fixtures(tmp_path, monkeypatch):
    """Write the synthetic protocol/bundles to tmp_path and patch just enough of phase3_plan.

    ``validate_protocol`` is replaced with a no-op (see ``_synthetic_protocol``'s docstring).
    ``load_reference_question_ids``/``candidate_roster_judges`` are replaced because their real
    implementations read fields (``sources``, ``source_bindings``, ``roster.judges_continuing``)
    this minimal document has no reason to carry. ``load_protocol`` itself, ``enumerate_cells``,
    ``enumerate_canary_cells``, ``make_cell_key`` and ``validate_cells`` all run for real.
    """
    protocol = _synthetic_protocol()
    monkeypatch.setattr(phase3_plan, "validate_protocol", lambda protocol: None)
    monkeypatch.setattr(
        phase3_plan, "load_reference_question_ids",
        lambda protocol, project_root: (tuple(MAIN_QUESTION_IDS), tuple(HELD_OUT_QUESTION_IDS)))
    monkeypatch.setattr(
        phase3_plan, "candidate_roster_judges", lambda protocol, n: ["judge-a"])

    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")

    # phase3_plan hard-codes these multipliers (MAIN_TRANSCRIPTS_PER_QUESTION_PER_DEBATER=3,
    # CANARY_TRANSCRIPTS_PER_QUESTION_PER_DEBATER=1) rather than reading them from the
    # protocol, so the synthetic bundles must match them exactly or enumerate_cells's
    # coverage check (every enumerated transcript cell has a matching bundle record) refuses.
    main_bundle = _bundle(
        "main", MAIN_QUESTION_IDS,
        transcripts_per_pair=phase3_plan.MAIN_TRANSCRIPTS_PER_QUESTION_PER_DEBATER)
    canary_bundle = _bundle(
        "canary", HELD_OUT_QUESTION_IDS,
        transcripts_per_pair=phase3_plan.CANARY_TRANSCRIPTS_PER_QUESTION_PER_DEBATER)
    verification_report = {
        "schema_version": "phase3_transcript_verification_v1",
        "bundle_canonical_sha256": {
            "main_bundle": canonical_sha256(main_bundle),
            "canary_bundle": canonical_sha256(canary_bundle),
        },
    }

    main_path = tmp_path / "main_bundle.json"
    canary_path = tmp_path / "canary_bundle.json"
    report_path = tmp_path / "verification_report.json"
    main_path.write_text(json.dumps(main_bundle), encoding="utf-8")
    canary_path.write_text(json.dumps(canary_bundle), encoding="utf-8")
    report_path.write_text(json.dumps(verification_report), encoding="utf-8")

    return {
        "protocol_path": protocol_path, "main_bundle_path": main_path,
        "canary_bundle_path": canary_path, "verification_report_path": report_path,
        "main_bundle": main_bundle, "canary_bundle": canary_bundle,
    }


def _run(fixtures, target_store_path):
    return preseed(
        protocol_path=fixtures["protocol_path"], project_root=ROOT,
        main_bundle_path=fixtures["main_bundle_path"],
        canary_bundle_path=fixtures["canary_bundle_path"],
        verification_report_path=fixtures["verification_report_path"],
        target_store_path=target_store_path)


def _enumerated_transcript_keys(fixtures):
    protocol = phase3_plan.load_protocol(fixtures["protocol_path"])
    main_cells = phase3_plan.enumerate_cells(protocol, ["judge-a"], MAIN_QUESTION_IDS)
    canary_cells = phase3_plan.enumerate_canary_cells(
        protocol, ["judge-a"], HELD_OUT_QUESTION_IDS)
    main_keys = [c["cell_key"] for c in main_cells if c["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND]
    canary_keys = [c["cell_key"] for c in canary_cells
                  if c["kind"] == phase3_plan.CANARY_TRANSCRIPT_KIND]
    return main_keys, canary_keys


# ---------------------------------------------------------------------------
# happy path: rows chain-verify and satisfy is_complete for every transcript cell
# ---------------------------------------------------------------------------


def test_preseeding_writes_a_row_per_transcript_and_satisfies_is_complete(tmp_path, fixtures):
    target = tmp_path / "store.jsonl"
    result = _run(fixtures, target)

    assert result["main_bundle_count"] == 12   # 2 questions x 2 debaters x 3 transcripts
    assert result["canary_bundle_count"] == 2  # 1 question x 2 debaters x 1 transcript
    assert result["written"] == {"main": 12, "canary": 2}
    assert result["skipped"] == {"main": 0, "canary": 0}

    # Re-opening chain-verifies the whole file (rejudge.phase2_canary_order.CellResultStore),
    # never bypassed: this is what proves the rows are CORRECTLY hash-chained, not merely
    # present.
    store = CellResultStore(target)
    assert len(store._results) == 14

    main_keys, canary_keys = _enumerated_transcript_keys(fixtures)
    assert len(main_keys) == 12
    assert len(canary_keys) == 2
    for key in main_keys + canary_keys:
        assert store.is_complete(key), key


def test_canary_only_preseed_never_writes_main_rows(tmp_path, fixtures):
    target = tmp_path / "canary-only.jsonl"
    result = preseed_canary(
        protocol_path=fixtures["protocol_path"], project_root=ROOT,
        canary_bundle_path=fixtures["canary_bundle_path"],
        verification_report_path=fixtures["verification_report_path"],
        target_store_path=target)
    assert result == {
        "target_store_path": str(target), "canary_bundle_count": 2,
        "written": 2, "skipped": 0,
    }
    store = CellResultStore(target)
    main_keys, canary_keys = _enumerated_transcript_keys(fixtures)
    assert set(store._results) == set(canary_keys)
    assert not set(store._results) & set(main_keys)


def test_canary_only_preseed_rejects_existing_row_that_differs_from_bundle(
    tmp_path, fixtures,
):
    target = tmp_path / "canary-drift.jsonl"
    _main_keys, canary_keys = _enumerated_transcript_keys(fixtures)
    key = canary_keys[0]
    CellResultStore(target).record(key, {"cell_key": key, "question": "wrong transcript"})
    with pytest.raises(PreseedError, match="differs from the frozen bundle"):
        preseed_canary(
            protocol_path=fixtures["protocol_path"], project_root=ROOT,
            canary_bundle_path=fixtures["canary_bundle_path"],
            verification_report_path=fixtures["verification_report_path"],
            target_store_path=target)


def test_main_only_preseed_writes_exact_main_transcript_coverage(tmp_path, fixtures):
    target = tmp_path / "main-only.jsonl"
    result = preseed_main(
        protocol_path=fixtures["protocol_path"], project_root=ROOT,
        main_bundle_path=fixtures["main_bundle_path"],
        verification_report_path=fixtures["verification_report_path"],
        target_store_path=target)
    assert result == {
        "target_store_path": str(target), "main_bundle_count": 12,
        "written": 12, "skipped": 0,
    }
    store = CellResultStore(target)
    main_keys, canary_keys = _enumerated_transcript_keys(fixtures)
    assert set(store._results) == set(main_keys)
    assert not set(store._results) & set(canary_keys)

    before = target.read_bytes()
    second = preseed_main(
        protocol_path=fixtures["protocol_path"], project_root=ROOT,
        main_bundle_path=fixtures["main_bundle_path"],
        verification_report_path=fixtures["verification_report_path"],
        target_store_path=target)
    assert second["written"] == 0
    assert second["skipped"] == 12
    assert target.read_bytes() == before


def test_main_only_preseed_checks_all_existing_rows_before_appending(tmp_path, fixtures):
    target = tmp_path / "main-drift.jsonl"
    main_keys, _canary_keys = _enumerated_transcript_keys(fixtures)
    CellResultStore(target).record(
        main_keys[-1], {"cell_key": main_keys[-1], "question": "wrong transcript"})
    before = target.read_bytes()

    with pytest.raises(PreseedError, match="existing main transcript row differs"):
        preseed_main(
            protocol_path=fixtures["protocol_path"], project_root=ROOT,
            main_bundle_path=fixtures["main_bundle_path"],
            verification_report_path=fixtures["verification_report_path"],
            target_store_path=target)
    assert target.read_bytes() == before


def test_main_only_preseed_refuses_any_foreign_existing_row_before_appending(
    tmp_path, fixtures,
):
    target = tmp_path / "main-foreign.jsonl"
    CellResultStore(target).record(
        "canary:foreign", {"cell_key": "canary:foreign", "question": "foreign"})
    before = target.read_bytes()
    with pytest.raises(PreseedError, match="outside the exact main transcript partition"):
        preseed_main(
            protocol_path=fixtures["protocol_path"], project_root=ROOT,
            main_bundle_path=fixtures["main_bundle_path"],
            verification_report_path=fixtures["verification_report_path"],
            target_store_path=target)
    assert target.read_bytes() == before


def test_main_only_preseed_refuses_incomplete_bundle_coverage_before_writing(
    tmp_path, fixtures,
):
    incomplete = json.loads(json.dumps(fixtures["main_bundle"]))
    incomplete["transcripts"].pop()
    incomplete["actual_transcript_count"] -= 1
    incomplete_path = tmp_path / "incomplete-main.json"
    incomplete_path.write_text(json.dumps(incomplete), encoding="utf-8")
    report = {
        "bundle_canonical_sha256": {"main_bundle": canonical_sha256(incomplete)},
    }
    report_path = tmp_path / "incomplete-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    target = tmp_path / "not-written.jsonl"

    with pytest.raises(PreseedError, match="have no matching bundle record"):
        preseed_main(
            protocol_path=fixtures["protocol_path"], project_root=ROOT,
            main_bundle_path=incomplete_path, verification_report_path=report_path,
            target_store_path=target)
    assert not target.exists()


def test_main_only_preseed_refuses_duplicate_bundle_coverage_before_writing(
    tmp_path, fixtures,
):
    duplicated = json.loads(json.dumps(fixtures["main_bundle"]))
    duplicated["transcripts"].append(dict(duplicated["transcripts"][0]))
    duplicated["actual_transcript_count"] += 1
    duplicated_path = tmp_path / "duplicated-main.json"
    duplicated_path.write_text(json.dumps(duplicated), encoding="utf-8")
    report_path = tmp_path / "duplicated-report.json"
    report_path.write_text(json.dumps({
        "bundle_canonical_sha256": {"main_bundle": canonical_sha256(duplicated)},
    }), encoding="utf-8")
    target = tmp_path / "not-written-duplicate.jsonl"

    with pytest.raises(PreseedError, match="does not map one-to-one"):
        preseed_main(
            protocol_path=fixtures["protocol_path"], project_root=ROOT,
            main_bundle_path=duplicated_path, verification_report_path=report_path,
            target_store_path=target)
    assert not target.exists()


def test_a_preseeded_rows_result_carries_the_transcript_payload_and_phase3_cell_key(
    tmp_path, fixtures,
):
    target = tmp_path / "store.jsonl"
    _run(fixtures, target)
    store = CellResultStore(target)

    protocol = phase3_plan.load_protocol(fixtures["protocol_path"])
    main_cells = phase3_plan.enumerate_cells(protocol, ["judge-a"], MAIN_QUESTION_IDS)
    transcript_cell = next(
        c for c in main_cells if c["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND
        and c["question_id"] == "Q-001" and c["debater_model"] == "debater-a")

    result = store.get(transcript_cell["cell_key"])
    assert result["cell_key"] == transcript_cell["cell_key"]
    assert result["question_id"] == "Q-001"
    assert result["debate_transcript"], "the transcript payload's own fields must survive"


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def test_preseeding_twice_against_the_same_store_is_a_verified_no_op(tmp_path, fixtures):
    target = tmp_path / "store.jsonl"
    _run(fixtures, target)
    before = target.read_bytes()

    second = _run(fixtures, target)
    assert second["written"] == {"main": 0, "canary": 0}
    assert second["skipped"] == {"main": 12, "canary": 2}

    after = target.read_bytes()
    assert after == before, "a no-op re-run must not change the file at all"

    # The re-opened store still chain-verifies (CellResultStore's own load-time check).
    store = CellResultStore(target)
    assert len(store._results) == 14


def test_preseeding_rejects_a_preexisting_row_that_differs_from_the_bundle(
    tmp_path, fixtures,
):
    target = tmp_path / "store.jsonl"
    store = CellResultStore(target)
    main_keys, _canary_keys = _enumerated_transcript_keys(fixtures)
    store.record(main_keys[0], {"pre-existing": True})

    before = target.read_bytes()
    with pytest.raises(PreseedError, match="differs from the frozen bundle"):
        _run(fixtures, target)
    assert target.read_bytes() == before
    reopened = CellResultStore(target)
    assert reopened.get(main_keys[0]) == {"pre-existing": True}


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_a_bundle_hash_mismatch_refuses_before_writing_anything(tmp_path, fixtures):
    tampered = dict(fixtures["main_bundle"])
    tampered["transcripts"] = list(tampered["transcripts"])
    tampered["transcripts"][0] = dict(tampered["transcripts"][0])
    tampered["transcripts"][0]["transcript_payload"] = dict(
        tampered["transcripts"][0]["transcript_payload"])
    tampered["transcripts"][0]["transcript_payload"]["question"] += " (tampered)"
    tampered_path = tmp_path / "tampered_main_bundle.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")

    target = tmp_path / "store.jsonl"
    with pytest.raises(PreseedError, match="does not match the verification report"):
        preseed(
            protocol_path=fixtures["protocol_path"], project_root=ROOT,
            main_bundle_path=tampered_path, canary_bundle_path=fixtures["canary_bundle_path"],
            verification_report_path=fixtures["verification_report_path"],
            target_store_path=target)
    assert not target.exists(), "nothing may be written when bundle verification fails"


def test_a_target_store_whose_chain_does_not_verify_is_refused(tmp_path, fixtures):
    target = tmp_path / "store.jsonl"
    target.write_text(
        json.dumps({"cell_key": "bogus", "result": {}, "sequence": 0,
                   "prev_event_hash": "genesis", "event_hash": "0" * 64}) + "\n",
        encoding="utf-8")
    with pytest.raises(ValueError):
        _run(fixtures, target)


# ---------------------------------------------------------------------------
# the real (frozen) bundles: exercised once, deliberately reading the archive
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not DEFAULT_MAIN_BUNDLE_PATH.exists(),
    reason="archive transcript bundles not present on this machine")
def test_preseeding_the_real_bundles_is_idempotent_against_a_tmp_store(tmp_path):
    """Deliberately reads the archive (DEFAULT_MAIN_BUNDLE_PATH / DEFAULT_CANARY_BUNDLE_PATH,
    the two bundles' default location now that they no longer live in the repo) plus the frozen,
    repo-tracked verification report. Confirms the real 540-transcript bundles pre-seed cleanly
    against the real phase3_plan enumerator and that a second run is a verified no-op."""
    target = tmp_path / "store.jsonl"
    first = preseed(
        protocol_path=ROOT / "rejudge" / "phase3_protocol.json", project_root=ROOT,
        target_store_path=target)
    assert first["written"] == {"main": 492, "canary": 48}

    before = target.read_bytes()
    second = preseed(
        protocol_path=ROOT / "rejudge" / "phase3_protocol.json", project_root=ROOT,
        target_store_path=target)
    assert second["written"] == {"main": 0, "canary": 0}
    assert second["skipped"] == {"main": 492, "canary": 48}
    assert target.read_bytes() == before
