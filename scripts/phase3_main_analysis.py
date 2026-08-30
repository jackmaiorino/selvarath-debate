"""Frozen, offline analysis engine for the Phase-3 v3 main run.

The engine implements the estimands in ``rejudge/phase3_protocol_v3_r6.json``.
It performs no provider calls and does not know how to authorize a run.  Its
public pure-function surface is intentionally usable by synthetic tests before
any main outcome exists.

Primary family: D1, D2, D4, and D8, each ``error(b) - error(b0)``.
Confirmatory secondary: S1, ``error(b8) - error(b2)``.

Question-cluster bootstrap draws are generated once and shared by every
estimand, sensitivity, endpoint-specific descriptive, and the primary sup-t
band.  Strict INVALID counts wrong.  The valid-only sensitivity uses matched
slot support separately for each contrast.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import statistics
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import (  # noqa: E402
    phase2_plan,
    phase3_main_authorization,
    phase3_main_context,
    phase3_main_finalization,
    phase3_main_manifest,
    phase3_main_runner,
    phase3_plan,
)
from rejudge.phase2_execution import canonical_sha256  # noqa: E402
from scripts import phase3_polarity_verify  # noqa: E402
from scripts.phase2_main_analysis import (  # noqa: E402
    bootstrap_p_two_sided,
    holm,
    percentile_ci,
)


PINS_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_main_analysis_pins_2026-08-29.json"
PROTOCOL_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_protocol_v3_r6.json"
OUTPUT_PATH_DEFAULT = REPO_ROOT / "analysis_out" / "phase3_main_results.json"

PRIMARY_SPECS: dict[str, tuple[str, str]] = {
    "D1": ("sequential_b1", "b0"),
    "D2": ("sequential_b2", "b0"),
    "D4": ("sequential_b4", "b0"),
    "D8": ("sequential_b8", "b0"),
}
PRIMARY_IDS = tuple(PRIMARY_SPECS)
S1_SPEC = ("sequential_b8", "sequential_b2")
ALL_CONDITIONS = ("b0", "sequential_b1", "sequential_b2", "sequential_b4",
                  "sequential_b8")
CONDITION_BUDGET = {
    "b0": 0,
    "sequential_b1": 1,
    "sequential_b2": 2,
    "sequential_b4": 4,
    "sequential_b8": 8,
}
B_DEFAULT = 10_000
SEED_DEFAULT = 20_260_829
ZERO_SE_TOLERANCE = 1e-15
FROZEN_PINS_CANONICAL_SHA256 = (
    "6e8d607d0a4a5d9ff24f4a729d3dbfa9ab27d5d862bcc0fcebf1da03fc0a0a77")
FROZEN_PINS_SECTION_SHA256 = {
    "bindings": "deebd7910cbb7a7fcb50af19cdd3db3f442f1df85fb4a4626ca93cab927f37e0",
    "scope": "a5b4dab1594314cd7000d7d05cb97e73a9b3013b264a48ff16d7f170f37747b0",
    "estimands": "ae38c7bbd0023a4a50ace03ec52c2cb60e8b098a074ba595655562fca3a5e85b",
    "aggregation": "25a2d6c4100df426381c88eed8c8bde66350c15eefa30bca146feda75f36bce6",
    "invalid_policy": "ff8eee572159bd18b1b07cf02969ba5d86ba87a6690fcd77296b059e6a915451",
    "capability_anchor_scores": (
        "9e421b076369adedb4497818c7df48e98b719ff42ddf971086a808d31203f4bc"),
    "bootstrap": "fdad2c6deba1da72c569abf892e593852a36737b63ea6dfb186ca94ef6e2e504",
    "sup_t": "994c8b57e4ad01babc0fa0922cd42f4f05f4dd949758f7bee14215f7d7176a43",
    "rounding": "797b369930b1127b4ea213964c4d24b453e64829bb0c02a7d901f90fa9065382",
    "reporting": "a2de82f5f8ee5442246f67a1cf8cb45037b6119da05ca2f83f5bd66cf38be70f",
    "integrity": "42dbe5dfdcf558f8e27ca6e2ae8c2c4913d48c89caa525ef48a22c2ab242d814",
    "authority": "625cf90c47d32f20642db3ab844c2996a2fcebbf69e2d32425f84e75f25e2476",
}
MAIN_FINALIZATION_SCHEMA = phase3_main_finalization.FINALIZATION_SCHEMA
MAIN_FINALIZATION_STATUS = phase3_main_finalization.FINALIZATION_STATUS


class AnalysisError(ValueError):
    """Raised when an input or analysis invariant fails closed."""


@dataclass(frozen=True, slots=True)
class AnalysisRunReceipt:
    """Exact bytes produced by one successful in-process analysis execution."""

    returncode: int
    output_raw: bytes


@dataclass(frozen=True)
class AnalysisRecord:
    cell_key: str
    question_id: str
    world: str
    condition: str
    judge: str
    debater: str
    transcript_index: int
    side: int
    within_side_replicate: int
    correct: bool | None
    correct_position: str | None
    context_eligible: bool = True
    origin: str = "observed"  # observed | terminal_invalid | context_ineligible

    @property
    def slot_key(self) -> tuple[str, str, str, int, int, int]:
        """Condition-free matched identity used by every arm contrast."""
        return (
            self.question_id,
            self.judge,
            self.debater,
            self.transcript_index,
            self.side,
            self.within_side_replicate,
        )


def plan_replicate_to_side(replicate_index: int, replicates_per_side: int) -> tuple[int, int]:
    """Translate the plan's folded zero-based index into (side, within-side replicate).

    Configuration A has one replicate per side, so plan replicate indices 0 and 1
    mean ``(side 0, within 0)`` and ``(side 1, within 0)``.  The protocol's stale
    phrase "replicate index 1 per (transcript, side)" is therefore operationalized
    as zero-based ``within_side_replicate == 0`` while preserving both sides.
    """
    if replicates_per_side <= 0:
        raise AnalysisError("replicates_per_side must be positive")
    if replicate_index < 0:
        raise AnalysisError("replicate_index must be non-negative")
    side, within = divmod(replicate_index, replicates_per_side)
    if side not in (0, 1):
        raise AnalysisError(f"replicate_index {replicate_index} does not encode a K2 side")
    return side, within


def _read_stable_bytes(path: Path, label: str) -> bytes:
    """Read one immutable analysis input twice and require identical bytes."""
    try:
        first = path.read_bytes()
        second = path.read_bytes()
    except OSError as exc:
        raise AnalysisError(f"could not read {label}: {path}") from exc
    if first != second:
        raise AnalysisError(f"{label} changed while analysis read it")
    return first


def _read_stable_json_object(
    path: Path, label: str,
) -> tuple[Mapping[str, Any], bytes]:
    raw = _read_stable_bytes(path, label)
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except AnalysisError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"{label} is not valid unique-key UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise AnalysisError(f"{label} must be a JSON object")
    return value, raw


def _require_unchanged(path: Path, expected: bytes, label: str) -> None:
    try:
        observed = path.read_bytes()
    except OSError as exc:
        raise AnalysisError(f"{label} became unreadable during analysis") from exc
    if observed != expected:
        raise AnalysisError(f"{label} changed during analysis")


def _write_analysis_output(path: Path, result: Mapping[str, Any]) -> bytes:
    output_raw = (
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(output_raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise AnalysisError(
            f"analysis output already exists and is immutable: {path}") from exc
    try:
        if path.read_bytes() != output_raw:
            raise AnalysisError("analysis output changed during its durable write")
    except OSError as exc:
        raise AnalysisError("analysis output became unreadable after its durable write") from exc
    return output_raw


def _snapshot_sha256_bound_input(
    path: Path, expected_sha256: str, label: str,
) -> tuple[Path, bytes, str]:
    """Snapshot the exact bytes already admitted by an earlier validation step."""
    raw = _read_stable_bytes(path, label)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise AnalysisError(f"{label} changed after validation")
    return path, raw, label


def _read_jsonl_bytes(raw: bytes, path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise AnalysisError(f"result store is not UTF-8: {path}") from exc
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise AnalysisError(f"blank JSONL row at {path}:{line_number}")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"invalid JSON at {path}:{line_number}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("cell_key"), str):
            raise AnalysisError(f"invalid result row at {path}:{line_number}")
        key = str(row["cell_key"])
        if key in seen:
            raise AnalysisError(f"duplicate result cell_key {key}")
        seen.add(key)
        rows.append(row)
    return rows


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return _read_jsonl_bytes(_read_stable_bytes(path, "result store"), path)


@dataclass(frozen=True, slots=True)
class _QuestionBankSnapshot:
    bank: dict[str, dict[str, Any]]
    main_ids: tuple[str, ...]
    held_out_ids: tuple[str, ...]
    stable_inputs: tuple[tuple[Path, bytes, str], ...]


def _parse_unique_json_bytes(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except AnalysisError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"{label} is not valid unique-key UTF-8 JSON") from exc


def _snapshot_protocol_bound_question_bank(
    protocol: Mapping[str, Any], root: Path,
) -> _QuestionBankSnapshot:
    """Load each bound question source once and retain bytes for the final stability check."""
    sources = protocol.get("sources")
    source_bindings = protocol.get("source_bindings")
    if not isinstance(sources, Mapping) or not isinstance(source_bindings, Mapping):
        raise AnalysisError("protocol question sources or source bindings are missing")
    phase3_bound_json = source_bindings.get("canonical_json_sha256")
    if not isinstance(phase3_bound_json, Mapping):
        raise AnalysisError("protocol canonical source bindings are missing")
    phase2_relative = sources.get("phase2_protocol")
    if not isinstance(phase2_relative, str) or not phase2_relative:
        raise AnalysisError("protocol phase-2 question source is missing")
    phase2_path = (root / phase2_relative).resolve()
    phase2_raw = _read_stable_bytes(phase2_path, "bound phase-2 protocol")
    phase2_protocol = _parse_unique_json_bytes(
        phase2_raw, "bound phase-2 protocol")
    if not isinstance(phase2_protocol, Mapping):
        raise AnalysisError("bound phase-2 protocol must be a JSON object")
    expected_phase2_sha = phase3_bound_json.get(phase2_relative)
    if canonical_sha256(phase2_protocol) != expected_phase2_sha:
        raise AnalysisError("bound phase-2 protocol differs from the phase-3 binding")
    try:
        phase2_plan.validate_protocol(phase2_protocol)
    except (phase2_plan.ProtocolValidationError, KeyError, TypeError, ValueError) as exc:
        raise AnalysisError(f"bound phase-2 protocol is invalid: {exc}") from exc

    question_set = phase2_protocol.get("question_set")
    phase2_bindings = phase2_protocol.get("source_bindings")
    if not isinstance(question_set, Mapping) or not isinstance(phase2_bindings, Mapping):
        raise AnalysisError("bound phase-2 question set or source bindings are missing")
    question_sources = question_set.get("question_sources")
    bound_json = phase2_bindings.get("canonical_json_sha256")
    if (
        not isinstance(question_sources, list)
        or not all(isinstance(path, str) and path for path in question_sources)
        or len(set(question_sources)) != len(question_sources)
        or not isinstance(bound_json, Mapping)
    ):
        raise AnalysisError("bound phase-2 question-source contract is malformed")

    bank: dict[str, dict[str, Any]] = {}
    payloads: dict[str, Any] = {}
    stable_inputs: list[tuple[Path, bytes, str]] = [
        (phase2_path, phase2_raw, "bound phase-2 protocol")]
    for relative in question_sources:
        path = (root / relative).resolve()
        label = f"bound question source {relative}"
        raw = _read_stable_bytes(path, label)
        payload = _parse_unique_json_bytes(raw, label)
        if not isinstance(payload, list):
            raise AnalysisError(f"{label} must be a JSON array")
        expected_sha = bound_json.get(relative)
        if canonical_sha256(payload) != expected_sha:
            raise AnalysisError(f"{label} differs from the phase-2 binding")
        payloads[relative] = payload
        stable_inputs.append((path, raw, label))
        for row in payload:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not row["id"]
            ):
                raise AnalysisError(f"{label} contains a malformed row")
            question_id = str(row["id"])
            if question_id in bank:
                raise AnalysisError(f"duplicate bound question ID {question_id}")
            bank[question_id] = row

    bundle_sha = canonical_sha256(payloads)
    expected_bundle_hashes = (
        phase2_bindings.get("question_bank_bundle_sha256"),
        source_bindings.get("question_bank_bundle_sha256"),
        (
            protocol.get("planning_cell_identity", {}).get(
                "question_bank_bundle_sha256")
            if isinstance(protocol.get("planning_cell_identity"), Mapping)
            else None
        ),
    )
    if any(expected != bundle_sha for expected in expected_bundle_hashes):
        raise AnalysisError("bound question-bank bundle hash drifted")

    expected_total = question_set.get("expected_total_question_count")
    excluded = question_set.get("calibration_excluded_question_ids")
    if (
        type(expected_total) is not int
        or len(bank) != expected_total
        or not isinstance(excluded, list)
        or not all(isinstance(value, str) and value for value in excluded)
        or len(set(excluded)) != len(excluded)
    ):
        raise AnalysisError("bound question-bank partition is malformed")
    held_out_ids = tuple(sorted(excluded))
    missing_exclusions = set(held_out_ids) - set(bank)
    if missing_exclusions:
        raise AnalysisError(
            f"bound question bank lacks held-out IDs: {sorted(missing_exclusions)}")
    main_ids = tuple(sorted(set(bank) - set(held_out_ids)))
    phase3_question_set = protocol.get("question_set")
    if not isinstance(phase3_question_set, Mapping):
        raise AnalysisError("phase-3 question-set contract is missing")
    if (
        len(main_ids) != question_set.get("expected_main_question_count")
        or len(main_ids) != phase3_question_set.get("expected_main_question_count")
        or len(held_out_ids) != phase3_question_set.get("held_out_question_count")
    ):
        raise AnalysisError("bound question-bank partition counts drifted")
    return _QuestionBankSnapshot(
        bank=bank,
        main_ids=main_ids,
        held_out_ids=held_out_ids,
        stable_inputs=tuple(stable_inputs),
    )


def _load_protocol_bound_question_bank(
    protocol: Mapping[str, Any], root: Path,
) -> dict[str, dict[str, Any]]:
    """Compatibility wrapper returning the exact hash-verified snapshot payload."""
    return _snapshot_protocol_bound_question_bank(protocol, root).bank


def _rendered_correct_position(
    row: Mapping[str, Any], question: Mapping[str, Any],
) -> str:
    result = row.get("result")
    if not isinstance(result, Mapping):
        raise AnalysisError(f"result row {row.get('cell_key')} has no result object")
    content = phase3_polarity_verify._rendered_user_content(result)
    positions = phase3_polarity_verify.extract_rendered_positions(content) if content else None
    if positions is None:
        raise AnalysisError(f"rendered positions unparseable for {row.get('cell_key')}")
    a_text, b_text = (value.strip() for value in positions)
    correct = str(question["correct_answer"]).strip()
    wrong = str(question["wrong_answer"]).strip()
    if (a_text, b_text) == (correct, wrong):
        return "A"
    if (a_text, b_text) == (wrong, correct):
        return "B"
    raise AnalysisError(f"rendered answers do not match the bank for {row.get('cell_key')}")


def _check_result_metadata(result: Mapping[str, Any], cell: Mapping[str, Any]) -> None:
    expected = {
        "question_id": cell["question_id"],
        "condition": cell["condition"],
        "judge_model": cell["judge_model"],
        "transcript_index": cell["transcript_index"],
        "replicate": cell["replicate_index"],
    }
    for field, value in expected.items():
        if field in result and result[field] != value:
            raise AnalysisError(
                f"result metadata mismatch for {cell['cell_key']}: {field}="
                f"{result[field]!r}, expected {value!r}")


def build_analysis_records(
    *,
    rows: Sequence[Mapping[str, Any]],
    plan_cells: Sequence[Mapping[str, Any]],
    protocol: Mapping[str, Any],
    question_bank: Mapping[str, Mapping[str, Any]],
    terminal_cell_keys: Iterable[str] = (),
    context_ineligible_cell_keys: Iterable[str] = (),
) -> list[AnalysisRecord]:
    """Validate the planned partition and derive outcomes from rendered prompts.

    Stored correctness and stored A/B-correct metadata are cross-checks only.  The
    analysis derives the correct position from the actual rendered prompt and the
    frozen question bank.
    """
    judgment_plan = {
        str(cell["cell_key"]): cell for cell in plan_cells
        if cell["kind"] == phase3_plan.MAIN_JUDGMENT_KIND
    }
    transcript_plan = {
        str(cell["cell_key"]): cell for cell in plan_cells
        if cell["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND
    }
    all_plan_keys = {str(cell["cell_key"]) for cell in plan_cells}
    if len(all_plan_keys) != len(plan_cells):
        raise AnalysisError("main plan contains duplicate cell keys")
    if all_plan_keys != judgment_plan.keys() | transcript_plan.keys():
        raise AnalysisError("main plan contains an unexpected cell kind")
    protocol_judgment_count = int(
        protocol["debate_grid"]["slot_arithmetic"]["total_judgment_slots"])
    if protocol_judgment_count != 9_840 or len(judgment_plan) != protocol_judgment_count:
        raise AnalysisError(
            f"main judgment plan must contain exactly 9,840 cells, got {len(judgment_plan)}")
    protocol_question_count = int(protocol["question_set"]["expected_main_question_count"])
    expected_transcripts = (
        protocol_question_count
        * len(protocol["roster"]["debaters"])
        * phase3_plan.MAIN_TRANSCRIPTS_PER_QUESTION_PER_DEBATER
    )
    if expected_transcripts != 492 or len(transcript_plan) != expected_transcripts:
        raise AnalysisError(
            f"main transcript plan must contain exactly 492 cells, got {len(transcript_plan)}")
    terminal = frozenset(map(str, terminal_cell_keys))
    ineligible = frozenset(map(str, context_ineligible_cell_keys))
    if terminal & ineligible:
        raise AnalysisError("terminal and context-ineligible cell sets overlap")
    if not terminal <= judgment_plan.keys() or not ineligible <= judgment_plan.keys():
        raise AnalysisError("terminal or context-ineligible key is outside the main plan")

    row_by_key: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = str(row.get("cell_key"))
        if key in row_by_key:
            raise AnalysisError(f"duplicate result cell_key {key}")
        if key not in all_plan_keys:
            raise AnalysisError(f"result row {key} is outside the main plan")
        row_by_key[key] = row
    observed_judgments = set(row_by_key) & judgment_plan.keys()
    observed_transcripts = set(row_by_key) & transcript_plan.keys()
    if observed_transcripts != transcript_plan.keys():
        missing_transcripts = sorted(transcript_plan.keys() - observed_transcripts)
        raise AnalysisError(
            f"main transcript partition is incomplete: {missing_transcripts[:3]}")
    if observed_judgments & (terminal | ineligible):
        raise AnalysisError("a terminal or context-ineligible cell has a result row")
    covered = observed_judgments | terminal | ineligible
    if covered != judgment_plan.keys():
        missing = sorted(judgment_plan.keys() - covered)
        raise AnalysisError(f"main judgment partition is incomplete: {missing[:3]}")

    replicates = {
        str(condition["id"]): int(condition["judgment_replicates_per_transcript_side"])
        for condition in protocol["debate_grid"]["conditions"]
    }
    records: list[AnalysisRecord] = []
    for key, cell in sorted(judgment_plan.items()):
        condition = str(cell["condition"])
        side, within = plan_replicate_to_side(
            int(cell["replicate_index"]), replicates[condition])
        qid = str(cell["question_id"])
        question = question_bank.get(qid)
        if question is None:
            raise AnalysisError(f"question {qid} is absent from the frozen bank")
        common: dict[str, Any] = dict(
            cell_key=key,
            question_id=qid,
            world=str(question["world"]),
            condition=condition,
            judge=str(cell["judge_model"]),
            debater=str(cell["debater_model"]),
            transcript_index=int(cell["transcript_index"]),
            side=side,
            within_side_replicate=within,
        )
        if key in terminal:
            records.append(AnalysisRecord(
                **common, correct=None, correct_position=None,
                context_eligible=True, origin="terminal_invalid"))
            continue
        if key in ineligible:
            records.append(AnalysisRecord(
                **common, correct=None, correct_position=None,
                context_eligible=False, origin="context_ineligible"))
            continue

        row = row_by_key[key]
        result = row.get("result")
        if not isinstance(result, Mapping):
            raise AnalysisError(f"result row {key} has no result object")
        _check_result_metadata(result, cell)
        correct_position = _rendered_correct_position(row, question)
        verdict = result.get("verdict_strict")
        selected = verdict.get("verdict") if isinstance(verdict, Mapping) else None
        parse_ok = verdict.get("parse_ok") if isinstance(verdict, Mapping) else False
        if bool(parse_ok) != (selected in {"A", "B"}):
            raise AnalysisError(f"strict parser fields are inconsistent for {key}")
        correct_value = None if selected not in {"A", "B"} else selected == correct_position
        if "verdict_correct_strict" in result and result["verdict_correct_strict"] != correct_value:
            raise AnalysisError(f"stored strict correctness disagrees with rendered text for {key}")
        records.append(AnalysisRecord(
            **common, correct=correct_value, correct_position=correct_position))

    validate_record_structure(records)
    return records


def validate_record_structure(records: Sequence[AnalysisRecord]) -> None:
    if not records:
        raise AnalysisError("analysis record set is empty")
    keys = [record.cell_key for record in records]
    if len(keys) != len(set(keys)):
        raise AnalysisError("analysis records contain duplicate cell keys")
    slots: set[tuple[str, tuple[str, str, str, int, int, int]]] = set()
    pairs: dict[tuple[str, str, str, int, str, int], list[AnalysisRecord]] = defaultdict(list)
    world_by_q: dict[str, str] = {}
    for record in records:
        identity = (record.condition, record.slot_key)
        if identity in slots:
            raise AnalysisError(f"duplicate condition-slot identity {identity}")
        slots.add(identity)
        prior_world = world_by_q.setdefault(record.question_id, record.world)
        if prior_world != record.world:
            raise AnalysisError(f"question {record.question_id} has inconsistent worlds")
        pairs[(record.question_id, record.judge, record.debater,
               record.transcript_index, record.condition,
               record.within_side_replicate)].append(record)
    for unit, pair in pairs.items():
        if {record.side for record in pair} != {0, 1} or len(pair) != 2:
            raise AnalysisError(f"mirror unit {unit} does not contain exactly sides 0 and 1")
        observed = [record for record in pair if record.origin == "observed"]
        if len(observed) == 2 and {record.correct_position for record in observed} != {"A", "B"}:
            raise AnalysisError(f"mirror unit {unit} is not mirrored in rendered text")


def _error(correct: bool | None, *, invalid_error: float = 1.0) -> float:
    if correct is None:
        return invalid_error
    return 0.0 if correct else 1.0


def contrast_question_values(
    records: Sequence[AnalysisRecord],
    left_condition: str,
    right_condition: str,
    *,
    domain_questions: Iterable[str] | None = None,
    valid_only: bool = False,
    invalid_error: float = 1.0,
) -> dict[str, Any]:
    """Return per-question pooled and per-judge paired contrast values."""
    if invalid_error not in (0.0, 1.0):
        raise AnalysisError("invalid_error must be exactly 0.0 or 1.0")
    domain = ({record.question_id for record in records}
              if domain_questions is None else set(domain_questions))
    if not domain:
        raise AnalysisError("estimand domain is empty")
    relevant = [record for record in records
                if record.question_id in domain
                and record.condition in {left_condition, right_condition}]
    indexed: dict[str, dict[tuple[str, str, str, int, int, int], AnalysisRecord]] = {
        left_condition: {}, right_condition: {}}
    for record in relevant:
        bucket = indexed[record.condition]
        if record.slot_key in bucket:
            raise AnalysisError(f"duplicate matched slot {record.condition}:{record.slot_key}")
        bucket[record.slot_key] = record
    if set(indexed[left_condition]) != set(indexed[right_condition]):
        raise AnalysisError(f"{left_condition}/{right_condition} matched slot sets differ")

    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    kept_slots = 0
    dropped_context = 0
    dropped_invalid = 0
    for slot in sorted(indexed[left_condition]):
        left = indexed[left_condition][slot]
        right = indexed[right_condition][slot]
        if not left.context_eligible or not right.context_eligible:
            dropped_context += 1
            continue
        if valid_only and (left.correct is None or right.correct is None):
            dropped_invalid += 1
            continue
        grouped[(left.question_id, left.judge, left.debater)].append(
            _error(left.correct, invalid_error=invalid_error)
            - _error(right.correct, invalid_error=invalid_error))
        kept_slots += 1

    judges = sorted({record.judge for record in relevant})
    debaters = sorted({record.debater for record in relevant})
    per_judge: dict[str, dict[str, float]] = {judge: {} for judge in judges}
    for question in sorted(domain):
        for judge in judges:
            debater_values: list[float] = []
            for debater in debaters:
                values = grouped.get((question, judge, debater), [])
                if not values:
                    raise AnalysisError(
                        f"zero support for {(question, judge, debater)} in "
                        f"{left_condition}/{right_condition}")
                debater_values.append(statistics.fmean(values))
            per_judge[judge][question] = statistics.fmean(debater_values)
    pooled = {
        question: statistics.fmean(per_judge[judge][question] for judge in judges)
        for question in sorted(domain)
    }
    return {
        "pooled": pooled,
        "by_judge": per_judge,
        "support": {
            "kept_matched_slots": kept_slots,
            "dropped_context_slots": dropped_context,
            "dropped_invalid_slots": dropped_invalid,
        },
    }


def stratified_question_draws(
    world_by_question: Mapping[str, str], b: int, seed: int,
) -> list[dict[str, int]]:
    if b <= 0:
        raise AnalysisError("bootstrap B must be positive")
    strata: dict[str, list[str]] = defaultdict(list)
    for question, world in sorted(world_by_question.items()):
        strata[str(world)].append(str(question))
    if not strata:
        raise AnalysisError("bootstrap has no question strata")
    rng = random.Random(seed)
    draws: list[dict[str, int]] = []
    for _ in range(b):
        multiplicity: dict[str, int] = defaultdict(int)
        for world in sorted(strata):
            questions = sorted(strata[world])
            for _ in questions:
                multiplicity[questions[rng.randrange(len(questions))]] += 1
        draws.append(dict(sorted(multiplicity.items())))
    return draws


def draw_matrix_sha256(draws: Sequence[Mapping[str, int]]) -> str:
    payload = json.dumps(
        list(draws), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def weighted_domain_mean(
    per_question: Mapping[str, float],
    multiplicity: Mapping[str, int] | None,
    world_by_question: Mapping[str, str],
    domain_questions: Iterable[str],
) -> float:
    """Fixed-domain world mixture with within-world bootstrap normalization."""
    domain = tuple(sorted(set(domain_questions)))
    if not domain:
        raise AnalysisError("estimand domain is empty")
    if set(domain) - per_question.keys():
        raise AnalysisError("per-question values do not cover the estimand domain")
    by_world: dict[str, list[str]] = defaultdict(list)
    for question in domain:
        by_world[str(world_by_question[question])].append(question)
    total_questions = len(domain)
    answer = 0.0
    for world in sorted(by_world):
        questions = by_world[world]
        weights = [1 if multiplicity is None else int(multiplicity.get(q, 0))
                   for q in questions]
        denominator = sum(weights)
        if denominator == 0:
            raise AnalysisError(f"bootstrap draw has zero support in domain world {world}")
        world_mean = sum(w * per_question[q] for q, w in zip(questions, weights, strict=True))
        world_mean /= denominator
        answer += (len(questions) / total_questions) * world_mean
    return answer


def _bootstrap_values(
    per_question: Mapping[str, float],
    draws: Sequence[Mapping[str, int]],
    world_by_question: Mapping[str, str],
    domain: Sequence[str],
) -> list[float]:
    return [weighted_domain_mean(per_question, draw, world_by_question, domain)
            for draw in draws]


def sup_t_band(
    points: Mapping[str, float],
    replicates: Mapping[str, Sequence[float]],
    *,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Studentized maximum-modulus band over the primary family only."""
    ids = tuple(sorted(points))
    lengths = {len(replicates[key]) for key in ids}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) < 2:
        raise AnalysisError("sup-t requires equal replicate counts of at least two")
    ses = {key: statistics.stdev(replicates[key]) for key in ids}
    maxima: list[float] = []
    for index in range(next(iter(lengths))):
        components: list[float] = []
        for key in ids:
            deviation = abs(float(replicates[key][index]) - float(points[key]))
            se = ses[key]
            if se == 0.0:
                if deviation > ZERO_SE_TOLERANCE:
                    raise AnalysisError(
                        f"zero bootstrap SE for {key} but a draw differs from its point")
                components.append(0.0)
            else:
                components.append(deviation / se)
        maxima.append(max(components))
    critical = percentile_ci(maxima, lo=100.0 * (1.0 - alpha),
                             hi=100.0 * (1.0 - alpha))[0]
    assert critical is not None
    return {
        "critical_value": critical,
        "standard_errors": ses,
        "bands": {
            key: [points[key] - critical * ses[key], points[key] + critical * ses[key]]
            for key in ids
        },
        "role": "descriptive_only_not_a_significance_criterion",
    }


