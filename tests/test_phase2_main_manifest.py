"""The main-run execution manifest.

A sibling of the canary manifest rather than an extension, because widening the canary
validator would retroactively change what two completed runs' manifests must contain.
"""
import json

import pytest
from pathlib import Path

from rejudge.phase2_main_manifest import (MainManifestError, billable_cells,
                                          build_main_manifest, enumerate_main_cells,
                                          main_question_ids, validate_main_manifest)

EXECUTION = {
    "max_workers": 16,
    "block_size": 32,
    "model_caps": {"google/gemma-4-31B-it": 8},
    "width_is_a_hypothesis": "validated in the first hours, reverted if it does not hold",
}


def _manifest(**overrides):
    m = build_main_manifest(
        project_root=".", recorded_at_utc="2026-08-06T00:00:00Z",
        archive_dir="E:/selvarath-archive/main-2026-08-06", stage_cap_usd=400.0,
        per_cell_settled_usd=0.00572, per_cell_uncertain_usd=0.00213, execution=EXECUTION)
    m.update(overrides)
    return m


# The pilot transcript corpus is untracked by design (data/*.jsonl is gitignored so the
# fictional eval worlds stay out of public training corpora). These tests bind the real
# corpus and can only run where it exists; skipping elsewhere is the honest outcome.
pytestmark = pytest.mark.skipif(not Path("data/transcripts.jsonl").exists(),
                                  reason="pilot transcript corpus is untracked by design")

def test_the_grid_is_the_pre_registered_one():
    assert len(main_question_ids(".")) == 82
    assert len(enumerate_main_cells(".")) == 23200


def test_the_capability_qa_cells_are_in_the_plan_but_not_billed():
    cells = enumerate_main_cells(".")
    billable = billable_cells(cells)
    assert len(cells) - len(billable) == 1060
    m = _manifest()
    assert m["planning"]["cell_count"] == 23200
    assert m["planning"]["billable_cell_count"] == 22140
    assert m["forecast"]["projected_total_usd"] < m["caps"]["stage_cap_usd"], (
        "a manifest whose own forecast exceeds its cap is asking for a halt, not a run")


def test_the_enumerated_cell_set_is_bound_not_just_its_size():
    """A plan that silently differs is a different experiment, and a count cannot detect it."""
    m = _manifest()
    assert len(m["planning"]["cells_sha256"]) == 64
    validate_main_manifest(m, project_root=".")
    m["planning"]["cells_sha256"] = "0" * 64
    with pytest.raises(MainManifestError):
        validate_main_manifest(m, project_root=".")


def test_a_freshly_built_manifest_validates():
    out = validate_main_manifest(_manifest(), project_root=".")
    assert out["cells"] == 23200 and out["billable"] == 22140


def test_a_manifest_cannot_authorize_itself():
    with pytest.raises(MainManifestError):
        validate_main_manifest(_manifest(execution_authorized=True), project_root=".")


def test_a_tampered_identity_is_refused():
    m = _manifest()
    m["caps"] = {**m["caps"], "stage_cap_usd": 99999.0}
    with pytest.raises(MainManifestError):
        validate_main_manifest(m, project_root=".")


def test_the_bridge_canary_close_out_is_bound():
    """The main run's licence to exist is that the corrected pipeline was proven to run clean.
    Binding that record's hash is what makes the claim checkable rather than asserted."""
    gov = _manifest()["governance"]
    assert "bridge_canary_completion" in gov
    assert "missing_data_policy" in gov


def test_a_freshly_built_main_manifest_carries_the_canary_validated_transport_pins():
    # The main manifest built on 2026-08-06 resolved and bound the same role-limits artifact
    # the canary used (rejudge.phase2_canary_manifest.resolve_role_limits is shared code, not
    # duplicated per stage), which includes the v5-restructured request_settings.transport
    # section as amended on 2026-08-01 (read timeout 600s -> 120s; every other v5 transport
    # value, and sdk_internal_max_retries, unchanged). Until now nothing asserted that this
    # binding actually resolves to a role-limits artifact carrying the pins at all -- only that
    # whatever it resolved to got hashed -- so a role-limits artifact that silently dropped its
    # transport section, leaving a live run's SDK client built with no explicit timeout (the
    # class of gap behind the 2026-08-10 main-run silent-hang incident, where a dead streaming
    # connection went unnoticed until an operator killed it by hand), would have bound cleanly.
    # This test locks the main path to the exact values the canary actually validated live.
    from rejudge.phase2_canary_manifest import resolve_role_limits
    resolved = resolve_role_limits(Path("."))
    bindings = _manifest()["frozen_inputs"]
    assert bindings["role_limits_sha256"] == resolved["sha256"]
    assert bindings["role_limits_tracked_path"] == resolved["tracked_path"]

    transport = json.loads(
        Path(resolved["tracked_path"]).read_text(encoding="utf-8")
    )["request_settings"]["transport"]
    assert transport["http_timeout"] == {"connect": 10, "read": 120, "write": 60, "pool": 60}
    assert transport["sdk_internal_max_retries"] == 0
    assert transport["per_call_wall_clock_ceiling_seconds"] == 1200
