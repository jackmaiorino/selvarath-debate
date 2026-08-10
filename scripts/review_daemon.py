"""Watch for reviewer worklists, review them, commit the rulings. Repeat.

The canary surfaces payloads in waves of one to a few, so hand-running each wave is slow and
is exactly the manual handling that produced the 2026-07-29 prompt contamination. This closes
the loop: it packages the worklist's undecided payloads straight from their frozen bytes,
dispatches them through the codex reviewer batch, verifies every ruling, and commits.

Safety properties it does not relax:
- packets are written from ``subagent_prompt`` and re-hashed against ``subagent_prompt_sha256``;
  a mismatch aborts rather than dispatching unverified evidence;
- a ruling is only committed with its dispatched-prompt hash, so the commit path's own check
  can still refuse it;
- rulings whose reviewer used a tool, or failed, are committed as reviewer_error (non-ALLOW)
  by the batch runner, never silently dropped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path


def undecided(archive: Path, *,
              decisions_filename: str = "canary_reviewer_decisions.jsonl") -> list[dict]:
    """Worklist payloads with no committed ruling yet.

    The decisions filename comes from the run's manifest rather than a constant. It was
    hardcoded to the canary's, and the main run's manifest names a different one, so the
    daemon found no decisions at all, treated every payload as undecided, and would have
    re-reviewed the whole worklist on every wave.
    """
    wl = archive / "reviewer_worklist.json"
    if not wl.exists():
        return []
    items = json.loads(wl.read_text(encoding="utf-8"))["items"]
    decided = set()
    dp = archive / decisions_filename
    if dp.exists():
        for line in dp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                decided.add(json.loads(line)["payload_sha256"])
    return [i for i in items if i["payload_sha256"] not in decided]


def review_and_commit(todo: list[dict], archive: Path, args) -> str:
    stamp = hashlib.sha256("".join(i["payload_sha256"] for i in todo).encode()).hexdigest()[:10]
    # The stamp alone is a hash of the payload set, so re-reviewing the same set lands on the
    # same directory name. That let a stale rulings.jsonl from an aborted attempt masquerade as
    # this attempt's output and get committed verbatim (2026-08-07, 329 rows in 47 seconds). The
    # nonce gives every invocation its own directory, so an old attempt's files are never even
    # visible to a new one; mkdir with no exist_ok also refuses outright if that guarantee is
    # ever violated instead of silently writing into whatever is already there.
    nonce = uuid.uuid4().hex[:8]
    pk = archive / f"review_packets_auto_{stamp}_{nonce}"
    pk.mkdir()
    index = []
    for n, it in enumerate(todo, 1):
        prompt = it["subagent_prompt"]
        h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if h != it["subagent_prompt_sha256"]:
            return f"ABORT: prompt hash drift on {it['payload_sha256']}"
        f = pk / f"{n:03d}_{it['payload_sha256'][:12]}.txt"
        f.write_text(prompt, encoding="utf-8", newline="")
        index.append({"n": n, "file": f.name, "payload_sha256": it["payload_sha256"],
                      "prompt_sha256": h})
    (pk / "INDEX.json").write_text(
        json.dumps({"count": len(index), "items": index}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="")

    here = Path(__file__).resolve().parent
    rc = subprocess.run([sys.executable, str(here / "codex_reviewer_batch.py"),
                         "--packets", str(pk), "--out", str(pk / "rulings.jsonl"),
                         "--codex", args.codex, "--concurrency", str(args.concurrency)],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        return f"ABORT: reviewer batch exit {rc.returncode}: {rc.stderr[-300:]}"

    # Every dispatched payload_sha256, and nothing else: a row for anything else means
    # rulings.jsonl holds output this dispatch never produced, and it must not be committed.
    expected = {i["payload_sha256"]: i["prompt_sha256"] for i in index}
    commit, clean, errs = [], 0, 0
    for line in (pk / "rulings.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["payload_sha256"] not in expected:
            return (f"ABORT: rulings.jsonl has a ruling for {r['payload_sha256']}, which this "
                     f"dispatch never sent out; refusing to commit output from another attempt")
        if "tool_uses" in r:
            if r["prompt_sha256"] != expected[r["payload_sha256"]]:
                return f"ABORT: prompt proof mismatch on {r['payload_sha256']}"
            commit.append({"payload_sha256": r["payload_sha256"],
                           "raw_output": r["raw_output"],
                           "prompt_sha256": r["prompt_sha256"]})
            clean += 1
        else:
            commit.append({"payload_sha256": r["payload_sha256"], "status": "reviewer_error",
                           "raw_output": r["raw_output"]})
            errs += 1
    cf = pk / "commit.json"
    cf.write_text(json.dumps(commit, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    rc = subprocess.run([sys.executable, "-m", "rejudge.phase2_canary_live",
                         "--manifest", args.manifest, "--authorization", args.authorization,
                         "--project-root", ".", "--commit-decisions", str(cf)],
                        capture_output=True, text=True)
    if rc.returncode != 0:
        return f"ABORT: commit exit {rc.returncode}: {rc.stderr[-300:]}"
    return f"committed {len(commit)} (clean={clean} reviewer_error={errs})"


def decisions_filename_for(manifest_path: str) -> str:
    """Read it off the manifest, so the daemon and the driver cannot disagree."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return Path(str(manifest["ledger"]["decisions_path"])).name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="review_daemon")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--authorization", required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--codex", default="codex")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--poll-seconds", type=int, default=60)
    ap.add_argument("--max-waves", type=int, default=200)
    args = ap.parse_args(argv)
    decisions_name = decisions_filename_for(args.manifest)
    archive = Path(args.archive)

    waves = 0
    while waves < args.max_waves:
        todo = undecided(archive, decisions_filename=decisions_name)
        if todo:
            waves += 1
            print(f"[wave {waves}] {len(todo)} undecided payload(s)", flush=True)
            msg = review_and_commit(todo, archive, args)
            print(f"[wave {waves}] {msg}", flush=True)
            if msg.startswith("ABORT"):
                return 4
        time.sleep(args.poll_seconds)
    print(f"max-waves {args.max_waves} reached", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
