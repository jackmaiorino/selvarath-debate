"""Dual verdict parsing: hardened strict parser + a faithful port of the pilot parser.

The pilot parser (judge.py:_parse_verdict) is ported bug-for-bug — default-to-Position-B,
int(raw[0]) confidence, full-text fallback scan — because every arm parses every raw verdict
BOTH ways (measurement-side comparison at zero API cost).
"""
from __future__ import annotations

from analysis.infra.parsing import parse_verdict_strict

PARSER_VERSION = "2.0.0"


def parse_pilot_compat(text: str) -> dict:
    verdict = ""
    confidence = 1
    reasoning = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("VERDICT:"):
            verdict = stripped[len("VERDICT:"):].strip()
        elif stripped.upper().startswith("CONFIDENCE:"):
            raw = stripped[len("CONFIDENCE:"):].strip()
            try:
                confidence = max(1, min(5, int(raw[0])))
            except (ValueError, IndexError):
                confidence = 1
        elif stripped.upper().startswith("REASONING:"):
            reasoning = stripped[len("REASONING:"):].strip()
    verdict_upper = verdict.upper()
    if "POSITION A" in verdict_upper:
        verdict = "Position A"
    elif "POSITION B" in verdict_upper:
        verdict = "Position B"
    else:
        text_upper = text.upper()
        if "POSITION A" in text_upper:
            verdict = "Position A"
        elif "POSITION B" in text_upper:
            verdict = "Position B"
        else:
            verdict = "Position B"  # pilot behavior: default to B when unparseable
    if not reasoning:
        reasoning = text[:500]
    return {"verdict": verdict, "confidence": confidence, "reasoning": reasoning}


def parse_both(text: str) -> dict:
    return {"strict": parse_verdict_strict(text),
            "pilot": parse_pilot_compat(text),
            "parser_version": PARSER_VERSION}
