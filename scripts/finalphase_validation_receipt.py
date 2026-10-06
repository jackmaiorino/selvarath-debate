"""Refresh the existing private manifest and verify a zero-dispatch author replay."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from finalphase import cli, preflight, validation as V


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--openai-credit", type=float)
    parser.add_argument("--together-credit", type=float)
    parser.add_argument("--openai-spend-limit", type=float)
    parser.add_argument("--openai-queue-input-tokens", type=int)
    parser.add_argument("--reference")
    args = parser.parse_args()
    root = cli.RUN_ROOT
    path = root / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    previous = root / "preserved" / "pre-token-bounded-validation"
    previous.mkdir(parents=True, exist_ok=True)
    for name in ("run_manifest.json", "validation_reservation.json", "provider_funding_plan.json"):
        saved = previous / name
        if not saved.exists():
            saved.write_bytes((root / name).read_bytes())
    execution = manifest["validation_execution"]
    if args.prepare:
        if any(value is None for value in (args.openai_credit, args.together_credit,
                   args.openai_spend_limit, args.openai_queue_input_tokens, args.reference)):
            parser.error("prepare requires current reported balances, spend/queue limits and their reference")
        if (root / "validate.db").exists():
            raise RuntimeError("do not replace stage-start funding after requests are registered")
        execution["account_balances_at_stage_start"].update({
            "openai": {"available_usd": args.openai_credit, "reference": args.reference},
            "together": {"available_usd": args.together_credit, "reference": args.reference}})
        # Keep the account setting supplied by Jack. This is an estimate of
        # remaining headroom from our ledger, not a provider monthly-usage query.
        known_openai = manifest["quality_review"]["accounting"]["provider_authoring_usd"]["openai"]
        known_openai += manifest["quality_review"]["accounting"]["access_check_usd"]["openai"]
        execution["openai_account_limits"] = {
            "monthly_limit_usd": args.openai_spend_limit, "monthly_remaining_usd": args.openai_spend_limit - known_openai,
            "remaining_allowance_basis": "Reported effective $120 spend limit minus known October author/access ledger; unrelated account usage cannot be queried with this key",
            "project_hard_limit_enabled": True, "project_remaining_usd": args.openai_spend_limit - known_openai,
            "project_limit_basis": "Using Jack's reported current effective spend limit; scope not separately identified",
            "queue_confirmed": True, "batch_queue_input_tokens": args.openai_queue_input_tokens,
            "requests_per_minute": 10000, "tokens_per_minute": 4000000,
            "reference": args.reference,
            "tier": "not inferred from deposits; displayed limits are authoritative",
            "purchase_limits": "not exposed to this API key; Jack completed the deposit; no further purchase required"}
        execution["batch_limits"] = {key: {"max_input_tokens": min(1500000, args.openai_queue_input_tokens),
            "max_requests": 100, "max_in_flight": 1} for key in ("astra", "fable")}
        execution["status"] = "funded_preparing_qualification"
        execution["next_action"] = "Run guarded real qualification after affected checks pass"
        execution["next_wake_condition"] = "Qualification completes or reports an error"
        manifest["git_commit"] = preflight.source_commit()
        V.atomic_json(path, manifest)
    worlds = cli._worlds()
    reservation = V.plan(root, worlds)
    original = json.loads((previous / "validation_reservation.json").read_text(encoding="utf-8"))
    if reservation["validation_input_sha256"] != original["validation_input_sha256"]:
        raise RuntimeError("frozen validation request bodies changed")
    V.atomic_json(root / "validation_reservation.json", reservation)
    source = importlib.util.spec_from_file_location("length_receipt", REPO / "scripts/finalphase_length_correction.py")
    assert source is not None and source.loader is not None
    helper = importlib.util.module_from_spec(source)
    source.loader.exec_module(helper)
    replay = helper.replay(helper.ledger()[0])
    V.atomic_json(root / "validation_author_replay.json", replay)
    source_db = sqlite3.connect((root / "author.db").resolve().as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(previous / "author.db")
    try:
        source_db.backup(destination)
    finally:
        destination.close()
        source_db.close()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["git_commit"] = preflight.source_commit()
    execution = manifest["validation_execution"]
    execution.update(execution_sha256=preflight.execution_sha256(),
        maximum_reserved_cost_usd=reservation["maximum_cumulative_stage_cost_usd"],
        funding_gap_by_provider_usd=reservation["credit_gap_by_provider_usd"],
        delivery_git_commit=manifest["git_commit"])
    manifest["verification"]["validation_token_wave_preparation"] = {
        "cached_author_replay": replay, "frozen_validation_bodies_preserved": True,
        "validation_input_sha256": reservation["validation_input_sha256"]}
    for artifact in list((REPO / "finalphase").glob("*.py")) + [REPO / "uv.lock", REPO / "pyproject.toml"]:
        manifest["inputs_sha256"][artifact.relative_to(REPO).as_posix()] = V.sha(artifact.read_bytes())
    for name in ("validation_reservation.json", "validation_author_replay.json"):
        manifest["outputs_sha256"][name] = V.sha((root / name).read_bytes())
    V.atomic_json(path, manifest)
    print(json.dumps({"reservation": reservation, "replay": replay}, indent=2))


if __name__ == "__main__":
    main()
