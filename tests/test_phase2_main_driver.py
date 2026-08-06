"""The driver accepting a main-run manifest.

run_live validated only canary manifests and _run_passes only ever ran the canary plan, so a
main-run manifest could be built, validated and hash-bound while nothing could execute it.
"""
import json

import pytest

from rejudge.phase2_canary_live import CanaryLiveError, plan_for, validate_manifest


def _canary_manifest():
    """Built fresh, not read from disk.

    The completed canaries' manifests are historical records whose code bundle is the code as
    it was then, and the bundle has since moved. Revalidating one against today's tree tests
    that the code changed, which it did, rather than that dispatch works.
    """
    from rejudge.phase2_canary_manifest import build_canary_manifest
    return build_canary_manifest(
        project_root=".", recorded_at_utc="2026-08-06T00:00:00Z",
        archive_dir="E:/selvarath-archive/unused-for-this-test")


def _main_manifest():
    from pathlib import Path
    return json.loads(Path(
        "rejudge/phase2_main_manifest_2026-08-06.json").read_text(encoding="utf-8"))


def test_validation_dispatches_on_the_manifests_own_schema():
    out = validate_manifest(_canary_manifest(), project_root=".")
    assert out["stage"] == "canary"


def test_an_unknown_schema_is_refused_rather_than_guessed():
    manifest = dict(_canary_manifest())
    manifest["schema_version"] = "something_invented"
    with pytest.raises((CanaryLiveError, ValueError)):
        validate_manifest(manifest, project_root=".")


def test_the_canary_plan_is_resolved_for_a_canary_manifest():
    cells = plan_for({"stage": "canary"}, project_root=".")
    assert len(cells) == 945
    assert all(c["kind"].startswith("canary_") for c in cells)


def test_the_main_plan_is_resolved_for_a_main_manifest():
    cells = plan_for(_main_manifest(), project_root=".")
    # capability_qa ran under the preflight's own authorization and must not be re-run here.
    assert len(cells) == 22140
    assert not any(c["kind"] == "capability_qa" for c in cells)
    assert not any(c["kind"].startswith("canary_") for c in cells)


def test_the_resolved_main_plan_is_dependency_closed():
    """execution_order refuses an orphaned dependency, so dropping capability_qa must not
    strand anything that depends on it."""
    from rejudge import phase2_canary_cells as cells_mod
    from rejudge.phase2_canary_order import execution_order
    from rejudge.phase2_plan import load_protocol

    protocol = load_protocol()
    bundle = json.loads(open("rejudge/phase2_prompt_bundle.json", encoding="utf-8").read())
    cells = plan_for(_main_manifest(), project_root=".")
    resolved = [cells_mod.resolve_cell(c, protocol, bundle,
                                       anchor_judge_model="Qwen/Qwen2.5-7B-Instruct-Turbo")
                for c in cells]
    ordered = execution_order(resolved)
    assert len(ordered) == len(resolved)
