import pytest
from analysis import parse_sensitivity as ps, load
from analysis.load import DATA_DIR
from types import SimpleNamespace

real = pytest.mark.skipif(
    not (DATA_DIR / "judgments.jsonl").exists(), reason="pilot data not present")


def test_detector_fires_on_leaked_format():
    leaked = SimpleNamespace(reasoning="VERDICT: Position B\nCONFIDENCE: 1\nblah")
    clean = SimpleNamespace(reasoning="The honest debater cited exact figures.")
    assert ps.suspected_fallback(leaked) is True
    assert ps.suspected_fallback(clean) is False


@real
def test_treatments_return_five_and_survive():
    df = load.load_judgments_df()
    t = ps.delta_few_under_treatments(df, "70B")
    assert set(t) == {"baseline", "exclude", "suspect_wrong",
                      "suspect_correct", "suspect_5050"}
    # The effect should not collapse to ~0 under any treatment.
    assert min(t.values()) > 2.0
