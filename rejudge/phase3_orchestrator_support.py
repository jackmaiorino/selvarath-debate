"""Pure, unit-testable logic backing scripts/phase3_canary_orchestrator.sh's v2 gates.

The orchestrator is a bash script (POSIX sh/bash, WSL-targeted) that was, before this module
existed, computing its convergence arithmetic and its post-convergence polarity-gate pass/fail
decision inline, inside ``$VENV -c '...'`` one-liners embedded in bash single-quoted strings.
That is a bad place to put anything non-trivial: it is unreviewable by normal Python tooling,
untestable without shelling out, and easy to get subtly wrong (an earlier draft of the
polarity-gate one-liner used an f-string with an escaped quote inside its expression part --
a SyntaxError on Python < 3.12 -- caught only by actually running it).

This module holds that logic as ordinary, directly testable Python functions. The orchestrator
script's ``$VENV -c`` blocks now do the minimum possible: read JSON off disk/argv, call one of
these functions, print the result. Nothing here has any I/O side effect beyond reading files the
caller names and (for :func:`write_condition_filtered_results`) writing the one output file the
caller names -- no network, no provider client, no writes to any result store.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from rejudge import phase3_plan

CRANK_SETTINGS_KEYS = ("review_daemon_concurrency", "max_waves_per_round")


def resolve_crank_settings(manifest: Mapping[str, Any]) -> dict[str, int]:
    """The frozen review-daemon crank settings bound in a v2 manifest's frozen_inputs.

    Raises ``KeyError`` (propagated, never swallowed) if the manifest carries no such binding --
    e.g. a v1-protocol manifest, which has no crank_settings at all (see
    :mod:`rejudge.phase3_manifest`'s v2-only binding). The orchestrator has no valid fallback
    for that case: crank settings are only meaningful once they are frozen and bound.
    """
    frozen_inputs = manifest["frozen_inputs"]
    crank = frozen_inputs["crank_settings"]
    return {key: int(crank[key]) for key in CRANK_SETTINGS_KEYS}


def remaining_canary_cells(manifest: Mapping[str, Any], results_path: str | Path, *,
                           project_root: str | Path = ".") -> dict[str, int]:
    """Manifested-minus-completed canary convergence, v2-aware.

    "Manifested" is every row this v2 canary stage's result store (the SAME single file
    ``scripts/phase3_preseed_transcripts.py`` writes into, per :mod:`rejudge.phase3_manifest`'s
    "one manifest, not two" design) is expected to hold once the stage genuinely converges:

    * the 1,152 fresh judgment cells (:func:`rejudge.phase3_plan.enumerate_canary_cells`,
      ``kind == CANARY_JUDGMENT_KIND``, against the manifest's own roster) -- the cells this
      orchestrator's run passes actually execute;
    * the 540 preseeded transcript-reference rows: 48 canary-scope transcripts
      (``enumerate_canary_cells``, ``kind == CANARY_TRANSCRIPT_KIND``) PLUS 492 main-scope
      transcripts (:func:`rejudge.phase3_plan.enumerate_cells`, ``kind == MAIN_TRANSCRIPT_KIND``)
      -- the main-scope 492 are "inert" for THIS stage (they satisfy a later main-run dependency,
      never a canary one), but they are preseeded into this same store, so a converged store
      genuinely contains all 540, not just the 48 the canary plan itself depends on.

    EXCLUDED from "manifested": the 288 capability-anchor cells (``kind ==
    CAPABILITY_ANCHOR_KIND``) -- unlike v1's original arithmetic, v2 never preseeds or executes
    them against this store at all (``decisions.launch_gates.canary_scope``: they CARRY from the
    v1 identity by exact cell-key set and store/hash binding), so counting them here would make
    convergence permanently unreachable; their integrity is verified separately, by manifest
    (re-)validation (the anchor-carry binding), never by a row appearing in the results store.
    Also excluded: the 29,520 main-scope JUDGMENT cells -- main-run execution is a later stage
    with its own manifest binding, entirely out of THIS canary orchestrator's scope.

    Returns ``{"manifested": N, "completed": N, "remaining": N}`` (1,692 manifested at the v2
    six-judge roster: 1,152 + 48 + 492). ``results_path`` need not exist yet (an absent file
    counts as zero completed cells, matching a freshly preseeded but not-yet-run store).
    """
    root = Path(project_root)
    protocol = phase3_plan.load_protocol(manifest["protocol_tracked_path"])
    roster_judges = list(manifest["roster"]["judges"])
    main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, root)
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, roster_judges, held_out_ids)
    main_cells = phase3_plan.enumerate_cells(protocol, roster_judges, main_ids)
    manifested = {
        str(cell["cell_key"]) for cell in canary_cells
        if cell["kind"] != phase3_plan.CAPABILITY_ANCHOR_KIND
    } | {
        str(cell["cell_key"]) for cell in main_cells
        if cell["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND
    }

    completed: set[str] = set()
    path = Path(results_path)
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                key = json.loads(line).get("cell_key")
                if key:
                    completed.add(str(key))

    settled = completed & manifested
    return {
        "manifested": len(manifested), "completed": len(settled),
        "remaining": len(manifested - completed),
    }


def condition_cell_keys(manifest: Mapping[str, Any], *, condition: str,
                        project_root: str | Path = ".") -> set[str]:
    """The plan's judgment cell keys for one debate_grid condition (e.g. ``"b0"``).

    Used to filter a results store down to the core-b0 subset before re-running the polarity
    verifier against just that subset (the frozen protocol's 48/48 structural-mirroring gate is
    defined on the core b0 population specifically, not the whole store).
    """
    root = Path(project_root)
    protocol = phase3_plan.load_protocol(manifest["protocol_tracked_path"])
    roster_judges = list(manifest["roster"]["judges"])
    _main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, root)
    plan_cells = phase3_plan.enumerate_canary_cells(protocol, roster_judges, held_out_ids)
    return {
        str(cell["cell_key"]) for cell in plan_cells
        if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND and str(cell["condition"]) == condition
    }


def write_condition_filtered_results(results_path: str | Path, out_path: str | Path,
                                     cell_keys: Sequence[str] | set[str]) -> int:
    """Copy every row of ``results_path`` whose ``cell_key`` is in ``cell_keys`` to ``out_path``.

    Returns the number of rows written. An absent ``results_path`` writes an empty file (0 rows)
    rather than raising -- matches :func:`remaining_canary_cells`'s "not-yet-run store" handling.
    """
    keys = set(cell_keys)
    src_path = Path(results_path)
    written = 0
    with Path(out_path).open("w", encoding="utf-8") as dst:
        if src_path.exists():
            with src_path.open(encoding="utf-8") as src:
                for line in src:
                    line = line.strip()
                    if not line:
                        continue
                    if json.loads(line).get("cell_key") in keys:
                        dst.write(line + "\n")
                        written += 1
    return written


def evaluate_polarity_gate(full_report: Mapping[str, Any],
                           b0_report: Mapping[str, Any]) -> list[str]:
    """The post-convergence polarity-gate pass/fail decision. Returns a list of problems.

    Empty list == PASS. Non-empty == FAIL (the orchestrator STOPs rather than reporting
    success). Two independent things must both hold:

    * over the FULL store, every K2 pair is mirrored (never duplicated or otherwise
      inconsistent), every side-group is complete, and no unit's realized position disagrees
      across the conditions/judges/debaters that share it (``full_report``, from
      ``scripts/phase3_polarity_verify.py`` run against the whole results store);
    * over the CORE b0 subset alone, every rostered judge's realized split is EXACTLY 48
      A-correct / 48 B-correct / 0 unresolved (``b0_report``, from the same verifier run against
      a results file pre-filtered to just the b0 condition's judgment cells) --
      ``decisions.launch_gates.calibration_gates_per_judge.structural_mirroring_gates_zero_tolerance``:
      "per-judge realized split exactly 48/48 on the core b0 set".
    """
    problems: list[str] = []

    pairs_total = full_report.get("pairs_total", 0)
    mirrored_pct = full_report.get("pairs_mirrored_pct")
    duplicated = full_report.get("pairs_duplicated", 0)
    other_shape = full_report.get("pairs_other_inconsistent_shape", 0)
    incomplete_groups = full_report.get("n_incomplete_or_missing_side_groups", 0)
    cross_condition = full_report.get("n_pairs_inconsistent_across_conditions", 0)

    if pairs_total <= 0:
        problems.append("full store: no judged pairs found")
    if mirrored_pct != 100.0:
        problems.append(f"full store: pairs_mirrored_pct={mirrored_pct!r}, expected 100.0")
    if duplicated != 0:
        problems.append(f"full store: {duplicated} duplicated pair(s)")
    if other_shape != 0:
        problems.append(f"full store: {other_shape} inconsistent-shape pair(s)")
    if incomplete_groups != 0:
        problems.append(f"full store: {incomplete_groups} incomplete/missing side group(s)")
    if cross_condition != 0:
        problems.append(f"full store: {cross_condition} cross-condition inconsistency(ies)")

    b0_split = b0_report.get("per_judge_realized_split") or {}
    if not b0_split:
        problems.append("core b0: no per-judge split computed at all")
    for judge in sorted(b0_split):
        counts = b0_split[judge]
        a_correct = counts.get("realized_A_correct")
        b_correct = counts.get("realized_B_correct")
        unresolved = counts.get("unresolved")
        if a_correct != 48 or b_correct != 48 or unresolved:
            problems.append(f"core b0: {judge} split is {counts!r}, expected exactly 48/48/0")

    return problems
