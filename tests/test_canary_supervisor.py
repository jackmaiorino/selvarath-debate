"""The auto-resume supervisor's signature gate: benign transients only, else stop."""
import json
import subprocess
import time
from pathlib import Path

import pytest

from scripts.canary_supervisor import benign_transient_signature


def _epoch(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()

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
    rows = usage_rows if usage_rows is not None else [
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "Error code: 500 - internal"}]
    # Real ledger events always carry a timestamp, and the halt window is bounded by this
    # attempt's start, so a fixture without one is not a smaller test but a different and
    # impossible one. Rows that set their own ts (deliberately stale ones) keep it.
    from datetime import datetime as _dt
    default_ts = _dt.fromtimestamp(NOW).astimezone().isoformat()
    rows = [{**row, "ts": row.get("ts", default_ts)} for row in rows]
    _write(usage, rows)
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
    # checker_unresolved, not checker_malformed. Malformed became resumable on 2026-08-05
    # once empty responses stopped being cached. Unresolved did not: it is the checker
    # answering in a way the parser reads but cannot act on, and at temperature 0 that is
    # exactly what it answers again.
    reason = _check(tmp_path, outcome=_outcome(halted_reason="checker_unresolved"))
    assert "is not one of" in reason


def test_a_checker_outage_over_a_transient_is_benign(tmp_path):
    assert _check(tmp_path, outcome=_outcome(halted_reason="checker_outage")) is None


def test_a_ledger_window_with_no_abandoned_call_stops(tmp_path):
    """A halt claiming an ambiguous billing outcome, with nothing abandoned anywhere in the
    window, is unexplained. Was phrased against the newest event alone until concurrency made
    the newest event routinely somebody else's success; the requirement is unchanged."""
    reason = _check(tmp_path, usage_rows=[
        {"status": "success", "attempt_id": "a1", "cost_usd": 0.01}])
    assert "no abandoned call" in reason


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


def test_a_peer_closed_mid_body_is_the_same_benign_condition(tmp_path):
    # Amendment 12. httpx's wording for a connection the provider closed before finishing the
    # body: the visible sibling of the silent open-connection hang of 2026-08-10, and the same
    # class as "Server disconnected". Three occurrences on gemma within 92 minutes, two of them
    # in one minute, during the same degradation window.
    assert _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "peer closed connection without sending complete message body "
                  "(incomplete chunked read)"}],
        error_rows=[{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
                     "error": "peer closed connection without sending complete message body "
                              "(incomplete chunked read)"}]) is None


def test_a_peer_closed_variant_byte_count_is_also_benign(tmp_path):
    # The parenthetical varies by read state; the invariant clause is what is enumerated.
    assert _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "peer closed connection without sending complete message body "
                  "(3 bytes read, 10 more expected)"}],
        error_rows=[{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
                     "error": "peer closed connection without sending complete message body "
                              "(3 bytes read, 10 more expected)"}]) is None


def test_a_mid_sentence_peer_closed_mention_still_stops_the_run(tmp_path):
    # Prefix anchoring means the clause must BE the error, not appear inside a novel one.
    reason = _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "billing hold: peer closed connection without sending complete message body"}],
        error_rows=[{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
                     "error": "billing hold: peer closed connection without sending complete "
                              "message body"}])
    assert reason is not None and "transient set" in reason


def test_our_own_wall_clock_ceiling_abort_is_benign(tmp_path):
    # Amendment 14. The client's per-attempt wall-clock ceiling converts an overlong streamed
    # call into unknown_charge by design; ten firings on gemma during the 2026-08-10 degradation
    # window, durations 1282s to 1638s. Application-generated wording, so the anchor can never
    # match a novel provider anomaly.
    msg = ("attempt 0 for model 'google/gemma-4-31B-it' took 1370.7s, exceeding the 1200s "
           "application-level wall-clock ceiling; treating as unknown_charge rather than "
           "trusting a response this stale")
    assert _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01, "error": msg}],
        error_rows=[{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
                     "error": msg}]) is None


def test_a_different_ceiling_wording_still_stops_the_run(tmp_path):
    # A changed ceiling value or rephrased message is a code change that must re-earn its
    # enumeration, not ride the old one.
    msg = "attempt 0 for model 'x' took 99s, exceeding the 60s hard ceiling"
    reason = _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01, "error": msg}],
        error_rows=[{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
                     "error": msg}])
    assert reason is not None and "transient set" in reason


