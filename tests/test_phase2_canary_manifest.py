"""The canary execution manifest and its validator.

A sibling of phase2_execution rather than an extension of it. That module runs an exact-key
check against a 29-key preflight-specific schema *before* it reaches its stage check, so a
canary manifest fails there with a drift error rather than an unsupported-stage one; and
widening those constants would retroactively change what a preflight manifest must contain,
invalidating the r3 manifest that is already bound and executed.

Two shape differences from the preflight are deliberate. The canary binds no fixed
provider_call_inventory, because a judgment's call count depends on how many slots the judge
uses, how many rejections trigger the free retry, and whether the gate blocks before dispatch
-- none of which a manifest can know in advance. And it binds the call-cache path, because a
cache hit bypasses both the provider and the spend ledger, so a cache from another run must
never be readable by this one.

The manifest authorizes nothing on its own. Its whole purpose is to pin what a later, separate
authorization record refers to by hash.
"""
import json
from pathlib import Path

import pytest

from rejudge.phase2_canary_manifest import (
    CANARY_CODE_PROVENANCE_FILES, ManifestValidationError, build_canary_manifest,
    canary_code_bundle_sha256, validate_canary_manifest)


def _manifest(**overrides):
    manifest = build_canary_manifest(
        project_root=".", recorded_at_utc="2026-07-25T00:00:00Z",
        archive_dir="E:/selvarath-archive/canary-2026-07-25")
    manifest.update(overrides)
    return manifest


# --- what the manifest binds ------------------------------------------------------------

def test_the_frozen_plan_hashes_are_bound():
    bindings = _manifest()["frozen_inputs"]
    assert bindings["canary_cells_sha256"] == (
        "b625ce977df0c101fd83fe5086494eac73b0a48371b4f385b49ea438750c8537")
    assert bindings["canary_plan_sha256"] == (
        "eeeb1bc312fb7e52f9fef7492484d24360b12baffa86f1eca5b3be919e37ee85")


def test_the_gate_artifacts_are_bound():
    bindings = _manifest()["frozen_inputs"]
    assert bindings["reviewer_prompt_sha256"] == (
        "b48a125af874b287aa93e73671c9c12c9d8c4245d50e61013c690209139815f4")
    assert bindings["checker_system_prompt_sha256"] == (
        "ecb22b55af091a2dc35c3f46e145db9c6796b2517a83dfec8682b75eb58e7428")
    assert bindings["checker_model"] == "google/gemma-4-31B-it"


def test_the_reviewer_model_is_pinned_to_the_frozen_identity():
    # The amendment, the owner decision record and the frozen reviewer prompt all name
    # claude-fable-5. A reviewer that inherited whatever session model happened to be running
    # would be a protocol deviation under consult #27.
    assert _manifest()["reviewer"]["model"] == "claude-fable-5"


def test_the_resolved_anchor_and_its_approval_are_bound():
    manifest = _manifest()
    assert manifest["anchor"]["judge_model"] == "Qwen/Qwen2.5-7B-Instruct-Turbo"
    assert manifest["anchor"]["approval_tracked_path"].endswith(
        "phase2_anchor_parser_policy_approval_2026-07-24.json")


def test_the_cap_is_the_owner_approved_forty_dollars():
    assert _manifest()["caps"]["stage_cap_usd"] == 40.0


def test_the_call_cache_path_is_bound():
    # A cache hit bypasses the provider and the ledger, so which cache this run may read is
    # part of its identity, not an incidental detail.
    assert _manifest()["ledger"]["call_cache_path"].endswith("canary_call_cache.jsonl")


def test_no_fixed_provider_call_inventory_is_bound():
    # Canary call counts are runtime-dependent; a fixed inventory would be a lie.
    manifest = _manifest()
    assert "provider_call_inventory" not in manifest
    assert manifest["resume_granularity"] == "cell"


def test_the_planned_cell_count_is_bound():
    assert _manifest()["planning"]["cell_count"] == 945


def test_the_manifest_authorizes_nothing_by_itself():
    assert _manifest()["execution_authorized"] is False


# --- code provenance ----------------------------------------------------------------------

def test_the_canary_provenance_set_is_separate_from_the_preflight_nine():
    from rejudge.phase2_execution import CODE_PROVENANCE_FROZEN_FILES
    assert not set(CANARY_CODE_PROVENANCE_FILES) & set(CODE_PROVENANCE_FROZEN_FILES)


def test_the_canary_provenance_set_covers_the_modules_that_execute_the_science():
    for expected in ("rejudge/judge_loop.py", "rejudge/debate_gen.py",
                     "rejudge/phase2_canary_gate.py", "rejudge/phase2_canary_execute.py",
                     "rejudge/phase2_canary_runner.py", "rejudge/phase2_call_cache.py"):
        assert expected in CANARY_CODE_PROVENANCE_FILES, expected


