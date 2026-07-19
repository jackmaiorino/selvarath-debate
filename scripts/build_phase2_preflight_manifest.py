"""Build the REAL capability-preflight execution manifest + authorization record.

Produces (canonical JSON, meant to be committed):

* ``rejudge/phase2_preflight_manifest_2026-07-19.json``
* ``rejudge/phase2_preflight_authorization_2026-07-19.json``
* ``rejudge/phase2_capability_preflight_seed_derivation_2026-07-19.json`` (see KNOWN DEVIATION
  below)

Run with::

    uv run python scripts/build_phase2_preflight_manifest.py

Everything this script binds is either a real, already-committed, git-tracked repo artifact
(protocol, combined AI audit + A1 amendment, prompt bundle + its owner-methods approval,
provider price snapshot, role-limits v4, the 2026-07-19 preflight delegation, provider refresh,
provider reconciliation, the Gemma recovery closure, uv.lock, the real storage policy) or a value
this script derives from those artifacts by calling the SAME frozen validation/derivation code
``rejudge.phase2_execution.validate_execution_manifest`` will itself call at manifest-validation
time (``rejudge.phase2_plan.enumerate_cells``, ``rejudge.phase2_capability_corpus``,
``rejudge.phase2_role_limits.resolve_request_parameters``, and -- critically, for
``request_fields_sha256`` -- ``rejudge.api_client.RejudgeClient``'s own private
``_resolve_max_tokens``/``_build_request_kwargs`` methods plus its module-level
``_canonical_json`` hashing helper, never reimplemented here; see
:func:`compute_request_fields_sha256`). Nothing here can itself execute a provider call: this
module never imports the real ``together`` SDK and never calls ``RejudgeClient.complete()``.

This is the successor to the disposable rehearsal builder
(``rehearsal_common.py``, not part of this repo) that removed its two placeholders:
``request_fields_sha256`` is now the byte-exact hash the production client will itself produce
(not ``"a" * 64``), and ``seed`` is now derived from the frozen protocol seed policy (not the
call's list index).

--- KNOWN DEVIATION: seed_derivation is NOT a manifest top-level field -----------------------

The task that produced this script asked for the frozen seed-derivation formula to be "recorded
in the manifest as seed_derivation". ``rejudge.phase2_execution.MANIFEST_TOP_LEVEL_KEYS`` is an
exact, frozen key set (``validate_execution_manifest`` calls ``_exact_keys(manifest,
MANIFEST_TOP_LEVEL_KEYS, ...)`` and fails closed -- "execution manifest fields drifted" -- on any
extra top-level key), and it has no ``seed_derivation`` slot. Modifying that frozen validator to
add one would itself be a change to the reviewed control-plane's validation surface, which this
script must not do. Instead, the formula is frozen here as a documented constant/function
(:data:`SEED_DERIVATION_FORMULA_DOCSTRING`, :func:`derive_capability_qa_seed`) and recorded, for
audit purposes only, in a separate sidecar artifact
(``rejudge/phase2_capability_preflight_seed_derivation_2026-07-19.json``) that is NOT bound or
hashed into the execution manifest and grants no execution authority. Reported, not improvised
around; see this script's caller-facing summary output for a restatement of this deviation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rejudge import api_client  # noqa: E402
from rejudge import phase2_capability_corpus as capability_corpus  # noqa: E402
from rejudge import phase2_execution as pe  # noqa: E402
from rejudge import phase2_plan  # noqa: E402
from rejudge import phase2_preflight_forecast as preflight_forecast  # noqa: E402
from rejudge import phase2_prompt_bundle as prompt_bundle  # noqa: E402
from rejudge import phase2_provider_price_snapshot as price_snapshot  # noqa: E402
from rejudge import phase2_resolvability_ai_review as ai_review  # noqa: E402
from rejudge import phase2_role_limits as role_limits  # noqa: E402


# --- frozen constants ----------------------------------------------------------------------

# The exact, already-reviewed commit this manifest's implementation_provenance.git_commit binds.
# The builder REFUSES to run against any other HEAD (see assert_clean_git_state): rebinding to a
# newer commit is a deliberate act (update this constant after re-reviewing that commit), not an
# automatic side effect of running the script again.
EXPECTED_GIT_COMMIT = "43e568df27508e7a8239a614ffff11a96d966abb"

MANIFEST_RELATIVE_PATH = "rejudge/phase2_preflight_manifest_2026-07-19.json"
AUTHORIZATION_RELATIVE_PATH = "rejudge/phase2_preflight_authorization_2026-07-19.json"
SEED_DERIVATION_RELATIVE_PATH = (
    "rejudge/phase2_capability_preflight_seed_derivation_2026-07-19.json")
OUTPUT_RELATIVE_PATHS: frozenset[str] = frozenset({
    MANIFEST_RELATIVE_PATH, AUTHORIZATION_RELATIVE_PATH, SEED_DERIVATION_RELATIVE_PATH,
})
# This script's own path plus its test file: implementation_provenance.git_commit must bind the
# ALREADY-REVIEWED commit (EXPECTED_GIT_COMMIT), so this builder necessarily runs BEFORE that
# commit gains a child commit adding the builder + tests themselves -- both therefore
# legitimately show up as untracked new files at build time, exactly like the three generated
# artifacts above. "The tree must stay clean apart from your new files" (this whole deliverable),
# not merely apart from the three files main() itself writes.
SCRIPT_RELATIVE_PATH = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
TEST_RELATIVE_PATH = "tests/test_build_phase2_preflight_manifest.py"
ALLOWED_DIRTY_RELATIVE_PATHS: frozenset[str] = OUTPUT_RELATIVE_PATHS | frozenset({
    SCRIPT_RELATIVE_PATH, TEST_RELATIVE_PATH,
})

# C: is critically low on disk; every byte of run data (ledger, results, completion, error, and
# abort records -- all derived by rejudge.phase2_preflight_runner as siblings of the ledger path)
# must live on E:. The ledger's own binding is deliberately-anywhere (phase2_execution.py never
# filesystem-checks manifest.ledger.path), so an absolute E: path is a legitimate manifest value,
# not a bypass of any check.
LEDGER_ARCHIVE_DIR = "E:/selvarath-archive/capability-preflight"
LEDGER_PATH = f"{LEDGER_ARCHIVE_DIR}/phase2_capability_preflight_ledger.jsonl"
LEDGER_IDENTITY_LABEL = "phase2-project-wide-ledger-v1"

STAGE_CAP_USD = 15.0
CUMULATIVE_CAP_USD = 1500.0

CAPABILITY_CONDITION_ID = "full_document_solo_qa"

# rejudge/phase2_role_limits_v4_2026-07-19.json has no repo-relative-string constant of its own
# in phase2_role_limits.py (only an absolute DEFAULT_V4_ARTIFACT_PATH); derive it once, robustly,
# rather than hardcoding a second copy of the filename.
ROLE_LIMITS_V4_RELATIVE_PATH = (
    role_limits.DEFAULT_V4_ARTIFACT_PATH.resolve().relative_to(REPO_ROOT.resolve()).as_posix())
# Same story for the v5 (2026-07-19 r2-incident transport hardening) artifact: streaming pin
# extended to all three reasoning models, SDK-internal retries pinned to 0, an explicit
# http_timeout, and a new application-level per-call wall-clock ceiling -- see
# rejudge/phase2_role_limits.py's own v5 section docstring. The MERGED
# role_limits_and_request_settings_artifact manifest slot is now v5-only (phase2_execution.py's
# validate_execution_manifest rejects v4/v3/v2/v1 there); the frozen v3 READY forecast schema
# (FORECAST_R2_RELATIVE_PATH below) stays bound to the real v4 artifact regardless -- see
# phase2_execution.py's own merged-slot validation block for why that split is deliberate and
# unaffected by this transport-only fix.
ROLE_LIMITS_V5_RELATIVE_PATH = (
    role_limits.DEFAULT_V5_ARTIFACT_PATH.resolve().relative_to(REPO_ROOT.resolve()).as_posix())
# Same story for the v2 "ready" forecast artifact.
FORECAST_RELATIVE_PATH = (
    preflight_forecast.DEFAULT_ARTIFACT_V2_PATH.resolve()
    .relative_to(REPO_ROOT.resolve()).as_posix())
# storage_policy has no DEFAULT_*_RELATIVE_PATH constant in phase2_execution.py (its binding is
# manifest-controlled, not pinned -- see _validate_storage_policy_gate); this is simply where the
# one real, tracked policy record lives.
STORAGE_POLICY_RELATIVE_PATH = "rejudge/phase2_storage_policy_2026-07-18.json"

# =================================================================================================
# RELAUNCH ATTEMPT r2: rebuilds the manifest + authorization for a fresh run directory, bound to
# the v3 (role-limits-v4) forecast and the frozen prior-attempt closure, with a PENDING
# authorization template (never auto-authorized off the old blanket delegation alone -- see
# ``build_authorization_template_r2``).
# =================================================================================================

MANIFEST_RELATIVE_PATH_R2 = "rejudge/phase2_preflight_manifest_2026-07-19-r2.json"
AUTHORIZATION_RELATIVE_PATH_R2 = "rejudge/phase2_preflight_authorization_2026-07-19-r2.json"
REAUTHORIZATION_ASK_RELATIVE_PATH_R2 = (
    "rejudge/phase2_preflight_reauthorization_ask_2026-07-19-r2.json")

# A genuinely fresh archive directory: the prior (r1) attempt's ledger/results/errors/abort files
# live under LEDGER_ARCHIVE_DIR (E:/selvarath-archive/capability-preflight) and are NEVER reused
# or written into by a relaunch -- see assert_r2_run_directory_absent.
LEDGER_ARCHIVE_DIR_R2 = "E:/selvarath-archive/capability-preflight-r2"
LEDGER_PATH_R2 = f"{LEDGER_ARCHIVE_DIR_R2}/phase2_capability_preflight_ledger.jsonl"

# Every sibling output path rejudge.phase2_preflight_runner derives from the ledger path (see its
# _results_path/_completion_path/_error_log_path/_abort_path, all Path.with_name/_sibling_path
# derivations off the same ledger.path). A relaunch manifest must bind a directory none of these
# already exist in -- reusing r1's directory (or a stale partial r2 attempt) would risk a resume
# audit silently treating old output as this new identity's own.
R2_SIBLING_FILENAMES: tuple[str, ...] = (
    "phase2_capability_preflight_ledger.jsonl",
    "phase2_capability_preflight_results.jsonl",
    "phase2_capability_preflight_completion.json",
    "phase2_capability_preflight_errors.jsonl",
    "phase2_capability_preflight_abort.json",
)

# rejudge/phase2_preflight_forecast_2026-07-19-r2.json has no repo-relative-string constant of
# its own in phase2_preflight_forecast.py (only an absolute DEFAULT_ARTIFACT_V3_PATH); derive it
# once, robustly, rather than hardcoding a second copy of the filename.
FORECAST_R2_RELATIVE_PATH = (
    preflight_forecast.DEFAULT_ARTIFACT_V3_PATH.resolve()
    .relative_to(REPO_ROOT.resolve()).as_posix())
PRIOR_ATTEMPT_CLOSURE_RELATIVE_PATH = pe.DEFAULT_PRIOR_ATTEMPT_CLOSURE_RELATIVE_PATH.as_posix()

REAUTHORIZATION_PENDING_MARKER_KEY = "reauthorization_pending"

REAUTHORIZATION_ASK_TEMPLATE = (
    "Approve relaunch of the capability preflight under execution identity {identity}, "
    "role-limits v4 {v4_sha}, and the existing $15 cap. V4 only removes the SDK-unsupported "
    "stream_options field while retaining stream: true; models, prompts, seeds, token limits, "
    "three-attempt ceiling, scope, and canary/main exclusions are unchanged."
)


def build_reauthorization_ask_text(*, execution_identity_sha256: str, role_limits_v4_sha256: str) -> str:
    """The exact, one-line reauthorization ask text for the delegated owner (Jack Maiorino)."""
    return REAUTHORIZATION_ASK_TEMPLATE.format(
        identity=execution_identity_sha256, v4_sha=role_limits_v4_sha256)


def assert_r2_run_directory_absent(archive_dir: str = LEDGER_ARCHIVE_DIR_R2) -> None:
    """Refuse to proceed unless every sibling output path under ``archive_dir`` is ABSENT.

    A relaunch manifest must bind a genuinely fresh run directory: this is a pure filesystem
    check (independent of manifest validation itself, which never filesystem-checks
    ``ledger.path`` -- see ``phase2_execution``'s deliberately-anywhere ledger binding), run
    BEFORE any artifact is written, so a stale or reused directory halts the build immediately.
    """
    directory = Path(archive_dir)
    if not directory.is_dir():
        return
    existing = [name for name in R2_SIBLING_FILENAMES if (directory / name).exists()]
    if existing:
        raise BuilderRefusedError(
            f"refusing to build the r2 manifest: run directory {archive_dir!r} already contains "
            f"sibling output path(s) {existing!r}. A relaunch manifest must bind a genuinely "
            "fresh run directory, never one a prior attempt (or a stale partial relaunch) "
            "already wrote into.")


def assert_exactly_qwen_request_hashes_changed(
    old_manifest: dict[str, Any], new_entries: list[dict[str, Any]],
) -> dict[str, int]:
    """Assert the r2 request-hash regeneration changed EXACTLY the 212 Qwen/Qwen3.7-Plus entries.

    Removing ``stream_options`` from the streaming transport only changes the request-kwargs
    shape (and therefore ``request_fields_sha256``) for the one model in
    ``STREAMING_PINNED_MODELS_V4`` (Qwen/Qwen3.7-Plus); every other of the 1,060 capability_qa
    calls must be byte-identical to the old (r1, role-limits-v3-bound) manifest's own
    ``request_fields_sha256``. Fails closed (not merely a warning) if any OTHER model's hash
    changed, if Qwen's hash failed to change, or if the changed/unchanged counts are not exactly
    212/848.
    """
    old_by_key = {
        str(entry["planning_cell_key"]): entry
        for entry in old_manifest["provider_call_inventory"]
    }
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for entry in new_entries:
        key = str(entry["planning_cell_key"])
        old_entry = old_by_key.get(key)
        if old_entry is None:
            raise BuilderRefusedError(
                f"planning_cell_key {key!r} is present in the new (r2) inventory but absent "
                "from the old (r1) manifest; the two inventories must cover an identical cell "
                "set for this comparison to be meaningful")
        if old_entry["model"] != entry["model"]:
            raise BuilderRefusedError(
                f"planning_cell_key {key!r} is bound to a different model across r1/r2 "
                f"({old_entry['model']!r} vs {entry['model']!r}); the cell-to-model assignment "
                "must be identical across a relaunch")
        if old_entry["request_fields_sha256"] != entry["request_fields_sha256"]:
            changed.append(entry)
        else:
            unchanged.append(entry)

    changed_models = {str(entry["model"]) for entry in changed}
    if changed_models != {"Qwen/Qwen3.7-Plus"}:
        raise BuilderRefusedError(
            "request_fields_sha256 changed for model(s) other than exactly "
            f"{{'Qwen/Qwen3.7-Plus'}}: observed changed_models={sorted(changed_models)!r} "
            f"({len(changed)} changed entries)")
    if len(changed) != 212 or len(unchanged) != 848:
        raise BuilderRefusedError(
            "expected exactly 212 changed / 848 unchanged request_fields_sha256 entries (the "
            f"Qwen/Qwen3.7-Plus stream_options removal), observed {len(changed)} changed / "
            f"{len(unchanged)} unchanged")
    return {"changed": len(changed), "unchanged": len(unchanged)}


def build_authorization_template_r2(
    *, execution_identity_sha256: str, stage_cap_usd: float, cumulative_cap_usd: float,
    recorded_at_utc: str,
) -> dict[str, Any]:
    """The r2 (relaunch) authorization record TEMPLATE: pending reauthorization.

    Reuses the SAME delegation basis, approver, and ``approved_at_utc`` as the frozen 2026-07-19
    preflight delegation (the delegation itself is unedited and still real), but is deliberately
    NOT a valid :data:`rejudge.phase2_execution.AUTHORIZATION_KEYS`-shaped record: the extra
    ``reauthorization_pending`` key makes
    ``pe.validate_execution_manifest(require_authorized=True)`` refuse it via the same
    ``_exact_keys`` "fields drifted" check every other schema drift in this codebase fails closed
    on (see ``pe._exact_keys``). This manifest's execution identity differs materially from the
    one the original delegation covered (new code, new run directory, a new required
    prior-attempt-closure binding, a new forecast) -- reusing the old blanket delegation to
    silently authorize a materially different execution would defeat the point of binding
    authorization to a specific, re-derived identity. It becomes a real, accepted authorization
    only once Jack explicitly reauthorizes (see :func:`build_reauthorization_ask_text`) and the
    pending marker is removed; no script in this repository promotes this template to a real
    authorization automatically.
    """
    delegation_raw = (REPO_ROOT / pe.DEFAULT_PREFLIGHT_DELEGATION_RELATIVE_PATH).read_bytes()
    return {
        "execution_identity_sha256": execution_identity_sha256,
        "stage": pe.STAGE_CAPABILITY_PREFLIGHT,
        "stage_cap_usd": float(stage_cap_usd),
        "cumulative_cap_usd": float(cumulative_cap_usd),
        "approver": pe.PREFLIGHT_DELEGATION_APPROVER,
        "approved_at_utc": pe.PREFLIGHT_DELEGATION_APPROVED_AT_UTC,
        "recorded_at_utc": recorded_at_utc,
        "approval_basis_tracked_path": pe.DEFAULT_PREFLIGHT_DELEGATION_RELATIVE_PATH.as_posix(),
        "approval_basis_sha256": hashlib.sha256(delegation_raw).hexdigest(),
        REAUTHORIZATION_PENDING_MARKER_KEY: True,
    }


class BuilderRefusedError(RuntimeError):
    """The builder refuses to build or write artifacts; see the message for why."""


# --- SEED DERIVATION: frozen formula (documented constant + docstring) ----------------------

SEED_DERIVATION_FORMULA_DOCSTRING = (
    "seed = int.from_bytes(sha256(canonical_json({'namespace': protocol.cell_key_namespace, "
    "'question_id': question_id, 'model': model, 'condition': condition, "
    "'replicate_index': replicate_index, 'call_role': 'capability_qa'})).digest()[:4], 'big')"
)


def derive_capability_qa_seed(
    *, namespace: str, question_id: str, model: str, condition: str, replicate_index: int,
) -> int:
    """FROZEN capability_qa seed derivation formula. Never change without a fresh manifest.

    Per the frozen protocol's ``decisions.execution_semantics.seed_policy`` ("include protocol
    namespace, question, debater, transcript, judge, condition, replicate, call role, and
    attempt"): capability_qa calls have no debater or transcript dimension (no debate transcript
    is ever produced or judged for this stage), so those two policy dimensions are inapplicable
    rather than omitted by oversight. ``judge_model`` fills the policy's "judge" dimension (named
    ``model`` here, since a capability_qa call has no separate debater to judge). Attempt-level
    variation is deliberately NOT folded into the seed: per the frozen
    ``transport_retry_policy`` ("repeat identical request and seed"), every transport retry of
    one call reuses the exact same seed (and therefore the exact same ``request_fields_sha256``)
    as its first attempt -- attempt number is the transport layer's concern, not the call
    identity's.

    Formula (also frozen verbatim in :data:`SEED_DERIVATION_FORMULA_DOCSTRING`, and recorded --
    for audit only, never bound into the execution manifest; see this module's KNOWN DEVIATION
    section -- in the seed_derivation sidecar artifact this script writes)::

        seed = int.from_bytes(
            sha256(canonical_json({
                "namespace": namespace, "question_id": question_id, "model": model,
                "condition": condition, "replicate_index": replicate_index,
                "call_role": "capability_qa",
            })).digest()[:4], "big")

    ``canonical_json`` here is this project's usual canonical-JSON convention (``ensure_ascii=
    True``, ``sort_keys=True``, compact ``(",", ":")`` separators -- matching
    ``rejudge.phase2_plan.canonical_sha256``). The result is a 32-bit non-negative integer
    (``0 <= seed < 2**32``); provider seed fields are int32-safe.
    """
    payload = {
        "namespace": namespace,
        "question_id": question_id,
        "model": model,
        "condition": condition,
        "replicate_index": replicate_index,
        "call_role": pe.CAPABILITY_CALL_ROLE,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


# --- REQUEST-FIELDS HASH: reuse api_client's own kwargs-construction + hashing, never reimplement

# Never dereferenced: the hash-only client below is constructed strictly enough
# (dry_run=False, strict_model_pricing=True, require_explicit_reasoning_max_tokens=True,
# strict_context_mode=True, halt_on_unknown_charge=True -- every production-mode flag
# build_production_client_factory sets) that api_client.RejudgeClient's own constructor
# guards are satisfied by "an SDK object was supplied", but this script never calls
# .complete()/.chat.completions.create(...) on it, so the placeholder is never touched.
_UNUSED_FAKE_SDK = object()


def capability_qa_client_construction_inputs(
    role_limits_payload: dict[str, Any], snapshot: dict[str, Any],
) -> tuple[dict[str, int], frozenset[str], dict[str, dict[str, Any]], dict[str, dict[str, float]]]:
    """Extract (model_context_limits, streaming_pinned_models, extra_request_fields, model_prices)

    Mirrors ``rejudge.phase2_preflight_runner._build_client_params``'s own extraction of these
    four fields from the bound role-limits-and-request-settings artifact and the price snapshot
    (the runner's ``max_retries``/``usage_log_path``/``ledger_identity`` fields are irrelevant to
    request-kwargs construction and are not needed here). ``role_limits_payload`` is whatever
    schema version the caller passes (v5 for the current merged-slot binding; the historical r2
    relaunch builder still passes v4 -- these four fields' SHAPE is identical across v4/v5, only
    ``streaming_pinned_models``'s per-model VALUE differs).
    """
    request_settings = role_limits_payload["request_settings"]
    context_ceilings = role_limits_payload["context_ceilings"]
    model_context_limits = {
        model_id: int(entry["context_length_tokens"])
        for model_id, entry in context_ceilings.items()
    }
    streaming_pinned_models = frozenset(request_settings["streaming_pinned_models"])
    extra_request_fields = {
        model_id: dict(fields)
        for model_id, fields in request_settings["per_model_extra_fields"].items()
    }
    model_prices = {
        model_id: {
            "in": float(entry["input_usd_per_million_tokens"]),
            "out": float(entry["output_usd_per_million_tokens"]),
        }
        for model_id, entry in snapshot["models"].items()
    }
    return model_context_limits, streaming_pinned_models, extra_request_fields, model_prices


def build_hash_only_client(
    *, model_context_limits: dict[str, int], streaming_pinned_models: frozenset[str],
    extra_request_fields: dict[str, dict[str, Any]], model_prices: dict[str, dict[str, float]],
    approved_cap_usd: float,
) -> api_client.RejudgeClient:
    """A real, strictly production-configured ``RejudgeClient`` that is never used to call out.

    Every strict-mode Phase 2 setting ``build_production_client_factory`` sets is threaded
    through verbatim (``require_explicit_reasoning_max_tokens=True``, ``strict_context_mode=
    True``, ``halt_on_unknown_charge=True``, ``strict_model_pricing=True``), so this client's
    private ``_resolve_max_tokens``/``_build_request_kwargs`` methods (see
    :func:`compute_request_fields_sha256`) compute exactly what the live client would compute for
    the same call. A stand-in SDK object satisfies the constructor's live-client guards; no
    ``usage_log_path``/``error_log_path`` is configured, so this client never writes a file, and
    it is never asked to ``complete()`` a real (or fake) call -- only its private kwargs-building
    methods are ever invoked.
    """
    return api_client.RejudgeClient(
        approved_cap_usd=approved_cap_usd,
        dry_run=False,
        _sdk_client=_UNUSED_FAKE_SDK,
        model_prices=model_prices,
        strict_model_pricing=True,
        require_explicit_reasoning_max_tokens=True,
        model_context_limits=model_context_limits,
        strict_context_mode=True,
        streaming_pinned_models=streaming_pinned_models,
        extra_request_fields=extra_request_fields,
        halt_on_unknown_charge=True,
    )


def compute_request_fields_sha256(
    client: api_client.RejudgeClient, *, model: str, messages: list[dict[str, str]],
    temperature: float, max_tokens: int, seed: int,
) -> str:
    """Byte-exact ``request_fields_sha256`` for one call, via api_client's OWN code, never reimplemented.

    Mirrors exactly what ``RejudgeClient.complete()`` computes for the first (and, per the
    frozen ``transport_retry_policy``, every identical-seed retry) attempt of the same call:

    1. ``max_tokens`` passes through the client's own ``_resolve_max_tokens`` (the
       reasoning-floor guard) -- a no-op here since the v4 role-limits artifact's
       ``effective_request_max_tokens`` already applies that floor, but calling it anyway keeps
       this function byte-identical to ``complete()``'s own code path rather than merely
       "equivalent".
    2. The request kwargs dict is built with the client's own ``_build_request_kwargs`` (base
       fields plus the streaming transport pins and any per-model ``extra_request_fields``,
       exactly as production sends them).
    3. The hash is computed with the EXACT same expression ``RejudgeClient.complete`` uses:
       ``hashlib.sha256(api_client._canonical_json(request_kwargs)...).hexdigest()``.

    See ``tests/test_build_phase2_preflight_manifest.py``'s
    ``test_request_fields_sha256_matches_live_complete_call`` for the fidelity proof: a strict
    production-config client with a FAKE SDK, actually calling ``.complete()``, records this
    exact hash in its response metadata for a diverse sample of manifest entries.
    """
    resolved_max_tokens = client._resolve_max_tokens(model, max_tokens)
    streaming = model in client._streaming_models
    request_kwargs = client._build_request_kwargs(
        model=model, messages=messages, temperature=temperature, max_tokens=resolved_max_tokens,
        seed=seed, streaming=streaming)
    return hashlib.sha256(
        api_client._canonical_json(request_kwargs).encode("utf-8")).hexdigest()


# --- git state guard --------------------------------------------------------------------------


def _git_output(repo_root: Path, *args: str, run: Callable[..., Any]) -> str:
    result = run(["git", *args], cwd=repo_root, capture_output=True, text=True, check=True)
    return result.stdout


def dirty_paths_beyond(
    porcelain_output: str, allowed_relative_posix_paths: frozenset[str],
) -> list[str]:
    """Pure parser: which ``git status --porcelain=v1`` lines name a path outside the allow-list.

    Split out from :func:`assert_clean_git_state` so its logic is unit-testable against
    fabricated porcelain text without invoking real git or touching this repo's actual state.
    """
    dirty: list[str] = []
    for line in porcelain_output.splitlines():
        if not line.strip():
            continue
        path_field = line[3:]
        if " -> " in path_field:  # renames: "old -> new"; only the new path matters
            path_field = path_field.split(" -> ", 1)[1]
        path_field = path_field.strip()
        if len(path_field) >= 2 and path_field[0] == '"' and path_field[-1] == '"':
            path_field = path_field[1:-1]
        if path_field not in allowed_relative_posix_paths:
            dirty.append(line)
    return dirty


def assert_clean_git_state(
    repo_root: Path, *, allowed_relative_posix_paths: frozenset[str],
    expected_head: str = EXPECTED_GIT_COMMIT,
    run: Callable[..., Any] = subprocess.run,
) -> str:
    """Refuse to proceed unless HEAD is the reviewed commit and the tree is otherwise clean.

    "Otherwise clean" means: every path named by ``git status --porcelain`` is one of this
    builder's own output artifacts (which legitimately show up as untracked-then-modified across
    repeated runs). Anything else -- an unrelated edit, a stray file, a half-finished change --
    halts before a single byte is written. Returns the verified HEAD commit.
    """
    head = _git_output(repo_root, "rev-parse", "HEAD", run=run).strip()
    if head != expected_head:
        raise BuilderRefusedError(
            f"refusing to build: HEAD is {head!r}, expected the recorded, already-reviewed "
            f"commit {expected_head!r}. This builder only ever binds that exact commit's code "
            "into implementation_provenance.git_commit; update EXPECTED_GIT_COMMIT only after "
            "deliberately re-reviewing the new commit, never as an automatic side effect of "
            "running this script.")
    status = _git_output(
        repo_root, "status", "--porcelain=v1", "--untracked-files=all", run=run)
    dirty = dirty_paths_beyond(status, allowed_relative_posix_paths)
    if dirty:
        raise BuilderRefusedError(
            "refusing to build: the git tree is dirty beyond this builder's own output "
            f"artifacts ({sorted(allowed_relative_posix_paths)!r}): {dirty!r}")
    return head


def _git_blob_bytes(
    repo_root: Path, *, revision: str, relative_path: str, run: Callable[..., Any],
) -> bytes:
    """Return the RAW, stored bytes of ``relative_path`` at ``revision`` (no smudge filtering).

    ``git show <rev>:<path>`` emits the blob's stored content verbatim: unlike a checkout, it
    never applies ``core.autocrlf`` (or any other smudge filter), so this is the one git
    primitive that reveals exactly what ``git cat-file`` -- and therefore ``git clone``'s stored
    object -- actually contains, independent of this working tree's own filter configuration.
    """
    result = run(
        ["git", "show", f"{revision}:{relative_path}"], cwd=repo_root,
        capture_output=True, check=False)
    if result.returncode != 0:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise BuilderRefusedError(
            f"refusing to build: could not read the committed blob for {relative_path!r} at "
            f"{revision!r} (needed for code-provenance byte verification): {stderr}")
    stdout = result.stdout
    if isinstance(stdout, str):
        # A caller-supplied ``run`` configured with text=True would have already decoded/
        # newline-translated the blob, defeating the whole point of this raw comparison; treat
        # that as a caller bug rather than silently re-encoding a possibly-corrupted string.
        raise BuilderRefusedError(
            f"refusing to build: git blob read for {relative_path!r} returned decoded text, "
            "not raw bytes; this comparison requires a byte-mode subprocess runner")
    return stdout


def frozen_code_bytes_diverging_from_git_blob(
    repo_root: Path, *, expected_head: str,
    frozen_relative_paths: Iterable[str] = pe.CODE_PROVENANCE_FROZEN_FILES,
    run: Callable[..., Any] = subprocess.run,
) -> list[str]:
    """Which :data:`rejudge.phase2_execution.CODE_PROVENANCE_FROZEN_FILES` paths have working-
    tree bytes that differ, byte-for-byte, from the exact blob committed at ``expected_head``.

    This check is deliberately INDEPENDENT of ``git status``/``git diff``: those compare content
    through git's own text-conversion filters (e.g. ``core.autocrlf``), so a line-ending-only
    drift between the working tree and the committed blob can be completely invisible to
    ``git status --porcelain`` -- and therefore to :func:`assert_clean_git_state` -- while still
    changing ``code_bundle_sha256``, which hashes RAW file bytes and is never routed through any
    git filter (see ``rejudge.phase2_execution.compute_code_bundle_sha256``). Comparing
    ``Path.read_bytes()`` directly against :func:`_git_blob_bytes` catches exactly the drift a
    porcelain-status-only gate cannot, on any platform/config where checkout-time text
    conversion is active.
    """
    diverging: list[str] = []
    for relative in frozen_relative_paths:
        path = repo_root / relative
        try:
            worktree_bytes = path.read_bytes()
        except OSError as exc:
            raise BuilderRefusedError(
                f"refusing to build: frozen code-provenance file is unreadable: {path}: {exc}"
            ) from exc
        blob_bytes = _git_blob_bytes(
            repo_root, revision=expected_head, relative_path=relative, run=run)
        if worktree_bytes != blob_bytes:
            diverging.append(relative)
    return diverging


def assert_frozen_code_bytes_match_git_blob(
    repo_root: Path, *, expected_head: str,
    frozen_relative_paths: Iterable[str] = pe.CODE_PROVENANCE_FROZEN_FILES,
    run: Callable[..., Any] = subprocess.run,
) -> None:
    """Refuse to proceed if any frozen code-provenance file's working-tree bytes have drifted
    from the exact blob committed at ``expected_head``, even when ``git status`` cannot see it.

    Must be called in addition to, never instead of, :func:`assert_clean_git_state`: that check
    catches ordinary content edits; this one catches the narrower, more dangerous case of a
    filter-only drift (observed in practice: ``core.autocrlf=true`` silently re-writing tracked
    ``.py`` files to CRLF on checkout/touch) that leaves git's own comparison tools reporting a
    perfectly clean tree while ``code_bundle_sha256`` -- and so the whole
    ``execution_identity_sha256``/authorization pair -- is bound to bytes a pristine checkout of
    the same commit would never reproduce.
    """
    diverging = frozen_code_bytes_diverging_from_git_blob(
        repo_root, expected_head=expected_head, frozen_relative_paths=frozen_relative_paths,
        run=run)
    if diverging:
        raise BuilderRefusedError(
            "refusing to build: the following code-provenance frozen file(s) have working-tree "
            "bytes that differ from the exact blob committed at "
            f"{expected_head!r}, even though `git status`/`git diff` may report the tree as "
            "clean (this happens when a checkout-time text filter, e.g. core.autocrlf, rewrites "
            f"line endings): {sorted(diverging)!r}. Restore each file to the committed blob's "
            "raw bytes (e.g. `git show "
            f"{expected_head}:<path>` written back verbatim, not `git checkout` which re-applies "
            "the same filter) before re-running this builder.")


# --- canonical JSON I/O --------------------------------------------------------------------


def canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                   allow_nan=False)
        + "\n"
    ).encode("utf-8")


def write_canonical_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


# --- planning-cell inventory ----------------------------------------------------------------


def load_capability_planning(
    protocol: dict[str, Any], repo_root: Path,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Return (sorted planning_cell_keys, cell_key -> cell) for exactly the capability_qa cells.

    Reuses ``phase2_plan.enumerate_cells`` -- the same function ``validate_execution_manifest``
    itself calls to derive the expected planning-cell inventory -- rather than deriving the cell
    set independently.
    """
    main_ids = phase2_plan.load_main_question_ids(protocol, repo_root)
    cells = phase2_plan.enumerate_cells(protocol, main_ids)
    capability_cells = [cell for cell in cells if cell["kind"] == "capability_qa"]
    if len(capability_cells) != pe.EXPECTED_CAPABILITY_CELL_COUNT:
        raise BuilderRefusedError(
            f"the frozen protocol produced {len(capability_cells)} capability_qa planning "
            f"cells, expected exactly {pe.EXPECTED_CAPABILITY_CELL_COUNT}")
    cells_by_key = {str(cell["cell_key"]): cell for cell in capability_cells}
    planning_keys = sorted(cells_by_key)
    return planning_keys, cells_by_key


