"""Offline delivery, budgeting, and blinded-input tests for Phase 4B."""
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

from rejudge import phase4b_runner as runner
from rejudge.api_client import _estimate_usage, estimate_context_tokens


MODELS = ("offline/reasoner-a", "offline/reasoner-b")
SETTINGS = {j: {"max_tokens": 128, "temperature": 1.0, "top_p": 1.0, "reasoning_effort": "low",
                "context_length": 131072, "reservation_multiplier": 3, "initial_workers": 1, "max_workers": 1}
            for j in MODELS}
PRICES = {j: {"input": .15, "output": .6} for j in MODELS}
LIMITS = {"total": runner.money(100), "uncertain": runner.money(10), "preflight": runner.money(1)}


def panel(count=1, models=MODELS):
    calls, packets = [], {}
    for i in range(count):
        claim = f"claim-{i}"
        messages = [{"role": "system", "content": "Adjudicate the exact claim."},
                    {"role": "user", "content": "World: blue bridge. Claim: blue bridge."}]
        packets[claim] = {"packet_id": claim, "claim_id": claim, "messages": messages,
                          "messages_sha256": runner.digest(messages), "gold": "PRIVATE_GOLD", "donor": "PRIVATE_DONOR"}
        for judge in models:
            calls.append({"cell_id": claim + ":" + judge, "claim_id": claim, "packet_id": claim,
                          "judge": judge, "messages_sha256": runner.digest(messages), "seed": 100 + i,
                          **{k: SETTINGS[judge][k] for k in runner.PROFILE_FIELDS}})
    return calls, packets


