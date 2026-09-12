"""Durable Phase 4B recipient verdict collection using the exact Phase 4A profile.

This runner collects the frozen original/repaired history panel. It neither
changes repair labels nor authorizes or starts Phase 4C. Empty and malformed
received verdicts are retained once; only uncertain deliveries are retried.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import uuid

from rejudge import phase4_runner as phase4a
from rejudge import phase4b_runner as recovery
from rejudge.parsers import parse_both

ROOT = Path(__file__).resolve().parents[1]
QWEN, LLAMA = phase4a.QWEN, phase4a.LLAMA
MODELS = (QWEN, LLAMA)
ARMS = ("qwen_history_original", "qwen_history_repaired", "llama_history_original", "llama_history_repaired")
OUTPUT_LIMITS = {QWEN: 16384, LLAMA: 512}
MULTIPLIERS = {QWEN: 3, LLAMA: 1}
SEED_NAMESPACE = "phase4b-recipient-verdict-v1"
EXPECTED_MAIN_CALLS = 5248
SOURCE_FILES = ("rejudge/phase4b_recipient_runner.py", "rejudge/phase4b_runner.py", "rejudge/phase4_runner.py",
                "rejudge/api_client.py", "rejudge/parsers.py", "analysis/infra/parsing.py", "rejudge/durable_fs.py")
canonical, sha, utc = phase4a.canonical, phase4a.sha, phase4a.utc
money, usd, token_cost = phase4a.money, phase4a.usd, phase4a.token_cost
atomic_json, atomic_bytes, run_lock = phase4a.atomic_json, phase4a.atomic_bytes, phase4a.run_lock
request_kwargs, Provider = phase4a.request_kwargs, phase4a.Provider
reservation, execute = recovery.reservation, recovery.execute


def verdict_seed(unit_id, judge):
    return int(hashlib.sha256((SEED_NAMESPACE + "|" + unit_id + "|" + judge).encode()).hexdigest()[:8], 16) % 2147483647


class Store(recovery.Store):
    """Reuse the tested database, retry schedule, and coordinator without global overrides."""

    status = phase4a.Store.status

    def reserve(self, call, packet, stage, prices):
        amount = reservation(call, packet, prices, self.model_settings)
        request_sha = recovery.digest(request_kwargs(call, packet))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.fatal_reason():
                raise ValueError("A durable fatal transport or accounting stop is active")
            if self.db.execute("SELECT 1 FROM results WHERE cell_id=?", (call["cell_id"],)).fetchone():
                raise ValueError("Refusing to dispatch a completed recipient verdict")
            previous = self.db.execute("SELECT stage,judge,request_sha256,state FROM attempts WHERE cell_id=?",
                                       (call["cell_id"],)).fetchall()
            if any(r["stage"] != stage or r["judge"] != call["judge"] or r["request_sha256"] != request_sha for r in previous):
                raise ValueError("Retry request differs from its durable identity")
            if any(r["state"] == "inflight" for r in previous):
                raise ValueError("The recipient verdict already has an in-flight attempt")
            totals = self.totals()
            reason = None
            if totals["exposure"] + amount > self.limits["total"]:
                reason = "total_cap"
            elif totals["uncertain"] + totals["inflight"] + amount > self.limits["uncertain"]:
                reason = "uncertain_cap"
            elif stage == "preflight" and (totals["preflight"] + amount > self.limits["preflight"] or totals["preflight_attempts"] >= 16):
                reason = "preflight_cap"
            if reason:
                self.db.rollback()
                return None, reason
            attempt = uuid.uuid4().hex
            self.db.execute("INSERT INTO attempts(attempt_id,cell_id,stage,judge,started_at,state,reserved,request_sha256)"
                            " VALUES (?,?,?,?,?,'inflight',?,?)",
                            (attempt, call["cell_id"], stage, call["judge"], utc(), amount, request_sha))
            self.db.commit()
            return attempt, None
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, attempt, call, outcome, prices):
        row = self.db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt,)).fetchone()
        if row is None or row["state"] != "inflight" or row["cell_id"] != call["cell_id"] or row["judge"] != call["judge"]:
            raise ValueError("Attempt is absent, resolved, or bound to another recipient verdict")
        raw = outcome.get("response")
        if raw is None:
            return phase4a.Store.finish(self, attempt, call, {**outcome, "error": outcome.get("error", "unobserved_delivery")}, prices)
        if not isinstance(raw, dict):
            raise ValueError("Provider response is not a serializable response object")
        usage = raw.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        known = all(type(t) is int and t >= 0 for t in (prompt, completion))
        actual = token_cost(prompt, completion, prices[call["judge"]]) if known else 0
        uncertain = 0 if known else row["reserved"]
        choices = raw.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        content = content if isinstance(content, str) else ""
        mismatch = raw.get("model") != call["judge"]
        exceeded = actual > row["reserved"]
        fatal = "returned_model_mismatch" if mismatch else ("reservation_underestimated" if exceeded else None)
        result = {name: call[name] for name in ("cell_id", "unit_id", "judge", "arm", "packet_id", "messages_sha256")}
        result.update({"raw_verdict_text": content, "completed_at": utc(), "attempt_id": attempt,
                       "usage": usage, "cost_usd": usd(actual), "uncertain_usd": usd(uncertain),
                       "returned_model_id": raw.get("model"), "response_id": raw.get("id"),
                       "finish_reason": choice.get("finish_reason"), "system_fingerprint": raw.get("system_fingerprint"),
                       "request_sha256": row["request_sha256"], "configuration_valid": not mismatch,
                       "duration_seconds": outcome.get("duration_seconds")})
        with self.db:
            self.db.execute("UPDATE attempts SET state=?,actual=?,uncertain=?,finished_at=?,response=? WHERE attempt_id=?",
                            ("success" if known else "completed_unknown_usage", actual, uncertain, utc(), canonical(raw), attempt))
            self.db.execute("INSERT INTO results VALUES (?,?,?)", (call["cell_id"], row["stage"], canonical(result)))
            if fatal:
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('fatal_reason',?)", (fatal,))
            self.db.execute("DELETE FROM retry_state WHERE judge=? AND cell_id=?", (call["judge"], call["cell_id"]))
        return {"completed": True, "fatal": bool(fatal), "error": fatal,
                "strict_valid": parse_both(content)["strict"]["parse_ok"]}


def verify_authorization(path, inputs, run_dir, *, require_paid=True):
    auth = json.loads(path.read_bytes())
    if auth.get("stage") != "4B-recipient" or not isinstance(auth.get("run_id"), str) or not auth["run_id"]:
        raise ValueError("Phase 4B recipient identity is absent")
    if require_paid and auth.get("paid_execution_authorized") is not True:
        raise ValueError("Phase 4B recipient paid execution authorization is absent")
    if Path(auth["inputs"]).resolve() != inputs or Path(auth["run_directory"]).resolve() != run_dir:
        raise ValueError("Authorized recipient input/output paths do not match")
    if run_dir.is_relative_to(ROOT) or inputs.is_relative_to(run_dir) or run_dir.is_relative_to(inputs):
        raise ValueError("Recipient outputs must be separate from Git and frozen inputs")
    if not require_paid:
        return auth
    if sha(ROOT / "rejudge/phase4_protocol_v1.json") != auth.get("protocol_sha256"):
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
        raise ValueError("The frozen recipient experiment requires exactly 5248 verdict calls")
    for name in ("approved_cap_usd", "uncertain_cap_usd", "preflight_cap_usd"):
        recovery._positive(auth[name], name)
    if max(auth["uncertain_cap_usd"], auth["preflight_cap_usd"]) > auth["approved_cap_usd"]:
        raise ValueError("Recipient subcaps exceed the approved aggregate cap")
    return auth


def load_panel(inputs, auth):
    path = inputs / "manifest.json"
    if sha(path) != auth["input_manifest_sha256"]:
        raise ValueError("Prepared recipient manifest changed")
    manifest = json.loads(path.read_bytes())
    if manifest.get("schema_version") != "phase4b_recipient_prepared_panel_v1":
        raise ValueError("Unsupported recipient panel schema")
    if (manifest.get("main_verdict_calls") != auth["main_verdict_calls"] or set(manifest.get("models", [])) != set(MODELS)
            or manifest.get("protocol_sha256") != auth["protocol_sha256"] or manifest.get("arms") != list(ARMS)
            or manifest.get("call_seed_namespace") != SEED_NAMESPACE):
        raise ValueError("Prepared recipient scope differs from authorization")
    expected_settings = {j: {"temperature": .3, "max_tokens": OUTPUT_LIMITS[j], "stream": False} for j in MODELS}
    if manifest.get("model_settings") != expected_settings:
        raise ValueError("Prepared recipient decoding profile changed")
    if auth.get("repair_labels_sha256") and manifest.get("provenance", {}).get("repair_labels_sha256") != auth["repair_labels_sha256"]:
        raise ValueError("Authorized reviewed labels differ from the prepared panel")
    required = {"calls.jsonl", "packets.jsonl", "units_private.jsonl", "edit_map_private.jsonl"}
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
    if (len(packets) != len(packet_rows) or len(units) != len(unit_rows) or not units
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
    verify_files()
    if sha(path) != auth["input_manifest_sha256"]:
        raise ValueError("Recipient manifest changed during loading")
    return calls, packets, manifest


def synthetic_panel():
    # Reuse the six synthetic 4A endpoint checks: empty, answered, and blocked histories.
    return phase4a.synthetic_panel()


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
    identity = {"stage": "4B-recipient", "run_id": auth["run_id"], "authorization_sha256": auth_sha,
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
                    "phase4c_authorized": False})
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
