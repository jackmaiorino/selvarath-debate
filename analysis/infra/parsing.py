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


def _commit_side(v):
    """Return 'A'/'B' only when the text commits to exactly one side; else None."""
    v = v.strip().lstrip(_LEAD).strip()
    if re.match(r"(NOT|NEITHER|NO)\b", v):
        return None
    if re.search(r"\b(DISAGREE|REJECT)\b", v):
        return None
    a = bool(re.search(r"\b(?:POSITION|DEBATER)\s+A\b", v)) or bool(re.match(r"A\b", v))
    b = bool(re.search(r"\b(?:POSITION|DEBATER)\s+B\b", v)) or bool(re.match(r"B\b", v))
    if a and b:
        return None
    # "A or B" / "Position A or B": an explicit OR naming the other side is
    # ambiguous even when only one side matched the bare-letter/qualified checks
    # above (a bare `\bB\b`/`\bA\b` search would false-positive on the article "A").
    if a and re.search(r"\bOR\s+(?:POSITION\s+|DEBATER\s+)?B\b", v):
        return None
    if b and re.search(r"\bOR\s+(?:POSITION\s+|DEBATER\s+)?A\b", v):
        return None
    return "A" if a else ("B" if b else None)


def parse_verdict_strict(text):
    """Parse a judge verdict block. NO silent default.

    Returns dict:
      verdict: 'A' | 'B' | None   (None => unparseable/ambiguous/negated)
      confidence: int 1-5 | None
      reasoning: str (multi-line: runs until the next KEY: line)
      parse_ok: bool              (True iff a verdict side was found)
      raw: str
    """
    text = text or ""
    verdict = None
    confidence = None
    reasoning_lines = []
    capturing = False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip().lstrip(_LEAD).strip()
        up = s.upper()
        if up.startswith("VERDICT:"):
            capturing = False
            v = up[len("VERDICT:"):]
            if not v.strip().lstrip(_LEAD).strip() and i + 1 < len(lines):
                v = lines[i + 1].strip().lstrip(_LEAD).strip().upper()
            # First-wins: once a side is committed, a later "VERDICT:"-looking
            # line (e.g. REASONING text quoting the debater) must not overwrite
            # it. A later line may still set it if the first attempt was None.
            if verdict is None:
                verdict = _commit_side(v)
        elif up.startswith("CONFIDENCE:"):
            capturing = False
            m = re.match(r"[\s*_`]*([1-5])\b", s[len("CONFIDENCE:"):])
            if m:
                confidence = int(m.group(1))
        elif up.startswith("REASONING:"):
            capturing = True
            reasoning_lines.append(s[len("REASONING:"):].strip())
        elif capturing:
            reasoning_lines.append(line.strip())
    reasoning = "\n".join(reasoning_lines).strip()
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
    if re.match(r"NO\s+EVIDENCE\b", t):
        return "INVALID"
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