def paired_mirror_diagnostics(records: Sequence[AnalysisRecord]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, int, str, int], list[AnalysisRecord]] = defaultdict(list)
    for record in records:
        groups[(record.question_id, record.judge, record.debater,
                record.transcript_index, record.condition,
                record.within_side_replicate)].append(record)
    raw: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"planned": 0, "affected": 0, "diffs": [], "means": [],
                 "errors": [], "valid": 0, "consistent": 0, "invalid_rows": 0})
    for unit, pair in groups.items():
        _question, judge, _debater, _transcript, condition, _within = unit
        bucket = raw[(judge, condition)]
        bucket["planned"] += 1
        if len(pair) != 2 or any(
                record.origin != "observed" or not record.context_eligible for record in pair):
            bucket["affected"] += 1
            continue
        by_position = {record.correct_position: record for record in pair}
        if set(by_position) != {"A", "B"}:
            raise AnalysisError(f"paired diagnostic unit {unit} is not rendered-mirrored")
        error_a = _error(by_position["A"].correct)
        error_b = _error(by_position["B"].correct)
        bucket["diffs"].append(error_a - error_b)
        bucket["means"].append((error_a + error_b) / 2.0)
        bucket["errors"].extend((error_a, error_b))
        invalids = int(by_position["A"].correct is None) + int(
            by_position["B"].correct is None)
        bucket["invalid_rows"] += invalids
        if invalids == 0:
            bucket["valid"] += 1
            bucket["consistent"] += int(
                by_position["A"].correct == by_position["B"].correct)

    out: dict[str, Any] = {}
    for (judge, condition), values in sorted(raw.items()):
        pair_variance = (statistics.variance(values["means"])
                         if len(values["means"]) >= 2 else None)
        pooled_error = (statistics.fmean(values["errors"])
                        if values["errors"] else None)
        reference = (pooled_error * (1.0 - pooled_error) / 2.0
                     if pooled_error is not None else None)
        out[f"{judge}|{condition}"] = {
            "planned_pairs": values["planned"],
            "affected_pairs_excluded_from_diagnostic_only": values["affected"],
            "retained_pairs": len(values["diffs"]),
            "signed_position_effect_pp": (
                100.0 * statistics.fmean(values["diffs"]) if values["diffs"] else None),
            "invalid_rows_counted_wrong": values["invalid_rows"],
            "semantic_consistency_valid_pairs": (
                values["consistent"] / values["valid"] if values["valid"] else None),
            "pair_mean_error_sample_variance": pair_variance,
            "independent_rows_reference_variance": reference,
            "variance_ratio_vs_independent_rows": (
                pair_variance / reference if pair_variance is not None and reference else None),
        }
    return out


