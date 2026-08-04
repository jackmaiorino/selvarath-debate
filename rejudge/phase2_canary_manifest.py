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
from rejudge.phase2_canary_compose import load_no_query_transition
from rejudge.phase2_execution import canonical_sha256

STAGE = "canary"
STAGE_CAP_USD = 40.0
REVIEWER_MODEL = "claude-fable-5"
# The reviewer was substituted mid-canary on 2026-08-01 under an owner deviation, because the
# pinned model's quota is unavailable. The manifest must describe the run as it actually is,
# so the reviewer identity is read from that append-only record when it exists rather than
# from the constant above. Its presence necessarily changes the execution identity, which is
# the point: a different gate is a different run.
REVIEWER_SUBSTITUTION_RELATIVE_PATH = Path(
    "rejudge/phase2_canary_reviewer_substitution_2026-08-01.json")
REVIEWER_SUBSTITUTION_SCHEMA = "phase2_canary_reviewer_substitution_v1"
# The transport pins were amended the same way on 2026-08-01, and for the same reason that the
# reviewer identity above is resolved rather than hardcoded. Together's gemma endpoint began
# returning nothing at all on roughly a third of calls, and v5's 600s read timeout spent ten
# minutes on each one before abandoning it. Shortening that changes no science, but the v5
# hash is manifest-bound, so the artifact this run actually used is read from an append-only
# record: a different transport is a different run, and must show up as a different identity.
ROLE_LIMITS_FALLBACK_RELATIVE_PATH = Path("rejudge/phase2_role_limits_v5_2026-07-19.json")
ROLE_LIMITS_AMENDED_RELATIVE_PATH = Path("rejudge/phase2_role_limits_v6_2026-08-01.json")
TRANSPORT_AMENDMENT_RELATIVE_PATH = Path(
    "rejudge/phase2_canary_transport_amendment_2026-08-01.json")
TRANSPORT_AMENDMENT_SCHEMA = "phase2_canary_transport_amendment_v1"
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

MANIFEST_TOP_LEVEL_KEYS_V1 = frozenset({
    "schema_version", "stage", "recorded_at_utc", "planning", "frozen_inputs", "reviewer",
    "anchor", "caps", "ledger", "governance", "code_provenance", "resume_granularity",
    "execution_authorized", "execution_identity_sha256",
})
# v2 adds the execution block. The canary that ran serially had nothing to say here: one cell
# at a time is not a choice, it is the absence of one. Concurrency is a choice, it decides how
# much load lands on the model carrying almost all of it and how conditions are interleaved in
# time, and everything that shapes the run is hash-bound rather than passed on a command line.
MANIFEST_TOP_LEVEL_KEYS_V2 = MANIFEST_TOP_LEVEL_KEYS_V1 | {"execution"}
MANIFEST_TOP_LEVEL_KEYS = MANIFEST_TOP_LEVEL_KEYS_V2
SCHEMA_V1 = "phase2_canary_execution_manifest_v1"
SCHEMA_V2 = "phase2_canary_execution_manifest_v2"
_KEYS_BY_SCHEMA = {SCHEMA_V1: MANIFEST_TOP_LEVEL_KEYS_V1, SCHEMA_V2: MANIFEST_TOP_LEVEL_KEYS_V2}

MISSING_DATA_POLICY_RELATIVE_PATH = Path(
    "rejudge/phase2_missing_data_policy_proposal_2026-08-04.json")

