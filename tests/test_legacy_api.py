import asyncio

import pytest

import api


def _complete():
    return api.complete(
        [{"role": "user", "content": "test"}],
        "model", 0.0, seed=1, max_tokens=8)


def test_legacy_api_remains_available_for_dry_reproduction(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    assert "Dry run" in asyncio.run(_complete())


def test_legacy_api_refuses_live_calls_before_loading_an_sdk(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    with pytest.raises(api.LegacyLiveRunDisabledError, match="original pilot harness"):
        asyncio.run(_complete())
