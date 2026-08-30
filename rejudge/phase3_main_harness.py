"""Isolated deterministic harness for the production-shared Phase 3 main path.

The harness selects one real budget-zero main judgment from the exact confirmed inventory.
Each of two fresh executions preseeds all 492 main transcripts from the bound bundle, resolves
the 492 transcript markers plus that judgment through ``phase3_runner.resolve_main_cells``,
and calls ``phase2_canary_runner.run_canary`` with the formal main safety settings. The only
model call reaches a module-owned fake through the same role-limit and request-journal wrapper
order used by main. A fixed harness clock makes the two 493-row output stores byte-identical.

This module constructs no provider client and performs no reviewer, finalization, or analysis
dispatch.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from rejudge import (
    api_client,
    phase2_canary_runner,
    phase3_main_runner,
    phase3_plan,
    phase3_runner,
    records,
)
from rejudge.phase2_call_cache import CallKey, request_fingerprint
from rejudge.phase2_canary_live import RoleLimitResolvingClient
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_execution import canonical_sha256
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD,
    JournalingClient,
    RequestJournal,
)
from scripts import phase3_preseed_transcripts


SCHEMA_VERSION = "phase3_main_harness_receipt_v2"
SELECTION_ALGORITHM = "sorted_b0_main_judgments_random_choice_v1"
DETERMINISTIC_CREATED_AT = "2000-01-01T00:00:00+00:00"
DETERMINISTIC_VERDICT = (
    "VERDICT: Position A\nCONFIDENCE: 5\n"
    "REASONING: deterministic Phase 3 main-path harness response"
)
EXPECTED_TRANSCRIPT_COUNT = phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT
EXPECTED_JUDGMENT_COUNT = 1
EXPECTED_RESULT_ROW_COUNT = EXPECTED_TRANSCRIPT_COUNT + EXPECTED_JUDGMENT_COUNT
JUDGMENT_CALL_ROLE = "judge_verdict"
JUDGMENT_REQUEST_MAX_TOKENS = 512

TOP_FIELDS = frozenset({
    "schema_version",
    "status",
    "execution_authorized",
    "provider_calls_authorized",
    "main_run_spend_authorized",
    "harness_seed",
    "deterministic_created_at",
    "protocol_canonical_sha256",
    "prompt_bundle_canonical_sha256",
    "role_limits_canonical_sha256",
    "model_ids",
    "transcript_inputs",
    "selection",
    "result_contract",
    "executions",
    "first_output_store_sha256",
    "rerun_output_store_sha256",
})
TRANSCRIPT_INPUT_FIELDS = frozenset({
    "main_bundle_path",
    "main_bundle_raw_sha256",
    "main_bundle_canonical_sha256",
    "verification_path",
    "verification_raw_sha256",
    "verification_canonical_sha256",
    "verification_expected_main_bundle_canonical_sha256",
    "main_transcript_count",
})
SELECTION_FIELDS = frozenset({
    "algorithm",
    "b0_candidate_count",
    "selected_index",
    "selected_main_judgment",
    "selected_transcript_dependency",
})
CELL_FIELDS = frozenset({
    "cell_key",
    "kind",
    "condition",
    "question_id",
    "judge_model",
    "debater_model",
    "transcript_index",
    "replicate_index",
    "query_budget",
    "dependency_keys",
})
RESULT_CONTRACT_FIELDS = frozenset({
    "transcript_count",
    "judgment_count",
    "row_count",
    "cell_keys_canonical_sha256",
})
EXECUTION_FIELDS = frozenset({
    "execution_index",
    "execution_identity",
    "root",
    "result_store_path",
    "result_store_raw_sha256",
    "result_store_row_count",
    "result_store_cell_keys_canonical_sha256",
    "selected_judgment_result_canonical_sha256",
    "usage_ledger_path",
    "usage_ledger_raw_sha256",
    "usage_ledger_state_path",
    "usage_ledger_state_raw_sha256",
    "usage_ledger_id",
    "request_journal_path",
    "request_journal_raw_sha256",
    "request_journal_entry_count",
    "journal_call",
})
JOURNAL_CALL_FIELDS = frozenset({
    "cell_key",
    "call_role",
    "model",
    "kind",
    "temperature",
    "seed",
    "requested_max_tokens",
    "effective_max_tokens",
    "slot",
    "attempt",
    "request_sha256",
})


class MainHarnessError(RuntimeError, ValueError):
    """The isolated main harness or its receipt failed closed."""


class _DeterministicMainClient:
    """Exact module-owned fake below the production role-limit wrapper."""

    dry_run = True
    offline_only = True
    halt_on_unknown_charge = True

    def __init__(self, *, expected_cell_key: str, expected_model: str,
                 expected_effective_max_tokens: int) -> None:
        self.expected_cell_key = expected_cell_key
        self.expected_model = expected_model
        self.expected_effective_max_tokens = expected_effective_max_tokens
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
        request_metadata=None,
    ) -> str:
        if kind != "verdict":
            raise MainHarnessError(f"harness received unexpected call kind {kind!r}")
        if model != self.expected_model:
            raise MainHarnessError("harness call used a model other than the selected judge")
        if max_tokens != self.expected_effective_max_tokens:
            raise MainHarnessError("harness call bypassed the bound effective role limit")
        if not isinstance(request_metadata, Mapping):
            raise MainHarnessError("harness call omitted request metadata")
        if (
            request_metadata.get("cell_key") != self.expected_cell_key
            or request_metadata.get("call_role") != JUDGMENT_CALL_ROLE
            or request_metadata.get("judge_model") != self.expected_model
        ):
            raise MainHarnessError("harness call metadata differs from the selected judgment")
        journal_sha = _sha256(
            request_metadata.get(JOURNAL_REQUEST_SHA256_FIELD),
            f"request_metadata.{JOURNAL_REQUEST_SHA256_FIELD}",
        )
        expected_sha = request_fingerprint(
            messages=messages,
            model=model,
            temperature=temperature,
            seed=seed,
            max_tokens=JUDGMENT_REQUEST_MAX_TOKENS,
        )
        if journal_sha != expected_sha:
            raise MainHarnessError("journal request fingerprint did not bind the harness call")
        self.calls.append({
            "cell_key": self.expected_cell_key,
            "call_role": JUDGMENT_CALL_ROLE,
            "model": model,
            "kind": kind,
            "temperature": temperature,
            "seed": seed,
            "requested_max_tokens": JUDGMENT_REQUEST_MAX_TOKENS,
            "effective_max_tokens": max_tokens,
            "slot": 0,
            "attempt": 1,
            "request_sha256": journal_sha,
        })
        return DETERMINISTIC_VERDICT


class _ForbiddenReviewer:
    """A reviewer sentinel. A b0 harness cell must never invoke it."""

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        raise MainHarnessError("budget-zero main harness attempted a reviewer call")


@dataclass(frozen=True, slots=True)
class _HarnessExecution:
    index: int
    root: Path
    execution_identity: str

    @property
    def results(self) -> Path:
        return self.root / "harness_results.jsonl"

    @property
    def decisions(self) -> Path:
        return self.root / "harness_decisions.jsonl"

    @property
    def usage(self) -> Path:
        return self.root / "harness_usage.jsonl"

    @property
    def journal(self) -> Path:
        return self.root / "harness_request_journal.jsonl"


@dataclass(frozen=True, slots=True)
class _TranscriptMaterial:
    main_bundle_path: Path
    verification_path: Path
    binding: dict[str, Any]
    expected_rows: tuple[tuple[str, dict[str, Any]], ...]


def _raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise MainHarnessError(f"could not hash harness artifact {path}") from exc
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MainHarnessError(f"could not load {label} from {path}") from exc
    if not isinstance(value, dict):
        raise MainHarnessError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MainHarnessError(f"{label} must be an object")
    if set(value) != expected:
        raise MainHarnessError(f"{label} fields drifted")
    return value


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise MainHarnessError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MainHarnessError(f"{label} must be a non-negative integer")
    return value


def _absolute(value: Any, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise MainHarnessError(f"{label} must be a non-empty absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise MainHarnessError(f"{label} must be an absolute path")
    return path.resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _cell_dict(cell: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(cell, CELL_FIELDS, "main inventory cell")
    result = dict(cell)
    dependencies = result["dependency_keys"]
    if isinstance(dependencies, (str, bytes)) or not isinstance(dependencies, Sequence):
        raise MainHarnessError("main inventory cell dependency_keys must be a sequence")
    result["dependency_keys"] = list(dependencies)
    return result


def _confirmed_protocol_path(root: Path, protocol: Mapping[str, Any]) -> Path:
    path = (root / phase3_main_runner.CONFIRMED_PROTOCOL_RELATIVE_PATH).resolve()
    tracked = phase3_plan.load_protocol(path)
    if canonical_sha256(tracked) != canonical_sha256(protocol):
        raise MainHarnessError("harness protocol differs from the confirmed tracked main protocol")
    return path


def _select_main_judgment(
    protocol: Mapping[str, Any], *, project_root: Path, seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], int, int]:
    inventory = phase3_main_runner.build_main_inventory(protocol, project_root)
    transcript_cells = [_cell_dict(cell) for cell in inventory.transcript_cells]
    candidates = sorted(
        (
            _cell_dict(cell)
            for cell in inventory.judgment_cells
            if cell["kind"] == phase3_plan.MAIN_JUDGMENT_KIND
            and cell["condition"] == "b0"
            and cell["query_budget"] == 0
        ),
        key=lambda cell: str(cell["cell_key"]),
    )
    if not candidates:
        raise MainHarnessError("confirmed main inventory has no budget-zero judgment cell")
    selected_index = random.Random(seed).randrange(len(candidates))
    selected = candidates[selected_index]
    dependencies = selected["dependency_keys"]
    if len(dependencies) != 1:
        raise MainHarnessError("selected main judgment must have exactly one transcript dependency")
    transcript_by_key = {str(cell["cell_key"]): cell for cell in transcript_cells}
    dependency = transcript_by_key.get(str(dependencies[0]))
    if dependency is None:
        raise MainHarnessError(
            "selected main judgment dependency is not in the transcript inventory")
    if (
        dependency["kind"] != phase3_plan.MAIN_TRANSCRIPT_KIND
        or dependency["question_id"] != selected["question_id"]
        or dependency["debater_model"] != selected["debater_model"]
        or dependency["transcript_index"] != selected["transcript_index"]
    ):
        raise MainHarnessError("selected main judgment and transcript dependency are misjoined")
    return transcript_cells, selected, dependency, selected_index, len(candidates)


def _load_transcript_material(
    *, main_bundle_path: str | Path, verification_path: str | Path,
    inventory_transcripts: Sequence[Mapping[str, Any]],
) -> _TranscriptMaterial:
    bundle_path = _absolute(main_bundle_path, "main transcript bundle path")
    report_path = _absolute(verification_path, "transcript verification path")
    bundle = _load_object(bundle_path, "main transcript bundle")
    verification = _load_object(report_path, "transcript verification")
    try:
        expected_bundle_sha = verification["bundle_canonical_sha256"]["main_bundle"]
    except (KeyError, TypeError) as exc:
        raise MainHarnessError(
            "transcript verification omits the expected main bundle canonical hash") from exc
    expected_bundle_sha = _sha256(
        expected_bundle_sha, "verification expected main bundle canonical hash")
    bundle_canonical_sha = canonical_sha256(bundle)
    if bundle_canonical_sha != expected_bundle_sha:
        raise MainHarnessError("main transcript bundle differs from its verification binding")
    try:
        rows = phase3_preseed_transcripts._rows_for_bundle(
            bundle,
            kind=phase3_plan.MAIN_TRANSCRIPT_KIND,
            cells=[dict(cell) for cell in inventory_transcripts],
            label=str(bundle_path),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MainHarnessError(
            "main transcript bundle does not exactly cover the main plan") from exc
    row_keys = [cell_key for cell_key, _result in rows]
    if len(rows) != EXPECTED_TRANSCRIPT_COUNT or len(row_keys) != len(set(row_keys)):
        raise MainHarnessError(
            f"main transcript bundle must map one-to-one onto {EXPECTED_TRANSCRIPT_COUNT} rows")
    binding = {
        "main_bundle_path": bundle_path.as_posix(),
        "main_bundle_raw_sha256": _raw_sha256(bundle_path),
        "main_bundle_canonical_sha256": bundle_canonical_sha,
        "verification_path": report_path.as_posix(),
        "verification_raw_sha256": _raw_sha256(report_path),
        "verification_canonical_sha256": canonical_sha256(verification),
        "verification_expected_main_bundle_canonical_sha256": expected_bundle_sha,
        "main_transcript_count": len(rows),
    }
    return _TranscriptMaterial(
        main_bundle_path=bundle_path,
        verification_path=report_path,
        binding=binding,
        expected_rows=tuple((key, dict(result)) for key, result in rows),
    )


def _result_contract(
    expected_rows: Sequence[tuple[str, Mapping[str, Any]]], selected: Mapping[str, Any],
) -> dict[str, Any]:
    keys = sorted([key for key, _result in expected_rows] + [str(selected["cell_key"])])
    return {
        "transcript_count": EXPECTED_TRANSCRIPT_COUNT,
        "judgment_count": EXPECTED_JUDGMENT_COUNT,
        "row_count": EXPECTED_RESULT_ROW_COUNT,
        "cell_keys_canonical_sha256": canonical_sha256(keys),
    }


def _execution_identity(
    *, index: int, harness_seed: int, protocol_sha: str, prompt_bundle_sha: str,
    role_limits_sha: str, transcript_binding: Mapping[str, Any],
    selected: Mapping[str, Any], dependency: Mapping[str, Any],
) -> str:
    digest = canonical_sha256({
        "schema_version": SCHEMA_VERSION,
        "execution_index": index,
        "harness_seed": harness_seed,
        "protocol_canonical_sha256": protocol_sha,
        "prompt_bundle_canonical_sha256": prompt_bundle_sha,
        "role_limits_canonical_sha256": role_limits_sha,
        "main_bundle_raw_sha256": transcript_binding["main_bundle_raw_sha256"],
        "verification_raw_sha256": transcript_binding["verification_raw_sha256"],
        "selected_main_judgment": dict(selected),
        "selected_transcript_dependency": dict(dependency),
    })
    return f"phase3-main-harness-v2:{index}:{digest}"


def _fresh_root(path: Path) -> None:
    if path.exists():
        raise MainHarnessError(f"harness execution root is not fresh: {path}")
    path.mkdir(parents=True)


def _row_count(path: Path) -> int:
    try:
        return sum(bool(line.strip()) for line in path.read_bytes().splitlines())
    except OSError as exc:
        raise MainHarnessError(f"could not count result rows in {path}") from exc


def _validate_selected_result(
    result: Any, selected: Mapping[str, Any], dependency_result: Mapping[str, Any],
) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise MainHarnessError("selected main judgment result must be an object")
    expected_fields = {
        "cell_key": selected["cell_key"],
        "condition": selected["condition"],
        "question_id": selected["question_id"],
        "transcript_index": selected["transcript_index"],
        "judge_model": selected["judge_model"],
        "budget": selected["query_budget"],
        "replicate": selected["replicate_index"],
        "queries_used": 0,
        "exchanges": [],
        "raw_verdict_text": DETERMINISTIC_VERDICT,
        "dry_run": True,
        "created_at": DETERMINISTIC_CREATED_AT,
    }
    for field, expected in expected_fields.items():
        if result.get(field) != expected:
            raise MainHarnessError(f"selected main judgment result {field} drifted")
    if result.get("world") != dependency_result.get("world"):
        raise MainHarnessError("selected judgment is not joined to its transcript world")
    messages = result.get("judge_messages")
    if (
        isinstance(messages, (str, bytes))
        or not isinstance(messages, Sequence)
        or len(messages) < 2
        or not isinstance(messages[-1], Mapping)
        or messages[-1].get("role") != "assistant"
        or messages[-1].get("content") != DETERMINISTIC_VERDICT
    ):
        raise MainHarnessError("selected judgment does not carry the deterministic verdict call")
    return result


def _validate_result_store(
    path: Path, *, expected_rows: Sequence[tuple[str, Mapping[str, Any]]],
    selected: Mapping[str, Any], contract: Mapping[str, Any],
) -> tuple[Mapping[str, Any], int, str]:
    store = CellResultStore(path)
    expected_transcripts = {key: dict(result) for key, result in expected_rows}
    expected_keys = set(expected_transcripts) | {str(selected["cell_key"])}
    if set(store._results) != expected_keys:
        raise MainHarnessError("harness result store exact cell coverage drifted")
    rows = _row_count(path)
    if rows != EXPECTED_RESULT_ROW_COUNT:
        raise MainHarnessError("harness result store row count drifted")
    for cell_key, expected in expected_transcripts.items():
        if store.get(cell_key) != expected:
            raise MainHarnessError(
                f"harness transcript row differs from the bound bundle: {cell_key}")
    dependency_key = str(selected["dependency_keys"][0])
    judgment = _validate_selected_result(
        store.get(str(selected["cell_key"])), selected, expected_transcripts[dependency_key])
    observed_keys_sha = canonical_sha256(sorted(expected_keys))
    if observed_keys_sha != contract["cell_keys_canonical_sha256"]:
        raise MainHarnessError("harness result-store cell-key digest drifted")
    return judgment, rows, observed_keys_sha


def _effective_judgment_limit(
    role_limits: Mapping[str, Any], selected: Mapping[str, Any],
) -> int:
    try:
        entry = role_limits["model_role_limits"][selected["judge_model"]][JUDGMENT_CALL_ROLE]
        base = int(entry["base_role_max_tokens"])
        effective = int(entry["effective_request_max_tokens"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MainHarnessError("role limits omit the selected judge verdict call") from exc
    if base != JUDGMENT_REQUEST_MAX_TOKENS or effective < base:
        raise MainHarnessError("selected judge verdict role limits differ from the call contract")
    return effective


def _expected_journal_call(
    judgment: Mapping[str, Any], selected: Mapping[str, Any],
    protocol: Mapping[str, Any], role_limits: Mapping[str, Any],
) -> dict[str, Any]:
    seed = judgment.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise MainHarnessError("selected judgment result seed is invalid")
    try:
        temperature = protocol["decisions"]["execution_semantics"][
            "temperature_by_call_role"][JUDGMENT_CALL_ROLE]
    except (KeyError, TypeError) as exc:
        raise MainHarnessError("protocol omits the judge verdict temperature") from exc
    messages = judgment["judge_messages"]
    request_messages = list(messages[:-1])
    call_seed = seed + 99_999
    model = str(selected["judge_model"])
    request_sha = request_fingerprint(
        messages=request_messages,
        model=model,
        temperature=temperature,
        seed=call_seed,
        max_tokens=JUDGMENT_REQUEST_MAX_TOKENS,
    )
    return {
        "cell_key": str(selected["cell_key"]),
        "call_role": JUDGMENT_CALL_ROLE,
        "model": model,
        "kind": "verdict",
        "temperature": temperature,
        "seed": call_seed,
        "requested_max_tokens": JUDGMENT_REQUEST_MAX_TOKENS,
        "effective_max_tokens": _effective_judgment_limit(role_limits, selected),
        "slot": 0,
        "attempt": 1,
        "request_sha256": request_sha,
    }


def _validate_genesis_ledger(ledger: api_client.UsageLedgerSnapshot) -> None:
    expected_summary = {
        "events": 0,
        "actual_spend_usd": 0.0,
        "uncertain_spend_usd": 0.0,
        "accounted_spend_usd": 0.0,
        "unmatched_reservations": 0,
    }
    if ledger.last_sequence != 0 or ledger.summary != expected_summary:
        raise MainHarnessError("offline harness usage ledger is not genesis-only")


def _run_execution(
    execution: _HarnessExecution, *, protocol_path: Path,
    inventory_transcripts: Sequence[Mapping[str, Any]], selected: Mapping[str, Any],
    protocol: Mapping[str, Any], bundle: Mapping[str, Any], role_limits: Mapping[str, Any],
    transcript_material: _TranscriptMaterial, result_contract: Mapping[str, Any],
) -> dict[str, Any]:
    _fresh_root(execution.root)
    preseed = phase3_preseed_transcripts.preseed_main(
        protocol_path=protocol_path,
        project_root=protocol_path.parents[1],
        main_bundle_path=transcript_material.main_bundle_path,
        verification_report_path=transcript_material.verification_path,
        target_store_path=execution.results,
    )
    if preseed != {
        "target_store_path": str(execution.results),
        "main_bundle_count": EXPECTED_TRANSCRIPT_COUNT,
        "written": EXPECTED_TRANSCRIPT_COUNT,
        "skipped": 0,
    }:
        raise MainHarnessError("main harness did not perform one exact fresh 492-row preseed")

    ledger_identity = api_client.prepare_usage_ledger(execution.usage, allow_create=True)
    ledger = api_client.load_chained_usage_ledger(
        execution.usage, expected_identity=ledger_identity)
    _validate_genesis_ledger(ledger)

    effective_limit = _effective_judgment_limit(role_limits, selected)
    fake = _DeterministicMainClient(
        expected_cell_key=str(selected["cell_key"]),
        expected_model=str(selected["judge_model"]),
        expected_effective_max_tokens=effective_limit,
    )
    journal = RequestJournal(
        execution.journal, execution_identity=execution.execution_identity)
    client = JournalingClient(
        RoleLimitResolvingClient(fake, role_limits["model_role_limits"]), journal)
    plan = [dict(cell) for cell in inventory_transcripts] + [dict(selected)]
    resolved = phase3_runner.resolve_main_cells(
        plan, protocol=protocol, bundle=bundle)
    if (
        len(resolved) != EXPECTED_RESULT_ROW_COUNT
        or sum(cell.is_transcript for cell in resolved) != EXPECTED_TRANSCRIPT_COUNT
        or sum(cell.cell_key == selected["cell_key"] for cell in resolved) != 1
    ):
        raise MainHarnessError("main harness resolved cell set differs from 492 plus one")
    with patch.object(records, "utc_now_iso", return_value=DETERMINISTIC_CREATED_AT):
        outcome = phase2_canary_runner.run_canary(
            results_path=execution.results,
            decisions_path=execution.decisions,
            client=client,
            reviewer=_ForbiddenReviewer(),
            anchor_judge_model="",
            protocol=dict(protocol),
            bundle=dict(bundle),
            pause_when_unlabeled=True,
            limit=1,
            cells=resolved,
            max_workers=1,
            transcript_generation_forbidden=True,
            namespace=str(protocol["cell_key_namespace"]),
            pending_payload_limit=64,
            role_limits=dict(role_limits),
            fatal_unknown_charge=True,
        )
    if (
        outcome.completed != 1
        or outcome.skipped != EXPECTED_TRANSCRIPT_COUNT
        or outcome.paused != 0
        or outcome.deferred != 0
        or outcome.abandoned != 0
        or outcome.halted_reason is not None
        or outcome.pending_payloads
    ):
        raise MainHarnessError(
            f"main harness did not complete exactly one judgment: {outcome!r}")
    if execution.decisions.exists() and execution.decisions.read_bytes():
        raise MainHarnessError("budget-zero harness unexpectedly wrote reviewer decisions")

    judgment, row_count, cell_keys_sha = _validate_result_store(
        execution.results,
        expected_rows=transcript_material.expected_rows,
        selected=selected,
        contract=result_contract,
    )
    expected_call = _expected_journal_call(judgment, selected, protocol, role_limits)
    if fake.calls != [expected_call]:
        raise MainHarnessError("module-owned fake did not receive the exact judgment call")
    expected_identity = (
        str(selected["cell_key"]), JUDGMENT_CALL_ROLE, 0, 1)
    verified_journal = RequestJournal(
        execution.journal, execution_identity=execution.execution_identity)
    if verified_journal.entry_identities() != frozenset({expected_identity}):
        raise MainHarnessError("harness request journal does not contain the exact one call")
    key = CallKey(*expected_identity)
    if verified_journal.request_sha256(key) != expected_call["request_sha256"]:
        raise MainHarnessError("harness journal request fingerprint drifted")

    state_path = ledger.state_path
    return {
        "execution_index": execution.index,
        "execution_identity": execution.execution_identity,
        "root": execution.root.as_posix(),
        "result_store_path": execution.results.as_posix(),
        "result_store_raw_sha256": _raw_sha256(execution.results),
        "result_store_row_count": row_count,
        "result_store_cell_keys_canonical_sha256": cell_keys_sha,
        "selected_judgment_result_canonical_sha256": canonical_sha256(judgment),
        "usage_ledger_path": execution.usage.as_posix(),
        "usage_ledger_raw_sha256": _raw_sha256(execution.usage),
        "usage_ledger_state_path": state_path.as_posix(),
        "usage_ledger_state_raw_sha256": _raw_sha256(state_path),
        "usage_ledger_id": str(ledger.identity["ledger_id"]),
        "request_journal_path": execution.journal.as_posix(),
        "request_journal_raw_sha256": _raw_sha256(execution.journal),
        "request_journal_entry_count": 1,
        "journal_call": expected_call,
    }


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    try:
        with path.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise MainHarnessError(f"refusing to overwrite harness receipt {path}") from exc


def run_isolated_harness(
    *, protocol: Mapping[str, Any], prompt_bundle: Mapping[str, Any],
    role_limits: Mapping[str, Any], project_root: str | Path,
    harness_root: str | Path, receipt_path: str | Path, harness_seed: int,
    formal_artifact_root: str | Path | None = None,
    main_transcript_bundle_path: str | Path = (
        phase3_preseed_transcripts.DEFAULT_MAIN_BUNDLE_PATH),
    transcript_verification_path: str | Path = (
        phase3_preseed_transcripts.DEFAULT_VERIFICATION_REPORT_PATH),
) -> dict[str, Any]:
    """Run two fresh offline executions of one selected real main judgment."""
    seed = _nonnegative_int(harness_seed, "harness_seed")
    phase3_plan.validate_protocol(protocol)
    root = Path(project_root).resolve()
    harness = _absolute(harness_root, "harness_root")
    receipt = _absolute(receipt_path, "receipt_path")
    formal = Path(formal_artifact_root).resolve() if formal_artifact_root is not None else None
    if formal is not None and (
        _is_within(harness, formal)
        or _is_within(formal, harness)
        or _is_within(receipt, formal)
    ):
        raise MainHarnessError("harness artifacts must be outside the formal artifact root")
    if harness.exists():
        raise MainHarnessError(f"harness root is not fresh: {harness}")
    if receipt.exists():
        raise MainHarnessError(f"refusing to overwrite harness receipt {receipt}")

    protocol_path = _confirmed_protocol_path(root, protocol)
    transcript_cells, selected, dependency, selected_index, candidate_count = (
        _select_main_judgment(protocol, project_root=root, seed=seed))
    transcript_material = _load_transcript_material(
        main_bundle_path=main_transcript_bundle_path,
        verification_path=transcript_verification_path,
        inventory_transcripts=transcript_cells,
    )
    contract = _result_contract(transcript_material.expected_rows, selected)
    protocol_sha = canonical_sha256(protocol)
    prompt_sha = canonical_sha256(prompt_bundle)
    role_limits_sha = canonical_sha256(role_limits)

    harness.parent.mkdir(parents=True, exist_ok=True)
    harness.mkdir()
    executions = []
    for index in (1, 2):
        identity = _execution_identity(
            index=index,
            harness_seed=seed,
            protocol_sha=protocol_sha,
            prompt_bundle_sha=prompt_sha,
            role_limits_sha=role_limits_sha,
            transcript_binding=transcript_material.binding,
            selected=selected,
            dependency=dependency,
        )
        executions.append(_run_execution(
            _HarnessExecution(index, harness / f"execution-{index}", identity),
            protocol_path=protocol_path,
            inventory_transcripts=transcript_cells,
            selected=selected,
            protocol=protocol,
            bundle=prompt_bundle,
            role_limits=role_limits,
            transcript_material=transcript_material,
            result_contract=contract,
        ))
    first = executions[0]["result_store_raw_sha256"]
    rerun = executions[1]["result_store_raw_sha256"]
    if first != rerun:
        raise MainHarnessError("isolated main-path output stores are not bit-identical")
    if executions[0]["journal_call"] != executions[1]["journal_call"]:
        raise MainHarnessError("isolated executions did not make the same judgment request")

    result = {
        "schema_version": SCHEMA_VERSION,
        "status": "bit_identical_pass",
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "main_run_spend_authorized": False,
        "harness_seed": seed,
        "deterministic_created_at": DETERMINISTIC_CREATED_AT,
        "protocol_canonical_sha256": protocol_sha,
        "prompt_bundle_canonical_sha256": prompt_sha,
        "role_limits_canonical_sha256": role_limits_sha,
        "model_ids": list(protocol["roster"]["judges_final"]),
        "transcript_inputs": transcript_material.binding,
        "selection": {
            "algorithm": SELECTION_ALGORITHM,
            "b0_candidate_count": candidate_count,
            "selected_index": selected_index,
            "selected_main_judgment": selected,
            "selected_transcript_dependency": dependency,
        },
        "result_contract": contract,
        "executions": executions,
        "first_output_store_sha256": first,
        "rerun_output_store_sha256": rerun,
    }
    validate_harness_receipt(
        result,
        protocol=protocol,
        prompt_bundle=prompt_bundle,
        role_limits=role_limits,
        project_root=root,
        formal_artifact_root=formal,
        main_transcript_bundle_path=transcript_material.main_bundle_path,
        transcript_verification_path=transcript_material.verification_path,
    )
    _write_exclusive(receipt, result)
    return result


def validate_harness_receipt(
    receipt: Mapping[str, Any], *, protocol: Mapping[str, Any],
    prompt_bundle: Mapping[str, Any], role_limits: Mapping[str, Any],
    project_root: str | Path, formal_artifact_root: str | Path | None = None,
    main_transcript_bundle_path: str | Path = (
        phase3_preseed_transcripts.DEFAULT_MAIN_BUNDLE_PATH),
    transcript_verification_path: str | Path = (
        phase3_preseed_transcripts.DEFAULT_VERIFICATION_REPORT_PATH),
) -> dict[str, Any]:
    """Recompute selection and verify the receipt-bound local main-path artifacts."""
    _exact_keys(receipt, TOP_FIELDS, "harness receipt")
    if receipt["schema_version"] != SCHEMA_VERSION:
        raise MainHarnessError("unsupported harness receipt schema")
    if receipt["status"] != "bit_identical_pass":
        raise MainHarnessError("harness receipt is not a bit-identical pass")
    if any(receipt[field] is not False for field in (
        "execution_authorized", "provider_calls_authorized", "main_run_spend_authorized"
    )):
        raise MainHarnessError("a harness receipt cannot authorize execution or spend")
    seed = _nonnegative_int(receipt["harness_seed"], "harness receipt seed")
    if receipt["deterministic_created_at"] != DETERMINISTIC_CREATED_AT:
        raise MainHarnessError("harness deterministic clock binding drifted")

    phase3_plan.validate_protocol(protocol)
    root = Path(project_root).resolve()
    _confirmed_protocol_path(root, protocol)
    protocol_sha = canonical_sha256(protocol)
    prompt_sha = canonical_sha256(prompt_bundle)
    role_limits_sha = canonical_sha256(role_limits)
    if receipt["protocol_canonical_sha256"] != protocol_sha:
        raise MainHarnessError("harness receipt binds a different protocol")
    if receipt["prompt_bundle_canonical_sha256"] != prompt_sha:
        raise MainHarnessError("harness receipt binds a different prompt bundle")
    if receipt["role_limits_canonical_sha256"] != role_limits_sha:
        raise MainHarnessError("harness receipt binds different role limits")
    if receipt["model_ids"] != list(protocol["roster"]["judges_final"]):
        raise MainHarnessError("harness receipt model roster drifted")

    transcript_cells, selected, dependency, selected_index, candidate_count = (
        _select_main_judgment(protocol, project_root=root, seed=seed))
    transcript_material = _load_transcript_material(
        main_bundle_path=main_transcript_bundle_path,
        verification_path=transcript_verification_path,
        inventory_transcripts=transcript_cells,
    )
    transcript_binding = _exact_keys(
        receipt["transcript_inputs"], TRANSCRIPT_INPUT_FIELDS, "transcript inputs")
    if dict(transcript_binding) != transcript_material.binding:
        raise MainHarnessError("harness transcript bundle or verification binding drifted")

    selection = _exact_keys(receipt["selection"], SELECTION_FIELDS, "harness selection")
    receipt_selected = _exact_keys(
        selection["selected_main_judgment"], CELL_FIELDS, "selected main judgment")
    receipt_dependency = _exact_keys(
        selection["selected_transcript_dependency"], CELL_FIELDS,
        "selected transcript dependency")
    if (
        selection["algorithm"] != SELECTION_ALGORITHM
        or selection["b0_candidate_count"] != candidate_count
        or selection["selected_index"] != selected_index
        or dict(receipt_selected) != selected
        or dict(receipt_dependency) != dependency
    ):
        raise MainHarnessError("harness selected main judgment or dependency drifted")

    expected_contract = _result_contract(transcript_material.expected_rows, selected)
    contract = _exact_keys(
        receipt["result_contract"], RESULT_CONTRACT_FIELDS, "result contract")
    if dict(contract) != expected_contract:
        raise MainHarnessError("harness exact result-coverage contract drifted")

    executions = receipt["executions"]
    if (
        isinstance(executions, (str, bytes))
        or not isinstance(executions, Sequence)
        or len(executions) != 2
    ):
        raise MainHarnessError("harness receipt must bind exactly two executions")
    formal = Path(formal_artifact_root).resolve() if formal_artifact_root is not None else None
    roots: list[Path] = []
    result_hashes: list[str] = []
    journal_calls: list[dict[str, Any]] = []
    journal_identities: list[str] = []
    ledger_ids: list[str] = []
    for expected_index, raw_execution in enumerate(executions, 1):
        execution = _exact_keys(
            raw_execution, EXECUTION_FIELDS, f"harness execution {expected_index}")
        if execution["execution_index"] != expected_index:
            raise MainHarnessError("harness execution order drifted")
        expected_identity = _execution_identity(
            index=expected_index,
            harness_seed=seed,
            protocol_sha=protocol_sha,
            prompt_bundle_sha=prompt_sha,
            role_limits_sha=role_limits_sha,
            transcript_binding=transcript_material.binding,
            selected=selected,
            dependency=dependency,
        )
        if execution["execution_identity"] != expected_identity:
            raise MainHarnessError("harness deterministic execution identity drifted")
        root_path = _absolute(execution["root"], "harness execution root")
        if formal is not None and (
            _is_within(root_path, formal) or _is_within(formal, root_path)
        ):
            raise MainHarnessError("harness execution overlaps the formal artifact root")
        roots.append(root_path)
        expected_paths = {
            "result_store_path": root_path / "harness_results.jsonl",
            "usage_ledger_path": root_path / "harness_usage.jsonl",
            "usage_ledger_state_path": root_path / "harness_usage.jsonl.state.json",
            "request_journal_path": root_path / "harness_request_journal.jsonl",
        }
        for field, expected_path in expected_paths.items():
            if _absolute(execution[field], field) != expected_path:
                raise MainHarnessError(f"{field} escapes its execution root")
        for path_field, hash_field in (
            ("result_store_path", "result_store_raw_sha256"),
            ("usage_ledger_path", "usage_ledger_raw_sha256"),
            ("usage_ledger_state_path", "usage_ledger_state_raw_sha256"),
            ("request_journal_path", "request_journal_raw_sha256"),
        ):
            expected_hash = _sha256(execution[hash_field], hash_field)
            if _raw_sha256(expected_paths[path_field]) != expected_hash:
                raise MainHarnessError(f"harness artifact hash drifted: {path_field}")

        judgment, row_count, cell_keys_sha = _validate_result_store(
            expected_paths["result_store_path"],
            expected_rows=transcript_material.expected_rows,
            selected=selected,
            contract=expected_contract,
        )
        if (
            execution["result_store_row_count"] != row_count
            or execution["result_store_cell_keys_canonical_sha256"] != cell_keys_sha
            or execution["selected_judgment_result_canonical_sha256"]
            != canonical_sha256(judgment)
        ):
            raise MainHarnessError("harness result coverage receipt fields drifted")

        ledger = api_client.load_chained_usage_ledger(expected_paths["usage_ledger_path"])
        _validate_genesis_ledger(ledger)
        if execution["usage_ledger_id"] != ledger.identity["ledger_id"]:
            raise MainHarnessError("harness receipt ledger identity drifted")

        call = _exact_keys(
            execution["journal_call"], JOURNAL_CALL_FIELDS,
            f"harness execution {expected_index} journal call")
        expected_call = _expected_journal_call(judgment, selected, protocol, role_limits)
        if dict(call) != expected_call:
            raise MainHarnessError("harness exact journal call role, cell, or model drifted")
        journal = RequestJournal(
            expected_paths["request_journal_path"], execution_identity=expected_identity)
        call_identity = (
            expected_call["cell_key"], expected_call["call_role"],
            expected_call["slot"], expected_call["attempt"])
        if (
            execution["request_journal_entry_count"] != 1
            or journal.entry_identities() != frozenset({call_identity})
            or journal.request_sha256(CallKey(*call_identity))
            != expected_call["request_sha256"]
        ):
            raise MainHarnessError("harness request journal exact call coverage drifted")
        result_hashes.append(str(execution["result_store_raw_sha256"]))
        journal_calls.append(expected_call)
        journal_identities.append(expected_identity)
        ledger_ids.append(str(ledger.identity["ledger_id"]))

    if (
        roots[0] == roots[1]
        or _is_within(roots[0], roots[1])
        or _is_within(roots[1], roots[0])
    ):
        raise MainHarnessError("harness execution roots are not isolated")
    if len(set(journal_identities)) != 2 or len(set(ledger_ids)) != 2:
        raise MainHarnessError("harness executions do not have separate ledger/journal identities")
    if journal_calls[0] != journal_calls[1]:
        raise MainHarnessError("harness executions did not journal the same main judgment call")
    first = _sha256(receipt["first_output_store_sha256"], "first output-store hash")
    rerun = _sha256(receipt["rerun_output_store_sha256"], "rerun output-store hash")
    if result_hashes != [first, rerun] or first != rerun:
        raise MainHarnessError("harness output stores are not bit-identical")
    return {
        "status": "bit_identical_pass",
        "harness_seed": seed,
        "selected_main_judgment_cell_key": str(selected["cell_key"]),
        "selected_transcript_dependency_cell_key": str(dependency["cell_key"]),
        "output_store_sha256": first,
        "result_store_row_count": EXPECTED_RESULT_ROW_COUNT,
        "execution_roots": tuple(roots),
    }


__all__ = [
    "MainHarnessError",
    "SCHEMA_VERSION",
    "run_isolated_harness",
    "validate_harness_receipt",
]
