"""The phase-3 canary driver: load, validate, build the client, execute, resume.

Phase 3 has no incident history of its own yet -- unlike ``rejudge.phase2_canary_live`` (whose
bulk is recorded incident recovery for things that already went wrong on the phase-2 canary),
this module is deliberately narrow: validate the manifest and its authorization, build one
correctly-pinned client, resolve the frozen plan into the shapes the shared phase-2 executor
already knows how to run, and execute -- resumable at cell granularity, exactly like phase 2.

Three properties this module exists to guarantee, all correctness-critical because a bug here
either spends real money or corrupts a pre-registered experiment:

**Transport pins actually reach the live client.** The 2026-08-10 five-hour ``ssl.read`` hang
happened because the phase-2 main driver built its client through
``rejudge.run_accounting.create_accounted_client`` without passing ``http_timeout`` /
``sdk_internal_max_retries`` / ``per_call_wall_clock_ceiling_seconds`` -- that function accepted
no such parameters at the time, so the live client silently fell back to the Together SDK's own
unpinned default. :func:`build_phase3_client` threads all three, derived from the manifest-bound
role-limits artifact's ``request_settings.transport`` the SAME way
``rejudge.phase2_preflight_runner.build_production_client_factory`` does (see that function's
docstring), through ``create_accounted_client``'s now-extended kwargs.

``create_accounted_client`` does not expose the OTHER phase-2 hardening knobs
(``strict_context_mode``, per-model context ceilings, ``streaming_pinned_models``,
``reasoning_models``, ``extra_request_fields``, ``halt_on_unknown_charge``,
``require_explicit_reasoning_max_tokens``) -- every other live driver in this codebase
(``build_production_client_factory``, ``phase2_canary_live.build_live_client``,
``phase2_checker_selection_runner._create_v5_client``) sets them at construction time, so
:func:`build_phase3_client` applies them to the client it gets back rather than shipping a
canary whose live client is measurably weaker than every sibling driver. See
:func:`_apply_role_limit_hardening`'s docstring for exactly what this does and why it is a
flagged design decision rather than a silent extension of ``create_accounted_client`` itself.

**Zero transcript generation, ever.** ``rejudge.phase2_canary_execute.execute_cell`` already
carries the additive ``transcript_generation_forbidden`` guard (``GenerationForbiddenError``,
raised before any provider call); this module is the first caller that actually sets it, via
:func:`rejudge.phase2_canary_runner.run_canary`'s own additive ``transcript_generation_forbidden``
parameter (added alongside this module, since ``run_canary`` previously built its
``CellContext`` with the flag left at its dataclass default of ``False`` regardless of what a
caller wanted).

**capability_qa is a fourth call shape phase 2 never had.** The canary's capability anchor
(``phase3_capability_qa`` cells: no debater, no transcript dependency, a direct forced-choice
question against the reused ``capability_qa`` prompt template) fits none of
``phase2_canary_execute``'s three patterns (transcript / single-call / judgment-loop), so this
module resolves and executes it itself (:func:`resolve_canary_cells`,
:func:`run_capability_cells`) rather than forcing a mismatched shape through the shared
executor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from rejudge import phase3_manifest, phase3_plan, run_accounting
from rejudge.api_client import CapExceededError, _RESERVED_REQUEST_KWARGS
from rejudge.config import make_seed
from rejudge.phase2_call_cache import CallCache
from rejudge.phase2_caching_client import CachingClient
from rejudge.phase2_canary_cells import ResolvedCell
from rejudge.phase2_canary_execute import CellContext, GenerationForbiddenError
from rejudge.phase2_canary_live import (
    RoleLimitResolvingClient,
    WORKLIST_FILENAME,
    _PauseModeReviewer,
    commit_decisions_into,
    export_reviewer_worklist,
    local_path,
)
from rejudge.phase2_canary_order import CellResultStore
from rejudge.phase2_canary_runner import RunOutcome, run_canary
from rejudge.phase2_dual_gate import DualGateDecisionStore

REPO_ROOT = Path(__file__).resolve().parents[1]

PHASE2_PROMPT_BUNDLE_RELATIVE_PATH = phase3_manifest.PHASE2_PROMPT_BUNDLE_RELATIVE_PATH
REVIEWER_PROMPT_RELATIVE_PATH = phase3_manifest.REVIEWER_PROMPT_RELATIVE_PATH
PHASE2_PROVIDER_PRICE_SNAPSHOT_RELATIVE_PATH = Path(
    "rejudge/phase2_provider_price_snapshot_2026-07-18.json")

CAPABILITY_QA_ROLE = "capability_qa"
CAPABILITY_QA_TEMPERATURE = 0.0

# The 2026-08-18 canary stall bound (see run_phase3_canary's docstring): how many NEW pending
# payloads one judgment pass will accumulate before it stops attempting further not-yet-complete
# cells and returns. Deliberately a "meaningful review-wave batch" size, not a tiny number: too
# small wastes round trips (a review wave has real fixed overhead -- packaging, dispatch,
# commit); too large re-exposes the exact unbounded-pass risk this constant exists to bound.
# Overridable per call (run_phase3_canary's own pending_payload_limit parameter); explicitly
# None disables the bound entirely, matching rejudge.phase2_canary_runner.run_canary's own
# unbounded default.
DEFAULT_PENDING_PAYLOAD_LIMIT = 64


class Phase3RunnerError(ValueError):
    """Raised when the phase-3 driver refuses to proceed. Always fails closed."""


def _load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --- manifest + authorization ------------------------------------------------------------


def load_and_validate_manifest(manifest_path: str | Path, *,
                               project_root: str | Path = ".",
                               transcript_bundle_dir: str | Path | None = None) -> dict[str, Any]:
    """Load a phase-3 manifest and re-validate every binding against the artifacts on disk.

    ``manifest_path`` is injected, never hard-coded, matching every other loader in this
    codebase. Delegates entirely to :func:`rejudge.phase3_manifest.validate_manifest`, which
    fails closed on any hash mismatch, schema drift, or a manifest that (illegally) authorizes
    itself.
    """
    manifest = _load_json(manifest_path)
    protocol_path = Path(project_root) / str(manifest.get("protocol_tracked_path") or "")
    if transcript_bundle_dir is None:
        # The manifest itself records where its bundles were resolved at build time (the
        # archive, for a real manifest; a fixture root, in tests). Honor that binding rather
        # than falling back to the repo-relative default. The recorded string is passed
        # verbatim: phase3_manifest translates drive-letter paths for filesystem access only,
        # keeping the recorded binding host-independent.
        recorded = manifest.get("frozen_inputs", {}).get("main_transcript_bundle_path")
        if recorded:
            transcript_bundle_dir = str(Path(str(recorded).replace("\\", "/")).parent)
    return phase3_manifest.validate_manifest(
        manifest, protocol_path=protocol_path, project_root=project_root,
        transcript_bundle_dir=transcript_bundle_dir)


def validate_canary_authorization(record: Mapping[str, Any],
                                  manifest: Mapping[str, Any]) -> dict[str, Any]:
    """The three checks a phase-3 canary launch requires of its authorization record.

    Deliberately narrower than phase 2's ``AUTHORIZATION_KEYS`` cross-match: the phase-3
    canary authorization record (``rejudge/phase3_canary_authorization_2026-08-18.json``) is a
    free-form, owner-authored append-only record rather than a fixed-schema artifact, so this
    checks exactly what a launch gate must never skip -- explicit authorization, and binding to
    THIS manifest's identity -- and reads the canary spend cap it also carries, rather than
    re-deriving a rigid field-set contract this record was never designed to satisfy.
    """
    if not isinstance(record, Mapping):
        raise Phase3RunnerError("phase-3 canary authorization must be a mapping")
    if record.get("execution_authorized") is not True:
        raise Phase3RunnerError(
            "phase-3 canary authorization does not carry execution_authorized == true")
    binds = record.get("binds")
    bound_identity = binds.get("execution_identity_sha256") if isinstance(binds, Mapping) else None
    manifest_identity = manifest.get("execution_identity_sha256")
    if bound_identity != manifest_identity:
        raise Phase3RunnerError(
            "phase-3 canary authorization binds a different execution_identity_sha256 than "
            f"the loaded manifest: authorization={bound_identity!r}, "
            f"manifest={manifest_identity!r}")
    scope = record.get("scope")
    cap = scope.get("canary_cap_usd") if isinstance(scope, Mapping) else None
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
        raise Phase3RunnerError(
            "phase-3 canary authorization's scope.canary_cap_usd must be a positive number")
    return dict(record)


def load_phase3_canary_authorization(path: str | Path,
                                     manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Load and validate the authorization record. Refuses outright if the file is absent."""
    path = Path(path)
    if not path.exists():
        raise Phase3RunnerError(
            f"no phase-3 canary authorization record at {path}; refusing to start")
    return validate_canary_authorization(_load_json(path), manifest)


