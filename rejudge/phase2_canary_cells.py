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

# Keyed on the UNPREFIXED kind. The canary plan prefixes every kind and transcript condition
# with "canary_" and the main plan does not, so keying on the prefixed names made all 23,200
# main cells unresolvable while the canary's 945 worked. Both plans describe the same cell
# shapes; the prefix is a namespace, not a difference in kind.
TRANSCRIPT_KINDS = frozenset({"debate_transcript", "capped_debate_transcript"})

# The canary prefixes its transcript conditions; the prompt bundle does not. Strip the prefix
# to reach the bundle entry, then map to the protocol name debate_gen actually accepts.
CANARY_CONDITION_PREFIX = "canary_"
TRANSCRIPT_PROTOCOL_NAMES = {
    "blind_uncapped_3_round": "uncapped3",
    "blind_capped150_3_round": "capped3",
}

# Which section of the bundle's condition_composition map governs each cell kind.
_COMPOSITION_SECTION = {
    "debate_transcript": "transcript_generation",
    "capped_debate_transcript": "transcript_generation",
    "debate_judgment": "debate_grid",
    "no_debate_judgment": "no_debate_references",
    "cap_protection_judgment": "cap_protection_secondary",
    "empty_evidence_judgment": "diagnostics",
    "full_document_judgment": "diagnostics",
}

# Which protocol section states the condition's query budget and oracle mode. The specials
# carry no such entry: they are budget-0 by construction.
_CONDITION_SECTION = {
    "debate_judgment": ("debate_grid", "conditions"),
    "no_debate_judgment": ("no_debate_references", "conditions"),
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
    # Additive (2026-08-21 mirroring fix, incident phase3_incident1_k2_mirroring_never_
    # implemented_2026-08-21): how many within-side judgment replicates one K2 side carries
    # for this cell's condition, i.e. ``debate_grid.conditions[*].
    # judgment_replicates_per_transcript_side`` -- the divisor ``phase2_canary_execute._polarity``
    # needs to unfold phase3_plan's ``replicate_index = side * replicates + within_side_replicate``
    # fold back into (side, within_side_replicate). ``None`` (the default, and every value this
    # module's own :func:`resolve_cell` ever supplies -- genuine phase-2 cells fold no side into
    # ``replicate_index`` at all) means "no side semantics": ``_polarity`` falls back to its
    # pre-fix behavior byte-for-byte. Only :mod:`rejudge.phase3_runner`'s judgment-cell resolver
    # (the one caller whose ``replicate_index`` actually IS such a fold) sets this to a real int.
    judgment_replicates_per_side: int | None = None

    @property
    def is_transcript(self) -> bool:
        return base_kind(self.kind) in TRANSCRIPT_KINDS

    @property
    def produces_queries(self) -> bool:
        """True only for the four gated arms that actually generate oracle queries.

        replay_clean_qa is budget-2 but generates nothing: it replays the paired sequential
        judgment's exchanges, so it never reaches the gate.
        """
        return self.query_budget > 0 and self.oracle_mode in _ARM_BY_ORACLE_MODE


def base_kind(cell_kind: str) -> str:
    """The cell shape, with the plan's namespace prefix removed.

    ``canary_debate_judgment`` and ``debate_judgment`` are the same shape executed over
    different question sets. Normalising here keeps one executor for both rather than a
    second copy that would drift.
    """
    return (cell_kind[len(CANARY_CONDITION_PREFIX):]
            if cell_kind.startswith(CANARY_CONDITION_PREFIX) else cell_kind)


def _composition(cell_kind: str, condition: str, bundle: Mapping[str, Any]) -> Mapping[str, Any]:
    section_name = _COMPOSITION_SECTION.get(base_kind(cell_kind))
    if section_name is None:
        raise UnresolvableCell(f"unknown cell kind: {cell_kind!r}")
    section = bundle["condition_composition"][section_name]
    key = (condition[len(CANARY_CONDITION_PREFIX):]
           if base_kind(cell_kind) in TRANSCRIPT_KINDS
           and condition.startswith(CANARY_CONDITION_PREFIX)
           else condition)
    if key not in section:
        raise UnresolvableCell(
            f"condition {condition!r} is not composed in bundle section {section_name!r}")
    return section[key]


def _budget_and_oracle_mode(cell: Mapping[str, Any],
                            protocol: Mapping[str, Any]) -> tuple[int, str]:
    kind, condition = base_kind(str(cell["kind"])), cell["condition"]
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
    """Resolve one frozen plan cell. Refuses anything it cannot resolve exactly.

    Every genuine phase-2 cell's ``replicate_index`` is a plain K2 index -- phase-2's plan
    enumerators never fold a side into it (only ``rejudge.phase3_plan``'s DOES, and phase 3
    resolves its own cells through ``rejudge.phase3_runner._resolve_judgment_cell`` instead of
    this function; see ``phase2_canary_execute._polarity``). So this leaves
    ``ResolvedCell.judgment_replicates_per_side`` at its dataclass default of ``None``,
    deliberately: passing anything else here would tell ``_polarity`` a side is foldable into a
    ``replicate_index`` that in fact never carries one.
    """
    kind, condition = str(cell["kind"]), str(cell["condition"])
    composition = _composition(kind, condition, bundle)

    transcript_protocol_name = None
    if base_kind(kind) in TRANSCRIPT_KINDS:
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
