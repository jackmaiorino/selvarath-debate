"""Oracle-reply normalization (pilot port + strict) and DONE detection."""
from __future__ import annotations

import re

from analysis.infra.parsing import normalize_oracle as _strict


def normalize_pilot(text: str) -> str:
    """Faithful port of judge.py:186-192 -- including NOT ADDRESSED -> NO."""
    t = text.strip().upper()
    if t.startswith("YES"):
        return "YES"
    if t.startswith("NO"):          # catches "NOT ADDRESSED" too: the bug
        return "NO"
    return "NOT ADDRESSED"


def normalize_strict(text: str) -> str:
    return _strict(text)


def is_done_pilot(text: str) -> bool:
    """Faithful port of judge.py:93-94."""
    return text.strip().upper() == "DONE"


_DONE_ROBUST = re.compile(
    r"(?i)\bdone\b|\bno\s+(?:more|further)\s+quer",
)


def is_done_robust(text: str) -> bool:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if first_line.rstrip().endswith("?"):
        return False
    return bool(_DONE_ROBUST.search(first_line))
