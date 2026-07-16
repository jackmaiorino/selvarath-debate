import json
from pathlib import Path

import pytest

from rejudge import composer

JUDGMENTS_PATH = Path("data/judgments.jsonl")
requires_pilot_corpus = pytest.mark.skipif(
    not JUDGMENTS_PATH.is_file(),
    reason="requires local pilot corpus data/judgments.jsonl (not included in clean clones)",
)

TEMPLATE = ("WORLD DOCUMENT:\n{world_document}\n\n"
            "QUERY: Is it supported by the text that {query_claim}\n\n"
            "Respond with exactly one of: YES, NO, or NOT ADDRESSED.")


def test_pilot_port_reproduces_doubling():
    # the judge follows its instruction: interrogative phrasing the strip does NOT handle
    q = "Is it stated in the text that the treaty was signed in Year 38?"
    claim = composer.pilot_extract_claim(q)
    assert claim == q                                    # strip never fires (the bug)
    prompt = composer.compose_oracle_prompt(TEMPLATE, "DOC", claim)
    assert "Is it supported by the text that Is it stated in the text that" in prompt


def test_pilot_port_strip_fires_on_its_own_phrase():
    q = "Is it supported by the text that the treaty was signed?"
    assert composer.pilot_extract_claim(q) == "the treaty was signed?"


def test_clean_extract_claim_prefixed():
    claim, ok = composer.clean_extract_claim("CLAIM: the treaty was signed in Year 38")
    assert claim == "the treaty was signed in Year 38" and ok is True


def test_clean_extract_tolerates_interrogative_scaffold():
    claim, ok = composer.clean_extract_claim("Is it stated in the text that the king died?")
    assert claim == "the king died" and ok is False


def test_clean_never_doubles():
    claim, _ = composer.clean_extract_claim("Is it supported by the text that the king died?")
    prompt = composer.compose_oracle_prompt(TEMPLATE, "DOC", claim)
    assert prompt.count("Is it supported by the text that") == 1


def test_clean_claim_with_embedded_scaffold_is_stripped_and_flagged():
    claim, ok = composer.clean_extract_claim("CLAIM: Is it stated in the text that the king died?")
    assert claim == "the king died" and ok is False
    prompt = composer.compose_oracle_prompt(TEMPLATE, "DOC", claim)
    assert prompt.count("Is it supported by the text that") == 1


def test_clean_claim_takes_first_line_only():
    claim, ok = composer.clean_extract_claim("CLAIM: the treaty was signed.\nNOTES: extra commentary")
    assert claim == "the treaty was signed." and ok is True


@requires_pilot_corpus
def test_pilot_port_matches_real_data_shape():
    # stored queries in data/judgments.jsonl are PRE-doubling claims; wrapping one that starts
    # with an interrogative must reproduce the garble the audit found
    rows = [json.loads(l) for l in JUDGMENTS_PATH.open(encoding="utf-8")]
    qs = [e["query"] for r in rows for e in r["queries_submitted"]]
    interrogative = [q for q in qs if q.lower().startswith("is it ")]
    assert interrogative, "expected interrogative stored queries in pilot data"
    p = composer.compose_oracle_prompt(TEMPLATE, "DOC", composer.pilot_extract_claim(interrogative[0]))
    assert "Is it supported by the text that Is it" in p
