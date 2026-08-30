"""Focused tests for deterministic main context-blocklist admission."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from rejudge import phase3_main_context


def test_context_blocklist_must_equal_frozen_input_recomputation(monkeypatch):
    report = {
        "generated_at": "2026-08-29T00:00:00Z",
        "scope": "main",
        "cell_key_namespace": "fixture",
        "estimator_provenance": {
            "module": "rejudge.api_client",
            "function": "estimate_context_tokens",
        },
        "ceilings_used": {"judge": 100},
        "excluded": [{"cell_key": "cell-1", "worst_case_tokens": 101}],
        "counts_by_judge_and_condition": [],
        "excluded_count": 1,
    }
    inventory = SimpleNamespace(cells=({"cell_key": "cell-1"},))
    transcript_index = {("debater", "question", 0): {"question": "fixture"}}
    monkeypatch.setattr(
        phase3_main_context.phase3_context_precheck,
        "load_transcript_index",
        lambda bundle: transcript_index,
    )

    def fake_build(**kwargs):
        assert kwargs["scope"] == "main"
        assert kwargs["generated_at"] == report["generated_at"]
        assert kwargs["plan_cells"] == inventory.cells
        assert kwargs["transcript_index"] is transcript_index
        return report

    monkeypatch.setattr(
        phase3_main_context.phase3_context_precheck, "build_report", fake_build)
    assert phase3_main_context.validate_main_context_blocklist(
        report,
        protocol={"cell_key_namespace": "fixture"},
        inventory=inventory,
        prompt_bundle={},
        role_limits={},
        transcript_bundle={},
    ) == ("cell-1",)

    drifted = {**report, "excluded_count": 0}
    with pytest.raises(
        phase3_main_context.MainContextBlocklistError,
        match="deterministic frozen-input recomputation",
    ):
        phase3_main_context.validate_main_context_blocklist(
            drifted,
            protocol={"cell_key_namespace": "fixture"},
            inventory=inventory,
            prompt_bundle={},
            role_limits={},
            transcript_bundle={},
        )


def test_context_blocklist_requires_generation_input():
    with pytest.raises(
        phase3_main_context.MainContextBlocklistError,
        match="generated_at",
    ):
        phase3_main_context.validate_main_context_blocklist(
            {},
            protocol={},
            inventory=SimpleNamespace(cells=()),
            prompt_bundle={},
            role_limits={},
            transcript_bundle={},
        )
