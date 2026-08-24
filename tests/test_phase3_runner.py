"""Launch-gate evidence for rejudge.phase3_runner: the phase-3 canary driver.

Fully hermetic: every client is either ``rejudge.phase2_canary_fixtures.DeterministicCanaryClient``
(a pure offline stub answering every canary call role deterministically, at zero cost) or a real
``rejudge.api_client.RejudgeClient`` pointed at an injected fake SDK object -- never a real
``together``/network client. Nothing here reads or writes ``E:/``.

The big dry run (``test_full_canary_dry_run_completes_all_1680_slots``) drives the REAL frozen
protocol, 7-judge roster (including the amendment-1 substitute), reused phase-2 prompt bundle,
and role-limits artifact -- all tracked, in-repo files -- against SYNTHETIC transcript bundles,
mirroring ``tests/test_phase3_manifest.py``'s own ``_copied_repo`` pattern (a tmp copy of the
repo, with only the two transcript bundles and the verification report overwritten) but extended
to cover the real 24 held-out question IDs so judgment cells execute against real question/world
content rather than placeholder text.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

import pytest

from rejudge import api_client, phase3_manifest, phase3_plan, phase3_runner, run_accounting
from rejudge.api_client import CapExceededError, ContextGuardError
from rejudge.debate_gen import _load_question_bank
from rejudge.phase2_canary_execute import GenerationForbiddenError
from rejudge.phase2_canary_fixtures import DeterministicCanaryClient, StubReviewer
from rejudge.phase2_canary_live import local_path
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_execution import canonical_sha256
from scripts.phase3_preseed_transcripts import preseed

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "rejudge" / "phase3_protocol.json"

# The 6 protocol-native candidates plus the amendment-1 substitute for the one catalog-absent
# candidate (meta-llama/Meta-Llama-3-8B-Instruct-Lite) -- the frozen 7 the real manifest binds,
# matching decisions.launch_gates.canary_slot_inventory's N7 = 1,680 canary judgment slots.
FULL_ROSTER = [
    "Qwen/Qwen2.5-7B-Instruct-Turbo", "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo", "openai/gpt-oss-120b",
    "google/gemma-3n-E4B-it", "Qwen/Qwen3.7-Max",
    "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
]
# The 4 continuing judges only, for tests that need SOME valid roster but not the full slot
# count (faster to build/execute against).
GUARD_ROSTER = [
    "Qwen/Qwen2.5-7B-Instruct-Turbo", "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo", "openai/gpt-oss-120b",
]

_IGNORE = shutil.ignore_patterns(".git", "data", "rejudge/output", "__pycache__", ".venv",
                                 ".pytest_cache")
_DEBATERS = ("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen3.7-Plus")


# --- repo/bundle fixtures ------------------------------------------------------------------


def _reference_question_ids() -> tuple[tuple[str, ...], tuple[str, ...]]:
    protocol = phase3_plan.load_protocol(PROTOCOL_PATH)
    return phase3_plan.load_reference_question_ids(protocol, ROOT)


def _real_transcript_payload(question: dict, debater: str, transcript_index: int = 0) -> dict:
    return {
        "question_id": question["id"], "transcript_index": transcript_index,
        "world": question["world"], "question": question["question"],
        "correct_answer": question["correct_answer"], "wrong_answer": question["wrong_answer"],
        "honest_first": True,
        "debate_transcript": [
            {"speaker": "honest", "text": f"{debater} opens honestly.", "round": 1},
            {"speaker": "dishonest", "text": f"{debater} opens dishonestly.", "round": 1},
            {"speaker": "honest", "text": f"{debater} closes honestly.", "round": 2},
            {"speaker": "dishonest", "text": f"{debater} closes dishonestly.", "round": 2},
        ],
    }


def _bundle_row(name: str, question_id: str, world: str, debater: str, transcript_index: int,
                payload: dict) -> dict:
    return {
        "question_id": question_id, "world": world, "debater_model": debater,
        "transcript_index": transcript_index,
        "source_cell_key": f"synthetic:{name}:{question_id}:{debater}:{transcript_index}",
        "source_event_hash": "0" * 64, "transcript_sha256": canonical_sha256(payload),
        "transcript_payload": payload,
    }


def _real_canary_transcript_bundle(question_ids: tuple[str, ...]) -> dict:
    """One real-question-content transcript per (debater, held-out question) -- exactly the 48
    the frozen canary bundle carries -- so judgment cells execute against real question/world
    text rather than placeholders."""
    bank = _load_question_bank()
    records = [
        _bundle_row("canary", question_id, bank[question_id]["world"], debater, 0,
                   _real_transcript_payload(bank[question_id], debater))
        for debater in _DEBATERS for question_id in question_ids
    ]
    return {"schema_version": "phase3_transcript_bundle_v1", "bundle": "canary",
            "expected_transcript_count": len(records), "actual_transcript_count": len(records),
            "transcripts": records}


def _real_main_transcript_bundle(main_ids: tuple[str, ...]) -> dict:
    """All 492 (82 real main questions x 2 debaters x 3 transcripts) main-stage transcript
    rows, with real question content. No test in this module exercises the phase-3 MAIN stage,
    but ``scripts.phase3_preseed_transcripts.preseed`` refuses a bundle that does not cover
    EVERY enumerated main transcript cell, so a placeholder subset does not satisfy it -- this
    has to be the real, full main question-id set."""
    bank = _load_question_bank()
    records = [
        _bundle_row("main", question_id, bank[question_id]["world"], debater, index,
                   _real_transcript_payload(bank[question_id], debater, index))
        for debater in _DEBATERS for question_id in main_ids for index in range(3)
    ]
    return {"schema_version": "phase3_transcript_bundle_v1", "bundle": "main",
            "expected_transcript_count": len(records), "actual_transcript_count": len(records),
            "transcripts": records}


def _write_transcript_bundles(root: Path, *, main_bundle: dict, canary_bundle: dict) -> None:
    main_path = root / phase3_manifest.MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH
    canary_path = root / phase3_manifest.CANARY_TRANSCRIPT_BUNDLE_RELATIVE_PATH
    main_path.write_text(json.dumps(main_bundle), encoding="utf-8")
    canary_path.write_text(json.dumps(canary_bundle), encoding="utf-8")
    report_path = root / phase3_manifest.TRANSCRIPT_VERIFICATION_RELATIVE_PATH
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["bundle_canonical_sha256"] = {
        "main_bundle": canonical_sha256(main_bundle),
        "canary_bundle": canonical_sha256(canary_bundle),
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")


@pytest.fixture(scope="module")
def reference_question_ids() -> tuple[tuple[str, ...], tuple[str, ...]]:
    return _reference_question_ids()


@pytest.fixture(scope="module")
def held_out_ids(reference_question_ids) -> tuple[str, ...]:
    return reference_question_ids[1]


@pytest.fixture(scope="module")
def repo_root(tmp_path_factory, reference_question_ids) -> Path:
    """A module-scoped copied repo (see the docstring) so every test below shares one
    (relatively expensive) repo copy + synthetic-bundle write."""
    main_ids, held_out_ids = reference_question_ids
    root = tmp_path_factory.mktemp("phase3_runner") / "repo"
    shutil.copytree(ROOT, root, ignore=_IGNORE)
    _write_transcript_bundles(
        root, main_bundle=_real_main_transcript_bundle(main_ids),
        canary_bundle=_real_canary_transcript_bundle(held_out_ids))
    return root


_DEFAULT_CONTEXT_BLOCKLIST_CANARY_PATH = "rejudge/phase3_context_blocklist_canary_2026-08-19b.json"
_DEFAULT_CONTEXT_BLOCKLIST_MAIN_PATH = "rejudge/phase3_context_blocklist_main_2026-08-19b.json"


def _build_manifest(root: Path, roster: list[str], archive_dir: Path, *,
                    context_blocklist_canary_path: str | None = None) -> dict:
    """``context_blocklist_canary_path`` (Codex re-review, 2026-08-19, blocker 2), left at its
    default, binds the real regenerated (zero-exclusion) precheck output -- what every test that
    does not itself exercise blocklist behavior wants. A test that builds its OWN synthetic
    blocklist (to exercise skip/halt behavior against specific cell keys) passes its path here
    (root-relative or absolute) so the manifest's bound identity and the file it later supplies
    to ``run_phase3_canary`` as ``--context-blocklist`` agree, exactly as a real successor
    manifest and its real regenerated blocklist would.
    """
    return phase3_manifest.build_manifest(
        root / "rejudge" / "phase3_protocol.json", project_root=root,
        recorded_at_utc="2026-08-18T00:00:00Z",
        archive_dir=str(archive_dir).replace("\\", "/"), roster_judges=roster,
        # Amendment 4 (2026-08-19): _copied_repo (via shutil.copytree) carries this real,
        # on-disk validation report along with the rest of the repo, so the same root-relative
        # path resolves under every per-test tmp copy.
        estimator_validation_path="rejudge/phase3_estimator_validation_2026-08-19.json",
        context_blocklist_canary_path=(
            context_blocklist_canary_path or _DEFAULT_CONTEXT_BLOCKLIST_CANARY_PATH),
        context_blocklist_main_path=_DEFAULT_CONTEXT_BLOCKLIST_MAIN_PATH)


def _authorization_for(manifest: dict, *, cap_usd: float = 40.0) -> dict:
    return {
        "schema_version": "phase3_canary_authorization_v1",
        "binds": {"execution_identity_sha256": manifest["execution_identity_sha256"]},
        "scope": {"canary_cap_usd": cap_usd},
        "execution_authorized": True,
    }


def _write_manifest_and_authorization(tmp_path: Path, manifest: dict,
                                      authorization: dict) -> tuple[Path, Path]:
    manifest_path = tmp_path / "manifest.json"
    authorization_path = tmp_path / "authorization.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
    return manifest_path, authorization_path


def _preseed(root: Path, manifest: dict) -> dict:
    results_path = local_path(manifest["ledger"]["canary_results_path"])
    return preseed(
        protocol_path=root / "rejudge" / "phase3_protocol.json", project_root=root,
        main_bundle_path=root / phase3_manifest.MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH,
        canary_bundle_path=root / phase3_manifest.CANARY_TRANSCRIPT_BUNDLE_RELATIVE_PATH,
        verification_report_path=root / phase3_manifest.TRANSCRIPT_VERIFICATION_RELATIVE_PATH,
        target_store_path=results_path)


# ---------------------------------------------------------------------------
# THE DRY RUN: the full canary plan, all 1,728 cells
# ---------------------------------------------------------------------------


def test_full_canary_dry_run_completes_all_1680_slots(tmp_path, repo_root, held_out_ids):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, FULL_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)

    seed_summary = _preseed(repo_root, manifest)
    assert seed_summary["written"]["canary"] == 48
    assert seed_summary["skipped"]["canary"] == 0

    client = DeterministicCanaryClient()
    reviewer = StubReviewer()

    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=reviewer, mode="api")

    assert outcome.halted_reason is None
    assert outcome.paused == 0
    assert outcome.deferred == 0
    assert outcome.abandoned == 0
    assert outcome.skipped == 48
    assert outcome.completed == 1680

    # Zero transcript-generation attempts: every debater_turn call would mean live generation.
    assert not any(call.get("call_role") == "debater_turn" for call in client.calls)

    # capability_qa cells used the capability prompt.
    capability_calls = [c for c in client.calls if c.get("call_role") == "capability_qa"]
    assert len(capability_calls) == 336
    bundle = json.loads(
        (repo_root / "rejudge" / "phase2_prompt_bundle.json").read_text(encoding="utf-8"))
    expected_system = bundle["templates"]["capability_qa"]["system_prompt"]
    assert all(call["messages"][0]["content"] == expected_system for call in capability_calls)
    assert all(call["messages"][0]["role"] == "system" for call in capability_calls)

    # Phase 3 uses one role taxonomy for both b0 and positive-budget verdicts. This is also
    # what the v3 dynamic forecast accepts when attributing every paid attempt to a slot.
    b0_verdicts = [c for c in client.calls
                   if c.get("budget") == 0 and c.get("call_role") == "judge_verdict"]
    assert b0_verdicts
    assert all(
        c.get("stage") == "judgment" and c.get("question_id") and c.get("judge_model")
        for c in b0_verdicts)
    assert not any(c.get("call_role") == "batch_verdict" for c in client.calls)

    # Budgets flow through generically: every sequential_b1/b2/b4/b8 judgment saw the correct
    # total_budget in its rendered query prompt -- not hard-coded to phase 2's budget-2 grid.
    for budget in (1, 2, 4, 8):
        query_calls = [c for c in client.calls
                       if c.get("call_role") == "judge_query" and c.get("budget") == budget]
        assert query_calls, f"no judge_query calls captured for budget {budget}"
        assert any(
            f"out of {budget}." in message["content"]
            for call in query_calls for message in call["messages"]
            if message["role"] == "user"
        ), f"no judge_query prompt rendered 'out of {budget}.' for budget {budget}"

    # The store chain verifies at the end: reopening it re-walks the whole hash chain without
    # raising. It holds 2,220 rows in total -- preseed() seeds BOTH bundles into this one store
    # (492 main + 48 canary transcript rows), plus the 1,680 cells this run just executed.
    results_path = local_path(manifest["ledger"]["canary_results_path"])
    reopened = CellResultStore(results_path)
    assert len(reopened._results) == 492 + 48 + 1680

    protocol_for_count = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    canary_cells = phase3_plan.enumerate_canary_cells(protocol_for_count, FULL_ROSTER, held_out_ids)
    canary_cell_keys = {str(cell["cell_key"]) for cell in canary_cells}
    assert len(canary_cell_keys) == 1728
    assert canary_cell_keys <= set(reopened._results)


# ---------------------------------------------------------------------------
# model pricing: split across two snapshot files, one candidate priced only inside
# a DIFFERENT candidate's closest_catalog_ids
# ---------------------------------------------------------------------------


def test_roster_model_prices_resolve_for_the_full_7_judge_roster(repo_root):
    """Exercises all three price sources ``resolve_roster_model_prices`` searches: the 4
    continuing judges (from ``phase2_provider_price_snapshot``), the 2 new candidates with a
    direct ``new_judges_pending_verification`` entry, and the one admitted-by-substitution
    candidate (``meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo``) whose price is recorded only
    inside a DIFFERENT (catalog-absent) candidate's ``closest_catalog_ids`` list."""
    prices = phase3_runner.resolve_roster_model_prices(FULL_ROSTER, project_root=repo_root)
    assert set(prices) == set(FULL_ROSTER)
    for model, pair in prices.items():
        assert pair["in"] > 0, model
        assert pair["out"] > 0, model
    # The substitute's price, cross-checked against the real snapshot's nested record.
    assert prices["meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"] == {"in": 0.18, "out": 0.18}


