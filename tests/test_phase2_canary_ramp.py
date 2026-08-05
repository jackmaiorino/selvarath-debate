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
    RAMP_MIN_CHECKER_CALLS, RampAborted, current_ramp_step, measure_rung, record_rung,
    record_rung_if_concluded, rung_verdict)

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


# --- what a rung may conclude ---------------------------------------------------------------
#
# Both of these were live failures on the first bridge-canary pass, at $0.48 of spend. The
# ramp judged rung 0 FAILED on a window containing zero checker calls (every cell so far was
# transcript generation, which never touches the checker model), and it counted a benign
# transient as a terminal halt. Together they would have aborted the ramp on ordinary provider
# noise before gemma made a single call.

def test_a_rung_with_too_few_checker_calls_stays_open(tmp_path):
    """A rung is a measurement. An empty window measures nothing, so it must not conclude
    anything: not a pass, and emphatically not a failure."""
    assert rung_verdict({"abandoned": 0, "total": 0, "rate": 0.0},
                        halted_reason=None) == "open"
    assert rung_verdict({"abandoned": 0, "total": 3, "rate": 0.0},
                        halted_reason=None) == "open"


def test_a_rung_with_enough_evidence_concludes(tmp_path):
    plenty = RAMP_MIN_CHECKER_CALLS
    assert rung_verdict({"abandoned": 2, "total": plenty, "rate": 2 / plenty},
                        halted_reason=None) == "pass"
    assert rung_verdict({"abandoned": plenty // 2, "total": plenty, "rate": 0.5},
                        halted_reason=None) == "fail"


def test_a_benign_transient_halt_does_not_fail_a_rung(tmp_path):
    """UnknownChargeHalt is the run's normal response to an ambiguous billing outcome, and the
    auto-resume policy exists precisely because it happens constantly. It says nothing about
    whether the checker tolerates this concurrency."""
    measured = {"abandoned": 1, "total": RAMP_MIN_CHECKER_CALLS, "rate": 0.02}
    assert rung_verdict(measured, halted_reason="UnknownChargeHalt") == "pass"


def test_a_frozen_checker_halt_does_fail_a_rung(tmp_path):
    """checker_malformed means the frozen gate is not behaving as frozen. That is exactly the
    evidence a rung exists to catch, so it fails regardless of the abandonment rate."""
    measured = {"abandoned": 0, "total": RAMP_MIN_CHECKER_CALLS, "rate": 0.0}
    assert rung_verdict(measured, halted_reason="checker_malformed") == "fail"
    assert rung_verdict(measured, halted_reason="checker_unresolved") == "fail"


def test_an_open_rung_is_not_recorded_so_the_next_pass_continues_it(tmp_path):
    """The run pauses and resumes constantly, so a rung routinely spans passes. An open rung
    must leave no verdict behind, or the next pass would read a conclusion nobody reached."""
    record_rung_if_concluded(tmp_path, rung_index=0, model_caps={CHECKER: 1},
                             measured={"abandoned": 0, "total": 0, "rate": 0.0},
                             halted_reason=None, ledger_sequence=471)
    assert not (tmp_path / "canary_ramp_state.jsonl").exists()
    step = current_ramp_step(RAMP, SETTLED, tmp_path)
    assert step.rung_index == 0, "still on rung 0, still measuring"


# --- owner override -------------------------------------------------------------------------

def test_an_owner_override_pins_the_caps_and_continues(tmp_path):
    """The abort rule stops the run and asks a human. An override is that human answering.

    It pins the width rather than reinterpreting the evidence: the rung still stands as
    FAILED on the record, and the run continues at a width already validated elsewhere.
    """
    record_rung(tmp_path, rung_index=0, model_caps={CHECKER: 1},
                measured={"abandoned": 16, "total": 28, "rate": 0.571},
                promoted=False, ledger_sequence=805)
    with pytest.raises(RampAborted):
        current_ramp_step(RAMP, SETTLED, tmp_path)

    step = current_ramp_step(RAMP, SETTLED, tmp_path,
                             override={"pinned_model_caps": {CHECKER: 1}})
    assert step.rung_index is None, "an override ends the ramp; it does not resume climbing"
    assert step.model_caps[CHECKER] == 1
    assert step.cell_limit is None


def test_an_override_cannot_be_used_to_climb_higher_than_it_pins(tmp_path):
    """An override is permission to continue at a stated width, not a blank cheque. Letting it
    name a width above the settled caps would turn 'proceed despite degradation' into
    'proceed faster because of it'."""
    record_rung(tmp_path, rung_index=0, model_caps={CHECKER: 1},
                measured={"abandoned": 16, "total": 28, "rate": 0.571},
                promoted=False, ledger_sequence=805)
    with pytest.raises(ValueError):
        current_ramp_step(RAMP, SETTLED, tmp_path,
                          override={"pinned_model_caps": {CHECKER: 99}})


def test_an_override_does_not_apply_to_a_ramp_that_never_failed(tmp_path):
    """It must not silently short-circuit a healthy ramp into its pinned width."""
    step = current_ramp_step(RAMP, SETTLED, tmp_path,
                             override={"pinned_model_caps": {CHECKER: 1}})
    assert step.rung_index == 0, "still measuring; nothing has failed yet"


# --- terminal-halt exclusions are per run ----------------------------------------------------

def test_terminal_halts_are_scoped_to_the_run_that_recorded_them(tmp_path, monkeypatch):
    """Exclusions are evidence about ONE run's cells, not a standing list.

    The bridge canary reruns the same 945 cell keys the July canary ran, so inheriting July's
    exclusions would silently skip four cells this run has never attempted, and skipping a
    cell is indistinguishable in the results from a cell that could not complete.
    """
    import json as _json

    from rejudge import phase2_canary_live as live

    root = tmp_path / "repo"
    (root / "rejudge").mkdir(parents=True)
    (root / "rejudge" / "halts_other.json").write_text(_json.dumps({
        "schema_version": "phase2_canary_terminal_halts_v1",
        "execution_identity_sha256": "a" * 64,
        "cells": [{"cell_key": "cell-from-another-run"}]}), encoding="utf-8")
    (root / "rejudge" / "halts_mine.json").write_text(_json.dumps({
        "schema_version": "phase2_canary_terminal_halts_v1",
        "execution_identity_sha256": "b" * 64,
        "cells": [{"cell_key": "cell-from-this-run"}]}), encoding="utf-8")

    excluded = live.load_terminal_halt_cells(
        root, tmp_path / "results.jsonl", execution_identity="b" * 64)
    assert excluded == frozenset({"cell-from-this-run"})


def test_an_identity_less_record_is_only_used_when_no_identity_is_given(tmp_path):
    """July's records predate identity scoping. They must keep working for that run and must
    not leak into a run that states which identity it is."""
    import json as _json

    from rejudge import phase2_canary_live as live

    root = tmp_path / "repo"
    (root / "rejudge").mkdir(parents=True)
    (root / "rejudge" / "halts_legacy.json").write_text(_json.dumps({
        "schema_version": "phase2_canary_terminal_halts_v1",
        "cells": [{"cell_key": "legacy-cell"}]}), encoding="utf-8")

    assert live.load_terminal_halt_cells(
        root, tmp_path / "r.jsonl", execution_identity=None) == frozenset({"legacy-cell"})
    assert live.load_terminal_halt_cells(
        root, tmp_path / "r.jsonl", execution_identity="b" * 64) == frozenset()
