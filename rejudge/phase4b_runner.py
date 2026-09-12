"""Blinded Phase 4B adjudication collection with transactional delivery recovery.

Raw adjudications, including empty or malformed answers, are final observations.
Only failed deliveries are retried. SQLite accounts for every physical attempt;
JSONL exports are deterministic views, not the source of recovery state.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from rejudge import phase4_runner as durable
from rejudge.api_client import _estimate_usage, estimate_context_tokens, RejudgeClient
from rejudge.phase4b_labels import assess_response, call_seed, messages_for

ROOT = Path(__file__).resolve().parents[1]
canonical, sha = durable.canonical, durable.sha
money, usd, token_cost = durable.money, durable.usd, durable.token_cost
utc, atomic_json, atomic_bytes, run_lock = durable.utc, durable.atomic_json, durable.atomic_bytes, durable.run_lock
SOURCE_FILES = ("rejudge/phase4b_runner.py", "rejudge/phase4b_labels.py", "rejudge/phase4_runner.py",
                "rejudge/api_client.py", "rejudge/durable_fs.py")
PROFILE_FIELDS = ("temperature", "top_p", "reasoning_effort", "max_tokens")


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def request_kwargs(call, packet):
    # No identifiers, donor information, gold labels, or packet metadata are sent.
    return {"model": call["judge"], "messages": packet["messages"], "seed": call["seed"],
            **{name: call[name] for name in PROFILE_FIELDS}}


def reservation(call, packet, prices, model_settings):
    setting = model_settings[call["judge"]]
    prompt, completion = _estimate_usage(packet["messages"], call["max_tokens"])
    _, context = estimate_context_tokens(packet["messages"], call["max_tokens"])
    if context > setting["context_length"]:
        raise ValueError("Prepared request exceeds the configured context limit")
    # The multiplier is a spend reserve, not extra requested context or output.
    completion = math.ceil(completion * setting["reservation_multiplier"])
    return token_cost(prompt, completion, prices[call["judge"]])


class Store(durable.Store):
    def __init__(self, directory, identity, limits, model_settings):
        super().__init__(directory, identity, limits)
        self.model_settings = model_settings
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS retry_state (judge TEXT PRIMARY KEY, cell_id TEXT NOT NULL,"
                            " eligible_at REAL NOT NULL, failures INTEGER NOT NULL)")
            self.db.execute("CREATE INDEX IF NOT EXISTS attempts_cell ON attempts(cell_id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS attempts_stage_model_state ON attempts(stage,judge,state)")
            operational = canonical({"limits": limits, "model_settings": model_settings})
            prior = self.db.execute("SELECT value FROM metadata WHERE key='adjudication_settings'").fetchone()
            if prior and prior[0] != operational:
                raise ValueError("Adjudication limits or model settings changed on resume")
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('adjudication_settings',?)", (operational,))

    def reserve(self, call, packet, stage, prices):
        amount = reservation(call, packet, prices, self.model_settings)
        request_sha = digest(request_kwargs(call, packet))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.fatal_reason():
                raise ValueError("A durable fatal transport or accounting stop is active")
            if self.db.execute("SELECT 1 FROM results WHERE cell_id=?", (call["cell_id"],)).fetchone():
                raise ValueError("Refusing to dispatch a completed adjudication")
            prior = self.db.execute("SELECT stage,judge,request_sha256,state FROM attempts WHERE cell_id=?",
                                    (call["cell_id"],)).fetchall()
            if any(r["stage"] != stage or r["judge"] != call["judge"] or r["request_sha256"] != request_sha for r in prior):
                raise ValueError("Retry request differs from its durable identity")
            if any(r["state"] == "inflight" for r in prior):
                raise ValueError("The adjudication already has an in-flight attempt")
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
            raise ValueError("Attempt is absent, resolved, or bound to another adjudication")
        raw = outcome.get("response")
        if raw is None:
            # Inherits atomic unknown-charge/fatal classification; no verdict parser is used here.
            return super().finish(attempt, call, {**outcome, "error": outcome.get("error", "unobserved_delivery")}, prices)
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
        result = {name: call[name] for name in ("cell_id", "claim_id", "judge", "packet_id", "messages_sha256")}
        result.update({"raw_adjudication_text": content, "completed_at": utc(), "attempt_id": attempt,
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
        return {"completed": True, "fatal": bool(fatal), "error": fatal}

    def retries(self, stage, cooldown_seconds, cycle_seconds=900):
        # Reconstruct a crash between the durable unknown and its scheduling update.
        done = self.completed(stage)
        for judge in self.model_settings:
            prior = self.db.execute("SELECT * FROM retry_state WHERE judge=?", (judge,)).fetchone()
            if prior and prior["cell_id"] not in done:
                continue
            with self.db:
                self.db.execute("DELETE FROM retry_state WHERE judge=?", (judge,))
            row = self.db.execute("SELECT cell_id,finished_at FROM attempts WHERE judge=? AND stage=? AND state='unknown'"
                                  " AND cell_id NOT IN (SELECT cell_id FROM results) ORDER BY rowid LIMIT 1", (judge, stage)).fetchone()
            if row:
                self.defer(judge, row["cell_id"], cooldown_seconds, cycle_seconds=cycle_seconds)
        return {r["judge"]: dict(r) for r in self.db.execute("SELECT * FROM retry_state")}

    def defer(self, judge, cell, cooldown_seconds, retry_after_seconds=0, *, cycle_seconds=900):
        rows = self.db.execute("SELECT finished_at FROM attempts WHERE judge=? AND cell_id=? AND state='unknown' ORDER BY rowid",
                               (judge, cell)).fetchall()
        if not rows:
            raise ValueError("Cooldown requires a durable unknown delivery")
        delay = max(retry_after_seconds, min(900, cooldown_seconds * 2 ** min(len(rows) - 1, 4)))
        if len(rows) % 3 == 0:
            delay = max(delay, cycle_seconds)
        last = datetime.fromisoformat(rows[-1][0]).timestamp()
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO retry_state VALUES (?,?,?,?)", (judge, cell, last + delay, len(rows)))

    def status(self, stage, expected, state, **extra):
        totals, rows = self.totals(), self.rows(stage)
        result = {"updated_at": utc(), "pid": os.getpid(), "stage": stage, "state": state,
                  "completed": len(rows), "expected": expected, "by_judge": dict(Counter(r["judge"] for r in rows)),
                  "spend": {k + "_usd": usd(v) for k, v in totals.items() if k != "preflight_attempts"},
                  "preflight_attempts": totals["preflight_attempts"], "cap_usd": usd(self.limits["total"]),
                  "last_completion_at": max((r["completed_at"] for r in rows), default=None), **extra}
        atomic_json(self.directory / "status.json", result)
        return result


class Provider(durable.Provider):
    # Reuses the pinned Together client, zero SDK retries, TLS and timeout profile.
    def __call__(self, call, packet):
        start = time.monotonic()
        try:
            response = RejudgeClient._call_with_deadline(
                lambda: self.client.chat.completions.create(**request_kwargs(call, packet)), 1200)
            return {"response": response.model_dump(mode="json"), "duration_seconds": time.monotonic() - start}
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            headers = getattr(getattr(exc, "response", None), "headers", {})
            try:
                delay = float(headers.get("x-ratelimit-reset", headers.get("retry-after", 0)))
                delay = delay if math.isfinite(delay) and delay > 0 else 0
            except (ValueError, TypeError):
                delay = 0
            return {"error": type(exc).__name__ + (" HTTP " + str(status) if status else ""),
                    "fatal": status in (400, 401, 403, 404, 422), "retry_after_seconds": delay,
                    "duration_seconds": time.monotonic() - start}


def execute(store, calls, packets, stage, prices, provider, *, cooldown_seconds=60, poll_seconds=1,
            cycle_seconds=900, tune_seconds=120):
    """One coordinator owns accounting; threads only call the bounded provider."""
    store.recover()
    done = store.completed(stage)
    pending = {j: deque(c for c in calls if c["judge"] == j and c["cell_id"] not in done) for j in store.model_settings}
    active, counts = {}, Counter()
    maximum = {j: s.get("max_workers", 16) for j, s in store.model_settings.items()}
    capacity = {j: min(store.model_settings[j].get("initial_workers", 4), n) for j, n in maximum.items()}
    samples, last_tune = Counter(), time.monotonic()
    trial_capacity, trial_rate = {}, {}
    halted, last_export = store.fatal_reason(), 0.0
    store.event(kind="start", stage=stage, completed=len(done), workers=capacity)
    with ThreadPoolExecutor(max_workers=sum(maximum.values())) as pool:
        while any(pending.values()) or active:
            for future in list(active):
                if not future.done():
                    continue
                call, attempt = active.pop(future)
                judge = call["judge"]
                counts[judge] -= 1
                try:
                    outcome = future.result()
                except Exception as exc:
                    outcome = {"error": type(exc).__name__, "fatal": True}
                result = store.finish(attempt, call, outcome, prices)
                if result["completed"]:
                    done.add(call["cell_id"])
                    samples[judge] += 1
                else:
                    store.defer(judge, call["cell_id"], cooldown_seconds, outcome.get("retry_after_seconds", 0),
                                cycle_seconds=cycle_seconds)
                    pending[judge].appendleft(call)
                    store.event(kind="transport_failure", judge=judge, cell_id=call["cell_id"], error=result["error"])
                if result["fatal"]:
                    halted = result["error"]
            retries = store.retries(stage, cooldown_seconds, cycle_seconds)
            if (store.directory / "pause.request").exists():
                halted = "operator_pause"
            if not halted:
                for judge, queue in pending.items():
                    retry = retries.get(judge)
                    scans = len(queue)
                    while queue and counts[judge] < capacity[judge] and scans:
                        scans -= 1
                        call = queue.popleft()
                        if retry and (call["cell_id"] != retry["cell_id"] or counts[judge] or time.time() < retry["eligible_at"]):
                            queue.append(call)
                            continue
                        attempt, cap = store.reserve(call, packets[call["packet_id"]], stage, prices)
                        if cap:
                            queue.appendleft(call)
                            if not active:
                                halted = cap
                            break
                        counts[judge] += 1
                        active[pool.submit(provider, call, packets[call["packet_id"]])] = (call, attempt)
            now = time.monotonic()
            if now - last_tune >= tune_seconds and not halted:
                for judge in pending:
                    before = capacity[judge]
                    rate = samples[judge] / max(now - last_tune, 1)
                    if judge in retries:
                        capacity[judge] = max(1, before // 2)
                        trial_capacity.pop(judge, None)
                        trial_rate.pop(judge, None)
                    elif samples[judge] >= 8:
                        if judge in trial_rate and rate < trial_rate[judge] * 1.05:
                            capacity[judge] = trial_capacity.pop(judge)
                            trial_rate.pop(judge)
                        elif before < maximum[judge]:
                            trial_capacity[judge], trial_rate[judge] = before, rate
                            capacity[judge] = min(maximum[judge], before * 2)
                    store.event(kind="throughput", judge=judge, completed_per_hour=rate * 3600,
                                previous_workers=before, workers=capacity[judge])
                samples.clear()
                last_tune = now
            if now - last_export >= 10 or (not active and (halted or not any(pending.values()))):
                store.export(stage)
                store.status(stage, len(calls), "stopping" if halted and active else ("paused" if halted else "running"),
                             halt_reason=halted, active=dict(counts), workers=capacity,
                             retry_deadlines={j: datetime.fromtimestamp(r["eligible_at"], timezone.utc).isoformat() for j, r in retries.items()})
                last_export = now
            if halted and not active:
                break
            if active or any(pending.values()):
                time.sleep(poll_seconds)
    store.export(stage)
    state = "requests_complete" if len(store.completed(stage)) == len(calls) and not halted else "paused"
    status = store.status(stage, len(calls), state, halt_reason=halted, active=dict(counts), workers=capacity)
    store.event(kind="stop", stage=stage, state=state, halt_reason=halted)
    return status


def _positive(value, name, *, integer=False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid " + name)
    if integer and type(value) is not int or maximum is not None and value > maximum:
        raise ValueError("Invalid " + name)


def verify_authorization(path, inputs, run_dir, *, require_paid=True):
    auth = json.loads(path.read_bytes())
    if auth.get("stage") != "4B" or not isinstance(auth.get("run_id"), str) or not auth["run_id"]:
        raise ValueError("Phase 4B run identity is absent")
    if require_paid and auth.get("paid_execution_authorized") is not True:
        raise ValueError("Phase 4B paid execution authorization is absent")
    if require_paid and sha(ROOT / "rejudge/phase4_protocol_v1.json") != auth.get("protocol_sha256"):
        raise ValueError("Scientific protocol changed")
    if Path(auth["inputs"]).resolve() != inputs or Path(auth["run_directory"]).resolve() != run_dir:
        raise ValueError("Authorized input/output paths do not match")
    if run_dir.is_relative_to(ROOT) or run_dir == inputs or inputs.is_relative_to(run_dir) or run_dir.is_relative_to(inputs):
        raise ValueError("Live outputs must be separate from Git and prepared inputs")
    models = auth["models"]
    if not isinstance(models, list) or len(models) != 2 or len(set(models)) != 2 or any(not isinstance(j, str) or not j for j in models):
        raise ValueError("Exactly two distinct adjudicator model IDs are required")
    if set(auth["prices_per_million"]) != set(models) or set(auth["model_settings"]) != set(models):
        raise ValueError("Model price/settings roster differs")
    for judge in models:
        setting = auth["model_settings"][judge]
        for name in ("max_tokens", "context_length"):
            _positive(setting[name], name, integer=True)
        _positive(setting["reservation_multiplier"], "reservation_multiplier", maximum=16)
        if setting["reservation_multiplier"] < 1:
            raise ValueError("Reservation multiplier cannot reduce the output reserve")
        _positive(setting.get("max_workers", 16), "max_workers", integer=True, maximum=16)
        _positive(setting.get("initial_workers", 4), "initial_workers", integer=True, maximum=setting.get("max_workers", 16))
        _positive(setting["top_p"], "top_p", maximum=1)
        temperature = setting["temperature"]
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise ValueError("Invalid temperature")
        if not isinstance(setting["reasoning_effort"], str) or not setting["reasoning_effort"]:
            raise ValueError("Explicit reasoning effort is required")
        for rate in ("input", "output"):
            _positive(auth["prices_per_million"][judge][rate], rate + " price")
    if require_paid or auth.get("adjudication_calls") is not None:
        _positive(auth["adjudication_calls"], "adjudication_calls", integer=True)
    if require_paid:
        for name in ("approved_cap_usd", "uncertain_cap_usd", "preflight_cap_usd"):
            _positive(auth[name], name)
        if max(auth["uncertain_cap_usd"], auth["preflight_cap_usd"]) > auth["approved_cap_usd"]:
            raise ValueError("Subcaps exceed the aggregate cap")
    return auth


def load_panel(inputs, auth):
    manifest_path = inputs / "adjudication_manifest.json"
    if sha(manifest_path) != auth["input_manifest_sha256"]:
        raise ValueError("Prepared adjudication manifest changed")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("prompt_script_sha256") != sha(ROOT / "rejudge/phase4b_labels.py"):
        raise ValueError("Adjudication prompt source changed after preparation")
    for name in ("adjudication_calls", "models", "model_settings", "prices_per_million"):
        if manifest.get(name) != auth[name]:
            raise ValueError("Prepared panel differs from authorized " + name)
    if not isinstance(manifest.get("oracle_contract"), str) or not manifest["oracle_contract"].strip():
        raise ValueError("The frozen oracle contract is absent")
    if not {"claim_packets.jsonl", "calls.jsonl"} <= set(manifest["outputs"]):
        raise ValueError("Adjudication inputs lack mandatory hash bindings")
    for name, binding in manifest["outputs"].items():
        path = inputs / name
        if Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink() or not path.is_file():
            raise ValueError("Unsafe prepared input path")
        expected = binding if isinstance(binding, str) else binding.get("sha256", binding.get("raw_sha256"))
        if sha(path) != expected:
            raise ValueError("Prepared input changed: " + name)
    packet_rows = [json.loads(line) for line in (inputs / "claim_packets.jsonl").read_text(encoding="utf-8").splitlines()]
    packets = {p["packet_id"]: p for p in packet_rows}
    calls = [json.loads(line) for line in (inputs / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(packets) != len(packet_rows) or len(calls) != auth["adjudication_calls"] or len({c["cell_id"] for c in calls}) != len(calls):
        raise ValueError("Duplicate or incorrect prepared adjudication count")
    groups = defaultdict(dict)
    for call in calls:
        judge = call["judge"]
        packet = packets[call["packet_id"]]
        messages = packet["messages"]
        if (not isinstance(messages, list) or not messages or any(not isinstance(m, dict) or set(m) != {"role", "content"}
                or m["role"] not in ("system", "user", "assistant") or not isinstance(m["content"], str) for m in messages)):
            raise ValueError("Invalid provider message shape")
        if judge not in auth["models"] or digest(messages) != call["messages_sha256"] or digest(messages) != packet["messages_sha256"]:
            raise ValueError("Prepared request identity mismatch")
        setting = auth["model_settings"][judge]
        if (any(call[name] != setting[name] for name in PROFILE_FIELDS) or call.get("stream", False) is not False
                or any(isinstance(call[name], bool) for name in ("temperature", "top_p", "max_tokens"))
                or type(call["max_tokens"]) is not int):
            raise ValueError("Prepared decoding profile differs from authorization")
        if type(call["seed"]) is not int or not 0 <= call["seed"] < 2147483647:
            raise ValueError("Invalid request seed")
        if judge in groups[call["claim_id"]]:
            raise ValueError("A claim has duplicate adjudication recipients")
        groups[call["claim_id"]][judge] = call["packet_id"]
        if packet.get("claim_id", call["claim_id"]) != call["claim_id"]:
            raise ValueError("Packet and claim identity differ")
        reservation(call, packet, auth["prices_per_million"], auth["model_settings"])
    if any(set(g) != set(auth["models"]) or len(set(g.values())) != 1 for g in groups.values()):
        raise ValueError("Both recipients must receive the exact same claim packet")
    if {c["packet_id"] for c in calls} != set(packets) or len(groups) != len(packets):
        raise ValueError("Claim packets are not a unique complete adjudication panel")
    # Bind the bytes actually parsed, not just a pre-read observation.
    if sha(manifest_path) != auth["input_manifest_sha256"]:
        raise ValueError("Adjudication manifest changed during loading")
    for name in ("claim_packets.jsonl", "calls.jsonl"):
        bound = manifest["outputs"][name]
        if sha(inputs / name) != (bound if isinstance(bound, str) else bound.get("sha256", bound.get("raw_sha256"))):
            raise ValueError("Adjudication inputs changed during loading")
    return calls, packets, manifest


def synthetic_panel(auth, oracle_contract):
    world = "Amberford has exactly one bridge. The bridge is blue. The bridge is not red. The mayor is Mira."
    examples = (("YES", "Amberford has exactly one bridge."), ("NO", "The bridge is red."),
                ("NOT ADDRESSED", "Mira was born in Amberford."))
    calls, packets, expected = [], {}, {}
    for index, (label, claim) in enumerate(examples):
        claim_id = "synthetic-" + str(index)
        messages = messages_for(world, claim, oracle_contract)
        packets[claim_id] = {"packet_id": claim_id, "claim_id": claim_id,
                             "messages": messages, "messages_sha256": digest(messages)}
        for judge in auth["models"]:
            cell = "preflight:" + judge + ":" + claim_id
            calls.append({"cell_id": cell, "claim_id": claim_id, "judge": judge, "packet_id": claim_id,
                          "messages_sha256": digest(messages), "seed": call_seed(claim_id, judge),
                          **{name: auth["model_settings"][judge][name] for name in PROFILE_FIELDS}})
            expected[cell] = {"label": label, "world": world}
    return calls, packets, expected


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
    identity = {"stage": "4B", "run_id": auth["run_id"], "authorization_sha256": auth_sha,
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
                    raise ValueError("Resume source/runtime differs from the run manifest")
            else:
                atomic_json(path, {"created_at": utc(), "identity": identity, "runtime": runtime,
                                   "authorization": auth, "input_hashes": manifest["outputs"], "request_count": len(calls),
                                   "provider_settings": {"sdk_retries": 0, "stream": False, "proactive_deadline_seconds": 1200,
                                                         "timeout_seconds": {"connect": 10, "read": 600, "write": 60, "pool": 60}},
                                   "result_semantics": "raw adjudications; malformed or empty answers are never regenerated"})
            if sha(auth_path) != auth_sha:
                raise ValueError("Authorization changed during preparation")
            if stage == "preflight":
                calls, packets, expected = synthetic_panel(auth, manifest["oracle_contract"])
            else:
                preflight = run_dir / "preflight.json"
                rows = store.rows("preflight")
                if not preflight.exists() or json.loads(preflight.read_bytes()).get("status") != "passed" or len(rows) != 6:
                    raise ValueError("Synthetic adjudicator preflight has not passed")
                _, _, expected = synthetic_panel(auth, manifest["oracle_contract"])
                if not _preflight_passed(rows, expected) or store.fatal_reason():
                    raise ValueError("Durable synthetic adjudicator preflight does not verify")
            provider = Provider() if len(store.completed(stage)) < len(calls) and not store.fatal_reason() else None
            status = execute(store, calls, packets, stage, auth["prices_per_million"], provider)
            if stage == "preflight":
                passed = status["state"] == "requests_complete" and not store.fatal_reason() and _preflight_passed(store.rows(stage), expected)
                atomic_json(run_dir / "preflight.json", {"status": "passed" if passed else "not_passed", "completed_at": utc(),
                                                         "synthetic_only": True, "spend": status["spend"]})
                print(canonical({"mode": stage, "status": "passed" if passed else "not_passed", "spend": status["spend"]}))
                return 0 if passed else 2
            if status["state"] == "requests_complete":
                atomic_json(run_dir / "completion.json", {"status": "requests_complete", "completed_at": utc(),
                    "run_id": auth["run_id"], "completed": len(calls), "spend": status["spend"],
                    "results_sha256": sha(run_dir / "results.jsonl"), "scope": "transport_collection_only"})
                store.status(stage, len(calls), "complete", completion_scope="transport_collection_only")
                return 0
            return 2
        except BaseException as exc:
            store.event(kind="runner_exception", error_type=type(exc).__name__)
            store.status(stage, 6 if stage == "preflight" else len(calls), "runner_exception", error_type=type(exc).__name__)
            raise
        finally:
            store.db.close()


def _preflight_passed(rows, expected):
    if len(rows) != len(expected) or {r["cell_id"] for r in rows} != set(expected):
        return False
    for row in rows:
        target = expected[row["cell_id"]]
        assessment = assess_response(row["raw_adjudication_text"], target["world"])
        if not row["configuration_valid"] or row["uncertain_usd"] or not assessment["eligible"] or assessment["label"] != target["label"]:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
