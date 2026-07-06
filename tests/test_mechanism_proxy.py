import pytest
from analysis import mechanism_proxy as mp


def sig(ov="correct", qq="atomic", cr="decisive", relied=True):
    return {"oracle_validity": ov, "query_quality": qq, "claim_relevance": cr, "judge_relied": relied}


def test_derive_label_all_paths():
    assert mp.derive_label(sig(relied=False)) == "S1"                     # didn't rely -> stochastic
    assert mp.derive_label(sig(ov="incorrect")) == "O1"                    # wrong oracle answer
    assert mp.derive_label(sig(ov="ambiguous")) == "M1"                    # can't tell
    assert mp.derive_label(sig(qq="compound_or_malformed")) == "Q1"        # bad query, answer defensible
    assert mp.derive_label(sig(cr="irrelevant")) == "R1"                   # irrelevant true confirmation
    assert mp.derive_label(sig(cr="partial")) == "R2"                      # correct+relevant, over-penalized
    assert mp.derive_label(sig(cr="decisive")) == "R2"


def test_incorrect_oracle_beats_bad_query():
    # a wrong oracle answer is O1 even if the query was also compound
    assert mp.derive_label(sig(ov="incorrect", qq="compound_or_malformed")) == "O1"


def test_not_relied_short_circuits():
    # if the judge didn't rely on the oracle, it's S1 regardless of other signals
    assert mp.derive_label(sig(ov="incorrect", qq="compound_or_malformed", cr="irrelevant", relied=False)) == "S1"


def test_three_way_mapping():
    assert mp.to_three_way("O1") == "FM1"
    assert mp.to_three_way("R1") == "FM2"
    for c in ("Q1", "R2", "M1", "S1"):
        assert mp.to_three_way(c) == "other"


def test_bad_signal_raises():
    with pytest.raises(ValueError):
        mp.derive_label(sig(ov="nonsense"))
