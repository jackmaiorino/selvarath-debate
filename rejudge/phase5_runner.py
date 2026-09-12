"""Durable Phase 5 evidence-scope collection with the unchanged Phase 4A request profile.

All six arms receive fresh verdicts. Completed invalid responses are retained
once; the existing recipient store and scheduler recover uncertain deliveries.
Collection completion does not authorize a later experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from rejudge import phase4_runner as phase4a
from rejudge import phase4b_runner as recovery
from rejudge import phase4b_recipient_runner as recipients
from rejudge.parsers import parse_both

ROOT = Path(__file__).resolve().parents[1]
QWEN, LLAMA = phase4a.QWEN, phase4a.LLAMA
MODELS = (QWEN, LLAMA)
CONTEXTS = ("empty", "qwen_history", "llama_history")
PROMPTS = ("ordinary", "scope")
ARMS = tuple(prompt + "_" + context for prompt in PROMPTS for context in CONTEXTS)
STAGE = "5-evidence-scope"
RUN_ID = "phase5-evidence-scope-2026-09-12"
PROTOCOL_SHA256 = "c611aa62d7b5290cc240e82a881488c3ce309b26ce33ab3979c3e09838043f07"
EXPECTED_UNITS = 656
ORDER_SEED = 2026091209
ANALYSIS_SEED = 2026091208
OUTPUT_LIMITS = {QWEN: 16384, LLAMA: 512}
MULTIPLIERS = {QWEN: 3, LLAMA: 1}
SEED_NAMESPACE = "phase5-evidence-scope-verdict-v1"
EXPECTED_MAIN_CALLS = 7872
SOURCE_FILES = (
    "rejudge/phase5_runner.py", "rejudge/phase4b_recipient_runner.py", "rejudge/phase4b_runner.py",
    "rejudge/phase4_runner.py", "rejudge/phase4b_labels.py", "rejudge/api_client.py", "rejudge/parsers.py",
    "analysis/infra/parsing.py", "rejudge/durable_fs.py", "rejudge/phase2_role_limits.py",
    "rejudge/phase2_plan.py", "rejudge/phase2_provider_price_snapshot.py",
    "rejudge/__init__.py", "analysis/__init__.py", "analysis/infra/__init__.py",
)
canonical, sha, utc = phase4a.canonical, phase4a.sha, phase4a.utc
money, usd, token_cost = phase4a.money, phase4a.usd, phase4a.token_cost
atomic_json, atomic_bytes, run_lock = phase4a.atomic_json, phase4a.atomic_bytes, phase4a.run_lock
request_kwargs, Provider = phase4a.request_kwargs, phase4a.Provider
reservation, execute = recovery.reservation, recovery.execute


def verdict_seed(unit_id, judge):
    return int(hashlib.sha256((SEED_NAMESPACE + "|" + unit_id + "|" + judge).encode()).hexdigest()[:8], 16) % 2147483647


Store = recipients.Store


def verify_authorization(path, inputs, run_dir, *, require_paid=True):
    auth = json.loads(path.read_bytes())
    if (auth.get("schema_version") != "phase5_execution_v1" or auth.get("stage") != STAGE
            or auth.get("run_id") != RUN_ID):
        raise ValueError("Phase 5 evidence-scope identity is absent")
    if require_paid and auth.get("paid_execution_authorized") is not True:
        raise ValueError("Phase 5 evidence-scope paid execution authorization is absent")
    if Path(auth["inputs"]).resolve() != inputs or Path(auth["run_directory"]).resolve() != run_dir:
        raise ValueError("Authorized recipient input/output paths do not match")
    if run_dir.is_relative_to(ROOT) or inputs.is_relative_to(run_dir) or run_dir.is_relative_to(inputs):
        raise ValueError("Recipient outputs must be separate from Git and frozen inputs")
    if not require_paid:
        return auth
    if auth.get("protocol_sha256") != PROTOCOL_SHA256 or sha(ROOT / "rejudge/phase5_protocol_v1.json") != PROTOCOL_SHA256:
        raise ValueError("Original scientific protocol changed")
    if auth.get("seed_namespace", SEED_NAMESPACE) != SEED_NAMESPACE:
        raise ValueError("Recipient seed namespace changed")
    if (not isinstance(auth.get("models"), list) or len(auth["models"]) != 2 or set(auth["models"]) != set(MODELS)
            or set(auth["prices_per_million"]) != set(MODELS) or set(auth["model_settings"]) != set(MODELS)):
        raise ValueError("The frozen recipient model roster differs")
    for judge, setting in auth["model_settings"].items():
        if (setting.get("temperature") != .3 or isinstance(setting.get("temperature"), bool)
                or type(setting.get("max_tokens")) is not int or setting["max_tokens"] != OUTPUT_LIMITS[judge]
                or type(setting.get("reservation_multiplier")) is not int or setting["reservation_multiplier"] != MULTIPLIERS[judge]
                or any(name in setting for name in ("top_p", "reasoning_effort"))
                or setting.get("stream", False) is not False):
            raise ValueError("Recipient decoding or reasoning reserve differs from the Phase 4A profile")
        recovery._positive(setting["context_length"], "context_length", integer=True)
        recovery._positive(setting.get("max_workers", 16), "max_workers", integer=True, maximum=16)
        recovery._positive(setting.get("initial_workers", 4), "initial_workers", integer=True, maximum=setting.get("max_workers", 16))
        for name in ("input", "output"):
            recovery._positive(auth["prices_per_million"][judge][name], name + " price")
    recovery._positive(auth["main_verdict_calls"], "main_verdict_calls", integer=True)
    if auth["main_verdict_calls"] != EXPECTED_MAIN_CALLS:
        raise ValueError("The frozen recipient experiment requires exactly 7872 verdict calls")
    for name in ("approved_cap_usd", "uncertain_cap_usd", "preflight_cap_usd"):
        recovery._positive(auth[name], name)
    if auth["uncertain_cap_usd"] > 25 or auth["preflight_cap_usd"] > 5:
        raise ValueError("Recipient subcaps exceed the frozen Phase 5 ceilings")
    if max(auth["uncertain_cap_usd"], auth["preflight_cap_usd"]) > auth["approved_cap_usd"]:
        raise ValueError("Recipient subcaps exceed the approved aggregate cap")
    return auth


def load_panel(inputs, auth):
    path = inputs / "manifest.json"
    if sha(path) != auth["input_manifest_sha256"]:
        raise ValueError("Prepared recipient manifest changed")
    manifest = json.loads(path.read_bytes())
    if manifest.get("schema_version") != "phase5_evidence_scope_prepared_panel_v1":
        raise ValueError("Unsupported recipient panel schema")
    if (manifest.get("main_verdict_calls") != auth["main_verdict_calls"] or set(manifest.get("models", [])) != set(MODELS)
            or manifest.get("protocol_sha256") != auth["protocol_sha256"] or manifest.get("arms") != list(ARMS)
            or manifest.get("call_seed_namespace") != SEED_NAMESPACE
            or manifest.get("order_seed") != ORDER_SEED or manifest.get("analysis_seed") != ANALYSIS_SEED):
        raise ValueError("Prepared recipient scope differs from authorization")
    expected_settings = {j: {"temperature": .3, "max_tokens": OUTPUT_LIMITS[j], "stream": False} for j in MODELS}
    if manifest.get("model_settings") != expected_settings:
        raise ValueError("Prepared recipient decoding profile changed")
    if auth.get("repair_labels_sha256") and manifest.get("provenance", {}).get("repair_labels_sha256") != auth["repair_labels_sha256"]:
        raise ValueError("Authorized reviewed labels differ from the prepared panel")
    required = {"calls.jsonl", "packets.jsonl", "units_private.jsonl", "prompt_edits_private.jsonl"}
    if not required <= set(manifest["outputs"]):
        raise ValueError("Recipient input manifest lacks required file bindings")
    def verify_files():
        for name, binding in manifest["outputs"].items():
            bound = inputs / name
            if (Path(name).name != name or bound.is_symlink() or not bound.is_file()
                    or sha(bound) != binding["sha256"] or bound.stat().st_size != binding["bytes"]):
                raise ValueError("Prepared recipient input changed: " + name)
    verify_files()
    def rows(name):
        return [json.loads(line) for line in (inputs / name).read_text(encoding="utf-8").splitlines()]
    calls, packet_rows, unit_rows = rows("calls.jsonl"), rows("packets.jsonl"), rows("units_private.jsonl")
    packets, units = {r["packet_id"]: r for r in packet_rows}, {r["unit_id"]: r for r in unit_rows}
    if (len(packets) != len(packet_rows) or len(units) != len(unit_rows) or len(units) != EXPECTED_UNITS
            or len(calls) != auth["main_verdict_calls"] or len({c["cell_id"] for c in calls}) != len(calls)
            or len(calls) != len(units) * len(MODELS) * len(ARMS) or len(packets) != len(units) * len(ARMS)):
        raise ValueError("Duplicate or incorrect recipient panel size")
    seen, seeds, recipient_packets = set(), {}, {}
    for call in calls:
        unit, judge, arm = call["unit_id"], call["judge"], call["arm"]
        if unit not in units or judge not in MODELS or arm not in ARMS or (unit, judge, arm) in seen:
            raise ValueError("Invalid or duplicate unit/model/arm recipient cell")
        if call["cell_id"] != unit + ":" + judge + ":" + arm or call["packet_id"] != unit + ":" + arm:
            raise ValueError("Recipient cell or packet identity differs from the frozen construction")
        seen.add((unit, judge, arm))
        expected_prompt, expected_context = arm.split("_", 1)
        if call.get("context_arm") != expected_context or call.get("prompt_variant") != expected_prompt:
            raise ValueError("Call prompt/context does not match its frozen arm")
        packet = packets[call["packet_id"]]
        messages = packet["messages"]
        if (not isinstance(messages, list) or not messages or any(not isinstance(m, dict) or set(m) != {"role", "content"}
                or m["role"] not in ("system", "user", "assistant") or not isinstance(m["content"], str) for m in messages)):
            raise ValueError("Invalid recipient message shape")
        message_sha = recovery.digest(messages)
        if message_sha != call["messages_sha256"] or message_sha != packet["messages_sha256"]:
            raise ValueError("Recipient message identity mismatch")
        if (call.get("temperature") != .3 or isinstance(call.get("temperature"), bool)
                or type(call.get("max_tokens")) is not int or call["max_tokens"] != OUTPUT_LIMITS[judge]
                or call.get("stream") is not False or any(name in call for name in ("top_p", "reasoning_effort"))):
            raise ValueError("Prepared recipient request differs from the exact Phase 4A profile")
        if type(call.get("seed")) is not int or not 0 <= call["seed"] < 2147483647:
            raise ValueError("Invalid recipient request seed")
        if call["seed"] != verdict_seed(unit, judge):
            raise ValueError("Recipient request seed differs from the frozen construction")
        key = (unit, judge)
        if key in seeds and seeds[key] != call["seed"]:
            raise ValueError("Recipient arms do not use a matched seed")
        seeds[key] = call["seed"]
        pair = (unit, arm)
        if pair in recipient_packets and recipient_packets[pair] != call["packet_id"]:
            raise ValueError("Recipient models do not share identical history packets")
        recipient_packets[pair] = call["packet_id"]
        reservation(call, packet, auth["prices_per_million"], auth["model_settings"])
    if set(recipient_packets.values()) != set(packets):
        raise ValueError("Prepared history packets are not a complete recipient join")
    validate_message_pairs(packets, units, scope_suffix())
    verify_files()
    if sha(path) != auth["input_manifest_sha256"]:
        raise ValueError("Recipient manifest changed during loading")
    return calls, packets, manifest


def scope_suffix():
    path = ROOT / "rejudge/phase5_protocol_v1.json"
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA256:
        raise ValueError("Frozen Phase 5 protocol changed")
    suffix = json.loads(raw)["intervention"]["scope_suffix"]
    if not isinstance(suffix, str) or not suffix:
        raise ValueError("Frozen scope suffix is absent")
    return suffix


def validate_message_pairs(packets, units, suffix):
    """The scope intervention changes only the first system message by its exact append."""
    for unit in units:
        for prompt in PROMPTS:
            common = packets[unit + ":" + prompt + "_empty"]["messages"][:2]
            if len(common) != 2 or common[0]["role"] != "system" or common[1]["role"] != "user":
                raise ValueError("Shared system/debate prefix is absent")
            for context in CONTEXTS:
                if packets[unit + ":" + prompt + "_" + context]["messages"][:2] != common:
                    raise ValueError("Shared system/debate prefix changed across contexts")
        for context in CONTEXTS:
            ordinary = packets[unit + ":ordinary_" + context]["messages"]
            scoped = packets[unit + ":scope_" + context]["messages"]
            expected = [{**ordinary[0], "content": ordinary[0]["content"] + suffix}, *ordinary[1:]]
            if scoped != expected:
                raise ValueError("Scope pair differs beyond the exact frozen system suffix")


def synthetic_panel():
    # Six transport/format checks, not an intervention-efficacy test: ordinary empty,
    # scoped answered history and scoped blocked history, each on both endpoints.
    calls, packets = phase4a.synthetic_panel()
    suffix = scope_suffix()
    for call in calls:
        context = call["arm"]
        prompt = "ordinary" if context == "empty" else "scope"
        packet = packets[call["packet_id"]]
        if prompt == "scope":
            packet["messages"][0]["content"] += suffix
        packet["messages_sha256"] = recovery.digest(packet["messages"])
        call.update(arm=prompt + "_" + context, context_arm=context, prompt_variant=prompt,
                    messages_sha256=packet["messages_sha256"], seed=verdict_seed(call["unit_id"], call["judge"]))
    return calls, packets


def _preflight_passed(rows, calls):
    planned = {c["cell_id"]: c for c in calls}
    if len(rows) != 6 or len(planned) != 6 or {r["cell_id"] for r in rows} != set(planned):
        return False
    for row in rows:
        call = planned[row["cell_id"]]
        parsed = parse_both(row["raw_verdict_text"])["strict"]
        if (not row["configuration_valid"] or row["returned_model_id"] != call["judge"] or row["uncertain_usd"]
                or row["messages_sha256"] != call["messages_sha256"] or not parsed["parse_ok"] or parsed["verdict"] != "A"):
            return False
    return True


def runtime_snapshot():
    subprocess.run(["git", "ls-files", "--error-unmatch", "--", *SOURCE_FILES], cwd=ROOT, check=True, capture_output=True)
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *SOURCE_FILES], cwd=ROOT, check=True, capture_output=True)
    return {"git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "python": sys.version, "versions": {p: importlib.metadata.version(p) for p in ("together", "httpx")},
            "source_sha256s": {name: sha(ROOT / name) for name in SOURCE_FILES}, "gpu_ordinal": None,
            "linker": "not applicable; Python/API study"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--mode", choices=("preflight", "run", "status"), required=True)
    args = parser.parse_args(argv)
    inputs, run_dir, auth_path = args.inputs.resolve(), args.run_dir.resolve(), args.authorization.resolve()
    auth_sha = sha(auth_path)
    auth = verify_authorization(auth_path, inputs, run_dir, require_paid=args.mode != "status")
    if args.mode == "status":
        path = run_dir / "status.json"
        print(path.read_text(encoding="utf-8") if path.exists() else '{"state":"not_started"}')
        return 0
    calls, packets, manifest = load_panel(inputs, auth)
    identity = {"stage": STAGE, "run_id": auth["run_id"], "authorization_sha256": auth_sha,
                "input_manifest_sha256": auth["input_manifest_sha256"]}
    limits = {"total": money(auth["approved_cap_usd"]), "uncertain": money(auth["uncertain_cap_usd"]),
              "preflight": money(auth["preflight_cap_usd"])}
    with run_lock(run_dir):
        store = Store(run_dir, identity, limits, auth["model_settings"])
        stage = "preflight" if args.mode == "preflight" else "main"
        try:
            runtime = runtime_snapshot()
            path = run_dir / "manifest.json"
            if path.exists():
                saved = json.loads(path.read_bytes())
                if saved["identity"] != identity or {k: v for k, v in saved["runtime"].items() if k != "git_commit"} != {
                        k: v for k, v in runtime.items() if k != "git_commit"}:
                    raise ValueError("Recipient resume source/runtime differs from its run manifest")
            else:
                atomic_json(path, {"created_at": utc(), "identity": identity, "runtime": runtime,
                    "authorization": auth, "input_hashes": manifest["outputs"], "request_count": len(calls),
                    "provider_settings": {"sdk_retries": 0, "stream": False, "proactive_deadline_seconds": 1200,
                        "timeout_seconds": {"connect": 10, "read": 600, "write": 60, "pool": 60},
                        "extra_reasoning_fields": {}, "request_profile": "exact_phase4a"},
                    "result_semantics": "completed invalid verdicts retained once; transport collection only"})
            if sha(auth_path) != auth_sha:
                raise ValueError("Recipient authorization changed during preparation")
            preflight_calls, preflight_packets = synthetic_panel()
            if stage == "preflight":
                calls, packets = preflight_calls, preflight_packets
            else:
                receipt = run_dir / "preflight.json"
                if (not receipt.exists() or json.loads(receipt.read_bytes()).get("status") != "passed"
                        or not _preflight_passed(store.rows("preflight"), preflight_calls) or store.fatal_reason()):
                    raise ValueError("Durable synthetic recipient preflight has not passed")
            provider = Provider() if len(store.completed(stage)) < len(calls) and not store.fatal_reason() else None
            status = execute(store, calls, packets, stage, auth["prices_per_million"], provider)
            if stage == "preflight":
                passed = status["state"] == "requests_complete" and not store.fatal_reason() and _preflight_passed(store.rows(stage), calls)
                atomic_json(run_dir / "preflight.json", {"status": "passed" if passed else "not_passed", "completed_at": utc(),
                                                         "synthetic_only": True, "spend": status["spend"]})
                print(canonical({"mode": stage, "status": "passed" if passed else "not_passed", "spend": status["spend"]}))
                return 0 if passed else 2
            if status["state"] == "requests_complete":
                atomic_json(run_dir / "completion.json", {"status": "requests_complete", "completed_at": utc(),
                    "run_id": auth["run_id"], "completed": len(calls), "spend": status["spend"],
                    "results_sha256": sha(run_dir / "results.jsonl"), "scope": "transport_collection_only",
                    "next_stage_paid_authorized": False})
                store.status(stage, len(calls), "complete", completion_scope="transport_collection_only")
                return 0
            return 2
        except BaseException as exc:
            store.event(kind="runner_exception", error_type=type(exc).__name__)
            store.status(stage, 6 if stage == "preflight" else len(calls), "runner_exception", error_type=type(exc).__name__)
            raise
        finally:
            store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
