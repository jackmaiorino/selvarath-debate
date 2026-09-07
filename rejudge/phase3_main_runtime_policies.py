"""Strict, non-authorizing runtime policy contracts for Phase 3 main.

The tracked policy candidates define how a detected provider price change stops new
logical calls and how external reviewer usage is bounded. Final authority still lives only
in an exact signed main authorization that binds both policy byte hashes.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


PRICE_CHANGE_POLICY_SCHEMA = "phase3_main_price_change_policy_v1"
REVIEWER_USAGE_POLICY_SCHEMA = "phase3_main_reviewer_usage_policy_v1"
PRICE_CHANGE_SIGNAL_SCHEMA = "phase3_main_price_change_signal_v1"
PRICE_CHANGE_SIGNAL_FILENAME = "main_price_change.signal.json"
PRICE_CHANGE_SIGNAL_TRIGGERS = frozenset({
    "operator_observation",
    "provider_catalog_observation",
    "provider_notification",
})
REVIEWER_USAGE_UNIT = "external_reviewer_dispatch"
MAXIMUM_REVIEWER_DISPATCHES = 59_040
# Amendment 14 (2026-09-06): bounded uncertain-spend tolerance for the main identity. The
# policy bytes are pinned here by raw SHA-256 and the implementing commit is bound by the
# main manifest, which the 2026-09-06 methods consult accepted in place of a manifest
# input binding. The numbers below are re-read from the policy at load and must agree.
UNCERTAIN_SPEND_POLICY_SCHEMA = "phase3_main_uncertain_spend_policy_v1"
UNCERTAIN_SPEND_POLICY_ID = "phase3-main-uncertain-spend-policy-2026-09-06"
UNCERTAIN_SPEND_POLICY_RELATIVE_PATH = (
    "rejudge/phase3_main_uncertain_spend_policy_2026-09-06.json"
)
UNCERTAIN_SPEND_POLICY_RAW_SHA256 = "199576d7455afb729b1a84c9cc7b66db6058c694c5b04be4b39054504762540f"
UNCERTAIN_SPEND_POLICY_FATAL_FINDINGS = frozenset({
    "reservation_without_terminal_event",
    "charged_malformed_response",
    "success_without_journal_entry",
    "success_without_valid_request_fingerprint_binding",
    "ledger_journal_request_fingerprint_mismatch",
    "duplicate_success_for_key",
    "journal_entry_without_success",
})
UNCERTAIN_SPEND_TOLERATED_FINDING = "unknown_charge"
PRICE_CHANGE_SIGNAL_FIELDS = frozenset({
    "schema_version",
    "status",
    "run_id",
    "manifest_canonical_sha256",
    "artifact_root",
    "artifact_root_sha256",
    "journal_execution_identity",
    "observed_at_utc",
    "trigger_kind",
    "evidence",
    "note",
    "stop_new_logical_provider_calls",
    "execution_authorized",
    "provider_calls_authorized",
    "main_run_spend_authorized",
})
PRICE_CHANGE_SIGNAL_EVIDENCE_FIELDS = frozenset({
    "path",
    "raw_sha256",
    "byte_count",
})

EXPECTED_PRICE_CHANGE_POLICY: dict[str, Any] = {
    "schema_version": PRICE_CHANGE_POLICY_SCHEMA,
    "policy_id": "phase3-main-price-change-policy-2026-08-30",
    "stage": "main",
    "provider": "Together",
    "status": "proposal_pending_exact_main_authorization",
    "detection_contract": {
        "bound_price_snapshot_revalidated_before_each_new_logical_provider_call": True,
        "operator_signal_filename": PRICE_CHANGE_SIGNAL_FILENAME,
        "operator_signal_presence_halts_before_new_logical_provider_call": True,
        "provider_side_change_without_snapshot_drift_or_operator_signal_detected": False,
    },
    "response": {
        "stop_new_logical_provider_calls": True,
        "allow_already_started_call_to_finish_and_be_accounted": True,
        "identity_disposition": "halt_preserve_no_resume",
        "automatic_reprice": False,
        "automatic_cap_change": False,
        "automatic_model_substitution": False,
        "automatic_resume": False,
        "successor_requires_fresh_price_snapshot": True,
        "successor_requires_fresh_forecast": True,
        "successor_requires_fresh_manifest": True,
        "successor_requires_fresh_exact_authorization": True,
    },
    "execution_authorized": False,
    "provider_calls_authorized": False,
    "main_run_spend_authorized": False,
}

EXPECTED_REVIEWER_USAGE_POLICY: dict[str, Any] = {
    "schema_version": REVIEWER_USAGE_POLICY_SCHEMA,
    "policy_id": "phase3-main-reviewer-usage-policy-2026-08-30",
    "stage": "main",
    "status": "proposal_pending_exact_main_authorization",
    "usage_unit": REVIEWER_USAGE_UNIT,
    "maximum_reviewer_dispatches": MAXIMUM_REVIEWER_DISPATCHES,
    "quantity_source": "one_durable_dispatch_reservation_per_reviewer_child_release",
    "reservation_treatment": {
        "count_before_child_release": True,
        "failed_or_ambiguous_dispatch_counts": True,
        "redispatch_after_reservation": False,
        "hard_crash_with_reservation_makes_identity_non_resumable": True,
    },
    "accounting_treatment": {
        "separate_from_together_provider_usage_ledger": True,
        "excluded_from_together_usd_stage_cap": True,
        "dispatch_count_is_usd_accounting": False,
        "dispatch_count_is_token_accounting": False,
        "zero_monetary_cost_claimed": False,
        "unlimited_service_capacity_claimed": False,
    },
    "execution_authorized": False,
    "external_reviewer_dispatch_authorized": False,
    "provider_calls_authorized": False,
    "main_run_spend_authorized": False,
}


class MainRuntimePolicyError(ValueError):
    """A Phase 3 main runtime policy failed closed."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MainRuntimePolicyError(f"JSON object repeats key {key!r}")
        value[key] = item
    return value