def test_an_unpriceable_roster_judge_refuses(repo_root):
    with pytest.raises(phase3_runner.Phase3RunnerError, match="no price could be resolved"):
        phase3_runner.resolve_roster_model_prices(
            [*GUARD_ROSTER, "not-a-real-model-id"], project_root=repo_root)


# ---------------------------------------------------------------------------
# pins-threading: the constructed client actually carries the v5 transport pins
# ---------------------------------------------------------------------------


def test_transport_pins_are_threaded_into_the_constructed_client(tmp_path, repo_root):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    role_limits = json.loads(
        (repo_root / str(manifest["frozen_inputs"]["role_limits_tracked_path"]))
        .read_text(encoding="utf-8"))
    transport = role_limits["request_settings"]["transport"]

    client = phase3_runner.build_phase3_client(
        manifest, project_root=repo_root, canary_cap_usd=40.0,
        usage_log_path=archive_dir / "usage.jsonl", error_log_path=archive_dir / "err.jsonl",
        call_cache_path=archive_dir / "cache.jsonl")
    raw = phase3_runner.underlying_rejudge_client(client)

    assert raw.http_timeout is not None
    assert raw.http_timeout["read"] == float(transport["http_timeout"]["read"])
    assert raw.http_timeout["connect"] == float(transport["http_timeout"]["connect"])
    assert raw.sdk_internal_max_retries == int(transport["sdk_internal_max_retries"])
    assert raw.per_call_wall_clock_ceiling_seconds == float(
        transport["per_call_wall_clock_ceiling_seconds"])
    # The other phase-2 hardening knobs create_accounted_client does not expose (see
    # phase3_runner._apply_role_limit_hardening's docstring) are also carried.
    assert raw.strict_context_mode is True
    assert raw.halt_on_unknown_charge is True
    assert raw.require_explicit_reasoning_max_tokens is True
    assert "openai/gpt-oss-120b" in raw.reasoning_models


