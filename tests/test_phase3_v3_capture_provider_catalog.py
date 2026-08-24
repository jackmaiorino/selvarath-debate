"""Tests for the read-only Together catalog capture command."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import phase3_v3_capture_provider_catalog as capture


class _FakeModel:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

    def to_dict(self, **kwargs):
        self.kwargs = kwargs
        return self.payload


class _FakeModels:
    def __init__(self, entries):
        self.entries = entries
        self.calls = 0

    def list(self):
        self.calls += 1
        return self.entries


class _FakeClient:
    def __init__(self, entries):
        self.models = _FakeModels(entries)


def test_capture_uses_only_model_list_and_complete_sdk_serialization():
    model = _FakeModel({"id": "example/model", "link": None})
    client = _FakeClient([model])
    assert capture.capture_catalog(client) == [{"id": "example/model", "link": None}]
    assert client.models.calls == 1
    assert model.kwargs == {
        "mode": "json",
        "use_api_names": True,
        "exclude_unset": False,
        "exclude_none": False,
    }


def test_write_is_compact_create_only_and_hashes_raw_bytes(tmp_path: Path):
    path = tmp_path / "catalog.json"
    payload = [{"id": "example/model", "pricing": {"input": 1.0}}]
    digest = capture.write_catalog(path, payload)
    raw = path.read_bytes()
    assert raw == b'[{"id":"example/model","pricing":{"input":1.0}}]\n'
    assert digest == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw) == payload
    with pytest.raises(capture.CatalogCaptureError, match="refusing to overwrite"):
        capture.write_catalog(path, payload)


def test_capture_rejects_empty_or_unserializable_catalog():
    with pytest.raises(capture.CatalogCaptureError, match="empty"):
        capture.serialize_catalog([])
    with pytest.raises(capture.CatalogCaptureError, match="to_dict"):
        capture.serialize_catalog([{"id": "not-an-sdk-object"}])
