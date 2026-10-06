import json
import platform
import sys

import pytest

from finalphase import cli, preflight


def test_supported_launcher_refuses_unqualified_spend(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "RUN_ROOT", tmp_path)
    monkeypatch.setattr(preflight, "source_commit", lambda: "commit")
    for key in preflight.PROVIDER_KEYS:
        monkeypatch.setenv(key, "test-key")
    (tmp_path / "run_manifest.json").write_text(json.dumps({
        "git_commit": "commit", "ceiling_usd": 6000,
        "provider_checks": {"anthropic": "passed", "openai": "passed", "together": "passed"},
        "funding": {"reconciled": True, "available_usd": 6000, "reference": "owner confirmation"},
    }))
    calls = []
    monkeypatch.setattr(cli, "cmd_author", lambda args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["cli", "author"])
    with pytest.raises(RuntimeError, match="throughput receipt"):
        cli.main()
    assert not calls
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setattr(sys, "argv", ["cli", "author", "--worlds", "8", "--quality-check"])
    with pytest.raises(RuntimeError, match="missing ANTHROPIC_API_KEY"):
        cli.main()
    assert not calls


def test_qualified_launch_and_bounded_quality_check(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "source_commit", lambda: "commit")
    for key in preflight.PROVIDER_KEYS:
        monkeypatch.setenv(key, "test-key")
    receipt = {
        "git_commit": "commit", "host": platform.node(), "mode": "batch", "selected_workers": 8,
        "semantics_preserved": True, "placements_checked": ["Jack's PC", "HaleysPC", "RunPod"],
        "serial": {"workers": 1, "completed": 4, "elapsed_seconds": 8,
                   "input_sha256": "a" * 64, "output_sha256": "b" * 64},
        "parallel": {"workers": 8, "completed": 4, "elapsed_seconds": 2,
                     "input_sha256": "a" * 64, "output_sha256": "c" * 64},
    }
    manifest = {"git_commit": "commit", "ceiling_usd": 6000,
                "provider_checks": {"anthropic": "passed", "openai": "passed", "together": "passed"},
                "funding": {"reconciled": True, "available_usd": 6000, "reference": "owner confirmation"},
                "throughput": {"author": receipt}}
    path = tmp_path / "run_manifest.json"
    path.write_text(json.dumps(manifest))
    assert not preflight.check("author", tmp_path, 8, "batch")["ready"]
    assert "independent Claude audit receipt is missing or invalid" in preflight.check("author", tmp_path, 8, "batch")["reasons"]
    assert not preflight.check("author", tmp_path, 16, "batch")["ready"]
    assert not preflight.check("pilot", tmp_path, 8, "batch")["ready"]
    assert preflight.check("author", tmp_path, 8, "batch", quality_check=True)["ready"]
    manifest["funding"]["reconciled"] = False
    path.write_text(json.dumps(manifest))
    assert preflight.check("author", tmp_path, 8, "batch", quality_check=True)["ready"]
    assert not preflight.check("main", tmp_path, 8, "batch")["ready"]
    manifest["throughput"]["author"] = {**receipt, "execution_sha256": "source"}
    monkeypatch.setattr(preflight, "execution_sha256", lambda: "source")
    monkeypatch.setattr(preflight, "source_commit", lambda: "docs-commit")
    manifest["git_commit"] = "docs-commit"
    path.write_text(json.dumps(manifest))
    assert not preflight.check("author", tmp_path, 8, "batch")["ready"]
    monkeypatch.setattr(preflight, "execution_sha256", lambda: "changed-source")
    assert not preflight.check("author", tmp_path, 8, "batch")["ready"]


def test_stage_caps_and_reserve_stay_inside_total():
    assert sum(cli.STAGE_CAPS.values()) + 590 == preflight.CEILING_USD
