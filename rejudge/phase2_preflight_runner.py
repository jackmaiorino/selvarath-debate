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
  COMPLETE cell is never re-run.
* A completion gate re-derives that audit from disk one more time after the loop and refuses
  to call the run done unless it is exact: 1,060 rows, no duplicates, no unknown keys, and (for
  a live run) a reconciled ledger success-event count.
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
factory that never touches a real SDK at all.
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
STORAGE_POLICY_ARCHIVE_DESTINATION_KEY = "archive_destination"
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
    """The startup resume audit found a call that cannot be safely resumed."""


class CompletionGateError(PreflightRunnerError):
    """The post-loop completion audit is not exact."""


class StoragePolicyError(PreflightRunnerError):
    """The manifest-bound storage-policy artifact is missing or malformed."""


class ArchiveError(PreflightRunnerError):
    """Outputs could not be archived to the manifest-bound destination."""


# --- the client seam: no SDK, no network, defined entirely by this module ------------------------


@dataclass(frozen=True, slots=True)
class ClientConstructionParams:
    """Every strict-mode setting a production ``client_factory`` needs.

    A production factory is expected to build this run's ``rejudge.api_client.RejudgeClient``
    from exactly these fields: ``require_explicit_reasoning_max_tokens=True``,
    ``strict_context_mode=True`` with ``model_context_limits``, ``max_retries`` (frozen at 3),
    ``streaming_pinned_models``/``extra_request_fields`` copied verbatim from the manifest-bound
    role-limits-v2 artifact, and the manifest's OWN stage cap (never the cumulative cap) as the
    client's ``approved_cap_usd``. ``usage_log_path`` is the single durable events log the
    client must append every reservation/terminal lifecycle event to (fsynced before each
    ``complete()`` call returns) -- this module's resume and completion audits read only that
    file, never any in-process client state, so a resumed process with a brand-new client object
    still sees every prior call correctly.
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


# --- archival (contract item 12) -----------------------------------------------------------------


def _load_storage_policy_archive_destination(
    project_root: Path, validated: pe.ValidatedExecutionManifest,
) -> Path:
    path = _resolve_manifest_path(
        project_root, str(validated.execution_identity["storage_policy"]["path"]))
    payload = _load_json(path, StoragePolicyError)
    destination = payload.get(STORAGE_POLICY_ARCHIVE_DESTINATION_KEY)
    if not isinstance(destination, str) or not destination.strip():
        raise StoragePolicyError(
            f"storage_policy.{STORAGE_POLICY_ARCHIVE_DESTINATION_KEY} must be a non-empty "
            f"string in {path}")
    return Path(destination)


def _archive_outputs(
    *, archive_root: Path, validated: pe.ValidatedExecutionManifest, dry_run: bool,
    manifest_path: Path, results_path: Path, completion_path: Path, usage_log_path: Path,
) -> Path:
    subdir = (
        f"{validated.stage}_{validated.execution_identity_sha256[:16]}_"
        f"{'dryrun' if dry_run else 'live'}"
    )
    destination = archive_root / subdir
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

    corpus_entries, protocol = _render_and_verify_corpus(project_root, validated)
    cells_by_key = _capability_cells_by_key(protocol, project_root)
    corpus_lookup = _corpus_lookup(corpus_entries)
    role_limits_v2_payload = _load_role_limits_v2(project_root, validated)

    results_path = _results_path(project_root, validated, dry_run=dry_run)
    completion_path = _completion_path(project_root, validated, dry_run=dry_run)
    usage_log_path = _usage_log_path(project_root, validated, dry_run=dry_run)

    try:
        prepare_jsonl_output(results_path)
    except RunnerOutputPersistenceError as exc:
        raise OutputPersistenceError(str(exc)) from exc

    # --- contract item 10: startup resume audit (BEFORE the client is ever constructed, so a
    # --- blocked resume can never trigger even one provider-facing side effect) ---
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
                f"{pe.EXPECTED_CAPABILITY_CELL_COUNT} manifested calls: observed {success_count}")

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

    # --- contract item 12: mandatory archival (both dry and live; see module docstring) ---
    archive_root = _load_storage_policy_archive_destination(project_root, validated)
    archive_destination = _archive_outputs(
        archive_root=archive_root, validated=validated, dry_run=dry_run,
        manifest_path=manifest_path, results_path=results_path, completion_path=completion_path,
        usage_log_path=usage_log_path,
    )
    completion = replace(completion, archive_destination=str(archive_destination))
    _atomic_write_json(completion_path, completion.to_json())
    return completion


# --- dry-run-only CLI (contract item 1) ------------------------------------------------------------


def _dry_run_client_factory(params: ClientConstructionParams) -> DeterministicDryRunClient:
    return DeterministicDryRunClient(params.usage_log_path)


def main(argv: Sequence[str] | None = None) -> int:
    """A ``--dry-run``-only CLI. Any invocation without an explicit ``--dry-run`` flag refuses.

    The live CLI (with a real, authorized manifest and a production ``client_factory`` wrapping
    ``rejudge.api_client.RejudgeClient``) does not exist yet -- see the module docstring.
    """
    parser = argparse.ArgumentParser(prog="phase2_preflight_runner")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.dry_run:
        print(
            "REFUSED: this CLI only supports --dry-run; the live CLI arrives with the real, "
            "authorized manifest.", file=sys.stderr)
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