# --- top-level assembly ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuiltArtifacts:
    manifest: dict[str, Any]
    authorization: dict[str, Any]
    seed_derivation: dict[str, Any]
    execution_identity_sha256: str


def build_manifest_and_authorization(
    repo_root: Path,
    *,
    recorded_at_utc: str | None = None,
    stage_cap_usd: float = STAGE_CAP_USD,
    cumulative_cap_usd: float = CUMULATIVE_CAP_USD,
    git_commit: str = EXPECTED_GIT_COMMIT,
) -> BuiltArtifacts:
    """Build the manifest, its authorization record, and the seed-derivation sidecar.

    Pure function of the real, tracked repo artifacts under ``repo_root`` plus
    ``recorded_at_utc`` (defaults to "now" if not supplied; pass an explicit value for
    determinism, e.g. in tests). Every hash is recomputed fresh from disk; nothing is trusted
    from any prior build. Never writes anything -- see :func:`main` for that.
    """
    repo_root = Path(repo_root)

    protocol = phase2_plan.load_protocol(repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)
    execution_semantics = protocol["decisions"]["execution_semantics"]
    seed_policy = str(execution_semantics["seed_policy"])
    side_assignment_policy = str(execution_semantics["side_assignment_policy"])
    namespace = str(protocol["cell_key_namespace"])
    capability_condition_id = str(
        protocol["decisions"]["capability_measurement"]["condition_id"])
    if capability_condition_id != CAPABILITY_CONDITION_ID:
        raise BuilderRefusedError(
            "frozen protocol decisions.capability_measurement.condition_id drifted from "
            f"{CAPABILITY_CONDITION_ID!r}: observed {capability_condition_id!r}")

    planning_keys, cells_by_key = load_capability_planning(protocol, repo_root)

    combined = json.loads(
        (repo_root / pe.DEFAULT_COMBINED_AI_AUDIT_RELATIVE_PATH).read_text(encoding="utf-8"))
    ai_review.validate_combined(combined, root=repo_root)
    amendment = json.loads(
        (repo_root / pe.DEFAULT_A1_AMENDMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    ai_review.validate_amendment(amendment, combined_review=combined, root=repo_root)

    bundle, _bundle_protocol = prompt_bundle.load_and_validate(
        repo_root / pe.DEFAULT_PROMPT_BUNDLE_RELATIVE_PATH,
        repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)
    approval = json.loads(
        (repo_root / pe.DEFAULT_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH).read_text(encoding="utf-8"))

    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(
        repo_root / pe.DEFAULT_PRICE_SNAPSHOT_RELATIVE_PATH,
        repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)

    v4_payload, _v4_protocol, _v4_snapshot = role_limits.load_and_validate_v4(
        artifact_path=repo_root / ROLE_LIMITS_V4_RELATIVE_PATH,
        protocol_path=repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH,
        snapshot_path=repo_root / pe.DEFAULT_PRICE_SNAPSHOT_RELATIVE_PATH,
        v3_artifact_path=repo_root / pe.DEFAULT_ROLE_LIMITS_V3_RELATIVE_PATH,
        project_root=repo_root,
    )
    # v5 is the 2026-07-19 r2-incident transport fix (streaming pin extended to all three
    # reasoning models; SDK-internal retries pinned to 0; explicit http_timeout; a new
    # application-level per-call wall-clock ceiling). The MERGED role_limits_and_request_
    # settings_artifact manifest slot is v5-only (phase2_execution.py's validate_execution_
    # manifest rejects v4/v3/v2/v1 there), so it -- not v4_payload -- is what's bound below and
    # what drives request-hash computation (streaming_pinned_models' now-3-model value is a real
    # content change to the request kwargs the live client will actually send). v4_payload is
    # STILL loaded and used, deliberately, for the frozen v3 READY forecast schema's
    # bindings.role_limits_v4 slot immediately below -- that binding is pinned to the real v4
    # artifact forever and is unaffected by a transport-only fix; see phase2_execution.py's own
    # merged-slot validation block for why this split is correct.
    v5_payload, _v5_protocol, _v5_snapshot = role_limits.load_and_validate_v5(
        artifact_path=repo_root / ROLE_LIMITS_V5_RELATIVE_PATH,
        protocol_path=repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH,
        snapshot_path=repo_root / pe.DEFAULT_PRICE_SNAPSHOT_RELATIVE_PATH,
        v4_artifact_path=repo_root / ROLE_LIMITS_V4_RELATIVE_PATH,
        project_root=repo_root,
    )
    # The manifest's cost_forecast slot now requires the v3 "ready" forecast schema (relaunch
    # attempt r2), bound to role-limits v4 directly -- no more v3-payload substitution workaround
    # (that was only ever needed while the forecast schema itself still bound role-limits v3; see
    # rejudge.phase2_preflight_forecast.validate_forecast_v3 and this task's r2 forecast rebuild).
    provider_refresh_payload = json.loads(
        (repo_root / pe.DEFAULT_PROVIDER_REFRESH_RELATIVE_PATH).read_text(encoding="utf-8"))
    forecast_payload = json.loads(
        (repo_root / FORECAST_R2_RELATIVE_PATH).read_text(encoding="utf-8"))
    preflight_forecast.validate_forecast_v3(
        forecast_payload, root=repo_root, protocol=protocol, role_limits_v4=v4_payload,
        snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh_payload)
    storage_policy_payload = json.loads(
        (repo_root / STORAGE_POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
    provider_reconciliation_payload = json.loads(
        (repo_root / pe.DEFAULT_PROVIDER_RECONCILIATION_2026_07_19_RELATIVE_PATH)
        .read_text(encoding="utf-8"))
    gemma_closure_payload = json.loads(
        (repo_root / pe.DEFAULT_GEMMA_RECOVERY_CLOSURE_RELATIVE_PATH).read_text(encoding="utf-8"))
    prior_attempt_closure_payload = json.loads(
        (repo_root / PRIOR_ATTEMPT_CLOSURE_RELATIVE_PATH).read_text(encoding="utf-8"))
    delegation_raw = (repo_root / pe.DEFAULT_PREFLIGHT_DELEGATION_RELATIVE_PATH).read_bytes()

    uv_lock_sha256 = hashlib.sha256(
        (repo_root / pe.DEFAULT_UV_LOCK_RELATIVE_PATH).read_bytes()).hexdigest()

    # --- corpus (byte-identical messages the runner will itself render and send) ---
    corpus_entries = capability_corpus.render_capability_corpus(bundle, protocol, repo_root)
    corpus_lookup = {(str(e["question_id"]), str(e["side"])): e for e in corpus_entries}

    # --- request-hash inputs (v5-bound: this is where the streaming-pin extension shows up) ----
    model_context_limits, streaming_pinned_models, extra_request_fields, model_prices = (
        capability_qa_client_construction_inputs(v5_payload, snapshot))
    hash_client = build_hash_only_client(
        model_context_limits=model_context_limits,
        streaming_pinned_models=streaming_pinned_models,
        extra_request_fields=extra_request_fields, model_prices=model_prices,
        approved_cap_usd=float(cumulative_cap_usd))

    entries_without_key: list[dict[str, Any]] = []
    worked_seed_examples: list[dict[str, Any]] = []
    for index, planning_cell_key in enumerate(planning_keys):
        cell = cells_by_key[planning_cell_key]
        model = str(cell["judge_model"])
        question_id = str(cell["question_id"])
        replicate_index = int(cell["replicate_index"])
        side = "A" if replicate_index == 0 else "B"
        condition = str(cell["condition"])

        corpus_entry = corpus_lookup.get((question_id, side))
        if corpus_entry is None:
            raise BuilderRefusedError(
                f"no rendered capability_qa corpus entry for question {question_id!r} side "
                f"{side!r} (planning cell {planning_cell_key!r})")
        messages = [
            {"role": "system", "content": corpus_entry["system_prompt"]},
            {"role": "user", "content": corpus_entry["user_prompt"]},
        ]
        resolved = role_limits.resolve_request_parameters(
            v5_payload, protocol, model, pe.CAPABILITY_CALL_ROLE)

        seed = derive_capability_qa_seed(
            namespace=namespace, question_id=question_id, model=model, condition=condition,
            replicate_index=replicate_index)

        request_fields_sha256 = compute_request_fields_sha256(
            hash_client, model=model, messages=messages, temperature=resolved.temperature,
            max_tokens=resolved.effective_max_tokens, seed=seed)

        entries_without_key.append({
            "planning_cell_key": planning_cell_key,
            "call_role": pe.CAPABILITY_CALL_ROLE,
            "call_index": index,
            "model": model,
            "seed": seed,
            "side": side,
            "request_fields_sha256": request_fields_sha256,
        })
        if index < 5:
            worked_seed_examples.append({
                "planning_cell_key": planning_cell_key, "question_id": question_id,
                "model": model, "condition": condition, "replicate_index": replicate_index,
                "side": side, "seed": seed,
            })

    schema_version = str(protocol["materialization_requirements"]["transition_model"][
        "manifest_schema_version"])

    prompt_bundle_approval_binding = {
        "tracked_path": pe.DEFAULT_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH.as_posix(),
        "sha256": pe.canonical_sha256(approval),
    }
    role_limits_binding = {
        "path": ROLE_LIMITS_V5_RELATIVE_PATH, "sha256": pe.canonical_sha256(v5_payload),
    }
    cost_forecast_binding = {
        "path": FORECAST_R2_RELATIVE_PATH, "sha256": pe.canonical_sha256(forecast_payload),
    }
    storage_policy_binding = {
        "path": STORAGE_POLICY_RELATIVE_PATH,
        "sha256": pe.canonical_sha256(storage_policy_payload),
    }
    provider_reconciliation_binding = {
        "path": pe.DEFAULT_PROVIDER_RECONCILIATION_2026_07_19_RELATIVE_PATH.as_posix(),
        "sha256": pe.canonical_sha256(provider_reconciliation_payload),
    }
    provider_refresh_binding = {
        "path": pe.DEFAULT_PROVIDER_REFRESH_RELATIVE_PATH.as_posix(),
        "sha256": pe.canonical_sha256(provider_refresh_payload),
    }
    gemma_closure_binding = {
        "path": pe.DEFAULT_GEMMA_RECOVERY_CLOSURE_RELATIVE_PATH.as_posix(),
        "sha256": pe.canonical_sha256(gemma_closure_payload),
    }
    prior_attempt_closure_binding = {
        "path": PRIOR_ATTEMPT_CLOSURE_RELATIVE_PATH,
        "sha256": pe.canonical_sha256(prior_attempt_closure_payload),
    }
    implementation_provenance = {
        "git_commit": git_commit,
        "code_bundle_sha256": pe.compute_code_bundle_sha256(repo_root),
    }
    ledger_binding = {"path": LEDGER_PATH, "ledger_identity": LEDGER_IDENTITY_LABEL}
    satisfied_prerequisites = {"gemma_recovery_or_waiver": gemma_closure_binding}

    manifest_without_inventory = {
        "schema_version": schema_version,
        "stage": pe.STAGE_CAPABILITY_PREFLIGHT,
        "protocol_canonical_sha256": pe.canonical_sha256(protocol),
        "a1_amendment_canonical_sha256": pe.canonical_sha256(amendment),
        "combined_ai_audit_canonical_sha256": pe.canonical_sha256(combined),
        "question_bank_bundle_sha256": protocol["source_bindings"]["question_bank_bundle_sha256"],
        "prompt_bundle_canonical_sha256": pe.canonical_sha256(bundle),
        "prompt_bundle_declared_status": bundle["status"],
        "prompt_bundle_approval_tracked_path": prompt_bundle_approval_binding["tracked_path"],
        "prompt_bundle_approval_canonical_sha256": prompt_bundle_approval_binding["sha256"],
        "role_limits_and_request_settings_artifact": role_limits_binding,
        "provider_price_snapshot_canonical_sha256": pe.canonical_sha256(snapshot),
        "uv_lock_sha256": uv_lock_sha256,
        "seed_policy": seed_policy,
        "side_assignment_policy": side_assignment_policy,
        "satisfied_prerequisites": satisfied_prerequisites,
        "ledger": ledger_binding,
        "planning_cell_keys": list(planning_keys),
        "stage_cap_usd": float(stage_cap_usd),
        "cumulative_cap_usd": float(cumulative_cap_usd),
        "cost_forecast": cost_forecast_binding,
        "storage_policy": storage_policy_binding,
        "provider_reconciliation_evidence": provider_reconciliation_binding,
        "provider_refresh": provider_refresh_binding,
        "prior_attempt_closure": prior_attempt_closure_binding,
        "implementation_provenance": implementation_provenance,
    }

    identity = pe.build_execution_identity(
        schema_version=schema_version,
        stage=pe.STAGE_CAPABILITY_PREFLIGHT,
        protocol_canonical_sha256=manifest_without_inventory["protocol_canonical_sha256"],
        a1_amendment_canonical_sha256=manifest_without_inventory["a1_amendment_canonical_sha256"],
        combined_ai_audit_canonical_sha256=manifest_without_inventory[
            "combined_ai_audit_canonical_sha256"],
        question_bank_bundle_sha256=manifest_without_inventory["question_bank_bundle_sha256"],
        prompt_bundle_canonical_sha256=manifest_without_inventory[
            "prompt_bundle_canonical_sha256"],
        prompt_bundle_declared_status=manifest_without_inventory["prompt_bundle_declared_status"],
        prompt_bundle_approval_artifact=prompt_bundle_approval_binding,
        role_limits_and_request_settings_artifact=role_limits_binding,
        provider_price_snapshot_canonical_sha256=manifest_without_inventory[
            "provider_price_snapshot_canonical_sha256"],
        uv_lock_sha256=uv_lock_sha256,
        seed_policy=seed_policy, side_assignment_policy=side_assignment_policy,
        satisfied_prerequisites=satisfied_prerequisites,
        ledger=ledger_binding,
        planning_cell_keys=planning_keys,
        provider_call_inventory_entries=entries_without_key,
        stage_cap_usd=float(stage_cap_usd), cumulative_cap_usd=float(cumulative_cap_usd),
        cost_forecast=cost_forecast_binding, storage_policy=storage_policy_binding,
        provider_reconciliation_evidence=provider_reconciliation_binding,
        provider_refresh=provider_refresh_binding,
        prior_attempt_closure=prior_attempt_closure_binding,
        implementation_provenance=implementation_provenance,
    )
    execution_identity_sha256 = pe.derive_execution_identity_sha256(identity)

    provider_call_inventory = [
        {
            **entry,
            "execution_call_key": pe.derive_execution_call_key(
                execution_identity_sha256, planning_cell_key=entry["planning_cell_key"],
                call_role=entry["call_role"], call_index=entry["call_index"]),
        }
        for entry in entries_without_key
    ]

    manifest = {**manifest_without_inventory, "provider_call_inventory": provider_call_inventory}
    if set(manifest) != pe.MANIFEST_TOP_LEVEL_KEYS:
        # Defensive only: every key above is drawn 1:1 from MANIFEST_TOP_LEVEL_KEYS, so this
        # can only fire if that frozen set itself changes out from under this builder.
        raise BuilderRefusedError(
            "assembled manifest key set no longer matches "
            "rejudge.phase2_execution.MANIFEST_TOP_LEVEL_KEYS; this builder needs updating")

    if recorded_at_utc is None:
        recorded_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    authorization = {
        "execution_identity_sha256": execution_identity_sha256,
        "stage": pe.STAGE_CAPABILITY_PREFLIGHT,
        "stage_cap_usd": float(stage_cap_usd),
        "cumulative_cap_usd": float(cumulative_cap_usd),
        "approver": pe.PREFLIGHT_DELEGATION_APPROVER,
        "approved_at_utc": pe.PREFLIGHT_DELEGATION_APPROVED_AT_UTC,
        "recorded_at_utc": recorded_at_utc,
        "approval_basis_tracked_path": pe.DEFAULT_PREFLIGHT_DELEGATION_RELATIVE_PATH.as_posix(),
        "approval_basis_sha256": hashlib.sha256(delegation_raw).hexdigest(),
    }
    if set(authorization) != pe.AUTHORIZATION_KEYS:
        raise BuilderRefusedError(
            "assembled authorization key set no longer matches "
            "rejudge.phase2_execution.AUTHORIZATION_KEYS; this builder needs updating")

    seed_derivation = {
        "schema_version": "phase2_capability_preflight_seed_derivation_v1",
        "artifact_id": "phase2_capability_preflight_seed_derivation_2026-07-19",
        "protocol_id": protocol["protocol_id"],
        "manifest_tracked_path": MANIFEST_RELATIVE_PATH,
        "execution_identity_sha256": execution_identity_sha256,
        "formula": SEED_DERIVATION_FORMULA_DOCSTRING,
        "protocol_seed_policy": seed_policy,
        "omitted_dimensions_note": (
            "capability_qa calls have no debater or transcript dimension (no debate occurs for "
            "this stage), so those two protocol seed_policy dimensions are inapplicable rather "
            "than omitted by oversight; 'judge' is filled by the calling model (there is no "
            "separate debater to judge). Attempt-level variation is deliberately excluded: per "
            "transport_retry_policy ('repeat identical request and seed'), every transport "
            "retry of one call reuses the exact same seed as its first attempt."
        ),
        "worked_examples": worked_seed_examples,
        "execution_authorized": False,
        "note": (
            "Informational cross-reference only. NOT bound or hashed into the execution "
            "manifest: rejudge/phase2_execution.py's MANIFEST_TOP_LEVEL_KEYS is an exact, "
            "frozen key set with no seed_derivation slot (see scripts/"
            "build_phase2_preflight_manifest.py's module docstring, 'KNOWN DEVIATION'). This "
            "record grants no execution authority."
        ),
    }

    return BuiltArtifacts(
        manifest=manifest, authorization=authorization, seed_derivation=seed_derivation,
        execution_identity_sha256=execution_identity_sha256)


@dataclass(frozen=True, slots=True)
class BuiltArtifactsR2:
    manifest: dict[str, Any]
    authorization_template: dict[str, Any]
    reauthorization_ask: dict[str, Any]
    execution_identity_sha256: str
    request_hash_delta: dict[str, int]


def build_manifest_and_authorization_r2(
    repo_root: Path,
    *,
    recorded_at_utc: str | None = None,
    stage_cap_usd: float = STAGE_CAP_USD,
    cumulative_cap_usd: float = CUMULATIVE_CAP_USD,
    git_commit: str | None = None,
) -> BuiltArtifactsR2:
    """Build the r2 (relaunch) manifest + PENDING authorization template + reauthorization ask.

    Pure function of the real, tracked repo artifacts under ``repo_root`` (plus the E: r2 run
    directory's absence, asserted as a side-effect-free filesystem check). Every hash is
    recomputed fresh from disk; nothing is trusted from any prior build. Never writes anything.

    ``git_commit`` defaults to the actual current ``git rev-parse HEAD`` -- unlike the r1
    builder's pinned ``EXPECTED_GIT_COMMIT``, this is deliberately dynamic: the orchestrator
    commits the code fix separately and may rebuild this any number of times as HEAD moves, so
    this path must stay cheap to rerun without a manual constant bump (see this module's r2
    section docstring). ``implementation_provenance.code_bundle_sha256`` is, as always,
    recomputed fresh from the real files on disk under ``repo_root`` regardless of what
    ``git_commit`` says -- git_commit is provenance-only (see
    ``rejudge.phase2_execution._validate_implementation_provenance``'s own docstring).
    """
    repo_root = Path(repo_root)
    assert_r2_run_directory_absent()

    if git_commit is None:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True,
            check=True)
        git_commit = result.stdout.strip()

    protocol = phase2_plan.load_protocol(repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)
    execution_semantics = protocol["decisions"]["execution_semantics"]
    seed_policy = str(execution_semantics["seed_policy"])
    side_assignment_policy = str(execution_semantics["side_assignment_policy"])
    namespace = str(protocol["cell_key_namespace"])
    capability_condition_id = str(
        protocol["decisions"]["capability_measurement"]["condition_id"])
    if capability_condition_id != CAPABILITY_CONDITION_ID:
        raise BuilderRefusedError(
            "frozen protocol decisions.capability_measurement.condition_id drifted from "
            f"{CAPABILITY_CONDITION_ID!r}: observed {capability_condition_id!r}")

    planning_keys, cells_by_key = load_capability_planning(protocol, repo_root)

    combined = json.loads(
        (repo_root / pe.DEFAULT_COMBINED_AI_AUDIT_RELATIVE_PATH).read_text(encoding="utf-8"))
    ai_review.validate_combined(combined, root=repo_root)
    amendment = json.loads(
        (repo_root / pe.DEFAULT_A1_AMENDMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    ai_review.validate_amendment(amendment, combined_review=combined, root=repo_root)

    bundle, _bundle_protocol = prompt_bundle.load_and_validate(
        repo_root / pe.DEFAULT_PROMPT_BUNDLE_RELATIVE_PATH,
        repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)
    approval = json.loads(
        (repo_root / pe.DEFAULT_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH).read_text(encoding="utf-8"))

    snapshot, _snapshot_protocol = price_snapshot.load_and_validate(
        repo_root / pe.DEFAULT_PRICE_SNAPSHOT_RELATIVE_PATH,
        repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)

    v4_payload, _v4_protocol, _v4_snapshot = role_limits.load_and_validate_v4(
        artifact_path=repo_root / ROLE_LIMITS_V4_RELATIVE_PATH,
        protocol_path=repo_root / pe.DEFAULT_PROTOCOL_RELATIVE_PATH,
        snapshot_path=repo_root / pe.DEFAULT_PRICE_SNAPSHOT_RELATIVE_PATH,
        v3_artifact_path=repo_root / pe.DEFAULT_ROLE_LIMITS_V3_RELATIVE_PATH,
        project_root=repo_root,
    )

    provider_refresh_payload = json.loads(
        (repo_root / pe.DEFAULT_PROVIDER_REFRESH_RELATIVE_PATH).read_text(encoding="utf-8"))
    forecast_payload = json.loads(
        (repo_root / FORECAST_R2_RELATIVE_PATH).read_text(encoding="utf-8"))
    preflight_forecast.validate_forecast_v3(
        forecast_payload, root=repo_root, protocol=protocol, role_limits_v4=v4_payload,
        snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh_payload)

    storage_policy_payload = json.loads(
        (repo_root / STORAGE_POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
    provider_reconciliation_payload = json.loads(
        (repo_root / pe.DEFAULT_PROVIDER_RECONCILIATION_2026_07_19_RELATIVE_PATH)
        .read_text(encoding="utf-8"))
    gemma_closure_payload = json.loads(
        (repo_root / pe.DEFAULT_GEMMA_RECOVERY_CLOSURE_RELATIVE_PATH).read_text(encoding="utf-8"))
    prior_attempt_closure_payload = json.loads(
        (repo_root / PRIOR_ATTEMPT_CLOSURE_RELATIVE_PATH).read_text(encoding="utf-8"))

    uv_lock_sha256 = hashlib.sha256(
        (repo_root / pe.DEFAULT_UV_LOCK_RELATIVE_PATH).read_bytes()).hexdigest()

    # --- corpus (byte-identical messages the runner will itself render and send) ---
    corpus_entries = capability_corpus.render_capability_corpus(bundle, protocol, repo_root)
    corpus_lookup = {(str(e["question_id"]), str(e["side"])): e for e in corpus_entries}

    # --- request-hash inputs (v4-bound: this is where the stream_options removal shows up) -----
    model_context_limits, streaming_pinned_models, extra_request_fields, model_prices = (
        capability_qa_client_construction_inputs(v4_payload, snapshot))
    hash_client = build_hash_only_client(
        model_context_limits=model_context_limits,
        streaming_pinned_models=streaming_pinned_models,
        extra_request_fields=extra_request_fields, model_prices=model_prices,
        approved_cap_usd=float(cumulative_cap_usd))

    entries_without_key: list[dict[str, Any]] = []
    for index, planning_cell_key in enumerate(planning_keys):
        cell = cells_by_key[planning_cell_key]
        model = str(cell["judge_model"])
        question_id = str(cell["question_id"])
        replicate_index = int(cell["replicate_index"])
        side = "A" if replicate_index == 0 else "B"
        condition = str(cell["condition"])

        corpus_entry = corpus_lookup.get((question_id, side))
        if corpus_entry is None:
            raise BuilderRefusedError(
                f"no rendered capability_qa corpus entry for question {question_id!r} side "
                f"{side!r} (planning cell {planning_cell_key!r})")
        messages = [
            {"role": "system", "content": corpus_entry["system_prompt"]},
            {"role": "user", "content": corpus_entry["user_prompt"]},
        ]
        resolved = role_limits.resolve_request_parameters(
            v4_payload, protocol, model, pe.CAPABILITY_CALL_ROLE)

        seed = derive_capability_qa_seed(
            namespace=namespace, question_id=question_id, model=model, condition=condition,
            replicate_index=replicate_index)

        request_fields_sha256 = compute_request_fields_sha256(
            hash_client, model=model, messages=messages, temperature=resolved.temperature,
            max_tokens=resolved.effective_max_tokens, seed=seed)

        entries_without_key.append({
            "planning_cell_key": planning_cell_key,
            "call_role": pe.CAPABILITY_CALL_ROLE,
            "call_index": index,
            "model": model,
            "seed": seed,
            "side": side,
            "request_fields_sha256": request_fields_sha256,
        })

    schema_version = str(protocol["materialization_requirements"]["transition_model"][
        "manifest_schema_version"])

    prompt_bundle_approval_binding = {
        "tracked_path": pe.DEFAULT_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH.as_posix(),
        "sha256": pe.canonical_sha256(approval),
    }
    role_limits_binding = {
        "path": ROLE_LIMITS_V4_RELATIVE_PATH, "sha256": pe.canonical_sha256(v4_payload),
    }
    cost_forecast_binding = {
        "path": FORECAST_R2_RELATIVE_PATH, "sha256": pe.canonical_sha256(forecast_payload),
    }
    storage_policy_binding = {
        "path": STORAGE_POLICY_RELATIVE_PATH,
        "sha256": pe.canonical_sha256(storage_policy_payload),
    }
    provider_reconciliation_binding = {
        "path": pe.DEFAULT_PROVIDER_RECONCILIATION_2026_07_19_RELATIVE_PATH.as_posix(),
        "sha256": pe.canonical_sha256(provider_reconciliation_payload),
    }
    provider_refresh_binding = {
        "path": pe.DEFAULT_PROVIDER_REFRESH_RELATIVE_PATH.as_posix(),
        "sha256": pe.canonical_sha256(provider_refresh_payload),
    }
    gemma_closure_binding = {
        "path": pe.DEFAULT_GEMMA_RECOVERY_CLOSURE_RELATIVE_PATH.as_posix(),
        "sha256": pe.canonical_sha256(gemma_closure_payload),
    }
    prior_attempt_closure_binding = {
        "path": PRIOR_ATTEMPT_CLOSURE_RELATIVE_PATH,
        "sha256": pe.canonical_sha256(prior_attempt_closure_payload),
    }
    implementation_provenance = {
        "git_commit": git_commit,
        "code_bundle_sha256": pe.compute_code_bundle_sha256(repo_root),
    }
    ledger_binding = {"path": LEDGER_PATH_R2, "ledger_identity": LEDGER_IDENTITY_LABEL}
    satisfied_prerequisites = {"gemma_recovery_or_waiver": gemma_closure_binding}

    manifest_without_inventory = {
        "schema_version": schema_version,
        "stage": pe.STAGE_CAPABILITY_PREFLIGHT,
        "protocol_canonical_sha256": pe.canonical_sha256(protocol),
        "a1_amendment_canonical_sha256": pe.canonical_sha256(amendment),
        "combined_ai_audit_canonical_sha256": pe.canonical_sha256(combined),
        "question_bank_bundle_sha256": protocol["source_bindings"]["question_bank_bundle_sha256"],
        "prompt_bundle_canonical_sha256": pe.canonical_sha256(bundle),
        "prompt_bundle_declared_status": bundle["status"],
        "prompt_bundle_approval_tracked_path": prompt_bundle_approval_binding["tracked_path"],
        "prompt_bundle_approval_canonical_sha256": prompt_bundle_approval_binding["sha256"],
        "role_limits_and_request_settings_artifact": role_limits_binding,
        "provider_price_snapshot_canonical_sha256": pe.canonical_sha256(snapshot),
        "uv_lock_sha256": uv_lock_sha256,
        "seed_policy": seed_policy,
        "side_assignment_policy": side_assignment_policy,
        "satisfied_prerequisites": satisfied_prerequisites,
        "ledger": ledger_binding,
        "planning_cell_keys": list(planning_keys),
        "stage_cap_usd": float(stage_cap_usd),
        "cumulative_cap_usd": float(cumulative_cap_usd),
        "cost_forecast": cost_forecast_binding,
        "storage_policy": storage_policy_binding,
        "provider_reconciliation_evidence": provider_reconciliation_binding,
        "provider_refresh": provider_refresh_binding,
        "prior_attempt_closure": prior_attempt_closure_binding,
        "implementation_provenance": implementation_provenance,
    }

    identity = pe.build_execution_identity(
        schema_version=schema_version,
        stage=pe.STAGE_CAPABILITY_PREFLIGHT,
        protocol_canonical_sha256=manifest_without_inventory["protocol_canonical_sha256"],
        a1_amendment_canonical_sha256=manifest_without_inventory["a1_amendment_canonical_sha256"],
        combined_ai_audit_canonical_sha256=manifest_without_inventory[
            "combined_ai_audit_canonical_sha256"],
        question_bank_bundle_sha256=manifest_without_inventory["question_bank_bundle_sha256"],
        prompt_bundle_canonical_sha256=manifest_without_inventory[
            "prompt_bundle_canonical_sha256"],
        prompt_bundle_declared_status=manifest_without_inventory["prompt_bundle_declared_status"],
        prompt_bundle_approval_artifact=prompt_bundle_approval_binding,
        role_limits_and_request_settings_artifact=role_limits_binding,
        provider_price_snapshot_canonical_sha256=manifest_without_inventory[
            "provider_price_snapshot_canonical_sha256"],
        uv_lock_sha256=uv_lock_sha256,
        seed_policy=seed_policy, side_assignment_policy=side_assignment_policy,
        satisfied_prerequisites=satisfied_prerequisites,
        ledger=ledger_binding,
        planning_cell_keys=planning_keys,
        provider_call_inventory_entries=entries_without_key,
        stage_cap_usd=float(stage_cap_usd), cumulative_cap_usd=float(cumulative_cap_usd),
        cost_forecast=cost_forecast_binding, storage_policy=storage_policy_binding,
        provider_reconciliation_evidence=provider_reconciliation_binding,
        provider_refresh=provider_refresh_binding,
        prior_attempt_closure=prior_attempt_closure_binding,
        implementation_provenance=implementation_provenance,
    )
    execution_identity_sha256 = pe.derive_execution_identity_sha256(identity)

    provider_call_inventory = [
        {
            **entry,
            "execution_call_key": pe.derive_execution_call_key(
                execution_identity_sha256, planning_cell_key=entry["planning_cell_key"],
                call_role=entry["call_role"], call_index=entry["call_index"]),
        }
        for entry in entries_without_key
    ]

    manifest = {**manifest_without_inventory, "provider_call_inventory": provider_call_inventory}
    if set(manifest) != pe.MANIFEST_TOP_LEVEL_KEYS:
        # Defensive only: every key above is drawn 1:1 from MANIFEST_TOP_LEVEL_KEYS, so this
        # can only fire if that frozen set itself changes out from under this builder.
        raise BuilderRefusedError(
            "assembled r2 manifest key set no longer matches "
            "rejudge.phase2_execution.MANIFEST_TOP_LEVEL_KEYS; this builder needs updating")

    # --- the 212/848 regenerated-request-hash invariant, against the real OLD (r1) manifest ----
    old_manifest_path = repo_root / MANIFEST_RELATIVE_PATH
    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    request_hash_delta = assert_exactly_qwen_request_hashes_changed(
        old_manifest, provider_call_inventory)

    if recorded_at_utc is None:
        recorded_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    authorization_template = build_authorization_template_r2(
        execution_identity_sha256=execution_identity_sha256, stage_cap_usd=stage_cap_usd,
        cumulative_cap_usd=cumulative_cap_usd, recorded_at_utc=recorded_at_utc)

    role_limits_v4_sha256 = pe.canonical_sha256(v4_payload)
    ask_text = build_reauthorization_ask_text(
        execution_identity_sha256=execution_identity_sha256,
        role_limits_v4_sha256=role_limits_v4_sha256)
    reauthorization_ask = {
        "schema_version": "phase2_preflight_reauthorization_ask_v1",
        "artifact_id": "phase2_preflight_reauthorization_ask_2026-07-19-r2",
        "execution_identity_sha256": execution_identity_sha256,
        "role_limits_v4_sha256": role_limits_v4_sha256,
        "stage_cap_usd": float(stage_cap_usd),
        "ask_text": ask_text,
        "execution_authorized": False,
        "note": (
            "Informational only. NOT bound or hashed into the execution manifest or the "
            "authorization record; this is the exact text to send to the delegated owner (Jack "
            "Maiorino) to clear the authorization template's reauthorization_pending marker."
        ),
    }

    return BuiltArtifactsR2(
        manifest=manifest, authorization_template=authorization_template,
        reauthorization_ask=reauthorization_ask,
        execution_identity_sha256=execution_identity_sha256,
        request_hash_delta=request_hash_delta,
    )


def main_r2(repo_root: Path, *, recorded_at_utc: str | None = None) -> int:
    """Build, verify, and write the r2 relaunch manifest + pending authorization template."""
    built = build_manifest_and_authorization_r2(repo_root, recorded_at_utc=recorded_at_utc)

    # require_authorized=False must fully validate: the manifest itself, apart from
    # authorization, is structurally and semantically complete.
    validated_unauthorized = pe.validate_execution_manifest(
        built.manifest, project_root=repo_root, require_authorized=False)
    if validated_unauthorized.authorized:
        raise BuilderRefusedError(
            "require_authorized=False unexpectedly reported authorized=True; unreachable given "
            "no authorization argument was passed")
    if validated_unauthorized.execution_identity_sha256 != built.execution_identity_sha256:
        raise BuilderRefusedError(
            "builder-computed execution_identity_sha256 disagrees with the validator's own "
            f"independently recomputed value: builder={built.execution_identity_sha256!r}, "
            f"validator={validated_unauthorized.execution_identity_sha256!r}")

    # require_authorized=True must REFUSE the pending-marker template, and for the RIGHT reason:
    # the extra reauthorization_pending key makes AUTHORIZATION_KEYS drift ("fields drifted"),
    # not some unrelated failure.
    try:
        pe.validate_execution_manifest(
            built.manifest, project_root=repo_root, authorization=built.authorization_template,
            require_authorized=True)
    except pe.ExecutionAuthorityError as exc:
        if "fields drifted" not in str(exc):
            raise BuilderRefusedError(
                "require_authorized=True refused the pending authorization template, but NOT "
                f"for the expected reason ('fields drifted'): {exc}") from exc
        print(f"require_authorized=True correctly REFUSED (pending marker): {exc}")
    else:
        raise BuilderRefusedError(
            "require_authorized=True unexpectedly ACCEPTED the pending authorization template; "
            "this must never happen while reauthorization_pending is set")

    manifest_path = repo_root / MANIFEST_RELATIVE_PATH_R2
    authorization_path = repo_root / AUTHORIZATION_RELATIVE_PATH_R2
    ask_path = repo_root / REAUTHORIZATION_ASK_RELATIVE_PATH_R2
    write_canonical_json(manifest_path, built.manifest)
    write_canonical_json(authorization_path, built.authorization_template)
    write_canonical_json(ask_path, built.reauthorization_ask)

    # Allowed: creating the E: r2 archive directory. NEVER create the ledger file itself.
    Path(LEDGER_ARCHIVE_DIR_R2).mkdir(parents=True, exist_ok=True)

    # Re-load the just-written bytes from disk and re-validate (require_authorized=False): proves
    # the COMMITTED manifest itself passes, not merely the in-memory object that produced it.
    reloaded_manifest = pe.load_execution_manifest(manifest_path)
    revalidated = pe.validate_execution_manifest(
        reloaded_manifest, project_root=repo_root, require_authorized=False)

    print(f"execution_identity_sha256={revalidated.execution_identity_sha256}")
    print(f"authorized={revalidated.authorized}")
    print(f"request_hash_delta={built.request_hash_delta}")
    print(f"manifest_path={manifest_path}")
    print(f"authorization_template_path={authorization_path}")
    print(f"reauthorization_ask_path={ask_path}")
    print(f"reauthorization_ask_text={built.reauthorization_ask['ask_text']}")
    print(f"ledger_archive_dir_created={LEDGER_ARCHIVE_DIR_R2}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--recorded-at-utc", default=None,
        help="Override authorization.recorded_at_utc (mainly for deterministic tests).")
    parser.add_argument(
        "--r2", action="store_true",
        help=(
            "build the relaunch-attempt-r2 manifest + PENDING authorization template (new run "
            "directory, v3/v4-bound forecast, required prior_attempt_closure binding) instead "
            "of the original r1 manifest+authorization pair. Does NOT run the r1 git-clean-HEAD "
            "gates: it stamps whatever the actual current HEAD is (see build_manifest_and_"
            "authorization_r2's docstring) so it stays cheap to rerun as the orchestrator "
            "commits the code fix."))
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    if args.r2:
        return main_r2(repo_root, recorded_at_utc=args.recorded_at_utc)

    head = assert_clean_git_state(
        repo_root, allowed_relative_posix_paths=ALLOWED_DIRTY_RELATIVE_PATHS)
    # assert_clean_git_state only sees what `git status` sees, which is blind to filter-only
    # (e.g. core.autocrlf) drift between the working tree and the committed blob; this second,
    # independent gate checks the frozen code-provenance files' RAW bytes directly against the
    # committed blob so code_bundle_sha256 can never silently bind to bytes a pristine checkout
    # of `head` would not itself produce.
    assert_frozen_code_bytes_match_git_blob(repo_root, expected_head=head)

    built = build_manifest_and_authorization(
        repo_root, recorded_at_utc=args.recorded_at_utc, git_commit=head)

    # Fail closed BEFORE writing anything: the in-memory artifacts must themselves pass the real
    # validator, fully authorized, against the real repo root.
    validated = pe.validate_execution_manifest(
        built.manifest, project_root=repo_root, authorization=built.authorization,
        require_authorized=True)
    if not validated.authorized:
        raise BuilderRefusedError(
            "validator returned an unauthorized result for the freshly built manifest; "
            "refusing to write")
    if validated.execution_identity_sha256 != built.execution_identity_sha256:
        raise BuilderRefusedError(
            "builder-computed execution_identity_sha256 disagrees with the validator's own "
            f"independently recomputed value: builder={built.execution_identity_sha256!r}, "
            f"validator={validated.execution_identity_sha256!r}")

    manifest_path = repo_root / MANIFEST_RELATIVE_PATH
    authorization_path = repo_root / AUTHORIZATION_RELATIVE_PATH
    seed_derivation_path = repo_root / SEED_DERIVATION_RELATIVE_PATH
    write_canonical_json(manifest_path, built.manifest)
    write_canonical_json(authorization_path, built.authorization)
    write_canonical_json(seed_derivation_path, built.seed_derivation)

    # Allowed: creating the E: archive directory. NEVER create the ledger file itself -- genesis
    # belongs to the run.
    Path(LEDGER_ARCHIVE_DIR).mkdir(parents=True, exist_ok=True)

    # Re-load the just-written bytes from disk and re-validate: proves the COMMITTED artifacts
    # themselves pass, not merely the in-memory objects that produced them.
    reloaded_manifest = pe.load_execution_manifest(manifest_path)
    reloaded_authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    revalidated = pe.validate_execution_manifest(
        reloaded_manifest, project_root=repo_root, authorization=reloaded_authorization,
        require_authorized=True)
    if not revalidated.authorized:
        raise BuilderRefusedError(
            "the manifest/authorization files just written to disk failed re-validation; "
            "this should be unreachable if the in-memory pre-check above passed")

    print(f"execution_identity_sha256={revalidated.execution_identity_sha256}")
    print(f"authorized={revalidated.authorized}")
    print(f"stage_cap_usd={revalidated.stage_cap_usd} cumulative_cap_usd="
          f"{revalidated.cumulative_cap_usd}")
    print(f"planning_cell_keys={len(revalidated.planning_cell_keys)}")
    print(f"provider_call_inventory={len(revalidated.provider_call_inventory)}")
    print(f"manifest_path={manifest_path}")
    print(f"authorization_path={authorization_path}")
    print(f"seed_derivation_path={seed_derivation_path} (informational sidecar; NOT bound into "
          "the manifest -- see KNOWN DEVIATION in this script's module docstring)")
    print(f"ledger_archive_dir_created={LEDGER_ARCHIVE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
