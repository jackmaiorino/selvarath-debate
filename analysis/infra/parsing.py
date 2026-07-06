"""Strict verdict parsing and robust oracle normalization.

Fixes two pilot-harness artifacts flagged in review:
1. The pilot silently defaulted an unparseable verdict to "Position B" — a
   budget-dependent measurement artifact. Here an unparseable verdict yields
   `verdict=None, parse_ok=False`; the caller records INVALID and EXCLUDES it (or
   analyzes it separately), never silently coercing a side.
2. The pilot normalized oracle replies with a bare `startswith`, so a verbose
   reply ("Based on the text, ...") silently became NOT ADDRESSED. Here a reply
   that does not clearly commit to YES/NO/NOT ADDRESSED yields INVALID.
"""
from __future__ import annotations

import re

_LEAD = " *_>#-\t\"'`"


def parse_verdict_strict(text):
    """Parse a judge verdict block. NO silent default.

    Returns dict:
      verdict: 'A' | 'B' | None   (None => unparseable)
      confidence: int 1-5 | None
      reasoning: str
      parse_ok: bool              (True iff a verdict side was found)
      raw: str
    """
    text = text or ""
    verdict = None
    confidence = None
    reasoning = ""
    for line in text.splitlines():
        s = line.strip()
        up = s.upper()
        if up.startswith("VERDICT:"):
            v = up[len("VERDICT:"):].strip()
            if "POSITION A" in v or v == "A":
                verdict = "A"
            elif "POSITION B" in v or v == "B":
                verdict = "B"
        elif up.startswith("CONFIDENCE:"):
            m = re.search(r"[1-5]", s[len("CONFIDENCE:"):])
            if m:
                confidence = int(m.group())
        elif up.startswith("REASONING:"):
            reasoning = s[len("REASONING:"):].strip()
    return {"verdict": verdict, "confidence": confidence, "reasoning": reasoning,
            "parse_ok": verdict is not None, "raw": text}


def normalize_oracle(text):
    """Map an oracle reply to 'YES' | 'NO' | 'NOT ADDRESSED' | 'INVALID'.

    Order matters: NOT ADDRESSED is checked before NO (since 'NOT...' startswith 'NO').
    A reply that does not clearly commit to one of the three tokens returns INVALID.
    """
    t = (text or "").strip().lstrip(_LEAD).strip().upper()
    # Leading committed token (word-boundary so "NOTHING" does NOT match "NO").
    if re.match(r"YES\b", t):
        return "YES"
    if re.match(r"NOT[ _]?ADDRESSED\b", t):
        return "NOT ADDRESSED"
    if re.match(r"NO\b", t):
        return "NO"
    # fallback: a clearly-committed token anywhere as a standalone word
    if re.search(r"\bNOT[ _]?ADDRESSED\b", t):
        return "NOT ADDRESSED"
    if re.search(r"\bYES\b", t):
        return "YES"
    if re.search(r"\bNO\b", t):
        return "NO"
    return "INVALID"
