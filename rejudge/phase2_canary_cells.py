"""Resolve a frozen canary plan cell into the parameters needed to execute it.

:func:`rejudge.phase2_plan.enumerate_canary_cells` says *what* the 945 cells are and
deliberately says nothing about how to run one. This module closes that gap. It exists
because three of the joins between the plan and the execution stack are traps:

- the canary's transcript conditions (``canary_blind_uncapped_3_round``) are not valid
  ``debate_gen`` protocol names, and passing one straight through raises;
- ``debate_gen.generate_transcript`` computes its own cell key in a different scheme, so a
  record persisted under that key loses its link to the plan and the manifest;
- exactly one cell carries a symbolic judge model that must never be dispatched literally.

Everything the frozen protocol already states -- query budget, oracle mode -- is read from it
rather than re-derived here, so the protocol stays the single source of truth. Anything this
module cannot resolve is refused rather than defaulted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

TRANSCRIPT_KINDS = frozenset({"canary_debate_transcript", "canary_capped_debate_transcript"})

# The canary prefixes its transcript conditions; the prompt bundle does not. Strip the prefix
# to reach the bundle entry, then map to the protocol name debate_gen actually accepts.
CANARY_CONDITION_PREFIX = "canary_"
TRANSCRIPT_PROTOCOL_NAMES = {
    "blind_uncapped_3_round": "uncapped3",
    "blind_capped150_3_round": "capped3",
}

# Which section of the bundle's condition_composition map governs each cell kind.
_COMPOSITION_SECTION = {
    "canary_debate_transcript": "transcript_generation",
    "canary_capped_debate_transcript": "transcript_generation",
    "canary_debate_judgment": "debate_grid",
    "canary_no_debate_judgment": "no_debate_references",
    "canary_cap_protection_judgment": "cap_protection_secondary",
    "canary_empty_evidence_judgment": "diagnostics",
    "canary_full_document_judgment": "diagnostics",
}

# Which protocol section states the condition's query budget and oracle mode. The specials
# carry no such entry: they are budget-0 by construction.
_CONDITION_SECTION = {
    "canary_debate_judgment": ("debate_grid", "conditions"),
    "canary_no_debate_judgment": ("no_debate_references", "conditions"),
}

_ARM_BY_ORACLE_MODE = {"clean": "clean", "placebo": "placebo"}


class UnresolvableCell(ValueError):
    """Raised when a cell's kind or condition is not part of the frozen canary."""


class UnresolvedAnchor(ValueError):
    """Raised when the symbolic anchor cell is resolved without an approved judge model."""


@dataclass(frozen=True, slots=True)
class ResolvedCell:
    """One plan cell, plus everything needed to execute it."""

    cell_key: str
    kind: str
    condition: str
    question_id: str
    judge_model: str | None
    debater_model: str | None
    transcript_index: int | None
    replicate_index: int | None
    query_budget: int
    dependency_keys: tuple[str, ...]
    composition: Mapping[str, Any]
    transcript_protocol_name: str | None
    oracle_mode: str
    arm_name: str | None

    @property
    def is_transcript(self) -> bool:
        return self.kind in TRANSCRIPT_KINDS

    @property
    def produces_queries(self) -> bool:
        """True only for the four gated arms that actually generate oracle queries.

        replay_clean_qa is budget-2 but generates nothing: it replays the paired sequential
        judgment's exchanges, so it never reaches the gate.
        """
        return self.query_budget > 0 and self.oracle_mode in _ARM_BY_ORACLE_MODE


def _composition(cell_kind: str, condition: str, bundle: Mapping[str, Any]) -> Mapping[str, Any]:
    section_name = _COMPOSITION_SECTION.get(cell_kind)
    if section_name is None:
        raise UnresolvableCell(f"unknown canary cell kind: {cell_kind!r}")
    section = bundle["condition_composition"][section_name]
    key = (condition[len(CANARY_CONDITION_PREFIX):]
           if cell_kind in TRANSCRIPT_KINDS and condition.startswith(CANARY_CONDITION_PREFIX)
           else condition)
    if key not in section:
        raise UnresolvableCell(
            f"condition {condition!r} is not composed in bundle section {section_name!r}")
    return section[key]


def _budget_and_oracle_mode(cell: Mapping[str, Any],
                            protocol: Mapping[str, Any]) -> tuple[int, str]:
    kind, condition = cell["kind"], cell["condition"]
    if kind in TRANSCRIPT_KINDS:
        return 0, "none"
    location = _CONDITION_SECTION.get(kind)
    if location is None:
        # A special (cap protection, empty evidence, full document). The plan already fixes
        # its budget at 0; there is no condition entry to read an oracle mode from.
        return int(cell["query_budget"] or 0), "none"
    section, field = location
    for entry in protocol[section][field]:
        if str(entry["id"]) == condition:
            return int(entry["query_budget"]), str(entry["oracle_mode"])
    raise UnresolvableCell(
        f"condition {condition!r} is not declared in protocol {section}.{field}")


def resolve_cell(cell: Mapping[str, Any], protocol: Mapping[str, Any],
                 bundle: Mapping[str, Any], *,
                 anchor_judge_model: str | None = None) -> ResolvedCell:
    """Resolve one frozen plan cell. Refuses anything it cannot resolve exactly."""
    kind, condition = str(cell["kind"]), str(cell["condition"])
    composition = _composition(kind, condition, bundle)

    transcript_protocol_name = None
    if kind in TRANSCRIPT_KINDS:
        stripped = condition[len(CANARY_CONDITION_PREFIX):] if condition.startswith(
            CANARY_CONDITION_PREFIX) else condition
        if stripped not in TRANSCRIPT_PROTOCOL_NAMES:
            raise UnresolvableCell(
                f"transcript condition {condition!r} has no debate_gen protocol name")
        transcript_protocol_name = TRANSCRIPT_PROTOCOL_NAMES[stripped]

    judge_model = cell.get("judge_model")
    if isinstance(judge_model, str) and judge_model.startswith("selector:"):
        if not anchor_judge_model:
            raise UnresolvedAnchor(
                f"cell {cell['cell_key']} carries the symbolic judge model {judge_model!r}; "
                "it must never be dispatched literally. Supply the anchor approved in "
                "rejudge/phase2_anchor_parser_policy_approval_2026-07-24.json")
        judge_model = anchor_judge_model

    query_budget, oracle_mode = _budget_and_oracle_mode(cell, protocol)

    return ResolvedCell(
        cell_key=str(cell["cell_key"]), kind=kind, condition=condition,
        question_id=str(cell["question_id"]), judge_model=judge_model,
        debater_model=cell.get("debater_model"),
        transcript_index=cell.get("transcript_index"),
        replicate_index=cell.get("replicate_index"),
        query_budget=query_budget,
        dependency_keys=tuple(cell.get("dependency_keys") or ()),
        composition=composition, transcript_protocol_name=transcript_protocol_name,
        oracle_mode=oracle_mode, arm_name=_ARM_BY_ORACLE_MODE.get(oracle_mode))
