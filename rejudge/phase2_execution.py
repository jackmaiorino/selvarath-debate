"""Phase-2 execution control plane: identity, call keys, and resume audits only.

This module deliberately CANNOT do any of the following:

* Import a provider SDK, open a network connection, or make a provider call.
* Run as a CLI or otherwise be invoked as a standalone entry point.
* Create, write, or otherwise persist any file. It only reads tracked repo artifacts
  under an explicit ``project_root`` and returns in-memory results.
* Persist an "authorized" manifest. Authorization lives only in a separate,
  caller-supplied authorization record; nothing in this module can flip a manifest
  from unauthorized to authorized, and no manifest field can claim authorization.

Its job is narrow and fail-closed: validate an external Phase-2 execution manifest
against the frozen design protocol and every artifact it binds, derive stable
per-call execution identities that are non-circular with respect to the manifest's
own execution identity, and audit a resume (planned calls vs. durable outputs vs.
ledger events) so a crashed or interrupted run can never be silently replayed or
silently marked done. Every check fails closed: missing, mismatched, extra, or
malformed data halts validation instead of being repaired, defaulted, or ignored.

The only stage this version actually validates end-to-end is ``capability_preflight``.
``gemma_recovery_or_waiver``, ``canary``, and ``main`` are real, frozen-protocol stage
names but are unconditionally unsupported here and raise :class:`UnsupportedStageError`
without further inspection of the manifest.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from rejudge import phase2_plan
from rejudge import phase2_prompt_bundle as prompt_bundle
from rejudge import phase2_provider_price_snapshot as price_snapshot
from rejudge import phase2_resolvability_ai_review as ai_review


STAGE_CAPABILITY_PREFLIGHT = "capability_preflight"
CAPABILITY_CALL_ROLE = "capability_qa"
EXPECTED_CAPABILITY_CELL_COUNT = 1060

DEFAULT_PROTOCOL_RELATIVE_PATH = Path("rejudge/phase2_protocol.json")
DEFAULT_COMBINED_AI_AUDIT_RELATIVE_PATH = Path("rejudge/phase2_resolvability_ai_review.json")
DEFAULT_A1_AMENDMENT_RELATIVE_PATH = Path(
    "rejudge/phase2_resolvability_review_amendment_2026-07-16.json")
DEFAULT_PROMPT_BUNDLE_RELATIVE_PATH = Path("rejudge/phase2_prompt_bundle.json")
DEFAULT_PRICE_SNAPSHOT_RELATIVE_PATH = Path(
    "rejudge/phase2_provider_price_snapshot_2026-07-18.json")
DEFAULT_UV_LOCK_RELATIVE_PATH = Path("uv.lock")
DEFAULT_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH = Path(
    "rejudge/phase2_prompt_bundle_approval_2026-07-18.json")

# --- prompt-bundle owner-methods-approval artifact: frozen literal bindings ---------------------
#
# Per the frozen governance, the candidate prompt bundle itself never changes status (it stays
# ``candidate_pending_owner_methods_review`` forever). The owner's 2026-07-18 methods-review
# approval instead lives ONLY in a separate, append-only external artifact that binds the
# bundle's observed canonical hash. That artifact never itself claims execution authority
# (``execution_authorized`` is always literally ``false`` inside it); it only ever establishes
# that the literal wording was reviewed and accepted. Every literal below is frozen from the
# real, already-committed artifact at ``rejudge/phase2_prompt_bundle_approval_2026-07-18.json``.
PROMPT_BUNDLE_APPROVAL_SCHEMA_VERSION = "phase2_prompt_bundle_owner_approval_v1"
PROMPT_BUNDLE_APPROVAL_ID = "phase2_prompt_bundle_methods_approval_2026-07-18"
PROMPT_BUNDLE_APPROVAL_APPROVER = "Jack Maiorino"
PROMPT_BUNDLE_APPROVAL_CHANNEL = (
    "direct chat approval to the assistant session, recorded same day")
PROMPT_BUNDLE_APPROVAL_NOTE = (
    "Owner methods approval of the candidate bundle wording only. This record is append-only "
    "and grants no execution authority: paid stages still require their own execution "
    "manifests binding this bundle's canonical hash plus separate stage spend authorizations "
    "under the frozen transition model."
)
PROMPT_BUNDLE_APPROVAL_SCOPE: tuple[str, ...] = (
    "candidate literal wording of all 17 template families",
    "checker reply-token alignment to the query-gate parser vocabulary (allow/reject/unresolved)",
    "legacy template replaced with byte-exact pilot judge prompts from experiment_protocol.json",
    "condition_composition map binding each frozen protocol condition to exact template families",
    "honest-only 400-word continuity asymmetry preserved unnormalized",
)
PROMPT_BUNDLE_APPROVAL_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "approval_id",
    "protocol_id",
    "approved_bundle_tracked_path",
    "approved_bundle_canonical_sha256",
    "approved_bundle_commit",
    "scope",
    "approver",
    "approved_at_utc",
    "approval_channel",
    "execution_authorized",
    "note",
})
_APPROVED_BUNDLE_COMMIT_MIN_LEN = 7
_APPROVED_BUNDLE_COMMIT_MAX_LEN = 40
_HEX_DIGITS = frozenset("0123456789abcdef")

MANIFEST_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "stage",
    "protocol_canonical_sha256",
    "a1_amendment_canonical_sha256",
    "combined_ai_audit_canonical_sha256",
    "question_bank_bundle_sha256",
    "prompt_bundle_canonical_sha256",
    "prompt_bundle_declared_status",
    "prompt_bundle_approval_tracked_path",
    "prompt_bundle_approval_canonical_sha256",
    "per_model_role_limits_artifact",
    "provider_request_fields_artifact",
    "provider_price_snapshot_canonical_sha256",
    "uv_lock_sha256",
    "seed_policy",
    "side_assignment_policy",
    "satisfied_prerequisites",
    "ledger",
    "planning_cell_keys",
    "provider_call_inventory",
    "stage_cap_usd",
    "cumulative_cap_usd",
    "cost_forecast",
    "storage_policy",
    "provider_reconciliation_evidence",
})

EXPECTED_CALL_ENTRY_KEYS: frozenset[str] = frozenset({
    "execution_call_key",
    "planning_cell_key",
    "call_role",
    "call_index",
    "model",
    "seed",
    "side",
    "request_fields_sha256",
})

ARTIFACT_BINDING_KEYS: frozenset[str] = frozenset({"path", "sha256"})
LEDGER_KEYS: frozenset[str] = frozenset({"path", "ledger_identity"})
AUTHORIZATION_KEYS: frozenset[str] = frozenset({
    "execution_identity_sha256",
    "stage",
    "stage_cap_usd",
    "cumulative_cap_usd",
    "approver",
    "approved_at_utc",
    "approval_basis_tracked_path",
    "approval_basis_sha256",
})

_TERMINAL_LEDGER_STATUSES: frozenset[str] = frozenset({
    "success", "charged_malformed", "unknown_charge", "released_no_charge",
})


class Phase2ExecutionError(Exception):
    """Base class for every fail-closed Phase-2 execution-control-plane error."""


class ManifestValidationError(Phase2ExecutionError):
    """The execution manifest, or one of the artifacts it binds, is invalid."""


class UnsupportedStageError(Phase2ExecutionError):
    """The manifest names a real stage that this version does not support."""


class ExecutionAuthorityError(Phase2ExecutionError):
    """The manifest's execution identity is not authorized to run."""