def test_building_the_client_the_old_way_carries_none_regression(tmp_path):
    """Regression for the exact 2026-08-10 gap: create_accounted_client called WITHOUT the v5
    pin kwargs (the shape every call site used before this change) must still carry None, so
    the finding that motivated threading them stays falsifiable."""
    ledger = tmp_path / "usage.jsonl"
    identity = run_accounting.prepare_usage_ledger(ledger, allow_create=True)
    client, _summary = run_accounting.create_accounted_client(
        approved_cap_usd=1.0, dry_run=False, model_prices={"m": {"in": 1.0, "out": 1.0}},
        usage_log_path=ledger, error_log_path=tmp_path / "errors.jsonl",
        ledger_identity=identity)
    assert client.http_timeout is None
    assert client.sdk_internal_max_retries is None
    assert client.per_call_wall_clock_ceiling_seconds is None


# ---------------------------------------------------------------------------
# generation guard: a missing transcript row crashes, never a provider call
# ---------------------------------------------------------------------------


def test_a_missing_transcript_row_crashes_with_generation_forbidden_error(tmp_path, repo_root,
                                                                          held_out_ids):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)

    _preseed(repo_root, manifest)

    protocol = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, GUARD_ROSTER, held_out_ids)
    dropped_key = next(
        str(cell["cell_key"]) for cell in canary_cells
        if cell["kind"] == phase3_plan.CANARY_TRANSCRIPT_KIND)

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()
           if line.strip()]
    kept = [row for row in rows if row["cell_key"] != dropped_key]
    assert len(kept) == len(rows) - 1
    results_path.unlink()
    store = CellResultStore(results_path)
    for row in kept:
        store.record(row["cell_key"], row["result"])

    client = DeterministicCanaryClient()
    with pytest.raises(GenerationForbiddenError, match=re.escape(dropped_key)):
        phase3_runner.run_phase3_canary(
            manifest_path, authorization_path, project_root=repo_root, client=client,
            reviewer=StubReviewer(), mode="api")
    assert client.calls == [], "the guard must fire before any provider call"


