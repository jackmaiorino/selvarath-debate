from analysis.infra import parsing, design


# ---- parsing.parse_verdict_strict ----

def test_verdict_clean():
    r = parsing.parse_verdict_strict("VERDICT: Position A\nCONFIDENCE: 4\nREASONING: because x")
    assert r["verdict"] == "A" and r["confidence"] == 4 and r["parse_ok"] is True
    assert r["reasoning"] == "because x"


def test_verdict_b():
    r = parsing.parse_verdict_strict("VERDICT: Position B\nCONFIDENCE: 2")
    assert r["verdict"] == "B" and r["confidence"] == 2


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