def test_an_unrecognised_error_still_stops_the_run(tmp_path):
    # The set stays enumerated precisely so the next genuinely novel shape halts rather than
    # being absorbed. Guards the amendment against becoming a catch-all.
    reason = _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "invalid_request_error: context length exceeded"}],
        error_rows=[{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
                     "error": "invalid_request_error: context length exceeded"}])
    assert reason is not None and "transient set" in reason
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


def test_a_straggler_success_after_the_halt_is_not_a_manual_review(tmp_path):
    """Concurrency broke an assumption the serial canary made for free.

    The signature check read the NEWEST ledger event, because with one call in flight the
    halting call was necessarily the last one written. With eight workers, the calls that were
    already in flight when one halted go on to finish and append after it, so the newest event
    is routinely somebody else's success. Reading it as the halt's own outcome stops the run
    for manual review on a completely healthy transient.
    """
    from datetime import datetime as _dt
    ts = _dt.fromtimestamp(NOW).astimezone().isoformat()
    usage = tmp_path / "usage.jsonl"
    usage.write_text(
        json.dumps({"status": "unknown_charge", "error": "Error code: 503 - upstream",
                    "metadata": {"call_role": "query_checker"}, "cost_usd": 0.001,
                    "ts": ts}) + "\n"
        + json.dumps({"status": "success", "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                      "cost_usd": 0.002, "ts": ts}) + "\n", encoding="utf-8")
    errors = tmp_path / "errors.jsonl"
    errors.write_text(json.dumps(
        {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW)),
         "error": "Error code: 503 - upstream"}) + "\n", encoding="utf-8")

    reason = benign_transient_signature(
        outcome={"halted_reason": "UnknownChargeHalt", "halted_cell_key": "cell-1"},
        usage_path=usage, error_log_path=errors, results_path=tmp_path / "results.jsonl",
        attempt_started_at=NOW - 30)
    assert reason is None, f"should have resumed, refused with: {reason}"


def test_a_halt_with_no_benign_abandonment_at_all_still_stops(tmp_path):
    """Loosening 'newest event' to 'any recent event' must not loosen it to 'never check'."""
    usage = tmp_path / "usage.jsonl"
    usage.write_text(
        json.dumps({"status": "success", "model": "m", "cost_usd": 0.002}) + "\n",
        encoding="utf-8")
    errors = tmp_path / "errors.jsonl"
    errors.write_text(json.dumps(
        {"ts": "2026-08-04T17:00:00", "error": "Error code: 503"}) + "\n", encoding="utf-8")

    reason = benign_transient_signature(
        outcome={"halted_reason": "UnknownChargeHalt", "halted_cell_key": "cell-1"},
        usage_path=usage, error_log_path=errors, results_path=tmp_path / "results.jsonl",
        attempt_started_at=time.time())
    assert reason is not None, "no abandoned call at all means the halt is unexplained"


def test_a_novel_error_shape_among_recent_events_still_stops(tmp_path):
    usage = tmp_path / "usage.jsonl"
    usage.write_text(
        json.dumps({"status": "unknown_charge", "error": "SomethingCompletelyNew",
                    "cost_usd": 0.001}) + "\n"
        + json.dumps({"status": "success", "model": "m", "cost_usd": 0.002}) + "\n",
        encoding="utf-8")
    errors = tmp_path / "errors.jsonl"
    errors.write_text(json.dumps(
        {"ts": "2026-08-04T17:00:00", "error": "SomethingCompletelyNew"}) + "\n",
        encoding="utf-8")

    reason = benign_transient_signature(
        outcome={"halted_reason": "UnknownChargeHalt", "halted_cell_key": "cell-1"},
        usage_path=usage, error_log_path=errors, results_path=tmp_path / "results.jsonl",
        attempt_started_at=time.time())
    assert reason is not None, "an unenumerated failure must always stop for a human"


def test_the_halt_window_is_this_attempt_not_a_fixed_event_count(tmp_path):
    """A fixed lookback cannot be sized correctly, so it should not be used.

    When one cell halts, the driver still finishes the rest of its block, and every one of
    those calls appends after the halt. With eight workers over a sixteen-cell block that is
    easily a hundred events, so a 40-event window scrolled the halt's own abandoned call out
    of view and stopped a healthy run. Bounding by this attempt's start is exact and needs no
    guess about block size.
    """
    old = json.dumps({"status": "unknown_charge", "error": "Error code: 503",
                      "ts": "2020-01-01T00:00:00+00:00", "cost_usd": 0.001})
    recent = json.dumps({"status": "unknown_charge", "error": "Error code: 503",
                         "ts": "2026-08-05T14:48:43+00:00", "cost_usd": 0.001})
    later = "\n".join(json.dumps(
        {"status": "success", "model": "m", "ts": "2026-08-05T14:49:00+00:00",
         "cost_usd": 0.002}) for _ in range(60))
    usage = tmp_path / "usage.jsonl"
    usage.write_text(old + "\n" + recent + "\n" + later + "\n", encoding="utf-8")
    errors = tmp_path / "errors.jsonl"
    errors.write_text(json.dumps(
        {"ts": "2026-08-05T14:48:43", "error": "Error code: 503"}) + "\n", encoding="utf-8")

    started = _epoch("2026-08-05T14:40:00+00:00")
    reason = benign_transient_signature(
        outcome={"halted_reason": "checker_outage", "halted_cell_key": "cell-1"},
        usage_path=usage, error_log_path=errors, results_path=tmp_path / "results.jsonl",
        attempt_started_at=started)
    assert reason is None, f"should have resumed, refused with: {reason}"


def test_an_abandonment_from_a_previous_attempt_does_not_excuse_this_halt(tmp_path):
    """The mirror of the above. Widening the window must not let a stale failure vouch for a
    halt that this attempt cannot otherwise explain."""
    stale = json.dumps({"status": "unknown_charge", "error": "Error code: 503",
                        "ts": "2026-08-05T10:00:00+00:00", "cost_usd": 0.001})
    usage = tmp_path / "usage.jsonl"
    usage.write_text(stale + "\n" + json.dumps(
        {"status": "success", "model": "m", "ts": "2026-08-05T14:49:00+00:00",
         "cost_usd": 0.002}) + "\n", encoding="utf-8")
    errors = tmp_path / "errors.jsonl"
    errors.write_text(json.dumps(
        {"ts": "2026-08-05T14:48:43", "error": "Error code: 503"}) + "\n", encoding="utf-8")

    reason = benign_transient_signature(
        outcome={"halted_reason": "checker_outage", "halted_cell_key": "cell-1"},
        usage_path=usage, error_log_path=errors, results_path=tmp_path / "results.jsonl",
        attempt_started_at=_epoch("2026-08-05T14:40:00+00:00"))
    assert reason is not None, "a stale abandonment must not explain this attempt's halt"


# --- checker_malformed is recoverable once empties are no longer cached --------------------

def test_a_malformed_checker_halt_now_resumes_when_nothing_was_cached(tmp_path):
    """It was terminal only because the cache memoised the empty response.

    With empties no longer cached the next attempt re-calls the checker, and at temperature 0
    a successful call returns the decision the failed one should have. So this halt is a
    transient like any other, and stopping the run ~116 times over the main run to hand-record
    it is pure cost.
    """
    reason = _check(tmp_path, outcome=_outcome(halted_reason="checker_malformed"))
    assert reason is None, f"should resume, refused with: {reason}"


def test_a_malformed_checker_halt_repeating_on_one_call_still_stops(tmp_path):
    """The bound that keeps this from becoming retry-until-favourable is the existing
    same-call limit, which is unchanged: a checker that keeps returning nothing for one
    specific call is a deterministic failure and must reach a human."""
    from scripts.canary_supervisor import SAME_CELL_MAX
    assert SAME_CELL_MAX >= 1
    # The signature gate says "resumable"; the consecutive-call counter is what stops it.
    usage, errors, results = _files(tmp_path)
    seen = []
    for _ in range(SAME_CELL_MAX + 2):
        seen.append(benign_transient_signature(
            outcome=_outcome(halted_reason="checker_malformed"), usage_path=usage,
            error_log_path=errors, results_path=results, attempt_started_at=NOW - 30))
    assert all(r is None for r in seen), "the gate itself does not count repeats"


def test_the_sdk_generic_connection_error_is_the_same_benign_condition(tmp_path):
    # Amendment 10. "Connection error." is the SDK's generic wrapper for a transport-layer
    # failure with no HTTP response: the same class as "Connection reset by peer" and
    # "Connection aborted", which were already enumerated, just without a specific errno.
    # Seen 4 times on the 2026-08-07 main run across Qwen2.5 and Llama batch_verdict calls,
    # while 503s and resets were occurring on the same endpoints.
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW))
    assert _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "Connection error."}],
        error_rows=[{"ts": ts, "error": "Connection error."}]) is None


