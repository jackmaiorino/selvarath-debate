"""Focused fail-closed tests for Phase 3 main finalization."""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from rejudge import api_client, phase3_main_finalization as finalization
from rejudge import phase3_main_manifest, phase3_main_runner
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_call_cache import request_fingerprint
from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
from rejudge.config import ARMS, judgment_seed, position_for
from rejudge.parsers import parse_both
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD,
    RequestJournal,
    journal_key,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SHA256 = "a" * 64
AUTHORIZATION_SHA256 = "b" * 64
AUTHORIZATION_RAW_SHA256 = "c" * 64
AUTHORIZATION_SIGNATURE_RAW_SHA256 = "d" * 64
RUN_ID = "phase3-main-finalization-test"
JOURNAL_IDENTITY = f"{RUN_ID}:{MANIFEST_SHA256}:test-root"
PRIOR_RECONCILED_USD = "0.30985289"
STAGE_CAP_USD = "60.00"


@pytest.fixture(scope="module")
def inventory() -> phase3_main_runner.MainInventory:
    return phase3_main_runner.build_canonical_main_inventory(REPO_ROOT)


def _context_blocklist(
    inventory: phase3_main_runner.MainInventory,
    excluded_cells=(),
) -> dict:
    excluded = []
    counts: Counter[tuple[str, str]] = Counter()
    for cell in excluded_cells:
        excluded.append({
            field: cell[field]
            for field in (
                "cell_key",
                "judge_model",
                "question_id",
                "debater_model",
                "transcript_index",
                "condition",
            )
        })
        counts[(str(cell["judge_model"]), str(cell["condition"]))] += 1
    namespace = str(inventory.cells[0]["cell_key"]).split(":", 1)[0]
    return {
        "scope": "main",
        "cell_key_namespace": namespace,
        "excluded": excluded,
        "excluded_count": len(excluded),
        "counts_by_judge_and_condition": [
            {"judge_model": judge, "condition": condition, "count": count}
            for (judge, condition), count in sorted(counts.items())
        ],
    }


def _terminal_record(cell_key: str) -> dict:
    return {"cell_key": cell_key, "reason": finalization.TERMINAL_REASON}


def _write_result_store(path: Path, rows_to_write) -> None:
    previous = "genesis"
    rows: list[str] = []
    for sequence, (cell_key, result) in enumerate(rows_to_write):
        row = {
            "cell_key": cell_key,
            "result": result,
            "sequence": sequence,
            "prev_event_hash": previous,
        }
        row["event_hash"] = CellResultStore._row_hash(row)
        previous = row["event_hash"]
        rows.append(json.dumps(row, ensure_ascii=False))
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def _main_journal_identity(root: Path) -> str:
    canonical = root.resolve().as_posix()
    root_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{RUN_ID}:{MANIFEST_SHA256}:{root_sha256}"


def _transcript_result(cell: dict) -> dict:
    return {
        "cell_key": cell["cell_key"],
        "question_id": cell["question_id"],
        "transcript_index": cell["transcript_index"],
        "debater_model": cell["debater_model"],
        "world": "fixture-world",
        "question": "Fixture question?",
        "correct_answer": "fixture correct",
        "wrong_answer": "fixture wrong",
        "debate_transcript": [],
        "dry_run": False,
    }


def _judgment_result(cell: dict, response: str, *, dry_run: bool = False) -> dict:
    namespace = str(cell["cell_key"]).split(":", 1)[0]
    seed = judgment_seed(
        cell["question_id"], cell["transcript_index"], cell["judge_model"],
        cell["query_budget"], "clean", cell["replicate_index"],
        debater_model=cell["debater_model"], namespace=namespace,
    )
    base_position = position_for(
        ARMS["clean"], cell["question_id"], cell["transcript_index"],
        cell["judge_model"], cell["query_budget"],
    )
    position_a_is_correct = (
        base_position if int(cell["replicate_index"]) == 0 else not base_position)
    parses = parse_both(response)
    strict_verdict = parses["strict"]["verdict"]
    messages = [
        {"role": "system", "content": "fixture system"},
        {"role": "user", "content": "fixture verdict request"},
        {"role": "assistant", "content": response},
    ]
    return {
        "cell_key": cell["cell_key"],
        "question_id": cell["question_id"],
        "transcript_index": cell["transcript_index"],
        "world": "fixture-world",
        "arm": "clean",
        "judge_model": cell["judge_model"],
        "replicate": cell["replicate_index"],
        "budget": cell["query_budget"],
        "condition": cell["condition"],
        "position_a_is_correct": position_a_is_correct,
        "queries_used": 0,
        "exchanges": [],
        "raw_verdict_text": response,
        "verdict_strict": parses["strict"],
        "verdict_pilot": parses["pilot"],
        "verdict_correct_strict": (
            None if strict_verdict is None
            else (strict_verdict == "A") == position_a_is_correct),
        "verdict_correct_pilot": (
            (parses["pilot"]["verdict"] == "Position A")
            == position_a_is_correct),
        "judge_messages": messages,
        "seed": seed,
        "oracle_model": "fixture oracle",
        "parser_version": parses["parser_version"],
        "harness_version": "fixture",
        "created_at": "2026-08-29T12:00:00Z",
        "dry_run": dry_run,
    }


