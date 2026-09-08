"""Fail-closed finalization and exclusion admission for the Phase 3 main run.

This module is deliberately provider-free.  It validates the durable artifacts produced by
one main identity and emits the only record that may admit terminal or context-ineligible
keys to the confirmatory analysis.

Terminal dispositions are narrower than the successor-canary mechanism.  The main request
journal removes replay divergence; admitted reasons are ``checker_malformed`` and the
strict checker decision ``checker_unresolved``, sharing the same cumulative bounds.
The disposition store never trusts a hand-written explanation: it validates the complete
usage-ledger and request-journal chains, joins one successful query-checker attempt to its
journaled response, and reruns the frozen strict checker parser.  Valid ``allow`` and
``reject`` decisions cannot be disposed.  Unresolved cells count as strict INVALID.

The finalization gate additionally requires the exact 492-transcript plus 9,840-judgment
partition, the frozen cumulative terminal bounds, a checker-truncation diagnostic, immutable
artifact hashes, and an empty final ledger/journal reconciliation.  It performs no analysis
and grants no execution or spend authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from rejudge import (
    api_client,
    oracle_channel,
    phase3_main_manifest,
    phase3_main_provider_provenance,
    phase3_main_reviewer_provenance,
    phase3_main_transcript_provenance,
    phase3_plan,
)
from rejudge.config import ARMS, judgment_seed, position_for
from rejudge.parsers import PARSER_VERSION, parse_both
from rejudge.phase2_call_cache import request_fingerprint
from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
from rejudge.phase2_execution import canonical_sha256
from rejudge.phase2_query_gate import (
    CheckerDecision, MalformedCheckerOutput, parse_checker_output,
)
from rejudge.phase3_main_runner import (
    EXPECTED_MAIN_CELL_COUNT,
    EXPECTED_MAIN_INVENTORY_SHA256,
    EXPECTED_MAIN_JUDGMENT_COUNT,
    EXPECTED_MAIN_TRANSCRIPT_COUNT,
    MainInventory,
)
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD,
    RequestJournal,
    find_ambiguous_dispatches,
    journal_key,
)


TERMINAL_SCHEMA = "phase3_main_terminal_disposition_v1"
FINALIZATION_SCHEMA = "phase3_main_finalization_admission_v3"
FINALIZATION_STATUS = "admitted_for_confirmatory_analysis"
TERMINAL_REASON = "checker_malformed"
UNRESOLVED_TERMINAL_REASON = "checker_unresolved"
TERMINAL_REASONS = frozenset({TERMINAL_REASON, UNRESOLVED_TERMINAL_REASON})
QUERY_CHECKER_ROLE = "query_checker"
JUDGE_QUERY_ROLE = "judge_query"
ORACLE_VERIFICATION_ROLE = "oracle_verification"
JUDGE_VERDICT_ROLE = "judge_verdict"
ALLOWED_MAIN_CALL_ROLES = frozenset({
    JUDGE_QUERY_ROLE,
    QUERY_CHECKER_ROLE,
    ORACLE_VERIFICATION_ROLE,
    JUDGE_VERDICT_ROLE,
})
PRE_VERDICT_MAIN_CALL_ROLES = frozenset({
    JUDGE_QUERY_ROLE,
    QUERY_CHECKER_ROLE,
    ORACLE_VERIFICATION_ROLE,
})
DEFAULT_CHECKER_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
MAIN_ARM_NAME = "clean"
MAIN_VERDICT_TEMPERATURE = 0.3
MAIN_VERDICT_MAX_TOKENS = 512
MAIN_JUDGMENT_REPLICATES_PER_SIDE = 1

MAX_TERMINAL_JUDGMENT_CELLS = 20
MAX_AFFECTED_MIRROR_UNIT_FRACTION = 0.04
CONCENTRATION_MIN_CELLS = 5
CONCENTRATION_RATE = 0.02
CONCENTRATION_RATIO = 3.0

FROZEN_ANALYSIS_PINS_CANONICAL_SHA256 = (
    "6e8d607d0a4a5d9ff24f4a729d3dbfa9ab27d5d862bcc0fcebf1da03fc0a0a77"
)

TERMINAL_ROW_FIELDS = frozenset({
    "schema_version",
    "run_id",
    "manifest_canonical_sha256",
    "inventory_canonical_sha256",
    "cell_key",
    "reason",
    "evidence",
    "recorded_at_utc",
    "sequence",
    "prev_event_hash",
    "event_hash",
})
TERMINAL_EVIDENCE_FIELDS = frozenset({
    "ledger_attempt_id",
    "ledger_reserved_sequence",
    "ledger_terminal_sequence",
    "ledger_terminal_event_sha256",
    "journal_sequence",
    "journal_event_sha256",
    "journal_request_sha256",
    "checker_response_sha256",
    "checker_model",
    "finish_reason",
    "prompt_tokens",
    "completion_tokens",
    "parse_failure",
})
UNRESOLVED_TERMINAL_EVIDENCE_FIELDS = (
    TERMINAL_EVIDENCE_FIELDS - {"parse_failure"} | {"checker_decision"})
FINALIZATION_FIELDS = frozenset({
    "schema_version",
    "stage",
    "status",
    "run_id",
    "recorded_at_utc",
    "execution_authorized",
    "provider_calls_authorized",
    "main_run_spend_authorized",
    "bindings",
    "terminal_store",
    "partition",
    "terminal_bounds",
    "checker_truncation_diagnostic",
    "provider_provenance",
    "reviewer_provenance",
    "accounting",
    "artifact_hashes",
    "reconciliation",
    "uncertain_spend_policy",
    "retry_report",
})
BINDING_FIELDS = frozenset({
    "manifest_canonical_sha256",
    "authorization_canonical_sha256",
    "authorization_raw_sha256",
    "authorization_signature_raw_sha256",
    "inventory_canonical_sha256",
    "context_blocklist_raw_sha256",
    "analysis_pins_canonical_sha256",
    "artifact_root",
    "artifact_root_sha256",
    "journal_execution_identity",
})
TERMINAL_TAIL_FIELDS = frozenset({
    "path",
    "raw_sha256",
    "record_count",
    "last_sequence",
    "last_event_hash",
})
ARTIFACT_BINDING_FIELDS = frozenset({"path", "raw_sha256", "bytes"})
REQUIRED_ARTIFACTS = frozenset({
    "result_store",
    "usage_ledger",
    "usage_ledger_state",
    "request_journal",
    "review_decisions",
    "reviewer_index",
    "reviewer_worklist",
    "provider_error_log",
    "run_log",
    "context_blocklist",
    "terminal_dispositions",
    "analysis_pins",
})
FINALIZATION_ARTIFACT_TO_MANIFEST_OUTPUT = {
    "result_store": "results",
    "usage_ledger": "usage_ledger",
    "request_journal": "request_journal",
    "review_decisions": "decisions",
    "reviewer_index": "reviewer_index",
    "reviewer_worklist": "reviewer_worklist",
    "provider_error_log": "provider_error_log",
    "run_log": "run_log",
    "terminal_dispositions": "terminal_dispositions",
}
RECONCILIATION_FIELDS = frozenset({
    "status",
    "ambiguous_dispatches",
    "unmatched_reservations",
    "unknown_charge_attempt_ids",
    "charged_malformed_attempt_ids",
    "settled_success_without_journal_attempt_ids",
    "unresolved_dispatch_marker_present",
})
PROVIDER_PROVENANCE_FIELDS = frozenset({
    "status",
    "allowed_main_call_roles",
    "observed_judgment_count",
    "journaled_call_count",
    "settled_success_call_count",
    "verdict_journal_count",
    "verdict_ledger_success_count",
    "result_store_raw_sha256",
    "request_journal_raw_sha256",
    "usage_ledger_raw_sha256",
    "normal_execution_replay_status",
    "replayed_judgment_count",
    "replayed_terminal_count",
    "logical_request_count",
    "provider_request_count",
    "redispatched_logical_call_count",
    "unknown_charge_episode_count",
    "unknown_charge_episodes_by_model",
    "logical_request_hashes_sha256",
    "provider_request_hashes_sha256",
    "authorization_dispatch_status",
    "latest_logical_dispatch_at_utc",
    "latest_provider_completion_at_utc",
    "protocol_raw_sha256",
    "prompt_bundle_raw_sha256",
    "role_limits_raw_sha256",
    "main_transcript_bundle_raw_sha256",
    "transcript_verification_raw_sha256",
    "main_transcript_bundle_canonical_sha256",
    "transcript_results_canonical_sha256",
})
PROVIDER_INPUT_FIELDS = frozenset({
    "protocol",
    "prompt_bundle",
    "role_limits",
    "main_transcript_bundle",
    "transcript_verification",
})
REVIEWER_INPUT_FIELDS = frozenset({
    "reviewer_prompt",
    "reviewer_failure_policy",
    "capacity_plan",
})
CAPACITY_EVIDENCE_FIELDS = frozenset({
    "capacity_result",
    "capacity_dispatch_history",
})
REVIEWER_PROVENANCE_FIELDS = frozenset({
    "reviewer_provenance_status",
    "reviewer_wave_count",
    "reviewed_payload_count",
    "parsed_decision_count",
    "malformed_decision_count",
    "reviewer_error_decision_count",
    "review_packets_root",
    "review_packets_tree_canonical_sha256",
    "reviewer_index_raw_sha256",
    "reviewer_worklist_raw_sha256",
    "reviewer_prompt_raw_sha256",
    "reviewer_failure_policy_raw_sha256",
    "capacity_plan_raw_sha256",
    "capacity_result_raw_sha256",
    "capacity_dispatch_history_raw_sha256",
})
ACCOUNTING_FIELDS = frozenset({
    "prior_reconciled_usd",
    "voided_predecessor_usd",
    "current_settled_usd",
    "current_uncertain_usd",
    "current_accounted_usd",
    "stage_total_usd",
    "stage_cap_usd",
    "within_stage_cap",
    "usage_ledger_raw_sha256",
    "usage_ledger_state_raw_sha256",
    "usage_ledger_id",
    "usage_ledger_tail_sequence",
    "usage_ledger_tail_event_hash",
    "completion_label",
    "run_uncertain_ceiling_usd",
    "uncertain_within_ceiling",
    "unknown_charge_attempt_count",
    "unknown_charge_by_model",
})
ROOT_BOUND_ARTIFACTS = frozenset({
    "result_store",
    "usage_ledger",
    "usage_ledger_state",
    "request_journal",
    "review_decisions",
    "reviewer_index",
    "reviewer_worklist",
    "provider_error_log",
    "run_log",
    "terminal_dispositions",
})
_EXACT_NON_NEGATIVE_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


class MainFinalizationError(ValueError):
    """A main finalization artifact or invariant failed closed."""


class TerminalDispositionError(MainFinalizationError):
    """A terminal disposition was not mechanically admissible."""


class MainPartitionError(MainFinalizationError):
    """Results, terminal cells, and context exclusions do not partition the plan."""


class TerminalBoundError(MainFinalizationError):
    """A frozen cumulative terminal bound was crossed."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256(value: Any, label: str) -> str:
    if not _is_sha256(value):
        raise MainFinalizationError(f"{label} must be a lowercase SHA-256 digest")
    return str(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise MainFinalizationError(f"{label} must be a non-empty exact string")
    return value


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MainFinalizationError(f"{label} must be a non-negative integer")
    return value


def _positive_int(value: Any, label: str) -> int:
    parsed = _non_negative_int(value, label)
    if parsed == 0:
        raise MainFinalizationError(f"{label} must be positive")
    return parsed


def _utc(value: Any, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise MainFinalizationError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MainFinalizationError(f"{label} must use UTC")
    return text


def _utc_datetime(value: Any, label: str) -> datetime:
    text = _utc(value, label)
    return datetime.fromisoformat(
        text[:-1] + "+00:00" if text.endswith("Z") else text)


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MainFinalizationError(f"{label} must be an object")
    observed = set(value)
    if observed != expected:
        raise MainFinalizationError(
            f"{label} fields drifted: missing={sorted(expected - observed)!r}, "
            f"unexpected={sorted(observed - expected)!r}")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MainFinalizationError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path, label: str) -> tuple[Mapping[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MainFinalizationError(f"could not read valid {label}: {path}") from exc
    if not isinstance(payload, Mapping):
        raise MainFinalizationError(f"{label} must be a JSON object: {path}")
    return payload, raw


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MainFinalizationError(f"could not read {label}: {path}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise MainFinalizationError(
                f"{label} has a blank row at {path}:line {line_number}")
        try:
            row = json.loads(line, object_pairs_hook=_unique_object)
        except json.JSONDecodeError as exc:
            raise MainFinalizationError(
                f"{label} has invalid JSON at {path}:line {line_number}") from exc
        if not isinstance(row, dict):
            raise MainFinalizationError(
                f"{label} row {line_number} must be an object")
        rows.append(row)
    return rows


def _json_bytes(
    raw: bytes, label: str, *, exact_numbers: bool = False,
) -> Any:
    try:
        options: dict[str, Any] = {"object_pairs_hook": _unique_object}
        if exact_numbers:
            options["parse_float"] = Decimal
        return json.loads(raw.decode("utf-8"), **options)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MainFinalizationError(f"{label} is not valid UTF-8 JSON") from exc


def _jsonl_bytes(
    raw: bytes, label: str, *, exact_numbers: bool = False,
) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise MainFinalizationError(f"{label} is not valid UTF-8") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise MainFinalizationError(
                f"{label} has a blank row at line {line_number}")
        try:
            options: dict[str, Any] = {"object_pairs_hook": _unique_object}
            if exact_numbers:
                options["parse_float"] = Decimal
            row = json.loads(line, **options)
        except json.JSONDecodeError as exc:
            raise MainFinalizationError(
                f"{label} has invalid JSON at line {line_number}") from exc
        if not isinstance(row, dict):
            raise MainFinalizationError(
                f"{label} row {line_number} must be an object")
        rows.append(row)
    return rows


def _exact_decimal(value: Any, label: str) -> tuple[str, Decimal]:
    if (not isinstance(value, str)
            or _EXACT_NON_NEGATIVE_DECIMAL.fullmatch(value) is None):
        raise MainFinalizationError(
            f"{label} must be an exact non-negative fixed-point Decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:  # pragma: no cover - grammar already excludes it
        raise MainFinalizationError(f"{label} is not a valid Decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise MainFinalizationError(f"{label} is not a valid non-negative Decimal")
    return value, amount


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _exact_event_cost(event: Mapping[str, Any], label: str) -> Decimal:
    value = event.get("cost_usd")
    if isinstance(value, bool):
        raise MainFinalizationError(f"{label}.cost_usd is invalid")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif (isinstance(value, str)
          and _EXACT_NON_NEGATIVE_DECIMAL.fullmatch(value) is not None):
        amount = Decimal(value)
    else:
        raise MainFinalizationError(f"{label}.cost_usd is not exact numeric JSON")
    if not amount.is_finite() or amount < 0:
        raise MainFinalizationError(f"{label}.cost_usd is invalid")
    return amount


def _read_stable_bytes(path: Path, label: str) -> bytes:
    try:
        first = path.read_bytes()
        second = path.read_bytes()
    except OSError as exc:
        raise MainFinalizationError(f"could not read {label}: {path}") from exc
    if first != second:
        raise MainFinalizationError(f"{label} changed while finalization read it")
    return first


def _load_bound_json_inputs(
    paths: Mapping[str, str | Path],
    raw_sha256s: Mapping[str, str],
    *,
    expected_fields: frozenset[str],
    group_label: str,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, bytes]]:
    if set(paths) != expected_fields:
        raise MainFinalizationError(
            f"{group_label} input paths drifted: "
            f"missing={sorted(expected_fields - set(paths))!r}, "
            f"unexpected={sorted(set(paths) - expected_fields)!r}")
    if set(raw_sha256s) != expected_fields:
        raise MainFinalizationError(
            f"{group_label} input hashes drifted: "
            f"missing={sorted(expected_fields - set(raw_sha256s))!r}, "
            f"unexpected={sorted(set(raw_sha256s) - expected_fields)!r}")
    values: dict[str, Mapping[str, Any]] = {}
    raw_values: dict[str, bytes] = {}
    for name in sorted(expected_fields):
        raw_path = paths[name]
        if not isinstance(raw_path, (str, Path)):
            raise MainFinalizationError(
                f"{group_label} input path {name!r} must be a path")
        path = Path(raw_path)
        if not path.is_absolute():
            raise MainFinalizationError(
                f"{group_label} input path {name!r} must be absolute")
        expected_sha = _sha256(
            raw_sha256s[name], f"{group_label} input {name} raw SHA-256")
        raw = _read_stable_bytes(path.resolve(), f"{group_label} input {name}")
        if _sha256_bytes(raw) != expected_sha:
            raise MainFinalizationError(
                f"{group_label} input {name} differs from its manifest binding")
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise MainFinalizationError(
                f"{group_label} input {name} is not unique-key UTF-8 JSON") from exc
        if not isinstance(value, Mapping):
            raise MainFinalizationError(
                f"{group_label} input {name} must be a JSON object")
        values[name] = value
        raw_values[name] = raw
    return values, raw_values


def _load_bound_provider_inputs(
    paths: Mapping[str, str | Path],
    raw_sha256s: Mapping[str, str],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, bytes]]:
    return _load_bound_json_inputs(
        paths,
        raw_sha256s,
        expected_fields=PROVIDER_INPUT_FIELDS,
        group_label="provider provenance",
    )


def _load_bound_reviewer_inputs(
    paths: Mapping[str, str | Path],
    raw_sha256s: Mapping[str, str],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, bytes]]:
    return _load_bound_json_inputs(
        paths,
        raw_sha256s,
        expected_fields=REVIEWER_INPUT_FIELDS,
        group_label="reviewer provenance",
    )


def _require_bound_inputs_unchanged(
    paths: Mapping[str, str | Path],
    raw_values: Mapping[str, bytes],
    *,
    expected_fields: frozenset[str],
    group_label: str,
) -> None:
    for name in sorted(expected_fields):
        path = Path(paths[name]).resolve()
        try:
            observed = path.read_bytes()
        except OSError as exc:
            raise MainFinalizationError(
                f"{group_label} input {name} became unreadable") from exc
        if observed != raw_values[name]:
            raise MainFinalizationError(
                f"{group_label} input {name} changed during finalization")


def _require_bound_provider_inputs_unchanged(
    paths: Mapping[str, str | Path], raw_values: Mapping[str, bytes],
) -> None:
    _require_bound_inputs_unchanged(
        paths,
        raw_values,
        expected_fields=PROVIDER_INPUT_FIELDS,
        group_label="provider provenance",
    )


def _require_bound_reviewer_inputs_unchanged(
    paths: Mapping[str, str | Path], raw_values: Mapping[str, bytes],
) -> None:
    _require_bound_inputs_unchanged(
        paths,
        raw_values,
        expected_fields=REVIEWER_INPUT_FIELDS,
        group_label="reviewer provenance",
    )


def _load_bound_capacity_evidence(
    *,
    capacity_result_path: str | Path,
    expected_capacity_result_raw_sha256: str,
    capacity_dispatch_history_path: str | Path,
    expected_capacity_dispatch_history_raw_sha256: str,
) -> tuple[dict[str, Path], dict[str, bytes]]:
    paths = {
        "capacity_result": capacity_result_path,
        "capacity_dispatch_history": capacity_dispatch_history_path,
    }
    hashes = {
        "capacity_result": expected_capacity_result_raw_sha256,
        "capacity_dispatch_history": expected_capacity_dispatch_history_raw_sha256,
    }
    resolved: dict[str, Path] = {}
    raw_values: dict[str, bytes] = {}
    for name in sorted(CAPACITY_EVIDENCE_FIELDS):
        raw_path = paths[name]
        if not isinstance(raw_path, (str, Path)):
            raise MainFinalizationError(
                f"capacity evidence path {name!r} must be a path")
        path = Path(raw_path)
        if not path.is_absolute():
            raise MainFinalizationError(
                f"capacity evidence path {name!r} must be absolute")
        expected_sha = _sha256(
            hashes[name], f"capacity evidence {name} raw SHA-256")
        resolved_path = path.resolve()
        raw = _read_stable_bytes(resolved_path, f"capacity evidence {name}")
        if _sha256_bytes(raw) != expected_sha:
            raise MainFinalizationError(
                f"capacity evidence {name} differs from its manifest binding")
        resolved[name] = resolved_path
        raw_values[name] = raw
    return resolved, raw_values


def _require_bound_capacity_evidence_unchanged(
    paths: Mapping[str, str | Path], raw_values: Mapping[str, bytes],
) -> None:
    _require_bound_inputs_unchanged(
        paths,
        raw_values,
        expected_fields=CAPACITY_EVIDENCE_FIELDS,
        group_label="capacity evidence",
    )


def _reviewer_max_passes(capacity_plan: Mapping[str, Any]) -> int:
    try:
        reviewer = capacity_plan["reviewer_configuration"]
        workload = capacity_plan["workload"]
        thresholds = capacity_plan["capacity_thresholds"]
        wave_size = reviewer["actual_capacity_wave_size"]
        pending_limit = reviewer["wave_pending_payload_limit"]
        measured_wave_size = workload["wave_size"]
        maximum_payloads = thresholds["maximum_unique_review_payloads_zero_dedup"]
    except (KeyError, TypeError) as exc:
        raise MainFinalizationError(
            "capacity plan omits the reviewer loop contract") from exc
    for label, value in (
        ("reviewer wave size", wave_size),
        ("reviewer pending limit", pending_limit),
        ("reviewer measured wave size", measured_wave_size),
        ("maximum reviewer payload count", maximum_payloads),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise MainFinalizationError(f"{label} must be an integer")
    if wave_size <= 0 or wave_size > pending_limit or wave_size != measured_wave_size:
        raise MainFinalizationError(
            "capacity plan reviewer wave contract is inconsistent")
    if maximum_payloads < 0:
        raise MainFinalizationError(
            "capacity plan maximum reviewer payload count is negative")
    return (
        math.ceil(maximum_payloads / wave_size)
        + MAX_TERMINAL_JUDGMENT_CELLS
        + 2
    )


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def inventory_canonical_sha256(inventory: MainInventory) -> str:
    """Return the canonical digest used by the main-runner inventory contract."""
    payload = {
        "question_ids": list(inventory.question_ids),
        "judges": list(inventory.judges),
        "cells": [_thaw(cell) for cell in inventory.cells],
    }
    return _sha256_text(_canonical_json(payload))


def _inventory_index(
    inventory: MainInventory,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    if not isinstance(inventory, MainInventory):
        raise MainFinalizationError("inventory must be a validated MainInventory")
    digest = inventory_canonical_sha256(inventory)
    if digest != EXPECTED_MAIN_INVENTORY_SHA256:
        raise MainFinalizationError(
            "main inventory differs from the exact confirmed inventory: "
            f"{digest} != {EXPECTED_MAIN_INVENTORY_SHA256}")
    cells = {_text(cell.get("cell_key"), "inventory cell_key"): cell
             for cell in inventory.cells}
    if len(cells) != EXPECTED_MAIN_CELL_COUNT:
        raise MainFinalizationError("main inventory cell keys are not exact and unique")
    transcripts = {
        key: cell for key, cell in cells.items()
        if cell.get("kind") == phase3_plan.MAIN_TRANSCRIPT_KIND
    }
    judgments = {
        key: cell for key, cell in cells.items()
        if cell.get("kind") == phase3_plan.MAIN_JUDGMENT_KIND
    }
    if (len(transcripts) != EXPECTED_MAIN_TRANSCRIPT_COUNT
            or len(judgments) != EXPECTED_MAIN_JUDGMENT_COUNT
            or len(transcripts) + len(judgments) != len(cells)):
        raise MainFinalizationError(
            "main inventory is not exactly 492 transcripts plus 9,840 judgments")
    return transcripts, judgments


def _cell_key_sequence(value: Iterable[str], label: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise MainFinalizationError(f"{label} must be an iterable of cell keys")
    keys = tuple(_text(key, f"{label} item") for key in value)
    if len(keys) != len(set(keys)):
        raise MainFinalizationError(f"{label} contains duplicate cell keys")
    return keys


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _terminal_seed(
    *, run_id: str, manifest_sha256: str, inventory_sha256: str,
) -> str:
    return _sha256_text(_canonical_json({
        "schema_version": TERMINAL_SCHEMA,
        "run_id": run_id,
        "manifest_canonical_sha256": manifest_sha256,
        "inventory_canonical_sha256": inventory_sha256,
    }))


def _terminal_row_hash(row: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "event_hash"}
    return _sha256_text(_canonical_json(payload))


def _validate_evidence_shape(
    evidence: Any, label: str, *, reason: str,
) -> Mapping[str, Any]:
    evidence = _exact_keys(
        evidence,
        (UNRESOLVED_TERMINAL_EVIDENCE_FIELDS
         if reason == UNRESOLVED_TERMINAL_REASON else TERMINAL_EVIDENCE_FIELDS),
        label,
    )
    if reason == UNRESOLVED_TERMINAL_REASON:
        if evidence["checker_decision"] != CheckerDecision.UNRESOLVED.value:
            raise TerminalDispositionError(
                f"{label} checker decision must be exactly unresolved")
    else:
        _text(evidence["parse_failure"], f"{label}.parse_failure")
    for field in (
        "ledger_attempt_id",
        "checker_model",
        "finish_reason",
    ):
        _text(evidence[field], f"{label}.{field}")
    for field in (
        "ledger_terminal_event_sha256",
        "journal_event_sha256",
        "journal_request_sha256",
        "checker_response_sha256",
    ):
        _sha256(evidence[field], f"{label}.{field}")
    for field in (
        "ledger_reserved_sequence",
        "ledger_terminal_sequence",
        "journal_sequence",
        "prompt_tokens",
        "completion_tokens",
    ):
        _non_negative_int(evidence[field], f"{label}.{field}")
    if evidence["ledger_reserved_sequence"] >= evidence["ledger_terminal_sequence"]:
        raise TerminalDispositionError(
            f"{label} terminal event must follow its reservation")
    return evidence


def _journal_rows(
    path: Path, *, execution_identity: str,
) -> list[dict[str, Any]]:
    # Construction validates the complete identity-seeded chain before bytes are reused.
    raw_before = _read_stable_bytes(path, "request journal")
    try:
        RequestJournal(path, execution_identity=execution_identity)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise MainFinalizationError(
            "request journal identity or hash chain is invalid") from exc
    raw_after = _read_stable_bytes(path, "request journal")
    if raw_before != raw_after:
        raise MainFinalizationError(
            "request journal changed while finalization validated it")
    return _jsonl_bytes(raw_before, "request journal")


def _load_chained_usage_ledger_readonly(path: str | Path) -> api_client.UsageLedgerSnapshot:
    """Validate one stable ledger and state tail without repairing either file."""
    ledger_path = Path(path).resolve()
    events = api_client._read_usage_events(ledger_path)  # noqa: SLF001
    try:
        identity, hashes = api_client._validate_usage_chain(  # noqa: SLF001
            events, ledger_path)
        state_path = api_client.usage_ledger_state_path(ledger_path)
        state = api_client._read_usage_state(state_path)  # noqa: SLF001
        if (
            state["ledger_id"] != identity["ledger_id"]
            or state["last_sequence"] != len(hashes) - 1
            or state["last_event_hash"] != hashes[-1]
        ):
            raise api_client.UsageLedgerError(
                "ledger state is not at the exact immutable ledger tail")
        summary = api_client._summarize_usage_events(  # noqa: SLF001
            events[1:], ledger_path, strict_lifecycle=True)
    except (api_client.UsageLedgerError, IndexError, KeyError, TypeError) as exc:
        raise MainFinalizationError(
            f"usage ledger is not a stable read-only final snapshot: {ledger_path}") from exc
    return api_client.UsageLedgerSnapshot(
        path=ledger_path,
        state_path=state_path,
        identity=identity,
        summary=summary,
        last_sequence=len(hashes) - 1,
        last_event_hash=hashes[-1],
    )


def _load_stable_usage_ledger_material(path: str | Path) -> dict[str, Any]:
    """Return chain-validated events and exact costs from unchanged ledger bytes."""
    ledger_path = Path(path).resolve()
    state_path = api_client.usage_ledger_state_path(ledger_path).resolve()
    ledger_raw_before = _read_stable_bytes(ledger_path, "usage ledger")
    state_raw_before = _read_stable_bytes(state_path, "usage ledger state")
    snapshot = _load_chained_usage_ledger_readonly(ledger_path)
    events = api_client._read_usage_events(ledger_path)  # noqa: SLF001
    ledger_raw_after = _read_stable_bytes(ledger_path, "usage ledger")
    state_raw_after = _read_stable_bytes(state_path, "usage ledger state")
    if ledger_raw_before != ledger_raw_after or state_raw_before != state_raw_after:
        raise MainFinalizationError(
            "usage ledger or state changed while finalization validated it")

    exact_events = _jsonl_bytes(
        ledger_raw_before, "usage ledger", exact_numbers=True)
    if (len(exact_events) != len(events)
            or [row.get("event_hash") for row in exact_events]
            != [row.get("event_hash") for row in events]):
        raise MainFinalizationError(
            "exact usage-ledger parse differs from the validated event chain")
    state = _json_bytes(state_raw_before, "usage ledger state")
    if not isinstance(state, Mapping):
        raise MainFinalizationError("usage ledger state must be an object")
    if (state.get("ledger_id") != snapshot.identity["ledger_id"]
            or state.get("last_sequence") != snapshot.last_sequence
            or state.get("last_event_hash") != snapshot.last_event_hash):
        raise MainFinalizationError(
            "usage ledger state differs from the validated immutable tail")
    return {
        "snapshot": snapshot,
        "events": events,
        "exact_events": exact_events,
        "ledger_raw": ledger_raw_before,
        "state_raw": state_raw_before,
        "ledger_raw_sha256": _sha256_bytes(ledger_raw_before),
        "state_raw_sha256": _sha256_bytes(state_raw_before),
    }


def _exact_current_ledger_totals(
    exact_events: Sequence[Mapping[str, Any]],
) -> tuple[Decimal, Decimal]:
    reservations: dict[str, Decimal] = {}
    settled = Decimal("0")
    uncertain = Decimal("0")
    for event_number, event in enumerate(exact_events[1:], 1):
        status = event.get("status")
        attempt_id = event.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise MainFinalizationError(
                f"usage ledger event {event_number} has no exact attempt identity")
        cost = _exact_event_cost(event, f"usage ledger event {event_number}")
        if status == "reserved":
            reservations[attempt_id] = cost
        elif status in {"success", "charged_malformed"}:
            settled += cost
            reservations.pop(attempt_id, None)
        elif status == "unknown_charge":
            uncertain += cost
            reservations.pop(attempt_id, None)
        elif status == "released_no_charge":
            reservations.pop(attempt_id, None)
        else:  # pragma: no cover - the read-only lifecycle validator rejects this first
            raise MainFinalizationError(
                f"usage ledger event {event_number} has an unsupported status")
    uncertain += sum(reservations.values(), Decimal("0"))
    return settled, uncertain


def _accounting_section(
    material: Mapping[str, Any], *, prior_reconciled_usd: Any,
    stage_cap_usd: Any,
    uncertain_spend_policy: Mapping[str, Any] | None = None,
    voided_predecessor_usd: Any = "0",
) -> dict[str, Any]:
    prior_text, prior = _exact_decimal(
        prior_reconciled_usd, "prior_reconciled_usd")
    # Amendment 15: a voided predecessor identity's accounted spend is stage expenditure
    # outside the reconciled segments and counts toward the cap below.
    voided_text, voided = _exact_decimal(
        voided_predecessor_usd, "voided_predecessor_usd")
    cap_text, cap = _exact_decimal(stage_cap_usd, "stage_cap_usd")
    exact_events = material.get("exact_events")
    if not isinstance(exact_events, Sequence):
        raise MainFinalizationError("exact usage-ledger events are missing")
    settled, uncertain = _exact_current_ledger_totals(exact_events)
    if uncertain_spend_policy is None:
        if uncertain != 0:
            raise MainFinalizationError(
                "current main usage ledger has nonzero uncertain spend")
        ceiling_text = None
    else:
        # Amendment 14: uncertain spend is bounded by the frozen per-identity ceiling and
        # still counts fully toward the stage cap below.
        _ceiling_text, ceiling = _exact_decimal(
            format(Decimal(str(uncertain_spend_policy["run_uncertain_ceiling_usd"])), "f"),
            "run_uncertain_ceiling_usd")
        if uncertain > ceiling:
            raise MainFinalizationError(
                "current main uncertain spend exceeds the frozen policy ceiling")
        ceiling_text = _decimal_text(ceiling)
    unknown_counts: dict[str, int] = {}
    unknown_sums: dict[str, Decimal] = {}
    for event_number, event in enumerate(exact_events[1:], 1):
        if event.get("status") != "unknown_charge":
            continue
        model = _text(event.get("model"), f"usage ledger event {event_number}.model")
        unknown_counts[model] = unknown_counts.get(model, 0) + 1
        unknown_sums[model] = unknown_sums.get(model, Decimal("0")) + _exact_event_cost(
            event, f"usage ledger event {event_number}")
    unknown_by_model = {
        model: {
            "events": unknown_counts[model],
            "uncertain_usd": _decimal_text(unknown_sums[model]),
        }
        for model in sorted(unknown_counts)
    }
    accounted = settled + uncertain
    stage_total = prior + voided + accounted
    if stage_total > cap:
        raise MainFinalizationError(
            "prior reconciled plus voided predecessor plus current main spend exceeds "
            "the stage cap")
    snapshot = material.get("snapshot")
    if not isinstance(snapshot, api_client.UsageLedgerSnapshot):
        raise MainFinalizationError("validated usage-ledger snapshot is missing")
    return {
        "prior_reconciled_usd": prior_text,
        "voided_predecessor_usd": voided_text,
        "current_settled_usd": _decimal_text(settled),
        "current_uncertain_usd": _decimal_text(uncertain),
        "current_accounted_usd": _decimal_text(accounted),
        "stage_total_usd": _decimal_text(stage_total),
        "stage_cap_usd": cap_text,
        "within_stage_cap": True,
        "completion_label": (
            "PASS_CLEAN" if uncertain == 0 else "PASS_CONSERVATIVE_UNCERTAIN"),
        "run_uncertain_ceiling_usd": ceiling_text,
        "uncertain_within_ceiling": True,
        "unknown_charge_attempt_count": sum(unknown_counts.values()),
        "unknown_charge_by_model": unknown_by_model,
        "usage_ledger_raw_sha256": material["ledger_raw_sha256"],
        "usage_ledger_state_raw_sha256": material["state_raw_sha256"],
        "usage_ledger_id": _text(
            snapshot.identity.get("ledger_id"), "usage ledger id"),
        "usage_ledger_tail_sequence": snapshot.last_sequence,
        "usage_ledger_tail_event_hash": _sha256(
            snapshot.last_event_hash, "usage ledger tail event hash"),
    }


def mechanically_validate_checker_malformed(
    *,
    cell_key: str,
    ledger_attempt_id: str,
    expected_checker_model: str,
    usage_ledger_path: str | Path,
    request_journal_path: str | Path,
    journal_execution_identity: str,
) -> dict[str, Any]:
    """Retain the original malformed-only evidence contract."""
    return mechanically_validate_checker_terminal(
        cell_key=cell_key,
        reason=TERMINAL_REASON,
        ledger_attempt_id=ledger_attempt_id,
        expected_checker_model=expected_checker_model,
        usage_ledger_path=usage_ledger_path,
        request_journal_path=request_journal_path,
        journal_execution_identity=journal_execution_identity,
    )


def mechanically_validate_checker_terminal(
    *,
    cell_key: str,
    reason: str,
    ledger_attempt_id: str,
    expected_checker_model: str,
    usage_ledger_path: str | Path,
    request_journal_path: str | Path,
    journal_execution_identity: str,
) -> dict[str, Any]:
    """Join and validate one terminal checker response from immutable run artifacts."""
    if reason not in TERMINAL_REASONS:
        raise TerminalDispositionError("inadmissible terminal checker reason")
    cell_key = _text(cell_key, "cell_key")
    ledger_attempt_id = _text(ledger_attempt_id, "ledger_attempt_id")
    expected_checker_model = _text(expected_checker_model, "expected_checker_model")
    journal_execution_identity = _text(
        journal_execution_identity, "journal_execution_identity")
    ledger_path = Path(usage_ledger_path).resolve()
    journal_path = Path(request_journal_path).resolve()

    _load_chained_usage_ledger_readonly(ledger_path)
    ledger_events = api_client._read_usage_events(ledger_path)  # noqa: SLF001
    matching = [
        event for event in ledger_events
        if event.get("attempt_id") == ledger_attempt_id
    ]
    reserved = [event for event in matching if event.get("status") == "reserved"]
    terminal = [event for event in matching if event.get("status") != "reserved"]
    if len(reserved) != 1 or len(terminal) != 1:
        raise TerminalDispositionError(
            f"{reason} evidence must name exactly one reserved and one terminal "
            "ledger event")
    reservation, event = reserved[0], terminal[0]
    if event.get("status") != "success":
        raise TerminalDispositionError(
            f"{reason} requires a settled successful provider response")
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        raise TerminalDispositionError("checker terminal event has no request metadata")
    if metadata.get("cell_key") != cell_key:
        raise TerminalDispositionError("checker terminal event names another cell")
    if metadata.get("call_role") != QUERY_CHECKER_ROLE:
        raise TerminalDispositionError("terminal evidence is not a query_checker call")
    if event.get("model") != expected_checker_model:
        raise TerminalDispositionError("terminal evidence uses the wrong checker model")

    key = journal_key(metadata)
    rows = _journal_rows(journal_path, execution_identity=journal_execution_identity)
    journal_matches = [
        row for row in rows
        if (row.get("cell_key"), row.get("call_role"), row.get("slot"), row.get("attempt"))
        == (key.cell_key, key.call_role, key.slot, key.attempt)
    ]
    if len(journal_matches) != 1:
        raise TerminalDispositionError(
            "checker ledger event does not join to exactly one request-journal response")
    journal_row = journal_matches[0]
    request_sha256 = metadata.get(JOURNAL_REQUEST_SHA256_FIELD)
    if request_sha256 != journal_row.get("request_sha256"):
        raise TerminalDispositionError(
            "checker ledger/journal request fingerprint binding disagrees")
    response = journal_row.get("response")
    if not isinstance(response, str):
        raise TerminalDispositionError("journaled checker response is not text")
    try:
        parsed = parse_checker_output(response)
    except MalformedCheckerOutput as exc:
        if reason != TERMINAL_REASON:
            raise TerminalDispositionError(
                "checker_unresolved requires the exact unresolved decision; "
                "journaled checker response is malformed") from exc
        parser_evidence = {"parse_failure": str(exc)}
    else:
        if reason == TERMINAL_REASON:
            raise TerminalDispositionError(
                "journaled checker response parses successfully and cannot be terminally disposed")
        if parsed.decision != CheckerDecision.UNRESOLVED:
            raise TerminalDispositionError(
                "checker_unresolved requires the exact unresolved decision; "
                f"journaled checker response is {parsed.decision.value}")
        parser_evidence = {"checker_decision": parsed.decision.value}

    response_metadata = event.get("response_metadata")
    if not isinstance(response_metadata, Mapping):
        raise TerminalDispositionError("checker success has no response metadata")
    finish_reason = _text(
        response_metadata.get("finish_reason"), "checker finish_reason")
    prompt_tokens = _non_negative_int(
        event.get("prompt_tokens"), "checker prompt_tokens")
    completion_tokens = _non_negative_int(
        event.get("completion_tokens"), "checker completion_tokens")
    if response_metadata.get("prompt_tokens") != prompt_tokens:
        raise TerminalDispositionError("checker prompt-token evidence is inconsistent")
    if response_metadata.get("completion_tokens") != completion_tokens:
        raise TerminalDispositionError("checker completion-token evidence is inconsistent")

    return {
        "ledger_attempt_id": ledger_attempt_id,
        "ledger_reserved_sequence": _non_negative_int(
            reservation.get("sequence"), "ledger reservation sequence"),
        "ledger_terminal_sequence": _non_negative_int(
            event.get("sequence"), "ledger terminal sequence"),
        "ledger_terminal_event_sha256": _sha256(
            event.get("event_hash"), "ledger terminal event_hash"),
        "journal_sequence": _non_negative_int(
            journal_row.get("sequence"), "journal sequence"),
        "journal_event_sha256": _sha256(
            journal_row.get("event_hash"), "journal event_hash"),
        "journal_request_sha256": _sha256(
            journal_row.get("request_sha256"), "journal request_sha256"),
        "checker_response_sha256": _sha256_text(response),
        "checker_model": expected_checker_model,
        "finish_reason": finish_reason,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        **parser_evidence,
    }


class MainTerminalDispositionStore:
    """Identity-seeded, append-only, hash-chained main terminal dispositions."""

    def __init__(
        self,
        path: str | Path,
        *,
        run_id: str,
        manifest_canonical_sha256: str,
        inventory: MainInventory,
        checker_model: str = DEFAULT_CHECKER_MODEL,
    ) -> None:
        self.path = Path(path).resolve()
        self.run_id = _text(run_id, "run_id")
        self.manifest_canonical_sha256 = _sha256(
            manifest_canonical_sha256, "manifest_canonical_sha256")
        self.inventory = inventory
        _transcripts, self._judgments = _inventory_index(inventory)
        self.inventory_canonical_sha256 = inventory_canonical_sha256(inventory)
        self.checker_model = _text(checker_model, "checker_model")
        if self.checker_model != DEFAULT_CHECKER_MODEL:
            raise MainFinalizationError(
                "main terminal dispositions must use the frozen query-checker model")
        self._seed = _terminal_seed(
            run_id=self.run_id,
            manifest_sha256=self.manifest_canonical_sha256,
            inventory_sha256=self.inventory_canonical_sha256,
        )
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._by_cell: dict[str, dict[str, Any]] = {}
        self._last_hash = self._seed
        self._sequence = -1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                with self.path.open("xb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
                _fsync_parent(self.path)
            except FileExistsError:
                pass
        self._load()

    def _reset(self) -> None:
        self._rows = []
        self._by_cell = {}
        self._last_hash = self._seed
        self._sequence = -1

    def _load(self) -> None:
        self._reset()
        rows = _read_jsonl(self.path, "main terminal disposition store")
        for line_number, row in enumerate(rows, 1):
            try:
                _exact_keys(row, TERMINAL_ROW_FIELDS, f"terminal row {line_number}")
                if row["schema_version"] != TERMINAL_SCHEMA:
                    raise TerminalDispositionError("unsupported terminal disposition schema")
                if (row["run_id"] != self.run_id
                        or row["manifest_canonical_sha256"]
                        != self.manifest_canonical_sha256
                        or row["inventory_canonical_sha256"]
                        != self.inventory_canonical_sha256):
                    raise TerminalDispositionError(
                        "terminal disposition belongs to another main identity")
                cell_key = _text(row["cell_key"], "terminal cell_key")
                if cell_key not in self._judgments:
                    raise TerminalDispositionError(
                        "terminal disposition names an unplanned or non-judgment main cell")
                if row["reason"] not in TERMINAL_REASONS:
                    raise TerminalDispositionError(
                        "main terminal store admits only checker_malformed or checker_unresolved")
                _validate_evidence_shape(
                    row["evidence"], "terminal evidence", reason=row["reason"])
                _utc(row["recorded_at_utc"], "terminal recorded_at_utc")
                sequence = _non_negative_int(row["sequence"], "terminal sequence")
                if sequence != self._sequence + 1:
                    raise TerminalDispositionError("terminal disposition sequence is broken")
                if row["prev_event_hash"] != self._last_hash:
                    raise TerminalDispositionError("terminal disposition chain is broken")
                if row["event_hash"] != _terminal_row_hash(row):
                    raise TerminalDispositionError("terminal disposition row hash is invalid")
                if cell_key in self._by_cell:
                    raise TerminalDispositionError(
                        "terminal disposition store repeats a cell key")
            except MainFinalizationError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise TerminalDispositionError(
                    f"invalid terminal disposition at line {line_number}") from exc
            self._rows.append(dict(row))
            self._by_cell[cell_key] = dict(row)
            self._sequence = sequence
            self._last_hash = str(row["event_hash"])

    @property
    def records(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(dict(row) for row in self._rows)

    @property
    def cell_keys(self) -> frozenset[str]:
        return frozenset(self._by_cell)

    @property
    def tail(self) -> dict[str, Any]:
        raw = self.path.read_bytes()
        return {
            "path": self.path.as_posix(),
            "raw_sha256": _sha256_bytes(raw),
            "record_count": len(self._rows),
            "last_sequence": self._sequence,
            "last_event_hash": self._last_hash,
        }

    def record_checker_malformed(
        self,
        cell_key: str,
        *,
        ledger_attempt_id: str,
        usage_ledger_path: str | Path,
        request_journal_path: str | Path,
        journal_execution_identity: str,
        result_cell_keys: Iterable[str],
        recorded_at_utc: str,
    ) -> Mapping[str, Any]:
        """Retain the original malformed-only terminal-disposition API."""
        return self.record_checker_terminal(
            cell_key,
            reason=TERMINAL_REASON,
            ledger_attempt_id=ledger_attempt_id,
            usage_ledger_path=usage_ledger_path,
            request_journal_path=request_journal_path,
            journal_execution_identity=journal_execution_identity,
            result_cell_keys=result_cell_keys,
            recorded_at_utc=recorded_at_utc,
        )

    def record_checker_terminal(
        self,
        cell_key: str,
        *,
        reason: str,
        ledger_attempt_id: str,
        usage_ledger_path: str | Path,
        request_journal_path: str | Path,
        journal_execution_identity: str,
        result_cell_keys: Iterable[str],
        recorded_at_utc: str,
    ) -> Mapping[str, Any]:
        """Mechanically validate and durably append one main terminal disposition."""
        cell_key = _text(cell_key, "cell_key")
        if cell_key not in self._judgments:
            raise TerminalDispositionError(
                "terminal disposition names an unplanned or non-judgment main cell")
        result_keys = frozenset(_cell_key_sequence(result_cell_keys, "result_cell_keys"))
        if cell_key in result_keys:
            raise TerminalDispositionError(
                "a terminally disposed cell already has a result row")
        recorded_at_utc = _utc(recorded_at_utc, "recorded_at_utc")
        evidence = mechanically_validate_checker_terminal(
            cell_key=cell_key,
            reason=reason,
            ledger_attempt_id=ledger_attempt_id,
            expected_checker_model=self.checker_model,
            usage_ledger_path=usage_ledger_path,
            request_journal_path=request_journal_path,
            journal_execution_identity=journal_execution_identity,
        )
        with self._lock:
            self._load()
            if cell_key in self._by_cell:
                raise TerminalDispositionError(
                    f"terminal disposition already exists for {cell_key}")
            row: dict[str, Any] = {
                "schema_version": TERMINAL_SCHEMA,
                "run_id": self.run_id,
                "manifest_canonical_sha256": self.manifest_canonical_sha256,
                "inventory_canonical_sha256": self.inventory_canonical_sha256,
                "cell_key": cell_key,
                "reason": reason,
                "evidence": evidence,
                "recorded_at_utc": recorded_at_utc,
                "sequence": self._sequence + 1,
                "prev_event_hash": self._last_hash,
            }
            row["event_hash"] = _terminal_row_hash(row)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_parent(self.path)
            self._rows.append(dict(row))
            self._by_cell[cell_key] = dict(row)
            self._sequence = int(row["sequence"])
            self._last_hash = str(row["event_hash"])
            return dict(row)

    def verify_all_evidence(
        self,
        *,
        usage_ledger_path: str | Path,
        request_journal_path: str | Path,
        journal_execution_identity: str,
        result_cell_keys: Iterable[str],
    ) -> None:
        """Re-prove every stored disposition against the final immutable artifacts."""
        result_keys = frozenset(_cell_key_sequence(result_cell_keys, "result_cell_keys"))
        for record in self._rows:
            cell_key = str(record["cell_key"])
            if cell_key in result_keys:
                raise TerminalDispositionError(
                    "a terminally disposed cell has a result row")
            observed = mechanically_validate_checker_terminal(
                cell_key=cell_key,
                reason=str(record["reason"]),
                ledger_attempt_id=str(record["evidence"]["ledger_attempt_id"]),
                expected_checker_model=self.checker_model,
                usage_ledger_path=usage_ledger_path,
                request_journal_path=request_journal_path,
                journal_execution_identity=journal_execution_identity,
            )
            if observed != record["evidence"]:
                raise TerminalDispositionError(
                    f"terminal evidence drifted for cell {cell_key}")


def _context_exclusion_keys(
    blocklist: Mapping[str, Any],
    *,
    inventory: MainInventory,
    judgments: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    if blocklist.get("scope") != "main":
        raise MainPartitionError("context blocklist scope must be main")
    namespace = str(inventory.cells[0]["cell_key"]).split(":", 1)[0]
    if blocklist.get("cell_key_namespace") != namespace:
        raise MainPartitionError("context blocklist names another plan namespace")
    excluded = blocklist.get("excluded")
    if isinstance(excluded, (str, bytes)) or not isinstance(excluded, Sequence):
        raise MainPartitionError("context blocklist excluded must be a list")
    keys: list[str] = []
    counts: Counter[tuple[str, str]] = Counter()
    for index, raw in enumerate(excluded):
        if not isinstance(raw, Mapping):
            raise MainPartitionError(f"context exclusion {index} must be an object")
        raw = dict(raw)
        key = _text(raw.get("cell_key"), f"context exclusion {index}.cell_key")
        cell = judgments.get(key)
        if cell is None:
            raise MainPartitionError(
                "context blocklist names an unplanned or non-judgment main cell")
        for field in (
            "judge_model",
            "question_id",
            "debater_model",
            "transcript_index",
            "condition",
        ):
            if raw.get(field) != cell.get(field):
                raise MainPartitionError(
                    f"context exclusion {key} disagrees on {field}")
        keys.append(key)
        counts[(str(cell["judge_model"]), str(cell["condition"]))] += 1
    if len(keys) != len(set(keys)):
        raise MainPartitionError("context blocklist contains duplicate cell keys")
    if blocklist.get("excluded_count") != len(keys):
        raise MainPartitionError("context blocklist excluded_count is inconsistent")
    claimed_counts = blocklist.get("counts_by_judge_and_condition")
    if not isinstance(claimed_counts, Sequence) or isinstance(claimed_counts, (str, bytes)):
        raise MainPartitionError(
            "context blocklist counts_by_judge_and_condition must be a list")
    normalized_claims: list[tuple[str, str, int]] = []
    for index, row in enumerate(claimed_counts):
        if not isinstance(row, Mapping):
            raise MainPartitionError(f"context count row {index} must be an object")
        row = dict(row)
        normalized_claims.append((
            _text(row.get("judge_model"), f"context count {index}.judge_model"),
            _text(row.get("condition"), f"context count {index}.condition"),
            _non_negative_int(row.get("count"), f"context count {index}.count"),
        ))
    expected_counts = sorted((judge, condition, count)
                             for (judge, condition), count in counts.items())
    if normalized_claims != expected_counts:
        raise MainPartitionError("context blocklist count summary is inconsistent")
    return tuple(keys)


def validate_main_partition(
    *,
    inventory: MainInventory,
    result_cell_keys: Iterable[str],
    terminal_records: Sequence[Mapping[str, Any]],
    context_blocklist: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the exact transcript/result/terminal/context partition."""
    transcripts, judgments = _inventory_index(inventory)
    results = _cell_key_sequence(result_cell_keys, "result_cell_keys")
    result_set = frozenset(results)
    terminal_keys = tuple(
        _text(record.get("cell_key"), f"terminal record {index}.cell_key")
        for index, record in enumerate(terminal_records)
    )
    if len(terminal_keys) != len(set(terminal_keys)):
        raise MainPartitionError("terminal records contain duplicate cell keys")
    for index, record in enumerate(terminal_records):
        if record.get("reason") not in TERMINAL_REASONS:
            raise MainPartitionError(
                f"terminal record {index} names an inadmissible reason")
        if terminal_keys[index] not in judgments:
            raise MainPartitionError(
                "terminal record names an unplanned or non-judgment main cell")
    context_keys = _context_exclusion_keys(
        context_blocklist, inventory=inventory, judgments=judgments)
    terminal_set = frozenset(terminal_keys)
    context_set = frozenset(context_keys)
    if terminal_set & context_set:
        raise MainPartitionError(
            "terminal and context-ineligible judgment sets overlap")
    if result_set & (terminal_set | context_set):
        raise MainPartitionError(
            "a terminal or context-ineligible cell also has a result row")

    expected = frozenset(transcripts) | frozenset(judgments)
    planned_results = expected - terminal_set - context_set
    if result_set != planned_results:
        missing = planned_results - result_set
        extra = result_set - planned_results
        raise MainPartitionError(
            "result/terminal/context partition is not exact: "
            f"missing={len(missing)}, extra={len(extra)}")
    observed_transcripts = len(result_set & frozenset(transcripts))
    observed_judgments = len(result_set & frozenset(judgments))
    if observed_transcripts != EXPECTED_MAIN_TRANSCRIPT_COUNT:
        raise MainPartitionError("all 492 frozen transcripts must have result rows")
    if (observed_judgments + len(terminal_set) + len(context_set)
            != EXPECTED_MAIN_JUDGMENT_COUNT):
        raise MainPartitionError("judgment partition does not cover exactly 9,840 slots")

    return {
        "exact": True,
        "expected_transcripts": EXPECTED_MAIN_TRANSCRIPT_COUNT,
        "observed_transcripts": observed_transcripts,
        "expected_judgments": EXPECTED_MAIN_JUDGMENT_COUNT,
        "observed_judgments": observed_judgments,
        "terminal_invalid_judgments": len(terminal_set),
        "context_ineligible_judgments": len(context_set),
        "result_cell_keys_sha256": canonical_sha256(sorted(result_set)),
        "terminal_cell_keys": sorted(terminal_set),
        "terminal_cell_keys_sha256": canonical_sha256(sorted(terminal_set)),
        "context_ineligible_cell_keys": sorted(context_set),
        "context_ineligible_cell_keys_sha256": canonical_sha256(sorted(context_set)),
    }


def _mirror_units(
    judgments: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, tuple[str, str, str, int, str]], dict[tuple[str, str, str, int, str], tuple[str, ...]]]:
    grouped: dict[tuple[str, str, str, int, str], list[tuple[int, str]]] = {}
    for key, cell in judgments.items():
        unit = (
            str(cell["question_id"]),
            str(cell["judge_model"]),
            str(cell["debater_model"]),
            int(cell["transcript_index"]),
            str(cell["condition"]),
        )
        replicate = int(cell["replicate_index"])
        grouped.setdefault(unit, []).append((replicate, key))
    unit_of: dict[str, tuple[str, str, str, int, str]] = {}
    cells_of: dict[tuple[str, str, str, int, str], tuple[str, ...]] = {}
    for unit, members in grouped.items():
        if len(members) != 2 or {index for index, _key in members} != {0, 1}:
            raise MainFinalizationError(
                f"main mirror unit {unit!r} is not exactly the two frozen sides")
        keys = tuple(key for _index, key in sorted(members))
        cells_of[unit] = keys
        for key in keys:
            unit_of[key] = unit
    if len(cells_of) * 2 != EXPECTED_MAIN_JUDGMENT_COUNT:
        raise MainFinalizationError("main mirror-unit index is incomplete")
    return unit_of, cells_of


def evaluate_terminal_bounds(
    *,
    inventory: MainInventory,
    terminal_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply all cumulative terminal bounds with the frozen strict inequalities."""
    _transcripts, judgments = _inventory_index(inventory)
    terminal = _cell_key_sequence(
        (_text(record.get("cell_key"), "terminal cell_key")
         for record in terminal_records),
        "terminal_cell_keys",
    )
    if any(key not in judgments for key in terminal):
        raise TerminalBoundError("terminal bound input contains an unplanned judgment")
    if len(terminal) > MAX_TERMINAL_JUDGMENT_CELLS:
        raise TerminalBoundError(
            f"{len(terminal)} terminal cells exceed the frozen bound of "
            f"{MAX_TERMINAL_JUDGMENT_CELLS}")

    unit_of, cells_of = _mirror_units(judgments)
    affected_units = {unit_of[key] for key in terminal}
    affected_fraction = len(affected_units) / len(cells_of)
    if affected_fraction > MAX_AFFECTED_MIRROR_UNIT_FRACTION:
        raise TerminalBoundError(
            "affected mirror-unit fraction exceeds the frozen 4 percent bound")

    concentration: dict[str, dict[str, Any]] = {}
    terminal_set = frozenset(terminal)
    for field in ("judge_model", "condition"):
        exposures = Counter(str(cell[field]) for cell in judgments.values())
        hits = Counter(str(judgments[key][field]) for key in terminal_set)
        groups: dict[str, Any] = {}
        total_hits = sum(hits.values())
        total_exposure = sum(exposures.values())
        for group in sorted(exposures):
            count = hits[group]
            exposure = exposures[group]
            rate = count / exposure
            complement_hits = total_hits - count
            complement_exposure = total_exposure - exposure
            complement_rate = (
                complement_hits / complement_exposure if complement_exposure else 0.0)
            rate_trigger = count >= CONCENTRATION_MIN_CELLS and rate > CONCENTRATION_RATE
            ratio_trigger = (
                count >= CONCENTRATION_MIN_CELLS
                and rate > CONCENTRATION_RATIO * complement_rate
            )
            groups[group] = {
                "terminal_cells": count,
                "exposure": exposure,
                "rate": rate,
                "complement_rate": complement_rate,
                "rate_trigger": rate_trigger,
                "ratio_trigger": ratio_trigger,
            }
            if rate_trigger or ratio_trigger:
                raise TerminalBoundError(
                    f"terminal-cell concentration bound crossed for {field}={group}")
        concentration[field] = groups

    affected_cells = sorted(
        key for unit in affected_units for key in cells_of[unit])
    return {
        "pass": True,
        "terminal_cells": len(terminal),
        "max_terminal_cells": MAX_TERMINAL_JUDGMENT_CELLS,
        "affected_mirror_units": len(affected_units),
        "total_mirror_units": len(cells_of),
        "affected_mirror_unit_fraction": affected_fraction,
        "max_affected_mirror_unit_fraction": MAX_AFFECTED_MIRROR_UNIT_FRACTION,
        "affected_mirror_unit_cell_keys": affected_cells,
        "concentration_min_cells": CONCENTRATION_MIN_CELLS,
        "concentration_rate": CONCENTRATION_RATE,
        "concentration_ratio": CONCENTRATION_RATIO,
        "concentration": concentration,
    }


def _prompt_token_band(prompt_tokens: int) -> str:
    if prompt_tokens < 2_048:
        return "<2k"
    if prompt_tokens < 4_096:
        return "2-4k"
    if prompt_tokens < 8_192:
        return "4-8k"
    return ">=8k"


def _diagnostic_group(entries: Mapping[str, dict[str, int]]) -> dict[str, Any]:
    return {
        group: {
            **counts,
            "finish_length_rate": (
                counts["finish_length"] / counts["checker_calls"]
                if counts["checker_calls"] else None
            ),
        }
        for group, counts in sorted(entries.items())
    }


def checker_truncation_diagnostic(
    *,
    inventory: MainInventory,
    ledger_events: Sequence[Mapping[str, Any]],
    terminal_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Emit the frozen finish-reason-length diagnostic and its clustering."""
    _transcripts, judgments = _inventory_index(inventory)
    by_judge = {
        judge: {"checker_calls": 0, "finish_length": 0}
        for judge in sorted(inventory.judges)
    }
    by_condition = {
        condition: {"checker_calls": 0, "finish_length": 0}
        for condition in sorted({str(cell["condition"]) for cell in judgments.values()})
    }
    by_band: dict[str, dict[str, int]] = {}
    length_calls: list[dict[str, Any]] = []
    checker_calls = 0
    for event in ledger_events:
        metadata = event.get("metadata")
        if (event.get("status") != "success" or not isinstance(metadata, Mapping)
                or metadata.get("call_role") != QUERY_CHECKER_ROLE):
            continue
        cell_key = _text(metadata.get("cell_key"), "checker ledger cell_key")
        cell = judgments.get(cell_key)
        if cell is None:
            raise MainFinalizationError(
                "main ledger contains a query_checker success outside the exact inventory")
        response_metadata = event.get("response_metadata")
        if not isinstance(response_metadata, Mapping):
            raise MainFinalizationError("checker success lacks response metadata")
        finish_reason = _text(
            response_metadata.get("finish_reason"), "checker finish_reason")
        prompt_tokens = _non_negative_int(
            event.get("prompt_tokens"), "checker prompt_tokens")
        is_length = finish_reason == "length"
        checker_calls += 1
        judge = str(cell["judge_model"])
        condition = str(cell["condition"])
        band = _prompt_token_band(prompt_tokens)
        by_band.setdefault(band, {"checker_calls": 0, "finish_length": 0})
        for entry in (by_judge[judge], by_condition[condition], by_band[band]):
            entry["checker_calls"] += 1
            entry["finish_length"] += int(is_length)
        if is_length:
            length_calls.append({
                "cell_key": cell_key,
                "ledger_attempt_id": event.get("attempt_id"),
                "ledger_event_sha256": event.get("event_hash"),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": event.get("completion_tokens"),
                "judge_model": judge,
                "condition": condition,
            })
    bounds = evaluate_terminal_bounds(
        inventory=inventory, terminal_records=terminal_records)
    terminal_signatures = [
        {
            "cell_key": str(record["cell_key"]),
            "reason": str(record["reason"]),
            "ledger_attempt_id": record["evidence"]["ledger_attempt_id"],
            "finish_reason": record["evidence"]["finish_reason"],
            "completion_tokens": record["evidence"]["completion_tokens"],
            "checker_response_sha256": record["evidence"]["checker_response_sha256"],
        }
        for record in terminal_records
    ]
    return {
        "checker_calls_total": checker_calls,
        "finish_length_count": len(length_calls),
        "finish_length_rate": (
            len(length_calls) / checker_calls if checker_calls else None),
        "finish_length_calls": length_calls,
        "terminal_checker_malformed_count": sum(
            record["reason"] == TERMINAL_REASON for record in terminal_records),
        "terminal_checker_unresolved_count": sum(
            record["reason"] == UNRESOLVED_TERMINAL_REASON for record in terminal_records),
        "terminal_signatures": terminal_signatures,
        "affected_mirror_unit_count": bounds["affected_mirror_units"],
        "by_judge": _diagnostic_group(by_judge),
        "by_condition": _diagnostic_group(by_condition),
        "by_prompt_token_band": _diagnostic_group(by_band),
        "non_claim": (
            "checker truncation is not assumed missing completely at random; difficult "
            "or lengthy prompts may be more likely to trigger it"
        ),
    }


def _load_result_rows_material(path: str | Path) -> tuple[tuple[dict[str, Any], ...], bytes]:
    """Strictly validate complete CellResultStore rows from stable bytes."""
    result_path = Path(path).resolve()
    raw = _read_stable_bytes(result_path, "main result store")
    rows = _jsonl_bytes(raw, "main result store")
    previous = "genesis"
    keys: set[str] = set()
    validated: list[dict[str, Any]] = []
    for sequence, row in enumerate(rows):
        required = frozenset({
            "cell_key", "result", "sequence", "prev_event_hash", "event_hash"})
        _exact_keys(row, required, f"result row {sequence}")
        if row["sequence"] != sequence or isinstance(row["sequence"], bool):
            raise MainPartitionError("main result-store sequence is broken")
        if row["prev_event_hash"] != previous:
            raise MainPartitionError("main result-store hash chain is broken")
        material = _canonical_json({
            key: row[key]
            for key in ("cell_key", "result", "sequence", "prev_event_hash")
        })
        expected_hash = _sha256_text(material)
        if row["event_hash"] != expected_hash:
            raise MainPartitionError("main result-store row hash is invalid")
        key = _text(row["cell_key"], "result cell_key")
        if key in keys:
            raise MainPartitionError("main result store repeats a cell key")
        result = row["result"]
        if not isinstance(result, Mapping):
            raise MainPartitionError(
                f"main result row {sequence} result must be an object")
        if result.get("cell_key") != key:
            raise MainPartitionError(
                f"main result row {sequence} payload names another cell")
        keys.add(key)
        validated.append({**row, "result": dict(result)})
        previous = expected_hash
    if _read_stable_bytes(result_path, "main result store") != raw:
        raise MainPartitionError(
            "main result store changed while finalization validated it")
    return tuple(validated), raw


def load_result_rows(path: str | Path) -> tuple[Mapping[str, Any], ...]:
    """Return every fully chain-validated result row in exact store order."""
    rows, _raw = _load_result_rows_material(path)
    return tuple(dict(row) for row in rows)


def load_result_cell_keys(path: str | Path) -> tuple[str, ...]:
    """Strictly validate the main CellResultStore chain and return its exact key order."""
    rows, _raw = _load_result_rows_material(path)
    return tuple(str(row["cell_key"]) for row in rows)


def _load_stable_journal_material(
    path: Path, *, execution_identity: str,
) -> tuple[list[dict[str, Any]], bytes]:
    raw = _read_stable_bytes(path, "request journal")
    rows = _journal_rows(path, execution_identity=execution_identity)
    if _read_stable_bytes(path, "request journal") != raw:
        raise MainFinalizationError(
            "request journal changed while finalization validated it")
    return rows, raw


def _same_exact(observed: Any, expected: Any) -> bool:
    if isinstance(expected, int) and not isinstance(expected, bool):
        return (isinstance(observed, int) and not isinstance(observed, bool)
                and observed == expected)
    return type(observed) is type(expected) and observed == expected


def _require_planned_metadata(
    observed: Mapping[str, Any], cell: Mapping[str, Any], *, label: str,
) -> None:
    expected = {
        "question_id": cell["question_id"],
        "transcript_index": cell["transcript_index"],
        "judge_model": cell["judge_model"],
        "replicate": cell["replicate_index"],
        "budget": cell["query_budget"],
        "condition": cell["condition"],
    }
    for field, value in expected.items():
        if not _same_exact(observed.get(field), value):
            raise MainFinalizationError(
                f"{label} disagrees with the planned cell on {field}")


def _load_reviewer_decisions(
    path: str | Path,
) -> tuple[dict[str, Any], bytes]:
    """Validate the complete decision chain without permitting a byte race."""
    decision_path = Path(path).resolve()
    raw = _read_stable_bytes(decision_path, "review decision store")
    try:
        store = DualGateDecisionStore(decision_path)
    except (OSError, TypeError, ValueError) as exc:
        raise MainFinalizationError("review decision store is invalid") from exc
    if _read_stable_bytes(decision_path, "review decision store") != raw:
        raise MainFinalizationError(
            "review decision store changed while finalization validated it")
    decisions = {
        str(payload): decision
        for payload, decision in store._by_payload.items()  # noqa: SLF001
    }
    return decisions, raw


def _expected_judgment_polarity(cell: Mapping[str, Any]) -> bool:
    replicate = _non_negative_int(
        cell.get("replicate_index"), "planned judgment replicate_index")
    if replicate not in (0, 1):
        raise MainFinalizationError(
            "planned main judgment replicate does not encode one of the two mirror sides")
    base = position_for(
        ARMS[MAIN_ARM_NAME],
        str(cell["question_id"]),
        int(cell["transcript_index"]),
        str(cell["judge_model"]),
        int(cell["query_budget"]),
    )
    side = replicate // MAIN_JUDGMENT_REPLICATES_PER_SIDE
    return base if side % 2 == 0 else not base


def _expected_judgment_seed(cell: Mapping[str, Any]) -> int:
    namespace = str(cell["cell_key"]).split(":", 1)[0]
    return judgment_seed(
        str(cell["question_id"]),
        int(cell["transcript_index"]),
        str(cell["judge_model"]),
        int(cell["query_budget"]),
        MAIN_ARM_NAME,
        int(cell["replicate_index"]),
        debater_model=str(cell["debater_model"]),
        namespace=namespace,
    )


def _candidate_pair(
    cell: Mapping[str, Any], transcript: Mapping[str, Any],
) -> tuple[str, str]:
    correct = _text(transcript.get("correct_answer"), "transcript correct_answer")
    wrong = _text(transcript.get("wrong_answer"), "transcript wrong_answer")
    return (correct, wrong) if _expected_judgment_polarity(cell) else (wrong, correct)


def _validate_observed_judgment_semantics(
    *,
    cell: Mapping[str, Any],
    result: Mapping[str, Any],
    transcript: Mapping[str, Any],
    verdict_journal_row: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> tuple[dict[tuple[str, str, int, int], str], set[str]]:
    """Recompute one result's outcome, request identity, and provider-call schedule."""
    cell_key = str(cell["cell_key"])
    raw_verdict = result.get("raw_verdict_text")
    if not isinstance(raw_verdict, str):
        raise MainFinalizationError(
            f"main judgment result {cell_key} has no raw verdict text")
    expected_seed = _expected_judgment_seed(cell)
    if not _same_exact(result.get("seed"), expected_seed):
        raise MainFinalizationError(
            f"main judgment result {cell_key} has a non-frozen seed")
    expected_position = _expected_judgment_polarity(cell)
    if result.get("position_a_is_correct") is not expected_position:
        raise MainFinalizationError(
            f"main judgment result {cell_key} has a non-frozen A/B polarity")
    if result.get("arm") != MAIN_ARM_NAME:
        raise MainFinalizationError(
            f"main judgment result {cell_key} used a non-main arm")
    if result.get("world") != transcript.get("world"):
        raise MainFinalizationError(
            f"main judgment result {cell_key} disagrees with its transcript world")
    if result.get("parser_version") != PARSER_VERSION:
        raise MainFinalizationError(
            f"main judgment result {cell_key} parser version drifted")

    parsed = parse_both(raw_verdict)
    if result.get("verdict_strict") != parsed["strict"]:
        raise MainFinalizationError(
            f"main judgment result {cell_key} strict verdict was not recomputed")
    if result.get("verdict_pilot") != parsed["pilot"]:
        raise MainFinalizationError(
            f"main judgment result {cell_key} pilot verdict was not recomputed")
    strict_verdict = parsed["strict"]["verdict"]
    expected_strict_correct = (
        None if strict_verdict is None
        else (strict_verdict == "A") == expected_position
    )
    expected_pilot_correct = (
        (parsed["pilot"]["verdict"] == "Position A") == expected_position
    )
    if result.get("verdict_correct_strict") is not expected_strict_correct:
        raise MainFinalizationError(
            f"main judgment result {cell_key} strict correctness drifted")
    if result.get("verdict_correct_pilot") is not expected_pilot_correct:
        raise MainFinalizationError(
            f"main judgment result {cell_key} pilot correctness drifted")

    messages = result.get("judge_messages")
    if (
        not isinstance(messages, list)
        or len(messages) < 3
        or any(
            not isinstance(message, Mapping)
            or set(message) != {"role", "content"}
            or message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str)
            for message in messages
        )
        or messages[-1] != {"role": "assistant", "content": raw_verdict}
    ):
        raise MainFinalizationError(
            f"main judgment result {cell_key} has invalid judge_messages")
    verdict_request_sha = request_fingerprint(
        messages=messages[:-1],
        model=str(cell["judge_model"]),
        temperature=MAIN_VERDICT_TEMPERATURE,
        seed=expected_seed + 99_999,
        max_tokens=MAIN_VERDICT_MAX_TOKENS,
    )
    if verdict_journal_row.get("request_sha256") != verdict_request_sha:
        raise MainFinalizationError(
            f"main judgment result {cell_key} verdict request fingerprint drifted")

    budget = _non_negative_int(cell.get("query_budget"), "planned query budget")
    queries_used = _non_negative_int(
        result.get("queries_used"), f"main judgment result {cell_key}.queries_used")
    exchanges = result.get("exchanges")
    if not isinstance(exchanges, list) or len(exchanges) != queries_used:
        raise MainFinalizationError(
            f"main judgment result {cell_key} exchanges differ from queries_used")
    if queries_used > budget:
        raise MainFinalizationError(
            f"main judgment result {cell_key} exceeds its query budget")

    expected_calls: dict[tuple[str, str, int, int], str] = {
        (cell_key, JUDGE_VERDICT_ROLE, 0, 1): raw_verdict,
    }
    reviewed_payloads: set[str] = set()
    events = result.get("gate_events", [])
    intercepts = result.get("checker_false_allow_intercepts", [])
    if budget == 0:
        if events not in (None, []) or intercepts not in (None, []) or exchanges:
            raise MainFinalizationError(
                f"budget-zero result {cell_key} contains query-gate activity")
        return expected_calls, reviewed_payloads
    if not isinstance(events, list) or not isinstance(intercepts, list):
        raise MainFinalizationError(
            f"main judgment result {cell_key} lacks query-gate evidence")

    candidate_a, candidate_b = _candidate_pair(cell, transcript)
    expected_slot = 1
    expected_attempt = 1
    consumed_events: list[Mapping[str, Any]] = []
    expected_intercepts: list[str] = []
    for event_index, event in enumerate(events, 1):
        if not isinstance(event, Mapping):
            raise MainFinalizationError(
                f"main judgment result {cell_key} gate event is not an object")
        event = cast(Mapping[str, Any], event)
        if (
            event.get("sequence") != event_index
            or event.get("slot") != expected_slot
            or event.get("attempt") != expected_attempt
            or event.get("candidate_a") != candidate_a
            or event.get("candidate_b") != candidate_b
        ):
            raise MainFinalizationError(
                f"main judgment result {cell_key} gate-event state drifted")
        raw_query = event.get("raw_query")
        if not isinstance(raw_query, str):
            raise MainFinalizationError(
                f"main judgment result {cell_key} gate event lacks raw query text")
        expected_payload = payload_hash(raw_query, candidate_a, candidate_b)
        if event.get("reviewer_payload_sha256") != expected_payload:
            raise MainFinalizationError(
                f"main judgment result {cell_key} reviewer payload hash drifted")
        decision = decisions.get(expected_payload)
        if decision is None:
            raise MainFinalizationError(
                f"main judgment result {cell_key} lacks a committed reviewer decision")
        if (
            event.get("reviewer_label") != decision.label
            or event.get("reviewer_clause") != decision.clause
            or event.get("reviewer_status") != decision.status
        ):
            raise MainFinalizationError(
                f"main judgment result {cell_key} reviewer decision fields drifted")
        reviewed_payloads.add(expected_payload)

        action = event.get("action")
        if action not in {"allow", "retry", "block"}:
            raise MainFinalizationError(
                f"main judgment result {cell_key} gate event has an invalid action")
        mechanical = event.get("mechanical_reasons")
        if not isinstance(mechanical, list) or any(
                not isinstance(reason, str) or not reason for reason in mechanical):
            raise MainFinalizationError(
                f"main judgment result {cell_key} mechanical reasons are invalid")
        checker_identity = (
            cell_key, QUERY_CHECKER_ROLE, expected_slot, expected_attempt)
        checker_decision = event.get("checker_decision")
        if mechanical:
            if event.get("checker_raw_output") is not None or checker_decision is not None:
                raise MainFinalizationError(
                    f"mechanical gate event {cell_key} unexpectedly used the checker")
        else:
            checker_response = event.get("checker_raw_output")
            if not isinstance(checker_response, str) or checker_decision not in {
                    "allow", "reject"}:
                raise MainFinalizationError(
                    f"main judgment result {cell_key} checker evidence is invalid")
            expected_calls[checker_identity] = checker_response

        expected_calls[(
            cell_key, JUDGE_QUERY_ROLE, expected_slot - 1, expected_attempt
        )] = raw_query
        checker_allow = checker_decision == "allow"
        expected_action = (
            "retry" if checker_decision == "reject" and expected_attempt == 1
            else "block" if checker_decision == "reject"
            else "allow" if checker_allow and decision.effective_allow
            else "block"
        )
        if mechanical:
            expected_action = "retry" if expected_attempt == 1 else "block"
        if action != expected_action:
            raise MainFinalizationError(
                f"main judgment result {cell_key} gate action differs from its decisions")
        if checker_allow and not decision.effective_allow:
            expected_intercepts.append(expected_payload)

        consumed = action != "retry"
        if event.get("slot_consumed") is not consumed:
            raise MainFinalizationError(
                f"main judgment result {cell_key} slot-consumption evidence drifted")
        if consumed:
            consumed_events.append(event)
            expected_slot += 1
            expected_attempt = 1
        else:
            expected_attempt = 2
    if len(consumed_events) != len(exchanges) or expected_intercepts != intercepts:
        raise MainFinalizationError(
            f"main judgment result {cell_key} gate evidence does not match its exchanges")

    for exchange_index, (event, exchange) in enumerate(
        zip(consumed_events, exchanges, strict=True), 1
    ):
        if not isinstance(exchange, Mapping) or exchange.get(
                "raw_query_response") != event.get("raw_query"):
            raise MainFinalizationError(
                f"main judgment result {cell_key} exchange/query evidence drifted")
        if event.get("action") == "allow":
            oracle_reply = exchange.get("raw_oracle_reply")
            if (
                exchange.get("blocked") is True
                or not isinstance(exchange.get("oracle_prompt"), str)
                or not isinstance(oracle_reply, str)
            ):
                raise MainFinalizationError(
                    f"main judgment result {cell_key} allowed exchange lacks oracle evidence")
            expected_calls[(
                cell_key, ORACLE_VERIFICATION_ROLE, exchange_index - 1, 1
            )] = oracle_reply
        elif (
            exchange.get("blocked") is not True
            or exchange.get("oracle_prompt") is not None
            or exchange.get("raw_oracle_reply") is not None
        ):
            raise MainFinalizationError(
                f"main judgment result {cell_key} blocked exchange has oracle evidence")

    return expected_calls, reviewed_payloads


def _validate_ledger_call_metadata(
    event: Mapping[str, Any], cell: Mapping[str, Any], *, expected_checker_model: str,
    expected_oracle_model: str,
) -> tuple[str, str, int, int]:
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        raise MainFinalizationError(
            "main usage-ledger event has no journal-bound request metadata")
    try:
        key = journal_key(metadata)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise MainFinalizationError(
            "main usage-ledger event has invalid call identity metadata") from exc
    role = key.call_role
    if role not in ALLOWED_MAIN_CALL_ROLES:
        raise MainFinalizationError(
            f"main usage ledger contains forbidden call role {role!r}")
    if role == QUERY_CHECKER_ROLE:
        if metadata.get("condition") != cell.get("condition"):
            raise MainFinalizationError(
                "main query-checker call disagrees with its planned condition")
    else:
        expected = {
            "stage": "judgment",
            "question_id": cell["question_id"],
            "transcript_index": cell["transcript_index"],
            "budget": cell["query_budget"],
            "replicate": cell["replicate_index"],
            "judge_model": cell["judge_model"],
        }
        for field, value in expected.items():
            if not _same_exact(metadata.get(field), value):
                raise MainFinalizationError(
                    "main usage-ledger call disagrees with its planned cell on "
                    f"{field}")
    if role in {JUDGE_QUERY_ROLE, JUDGE_VERDICT_ROLE}:
        if event.get("model") != cell.get("judge_model"):
            raise MainFinalizationError(
                "main judge call used a model other than the planned judge")
    elif role == QUERY_CHECKER_ROLE and event.get("model") != expected_checker_model:
        raise MainFinalizationError(
            "main query-checker call used a model other than the frozen checker")
    elif role == ORACLE_VERIFICATION_ROLE and event.get("model") != expected_oracle_model:
        raise MainFinalizationError(
            "main oracle call used a model other than the frozen oracle")
    return key.cell_key, role, key.slot, key.attempt


def _validate_main_provider_provenance(
    *,
    inventory: MainInventory,
    result_rows: Sequence[Mapping[str, Any]],
    terminal_records: Sequence[Mapping[str, Any]],
    context_ineligible_cell_keys: Iterable[str],
    ledger_events: Sequence[Mapping[str, Any]],
    journal_rows: Sequence[Mapping[str, Any]],
    reviewer_decisions: Mapping[str, Any],
    review_decisions_path: str | Path,
    protocol: Mapping[str, Any],
    prompt_bundle: Mapping[str, Any],
    role_limits: Mapping[str, Any],
    transcript_provenance: (
        phase3_main_transcript_provenance.MainTranscriptProvenance),
    expected_checker_model: str,
    expected_oracle_model: str,
    result_store_raw_sha256: str,
    request_journal_raw_sha256: str,
    usage_ledger_raw_sha256: str,
    authorization_approved_at_utc: str,
    authorization_valid_until_utc: str,
    finalization_recorded_at_utc: str,
) -> dict[str, Any]:
    transcripts, judgments = _inventory_index(inventory)
    expected_checker_model = _text(
        expected_checker_model, "expected_checker_model")
    expected_oracle_model = _text(
        expected_oracle_model, "expected_oracle_model")
    terminal = frozenset(
        _text(record.get("cell_key"), "terminal record cell_key")
        for record in terminal_records
    )
    context = frozenset(_cell_key_sequence(
        context_ineligible_cell_keys, "context_ineligible_cell_keys"))

    transcript_results: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(result_rows):
        cell_key = _text(row.get("cell_key"), f"result row {index}.cell_key")
        result = row.get("result")
        if not isinstance(result, Mapping):
            raise MainFinalizationError(f"result row {index} payload must be an object")
        if result.get("cell_key") != cell_key:
            raise MainFinalizationError(f"result row {index} payload names another cell")
        if cell_key in transcripts:
            transcript = transcripts[cell_key]
            for field in ("question_id", "transcript_index", "debater_model"):
                if not _same_exact(result.get(field), transcript.get(field)):
                    raise MainFinalizationError(
                        f"transcript result {cell_key} disagrees on {field}")
            for field in ("question", "correct_answer", "wrong_answer", "world"):
                if not isinstance(result.get(field), str):
                    raise MainFinalizationError(
                        f"transcript result {cell_key} lacks required field {field}")
            transcript_results[cell_key] = result

    journal_by_identity: dict[tuple[str, str, int, int], Mapping[str, Any]] = {}
    verdict_journal_by_cell: dict[str, list[Mapping[str, Any]]] = {}
    for index, row in enumerate(journal_rows):
        cell_key = _text(row.get("cell_key"), f"request journal row {index}.cell_key")
        role = _text(row.get("call_role"), f"request journal row {index}.call_role")
        if role not in ALLOWED_MAIN_CALL_ROLES:
            raise MainFinalizationError(
                f"request journal contains forbidden main call role {role!r}")
        if cell_key not in judgments:
            raise MainFinalizationError(
                "request journal contains a call for an unplanned or non-judgment cell")
        if cell_key in context:
            raise MainFinalizationError(
                "request journal contains a call for a context-ineligible cell")
        if cell_key in terminal and role not in PRE_VERDICT_MAIN_CALL_ROLES:
            raise MainFinalizationError(
                "terminal main cell contains a post-checker verdict call")
        identity = (
            cell_key,
            role,
            _non_negative_int(row.get("slot"), f"request journal row {index}.slot"),
            _non_negative_int(
                row.get("attempt"), f"request journal row {index}.attempt"),
        )
        if identity in journal_by_identity:
            raise MainFinalizationError("request journal repeats a call identity")
        journal_by_identity[identity] = row
        if role == JUDGE_VERDICT_ROLE:
            verdict_journal_by_cell.setdefault(cell_key, []).append(row)

    success_by_identity: dict[
        tuple[str, str, int, int], list[Mapping[str, Any]]
    ] = {}
    for event_number, event in enumerate(ledger_events):
        if event.get("status") == "ledger_genesis":
            continue
        metadata = event.get("metadata")
        cell_key = metadata.get("cell_key") if isinstance(metadata, Mapping) else None
        if not isinstance(cell_key, str) or cell_key not in judgments:
            raise MainFinalizationError(
                "usage ledger contains a provider attempt for an unplanned or "
                "non-judgment cell")
        if cell_key in context:
            raise MainFinalizationError(
                "usage ledger contains a provider attempt for a context-ineligible cell")
        identity = _validate_ledger_call_metadata(
            event,
            judgments[cell_key],
            expected_checker_model=expected_checker_model,
            expected_oracle_model=expected_oracle_model,
        )
        if cell_key in terminal and identity[1] not in PRE_VERDICT_MAIN_CALL_ROLES:
            raise MainFinalizationError(
                "terminal main cell contains a post-checker verdict provider attempt")
        if event.get("status") == "success":
            success_by_identity.setdefault(identity, []).append(event)

    observed_judgments: list[str] = []
    result_reviewer_payloads: set[str] = set()
    for index, row in enumerate(result_rows):
        cell_key = _text(row.get("cell_key"), f"result row {index}.cell_key")
        result = row.get("result")
        if cell_key in transcripts:
            continue
        if not isinstance(result, Mapping):
            raise MainFinalizationError(
                f"main judgment result {cell_key} payload must be an object")
        result = cast(Mapping[str, Any], result)
        cell = judgments.get(cell_key)
        if cell is None:
            raise MainFinalizationError("result store contains an unplanned main cell")
        if result.get("dry_run") is not False:
            raise MainFinalizationError(
                f"main judgment result {cell_key} is dry-run or lacks live provenance")
        _require_planned_metadata(
            result, cell, label=f"main judgment result {cell_key}")
        if result.get("oracle_model") != expected_oracle_model:
            raise MainFinalizationError(
                f"main judgment result {cell_key} used another oracle model")
        verdict_journal = verdict_journal_by_cell.get(cell_key, [])
        if len(verdict_journal) != 1:
            raise MainFinalizationError(
                f"main judgment result {cell_key} does not have exactly one journaled "
                "judge_verdict response")
        journal_row = verdict_journal[0]
        dependency_keys = cell.get("dependency_keys")
        if not isinstance(dependency_keys, Sequence) or len(dependency_keys) != 1:
            raise MainFinalizationError(
                f"main judgment cell {cell_key} does not have one transcript dependency")
        transcript_result = transcript_results.get(str(dependency_keys[0]))
        if transcript_result is None:
            raise MainFinalizationError(
                f"main judgment result {cell_key} lacks its frozen transcript result")
        expected_calls, payloads = _validate_observed_judgment_semantics(
            cell=cell,
            result=result,
            transcript=transcript_result,
            verdict_journal_row=journal_row,
            decisions=reviewer_decisions,
        )
        result_reviewer_payloads.update(payloads)
        actual_identities = {
            identity for identity in journal_by_identity if identity[0] == cell_key
        }
        extras = actual_identities - set(expected_calls)
        queries_used = int(result["queries_used"])
        budget = int(cell["query_budget"])
        if queries_used < budget and len(extras) == 1:
            first_attempt_identity = (
                cell_key, JUDGE_QUERY_ROLE, queries_used, 1)
            done_attempt = 2 if first_attempt_identity in expected_calls else 1
            done_identity = (
                cell_key, JUDGE_QUERY_ROLE, queries_used, done_attempt)
            done_row = journal_by_identity.get(done_identity)
            if done_identity in extras and done_row is not None and oracle_channel.is_done_robust(
                    str(done_row.get("response"))):
                expected_calls[done_identity] = str(done_row["response"])
                extras.clear()
        if extras or set(expected_calls) != actual_identities:
            raise MainFinalizationError(
                f"main judgment result {cell_key} provider-call schedule drifted")
        for identity, expected_response in expected_calls.items():
            actual_journal = journal_by_identity[identity]
            if actual_journal.get("response") != expected_response:
                raise MainFinalizationError(
                    f"main judgment result {cell_key} provider response evidence drifted")
            successes = success_by_identity.get(identity, [])
            if len(successes) != 1:
                raise MainFinalizationError(
                    f"main judgment result {cell_key} call does not join exactly one "
                    "settled ledger success")
            metadata = successes[0].get("metadata")
            if not isinstance(metadata, Mapping):  # pragma: no cover - checked above
                raise MainFinalizationError("provider success metadata is missing")
            request_sha256 = _sha256(
                actual_journal.get("request_sha256"), "journal request SHA-256")
            if metadata.get(JOURNAL_REQUEST_SHA256_FIELD) != request_sha256:
                raise MainFinalizationError(
                    f"main judgment result {cell_key} ledger/journal request SHA differs")
        observed_judgments.append(cell_key)

    expected_reviewer_payloads: set[str] = set()
    for identity, journal_row in journal_by_identity.items():
        cell_key, role, _slot, _attempt = identity
        if role != JUDGE_QUERY_ROLE:
            continue
        response = str(journal_row.get("response"))
        if oracle_channel.is_done_robust(response):
            continue
        cell = judgments[cell_key]
        dependency_keys = cell.get("dependency_keys")
        transcript_result = (
            transcript_results.get(str(dependency_keys[0]))
            if isinstance(dependency_keys, Sequence) and len(dependency_keys) == 1
            else None
        )
        if transcript_result is None:
            raise MainFinalizationError(
                f"reviewed query for {cell_key} lacks its frozen transcript result")
        candidate_a, candidate_b = _candidate_pair(cell, transcript_result)
        expected_reviewer_payloads.add(
            payload_hash(response, candidate_a, candidate_b))
    if result_reviewer_payloads - expected_reviewer_payloads:
        raise MainFinalizationError("result gate events contain an unjournaled reviewer payload")
    if set(reviewer_decisions) != expected_reviewer_payloads:
        raise MainFinalizationError(
            "review decision store does not exactly cover the journaled main query payloads")

    verdict_success_count = sum(
        len(events) for identity, events in success_by_identity.items()
        if identity[1] == JUDGE_VERDICT_ROLE
    )
    if verdict_success_count != len(observed_judgments):
        raise MainFinalizationError(
            "judge_verdict ledger successes do not equal observed judgment results")
    try:
        transcript_verification = (
            phase3_main_transcript_provenance.verify_main_transcript_partition(
                result_rows=result_rows,
                provenance=transcript_provenance,
                result_store_raw_sha256=result_store_raw_sha256,
            )
        )
        replay = phase3_main_provider_provenance.verify_main_provider_replay(
            cells=inventory.cells,
            result_rows=result_rows,
            terminal_cell_keys=terminal,
            context_ineligible_cell_keys=context,
            protocol=protocol,
            prompt_bundle=prompt_bundle,
            role_limits=role_limits,
            review_decisions_path=review_decisions_path,
            journal_rows=journal_rows,
            ledger_events=ledger_events,
            authorization_approved_at_utc=authorization_approved_at_utc,
            authorization_valid_until_utc=authorization_valid_until_utc,
            finalization_recorded_at_utc=finalization_recorded_at_utc,
        )
    except (
        phase3_main_provider_provenance.MainProviderProvenanceError,
        phase3_main_transcript_provenance.MainTranscriptProvenanceError,
    ) as exc:
        raise MainFinalizationError(
            f"exact main provider reconstruction failed: {exc}") from exc
    return {
        "status": "exact_provider_join",
        "allowed_main_call_roles": sorted(ALLOWED_MAIN_CALL_ROLES),
        "observed_judgment_count": len(observed_judgments),
        "journaled_call_count": len(journal_rows),
        "settled_success_call_count": sum(len(rows) for rows in success_by_identity.values()),
        "verdict_journal_count": sum(
            len(rows) for rows in verdict_journal_by_cell.values()),
        "verdict_ledger_success_count": verdict_success_count,
        "result_store_raw_sha256": _sha256(
            result_store_raw_sha256, "result store raw SHA-256"),
        "request_journal_raw_sha256": _sha256(
            request_journal_raw_sha256, "request journal raw SHA-256"),
        "usage_ledger_raw_sha256": _sha256(
            usage_ledger_raw_sha256, "usage ledger raw SHA-256"),
        "normal_execution_replay_status": replay["status"],
        "replayed_judgment_count": replay["replayed_judgment_count"],
        "replayed_terminal_count": replay["replayed_terminal_count"],
        "logical_request_count": replay["logical_request_count"],
        "provider_request_count": replay["provider_request_count"],
        "redispatched_logical_call_count": replay["redispatched_logical_call_count"],
        "unknown_charge_episode_count": replay["unknown_charge_episode_count"],
        "unknown_charge_episodes_by_model": replay["unknown_charge_episodes_by_model"],
        "logical_request_hashes_sha256": replay[
            "logical_request_hashes_sha256"],
        "provider_request_hashes_sha256": replay[
            "provider_request_hashes_sha256"],
        "authorization_dispatch_status": replay[
            "authorization_dispatch_status"],
        "latest_logical_dispatch_at_utc": replay[
            "latest_logical_dispatch_at_utc"],
        "latest_provider_completion_at_utc": replay[
            "latest_provider_completion_at_utc"],
        "main_transcript_bundle_raw_sha256": (
            transcript_provenance.main_bundle_raw_sha256),
        "transcript_verification_raw_sha256": (
            transcript_provenance.transcript_verification_raw_sha256),
        "main_transcript_bundle_canonical_sha256": (
            transcript_provenance.main_bundle_canonical_sha256),
        "transcript_results_canonical_sha256": (
            transcript_verification.expected_results_canonical_sha256),
    }


def _derive_clean_reconciliation(
    *,
    usage_ledger_path: Path,
    request_journal_path: Path,
    journal_execution_identity: str,
    tolerate_unknown_charges: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    material = _load_stable_usage_ledger_material(usage_ledger_path)
    snapshot = material["snapshot"]
    events = material["events"]
    journal = RequestJournal(
        request_journal_path, execution_identity=journal_execution_identity)
    ambiguous = find_ambiguous_dispatches(journal, events)
    if (_read_stable_bytes(usage_ledger_path, "usage ledger")
            != material["ledger_raw"]
            or _read_stable_bytes(
                api_client.usage_ledger_state_path(usage_ledger_path),
                "usage ledger state",
            ) != material["state_raw"]):
        raise MainFinalizationError(
            "usage ledger changed during final reconciliation")
    unknown = sorted({
        str(event["attempt_id"]) for event in events
        if event.get("status") == "unknown_charge"
    })
    charged_malformed = sorted({
        str(event["attempt_id"]) for event in events
        if event.get("status") == "charged_malformed"
    })
    success_without_journal = sorted({
        str(item.get("attempt_id")) for item in ambiguous
        if item.get("problem") == "success_without_journal_entry"
    })
    marker_present = journal.unresolved_marker_path.exists()
    tolerated = [item for item in ambiguous if item.get("problem") == "unknown_charge"]
    fatal_findings = [item for item in ambiguous if item.get("problem") != "unknown_charge"]
    if tolerate_unknown_charges and {
        str(item.get("attempt_id")) for item in tolerated
    } != set(unknown):
        raise MainFinalizationError(
            "tolerated unknown-charge findings do not match the ledger's unknown charges")
    status = "clean"
    if tolerate_unknown_charges and tolerated:
        # Amendment 14: unobserved transport failures stay booked as uncertain spend and
        # are the only finding the frozen policy tolerates; every other finding is fatal.
        status = "clean_with_tolerated_unknown_charges"
    reconciliation = {
        "status": status,
        "ambiguous_dispatches": ambiguous,
        "unmatched_reservations": int(snapshot.summary["unmatched_reservations"]),
        "unknown_charge_attempt_ids": unknown,
        "charged_malformed_attempt_ids": charged_malformed,
        "settled_success_without_journal_attempt_ids": success_without_journal,
        "unresolved_dispatch_marker_present": marker_present,
    }
    blocked = (
        fatal_findings or reconciliation["unmatched_reservations"] != 0
        or charged_malformed or success_without_journal or marker_present
    )
    if not tolerate_unknown_charges:
        blocked = blocked or bool(ambiguous) or bool(unknown)
    if blocked:
        raise MainFinalizationError(
            "final ledger/journal reconciliation is not empty")
    return reconciliation, events


def _artifact_hashes(paths: Mapping[str, str | Path]) -> dict[str, Any]:
    missing = REQUIRED_ARTIFACTS - set(paths)
    unexpected = set(paths) - REQUIRED_ARTIFACTS
    if missing or unexpected:
        raise MainFinalizationError(
            "final artifact mapping fields drifted: "
            f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}")
    result: dict[str, Any] = {}
    canonical_paths: set[str] = set()
    for label in sorted(paths):
        _text(label, "artifact label")
        path = Path(paths[label]).resolve()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise MainFinalizationError(
                f"could not read final artifact {label}: {path}") from exc
        canonical = path.as_posix()
        if canonical in canonical_paths:
            raise MainFinalizationError("two final artifact labels name the same path")
        canonical_paths.add(canonical)
        result[label] = {
            "path": canonical,
            "raw_sha256": _sha256_bytes(raw),
            "bytes": len(raw),
        }
    return result


def _bound_artifact_root(
    paths: Mapping[str, str | Path], *, result_path: Path, run_id: str,
    manifest_sha256: str, journal_execution_identity: str,
) -> dict[str, str]:
    missing = REQUIRED_ARTIFACTS - set(paths)
    unexpected = set(paths) - REQUIRED_ARTIFACTS
    if missing or unexpected:
        raise MainFinalizationError(
            "final artifact mapping fields drifted: "
            f"missing={sorted(missing)!r}, unexpected={sorted(unexpected)!r}")
    root = result_path.resolve().parent
    for label in ROOT_BOUND_ARTIFACTS:
        path = Path(paths[label]).resolve()
        if path.parent != root:
            raise MainFinalizationError(
                f"main output artifact {label} is outside the result artifact root")
    root_sha256 = _sha256_text(root.as_posix())
    expected_identity = f"{run_id}:{manifest_sha256}:{root_sha256}"
    if journal_execution_identity != expected_identity:
        raise MainFinalizationError(
            "request-journal execution identity is not bound to the main artifact root")
    return {
        "artifact_root": root.as_posix(),
        "artifact_root_sha256": root_sha256,
        "journal_execution_identity": expected_identity,
    }


def _uncertain_spend_policy_section(
    policy: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if policy is None:
        return None
    _ceiling_text, ceiling = _exact_decimal(
        format(Decimal(str(policy["run_uncertain_ceiling_usd"])), "f"),
        "run_uncertain_ceiling_usd")
    return {
        "policy_id": _text(policy["policy_id"], "uncertain_spend_policy.policy_id"),
        "policy_raw_sha256": _sha256(
            policy["policy_raw_sha256"], "uncertain_spend_policy.policy_raw_sha256"),
        "run_uncertain_ceiling_usd": _decimal_text(ceiling),
        "tolerated_finding": _text(
            policy["tolerated_finding"], "uncertain_spend_policy.tolerated_finding"),
        "unknown_charge_pass_allowance": _non_negative_int(
            policy["unknown_charge_pass_allowance"],
            "uncertain_spend_policy.unknown_charge_pass_allowance"),
        "abandoned_rate_cooldown_seconds": _non_negative_int(
            policy["abandoned_rate_cooldown_seconds"],
            "uncertain_spend_policy.abandoned_rate_cooldown_seconds"),
        "abandoned_rate_consecutive_pass_allowance": _non_negative_int(
            policy["abandoned_rate_consecutive_pass_allowance"],
            "uncertain_spend_policy.abandoned_rate_consecutive_pass_allowance"),
        "read_timeout_seconds": _non_negative_int(
            policy["read_timeout_seconds"], "uncertain_spend_policy.read_timeout_seconds"),
    }


def _retry_report(
    *,
    ledger_events: Sequence[Mapping[str, Any]],
    inventory: MainInventory,
    provenance: Mapping[str, Any],
    accounting: Mapping[str, Any],
    policy: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Report unobserved-transport-failure redispatches by model, condition, and question.

    Amendment 14: results apply to the declared timeout and retry procedure, so the
    redispatch pattern is part of the record rather than a hidden operational detail.
    """
    if policy is None:
        return None
    _transcripts, judgments = _inventory_index(inventory)
    by_condition: dict[str, int] = {}
    by_question: dict[str, int] = {}
    by_role: dict[str, int] = {}
    event_count = 0
    for event_number, event in enumerate(ledger_events):
        if event.get("status") != "unknown_charge":
            continue
        event_count += 1
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping):
            raise MainFinalizationError(
                f"unknown-charge ledger event {event_number} has no metadata")
        cell = judgments.get(str(metadata.get("cell_key")))
        if cell is None:
            raise MainFinalizationError(
                f"unknown-charge ledger event {event_number} names an unplanned cell")
        condition = _text(cell.get("condition"), "inventory cell condition")
        question = _text(cell.get("question_id"), "inventory cell question_id")
        role = _text(metadata.get("call_role"), "unknown-charge call_role")
        by_condition[condition] = by_condition.get(condition, 0) + 1
        by_question[question] = by_question.get(question, 0) + 1
        by_role[role] = by_role.get(role, 0) + 1
    if event_count != _non_negative_int(
            provenance["unknown_charge_episode_count"],
            "provider_provenance.unknown_charge_episode_count"):
        raise MainFinalizationError(
            "unknown-charge ledger events differ from the replayed failed episodes")
    if event_count != _non_negative_int(
            accounting["unknown_charge_attempt_count"],
            "accounting.unknown_charge_attempt_count"):
        raise MainFinalizationError(
            "unknown-charge ledger events differ from the accounting count")
    return {
        "unknown_charge_events": event_count,
        "redispatched_logical_calls": _non_negative_int(
            provenance["redispatched_logical_call_count"],
            "provider_provenance.redispatched_logical_call_count"),
        "uncertain_usd": accounting["current_uncertain_usd"],
        "by_model": dict(accounting["unknown_charge_by_model"]),
        "by_role": dict(sorted(by_role.items())),
        "by_condition": dict(sorted(by_condition.items())),
        "by_question": dict(sorted(by_question.items())),
        "procedure": (
            "unobserved transport failures (read timeout "
            f"{int(policy['read_timeout_seconds'])} s, per-call wall clock, HTTP error "
            "after dispatch) are booked as uncertain spend and redispatched under a new "
            "attempt id with the same request, seed, and journaled upstream history; "
            "bounded by the "
            f"{_decimal_text(Decimal(str(policy['run_uncertain_ceiling_usd'])))} "
            "USD per-identity ceiling; results apply to this declared procedure"),
    }


def build_finalization_admission(
    *,
    run_id: str,
    manifest_canonical_sha256: str,
    authorization_canonical_sha256: str,
    authorization_raw_sha256: str,
    authorization_signature_raw_sha256: str,
    inventory: MainInventory,
    context_blocklist_path: str | Path,
    terminal_store: MainTerminalDispositionStore,
    result_store_path: str | Path,
    usage_ledger_path: str | Path,
    request_journal_path: str | Path,
    journal_execution_identity: str,
    analysis_pins_path: str | Path,
    provider_input_paths: Mapping[str, str | Path],
    provider_input_raw_sha256s: Mapping[str, str],
    reviewer_input_paths: Mapping[str, str | Path],
    reviewer_input_raw_sha256s: Mapping[str, str],
    capacity_result_path: str | Path,
    expected_capacity_result_raw_sha256: str,
    capacity_dispatch_history_path: str | Path,
    expected_capacity_dispatch_history_raw_sha256: str,
    review_packets_root_path: str | Path,
    artifact_paths: Mapping[str, str | Path],
    expected_oracle_model: str,
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    authorization_approved_at_utc: str,
    authorization_valid_until_utc: str,
    prior_reconciled_usd: str,
    stage_cap_usd: str,
    recorded_at_utc: str,
    uncertain_spend_policy: Mapping[str, Any] | None = None,
    voided_predecessor_usd: str = "0",
) -> dict[str, Any]:
    """Build the pre-analysis admission from final immutable main artifacts."""
    run_id = _text(run_id, "run_id")
    manifest_sha = _sha256(
        manifest_canonical_sha256, "manifest_canonical_sha256")
    authorization_sha = _sha256(
        authorization_canonical_sha256, "authorization_canonical_sha256")
    authorization_raw_sha = _sha256(
        authorization_raw_sha256, "authorization_raw_sha256")
    authorization_signature_raw_sha = _sha256(
        authorization_signature_raw_sha256,
        "authorization_signature_raw_sha256",
    )
    recorded_at_utc = _utc(recorded_at_utc, "recorded_at_utc")
    authorization_approved_at_utc = _utc(
        authorization_approved_at_utc, "authorization_approved_at_utc")
    inventory_sha = inventory_canonical_sha256(inventory)
    _inventory_index(inventory)
    if (terminal_store.run_id != run_id
            or terminal_store.manifest_canonical_sha256 != manifest_sha
            or terminal_store.inventory_canonical_sha256 != inventory_sha):
        raise MainFinalizationError("terminal store belongs to another main identity")

    context_path = Path(context_blocklist_path).resolve()
    context_blocklist, context_raw = _read_json(context_path, "context blocklist")
    context_sha = _sha256_bytes(context_raw)
    pins_path = Path(analysis_pins_path).resolve()
    pins, _pins_raw = _read_json(pins_path, "analysis pins")
    pins_sha = canonical_sha256(pins)
    if pins_sha != FROZEN_ANALYSIS_PINS_CANONICAL_SHA256:
        raise MainFinalizationError(
            "analysis pins differ from the frozen main analysis contract")
    provider_inputs, provider_input_raw = _load_bound_provider_inputs(
        provider_input_paths, provider_input_raw_sha256s)
    reviewer_inputs, reviewer_input_raw = _load_bound_reviewer_inputs(
        reviewer_input_paths, reviewer_input_raw_sha256s)
    capacity_evidence_paths, capacity_evidence_raw = _load_bound_capacity_evidence(
        capacity_result_path=capacity_result_path,
        expected_capacity_result_raw_sha256=expected_capacity_result_raw_sha256,
        capacity_dispatch_history_path=capacity_dispatch_history_path,
        expected_capacity_dispatch_history_raw_sha256=(
            expected_capacity_dispatch_history_raw_sha256),
    )
    expected_reviewer_model = _text(
        expected_reviewer_model, "expected_reviewer_model")
    expected_reviewer_reasoning_effort = _text(
        expected_reviewer_reasoning_effort,
        "expected_reviewer_reasoning_effort",
    )
    expected_reviewer_concurrency = _positive_int(
        expected_reviewer_concurrency, "expected_reviewer_concurrency")
    authorization_valid_until_utc = _utc(
        authorization_valid_until_utc, "authorization_valid_until_utc")
    approved_at = _utc_datetime(
        authorization_approved_at_utc, "authorization_approved_at_utc")
    finalized_at = _utc_datetime(recorded_at_utc, "recorded_at_utc")
    valid_until = _utc_datetime(
        authorization_valid_until_utc, "authorization_valid_until_utc")
    if valid_until <= approved_at:
        raise MainFinalizationError(
            "signed authorization dispatch window is empty")
    if finalized_at < approved_at:
        raise MainFinalizationError(
            "finalization predates the signed authorization")
    try:
        transcript_provenance = (
            phase3_main_transcript_provenance.
            load_manifest_bound_main_transcript_provenance(
                inventory=inventory,
                main_bundle_path=provider_input_paths["main_transcript_bundle"],
                transcript_verification_path=provider_input_paths[
                    "transcript_verification"],
                expected_main_bundle_raw_sha256=provider_input_raw_sha256s[
                    "main_transcript_bundle"],
                expected_transcript_verification_raw_sha256=(
                    provider_input_raw_sha256s["transcript_verification"]),
            )
        )
    except phase3_main_transcript_provenance.MainTranscriptProvenanceError as exc:
        raise MainFinalizationError(
            f"main transcript provenance failed: {exc}") from exc

    result_path = Path(result_store_path).resolve()
    ledger_path = Path(usage_ledger_path).resolve()
    journal_path = Path(request_journal_path).resolve()
    journal_execution_identity = _text(
        journal_execution_identity, "journal_execution_identity")
    root_binding = _bound_artifact_root(
        artifact_paths,
        result_path=result_path,
        run_id=run_id,
        manifest_sha256=manifest_sha,
        journal_execution_identity=journal_execution_identity,
    )
    review_packets_root = Path(review_packets_root_path)
    if not review_packets_root.is_absolute():
        raise MainFinalizationError(
            "review packet root path must be absolute")
    review_packets_root = review_packets_root.resolve()
    if review_packets_root.parent != result_path.parent:
        raise MainFinalizationError(
            "review packet root is outside the main artifact root")
    result_rows, result_raw = _load_result_rows_material(result_path)
    result_keys = tuple(str(row["cell_key"]) for row in result_rows)
    terminal_store.verify_all_evidence(
        usage_ledger_path=ledger_path,
        request_journal_path=journal_path,
        journal_execution_identity=journal_execution_identity,
        result_cell_keys=result_keys,
    )
    partition = validate_main_partition(
        inventory=inventory,
        result_cell_keys=result_keys,
        terminal_records=terminal_store.records,
        context_blocklist=context_blocklist,
    )
    bounds = evaluate_terminal_bounds(
        inventory=inventory, terminal_records=terminal_store.records)
    ledger_material = _load_stable_usage_ledger_material(ledger_path)
    journal_rows, journal_raw = _load_stable_journal_material(
        journal_path, execution_identity=journal_execution_identity)
    reviewer_decisions, reviewer_decisions_raw = _load_reviewer_decisions(
        artifact_paths["review_decisions"])
    reconciliation, ledger_events = _derive_clean_reconciliation(
        usage_ledger_path=ledger_path,
        request_journal_path=journal_path,
        journal_execution_identity=journal_execution_identity,
        tolerate_unknown_charges=uncertain_spend_policy is not None,
    )
    if ([event.get("event_hash") for event in ledger_events]
            != [event.get("event_hash") for event in ledger_material["events"]]
            or _read_stable_bytes(journal_path, "request journal") != journal_raw):
        raise MainFinalizationError(
            "ledger or journal changed during final reconciliation")
    provenance = _validate_main_provider_provenance(
        inventory=inventory,
        result_rows=result_rows,
        terminal_records=terminal_store.records,
        context_ineligible_cell_keys=partition["context_ineligible_cell_keys"],
        ledger_events=ledger_material["events"],
        journal_rows=journal_rows,
        reviewer_decisions=reviewer_decisions,
        review_decisions_path=artifact_paths["review_decisions"],
        protocol=provider_inputs["protocol"],
        prompt_bundle=provider_inputs["prompt_bundle"],
        role_limits=provider_inputs["role_limits"],
        transcript_provenance=transcript_provenance,
        expected_checker_model=terminal_store.checker_model,
        expected_oracle_model=expected_oracle_model,
        result_store_raw_sha256=_sha256_bytes(result_raw),
        request_journal_raw_sha256=_sha256_bytes(journal_raw),
        usage_ledger_raw_sha256=ledger_material["ledger_raw_sha256"],
        authorization_approved_at_utc=authorization_approved_at_utc,
        authorization_valid_until_utc=authorization_valid_until_utc,
        finalization_recorded_at_utc=recorded_at_utc,
    )
    provenance = {
        **provenance,
        "protocol_raw_sha256": _sha256_bytes(provider_input_raw["protocol"]),
        "prompt_bundle_raw_sha256": _sha256_bytes(
            provider_input_raw["prompt_bundle"]),
        "role_limits_raw_sha256": _sha256_bytes(provider_input_raw["role_limits"]),
    }
    capacity_configuration = reviewer_inputs["capacity_plan"].get(
        "reviewer_configuration")
    if not isinstance(capacity_configuration, Mapping):
        raise MainFinalizationError(
            "capacity plan omits reviewer_configuration")
    expected_reviewer_cli_path = _text(
        capacity_configuration.get("reviewer_cli_resolved_path"),
        "capacity plan reviewer CLI path",
    )
    try:
        from rejudge import phase3_main_reviewer_recovery
        reviewer_recovery = phase3_main_reviewer_recovery.recovery_from_run_log(
            artifact_paths["run_log"], expected_run_id=run_id,
            expected_manifest_sha256=manifest_sha)
        reviewer_provenance = (
            phase3_main_reviewer_provenance.verify_main_reviewer_provenance(
                reviewer_index_path=artifact_paths["reviewer_index"],
                **({"reviewer_recovery": reviewer_recovery} if reviewer_recovery is not None else {}),
                reviewer_worklist_path=artifact_paths["reviewer_worklist"],
                review_packets_root=review_packets_root,
                reviewer_prompt_path=reviewer_input_paths["reviewer_prompt"],
                expected_reviewer_prompt_raw_sha256=(
                    reviewer_input_raw_sha256s["reviewer_prompt"]),
                reviewer_failure_policy_path=(
                    reviewer_input_paths["reviewer_failure_policy"]),
                expected_reviewer_failure_policy_raw_sha256=(
                    reviewer_input_raw_sha256s["reviewer_failure_policy"]),
                capacity_plan_path=reviewer_input_paths["capacity_plan"],
                expected_capacity_plan_raw_sha256=(
                    reviewer_input_raw_sha256s["capacity_plan"]),
                capacity_result_path=capacity_evidence_paths["capacity_result"],
                expected_capacity_result_raw_sha256=(
                    expected_capacity_result_raw_sha256),
                capacity_dispatch_history_path=(
                    capacity_evidence_paths["capacity_dispatch_history"]),
                expected_capacity_dispatch_history_raw_sha256=(
                    expected_capacity_dispatch_history_raw_sha256),
                expected_run_id=run_id,
                expected_manifest_canonical_sha256=manifest_sha,
                expected_authorization_canonical_sha256=authorization_sha,
                expected_authorization_raw_sha256=authorization_raw_sha,
                expected_authorization_signature_raw_sha256=(
                    authorization_signature_raw_sha),
                expected_reviewer_model=expected_reviewer_model,
                expected_reviewer_reasoning_effort=(
                    expected_reviewer_reasoning_effort),
                expected_reviewer_concurrency=expected_reviewer_concurrency,
                expected_reviewer_cli_resolved_path=(
                    expected_reviewer_cli_path),
                expected_authorization_approved_at_utc=(
                    authorization_approved_at_utc),
                expected_authorization_deadline_utc=(
                    authorization_valid_until_utc),
                expected_finalization_recorded_at_utc=recorded_at_utc,
                max_passes=_reviewer_max_passes(
                    reviewer_inputs["capacity_plan"]) + (
                    _non_negative_int(
                        uncertain_spend_policy["unknown_charge_pass_allowance"],
                        "uncertain_spend_policy.unknown_charge_pass_allowance")
                    if uncertain_spend_policy is not None else 0),
                decisions_path=artifact_paths["review_decisions"],
                # The provider reconstruction above independently proves that
                # these are exactly the non-DONE journaled query payloads.
                expected_reviewed_payload_sha256s=set(reviewer_decisions),
            )
        )
    except phase3_main_reviewer_provenance.MainReviewerProvenanceError as exc:
        raise MainFinalizationError(
            f"main reviewer provenance failed: {exc}") from exc
    accounting = _accounting_section(
        ledger_material,
        prior_reconciled_usd=prior_reconciled_usd,
        stage_cap_usd=stage_cap_usd,
        uncertain_spend_policy=uncertain_spend_policy,
        voided_predecessor_usd=voided_predecessor_usd,
    )
    policy_section = _uncertain_spend_policy_section(uncertain_spend_policy)
    retry_report = _retry_report(
        ledger_events=ledger_events,
        inventory=inventory,
        provenance=provenance,
        accounting=accounting,
        policy=uncertain_spend_policy,
    )
    diagnostic = checker_truncation_diagnostic(
        inventory=inventory,
        ledger_events=ledger_events,
        terminal_records=terminal_store.records,
    )
    artifacts = _artifact_hashes(artifact_paths)

    expected_paths = {
        "result_store": result_path,
        "usage_ledger": ledger_path,
        "usage_ledger_state": api_client.usage_ledger_state_path(ledger_path).resolve(),
        "request_journal": journal_path,
        "context_blocklist": context_path,
        "terminal_dispositions": terminal_store.path,
        "analysis_pins": pins_path,
    }
    for label, path in expected_paths.items():
        if artifacts[label]["path"] != path.as_posix():
            raise MainFinalizationError(
                f"artifact mapping {label} does not name the validated path")
    if artifacts["context_blocklist"]["raw_sha256"] != context_sha:
        raise MainFinalizationError("context blocklist artifact hash drifted")
    if artifacts["terminal_dispositions"]["raw_sha256"] != terminal_store.tail[
            "raw_sha256"]:
        raise MainFinalizationError("terminal disposition artifact hash drifted")
    if artifacts["result_store"]["raw_sha256"] != provenance[
            "result_store_raw_sha256"]:
        raise MainFinalizationError("result store changed after provenance validation")
    if artifacts["request_journal"]["raw_sha256"] != provenance[
            "request_journal_raw_sha256"]:
        raise MainFinalizationError("request journal changed after provenance validation")
    if artifacts["review_decisions"]["raw_sha256"] != _sha256_bytes(
            reviewer_decisions_raw):
        raise MainFinalizationError(
            "review decision store changed after semantic validation")
    for artifact_name, provenance_field in (
        ("reviewer_index", "reviewer_index_raw_sha256"),
        ("reviewer_worklist", "reviewer_worklist_raw_sha256"),
    ):
        if artifacts[artifact_name]["raw_sha256"] != reviewer_provenance[
                provenance_field]:
            raise MainFinalizationError(
                f"{artifact_name.replace('_', ' ')} changed after reviewer "
                "provenance validation")
    if artifacts["usage_ledger"]["raw_sha256"] != accounting[
            "usage_ledger_raw_sha256"]:
        raise MainFinalizationError("usage ledger changed after exact accounting")
    if artifacts["usage_ledger_state"]["raw_sha256"] != accounting[
            "usage_ledger_state_raw_sha256"]:
        raise MainFinalizationError("usage ledger state changed after exact accounting")
    _require_bound_provider_inputs_unchanged(
        provider_input_paths, provider_input_raw)
    _require_bound_reviewer_inputs_unchanged(
        reviewer_input_paths, reviewer_input_raw)
    _require_bound_capacity_evidence_unchanged(
        capacity_evidence_paths, capacity_evidence_raw)
    for artifact_name, provenance_field in (
        ("reviewer_index", "reviewer_index_raw_sha256"),
        ("reviewer_worklist", "reviewer_worklist_raw_sha256"),
    ):
        current_raw = _read_stable_bytes(
            Path(artifacts[artifact_name]["path"]),
            f"{artifact_name.replace('_', ' ')} final snapshot",
        )
        if _sha256_bytes(current_raw) != reviewer_provenance[provenance_field]:
            raise MainFinalizationError(
                f"{artifact_name.replace('_', ' ')} changed after reviewer "
                "provenance validation")
    try:
        current_review_tree_sha = (
            phase3_main_reviewer_provenance.
            review_packets_tree_canonical_sha256(review_packets_root)
        )
    except phase3_main_reviewer_provenance.MainReviewerProvenanceError as exc:
        raise MainFinalizationError(
            f"review packet tree became invalid after provenance validation: {exc}") from exc
    if current_review_tree_sha != reviewer_provenance[
            "review_packets_tree_canonical_sha256"]:
        raise MainFinalizationError(
            "review packet tree changed after reviewer provenance validation")

    return {
        "schema_version": FINALIZATION_SCHEMA,
        "stage": "main",
        "status": FINALIZATION_STATUS,
        "run_id": run_id,
        "recorded_at_utc": recorded_at_utc,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
        "bindings": {
            "manifest_canonical_sha256": manifest_sha,
            "authorization_canonical_sha256": authorization_sha,
            "authorization_raw_sha256": authorization_raw_sha,
            "authorization_signature_raw_sha256": authorization_signature_raw_sha,
            "inventory_canonical_sha256": inventory_sha,
            "context_blocklist_raw_sha256": context_sha,
            "analysis_pins_canonical_sha256": pins_sha,
            **root_binding,
        },
        "terminal_store": terminal_store.tail,
        "partition": partition,
        "terminal_bounds": bounds,
        "checker_truncation_diagnostic": diagnostic,
        "provider_provenance": provenance,
        "reviewer_provenance": reviewer_provenance,
        "accounting": accounting,
        "artifact_hashes": artifacts,
        "reconciliation": reconciliation,
        "uncertain_spend_policy": policy_section,
        "retry_report": retry_report,
    }


def validate_finalization_admission(
    record: Mapping[str, Any],
    **build_inputs: Any,
) -> dict[str, Any]:
    """Rebuild a finalization admission from its artifacts and require exact equality."""
    _exact_keys(record, FINALIZATION_FIELDS, "finalization record")
    if (record.get("schema_version") != FINALIZATION_SCHEMA
            or record.get("stage") != "main"
            or record.get("status") != FINALIZATION_STATUS):
        raise MainFinalizationError("unsupported main finalization record")
    if any(record.get(field) is not False for field in (
            "execution_authorized", "provider_calls_authorized",
            "main_run_spend_authorized")):
        raise MainFinalizationError(
            "finalization admission is evidence only and cannot authorize execution")
    _exact_keys(record.get("bindings"), BINDING_FIELDS, "finalization bindings")
    bindings = record["bindings"]
    for field in (
        "manifest_canonical_sha256",
        "authorization_canonical_sha256",
        "authorization_raw_sha256",
        "authorization_signature_raw_sha256",
        "inventory_canonical_sha256",
        "context_blocklist_raw_sha256",
        "analysis_pins_canonical_sha256",
        "artifact_root_sha256",
    ):
        _sha256(bindings[field], f"finalization bindings.{field}")
    _text(bindings["artifact_root"], "finalization bindings.artifact_root")
    _text(
        bindings["journal_execution_identity"],
        "finalization bindings.journal_execution_identity",
    )

    terminal_tail = _exact_keys(
        record.get("terminal_store"), TERMINAL_TAIL_FIELDS, "terminal_store")
    _text(terminal_tail["path"], "terminal_store.path")
    _sha256(terminal_tail["raw_sha256"], "terminal_store.raw_sha256")
    record_count = _non_negative_int(
        terminal_tail["record_count"], "terminal_store.record_count")
    last_sequence = terminal_tail["last_sequence"]
    if (isinstance(last_sequence, bool) or not isinstance(last_sequence, int)
            or last_sequence < -1 or last_sequence != record_count - 1):
        raise MainFinalizationError(
            "terminal_store.last_sequence must equal record_count minus one")
    _sha256(terminal_tail["last_event_hash"], "terminal_store.last_event_hash")

    reconciliation = _exact_keys(
        record.get("reconciliation"), RECONCILIATION_FIELDS, "reconciliation")
    policy_section = record.get("uncertain_spend_policy")
    if policy_section is not None and not isinstance(policy_section, Mapping):
        raise MainFinalizationError("uncertain_spend_policy must be an object or null")
    tolerated_status = "clean_with_tolerated_unknown_charges"
    allowed_statuses = {"clean"} | ({tolerated_status} if policy_section else set())
    if reconciliation["status"] not in allowed_statuses:
        raise MainFinalizationError("final reconciliation status must be clean")
    if _non_negative_int(
            reconciliation["unmatched_reservations"],
            "reconciliation.unmatched_reservations") != 0:
        raise MainFinalizationError("final reconciliation has unmatched reservations")
    for field in (
        "charged_malformed_attempt_ids",
        "settled_success_without_journal_attempt_ids",
    ):
        value = reconciliation[field]
        if not isinstance(value, list) or value:
            raise MainFinalizationError(
                f"final reconciliation {field} must be an empty list")
    ambiguous = reconciliation["ambiguous_dispatches"]
    unknown_ids = reconciliation["unknown_charge_attempt_ids"]
    if not isinstance(ambiguous, list) or not isinstance(unknown_ids, list):
        raise MainFinalizationError("final reconciliation finding lists are malformed")
    if reconciliation["status"] == "clean":
        if ambiguous or unknown_ids:
            raise MainFinalizationError(
                "final reconciliation ambiguous_dispatches must be an empty list")
    else:
        if not unknown_ids or not ambiguous:
            raise MainFinalizationError(
                "tolerated reconciliation status requires unknown-charge findings")
        if any(
            not isinstance(item, Mapping) or item.get("problem") != "unknown_charge"
            for item in ambiguous
        ):
            raise MainFinalizationError(
                "final reconciliation tolerates only unknown_charge findings")
        if {str(item.get("attempt_id")) for item in ambiguous} != set(unknown_ids):
            raise MainFinalizationError(
                "tolerated findings do not match the unknown-charge attempt ids")
    if reconciliation["unresolved_dispatch_marker_present"] is not False:
        raise MainFinalizationError(
            "final reconciliation has an unresolved dispatch marker")

    provenance = _exact_keys(
        record.get("provider_provenance"),
        PROVIDER_PROVENANCE_FIELDS,
        "provider_provenance",
    )
    if provenance["status"] != "exact_provider_join":
        raise MainFinalizationError("provider provenance status is not exact")
    if provenance["normal_execution_replay_status"] != "exact_normal_execution_replay":
        raise MainFinalizationError(
            "provider provenance normal-execution replay status is not exact")
    if provenance["allowed_main_call_roles"] != sorted(ALLOWED_MAIN_CALL_ROLES):
        raise MainFinalizationError("provider provenance call roles drifted")
    for field in (
        "observed_judgment_count",
        "journaled_call_count",
        "settled_success_call_count",
        "verdict_journal_count",
        "verdict_ledger_success_count",
        "replayed_judgment_count",
        "replayed_terminal_count",
        "logical_request_count",
        "provider_request_count",
    ):
        _non_negative_int(provenance[field], f"provider_provenance.{field}")
    for field in (
        "result_store_raw_sha256",
        "request_journal_raw_sha256",
        "usage_ledger_raw_sha256",
        "logical_request_hashes_sha256",
        "provider_request_hashes_sha256",
        "protocol_raw_sha256",
        "prompt_bundle_raw_sha256",
        "role_limits_raw_sha256",
        "main_transcript_bundle_raw_sha256",
        "transcript_verification_raw_sha256",
        "main_transcript_bundle_canonical_sha256",
        "transcript_results_canonical_sha256",
    ):
        _sha256(provenance[field], f"provider_provenance.{field}")

    reviewer_provenance = _exact_keys(
        record.get("reviewer_provenance"),
        REVIEWER_PROVENANCE_FIELDS,
        "reviewer_provenance",
    )
    if (
        reviewer_provenance["reviewer_provenance_status"]
        != "reviewer_provenance_verified"
    ):
        raise MainFinalizationError(
            "reviewer provenance status is not verified")
    for field in (
        "reviewer_wave_count",
        "reviewed_payload_count",
        "parsed_decision_count",
        "malformed_decision_count",
        "reviewer_error_decision_count",
    ):
        _non_negative_int(
            reviewer_provenance[field], f"reviewer_provenance.{field}")
    if reviewer_provenance["reviewed_payload_count"] != sum(
        int(reviewer_provenance[field])
        for field in (
            "parsed_decision_count",
            "malformed_decision_count",
            "reviewer_error_decision_count",
        )
    ):
        raise MainFinalizationError(
            "reviewer provenance decision counts do not cover reviewed payloads")
    _text(
        reviewer_provenance["review_packets_root"],
        "reviewer_provenance.review_packets_root",
    )
    for field in (
        "review_packets_tree_canonical_sha256",
        "reviewer_index_raw_sha256",
        "reviewer_worklist_raw_sha256",
        "reviewer_prompt_raw_sha256",
        "reviewer_failure_policy_raw_sha256",
        "capacity_plan_raw_sha256",
        "capacity_result_raw_sha256",
        "capacity_dispatch_history_raw_sha256",
    ):
        _sha256(
            reviewer_provenance[field], f"reviewer_provenance.{field}")

    accounting = _exact_keys(
        record.get("accounting"), ACCOUNTING_FIELDS, "accounting")
    amounts: dict[str, Decimal] = {}
    for field in (
        "prior_reconciled_usd",
        "voided_predecessor_usd",
        "current_settled_usd",
        "current_uncertain_usd",
        "current_accounted_usd",
        "stage_total_usd",
        "stage_cap_usd",
    ):
        _text_value, amounts[field] = _exact_decimal(
            accounting[field], f"accounting.{field}")
    if policy_section is None:
        if amounts["current_uncertain_usd"] != 0:
            raise MainFinalizationError("final accounting uncertainty must be zero")
        if accounting["run_uncertain_ceiling_usd"] is not None:
            raise MainFinalizationError(
                "final accounting names a ceiling without a bound policy")
    else:
        _ceiling_text, ceiling = _exact_decimal(
            accounting["run_uncertain_ceiling_usd"], "accounting.run_uncertain_ceiling_usd")
        if _decimal_text(ceiling) != policy_section["run_uncertain_ceiling_usd"]:
            raise MainFinalizationError(
                "final accounting ceiling differs from the bound policy")
        if amounts["current_uncertain_usd"] > ceiling:
            raise MainFinalizationError(
                "final accounting uncertainty exceeds the frozen policy ceiling")
    if accounting["uncertain_within_ceiling"] is not True:
        raise MainFinalizationError("final accounting must be within the uncertain ceiling")
    expected_label = (
        "PASS_CLEAN" if amounts["current_uncertain_usd"] == 0
        else "PASS_CONSERVATIVE_UNCERTAIN")
    if accounting["completion_label"] != expected_label:
        raise MainFinalizationError("final completion label is inconsistent")
    if _non_negative_int(
            accounting["unknown_charge_attempt_count"],
            "accounting.unknown_charge_attempt_count") != len(unknown_ids):
        raise MainFinalizationError(
            "final accounting unknown-charge count differs from the reconciliation")
    if accounting["within_stage_cap"] is not True:
        raise MainFinalizationError("final accounting must be within the stage cap")
    if amounts["current_accounted_usd"] != (
            amounts["current_settled_usd"] + amounts["current_uncertain_usd"]):
        raise MainFinalizationError("final current accounted spend is inconsistent")
    if amounts["stage_total_usd"] != (
            amounts["prior_reconciled_usd"] + amounts["voided_predecessor_usd"]
            + amounts["current_accounted_usd"]):
        raise MainFinalizationError("final stage total spend is inconsistent")
    if amounts["stage_total_usd"] > amounts["stage_cap_usd"]:
        raise MainFinalizationError("final stage total exceeds its cap")
    for field in (
        "usage_ledger_raw_sha256",
        "usage_ledger_state_raw_sha256",
        "usage_ledger_tail_event_hash",
    ):
        _sha256(accounting[field], f"accounting.{field}")
    _text(accounting["usage_ledger_id"], "accounting.usage_ledger_id")
    _non_negative_int(
        accounting["usage_ledger_tail_sequence"],
        "accounting.usage_ledger_tail_sequence",
    )
    artifacts = record.get("artifact_hashes")
    if not isinstance(artifacts, Mapping):
        raise MainFinalizationError("artifact_hashes must be an object")
    if set(artifacts) != REQUIRED_ARTIFACTS:
        raise MainFinalizationError(
            "finalization artifact_hashes must name the exact required artifact set")
    for label, binding in artifacts.items():
        _text(label, "artifact label")
        _exact_keys(binding, ARTIFACT_BINDING_FIELDS, f"artifact_hashes.{label}")
        _text(binding["path"], f"artifact_hashes.{label}.path")
        _sha256(binding["raw_sha256"], f"artifact_hashes.{label}.raw_sha256")
        _non_negative_int(binding["bytes"], f"artifact_hashes.{label}.bytes")

    expected_inputs = dict(build_inputs)
    expected_inputs["recorded_at_utc"] = _utc(
        record.get("recorded_at_utc"), "recorded_at_utc")
    expected = build_finalization_admission(**expected_inputs)
    if dict(record) != expected:
        raise MainFinalizationError(
            "finalization record differs from the recomputed artifact admission")
    return expected


def validate_finalization_from_bound_artifacts(
    record: Mapping[str, Any],
    *,
    inventory: MainInventory,
    expected_run_id: str,
    expected_manifest_canonical_sha256: str,
    expected_authorization_canonical_sha256: str,
    expected_authorization_raw_sha256: str,
    expected_authorization_signature_raw_sha256: str,
    authorization_approved_at_utc: str,
    authorization_valid_until_utc: str,
    expected_result_store_path: str | Path,
    expected_analysis_pins_path: str | Path,
    expected_context_blocklist_path: str | Path,
    expected_manifest_output_paths: Mapping[str, str | Path],
    expected_provider_input_paths: Mapping[str, str | Path],
    expected_provider_input_raw_sha256s: Mapping[str, str],
    expected_reviewer_input_paths: Mapping[str, str | Path],
    expected_reviewer_input_raw_sha256s: Mapping[str, str],
    expected_capacity_result_path: str | Path,
    expected_capacity_result_raw_sha256: str,
    expected_capacity_dispatch_history_path: str | Path,
    expected_capacity_dispatch_history_raw_sha256: str,
    expected_review_packets_root_path: str | Path,
    expected_checker_model: str,
    expected_oracle_model: str,
    expected_reviewer_model: str,
    expected_reviewer_reasoning_effort: str,
    expected_reviewer_concurrency: int,
    prior_reconciled_usd: str,
    stage_cap_usd: str,
) -> dict[str, Any]:
    """Reopen every bound artifact and perform the full finalization admission again."""
    _exact_keys(record, FINALIZATION_FIELDS, "finalization record")
    bindings = _exact_keys(
        record.get("bindings"), BINDING_FIELDS, "finalization bindings")
    artifacts = record.get("artifact_hashes")
    if not isinstance(artifacts, Mapping) or set(artifacts) != REQUIRED_ARTIFACTS:
        raise MainFinalizationError(
            "finalization artifact_hashes must name the exact required artifact set")
    artifact_paths: dict[str, Path] = {}
    for label, value in artifacts.items():
        binding = _exact_keys(
            value, ARTIFACT_BINDING_FIELDS, f"artifact_hashes.{label}")
        path_text = _text(binding["path"], f"artifact_hashes.{label}.path")
        path = Path(path_text)
        if not path.is_absolute():
            raise MainFinalizationError(
                f"artifact_hashes.{label}.path must be absolute")
        artifact_paths[str(label)] = path.resolve()

    if not isinstance(expected_manifest_output_paths, Mapping):
        raise MainFinalizationError(
            "expected manifest output paths must be a complete object")
    observed_output_names = set(expected_manifest_output_paths)
    expected_output_names = set(phase3_main_manifest.OUTPUT_PATH_FIELDS)
    if observed_output_names != expected_output_names:
        raise MainFinalizationError(
            "expected manifest output path fields drifted: "
            f"missing={sorted(expected_output_names - observed_output_names)!r}, "
            f"unexpected={sorted(observed_output_names - expected_output_names)!r}")
    manifest_output_paths: dict[str, Path] = {}
    for name in sorted(expected_output_names):
        raw_path = expected_manifest_output_paths[name]
        if not isinstance(raw_path, (str, Path)):
            raise MainFinalizationError(
                f"expected manifest output path {name!r} must be a path")
        path = Path(raw_path)
        if not path.is_absolute():
            raise MainFinalizationError(
                f"expected manifest output path {name!r} must be absolute")
        manifest_output_paths[name] = path.resolve()
    for artifact_label, output_name in (
        FINALIZATION_ARTIFACT_TO_MANIFEST_OUTPUT.items()
    ):
        if artifact_paths[artifact_label] != manifest_output_paths[output_name]:
            raise MainFinalizationError(
                f"finalization artifact {artifact_label} does not equal the signed "
                f"manifest output path {output_name}")

    if not isinstance(expected_provider_input_paths, Mapping):
        raise MainFinalizationError(
            "expected provider input paths must be a complete object")
    if not isinstance(expected_provider_input_raw_sha256s, Mapping):
        raise MainFinalizationError(
            "expected provider input hashes must be a complete object")
    provider_input_paths: dict[str, Path] = {}
    provider_input_raw_sha256s: dict[str, str] = {}
    if set(expected_provider_input_paths) != PROVIDER_INPUT_FIELDS:
        raise MainFinalizationError(
            "expected provider input path fields drifted: "
            f"missing={sorted(PROVIDER_INPUT_FIELDS - set(expected_provider_input_paths))!r}, "
            f"unexpected={sorted(set(expected_provider_input_paths) - PROVIDER_INPUT_FIELDS)!r}")
    if set(expected_provider_input_raw_sha256s) != PROVIDER_INPUT_FIELDS:
        raise MainFinalizationError(
            "expected provider input hash fields drifted: "
            f"missing={sorted(PROVIDER_INPUT_FIELDS - set(expected_provider_input_raw_sha256s))!r}, "
            f"unexpected={sorted(set(expected_provider_input_raw_sha256s) - PROVIDER_INPUT_FIELDS)!r}")
    for name in sorted(PROVIDER_INPUT_FIELDS):
        raw_path = expected_provider_input_paths[name]
        if not isinstance(raw_path, (str, Path)):
            raise MainFinalizationError(
                f"expected provider input path {name!r} must be a path")
        path = Path(raw_path)
        if not path.is_absolute():
            raise MainFinalizationError(
                f"expected provider input path {name!r} must be absolute")
        provider_input_paths[name] = path.resolve()
        provider_input_raw_sha256s[name] = _sha256(
            expected_provider_input_raw_sha256s[name],
            f"expected provider input {name} raw SHA-256",
        )

    if not isinstance(expected_reviewer_input_paths, Mapping):
        raise MainFinalizationError(
            "expected reviewer input paths must be a complete object")
    if not isinstance(expected_reviewer_input_raw_sha256s, Mapping):
        raise MainFinalizationError(
            "expected reviewer input hashes must be a complete object")
    reviewer_input_paths: dict[str, Path] = {}
    reviewer_input_raw_sha256s: dict[str, str] = {}
    if set(expected_reviewer_input_paths) != REVIEWER_INPUT_FIELDS:
        raise MainFinalizationError(
            "expected reviewer input path fields drifted: "
            f"missing={sorted(REVIEWER_INPUT_FIELDS - set(expected_reviewer_input_paths))!r}, "
            f"unexpected={sorted(set(expected_reviewer_input_paths) - REVIEWER_INPUT_FIELDS)!r}")
    if set(expected_reviewer_input_raw_sha256s) != REVIEWER_INPUT_FIELDS:
        raise MainFinalizationError(
            "expected reviewer input hash fields drifted: "
            f"missing={sorted(REVIEWER_INPUT_FIELDS - set(expected_reviewer_input_raw_sha256s))!r}, "
            f"unexpected={sorted(set(expected_reviewer_input_raw_sha256s) - REVIEWER_INPUT_FIELDS)!r}")
    for name in sorted(REVIEWER_INPUT_FIELDS):
        raw_path = expected_reviewer_input_paths[name]
        if not isinstance(raw_path, (str, Path)):
            raise MainFinalizationError(
                f"expected reviewer input path {name!r} must be a path")
        path = Path(raw_path)
        if not path.is_absolute():
            raise MainFinalizationError(
                f"expected reviewer input path {name!r} must be absolute")
        reviewer_input_paths[name] = path.resolve()
        reviewer_input_raw_sha256s[name] = _sha256(
            expected_reviewer_input_raw_sha256s[name],
            f"expected reviewer input {name} raw SHA-256",
        )
    capacity_result_path = Path(expected_capacity_result_path)
    capacity_dispatch_history_path = Path(expected_capacity_dispatch_history_path)
    if (
        not capacity_result_path.is_absolute()
        or not capacity_dispatch_history_path.is_absolute()
    ):
        raise MainFinalizationError(
            "expected capacity evidence paths must be absolute")
    capacity_result_path = capacity_result_path.resolve()
    capacity_dispatch_history_path = capacity_dispatch_history_path.resolve()
    capacity_result_raw_sha256 = _sha256(
        expected_capacity_result_raw_sha256,
        "expected capacity result raw SHA-256",
    )
    capacity_dispatch_history_raw_sha256 = _sha256(
        expected_capacity_dispatch_history_raw_sha256,
        "expected capacity dispatch history raw SHA-256",
    )
    review_packets_root = Path(expected_review_packets_root_path)
    if not review_packets_root.is_absolute():
        raise MainFinalizationError(
            "expected review packet root path must be absolute")
    review_packets_root = review_packets_root.resolve()
    if review_packets_root != manifest_output_paths["review_packets_root"]:
        raise MainFinalizationError(
            "expected review packet root differs from the signed manifest output path")

    result_path = Path(expected_result_store_path)
    pins_path = Path(expected_analysis_pins_path)
    context_path = Path(expected_context_blocklist_path)
    if (
        not result_path.is_absolute()
        or not pins_path.is_absolute()
        or not context_path.is_absolute()
    ):
        raise MainFinalizationError(
            "expected result, pins, and context paths must be absolute")
    result_path = result_path.resolve()
    pins_path = pins_path.resolve()
    context_path = context_path.resolve()
    if artifact_paths["result_store"] != result_path:
        raise MainFinalizationError(
            "finalization record is not bound to the expected result store")
    if artifact_paths["analysis_pins"] != pins_path:
        raise MainFinalizationError(
            "finalization record is not bound to the expected analysis pins")
    if artifact_paths["context_blocklist"] != context_path:
        raise MainFinalizationError(
            "finalization record is not bound to the expected context blocklist")
    terminal_path = artifact_paths["terminal_dispositions"]
    if not terminal_path.is_file():
        raise MainFinalizationError(
            "bound terminal disposition store is missing")

    run_id = _text(expected_run_id, "expected_run_id")
    if record.get("run_id") != run_id:
        raise MainFinalizationError("finalization run ID differs from the signed launch")
    manifest_sha = _sha256(
        expected_manifest_canonical_sha256,
        "expected_manifest_canonical_sha256",
    )
    authorization_sha = _sha256(
        expected_authorization_canonical_sha256,
        "expected_authorization_canonical_sha256",
    )
    authorization_raw_sha = _sha256(
        expected_authorization_raw_sha256,
        "expected_authorization_raw_sha256",
    )
    authorization_signature_raw_sha = _sha256(
        expected_authorization_signature_raw_sha256,
        "expected_authorization_signature_raw_sha256",
    )
    if bindings["manifest_canonical_sha256"] != manifest_sha:
        raise MainFinalizationError(
            "finalization manifest digest differs from the signed launch")
    if bindings["authorization_canonical_sha256"] != authorization_sha:
        raise MainFinalizationError(
            "finalization authorization digest differs from the signed launch")
    if bindings["authorization_raw_sha256"] != authorization_raw_sha:
        raise MainFinalizationError(
            "finalization authorization bytes differ from the signed launch")
    if (
        bindings["authorization_signature_raw_sha256"]
        != authorization_signature_raw_sha
    ):
        raise MainFinalizationError(
            "finalization authorization signature bytes differ from the signed launch")
    approved_at_utc = _utc(
        authorization_approved_at_utc, "authorization_approved_at_utc")
    valid_until_utc = _utc(
        authorization_valid_until_utc, "authorization_valid_until_utc")
    approved_at = _utc_datetime(
        approved_at_utc, "authorization_approved_at_utc")
    valid_until = _utc_datetime(
        valid_until_utc, "authorization_valid_until_utc")
    finalized_at = _utc_datetime(
        record.get("recorded_at_utc"), "recorded_at_utc")
    if valid_until <= approved_at:
        raise MainFinalizationError(
            "signed authorization dispatch window is empty")
    if finalized_at < approved_at:
        raise MainFinalizationError(
            "finalization predates the signed authorization")
    root = result_path.parent
    root_sha = _sha256_text(root.as_posix())
    journal_identity = f"{run_id}:{manifest_sha}:{root_sha}"
    if (bindings.get("artifact_root") != root.as_posix()
            or bindings.get("artifact_root_sha256") != root_sha
            or bindings.get("journal_execution_identity") != journal_identity):
        raise MainFinalizationError(
            "finalization root or journal identity differs from the expected result root")

    expected_checker_model = _text(
        expected_checker_model, "expected_checker_model")
    terminal_store = MainTerminalDispositionStore(
        terminal_path,
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        inventory=inventory,
        checker_model=expected_checker_model,
    )
    return validate_finalization_admission(
        record,
        run_id=run_id,
        manifest_canonical_sha256=manifest_sha,
        authorization_canonical_sha256=authorization_sha,
        authorization_raw_sha256=authorization_raw_sha,
        authorization_signature_raw_sha256=authorization_signature_raw_sha,
        inventory=inventory,
        context_blocklist_path=context_path,
        terminal_store=terminal_store,
        result_store_path=result_path,
        usage_ledger_path=artifact_paths["usage_ledger"],
        request_journal_path=artifact_paths["request_journal"],
        journal_execution_identity=journal_identity,
        analysis_pins_path=pins_path,
        provider_input_paths=provider_input_paths,
        provider_input_raw_sha256s=provider_input_raw_sha256s,
        reviewer_input_paths=reviewer_input_paths,
        reviewer_input_raw_sha256s=reviewer_input_raw_sha256s,
        capacity_result_path=capacity_result_path,
        expected_capacity_result_raw_sha256=capacity_result_raw_sha256,
        capacity_dispatch_history_path=capacity_dispatch_history_path,
        expected_capacity_dispatch_history_raw_sha256=(
            capacity_dispatch_history_raw_sha256),
        review_packets_root_path=review_packets_root,
        artifact_paths=artifact_paths,
        expected_oracle_model=_text(
            expected_oracle_model, "expected_oracle_model"),
        expected_reviewer_model=_text(
            expected_reviewer_model, "expected_reviewer_model"),
        expected_reviewer_reasoning_effort=_text(
            expected_reviewer_reasoning_effort,
            "expected_reviewer_reasoning_effort",
        ),
        expected_reviewer_concurrency=_positive_int(
            expected_reviewer_concurrency,
            "expected_reviewer_concurrency",
        ),
        authorization_approved_at_utc=approved_at_utc,
        authorization_valid_until_utc=valid_until_utc,
        prior_reconciled_usd=prior_reconciled_usd,
        stage_cap_usd=stage_cap_usd,
    )


def write_finalization_admission(path: str | Path, record: Mapping[str, Any]) -> None:
    """Persist one immutable admission record, refusing any overwrite."""
    _exact_keys(record, FINALIZATION_FIELDS, "finalization record")
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(
        record, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8")
    try:
        with target.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent(target)
    except FileExistsError as exc:
        raise MainFinalizationError(
            f"finalization admission already exists and is immutable: {target}") from exc


__all__ = [
    "ALLOWED_MAIN_CALL_ROLES",
    "CONCENTRATION_MIN_CELLS",
    "CONCENTRATION_RATE",
    "CONCENTRATION_RATIO",
    "DEFAULT_CHECKER_MODEL",
    "FINALIZATION_SCHEMA",
    "FINALIZATION_STATUS",
    "FROZEN_ANALYSIS_PINS_CANONICAL_SHA256",
    "MAX_AFFECTED_MIRROR_UNIT_FRACTION",
    "MAX_TERMINAL_JUDGMENT_CELLS",
    "JUDGE_QUERY_ROLE",
    "JUDGE_VERDICT_ROLE",
    "MainFinalizationError",
    "MainPartitionError",
    "MainTerminalDispositionStore",
    "ORACLE_VERIFICATION_ROLE",
    "PRE_VERDICT_MAIN_CALL_ROLES",
    "QUERY_CHECKER_ROLE",
    "TerminalBoundError",
    "TerminalDispositionError",
    "build_finalization_admission",
    "checker_truncation_diagnostic",
    "evaluate_terminal_bounds",
    "inventory_canonical_sha256",
    "load_result_cell_keys",
    "load_result_rows",
    "mechanically_validate_checker_malformed",
    "mechanically_validate_checker_terminal",
    "validate_finalization_admission",
    "validate_finalization_from_bound_artifacts",
    "validate_main_partition",
    "write_finalization_admission",
]
