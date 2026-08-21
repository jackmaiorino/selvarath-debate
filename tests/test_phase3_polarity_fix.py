"""The mirroring fix (2026-08-21): _polarity's side-XOR, and phase3_polarity_verify's
structural gate, proven end to end.

Context: rejudge/phase3_incident1_mirroring_2026-08-21.json and
rejudge/phase3_codex_mirroring_consult_2026-08-21.md (section 5: "v2 verification must happen
on rendered prompt pairs end to end"; section 3: the redesigned structural gates).

Three things this file proves:

1. ``rejudge.phase2_canary_execute._polarity`` is BYTE-IDENTICAL to the pre-fix function for
   every phase-2-shaped cell (``ResolvedCell.judgment_replicates_per_side`` left at its default
   of ``None``) -- proven against the ACTUAL pre-fix module (git HEAD at the start of this
   worktree, before any of this fix's edits landed), loaded as an isolated module, exactly the
   ``tests/test_phase3_amendment4_context_guard.py`` technique
   (``test_phase2_shaped_blocked_exchange_is_byte_identical_pre_and_post_amendment``).
2. The side-XOR arithmetic itself, directly: side 0 keeps the per-question base draw, side 1
   flips it, generalized correctly for ``judgment_replicates_per_side > 1``.
3. Driven end to end through a small synthetic canary (reusing tests/test_phase3_runner.py's
   dry-run fixture machinery -- the SAME ``repo_root``/``held_out_ids`` fixtures and manifest/
   authorization/preseed helpers), every phase-3 K2 pair is genuinely mirrored in the RENDERED
   judge prompts -- verified using ``scripts.phase3_polarity_verify``'s own logic, never
   metadata -- both sides complete, and the per-judge realized A/B split is EXACTLY balanced (a
   structural identity of true mirroring: each matched pair contributes exactly one A-correct
   and one B-correct realization, regardless of the per-question base draw's own imbalance).
"""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from rejudge import phase2_plan, phase3_plan, phase3_runner
from rejudge import phase2_canary_cells as cells_mod
from rejudge import phase2_canary_execute as new_module
from rejudge.phase2_canary_cells import ResolvedCell
from rejudge.phase2_canary_fixtures import DeterministicCanaryClient, StubReviewer
from rejudge.phase2_canary_live import local_path
from rejudge.phase2_dual_gate import DualGateDecisionStore
from scripts import phase3_polarity_verify as pv

# Reuse tests/test_phase3_runner.py's dry-run fixture machinery rather than re-deriving it:
# same real 7-candidate roster subset, same manifest/authorization/preseed helpers, same
# module-scoped repo_root/held_out_ids fixtures.
from tests.test_phase3_runner import (  # noqa: F401 -- repo_root/held_out_ids are fixtures
    GUARD_ROSTER, _authorization_for, _build_manifest, _preseed,
    _write_manifest_and_authorization, held_out_ids, reference_question_ids, repo_root,
)

ROOT = Path(__file__).resolve().parents[1]
ANCHOR = "Qwen/Qwen2.5-7B-Instruct-Turbo"
# HEAD of this worktree at the start of the mirroring fix, i.e. before any of _polarity's,
# ResolvedCell's, or phase3_runner._resolve_judgment_cell's edits for this fix landed.
PRE_FIX_COMMIT = "34898fb"


def _load_pre_fix_execute_module():
    old_source = subprocess.run(
        ["git", "show", f"{PRE_FIX_COMMIT}:rejudge/phase2_canary_execute.py"], cwd=str(ROOT),
        capture_output=True, text=True, check=True).stdout
    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8") as handle:
        handle.write(old_source)
        old_path = handle.name
    try:
        spec = importlib.util.spec_from_file_location("_pre_fix_phase2_canary_execute", old_path)
        module = importlib.util.module_from_spec(spec)
        # dataclasses' string-annotation resolution (CellContext's fields) looks the module up
        # in sys.modules by __name__; a module built via spec_from_file_location is never
        # inserted there automatically, so @dataclass would otherwise crash resolving `Any`.
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            del sys.modules[spec.name]
    finally:
        Path(old_path).unlink()
    return module


def _phase2_shaped_cell(**overrides) -> ResolvedCell:
    base = dict(
        cell_key="k", kind="debate_judgment", condition="sequential_b2",
        question_id="CN-021", judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        debater_model="Qwen/Qwen3.7-Plus", transcript_index=1, replicate_index=1,
        query_budget=2, dependency_keys=(), composition={}, transcript_protocol_name=None,
        oracle_mode="clean", arm_name="clean",
    )
    base.update(overrides)
    return ResolvedCell(**base)


# ---------------------------------------------------------------------------------------------
# 1. phase-2-shaped cells: _polarity, and a genuine execute_cell drive, byte-identical
# ---------------------------------------------------------------------------------------------