def test_every_provenance_file_exists_and_is_lf_pinned():
    attributes = Path(".gitattributes").read_text(encoding="utf-8")
    for relative in CANARY_CODE_PROVENANCE_FILES:
        assert Path(relative).is_file(), relative
        assert f"{relative} text eol=lf" in attributes, relative


def test_the_bundle_hash_changes_when_a_provenance_file_changes(tmp_path):
    import shutil
    root = tmp_path / "repo"
    shutil.copytree(".", root, ignore=shutil.ignore_patterns(
        ".git", "data", "rejudge/output", "__pycache__", ".venv", ".pytest_cache"))
    before = canary_code_bundle_sha256(root)
    target = root / "rejudge" / "judge_loop.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# drift\n",
                      encoding="utf-8", newline="\n")
    assert canary_code_bundle_sha256(root) != before


def test_the_canary_bundle_is_independent_of_the_preflight_bundle(tmp_path):
    import shutil
    from rejudge.phase2_execution import compute_code_bundle_sha256
    root = tmp_path / "repo"
    shutil.copytree(".", root, ignore=shutil.ignore_patterns(
        ".git", "data", "rejudge/output", "__pycache__", ".venv", ".pytest_cache"))
    preflight_before = compute_code_bundle_sha256(root)
    target = root / "rejudge" / "phase2_canary_runner.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# drift\n",
                      encoding="utf-8", newline="\n")
    # Touching a canary module must not disturb the preflight manifest already bound.
    assert compute_code_bundle_sha256(root) == preflight_before


# --- identity -----------------------------------------------------------------------------

def test_the_execution_identity_is_the_canonical_hash_of_the_manifest():
    from rejudge.phase2_execution import canonical_sha256
    manifest = _manifest()
    identity = manifest.pop("execution_identity_sha256")
    assert identity == canonical_sha256(manifest)


def test_two_builds_agree():
    assert _manifest() == _manifest()


def test_changing_any_bound_field_changes_the_identity():
    baseline = _manifest()["execution_identity_sha256"]
    drifted = _manifest()
    drifted["caps"]["stage_cap_usd"] = 41.0
    drifted.pop("execution_identity_sha256")
    from rejudge.phase2_execution import canonical_sha256
    assert canonical_sha256(drifted) != baseline


# --- validation -----------------------------------------------------------------------------

def test_a_freshly_built_manifest_validates():
    validate_canary_manifest(_manifest(), project_root=".")


def test_a_manifest_whose_identity_does_not_match_is_refused():
    manifest = _manifest(execution_identity_sha256="0" * 64)
    with pytest.raises(ManifestValidationError):
        validate_canary_manifest(manifest, project_root=".")


def test_a_drifted_frozen_hash_is_refused():
    manifest = _manifest()
    manifest["frozen_inputs"]["reviewer_prompt_sha256"] = "0" * 64
    with pytest.raises(ManifestValidationError):
        validate_canary_manifest(manifest, project_root=".")


def test_an_unexpected_top_level_key_is_refused():
    manifest = _manifest(surprise="value")
    with pytest.raises(ManifestValidationError):
        validate_canary_manifest(manifest, project_root=".")


def test_a_missing_top_level_key_is_refused():
    manifest = _manifest()
    del manifest["caps"]
    with pytest.raises(ManifestValidationError):
        validate_canary_manifest(manifest, project_root=".")


def test_an_authorized_flag_set_in_the_manifest_itself_is_refused():
    # Authorization lives in its own record. A manifest that could authorize itself would
    # defeat the separation the control plane is built on.
    manifest = _manifest(execution_authorized=True)
    with pytest.raises(ManifestValidationError):
        validate_canary_manifest(manifest, project_root=".")


def test_the_stage_is_canary():
    assert _manifest()["stage"] == "canary"


def test_phase2_execution_still_refuses_a_canary_stage():
    # Confirms the sibling really is necessary rather than a duplication of something that
    # would have worked.
    from rejudge.phase2_execution import validate_execution_manifest
    with pytest.raises(Exception):
        validate_execution_manifest(_manifest(), project_root=".")


def test_the_written_manifest_reloads_and_revalidates(tmp_path):
    path = tmp_path / "manifest.json"
    manifest = _manifest()
    path.write_text(json.dumps(manifest, ensure_ascii=True, sort_keys=True,
                               separators=(",", ":"), allow_nan=False) + "\n",
                    encoding="utf-8")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == manifest
    validate_canary_manifest(reloaded, project_root=".")