def invalid_count_strata(records: Sequence[AnalysisRecord]) -> list[dict[str, Any]]:
    """Return the complete budget x judge x replicate-block INVALID census."""
    judges = sorted({record.judge for record in records})
    conditions = [condition for condition in ALL_CONDITIONS
                  if any(record.condition == condition for record in records)]
    within_values = sorted({record.within_side_replicate for record in records})
    if within_values != list(range(len(within_values))):
        raise AnalysisError("within-side replicate indices must be contiguous from zero")
    replicates_per_side = len(within_values)
    rows: list[dict[str, Any]] = []
    for condition in conditions:
        for judge in judges:
            for side in (0, 1):
                for within in within_values:
                    block = side * replicates_per_side + within
                    stratum = [
                        record for record in records
                        if record.condition == condition
                        and record.judge == judge
                        and record.side == side
                        and record.within_side_replicate == within
                    ]
                    if not stratum:
                        raise AnalysisError(
                            f"missing INVALID-count stratum {condition}/{judge}/{block}")
                    rows.append({
                        "condition": condition,
                        "query_budget": CONDITION_BUDGET[condition],
                        "judge": judge,
                        "replicate_block_zero_based": block,
                        "side_zero_based": side,
                        "within_side_replicate_zero_based": within,
                        "planned_rows": len(stratum),
                        "context_ineligible_rows": sum(
                            not record.context_eligible for record in stratum),
                        "invalid_count": sum(
                            record.context_eligible and record.correct is None
                            for record in stratum),
                        "terminal_invalid_count": sum(
                            record.origin == "terminal_invalid" for record in stratum),
                    })
    return rows


