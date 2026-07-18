import json
import subprocess
import sys
from pathlib import Path

import pytest

from rejudge import phase2_checker_sampling as sampling
from rejudge.query_screen import screen_query


REPO_ROOT = Path(__file__).resolve().parents[1]

real = pytest.mark.skipif(
    not (sampling.RECORDS_PATH.exists() and sampling.TRANSCRIPTS_PATH.exists()),
    reason="rejudge/output/records.jsonl and/or data/transcripts.jsonl not present",
)


# ---------------------------------------------------------------------------
# Synthetic pairs: pure, no data dependency
# ---------------------------------------------------------------------------

def test_synthetic_pair_count_and_ids_are_unique_and_complete():
    assert len(sampling.SYNTHETIC_PAIRS) == sampling.SYNTHETIC_PAIR_COUNT
    pair_ids = [pair["pair_id"] for pair in sampling.SYNTHETIC_PAIRS]
    assert len(set(pair_ids)) == len(pair_ids)


def test_synthetic_pairs_are_grounded_in_known_worlds():
    for pair in sampling.SYNTHETIC_PAIRS:
        assert pair["world"] in sampling.WORLDS
        assert pair["category"] in sampling.SYNTHETIC_CATEGORIES


def test_synthetic_world_distribution_matches_round_robin():
    from collections import Counter
    counts = Counter(pair["world"] for pair in sampling.SYNTHETIC_PAIRS)
    assert counts == {"carath_norn": 7, "selvarath": 7, "vethun_sarak": 6}


def test_synthetic_category_distribution_is_five_each():
    from collections import Counter
    counts = Counter(pair["category"] for pair in sampling.SYNTHETIC_PAIRS)
    assert counts == {category: 5 for category in sampling.SYNTHETIC_CATEGORIES}


def test_every_synthetic_claim_passes_the_mechanical_screen():
    for pair in sampling.SYNTHETIC_PAIRS:
        for key in ("allowed_claim", "violation_claim"):
            result = screen_query(pair[key], pair["candidate_a"], pair["candidate_b"])
            assert result.allowed, (
                f"{pair['pair_id']} {key} unexpectedly rejected: {result.reasons!r}"
            )


def test_build_synthetic_items_produces_complete_pairs_with_both_roles():
    items = sampling.build_synthetic_items()
    assert len(items) == sampling.SYNTHETIC_TOTAL
    by_pair: dict[str, set[str]] = {}
    for item in items:
        assert item.synthetic is True
        assert item.question_id is None
        pair_id = item.pair_id
        role = item.synthetic_role
        assert isinstance(pair_id, str)
        assert isinstance(role, str)
        by_pair.setdefault(pair_id, set()).add(role)
    assert set(by_pair) == {pair["pair_id"] for pair in sampling.SYNTHETIC_PAIRS}
    assert all(roles == {"allowed", "violation"} for roles in by_pair.values())


# ---------------------------------------------------------------------------
# Real-data universe and sets (skipped if the data files are absent)
# ---------------------------------------------------------------------------

@real
def test_universe_is_deduplicated_and_screened():
    universe = sampling.build_universe()
    assert len(universe) > 0
    # Matches build_universe()'s actual dedup key exactly: `source` is deliberately
    # excluded (see its docstring) because the same (question_id, claim) pair must be
    # deduped ACROSS stage1 and calibration sources, not merely within each source.
    # Including `source` here would be a strictly weaker invariant that misses a
    # regression reintroducing a per-source `seen` set.
    keys = [(item.question_id, item.raw_query) for item in universe]
    assert len(set(keys)) == len(keys)
    for item in universe:
        expected = screen_query(item.raw_query, item.candidate_a, item.candidate_b)
        assert item.mechanical == expected
        assert item.world in sampling.WORLDS


@real
def test_same_seed_rerun_is_byte_identical():
    first = sampling.build_all()
    second = sampling.build_all()
    assert sampling.render_json(first.primary_set) == sampling.render_json(second.primary_set)
    assert sampling.render_json(first.regression_set) == sampling.render_json(second.regression_set)
    assert sampling.render_json(first.reserve_pool) == sampling.render_json(second.reserve_pool)


