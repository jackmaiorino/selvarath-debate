"""Offline transport/recovery checks for the bounded captured-history runner."""
from collections import Counter
from contextlib import suppress
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from rejudge import phase4_runner as runner
from rejudge.api_client import _estimate_usage, estimate_context_tokens


PRICES = {runner.QWEN: {"input": 2.0, "output": 6.0},
          runner.LLAMA: {"input": 1.04, "output": 1.04}}
VALID = "VERDICT: Position A\nCONFIDENCE: 3\nREASONING: The supplied fact supports A."


def panel(count=1, judge=runner.LLAMA):
    calls, packets = [], {}
    for index in range(count):
        messages = [{"role": "system", "content": "Select the supported position."},
                    {"role": "user", "content": "A: a blue bridge. B: a red bridge. Fact: blue."}]
        digest = hashlib.sha256(runner.canonical(messages).encode()).hexdigest()
        cell = f"offline:{judge}:{index}"
        packets[cell] = {"messages": messages, "messages_sha256": digest,
                         "source_cell_key": "private-donor-key", "gold": "DO_NOT_SEND_GOLD"}
        calls.append({"cell_id": cell, "unit_id": f"unit-{index}", "judge": judge,
                      "arm": "empty", "packet_id": cell, "messages_sha256": digest,
                      "temperature": 0.3, "max_tokens": 16384 if judge == runner.QWEN else 512,
                      "seed": 4100 + index, "stream": False})
    return calls, packets


