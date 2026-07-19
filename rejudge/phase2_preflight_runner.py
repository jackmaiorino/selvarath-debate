"""Executable Phase-2 ``capability_preflight`` runner: a thin orchestrator over
``rejudge.phase2_execution`` (manifest validation, call-key derivation, resume audits) and
``rejudge.phase2_role_limits`` (the only source of per-call temperature/max_tokens).

This module is the ONLY place in the codebase that actually spends real Together-API dollars
for the ``capability_preflight`` stage, and every path is fail-closed:

* Nothing runs without a validated manifest (:func:`rejudge.phase2_execution.validate_execution_manifest`).
  A live run additionally requires a matching, resolvable authorization record. This is enforced
  twice: once by ``run_preflight`` (``require_authorized=not dry_run`` passed into
  ``validate_execution_manifest``) and again, independently, by ``_run_locked`` itself -- the
  function that actually dispatches provider calls -- by asserting
  ``validated.authorized`` before doing anything else.
* A dry run may use an unauthorized manifest, but every artifact it writes is durably tagged
  ``dry_run: true`` and lands under a separate, ``.dry_run``-suffixed sibling path so it can
  never be mistaken for -- or contaminate -- live state.
* Exactly one process may run a preflight (dry or live) at a time: :func:`run_preflight`
  acquires the exclusive OS-backed lock ``rejudge.run_manifest.output_lock`` provides, on a
  fixed path derived from the manifest-bound project-wide ledger path, before doing anything
  else that touches the filesystem or a provider.
* Prompts are rendered EXCLUSIVELY via :mod:`rejudge.phase2_capability_corpus`; this module
  contains no inline template text. The freshly rendered corpus's canonical hash is asserted
  against the manifest-bound cost-forecast artifact's ``bindings.rendered_corpus`` binding
  before any provider call.
* Temperature and max_tokens are resolved EXCLUSIVELY via
  ``rejudge.phase2_role_limits.resolve_request_parameters``.
* A verdict is parsed with a single, strict rule (exactly ``"ANSWER: A"`` or ``"ANSWER: B"``,
  leading/trailing whitespace tolerated, nothing else); anything else is recorded as the
  literal string ``"INVALID"`` alongside the raw text. A semantic parse failure is NEVER
  regenerated or retried -- only the client's own internal *transport* retries (inside
  ``rejudge.api_client``) ever re-issue a request.
* Every result row is fsynced to disk (via ``rejudge.runner.append_jsonl_record``) before the
  next call is attempted ("persist-before-advance"). A crash can therefore only ever lose the
  one in-flight call, never a completed one.
* On startup, ``rejudge.phase2_execution.audit_resume`` classifies every manifested call as
  TODO / COMPLETE / BLOCKED_RECONCILIATION from durable state alone (result rows plus the
  usage-events log); a BLOCKED_RECONCILIATION disposition refuses the run outright, and a
  COMPLETE cell is never re-run. The SAME audit is now also re-run immediately after every
  single call's persist step (not deferred to finalization): a blocker condition that only
  becomes visible once the just-recorded row/ledger events are on disk (an unknown charge, a
  charged-malformed response, or a duplicate) halts the run before any further call is
  dispatched.
* A completion gate re-derives that audit from disk one more time after the loop and refuses
  to call the run done unless it is exact: 1,060 rows, no duplicates, no unknown keys, and (for
  a live run) a reconciled ledger success-event count.
* The manifest-bound storage-policy artifact is validated against its REAL schema (see
  ``rejudge.phase2_execution.STORAGE_POLICY_TOP_LEVEL_KEYS``; its archive destination lives at
  ``versioned_destination``, never an invented ``archive_destination`` key) and the resolved
  destination's writability is PROVEN -- a run-scoped probe file is created, fsynced, and
  deleted -- before the provider client is ever constructed. A failed probe refuses the entire
  run with zero calls dispatched.
* Every exception path (a cost-cap halt, a client-raised
  ``rejudge.api_client.UnknownChargeHalt``, a resume/per-call blocker, a corpus or inventory
  refusal, or any other unexpected exception) writes a distinct, timestamped abort record and
  archives whatever partial state exists (outputs so far, the usage ledger, the manifest, and a
  SHA256SUMS) to an ``-aborted``-suffixed sibling of the normal archive destination, before
  re-raising the original exception unchanged. The abort record's own timestamp is never folded
  into any canonical identity hash -- it is a runtime-only diagnostic, not a frozen artifact.
* A finalize step archives outputs + manifest + usage-events log + a ``SHA256SUMS`` file to the
  destination named by the manifest-bound storage-policy artifact. Archival is mandatory for
  both dry and live runs (a stricter reading than the letter of the design note, which only
  says "live runs refuse to finish without a successful archive" -- see the module docstring's
  final paragraph and the accompanying report for why this was tightened rather than narrowed).

This module intentionally never imports a provider SDK and never constructs one: the caller
supplies ``client_factory``, a callable that receives a :class:`ClientConstructionParams` (every
strict-mode setting a production factory needs to build a properly configured
``rejudge.api_client.RejudgeClient``) and returns an object satisfying the small
:class:`PreflightClient` protocol below. Tests -- and the built-in ``--dry-run`` CLI -- pass a
factory that never touches a real SDK at all. :func:`build_production_client_factory` is the one
REAL factory this module ships: it builds a fully strict ``rejudge.api_client.RejudgeClient``, but
only ever imports the real ``together`` SDK lazily, inside a call to the factory IT returns (never
merely by calling ``build_production_client_factory()`` itself, and never at this module's import
time) -- see its own docstring. :func:`run_live` is the one library entry point that can spend
real money: it REQUIRES a resolvable authorization record with no bypass, unlike ``main()``'s
``--dry-run``-only CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from rejudge import api_client
from rejudge import phase2_capability_corpus as capability_corpus
from rejudge import phase2_execution as pe
from rejudge import phase2_plan
from rejudge import phase2_prompt_bundle as prompt_bundle
from rejudge import phase2_provider_price_snapshot as price_snapshot
from rejudge import phase2_role_limits as role_limits
from rejudge import run_manifest
from rejudge.runner import OutputPersistenceError as RunnerOutputPersistenceError
from rejudge.runner import append_jsonl_record, prepare_jsonl_output


COMPLETION_SCHEMA_VERSION = "phase2_capability_preflight_completion_v1"
ABORT_SCHEMA_VERSION = "phase2_capability_preflight_abort_v1"
_ANSWER_A = "ANSWER: A"
_ANSWER_B = "ANSWER: B"
_VALID_VERDICT_TEXT: dict[str, str] = {_ANSWER_A: "A", _ANSWER_B: "B"}
INVALID_VERDICT = "INVALID"


# --- error hierarchy -----------------------------------------------------------------------------


class PreflightRunnerError(Exception):
    """Base class for every fail-closed capability-preflight runner failure."""


class ManifestRejectedError(PreflightRunnerError):
    """The manifest, or its authorization, did not validate."""


class LockHeldError(PreflightRunnerError):
    """Another process already holds the project-wide capability-preflight lock."""


class InventoryMismatchError(PreflightRunnerError):
    """The independently recomputed call inventory disagrees with the validated manifest."""


class CorpusMismatchError(PreflightRunnerError):
    """The freshly rendered capability_qa corpus disagrees with the manifest-bound forecast."""


class OutputPersistenceError(PreflightRunnerError):
    """A result row or completion record could not be durably persisted."""


class ResumeBlockedError(PreflightRunnerError):
    """The resume audit found a call that cannot be safely resumed.

    Raised both by the startup resume audit (before any call this invocation makes) and by the
    per-call blocker check that runs immediately after every single call's persist step (see the
    module docstring): a blocker condition -- an unknown charge, a charged-malformed response, or
    a duplicate -- halts the run the moment it becomes visible in durable state, never deferred
    to the end-of-loop completion gate.
    """


class CompletionGateError(PreflightRunnerError):
    """The post-loop completion audit is not exact."""


class StoragePolicyError(PreflightRunnerError):
    """The manifest-bound storage-policy artifact is missing or malformed."""


class ArchiveError(PreflightRunnerError):
    """Outputs could not be archived to the manifest-bound destination.

    Also raised by the pre-flight archive-writability probe (see
    :func:`_probe_archive_writability`): a destination that fails the probe refuses the entire
    run with zero calls dispatched, exactly like a real end-of-run archive failure.
    """


# --- the client seam: no SDK, no network, defined entirely by this module ------------------------


@dataclass(frozen=True, slots=True)
class ClientConstructionParams:
    """Every strict-mode setting a production ``client_factory`` needs.

    A production factory is expected to build this run's ``rejudge.api_client.RejudgeClient``
    from exactly these fields: ``require_explicit_reasoning_max_tokens=True``,
    ``strict_context_mode=True`` with ``model_context_limits``, ``max_retries`` (sourced from the
    manifest-bound role-limits-and-request-settings artifact's own
    ``request_settings.transport.max_retries`` -- 2 under the frozen v3 artifact, not the older
    v2 pin of 3), ``streaming_pinned_models``/``extra_request_fields`` copied verbatim from that
    same artifact, ``halt_on_unknown_charge=True``, and the manifest's OWN stage cap (never the
    cumulative cap) as the client's ``approved_cap_usd``. ``usage_log_path`` is the single
    durable events log the client must append every reservation/terminal lifecycle event to
    (fsynced before each ``complete()`` call returns) -- this module's resume and completion
    audits read only that file, never any in-process client state, so a resumed process with a
    brand-new client object still sees every prior call correctly. See
    :func:`build_production_client_factory` for the one REAL factory this module ships.
    """

    dry_run: bool
    approved_cap_usd: float
    require_explicit_reasoning_max_tokens: bool
    strict_context_mode: bool
    model_context_limits: Mapping[str, int]
    max_retries: int
    streaming_pinned_models: frozenset[str]
    extra_request_fields: Mapping[str, Mapping[str, Any]]
    model_prices: Mapping[str, Mapping[str, float]]
    usage_log_path: Path
    error_log_path: Path
    ledger_identity: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class PreflightCallResult:
    """What a :class:`PreflightClient` returns for one completed call."""

    raw_output: str
    response_metadata: Mapping[str, Any]


class PreflightClient(Protocol):
    """The only interface this module ever calls on a client."""

    def complete(
        self, *, messages: Sequence[Mapping[str, str]], model: str, temperature: float,
        seed: int, max_tokens: int, request_metadata: Mapping[str, Any],
    ) -> PreflightCallResult: ...


ClientFactory = Callable[[ClientConstructionParams], PreflightClient]


class DeterministicDryRunClient:
    """Offline, deterministic dry-run client: every call answers exactly ``"ANSWER: A"``.

    Durably appends ``{reserved, success}`` usage-lifecycle events to ``usage_log_path``
    (flushed and fsynced before ``complete()`` returns) in exactly the shape
    :func:`rejudge.phase2_execution.audit_resume` expects, so a dry run built on this client can
    be interrupted and resumed exactly like a live run. Never imports or constructs a provider
    SDK. This is the client the built-in ``--dry-run`` CLI uses; tests may use it directly or
    supply their own stub.
    """

    def __init__(self, usage_log_path: str | Path) -> None:
        self._usage_log_path = Path(usage_log_path)
        self._usage_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._attempt_counter = 0

    def _append_event(self, event: dict[str, Any]) -> None:
        payload = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        with self._usage_log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def complete(
        self, *, messages: Sequence[Mapping[str, str]], model: str, temperature: float,
        seed: int, max_tokens: int, request_metadata: Mapping[str, Any],
    ) -> PreflightCallResult:
        self._attempt_counter += 1
        attempt_id = f"dryrun-{self._attempt_counter:06d}-{uuid.uuid4().hex}"
        metadata = dict(request_metadata)
        self._append_event({
            "status": "reserved", "attempt_id": attempt_id, "model": model, "seed": seed,
            "metadata": metadata,
        })
        response_metadata = {
            "request_fields_sha256": metadata.get("request_fields_sha256"),
            "returned_model_id": model,
            "response_id": attempt_id,
            "finish_reason": "stop",
            "system_fingerprint_if_present": None,
            "prompt_tokens": 0,
            "completion_tokens": len(_ANSWER_A.split()),
            "reasoning_tokens_if_returned": None,
        }
        self._append_event({
            "status": "success", "attempt_id": attempt_id, "model": model, "seed": seed,
            "metadata": metadata, "response_metadata": response_metadata,
        })
        return PreflightCallResult(raw_output=_ANSWER_A, response_metadata=response_metadata)


# --- strict verdict parsing (contract item 8) -----------------------------------------------------


def parse_capability_verdict(raw_text: Any) -> str:
    """Return ``"A"``, ``"B"``, or :data:`INVALID_VERDICT`.

    Exactly ``"ANSWER: A"`` or ``"ANSWER: B"`` after stripping leading/trailing whitespace and
    nothing else; any other text (including a correct answer buried among extra lines, extra
    words, or an unstripped internal newline) is ``INVALID``. A semantic parse failure is never
    regenerated or retried by this module.
    """
    if not isinstance(raw_text, str):
        return INVALID_VERDICT
    return _VALID_VERDICT_TEXT.get(raw_text.strip(), INVALID_VERDICT)


# --- small path/JSON helpers ------------------------------------------------------------------


def _resolve_manifest_path(project_root: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    return candidate if candidate.is_absolute() else (project_root / candidate)


def _load_json(path: Path, error_cls: type[PreflightRunnerError]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_cls(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise error_cls(f"{path} must contain a JSON object")
    return payload


def _load_authorization(path: Path) -> dict[str, Any]:
    return _load_json(path, ManifestRejectedError)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    """Best-effort JSONL reader for resume auditing: a malformed line becomes a keyless row.

    A keyless row (``{"__malformed_line__": N}``) can never match a known
    ``execution_call_key``, so :func:`rejudge.phase2_execution.audit_resume` always reports it
    as a blocker (`"output row N is missing execution_call_key"`) rather than this reader
    silently discarding it -- a crash-truncated tail must halt a resume, never be skipped.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"__malformed_line__": line_number})
            continue
        if not isinstance(payload, dict):
            rows.append({"__malformed_line__": line_number})
            continue
        rows.append(payload)
    return rows