# ---------------------------------------------------------------------------
# spend cap: refuses to start, and stops mid-run
# ---------------------------------------------------------------------------


class _FixedResponseSDK:
    """A minimal fake `together` SDK client: one fixed, instant, non-network response."""

    def __init__(self) -> None:
        self.calls = 0
        outer = self

        class _Usage:
            # Small and well under any max_tokens this test requests: the reservation the
            # client made before the call is sized from the REQUEST (max_tokens, estimated
            # prompt tokens), and an actual cost that exceeds it trips
            # RejudgeClient's AccountingInvariantError.
            prompt_tokens = 20
            completion_tokens = 16

        class _Message:
            content = "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: fixture"

        class _Choice:
            message = _Message()

        class _Resp:
            usage = _Usage()
            choices = [_Choice()]

        class _Completions:
            def create(self, **kwargs):
                outer.calls += 1
                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def test_cap_already_reached_refuses_to_start(tmp_path, repo_root):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    usage_log_path = archive_dir / "usage.jsonl"

    prices = phase3_runner.resolve_roster_model_prices(GUARD_ROSTER, project_root=repo_root)
    model = GUARD_ROSTER[0]
    identity = run_accounting.prepare_usage_ledger(usage_log_path, allow_create=True)
    snapshot = api_client.load_chained_usage_ledger(
        str(usage_log_path), expected_identity=identity)
    seed_client = api_client.RejudgeClient(
        approved_cap_usd=1000.0, dry_run=False, error_log_path=str(archive_dir / "seed_err.jsonl"),
        model_prices=prices, strict_model_pricing=True, initial_spend_usd=0.0,
        initial_uncertain_spend_usd=0.0, usage_log_path=str(usage_log_path),
        _ledger_snapshot=snapshot, _accounting_factory_token=api_client._LIVE_ACCOUNTING_FACTORY_TOKEN,
        _sdk_client=_FixedResponseSDK())
    seed_client.complete(
        [{"role": "user", "content": "seed spend"}], model, 0.0, 1, 64, kind="verdict",
        request_metadata={"cell_key": "seed", "call_role": "judge_verdict"})
    spent = seed_client.actual_spent_usd
    assert spent > 0, "the seed call must have accounted some real spend"

    with pytest.raises(phase3_runner.Phase3RunnerError, match="reached the canary cap"):
        phase3_runner.build_phase3_client(
            manifest, project_root=repo_root, canary_cap_usd=spent,
            usage_log_path=usage_log_path, error_log_path=archive_dir / "err.jsonl",
            call_cache_path=archive_dir / "cache.jsonl")


