"""Bounded Phase 4A captured-history replay with transactional recovery.

SQLite is the durable source of completed cells and charged attempts. JSON files
are replaceable views. Only packet.messages crosses the provider boundary.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

from rejudge.api_client import _estimate_usage, estimate_context_tokens, RejudgeClient, build_pinned_together_client
from rejudge.durable_fs import windows_move_write_through
from rejudge.parsers import parse_both

ROOT = Path(__file__).resolve().parents[1]
QWEN = "Qwen/Qwen3.8-2.4T-A95B"
LLAMA = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
JUDGES = (QWEN, LLAMA)
SCALE = 1_000_000_000


def utc():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def money(value):
    return int(Decimal(str(value)) * SCALE)


def usd(value):
    return round(value / SCALE, 9)


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "nt":
        windows_move_write_through(temp, path, replace_existing=True)
    else:
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode())


@contextmanager
def run_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "runner.lock").open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def token_cost(prompt, completion, rates):
    return int((Decimal(prompt) * Decimal(str(rates["input"])) +
                Decimal(completion) * Decimal(str(rates["output"]))) * 1000)


def request_kwargs(call, packet):
    # Keep the historical absent-field profile: stream=False is the SDK default.
    return {"model": call["judge"], "messages": packet["messages"],
            "temperature": call["temperature"], "max_tokens": call["max_tokens"],
            "seed": call["seed"]}


def reservation(call, packet, prices):
    prompt, completion = _estimate_usage(packet["messages"], call["max_tokens"])
    if call["judge"] == QWEN:
        completion *= 3
    _, total_context = estimate_context_tokens(packet["messages"], call["max_tokens"])
    if total_context > 131072:
        raise ValueError("Prepared request exceeds conservative context limit")
    return token_cost(prompt, completion, prices[call["judge"]])


class Store:
    def __init__(self, directory, identity, limits):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "state.sqlite3", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY, cell_id TEXT NOT NULL, stage TEXT NOT NULL,
                judge TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                state TEXT NOT NULL, reserved INTEGER NOT NULL, actual INTEGER NOT NULL DEFAULT 0,
                uncertain INTEGER NOT NULL DEFAULT 0, request_sha256 TEXT NOT NULL,
                error TEXT, response TEXT);
            CREATE TABLE IF NOT EXISTS results (
                cell_id TEXT PRIMARY KEY, stage TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, payload TEXT NOT NULL);
        """)
        self.limits = limits
        serialized = canonical(identity)
        prior = self.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if prior is not None and prior[0] != serialized:
            raise ValueError("Run identity differs from the durable store")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('identity',?)", (serialized,))

    def event(self, **payload):
        with self.db:
            self.db.execute("INSERT INTO events(at,payload) VALUES (?,?)", (utc(), canonical(payload)))

    def recover(self):
        with self.db:
            pending = self.db.execute("SELECT count(*) FROM attempts WHERE state='inflight'").fetchone()[0]
            self.db.execute("UPDATE attempts SET state='unknown',uncertain=reserved,finished_at=?,"
                            "error='environmental interruption before durable response' WHERE state='inflight'", (utc(),))
        if pending:
            self.event(kind="recovery", missing_attempts_retained_as_uncertain=pending)
        return pending

    def totals(self):
        row = self.db.execute("SELECT coalesce(sum(actual),0),coalesce(sum(uncertain),0),"
                              "coalesce(sum(CASE WHEN state='inflight' THEN reserved ELSE 0 END),0) FROM attempts").fetchone()
        preflight = self.db.execute("SELECT coalesce(sum(actual+uncertain+CASE WHEN state='inflight' THEN reserved ELSE 0 END),0),"
                                   "count(*) FROM attempts WHERE stage='preflight'").fetchone()
        return {"actual": row[0], "uncertain": row[1], "inflight": row[2],
                "exposure": sum(row), "preflight": preflight[0], "preflight_attempts": preflight[1]}

    def completed(self, stage):
        return {r[0] for r in self.db.execute("SELECT cell_id FROM results WHERE stage=?", (stage,))}

    def fatal_reason(self):
        row = self.db.execute("SELECT value FROM metadata WHERE key='fatal_reason'").fetchone()
        return row[0] if row else None

    def interrupted_probes(self, stage):
        probes = {}
        for judge in JUDGES:
            last = self.db.execute("SELECT cell_id,state FROM attempts WHERE judge=? AND stage=? ORDER BY finished_at DESC LIMIT 1",
                                   (judge, stage)).fetchone()
            if last and last["state"] == "unknown" and last["cell_id"] not in self.completed(stage):
                probes[judge] = last["cell_id"]
        return probes

    def reserve(self, call, packet, stage, prices):
        amount = reservation(call, packet, prices)
        totals = self.totals()
        if totals["exposure"] + amount > self.limits["total"]:
            return None, "total_cap"
        if totals["uncertain"] + totals["inflight"] + amount > self.limits["uncertain"]:
            return None, "uncertain_cap"
        if stage == "preflight" and (totals["preflight"] + amount > self.limits["preflight"] or totals["preflight_attempts"] >= 16):
            return None, "preflight_cap"
        if self.db.execute("SELECT 1 FROM results WHERE cell_id=?", (call["cell_id"],)).fetchone():
            raise ValueError("Refusing to dispatch a completed cell")
        attempt = uuid.uuid4().hex
        request_sha = hashlib.sha256(canonical(request_kwargs(call, packet)).encode()).hexdigest()
        with self.db:
            self.db.execute("INSERT INTO attempts(attempt_id,cell_id,stage,judge,started_at,state,reserved,request_sha256)"
                            " VALUES (?,?,?,?,?,'inflight',?,?)",
                            (attempt, call["cell_id"], stage, call["judge"], utc(), amount, request_sha))
        return attempt, None

    def finish(self, attempt, call, outcome, prices):
        row = self.db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt,)).fetchone()
        if row is None or row["state"] != "inflight":
            raise ValueError("Attempt is absent or already resolved")
        raw = outcome.get("response")
        if raw is None:
            with self.db:
                self.db.execute("UPDATE attempts SET state='unknown',uncertain=reserved,finished_at=?,error=? WHERE attempt_id=?",
                                (utc(), outcome["error"], attempt))
                if outcome.get("fatal", False):
                    self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('fatal_reason',?)", (outcome["error"],))
            return {"completed": False, "fatal": outcome.get("fatal", False), "error": outcome["error"]}
        usage = raw.get("usage") or {}
        p, c = usage.get("prompt_tokens"), usage.get("completion_tokens")
        known = all(isinstance(t, int) and not isinstance(t, bool) and t >= 0 for t in (p, c))
        actual = token_cost(p, c, prices[call["judge"]]) if known else 0
        uncertain = 0 if known else row["reserved"]
        choices = raw.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        content = message.get("content")
        content = content if isinstance(content, str) else ""
        mismatch = raw.get("model") != call["judge"]
        exceeded = actual > row["reserved"]
        result = {k: call[k] for k in ("cell_id", "unit_id", "judge", "arm")}
        result.update({"raw_verdict_text": content, "completed_at": utc(), "attempt_id": attempt,
                       "usage": usage, "cost_usd": usd(actual), "uncertain_usd": usd(uncertain),
                       "returned_model_id": raw.get("model"), "response_id": raw.get("id"),
                       "finish_reason": choice.get("finish_reason"), "system_fingerprint": raw.get("system_fingerprint"),
                       "request_sha256": row["request_sha256"], "messages_sha256": call["messages_sha256"],
                       "configuration_valid": not mismatch, "duration_seconds": outcome["duration_seconds"]})
        with self.db:
            self.db.execute("UPDATE attempts SET state=?,actual=?,uncertain=?,finished_at=?,response=? WHERE attempt_id=?",
                            ("success" if known else "completed_unknown_usage", actual, uncertain, utc(), canonical(raw), attempt))
            self.db.execute("INSERT INTO results VALUES (?,?,?)", (call["cell_id"], row["stage"], canonical(result)))
            if mismatch or exceeded:
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('fatal_reason',?)",
                                ("returned_model_mismatch" if mismatch else "reservation_underestimated",))
        return {"completed": True, "fatal": mismatch or exceeded,
                "error": "returned_model_mismatch" if mismatch else ("reservation_underestimated" if exceeded else None),
                "strict_valid": parse_both(content)["strict"]["parse_ok"]}

    def rows(self, stage):
        return [json.loads(r[0]) for r in self.db.execute("SELECT result FROM results WHERE stage=? ORDER BY cell_id", (stage,))]

    def export(self, stage):
        path = self.directory / ("results.jsonl" if stage == "main" else "preflight_results.jsonl")
        atomic_bytes(path, "".join(canonical(r) + "\n" for r in self.rows(stage)).encode())

    def status(self, stage, expected, state, **extra):
        totals = self.totals()
        counts = Counter()
        rows = self.rows(stage)
        for row in rows:
            counts[row["judge"] + "|" + row["arm"]] += 1
        result = {"updated_at": utc(), "pid": os.getpid(), "stage": stage, "state": state,
                  "completed": sum(counts.values()), "expected": expected, "by_judge_arm": dict(counts),
                  "spend": {k + "_usd": usd(v) for k, v in totals.items() if k != "preflight_attempts"},
                  "preflight_attempts": totals["preflight_attempts"], "cap_usd": usd(self.limits["total"]), **extra}
        result["last_completion_at"] = max((r["completed_at"] for r in rows), default=None)
        atomic_json(self.directory / "status.json", result)
        return result


