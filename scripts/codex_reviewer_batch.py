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
from pathlib import Path

MODEL_DEFAULT = "gpt-5.6-sol"
EFFORT_DEFAULT = "high"
PER_REVIEW_TIMEOUT_SECONDS = 600


def run_one(packet: Path, model: str, effort: str, codex: str) -> dict:
    """Review one packet. Never raises: a failure is reported, not silently dropped."""
    prompt_bytes = packet.read_bytes()
    prompt_sha = hashlib.sha256(prompt_bytes).hexdigest()
    workdir = Path(tempfile.mkdtemp(prefix="reviewer-iso-"))
    out_file = workdir / "ruling.txt"
    try:
        proc = subprocess.run(
            [codex, "exec", "-m", model, "-c", f'model_reasoning_effort="{effort}"',
             "-C", str(workdir), "--skip-git-repo-check", "--ephemeral",
             "--ignore-user-config", "--ignore-rules", "-s", "read-only", "--json",
             "-o", str(out_file), "-"],
            input=prompt_bytes, capture_output=True,
            timeout=PER_REVIEW_TIMEOUT_SECONDS)
        commands = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "command_execution":
                commands.append(item.get("command", "")[:200])
        ruling = out_file.read_text(encoding="utf-8").strip() if out_file.exists() else ""
        if proc.returncode != 0 or not ruling:
            return {"packet": packet.name, "prompt_sha256": prompt_sha, "ok": False,
                    "error": f"exit={proc.returncode}, ruling_empty={not ruling}",
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
    args = ap.parse_args(argv)

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
        futures = {pool.submit(run_one, packets_dir / i["file"], args.model, args.effort,
                               args.codex): i for i in todo}
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