def capability_slope(
    per_judge_d8: Mapping[str, float], anchor_scores: Mapping[str, float],
) -> dict[str, Any]:
    judges = sorted(per_judge_d8)
    if set(judges) != set(anchor_scores):
        raise AnalysisError("capability scores and per-judge D8 have different rosters")
    xs = [float(anchor_scores[judge]) for judge in judges]
    ys = [float(per_judge_d8[judge]) for judge in judges]
    xbar = statistics.fmean(xs)
    denominator = sum((x - xbar) ** 2 for x in xs)
    if denominator == 0.0:
        return {
            "status": "not_estimable_zero_anchor_spread",
            "estimate": None,
            "p_value": None,
            "judge_count": len(judges),
        }
    ybar = statistics.fmean(ys)
    slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys, strict=True)) / denominator
    return {
        "status": "estimate_and_plot_only_no_p_value" if len(judges) < 6 else "nominal",
        "estimate": slope,
        "p_value": None if len(judges) < 6 else "requires_bootstrap_replicates",
        "judge_count": len(judges),
    }


def capability_slope_with_bootstrap(
    per_judge_d8: Mapping[str, float],
    per_judge_d8_replicates: Mapping[str, Sequence[float]],
    anchor_scores: Mapping[str, float],
) -> dict[str, Any]:
    """Fit the frozen descriptive capability slope on common D8 draws.

    Anchor scores stay fixed.  Each bootstrap replicate refits the slope to the
    per-judge D8 estimates produced by that same question-cluster draw.  With
    the frozen two-judge roster this is descriptive only and never yields a
    p-value.
    """
    point = capability_slope(per_judge_d8, anchor_scores)
    judges = sorted(per_judge_d8)
    if set(per_judge_d8_replicates) != set(judges):
        raise AnalysisError("capability D8 replicates and point estimates differ in roster")
    lengths = {len(per_judge_d8_replicates[judge]) for judge in judges}
    if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
        raise AnalysisError("capability D8 bootstrap replicate counts differ or are empty")
    out = {
        **point,
        "anchor_scores": {judge: float(anchor_scores[judge]) for judge in judges},
        "per_judge_d8": {judge: float(per_judge_d8[judge]) for judge in judges},
        "common_d8_bootstrap_replicates": next(iter(lengths)),
        "slope_bootstrap_replicates": 0,
        "ci95_percentile_descriptive": None,
        "display": None,
        "leave_one_judge_out": {
            omitted: capability_slope(
                {judge: per_judge_d8[judge] for judge in judges if judge != omitted},
                {judge: anchor_scores[judge] for judge in judges if judge != omitted},
            )
            for omitted in judges
        },
    }
    if point["estimate"] is None:
        return out

    samples: list[float] = []
    for replicate_index in range(next(iter(lengths))):
        replicate_d8 = {
            judge: float(per_judge_d8_replicates[judge][replicate_index])
            for judge in judges
        }
        replicate = capability_slope(replicate_d8, anchor_scores)
        if replicate["estimate"] is None:
            raise AnalysisError("nonzero anchor spread produced an undefined slope")
        samples.append(float(replicate["estimate"]))
    interval = percentile_ci(samples)
    estimate = float(point["estimate"])
    out["ci95_percentile_descriptive"] = list(interval)
    out["slope_bootstrap_replicates"] = len(samples)
    out["display"] = {
        "estimate_d8_pp_per_10pp_anchor": format_effect_pp(estimate * 0.1),
        "ci95_d8_pp_per_10pp_anchor": [
            format_effect_pp(interval[0] * 0.1),
            format_effect_pp(interval[1] * 0.1),
        ],
    }
    return out


def format_effect_pp(value: float) -> str:
    return format(
        (Decimal(str(value)) * Decimal(100)).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_EVEN),
        ".3f",
    )


def format_p_value(value: float) -> str:
    return format(
        Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN),
        ".6f",
    )


def _entry(point: float, samples: Sequence[float], *, p: float | None = None,
           p_holm: float | None = None) -> dict[str, Any]:
    ci = percentile_ci(list(samples))
    return {
        "estimate": point,
        "ci95_percentile": list(ci),
        "p_two_sided": p,
        "p_holm": p_holm,
        "display": {
            "estimate_pp": format_effect_pp(point),
            "ci95_pp": [format_effect_pp(ci[0]), format_effect_pp(ci[1])],
            "p_two_sided": format_p_value(p) if p is not None else None,
            "p_holm": format_p_value(p_holm) if p_holm is not None else None,
        },
    }


