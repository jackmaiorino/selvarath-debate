"""Validate the r2 capability-preflight closure, carry-forward, and stage-family ledger.

Three append-only, evidence-only artifacts govern how the r2 capability-preflight relaunch
(execution identity ``5ab39b4d1479050683a37811a377538ec028ca491f28a88db6dbd23f79113aa8``) is
resolved before any r3 attempt can be authorized:

* ``phase2_preflight_r2_closure_2026-07-19.json`` -- like the r1 abort closure, binds the r2
  manifest/authorization hashes, the archived abort record, ``SHA256SUMS``, and the ledger id
  plus every event hash -- PLUS a ``resolution`` block that classifies the ambiguous Gemma call
  (``CLOSED_AMBIGUOUS_COUNTED_CHARGED_NO_OUTPUT``: never eligible for resume, counted at an
  adjudicated upper-bound spend, its eventual r3 counterpart a REPLACEMENT call under a new
  identity, never a resumption of the old one) and records the successful Qwen call's carry
  forward disposition.
* ``phase2_preflight_carryforward_2026-07-19.json`` -- binds the successful Qwen call's real
  result row, ledger success event, response id, and actual charge, and states the rule that r3
  must count it as 1 of the 1,060 logical cells without re-running it.
* ``phase2_stage_family_ledger_2026-07-19.json`` -- the aggregate accounting: a SINGLE $15 cap
  across every capability-preflight attempt (r1, r2, r3, ...), the carried-forward accounted
  spend, and the cap remaining for r3. Fresh per-attempt ledger directories/ledger ids never
  reset this stage-family cap.

This module validates the shape, the literal frozen facts, and the Decimal-exact arithmetic of
all three, and cross-checks that they agree with each other. It deliberately CANNOT do any of
the following: import a provider SDK, open a network connection, make a provider call, run as a
CLI action other than ``--check``, or write/mutate any file. Every check fails closed: missing,
mismatched, extra, or malformed data halts validation instead of being repaired or ignored.
"""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from rejudge import phase2_plan


DEFAULT_CLOSURE_PATH = Path(__file__).with_name("phase2_preflight_r2_closure_2026-07-19.json")
DEFAULT_CARRYFORWARD_PATH = Path(__file__).with_name(
    "phase2_preflight_carryforward_2026-07-19.json")
DEFAULT_LEDGER_PATH = Path(__file__).with_name("phase2_stage_family_ledger_2026-07-19.json")

# --- frozen historical facts (r1/r2 capability-preflight incident) ------------------------------

STAGE = "capability_preflight"

R1_EXECUTION_IDENTITY_SHA256 = "23060e5e08ca2b0c6856529d74184400bab5e8cb4468b29e641a465e35346ab6"
R2_EXECUTION_IDENTITY_SHA256 = "5ab39b4d1479050683a37811a377538ec028ca491f28a88db6dbd23f79113aa8"

R1_LEDGER_ID = "46d90ea8ce21475a87a54258566790ac"
R2_LEDGER_ID = "80832328bf0d474e8a402747b07f4c0b"

R1_CLOSURE_TRACKED_PATH = "rejudge/phase2_preflight_abort_closure_2026-07-19.json"
R1_CLOSURE_CANONICAL_SHA256 = "309968ab9d67dd7a2e4a8d096f2ce6695efea87ff894b52d336ae8f4434c13dd"

R2_CLOSURE_ID = "phase2_preflight_r2_closure_2026-07-19"
CARRYFORWARD_ID = "phase2_preflight_carryforward_2026-07-19"
STAGE_FAMILY_LEDGER_ID = "phase2_stage_family_ledger_2026-07-19"

SCHEMA_VERSION_CLOSURE = "phase2_preflight_r2_closure_v1"
SCHEMA_VERSION_CARRYFORWARD = "phase2_stage_family_carryforward_v1"
SCHEMA_VERSION_LEDGER = "phase2_stage_family_ledger_v1"

CLOSURE_STATUS = "closed_ambiguous_upper_bound_counted"

QWEN_MODEL = "Qwen/Qwen3.7-Plus"
QWEN_CALL_KEY = "52f5e228ab939a6023d91411aca3becf8d8a5dc5fab7c1c89af564849ec47939"
QWEN_ATTEMPT_ID = "1f1c34abbbe14543b6aa74842df3f6e0"
QWEN_PLANNING_CELL_KEY = (
    "phase2-pooled-hpr-2026-07-16-v1.qb-d9e52c3339ab:capability_qa:"
    "001fd59e7df85a7792981fda50a5a7cf175222b6ec61b46c6a946e33bce91e8d"
)
QWEN_ACTUAL_CHARGE_USD = "0.00107328"
QWEN_LEDGER_SUCCESS_EVENT_HASH = "e5b4305bda54063d1c9984cb64cd411fafae6503eb5dd0757be8d6eb41e83a08"
QWEN_RESPONSE_ID = "chatcmpl-e97c6b0f-7574-9b95-8a71-67e618d79448"

