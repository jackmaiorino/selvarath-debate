"""Structural K2-polarity verification, from RENDERED judge prompts -- never from metadata.

Context: rejudge/phase3_incident1_mirroring_2026-08-21.json and
rejudge/phase3_codex_mirroring_consult_2026-08-21.md (section 5: "v2 verification must happen
on rendered prompt pairs end to end"). The defect this tool exists to catch: before the
2026-08-21 mirroring fix, ``rejudge.phase2_canary_execute._polarity`` carried no side input at
all, so both K2 slots of every judgment cell rendered the IDENTICAL "POSITION A: .../POSITION
B: ..." block -- duplication, not the mirroring the frozen protocols always claimed.

This module never trusts a result row's stored ``position_a_is_correct`` field or re-derives
polarity from ``analysis.infra.design.position_a_is_correct``. It reads the ACTUAL rendered
judge-facing prompt text out of ``judge_messages`` (the composed presentation the judge really
saw) and compares that, structurally, across the K2 pair. This is the same "trust the rendered
text, not the label" method ``scripts/phase2_mirror_reanalysis.py``'s ``verify_step1`` uses for
phase 2's main archive, adapted to phase 3's canary shape and extended to also report the
per-condition structural gates section 3 of the consult calls for: every K2 pair mirrored or
duplicated, both sides present, and the realized assignment IDENTICAL across every condition/
judge/debater that shares one (question, transcript, side) unit (position depends only on
(question_id, transcript_index, side), never on judge/debater/budget/condition).

Debater identity is not carried in a judgment result row at all (checked against the real
archive), so cell_key -> dimension resolution is done by re-enumerating the frozen canary plan
(``rejudge.phase3_plan.enumerate_canary_cells``) and joining on cell_key -- exactly the
technique ``scripts/phase2_main_analysis.load_plan_meta`` already uses for phase 2's main
archive. The judge roster needed to enumerate is discovered from the results file itself
(every ``judge_model`` observed), not assumed, since cell_key content never depends on which
OTHER judges share a roster (``rejudge.phase2_plan.make_cell_key`` hashes only the cell's own
fields).

Runnable both offline against a synthetic fixed store (expect ~100% mirrored, exact per-judge
splits) and against a real archive (expect the pre-fix duplication).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
# Invoked directly (python scripts/phase3_polarity_verify.py ...), not via `python -m`, so
# sys.path[0] defaults to this file's own directory (scripts/), not the repo root -- the rejudge
# package is not importable without this, matching the same fix every sibling phase-3 script
# (phase3_preseed_transcripts.py, phase3_context_precheck.py) already applies before its own
# `from rejudge import ...` line.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import phase3_plan  # noqa: E402
from rejudge.debate_gen import _load_question_bank  # noqa: E402

# Same extraction pattern as scripts/phase2_mirror_reanalysis.py's _extract_positions, applied
# to phase 3's reused "sequential_judge_presentation" template
# ("QUESTION: {question}\n\nPOSITION A: {position_a}\n\nPOSITION B: {position_b}\n\n"
# "DEBATE TRANSCRIPT:\n{debate_transcript}") -- the only judge presentation shape phase-3's
# debate_grid conditions (b0/sequential_b1/b2/b4/b8) ever compose.
_POSITION_A_RE = re.compile(r"POSITION A:\s(.*?)\n\nPOSITION B:\s", re.DOTALL)
_DEBATE_TRANSCRIPT_MARKER = "\n\nDEBATE TRANSCRIPT:"


class PolarityVerifyError(ValueError):
    """Raised when the tool itself cannot proceed (never for a data-level finding)."""


def extract_rendered_positions(user_content: str) -> tuple[str, str] | None:
    """(position_a_text, position_b_text) literally rendered in the judge's first user
    message, or ``None`` if this content is not the debate_grid presentation shape."""
    match = _POSITION_A_RE.search(user_content)
    if match is None:
        return None
    position_a = match.group(1)
    rest = user_content[match.end():]
    cut = rest.find(_DEBATE_TRANSCRIPT_MARKER)
    position_b = rest if cut == -1 else rest[:cut]
    return position_a, position_b


def _rendered_user_content(result: Mapping[str, Any]) -> str | None:
    """The first user-role message's content -- index 1, system is index 0 -- exactly the
    message judge_loop.run_judgment builds from position_a/position_b before any query loop
    appends anything further. Absent entirely for non-judgment rows (transcript references,
    capability_qa), which carry no ``judge_messages`` key at all."""
    messages = result.get("judge_messages")
    if not messages or len(messages) < 2:
        return None
    user_message = messages[1]
    if user_message.get("role") != "user":
        return None
    return user_message.get("content")


def load_result_rows(results_path: str | Path) -> list[dict[str, Any]]:
    """Read a ``CellResultStore``-shaped JSONL file: one ``{"cell_key", "result", ...}`` row
    per line. Chain integrity is not re-verified here (CellResultStore already owns that); this
    is a read-only reporting tool over an already-trusted store."""
    rows: list[dict[str, Any]] = []
    with Path(results_path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def discover_roster(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every distinct ``judge_model`` observed across the result rows, sorted.

    Cell-key content never depends on which OTHER judges share a roster (only on the cell's own
    fields -- see the module docstring), so this discovered set re-enumerates the exact same
    cell keys the real run produced, whether the archive covers the full candidate roster or a
    subset."""
    judges: set[str] = set()
    for row in rows:
        judge = (row.get("result") or {}).get("judge_model")
        if judge:
            judges.add(str(judge))
    return sorted(judges)