def test_polarity_is_byte_identical_for_phase2_shaped_cells_against_the_pre_fix_module():
    old_module = _load_pre_fix_execute_module()
    cases = [
        dict(question_id="CN-021", transcript_index=0, judge_model="j1", query_budget=0,
             arm_name="clean", replicate_index=0),
        dict(question_id="CN-021", transcript_index=0, judge_model="j1", query_budget=0,
             arm_name="clean", replicate_index=1),
        dict(question_id="VS-019", transcript_index=2, judge_model="j2", query_budget=2,
             arm_name="placebo", replicate_index=1),
        dict(question_id="SEL-030", transcript_index=1, judge_model="j3", query_budget=5,
             arm_name="both", replicate_index=0),
    ]
    for case in cases:
        cell = _phase2_shaped_cell(**case)
        # judgment_replicates_per_side didn't exist on the pre-fix ResolvedCell at all; the new
        # dataclass field must default to None so a caller that never sets it (every phase-2
        # resolver) is unaffected, and _polarity's output must match the pre-fix function exactly.
        assert cell.judgment_replicates_per_side is None
        assert new_module._polarity(cell) == old_module._polarity(cell)


def test_phase2_shaped_b0_judgment_record_is_byte_identical_to_the_pre_fix_module(tmp_path):
    """A genuine drive through execute_cell (transcript, then a b0 judgment depending on it),
    real phase-2 canary protocol/bundle, real question content -- not just the isolated
    function. ``phase2_canary_cells.resolve_cell`` never sets ``judgment_replicates_per_side``,
    so this is the exact phase-2 shape every real phase-2 caller produces."""
    old_module = _load_pre_fix_execute_module()
    protocol = json.loads(Path(ROOT / "rejudge" / "phase2_protocol.json").read_text(
        encoding="utf-8"))
    bundle = json.loads(Path(ROOT / "rejudge" / "phase2_prompt_bundle.json").read_text(
        encoding="utf-8"))

    transcript_raw = next(c for c in phase2_plan.enumerate_canary_cells(protocol)
                          if c["kind"] == "canary_debate_transcript"
                          and c["condition"] == "canary_blind_uncapped_3_round")
    judgment_raw = next(c for c in phase2_plan.enumerate_canary_cells(protocol)
                        if c["kind"] == "canary_debate_judgment" and c["condition"] == "b0")

    transcript_resolved = cells_mod.resolve_cell(transcript_raw, protocol, bundle,
                                                 anchor_judge_model=ANCHOR)
    judgment_resolved = cells_mod.resolve_cell(judgment_raw, protocol, bundle,
                                               anchor_judge_model=ANCHOR)
    judgment_resolved = dataclasses.replace(
        judgment_resolved, dependency_keys=(transcript_resolved.cell_key,))
    assert judgment_resolved.judgment_replicates_per_side is None

    def _run(module, tag):
        context = module.CellContext(
            client=DeterministicCanaryClient(), protocol=protocol, bundle=bundle,
            decision_store=DualGateDecisionStore(tmp_path / f"decisions_{tag}.jsonl"),
            reviewer=StubReviewer(), anchor_judge_model=ANCHOR, results={})
        transcript = module.execute_cell(transcript_resolved, context)
        context.results[transcript_resolved.cell_key] = transcript
        return module.execute_cell(judgment_resolved, context)

    old_record = _run(old_module, "old")
    new_record = _run(new_module, "new")

    def _stable(record):
        return {k: v for k, v in record.items() if k not in ("created_at", "harness_version")}

    assert json.dumps(_stable(old_record), sort_keys=True) == json.dumps(
        _stable(new_record), sort_keys=True)


# ---------------------------------------------------------------------------------------------
# 2. the side-XOR arithmetic, directly
# ---------------------------------------------------------------------------------------------


def test_polarity_side_xor_flips_exactly_the_odd_side():
    from rejudge.config import ARMS, position_for
    from rejudge.phase2_canary_execute import _polarity

    expected_base = position_for(ARMS["clean"], "CN-021", 0, "j", 0)

    side0 = _phase2_shaped_cell(question_id="CN-021", transcript_index=0, judge_model="j",
                                query_budget=0, arm_name="clean", replicate_index=0,
                                judgment_replicates_per_side=1)
    side1 = _phase2_shaped_cell(question_id="CN-021", transcript_index=0, judge_model="j",
                                query_budget=0, arm_name="clean", replicate_index=1,
                                judgment_replicates_per_side=1)
    assert _polarity(side0) == expected_base
    assert _polarity(side1) == (not expected_base)

    # judgment_replicates_per_side=2: replicate_index 0,1 -> side 0 (within 0,1);
    # replicate_index 2,3 -> side 1 (within 0,1). Only the SIDE (the floor division), never the
    # within-side replicate, ever flips the polarity.
    for replicate_index, expect_flip in ((0, False), (1, False), (2, True), (3, True)):
        cell = _phase2_shaped_cell(question_id="CN-021", transcript_index=0, judge_model="j",
                                   query_budget=0, arm_name="clean",
                                   replicate_index=replicate_index,
                                   judgment_replicates_per_side=2)
        assert _polarity(cell) == (not expected_base if expect_flip else expected_base)


