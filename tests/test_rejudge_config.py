import random

from rejudge import config


def test_arms_table():
    assert set(config.ARMS) == {"clean", "both", "placebo", "na_only", "doubled_only", "legacy"}
    both = config.ARMS["both"]
    assert (both.oracle_normalizer, both.composer, both.done_detector) == ("pilot", "pilot", "pilot")
    na = config.ARMS["na_only"]
    assert (na.oracle_normalizer, na.composer) == ("pilot", "clean")
    dbl = config.ARMS["doubled_only"]
    assert (dbl.oracle_normalizer, dbl.composer) == ("strict", "pilot")
    assert config.ARMS["placebo"].placebo is True
    assert config.ARMS["legacy"].randomize_ab_per_budget is True
    assert config.ARMS["legacy"].parser_primary == "pilot"
    assert config.DEFAULT_BUDGETS["clean"] == [0, 1, 2, 5]
    assert config.DEFAULT_BUDGETS["both"] == [1, 2, 5]


def test_protocol_loads_real_file():
    p = config.load_protocol()
    assert "{query_claim}" in p["oracle"]["user_prompt_template"]
    assert "Is it stated in the text that" in p["judge"]["query_phase_prompt"]


def test_clean_query_prompt_rewrites_phrasing():
    p = config.load_protocol()
    out = config.clean_query_phase_prompt(p["judge"]["query_phase_prompt"])
    assert "Is it stated in the text that" not in out
    assert 'CLAIM: ' in out


def test_clean_query_prompt_raises_on_drift():
    try:
        config.clean_query_phase_prompt("some unrelated template")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_ab_fixed_across_budgets_for_nonlegacy():
    arm = config.ARMS["clean"]
    a1 = config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 1)
    a2 = config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 5)
    assert a1 == a2


def test_legacy_matches_pilot_randomization():
    arm = config.ARMS["legacy"]
    got = config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 2)
    seed = config.make_seed("CN-001", 1, config.JUDGE_MODEL, 2)
    assert got == random.Random(seed).choice([True, False])


def test_replicates_change_seed_but_not_position():
    arm = config.ARMS["clean"]
    s1 = config.judgment_seed("CN-001", 1, config.JUDGE_MODEL, 2, "clean", 0)
    s2 = config.judgment_seed("CN-001", 1, config.JUDGE_MODEL, 2, "clean", 1)
    assert s1 != s2
    assert (config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 2)
            == config.position_for(arm, "CN-001", 1, config.JUDGE_MODEL, 2))
