"""The concurrency ramp: choosing gemma's cap by measurement rather than by assumption.

The canary measured gemma at concurrency ONE, where it abandoned 189 of 1,702 calls (11.1%)
while carrying 95% of all call time. Every wider setting is an extrapolation, so the bridge
canary walks up 1 -> 2 -> 4 -> 8, running real plan cells at each rung and promoting only on
measured evidence.

Ramp state lives in the archive rather than in memory, because the run does not execute in one
process: it pauses for each reviewer batch and resumes, so a rung routinely spans several
invocations.
"""
import json

import pytest

from rejudge.phase2_canary_live import (
    RampAborted, current_ramp_step, measure_rung, record_rung)

CHECKER = "google/gemma-4-31B-it"
RAMP = {
    "steps": [
        {"cells": 40, "model_caps": {CHECKER: 1}},
        {"cells": 40, "model_caps": {CHECKER: 2}},
        {"cells": 40, "model_caps": {CHECKER: 4}},
    ],
    "promotion_rule": "abandonment <= 15% and no terminal halt",
    "abort_rule": "pin the last passing rung",
}
SETTLED = {CHECKER: 8, "other/model": 8}


def _usage(tmp_path, events):
    path = tmp_path / "canary_usage.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for index, (status, model) in enumerate(events):
            handle.write(json.dumps(
                {"sequence": index, "status": status, "model": model}) + "\n")
    return path


def test_a_fresh_archive_starts_on_the_first_rung(tmp_path):
    step = current_ramp_step(RAMP, SETTLED, tmp_path)
    assert step.rung_index == 0
    assert step.model_caps[CHECKER] == 1
    assert step.cell_limit == 40


def test_a_passing_rung_promotes_to_the_next(tmp_path):
    record_rung(tmp_path, rung_index=0, model_caps={CHECKER: 1},
                measured={"abandoned": 4, "total": 100, "rate": 0.04, "terminal_halts": 0},
                promoted=True, ledger_sequence=100)
    step = current_ramp_step(RAMP, SETTLED, tmp_path)
    assert step.rung_index == 1
    assert step.model_caps[CHECKER] == 2


def test_the_ramp_settles_after_its_last_rung(tmp_path):
    for index, width in enumerate((1, 2, 4)):
        record_rung(tmp_path, rung_index=index, model_caps={CHECKER: width},
                    measured={"abandoned": 1, "total": 100, "rate": 0.01,
                              "terminal_halts": 0},
                    promoted=True, ledger_sequence=100 * (index + 1))
    step = current_ramp_step(RAMP, SETTLED, tmp_path)
    assert step.rung_index is None, "the ramp is done"
    assert step.model_caps == SETTLED
    assert step.cell_limit is None, "the rest of the run is not limited"


def test_a_failing_rung_pins_the_last_one_that_passed(tmp_path):
    record_rung(tmp_path, rung_index=0, model_caps={CHECKER: 1},
                measured={"abandoned": 4, "total": 100, "rate": 0.04, "terminal_halts": 0},
                promoted=True, ledger_sequence=100)
    record_rung(tmp_path, rung_index=1, model_caps={CHECKER: 2},
                measured={"abandoned": 40, "total": 100, "rate": 0.40, "terminal_halts": 0},
                promoted=False, ledger_sequence=200)
    step = current_ramp_step(RAMP, SETTLED, tmp_path)
    assert step.rung_index is None
    assert step.model_caps[CHECKER] == 1, "pinned to the last rung that passed"
    assert step.cell_limit is None


def test_failing_the_first_rung_stops_the_run_instead_of_ramping_down(tmp_path):
    """Concurrency 1 is what the canary already measured. Failing there is not a cap
    question: the provider is degraded relative to the canary, and there is no lower rung to
    fall back to, so continuing would just produce a slow bad run."""
    record_rung(tmp_path, rung_index=0, model_caps={CHECKER: 1},
                measured={"abandoned": 55, "total": 100, "rate": 0.55, "terminal_halts": 0},
                promoted=False, ledger_sequence=100)
    with pytest.raises(RampAborted):
        current_ramp_step(RAMP, SETTLED, tmp_path)


def test_a_rung_is_measured_only_over_its_own_ledger_window(tmp_path):
    """Earlier rungs' failures must not be charged to a later one, or a bad first rung would
    poison every measurement after it."""
    path = _usage(tmp_path, [("unknown_charge", CHECKER)] * 10
                  + [("success", CHECKER)] * 10)
    measured = measure_rung(path, since_sequence=10, checker_model=CHECKER)
    assert measured["total"] == 10
    assert measured["abandoned"] == 0
    assert measured["rate"] == 0.0


def test_only_the_checker_models_calls_count_toward_its_own_rate(tmp_path):
    path = _usage(tmp_path, [("unknown_charge", "other/model")] * 10
                  + [("success", CHECKER)] * 10)
    measured = measure_rung(path, since_sequence=0, checker_model=CHECKER)
    assert measured["total"] == 10 and measured["abandoned"] == 0
