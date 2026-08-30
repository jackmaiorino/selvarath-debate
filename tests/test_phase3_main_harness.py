"""Isolated offline main-path harness tests for Phase 3."""
from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_main_harness, phase3_main_runner, phase3_plan
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_execution import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
HARNESS_SEED = 20260829


def _inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    protocol = phase3_plan.load_protocol(ROOT / "rejudge" / "phase3_protocol_v3_r6.json")
    prompt_bundle = json.loads(
        (ROOT / "rejudge" / "phase2_prompt_bundle.json").read_text(encoding="utf-8"))
    role_limits = json.loads(
        (ROOT / "rejudge" / "phase3_v3_role_limits_r10_2026-08-28.json").read_text(
            encoding="utf-8"))
    return protocol, prompt_bundle, role_limits


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_compact_main_material(
    root: Path, protocol: dict[str, Any],
) -> tuple[Path, Path]:
    inventory = phase3_main_runner.build_main_inventory(protocol, ROOT)
    transcripts = []
    for cell in inventory.transcript_cells:
        question_id = str(cell["question_id"])
        debater_model = str(cell["debater_model"])
        transcript_index = int(cell["transcript_index"])
        payload = {
            "question_id": question_id,
            "transcript_index": transcript_index,
            "world": "deterministic_harness_fixture_world",
            "question": f"Deterministic harness question {question_id}?",
            "correct_answer": "Position supported by the fixture.",
            "wrong_answer": "Position contradicted by the fixture.",
            "honest_first": True,
            "debate_transcript": [
                {"speaker": "honest", "text": "Deterministic honest argument.", "round": 1},
                {"speaker": "dishonest", "text": "Deterministic dishonest argument.",
                 "round": 1},
            ],
            "debater_model": debater_model,
            "protocol": "compact-main-harness-fixture",
            "temperature": 0.0,
            "seed": transcript_index,
            "harness_version": "test-fixture",
            "created_at": "2000-01-01T00:00:00+00:00",
            "dry_run": True,
            "cell_key": f"source:{cell['cell_key']}",
        }
        transcripts.append({
            "question_id": question_id,
            "world": payload["world"],
            "debater_model": debater_model,
            "transcript_index": transcript_index,
            "source_cell_key": f"source:{cell['cell_key']}",
            "source_event_hash": "0" * 64,
            "transcript_sha256": canonical_sha256(payload),
            "transcript_payload": payload,
        })
    assert len(transcripts) == phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT
    bundle = {
        "schema_version": "phase3_transcript_bundle_v1",
        "bundle": "compact deterministic Phase 3 main harness fixture",
        "expected_transcript_count": len(transcripts),
        "actual_transcript_count": len(transcripts),
        "transcripts": transcripts,
    }
    verification = {
        "schema_version": "phase3_transcript_verification_v1",
        "bundle_canonical_sha256": {"main_bundle": canonical_sha256(bundle)},
    }
    input_root = root / "inputs"
    input_root.mkdir(parents=True)
    bundle_path = (input_root / "main_bundle.json").resolve()
    verification_path = (input_root / "verification.json").resolve()
    bundle_path.write_text(json.dumps(bundle, sort_keys=True), encoding="utf-8")
    verification_path.write_text(json.dumps(verification, sort_keys=True), encoding="utf-8")
    return bundle_path, verification_path


