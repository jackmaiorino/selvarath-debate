"""Every path the supervisor and driver touch comes from the manifest, not a constant.

The tooling was written for the canary and hardcoded its filenames. The main run's manifest
names main_usage.jsonl / main_results.jsonl / main_reviewer_decisions.jsonl, so:

- the supervisor's halt signature read a usage ledger that does not exist, found no abandoned
  call, and would have stopped for manual review on EVERY ordinary transient, killing
  auto-resume for a run with dozens of them;
- its "halted cell already has a result row" guard read a results file that does not exist,
  so a real safety check silently passed;
- the review daemon's commit path validated against the canary schema and refused the main
  manifest outright, which aborted a 960-payload wave with zero committed.

These are one class of defect, not three incidents: tooling that verifies the artifact it was
handed while reading paths it assumed.
"""
import json

import pytest


def _manifest(tmp_path, stage, prefix):
    p = tmp_path / f"{stage}_manifest.json"
    p.write_text(json.dumps({
        "stage": stage,
        "schema_version": f"phase2_{stage}_execution_manifest_v1",
        "ledger": {
            "archive_dir": str(tmp_path),
            "usage_log_path": f"{tmp_path}/{prefix}_usage.jsonl",
            "results_path": f"{tmp_path}/{prefix}_results.jsonl",
            "decisions_path": f"{tmp_path}/{prefix}_reviewer_decisions.jsonl",
            "call_cache_path": f"{tmp_path}/{prefix}_call_cache.jsonl",
        },
    }), encoding="utf-8")
    return p


def test_the_supervisor_reads_the_manifests_ledger_paths(tmp_path):
    from scripts.canary_supervisor import ledger_paths_for

    manifest = _manifest(tmp_path, "main", "main")
    paths = ledger_paths_for(str(manifest), tmp_path)
    assert paths["usage"].name == "main_usage.jsonl"
    assert paths["results"].name == "main_results.jsonl"


def test_the_supervisor_still_reads_the_canary_paths(tmp_path):
    from scripts.canary_supervisor import ledger_paths_for

    manifest = _manifest(tmp_path, "canary", "canary")
    paths = ledger_paths_for(str(manifest), tmp_path)
    assert paths["usage"].name == "canary_usage.jsonl"
    assert paths["results"].name == "canary_results.jsonl"


def test_the_error_log_path_is_derived_not_hardcoded(tmp_path):
    """The driver wrote canary_error_log.jsonl into the MAIN archive, which happened to match
    what the supervisor read only by coincidence."""
    from rejudge.phase2_canary_live import error_log_path_for

    manifest = json.loads(_manifest(tmp_path, "main", "main").read_text(encoding="utf-8"))
    assert error_log_path_for(manifest, tmp_path).name == "main_error_log.jsonl"
    canary = json.loads(_manifest(tmp_path, "canary", "canary").read_text(encoding="utf-8"))
    assert error_log_path_for(canary, tmp_path).name == "canary_error_log.jsonl"


def test_the_commit_path_accepts_a_main_manifest():
    """What aborted the 960-payload wave: commit_reviewer_decisions validated against the
    canary schema directly rather than dispatching on the manifest's own."""
    import inspect

    from rejudge import phase2_canary_live as live

    src = inspect.getsource(live)
    # No direct canary-only validation should remain outside the dispatcher itself.
    lines = [l.strip() for l in src.splitlines()
             if "validate_canary_manifest(" in l and "def " not in l
             and "return validate_canary_manifest" not in l]
    assert not lines, (
        "these call sites bypass validate_manifest and will refuse a main manifest: "
        f"{lines}")


def test_the_error_log_prefix_follows_the_results_name_for_phase3_manifests(tmp_path):
    """2026-08-18 canary incident: the phase-3 manifest's usage ledger is phase3_usage.jsonl
    but its error log is keyed like its results file (phase3_canary_error_log.jsonl). The
    usage-prefix derivation tailed a nonexistent error log and refused every benign
    auto-resume; the prefix now follows the results name, identical for phase-2 shapes."""
    import json
    from scripts.canary_supervisor import ledger_paths_for

    p = tmp_path / "m.json"
    p.write_text(json.dumps({
        "stage": "phase3",
        "ledger": {
            "archive_dir": str(tmp_path),
            "usage_log_path": f"{tmp_path}/phase3_usage.jsonl",
            "canary_results_path": f"{tmp_path}/phase3_canary_results.jsonl",
            "main_results_path": f"{tmp_path}/phase3_main_results.jsonl",
            "decisions_path": f"{tmp_path}/phase3_reviewer_decisions.jsonl",
            "call_cache_path": f"{tmp_path}/phase3_call_cache.jsonl",
        },
    }), encoding="utf-8")
    paths = ledger_paths_for(str(p), tmp_path, results_key="canary_results_path")
    assert paths["errors"].name == "phase3_canary_error_log.jsonl"
