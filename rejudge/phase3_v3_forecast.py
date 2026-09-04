"""Zero-filled slot-role inputs for the Phase 3 v3 token forecast."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence, cast

from rejudge import phase3_main_stage_cap, phase3_plan, phase3_v3_inputs
from rejudge.phase2_execution import canonical_sha256


SCHEMA_VERSION = "phase3_v3_slot_role_frame_v2"
DYNAMIC_SCHEMA_VERSION = "phase3_v3_dynamic_residual_frame_v1"
COST_SCHEMA_VERSION = "phase3_v3_cost_forecast_v2"
CALL_ROLES = (
    "judge_query",
    "judge_verdict",
    "query_checker",
    "oracle_verification",
)
CALL_ROLE_SET = frozenset(CALL_ROLES)
TERMINAL_STATUSES = frozenset({
    "success", "charged_malformed", "unknown_charge", "released_no_charge",
})
ACTUAL_TOKEN_STATUSES = frozenset({"success", "charged_malformed"})
TOKEN_METRICS = (
    "prompt_tokens", "completion_tokens", "attempt_count", "billed_attempt_count",
)


class ForecastInputError(ValueError):
    """Raised when a slot-role frame would omit or misattribute billed usage."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ForecastInputError(f"{label} must be a non-empty string")
    return value


