"""Zero-filled slot-role inputs for the Phase 3 v3 token forecast."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from rejudge import phase3_plan
from rejudge.phase2_execution import canonical_sha256


SCHEMA_VERSION = "phase3_v3_slot_role_frame_v1"
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
TOKEN_METRICS = ("prompt_tokens", "completion_tokens", "attempt_count")


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
    usage_events: Sequence[Mapping[str, Any]],
    usage_ledger_sha256: str,
) -> dict[str, Any]:
    """Aggregate every attempt into exactly one record per planned judgment slot and role."""
    phase3_plan.validate_protocol(protocol)
    if protocol.get("schema_version") != "phase3_plan_v3":
        raise ForecastInputError("slot-role frames require a resolved v3 protocol")
    ledger_sha = _sha256(usage_ledger_sha256, "usage_ledger_sha256")

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
        _text(cell.get("condition"), f"{cell_key}.condition")
        _text(cell.get("question_id"), f"{cell_key}.question_id")
        cell_by_key[cell_key] = cell

    completed = list(completed_cell_keys)
    if not all(isinstance(cell_key, str) and cell_key for cell_key in completed):
        raise ForecastInputError("completed_cell_keys must contain non-empty strings")
    if len(completed) != len(set(completed)):
        raise ForecastInputError("completed_cell_keys contains duplicates")
    expected_keys = set(cell_by_key)
    completed_keys = set(completed)
    if completed_keys != expected_keys:
        missing = sorted(expected_keys - completed_keys)
        extra = sorted(completed_keys - expected_keys)
        raise ForecastInputError(
            f"completed judgment set differs from plan: missing={len(missing)}, extra={len(extra)}")

    aggregates: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {
            "attempt_count": 0,
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
        elif status == "unknown_charge":
            aggregate["unknown_charge_attempt_count"] += 1
        elif status == "released_no_charge":
            aggregate["released_no_charge_attempt_count"] += 1

    role_records: list[dict[str, Any]] = []
    for cell in cells:
        cell_key = str(cell["cell_key"])
        source_judge = str(cell["judge_model"])
        for role in CALL_ROLES:
            aggregate = aggregates.get((cell_key, role), {
                "attempt_count": 0,
                "actual_token_attempt_count": 0,
                "unknown_charge_attempt_count": 0,
                "released_no_charge_attempt_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            })
            role_records.append({
                "slot_role_key": canonical_sha256({"cell_key": cell_key, "role": role}),
                "cell_key": cell_key,
                "source_judge": source_judge,
                "billed_model": _billed_model(protocol, source_judge, role),
                "role": role,
                "condition": str(cell["condition"]),
                "question_id": str(cell["question_id"]),
                "query_budget": cell.get("query_budget"),
                **aggregate,
                "zero_filled": aggregate["attempt_count"] == 0,
            })

    verdict_records = [record for record in role_records if record["role"] == "judge_verdict"]
    missing_verdicts = [record["cell_key"] for record in verdict_records
                        if record["actual_token_attempt_count"] == 0]
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
        "planned_judgment_slot_count": len(cells),
        "completed_judgment_slot_count": len(completed),
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