def outcome(call, content="{malformed adjudication", *, model=None, usage=None):
    return {"response": {"model": model or call["judge"], "id": "offline-response",
                         "usage": {"prompt_tokens": 5, "completion_tokens": 6} if usage is None else usage,
                         "choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
            "duration_seconds": .001}


@pytest.fixture
def stores(tmp_path):
    opened = []

    def make(name="run", limits=None, settings=None, identity=None):
        store = runner.Store(tmp_path / name, identity or {"run_id": "offline-4b"}, limits or LIMITS, settings or SETTINGS)
        opened.append(store)
        return store

    yield make
    for store in opened:
        with suppress(Exception):
            store.db.close()


def execute(store, calls, packets, provider, stage="main", **kwargs):
    return runner.execute(store, calls, packets, stage, PRICES, provider,
                          cooldown_seconds=.001, cycle_seconds=.003, poll_seconds=.001, **kwargs)


def test_crash_recovery_retains_unknown_once_and_retries_exact_request(stores):
    calls, packets = panel(models=MODELS[:1])
    call, store = calls[0], stores()
    attempt, _ = store.reserve(call, packets[call["packet_id"]], "main", PRICES)
    original = dict(store.db.execute("SELECT * FROM attempts").fetchone())
    store.db.close()
    reopened = stores()
    assert reopened.recover() == 1
    assert reopened.recover() == 0
    assert reopened.totals()["uncertain"] == original["reserved"]
    observed = []
    status = execute(reopened, calls, packets, lambda c, p: observed.append(runner.request_kwargs(c, p)) or outcome(c))
    assert status["state"] == "requests_complete"
    assert observed == [runner.request_kwargs(call, packets[call["packet_id"]])]
    rows = [dict(r) for r in reopened.db.execute("SELECT * FROM attempts ORDER BY rowid")]
    assert rows[0]["attempt_id"] == attempt
    assert [r["state"] for r in rows] == ["unknown", "success"]
    assert len({r["request_sha256"] for r in rows}) == 1
    assert reopened.totals()["exposure"] == original["reserved"] + rows[1]["actual"]


@pytest.mark.parametrize("text", ["", "not JSON", '{"label":"AMBIGUOUS"}', '{"label":"YES"}'])
def test_all_received_content_is_final_and_export_replay_is_identical(stores, text):
    calls, packets = panel()
    store = stores()
    observed = []
    assert execute(store, calls, packets, lambda c, p: observed.append(c["cell_id"]) or outcome(c, text))["state"] == "requests_complete"
    before = (store.directory / "results.jsonl").read_bytes()
    rows = store.rows("main")
    assert all(r["raw_adjudication_text"] == text for r in rows)
    assert all("strict_valid" not in r and "eligible" not in r for r in rows)
    store.db.close()
    reopened = stores()
    assert execute(reopened, calls, packets, lambda *_: pytest.fail("A completed adjudication was regenerated"))["state"] == "requests_complete"
    assert (reopened.directory / "results.jsonl").read_bytes() == before
    assert Counter(observed) == Counter(c["cell_id"] for c in calls)
    assert reopened.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == len(calls)


@pytest.mark.parametrize("cap", ["total", "uncertain", "preflight"])
def test_reserve_cap_includes_inflight_and_uncertain_and_is_inclusive(stores, cap):
    calls, packets = panel(3, MODELS[:1])
    amount = runner.reservation(calls[0], packets[calls[0]["packet_id"]], PRICES, SETTINGS)
    limits = {**LIMITS, cap: amount * 2}
    store = stores(limits=limits)
    stage = "preflight" if cap == "preflight" else "main"
    first, _ = store.reserve(calls[0], packets[calls[0]["packet_id"]], stage, PRICES)
    assert store.reserve(calls[1], packets[calls[1]["packet_id"]], stage, PRICES)[1] is None
    assert store.reserve(calls[2], packets[calls[2]["packet_id"]], stage, PRICES) == (None, cap + "_cap")
    store.finish(first, calls[0], {"error": "offline lost delivery"}, PRICES)
    assert store.totals()["uncertain"] == amount
    assert store.reserve(calls[2], packets[calls[2]["packet_id"]], stage, PRICES) == (None, cap + "_cap")


def test_generic_reasoning_spend_reserve_does_not_double_context():
    calls, packets = panel(models=MODELS[:1])
    call, packet = calls[0], packets[calls[0]["packet_id"]]
    prompt, output = _estimate_usage(packet["messages"], call["max_tokens"])
    _, context = estimate_context_tokens(packet["messages"], call["max_tokens"])
    settings = copy.deepcopy(SETTINGS)
    settings[call["judge"]]["context_length"] = context
    assert runner.reservation(call, packet, PRICES, settings) == runner.token_cost(prompt, output * 3, PRICES[call["judge"]])
    settings[call["judge"]]["context_length"] -= 1
    with pytest.raises(ValueError, match="context"):
        runner.reservation(call, packet, PRICES, settings)


def test_preflight_physical_attempt_ceiling_survives_restart(stores):
    calls, packets = panel(models=MODELS[:1])
    store, call = stores(), calls[0]
    for _ in range(16):
        attempt, _ = store.reserve(call, packets[call["packet_id"]], "preflight", PRICES)
        store.finish(attempt, call, {"error": "offline transient"}, PRICES)
    store.db.close()
    reopened = stores()
    assert reopened.reserve(call, packets[call["packet_id"]], "preflight", PRICES) == (None, "preflight_cap")


def test_retry_refuses_changed_request_and_duplicate_inflight(stores):
    calls, packets = panel(models=MODELS[:1])
    call, store = calls[0], stores()
    attempt, _ = store.reserve(call, packets[call["packet_id"]], "main", PRICES)
    with pytest.raises(ValueError, match="in-flight"):
        store.reserve(call, packets[call["packet_id"]], "main", PRICES)
    store.finish(attempt, call, {"error": "offline timeout"}, PRICES)
    with pytest.raises(ValueError, match="durable identity"):
        store.reserve({**call, "seed": 400}, packets[call["packet_id"]], "main", PRICES)


def test_third_failure_deadline_survives_restart_and_later_retry(stores):
    calls, packets = panel(models=MODELS[:1])
    call, store = calls[0], stores()
    for _ in range(3):
        attempt, _ = store.reserve(call, packets[call["packet_id"]], "main", PRICES)
        store.finish(attempt, call, {"error": "offline transient"}, PRICES)
        store.defer(call["judge"], call["cell_id"], .001, cycle_seconds=.02)
    prior = store.retries("main", .001, .02)[call["judge"]]
    last = store.db.execute("SELECT finished_at FROM attempts ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    assert prior["eligible_at"] == pytest.approx(runner.datetime.fromisoformat(last).timestamp() + .02)
    store.db.close()
    reopened = stores()
    assert reopened.recover() == 0
    assert reopened.retries("main", .001, .02)[call["judge"]] == prior
    observed = []
    status = execute(reopened, calls, packets, lambda c, p: observed.append(time.time()) or outcome(c))
    assert status["state"] == "requests_complete"
    assert observed[0] >= prior["eligible_at"]
    assert reopened.db.execute("SELECT count(*) FROM attempts WHERE state='unknown'").fetchone()[0] == 3


def test_mixed_transport_retry_does_not_block_other_model_or_resample_semantics(stores):
    calls, packets = panel(3)
    observed, lock = [], threading.Lock()
    failing = calls[0]["cell_id"]
    def fake(call, packet):
        with lock:
            observed.append(call["cell_id"])
            n = observed.count(call["cell_id"])
        return {"error": "offline 503"} if call["cell_id"] == failing and n <= 3 else outcome(call)
    store = stores()
    status = execute(store, calls, packets, fake)
    assert status["state"] == "requests_complete"
    assert Counter(observed) == Counter({c["cell_id"]: 4 if c["cell_id"] == failing else 1 for c in calls})
    positions = [i for i, c in enumerate(observed) if c == failing]
    assert any(MODELS[1] in c for c in observed[positions[0] + 1:positions[-1]])
    assert store.totals()["uncertain"] > 0 and store.totals()["inflight"] == 0


def test_cap_waits_for_existing_reservations_to_settle(stores):
    calls, packets = panel(2, MODELS[:1])
    amount = runner.reservation(calls[0], packets[calls[0]["packet_id"]], PRICES, SETTINGS)
    store = stores(limits={**LIMITS, "uncertain": amount})
    settings = store.model_settings[MODELS[0]]
    settings["initial_workers"], settings["max_workers"] = 2, 2
    try:
        status = execute(store, calls, packets, lambda c, p: (time.sleep(.005), outcome(c))[1])
        assert status["state"] == "requests_complete" and status["halt_reason"] is None
    finally:
        settings["initial_workers"], settings["max_workers"] = 1, 1


@pytest.mark.parametrize("kind", ["model", "cost", "transport"])
def test_fatal_configuration_or_spend_stop_is_durable(stores, kind):
    calls, packets = panel(models=MODELS[:1])
    store, call = stores(), calls[0]
    if kind == "model":
        result = outcome(call, model="unapproved/alias")
    elif kind == "cost":
        result = outcome(call, usage={"prompt_tokens": 10**8, "completion_tokens": 10**8})
    else:
        result = {"error": "offline HTTP 401", "fatal": True}
    assert execute(store, calls, packets, lambda *_: result)["state"] == "paused"
    reason = store.fatal_reason()
    assert reason
    store.db.close()
    reopened = stores()
    status = execute(reopened, calls, packets, lambda *_: pytest.fail("Fatal stop was bypassed"))
    assert status["state"] == "paused" and status["halt_reason"] == reason


def test_missing_usage_retains_raw_response_without_retry(stores):
    calls, packets = panel(models=MODELS[:1])
    store = stores()
    assert execute(store, calls, packets, lambda c, p: outcome(c, usage={}))["state"] == "requests_complete"
    assert store.totals()["uncertain"] > 0
    assert store.rows("main")[0]["raw_adjudication_text"] == "{malformed adjudication"
    assert store.db.execute("SELECT response FROM attempts").fetchone()[0]


def test_request_boundary_sends_only_blinded_messages_and_explicit_decoding(monkeypatch):
    calls, packets = panel(models=MODELS[:1])
    call, captured = calls[0], []
    packet = packets[call["packet_id"]]
    raw = outcome(call)["response"]
    class Completion:
        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(model_dump=lambda **_: raw)
    provider = runner.Provider.__new__(runner.Provider)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=Completion()))
    assert provider(call, packet)["response"] == raw
    assert captured == [{"model": call["judge"], "messages": packet["messages"], "seed": call["seed"],
                         **{k: call[k] for k in runner.PROFILE_FIELDS}}]
    assert "PRIVATE_" not in runner.canonical(captured)


