from rejudge import parsers


def test_pilot_compat_default_to_b():
    r = parsers.parse_pilot_compat("I can't decide, both sound plausible.")
    assert r["verdict"] == "Position B"          # the pilot's silent default


def test_pilot_compat_first_digit_confidence_bug():
    r = parsers.parse_pilot_compat("VERDICT: Position A\nCONFIDENCE: 10% unsure, 4")
    assert r["confidence"] == 1                  # int(raw[0]) -> 1, faithfully wrong


def test_pilot_compat_fallback_scan():
    r = parsers.parse_pilot_compat("blah blah I lean Position A overall")
    assert r["verdict"] == "Position A"          # full-text scan fallback


def test_parse_both_carries_version_and_disagreement():
    out = parsers.parse_both("gibberish with no verdict")
    assert out["parser_version"] == parsers.PARSER_VERSION
    assert out["strict"]["verdict"] is None       # strict: INVALID
    assert out["pilot"]["verdict"] == "Position B" # pilot: coerced
