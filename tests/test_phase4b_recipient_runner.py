"""Offline recipient request, recovery, authorization, and hidden-launch tests."""
from collections import Counter
from contextlib import suppress
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from rejudge import phase4_runner as phase4a
from rejudge import phase4b_recipient_runner as r
from rejudge.api_client import _estimate_usage, estimate_context_tokens


SETTINGS = {j: {"temperature": .3, "max_tokens": r.OUTPUT_LIMITS[j], "context_length": 1010000 if j == r.QWEN else 131072,
                "reservation_multiplier": r.MULTIPLIERS[j], "initial_workers": 1, "max_workers": 1} for j in r.MODELS}
PRICES = {r.QWEN: {"input": 2, "output": 6}, r.LLAMA: {"input": 1.04, "output": 1.04}}
LIMITS = {"total": r.money(150), "uncertain": r.money(25), "preflight": r.money(5)}
VALID = "VERDICT: Position A\nCONFIDENCE: 3\nREASONING: The bridge is blue."


def panel(units=1):
    calls, packets, unit_rows = [], {}, []
    for index in range(units):
        uid = f"unit-{index}"
        unit_rows.append({"unit_id": uid, "gold": "PRIVATE_GOLD"})
        for arm in r.ARMS:
            pid = uid + ":" + arm
            messages = [{"role": "system", "content": "Select the supported position."},
                        {"role": "user", "content": "World: blue bridge. Position A: blue. Position B: red."},
                        {"role": "assistant", "content": "CLAIM: The bridge is blue."},
                        {"role": "user", "content": "ORACLE: YES"}]
            packets[pid] = {"packet_id": pid, "messages": messages, "messages_sha256": r.recovery.digest(messages),
                            "private_gold": "DO_NOT_SEND", "changed_label_spans": [{"private_metadata": "DO_NOT_SEND"}]}
            for judge in r.MODELS:
                calls.append({"cell_id": uid + ":" + judge + ":" + arm, "unit_id": uid, "judge": judge, "arm": arm,
                              "packet_id": pid, "messages_sha256": packets[pid]["messages_sha256"],
                              "seed": r.verdict_seed(uid, judge), "temperature": .3,
                              "max_tokens": r.OUTPUT_LIMITS[judge], "stream": False})
    return calls, packets, unit_rows