@real
def test_cli_rerun_across_separate_processes_is_byte_identical(tmp_path):
    # test_same_seed_rerun_is_byte_identical above only reruns build_all() twice inside
    # ONE interpreter -- same PYTHONHASHSEED, same process state throughout. The module
    # docstring's "a rerun against unchanged input files reproduces byte-identical JSON"
    # is naturally read as surviving separate invocations (e.g. re-running the CLI
    # tomorrow), so this drives the actual `--build` CLI as two independent OS
    # subprocesses and compares their written output byte-for-byte.
    def run_build(out_dir: Path) -> tuple[str, str, str]:
        primary = out_dir / "primary.json"
        regression = out_dir / "regression.json"
        reserve = out_dir / "reserve.json"
        subprocess.run(
            [
                sys.executable, "-m", "rejudge.phase2_checker_sampling", "--build",
                "--primary-set-path", str(primary),
                "--regression-set-path", str(regression),
                "--reserve-pool-path", str(reserve),
            ],
            cwd=REPO_ROOT, check=True, capture_output=True, text=True,
        )
        return (
            primary.read_text(encoding="utf-8"),
            regression.read_text(encoding="utf-8"),
            reserve.read_text(encoding="utf-8"),
        )

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = run_build(first_dir)
    second = run_build(second_dir)
    assert first == second


@real
def test_primary_set_counts_and_strata_are_exact():
    built = sampling.build_all()
    primary = built.primary_set
    assert len(primary["items"]) == sampling.PRIMARY_TOTAL == 200
    counts = primary["counts"]
    assert counts["world_stratified"] == 120
    assert counts["world_stratified_per_world"] == {
        world: sampling.WORLD_STRATIFIED_PER_WORLD for world in sampling.WORLDS
    }
    assert counts["boundary"] == 40
    assert counts["boundary_per_heuristic"] == {
        heuristic.name: sampling.BOUNDARY_PER_HEURISTIC
        for heuristic in sampling.BOUNDARY_HEURISTICS
    }
    assert counts["synthetic"] == 40
    assert counts["synthetic_pairs"] == 20


@real
def test_primary_set_items_all_pass_the_mechanical_screen():
    built = sampling.build_all()
    for row in built.primary_set["items"]:
        result = screen_query(row["raw_query"], row["candidate_a"], row["candidate_b"])
        assert result.allowed, f"primary item {row['item_id']} unexpectedly fails screen"
        assert row["mechanical_reasons"] == []


@real
def test_primary_set_item_ids_are_unique():
    built = sampling.build_all()
    item_ids = [row["item_id"] for row in built.primary_set["items"]]
    assert len(set(item_ids)) == len(item_ids) == 200


@real
def test_regression_set_is_exactly_60_and_all_mechanically_rejected():
    built = sampling.build_all()
    regression = built.regression_set
    assert len(regression["items"]) == sampling.REGRESSION_TOTAL == 60
    for row in regression["items"]:
        result = screen_query(row["raw_query"], row["candidate_a"], row["candidate_b"])
        assert not result.allowed, f"regression item {row['item_id']} unexpectedly passes screen"
        assert row["mechanical_reasons"] == list(result.reasons)
    item_ids = [row["item_id"] for row in regression["items"]]
    assert len(set(item_ids)) == len(item_ids)


@real
def test_regression_set_counts_sum_to_total_and_report_the_frozen_mismatch():
    built = sampling.build_all()
    counts = built.regression_set["counts"]
    assert (
        counts["candidate_restatement"]
        + counts["meta_or_evaluative"]
        + counts["answer_or_debate_reference"]
        + counts["compound_claim"]
        == 60
    )
    mismatch = built.regression_set["frozen_design_vs_actual"]
    assert mismatch["candidate_restatement_design_target"] == 8
    assert mismatch["meta_or_evaluative_design_target"] == 18
    # The actual counts are the real, reported (not fabricated) totals available in the
    # frozen universe; this test pins them so silent data drift is caught.
    assert mismatch["candidate_restatement_actual_available"] == counts["candidate_restatement"]
    assert mismatch["meta_or_evaluative_actual_available"] == counts["meta_or_evaluative"]


