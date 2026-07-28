"""The auto-resume supervisor's signature gate: benign transients only, else stop."""
import json
import time
from pathlib import Path

from scripts.canary_supervisor import benign_transient_signature

NOW = time.time()


def _outcome(**overrides):
    outcome = {"halted_reason": "UnknownChargeHalt", "halted_cell_key": "plan:kind:abc"}
    outcome.update(overrides)
    return outcome


def _write(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _files(tmp_path, *, usage_rows=None, error_rows=None, result_rows=None):
    usage = tmp_path / "usage.jsonl"
    errors = tmp_path / "errors.jsonl"
    results = tmp_path / "results.jsonl"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW))
    _write(usage, usage_rows if usage_rows is not None else [
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "Error code: 500 - internal"}])
    _write(errors, error_rows if error_rows is not None else [
        {"ts": ts, "error": "Error code: 500 - internal"}])
    if result_rows is not None:
        _write(results, result_rows)
    return usage, errors, results


def _check(tmp_path, outcome=None, **kwargs):
    usage, errors, results = _files(tmp_path, **kwargs)
    return benign_transient_signature(
        outcome=outcome or _outcome(), usage_path=usage, error_log_path=errors,
        results_path=results, attempt_started_at=NOW - 30)


def test_the_benign_signature_passes(tmp_path):
    assert _check(tmp_path) is None


def test_a_non_unknown_charge_halt_stops(tmp_path):
    reason = _check(tmp_path, outcome=_outcome(halted_reason="CanaryCellHalted"))
    assert "not UnknownChargeHalt" in reason


def test_a_ledger_tail_that_is_not_unknown_charge_stops(tmp_path):
    reason = _check(tmp_path, usage_rows=[
        {"status": "success", "attempt_id": "a1", "cost_usd": 0.01}])
    assert "not an unknown_charge" in reason


def test_a_non_5xx_envelope_stops(tmp_path):
    reason = _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "Error code: 429 - rate limited"}])
    assert "5xx" in reason


def test_a_stale_error_log_stops(tmp_path):
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 3600))
    reason = _check(tmp_path, error_rows=[{"ts": old, "error": "Error code: 500 - x"}])
    assert "predates" in reason


def test_a_half_recorded_cell_stops(tmp_path):
    reason = _check(tmp_path, result_rows=[{"cell_key": "plan:kind:abc"}])
    assert "already has a result row" in reason
