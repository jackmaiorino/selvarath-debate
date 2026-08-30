"""Run blinded reviews through the codex CLI, proving prompt bytes and zero tool use.

The reviewer model was substituted mid-canary under an owner deviation because the pinned
claude-fable-5 quota is unavailable until 2026-08-04. This runner preserves both controls the
subagent transport gave us, and adds one the external-chat transport could never have had:

- **prompt integrity**: the packet file is piped to ``codex exec`` as stdin, so the bytes the
  model receives are exactly the bytes on disk, and their sha256 is recorded per ruling and
  checked against the frozen ``subagent_prompt_sha256`` at commit time;
- **no tool use**: codex's ``--json`` event stream reports every shell call as a
  ``command_execution`` item. Any ruling whose stream contains one is refused rather than
  recorded, because the sandbox permits filesystem reads and a reviewer that consulted the
  world document would no longer be blind.

Isolation per review: a fresh ephemeral session in an empty working root, with user config
and project rules disabled, so nothing about this experiment is in context but the payload.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MODEL_DEFAULT = "gpt-5.6-sol"
EFFORT_DEFAULT = "high"
PER_REVIEW_TIMEOUT_SECONDS = 600
_LIFECYCLE_EVENT_TYPES = frozenset({
    "thread.started",
    "turn.started",
    "turn.completed",
})
_FAILURE_EVENT_TYPES = frozenset({"error", "turn.failed"})
_ITEM_EVENT_TYPES = frozenset({"item.started", "item.updated", "item.completed"})
_NON_TOOL_ITEM_TYPES = frozenset({"agent_message", "reasoning", "user_message"})
_TOOL_ITEM_TYPES = frozenset({
    "collab_tool_call",
    "command_execution",
    "dynamic_tool_call",
    "entered_review_mode",
    "exited_review_mode",
    "file_change",
    "image_view",
    "mcp_tool_call",
    "plan",
    "todo_list",
    "web_search",
})


def _active_deadline(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("reviewer authorization deadline must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("reviewer authorization deadline must use UTC")
    return parsed.astimezone(timezone.utc)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _inspect_json_event_stream(raw: bytes) -> tuple[list[str], list[str]]:
    """Return detected tool uses and structural errors from one Codex JSONL stream."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [], ["stdout is not valid UTF-8"]
    commands: list[str] = []
    errors: list[str] = []
    lifecycle_counts = {event_type: 0 for event_type in _LIFECYCLE_EVENT_TYPES}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            errors.append(f"line {line_number} is blank")
            continue
        try:
            event = json.loads(line, object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"line {line_number} is not unique-key JSON: {exc}")
            continue
        if not isinstance(event, dict):
            errors.append(f"line {line_number} is not a JSON object")
            continue
        event_type = event.get("type")
        if not isinstance(event_type, str):
            errors.append(f"line {line_number} has no string event type")
            continue
        if event_type in _FAILURE_EVENT_TYPES:
            errors.append(f"line {line_number} reports {event_type}")
            continue
        if event_type in _LIFECYCLE_EVENT_TYPES:
            lifecycle_counts[event_type] += 1
            continue
        if event_type not in _ITEM_EVENT_TYPES:
            errors.append(f"line {line_number} has unknown event type {event_type!r}")
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            errors.append(f"line {line_number} {event_type} has no item object")
            continue
        item_type = item.get("type")
        if not isinstance(item_type, str):
            errors.append(f"line {line_number} {event_type} has no string item type")
            continue
        if item_type in _NON_TOOL_ITEM_TYPES:
            continue
        if item_type not in _TOOL_ITEM_TYPES:
            errors.append(f"line {line_number} has unknown item type {item_type!r}")
            continue
        command = item.get("command")
        detail = (
            str(command)[:200]
            if item_type == "command_execution" and command is not None
            else item_type
        )
        if detail not in commands:
            commands.append(detail)
    for event_type, count in lifecycle_counts.items():
        if count != 1:
            errors.append(f"event stream has {count} {event_type!r} events instead of one")
    return commands, errors


def run_one(
    packet: Path, model: str, effort: str, codex: str,
    not_after_utc: datetime | None = None,
) -> dict:
    """Review one packet. Never raises: a failure is reported, not silently dropped."""
    prompt_bytes = packet.read_bytes()
    prompt_sha = hashlib.sha256(prompt_bytes).hexdigest()
    workdir = Path(tempfile.mkdtemp(prefix="reviewer-iso-"))
    out_file = workdir / "ruling.txt"
    try:
        if not_after_utc is not None and datetime.now(timezone.utc) > not_after_utc:
            return {
                "packet": packet.name,
                "prompt_sha256": prompt_sha,
                "ok": False,
                "error": "authorization deadline expired before reviewer dispatch",
                "commands": [],
            }
        proc = subprocess.run(
            [codex, "exec", "-m", model, "-c", f'model_reasoning_effort="{effort}"',
             "-C", str(workdir), "--skip-git-repo-check", "--ephemeral",
             "--ignore-user-config", "--ignore-rules", "-s", "read-only", "--json",
             "-o", str(out_file), "-"],
            input=prompt_bytes, capture_output=True,
            timeout=PER_REVIEW_TIMEOUT_SECONDS)
        commands, stream_errors = _inspect_json_event_stream(proc.stdout)
        ruling = out_file.read_text(encoding="utf-8").strip() if out_file.exists() else ""
        if proc.returncode != 0 or not ruling or stream_errors:
            event_error = stream_errors[0] if stream_errors else None
            return {"packet": packet.name, "prompt_sha256": prompt_sha, "ok": False,
                    "error": f"exit={proc.returncode}, ruling_empty={not ruling}, "
                             f"event_stream_error={event_error!r}",
                    "commands": commands}
        return {"packet": packet.name, "prompt_sha256": prompt_sha, "ok": True,
                "raw_output": ruling, "commands": commands}
    except subprocess.TimeoutExpired:
        return {"packet": packet.name, "prompt_sha256": prompt_sha, "ok": False,
                "error": f"timeout after {PER_REVIEW_TIMEOUT_SECONDS}s", "commands": []}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)