GEMMA_MODEL = "google/gemma-4-31B-it"
GEMMA_CALL_KEY = "c2b36c65bbb02bfbe1c31a252abbc934e2bbb163b92700baea664dbb8e093dfd"
GEMMA_ATTEMPT_ID = "72fa729a402545c799c4bb35d798aadf"
GEMMA_PLANNING_CELL_KEY = (
    "phase2-pooled-hpr-2026-07-16-v1.qb-d9e52c3339ab:capability_qa:"
    "0044a8665dadba42627513de95916ec6312916e7d71cb547aa25fb1268320062"
)
GEMMA_RESERVED_COST_USD = "0.00738601"
GEMMA_TRANSMISSION_MULTIPLIER = 3
GEMMA_UPPER_BOUND_SPEND_USD = "0.02215803"
GEMMA_LEDGER_RESERVED_EVENT_HASH = "0e21c0cfa9471aa531e6fc41656de7c8c19a1133f469a664d346dc1e46f8b7dd"
GEMMA_LEDGER_TERMINAL_EVENT_HASH = "a6f176fe8479bd6f24ec3e0c2b4691b79278b31e910c883ab999656bfe48fa8b"

GEMMA_CLASSIFICATION = "CLOSED_AMBIGUOUS_COUNTED_CHARGED_NO_OUTPUT"
GEMMA_R3_DISPOSITION = "replacement_call_under_new_identity"

STAGE_CAP_USD = "15.00000000"
CARRIED_FORWARD_ACCOUNTED_SPEND_USD = "0.02323131"
R3_AVAILABLE_CAP_USD = "14.97676869"

EXPECTED_LOGICAL_CELLS_TOTAL = 1060

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DECIMAL_USD_PATTERN = re.compile(r"^-?\d+\.\d{8}$")


class StageFamilyError(ValueError):
    """A stage-family closure/carryforward/ledger artifact is malformed or disagrees with the
    frozen r1/r2 capability-preflight incident record or with a sibling artifact."""


# --- generic fail-closed helpers (conventions shared with phase2_prompt_bundle.py / phase2_role_limits.py) --


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StageFamilyError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise StageFamilyError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise StageFamilyError(f"{label} fields drifted")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise StageFamilyError(f"{label} must be a non-empty string")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise StageFamilyError(f"{label} must be exactly true or false")
    return value


def _int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StageFamilyError(f"{label} must be an integer")
    return value


def _hex64(value: Any, label: str) -> str:
    text = _string(value, label)
    if not _HEX64.match(text):
        raise StageFamilyError(f"{label} must be a lowercase 64-character hex sha256 digest")
    return text


def _literal(value: Any, expected: Any, label: str) -> Any:
    if value != expected:
        raise StageFamilyError(f"{label} must be exactly {expected!r}, got {value!r}")
    return value


def _decimal_usd(value: Any, label: str) -> Decimal:
    """Require an exact 8-decimal-place USD string; never accept a JSON float."""
    if not isinstance(value, str) or not _DECIMAL_USD_PATTERN.match(value):
        raise StageFamilyError(
            f"{label} must be a decimal string with exactly 8 fractional digits, got {value!r}")
    return Decimal(value)


def _exact_decimal_usd(value: Any, expected: str, label: str) -> Decimal:
    parsed = _decimal_usd(value, label)
    if value != expected:
        raise StageFamilyError(f"{label} must be exactly {expected!r}, got {value!r}")
    return parsed


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StageFamilyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(token: str) -> Any:
    raise StageFamilyError(f"JSON must not contain the non-finite literal: {token}")


