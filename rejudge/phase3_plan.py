"""Deterministic, offline-only enumeration for the frozen Phase-3 budget-knob design.

Like ``phase2_plan``, this module has no API client, network, credential, or execution code.
It turns ``phase3_protocol.json`` into a reviewable main-grid and canary cell inventory and
validates that inventory against the frozen protocol's own slot arithmetic
(``debate_grid.slot_arithmetic_by_roster`` and ``decisions.launch_gates.canary_slot_inventory``).

Phase 3 makes zero debater calls: every judged transcript is a phase-2 uncapped blind debate
reused byte-identical (``transcript_reuse``). This module never enumerates transcript
*generation* work; it enumerates the transcript-*reference* cells that a later ingestion script
pre-seeds into the hash-chained cell-result store, so the runner's completed-cell skip logic
bypasses generation entirely and can never reach live transcript generation for a phase-3
question.

The final judge roster is not read from the protocol: it is only known after the phase-3 canary
calibration gates run (``roster.new_judge_failure_rule`` -- a new judge can be dropped, a
continuing judge failing a gate halts and escalates to the owner). The protocol only lists
*candidates* (``roster.judges_continuing`` + ``roster.judges_new``), so :func:`enumerate_cells`
and :func:`enumerate_canary_cells` both take the roster as an explicit argument rather than
reading ``protocol["roster"]`` for it directly.

Key derivation and canonical hashing are reused from ``phase2_plan``/``phase2_execution``
verbatim (:func:`make_cell_key`, :data:`canonical_sha256`), never re-implemented here. Unlike
phase 2's ``enumerate_canary_cells`` -- which hard-codes a literal condition tuple -- every
condition here is read generically off ``debate_grid.conditions``, so a protocol edit to that
list (adding, removing, or reordering budgets) changes what gets enumerated without a code
change, while :func:`validate_protocol` still fails closed on the frozen document's exact
canonical hash.

Scientific scope has owner approval (``authorization.design_scope_approved``), but the design
remains ``offline_planning_only`` and ``execution_authorized`` stays false throughout. Nothing
in this module can flip that.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from rejudge import phase2_plan
from rejudge.phase2_execution import canonical_sha256


DEFAULT_PROTOCOL_PATH = Path(__file__).with_name("phase3_protocol.json")
DEFAULT_PROTOCOL_V2_PATH = Path(__file__).with_name("phase3_protocol_v2.json")
OFFLINE_ONLY = True

# The frozen phase-3 protocol's own canonical_sha256 (whole document, sort_keys, no formatting
# sensitivity -- see phase2_plan.canonical_sha256 / rejudge.phase2_execution.canonical_sha256).
# Any semantic edit to phase3_protocol.json, however small, changes this hash: validate_protocol
# treats it as the final, catch-all check after the more specific structural checks below give
# a targeted error message for the mutations this planning module actually acts on.
#
# This is the IMMUTABLE v1 pin -- kept forever, even after v2 supersedes v1 as the design in
# force, so a v1 document (and everything historically bound to it: the v1 manifest, the v1
# canary archive) keeps validating exactly as it always did. v2 gets its own, separate pin
# below; validate_protocol/load_protocol select between the two by schema_version, never by
# trying one pin and falling back to the other.
FROZEN_PROTOCOL_CANONICAL_SHA256 = (
    "615d1e1f46e32978fbf4af54ab0c6098c2f65354a4b287e3677dc169d9013aee"
)

# The ratified phase-3 v2 protocol's canonical_sha256 (rejudge/phase3_protocol_v2.json), pinned
# post-ratification (rejudge/phase3_v2_ratification_2026-08-21.json). v2 supersedes v1
# (supersedes.canonical_sha256 in the v2 document itself names the v1 pin above, cross-checked
# by _validate_protocol_v2), but v1 stays immutable and separately pinned -- this is a NEW
# constant, never a mutation of FROZEN_PROTOCOL_CANONICAL_SHA256.
FROZEN_PROTOCOL_V2_CANONICAL_SHA256 = (
    "33e0a1213c682dbd658d73dfc7f87f255e1e46db002c7237cbd0f04347420104"
)

# v3 is materialized only after the conditional Qwen2.5 roster branch resolves. Its exact
# protocol hash therefore cannot be pinned in source before that resolution exists. Instead,
# the materializer binds the owner-approved successor design below, the immutable v2 protocol,
# and the roster-resolution artifact, including a provider-unavailability disposition when
# needed, then records a content digest inside the generated protocol. The small run manifest
# pins the generated protocol's full canonical hash.
FROZEN_SUCCESSOR_DESIGN_CANONICAL_SHA256 = (
    "75c1790a54d7a6ca780839f8a1efe4ca5a5db9aee075d4c473be516004cc0479"
)
FROZEN_V3_ROSTER_AMENDMENT_CANONICAL_SHA256 = (
    "44f082a459cd22e5c8586043e18f781e624ebb487c0f7fa074087f9930136b58"
)
FROZEN_PROTOCOL_V3_R2_CANONICAL_SHA256 = (
    "1415949888eefdd995d2ae8c7870b0fd5949fcdfe5f3ea4ea4e6d3ec4c93038e"
)
FROZEN_V3_RECOVERY_AMENDMENT_CANONICAL_SHA256 = (
    "ae27ed66e44b38d3e48883463e405603d7e52d8cc389beff43f2dfe91d7d334c"
)
FROZEN_V3_R5_HALT_OBSERVATION_CANONICAL_SHA256 = (
    "36e0e8c96bbf5b76ffa1ee798829c504e60dfddaed5cf1a1488e1ee65f5f1e45"
)
FROZEN_V3R2_PROVIDER_CATALOG_CANONICAL_SHA256 = (
    "e5734592091f9dc94fdd43100079a735b0c25a85c102dd550ba3727970908444"
)
FROZEN_V3R2_SERVERLESS_ENDPOINTS_CANONICAL_SHA256 = (
    "0701dc10a50dd6cba8bf9333576f7d80d698182fce5dea246fb7198088302b3a"
)
FROZEN_V3R2_QWEN38_PROVIDER_TEMPLATE_CANONICAL_SHA256 = (
    "71056ce80b3c50f6ceaf633236e566557b742f4ba95f540041ab8d0c55bd0ffb"
)
FROZEN_PHASE2_PROMPT_BUNDLE_CANONICAL_SHA256 = (
    "cc02d29cfc8e7410c270c21f53da56457e44c31f74f8e512299e4e80726a076f"
)
FROZEN_CHECKER_CONFIG_CANONICAL_SHA256 = (
    "8e674eddbb22ba73ee5a4ae4f359f1630cf4cd65c5f5d98cb70312dc868b9872"
)
FROZEN_CHECKER_VALIDATION_DESIGN_CANONICAL_SHA256 = (
    "4f9d3a34008234259503ce4e9b6d8152566ad7414b15e8c3d62135d892c7ed5f"
)

PHASE3_V3_DESIGN_BASE_JUDGES = (
    "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "google/gemma-3n-E4B-it",
    "Qwen/Qwen3.7-Max",
)
PHASE3_V3_BASE_JUDGES = (
    "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "google/gemma-3n-E4B-it",
    "Qwen/Qwen3.5-397B-A17B",
)
PHASE3_V3_REPLACED_JUDGE = "Qwen/Qwen3.7-Max"
PHASE3_V3_REPLACEMENT_JUDGE = "Qwen/Qwen3.5-397B-A17B"
PHASE3_V3_RECOVERY_REPLACED_JUDGE = "Qwen/Qwen3.5-397B-A17B"
PHASE3_V3_RECOVERY_REPLACEMENT_JUDGE = "Qwen/Qwen3.8-2.4T-A95B"
PHASE3_V3_RECOVERY_BASE_JUDGES = (
    "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "google/gemma-3n-E4B-it",
    PHASE3_V3_RECOVERY_REPLACEMENT_JUDGE,
)
PHASE3_V3_CONDITIONAL_JUDGE = "Qwen/Qwen2.5-7B-Instruct-Turbo"
PHASE3_V3_EXCLUDED_JUDGE = "openai/gpt-oss-120b"

# Second recovery generation (2026-08-25): Together delisted gemma-3n-E4B from serverless
# mid-canary (r11 halt observation). The owner-approved amendment 4 substitutes the only
# genuinely small non-Gemma serverless chat model in the authenticated catalog.
FROZEN_PROTOCOL_V3_R3_CANONICAL_SHA256 = (
    "a884ccefc2e4b62c4c09882e5c66f36b86c7ad8d8cb1277d94579e36d79a7fb5"
)
FROZEN_V3_RECOVERY2_AMENDMENT_CANONICAL_SHA256 = (
    "80c4b7ce5da8bffb31bf9172b557e4c4075b31f74248643beda7740e92e8b715"
)
FROZEN_V3_R11_HALT_OBSERVATION_CANONICAL_SHA256 = (
    "7cff6cc8604c17dad3ad67574cf1efe42ac1611db28b0148a74d58d8a3246618"
)
FROZEN_V3R4_PROVIDER_CATALOG_CANONICAL_SHA256 = (
    "5cb0458c39c1bd5337f0ebf1b0f0804c12db31b42753c82502af02462d01f055"
)
FROZEN_V3R4_SERVERLESS_ENDPOINTS_CANONICAL_SHA256 = (
    "f04b9f8fd88c6c17f69b980374a870df8986b5a18ddb8b682e17bacd820e77e8"
)
FROZEN_QWEN35_9B_SCREENING_CANONICAL_SHA256 = (
    "a5b28589b5855cd1a1dee90a2ff46afb65461fa953fa7d7714fd209f3041b116"
)
PHASE3_V3_RECOVERY2_REPLACED_JUDGE = "google/gemma-3n-E4B-it"
PHASE3_V3_RECOVERY2_REPLACEMENT_JUDGE = "Qwen/Qwen3.5-9B"
PHASE3_V3_RECOVERY2_BASE_JUDGES = (
    "google/gemma-4-31B-it",
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    PHASE3_V3_RECOVERY2_REPLACEMENT_JUDGE,
    "Qwen/Qwen3.8-2.4T-A95B",
)

# Third recovery generation (2026-08-27): the r21 empty-verdict discovery showed thinking
# judges exhausting the frozen 4,096-token effective completion cap on judgment-shaped
# prompts (finish_reason=length, empty visible content), which structurally failed the
# strict INVALID gate in every four-judge attempt. Judgment-shaped screening then found:
# the weak slot unfillable (Qwen3.5-9B degenerate at 8,192 AND 16,384; gemma-4-E4B not
# serverless-servable; gemma-3n delisted); Qwen3.8 genuinely bounded and admitted at a
# screened 16,384 verdict budget; and gemma-4-31B carrying a rare per-prompt-deterministic
# runaway mode that no cap contains, failing verdict admission at every screened cap while
# passing its checker role 0-of-96. Codex ruled a post-hoc exemption gate-weakening, so
# the owner-approved amendment 8 rebuilds N=2 (Llama + Qwen3.8) with gemma-4 checker-only.
# Judgment-screening evidence lives in the bound screen plan and results record.
FROZEN_PROTOCOL_V3_R4_CANONICAL_SHA256 = (
    "128fa5ddc2dc6604c7dc9a05923e385d285b40374a344deeff0c68c4fd82aa20"
)
FROZEN_V3_R21_DISCOVERY_CANONICAL_SHA256 = (
    "e9ea382e1bcbcd15e3b0ef29c494d7e81f58c872d14046eccf44566122be2505"
)
FROZEN_JUDGMENT_SCREEN_PLAN_CANONICAL_SHA256 = (
    "96f8d21ed6336837f578283f6da06d36f471885c302e3f0396b383fc0c5366a3"
)
# Both values below were frozen from the amendment-8 builder's computed output after the
# stage-3 screens completed; they were never hand-typed.
FROZEN_V3_RECOVERY3_AMENDMENT_CANONICAL_SHA256: str | None = (
    "dfdff7a1d2f893a37dab5816c442608191e357a0add44acb20797a9d112dbd50"
)
FROZEN_JUDGMENT_SCREEN_RESULTS_CANONICAL_SHA256: str | None = (
    "07b99686c742a985b3953356acdd519c9a1b8095c4f3580fe32a18e5c9022432"
)
PHASE3_V3_RECOVERY3_REMOVED_JUDGES = (
    "Qwen/Qwen3.5-9B",
    "google/gemma-4-31B-it",
)
PHASE3_V3_RECOVERY3_BASE_JUDGES = (
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "Qwen/Qwen3.8-2.4T-A95B",
)
PHASE3_V3_RECOVERY3_CHECKER_ONLY_MODEL = "google/gemma-4-31B-it"

# Fourth recovery generation (2026-08-28): the r25 concentration-bound stop measured
# gemma-4's checker runaway at 4.3% per call on LIVE judge queries (vs 0-of-96 on
# pilot-composed screening probes: the trigger is prompt-content-dependent), so the b2
# condition cannot converge under the frozen gemma-4 checker at all. Codex ruled B > C
# > A: substitute Llama (non-thinking, structurally immune) as checker, admitted by a
# 0-of-96 screen on hash-verified LIVE-query probes including every known runaway
# trigger. The checker prompts, parser, decoding, and gate semantics stay the frozen
# phase-2 artifacts; only the executing model changes, disclosed as an adaptive
# operational-validity substitution with a pre-registered judge-origin disparity
# diagnostic. gemma-4 leaves the billed registry entirely.
FROZEN_PROTOCOL_V3_R5_CANONICAL_SHA256 = (
    "6e7f686e349f77c65b56c862221e27f8068f410f056c6f0764f97fd54e260e2d"
)
FROZEN_V3_R25_STOP_CANONICAL_SHA256 = (
    "95c485fb0a545ecfc7bc375e2d17f62b2558eefb1f19ec72603feed8e73d76d1"
)
FROZEN_LLAMA_CHECKER_SCREEN_PLAN_CANONICAL_SHA256 = (
    "ec460456bf4ccde4e98980cf028ed10fa2c7e3628244ca014ec922906ac8b77f"
)
FROZEN_LLAMA_CHECKER_SCREEN_BANK_CANONICAL_SHA256 = (
    "0037140455a31b5c515b627ab29e96c256aa4976a3fdb67b5b91cf5c536e3021"
)
FROZEN_LLAMA_CHECKER_SCREEN_RESULTS_CANONICAL_SHA256 = (
    "401006c2ba4e681235ad12dad44d62df509cab6490ed3a955f244e3b6d8d22c8"
)
# Frozen from the amendment-9 builder's computed output; never hand-typed.
FROZEN_V3_RECOVERY4_AMENDMENT_CANONICAL_SHA256: str | None = (
    "ec73ee30588c5f282ac655c8c6e37859554809591051405ae350c1dec1b33b1f"
)
PHASE3_V3_RECOVERY4_REMOVED_CHECKER = "google/gemma-4-31B-it"
PHASE3_V3_RECOVERY4_CHECKER_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"

# make_cell_key is reused verbatim from phase2_plan, never copy-pasted; re-exported here so
# callers (and tests) can address it as phase3_plan.make_cell_key, matching phase2_plan's own
# module-level surface.
make_cell_key = phase2_plan.make_cell_key

# Phase 3 has no structured protocol field for these two counts: they appear only in prose
# (transcript_reuse.main_bundle.content: "82 questions x 2 self-play debaters x 3 transcripts";
# transcript_reuse.canary_bundle.content: "one per question per debater"). They are pinned here
# as documented literals and cross-checked by selftest() against debate_grid.slot_arithmetic_by_roster
# and decisions.launch_gates.canary_slot_inventory, both of which DO encode them numerically.
MAIN_TRANSCRIPTS_PER_QUESTION_PER_DEBATER = 3
CANARY_TRANSCRIPTS_PER_QUESTION_PER_DEBATER = 1

MAIN_TRANSCRIPT_KIND = "phase3_transcript_reference"
MAIN_JUDGMENT_KIND = "phase3_debate_judgment"
CANARY_TRANSCRIPT_KIND = "phase3_canary_transcript_reference"
CANARY_JUDGMENT_KIND = "phase3_canary_debate_judgment"
CAPABILITY_ANCHOR_KIND = "phase3_capability_qa"
TRANSCRIPT_KINDS = frozenset({MAIN_TRANSCRIPT_KIND, CANARY_TRANSCRIPT_KIND})

MAIN_TRANSCRIPT_CONDITION = "phase2_blind_uncapped_3_round_reused"
CANARY_TRANSCRIPT_CONDITION = "phase2_canary_blind_uncapped_3_round_reused"
CAPABILITY_ANCHOR_CONDITION = "capability_qa"

EXPECTED_DECISION_KEYS = frozenset({
    "primary_tests", "secondary_analyses", "capability_anchor", "configuration_selection",
    "query_screening", "execution_semantics", "launch_gates", "spend",
})

# v2 adds exactly one new decisions section relative to v1: decisions.context_guard (native in
# v2 per amendment 4; the v1 immutable document never had this key at all).
EXPECTED_DECISION_KEYS_V2 = EXPECTED_DECISION_KEYS | {"context_guard"}


class ProtocolValidationError(ValueError):
    """Raised when the frozen protocol is internally inconsistent or has drifted."""


class PlanValidationError(ValueError):
    """Raised when an enumerated plan has duplicates or broken dependencies."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolValidationError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProtocolValidationError(f"{label} must be an array")
    return value


