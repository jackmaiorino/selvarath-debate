"""Unit tests for rejudge.phase3_orchestrator_support -- the v2 canary orchestrator's pure logic.

Extracted out of scripts/phase3_canary_orchestrator.sh's inline $VENV -c one-liners specifically
so it could be tested directly, without shelling out to bash. Covers: crank-settings recovery,
v2 convergence arithmetic (anchor cells excluded from the denominator), the core-b0 filtering
helper, and the post-convergence polarity-gate pass/fail decision (evaluate_polarity_gate),
stubbed against synthetic verifier-report shapes rather than a live polarity_verify.py run.
"""
import json
from pathlib import Path

import pytest

from rejudge import phase3_orchestrator_support as support
from rejudge import phase3_plan, phase3_runner

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_V2_PATH = ROOT / "rejudge" / "phase3_protocol_v2.json"
V2_ROSTER = [
    "Qwen/Qwen2.5-7B-Instruct-Turbo", "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo", "openai/gpt-oss-120b",
    "google/gemma-3n-E4B-it", "Qwen/Qwen3.7-Max",
]
# v2 amendment 1 (2026-08-22), the bounded Qwen2.5-7B carve-out: the REAL, shipped, tracked
# deferral list -- used here as a fixture-free, always-in-sync-with-the-current-protocol input,
# exactly like the real orchestrator would resolve it.
DEFERRAL_LIST_PATH = ROOT / "rejudge" / "phase3_v2_qwen_deferral_cells_2026-08-22.json"


def _v2_manifest_stub() -> dict:
    """The minimal manifest shape these functions actually read: protocol_tracked_path and
    roster.judges. Deliberately not a full, hash-bound execution manifest -- these functions
    never validate manifest identity, only read these two fields."""
    return {
        "protocol_tracked_path": str(PROTOCOL_V2_PATH).replace("\\", "/"),
        "roster": {"judges": list(V2_ROSTER)},
    }


# ---------------------------------------------------------------------------
# resolve_crank_settings
# ---------------------------------------------------------------------------


def test_resolve_crank_settings_reads_the_frozen_values():
    manifest = {"frozen_inputs": {"crank_settings": {
        "review_daemon_concurrency": 12, "max_waves_per_round": 4}}}
    assert support.resolve_crank_settings(manifest) == {
        "review_daemon_concurrency": 12, "max_waves_per_round": 4}


def test_resolve_crank_settings_missing_binding_raises_keyerror():
    manifest = {"frozen_inputs": {}}
    with pytest.raises(KeyError):
        support.resolve_crank_settings(manifest)


# ---------------------------------------------------------------------------
# remaining_canary_cells -- v2 convergence arithmetic, anchor cells excluded
# ---------------------------------------------------------------------------


def test_remaining_canary_cells_v2_denominator_excludes_anchor_cells():
    # 1,152 fresh judgment slots + 540 preseeded transcript rows = 1,692 -- the 288
    # capability-anchor cells never enter this count at all (they carry from v1).
    manifest = _v2_manifest_stub()
    counts = support.remaining_canary_cells(manifest, ROOT / "no-such-results-file.jsonl",
                                             project_root=ROOT)
    assert counts["manifested"] == 1692
    assert counts["completed"] == 0
    assert counts["remaining"] == 1692


def test_remaining_canary_cells_against_the_real_preseeded_v2_store():
    # Integration-style check against the LIVE v2 store, which accumulates judgment rows as the
    # canary runs, so exact counts are a moving target. The invariants that must always hold:
    # the manifested total is the fixed v2 inventory, the 540 preseeded transcript rows are a
    # floor on completed, and remaining is exactly their difference.
    store_path = Path("E:/selvarath-archive/phase3-v2-2026-08-21/phase3_canary_results.jsonl")
    if not store_path.exists():
        pytest.skip("v2 archive store not present in this environment")
    manifest = _v2_manifest_stub()
    counts = support.remaining_canary_cells(manifest, store_path, project_root=ROOT)
    assert counts["manifested"] == 1692
    assert counts["completed"] >= 540
    assert counts["remaining"] == counts["manifested"] - counts["completed"]


def test_remaining_canary_cells_only_counts_cells_the_current_plan_actually_has(tmp_path):
    # A results row for a cell_key NOT in the current plan (e.g. a stale/foreign key) must never
    # be counted as "completed" -- completed is intersected with manifested.
    manifest = _v2_manifest_stub()
    results_path = tmp_path / "results.jsonl"
    results_path.write_text(json.dumps({"cell_key": "not-a-real-cell-key"}) + "\n",
                            encoding="utf-8")
    counts = support.remaining_canary_cells(manifest, results_path, project_root=ROOT)
    assert counts["completed"] == 0
    assert counts["remaining"] == 1692


# ---------------------------------------------------------------------------
# remaining_canary_cells with deferral_list_path -- v2 amendment 1's Qwen carve-out
# ---------------------------------------------------------------------------