def _read_usage_events_for_audit(path: Path) -> list[dict[str, Any]]:
    """Read a usage-events JSONL log, dropping a leading chained-ledger genesis marker if any."""
    events = _read_jsonl_rows(path)
    if events and events[0].get("status") == "ledger_genesis":
        events = events[1:]
    return events


def _count_completed_cells(results_path: Path) -> int:
    """Count durable result rows on disk, for the abort record's ``cells_completed`` field.

    ``is_file()`` (not merely ``exists()``): a path OCCUPIED by a directory -- the exact
    scenario :func:`_run_locked`'s own ``prepare_jsonl_output`` guard exists to catch -- must
    never be read as a JSONL file here.
    """
    if not results_path.is_file():
        return 0
    return sum(1 for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip())


# --- derived, manifest-bound paths --------------------------------------------------------------


def _ledger_path(project_root: Path, validated: pe.ValidatedExecutionManifest) -> Path:
    return _resolve_manifest_path(project_root, str(validated.execution_identity["ledger"]["path"]))


def _usage_log_path(project_root: Path, validated: pe.ValidatedExecutionManifest, *,
                     dry_run: bool) -> Path:
    ledger_path = _ledger_path(project_root, validated)
    if dry_run:
        # Never the manifest-bound path itself: a dry run must not be able to pollute the real
        # project-wide ledger a later live run resumes against.
        return ledger_path.with_name(f"{ledger_path.name}.dry_run.jsonl")
    return ledger_path


