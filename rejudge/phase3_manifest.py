"""The phase-3 execution manifest, and its validator.

A sibling of :mod:`rejudge.phase2_canary_manifest` and :mod:`rejudge.phase2_main_manifest`,
not an extension of either: phase 3 has its own frozen protocol
(``rejudge/phase3_protocol.json``), its own plan enumerator (:mod:`rejudge.phase3_plan`), its
own role-limits artifact, and a roster that is not fixed in the protocol at all -- it is only
known after the phase-3 canary calibration gates run (``roster.new_judge_failure_rule``), so
this module's manifest binds whichever roster the caller supplies rather than reading one out
of the protocol.

One manifest, not two. Phase 2 split canary and main because their grids, caps and governance
differed. Phase 3's canary and main share the same frozen protocol, the same reused prompt
bundle, the same role limits, the same provider snapshot and the same roster: the only thing
that differs between the two sub-stages is which cells run when, and that is exactly what
``planning.main``/``planning.canary`` already separate inside one manifest.

**The protocol path is always injected, never hard-coded.** Phase 2's manifest modules read
``rejudge/phase2_protocol.json`` off a literal path in three places
(:func:`rejudge.phase2_canary_manifest._frozen_inputs`,
:func:`rejudge.phase2_main_manifest.main_question_ids`,
:func:`rejudge.phase2_main_manifest.enumerate_main_cells`), which means the only way to point
either module at a different (e.g. tampered, for a test) protocol file is to replace the real
one on disk. :func:`build_manifest` takes ``protocol_path`` as its first argument instead, and
every read of the frozen protocol inside this module goes through that one value.

**Zero live transcript generation.** Phase 3 makes ZERO debater calls
(``transcript_reuse.policy``): every judged transcript is a phase-2 uncapped blind debate
reused byte-identically, pre-seeded into the hash-chained result store by
``scripts/phase3_preseed_transcripts.py``. The manifest carries a dedicated
``transcript_generation_forbidden: true`` flag so the executor
(:mod:`rejudge.phase2_canary_execute`) can refuse a transcript-generation cell before it ever
reaches a provider call, independent of whether the pre-seeding actually happened to cover it.

The manifest authorizes nothing. Its purpose is to pin exactly what a later, separate
authorization record refers to by hash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from rejudge import phase3_plan
from rejudge.phase2_canary_live import local_path
from rejudge.phase2_execution import canonical_sha256
from rejudge.phase2_role_limits import HTTP_TIMEOUT_KEYS, TRANSPORT_KEYS_V5

SCHEMA_VERSION = "phase3_execution_manifest_v1"
STAGE = "phase3"

# Frozen pins for the phase-2 artifacts phase 3 REUSES byte-identically. Unlike the role-limits
# and provider-snapshot artifacts below (both still "draft_pending_manifest_binding" at the
# time this module was written, and expected to change), these three are settled phase-2
# outputs: pinning their exact hash here means a tampered copy of any of them fails closed even
# in a repo where frozen_inputs would otherwise be internally consistent (build vs. rebuild
# alone cannot catch a file that was wrong from the start).
REUSED_PROMPT_BUNDLE_SHA256 = (
    "cc02d29cfc8e7410c270c21f53da56457e44c31f74f8e512299e4e80726a076f")
CHECKER_FROZEN_CONFIG_SHA256 = (
    "8e674eddbb22ba73ee5a4ae4f359f1630cf4cd65c5f5d98cb70312dc868b9872")
REVIEWER_PROMPT_SHA256 = (
    "b48a125af874b287aa93e73671c9c12c9d8c4245d50e61013c690209139815f4")

# Owner-authorized roster-candidate substitutions, applied on top of the immutable frozen
# protocol's candidate list: (tracked_path, canonical_sha256). Append-only, like the records
# they bind.
ROSTER_AMENDMENTS: tuple[tuple[str, str], ...] = (
    ("rejudge/phase3_amendment1_roster_2026-08-18.json",
     "a99e2b100d089011e28057be484d19565e4b401bc943b23d554c26d352ad34d3"),
)

PHASE2_PROMPT_BUNDLE_RELATIVE_PATH = Path("rejudge/phase2_prompt_bundle.json")
PHASE2_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH = Path(
    "rejudge/phase2_prompt_bundle_approval_2026-07-18.json")
CHECKER_FROZEN_CONFIG_RELATIVE_PATH = Path(
    "rejudge/phase2_checker_frozen_config_2026-07-23.json")
REVIEWER_PROMPT_RELATIVE_PATH = Path("rejudge/phase2_reviewer_prompt_2026-07-23.json")
ROLE_LIMITS_RELATIVE_PATH = Path("rejudge/phase3_role_limits_v1_2026-08-18.json")
PROVIDER_SNAPSHOT_RELATIVE_PATH = Path("rejudge/phase3_provider_snapshot_2026-08-18.json")
TRANSCRIPT_VERIFICATION_RELATIVE_PATH = Path(
    "rejudge/phase3_transcript_verification_2026-08-18.json")
MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH = Path(
    "rejudge/phase3_transcript_bundle_main_2026-08-18.json")
CANARY_TRANSCRIPT_BUNDLE_RELATIVE_PATH = Path(
    "rejudge/phase3_transcript_bundle_canary_2026-08-18.json")

# The phase-3 code bundle. Deliberately disjoint in PURPOSE from
# rejudge.phase2_canary_manifest.CANARY_CODE_PROVENANCE_FILES even where the file lists
# overlap (judge_loop.py, debate_gen.py, ... execute the shared judgment machinery both phases
# dispatch through): this is a SEPARATE hash over a list this module owns, so a phase-3-only
# change (e.g. to phase3_plan.py) never disturbs a phase-2 manifest already bound and executed,
# and vice versa. Order is part of the hash.
PHASE3_CODE_PROVENANCE_FILES: tuple[str, ...] = (
    "rejudge/phase3_plan.py",
    "rejudge/phase3_manifest.py",
    "rejudge/phase3_runner.py",
    "rejudge/phase2_canary_runner.py",
    "rejudge/phase2_canary_live.py",
    "rejudge/phase2_canary_cells.py",
    "rejudge/phase2_canary_compose.py",
    "rejudge/phase2_canary_gate.py",
    "rejudge/phase2_dual_gate.py",
    "rejudge/phase2_canary_order.py",
    "rejudge/phase2_canary_execute.py",
    "rejudge/judge_loop.py",
    "rejudge/debate_gen.py",
    "rejudge/composer.py",
    "rejudge/oracle_channel.py",
    "rejudge/query_screen.py",
    "rejudge/parsers.py",
    "rejudge/records.py",
    "rejudge/config.py",
    "rejudge/phase2_query_gate.py",
    "rejudge/phase2_call_cache.py",
    "rejudge/phase2_caching_client.py",
    "rejudge/api_client.py",
    "rejudge/run_accounting.py",
    "scripts/phase3_preseed_transcripts.py",
    # Amendment 4 (2026-08-19), package item 6: the reviewer flagged that the prior provenance
    # list omitted the precheck and orchestrator scripts even though both execute logic that
    # decides which cells run (the precheck's context-exclusion arithmetic; the orchestrator's
    # completion-control worklist computation) -- see
    # rejudge/phase3_codex_context_guard_consult_2026-08-19.md, "4."
    "scripts/phase3_context_precheck.py",
    "scripts/phase3_canary_orchestrator.sh",
)

MANIFEST_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "stage", "recorded_at_utc", "protocol_tracked_path", "planning",
    "frozen_inputs", "roster", "caps", "ledger", "code_provenance", "resume_granularity",
    "transcript_generation_forbidden", "execution_authorized", "execution_identity_sha256",
})


class ManifestValidationError(ValueError):
    """Raised when a phase-3 manifest does not validate. Always fails closed."""


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    return str(path).replace("\\", "/")


def phase3_code_bundle_sha256(project_root: str | Path = ".") -> str:
    """Hash the phase-3 provenance files as an ordered list of raw-byte hashes.

    Raw bytes, not canonical JSON: these are Python sources. Mirrors
    :func:`rejudge.phase2_canary_manifest.canary_code_bundle_sha256` exactly, over a disjoint
    file list this module owns (see :data:`PHASE3_CODE_PROVENANCE_FILES`).
    """
    root = Path(project_root)
    entries = [{"path": relative, "sha256": _raw_sha256(root / relative)}
               for relative in PHASE3_CODE_PROVENANCE_FILES]
    return canonical_sha256(entries)


def _require_transport_pins(role_limits_payload: Mapping[str, Any], *,
                            tracked_path: str) -> None:
    """Refuse a role-limits artifact whose ``request_settings.transport`` omits the v5 pins.

    Equivalent to :func:`rejudge.phase2_canary_manifest._require_transport_pins`: same shape
    check (the transport section carries exactly :data:`TRANSPORT_KEYS_V5`, and its
    ``http_timeout`` carries exactly :data:`HTTP_TIMEOUT_KEYS`), reusing those constants rather
    than re-deriving what "complete" means. A distinct function, not an import of the phase-2
    one, so this module's callers only ever need to catch this module's own
    :class:`ManifestValidationError` -- never a sibling manifest module's exception type.
    """
    transport = (role_limits_payload.get("request_settings") or {}).get("transport")
    if not isinstance(transport, Mapping) or set(transport) != TRANSPORT_KEYS_V5:
        raise ManifestValidationError(
            f"{tracked_path} does not carry the v5-shape request_settings.transport section "
            f"(expected exactly {sorted(TRANSPORT_KEYS_V5)!r}); refusing to bind a role-limits "
            "artifact that would leave a live run with no explicit transport pins")
    http_timeout = transport.get("http_timeout")
    if not isinstance(http_timeout, Mapping) or set(http_timeout) != HTTP_TIMEOUT_KEYS:
        raise ManifestValidationError(
            f"{tracked_path}'s request_settings.transport.http_timeout does not carry exactly "
            f"{sorted(HTTP_TIMEOUT_KEYS)!r}; refusing to bind an incomplete timeout pin")


def _require_roster_role_limits(role_limits_payload: Mapping[str, Any],
                                roster_judges: Sequence[str], *, tracked_path: str) -> None:
    """Refuse a roster carrying any model absent from ``model_role_limits``.

    A model listed only under ``unavailable_candidates`` (e.g.
    ``meta-llama/Meta-Llama-3-8B-Instruct-Lite`` as of the 2026-08-18 provider snapshot) has no
    per-role token limits, reasoning-model classification, or request-field pins at all; binding
    it into a manifest's roster anyway would leave a live run to dispatch that model with no
    explicit configuration. This is the "for EVERY model in the manifest roster" half of the
    transport-pins check above: that check proves the shared transport section is well-shaped
    once; this proves every roster model actually has role limits to use it under.
    """
    model_role_limits = role_limits_payload.get("model_role_limits")
    if not isinstance(model_role_limits, Mapping):
        raise ManifestValidationError(f"{tracked_path} is missing model_role_limits")
    missing = sorted(model for model in roster_judges if model not in model_role_limits)
    if missing:
        raise ManifestValidationError(
            f"{tracked_path} carries no model_role_limits entry for roster judge(s) "
            f"{missing!r}; every model in the manifest roster must have role limits bound "
            "before it can be dispatched")


def _apply_roster_amendments(root: Path, allowed: set[str]) -> list[dict[str, Any]]:
    """Apply owner-authorized candidate substitutions to the allowed-judge set.

    The frozen protocol is immutable, so an availability-driven candidate substitution lives
    in an append-only amendment record instead. Each entry here is re-read and hash-verified
    from disk, its ``from`` candidate is retired from the allowed set and its ``to`` candidate
    admitted, and the binding (path + hash + mapping) is returned for the manifest to record.
    """
    bindings = []
    for relative, expected_sha in ROSTER_AMENDMENTS:
        payload = _json(root / relative)
        observed = canonical_sha256(payload)
        if observed != expected_sha:
            raise ManifestValidationError(
                f"{relative} does not match its pinned hash: observed {observed}, "
                f"expected {expected_sha}")
        retired = str(payload["what_changed"]["from"]).split(" (")[0]
        admitted = str(payload["what_changed"]["to"]).split(" (")[0]
        allowed.discard(retired)
        allowed.add(admitted)
        bindings.append({"tracked_path": relative, "canonical_sha256": observed,
                         "retired_candidate": retired, "admitted_candidate": admitted})
    return bindings


def _validate_roster_against_protocol(protocol: Mapping[str, Any],
                                      roster_judges: Sequence[str],
                                      root: Path) -> list[dict[str, Any]]:
    if not roster_judges:
        raise ManifestValidationError("roster_judges must be non-empty")
    if len(roster_judges) != len(set(roster_judges)):
        raise ManifestValidationError("roster_judges contains duplicates")
    roster = protocol["roster"]
    continuing = set(roster["judges_continuing"])
    new_candidates = {str(candidate["candidate_model_id"]) for candidate in roster["judges_new"]}
    allowed = continuing | new_candidates
    amendment_bindings = _apply_roster_amendments(root, allowed)
    unknown = sorted(model for model in roster_judges if model not in allowed)
    if unknown:
        raise ManifestValidationError(
            f"roster judge(s) {unknown!r} are not among the frozen protocol's "
            "roster.judges_continuing or roster.judges_new candidates as amended")
    return amendment_bindings


def _load_phase2_protocol_verified(root: Path, protocol: Mapping[str, Any]) -> dict[str, Any]:
    """Load ``rejudge/phase2_protocol.json``, re-verified against phase 3's own source binding.

    Never trusted from the bound hash alone: ``source_bindings.canonical_json_sha256`` names
    the expected hash, and this recomputes it from the real file on disk before returning it,
    matching :func:`rejudge.phase3_plan.load_reference_question_ids`'s own re-verification.
    """
    relative = str(protocol["sources"]["phase2_protocol"])
    expected = protocol["source_bindings"]["canonical_json_sha256"][relative]
    payload = _json(root / relative)
    observed = canonical_sha256(payload)
    if observed != expected:
        raise ManifestValidationError(
            f"{relative} hash drift: observed {observed}, expected {expected}")
    return payload


def _question_bank_bundle_sha256(root: Path, phase2_protocol: Mapping[str, Any]) -> str:
    """Recompute the canonical question-bank bundle hash from the real bank files on disk.

    Mirrors the inline check inside :func:`rejudge.phase2_plan.validate_protocol` (the same
    ``{path: payload}`` construction over ``question_set.question_sources``), so phase 3's
    "same three banks, same hashes" claim (``question_set.identical_to``) is checked against
    the actual bank content, not merely against the hash phase 3's own protocol happens to
    quote.
    """
    paths = phase2_protocol["question_set"]["question_sources"]
    bundle = {path: _json(root / path) for path in paths}
    return canonical_sha256(bundle)


def _resolve_transcript_bundle_path(
    root: Path, relative_path: Path, transcript_bundle_dir: str | Path | None,
) -> Path:
    """Resolve one transcript bundle's real path for this build/validate call.

    ``transcript_bundle_dir=None`` keeps today's behavior (``project_root``-relative, e.g. for
    the synthetic-fixture tests): the bundle resolves at ``root / relative_path``. A caller that
    supplies ``transcript_bundle_dir`` (e.g. the archive directory the two bundles were moved
    to, out of the repo) gets ``<transcript_bundle_dir>/<relative_path.name>`` instead --
    ``project_root`` never enters into that resolution.
    """
    if transcript_bundle_dir is None:
        return root / relative_path
    return Path(transcript_bundle_dir) / relative_path.name


def _frozen_inputs(root: Path, protocol: Mapping[str, Any],
                   roster_judges: Sequence[str], *,
                   transcript_bundle_dir: str | Path | None,
                   estimator_validation_path: str | Path,
                   context_blocklist_canary_path: str | Path,
                   context_blocklist_main_path: str | Path) -> dict[str, Any]:
    phase2_protocol = _load_phase2_protocol_verified(root, protocol)

    observed_bank_sha = _question_bank_bundle_sha256(root, phase2_protocol)
    expected_bank_sha = protocol["planning_cell_identity"]["question_bank_bundle_sha256"]
    if observed_bank_sha != expected_bank_sha:
        raise ManifestValidationError(
            "question-bank bundle hash mismatch: observed "
            f"{observed_bank_sha}, expected {expected_bank_sha}")

    prompt_bundle = _json(root / PHASE2_PROMPT_BUNDLE_RELATIVE_PATH)
    prompt_bundle_sha = canonical_sha256(prompt_bundle)
    if prompt_bundle_sha != REUSED_PROMPT_BUNDLE_SHA256:
        raise ManifestValidationError(
            f"{_rel(PHASE2_PROMPT_BUNDLE_RELATIVE_PATH)} does not match the frozen phase-2 "
            f"pin: observed {prompt_bundle_sha}, expected {REUSED_PROMPT_BUNDLE_SHA256}")
    prompt_bundle_approval = _json(root / PHASE2_PROMPT_BUNDLE_APPROVAL_RELATIVE_PATH)

    role_limits_tracked_path = _rel(ROLE_LIMITS_RELATIVE_PATH)
    role_limits = _json(root / ROLE_LIMITS_RELATIVE_PATH)
    _require_transport_pins(role_limits, tracked_path=role_limits_tracked_path)
    _require_roster_role_limits(
        role_limits, roster_judges, tracked_path=role_limits_tracked_path)

    provider_snapshot = _json(root / PROVIDER_SNAPSHOT_RELATIVE_PATH)

    checker = _json(root / CHECKER_FROZEN_CONFIG_RELATIVE_PATH)
    checker_sha = canonical_sha256(checker)
    if checker_sha != CHECKER_FROZEN_CONFIG_SHA256:
        raise ManifestValidationError(
            f"{_rel(CHECKER_FROZEN_CONFIG_RELATIVE_PATH)} does not match the frozen phase-2 "
            f"pin: observed {checker_sha}, expected {CHECKER_FROZEN_CONFIG_SHA256}")

    reviewer_prompt = _json(root / REVIEWER_PROMPT_RELATIVE_PATH)
    reviewer_prompt_sha = reviewer_prompt.get("prompt_sha256")
    if reviewer_prompt_sha != REVIEWER_PROMPT_SHA256:
        raise ManifestValidationError(
            f"{_rel(REVIEWER_PROMPT_RELATIVE_PATH)} does not match the frozen phase-2 pin: "
            f"observed {reviewer_prompt_sha!r}, expected {REVIEWER_PROMPT_SHA256!r}")

    verification_report = _json(root / TRANSCRIPT_VERIFICATION_RELATIVE_PATH)
    expected_bundle_hashes = verification_report["bundle_canonical_sha256"]

    main_bundle_path = _resolve_transcript_bundle_path(
        root, MAIN_TRANSCRIPT_BUNDLE_RELATIVE_PATH, transcript_bundle_dir)
    # The resolved path string is the authoritative recorded binding; only filesystem access
    # is host-translated (E:/ vs /mnt/e), so a manifest built on Windows validates under WSL
    # with an identical frozen_inputs dict.
    main_bundle = _json(local_path(str(main_bundle_path)))
    main_bundle_sha = canonical_sha256(main_bundle)
    if main_bundle_sha != expected_bundle_hashes["main_bundle"]:
        raise ManifestValidationError(
            f"{_rel(main_bundle_path)} does not match the verification "
            f"report's pinned hash: observed {main_bundle_sha}, expected "
            f"{expected_bundle_hashes['main_bundle']}")

    canary_bundle_path = _resolve_transcript_bundle_path(
        root, CANARY_TRANSCRIPT_BUNDLE_RELATIVE_PATH, transcript_bundle_dir)
    canary_bundle = _json(local_path(str(canary_bundle_path)))
    canary_bundle_sha = canonical_sha256(canary_bundle)
    if canary_bundle_sha != expected_bundle_hashes["canary_bundle"]:
        raise ManifestValidationError(
            f"{_rel(canary_bundle_path)} does not match the verification "
            f"report's pinned hash: observed {canary_bundle_sha}, expected "
            f"{expected_bundle_hashes['canary_bundle']}")

    # Amendment 4 (2026-08-19), package items 3 and 6: bound the SAME way as
    # transcript_verification_report above (path + canonical sha, re-read and re-hashed from
    # disk here, never trusted from a caller-supplied hash) -- REQUIRED, never null-tolerant:
    # a successor manifest with no validated estimator is not a manifest this codebase should
    # be able to build at all (see the consult, "4.": "successor identity should bind ...
    # Frozen ledger prefix and validation report").
    estimator_validation_relative = _rel(Path(estimator_validation_path))
    estimator_validation_report = _json(root / estimator_validation_path)

    # Codex re-review (2026-08-19), blocker 2: amendment item 6's "regenerated eligibility
    # list" was never bound to the successor identity at all -- phase3_runner.py accepted an
    # ARBITRARY --context-blocklist file, checking only its namespace, so the same manifest
    # identity could run with zero, 72 (the pre-amendment blocklist), or no exclusions. Bound
    # the SAME way as estimator_validation_path above: REQUIRED, path + canonical sha256,
    # re-read and re-hashed from disk here. Both scopes are bound even though only the canary
    # scope has a runtime consumer today (phase 3 main execution is not yet built) -- the
    # eligibility PIN is a build-time identity property, independent of which stage runs it.
    context_blocklist_canary_relative = _rel(Path(context_blocklist_canary_path))
    context_blocklist_canary = _json(root / context_blocklist_canary_path)
    context_blocklist_main_relative = _rel(Path(context_blocklist_main_path))
    context_blocklist_main = _json(root / context_blocklist_main_path)

    return {
        "protocol_sha256": canonical_sha256(protocol),
        "phase2_protocol_sha256": canonical_sha256(phase2_protocol),
        "question_bank_bundle_sha256": observed_bank_sha,
        "prompt_bundle_sha256": prompt_bundle_sha,
        "prompt_bundle_approval_sha256": canonical_sha256(prompt_bundle_approval),
        "role_limits_sha256": canonical_sha256(role_limits),
        "role_limits_tracked_path": role_limits_tracked_path,
        "provider_snapshot_sha256": canonical_sha256(provider_snapshot),
        "provider_snapshot_tracked_path": _rel(PROVIDER_SNAPSHOT_RELATIVE_PATH),
        "checker_frozen_config_sha256": checker_sha,
        "checker_system_prompt_sha256": checker["configuration"]["system_prompt_sha256"],
        "checker_model": checker["configuration"]["model"],
        "reviewer_prompt_sha256": reviewer_prompt_sha,
        "main_transcript_bundle_sha256": main_bundle_sha,
        "main_transcript_bundle_path": _rel(main_bundle_path),
        "canary_transcript_bundle_sha256": canary_bundle_sha,
        "canary_transcript_bundle_path": _rel(canary_bundle_path),
        "transcript_verification_report_sha256": canonical_sha256(verification_report),
        "transcript_verification_report_tracked_path": _rel(
            TRANSCRIPT_VERIFICATION_RELATIVE_PATH),
        "estimator_validation_report_sha256": canonical_sha256(estimator_validation_report),
        "estimator_validation_report_tracked_path": estimator_validation_relative,
        "context_blocklist_canary_report_sha256": canonical_sha256(context_blocklist_canary),
        "context_blocklist_canary_report_tracked_path": context_blocklist_canary_relative,
        "context_blocklist_main_report_sha256": canonical_sha256(context_blocklist_main),
        "context_blocklist_main_report_tracked_path": context_blocklist_main_relative,
    }


def build_manifest(protocol_path: str | Path, *, project_root: str | Path = ".",
                   recorded_at_utc: str, archive_dir: str,
                   roster_judges: Sequence[str],
                   estimator_validation_path: str | Path,
                   context_blocklist_canary_path: str | Path,
                   context_blocklist_main_path: str | Path,
                   transcript_bundle_dir: str | Path | None = None) -> dict[str, Any]:
    """Assemble the phase-3 manifest.

    ``protocol_path`` is INJECTED: every read of the frozen protocol inside this function (and
    everything it calls) flows from this one argument, never from a literal path baked into the
    module. ``project_root`` anchors every OTHER bound artifact (role limits, provider
    snapshot, checker config, reviewer prompt) and, when ``transcript_bundle_dir`` is left at its
    default of ``None``, the two transcript bundles too, matching the phase-2 manifest modules'
    convention -- the two are independent, so a caller can point ``protocol_path`` at a tampered
    copy while every sibling artifact still resolves under the real repo, or vice versa.

    ``transcript_bundle_dir`` overrides where the two transcript bundles resolve from:
    ``rejudge/phase3_transcript_bundle_{main,canary}_2026-08-18.json`` no longer live in the
    repo (they were moved to an archive directory to keep eval-world debate text out of version
    control), so a caller binding a manifest against that archive passes its path here and each
    bundle resolves as ``<transcript_bundle_dir>/<filename>``. Left at ``None``, both bundles
    resolve under ``project_root`` exactly as before -- what the synthetic-fixture tests rely on
    to stay hermetic. Either way, the returned ``frozen_inputs`` records the ACTUAL path used for
    each bundle (``main_transcript_bundle_path`` / ``canary_transcript_bundle_path``), so
    :func:`validate_manifest` can re-read from wherever a given manifest was really built.

    ``roster_judges`` is likewise required and explicit: the final phase-3 roster is decided by
    the canary calibration gates (``roster.new_judge_failure_rule``), not read out of the
    protocol, so every caller must state which candidate judges this particular manifest binds.

    ``estimator_validation_path`` (amendment 4, 2026-08-19, package items 3/6) is REQUIRED, with
    no null-tolerant default: the successor manifest must bind a real estimator-validation
    report (``scripts/phase3_estimator_validation.py``'s output), resolved under ``root`` and
    bound by path + canonical sha256 the same way ``TRANSCRIPT_VERIFICATION_RELATIVE_PATH`` is
    bound above. A caller building a synthetic-fixture manifest (e.g. this module's own tests)
    must supply a real, on-disk fixture report explicitly -- there is no fallback that lets an
    old caller omit it and keep passing.

    ``context_blocklist_canary_path``/``context_blocklist_main_path`` (Codex re-review,
    2026-08-19, blocker 2) are likewise REQUIRED, bound the same way: the regenerated
    eligibility list (``scripts/phase3_context_precheck.py``'s output) is a build-time identity
    property of the manifest, not a runtime-supplied file a caller could point anywhere.
    :func:`rejudge.phase3_runner.run_phase3_canary` verifies a runtime ``--context-blocklist``
    against ``frozen_inputs.context_blocklist_canary_report_sha256`` and refuses on any mismatch
    or on a blocklist supplied against a manifest with no such binding.
    """
    root = Path(project_root)
    protocol = phase3_plan.load_protocol(protocol_path)
    observed_protocol_sha = canonical_sha256(protocol)
    if observed_protocol_sha != phase3_plan.FROZEN_PROTOCOL_CANONICAL_SHA256:
        # phase3_plan.load_protocol already enforces this; restated so a caller of THIS module
        # never needs to know phase3_plan raises a different exception type to catch the case.
        raise ManifestValidationError(
            "loaded protocol does not match phase3_plan's frozen pin: observed "
            f"{observed_protocol_sha}, expected {phase3_plan.FROZEN_PROTOCOL_CANONICAL_SHA256}")

    roster_judges = list(roster_judges)
    amendment_bindings = _validate_roster_against_protocol(protocol, roster_judges, root)

    main_ids, held_out_ids = phase3_plan.load_reference_question_ids(protocol, root)
    main_cells = phase3_plan.enumerate_cells(protocol, roster_judges, main_ids)
    canary_cells = phase3_plan.enumerate_canary_cells(protocol, roster_judges, held_out_ids)

    frozen = _frozen_inputs(root, protocol, roster_judges,
                            transcript_bundle_dir=transcript_bundle_dir,
                            estimator_validation_path=estimator_validation_path,
                            context_blocklist_canary_path=context_blocklist_canary_path,
                            context_blocklist_main_path=context_blocklist_main_path)

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE,
        "recorded_at_utc": recorded_at_utc,
        "protocol_tracked_path": str(protocol_path).replace("\\", "/"),
        "planning": {
            "question_counts": {"main": len(main_ids), "held_out": len(held_out_ids)},
            "main": {**phase3_plan.summarize_cells(main_cells),
                    "cells_sha256": canonical_sha256(main_cells)},
            "canary": {**phase3_plan.summarize_cells(canary_cells),
                      "cells_sha256": canonical_sha256(canary_cells)},
        },
        "frozen_inputs": frozen,
        "roster": {
            "judges": roster_judges,
            "amendments": amendment_bindings,
            "debaters": list(protocol["roster"]["debaters"]),
            "oracle": protocol["roster"]["oracle"],
            "query_checker": protocol["roster"]["query_checker"],
            "gate_reviewer": protocol["roster"]["gate_reviewer"],
        },
        "caps": {
            "stage_cap_usd": protocol["decisions"]["spend"]["stage_cap_usd"],
            "configuration_selection_limits": dict(
                protocol["decisions"]["configuration_selection"]["limits"]),
        },
        "ledger": {
            "archive_dir": archive_dir,
            "main_results_path": f"{archive_dir}/phase3_main_results.jsonl",
            "canary_results_path": f"{archive_dir}/phase3_canary_results.jsonl",
            "usage_log_path": f"{archive_dir}/phase3_usage.jsonl",
            "decisions_path": f"{archive_dir}/phase3_reviewer_decisions.jsonl",
            "call_cache_path": f"{archive_dir}/phase3_call_cache.jsonl",
        },
        "code_provenance": {
            "files": list(PHASE3_CODE_PROVENANCE_FILES),
            "code_bundle_sha256": phase3_code_bundle_sha256(root),
        },
        "resume_granularity": "cell",
        # transcript_reuse.ingestion_mechanics: phase 3 makes zero debater calls, ever. This
        # flag is what rejudge.phase2_canary_execute.execute_cell checks to refuse a
        # transcript-generation cell before any provider call, independent of whether
        # pre-seeding actually covers it.
        "transcript_generation_forbidden": True,
        "execution_authorized": False,
    }
    manifest["execution_identity_sha256"] = canonical_sha256(manifest)
    return manifest


def validate_manifest(manifest: Mapping[str, Any], *, protocol_path: str | Path,
                      project_root: str | Path = ".",
                      transcript_bundle_dir: str | Path | None = None,
                      estimator_validation_path: str | Path | None = None,
                      context_blocklist_canary_path: str | Path | None = None,
                      context_blocklist_main_path: str | Path | None = None) -> dict[str, Any]:
    """Re-derive every binding from the real artifacts and refuse on any mismatch.

    ``transcript_bundle_dir`` must name the same directory (or ``None``) the manifest was
    originally built with: passed straight through to the rebuild below, it makes the rebuild
    resolve the two transcript bundles at the exact paths the manifest itself records
    (``frozen_inputs.main_transcript_bundle_path`` / ``canary_transcript_bundle_path``), so a
    manifest built against the archive re-validates by re-reading from the archive, and a
    manifest built hermetically under ``project_root`` re-validates the same way.

    ``estimator_validation_path`` (amendment 4, 2026-08-19) likewise must resolve to the SAME
    estimator-validation report the manifest was originally built with. Left at ``None``, it is
    recovered from the manifest's own recorded binding
    (``frozen_inputs.estimator_validation_report_tracked_path``) -- the manifest under
    validation is untrusted for every OTHER purpose, but its own tracked path is exactly what a
    caller re-validating without independently knowing that path needs to re-read from, mirroring
    how :func:`rejudge.phase3_runner.load_and_validate_manifest` recovers ``transcript_bundle_dir``
    from ``frozen_inputs.main_transcript_bundle_path``. A manifest with no such binding at all
    (pre-amendment-4 shape) fails the top-level key check below before this ever matters.
    """
    if not isinstance(manifest, Mapping):
        raise ManifestValidationError("manifest must be a mapping")
    if estimator_validation_path is None:
        frozen_inputs = manifest.get("frozen_inputs")
        if isinstance(frozen_inputs, Mapping):
            estimator_validation_path = frozen_inputs.get(
                "estimator_validation_report_tracked_path")
        if not estimator_validation_path:
            raise ManifestValidationError(
                "no estimator_validation_path was supplied and the manifest carries no "
                "frozen_inputs.estimator_validation_report_tracked_path to recover it from")
    if context_blocklist_canary_path is None:
        frozen_inputs = manifest.get("frozen_inputs")
        if isinstance(frozen_inputs, Mapping):
            context_blocklist_canary_path = frozen_inputs.get(
                "context_blocklist_canary_report_tracked_path")
        if not context_blocklist_canary_path:
            raise ManifestValidationError(
                "no context_blocklist_canary_path was supplied and the manifest carries no "
                "frozen_inputs.context_blocklist_canary_report_tracked_path to recover it from")
    if context_blocklist_main_path is None:
        frozen_inputs = manifest.get("frozen_inputs")
        if isinstance(frozen_inputs, Mapping):
            context_blocklist_main_path = frozen_inputs.get(
                "context_blocklist_main_report_tracked_path")
        if not context_blocklist_main_path:
            raise ManifestValidationError(
                "no context_blocklist_main_path was supplied and the manifest carries no "
                "frozen_inputs.context_blocklist_main_report_tracked_path to recover it from")
    keys = set(manifest)
    if keys != MANIFEST_TOP_LEVEL_KEYS:
        raise ManifestValidationError(
            f"phase-3 manifest fields drifted: unexpected "
            f"{sorted(keys - MANIFEST_TOP_LEVEL_KEYS)!r}, missing "
            f"{sorted(MANIFEST_TOP_LEVEL_KEYS - keys)!r}")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ManifestValidationError(f"schema_version must be {SCHEMA_VERSION!r}")
    if manifest["stage"] != STAGE:
        raise ManifestValidationError(f"stage must be {STAGE!r}")
    if manifest["transcript_generation_forbidden"] is not True:
        raise ManifestValidationError("transcript_generation_forbidden must be exactly true")
    if manifest["execution_authorized"] is not False:
        raise ManifestValidationError(
            "a manifest must never authorize itself; authorization lives in its own record")

    roster = manifest.get("roster")
    if not isinstance(roster, Mapping) or not isinstance(roster.get("judges"), list):
        raise ManifestValidationError("roster.judges must be a list")

    rebuilt = build_manifest(
        protocol_path, project_root=project_root, recorded_at_utc=manifest["recorded_at_utc"],
        archive_dir=manifest["ledger"]["archive_dir"], roster_judges=roster["judges"],
        transcript_bundle_dir=transcript_bundle_dir,
        estimator_validation_path=estimator_validation_path,
        context_blocklist_canary_path=context_blocklist_canary_path,
        context_blocklist_main_path=context_blocklist_main_path)
    for section in ("protocol_tracked_path", "planning", "frozen_inputs", "roster", "caps",
                    "ledger", "code_provenance", "resume_granularity",
                    "transcript_generation_forbidden"):
        if manifest[section] != rebuilt[section]:
            raise ManifestValidationError(
                f"{section} does not match the artifacts on disk; the manifest is stale or "
                "an input drifted")

    without_identity = {k: v for k, v in manifest.items() if k != "execution_identity_sha256"}
    expected = canonical_sha256(without_identity)
    if manifest["execution_identity_sha256"] != expected:
        raise ManifestValidationError(
            f"execution identity does not match the manifest it names: expected {expected}")
    return dict(manifest)