@real
def test_reserve_pool_is_at_least_minimum_and_world_stratified_round_robin():
    built = sampling.build_all()
    reserve = built.reserve_pool
    assert len(reserve["items"]) >= sampling.RESERVE_MINIMUM
    assert reserve["order"] == "world_stratified_round_robin"
    worlds_in_order = [row["world"] for row in reserve["items"]]
    # Round robin over sorted(WORLDS): position i's world is WORLDS[i % 3].
    expected = [sampling.WORLDS[i % len(sampling.WORLDS)] for i in range(len(worlds_in_order))]
    assert worlds_in_order == expected
    for row in reserve["items"]:
        result = screen_query(row["raw_query"], row["candidate_a"], row["candidate_b"])
        assert result.allowed


@real
def test_reserve_pool_order_is_stable_across_reruns():
    first = sampling.build_all().reserve_pool
    second = sampling.build_all().reserve_pool
    first_ids = [row["item_id"] for row in first["items"]]
    second_ids = [row["item_id"] for row in second["items"]]
    assert first_ids == second_ids


@real
def test_reserve_pool_within_each_world_item_ids_are_ascending():
    # The reserve pool's own order_note promises the minimum-top-up rule always draws
    # replacements "in this exact pre-declared order" because each world's pool is
    # pre-sorted ascending by item_id before round-robin interleaving. The round-robin
    # world-cycling test above and the rerun-stability test above it both hold even if
    # this per-world sort were reversed or dropped, so this checks the documented
    # ascending-within-world contract directly.
    built = sampling.build_all()
    reserve = built.reserve_pool
    for world in sampling.WORLDS:
        world_item_ids = [row["item_id"] for row in reserve["items"] if row["world"] == world]
        assert len(world_item_ids) == sampling.RESERVE_PER_WORLD
        assert world_item_ids == sorted(world_item_ids)


@real
def test_reserve_pool_counts_are_exact():
    # Pins the exact total and per-world size (150 = 50/world), the way
    # test_primary_set_counts_and_strata_are_exact pins the primary set's counts. A
    # `>= RESERVE_MINIMUM` floor alone would not catch RESERVE_PER_WORLD drifting from
    # 50, since the round-robin cadence check holds regardless of the per-world size.
    built = sampling.build_all()
    reserve = built.reserve_pool
    assert sampling.RESERVE_PER_WORLD == 50
    assert len(reserve["items"]) == sampling.RESERVE_PER_WORLD * len(sampling.WORLDS) == 150
    assert reserve["counts"]["total"] == 150
    assert reserve["counts"]["per_world"] == {
        world: sampling.RESERVE_PER_WORLD for world in sampling.WORLDS
    }
    from collections import Counter
    actual_per_world = Counter(row["world"] for row in reserve["items"])
    assert actual_per_world == {world: sampling.RESERVE_PER_WORLD for world in sampling.WORLDS}


@real
def test_no_item_appears_in_two_sets():
    built = sampling.build_all()
    primary_ids = {row["item_id"] for row in built.primary_set["items"]}
    regression_ids = {row["item_id"] for row in built.regression_set["items"]}
    reserve_ids = {row["item_id"] for row in built.reserve_pool["items"]}
    assert not (primary_ids & regression_ids)
    assert not (primary_ids & reserve_ids)
    assert not (regression_ids & reserve_ids)


@real
def test_boundary_items_do_not_overlap_world_stratified_or_synthetic_items():
    built = sampling.build_all()
    items_by_role: dict[str, list[dict]] = {"world_stratified": [], "boundary": [], "synthetic": []}
    for row in built.primary_set["items"]:
        if row["synthetic"]:
            items_by_role["synthetic"].append(row)
        elif row["boundary"]:
            items_by_role["boundary"].append(row)
        else:
            items_by_role["world_stratified"].append(row)
    assert len(items_by_role["world_stratified"]) == 120
    assert len(items_by_role["boundary"]) == 40
    assert len(items_by_role["synthetic"]) == 40
    all_ids = [row["item_id"] for role in items_by_role.values() for row in role]
    assert len(set(all_ids)) == len(all_ids)


