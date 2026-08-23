"""Build the read-only phase-3 v2 canary review artifact.

This is deliberately separate from ``phase3_canary_closeout.py``. That script is the
historical v1 derivation and encodes v1 completion, side-bias, pace, and forecast rules. The
v2 protocol changed all four. Reusing the v1 orchestration with new paths can therefore
produce a syntactically valid but scientifically wrong close-out.

This module performs no provider calls and never opens an archive file for writing. A normal
successful exit means the audit completed, not that the main-run gates passed. The binding
decision is recorded in ``main_authorization_ready`` in the output.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from contextlib import chdir
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rejudge import phase3_plan  # noqa: E402
from rejudge.api_client import (  # noqa: E402
    _read_usage_events,
    _read_usage_state,
    _summarize_usage_events,
    _validate_usage_chain,
    usage_ledger_state_path,
)
from rejudge.debate_gen import _load_question_bank  # noqa: E402
from rejudge.phase2_call_cache import CallCache  # noqa: E402
from rejudge.phase2_canary_order import CellResultStore  # noqa: E402
from rejudge.phase2_dual_gate import DualGateDecisionStore  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402
from rejudge.phase3_runner import (  # noqa: E402
    load_and_validate_manifest,
    load_deferral_list,
    load_phase3_canary_authorization,
    parse_capability_verdict_strict,
    parse_capability_verdict_tolerant,
)
import phase3_polarity_verify as polarity_verify  # noqa: E402


ARCHIVE_DIR_DEFAULT = Path("E:/selvarath-archive/phase3-v2-2026-08-21")
V1_ARCHIVE_DIR_DEFAULT = Path("E:/selvarath-archive/phase3-2026-08-18")
PROTOCOL_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_protocol_v2.json"
MANIFEST_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_manifest_v2_2026-08-21.json"
AUTHORIZATION_PATH_DEFAULT = (
    REPO_ROOT / "rejudge" / "phase3_canary_authorization_v2_2026-08-21.json")
DEFERRAL_PATH_DEFAULT = (
    REPO_ROOT / "rejudge" / "phase3_v2_qwen_deferral_cells_2026-08-22.json")
OUTPUT_PATH_DEFAULT = REPO_ROOT / "rejudge" / "phase3_canary_closeout_v2_2026-08-23.json"

INVALID_RATE_THRESHOLD = 0.02
PACE_GAP = timedelta(minutes=30)


def stream_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSONL file strictly enough for an audit."""
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"non-object JSON row at {path}:{line_number}")
            rows.append(row)
    return rows


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _store_tail(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "row_count": len(rows),
        "last_sequence": rows[-1]["sequence"] if rows else None,
        "last_event_hash": rows[-1]["event_hash"] if rows else None,
    }