def outcome(call, content=VALID, *, model=None, prompt_tokens=20, completion_tokens=10):
    return {"response": {"id": "offline-response", "model": model or call["judge"],
                         "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
                         "choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
            "duration_seconds": 0.001}


@pytest.fixture
def stores(tmp_path):
    opened = []

    def make(name="run", limits=None, identity=None):
        value = runner.Store(tmp_path / name, identity or {"run_id": "offline-phase4"},
                             limits or {"total": runner.money(250), "uncertain": runner.money(25),
                                        "preflight": runner.money(5)})
        opened.append(value)
        return value

    yield make
    for store in opened:
        with suppress(Exception):
            store.db.close()


def execute(store, calls, packets, provider, stage="main", **kwargs):
    return runner.execute(store, calls, packets, stage, PRICES, provider,
                          max_workers=kwargs.pop("max_workers", 1), cooldown_seconds=0.001,
                          cycle_seconds=0.003, poll_seconds=0.001, tune_seconds=1000, **kwargs)


def test_interrupted_inflight_is_unknown_once_and_successful_retry_keeps_charge(stores):
    calls, packets = panel()
    call = calls[0]
    store = stores()
    attempt, reason = store.reserve(call, packets[call["packet_id"]], "main", PRICES)
    assert reason is None
    reserved = store.totals()["inflight"]
    store.db.close()

    reopened = stores()
    assert reopened.recover() == 1
    assert reopened.totals() == {"actual": 0, "uncertain": reserved, "inflight": 0,
                                "exposure": reserved, "preflight": 0, "preflight_attempts": 0}
    reopened.db.close()
    restarted = stores()
    assert restarted.recover() == 0
    observed = []
    status = execute(restarted, calls, packets, lambda c, p: observed.append(c["cell_id"]) or outcome(c))
    assert observed == [call["cell_id"]]
    assert status["state"] == "requests_complete"
    assert restarted.totals()["uncertain"] == reserved
    assert restarted.totals()["exposure"] == reserved + runner.token_cost(20, 10, PRICES[call["judge"]])
    attempts = [dict(r) for r in restarted.db.execute("SELECT * FROM attempts ORDER BY started_at")]
    assert len(attempts) == 2 and attempts[0]["attempt_id"] == attempt
    assert [r["state"] for r in attempts] == ["unknown", "success"]
    assert attempts[0]["reserved"] == attempts[0]["uncertain"] == reserved


@pytest.mark.parametrize("content", [VALID, "", "No parseable verdict is supplied."])
def test_completed_response_including_empty_and_malformed_is_never_regenerated(stores, content):
    calls, packets = panel()
    store = stores()
    observed = []
    status = execute(store, calls, packets, lambda c, p: observed.append(c["cell_id"]) or outcome(c, content))
    assert status["state"] == "requests_complete"
    before = (store.directory / "results.jsonl").read_bytes()
    rows = store.rows("main")
    assert rows[0]["raw_verdict_text"] == content
    store.db.close()
    resumed = stores()
    status = execute(resumed, calls, packets, lambda *_: pytest.fail("Completed call was regenerated"))
    assert status["state"] == "requests_complete"
    assert observed == [calls[0]["cell_id"]]
    assert (resumed.directory / "results.jsonl").read_bytes() == before
    assert resumed.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1


@pytest.mark.parametrize("content,expected", [(VALID, True), ("", False), ("I decline to choose.", False)])
def test_finish_reports_actual_strict_parse_validity(stores, content, expected):
    calls, packets = panel()
    store = stores()
    attempt, _ = store.reserve(calls[0], packets[calls[0]["packet_id"]], "preflight", PRICES)
    assert store.finish(attempt, calls[0], outcome(calls[0], content), PRICES)["strict_valid"] is expected


@pytest.mark.parametrize("cap_name", ["total", "uncertain"])
def test_reservations_enforce_cap_inclusive_of_other_inflight_calls(stores, cap_name):
    calls, packets = panel(3)
    amount = runner.reservation(calls[0], packets[calls[0]["packet_id"]], PRICES)
    limits = {"total": runner.money(250), "uncertain": runner.money(25), "preflight": runner.money(5)}
    limits[cap_name] = amount * 2
    store = stores(limits=limits)
    assert store.reserve(calls[0], packets[calls[0]["packet_id"]], "main", PRICES)[1] is None
    assert store.reserve(calls[1], packets[calls[1]["packet_id"]], "main", PRICES)[1] is None
    assert store.totals()["exposure"] == 2 * amount
    assert store.reserve(calls[2], packets[calls[2]["packet_id"]], "main", PRICES) == (None, cap_name + "_cap")
    assert store.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 2


@pytest.mark.parametrize("cap_name", ["total", "uncertain", "preflight"])
def test_unknown_delivery_still_consumes_each_applicable_cap(stores, cap_name):
    calls, packets = panel(2)
    amount = runner.reservation(calls[0], packets[calls[0]["packet_id"]], PRICES)
    limits = {"total": runner.money(250), "uncertain": runner.money(25), "preflight": runner.money(5)}
    limits[cap_name] = amount
    store = stores(limits=limits)
    stage = "preflight" if cap_name == "preflight" else "main"
    attempt, _ = store.reserve(calls[0], packets[calls[0]["packet_id"]], stage, PRICES)
    store.finish(attempt, calls[0], {"error": "offline transport failure"}, PRICES)
    assert store.totals()["uncertain"] == amount
    assert store.reserve(calls[1], packets[calls[1]["packet_id"]], stage, PRICES) == (None, cap_name + "_cap")


def test_preflight_counts_unknown_attempts_toward_sixteen_attempt_limit(stores):
    calls, packets = panel()
    store = stores()
    for _ in range(16):
        attempt, reason = store.reserve(calls[0], packets[calls[0]["packet_id"]], "preflight", PRICES)
        assert reason is None
        store.finish(attempt, calls[0], {"error": "synthetic timeout"}, PRICES)
    store.db.close()
    resumed = stores()
    assert resumed.totals()["preflight_attempts"] == 16
    assert resumed.reserve(calls[0], packets[calls[0]["packet_id"]], "preflight", PRICES) == (None, "preflight_cap")
    assert resumed.reserve(calls[0], packets[calls[0]["packet_id"]], "main", PRICES)[1] is None


def test_execute_mixed_models_retries_failed_calls_without_repeating_successes(stores):
    calls, packets = panel(2)
    qcalls, qpackets = panel(1, runner.QWEN)
    calls += qcalls
    packets.update(qpackets)
    store = stores()
    attempts = Counter()
    mutex = threading.Lock()
    fail_once = {calls[0]["cell_id"], qcalls[0]["cell_id"]}

    def provider(call, packet):
        with mutex:
            attempts[call["cell_id"]] += 1
            current = attempts[call["cell_id"]]
        if call["cell_id"] in fail_once and current == 1:
            return {"error": "offline503", "fatal": False, "duration_seconds": 0.001}
        return outcome(call)

    status = execute(store, calls, packets, provider)
    assert status["state"] == "requests_complete"
    assert attempts == Counter({calls[0]["cell_id"]: 2, calls[1]["cell_id"]: 1, qcalls[0]["cell_id"]: 2})
    assert len(store.completed("main")) == 3
    states = Counter(row[0] for row in store.db.execute("SELECT state FROM attempts"))
    assert states == {"success": 3, "unknown": 2}
    expected_unknown = sum(runner.reservation(c, packets[c["packet_id"]], PRICES) for c in calls if c["cell_id"] in fail_once)
    assert store.totals()["uncertain"] == expected_unknown
    events = [json.loads(row[0]) for row in store.db.execute("SELECT payload FROM events")]
    assert sum(e["kind"] == "transport_failure" for e in events) == 2


def test_inflight_reservation_shortage_waits_for_settlement_then_finishes(stores):
    calls, packets = panel(3)
    amount = runner.reservation(calls[0], packets[calls[0]["packet_id"]], PRICES)
    charge = runner.token_cost(20, 10, PRICES[runner.LLAMA])
    store = stores(limits={"total": amount + 4 * charge, "uncertain": runner.money(25), "preflight": runner.money(5)})
    observed = []

    def provider(call, packet):
        observed.append(call["cell_id"])
        time.sleep(0.005)
        return outcome(call)

    status = execute(store, calls, packets, provider, max_workers=2)
    assert status["state"] == "requests_complete"
    assert Counter(observed) == Counter(c["cell_id"] for c in calls)
    assert store.totals()["actual"] == charge * 3 and store.totals()["inflight"] == 0


def test_provider_boundary_sends_only_frozen_request_fields_and_messages():
    calls, packets = panel()
    call, packet = calls[0], packets[calls[0]["packet_id"]]
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(model_dump=lambda **_: outcome(call)["response"])

    provider = object.__new__(runner.Provider)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = provider(call, packet)
    assert result["response"]["model"] == call["judge"]
    assert len(sent) == 1
    assert set(sent[0]) == {"model", "messages", "temperature", "max_tokens", "seed"}
    assert sent[0]["messages"] == packet["messages"]
    serialized = json.dumps(sent[0])
    for forbidden in ("DO_NOT_SEND_GOLD", "private-donor-key", "messages_sha256", "unit_id", "packet_id"):
        assert forbidden not in serialized


def test_provider_classifies_transport_failure_without_logging_exception_content():
    calls, packets = panel()

    class OfflineUnavailable(Exception):
        status_code = 503
        response = SimpleNamespace(headers={"retry-after": "0.005"})

    def create(**_kwargs):
        raise OfflineUnavailable("SENSITIVE_EXCEPTION_TEXT_MUST_NOT_BE_LOGGED")

    provider = object.__new__(runner.Provider)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = provider(calls[0], packets[calls[0]["packet_id"]])
    assert result["fatal"] is False and result["error"] == "OfflineUnavailable HTTP 503"
    assert result["retry_after_seconds"] == 0.005
    assert "SENSITIVE_EXCEPTION_TEXT" not in json.dumps(result)


def test_completed_response_with_missing_usage_retains_full_reserve_without_resampling(stores):
    calls, packets = panel()
    store = stores()
    response = outcome(calls[0])
    response["response"]["usage"] = None
    reserved = runner.reservation(calls[0], packets[calls[0]["packet_id"]], PRICES)
    status = execute(store, calls, packets, lambda *_: response)
    assert status["state"] == "requests_complete"
    assert store.rows("main")[0]["raw_verdict_text"] == VALID
    assert store.totals()["actual"] == 0 and store.totals()["uncertain"] == reserved
    store.db.close()
    resumed = stores()
    status = execute(resumed, calls, packets, lambda *_: pytest.fail("Completed response with unknown billing was resampled"))
    assert status["state"] == "requests_complete"
    assert resumed.totals()["uncertain"] == reserved
    assert resumed.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1


def test_fatal_transport_error_is_durable_and_retains_full_uncertainty(stores):
    calls, packets = panel()
    store = stores()
    reserved = runner.reservation(calls[0], packets[calls[0]["packet_id"]], PRICES)
    status = execute(store, calls, packets, lambda *_: {"error": "OfflineUnauthorized HTTP 401", "fatal": True})
    assert status["state"] == "paused" and store.completed("main") == set()
    assert store.totals()["uncertain"] == reserved
    store.db.close()
    resumed = stores()
    status = execute(resumed, calls, packets, lambda *_: pytest.fail("Persistent unauthorized transport stop retried"))
    assert status["state"] == "paused" and status.get("halt_reason")
    assert resumed.totals()["uncertain"] == reserved
    assert resumed.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 1


def test_reasoning_reservation_is_tripled_but_context_total_is_not_double_counted():
    calls, packets = panel(1, runner.QWEN)
    call, packet = calls[0], packets[calls[0]["packet_id"]]
    prompt, completion = _estimate_usage(packet["messages"], call["max_tokens"])
    assert runner.reservation(call, packet, PRICES) == runner.token_cost(prompt, completion * 3, PRICES[runner.QWEN])
    # A request fitting the frozen context limit must not be rejected by adding E_prompt twice.
    long_packet = {"messages": [{"role": "user", "content": "x" * 180_000}]}
    estimated_prompt, estimated_total = estimate_context_tokens(long_packet["messages"], call["max_tokens"])
    assert estimated_total < 131072 < estimated_prompt + estimated_total
    assert runner.reservation(call, long_packet, PRICES) > 0


@pytest.mark.parametrize("failure", ["model_mismatch", "reservation_underestimated"])
def test_fatal_response_stop_survives_restart_and_cannot_become_complete(stores, failure):
    calls, packets = panel()
    store = stores()
    call = calls[0]
    bad = outcome(call, model="unexpected/model") if failure == "model_mismatch" else outcome(call, prompt_tokens=1_000_000)
    status = execute(store, calls, packets, lambda *_: bad)
    assert status["state"] == "paused"
    assert len(store.rows("main")) == 1
    before = (store.directory / "results.jsonl").read_bytes()
    actual = store.totals()["actual"]
    assert actual > 0
    if failure == "model_mismatch":
        assert store.rows("main")[0]["configuration_valid"] is False
    else:
        reserved = store.db.execute("SELECT reserved FROM attempts").fetchone()[0]
        assert actual > reserved
    store.db.close()
    resumed = stores()
    status = execute(resumed, calls, packets, lambda *_: pytest.fail("Fatal saved response was regenerated"))
    assert status["state"] == "paused", "A persisted fatal response must never become requests_complete on restart"
    assert status.get("halt_reason")
    assert (resumed.directory / "results.jsonl").read_bytes() == before
    assert resumed.totals()["actual"] == actual


def test_single_run_lock_refuses_second_owner_then_releases(tmp_path):
    code = "from pathlib import Path\nfrom rejudge.phase4_runner import run_lock\nimport sys\nwith run_lock(Path(sys.argv[1])):\n print('acquired')\n"
    command = [sys.executable, "-B", "-c", code, str(tmp_path / "lock-test")]
    with runner.run_lock(tmp_path / "lock-test"):
        refused = subprocess.run(command, cwd=runner.ROOT, capture_output=True, text=True, timeout=20)
        assert refused.returncode != 0 and "acquired" not in refused.stdout
    admitted = subprocess.run(command, cwd=runner.ROOT, capture_output=True, text=True, timeout=20)
    assert admitted.returncode == 0 and admitted.stdout.strip() == "acquired"


def test_store_refuses_different_run_identity(stores):
    store = stores(identity={"run_id": "original"})
    store.db.close()
    with pytest.raises(ValueError, match="identity differs"):
        stores(identity={"run_id": "replacement"})


@pytest.mark.parametrize("content,expected_rc", [(VALID, 0), ("", 2), ("No strict verdict.", 2)])
def test_main_preflight_requires_real_strict_verdicts(tmp_path, monkeypatch, content, expected_rc):
    source, inputs, run_dir = tmp_path / "source", tmp_path / "inputs", tmp_path / "run"
    for relative in ("scripts/phase4_analysis.py", "rejudge/phase4_runner.py", "rejudge/api_client.py",
                     "rejudge/parsers.py", "analysis/infra/parsing.py", "rejudge/durable_fs.py"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# offline fixture\n", encoding="utf-8")
    inputs.mkdir()
    (inputs / "manifest.json").write_text('{"outputs":{}}')
    authorization = tmp_path / "authorization.json"
    authorization.write_text('{"offline":true}')
    auth = {"run_id": "offline-main-entrypoint", "protocol_sha256": "a" * 64, "prices_per_million": PRICES}
    monkeypatch.setattr(runner, "ROOT", source)
    monkeypatch.setattr(runner, "verify_authorization", lambda *_: auth)
    monkeypatch.setattr(runner, "load_panel", lambda *_: ([], {}))
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setattr(runner.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(runner, "Provider", lambda: (lambda c, p: outcome(c, content)))
    original_execute = runner.execute

    def fast_execute(*args, **kwargs):
        return original_execute(*args, poll_seconds=0.001, cooldown_seconds=0.001, cycle_seconds=0.003, **kwargs)

    monkeypatch.setattr(runner, "execute", fast_execute)
    monkeypatch.setattr(sys, "argv", ["phase4_runner", "--inputs", str(inputs), "--run-dir", str(run_dir),
                                      "--authorization", str(authorization), "--mode", "preflight"])
    assert runner.main() == expected_rc
    receipt = json.loads((run_dir / "preflight.json").read_bytes())
    assert receipt["status"] == ("passed" if expected_rc == 0 else "not_passed")
    assert receipt["completed"] == 6
    assert not (run_dir / "completion.json").exists()