class _CapExceededAfterNCalls:
    """Wraps a client and raises CapExceededError from the (N+1)th call on -- simulating a real
    spend cap being crossed mid-run without needing real ledger accounting."""

    def __init__(self, inner, limit: int) -> None:
        self._inner = inner
        self._limit = limit
        self.calls = 0

    @property
    def dry_run(self) -> bool:
        return getattr(self._inner, "dry_run", False)

    def complete(self, *args, **kwargs):
        self.calls += 1
        if self.calls > self._limit:
            raise CapExceededError("synthetic cap breach")
        return self._inner.complete(*args, **kwargs)


def test_cap_exceeded_stops_mid_run(tmp_path, repo_root):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    client = _CapExceededAfterNCalls(DeterministicCanaryClient(), limit=5)
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=StubReviewer(), mode="api")

    assert outcome.halted_reason == "cap_exceeded"
    assert outcome.halted_cell_key is not None
    # Only a handful of cells could have completed before the synthetic breach.
    assert outcome.completed < 50


def test_resumes_correctly_after_a_mid_run_halt(tmp_path, repo_root):
    """Resumable at cell granularity: cells the store already carries a result for are never
    re-attempted (and so never re-billed) on the next invocation, exactly phase 2's own
    resume contract via CellResultStore.is_complete."""
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    first_client = _CapExceededAfterNCalls(DeterministicCanaryClient(), limit=5)
    first_outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=first_client,
        reviewer=StubReviewer(), mode="api")
    assert first_outcome.halted_reason == "cap_exceeded"
    assert first_outcome.completed > 0

    second_outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root,
        client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api")

    assert second_outcome.halted_reason is None
    # Everything the second pass skips is exactly what the first pass had already preseeded
    # or completed -- nothing more, nothing less. (The capability phase only starts once the
    # judgment/transcript pass is clean, so it contributed zero skips in the first, halted run.)
    assert second_outcome.skipped == first_outcome.skipped + first_outcome.completed


# ---------------------------------------------------------------------------
# condition-balanced block scheduling (phase2_canary_runner.run_canary's concurrent path)
# is reused, not reimplemented -- prove the parameters actually reach it
# ---------------------------------------------------------------------------


def test_max_workers_and_block_size_thread_through_to_a_concurrent_pass(tmp_path, repo_root,
                                                                        held_out_ids):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    protocol = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    expected_canary_cells = len(
        phase3_plan.enumerate_canary_cells(protocol, GUARD_ROSTER, held_out_ids))

    client = DeterministicCanaryClient()
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=StubReviewer(), mode="api", max_workers=4, block_size=8)

    assert outcome.halted_reason is None
    assert outcome.paused == 0

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    reopened = CellResultStore(results_path)  # re-walks the hash chain; raises if broken
    canary_cell_keys = {str(cell["cell_key"]) for cell in
                        phase3_plan.enumerate_canary_cells(protocol, GUARD_ROSTER, held_out_ids)}
    assert len(canary_cell_keys) == expected_canary_cells
    assert canary_cell_keys <= set(reopened._results)


# ---------------------------------------------------------------------------
# gate reviews: subagent-batch mode pauses and exports a worklist, exactly phase 2's mechanism
# ---------------------------------------------------------------------------


def test_subagent_batch_mode_pauses_and_exports_a_worklist(tmp_path, repo_root):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    client = DeterministicCanaryClient()
    # reviewer omitted: mode="subagent-batch" defaults to _PauseModeReviewer, which must never
    # be consulted -- an unlabelled payload pauses its cell instead.
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        mode="subagent-batch")

    assert outcome.halted_reason is None
    assert outcome.needs_labelling
    assert outcome.pending_payloads
    # b0 cells never touch the gate at all, so some judgment cells still complete this pass.
    assert outcome.completed > 0

    archive_dir_local = local_path(manifest["ledger"]["archive_dir"])
    worklist_path = archive_dir_local / phase3_runner.WORKLIST_FILENAME
    assert worklist_path.exists()
    worklist = json.loads(worklist_path.read_text(encoding="utf-8"))
    assert len(worklist["items"]) == len(outcome.pending_payloads)
    for item in worklist["items"]:
        assert item["subagent_prompt"]
        assert item["subagent_prompt_sha256"]


# ---------------------------------------------------------------------------
# 2026-08-18 canary stall regressions: a reviewer that withholds every label must never
# stall the driver -- it must return promptly, with schedulable non-gated work done and
# pending payloads exported, exactly the orchestrator's intended contract.
# ---------------------------------------------------------------------------


