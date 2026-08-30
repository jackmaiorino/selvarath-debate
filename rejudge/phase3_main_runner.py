"""Offline safety foundation for the Phase 3 main runner.

This module deliberately has no production client factory and no live command-line entry
point.  It establishes the ordering and identity rules that a later, separately authorized
main driver must preserve:

* the exact two-judge main inventory is derived internally from the frozen protocol and
  checked against its canonical inventory digest;
* one run lease is held before any run marker, ledger, journal, or client is opened;
* the identity owns one canonical artifact root, which derives its lease and every store;
* a formal identity starts only from absent output artifacts and records a root binding
  behind the active marker;
* exact active-marker and root-binding bytes are restored before the lease releases when
  control unwinds through Python, covering crashes and accidental deletion in the harness;
* the fresh chained ledger is validated and reconciled with the request journal before the
  module constructs its scripted fake; and
* there is no caller-supplied client or client-factory parameter.

The body callback and this module's globals are trusted offline harness code. The callback can
execute arbitrary Python, and Python monkeypatching or filesystem access can bypass ordinary
object boundaries. This module is not a security sandbox. Entry refuses a recorded identity
within its canonical root while its start evidence remains. The binding is crash and
accidental-deletion durable while the lease is held, not tamper-proof after return. A future
production completion/archive step or external identity registry must protect identity reuse
after the foundation returns. That driver must also require a validated new manifest, new
output paths, and separate exact owner authorization before constructing a provider client.

``MainRunIdentity.manifest_sha256`` is a typed offline binding, not a substitute for manifest
validation.  The future production integration must validate the small main manifest and
derive its canonical SHA-256 internally before entering this foundation.  Terminal-disposition
stop bounds, exact full provider completion, final reconciliation, worker concurrency, and the
one-seed bit-identical output-store check are also production integration gates.  This module
does not imply launch readiness.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, TypeVar

from rejudge import api_client, phase3_plan
from rejudge.phase3_v3_live import RunLease
from rejudge.request_journal import (
    JournalingClient,
    RequestJournal,
    find_ambiguous_dispatches,
)


CONFIRMED_MAIN_JUDGES = (
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "Qwen/Qwen3.8-2.4T-A95B",
)
EXPECTED_MAIN_QUESTION_COUNT = 82
EXPECTED_MAIN_TRANSCRIPT_COUNT = 492
EXPECTED_MAIN_JUDGMENT_COUNT = 9_840
EXPECTED_MAIN_CELL_COUNT = EXPECTED_MAIN_TRANSCRIPT_COUNT + EXPECTED_MAIN_JUDGMENT_COUNT
EXPECTED_JUDGMENTS_PER_JUDGE = 4_920
EXPECTED_JUDGMENTS_PER_JUDGE_CONDITION = 984
EXPECTED_MAIN_INVENTORY_SHA256 = (
    "b9019914def49432ebca11164c6a025310757d53d34cb9dc0170c072641937c6"
)
ACTIVE_MARKER_SCHEMA = "phase3_main_active_marker_v1"
IDENTITY_BINDING_SCHEMA = "phase3_main_identity_binding_v1"
CONFIRMED_PROTOCOL_ID = "phase3_budget_knob_2026_08_28_v3r6"
CONFIRMED_PROTOCOL_RELATIVE_PATH = Path("rejudge") / "phase3_protocol_v3_r6.json"


class Phase3MainRunnerError(RuntimeError, ValueError):
    """Base class for a refused offline main-runner setup."""


class MainInventoryError(Phase3MainRunnerError):
    """The protocol-derived main inventory differs from the confirmed scope."""


class MainIdentityInterrupted(Phase3MainRunnerError):
    """The run identity has an active marker or a pre-existing formal artifact."""


class MainLedgerError(Phase3MainRunnerError):
    """The fresh main ledger or its journal reconciliation is not clean."""


class OfflineClientRequired(Phase3MainRunnerError):
    """The module-owned scripted-client invariant was violated."""


@dataclass(frozen=True, slots=True)
class MainRunIdentity:
    """A non-authorizing identity supplied by the offline harness.

    Only digest syntax is checked here.  A future live integration must derive the value from
    a validated canonical manifest rather than accepting a caller assertion.
    """

    run_id: str
    manifest_sha256: str
    artifact_root: Path

    def __post_init__(self) -> None:
        root = Path(self.artifact_root)
        if not root.is_absolute():
            raise Phase3MainRunnerError("main artifact_root must be absolute")
        object.__setattr__(self, "artifact_root", root.resolve())

    @property
    def artifact_root_sha256(self) -> str:
        return hashlib.sha256(
            self.artifact_root.as_posix().encode("utf-8")).hexdigest()

    @property
    def journal_execution_identity(self) -> str:
        return (
            f"{self.run_id}:{self.manifest_sha256}:"
            f"{self.artifact_root_sha256}"
        )

    @property
    def paths(self) -> "MainRunPaths":
        return MainRunPaths.under(self.artifact_root)


@dataclass(frozen=True, slots=True)
class MainRunPaths:
    """All mutable paths owned by one formal main identity."""

    root: Path

    @classmethod
    def under(cls, root: str | Path) -> "MainRunPaths":
        root = Path(root)
        if not root.is_absolute():
            raise Phase3MainRunnerError("main artifact root must be absolute")
        return cls(root=root.resolve())

    @property
    def lease(self) -> Path:
        return self.root / "main_run.lock"

    @property
    def active_marker(self) -> Path:
        return self.root / "main_run.active.json"

    @property
    def identity_binding(self) -> Path:
        return self.root / "main_run.identity.json"

    @property
    def usage_ledger(self) -> Path:
        return self.root / "main_usage.jsonl"

    @property
    def request_journal(self) -> Path:
        return self.root / "main_request_journal.jsonl"

    @property
    def results(self) -> Path:
        return self.root / "main_results.jsonl"

    @property
    def decisions(self) -> Path:
        return self.root / "main_reviewer_decisions.jsonl"

    @property
    def completion(self) -> Path:
        return self.root / "main_completion.json"

    @property
    def unresolved_dispatch_marker(self) -> Path:
        return self.request_journal.with_name(
            f"{self.request_journal.name}.unresolved.json")

    def formal_artifacts(self) -> tuple[tuple[str, Path], ...]:
        """Artifacts that must all be absent when this identity first starts."""
        return (
            ("usage ledger", self.usage_ledger),
            ("usage ledger state", api_client.usage_ledger_state_path(self.usage_ledger)),
            ("request journal", self.request_journal),
            ("unresolved dispatch marker", self.unresolved_dispatch_marker),
            ("result store", self.results),
            ("review decision store", self.decisions),
            ("completion record", self.completion),
        )


@dataclass(frozen=True, slots=True)
class MainInventory:
    """Exact protocol-derived main cells, with no canary or capability rows."""

    question_ids: tuple[str, ...]
    judges: tuple[str, ...]
    cells: tuple[Mapping[str, Any], ...]

    @property
    def transcript_cells(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            cell for cell in self.cells
            if cell["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND)

    @property
    def judgment_cells(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            cell for cell in self.cells
            if cell["kind"] == phase3_plan.MAIN_JUDGMENT_KIND)


@dataclass(frozen=True, slots=True)
class OfflineMainSession:
    """A fake-only session that remains inside the held run lease."""

    identity: MainRunIdentity
    inventory: MainInventory
    ledger_snapshot: api_client.UsageLedgerSnapshot
    journal: RequestJournal
    client: "OfflineJournaledClient"


class ScriptedOfflineClient:
    """Unmodified module-owned fake with no provider construction or transport code."""

    offline_only = True
    dry_run = True
    halt_on_unknown_charge = True

    def __init__(self, responses: tuple[str, ...]) -> None:
        if (type(responses) is not tuple or not responses
                or any(type(response) is not str for response in responses)):
            raise OfflineClientRequired(
                "offline responses must be a non-empty exact tuple of strings")
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
        request_metadata=None,
    ) -> str:
        self.calls.append({
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_tokens,
            "kind": kind,
            "request_metadata": dict(request_metadata or {}),
        })
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


class OfflineJournaledClient:
    """Narrow session facade that does not expose the journal wrapper's inner client."""

    __complete: Callable[..., str]
    __slots__ = ("__complete",)

    def __init__(self, client: JournalingClient) -> None:
        object.__setattr__(
            self, "_OfflineJournaledClient__complete", client.complete)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("offline journaled client is immutable")

    def __delattr__(self, _name: str) -> None:
        raise AttributeError("offline journaled client is immutable")

    @property
    def dry_run(self) -> bool:
        return True

    def complete(
        self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
        request_metadata=None,
    ) -> str:
        return self.__complete(
            messages, model, temperature, seed, max_tokens, kind=kind,
            request_metadata=request_metadata)


