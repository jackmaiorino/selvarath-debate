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


def test_a_non_transient_halt_reason_stops(tmp_path):
    reason = _check(tmp_path, outcome=_outcome(halted_reason="checker_malformed"))
    assert "neither UnknownChargeHalt nor checker_outage" in reason


def test_a_checker_outage_over_a_transient_is_benign(tmp_path):
    assert _check(tmp_path, outcome=_outcome(halted_reason="checker_outage")) is None


def test_a_ledger_tail_that_is_not_unknown_charge_stops(tmp_path):
    reason = _check(tmp_path, usage_rows=[
        {"status": "success", "attempt_id": "a1", "cost_usd": 0.01}])
    assert "not an unknown_charge" in reason


def test_a_rate_limit_is_benign(tmp_path):
    import time as _t
    ts = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime(NOW))
    reason = _check(
        tmp_path,
        usage_rows=[{"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
                     "error": "Error code: 429 - rate limited"}],
        error_rows=[{"ts": ts, "error": "Error code: 429 - rate limited"}])
    assert reason is None


def test_an_unrecognized_error_still_stops(tmp_path):
    reason = _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "Error code: 401 - unauthorized"}])
    assert "not in the enumerated transient set" in reason


def test_an_sdk_timeout_is_benign(tmp_path):
    import time as _t
    ts = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime(NOW))
    reason = _check(
        tmp_path,
        usage_rows=[{"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
                     "error": "Request timed out."}],
        error_rows=[{"ts": ts, "error": "Request timed out."}])
    assert reason is None


def test_a_truncated_stream_is_benign(tmp_path):
    import time as _t
    ts = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime(NOW))
    err = "streaming response ended without usage chunk"
    reason = _check(
        tmp_path,
        usage_rows=[{"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
                     "error": err}],
        error_rows=[{"ts": ts, "error": err}])
    assert reason is None


def test_a_connection_reset_is_benign(tmp_path):
    import time as _t
    ts = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.localtime(NOW))
    err = "[Errno 104] Connection reset by peer"
    reason = _check(
        tmp_path,
        usage_rows=[{"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
                     "error": err}],
        error_rows=[{"ts": ts, "error": err}])
    assert reason is None


def test_a_stale_error_log_stops(tmp_path):
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 3600))
    reason = _check(tmp_path, error_rows=[{"ts": old, "error": "Error code: 500 - x"}])
    assert "predates" in reason


def test_a_half_recorded_cell_stops(tmp_path):
    reason = _check(tmp_path, result_rows=[{"cell_key": "plan:kind:abc"}])
    assert "already has a result row" in reason


def test_a_socket_level_read_timeout_is_the_same_benign_condition(tmp_path):
    # Amendment 8. Together surfaced a read timeout through the socket layer rather than the
    # SDK's own wording: "The read operation timed out" instead of "Request timed out.". The
    # 2026-08-02 instance waited 126s against the pinned 120s read timeout, so it is that
    # timeout firing, not a new failure mode. Enumerating it keeps the set exhaustive without
    # widening what counts as benign.
    assert _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "The read operation timed out"}],
        error_rows=[{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
                     "error": "The read operation timed out"}]) is None


def test_an_unrecognised_error_still_stops_the_run(tmp_path):
    # The set stays enumerated precisely so the next genuinely novel shape halts rather than
    # being absorbed. Guards the amendment against becoming a catch-all.
    reason = _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "invalid_request_error: context length exceeded"}],
        error_rows=[{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
                     "error": "invalid_request_error: context length exceeded"}])
    assert reason is not None and "transient set" in reason