# --- transport pins + strict-mode client construction -------------------------------------


def transport_pins(role_limits: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the v5 transport pins the SAME way ``build_production_client_factory`` does.

    Reads ``request_settings.transport`` off the manifest-bound role-limits artifact --
    ``http_timeout``, ``sdk_internal_max_retries``, ``per_call_wall_clock_ceiling_seconds`` --
    never a hard-coded value. These are exactly the three ``create_accounted_client`` kwargs
    the 2026-08-10 hang's fix added.
    """
    transport = role_limits["request_settings"]["transport"]
    return {
        "http_timeout": {field: float(value)
                         for field, value in transport["http_timeout"].items()},
        "sdk_internal_max_retries": int(transport["sdk_internal_max_retries"]),
        "per_call_wall_clock_ceiling_seconds": float(
            transport["per_call_wall_clock_ceiling_seconds"]),
    }


def _apply_role_limit_hardening(client: Any, role_limits: Mapping[str, Any]) -> None:
    """Apply the phase-2 hardening knobs ``create_accounted_client`` does not expose.

    FLAGGED DESIGN DECISION: ``create_accounted_client`` was extended (see the module
    docstring) with exactly the three v5 transport-pin kwargs the 2026-08-10 incident named;
    it was not extended to also carry ``strict_context_mode``/``model_context_limits``/
    ``streaming_pinned_models``/``reasoning_models``/``extra_request_fields``/
    ``halt_on_unknown_charge``/``require_explicit_reasoning_max_tokens``. Every OTHER live
    client this codebase builds sets all of those (``build_production_client_factory``,
    ``phase2_canary_live.build_live_client``, ``phase2_checker_selection_runner.
    _create_v5_client``), and phase 3's roster includes reasoning models
    (``openai/gpt-oss-120b`` with a pinned ``reasoning_effort`` extra field, plus three more
    under ``reasoning_models.model_ids``) for which these knobs are not cosmetic. Rather than
    leave the canary's live client the only one in the codebase without them, this function
    sets them directly on the ``RejudgeClient`` instance ``create_accounted_client`` returns.
    ``RejudgeClient`` stores every one of these as a plain instance attribute with no
    constructor-only validation beyond the ``extra_request_fields`` reserved-keyword collision
    check (re-run here, reusing the SAME frozen constant) and the ``streaming_pinned_models`` ->
    ``_streaming_models`` derivation (mirrored here too, so a pinned model is proactively
    streamed on its first attempt rather than falling back to reactive discovery).
    """
    request_settings = role_limits["request_settings"]
    model_context_limits = {
        model: int(entry["context_length_tokens"])
        for model, entry in role_limits["context_ceilings"].items()}
    streaming_pinned_models = frozenset(request_settings["streaming_pinned_models"])
    extra_request_fields = {
        model: dict(fields)
        for model, fields in request_settings["per_model_extra_fields"].items()}
    for model, fields in extra_request_fields.items():
        collisions = _RESERVED_REQUEST_KWARGS & set(fields)
        if collisions:
            raise Phase3RunnerError(
                f"role-limits per_model_extra_fields[{model!r}] reuses reserved request "
                f"field(s) {sorted(collisions)!r}")
    reasoning_models = frozenset(
        (role_limits.get("reasoning_models") or {}).get("model_ids") or ())

    client.require_explicit_reasoning_max_tokens = True
    client.strict_context_mode = True
    client.model_context_limits = model_context_limits
    client.streaming_pinned_models = streaming_pinned_models
    client._streaming_models = set(streaming_pinned_models)
    client.reasoning_models = reasoning_models
    client.extra_request_fields = extra_request_fields
    client.halt_on_unknown_charge = True


def resolve_roster_model_prices(roster_judges: list[str], *,
                                project_root: str | Path = ".") -> dict[str, dict[str, float]]:
    """Model prices for every roster judge, fail-closed if any is unresolved.

    Phase 3's 7-candidate roster splits across TWO price sources, and neither alone is
    complete: the 4 continuing judges (plus the oracle and query-checker, which happen to be
    continuing judges too) are priced in the clean ``phase2_provider_price_snapshot``; the new
    candidate judges are priced in ``phase3_provider_snapshot``'s
    ``new_judges_pending_verification`` section, EXCEPT the one admitted by owner substitution
    (``rejudge/phase3_amendment1_roster_2026-08-18.json``), whose price is recorded only inside
    a DIFFERENT (catalog-absent) candidate's ``closest_catalog_ids`` list -- see that snapshot.
    All three sources are searched, generically (never by hard-coding one candidate's name);
    an unresolved judge fails closed rather than defaulting to any price, since silently
    under-pricing a live model is a real-money bug, not a cosmetic one.
    """
    root = Path(project_root)
    phase2_models = (_load_json(root / PHASE2_PROVIDER_PRICE_SNAPSHOT_RELATIVE_PATH)
                     .get("models") or {})
    phase3_snapshot = _load_json(
        root / phase3_manifest.PROVIDER_SNAPSHOT_RELATIVE_PATH)
    continuing = phase3_snapshot.get("continuing_judges_and_oracle") or {}
    new_candidates = phase3_snapshot.get("new_judges_pending_verification") or {}

    def _pair(pricing: Mapping[str, Any] | None) -> dict[str, float] | None:
        if not isinstance(pricing, Mapping) or pricing.get("input") is None:
            return None
        return {"in": float(pricing["input"]), "out": float(pricing["output"])}

    prices: dict[str, dict[str, float]] = {}
    for model in roster_judges:
        entry = phase2_models.get(model)
        if isinstance(entry, Mapping) and "input_usd_per_million_tokens" in entry:
            prices[model] = {"in": float(entry["input_usd_per_million_tokens"]),
                             "out": float(entry["output_usd_per_million_tokens"])}
            continue
        pair = _pair((continuing.get(model) or {}).get("pricing_usd_per_million_tokens"))
        if pair is not None:
            prices[model] = pair
            continue
        candidate = new_candidates.get(model)
        if isinstance(candidate, Mapping) and candidate.get("status") == "present_exact_id":
            pair = _pair(candidate.get("pricing_usd_per_million_tokens"))
            if pair is not None:
                prices[model] = pair
                continue
        # An owner-substituted candidate: priced only inside a DIFFERENT (absent) candidate's
        # closest_catalog_ids list.
        found = None
        for other in new_candidates.values():
            if not isinstance(other, Mapping):
                continue
            for alt in other.get("closest_catalog_ids") or []:
                if isinstance(alt, Mapping) and alt.get("id") == model:
                    found = _pair(alt.get("pricing_usd_per_million_tokens"))
                    break
            if found is not None:
                break
        if found is not None:
            prices[model] = found
            continue
        raise Phase3RunnerError(
            f"no price could be resolved for roster judge {model!r} from "
            f"{PHASE2_PROVIDER_PRICE_SNAPSHOT_RELATIVE_PATH}, "
            f"{phase3_manifest.PROVIDER_SNAPSHOT_RELATIVE_PATH}'s continuing_judges_and_oracle, "
            "or its new_judges_pending_verification (including closest_catalog_ids); refusing "
            "to build a live client with an unpriced model")
    return prices


def build_phase3_client(manifest: Mapping[str, Any], *, project_root: str | Path,
                        canary_cap_usd: float, usage_log_path: str | Path,
                        error_log_path: str | Path, call_cache_path: str | Path,
                        dry_run: bool = False) -> Any:
    """Build the phase-3 canary's accounted, role-limit-resolving, caching client.

    Layering, matching ``phase2_canary_live.build_live_client``:
    ``CachingClient(RoleLimitResolvingClient(RejudgeClient))``. The innermost client is built
    by ``run_accounting.create_accounted_client`` (cap + ledger + the v5 transport pins), then
    hardened in place (see :func:`_apply_role_limit_hardening`), then wrapped.

    Refuses to start if accounted spend has already reached ``canary_cap_usd`` (``>=``, not the
    ``RejudgeClient`` constructor's own strictly-``>`` prior-spend guard) -- the phase-3 spend
    control CONTEXT calls a hard requirement, checked here, at construction, before anything
    else can run.
    """
    root = Path(project_root)
    role_limits_path = root / str(manifest["frozen_inputs"]["role_limits_tracked_path"])
    role_limits = _load_json(role_limits_path)
    pins = transport_pins(role_limits)

    roster_judges = list(manifest["roster"]["judges"])
    model_prices = resolve_roster_model_prices(roster_judges, project_root=root)

    ledger_identity = None
    if not dry_run:
        # allow_create=True is only valid for a genuinely fresh (absent or empty) ledger --
        # prepare_usage_ledger refuses a PAID ledger with allow_create=True (a safeguard
        # against silently adopting someone else's spend). A resumed invocation must instead
        # validate the existing ledger, matching phase2_canary_live.bind_or_verify_ledger's
        # own create-vs-resume split.
        usage_path = Path(usage_log_path)
        ledger_already_paid = usage_path.exists() and usage_path.stat().st_size > 0
        ledger_identity = run_accounting.prepare_usage_ledger(
            usage_log_path, allow_create=not ledger_already_paid)

    raw_client, _summary = run_accounting.create_accounted_client(
        approved_cap_usd=canary_cap_usd, dry_run=dry_run, model_prices=model_prices,
        usage_log_path=usage_log_path, error_log_path=error_log_path,
        ledger_identity=ledger_identity, **pins)

    spent = float(getattr(raw_client, "actual_spent_usd", 0.0)) + float(
        getattr(raw_client, "uncertain_spend_usd", 0.0))
    if spent >= canary_cap_usd:
        raise Phase3RunnerError(
            f"accounted spend ${spent:.4f} has already reached the canary cap "
            f"${canary_cap_usd:.2f}; refusing to start")

    _apply_role_limit_hardening(raw_client, role_limits)

    resolving = RoleLimitResolvingClient(raw_client, role_limits["model_role_limits"])
    return CachingClient(resolving, CallCache(call_cache_path))


def underlying_rejudge_client(client: Any) -> Any:
    """Unwrap ``CachingClient``/``RoleLimitResolvingClient`` layers to the raw ``RejudgeClient``.

    Walks ``.inner`` (the attribute name both wrappers use) until it finds an object carrying
    ``http_timeout`` -- a real ``RejudgeClient`` attribute neither wrapper defines -- so this
    stays correct if a wrapping layer is added or removed later.
    """
    seen = client
    while hasattr(seen, "inner") and not hasattr(seen, "http_timeout"):
        seen = seen.inner
    return seen


# --- cell resolution: phase3_plan cells -> the shared phase-2 executor's shapes -----------


# Every sequential_bN>0 condition reuses the SAME bundle template family (the numeric budget is
# a runtime value threaded through judge_loop.run_judgment's remaining_budget/total_budget
# rendering, not a template choice -- see decisions.execution_semantics.prompt_bundle in
# rejudge/phase3_protocol.json: "the sequential_judge_query template is fully parameterized by
# {remaining_budget}/{total_budget}, so budgets 1, 4, and 8 require no prompt changes"). b0 has
# its own dedicated, budget-0 bundle entry.
_ZERO_BUDGET_COMPOSITION_KEY = "b0"
_NONZERO_BUDGET_COMPOSITION_KEY = "sequential_b2"


def _condition_lookup(protocol: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(condition["id"]): condition for condition in protocol["debate_grid"]["conditions"]}


# ResolvedCell.is_transcript checks base_kind(self.kind) against phase2_canary_cells'
# TRANSCRIPT_KINDS ({"debate_transcript", "capped_debate_transcript"}), the phase-2 kind
# vocabulary -- a phase-3 kind string like "phase3_canary_transcript_reference" never matches
# it, so a transcript cell resolved under its OWN phase-3 kind would silently skip the
# GenerationForbiddenError guard entirely and fall through to is_single_call's UncomposableCell
# instead. ResolvedCell.kind is purely an EXECUTION-DISPATCH hint for the shared executor (the
# real phase-3 kind/condition/cell_key are already the plan's own, recorded in the manifest and
# the preseeded row independently of this field), so this marks every phase-3 transcript-
# reference cell with phase 2's own recognised marker kind -- the one value that makes the
# shared ``is_transcript`` property, and therefore the guard, actually fire.
_TRANSCRIPT_MARKER_KIND = "debate_transcript"


def _resolve_transcript_cell(cell: Mapping[str, Any]) -> ResolvedCell:
    """A transcript-reference cell, resolved only enough to be recognised and skipped/refused.

    Never actually composed: reaching ``execute_cell`` under ``transcript_generation_forbidden
    =True`` raises ``GenerationForbiddenError`` as the FIRST check, before ``composition`` (or
    anything else about this cell) is ever read. Every transcript-reference cell must already
    be recorded in the result store by ``scripts/phase3_preseed_transcripts.py`` before a real
    run starts; this resolution exists so the plan's SKIP-if-``is_complete`` logic still works
    for it.
    """
    return ResolvedCell(
        cell_key=str(cell["cell_key"]), kind=_TRANSCRIPT_MARKER_KIND,
        condition=str(cell["condition"]),
        question_id=str(cell["question_id"]), judge_model=None,
        debater_model=cell.get("debater_model"), transcript_index=cell.get("transcript_index"),
        replicate_index=None, query_budget=0,
        dependency_keys=tuple(cell.get("dependency_keys") or ()), composition={},
        transcript_protocol_name=None, oracle_mode="none", arm_name=None)


def _resolve_judgment_cell(cell: Mapping[str, Any], *, conditions: Mapping[str, Any],
                           bundle: Mapping[str, Any]) -> ResolvedCell:
    condition_id = str(cell["condition"])
    protocol_condition = conditions.get(condition_id)
    if protocol_condition is None:
        raise Phase3RunnerError(
            f"cell {cell['cell_key']} names condition {condition_id!r}, which is not in the "
            "frozen protocol's debate_grid.conditions")
    query_budget = int(protocol_condition["query_budget"])
    oracle_mode = str(protocol_condition["oracle_mode"])
    bundle_key = (_ZERO_BUDGET_COMPOSITION_KEY if query_budget == 0
                 else _NONZERO_BUDGET_COMPOSITION_KEY)
    composition = bundle["condition_composition"]["debate_grid"].get(bundle_key)
    if composition is None:
        raise Phase3RunnerError(
            f"reused phase-2 prompt bundle has no debate_grid composition for {bundle_key!r} "
            f"(condition {condition_id!r}, budget {query_budget})")
    arm_name = "clean" if oracle_mode == "clean" else None

    return ResolvedCell(
        cell_key=str(cell["cell_key"]), kind=str(cell["kind"]), condition=condition_id,
        question_id=str(cell["question_id"]), judge_model=cell.get("judge_model"),
        debater_model=cell.get("debater_model"), transcript_index=cell.get("transcript_index"),
        replicate_index=cell.get("replicate_index"), query_budget=query_budget,
        dependency_keys=tuple(cell.get("dependency_keys") or ()), composition=composition,
        transcript_protocol_name=None, oracle_mode=oracle_mode, arm_name=arm_name)


def resolve_canary_cells(cells: list[Mapping[str, Any]], *, protocol: Mapping[str, Any],
                         bundle: Mapping[str, Any],
                         ) -> tuple[list[ResolvedCell], list[dict[str, Any]]]:
    """Split the phase-3 canary plan into (judgment+transcript ``ResolvedCell``s, capability_qa
    cells).

    Judgment and transcript cells resolve to :class:`~rejudge.phase2_canary_cells.ResolvedCell`
    and run through the shared ``phase2_canary_execute``/``phase2_canary_runner`` machinery
    unmodified. capability_qa cells fit none of that machinery's three call patterns -- they
    never existed in phase 2 -- so they are returned as plain dicts for
    :func:`run_capability_cells` to execute directly.
    """
    conditions = _condition_lookup(protocol)
    resolved: list[ResolvedCell] = []
    capability: list[dict[str, Any]] = []
    for cell in cells:
        kind = str(cell["kind"])
        if kind == phase3_plan.CAPABILITY_ANCHOR_KIND:
            capability.append(dict(cell))
        elif kind == phase3_plan.CANARY_TRANSCRIPT_KIND:
            resolved.append(_resolve_transcript_cell(cell))
        elif kind == phase3_plan.CANARY_JUDGMENT_KIND:
            resolved.append(_resolve_judgment_cell(cell, conditions=conditions, bundle=bundle))
        else:
            raise Phase3RunnerError(f"unrecognised phase-3 canary cell kind: {kind!r}")
    return resolved, capability


# --- capability_qa: a phase-3-only cell shape, executed directly --------------------------


_TOLERANT_ANSWER = re.compile(r"^ANSWER: ([AB])\.?$")
_STRICT_ANSWER = {"ANSWER: A": "A", "ANSWER: B": "B"}
INVALID_CAPABILITY_VERDICT = "INVALID"


def parse_capability_verdict_tolerant(raw_text: Any) -> str:
    """The frozen phase-3 ``parser_rule``: after stripping whitespace, exactly ``'ANSWER: A'``
    or ``'ANSWER: B'`` with at most one optional trailing period; anything else is INVALID."""
    if not isinstance(raw_text, str):
        return INVALID_CAPABILITY_VERDICT
    match = _TOLERANT_ANSWER.match(raw_text.strip())
    return match.group(1) if match else INVALID_CAPABILITY_VERDICT


def parse_capability_verdict_strict(raw_text: Any) -> str:
    """The no-trailing-period sensitivity variant (``secondary_analyses.capability_slope``)."""
    if not isinstance(raw_text, str):
        return INVALID_CAPABILITY_VERDICT
    return _STRICT_ANSWER.get(raw_text.strip(), INVALID_CAPABILITY_VERDICT)


def _capability_qa_side(question: Mapping[str, Any], mirror_index: int) -> tuple[str, str, str]:
    """(side, candidate_a, candidate_b) for one K2-mirrored capability_qa cell.

    Matches ``rejudge.phase2_capability_corpus``'s own side convention: side A puts the correct
    answer at candidate_a, side B mirrors it.
    """
    correct, wrong = str(question["correct_answer"]), str(question["wrong_answer"])
    if mirror_index == 0:
        return "A", correct, wrong
    return "B", wrong, correct


def _execute_capability_cell(cell: Mapping[str, Any], *, context: CellContext,
                             base_max_tokens: int) -> dict[str, Any]:
    question_id = str(cell["question_id"])
    judge_model = str(cell["judge_model"])
    mirror_index = int(cell["replicate_index"])
    question = context.question(question_id)
    world_document = context.world_document(question_id)
    side, candidate_a, candidate_b = _capability_qa_side(question, mirror_index)
    template = context.bundle["templates"][CAPABILITY_QA_ROLE]
    user_prompt = template["user_prompt_template"].format(
        world_document=world_document, question=question["question"],
        candidate_a=candidate_a, candidate_b=candidate_b)
    cell_key = str(cell["cell_key"])
    seed = make_seed(cell_key)
    raw = context.client.complete(
        [{"role": "system", "content": template["system_prompt"]},
         {"role": "user", "content": user_prompt}],
        judge_model, CAPABILITY_QA_TEMPERATURE, seed, base_max_tokens, kind="verdict",
        request_metadata={"cell_key": cell_key, "call_role": CAPABILITY_QA_ROLE,
                          "condition": "capability_qa", "question_id": question_id})
    tolerant = parse_capability_verdict_tolerant(raw)
    strict = parse_capability_verdict_strict(raw)
    return {
        "cell_key": cell_key, "condition": "capability_qa", "question_id": question_id,
        "judge_model": judge_model, "replicate_index": mirror_index, "side": side,
        "correct_side": side, "raw_verdict_text": raw,
        "parsed_answer_tolerant": tolerant, "parsed_answer_strict": strict,
        "is_correct_tolerant": tolerant == side, "is_correct_strict": strict == side,
        "seed": seed, "dry_run": getattr(context.client, "dry_run", False),
    }


def run_capability_cells(cells: list[dict[str, Any]], *, context: CellContext,
                         store: CellResultStore, base_max_tokens: int,
                         limit: int | None = None) -> RunOutcome:
    """Execute the phase-3 capability anchor: no dependencies, no gate, never pauses.

    Same halt taxonomy as ``phase2_canary_runner.run_canary``'s serial loop (a cap breach halts
    cleanly; anything unmodelled halts rather than being swallowed), scaled down to what this
    cell shape actually needs: capability_qa cells never propose an oracle query, so there is no
    pause/defer/abandon case to model, and no block scheduling -- 336 cells is small enough that
    the condition-balance concern phase 2's blocks exist for (spreading query-producing arms
    against time-varying provider degradation) does not apply to a cell kind with no query loop.
    """
    outcome = RunOutcome()
    attempted = 0
    for cell in cells:
        cell_key = str(cell["cell_key"])
        if store.is_complete(cell_key):
            outcome.skipped += 1
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            record = _execute_capability_cell(
                cell, context=context, base_max_tokens=base_max_tokens)
        except CapExceededError:
            outcome.halted_reason = "cap_exceeded"
            outcome.halted_cell_key = cell_key
            break
        except Exception as exc:  # noqa: BLE001 - halt on anything unmodelled, never swallow
            outcome.halted_reason = type(exc).__name__
            outcome.halted_cell_key = cell_key
            break
        store.record(cell_key, record)
        outcome.completed += 1
    return outcome


def _merge_outcomes(first: RunOutcome, second: RunOutcome) -> RunOutcome:
    return RunOutcome(
        completed=first.completed + second.completed, skipped=first.skipped + second.skipped,
        paused=first.paused + second.paused, deferred=first.deferred + second.deferred,
        abandoned=first.abandoned + second.abandoned,
        halted_reason=first.halted_reason if first.halted_reason is not None
        else second.halted_reason,
        halted_cell_key=first.halted_cell_key if first.halted_reason is not None
        else second.halted_cell_key,
        pending_payloads=list(first.pending_payloads) + list(second.pending_payloads),
        paused_cell_keys=list(first.paused_cell_keys) + list(second.paused_cell_keys),
        # Context-blocking is a judgment-phase-only concept (see run_phase3_canary): carried
        # from `first` only, matching halted_reason/halted_cell_key's own "judgment phase wins"
        # convention, since the capability phase never sets either.
        context_blocked=first.context_blocked, context_blocklist_sha256=first.context_blocklist_sha256)


# --- gate-review wiring: reuse phase 2's dual-gate flow unmodified ------------------------


def load_frozen_reviewer_prompt(manifest: Mapping[str, Any],
                                project_root: str | Path = ".") -> dict[str, str]:
    """The reused phase-2 reviewer prompt, re-hashed against its own declared sha and the
    manifest's binding before use -- mirrors ``phase2_canary_live.load_frozen_reviewer_prompt``,
    adapted to phase 3's manifest shape (no ``manifest["reviewer"]`` block; the tracked path is
    read from ``phase3_manifest``'s own constant, and the prompt hash from
    ``frozen_inputs.reviewer_prompt_sha256``, both already re-verified by manifest validation).
    """
    path = Path(project_root) / REVIEWER_PROMPT_RELATIVE_PATH
    artifact = _load_json(path)
    prompt = str(artifact["prompt"])
    observed = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if observed != artifact.get("prompt_sha256"):
        raise Phase3RunnerError(f"reviewer prompt drifted from its own declared sha in {path}")
    if observed != manifest["frozen_inputs"]["reviewer_prompt_sha256"]:
        raise Phase3RunnerError("reviewer prompt does not match the manifest binding")
    return {"prompt": prompt}


def commit_reviewer_decisions(manifest_path: str | Path, decisions_file: str | Path,
                              project_root: str | Path = ".") -> dict[str, int]:
    """Commit out-of-band reviewer outputs into the hash-chained decision store.

    Thin phase-3 adapter around ``phase2_canary_live.commit_decisions_into`` -- the actual
    commit logic is reused verbatim; only manifest loading/validation and path resolution are
    phase-3-shaped.
    """
    manifest = load_and_validate_manifest(manifest_path, project_root=project_root)
    archive_dir = local_path(manifest["ledger"]["archive_dir"])
    worklist = _load_json(archive_dir / WORKLIST_FILENAME)
    store = DualGateDecisionStore(local_path(manifest["ledger"]["decisions_path"]))
    return commit_decisions_into(store, worklist, _load_json(decisions_file))


# --- context-exclusion blocklist (scripts/phase3_context_precheck.py's output) -------------


def load_context_blocklist(path: str | Path,
                           protocol: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Load and namespace-verify a context-exclusion blocklist. Refuses outright if absent.

    Returns ``(blocklist, sha256_of_the_raw_file_bytes)`` -- the hash is over exactly the bytes
    read, so it identifies the file actually used, not a re-serialization of it.
    """
    path = Path(path)
    if not path.exists():
        raise Phase3RunnerError(f"context blocklist not found: {path}")
    raw = path.read_bytes()
    blocklist = json.loads(raw.decode("utf-8"))
    expected_namespace = str(protocol["cell_key_namespace"])
    actual_namespace = blocklist.get("cell_key_namespace")
    if actual_namespace != expected_namespace:
        raise Phase3RunnerError(
            f"context blocklist {path} names namespace {actual_namespace!r}, but this "
            f"manifest's protocol binds namespace {expected_namespace!r}; refusing to trust a "
            "blocklist computed against a different plan")
    return blocklist, hashlib.sha256(raw).hexdigest()