def outcome(call, text=VALID, *, model=None, usage=None):
    return {"response": {"model": model or call["judge"], "id": "offline-response",
                         "usage": {"prompt_tokens": 10, "completion_tokens": 12} if usage is None else usage,
                         "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}, "duration_seconds": .001}


@pytest.fixture
def stores(tmp_path):
    opened = []
    def make(name="run", limits=None, settings=None):
        value = r.Store(tmp_path / name, {"run_id": "offline-recipient", "stage": "4B-recipient"},
                        limits or LIMITS, settings or copy.deepcopy(SETTINGS))
        opened.append(value)
        return value
    yield make
    for value in opened:
        with suppress(Exception):
            value.db.close()


def execute(store, calls, packets, provider, stage="main", **kwargs):
    return r.execute(store, calls, packets, stage, PRICES, provider,
                     cooldown_seconds=.001, cycle_seconds=.003, poll_seconds=.001, **kwargs)


def test_request_kwargs_are_exact_phase4a_and_never_send_metadata():
    calls, packets, _ = panel()
    for call in calls:
        packet = packets[call["packet_id"]]
        actual = r.request_kwargs(call, packet)
        assert actual == phase4a.request_kwargs(call, packet)
        assert set(actual) == {"model", "messages", "temperature", "max_tokens", "seed"}
        assert "PRIVATE" not in r.canonical(actual) and "DO_NOT_SEND" not in r.canonical(actual)
        assert actual["max_tokens"] == (16384 if call["judge"] == r.QWEN else 512)
    assert r.Provider is phase4a.Provider


def test_provider_boundary_retains_exact_absent_fields():
    calls, packets, _ = panel()
    for call in calls[:2]:
        observed = []
        def create(**kwargs):
            observed.append(kwargs)
            return SimpleNamespace(model_dump=lambda **_: outcome(call)["response"])
        provider = r.Provider.__new__(r.Provider)
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        assert provider(call, packets[call["packet_id"]])["response"]["model"] == call["judge"]
        assert observed == [phase4a.request_kwargs(call, packets[call["packet_id"]])]


def test_crash_recovery_preserves_completed_and_unknown_attempts_exactly_once(stores):
    calls, packets, _ = panel()
    store = stores()
    completed = calls[0]
    attempt, _ = store.reserve(completed, packets[completed["packet_id"]], "main", PRICES)
    store.finish(attempt, completed, outcome(completed, ""), PRICES)
    completed_before = store.rows("main")[0]
    interrupted = calls[1]
    old_attempt, _ = store.reserve(interrupted, packets[interrupted["packet_id"]], "main", PRICES)
    reserved = store.totals()["inflight"]
    store.db.close()
    resumed = stores()
    assert resumed.recover() == 1 and resumed.recover() == 0
    assert resumed.totals()["uncertain"] == reserved
    seen = []
    assert execute(resumed, calls, packets, lambda c, p: seen.append(c["cell_id"]) or outcome(c))["state"] == "requests_complete"
    assert completed["cell_id"] not in seen
    assert Counter(seen) == Counter(c["cell_id"] for c in calls[1:])
    assert next(row for row in resumed.rows("main") if row["cell_id"] == completed["cell_id"]) == completed_before
    rows = [dict(x) for x in resumed.db.execute("SELECT * FROM attempts WHERE cell_id=? ORDER BY rowid", (interrupted["cell_id"],))]
    assert rows[0]["attempt_id"] == old_attempt and [x["state"] for x in rows] == ["unknown", "success"]
    assert rows[0]["request_sha256"] == rows[1]["request_sha256"]
    assert rows[0]["uncertain"] == reserved and resumed.totals()["inflight"] == 0


@pytest.mark.parametrize("text", [VALID, "", "Malformed received verdict"])
def test_completed_invalid_verdicts_are_final_and_equal_packets_still_get_separate_calls(stores, text):
    calls, packets, _ = panel()
    assert len({r.canonical(p["messages"]) for p in packets.values()}) == 1
    store, seen = stores(), []
    assert execute(store, calls, packets, lambda c, p: seen.append(c["cell_id"]) or outcome(c, text))["state"] == "requests_complete"
    before = (store.directory / "results.jsonl").read_bytes()
    assert len(seen) == len(set(seen)) == 8
    assert all(row["raw_verdict_text"] == text and row["packet_id"] == row["unit_id"] + ":" + row["arm"] for row in store.rows("main"))
    store.db.close()
    resumed = stores()
    assert execute(resumed, calls, packets, lambda *_: pytest.fail("Completed cell was dispatched again"))["state"] == "requests_complete"
    assert (resumed.directory / "results.jsonl").read_bytes() == before


@pytest.mark.parametrize("judge", r.MODELS)
def test_configured_reasoning_reserve_is_spend_only(judge):
    calls, packets, _ = panel()
    call = next(c for c in calls if c["judge"] == judge)
    packet = packets[call["packet_id"]]
    prompt, completion = _estimate_usage(packet["messages"], call["max_tokens"])
    _, context = estimate_context_tokens(packet["messages"], call["max_tokens"])
    settings = copy.deepcopy(SETTINGS)
    settings[judge]["context_length"] = context
    assert r.reservation(call, packet, PRICES, settings) == r.token_cost(prompt, completion * r.MULTIPLIERS[judge], PRICES[judge])
    settings[judge]["context_length"] -= 1
    with pytest.raises(ValueError, match="context"):
        r.reservation(call, packet, PRICES, settings)


@pytest.mark.parametrize("cap", ["total", "uncertain", "preflight"])
def test_all_caps_include_pending_and_unknown_attempts(stores, cap):
    calls, packets, _ = panel()
    calls = [c for c in calls if c["judge"] == r.LLAMA]
    amount = r.reservation(calls[0], packets[calls[0]["packet_id"]], PRICES, SETTINGS)
    store = stores(limits={**LIMITS, cap: amount * 2})
    stage = "preflight" if cap == "preflight" else "main"
    a, _ = store.reserve(calls[0], packets[calls[0]["packet_id"]], stage, PRICES)
    assert store.reserve(calls[1], packets[calls[1]["packet_id"]], stage, PRICES)[1] is None
    assert store.reserve(calls[2], packets[calls[2]["packet_id"]], stage, PRICES) == (None, cap + "_cap")
    store.finish(a, calls[0], {"error": "mock disconnect"}, PRICES)
    assert store.reserve(calls[2], packets[calls[2]["packet_id"]], stage, PRICES) == (None, cap + "_cap")


def test_preflight_physical_attempt_limit_and_changed_retry_refusal(stores):
    calls, packets, _ = panel()
    call = next(c for c in calls if c["judge"] == r.LLAMA)
    store = stores()
    for _ in range(16):
        attempt, cap = store.reserve(call, packets[call["packet_id"]], "preflight", PRICES)
        assert cap is None
        store.finish(attempt, call, {"error": "mock unknown"}, PRICES)
    with pytest.raises(ValueError, match="durable identity"):
        store.reserve({**call, "seed": call["seed"] + 1}, packets[call["packet_id"]], "preflight", PRICES)
    store.db.close()
    resumed = stores()
    assert resumed.reserve(call, packets[call["packet_id"]], "preflight", PRICES) == (None, "preflight_cap")


@pytest.mark.parametrize("kind", ["model", "cost", "transport"])
def test_fatal_stop_survives_restart(stores, kind):
    calls, packets, _ = panel()
    calls = [calls[0]]
    response = (outcome(calls[0], model="wrong/model") if kind == "model" else
                outcome(calls[0], usage={"prompt_tokens": 10**8, "completion_tokens": 10**8}) if kind == "cost" else
                {"error": "mock HTTP 401", "fatal": True})
    store = stores()
    assert execute(store, calls, packets, lambda *_: response)["state"] == "paused"
    reason = store.fatal_reason()
    assert reason
    store.db.close()
    resumed = stores()
    status = execute(resumed, calls, packets, lambda *_: pytest.fail("Fatal latch bypassed"))
    assert status["state"] == "paused" and status["halt_reason"] == reason


def test_persistent_exact_model_probe_retries_without_burning_fresh_cells(stores):
    calls, packets, _ = panel()
    failed = calls[0]
    store = stores()
    for _ in range(3):
        attempt, _ = store.reserve(failed, packets[failed["packet_id"]], "main", PRICES)
        store.finish(attempt, failed, {"error": "mock 503"}, PRICES)
        store.defer(failed["judge"], failed["cell_id"], .001, cycle_seconds=.04)
    deadline = store.retries("main", .001, .04)[failed["judge"]]["eligible_at"]
    store.db.close()
    resumed, seen = stores(), []
    lock = threading.Lock()
    def fake(call, packet):
        with lock:
            seen.append((call["cell_id"], call["judge"], time.time()))
        return outcome(call)
    assert execute(resumed, calls, packets, fake)["state"] == "requests_complete"
    qwen = [row for row in seen if row[1] == failed["judge"]]
    assert qwen[0][0] == failed["cell_id"] and qwen[0][2] >= deadline
    assert any(row[1] == r.LLAMA for row in seen[:seen.index(qwen[0])])
    assert resumed.db.execute("SELECT count(*) FROM attempts WHERE state='unknown'").fetchone()[0] == 3


def test_single_run_lock_refuses_other_process(tmp_path):
    code = "from pathlib import Path; from rejudge.phase4b_recipient_runner import run_lock; import sys\ntry:\n with run_lock(Path(sys.argv[1])): sys.exit(7)\nexcept OSError:\n sys.exit(0)"
    with r.run_lock(tmp_path / "locked"):
        child = subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path / "locked")], capture_output=True, timeout=30)
    assert child.returncode == 0, child.stderr.decode()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "rejudge").mkdir(parents=True)
    protocol = project / "rejudge/phase4_protocol_v1.json"
    protocol.write_text('{"frozen":true}')
    monkeypatch.setattr(r, "ROOT", project)
    inputs, output = tmp_path / "inputs", tmp_path / "output"
    inputs.mkdir()
    calls, packets, units = panel()
    # Only this offline fixture scales the fixed formal panel down to one unit.
    monkeypatch.setattr(r, "EXPECTED_MAIN_CALLS", len(calls))
    auth = {"stage": "4B-recipient", "run_id": "offline-recipient", "paid_execution_authorized": True,
            "approved_cap_usd": 150, "uncertain_cap_usd": 25, "preflight_cap_usd": 5,
            "inputs": str(inputs), "run_directory": str(output), "models": list(r.MODELS),
            "model_settings": copy.deepcopy(SETTINGS), "prices_per_million": PRICES,
            "main_verdict_calls": len(calls), "protocol_sha256": r.sha(protocol), "seed_namespace": r.SEED_NAMESPACE}
    data = {"calls.jsonl": calls, "packets.jsonl": list(packets.values()), "units_private.jsonl": units, "edit_map_private.jsonl": []}
    for name, rows in data.items():
        (inputs / name).write_text("".join(r.canonical(row) + "\n" for row in rows), encoding="utf-8")
    manifest = {"schema_version": "phase4b_recipient_prepared_panel_v1", "main_verdict_calls": len(calls), "models": list(r.MODELS),
                "protocol_sha256": auth["protocol_sha256"], "call_seed_namespace": r.SEED_NAMESPACE, "arms": list(r.ARMS),
                "model_settings": {j: {"temperature": .3, "max_tokens": r.OUTPUT_LIMITS[j], "stream": False} for j in r.MODELS},
                "outputs": {name: {"sha256": r.sha(inputs / name), "bytes": (inputs / name).stat().st_size} for name in data}}
    r.atomic_json(inputs / "manifest.json", manifest)
    auth["input_manifest_sha256"] = r.sha(inputs / "manifest.json")
    authorization = tmp_path / "authorization.json"
    r.atomic_json(authorization, auth)
    return SimpleNamespace(project=project, inputs=inputs, output=output, auth=auth, authorization=authorization,
                           calls=calls, packets=packets, manifest=manifest)


