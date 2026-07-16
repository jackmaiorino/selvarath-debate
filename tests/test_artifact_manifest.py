import json

import pytest

from rejudge import artifact_manifest as am


def test_build_and_verify_manifest(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    rows = data / "records.jsonl"
    rows.write_text('{"x":1}\n{"x":2}', encoding="utf-8")
    note = data / "README.md"
    note.write_text("provenance", encoding="utf-8")

    manifest = am.build_manifest(tmp_path, ["data/*"])
    by_name = {entry["path"]: entry for entry in manifest["entries"]}
    assert by_name["data/records.jsonl"]["jsonl_rows"] == 2
    assert "jsonl_rows" not in by_name["data/README.md"]
    am.verify_manifest(tmp_path, manifest)

    rows.write_text('{"x":3}\n', encoding="utf-8")
    with pytest.raises(am.ArtifactVerificationError, match="mismatch"):
        am.verify_manifest(tmp_path, manifest)


def test_cli_round_trip(tmp_path):
    (tmp_path / "artifact.bin").write_bytes(b"abc")
    out = tmp_path / "manifest.json"
    assert am.main(["build", "--root", str(tmp_path), "--out", str(out),
                    "artifact.bin"]) == 0
    stored = json.loads(out.read_text(encoding="utf-8"))
    assert stored["entries"][0]["bytes"] == 3
    assert am.main(["verify", "--root", str(tmp_path), str(out)]) == 0


def test_expansion_deduplicates_and_stays_inside_root(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    paths = am.expand_artifacts(tmp_path.resolve(), ["*.txt", "a.*"])
    assert paths == [(tmp_path / "a.txt").resolve()]
