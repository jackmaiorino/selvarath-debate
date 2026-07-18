from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase2_plan, phase2_provider_price_snapshot as provider_snapshot


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json"
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"


def _artifacts():
    snapshot, protocol = provider_snapshot.load_and_validate(
        SNAPSHOT_PATH, PROTOCOL_PATH)
    return snapshot, protocol


def test_tracked_snapshot_matches_all_five_frozen_models_and_prices():
    snapshot, protocol = _artifacts()
    assert set(snapshot["models"]) == set(protocol["model_registry"])
    assert len(snapshot["models"]) == 5
    assert snapshot["status"] == (
        "public_catalog_verified_pending_account_reconciliation")
    assert "authorization to make a provider call" in (
        snapshot["scope"]["does_not_establish"])


def test_price_drift_is_rejected():
    snapshot, protocol = _artifacts()
    changed = deepcopy(snapshot)
    changed["models"]["google/gemma-4-31B-it"][
        "output_usd_per_million_tokens"] = 0.98
    with pytest.raises(provider_snapshot.ProviderSnapshotError, match="price mismatch"):
        provider_snapshot.validate_snapshot(changed, protocol)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_model_roster_drift_is_rejected(mutation):
    snapshot, protocol = _artifacts()
    changed = deepcopy(snapshot)
    if mutation == "missing":
        del changed["models"]["openai/gpt-oss-120b"]
    else:
        changed["models"]["example/unapproved"] = deepcopy(
            next(iter(changed["models"].values())))
    with pytest.raises(provider_snapshot.ProviderSnapshotError, match="model roster"):
        provider_snapshot.validate_snapshot(changed, protocol)


def test_invalid_context_and_timestamp_are_rejected():
    snapshot, protocol = _artifacts()
    bad_context = deepcopy(snapshot)
    bad_context["models"]["Qwen/Qwen3.7-Plus"]["context_length_tokens"] = True
    with pytest.raises(provider_snapshot.ProviderSnapshotError, match="context length"):
        provider_snapshot.validate_snapshot(bad_context, protocol)

    bad_time = deepcopy(snapshot)
    bad_time["verified_at_utc"] = "2026-07-18"
    with pytest.raises(provider_snapshot.ProviderSnapshotError, match="UTC timestamp"):
        provider_snapshot.validate_snapshot(bad_time, protocol)


def test_limitations_and_source_cannot_silently_drift():
    snapshot, protocol = _artifacts()
    changed = deepcopy(snapshot)
    changed["scope"]["does_not_establish"].remove(
        "authorization to make a provider call")
    with pytest.raises(provider_snapshot.ProviderSnapshotError, match="limitations"):
        provider_snapshot.validate_snapshot(changed, protocol)

    changed = deepcopy(snapshot)
    changed["source"]["url"] = "https://example.invalid/prices"
    with pytest.raises(provider_snapshot.ProviderSnapshotError, match="source drifted"):
        provider_snapshot.validate_snapshot(changed, protocol)


def test_cli_check_is_offline_and_prints_canonical_hash(capsys):
    assert provider_snapshot.main([
        "--check", "--snapshot", str(SNAPSHOT_PATH), "--protocol", str(PROTOCOL_PATH),
    ]) == 0
    output = capsys.readouterr().out
    snapshot, _protocol = _artifacts()
    assert phase2_plan.canonical_sha256(snapshot) in output
    assert "account_reconciled=NO" in output
    assert "execution_authorized=NO" in output
