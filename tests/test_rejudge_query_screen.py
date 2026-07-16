from rejudge import query_screen


CORRECT = "The Compact was established in Year 31 after the coastal accord."
WRONG = "The Compact was established in Year 44 after the inland revolt."


def test_atomic_world_fact_is_allowed_and_record_is_json_ready():
    result = query_screen.screen_query(
        "The removal threshold is 24 votes.", CORRECT, WRONG)
    assert result.allowed is True
    assert result.reasons == ()
    assert result.as_record() == {"allowed": True, "reasons": []}


def test_answer_debater_position_and_debate_markers_are_rejected():
    for claim in (
        "Position A is correct.",
        "The answer is B.",
        "Did Debater B lie?",
        "The debate favors the first account.",
        "A is the right answer.",
    ):
        result = query_screen.screen_query(claim, CORRECT, WRONG)
        assert result.allowed is False
        assert query_screen.ANSWER_OR_DEBATE_REFERENCE in result.reasons


def test_candidate_restatement_uses_shared_overlap_rule():
    result = query_screen.screen_query(CORRECT, CORRECT, WRONG)
    assert result.allowed is False
    assert result.reasons == (query_screen.CANDIDATE_RESTATEMENT,)


def test_meta_and_evaluative_markers_are_rejected_but_stated_facts_are_allowed():
    for claim in (
        "The document implies that the threshold is 24 votes.",
        "The threshold is likely 24 votes.",
        "This reasoning is convincing.",
        "The claim is correct.",
    ):
        result = query_screen.screen_query(claim, CORRECT, WRONG)
        assert query_screen.META_OR_EVALUATIVE in result.reasons

    assert query_screen.screen_query(
        "The document states that the threshold is 24 votes.", CORRECT, WRONG
    ).allowed is True


def test_obvious_compound_claims_are_rejected():
    for claim in (
        "The Compact began in Year 31 and therefore the threshold is 24 votes.",
        "The Compact began in Year 31; the threshold is 24 votes.",
        "The Compact began in Year 31 and the threshold is 24 votes.",
        "The Compact began in Year 31. The threshold is 24 votes.",
    ):
        result = query_screen.screen_query(claim, CORRECT, WRONG)
        assert query_screen.COMPOUND_CLAIM in result.reasons


def test_conjunction_inside_one_fact_is_not_automatically_compound():
    for claim in (
        "The Trade and Security Council was founded in Year 31.",
        "The red and the blue factions signed the accord.",
        "The council was composed of the red and the blue factions.",
    ):
        result = query_screen.screen_query(claim, CORRECT, WRONG)
        assert query_screen.COMPOUND_CLAIM not in result.reasons
        assert result.allowed is True


def test_all_reasons_are_reported_in_stable_order():
    result = query_screen.screen_query(
        "Position A repeats that the Compact was established in Year 31 after the "
        "coastal accord, and therefore it is likely correct.",
        CORRECT,
        WRONG,
    )
    assert result.reasons == (
        query_screen.ANSWER_OR_DEBATE_REFERENCE,
        query_screen.CANDIDATE_RESTATEMENT,
        query_screen.META_OR_EVALUATIVE,
        query_screen.COMPOUND_CLAIM,
    )


def test_empty_query_is_rejected_explicitly():
    result = query_screen.screen_query("  ", CORRECT, WRONG)
    assert result.allowed is False
    assert result.reasons == (query_screen.EMPTY_QUERY,)
