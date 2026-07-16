import json
from pathlib import Path

import pytest

from rejudge import run_accounting
from rejudge.api_client import UsageLedgerError


def _schedule(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({
        "provider": "test serverless",
        "price_verified_at": "2026-07-15",
        "price_source_url": "https://example.test/prices",
        "prices_per_mtok": {
            "model-a": {"in": 0.2, "out": 0.8},
            "model-b": {"in": 1.0, "out": 1.0},
        },
    }), encoding="utf-8")
    return path


def test_price_schedule_is_strict_and_manifest_ready(tmp_path):
    schedule = run_accounting.load_price_schedule(_schedule(tmp_path))
    prices = run_accounting.select_model_prices(schedule, ["model-b", "model-a", "model-a"])
    assert list(prices) == ["model-a", "model-b"]
    assert run_accounting.pricing_identity(schedule, prices) == {
        "mode": "strict_per_model",
        "provider": "test serverless",
        "verified_at": "2026-07-15",
        "source_url": "https://example.test/prices",
        "prices_per_mtok": prices,
        "strict_model_pricing": True,
    }


def test_price_schedule_refuses_unknown_or_invalid_models(tmp_path):
    schedule = run_accounting.load_price_schedule(_schedule(tmp_path))
    with pytest.raises(run_accounting.PriceScheduleError, match="no frozen"):
        run_accounting.select_model_prices(schedule, ["unknown"])
    schedule["prices_per_mtok"]["model-a"]["in"] = float("nan")
    with pytest.raises(run_accounting.PriceScheduleError, match="finite"):
        run_accounting.select_model_prices(schedule, ["model-a"])


class _Usage:
    prompt_tokens = 10
    completion_tokens = 2


class _Response:
    usage = _Usage()

    class _Choice:
        class message:
            content = "ok"

    choices = [_Choice()]


class _SDK:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                return _Response()


def _accounted_client(tmp_path: Path, ledger: Path, identity: dict):
    client, summary = run_accounting.create_accounted_client(
        approved_cap_usd=1.0,
        dry_run=False,
        model_prices={"model-a": {"in": 0.2, "out": 0.8}},
        usage_log_path=ledger,
        error_log_path=tmp_path / "errors.jsonl",
        ledger_identity=identity,
    )
    client._sdk = _SDK()
    return client, summary


def test_accounted_client_reopens_prior_actual_and_unmatched_reservation(tmp_path):
    ledger = tmp_path / "usage.jsonl"
    identity = run_accounting.prepare_usage_ledger(ledger, allow_create=True)
    client, initial = _accounted_client(tmp_path, ledger, identity)
    assert initial["events"] == 0
    client.complete([{"role": "user", "content": "hello"}], "model-a", 0.0, 1, 8)
    client._reserve_attempt(
        model="model-a", prompt_tokens=10, completion_tokens=2,
        kind="verdict", seed=2, attempt=0, request_metadata={"cell_key": "crashed"})

    reopened, summary = _accounted_client(tmp_path, ledger, identity)
    actual = (10 * 0.2 + 2 * 0.8) / 1_000_000
    uncertain = actual
    assert summary["actual_spend_usd"] == pytest.approx(actual)
    assert summary["uncertain_spend_usd"] == pytest.approx(uncertain)
    assert summary["unmatched_reservations"] == 1
    assert reopened.spent_usd == pytest.approx(actual + uncertain)


def test_missing_or_truncated_bound_ledger_is_refused(tmp_path):
    ledger = tmp_path / "usage.jsonl"
    identity = run_accounting.prepare_usage_ledger(ledger, allow_create=True)
    client, _ = _accounted_client(tmp_path, ledger, identity)
    client.complete([{"role": "user", "content": "hello"}], "model-a", 0.0, 1, 8)

    original_lines = ledger.read_text(encoding="utf-8").splitlines(keepends=True)
    ledger.write_text(original_lines[0], encoding="utf-8")
    with pytest.raises(UsageLedgerError, match="truncated"):
        run_accounting.prepare_usage_ledger(ledger, allow_create=False)

    ledger.unlink()
    with pytest.raises(UsageLedgerError, match="missing"):
        run_accounting.prepare_usage_ledger(ledger, allow_create=False)


def test_recreated_ledger_has_new_identity_and_cannot_match_manifest(tmp_path):
    ledger = tmp_path / "usage.jsonl"
    identity = run_accounting.prepare_usage_ledger(ledger, allow_create=True)
    state = ledger.with_name(f"{ledger.name}.state.json")
    ledger.unlink()
    state.unlink()

    replacement = run_accounting.prepare_usage_ledger(ledger, allow_create=True)
    assert replacement != identity
    with pytest.raises(UsageLedgerError, match="does not match"):
        run_accounting.create_accounted_client(
            approved_cap_usd=1.0, dry_run=False,
            model_prices={"model-a": {"in": 0.2, "out": 0.8}},
            usage_log_path=ledger, error_log_path=tmp_path / "errors.jsonl",
            ledger_identity=identity)


def test_fsynced_ledger_ahead_of_state_recovers_forward(tmp_path):
    ledger = tmp_path / "usage.jsonl"
    identity = run_accounting.prepare_usage_ledger(ledger, allow_create=True)
    client, _ = _accounted_client(tmp_path, ledger, identity)
    client.complete([{"role": "user", "content": "hello"}], "model-a", 0.0, 1, 8)

    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    state_path = ledger.with_name(f"{ledger.name}.state.json")
    state_path.write_text(json.dumps({
        "schema_version": identity["schema_version"],
        "ledger_id": identity["ledger_id"],
        "last_sequence": 0,
        "last_event_hash": events[0]["event_hash"],
    }) + "\n", encoding="utf-8")

    assert run_accounting.prepare_usage_ledger(
        ledger, allow_create=False) == identity
    recovered = json.loads(state_path.read_text(encoding="utf-8"))
    assert recovered["last_sequence"] == len(events) - 1
    assert recovered["last_event_hash"] == events[-1]["event_hash"]


def test_dry_client_does_not_create_usage_ledger(tmp_path):
    ledger = tmp_path / "usage.jsonl"
    client, summary = run_accounting.create_accounted_client(
        approved_cap_usd=0.0,
        dry_run=True,
        model_prices={"model-a": {"in": 0.2, "out": 0.8}},
        usage_log_path=ledger,
        error_log_path=tmp_path / "errors.jsonl",
    )
    assert client.dry_run is True
    assert summary["events"] == 0
    assert not ledger.exists()


def test_usage_ledger_generated_paths_names_ledger_state_and_lock(tmp_path):
    ledger = tmp_path / "usage.jsonl"
    assert run_accounting.usage_ledger_generated_paths(ledger) == (
        ledger,
        tmp_path / "usage.jsonl.state.json",
        tmp_path / "usage.jsonl.lock",
    )