def _sibling_path(project_root: Path, validated: pe.ValidatedExecutionManifest, *,
                   dry_run: bool, filename: str) -> Path:
    ledger_path = _ledger_path(project_root, validated)
    suffix = ".dry_run" if dry_run else ""
    stem, dot, ext = filename.partition(".")
    return ledger_path.parent / f"{stem}{suffix}{dot}{ext}"


def _results_path(project_root: Path, validated: pe.ValidatedExecutionManifest, *,
                   dry_run: bool) -> Path:
    return _sibling_path(
        project_root, validated, dry_run=dry_run,
        filename="phase2_capability_preflight_results.jsonl")


def _completion_path(project_root: Path, validated: pe.ValidatedExecutionManifest, *,
                      dry_run: bool) -> Path:
    return _sibling_path(
        project_root, validated, dry_run=dry_run,
        filename="phase2_capability_preflight_completion.json")


def _error_log_path(project_root: Path, validated: pe.ValidatedExecutionManifest, *,
                     dry_run: bool) -> Path:
    return _sibling_path(
        project_root, validated, dry_run=dry_run,
        filename="phase2_capability_preflight_errors.jsonl")


def _abort_record_path(project_root: Path, validated: pe.ValidatedExecutionManifest, *,
                        dry_run: bool) -> Path:
    return _sibling_path(
        project_root, validated, dry_run=dry_run,
        filename="phase2_capability_preflight_abort.json")


# --- contract item 7: independent call-inventory recomputation -----------------------------------


def _verify_call_inventory(validated: pe.ValidatedExecutionManifest) -> None:
    """Recompute the call inventory from the validated manifest; refuse on any mismatch.

    Independent of, and strictly redundant with, the self-consistency check
    ``validate_execution_manifest`` already performs -- this is the runner's OWN belt-and-
    suspenders recomputation, so a future caller that hands this module an already-"validated"
    but tampered :class:`~rejudge.phase2_execution.ValidatedExecutionManifest` (for example,
    constructed directly rather than through ``validate_execution_manifest``) is still caught
    before a single provider call is made.
    """
    entries = validated.provider_call_inventory
    if len(entries) != pe.EXPECTED_CAPABILITY_CELL_COUNT:
        raise InventoryMismatchError(
            f"expected exactly {pe.EXPECTED_CAPABILITY_CELL_COUNT} manifested calls, found "
            f"{len(entries)}")

    seen_call_keys: set[str] = set()
    seen_planning_keys: set[str] = set()
    for index, entry in enumerate(entries):
        if entry.get("call_index") != index:
            raise InventoryMismatchError(
                f"provider_call_inventory is not in manifest order at position {index}")
        if entry.get("call_role") != pe.CAPABILITY_CALL_ROLE:
            raise InventoryMismatchError(
                f"provider_call_inventory[{index}] has an unexpected call_role: "
                f"{entry.get('call_role')!r}")
        planning_cell_key = entry.get("planning_cell_key")
        if not isinstance(planning_cell_key, str) or not planning_cell_key:
            raise InventoryMismatchError(
                f"provider_call_inventory[{index}] has no planning_cell_key")
        recomputed_key = pe.derive_execution_call_key(
            validated.execution_identity_sha256, planning_cell_key=planning_cell_key,
            call_role=str(entry.get("call_role")), call_index=int(entry.get("call_index", -1)),
        )
        if recomputed_key != entry.get("execution_call_key"):
            raise InventoryMismatchError(
                f"provider_call_inventory[{index}] execution_call_key does not match its "
                "freshly recomputed value")
        if recomputed_key in seen_call_keys:
            raise InventoryMismatchError(f"duplicate execution_call_key: {recomputed_key!r}")
        seen_call_keys.add(recomputed_key)
        if planning_cell_key in seen_planning_keys:
            raise InventoryMismatchError(f"duplicate planning_cell_key: {planning_cell_key!r}")
        seen_planning_keys.add(planning_cell_key)

    if seen_planning_keys != set(validated.planning_cell_keys):
        raise InventoryMismatchError(
            "provider_call_inventory planning cells disagree with the manifest's own planning "
            "cell inventory")


# --- contract item 5: corpus rendering + forecast cross-check ------------------------------------