class ReviewerUnavailable(RuntimeError):
    """The reviewer was never reached. Abort the wave; do not rule on its queue.

    The distinction this draws is the one that cost 329 payloads on 2026-08-07. A reviewer
    that RULED and produced something unusable (unparseable output, or tool use that breaks
    blindness) has told us something about THAT PAYLOAD, and the frozen failure rule rightly
    commits it as non-ALLOW. A reviewer that was never reached has told us something about the
    REVIEWER, and committing it writes a permanent verdict, in an append-only store, from
    nothing at all.

    Failing closed means refusing to proceed. It does not mean manufacturing a refusal for
    every payload in the queue.
    """


def classify_result(meta: dict, result: dict, *, packet_ok: bool) -> dict:
    """One dispatched packet's outcome as a decision row, or raise if the reviewer was down."""
    if not packet_ok:
        return {"payload_sha256": meta["payload_sha256"], "status": "reviewer_error",
                "raw_output": "PACKET_DRIFT: packet bytes no longer match the frozen "
                              "prompt hash; not dispatched as evidence"}
    if not result.get("ok"):
        raise ReviewerUnavailable(str(result.get("error")))
    if result.get("commands"):
        # Blindness cannot be assumed after the fact; refuse the ruling outright.
        return {"payload_sha256": meta["payload_sha256"], "status": "reviewer_error",
                "raw_output": "TOOL_USE_DETECTED: reviewer issued "
                              f"{len(result['commands'])} command(s); ruling discarded "
                              f"as non-blind. first={result['commands'][0]!r}"}
    return {"payload_sha256": meta["payload_sha256"],
            "raw_output": result.get("raw_output"),
            "prompt_sha256": result.get("prompt_sha256"), "tool_uses": 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="codex_reviewer_batch")
    ap.add_argument("--packets", required=True, help="directory holding INDEX.json + packets")
    ap.add_argument("--out", required=True, help="JSONL to append results to")
    ap.add_argument("--model", default=MODEL_DEFAULT)
    ap.add_argument("--effort", default=EFFORT_DEFAULT)
    ap.add_argument("--codex", default="codex")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument(
        "--not-after-utc",
        help="signed authorization deadline checked immediately before every reviewer call",
    )
    args = ap.parse_args(argv)
    try:
        not_after_utc = (
            _active_deadline(args.not_after_utc)
            if args.not_after_utc is not None else None)
    except ValueError as exc:
        ap.error(str(exc))

    packets_dir = Path(args.packets)
    index = json.loads((packets_dir / "INDEX.json").read_text(encoding="utf-8"))
    out_path = Path(args.out)
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["payload_sha256"])

    todo = [i for i in index["items"] if i["payload_sha256"] not in done]
    if args.limit is not None:
        todo = todo[:args.limit]
    print(f"{len(done)} already done; dispatching {len(todo)} "
          f"(model={args.model}, effort={args.effort}, concurrency={args.concurrency})",
          flush=True)

    written = clean = refused = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        if not_after_utc is None:
            futures = {
                pool.submit(
                    run_one, packets_dir / item["file"], args.model, args.effort,
                    args.codex,
                ): item
                for item in todo
            }
        else:
            futures = {
                pool.submit(
                    run_one, packets_dir / item["file"], args.model, args.effort,
                    args.codex, not_after_utc,
                ): item
                for item in todo
            }
        for fut in concurrent.futures.as_completed(futures):
            meta = futures[fut]
            result = fut.result()
            # The packet's bytes must still hash to what the frozen index recorded.
            try:
                row = classify_result(
                    meta, result,
                    packet_ok=result["prompt_sha256"] == meta["prompt_sha256"])
            except ReviewerUnavailable as down:
                print(f"ABORT: reviewer unreachable ({down}); {written} ruling(s) written, "
                      f"the rest of this wave is NOT ruled on. Nothing is committed from a "
                      f"reviewer that was never reached.", flush=True)
                for pending in futures:
                    pending.cancel()
                return 3
            if "tool_uses" in row:
                clean += 1
            elif "TOOL_USE_DETECTED" in row["raw_output"]:
                refused += 1
            else:
                failed += 1
            with out_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
            print(f"  [{written}/{len(todo)}] {meta['file']} -> "
                  f"{'clean' if 'tool_uses' in row else row['status']}", flush=True)

    print(f"done: {written} written | clean={clean} refused_tool_use={refused} failed={failed}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
