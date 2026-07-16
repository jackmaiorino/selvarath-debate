"""Mechanical screening for the frozen Phase-2 oracle-query contract.

This module deliberately implements only checks that can be applied deterministically.
It does not call a model and is not yet wired into the live judge loop.  A caller can
log ``QueryScreenResult.reasons`` directly and separately add any later model-check
decision for less obvious compound or meta/evaluative claims.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from rejudge.query_overlap import is_shortcut_query


EMPTY_QUERY = "empty_query"
ANSWER_OR_DEBATE_REFERENCE = "answer_or_debate_reference"
CANDIDATE_RESTATEMENT = "candidate_restatement"
META_OR_EVALUATIVE = "meta_or_evaluative"
COMPOUND_CLAIM = "compound_claim"


@dataclass(frozen=True, slots=True)
class QueryScreenResult:
    """Serializable screening decision with stable, audit-friendly reason codes."""

    allowed: bool
    reasons: tuple[str, ...]

    def as_record(self) -> dict[str, object]:
        """Return a JSON-ready representation for a future runner's event log."""
        return {"allowed": self.allowed, "reasons": list(self.reasons)}


_ANSWER_OR_DEBATE_RE = re.compile(
    r"\b(?:debate|debaters?)\b"
    r"|\b(?:position|answer|candidate|debater)\s*[ab]\b"
    r"|\b[ab]\s+(?:is|was|seems?)\s+(?:correct|incorrect|right|wrong)\b"
    r"|\b(?:correct|incorrect|right|wrong|better|best)\s+"
    r"(?:answer|position|candidate|debater)\b"
    r"|\b(?:answer|position)\s+(?:is|was|seems?)\b",
    re.IGNORECASE,
)

_META_OR_EVALUATIVE_RE = re.compile(
    r"\b(?:argument|reasoning)\b"
    r"|\b(?:likely|unlikely|likelihood|probability|probable|improbable|"
    r"plausible|implausible|persuasive|convincing)\b"
    r"|\b(?:document|text|passage)\b.{0,40}\b"
    r"(?:implies?|suggests?|supports?|proves?|entails?|indicates?)\b"
    r"|\b(?:implied|suggested|supported|entailed|inferred)\s+by\s+"
    r"(?:the\s+)?(?:document|text|passage)\b"
    r"|\b(?:claim|assertion|proposition)\b.{0,30}\b"
    r"(?:true|false|correct|incorrect|valid|invalid)\b",
    re.IGNORECASE,
)

_INFERENTIAL_COMPOUND_RE = re.compile(
    r"\b(?:and\s+)?(?:therefore|thus|consequently|hence)\b|\bbecause\b",
    re.IGNORECASE,
)
_SECOND_CLAUSE_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|does|did|began|became|occurred|"
    r"happened|remained|remains)\b[^;.!?]*\b(?:and|but)\s+"
    r"(?:the|a|an|it|they|he|she|this|that|these|those)\b[^;.!?]*\b"
    r"(?:is|are|was|were|has|have|had|does|did|began|became|occurred|"
    r"happened|remained|remains)\b",
    re.IGNORECASE,
)


def _has_obvious_compound_claim(query: str) -> bool:
    """Detect only high-precision compound forms; ambiguous cases remain model-checkable."""
    stripped = query.strip()
    if ";" in stripped or _INFERENTIAL_COMPOUND_RE.search(stripped):
        return True
    if _SECOND_CLAUSE_RE.search(stripped):
        return True
    # More than one sentence/question is necessarily more than one atomic query.
    sentence_ends = re.findall(r"[.!?](?=\s|$)", stripped)
    return len(sentence_ends) > 1


def screen_query(query: str, correct_answer: str, wrong_answer: str) -> QueryScreenResult:
    """Apply the mechanical Phase-2 checks and return every triggered reason.

    Reasons are emitted in contract order, making records deterministic and allowing
    callers to distinguish label/meta violations from answer overlap and compounds.
    """
    query = query or ""
    reasons: list[str] = []

    if not query.strip():
        reasons.append(EMPTY_QUERY)
    if _ANSWER_OR_DEBATE_RE.search(query):
        reasons.append(ANSWER_OR_DEBATE_REFERENCE)
    if is_shortcut_query(query, correct_answer, wrong_answer):
        reasons.append(CANDIDATE_RESTATEMENT)
    if _META_OR_EVALUATIVE_RE.search(query):
        reasons.append(META_OR_EVALUATIVE)
    if _has_obvious_compound_claim(query):
        reasons.append(COMPOUND_CLAIM)

    return QueryScreenResult(allowed=not reasons, reasons=tuple(reasons))
