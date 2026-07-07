from rejudge import oracle_channel as oc


def test_pilot_normalizer_na_to_no_bug():
    # "NOT ADDRESSED".startswith("NO") is True -> the pilot recorded NO (judge.py:189)
    assert oc.normalize_pilot("NOT ADDRESSED") == "NO"
    assert oc.normalize_pilot("YES") == "YES"
    assert oc.normalize_pilot("NO") == "NO"
    assert oc.normalize_pilot("The text is unclear.") == "NOT ADDRESSED"  # pilot else-branch


def test_strict_normalizer_preserves_na():
    assert oc.normalize_strict("NOT ADDRESSED") == "NOT ADDRESSED"
    assert oc.normalize_strict("The text is unclear.") == "INVALID"


def test_done_detectors():
    assert oc.is_done_pilot("DONE") is True
    assert oc.is_done_pilot("I'm done.") is False        # the pilot brittleness, kept
    assert oc.is_done_robust("DONE") is True
    assert oc.is_done_robust("done.") is True
    assert oc.is_done_robust("I'm done — no further queries.") is True
    assert oc.is_done_robust("No more queries needed") is True
    assert oc.is_done_robust("Is it stated that the work is done?") is False


def test_done_robust_rejects_mid_sentence_done():
    assert oc.is_done_robust("The treaty was done in Year 38") is False
    assert oc.is_done_robust("CLAIM: the war is done") is False
    assert oc.is_done_robust("") is False
