"""The main-run execution manifest, and its validator.

A **sibling** of :mod:`rejudge.phase2_canary_manifest`, not an extension of it, for the same
reason that module is a sibling of :mod:`rejudge.phase2_execution`: the canary validator runs
an exact-key check against a canary-specific schema and pins ``stage == "canary"``, and
widening it would retroactively change what the canary manifests must contain, invalidating
records for two completed runs. Only the genuinely stage-agnostic helpers are reused.

Three differences from the canary manifest are substantive rather than cosmetic.

**The grid is the estimand.** 23,200 cells over the 82 main questions, disjoint from the 24
the canaries used. The canary could describe its plan by a count; this one binds the enumerated
cell set by hash, because a main-run plan that silently differs is a different experiment.

**Some cells are already paid for.** 1,060 capability-QA cells ran during the capability
preflight under its own manifest and authorization. They are part of the plan and must not be
re-run, so the manifest records both the full plan and the billable remainder, and the forecast
is built on the latter.

**Execution width is measured, not assumed.** The canary's concurrency ramp was overridden
rather than climbed, so no settled cap exists. This manifest therefore states its width as a
hypothesis with a defined validation window and revert, rather than as a settled value.

The manifest authorizes nothing. Its purpose is to pin exactly what a later, separate
authorization record refers to by hash.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase2_plan
from rejudge.phase2_canary_manifest import (CANARY_CODE_PROVENANCE_FILES,
                                            canary_code_bundle_sha256)
from rejudge.phase2_canary_manifest import _frozen_inputs as _shared_frozen_inputs
from rejudge.phase2_execution import canonical_sha256

STAGE = "main"
SCHEMA = "phase2_main_execution_manifest_v1"

MANIFEST_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "stage", "recorded_at_utc", "planning", "frozen_inputs", "reviewer",
    "anchor", "caps", "ledger", "governance", "code_provenance", "resume_granularity",
    "execution", "forecast", "execution_authorized", "execution_identity_sha256",
})


class MainManifestError(ValueError):
    """Raised when a main-run manifest does not validate. Always fails closed."""


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main_question_ids(project_root: str | Path = ".") -> tuple[str, ...]:
    """The 82 main questions: every question that is not held out for calibration.

    Derived rather than listed, so it cannot drift from the protocol's own exclusion set.
    """
    root = Path(project_root)
    protocol = _json(root / "rejudge" / "phase2_protocol.json")
    excluded = set(protocol["question_set"]["calibration_excluded_question_ids"])
    seen: list[str] = []
    with (root / "data" / "transcripts.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            qid = json.loads(line)["question_id"]
            if qid not in excluded and qid not in seen:
                seen.append(qid)
    return tuple(sorted(seen))


def enumerate_main_cells(project_root: str | Path = ".") -> list[dict[str, Any]]:
    protocol = _json(Path(project_root) / "rejudge" / "phase2_protocol.json")
    return phase2_plan.enumerate_cells(protocol, main_question_ids(project_root))


def billable_cells(cells) -> list[dict[str, Any]]:
    """Cells this stage actually pays for.

    capability_qa ran under the capability preflight's own manifest and authorization. Leaving
    it in the forecast would over-state the ask; leaving it out of the PLAN would understate
    what the analysis covers. So both are recorded and only this set is priced.
    """
    return [c for c in cells if c["kind"] != "capability_qa"]


def build_main_manifest(*, project_root: str | Path = ".", recorded_at_utc: str,
                        archive_dir: str, stage_cap_usd: float,
                        per_cell_settled_usd: float, per_cell_uncertain_usd: float,
                        execution: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble the manifest. Deterministic apart from the caller-supplied fields."""
    root = Path(project_root)
    cells = enumerate_main_cells(root)
    billable = billable_cells(cells)
    # REUSED, not reimplemented. Hand-rolling this set bound the reviewer prompt with a
    # canonical JSON hash where the driver checks the artifact's declared hash of the prompt
    # TEXT, and silently omitted the checker system prompt, the no-query payload and the price
    # snapshot. Each frozen input has its own correct hash KIND, and the canary builder is
    # where that knowledge lives; only the plan bindings differ between stages.
    frozen = dict(_shared_frozen_inputs(root))
    del frozen["canary_cells_sha256"]
    del frozen["canary_plan_sha256"]
    anchor_approval = "rejudge/phase2_anchor_parser_policy_approval_2026-07-24.json"
    canary_completion = "rejudge/phase2_canary_bridge_completion_2026-08-06.json"
    missing_data = "rejudge/phase2_missing_data_policy_proposal_2026-08-04.json"

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA,
        "stage": STAGE,
        "recorded_at_utc": recorded_at_utc,
        "planning": {
            "cell_count": len(cells),
            "billable_cell_count": len(billable),
            "already_executed_elsewhere": {
                "capability_qa": len(cells) - len(billable),
                "under": "the capability preflight's own manifest and authorization",
            },
            "question_count": len(main_question_ids(root)),
            "cell_key_namespace": _json(
                root / "rejudge" / "phase2_protocol.json")["cell_key_namespace"],
            # The enumerated set itself, not just its size: a plan that silently differs is a
            # different experiment, and a count cannot detect that.
            "cells_sha256": canonical_sha256(cells),
        },
        "frozen_inputs": frozen,
        "reviewer": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "prompt_tracked_path": "rejudge/phase2_reviewer_prompt_2026-07-23.json",
            "isolation": ("fresh isolated context per payload: no repository, tools, "
                          "retrieval, conversation memory, or cross-query discussion"),
        },
        "anchor": {
            "judge_model": "Qwen/Qwen2.5-7B-Instruct-Turbo",
            "approval_tracked_path": anchor_approval,
            "approval_sha256": canonical_sha256(_json(root / anchor_approval)),
        },
        "caps": {"stage_cap_usd": stage_cap_usd, "cumulative_cap_usd": 1709.24},
        "ledger": {
            "archive_dir": archive_dir,
            "usage_log_path": f"{archive_dir}/main_usage.jsonl",
            "results_path": f"{archive_dir}/main_results.jsonl",
            "decisions_path": f"{archive_dir}/main_reviewer_decisions.jsonl",
            "call_cache_path": f"{archive_dir}/main_call_cache.jsonl",
        },
        "governance": {
            name: {"tracked_path": path,
                   "canonical_sha256": canonical_sha256(_json(root / path))}
            for name, path in sorted({
                "bridge_canary_completion": canary_completion,
                "missing_data_policy": missing_data,
                "checker_frozen_config":
                    "rejudge/phase2_checker_frozen_config_2026-07-23.json",
                "prompt_bundle_approval":
                    "rejudge/phase2_prompt_bundle_approval_2026-07-18.json",
            }.items())
        },
        "code_provenance": {
            "files": list(CANARY_CODE_PROVENANCE_FILES),
            "code_bundle_sha256": canary_code_bundle_sha256(root),
        },
        "resume_granularity": "cell",
        "execution": dict(execution),
        "forecast": {
            "basis": ("measured per-cell cost of the 2026-08-04 bridge canary, which ran the "
                      "same pipeline over the 24 HELD-OUT questions. Main questions may differ "
                      "in length and query behaviour, so this is an estimate from a related "
                      "but not identical grid."),
            "per_cell_settled_usd": per_cell_settled_usd,
            "per_cell_uncertain_usd": per_cell_uncertain_usd,
            "projected_settled_usd": round(per_cell_settled_usd * len(billable), 2),
            "projected_uncertain_usd": round(per_cell_uncertain_usd * len(billable), 2),
            "projected_total_usd": round(
                (per_cell_settled_usd + per_cell_uncertain_usd) * len(billable), 2),
        },
        "execution_authorized": False,
    }
    manifest["execution_identity_sha256"] = canonical_sha256(manifest)
    return manifest