def test_single_run_lock_blocks_another_process(tmp_path):
    run_dir = tmp_path / "lock"
    script = "from pathlib import Path; from rejudge.phase4b_runner import run_lock; import sys\ntry:\n with run_lock(Path(sys.argv[1])): sys.exit(9)\nexcept OSError:\n sys.exit(0)"
    with runner.run_lock(run_dir):
        child = subprocess.run([sys.executable, "-B", "-c", script, str(run_dir)], capture_output=True, timeout=30)
    assert child.returncode == 0, child.stderr.decode()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "rejudge").mkdir(parents=True)
    (project / "rejudge/phase4_protocol_v1.json").write_text('{"frozen":true}', encoding="utf-8")
    (project / "rejudge/phase4b_labels.py").write_text("# frozen synthetic label helper\n", encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", project)
    inputs, run_dir = tmp_path / "inputs", tmp_path / "output"
    inputs.mkdir()
    calls, packets = panel()
    auth = {"stage": "4B", "run_id": "offline-blinded", "paid_execution_authorized": True,
            "approved_cap_usd": 100, "uncertain_cap_usd": 10, "preflight_cap_usd": 1,
            "inputs": str(inputs), "run_directory": str(run_dir), "adjudication_calls": len(calls),
            "models": list(MODELS), "model_settings": copy.deepcopy(SETTINGS), "prices_per_million": PRICES,
            "protocol_sha256": runner.sha(project / "rejudge/phase4_protocol_v1.json")}
    for name, rows in (("calls.jsonl", calls), ("claim_packets.jsonl", list(packets.values()))):
        (inputs / name).write_text("".join(runner.canonical(r) + "\n" for r in rows), encoding="utf-8")
    manifest = {k: auth[k] for k in ("adjudication_calls", "models", "model_settings", "prices_per_million")}
    manifest.update(oracle_contract="Say YES for a stated fact, NO for a contradiction, NOT ADDRESSED when neither is stated.",
                    prompt_script_sha256=runner.sha(project / "rejudge/phase4b_labels.py"),
                    outputs={n: {"sha256": runner.sha(inputs / n)} for n in ("calls.jsonl", "claim_packets.jsonl")})
    runner.atomic_json(inputs / "adjudication_manifest.json", manifest)
    auth["input_manifest_sha256"] = runner.sha(inputs / "adjudication_manifest.json")
    auth_path = tmp_path / "auth.json"
    runner.atomic_json(auth_path, auth)
    return SimpleNamespace(project=project, inputs=inputs, run_dir=run_dir, auth=auth, auth_path=auth_path,
                           calls=calls, packets=packets, manifest=manifest)


def test_bound_panel_and_hashes_reopen_and_tampering_refuses(prepared):
    p = prepared
    assert runner.verify_authorization(p.auth_path, p.inputs, p.run_dir) == p.auth
    calls, packets, _ = runner.load_panel(p.inputs, p.auth)
    assert calls == p.calls and packets == p.packets
    with (p.inputs / "calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="Prepared input changed"):
        runner.load_panel(p.inputs, p.auth)


@pytest.mark.parametrize("source", ["protocol", "prompt"])
def test_changed_scientific_source_refuses(prepared, source):
    p = prepared
    if source == "protocol":
        (p.project / "rejudge/phase4_protocol_v1.json").write_text("changed")
        with pytest.raises(ValueError, match="protocol"):
            runner.verify_authorization(p.auth_path, p.inputs, p.run_dir)
    else:
        (p.project / "rejudge/phase4b_labels.py").write_text("changed")
        with pytest.raises(ValueError, match="prompt source"):
            runner.load_panel(p.inputs, p.auth)


def test_unapproved_run_and_preflight_cannot_construct_provider_or_store(prepared, monkeypatch):
    p = prepared
    p.auth.update(paid_execution_authorized=False, approved_cap_usd=None, adjudication_calls=None)
    runner.atomic_json(p.auth_path, p.auth)
    monkeypatch.setattr(runner, "Provider", lambda: pytest.fail("Unauthorized SDK construction"))
    for mode in ("preflight", "run"):
        with pytest.raises(ValueError, match="authorization is absent"):
            runner.main(["--inputs", str(p.inputs), "--run-dir", str(p.run_dir), "--authorization", str(p.auth_path), "--mode", mode])
    assert not p.run_dir.exists()
    assert runner.main(["--inputs", str(p.inputs), "--run-dir", str(p.run_dir), "--authorization", str(p.auth_path), "--mode", "status"]) == 0
    assert not p.run_dir.exists()


def test_paid_main_needs_durable_preflight_before_provider(prepared, monkeypatch):
    p = prepared
    monkeypatch.setattr(runner, "runtime_snapshot", lambda: {"git_commit": "offline", "source_sha256s": {}})
    monkeypatch.setattr(runner, "Provider", lambda: pytest.fail("Preflight gate bypassed"))
    with pytest.raises(ValueError, match="preflight has not passed"):
        runner.main(["--inputs", str(p.inputs), "--run-dir", str(p.run_dir), "--authorization", str(p.auth_path), "--mode", "run"])


def synthetic_good(call, packet):
    i = int(call["claim_id"].split("-")[-1])
    label = ("YES", "NO", "NOT ADDRESSED")[i]
    quote = ("Amberford has exactly one bridge.", "The bridge is not red.", "The mayor is Mira.")[i]
    text = runner.canonical({"label": label, "unambiguous": True, "quotes": [quote], "rationale": "Synthetic contract check.",
                             "missing_information": "Mira's birthplace is unstated." if i == 2 else ""})
    return outcome(call, text)


@pytest.mark.parametrize("bad", [False, True])
def test_end_to_end_preflight_main_and_finished_restart(prepared, monkeypatch, bad):
    p = prepared
    monkeypatch.setattr(runner, "runtime_snapshot", lambda: {"git_commit": "offline", "source_sha256s": {}})
    actual_execute = runner.execute
    monkeypatch.setattr(runner, "execute", lambda *a, **kw: actual_execute(*a, cooldown_seconds=.001, cycle_seconds=.003, poll_seconds=.001, **kw))
    seen = []
    def fake(call, packet):
        seen.append(call["cell_id"])
        if call["cell_id"].startswith("preflight:"):
            return outcome(call, "") if bad else synthetic_good(call, packet)
        return outcome(call, "malformed final adjudication")
    monkeypatch.setattr(runner, "Provider", lambda: fake)
    base = ["--inputs", str(p.inputs), "--run-dir", str(p.run_dir), "--authorization", str(p.auth_path)]
    assert runner.main(base + ["--mode", "preflight"]) == (2 if bad else 0)
    assert len(seen) == 6
    if bad:
        assert runner.main(base + ["--mode", "preflight"]) == 2
        assert len(seen) == 6
        with pytest.raises(ValueError, match="preflight has not passed"):
            runner.main(base + ["--mode", "run"])
        return
    assert runner.main(base + ["--mode", "run"]) == 0
    original = (p.run_dir / "results.jsonl").read_bytes()
    monkeypatch.setattr(runner, "Provider", lambda: pytest.fail("Completed restart must not construct SDK"))
    assert runner.main(base + ["--mode", "run"]) == 0
    assert (p.run_dir / "results.jsonl").read_bytes() == original
    assert len(seen) == 8
    status = json.loads((p.run_dir / "status.json").read_bytes())
    assert status["pid"] == os.getpid() and status["state"] == "complete"
    assert status["completion_scope"] == "transport_collection_only"