def test_withheld_labels_still_return_promptly_with_all_non_query_cells_complete(
        tmp_path, repo_root, held_out_ids):
    """Direct regression for the 2026-08-18 stall: with a completely fresh decisions store
    (every label withheld) and the FULL 7-judge roster (all 1,680 canary slots, matching the
    live incident's scale), the driver must still return within a generous but bounded wall
    clock, with every b0 and capability_qa cell (neither ever touches the gate) complete and
    every query-producing cell either paused-with-a-payload or -- for cells the
    pending_payload_limit stopped short of even reaching -- simply not yet attempted."""
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, FULL_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    protocol = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, FULL_ROSTER, held_out_ids)
    expected_b0 = sum(1 for c in canary_cells
                      if c["kind"] == phase3_plan.CANARY_JUDGMENT_KIND and c["condition"] == "b0")
    expected_capability = sum(
        1 for c in canary_cells if c["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND)

    client = DeterministicCanaryClient()
    started = time.monotonic()
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        mode="subagent-batch")
    elapsed = time.monotonic() - started

    # Bounded wall clock: this is an in-memory stub client with zero real network latency, so a
    # driver that is genuinely returning promptly (rather than draining the entire 1,344-cell
    # judgment set) finishes in well under a minute. The 2026-08-18 incident was a 30-MINUTE
    # silence against real providers; this bound only needs to be generous enough to catch a
    # reintroduced unbounded pass, not to model real latency.
    assert elapsed < 60, f"driver took {elapsed:.1f}s against an all-stub client; expected < 60s"

    assert outcome.halted_reason is None
    assert outcome.needs_labelling
    assert outcome.pending_payloads

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    reopened = CellResultStore(results_path)
    completed_b0 = sum(
        1 for c in canary_cells
        if c["kind"] == phase3_plan.CANARY_JUDGMENT_KIND and c["condition"] == "b0"
        and c["cell_key"] in reopened._results)
    completed_capability = sum(
        1 for c in canary_cells if c["kind"] == phase3_plan.CAPABILITY_ANCHOR_KIND
        and c["cell_key"] in reopened._results)
    # The bug this regresses: capability_qa cells used to be skipped OUTRIGHT whenever any
    # judgment cell paused. They share no gate, no dependency, no reviewer with the judgment
    # pass -- every one of them must complete.
    assert completed_capability == expected_capability
    # b0 cells never touch the gate either and sort before every query-producing cell, so
    # every one of them completes in this same pass regardless of how many smoke cells paused.
    assert completed_b0 == expected_b0


def test_pending_payload_limit_bounds_a_pass_instead_of_draining_the_whole_smoke_set(
        tmp_path, repo_root):
    """The other half of the fix: a pass stops accumulating NEW pending payloads once it hits
    the limit, leaving the rest of the query-producing cells genuinely untouched for the next
    invocation -- rather than making a real (uncached) judge_query call for every one of them
    in a single unbounded pass."""
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    client = DeterministicCanaryClient()
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        mode="subagent-batch", pending_payload_limit=3)

    assert outcome.halted_reason is None
    assert len(outcome.pending_payloads) == 3
    # judge_query is the FIRST real call a query-producing cell makes before it can even be
    # classified as paused; capping pending payloads to 3 must cap how many of those calls a
    # pass makes, proving the limit is enforced BEFORE the cell is attempted, not after.
    query_calls = [c for c in client.calls if c.get("call_role") == "judge_query"]
    assert len(query_calls) == 3

    # A second pass, unbounded, must still be able to pick up and finish the rest -- the limit
    # only shapes ONE pass's exposure, it does not lose or corrupt any cell.
    second_outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root,
        client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api")
    assert second_outcome.halted_reason is None
    assert second_outcome.paused == 0
    assert not second_outcome.pending_payloads


# ---------------------------------------------------------------------------
# context-exclusion blocklist (2026-08-19 ContextGuardError fix): blocklisted cells are
# skipped entirely; a miss (ContextGuardError on a cell NOT blocklisted) still halts loudly;
# a blocklist for the wrong namespace is refused outright.
# ---------------------------------------------------------------------------


def _write_blocklist(path: Path, *, namespace: str, cell_keys: list[str]) -> dict:
    payload = {
        "generated_at": "2026-08-19T00:00:00Z", "scope": "canary",
        "cell_key_namespace": namespace,
        "estimator_provenance": {"module": "rejudge.api_client", "function": "_estimate_usage"},
        "ceilings_used": {}, "excluded": [{"cell_key": key} for key in cell_keys],
        "counts_by_judge_and_condition": [], "excluded_count": len(cell_keys),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_blocklisted_cells_are_skipped_entirely_and_the_rest_complete(
        tmp_path, repo_root, held_out_ids):
    protocol = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, GUARD_ROSTER, held_out_ids)
    judgment_keys = sorted(str(c["cell_key"]) for c in canary_cells
                           if c["kind"] == phase3_plan.CANARY_JUDGMENT_KIND)
    blocked_keys = judgment_keys[:2]

    # The blocklist is written and bound into the manifest's OWN identity BEFORE the manifest
    # is built (Codex re-review, 2026-08-19, blocker 2): a real successor manifest binds its
    # regenerated blocklist the same way, and run_phase3_canary now refuses a runtime
    # --context-blocklist that disagrees with what the manifest bound.
    blocklist_path = tmp_path / "blocklist.json"
    _write_blocklist(blocklist_path, namespace=str(protocol["cell_key_namespace"]),
                     cell_keys=blocked_keys)

    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir,
                               context_blocklist_canary_path=str(blocklist_path))
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    blocklist_sha256 = phase3_manifest.canonical_sha256(json.loads(
        blocklist_path.read_text(encoding="utf-8")))

    client = DeterministicCanaryClient()
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=StubReviewer(), mode="api", context_blocklist_path=blocklist_path)

    assert outcome.halted_reason is None
    assert outcome.context_blocked == 2
    assert outcome.context_blocklist_sha256 == blocklist_sha256

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    reopened = CellResultStore(results_path)
    for key in blocked_keys:
        assert key not in reopened._results, "a blocklisted cell must never be attempted"
    for key in judgment_keys:
        if key not in blocked_keys:
            assert key in reopened._results, "every non-blocklisted judgment cell must complete"


