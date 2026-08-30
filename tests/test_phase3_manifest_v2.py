"""v2-specific coverage for rejudge.phase3_manifest: protocol pin dispatch (v1 vs v2), the
anchor-carry binding (synthetic v1-store fixtures: matching/missing/tampered rows), and the
frozen crank-settings binding.

Sibling of tests/test_phase3_manifest.py's v1 coverage; reuses its synthetic-transcript-bundle
fixture technique (the real 540-transcript bundles never live in the repo) so these tests stay
hermetic and fast, never touching the real E:/ archive.
"""
import json
import shutil
from pathlib import Path

import pytest

from rejudge import phase3_manifest, phase3_plan
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_execution import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_V1_PATH = ROOT / "rejudge" / "phase3_protocol.json"
PROTOCOL_V2_PATH = ROOT / "rejudge" / "phase3_protocol_v2.json"

# The frozen v2 roster (matches rejudge/phase3_manifest_v2_2026-08-21.json, the real manifest
# this materialization task built).
V2_ROSTER = [
    "Qwen/Qwen2.5-7B-Instruct-Turbo", "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo", "openai/gpt-oss-120b",
    "google/gemma-3n-E4B-it", "Qwen/Qwen3.7-Max",
]

ESTIMATOR_VALIDATION_PATH = "rejudge/phase3_estimator_validation_2026-08-19.json"
CONTEXT_BLOCKLIST_CANARY_V2_PATH = "rejudge/phase3_context_blocklist_canary_v2_2026-08-21.json"
CONTEXT_BLOCKLIST_MAIN_V2_PATH = "rejudge/phase3_context_blocklist_main_v2_2026-08-21.json"
CONTEXT_BLOCKLIST_CANARY_V1_PATH = "rejudge/phase3_context_blocklist_canary_2026-08-19b.json"
CONTEXT_BLOCKLIST_MAIN_V1_PATH = "rejudge/phase3_context_blocklist_main_2026-08-19b.json"

FROZEN_CRANK_SETTINGS = {"review_daemon_concurrency": 12, "max_waves_per_round": 4}

_IGNORE = shutil.ignore_patterns(".git", "data", "rejudge/output", "__pycache__", ".venv",
                                 ".pytest_cache", ".tmp")

_SYNTHETIC_MAIN_QUESTION_IDS = ("SYN-001", "SYN-002")
_SYNTHETIC_CANARY_QUESTION_IDS = ("SYN-003",)
_SYNTHETIC_DEBATERS = ("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen3.7-Plus")


def _synthetic_transcript_payload(question_id: str, debater: str) -> dict:
    return {
        "question_id": question_id, "transcript_index": 0, "world": "synthetic_world",
        "question": f"Is {question_id} true?", "correct_answer": "Yes.",
        "wrong_answer": "No.", "honest_first": True,
        "debate_transcript": [
            {"speaker": "honest", "text": f"{debater} argues yes.", "round": 1},
            {"speaker": "dishonest", "text": f"{debater} argues no.", "round": 1},
        ],
    }


