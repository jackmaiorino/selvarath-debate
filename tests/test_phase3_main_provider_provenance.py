"""Focused tests for deterministic Phase 3 main provider replay."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rejudge import api_client, judge_loop, phase2_canary_execute, phase3_main_live
from rejudge import phase3_main_provider_provenance as provenance
from rejudge import phase3_main_runner, phase3_runner
from rejudge.phase2_canary_live import RoleLimitResolvingClient
from rejudge.phase2_call_cache import request_fingerprint
from rejudge.phase2_canary_execute import CellContext, execute_cell
from rejudge.phase2_canary_gate import CanaryCellHalted
from rejudge.phase2_dual_gate import DualGateDecisionStore, payload_hash
from rejudge.request_journal import (
    JOURNAL_REQUEST_SHA256_FIELD,
    JournalingClient,
    RequestJournal,
    journal_key,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads(
    (REPO_ROOT / "rejudge" / "phase3_protocol_v3_r6.json").read_text(encoding="utf-8"))
PROMPT_BUNDLE = json.loads(
    (REPO_ROOT / "rejudge" / "phase2_prompt_bundle.json").read_text(encoding="utf-8"))
ROLE_LIMITS = json.loads(
    (REPO_ROOT / "rejudge" / "phase3_v3_role_limits_r10_2026-08-28.json").read_text(
        encoding="utf-8"))
RAW_VERDICT = "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: replay fixture"
RAW_QUERY = "CLAIM: the replay fixture contains one checkable fact"
RAW_REVIEW = "LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: one atomic factual claim"
AUTHORIZATION_APPROVED_AT_UTC = "2020-01-01T00:00:00+00:00"
AUTHORIZATION_VALID_UNTIL_UTC = "2099-01-01T00:00:00+00:00"
FINALIZATION_RECORDED_AT_UTC = "2100-01-01T00:00:00+00:00"


def _fixture_ledger_ts(index: int) -> str:
    return f"2026-08-30T12:00:00.{index:06d}+00:00"


class _CaptureClient:
    dry_run = False

    def __init__(
        self, *, checker_response: str = "allow", query_responses: tuple[str, ...] = (RAW_QUERY,),
    ) -> None:
        self.calls: list[dict] = []
        self.checker_response = checker_response
        self.query_responses = list(query_responses)

    def complete(
        self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
        request_metadata=None,
    ) -> str:
        self.calls.append({
            "messages": copy.deepcopy(messages),
            "model": model,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_tokens,
            "kind": kind,
            "request_metadata": dict(request_metadata or {}),
        })
        role = self.calls[-1]["request_metadata"].get("call_role")
        if role == "judge_query":
            response = self.query_responses.pop(0)
        else:
            response = {
                "query_checker": self.checker_response,
                "oracle_verification": "YES",
                "judge_verdict": RAW_VERDICT,
            }[role]
        self.calls[-1]["response"] = response
        return response


class _ZeroNetworkSdk:
    """Together-shaped SDK stub that records literal kwargs without network access."""

    def __init__(self, *, model: str, response: str) -> None:
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            @staticmethod
            def create(**kwargs):
                outer.calls.append(copy.deepcopy(kwargs))
                return SimpleNamespace(
                    usage=SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=4,
                    ),
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content=response),
                        finish_reason="stop",
                    )],
                    model=model,
                    id="zero-network-response",
                    system_fingerprint=None,
                )

        self.chat = SimpleNamespace(completions=_Completions())


def _unexpected_reviewer(_query: str, _candidate_a: str, _candidate_b: str) -> str:
    raise AssertionError("budget-zero fixture must not consult a reviewer")


def _fixture(
    tmp_path: Path,
    *,
    judge_model: str,
    query_budget: int = 0,
    checker_response: str = "allow",
    query_responses: tuple[str, ...] = (RAW_QUERY,),
) -> dict:
    inventory = phase3_main_runner.build_canonical_main_inventory(REPO_ROOT)
    judgment = next(
        dict(cell) for cell in inventory.judgment_cells
        if cell["query_budget"] == query_budget and cell["judge_model"] == judge_model
    )
    dependency = str(judgment["dependency_keys"][0])
    transcript_cell = next(
        dict(cell) for cell in inventory.transcript_cells
        if cell["cell_key"] == dependency
    )
    transcript = {
        "cell_key": dependency,
        "question_id": transcript_cell["question_id"],
        "transcript_index": transcript_cell["transcript_index"],
        "debater_model": transcript_cell["debater_model"],
        "world": "replay-fixture-world",
        "question": "Which fixture position is correct?",
        "correct_answer": "fixture answer A",
        "wrong_answer": "fixture answer B",
        "debate_transcript": [],
        "dry_run": False,
    }
    decisions_path = tmp_path / "decisions.jsonl"
    decisions_path.write_text("", encoding="utf-8")
    client = _CaptureClient(
        checker_response=checker_response,
        query_responses=query_responses,
    )
    store = DualGateDecisionStore(decisions_path)
    context = CellContext(
        client=client,
        protocol=copy.deepcopy(PROTOCOL),
        bundle=copy.deepcopy(PROMPT_BUNDLE),
        decision_store=store,
        reviewer=_unexpected_reviewer,
        anchor_judge_model="",
        results={dependency: transcript},
        transcript_generation_forbidden=True,
        role_limits=copy.deepcopy(ROLE_LIMITS),
    )
    resolved = phase3_runner.resolve_main_cells(
        [transcript_cell, judgment],
        protocol=PROTOCOL,
        bundle=PROMPT_BUNDLE,
    )
    resolved_judgment = next(cell for cell in resolved if not cell.is_transcript)
    if query_budget:
        position_a_is_correct = phase2_canary_execute._polarity(resolved_judgment)
        candidate_a, candidate_b, _debate = judge_loop._format_transcript(
            transcript, position_a_is_correct)
        for raw_query in query_responses:
            if raw_query.strip() == "DONE":
                continue
            sha = payload_hash(raw_query, candidate_a, candidate_b)
            if store.get(sha) is None:
                store.commit(
                    sha,
                    "ALLOW",
                    "Allowed",
                    "one atomic factual claim",
                    RAW_REVIEW,
                    "parsed",
                )
    terminal = checker_response != "allow"
    record = None
    try:
        record = execute_cell(
            resolved_judgment,
            context,
            debater_model=resolved_judgment.debater_model,
            namespace=PROTOCOL["cell_key_namespace"],
        )
    except CanaryCellHalted as exc:
        assert terminal
        assert exc.reason == "checker_malformed"
    if terminal:
        assert record is None
    else:
        assert record is not None

    journal_rows = []
    ledger_events = []
    for call in client.calls:
        key = journal_key(call["request_metadata"])
        logical_sha = request_fingerprint(
            messages=call["messages"],
            model=call["model"],
            temperature=call["temperature"],
            seed=call["seed"],
            max_tokens=call["max_tokens"],
        )
        resolved_role = "oracle" if key.call_role == "oracle_verification" else key.call_role
        role_entry = ROLE_LIMITS["model_role_limits"][call["model"]][resolved_role]
        provider_kwargs = provenance.build_provider_request_kwargs(
            model=call["model"],
            messages=call["messages"],
            temperature=call["temperature"],
            max_tokens=role_entry["effective_request_max_tokens"],
            seed=call["seed"],
            streaming=call["model"] in ROLE_LIMITS["request_settings"][
                "streaming_pinned_models"],
            extra_request_fields=ROLE_LIMITS["request_settings"][
                "per_model_extra_fields"].get(call["model"]),
        )
        provider_sha = provenance.compute_request_fields_sha256(provider_kwargs)
        journal_rows.append({
            "cell_key": key.cell_key,
            "call_role": key.call_role,
            "slot": key.slot,
            "attempt": key.attempt,
            "request_sha256": logical_sha,
            "response": call["response"],
        })
        dispatch_at = _fixture_ledger_ts(len(ledger_events))
        metadata = {
            **call["request_metadata"],
            JOURNAL_REQUEST_SHA256_FIELD: logical_sha,
            api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD: dispatch_at,
        }
        attempt_id = f"provider-attempt-{len(ledger_events)}"
        common_event = {
            "attempt_id": attempt_id,
            "status": "success",
            "model": call["model"],
            "kind": call["kind"],
            "seed": call["seed"],
            "attempt": 0,
            "metadata": metadata,
            "estimated_tokens": 1,
            "reserved_prompt_tokens": 1,
            "reserved_completion_tokens": 0,
        }
        ledger_events.append({
            **common_event,
            "status": "reserved",
            "ts": dispatch_at,
        })
        ledger_events.append({
            **common_event,
            "ts": _fixture_ledger_ts(len(ledger_events)),
            "response_metadata": {
                "request_fields_sha256": provider_sha,
                "returned_model_id": call["model"],
            },
        })
    result_rows = [{"cell_key": dependency, "result": transcript}]
    if record is not None:
        result_rows.append({"cell_key": judgment["cell_key"], "result": record})
    return {
        "cells": [transcript_cell, judgment],
        "result_rows": result_rows,
        "terminal_cell_keys": [judgment["cell_key"]] if terminal else [],
        "context_ineligible_cell_keys": [],
        "protocol": PROTOCOL,
        "prompt_bundle": PROMPT_BUNDLE,
        "role_limits": ROLE_LIMITS,
        "authorization_approved_at_utc": AUTHORIZATION_APPROVED_AT_UTC,
        "authorization_valid_until_utc": AUTHORIZATION_VALID_UNTIL_UTC,
        "finalization_recorded_at_utc": FINALIZATION_RECORDED_AT_UTC,
        "review_decisions_path": decisions_path,
        "journal_rows": journal_rows,
        "ledger_events": ledger_events,
    }


@pytest.mark.parametrize("judge_model", [
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "Qwen/Qwen3.8-2.4T-A95B",
])
def test_exact_normal_execution_replay_joins_logical_and_provider_hashes(
    tmp_path: Path, judge_model: str,
) -> None:
    inputs = _fixture(tmp_path, judge_model=judge_model)

    result = provenance.verify_main_provider_replay(**inputs)

    assert result["status"] == "exact_normal_execution_replay"
    assert result["replayed_judgment_count"] == 1
    assert result["replayed_terminal_count"] == 0
    assert result["logical_request_count"] == 1
    assert result["provider_request_count"] == 1
    assert result["authorization_dispatch_status"] == (
        "all_logical_dispatches_within_authorization")
    assert result["latest_logical_dispatch_at_utc"] == (
        "2026-08-30T12:00:00+00:00")
    assert result["latest_provider_completion_at_utc"] == (
        "2026-08-30T12:00:00.000001+00:00")


@pytest.mark.parametrize("boundary", ["approved", "valid_until"])
def test_replay_accepts_dispatch_at_each_authorization_boundary_and_completion_after(
    tmp_path: Path, boundary: str,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    dispatch_at = inputs["ledger_events"][0]["ts"]
    completion_at = inputs["ledger_events"][1]["ts"]
    if boundary == "approved":
        inputs["authorization_approved_at_utc"] = dispatch_at
        inputs["authorization_valid_until_utc"] = (
            "2026-08-30T13:00:00+00:00")
    else:
        inputs["authorization_approved_at_utc"] = (
            "2026-08-30T11:00:00+00:00")
        inputs["authorization_valid_until_utc"] = dispatch_at
    inputs["finalization_recorded_at_utc"] = completion_at

    result = provenance.verify_main_provider_replay(**inputs)

    assert result["latest_logical_dispatch_at_utc"] == (
        "2026-08-30T12:00:00+00:00")
    assert result["latest_provider_completion_at_utc"] == completion_at


def test_replay_rejects_empty_authorization_window(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    dispatch_at = inputs["ledger_events"][0]["metadata"][
        api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD]
    inputs["authorization_approved_at_utc"] = dispatch_at
    inputs["authorization_valid_until_utc"] = dispatch_at

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="authorization dispatch window is empty",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_requires_durable_logical_dispatch_authorization_timestamp(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    del inputs["ledger_events"][0]["metadata"][
        api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="logical_dispatch_authorized_at_utc.*timestamp",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_logical_dispatch_authorization_after_reservation(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    authorized_at = inputs["ledger_events"][1]["ts"]
    for event in inputs["ledger_events"][:2]:
        event["metadata"][
            api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD] = authorized_at

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="authorization timestamp is after its ledger reservation",
    ):
        provenance.verify_main_provider_replay(**inputs)


@pytest.mark.parametrize(
    ("approved_at", "valid_until"),
    [
        (
            "2026-08-30T12:00:00.000001+00:00",
            "2026-08-30T13:00:00+00:00",
        ),
        (
            "2026-08-30T11:00:00+00:00",
            "2026-08-30T11:59:59.999999+00:00",
        ),
    ],
)
def test_replay_rejects_logical_dispatch_outside_authorization_window(
    tmp_path: Path, approved_at: str, valid_until: str,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    inputs["authorization_approved_at_utc"] = approved_at
    inputs["authorization_valid_until_utc"] = valid_until

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="dispatch began outside the signed authorization window",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_new_logical_dispatch_after_authorization_expiry(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
    )
    inputs["authorization_approved_at_utc"] = "2026-08-30T11:00:00+00:00"
    inputs["authorization_valid_until_utc"] = inputs["ledger_events"][0]["ts"]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="dispatch began outside the signed authorization window",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_provider_completion_after_finalization(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    inputs["finalization_recorded_at_utc"] = inputs["ledger_events"][0]["ts"]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="terminal timestamp is after finalization",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_provider_event_timestamp_reversal(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    inputs["ledger_events"][1]["ts"] = "2026-08-30T11:59:59.999999+00:00"

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="provider event timestamps are out of order",
    ):
        provenance.verify_main_provider_replay(**inputs)


@pytest.mark.parametrize("timestamp", ["not-a-timestamp", "2026-08-30T12:00:00"])
def test_replay_rejects_invalid_or_non_utc_provider_event_timestamp(
    tmp_path: Path, timestamp: str,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    inputs["ledger_events"][0]["ts"] = timestamp

    with pytest.raises(provenance.MainProviderProvenanceError, match="timestamp|UTC"):
        provenance.verify_main_provider_replay(**inputs)


def test_real_runtime_wrapper_layers_emit_hashes_accepted_by_replay(
    tmp_path: Path, monkeypatch,
) -> None:
    """Exercise the production wrapper order against a zero-network SDK stub."""
    judge_model = "Qwen/Qwen3.8-2.4T-A95B"
    inputs = _fixture(tmp_path, judge_model=judge_model)
    transcript = inputs["result_rows"][0]["result"]
    judgment = next(
        cell for cell in inputs["cells"] if cell["kind"].endswith("judgment"))
    resolved = phase3_runner.resolve_main_cells(
        [judgment], protocol=PROTOCOL, bundle=PROMPT_BUNDLE)

    sdk = _ZeroNetworkSdk(model=judge_model, response=RAW_VERDICT)
    ledger_path = tmp_path / "runtime-ledger.jsonl"
    raw_client = api_client.RejudgeClient(
        approved_cap_usd=100.0,
        _sdk_client=sdk,
        max_retries=0,
        usage_log_path=ledger_path,
        require_explicit_reasoning_max_tokens=True,
        model_context_limits={
            model: int(entry["context_length_tokens"])
            for model, entry in ROLE_LIMITS["context_ceilings"].items()
        },
        strict_context_mode=True,
        streaming_pinned_models=frozenset(
            ROLE_LIMITS["request_settings"]["streaming_pinned_models"]),
        reasoning_models=frozenset(ROLE_LIMITS["reasoning_models"]["model_ids"]),
        extra_request_fields=ROLE_LIMITS["request_settings"]["per_model_extra_fields"],
        require_returned_model_match=True,
    )
    prepared_sentinel = object()

    def authorize(prepared):
        assert prepared is prepared_sentinel
        return phase3_main_live.datetime.now(
            phase3_main_live.timezone.utc).isoformat()

    monkeypatch.setattr(
        phase3_main_live,
        "_authorize_provider_logical_dispatch",
        authorize,
    )
    authorized_client = phase3_main_live._AuthorizationDeadlineClient(
        prepared_sentinel, raw_client)
    resolving_client = RoleLimitResolvingClient(
        authorized_client, ROLE_LIMITS["model_role_limits"])
    journal_path = tmp_path / "runtime-journal.jsonl"
    journal_client = JournalingClient(
        resolving_client,
        RequestJournal(journal_path, execution_identity="zero-network-wrapper-test"),
    )
    assert isinstance(journal_client.inner, RoleLimitResolvingClient)
    assert isinstance(
        journal_client.inner.inner,
        phase3_main_live._AuthorizationDeadlineClient,
    )
    assert journal_client.inner.inner._inner is raw_client

    context = CellContext(
        client=journal_client,
        protocol=copy.deepcopy(PROTOCOL),
        bundle=copy.deepcopy(PROMPT_BUNDLE),
        decision_store=DualGateDecisionStore(inputs["review_decisions_path"]),
        reviewer=_unexpected_reviewer,
        anchor_judge_model="",
        results={transcript["cell_key"]: transcript},
        transcript_generation_forbidden=True,
        role_limits=copy.deepcopy(ROLE_LIMITS),
    )
    record = execute_cell(
        resolved[0],
        context,
        debater_model=resolved[0].debater_model,
        namespace=PROTOCOL["cell_key_namespace"],
    )

    journal_rows = [
        json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ledger_events = [
        json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(sdk.calls) == 1
    literal_provider_kwargs = sdk.calls[0]
    assert literal_provider_kwargs["max_tokens"] == 16384
    logical_sha = request_fingerprint(
        messages=literal_provider_kwargs["messages"],
        model=literal_provider_kwargs["model"],
        temperature=literal_provider_kwargs["temperature"],
        seed=literal_provider_kwargs["seed"],
        max_tokens=512,
    )
    provider_sha = provenance.compute_request_fields_sha256(literal_provider_kwargs)
    assert journal_rows[0]["request_sha256"] == logical_sha
    assert ledger_events[-1]["response_metadata"]["request_fields_sha256"] == provider_sha
    assert logical_sha != provider_sha

    inputs["result_rows"] = [
        {"cell_key": transcript["cell_key"], "result": transcript},
        {"cell_key": judgment["cell_key"], "result": record},
    ]
    inputs["journal_rows"] = journal_rows
    inputs["ledger_events"] = ledger_events
    result = provenance.verify_main_provider_replay(**inputs)

    assert result["status"] == "exact_normal_execution_replay"
    assert result["logical_request_count"] == 1
    assert result["provider_request_count"] == 1


def test_replay_rejects_coherently_rehashed_wrong_logical_request(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    wrong_sha = "f" * 64
    inputs["journal_rows"][0]["request_sha256"] = wrong_sha
    for event in inputs["ledger_events"][:2]:
        event["metadata"] = dict(event["metadata"])
        event["metadata"][JOURNAL_REQUEST_SHA256_FIELD] = wrong_sha

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="logical request fingerprint differs",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_post_role_limit_provider_hash_drift(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path, judge_model="Qwen/Qwen3.8-2.4T-A95B")
    inputs["ledger_events"][1]["response_metadata"]["request_fields_sha256"] = "e" * 64

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="post-role-limit provider request hash differs",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_recorded_result_message_drift(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    inputs["result_rows"][1]["result"] = copy.deepcopy(
        inputs["result_rows"][1]["result"])
    inputs["result_rows"][1]["result"]["judge_messages"][0]["content"] += " drift"

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="differs from normal execution replay",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_covers_query_checker_oracle_and_verdict_requests(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="Qwen/Qwen3.8-2.4T-A95B",
        query_budget=1,
    )

    result = provenance.verify_main_provider_replay(**inputs)

    assert result["logical_request_count"] == 4
    assert result["provider_request_count"] == 4
    assert [row["call_role"] for row in inputs["journal_rows"]] == [
        "judge_query",
        "query_checker",
        "oracle_verification",
        "judge_verdict",
    ]


def test_replay_covers_checker_malformed_terminal_prefix(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
        checker_response="ALLOW",
    )

    result = provenance.verify_main_provider_replay(**inputs)

    assert result["replayed_judgment_count"] == 0
    assert result["replayed_terminal_count"] == 1
    assert result["logical_request_count"] == 2


def test_replay_covers_checker_malformed_on_application_attempt_two(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
        checker_response="ALLOW",
        query_responses=("", RAW_QUERY),
    )

    result = provenance.verify_main_provider_replay(**inputs)

    assert result["replayed_judgment_count"] == 0
    assert result["replayed_terminal_count"] == 1
    assert [
        (row["call_role"], row["slot"], row["attempt"])
        for row in inputs["journal_rows"]
    ] == [
        ("judge_query", 0, 1),
        ("judge_query", 0, 2),
        ("query_checker", 1, 2),
    ]


def test_replay_covers_retry_then_done_attempt_two(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
        query_responses=("CLAIM: Position A is correct", "DONE"),
    )

    result = provenance.verify_main_provider_replay(**inputs)

    assert result["replayed_judgment_count"] == 1
    assert [
        (row["call_role"], row["slot"], row["attempt"])
        for row in inputs["journal_rows"]
    ] == [
        ("judge_query", 0, 1),
        ("judge_query", 0, 2),
        ("judge_verdict", 0, 1),
    ]


def test_replay_covers_no_charge_transport_negotiation_then_streamed_success(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    reserved = inputs["ledger_events"][0]
    success = inputs["ledger_events"][1]
    release = {
        **copy.deepcopy(reserved),
        "status": "released_no_charge",
    }
    retry_reservation = {
        **copy.deepcopy(reserved),
        "attempt_id": "provider-attempt-retry",
        "attempt": 1,
    }
    streamed_kwargs = provenance.build_provider_request_kwargs(
        model=success["model"],
        messages=_fixture_call_messages(inputs),
        temperature=PROTOCOL["decisions"]["execution_semantics"][
            "temperature_by_call_role"]["judge_verdict"],
        max_tokens=512,
        seed=success["seed"],
        streaming=True,
    )
    retry_success = {
        **copy.deepcopy(success),
        "attempt_id": "provider-attempt-retry",
        "attempt": 1,
        "response_metadata": {
            **success["response_metadata"],
            "request_fields_sha256": provenance.compute_request_fields_sha256(
                streamed_kwargs),
        },
    }
    inputs["ledger_events"] = [
        reserved, release, retry_reservation, retry_success,
    ]

    result = provenance.verify_main_provider_replay(**inputs)

    assert result["logical_request_count"] == 1
    assert result["provider_request_count"] == 2


def test_replay_requires_later_serial_calls_to_remain_streaming_after_negotiation(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
    )
    reserved = inputs["ledger_events"][0]
    success = inputs["ledger_events"][1]
    model = str(reserved["model"])
    call_role = str(reserved["metadata"]["call_role"])
    release = {**copy.deepcopy(reserved), "status": "released_no_charge"}
    retry_reservation = {
        **copy.deepcopy(reserved),
        "attempt_id": "provider-attempt-streaming-retry",
        "attempt": 1,
    }
    resolved_role = "oracle" if call_role == "oracle_verification" else call_role
    role_entry = ROLE_LIMITS["model_role_limits"][model][resolved_role]
    streamed_kwargs = provenance.build_provider_request_kwargs(
        model=model,
        messages=_fixture_call_messages(inputs),
        temperature=PROTOCOL["decisions"]["execution_semantics"][
            "temperature_by_call_role"][call_role],
        max_tokens=role_entry["effective_request_max_tokens"],
        seed=success["seed"],
        streaming=True,
        extra_request_fields=ROLE_LIMITS["request_settings"][
            "per_model_extra_fields"].get(model),
    )
    retry_success = {
        **copy.deepcopy(success),
        "attempt_id": "provider-attempt-streaming-retry",
        "attempt": 1,
        "response_metadata": {
            **success["response_metadata"],
            "request_fields_sha256": provenance.compute_request_fields_sha256(
                streamed_kwargs),
        },
    }
    inputs["ledger_events"] = [
        reserved,
        release,
        retry_reservation,
        retry_success,
        *inputs["ledger_events"][2:],
    ]
    assert any(
        event.get("status") == "reserved" and event.get("model") == model
        for event in inputs["ledger_events"][4:]
    )

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="normal execution unexpectedly halted.*checker_outage",
    ):
        provenance.verify_main_provider_replay(**inputs)


def _fixture_call_messages(inputs: dict) -> list[dict]:
    transcript = inputs["result_rows"][0]["result"]
    judgment = next(cell for cell in inputs["cells"] if cell["kind"].endswith("judgment"))
    resolved = phase3_runner.resolve_main_cells(
        [judgment], protocol=PROTOCOL, bundle=PROMPT_BUNDLE)
    capture = _CaptureClient()
    context = CellContext(
        client=capture,
        protocol=copy.deepcopy(PROTOCOL),
        bundle=copy.deepcopy(PROMPT_BUNDLE),
        decision_store=DualGateDecisionStore(inputs["review_decisions_path"]),
        reviewer=_unexpected_reviewer,
        anchor_judge_model="",
        results={transcript["cell_key"]: transcript},
        transcript_generation_forbidden=True,
        role_limits=copy.deepcopy(ROLE_LIMITS),
    )
    execute_cell(
        resolved[0],
        context,
        debater_model=resolved[0].debater_model,
        namespace=PROTOCOL["cell_key_namespace"],
    )
    return capture.calls[0]["messages"]


def _first_call_negotiation_events(inputs: dict) -> list[dict]:
    reserved = inputs["ledger_events"][0]
    success = inputs["ledger_events"][1]
    model = str(reserved["model"])
    call_role = str(reserved["metadata"]["call_role"])
    resolved_role = "oracle" if call_role == "oracle_verification" else call_role
    role_entry = ROLE_LIMITS["model_role_limits"][model][resolved_role]
    streamed_kwargs = provenance.build_provider_request_kwargs(
        model=model,
        messages=_fixture_call_messages(inputs),
        temperature=PROTOCOL["decisions"]["execution_semantics"][
            "temperature_by_call_role"][call_role],
        max_tokens=role_entry["effective_request_max_tokens"],
        seed=success["seed"],
        streaming=True,
        extra_request_fields=ROLE_LIMITS["request_settings"][
            "per_model_extra_fields"].get(model),
    )
    release = {**copy.deepcopy(reserved), "status": "released_no_charge"}
    retry_reservation = {
        **copy.deepcopy(reserved),
        "attempt_id": "provider-attempt-overlap-retry",
        "attempt": 1,
    }
    retry_success = {
        **copy.deepcopy(success),
        "attempt_id": "provider-attempt-overlap-retry",
        "attempt": 1,
        "response_metadata": {
            **success["response_metadata"],
            "request_fields_sha256": provenance.compute_request_fields_sha256(
                streamed_kwargs),
        },
    }
    return [reserved, release, retry_reservation, retry_success]


def test_replay_accepts_only_immediate_same_logical_streaming_retry_after_expiry(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    events = _first_call_negotiation_events(inputs)
    for index, event in enumerate(events):
        event["ts"] = _fixture_ledger_ts(index)
    inputs["ledger_events"] = events
    inputs["authorization_approved_at_utc"] = "2026-08-30T11:00:00+00:00"
    inputs["authorization_valid_until_utc"] = events[0]["ts"]
    inputs["finalization_recorded_at_utc"] = events[-1]["ts"]

    result = provenance.verify_main_provider_replay(**inputs)

    assert result["provider_request_count"] == 2
    assert result["latest_logical_dispatch_at_utc"] == (
        "2026-08-30T12:00:00+00:00")
    assert result["latest_provider_completion_at_utc"] == events[-1]["ts"]


def test_replay_requires_streaming_retry_to_inherit_logical_dispatch_authorization(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    events = _first_call_negotiation_events(inputs)
    for index, event in enumerate(events):
        event["ts"] = _fixture_ledger_ts(index)
    for event in events[2:]:
        event["metadata"] = dict(event["metadata"])
        event["metadata"][
            api_client.LOGICAL_DISPATCH_AUTHORIZED_AT_UTC_FIELD
        ] = _fixture_ledger_ts(1)
    inputs["ledger_events"] = events

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="streaming retry did not inherit",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_non_negotiation_transport_retry_after_expiry(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    reserved, success = inputs["ledger_events"]
    retry_reservation = {
        **copy.deepcopy(reserved),
        "attempt_id": "provider-attempt-not-a-negotiation-retry",
        "attempt": 1,
        "ts": _fixture_ledger_ts(2),
    }
    retry_success = {
        **copy.deepcopy(success),
        "attempt_id": "provider-attempt-not-a-negotiation-retry",
        "attempt": 1,
        "ts": _fixture_ledger_ts(3),
    }
    inputs["ledger_events"] = [
        reserved,
        success,
        retry_reservation,
        retry_success,
    ]
    inputs["authorization_valid_until_utc"] = reserved["ts"]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="not the same logical call's immediate streaming retry",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_overlapping_serial_transport_reservations(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    reserved, release, retry_reservation, retry_success = (
        _first_call_negotiation_events(inputs))
    inputs["ledger_events"] = [
        reserved,
        retry_reservation,
        release,
        retry_success,
    ]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="overlapping provider reservations",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_transport_attempts_completed_in_reverse_order(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    reserved, release, retry_reservation, retry_success = (
        _first_call_negotiation_events(inputs))
    inputs["ledger_events"] = [
        retry_reservation,
        retry_success,
        reserved,
        release,
    ]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="transport attempts are out of order",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_an_interposed_call_before_the_streaming_retry(
    tmp_path: Path,
) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
    )
    reserved, release, retry_reservation, retry_success = (
        _first_call_negotiation_events(inputs))
    later_reservation, later_success = inputs["ledger_events"][2:4]
    inputs["ledger_events"] = [
        reserved,
        release,
        later_reservation,
        later_success,
        retry_reservation,
        retry_success,
        *inputs["ledger_events"][4:],
    ]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="interposes a call before its streaming retry",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_orphan_terminal_attempt(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    inputs["ledger_events"] = inputs["ledger_events"][1:]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="no open reservation",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_orphan_application_attempt_two(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
        query_responses=("CLAIM: Position A is correct", "DONE"),
    )
    orphan_identity = ("judge_query", 0, 1)
    inputs["journal_rows"] = [
        row for row in inputs["journal_rows"]
        if (row["call_role"], row["slot"], row["attempt"]) != orphan_identity
    ]
    inputs["ledger_events"] = [
        event for event in inputs["ledger_events"]
        if (
            event["metadata"]["call_role"],
            event["metadata"].get("slot", event["metadata"].get("query_index", 0)),
            event["metadata"].get("attempt", 1),
        ) != orphan_identity
    ]

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="unjournaled logical call",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_extra_call_after_checker_malformed_terminal(
    tmp_path: Path,
) -> None:
    terminal_dir = tmp_path / "terminal"
    completed_dir = tmp_path / "completed"
    terminal_dir.mkdir()
    completed_dir.mkdir()
    terminal = _fixture(
        terminal_dir,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
        checker_response="ALLOW",
    )
    completed = _fixture(
        completed_dir,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
    )
    verdict_row = next(
        copy.deepcopy(row) for row in completed["journal_rows"]
        if row["call_role"] == "judge_verdict"
    )
    verdict_identity = (
        verdict_row["cell_key"],
        verdict_row["call_role"],
        verdict_row["slot"],
        verdict_row["attempt"],
    )
    verdict_attempt_ids = {
        event["attempt_id"] for event in completed["ledger_events"]
        if (
            event["metadata"]["cell_key"],
            event["metadata"]["call_role"],
            event["metadata"].get("slot", event["metadata"].get("query_index", 0)),
            event["metadata"].get("attempt", 1),
        ) == verdict_identity
    }
    assert len(verdict_attempt_ids) == 1
    terminal["journal_rows"].append(verdict_row)
    terminal["ledger_events"].extend(
        copy.deepcopy(event) for event in completed["ledger_events"]
        if event["attempt_id"] in verdict_attempt_ids
    )

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="did not consume the exact journal and ledger sets",
    ):
        provenance.verify_main_provider_replay(**terminal)


def test_replay_rejects_unplanned_result_row(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path, judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    inputs["result_rows"].append({
        "cell_key": "foreign:phase3_debate_judgment:row",
        "result": {"cell_key": "foreign:phase3_debate_judgment:row"},
    })

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="unplanned cell keys",
    ):
        provenance.verify_main_provider_replay(**inputs)


def test_replay_rejects_result_for_terminal_cell(tmp_path: Path) -> None:
    inputs = _fixture(
        tmp_path,
        judge_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        query_budget=1,
        checker_response="ALLOW",
    )
    terminal_key = inputs["terminal_cell_keys"][0]
    inputs["result_rows"].append({
        "cell_key": terminal_key,
        "result": {"cell_key": terminal_key},
    })

    with pytest.raises(
        provenance.MainProviderProvenanceError,
        match="exact completed judgment partition",
    ):
        provenance.verify_main_provider_replay(**inputs)