def _append_ledger_events(path: Path, events: list[dict]) -> None:
    snapshot = api_client.load_chained_usage_ledger(path)
    identity = snapshot.identity
    sequence = snapshot.last_sequence
    previous = snapshot.last_event_hash
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for raw_event in events:
            sequence += 1
            event = {
                **raw_event,
                "ledger_id": identity["ledger_id"],
                "sequence": sequence,
                "prev_event_hash": previous,
            }
            event["event_hash"] = api_client._usage_event_hash(event)
            previous = event["event_hash"]
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    api_client._atomic_write_json(
        api_client.usage_ledger_state_path(path),
        api_client._usage_state_payload(identity, sequence, previous),
    )


def _append_provider_success(
    *,
    ledger_path: Path,
    journal_path: Path,
    journal_identity: str,
    cell: dict,
    response: str,
    call_role: str = finalization.JUDGE_VERDICT_ROLE,
    attempt_suffix: str = "1",
    request_sha256: str | None = None,
    seed: int = 7,
    query_index: int | None = None,
    query_attempt: int | None = None,
    slot: int | None = None,
    model: str | None = None,
) -> str:
    if query_index is not None and slot is not None:
        raise ValueError("provider fixture cannot specify both query_index and slot")
    request_sha256 = request_sha256 or hashlib.sha256(
        f"{cell['cell_key']}:{call_role}:{attempt_suffix}".encode("utf-8")
    ).hexdigest()
    metadata = {
        "stage": "judgment",
        "cell_key": cell["cell_key"],
        "call_role": call_role,
        "condition": cell["condition"],
        "question_id": cell["question_id"],
        "transcript_index": cell["transcript_index"],
        "budget": cell["query_budget"],
        "replicate": cell["replicate_index"],
        "judge_model": cell["judge_model"],
        JOURNAL_REQUEST_SHA256_FIELD: request_sha256,
    }
    if query_index is not None:
        metadata["query_index"] = query_index
    if slot is not None:
        metadata["slot"] = slot
    if query_attempt is not None:
        metadata["attempt"] = query_attempt
    journal = RequestJournal(journal_path, execution_identity=journal_identity)
    journal.put(journal_key(metadata), request_sha256, response)
    attempt_id = f"attempt-{attempt_suffix}"
    stable = {
        "attempt_id": attempt_id,
        "model": model or cell["judge_model"],
        "kind": "verdict",
        "seed": seed,
        "attempt": 0,
        "reserved_prompt_tokens": 10,
        "reserved_completion_tokens": 10,
        "estimated_tokens": 20,
        "metadata": metadata,
    }
    _append_ledger_events(ledger_path, [
        {
            **stable,
            "status": "reserved",
            "prompt_tokens": None,
            "completion_tokens": None,
            "cost_usd": 0.000020,
        },
        {
            **stable,
            "status": "success",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_usd": 0.000015,
            "response_metadata": {
                "finish_reason": "stop",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        },
    ])
    return attempt_id


def _checker_artifacts(
    root: Path,
    *,
    cell_key: str,
    response: str,
    finish_reason: str = "length",
) -> tuple[Path, Path, str]:
    ledger_path = root / "usage.jsonl"
    journal_path = root / "journal.jsonl"
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    request_sha256 = "c" * 64
    metadata = {
        "cell_key": cell_key,
        "call_role": finalization.QUERY_CHECKER_ROLE,
        "slot": 0,
        "attempt": 1,
        JOURNAL_REQUEST_SHA256_FIELD: request_sha256,
    }
    journal = RequestJournal(journal_path, execution_identity=JOURNAL_IDENTITY)
    journal.put(journal_key(metadata), request_sha256, response)
    attempt_id = "checker-attempt-1"
    stable = {
        "attempt_id": attempt_id,
        "model": finalization.DEFAULT_CHECKER_MODEL,
        "kind": "verdict",
        "seed": 7,
        "attempt": 0,
        "reserved_prompt_tokens": 10,
        "reserved_completion_tokens": 10,
        "estimated_tokens": 20,
        "metadata": metadata,
    }
    _append_ledger_events(ledger_path, [
        {
            **stable,
            "status": "reserved",
            "prompt_tokens": None,
            "completion_tokens": None,
            "cost_usd": 0.000020,
        },
        {
            **stable,
            "status": "success",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_usd": 0.000015,
            "response_metadata": {
                "finish_reason": finish_reason,
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        },
    ])
    return ledger_path, journal_path, attempt_id


def test_exact_partition_exposes_terminal_and_context_keys(inventory):
    terminal_cell = inventory.judgment_cells[0]
    context_cell = inventory.judgment_cells[1]
    excluded = {terminal_cell["cell_key"], context_cell["cell_key"]}
    results = [
        cell["cell_key"] for cell in inventory.cells
        if cell["cell_key"] not in excluded
    ]

    partition = finalization.validate_main_partition(
        inventory=inventory,
        result_cell_keys=results,
        terminal_records=[_terminal_record(str(terminal_cell["cell_key"]))],
        context_blocklist=_context_blocklist(inventory, [context_cell]),
    )

    assert partition["observed_transcripts"] == 492
    assert partition["observed_judgments"] == 9_838
    assert partition["terminal_invalid_judgments"] == 1
    assert partition["context_ineligible_judgments"] == 1
    assert partition["terminal_cell_keys"] == [terminal_cell["cell_key"]]
    assert partition["context_ineligible_cell_keys"] == [context_cell["cell_key"]]


def test_partition_rejects_missing_overlap_and_non_checker_terminal(inventory):
    all_keys = [cell["cell_key"] for cell in inventory.cells]
    terminal_cell = inventory.judgment_cells[0]
    with pytest.raises(finalization.MainPartitionError, match="not exact"):
        finalization.validate_main_partition(
            inventory=inventory,
            result_cell_keys=all_keys[:-1],
            terminal_records=[],
            context_blocklist=_context_blocklist(inventory),
        )
    with pytest.raises(finalization.MainPartitionError, match="also has a result"):
        finalization.validate_main_partition(
            inventory=inventory,
            result_cell_keys=all_keys,
            terminal_records=[_terminal_record(str(terminal_cell["cell_key"]))],
            context_blocklist=_context_blocklist(inventory),
        )
    with pytest.raises(finalization.MainPartitionError, match="inadmissible reason"):
        finalization.validate_main_partition(
            inventory=inventory,
            result_cell_keys=all_keys[:-1],
            terminal_records=[
                {"cell_key": terminal_cell["cell_key"], "reason": "provider_error"}
            ],
            context_blocklist=_context_blocklist(inventory),
        )


def test_terminal_bounds_enforce_count_concentration_and_mirror_fraction(
    inventory, monkeypatch,
):
    judgments = list(inventory.judgment_cells)
    with pytest.raises(finalization.TerminalBoundError, match="exceed the frozen bound"):
        finalization.evaluate_terminal_bounds(
            inventory=inventory,
            terminal_records=[
                _terminal_record(str(cell["cell_key"])) for cell in judgments[:21]
            ],
        )

    group = [
        cell for cell in judgments
        if (cell["judge_model"], cell["condition"])
        == (judgments[0]["judge_model"], judgments[0]["condition"])
    ]
    with pytest.raises(finalization.TerminalBoundError, match="concentration"):
        finalization.evaluate_terminal_bounds(
            inventory=inventory,
            terminal_records=[
                _terminal_record(str(cell["cell_key"])) for cell in group[:5]
            ],
        )

    monkeypatch.setattr(finalization, "MAX_TERMINAL_JUDGMENT_CELLS", 500)
    unit_cells: dict[tuple, dict] = {}
    for cell in judgments:
        unit = (
            cell["question_id"],
            cell["judge_model"],
            cell["debater_model"],
            cell["transcript_index"],
            cell["condition"],
        )
        unit_cells.setdefault(unit, cell)
    spread = list(unit_cells.values())[:198]
    with pytest.raises(finalization.TerminalBoundError, match="4 percent"):
        finalization.evaluate_terminal_bounds(
            inventory=inventory,
            terminal_records=[
                _terminal_record(str(cell["cell_key"])) for cell in spread
            ],
        )


def test_checker_malformed_is_reproved_from_ledger_and_journal(tmp_path, inventory):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    ledger, journal, attempt_id = _checker_artifacts(
        tmp_path, cell_key=cell_key, response="")

    evidence = finalization.mechanically_validate_checker_malformed(
        cell_key=cell_key,
        ledger_attempt_id=attempt_id,
        expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
        usage_ledger_path=ledger,
        request_journal_path=journal,
        journal_execution_identity=JOURNAL_IDENTITY,
    )

    assert evidence["ledger_reserved_sequence"] == 1
    assert evidence["ledger_terminal_sequence"] == 2
    assert evidence["journal_sequence"] == 0
    assert evidence["finish_reason"] == "length"
    assert evidence["checker_response_sha256"] == finalization._sha256_text("")
    assert "exactly allow" in evidence["parse_failure"]


def test_valid_checker_token_cannot_be_terminally_disposed(tmp_path, inventory):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    ledger, journal, attempt_id = _checker_artifacts(
        tmp_path, cell_key=cell_key, response="allow", finish_reason="stop")
    with pytest.raises(
            finalization.TerminalDispositionError, match="parses successfully"):
        finalization.mechanically_validate_checker_malformed(
            cell_key=cell_key,
            ledger_attempt_id=attempt_id,
            expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
            usage_ledger_path=ledger,
            request_journal_path=journal,
            journal_execution_identity=JOURNAL_IDENTITY,
        )


def test_terminal_store_is_identity_seeded_chained_and_evidence_bound(
    tmp_path, inventory,
):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    ledger, journal, attempt_id = _checker_artifacts(
        tmp_path, cell_key=cell_key, response="bad checker text")
    path = tmp_path / "terminal.jsonl"
    store = finalization.MainTerminalDispositionStore(
        path,
        run_id=RUN_ID,
        manifest_canonical_sha256=MANIFEST_SHA256,
        inventory=inventory,
    )
    row = store.record_checker_malformed(
        cell_key,
        ledger_attempt_id=attempt_id,
        usage_ledger_path=ledger,
        request_journal_path=journal,
        journal_execution_identity=JOURNAL_IDENTITY,
        result_cell_keys=(),
        recorded_at_utc="2026-08-29T12:00:00Z",
    )
    assert row["prev_event_hash"] == finalization._terminal_seed(
        run_id=RUN_ID,
        manifest_sha256=MANIFEST_SHA256,
        inventory_sha256=finalization.inventory_canonical_sha256(inventory),
    )
    reopened = finalization.MainTerminalDispositionStore(
        path,
        run_id=RUN_ID,
        manifest_canonical_sha256=MANIFEST_SHA256,
        inventory=inventory,
    )
    reopened.verify_all_evidence(
        usage_ledger_path=ledger,
        request_journal_path=journal,
        journal_execution_identity=JOURNAL_IDENTITY,
        result_cell_keys=(),
    )
    assert reopened.cell_keys == {cell_key}
    assert reopened.tail["last_sequence"] == 0
    with pytest.raises(finalization.TerminalDispositionError, match="already exists"):
        reopened.record_checker_malformed(
            cell_key,
            ledger_attempt_id=attempt_id,
            usage_ledger_path=ledger,
            request_journal_path=journal,
            journal_execution_identity=JOURNAL_IDENTITY,
            result_cell_keys=(),
            recorded_at_utc="2026-08-29T12:01:00Z",
        )
    with pytest.raises(finalization.TerminalDispositionError, match="another main identity"):
        finalization.MainTerminalDispositionStore(
            path,
            run_id=f"{RUN_ID}-other",
            manifest_canonical_sha256=MANIFEST_SHA256,
            inventory=inventory,
        )
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["evidence"]["checker_response_sha256"] = "f" * 64
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(finalization.TerminalDispositionError, match="row hash"):
        finalization.MainTerminalDispositionStore(
            path,
            run_id=RUN_ID,
            manifest_canonical_sha256=MANIFEST_SHA256,
            inventory=inventory,
        )
    with pytest.raises(finalization.MainFinalizationError, match="frozen"):
        finalization.MainTerminalDispositionStore(
            tmp_path / "wrong-model.jsonl",
            run_id=RUN_ID,
            manifest_canonical_sha256=MANIFEST_SHA256,
            inventory=inventory,
            checker_model="another-model",
        )


def test_terminal_cell_provider_provenance_allows_only_pre_verdict_roles(inventory):
    cell_key = str(inventory.judgment_cells[0]["cell_key"])
    with pytest.raises(finalization.MainFinalizationError, match="post-checker verdict"):
        finalization._validate_main_provider_provenance(
            inventory=inventory,
            result_rows=[],
            terminal_records=[_terminal_record(cell_key)],
            context_ineligible_cell_keys=[],
            ledger_events=[{"status": "ledger_genesis"}],
            journal_rows=[{
                "cell_key": cell_key,
                "call_role": finalization.JUDGE_VERDICT_ROLE,
                "slot": 0,
                "attempt": 0,
                "request_sha256": "a" * 64,
                "response": "foreign verdict",
            }],
            reviewer_decisions={},
            expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
            expected_oracle_model="fixture oracle",
            result_store_raw_sha256="b" * 64,
            request_journal_raw_sha256="c" * 64,
            usage_ledger_raw_sha256="d" * 64,
        )


def test_checker_truncation_diagnostic_is_stratified(inventory):
    cell = inventory.judgment_cells[0]
    diagnostic = finalization.checker_truncation_diagnostic(
        inventory=inventory,
        ledger_events=[{
            "status": "success",
            "attempt_id": "attempt-length",
            "event_hash": "d" * 64,
            "metadata": {
                "cell_key": cell["cell_key"],
                "call_role": finalization.QUERY_CHECKER_ROLE,
            },
            "response_metadata": {"finish_reason": "length"},
            "prompt_tokens": 2_500,
            "completion_tokens": 16,
        }],
        terminal_records=[],
    )
    assert diagnostic["checker_calls_total"] == 1
    assert diagnostic["finish_length_count"] == 1
    assert diagnostic["by_judge"][cell["judge_model"]]["finish_length_rate"] == 1.0
    assert diagnostic["by_condition"][cell["condition"]]["finish_length_rate"] == 1.0
    assert diagnostic["by_prompt_token_band"]["2-4k"]["finish_length_rate"] == 1.0
    assert "not assumed missing completely at random" in diagnostic["non_claim"]


def _complete_finalization_inputs(tmp_path: Path, inventory, *, observed_cell=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    observed_cell = dict(observed_cell or inventory.judgment_cells[0])
    context_cells = [
        dict(cell) for cell in inventory.judgment_cells
        if cell["cell_key"] != observed_cell["cell_key"]
    ]
    raw_verdict = "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: provider row"
    result_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["results"]
    result_rows = [
        (str(cell["cell_key"]), _transcript_result(dict(cell)))
        for cell in inventory.transcript_cells
    ]
    judgment_result = _judgment_result(observed_cell, raw_verdict)
    result_rows.append((str(observed_cell["cell_key"]), judgment_result))
    _write_result_store(result_path, result_rows)
    ledger_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["usage_ledger"]
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    journal_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["request_journal"]
    journal_identity = _main_journal_identity(tmp_path)
    _append_provider_success(
        ledger_path=ledger_path,
        journal_path=journal_path,
        journal_identity=journal_identity,
        cell=observed_cell,
        response=raw_verdict,
        request_sha256=request_fingerprint(
            messages=judgment_result["judge_messages"][:-1],
            model=str(observed_cell["judge_model"]),
            temperature=finalization.MAIN_VERDICT_TEMPERATURE,
            seed=int(judgment_result["seed"]) + 99_999,
            max_tokens=finalization.MAIN_VERDICT_MAX_TOKENS,
        ),
        seed=int(judgment_result["seed"]) + 99_999,
    )
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(_context_blocklist(inventory, context_cells), sort_keys=True),
        encoding="utf-8",
    )
    review_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["decisions"]
    review_path.write_text("", encoding="utf-8")
    reviewer_index_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["reviewer_index"]
    reviewer_index_path.write_text("", encoding="utf-8")
    reviewer_worklist_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES[
        "reviewer_worklist"]
    reviewer_worklist_path.write_text("{}\n", encoding="utf-8")
    provider_error_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES[
        "provider_error_log"]
    provider_error_path.write_text("", encoding="utf-8")
    run_log_path = tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["run_log"]
    run_log_path.write_text("", encoding="utf-8")
    terminal_store = finalization.MainTerminalDispositionStore(
        tmp_path / phase3_main_manifest.OUTPUT_FILENAMES["terminal_dispositions"],
        run_id=RUN_ID,
        manifest_canonical_sha256=MANIFEST_SHA256,
        inventory=inventory,
    )
    pins_path = REPO_ROOT / "rejudge" / "phase3_main_analysis_pins_2026-08-29.json"
    artifacts = {
        "result_store": result_path,
        "usage_ledger": ledger_path,
        "usage_ledger_state": api_client.usage_ledger_state_path(ledger_path),
        "request_journal": journal_path,
        "review_decisions": review_path,
        "reviewer_index": reviewer_index_path,
        "reviewer_worklist": reviewer_worklist_path,
        "provider_error_log": provider_error_path,
        "run_log": run_log_path,
        "context_blocklist": context_path,
        "terminal_dispositions": terminal_store.path,
        "analysis_pins": pins_path,
    }
    build_inputs = {
        "run_id": RUN_ID,
        "manifest_canonical_sha256": MANIFEST_SHA256,
        "authorization_canonical_sha256": AUTHORIZATION_SHA256,
        "authorization_raw_sha256": AUTHORIZATION_RAW_SHA256,
        "authorization_signature_raw_sha256": AUTHORIZATION_SIGNATURE_RAW_SHA256,
        "inventory": inventory,
        "context_blocklist_path": context_path,
        "terminal_store": terminal_store,
        "result_store_path": result_path,
        "usage_ledger_path": ledger_path,
        "request_journal_path": journal_path,
        "journal_execution_identity": journal_identity,
        "analysis_pins_path": pins_path,
        "artifact_paths": artifacts,
        "expected_oracle_model": "fixture oracle",
        "prior_reconciled_usd": PRIOR_RECONCILED_USD,
        "stage_cap_usd": STAGE_CAP_USD,
    }
    return build_inputs


def _rewrite_result_payload(path: Path, cell_key: str, replacement: dict) -> None:
    rows = []
    for row in finalization.load_result_rows(path):
        result = dict(row["result"])
        if row["cell_key"] == cell_key:
            result = dict(replacement)
        rows.append((str(row["cell_key"]), result))
    _write_result_store(path, rows)


def _truncate_ledger_to_genesis(path: Path) -> None:
    genesis = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    path.write_text(json.dumps(genesis, sort_keys=True) + "\n", encoding="utf-8")
    identity = api_client._ledger_identity(path, str(genesis["ledger_id"]))
    api_client._atomic_write_json(
        api_client.usage_ledger_state_path(path),
        api_client._usage_state_payload(identity, 0, str(genesis["event_hash"])),
    )


def _rewrite_journal_for_foreign_identity(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    previous = "identity:foreign-run:foreign-manifest:foreign-root"
    for sequence, row in enumerate(rows):
        row["sequence"] = sequence
        row["prev_event_hash"] = previous
        row["event_hash"] = RequestJournal._row_hash(row)
        previous = row["event_hash"]
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_finalization_admits_exact_provider_bound_run_and_is_immutable(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    record = finalization.build_finalization_admission(
        **build_inputs,
        recorded_at_utc="2026-08-29T13:00:00Z",
    )
    assert record["status"] == finalization.FINALIZATION_STATUS
    assert record["partition"]["observed_transcripts"] == 492
    assert record["partition"]["observed_judgments"] == 1
    assert record["partition"]["context_ineligible_judgments"] == 9_839
    assert record["terminal_store"]["record_count"] == 0
    assert record["terminal_store"]["last_sequence"] == -1
    assert record["provider_provenance"]["observed_judgment_count"] == 1
    assert record["provider_provenance"]["verdict_journal_count"] == 1
    assert record["provider_provenance"]["verdict_ledger_success_count"] == 1
    assert record["accounting"] == {
        "prior_reconciled_usd": PRIOR_RECONCILED_USD,
        "current_settled_usd": "0.000015",
        "current_uncertain_usd": "0",
        "current_accounted_usd": "0.000015",
        "stage_total_usd": "0.30986789",
        "stage_cap_usd": STAGE_CAP_USD,
        "within_stage_cap": True,
        "usage_ledger_raw_sha256": record["artifact_hashes"]["usage_ledger"][
            "raw_sha256"],
        "usage_ledger_state_raw_sha256": record["artifact_hashes"][
            "usage_ledger_state"]["raw_sha256"],
        "usage_ledger_id": record["accounting"]["usage_ledger_id"],
        "usage_ledger_tail_sequence": 2,
        "usage_ledger_tail_event_hash": record["accounting"][
            "usage_ledger_tail_event_hash"],
    }
    assert record["reconciliation"] == {
        "status": "clean",
        "ambiguous_dispatches": [],
        "unmatched_reservations": 0,
        "unknown_charge_attempt_ids": [],
        "charged_malformed_attempt_ids": [],
        "settled_success_without_journal_attempt_ids": [],
        "unresolved_dispatch_marker_present": False,
    }
    assert set(record["artifact_hashes"]) == finalization.REQUIRED_ARTIFACTS
    assert finalization.validate_finalization_admission(
        record, **build_inputs) == record
    manifest_output_paths = {
        name: (tmp_path / filename).resolve()
        for name, filename in phase3_main_manifest.OUTPUT_FILENAMES.items()
    }
    def validate_bound(
        authorization_raw_sha256: str = AUTHORIZATION_RAW_SHA256,
        authorization_signature_raw_sha256: str = (
            AUTHORIZATION_SIGNATURE_RAW_SHA256),
        candidate=record,
        expected_manifest_output_paths=manifest_output_paths,
    ):
        return finalization.validate_finalization_from_bound_artifacts(
            candidate,
            inventory=inventory,
            expected_run_id=RUN_ID,
            expected_manifest_canonical_sha256=MANIFEST_SHA256,
            expected_authorization_canonical_sha256=AUTHORIZATION_SHA256,
            expected_authorization_raw_sha256=authorization_raw_sha256,
            expected_authorization_signature_raw_sha256=(
                authorization_signature_raw_sha256),
            authorization_approved_at_utc="2026-08-29T12:00:00Z",
            authorization_valid_until_utc="2026-08-30T12:00:00Z",
            expected_result_store_path=build_inputs["result_store_path"],
            expected_analysis_pins_path=build_inputs["analysis_pins_path"],
            expected_context_blocklist_path=build_inputs["context_blocklist_path"],
            expected_manifest_output_paths=expected_manifest_output_paths,
            expected_checker_model=finalization.DEFAULT_CHECKER_MODEL,
            expected_oracle_model="fixture oracle",
            prior_reconciled_usd=PRIOR_RECONCILED_USD,
            stage_cap_usd=STAGE_CAP_USD,
        )

    assert validate_bound() == record
    with pytest.raises(
        finalization.MainFinalizationError, match="authorization bytes differ",
    ):
        validate_bound(authorization_raw_sha256="e" * 64)
    with pytest.raises(
        finalization.MainFinalizationError,
        match="authorization signature bytes differ",
    ):
        validate_bound(authorization_signature_raw_sha256="f" * 64)
    incomplete_output_paths = dict(manifest_output_paths)
    incomplete_output_paths.pop("completion")
    with pytest.raises(
        finalization.MainFinalizationError,
        match="expected manifest output path fields drifted",
    ):
        validate_bound(expected_manifest_output_paths=incomplete_output_paths)
    for artifact_label in ("usage_ledger", "request_journal"):
        shadow = copy.deepcopy(record)
        shadow["artifact_hashes"][artifact_label]["path"] = (
            tmp_path / f"shadow-{artifact_label}.jsonl").resolve().as_posix()
        with pytest.raises(
            finalization.MainFinalizationError,
            match=(
                rf"finalization artifact {artifact_label} does not equal the signed "
                "manifest output path"
            ),
        ):
            validate_bound(candidate=shadow)

    output = tmp_path / "finalization.json"
    finalization.write_finalization_admission(output, record)
    first = output.read_bytes()
    with pytest.raises(finalization.MainFinalizationError, match="immutable"):
        finalization.write_finalization_admission(output, record)
    assert output.read_bytes() == first


@pytest.mark.parametrize(
    "failure", ["fabricated", "cross_run", "dry_run", "oracle_model"])
def test_finalization_rejects_unproven_judgment_results(
    tmp_path, inventory, failure,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    cell = dict(inventory.judgment_cells[0])
    if failure == "fabricated":
        replacement = {
            "cell_key": cell["cell_key"],
            "status": "complete",
            "dry_run": False,
        }
        expected = "planned cell"
    else:
        replacement = _judgment_result(
            cell,
            "VERDICT: Position B\nCONFIDENCE: 4\nREASONING: foreign run",
            dry_run=failure == "dry_run",
        )
        if failure == "oracle_model":
            replacement["oracle_model"] = "foreign oracle"
            expected = "another oracle model"
        else:
            expected = "dry-run" if failure == "dry_run" else "provider response evidence"
    _rewrite_result_payload(
        Path(build_inputs["result_store_path"]), str(cell["cell_key"]), replacement)

    with pytest.raises(finalization.MainFinalizationError, match=expected):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:00Z")


@pytest.mark.parametrize("mutation", ["strict_verdict", "judge_messages"])
def test_finalization_recomputes_outcome_and_verdict_request_semantics(
    tmp_path, inventory, mutation,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    cell = dict(inventory.judgment_cells[0])
    result = _judgment_result(
        cell, "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: provider row")
    if mutation == "strict_verdict":
        result["verdict_strict"] = parse_both(
            "VERDICT: Position B\nCONFIDENCE: 4\nREASONING: replacement"
        )["strict"]
        expected = "strict verdict was not recomputed"
    else:
        result["judge_messages"][1]["content"] = "mutated verdict request"
        expected = "verdict request fingerprint drifted"
    _rewrite_result_payload(
        Path(build_inputs["result_store_path"]), str(cell["cell_key"]), result)

    with pytest.raises(finalization.MainFinalizationError, match=expected):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:30Z")


def test_finalization_admits_done_retry_after_first_query_rejection(
    tmp_path, inventory,
):
    cell = next(
        dict(candidate) for candidate in inventory.judgment_cells
        if candidate["query_budget"] == 1
    )
    build_inputs = _complete_finalization_inputs(
        tmp_path, inventory, observed_cell=cell)
    result_rows = finalization.load_result_rows(
        Path(build_inputs["result_store_path"]))
    result = dict(next(
        row["result"] for row in result_rows
        if row["cell_key"] == cell["cell_key"]
    ))
    transcript = next(
        row["result"] for row in result_rows
        if row["cell_key"] == cell["dependency_keys"][0]
    )
    candidate_a, candidate_b = finalization._candidate_pair(cell, transcript)
    raw_query = "Is the fixture fact explicitly stated?"
    reviewer_payload = payload_hash(raw_query, candidate_a, candidate_b)
    reviewer_raw = (
        "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: focused retry fixture")
    DualGateDecisionStore(
        build_inputs["artifact_paths"]["review_decisions"]
    ).commit(
        reviewer_payload,
        "ALLOW",
        "Allowed",
        "focused retry fixture",
        reviewer_raw,
        "parsed",
    )
    result["gate_events"] = [{
        "sequence": 1,
        "raw_query": raw_query,
        "candidate_a": candidate_a,
        "candidate_b": candidate_b,
        "slot": 1,
        "attempt": 1,
        "mechanical_reasons": [],
        "checker_raw_output": "reject",
        "checker_decision": "reject",
        "checker_reasons": [],
        "final_decision": "reject",
        "decision_source": "checker",
        "slot_consumed": False,
        "oracle_eligible": False,
        "halted": False,
        "halt_reason": None,
        "checker_error": None,
        "exhausted": False,
        "reviewer_payload_sha256": reviewer_payload,
        "reviewer_label": "ALLOW",
        "reviewer_clause": "Allowed",
        "reviewer_status": "parsed",
        "action": "retry",
    }]
    result["checker_false_allow_intercepts"] = []
    _rewrite_result_payload(
        Path(build_inputs["result_store_path"]), str(cell["cell_key"]), result)

    ledger_path = Path(build_inputs["usage_ledger_path"])
    journal_path = Path(build_inputs["request_journal_path"])
    _truncate_ledger_to_genesis(ledger_path)
    journal_path.write_text("", encoding="utf-8")
    _append_provider_success(
        ledger_path=ledger_path,
        journal_path=journal_path,
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response=raw_query,
        call_role=finalization.JUDGE_QUERY_ROLE,
        attempt_suffix="query-1",
        query_index=0,
        query_attempt=1,
    )
    _append_provider_success(
        ledger_path=ledger_path,
        journal_path=journal_path,
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response="reject",
        call_role=finalization.QUERY_CHECKER_ROLE,
        attempt_suffix="checker-1",
        slot=1,
        query_attempt=1,
        model=finalization.DEFAULT_CHECKER_MODEL,
    )
    _append_provider_success(
        ledger_path=ledger_path,
        journal_path=journal_path,
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response="DONE",
        call_role=finalization.JUDGE_QUERY_ROLE,
        attempt_suffix="query-2-done",
        query_index=0,
        query_attempt=2,
    )
    _append_provider_success(
        ledger_path=ledger_path,
        journal_path=journal_path,
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response=str(result["raw_verdict_text"]),
        request_sha256=request_fingerprint(
            messages=result["judge_messages"][:-1],
            model=str(cell["judge_model"]),
            temperature=finalization.MAIN_VERDICT_TEMPERATURE,
            seed=int(result["seed"]) + 99_999,
            max_tokens=finalization.MAIN_VERDICT_MAX_TOKENS,
        ),
        seed=int(result["seed"]) + 99_999,
    )

    record = finalization.build_finalization_admission(
        **build_inputs, recorded_at_utc="2026-08-29T13:01:40Z")
    assert record["status"] == finalization.FINALIZATION_STATUS
    assert record["provider_provenance"]["journaled_call_count"] == 4
    assert record["provider_provenance"]["settled_success_call_count"] == 4


def test_finalization_rejects_done_attempt_two_without_first_query_retry(
    tmp_path, inventory,
):
    cell = next(
        dict(candidate) for candidate in inventory.judgment_cells
        if candidate["query_budget"] == 1
    )
    build_inputs = _complete_finalization_inputs(
        tmp_path, inventory, observed_cell=cell)
    _append_provider_success(
        ledger_path=Path(build_inputs["usage_ledger_path"]),
        journal_path=Path(build_inputs["request_journal_path"]),
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response="DONE",
        call_role=finalization.JUDGE_QUERY_ROLE,
        attempt_suffix="orphan-query-2-done",
        query_index=0,
        query_attempt=2,
    )

    with pytest.raises(
        finalization.MainFinalizationError, match="provider-call schedule drifted",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:41Z")


def test_budget_zero_result_rejects_an_extra_provider_call(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    cell = dict(inventory.judgment_cells[0])
    assert cell["query_budget"] == 0
    _append_provider_success(
        ledger_path=Path(build_inputs["usage_ledger_path"]),
        journal_path=Path(build_inputs["request_journal_path"]),
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response="unexpected query",
        call_role=finalization.JUDGE_QUERY_ROLE,
        attempt_suffix="extra-b0-query",
    )
    with pytest.raises(finalization.MainFinalizationError, match="provider-call schedule"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:45Z")


def test_finalization_rejects_a_foreign_reviewer_decision_store(
    tmp_path, inventory,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    store = DualGateDecisionStore(build_inputs["artifact_paths"]["review_decisions"])
    raw = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: fixture"
    store.commit("f" * 64, "ALLOW", "Allowed", "fixture", raw, "parsed")
    with pytest.raises(
        finalization.MainFinalizationError,
        match="does not exactly cover",
    ):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:01:50Z")


def test_finalization_rejects_foreign_run_journal_identity(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    _rewrite_journal_for_foreign_identity(
        Path(build_inputs["request_journal_path"]))
    with pytest.raises(finalization.MainFinalizationError, match="identity or hash chain"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:02:00Z")


@pytest.mark.parametrize("missing", ["journal", "ledger"])
def test_finalization_rejects_result_without_verdict_journal_and_ledger_join(
    tmp_path, inventory, missing,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    if missing == "journal":
        Path(build_inputs["request_journal_path"]).write_text("", encoding="utf-8")
    else:
        _truncate_ledger_to_genesis(Path(build_inputs["usage_ledger_path"]))
    with pytest.raises(finalization.MainFinalizationError, match="not empty"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:03:00Z")


def test_finalization_rejects_exact_current_spend_over_stage_cap(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    build_inputs["prior_reconciled_usd"] = "0"
    build_inputs["stage_cap_usd"] = "0.000014"
    with pytest.raises(finalization.MainFinalizationError, match="exceeds the stage cap"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:04:00Z")
    build_inputs["stage_cap_usd"] = 60.0
    with pytest.raises(finalization.MainFinalizationError, match="Decimal string"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:04:01Z")


@pytest.mark.parametrize("target", ["context", "unplanned"])
def test_finalization_rejects_provider_and_journal_calls_outside_observed_plan(
    tmp_path, inventory, target,
):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    if target == "context":
        cell = dict(inventory.judgment_cells[1])
        expected = "context-ineligible"
    else:
        cell = {
            **dict(inventory.judgment_cells[0]),
            "cell_key": "not-in-the-main-inventory",
        }
        expected = "unplanned"
    _append_provider_success(
        ledger_path=Path(build_inputs["usage_ledger_path"]),
        journal_path=Path(build_inputs["request_journal_path"]),
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=cell,
        response="foreign call",
        call_role=finalization.JUDGE_QUERY_ROLE,
        attempt_suffix=target,
    )
    with pytest.raises(finalization.MainFinalizationError, match=expected):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:05:00Z")


def test_finalization_rejects_forbidden_main_call_role(tmp_path, inventory):
    build_inputs = _complete_finalization_inputs(tmp_path, inventory)
    _append_provider_success(
        ledger_path=Path(build_inputs["usage_ledger_path"]),
        journal_path=Path(build_inputs["request_journal_path"]),
        journal_identity=str(build_inputs["journal_execution_identity"]),
        cell=dict(inventory.judgment_cells[0]),
        response="legacy batch verdict",
        call_role="batch_verdict",
        attempt_suffix="forbidden-role",
    )
    with pytest.raises(finalization.MainFinalizationError, match="forbidden main call role"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:06:00Z")


def test_finalization_rejects_nonempty_reconciliation_and_artifact_drift(
    tmp_path, inventory,
):
    ledger_path = tmp_path / "usage.jsonl"
    api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    journal_path = tmp_path / "journal.jsonl"
    journal = RequestJournal(journal_path, execution_identity=JOURNAL_IDENTITY)
    metadata = {"cell_key": "cell", "call_role": "judge_verdict"}
    journal.put(journal_key(metadata), "e" * 64, "response")
    with pytest.raises(finalization.MainFinalizationError, match="not empty"):
        finalization._derive_clean_reconciliation(
            usage_ledger_path=ledger_path,
            request_journal_path=journal_path,
            journal_execution_identity=JOURNAL_IDENTITY,
        )

    build_inputs = _complete_finalization_inputs(tmp_path / "complete", inventory)
    cell = dict(inventory.judgment_cells[0])
    dirty_metadata = {
        "cell_key": cell["cell_key"],
        "call_role": finalization.JUDGE_QUERY_ROLE,
        "condition": cell["condition"],
    }
    dirty_journal = RequestJournal(
        build_inputs["request_journal_path"],
        execution_identity=str(build_inputs["journal_execution_identity"]),
    )
    dirty_journal.put(journal_key(dirty_metadata), "d" * 64, "unsettled response")
    with pytest.raises(finalization.MainFinalizationError, match="not empty"):
        finalization.build_finalization_admission(
            **build_inputs, recorded_at_utc="2026-08-29T13:07:00Z")

    with pytest.raises(finalization.MainFinalizationError, match="fields drifted"):
        finalization._artifact_hashes({
            **build_inputs["artifact_paths"],
            "unexpected": build_inputs["result_store_path"],
        })