# --- the run itself ------------------------------------------------------------------------


def run_phase3_canary(manifest_path: str | Path, authorization_path: str | Path,
                      project_root: str | Path = ".", *, limit: int | None = None,
                      client: Any = None, reviewer: Any = None, mode: str = "subagent-batch",
                      max_workers: int = 1, block_size: int | None = None,
                      model_caps: dict[str, int] | None = None,
                      transcript_bundle_dir: str | Path | None = None,
                      pending_payload_limit: int | None = DEFAULT_PENDING_PAYLOAD_LIMIT,
                      context_blocklist_path: str | Path | None = None,
                      ) -> RunOutcome:
    """Execute the authorized phase-3 canary. The only entry point here that can spend money.

    Fail-closed launch gates, all checked before a single cell runs: the manifest validates
    (:func:`load_and_validate_manifest`); the authorization record exists, authorizes
    execution, and binds THIS manifest's identity (:func:`load_phase3_canary_authorization`);
    accounted spend has not already reached the authorized canary cap
    (:func:`build_phase3_client`).

    ``mode="subagent-batch"`` (the default, matching phase 2's own dominant operational mode)
    never consults a live reviewer inline: an unlabelled payload pauses its cell, and once a
    pass exhausts what CAN run without new labels, the accumulated payloads are exported as a
    worklist (``scripts/review_daemon.py`` picks them up, exactly as phase 2's did).
    ``mode="api"`` requires an explicit ``reviewer`` callable (offline tests only; this module
    does not construct a live gate-reviewer client itself).

    A ``GenerationForbiddenError`` halt is never returned as a soft outcome: it means a
    transcript-reference cell reached the executor unseeded, which pre-seeding
    (``scripts/phase3_preseed_transcripts.py``) should have made impossible, so this function
    re-raises it as a hard crash rather than letting a caller mistake it for a resumable halt.

    **The 2026-08-18 canary stall, and the two fixes for it.** The stall watchdog killed a live
    invocation after 1,808s of total silence (zero new result rows, no errors, no exit) while
    ~12 b0 cells, all 576 budget-smoke cells, and all 288 capability_qa cells still remained.
    Root cause, confirmed by reading ``rejudge.phase2_canary_gate``/``phase2_dual_gate`` in
    full: NOT an in-process wait for labels (``CanaryQueryGate._ensure_reviewer_decision``
    raises ``PendingReviewerDecision`` immediately when unlabeled, no retry loop exists) and
    NOT the block scheduler (this module's CLI has no ``--max-workers`` flag, so the live run
    was necessarily the serial ``max_workers=1`` path, never
    ``rejudge.phase2_canary_runner._run_concurrent``). Two real, separate problems instead:
    (1) THIS function used to gate the capability-cell phase on ``not outcome.needs_labelling``
    -- so the moment even ONE judgment cell paused, all 288 gate-independent capability_qa
    cells were skipped outright, every single invocation, regardless of how much schedulable
    work remained. Fixed below: the capability phase now runs whenever the judgment pass did
    not fatally halt, full stop.
    (2) the serial pass has no bound on how much real wall-clock time it spends before
    returning: a query-producing cell that pauses still needs ONE real, uncached judge_query
    call first (pausing records nothing), so a pass reaching a long run of never-before-attempted
    smoke cells burns provider time with zero rows to show for any of them, and if even a
    handful land on a degraded model (as gemma-4-31B was, per the one logged timeout) the whole
    pass's wall-clock exposure balloons with no interim checkpoint. Fixed via
    ``pending_payload_limit``, threaded into ``run_canary``: once a pass accumulates that many
    NEW pending payloads it stops attempting further not-yet-complete judgment/transcript
    cells and returns -- bounding worst-case exposure to a fixed batch instead of the entire
    remaining query-producing set, and letting the orchestrator relaunch after a smaller,
    faster review wave.

    **The 2026-08-19 ``ContextGuardError`` halt, and the ex-ante fix for it.**
    ``ContextGuardError`` on a long, uncapped b8 transcript is not a bug (truncation is a
    zero-tolerance gate; it fired correctly), so the fix is a deterministic EXCLUSION decided
    offline before any live run, never mid-run discretion --
    ``scripts/phase3_context_precheck.py`` computes it.

    ``context_blocklist_path`` is an OPTIONAL OVERRIDE, not a switch that turns exclusion on or
    off (second re-review, 2026-08-19): every post-amendment-4 manifest binds its regenerated
    canary eligibility list as a REQUIRED part of its own identity
    (``frozen_inputs.context_blocklist_canary_report_sha256``/``..._tracked_path``), so there is
    no valid "run without a blocklist" mode against a manifest that binds one. Left at its
    default of ``None``, the bound blocklist is resolved and applied automatically from the
    manifest's own tracked path -- omitting the flag can never silently mean "run unfiltered."
    An explicitly supplied path is still accepted, but only as a caller-visible confirmation of
    the SAME file: it must canonically hash-match the manifest's binding, exactly like the
    auto-resolved path does, or the run refuses outright. Only a genuinely pre-amendment-4
    manifest (no such binding recorded at all) runs with no exclusions when no path is supplied
    -- there is nothing to resolve automatically in that case, and an explicitly supplied path
    against it is refused rather than trusted blind.

    Either way, :func:`load_context_blocklist` still namespace-verifies the resolved blocklist
    and removes every listed cell from the judgment plan BEFORE ``run_canary`` ever sees it -- a
    blocklisted cell is never attempted and never counted as completed, only as
    ``context_blocked`` on the returned outcome (alongside the blocklist's own canonical
    sha256, for audit). A cell NOT on the blocklist that still raises ``ContextGuardError`` is
    left to the EXISTING generic-exception halt path unchanged: the precheck claimed
    completeness, so a miss there is the precheck being wrong, and must still halt the run
    loudly, exactly as any other unmodelled exception does.
    """
    if mode not in ("api", "subagent-batch"):
        raise Phase3RunnerError(f"unknown mode {mode!r}")
    manifest = load_and_validate_manifest(
        manifest_path, project_root=project_root, transcript_bundle_dir=transcript_bundle_dir)
    authorization = load_phase3_canary_authorization(authorization_path, manifest)
    canary_cap_usd = float(authorization["scope"]["canary_cap_usd"])

    root = Path(project_root)
    protocol = phase3_plan.load_protocol(root / str(manifest["protocol_tracked_path"]))
    bundle = _load_json(root / PHASE2_PROMPT_BUNDLE_RELATIVE_PATH)
    role_limits = _load_json(root / str(manifest["frozen_inputs"]["role_limits_tracked_path"]))
    roster_judges = list(manifest["roster"]["judges"])
    namespace = str(protocol["cell_key_namespace"])

    context_blocklist_sha256 = None
    context_blocked_keys: frozenset[str] = frozenset()
    bound_sha256 = manifest["frozen_inputs"].get("context_blocklist_canary_report_sha256")

    resolved_blocklist_path = context_blocklist_path
    if resolved_blocklist_path is None and bound_sha256 is not None:
        # Second re-review (2026-08-19), blocker 2: the manifest ALWAYS binds a canary
        # eligibility list now (build_manifest requires it) -- there is NO VALID no-blocklist
        # mode against a binding manifest. A caller that omits --context-blocklist must still
        # get the bound exclusions applied, resolved from the manifest's own tracked path,
        # never silently run unfiltered because a flag happened to be left off.
        tracked_path = manifest["frozen_inputs"].get(
            "context_blocklist_canary_report_tracked_path")
        if not tracked_path:
            raise Phase3RunnerError(
                "manifest binds context_blocklist_canary_report_sha256 but no "
                "context_blocklist_canary_report_tracked_path to resolve it from; the "
                "manifest is internally inconsistent")
        resolved_blocklist_path = root / str(tracked_path)

    if resolved_blocklist_path is not None:
        # A namespace match alone let an ARBITRARY blocklist file run under this manifest's
        # identity (the pre-amendment 72-exclusion file and the regenerated zero-exclusion one
        # both name the same namespace). The resolved file's CANONICAL sha256 must match the
        # one this manifest actually bound at build time -- refusing outright if the manifest
        # carries no such binding at all (a pre-amendment-4 manifest shape) and a caller
        # nonetheless supplied one explicitly, since there would be nothing to verify it
        # against.
        if bound_sha256 is None:
            raise Phase3RunnerError(
                "--context-blocklist was supplied but this manifest binds no "
                "frozen_inputs.context_blocklist_canary_report_sha256 at all; refusing to "
                "trust a runtime blocklist file this manifest never pinned")
        blocklist, _raw_sha256 = load_context_blocklist(resolved_blocklist_path, protocol)
        context_blocklist_sha256 = phase3_manifest.canonical_sha256(blocklist)
        if context_blocklist_sha256 != bound_sha256:
            raise Phase3RunnerError(
                f"context blocklist {resolved_blocklist_path} does not match this manifest's "
                f"bound canary eligibility list: observed {context_blocklist_sha256}, expected "
                f"{bound_sha256}")
        context_blocked_keys = frozenset(
            str(entry["cell_key"]) for entry in blocklist["excluded"])

    _main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, root)
    plan_cells = phase3_plan.enumerate_canary_cells(protocol, roster_judges, held_out_ids)
    judgment_cells, capability_cells = resolve_canary_cells(
        plan_cells, protocol=protocol, bundle=bundle)

    context_blocked_count = 0
    if context_blocked_keys:
        before = len(judgment_cells)
        judgment_cells = [cell for cell in judgment_cells
                          if cell.cell_key not in context_blocked_keys]
        # Counts only cells THIS plan actually contained -- a blocklist computed against a
        # different roster/scope could name keys with no match here at all, and that must
        # never be silently reported as if they were excluded from THIS run.
        context_blocked_count = before - len(judgment_cells)

    archive_dir = local_path(manifest["ledger"]["archive_dir"])
    results_path = local_path(manifest["ledger"]["canary_results_path"])
    decisions_path = local_path(manifest["ledger"]["decisions_path"])
    usage_log_path = local_path(manifest["ledger"]["usage_log_path"])
    call_cache_path = local_path(manifest["ledger"]["call_cache_path"])
    error_log_path = archive_dir / "phase3_canary_error_log.jsonl"

    if client is None:
        client = build_phase3_client(
            manifest, project_root=root, canary_cap_usd=canary_cap_usd,
            usage_log_path=usage_log_path, error_log_path=error_log_path,
            call_cache_path=call_cache_path)

    if reviewer is None:
        if mode != "subagent-batch":
            raise Phase3RunnerError(
                "mode='api' requires an explicit reviewer; this driver does not construct a "
                "live gate-review client on its own")
        reviewer = _PauseModeReviewer()

    outcome = run_canary(
        results_path=results_path, decisions_path=decisions_path, client=client,
        reviewer=reviewer, anchor_judge_model="", protocol=protocol, bundle=bundle,
        pause_when_unlabeled=(mode == "subagent-batch"), limit=limit, cells=judgment_cells,
        max_workers=max_workers, block_size=block_size, model_caps=model_caps,
        transcript_generation_forbidden=True, namespace=namespace,
        pending_payload_limit=pending_payload_limit, role_limits=role_limits)
    outcome.context_blocked = context_blocked_count
    outcome.context_blocklist_sha256 = context_blocklist_sha256

    if outcome.halted_reason == "GenerationForbiddenError":
        raise GenerationForbiddenError(
            f"cell {outcome.halted_cell_key} reached the executor unseeded under "
            "transcript_generation_forbidden=True; pre-seeding is incomplete or a transcript "
            "dependency was never recorded. Run scripts/phase3_preseed_transcripts.py before "
            "starting a real run. This is a crash, not a resumable halt.")

    # Schedulable, gate-independent work (capability_qa) must run whenever the judgment pass
    # did not fatally halt -- REGARDLESS of outcome.needs_labelling. Gating this on "no pending
    # labels" (the pre-2026-08-18-fix behavior) meant a single paused judgment cell silently
    # blocked all 288 capability cells, every invocation, even though they share no gate, no
    # dependency, and no reviewer with the judgment pass at all.
    if outcome.halted_reason is None:
        # A FRESH store, opened only now: run_canary above owns (and has already closed) its
        # own independent CellResultStore instance over the same file, appending every
        # judgment/transcript row to the on-disk hash chain. A store instance opened any
        # earlier would carry a stale in-memory chain tip (sequence/prev_event_hash) from
        # before those rows existed, and every capability row it then recorded would corrupt
        # the chain by pointing at that stale tip instead of the real one.
        store = CellResultStore(results_path)
        base_max_tokens = int(role_limits["base_role_max_tokens"][CAPABILITY_QA_ROLE])
        capability_context = CellContext(
            client=client, protocol=protocol, bundle=bundle,
            decision_store=DualGateDecisionStore(decisions_path), reviewer=reviewer,
            anchor_judge_model="", results={}, pause_when_unlabeled=False,
            transcript_generation_forbidden=True)
        cap_outcome = run_capability_cells(
            capability_cells, context=capability_context, store=store,
            base_max_tokens=base_max_tokens, limit=limit)
        outcome = _merge_outcomes(outcome, cap_outcome)

    if outcome.needs_labelling and mode == "subagent-batch":
        frozen_prompt = load_frozen_reviewer_prompt(manifest, project_root=root)
        worklist_path = archive_dir / WORKLIST_FILENAME
        export_reviewer_worklist(outcome.pending_payloads, frozen_prompt["prompt"], worklist_path)

    return outcome