def test_remaining_canary_cells_deferral_list_excludes_the_deferred_judges_cells():
    # 1,692 - 192 deferred Qwen judgment cells = 1,500.
    manifest = _v2_manifest_stub()
    counts = support.remaining_canary_cells(
        manifest, ROOT / "no-such-results-file.jsonl", project_root=ROOT,
        deferral_list_path=DEFERRAL_LIST_PATH)
    assert counts["manifested"] == 1500
    assert counts["completed"] == 0
    assert counts["remaining"] == 1500


def test_remaining_canary_cells_without_a_deferral_list_path_is_unchanged():
    # Omitting deferral_list_path (the default) must reproduce the pre-amendment-1 arithmetic
    # exactly -- this parameter is additive, never a behavior change for an existing caller.
    manifest = _v2_manifest_stub()
    counts = support.remaining_canary_cells(
        manifest, ROOT / "no-such-results-file.jsonl", project_root=ROOT)
    assert counts["manifested"] == 1692
    assert counts["remaining"] == 1692


def test_remaining_canary_cells_a_stray_row_for_a_deferred_cell_never_counts_as_completed(
        tmp_path):
    # A deferred cell is excluded from "manifested" entirely, so even a stray/foreign result
    # row recorded against one (which should never happen -- the driver refuses to attempt a
    # deferred cell) can never inflate "completed".
    protocol = phase3_plan.load_protocol(PROTOCOL_V2_PATH)
    _main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, ROOT)
    plan_cells = phase3_plan.enumerate_canary_cells(protocol, V2_ROSTER, held_out_ids)
    one_deferred_key = phase3_runner.mechanical_deferral_cell_keys(
        plan_cells, "Qwen/Qwen2.5-7B-Instruct-Turbo")[0]

    manifest = _v2_manifest_stub()
    results_path = tmp_path / "results.jsonl"
    results_path.write_text(json.dumps({"cell_key": one_deferred_key}) + "\n", encoding="utf-8")
    counts = support.remaining_canary_cells(
        manifest, results_path, project_root=ROOT, deferral_list_path=DEFERRAL_LIST_PATH)
    assert counts["manifested"] == 1500
    assert counts["completed"] == 0
    assert counts["remaining"] == 1500


def test_remaining_canary_cells_deferral_list_refuses_a_tampered_amendment_binding(tmp_path):
    tampered = json.loads(DEFERRAL_LIST_PATH.read_text(encoding="utf-8"))
    tampered["amendment"]["canonical_sha256"] = "0" * 64
    tampered_path = tmp_path / "tampered_deferral.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")

    manifest = _v2_manifest_stub()
    with pytest.raises(phase3_runner.Phase3RunnerError, match="drifted or tampered"):
        support.remaining_canary_cells(
            manifest, ROOT / "no-such-results-file.jsonl", project_root=ROOT,
            deferral_list_path=tampered_path)


def test_remaining_canary_cells_deferral_list_refuses_a_wrong_cell_set(tmp_path):
    tampered = json.loads(DEFERRAL_LIST_PATH.read_text(encoding="utf-8"))
    tampered["cell_keys"] = tampered["cell_keys"][:-1]   # drop one -- no longer the mechanical set
    tampered_path = tmp_path / "tampered_deferral.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")

    manifest = _v2_manifest_stub()
    with pytest.raises(phase3_runner.Phase3RunnerError,
                       match="does not match the mechanical rule's output"):
        support.remaining_canary_cells(
            manifest, ROOT / "no-such-results-file.jsonl", project_root=ROOT,
            deferral_list_path=tampered_path)


def test_remaining_canary_cells_a_real_cell_key_counts_as_completed():
    manifest = _v2_manifest_stub()
    protocol = phase3_plan.load_protocol(PROTOCOL_V2_PATH)
    _main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, ROOT)
    plan_cells = phase3_plan.enumerate_canary_cells(protocol, V2_ROSTER, held_out_ids)
    one_transcript_key = next(
        str(c["cell_key"]) for c in plan_cells if c["kind"] == phase3_plan.CANARY_TRANSCRIPT_KIND)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        results_path = Path(tmp) / "results.jsonl"
        results_path.write_text(json.dumps({"cell_key": one_transcript_key}) + "\n",
                                encoding="utf-8")
        counts = support.remaining_canary_cells(manifest, results_path, project_root=ROOT)
    assert counts["completed"] == 1
    assert counts["remaining"] == 1691


# ---------------------------------------------------------------------------
# condition_cell_keys / write_condition_filtered_results
# ---------------------------------------------------------------------------


def test_condition_cell_keys_b0_count_matches_the_core_gate_population():
    manifest = _v2_manifest_stub()
    keys = support.condition_cell_keys(manifest, condition="b0", project_root=ROOT)
    # 24 held-out questions x 2 debaters x 1 transcript x K2(2) x replicates(1) x 6 judges.
    assert len(keys) == 576


