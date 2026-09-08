"""Operational restart counters and concurrency changes must never reset used limits."""
from __future__ import annotations

import json

import pytest

from rejudge.phase3_main_recovery_driver import (
    ProviderConcurrencyTuner, RecoveryDriverError, restore_driver_state,
)
from rejudge.phase3_main_runner import MainRunPaths
from rejudge.phase3_v3_live import RunLease
from test_phase3_main_reviewer_commit import (
    MANIFEST_SHA, RUN_ID, _commit, _prepare, _wave_fixture,
)


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _reserve(wave, quantity=3, cumulative=None):
    return {"event": "reviewer_usage_reserved", "wave": wave,
            "dispatches_this_wave": quantity,
            "cumulative_reviewer_dispatches": quantity if cumulative is None else cumulative}


def _completed(wave, quantity=3, cumulative=None):
    return {**_reserve(wave, quantity, cumulative), "event": "reviewer_usage_wave_completed"}


def _index(wave, quantity=3):
    return {"wave": wave, "payload_count": quantity, "run_id": RUN_ID,
            "manifest_canonical_sha256": MANIFEST_SHA}


def _restore(paths, **kwargs):
    return restore_driver_state(paths, expected_run_id=RUN_ID,
                                expected_manifest_sha256=MANIFEST_SHA, **kwargs)


def test_restores_review_spend_passes_and_unknowns_without_reset(tmp_path):
    paths = MainRunPaths.under(tmp_path)
    _write(paths.run_log, [
        {"event": "formal_main_pass_complete", "pass_index": 1},
        _reserve(1, 60, 60), _completed(1, 60, 60),
        {"event": "formal_main_pass_complete", "pass_index": 3},
        _reserve(3, 60, 120), _completed(3, 60, 120),
        {"event": "abandoned_cell_rate_pass", "pass_index": 4,
         "consecutive_abandoned_rate_passes": 2},
    ])
    _write(paths.reviewer_index, [_index(1, 60), _index(3, 60)])
    _write(paths.usage_ledger, [
        {"status": "unknown_charge", "attempt_id": "a"},
        {"status": "reserved", "attempt_id": "b"},
        {"status": "success", "attempt_id": "b"},
    ])
    state = _restore(paths)
    assert state.first_pass_index == 5
    assert state.reviewer_dispatches == 120
    assert state.consecutive_abandoned_rate == 2
    assert state.existing_unknown_charge_count == 1
    assert state.usage_event_count == 3
    assert state.recovered_reviewer_waves == ()


def test_successful_pass_resets_prior_abandoned_streak(tmp_path):
    paths = MainRunPaths.under(tmp_path)
    _write(paths.run_log, [
        {"event": "abandoned_cell_rate_pass", "pass_index": 4,
         "consecutive_abandoned_rate_passes": 2},
        {"event": "formal_main_pass_complete", "pass_index": 5},
    ])
    assert _restore(paths).consecutive_abandoned_rate == 0


@pytest.mark.parametrize("rows", [
    [_reserve(1, cumulative=0)],
    [_reserve(1), _reserve(1, cumulative=6)],
    [_completed(1)],
    [_reserve(1), _completed(1, cumulative=2)],
])
def test_refuses_drifted_or_duplicate_review_accounting(tmp_path, rows):
    paths = MainRunPaths.under(tmp_path)
    _write(paths.run_log, rows)
    with pytest.raises(RecoveryDriverError):
        _restore(paths)


def test_never_repeats_uncertain_external_reviewer_dispatch(tmp_path):
    paths = MainRunPaths.under(tmp_path)
    _write(paths.run_log, [_reserve(7)])
    with pytest.raises(RecoveryDriverError, match="without redispatching"):
        _restore(paths)