def args(p, mode):
    return ["--inputs", str(p.inputs), "--run-dir", str(p.output), "--authorization", str(p.authorization), "--mode", mode]


def test_unapproved_draft_cannot_create_store_or_provider_and_status_is_readonly(prepared, monkeypatch):
    p = prepared
    p.auth.update(paid_execution_authorized=False, approved_cap_usd=None, input_manifest_sha256=None)
    r.atomic_json(p.authorization, p.auth)
    monkeypatch.setattr(r, "Provider", lambda: pytest.fail("Unauthorized SDK construction"))
    for mode in ("preflight", "run"):
        with pytest.raises(ValueError, match="authorization is absent"):
            r.main(args(p, mode))
    assert not p.output.exists()
    assert r.main(args(p, "status")) == 0 and not p.output.exists()


@pytest.mark.parametrize("mutation", ["temperature", "top_p", "reasoning_effort", "max_tokens", "reservation", "protocol"])
def test_authorized_config_cannot_change_frozen_profile(prepared, mutation):
    p = prepared
    if mutation == "protocol": (p.project / "rejudge/phase4_protocol_v1.json").write_text("changed")
    elif mutation == "reservation": p.auth["model_settings"][r.QWEN]["reservation_multiplier"] = 1
    else: p.auth["model_settings"][r.QWEN][mutation] = {"temperature": .2, "top_p": 1.0, "reasoning_effort": "low", "max_tokens": 512}[mutation]
    r.atomic_json(p.authorization, p.auth)
    with pytest.raises(ValueError):
        r.verify_authorization(p.authorization, p.inputs, p.output)


