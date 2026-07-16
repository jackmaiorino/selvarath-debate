import json
from pathlib import Path

import pytest

from rejudge import runner


TRANSCRIPTS_PATH = Path("data/transcripts.jsonl")
requires_transcripts = pytest.mark.skipif(
    not TRANSCRIPTS_PATH.is_file(),
    reason="requires local research corpus data/transcripts.jsonl (not included in clean clones)",
)


def test_iter_cells_counts_and_legacy_k1():
    trs = [{"question_id": f"Q{i}", "transcript_index": i, "world": "w0"} for i in range(4)]
    cells = runner.iter_cells(["clean", "legacy"], {"clean": [0, 1], "legacy": [1]},
                              trs, replicates=2)
    clean = [c for c in cells if c["arm"] == "clean"]
    legacy = [c for c in cells if c["arm"] == "legacy"]
    assert len(clean) == 4 * 2 * 2            # transcripts x budgets x K
    assert len(legacy) == 4 * 1 * 1           # legacy is K=1
    assert len({c["cell_key"] for c in cells}) == len(cells)


@requires_transcripts
def test_dry_run_e2e_and_resume(tmp_path):
    out = tmp_path / "records.jsonl"
    rc = runner.main(["--arms", "clean,both,placebo", "--replicates", "1",
                      "--limit", "2", "--dry-run", "--workers", "1",
                      "--out", str(out)])
    assert rc == 0
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    # 2 transcripts x (clean 4 budgets + both 3 + placebo 3) x K=1
    assert len(rows) == 2 * (4 + 3 + 3)
    assert all(r["dry_run"] is True for r in rows)
    assert all(r["harness_version"] for r in rows)
    arms = {r["arm"] for r in rows}
    assert arms == {"clean", "both", "placebo"}
    # resume: second run adds nothing
    rc2 = runner.main(["--arms", "clean,both,placebo", "--replicates", "1",
                       "--limit", "2", "--dry-run", "--workers", "1",
                       "--out", str(out)])
    assert rc2 == 0
    rows2 = out.read_text(encoding="utf-8").splitlines()
    assert len(rows2) == len(rows)


def test_live_requires_cap(tmp_path):
    rc = runner.main(["--arms", "clean", "--limit", "1",
                      "--out", str(tmp_path / "r.jsonl")])
    assert rc == 2                              # refused: no --approved-cap and not --dry-run


@requires_transcripts
def test_transient_cell_error_skips_continues_and_returns_incomplete(
        tmp_path, monkeypatch, capsys):
    # --arms clean --limit 1 --replicates 1 -> 1 transcript x [0, 1, 2, 5] budgets x K=1
    # = 4 cells. iter_cells nests budgets inside the (single) transcript, replicate
    # innermost, so with --workers 1 (a single worker thread draining `todo` in list
    # order) the 4 judge_loop.run_judgment calls happen in that same deterministic
    # order: the first call is budget=0, which `flaky` makes fail; the remaining 3
    # (budgets 1, 2, 5) succeed and get written.
    calls = {"n": 0}

    def flaky(tr, wd, arm, budget, replicate, client, protocol, judge_model=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {"cell_key": f"{arm.name}|{tr['question_id']}|{tr['transcript_index']}|"
                            f"{budget}|{replicate}", "dry_run": True}
    monkeypatch.setattr(runner.judge_loop, "run_judgment", flaky)
    out = tmp_path / "r.jsonl"
    rc = runner.main(["--arms", "clean", "--replicates", "1", "--limit", "1",
                      "--dry-run", "--workers", "1", "--out", str(out)])
    assert rc == 1
    assert "completion check: 1 missing" in capsys.readouterr().out
    rows = out.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3            # clean has 4 budgets; 1 failed, 3 written
    failed = (tmp_path / "failed_cells.jsonl").read_text(encoding="utf-8")
    assert "boom" in failed


@requires_transcripts
def test_cap_exceeded_halts_run(tmp_path, monkeypatch):
    from rejudge.api_client import CapExceededError

    def capped(*a, **k):
        raise CapExceededError("cap")
    monkeypatch.setattr(runner.judge_loop, "run_judgment", capped)
    rc = runner.main(["--arms", "clean", "--replicates", "1", "--limit", "2",
                      "--dry-run", "--workers", "2", "--out", str(tmp_path / "r.jsonl")])
    assert rc == 3


def test_stratified_subset_is_world_balanced_and_deterministic():
    # Synthetic 3-world list, 4 transcripts per world (file-order grouped, like the real
    # data/transcripts.jsonl), so a naive transcripts[:6] prefix would be 100% world "a".
    trs = ([{"question_id": f"a{i}", "transcript_index": i, "world": "a"} for i in range(4)]
          + [{"question_id": f"b{i}", "transcript_index": i, "world": "b"} for i in range(4)]
          + [{"question_id": f"c{i}", "transcript_index": i, "world": "c"} for i in range(4)])

    result = runner.stratified_subset(trs, 6)
    assert len(result) == 6
    counts = {}
    for tr in result:
        counts[tr["world"]] = counts.get(tr["world"], 0) + 1
    assert counts == {"a": 2, "b": 2, "c": 2}

    # determinism: same input -> same output (byte-identical, not just same counts)
    assert runner.stratified_subset(trs, 6) == result


@requires_transcripts
def test_stratified_subset_covers_all_worlds_on_real_data():
    transcripts = runner._load_jsonl(TRANSCRIPTS_PATH)
    subset = runner.stratified_subset(transcripts, 100)
    assert len(subset) == 100
    assert {tr["world"] for tr in subset} == {"carath_norn", "selvarath", "vethun_sarak"}


def test_limit_zero_is_refused_not_treated_as_no_limit(tmp_path):
    # `if args.limit:` used to treat 0 as falsy -> "no limit", silently running the full
    # 318-transcript set instead of the caller's evident intent ("run zero transcripts").
    rc = runner.main(["--arms", "clean", "--limit", "0", "--dry-run",
                      "--out", str(tmp_path / "r.jsonl")])
    assert rc == 2


def test_load_done_keys_tolerates_malformed_tail(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"cell_key": "a|b|1|0|0"}\n{"cell_key": "trunc', encoding="utf-8")
    assert runner.load_done_keys(p) == {"a|b|1|0|0"}


def test_append_boundary_preserves_truncated_tail_and_separates_next_row(tmp_path):
    p = tmp_path / "r.jsonl"
    prefix = b'{"cell_key":"old"}\n{"cell_key":"trunc'
    p.write_bytes(prefix)

    runner.ensure_jsonl_append_boundary(p)
    with p.open("ab") as stream:
        stream.write(b'{"cell_key":"new"}\n')

    assert p.read_bytes() == prefix + b'\n{"cell_key":"new"}\n'
    assert runner.load_done_keys(p) == {"old", "new"}