@pytest.fixture(scope="module")
def completed_harness(tmp_path_factory):
    base = tmp_path_factory.mktemp("phase3-main-harness")
    protocol, prompt_bundle, role_limits = _inputs()
    bundle_path, verification_path = _write_compact_main_material(base, protocol)
    receipt_path = (base / "receipt.json").resolve()
    harness_root = (base / "harness").resolve()
    formal_root = (base / "formal").resolve()
    calls: dict[str, list[Any]] = {"preseed": [], "resolve": [], "run": []}

    monkeypatch = pytest.MonkeyPatch()
    original_preseed = phase3_main_harness.phase3_preseed_transcripts.preseed_main
    original_resolve = phase3_main_harness.phase3_runner.resolve_main_cells
    original_run = phase3_main_harness.phase2_canary_runner.run_canary

    def forbidden_provider(*_args, **_kwargs):
        raise AssertionError("harness must not construct a provider client")

    def forbidden_capability(*_args, **_kwargs):
        raise AssertionError("main harness must not execute the capability-anchor path")

    def preseed_spy(**kwargs):
        result = original_preseed(**kwargs)
        calls["preseed"].append(dict(result))
        return result

    def resolve_spy(cells, *, protocol, bundle):
        calls["resolve"].append({
            "count": len(cells),
            "transcripts": sum(
                cell["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND for cell in cells),
            "judgments": sum(
                cell["kind"] == phase3_plan.MAIN_JUDGMENT_KIND for cell in cells),
        })
        return original_resolve(cells, protocol=protocol, bundle=bundle)

    def run_spy(**kwargs):
        calls["run"].append({
            "cell_count": len(kwargs["cells"]),
            "max_workers": kwargs["max_workers"],
            "limit": kwargs["limit"],
            "pause_when_unlabeled": kwargs["pause_when_unlabeled"],
            "pending_payload_limit": kwargs["pending_payload_limit"],
            "transcript_generation_forbidden": kwargs["transcript_generation_forbidden"],
            "fatal_unknown_charge": kwargs["fatal_unknown_charge"],
            "client_type": type(kwargs["client"]).__name__,
            "inner_type": type(kwargs["client"].inner).__name__,
        })
        return original_run(**kwargs)

    monkeypatch.setattr(phase3_main_harness.api_client, "RejudgeClient", forbidden_provider)
    monkeypatch.setattr(
        phase3_main_harness.phase3_runner, "run_capability_cells", forbidden_capability)
    monkeypatch.setattr(
        phase3_main_harness.phase3_preseed_transcripts, "preseed_main", preseed_spy)
    monkeypatch.setattr(
        phase3_main_harness.phase3_runner, "resolve_main_cells", resolve_spy)
    monkeypatch.setattr(
        phase3_main_harness.phase2_canary_runner, "run_canary", run_spy)
    try:
        result = phase3_main_harness.run_isolated_harness(
            protocol=protocol,
            prompt_bundle=prompt_bundle,
            role_limits=role_limits,
            project_root=ROOT,
            harness_root=harness_root,
            receipt_path=receipt_path,
            harness_seed=HARNESS_SEED,
            formal_artifact_root=formal_root,
            main_transcript_bundle_path=bundle_path,
            transcript_verification_path=verification_path,
        )
    finally:
        monkeypatch.undo()
    return {
        "base": base,
        "result": result,
        "receipt_path": receipt_path,
        "harness_root": harness_root,
        "formal_root": formal_root,
        "bundle_path": bundle_path,
        "verification_path": verification_path,
        "protocol": protocol,
        "prompt_bundle": prompt_bundle,
        "role_limits": role_limits,
        "calls": calls,
    }


def _clone_completed(completed_harness, tmp_path: Path) -> dict[str, Any]:
    receipt = deepcopy(completed_harness["result"])
    harness_root = (tmp_path / "harness").resolve()
    shutil.copytree(completed_harness["harness_root"], harness_root)
    input_root = tmp_path / "inputs"
    input_root.mkdir()
    bundle_path = (input_root / "main_bundle.json").resolve()
    verification_path = (input_root / "verification.json").resolve()
    shutil.copy2(completed_harness["bundle_path"], bundle_path)
    shutil.copy2(completed_harness["verification_path"], verification_path)
    receipt["transcript_inputs"]["main_bundle_path"] = bundle_path.as_posix()
    receipt["transcript_inputs"]["verification_path"] = verification_path.as_posix()
    for index, execution in enumerate(receipt["executions"], 1):
        root = harness_root / f"execution-{index}"
        execution["root"] = root.as_posix()
        execution["result_store_path"] = (root / "harness_results.jsonl").as_posix()
        execution["usage_ledger_path"] = (root / "harness_usage.jsonl").as_posix()
        execution["usage_ledger_state_path"] = (
            root / "harness_usage.jsonl.state.json").as_posix()
        execution["request_journal_path"] = (
            root / "harness_request_journal.jsonl").as_posix()
    return {
        "receipt": receipt,
        "harness_root": harness_root,
        "formal_root": (tmp_path / "formal").resolve(),
        "bundle_path": bundle_path,
        "verification_path": verification_path,
        "protocol": completed_harness["protocol"],
        "prompt_bundle": completed_harness["prompt_bundle"],
        "role_limits": completed_harness["role_limits"],
    }


def _validate(cloned: dict[str, Any]) -> dict[str, Any]:
    return phase3_main_harness.validate_harness_receipt(
        cloned["receipt"],
        protocol=cloned["protocol"],
        prompt_bundle=cloned["prompt_bundle"],
        role_limits=cloned["role_limits"],
        project_root=ROOT,
        formal_artifact_root=cloned["formal_root"],
        main_transcript_bundle_path=cloned["bundle_path"],
        transcript_verification_path=cloned["verification_path"],
    )


def _refresh_result_receipt(receipt: dict[str, Any]) -> None:
    for execution in receipt["executions"]:
        path = Path(execution["result_store_path"])
        store = CellResultStore(path)
        judgment_key = receipt["selection"]["selected_main_judgment"]["cell_key"]
        execution["result_store_raw_sha256"] = _raw_sha256(path)
        execution["result_store_row_count"] = sum(
            bool(line.strip()) for line in path.read_bytes().splitlines())
        execution["result_store_cell_keys_canonical_sha256"] = canonical_sha256(
            sorted(store._results))
        execution["selected_judgment_result_canonical_sha256"] = canonical_sha256(
            store.get(judgment_key))
    first = receipt["executions"][0]["result_store_raw_sha256"]
    second = receipt["executions"][1]["result_store_raw_sha256"]
    assert first == second
    receipt["first_output_store_sha256"] = first
    receipt["rerun_output_store_sha256"] = second


def _relabel_selected_result(path: Path, selected_key: str) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    found = False
    previous = "genesis"
    for sequence, row in enumerate(rows):
        if row["cell_key"] == selected_key:
            row["result"]["condition"] = "relabelled_b0"
            found = True
        row["sequence"] = sequence
        row["prev_event_hash"] = previous
        row["event_hash"] = CellResultStore._row_hash(row)
        previous = row["event_hash"]
    assert found
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8")


def test_two_executions_use_the_real_main_path_and_are_bit_identical(completed_harness):
    result = completed_harness["result"]
    selection = result["selection"]
    selected = selection["selected_main_judgment"]
    dependency = selection["selected_transcript_dependency"]
    assert result["schema_version"] == phase3_main_harness.SCHEMA_VERSION
    assert result["status"] == "bit_identical_pass"
    assert result["first_output_store_sha256"] == result["rerun_output_store_sha256"]
    assert result["result_contract"] == {
        "transcript_count": 492,
        "judgment_count": 1,
        "row_count": 493,
        "cell_keys_canonical_sha256": result["result_contract"][
            "cell_keys_canonical_sha256"],
    }
    assert selected["kind"] == phase3_plan.MAIN_JUDGMENT_KIND
    assert selected["condition"] == "b0"
    assert selected["query_budget"] == 0
    assert selected["dependency_keys"] == [dependency["cell_key"]]
    assert dependency["kind"] == phase3_plan.MAIN_TRANSCRIPT_KIND
    assert result["transcript_inputs"]["main_transcript_count"] == 492
    assert json.loads(completed_harness["receipt_path"].read_text(encoding="utf-8")) == result

    executions = result["executions"]
    assert executions[0]["execution_identity"] != executions[1]["execution_identity"]
    assert executions[0]["usage_ledger_id"] != executions[1]["usage_ledger_id"]
    assert Path(executions[0]["result_store_path"]).read_bytes() == Path(
        executions[1]["result_store_path"]).read_bytes()
    for execution in executions:
        store = CellResultStore(execution["result_store_path"])
        assert len(store._results) == 493
        assert execution["result_store_row_count"] == 493
        call = execution["journal_call"]
        assert call["cell_key"] == selected["cell_key"]
        assert call["call_role"] == "judge_verdict"
        assert call["model"] == selected["judge_model"]
        assert call["kind"] == "verdict"
        assert call["requested_max_tokens"] == 512
        assert call["effective_max_tokens"] == completed_harness["role_limits"][
            "model_role_limits"][selected["judge_model"]]["judge_verdict"][
                "effective_request_max_tokens"]
        decisions = Path(execution["root"]) / "harness_decisions.jsonl"
        assert not decisions.exists() or decisions.read_bytes() == b""

    assert completed_harness["calls"]["preseed"] == [
        {"target_store_path": str(Path(executions[0]["result_store_path"])),
         "main_bundle_count": 492, "written": 492, "skipped": 0},
        {"target_store_path": str(Path(executions[1]["result_store_path"])),
         "main_bundle_count": 492, "written": 492, "skipped": 0},
    ]
    assert completed_harness["calls"]["resolve"] == [
        {"count": 493, "transcripts": 492, "judgments": 1},
        {"count": 493, "transcripts": 492, "judgments": 1},
    ]
    assert completed_harness["calls"]["run"] == [
        {"cell_count": 493, "max_workers": 1, "limit": 1,
         "pause_when_unlabeled": True, "pending_payload_limit": 64,
         "transcript_generation_forbidden": True, "fatal_unknown_charge": True,
         "client_type": "JournalingClient", "inner_type": "RoleLimitResolvingClient"},
        {"cell_count": 493, "max_workers": 1, "limit": 1,
         "pause_when_unlabeled": True, "pending_payload_limit": 64,
         "transcript_generation_forbidden": True, "fatal_unknown_charge": True,
         "client_type": "JournalingClient", "inner_type": "RoleLimitResolvingClient"},
    ]


def test_receipt_recomputes_selection_dependency_and_execution_identities(
    completed_harness, tmp_path,
):
    cloned = _clone_completed(completed_harness, tmp_path)
    validated = _validate(cloned)
    selection = cloned["receipt"]["selection"]
    assert validated["selected_main_judgment_cell_key"] == selection[
        "selected_main_judgment"]["cell_key"]
    assert validated["selected_transcript_dependency_cell_key"] == selection[
        "selected_transcript_dependency"]["cell_key"]
    assert validated["result_store_row_count"] == 493

    changed = deepcopy(cloned["receipt"])
    changed["selection"]["selected_main_judgment"]["condition"] = "sequential_b1"
    cloned["receipt"] = changed
    with pytest.raises(phase3_main_harness.MainHarnessError, match="judgment or dependency"):
        _validate(cloned)


@pytest.mark.parametrize("field,value", [
    ("call_role", "batch_verdict"),
    ("model", "forged/model"),
])
def test_receipt_refuses_journal_call_relabel(
    completed_harness, tmp_path, field, value,
):
    cloned = _clone_completed(completed_harness, tmp_path)
    cloned["receipt"]["executions"][0]["journal_call"][field] = value
    with pytest.raises(
        phase3_main_harness.MainHarnessError,
        match="journal call role, cell, or model",
    ):
        _validate(cloned)


def test_receipt_refuses_validly_rechained_result_relabel(completed_harness, tmp_path):
    cloned = _clone_completed(completed_harness, tmp_path)
    selected_key = cloned["receipt"]["selection"]["selected_main_judgment"]["cell_key"]
    for execution in cloned["receipt"]["executions"]:
        _relabel_selected_result(Path(execution["result_store_path"]), selected_key)
    _refresh_result_receipt(cloned["receipt"])
    with pytest.raises(phase3_main_harness.MainHarnessError, match="result condition drifted"):
        _validate(cloned)


def test_receipt_refuses_forged_extra_coverage_even_with_matching_store_hashes(
    completed_harness, tmp_path,
):
    cloned = _clone_completed(completed_harness, tmp_path)
    for execution in cloned["receipt"]["executions"]:
        CellResultStore(execution["result_store_path"]).record(
            "forged-extra-cell", {"cell_key": "forged-extra-cell", "condition": "b0"})
    _refresh_result_receipt(cloned["receipt"])
    with pytest.raises(phase3_main_harness.MainHarnessError, match="exact cell coverage"):
        _validate(cloned)


def test_receipt_refuses_result_bundle_and_verification_tamper(completed_harness, tmp_path):
    cloned = _clone_completed(completed_harness, tmp_path)
    result_path = Path(cloned["receipt"]["executions"][0]["result_store_path"])
    with result_path.open("ab") as handle:
        handle.write(b"{}\n")
    with pytest.raises(phase3_main_harness.MainHarnessError, match="artifact hash drifted"):
        _validate(cloned)

    cloned = _clone_completed(completed_harness, tmp_path / "bundle")
    cloned["bundle_path"].write_bytes(cloned["bundle_path"].read_bytes() + b" \n")
    with pytest.raises(
        phase3_main_harness.MainHarnessError, match="bundle or verification binding drifted"):
        _validate(cloned)

    cloned = _clone_completed(completed_harness, tmp_path / "verification")
    cloned["verification_path"].write_bytes(
        cloned["verification_path"].read_bytes() + b" \n")
    with pytest.raises(
        phase3_main_harness.MainHarnessError, match="bundle or verification binding drifted"):
        _validate(cloned)


def test_harness_refuses_formal_overlap_and_reuse(completed_harness, tmp_path):
    protocol = completed_harness["protocol"]
    prompt_bundle = completed_harness["prompt_bundle"]
    role_limits = completed_harness["role_limits"]
    formal = (tmp_path / "formal").resolve()
    with pytest.raises(phase3_main_harness.MainHarnessError, match="outside the formal"):
        phase3_main_harness.run_isolated_harness(
            protocol=protocol,
            prompt_bundle=prompt_bundle,
            role_limits=role_limits,
            project_root=ROOT,
            harness_root=(formal / "harness").resolve(),
            receipt_path=(tmp_path / "receipt.json").resolve(),
            harness_seed=1,
            formal_artifact_root=formal,
            main_transcript_bundle_path=completed_harness["bundle_path"],
            transcript_verification_path=completed_harness["verification_path"],
        )

    with pytest.raises(phase3_main_harness.MainHarnessError, match="not fresh"):
        phase3_main_harness.run_isolated_harness(
            protocol=protocol,
            prompt_bundle=prompt_bundle,
            role_limits=role_limits,
            project_root=ROOT,
            harness_root=completed_harness["harness_root"],
            receipt_path=(tmp_path / "unused.json").resolve(),
            harness_seed=HARNESS_SEED,
            formal_artifact_root=formal,
            main_transcript_bundle_path=completed_harness["bundle_path"],
            transcript_verification_path=completed_harness["verification_path"],
        )


def test_receipt_refuses_unknown_fields(completed_harness, tmp_path):
    cloned = _clone_completed(completed_harness, tmp_path)
    cloned["receipt"]["unexpected"] = True
    with pytest.raises(phase3_main_harness.MainHarnessError, match="fields drifted"):
        _validate(cloned)
