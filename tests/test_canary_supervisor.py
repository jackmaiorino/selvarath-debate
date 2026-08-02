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


# --- which repetition actually indicates a deterministic failure ------------------------

def test_the_repeat_key_distinguishes_calls_within_one_cell(tmp_path):
    # SAME_CELL_MAX exists to catch "a deterministic failure masquerading as a transient".
    # Determinism is a property of a CALL, not of a cell: a judgment cell issues a checker
    # call, one judge query per slot, and a verdict. Counting halts per cell conflated them,
    # so on 2026-08-02, with gemma failing ~45% of calls, four consecutive halts on one cell
    # tripped the guard while every individual call was still succeeding on retry -- one had
    # failed three times at 120s and then returned in four seconds.
    from scripts.canary_supervisor import repeat_key
    usage, _, _ = _files(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "Error code: 500 - internal",
         "metadata": {"cell_key": "plan:kind:abc", "call_role": "query_checker",
                      "query_index": None, "attempt": 1}}])
    checker = repeat_key("plan:kind:abc", usage)

    (tmp_path / "b").mkdir()
    usage2, _, _ = _files(tmp_path / "b", usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a2", "cost_usd": 0.01,
         "error": "Error code: 500 - internal",
         "metadata": {"cell_key": "plan:kind:abc", "call_role": "judge_query",
                      "query_index": 0, "attempt": 1}}])
    judge = repeat_key("plan:kind:abc", usage2)

    assert checker != judge, "distinct calls in one cell must not accumulate together"
    assert "plan:kind:abc" in checker and "query_checker" in checker


def test_the_same_call_repeating_still_accumulates(tmp_path):
    # The safety property is preserved exactly: one call failing over and over still yields
    # one key, so it still trips the limit and still stops the run for manual review.
    from scripts.canary_supervisor import repeat_key
    rows = [{"status": "unknown_charge", "attempt_id": f"a{n}", "cost_usd": 0.01,
             "error": "Error code: 500 - internal",
             "metadata": {"cell_key": "plan:kind:abc", "call_role": "judge_query",
                          "query_index": 2, "attempt": 1}} for n in range(3)]
    keys = set()
    for n in range(3):
        (tmp_path / f"r{n}").mkdir()
        usage, _, _ = _files(tmp_path / f"r{n}", usage_rows=rows[: n + 1])
        keys.add(repeat_key("plan:kind:abc", usage))
    assert len(keys) == 1


def test_the_repeat_key_falls_back_to_the_cell_without_metadata(tmp_path):
    # A halt whose newest ledger event carries no metadata must not silently become
    # un-countable; falling back to the cell restores exactly the old behaviour.
    from scripts.canary_supervisor import repeat_key
    usage, _, _ = _files(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "Error code: 500 - internal"}])
    assert repeat_key("plan:kind:abc", usage) == "plan:kind:abc"
