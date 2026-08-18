"""The phase-3 execution manifest, its validator, and the transcript-generation guard it pins.

Two things this module and rejudge.phase2_canary_execute together must get right before any
phase-3 cell runs: every frozen input the protocol names is bound by hash (and a tampered copy
of any of them is refused before the manifest is even assembled), and a transcript-generation
cell can never reach a live provider call under a manifest that forbids it.
"""
import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from rejudge import phase2_canary_cells as cells_mod
from rejudge import phase2_plan
from rejudge import phase3_manifest, phase3_plan
from rejudge.phase2_canary_execute import (
    CellContext, GenerationForbiddenError, MissingTranscript, execute_cell)
from rejudge.phase2_canary_fixtures import DeterministicCanaryClient, StubReviewer
from rejudge.phase2_dual_gate import DualGateDecisionStore
from rejudge.phase2_execution import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "rejudge" / "phase3_protocol.json"
ANCHOR = "Qwen/Qwen2.5-7B-Instruct-Turbo"

# Excludes meta-llama/Meta-Llama-3-8B-Instruct-Lite, the one candidate the 2026-08-18 provider
# snapshot found absent from the catalog (rejudge/phase3_role_limits_v1_2026-08-18.json's
# unavailable_candidates) -- the frozen 6 the real manifest can actually bind today.
AVAILABLE_ROSTER = [
    "Qwen/Qwen2.5-7B-Instruct-Turbo", "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo", "openai/gpt-oss-120b",
    "google/gemma-3n-E4B-it", "Qwen/Qwen3.7-Max",
]

_IGNORE = shutil.ignore_patterns(".git", "data", "rejudge/output", "__pycache__", ".venv",
                                 ".pytest_cache")


# The real transcript bundles (492 main + 48 canary phase-2 transcripts) no longer live in the
# repo at all -- they were moved to an out-of-repo archive to keep eval-world debate text out of
# version control -- so a plain shutil.copytree of ROOT never carries them. phase3_manifest
# never reads either bundle's CONTENT beyond its own canonical hash, so a handful of stub
# records is enough to exercise every binding and tamper-refusal check this module has; tests
# that need the real 540-transcript bundles belong in test_phase3_preseed.py, not here.
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
    """Overwrite a copied repo's two transcript bundles + verification report with a small,
    internally-consistent synthetic set, regenerating bundle_canonical_sha256 with the same
    rejudge.phase2_execution.canonical_sha256 the real report was built with."""
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


@pytest.fixture(scope="module")
def manifest_root(tmp_path_factory):
    """A module-scoped copied repo (see _copied_repo) so the many tests below that need a full,
    successfully-buildable manifest don't each pay for their own repo copy."""
    return _copied_repo(tmp_path_factory.mktemp("phase3_manifest"))


@pytest.fixture(scope="module")
def protocol(manifest_root):
    return phase3_plan.load_protocol(manifest_root / "rejudge" / "phase3_protocol.json")


@pytest.fixture(scope="module")
def manifest(manifest_root):
    # protocol_path is PROTOCOL_PATH (the real repo's, byte-identical to manifest_root's copy),
    # not manifest_root's own copy: protocol_path and project_root are independent (see
    # build_manifest's docstring), and every validate_manifest re-check below passes the same
    # PROTOCOL_PATH, so protocol_tracked_path matches on rebuild.
    return phase3_manifest.build_manifest(
        PROTOCOL_PATH, project_root=manifest_root, recorded_at_utc="2026-08-18T00:00:00Z",
        archive_dir="E:/selvarath-archive/phase3-canary-TBD", roster_judges=AVAILABLE_ROSTER)


# ---------------------------------------------------------------------------
# what the manifest binds
# ---------------------------------------------------------------------------


def test_the_protocol_hash_is_bound_and_matches_phase3_plans_pin(manifest, protocol):
    assert manifest["frozen_inputs"]["protocol_sha256"] == canonical_sha256(protocol)
    assert manifest["frozen_inputs"]["protocol_sha256"] == (
        phase3_plan.FROZEN_PROTOCOL_CANONICAL_SHA256)


def test_the_question_bank_bundle_hash_matches_the_protocols_own_pin(manifest, protocol):
    assert manifest["frozen_inputs"]["question_bank_bundle_sha256"] == (
        protocol["planning_cell_identity"]["question_bank_bundle_sha256"])