def test_omitting_context_blocklist_still_applies_the_manifest_bound_one(
        tmp_path, repo_root, held_out_ids):
    """Second re-review (2026-08-19), blocker 2: a manifest that binds a canary eligibility
    list (every post-amendment-4 manifest does) has NO valid no-blocklist mode. Invoking the
    runner with NO --context-blocklist at all must still resolve and apply the bound exclusions
    from the manifest's own tracked path, and report their count and canonical sha exactly as
    if the same file had been passed explicitly."""
    protocol = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, GUARD_ROSTER, held_out_ids)
    judgment_keys = sorted(str(c["cell_key"]) for c in canary_cells
                           if c["kind"] == phase3_plan.CANARY_JUDGMENT_KIND)
    blocked_keys = judgment_keys[:2]

    blocklist_path = tmp_path / "blocklist.json"
    _write_blocklist(blocklist_path, namespace=str(protocol["cell_key_namespace"]),
                     cell_keys=blocked_keys)

    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir,
                               context_blocklist_canary_path=str(blocklist_path))
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    blocklist_sha256 = phase3_manifest.canonical_sha256(json.loads(
        blocklist_path.read_text(encoding="utf-8")))
    assert blocklist_sha256 == manifest["frozen_inputs"]["context_blocklist_canary_report_sha256"]

    client = DeterministicCanaryClient()
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=StubReviewer(), mode="api")   # NO context_blocklist_path supplied

    assert outcome.halted_reason is None
    assert outcome.context_blocked == 2
    assert outcome.context_blocklist_sha256 == blocklist_sha256

    results_path = local_path(manifest["ledger"]["canary_results_path"])
    reopened = CellResultStore(results_path)
    for key in blocked_keys:
        assert key not in reopened._results, "a manifest-bound blocklisted cell must never be " \
            "attempted, even when --context-blocklist is never passed"
    for key in judgment_keys:
        if key not in blocked_keys:
            assert key in reopened._results, "every non-blocklisted judgment cell must complete"


class _ContextGuardErrorOnCell:
    """Raises ContextGuardError for exactly one target cell_key; passes everything else through."""

    def __init__(self, inner, target_cell_key: str) -> None:
        self._inner = inner
        self._target = target_cell_key

    @property
    def dry_run(self) -> bool:
        return getattr(self._inner, "dry_run", False)

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict", *,
                request_metadata=None):
        if (request_metadata or {}).get("cell_key") == self._target:
            raise ContextGuardError(f"synthetic guard trip on {self._target}")
        return self._inner.complete(messages, model, temperature, seed, max_tokens, kind=kind,
                                    request_metadata=request_metadata)


def test_a_context_guard_error_outside_the_blocklist_still_halts(tmp_path, repo_root, held_out_ids):
    """The precheck claimed completeness; a miss means the precheck was wrong, and that must
    still halt the run loudly -- never be silently absorbed because a blocklist happens to be
    active for OTHER cells."""
    protocol = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, GUARD_ROSTER, held_out_ids)
    b0_keys = sorted(str(c["cell_key"]) for c in canary_cells
                     if c["kind"] == phase3_plan.CANARY_JUDGMENT_KIND and c["condition"] == "b0")
    target = b0_keys[0]        # b0 sorts first, so this cell is reached almost immediately
    unrelated_blocked = b0_keys[-1]   # blocklisted, but NOT the one that trips the guard

    # Written and bound into the manifest's identity BEFORE the manifest is built -- see the
    # sibling test above for why.
    blocklist_path = tmp_path / "blocklist.json"
    _write_blocklist(blocklist_path, namespace=str(protocol["cell_key_namespace"]),
                     cell_keys=[unrelated_blocked])

    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir,
                               context_blocklist_canary_path=str(blocklist_path))
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    client = _ContextGuardErrorOnCell(DeterministicCanaryClient(), target)
    outcome = phase3_runner.run_phase3_canary(
        manifest_path, authorization_path, project_root=repo_root, client=client,
        reviewer=StubReviewer(), mode="api", context_blocklist_path=blocklist_path)

    assert outcome.halted_reason == "ContextGuardError"
    assert outcome.halted_cell_key == target