def test_completed_index_recovers_missing_log_without_recount(tmp_path):
    paths = MainRunPaths.under(tmp_path)
    _write(paths.run_log, [_reserve(3)])
    _write(paths.reviewer_index, [_index(3)])
    state = _restore(paths)
    assert state.reviewer_dispatches == 3
    assert state.recovered_reviewer_waves == (3,)
    _write(paths.run_log, [_reserve(3), {"event": "reviewer_usage_wave_recovered", "wave": 3}])
    assert _restore(paths).recovered_reviewer_waves == ()


def test_prepared_review_commit_recovers_only_missing_local_writes(tmp_path):
    paths = MainRunPaths.under(tmp_path)
    fixture = _wave_fixture(tmp_path, wave=2)
    _write(paths.run_log, [_reserve(2)])
    with RunLease(paths.lease) as lease:
        _prepare(fixture, lease)
        with pytest.raises(RecoveryDriverError, match="held run lease"):
            _restore(paths)
        state = _restore(paths, held_run_lease=lease)
        first_bytes = paths.decisions.read_bytes(), paths.reviewer_index.read_bytes()
        again = _restore(paths, held_run_lease=lease)
    assert state.reviewer_dispatches == again.reviewer_dispatches == 3
    assert state.recovered_reviewer_waves == again.recovered_reviewer_waves == (2,)
    assert first_bytes == (paths.decisions.read_bytes(), paths.reviewer_index.read_bytes())


def test_committed_review_reuses_exact_rows_after_missing_completion_log(tmp_path):
    paths = MainRunPaths.under(tmp_path)
    fixture = _wave_fixture(tmp_path, wave=1)
    _write(paths.run_log, [_reserve(1)])
    with RunLease(paths.lease) as lease:
        _commit(fixture, lease)
        first_bytes = paths.decisions.read_bytes(), paths.reviewer_index.read_bytes()
        state = _restore(paths)
    assert state.recovered_reviewer_waves == (1,)
    assert first_bytes == (paths.decisions.read_bytes(), paths.reviewer_index.read_bytes())


def _event(number, status="success", model="qwen"):
    return {"attempt_id": str(number), "status": status, "model": model}


def test_tuner_promotes_only_after_unique_clean_transport_window():
    tuner = ProviderConcurrencyTuner({"qwen": 2, "llama": 4}, {"qwen": 4, "llama": 4})
    first = [_event(i) for i in range(99)]
    assert tuner.observe(first) == []
    assert tuner.observe(first) == []
    assert tuner.limits == {"qwen": 2, "llama": 4}
    changes = tuner.observe([_event(99)])
    assert changes == [{"model": "qwen", "previous_limit": 2, "new_limit": 4,
                        "reason": "clean_transport_window", "clean_successful_calls": 100}]
    assert tuner.limits == {"qwen": 4, "llama": 4}


def test_tuner_uncertain_charge_reduces_width_and_restarts_window():
    tuner = ProviderConcurrencyTuner({"qwen": 2}, {"qwen": 4}, 3)
    tuner.observe([_event(i) for i in range(3)])
    changes = tuner.observe([_event(3, "unknown_charge")])
    assert changes[0]["reason"] == "uncertain_transport_charge"
    assert changes[0]["new_limit"] == tuner.limits["qwen"] == 2
    assert tuner.observe([_event(4), _event(5)]) == []
    assert tuner.observe([_event(6)])[0]["new_limit"] == 4


def test_tuner_does_not_use_reservations_or_unrecognized_models():
    tuner = ProviderConcurrencyTuner({"qwen": 2}, {"qwen": 4}, 1)
    assert tuner.observe([_event(1, "reserved"), _event(2, model="unbound")]) == []
    assert tuner.limits == {"qwen": 2}
    limits = tuner.limits
    limits["qwen"] = 100
    assert tuner.limits == {"qwen": 2}


def test_tuner_rejects_limit_above_signed_maximum():
    with pytest.raises(RecoveryDriverError, match="signed maximum"):
        ProviderConcurrencyTuner({"qwen": 8}, {"qwen": 4})
