"""Exercise one representative long Qwen3.8 judge verdict live under role limits r11.

Amendment 14 transport probe (methods consult item 3): the 600-second read timeout must be
shown to carry a real non-streaming reasoning verdict of the kind that timed out at 120
seconds in the sealed canary. This runs exactly one main b0 judgment cell for the Qwen
judge through the production cell executor with a strict accounted client, its own fresh
ledger and journal under a probe root outside every formal identity, and a small cap. The
probe row is engineering evidence only and never enters analysis; the formal identity will
re-run the same cell fresh.
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

from rejudge import api_client, phase2_canary_runner, phase3_runner  # noqa: E402
from rejudge.phase2_canary_live import RoleLimitResolvingClient  # noqa: E402
from rejudge.phase3_main_runner import build_main_inventory  # noqa: E402
from rejudge.request_journal import JournalingClient, RequestJournal  # noqa: E402
from scripts import phase3_preseed_transcripts  # noqa: E402

QWEN = "Qwen/Qwen3.8-2.4T-A95B"


class _ForbiddenReviewer:
    def __call__(self, *_args, **_kwargs):
        raise RuntimeError("a budget-zero probe cell must not consult the reviewer")


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path} must be a JSON object")
    return value


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _select_longest_qwen_b0(
    inventory, bundle_path: Path, count: int = 1,
) -> list[tuple[dict, dict, int]]:
    """Deterministic: Qwen b0 judgments on distinct questions, largest transcripts first.

    Transcript size is the UTF-8 byte length of the bundle row's ``transcript_payload``
    keyed by (question_id, transcript_index, debater_model); ties break on cell key.
    """
    with bundle_path.open("r", encoding="utf-8") as handle:
        bundle = json.load(handle)
    def _rows(value):
        if isinstance(value, list) and value and all(
                isinstance(item, dict) and "transcript_payload" in item for item in value):
            return value
        if isinstance(value, dict):
            for nested in value.values():
                found = _rows(nested)
                if found is not None:
                    return found
        return None

    rows = _rows(bundle)
    if rows is None:
        raise SystemExit("main transcript bundle has no transcript_payload rows")
    sizes: dict[tuple[str, int, str], int] = {}
    for row in rows:
        key = (str(row["question_id"]), int(row["transcript_index"]), str(row["debater_model"]))
        sizes[key] = len(json.dumps(row["transcript_payload"], ensure_ascii=False).encode("utf-8"))
    transcripts = {str(cell["cell_key"]): dict(cell) for cell in inventory.transcript_cells}
    candidates = []
    for cell in inventory.judgment_cells:
        if cell["judge_model"] != QWEN or cell["condition"] != "b0":
            continue
        dependency = transcripts[str(cell["dependency_keys"][0])]
        key = (str(dependency["question_id"]), int(dependency["transcript_index"]),
               str(dependency["debater_model"]))
        candidates.append((sizes.get(key, -1), str(cell["cell_key"]), dict(cell), dependency))
    if not candidates:
        raise SystemExit("no Qwen b0 judgment cells in the inventory")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[tuple[dict, dict, int]] = []
    seen_questions: set[str] = set()
    for size, _key, cell, dependency in candidates:
        if str(cell["question_id"]) in seen_questions:
            continue
        seen_questions.add(str(cell["question_id"]))
        chosen.append((cell, dependency, size))
        if len(chosen) == count:
            break
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=REPO_ROOT / "rejudge/phase3_protocol_v3_r6.json")
    parser.add_argument("--prompt-bundle", type=Path, default=REPO_ROOT / "rejudge/phase2_prompt_bundle.json")
    parser.add_argument(
        "--role-limits", type=Path,
        default=REPO_ROOT / "rejudge/phase3_v3_role_limits_r11_2026-09-06.json")
    parser.add_argument("--price-snapshot", type=Path, required=True)
    parser.add_argument("--main-bundle", type=Path, default=Path(
        phase3_preseed_transcripts.DEFAULT_MAIN_BUNDLE_PATH))
    parser.add_argument("--verification-report", type=Path, default=Path(
        phase3_preseed_transcripts.DEFAULT_VERIFICATION_REPORT_PATH))
    parser.add_argument("--cap-usd", type=float, default=2.0)
    parser.add_argument("--uncertain-ceiling-usd", type=float, default=1.0)
    parser.add_argument("--record-out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1)
    args = parser.parse_args(argv)
    if args.count < 1 or args.count > 20:
        raise SystemExit("--count must be between 1 and 20")

    root = args.probe_root.resolve()
    if root.exists():
        raise SystemExit(f"probe root must be fresh: {root}")
    try:
        root.relative_to(REPO_ROOT)
    except ValueError:
        pass
    else:
        raise SystemExit("probe root must be outside the repository")
    record_out = args.record_out.resolve()
    if record_out.exists():
        raise SystemExit(f"refusing to overwrite {record_out}")

    protocol = _load(args.protocol)
    bundle = _load(args.prompt_bundle)
    role_limits = _load(args.role_limits)
    price = _load(args.price_snapshot)
    transport = role_limits["request_settings"]["transport"]
    read_timeout = int(transport["http_timeout"]["read"])
    inventory = build_main_inventory(protocol, project_root=REPO_ROOT)
    chosen = _select_longest_qwen_b0(inventory, args.main_bundle.resolve(), count=args.count)
    selected_cells = [cell for cell, _dependency, _size in chosen]

    root.mkdir(parents=True)
    results = root / "probe_results.jsonl"
    decisions = root / "probe_decisions.jsonl"
    ledger = root / "probe_usage.jsonl"
    journal_path = root / "probe_request_journal.jsonl"
    error_log = root / "probe_provider_errors.jsonl"
    preseed = phase3_preseed_transcripts.preseed_main(
        protocol_path=args.protocol.resolve(),
        project_root=REPO_ROOT,
        main_bundle_path=args.main_bundle.resolve(),
        verification_report_path=args.verification_report.resolve(),
        target_store_path=results,
    )
    if preseed["written"] != 492:
        raise SystemExit(f"preseed did not write 492 transcripts: {preseed}")
    ledger_identity = api_client.prepare_usage_ledger(ledger, allow_create=True)
    snapshot = api_client.load_chained_usage_ledger(ledger, expected_identity=ledger_identity)
    request = role_limits["request_settings"]
    raw = api_client.RejudgeClient(
        approved_cap_usd=float(args.cap_usd),
        dry_run=False,
        error_log_path=str(error_log),
        max_retries=int(transport["ledger_max_retries"]),
        model_prices={
            str(model): {
                "in": float(entry["input_usd_per_million"]),
                "out": float(entry["output_usd_per_million"]),
            }
            for model, entry in price["models"].items()
        },
        strict_model_pricing=True,
        initial_spend_usd=0.0,
        initial_uncertain_spend_usd=0.0,
        run_uncertain_ceiling_usd=float(args.uncertain_ceiling_usd),
        initial_run_uncertain_spend_usd=0.0,
        usage_log_path=str(ledger),
        _ledger_snapshot=snapshot,
        _accounting_factory_token=api_client._LIVE_ACCOUNTING_FACTORY_TOKEN,  # noqa: SLF001
        require_explicit_reasoning_max_tokens=True,
        model_context_limits={
            model: int(entry["context_length_tokens"])
            for model, entry in role_limits["context_ceilings"].items()
        },
        strict_context_mode=True,
        streaming_pinned_models=frozenset(request["streaming_pinned_models"]),
        reasoning_models=frozenset(role_limits["reasoning_models"]["model_ids"]),
        extra_request_fields={
            model: dict(fields) for model, fields in request["per_model_extra_fields"].items()
        },
        halt_on_unknown_charge=True,
        http_timeout=dict(transport["http_timeout"]),
        sdk_internal_max_retries=int(transport["sdk_internal_max_retries"]),
        per_call_wall_clock_ceiling_seconds=float(
            transport["per_call_wall_clock_ceiling_seconds"]),
        require_returned_model_match=True,
    )
    journal = RequestJournal(journal_path, execution_identity="phase3-main-long-verdict-probe")
    client = JournalingClient(
        RoleLimitResolvingClient(raw, role_limits["model_role_limits"]), journal)
    resolved = phase3_runner.resolve_main_cells(
        [dict(cell) for cell in inventory.transcript_cells] + selected_cells,
        protocol=protocol, bundle=bundle)
    started = datetime.now(timezone.utc)
    outcome = phase2_canary_runner.run_canary(
        results_path=results,
        decisions_path=decisions,
        client=client,
        reviewer=_ForbiddenReviewer(),
        anchor_judge_model="",
        protocol=dict(protocol),
        bundle=dict(bundle),
        pause_when_unlabeled=True,
        limit=len(selected_cells),
        cells=resolved,
        max_workers=1,
        transcript_generation_forbidden=True,
        namespace=str(protocol["cell_key_namespace"]),
        pending_payload_limit=64,
        role_limits=dict(role_limits),
        fatal_unknown_charge=False,
    )
    finished = datetime.now(timezone.utc)
    events = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
    provider = [e for e in events if e.get("status") in {"reserved", "success", "unknown_charge",
                                                          "charged_malformed",
                                                          "released_no_charge"}]
    calls = []
    for event in provider:
        if event.get("status") == "reserved":
            continue
        reservation = next(
            r for r in provider if r.get("status") == "reserved"
            and r.get("attempt_id") == event.get("attempt_id"))
        elapsed = (_utc(event["ts"]) - _utc(reservation["ts"])).total_seconds()
        meta = event.get("response_metadata") or {}
        calls.append({
            "attempt_id": event.get("attempt_id"),
            "status": event.get("status"),
            "model": event.get("model"),
            "call_role": (event.get("metadata") or {}).get("call_role"),
            "elapsed_seconds": round(elapsed, 3),
            "prompt_tokens": event.get("prompt_tokens"),
            "completion_tokens": event.get("completion_tokens"),
            "finish_reason": meta.get("finish_reason"),
            "cost_usd": event.get("cost_usd"),
            "error": event.get("error"),
        })
    summary = snapshot_summary = api_client.load_chained_usage_ledger(
        ledger, expected_identity=ledger_identity).summary
    record = {
        "schema_version": "phase3_main_long_verdict_probe_v1",
        "record_id": "phase3-main-long-verdict-probe-2026-09-06",
        "purpose": (
            "amendment 14 transport probe: one representative long Qwen3.8 b0 judge verdict "
            "live under the 600-second read timeout of role limits r11"),
        "probe_root": root.as_posix(),
        "role_limits": {
            "path": args.role_limits.resolve().as_posix(),
            "raw_sha256": hashlib.sha256(args.role_limits.resolve().read_bytes()).hexdigest(),
            "read_timeout_seconds": read_timeout,
            "per_call_wall_clock_ceiling_seconds": transport[
                "per_call_wall_clock_ceiling_seconds"],
        },
        "price_snapshot_raw_sha256": hashlib.sha256(
            args.price_snapshot.resolve().read_bytes()).hexdigest(),
        "selection": {
            "algorithm": "qwen_b0_judgments_distinct_questions_largest_transcript_payload_bytes_v2",
            "count": len(chosen),
            "cells": [
                {
                    "cell_key": cell["cell_key"],
                    "question_id": cell["question_id"],
                    "transcript_cell_key": dependency["cell_key"],
                    "transcript_payload_bytes": size,
                }
                for cell, dependency, size in chosen
            ],
        },
        "outcome": {
            "completed": outcome.completed,
            "abandoned": outcome.abandoned,
            "attempted": outcome.attempted,
            "halted_reason": outcome.halted_reason,
            "started_at_utc": started.isoformat(),
            "finished_at_utc": finished.isoformat(),
            "wall_seconds": round((finished - started).total_seconds(), 3),
        },
        "calls": calls,
        "ledger_summary": dict(snapshot_summary),
        "ledger_raw_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
        "journal_raw_sha256": hashlib.sha256(journal_path.read_bytes()).hexdigest()
        if journal_path.exists() else None,
        "results_raw_sha256": hashlib.sha256(results.read_bytes()).hexdigest(),
        "analysis_use": "none; engineering evidence outside every formal identity",
        "execution_authorized": False,
        "main_run_spend_authorized": False,
    }
    record_out.parent.mkdir(parents=True, exist_ok=True)
    record_out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "record": record_out.as_posix(),
        "completed": outcome.completed,
        "abandoned": outcome.abandoned,
        "halted_reason": outcome.halted_reason,
        "calls": calls,
        "summary": dict(summary),
    }, indent=1, default=str))
    return 0 if outcome.completed == len(selected_cells) and outcome.halted_reason is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