def _unique_strings(value: Any, label: str) -> list[str]:
    items = _list(value, label)
    if not all(isinstance(item, str) and item for item in items):
        raise ProtocolValidationError(f"{label} must contain non-empty strings")
    strings = list(items)
    if len(strings) != len(set(strings)):
        raise ProtocolValidationError(f"{label} contains duplicates")
    return strings


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolValidationError(f"{label} must be a non-empty string")
    return value


def _sha256_string(value: Any, label: str) -> str:
    text = _non_empty_string(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ProtocolValidationError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolValidationError(f"{label} must be a positive integer")
    return value


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolValidationError(f"{label} must be a non-negative integer")
    return value


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    """Validate a frozen Phase-3 design document, selecting the check-set by schema_version.

    v1 and v2 each have their own immutable code pin. v3 does not exist until the conditional
    roster branch resolves, so it instead binds the code-pinned successor design, the v2 pin,
    and a roster-resolution hash. Its generated content digest is verified here and its full
    canonical hash is pinned by the small run manifest.
    """
    schema_version = protocol.get("schema_version")
    if schema_version == "phase3_plan_v1":
        _validate_protocol_v1(protocol)
    elif schema_version == "phase3_plan_v2":
        _validate_protocol_v2(protocol)
    elif schema_version == "phase3_plan_v3":
        _validate_protocol_v3(protocol)
    else:
        raise ProtocolValidationError("unsupported schema_version")


def _validate_protocol_v1(protocol: Mapping[str, Any]) -> None:
    """Validate the frozen Phase-3 v1 design while keeping materialization fail-closed.

    Structural checks run first, each with a targeted error message, for the shapes this
    planning module actually reads: the roster's continuing/new split, ``debate_grid.conditions``
    (read generically -- never as a hard-coded tuple), each condition's
    ``judgment_replicates_per_transcript_side`` cross-checked against
    ``tail_replicate_configurations.selected``, ``configuration_selection.limits``, and the
    ``transcript_reuse`` block. The frozen whole-document canonical hash is checked last as a
    catch-all: any semantic drift this function does not explicitly name still fails closed.
    """
    if protocol.get("schema_version") != "phase3_plan_v1":
        raise ProtocolValidationError("unsupported schema_version")
    if protocol.get("status") != "approved_design_pending_materialization":
        raise ProtocolValidationError(
            "status must remain approved_design_pending_materialization")
    if protocol.get("offline_planning_only") is not True:
        raise ProtocolValidationError("offline_planning_only must be true")
    if protocol.get("execution_authorized") is not False:
        raise ProtocolValidationError("execution_authorized must be false")
    for field in ("protocol_id", "cell_key_namespace"):
        _non_empty_string(protocol.get(field), field)

    planning_identity = _mapping(
        protocol.get("planning_cell_identity"), "planning_cell_identity")
    if planning_identity.get("status") != "planning_only_not_executable":
        raise ProtocolValidationError("planning cell keys cannot be represented as executable")
    bundle_sha = planning_identity.get("question_bank_bundle_sha256")
    if not isinstance(bundle_sha, str) or len(bundle_sha) != 64:
        raise ProtocolValidationError("question-bank bundle hash must be SHA-256")
    prefix_length = planning_identity.get("namespace_hash_prefix_length")
    if prefix_length != 12:
        raise ProtocolValidationError("planning namespace hash-prefix length must remain 12")
    expected_suffix = f".qb-{bundle_sha[:prefix_length]}"
    if not str(protocol["cell_key_namespace"]).endswith(expected_suffix):
        raise ProtocolValidationError("planning namespace is not bound to the question banks")
    if planning_identity.get("execution_key_requirement") != (
            "external execution manifests must bind this frozen design protocol, the question "
            "banks, the reused phase-2 prompt bundle, extended role limits, provider request "
            "fields, the side/seed policy, and the frozen transcript bundles"):
        raise ProtocolValidationError("execution-key binding contract drifted")

    authorization = _mapping(protocol.get("authorization"), "authorization")
    if authorization.get("design_scope_approved") is not True:
        raise ProtocolValidationError("the scientific design must have explicit design-scope approval")
    if authorization.get("canary_spend_authorized") is not False:
        raise ProtocolValidationError("canary spend must remain separately unauthorized")
    if authorization.get("main_run_spend_authorized") is not False:
        raise ProtocolValidationError("main-run spend must remain separately unauthorized")
    for field in ("approval_record", "approver", "approved_at_utc", "redecision_record"):
        _non_empty_string(authorization.get(field), f"authorization.{field}")

    question_set = _mapping(protocol.get("question_set"), "question_set")
    expected_main = _positive_int(
        question_set.get("expected_main_question_count"), "expected_main_question_count")
    expected_held_out = _positive_int(
        question_set.get("held_out_question_count"), "held_out_question_count")
    if expected_main != 82 or expected_held_out != 24:
        raise ProtocolValidationError(
            "the frozen design must keep 82 main questions and 24 held-out questions")

    roster = _mapping(protocol.get("roster"), "roster")
    judges_continuing = _unique_strings(
        roster.get("judges_continuing"), "roster.judges_continuing")
    if len(judges_continuing) != 4:
        raise ProtocolValidationError("roster.judges_continuing must list exactly 4 judges")
    judges_new = _list(roster.get("judges_new"), "roster.judges_new")
    seen_candidates: set[str] = set()
    for index, raw_candidate in enumerate(judges_new):
        candidate = _mapping(raw_candidate, f"roster.judges_new[{index}]")
        candidate_id = _non_empty_string(
            candidate.get("candidate_model_id"), f"roster.judges_new[{index}].candidate_model_id")
        if candidate_id in seen_candidates or candidate_id in judges_continuing:
            raise ProtocolValidationError(
                "roster.judges_new candidate_model_id values must be unique and distinct from "
                "the continuing roster")
        seen_candidates.add(candidate_id)
        for field in ("display_name", "rationale", "id_and_price_status"):
            _non_empty_string(candidate.get(field), f"roster.judges_new[{index}].{field}")
    debaters = _unique_strings(roster.get("debaters"), "roster.debaters")
    if len(debaters) != 2:
        raise ProtocolValidationError("roster.debaters must list exactly 2 debaters")
    for field in (
        "debater_role_note", "oracle", "query_checker", "gate_reviewer",
        "new_judge_failure_rule", "roster_freeze_timing",
    ):
        _non_empty_string(roster.get(field), f"roster.{field}")

    debate_grid = _mapping(protocol.get("debate_grid"), "debate_grid")
    if debate_grid.get("k") != 2:
        raise ProtocolValidationError("debate-grid K must be 2")
    conditions = _list(debate_grid.get("conditions"), "debate_grid.conditions")
    if not conditions:
        raise ProtocolValidationError("debate_grid.conditions must be non-empty")
    seen_condition_ids: set[str] = set()
    budgets_seen: set[int] = set()
    by_budget: dict[int, Mapping[str, Any]] = {}
    for index, raw_condition in enumerate(conditions):
        condition = _mapping(raw_condition, f"debate_grid.conditions[{index}]")
        condition_id = _non_empty_string(condition.get("id"), f"debate_grid.conditions[{index}].id")
        if condition_id in seen_condition_ids:
            raise ProtocolValidationError(f"duplicate debate_grid.conditions id: {condition_id!r}")
        seen_condition_ids.add(condition_id)
        budget = _non_negative_int(
            condition.get("query_budget"), f"debate_grid.conditions[{index}].query_budget")
        budgets_seen.add(budget)
        by_budget[budget] = condition
        _non_empty_string(
            condition.get("oracle_mode"), f"debate_grid.conditions[{index}].oracle_mode")
        _non_empty_string(
            condition.get("presentation"), f"debate_grid.conditions[{index}].presentation")
        _positive_int(
            condition.get("judgment_replicates_per_transcript_side"),
            f"debate_grid.conditions[{index}].judgment_replicates_per_transcript_side")
    if budgets_seen != {0, 1, 2, 4, 8}:
        raise ProtocolValidationError(
            "debate_grid.conditions must cover exactly query budgets {0, 1, 2, 4, 8}")

    tail = _mapping(
        debate_grid.get("tail_replicate_configurations"), "debate_grid.tail_replicate_configurations")
    tail_config_names = ("A_no_d", "B_b4_only", "C_b8_only", "D_full")
    tail_configs: dict[str, Mapping[str, Any]] = {}
    for name in tail_config_names:
        config = _mapping(tail.get(name), f"tail_replicate_configurations.{name}")
        if set(config) != {"b4", "b8"}:
            raise ProtocolValidationError(
                f"tail_replicate_configurations.{name} must specify exactly b4 and b8")
        for key in ("b4", "b8"):
            _positive_int(config.get(key), f"tail_replicate_configurations.{name}.{key}")
        tail_configs[name] = config
    selected_name = tail.get("selected")
    if selected_name not in tail_configs:
        raise ProtocolValidationError(
            "tail_replicate_configurations.selected must name one of the four configurations")
    selected_config = tail_configs[str(selected_name)]
    for budget in (4, 8):
        condition = by_budget[budget]
        expected_replicates = int(selected_config[f"b{budget}"])
        actual_replicates = int(condition["judgment_replicates_per_transcript_side"])
        if actual_replicates != expected_replicates:
            raise ProtocolValidationError(
                f"condition {condition['id']!r} judgment_replicates_per_transcript_side "
                f"({actual_replicates}) disagrees with the selected tail configuration "
                f"{selected_name!r} ({expected_replicates})")

    slot_arithmetic = _mapping(
        debate_grid.get("slot_arithmetic_by_roster"), "debate_grid.slot_arithmetic_by_roster")
    total_slots = _mapping(
        slot_arithmetic.get("total_judgment_slots"), "slot_arithmetic_by_roster.total_judgment_slots")
    if set(total_slots) != set(tail_config_names):
        raise ProtocolValidationError(
            "slot_arithmetic_by_roster.total_judgment_slots config names drifted")
    for name, per_roster in total_slots.items():
        per_roster_map = _mapping(per_roster, f"total_judgment_slots.{name}")
        if set(per_roster_map) != {"N7", "N6", "N5", "N4"}:
            raise ProtocolValidationError(f"total_judgment_slots.{name} must list N4..N7")
        for roster_key, count in per_roster_map.items():
            _positive_int(count, f"total_judgment_slots.{name}.{roster_key}")
    if str(selected_name) not in str(slot_arithmetic.get("selected", "")):
        raise ProtocolValidationError(
            "slot_arithmetic_by_roster.selected must reference the selected tail configuration")

    decisions = _mapping(protocol.get("decisions"), "decisions")
    if set(decisions) != EXPECTED_DECISION_KEYS:
        raise ProtocolValidationError(f"decisions must be exactly {sorted(EXPECTED_DECISION_KEYS)!r}")

    configuration_selection = _mapping(
        decisions["configuration_selection"], "decisions.configuration_selection")
    limits = _mapping(configuration_selection.get("limits"), "configuration_selection.limits")
    _positive_int(limits.get("R_max"), "configuration_selection.limits.R_max")
    _positive_int(limits.get("D_max"), "configuration_selection.limits.D_max")

    launch_gates = _mapping(decisions["launch_gates"], "decisions.launch_gates")
    smoke_subset = _unique_strings(
        launch_gates.get("budget_smoke_subset"), "launch_gates.budget_smoke_subset")
    if len(smoke_subset) != 6:
        raise ProtocolValidationError("launch_gates.budget_smoke_subset must list exactly 6 questions")
    canary_inventory = _mapping(
        launch_gates.get("canary_slot_inventory"), "launch_gates.canary_slot_inventory")
    for field in ("per_judge_formula", "totals_by_config_and_roster", "pinned_denominator_rule"):
        _non_empty_string(canary_inventory.get(field), f"launch_gates.canary_slot_inventory.{field}")

    transcript_reuse = _mapping(protocol.get("transcript_reuse"), "transcript_reuse")
    policy = _non_empty_string(transcript_reuse.get("policy"), "transcript_reuse.policy")
    if "ZERO debater calls" not in policy:
        raise ProtocolValidationError("transcript_reuse.policy must assert zero debater calls")
    main_bundle = _mapping(transcript_reuse.get("main_bundle"), "transcript_reuse.main_bundle")
    for field in ("content", "source_archive", "materialization", "verifier"):
        _non_empty_string(main_bundle.get(field), f"transcript_reuse.main_bundle.{field}")
    canary_bundle = _mapping(transcript_reuse.get("canary_bundle"), "transcript_reuse.canary_bundle")
    for field in ("content", "source_archive", "materialization"):
        _non_empty_string(canary_bundle.get(field), f"transcript_reuse.canary_bundle.{field}")
    _non_empty_string(
        transcript_reuse.get("ingestion_mechanics"), "transcript_reuse.ingestion_mechanics")

    observed_hash = canonical_sha256(protocol)
    if observed_hash != FROZEN_PROTOCOL_CANONICAL_SHA256:
        raise ProtocolValidationError(
            "frozen phase-3 protocol hash drift: observed "
            f"{observed_hash}, expected {FROZEN_PROTOCOL_CANONICAL_SHA256}")


def _validate_protocol_v2(protocol: Mapping[str, Any]) -> None:
    """Validate the ratified Phase-3 v2 design (rejudge/phase3_protocol_v2.json).

    v2 restates every v1 structural shape that is unchanged (question set, K2 debate grid,
    tail-replicate configuration, transcript reuse) and adds the sections the ratification
    (``rejudge/phase3_v2_ratification_2026-08-21.json``) actually changed: a ``supersedes``
    block naming the superseded v1 pin, a ``ratified_design_pending_materialization`` status, a
    6-judge roster (4 continuing + exactly 2 new candidates, one carrying an
    ``inclusion_rule``), a v2-shaped ``canary_slot_inventory`` (``per_judge`` prose +
    ``six_judge_totals`` with the fresh/carried/combined counts the manifest's anchor-carry
    binding depends on), ``canary_scope`` and ``pace_measurement_window`` (both drive the
    manifest's crank-settings binding), and a native ``decisions.context_guard`` section. The
    frozen whole-document canonical hash is checked last, against
    :data:`FROZEN_PROTOCOL_V2_CANONICAL_SHA256`, as the catch-all for anything not explicitly
    named here.
    """
    if protocol.get("schema_version") != "phase3_plan_v2":
        raise ProtocolValidationError("unsupported schema_version")
    if protocol.get("status") != "ratified_design_pending_materialization":
        raise ProtocolValidationError(
            "v2 status must remain ratified_design_pending_materialization")
    if protocol.get("offline_planning_only") is not True:
        raise ProtocolValidationError("offline_planning_only must be true")
    if protocol.get("execution_authorized") is not False:
        raise ProtocolValidationError("execution_authorized must be false")
    for field in ("protocol_id", "cell_key_namespace"):
        _non_empty_string(protocol.get(field), field)

    planning_identity = _mapping(
        protocol.get("planning_cell_identity"), "planning_cell_identity")
    if planning_identity.get("status") != "planning_only_not_executable":
        raise ProtocolValidationError("planning cell keys cannot be represented as executable")
    bundle_sha = planning_identity.get("question_bank_bundle_sha256")
    if not isinstance(bundle_sha, str) or len(bundle_sha) != 64:
        raise ProtocolValidationError("question-bank bundle hash must be SHA-256")
    prefix_length = planning_identity.get("namespace_hash_prefix_length")
    if prefix_length != 12:
        raise ProtocolValidationError("planning namespace hash-prefix length must remain 12")
    expected_suffix = f".qb-{bundle_sha[:prefix_length]}"
    if not str(protocol["cell_key_namespace"]).endswith(expected_suffix):
        raise ProtocolValidationError("planning namespace is not bound to the question banks")
    if planning_identity.get("execution_key_requirement") != (
            "external execution manifests must bind this frozen design protocol, the question "
            "banks, the reused phase-2 prompt bundle, extended role limits, provider request "
            "fields, the side/seed policy, and the frozen transcript bundles"):
        raise ProtocolValidationError("execution-key binding contract drifted")

    authorization = _mapping(protocol.get("authorization"), "authorization")
    if authorization.get("design_scope_approved") is not True:
        raise ProtocolValidationError("the scientific design must have explicit design-scope approval")
    if authorization.get("canary_spend_authorized") is not False:
        raise ProtocolValidationError("canary spend must remain separately unauthorized")
    if authorization.get("main_run_spend_authorized") is not False:
        raise ProtocolValidationError("main-run spend must remain separately unauthorized")
    for field in ("approval_record", "approver", "approved_at_utc", "redecision_record"):
        _non_empty_string(authorization.get(field), f"authorization.{field}")

    question_set = _mapping(protocol.get("question_set"), "question_set")
    expected_main = _positive_int(
        question_set.get("expected_main_question_count"), "expected_main_question_count")
    expected_held_out = _positive_int(
        question_set.get("held_out_question_count"), "held_out_question_count")
    if expected_main != 82 or expected_held_out != 24:
        raise ProtocolValidationError(
            "the frozen design must keep 82 main questions and 24 held-out questions")

    roster = _mapping(protocol.get("roster"), "roster")
    judges_continuing = _unique_strings(
        roster.get("judges_continuing"), "roster.judges_continuing")
    if len(judges_continuing) != 4:
        raise ProtocolValidationError("roster.judges_continuing must list exactly 4 judges")
    judges_new = _list(roster.get("judges_new"), "roster.judges_new")
    if len(judges_new) != 2:
        raise ProtocolValidationError(
            "v2 roster.judges_new must list exactly 2 new candidates (the native 6-judge roster)")
    seen_candidates: set[str] = set()
    for index, raw_candidate in enumerate(judges_new):
        candidate = _mapping(raw_candidate, f"roster.judges_new[{index}]")
        candidate_id = _non_empty_string(
            candidate.get("candidate_model_id"), f"roster.judges_new[{index}].candidate_model_id")
        if candidate_id in seen_candidates or candidate_id in judges_continuing:
            raise ProtocolValidationError(
                "roster.judges_new candidate_model_id values must be unique and distinct from "
                "the continuing roster")
        seen_candidates.add(candidate_id)
        for field in ("display_name", "rationale", "id_and_price_status"):
            _non_empty_string(candidate.get(field), f"roster.judges_new[{index}].{field}")
    debaters = _unique_strings(roster.get("debaters"), "roster.debaters")
    if len(debaters) != 2:
        raise ProtocolValidationError("roster.debaters must list exactly 2 debaters")
    for field in (
        "debater_role_note", "oracle", "query_checker", "gate_reviewer",
        "new_judge_failure_rule", "roster_freeze_timing",
    ):
        _non_empty_string(roster.get(field), f"roster.{field}")

    debate_grid = _mapping(protocol.get("debate_grid"), "debate_grid")
    if debate_grid.get("k") != 2:
        raise ProtocolValidationError("debate-grid K must be 2")
    conditions = _list(debate_grid.get("conditions"), "debate_grid.conditions")
    if not conditions:
        raise ProtocolValidationError("debate_grid.conditions must be non-empty")
    seen_condition_ids: set[str] = set()
    budgets_seen: set[int] = set()
    by_budget: dict[int, Mapping[str, Any]] = {}
    for index, raw_condition in enumerate(conditions):
        condition = _mapping(raw_condition, f"debate_grid.conditions[{index}]")
        condition_id = _non_empty_string(condition.get("id"), f"debate_grid.conditions[{index}].id")
        if condition_id in seen_condition_ids:
            raise ProtocolValidationError(f"duplicate debate_grid.conditions id: {condition_id!r}")
        seen_condition_ids.add(condition_id)
        budget = _non_negative_int(
            condition.get("query_budget"), f"debate_grid.conditions[{index}].query_budget")
        budgets_seen.add(budget)
        by_budget[budget] = condition
        _non_empty_string(
            condition.get("oracle_mode"), f"debate_grid.conditions[{index}].oracle_mode")
        _non_empty_string(
            condition.get("presentation"), f"debate_grid.conditions[{index}].presentation")
        _positive_int(
            condition.get("judgment_replicates_per_transcript_side"),
            f"debate_grid.conditions[{index}].judgment_replicates_per_transcript_side")
    if budgets_seen != {0, 1, 2, 4, 8}:
        raise ProtocolValidationError(
            "debate_grid.conditions must cover exactly query budgets {0, 1, 2, 4, 8}")

    tail = _mapping(
        debate_grid.get("tail_replicate_configurations"), "debate_grid.tail_replicate_configurations")
    tail_config_names = ("A_no_d", "B_b4_only", "C_b8_only", "D_full")
    tail_configs: dict[str, Mapping[str, Any]] = {}
    for name in tail_config_names:
        config = _mapping(tail.get(name), f"tail_replicate_configurations.{name}")
        if set(config) != {"b4", "b8"}:
            raise ProtocolValidationError(
                f"tail_replicate_configurations.{name} must specify exactly b4 and b8")
        for key in ("b4", "b8"):
            _positive_int(config.get(key), f"tail_replicate_configurations.{name}.{key}")
        tail_configs[name] = config
    selected_name = tail.get("selected")
    if selected_name not in tail_configs:
        raise ProtocolValidationError(
            "tail_replicate_configurations.selected must name one of the four configurations")
    selected_config = tail_configs[str(selected_name)]
    for budget in (4, 8):
        condition = by_budget[budget]
        expected_replicates = int(selected_config[f"b{budget}"])
        actual_replicates = int(condition["judgment_replicates_per_transcript_side"])
        if actual_replicates != expected_replicates:
            raise ProtocolValidationError(
                f"condition {condition['id']!r} judgment_replicates_per_transcript_side "
                f"({actual_replicates}) disagrees with the selected tail configuration "
                f"{selected_name!r} ({expected_replicates})")

    slot_arithmetic = _mapping(
        debate_grid.get("slot_arithmetic_by_roster"), "debate_grid.slot_arithmetic_by_roster")
    total_slots = _mapping(
        slot_arithmetic.get("total_judgment_slots"), "slot_arithmetic_by_roster.total_judgment_slots")
    if set(total_slots) != set(tail_config_names):
        raise ProtocolValidationError(
            "slot_arithmetic_by_roster.total_judgment_slots config names drifted")
    for name, per_roster in total_slots.items():
        per_roster_map = _mapping(per_roster, f"total_judgment_slots.{name}")
        if set(per_roster_map) != {"N7", "N6", "N5", "N4"}:
            raise ProtocolValidationError(f"total_judgment_slots.{name} must list N4..N7")
        for roster_key, count in per_roster_map.items():
            _positive_int(count, f"total_judgment_slots.{name}.{roster_key}")
    if str(selected_name) not in str(slot_arithmetic.get("selected", "")):
        raise ProtocolValidationError(
            "slot_arithmetic_by_roster.selected must reference the selected tail configuration")

    decisions = _mapping(protocol.get("decisions"), "decisions")
    if set(decisions) != EXPECTED_DECISION_KEYS_V2:
        raise ProtocolValidationError(
            f"v2 decisions must be exactly {sorted(EXPECTED_DECISION_KEYS_V2)!r}")

    configuration_selection = _mapping(
        decisions["configuration_selection"], "decisions.configuration_selection")
    limits = _mapping(configuration_selection.get("limits"), "configuration_selection.limits")
    _positive_int(limits.get("R_max"), "configuration_selection.limits.R_max")
    _positive_int(limits.get("D_max"), "configuration_selection.limits.D_max")

    launch_gates = _mapping(decisions["launch_gates"], "decisions.launch_gates")
    smoke_subset = _unique_strings(
        launch_gates.get("budget_smoke_subset"), "launch_gates.budget_smoke_subset")
    if len(smoke_subset) != 6:
        raise ProtocolValidationError("launch_gates.budget_smoke_subset must list exactly 6 questions")

    # v2's canary_slot_inventory is a DIFFERENT shape than v1's: "per_judge" prose (not
    # "per_judge_formula"), and a "six_judge_totals" object carrying the fresh/carried/combined
    # counts this task's manifest anchor-carry binding and orchestrator convergence arithmetic
    # both depend on (decisions.launch_gates.canary_slot_inventory "drives everything below").
    canary_inventory = _mapping(
        launch_gates.get("canary_slot_inventory"), "launch_gates.canary_slot_inventory")
    _non_empty_string(canary_inventory.get("per_judge"), "launch_gates.canary_slot_inventory.per_judge")
    _non_empty_string(
        canary_inventory.get("pinned_denominator_rule"),
        "launch_gates.canary_slot_inventory.pinned_denominator_rule")
    six_judge_totals = _mapping(
        canary_inventory.get("six_judge_totals"), "launch_gates.canary_slot_inventory.six_judge_totals")
    fresh_slots = _positive_int(
        six_judge_totals.get("fresh_v2_judgment_slots"),
        "canary_slot_inventory.six_judge_totals.fresh_v2_judgment_slots")
    carried_anchors = _positive_int(
        six_judge_totals.get("carried_anchor_cells"),
        "canary_slot_inventory.six_judge_totals.carried_anchor_cells")
    combined = _positive_int(
        six_judge_totals.get("combined_gate_inventory"),
        "canary_slot_inventory.six_judge_totals.combined_gate_inventory")
    if fresh_slots != 1152 or carried_anchors != 288 or combined != 1440:
        raise ProtocolValidationError(
            "canary_slot_inventory.six_judge_totals must be exactly fresh_v2_judgment_slots=1152, "
            "carried_anchor_cells=288, combined_gate_inventory=1440")
    if combined != fresh_slots + carried_anchors:
        raise ProtocolValidationError(
            "canary_slot_inventory.six_judge_totals.combined_gate_inventory must equal "
            "fresh_v2_judgment_slots + carried_anchor_cells")

    _non_empty_string(
        launch_gates.get("canary_scope"), "decisions.launch_gates.canary_scope")
    calibration_gates = _mapping(
        launch_gates.get("calibration_gates_per_judge"), "launch_gates.calibration_gates_per_judge")
    if not calibration_gates:
        raise ProtocolValidationError("launch_gates.calibration_gates_per_judge must be non-empty")

    pace_window = _mapping(
        launch_gates.get("pace_measurement_window"), "decisions.launch_gates.pace_measurement_window")
    for field in ("opens", "closes", "counted", "denominator", "quota_day_definition",
                  "interruption_rule", "crank_settings_freeze"):
        _non_empty_string(pace_window.get(field), f"pace_measurement_window.{field}")

    context_guard = _mapping(decisions["context_guard"], "decisions.context_guard")
    for field in ("status", "context_estimator", "visible_history_caps", "validation_gate",
                  "eligibility", "common_support_rule", "independent_review"):
        _non_empty_string(context_guard.get(field), f"decisions.context_guard.{field}")

    transcript_reuse = _mapping(protocol.get("transcript_reuse"), "transcript_reuse")
    policy = _non_empty_string(transcript_reuse.get("policy"), "transcript_reuse.policy")
    if "ZERO debater calls" not in policy:
        raise ProtocolValidationError("transcript_reuse.policy must assert zero debater calls")
    main_bundle = _mapping(transcript_reuse.get("main_bundle"), "transcript_reuse.main_bundle")
    for field in ("content", "source_archive", "materialization", "verifier"):
        _non_empty_string(main_bundle.get(field), f"transcript_reuse.main_bundle.{field}")
    canary_bundle = _mapping(transcript_reuse.get("canary_bundle"), "transcript_reuse.canary_bundle")
    for field in ("content", "source_archive", "materialization"):
        _non_empty_string(canary_bundle.get(field), f"transcript_reuse.canary_bundle.{field}")
    _non_empty_string(
        transcript_reuse.get("ingestion_mechanics"), "transcript_reuse.ingestion_mechanics")

    # supersedes: the ratification's core semantic claim -- v2 supersedes the IMMUTABLE v1
    # document, named here by its own frozen pin, never re-derived.
    supersedes = _mapping(protocol.get("supersedes"), "supersedes")
    _non_empty_string(supersedes.get("protocol_id"), "supersedes.protocol_id")
    if supersedes.get("canonical_sha256") != FROZEN_PROTOCOL_CANONICAL_SHA256:
        raise ProtocolValidationError(
            "supersedes.canonical_sha256 must name the immutable v1 protocol's own frozen pin")
    _non_empty_string(supersedes.get("reason"), "supersedes.reason")
    amendments_folded_in = _unique_strings(
        supersedes.get("amendments_folded_in"), "supersedes.amendments_folded_in")
    if not amendments_folded_in:
        raise ProtocolValidationError("supersedes.amendments_folded_in must be non-empty")

    observed_hash = canonical_sha256(protocol)
    if observed_hash != FROZEN_PROTOCOL_V2_CANONICAL_SHA256:
        raise ProtocolValidationError(
            "frozen phase-3 v2 protocol hash drift: observed "
            f"{observed_hash}, expected {FROZEN_PROTOCOL_V2_CANONICAL_SHA256}")


def _validate_protocol_v3(protocol: Mapping[str, Any]) -> None:
    """Validate a successor protocol produced after the final roster resolves.

    The exact v3 document cannot be code-pinned before the conditional Qwen2.5 outcome exists.
    The generated document instead binds the immutable v2 protocol, the owner-approved v3
    design, and one roster-resolution artifact. A self-contained content digest detects drift;
    the run manifest later pins the full canonical protocol hash.
    """
    if protocol.get("schema_version") != "phase3_plan_v3":
        raise ProtocolValidationError("unsupported schema_version")
    if protocol.get("status") != "materialized_offline_protocol":
        raise ProtocolValidationError("v3 status must be materialized_offline_protocol")
    if protocol.get("offline_planning_only") is not True:
        raise ProtocolValidationError("offline_planning_only must be true")
    if protocol.get("execution_authorized") is not False:
        raise ProtocolValidationError("execution_authorized must be false")
    protocol_id = protocol.get("protocol_id")
    recovery4 = protocol_id == "phase3_budget_knob_2026_08_28_v3r6"
    # Each recovery generation folds in and re-binds everything its predecessors bound, so
    # every `recovery` requirement below applies to all of them; generation-specific
    # expectations are selected newest-first where the generations differ.
    recovery3 = protocol_id == "phase3_budget_knob_2026_08_27_v3r5" or recovery4
    recovery2 = protocol_id == "phase3_budget_knob_2026_08_25_v3r4" or recovery3
    recovery = protocol_id == "phase3_budget_knob_2026_08_24_v3r2" or recovery2
    if protocol_id not in {
            "phase3_budget_knob_2026_08_23_v3",
            "phase3_budget_knob_2026_08_24_v3r2",
            "phase3_budget_knob_2026_08_25_v3r4",
            "phase3_budget_knob_2026_08_27_v3r5",
            "phase3_budget_knob_2026_08_28_v3r6"}:
        raise ProtocolValidationError("unexpected v3 protocol_id")
    if recovery3 and (
            FROZEN_V3_RECOVERY3_AMENDMENT_CANONICAL_SHA256 is None
            or FROZEN_JUDGMENT_SCREEN_RESULTS_CANONICAL_SHA256 is None):
        raise ProtocolValidationError(
            "recovery3 bindings are not frozen yet: the stage-3 screening evidence and "
            "amendment 8 must exist before any r5 protocol can validate")
    if recovery4 and FROZEN_V3_RECOVERY4_AMENDMENT_CANONICAL_SHA256 is None:
        raise ProtocolValidationError(
            "recovery4 bindings are not frozen yet: amendment 9 must exist before any "
            "r6 protocol can validate")

    content_digest = _sha256_string(
        protocol.get("protocol_content_sha256"), "protocol_content_sha256")
    content = dict(protocol)
    del content["protocol_content_sha256"]
    observed_content_digest = canonical_sha256(content)
    if observed_content_digest != content_digest:
        raise ProtocolValidationError(
            "phase-3 v3 protocol content digest drift: observed "
            f"{observed_content_digest}, expected {content_digest}")

    planning_identity = _mapping(
        protocol.get("planning_cell_identity"), "planning_cell_identity")
    if planning_identity.get("status") != "planning_only_not_executable":
        raise ProtocolValidationError("v3 planning cell keys cannot be executable")
    question_bank_sha = _sha256_string(
        planning_identity.get("question_bank_bundle_sha256"),
        "planning_cell_identity.question_bank_bundle_sha256",
    )
    resolution_sha = _sha256_string(
        planning_identity.get("roster_resolution_sha256"),
        "planning_cell_identity.roster_resolution_sha256",
    )
    amendment_sha = _sha256_string(
        planning_identity.get("roster_amendment_sha256"),
        "planning_cell_identity.roster_amendment_sha256",
    )
    namespace = _non_empty_string(protocol.get("cell_key_namespace"), "cell_key_namespace")
    namespace_prefix = (
        "phase3-budget-knob-2026-08-28-v3r6" if recovery4
        else "phase3-budget-knob-2026-08-27-v3r5" if recovery3
        else "phase3-budget-knob-2026-08-25-v3r4" if recovery2
        else "phase3-budget-knob-2026-08-24-v3r2" if recovery
        else "phase3-budget-knob-2026-08-23-v3")
    expected_namespace = (
        f"{namespace_prefix}.rr-{resolution_sha[:12]}."
        f"ra-{amendment_sha[:12]}.qb-{question_bank_sha[:12]}")
    if namespace != expected_namespace:
        raise ProtocolValidationError(
            "v3 namespace is not bound to roster resolution, amendment, and banks")

    source_bindings = _mapping(protocol.get("source_bindings"), "source_bindings")
    canonical_bindings = _mapping(
        source_bindings.get("canonical_json_sha256"),
        "source_bindings.canonical_json_sha256",
    )
    if canonical_bindings.get("rejudge/phase3_protocol_v2.json") != (
            FROZEN_PROTOCOL_V2_CANONICAL_SHA256):
        raise ProtocolValidationError("v3 must bind the immutable v2 protocol")
    if canonical_bindings.get(
            "rejudge/phase3_v3_successor_design_2026-08-23.json") != (
                FROZEN_SUCCESSOR_DESIGN_CANONICAL_SHA256):
        raise ProtocolValidationError("v3 must bind the owner-approved successor design")
    if canonical_bindings.get(
            "rejudge/phase3_v3_amendment1_qwen3_7_replacement_2026-08-23.json") != (
                FROZEN_V3_ROSTER_AMENDMENT_CANONICAL_SHA256):
        raise ProtocolValidationError("v3 must bind the owner-approved roster amendment")
    if recovery:
        recovery_bindings = {
            "rejudge/phase3_protocol_v3_r2.json": (
                FROZEN_PROTOCOL_V3_R2_CANONICAL_SHA256),
            "rejudge/phase3_v3_amendment2_qwen3_8_replacement_2026-08-24.json": (
                FROZEN_V3_RECOVERY_AMENDMENT_CANONICAL_SHA256),
            "rejudge/phase3_v3_r5_provider_halt_2026-08-24.json": (
                FROZEN_V3_R5_HALT_OBSERVATION_CANONICAL_SHA256),
            "rejudge/output/phase3_v3r2_provider_models_2026-08-24T2321Z.json": (
                FROZEN_V3R2_PROVIDER_CATALOG_CANONICAL_SHA256),
            "rejudge/output/phase3_v3r2_serverless_endpoints_2026-08-24T2321Z.json": (
                FROZEN_V3R2_SERVERLESS_ENDPOINTS_CANONICAL_SHA256),
            "rejudge/phase3_v3r2_qwen38_provider_chat_template_2026-08-24.json": (
                FROZEN_V3R2_QWEN38_PROVIDER_TEMPLATE_CANONICAL_SHA256),
        }
        for path, expected_sha in recovery_bindings.items():
            if canonical_bindings.get(path) != expected_sha:
                raise ProtocolValidationError(
                    f"v3 recovery source binding drifted for {path}")
    if recovery2:
        recovery2_bindings = {
            "rejudge/phase3_protocol_v3_r3.json": (
                FROZEN_PROTOCOL_V3_R3_CANONICAL_SHA256),
            "rejudge/phase3_v3_amendment4_gemma3n_replacement_2026-08-25.json": (
                FROZEN_V3_RECOVERY2_AMENDMENT_CANONICAL_SHA256),
            "rejudge/phase3_v3_r11_gemma3n_delisting_halt_2026-08-25.json": (
                FROZEN_V3_R11_HALT_OBSERVATION_CANONICAL_SHA256),
            "rejudge/output/phase3_v3r4_provider_models_2026-08-25T2130Z.json": (
                FROZEN_V3R4_PROVIDER_CATALOG_CANONICAL_SHA256),
            "rejudge/output/phase3_v3r4_serverless_endpoints_2026-08-25T2130Z.json": (
                FROZEN_V3R4_SERVERLESS_ENDPOINTS_CANONICAL_SHA256),
            "rejudge/phase3_v3_qwen35_9b_screening_2026-08-25.json": (
                FROZEN_QWEN35_9B_SCREENING_CANONICAL_SHA256),
        }
        for path, expected_sha in recovery2_bindings.items():
            if canonical_bindings.get(path) != expected_sha:
                raise ProtocolValidationError(
                    f"v3 recovery2 source binding drifted for {path}")
    if recovery3:
        recovery3_bindings = {
            "rejudge/phase3_protocol_v3_r4.json": (
                FROZEN_PROTOCOL_V3_R4_CANONICAL_SHA256),
            "rejudge/phase3_v3_amendment8_n2_roster_2026-08-27.json": (
                FROZEN_V3_RECOVERY3_AMENDMENT_CANONICAL_SHA256),
            "rejudge/phase3_v3_r21_empty_verdict_discovery_2026-08-27.json": (
                FROZEN_V3_R21_DISCOVERY_CANONICAL_SHA256),
            "rejudge/phase3_v3_judgment_screen_plan_2026-08-27.json": (
                FROZEN_JUDGMENT_SCREEN_PLAN_CANONICAL_SHA256),
            "rejudge/phase3_v3_judgment_screen_results_record_2026-08-27.json": (
                FROZEN_JUDGMENT_SCREEN_RESULTS_CANONICAL_SHA256),
        }
        for path, expected_sha in recovery3_bindings.items():
            if canonical_bindings.get(path) != expected_sha:
                raise ProtocolValidationError(
                    f"v3 recovery3 source binding drifted for {path}")
    if recovery4:
        recovery4_bindings = {
            "rejudge/phase3_protocol_v3_r5.json": (
                FROZEN_PROTOCOL_V3_R5_CANONICAL_SHA256),
            "rejudge/phase3_v3_amendment9_llama_checker_2026-08-28.json": (
                FROZEN_V3_RECOVERY4_AMENDMENT_CANONICAL_SHA256),
            "rejudge/phase3_v3_r25_concentration_bound_stop_2026-08-28.json": (
                FROZEN_V3_R25_STOP_CANONICAL_SHA256),
            "rejudge/phase3_v3_llama_checker_screen_plan_2026-08-28.json": (
                FROZEN_LLAMA_CHECKER_SCREEN_PLAN_CANONICAL_SHA256),
            "rejudge/phase3_v3_llama_checker_screen_bank_2026-08-28.json": (
                FROZEN_LLAMA_CHECKER_SCREEN_BANK_CANONICAL_SHA256),
            "rejudge/phase3_v3_llama_checker_screen_results_record_2026-08-28.json": (
                FROZEN_LLAMA_CHECKER_SCREEN_RESULTS_CANONICAL_SHA256),
        }
        for path, expected_sha in recovery4_bindings.items():
            if canonical_bindings.get(path) != expected_sha:
                raise ProtocolValidationError(
                    f"v3 recovery4 source binding drifted for {path}")
    if canonical_bindings.get("rejudge/phase2_prompt_bundle.json") != (
            FROZEN_PHASE2_PROMPT_BUNDLE_CANONICAL_SHA256):
        raise ProtocolValidationError("v3 must bind the exact reused prompt bundle")
    if canonical_bindings.get(
            "rejudge/phase2_checker_frozen_config_2026-07-23.json") != (
                FROZEN_CHECKER_CONFIG_CANONICAL_SHA256):
        raise ProtocolValidationError("v3 must bind the frozen query-checker configuration")
    if canonical_bindings.get(
            "rejudge/phase2_checker_validation_design_2026-07-18.json") != (
                FROZEN_CHECKER_VALIDATION_DESIGN_CANONICAL_SHA256):
        raise ProtocolValidationError("v3 must bind the frozen query-checker user template")
    resolution = _mapping(protocol.get("roster_resolution"), "roster_resolution")
    resolution_path = _non_empty_string(
        resolution.get("tracked_path"), "roster_resolution.tracked_path")
    if canonical_bindings.get(resolution_path) != resolution_sha:
        raise ProtocolValidationError("v3 roster-resolution source binding drifted")
    amendment_path = _non_empty_string(
        resolution.get("amendment_tracked_path"),
        "roster_resolution.amendment_tracked_path",
    )
    expected_amendment_path = (
        "rejudge/phase3_v3_amendment9_llama_checker_2026-08-28.json"
        if recovery4 else
        "rejudge/phase3_v3_amendment8_n2_roster_2026-08-27.json"
        if recovery3 else
        "rejudge/phase3_v3_amendment4_gemma3n_replacement_2026-08-25.json"
        if recovery2 else
        "rejudge/phase3_v3_amendment2_qwen3_8_replacement_2026-08-24.json"
        if recovery else
        "rejudge/phase3_v3_amendment1_qwen3_7_replacement_2026-08-23.json")
    if amendment_path != expected_amendment_path:
        raise ProtocolValidationError("v3 roster amendment path drifted")
    if canonical_bindings.get(amendment_path) != amendment_sha:
        raise ProtocolValidationError("v3 roster-amendment source binding drifted")
    if resolution.get("amendment_canonical_sha256") != amendment_sha:
        raise ProtocolValidationError("v3 roster amendment identity drifted")
    replacement = _mapping(resolution.get("replacement"), "roster_resolution.replacement")
    if recovery3:
        # recovery3 is a double removal, not a substitution: the weak slot closed on
        # completion-infeasibility evidence and gemma-4 failed verdict admission at every
        # screened cap (rare deterministic runaway; it keeps ONLY its screened checker
        # role), with no admissible serverless candidate added. recovery4 then ends even
        # the checker retention: the r25 stop measured gemma-4's runaway at 4.3% per call
        # on live queries, so the Llama-admitted checker substitution replaces it and
        # gemma-4 leaves the billed registry entirely.
        if replacement.get("removed_models") != list(PHASE3_V3_RECOVERY3_REMOVED_JUDGES):
            raise ProtocolValidationError("v3 recovery3 removed judges drifted")
        if "added_model" not in replacement or replacement.get("added_model") is not None:
            raise ProtocolValidationError("v3 recovery3 must record an explicit removal")
        if recovery4:
            if ("checker_only_model" not in replacement
                    or replacement.get("checker_only_model") is not None):
                raise ProtocolValidationError(
                    "v3 recovery4 must record the ended checker-only retention")
            if replacement.get("checker_substitution") != {
                "removed_checker": PHASE3_V3_RECOVERY4_REMOVED_CHECKER,
                "added_checker": PHASE3_V3_RECOVERY4_CHECKER_MODEL,
            }:
                raise ProtocolValidationError("v3 recovery4 checker substitution drifted")
        elif replacement.get("checker_only_model") != (
                PHASE3_V3_RECOVERY3_CHECKER_ONLY_MODEL):
            raise ProtocolValidationError("v3 recovery3 checker-only retention drifted")
    else:
        expected_removed = (
            PHASE3_V3_RECOVERY2_REPLACED_JUDGE if recovery2
            else PHASE3_V3_RECOVERY_REPLACED_JUDGE if recovery
            else PHASE3_V3_REPLACED_JUDGE)
        expected_added = (
            PHASE3_V3_RECOVERY2_REPLACEMENT_JUDGE if recovery2
            else PHASE3_V3_RECOVERY_REPLACEMENT_JUDGE if recovery
            else PHASE3_V3_REPLACEMENT_JUDGE)
        if replacement.get("removed_model") != expected_removed:
            raise ProtocolValidationError("v3 removed judge drifted")
        if replacement.get("added_model") != expected_added:
            raise ProtocolValidationError("v3 replacement judge drifted")
    if source_bindings.get("question_bank_bundle_sha256") != question_bank_sha:
        raise ProtocolValidationError("v3 question-bank binding drifted")

    authorization = _mapping(protocol.get("authorization"), "authorization")
    if authorization.get("design_scope_approved") is not True:
        raise ProtocolValidationError("v3 design scope must be owner approved")
    if authorization.get("canary_spend_authorized") is not False:
        raise ProtocolValidationError("v3 canary spend must remain separately unauthorized")
    if authorization.get("main_run_spend_authorized") is not False:
        raise ProtocolValidationError("v3 main spend must remain separately unauthorized")
    if recovery and authorization.get("amendment_record") != expected_amendment_path:
        raise ProtocolValidationError("v3 recovery authorization record drifted")

    question_set = _mapping(protocol.get("question_set"), "question_set")
    if question_set.get("expected_main_question_count") != 82:
        raise ProtocolValidationError("v3 must keep 82 main questions")
    if question_set.get("held_out_question_count") != 24:
        raise ProtocolValidationError("v3 must keep 24 held-out questions")

    roster = _mapping(protocol.get("roster"), "roster")
    judges = _unique_strings(roster.get("judges_final"), "roster.judges_final")
    expected_base_judges = (
        PHASE3_V3_RECOVERY3_BASE_JUDGES if recovery3
        else PHASE3_V3_RECOVERY2_BASE_JUDGES if recovery2
        else PHASE3_V3_RECOVERY_BASE_JUDGES if recovery
        else PHASE3_V3_BASE_JUDGES)
    if judges[:len(expected_base_judges)] != list(expected_base_judges):
        raise ProtocolValidationError(
            "v3 final roster must preserve the approved base judges in order")
    if recovery3:
        if len(judges) != 2:
            raise ProtocolValidationError(
                "v3 recovery3 roster must contain exactly two judges")
        expected_outcome = "excluded_completion_infeasible"
        if recovery4 and str(roster.get("query_checker")) != (
                PHASE3_V3_RECOVERY4_CHECKER_MODEL):
            raise ProtocolValidationError(
                "v3 recovery4 roster must pin the admitted Llama checker")
    elif recovery and len(judges) != 4:
        raise ProtocolValidationError("v3 recovery roster must contain exactly four judges")
    elif len(judges) == 5:
        if judges[-1] != PHASE3_V3_CONDITIONAL_JUDGE:
            raise ProtocolValidationError("the only permitted fifth v3 judge is Qwen2.5")
        expected_outcome = "included_recovery_pass"
    elif len(judges) == 4:
        expected_outcome = {
            "excluded_recovery_fail", "excluded_deadline", "excluded_main_authorization",
            "excluded_provider_unavailable",
        }
    else:
        raise ProtocolValidationError("v3 final roster must contain exactly 4 or 5 judges")
    if PHASE3_V3_EXCLUDED_JUDGE in judges:
        raise ProtocolValidationError("gpt-oss cannot rejoin the v3 roster")
    if roster.get("final_size") != len(judges):
        raise ProtocolValidationError("roster.final_size does not match judges_final")
    outcome = _non_empty_string(resolution.get("outcome"), "roster_resolution.outcome")
    if isinstance(expected_outcome, str):
        if outcome != expected_outcome:
            raise ProtocolValidationError(
                f"v3 roster resolution outcome must be {expected_outcome!r}")
    elif outcome not in expected_outcome:
        raise ProtocolValidationError("four-judge v3 roster requires an exclusion outcome")
    if roster.get("capability_slope_inference") != "estimate_and_plot_only_no_p_value":
        raise ProtocolValidationError("v3 capability slope must not report a p-value")
    debaters = _unique_strings(roster.get("debaters"), "roster.debaters")
    if len(debaters) != 2:
        raise ProtocolValidationError("v3 must retain exactly two reused-transcript debaters")
    for field in ("debater_role_note", "oracle", "query_checker", "gate_reviewer"):
        _non_empty_string(roster.get(field), f"roster.{field}")

    transcript_reuse = _mapping(protocol.get("transcript_reuse"), "transcript_reuse")
    policy = _non_empty_string(transcript_reuse.get("policy"), "transcript_reuse.policy")
    if "ZERO debater calls" not in policy:
        raise ProtocolValidationError("v3 transcript policy must assert zero debater calls")
    for bundle_name in ("main_bundle", "canary_bundle"):
        bundle = _mapping(transcript_reuse.get(bundle_name), f"transcript_reuse.{bundle_name}")
        for field in ("content", "source_archive", "materialization"):
            _non_empty_string(bundle.get(field), f"transcript_reuse.{bundle_name}.{field}")
    _non_empty_string(
        transcript_reuse.get("ingestion_mechanics"), "transcript_reuse.ingestion_mechanics")

    debate_grid = _mapping(protocol.get("debate_grid"), "debate_grid")
    if debate_grid.get("k") != 2:
        raise ProtocolValidationError("v3 debate-grid K must be 2")
    conditions = _list(debate_grid.get("conditions"), "debate_grid.conditions")
    seen_ids: set[str] = set()
    by_budget: dict[int, Mapping[str, Any]] = {}
    for index, raw_condition in enumerate(conditions):
        condition = _mapping(raw_condition, f"debate_grid.conditions[{index}]")
        condition_id = _non_empty_string(
            condition.get("id"), f"debate_grid.conditions[{index}].id")
        if condition_id in seen_ids:
            raise ProtocolValidationError(f"duplicate debate-grid condition {condition_id!r}")
        seen_ids.add(condition_id)
        budget = _non_negative_int(
            condition.get("query_budget"), f"debate_grid.conditions[{index}].query_budget")
        if budget in by_budget:
            raise ProtocolValidationError(f"duplicate query budget {budget}")
        by_budget[budget] = condition
        if condition.get("judgment_replicates_per_transcript_side") != 1:
            raise ProtocolValidationError("v3 configuration A requires one replicate per side")
    if set(by_budget) != {0, 1, 2, 4, 8}:
        raise ProtocolValidationError("v3 debate grid must cover budgets 0, 1, 2, 4, and 8")
    if debate_grid.get("selected_configuration") != "A_no_d":
        raise ProtocolValidationError("v3 selected configuration must remain A_no_d")
    arithmetic = _mapping(debate_grid.get("slot_arithmetic"), "debate_grid.slot_arithmetic")
    if arithmetic.get("per_judge_per_condition") != 984:
        raise ProtocolValidationError("v3 per-judge per-condition slot count must be 984")
    if arithmetic.get("per_judge_total") != 4920:
        raise ProtocolValidationError("v3 per-judge total slot count must be 4920")
    if arithmetic.get("final_roster_size") != len(judges):
        raise ProtocolValidationError("v3 slot arithmetic roster size drifted")
    if arithmetic.get("total_judgment_slots") != 4920 * len(judges):
        raise ProtocolValidationError("v3 total judgment-slot count drifted")

    decisions = _mapping(protocol.get("decisions"), "decisions")
    if set(decisions) != EXPECTED_DECISION_KEYS_V2:
        raise ProtocolValidationError(
            f"v3 decisions must be exactly {sorted(EXPECTED_DECISION_KEYS_V2)!r}")
    capability_slope = _mapping(
        _mapping(decisions.get("secondary_analyses"), "decisions.secondary_analyses").get(
            "capability_slope"),
        "decisions.secondary_analyses.capability_slope",
    )
    if capability_slope.get("p_value_rule") != "never_report_with_v3_roster_below_6":
        raise ProtocolValidationError("v3 capability-slope p-value rule drifted")

    launch_gates = _mapping(decisions.get("launch_gates"), "decisions.launch_gates")
    smoke_subset = _unique_strings(
        launch_gates.get("budget_smoke_subset"), "launch_gates.budget_smoke_subset")
    if len(smoke_subset) != 6:
        raise ProtocolValidationError("v3 budget smoke must contain exactly six questions")
    inventory = _mapping(
        launch_gates.get("canary_slot_inventory"),
        "decisions.launch_gates.canary_slot_inventory",
    )
    per_judge = _mapping(inventory.get("per_judge"), "canary_slot_inventory.per_judge")
    expected_per_judge = {
        "core_b0_judgment_slots": 96,
        "budget_smoke_judgment_slots": 96,
        "capability_anchor_slots": 48,
        "combined_fresh_gate_slots": 240,
    }
    if dict(per_judge) != expected_per_judge:
        raise ProtocolValidationError("v3 per-judge canary inventory drifted")
    if inventory.get("final_roster_size") != len(judges):
        raise ProtocolValidationError("v3 canary inventory roster size drifted")
    if inventory.get("fresh_judgment_slots") != 192 * len(judges):
        raise ProtocolValidationError("v3 fresh canary judgment count drifted")
    if inventory.get("fresh_capability_anchor_slots") != 48 * len(judges):
        raise ProtocolValidationError("v3 fresh capability-anchor count drifted")
    if inventory.get("combined_fresh_gate_slots") != 240 * len(judges):
        raise ProtocolValidationError("v3 combined canary count drifted")
    if inventory.get("carry_forward_result_rows") != 0:
        raise ProtocolValidationError("v3 cannot carry v1 or v2 result rows")

    spend = _mapping(decisions.get("spend"), "decisions.spend")
    if spend.get("status") != (
            "canary_pending_separate_authorization_main_pending_forecast"):
        raise ProtocolValidationError("v3 spend status must preserve the two-stage boundary")
    authorization_rule = _mapping(
        spend.get("authorization_rule"), "decisions.spend.authorization_rule")
    if set(authorization_rule) != {"successor_canary", "main"}:
        raise ProtocolValidationError("v3 spend authorization stages drifted")
    canary_authorization = _mapping(
        authorization_rule.get("successor_canary"),
        "decisions.spend.authorization_rule.successor_canary",
    )
    if canary_authorization.get("separate_owner_authorization_required") is not True:
        raise ProtocolValidationError("v3 successor canary requires separate owner authorization")
    if canary_authorization.get("certified_main_forecast_required") is not False:
        raise ProtocolValidationError(
            "v3 successor canary cannot depend on its downstream main forecast")
    _non_empty_string(
        canary_authorization.get("dependency_reason"),
        "decisions.spend.authorization_rule.successor_canary.dependency_reason",
    )
    main_authorization = _mapping(
        authorization_rule.get("main"), "decisions.spend.authorization_rule.main")
    if main_authorization.get("separate_owner_authorization_required") is not True:
        raise ProtocolValidationError("v3 main run requires separate owner authorization")
    if main_authorization.get("certified_main_forecast_required") is not True:
        raise ProtocolValidationError("v3 main run requires a certified forecast")
    _non_empty_string(
        main_authorization.get("forecast_input_requirement"),
        "decisions.spend.authorization_rule.main.forecast_input_requirement",
    )

    forecast = _mapping(
        spend.get("forecast_contract"),
        "decisions.spend.forecast_contract",
    )
    for field in (
        "slot_frame", "attempt_accounting", "missing_reservation_split", "billing_model_key",
        "cluster_estimator", "exact_context", "dynamic_context", "completion_tokens",
        "price_snapshot", "rounding", "cumulative_spend", "proxy_or_byte_bound_policy",
    ):
        _non_empty_string(forecast.get(field), f"decisions.spend.forecast_contract.{field}")
    if forecast.get("transport_multiplier") != 1.15:
        raise ProtocolValidationError("v3 forecast transport multiplier must be 1.15")
    if forecast.get("required_main_transcript_count") != 492:
        raise ProtocolValidationError("v3 exact-context corpus must contain 492 transcripts")

    supersedes = _mapping(protocol.get("supersedes"), "supersedes")
    expected_superseded_sha = (
        FROZEN_PROTOCOL_V3_R5_CANONICAL_SHA256 if recovery4
        else FROZEN_PROTOCOL_V3_R4_CANONICAL_SHA256 if recovery3
        else FROZEN_PROTOCOL_V3_R3_CANONICAL_SHA256 if recovery2
        else FROZEN_PROTOCOL_V3_R2_CANONICAL_SHA256 if recovery
        else FROZEN_PROTOCOL_V2_CANONICAL_SHA256)
    if supersedes.get("canonical_sha256") != expected_superseded_sha:
        raise ProtocolValidationError("v3 superseded protocol binding drifted")
    expected_superseded_id = (
        "phase3_budget_knob_2026_08_27_v3r5" if recovery4
        else "phase3_budget_knob_2026_08_25_v3r4" if recovery3
        else "phase3_budget_knob_2026_08_24_v3r2" if recovery2
        else "phase3_budget_knob_2026_08_23_v3" if recovery
        else None)
    if (expected_superseded_id is not None
            and supersedes.get("protocol_id") != expected_superseded_id):
        raise ProtocolValidationError("v3 recovery supersedes the wrong protocol identity")
    _non_empty_string(supersedes.get("reason"), "supersedes.reason")


def load_protocol(path: str | Path = DEFAULT_PROTOCOL_PATH) -> dict[str, Any]:
    protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(protocol, dict):
        raise ProtocolValidationError("protocol root must be an object")
    validate_protocol(protocol)
    return protocol


def load_reference_question_ids(
    protocol: Mapping[str, Any], project_root: str | Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Load the (82 main, 24 held-out) question IDs from the bound, byte-identical phase-2 banks.

    ``question_set.identical_to`` declares phase 3's question set identical to phase 2's; this
    function never re-derives or duplicates that split. It loads the real ``phase2_protocol.json``
    (hash-bound in ``source_bindings.canonical_json_sha256``, verified here) and reuses
    :func:`phase2_plan.load_main_question_ids` for the 82-question split rather than
    re-implementing it. Returns ``(main_question_ids, held_out_question_ids)``, both sorted.
    """
    root = Path(project_root)
    sources = _mapping(protocol.get("sources"), "sources")
    relative_path = str(sources.get("phase2_protocol"))
    bound = _mapping(
        _mapping(protocol.get("source_bindings"), "source_bindings").get("canonical_json_sha256"),
        "source_bindings.canonical_json_sha256")
    expected_sha = bound.get(relative_path)
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise ProtocolValidationError(
            f"{relative_path} must be bound with a SHA-256 hash in source_bindings")

    phase2_protocol = phase2_plan.load_protocol(root / relative_path)
    observed_sha = canonical_sha256(phase2_protocol)
    if observed_sha != expected_sha:
        raise ProtocolValidationError(
            f"bound {relative_path} hash drift: observed {observed_sha}, expected {expected_sha}")

    main_ids = phase2_plan.load_main_question_ids(phase2_protocol, root)
    held_out_ids = tuple(sorted(
        _mapping(phase2_protocol["question_set"], "phase2 question_set")[
            "calibration_excluded_question_ids"]))

    question_set = _mapping(protocol.get("question_set"), "question_set")
    expected_main = int(question_set["expected_main_question_count"])
    expected_held_out = int(question_set["held_out_question_count"])
    if len(main_ids) != expected_main:
        raise ProtocolValidationError(
            f"phase-2 main question IDs total {len(main_ids)}, expected {expected_main}")
    if len(held_out_ids) != expected_held_out:
        raise ProtocolValidationError(
            f"phase-2 held-out question IDs total {len(held_out_ids)}, expected {expected_held_out}")
    return main_ids, held_out_ids


def candidate_roster_judges(protocol: Mapping[str, Any], n: int) -> list[str]:
    """Return the protocol-ordered judges for a requested planning roster size.

    v1 and v2 expose a continuing-plus-candidate roster. A v3 protocol exists only after roster
    resolution, so v3 accepts only its exact final size and returns ``judges_final``.
    """
    roster = _mapping(protocol["roster"], "roster")
    if protocol.get("schema_version") == "phase3_plan_v3":
        judges = list(_unique_strings(roster.get("judges_final"), "roster.judges_final"))
        if n != len(judges):
            raise PlanValidationError(
                f"v3 roster is resolved at exactly {len(judges)} judges, not {n}")
        return judges

    continuing = list(_unique_strings(roster["judges_continuing"], "roster.judges_continuing"))
    new_candidates = [
        str(_mapping(candidate, "roster.judges_new item")["candidate_model_id"])
        for candidate in _list(roster["judges_new"], "roster.judges_new")
    ]
    max_n = len(continuing) + len(new_candidates)
    if not (len(continuing) <= n <= max_n):
        raise PlanValidationError(
            f"n must be between {len(continuing)} and {max_n} candidate judges")
    return continuing + new_candidates[: n - len(continuing)]


def _cell(
    namespace: str,
    *,
    kind: str,
    condition: str,
    question_id: str,
    judge_model: str | None,
    debater_model: str | None,
    transcript_index: int | None,
    replicate_index: int | None,
    query_budget: int | None,
    dependency_keys: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "cell_key": make_cell_key(
            namespace,
            kind=kind,
            condition=condition,
            question_id=question_id,
            judge_model=judge_model,
            debater_model=debater_model,
            transcript_index=transcript_index,
            replicate_index=replicate_index,
            query_budget=query_budget,
        ),
        "kind": kind,
        "condition": condition,
        "question_id": question_id,
        "judge_model": judge_model,
        "debater_model": debater_model,
        "transcript_index": transcript_index,
        "replicate_index": replicate_index,
        "query_budget": query_budget,
        "dependency_keys": list(dependency_keys),
    }


def enumerate_cells(
    protocol: Mapping[str, Any],
    roster_judges: Iterable[str],
    main_question_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Enumerate the phase-3 main grid: transcript-reference cells plus judgment slots.

    For each condition in ``debate_grid.conditions`` (read generically, never a hard-coded
    tuple) x 82 main questions x ``roster_judges`` x 2 debaters x 3 transcripts x K2 sides x
    that condition's ``judgment_replicates_per_transcript_side``: one judgment cell
    (``kind == MAIN_JUDGMENT_KIND``), the provider-billed "slot" per ``unit_definitions``. Side
    and within-side replicate are folded into one ``replicate_index`` (``side * replicates +
    within_side_replicate``), a bijection onto ``0 .. 2*replicates-1`` that keeps
    :func:`make_cell_key`'s signature unmodified while still giving every (side, replicate)
    combination a distinct, stable key.

    Also enumerates the transcript-reference cells (``kind == MAIN_TRANSCRIPT_KIND``, one per
    (debater, question, transcript index), 492 total) that a later ingestion script pre-seeds
    per ``transcript_reuse.ingestion_mechanics``; every judgment cell depends on exactly one.
    """
    validate_protocol(protocol)
    judges = tuple(roster_judges)
    if not judges:
        raise PlanValidationError("roster_judges must be non-empty")
    if len(judges) != len(set(judges)):
        raise PlanValidationError("roster_judges contains duplicates")

    question_ids = tuple(sorted(main_question_ids))
    if len(question_ids) != len(set(question_ids)):
        raise PlanValidationError("main_question_ids contains duplicates")
    expected_questions = int(_mapping(protocol["question_set"], "question_set")
                              ["expected_main_question_count"])
    if len(question_ids) != expected_questions:
        raise PlanValidationError(
            f"received {len(question_ids)} main question IDs, expected {expected_questions}")

    namespace = str(protocol["cell_key_namespace"])
    roster = _mapping(protocol["roster"], "roster")
    debaters = list(roster["debaters"])
    debate_grid = _mapping(protocol["debate_grid"], "debate_grid")
    k2 = int(debate_grid["k"])
    conditions = [
        _mapping(condition, "debate condition") for condition in debate_grid["conditions"]
    ]

    cells: list[dict[str, Any]] = []
    transcript_keys: dict[tuple[str, str, int], str] = {}
    for debater_model in debaters:
        for question_id in question_ids:
            for transcript_index in range(MAIN_TRANSCRIPTS_PER_QUESTION_PER_DEBATER):
                transcript = _cell(
                    namespace,
                    kind=MAIN_TRANSCRIPT_KIND,
                    condition=MAIN_TRANSCRIPT_CONDITION,
                    question_id=question_id,
                    judge_model=None,
                    debater_model=debater_model,
                    transcript_index=transcript_index,
                    replicate_index=None,
                    query_budget=None,
                )
                transcript_keys[(debater_model, question_id, transcript_index)] = transcript[
                    "cell_key"]
                cells.append(transcript)

    for condition in conditions:
        condition_id = str(condition["id"])
        query_budget = int(condition["query_budget"])
        replicates = int(condition["judgment_replicates_per_transcript_side"])
        for question_id in question_ids:
            for judge_model in judges:
                for debater_model in debaters:
                    for transcript_index in range(MAIN_TRANSCRIPTS_PER_QUESTION_PER_DEBATER):
                        transcript_key = transcript_keys[
                            (debater_model, question_id, transcript_index)]
                        for side in range(k2):
                            for within_side_replicate in range(replicates):
                                cells.append(_cell(
                                    namespace,
                                    kind=MAIN_JUDGMENT_KIND,
                                    condition=condition_id,
                                    question_id=question_id,
                                    judge_model=judge_model,
                                    debater_model=debater_model,
                                    transcript_index=transcript_index,
                                    replicate_index=side * replicates + within_side_replicate,
                                    query_budget=query_budget,
                                    dependency_keys=[transcript_key],
                                ))

    validate_cells(cells, namespace)
    return cells


def enumerate_canary_cells(
    protocol: Mapping[str, Any],
    candidate_judges: Iterable[str],
    held_out_question_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Enumerate the phase-3 canary: core b0 gates, budget smoke, and the capability anchor.

    All three components are driven off the protocol JSON, nothing hard-coded that the
    protocol specifies:

    * core b0 gates -- the condition with ``query_budget == 0`` (located generically, never by
      literal id), all 24 held-out questions x 2 debaters x 1 transcript x K2 sides x that
      condition's replicate count.
    * budget smoke -- every condition with ``query_budget > 0`` (b1/b2/b4/b8, located the same
      way), ``decisions.launch_gates.budget_smoke_subset`` questions x 2 debaters x 1 transcript
      x K2 sides x that condition's replicate count (all 1 under the selected config A, summing
      to the protocol's documented "4" multiplier).
    * capability anchor -- ``kind == CAPABILITY_ANCHOR_KIND``, all 24 held-out questions x K2
      mirrored sides per judge, no transcript dependency (matches
      ``decisions.capability_anchor.cells``).

    Also enumerates canary transcript-reference cells (one per (debater, held-out question),
    48 total -- the canary bundle has only 1 transcript per question per debater).
    """
    validate_protocol(protocol)
    judges = tuple(candidate_judges)
    if not judges:
        raise PlanValidationError("candidate_judges must be non-empty")
    if len(judges) != len(set(judges)):
        raise PlanValidationError("candidate_judges contains duplicates")

    canary_questions = tuple(sorted(held_out_question_ids))
    if len(canary_questions) != len(set(canary_questions)):
        raise PlanValidationError("held_out_question_ids contains duplicates")
    expected_held_out = int(_mapping(protocol["question_set"], "question_set")
                             ["held_out_question_count"])
    if len(canary_questions) != expected_held_out:
        raise PlanValidationError(
            f"received {len(canary_questions)} held-out question IDs, expected "
            f"{expected_held_out}")

    namespace = str(protocol["cell_key_namespace"])
    roster = _mapping(protocol["roster"], "roster")
    debaters = list(roster["debaters"])
    debate_grid = _mapping(protocol["debate_grid"], "debate_grid")
    k2 = int(debate_grid["k"])
    conditions = [
        _mapping(condition, "debate condition") for condition in debate_grid["conditions"]
    ]
    by_budget = {int(condition["query_budget"]): condition for condition in conditions}
    core_condition = by_budget[0]
    smoke_conditions = [condition for budget, condition in sorted(by_budget.items())
                        if budget > 0]

    launch_gates = _mapping(_mapping(protocol["decisions"], "decisions")["launch_gates"],
                            "launch_gates")
    smoke_subset = tuple(sorted(launch_gates["budget_smoke_subset"]))
    if not set(smoke_subset).issubset(canary_questions):
        raise PlanValidationError("budget_smoke_subset is not a subset of the held-out questions")

    cells: list[dict[str, Any]] = []
    transcript_keys: dict[tuple[str, str], str] = {}
    for debater_model in debaters:
        for question_id in canary_questions:
            transcript = _cell(
                namespace,
                kind=CANARY_TRANSCRIPT_KIND,
                condition=CANARY_TRANSCRIPT_CONDITION,
                question_id=question_id,
                judge_model=None,
                debater_model=debater_model,
                transcript_index=0,
                replicate_index=None,
                query_budget=None,
            )
            transcript_keys[(debater_model, question_id)] = transcript["cell_key"]
            cells.append(transcript)

    def _debate_judgment_cells(
        condition: Mapping[str, Any], question_ids: Sequence[str],
    ) -> Iterable[dict[str, Any]]:
        condition_id = str(condition["id"])
        query_budget = int(condition["query_budget"])
        replicates = int(condition["judgment_replicates_per_transcript_side"])
        for judge_model in judges:
            for question_id in question_ids:
                for debater_model in debaters:
                    transcript_key = transcript_keys[(debater_model, question_id)]
                    for side in range(k2):
                        for within_side_replicate in range(replicates):
                            yield _cell(
                                namespace,
                                kind=CANARY_JUDGMENT_KIND,
                                condition=condition_id,
                                question_id=question_id,
                                judge_model=judge_model,
                                debater_model=debater_model,
                                transcript_index=0,
                                replicate_index=side * replicates + within_side_replicate,
                                query_budget=query_budget,
                                dependency_keys=[transcript_key],
                            )

    cells.extend(_debate_judgment_cells(core_condition, canary_questions))
    for condition in smoke_conditions:
        cells.extend(_debate_judgment_cells(condition, smoke_subset))

    for judge_model in judges:
        for question_id in canary_questions:
            for mirror_index in range(k2):
                cells.append(_cell(
                    namespace,
                    kind=CAPABILITY_ANCHOR_KIND,
                    condition=CAPABILITY_ANCHOR_CONDITION,
                    question_id=question_id,
                    judge_model=judge_model,
                    debater_model=None,
                    transcript_index=None,
                    replicate_index=mirror_index,
                    query_budget=None,
                    dependency_keys=[],
                ))

    validate_cells(cells, namespace)
    return cells


def duplicate_cell_keys(cells: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    counts = Counter(str(cell.get("cell_key")) for cell in cells)
    return tuple(sorted(key for key, count in counts.items() if count > 1))


def validate_cells(cells: Sequence[Mapping[str, Any]], namespace: str) -> None:
    """Reject duplicate keys, unstable keys, missing dependencies, and bad transcript links."""
    duplicates = duplicate_cell_keys(cells)
    if duplicates:
        raise PlanValidationError(f"duplicate cell keys: {duplicates[:5]!r}")
    by_key = {str(cell["cell_key"]): cell for cell in cells}
    for cell in cells:
        expected_key = make_cell_key(
            namespace,
            kind=str(cell["kind"]),
            condition=str(cell["condition"]),
            question_id=str(cell["question_id"]),
            judge_model=cell.get("judge_model"),
            debater_model=cell.get("debater_model"),
            transcript_index=cell.get("transcript_index"),
            replicate_index=cell.get("replicate_index"),
            query_budget=cell.get("query_budget"),
        )
        if cell["cell_key"] != expected_key:
            raise PlanValidationError(f"unstable or malformed cell key: {cell['cell_key']!r}")
        dependencies = cell.get("dependency_keys")
        if not isinstance(dependencies, list) or not all(
                isinstance(key, str) for key in dependencies):
            raise PlanValidationError(f"invalid dependency list for {cell['cell_key']}")
        missing = [key for key in dependencies if key not in by_key]
        if missing:
            raise PlanValidationError(
                f"missing dependencies for {cell['cell_key']}: {missing!r}")

        if cell["kind"] in (MAIN_JUDGMENT_KIND, CANARY_JUDGMENT_KIND):
            transcript_dependencies = [
                by_key[key] for key in dependencies if by_key[key]["kind"] in TRANSCRIPT_KINDS
            ]
            if len(transcript_dependencies) != 1 or len(dependencies) != 1:
                raise PlanValidationError(
                    f"judgment {cell['cell_key']} needs exactly one transcript dependency")
            transcript = transcript_dependencies[0]
            for field in ("question_id", "debater_model", "transcript_index"):
                if transcript[field] != cell[field]:
                    raise PlanValidationError(
                        f"transcript dependency dimension mismatch for {cell['cell_key']}")

        if cell["kind"] in TRANSCRIPT_KINDS:
            if cell.get("judge_model") is not None or dependencies:
                raise PlanValidationError(
                    f"transcript cell {cell['cell_key']} has a judge or dependency")

        if cell["kind"] == CAPABILITY_ANCHOR_KIND:
            if (cell.get("debater_model") is not None
                    or cell.get("transcript_index") is not None or dependencies):
                raise PlanValidationError(
                    f"capability cell {cell['cell_key']} has forbidden dimensions/dependencies")


def summarize_cells(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_kind = Counter(str(cell["kind"]) for cell in cells)
    transcript_cells = sum(count for kind, count in by_kind.items() if kind in TRANSCRIPT_KINDS)
    return {
        "all_cells": len(cells),
        "by_kind": dict(sorted(by_kind.items())),
        "transcript_cells": transcript_cells,
        # "slot" per unit_definitions: every fully-expanded judgment unit, i.e. every cell that
        # is not itself a transcript-reference cell.
        "slot_count": len(cells) - transcript_cells,
    }


def selftest(project_root: str | Path | None = None) -> dict[str, Any]:
    """Assert the enumerator matches the frozen slot arithmetic before any plan is accepted.

    Checks the main-grid judgment-slot count for every candidate roster size N in {4, 5, 6, 7}
    against ``debate_grid.slot_arithmetic_by_roster.total_judgment_slots.A_no_d`` and the canary
    judgment-slot count at N=7 against the selected-config, 7-candidate-judge total in
    ``decisions.launch_gates.canary_slot_inventory`` (1,680). Returns the observed counts.
    """
    protocol = load_protocol()
    root = (Path(project_root) if project_root is not None
            else DEFAULT_PROTOCOL_PATH.resolve().parent.parent)
    main_question_ids, held_out_question_ids = load_reference_question_ids(protocol, root)

    debate_grid = _mapping(protocol["debate_grid"], "debate_grid")
    expected_by_n = _mapping(
        _mapping(debate_grid["slot_arithmetic_by_roster"], "slot_arithmetic_by_roster")
        ["total_judgment_slots"]["A_no_d"],
        "total_judgment_slots.A_no_d",
    )

    main_slots_by_n: dict[int, int] = {}
    for n in (4, 5, 6, 7):
        judges = candidate_roster_judges(protocol, n)
        cells = enumerate_cells(protocol, judges, main_question_ids)
        slot_count = summarize_cells(cells)["slot_count"]
        main_slots_by_n[n] = slot_count
        expected = int(expected_by_n[f"N{n}"])
        if slot_count != expected:
            raise PlanValidationError(
                f"main judgment-slot count at N={n} is {slot_count}, expected {expected} per "
                "debate_grid.slot_arithmetic_by_roster.total_judgment_slots.A_no_d")

    judges7 = candidate_roster_judges(protocol, 7)
    canary_cells = enumerate_canary_cells(protocol, judges7, held_out_question_ids)
    canary_slot_count = summarize_cells(canary_cells)["slot_count"]
    if canary_slot_count != 1680:
        raise PlanValidationError(
            f"canary judgment-slot count at N=7 is {canary_slot_count}, expected 1680 per "
            "decisions.launch_gates.canary_slot_inventory (selected config A, 7 candidates)")

    return {"main_slots_by_n": main_slots_by_n, "canary_slots_n7": canary_slot_count}


def selftest_v2(project_root: str | Path | None = None) -> dict[str, Any]:
    """Assert the v2 enumerator matches the ratified v2 slot arithmetic before any plan is
    accepted.

    v2 has exactly one candidate roster size (4 continuing + 2 new = 6; unlike v1's N4..N7
    ladder, v2's ``judges_new`` always has exactly 2 entries -- enforced by
    :func:`_validate_protocol_v2`), so this checks exactly three numbers, all pinned in the
    ratified protocol: main judgment slots at N6 (``debate_grid.slot_arithmetic_by_roster.
    total_judgment_slots.A_no_d.N6``, 29,520), and the canary's fresh judgment slots (1,152 = 576
    core b0 + 576 budget smoke) plus capability-anchor cells (288) against
    ``decisions.launch_gates.canary_slot_inventory.six_judge_totals``.
    """
    protocol = load_protocol(DEFAULT_PROTOCOL_V2_PATH)
    root = (Path(project_root) if project_root is not None
            else DEFAULT_PROTOCOL_V2_PATH.resolve().parent.parent)
    main_question_ids, held_out_question_ids = load_reference_question_ids(protocol, root)

    debate_grid = _mapping(protocol["debate_grid"], "debate_grid")
    expected_main_n6 = int(_mapping(
        _mapping(debate_grid["slot_arithmetic_by_roster"], "slot_arithmetic_by_roster")
        ["total_judgment_slots"]["A_no_d"], "total_judgment_slots.A_no_d")["N6"])

    judges6 = candidate_roster_judges(protocol, 6)
    main_cells = enumerate_cells(protocol, judges6, main_question_ids)
    main_slot_count = summarize_cells(main_cells)["slot_count"]
    if main_slot_count != expected_main_n6:
        raise PlanValidationError(
            f"v2 main judgment-slot count at N=6 is {main_slot_count}, expected "
            f"{expected_main_n6} per debate_grid.slot_arithmetic_by_roster."
            "total_judgment_slots.A_no_d.N6")

    canary_cells = enumerate_canary_cells(protocol, judges6, held_out_question_ids)
    canary_summary = summarize_cells(canary_cells)
    fresh_judgment_slots = int(canary_summary["by_kind"].get(CANARY_JUDGMENT_KIND, 0))
    anchor_cells = int(canary_summary["by_kind"].get(CAPABILITY_ANCHOR_KIND, 0))
    combined = int(canary_summary["slot_count"])
    if fresh_judgment_slots != 1152 or anchor_cells != 288 or combined != 1440:
        raise PlanValidationError(
            f"v2 canary slots are fresh_judgment={fresh_judgment_slots}, anchor={anchor_cells}, "
            f"combined={combined}; expected 1152/288/1440 per decisions.launch_gates."
            "canary_slot_inventory.six_judge_totals")

    return {
        "main_slots_n6": main_slot_count, "canary_fresh_judgment_slots": fresh_judgment_slots,
        "canary_anchor_cells": anchor_cells, "canary_combined_slots": combined,
    }
