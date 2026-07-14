import json
from pathlib import Path

import pytest

from rejudge import query_overlap

RECORDS_PATH = Path("rejudge/output/records.jsonl")
TRANSCRIPTS_PATH = Path("data/transcripts.jsonl")

real = pytest.mark.skipif(
    not (RECORDS_PATH.exists() and TRANSCRIPTS_PATH.exists()),
    reason="rejudge/output/records.jsonl and/or data/transcripts.jsonl not present")


# ---------------------------------------------------------------------------
# Rule semantics
# ---------------------------------------------------------------------------

def test_flags_claim_that_fully_restates_a_short_answer():
    correct = "No, it strongly favors the Caran."
    wrong = "Yes, the Spine is equally difficult to cross in both directions."
    assert query_overlap.is_shortcut_query(
        "the spine is equally difficult to cross in both directions", correct, wrong) is True


def test_does_not_flag_unrelated_claim():
    correct = "No, it strongly favors the Caran."
    wrong = "Yes, the Spine is equally difficult to cross in both directions."
    assert query_overlap.is_shortcut_query("the treaty concerns river access",
                                           correct, wrong) is False


def test_checks_either_candidate_answer():
    correct = "The removal threshold is 24 votes and was fixed in the founding charter."
    wrong = "The Guild controls appointments through an informal seniority convention only."
    # matches the WRONG answer specifically, not the correct one
    assert query_overlap.is_shortcut_query(
        "the guild controls appointments through an informal seniority convention",
        correct, wrong) is True


def test_denominator_is_the_answer_not_the_claim():
    # The bug this module fixes: a short claim whose tokens are a subset of a long,
    # multi-sentence answer covers 100% of the CLAIM's own tokens (the old, wrong
    # denominator) but only a small fraction of the ANSWER's distinct tokens (the
    # correct denominator) -- it must NOT be flagged, because restating one sentence
    # out of a paragraph is not "substantially restating" the answer.
    answer = ("The Compact was established in Year 31. It has governed trade relations "
             "between the three polities for over a century, surviving multiple "
             "succession crises and regional conflicts without major incident, and "
             "remains the primary legal basis for cross-border commerce today.")
    claim = "the compact was established in year 31"
    assert query_overlap.is_shortcut_query(claim, answer, "some unrelated wrong answer") is False


def test_answer_with_no_qualifying_tokens_never_matches():
    # An answer with zero 4+-letter tokens (e.g. "Yes.") has an empty denominator --
    # must not divide by zero, and must never register as a match.
    assert query_overlap.is_shortcut_query("yes it is true", "Yes.", "No.") is False


def test_threshold_boundary_is_strictly_greater_than():
    # answer has 10 distinct 4+-letter tokens; claim covers exactly 7 (70%, the boundary)
    answer = "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
    claim_70 = "alpha bravo charlie delta echo foxtrot golf"          # 7/10 = 0.70, not > 0.70
    claim_71 = "alpha bravo charlie delta echo foxtrot golf hotel"    # 8/10 = 0.80, > 0.70
    assert query_overlap.is_shortcut_query(claim_70, answer, "irrelevant") is False
    assert query_overlap.is_shortcut_query(claim_71, answer, "irrelevant") is True


def test_empty_claim_never_matches():
    assert query_overlap.is_shortcut_query("", "a reasonably long candidate answer here",
                                           "another reasonably long wrong answer here") is False


# ---------------------------------------------------------------------------
# Real-data regression: reproduces the documented Stage-1 shortcut-audit benchmark
# ---------------------------------------------------------------------------

@real
def test_shortcut_rate_matches_documented_benchmark():
    # docs/oracle-query-contract.md: "Stage-1 judges spontaneously asked candidate-level
    # queries in only 3 of 7,627 logged cases." Reproduces that exact count against the
    # Stage-1 clean-arm corpus (rejudge/output/records.jsonl, budget>0) joined to its
    # source transcripts (data/transcripts.jsonl).
    transcripts = {}
    for line in TRANSCRIPTS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        tr = json.loads(line)
        transcripts[(tr["question_id"], tr["transcript_index"])] = tr

    clean_exchanges = []
    for line in RECORDS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("arm") == "clean" and rec.get("budget", 0) > 0:
            clean_exchanges.append(rec)

    total = 0
    flagged = 0
    for rec in clean_exchanges:
        tr = transcripts.get((rec["question_id"], rec["transcript_index"]))
        if tr is None:
            continue
        for ex in rec["exchanges"]:
            claim = ex.get("extracted_claim") or ""
            total += 1
            if query_overlap.is_shortcut_query(claim, tr["correct_answer"], tr["wrong_answer"]):
                flagged += 1

    assert total == 7627
    assert flagged == 3