class Provider:
    def __init__(self):
        if not os.environ.get("TOGETHER_API_KEY", "").strip():
            raise RuntimeError("TOGETHER_API_KEY is missing")
        self.client = build_pinned_together_client(api_key=os.environ["TOGETHER_API_KEY"],
            base_url="https://api.together.xyz/v1", follow_redirects=False, sdk_internal_max_retries=0,
            http_timeout={"connect": 10, "read": 600, "write": 60, "pool": 60})

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
                reset = max(0, float(headers.get("x-ratelimit-reset", headers.get("retry-after", 0))))
            except (ValueError, TypeError):
                reset = 0
            # Only non-sensitive classification is written to status or stdout.
            return {"error": type(exc).__name__ + (" HTTP " + str(status) if status else ""),
                    "fatal": status in (400, 401, 403, 404, 422), "retry_after_seconds": reset,
                    "duration_seconds": time.monotonic() - start}


def synthetic_panel():
    calls, packets = [], {}
    for judge in JUDGES:
        for index, arm in enumerate(("empty", "qwen_history", "llama_history")):
            messages = [{"role": "system", "content": "Select the factually supported position. End with VERDICT: Position A or VERDICT: Position B, then CONFIDENCE: 3 and a short REASONING."},
                        {"role": "user", "content": "Synthetic world: The town of Amberford has a blue bridge. Position A: The bridge is blue. Position B: The bridge is red."}]
            if index:
                messages += [{"role": "assistant", "content": "CLAIM: Amberford has a blue bridge"},
                             {"role": "user", "content": "ORACLE: YES"}]
                if index == 2:
                    messages += [{"role": "assistant", "content": "CLAIM: A second identical claim"},
                                 {"role": "user", "content": "Query blocked. No oracle answer was supplied for this slot."}]
            messages += [{"role": "user", "content": "Now give your final verdict. Verification history: " + ("none" if not index else "Amberford has a blue bridge: YES") }]
            cell = "preflight:" + judge + ":" + arm
            digest = hashlib.sha256(canonical(messages).encode()).hexdigest()
            packets[cell] = {"messages": messages, "messages_sha256": digest}
            calls.append({"cell_id": cell, "unit_id": "synthetic-amberford", "judge": judge, "arm": arm,
                          "packet_id": cell, "messages_sha256": digest, "seed": 2026091205,
                          "temperature": 0.3, "max_tokens": 16384 if judge == QWEN else 512, "stream": False})
    return calls, packets


