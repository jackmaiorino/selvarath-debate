"""Validate the candidate Phase 2 prompt bundle against the frozen protocol.

This artifact is literal candidate wording only. It deliberately cannot freeze the
scientific wording, pass owner methods review, or authorize any provider call.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from string import Formatter
from typing import Any

from rejudge import phase2_plan
from rejudge.phase2_query_gate import CheckerDecision


DEFAULT_BUNDLE_PATH = Path(__file__).with_name("phase2_prompt_bundle.json")
DEFAULT_PROTOCOL_PATH = phase2_plan.DEFAULT_PROTOCOL_PATH
DEFAULT_EXPERIMENT_PROTOCOL_PATH = Path(__file__).resolve().parent.parent / "experiment_protocol.json"

SCHEMA_VERSION = "phase2_prompt_bundle_candidate_v1"
BUNDLE_ID = "phase2_pooled_hpr_2026_07_16_prompt_candidate_v1"
STATUS = "candidate_pending_owner_methods_review"
SCIENTIFIC_WORDING_DISPOSITION = (
    "candidate_literal_wording_not_frozen_or_authorized_owner_methods_review_required"
)
CONTINUITY_POLICY = "preserve_calibration_role_asymmetry_pending_owner_methods_review"
HONEST_SOFT_INSTRUCTION = "Keep each reply under 400 words."
LEGACY_REASON = (
    "The approved Phase 2 design drops the legacy bridge. These prompts are the pilot "
    "judge prompt family copied verbatim from experiment_protocol.json, retained only "
    "for provenance, and must never be included in an execution manifest."
)

# Literal payload/marker bindings (amendment F). Each mirrors a wording frozen elsewhere;
# any drift between the bundle and its source must fail closed rather than silently pass.
# Source: docs/oracle-query-contract.md, "Enforcement (phase 2 runner)" section.
REJECTION_PAYLOAD = "Query rejected: ask a single specific factual claim"
# Source: the diagnostic condition's own verdict-format template; frozen wording only.
EMPTY_EVIDENCE_MARKER = "[No factual claims were checked.]"
NO_DEBATE_LITERAL = "No debate transcript or substitute transcript is provided."

# Cap wording constants (amendment C). These mirror rejudge/debate_gen.py's
# CAP_INSTRUCTIONS / stronger_reminder / MAX_CAP_ATTEMPTS. They are hardcoded here,
# rather than imported, because debate_gen.py is not provider-free.
CAP_INSTRUCTION_TEMPLATE = "Your response must be {cap_words} words or fewer."
CAP_RETRY_REMINDER_TEMPLATE = (
    "REMINDER: your previous response exceeded the word limit. You MUST keep this "
    "response to {cap_words} words or fewer this time. Cut it down."
)
MAX_CAP_ATTEMPTS = 3

HONEST_DEBATER_TEMPLATES = ("debate_honest_blind_opening", "debate_honest_later_turn")
DISHONEST_DEBATER_TEMPLATES = (
    "debate_dishonest_blind_opening", "debate_dishonest_later_turn",
)
DEBATER_TEMPLATES = frozenset(HONEST_DEBATER_TEMPLATES + DISHONEST_DEBATER_TEMPLATES)

# Literal substrings that only the four debater templates may mention. "wrong_answer" is
# also a substring of "wrong_answer_defensibility"; both are checked explicitly so the
# failure is never masked by substring overlap.
TRUTH_LABELED_SUBSTRINGS = ("correct_answer", "wrong_answer", "wrong_answer_defensibility")


def _fold_for_truth_check(text: str) -> str:
    """Case-fold ``text`` and drop every non-alphanumeric character.

    Used to compare guarded truth-labelled tokens against template text so that casing
    ("CORRECT_ANSWER"), separator ("correct answer", "correct-answer"), and doubled-
    separator ("correct__answer") variants cannot smuggle a truth label past the literal
    substring check. Fail-closed: this only ever makes the check *more* likely to match,
    never less.
    """
    return "".join(ch for ch in text.casefold() if ch.isalnum())


_FOLDED_TRUTH_LABELED_SUBSTRINGS = tuple(
    _fold_for_truth_check(substring) for substring in TRUTH_LABELED_SUBSTRINGS
)

_STANDARD_KEYS = frozenset({"active", "system_prompt", "user_prompt_template"})
_QUERY_ONLY_KEYS = frozenset({"active", "user_prompt_template"})
_PAYLOAD_KEYS = frozenset({"active", "payload"})
_LEGACY_KEYS = frozenset({
    "active", "reason", "system_prompt", "query_phase_prompt", "verdict_prompt",
    "user_prompt_template",
})

TEMPLATE_KEY_SETS: dict[str, frozenset[str]] = {
    "debate_honest_blind_opening": _STANDARD_KEYS,
    "debate_dishonest_blind_opening": _STANDARD_KEYS,
    "debate_honest_later_turn": _STANDARD_KEYS,
    "debate_dishonest_later_turn": _STANDARD_KEYS,
    "sequential_judge_presentation": _STANDARD_KEYS,
    "sequential_judge_query": _QUERY_ONLY_KEYS,
    "sequential_judge_rejection": _PAYLOAD_KEYS,
    "sequential_judge_verdict": _QUERY_ONLY_KEYS,
    "query_checker": _STANDARD_KEYS,
    "oracle": _STANDARD_KEYS,
    "placebo": _PAYLOAD_KEYS,
    "batch_same_qa": _STANDARD_KEYS,
    "no_debate": _STANDARD_KEYS,
    "empty_evidence": _STANDARD_KEYS,
    "full_document": _STANDARD_KEYS,
    "capability_qa": _STANDARD_KEYS,
    "legacy": _LEGACY_KEYS,
}
EXPECTED_TEMPLATE_NAMES = frozenset(TEMPLATE_KEY_SETS)

# Fields that must contain zero placeholders (checked with _check_no_placeholders).
_NO_PLACEHOLDER_FIELDS = frozenset({"system_prompt", "payload"})
# Fields that must contain exactly their EXPECTED_PLACEHOLDERS[(template, field)] set,
# and are additionally sentinel-rendered (amendment E).
_PLACEHOLDER_CHECKED_FIELDS = frozenset(
    {"user_prompt_template", "query_phase_prompt", "verdict_prompt"})

EXPECTED_PLACEHOLDERS: dict[tuple[str, str], frozenset[str]] = {
    ("debate_honest_blind_opening", "user_prompt_template"): frozenset(
        {"world_document", "question", "correct_answer"}),
    ("debate_dishonest_blind_opening", "user_prompt_template"): frozenset(
        {"world_document", "question", "wrong_answer", "wrong_answer_defensibility"}),
    ("debate_honest_later_turn", "user_prompt_template"): frozenset(
        {"world_document", "question", "correct_answer", "wrong_answer", "debate_history"}),
    ("debate_dishonest_later_turn", "user_prompt_template"): frozenset({
        "world_document", "question", "wrong_answer", "correct_answer",
        "wrong_answer_defensibility", "debate_history",
    }),
    ("sequential_judge_presentation", "user_prompt_template"): frozenset(
        {"question", "position_a", "position_b", "debate_transcript"}),
    ("sequential_judge_query", "user_prompt_template"): frozenset(
        {"remaining_budget", "total_budget", "previous_queries"}),
    ("sequential_judge_verdict", "user_prompt_template"): frozenset({"query_results"}),
    ("query_checker", "user_prompt_template"): frozenset({"candidate_a", "candidate_b", "query"}),
    ("oracle", "user_prompt_template"): frozenset({"world_document", "query_claim"}),
    ("batch_same_qa", "user_prompt_template"): frozenset({
        "question", "position_a", "position_b", "debate_transcript", "verification_results",
    }),
    ("no_debate", "user_prompt_template"): frozenset({"question", "position_a", "position_b"}),
    ("empty_evidence", "user_prompt_template"): frozenset(
        {"question", "position_a", "position_b", "debate_transcript"}),
    ("full_document", "user_prompt_template"): frozenset(
        {"world_document", "question", "position_a", "position_b", "debate_transcript"}),
    ("capability_qa", "user_prompt_template"): frozenset(
        {"world_document", "question", "candidate_a", "candidate_b"}),
    ("legacy", "user_prompt_template"): frozenset(
        {"question", "position_a", "position_b", "debate_transcript"}),
    ("legacy", "query_phase_prompt"): frozenset(
        {"remaining_budget", "total_budget", "previous_queries"}),
    ("legacy", "verdict_prompt"): frozenset({"query_results"}),
}

# The historical judge fields that templates.legacy must reproduce byte-exactly from the
# tracked experiment_protocol.json "judge" block (amendment B literal binding).
LEGACY_HISTORICAL_FIELDS = ("system_prompt", "query_phase_prompt", "verdict_prompt",
                             "user_prompt_template")

# Fixed role -> template-name bindings shared by debate_grid, no_debate_references, and
# cap_protection_secondary condition_composition entries.
_FIXED_ROLE_TEMPLATES: dict[str, str] = {
    "query": "sequential_judge_query",
    "checker": "query_checker",
    "rejection": "sequential_judge_rejection",
    "verdict": "sequential_judge_verdict",
    "oracle": "oracle",
    "placebo": "placebo",
}

EXPECTED_OPENING_TURN: dict[str, str] = {
    "honest": "debate_honest_blind_opening", "dishonest": "debate_dishonest_blind_opening",
}
EXPECTED_LATER_TURNS: dict[str, str] = {
    "honest": "debate_honest_later_turn", "dishonest": "debate_dishonest_later_turn",
}

CONDITION_COMPOSITION_KEYS = frozenset({
    "status", "purpose", "transcript_generation", "debate_grid", "no_debate_references",
    "cap_protection_secondary", "diagnostics", "capability_measurement",
})


class PromptBundleError(ValueError):
    """The candidate prompt bundle is malformed or disagrees with the frozen protocol/policy."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PromptBundleError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise PromptBundleError(f"{label} must be an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: Iterable[str], label: str) -> None:
    if set(value) != set(expected):
        raise PromptBundleError(f"{label} fields drifted")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PromptBundleError(f"{label} must be a string")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` that fails closed on duplicate JSON object keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PromptBundleError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(token: str) -> Any:
    """``parse_constant`` hook that fails closed on NaN/Infinity/-Infinity literals."""
    raise PromptBundleError(f"JSON must not contain the non-finite literal: {token}")


def _load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PromptBundleError(f"could not read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PromptBundleError(f"{path} must contain a JSON object")
    return payload


def load_experiment_protocol_judge(
    path: str | Path = DEFAULT_EXPERIMENT_PROTOCOL_PATH,
) -> dict[str, Any]:
    """Load the tracked historical judge prompt family, failing closed."""
    payload = _load_json(path)
    return dict(_mapping(payload.get("judge"), f"{path} judge"))


def _extract_placeholders(text: str, label: str) -> set[str]:
    """Return the set of plain named placeholders in a format string, fail-closed.

    Rejects positional (``{}``, ``{0}``), attribute/index (``{obj.attr}``, ``{obj[0]}``),
    conversion (``{name!r}``), and format-spec (``{name:>10}``) placeholders outright, and
    surfaces any malformed format string (unbalanced braces) as a ``PromptBundleError``.
    """
    try:
        parsed = list(Formatter().parse(text))
    except ValueError as exc:
        raise PromptBundleError(f"{label} is a malformed format string: {exc}") from exc
    placeholders: set[str] = set()
    for _literal_text, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if conversion is not None:
            raise PromptBundleError(f"{label} uses a disallowed placeholder conversion")
        if format_spec:
            raise PromptBundleError(f"{label} uses a disallowed placeholder format spec")
        if field_name == "" or not field_name.isidentifier():
            raise PromptBundleError(
                f"{label} contains a positional, empty, attribute, or index placeholder")
        placeholders.add(field_name)
    return placeholders


def _check_no_placeholders(text: str, label: str) -> None:
    if _extract_placeholders(text, label):
        raise PromptBundleError(f"{label} must not contain any placeholders")


def _check_exact_placeholders(text: str, expected: frozenset[str], label: str) -> None:
    if _extract_placeholders(text, label) != expected:
        raise PromptBundleError(f"{label} placeholder set drifted")


def _render_with_sentinels(text: str, placeholders: frozenset[str], label: str) -> None:
    """Render ``text`` with a sentinel per placeholder (amendment E), fail-closed.

    Catches every rendering exception, requires each sentinel to survive rendering
    verbatim, and rejects any residual ``{``/``}`` left in the output -- none of the
    frozen templates use escaped (``{{``/``}}``) braces, so any that remain indicate an
    escaped-brace smuggling attempt that the exact-placeholder-set check alone would miss.
    """
    sentinel_mapping = {name: f"<<{name}>>" for name in placeholders}
    try:
        rendered = text.format(**sentinel_mapping)
    except Exception as exc:
        raise PromptBundleError(f"{label} failed sentinel rendering: {exc}") from exc
    missing = sorted(
        name for name, sentinel in sentinel_mapping.items() if sentinel not in rendered)
    if missing:
        raise PromptBundleError(f"{label} sentinel rendering dropped placeholders: {missing!r}")
    if "{" in rendered or "}" in rendered:
        raise PromptBundleError(f"{label} rendered output retains residual brace characters")


def _check_truth_neutral(text: str, label: str) -> None:
    folded = _fold_for_truth_check(text)
    for substring, folded_substring in zip(
        TRUTH_LABELED_SUBSTRINGS, _FOLDED_TRUTH_LABELED_SUBSTRINGS
    ):
        if folded_substring in folded:
            raise PromptBundleError(f"{label} leaks a truth-labelled placeholder: {substring}")


def _check_role_value(
    entry: Mapping[str, Any], role: str, expected: str,
    templates: Mapping[str, Any], label: str,
) -> None:
    """Require ``entry[role] == expected`` and that the referenced template is active."""
    value = entry.get(role)
    if value != expected:
        raise PromptBundleError(f"{label}.{role} must be {expected!r}")
    referenced = _mapping(templates.get(expected), f"templates.{expected}")
    if referenced.get("active") is not True:
        raise PromptBundleError(
            f"{label}.{role} references an inactive or unknown template: {expected!r}")


def _condition_role_set(
    condition: Mapping[str, Any], *, has_transcript_protocol: bool,
) -> frozenset[str]:
    """Derive the expected role-key set for one protocol condition (amendment C).

    Driven entirely by the condition's own ``query_budget``/``oracle_mode``/
    ``presentation`` attributes -- never by hardcoding which condition ids are which --
    so this enforces the frozen checker_scope ("all query-producing clean and placebo
    conditions") mechanically.
    """
    if condition.get("presentation") == "batch_same_qa":
        roles = {"judge"}
        if has_transcript_protocol:
            roles.add("transcript_protocol")
        return frozenset(roles)

    base = {"presentation", "verdict"}
    if has_transcript_protocol:
        base.add("transcript_protocol")

    query_budget = condition.get("query_budget")
    if query_budget == 0:
        return frozenset(base)

    oracle_mode = condition.get("oracle_mode")
    if oracle_mode == "clean":
        return frozenset(base | {"query", "checker", "rejection", "oracle"})
    if oracle_mode == "placebo":
        return frozenset(base | {"query", "checker", "rejection", "placebo"})

    raise PromptBundleError(
        f"protocol condition {condition.get('id')!r} has unrecognized "
        "query_budget/oracle_mode/presentation semantics; cannot derive expected roles"
    )


def _validate_condition_section(
    section_raw: Any, *, section_label: str, conditions: Sequence[Mapping[str, Any]],
    has_transcript_protocol: bool, transcript_protocol_name: str | None,
    presentation_value: str, templates: Mapping[str, Any],
) -> None:
    section = _mapping(section_raw, section_label)
    expected_ids = {str(condition.get("id")) for condition in conditions}
    _exact_keys(section, expected_ids, section_label)
    conditions_by_id = {str(condition.get("id")): condition for condition in conditions}

    for condition_id, entry_raw in section.items():
        label = f"{section_label}.{condition_id}"
        entry = _mapping(entry_raw, label)
        condition = conditions_by_id[condition_id]
        expected_roles = _condition_role_set(
            condition, has_transcript_protocol=has_transcript_protocol)
        _exact_keys(entry, expected_roles, label)

        if has_transcript_protocol:
            transcript_value = entry.get("transcript_protocol")
            if transcript_value != transcript_protocol_name:
                raise PromptBundleError(
                    f"{label}.transcript_protocol must be {transcript_protocol_name!r}")

        if "presentation" in expected_roles:
            _check_role_value(entry, "presentation", presentation_value, templates, label)
        if "judge" in expected_roles:
            _check_role_value(entry, "judge", "batch_same_qa", templates, label)
        for role, expected_template in _FIXED_ROLE_TEMPLATES.items():
            if role in expected_roles:
                _check_role_value(entry, role, expected_template, templates, label)


def _validate_transcript_protocol_entry(
    entry_raw: Any, name: str, *, expected_cap_words: int | None,
) -> None:
    label = f"condition_composition.transcript_generation.{name}"
    entry = _mapping(entry_raw, label)
    _exact_keys(entry, {"opening_turn", "later_turns", "word_cap"}, label)

    opening_turn = _mapping(entry.get("opening_turn"), f"{label}.opening_turn")
    if dict(opening_turn) != EXPECTED_OPENING_TURN:
        raise PromptBundleError(f"{label}.opening_turn drifted")
    later_turns = _mapping(entry.get("later_turns"), f"{label}.later_turns")
    if dict(later_turns) != EXPECTED_LATER_TURNS:
        raise PromptBundleError(f"{label}.later_turns drifted")

    word_cap = entry.get("word_cap")
    if expected_cap_words is None:
        if word_cap is not None:
            raise PromptBundleError(f"{label}.word_cap must be exactly null")
        return

    cap = _mapping(word_cap, f"{label}.word_cap")
    _exact_keys(
        cap, {"cap_words", "cap_instruction", "cap_retry_reminder", "max_cap_attempts"},
        f"{label}.word_cap",
    )
    cap_words_value = cap.get("cap_words")
    if (
        isinstance(cap_words_value, bool)
        or not isinstance(cap_words_value, int)
        or cap_words_value != expected_cap_words
    ):
        raise PromptBundleError(f"{label}.word_cap.cap_words disagrees with the frozen protocol")
    if cap.get("cap_instruction") != CAP_INSTRUCTION_TEMPLATE.format(cap_words=expected_cap_words):
        raise PromptBundleError(f"{label}.word_cap.cap_instruction wording drifted")
    if cap.get("cap_retry_reminder") != CAP_RETRY_REMINDER_TEMPLATE.format(
        cap_words=expected_cap_words
    ):
        raise PromptBundleError(f"{label}.word_cap.cap_retry_reminder wording drifted")
    max_cap_attempts_value = cap.get("max_cap_attempts")
    if (
        isinstance(max_cap_attempts_value, bool)
        or not isinstance(max_cap_attempts_value, int)
        or max_cap_attempts_value != MAX_CAP_ATTEMPTS
    ):
        raise PromptBundleError(f"{label}.word_cap.max_cap_attempts drifted")


def _validate_transcript_generation(
    composition: Mapping[str, Any], protocol: Mapping[str, Any],
) -> tuple[str, str]:
    debate_protocol = _mapping(protocol.get("debate_protocol"), "protocol debate_protocol")
    uncapped_name = debate_protocol.get("name")
    if not isinstance(uncapped_name, str) or not uncapped_name:
        raise PromptBundleError("frozen protocol debate_protocol.name must be a non-empty string")

    decisions = _mapping(protocol.get("decisions"), "protocol decisions")
    cap_decision = _mapping(
        decisions.get("cap_protection_secondary"),
        "protocol decisions.cap_protection_secondary",
    )
    capped_protocol = _mapping(
        cap_decision.get("capped_debate_protocol"),
        "protocol decisions.cap_protection_secondary.capped_debate_protocol",
    )
    capped_name = capped_protocol.get("name")
    if not isinstance(capped_name, str) or not capped_name:
        raise PromptBundleError(
            "frozen capped_debate_protocol.name must be a non-empty string")
    if capped_name == uncapped_name:
        raise PromptBundleError("capped and uncapped transcript protocol names must differ")

    cap_words = capped_protocol.get("maximum_words_per_debater_turn")
    if isinstance(cap_words, bool) or not isinstance(cap_words, int):
        raise PromptBundleError(
            "frozen capped_debate_protocol.maximum_words_per_debater_turn must be an int")

    transcript_generation = _mapping(
        composition.get("transcript_generation"), "condition_composition.transcript_generation")
    _exact_keys(
        transcript_generation, {uncapped_name, capped_name},
        "condition_composition.transcript_generation",
    )
    _validate_transcript_protocol_entry(
        transcript_generation[uncapped_name], uncapped_name, expected_cap_words=None)
    _validate_transcript_protocol_entry(
        transcript_generation[capped_name], capped_name, expected_cap_words=cap_words)
    return uncapped_name, capped_name


def _validate_diagnostics_section(
    section_raw: Any, protocol: Mapping[str, Any], uncapped_name: str,
    templates: Mapping[str, Any],
) -> None:
    decisions = _mapping(protocol.get("decisions"), "protocol decisions")
    scope = _mapping(
        decisions.get("design_scope_reconciliation"),
        "protocol decisions.design_scope_reconciliation",
    )
    empty = _mapping(
        scope.get("empty_evidence_table_control"),
        "protocol decisions.design_scope_reconciliation.empty_evidence_table_control",
    )
    full_document = _mapping(
        scope.get("full_document_ceiling_anchors"),
        "protocol decisions.design_scope_reconciliation.full_document_ceiling_anchors",
    )
    empty_condition_id = empty.get("condition_id")
    if not isinstance(empty_condition_id, str) or not empty_condition_id:
        raise PromptBundleError(
            "frozen design_scope_reconciliation.empty_evidence_table_control.condition_id "
            "must be a non-empty string"
        )
    full_document_condition_id = full_document.get("condition_id")
    if not isinstance(full_document_condition_id, str) or not full_document_condition_id:
        raise PromptBundleError(
            "frozen design_scope_reconciliation.full_document_ceiling_anchors.condition_id "
            "must be a non-empty string"
        )
    if empty_condition_id == full_document_condition_id:
        raise PromptBundleError(
            "frozen empty-evidence and full-document diagnostic condition_ids must differ")

    diagnostics_map = {
        empty_condition_id: "empty_evidence",
        full_document_condition_id: "full_document",
    }
    section = _mapping(section_raw, "condition_composition.diagnostics")
    _exact_keys(section, set(diagnostics_map), "condition_composition.diagnostics")
    for condition_id, judge_template in diagnostics_map.items():
        label = f"condition_composition.diagnostics.{condition_id}"
        entry = _mapping(section[condition_id], label)
        _exact_keys(entry, {"transcript_protocol", "judge"}, label)
        if entry.get("transcript_protocol") != uncapped_name:
            raise PromptBundleError(f"{label}.transcript_protocol must be {uncapped_name!r}")
        _check_role_value(entry, "judge", judge_template, templates, label)


def _validate_capability_measurement_section(
    section_raw: Any, protocol: Mapping[str, Any], templates: Mapping[str, Any],
) -> None:
    decisions = _mapping(protocol.get("decisions"), "protocol decisions")
    capability = _mapping(
        decisions.get("capability_measurement"), "protocol decisions.capability_measurement")
    condition_id = capability.get("condition_id")
    if not isinstance(condition_id, str) or not condition_id:
        raise PromptBundleError(
            "frozen decisions.capability_measurement.condition_id must be a non-empty string")

    section = _mapping(section_raw, "condition_composition.capability_measurement")
    _exact_keys(section, {condition_id}, "condition_composition.capability_measurement")
    label = f"condition_composition.capability_measurement.{condition_id}"
    entry = _mapping(section[condition_id], label)
    _exact_keys(entry, {"qa"}, label)
    _check_role_value(entry, "qa", "capability_qa", templates, label)


def _validate_condition_composition(
    bundle: Mapping[str, Any], protocol: Mapping[str, Any], templates: Mapping[str, Any],
) -> None:
    composition = _mapping(bundle.get("condition_composition"), "condition_composition")
    _exact_keys(composition, CONDITION_COMPOSITION_KEYS, "condition_composition")
    if composition.get("status") != STATUS:
        raise PromptBundleError("condition_composition status drifted")
    purpose = composition.get("purpose")
    if not isinstance(purpose, str) or not purpose:
        raise PromptBundleError("condition_composition purpose must be a non-empty string")

    uncapped_name, capped_name = _validate_transcript_generation(composition, protocol)

    decisions = _mapping(protocol.get("decisions"), "protocol decisions")

    debate_grid_raw = _mapping(protocol.get("debate_grid"), "protocol debate_grid")
    debate_conditions = [
        _mapping(condition, "protocol debate_grid condition")
        for condition in _list(debate_grid_raw.get("conditions"), "protocol debate_grid.conditions")
    ]
    _validate_condition_section(
        composition.get("debate_grid"),
        section_label="condition_composition.debate_grid",
        conditions=debate_conditions,
        has_transcript_protocol=True,
        transcript_protocol_name=uncapped_name,
        presentation_value="sequential_judge_presentation",
        templates=templates,
    )

    no_debate_raw = _mapping(
        protocol.get("no_debate_references"), "protocol no_debate_references")
    no_debate_conditions = [
        _mapping(condition, "protocol no_debate condition")
        for condition in _list(
            no_debate_raw.get("conditions"), "protocol no_debate_references.conditions")
    ]
    _validate_condition_section(
        composition.get("no_debate_references"),
        section_label="condition_composition.no_debate_references",
        conditions=no_debate_conditions,
        has_transcript_protocol=False,
        transcript_protocol_name=None,
        presentation_value="no_debate",
        templates=templates,
    )

    cap_decision = _mapping(
        decisions.get("cap_protection_secondary"), "protocol decisions.cap_protection_secondary")
    cap_condition_id = cap_decision.get("condition_id")
    if not isinstance(cap_condition_id, str) or not cap_condition_id:
        raise PromptBundleError(
            "frozen cap_protection_secondary.condition_id must be a non-empty string")
    _validate_condition_section(
        composition.get("cap_protection_secondary"),
        section_label="condition_composition.cap_protection_secondary",
        conditions=[{"id": cap_condition_id, "query_budget": cap_decision.get("query_budget")}],
        has_transcript_protocol=True,
        transcript_protocol_name=capped_name,
        presentation_value="sequential_judge_presentation",
        templates=templates,
    )

    _validate_diagnostics_section(
        composition.get("diagnostics"), protocol, uncapped_name, templates)

    _validate_capability_measurement_section(
        composition.get("capability_measurement"), protocol, templates)


def validate_bundle(
    bundle: Mapping[str, Any], protocol: Mapping[str, Any],
    historical_judge: Mapping[str, Any],
) -> None:
    """Validate the candidate prompt bundle, keeping wording and execution unauthorized."""
    bundle = _mapping(bundle, "bundle")
    protocol = _mapping(protocol, "protocol")
    historical_judge = _mapping(historical_judge, "historical judge mapping")
    _exact_keys(
        bundle,
        {
            "schema_version", "bundle_id", "protocol_id", "status", "execution_authorized",
            "scientific_wording_disposition", "continuity_policy", "condition_composition",
            "templates",
        },
        "bundle",
    )
    if bundle.get("schema_version") != SCHEMA_VERSION:
        raise PromptBundleError("unsupported prompt bundle schema_version")
    if bundle.get("bundle_id") != BUNDLE_ID:
        raise PromptBundleError("prompt bundle_id drifted")
    protocol_id = protocol.get("protocol_id")
    if not isinstance(protocol_id, str) or not protocol_id:
        raise PromptBundleError("frozen protocol_id must be a non-empty string")
    if bundle.get("protocol_id") != protocol_id:
        raise PromptBundleError("prompt bundle protocol_id disagrees with the frozen protocol")
    if bundle.get("status") != STATUS:
        raise PromptBundleError("prompt bundle status drifted")
    if bundle.get("scientific_wording_disposition") != SCIENTIFIC_WORDING_DISPOSITION:
        raise PromptBundleError("scientific wording disposition drifted")
    if bundle.get("execution_authorized") is not False:
        raise PromptBundleError("execution_authorized must be exactly false")

    decisions = _mapping(protocol.get("decisions"), "protocol decisions")

    continuity = _mapping(bundle.get("continuity_policy"), "continuity_policy")
    _exact_keys(
        continuity,
        {
            "policy", "honest_debater_soft_instruction", "dishonest_debater_soft_instruction",
            "normalization_authorized",
        },
        "continuity_policy",
    )
    if continuity.get("policy") != CONTINUITY_POLICY:
        raise PromptBundleError("continuity policy drifted")
    if continuity.get("honest_debater_soft_instruction") != HONEST_SOFT_INSTRUCTION:
        raise PromptBundleError("honest debater soft instruction drifted")
    if continuity.get("dishonest_debater_soft_instruction") is not None:
        raise PromptBundleError("dishonest debater soft instruction must remain unset")
    if continuity.get("normalization_authorized") is not False:
        raise PromptBundleError("continuity normalization_authorized must be exactly false")

    templates = _mapping(bundle.get("templates"), "templates")
    if set(templates) != EXPECTED_TEMPLATE_NAMES:
        raise PromptBundleError("template name set drifted")

    for name, expected_keys in TEMPLATE_KEY_SETS.items():
        template = _mapping(templates[name], f"templates.{name}")
        _exact_keys(template, expected_keys, f"templates.{name}")

        if name == "legacy":
            if template.get("active") is not False:
                raise PromptBundleError("legacy template must remain inactive")
            if template.get("reason") != LEGACY_REASON:
                raise PromptBundleError("legacy retirement reason drifted")
        elif template.get("active") is not True:
            raise PromptBundleError(f"templates.{name}.active must be exactly true")

        for field in expected_keys:
            if field in ("active", "reason"):
                continue
            label = f"templates.{name}.{field}"
            value = _string(template.get(field), label)
            if field in _NO_PLACEHOLDER_FIELDS:
                _check_no_placeholders(value, label)
            else:
                expected_placeholders = EXPECTED_PLACEHOLDERS[(name, field)]
                _check_exact_placeholders(value, expected_placeholders, label)
                _render_with_sentinels(value, expected_placeholders, label)
            if name not in DEBATER_TEMPLATES:
                _check_truth_neutral(value, label)

        if name == "legacy":
            for field in LEGACY_HISTORICAL_FIELDS:
                if template.get(field) != historical_judge.get(field):
                    raise PromptBundleError(
                        f"templates.legacy.{field} disagrees byte-for-byte with the "
                        f"historical experiment_protocol.json judge.{field}"
                    )

    # Pre-registered continuity asymmetry: the honest soft instruction must appear
    # verbatim in both honest debater system prompts and in neither dishonest one. This
    # must never be silently normalized away.
    for name in HONEST_DEBATER_TEMPLATES:
        system_prompt = str(templates[name]["system_prompt"])
        if HONEST_SOFT_INSTRUCTION not in system_prompt:
            raise PromptBundleError(
                f"templates.{name}.system_prompt must contain the honest soft "
                "instruction verbatim"
            )
    for name in DISHONEST_DEBATER_TEMPLATES:
        system_prompt = str(templates[name]["system_prompt"])
        if HONEST_SOFT_INSTRUCTION in system_prompt:
            raise PromptBundleError(
                f"templates.{name}.system_prompt must not contain the honest soft instruction"
            )

    # This module hardcodes templates.legacy.active=False (and a fixed LEGACY_REASON)
    # because the frozen protocol currently drops the legacy bridge. That hardcoded
    # assumption must stay pinned to the protocol's own recorded decision on the same
    # question: if a future protocol amendment flips matched_legacy_bridge.included, this
    # must fail loudly instead of silently continuing to validate against a stale
    # assumption that no longer matches the frozen protocol.
    design_scope_reconciliation = _mapping(
        decisions.get("design_scope_reconciliation"),
        "protocol decisions.design_scope_reconciliation",
    )
    matched_legacy_bridge = _mapping(
        design_scope_reconciliation.get("matched_legacy_bridge"),
        "protocol decisions.design_scope_reconciliation.matched_legacy_bridge",
    )
    if matched_legacy_bridge.get("included") is not False:
        raise PromptBundleError(
            "protocol decisions.design_scope_reconciliation.matched_legacy_bridge.included "
            "no longer matches the hardcoded legacy-inactive assumption in "
            "phase2_prompt_bundle.py; this validator must be updated before the bundle can "
            "be revalidated"
        )

    execution_semantics = _mapping(
        decisions.get("execution_semantics"), "protocol decisions.execution_semantics")
    expected_placebo_payload = execution_semantics.get("placebo_payload")
    if not isinstance(expected_placebo_payload, str) or not expected_placebo_payload:
        raise PromptBundleError("frozen placebo payload must be a non-empty string")
    if templates["placebo"]["payload"] != expected_placebo_payload:
        raise PromptBundleError("placebo payload disagrees with the frozen protocol wording")

    # Amendment A: bind the checker's instructed wording to the only implemented parser
    # (rejudge.phase2_query_gate.CheckerDecision) so future divergence fails validation.
    checker_template = templates["query_checker"]
    expected_user_phrase = (
        "Respond with exactly one token: "
        f"{CheckerDecision.ALLOW.value}, {CheckerDecision.REJECT.value}, or "
        f"{CheckerDecision.UNRESOLVED.value}."
    )
    if expected_user_phrase not in str(checker_template["user_prompt_template"]):
        raise PromptBundleError(
            "templates.query_checker.user_prompt_template must contain the exact "
            "checker-token phrase bound to CheckerDecision"
        )
    expected_system_phrase = f"return {CheckerDecision.UNRESOLVED.value}."
    if expected_system_phrase not in str(checker_template["system_prompt"]):
        raise PromptBundleError(
            "templates.query_checker.system_prompt must contain the exact "
            "unresolved-token phrase bound to CheckerDecision"
        )

    # Amendment F: literal payload/marker bindings.
    if templates["sequential_judge_rejection"]["payload"] != REJECTION_PAYLOAD:
        raise PromptBundleError(
            "templates.sequential_judge_rejection.payload disagrees with the frozen "
            "oracle-query contract wording"
        )
    if EMPTY_EVIDENCE_MARKER not in str(templates["empty_evidence"]["user_prompt_template"]):
        raise PromptBundleError(
            "templates.empty_evidence.user_prompt_template must contain the frozen "
            "empty-evidence marker line"
        )
    if NO_DEBATE_LITERAL not in str(templates["no_debate"]["user_prompt_template"]):
        raise PromptBundleError(
            "templates.no_debate.user_prompt_template must contain the frozen "
            "no-debate literal line"
        )

    # Amendment C: the condition_composition map.
    _validate_condition_composition(bundle, protocol, templates)


def load_and_validate(
    bundle_path: str | Path = DEFAULT_BUNDLE_PATH,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    experiment_protocol_path: str | Path = DEFAULT_EXPERIMENT_PROTOCOL_PATH,
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = phase2_plan.load_protocol(protocol_path)
    bundle = _load_json(bundle_path)
    historical_judge = load_experiment_protocol_judge(experiment_protocol_path)
    validate_bundle(bundle, protocol, historical_judge)
    return bundle, protocol


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE_PATH))
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL_PATH))
    parser.add_argument("--experiment-protocol", default=str(DEFAULT_EXPERIMENT_PROTOCOL_PATH))
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("only --check is supported")
    bundle, _protocol = load_and_validate(args.bundle, args.protocol, args.experiment_protocol)
    print(
        "verified candidate Phase 2 prompt bundle; "
        f"templates={len(bundle['templates'])}; "
        f"canonical_sha256={phase2_plan.canonical_sha256(bundle)}; "
        "owner_methods_review=PENDING; execution_authorized=NO"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