@real
def test_primary_set_items_do_not_leak_which_boundary_heuristic_selected_them():
    # A labeler-facing record naming e.g. "ambiguous_referents" would tell the human
    # exactly what borderline pattern to look for on that specific item, biasing the
    # supposedly-independent allow/reject/unresolved call -- the same class of leakage
    # the synthetic set avoids by omitting `synthetic_role` entirely. Only the neutral
    # `boundary` bool (analogous to `synthetic`) may appear per item; the heuristic name
    # is reported only in aggregate, via `counts.boundary_per_heuristic` and the
    # top-level `boundary_heuristics` description list, neither of which is keyed by
    # item_id.
    built = sampling.build_all()
    for row in built.primary_set["items"]:
        assert "boundary_heuristic" not in row
        assert isinstance(row["boundary"], bool)
    assert "boundary_per_heuristic" in built.primary_set["counts"]


@real
def test_synthetic_pair_ids_complete_in_primary_set():
    built = sampling.build_all()
    synthetic_rows = [row for row in built.primary_set["items"] if row["synthetic"]]
    pair_ids = [row["pair_id"] for row in synthetic_rows]
    from collections import Counter
    counts = Counter(pair_ids)
    assert len(counts) == 20
    assert all(count == 2 for count in counts.values())


@real
def test_tracked_artifacts_are_current(tmp_path):
    built = sampling.build_all()
    primary_path = REPO_ROOT / "rejudge" / "phase2_checker_primary_set_2026-07-18.json"
    regression_path = REPO_ROOT / "rejudge" / "phase2_checker_regression_set_2026-07-18.json"
    reserve_path = REPO_ROOT / "rejudge" / "phase2_checker_reserve_pool_2026-07-18.json"
    if not (primary_path.exists() and regression_path.exists() and reserve_path.exists()):
        pytest.skip("tracked checker-sampling artifacts not present")
    assert primary_path.read_text(encoding="utf-8") == sampling.render_json(built.primary_set)
    assert regression_path.read_text(encoding="utf-8") == sampling.render_json(built.regression_set)
    assert reserve_path.read_text(encoding="utf-8") == sampling.render_json(built.reserve_pool)


@real
def test_calibration_file_drift_fails_closed(tmp_path):
    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir()
    (calibration_dir / "calibration_judgments_a70.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(sampling.CheckerSamplingError):
        sampling.build_universe(calibration_dir=calibration_dir)


@real
def test_calibration_file_drift_fails_closed_on_extra_unexpected_file(tmp_path):
    # `on_disk != expected` is symmetric and should fail closed in both directions; the
    # test above only exercises "missing files". This exercises the other half of the
    # contract: all 6 expected files present, plus one extra/unexpected file.
    calibration_dir = tmp_path / "calibration"
    calibration_dir.mkdir()
    for name in sampling.CALIBRATION_FILES:
        (calibration_dir / name).write_text("", encoding="utf-8")
    (calibration_dir / "calibration_judgments_unexpected_extra.jsonl").write_text(
        "", encoding="utf-8"
    )
    with pytest.raises(sampling.CheckerSamplingError):
        sampling.build_universe(calibration_dir=calibration_dir)


def test_bucket_reason_priority_order():
    from rejudge.query_screen import (
        ANSWER_OR_DEBATE_REFERENCE,
        CANDIDATE_RESTATEMENT,
        COMPOUND_CLAIM,
        META_OR_EVALUATIVE,
    )
    assert sampling.bucket_reason((CANDIDATE_RESTATEMENT,)) == CANDIDATE_RESTATEMENT
    assert sampling.bucket_reason(
        (ANSWER_OR_DEBATE_REFERENCE, CANDIDATE_RESTATEMENT)
    ) == CANDIDATE_RESTATEMENT
    assert sampling.bucket_reason(
        (ANSWER_OR_DEBATE_REFERENCE, META_OR_EVALUATIVE)
    ) == META_OR_EVALUATIVE
    assert sampling.bucket_reason(
        (ANSWER_OR_DEBATE_REFERENCE, COMPOUND_CLAIM)
    ) == ANSWER_OR_DEBATE_REFERENCE
    assert sampling.bucket_reason((COMPOUND_CLAIM,)) == COMPOUND_CLAIM
    with pytest.raises(sampling.CheckerSamplingError):
        sampling.bucket_reason(())


def test_largest_remainder_split_sums_exactly():
    result = sampling._largest_remainder_split(40, {"a": 1296, "b": 141})
    assert sum(result.values()) == 40
    assert result["a"] >= result["b"]


def test_largest_remainder_split_zero_total():
    result = sampling._largest_remainder_split(0, {"a": 5, "b": 1})
    assert result == {"a": 0, "b": 0}
