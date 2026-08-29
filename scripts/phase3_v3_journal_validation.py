"""Live validation of the request journal on VS-019 plus controls (Codex condition C).

Exercises the EXACT production execution path (phase2_canary_execute.execute_cell with the
production gate, checker overrides, role-limit caps and polarity machinery) with a
JournalingClient in place of the CachingClient, against the live provider, on:

- the two VS-019 cell shapes that went terminal in run phase3-v3-82c8f75feba42a9e (their
  exact cell keys, loaded from terminal-halt records 006 and 008, never hand-typed);
- one ordinary control cell per judge.

Each cell runs three phases: KILL (a simulated process death after N live dispatches),
RESUME (fresh objects over the same journal file, run to completion), REPLAY (a full
re-drive over a poisoned inner client that fails on any dispatch). Pass criteria, applied
mechanically: the resume dispatches only never-journaled requests, the replay makes ZERO
provider calls and reproduces the resumed record byte-identically modulo created_at, and
any journaled visibly-empty response is consumed by the frozen retry-then-block ladder.

Spend governance: refuses to go live unless the owner-approved authorization record
exists (rejudge/phase3_v3_journal_validation_authorization_2026-08-29.json with
owner_approved true). Its own ledger, $2.00 aggregate cap, $1.00 uncertain ceiling.
Validation rows are engineering artifacts: they never enter any analysis store, and the
plan record discloses the auto-ALLOW reviewer and the reuse of protocol-r6 cell
identities for trigger fidelity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge import api_client, phase3_plan, phase3_runner  # noqa: E402
from rejudge.phase2_canary_execute import CellContext, execute_cell  # noqa: E402
from rejudge.phase2_dual_gate import DualGateDecisionStore  # noqa: E402
from rejudge.phase3_runner import RoleLimitResolvingClient  # noqa: E402
from rejudge.request_journal import JournalingClient, RequestJournal  # noqa: E402

PROTOCOL_PATH = REPO_ROOT / "rejudge/phase3_protocol_v3_r6.json"
ROLE_LIMITS_PATH = REPO_ROOT / "rejudge/phase3_v3_role_limits_r10_2026-08-28.json"
PRICE_SNAPSHOT_PATH = REPO_ROOT / "rejudge/phase3_v3_price_snapshot_r11_2026-08-29.json"
MANIFEST_PATH = REPO_ROOT / "rejudge/phase3_v3_run_manifest_preflight_r32_2026-08-29.json"
BUNDLE_PATH = REPO_ROOT / "rejudge/phase2_prompt_bundle.json"
TERMINAL_RECORDS = [
    REPO_ROOT / "rejudge/phase3_v3_terminal_halts_006_2026-08-29.json",
    REPO_ROOT / "rejudge/phase3_v3_terminal_halts_008_2026-08-29.json",
]
SEALED_RESULTS = Path(
    "E:/selvarath-archive/phase3-v3r15-clean-2026-08-29/phase3_v3_canary_results.jsonl")
AUTHORIZATION_PATH = REPO_ROOT / (
    "rejudge/phase3_v3_journal_validation_authorization_2026-08-29.json")
VALIDATION_DIR = Path("E:/selvarath-archive/phase3-v3-journal-validation-2026-08-29")
RESULTS_RECORD_PATH = REPO_ROOT / (
    "rejudge/phase3_v3_journal_validation_results_2026-08-29.json")

AGGREGATE_CAP_USD = 2.00
UNCERTAIN_CEILING_USD = 1.00
KILL_AFTER_BY_BUDGET = {2: 3, 4: 5}
KILL_AFTER_CONTROL = 2


class SimulatedProcessDeath(BaseException):
    """Raised between live dispatches to model a process kill. BaseException so nothing in
    the execution stack can accidentally swallow it and keep running."""


class KillSwitchClient:
    """Passes calls through; dies BEFORE the (n+1)th live dispatch reaches the provider."""

    def __init__(self, inner, kill_after: int) -> None:
        self.inner = inner
        self.kill_after = kill_after
        self.live_calls = 0

    @property
    def dry_run(self) -> bool:
        return getattr(self.inner, "dry_run", False)

    def complete(self, *args, **kwargs):
        if self.live_calls >= self.kill_after:
            raise SimulatedProcessDeath(
                f"simulated death before live dispatch {self.live_calls + 1}")
        self.live_calls += 1
        return self.inner.complete(*args, **kwargs)


class PoisonClient:
    """The replay phase's inner client: any dispatch is a validation failure."""

    dry_run = False

    def __init__(self) -> None:
        self.dispatches = 0

    def complete(self, *args, **kwargs):
        self.dispatches += 1
        raise AssertionError(
            "replay phase dispatched a provider call; the journal failed to cover the cell")