def _load_object(path: str | Path, label: str) -> Mapping[str, Any]:
    source = Path(path).resolve()
    try:
        first = source.read_bytes()
        second = source.read_bytes()
        if first != second:
            raise MainRuntimePolicyError(f"{label} changed while read")
        value = json.loads(
            first.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {item!r}")),
        )
        pending = [value]
        while pending:
            current = pending.pop()
            if isinstance(current, float) and not math.isfinite(current):
                raise ValueError("non-finite JSON number")
            if isinstance(current, Mapping):
                pending.extend(current.values())
            elif isinstance(current, list):
                pending.extend(current)
    except MainRuntimePolicyError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MainRuntimePolicyError(f"could not load strict {label}: {source}") from exc
    if not isinstance(value, Mapping):
        raise MainRuntimePolicyError(f"{label} must be a JSON object")
    return value


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise MainRuntimePolicyError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MainRuntimePolicyError(f"{label} must be a non-empty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MainRuntimePolicyError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MainRuntimePolicyError(f"{label} must use UTC")
    return parsed.astimezone(timezone.utc)


def _stable_file_bytes(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise MainRuntimePolicyError(f"{label} must be a regular file: {path}")
    try:
        first = path.read_bytes()
        second = path.read_bytes()
    except OSError as exc:
        raise MainRuntimePolicyError(f"could not read {label}: {path}") from exc
    if first != second:
        raise MainRuntimePolicyError(f"{label} changed while read")
    return first


def validate_price_change_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Require the exact non-authorizing detected-price-change response."""
    if dict(policy) != EXPECTED_PRICE_CHANGE_POLICY:
        raise MainRuntimePolicyError("price-change policy differs from the frozen candidate")
    return {
        "schema_version": PRICE_CHANGE_POLICY_SCHEMA,
        "operator_signal_filename": PRICE_CHANGE_SIGNAL_FILENAME,
        "execution_authorized": False,
    }


def validate_reviewer_usage_policy(
    policy: Mapping[str, Any], *, capacity_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Require the exact separate reviewer-dispatch accounting treatment."""
    if dict(policy) != EXPECTED_REVIEWER_USAGE_POLICY:
        raise MainRuntimePolicyError("reviewer-usage policy differs from the frozen candidate")
    if capacity_plan is not None:
        try:
            capacity_maximum = capacity_plan["capacity_thresholds"][
                "maximum_unique_review_payloads_zero_dedup"
            ]
        except (KeyError, TypeError) as exc:
            raise MainRuntimePolicyError(
                "capacity plan omits its maximum reviewer payload count"
            ) from exc
        if (
            isinstance(capacity_maximum, bool)
            or not isinstance(capacity_maximum, int)
            or capacity_maximum != MAXIMUM_REVIEWER_DISPATCHES
        ):
            raise MainRuntimePolicyError(
                "reviewer-usage ceiling differs from the bound capacity plan"
            )
    return {
        "schema_version": REVIEWER_USAGE_POLICY_SCHEMA,
        "usage_unit": REVIEWER_USAGE_UNIT,
        "maximum_reviewer_dispatches": MAXIMUM_REVIEWER_DISPATCHES,
        "execution_authorized": False,
    }


def validate_price_change_signal(
    signal: Mapping[str, Any],
    *,
    run_id: str,
    manifest_canonical_sha256: str,
    artifact_root: str | Path,
    journal_execution_identity: str,
    verify_evidence: bool = True,
) -> dict[str, Any]:
    """Validate one non-authorizing stop signal for an exact started identity."""
    if not isinstance(signal, Mapping) or set(signal) != PRICE_CHANGE_SIGNAL_FIELDS:
        raise MainRuntimePolicyError("price-change signal fields drifted")
    root = Path(artifact_root)
    if not root.is_absolute():
        raise MainRuntimePolicyError("price-change signal artifact root must be absolute")
    root = root.resolve()
    root_sha256 = hashlib.sha256(root.as_posix().encode("utf-8")).hexdigest()
    expected_identity = {
        "schema_version": PRICE_CHANGE_SIGNAL_SCHEMA,
        "status": "provider_price_change_observed",
        "run_id": run_id,
        "manifest_canonical_sha256": _sha256(
            manifest_canonical_sha256, "manifest_canonical_sha256"),
        "artifact_root": root.as_posix(),
        "artifact_root_sha256": root_sha256,
        "journal_execution_identity": journal_execution_identity,
        "stop_new_logical_provider_calls": True,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
    }
    for field, expected in expected_identity.items():
        if signal.get(field) != expected:
            raise MainRuntimePolicyError(
                f"price-change signal {field} differs from the started identity")
    _utc(signal.get("observed_at_utc"), "price-change signal observed_at_utc")
    if signal.get("trigger_kind") not in PRICE_CHANGE_SIGNAL_TRIGGERS:
        raise MainRuntimePolicyError("price-change signal trigger kind is not supported")
    note = signal.get("note")
    if not isinstance(note, str) or not note.strip() or len(note) > 2_000:
        raise MainRuntimePolicyError(
            "price-change signal note must contain 1 through 2000 characters")
    evidence = signal.get("evidence")
    if not isinstance(evidence, Mapping) or set(evidence) != (
        PRICE_CHANGE_SIGNAL_EVIDENCE_FIELDS
    ):
        raise MainRuntimePolicyError("price-change signal evidence fields drifted")
    evidence_path = Path(str(evidence.get("path", "")))
    if not evidence_path.is_absolute():
        raise MainRuntimePolicyError("price-change signal evidence path must be absolute")
    evidence_path = evidence_path.resolve()
    if evidence.get("path") != evidence_path.as_posix():
        raise MainRuntimePolicyError("price-change signal evidence path is not canonical")
    expected_evidence_sha256 = _sha256(
        evidence.get("raw_sha256"), "price-change signal evidence raw_sha256")
    byte_count = evidence.get("byte_count")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
        raise MainRuntimePolicyError(
            "price-change signal evidence byte_count must be a non-negative integer")
    if verify_evidence:
        raw = _stable_file_bytes(evidence_path, "price-change signal evidence")
        if (
            hashlib.sha256(raw).hexdigest() != expected_evidence_sha256
            or len(raw) != byte_count
        ):
            raise MainRuntimePolicyError("price-change signal evidence bytes drifted")
    return {
        "schema_version": PRICE_CHANGE_SIGNAL_SCHEMA,
        "run_id": run_id,
        "manifest_canonical_sha256": manifest_canonical_sha256,
        "trigger_kind": signal["trigger_kind"],
        "observed_at_utc": signal["observed_at_utc"],
        "evidence_path": evidence_path.as_posix(),
        "evidence_raw_sha256": expected_evidence_sha256,
        "stop_new_logical_provider_calls": True,
        "execution_authorized": False,
    }


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MainRuntimePolicyError(f"{label} must be a positive integer")
    return value


def validate_uncertain_spend_policy(
    policy: Mapping[str, Any],
    *,
    raw: bytes,
    role_limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Require the exact pinned amendment-14 policy bytes and extract its frozen numbers."""
    observed_sha = hashlib.sha256(raw).hexdigest()
    if observed_sha != UNCERTAIN_SPEND_POLICY_RAW_SHA256:
        raise MainRuntimePolicyError(
            "uncertain-spend policy bytes differ from the pinned raw SHA-256")
    if policy.get("schema_version") != UNCERTAIN_SPEND_POLICY_SCHEMA:
        raise MainRuntimePolicyError("uncertain-spend policy schema drifted")
    if policy.get("policy_id") != UNCERTAIN_SPEND_POLICY_ID or policy.get("stage") != "main":
        raise MainRuntimePolicyError("uncertain-spend policy identity drifted")
    for flag in (
        "execution_authorized", "provider_calls_authorized", "main_run_spend_authorized",
    ):
        if policy.get(flag) is not False:
            raise MainRuntimePolicyError(f"uncertain-spend policy {flag} must be false")
    ceiling = policy.get("ceiling")
    if not isinstance(ceiling, Mapping):
        raise MainRuntimePolicyError("uncertain-spend policy omits its ceiling")
    ceiling_usd = ceiling.get("run_uncertain_ceiling_usd")
    if (
        isinstance(ceiling_usd, bool)
        or not isinstance(ceiling_usd, (int, float))
        or not math.isfinite(float(ceiling_usd))
        or float(ceiling_usd) <= 0
    ):
        raise MainRuntimePolicyError("run_uncertain_ceiling_usd must be a positive number")
    initial = ceiling.get("initial_run_uncertain_spend_usd")
    if isinstance(initial, bool) or not isinstance(initial, (int, float)) or float(initial) != 0:
        raise MainRuntimePolicyError("initial_run_uncertain_spend_usd must be zero")
    if (
        ceiling.get("fresh_ledger_required") is not True
        or ceiling.get("counts_fully_against_stage_cap") is not True
        or ceiling.get("creates_additional_spending_authority") is not False
    ):
        raise MainRuntimePolicyError("uncertain-spend ceiling treatment drifted")
    guard = policy.get("abandonment_guard")
    if not isinstance(guard, Mapping) or guard.get("scope") != "per pass":
        raise MainRuntimePolicyError("abandonment guard must be scoped per pass")
    from rejudge import phase2_canary_runner as runner  # local import: avoids a cycle

    if (
        guard.get("absolute_abandoned_cells") != runner.ABANDONED_ABSOLUTE
        or guard.get("abandoned_fraction") != runner.ABANDONED_FRACTION
        or guard.get("abandoned_fraction_floor_attempted") != runner.ABANDONED_FRACTION_FLOOR
    ):
        raise MainRuntimePolicyError(
            "abandonment guard numbers differ from the pass runner constants")
    cooldown = _positive_int(
        guard.get("abandoned_rate_cooldown_seconds"), "abandoned_rate_cooldown_seconds")
    consecutive = _positive_int(
        guard.get("abandoned_rate_consecutive_pass_allowance"),
        "abandoned_rate_consecutive_pass_allowance")
    pass_bound = policy.get("pass_bound")
    if not isinstance(pass_bound, Mapping):
        raise MainRuntimePolicyError("uncertain-spend policy omits its pass bound")
    pass_allowance = _positive_int(
        pass_bound.get("unknown_charge_pass_allowance"), "unknown_charge_pass_allowance")
    finalization = policy.get("finalization")
    if not isinstance(finalization, Mapping):
        raise MainRuntimePolicyError("uncertain-spend policy omits its finalization section")
    if finalization.get("reconciliation_tolerated_finding") != UNCERTAIN_SPEND_TOLERATED_FINDING:
        raise MainRuntimePolicyError("tolerated reconciliation finding drifted")
    fatal = finalization.get("reconciliation_fatal_findings")
    if not isinstance(fatal, list) or set(fatal) != UNCERTAIN_SPEND_POLICY_FATAL_FINDINGS:
        raise MainRuntimePolicyError("fatal reconciliation findings drifted")
    if (
        finalization.get("completion_label_when_uncertain_is_zero") != "PASS_CLEAN"
        or finalization.get("completion_label_when_uncertain_is_positive")
        != "PASS_CONSERVATIVE_UNCERTAIN"
    ):
        raise MainRuntimePolicyError("completion labels drifted")
    transport = policy.get("transport")
    if not isinstance(transport, Mapping):
        raise MainRuntimePolicyError("uncertain-spend policy omits its transport section")
    read_timeout = _positive_int(
        transport.get("read_timeout_seconds"), "transport.read_timeout_seconds")
    if role_limits is not None:
        try:
            bound_read = role_limits["request_settings"]["transport"]["http_timeout"]["read"]
        except (KeyError, TypeError) as exc:
            raise MainRuntimePolicyError(
                "role limits omit the HTTP read timeout pin") from exc
        if isinstance(bound_read, bool) or bound_read != read_timeout:
            raise MainRuntimePolicyError(
                "role-limits read timeout differs from the uncertain-spend policy")
    return {
        "schema_version": UNCERTAIN_SPEND_POLICY_SCHEMA,
        "policy_id": UNCERTAIN_SPEND_POLICY_ID,
        "policy_raw_sha256": observed_sha,
        "run_uncertain_ceiling_usd": float(ceiling_usd),
        "initial_run_uncertain_spend_usd": 0.0,
        "abandoned_rate_cooldown_seconds": cooldown,
        "abandoned_rate_consecutive_pass_allowance": consecutive,
        "unknown_charge_pass_allowance": pass_allowance,
        "read_timeout_seconds": read_timeout,
        "tolerated_finding": UNCERTAIN_SPEND_TOLERATED_FINDING,
        "execution_authorized": False,
    }


def load_and_validate_uncertain_spend_policy(
    project_root: str | Path, *, role_limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(project_root).resolve() / UNCERTAIN_SPEND_POLICY_RELATIVE_PATH
    raw = _stable_file_bytes(path, "uncertain-spend policy")
    return validate_uncertain_spend_policy(
        _load_object(path, "uncertain-spend policy"), raw=raw, role_limits=role_limits)


def load_and_validate_price_change_policy(path: str | Path) -> dict[str, Any]:
    return validate_price_change_policy(_load_object(path, "price-change policy"))


def load_and_validate_reviewer_usage_policy(
    path: str | Path, *, capacity_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return validate_reviewer_usage_policy(
        _load_object(path, "reviewer-usage policy"),
        capacity_plan=capacity_plan,
    )


def load_and_validate_price_change_signal(
    path: str | Path,
    *,
    run_id: str,
    manifest_canonical_sha256: str,
    artifact_root: str | Path,
    journal_execution_identity: str,
    verify_evidence: bool = True,
) -> dict[str, Any]:
    return validate_price_change_signal(
        _load_object(path, "price-change signal"),
        run_id=run_id,
        manifest_canonical_sha256=manifest_canonical_sha256,
        artifact_root=artifact_root,
        journal_execution_identity=journal_execution_identity,
        verify_evidence=verify_evidence,
    )


__all__ = [
    "EXPECTED_PRICE_CHANGE_POLICY",
    "EXPECTED_REVIEWER_USAGE_POLICY",
    "MAXIMUM_REVIEWER_DISPATCHES",
    "MainRuntimePolicyError",
    "PRICE_CHANGE_POLICY_SCHEMA",
    "PRICE_CHANGE_SIGNAL_FILENAME",
    "PRICE_CHANGE_SIGNAL_FIELDS",
    "PRICE_CHANGE_SIGNAL_SCHEMA",
    "PRICE_CHANGE_SIGNAL_TRIGGERS",
    "REVIEWER_USAGE_POLICY_SCHEMA",
    "REVIEWER_USAGE_UNIT",
    "load_and_validate_price_change_policy",
    "load_and_validate_price_change_signal",
    "load_and_validate_reviewer_usage_policy",
    "validate_price_change_policy",
    "validate_price_change_signal",
    "validate_reviewer_usage_policy",
]


# Amendment 15 (2026-09-07): the voided predecessor identity's settled spend is stage
# expenditure counted against the cumulative cap outside the reconciled ledger segments.
# The record is pinned by raw bytes at the bound source commit, exactly like the
# amendment-14 policy, and the driver adds its accounted amount to the provider client's
# initial spend so the successor's enforceable ceiling is cap - prior - voided.
PREDECESSOR_VOID_ACCOUNTING_SCHEMA = "phase3_main_predecessor_void_accounting_v1"
PREDECESSOR_VOID_ACCOUNTING_ID = "phase3-main-predecessor-void-accounting-2026-09-07"
PREDECESSOR_VOID_ACCOUNTING_RELATIVE_PATH = (
    "rejudge/phase3_main_predecessor_void_accounting_2026-09-07.json"
)
PREDECESSOR_VOID_ACCOUNTING_RAW_SHA256 = "54265df380d769a9e0467f34ce662be8d6c88256699c611be1977bc2766f0c57"
_VOID_STATUS = "voided_driver_integrity_refusal"


def _exact_money(value: Any, label: str) -> Decimal:
    if not isinstance(value, str) or value.strip() != value:
        raise MainRuntimePolicyError(f"{label} must be an exact decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise MainRuntimePolicyError(f"{label} must be an exact decimal string") from exc
    if not result.is_finite() or result < 0:
        raise MainRuntimePolicyError(f"{label} must be finite and non-negative")
    return result


def validate_predecessor_void_accounting(
    record: Mapping[str, Any], *, raw: bytes, verify_ledger: bool = False,
) -> dict[str, Any]:
    """Require the exact pinned amendment-15 record and extract its enforceable numbers."""
    from rejudge import phase3_main_stage_cap as stage_cap  # local import: avoids a cycle

    observed_sha = hashlib.sha256(raw).hexdigest()
    if observed_sha != PREDECESSOR_VOID_ACCOUNTING_RAW_SHA256:
        raise MainRuntimePolicyError(
            "predecessor void accounting bytes differ from the pinned raw SHA-256")
    if record.get("schema_version") != PREDECESSOR_VOID_ACCOUNTING_SCHEMA:
        raise MainRuntimePolicyError("predecessor void accounting schema drifted")
    if (
        record.get("record_id") != PREDECESSOR_VOID_ACCOUNTING_ID
        or record.get("stage") != "main"
    ):
        raise MainRuntimePolicyError("predecessor void accounting identity drifted")
    for flag in (
        "execution_authorized", "provider_calls_authorized", "main_run_spend_authorized",
        "creates_additional_spending_authority",
    ):
        if record.get(flag) is not False:
            raise MainRuntimePolicyError(f"predecessor void accounting {flag} must be false")
    predecessor = record.get("predecessor")
    if not isinstance(predecessor, Mapping):
        raise MainRuntimePolicyError("predecessor void accounting omits its predecessor")
    run_id = predecessor.get("run_id")
    if not isinstance(run_id, str) or not run_id.startswith("phase3-main-"):
        raise MainRuntimePolicyError("predecessor run_id must be a phase3-main run id")
    if predecessor.get("status") != _VOID_STATUS:
        raise MainRuntimePolicyError("predecessor status drifted")
    for field in (
        "manifest_canonical_sha256", "manifest_identity_sha256",
        "authorization_canonical_sha256", "authorization_raw_sha256",
        "authorization_signature_raw_sha256", "usage_ledger_raw_sha256",
    ):
        _sha256(predecessor.get(field), f"predecessor.{field}")
    artifact_root = Path(str(predecessor.get("artifact_root", "")))
    ledger_path = Path(str(predecessor.get("usage_ledger_path", "")))
    if not artifact_root.is_absolute() or not ledger_path.is_absolute():
        raise MainRuntimePolicyError("predecessor evidence paths must be absolute")
    if ledger_path.parent != artifact_root or ledger_path.name != "main_usage.jsonl":
        raise MainRuntimePolicyError(
            "predecessor usage ledger must be main_usage.jsonl under its artifact root")
    events = predecessor.get("usage_ledger_events")
    if isinstance(events, bool) or not isinstance(events, int) or events <= 0:
        raise MainRuntimePolicyError("predecessor usage_ledger_events must be positive")
    accounting = record.get("accounting")
    if not isinstance(accounting, Mapping):
        raise MainRuntimePolicyError("predecessor void accounting omits its accounting")
    settled = _exact_money(accounting.get("settled_spend_usd"), "settled_spend_usd")
    uncertain = _exact_money(accounting.get("uncertain_spend_usd"), "uncertain_spend_usd")
    accounted = _exact_money(accounting.get("accounted_spend_usd"), "accounted_spend_usd")
    if accounted != settled + uncertain:
        raise MainRuntimePolicyError("accounted spend must equal settled plus uncertain")
    unmatched = accounting.get("unmatched_reservations")
    if isinstance(unmatched, bool) or unmatched != 0:
        raise MainRuntimePolicyError("voided predecessor must carry no open reservations")
    unknown = accounting.get("unknown_charge_count")
    if isinstance(unknown, bool) or not isinstance(unknown, int) or unknown < 0:
        raise MainRuntimePolicyError("unknown_charge_count must be a non-negative integer")
    if (
        accounting.get("counted_against_stage_cap") is not True
        or accounting.get("inside_reconciled_segments") is not False
    ):
        raise MainRuntimePolicyError("voided predecessor spend treatment drifted")
    cap = _exact_money(accounting.get("stage_cap_usd"), "stage_cap_usd")
    prior = _exact_money(accounting.get("prior_reconciled_usd"), "prior_reconciled_usd")
    forecast = _exact_money(
        accounting.get("successor_forecast_main_usd"), "successor_forecast_main_usd")
    total = _exact_money(accounting.get("expected_stage_total_usd"), "expected_stage_total_usd")
    ceiling = _exact_money(
        accounting.get("maximum_successor_expenditure_usd"),
        "maximum_successor_expenditure_usd")
    if cap != stage_cap.STAGE_CAP_USD or prior != stage_cap.PREDECESSOR_UPPER_BOUND_USD:
        raise MainRuntimePolicyError(
            "voided predecessor accounting cap or prior differs from the owner ratification")
    if forecast != stage_cap.PROJECTED_MAIN_USD:
        raise MainRuntimePolicyError(
            "voided predecessor accounting forecast differs from the certified main forecast")
    if total != prior + accounted + forecast or ceiling != cap - prior - accounted:
        raise MainRuntimePolicyError("voided predecessor accounting arithmetic drifted")
    if ceiling < forecast:
        raise MainRuntimePolicyError(
            "the certified forecast no longer fits under the successor ceiling")
    science = record.get("scientific_use")
    if (
        not isinstance(science, Mapping)
        or science.get("predecessor_outputs_excluded_from_inference") is not True
        or science.get("verdict_content_inspected") is not False
    ):
        raise MainRuntimePolicyError("voided predecessor scientific-use exclusion drifted")
    non_claims = record.get("non_claims")
    if not isinstance(non_claims, list) or not non_claims:
        raise MainRuntimePolicyError("voided predecessor accounting omits its non-claims")
    ledger_verified = False
    if verify_ledger:
        from rejudge import api_client  # local import: keeps this module import-light

        ledger_raw = _stable_file_bytes(ledger_path, "predecessor usage ledger")
        if hashlib.sha256(ledger_raw).hexdigest() != predecessor["usage_ledger_raw_sha256"]:
            raise MainRuntimePolicyError("predecessor usage ledger bytes drifted")
        try:
            snapshot = api_client.load_chained_usage_ledger(ledger_path)
        except api_client.UsageLedgerError as exc:
            raise MainRuntimePolicyError(
                f"predecessor usage ledger failed to load: {exc}") from exc
        summary = dict(snapshot.summary)
        if summary.get("events") != events or summary.get("unmatched_reservations") != 0:
            raise MainRuntimePolicyError("predecessor usage ledger shape drifted")
        if (
            Decimal(f"{float(summary.get('actual_spend_usd', 0.0)):.8f}") != settled
            or Decimal(f"{float(summary.get('uncertain_spend_usd', 0.0)):.8f}") != uncertain
        ):
            raise MainRuntimePolicyError(
                "predecessor usage ledger totals differ from the pinned accounting")
        ledger_verified = True
    return {
        "schema_version": PREDECESSOR_VOID_ACCOUNTING_SCHEMA,
        "record_id": PREDECESSOR_VOID_ACCOUNTING_ID,
        "record_raw_sha256": observed_sha,
        "predecessor_run_id": run_id,
        "predecessor_manifest_canonical_sha256": predecessor["manifest_canonical_sha256"],
        "predecessor_artifact_root": str(artifact_root),
        "accounted_spend_usd": format(accounted, "f"),
        "maximum_successor_expenditure_usd": format(ceiling, "f"),
        "expected_stage_total_usd": format(total, "f"),
        "ledger_verified": ledger_verified,
        "execution_authorized": False,
    }


def load_and_validate_predecessor_void_accounting(
    project_root: str | Path, *, verify_ledger: bool = True,
) -> dict[str, Any]:
    path = Path(project_root).resolve() / PREDECESSOR_VOID_ACCOUNTING_RELATIVE_PATH
    raw = _stable_file_bytes(path, "predecessor void accounting")
    return validate_predecessor_void_accounting(
        _load_object(path, "predecessor void accounting"), raw=raw,
        verify_ledger=verify_ledger)
