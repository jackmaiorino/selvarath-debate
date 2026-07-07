import json

from rejudge import runner


def test_iter_cells_counts_and_legacy_k1():
    trs = [{"question_id": f"Q{i}", "transcript_index": i} for i in range(4)]
    cells = runner.iter_cells(["clean", "legacy"], {"clean": [0, 1], "legacy": [1]},
                              trs, replicates=2)
    clean = [c for c in cells if c["arm"] == "clean"]
    legacy = [c for c in cells if c["arm"] == "legacy"]
    assert len(clean) == 4 * 2 * 2            # transcripts x budgets x K
    assert len(legacy) == 4 * 1 * 1           # legacy is K=1
    assert len({c["cell_key"] for c in cells}) == len(cells)


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