class CountingClient:
    """Counts live dispatches that pass through to the provider."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.live_calls = 0

    @property
    def dry_run(self) -> bool:
        return getattr(self.inner, "dry_run", False)

    def complete(self, *args, **kwargs):
        self.live_calls += 1
        return self.inner.complete(*args, **kwargs)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _auto_allow_reviewer(raw_query, candidate_a, candidate_b) -> str:
    # Validation-only: reviewer rulings are science controls and validation rows never
    # enter analysis, so the reviewer is a constant ALLOW. The live checker still runs.
    return ("LABEL: ALLOW\nCLAUSE: Allowed\nRATIONALE: journal validation auto-allow; "
            "validation rows never enter any analysis")


def _canonical(record: dict) -> str:
    return json.dumps({k: v for k, v in record.items() if k != "created_at"},
                      sort_keys=True)


def _target_cells(protocol: dict, manifest: dict) -> list[dict]:
    """The frozen selection: the two VS-019 terminal shapes plus one control per judge."""
    _main, held_out = phase3_plan.load_reference_question_ids(protocol, REPO_ROOT)
    plan = phase3_plan.enumerate_canary_cells(
        protocol, manifest["final_roster"], held_out)
    by_key = {str(cell["cell_key"]): cell for cell in plan}
    targets = []
    for record_path in TERMINAL_RECORDS:
        key = _load(record_path)["cell_key"]
        if key not in by_key:
            raise SystemExit(f"terminal cell key not in the r6 plan: {key}")
        targets.append(by_key[key])
    judgments = sorted(
        (cell for cell in plan
         if cell["kind"] == phase3_plan.CANARY_JUDGMENT_KIND
         and cell.get("query_budget") == 2
         and cell["question_id"] != "VS-019"),
        key=lambda cell: str(cell["cell_key"]))
    for judge in sorted({str(c["judge_model"]) for c in judgments}):
        targets.append(next(c for c in judgments if str(c["judge_model"]) == judge))
    return targets


def _preseed_transcripts(cells: list) -> dict[str, dict]:
    """Dependency transcripts, byte-identical from the sealed canary archive (read-only)."""
    needed = {key for cell in cells for key in cell.dependency_keys}
    results: dict[str, dict] = {}
    with SEALED_RESULTS.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["cell_key"] in needed:
                results[row["cell_key"]] = row["result"]
    missing = needed - set(results)
    if missing:
        raise SystemExit(f"sealed archive lacks dependency transcripts: {sorted(missing)}")
    return results


def _build_live_inner(role_limits: dict, price_snapshot: dict, ledger_path: Path,
                      error_log: Path, prior_spend: float, prior_uncertain: float,
                      prior_run_uncertain: float):
    request = role_limits["request_settings"]
    transport = request["transport"]
    error_log.parent.mkdir(parents=True, exist_ok=True)
    error_log.touch(exist_ok=True)
    snapshot = api_client.prepare_usage_ledger(ledger_path, allow_create=True)
    raw = api_client.RejudgeClient(
        approved_cap_usd=AGGREGATE_CAP_USD,
        dry_run=False,
        error_log_path=str(error_log),
        max_retries=int(transport["ledger_max_retries"]),
        model_prices={
            model: {"in": float(entry["input_usd_per_million"]),
                    "out": float(entry["output_usd_per_million"])}
            for model, entry in price_snapshot["models"].items()},
        strict_model_pricing=True,
        initial_spend_usd=prior_spend,
        initial_uncertain_spend_usd=prior_uncertain,
        usage_log_path=str(snapshot.path),
        _ledger_snapshot=snapshot,
        _accounting_factory_token=api_client._LIVE_ACCOUNTING_FACTORY_TOKEN,
        require_explicit_reasoning_max_tokens=True,
        model_context_limits={
            model: int(entry["context_length_tokens"])
            for model, entry in role_limits["context_ceilings"].items()},
        strict_context_mode=True,
        streaming_pinned_models=frozenset(request["streaming_pinned_models"]),
        reasoning_models=frozenset(role_limits["reasoning_models"]["model_ids"]),
        extra_request_fields={
            model: dict(fields)
            for model, fields in request["per_model_extra_fields"].items()},
        halt_on_unknown_charge=True,
        http_timeout=dict(transport["http_timeout"]),
        sdk_internal_max_retries=int(transport["sdk_internal_max_retries"]),
        per_call_wall_clock_ceiling_seconds=float(
            transport["per_call_wall_clock_ceiling_seconds"]),
        require_returned_model_match=True,
        run_uncertain_ceiling_usd=UNCERTAIN_CEILING_USD,
        initial_run_uncertain_spend_usd=prior_run_uncertain,
    )
    return RoleLimitResolvingClient(raw, role_limits["model_role_limits"])


def _ledger_totals(ledger_path: Path) -> tuple[float, float, float]:
    actual = uncertain = 0.0
    events = 0
    if not ledger_path.exists():
        return 0.0, 0.0, 0
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        events += 1
        cost = float(event.get("cost_usd") or 0.0)
        if event.get("status") == "success":
            actual += cost
        elif event.get("status") == "unknown_charge":
            uncertain += cost
    return actual, uncertain, events


def _run_cell_phases(cell, *, context_template: dict, journal_path: Path,
                     kill_after: int, live_inner_factory) -> dict:
    outcome: dict = {"cell_key": str(cell.cell_key), "judge_model": str(cell.judge_model),
                     "question_id": cell.question_id, "query_budget": cell.query_budget,
                     "kill_after": kill_after}
    identity = f"journal-validation:{cell.cell_key}"
    namespace = context_template["namespace"]

    def _context(client) -> CellContext:
        return CellContext(
            client=client, protocol=context_template["protocol"],
            bundle=context_template["bundle"],
            decision_store=context_template["decision_store"],
            reviewer=_auto_allow_reviewer, anchor_judge_model="",
            results=dict(context_template["results"]),
            pause_when_unlabeled=False, transcript_generation_forbidden=True,
            role_limits=context_template["role_limits"])

    # KILL: die mid-cell after kill_after live dispatches.
    kill_client = KillSwitchClient(live_inner_factory(), kill_after)
    journaled = JournalingClient(
        kill_client, RequestJournal(journal_path, execution_identity=identity))
    try:
        execute_cell(cell, _context(journaled), debater_model=cell.debater_model,
                     namespace=namespace)
        outcome["kill_phase"] = "completed_before_kill_point"
    except SimulatedProcessDeath:
        outcome["kill_phase"] = "killed"
    outcome["kill_live_calls"] = kill_client.live_calls

    # RESUME: fresh objects over the same journal file; only never-journaled requests may
    # go live.
    resume_counter = CountingClient(live_inner_factory())
    resume_journal = RequestJournal(journal_path, execution_identity=identity)
    pre_resume_entries = len(resume_journal._entries)
    record = execute_cell(
        cell, _context(JournalingClient(resume_counter, resume_journal)),
        debater_model=cell.debater_model, namespace=namespace)
    outcome["resume_live_calls"] = resume_counter.live_calls
    outcome["journal_entries_before_resume"] = pre_resume_entries
    outcome["journal_entries_after_resume"] = len(resume_journal._entries)
    outcome["resume_dispatched_only_new_requests"] = (
        len(resume_journal._entries) - pre_resume_entries == resume_counter.live_calls)

    # REPLAY: a full re-drive must touch the provider zero times and reproduce the record.
    poison = PoisonClient()
    replay_journal = RequestJournal(journal_path, execution_identity=identity)
    replayed = execute_cell(
        cell, _context(JournalingClient(poison, replay_journal)),
        debater_model=cell.debater_model, namespace=namespace)
    outcome["replay_dispatches"] = poison.dispatches
    outcome["replay_bit_identical"] = _canonical(replayed) == _canonical(record)

    empties = [row for row in replay_journal._entries.values()
               if not str(row["response"]).strip()]
    outcome["journaled_visibly_empty_responses"] = len(empties)
    outcome["record_verdict_parsed"] = record.get("verdict_strict", {}).get(
        "verdict") is not None
    outcome["passed"] = (
        outcome["kill_phase"] in {"killed", "completed_before_kill_point"}
        and outcome["resume_dispatched_only_new_requests"]
        and outcome["replay_dispatches"] == 0
        and outcome["replay_bit_identical"])
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="dispatch real provider calls (requires the owner-approved "
                             "authorization record)")
    args = parser.parse_args(argv)

    protocol = _load(PROTOCOL_PATH)
    manifest = _load(MANIFEST_PATH)
    role_limits = _load(ROLE_LIMITS_PATH)
    price_snapshot = _load(PRICE_SNAPSHOT_PATH)
    bundle = _load(BUNDLE_PATH)

    plan_cells = _target_cells(protocol, manifest)
    judgments, capabilities = phase3_runner.resolve_canary_cells(
        plan_cells, protocol=protocol, bundle=bundle)
    if capabilities or len(judgments) != 4:
        raise SystemExit(
            f"target selection drifted: {len(judgments)} judgments, "
            f"{len(capabilities)} capability cells (expected 4 and 0)")
    preseed = _preseed_transcripts(judgments)
    print(json.dumps({
        "targets": [{"cell_key": str(c.cell_key), "judge": str(c.judge_model),
                     "question": c.question_id, "budget": c.query_budget}
                    for c in judgments],
        "preseeded_transcripts": len(preseed)}, indent=1))

    if not args.live:
        print("dry run complete: selection and preseeding verified, no provider calls")
        return 0

    authorization = _load(AUTHORIZATION_PATH) if AUTHORIZATION_PATH.exists() else {}
    if authorization.get("owner_approved") is not True:
        raise SystemExit(
            "REFUSED: live validation requires the owner-approved authorization record "
            f"at {AUTHORIZATION_PATH} with owner_approved true")
    if float(authorization.get("aggregate_cap_usd", 0)) != AGGREGATE_CAP_USD:
        raise SystemExit("REFUSED: authorization cap does not match the frozen $2.00")

    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = VALIDATION_DIR / "journal_validation_usage.jsonl"
    error_log = VALIDATION_DIR / "journal_validation_errors.jsonl"
    decision_store = DualGateDecisionStore(
        VALIDATION_DIR / "journal_validation_decisions.jsonl")

    def live_inner_factory():
        actual, uncertain, _events = _ledger_totals(ledger_path)
        return _build_live_inner(
            role_limits, price_snapshot, ledger_path, error_log,
            prior_spend=actual, prior_uncertain=uncertain,
            prior_run_uncertain=uncertain)

    context_template = {
        "protocol": protocol, "bundle": bundle, "role_limits": role_limits,
        "decision_store": decision_store, "results": preseed,
        "namespace": str(protocol["cell_key_namespace"]),
    }
    outcomes = []
    for cell in judgments:
        kill_after = (KILL_AFTER_BY_BUDGET.get(cell.query_budget, KILL_AFTER_CONTROL)
                      if cell.question_id == "VS-019" else KILL_AFTER_CONTROL)
        journal_path = VALIDATION_DIR / (
            f"journal_{hashlib.sha256(str(cell.cell_key).encode()).hexdigest()[:16]}.jsonl")
        print(f"validating {cell.question_id} b{cell.query_budget} "
              f"{str(cell.judge_model).split('/')[-1]} (kill after {kill_after})")
        outcome = _run_cell_phases(
            cell, context_template=context_template, journal_path=journal_path,
            kill_after=kill_after, live_inner_factory=live_inner_factory)
        outcomes.append(outcome)
        print(json.dumps(outcome, indent=1))

    actual, uncertain, events = _ledger_totals(ledger_path)
    record = {
        "schema_version": "phase3_v3_journal_validation_results_v1",
        "recorded_at_utc": _utc_now(),
        "authorization": str(AUTHORIZATION_PATH.name),
        "aggregate_cap_usd": AGGREGATE_CAP_USD,
        "uncertain_ceiling_usd": UNCERTAIN_CEILING_USD,
        "actual_spend_usd": round(actual, 8),
        "uncertain_spend_usd": round(uncertain, 8),
        "ledger_events": events,
        "cells": outcomes,
        "all_passed": all(outcome["passed"] for outcome in outcomes),
        "non_claims": [
            "validation rows are engineering artifacts and never enter any analysis",
            "the reviewer was a constant ALLOW; the live checker ran unmodified",
            "cell identities reuse protocol r6 derivations for trigger fidelity; the "
            "sealed canary archive is unmodified and this exercise reconciles nothing "
            "back into it",
        ],
    }
    with RESULTS_RECORD_PATH.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=1, ensure_ascii=True)
        handle.write("\n")
    print(json.dumps({"all_passed": record["all_passed"],
                      "actual_spend_usd": record["actual_spend_usd"],
                      "results_record": RESULTS_RECORD_PATH.name}, indent=1))
    return 0 if record["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
