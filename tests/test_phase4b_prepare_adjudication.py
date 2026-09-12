import hashlib
import json
from pathlib import Path

import pytest

from scripts.phase4b_prepare_adjudication import build, jsonl_bytes
from rejudge.phase4_runner import sha


def fixture_panel(tmp_path):
    world = "The bridge is blue."
    world_sha = hashlib.sha256(world.encode()).hexdigest()
    claim = "The bridge is blue."
    claim_id = hashlib.sha256((world_sha + "|" + claim).encode()).hexdigest()
    files = {
        "blind_claims.jsonl": [{"claim_id": claim_id, "world_sha256": world_sha, "exact_claim": claim}],
        "worlds_private.jsonl": [{"world_sha256": world_sha, "world_document": world}],
    }
    for name, rows in files.items():
        (tmp_path / name).write_bytes(jsonl_bytes(rows))
    oracle = "The frozen three-label oracle contract."
    (tmp_path / "manifest.json").write_text(json.dumps({
        "outputs": {name: {"sha256": sha(tmp_path / name)} for name in files},
        "oracle_contract": {"system_prompt": oracle},
        "template_provenance": {"oracle_system_prompt_sha256": hashlib.sha256(oracle.encode()).hexdigest()}}))
    config = json.loads((Path(__file__).parents[1] / "rejudge/phase4b_adjudication_2026-09-12.json").read_bytes())
    return config


def test_blind_paired_panel_reproduces_without_original_labels(tmp_path):
    config = fixture_panel(tmp_path)
    first = build(tmp_path, config)
    assert first["adjudication_calls"] == 2
    assert first == build(tmp_path, config)
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    packets = [json.loads(line) for line in (tmp_path / "claim_packets.jsonl").read_text().splitlines()]
    assert len(packets) == 1
    assert {c["judge"] for c in calls} == set(config["models"])
    assert len({c["messages_sha256"] for c in calls}) == 1
    assert len({c["seed"] for c in calls}) == 2
    content = json.loads(packets[0]["messages"][1]["content"])
    assert set(content) == {"world_document", "exact_claim"}


def test_prepared_source_drift_rejected(tmp_path):
    config = fixture_panel(tmp_path)
    with (tmp_path / "worlds_private.jsonl").open("a") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="Prepared source changed"):
        build(tmp_path, config)


def test_changing_frozen_call_setting_cannot_overwrite_panel(tmp_path):
    config = fixture_panel(tmp_path)
    build(tmp_path, config)
    config["model_settings"][config["models"][0]]["reasoning_effort"] = "high"
    with pytest.raises(ValueError, match="Existing prepared output differs"):
        build(tmp_path, config)