def analyze_records(
    records: Sequence[AnalysisRecord],
    *,
    b: int = B_DEFAULT,
    seed: int = SEED_DEFAULT,
    domain_by_estimand: Mapping[str, Sequence[str]] | None = None,
    capability_anchor_scores: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, Any]:
    validate_record_structure(records)
    world_by_question = {
        record.question_id: record.world for record in records
    }
    all_questions = tuple(sorted(world_by_question))
    domains = {key: tuple(all_questions) for key in (*PRIMARY_IDS, "S1")}
    if domain_by_estimand:
        for key, domain in domain_by_estimand.items():
            if key not in domains:
                raise AnalysisError(f"unknown estimand domain {key}")
            domains[key] = tuple(sorted(set(domain)))
    draws = stratified_question_draws(world_by_question, b, seed)

    strict_values: dict[str, dict[str, Any]] = {}
    valid_values: dict[str, dict[str, Any]] = {}
    specs = {**PRIMARY_SPECS, "S1": S1_SPEC}
    for key, (left, right) in specs.items():
        strict_values[key] = contrast_question_values(
            records, left, right, domain_questions=domains[key], valid_only=False)
        valid_values[key] = contrast_question_values(
            records, left, right, domain_questions=domains[key], valid_only=True)

    points: dict[str, float] = {}
    reps: dict[str, list[float]] = {}
    valid_points: dict[str, float] = {}
    valid_reps: dict[str, list[float]] = {}
    for key in specs:
        points[key] = weighted_domain_mean(
            strict_values[key]["pooled"], None, world_by_question, domains[key])
        reps[key] = _bootstrap_values(
            strict_values[key]["pooled"], draws, world_by_question, domains[key])
        valid_points[key] = weighted_domain_mean(
            valid_values[key]["pooled"], None, world_by_question, domains[key])
        valid_reps[key] = _bootstrap_values(
            valid_values[key]["pooled"], draws, world_by_question, domains[key])

    invalid_scenarios: dict[str, Any] = {}
    for scenario, invalid_error in (
        ("all_invalid_correct", 0.0),
        ("all_invalid_wrong", 1.0),
    ):
        scenario_entries: dict[str, Any] = {}
        for key, (left, right) in specs.items():
            values = contrast_question_values(
                records,
                left,
                right,
                domain_questions=domains[key],
                invalid_error=invalid_error,
            )
            point = weighted_domain_mean(
                values["pooled"], None, world_by_question, domains[key])
            samples = _bootstrap_values(
                values["pooled"], draws, world_by_question, domains[key])
            by_judge = {}
            for judge, question_values in values["by_judge"].items():
                judge_point = weighted_domain_mean(
                    question_values, None, world_by_question, domains[key])
                judge_samples = _bootstrap_values(
                    question_values, draws, world_by_question, domains[key])
                by_judge[judge] = {
                    **_entry(judge_point, judge_samples),
                    "role": "descriptive_endpoint_specific_scenario_no_p_value",
                }
            scenario_entries[key] = {
                **_entry(point, samples),
                "role": "descriptive_scenario_no_p_value",
                "by_judge": by_judge,
            }
        invalid_scenarios[scenario] = scenario_entries

    primary_p = {key: bootstrap_p_two_sided(reps[key]) for key in PRIMARY_IDS}
    primary_holm = holm(primary_p)
    primary = {
        key: _entry(points[key], reps[key], p=primary_p[key], p_holm=primary_holm[key])
        for key in PRIMARY_IDS
    }
    s1_p = bootstrap_p_two_sided(reps["S1"])

    per_judge: dict[str, Any] = {}
    per_judge_points: dict[str, dict[str, float]] = {}
    per_judge_replicates: dict[str, dict[str, list[float]]] = {}
    judges = sorted({record.judge for record in records})
    for judge in judges:
        per_judge[judge] = {}
        per_judge_points[judge] = {}
        per_judge_replicates[judge] = {}
        for key in specs:
            question_values = strict_values[key]["by_judge"][judge]
            point = weighted_domain_mean(
                question_values, None, world_by_question, domains[key])
            samples = _bootstrap_values(
                question_values, draws, world_by_question, domains[key])
            per_judge_points[judge][key] = point
            per_judge_replicates[judge][key] = samples
            per_judge[judge][key] = _entry(point, samples)
            per_judge[judge][key]["role"] = "descriptive_endpoint_specific_no_p_value"

    draw_hash = draw_matrix_sha256(draws)
    if capability_anchor_scores is None:
        capability: dict[str, Any] = {
            "status": "not_computed_no_frozen_anchor_scores_supplied",
        }
    else:
        if set(capability_anchor_scores) != {"tolerant", "strict"}:
            raise AnalysisError("capability anchors must provide tolerant and strict scores")
        d8_points = {judge: per_judge_points[judge]["D8"] for judge in judges}
        d8_replicates = {
            judge: per_judge_replicates[judge]["D8"] for judge in judges
        }
        tolerant = capability_slope_with_bootstrap(
            d8_points, d8_replicates, capability_anchor_scores["tolerant"])
        strict = capability_slope_with_bootstrap(
            d8_points, d8_replicates, capability_anchor_scores["strict"])
        capability = {
            "status": "reported_from_frozen_canary_anchors",
            "outcome": "per-judge strict-primary D8",
            "common_draw_matrix_sha256": draw_hash,
            "anchor_scores_fixed_across_draws": True,
            "tolerant_primary": {
                **tolerant,
                "role": "not_estimable_when_anchor_spread_is_zero",
            },
            "strict_sensitivity": {
                **strict,
                "role": "estimate_and_plot_only_descriptive_no_p_value",
            },
        }

    invalid_counts: dict[str, int] = {
        f"{judge}|{condition}": 0
        for judge in judges for condition in ALL_CONDITIONS
    }
    terminal_counts: dict[str, int] = {
        f"{judge}|{condition}": 0
        for judge in judges for condition in ALL_CONDITIONS
    }
    for record in records:
        if record.correct is None and record.context_eligible:
            invalid_counts[f"{record.judge}|{record.condition}"] += 1
        if record.origin == "terminal_invalid":
            terminal_counts[f"{record.judge}|{record.condition}"] += 1

    return {
        "schema_version": "phase3_main_analysis_results_v1",
        "bootstrap": {
            "B": b,
            "seed": seed,
            "prng": "CPython random.Random MT19937",
            "draw_matrix_sha256": draw_hash,
            "strata": {
                world: sorted(q for q, value in world_by_question.items() if value == world)
                for world in sorted(set(world_by_question.values()))
            },
        },
        "domains": {key: list(domains[key]) for key in domains},
        "primary": primary,
        "primary_sup_t": sup_t_band(
            {key: points[key] for key in PRIMARY_IDS},
            {key: reps[key] for key in PRIMARY_IDS}),
        "S1": {
            **_entry(points["S1"], reps["S1"], p=s1_p, p_holm=s1_p),
            "role": "confirmatory_secondary_family_of_one",
        },
        "valid_only_sensitivity": {
            key: {
                **_entry(valid_points[key], valid_reps[key]),
                "support": valid_values[key]["support"],
            }
            for key in specs
        },
        "all_invalid_scenarios": {
            "role": "descriptive_no_p_values_or_confirmatory_claims",
            "common_draw_matrix_sha256": draw_hash,
            **invalid_scenarios,
        },
        "strict_support": {key: strict_values[key]["support"] for key in specs},
        "per_judge_descriptive": per_judge,
        "capability_slope": capability,
        "paired_mirror_diagnostics": paired_mirror_diagnostics(records),
        "invalid_counts_by_judge_condition": dict(sorted(invalid_counts.items())),
        "invalid_counts_by_budget_judge_replicate_block": invalid_count_strata(records),
        "terminal_invalid_counts_by_judge_condition": dict(sorted(terminal_counts.items())),
        "claims": {
            "pooled": (
                "confirmatory only for the fixed equal-weight two-endpoint Together roster"),
            "per_judge": "descriptive endpoint-specific estimates; no per-judge p-values",
            "prohibited": "no model-scale or broader judge-population generalization",
        },
    }


def _validate_key_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AnalysisError(f"{label} must contain a JSON array of cell-key strings")
    if any(not item.strip() for item in value):
        raise AnalysisError(f"{label} contains an empty cell key")
    if len(value) != len(set(value)):
        raise AnalysisError(f"{label} contains duplicate cell keys")
    return [str(item) for item in value]


def _load_key_list(path: Path | None) -> list[str]:
    if path is None:
        return []
    return _validate_key_list(
        json.loads(path.read_text(encoding="utf-8")), str(path))


def _require_admitted_exclusions(
    terminal_cell_keys: Sequence[str], context_ineligible_cell_keys: Sequence[str],
) -> None:
    """Block bare exclusions until a production evidence artifact is implemented."""
    if terminal_cell_keys or context_ineligible_cell_keys:
        raise AnalysisError(
            "nonempty terminal/context exclusion keys are not admitted for confirmatory "
            "analysis without a production finalization artifact validating terminal "
            "evidence and stop bounds, the context-blocklist hash, and the exact partition"
        )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AnalysisError(f"JSON repeats key {key!r}")
        result[key] = value
    return result