def build_plan_index(protocol: Mapping[str, Any], judges: Iterable[str],
                     held_out_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """cell_key -> plan cell dict, for judgment cells only (kind == CANARY_JUDGMENT_KIND).

    Transcript-reference and capability_qa cells are silently excluded here -- capability
    anchors have their own, already-verified-mirrored path (mirror_index driven directly, see
    rejudge.phase3_runner._capability_qa_side) and carry no judge_messages/POSITION A block to
    extract in the first place; transcripts carry no polarity at all.
    """
    cells = phase3_plan.enumerate_canary_cells(protocol, list(judges), list(held_out_ids))
    return {str(cell["cell_key"]): cell for cell in cells
           if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND}


def condition_replicates(protocol: Mapping[str, Any]) -> dict[str, int]:
    return {str(condition["id"]): int(condition["judgment_replicates_per_transcript_side"])
           for condition in protocol["debate_grid"]["conditions"]}


def k2_sides(protocol: Mapping[str, Any]) -> int:
    return int(protocol["debate_grid"]["k"])


def verify(rows: Iterable[Mapping[str, Any]], *, protocol: Mapping[str, Any],
          judges: Iterable[str], held_out_ids: Iterable[str],
          question_bank: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The structural verification. Never trusts stored metadata for the pairing itself: only
    the plan (to know WHICH cell_keys are a K2 pair) and the rendered prompt text (to know what
    was actually shown)."""
    plan_index = build_plan_index(protocol, judges, held_out_ids)
    replicates_by_condition = condition_replicates(protocol)
    k2 = k2_sides(protocol)
    if k2 != 2:
        raise PolarityVerifyError(
            f"debate_grid.k = {k2}; this tool's mirrored/duplicated classification assumes "
            "exactly 2 sides, matching the frozen phase-3 design")

    # unit = (question_id, judge_model, debater_model, transcript_index, condition,
    #         within_side_replicate) -> {side: {"cell_key", "position_a_text", "position_b_text"}}
    groups: dict[tuple, dict[int, dict[str, Any]]] = defaultdict(dict)
    rows_total = 0
    rows_unparseable: list[str] = []

    for row in rows:
        cell_key = str(row.get("cell_key"))
        plan_cell = plan_index.get(cell_key)
        if plan_cell is None:
            continue  # not a canary judgment cell (transcript / capability_qa / unknown key)
        result = row.get("result") or {}
        content = _rendered_user_content(result)
        extracted = extract_rendered_positions(content) if content is not None else None
        if extracted is None:
            rows_unparseable.append(cell_key)
            continue
        rows_total += 1
        position_a_text, position_b_text = extracted
        condition = str(plan_cell["condition"])
        replicates = replicates_by_condition.get(condition)
        if not replicates:
            rows_unparseable.append(cell_key)
            continue
        replicate_index = int(plan_cell["replicate_index"])
        side, within = replicate_index // replicates, replicate_index % replicates
        unit = (str(plan_cell["question_id"]), str(plan_cell["judge_model"]),
               str(plan_cell["debater_model"]), plan_cell.get("transcript_index"),
               condition, within)
        groups[unit][side] = {
            "cell_key": cell_key,
            "position_a_text": position_a_text,
            "position_b_text": position_b_text,
        }

    pairs_mirrored = pairs_duplicated = pairs_other = 0
    incomplete_groups: list[str] = []
    # (question_id, transcript_index, side) -> {True, False} of realized A-correct, across
    # every condition/judge/debater that shares this unit; position depends only on
    # (question_id, transcript_index, side), so this set must always have size <= 1.
    realized_by_qts: dict[tuple, set] = defaultdict(set)
    per_judge: dict[str, dict[str, int]] = defaultdict(
        lambda: {"realized_A_correct": 0, "realized_B_correct": 0, "unresolved": 0})

    for unit, sides in groups.items():
        question_id, judge_model, _debater_model, transcript_index, _condition, _within = unit
        if set(sides) != {0, 1}:
            incomplete_groups.append(str({**dict(zip(
                ("question_id", "judge_model", "debater_model", "transcript_index",
                 "condition", "within_side_replicate"), unit)), "sides_present": sorted(sides)}))
            continue
        side0, side1 = sides[0], sides[1]
        a0, b0 = side0["position_a_text"], side0["position_b_text"]
        a1, b1 = side1["position_a_text"], side1["position_b_text"]
        if a0 != b0 and a0 == b1 and b0 == a1:
            pairs_mirrored += 1
        elif a0 == a1 and b0 == b1:
            pairs_duplicated += 1
        else:
            pairs_other += 1

        bank_entry = question_bank.get(question_id)
        for side_index, payload in sides.items():
            realized_a_correct = None
            if bank_entry is not None:
                a_stripped = payload["position_a_text"].strip()
                if a_stripped == bank_entry["correct_answer"].strip():
                    realized_a_correct = True
                elif a_stripped == bank_entry["wrong_answer"].strip():
                    realized_a_correct = False
            if realized_a_correct is not None:
                realized_by_qts[(question_id, transcript_index, side_index)].add(
                    realized_a_correct)
            counts = per_judge[judge_model]
            if realized_a_correct is True:
                counts["realized_A_correct"] += 1
            elif realized_a_correct is False:
                counts["realized_B_correct"] += 1
            else:
                counts["unresolved"] += 1

    inconsistent_units = {
        f"{q}|transcript{t}|side{s}": sorted(v)
        for (q, t, s), v in realized_by_qts.items() if len(v) > 1
    }

    total_pairs = pairs_mirrored + pairs_duplicated + pairs_other

    def pct(n: int) -> float | None:
        return round(100.0 * n / total_pairs, 4) if total_pairs else None

    return {
        "rows_seen_as_canary_judgment_cells": rows_total,
        "rows_unparseable": rows_unparseable,
        "n_rows_unparseable": len(rows_unparseable),
        "incomplete_or_missing_side_groups": incomplete_groups,
        "n_incomplete_or_missing_side_groups": len(incomplete_groups),
        "pairs_total": total_pairs,
        "pairs_mirrored": pairs_mirrored,
        "pairs_mirrored_pct": pct(pairs_mirrored),
        "pairs_duplicated": pairs_duplicated,
        "pairs_duplicated_pct": pct(pairs_duplicated),
        "pairs_other_inconsistent_shape": pairs_other,
        "pairs_other_inconsistent_shape_pct": pct(pairs_other),
        "pairs_inconsistent_across_conditions": inconsistent_units,
        "n_pairs_inconsistent_across_conditions": len(inconsistent_units),
        "per_judge_realized_split": dict(sorted(per_judge.items())),
    }


# --- CLI -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="phase3_polarity_verify",
        description="Structural K2-polarity verification from rendered judge prompts.")
    parser.add_argument("--results", required=True,
                        help="canary results store JSONL (CellResultStore rows)")
    parser.add_argument("--protocol", default=str(REPO_ROOT / "rejudge" / "phase3_protocol.json"))
    parser.add_argument("--project-root", default=str(REPO_ROOT),
                        help="used to resolve the frozen question set (phase2_protocol.json)")
    parser.add_argument("--roster", default=None,
                        help="comma-separated judge model ids; default: every judge_model "
                             "observed in --results")
    parser.add_argument("--out", default=None,
                        help="optional path to also write the JSON report")
    args = parser.parse_args(argv)

    root = Path(args.project_root)
    protocol = phase3_plan.load_protocol(args.protocol)
    rows = load_result_rows(args.results)
    judges = ([j.strip() for j in args.roster.split(",") if j.strip()]
             if args.roster else discover_roster(rows))
    if not judges:
        print("no judge_model observed in --results and none supplied via --roster",
             file=sys.stderr)
        return 2
    _main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, root)
    question_bank = _load_question_bank()

    report = verify(rows, protocol=protocol, judges=judges, held_out_ids=held_out_ids,
                    question_bank=question_bank)
    payload = json.dumps(report, indent=1, sort_keys=True)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