def _load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StageFamilyError(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StageFamilyError(f"{path} must contain a JSON object")
    return payload


# --- r2 closure -----------------------------------------------------------------------------

CLOSURE_TOP_KEYS = frozenset({
    "schema_version", "closure_id", "status", "stage", "attempt", "execution_identity_sha256",
    "r2_manifest_binding", "r2_authorization_binding", "reauthorization_binding", "archive",
    "ledger_evidence", "resolution", "codex_adversarial_review", "ledger_retired",
    "ledger_disposition", "execution_authorized", "note", "closed_at_utc",
    "closure_repo_git_head", "committed", "self_note",
})

AMBIGUOUS_CALL_KEYS = frozenset({
    "execution_call_key", "attempt_id", "model", "planning_cell_key", "request_fields_sha256",
    "ledger_reserved_event_hash", "ledger_terminal_event_hash", "exception_type",
    "exception_message", "durable_output_observed", "provider_dashboard_confirmation",
    "ambiguity_basis", "sdk_internal_retry_exposure", "classification",
    "classification_definition", "classification_scope_restriction", "provider_charge_truth",
    "accounting_treatment", "max_possible_transmissions", "resume_eligible",
    "adjudicated_reserved_cost_usd", "adjudicated_transmission_multiplier",
    "adjudicated_upper_bound_spend_usd", "adjudicated_upper_bound_spend_usd_derivation",
    "installed_sdk_evidence", "negative_evidence", "resume_eligibility",
    "resume_eligibility_note", "r3_disposition", "r3_disposition_note",
    "r3_binding_requirements",
})

CARRIED_FORWARD_CALL_KEYS = frozenset({
    "execution_call_key", "attempt_id", "model", "disposition", "note",
})


def _validate_ledger_events(events_raw: Any, *, ledger_id: str) -> list[Mapping[str, Any]]:
    events = [_mapping(e, "closure.ledger_evidence.events[]") for e in
              _list(events_raw, "closure.ledger_evidence.events")]
    if len(events) != 5:
        raise StageFamilyError("closure.ledger_evidence.events must have exactly 5 entries")
    prev_hash: str | None = None
    for index, event in enumerate(events):
        label = f"closure.ledger_evidence.events[{index}]"
        _literal(event.get("sequence"), index, f"{label}.sequence")
        _hex64(event.get("event_hash"), f"{label}.event_hash")
        if index == 0:
            if event.get("prev_event_hash") is not None:
                raise StageFamilyError(f"{label}.prev_event_hash must be null for the genesis event")
        else:
            _literal(event.get("prev_event_hash"), prev_hash, f"{label}.prev_event_hash")
        prev_hash = event["event_hash"]
    if events[0].get("status") != "ledger_genesis":
        raise StageFamilyError("closure.ledger_evidence.events[0].status must be ledger_genesis")
    if events[4].get("status") != "unknown_charge":
        raise StageFamilyError(
            "closure.ledger_evidence.events[4] (terminal event) status must be unknown_charge")
    if events[4].get("is_chain_head") is not True:
        raise StageFamilyError(
            "closure.ledger_evidence.events[4].is_chain_head must be exactly true")
    # Pin the two Qwen events and the two Gemma events to their known historical hashes here,
    # in the raw ledger events array itself, rather than a second time on the resolution block's
    # own reference fields (those are checked structurally against THIS array instead; see
    # validate_r2_closure's events_by_hash cross-check below).
    _literal(events[2]["event_hash"], QWEN_LEDGER_SUCCESS_EVENT_HASH, f"{ledger_id}: events[2].event_hash")
    _literal(
        events[3]["event_hash"], GEMMA_LEDGER_RESERVED_EVENT_HASH,
        f"{ledger_id}: events[3].event_hash")
    _literal(
        events[4]["event_hash"], GEMMA_LEDGER_TERMINAL_EVENT_HASH,
        f"{ledger_id}: events[4].event_hash")
    return events


def validate_r2_closure(doc: Mapping[str, Any]) -> None:
    """Validate the r2 abort closure plus its ambiguous-call/carry-forward resolution block."""
    doc = _mapping(doc, "closure")
    _exact_keys(doc, CLOSURE_TOP_KEYS, "closure")
    _literal(doc.get("schema_version"), SCHEMA_VERSION_CLOSURE, "closure.schema_version")
    _literal(doc.get("closure_id"), R2_CLOSURE_ID, "closure.closure_id")
    _literal(doc.get("status"), CLOSURE_STATUS, "closure.status")
    _literal(doc.get("stage"), STAGE, "closure.stage")
    _literal(doc.get("attempt"), "r2", "closure.attempt")
    _literal(
        doc.get("execution_identity_sha256"), R2_EXECUTION_IDENTITY_SHA256,
        "closure.execution_identity_sha256")

    manifest_binding = _mapping(doc.get("r2_manifest_binding"), "closure.r2_manifest_binding")
    for field in (
        "tracked_path", "canonical_sha256", "raw_file_sha256", "hash_method",
        "raw_file_sha256_note", "implementation_provenance", "prior_attempt_closure_binding",
        "role_limits_binding",
    ):
        if field not in manifest_binding:
            raise StageFamilyError(f"closure.r2_manifest_binding.{field} is required")
    _hex64(manifest_binding.get("canonical_sha256"), "closure.r2_manifest_binding.canonical_sha256")
    _hex64(manifest_binding.get("raw_file_sha256"), "closure.r2_manifest_binding.raw_file_sha256")
    prior_closure = _mapping(
        manifest_binding.get("prior_attempt_closure_binding"),
        "closure.r2_manifest_binding.prior_attempt_closure_binding")
    _literal(
        prior_closure.get("tracked_path"), R1_CLOSURE_TRACKED_PATH,
        "closure.r2_manifest_binding.prior_attempt_closure_binding.tracked_path")
    _literal(
        prior_closure.get("canonical_sha256"), R1_CLOSURE_CANONICAL_SHA256,
        "closure.r2_manifest_binding.prior_attempt_closure_binding.canonical_sha256")

    authorization_binding = _mapping(
        doc.get("r2_authorization_binding"), "closure.r2_authorization_binding")
    _hex64(
        authorization_binding.get("canonical_sha256"),
        "closure.r2_authorization_binding.canonical_sha256")
    auth_content = _mapping(
        authorization_binding.get("content"), "closure.r2_authorization_binding.content")
    _literal(
        auth_content.get("execution_identity_sha256"), R2_EXECUTION_IDENTITY_SHA256,
        "closure.r2_authorization_binding.content.execution_identity_sha256")
    _literal(auth_content.get("stage"), STAGE, "closure.r2_authorization_binding.content.stage")

    reauthorization_binding = _mapping(
        doc.get("reauthorization_binding"), "closure.reauthorization_binding")
    _hex64(
        reauthorization_binding.get("canonical_sha256"),
        "closure.reauthorization_binding.canonical_sha256")

    archive = _mapping(doc.get("archive"), "closure.archive")
    abort_json = _mapping(archive.get("abort_json"), "closure.archive.abort_json")
    abort_content = _mapping(abort_json.get("content"), "closure.archive.abort_json.content")
    _literal(
        abort_content.get("execution_identity_sha256"), R2_EXECUTION_IDENTITY_SHA256,
        "closure.archive.abort_json.content.execution_identity_sha256")
    _literal(abort_content.get("cells_completed"), 1, "closure.archive.abort_json.content.cells_completed")
    _literal(
        abort_content.get("reason"), "unknown_charge_halt",
        "closure.archive.abort_json.content.reason")
    sha256sums_file = _mapping(
        archive.get("sha256sums_file"), "closure.archive.sha256sums_file")
    listed_entries = _list(
        sha256sums_file.get("listed_entries"), "closure.archive.sha256sums_file.listed_entries")
    if len(listed_entries) != 4:
        raise StageFamilyError(
            "closure.archive.sha256sums_file.listed_entries must have exactly 4 entries")
    for entry in listed_entries:
        entry = _mapping(entry, "closure.archive.sha256sums_file.listed_entries[]")
        if entry.get("independently_recomputed_match") is not True:
            raise StageFamilyError(
                "every closure.archive.sha256sums_file.listed_entries[].independently_"
                "recomputed_match must be exactly true"
            )

    ledger_evidence = _mapping(doc.get("ledger_evidence"), "closure.ledger_evidence")
    _literal(ledger_evidence.get("ledger_id"), R2_LEDGER_ID, "closure.ledger_evidence.ledger_id")
    events = _validate_ledger_events(
        ledger_evidence.get("events"), ledger_id=ledger_evidence["ledger_id"])
    _literal(
        ledger_evidence.get("genesis_event_hash"), events[0]["event_hash"],
        "closure.ledger_evidence.genesis_event_hash")
    if ledger_evidence.get("live_ledger_directory_byte_identical_to_archive_mirror") is not True:
        raise StageFamilyError(
            "closure.ledger_evidence.live_ledger_directory_byte_identical_to_archive_mirror "
            "must be exactly true")

    resolution = _mapping(doc.get("resolution"), "closure.resolution")
    _exact_keys(resolution, {"carried_forward_call", "ambiguous_call"}, "closure.resolution")

    carried = _mapping(
        resolution.get("carried_forward_call"), "closure.resolution.carried_forward_call")
    _exact_keys(carried, CARRIED_FORWARD_CALL_KEYS, "closure.resolution.carried_forward_call")
    _literal(
        carried.get("execution_call_key"), QWEN_CALL_KEY,
        "closure.resolution.carried_forward_call.execution_call_key")
    _literal(
        carried.get("attempt_id"), QWEN_ATTEMPT_ID,
        "closure.resolution.carried_forward_call.attempt_id")
    _literal(carried.get("model"), QWEN_MODEL, "closure.resolution.carried_forward_call.model")
    _literal(
        carried.get("disposition"), "carried_forward_success",
        "closure.resolution.carried_forward_call.disposition")

    ambiguous = _mapping(resolution.get("ambiguous_call"), "closure.resolution.ambiguous_call")
    _exact_keys(ambiguous, AMBIGUOUS_CALL_KEYS, "closure.resolution.ambiguous_call")
    _literal(
        ambiguous.get("execution_call_key"), GEMMA_CALL_KEY,
        "closure.resolution.ambiguous_call.execution_call_key")
    _literal(
        ambiguous.get("attempt_id"), GEMMA_ATTEMPT_ID,
        "closure.resolution.ambiguous_call.attempt_id")
    _literal(ambiguous.get("model"), GEMMA_MODEL, "closure.resolution.ambiguous_call.model")
    _literal(
        ambiguous.get("planning_cell_key"), GEMMA_PLANNING_CELL_KEY,
        "closure.resolution.ambiguous_call.planning_cell_key")
    _hex64(
        ambiguous.get("ledger_reserved_event_hash"),
        "closure.resolution.ambiguous_call.ledger_reserved_event_hash")
    _hex64(
        ambiguous.get("ledger_terminal_event_hash"),
        "closure.resolution.ambiguous_call.ledger_terminal_event_hash")
    # The bound ledger event hashes are deliberately NOT pinned to hardcoded constants here;
    # instead they must actually appear, at the right sequence, in this same closure's own
    # ledger_evidence.events -- a structural, non-redundant cross-check rather than a second
    # hardcoded copy of the same two hashes already pinned inside _validate_ledger_events.
    events_by_hash = {event["event_hash"]: event for event in events}
    if ambiguous["ledger_reserved_event_hash"] not in events_by_hash:
        raise StageFamilyError(
            "closure.resolution.ambiguous_call.ledger_reserved_event_hash does not appear in "
            "closure.ledger_evidence.events")
    if ambiguous["ledger_terminal_event_hash"] not in events_by_hash:
        raise StageFamilyError(
            "closure.resolution.ambiguous_call.ledger_terminal_event_hash does not appear in "
            "closure.ledger_evidence.events")
    if events_by_hash[ambiguous["ledger_reserved_event_hash"]]["sequence"] != 3:
        raise StageFamilyError(
            "closure.resolution.ambiguous_call.ledger_reserved_event_hash must reference "
            "ledger_evidence.events[3]")
    if events_by_hash[ambiguous["ledger_terminal_event_hash"]]["sequence"] != 4:
        raise StageFamilyError(
            "closure.resolution.ambiguous_call.ledger_terminal_event_hash must reference "
            "ledger_evidence.events[4]")

    if ambiguous.get("durable_output_observed") is not False:
        raise StageFamilyError(
            "closure.resolution.ambiguous_call.durable_output_observed must be exactly false")
    _literal(
        ambiguous.get("classification"), GEMMA_CLASSIFICATION,
        "closure.resolution.ambiguous_call.classification")
    _literal(
        ambiguous.get("provider_charge_truth"), "unresolved",
        "closure.resolution.ambiguous_call.provider_charge_truth")
    _literal(
        ambiguous.get("accounting_treatment"), "charged_at_upper_bound",
        "closure.resolution.ambiguous_call.accounting_treatment")
    _literal(
        ambiguous.get("max_possible_transmissions"), GEMMA_TRANSMISSION_MULTIPLIER,
        "closure.resolution.ambiguous_call.max_possible_transmissions")
    if ambiguous.get("resume_eligible") is not False:
        raise StageFamilyError(
            "closure.resolution.ambiguous_call.resume_eligible must be exactly false")
    _literal(
        ambiguous.get("resume_eligibility"), "never_eligible_for_resume",
        "closure.resolution.ambiguous_call.resume_eligibility")
    _literal(
        ambiguous.get("r3_disposition"), GEMMA_R3_DISPOSITION,
        "closure.resolution.ambiguous_call.r3_disposition")

    reserved_cost = _exact_decimal_usd(
        ambiguous.get("adjudicated_reserved_cost_usd"), GEMMA_RESERVED_COST_USD,
        "closure.resolution.ambiguous_call.adjudicated_reserved_cost_usd")
    multiplier = _int(
        ambiguous.get("adjudicated_transmission_multiplier"),
        "closure.resolution.ambiguous_call.adjudicated_transmission_multiplier")
    if multiplier != GEMMA_TRANSMISSION_MULTIPLIER:
        raise StageFamilyError(
            "closure.resolution.ambiguous_call.adjudicated_transmission_multiplier must be "
            f"exactly {GEMMA_TRANSMISSION_MULTIPLIER}")
    upper_bound = _exact_decimal_usd(
        ambiguous.get("adjudicated_upper_bound_spend_usd"), GEMMA_UPPER_BOUND_SPEND_USD,
        "closure.resolution.ambiguous_call.adjudicated_upper_bound_spend_usd")
    if reserved_cost * multiplier != upper_bound:
        raise StageFamilyError(
            "closure.resolution.ambiguous_call: adjudicated_reserved_cost_usd x "
            "adjudicated_transmission_multiplier must equal adjudicated_upper_bound_spend_usd "
            "exactly (Decimal arithmetic)"
        )

    negative_evidence = _mapping(
        ambiguous.get("negative_evidence"), "closure.resolution.ambiguous_call.negative_evidence")
    for field in (
        "gemma_result_row_present", "gemma_response_id_present", "gemma_usage_response_present",
    ):
        if negative_evidence.get(field) is not False:
            raise StageFamilyError(
                f"closure.resolution.ambiguous_call.negative_evidence.{field} must be exactly "
                "false"
            )

    r3_requirements = _mapping(
        ambiguous.get("r3_binding_requirements"),
        "closure.resolution.ambiguous_call.r3_binding_requirements")
    _literal(
        r3_requirements.get("planning_cell_key"), GEMMA_PLANNING_CELL_KEY,
        "closure.resolution.ambiguous_call.r3_binding_requirements.planning_cell_key")
    _literal(
        r3_requirements.get("replacement_of_execution_call_key"), GEMMA_CALL_KEY,
        "closure.resolution.ambiguous_call.r3_binding_requirements."
        "replacement_of_execution_call_key")
    _literal(
        r3_requirements.get("replacement_of_attempt_id"), GEMMA_ATTEMPT_ID,
        "closure.resolution.ambiguous_call.r3_binding_requirements.replacement_of_attempt_id")
    _literal(
        r3_requirements.get("replacement_reason"), "closed_ambiguous_charged_no_output",
        "closure.resolution.ambiguous_call.r3_binding_requirements.replacement_reason")

    if doc.get("ledger_retired") is not True:
        raise StageFamilyError("closure.ledger_retired must be exactly true")
    ledger_disposition = _mapping(doc.get("ledger_disposition"), "closure.ledger_disposition")
    _exact_decimal_usd(
        ledger_disposition.get("effective_billable_amount_for_cumulative_accounting_usd"),
        GEMMA_UPPER_BOUND_SPEND_USD,
        "closure.ledger_disposition.effective_billable_amount_for_cumulative_accounting_usd")
    if ledger_disposition.get("reused_for_future_attempts") is not False:
        raise StageFamilyError(
            "closure.ledger_disposition.reused_for_future_attempts must be exactly false")

    if doc.get("execution_authorized") is not False:
        raise StageFamilyError("closure.execution_authorized must be exactly false")
    if doc.get("committed") is not False:
        raise StageFamilyError("closure.committed must be exactly false")


# --- carryforward ----------------------------------------------------------------------------

CARRYFORWARD_TOP_KEYS = frozenset({
    "schema_version", "carryforward_id", "stage", "source_attempt",
    "source_execution_identity_sha256", "r2_closure_binding", "call", "results_row",
    "response_metadata", "ledger_success_event", "actual_charge_usd", "actual_charge_usd_note",
    "carryforward_rule", "execution_authorized", "note", "closed_at_utc", "committed",
})


def validate_carryforward(doc: Mapping[str, Any]) -> None:
    """Validate the carried-forward successful Qwen call from r2."""
    doc = _mapping(doc, "carryforward")
    _exact_keys(doc, CARRYFORWARD_TOP_KEYS, "carryforward")
    _literal(
        doc.get("schema_version"), SCHEMA_VERSION_CARRYFORWARD, "carryforward.schema_version")
    _literal(doc.get("carryforward_id"), CARRYFORWARD_ID, "carryforward.carryforward_id")
    _literal(doc.get("stage"), STAGE, "carryforward.stage")
    _literal(doc.get("source_attempt"), "r2", "carryforward.source_attempt")
    _literal(
        doc.get("source_execution_identity_sha256"), R2_EXECUTION_IDENTITY_SHA256,
        "carryforward.source_execution_identity_sha256")

    call = _mapping(doc.get("call"), "carryforward.call")
    _literal(call.get("execution_call_key"), QWEN_CALL_KEY, "carryforward.call.execution_call_key")
    _literal(call.get("attempt_id"), QWEN_ATTEMPT_ID, "carryforward.call.attempt_id")
    _literal(call.get("call_index"), 0, "carryforward.call.call_index")
    _literal(call.get("call_role"), "capability_qa", "carryforward.call.call_role")
    _literal(call.get("model"), QWEN_MODEL, "carryforward.call.model")
    _literal(
        call.get("planning_cell_key"), QWEN_PLANNING_CELL_KEY,
        "carryforward.call.planning_cell_key")

    results_row = _mapping(doc.get("results_row"), "carryforward.results_row")
    raw_line = _string(results_row.get("raw_line"), "carryforward.results_row.raw_line")
    declared_sha = _hex64(
        results_row.get("raw_line_sha256"), "carryforward.results_row.raw_line_sha256")
    import hashlib
    observed_sha = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()
    if observed_sha != declared_sha:
        raise StageFamilyError(
            "carryforward.results_row.raw_line_sha256 does not match sha256(raw_line); "
            f"declared {declared_sha}, recomputed {observed_sha}"
        )
    try:
        parsed_line = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        raise StageFamilyError(
            f"carryforward.results_row.raw_line is not valid JSON: {exc}") from exc
    if not isinstance(parsed_line, Mapping):
        raise StageFamilyError("carryforward.results_row.raw_line must decode to a JSON object")
    if parsed_line.get("execution_call_key") != QWEN_CALL_KEY:
        raise StageFamilyError(
            "carryforward.results_row.raw_line's own execution_call_key disagrees with "
            "carryforward.call.execution_call_key"
        )
    if parsed_line.get("verdict") != results_row.get("verdict"):
        raise StageFamilyError(
            "carryforward.results_row.verdict disagrees with the verdict embedded in raw_line")

    response_metadata = _mapping(
        doc.get("response_metadata"), "carryforward.response_metadata")
    _literal(
        response_metadata.get("response_id"), QWEN_RESPONSE_ID,
        "carryforward.response_metadata.response_id")

    ledger_event = _mapping(doc.get("ledger_success_event"), "carryforward.ledger_success_event")
    _literal(ledger_event.get("ledger_id"), R2_LEDGER_ID, "carryforward.ledger_success_event.ledger_id")
    _literal(
        ledger_event.get("event_hash"), QWEN_LEDGER_SUCCESS_EVENT_HASH,
        "carryforward.ledger_success_event.event_hash")
    _literal(ledger_event.get("status"), "success", "carryforward.ledger_success_event.status")

    _exact_decimal_usd(
        doc.get("actual_charge_usd"), QWEN_ACTUAL_CHARGE_USD, "carryforward.actual_charge_usd")

    if doc.get("execution_authorized") is not False:
        raise StageFamilyError("carryforward.execution_authorized must be exactly false")
    if doc.get("committed") is not False:
        raise StageFamilyError("carryforward.committed must be exactly false")


# --- stage-family ledger -----------------------------------------------------------------------

LEDGER_TOP_KEYS = frozenset({
    "schema_version", "stage_family_ledger_id", "stage", "stage_cap_usd", "stage_cap_policy",
    "attempts", "carried_forward_accounted_spend_usd",
    "carried_forward_accounted_spend_usd_derivation", "r3_available_cap_usd",
    "r3_available_cap_usd_derivation", "carryforward_completion_credit",
    "cap_never_reset_by_fresh_ledger", "cap_never_reset_by_fresh_ledger_note",
    "provider_reconciliation_cross_check", "execution_authorized", "note", "generated_at_utc",
    "committed",
})

R1_ATTEMPT_KEYS = frozenset({
    "attempt", "execution_identity_sha256", "ledger_id", "closure_tracked_path",
    "closure_canonical_sha256", "disposition", "cells_completed", "accounted_spend_usd",
    "accounted_spend_usd_note",
})

R2_ATTEMPT_KEYS = frozenset({
    "attempt", "execution_identity_sha256", "ledger_id", "closure_tracked_path",
    "carryforward_tracked_path", "cells_completed", "cells_ambiguous", "components",
    "accounted_spend_usd", "accounted_spend_usd_derivation",
})

COMPONENT_KEYS = frozenset({
    "execution_call_key", "model", "disposition", "spend_usd", "spend_usd_basis",
})


def _validate_r1_attempt(attempt: Mapping[str, Any]) -> Decimal:
    _exact_keys(attempt, R1_ATTEMPT_KEYS, "ledger.attempts[r1]")
    _literal(attempt.get("attempt"), "r1", "ledger.attempts[r1].attempt")
    _literal(
        attempt.get("execution_identity_sha256"), R1_EXECUTION_IDENTITY_SHA256,
        "ledger.attempts[r1].execution_identity_sha256")
    _literal(attempt.get("ledger_id"), R1_LEDGER_ID, "ledger.attempts[r1].ledger_id")
    _literal(
        attempt.get("closure_tracked_path"), R1_CLOSURE_TRACKED_PATH,
        "ledger.attempts[r1].closure_tracked_path")
    _literal(
        attempt.get("closure_canonical_sha256"), R1_CLOSURE_CANONICAL_SHA256,
        "ledger.attempts[r1].closure_canonical_sha256")
    _literal(attempt.get("cells_completed"), 0, "ledger.attempts[r1].cells_completed")
    return _exact_decimal_usd(
        attempt.get("accounted_spend_usd"), "0.00000000", "ledger.attempts[r1].accounted_spend_usd")


def _validate_r2_attempt(attempt: Mapping[str, Any]) -> Decimal:
    _exact_keys(attempt, R2_ATTEMPT_KEYS, "ledger.attempts[r2]")
    _literal(attempt.get("attempt"), "r2", "ledger.attempts[r2].attempt")
    _literal(
        attempt.get("execution_identity_sha256"), R2_EXECUTION_IDENTITY_SHA256,
        "ledger.attempts[r2].execution_identity_sha256")
    _literal(attempt.get("ledger_id"), R2_LEDGER_ID, "ledger.attempts[r2].ledger_id")
    _literal(attempt.get("cells_completed"), 1, "ledger.attempts[r2].cells_completed")
    _literal(attempt.get("cells_ambiguous"), 1, "ledger.attempts[r2].cells_ambiguous")

    components = [
        _mapping(c, "ledger.attempts[r2].components[]")
        for c in _list(attempt.get("components"), "ledger.attempts[r2].components")
    ]
    if len(components) != 2:
        raise StageFamilyError("ledger.attempts[r2].components must have exactly 2 entries")
    by_key = {}
    component_sum = Decimal("0")
    for component in components:
        _exact_keys(component, COMPONENT_KEYS, "ledger.attempts[r2].components[]")
        key = _string(
            component.get("execution_call_key"),
            "ledger.attempts[r2].components[].execution_call_key")
        by_key[key] = component
        component_sum += _decimal_usd(
            component.get("spend_usd"), "ledger.attempts[r2].components[].spend_usd")

    if QWEN_CALL_KEY not in by_key:
        raise StageFamilyError(
            "ledger.attempts[r2].components is missing the carried-forward Qwen call")
    if GEMMA_CALL_KEY not in by_key:
        raise StageFamilyError(
            "ledger.attempts[r2].components is missing the closed-ambiguous Gemma call")
    qwen_component = by_key[QWEN_CALL_KEY]
    _literal(
        qwen_component.get("model"), QWEN_MODEL,
        "ledger.attempts[r2].components[qwen].model")
    _literal(
        qwen_component.get("disposition"), "carried_forward_success",
        "ledger.attempts[r2].components[qwen].disposition")
    _exact_decimal_usd(
        qwen_component.get("spend_usd"), QWEN_ACTUAL_CHARGE_USD,
        "ledger.attempts[r2].components[qwen].spend_usd")

    gemma_component = by_key[GEMMA_CALL_KEY]
    _literal(
        gemma_component.get("model"), GEMMA_MODEL,
        "ledger.attempts[r2].components[gemma].model")
    _literal(
        gemma_component.get("disposition"), GEMMA_CLASSIFICATION,
        "ledger.attempts[r2].components[gemma].disposition")
    _exact_decimal_usd(
        gemma_component.get("spend_usd"), GEMMA_UPPER_BOUND_SPEND_USD,
        "ledger.attempts[r2].components[gemma].spend_usd")

    accounted = _decimal_usd(
        attempt.get("accounted_spend_usd"), "ledger.attempts[r2].accounted_spend_usd")
    if accounted != component_sum:
        raise StageFamilyError(
            "ledger.attempts[r2].accounted_spend_usd does not equal the Decimal-exact sum of "
            "its own components[].spend_usd"
        )
    return accounted


def validate_stage_family_ledger(doc: Mapping[str, Any]) -> None:
    """Validate the aggregate stage-family accounting across every capability-preflight attempt."""
    doc = _mapping(doc, "ledger")
    _exact_keys(doc, LEDGER_TOP_KEYS, "ledger")
    _literal(doc.get("schema_version"), SCHEMA_VERSION_LEDGER, "ledger.schema_version")
    _literal(
        doc.get("stage_family_ledger_id"), STAGE_FAMILY_LEDGER_ID,
        "ledger.stage_family_ledger_id")
    _literal(doc.get("stage"), STAGE, "ledger.stage")

    stage_cap = _exact_decimal_usd(
        doc.get("stage_cap_usd"), STAGE_CAP_USD, "ledger.stage_cap_usd")

    attempts = [
        _mapping(a, "ledger.attempts[]") for a in _list(doc.get("attempts"), "ledger.attempts")
    ]
    if len(attempts) != 2:
        raise StageFamilyError("ledger.attempts must have exactly 2 entries (r1, r2)")
    attempts_by_id = {a.get("attempt"): a for a in attempts}
    if set(attempts_by_id) != {"r1", "r2"}:
        raise StageFamilyError("ledger.attempts must contain exactly one r1 entry and one r2 entry")

    r1_spend = _validate_r1_attempt(attempts_by_id["r1"])
    r2_spend = _validate_r2_attempt(attempts_by_id["r2"])
    total_spend = r1_spend + r2_spend

    carried_forward = _exact_decimal_usd(
        doc.get("carried_forward_accounted_spend_usd"), CARRIED_FORWARD_ACCOUNTED_SPEND_USD,
        "ledger.carried_forward_accounted_spend_usd")
    if carried_forward != total_spend:
        raise StageFamilyError(
            "ledger.carried_forward_accounted_spend_usd does not equal the Decimal-exact sum "
            "of every ledger.attempts[].accounted_spend_usd"
        )

    r3_available = _exact_decimal_usd(
        doc.get("r3_available_cap_usd"), R3_AVAILABLE_CAP_USD, "ledger.r3_available_cap_usd")
    if stage_cap - carried_forward != r3_available:
        raise StageFamilyError(
            "ledger.r3_available_cap_usd does not equal stage_cap_usd minus "
            "carried_forward_accounted_spend_usd exactly (Decimal arithmetic)"
        )
    if r3_available < 0:
        raise StageFamilyError("ledger.r3_available_cap_usd must not be negative")

    completion_credit = _mapping(
        doc.get("carryforward_completion_credit"), "ledger.carryforward_completion_credit")
    _literal(
        completion_credit.get("logical_cells_total"), EXPECTED_LOGICAL_CELLS_TOTAL,
        "ledger.carryforward_completion_credit.logical_cells_total")
    _literal(
        completion_credit.get("logical_cells_already_completed_and_credited"), 1,
        "ledger.carryforward_completion_credit.logical_cells_already_completed_and_credited")

    if doc.get("cap_never_reset_by_fresh_ledger") is not True:
        raise StageFamilyError("ledger.cap_never_reset_by_fresh_ledger must be exactly true")

    if doc.get("execution_authorized") is not False:
        raise StageFamilyError("ledger.execution_authorized must be exactly false")
    if doc.get("committed") is not False:
        raise StageFamilyError("ledger.committed must be exactly false")


# --- cross-artifact consistency ------------------------------------------------------------


def validate_stage_family(
    closure: Mapping[str, Any], carryforward: Mapping[str, Any], ledger: Mapping[str, Any],
) -> None:
    """Validate all three artifacts individually, then cross-check they agree with each other.

    Each of the three files can drift independently (hand-edited, partially regenerated, ...);
    this function is the only place that guarantees they still tell one consistent story.
    """
    validate_r2_closure(closure)
    validate_carryforward(carryforward)
    validate_stage_family_ledger(ledger)

    resolution = closure["resolution"]
    ambiguous = resolution["ambiguous_call"]
    carried = resolution["carried_forward_call"]

    if carryforward["source_execution_identity_sha256"] != closure["execution_identity_sha256"]:
        raise StageFamilyError(
            "carryforward.source_execution_identity_sha256 disagrees with "
            "closure.execution_identity_sha256"
        )
    if carryforward["call"]["execution_call_key"] != carried["execution_call_key"]:
        raise StageFamilyError(
            "carryforward.call.execution_call_key disagrees with "
            "closure.resolution.carried_forward_call.execution_call_key"
        )

    r2_attempt = next(a for a in ledger["attempts"] if a["attempt"] == "r2")
    components_by_key = {c["execution_call_key"]: c for c in r2_attempt["components"]}

    if components_by_key[QWEN_CALL_KEY]["spend_usd"] != carryforward["actual_charge_usd"]:
        raise StageFamilyError(
            "stage-family ledger's qwen component spend_usd disagrees with "
            "carryforward.actual_charge_usd"
        )
    if (
        components_by_key[GEMMA_CALL_KEY]["spend_usd"]
        != ambiguous["adjudicated_upper_bound_spend_usd"]
    ):
        raise StageFamilyError(
            "stage-family ledger's gemma component spend_usd disagrees with "
            "closure.resolution.ambiguous_call.adjudicated_upper_bound_spend_usd"
        )
    if components_by_key[GEMMA_CALL_KEY]["disposition"] != ambiguous["classification"]:
        raise StageFamilyError(
            "stage-family ledger's gemma component disposition disagrees with "
            "closure.resolution.ambiguous_call.classification"
        )


def load_and_validate_all(
    closure_path: str | Path = DEFAULT_CLOSURE_PATH,
    carryforward_path: str | Path = DEFAULT_CARRYFORWARD_PATH,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    closure = _load_json(closure_path)
    carryforward = _load_json(carryforward_path)
    ledger = _load_json(ledger_path)
    validate_stage_family(closure, carryforward, ledger)
    return closure, carryforward, ledger


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--closure", default=str(DEFAULT_CLOSURE_PATH))
    parser.add_argument("--carryforward", default=str(DEFAULT_CARRYFORWARD_PATH))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER_PATH))
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("only --check is supported")
    closure, carryforward, ledger = load_and_validate_all(
        args.closure, args.carryforward, args.ledger)
    print(
        "verified r2 capability-preflight closure/carryforward/stage-family-ledger triple; "
        f"closure_canonical_sha256={phase2_plan.canonical_sha256(closure)}; "
        f"carryforward_canonical_sha256={phase2_plan.canonical_sha256(carryforward)}; "
        f"ledger_canonical_sha256={phase2_plan.canonical_sha256(ledger)}; "
        f"r3_available_cap_usd={ledger['r3_available_cap_usd']}; "
        "execution_authorized=NO"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