# The ramp exists because the canary measured gemma at concurrency ONE and nothing above it.
# It abandoned 189 of 1,702 calls (11%) at that width while carrying 95% of all call time, so
# every higher width is an extrapolation. Each rung runs a slice of the real plan and is
# promoted only on measured evidence; there is no separate throwaway probe, which would be
# unmanifested spend.
CONCURRENCY_RAMP_STEPS: tuple[dict[str, Any], ...] = (
    {"cells": 40, "model_caps": {"google/gemma-4-31B-it": 1}},
    {"cells": 40, "model_caps": {"google/gemma-4-31B-it": 2}},
    {"cells": 40, "model_caps": {"google/gemma-4-31B-it": 4}},
    {"cells": 40, "model_caps": {"google/gemma-4-31B-it": 8}},
)

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
    # Validated load, not _json: the loader re-checks the artifact's declared UTF-8 hash, so a
    # tampered payload refuses here instead of binding a plausible-looking hash.
    no_query = load_no_query_transition(root)
    role_limits = resolve_role_limits(root)

    bindings: dict[str, Any] = {
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
        # Consult #28: the successor manifest binds both the decision artifact (below, under
        # governance) and the exact judge-visible payload bytes.
        "no_query_payload_sha256": no_query["payload"]["utf8_sha256"],
        # Resolved, not constant: see resolve_role_limits and the 2026-08-01 amendment.
        "role_limits_sha256": role_limits["sha256"],
        "role_limits_tracked_path": role_limits["tracked_path"],
        "price_snapshot_sha256": canonical_sha256(
            _json(root / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json")),
    }
    if role_limits["amended"]:
        bindings["transport_amendment_sha256"] = role_limits["amendment_sha256"]
    return bindings


def resolve_role_limits(root: Path) -> dict[str, Any]:
    """The role-limits artifact this run actually used, plus the record that amends it.

    The fallback is not a formality. With the amendment record absent this returns the frozen
    v5 pin, which is what makes the amendment legible as a deviation from a still-existing
    baseline rather than as the new normal.
    """
    fallback = str(ROLE_LIMITS_FALLBACK_RELATIVE_PATH).replace("\\", "/")
    superseded = canonical_sha256(_json(root / ROLE_LIMITS_FALLBACK_RELATIVE_PATH))
    record_path = root / TRANSPORT_AMENDMENT_RELATIVE_PATH
    if not record_path.exists():
        return {"tracked_path": fallback, "sha256": superseded, "amended": False}

    record = _json(record_path)
    if record.get("schema_version") != TRANSPORT_AMENDMENT_SCHEMA:
        raise ManifestValidationError("transport amendment record schema drifted")
    if record.get("execution_authorized") is not False:
        raise ManifestValidationError(
            "a transport amendment record must grant no execution authority")
    if record.get("classification", {}).get("science_affected") is not False:
        raise ManifestValidationError(
            "a transport amendment must declare itself science-neutral; a science change "
            "belongs in a protocol amendment, not a transport one")

    successor_path = root / ROLE_LIMITS_AMENDED_RELATIVE_PATH
    if not successor_path.exists():
        raise ManifestValidationError(
            "the transport amendment names a successor artifact that is not on disk")
    successor = _json(successor_path)
    # Refuse a successor that does not name the exact artifact it claims to replace. Without
    # this the chain could be re-pointed at any role-limits file that happened to parse.
    if successor.get("supersedes", {}).get("canonical_sha256") != superseded:
        raise ManifestValidationError(
            "the successor role-limits artifact does not name the frozen pin it supersedes")
    return {
        "tracked_path": str(ROLE_LIMITS_AMENDED_RELATIVE_PATH).replace("\\", "/"),
        "sha256": canonical_sha256(successor),
        "amended": True,
        "superseded_tracked_path": fallback,
        "superseded_sha256": superseded,
        "amendment_tracked_path": str(TRANSPORT_AMENDMENT_RELATIVE_PATH).replace("\\", "/"),
        "amendment_sha256": canonical_sha256(record),
    }


def resolve_reviewer(root: Path) -> dict[str, Any]:
    """The reviewer identity this run actually used, plus the record that authorises it."""
    path = root / REVIEWER_SUBSTITUTION_RELATIVE_PATH
    if not path.exists():
        return {"model": REVIEWER_MODEL, "substituted": False}
    record = _json(path)
    if record.get("schema_version") != REVIEWER_SUBSTITUTION_SCHEMA:
        raise ManifestValidationError("reviewer substitution record schema drifted")
    if record.get("execution_authorized") is not False:
        raise ManifestValidationError(
            "a reviewer substitution record must grant no execution authority")
    reviewer = record["reviewer"]
    if not reviewer.get("model") or not reviewer.get("reasoning_effort"):
        raise ManifestValidationError(
            "reviewer substitution must state both model and reasoning_effort")
    return {"model": str(reviewer["model"]),
            "reasoning_effort": str(reviewer["reasoning_effort"]),
            "substituted": True,
            "substitution_tracked_path": str(REVIEWER_SUBSTITUTION_RELATIVE_PATH).replace(
                "\\", "/"),
            "substitution_sha256": canonical_sha256(record),
            "superseded_model": REVIEWER_MODEL}


def _governance(root: Path) -> dict[str, Any]:
    """The artifacts that make this run the run they describe."""
    paths = {
        "claude_gate_amendment": "rejudge/phase2_canary_claude_gate_amendment_2026-07-23.json",
        "governance_ratification": "rejudge/phase2_canary_governance_ratification_2026-07-23.json",
        "owner_decision": "rejudge/phase2_canary_owner_decision_2026-07-23.json",
        "build_decisions": "rejudge/phase2_canary_build_decisions_2026-07-24.json",
        "checker_frozen_config": "rejudge/phase2_checker_frozen_config_2026-07-23.json",
        # Supersedes the PROVISIONAL no_query_transition_text entry inside build_decisions
        # (Consult #28); both stay bound because the record is append-only.
        "no_query_transition": "rejudge/phase2_no_query_transition_2026-07-26.json",
    }
    # Conditional for the same reason the reviewer block is: with the record absent this run
    # is the unamended one, and the manifest must not claim a governance artifact it lacks.
    if (root / TRANSPORT_AMENDMENT_RELATIVE_PATH).exists():
        paths["transport_amendment"] = str(TRANSPORT_AMENDMENT_RELATIVE_PATH).replace(
            "\\", "/")
    # Bound here, before the run, for the same reason as everything else in this block: a
    # disposition for incomplete cells chosen once the outcomes are visible is not a policy,
    # and binding its hash is what makes "decided in advance" checkable rather than asserted.
    if (root / MISSING_DATA_POLICY_RELATIVE_PATH).exists():
        paths["missing_data_policy"] = str(MISSING_DATA_POLICY_RELATIVE_PATH).replace(
            "\\", "/")
    return {name: {"tracked_path": relative,
                   "canonical_sha256": canonical_sha256(_json(root / relative))}
            for name, relative in sorted(paths.items())}


def _validate_execution(execution: Any, *, checker_model: str) -> None:
    """Refuse an execution block that cannot be acted on as written.

    Every check here is a way the block could look complete while leaving the run's actual
    width undetermined, which would put the load somewhere the manifest does not record.
    """
    if not isinstance(execution, Mapping):
        raise ManifestValidationError("execution must be a mapping")
    for field in ("max_workers", "block_size"):
        value = execution.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ManifestValidationError(f"execution.{field} must be a positive integer")
    caps = execution.get("model_caps")
    if not isinstance(caps, Mapping) or not caps:
        raise ManifestValidationError("execution.model_caps must be a non-empty mapping")
    if checker_model not in caps:
        raise ManifestValidationError(
            f"execution.model_caps must cap {checker_model!r}: it serves the frozen checker "
            "and a judge role, and carried 95% of the canary's call time")
    for model, limit in caps.items():
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ManifestValidationError(f"execution.model_caps[{model!r}] must be >= 1")
    if not str(execution.get("block_scheduling") or "").strip():
        raise ManifestValidationError("execution.block_scheduling must be recorded")

    ramp = execution.get("concurrency_ramp")
    if not isinstance(ramp, Mapping):
        raise ManifestValidationError("execution.concurrency_ramp must be a mapping")
    steps = ramp.get("steps")
    if not isinstance(steps, list) or len(steps) < 2:
        raise ManifestValidationError("a concurrency ramp needs at least two rungs")
    widths = []
    for step in steps:
        if not isinstance(step, Mapping):
            raise ManifestValidationError("each ramp step must be a mapping")
        cells = step.get("cells")
        if not isinstance(cells, int) or isinstance(cells, bool) or cells < 1:
            raise ManifestValidationError("each ramp step must run at least one cell")
        step_caps = step.get("model_caps")
        if not isinstance(step_caps, Mapping) or checker_model not in step_caps:
            raise ManifestValidationError(
                f"each ramp step must set a cap for {checker_model!r}; a ramp that does "
                "not move the model carrying the load measures nothing")
        widths.append(step_caps[checker_model])
    if widths != sorted(widths):
        raise ManifestValidationError("a ramp must escalate monotonically, never jump around")
    if widths[0] != 1:
        raise ManifestValidationError(
            "the first rung must be concurrency 1, the only width the canary actually measured")
    if widths[-1] > caps[checker_model]:
        raise ManifestValidationError(
            "the ramp must not exceed the settled cap it is choosing among")
    if sum(step["cells"] for step in steps) >= 945:
        raise ManifestValidationError("the ramp must be a prefix of the run, not the whole run")
    for rule in ("promotion_rule", "abort_rule"):
        if not str(ramp.get(rule) or "").strip():
            raise ManifestValidationError(
                f"concurrency_ramp.{rule} must be stated: a ramp with no rule for stopping "
                "is a warm-up, not a measurement")


def build_canary_manifest(*, project_root: str | Path = ".", recorded_at_utc: str,
                          archive_dir: str) -> dict[str, Any]:
    """Assemble the manifest. Deterministic apart from the two caller-supplied fields."""
    root = Path(project_root)
    anchor_approval_path = (
        "rejudge/phase2_anchor_parser_policy_approval_2026-07-24.json")

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_V2,
        "stage": STAGE,
        "recorded_at_utc": recorded_at_utc,
        "planning": {
            "cell_count": 945,
            "cell_key_namespace": _json(
                root / "rejudge" / "phase2_protocol.json")["cell_key_namespace"],
        },
        "frozen_inputs": _frozen_inputs(root),
        "reviewer": {
            **resolve_reviewer(root),
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
        "execution": {
            "max_workers": 8,
            "block_size": 16,
            # The ceiling each model may reach once the ramp has settled. gemma is listed at
            # the ramp's top rung and is the only one that gets there by measurement; the rest
            # were never the constraint (Qwen, Llama and gpt-oss together were 0.89 h of the
            # canary's 13.69 h of call time) and are capped at the worker count.
            "model_caps": {
                "google/gemma-4-31B-it": 8,
                "meta-llama/Llama-3.3-70B-Instruct-Turbo": 8,
                "openai/gpt-oss-120b": 8,
                "Qwen/Qwen2.5-7B-Instruct-Turbo": 8,
                "Qwen/Qwen3.7-Plus": 8,
            },
            "block_scheduling": (
                "condition-balanced round robin over ready cells, readiness recomputed each "
                "block, results applied in block order rather than completion order. Batch "
                "cells depend on their sequential parents and so always run later; a "
                "work-conserving queue would give batch a systematically later slice of the "
                "run and land time-varying provider degradation preferentially on one of the "
                "two co-primary components."),
            "concurrency_ramp": {
                "steps": list(CONCURRENCY_RAMP_STEPS),
                "measured_baseline": (
                    "gemma abandoned 189 of 1,702 calls (11.1%) at concurrency 1 during the "
                    "2026-07-28 canary, while carrying 95% of all call time"),
                "promotion_rule": (
                    "promote to the next rung only if, over that rung's cells, gemma's "
                    "abandoned-call rate is at most 15% and no cell halted terminally. The "
                    "tolerance is the measured 11.1% baseline plus headroom, so a rung is "
                    "promoted on evidence of no degradation rather than on absence of "
                    "catastrophe."),
                "abort_rule": (
                    "on failing the promotion rule, pin the last rung that passed and run the "
                    "remaining cells there. Failing the FIRST rung is not a cap question at "
                    "all: it means the provider is degraded relative to the canary, and the "
                    "run stops for a human rather than ramping down into a slow bad run."),
                "spend_note": (
                    "the ramp is a prefix of the real 945-cell plan, not a synthetic probe. "
                    "Its cells are recorded and count toward the run, so it adds no "
                    "unmanifested spend and nothing is paid for twice."),
            },
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
    # Both schemas are accepted, and deliberately so: the v1 manifests of the completed
    # 2026-07-28 canary are historical records that must stay loadable and verifiable. Which
    # key set applies is decided by the manifest's own declared schema, so a v2 manifest cannot
    # quietly omit the execution block by claiming to be v1 without also failing the stage and
    # provenance checks below.
    expected_keys = _KEYS_BY_SCHEMA.get(str(manifest.get("schema_version")))
    if expected_keys is None:
        raise ManifestValidationError(
            f"unknown canary manifest schema {manifest.get('schema_version')!r}; "
            f"expected one of {sorted(_KEYS_BY_SCHEMA)!r}")
    if keys != expected_keys:
        raise ManifestValidationError(
            f"canary manifest fields drifted: unexpected {sorted(keys - expected_keys)!r}, "
            f"missing {sorted(expected_keys - keys)!r}")
    if manifest["schema_version"] == SCHEMA_V2:
        _validate_execution(
            manifest["execution"],
            # Checked against the manifest's OWN declared checker rather than a constant,
            # so a ramp that stops moving the model carrying the load fails loudly instead
            # of silently measuring a model that was never the constraint.
            checker_model=str(manifest["frozen_inputs"]["checker_model"]))
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