def test_phase3_resolver_supplies_the_unfold_divisor_from_the_frozen_protocol():
    """rejudge.phase3_runner._resolve_judgment_cell is the one caller that must supply
    judgment_replicates_per_side, read from the frozen protocol's own
    judgment_replicates_per_transcript_side rather than a hard-coded value."""
    protocol = phase3_plan.load_protocol(ROOT / "rejudge" / "phase3_protocol.json")
    bundle = json.loads(
        (ROOT / phase3_runner.PHASE2_PROMPT_BUNDLE_RELATIVE_PATH).read_text(encoding="utf-8"))
    conditions = phase3_runner._condition_lookup(protocol)
    for condition_id, condition in conditions.items():
        cell = {"cell_key": "k", "kind": phase3_plan.CANARY_JUDGMENT_KIND,
               "condition": condition_id, "question_id": "CN-021",
               "judge_model": "j", "debater_model": "d", "transcript_index": 0,
               "replicate_index": 1, "dependency_keys": []}
        resolved = phase3_runner._resolve_judgment_cell(cell, conditions=conditions, bundle=bundle)
        assert resolved.judgment_replicates_per_side == int(
            condition["judgment_replicates_per_transcript_side"])


# ---------------------------------------------------------------------------------------------
# 3. end to end: a small synthetic canary, corrected code, verified via
#    scripts.phase3_polarity_verify's own logic (never metadata)
# ---------------------------------------------------------------------------------------------


def test_corrected_canary_is_genuinely_mirrored_end_to_end(tmp_path, repo_root, held_out_ids):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    client = DeterministicCanaryClient()
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=StubReviewer(), mode="api")
    assert outcome.halted_reason is None
    assert outcome.paused == 0

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    rows = pv.load_result_rows(results_path)

    protocol = phase3_plan.load_protocol(ROOT / "rejudge" / "phase3_protocol.json")
    from rejudge.debate_gen import _load_question_bank
    question_bank = _load_question_bank()

    report = pv.verify(rows, protocol=protocol, judges=GUARD_ROSTER,
                       held_out_ids=held_out_ids, question_bank=question_bank)

    assert report["n_incomplete_or_missing_side_groups"] == 0
    assert report["n_rows_unparseable"] == 0
    assert report["pairs_total"] > 0
    assert report["pairs_duplicated"] == 0
    assert report["pairs_other_inconsistent_shape"] == 0
    assert report["pairs_mirrored"] == report["pairs_total"]
    assert report["pairs_mirrored_pct"] == 100.0
    assert report["n_pairs_inconsistent_across_conditions"] == 0

    # Structural identity of true mirroring: each matched pair contributes exactly one
    # A-correct and one B-correct realization, so the totals balance EXACTLY per judge --
    # regardless of the per-question base draw's own imbalance.
    for judge, counts in report["per_judge_realized_split"].items():
        assert counts["unresolved"] == 0, judge
        assert counts["realized_A_correct"] == counts["realized_B_correct"], judge
        assert counts["realized_A_correct"] > 0, judge


def test_uncorrected_polarity_would_have_shown_the_old_100_percent_duplication(
        tmp_path, repo_root, held_out_ids):
    """Sanity check on the verification tool itself: run the SAME synthetic canary through the
    PRE-FIX ``_polarity`` (monkeypatched in), and confirm ``phase3_polarity_verify`` reports the
    historical defect (0% mirrored, 100% duplicated) rather than a fixture artifact -- i.e. the
    100% mirrored result above is a genuine property of the fix, not of the fixture."""
    old_module = _load_pre_fix_execute_module()

    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    import rejudge.phase2_canary_execute as execute_module
    original_polarity = execute_module._polarity
    try:
        execute_module._polarity = old_module._polarity
        client = DeterministicCanaryClient()
        outcome = phase3_runner.run_phase3_canary(
            manifest_path, authorization_path, project_root=repo_root, client=client,
            reviewer=StubReviewer(), mode="api")
    finally:
        execute_module._polarity = original_polarity
    assert outcome.halted_reason is None

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    rows = pv.load_result_rows(results_path)
    protocol = phase3_plan.load_protocol(ROOT / "rejudge" / "phase3_protocol.json")
    from rejudge.debate_gen import _load_question_bank
    question_bank = _load_question_bank()

    report = pv.verify(rows, protocol=protocol, judges=GUARD_ROSTER,
                       held_out_ids=held_out_ids, question_bank=question_bank)
    assert report["pairs_total"] > 0
    assert report["pairs_mirrored"] == 0
    assert report["pairs_duplicated"] == report["pairs_total"]