def test_a_context_blocklist_for_the_wrong_namespace_is_refused(tmp_path, repo_root):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    blocklist_path = tmp_path / "blocklist.json"
    _write_blocklist(blocklist_path, namespace="totally-different-namespace.qb-000000000000",
                     cell_keys=[])

    with pytest.raises(phase3_runner.Phase3RunnerError, match="different namespace|different plan"):
        phase3_runner.run_phase3_canary(
            manifest_path, authorization_path, project_root=repo_root,
            client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api",
            context_blocklist_path=blocklist_path)


def test_a_context_blocklist_that_disagrees_with_the_manifests_binding_is_refused(
        tmp_path, repo_root, held_out_ids):
    """Codex re-review (2026-08-19), blocker 2: same namespace, but DIFFERENT content than
    what the manifest actually bound at build time -- a namespace match alone is not enough."""
    protocol = phase3_plan.load_protocol(repo_root / "rejudge" / "phase3_protocol.json")
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, GUARD_ROSTER, held_out_ids)
    judgment_keys = sorted(str(c["cell_key"]) for c in canary_cells
                           if c["kind"] == phase3_plan.CANARY_JUDGMENT_KIND)

    bound_blocklist_path = tmp_path / "bound_blocklist.json"
    _write_blocklist(bound_blocklist_path, namespace=str(protocol["cell_key_namespace"]),
                     cell_keys=judgment_keys[:1])

    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir,
                               context_blocklist_canary_path=str(bound_blocklist_path))
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    # Same namespace, different excluded cells -- never bound by THIS manifest.
    different_blocklist_path = tmp_path / "different_blocklist.json"
    _write_blocklist(different_blocklist_path, namespace=str(protocol["cell_key_namespace"]),
                     cell_keys=judgment_keys[1:2])

    with pytest.raises(phase3_runner.Phase3RunnerError, match="does not match"):
        phase3_runner.run_phase3_canary(
            manifest_path, authorization_path, project_root=repo_root,
            client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api",
            context_blocklist_path=different_blocklist_path)


def test_a_context_blocklist_is_refused_when_the_manifest_binds_none(
        tmp_path, repo_root, monkeypatch):
    """Codex re-review (2026-08-19), blocker 2: a --context-blocklist supplied against a
    manifest with NO frozen_inputs.context_blocklist_canary_report_sha256 binding at all (the
    pre-amendment-4 manifest shape) must be refused outright -- a namespace check alone is
    insufficient, and there is nothing here to verify a runtime file against."""
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    _preseed(repo_root, manifest)

    # Simulates a pre-amendment-4 manifest shape (build_manifest now makes this binding
    # required, so the only way to reach this shape is a manifest from before that change).
    # Isolates run_phase3_canary's OWN refusal from load_and_validate_manifest's unrelated
    # rebuild-and-compare drift check, which would otherwise refuse first for a different
    # reason (the on-disk manifest no longer matches a locally mutated in-memory copy).
    unbound_manifest = json.loads(json.dumps(manifest))
    del unbound_manifest["frozen_inputs"]["context_blocklist_canary_report_sha256"]
    monkeypatch.setattr(
        phase3_runner, "load_and_validate_manifest", lambda *a, **kw: unbound_manifest)

    blocklist_path = tmp_path / "blocklist.json"
    _write_blocklist(blocklist_path, namespace="irrelevant.qb-000000000000", cell_keys=[])

    with pytest.raises(phase3_runner.Phase3RunnerError, match="binds no"):
        phase3_runner.run_phase3_canary(
            manifest_path, authorization_path, project_root=repo_root,
            client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api",
            context_blocklist_path=blocklist_path)


# ---------------------------------------------------------------------------
# authorization: absent / mismatched identity / not authorized all refuse to start
# ---------------------------------------------------------------------------


def test_authorization_absent_refuses(tmp_path, repo_root):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    manifest_path, _ = _write_manifest_and_authorization(
        tmp_path, manifest, _authorization_for(manifest))
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(phase3_runner.Phase3RunnerError,
                       match="no phase-3 canary authorization"):
        phase3_runner.run_phase3_canary(
            manifest_path, missing_path, project_root=repo_root,
            client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api")


def test_authorization_mismatched_identity_refuses(tmp_path, repo_root):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    authorization["binds"]["execution_identity_sha256"] = "0" * 64
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    with pytest.raises(phase3_runner.Phase3RunnerError,
                       match="different execution_identity_sha256"):
        phase3_runner.run_phase3_canary(
            manifest_path, authorization_path, project_root=repo_root,
            client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api")


def test_authorization_not_authorized_refuses(tmp_path, repo_root):
    archive_dir = tmp_path / "archive"
    manifest = _build_manifest(repo_root, GUARD_ROSTER, archive_dir)
    authorization = _authorization_for(manifest)
    authorization["execution_authorized"] = False
    manifest_path, authorization_path = _write_manifest_and_authorization(
        tmp_path, manifest, authorization)
    with pytest.raises(phase3_runner.Phase3RunnerError, match="execution_authorized == true"):
        phase3_runner.run_phase3_canary(
            manifest_path, authorization_path, project_root=repo_root,
            client=DeterministicCanaryClient(), reviewer=StubReviewer(), mode="api")
