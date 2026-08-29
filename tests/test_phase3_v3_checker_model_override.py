"""Regression: the production checker call must target the protocol's checker model.

Run phase3-v3-5cdeb74a8f8f1742 (r29) wedged on a deterministic pre-reservation failure:
after amendment 9 substituted Llama for the query_checker role, FrozenCheckerAdapter
kept targeting the model named in the frozen phase-2 checker config (gemma-4), which
amendment 9 had removed from the role-limits tables entirely. Every b2 cell's checker
call then failed client-side and surfaced as checker_outage: an integration layer the
live-query screen never exercised because it called the substitute model directly. These
tests drive the exact production derivation from the committed r6 protocol and r10
role-limits artifacts, and pin the phase-2 default unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from rejudge import phase3_plan
from rejudge.phase2_canary_gate import FrozenCheckerAdapter
from rejudge.phase2_query_gate import CheckerRequest

ROOT = Path(__file__).resolve().parents[1]


class _CapturingClient:
    def __init__(self) -> None:
        self.calls = []

    def complete(self, messages, model, temperature, seed, max_tokens, kind="verdict",
                 *, request_metadata=None):
        self.calls.append({
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "seed": seed, "metadata": dict(request_metadata or {})})
        return "allow"


def _request() -> CheckerRequest:
    return CheckerRequest(
        candidate_a="Position A summary", candidate_b="Position B summary",
        raw_query="CLAIM: something checkable.", slot=1, attempt=1)


def test_r6_protocol_checker_calls_target_llama_at_the_frozen_16_token_budget():
    protocol = phase3_plan.load_protocol(ROOT / "rejudge/phase3_protocol_v3_r6.json")
    role_limits = json.loads(
        (ROOT / "rejudge/phase3_v3_role_limits_r10_2026-08-28.json").read_text(
            encoding="utf-8"))
    # The exact derivation _run_judgment_loop performs when role_limits is present.
    checker_model = str(protocol["roster"]["query_checker"])
    entry = role_limits["model_role_limits"][checker_model]["query_checker"]
    client = _CapturingClient()
    adapter = FrozenCheckerAdapter(
        client, request_metadata={"cell_key": "test-cell"},
        model_override=checker_model,
        max_tokens_override=int(entry["effective_request_max_tokens"]))
    assert adapter(_request()) == "allow"
    call = client.calls[0]
    assert call["model"] == "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    assert call["max_tokens"] == 16
    assert call["metadata"]["call_role"] == "query_checker"


def test_phase2_default_still_targets_the_frozen_config_model():
    client = _CapturingClient()
    adapter = FrozenCheckerAdapter(client)
    adapter(_request())
    call = client.calls[0]
    assert call["model"] == "google/gemma-4-31B-it"
    assert call["max_tokens"] == 4096


def test_override_is_identity_for_the_r5_protocol_generation():
    # Through r5 the roster's checker IS the config model, so the amendment-9 pathway
    # resolves to the same target it always had.
    protocol = phase3_plan.load_protocol(ROOT / "rejudge/phase3_protocol_v3_r5.json")
    assert str(protocol["roster"]["query_checker"]) == "google/gemma-4-31B-it"
