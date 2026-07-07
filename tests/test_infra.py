from analysis.infra import parsing, design


# ---- parsing.parse_verdict_strict ----

def test_verdict_clean():
    r = parsing.parse_verdict_strict("VERDICT: Position A\nCONFIDENCE: 4\nREASONING: because x")
    assert r["verdict"] == "A" and r["confidence"] == 4 and r["parse_ok"] is True
    assert r["reasoning"] == "because x"


def test_verdict_b():
    r = parsing.parse_verdict_strict("VERDICT: Position B\nCONFIDENCE: 2")
    assert r["verdict"] == "B" and r["confidence"] == 2


def test_verdict_trailing_punct_and_debater_vocab():
    # weaker models say "Debater A" / add punctuation; must still parse (audit finding)
    assert parsing.parse_verdict_strict("VERDICT: A.")["verdict"] == "A"
    assert parsing.parse_verdict_strict("VERDICT: Debater B\nCONFIDENCE: 3")["verdict"] == "B"


def test_confidence_ignores_stray_digits():
    # must not grab the '1' from '10%' (audit finding)
    r = parsing.parse_verdict_strict("VERDICT: Position A\nCONFIDENCE: I am 10% unsure, about 4\nREASONING: x")
    assert r["confidence"] != 1
    assert parsing.parse_verdict_strict("VERDICT: Position A\nCONFIDENCE: 4")["confidence"] == 4


def test_verdict_unparseable_does_not_default():
    # the whole point: no silent default-to-B
    r = parsing.parse_verdict_strict("I think the honest debater is right, but I'm unsure.")
    assert r["verdict"] is None and r["parse_ok"] is False


def test_verdict_empty():
    r = parsing.parse_verdict_strict("")
    assert r["verdict"] is None and r["parse_ok"] is False


# ---- parsing.normalize_oracle ----

def test_oracle_tokens():
    assert parsing.normalize_oracle("YES") == "YES"
    assert parsing.normalize_oracle("yes, the text supports it") == "YES"
    assert parsing.normalize_oracle("NO") == "NO"
    assert parsing.normalize_oracle("NOT ADDRESSED") == "NOT ADDRESSED"
    assert parsing.normalize_oracle("NOT_ADDRESSED") == "NOT ADDRESSED"


def test_oracle_no_false_match_on_nothing():
    # "NOTHING" must NOT be read as NO
    assert parsing.normalize_oracle("NOTHING in the text confirms this") != "NO"


def test_oracle_verbose_and_invalid():
    assert parsing.normalize_oracle("Based on my reading, NO.") == "NO"        # fallback standalone token
    assert parsing.normalize_oracle("The passage is about trade routes.") == "INVALID"
    assert parsing.normalize_oracle("") == "INVALID"


def test_verdict_negation_and_ambiguity_are_invalid():
    # review finding: hedged/negated lines mis-parsed with parse_ok=True
    assert parsing.parse_verdict_strict("VERDICT: Not Position A")["verdict"] is None
    assert parsing.parse_verdict_strict("VERDICT: Position A or Position B")["verdict"] is None
    assert parsing.parse_verdict_strict("VERDICT: Neither position")["verdict"] is None


def test_verdict_markdown_and_blockquote_leads():
    assert parsing.parse_verdict_strict("**VERDICT:** Position A")["verdict"] == "A"
    assert parsing.parse_verdict_strict("> VERDICT: B.")["verdict"] == "B"
    assert parsing.parse_verdict_strict("**VERDICT: Position B**")["verdict"] == "B"


def test_verdict_on_next_line():
    assert parsing.parse_verdict_strict("VERDICT:\nPosition A\nCONFIDENCE: 4")["verdict"] == "A"


def test_reasoning_multiline():
    r = parsing.parse_verdict_strict(
        "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: first line\nsecond line\n\nthird line")
    assert "second line" in r["reasoning"] and "third line" in r["reasoning"]


def test_confidence_markdown_bold():
    assert parsing.parse_verdict_strict("VERDICT: A\nCONFIDENCE: **4**")["confidence"] == 4


def test_oracle_no_evidence_is_invalid():
    # 'NO EVIDENCE' is closer to NOT ADDRESSED than to a contradicting NO
    assert parsing.normalize_oracle("NO EVIDENCE in the text supports this") == "INVALID"


def test_verdict_or_ambiguity_bare_letters():
    assert parsing.parse_verdict_strict("VERDICT: A or B")["verdict"] is None
    assert parsing.parse_verdict_strict("VERDICT: Position A or B")["verdict"] is None


def test_verdict_disagreement_is_invalid():
    assert parsing.parse_verdict_strict("VERDICT: I disagree with Position A")["verdict"] is None


def test_first_verdict_wins_over_quoted_later_one():
    r = parsing.parse_verdict_strict(
        "VERDICT: Position A\nCONFIDENCE: 4\nREASONING: earlier the debater said\nVERDICT: gibberish")
    assert r["verdict"] == "A"


# ---- design ----

def test_position_fixed_and_deterministic():
    a = design.position_a_is_correct("Q1", 0)
    b = design.position_a_is_correct("Q1", 0)
    assert a == b                                   # deterministic
    # no budget argument exists -> the label is inherently fixed across budgets
    assert "budget" not in design.position_a_is_correct.__code__.co_varnames


def test_build_grid_counts_and_filter():
    cells = design.build_grid(["llama-8b", "llama-70b"], ["llama-70b", "llama-405b"], [0, 2])
    assert len(cells) == 2 * 2 * 2
    filtered = design.build_grid(["llama-8b", "llama-405b"], ["llama-70b"], [0],
                                 require_debater_ge_judge=True)
    # judge llama-405b (405) vs debater llama-70b (70): debater < judge -> dropped
    assert all(c["judge"] != "llama-405b" for c in filtered)
    assert all(c["gap"] >= 0 for c in filtered)


def test_solo_correct():
    assert design.solo_correct("A", True) is True
    assert design.solo_correct("B", True) is False
    assert design.solo_correct("A", False) is False
    assert design.solo_correct("X", True) is None


def test_registry_has_three_families():
    fams = {m["family"] for m in design.MODEL_REGISTRY.values()}
    assert {"llama", "qwen", "gemma"} <= fams