def validate_result_store(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use the production loader for the chain, then add duplicate and sequence checks."""
    CellResultStore(path)
    rows = stream_jsonl(path)
    keys = [str(row["cell_key"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate cell key in result store {path}")
    if [row["sequence"] for row in rows] != list(range(len(rows))):
        raise ValueError(f"non-contiguous sequence in result store {path}")
    return rows, {"validation": "pass", **_store_tail(rows)}


def validate_decision_store(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    DualGateDecisionStore(path)
    rows = stream_jsonl(path)
    return rows, {"validation": "pass", **_store_tail(rows)}


def validate_call_cache(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = stream_jsonl(path)
    if not rows:
        raise ValueError(f"empty call cache: {path}")
    seed = rows[0].get("prev_event_hash")
    if seed == "genesis":
        CallCache(path)
    elif isinstance(seed, str) and seed.startswith("identity:"):
        CallCache(path, execution_identity=seed.removeprefix("identity:"))
    else:
        raise ValueError(f"unrecognized call-cache chain seed in {path}: {seed!r}")
    if [row["sequence"] for row in rows] != list(range(len(rows))):
        raise ValueError(f"non-contiguous sequence in call cache {path}")
    return rows, {"validation": "pass", "chain_seed": seed, **_store_tail(rows)}


def validate_usage_ledger(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the ledger and state without invoking the state-repairing public loader."""
    events = _read_usage_events(path)
    identity, hashes = _validate_usage_chain(events, path)
    state_path = usage_ledger_state_path(path)
    state = _read_usage_state(state_path)
    expected_state = {
        "schema_version": events[0]["schema_version"],
        "ledger_id": identity["ledger_id"],
        "last_sequence": len(events) - 1,
        "last_event_hash": hashes[-1],
    }
    if state != expected_state:
        raise ValueError(
            f"usage ledger state is not exactly at the validated tail: {state_path}")
    summary = _summarize_usage_events(events[1:], path, strict_lifecycle=True)
    return events, {
        "validation": "pass",
        "state_validation": "pass",
        "ledger_id": identity["ledger_id"],
        "last_sequence": len(events) - 1,
        "last_event_hash": hashes[-1],
        **summary,
    }


def build_completion_report(
    *,
    canary_cells: Sequence[Mapping[str, Any]],
    main_cells: Sequence[Mapping[str, Any]],
    result_rows: Sequence[Mapping[str, Any]],
    deferred_keys: Iterable[str],
    carried_anchor_count: int,
) -> dict[str, Any]:
    """Apply v2 plus amendment-1 completion semantics exactly."""
    deferred = frozenset(str(key) for key in deferred_keys)
    judgments = {
        str(cell["cell_key"]) for cell in canary_cells
        if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND
    }
    canary_transcripts = {
        str(cell["cell_key"]) for cell in canary_cells
        if cell["kind"] == phase3_plan.CANARY_TRANSCRIPT_KIND
    }
    anchors = {
        str(cell["cell_key"]) for cell in canary_cells
        if cell["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND
    }
    main_transcripts = {
        str(cell["cell_key"]) for cell in main_cells
        if cell["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND
    }
    if not deferred.issubset(judgments):
        raise ValueError("deferral list includes a non-canary-judgment key")

    expected_fresh = (judgments - deferred) | canary_transcripts | main_transcripts
    actual = {str(row["cell_key"]) for row in result_rows}
    missing = sorted(expected_fresh - actual)
    unexpected = sorted(actual - expected_fresh)
    completed_deferred = sorted(actual & deferred)
    anchors_in_v2 = sorted(actual & anchors)
    active_judgments = actual & judgments

    accounted_gate_inventory = len(active_judgments) + len(deferred) + carried_anchor_count
    expected_gate_inventory = len(judgments) + carried_anchor_count
    completion_pass = not any((missing, unexpected, completed_deferred, anchors_in_v2))
    completion_pass = completion_pass and len(active_judgments) == len(judgments - deferred)
    completion_pass = completion_pass and accounted_gate_inventory == expected_gate_inventory

    return {
        "fresh_store_rows": len(actual),
        "fresh_rows_expected_after_deferral": len(expected_fresh),
        "fresh_judgment_slots_planned": len(judgments),
        "fresh_judgments_completed_active": len(active_judgments),
        "fresh_judgments_deferred_by_amendment": len(deferred),
        "canary_transcript_references_completed": len(actual & canary_transcripts),
        "main_transcript_references_completed": len(actual & main_transcripts),
        "carried_anchor_bindings_verified": carried_anchor_count,
        "combined_gate_inventory_accounted": accounted_gate_inventory,
        "combined_gate_inventory_expected": expected_gate_inventory,
        "unexpected_missing_count": len(missing),
        "unexpected_missing_cell_keys": missing,
        "unexpected_row_count": len(unexpected),
        "unexpected_cell_keys": unexpected,
        "completed_deferred_count": len(completed_deferred),
        "completed_deferred_cell_keys": completed_deferred,
        "anchor_rows_erroneously_copied_into_v2_count": len(anchors_in_v2),
        "anchor_rows_erroneously_copied_into_v2": anchors_in_v2,
        "provisional_completion_gate_pass": completion_pass,
        "note": (
            "The 192 Qwen2.5 judgment cells are deferred by the owner-authorized carve-out. "
            "The 288 anchors are verified against the v1 store and are not copied into the "
            "v2 result store. The 540 transcript rows are preseeded operational inputs, not "
            "gate slots."),
    }


def capability_anchor_report(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Score only the exact v1 rows bound in the already revalidated v2 manifest."""
    frozen = manifest["frozen_inputs"]
    source_path = Path(str(frozen["anchor_carry_store_path"]))
    source_rows = stream_jsonl(source_path)
    by_key = {str(row["cell_key"]): row for row in source_rows}
    bound_rows = list(frozen["anchor_carry_rows"])
    if canonical_sha256(bound_rows) != frozen["anchor_carry_rows_sha256"]:
        raise ValueError("manifest anchor-carry rows hash mismatch")

    rows_by_judge: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    stored_parse_mismatches = 0
    for binding in bound_rows:
        key = str(binding["cell_key"])
        row = by_key.get(key)
        if row is None or row.get("event_hash") != binding.get("event_hash"):
            raise ValueError(f"anchor-carry binding mismatch for {key}")
        result = row["result"]
        raw = result.get("raw_verdict_text")
        side = result.get("side")
        tolerant = parse_capability_verdict_tolerant(raw)
        strict = parse_capability_verdict_strict(raw)
        if (tolerant == side) != bool(result.get("is_correct_tolerant")):
            stored_parse_mismatches += 1
        if (strict == side) != bool(result.get("is_correct_strict")):
            stored_parse_mismatches += 1
        rows_by_judge[str(result["judge_model"])].append(result)

    by_judge: dict[str, Any] = {}
    for judge in manifest["roster"]["judges"]:
        rows = rows_by_judge.get(judge, [])
        tolerant_score = sum(
            parse_capability_verdict_tolerant(row.get("raw_verdict_text")) == row.get("side")
            for row in rows)
        strict_score = sum(
            parse_capability_verdict_strict(row.get("raw_verdict_text")) == row.get("side")
            for row in rows)
        by_judge[judge] = {
            "n": len(rows),
            "tolerant_score": tolerant_score,
            "strict_score": strict_score,
        }
    return {
        "binding_validation": "pass",
        "source_store": source_path.as_posix(),
        "bound_row_count": len(bound_rows),
        "bound_rows_sha256": frozen["anchor_carry_rows_sha256"],
        "reparsed_vs_stored_mismatches": stored_parse_mismatches,
        "by_judge": by_judge,
    }


def _rendered_observation(
    row: Mapping[str, Any], question_bank: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    result = row["result"]
    content = polarity_verify._rendered_user_content(result)
    positions = polarity_verify.extract_rendered_positions(content) if content else None
    if positions is None:
        return {"resolved": False, "reason": "rendered_positions_unparseable"}
    question = question_bank.get(str(result["question_id"]))
    if question is None:
        return {"resolved": False, "reason": "question_missing_from_bank"}
    a_text, b_text = (value.strip() for value in positions)
    correct = str(question["correct_answer"]).strip()
    wrong = str(question["wrong_answer"]).strip()
    if a_text == correct and b_text == wrong:
        correct_position = "A"
    elif a_text == wrong and b_text == correct:
        correct_position = "B"
    else:
        return {"resolved": False, "reason": "rendered_answers_do_not_match_bank"}

    verdict = result.get("verdict_strict") or {}
    parse_ok = bool(verdict.get("parse_ok"))
    selected_position = verdict.get("verdict") if parse_ok else None
    selected_correct = bool(parse_ok and selected_position == correct_position)
    selected_semantic = None
    if parse_ok and selected_position in {"A", "B"}:
        selected_semantic = "correct" if selected_correct else "wrong"
    return {
        "resolved": True,
        "correct_position": correct_position,
        "parse_ok": parse_ok,
        "selected_correct": selected_correct,
        "selected_semantic": selected_semantic,
    }


def compute_paired_position_diagnostic(
    pairs: Sequence[Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Compute the v2 mandatory core-b0 paired diagnostic.

    The signed effect is error(A-correct row) minus error(B-correct row), in percentage
    points. Strict INVALID counts wrong for that ITT quantity. Semantic consistency is only
    defined when both verdicts parse. The variance ratio compares the observed sample
    variance of pair-mean error with p(1-p)/2, the variance expected for two independent rows
    having the same pooled error probability.
    """
    differences: list[float] = []
    pair_means: list[float] = []
    all_errors: list[float] = []
    valid_pair_count = 0
    semantic_consistent_count = 0
    invalid_pair_count = 0
    invalid_row_count = 0

    for pair in pairs:
        a = pair["A_correct"]
        b = pair["B_correct"]
        error_a = 0.0 if a["selected_correct"] else 1.0
        error_b = 0.0 if b["selected_correct"] else 1.0
        differences.append(error_a - error_b)
        pair_means.append((error_a + error_b) / 2.0)
        all_errors.extend((error_a, error_b))
        invalids = int(not a["parse_ok"]) + int(not b["parse_ok"])
        invalid_row_count += invalids
        if invalids:
            invalid_pair_count += 1
        else:
            valid_pair_count += 1
            if a["selected_semantic"] == b["selected_semantic"]:
                semantic_consistent_count += 1

    pair_variance = statistics.variance(pair_means) if len(pair_means) >= 2 else None
    pooled_error = statistics.fmean(all_errors) if all_errors else None
    independent_reference = (
        pooled_error * (1.0 - pooled_error) / 2.0 if pooled_error is not None else None)
    variance_ratio = None
    if pair_variance is not None and independent_reference and independent_reference > 0:
        variance_ratio = pair_variance / independent_reference

    return {
        "n_pairs": len(pairs),
        "signed_position_effect_pp": (
            100.0 * statistics.fmean(differences) if differences else None),
        "signed_position_effect_definition": (
            "error when the rendered correct answer is in A minus error when it is in B; "
            "strict INVALID counts wrong"),
        "valid_pair_count": valid_pair_count,
        "invalid_pair_count": invalid_pair_count,
        "invalid_row_count": invalid_row_count,
        "semantic_answer_consistent_valid_pairs": semantic_consistent_count,
        "semantic_answer_consistency_fraction": (
            semantic_consistent_count / valid_pair_count if valid_pair_count else None),
        "pair_mean_error_sample_variance": pair_variance,
        "independent_rows_reference_variance": independent_reference,
        "variance_ratio_vs_independent_rows": variance_ratio,
        "gate_consequence": "none; mandatory descriptive diagnostic",
    }


def build_calibration_report(
    *,
    result_rows: Sequence[Mapping[str, Any]],
    canary_cells: Sequence[Mapping[str, Any]],
    protocol: Mapping[str, Any],
    active_judges: Sequence[str],
    deferred_judge: str,
    held_out_ids: Sequence[str],
    question_bank: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    meta = {str(cell["cell_key"]): cell for cell in canary_cells}
    active = set(active_judges)
    continuing = set(protocol["roster"]["judges_continuing"])
    conditions = {
        str(condition["id"]): condition for condition in protocol["debate_grid"]["conditions"]}
    core_condition = next(
        condition_id for condition_id, condition in conditions.items()
        if int(condition["query_budget"]) == 0)

    rows_by_judge: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    invalid_keys_by_judge: dict[str, list[str]] = defaultdict(list)
    normalized_pairs: dict[str, dict[tuple[Any, ...], dict[str, Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(dict))
    unresolved_pair_rows: dict[str, list[str]] = defaultdict(list)

    for row in result_rows:
        cell = meta.get(str(row.get("cell_key")))
        if cell is None or cell.get("kind") != phase3_plan.CANARY_JUDGMENT_KIND:
            continue
        judge = str(cell["judge_model"])
        if judge not in active or cell["condition"] != core_condition:
            continue
        rows_by_judge[judge].append(row)
        verdict = row["result"].get("verdict_strict") or {}
        if not verdict.get("parse_ok"):
            invalid_keys_by_judge[judge].append(str(row["cell_key"]))

        observation = _rendered_observation(row, question_bank)
        if not observation.get("resolved"):
            unresolved_pair_rows[judge].append(str(row["cell_key"]))
            continue
        replicates = int(conditions[core_condition]["judgment_replicates_per_transcript_side"])
        replicate_index = int(cell["replicate_index"])
        within_side = replicate_index % replicates
        pair_key = (
            str(cell["question_id"]), str(cell["debater_model"]),
            cell.get("transcript_index"), within_side)
        label = f"{observation['correct_position']}_correct"
        if label in normalized_pairs[judge][pair_key]:
            unresolved_pair_rows[judge].append(str(row["cell_key"]))
            continue
        normalized_pairs[judge][pair_key][label] = observation

    gate_by_judge: dict[str, Any] = {}
    paired_by_judge: dict[str, Any] = {}
    core_split_by_judge: dict[str, Any] = {}
    continuing_failures: list[str] = []
    for judge in active_judges:
        rows = rows_by_judge.get(judge, [])
        invalid_keys = sorted(invalid_keys_by_judge.get(judge, []))
        invalid_rate = len(invalid_keys) / len(rows) if rows else None
        expected_n = 96
        passed = (
            len(rows) == expected_n and invalid_rate is not None
            and invalid_rate < INVALID_RATE_THRESHOLD)
        if not passed and judge in continuing:
            disposition = "halt_and_escalate_continuing_judge"
            continuing_failures.append(judge)
        elif not passed:
            disposition = "drop_new_judge_without_replacement"
        else:
            disposition = "pass"
        gate_by_judge[judge] = {
            "n_core_b0": len(rows),
            "expected_n_core_b0": expected_n,
            "strict_invalid_count": len(invalid_keys),
            "strict_invalid_rate": invalid_rate,
            "threshold": "strict INVALID / 96 < 0.02",
            "pass": passed,
            "disposition": disposition,
            "invalid_cell_keys": invalid_keys,
        }

        complete_pairs = [
            pair for pair in normalized_pairs.get(judge, {}).values()
            if set(pair) == {"A_correct", "B_correct"}
        ]
        incomplete_count = len(normalized_pairs.get(judge, {})) - len(complete_pairs)
        paired = compute_paired_position_diagnostic(complete_pairs)
        paired["incomplete_pair_count"] = incomplete_count
        paired["unresolved_row_count"] = len(unresolved_pair_rows.get(judge, []))
        paired["unresolved_cell_keys"] = sorted(unresolved_pair_rows.get(judge, []))
        paired_by_judge[judge] = paired
        a_count = sum(1 for pair in complete_pairs if "A_correct" in pair)
        b_count = sum(1 for pair in complete_pairs if "B_correct" in pair)
        core_split_by_judge[judge] = {
            "rendered_A_correct": a_count,
            "rendered_B_correct": b_count,
            "expected_each": 48,
            "pass": a_count == 48 and b_count == 48 and incomplete_count == 0,
        }

    structural = polarity_verify.verify(
        result_rows, protocol=protocol, judges=active_judges, held_out_ids=held_out_ids,
        question_bank=question_bank)
    structural_pass = (
        structural["rows_seen_as_canary_judgment_cells"] == 192 * len(active_judges)
        and structural["n_rows_unparseable"] == 0
        and structural["n_incomplete_or_missing_side_groups"] == 0
        and structural["pairs_duplicated"] == 0
        and structural["pairs_other_inconsistent_shape"] == 0
        and structural["n_pairs_inconsistent_across_conditions"] == 0
        and structural["pairs_mirrored"] == structural["pairs_total"]
        and all(item["pass"] for item in core_split_by_judge.values())
    )
    return {
        "active_judges": list(active_judges),
        "deferred_judge": deferred_judge,
        "strict_invalid_gate_by_judge": gate_by_judge,
        "all_active_invalid_gates_pass": all(item["pass"] for item in gate_by_judge.values()),
        "continuing_judge_failures": sorted(continuing_failures),
        "structural_mirroring": structural,
        "core_b0_rendered_split_by_judge": core_split_by_judge,
        "all_structural_gates_pass": structural_pass,
        "paired_position_diagnostics": paired_by_judge,
        "paired_diagnostic_scope": (
            "Core b0 only, 48 rendered mirrored pairs per active judge. These formulas are "
            "reported explicitly because v2 mandates the diagnostics but does not prescribe "
            "their exact estimator. They are descriptive and have no roster consequence."),
    }


def parse_stop_times(log_text: str) -> list[datetime]:
    stops: set[datetime] = set()
    for line in log_text.splitlines():
        is_stop_record = "a STOP was detected" in line or "]   supervisor: STOP" in line
        if not is_stop_record or not line.startswith("["):
            continue
        close = line.find("]")
        if close > 1:
            stops.add(parse_timestamp(line[1:close]))
    return sorted(stops)


def collect_packet_commits(archive_dir: Path) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for packet_dir in sorted(archive_dir.glob("review_packets_auto_*")):
        rulings_path = packet_dir / "rulings.jsonl"
        if not rulings_path.exists():
            continue
        packets.append({
            "path": rulings_path.as_posix(),
            "committed_at": datetime.fromtimestamp(
                rulings_path.stat().st_mtime, tz=timezone.utc),
            "payloads": [str(row["payload_sha256"]) for row in stream_jsonl(rulings_path)],
        })
    return packets


def compute_pace_window(
    *,
    decision_payloads: Iterable[str],
    packet_commits: Sequence[Mapping[str, Any]],
    stop_times: Sequence[datetime],
    usage_event_times: Sequence[datetime],
    gap: timedelta = PACE_GAP,
) -> dict[str, Any]:
    """Apply the v2 final uninterrupted-window rule to packet commit evidence."""
    required = set(str(payload) for payload in decision_payloads)
    earliest_commit: dict[str, datetime] = {}
    for packet in sorted(packet_commits, key=lambda item: item["committed_at"]):
        committed_at = packet["committed_at"].astimezone(timezone.utc)
        for payload in packet["payloads"]:
            payload = str(payload)
            if payload in required:
                earliest_commit.setdefault(payload, committed_at)

    unattributed = sorted(required - set(earliest_commit))
    ruling_times = sorted(earliest_commit.values())
    if not ruling_times:
        return {
            "attributed_unique_rulings": 0,
            "unattributed_unique_ruling_count": len(unattributed),
            "unattributed_payload_sha256": unattributed,
            "final_window": None,
        }

    ledger_times = sorted(value.astimezone(timezone.utc) for value in usage_event_times)
    gap_interruptions = []
    for previous, current in zip(ledger_times, ledger_times[1:]):
        if current - previous > gap:
            gap_interruptions.append({
                "previous_event": previous,
                "next_event": current,
                "gap_seconds": (current - previous).total_seconds(),
                "cutoff": current,
            })

    last_ruling = ruling_times[-1]
    interruption_candidates: list[tuple[datetime, str, dict[str, Any]]] = []
    for stop in stop_times:
        stop = stop.astimezone(timezone.utc)
        if stop < last_ruling:
            interruption_candidates.append((stop, "STOP", {"timestamp": iso_utc(stop)}))
    for item in gap_interruptions:
        cutoff = item["cutoff"]
        if cutoff < last_ruling:
            interruption_candidates.append((cutoff, "ledger_gap", {
                "cutoff": iso_utc(cutoff),
                "previous_event": iso_utc(item["previous_event"]),
                "next_event": iso_utc(item["next_event"]),
                "gap_seconds": item["gap_seconds"],
            }))

    latest_interruption = max(interruption_candidates, key=lambda item: item[0], default=None)
    cutoff = latest_interruption[0] if latest_interruption else None
    if latest_interruption and latest_interruption[1] == "ledger_gap":
        assert cutoff is not None
        window_times = [value for value in ruling_times if value >= cutoff]
    else:
        window_times = [value for value in ruling_times if cutoff is None or value > cutoff]
    start = window_times[0]
    end = window_times[-1]
    elapsed_seconds = (end - start).total_seconds()
    point_rate = (
        len(window_times) * 86400.0 / elapsed_seconds if elapsed_seconds > 0 else None)
    day_buckets = Counter(value.date().isoformat() for value in window_times)
    gaps_inside = [
        (current - previous).total_seconds()
        for previous, current in zip(ledger_times, ledger_times[1:])
        if previous >= start and current <= end
    ]

    return {
        "timestamp_evidence": "earliest mtime of an auto-review packet containing each payload",
        "attributed_unique_rulings": len(earliest_commit),
        "unattributed_unique_ruling_count": len(unattributed),
        "unattributed_payload_sha256": unattributed,
        "stop_count": len(set(stop_times)),
        "ledger_gap_over_30m_count": len(gap_interruptions),
        "latest_interruption": (
            {"type": latest_interruption[1], **latest_interruption[2]}
            if latest_interruption else None),
        "final_window": {
            "opens_at_utc": iso_utc(start),
            "closes_at_utc": iso_utc(end),
            "unique_rulings": len(window_times),
            "elapsed_seconds": elapsed_seconds,
            "elapsed_hours": elapsed_seconds / 3600.0,
            "point_rate_rulings_per_24_elapsed_hours": point_rate,
            "utc_calendar_day_buckets": dict(sorted(day_buckets.items())),
            "max_ledger_event_gap_seconds_inside_window": max(gaps_inside, default=None),
        },
        "configuration_selection_L90": None,
        "configuration_selection_L90_status": (
            "undefined_by_protocol: v2 requires a lower 90% interval over one continuous "
            "elapsed-time rate but specifies no sampling units or interval construction"),
    }


def cumulative_spend_report(
    *, v1_archive_dir: Path, v2_archive_dir: Path, canary_cap_usd: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    v1_events, v1_integrity = validate_usage_ledger(v1_archive_dir / "phase3_usage.jsonl")
    v2_events, v2_integrity = validate_usage_ledger(v2_archive_dir / "phase3_usage.jsonl")
    v1_summary = _summarize_usage_events(
        v1_events[1:], v1_archive_dir / "phase3_usage.jsonl", strict_lifecycle=True)
    v2_summary = _summarize_usage_events(
        v2_events[1:], v2_archive_dir / "phase3_usage.jsonl", strict_lifecycle=True)
    for summary in (v1_summary, v2_summary):
        for field in ("actual_spend_usd", "uncertain_spend_usd", "accounted_spend_usd"):
            summary[field] = round(float(summary[field]), 8)
    combined = round(
        v1_summary["accounted_spend_usd"] + v2_summary["accounted_spend_usd"], 8)
    return ({
        "v1_segment": v1_summary,
        "v2_segment": v2_summary,
        "combined_accounted_spend_usd": combined,
        "canary_family_cap_usd": canary_cap_usd,
        "headroom_usd": round(canary_cap_usd - combined, 8),
        "within_canary_family_cap": combined <= canary_cap_usd,
        "note": "The v1 and v2 segments share one continued canary-stage cap.",
    }, {"v1_usage": v1_integrity, "v2_usage": v2_integrity})


def build_review(
    *,
    archive_dir: Path = ARCHIVE_DIR_DEFAULT,
    v1_archive_dir: Path = V1_ARCHIVE_DIR_DEFAULT,
    protocol_path: Path = PROTOCOL_PATH_DEFAULT,
    manifest_path: Path = MANIFEST_PATH_DEFAULT,
    authorization_path: Path = AUTHORIZATION_PATH_DEFAULT,
    deferral_path: Path = DEFERRAL_PATH_DEFAULT,
    project_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    root = project_root.resolve()
    manifest_path = manifest_path.resolve()
    authorization_path = authorization_path.resolve()
    protocol_path = protocol_path.resolve()
    deferral_path = deferral_path.resolve()
    # The production validator records protocol paths relative to the repository. Entering the
    # supplied root preserves that exact binding while still allowing this script to be invoked
    # from any directory.
    with chdir(root):
        manifest = load_and_validate_manifest(manifest_path, project_root=Path("."))
        authorization = load_phase3_canary_authorization(authorization_path, manifest)
        protocol = phase3_plan.load_protocol(protocol_path)
        main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, Path("."))
        roster = list(manifest["roster"]["judges"])
        canary_cells = phase3_plan.enumerate_canary_cells(protocol, roster, held_out_ids)
        main_cells = phase3_plan.enumerate_cells(protocol, roster, main_ids)
        deferral = load_deferral_list(
            deferral_path, project_root=Path("."), protocol=protocol,
            plan_cells=cast(list[Mapping[str, Any]], canary_cells))
    if canonical_sha256(protocol) != manifest["frozen_inputs"]["protocol_sha256"]:
        raise ValueError("protocol does not match manifest binding")
    if Path(str(manifest["ledger"]["archive_dir"])) != archive_dir:
        raise ValueError("archive-dir argument does not match manifest ledger binding")

    deferred_judge = str(deferral["judge_model"])
    active_judges = [judge for judge in roster if judge != deferred_judge]

    result_rows, result_integrity = validate_result_store(
        archive_dir / "phase3_canary_results.jsonl")
    decision_rows, decision_integrity = validate_decision_store(
        archive_dir / "phase3_reviewer_decisions.jsonl")
    _cache_rows, cache_integrity = validate_call_cache(
        archive_dir / "phase3_call_cache.jsonl")

    completion = build_completion_report(
        canary_cells=canary_cells, main_cells=main_cells, result_rows=result_rows,
        deferred_keys=deferral["cell_keys"],
        carried_anchor_count=int(manifest["frozen_inputs"]["anchor_carry_cell_count"]))
    anchors = capability_anchor_report(manifest)
    calibration = build_calibration_report(
        result_rows=result_rows, canary_cells=canary_cells, protocol=protocol,
        active_judges=active_judges, deferred_judge=deferred_judge,
        held_out_ids=held_out_ids, question_bank=_load_question_bank())

    usage_events = _read_usage_events(archive_dir / "phase3_usage.jsonl")
    pace = compute_pace_window(
        decision_payloads=[str(row["payload_sha256"]) for row in decision_rows],
        packet_commits=collect_packet_commits(archive_dir),
        stop_times=parse_stop_times(
            (archive_dir / "orchestrator.log").read_text(encoding="utf-8")),
        usage_event_times=[
            parse_timestamp(str(event["ts"])) for event in usage_events if event.get("ts")],
    )
    spend, usage_integrity = cumulative_spend_report(
        v1_archive_dir=v1_archive_dir, v2_archive_dir=archive_dir,
        canary_cap_usd=float(authorization["scope"]["canary_cap_usd"]))

    gate_blockers: list[str] = []
    if not completion["provisional_completion_gate_pass"]:
        gate_blockers.append("v2 provisional completion accounting failed")
    if not calibration["all_structural_gates_pass"]:
        gate_blockers.append("rendered structural mirroring gate failed")
    for judge in calibration["continuing_judge_failures"]:
        gate_blockers.append(f"continuing judge strict-invalid gate failed: {judge}")
    if pace["configuration_selection_L90"] is None:
        gate_blockers.append("configuration-selection continuous-rate L90 is underspecified")

    return {
        "schema_version": "phase3_v2_canary_review_v1",
        "generated_by": "scripts/phase3_canary_closeout_v2.py",
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "protocol_canonical_sha256": manifest["frozen_inputs"]["protocol_sha256"],
        "archive_dir": archive_dir.as_posix(),
        "read_only_derivation": True,
        "archive_integrity": {
            "manifest_validation": "pass",
            "authorization_validation": "pass",
            "deferral_binding_validation": "pass",
            "result_store": result_integrity,
            "decision_store": decision_integrity,
            "call_cache": cache_integrity,
            **usage_integrity,
        },
        "completion": completion,
        "capability_anchors": anchors,
        "calibration": calibration,
        "review_pace": pace,
        "spend_to_date": spend,
        "configuration_selection": {
            "ready": False,
            "R_U": None,
            "D_U": None,
            "main_spend_forecast_usd": None,
            "reason": (
                "No configuration or cap selection is issued while a continuing-judge gate "
                "fails. The historical v1 close-out is also not reused because it applies the "
                "retired side-bias gate, calendar-day pace interval, success-only role samples, "
                "chars-per-four token estimates, and v2-only accrued spend."),
        },
        "main_authorization_ready": False,
        "binding_disposition": "halt_and_escalate_to_owner",
        "gate_blockers": gate_blockers,
        "recommended_next_step": (
            "Keep the gpt-oss gate failure binding. Do not waive or rerun it. Approve a "
            "successor roster design and a corrected exact-token forecast contract before "
            "any new paid calibration or main-run authorization."),
        "non_claims": [
            "No phase-3 main outcome or treatment-efficacy analysis was performed.",
            "The continuous pace point rate is not an L90 confidence bound.",
            "No main-run configuration or spend forecast is certified by this artifact.",
            "No provider call or archive mutation was performed by this derivation.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, default=ARCHIVE_DIR_DEFAULT)
    parser.add_argument("--v1-archive-dir", type=Path, default=V1_ARCHIVE_DIR_DEFAULT)
    parser.add_argument("--protocol-path", type=Path, default=PROTOCOL_PATH_DEFAULT)
    parser.add_argument("--manifest-path", type=Path, default=MANIFEST_PATH_DEFAULT)
    parser.add_argument("--authorization-path", type=Path, default=AUTHORIZATION_PATH_DEFAULT)
    parser.add_argument("--deferral-path", type=Path, default=DEFERRAL_PATH_DEFAULT)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--out", type=Path, default=OUTPUT_PATH_DEFAULT)
    args = parser.parse_args(argv)

    artifact = build_review(
        archive_dir=args.archive_dir,
        v1_archive_dir=args.v1_archive_dir,
        protocol_path=args.protocol_path,
        manifest_path=args.manifest_path,
        authorization_path=args.authorization_path,
        deferral_path=args.deferral_path,
        project_root=args.project_root,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(artifact, indent=1, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    print(
        "main_authorization_ready="
        f"{str(artifact['main_authorization_ready']).lower()} "
        f"blockers={len(artifact['gate_blockers'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