def _sha256(value: Any, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ForecastInputError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ForecastInputError(f"{label} must be a non-negative integer")
    return value


def _cell_dimensions(
    protocol: Mapping[str, Any], cell: Mapping[str, Any], cell_key: str,
) -> dict[str, Any]:
    debater_model = _text(cell.get("debater_model"), f"{cell_key}.debater_model")
    transcript_index = _non_negative_int(
        cell.get("transcript_index"), f"{cell_key}.transcript_index")
    replicate_index = _non_negative_int(
        cell.get("replicate_index"), f"{cell_key}.replicate_index")
    query_budget = _non_negative_int(cell.get("query_budget"), f"{cell_key}.query_budget")
    condition_id = _text(cell.get("condition"), f"{cell_key}.condition")
    matches = [
        condition for condition in protocol["debate_grid"]["conditions"]
        if condition.get("id") == condition_id
    ]
    if len(matches) != 1 or int(matches[0]["query_budget"]) != query_budget:
        raise ForecastInputError(f"{cell_key} condition and query budget disagree")
    replicates_per_side = int(matches[0]["judgment_replicates_per_transcript_side"])
    side_count = int(protocol["debate_grid"]["k"])
    if replicate_index >= side_count * replicates_per_side:
        raise ForecastInputError(f"{cell_key}.replicate_index is outside the mirrored grid")
    return {
        "debater_model": debater_model,
        "transcript_index": transcript_index,
        "replicate_index": replicate_index,
        "mirrored_side": replicate_index // replicates_per_side,
        "query_budget": query_budget,
    }


def _betacf(a: float, b: float, x: float, max_iter: int = 200, eps: float = 1e-12) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((qam + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    factor = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return factor * _betacf(a, b, x) / a
    return 1.0 - factor * _betacf(b, a, 1 - x) / b


def _t_cdf(value: float, df: int) -> float:
    x = df / (df + value * value)
    probability = _betai(df / 2.0, 0.5, x)
    return 1.0 - 0.5 * probability if value > 0 else 0.5 * probability


def t_ppf(probability: float, df: int) -> float:
    if not 0.0 < probability < 1.0:
        raise ForecastInputError("probability must be strictly between zero and one")
    if df <= 0:
        raise ForecastInputError("degrees of freedom must be positive")
    low, high = -1000.0, 1000.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if _t_cdf(middle, df) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def upper_central_90(values: Sequence[float]) -> dict[str, float | int]:
    if len(values) < 2:
        raise ForecastInputError("a clustered U90 requires at least two questions")
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ForecastInputError("cluster values must be finite and non-negative")
    mean = statistics.fmean(values)
    sample_sd = statistics.stdev(values)
    standard_error = sample_sd / math.sqrt(len(values))
    critical = t_ppf(0.95, len(values) - 1)
    return {
        "question_count": len(values),
        "mean": mean,
        "sample_sd": sample_sd,
        "standard_error": standard_error,
        "t_critical_0_95": critical,
        "upper90": mean + critical * standard_error,
    }


def _terminal_attempts(
    usage_events: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    reservations: dict[str, Mapping[str, Any]] = {}
    terminals: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    terminal_ids: set[str] = set()
    for index, event in enumerate(usage_events):
        status = event.get("status")
        if status == "ledger_genesis":
            continue
        attempt_id = event.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ForecastInputError(f"usage event {index} has no attempt_id")
        if status == "reserved":
            if attempt_id in reservations or attempt_id in terminal_ids:
                raise ForecastInputError(f"duplicate reservation for attempt {attempt_id}")
            reservations[attempt_id] = event
            continue
        if status not in TERMINAL_STATUSES:
            raise ForecastInputError(f"usage event {index} has unknown status {status!r}")
        reservation = reservations.pop(attempt_id, None)
        if reservation is None:
            raise ForecastInputError(f"terminal event has no reservation: {attempt_id}")
        if attempt_id in terminal_ids:
            raise ForecastInputError(f"duplicate terminal event for attempt {attempt_id}")
        terminal_ids.add(attempt_id)
        for field in (
            "model",
            "kind",
            "seed",
            "attempt",
            "estimated_tokens",
            "reserved_prompt_tokens",
            "reserved_completion_tokens",
            "metadata",
        ):
            if reservation.get(field) != event.get(field):
                raise ForecastInputError(
                    f"terminal event changed {field} for attempt {attempt_id}")
        terminals.append((reservation, event))
    if reservations:
        first = sorted(reservations)[0]
        raise ForecastInputError(
            f"usage ledger has {len(reservations)} unmatched reservation(s), including {first}")
    return terminals


def _attempt_tokens(
    reservation: Mapping[str, Any], terminal: Mapping[str, Any],
) -> tuple[int, int]:
    status = terminal.get("status")
    attempt_id = str(terminal.get("attempt_id"))
    if status in ACTUAL_TOKEN_STATUSES:
        return (
            _non_negative_int(terminal.get("prompt_tokens"), f"{attempt_id}.prompt_tokens"),
            _non_negative_int(
                terminal.get("completion_tokens"), f"{attempt_id}.completion_tokens"),
        )
    if status == "released_no_charge":
        prompt = _non_negative_int(
            terminal.get("prompt_tokens"), f"{attempt_id}.prompt_tokens")
        completion = _non_negative_int(
            terminal.get("completion_tokens"), f"{attempt_id}.completion_tokens")
        if prompt != 0 or completion != 0:
            raise ForecastInputError("released-no-charge attempt has nonzero actual tokens")
        return 0, 0
    if status == "unknown_charge":
        prompt = terminal.get(
            "reserved_prompt_tokens", reservation.get("reserved_prompt_tokens"))
        completion = terminal.get(
            "reserved_completion_tokens", reservation.get("reserved_completion_tokens"))
        prompt_tokens = _non_negative_int(prompt, f"{attempt_id}.reserved_prompt_tokens")
        completion_tokens = _non_negative_int(
            completion, f"{attempt_id}.reserved_completion_tokens")
        estimated = terminal.get("estimated_tokens", reservation.get("estimated_tokens"))
        if estimated != prompt_tokens + completion_tokens:
            raise ForecastInputError(
                f"unknown-charge reservation split does not total estimated_tokens: {attempt_id}")
        return prompt_tokens, completion_tokens
    raise ForecastInputError(f"unsupported terminal status: {status!r}")


def _billed_model(protocol: Mapping[str, Any], source_judge: str, role: str) -> str:
    roster = protocol["roster"]
    if role in {"judge_query", "judge_verdict"}:
        return source_judge
    if role == "query_checker":
        return str(roster["query_checker"])
    if role == "oracle_verification":
        return str(roster["oracle"])
    raise ForecastInputError(f"unsupported role: {role}")


def build_slot_role_frame(
    *,
    protocol: Mapping[str, Any],
    planned_cells: Iterable[Mapping[str, Any]],
    completed_cell_keys: Iterable[str],
    completed_results_sha256: str,
    usage_events: Sequence[Mapping[str, Any]],
    usage_ledger_sha256: str,
    terminal_cell_keys: Iterable[str] = (),
    terminal_dispositions_sha256: str | None = None,
) -> dict[str, Any]:
    """Aggregate every attempt into exactly one record per resolved slot and role."""
    phase3_plan.validate_protocol(protocol)
    if protocol.get("schema_version") != "phase3_plan_v3":
        raise ForecastInputError("slot-role frames require a resolved v3 protocol")
    ledger_sha = _sha256(usage_ledger_sha256, "usage_ledger_sha256")
    results_sha = _sha256(completed_results_sha256, "completed_results_sha256")

    judgment_kinds = {phase3_plan.MAIN_JUDGMENT_KIND, phase3_plan.CANARY_JUDGMENT_KIND}
    cells = [dict(cell) for cell in planned_cells if cell.get("kind") in judgment_kinds]
    if not cells:
        raise ForecastInputError("planned_cells contains no judgment slots")
    cells.sort(key=lambda cell: str(cell.get("cell_key")))
    cell_by_key: dict[str, dict[str, Any]] = {}
    final_roster = set(protocol["roster"]["judges_final"])
    for cell in cells:
        cell_key = _text(cell.get("cell_key"), "planned cell_key")
        if cell_key in cell_by_key:
            raise ForecastInputError(f"duplicate planned cell_key: {cell_key}")
        source_judge = _text(cell.get("judge_model"), f"{cell_key}.judge_model")
        if source_judge not in final_roster:
            raise ForecastInputError(f"planned cell judge is not in the final roster: {source_judge}")
        dimensions = _cell_dimensions(protocol, cell, cell_key)
        _text(cell.get("question_id"), f"{cell_key}.question_id")
        cell.update(dimensions)
        cell_by_key[cell_key] = cell

    completed = list(completed_cell_keys)
    if not all(isinstance(cell_key, str) and cell_key for cell_key in completed):
        raise ForecastInputError("completed_cell_keys must contain non-empty strings")
    if len(completed) != len(set(completed)):
        raise ForecastInputError("completed_cell_keys contains duplicates")
    terminal_cells = list(terminal_cell_keys)
    if not all(isinstance(cell_key, str) and cell_key for cell_key in terminal_cells):
        raise ForecastInputError("terminal_cell_keys must contain non-empty strings")
    if len(terminal_cells) != len(set(terminal_cells)):
        raise ForecastInputError("terminal_cell_keys contains duplicates")
    terminal_keys = set(terminal_cells)
    completed_keys = set(completed)
    overlap = sorted(completed_keys & terminal_keys)
    if overlap:
        raise ForecastInputError(
            f"completed and terminal judgment sets overlap at {len(overlap)} slot(s)")
    if terminal_keys:
        terminal_sha = _sha256(
            terminal_dispositions_sha256, "terminal_dispositions_sha256")
    elif terminal_dispositions_sha256 is not None:
        raise ForecastInputError(
            "terminal_dispositions_sha256 requires at least one terminal cell")
    else:
        terminal_sha = None
    expected_keys = set(cell_by_key)
    resolved_keys = completed_keys | terminal_keys
    if resolved_keys != expected_keys:
        missing = sorted(expected_keys - resolved_keys)
        extra = sorted(resolved_keys - expected_keys)
        raise ForecastInputError(
            f"resolved judgment set differs from plan: missing={len(missing)}, "
            f"extra={len(extra)}")

    aggregates: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {
            "attempt_count": 0,
            "billed_attempt_count": 0,
            "actual_token_attempt_count": 0,
            "unknown_charge_attempt_count": 0,
            "released_no_charge_attempt_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        })
    for reservation, terminal in _terminal_attempts(usage_events):
        metadata = terminal.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ForecastInputError("usage attempt metadata must be an object")
        cell_key_value = metadata.get("cell_key")
        role_value = metadata.get("call_role")
        if cell_key_value not in cell_by_key:
            if role_value == "capability_qa":
                continue
            raise ForecastInputError(
                f"usage attempt is not attributable to a planned judgment: {cell_key_value!r}")
        cell_key = str(cell_key_value)
        role = _text(role_value, f"{cell_key}.call_role")
        if role not in CALL_ROLE_SET:
            raise ForecastInputError(f"unsupported judgment call role {role!r}")
        cell = cell_by_key[cell_key]
        source_judge = str(cell["judge_model"])
        expected_model = _billed_model(protocol, source_judge, role)
        if terminal.get("model") != expected_model:
            raise ForecastInputError(
                f"{cell_key} {role} billed model {terminal.get('model')!r}, expected "
                f"{expected_model!r}")
        metadata_checks = {
            "judge_model": source_judge,
            "condition": str(cell["condition"]),
            "question_id": str(cell["question_id"]),
            "budget": int(cell["query_budget"]),
            "transcript_index": int(cell["transcript_index"]),
            "replicate": int(cell["replicate_index"]),
        }
        for field, expected in metadata_checks.items():
            if field in metadata and metadata[field] != expected:
                raise ForecastInputError(
                    f"{cell_key} usage metadata {field} differs from the planned slot")
        prompt_tokens, completion_tokens = _attempt_tokens(reservation, terminal)
        aggregate = aggregates[(cell_key, role)]
        aggregate["attempt_count"] += 1
        aggregate["prompt_tokens"] += prompt_tokens
        aggregate["completion_tokens"] += completion_tokens
        status = str(terminal["status"])
        if status in ACTUAL_TOKEN_STATUSES:
            aggregate["actual_token_attempt_count"] += 1
            aggregate["billed_attempt_count"] += 1
        elif status == "unknown_charge":
            aggregate["unknown_charge_attempt_count"] += 1
            aggregate["billed_attempt_count"] += 1
        elif status == "released_no_charge":
            aggregate["released_no_charge_attempt_count"] += 1

    role_records: list[dict[str, Any]] = []
    for cell in cells:
        cell_key = str(cell["cell_key"])
        source_judge = str(cell["judge_model"])
        for role in CALL_ROLES:
            aggregate = aggregates.get((cell_key, role), {
                "attempt_count": 0,
                "billed_attempt_count": 0,
                "actual_token_attempt_count": 0,
                "unknown_charge_attempt_count": 0,
                "released_no_charge_attempt_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            })
            role_records.append({
                "slot_role_key": canonical_sha256({"cell_key": cell_key, "role": role}),
                "cell_key": cell_key,
                "kind": cell["kind"],
                "source_judge": source_judge,
                "billed_model": _billed_model(protocol, source_judge, role),
                "role": role,
                "condition": str(cell["condition"]),
                "question_id": str(cell["question_id"]),
                "query_budget": cell.get("query_budget"),
                "debater_model": cell["debater_model"],
                "transcript_index": cell["transcript_index"],
                "replicate_index": cell["replicate_index"],
                "mirrored_side": cell["mirrored_side"],
                **aggregate,
                "zero_filled": aggregate["attempt_count"] == 0,
            })

    verdict_records = [record for record in role_records if record["role"] == "judge_verdict"]
    missing_verdicts = [record["cell_key"] for record in verdict_records
                        if record["cell_key"] in completed_keys
                        and record["actual_token_attempt_count"] == 0]
    if missing_verdicts:
        raise ForecastInputError(
            f"{len(missing_verdicts)} completed slot(s) have no charged verdict attempt")

    question_groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in role_records:
        key = (
            record["billed_model"], record["source_judge"], record["role"],
            record["condition"], record["question_id"],
        )
        question_groups[key].append(record)

    question_cluster_means: list[dict[str, Any]] = []
    for key in sorted(question_groups):
        records = question_groups[key]
        billed_model, source_judge, role, condition, question_id = key
        question_cluster_means.append({
            "billed_model": billed_model,
            "source_judge": source_judge,
            "role": role,
            "condition": condition,
            "question_id": question_id,
            "slot_count": len(records),
            "zero_filled_slot_count": sum(bool(record["zero_filled"]) for record in records),
            **{
                f"mean_{metric}_per_slot": statistics.fmean(
                    float(record[metric]) for record in records)
                for metric in TOKEN_METRICS
            },
        })

    estimator_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for cluster in question_cluster_means:
        key = (
            cluster["billed_model"], cluster["source_judge"], cluster["role"],
            cluster["condition"],
        )
        estimator_groups[key].append(cluster)
    clustered_u90: list[dict[str, Any]] = []
    for key in sorted(estimator_groups):
        clusters = estimator_groups[key]
        billed_model, source_judge, role, condition = key
        clustered_u90.append({
            "billed_model": billed_model,
            "source_judge": source_judge,
            "role": role,
            "condition": condition,
            "question_count": len(clusters),
            "metrics": {
                metric: upper_central_90([
                    float(cluster[f"mean_{metric}_per_slot"]) for cluster in clusters
                ])
                for metric in TOKEN_METRICS
            },
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "execution_authorized": False,
        "protocol_canonical_sha256": canonical_sha256(protocol),
        "usage_ledger_sha256": ledger_sha,
        "completed_results_sha256": results_sha,
        "completed_cell_keys_sha256": canonical_sha256(sorted(completed)),
        "terminal_cell_keys_sha256": canonical_sha256(sorted(terminal_cells)),
        "terminal_dispositions_sha256": terminal_sha,
        "resolved_cell_keys_sha256": canonical_sha256(sorted(resolved_keys)),
        "planned_judgment_slot_count": len(cells),
        "completed_judgment_slot_count": len(completed),
        "terminal_judgment_slot_count": len(terminal_cells),
        "resolved_judgment_slot_count": len(resolved_keys),
        "role_record_count": len(role_records),
        "expected_role_record_count": len(cells) * len(CALL_ROLES),
        "planned_judgment_cells_sha256": canonical_sha256(cells),
        "role_records_sha256": canonical_sha256(role_records),
        "zero_filled_role_record_count": sum(
            bool(record["zero_filled"]) for record in role_records),
        "unknown_charge_attempt_count": sum(
            int(record["unknown_charge_attempt_count"]) for record in role_records),
        "role_records": role_records,
        "question_cluster_means": question_cluster_means,
        "clustered_u90": clustered_u90,
    }


def _static_variant_id(record: Mapping[str, Any]) -> str:
    role = str(record["role"])
    condition = str(record["condition"])
    side = int(record["mirrored_side"])
    if role in {"judge_query", "judge_verdict"}:
        return f"{role}::{condition}::side{side}"
    if role == "query_checker":
        return f"query_checker::{record['source_judge']}::{condition}::side{side}"
    if role == "oracle_verification":
        return role
    raise ForecastInputError(f"unsupported static-context role: {role}")


def build_dynamic_residual_frame(
    *,
    protocol: Mapping[str, Any],
    slot_role_frame: Mapping[str, Any],
    exact_context_index: Mapping[str, Any],
) -> dict[str, Any]:
    """Subtract exact per-attempt canary static contexts and cluster the residuals."""
    phase3_plan.validate_protocol(protocol)
    protocol_sha = canonical_sha256(protocol)
    if slot_role_frame.get("schema_version") != SCHEMA_VERSION:
        raise ForecastInputError("unexpected slot-role frame schema")
    if slot_role_frame.get("protocol_canonical_sha256") != protocol_sha:
        raise ForecastInputError("slot-role frame binds a different protocol")
    manifest_sha = _sha256(
        exact_context_index.get("tokenizer_manifest_canonical_sha256"),
        "tokenizer_manifest_canonical_sha256",
    )
    context_validation = exact_context_index.get("validation")
    if (not isinstance(context_validation, Mapping)
            or context_validation.get("validation") != "pass"
            or context_validation.get("local_files_checked") is not True):
        raise ForecastInputError("exact context index was not fully file-verified")
    transcript_keys = exact_context_index.get("transcript_keys")
    prompt_token_index = exact_context_index.get("prompt_tokens")
    if not isinstance(transcript_keys, Mapping) or not isinstance(prompt_token_index, Mapping):
        raise ForecastInputError("exact context index is incomplete")
    raw_records = slot_role_frame.get("role_records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ForecastInputError("slot-role frame has no role records")

    residual_records: list[dict[str, Any]] = []
    for index, raw_record in enumerate(raw_records):
        if not isinstance(raw_record, dict):
            raise ForecastInputError(f"slot-role record {index} is not an object")
        record = dict(raw_record)
        if record.get("kind") != phase3_plan.CANARY_JUDGMENT_KIND:
            raise ForecastInputError("dynamic residuals require a canary-only slot-role frame")
        billed_attempts = _non_negative_int(
            record.get("billed_attempt_count"), f"role_records[{index}].billed_attempt_count")
        prompt_tokens = _non_negative_int(
            record.get("prompt_tokens"), f"role_records[{index}].prompt_tokens")
        transcript_dimension = (
            "canary",
            str(record["debater_model"]),
            str(record["question_id"]),
            int(record["transcript_index"]),
        )
        transcript_key = transcript_keys.get(transcript_dimension)
        if not isinstance(transcript_key, str):
            raise ForecastInputError(
                f"canary transcript is absent for dimensions {transcript_dimension[1:]!r}")

        role = str(record["role"])
        query_budget = int(record["query_budget"])
        impossible_query_role = query_budget == 0 and role in {
            "judge_query", "query_checker", "oracle_verification",
        }
        if impossible_query_role:
            if billed_attempts != 0 or prompt_tokens != 0:
                raise ForecastInputError(
                    f"budget-zero role {record['cell_key']} {role} has billed usage")
            variant_id: str | None = None
            static_per_attempt = 0
        else:
            variant_id = _static_variant_id(record)
            lookup_key = (
                "canary", str(record["billed_model"]), role, variant_id, transcript_key,
            )
            static_value = prompt_token_index.get(lookup_key)
            if (isinstance(static_value, bool) or not isinstance(static_value, int)
                    or static_value <= 0):
                raise ForecastInputError(f"exact canary static context is absent: {lookup_key!r}")
            static_per_attempt = static_value
        static_total = static_per_attempt * billed_attempts
        dynamic_tokens = prompt_tokens - static_total
        if dynamic_tokens < 0:
            raise ForecastInputError(
                f"negative dynamic-history residual for {record['cell_key']} {role}: "
                f"prompt={prompt_tokens}, static={static_total}")
        residual_records.append({
            **record,
            "transcript_key": transcript_key,
            "static_variant_id": variant_id,
            "static_prompt_tokens_per_billed_attempt": static_per_attempt,
            "static_prompt_tokens": static_total,
            "dynamic_prompt_tokens": dynamic_tokens,
        })

    metric_names = ("dynamic_prompt_tokens", "completion_tokens", "billed_attempt_count")
    question_groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in residual_records:
        key = (
            str(record["billed_model"]), str(record["source_judge"]),
            str(record["role"]), str(record["condition"]), str(record["question_id"]),
        )
        question_groups[key].append(record)
    question_cluster_means: list[dict[str, Any]] = []
    for key in sorted(question_groups):
        billed_model, source_judge, role, condition, question_id = key
        records = question_groups[key]
        question_cluster_means.append({
            "billed_model": billed_model,
            "source_judge": source_judge,
            "role": role,
            "condition": condition,
            "question_id": question_id,
            "slot_count": len(records),
            **{
                f"mean_{metric}_per_slot": statistics.fmean(
                    float(record[metric]) for record in records)
                for metric in metric_names
            },
        })

    estimator_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for cluster in question_cluster_means:
        key = (
            str(cluster["billed_model"]), str(cluster["source_judge"]),
            str(cluster["role"]), str(cluster["condition"]),
        )
        estimator_groups[key].append(cluster)
    clustered_u90 = []
    for key in sorted(estimator_groups):
        billed_model, source_judge, role, condition = key
        clusters = estimator_groups[key]
        clustered_u90.append({
            "billed_model": billed_model,
            "source_judge": source_judge,
            "role": role,
            "condition": condition,
            "question_count": len(clusters),
            "metrics": {
                metric: upper_central_90([
                    float(cluster[f"mean_{metric}_per_slot"]) for cluster in clusters
                ])
                for metric in metric_names
            },
        })
    return {
        "schema_version": DYNAMIC_SCHEMA_VERSION,
        "execution_authorized": False,
        "protocol_canonical_sha256": protocol_sha,
        "tokenizer_manifest_canonical_sha256": manifest_sha,
        "slot_role_frame_canonical_sha256": canonical_sha256(slot_role_frame),
        "role_record_count": len(residual_records),
        "role_records_sha256": canonical_sha256(residual_records),
        "role_records": residual_records,
        "question_cluster_means": question_cluster_means,
        "clustered_u90": clustered_u90,
    }


def _non_negative_number(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) < 0):
        raise ForecastInputError(f"{label} must be a finite non-negative number")
    return float(value)


def _upper90(metric: Any, label: str) -> float:
    if not isinstance(metric, Mapping):
        raise ForecastInputError(f"{label} must be an object")
    return _non_negative_number(metric.get("upper90"), f"{label}.upper90")


def _ceil_cents(value: Decimal) -> Decimal:
    return (value * Decimal("100")).to_integral_value(
        rounding=ROUND_CEILING) / Decimal("100")


def build_cost_forecast(
    *,
    protocol: Mapping[str, Any],
    planned_main_cells: Sequence[Mapping[str, Any]],
    dynamic_residual_frame: Mapping[str, Any],
    exact_context_index: Mapping[str, Any],
    price_snapshot: Mapping[str, Any],
    price_as_of: datetime,
    cumulative_spend_segments: Sequence[Mapping[str, Any]],
    stage_cap_ratification: Mapping[str, Any] | None = None,
    project_root: str | None = None,
    verify_price_catalog: bool = True,
) -> dict[str, Any]:
    """Project the complete main grid and add bound cumulative stage spend."""
    phase3_plan.validate_protocol(protocol)
    protocol_sha = canonical_sha256(protocol)
    if dynamic_residual_frame.get("schema_version") != DYNAMIC_SCHEMA_VERSION:
        raise ForecastInputError("unexpected dynamic-residual frame schema")
    if dynamic_residual_frame.get("protocol_canonical_sha256") != protocol_sha:
        raise ForecastInputError("dynamic-residual frame binds a different protocol")
    if dynamic_residual_frame.get("execution_authorized") is not False:
        raise ForecastInputError("dynamic-residual frame cannot authorize execution")
    tokenizer_sha = _sha256(
        exact_context_index.get("tokenizer_manifest_canonical_sha256"),
        "exact_context_index.tokenizer_manifest_canonical_sha256",
    )
    if dynamic_residual_frame.get("tokenizer_manifest_canonical_sha256") != tokenizer_sha:
        raise ForecastInputError("dynamic residuals and exact contexts bind different tokenizers")
    context_validation = exact_context_index.get("validation")
    if (not isinstance(context_validation, Mapping)
            or context_validation.get("validation") != "pass"
            or context_validation.get("local_files_checked") is not True):
        raise ForecastInputError("exact context index was not fully file-verified")
    transcript_keys = exact_context_index.get("transcript_keys")
    prompt_token_index = exact_context_index.get("prompt_tokens")
    if not isinstance(transcript_keys, Mapping) or not isinstance(prompt_token_index, Mapping):
        raise ForecastInputError("exact context index is incomplete")
    try:
        price_validation = phase3_v3_inputs.validate_price_snapshot(
            price_snapshot,
            protocol=protocol,
            as_of=price_as_of,
            project_root=project_root,
            verify_catalog=verify_price_catalog,
        )
    except phase3_v3_inputs.InputGateError as exc:
        raise ForecastInputError(f"fresh-price gate failed: {exc}") from exc

    cells = [dict(cell) for cell in planned_main_cells]
    try:
        phase3_plan.validate_cells(cells, str(protocol["cell_key_namespace"]))
    except phase3_plan.PlanValidationError as exc:
        raise ForecastInputError(f"main plan is invalid: {exc}") from exc
    judgment_cells = [
        cell for cell in cells if cell.get("kind") == phase3_plan.MAIN_JUDGMENT_KIND
    ]
    expected_slot_count = int(protocol["debate_grid"]["slot_arithmetic"][
        "total_judgment_slots"])
    if len(judgment_cells) != expected_slot_count:
        raise ForecastInputError(
            f"main plan has {len(judgment_cells)} judgment slots, expected {expected_slot_count}")
    normalized_cells: list[dict[str, Any]] = []
    final_roster = set(protocol["roster"]["judges_final"])
    for cell in judgment_cells:
        cell_key = _text(cell.get("cell_key"), "main cell_key")
        source_judge = _text(cell.get("judge_model"), f"{cell_key}.judge_model")
        if source_judge not in final_roster:
            raise ForecastInputError(f"main cell judge is not in final roster: {source_judge}")
        cell.update(_cell_dimensions(protocol, cell, cell_key))
        normalized_cells.append(cell)

    raw_estimators = dynamic_residual_frame.get("clustered_u90")
    if not isinstance(raw_estimators, list) or not raw_estimators:
        raise ForecastInputError("dynamic-residual frame has no clustered U90 estimates")
    estimators: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for index, estimator in enumerate(raw_estimators):
        if not isinstance(estimator, dict):
            raise ForecastInputError(f"clustered U90 row {index} is not an object")
        estimator_map = cast(Mapping[str, Any], estimator)
        key = (
            str(estimator_map.get("billed_model")), str(estimator_map.get("source_judge")),
            str(estimator_map.get("role")), str(estimator_map.get("condition")),
        )
        if key in estimators:
            raise ForecastInputError(f"duplicate clustered U90 estimator: {key!r}")
        estimators[key] = estimator_map

    grouped_cells: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for cell in normalized_cells:
        source_judge = str(cell["judge_model"])
        for role in CALL_ROLES:
            key = (
                _billed_model(protocol, source_judge, role), source_judge,
                role, str(cell["condition"]),
            )
            grouped_cells[key].append(cell)
    if set(grouped_cells) != set(estimators):
        missing = sorted(set(grouped_cells) - set(estimators))
        extra = sorted(set(estimators) - set(grouped_cells))
        raise ForecastInputError(
            f"dynamic estimators do not match main line items: missing={len(missing)}, "
            f"extra={len(extra)}")

    prices = price_snapshot.get("models")
    if not isinstance(prices, Mapping):
        raise ForecastInputError("price snapshot models are absent")
    line_items: list[dict[str, Any]] = []
    for key in sorted(grouped_cells):
        billed_model, source_judge, role, condition = key
        line_cells = grouped_cells[key]
        estimator = estimators[key]
        metrics = estimator.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ForecastInputError(f"clustered U90 metrics are absent for {key!r}")
        dynamic_u90 = _upper90(
            metrics.get("dynamic_prompt_tokens"), f"{key}.dynamic_prompt_tokens")
        completion_u90 = _upper90(
            metrics.get("completion_tokens"), f"{key}.completion_tokens")
        billed_attempt_u90 = _upper90(
            metrics.get("billed_attempt_count"), f"{key}.billed_attempt_count")
        query_budget = int(line_cells[0]["query_budget"])
        impossible_query_role = query_budget == 0 and role in {
            "judge_query", "query_checker", "oracle_verification",
        }
        static_sum = 0
        if impossible_query_role:
            if any(value != 0 for value in (
                    dynamic_u90, completion_u90, billed_attempt_u90)):
                raise ForecastInputError(f"budget-zero estimator has billed usage for {key!r}")
        elif billed_attempt_u90 > 0:
            for cell in line_cells:
                transcript_dimension = (
                    "main", str(cell["debater_model"]), str(cell["question_id"]),
                    int(cell["transcript_index"]),
                )
                transcript_key = transcript_keys.get(transcript_dimension)
                if not isinstance(transcript_key, str):
                    raise ForecastInputError(
                        f"main transcript is absent for {transcript_dimension[1:]!r}")
                variant_id = _static_variant_id({
                    "role": role,
                    "condition": condition,
                    "mirrored_side": cell["mirrored_side"],
                    "source_judge": source_judge,
                })
                context_key = (
                    "main", billed_model, role, variant_id, transcript_key,
                )
                static_value = prompt_token_index.get(context_key)
                if (isinstance(static_value, bool) or not isinstance(static_value, int)
                        or static_value <= 0):
                    raise ForecastInputError(
                        f"exact main static context is absent: {context_key!r}")
                static_sum += static_value

        slot_count = len(line_cells)
        static_tokens = float(static_sum) * billed_attempt_u90
        dynamic_tokens = float(slot_count) * dynamic_u90
        input_tokens = static_tokens + dynamic_tokens
        output_tokens = float(slot_count) * completion_u90
        price_entry = prices.get(billed_model)
        if not isinstance(price_entry, Mapping):
            raise ForecastInputError(f"price is absent for billed model {billed_model}")
        input_price = Decimal(str(_non_negative_number(
            price_entry.get("input_usd_per_million"), f"{billed_model}.input price")))
        output_price = Decimal(str(_non_negative_number(
            price_entry.get("output_usd_per_million"), f"{billed_model}.output price")))
        token_cost = (
            Decimal(str(input_tokens)) * input_price
            + Decimal(str(output_tokens)) * output_price
        ) / Decimal("1000000")
        transport_multiplier = Decimal(str(
            protocol["decisions"]["spend"]["forecast_contract"]["transport_multiplier"]))
        transported_cost = token_cost * transport_multiplier
        rounded_cost = _ceil_cents(transported_cost)
        line_items.append({
            "billed_model": billed_model,
            "source_judge": source_judge,
            "role": role,
            "condition": condition,
            "slot_count": slot_count,
            "billed_attempts_u90_per_slot": billed_attempt_u90,
            "dynamic_prompt_tokens_u90_per_slot": dynamic_u90,
            "completion_tokens_u90_per_slot": completion_u90,
            "projected_static_prompt_tokens": static_tokens,
            "projected_dynamic_prompt_tokens": dynamic_tokens,
            "projected_input_tokens": input_tokens,
            "projected_output_tokens": output_tokens,
            "input_usd_per_million": float(input_price),
            "output_usd_per_million": float(output_price),
            "cost_before_transport_usd": float(token_cost),
            "cost_after_transport_usd": float(transported_cost),
            "rounded_line_cost_usd": float(rounded_cost),
        })

    segment_names: set[str] = set()
    normalized_segments = []
    cumulative_cost = Decimal("0")
    for index, segment in enumerate(cumulative_spend_segments):
        if set(segment) != {
                "name", "ledger_sha256", "actual_spend_usd", "uncertain_spend_usd"}:
            raise ForecastInputError(f"cumulative spend segment {index} fields drifted")
        name = _text(segment.get("name"), f"cumulative segment {index}.name")
        if name in segment_names:
            raise ForecastInputError(f"duplicate cumulative spend segment: {name}")
        segment_names.add(name)
        ledger_sha = _sha256(
            segment.get("ledger_sha256"), f"cumulative segment {name}.ledger_sha256")
        actual = Decimal(str(_non_negative_number(
            segment.get("actual_spend_usd"), f"cumulative segment {name}.actual")))
        uncertain = Decimal(str(_non_negative_number(
            segment.get("uncertain_spend_usd"), f"cumulative segment {name}.uncertain")))
        cumulative_cost += actual + uncertain
        normalized_segments.append({
            "name": name,
            "ledger_sha256": ledger_sha,
            "actual_spend_usd": float(actual),
            "uncertain_spend_usd": float(uncertain),
        })
    if not normalized_segments:
        raise ForecastInputError("at least one cumulative spend segment is required")

    projected_main = sum(
        (Decimal(str(item["rounded_line_cost_usd"])) for item in line_items), Decimal("0"))
    stage_total = cumulative_cost + projected_main
    if stage_cap_ratification is None:
        stage_cap = Decimal(str(protocol["decisions"]["spend"]["stage_cap_usd"]))
        stage_cap_binding = {
            "kind": "protocol",
            "canonical_sha256": protocol_sha,
        }
    else:
        try:
            ratification_validation = (
                phase3_main_stage_cap.validate_stage_cap_ratification(
                    stage_cap_ratification,
                    protocol_canonical_sha256=protocol_sha,
                )
            )
        except phase3_main_stage_cap.StageCapRatificationError as exc:
            raise ForecastInputError(f"stage-cap ratification failed: {exc}") from exc
        stage_cap = ratification_validation["stage_cap_usd"]
        stage_cap_binding = {
            "kind": "owner_ratification",
            "canonical_sha256": ratification_validation["canonical_sha256"],
            "ratification_id": ratification_validation["ratification_id"],
        }
    return {
        "schema_version": COST_SCHEMA_VERSION,
        "certification": "pass",
        "execution_authorized": False,
        "protocol_canonical_sha256": protocol_sha,
        "tokenizer_manifest_canonical_sha256": tokenizer_sha,
        "dynamic_residual_frame_canonical_sha256": canonical_sha256(dynamic_residual_frame),
        "price_snapshot_canonical_sha256": canonical_sha256(price_snapshot),
        "price_validation": price_validation,
        "planned_main_cells_canonical_sha256": canonical_sha256(cells),
        "planned_main_judgment_slot_count": len(judgment_cells),
        "transport_multiplier": float(transport_multiplier),
        "line_item_rounding": "ceiling_to_whole_cents",
        "line_items": line_items,
        "projected_main_usd": float(projected_main),
        "cumulative_spend_segments": normalized_segments,
        "cumulative_spend_usd": float(cumulative_cost),
        "projected_stage_total_usd": float(stage_total),
        "stage_cap_usd": float(stage_cap),
        "stage_cap_binding": stage_cap_binding,
        "within_stage_cap": stage_total <= stage_cap,
        "non_claim": "A passing forecast does not authorize provider calls or GPU work.",
    }