def test_bound_panel_reopens_and_changed_input_refuses(prepared):
    p = prepared
    assert r.verify_authorization(p.authorization, p.inputs, p.output) == p.auth
    calls, packets, _ = r.load_panel(p.inputs, p.auth)
    assert calls == p.calls and packets == p.packets
    with (p.inputs / "edit_map_private.jsonl").open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="input changed"):
        r.load_panel(p.inputs, p.auth)


def test_formal_authorization_requires_exactly_5248_verdicts(prepared, monkeypatch):
    p = prepared
    monkeypatch.setattr(r, "EXPECTED_MAIN_CALLS", 5248)
    with pytest.raises(ValueError, match="exactly 5248"):
        r.verify_authorization(p.authorization, p.inputs, p.output)
    p.auth["main_verdict_calls"] = 5248
    r.atomic_json(p.authorization, p.auth)
    assert r.verify_authorization(p.authorization, p.inputs, p.output)["main_verdict_calls"] == 5248


@pytest.mark.parametrize("mutation", ["seed", "top_p", "duplicate", "wrong_arm", "wrong_packet"])
def test_hashbound_invalid_panel_still_refuses(prepared, mutation):
    p = prepared
    calls = copy.deepcopy(p.calls)
    if mutation == "seed": calls[0]["seed"] += 1
    elif mutation == "top_p": calls[0]["top_p"] = 1
    elif mutation == "duplicate": calls[1] = copy.deepcopy(calls[0])
    elif mutation == "wrong_arm": calls[0]["arm"] = "empty"
    elif mutation == "wrong_packet": calls[0]["packet_id"] = calls[-1]["packet_id"]
    path = p.inputs / "calls.jsonl"
    path.write_text("".join(r.canonical(row) + "\n" for row in calls), encoding="utf-8")
    p.manifest["outputs"]["calls.jsonl"] = {"sha256": r.sha(path), "bytes": path.stat().st_size}
    r.atomic_json(p.inputs / "manifest.json", p.manifest)
    p.auth["input_manifest_sha256"] = r.sha(p.inputs / "manifest.json")
    with pytest.raises(ValueError):
        r.load_panel(p.inputs, p.auth)