class ResumeAuditError(Phase2ExecutionError):
    """``output_rows`` or ``usage_events`` passed to :func:`audit_resume` are unusable."""


class ResumeDisposition(str, Enum):
    """The three, and only three, outcomes a manifested call can resolve to."""

    TODO = "todo"
    COMPLETE = "complete"
    BLOCKED_RECONCILIATION = "blocked_reconciliation"


@dataclass(frozen=True, slots=True)
class ValidatedExecutionManifest:
    """The immutable result of a successful :func:`validate_execution_manifest` call."""

    stage: str
    protocol_id: str
    execution_identity: Mapping[str, Any]
    execution_identity_sha256: str
    stage_cap_usd: float
    cumulative_cap_usd: float
    planning_cell_keys: tuple[str, ...]
    provider_call_inventory: tuple[Mapping[str, Any], ...]
    authorized: bool
    authorization: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class ResumeAudit:
    """The immutable result of a successful :func:`audit_resume` call."""

    stage: str
    disposition: ResumeDisposition
    per_call: Mapping[str, ResumeDisposition]
    todo_call_keys: tuple[str, ...]
    blockers: tuple[str, ...]
    counts: Mapping[str, int]


# --- small shared helpers, matching rejudge/phase2_prompt_bundle.py conventions --------------


