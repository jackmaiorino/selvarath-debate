import json

from rejudge.phase4b_labels import assess_response, consensus, messages_for, call_seed, human_rank

WORLD = "The town has a blue bridge. Its construction date is not recorded."


def response(**changes):
    return json.dumps({"label": "YES", "unambiguous": True,
                       "quotes": ["The town has a blue bridge."], "rationale": "Direct statement.",
                       "missing_information": "", **changes})


def test_only_blind_data_enters_prompt():
    prompt = messages_for(WORLD, "The bridge is blue.", "FROZEN LABEL CONTRACT")
    assert len(prompt) == 2
    assert json.loads(prompt[1]["content"]) == {"world_document": WORLD, "exact_claim": "The bridge is blue."}
    assert "FROZEN LABEL CONTRACT" in prompt[0]["content"]


def test_quoted_agreement_eligible_but_disagreement_is_not():
    yes = assess_response(response(), WORLD)
    no = assess_response(response(label="NO"), WORLD)
    assert yes["eligible"]
    assert consensus(yes, yes)["label"] == "YES"
    assert consensus(yes, no)["label"] == "AMBIGUOUS"


def test_invalid_quotes_and_malformed_outputs_excluded():
    for raw in (response(quotes=["The bridge is red."]), response(quotes=[]),
                response(quotes=[""]), response(quotes=[12]), response(rationale=""),
                response(unambiguous=1), response(label=[]), "```json\n" + response() + "\n```",
                "[]", "{}", '{"label":"NO","label":"YES"}', response(unambiguous=float("nan")), ""):
        assert not assess_response(raw, WORLD)["eligible"]


def test_absence_requires_relevant_quote_and_explicit_missing_fact():
    assert not assess_response(response(label="NOT ADDRESSED"), WORLD)["eligible"]
    assert assess_response(response(label="NOT ADDRESSED", missing_information="The opening year."), WORLD)["eligible"]
    assert not assess_response(response(missing_information="An unrelated missing detail."), WORLD)["eligible"]
    assert not assess_response(response(label="AMBIGUOUS", unambiguous=False), WORLD)["eligible"]


def test_deterministic_identity_based_seeds_and_human_selection():
    assert call_seed("claim", "judge1") == call_seed("claim", "judge1")
    assert call_seed("claim", "judge1") != call_seed("claim", "judge2")
    assert human_rank("world", "claim") == human_rank("world", "claim")
