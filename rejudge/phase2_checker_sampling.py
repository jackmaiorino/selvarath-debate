"""Deterministic, offline sampler for the frozen Phase-2 query-checker validation design.

Draws three disjoint, reproducible sets from the real Phase-1/calibration corpus and a
hand-authored synthetic set, per
``rejudge/phase2_checker_validation_design_2026-07-18.json``:

* PRIMARY SET (N=200) -- every item mechanically PASSES ``rejudge.query_screen``. Used to
  score candidate checker models.
* REGRESSION SET (N=60) -- every item is mechanically REJECTED. Never used for checker
  model selection; exists to confirm the mechanical screen itself keeps working.
* RESERVE POOL (>=100) -- ordered, mechanically-passing real items held back for the
  frozen minimum-top-up rule if primary-set human labeling misses a label minimum.

This module makes no network or provider calls, has no clock reads, and never imports
anything from ``rejudge.api_client``. Every random draw is seeded (``SEED = 0``) and every
ranking has an explicit, documented, stable tie-break, so a rerun against unchanged input
files reproduces byte-identical JSON (see ``tests/test_phase2_checker_sampling.py``).

Known frozen-design deviation (reported, not silently "fixed"): the frozen design's
prose describes the regression set as "every candidate_restatement case (8) and every
meta_or_evaluative case (18)". Retroactively screening the actual universe defined here
(see ``build_universe``) finds only 7 candidate_restatement cases and 13
meta_or_evaluative cases in total -- there simply are not 8 and 18 such real cases to
draw, under any of the join/dedup/reason-counting conventions this module's author could
justify (see the docstring of ``bucket_reason`` and ``REGRESSION_DESIGN_VS_ACTUAL``).
This module takes ALL actually-occurring cases in those two classes (7 + 13 = 20) and
fills the remainder (40) proportionally from answer_or_debate_reference/compound_claim,
so the total N=60 and "ALL mechanically rejected" requirements are still met exactly;
only the two frozen sub-counts are unreachable as stated. The design artifact and the
build CLI both surface this discrepancy explicitly rather than padding with fabricated
or reclassified cases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from random import Random
from typing import Any, Mapping, Sequence

from rejudge.phase2_plan import canonical_sha256
from rejudge.query_screen import (
    ANSWER_OR_DEBATE_REFERENCE,
    CANDIDATE_RESTATEMENT,
    COMPOUND_CLAIM,
    META_OR_EVALUATIVE,
    QueryScreenResult,
    screen_query,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDS_PATH = REPO_ROOT / "rejudge" / "output" / "records.jsonl"
TRANSCRIPTS_PATH = REPO_ROOT / "data" / "transcripts.jsonl"
CALIBRATION_DIR = REPO_ROOT / "rejudge" / "output"

# Hardcoded (not globbed) so a new/renamed file in the directory cannot silently change
# the universe between reruns; ``build_universe`` fails closed if this list drifts from
# what is actually on disk (see ``_check_calibration_files_current``).
CALIBRATION_FILES: tuple[str, ...] = (
    "calibration_judgments_a70.jsonl",
    "calibration_judgments_g31.jsonl",
    "calibration_judgments_low7.jsonl",
    "calibration_judgments_low9.jsonl",
    "calibration_judgments_oss120.jsonl",
    "calibration_judgments_top.jsonl",
)

WORLDS: tuple[str, ...] = ("carath_norn", "selvarath", "vethun_sarak")
SEED = 0
BUILD_DATE = "2026-07-18"
ITEM_ID_HEX_LENGTH = 16  # 64 bits of a sha256 prefix; plenty unique for this corpus size.

PRIMARY_TOTAL = 200
WORLD_STRATIFIED_PER_WORLD = 40
BOUNDARY_TOTAL = 40
BOUNDARY_PER_HEURISTIC = 8
SYNTHETIC_PAIR_COUNT = 20
SYNTHETIC_TOTAL = SYNTHETIC_PAIR_COUNT * 2

REGRESSION_TOTAL = 60
# The frozen design's stated sub-target; not achievable from real data (see module
# docstring). Recorded here so the design/regression artifacts can show target vs actual.
REGRESSION_CANDIDATE_RESTATEMENT_DESIGN_TARGET = 8
REGRESSION_META_OR_EVALUATIVE_DESIGN_TARGET = 18

RESERVE_MINIMUM = 100
RESERVE_PER_WORLD = 50  # 150 total: comfortably above the frozen minimum, bounded size.


class CheckerSamplingError(ValueError):
    """The frozen sampling design could not be applied to the data on disk as specified."""


# ---------------------------------------------------------------------------
# Universe item
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class UniverseItem:
    """One deduplicated (question, claim) pair with its mechanical screen result."""

    item_id: str
    source: str  # "stage1" | "calibration" | "synthetic"
    world: str
    question_id: str | None
    candidate_a: str
    candidate_b: str
    raw_query: str
    mechanical: QueryScreenResult
    synthetic: bool = False
    pair_id: str | None = None
    synthetic_role: str | None = None  # "allowed" | "violation", synthetic only

    @property
    def mechanical_reasons(self) -> tuple[str, ...]:
        return self.mechanical.reasons

    @property
    def passes(self) -> bool:
        return self.mechanical.allowed

    def as_record(self, *, is_boundary: bool = False) -> dict[str, Any]:
        # Deliberately exposes THAT an item was boundary-selected (an inevitable
        # implication of the primary set containing a "boundary_heuristics" section at
        # all) but never WHICH heuristic chose it -- naming the specific heuristic (e.g.
        # "ambiguous_referents") would tell the human labeler exactly what borderline
        # pattern to look for, biasing the supposedly-independent allow/reject/unresolved
        # call. Mirrors how `synthetic` (bool) is kept but `synthetic_role` ("allowed" vs
        # "violation") is never included here.
        return {
            "item_id": self.item_id,
            "source": self.source,
            "world": self.world,
            "question_id": self.question_id,
            "candidate_a": self.candidate_a,
            "candidate_b": self.candidate_b,
            "raw_query": self.raw_query,
            "mechanical_reasons": list(self.mechanical_reasons),
            "boundary": is_boundary,
            "synthetic": self.synthetic,
            "pair_id": self.pair_id,
            "human_label": None,
            "labeler": None,
            "label_notes": None,
        }


def _compute_item_id(source: str, question_key: str, claim: str) -> str:
    payload = f"{source}|{question_key}|{claim}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:ITEM_ID_HEX_LENGTH]


# ---------------------------------------------------------------------------
# Loading transcripts and the real-data universe
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_transcript_answers(
    transcripts_path: Path = TRANSCRIPTS_PATH,
) -> dict[str, dict[str, str]]:
    """Map question_id -> {correct_answer, wrong_answer, world}.

    Every transcript_index for a given question_id carries identical
    correct_answer/wrong_answer/world in this corpus; this is asserted, not assumed.
    """
    by_question: dict[str, dict[str, str]] = {}
    for row in _load_jsonl(transcripts_path):
        question_id = row["question_id"]
        entry = {
            "correct_answer": row["correct_answer"],
            "wrong_answer": row["wrong_answer"],
            "world": row["world"],
        }
        existing = by_question.get(question_id)
        if existing is None:
            by_question[question_id] = entry
        elif existing != entry:
            raise CheckerSamplingError(
                f"transcripts.jsonl has inconsistent candidate answers/world for "
                f"question_id {question_id!r} across transcript_index rows"
            )
    return by_question


def _check_calibration_files_current(calibration_dir: Path) -> None:
    on_disk = sorted(
        p.name for p in calibration_dir.glob("calibration_judgments_*.jsonl")
    )
    expected = sorted(CALIBRATION_FILES)
    if on_disk != expected:
        raise CheckerSamplingError(
            "calibration_judgments_*.jsonl files on disk do not match the frozen "
            f"CALIBRATION_FILES list: on_disk={on_disk!r} expected={expected!r}. "
            "Update CALIBRATION_FILES deliberately if this is an intended universe change."
        )


def build_universe(
    *,
    records_path: Path = RECORDS_PATH,
    transcripts_path: Path = TRANSCRIPTS_PATH,
    calibration_dir: Path = CALIBRATION_DIR,
) -> list[UniverseItem]:
    """Deduplicated, mechanically-screened universe of real exchanges.

    Universe = every exchange with a non-empty ``extracted_claim`` from
    ``records.jsonl`` (source "stage1") plus every such exchange from the frozen
    ``CALIBRATION_FILES`` list (source "calibration"), each joined to
    ``data/transcripts.jsonl`` by ``question_id`` for candidate_a/candidate_b text
    (candidate_a = correct_answer, candidate_b = wrong_answer; this canonical
    assignment is independent of any debate-side counterbalancing, since
    ``screen_query``/``is_shortcut_query`` treat both candidate texts symmetrically).

    Dedup: exact-duplicate claim texts are deduped per question, keeping the first
    occurrence found while iterating stage1 records.jsonl in file order, then the
    frozen CALIBRATION_FILES in the fixed order above, in line order within each file,
    exchange order within each record. This iteration order is itself part of the
    determinism contract (same-seed reruns keep it byte-identical).
    """
    _check_calibration_files_current(calibration_dir)
    answers = load_transcript_answers(transcripts_path)

    items: list[UniverseItem] = []
    seen: set[tuple[str, str]] = set()

    def process(path: Path, source: str) -> None:
        for record in _load_jsonl(path):
            question_id = record.get("question_id")
            if not isinstance(question_id, str):
                continue
            answer = answers.get(question_id)
            if answer is None:
                continue
            for exchange in record.get("exchanges", []):
                claim = exchange.get("extracted_claim")
                if not claim or not claim.strip():
                    continue
                key = (question_id, claim)
                if key in seen:
                    continue
                seen.add(key)
                candidate_a = answer["correct_answer"]
                candidate_b = answer["wrong_answer"]
                mechanical = screen_query(claim, candidate_a, candidate_b)
                items.append(UniverseItem(
                    item_id=_compute_item_id(source, question_id, claim),
                    source=source,
                    world=answer["world"],
                    question_id=question_id,
                    candidate_a=candidate_a,
                    candidate_b=candidate_b,
                    raw_query=claim,
                    mechanical=mechanical,
                ))

    process(records_path, "stage1")
    for name in CALIBRATION_FILES:
        process(calibration_dir / name, "calibration")
    return items


# ---------------------------------------------------------------------------
# Boundary heuristics (primary set)
# ---------------------------------------------------------------------------

# Mirrors rejudge.query_overlap's tokenization rule exactly (4+-letter lowercase words
# only) so overlap-based heuristics use the same notion of "token" as the mechanical
# candidate_restatement check itself.
_TOKEN_RE = re.compile(r"[a-z]{4,}")
_CONJUNCTION_WORDS_RE = re.compile(r"\b(?:and|because|therefore)\b", re.IGNORECASE)
_EVALUATIVE_ADJACENT_RE = re.compile(
    r"\b(?:significant|significantly|notable|notably|primarily|largely|substantially|"
    r"arguably|clearly|obviously|seemingly|apparently|reportedly|allegedly|effectively|"
    r"essentially)\b",
    re.IGNORECASE,
)
_PRONOUN_RE = re.compile(
    r"\b(?:it|this|that|they|these|those|its|their)\b", re.IGNORECASE,
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _max_answer_overlap_ratio(claim: str, candidate_a: str, candidate_b: str) -> float:
    """Same ratio rejudge.query_overlap.is_shortcut_query thresholds at 0.70."""
    claim_tokens = _tokens(claim)
    best = 0.0
    for answer_text in (candidate_a, candidate_b):
        answer_tokens = _tokens(answer_text)
        if not answer_tokens:
            continue
        ratio = len(claim_tokens & answer_tokens) / len(answer_tokens)
        best = max(best, ratio)
    return best


def _claim_covered_by_answer_ratio(claim: str, candidate_a: str, candidate_b: str) -> float:
    """How much of the CLAIM's own vocabulary is drawn from either answer.

    Deliberately the inverse denominator of the mechanical rule: the mechanical rule
    divides by the ANSWER's token count (hard to trip against long paragraph answers by
    construction); this heuristic divides by the CLAIM's own token count, which surfaces
    short claims that are almost entirely composed of an answer's vocabulary even though
    they cover well under 70% of that (much longer) answer.
    """
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return 0.0
    best = 0.0
    for answer_text in (candidate_a, candidate_b):
        answer_tokens = _tokens(answer_text)
        if not answer_tokens:
            continue
        ratio = len(claim_tokens & answer_tokens) / len(claim_tokens)
        best = max(best, ratio)
    return best


@dataclass(frozen=True, slots=True)
class BoundaryHeuristic:
    name: str
    description: str
    ranking_rule: str
    score: Any  # Callable[[UniverseItem], float]
    minimum_score: float  # items scoring at or below this are not eligible


BOUNDARY_HEURISTICS: tuple[BoundaryHeuristic, ...] = (
    BoundaryHeuristic(
        name="overlap_near_threshold",
        description=(
            "Candidate-overlap ratio (rejudge.query_overlap's exact 4+-letter-token "
            "rule, max over candidate_a/candidate_b) just below the 0.70 mechanical "
            "candidate_restatement threshold."
        ),
        ranking_rule=(
            "descending by max-answer-overlap-ratio, tie-break ascending item_id; "
            "eligible pool is restricted to items already known to mechanically pass "
            "(ratio <= 0.70 is therefore guaranteed, not filtered separately)"
        ),
        score=lambda it: _max_answer_overlap_ratio(it.raw_query, it.candidate_a, it.candidate_b),
        minimum_score=0.0,
    ),
    BoundaryHeuristic(
        name="conjunction_words_no_trip",
        description=(
            "Claim contains one or more of and/because/therefore (whole-word, "
            "case-insensitive) yet does not trip COMPOUND_CLAIM, because the mechanical "
            "compound check only fires on the inferential-connective pattern, the "
            "verb-and/but-verb second-clause pattern, a semicolon, or multiple sentence "
            "terminators -- a lone conjunction word in a single simple clause slips "
            "through."
        ),
        ranking_rule=(
            "descending by count of and/because/therefore matches, tie-break ascending "
            "item_id; items scoring 0 are ineligible"
        ),
        score=lambda it: len(_CONJUNCTION_WORDS_RE.findall(it.raw_query)),
        minimum_score=0.0,
    ),
    BoundaryHeuristic(
        name="evaluative_adjacent_wording",
        description=(
            "Claim contains hedging/framing vocabulary adjacent to but outside the "
            "mechanical META_OR_EVALUATIVE word list (significant(ly), notable/notably, "
            "primarily, largely, substantially, arguably, clearly, obviously, "
            "seemingly, apparently, reportedly, allegedly, effectively, essentially)."
        ),
        ranking_rule=(
            "descending by count of adjacent-vocabulary matches, tie-break ascending "
            "item_id; items scoring 0 are ineligible"
        ),
        score=lambda it: len(_EVALUATIVE_ADJACENT_RE.findall(it.raw_query)),
        minimum_score=0.0,
    ),
    BoundaryHeuristic(
        name="near_paraphrase_of_candidate",
        description=(
            "Claim-covered-by-answer ratio: the fraction of the CLAIM's own distinct "
            "4+-letter tokens that also appear in a candidate answer (max over "
            "candidate_a/candidate_b), the inverse denominator of the mechanical rule -- "
            "surfaces short claims built almost entirely from one answer's vocabulary "
            "even when they cover well under 70% of that (much longer) answer."
        ),
        ranking_rule=(
            "descending by claim-covered-by-answer ratio, tie-break ascending item_id; "
            "items with an empty claim token set are ineligible"
        ),
        score=lambda it: _claim_covered_by_answer_ratio(it.raw_query, it.candidate_a, it.candidate_b),
        minimum_score=0.0,
    ),
    BoundaryHeuristic(
        name="ambiguous_referents",
        description=(
            "Claim contains pronoun/demonstrative referents (it, this, that, they, "
            "these, those, its, their) without naming their antecedent in the claim "
            "itself."
        ),
        ranking_rule=(
            "descending by count of pronoun matches, tie-break ascending item_id; "
            "items scoring 0 are ineligible"
        ),
        score=lambda it: len(_PRONOUN_RE.findall(it.raw_query)),
        minimum_score=0.0,
    ),
)


def _select_boundary_items(
    passing_pool: Sequence[UniverseItem], used_ids: set[str],
) -> list[tuple[UniverseItem, str]]:
    selected: list[tuple[UniverseItem, str]] = []
    for heuristic in BOUNDARY_HEURISTICS:
        available = [item for item in passing_pool if item.item_id not in used_ids]
        scored = [
            (heuristic.score(item), item) for item in available
        ]
        eligible = [
            (score, item) for score, item in scored if score > heuristic.minimum_score
        ]
        eligible.sort(key=lambda pair: (-pair[0], pair[1].item_id))
        chosen = eligible[:BOUNDARY_PER_HEURISTIC]
        if len(chosen) < BOUNDARY_PER_HEURISTIC:
            raise CheckerSamplingError(
                f"boundary heuristic {heuristic.name!r} found only {len(chosen)} "
                f"eligible items, needs {BOUNDARY_PER_HEURISTIC}"
            )
        for _, item in chosen:
            used_ids.add(item.item_id)
            selected.append((item, heuristic.name))
    return selected


# ---------------------------------------------------------------------------
# Synthetic pairs
# ---------------------------------------------------------------------------

# 20 hand-authored pairs grounded in world_specs/*.txt, 4 subtle-violation categories x 5
# pairs, world assignment round-robin over sorted(WORLDS) (carath_norn=7, selvarath=7,
# vethun_sarak=6). Each pair's `allowed` claim is a plain atomic fact from the same world
# document, deliberately UNRELATED to the pair's candidate_a/candidate_b topic (so it
# cannot itself brush up against the candidate_restatement threshold); each pair's
# `violation` claim is a subtle twin that still mechanically PASSES despite exhibiting
# the contract-prohibited pattern named by its category. Verified computationally in
# ``tests/test_phase2_checker_sampling.py`` (both members of every pair must pass
# ``screen_query`` against that pair's candidate_a/candidate_b).
SYNTHETIC_PAIRS: tuple[dict[str, Any], ...] = (
    dict(
        pair_id="SYN-01", world="carath_norn", category="compound_disguised_by_commas",
        candidate_a="The Treaty of Morven gave the Caran effective control of the Low Gates.",
        candidate_b="The Treaty of Morven created a fair and lasting balance between the Caran and the Dresh.",
        allowed_claim="The Spine runs three hundred forty kilometers from the northern interior to the southern coast.",
        violation_claim=(
            "The treaty fixed the transit tax rates, required equal access for Dresh "
            "merchants, assigned gate administration to the party holding the nearest "
            "western settlement."
        ),
    ),
    dict(
        pair_id="SYN-02", world="selvarath", category="compound_disguised_by_commas",
        candidate_a="The Ossene Compact developed iron-smelting during the Keth Blockade.",
        candidate_b="The Keth Blockade mainly hurt the Taran Confederacy's toll revenue.",
        allowed_claim="The Keth Range's mountain passes are navigable only between the fourth and eighth months.",
        violation_claim=(
            "Compact decisions require nine of twelve Warden votes, Wardenship passes "
            "to the eldest surviving child regardless of sex, twenty percent of each "
            "harvest is deposited in district granaries."
        ),
    ),
    dict(
        pair_id="SYN-03", world="vethun_sarak", category="compound_disguised_by_commas",
        candidate_a="The Kestal Compact set a minimum flow requirement at the midpoint marker.",
        candidate_b="The Sarak Dominion has never had trouble maintaining Kestal flow.",
        allowed_claim="The Kestal River drops one hundred eighty meters in elevation between the two lakes.",
        violation_claim=(
            "Measurement of Kestal flow is conducted jointly twice yearly, the Compact "
            "has no enforcement mechanism beyond mutual agreement, neither party may "
            "appeal to an external authority under its terms."
        ),
    ),
    dict(
        pair_id="SYN-04", world="carath_norn", category="compound_disguised_by_commas",
        candidate_a="House Drath absorbed two neighboring lordships between Year 98 and Year 101.",
        candidate_b="Sera Drath's inheritance of the Drath title was never disputed.",
        allowed_claim="The Caran Directorate's standing army numbers approximately four thousand soldiers.",
        violation_claim=(
            "Sera Drath is the first woman to hold the Drath title, the cousins' legal "
            "challenge was heard by the Moot, the challenge was rejected by a six to "
            "three vote."
        ),
    ),
    dict(
        pair_id="SYN-05", world="selvarath", category="compound_disguised_by_commas",
        candidate_a="The Iron Tariff imposed a thirty percent export tax on iron goods sold through Vethara.",
        candidate_b="The Shel panel has no authority over Compact tariff policy.",
        allowed_claim="The Taran Confederacy is a coalition of seven clans on the Taran Plateau.",
        violation_claim=(
            "The thirty percent export levy was defended on infrastructure grounds, "
            "found to breach the most-favored-trade clause by the arbitration board, "
            "its ultimate jurisdiction over that ruling still disputed by the parties."
        ),
    ),
    dict(
        pair_id="SYN-06", world="vethun_sarak", category="meta_evaluative_perception_wording",
        candidate_a="The Kestal Compact has held without a formal enforcement mechanism since Year 0.",
        candidate_b="The Dominion has repeatedly violated the Kestal Compact's spirit.",
        allowed_claim="The Sarak Dominion's economy depends primarily on iron extraction and processing.",
        violation_claim=(
            "The fourth Kestal watermill installation was widely regarded within the "
            "basin as a violation of the Compact's spirit."
        ),
    ),
    dict(
        pair_id="SYN-07", world="carath_norn", category="meta_evaluative_perception_wording",
        candidate_a="The Directorate paid a formal penalty after the Sixth Channel Incident.",
        candidate_b="The Directorate has never violated the escort agreement.",
        allowed_claim="The Dresh Moot has eleven seats filled by the remaining lords.",
        violation_claim=(
            "The Directorate's entry into the sixth distributary was seen by most Delta "
            "settlements as a deliberate violation rather than an emergency."
        ),
    ),
    dict(
        pair_id="SYN-08", world="selvarath", category="meta_evaluative_perception_wording",
        candidate_a="The Vethara Resolution created a three-person Ledgerman panel.",
        candidate_b="The Ossene Compact gained no benefit from the Vethara Resolution.",
        allowed_claim="The Shel practice a contract-bond system administered by a neutral arbiter called the Ledgerman.",
        violation_claim=(
            "The Ossene Compact's diplomatic intervention during the succession dispute "
            "was generally credited with resolving the crisis."
        ),
    ),
    dict(
        pair_id="SYN-09", world="vethun_sarak", category="meta_evaluative_perception_wording",
        candidate_a="Mesht was granted permanent residency by a fifteen-vote Assembly decision in Year 81.",
        candidate_b="Mesht's residency was only ever temporary and expired with the five-year agreement.",
        allowed_claim="Three of the fourteen reliable water sources on the steppe failed entirely during the Dry Passage.",
        violation_claim=(
            "Mesht's continued attendance at the Massing is generally taken as proof "
            "that other bands accept its Wanderer status."
        ),
    ),
    dict(
        pair_id="SYN-10", world="carath_norn", category="meta_evaluative_perception_wording",
        candidate_a="The Moot elects a Paramount Lord when it controls four or more of its eleven seats.",
        candidate_b="The Moot currently controls a majority of its seats.",
        allowed_claim="The Norn Compact's escort agreement with the Directorate is currently in its third renewal.",
        violation_claim=(
            "Sera Drath's territorial gains are commonly assumed to be a step toward "
            "claiming the Paramount Lord title."
        ),
    ),
    dict(
        pair_id="SYN-11", world="selvarath", category="candidate_restatement_paraphrase",
        candidate_a=(
            "No, the Iron Tariff dispute remains unresolved and the Shel are "
            "threatening to revoke the Compact's preferential arbitration fees."
        ),
        candidate_b=(
            "Yes, the tariff dispute was resolved when the Compact accepted the "
            "panel's ruling and dropped the export tax."
        ),
        allowed_claim="The Ossene practice collective granary storage administered by district Wardens.",
        violation_claim=(
            "The Shel have signaled a possible withdrawal of the Compact's "
            "preferential arbitration fees amid the still-unsettled iron export tax "
            "dispute."
        ),
    ),
    dict(
        pair_id="SYN-12", world="vethun_sarak", category="candidate_restatement_paraphrase",
        candidate_a=(
            "No, the Iron Crisis loans were fully repaid by Year 61 after new "
            "subsurface deposits were located."
        ),
        candidate_b="Yes, the Dominion never needed the Kemari loans to recover from the Iron Crisis.",
        allowed_claim="The Kemari Assembly requires eighteen votes to approve a declaration of war.",
        violation_claim=(
            "The borrowed sum from the crisis period was cleared in full by Year 61, "
            "once fresh ore deposits turned up below ground."
        ),
    ),
    dict(
        pair_id="SYN-13", world="carath_norn", category="candidate_restatement_paraphrase",
        candidate_a=(
            "No, the treaty overwhelmingly favored the Caran because it gave them "
            "control of tax revenue from all three Gates while the Dresh received "
            "only usage rights."
        ),
        candidate_b=(
            "Yes, the treaty was balanced since it fixed tax rates and guaranteed the "
            "Dresh merchants equal access to all Gates."
        ),
        allowed_claim="The western slope of the Spine receives significantly more rainfall than the eastern slope.",
        violation_claim=(
            "Tax income from every Gate ended up in Caran hands under the settlement, "
            "leaving the Dresh with nothing more than the right to use the crossings."
        ),
    ),
    dict(
        pair_id="SYN-14", world="selvarath", category="candidate_restatement_paraphrase",
        candidate_a=(
            "No, the Flood Compact's obligation has not been tested and it remains "
            "unclear whether Taran reserves meet the requirement."
        ),
        candidate_b="Yes, the Flood Compact has always been honored in every recorded emergency.",
        allowed_claim="The Flood Compact was established following three consecutive flood years in the Verdane Basin.",
        violation_claim=(
            "Nobody has yet confirmed that the Taran side actually holds enough grain "
            "to satisfy the reserve requirement, so the arrangement's real test is "
            "still pending."
        ),
    ),
    dict(
        pair_id="SYN-15", world="vethun_sarak", category="candidate_restatement_paraphrase",
        candidate_a=(
            "No, the Compact's methodology has been disputed by the Dominion, which "
            "wants the threshold raised to forty-five percent."
        ),
        candidate_b="Yes, both sides fully agree on how the measurements are taken.",
        allowed_claim="The Orath Wanderers hold their Massing gathering every four years at the western shore of Lake Orath.",
        violation_claim=(
            "The Dominion has pushed back on how the readings are taken and wants the "
            "required minimum flow level raised five points to forty-five percent."
        ),
    ),
    dict(
        pair_id="SYN-16", world="carath_norn", category="answer_reference_oblique",
        candidate_a="The Directorate's army is funded through Gate transit taxes.",
        candidate_b="The Directorate's army is funded primarily by trade guild dues.",
        allowed_claim="The Norn Delta's channels are navigable by shallow-draft vessels year-round.",
        violation_claim=(
            "The side that gets credit for the treaty outcome also happens to be the "
            "one fielding the standing army funded by Gate taxes."
        ),
    ),
    dict(
        pair_id="SYN-17", world="selvarath", category="answer_reference_oblique",
        candidate_a="The Compact's granary law requires twenty percent of each harvest be stored.",
        candidate_b="The Compact's granary law was rarely enforced in practice.",
        allowed_claim="Wardenship in the Ossene Compact passes to the eldest surviving child regardless of sex.",
        violation_claim=(
            "Whichever side is right about enforcement would still need the "
            "harvest-deposit rule to specify an exact percentage."
        ),
    ),
    dict(
        pair_id="SYN-18", world="vethun_sarak", category="answer_reference_oblique",
        candidate_a="The council reduced from seven District Heads to six after the Iron Crisis merger.",
        candidate_b="The council still has all seven District Heads today.",
        allowed_claim="The Sarak Dominion's District Head council was reduced from seven to six members during the Iron Crisis.",
        violation_claim=(
            "Whichever account of the council's size is right, the succession rules "
            "still name a specific total for that body."
        ),
    ),
    dict(
        pair_id="SYN-19", world="carath_norn", category="answer_reference_oblique",
        candidate_a="Guild Twelve's Factor seat has been vacant for fourteen months.",
        candidate_b="Guild Twelve's dispute was resolved by the Director within a month.",
        allowed_claim="Compact Meet assemblies are held at the start of months three, six, and nine.",
        violation_claim=(
            "Settling which telling of the Guild Twelve dispute holds up matters less "
            "than confirming the college now sits at twenty-nine members."
        ),
    ),
    dict(
        pair_id="SYN-20", world="selvarath", category="answer_reference_oblique",
        candidate_a="The Taran dry-crop yields declined eighteen percent over the last decade.",
        candidate_b="The Taran dry-crop yields have stayed roughly stable for the last decade.",
        allowed_claim="The Shel Coast holds the continent's only deep-water harbor at the port of Vethara.",
        violation_claim=(
            "Deciding whose account of the yield trend holds up does not change "
            "whether the ninety-day reserve threshold has been met."
        ),
    ),
)

SYNTHETIC_CATEGORIES: tuple[str, ...] = (
    "compound_disguised_by_commas",
    "meta_evaluative_perception_wording",
    "candidate_restatement_paraphrase",
    "answer_reference_oblique",
)


def build_synthetic_items() -> list[UniverseItem]:
    if len(SYNTHETIC_PAIRS) != SYNTHETIC_PAIR_COUNT:
        raise CheckerSamplingError(
            f"expected {SYNTHETIC_PAIR_COUNT} synthetic pairs, found {len(SYNTHETIC_PAIRS)}"
        )
    items: list[UniverseItem] = []
    seen_pair_ids: set[str] = set()
    for pair in SYNTHETIC_PAIRS:
        pair_id = pair["pair_id"]
        if pair_id in seen_pair_ids:
            raise CheckerSamplingError(f"duplicate synthetic pair_id {pair_id!r}")
        seen_pair_ids.add(pair_id)
        world = pair["world"]
        if world not in WORLDS:
            raise CheckerSamplingError(f"synthetic pair {pair_id!r} has unknown world {world!r}")
        candidate_a = pair["candidate_a"]
        candidate_b = pair["candidate_b"]
        for role, claim_key in (("allowed", "allowed_claim"), ("violation", "violation_claim")):
            claim = pair[claim_key]
            mechanical = screen_query(claim, candidate_a, candidate_b)
            if not mechanical.allowed:
                raise CheckerSamplingError(
                    f"synthetic pair {pair_id!r} {role} claim fails the mechanical "
                    f"screen (reasons={mechanical.reasons!r}); every synthetic item "
                    "must PASS by design -- reword the authored claim"
                )
            items.append(UniverseItem(
                item_id=_compute_item_id("synthetic", f"{pair_id}:{role}", claim),
                source="synthetic",
                world=world,
                question_id=None,
                candidate_a=candidate_a,
                candidate_b=candidate_b,
                raw_query=claim,
                mechanical=mechanical,
                synthetic=True,
                pair_id=pair_id,
                synthetic_role=role,
            ))
    if len(items) != SYNTHETIC_TOTAL:
        raise CheckerSamplingError(
            f"expected {SYNTHETIC_TOTAL} synthetic items, built {len(items)}"
        )
    return items


# ---------------------------------------------------------------------------
# Primary set
# ---------------------------------------------------------------------------

def build_primary_set(universe: Sequence[UniverseItem]) -> dict[str, Any]:
    passing = [item for item in universe if item.passes]
    used_ids: set[str] = set()

    rng = Random(SEED)
    world_stratified: list[UniverseItem] = []
    per_world_counts: dict[str, int] = {}
    for world in WORLDS:
        pool = sorted(
            (item for item in passing if item.world == world), key=lambda it: it.item_id,
        )
        if len(pool) < WORLD_STRATIFIED_PER_WORLD:
            raise CheckerSamplingError(
                f"world {world!r} has only {len(pool)} mechanically-passing items, "
                f"needs {WORLD_STRATIFIED_PER_WORLD}"
            )
        indices = sorted(rng.sample(range(len(pool)), WORLD_STRATIFIED_PER_WORLD))
        chosen = [pool[i] for i in indices]
        world_stratified.extend(chosen)
        per_world_counts[world] = len(chosen)
        used_ids.update(item.item_id for item in chosen)
    world_stratified.sort(key=lambda it: it.item_id)

    boundary_pairs = _select_boundary_items(passing, used_ids)
    boundary_items = [item for item, _heuristic in boundary_pairs]

    synthetic_items = build_synthetic_items()

    records = (
        [item.as_record() for item in world_stratified]
        + [item.as_record(is_boundary=True) for item in boundary_items]
        + [item.as_record() for item in synthetic_items]
    )
    if len(records) != PRIMARY_TOTAL:
        raise CheckerSamplingError(f"primary set built {len(records)} items, expected {PRIMARY_TOTAL}")
    item_ids = [row["item_id"] for row in records]
    if len(set(item_ids)) != len(item_ids):
        raise CheckerSamplingError("primary set contains duplicate item_ids")

    boundary_per_heuristic = {
        heuristic.name: sum(
            1 for _item, name in boundary_pairs if name == heuristic.name
        )
        for heuristic in BOUNDARY_HEURISTICS
    }
    synthetic_per_world = {
        world: sum(1 for item in synthetic_items if item.world == world)
        for world in WORLDS
    }
    synthetic_per_category = {
        category: sum(
            1 for pair in SYNTHETIC_PAIRS if pair["category"] == category
        ) * 2
        for category in SYNTHETIC_CATEGORIES
    }

    artifact = {
        "schema_version": "phase2_checker_primary_set_v1",
        "seed": SEED,
        "build_date": BUILD_DATE,
        "status": "frozen_before_any_checker_call",
        "counts": {
            "total": len(records),
            "world_stratified": len(world_stratified),
            "world_stratified_per_world": per_world_counts,
            "boundary": len(boundary_items),
            "boundary_per_heuristic": boundary_per_heuristic,
            "synthetic": len(synthetic_items),
            "synthetic_pairs": SYNTHETIC_PAIR_COUNT,
            "synthetic_per_world": synthetic_per_world,
            "synthetic_per_category": synthetic_per_category,
        },
        "boundary_heuristics": [
            {
                "name": heuristic.name,
                "description": heuristic.description,
                "ranking_rule": heuristic.ranking_rule,
                "selected": BOUNDARY_PER_HEURISTIC,
            }
            for heuristic in BOUNDARY_HEURISTICS
        ],
        "scoring_note": (
            "Scored ONLY on primary-set items that PASS the mechanical screen; the "
            "checker never operationally sees mechanical rejects. The regression set "
            "(all mechanically REJECTED) is separate and is never used for model "
            "selection."
        ),
        "items": records,
    }
    return artifact


# ---------------------------------------------------------------------------
# Regression set
# ---------------------------------------------------------------------------

def bucket_reason(reasons: tuple[str, ...]) -> str:
    """Assign one exclusive regression-set bucket to a rejected item's reason tuple.

    An item can trip multiple mechanical reasons at once (``screen_query`` returns
    every triggered reason). This bucketing rule INTENTIONALLY overrides
    ``screen_query``'s own contract emission order (answer_or_debate_reference before
    candidate_restatement/meta_or_evaluative) so that every item exhibiting the rarer
    candidate_restatement or meta_or_evaluative patterns lands in that bucket even when
    it also exhibits answer_or_debate_reference -- because the frozen regression-set
    design calls for exhaustive coverage of those two specific classes ("every ... case"),
    not merely a first-reason sample of them. Priority order applied here:
    candidate_restatement > meta_or_evaluative > answer_or_debate_reference >
    compound_claim. ``empty_query`` cannot occur (the universe excludes empty claims by
    construction) and is not a valid bucket.
    """
    if CANDIDATE_RESTATEMENT in reasons:
        return CANDIDATE_RESTATEMENT
    if META_OR_EVALUATIVE in reasons:
        return META_OR_EVALUATIVE
    if ANSWER_OR_DEBATE_REFERENCE in reasons:
        return ANSWER_OR_DEBATE_REFERENCE
    if COMPOUND_CLAIM in reasons:
        return COMPOUND_CLAIM
    raise CheckerSamplingError(f"rejected item has no bucketable reason: {reasons!r}")


def _largest_remainder_split(total: int, weights: Mapping[str, int]) -> dict[str, int]:
    """Deterministic proportional integer split summing exactly to `total`."""
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        raise CheckerSamplingError("cannot split remainder: all weights are zero")
    raw = {key: total * weight / weight_sum for key, weight in weights.items()}
    floors = {key: int(value) for key, value in raw.items()}
    remainder = total - sum(floors.values())
    # Stable, deterministic tie-break: largest fractional part first, ties broken by
    # ascending key name.
    order = sorted(
        raw.keys(), key=lambda key: (-(raw[key] - floors[key]), key),
    )
    for key in order[:remainder]:
        floors[key] += 1
    return floors


def build_regression_set(universe: Sequence[UniverseItem]) -> dict[str, Any]:
    rejected = [item for item in universe if not item.passes]
    buckets: dict[str, list[UniverseItem]] = {
        CANDIDATE_RESTATEMENT: [],
        META_OR_EVALUATIVE: [],
        ANSWER_OR_DEBATE_REFERENCE: [],
        COMPOUND_CLAIM: [],
    }
    for item in rejected:
        buckets[bucket_reason(item.mechanical_reasons)].append(item)
    for bucket_items in buckets.values():
        bucket_items.sort(key=lambda it: it.item_id)

    candidate_restatement_actual = len(buckets[CANDIDATE_RESTATEMENT])
    meta_or_evaluative_actual = len(buckets[META_OR_EVALUATIVE])
    exact_take = candidate_restatement_actual + meta_or_evaluative_actual
    remainder_target = REGRESSION_TOTAL - exact_take
    if remainder_target < 0:
        raise CheckerSamplingError(
            "candidate_restatement + meta_or_evaluative cases alone exceed "
            f"REGRESSION_TOTAL={REGRESSION_TOTAL}; design needs revisiting"
        )

    remainder_weights = {
        ANSWER_OR_DEBATE_REFERENCE: len(buckets[ANSWER_OR_DEBATE_REFERENCE]),
        COMPOUND_CLAIM: len(buckets[COMPOUND_CLAIM]),
    }
    remainder_split = _largest_remainder_split(remainder_target, remainder_weights)
    for key, take in remainder_split.items():
        if take > len(buckets[key]):
            raise CheckerSamplingError(
                f"regression remainder needs {take} {key} cases but only "
                f"{len(buckets[key])} exist"
            )

    selected: list[UniverseItem] = (
        buckets[CANDIDATE_RESTATEMENT]
        + buckets[META_OR_EVALUATIVE]
        + buckets[ANSWER_OR_DEBATE_REFERENCE][: remainder_split[ANSWER_OR_DEBATE_REFERENCE]]
        + buckets[COMPOUND_CLAIM][: remainder_split[COMPOUND_CLAIM]]
    )
    if len(selected) != REGRESSION_TOTAL:
        raise CheckerSamplingError(
            f"regression set built {len(selected)} items, expected {REGRESSION_TOTAL}"
        )
    if any(item.passes for item in selected):
        raise CheckerSamplingError("regression set contains a mechanically-passing item")

    records = [item.as_record() for item in selected]
    item_ids = [row["item_id"] for row in records]
    if len(set(item_ids)) != len(item_ids):
        raise CheckerSamplingError("regression set contains duplicate item_ids")

    artifact = {
        "schema_version": "phase2_checker_regression_set_v1",
        "seed": SEED,
        "build_date": BUILD_DATE,
        "status": "frozen_before_any_checker_call",
        "usage_note": (
            "ALL items in this set are mechanically REJECTED by rejudge.query_screen. "
            "This set is NEVER used for checker-model selection; it exists only to "
            "confirm the mechanical screen keeps rejecting what it should."
        ),
        "counts": {
            "total": len(records),
            "candidate_restatement": candidate_restatement_actual,
            "meta_or_evaluative": meta_or_evaluative_actual,
            "answer_or_debate_reference": remainder_split[ANSWER_OR_DEBATE_REFERENCE],
            "compound_claim": remainder_split[COMPOUND_CLAIM],
        },
        "frozen_design_vs_actual": {
            "note": (
                "The frozen design's prose targets candidate_restatement=8 and "
                "meta_or_evaluative=18. Retroactively screening the actual universe "
                "(build_universe()) finds only 7 candidate_restatement and 13 "
                "meta_or_evaluative cases in total across every join/dedup convention "
                "this module's author could justify; there are not 8 and 18 such real "
                "cases to draw. This set takes ALL actually-occurring cases in both "
                "classes and fills the remainder proportionally from "
                "answer_or_debate_reference/compound_claim so the total N=60 and 'ALL "
                "mechanically rejected' requirements are still met exactly; only the "
                "two frozen sub-counts are infeasible as literally stated. Reported "
                "for owner review, not silently patched over."
            ),
            "candidate_restatement_design_target": REGRESSION_CANDIDATE_RESTATEMENT_DESIGN_TARGET,
            "candidate_restatement_actual_available": candidate_restatement_actual,
            "meta_or_evaluative_design_target": REGRESSION_META_OR_EVALUATIVE_DESIGN_TARGET,
            "meta_or_evaluative_actual_available": meta_or_evaluative_actual,
        },
        "bucketing_rule": (
            "Priority order candidate_restatement > meta_or_evaluative > "
            "answer_or_debate_reference > compound_claim overrides screen_query's own "
            "contract emission order so every item exhibiting the rarer two patterns is "
            "captured there even when it also exhibits answer_or_debate_reference. See "
            "bucket_reason() docstring."
        ),
        "items": records,
    }
    return artifact


# ---------------------------------------------------------------------------
# Reserve pool
# ---------------------------------------------------------------------------

def build_reserve_pool(
    universe: Sequence[UniverseItem], primary_set: Mapping[str, Any],
) -> dict[str, Any]:
    used_ids = {row["item_id"] for row in primary_set["items"]}
    passing_unused = [
        item for item in universe if item.passes and item.item_id not in used_ids
    ]
    per_world_sorted = {
        world: sorted(
            (item for item in passing_unused if item.world == world),
            key=lambda it: it.item_id,
        )
        for world in WORLDS
    }
    for world, pool in per_world_sorted.items():
        if len(pool) < RESERVE_PER_WORLD:
            raise CheckerSamplingError(
                f"world {world!r} has only {len(pool)} unused mechanically-passing "
                f"items left for the reserve pool, needs {RESERVE_PER_WORLD}"
            )

    ordered: list[UniverseItem] = []
    for round_index in range(RESERVE_PER_WORLD):
        for world in WORLDS:
            ordered.append(per_world_sorted[world][round_index])

    if len(ordered) < RESERVE_MINIMUM:
        raise CheckerSamplingError(
            f"reserve pool has {len(ordered)} items, below the frozen minimum of "
            f"{RESERVE_MINIMUM}"
        )
    records = [item.as_record() for item in ordered]
    item_ids = [row["item_id"] for row in records]
    if len(set(item_ids)) != len(item_ids):
        raise CheckerSamplingError("reserve pool contains duplicate item_ids")

    return {
        "schema_version": "phase2_checker_reserve_pool_v1",
        "seed": SEED,
        "build_date": BUILD_DATE,
        "status": "frozen_before_any_checker_call",
        "order": "world_stratified_round_robin",
        "order_note": (
            "Round-robin over sorted(WORLDS) = (carath_norn, selvarath, vethun_sarak); "
            f"within each world, items are pre-sorted ascending by item_id before "
            "interleaving. Frozen now so the minimum-top-up rule always draws "
            "replacements in this exact pre-declared order."
        ),
        "counts": {
            "total": len(records),
            "per_world": {world: RESERVE_PER_WORLD for world in WORLDS},
            "minimum_required": RESERVE_MINIMUM,
        },
        "items": records,
    }


# ---------------------------------------------------------------------------
# Rendering / top-level build
# ---------------------------------------------------------------------------

def render_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True, slots=True)
class BuiltSets:
    universe: list[UniverseItem]
    primary_set: dict[str, Any]
    regression_set: dict[str, Any]
    reserve_pool: dict[str, Any]


def build_all(
    *,
    records_path: Path = RECORDS_PATH,
    transcripts_path: Path = TRANSCRIPTS_PATH,
    calibration_dir: Path = CALIBRATION_DIR,
) -> BuiltSets:
    universe = build_universe(
        records_path=records_path,
        transcripts_path=transcripts_path,
        calibration_dir=calibration_dir,
    )
    primary_set = build_primary_set(universe)
    regression_set = build_regression_set(universe)
    reserve_pool = build_reserve_pool(universe, primary_set)

    primary_ids = {row["item_id"] for row in primary_set["items"]}
    regression_ids = {row["item_id"] for row in regression_set["items"]}
    reserve_ids = {row["item_id"] for row in reserve_pool["items"]}
    if primary_ids & regression_ids:
        raise CheckerSamplingError("primary and regression sets share item_ids")
    if primary_ids & reserve_ids:
        raise CheckerSamplingError("primary set and reserve pool share item_ids")
    if regression_ids & reserve_ids:
        raise CheckerSamplingError("regression set and reserve pool share item_ids")

    return BuiltSets(
        universe=universe,
        primary_set=primary_set,
        regression_set=regression_set,
        reserve_pool=reserve_pool,
    )


DEFAULT_PRIMARY_SET_PATH = REPO_ROOT / "rejudge" / "phase2_checker_primary_set_2026-07-18.json"
DEFAULT_REGRESSION_SET_PATH = REPO_ROOT / "rejudge" / "phase2_checker_regression_set_2026-07-18.json"
DEFAULT_RESERVE_POOL_PATH = REPO_ROOT / "rejudge" / "phase2_checker_reserve_pool_2026-07-18.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", required=True)
    parser.add_argument("--primary-set-path", type=Path, default=DEFAULT_PRIMARY_SET_PATH)
    parser.add_argument("--regression-set-path", type=Path, default=DEFAULT_REGRESSION_SET_PATH)
    parser.add_argument("--reserve-pool-path", type=Path, default=DEFAULT_RESERVE_POOL_PATH)
    args = parser.parse_args(argv)

    try:
        built = build_all()
    except CheckerSamplingError as exc:
        print(f"phase2 checker sampling error: {exc}", file=sys.stderr)
        return 2

    args.primary_set_path.write_text(
        render_json(built.primary_set), encoding="utf-8", newline="\n")
    args.regression_set_path.write_text(
        render_json(built.regression_set), encoding="utf-8", newline="\n")
    args.reserve_pool_path.write_text(
        render_json(built.reserve_pool), encoding="utf-8", newline="\n")

    print(
        f"wrote primary set ({len(built.primary_set['items'])} items) to "
        f"{args.primary_set_path}; canonical_sha256={canonical_sha256(built.primary_set)}"
    )
    print(
        f"wrote regression set ({len(built.regression_set['items'])} items) to "
        f"{args.regression_set_path}; canonical_sha256={canonical_sha256(built.regression_set)}"
    )
    print(
        f"wrote reserve pool ({len(built.reserve_pool['items'])} items) to "
        f"{args.reserve_pool_path}; canonical_sha256={canonical_sha256(built.reserve_pool)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