def _mapping(
    value: Any, label: str, error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise error_cls(f"{label} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: Iterable[str], label: str,
    error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> None:
    if set(value) != set(expected):
        raise error_cls(f"{label} fields drifted")


def _string(
    value: Any, label: str, error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error_cls(f"{label} must be a non-empty string")
    return value


def _sha256_hex(
    value: Any, label: str, error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise error_cls(f"{label} must be a SHA-256 hex digest")
    return value


def _finite_positive_number(
    value: Any, label: str, error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_cls(f"{label} must be a number")
    try:
        number = float(value)
    except OverflowError:
        # Arbitrary-precision JSON integers (Python ints have no size limit) can be too
        # large for float() to represent; that must fail closed through this function's
        # own typed error, not escape as a raw builtin OverflowError.
        raise error_cls(f"{label} must be a finite, positive number") from None
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        raise error_cls(f"{label} must be a finite, positive number")
    return number


def _parse_utc_timestamp(
    value: Any, label: str, error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise error_cls(f"{label} must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise error_cls(f"{label} is invalid: {exc}") from exc
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        # Defensive only: value is reconstructed above as removesuffix("Z") + "+00:00", so
        # any successful fromisoformat() parse always has a zero UTC offset here. No known
        # input reaches this branch; it is kept as a guard against future changes to the
        # reconstruction above rather than as a currently reachable check.
        raise error_cls(f"{label} must be UTC")


def _load_json_object(
    path: Path, error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_cls(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise error_cls(f"{path} must contain a JSON object")
    return payload


def _raw_file_sha256(
    path: Path, error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> str:
    """Hash raw file bytes. Never interchange with :func:`canonical_sha256`."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise error_cls(f"could not read {path}: {exc}") from exc
    return hashlib.sha256(data).hexdigest()


def _resolve_bound_path(
    root: Path, path_value: str, label: str,
    error_cls: type[Phase2ExecutionError] = ManifestValidationError,
    *, allow_absolute: bool = True,
) -> Path:
    """Resolve a manifest- or artifact-declared path, blocking relative-path traversal.

    A *relative* ``path_value`` must resolve inside ``root``; a ``..``-laden relative path
    that would otherwise escape the intended base directory fails closed instead. An
    *absolute* ``path_value`` is, by default, honored verbatim: several artifact bindings in
    this module (per-model role limits, provider request fields, satisfied-prerequisite
    waivers, the authorization approval basis) are deliberately bound from outside the
    tracked repository -- e.g. materialization-pending tmp copies -- and that established,
    intentional pattern is preserved here rather than silently narrowed. Callers that freeze
    a binding as always repository-relative (see :func:`_bind_json_artifact_checked`) must
    pass ``allow_absolute=False`` so an absolute ``path_value`` -- which would otherwise
    bypass the containment check entirely -- fails closed instead.
    """
    candidate = Path(path_value)
    if candidate.is_absolute():
        if not allow_absolute:
            raise error_cls(
                f"{label} must be a path relative to project_root, not absolute: "
                f"{path_value!r}")
        return candidate
    root_resolved = root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise error_cls(f"{label} escapes the repository root: {path_value!r}") from None
    return resolved


def _bind_json_artifact_checked(root: Path, entry: Any, label: str) -> str:
    """Like :func:`_bind_json_artifact`, additionally blocking relative-path traversal.

    Used for artifact bindings that this module freezes as always relative to a
    repository-style ``root`` (unlike the older, deliberately-anywhere artifact bindings
    that predate this check): missing file, hash mismatch, an absolute path, or a relative
    path that escapes ``root`` all fail closed.
    """
    mapping = _mapping(entry, label)
    path_value = _string(mapping.get("path"), f"{label}.path")
    _resolve_bound_path(root, path_value, f"{label}.path", allow_absolute=False)
    return _bind_json_artifact(root, entry, label)


def _load_strict_json_object(
    path: Path, error_cls: type[Phase2ExecutionError] = ManifestValidationError,
) -> dict[str, Any]:
    """Strictly parse a JSON object artifact: no duplicate keys, no NaN/Infinity."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise error_cls(f"could not read {path}: {exc}") from exc
    try:
        payload = json.loads(
            text, object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except json.JSONDecodeError as exc:
        raise error_cls(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise error_cls(f"{path} must contain a JSON object")
    return payload


def _bind_json_artifact(root: Path, entry: Any, label: str) -> str:
    """Bind a ``{"path", "sha256"}`` reference and recompute its canonical-JSON hash.

    Fails closed if the artifact is missing: a not-yet-materialized artifact is a
    valid thing to bind a path+hash at, but it can never validate until the real
    file exists and matches.
    """
    mapping = _mapping(entry, label)
    _exact_keys(mapping, ARTIFACT_BINDING_KEYS, label)
    path_value = _string(mapping.get("path"), f"{label}.path")
    sha_value = _sha256_hex(mapping.get("sha256"), f"{label}.sha256")
    artifact_path = root / path_value
    if not artifact_path.is_file():
        raise ManifestValidationError(f"{label} artifact is missing: {artifact_path}")
    payload = _load_json_object(artifact_path, ManifestValidationError)
    observed = canonical_sha256(payload)
    if observed != sha_value:
        raise ManifestValidationError(
            f"{label} hash drift: manifest bound {sha_value}, observed {observed}")
    return observed


# --- public hashing / identity primitives -----------------------------------------------------


canonical_sha256 = phase2_plan.canonical_sha256


def build_execution_identity(
    *,
    schema_version: str,
    stage: str,
    protocol_canonical_sha256: str,
    a1_amendment_canonical_sha256: str,
    combined_ai_audit_canonical_sha256: str,
    question_bank_bundle_sha256: str,
    prompt_bundle_canonical_sha256: str,
    prompt_bundle_declared_status: str,
    prompt_bundle_approval_artifact: Mapping[str, str],
    per_model_role_limits_artifact: Mapping[str, str],
    provider_request_fields_artifact: Mapping[str, str],
    provider_price_snapshot_canonical_sha256: str,
    uv_lock_sha256: str,
    seed_policy: str,
    side_assignment_policy: str,
    satisfied_prerequisites: Mapping[str, Mapping[str, str]],
    ledger: Mapping[str, str],
    planning_cell_keys: Sequence[str],
    provider_call_inventory_entries: Sequence[Mapping[str, Any]],
    stage_cap_usd: float,
    cumulative_cap_usd: float,
    cost_forecast: Mapping[str, str],
    storage_policy: Mapping[str, str],
    provider_reconciliation_evidence: Mapping[str, str],
) -> dict[str, Any]:
    """Assemble the execution-identity dict from already-verified pieces.

    Pure data assembly: no I/O, no validation. Shared by :func:`validate_execution_manifest`
    (which calls it with values it just read and verified from disk) and by tooling/tests
    that need to derive the identity a manifest would have to produce for its
    ``execution_call_key`` values. ``provider_call_inventory_entries`` are the full manifest
    entries (each may or may not already carry ``execution_call_key``); this function strips
    that field for hashing so the inventory contribution to the identity never depends on
    values the identity itself is used to derive (non-circular).
    """
    ordered_planning_keys = sorted(str(key) for key in planning_cell_keys)
    inventory_for_hash = [
        {key: value for key, value in entry.items() if key != "execution_call_key"}
        for entry in provider_call_inventory_entries
    ]
    return {
        "schema_version": schema_version,
        "stage": stage,
        "protocol_canonical_sha256": protocol_canonical_sha256,
        "a1_amendment_canonical_sha256": a1_amendment_canonical_sha256,
        "combined_ai_audit_canonical_sha256": combined_ai_audit_canonical_sha256,
        "question_bank_bundle_sha256": question_bank_bundle_sha256,
        "prompt_bundle_canonical_sha256": prompt_bundle_canonical_sha256,
        "prompt_bundle_declared_status": prompt_bundle_declared_status,
        "prompt_bundle_approval_artifact": dict(prompt_bundle_approval_artifact),
        "per_model_role_limits_artifact": dict(per_model_role_limits_artifact),
        "provider_request_fields_artifact": dict(provider_request_fields_artifact),
        "provider_price_snapshot_canonical_sha256": provider_price_snapshot_canonical_sha256,
        "uv_lock_sha256": uv_lock_sha256,
        "seed_policy": seed_policy,
        "side_assignment_policy": side_assignment_policy,
        "satisfied_prerequisites": {
            name: dict(value) for name, value in satisfied_prerequisites.items()
        },
        "ledger": dict(ledger),
        "planning_cell_inventory": {
            "count": len(ordered_planning_keys),
            "sha256": canonical_sha256(ordered_planning_keys),
        },
        "provider_call_inventory": {
            "count": len(inventory_for_hash),
            "sha256": canonical_sha256(inventory_for_hash),
        },
        "stage_cap_usd": stage_cap_usd,
        "cumulative_cap_usd": cumulative_cap_usd,
        "cost_forecast": dict(cost_forecast),
        "storage_policy": dict(storage_policy),
        "provider_reconciliation_evidence": dict(provider_reconciliation_evidence),
    }


def derive_execution_identity_sha256(identity: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(identity))


def derive_execution_call_key(
    execution_identity_sha256: str, *, planning_cell_key: str, call_role: str, call_index: int,
) -> str:
    """Return a stable, order-stable SHA-256 key binding a call to its full identity.

    Changes if the execution identity, the planning cell, the call role, or the
    zero-based call index changes. This is deliberately the only place a planning
    cell key (non-executable on its own) is combined with an execution identity to
    become an executable call key.
    """
    payload = {
        "execution_identity_sha256": execution_identity_sha256,
        "planning_cell_key": planning_cell_key,
        "call_role": call_role,
        "call_index": call_index,
    }
    return canonical_sha256(payload)


# --- manifest loading (strict JSON) -----------------------------------------------------------


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Shared strict-JSON ``object_pairs_hook``: used for both the execution manifest and
    every artifact this module parses with :func:`_load_strict_json_object`."""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ManifestValidationError(f"duplicate key in JSON: {key!r}")
        seen[key] = value
    return seen


def _reject_non_finite_constant(constant: str) -> float:
    """Shared strict-JSON ``parse_constant`` hook; see :func:`_reject_duplicate_keys`."""
    raise ManifestValidationError(f"JSON contains a non-finite constant: {constant}")


def load_execution_manifest(path: str | Path) -> dict[str, Any]:
    """Strictly parse an execution manifest: an object, no duplicate keys, no NaN/Infinity."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ManifestValidationError(f"could not read execution manifest {path}: {exc}") from exc
    try:
        manifest = json.loads(
            text, object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(
            f"execution manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ManifestValidationError(f"execution manifest root must be an object: {path}")
    return manifest


# --- prompt-bundle owner-methods-approval artifact ----------------------------------------------


def _validate_prompt_bundle_approval(
    manifest: Mapping[str, Any], *, root: Path, protocol: Mapping[str, Any],
    observed_bundle_sha: str,
) -> tuple[str, str]:
    """Load, strictly parse, and fully validate the bound owner-methods-approval artifact.

    This never grants execution authority by itself (the artifact's own
    ``execution_authorized`` field is required to be literally ``false``): it only
    establishes that a specific, append-only, human-approved record binds the exact
    candidate bundle content currently loaded. Every check is independent and fails closed
    with :class:`ManifestValidationError`. Returns ``(tracked_path, observed_sha256)`` for
    the caller to bind into the non-circular execution identity.
    """
    tracked_path = _string(
        manifest.get("prompt_bundle_approval_tracked_path"),
        "prompt_bundle_approval_tracked_path")
    declared_sha = _sha256_hex(
        manifest.get("prompt_bundle_approval_canonical_sha256"),
        "prompt_bundle_approval_canonical_sha256")

    approval_path = _resolve_bound_path(
        root, tracked_path, "prompt_bundle_approval_tracked_path")

    # The owner-methods-approval artifact is not a materialization-pending, bind-from-anywhere
    # artifact like per_model_role_limits/provider_request_fields: per this module's own header
    # guarantee, it lives ONLY at the real, already-committed, git-tracked path. Pin the bound
    # location to that frozen path (as an absolute-path comparison, so a relative path that
    # resolves to the same file under a different spelling still matches) rather than trusting
    # whatever path the manifest names -- otherwise a manifest could bind a self-authored,
    # never-committed copy that happens to satisfy every content check below.
    expected_approval_path = (root / DEFAULT_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH).resolve()
    if approval_path.resolve() != expected_approval_path:
        raise ManifestValidationError(
            "prompt_bundle_approval_tracked_path must resolve to the frozen, git-tracked "
            f"approval artifact at {DEFAULT_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH.as_posix()!r} "
            f"under project_root; got {tracked_path!r}")

    if not approval_path.is_file():
        raise ManifestValidationError(
            f"prompt_bundle_approval_tracked_path artifact is missing: {approval_path}")
    approval = _load_strict_json_object(approval_path)
    _exact_keys(
        approval, PROMPT_BUNDLE_APPROVAL_TOP_LEVEL_KEYS, "prompt bundle approval artifact")

    if approval.get("schema_version") != PROMPT_BUNDLE_APPROVAL_SCHEMA_VERSION:
        raise ManifestValidationError("prompt bundle approval schema_version drifted")
    if approval.get("approval_id") != PROMPT_BUNDLE_APPROVAL_ID:
        raise ManifestValidationError("prompt bundle approval approval_id drifted")

    frozen_protocol_id = _string(protocol.get("protocol_id"), "frozen protocol protocol_id")
    if approval.get("protocol_id") != frozen_protocol_id:
        raise ManifestValidationError(
            "prompt bundle approval protocol_id disagrees with the frozen protocol bound "
            "into this manifest")

    expected_bundle_tracked_path = DEFAULT_PROMPT_BUNDLE_RELATIVE_PATH.as_posix()
    approved_bundle_tracked_path = _string(
        approval.get("approved_bundle_tracked_path"),
        "prompt bundle approval approved_bundle_tracked_path")
    if approved_bundle_tracked_path != expected_bundle_tracked_path:
        raise ManifestValidationError(
            "prompt bundle approval approved_bundle_tracked_path drifted from "
            f"{expected_bundle_tracked_path!r}")
    _resolve_bound_path(
        root, approved_bundle_tracked_path,
        "prompt bundle approval approved_bundle_tracked_path")

    approved_bundle_sha = _sha256_hex(
        approval.get("approved_bundle_canonical_sha256"),
        "prompt bundle approval approved_bundle_canonical_sha256")
    if approved_bundle_sha != observed_bundle_sha:
        raise ManifestValidationError(
            "prompt bundle approval approved_bundle_canonical_sha256 does not match the "
            "bound prompt bundle's recomputed canonical hash: approval bound "
            f"{approved_bundle_sha}, observed {observed_bundle_sha}")

    approved_bundle_commit = approval.get("approved_bundle_commit")
    if (
        not isinstance(approved_bundle_commit, str)
        or not (_APPROVED_BUNDLE_COMMIT_MIN_LEN
                <= len(approved_bundle_commit) <= _APPROVED_BUNDLE_COMMIT_MAX_LEN)
        or any(ch not in _HEX_DIGITS for ch in approved_bundle_commit)
    ):
        raise ManifestValidationError(
            "prompt bundle approval approved_bundle_commit must be lowercase hexadecimal, "
            f"{_APPROVED_BUNDLE_COMMIT_MIN_LEN}-{_APPROVED_BUNDLE_COMMIT_MAX_LEN} characters "
            "(provenance only; the canonical content hash above is authoritative)")

    scope = approval.get("scope")
    if (
        not isinstance(scope, list)
        or not all(isinstance(item, str) for item in scope)
        or tuple(scope) != PROMPT_BUNDLE_APPROVAL_SCOPE
    ):
        raise ManifestValidationError("prompt bundle approval scope drifted")

    if approval.get("approver") != PROMPT_BUNDLE_APPROVAL_APPROVER:
        raise ManifestValidationError("prompt bundle approval approver drifted")

    _parse_utc_timestamp(
        approval.get("approved_at_utc"), "prompt bundle approval approved_at_utc")

    if approval.get("approval_channel") != PROMPT_BUNDLE_APPROVAL_CHANNEL:
        raise ManifestValidationError("prompt bundle approval approval_channel drifted")

    if approval.get("execution_authorized") is not False:
        raise ManifestValidationError(
            "prompt bundle approval execution_authorized must be exactly false: this "
            "record grants methods approval only, never execution authority")

    if approval.get("note") != PROMPT_BUNDLE_APPROVAL_NOTE:
        raise ManifestValidationError("prompt bundle approval note drifted")

    observed_approval_sha = canonical_sha256(approval)
    if observed_approval_sha != declared_sha:
        raise ManifestValidationError(
            "prompt_bundle_approval_canonical_sha256 hash drift: manifest bound "
            f"{declared_sha}, observed {observed_approval_sha}")

    return tracked_path, observed_approval_sha


# --- manifest validation -----------------------------------------------------------------------


def validate_execution_manifest(
    manifest: Mapping[str, Any],
    *,
    project_root: str | Path,
    authorization: Mapping[str, Any] | None = None,
    require_authorized: bool = False,
) -> ValidatedExecutionManifest:
    """Validate an execution manifest against the frozen protocol and every artifact it binds.

    Every bound hash is recomputed from the real artifact under ``project_root`` and
    compared; nothing is trusted from the manifest alone. Only ``stage ==
    "capability_preflight"`` is supported; ``gemma_recovery_or_waiver``, ``canary``, and
    ``main`` raise :class:`UnsupportedStageError` unconditionally.

    The bound prompt bundle's owner-methods-approval artifact (a separate, append-only
    external record; see :func:`_validate_prompt_bundle_approval`) is always fully validated,
    regardless of ``require_authorized``: a manifest whose bundle is still
    ``candidate_pending_owner_methods_review`` -- the bundle file itself never changes status
    under the frozen governance -- can only pass structural validation at all once a valid
    approval artifact binds the bundle's exact observed canonical hash. That approval artifact
    never itself claims execution authority (its own ``execution_authorized`` field is always
    literally ``false``); it only ever establishes that the literal wording was reviewed.

    When ``require_authorized`` is true, a matching :class:`authorization record
    <ExecutionAuthorityError>` (identity hash, stage, both caps, and a resolvable,
    hash-matching ``approval_basis`` must match/resolve exactly) is additionally required.
    Without ``require_authorized``, the manifest can still be inspected/reviewed as long as
    its approval-artifact binding is itself valid.
    """
    root = Path(project_root)
    manifest = _mapping(manifest, "execution manifest")
    _exact_keys(manifest, MANIFEST_TOP_LEVEL_KEYS, "execution manifest")

    protocol_path = root / DEFAULT_PROTOCOL_RELATIVE_PATH
    try:
        protocol = phase2_plan.load_protocol(protocol_path)
    except (OSError, json.JSONDecodeError, phase2_plan.ProtocolValidationError) as exc:
        raise ManifestValidationError(f"bound base protocol is invalid: {exc}") from exc

    materialization = _mapping(protocol["materialization_requirements"], "materialization")
    transition = _mapping(materialization["transition_model"], "transition_model")
    expected_schema_version = transition["manifest_schema_version"]
    if manifest.get("schema_version") != expected_schema_version:
        raise ManifestValidationError(
            f"unsupported execution manifest schema_version: {manifest.get('schema_version')!r}"
            f", expected {expected_schema_version!r}")

    stage_sequence = tuple(str(name) for name in transition["stage_sequence"])
    stage = manifest.get("stage")
    if stage not in stage_sequence:
        raise ManifestValidationError(f"unrecognized execution stage: {stage!r}")
    if stage != STAGE_CAPABILITY_PREFLIGHT:
        raise UnsupportedStageError(
            f"stage {stage!r} is not supported for execution in this version")

    # --- base protocol hash ---
    observed_protocol_sha = canonical_sha256(protocol)
    _check_bound_hash(manifest, "protocol_canonical_sha256", observed_protocol_sha)

    # --- question-bank bundle: declared binding cross-check + independent recompute ---
    try:
        phase2_plan.validate_source_bindings(protocol, root)
    except phase2_plan.ProtocolValidationError as exc:
        raise ManifestValidationError(f"question-bank source bindings are invalid: {exc}") from exc
    source_bindings = _mapping(protocol["source_bindings"], "protocol.source_bindings")
    bound_bundle_sha = _sha256_hex(
        source_bindings.get("question_bank_bundle_sha256"),
        "protocol.source_bindings.question_bank_bundle_sha256",
    )
    if manifest.get("question_bank_bundle_sha256") != bound_bundle_sha:
        raise ManifestValidationError(
            "question_bank_bundle_sha256 disagrees with protocol.source_bindings")

    # --- combined 106-question AI audit + its A1 amendment ---
    combined = _load_json_object(root / DEFAULT_COMBINED_AI_AUDIT_RELATIVE_PATH)
    try:
        ai_review.validate_combined(combined, root=root)
    except ai_review.AIReviewError as exc:
        raise ManifestValidationError(f"bound combined AI audit is invalid: {exc}") from exc
    observed_combined_sha = canonical_sha256(combined)
    _check_bound_hash(manifest, "combined_ai_audit_canonical_sha256", observed_combined_sha)

    amendment = _load_json_object(root / DEFAULT_A1_AMENDMENT_RELATIVE_PATH)
    try:
        ai_review.validate_amendment(amendment, combined_review=combined, root=root)
    except ai_review.AIReviewError as exc:
        raise ManifestValidationError(f"bound A1 amendment is invalid: {exc}") from exc
    observed_amendment_sha = canonical_sha256(amendment)
    _check_bound_hash(manifest, "a1_amendment_canonical_sha256", observed_amendment_sha)

    # --- candidate prompt bundle: hash + declared approval state ---
    try:
        bundle, _bundle_protocol = prompt_bundle.load_and_validate(
            root / DEFAULT_PROMPT_BUNDLE_RELATIVE_PATH, protocol_path)
    except prompt_bundle.PromptBundleError as exc:
        raise ManifestValidationError(f"bound prompt bundle is invalid: {exc}") from exc
    observed_bundle_sha = canonical_sha256(bundle)
    _check_bound_hash(manifest, "prompt_bundle_canonical_sha256", observed_bundle_sha)
    bundle_status = _string(bundle.get("status"), "bound prompt bundle status")
    if manifest.get("prompt_bundle_declared_status") != bundle_status:
        raise ManifestValidationError(
            "prompt_bundle_declared_status disagrees with the bound bundle's own status")

    # --- owner methods-review approval of the (still-candidate) bundle wording, always ---
    # --- fully validated: an append-only external record, never itself an authorization ---
    approval_tracked_path, observed_approval_sha = _validate_prompt_bundle_approval(
        manifest, root=root, protocol=protocol, observed_bundle_sha=observed_bundle_sha)

    # --- per-model/per-role output limits + provider request-field artifacts ---
    per_model_role_limits_artifact = _mapping(
        manifest.get("per_model_role_limits_artifact"), "per_model_role_limits_artifact")
    _bind_json_artifact(root, per_model_role_limits_artifact, "per_model_role_limits_artifact")
    provider_request_fields_artifact = _mapping(
        manifest.get("provider_request_fields_artifact"), "provider_request_fields_artifact")
    _bind_json_artifact(
        root, provider_request_fields_artifact, "provider_request_fields_artifact")

    # --- preflight cost forecast, durable storage policy, provider reconciliation evidence ---
    cost_forecast_binding = _mapping(manifest.get("cost_forecast"), "cost_forecast")
    _bind_json_artifact_checked(root, cost_forecast_binding, "cost_forecast")
    storage_policy_binding = _mapping(manifest.get("storage_policy"), "storage_policy")
    _bind_json_artifact_checked(root, storage_policy_binding, "storage_policy")
    provider_reconciliation_evidence_binding = _mapping(
        manifest.get("provider_reconciliation_evidence"), "provider_reconciliation_evidence")
    _bind_json_artifact_checked(
        root, provider_reconciliation_evidence_binding, "provider_reconciliation_evidence")

    # --- current provider price snapshot ---
    try:
        snapshot, _snapshot_protocol = price_snapshot.load_and_validate(
            root / DEFAULT_PRICE_SNAPSHOT_RELATIVE_PATH, protocol_path)
    except price_snapshot.ProviderSnapshotError as exc:
        raise ManifestValidationError(f"bound provider price snapshot is invalid: {exc}") from exc
    observed_snapshot_sha = canonical_sha256(snapshot)
    _check_bound_hash(
        manifest, "provider_price_snapshot_canonical_sha256", observed_snapshot_sha)

    # --- uv.lock: RAW file hash, never canonical-JSON ---
    observed_uv_lock_sha = _raw_file_sha256(root / DEFAULT_UV_LOCK_RELATIVE_PATH)
    _check_bound_hash(manifest, "uv_lock_sha256", observed_uv_lock_sha)

    # --- exact seed / side policy strings ---
    execution_semantics = _mapping(
        protocol["decisions"]["execution_semantics"], "protocol execution_semantics")
    expected_seed_policy = _string(
        execution_semantics.get("seed_policy"), "protocol execution_semantics.seed_policy")
    expected_side_policy = _string(
        execution_semantics.get("side_assignment_policy"),
        "protocol execution_semantics.side_assignment_policy",
    )
    seed_policy = manifest.get("seed_policy")
    side_assignment_policy = manifest.get("side_assignment_policy")
    if seed_policy != expected_seed_policy:
        raise ManifestValidationError("seed_policy disagrees with the frozen protocol")
    if side_assignment_policy != expected_side_policy:
        raise ManifestValidationError(
            "side_assignment_policy disagrees with the frozen protocol")

    # --- satisfied-prerequisite hashes, derived (not hardcoded) from the stage sequence ---
    stage_index = stage_sequence.index(stage)
    required_prereqs = stage_sequence[:stage_index]
    satisfied = _mapping(manifest.get("satisfied_prerequisites"), "satisfied_prerequisites")
    _exact_keys(satisfied, required_prereqs, "satisfied_prerequisites")
    satisfied_prerequisite_bindings: dict[str, Mapping[str, str]] = {}
    for name in required_prereqs:
        binding = _mapping(satisfied[name], f"satisfied_prerequisites.{name}")
        _bind_json_artifact(root, binding, f"satisfied_prerequisites.{name}")
        satisfied_prerequisite_bindings[name] = {
            "path": str(binding["path"]), "sha256": str(binding["sha256"]),
        }

    # --- fixed project-wide ledger path + identity string (binding only; no filesystem check) ---
    ledger = _mapping(manifest.get("ledger"), "ledger")
    _exact_keys(ledger, LEDGER_KEYS, "ledger")
    ledger_path = _string(ledger.get("path"), "ledger.path")
    ledger_identity = _string(ledger.get("ledger_identity"), "ledger.ledger_identity")

    # --- exact planning-cell inventory: derive from the frozen protocol via phase2_plan ---
    main_question_ids = phase2_plan.load_main_question_ids(protocol, root)
    all_cells = phase2_plan.enumerate_cells(protocol, main_question_ids)
    cells_by_key = {
        str(cell["cell_key"]): cell for cell in all_cells if cell["kind"] == "capability_qa"
    }
    if len(cells_by_key) != EXPECTED_CAPABILITY_CELL_COUNT:
        raise ManifestValidationError(
            "the frozen protocol no longer produces exactly "
            f"{EXPECTED_CAPABILITY_CELL_COUNT} capability_qa planning cells")
    expected_planning_keys = sorted(cells_by_key)

    manifest_planning_keys = manifest.get("planning_cell_keys")
    if not isinstance(manifest_planning_keys, list) or not all(
            isinstance(key, str) and key for key in manifest_planning_keys):
        raise ManifestValidationError("planning_cell_keys must be a list of non-empty strings")
    if len(manifest_planning_keys) != len(set(manifest_planning_keys)):
        raise ManifestValidationError("planning_cell_keys contains duplicate cell keys")
    if len(manifest_planning_keys) != EXPECTED_CAPABILITY_CELL_COUNT:
        raise ManifestValidationError(
            f"planning cell inventory must contain exactly {EXPECTED_CAPABILITY_CELL_COUNT} "
            f"cells, found {len(manifest_planning_keys)}")
    if sorted(manifest_planning_keys) != expected_planning_keys:
        raise ManifestValidationError(
            "planning_cell_keys does not match the frozen capability_qa cell inventory")
    ordered_planning_keys = sorted(str(key) for key in manifest_planning_keys)

    # --- provider-call inventory: structure, uniqueness, and cross-checks against cells ---
    raw_entries = manifest.get("provider_call_inventory")
    if not isinstance(raw_entries, list):
        raise ManifestValidationError("provider_call_inventory must be a list")
    if len(raw_entries) != EXPECTED_CAPABILITY_CELL_COUNT:
        raise ManifestValidationError(
            f"provider_call_inventory must contain exactly {EXPECTED_CAPABILITY_CELL_COUNT} "
            f"calls, found {len(raw_entries)}")

    seen_planning_keys: set[str] = set()
    seen_call_keys: set[str] = set()
    normalized_entries: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(raw_entries):
        label = f"provider_call_inventory[{index}]"
        entry = _mapping(raw_entry, label)
        _exact_keys(entry, EXPECTED_CALL_ENTRY_KEYS, label)

        if entry.get("call_role") != CAPABILITY_CALL_ROLE:
            raise ManifestValidationError(f"{label}.call_role must be {CAPABILITY_CALL_ROLE!r}")
        call_index = entry.get("call_index")
        if type(call_index) is not int or call_index != index:
            raise ManifestValidationError(f"{label}.call_index must be {index}")

        planning_cell_key = entry.get("planning_cell_key")
        if not isinstance(planning_cell_key, str) or planning_cell_key not in cells_by_key:
            raise ManifestValidationError(
                f"{label} does not reference a known capability_qa planning cell")
        if planning_cell_key in seen_planning_keys:
            raise ManifestValidationError(
                f"duplicate planning cell in provider_call_inventory: {planning_cell_key!r}")
        seen_planning_keys.add(planning_cell_key)
        cell = cells_by_key[planning_cell_key]

        if entry.get("model") != cell["judge_model"]:
            raise ManifestValidationError(f"{label}.model disagrees with its planning cell")
        seed = entry.get("seed")
        if type(seed) is not int or seed < 0:
            raise ManifestValidationError(f"{label}.seed must be a non-negative integer")
        expected_side = "A" if cell["replicate_index"] == 0 else "B"
        if entry.get("side") != expected_side:
            raise ManifestValidationError(
                f"{label}.side disagrees with its K2-mirrored replicate")
        _sha256_hex(entry.get("request_fields_sha256"), f"{label}.request_fields_sha256")
        execution_call_key = _sha256_hex(
            entry.get("execution_call_key"), f"{label}.execution_call_key")
        if execution_call_key in seen_call_keys:
            raise ManifestValidationError(
                f"duplicate execution_call_key in provider_call_inventory: "
                f"{execution_call_key!r}")
        seen_call_keys.add(execution_call_key)
        normalized_entries.append(dict(entry))

    # --- immutable stage cap and cumulative cap ---
    stage_cap_usd = _finite_positive_number(manifest.get("stage_cap_usd"), "stage_cap_usd")
    cumulative_cap_usd = _finite_positive_number(
        manifest.get("cumulative_cap_usd"), "cumulative_cap_usd")
    capability_preflight = _mapping(
        materialization["capability_preflight"], "materialization.capability_preflight")
    proposed_cap_usd = capability_preflight.get("proposed_cap_usd")
    if not isinstance(proposed_cap_usd, (int, float)) or isinstance(proposed_cap_usd, bool):
        raise ManifestValidationError(
            "materialization.capability_preflight.proposed_cap_usd must be a number")
    if stage_cap_usd > float(proposed_cap_usd):
        raise ManifestValidationError(
            f"stage_cap_usd {stage_cap_usd} exceeds the protocol's approved capability "
            f"preflight ceiling of {proposed_cap_usd}")
    if cumulative_cap_usd < stage_cap_usd:
        raise ManifestValidationError("cumulative_cap_usd must be at least stage_cap_usd")

    # --- assemble + hash the non-circular execution identity ---
    identity = build_execution_identity(
        schema_version=str(manifest["schema_version"]),
        stage=stage,
        protocol_canonical_sha256=observed_protocol_sha,
        a1_amendment_canonical_sha256=observed_amendment_sha,
        combined_ai_audit_canonical_sha256=observed_combined_sha,
        question_bank_bundle_sha256=bound_bundle_sha,
        prompt_bundle_canonical_sha256=observed_bundle_sha,
        prompt_bundle_declared_status=bundle_status,
        prompt_bundle_approval_artifact={
            "tracked_path": str(approval_tracked_path),
            "sha256": str(observed_approval_sha),
        },
        per_model_role_limits_artifact={
            "path": str(per_model_role_limits_artifact["path"]),
            "sha256": str(per_model_role_limits_artifact["sha256"]),
        },
        provider_request_fields_artifact={
            "path": str(provider_request_fields_artifact["path"]),
            "sha256": str(provider_request_fields_artifact["sha256"]),
        },
        provider_price_snapshot_canonical_sha256=observed_snapshot_sha,
        uv_lock_sha256=observed_uv_lock_sha,
        seed_policy=str(seed_policy),
        side_assignment_policy=str(side_assignment_policy),
        satisfied_prerequisites=satisfied_prerequisite_bindings,
        ledger={"path": ledger_path, "ledger_identity": ledger_identity},
        planning_cell_keys=ordered_planning_keys,
        provider_call_inventory_entries=normalized_entries,
        stage_cap_usd=stage_cap_usd,
        cumulative_cap_usd=cumulative_cap_usd,
        cost_forecast={
            "path": str(cost_forecast_binding["path"]),
            "sha256": str(cost_forecast_binding["sha256"]),
        },
        storage_policy={
            "path": str(storage_policy_binding["path"]),
            "sha256": str(storage_policy_binding["sha256"]),
        },
        provider_reconciliation_evidence={
            "path": str(provider_reconciliation_evidence_binding["path"]),
            "sha256": str(provider_reconciliation_evidence_binding["sha256"]),
        },
    )
    execution_identity_sha256 = derive_execution_identity_sha256(identity)

    finalized_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(normalized_entries):
        expected_call_key = derive_execution_call_key(
            execution_identity_sha256,
            planning_cell_key=str(entry["planning_cell_key"]),
            call_role=str(entry["call_role"]),
            call_index=int(entry["call_index"]),
        )
        if entry["execution_call_key"] != expected_call_key:
            raise ManifestValidationError(
                f"provider_call_inventory[{index}].execution_call_key does not match its "
                "derived value; the manifest is not internally consistent with its own "
                "execution identity")
        finalized_entries.append(entry)

    authorized = False
    validated_authorization: dict[str, Any] | None = None
    if require_authorized:
        if authorization is None:
            raise ExecutionAuthorityError(
                "execution requires a matching authorization record; none was provided")
        authorization_mapping = _mapping(
            authorization, "authorization record", ExecutionAuthorityError)
        _exact_keys(
            authorization_mapping, AUTHORIZATION_KEYS, "authorization record",
            ExecutionAuthorityError)
        auth_identity = _sha256_hex(
            authorization_mapping.get("execution_identity_sha256"),
            "authorization.execution_identity_sha256", ExecutionAuthorityError,
        )
        auth_stage = _string(
            authorization_mapping.get("stage"), "authorization.stage", ExecutionAuthorityError)
        auth_stage_cap = _finite_positive_number(
            authorization_mapping.get("stage_cap_usd"), "authorization.stage_cap_usd",
            ExecutionAuthorityError,
        )
        auth_cumulative_cap = _finite_positive_number(
            authorization_mapping.get("cumulative_cap_usd"),
            "authorization.cumulative_cap_usd", ExecutionAuthorityError,
        )
        _string(
            authorization_mapping.get("approver"), "authorization.approver",
            ExecutionAuthorityError,
        )
        _parse_utc_timestamp(
            authorization_mapping.get("approved_at_utc"), "authorization.approved_at_utc",
            ExecutionAuthorityError,
        )
        approval_basis_tracked_path = _string(
            authorization_mapping.get("approval_basis_tracked_path"),
            "authorization.approval_basis_tracked_path", ExecutionAuthorityError,
        )
        approval_basis_sha256 = _sha256_hex(
            authorization_mapping.get("approval_basis_sha256"),
            "authorization.approval_basis_sha256", ExecutionAuthorityError,
        )

        if auth_identity != execution_identity_sha256:
            raise ExecutionAuthorityError(
                "authorization.execution_identity_sha256 does not match this manifest's "
                "derived execution identity")
        if auth_stage != stage:
            raise ExecutionAuthorityError("authorization.stage does not match this manifest")
        if auth_stage_cap != stage_cap_usd or auth_cumulative_cap != cumulative_cap_usd:
            raise ExecutionAuthorityError(
                "authorization caps do not match this manifest's caps")

        # --- approval basis: a resolvable, hash-matching (RAW-file) record distinguishing ---
        # --- the owner's conditional policy approval from review of a final execution ---
        # --- identity. Deliberately RAW file hashing (the basis may be markdown), never ---
        # --- canonical-JSON: keep this contract distinct from every canonical-JSON binding. ---
        approval_basis_path = _resolve_bound_path(
            root, approval_basis_tracked_path, "authorization.approval_basis_tracked_path",
            ExecutionAuthorityError,
        )
        if not approval_basis_path.is_file():
            raise ExecutionAuthorityError(
                "authorization.approval_basis_tracked_path artifact is missing: "
                f"{approval_basis_path}")
        observed_approval_basis_sha256 = _raw_file_sha256(
            approval_basis_path, ExecutionAuthorityError)
        if observed_approval_basis_sha256 != approval_basis_sha256:
            raise ExecutionAuthorityError(
                "authorization.approval_basis_sha256 hash drift: authorization bound "
                f"{approval_basis_sha256}, observed {observed_approval_basis_sha256}")

        authorized = True
        validated_authorization = dict(authorization_mapping)

    return ValidatedExecutionManifest(
        stage=stage,
        protocol_id=str(protocol["protocol_id"]),
        execution_identity=identity,
        execution_identity_sha256=execution_identity_sha256,
        stage_cap_usd=stage_cap_usd,
        cumulative_cap_usd=cumulative_cap_usd,
        planning_cell_keys=tuple(ordered_planning_keys),
        provider_call_inventory=tuple(finalized_entries),
        authorized=authorized,
        authorization=validated_authorization,
    )


def _check_bound_hash(manifest: Mapping[str, Any], key: str, observed: str) -> None:
    bound = _sha256_hex(manifest.get(key), key)
    if bound != observed:
        raise ManifestValidationError(
            f"{key} hash drift: manifest bound {bound}, observed {observed}")


# --- resume audit ------------------------------------------------------------------------------


def audit_resume(
    validated_manifest: ValidatedExecutionManifest,
    *,
    output_rows: Iterable[Any],
    usage_events: Iterable[Any],
) -> ResumeAudit:
    """Audit a resume: classify every manifested call as TODO, COMPLETE, or blocked.

    ``output_rows`` are this stage's own durable JSONL result rows; every row is presumed
    to belong to this manifest, and any row that does not cleanly map to exactly one known
    call key is a blocker. ``usage_events`` come from the project-wide usage ledger (shared
    across run kinds and stages), so only events whose ``metadata`` carries an
    ``execution_call_key`` are treated as being in scope for this audit; events without that
    key are other traffic on the shared ledger and are silently ignored. A charge (success,
    charged-malformed, or unknown-charge) is never replayed and never quietly marked done: a
    charge without a matching durable output halts the call rather than resuming it.
    """
    if isinstance(output_rows, (str, bytes)) or not isinstance(output_rows, Iterable):
        raise ResumeAuditError("output_rows must be an iterable of row objects")
    if isinstance(usage_events, (str, bytes)) or not isinstance(usage_events, Iterable):
        raise ResumeAuditError("usage_events must be an iterable of event objects")

    inventory = {
        str(entry["execution_call_key"]): entry
        for entry in validated_manifest.provider_call_inventory
    }
    per_call: dict[str, ResumeDisposition] = {key: ResumeDisposition.TODO for key in inventory}
    blockers: list[str] = []

    output_call_keys: set[str] = set()
    for index, row in enumerate(output_rows):
        if not isinstance(row, Mapping):
            blockers.append(f"output row {index} is not an object")
            continue
        call_key = row.get("execution_call_key")
        if not isinstance(call_key, str) or not call_key:
            blockers.append(f"output row {index} is missing execution_call_key")
            continue
        if call_key not in inventory:
            blockers.append(f"output row {index} references unknown call key {call_key!r}")
            continue
        if call_key in output_call_keys:
            blockers.append(f"duplicate output row for call key {call_key!r}")
            per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION
            continue
        output_call_keys.add(call_key)

    reservations: dict[str, Mapping[str, Any]] = {}
    terminals: dict[str, Mapping[str, Any]] = {}
    attempt_call_key: dict[str, str] = {}

    for index, event in enumerate(usage_events):
        if not isinstance(event, Mapping):
            blockers.append(f"usage event {index} is not an object")
            continue
        metadata = event.get("metadata")
        if not isinstance(metadata, Mapping) or "execution_call_key" not in metadata:
            continue  # out of scope: not tagged as belonging to this execution manifest
        call_key = metadata.get("execution_call_key")
        attempt_id = event.get("attempt_id")
        status = event.get("status")
        if not isinstance(call_key, str) or not call_key:
            blockers.append(f"usage event {index} has a malformed execution_call_key")
            continue
        if not isinstance(attempt_id, str) or not attempt_id:
            blockers.append(
                f"usage event {index} for call key {call_key!r} is missing attempt_id")
            if call_key in per_call:
                per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION
            continue

        if status == "reserved":
            if attempt_id in reservations:
                blockers.append(f"duplicate reservation for attempt {attempt_id!r}")
                continue
            reservations[attempt_id] = event
            attempt_call_key[attempt_id] = call_key
            continue

        if status not in _TERMINAL_LEDGER_STATUSES:
            blockers.append(
                f"usage event {index} for call key {call_key!r} has an unknown status: "
                f"{status!r}")
            if call_key in per_call:
                per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION
            continue
        if attempt_id not in reservations:
            blockers.append(
                f"terminal event for call key {call_key!r} (attempt {attempt_id!r}) has no "
                "matching reservation")
            if call_key in per_call:
                per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION
            continue
        if attempt_id in terminals:
            blockers.append(f"duplicate terminal event for attempt {attempt_id!r}")
            if call_key in per_call:
                per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION
            continue
        if attempt_call_key[attempt_id] != call_key:
            blockers.append(
                f"terminal event for attempt {attempt_id!r} disagrees with its reservation's "
                "call key")
            for stale_key in (attempt_call_key[attempt_id], call_key):
                if stale_key in per_call:
                    per_call[stale_key] = ResumeDisposition.BLOCKED_RECONCILIATION
            continue
        terminals[attempt_id] = event

    for attempt_id, reservation in reservations.items():
        if attempt_id in terminals:
            continue
        call_key = attempt_call_key[attempt_id]
        blockers.append(
            f"unmatched reservation for call key {call_key!r} (attempt {attempt_id!r})")
        if call_key in per_call:
            per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION

    successes_by_call: dict[str, list[str]] = {}
    for attempt_id, terminal in terminals.items():
        call_key = attempt_call_key[attempt_id]
        reservation = reservations[attempt_id]
        if call_key not in inventory:
            blockers.append(
                f"ledger event references unknown call key {call_key!r} "
                f"(attempt {attempt_id!r})")
            continue
        entry = inventory[call_key]
        reservation_metadata = reservation.get("metadata") or {}
        terminal_metadata = terminal.get("metadata") or {}
        identity_matches = (
            reservation.get("model") == entry.get("model")
            and terminal.get("model") == entry.get("model")
            and reservation.get("seed") == entry.get("seed")
            and terminal.get("seed") == entry.get("seed")
            and reservation_metadata.get("request_fields_sha256")
            == entry.get("request_fields_sha256")
            and terminal_metadata.get("request_fields_sha256")
            == entry.get("request_fields_sha256")
        )
        if not identity_matches:
            blockers.append(
                "ledger request identity mismatches the manifest inventory for call key "
                f"{call_key!r}")
            per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION
            continue

        status = terminal.get("status")
        if status in ("charged_malformed", "unknown_charge"):
            blockers.append(f"{status} recorded for call key {call_key!r}")
            per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION
        elif status == "released_no_charge":
            continue  # retryable; stays TODO unless a later attempt on the same call succeeds
        elif status == "success":
            successes_by_call.setdefault(call_key, []).append(attempt_id)

    for call_key, attempt_ids in successes_by_call.items():
        if per_call.get(call_key) is ResumeDisposition.BLOCKED_RECONCILIATION:
            continue
        if len(attempt_ids) > 1:
            blockers.append(f"multiple successful charges recorded for call key {call_key!r}")
            per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION
            continue
        if call_key in output_call_keys:
            per_call[call_key] = ResumeDisposition.COMPLETE
        else:
            blockers.append(
                f"call key {call_key!r} was charged successfully but has no durable output row")
            per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION

    for call_key in output_call_keys:
        if call_key not in successes_by_call:
            blockers.append(
                f"output row for call key {call_key!r} has no successful ledger lifecycle")
            per_call[call_key] = ResumeDisposition.BLOCKED_RECONCILIATION

    todo_call_keys = tuple(sorted(
        key for key, disposition in per_call.items() if disposition is ResumeDisposition.TODO))
    counts = {
        "total": len(inventory),
        "todo": sum(1 for d in per_call.values() if d is ResumeDisposition.TODO),
        "complete": sum(1 for d in per_call.values() if d is ResumeDisposition.COMPLETE),
        "blocked_reconciliation": sum(
            1 for d in per_call.values() if d is ResumeDisposition.BLOCKED_RECONCILIATION),
    }

    if blockers:
        overall = ResumeDisposition.BLOCKED_RECONCILIATION
    elif counts["complete"] == counts["total"] and counts["total"] > 0:
        overall = ResumeDisposition.COMPLETE
    else:
        overall = ResumeDisposition.TODO

    return ResumeAudit(
        stage=validated_manifest.stage,
        disposition=overall,
        per_call=MappingProxyType(dict(per_call)),
        todo_call_keys=todo_call_keys,
        blockers=tuple(blockers),
        counts=MappingProxyType(counts),
    )