@pytest.mark.parametrize("bad_preflight", [False, True])
def test_cli_end_to_end_and_completed_restart_require_no_provider(prepared, monkeypatch, bad_preflight):
    p = prepared
    monkeypatch.setattr(r, "runtime_snapshot", lambda: {"git_commit": "offline", "source_sha256s": {}})
    actual_execute = r.execute
    monkeypatch.setattr(r, "execute", lambda *a, **kw: actual_execute(*a, cooldown_seconds=.001, cycle_seconds=.003, poll_seconds=.001, **kw))
    seen = []
    def provider(call, packet):
        seen.append(call["cell_id"])
        return outcome(call, "" if bad_preflight or not call["cell_id"].startswith("preflight:") else VALID)
    monkeypatch.setattr(r, "Provider", lambda: provider)
    with pytest.raises(ValueError, match="preflight has not passed"):
        r.main(args(p, "run"))
    assert seen == []
    assert r.main(args(p, "preflight")) == (2 if bad_preflight else 0)
    assert len(seen) == 6
    if bad_preflight:
        assert r.main(args(p, "preflight")) == 2
        assert len(seen) == 6
        with pytest.raises(ValueError, match="preflight has not passed"):
            r.main(args(p, "run"))
        return
    assert r.main(args(p, "run")) == 0 and len(seen) == 14
    before = (p.output / "results.jsonl").read_bytes()
    monkeypatch.setattr(r, "Provider", lambda: pytest.fail("Completed run should not construct provider"))
    assert r.main(args(p, "run")) == 0
    assert (p.output / "results.jsonl").read_bytes() == before
    assert all(json.loads(line)["raw_verdict_text"] == "" for line in before.decode().splitlines())
    status = json.loads((p.output / "status.json").read_bytes())
    assert status["state"] == "complete" and status["pid"] == os.getpid()
    assert status["completion_scope"] == "transport_collection_only"
    assert json.loads((p.output / "completion.json").read_bytes())["phase4c_authorized"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_hidden_launcher_is_valid_powershell_and_rejects_unapproved_before_mutation(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/phase4b_recipient_launch.ps1"
    authorization = tmp_path / "unapproved.json"
    output = tmp_path / "output"
    authorization.write_text(json.dumps({"paid_execution_authorized": False, "stage": "4B-recipient",
                                         "inputs": str(tmp_path / "absent-inputs"), "run_directory": str(output)}))
    shell = "powershell.exe"
    result = subprocess.run([shell, "-NoProfile", "-File", str(path), "-Authorization", str(authorization), "-Mode", "run"],
                            capture_output=True, timeout=30)
    assert result.returncode != 0 and b"recipient authorization is required" in result.stderr
    assert not output.exists()
    text = path.read_text(encoding="utf-8")
    assert "rejudge.phase4b_recipient_runner" in text and "rejudge.phase4b_runner" not in text
    assert "ShowWindow=[uint16]0" in text and "CreateFlags=[uint32]0x09000400" in text
    assert "$info.CreateNoWindow=$true" in text


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_launcher_status_is_readonly_from_outside_repository(tmp_path):
    launcher = Path(__file__).resolve().parents[1] / "scripts/phase4b_recipient_launch.ps1"
    inputs, output = tmp_path / "inputs", tmp_path / "output"
    inputs.mkdir()
    authorization = tmp_path / "draft.json"
    authorization.write_text(json.dumps({"paid_execution_authorized": False, "approved_cap_usd": None,
        "stage": "4B-recipient", "run_id": "offline-launcher-status", "inputs": str(inputs), "run_directory": str(output)}))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(launcher), "-Authorization", str(authorization), "-Mode", "status"],
                            cwd=tmp_path, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout)["state"] == "not_started"
    assert not output.exists()
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