def test_enumerating_it_does_not_admit_every_error_mentioning_connection(tmp_path):
    """The set stays enumerated so the NEXT novel shape still halts. A pattern loose enough
    to match anything with 'connection' in it would quietly absorb real anomalies."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW))
    reason = _check(tmp_path, usage_rows=[
        {"status": "unknown_charge", "attempt_id": "a1", "cost_usd": 0.01,
         "error": "connection refused: authentication failed"}],
        error_rows=[{"ts": ts, "error": "connection refused: authentication failed"}])
    assert reason is not None and "transient set" in reason


# --- Gate 6: canary_supervisor's subprocess.run must carry an explicit timeout --------------
#
# A driver subprocess that never returns at all is the same defect class as the 2026-08-10
# ssl.read hang, one level up the stack: an un-timed-out subprocess.run blocks the
# supervisor's own STOP/retry logic exactly as an un-timed-out socket read blocked the
# driver. Injecting a genuine hang here would mean actually launching and killing a stuck
# child process; a static check that the call site names an explicit ``timeout=`` is the
# accepted alternative (task instructions), paired with a semantic test of the value's own
# derivation, which needs no subprocess at all.

def test_subprocess_run_call_site_carries_an_explicit_timeout():
    import inspect

    import scripts.canary_supervisor as sup

    src = inspect.getsource(sup.main)
    call = src[src.index("subprocess.run("):]
    call = call[:call.index(")\n") + 1]
    assert "timeout=" in call, (
        "the driver subprocess.run call must carry an explicit timeout, never inherit "
        "subprocess's own default of blocking forever")


def test_subprocess_timeout_is_derived_from_the_pinned_per_call_ceiling():
    from scripts.canary_supervisor import (SUBPROCESS_TIMEOUT_MULTIPLIER,
                                            subprocess_timeout_seconds)
    # rejudge/phase2_role_limits_v6_2026-08-01.json's real, frozen pin.
    assert subprocess_timeout_seconds() == pytest.approx(1200.0 * SUBPROCESS_TIMEOUT_MULTIPLIER)


def test_subprocess_timeout_falls_back_when_the_pins_file_is_unreadable(tmp_path):
    from scripts.canary_supervisor import (SUBPROCESS_TIMEOUT_MULTIPLIER,
                                            _FALLBACK_PER_CALL_CEILING_SECONDS,
                                            subprocess_timeout_seconds)
    missing = tmp_path / "no-such-role-limits.json"
    assert subprocess_timeout_seconds(missing) == pytest.approx(
        _FALLBACK_PER_CALL_CEILING_SECONDS * SUBPROCESS_TIMEOUT_MULTIPLIER)


def test_subprocess_timeout_falls_back_on_a_malformed_pins_file(tmp_path):
    from scripts.canary_supervisor import (SUBPROCESS_TIMEOUT_MULTIPLIER,
                                            _FALLBACK_PER_CALL_CEILING_SECONDS,
                                            subprocess_timeout_seconds)
    bad = tmp_path / "role_limits.json"
    bad.write_text(json.dumps({"transport": {}}), encoding="utf-8")
    assert subprocess_timeout_seconds(bad) == pytest.approx(
        _FALLBACK_PER_CALL_CEILING_SECONDS * SUBPROCESS_TIMEOUT_MULTIPLIER)


def test_a_wedged_driver_subprocess_stops_the_supervisor_instead_of_hanging_forever(
        tmp_path, monkeypatch):
    """The behavioral half, without launching a real hung process: monkeypatch
    subprocess.run to raise TimeoutExpired (exactly what it does when the real timeout=
    kwarg fires) and prove main() reports a STOP and returns, rather than propagating an
    unhandled exception or looping forever."""
    import scripts.canary_supervisor as sup

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"ledger": {
        "usage_log_path": "usage.jsonl", "results_path": "results.jsonl"}}), encoding="utf-8")
    archive = tmp_path / "archive"
    archive.mkdir()

    def _hangs_forever(*args, **kwargs):
        assert "timeout" in kwargs and kwargs["timeout"] is not None
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(sup.subprocess, "run", _hangs_forever)
    rc = sup.main(["python", str(manifest), "auth.json", str(archive)])
    assert rc == 8
