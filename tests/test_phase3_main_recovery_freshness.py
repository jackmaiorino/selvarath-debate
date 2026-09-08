"""Recovery preserves the original admission clock without weakening fresh launches."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from rejudge import phase3_main_live as live
from rejudge import phase3_v3_inputs as inputs
from test_phase3_main_live import _prepared, _seed_price_signal_identity, inventory
from test_phase3_v3_inputs import _price_snapshot, protocol


ORIGINAL_START = datetime(2026, 9, 7, 17, tzinfo=timezone.utc)
RESUME = datetime(2026, 9, 8, 19, tzinfo=timezone.utc)


def _recovery_fixture(tmp_path, inventory, *, started_at=ORIGINAL_START):
    prepared = _prepared(tmp_path, inventory)
    start_path, _ = _seed_price_signal_identity(prepared)
    start = json.loads(start_path.read_bytes())
    start["recorded_at_utc"] = started_at.isoformat()
    start_raw = (json.dumps(start) + "\n").encode()
    start_path.write_bytes(start_raw)
    active_path = prepared.identity.paths.active_marker
    active = json.loads(active_path.read_bytes())
    active["started_at_utc"] = (started_at - timedelta(seconds=1)).isoformat()
    active_path.write_text(json.dumps(active) + "\n", encoding="utf-8")
    recovery = {"immutable_artifacts": {"identity_start": {
        "path": start_path.as_posix(), "size_bytes": len(start_raw),
        "raw_sha256": hashlib.sha256(start_raw).hexdigest()}}}
    return replace(prepared, recovery_validation=recovery), start_path


def _clock(prepared, recovery):
    return live._launch_evidence_as_of(
        prepared.manifest, prepared.manifest_validation, recovery, now=RESUME)


def test_original_price_admission_passes_on_resume_but_fresh_stale_price_still_refuses(
    tmp_path, inventory, protocol,
):
    prepared, _ = _recovery_fixture(tmp_path, inventory)
    snapshot = _price_snapshot(protocol, tmp_path / "catalog.json",
                               verified_at="2026-09-07T16:00:00Z")
    original_clock = _clock(prepared, prepared.recovery_validation)
    assert original_clock == ORIGINAL_START
    result = inputs.validate_price_snapshot(snapshot, protocol=protocol, as_of=original_clock)
    assert result["age_seconds"] == 3600
    with pytest.raises(inputs.InputGateError, match="24-hour"):
        inputs.validate_price_snapshot(snapshot, protocol=protocol, as_of=_clock(prepared, None))


def test_recovery_rejects_changed_original_start_bytes(tmp_path, inventory):
    prepared, path = _recovery_fixture(tmp_path, inventory)
    value = json.loads(path.read_bytes())
    value["recorded_at_utc"] = (ORIGINAL_START + timedelta(minutes=1)).isoformat()
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(live.Phase3MainLiveError, match="signed recovery start evidence"):
        _clock(prepared, prepared.recovery_validation)


def test_recovery_rejects_even_bound_future_start_clock(tmp_path, inventory):
    prepared, _ = _recovery_fixture(tmp_path, inventory, started_at=RESUME + timedelta(minutes=1))
    with pytest.raises(live.Phase3MainLiveError, match="future"):
        _clock(prepared, prepared.recovery_validation)


def test_recovery_refuses_missing_original_start(tmp_path, inventory):
    prepared, path = _recovery_fixture(tmp_path, inventory)
    path.unlink()
    with pytest.raises(live.Phase3MainLiveError, match="regular file"):
        _clock(prepared, prepared.recovery_validation)


def test_fresh_clock_never_uses_an_existing_start_without_recovery(tmp_path, inventory, monkeypatch):
    prepared = _prepared(tmp_path, inventory)
    def forbidden(*args, **kwargs):
        raise AssertionError("fresh admission must not read old start evidence")
    monkeypatch.setattr(live, "_price_change_start_evidence", forbidden)
    assert _clock(prepared, None) == RESUME
