import json
from pathlib import Path

import pytest

from rejudge import calibrate


ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "rejudge" / "calibration_recovery_gemma_2026-07-15.json"
MODELS = ROOT / "rejudge" / "output" / "calibration_models.json"
TRANSCRIPTS = ROOT / "rejudge" / "output" / "calibration_transcripts.jsonl"
JUDGMENTS = ROOT / "rejudge" / "output" / "calibration_judgments_g31.jsonl"


def test_recovery_selection_is_exactly_eleven_unique_b0_keys():
    keys = json.loads(SELECTION.read_text(encoding="utf-8"))
    assert len(keys) == len(set(keys)) == 11
    assert all(isinstance(key, str) and "|0|" in key for key in keys)


@pytest.mark.skipif(
    not TRANSCRIPTS.is_file() or not JUDGMENTS.is_file(),
    reason="requires local calibration artifacts (not included in clean clones)",
)
def test_recovery_selection_matches_the_local_gemma_completeness_gap():
    models = calibrate.load_calibration_models(str(MODELS))
    transcripts = calibrate.load_transcripts(str(TRANSCRIPTS))
    judge = {"mid_gemma": models["judges"]["mid_gemma"]}
    l70 = calibrate.find_debater_model(models, "l70")
    expected = calibrate.enumerate_cells(
        calibrate.ALL_CELL_GROUPS, transcripts, judge, l70)
    done = calibrate.load_done_keys(JUDGMENTS)
    missing = {cell["cell_key"] for cell in expected if cell["cell_key"] not in done}

    assert set(json.loads(SELECTION.read_text(encoding="utf-8"))) == missing