def test_condition_cell_keys_disjoint_across_conditions():
    manifest = _v2_manifest_stub()
    b0_keys = support.condition_cell_keys(manifest, condition="b0", project_root=ROOT)
    b8_keys = support.condition_cell_keys(manifest, condition="sequential_b8", project_root=ROOT)
    assert b0_keys.isdisjoint(b8_keys)


def test_write_condition_filtered_results_keeps_only_matching_rows(tmp_path):
    results_path = tmp_path / "results.jsonl"
    out_path = tmp_path / "filtered.jsonl"
    rows = [
        {"cell_key": "keep-1", "result": {}},
        {"cell_key": "drop-1", "result": {}},
        {"cell_key": "keep-2", "result": {}},
    ]
    results_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    written = support.write_condition_filtered_results(
        results_path, out_path, {"keep-1", "keep-2"})
    assert written == 2
    kept_keys = {json.loads(line)["cell_key"] for line in out_path.read_text(
        encoding="utf-8").splitlines() if line.strip()}
    assert kept_keys == {"keep-1", "keep-2"}


def test_write_condition_filtered_results_missing_source_writes_empty_file(tmp_path):
    out_path = tmp_path / "filtered.jsonl"
    written = support.write_condition_filtered_results(
        tmp_path / "does-not-exist.jsonl", out_path, {"anything"})
    assert written == 0
    assert out_path.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# evaluate_polarity_gate
# ---------------------------------------------------------------------------


def _passing_full_report() -> dict:
    return {
        "pairs_total": 100, "pairs_mirrored_pct": 100.0, "pairs_duplicated": 0,
        "pairs_other_inconsistent_shape": 0, "n_incomplete_or_missing_side_groups": 0,
        "n_pairs_inconsistent_across_conditions": 0,
    }


def _passing_b0_report() -> dict:
    return {"per_judge_realized_split": {
        judge: {"realized_A_correct": 48, "realized_B_correct": 48, "unresolved": 0}
        for judge in V2_ROSTER
    }}


def test_evaluate_polarity_gate_passes_on_true_mirroring():
    problems = support.evaluate_polarity_gate(_passing_full_report(), _passing_b0_report())
    assert problems == []


def test_evaluate_polarity_gate_fails_on_zero_pairs():
    full = dict(_passing_full_report())
    full["pairs_total"] = 0
    problems = support.evaluate_polarity_gate(full, _passing_b0_report())
    assert any("no judged pairs found" in p for p in problems)


def test_evaluate_polarity_gate_fails_below_100_percent_mirrored():
    full = dict(_passing_full_report())
    full["pairs_mirrored_pct"] = 99.9
    problems = support.evaluate_polarity_gate(full, _passing_b0_report())
    assert any("pairs_mirrored_pct" in p for p in problems)


def test_evaluate_polarity_gate_fails_on_duplicated_pairs():
    full = dict(_passing_full_report())
    full["pairs_duplicated"] = 3
    problems = support.evaluate_polarity_gate(full, _passing_b0_report())
    assert any("duplicated pair" in p for p in problems)


def test_evaluate_polarity_gate_fails_on_incomplete_side_groups():
    full = dict(_passing_full_report())
    full["n_incomplete_or_missing_side_groups"] = 2
    problems = support.evaluate_polarity_gate(full, _passing_b0_report())
    assert any("incomplete/missing side group" in p for p in problems)


def test_evaluate_polarity_gate_fails_on_cross_condition_inconsistency():
    full = dict(_passing_full_report())
    full["n_pairs_inconsistent_across_conditions"] = 1
    problems = support.evaluate_polarity_gate(full, _passing_b0_report())
    assert any("cross-condition inconsistency" in p for p in problems)


def test_evaluate_polarity_gate_fails_on_wrong_core_b0_split():
    b0 = {"per_judge_realized_split": {
        "Qwen/Qwen2.5-7B-Instruct-Turbo": {
            "realized_A_correct": 52, "realized_B_correct": 44, "unresolved": 0}}}
    problems = support.evaluate_polarity_gate(_passing_full_report(), b0)
    assert any("split is" in p and "48/48/0" in p for p in problems)


def test_evaluate_polarity_gate_fails_on_missing_core_b0_split():
    problems = support.evaluate_polarity_gate(
        _passing_full_report(), {"per_judge_realized_split": {}})
    assert any("no per-judge split computed" in p for p in problems)


def test_evaluate_polarity_gate_fails_on_unresolved_core_b0_rows():
    b0 = {"per_judge_realized_split": {
        "Qwen/Qwen2.5-7B-Instruct-Turbo": {
            "realized_A_correct": 47, "realized_B_correct": 48, "unresolved": 1}}}
    problems = support.evaluate_polarity_gate(_passing_full_report(), b0)
    assert problems