T = TypeVar("T")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_identity(identity: MainRunIdentity) -> None:
    if not isinstance(identity.run_id, str) or not identity.run_id.strip():
        raise Phase3MainRunnerError("main run_id must be a non-empty string")
    if not _is_sha256(identity.manifest_sha256):
        raise Phase3MainRunnerError(
            "main manifest_sha256 must be a lowercase SHA-256 digest")
    if identity.paths.root != identity.artifact_root:
        raise Phase3MainRunnerError(
            "main identity artifact root did not canonicalize consistently")


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_value(item) for item in value]
    return value


def _inventory_sha256(inventory: MainInventory) -> str:
    payload = {
        "question_ids": list(inventory.question_ids),
        "judges": list(inventory.judges),
        "cells": [_thaw_value(cell) for cell in inventory.cells],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_inventory(inventory: MainInventory) -> None:
    if inventory.judges != CONFIRMED_MAIN_JUDGES:
        raise MainInventoryError(
            "main judges differ from the confirmed endpoint-specific roster: "
            f"{inventory.judges!r}")
    if len(inventory.question_ids) != EXPECTED_MAIN_QUESTION_COUNT:
        raise MainInventoryError(
            f"main inventory has {len(inventory.question_ids)} questions, expected "
            f"{EXPECTED_MAIN_QUESTION_COUNT}")
    if len(set(inventory.question_ids)) != len(inventory.question_ids):
        raise MainInventoryError("main question inventory contains duplicates")

    by_kind = Counter(str(cell.get("kind")) for cell in inventory.cells)
    expected_kinds = {
        phase3_plan.MAIN_TRANSCRIPT_KIND: EXPECTED_MAIN_TRANSCRIPT_COUNT,
        phase3_plan.MAIN_JUDGMENT_KIND: EXPECTED_MAIN_JUDGMENT_COUNT,
    }
    if dict(by_kind) != expected_kinds:
        raise MainInventoryError(
            f"main cell inventory drifted: observed {dict(by_kind)!r}, "
            f"expected {expected_kinds!r}")
    if len(inventory.cells) != EXPECTED_MAIN_CELL_COUNT:
        raise MainInventoryError(
            f"main inventory has {len(inventory.cells)} cells, expected "
            f"{EXPECTED_MAIN_CELL_COUNT}")

    cell_keys = [str(cell.get("cell_key")) for cell in inventory.cells]
    if len(cell_keys) != len(set(cell_keys)):
        raise MainInventoryError("main inventory contains duplicate cell keys")

    judgment_counts = Counter(
        str(cell.get("judge_model")) for cell in inventory.judgment_cells)
    if judgment_counts != Counter({
        judge: EXPECTED_JUDGMENTS_PER_JUDGE for judge in CONFIRMED_MAIN_JUDGES
    }):
        raise MainInventoryError(
            f"per-judge main inventory drifted: {dict(judgment_counts)!r}")

    judge_condition_counts = Counter(
        (str(cell.get("judge_model")), str(cell.get("condition")))
        for cell in inventory.judgment_cells)
    expected_conditions = {"b0", "sequential_b1", "sequential_b2", "sequential_b4",
                           "sequential_b8"}
    expected_pairs = {
        (judge, condition): EXPECTED_JUDGMENTS_PER_JUDGE_CONDITION
        for judge in CONFIRMED_MAIN_JUDGES
        for condition in expected_conditions
    }
    if judge_condition_counts != Counter(expected_pairs):
        raise MainInventoryError(
            "per-judge, per-condition main inventory differs from 984 slots each")
    observed_sha256 = _inventory_sha256(inventory)
    if observed_sha256 != EXPECTED_MAIN_INVENTORY_SHA256:
        raise MainInventoryError(
            "main inventory differs from the exact canonical protocol-derived cells: "
            f"observed {observed_sha256}, expected {EXPECTED_MAIN_INVENTORY_SHA256}")


def build_main_inventory(
    protocol: Mapping[str, Any], project_root: str | Path,
) -> MainInventory:
    """Derive and assert the exact confirmed 492 plus 9,840 main-only inventory."""
    phase3_plan.validate_protocol(protocol)
    judges = tuple(str(model) for model in protocol["roster"]["judges_final"])
    main_question_ids, _held_out_ids = phase3_plan.load_reference_question_ids(
        protocol, project_root)
    cells = phase3_plan.enumerate_cells(protocol, judges, main_question_ids)
    inventory = MainInventory(
        question_ids=tuple(main_question_ids),
        judges=judges,
        cells=tuple(_freeze_value(dict(cell)) for cell in cells),
    )
    _validate_inventory(inventory)
    return inventory


def build_canonical_main_inventory(project_root: str | Path) -> MainInventory:
    """Load the one confirmed protocol path and derive the run inventory internally."""
    root = Path(project_root).resolve()
    protocol = phase3_plan.load_protocol(root / CONFIRMED_PROTOCOL_RELATIVE_PATH)
    if protocol.get("protocol_id") != CONFIRMED_PROTOCOL_ID:
        raise MainInventoryError(
            "confirmed main protocol path does not contain the r6 protocol")
    return build_main_inventory(protocol, root)


def _fsync_parent_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _assert_fresh_identity(paths: MainRunPaths) -> None:
    if paths.identity_binding.exists():
        raise MainIdentityInterrupted(
            f"main artifact root was already bound at {paths.identity_binding}; a started "
            "identity cannot reuse this root while the binding remains")
    if paths.active_marker.exists():
        raise MainIdentityInterrupted(
            f"main identity has an active marker at {paths.active_marker}; an interrupted "
            "formal identity cannot resume")
    for label, path in paths.formal_artifacts():
        if path.exists():
            raise MainIdentityInterrupted(
                f"main identity is not fresh: {label} already exists at {path}")


def _write_active_marker(identity: MainRunIdentity, paths: MainRunPaths) -> bytes:
    """Exclusively persist formal start.  Existing identical bytes are still a refusal."""
    paths.active_marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": ACTIVE_MARKER_SCHEMA,
        "status": "active",
        "run_id": identity.run_id,
        "manifest_sha256": identity.manifest_sha256,
        "artifact_root": identity.artifact_root.as_posix(),
        "artifact_root_sha256": identity.artifact_root_sha256,
        "journal_execution_identity": identity.journal_execution_identity,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
    }
    encoded = (json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
    try:
        with paths.active_marker.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent_directory(paths.active_marker)
    except FileExistsError as exc:
        raise MainIdentityInterrupted(
            f"main identity already started at {paths.active_marker}; it cannot resume") from exc
    return encoded


def _identity_binding_bytes(identity: MainRunIdentity) -> bytes:
    payload = {
        "schema_version": IDENTITY_BINDING_SCHEMA,
        "run_id": identity.run_id,
        "manifest_sha256": identity.manifest_sha256,
        "artifact_root": identity.artifact_root.as_posix(),
        "artifact_root_sha256": identity.artifact_root_sha256,
        "journal_execution_identity": identity.journal_execution_identity,
    }
    return (json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")


def _write_identity_binding(
    identity: MainRunIdentity, paths: MainRunPaths,
) -> bytes:
    """Persist root start evidence after the active marker starts the identity."""
    encoded = _identity_binding_bytes(identity)
    try:
        with paths.identity_binding.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_parent_directory(paths.identity_binding)
    except FileExistsError as exc:
        raise MainIdentityInterrupted(
            f"main artifact root already has identity binding {paths.identity_binding}") from exc
    return encoded


def _restore_durable_exact(path: Path, expected: bytes) -> None:
    """Atomically restore deleted or altered start evidence while the lease is held."""
    try:
        if path.read_bytes() == expected:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".restore.tmp", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_parent_directory(path)
        if path.read_bytes() != expected:
            raise MainIdentityInterrupted(
                f"could not restore exact main start evidence at {path}")
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _restore_start_evidence(
    paths: MainRunPaths, *, active_marker: bytes, identity_binding: bytes,
) -> None:
    errors: list[tuple[Path, BaseException]] = []
    for path, expected in (
            (paths.identity_binding, identity_binding),
            (paths.active_marker, active_marker)):
        try:
            _restore_durable_exact(path, expected)
        except BaseException as exc:
            errors.append((path, exc))
    if errors:
        rendered = ", ".join(f"{path}: {type(exc).__name__}: {exc}"
                             for path, exc in errors)
        raise MainIdentityInterrupted(
            f"main start evidence could not be restored before lease release: {rendered}")


def _load_ledger_events(path: Path) -> list[dict[str, Any]]:
    """Read events only after load_chained_usage_ledger has validated the stable file."""
    return api_client._read_usage_events(path)  # noqa: SLF001 - one canonical ledger parser


def _fresh_ledger_snapshot(path: Path) -> tuple[api_client.UsageLedgerSnapshot, list[dict]]:
    identity = api_client.prepare_usage_ledger(path, allow_create=True)
    snapshot = api_client.load_chained_usage_ledger(path, expected_identity=identity)
    expected_summary = {
        "events": 0,
        "actual_spend_usd": 0.0,
        "uncertain_spend_usd": 0.0,
        "accounted_spend_usd": 0.0,
        "unmatched_reservations": 0,
    }
    if snapshot.last_sequence != 0 or snapshot.summary != expected_summary:
        raise MainLedgerError(
            "fresh main identity did not start from a genesis-only zero-spend ledger")
    return snapshot, _load_ledger_events(snapshot.path)


def _validate_offline_client(client: Any) -> None:
    if type(client) is not ScriptedOfflineClient:
        raise OfflineClientRequired(
            "offline foundation accepts only its exact module-owned scripted fake")


def run_offline_foundation(
    identity: MainRunIdentity,
    *,
    project_root: str | Path,
    offline_responses: tuple[str, ...] = ("ANSWER: A",),
    body: Callable[[OfflineMainSession], T],
) -> T:
    """Open one fake-only main session under the complete formal lock order.

    This function restores its exact start evidence before releasing the lease, including
    after an ordinary exception. Trusted caller code can still mutate files after return.
    The foundation proves startup ordering and its own scripted-client path, not isolation
    from arbitrary Python and not completion of 9,840 formal judgments.
    """
    _validate_identity(identity)
    if (type(offline_responses) is not tuple or not offline_responses
            or any(type(response) is not str for response in offline_responses)):
        raise OfflineClientRequired(
            "offline responses must be a non-empty exact tuple of strings")
    inventory = build_canonical_main_inventory(project_root)
    _validate_inventory(inventory)
    paths = identity.paths

    with RunLease(paths.lease):
        _assert_fresh_identity(paths)
        active_marker = _write_active_marker(identity, paths)
        identity_binding = _identity_binding_bytes(identity)
        try:
            _write_identity_binding(identity, paths)

            snapshot, ledger_events = _fresh_ledger_snapshot(paths.usage_ledger)
            journal = RequestJournal(
                paths.request_journal,
                execution_identity=identity.journal_execution_identity,
            )
            findings = find_ambiguous_dispatches(journal, ledger_events)
            if findings:
                raise MainLedgerError(
                    "fresh main ledger/journal reconciliation found ambiguity: "
                    f"{findings[:3]!r}")

            raw_client = ScriptedOfflineClient(offline_responses)
            _validate_offline_client(raw_client)
            journaled_client = JournalingClient(raw_client, journal)
            session_client = OfflineJournaledClient(journaled_client)
            session = OfflineMainSession(
                identity=identity,
                inventory=inventory,
                ledger_snapshot=snapshot,
                journal=journal,
                client=session_client,
            )
            return body(session)
        finally:
            _restore_start_evidence(
                paths, active_marker=active_marker,
                identity_binding=identity_binding)


__all__ = [
    "ACTIVE_MARKER_SCHEMA",
    "CONFIRMED_MAIN_JUDGES",
    "EXPECTED_MAIN_CELL_COUNT",
    "EXPECTED_MAIN_INVENTORY_SHA256",
    "EXPECTED_MAIN_JUDGMENT_COUNT",
    "EXPECTED_MAIN_QUESTION_COUNT",
    "EXPECTED_MAIN_TRANSCRIPT_COUNT",
    "MainIdentityInterrupted",
    "MainInventory",
    "MainInventoryError",
    "MainLedgerError",
    "MainRunIdentity",
    "MainRunPaths",
    "OfflineClientRequired",
    "OfflineJournaledClient",
    "OfflineMainSession",
    "Phase3MainRunnerError",
    "ScriptedOfflineClient",
    "build_canonical_main_inventory",
    "build_main_inventory",
    "run_offline_foundation",
]
