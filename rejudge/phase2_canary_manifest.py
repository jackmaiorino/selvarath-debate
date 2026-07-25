"""The canary execution manifest, and its validator.

A **sibling** of :mod:`rejudge.phase2_execution`, not an extension of it. That module runs an
exact-key check against a 29-key preflight-specific schema *before* it reaches its stage
check, so a canary manifest fails there as field drift rather than as an unsupported stage;
and widening those constants would retroactively change what a preflight manifest must
contain, invalidating the r3 manifest that is already bound and executed. Only the genuinely
stage-agnostic helpers are reused, chiefly ``canonical_sha256``.

Two shape differences from the preflight are deliberate.

**No fixed provider call inventory.** The preflight can enumerate its calls exactly because
each of its cells is one call. A canary judgment's call count depends on how many query slots
the judge uses before signalling DONE, how many rejections trigger the one free retry, and
whether the dual gate blocks before dispatch. None of that is knowable in advance, so resume
is cell-granular and the per-call cache handles replay within a cell.

**The call-cache path is bound.** A cache hit bypasses the provider *and* the spend ledger, so
which cache this run may read is part of its identity rather than an incidental detail.

The manifest authorizes nothing. Its purpose is to pin exactly what a later, separate
authorization record refers to by hash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase2_plan
from rejudge.phase2_execution import canonical_sha256

STAGE = "canary"
STAGE_CAP_USD = 40.0
REVIEWER_MODEL = "claude-fable-5"
APPROVED_ANCHOR_JUDGE_MODEL = "Qwen/Qwen2.5-7B-Instruct-Turbo"

# Deliberately disjoint from phase2_execution.CODE_PROVENANCE_FROZEN_FILES: adding any of
# these to that tuple would change the capability preflight's own code_bundle_sha256 and
# force its already-executed manifest to be rebuilt. Several of these execute the science and
# were, until the canary, hash-bound nowhere. Order is part of the hash.
CANARY_CODE_PROVENANCE_FILES: tuple[str, ...] = (
    "rejudge/judge_loop.py",
    "rejudge/debate_gen.py",
    "rejudge/composer.py",
    "rejudge/oracle_channel.py",
    "rejudge/query_screen.py",
    "rejudge/parsers.py",
    "rejudge/records.py",
    "rejudge/config.py",
    "rejudge/phase2_query_gate.py",
    "rejudge/phase2_dual_gate.py",
    "rejudge/phase2_canary_gate.py",
    "rejudge/phase2_canary_cells.py",
    "rejudge/phase2_canary_compose.py",
    "rejudge/phase2_canary_order.py",
    "rejudge/phase2_canary_execute.py",
    "rejudge/phase2_canary_runner.py",
    "rejudge/phase2_call_cache.py",
    "rejudge/phase2_caching_client.py",
)

MANIFEST_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "stage", "recorded_at_utc", "planning", "frozen_inputs", "reviewer",
    "anchor", "caps", "ledger", "governance", "code_provenance", "resume_granularity",
    "execution_authorized", "execution_identity_sha256",
})

REPO_ROOT = Path(__file__).resolve().parents[1]


class ManifestValidationError(ValueError):
    """Raised when a canary manifest does not validate. Always fails closed."""


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canary_code_bundle_sha256(project_root: str | Path = ".") -> str:
    """Hash the canary provenance files as an ordered list of raw-byte hashes.

    Raw bytes, not canonical JSON: these are Python sources. The order is part of the hash, so
    reordering the tuple is itself a change.
    """
    root = Path(project_root)
    entries = [{"path": relative, "sha256": _raw_sha256(root / relative)}
               for relative in CANARY_CODE_PROVENANCE_FILES]
    return canonical_sha256(entries)


def _frozen_inputs(root: Path) -> dict[str, Any]:
    protocol = _json(root / "rejudge" / "phase2_protocol.json")
    cells = phase2_plan.enumerate_canary_cells(protocol)
    plan = phase2_plan.build_canary_plan(protocol)
    checker = _json(root / "rejudge" / "phase2_checker_frozen_config_2026-07-23.json")
    reviewer_prompt = _json(root / "rejudge" / "phase2_reviewer_prompt_2026-07-23.json")
    bundle_approval = _json(
        root / "rejudge" / "phase2_prompt_bundle_approval_2026-07-18.json")

    return {
        "protocol_sha256": canonical_sha256(protocol),
        "canary_cells_sha256": canonical_sha256(cells),
        "canary_plan_sha256": canonical_sha256(plan),
        "prompt_bundle_sha256": canonical_sha256(
            _json(root / "rejudge" / "phase2_prompt_bundle.json")),
        "prompt_bundle_approval_sha256": canonical_sha256(bundle_approval),
        "reviewer_prompt_sha256": reviewer_prompt["prompt_sha256"],
        "checker_system_prompt_sha256": checker["configuration"]["system_prompt_sha256"],
        "checker_model": checker["configuration"]["model"],
        "checker_frozen_config_sha256": canonical_sha256(checker),
        "role_limits_v5_sha256": canonical_sha256(
            _json(root / "rejudge" / "phase2_role_limits_v5_2026-07-19.json")),
        "price_snapshot_sha256": canonical_sha256(
            _json(root / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json")),
    }


def _governance(root: Path) -> dict[str, Any]:
    """The artifacts that make this run the run they describe."""
    paths = {
        "claude_gate_amendment": "rejudge/phase2_canary_claude_gate_amendment_2026-07-23.json",
        "governance_ratification": "rejudge/phase2_canary_governance_ratification_2026-07-23.json",
        "owner_decision": "rejudge/phase2_canary_owner_decision_2026-07-23.json",
        "build_decisions": "rejudge/phase2_canary_build_decisions_2026-07-24.json",
        "checker_frozen_config": "rejudge/phase2_checker_frozen_config_2026-07-23.json",
    }
    return {name: {"tracked_path": relative,
                   "canonical_sha256": canonical_sha256(_json(root / relative))}
            for name, relative in sorted(paths.items())}


def build_canary_manifest(*, project_root: str | Path = ".", recorded_at_utc: str,
                          archive_dir: str) -> dict[str, Any]:
    """Assemble the manifest. Deterministic apart from the two caller-supplied fields."""
    root = Path(project_root)
    anchor_approval_path = (
        "rejudge/phase2_anchor_parser_policy_approval_2026-07-24.json")

    manifest: dict[str, Any] = {
        "schema_version": "phase2_canary_execution_manifest_v1",
        "stage": STAGE,
        "recorded_at_utc": recorded_at_utc,
        "planning": {
            "cell_count": 945,
            "cell_key_namespace": _json(
                root / "rejudge" / "phase2_protocol.json")["cell_key_namespace"],
        },
        "frozen_inputs": _frozen_inputs(root),
        "reviewer": {
            "model": REVIEWER_MODEL,
            "prompt_tracked_path": "rejudge/phase2_reviewer_prompt_2026-07-23.json",
            "isolation": ("fresh isolated context per batch: no repository, tools, retrieval, "
                          "conversation memory, or cross-query discussion"),
        },
        "anchor": {
            "judge_model": APPROVED_ANCHOR_JUDGE_MODEL,
            "approval_tracked_path": anchor_approval_path,
            "approval_sha256": canonical_sha256(_json(root / anchor_approval_path)),
        },
        "caps": {
            "stage_cap_usd": STAGE_CAP_USD,
            "cumulative_cap_usd": 1709.24,
        },
        "ledger": {
            "archive_dir": archive_dir,
            "usage_log_path": f"{archive_dir}/canary_usage.jsonl",
            "results_path": f"{archive_dir}/canary_results.jsonl",
            "decisions_path": f"{archive_dir}/canary_reviewer_decisions.jsonl",
            "call_cache_path": f"{archive_dir}/canary_call_cache.jsonl",
        },
        "governance": _governance(root),
        "code_provenance": {
            "files": list(CANARY_CODE_PROVENANCE_FILES),
            "code_bundle_sha256": canary_code_bundle_sha256(root),
        },
        # Cell-granular, not call-granular: see the module docstring.
        "resume_granularity": "cell",
        "execution_authorized": False,
    }
    manifest["execution_identity_sha256"] = canonical_sha256(manifest)
    return manifest


def validate_canary_manifest(manifest: Mapping[str, Any], *,
                             project_root: str | Path = ".") -> dict[str, Any]:
    """Re-derive every binding from the real artifacts and refuse on any mismatch."""
    if not isinstance(manifest, Mapping):
        raise ManifestValidationError("manifest must be a mapping")
    keys = set(manifest)
    if keys != MANIFEST_TOP_LEVEL_KEYS:
        raise ManifestValidationError(
            f"canary manifest fields drifted: unexpected {sorted(keys - MANIFEST_TOP_LEVEL_KEYS)!r}, "
            f"missing {sorted(MANIFEST_TOP_LEVEL_KEYS - keys)!r}")
    if manifest["stage"] != STAGE:
        raise ManifestValidationError(f"stage must be {STAGE!r}")
    if manifest["execution_authorized"] is not False:
        raise ManifestValidationError(
            "a manifest must never authorize itself; authorization lives in its own record")

    rebuilt = build_canary_manifest(
        project_root=project_root, recorded_at_utc=manifest["recorded_at_utc"],
        archive_dir=manifest["ledger"]["archive_dir"])
    for section in ("frozen_inputs", "anchor", "code_provenance", "planning", "governance"):
        if manifest[section] != rebuilt[section]:
            raise ManifestValidationError(
                f"{section} does not match the artifacts on disk; the manifest is stale or "
                "an input drifted")

    without_identity = {k: v for k, v in manifest.items()
                        if k != "execution_identity_sha256"}
    expected = canonical_sha256(without_identity)
    if manifest["execution_identity_sha256"] != expected:
        raise ManifestValidationError(
            f"execution identity does not match the manifest it names: expected {expected}")
    return dict(manifest)
