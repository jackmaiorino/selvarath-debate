"""A regenerated payload set must not replay a previous attempt's stale rulings.

On 2026-08-07, contaminated rulings were dropped from the decision store for re-review. The
next wave regenerated the identical payload set, which hashed to the identical packet
directory (review_packets_auto_<hash>, created with mkdir(exist_ok=True)), found the STALE
rulings.jsonl left over from the previous failed attempt (329 rows, all reviewer_error),
dispatched nothing new, and committed the stale rows back verbatim in 47 seconds. Re-review of
a previously attempted payload set was structurally impossible: the old attempt's output
masqueraded as a fresh one.

The fix gives every invocation its own packet directory (payload-set hash plus a per-invocation
nonce), so an old attempt's files are never visible to a new one, and it refuses to commit any
row whose payload_sha256 the current dispatch did not itself send out.

These tests run the real review_and_commit / codex_reviewer_batch.main() flow, stubbing only
the innermost call to the external reviewer (run_one) and the final commit-to-ledger
subprocess, which belongs to the frozen rejudge bundle and is out of scope here.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from scripts import codex_reviewer_batch, review_daemon


def _todo(hashes):
    items = []
    for i, h in enumerate(hashes):
        prompt = f"payload {i} prompt text"
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        items.append({"payload_sha256": h, "subagent_prompt": prompt,
                      "subagent_prompt_sha256": prompt_sha})
    return items


def _stale_stamp(todo):
    return hashlib.sha256("".join(i["payload_sha256"] for i in todo).encode()).hexdigest()[:10]


def _seed_stale_directory(archive, todo):
    """What a prior, aborted attempt at this exact payload set left behind: a packet
    directory named for the payload-set hash alone, holding a rulings.jsonl of dead rulings."""
    stamp = _stale_stamp(todo)
    stale = archive / f"review_packets_auto_{stamp}"
    stale.mkdir(parents=True)
    stale_rows = [
        {"payload_sha256": it["payload_sha256"], "status": "reviewer_error",
         "raw_output": "REVIEWER_UNAVAILABLE: exit=1, ruling_empty=True (stale, prior wave)"}
        for it in todo
    ]
    (stale / "rulings.jsonl").write_text(
        "\n".join(json.dumps(r) for r in stale_rows) + "\n", encoding="utf-8")
    return stale


def _args():
    return SimpleNamespace(codex="fake-codex", concurrency=2, manifest="unused-manifest.json",
                           authorization="unused-auth")


def test_a_regenerated_payload_set_is_actually_redispatched_not_replayed(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    todo = _todo(["a" * 64, "b" * 64])
    stale_dir = _seed_stale_directory(archive, todo)
    stale_rulings_before = (stale_dir / "rulings.jsonl").read_text(encoding="utf-8")

    dispatched = []

    def fake_run_one(packet, model, effort, codex):
        """Stands in for the external reviewer: proves it was actually called."""
        dispatched.append(packet.name)
        prompt_sha = hashlib.sha256(packet.read_bytes()).hexdigest()
        return {"packet": packet.name, "prompt_sha256": prompt_sha, "ok": True,
                "raw_output": "LABEL: ALLOW\nCLAUSE: none\nRATIONALE: stub reviewer",
                "commands": []}

    monkeypatch.setattr(codex_reviewer_batch, "run_one", fake_run_one)

    commit_calls = []

    def fake_subprocess_run(cmd, **kwargs):
        joined = [str(c) for c in cmd]
        if "codex_reviewer_batch.py" in joined[1]:
            # Run the real batch script in-process instead of spawning a second interpreter,
            # so the run_one stub above is the only thing standing in for the reviewer.
            rc = codex_reviewer_batch.main(joined[2:])
            return SimpleNamespace(returncode=rc, stdout="", stderr="")
        if "rejudge.phase2_canary_live" in joined:
            # The commit step belongs to the frozen rejudge bundle; record the call instead
            # of exercising it.
            commit_calls.append(joined)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess.run call: {joined}")

    monkeypatch.setattr(review_daemon.subprocess, "run", fake_subprocess_run)

    result = review_daemon.review_and_commit(todo, archive, _args())

    assert result.startswith("committed"), result
    assert len(commit_calls) == 1

    # (b) the payloads were genuinely re-dispatched to the (stubbed) reviewer, not skipped
    # because some directory's rulings.jsonl already claimed them done.
    assert len(dispatched) == len(todo)

    # (a) no stale row survived into what got committed: a fresh packet directory was used,
    # distinct from the stale one, and its commit.json holds only the new ALLOW rulings.
    new_dirs = [d for d in archive.glob("review_packets_auto_*") if d != stale_dir]
    assert len(new_dirs) == 1, "expected exactly one fresh packet directory for this wave"
    new_dir = new_dirs[0]
    assert new_dir.name != stale_dir.name

    commit_json = json.loads((new_dir / "commit.json").read_text(encoding="utf-8"))
    assert len(commit_json) == len(todo)
    for row in commit_json:
        assert row.get("status") != "reviewer_error"
        assert "REVIEWER_UNAVAILABLE" not in row.get("raw_output", "")

    # The stale directory itself must never have been touched.
    assert (stale_dir / "rulings.jsonl").read_text(encoding="utf-8") == stale_rulings_before


def test_a_foreign_ruling_is_refused_rather_than_committed(tmp_path, monkeypatch):
    """Belt and suspenders: even inside a dispatch's own fresh directory, a rulings.jsonl row
    for a payload this dispatch never sent out must abort the wave rather than be committed."""
    archive = tmp_path / "archive"
    archive.mkdir()
    todo = _todo(["a" * 64])

    def fake_subprocess_run(cmd, **kwargs):
        joined = [str(c) for c in cmd]
        if "codex_reviewer_batch.py" in joined[1]:
            out_path = Path(joined[joined.index("--out") + 1])
            valid_prompt_sha = hashlib.sha256(
                todo[0]["subagent_prompt"].encode("utf-8")).hexdigest()
            rows = [
                {"payload_sha256": todo[0]["payload_sha256"], "tool_uses": 0,
                 "raw_output": "LABEL: ALLOW\nCLAUSE: none\nRATIONALE: stub",
                 "prompt_sha256": valid_prompt_sha},
                {"payload_sha256": "f" * 64, "status": "reviewer_error",
                 "raw_output": "foreign row from another attempt"},
            ]
            out_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                                encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess.run call: {joined}")

    monkeypatch.setattr(review_daemon.subprocess, "run", fake_subprocess_run)

    result = review_daemon.review_and_commit(todo, archive, _args())

    assert result.startswith("ABORT"), result
    assert "f" * 64 in result

    new_dirs = list(archive.glob("review_packets_auto_*"))
    assert len(new_dirs) == 1
    assert not (new_dirs[0] / "commit.json").exists()


def test_exit_when_empty_ends_instead_of_polling(tmp_path):
    """A fully-decided worklist must end a one-shot invocation, not poll forever.

    Observed 2026-08-10: the orchestrator's sequential wave/run-pass loop deadlocked for 103
    minutes because max-waves counts waves EXECUTED, so zero undecided payloads left the
    daemon polling for items only the blocked run pass could have exported.
    """
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(
        {"ledger": {"decisions_path": "E:/anywhere/decisions.jsonl"}}), encoding="utf-8")
    archive = tmp_path / "archive"
    archive.mkdir()
    rc = review_daemon.main([
        "--manifest", str(manifest), "--authorization", "unused.json",
        "--archive", str(archive), "--poll-seconds", "1", "--max-waves", "1",
        "--exit-when-empty"])
    assert rc == 0
