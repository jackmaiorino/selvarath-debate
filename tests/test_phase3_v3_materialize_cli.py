"""End-to-end determinism coverage for the Phase 3 v3 materializer CLI."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import phase3_v3_materialize as cli  # ty: ignore[unresolved-import]  # noqa: E402
from rejudge import phase3_v3_materialization as materialization  # noqa: E402
from rejudge.phase2_execution import canonical_sha256  # noqa: E402

from tests.test_phase3_v3_materialization import _resolution  # noqa: E402


def _stage_inputs(root: Path) -> Path:
    for relative_path in (materialization.V2_PROTOCOL_PATH, materialization.DESIGN_PATH):
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative_path, destination)

    evidence = {"result": "synthetic_provider_unavailable"}
    evidence_relative = Path("rejudge/e2e/roster_resolution_evidence.json")
    evidence_path = root / evidence_relative
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    resolution = _resolution(materialization.PROVIDER_UNAVAILABLE_OUTCOME)
    resolution_relative = Path("rejudge/e2e/roster_resolution.json")
    resolution["tracked_path"] = resolution_relative.as_posix()
    resolution["evidence"] = {
        "tracked_path": evidence_relative.as_posix(),
        "canonical_sha256": canonical_sha256(evidence),
    }
    resolution_path = root / resolution_relative
    resolution_path.write_text(
        json.dumps(resolution, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return resolution_path


def _materialize_once(monkeypatch: pytest.MonkeyPatch, root: Path) -> tuple[dict, bytes, bytes]:
    resolution_path = _stage_inputs(root)
    protocol_path = root / "rejudge/e2e/phase3_protocol_v3.json"
    pin_path = root / "rejudge/e2e/phase3_protocol_v3_pin.json"
    monkeypatch.setattr(cli, "REPO_ROOT", root)
    created = cli.materialize(
        resolution_path=resolution_path,
        protocol_output_path=protocol_path,
        pin_output_path=pin_path,
        check_only=False,
    )
    protocol_bytes = protocol_path.read_bytes()
    pin_bytes = pin_path.read_bytes()
    checked = cli.materialize(
        resolution_path=resolution_path,
        protocol_output_path=protocol_path,
        pin_output_path=pin_path,
        check_only=True,
    )
    assert checked["status"] == "verified"
    assert protocol_path.read_bytes() == protocol_bytes
    assert pin_path.read_bytes() == pin_bytes
    return created, protocol_bytes, pin_bytes


def test_two_fresh_materializations_are_bit_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    first, first_protocol, first_pin = _materialize_once(monkeypatch, tmp_path / "first")
    second, second_protocol, second_pin = _materialize_once(monkeypatch, tmp_path / "second")
    assert first["execution_authorized"] is False
    assert second["execution_authorized"] is False
    assert first["protocol_canonical_sha256"] == second["protocol_canonical_sha256"]
    assert first["pin_canonical_sha256"] == second["pin_canonical_sha256"]
    assert first_protocol == second_protocol
    assert first_pin == second_pin