def validate_main_manifest(manifest: Mapping[str, Any], *,
                           project_root: str | Path = ".") -> dict[str, Any]:
    """Re-derive every binding from the real artifacts and refuse on any mismatch."""
    if not isinstance(manifest, Mapping):
        raise MainManifestError("manifest must be a mapping")
    if set(manifest) != MANIFEST_TOP_LEVEL_KEYS:
        raise MainManifestError(
            f"main manifest fields drifted: unexpected "
            f"{sorted(set(manifest) - MANIFEST_TOP_LEVEL_KEYS)!r}, missing "
            f"{sorted(MANIFEST_TOP_LEVEL_KEYS - set(manifest))!r}")
    if manifest["schema_version"] != SCHEMA:
        raise MainManifestError(f"schema must be {SCHEMA!r}")
    if manifest["stage"] != STAGE:
        raise MainManifestError(f"stage must be {STAGE!r}")
    if manifest["execution_authorized"] is not False:
        raise MainManifestError(
            "a manifest must never authorize itself; authorization lives in its own record")

    root = Path(project_root)
    cells = enumerate_main_cells(root)
    if manifest["planning"]["cells_sha256"] != canonical_sha256(cells):
        raise MainManifestError(
            "the enumerated main-run cell set no longer matches the manifest's binding; the "
            "plan has changed and this is a different experiment")
    if manifest["planning"]["cell_count"] != len(cells):
        raise MainManifestError("planning.cell_count disagrees with the enumerated plan")
    if manifest["code_provenance"]["code_bundle_sha256"] != canary_code_bundle_sha256(root):
        raise MainManifestError("the code bundle has changed since this manifest was built")
    for name, entry in manifest["governance"].items():
        path = root / entry["tracked_path"]
        if not path.exists():
            raise MainManifestError(f"governance artifact {name} is missing at {path}")
        if canonical_sha256(_json(path)) != entry["canonical_sha256"]:
            raise MainManifestError(f"governance artifact {name} has changed since binding")

    identity = dict(manifest)
    recorded = identity.pop("execution_identity_sha256")
    if canonical_sha256(identity) != recorded:
        raise MainManifestError("execution identity does not match the manifest contents")
    return {"stage": STAGE, "execution_identity_sha256": recorded,
            "cells": len(cells), "billable": manifest["planning"]["billable_cell_count"]}
