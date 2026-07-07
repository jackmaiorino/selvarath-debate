"""Oracle-query composition: the pilot's buggy path (ported faithfully) and the clean path.

Pilot bug being reproduced/fixed: the judge is INSTRUCTED to phrase queries as
"Is it stated in the text that X?" while the oracle template wraps its input in
"Is it supported by the text that {query_claim}" and the strip only removes the
latter phrasing — so ~100% of pilot oracle queries were doubled. CLEAN passes a
bare claim exactly once.
"""
from __future__ import annotations

import re

PILOT_STRIP_PREFIXES = ("Is it supported by the text that ",
                        "is it supported by the text that ")

_SCAFFOLD = re.compile(r"(?i)^\s*is it (?:stated|supported)\s+(?:in|by)\s+the text that\s+")
_CLAIM = re.compile(r"(?is)^\s*claim\s*:\s*(.+)$")


def pilot_extract_claim(query_response: str) -> str:
    """Faithful port of judge.py:162-167 (incl. its ineffectiveness)."""
    claim = query_response.strip()
    for prefix in PILOT_STRIP_PREFIXES:
        if claim.startswith(prefix):
            claim = claim[len(prefix):]
            break
    return claim


def clean_extract_claim(query_response: str) -> tuple[str, bool]:
    """Extract a bare declarative claim. Returns (claim, well_formed).

    well_formed=True iff the judge followed the CLEAN instruction ("CLAIM: ...").
    Interrogative scaffolds are tolerated (stripped) but flagged well_formed=False.
    """
    s = query_response.strip()
    m = _CLAIM.match(s)
    if m:
        return m.group(1).strip().rstrip("?").strip(), True
    s = _SCAFFOLD.sub("", s).strip().rstrip("?").strip()
    return s, False


def compose_oracle_prompt(template: str, world_document: str, claim: str) -> str:
    return template.format(world_document=world_document, query_claim=claim)