def _render_and_verify_corpus(
    project_root: Path, validated: pe.ValidatedExecutionManifest,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Render the capability_qa corpus and assert its hash matches the manifest-bound forecast.

    Returns ``(corpus_entries, protocol)``. The manifest-bound ``cost_forecast`` artifact is
    expected to carry a ``bindings.rendered_corpus.{canonical_sha256,entry_count}`` binding --
    the same field path ``rejudge.phase2_preflight_forecast`` already uses for exactly this
    purpose (see this module's own docstring and the accompanying report for why that shape,
    rather than a bespoke one, was chosen).
    """
    protocol = phase2_plan.load_protocol(project_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)
    bundle, _bundle_protocol = prompt_bundle.load_and_validate(
        project_root / pe.DEFAULT_PROMPT_BUNDLE_RELATIVE_PATH,
        project_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH,
    )
    try:
        corpus_entries = capability_corpus.render_capability_corpus(bundle, protocol, project_root)
    except capability_corpus.CapabilityCorpusError as exc:
        raise CorpusMismatchError(f"could not render the capability_qa corpus: {exc}") from exc
    observed_sha = capability_corpus.corpus_canonical_sha256(corpus_entries)

    forecast_path = _resolve_manifest_path(
        project_root, str(validated.execution_identity["cost_forecast"]["path"]))
    forecast_payload = _load_json(forecast_path, CorpusMismatchError)
    try:
        rendered_corpus_binding = forecast_payload["bindings"]["rendered_corpus"]
        bound_sha = rendered_corpus_binding["canonical_sha256"]
        bound_count = rendered_corpus_binding["entry_count"]
    except (KeyError, TypeError) as exc:
        raise CorpusMismatchError(
            f"manifest-bound cost_forecast artifact is missing bindings.rendered_corpus: "
            f"{exc}") from exc
    if not isinstance(bound_sha, str) or bound_sha != observed_sha:
        raise CorpusMismatchError(
            "rendered capability_qa corpus sha does not match the manifest-bound forecast: "
            f"forecast bound {bound_sha!r}, freshly rendered {observed_sha!r}")
    if bound_count != len(corpus_entries):
        raise CorpusMismatchError(
            "rendered capability_qa corpus entry_count does not match the manifest-bound "
            f"forecast: forecast bound {bound_count!r}, freshly rendered {len(corpus_entries)!r}")

    return corpus_entries, protocol


def _capability_cells_by_key(protocol: Mapping[str, Any], project_root: Path) -> dict[str, dict]:
    main_ids = phase2_plan.load_main_question_ids(protocol, project_root)
    all_cells = phase2_plan.enumerate_cells(protocol, main_ids)
    return {str(cell["cell_key"]): cell for cell in all_cells if cell["kind"] == "capability_qa"}


def _corpus_lookup(
    corpus_entries: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(str(entry["question_id"]), str(entry["side"])): entry for entry in corpus_entries}


# --- contract item 6: temperature/max_tokens resolution -------------------------------------------


def _load_role_limits_v2(
    project_root: Path, validated: pe.ValidatedExecutionManifest,
) -> dict[str, Any]:
    path = _resolve_manifest_path(
        project_root,
        str(validated.execution_identity["role_limits_and_request_settings_artifact"]["path"]),
    )
    return _load_json(path, ManifestRejectedError)


# --- contract item 4: strict client-construction parameters ---------------------------------------


def _build_client_params(
    project_root: Path, validated: pe.ValidatedExecutionManifest,
    role_limits_v2_payload: Mapping[str, Any], *, dry_run: bool,
) -> ClientConstructionParams:
    request_settings = role_limits_v2_payload["request_settings"]
    context_ceilings = role_limits_v2_payload["context_ceilings"]
    model_context_limits = {
        model_id: int(entry["context_length_tokens"])
        for model_id, entry in context_ceilings.items()
    }
    streaming_pinned_models = frozenset(request_settings["streaming_pinned_models"])
    extra_request_fields = {
        model_id: dict(fields)
        for model_id, fields in request_settings["per_model_extra_fields"].items()
    }
    # Sourced from whatever role-limits-and-request-settings artifact the manifest actually
    # binds (the v3 artifact in every real manifest today: max_retries=2, not v2's 3) -- this
    # function never hardcodes a retry count of its own.
    max_retries = int(request_settings["transport"]["max_retries"])

    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(
        project_root / pe.DEFAULT_PRICE_SNAPSHOT_RELATIVE_PATH,
        project_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH,
    )
    model_prices = {
        model_id: {
            "in": float(entry["input_usd_per_million_tokens"]),
            "out": float(entry["output_usd_per_million_tokens"]),
        }
        for model_id, entry in snapshot["models"].items()
    }

    usage_log_path = _usage_log_path(project_root, validated, dry_run=dry_run)
    error_log_path = _error_log_path(project_root, validated, dry_run=dry_run)
    # The manifest-bound ledger binding itself, verbatim -- the exact
    # {"path", "ledger_identity"} pair a production client_factory needs to resolve the real,
    # hash-chained identity dict api_client.load_chained_usage_ledger expects. Never fabricated
    # here: this module does not know that chain's exact identity shape (see api_client.py's
    # private _ledger_identity()), only the manifest's own binding to it.
    ledger_identity = (
        dict(validated.execution_identity["ledger"]) if not dry_run else None)

    return ClientConstructionParams(
        dry_run=dry_run,
        approved_cap_usd=validated.stage_cap_usd,
        require_explicit_reasoning_max_tokens=True,
        strict_context_mode=True,
        model_context_limits=model_context_limits,
        max_retries=max_retries,
        streaming_pinned_models=streaming_pinned_models,
        extra_request_fields=extra_request_fields,
        model_prices=model_prices,
        usage_log_path=usage_log_path,
        error_log_path=error_log_path,
        ledger_identity=ledger_identity,
    )


# --- storage policy (real schema) + archival (contract item 12) ----------------------------------


def _load_and_validate_storage_policy(
    project_root: Path, validated: pe.ValidatedExecutionManifest,
) -> dict[str, Any]:
    """Load the manifest-bound storage_policy artifact and validate it against its REAL schema.

    Belt-and-suspenders redundant with (never a substitute for)
    ``rejudge.phase2_execution._validate_storage_policy_gate``, which already validates this
    same artifact during manifest validation -- this is this module's own independent
    recomputation, in the same spirit as :func:`_verify_call_inventory`. The archive destination
    lives at ``versioned_destination`` (the real, tracked
    ``rejudge/phase2_storage_policy_2026-07-18.json`` schema); there has never been an
    ``archive_destination`` key in the real artifact.
    """
    path = _resolve_manifest_path(
        project_root, str(validated.execution_identity["storage_policy"]["path"]))
    policy = _load_json(path, StoragePolicyError)
    if set(policy) != set(pe.STORAGE_POLICY_TOP_LEVEL_KEYS):
        raise StoragePolicyError(f"storage_policy fields drifted at {path}")
    if policy.get("schema_version") != pe.STORAGE_POLICY_SCHEMA_VERSION:
        raise StoragePolicyError(f"storage_policy.schema_version drifted at {path}")
    destination = policy.get("versioned_destination")
    if not isinstance(destination, str) or not destination.strip():
        raise StoragePolicyError(
            f"storage_policy.versioned_destination must be a non-empty string in {path}")
    if policy.get("execution_authorized") is not False:
        raise StoragePolicyError(
            f"storage_policy.execution_authorized must be exactly false in {path}")
    return policy


def _storage_policy_archive_root(policy: Mapping[str, Any]) -> Path:
    return Path(str(policy["versioned_destination"]))


def _archive_subdir_name(
    validated: pe.ValidatedExecutionManifest, *, dry_run: bool, suffix: str = "",
) -> str:
    base = (
        f"{validated.stage}_{validated.execution_identity_sha256[:16]}_"
        f"{'dryrun' if dry_run else 'live'}"
    )
    return f"{base}{suffix}"


def _probe_archive_writability(
    archive_root: Path, validated: pe.ValidatedExecutionManifest, *, dry_run: bool,
) -> Path:
    """Prove the manifest-bound archive destination is durably writable, before any call.

    Creates the run-scoped archive subdirectory (the SAME one :func:`_archive_outputs` will
    later write the real archive into) and writes, fsyncs, and deletes a small probe file inside
    it. Called before the provider client is ever constructed: a failed probe refuses the entire
    run with zero calls dispatched -- nothing has been spent yet, so there is nothing to lose by
    refusing early. Returns the (not-yet-populated) run-scoped destination directory so the
    caller can reuse it for the real archive later without re-parsing the storage policy.
    """
    destination = archive_root / _archive_subdir_name(validated, dry_run=dry_run)
    probe_path = destination / f".archive_writability_probe_{uuid.uuid4().hex}"
    try:
        destination.mkdir(parents=True, exist_ok=True)
        with probe_path.open("wb") as stream:
            stream.write(b"archive-writability-probe")
            stream.flush()
            os.fsync(stream.fileno())
        probe_path.unlink()
    except OSError as exc:
        raise ArchiveError(
            f"archive destination failed a pre-flight writability probe: {destination}: "
            f"{exc}") from exc
    return destination


def _archive_outputs(
    *, archive_root: Path, validated: pe.ValidatedExecutionManifest, dry_run: bool,
    manifest_path: Path, results_path: Path, completion_path: Path, usage_log_path: Path,
) -> Path:
    destination = archive_root / _archive_subdir_name(validated, dry_run=dry_run)
    try:
        destination.mkdir(parents=True, exist_ok=True)
        files: dict[str, Path] = {
            "manifest.json": manifest_path,
            "results.jsonl": results_path,
            "completion.json": completion_path,
        }
        if usage_log_path.exists():
            files["usage_events.jsonl"] = usage_log_path
        sums: list[str] = []
        for name, source in sorted(files.items()):
            target = destination / name
            shutil.copy2(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            sums.append(f"{digest}  {name}")
        (destination / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ArchiveError(f"could not archive outputs to {destination}: {exc}") from exc
    return destination


# --- abort archival: every exception path archives partial state before re-raising ---------------


def _abort_reason_for(exc: BaseException) -> str:
    """Classify an exception into the abort record's human-facing ``reason`` bucket."""
    if isinstance(exc, ResumeBlockedError):
        return "resume_or_per_call_blocker"
    if isinstance(exc, (CorpusMismatchError, InventoryMismatchError)):
        return "corpus_or_inventory_refusal"
    if isinstance(exc, api_client.UnknownChargeHalt):
        return "unknown_charge_halt"
    if isinstance(exc, api_client.CapExceededError):
        return "cost_cap_halt"
    if isinstance(exc, OutputPersistenceError):
        return "output_persistence_failure"
    if isinstance(exc, CompletionGateError):
        return "completion_gate_failure"
    return "unexpected_exception"


def _write_abort_record(
    path: Path, *, reason: str, exception: BaseException, cells_completed: int, dry_run: bool,
    execution_identity_sha256: str, stage: str,
) -> dict[str, Any]:
    """Write the abort record: reason, exception type+message, cells completed, a timestamp.

    A real wall-clock timestamp is fine here (unlike any canonical-identity-hashed artifact,
    where a clock would make the hash unreproducible): this record is a runtime-only diagnostic,
    never folded into ``execution_identity_sha256`` or any other canonical hash.
    """
    record = {
        "schema_version": ABORT_SCHEMA_VERSION,
        "stage": stage,
        "execution_identity_sha256": execution_identity_sha256,
        "dry_run": dry_run,
        "reason": reason,
        "exception_type": type(exception).__name__,
        "exception_message": str(exception),
        "cells_completed": cells_completed,
        "aborted_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(path, record)
    return record


def _archive_aborted_state(
    *, archive_root: Path, validated: pe.ValidatedExecutionManifest, dry_run: bool,
    manifest_path: Path, results_path: Path, completion_path: Path, usage_log_path: Path,
    abort_record_path: Path,
) -> Path:
    """Archive whatever partial state exists to an ``-aborted``-suffixed sibling destination.

    Mirrors :func:`_archive_outputs`'s own file set (manifest, outputs-so-far, usage ledger,
    SHA256SUMS) plus the abort record itself, but -- unlike :func:`_archive_outputs`, which only
    ever runs after a real completion -- never requires ``results_path``/``completion_path`` to
    exist: an abort can happen before a single output row does.
    """
    destination = archive_root / _archive_subdir_name(validated, dry_run=dry_run, suffix="-aborted")
    try:
        destination.mkdir(parents=True, exist_ok=True)
        files: dict[str, Path] = {"manifest.json": manifest_path, "abort.json": abort_record_path}
        if results_path.is_file():
            files["results.jsonl"] = results_path
        if completion_path.is_file():
            files["completion.json"] = completion_path
        if usage_log_path.is_file():
            files["usage_events.jsonl"] = usage_log_path
        sums: list[str] = []
        for name, source in sorted(files.items()):
            target = destination / name
            shutil.copy2(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            sums.append(f"{digest}  {name}")
        (destination / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ArchiveError(f"could not archive aborted state to {destination}: {exc}") from exc
    return destination


# --- completion record -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompletionRecord:
    schema_version: str
    stage: str
    execution_identity_sha256: str
    dry_run: bool
    total_calls: int
    counts: Mapping[str, int]
    output_rows_path: str
    output_rows_sha256: str
    completed_at_utc: str
    archive_destination: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "execution_identity_sha256": self.execution_identity_sha256,
            "dry_run": self.dry_run,
            "total_calls": self.total_calls,
            "counts": dict(self.counts),
            "output_rows_path": self.output_rows_path,
            "output_rows_sha256": self.output_rows_sha256,
            "completed_at_utc": self.completed_at_utc,
            "archive_destination": self.archive_destination,
        }


# --- main entry point --------------------------------------------------------------------------


def run_preflight(
    manifest_path: str | Path,
    project_root: str | Path,
    authorization_path: str | Path | None,
    *,
    client_factory: ClientFactory,
    dry_run: bool,
) -> CompletionRecord:
    """Execute (or resume, or idempotently re-finish) the capability_preflight stage.

    See the module docstring for the full fail-closed contract. ``client_factory`` is called
    exactly once, with a single :class:`ClientConstructionParams`, and must return an object
    satisfying :class:`PreflightClient`.
    """
    project_root = Path(project_root)
    manifest_path = Path(manifest_path)

    manifest_dict = pe.load_execution_manifest(manifest_path)
    authorization = (
        _load_authorization(Path(authorization_path)) if authorization_path is not None else None
    )
    try:
        validated = pe.validate_execution_manifest(
            manifest_dict, project_root=project_root, authorization=authorization,
            require_authorized=not dry_run,
        )
    except pe.Phase2ExecutionError as exc:
        raise ManifestRejectedError(str(exc)) from exc

    if validated.stage != pe.STAGE_CAPABILITY_PREFLIGHT:
        # Unreachable via validate_execution_manifest today (every other stage raises
        # UnsupportedStageError first), but this module supports exactly one stage and must
        # never silently proceed if that ever changes.
        raise ManifestRejectedError(
            f"this runner only executes {pe.STAGE_CAPABILITY_PREFLIGHT!r}, got {validated.stage!r}")

    _verify_call_inventory(validated)

    ledger_path = _ledger_path(project_root, validated)
    try:
        with run_manifest.output_lock(ledger_path):
            return _run_locked(
                validated=validated, project_root=project_root, manifest_path=manifest_path,
                client_factory=client_factory, dry_run=dry_run,
            )
    except run_manifest.OutputLockedError as exc:
        raise LockHeldError(str(exc)) from exc


def _run_locked(
    *, validated: pe.ValidatedExecutionManifest, project_root: Path, manifest_path: Path,
    client_factory: ClientFactory, dry_run: bool,
) -> CompletionRecord:
    # Defense-in-depth: this is the ONLY function that ever dispatches a real provider call, so
    # it must never simply trust the caller's ``dry_run`` flag. ``run_preflight`` already passes
    # ``require_authorized=not dry_run`` into ``validate_execution_manifest``, but that wiring
    # lives at a single call site; re-asserting it here means a live run is refused even if some
    # future caller (a refactor of run_preflight, a new test harness, a direct import) invokes
    # this function with an unauthorized manifest and ``dry_run=False``.
    if not dry_run and not validated.authorized:
        raise ManifestRejectedError(
            "refusing a live run: the validated manifest is not authorized "
            "(ValidatedExecutionManifest.authorized is False)")

    # Defense-in-depth: independently re-verify the call inventory here too, not only in
    # run_preflight before the lock is acquired. This is what makes _verify_call_inventory's own
    # documented purpose ("a future caller that hands this module an already-'validated' but
    # tampered ValidatedExecutionManifest ... is still caught before a single provider call is
    # made") actually true of THIS function, the one that issues the calls, rather than true only
    # of run_preflight's wiring around it.
    _verify_call_inventory(validated)

    results_path = _results_path(project_root, validated, dry_run=dry_run)
    completion_path = _completion_path(project_root, validated, dry_run=dry_run)
    usage_log_path = _usage_log_path(project_root, validated, dry_run=dry_run)
    abort_record_path = _abort_record_path(project_root, validated, dry_run=dry_run)

    # --- storage-policy fix: validate the REAL storage-policy artifact and prove the archive
    # --- destination is durably writable BEFORE the client is ever constructed ---
    storage_policy_payload = _load_and_validate_storage_policy(project_root, validated)
    archive_root = _storage_policy_archive_root(storage_policy_payload)
    _probe_archive_writability(archive_root, validated, dry_run=dry_run)

    def _abort_best_effort(exc: BaseException) -> None:
        """Archive whatever partial state exists, then let ``exc`` propagate unchanged.

        Never itself raised to the caller: a failed secondary safety-net write must never mask
        the primary failure that triggered it (see the try/except this closure is used from).
        """
        try:
            cells_completed = _count_completed_cells(results_path)
            _write_abort_record(
                abort_record_path, reason=_abort_reason_for(exc), exception=exc,
                cells_completed=cells_completed, dry_run=dry_run,
                execution_identity_sha256=validated.execution_identity_sha256,
                stage=validated.stage,
            )
            _archive_aborted_state(
                archive_root=archive_root, validated=validated, dry_run=dry_run,
                manifest_path=manifest_path, results_path=results_path,
                completion_path=completion_path, usage_log_path=usage_log_path,
                abort_record_path=abort_record_path,
            )
        except Exception:
            pass

    try:
        corpus_entries, protocol = _render_and_verify_corpus(project_root, validated)
        cells_by_key = _capability_cells_by_key(protocol, project_root)
        corpus_lookup = _corpus_lookup(corpus_entries)
        role_limits_v2_payload = _load_role_limits_v2(project_root, validated)

        try:
            prepare_jsonl_output(results_path)
        except RunnerOutputPersistenceError as exc:
            raise OutputPersistenceError(str(exc)) from exc

        # --- contract item 10: startup resume audit (BEFORE the client is ever constructed, so
        # --- a blocked resume can never trigger even one provider-facing side effect) ---
        existing_rows = _read_jsonl_rows(results_path)
        existing_events = _read_usage_events_for_audit(usage_log_path)
        audit = pe.audit_resume(validated, output_rows=existing_rows, usage_events=existing_events)
        if audit.disposition is pe.ResumeDisposition.BLOCKED_RECONCILIATION:
            raise ResumeBlockedError(
                f"resume audit blocked {len(audit.blockers)} call(s); first blockers: "
                f"{list(audit.blockers[:10])!r}")

        client_params = _build_client_params(
            project_root, validated, role_limits_v2_payload, dry_run=dry_run)
        client: PreflightClient = client_factory(client_params)

        todo_keys = set(audit.todo_call_keys)
        # Iterates provider_call_inventory's own order (not todo_keys, whose set order is
        # unspecified) so calls execute in manifest order (contract item 7).
        todo_entries = [
            entry for entry in validated.provider_call_inventory
            if str(entry["execution_call_key"]) in todo_keys
        ]

        for entry in todo_entries:
            planning_cell_key = str(entry["planning_cell_key"])
            cell = cells_by_key.get(planning_cell_key)
            if cell is None:
                raise InventoryMismatchError(
                    f"provider_call_inventory entry references unknown planning cell "
                    f"{planning_cell_key!r}")
            corpus_entry = corpus_lookup.get((str(cell["question_id"]), str(entry["side"])))
            if corpus_entry is None:
                raise InventoryMismatchError(
                    f"no rendered corpus entry for question {cell['question_id']!r} side "
                    f"{entry['side']!r}")

            resolved = role_limits.resolve_request_parameters(
                role_limits_v2_payload, protocol, str(entry["model"]), pe.CAPABILITY_CALL_ROLE)
            messages = [
                {"role": "system", "content": corpus_entry["system_prompt"]},
                {"role": "user", "content": corpus_entry["user_prompt"]},
            ]
            call_result = client.complete(
                messages=messages, model=str(entry["model"]), temperature=resolved.temperature,
                seed=int(entry["seed"]), max_tokens=resolved.effective_max_tokens,
                request_metadata={
                    "execution_call_key": entry["execution_call_key"],
                    "request_fields_sha256": entry["request_fields_sha256"],
                },
            )
            verdict = parse_capability_verdict(call_result.raw_output)
            row = {
                "execution_call_key": entry["execution_call_key"],
                "planning_cell_key": planning_cell_key,
                "call_index": entry["call_index"],
                "question_id": cell["question_id"],
                "model": entry["model"],
                "seed": entry["seed"],
                "side": entry["side"],
                "raw_output": call_result.raw_output,
                "verdict": verdict,
                "request_fields_sha256": entry["request_fields_sha256"],
                "response_metadata": dict(call_result.response_metadata),
                "dry_run": dry_run,
            }
            try:
                append_jsonl_record(results_path, row)
            except RunnerOutputPersistenceError as exc:
                raise OutputPersistenceError(str(exc)) from exc

            # --- contract item (per-call blocker detection): re-check durable state
            # --- immediately after every persist step, rather than deferring to the
            # --- end-of-loop completion gate. The client's own halt_on_unknown_charge=True
            # --- already halts an unknown charge at the source (see build_production_client_
            # --- factory); this is an ADDITIONAL, independent check reading only durable state,
            # --- so it also catches a blocker a non-strict client_factory's client might miss
            # --- (for example a duplicate terminal event). ---
            call_key = str(entry["execution_call_key"])
            post_call_rows = _read_jsonl_rows(results_path)
            post_call_events = _read_usage_events_for_audit(usage_log_path)
            post_call_audit = pe.audit_resume(
                validated, output_rows=post_call_rows, usage_events=post_call_events)
            if post_call_audit.per_call.get(call_key) is pe.ResumeDisposition.BLOCKED_RECONCILIATION:
                raise ResumeBlockedError(
                    f"blocker condition detected immediately after call {call_key!r} (an "
                    "unknown charge, a charged-malformed response, or a duplicate); halting "
                    "now rather than deferring to the completion gate; first blockers: "
                    f"{list(post_call_audit.blockers[:5])!r}")

        # --- contract item 11: completion gate ---
        final_rows = _read_jsonl_rows(results_path)
        final_events = _read_usage_events_for_audit(usage_log_path)
        final_audit = pe.audit_resume(validated, output_rows=final_rows, usage_events=final_events)
        if final_audit.disposition is not pe.ResumeDisposition.COMPLETE:
            raise CompletionGateError(
                f"completion gate failed: counts={dict(final_audit.counts)!r}; first blockers: "
                f"{list(final_audit.blockers[:10])!r}")
        if len(final_rows) != pe.EXPECTED_CAPABILITY_CELL_COUNT:
            raise CompletionGateError(
                f"expected exactly {pe.EXPECTED_CAPABILITY_CELL_COUNT} result rows, found "
                f"{len(final_rows)}")
        if not dry_run:
            success_count = sum(1 for event in final_events if event.get("status") == "success")
            if success_count != pe.EXPECTED_CAPABILITY_CELL_COUNT:
                raise CompletionGateError(
                    "live ledger success-event count does not reconcile with "
                    f"{pe.EXPECTED_CAPABILITY_CELL_COUNT} manifested calls: observed "
                    f"{success_count}")

        output_rows_sha256 = hashlib.sha256(results_path.read_bytes()).hexdigest()
        completion = CompletionRecord(
            schema_version=COMPLETION_SCHEMA_VERSION,
            stage=validated.stage,
            execution_identity_sha256=validated.execution_identity_sha256,
            dry_run=dry_run,
            total_calls=pe.EXPECTED_CAPABILITY_CELL_COUNT,
            counts=final_audit.counts,
            output_rows_path=str(results_path),
            output_rows_sha256=output_rows_sha256,
            completed_at_utc=datetime.now(timezone.utc).isoformat(),
            archive_destination="",
        )
        _atomic_write_json(completion_path, completion.to_json())
    except Exception as exc:
        _abort_best_effort(exc)
        raise

    # --- contract item 12: mandatory archival (both dry and live; see module docstring) ---
    archive_destination = _archive_outputs(
        archive_root=archive_root, validated=validated, dry_run=dry_run,
        manifest_path=manifest_path, results_path=results_path, completion_path=completion_path,
        usage_log_path=usage_log_path,
    )
    completion = replace(completion, archive_destination=str(archive_destination))
    _atomic_write_json(completion_path, completion.to_json())
    return completion


# --- production client factory (contract item 4) + run_live ---------------------------------------


def _lazy_together_sdk_client() -> Any:
    """Import and construct the real ``together`` SDK client. Called only for a live run."""
    from together import Together

    return Together()


class _ProductionClientAdapter:
    """Adapts a real ``api_client.RejudgeClient`` to this module's :class:`PreflightClient`.

    ``RejudgeClient.complete(...)`` returns only the raw completion text; this module
    additionally persists a structured ``response_metadata`` snapshot per row. That snapshot is
    recovered from the SAME durable usage event the client itself just fsynced before
    ``complete()`` returned -- its in-memory ``usage_events`` property is a copy of exactly that
    durable log -- never recomputed or guessed independently.
    """

    def __init__(self, client: "api_client.RejudgeClient") -> None:
        self._client = client

    def complete(
        self, *, messages: Sequence[Mapping[str, str]], model: str, temperature: float,
        seed: int, max_tokens: int, request_metadata: Mapping[str, Any],
    ) -> PreflightCallResult:
        raw_output = self._client.complete(
            list(messages), model, temperature, seed, max_tokens,
            kind=pe.CAPABILITY_CALL_ROLE, request_metadata=dict(request_metadata),
        )
        events = self._client.usage_events
        if not events or events[-1].get("status") != "success":
            raise RuntimeError(
                "production client adapter expected the client's own just-appended usage "
                "event to be a durable 'success' immediately after a successful complete() "
                "call")
        response_metadata = events[-1].get("response_metadata") or {}
        return PreflightCallResult(
            raw_output=raw_output, response_metadata=dict(response_metadata))


def build_production_client_factory(
    *, sdk_client_factory: Callable[[], Any] | None = None,
) -> ClientFactory:
    """Return a :data:`ClientFactory` that builds a real, fully strict ``api_client.RejudgeClient``.

    Every strict-mode Phase 2 setting :class:`ClientConstructionParams` carries is threaded
    through verbatim: ``require_explicit_reasoning_max_tokens=True``, ``strict_context_mode=True``
    with its per-model ``model_context_limits``, ``max_retries`` (as resolved by
    :func:`_build_client_params` from the bound v3 role-limits artifact -- 2, not v2's 3),
    ``streaming_pinned_models``, ``extra_request_fields``, ``halt_on_unknown_charge=True`` (so
    this module's own abort-archival ``except`` in :func:`_run_locked` also covers a
    client-raised :class:`rejudge.api_client.UnknownChargeHalt`), and the manifest's OWN stage
    cap. This cannot simply call ``rejudge.run_accounting.create_accounted_client``: that helper
    does not expose any of the strict-mode kwargs above.

    The manifest's OWN ``ledger_identity`` binding (``ClientConstructionParams.ledger_identity``)
    is a simplified ``{"path", "ledger_identity"}`` label pair frozen at manifest-authoring time
    (see ``phase2_execution.py``'s ``LEDGER_KEYS``), not the real hash-chained identity dict
    ``api_client.load_chained_usage_ledger`` compares against (that dict can only be known once
    the ledger file itself exists). Exactly like the legacy ``rejudge/runner.py`` live path, the
    REAL identity is instead established at call time via ``api_client.prepare_usage_ledger``
    against ``params.usage_log_path`` (the manifest-bound ledger PATH); ``params.ledger_identity``
    is still required to be present as the precondition proving the manifest actually bound a
    live ledger for this stage.

    The real ``together`` SDK is imported LAZILY: only inside a call to the factory this function
    returns, and only then -- never merely by calling ``build_production_client_factory()``
    itself, and never at this module's import time (see
    ``test_module_purity_no_sdk_import_at_module_load``). Pass ``sdk_client_factory`` to fully
    unit-test this path with a fake SDK object injected in place of a real ``together.Together()``
    client -- exactly the seam ``api_client.RejudgeClient``'s own ``_sdk_client`` constructor
    parameter exists for.
    """
    resolved_sdk_factory = sdk_client_factory or _lazy_together_sdk_client

    def _factory(params: ClientConstructionParams) -> PreflightClient:
        if params.dry_run:
            raise ValueError(
                "build_production_client_factory builds a LIVE client only; params.dry_run "
                "must be False (a dry run uses DeterministicDryRunClient instead)")
        if params.ledger_identity is None:
            raise ValueError(
                "a live run requires a manifest-bound ledger_identity, proving the manifest "
                "actually bound a live ledger for this stage")

        real_ledger_identity = api_client.prepare_usage_ledger(
            params.usage_log_path, allow_create=True)
        snapshot = api_client.load_chained_usage_ledger(
            params.usage_log_path, expected_identity=real_ledger_identity)

        sdk_client = resolved_sdk_factory()
        client = api_client.RejudgeClient(
            approved_cap_usd=params.approved_cap_usd,
            dry_run=False,
            error_log_path=str(params.error_log_path),
            max_retries=params.max_retries,
            _sdk_client=sdk_client,
            model_prices={model: dict(prices) for model, prices in params.model_prices.items()},
            strict_model_pricing=True,
            initial_spend_usd=float(snapshot.summary["actual_spend_usd"]),
            initial_uncertain_spend_usd=float(snapshot.summary["uncertain_spend_usd"]),
            usage_log_path=str(params.usage_log_path),
            _ledger_snapshot=snapshot,
            _accounting_factory_token=api_client._LIVE_ACCOUNTING_FACTORY_TOKEN,
            require_explicit_reasoning_max_tokens=params.require_explicit_reasoning_max_tokens,
            model_context_limits=dict(params.model_context_limits),
            strict_context_mode=params.strict_context_mode,
            streaming_pinned_models=frozenset(params.streaming_pinned_models),
            extra_request_fields={
                model: dict(fields) for model, fields in params.extra_request_fields.items()},
            halt_on_unknown_charge=True,
        )
        return _ProductionClientAdapter(client)

    return _factory


def run_live(
    manifest_path: str | Path, project_root: str | Path, authorization_path: str | Path,
) -> CompletionRecord:
    """Execute a REAL, spend-authorized capability_preflight run against the live Together API.

    The only entry point in this module that can spend real money. Unlike ``main()``'s
    ``--dry-run``-only CLI, this library function REQUIRES a resolvable ``authorization_path`` --
    there is no flag or parameter to bypass it. ``run_preflight``'s own
    ``require_authorized=not dry_run`` wiring into ``validate_execution_manifest``, and
    ``_run_locked``'s independent re-assertion of ``validated.authorized``, both still apply
    underneath this call unchanged; this function adds one more, even earlier fail-closed check
    so a caller cannot reach either of those by passing ``None`` here.

    Uses :func:`build_production_client_factory` for a real, fully strict
    ``api_client.RejudgeClient``. The real ``together`` SDK is imported only once this call
    actually reaches client construction (see that factory's own docstring) -- never merely by
    importing this module or calling this function up to that point.
    """
    if authorization_path is None:
        raise ManifestRejectedError(
            "run_live requires a resolvable authorization_path; a live run can never proceed "
            "without one")
    return run_preflight(
        manifest_path, project_root, authorization_path,
        client_factory=build_production_client_factory(), dry_run=False,
    )


# --- dry-run-only CLI (contract item 1) ------------------------------------------------------------


def _dry_run_client_factory(params: ClientConstructionParams) -> DeterministicDryRunClient:
    return DeterministicDryRunClient(params.usage_log_path)


def main(argv: Sequence[str] | None = None) -> int:
    """A ``--dry-run``-only CLI. Any invocation without an explicit ``--dry-run`` flag refuses.

    The live CLI (with a real, authorized manifest and a production ``client_factory`` wrapping
    ``rejudge.api_client.RejudgeClient``) intentionally does not exist as a CLI: use
    :func:`run_live` as a library entry point instead, which requires a resolvable
    authorization_path with no bypass.
    """
    parser = argparse.ArgumentParser(prog="phase2_preflight_runner")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.dry_run:
        print(
            "REFUSED: this CLI only supports --dry-run; a live run must go through the "
            "run_live(...) library entry point with a resolvable authorization_path.",
            file=sys.stderr)
        return 2

    try:
        completion = run_preflight(
            args.manifest, args.project_root, None,
            client_factory=_dry_run_client_factory, dry_run=True,
        )
    except PreflightRunnerError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(completion.to_json(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
