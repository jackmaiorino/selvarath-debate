"""Oracle shortcut-query detector: the phase-2 oracle query contract's word-overlap
"candidate restatement" rule (`docs/oracle-query-contract.md`, "Prohibited" #2: "a claim
that substantially restates either candidate answer... high word overlap with a
candidate answer, the audit script's >70% rule").

Ported verbatim from the original (uncommitted, not in this repository or its git
history) shortcut-audit script that produced
`rejudge/output/oracle_shortcut_audit.json`. Two details matter and are easy to get
backwards:

1. Tokens are 4+-letter lowercase words only (`re.findall(r"[a-z]{4,}", s.lower())`) --
   no digits, no short function words, no apostrophes.
2. The coverage denominator is the ANSWER's distinct token set, not the claim's: a
   claim is a shortcut attempt when it covers more than 70% of a candidate answer's
   distinct tokens, not when most of the claim's own tokens happen to appear somewhere
   in the answer. Answers are full multi-sentence paragraphs, so covering >70% of one
   is hard by construction -- which is why the true rate on real data is tiny (3 of
   7,627 logged Stage-1 clean-arm queries; see
   `tests/test_rejudge_calibrate_analyze.py::test_shortcut_rate_matches_documented_benchmark`,
   which reproduces that exact count against `rejudge/output/records.jsonl`).

A prior reconstruction in this codebase got both details backwards (tokenized on
`[a-z0-9']+` and divided by the CLAIM's token count instead of the answer's), which
inflated the flagged rate by roughly 900x (2717/7627 vs. the documented 3/7627). This
module exists so the rule can never drift like that again: it is the single place the
rule is defined, and every caller imports it rather than re-deriving it.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z]{4,}")


def is_shortcut_query(claim: str, correct_answer: str, wrong_answer: str,
                      threshold: float = 0.7) -> bool:
    """True iff `claim` substantially restates either candidate answer: its distinct
    4+-letter tokens cover more than `threshold` of that answer's distinct 4+-letter
    tokens. An answer with zero qualifying tokens never triggers a match.
    """
    claim_tokens = set(_TOKEN_RE.findall((claim or "").lower()))
    for answer_text in (correct_answer, wrong_answer):
        answer_tokens = set(_TOKEN_RE.findall((answer_text or "").lower()))
        if answer_tokens and len(claim_tokens & answer_tokens) / len(answer_tokens) > threshold:
            return True
    return False