def load_panel(inputs, authorization):
    manifest = json.loads((inputs / "manifest.json").read_bytes())
    if sha(inputs / "manifest.json") != authorization["input_manifest_sha256"]:
        raise ValueError("Prepared input manifest changed")
    for name, expected in manifest["outputs"].items():
        if sha(inputs / name) != expected["sha256"]:
            raise ValueError("Prepared input changed: " + name)
    calls = [json.loads(line) for line in (inputs / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    packets = {r["packet_id"]: r for r in map(json.loads, (inputs / "packets.jsonl").read_text(encoding="utf-8").splitlines())}
    if len(calls) != 3936 or len({c["cell_id"] for c in calls}) != 3936 or len(packets) != 1968:
        raise ValueError("Incorrect prepared panel size")
    for c in calls:
        p = packets[c["packet_id"]]
        digest = hashlib.sha256(canonical(p["messages"]).encode()).hexdigest()
        if c["judge"] not in JUDGES or digest != c["messages_sha256"] or digest != p["messages_sha256"]:
            raise ValueError("Prepared request identity mismatch")
        if c["temperature"] != 0.3 or c["stream"] is not False or c["max_tokens"] != (16384 if c["judge"] == QWEN else 512):
            raise ValueError("Prepared decoding profile mismatch")
    return calls, packets


def execute(store, calls, packets, stage, prices, provider, *, max_workers=16,
            cooldown_seconds=60, cycle_seconds=900, poll_seconds=1, tune_seconds=120):
    """Only the coordinator writes SQLite; workers only perform bounded transport."""
    store.recover()
    if store.fatal_reason():
        store.export(stage)
        return store.status(stage, len(calls), "paused", halt_reason=store.fatal_reason())
    done = store.completed(stage)
    pending = {j: deque(c for c in calls if c["judge"] == j and c["cell_id"] not in done) for j in JUDGES}
    probes = store.interrupted_probes(stage)
    capacity = {j: min(4, max_workers) for j in JUDGES}
    active, count = {}, Counter()
    attempts_in_cycle, retry_after = Counter(), {}
    pause_until, failures = defaultdict(float), Counter()
    samples = {j: deque() for j in JUDGES}
    last_tune = {j: time.monotonic() for j in JUDGES}
    last_rate, previous_capacity = {}, {}
    halted, last_export = None, 0.0
    store.event(kind="start", stage=stage, completed=len(done), workers=capacity)
    with ThreadPoolExecutor(max_workers=2 * max_workers) as pool:
        while any(pending.values()) or active:
            now = time.monotonic()
            for future in list(active):
                if not future.done():
                    continue
                call, attempt = active.pop(future)
                judge, cell = call["judge"], call["cell_id"]
                count[judge] -= 1
                try:
                    outcome = future.result()
                except Exception as exc:
                    outcome = {"error": type(exc).__name__, "fatal": True}
                result = store.finish(attempt, call, outcome, prices)
                if result["completed"]:
                    done.add(cell)
                    samples[judge].append(now)
                    failures[judge] = 0
                    probes.pop(judge, None)
                else:
                    failures[judge] += 1
                    probes.setdefault(judge, cell)
                    delay = max(outcome.get("retry_after_seconds", 0), min(900, cooldown_seconds * (2 ** min(failures[judge] - 1, 4))))
                    pause_until[judge] = max(pause_until[judge], now + delay)
                    retry_after[cell] = now + (cycle_seconds if attempts_in_cycle[cell] >= 3 else delay)
                    pending[judge].appendleft(call)
                    store.event(kind="transport_failure", judge=judge, cell_id=cell, error=result["error"], retry_seconds=retry_after[cell] - now)
                if result["fatal"]:
                    halted = result["error"]
            if (store.directory / "pause.request").exists():
                halted = "operator_pause"
            if not halted:
                for judge in JUDGES:
                    if now < pause_until[judge]:
                        continue
                    # A blocked retry does not consume other cells' attempts during an outage.
                    remaining_scan = len(pending[judge])
                    while pending[judge] and count[judge] < capacity[judge] and remaining_scan:
                        remaining_scan -= 1
                        call = pending[judge].popleft()
                        cell = call["cell_id"]
                        if judge in probes and (cell != probes[judge] or count[judge] > 0):
                            pending[judge].append(call)
                            continue
                        if now < retry_after.get(cell, 0):
                            pending[judge].append(call)
                            continue
                        if attempts_in_cycle[cell] >= 3:
                            attempts_in_cycle[cell] = 0
                        attempt, cap = store.reserve(call, packets[call["packet_id"]], stage, prices)
                        if cap:
                            pending[judge].appendleft(call)
                            # Wait for in-flight reservations to settle before declaring a cap stop.
                            if not active:
                                halted = cap
                            break
                        attempts_in_cycle[cell] += 1
                        count[judge] += 1
                        active[pool.submit(provider, call, packets[call["packet_id"]])] = (call, attempt)
            if stage == "main" and not halted:
                for judge in JUDGES:
                    if now - last_tune[judge] < tune_seconds or not pending[judge]:
                        continue
                    while samples[judge] and samples[judge][0] < last_tune[judge]:
                        samples[judge].popleft()
                    rate = len(samples[judge]) / max(now - last_tune[judge], 1)
                    old_capacity = capacity[judge]
                    if failures[judge] or pause_until[judge] > now:
                        capacity[judge] = max(1, capacity[judge] // 2)
                    elif len(samples[judge]) >= 8:
                        if judge in last_rate and rate < last_rate[judge] * 0.95:
                            capacity[judge] = previous_capacity[judge]
                        elif capacity[judge] < max_workers:
                            previous_capacity[judge] = capacity[judge]
                            last_rate[judge] = rate
                            capacity[judge] = min(max_workers, capacity[judge] * 2)
                    store.event(kind="throughput", judge=judge, completed_per_hour=rate * 3600,
                                previous_workers=old_capacity, workers=capacity[judge])
                    last_tune[judge] = now
            if now - last_export >= 10 or (not active and (halted or not any(pending.values()))):
                store.export(stage)
                state = "stopping" if halted and active else ("paused" if halted else "running")
                store.status(stage, len(calls), state, halt_reason=halted, workers=capacity,
                             active=dict(count), recovery_probes=probes,
                             cooldown_seconds={j: max(0, round(max(pause_until[j], retry_after.get(probes.get(j), 0)) - now)) for j in JUDGES})
                last_export = now
            if halted and not active:
                break
            time.sleep(poll_seconds)
    store.export(stage)
    complete = len(store.completed(stage)) == len(calls)
    final_state = "requests_complete" if complete and not halted else "paused"
    status = store.status(stage, len(calls), final_state, halt_reason=halted, workers=capacity, active=dict(count))
    store.event(kind="stop", stage=stage, state=final_state, halt_reason=halted)
    return status


def verify_authorization(path, inputs, run_dir):
    auth = json.loads(path.read_bytes())
    if auth.get("paid_execution_authorized") is not True or auth.get("stage") != "4A":
        raise ValueError("Phase 4A execution authorization is absent")
    if auth["approved_cap_usd"] != 250 or auth["preflight_cap_usd"] != 5 or auth["uncertain_cap_usd"] != 25:
        raise ValueError("Execution limits differ from the approved proposal")
    if Path(auth["inputs"]).resolve() != inputs or Path(auth["run_directory"]).resolve() != run_dir:
        raise ValueError("Authorized input/output paths do not match")
    if run_dir.is_relative_to(ROOT) or run_dir == inputs or inputs.is_relative_to(run_dir):
        raise ValueError("Live outputs must be separate from Git and prepared inputs")
    if sha(ROOT / "rejudge/phase4_protocol_v1.json") != auth["protocol_sha256"]:
        raise ValueError("Scientific protocol changed")
    return auth


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--mode", choices=("preflight", "run", "status"), required=True)
    args = parser.parse_args()
    inputs, run_dir = args.inputs.resolve(), args.run_dir.resolve()
    auth = verify_authorization(args.authorization.resolve(), inputs, run_dir)
    main_calls, main_packets = load_panel(inputs, auth)
    identity = {"authorization_sha256": sha(args.authorization), "input_manifest_sha256": sha(inputs / "manifest.json"),
                "protocol_sha256": auth["protocol_sha256"], "run_id": auth["run_id"]}
    limits = {"total": money(250), "uncertain": money(25), "preflight": money(5)}
    if args.mode == "status":
        status_path = run_dir / "status.json"
        print(status_path.read_text(encoding="utf-8") if status_path.exists() else '{"state":"not_started"}')
        return 0
    with run_lock(run_dir):
        store = Store(run_dir, identity, limits)
        try:
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
            source_files = ["rejudge/phase4_runner.py", "scripts/phase4_analysis.py", "rejudge/api_client.py",
                            "rejudge/parsers.py", "analysis/infra/parsing.py", "rejudge/durable_fs.py"]
            subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *source_files], cwd=ROOT, check=True, capture_output=True)
            runtime = {"git_commit": commit, "python": sys.version,
                       "versions": {p: importlib.metadata.version(p) for p in ("together", "httpx")},
                       "runner_sha256": sha(Path(__file__)), "analysis_sha256": sha(ROOT / "scripts/phase4_analysis.py"),
                       "source_sha256s": {name: sha(ROOT / name) for name in source_files},
                       "gpu_ordinal": None, "linker": "not applicable; Python/API study"}
            manifest_path = run_dir / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_bytes())
                pinned = {k: v for k, v in manifest["runtime"].items() if k != "git_commit"}
                current = {k: v for k, v in runtime.items() if k != "git_commit"}
                if pinned != current or manifest["identity"] != identity:
                    raise ValueError("Resume source/runtime differs from the run manifest")
                if manifest["runtime"]["git_commit"] != commit:
                    store.event(kind="resume_commit", observed_git_commit=commit, pinned_source_files_unchanged=True)
            else:
                atomic_json(manifest_path, {"created_at": utc(), "identity": identity, "runtime": runtime,
                                          "authorization": auth, "request_count": 3936,
                                          "input_hashes": json.loads((inputs / "manifest.json").read_bytes())["outputs"],
                                          "seeds": {"order": 2026091201, "bootstrap": 2026091202},
                                          "provider_settings": {"sdk_retries": 0, "timeout_seconds": {"connect": 10, "read": 600, "write": 60, "pool": 60},
                                                                "proactive_deadline_seconds": 1200, "extra_reasoning_fields": {}, "stream": False}})
            calls, packets = synthetic_panel() if args.mode == "preflight" else (main_calls, main_packets)
            if args.mode == "run":
                passed = run_dir / "preflight.json"
                if not passed.exists() or json.loads(passed.read_bytes()).get("status") != "passed":
                    raise ValueError("Synthetic endpoint preflight has not passed")
            provider = Provider()
            status = execute(store, calls, packets, "preflight" if args.mode == "preflight" else "main", auth["prices_per_million"], provider)
            if args.mode == "preflight":
                rows = store.rows("preflight")
                passed = (status["state"] == "requests_complete" and not store.fatal_reason() and len(rows) == 6
                          and all(r["configuration_valid"] and parse_both(r["raw_verdict_text"])["strict"]["parse_ok"]
                                  and r["uncertain_usd"] == 0 for r in rows))
                atomic_json(run_dir / "preflight.json", {"status": "passed" if passed else "not_passed", "completed_at": utc(),
                                                        "completed": len(rows), "spend": status["spend"], "synthetic_only": True})
                print(canonical({"mode": "preflight", "status": "passed" if passed else "not_passed", "spend": status["spend"]}))
                return 0 if passed else 2
            if status["state"] == "requests_complete":
                store.status("main", len(calls), "analyzing")
                with (run_dir / "analysis.log").open("ab") as log:
                    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/phase4_analysis.py"),
                                             "--inputs", str(inputs), "--results", str(run_dir / "results.jsonl"),
                                             "--out", str(run_dir / "analysis")], cwd=ROOT, stdout=log, stderr=log)
                if result.returncode:
                    store.status("main", len(calls), "analysis_failed")
                    return 3
                outputs = {str(p.relative_to(run_dir)): sha(p) for p in (run_dir / "analysis").glob("*") if p.is_file()}
                outputs["results.jsonl"] = sha(run_dir / "results.jsonl")
                atomic_json(run_dir / "completion.json", {"status": "complete", "completed_at": utc(), "run_id": auth["run_id"],
                                                          "completed": len(calls), "spend": status["spend"], "output_sha256s": outputs})
                store.status("main", len(calls), "complete")
                return 0
            return 2
        except BaseException as exc:
            store.event(kind="runner_exception", error_type=type(exc).__name__)
            store.status("preflight" if args.mode == "preflight" else "main", 6 if args.mode == "preflight" else 3936,
                         "runner_exception", error_type=type(exc).__name__)
            raise
        finally:
            store.db.close()


if __name__ == "__main__":
    raise SystemExit(main())
