"""v2 amendment 1 (2026-08-22), the bounded Qwen2.5-7B carve-out: rejudge.phase3_runner's
amendment-bound judgment-cell deferral list -- loading/verification, the runner's skip
semantics, and one real end-to-end v2 dry run proving the whole thing converges correctly.

Context: rejudge/phase3_v2_amendment1_qwen_carveout_2026-08-22.json (the amendment record) and
rejudge/phase3_v2_qwen_deferral_cells_2026-08-22.json (the mechanically-generated deferral list
it authorizes). See rejudge.phase3_runner.load_deferral_list's docstring for the two independent
fail-closed checks this file exercises: the amendment binding's canonical sha256 must match the
amendment record actually on disk, and the listed cell_keys must be EXACTLY the mechanical rule's
output re-derived against the live plan.

Three tiers:

1. Unit-level ``load_deferral_list`` checks against a synthetic, protocol-agnostic ``plan_cells``
   fixture (no manifest, no repo copy, fast) -- tampered amendment sha, wrong cell set,
   duplicate cell keys, a missing amendment file, a missing/incomplete generation_basis, and a
   protocol-sha mismatch.
2. An integration tier reusing tests/test_phase3_runner.py's real-content v1 GUARD_ROSTER
   fixtures for the runner's actual skip semantics (a deferred cell is never attempted, never
   billed, never counted as completed) and the overlap-with-blocklist refusal.
3. One real end-to-end v2 dry run -- the REAL frozen v2 protocol, the REAL 6-judge v2 roster,
   and the REAL amendment record -- proving the run converges with all 192 of Qwen's judgment
   cells absent and ``deferred_by_amendment``/``deferral_amendment_sha256`` reported correctly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rejudge import phase3_manifest, phase3_plan, phase3_runner
from rejudge.phase2_canary_fixtures import DeterministicCanaryClient, StubReviewer
from rejudge.phase2_canary_live import local_path
from rejudge.phase2_canary_order import CellResultStore
from scripts.phase3_preseed_transcripts import preseed

from tests.test_phase3_manifest_v2 import (
    CONTEXT_BLOCKLIST_CANARY_V2_PATH, CONTEXT_BLOCKLIST_MAIN_V2_PATH, ESTIMATOR_VALIDATION_PATH,
    FROZEN_CRANK_SETTINGS, V2_ROSTER, _build_synthetic_v1_anchor_store,
)
from tests.test_phase3_runner import (  # noqa: F401 -- repo_root/held_out_ids are fixtures
    GUARD_ROSTER, _authorization_for, _build_manifest, _preseed, _write_blocklist,
    _write_manifest_and_authorization, held_out_ids, reference_question_ids, repo_root,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_V2_RELATIVE_PATH = "rejudge/phase3_protocol_v2.json"
REAL_AMENDMENT_RELATIVE_PATH = "rejudge/phase3_v2_amendment1_qwen_carveout_2026-08-22.json"
REAL_DEFERRAL_LIST_PATH = ROOT / "rejudge" / "phase3_v2_qwen_deferral_cells_2026-08-22.json"
QWEN = "Qwen/Qwen2.5-7B-Instruct-Turbo"


# ---------------------------------------------------------------------------
# tier 1: unit-level load_deferral_list checks (synthetic plan_cells, no manifest)
# ---------------------------------------------------------------------------


def _synthetic_plan_cells() -> list[dict]:
    cells = [
        {"cell_key": f"cell:J1:{i}", "kind": phase3_plan.CANARY_JUDGMENT_KIND,
         "judge_model": "J1"}
        for i in range(3)
    ] + [
        {"cell_key": f"cell:J2:{i}", "kind": phase3_plan.CANARY_JUDGMENT_KIND,
         "judge_model": "J2"}
        for i in range(2)
    ] + [
        # A non-judgment cell, to prove the mechanical rule only ever counts judgment cells.
        {"cell_key": "transcript:1", "kind": phase3_plan.CANARY_TRANSCRIPT_KIND,
         "judge_model": None},
    ]
    return cells


def _write_amendment(path: Path, payload: dict | None = None) -> dict:
    payload = payload if payload is not None else {
        "schema_version": "test_amendment_v1", "note": "synthetic amendment for unit tests"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _write_deferral(path: Path, *, amendment_tracked_path: str, amendment_sha: str,
                    judge_model: str | None, cell_keys: list[str],
                    protocol_sha: str | None = None, include_generation_basis: bool = True
                    ) -> dict:
    payload: dict = {
        "schema_version": "phase3_v2_qwen_deferral_cells_v1",
        "amendment": {"tracked_path": amendment_tracked_path, "canonical_sha256": amendment_sha},
        "cell_keys": list(cell_keys),
    }
    if include_generation_basis:
        generation_basis: dict = {}
        if judge_model is not None:
            generation_basis["deferred_judge_model"] = judge_model
        if protocol_sha is not None:
            generation_basis["protocol_canonical_sha256"] = protocol_sha
        payload["generation_basis"] = generation_basis
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_load_deferral_list_happy_path(tmp_path):
    plan_cells = _synthetic_plan_cells()
    amendment_payload = _write_amendment(tmp_path / "amendment.json")
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="amendment.json",
                    amendment_sha=amendment_sha, judge_model="J1", cell_keys=keys)

    result = phase3_runner.load_deferral_list(
        deferral_path, project_root=tmp_path, protocol={}, plan_cells=plan_cells)
    assert result["cell_keys"] == frozenset(keys)
    assert result["judge_model"] == "J1"
    assert result["amendment_sha256"] == amendment_sha


def test_load_deferral_list_refuses_a_tampered_amendment_sha(tmp_path):
    plan_cells = _synthetic_plan_cells()
    _write_amendment(tmp_path / "amendment.json")
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="amendment.json",
                    amendment_sha="0" * 64, judge_model="J1", cell_keys=keys)

    with pytest.raises(phase3_runner.Phase3RunnerError, match="drifted or tampered"):
        phase3_runner.load_deferral_list(
            deferral_path, project_root=tmp_path, protocol={}, plan_cells=plan_cells)


def test_load_deferral_list_refuses_a_wrong_cell_set_missing_one(tmp_path):
    plan_cells = _synthetic_plan_cells()
    amendment_payload = _write_amendment(tmp_path / "amendment.json")
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="amendment.json",
                    amendment_sha=amendment_sha, judge_model="J1", cell_keys=keys[:-1])

    with pytest.raises(phase3_runner.Phase3RunnerError,
                       match="does not match the mechanical rule's output"):
        phase3_runner.load_deferral_list(
            deferral_path, project_root=tmp_path, protocol={}, plan_cells=plan_cells)


def test_load_deferral_list_refuses_a_wrong_cell_set_extra_key(tmp_path):
    plan_cells = _synthetic_plan_cells()
    amendment_payload = _write_amendment(tmp_path / "amendment.json")
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="amendment.json",
                    amendment_sha=amendment_sha, judge_model="J1",
                    cell_keys=[*keys, "cell:J2:0"])   # a real cell, but not J1's

    with pytest.raises(phase3_runner.Phase3RunnerError,
                       match="does not match the mechanical rule's output"):
        phase3_runner.load_deferral_list(
            deferral_path, project_root=tmp_path, protocol={}, plan_cells=plan_cells)


def test_load_deferral_list_refuses_duplicate_cell_keys(tmp_path):
    plan_cells = _synthetic_plan_cells()
    amendment_payload = _write_amendment(tmp_path / "amendment.json")
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="amendment.json",
                    amendment_sha=amendment_sha, judge_model="J1", cell_keys=[*keys, keys[0]])

    with pytest.raises(phase3_runner.Phase3RunnerError, match="duplicate"):
        phase3_runner.load_deferral_list(
            deferral_path, project_root=tmp_path, protocol={}, plan_cells=plan_cells)


def test_load_deferral_list_refuses_a_missing_amendment_file(tmp_path):
    plan_cells = _synthetic_plan_cells()
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="does-not-exist.json",
                    amendment_sha="0" * 64, judge_model="J1", cell_keys=keys)

    with pytest.raises(phase3_runner.Phase3RunnerError, match="does not exist"):
        phase3_runner.load_deferral_list(
            deferral_path, project_root=tmp_path, protocol={}, plan_cells=plan_cells)


def test_load_deferral_list_refuses_missing_generation_basis(tmp_path):
    plan_cells = _synthetic_plan_cells()
    amendment_payload = _write_amendment(tmp_path / "amendment.json")
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="amendment.json",
                    amendment_sha=amendment_sha, judge_model="J1", cell_keys=keys,
                    include_generation_basis=False)

    with pytest.raises(phase3_runner.Phase3RunnerError, match="generation_basis"):
        phase3_runner.load_deferral_list(
            deferral_path, project_root=tmp_path, protocol={}, plan_cells=plan_cells)


def test_load_deferral_list_refuses_missing_deferred_judge_model(tmp_path):
    plan_cells = _synthetic_plan_cells()
    amendment_payload = _write_amendment(tmp_path / "amendment.json")
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="amendment.json",
                    amendment_sha=amendment_sha, judge_model=None, cell_keys=keys)

    with pytest.raises(phase3_runner.Phase3RunnerError, match="deferred_judge_model"):
        phase3_runner.load_deferral_list(
            deferral_path, project_root=tmp_path, protocol={}, plan_cells=plan_cells)


def test_load_deferral_list_refuses_a_protocol_sha_mismatch(tmp_path):
    plan_cells = _synthetic_plan_cells()
    amendment_payload = _write_amendment(tmp_path / "amendment.json")
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    keys = sorted(c["cell_key"] for c in plan_cells if c["judge_model"] == "J1")

    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path="amendment.json",
                    amendment_sha=amendment_sha, judge_model="J1", cell_keys=keys,
                    protocol_sha="0" * 64)

    with pytest.raises(phase3_runner.Phase3RunnerError, match="different protocol"):
        phase3_runner.load_deferral_list(
            deferral_path, project_root=tmp_path, protocol={"real": "protocol"},
            plan_cells=plan_cells)


def test_load_deferral_list_refuses_a_missing_file(tmp_path):
    with pytest.raises(phase3_runner.Phase3RunnerError, match="not found"):
        phase3_runner.load_deferral_list(
            tmp_path / "does-not-exist.json", project_root=tmp_path, protocol={},
            plan_cells=_synthetic_plan_cells())


# ---------------------------------------------------------------------------
# the REAL shipped deferral list (rejudge/phase3_v2_qwen_deferral_cells_2026-08-22.json)
# validates against the REAL v2 protocol/roster, standalone -- a fast regression that the
# shipped artifact stays internally consistent even without a full manifest/run.
# ---------------------------------------------------------------------------


def test_the_real_shipped_v2_deferral_list_is_internally_consistent():
    protocol = phase3_plan.load_protocol(ROOT / PROTOCOL_V2_RELATIVE_PATH)
    _main_ids, held_out_ids_ = phase3_plan.load_reference_question_ids(protocol, ROOT)
    plan_cells = phase3_plan.enumerate_canary_cells(protocol, V2_ROSTER, held_out_ids_)

    result = phase3_runner.load_deferral_list(
        REAL_DEFERRAL_LIST_PATH, project_root=ROOT, protocol=protocol, plan_cells=plan_cells)
    assert result["judge_model"] == QWEN
    assert len(result["cell_keys"]) == 192

    raw = result["raw"]
    assert raw["count_by_condition"] == {
        "b0": 96, "sequential_b1": 24, "sequential_b2": 24, "sequential_b4": 24,
        "sequential_b8": 24}
    assert raw["cell_count"] == 192
    amendment = json.loads((ROOT / REAL_AMENDMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert result["amendment_sha256"] == phase3_manifest.canonical_sha256(amendment)


# ---------------------------------------------------------------------------
# tier 2: integration -- v1 GUARD_ROSTER fixtures, the runner's actual skip semantics and the
# overlap-with-blocklist refusal. The deferral mechanism itself is protocol/judge-agnostic, so
# this exercises it against a real (but small/fast) v1 plan rather than the full v2 roster.
# ---------------------------------------------------------------------------


def _v1_deferral_list(root: Path, roster: list[str], held_out_ids_, *, judge_model: str,
                      deferral_path: Path, amendment_path: Path) -> list[str]:
    protocol = phase3_plan.load_protocol(root / "rejudge" / "phase3_protocol.json")
    plan_cells = phase3_plan.enumerate_canary_cells(protocol, roster, held_out_ids_)
    keys = phase3_runner.mechanical_deferral_cell_keys(plan_cells, judge_model)
    assert keys, f"no judgment cells found for {judge_model!r} in this roster/protocol"

    amendment_payload = _write_amendment(amendment_path)
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    _write_deferral(
        deferral_path, amendment_tracked_path=str(amendment_path.name),
        amendment_sha=amendment_sha, judge_model=judge_model, cell_keys=keys)
    return keys


def test_runner_never_attempts_or_bills_deferred_cells_and_reports_the_count(
        tmp_path, repo_root, held_out_ids):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    # The amendment record must live under project_root (repo_root) -- the runner resolves its
    # tracked_path relative to project_root, exactly like the context blocklist binding.
    amendment_path = repo_root / "synthetic_amendment.json"
    deferral_path = tmp_path / "deferral.json"
    deferred_keys = _v1_deferral_list(
        repo_root, GUARD_ROSTER, held_out_ids, judge_model=QWEN, deferral_path=deferral_path,
        amendment_path=amendment_path)
    amendment_sha = phase3_manifest.canonical_sha256(
        json.loads(amendment_path.read_text(encoding="utf-8")))

    client = DeterministicCanaryClient()
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=StubReviewer(), mode="api", deferral_list_path=deferral_path)

    assert outcome.halted_reason is None
    assert outcome.deferred_by_amendment == len(deferred_keys)
    assert outcome.deferral_amendment_sha256 == amendment_sha

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    reopened = CellResultStore(results_path)
    for key in deferred_keys:
        assert key not in reopened._results, "a deferred cell must never be attempted"

    # Never billed either: no judge_query/judge_verdict call was ever made FOR the deferred
    # judge (capability_qa is untouched by the deferral -- it is a judgment-only carve-out).
    judgment_calls = [c for c in client.calls if c.get("call_role") in ("judge_query", "judge_verdict")]
    assert not any(c["model"] == QWEN for c in judgment_calls)
    assert any(c["model"] == QWEN for c in client.calls if c.get("call_role") == "capability_qa")


def test_a_cell_in_both_the_deferral_list_and_the_context_blocklist_is_refused(
        tmp_path, repo_root, held_out_ids):
    protocol = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    plan_cells = phase3_plan.enumerate_canary_cells(protocol, GUARD_ROSTER, held_out_ids)
    deferred_keys = phase3_runner.mechanical_deferral_cell_keys(plan_cells, QWEN)

    # The blocklist names ONE of the exact cells the deferral list also names -- an ambiguous
    # exclusion, refused outright rather than silently resolved by filter order.
    blocklist_path = tmp_path / "blocklist.json"
    _write_blocklist(blocklist_path, namespace=str(protocol["cell_key_namespace"]),
                     cell_keys=[deferred_keys[0]])

    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir,
                               context_blocklist_canary_path=str(blocklist_path))
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    amendment_path = repo_root / "synthetic_amendment_overlap.json"
    amendment_payload = _write_amendment(amendment_path)
    amendment_sha = phase3_manifest.canonical_sha256(amendment_payload)
    deferral_path = tmp_path / "deferral.json"
    _write_deferral(deferral_path, amendment_tracked_path=amendment_path.name,
                    amendment_sha=amendment_sha, judge_model=QWEN, cell_keys=deferred_keys)

    with pytest.raises(phase3_runner.Phase3RunnerError, match="BOTH"):
        phase3_runner.run_phase3_canary(
            manifest_path, authorization_path, project_root=repo_root,
            client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api",
            context_blocklist_path=blocklist_path, deferral_list_path=deferral_path)


# ---------------------------------------------------------------------------
# tier 3: one real end-to-end v2 dry run -- the REAL frozen v2 protocol, REAL 6-judge roster,
# REAL amendment record and REAL shipped deferral list.
# ---------------------------------------------------------------------------


def _preseed_v2(root: Path, manifest: dict) -> dict:
    results_path = local_path(manifest["ledger"]["canary_results_path"])
    return preseed(
        protocol_path=root / PROTOCOL_V2_RELATIVE_PATH, project_root=root,
        main_bundle_path=root / phase3_manifest.MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH,
        canary_bundle_path=root / phase3_manifest.CANARY_TRANSCRIPT_BUNDLE_RELATIVE_PATH,
        verification_report_path=root / phase3_manifest.TRANSCRIPT_VERIFICATION_RELATIVE_PATH,
        target_store_path=results_path)


def _build_v2_manifest_for_this_test(root: Path, archive_dir: Path,
                                     anchor_store_path: Path) -> dict:
    return phase3_manifest.build_manifest(
        root / PROTOCOL_V2_RELATIVE_PATH, project_root=root,
        recorded_at_utc="2026-08-22T00:00:00Z",
        archive_dir=str(archive_dir).replace("\\", "/"), roster_judges=V2_ROSTER,
        estimator_validation_path=ESTIMATOR_VALIDATION_PATH,
        context_blocklist_canary_path=CONTEXT_BLOCKLIST_CANARY_V2_PATH,
        context_blocklist_main_path=CONTEXT_BLOCKLIST_MAIN_V2_PATH,
        anchor_carry_v1_protocol_path="rejudge/phase3_protocol.json",
        anchor_carry_store_path=str(anchor_store_path), crank_settings=dict(FROZEN_CRANK_SETTINGS))


def test_the_real_v2_canary_converges_with_qwens_192_cells_deferred(
        tmp_path, repo_root, held_out_ids):
    """The full v2 dry run: 6-judge roster, 540 preseeded transcript rows, 1,152 fresh judgment
    slots minus Qwen's 192 = 960 executed, 288 capability_qa cells (all 6 judges -- the carve-out
    is judgment-only), and the amendment/deferral binding reported correctly on the outcome."""
    archive_dir = tmp_path / "archive"
    anchor_store_path = tmp_path / "v1_anchor_store.jsonl"
    _build_synthetic_v1_anchor_store(repo_root, anchor_store_path, roster=V2_ROSTER)
    manifest = _build_v2_manifest_for_this_test(repo_root, archive_dir, anchor_store_path)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)

    seed_summary = _preseed_v2(repo_root, manifest)
    assert seed_summary["written"]["main"] == 492
    assert seed_summary["written"]["canary"] == 48

    deferral_path = REAL_DEFERRAL_LIST_PATH   # the REAL shipped, tracked artifact
    amendment = json.loads((repo_root / REAL_AMENDMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    amendment_sha = phase3_manifest.canonical_sha256(amendment)

    client = DeterministicCanaryClient()
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=StubReviewer(), mode="api", deferral_list_path=deferral_path)

    assert outcome.halted_reason is None
    assert outcome.deferred_by_amendment == 192
    assert outcome.deferral_amendment_sha256 == amendment_sha
    # The driver's own judgment_cells list is canary-scope only (rejudge.phase3_plan.
    # enumerate_canary_cells): 48 preseeded canary transcripts (skipped) + 960 fresh judgment
    # cells for the 5 healthy judges (completed) + 288 capability_qa (completed). The 492
    # preseeded MAIN-scope transcript rows already sit in this same store (a later main-run
    # stage's dependency), but this canary driver never enumerates or counts them at all.
    assert outcome.skipped == 48
    # The 288 capability anchors CARRY from the v1 identity (manifest binding) and are never
    # re-executed under a carrying manifest; the runner reports them separately.
    assert outcome.completed == 960
    assert outcome.anchors_carried == 288
    assert (outcome.completed + outcome.skipped + outcome.deferred_by_amendment
            + outcome.anchors_carried == 1488)

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    reopened = CellResultStore(results_path)

    protocol = phase3_plan.load_protocol(repo_root / PROTOCOL_V2_RELATIVE_PATH)
    plan_cells = phase3_plan.enumerate_canary_cells(protocol, V2_ROSTER, held_out_ids)
    deferred_keys = frozenset(phase3_runner.mechanical_deferral_cell_keys(plan_cells, QWEN))
    assert len(deferred_keys) == 192
    for key in deferred_keys:
        assert key not in reopened._results, "a deferred Qwen judgment cell must never appear"

    other_judgment_keys = {
        str(c["cell_key"]) for c in plan_cells
        if c["kind"] == phase3_plan.CANARY_JUDGMENT_KIND
        and c.get("judge_model") != QWEN}
    assert other_judgment_keys <= set(reopened._results)

    capability_keys = {
        str(c["cell_key"]) for c in plan_cells
        if c["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND}
    assert capability_keys <= set(reopened._results), (
        "capability_qa cells (including Qwen's) are untouched by the judgment-only carve-out")

    judgment_calls = [c for c in client.calls if c.get("call_role") in ("judge_query", "judge_verdict")]
    assert not any(c["model"] == QWEN for c in judgment_calls), (
        "the deferred judge must never be billed for a judgment call")