# --- CLI -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase3_runner")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--mode", choices=("api", "subagent-batch"), default="subagent-batch")
    parser.add_argument("--limit", type=int, default=None,
                        help="attempt at most N incomplete cells per phase this invocation "
                             "(smoke)")
    parser.add_argument("--commit-decisions", default=None,
                        help="commit a JSON file of out-of-band reviewer outputs and exit")
    parser.add_argument("--pending-payload-limit", type=int,
                        default=DEFAULT_PENDING_PAYLOAD_LIMIT,
                        help="stop attempting further not-yet-complete cells once this many "
                             "NEW pending payloads have accumulated this pass (bounds "
                             "worst-case wall-clock exposure); 0 or negative disables the bound")
    parser.add_argument("--context-blocklist", default=None,
                        help="scripts/phase3_context_precheck.py output; excluded cells are "
                             "skipped entirely (never attempted, never counted as completed). "
                             "OPTIONAL OVERRIDE ONLY: a manifest that binds a canary blocklist "
                             "(every post-amendment-4 manifest does) has it resolved and "
                             "applied automatically even when this flag is omitted; an "
                             "explicitly supplied path must still hash-match that binding")
    args = parser.parse_args(argv)
    try:
        if args.commit_decisions is not None:
            counts = commit_reviewer_decisions(
                args.manifest, args.commit_decisions, args.project_root)
            print(json.dumps(counts, sort_keys=True))
            return 0
        pending_payload_limit = (args.pending_payload_limit
                                 if args.pending_payload_limit and args.pending_payload_limit > 0
                                 else None)
        outcome = run_phase3_canary(
            args.manifest, args.authorization, args.project_root, limit=args.limit,
            mode=args.mode, pending_payload_limit=pending_payload_limit,
            context_blocklist_path=args.context_blocklist)
    except Exception as exc:  # noqa: BLE001 - report, never swallow
        print(f"REFUSED/HALTED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "completed": outcome.completed, "skipped": outcome.skipped,
        "deferred": outcome.deferred, "paused": outcome.paused,
        "pending_labels": len(outcome.pending_payloads),
        "halted_reason": outcome.halted_reason,
        "halted_cell_key": outcome.halted_cell_key,
        "context_blocked": outcome.context_blocked,
        "context_blocklist_sha256": outcome.context_blocklist_sha256}, sort_keys=True))
    return 0 if outcome.halted_reason is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