def _load_finalization_exclusions(
    path: Path,
    *,
    results_path: Path,
    protocol: Mapping[str, Any],
    pins_path: Path,
    project_root: Path,
    expected_run_id: str,
    expected_manifest_canonical_sha256: str,
    expected_authorization_canonical_sha256: str,
    expected_authorization_raw_sha256: str,
    expected_authorization_signature_raw_sha256: str,
    authorization_approved_at_utc: str,
    authorization_valid_until_utc: str,
    expected_context_blocklist_path: Path,
    expected_manifest_output_paths: Mapping[str, Path],
    expected_provider_input_paths: Mapping[str, Path],
    expected_provider_input_raw_sha256s: Mapping[str, str],
    expected_reviewer_input_paths: Mapping[str, Path],
    expected_reviewer_input_raw_sha256s: Mapping[str, str],
    expected_capacity_result_path: Path,
    expected_capacity_result_raw_sha256: str,
    expected_capacity_dispatch_history_path: Path,
    expected_capacity_dispatch_history_raw_sha256: str,
    expected_review_packets_root_path: Path,
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    prior_reconciled_usd: str,
    stage_cap_usd: str,
    expected_results_raw_sha256: str | None = None,
    expected_pins_raw_sha256: str | None = None,
) -> tuple[list[str], list[str], str]:
    """Rebuild the full bound-artifact admission before admitting its partition."""
    try:
        raw = path.read_bytes()
        record = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except AnalysisError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"could not read a valid main finalization artifact: {path}") from exc
    if not isinstance(record, Mapping):
        raise AnalysisError("main finalization artifact must be an object")
    artifact_hashes = record.get("artifact_hashes")
    if expected_results_raw_sha256 is not None:
        if (
            not isinstance(artifact_hashes, Mapping)
            or not isinstance(artifact_hashes.get("result_store"), Mapping)
            or artifact_hashes["result_store"].get("raw_sha256")
            != expected_results_raw_sha256
        ):
            raise AnalysisError(
                "main finalization does not bind the result-store snapshot being analyzed")
    if expected_pins_raw_sha256 is not None:
        if (
            not isinstance(artifact_hashes, Mapping)
            or not isinstance(artifact_hashes.get("analysis_pins"), Mapping)
            or artifact_hashes["analysis_pins"].get("raw_sha256")
            != expected_pins_raw_sha256
        ):
            raise AnalysisError(
                "main finalization does not bind the analysis-pins snapshot being used")
    try:
        inventory = phase3_main_runner.build_main_inventory(protocol, project_root)
        validated = phase3_main_finalization.validate_finalization_from_bound_artifacts(
            record,
            inventory=inventory,
            expected_run_id=expected_run_id,
            expected_manifest_canonical_sha256=expected_manifest_canonical_sha256,
            expected_authorization_canonical_sha256=(
                expected_authorization_canonical_sha256),
            expected_authorization_raw_sha256=expected_authorization_raw_sha256,
            expected_authorization_signature_raw_sha256=(
                expected_authorization_signature_raw_sha256),
            authorization_approved_at_utc=authorization_approved_at_utc,
            authorization_valid_until_utc=authorization_valid_until_utc,
            expected_result_store_path=results_path.resolve(),
            expected_analysis_pins_path=pins_path.resolve(),
            expected_context_blocklist_path=expected_context_blocklist_path.resolve(),
            expected_manifest_output_paths=expected_manifest_output_paths,
            expected_provider_input_paths=expected_provider_input_paths,
            expected_provider_input_raw_sha256s=(
                expected_provider_input_raw_sha256s),
            expected_reviewer_input_paths=expected_reviewer_input_paths,
            expected_reviewer_input_raw_sha256s=(
                expected_reviewer_input_raw_sha256s),
            expected_capacity_result_path=expected_capacity_result_path,
            expected_capacity_result_raw_sha256=(
                expected_capacity_result_raw_sha256),
            expected_capacity_dispatch_history_path=(
                expected_capacity_dispatch_history_path),
            expected_capacity_dispatch_history_raw_sha256=(
                expected_capacity_dispatch_history_raw_sha256),
            expected_review_packets_root_path=(
                expected_review_packets_root_path),
            expected_checker_model=str(protocol["roster"]["query_checker"]),
            expected_oracle_model=str(protocol["roster"]["oracle"]),
            expected_reviewer_model=expected_reviewer_model,
            expected_reviewer_reasoning_effort=(
                expected_reviewer_reasoning_effort),
            expected_reviewer_concurrency=expected_reviewer_concurrency,
            prior_reconciled_usd=prior_reconciled_usd,
            stage_cap_usd=stage_cap_usd,
        )
    except (
        phase3_main_finalization.MainFinalizationError,
        phase3_main_runner.Phase3MainRunnerError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise AnalysisError(
            f"main finalization failed full bound-artifact validation: {exc}") from exc
    try:
        if path.read_bytes() != raw:
            raise AnalysisError("main finalization changed while it was validated")
    except OSError as exc:
        raise AnalysisError("main finalization became unreadable during validation") from exc
    partition = validated["partition"]
    terminal = _validate_key_list(
        partition.get("terminal_cell_keys"),
        "main finalization partition.terminal_cell_keys")
    context = _validate_key_list(
        partition.get("context_ineligible_cell_keys"),
        "main finalization partition.context_ineligible_cell_keys")
    return terminal, context, hashlib.sha256(raw).hexdigest()


def _resolve_exclusions(
    *,
    finalization_path: Path | None,
    terminal_path: Path | None,
    context_path: Path | None,
    results_path: Path,
    protocol: Mapping[str, Any],
    pins_path: Path,
    project_root: Path,
    expected_run_id: str,
    expected_manifest_canonical_sha256: str,
    expected_authorization_canonical_sha256: str,
    expected_authorization_raw_sha256: str,
    expected_authorization_signature_raw_sha256: str,
    authorization_approved_at_utc: str,
    authorization_valid_until_utc: str,
    expected_context_blocklist_path: Path,
    expected_manifest_output_paths: Mapping[str, Path],
    expected_provider_input_paths: Mapping[str, Path],
    expected_provider_input_raw_sha256s: Mapping[str, str],
    expected_reviewer_input_paths: Mapping[str, Path],
    expected_reviewer_input_raw_sha256s: Mapping[str, str],
    expected_capacity_result_path: Path,
    expected_capacity_result_raw_sha256: str,
    expected_capacity_dispatch_history_path: Path,
    expected_capacity_dispatch_history_raw_sha256: str,
    expected_review_packets_root_path: Path,
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    prior_reconciled_usd: str,
    stage_cap_usd: str,
    expected_results_raw_sha256: str | None = None,
    expected_pins_raw_sha256: str | None = None,
) -> tuple[list[str], list[str], str | None]:
    if finalization_path is not None and (terminal_path is not None or context_path is not None):
        raise AnalysisError(
            "--finalization cannot be combined with explicit terminal or context key lists")
    if finalization_path is not None:
        return _load_finalization_exclusions(
            finalization_path,
            results_path=results_path,
            protocol=protocol,
            pins_path=pins_path,
            project_root=project_root,
            expected_run_id=expected_run_id,
            expected_manifest_canonical_sha256=expected_manifest_canonical_sha256,
            expected_authorization_canonical_sha256=(
                expected_authorization_canonical_sha256),
            expected_authorization_raw_sha256=expected_authorization_raw_sha256,
            expected_authorization_signature_raw_sha256=(
                expected_authorization_signature_raw_sha256),
            authorization_approved_at_utc=authorization_approved_at_utc,
            authorization_valid_until_utc=authorization_valid_until_utc,
            expected_context_blocklist_path=expected_context_blocklist_path,
            expected_manifest_output_paths=expected_manifest_output_paths,
            expected_provider_input_paths=expected_provider_input_paths,
            expected_provider_input_raw_sha256s=(
                expected_provider_input_raw_sha256s),
            expected_reviewer_input_paths=expected_reviewer_input_paths,
            expected_reviewer_input_raw_sha256s=(
                expected_reviewer_input_raw_sha256s),
            expected_capacity_result_path=expected_capacity_result_path,
            expected_capacity_result_raw_sha256=(
                expected_capacity_result_raw_sha256),
            expected_capacity_dispatch_history_path=(
                expected_capacity_dispatch_history_path),
            expected_capacity_dispatch_history_raw_sha256=(
                expected_capacity_dispatch_history_raw_sha256),
            expected_review_packets_root_path=(
                expected_review_packets_root_path),
            expected_reviewer_model=expected_reviewer_model,
            expected_reviewer_reasoning_effort=(
                expected_reviewer_reasoning_effort),
            expected_reviewer_concurrency=expected_reviewer_concurrency,
            prior_reconciled_usd=prior_reconciled_usd,
            stage_cap_usd=stage_cap_usd,
            expected_results_raw_sha256=expected_results_raw_sha256,
            expected_pins_raw_sha256=expected_pins_raw_sha256,
        )
    terminal = _load_key_list(terminal_path)
    context = _load_key_list(context_path)
    _require_admitted_exclusions(terminal, context)
    return terminal, context, None


def _require_finalization_admission_unchanged(
    path: Path,
    *,
    results_path: Path,
    protocol: Mapping[str, Any],
    pins_path: Path,
    project_root: Path,
    validation_kwargs: Mapping[str, Any],
    expected_results_raw_sha256: str,
    expected_pins_raw_sha256: str,
    expected_terminal_cell_keys: Sequence[str],
    expected_context_ineligible_cell_keys: Sequence[str],
    expected_finalization_raw_sha256: str,
) -> None:
    """Repeat the complete evidence-graph admission immediately before writing PASS."""
    terminal, context, raw_sha256 = _load_finalization_exclusions(
        path,
        results_path=results_path,
        protocol=protocol,
        pins_path=pins_path,
        project_root=project_root,
        **dict(validation_kwargs),
        expected_results_raw_sha256=expected_results_raw_sha256,
        expected_pins_raw_sha256=expected_pins_raw_sha256,
    )
    if (
        terminal != list(expected_terminal_cell_keys)
        or context != list(expected_context_ineligible_cell_keys)
        or raw_sha256 != expected_finalization_raw_sha256
    ):
        raise AnalysisError(
            "main finalization admission changed during confirmatory analysis")


def _capability_anchor_proportions(
    pins: Mapping[str, Any],
) -> dict[str, dict[str, float]]:
    block = pins.get("capability_anchor_scores")
    if not isinstance(block, Mapping):
        raise AnalysisError("analysis-pins capability anchors are missing")
    result: dict[str, dict[str, float]] = {}
    for parser in ("tolerant", "strict"):
        scores = block.get(parser)
        if not isinstance(scores, Mapping) or not scores:
            raise AnalysisError(f"analysis-pins {parser} capability scores are missing")
        result[parser] = {}
        for judge, counts in scores.items():
            if not isinstance(judge, str) or not isinstance(counts, Mapping):
                raise AnalysisError(f"analysis-pins {parser} capability score is malformed")
            correct = counts.get("correct_count")
            total = counts.get("total_count")
            if (type(correct) is not int or type(total) is not int
                    or total <= 0 or correct < 0 or correct > total):
                raise AnalysisError(f"analysis-pins {parser} capability count is invalid")
            result[parser][judge] = correct / total
    return result


def validate_analysis_pins(
    pins: Mapping[str, Any], protocol: Mapping[str, Any], *, root: Path,
) -> None:
    """Fail closed on every pin the engine consumes or reports."""
    if pins.get("schema_version") != "phase3_main_analysis_pins_v1":
        raise AnalysisError("unsupported analysis-pins schema")
    if pins.get("status") != "offline_analysis_design_only":
        raise AnalysisError("analysis-pins status drifted")
    bindings = pins.get("bindings")
    if not isinstance(bindings, Mapping):
        raise AnalysisError("analysis-pins bindings are missing")
    if bindings.get("protocol_path") != "rejudge/phase3_protocol_v3_r6.json":
        raise AnalysisError("analysis-pins protocol path drifted")
    if canonical_sha256(protocol) != bindings.get("protocol_canonical_sha256"):
        raise AnalysisError("protocol hash does not match the analysis pins")
    if protocol.get("protocol_content_sha256") != bindings.get("protocol_content_sha256"):
        raise AnalysisError("protocol content hash does not match the analysis pins")
    scope = pins.get("scope")
    if not isinstance(scope, Mapping):
        raise AnalysisError("analysis-pins scope is missing")
    expected_scope = {
        "judges": list(protocol["roster"]["judges_final"]),
        "questions": 82,
        "world_question_counts": {"carath_norn": 27, "selvarath": 28,
                                  "vethun_sarak": 27},
        "conditions": list(ALL_CONDITIONS),
        "judgment_slots": 9_840,
        "domain": "all 82 questions for D1, D2, D4, D8, and S1",
    }
    for field, expected in expected_scope.items():
        if scope.get(field) != expected:
            raise AnalysisError(f"analysis-pins scope.{field} drifted")
    bootstrap = pins.get("bootstrap")
    if not isinstance(bootstrap, Mapping):
        raise AnalysisError("analysis-pins bootstrap block is missing")
    expected_bootstrap = {
        "B": B_DEFAULT,
        "seed": SEED_DEFAULT,
        "prng": (
            "CPython random.Random Mersenne Twister; randrange over sorted question IDs; "
            "worlds iterated in sorted order; Python version recorded in the result artifact"),
        "precomputed_full_draw_matrix_sha256": (
            "8ee28ad44b7db3ff32481303333f9f4ccfe75c35310436f46058af75183d7a54"),
    }
    for field, expected in expected_bootstrap.items():
        if bootstrap.get(field) != expected:
            raise AnalysisError(f"analysis-pins bootstrap.{field} drifted")
    expected_rounding = {
        "computation": (
            "IEEE-754 binary64 with no intermediate rounding; all tests and decisions use "
            "unrounded values"),
        "structured_output": "raw shortest-roundtrip JSON numbers",
        "display_effects": (
            "percentage points to 3 decimal places using Decimal(str(value)) and "
            "ROUND_HALF_EVEN"),
        "display_p_values": (
            "6 decimal places using Decimal(str(value)) and ROUND_HALF_EVEN"),
    }
    if pins.get("rounding") != expected_rounding:
        raise AnalysisError("analysis-pins rounding block drifted")
    authority = pins.get("authority")
    if authority != {
        "provider_calls_authorized": False,
        "paid_execution_authorized": False,
        "main_run_authorized": False,
        "spend_authorized_usd": 0,
    }:
        raise AnalysisError("analysis pins cannot authorize execution or spend")
    for section, expected_sha in FROZEN_PINS_SECTION_SHA256.items():
        if canonical_sha256(pins.get(section)) != expected_sha:
            raise AnalysisError(f"analysis-pins {section} block drifted")
    if canonical_sha256(pins) != FROZEN_PINS_CANONICAL_SHA256:
        raise AnalysisError("analysis-pins canonical content drifted")

    anchor_scores = _capability_anchor_proportions(pins)
    llama = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    qwen = "Qwen/Qwen3.8-2.4T-A95B"
    if anchor_scores != {
        "tolerant": {llama: 47 / 48, qwen: 47 / 48},
        "strict": {llama: 33 / 48, qwen: 47 / 48},
    }:
        raise AnalysisError("frozen capability anchor scores drifted")
    anchor_block = pins["capability_anchor_scores"]
    source = anchor_block["source"]
    finalization_path = root / source["finalization_record_path"]
    closeout_path = root / source["closeout_report_path"]
    for bound_path, expected_sha in (
        (finalization_path, source["finalization_record_raw_sha256"]),
        (closeout_path, source["closeout_report_raw_sha256"]),
    ):
        if not bound_path.is_file():
            raise AnalysisError(f"bound capability source is missing: {bound_path}")
        if hashlib.sha256(bound_path.read_bytes()).hexdigest() != expected_sha:
            raise AnalysisError(f"bound capability source hash drifted: {bound_path}")
    finalization = json.loads(finalization_path.read_text(encoding="utf-8"))
    if (finalization.get("run_id") != source["finalization_run_id"]
            or finalization.get("report_sha256") != source["sealed_canary_report_sha256"]
            or finalization.get("formal_status") != "complete"):
        raise AnalysisError("bound canary finalization identity or status drifted")
    if source["closeout_evidence"] not in closeout_path.read_text(encoding="utf-8"):
        raise AnalysisError("bound closeout report lacks the frozen capability evidence")
    for path_field, hash_field in (
        ("scope_decision_path", "scope_decision_raw_sha256"),
    ):
        bound_path = root / str(bindings.get(path_field))
        if not bound_path.is_file():
            raise AnalysisError(f"bound analysis input is missing: {bound_path}")
        if hashlib.sha256(bound_path.read_bytes()).hexdigest() != bindings.get(hash_field):
            raise AnalysisError(f"bound analysis input hash drifted: {bound_path}")
    reused = bindings.get("reused_inference_code")
    if not isinstance(reused, Mapping):
        raise AnalysisError("reused inference-code binding is missing")
    reused_path = root / str(reused.get("path"))
    if (not reused_path.is_file()
            or hashlib.sha256(reused_path.read_bytes()).hexdigest() != reused.get("raw_sha256")):
        raise AnalysisError("reused inference-code binding drifted")


def _git_head(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unavailable"


def _git_path_status(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(relative)],
            cwd=root, capture_output=True, text=True).returncode == 0
        if not tracked:
            return f"untracked:{relative}"
        return subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", str(relative)],
            cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unavailable"


def run_analysis(argv: Sequence[str] | None = None) -> AnalysisRunReceipt:
    parser = argparse.ArgumentParser(prog="phase3_main_analysis")
    parser.add_argument("--results", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--protocol", default=str(PROTOCOL_PATH_DEFAULT))
    parser.add_argument("--pins", default=str(PINS_PATH_DEFAULT))
    parser.add_argument(
        "--finalization",
        required=True,
        help="fully validated production finalization admission for this exact result store",
    )
    parser.add_argument("--out", default=str(OUTPUT_PATH_DEFAULT))
    parser.add_argument("--project-root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)

    root = Path(args.project_root).resolve()
    protocol_path = Path(args.protocol).resolve()
    pins_path = Path(args.pins).resolve()
    results_path = Path(args.results).resolve()
    manifest_path = Path(args.manifest).resolve()
    authorization_path = Path(args.authorization).resolve()
    authorization_signature_path = authorization_path.with_name(
        f"{authorization_path.name}.sig")
    finalization_path = Path(args.finalization).resolve()
    out_path = Path(args.out).resolve()
    if out_path in {
        protocol_path,
        pins_path,
        results_path,
        manifest_path,
        authorization_path,
        authorization_signature_path,
        finalization_path,
    }:
        raise AnalysisError("analysis output path must differ from every immutable input")

    manifest_raw = _read_stable_bytes(manifest_path, "launch manifest")
    try:
        manifest = json.loads(
            manifest_raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except AnalysisError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError("launch manifest is not valid unique-key UTF-8 JSON") from exc
    if not isinstance(manifest, Mapping):
        raise AnalysisError("launch manifest must be a JSON object")
    try:
        manifest_validation = phase3_main_manifest.validate_main_manifest(
            manifest, project_root=root, verify_files=True, verify_runtime=False)
        authorization = (
            phase3_main_authorization.load_authenticated_owner_authorization(
                authorization_path))
        approved_at = phase3_main_manifest._utc(  # noqa: SLF001
            authorization["approved_at_utc"], "authorization.approved_at_utc")
        phase3_main_manifest.validate_main_authorization(
            authorization, manifest, as_of=approved_at)
    except (
        phase3_main_authorization.MainAuthorizationSignatureError,
        phase3_main_manifest.MainManifestError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise AnalysisError(
            f"signed launch authority failed validation: {exc}") from exc
    authorization_raw = _read_stable_bytes(
        authorization_path, "owner authorization")
    authorization_signature_raw = _read_stable_bytes(
        authorization_signature_path, "owner authorization signature")
    if canonical_sha256(authorization) != canonical_sha256(json.loads(
            authorization_raw.decode("utf-8"), object_pairs_hook=_unique_json_object)):
        raise AnalysisError("authenticated authorization differs from its stable byte snapshot")

    protocol_raw = _read_stable_bytes(protocol_path, "protocol")
    pins_raw = _read_stable_bytes(pins_path, "analysis pins")
    results_raw = _read_stable_bytes(results_path, "result store")
    try:
        protocol = json.loads(
            protocol_raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
        pins = json.loads(
            pins_raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except AnalysisError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnalysisError("protocol or analysis pins is not valid unique-key UTF-8 JSON") from exc
    if not isinstance(protocol, Mapping) or not isinstance(pins, Mapping):
        raise AnalysisError("protocol and analysis pins must be JSON objects")
    input_paths = manifest_validation["input_paths"]
    output_paths = manifest_validation["output_paths"]
    if (
        input_paths["protocol"] != protocol_path
        or input_paths["analysis_pins"] != pins_path
        or output_paths["results"] != results_path
        or output_paths["finalization"] != finalization_path
        or output_paths["analysis_results"] != out_path
    ):
        raise AnalysisError(
            "analysis paths differ from the signed launch manifest")
    manifest_input_snapshots = tuple(
        _snapshot_sha256_bound_input(
            input_paths[name],
            str(manifest["input_bindings"][name]["sha256"]),
            f"manifest input {name}",
        )
        for name in sorted(input_paths)
    )
    phase3_plan.validate_protocol(protocol)
    validate_analysis_pins(pins, protocol, root=root)
    judges = tuple(protocol["roster"]["judges_final"])
    question_bank_snapshot = _snapshot_protocol_bound_question_bank(protocol, root)
    main_ids = question_bank_snapshot.main_ids
    plan_cells = phase3_plan.enumerate_cells(protocol, judges, main_ids)
    context_snapshots: list[tuple[Path, bytes, str]] = []
    context_inputs: dict[str, Mapping[str, Any]] = {}
    for name, label in (
        ("prompt_bundle", "prompt bundle"),
        ("role_limits", "role limits"),
        ("main_transcript_bundle", "main transcript bundle"),
        ("context_blocklist", "context blocklist"),
    ):
        value, raw = _read_stable_json_object(input_paths[name], label)
        context_inputs[name] = value
        context_snapshots.append((input_paths[name], raw, label))
    try:
        recomputed_context_keys = phase3_main_context.validate_main_context_blocklist(
            context_inputs["context_blocklist"],
            protocol=protocol,
            inventory=phase3_main_runner.build_main_inventory(protocol, root),
            prompt_bundle=context_inputs["prompt_bundle"],
            role_limits=context_inputs["role_limits"],
            transcript_bundle=context_inputs["main_transcript_bundle"],
        )
    except phase3_main_context.MainContextBlocklistError as exc:
        raise AnalysisError(f"main context blocklist failed recomputation: {exc}") from exc
    rows = _read_jsonl_bytes(results_raw, results_path)
    question_bank = question_bank_snapshot.bank
    missing_main_questions = set(main_ids) - set(question_bank)
    if missing_main_questions:
        raise AnalysisError(
            f"question bank lacks protocol-derived main IDs: {sorted(missing_main_questions)}")
    terminal_path: Path | None = None
    context_path: Path | None = None
    finalization_validation_kwargs: dict[str, Any] = {
        "expected_run_id": str(manifest["run_id"]),
        "expected_manifest_canonical_sha256": (
            phase3_main_manifest.manifest_canonical_sha256(manifest)),
        "expected_authorization_canonical_sha256": canonical_sha256(authorization),
        "expected_authorization_raw_sha256": (
            hashlib.sha256(authorization_raw).hexdigest()),
        "expected_authorization_signature_raw_sha256": (
            hashlib.sha256(authorization_signature_raw).hexdigest()),
        "authorization_approved_at_utc": str(authorization["approved_at_utc"]),
        "authorization_valid_until_utc": str(authorization["valid_until_utc"]),
        "expected_context_blocklist_path": input_paths["context_blocklist"],
        "expected_manifest_output_paths": output_paths,
        "expected_provider_input_paths": {
            name: input_paths[name]
            for name in phase3_main_finalization.PROVIDER_INPUT_FIELDS
        },
        "expected_provider_input_raw_sha256s": {
            name: str(manifest["input_bindings"][name]["sha256"])
            for name in phase3_main_finalization.PROVIDER_INPUT_FIELDS
        },
        "expected_reviewer_input_paths": {
            name: input_paths[name]
            for name in phase3_main_finalization.REVIEWER_INPUT_FIELDS
        },
        "expected_reviewer_input_raw_sha256s": {
            name: str(manifest["input_bindings"][name]["sha256"])
            for name in phase3_main_finalization.REVIEWER_INPUT_FIELDS
        },
        "expected_capacity_result_path": input_paths["capacity_result"],
        "expected_capacity_result_raw_sha256": str(
            manifest["input_bindings"]["capacity_result"]["sha256"]),
        "expected_capacity_dispatch_history_path": input_paths[
            "capacity_dispatch_history"],
        "expected_capacity_dispatch_history_raw_sha256": str(
            manifest["input_bindings"]["capacity_dispatch_history"]["sha256"]),
        "expected_review_packets_root_path": output_paths["review_packets_root"],
        "expected_reviewer_model": str(manifest["runtime"]["reviewer_model"]),
        "expected_reviewer_reasoning_effort": str(
            manifest["runtime"]["reviewer_reasoning_effort"]),
        "expected_reviewer_concurrency": int(
            manifest["runtime"]["reviewer_concurrency"]),
        "prior_reconciled_usd": str(manifest["spend"]["prior_reconciled_usd"]),
        "stage_cap_usd": str(authorization["stage_cap_usd"]),
    }
    (terminal_cell_keys,
     context_ineligible_cell_keys,
     finalization_raw_sha256) = _resolve_exclusions(
        finalization_path=finalization_path,
        terminal_path=terminal_path,
        context_path=context_path,
        results_path=results_path,
        protocol=protocol,
        pins_path=pins_path,
        project_root=root,
        **finalization_validation_kwargs,
        expected_results_raw_sha256=hashlib.sha256(results_raw).hexdigest(),
        expected_pins_raw_sha256=hashlib.sha256(pins_raw).hexdigest(),
    )
    if finalization_raw_sha256 is None:  # pragma: no cover - CLI requires --finalization
        raise AnalysisError("confirmatory analysis requires a validated finalization")
    finalization_input = _snapshot_sha256_bound_input(
        finalization_path, finalization_raw_sha256, "main finalization")
    if tuple(sorted(context_ineligible_cell_keys)) != tuple(
            sorted(recomputed_context_keys)):
        raise AnalysisError(
            "finalization context exclusions differ from the deterministic recomputation")
    stable_inputs = (
        (protocol_path, protocol_raw, "protocol"),
        (pins_path, pins_raw, "analysis pins"),
        (results_path, results_raw, "result store"),
        (manifest_path, manifest_raw, "launch manifest"),
        (authorization_path, authorization_raw, "owner authorization"),
        (authorization_signature_path, authorization_signature_raw,
         "owner authorization signature"),
        finalization_input,
        *context_snapshots,
        *question_bank_snapshot.stable_inputs,
        *manifest_input_snapshots,
    )
    for path, expected, label in stable_inputs:
        _require_unchanged(path, expected, label)
    records = build_analysis_records(
        rows=rows,
        plan_cells=plan_cells,
        protocol=protocol,
        question_bank=question_bank,
        terminal_cell_keys=terminal_cell_keys,
        context_ineligible_cell_keys=context_ineligible_cell_keys,
    )
    result = analyze_records(
        records,
        b=int(pins["bootstrap"]["B"]),
        seed=int(pins["bootstrap"]["seed"]),
        capability_anchor_scores=_capability_anchor_proportions(pins),
    )
    if result["bootstrap"]["draw_matrix_sha256"] != pins["bootstrap"][
            "precomputed_full_draw_matrix_sha256"]:
        raise AnalysisError("generated draw matrix differs from the frozen pin")
    engine_path = Path(__file__).resolve()
    repository_head = _git_head(root)
    engine_git_status = _git_path_status(root, engine_path)
    engine_clean_at_head = engine_git_status == ""
    result["integrity"] = {
        "repository_head_at_analysis": repository_head,
        "engine_git_commit": repository_head if engine_clean_at_head else None,
        "engine_git_state": (
            "clean_tracked_at_head" if engine_clean_at_head
            else "dirty_untracked_or_status_unavailable"),
        "engine_git_status_porcelain": engine_git_status or None,
        "engine_raw_sha256": hashlib.sha256(engine_path.read_bytes()).hexdigest(),
        "python_version": platform.python_version(),
        "protocol_canonical_sha256": canonical_sha256(protocol),
        "protocol_question_bank_bundle_sha256": protocol[
            "planning_cell_identity"]["question_bank_bundle_sha256"],
        "protocol_phase2_question_source_canonical_sha256": protocol[
            "source_bindings"]["canonical_json_sha256"]["rejudge/phase2_protocol.json"],
        "main_question_ids_canonical_sha256": canonical_sha256(list(main_ids)),
        "main_question_rows_canonical_sha256": canonical_sha256({
            question_id: question_bank[question_id] for question_id in sorted(main_ids)
        }),
        "pins_raw_sha256": hashlib.sha256(pins_raw).hexdigest(),
        "results_raw_sha256": hashlib.sha256(results_raw).hexdigest(),
        "finalization_raw_sha256": finalization_raw_sha256,
        "terminal_cell_keys_raw_sha256": (
            hashlib.sha256(terminal_path.read_bytes()).hexdigest() if terminal_path else None),
        "context_ineligible_cell_keys_raw_sha256": (
            hashlib.sha256(context_path.read_bytes()).hexdigest() if context_path else None),
        "record_count": len(records),
    }
    for path, expected, label in stable_inputs:
        _require_unchanged(path, expected, label)
    _require_finalization_admission_unchanged(
        finalization_path,
        results_path=results_path,
        protocol=protocol,
        pins_path=pins_path,
        project_root=root,
        validation_kwargs=finalization_validation_kwargs,
        expected_results_raw_sha256=hashlib.sha256(results_raw).hexdigest(),
        expected_pins_raw_sha256=hashlib.sha256(pins_raw).hexdigest(),
        expected_terminal_cell_keys=terminal_cell_keys,
        expected_context_ineligible_cell_keys=context_ineligible_cell_keys,
        expected_finalization_raw_sha256=finalization_raw_sha256,
    )
    output_raw = _write_analysis_output(out_path, result)
    print(json.dumps({
        "status": "pass",
        "out": str(out_path),
        "draw_matrix_sha256": result["bootstrap"]["draw_matrix_sha256"],
    }, sort_keys=True))
    return AnalysisRunReceipt(returncode=0, output_raw=output_raw)


def main(argv: Sequence[str] | None = None) -> int:
    return run_analysis(argv).returncode


if __name__ == "__main__":
    raise SystemExit(main())