def test_the_reused_phase2_artifacts_are_bound_to_their_frozen_pins(manifest):
    bindings = manifest["frozen_inputs"]
    assert bindings["prompt_bundle_sha256"] == phase3_manifest.REUSED_PROMPT_BUNDLE_SHA256
    assert bindings["checker_frozen_config_sha256"] == (
        phase3_manifest.CHECKER_FROZEN_CONFIG_SHA256)
    assert bindings["reviewer_prompt_sha256"] == phase3_manifest.REVIEWER_PROMPT_SHA256
    assert bindings["checker_model"] == "google/gemma-4-31B-it"
    assert len(bindings["prompt_bundle_approval_sha256"]) == 64


def test_the_role_limits_and_provider_snapshot_are_bound(manifest):
    bindings = manifest["frozen_inputs"]
    role_limits = json.loads(
        (ROOT / phase3_manifest.ROLE_LIMITS_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert bindings["role_limits_sha256"] == canonical_sha256(role_limits)
    assert bindings["role_limits_tracked_path"] == "rejudge/phase3_role_limits_v1_2026-08-18.json"
    snapshot = json.loads(
        (ROOT / phase3_manifest.PROVIDER_SNAPSHOT_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert bindings["provider_snapshot_sha256"] == canonical_sha256(snapshot)


def test_the_transcript_bundles_and_verification_report_are_bound(manifest, manifest_root):
    bindings = manifest["frozen_inputs"]
    report = json.loads(
        (manifest_root / phase3_manifest.TRANSCRIPT_VERIFICATION_RELATIVE_PATH).read_text(
            encoding="utf-8"))
    assert bindings["main_transcript_bundle_sha256"] == (
        report["bundle_canonical_sha256"]["main_bundle"])
    assert bindings["canary_transcript_bundle_sha256"] == (
        report["bundle_canonical_sha256"]["canary_bundle"])
    assert bindings["transcript_verification_report_sha256"] == canonical_sha256(report)
    # With transcript_bundle_dir left at its default (None), the recorded path is the ACTUAL
    # path read: project_root-relative, matching today's (pre-archive-move) resolution.
    assert bindings["main_transcript_bundle_path"] == (
        (manifest_root / phase3_manifest.MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH)
        .as_posix())
    assert bindings["canary_transcript_bundle_path"] == (
        (manifest_root / phase3_manifest.CANARY_TRANSCRIPT_BUNDLE_RELATIVE_PATH)
        .as_posix())


def test_the_plan_cells_are_bound_and_match_the_frozen_slot_arithmetic(manifest, protocol):
    main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, ROOT)
    main_cells = phase3_plan.enumerate_cells(protocol, AVAILABLE_ROSTER, main_ids)
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, AVAILABLE_ROSTER, held_out_ids)
    assert manifest["planning"]["main"]["cells_sha256"] == canonical_sha256(main_cells)
    assert manifest["planning"]["canary"]["cells_sha256"] == canonical_sha256(canary_cells)
    # N6 (6 rostered judges), selected config A_no_d: debate_grid.slot_arithmetic_by_roster.
    assert manifest["planning"]["main"]["slot_count"] == 29_520
    assert manifest["planning"]["main"]["transcript_cells"] == 492
    assert manifest["planning"]["canary"]["transcript_cells"] == 48


def test_the_caps_and_configuration_selection_limits_are_bound(manifest):
    assert manifest["caps"]["stage_cap_usd"] == 450
    limits = manifest["caps"]["configuration_selection_limits"]
    assert limits["R_max"] == 135_000
    assert limits["D_max"] == 45


def test_the_roster_is_bound(manifest):
    assert manifest["roster"]["judges"] == AVAILABLE_ROSTER
    assert manifest["roster"]["debaters"] == ["meta-llama/Llama-3.3-70B-Instruct-Turbo",
                                              "Qwen/Qwen3.7-Plus"]


def test_the_transcript_generation_forbidden_flag_is_set(manifest):
    assert manifest["transcript_generation_forbidden"] is True


def test_the_manifest_authorizes_nothing(manifest):
    assert manifest["execution_authorized"] is False


def test_the_code_provenance_bundle_is_bound(manifest):
    files = manifest["code_provenance"]["files"]
    for expected in ("rejudge/phase3_plan.py", "rejudge/phase2_canary_execute.py",
                     "scripts/phase3_preseed_transcripts.py"):
        assert expected in files
    assert manifest["code_provenance"]["code_bundle_sha256"] == (
        phase3_manifest.phase3_code_bundle_sha256(ROOT))


# ---------------------------------------------------------------------------
# identity and re-validation
# ---------------------------------------------------------------------------


def test_the_execution_identity_is_the_canonical_hash_of_the_manifest(manifest):
    without_identity = {k: v for k, v in manifest.items() if k != "execution_identity_sha256"}
    assert manifest["execution_identity_sha256"] == canonical_sha256(without_identity)


def test_two_builds_agree(manifest_root):
    a = phase3_manifest.build_manifest(
        PROTOCOL_PATH, project_root=manifest_root, recorded_at_utc="2026-08-18T00:00:00Z",
        archive_dir="archive", roster_judges=AVAILABLE_ROSTER)
    b = phase3_manifest.build_manifest(
        PROTOCOL_PATH, project_root=manifest_root, recorded_at_utc="2026-08-18T00:00:00Z",
        archive_dir="archive", roster_judges=AVAILABLE_ROSTER)
    assert a == b


def test_a_freshly_built_manifest_validates(manifest, manifest_root):
    phase3_manifest.validate_manifest(
        manifest, protocol_path=PROTOCOL_PATH, project_root=manifest_root)


def test_a_manifest_whose_identity_does_not_match_is_refused(manifest, manifest_root):
    tampered = dict(manifest)
    tampered["execution_identity_sha256"] = "0" * 64
    with pytest.raises(phase3_manifest.ManifestValidationError):
        phase3_manifest.validate_manifest(
            tampered, protocol_path=PROTOCOL_PATH, project_root=manifest_root)


def test_an_authorized_flag_set_in_the_manifest_itself_is_refused(manifest, manifest_root):
    tampered = dict(manifest)
    tampered["execution_authorized"] = True
    with pytest.raises(phase3_manifest.ManifestValidationError, match="never authorize itself"):
        phase3_manifest.validate_manifest(
            tampered, protocol_path=PROTOCOL_PATH, project_root=manifest_root)


def test_a_missing_top_level_key_is_refused(manifest, manifest_root):
    tampered = dict(manifest)
    del tampered["caps"]
    with pytest.raises(phase3_manifest.ManifestValidationError, match="fields drifted"):
        phase3_manifest.validate_manifest(
            tampered, protocol_path=PROTOCOL_PATH, project_root=manifest_root)


def test_a_flipped_transcript_generation_forbidden_flag_is_refused(manifest, manifest_root):
    tampered = dict(manifest)
    tampered["transcript_generation_forbidden"] = False
    with pytest.raises(phase3_manifest.ManifestValidationError,
                       match="transcript_generation_forbidden must be exactly true"):
        phase3_manifest.validate_manifest(
            tampered, protocol_path=PROTOCOL_PATH, project_root=manifest_root)


# ---------------------------------------------------------------------------
# roster validation
# ---------------------------------------------------------------------------


def test_roster_judges_must_be_non_empty():
    with pytest.raises(phase3_manifest.ManifestValidationError, match="non-empty"):
        phase3_manifest.build_manifest(
            PROTOCOL_PATH, project_root=ROOT, recorded_at_utc="t", archive_dir="a",
            roster_judges=[])


def test_roster_judges_must_not_contain_duplicates():
    with pytest.raises(phase3_manifest.ManifestValidationError, match="duplicates"):
        phase3_manifest.build_manifest(
            PROTOCOL_PATH, project_root=ROOT, recorded_at_utc="t", archive_dir="a",
            roster_judges=[*AVAILABLE_ROSTER, AVAILABLE_ROSTER[0]])


def test_a_roster_model_outside_the_protocol_candidates_is_refused():
    with pytest.raises(phase3_manifest.ManifestValidationError,
                       match="not among the frozen protocol"):
        phase3_manifest.build_manifest(
            PROTOCOL_PATH, project_root=ROOT, recorded_at_utc="t", archive_dir="a",
            roster_judges=[*AVAILABLE_ROSTER, "not-a-real-model"])


def test_an_unavailable_new_candidate_in_the_roster_is_refused():
    # meta-llama/Meta-Llama-3-8B-Instruct-Lite was a legitimate protocol candidate
    # (roster.judges_new), but the 2026-08-18 provider snapshot found it absent from the
    # catalog and amendment 1 RETIRED it in favor of Meta-Llama-3.1-8B-Instruct-Turbo, so
    # the roster validation itself refuses it, before role limits are even consulted.
    with pytest.raises(phase3_manifest.ManifestValidationError,
                       match="not among the frozen protocol's"):
        phase3_manifest.build_manifest(
            PROTOCOL_PATH, project_root=ROOT, recorded_at_utc="t", archive_dir="a",
            roster_judges=[*AVAILABLE_ROSTER, "meta-llama/Meta-Llama-3-8B-Instruct-Lite"])


def test_the_amendment_admitted_substitute_is_accepted_and_bound(manifest_root):
    manifest = phase3_manifest.build_manifest(
        PROTOCOL_PATH, project_root=manifest_root, recorded_at_utc="t", archive_dir="a",
        roster_judges=[*AVAILABLE_ROSTER, "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"])
    bindings = manifest["roster"]["amendments"]
    assert len(bindings) == 1
    assert bindings[0]["retired_candidate"] == "meta-llama/Meta-Llama-3-8B-Instruct-Lite"
    assert bindings[0]["admitted_candidate"] == (
        "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo")
    assert len(bindings[0]["canonical_sha256"]) == 64


# ---------------------------------------------------------------------------
# tamper rejection (wrong protocol hash / role-limits drift / missing transport pins)
# ---------------------------------------------------------------------------


def test_a_tampered_protocol_hash_is_refused(tmp_path):
    root = _copied_repo(tmp_path)
    protocol_path = root / "rejudge" / "phase3_protocol.json"
    payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    payload["decisions"]["spend"]["estimate_note"] += " (quietly edited)"
    protocol_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(phase3_plan.ProtocolValidationError,
                       match="frozen phase-3 protocol hash drift"):
        phase3_manifest.build_manifest(
            protocol_path, project_root=root, recorded_at_utc="t", archive_dir="a",
            roster_judges=AVAILABLE_ROSTER)


def test_role_limits_missing_the_transport_section_is_refused(tmp_path):
    root = _copied_repo(tmp_path)
    role_limits_path = root / phase3_manifest.ROLE_LIMITS_RELATIVE_PATH
    payload = json.loads(role_limits_path.read_text(encoding="utf-8"))
    del payload["request_settings"]["transport"]
    role_limits_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(phase3_manifest.ManifestValidationError,
                       match="request_settings.transport section"):
        phase3_manifest.build_manifest(
            root / "rejudge" / "phase3_protocol.json", project_root=root,
            recorded_at_utc="t", archive_dir="a", roster_judges=AVAILABLE_ROSTER)


def test_role_limits_with_an_incomplete_http_timeout_is_refused(tmp_path):
    root = _copied_repo(tmp_path)
    role_limits_path = root / phase3_manifest.ROLE_LIMITS_RELATIVE_PATH
    payload = json.loads(role_limits_path.read_text(encoding="utf-8"))
    del payload["request_settings"]["transport"]["http_timeout"]["pool"]
    role_limits_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(phase3_manifest.ManifestValidationError,
                       match="http_timeout does not carry exactly"):
        phase3_manifest.build_manifest(
            root / "rejudge" / "phase3_protocol.json", project_root=root,
            recorded_at_utc="t", archive_dir="a", roster_judges=AVAILABLE_ROSTER)


def test_a_role_limits_file_missing_a_roster_models_entry_is_refused(tmp_path):
    root = _copied_repo(tmp_path)
    role_limits_path = root / phase3_manifest.ROLE_LIMITS_RELATIVE_PATH
    payload = json.loads(role_limits_path.read_text(encoding="utf-8"))
    del payload["model_role_limits"]["Qwen/Qwen3.7-Max"]
    role_limits_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(phase3_manifest.ManifestValidationError,
                       match=r"no model_role_limits entry for roster judge\(s\)"):
        phase3_manifest.build_manifest(
            root / "rejudge" / "phase3_protocol.json", project_root=root,
            recorded_at_utc="t", archive_dir="a", roster_judges=AVAILABLE_ROSTER)


def test_a_tampered_reused_prompt_bundle_is_refused(tmp_path):
    root = _copied_repo(tmp_path)
    bundle_path = root / phase3_manifest.PHASE2_PROMPT_BUNDLE_RELATIVE_PATH
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    payload["_tampered_marker"] = True
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(phase3_manifest.ManifestValidationError,
                       match="does not match the frozen phase-2 pin"):
        phase3_manifest.build_manifest(
            root / "rejudge" / "phase3_protocol.json", project_root=root,
            recorded_at_utc="t", archive_dir="a", roster_judges=AVAILABLE_ROSTER)


def test_a_tampered_main_transcript_bundle_is_refused(tmp_path):
    root = _copied_repo(tmp_path)
    bundle_path = root / phase3_manifest.MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    payload["transcripts"][0]["transcript_payload"]["question"] += " (tampered)"
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(phase3_manifest.ManifestValidationError,
                       match="verification report's pinned hash"):
        phase3_manifest.build_manifest(
            root / "rejudge" / "phase3_protocol.json", project_root=root,
            recorded_at_utc="t", archive_dir="a", roster_judges=AVAILABLE_ROSTER)


# ---------------------------------------------------------------------------
# the runtime transcript-generation guard (rejudge.phase2_canary_execute)
# ---------------------------------------------------------------------------


def _phase2_protocol():
    return json.loads((ROOT / "rejudge" / "phase2_protocol.json").read_text(encoding="utf-8"))


def _phase2_bundle():
    return json.loads(
        (ROOT / "rejudge" / "phase2_prompt_bundle.json").read_text(encoding="utf-8"))


def _phase2_transcript_cell():
    protocol, bundle = _phase2_protocol(), _phase2_bundle()
    raw = next(c for c in phase2_plan.enumerate_canary_cells(protocol)
              if c["kind"] == "canary_debate_transcript")
    return cells_mod.resolve_cell(raw, protocol, bundle, anchor_judge_model=ANCHOR)


def test_the_guard_raises_before_any_provider_call_when_forbidden(tmp_path):
    resolved = _phase2_transcript_cell()
    client = DeterministicCanaryClient()
    context = CellContext(
        client=client, protocol=_phase2_protocol(), bundle=_phase2_bundle(),
        decision_store=DualGateDecisionStore(tmp_path / "decisions.jsonl"),
        reviewer=StubReviewer(), anchor_judge_model=ANCHOR,
        transcript_generation_forbidden=True)
    with pytest.raises(GenerationForbiddenError, match=resolved.cell_key):
        execute_cell(resolved, context)
    assert client.calls == [], "the guard must fire before any provider call"


def test_the_guard_does_not_fire_when_the_flag_is_absent(tmp_path):
    resolved = _phase2_transcript_cell()
    client = DeterministicCanaryClient()
    context = CellContext(
        client=client, protocol=_phase2_protocol(), bundle=_phase2_bundle(),
        decision_store=DualGateDecisionStore(tmp_path / "decisions.jsonl"),
        reviewer=StubReviewer(), anchor_judge_model=ANCHOR)
    assert context.transcript_generation_forbidden is False
    transcript = execute_cell(resolved, context)
    assert transcript["cell_key"] == resolved.cell_key
    assert client.calls, "with the flag absent, a transcript cell must generate normally"


def _phase2_judgment_cell(condition: str):
    protocol, bundle = _phase2_protocol(), _phase2_bundle()
    raw = next(c for c in phase2_plan.enumerate_canary_cells(protocol)
              if c["kind"] == "canary_debate_judgment" and c["condition"] == condition)
    return cells_mod.resolve_cell(raw, protocol, bundle, anchor_judge_model=ANCHOR)


def test_the_guard_does_not_fire_for_a_non_transcript_cell(tmp_path):
    """A forbidding context must not block cells the flag was never meant to reach."""
    resolved = dataclasses.replace(
        _phase2_judgment_cell("b0"), dependency_keys=("missing-transcript",))
    context = CellContext(
        client=DeterministicCanaryClient(), protocol=_phase2_protocol(), bundle=_phase2_bundle(),
        decision_store=DualGateDecisionStore(tmp_path / "decisions.jsonl"),
        reviewer=StubReviewer(), anchor_judge_model=ANCHOR,
        transcript_generation_forbidden=True)
    # Reaches the ordinary MissingTranscript refusal, not GenerationForbiddenError: the guard
    # only ever inspects transcript-shaped cells.
    with pytest.raises(MissingTranscript):
        execute_cell(resolved, context)