def _synthetic_transcript_bundle(name: str, question_ids: tuple[str, ...]) -> dict:
    records = []
    for debater in _SYNTHETIC_DEBATERS:
        for question_id in question_ids:
            payload = _synthetic_transcript_payload(question_id, debater)
            records.append({
                "question_id": question_id, "world": payload["world"],
                "debater_model": debater, "transcript_index": 0,
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


def _write_synthetic_transcript_bundles(root: Path) -> None:
    main_bundle = _synthetic_transcript_bundle("main", _SYNTHETIC_MAIN_QUESTION_IDS)
    canary_bundle = _synthetic_transcript_bundle("canary", _SYNTHETIC_CANARY_QUESTION_IDS)

    main_path = root / phase3_manifest.MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH
    canary_path = root / phase3_manifest.CANARY_TRANSCRIPT_BUNDLE_RELATIVE_PATH
    main_path.write_text(json.dumps(main_bundle), encoding="utf-8")
    canary_path.write_text(json.dumps(canary_bundle), encoding="utf-8")

    report_path = root / phase3_manifest.TRANSCRIPT_VERIFICATION_RELATIVE_PATH
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["bundle_canonical_sha256"] = {
        "main_bundle": canonical_sha256(main_bundle),
        "canary_bundle": canonical_sha256(canary_bundle),
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")


def _copied_repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT, root, ignore=_IGNORE)
    _write_synthetic_transcript_bundles(root)
    return root


def _build_synthetic_v1_anchor_store(root: Path, store_path: Path, *,
                                     roster=V2_ROSTER, omit: int = 0,
                                     ) -> tuple[list[str], CellResultStore]:
    """A synthetic v1-identity result store carrying exactly the real 288 anchor cell keys.

    Content-agnostic on purpose (matches the real binding's own indifference to result payload
    content -- only cell_key + event_hash matter): each row's "result" is a trivial marker dict.
    ``omit`` drops the last N anchor keys (by sorted order) entirely, to exercise the
    "missing anchor row" refusal.
    """
    v1_protocol = phase3_plan.load_protocol(root / "rejudge" / "phase3_protocol.json")
    _main_ids, held_out_ids = phase3_plan.load_reference_question_ids(v1_protocol, root)
    v1_cells = phase3_plan.enumerate_canary_cells(v1_protocol, list(roster), held_out_ids)
    anchor_keys = sorted(
        str(c["cell_key"]) for c in v1_cells if c["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND)
    assert len(anchor_keys) == 288
    keys_to_write = anchor_keys[: len(anchor_keys) - omit] if omit else anchor_keys

    store = CellResultStore(store_path)
    for key in keys_to_write:
        store.record(key, {"condition": "capability_qa", "cell_key": key, "marker": True})
    return anchor_keys, store


@pytest.fixture(scope="module")
def manifest_root(tmp_path_factory):
    return _copied_repo(tmp_path_factory.mktemp("phase3_manifest_v2"))


def _build_v2_manifest(root: Path, anchor_store_path: Path, **overrides):
    kwargs = dict(
        protocol_path=PROTOCOL_V2_PATH, project_root=root, recorded_at_utc="2026-08-21T00:00:00Z",
        archive_dir="E:/selvarath-archive/phase3-v2-test", roster_judges=V2_ROSTER,
        estimator_validation_path=ESTIMATOR_VALIDATION_PATH,
        context_blocklist_canary_path=CONTEXT_BLOCKLIST_CANARY_V2_PATH,
        context_blocklist_main_path=CONTEXT_BLOCKLIST_MAIN_V2_PATH,
        anchor_carry_v1_protocol_path="rejudge/phase3_protocol.json",
        anchor_carry_store_path=str(anchor_store_path), crank_settings=dict(FROZEN_CRANK_SETTINGS))
    kwargs.update(overrides)
    return phase3_manifest.build_manifest(**kwargs)


# ---------------------------------------------------------------------------
# protocol pin dispatch: v1 vs v2 selection
# ---------------------------------------------------------------------------


def test_build_manifest_binds_the_v2_pin_for_a_v2_protocol(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path)
    manifest = _build_v2_manifest(manifest_root, store_path)
    assert manifest["frozen_inputs"]["protocol_sha256"] == (
        phase3_plan.FROZEN_PROTOCOL_V2_CANONICAL_SHA256)
    assert manifest["frozen_inputs"]["protocol_sha256"] != (
        phase3_plan.FROZEN_PROTOCOL_CANONICAL_SHA256)


def test_build_manifest_still_binds_the_v1_pin_for_a_v1_protocol(manifest_root):
    # No v2-only kwargs supplied -- the v1 path must still work exactly as before.
    manifest = phase3_manifest.build_manifest(
        PROTOCOL_V1_PATH, project_root=manifest_root, recorded_at_utc="t", archive_dir="a",
        roster_judges=V2_ROSTER, estimator_validation_path=ESTIMATOR_VALIDATION_PATH,
        context_blocklist_canary_path=CONTEXT_BLOCKLIST_CANARY_V1_PATH,
        context_blocklist_main_path=CONTEXT_BLOCKLIST_MAIN_V1_PATH)
    assert manifest["frozen_inputs"]["protocol_sha256"] == (
        phase3_plan.FROZEN_PROTOCOL_CANONICAL_SHA256)
    assert "anchor_carry_store_path" not in manifest["frozen_inputs"]
    assert "crank_settings" not in manifest["frozen_inputs"]


def test_build_manifest_rejects_an_unsupported_protocol_schema_version(manifest_root, tmp_path):
    tampered_path = tmp_path / "bogus_protocol.json"
    payload = json.loads(PROTOCOL_V2_PATH.read_text(encoding="utf-8"))
    payload["schema_version"] = "phase3_plan_v3_does_not_exist"
    tampered_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(phase3_plan.ProtocolValidationError):
        phase3_manifest.build_manifest(
            tampered_path, project_root=manifest_root, recorded_at_utc="t", archive_dir="a",
            roster_judges=V2_ROSTER, estimator_validation_path=ESTIMATOR_VALIDATION_PATH,
            context_blocklist_canary_path=CONTEXT_BLOCKLIST_CANARY_V2_PATH,
            context_blocklist_main_path=CONTEXT_BLOCKLIST_MAIN_V2_PATH)


def test_build_manifest_v2_kwargs_forbidden_against_a_v1_protocol(manifest_root):
    with pytest.raises(phase3_manifest.ManifestValidationError, match="v2-only bindings"):
        phase3_manifest.build_manifest(
            PROTOCOL_V1_PATH, project_root=manifest_root, recorded_at_utc="t", archive_dir="a",
            roster_judges=V2_ROSTER, estimator_validation_path=ESTIMATOR_VALIDATION_PATH,
            context_blocklist_canary_path=CONTEXT_BLOCKLIST_CANARY_V1_PATH,
            context_blocklist_main_path=CONTEXT_BLOCKLIST_MAIN_V1_PATH,
            crank_settings=dict(FROZEN_CRANK_SETTINGS))


def test_build_manifest_v2_kwargs_required_against_a_v2_protocol(manifest_root):
    with pytest.raises(phase3_manifest.ManifestValidationError, match="anchor_carry"):
        phase3_manifest.build_manifest(
            PROTOCOL_V2_PATH, project_root=manifest_root, recorded_at_utc="t", archive_dir="a",
            roster_judges=V2_ROSTER, estimator_validation_path=ESTIMATOR_VALIDATION_PATH,
            context_blocklist_canary_path=CONTEXT_BLOCKLIST_CANARY_V2_PATH,
            context_blocklist_main_path=CONTEXT_BLOCKLIST_MAIN_V2_PATH)


# ---------------------------------------------------------------------------
# anchor-carry binding
# ---------------------------------------------------------------------------


def test_anchor_carry_binds_all_288_rows_from_a_matching_v1_store(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_full.jsonl"
    anchor_keys, _store = _build_synthetic_v1_anchor_store(manifest_root, store_path)
    manifest = _build_v2_manifest(manifest_root, store_path)
    frozen = manifest["frozen_inputs"]
    assert frozen["anchor_carry_cell_count"] == 288
    assert frozen["anchor_carry_cell_keys"] == anchor_keys
    assert len(frozen["anchor_carry_rows"]) == 288
    assert frozen["anchor_carry_rows_sha256"] == canonical_sha256(frozen["anchor_carry_rows"])
    assert frozen["anchor_carry_v1_protocol_sha256"] == phase3_plan.FROZEN_PROTOCOL_CANONICAL_SHA256


def test_anchor_carry_refuses_a_v1_store_missing_rows(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_incomplete.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path, omit=5)
    with pytest.raises(phase3_manifest.ManifestValidationError, match="not present"):
        _build_v2_manifest(manifest_root, store_path)


def test_anchor_carry_refuses_a_missing_v1_store_file(manifest_root, tmp_path):
    missing_path = tmp_path / "does-not-exist.jsonl"
    with pytest.raises(phase3_manifest.ManifestValidationError, match="not found"):
        _build_v2_manifest(manifest_root, missing_path)


def test_anchor_carry_refuses_a_tampered_v1_store(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_tampered.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path)
    # Flip a byte in one row's "result" payload without recomputing event_hash -- the store's
    # own hash-chain re-verification (CellResultStore.__init__) must catch this before the
    # anchor-carry binding ever gets to read a single event_hash out of it.
    lines = store_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["result"]["marker"] = False
    lines[0] = json.dumps(row)
    store_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tampered"):
        _build_v2_manifest(manifest_root, store_path)


def test_validate_manifest_reverifies_the_anchor_carry_against_the_real_store(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_validate.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path)
    manifest = _build_v2_manifest(manifest_root, store_path)
    revalidated = phase3_manifest.validate_manifest(
        manifest, protocol_path=PROTOCOL_V2_PATH, project_root=manifest_root)
    assert revalidated["frozen_inputs"]["anchor_carry_rows_sha256"] == (
        manifest["frozen_inputs"]["anchor_carry_rows_sha256"])


def test_validate_manifest_refuses_if_the_v1_store_is_tampered_after_build(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_post_tamper.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path)
    manifest = _build_v2_manifest(manifest_root, store_path)

    # Tamper the v1 store AFTER the manifest was built and bound against it.
    lines = store_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    row["result"]["marker"] = "tampered-post-build"
    lines[-1] = json.dumps(row)
    store_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="tampered"):
        phase3_manifest.validate_manifest(
            manifest, protocol_path=PROTOCOL_V2_PATH, project_root=manifest_root)


# ---------------------------------------------------------------------------
# crank settings binding
# ---------------------------------------------------------------------------


def test_crank_settings_bind_the_frozen_values(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_crank.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path)
    manifest = _build_v2_manifest(manifest_root, store_path)
    assert manifest["frozen_inputs"]["crank_settings"] == FROZEN_CRANK_SETTINGS


def test_crank_settings_refuses_a_wrong_concurrency_value(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_crank_wrong.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path)
    with pytest.raises(phase3_manifest.ManifestValidationError, match="frozen at exactly"):
        _build_v2_manifest(
            manifest_root, store_path,
            crank_settings={"review_daemon_concurrency": 8, "max_waves_per_round": 4})


def test_crank_settings_refuses_missing_keys(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_crank_missing.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path)
    with pytest.raises(phase3_manifest.ManifestValidationError, match="exactly"):
        _build_v2_manifest(
            manifest_root, store_path, crank_settings={"review_daemon_concurrency": 12})


def test_a_freshly_built_v2_manifest_validates_end_to_end(manifest_root, tmp_path):
    store_path = tmp_path / "v1_store_e2e.jsonl"
    _build_synthetic_v1_anchor_store(manifest_root, store_path)
    manifest = _build_v2_manifest(manifest_root, store_path)
    phase3_manifest.validate_manifest(
        manifest, protocol_path=PROTOCOL_V2_PATH, project_root=manifest_root)
    assert manifest["planning"]["main"]["slot_count"] == 29520
    assert manifest["planning"]["canary"]["slot_count"] == 1440
