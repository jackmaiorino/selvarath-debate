"""Tests for the offline, network-free capability-preflight forecast validator.

Tokenizer downloads (the network-touching step) live only in
``scripts/build_phase2_preflight_forecast.py``, never here: this suite exercises
``rejudge.phase2_capability_corpus`` (deterministic corpus rendering) and
``rejudge.phase2_preflight_forecast`` (fail-closed validation and Decimal arithmetic) purely
against tracked JSON and in-memory fixtures.
"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import json
import pytest

from rejudge import (
    phase2_capability_corpus as capability_corpus,
    phase2_plan,
    phase2_preflight_forecast as forecast,
    phase2_prompt_bundle,
    phase2_provider_price_snapshot as price_snapshot,
    phase2_role_limits,
)


ROOT = Path(__file__).resolve().parents[1]
CONFLICT_ARTIFACT_PATH = ROOT / "rejudge" / "phase2_preflight_forecast_conflict_2026-07-18.json"
READY_ARTIFACT_PATH = ROOT / "rejudge" / "phase2_preflight_forecast_2026-07-18.json"
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"


# --- shared real fixtures (module-scoped: these are read-only loads of tracked files) ----------


@pytest.fixture(scope="module")
def protocol():
    return phase2_plan.load_protocol(PROTOCOL_PATH)


@pytest.fixture(scope="module")
def role_limits_v2(protocol):
    artifact, _protocol, _snapshot = phase2_role_limits.load_and_validate_v2()
    return artifact


@pytest.fixture(scope="module")
def snapshot(protocol):
    artifact, _protocol = price_snapshot.load_and_validate()
    return artifact


@pytest.fixture(scope="module")
def bundle(protocol):
    artifact, _protocol = phase2_prompt_bundle.load_and_validate()
    return artifact


@pytest.fixture(scope="module")
def corpus_entries(bundle, protocol):
    return capability_corpus.render_capability_corpus(bundle, protocol, ROOT)


@pytest.fixture(scope="module")
def conflict_artifact():
    return forecast.load_and_validate_conflict_report()


# =================================================================================================
# rejudge.phase2_capability_corpus
# =================================================================================================


def test_corpus_has_exactly_212_entries_in_deterministic_order(corpus_entries):
    assert len(corpus_entries) == capability_corpus.EXPECTED_ENTRY_COUNT == 212
    question_ids = [e["question_id"] for e in corpus_entries]
    # ascending question_id, side A then B within each question
    assert question_ids == sorted(question_ids)
    for i in range(0, len(corpus_entries), 2):
        assert corpus_entries[i]["question_id"] == corpus_entries[i + 1]["question_id"]
        assert corpus_entries[i]["side"] == "A"
        assert corpus_entries[i + 1]["side"] == "B"


def test_corpus_covers_all_106_questions_including_calibration_excluded(corpus_entries, protocol):
    question_ids = {e["question_id"] for e in corpus_entries}
    assert len(question_ids) == 106
    excluded = set(protocol["question_set"]["calibration_excluded_question_ids"])
    assert excluded.issubset(question_ids)


def test_corpus_sides_are_mirrored_candidate_swaps(corpus_entries):
    by_question: dict[str, dict] = {}
    for entry in corpus_entries:
        by_question.setdefault(entry["question_id"], {})[entry["side"]] = entry
    for question_id, sides in by_question.items():
        a, b = sides["A"], sides["B"]
        assert a["system_prompt"] == b["system_prompt"]
        assert a["user_prompt"] != b["user_prompt"]
        # same world document, question, and candidate texts recur, just position-swapped
        assert a["world"] == b["world"]


def test_corpus_rendering_is_deterministic(bundle, protocol):
    first = capability_corpus.render_capability_corpus(bundle, protocol, ROOT)
    second = capability_corpus.render_capability_corpus(bundle, protocol, ROOT)
    assert first == second
    assert capability_corpus.corpus_canonical_sha256(first) == (
        capability_corpus.corpus_canonical_sha256(second))


def test_corpus_entry_keys_are_exact(corpus_entries):
    for entry in corpus_entries:
        assert set(entry) == capability_corpus.CORPUS_ENTRY_KEYS


def test_all_106_question_ids_rejects_a_non_all_106_protocol(bundle, protocol):
    changed = deepcopy(protocol)
    changed["decisions"]["capability_measurement"]["question_set"] = "main_82"
    with pytest.raises(capability_corpus.CapabilityCorpusError, match="all_106"):
        capability_corpus.all_106_question_ids(changed, ROOT)


# =================================================================================================
# pure helpers: percentile / compute_token_stats / byte_reservation_bound / scenario arithmetic
# =================================================================================================


def test_percentile_nearest_rank_examples():
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert forecast.percentile(values, Decimal("0.50")) == 50
    assert forecast.percentile(values, Decimal("0.95")) == 100
    assert forecast.percentile(values, Decimal("0")) == 10


def test_compute_token_stats_matches_hand_computation():
    stats = forecast.compute_token_stats([1, 2, 3, 4])
    assert stats["total"] == 10
    assert stats["mean"] == "2.500000"
    assert stats["max"] == 4
    assert stats["per_prompt"] == [1, 2, 3, 4]


def test_compute_token_stats_rejects_empty_or_negative():
    with pytest.raises(forecast.PreflightForecastError):
        forecast.compute_token_stats([])
    with pytest.raises(forecast.PreflightForecastError):
        forecast.compute_token_stats([1, -1])


def test_byte_reservation_bound_matches_documented_formula():
    system = "abc"
    user = "de"
    expected = 64 + (32 + len(b"system") + len(b"abc")) + (32 + len(b"user") + len(b"de"))
    assert forecast.byte_reservation_bound(system, user) == expected


def test_byte_reservation_bound_counts_utf8_bytes_not_characters():
    # a single multi-byte character must add its UTF-8 byte length, not 1
    narrow = forecast.byte_reservation_bound("a", "b")
    wide = forecast.byte_reservation_bound("中", "b")  # 3-byte UTF-8 character
    assert wide == narrow + 2


def test_compute_scenario_component_is_exact_decimal_arithmetic():
    component = forecast.compute_scenario_component(
        total_input_tokens=1_000_000, calls=10, output_tokens_per_call=5,
        input_price=Decimal("2"), output_price=Decimal("3"),
    )
    assert component["input_tokens"] == 1_000_000
    assert component["output_tokens_total"] == 50
    assert component["input_cost_usd"] == "2"
    assert component["output_cost_usd"] == "0.00015"
    assert component["total_usd"] == "2.00015"


# =================================================================================================
# validate_conflict_report: the real, on-disk diagnostic artifact
# =================================================================================================


def test_conflict_artifact_loads_and_validates(conflict_artifact, protocol):
    assert conflict_artifact["execution_authorized"] is False
    assert conflict_artifact["status"] == forecast.CONFLICT_STATUS
    assert conflict_artifact["protocol_id"] == protocol["protocol_id"]
    assert conflict_artifact["resolution"]["required"] is True
    assert conflict_artifact["resolution"]["stress_below_halt_cap"] is False
    assert len(conflict_artifact["resolution"]["options"]) >= 1


def test_conflict_artifact_reports_the_real_blocking_numbers(conflict_artifact):
    halt_cap = Decimal(conflict_artifact["halt_cap_usd"])
    stress = Decimal(conflict_artifact["scenarios"]["four_attempt_stress"]["total_usd"])
    margin = Decimal(conflict_artifact["stress_margin_usd"])
    assert halt_cap == Decimal("15")
    assert stress >= halt_cap  # this IS the conflict; if it ever stops holding, see below
    assert margin == halt_cap - stress
    assert margin < 0


def test_conflict_artifact_per_model_classifications(conflict_artifact):
    stats = conflict_artifact["per_model_token_stats"]
    assert stats["Qwen/Qwen2.5-7B-Instruct-Turbo"]["classification"] == (
        forecast.CLASSIFICATION_EXACT)
    assert stats["google/gemma-4-31B-it"]["classification"] == forecast.CLASSIFICATION_EXACT
    assert stats["openai/gpt-oss-120b"]["classification"] == forecast.CLASSIFICATION_EXACT
    assert stats["Qwen/Qwen3.7-Plus"]["classification"] == forecast.CLASSIFICATION_PROXY
    assert stats["meta-llama/Llama-3.3-70B-Instruct-Turbo"]["classification"] == (
        forecast.CLASSIFICATION_BYTE_BOUND)
    # the byte-bound-only model's "expected" count must exactly mirror its safety bound
    llama = stats["meta-llama/Llama-3.3-70B-Instruct-Turbo"]
    assert llama["input_tokens"] == llama["utf8_byte_reservation_bound"]


def test_conflict_artifact_llama_tokenizer_attempts_are_recorded_honestly(conflict_artifact):
    pin = conflict_artifact["tokenizer_pins"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"]
    assert pin["fallback_used"] is True
    assert len(pin["attempted_tokenizers"]) >= 1
    for attempt in pin["attempted_tokenizers"]:
        assert attempt["outcome"] != "loaded"
        assert attempt["error"]


def test_validate_forecast_rejects_the_conflict_shaped_artifact(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    """The 'ready' validator must never accept the conflict-report shape, or vice versa."""
    with pytest.raises(forecast.PreflightForecastError, match="fields drifted"):
        forecast.validate_forecast(
            conflict_artifact, root=ROOT, protocol=protocol, role_limits_v2=role_limits_v2,
            snapshot=snapshot, bundle=bundle,
        )


# --- mutation / fail-closed tests (deepcopy + mutate + pytest.raises) --------------------------


def _validate_conflict(artifact, protocol, role_limits_v2, snapshot, bundle):
    forecast.validate_conflict_report(
        artifact, root=ROOT, protocol=protocol, role_limits_v2=role_limits_v2,
        snapshot=snapshot, bundle=bundle,
    )


def test_duplicate_json_key_is_rejected(tmp_path):
    bad_path = tmp_path / "dup.json"
    bad_path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(forecast.PreflightForecastError, match="duplicate JSON key"):
        forecast._load_json(bad_path)


def test_nan_literal_is_rejected(tmp_path):
    bad_path = tmp_path / "nan.json"
    bad_path.write_text('{"a": NaN}', encoding="utf-8")
    with pytest.raises(forecast.PreflightForecastError, match="non-finite"):
        forecast._load_json(bad_path)


def test_top_level_key_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["unexpected_extra_field"] = True
    with pytest.raises(forecast.PreflightForecastError, match="fields drifted"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_execution_authorized_must_be_false(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["execution_authorized"] = True
    with pytest.raises(forecast.PreflightForecastError, match="execution_authorized"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_binding_hash_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    sha = changed["bindings"]["role_limits_v2"]["canonical_sha256"]
    changed["bindings"]["role_limits_v2"]["canonical_sha256"] = (
        sha[:-1] + ("0" if sha[-1] != "0" else "1"))
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_binding_tracked_path_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["bindings"]["prompt_bundle"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(forecast.PreflightForecastError, match="tracked_path must be exactly"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_rendered_corpus_hash_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    sha = changed["bindings"]["rendered_corpus"]["canonical_sha256"]
    changed["bindings"]["rendered_corpus"]["canonical_sha256"] = (
        sha[:-1] + ("0" if sha[-1] != "0" else "1"))
    with pytest.raises(forecast.PreflightForecastError, match="rendered_corpus"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_classification_vocabulary_is_enforced(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["per_model_token_stats"]["openai/gpt-oss-120b"]["classification"] = "made_up_kind"
    with pytest.raises(forecast.PreflightForecastError, match="classification"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_classification_must_match_the_frozen_per_model_expectation(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    # a real classification value, but the wrong one for this model
    changed["per_model_token_stats"]["openai/gpt-oss-120b"]["classification"] = (
        forecast.CLASSIFICATION_PROXY)
    with pytest.raises(forecast.PreflightForecastError, match="classification"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_per_prompt_total_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    stats = changed["per_model_token_stats"]["Qwen/Qwen2.5-7B-Instruct-Turbo"]["input_tokens"]
    stats["per_prompt"][0] += 1000
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with the recomputed"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_llama_input_tokens_must_mirror_its_byte_bound(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    llama = changed["per_model_token_stats"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"]
    # a fully self-consistent (recomputes cleanly on its own) but shifted per-prompt series,
    # so the failure is specifically the byte-bound mirroring check, not a stats-recompute error
    shifted = [v + 1 for v in llama["input_tokens"]["per_prompt"]]
    llama["input_tokens"] = forecast.compute_token_stats(shifted)
    with pytest.raises(forecast.PreflightForecastError, match="must exactly mirror"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_qwen_proxy_max_derivation_is_enforced(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    pin = changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]
    pin["proxies"][0]["per_prompt"][0] += 5000  # now the recorded max no longer matches
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with the recomputed"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_output_ceiling_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["output_token_policy"]["effective_output_ceiling_per_model"][
        "google/gemma-4-31B-it"] = 32
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with the frozen"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_retry_policy_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["retry_policy"]["max_attempts"] = 5
    with pytest.raises(forecast.PreflightForecastError, match="max_attempts"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_scenario_total_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["scenarios"]["theoretical_minimum"]["total_usd"] = "999999"
    with pytest.raises(forecast.PreflightForecastError, match="theoretical_minimum.total_usd"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_derived_scenario_multiplier_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["scenarios"]["planning_retry_scenario"]["multiplier"] = "2.00"
    with pytest.raises(forecast.PreflightForecastError, match="multiplier"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_qwen_byte_bound_stress_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["scenarios"]["four_attempt_stress"]["qwen_3_7_plus_byte_bound_stress_usd"] = "0.01"
    with pytest.raises(forecast.PreflightForecastError, match="qwen_3_7_plus_byte_bound_stress_usd"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_halt_cap_drift_from_protocol_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["halt_cap_usd"] = "20"
    with pytest.raises(forecast.PreflightForecastError, match="halt_cap_usd"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_stress_margin_drift_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["stress_margin_usd"] = "0"
    with pytest.raises(forecast.PreflightForecastError, match="stress_margin_usd"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_conflict_report_rejects_a_report_that_no_longer_conflicts(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle, monkeypatch,
):
    """A conflict report whose own numbers say stress < cap must be refused: it is stale.

    Isolates just ``validate_conflict_report``'s final gate direction by stubbing out
    ``_validate_shared_body`` (itself thoroughly exercised by the mutation tests above) with a
    fixed, already-clears-the-cap (stress, halt_cap) pair.
    """
    monkeypatch.setattr(
        forecast, "_validate_shared_body", lambda *args, **kwargs: (Decimal("1"), Decimal("15")))
    with pytest.raises(forecast.PreflightForecastError, match="no longer a genuine conflict"):
        _validate_conflict(conflict_artifact, protocol, role_limits_v2, snapshot, bundle)


@pytest.mark.parametrize("field,value", [
    ("required", False), ("stress_below_halt_cap", True),
])
def test_resolution_flags_are_pinned(conflict_artifact, protocol, role_limits_v2, snapshot, bundle, field, value):
    changed = deepcopy(conflict_artifact)
    changed["resolution"][field] = value
    with pytest.raises(forecast.PreflightForecastError, match="resolution"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_resolution_requires_nonempty_options(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["resolution"]["options"] = []
    with pytest.raises(forecast.PreflightForecastError, match="options"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_caveats_missing_required_entry_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["caveats"] = [c for c in changed["caveats"]
                           if c["id"] != "reasoning_token_wildcard"]
    with pytest.raises(forecast.PreflightForecastError, match="caveats missing"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_caveats_duplicate_id_is_rejected(conflict_artifact, protocol, role_limits_v2, snapshot, bundle):
    changed = deepcopy(conflict_artifact)
    changed["caveats"].append(dict(changed["caveats"][0]))
    with pytest.raises(forecast.PreflightForecastError, match="duplicate caveat id"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


# =================================================================================================
# validate_forecast (the "ready" shape): numeric gate direction + full acceptance path
# =================================================================================================


def test_ready_gate_fires_on_the_real_currently_blocking_numbers(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    """Reinterpreting the real (expensive) numbers as a 'ready' artifact must still fail --
    proves the numeric gate direction is live, not just the header-shape check."""
    changed = deepcopy(conflict_artifact)
    changed["schema_version"] = forecast.SCHEMA_VERSION
    changed["artifact_id"] = forecast.ARTIFACT_ID
    changed["status"] = forecast.STATUS
    del changed["resolution"]
    with pytest.raises(forecast.PreflightForecastError, match="does not remain below halt_cap_usd"):
        forecast.validate_forecast(
            changed, root=ROOT, protocol=protocol, role_limits_v2=role_limits_v2,
            snapshot=snapshot, bundle=bundle,
        )


@pytest.fixture(scope="module")
def ready_context(tmp_path_factory, bundle, conflict_artifact):
    """Full acceptance-path fixture: real protocol/role-limits/bundle/corpus, a synthetic
    cheap-priced snapshot (in-memory prices are the only thing that legitimately varies
    scenario dollar totals independent of token counts), reusing the REAL per-model token
    counts from the conflict artifact so every recomputed statistic is genuinely token-derived.

    Returns a dict with a genuinely-passing ``artifact`` plus the exact
    ``root``/``protocol``/``role_limits_v2``/``snapshot``/``bundle`` it validates against, so
    tests can deepcopy+mutate the artifact to exercise individual failure branches of
    ``validate_forecast`` without re-deriving this whole fixture per test.
    """
    cheap_snapshot: dict[str, Any] = {
        "schema_version": "phase2_provider_price_snapshot_v1",
        "status": "public_catalog_verified_pending_account_reconciliation",
        "provider": "Together AI",
        "verified_at_utc": "2026-07-18T12:31:29Z",
        "source": {
            "catalog_section": "Serverless models / Chat models",
            "url": "https://docs.together.ai/docs/serverless/models",
        },
        "scope": {
            "claim": (
                "Each frozen Phase 2 roster model ID was listed in Together's public "
                "serverless chat-model catalog at verification time, and its standard "
                "input/output prices matched rejudge/phase2_protocol.json."
            ),
            "does_not_establish": [
                "account-specific access or capacity", "successful completion behavior",
                "provider backend stability", "account usage or credit reconciliation",
                "authorization to make a provider call",
            ],
        },
        "models": {
            model_id: {
                "context_length_tokens": 1000,
                "input_usd_per_million_tokens": 0.0001,
                "output_usd_per_million_tokens": 0.0001,
            }
            for model_id in sorted(forecast.MODEL_IDS)
        },
        "comparison_to_frozen_design": {
            "base_protocol_path": "rejudge/phase2_protocol.json",
            "all_five_model_ids_listed": True,
            "all_standard_input_output_prices_match": True,
        },
    }

    protocol = phase2_plan.load_protocol(PROTOCOL_PATH)
    role_limits_v2, _protocol, _snapshot = phase2_role_limits.load_and_validate_v2()

    calls = capability_corpus.EXPECTED_ENTRY_COUNT
    output_ceilings = {
        model_id: phase2_role_limits.resolve_request_parameters(
            role_limits_v2, protocol, model_id, "capability_qa").effective_max_tokens
        for model_id in forecast.MODEL_IDS
    }

    def price_for(model_id):
        entry = cheap_snapshot["models"][model_id]
        return (
            Decimal(str(entry["input_usd_per_million_tokens"])),
            Decimal(str(entry["output_usd_per_million_tokens"])),
        )

    theo_per_model, no_retry_per_model = {}, {}
    theo_total = no_retry_total = Decimal(0)
    no_retry_component_usd = {}
    for model_id in forecast.MODEL_IDS:
        total_input_tokens = conflict_artifact["per_model_token_stats"][model_id][
            "input_tokens"]["total"]
        input_price, output_price = price_for(model_id)
        theo = forecast.compute_scenario_component(
            total_input_tokens=total_input_tokens, calls=calls,
            output_tokens_per_call=forecast.THEORETICAL_MINIMUM_OUTPUT_TOKENS_PER_CALL,
            input_price=input_price, output_price=output_price,
        )
        theo_per_model[model_id] = theo
        theo_total += Decimal(theo["total_usd"])
        no_retry = forecast.compute_scenario_component(
            total_input_tokens=total_input_tokens, calls=calls,
            output_tokens_per_call=output_ceilings[model_id],
            input_price=input_price, output_price=output_price,
        )
        no_retry_per_model[model_id] = no_retry
        no_retry_component_usd[model_id] = Decimal(no_retry["total_usd"])
        no_retry_total += Decimal(no_retry["total_usd"])

    def derived(multiplier):
        per_model, total = {}, Decimal(0)
        for model_id in forecast.MODEL_IDS:
            value = no_retry_component_usd[model_id] * multiplier
            per_model[model_id] = {"total_usd": str(value)}
            total += value
        return per_model, total

    planning_per_model, planning_total = derived(forecast.PLANNING_RETRY_MULTIPLIER)
    stress_per_model, stress_total = derived(Decimal(4))

    qwen_id = next(iter(forecast.PROXY_TOKENIZER_MODEL_IDS))
    qwen_input_price, qwen_output_price = price_for(qwen_id)
    qwen_byte_total = conflict_artifact["per_model_token_stats"][qwen_id][
        "utf8_byte_reservation_bound"]["total"]
    qwen_byte_component = forecast.compute_scenario_component(
        total_input_tokens=qwen_byte_total, calls=calls,
        output_tokens_per_call=output_ceilings[qwen_id],
        input_price=qwen_input_price, output_price=qwen_output_price,
    )
    qwen_byte_stress = Decimal(qwen_byte_component["total_usd"]) * Decimal(4)

    halt_cap = Decimal(str(
        protocol["materialization_requirements"]["capability_preflight"]["proposed_cap_usd"]))
    assert stress_total < halt_cap, "fixture prices must be cheap enough to clear the gate"

    artifact = deepcopy(conflict_artifact)
    artifact["schema_version"] = forecast.SCHEMA_VERSION
    artifact["artifact_id"] = forecast.ARTIFACT_ID
    artifact["status"] = forecast.STATUS
    del artifact["resolution"]
    artifact["bindings"]["price_snapshot"]["canonical_sha256"] = (
        phase2_plan.canonical_sha256(cheap_snapshot))
    artifact["scenarios"]["theoretical_minimum"]["per_model"] = theo_per_model
    artifact["scenarios"]["theoretical_minimum"]["total_usd"] = str(theo_total)
    artifact["scenarios"]["no_retry_maximum"]["per_model"] = no_retry_per_model
    artifact["scenarios"]["no_retry_maximum"]["total_usd"] = str(no_retry_total)
    artifact["scenarios"]["planning_retry_scenario"]["per_model"] = planning_per_model
    artifact["scenarios"]["planning_retry_scenario"]["total_usd"] = str(planning_total)
    artifact["scenarios"]["four_attempt_stress"]["per_model"] = stress_per_model
    artifact["scenarios"]["four_attempt_stress"]["total_usd"] = str(stress_total)
    artifact["scenarios"]["four_attempt_stress"][
        "qwen_3_7_plus_byte_bound_stress_usd"] = str(qwen_byte_stress)
    artifact["halt_cap_usd"] = str(halt_cap)
    artifact["stress_margin_usd"] = str(halt_cap - stress_total)

    # Mirror every small, tracked source `validate_source_bindings` / corpus rendering can
    # transitively read (world specs, question banks, and every top-level rejudge/*.json
    # artifact -- deliberately NOT rejudge/output/, which holds large local research data and
    # is never part of the frozen source-binding graph) into an isolated fake project root, so
    # this test never depends on enumerating that dependency graph by hand.
    fake_root = tmp_path_factory.mktemp("ready_context_root")
    for pattern in (
        "questions/*.json", "world_specs/*.txt", "rejudge/*.json",
        "rejudge/output/calibration_models.json",
    ):
        for source in ROOT.glob(pattern):
            destination = fake_root / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (fake_root / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json").write_text(
        json.dumps(cheap_snapshot, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    forecast.validate_forecast(
        artifact, root=fake_root, protocol=protocol, role_limits_v2=role_limits_v2,
        snapshot=cheap_snapshot, bundle=bundle,
    )
    return {
        "artifact": artifact, "root": fake_root, "protocol": protocol,
        "role_limits_v2": role_limits_v2, "snapshot": cheap_snapshot, "bundle": bundle,
    }


def test_ready_forecast_is_accepted_when_the_gate_genuinely_clears(ready_context):
    """The fixture itself only returns successfully if ``validate_forecast`` accepted the
    artifact it built; this test just names that acceptance path explicitly."""
    forecast.validate_forecast(
        ready_context["artifact"], root=ready_context["root"], protocol=ready_context["protocol"],
        role_limits_v2=ready_context["role_limits_v2"], snapshot=ready_context["snapshot"],
        bundle=ready_context["bundle"],
    )


# =================================================================================================
# validate_forecast: header-field mutation tests (finding: only the numeric gate and the
# conflict-shape rejection were exercised; the header fields themselves were never mutated)
# =================================================================================================


@pytest.mark.parametrize("field,value,match", [
    ("schema_version", "phase2_preflight_forecast_v0", "unsupported forecast schema_version"),
    ("artifact_id", "phase2_preflight_forecast_2020-01-01", "artifact_id drifted"),
    ("protocol_id", "not-the-real-protocol-id", "protocol_id disagrees"),
    ("status", "not_the_frozen_status", "status drifted"),
    ("execution_authorized", True, "execution_authorized must be exactly false"),
])
def test_validate_forecast_header_fields_are_pinned(ready_context, field, value, match):
    artifact = deepcopy(ready_context["artifact"])
    artifact[field] = value
    with pytest.raises(forecast.PreflightForecastError, match=match):
        forecast.validate_forecast(
            artifact, root=ready_context["root"], protocol=ready_context["protocol"],
            role_limits_v2=ready_context["role_limits_v2"], snapshot=ready_context["snapshot"],
            bundle=ready_context["bundle"],
        )


# =================================================================================================
# validate_conflict_report: header-field mutation tests (only execution_authorized was
# previously exercised; schema_version/artifact_id/protocol_id/status were not)
# =================================================================================================


@pytest.mark.parametrize("field,value,match", [
    ("schema_version", "phase2_preflight_forecast_conflict_v0",
     "unsupported conflict-report schema_version"),
    ("artifact_id", "phase2_preflight_forecast_conflict_2020-01-01", "artifact_id drifted"),
    ("protocol_id", "not-the-real-protocol-id", "protocol_id disagrees"),
    ("status", "not_the_frozen_conflict_status", "status drifted"),
])
def test_validate_conflict_report_header_fields_are_pinned(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle, field, value, match,
):
    changed = deepcopy(conflict_artifact)
    changed[field] = value
    with pytest.raises(forecast.PreflightForecastError, match=match):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


# =================================================================================================
# bindings cross-check against the caller-supplied protocol/role_limits_v2/snapshot/bundle
# objects: a distinct code path from the on-disk-file check exercised above (test_binding_*).
# =================================================================================================


def test_bindings_protocol_object_drift_from_caller_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    mutated_protocol = deepcopy(protocol)
    mutated_protocol["__test_only_marker__"] = True
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with the loaded protocol"):
        _validate_conflict(conflict_artifact, mutated_protocol, role_limits_v2, snapshot, bundle)


def test_bindings_role_limits_v2_object_drift_from_caller_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    mutated_role_limits_v2 = deepcopy(role_limits_v2)
    mutated_role_limits_v2["__test_only_marker__"] = True
    with pytest.raises(
        forecast.PreflightForecastError, match="disagrees with the loaded role-limits v2 artifact",
    ):
        _validate_conflict(conflict_artifact, protocol, mutated_role_limits_v2, snapshot, bundle)


def test_bindings_price_snapshot_object_drift_from_caller_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    mutated_snapshot = deepcopy(snapshot)
    mutated_snapshot["__test_only_marker__"] = True
    with pytest.raises(
        forecast.PreflightForecastError, match="disagrees with the loaded price snapshot",
    ):
        _validate_conflict(conflict_artifact, protocol, role_limits_v2, mutated_snapshot, bundle)


def test_bindings_prompt_bundle_object_drift_from_caller_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    mutated_bundle = deepcopy(bundle)
    mutated_bundle["__test_only_marker__"] = True
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with the loaded bundle"):
        _validate_conflict(conflict_artifact, protocol, role_limits_v2, snapshot, mutated_bundle)


def test_bindings_rendered_corpus_entry_count_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["bindings"]["rendered_corpus"]["entry_count"] = 1
    with pytest.raises(
        forecast.PreflightForecastError,
        match="bindings.rendered_corpus.entry_count disagrees with the frozen corpus size",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_per_model_classification_must_be_a_known_classification(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle, monkeypatch,
):
    """Defensive check at line 674: unreachable via a plain field mutation, since a
    classification that matches ``EXPECTED_CLASSIFICATION_BY_MODEL`` is always a member of
    ``CLASSIFICATIONS`` by construction. Isolate it directly by monkeypatching the vocabulary
    set, mirroring the isolation pattern used by
    ``test_conflict_report_rejects_a_report_that_no_longer_conflicts``."""
    monkeypatch.setattr(
        forecast, "CLASSIFICATIONS",
        frozenset(forecast.CLASSIFICATIONS - {forecast.CLASSIFICATION_EXACT}))
    with pytest.raises(
        forecast.PreflightForecastError, match="is not a known classification",
    ):
        _validate_conflict(conflict_artifact, protocol, role_limits_v2, snapshot, bundle)


# =================================================================================================
# _validate_corpus_section: field-drift checks (no test previously mutated artifact["corpus"])
# =================================================================================================


def test_corpus_question_count_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["corpus"]["question_count"] = 999
    with pytest.raises(forecast.PreflightForecastError, match="corpus.question_count disagrees"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_corpus_mirrored_replicates_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["corpus"]["mirrored_replicates"] = 3
    with pytest.raises(
        forecast.PreflightForecastError, match="mirrored_replicates must be exactly K=2",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_corpus_total_rendered_message_sets_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["corpus"]["total_rendered_message_sets"] = 999
    with pytest.raises(
        forecast.PreflightForecastError, match="total_rendered_message_sets disagrees",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_corpus_question_sources_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["corpus"]["question_sources"] = ["bogus/not_a_real_source.json"]
    with pytest.raises(forecast.PreflightForecastError, match="question_sources disagrees"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_corpus_world_spec_sources_empty_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["corpus"]["world_spec_sources"] = []
    with pytest.raises(
        forecast.PreflightForecastError, match="world_spec_sources must be non-empty",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_corpus_template_name_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["corpus"]["template_name"] = "not_capability_qa"
    with pytest.raises(forecast.PreflightForecastError, match="template_name must be"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


# =================================================================================================
# tokenizer_pins: field checks beyond the two already covered (per-prompt total drift, qwen
# proxy max-derivation)
# =================================================================================================


def test_exact_tokenizer_pin_classification_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["google/gemma-4-31B-it"]["classification"] = (
        forecast.CLASSIFICATION_PROXY)
    with pytest.raises(forecast.PreflightForecastError, match="classification must be"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_proxy_tokenizer_pin_classification_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["classification"] = (
        forecast.CLASSIFICATION_EXACT)
    with pytest.raises(forecast.PreflightForecastError, match="classification must be"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_exact_tokenizer_pin_generation_prompt_included_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["openai/gpt-oss-120b"]["generation_prompt_included"] = False
    with pytest.raises(
        forecast.PreflightForecastError,
        match="generation_prompt_included must be exactly true for a chat-template count",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_exact_tokenizer_pin_fallback_used_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["openai/gpt-oss-120b"]["fallback_used"] = True
    with pytest.raises(
        forecast.PreflightForecastError, match="fallback_used must be exactly false",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_proxy_entry_generation_prompt_included_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["proxies"][0][
        "generation_prompt_included"] = False
    with pytest.raises(
        forecast.PreflightForecastError, match="generation_prompt_included must be exactly true",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_proxy_pin_generation_prompt_included_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["generation_prompt_included"] = False
    with pytest.raises(
        forecast.PreflightForecastError, match="generation_prompt_included must be exactly true",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_proxy_pin_fallback_used_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["fallback_used"] = True
    with pytest.raises(
        forecast.PreflightForecastError, match="fallback_used must be exactly false",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_proxy_pin_requires_exactly_two_proxies(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["proxies"] = (
        changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["proxies"][:1])
    with pytest.raises(
        forecast.PreflightForecastError, match="proxies must have exactly 2 entries",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_proxy_pin_requires_two_distinct_repositories(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    proxies = changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["proxies"]
    proxies[1]["repository"] = proxies[0]["repository"]
    with pytest.raises(
        forecast.PreflightForecastError, match="must name two distinct repositories",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_proxy_convergence_identical_count_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    convergence = changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["convergence"]
    convergence["identical_count"] += 1
    with pytest.raises(
        forecast.PreflightForecastError, match="convergence.identical_count disagrees",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_proxy_convergence_divergent_count_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    convergence = changed["tokenizer_pins"]["Qwen/Qwen3.7-Plus"]["convergence"]
    convergence["divergent_count"] += 1
    with pytest.raises(
        forecast.PreflightForecastError, match="convergence.divergent_count disagrees",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_tokenizer_file_hashes_must_be_non_empty(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["openai/gpt-oss-120b"]["tokenizer_file_hashes"] = {}
    with pytest.raises(
        forecast.PreflightForecastError, match="must be a non-empty object",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_tokenizer_file_hashes_rejects_empty_filename_key(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["openai/gpt-oss-120b"]["tokenizer_file_hashes"] = {"": "0" * 64}
    with pytest.raises(
        forecast.PreflightForecastError, match="non-string/empty filename key",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_byte_bound_pin_classification_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"]["classification"] = (
        forecast.CLASSIFICATION_EXACT)
    with pytest.raises(forecast.PreflightForecastError, match="classification must be"):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_byte_bound_pin_requires_at_least_one_attempted_tokenizer(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"][
        "attempted_tokenizers"] = []
    with pytest.raises(
        forecast.PreflightForecastError, match="must record at least one attempt",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_byte_bound_pin_rejects_a_successful_load_outcome(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    """Line-630 safety guard: a byte-bound-classified model must never discard a tokenizer
    attempt that actually 'loaded' -- doing so would silently downgrade a real count to the
    looser safety bound."""
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"][
        "attempted_tokenizers"][0]["outcome"] = "loaded"
    with pytest.raises(
        forecast.PreflightForecastError,
        match="outcome is 'loaded' but the model is classified",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_byte_bound_pin_fallback_used_is_enforced(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["tokenizer_pins"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"]["fallback_used"] = False
    with pytest.raises(
        forecast.PreflightForecastError, match="fallback_used must be exactly true",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


# =================================================================================================
# scenario/output/retry-policy sub-fields whose sibling fields already had drift tests
# =================================================================================================


def test_no_retry_maximum_total_usd_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["scenarios"]["no_retry_maximum"]["total_usd"] = "999999"
    with pytest.raises(
        forecast.PreflightForecastError, match="no_retry_maximum.total_usd disagrees",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_derived_scenario_per_model_total_usd_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["scenarios"]["planning_retry_scenario"]["per_model"][
        "Qwen/Qwen2.5-7B-Instruct-Turbo"]["total_usd"] = "999999"
    with pytest.raises(
        forecast.PreflightForecastError, match="total_usd disagrees with the recomputed value",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_derived_scenario_aggregate_total_usd_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["scenarios"]["planning_retry_scenario"]["total_usd"] = "999999"
    with pytest.raises(
        forecast.PreflightForecastError, match="scenarios.planning_retry_scenario.total_usd disagrees",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_theoretical_minimum_output_tokens_per_call_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["output_token_policy"]["theoretical_minimum_output_tokens_per_call"] = 999
    with pytest.raises(
        forecast.PreflightForecastError,
        match="theoretical_minimum_output_tokens_per_call must be exactly",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_retry_policy_max_retries_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["retry_policy"]["max_retries"] = 999
    with pytest.raises(
        forecast.PreflightForecastError, match="retry_policy.max_retries disagrees with role-limits",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


# =================================================================================================
# remaining untested field checks: shared corpus-level byte-bound cross-check, generated_at_utc
# format, corpus_utf8_byte_reservation_bound_per_prompt count/value drift, and the Qwen3.7-Plus
# elementwise-max-of-two-proxies check
# =================================================================================================


def test_per_model_utf8_byte_reservation_bound_must_match_shared_corpus_stats(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    entry = changed["per_model_token_stats"]["openai/gpt-oss-120b"]
    shifted = [v + 1 for v in entry["utf8_byte_reservation_bound"]["per_prompt"]]
    entry["utf8_byte_reservation_bound"] = forecast.compute_token_stats(shifted)
    with pytest.raises(
        forecast.PreflightForecastError,
        match="disagrees with the shared corpus-level byte-reservation-bound stats",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_generated_at_utc_must_end_in_z(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["generated_at_utc"] = "2026-07-18T12:00:00"
    with pytest.raises(
        forecast.PreflightForecastError, match="generated_at_utc must be an explicit UTC timestamp",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_corpus_utf8_byte_reservation_bound_per_prompt_count_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["corpus_utf8_byte_reservation_bound_per_prompt"] = (
        changed["corpus_utf8_byte_reservation_bound_per_prompt"][:-1])
    with pytest.raises(
        forecast.PreflightForecastError,
        match="corpus_utf8_byte_reservation_bound_per_prompt must have exactly",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_corpus_utf8_byte_reservation_bound_per_prompt_value_drift_is_rejected(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    changed["corpus_utf8_byte_reservation_bound_per_prompt"][0] += 1
    with pytest.raises(
        forecast.PreflightForecastError,
        match="disagrees with the value freshly recomputed from the rendered corpus",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


def test_qwen_input_tokens_must_be_elementwise_max_of_its_two_proxies(
    conflict_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    changed = deepcopy(conflict_artifact)
    entry = changed["per_model_token_stats"]["Qwen/Qwen3.7-Plus"]["input_tokens"]
    shifted = [v + 1 for v in entry["per_prompt"]]
    changed["per_model_token_stats"]["Qwen/Qwen3.7-Plus"]["input_tokens"] = (
        forecast.compute_token_stats(shifted))
    with pytest.raises(
        forecast.PreflightForecastError,
        match="is not the elementwise max of its two recorded proxy tokenizer series",
    ):
        _validate_conflict(changed, protocol, role_limits_v2, snapshot, bundle)


# =================================================================================================
# v2 "ready" forecast: role-limits v3 + provider-refresh binding, attempt_ceiling_stress (3
# attempts), and the new utf8_reservation_envelope_3_attempts disclosure. The real, tracked
# 2026-07-19 artifact this task builds is used directly throughout (like ``conflict_artifact``
# above uses the real 2026-07-18 file) -- no synthetic cheap-price reconstruction is needed here
# because, unlike v1, the real numbers genuinely clear the gate.
# =================================================================================================


READY_ARTIFACT_V2_PATH = ROOT / "rejudge" / "phase2_preflight_forecast_2026-07-19.json"


@pytest.fixture(scope="module")
def role_limits_v3(protocol):
    artifact, _protocol, _snapshot = phase2_role_limits.load_and_validate_v3()
    return artifact


@pytest.fixture(scope="module")
def provider_refresh():
    return json.loads(forecast.DEFAULT_PROVIDER_REFRESH_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ready_v2_artifact():
    return forecast.load_and_validate_v2()


def _validate_v2(artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh):
    forecast.validate_forecast_v2(
        artifact, root=ROOT, protocol=protocol, role_limits_v3=role_limits_v3,
        snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh,
    )


def test_ready_v2_artifact_loads_and_validates(ready_v2_artifact, protocol):
    assert ready_v2_artifact["execution_authorized"] is False
    assert ready_v2_artifact["schema_version"] == forecast.SCHEMA_VERSION_V2
    assert ready_v2_artifact["artifact_id"] == forecast.ARTIFACT_ID_V2
    assert ready_v2_artifact["status"] == forecast.STATUS_V2
    assert ready_v2_artifact["protocol_id"] == protocol["protocol_id"]


def test_ready_v2_retry_policy_is_the_v3_three_attempt_pin(ready_v2_artifact):
    assert ready_v2_artifact["retry_policy"]["max_attempts"] == 3
    assert ready_v2_artifact["retry_policy"]["max_retries"] == 2


def test_ready_v2_attempt_ceiling_stress_clears_the_cap_with_positive_margin(ready_v2_artifact):
    halt_cap = Decimal(ready_v2_artifact["halt_cap_usd"])
    stress = Decimal(ready_v2_artifact["scenarios"]["attempt_ceiling_stress"]["total_usd"])
    margin = Decimal(ready_v2_artifact["stress_margin_usd"])
    assert halt_cap == Decimal("15")
    assert stress < halt_cap
    assert margin == halt_cap - stress
    assert margin > 0
    # documented expected magnitude (not re-derived here -- the arithmetic itself is fully
    # re-verified by validate_forecast_v2 above; this just pins the ballpark so a future price/
    # corpus change that silently shifts it far outside expectation is visible in a diff)
    assert Decimal("13.7") < stress < Decimal("13.8")


def test_ready_v2_reservation_envelope_matches_expected_magnitude(ready_v2_artifact):
    envelope = ready_v2_artifact["disclosures"]["utf8_reservation_envelope_3_attempts"]
    total = Decimal(envelope["total_usd"])
    assert envelope["attempts"] == 3
    assert Decimal("18.2") < total < Decimal("18.3")
    assert envelope["relationship_to_halt_cap"] == forecast.FROZEN_RESERVATION_ENVELOPE_SENTENCE


def test_ready_v2_envelope_exceeds_the_actual_stress_scenario(ready_v2_artifact):
    """The reservation envelope (every model priced at its own byte-reservation bound) must be a
    looser, larger worst case than the actual counted-token stress scenario -- otherwise it would
    not be a meaningful safety margin disclosure."""
    stress = Decimal(ready_v2_artifact["scenarios"]["attempt_ceiling_stress"]["total_usd"])
    envelope = Decimal(
        ready_v2_artifact["disclosures"]["utf8_reservation_envelope_3_attempts"]["total_usd"])
    assert envelope > stress


def test_ready_v2_supersedes_the_real_untouched_conflict_artifact(ready_v2_artifact):
    supersedes = ready_v2_artifact["supersedes"]
    assert supersedes["tracked_path"] == forecast.SUPERSEDED_ARTIFACT_TRACKED_PATH
    assert supersedes["note"] == forecast.SUPERSEDES_CONFLICT_NOTE
    real_conflict = json.loads(CONFLICT_ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert supersedes["canonical_sha256"] == phase2_plan.canonical_sha256(real_conflict)


def test_ready_v2_bindings_name_role_limits_v3_and_provider_refresh(ready_v2_artifact):
    bindings = ready_v2_artifact["bindings"]
    assert bindings["role_limits_v3"]["tracked_path"] == (
        "rejudge/phase2_role_limits_v3_2026-07-19.json")
    assert bindings["provider_refresh"]["tracked_path"] == (
        "rejudge/phase2_provider_refresh_2026-07-19.json")
    assert "role_limits_v2" not in bindings


def test_ready_v2_top_level_key_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["unexpected_extra_field"] = True
    with pytest.raises(forecast.PreflightForecastError, match="fields drifted"):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_validate_forecast_rejects_the_v2_shaped_artifact(
    ready_v2_artifact, protocol, role_limits_v2, snapshot, bundle,
):
    """The v1 'ready' validator must never accept the v2 shape (different bindings/scenario
    names/disclosures section), or vice versa (see test_validate_forecast_rejects_the_conflict_
    shaped_artifact above for the mirror-image v1 check)."""
    with pytest.raises(forecast.PreflightForecastError, match="fields drifted"):
        forecast.validate_forecast(
            ready_v2_artifact, root=ROOT, protocol=protocol, role_limits_v2=role_limits_v2,
            snapshot=snapshot, bundle=bundle,
        )


@pytest.mark.parametrize("field,value,match", [
    ("schema_version", "phase2_preflight_forecast_v0", "unsupported forecast schema_version"),
    ("artifact_id", "phase2_preflight_forecast_2020-01-01", "artifact_id drifted"),
    ("protocol_id", "not-the-real-protocol-id", "protocol_id disagrees"),
    ("status", "not_the_frozen_status", "status drifted"),
    ("execution_authorized", True, "execution_authorized must be exactly false"),
])
def test_validate_forecast_v2_header_fields_are_pinned(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
    field, value, match,
):
    changed = deepcopy(ready_v2_artifact)
    changed[field] = value
    with pytest.raises(forecast.PreflightForecastError, match=match):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_role_limits_v3_binding_hash_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    sha = changed["bindings"]["role_limits_v3"]["canonical_sha256"]
    changed["bindings"]["role_limits_v3"]["canonical_sha256"] = (
        sha[:-1] + ("0" if sha[-1] != "0" else "1"))
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with"):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_provider_refresh_binding_hash_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    sha = changed["bindings"]["provider_refresh"]["canonical_sha256"]
    changed["bindings"]["provider_refresh"]["canonical_sha256"] = (
        sha[:-1] + ("0" if sha[-1] != "0" else "1"))
    with pytest.raises(
        forecast.PreflightForecastError, match="disagrees with the loaded provider refresh",
    ):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_provider_refresh_raw_file_hash_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle,
):
    tampered_refresh = deepcopy(
        json.loads(forecast.DEFAULT_PROVIDER_REFRESH_PATH.read_text(encoding="utf-8")))
    sha = tampered_refresh["raw_response"]["file_sha256"]
    tampered_refresh["raw_response"]["file_sha256"] = (
        sha[:-1] + ("0" if sha[-1] != "0" else "1"))
    changed = deepcopy(ready_v2_artifact)
    changed["bindings"]["provider_refresh"]["canonical_sha256"] = (
        phase2_plan.canonical_sha256(tampered_refresh))
    with pytest.raises(
        forecast.PreflightForecastError,
        match="raw_response.file_sha256 disagrees with the real raw response file on disk",
    ):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, tampered_refresh)


def test_ready_v2_provider_refresh_binding_tracked_path_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["bindings"]["provider_refresh"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(forecast.PreflightForecastError, match="tracked_path must be exactly"):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_supersedes_tracked_path_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["supersedes"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(forecast.PreflightForecastError, match="supersedes.tracked_path must be exactly"):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_supersedes_hash_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    """Proves the historical conflict artifact's real hash is recomputed fresh from disk, not
    trusted from this artifact's own claim -- so a forecast could never falsely claim
    supersession of a conflict file it doesn't actually match."""
    changed = deepcopy(ready_v2_artifact)
    sha = changed["supersedes"]["canonical_sha256"]
    changed["supersedes"]["canonical_sha256"] = sha[:-1] + ("0" if sha[-1] != "0" else "1")
    with pytest.raises(
        forecast.PreflightForecastError,
        match="disagrees with the real historical conflict artifact on disk",
    ):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_supersedes_note_wording_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["supersedes"]["note"] = "some other note"
    with pytest.raises(forecast.PreflightForecastError, match="supersedes.note wording drifted"):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_disclosures_attempts_must_match_retry_policy(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["disclosures"]["utf8_reservation_envelope_3_attempts"]["attempts"] = 4
    with pytest.raises(forecast.PreflightForecastError, match="attempts disagrees"):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_disclosures_per_model_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["disclosures"]["utf8_reservation_envelope_3_attempts"]["per_model"][
        "openai/gpt-oss-120b"]["total_usd"] = "999999"
    with pytest.raises(
        forecast.PreflightForecastError, match="disagrees with the recomputed value",
    ):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_disclosures_total_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["disclosures"]["utf8_reservation_envelope_3_attempts"]["total_usd"] = "999999"
    with pytest.raises(
        forecast.PreflightForecastError,
        match="utf8_reservation_envelope_3_attempts.total_usd disagrees",
    ):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_disclosures_relationship_wording_drift_is_rejected(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["disclosures"]["utf8_reservation_envelope_3_attempts"][
        "relationship_to_halt_cap"] = "a different sentence"
    with pytest.raises(
        forecast.PreflightForecastError, match="relationship_to_halt_cap wording drifted",
    ):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_scenario_uses_attempt_ceiling_stress_not_four_attempt_stress(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v2_artifact)
    changed["scenarios"]["four_attempt_stress"] = changed["scenarios"].pop(
        "attempt_ceiling_stress")
    with pytest.raises(forecast.PreflightForecastError, match="fields drifted"):
        _validate_v2(changed, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_ready_v2_gate_fires_when_stress_does_not_clear_the_cap(
    ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh, monkeypatch,
):
    """Isolates just ``validate_forecast_v2``'s final gate direction (mirrors
    ``test_conflict_report_rejects_a_report_that_no_longer_conflicts``'s isolation pattern):
    stubs ``_validate_shared_body`` with a fixed (stress, halt_cap) pair where stress does NOT
    clear the cap, proving the hard gate is live and not merely satisfied by the real numbers'
    current margin."""
    monkeypatch.setattr(
        forecast, "_validate_shared_body",
        lambda *args, **kwargs: (Decimal("20"), Decimal("15")))
    with pytest.raises(
        forecast.PreflightForecastError, match="does not remain below halt_cap_usd",
    ):
        _validate_v2(
            ready_v2_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh)


def test_load_and_validate_v2_returns_the_real_artifact(ready_v2_artifact):
    assert ready_v2_artifact["artifact_id"] == forecast.ARTIFACT_ID_V2


def test_v2_check_cli_prints_canonical_sha(capsys):
    exit_code = forecast.main(["--check", "--v2"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "canonical_sha256=" in captured.out
    assert "execution_authorized=NO" in captured.out


# =================================================================================================
# v3 "ready" forecast (relaunch attempt r2): role-limits v4 (installed-SDK stream_options
# compatibility fix) instead of v3, identical economics, supersedes the real, tracked v2 forecast
# instead of the v1 conflict artifact. The real, tracked 2026-07-19-r2 artifact this task builds
# is used directly throughout, mirroring the v2 block's own use of the real tracked artifact.
# =================================================================================================


READY_ARTIFACT_V3_PATH = ROOT / "rejudge" / "phase2_preflight_forecast_2026-07-19-r2.json"


@pytest.fixture(scope="module")
def role_limits_v4(protocol):
    artifact, _protocol, _snapshot = phase2_role_limits.load_and_validate_v4()
    return artifact


@pytest.fixture(scope="module")
def ready_v3_artifact():
    return forecast.load_and_validate_v3()


def _validate_v3(artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh):
    forecast.validate_forecast_v3(
        artifact, root=ROOT, protocol=protocol, role_limits_v4=role_limits_v4,
        snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh,
    )


def test_ready_v3_artifact_loads_and_validates(ready_v3_artifact, protocol):
    assert ready_v3_artifact["execution_authorized"] is False
    assert ready_v3_artifact["schema_version"] == forecast.SCHEMA_VERSION_V3
    assert ready_v3_artifact["artifact_id"] == forecast.ARTIFACT_ID_V3
    assert ready_v3_artifact["status"] == forecast.STATUS_V3
    assert ready_v3_artifact["protocol_id"] == protocol["protocol_id"]


def test_ready_v3_retry_policy_is_unchanged_from_v2(ready_v3_artifact, ready_v2_artifact):
    assert ready_v3_artifact["retry_policy"]["max_attempts"] == (
        ready_v2_artifact["retry_policy"]["max_attempts"])
    assert ready_v3_artifact["retry_policy"]["max_retries"] == (
        ready_v2_artifact["retry_policy"]["max_retries"])
    assert ready_v3_artifact["retry_policy"]["max_attempts"] == 3


def test_ready_v3_numbers_unchanged_from_v2(ready_v3_artifact, ready_v2_artifact):
    """This task's whole premise: v4 only drops streaming_pinned_models's stream_options field --
    every dollar figure must be byte-identical to the real v2 (role-limits-v3-bound) artifact."""
    for scenario_name in (
        "theoretical_minimum", "no_retry_maximum", "planning_retry_scenario",
        forecast.ATTEMPT_CEILING_STRESS_SCENARIO,
    ):
        assert ready_v3_artifact["scenarios"][scenario_name]["total_usd"] == (
            ready_v2_artifact["scenarios"][scenario_name]["total_usd"])
    assert ready_v3_artifact["halt_cap_usd"] == ready_v2_artifact["halt_cap_usd"]
    assert ready_v3_artifact["stress_margin_usd"] == ready_v2_artifact["stress_margin_usd"]
    assert ready_v3_artifact["disclosures"] == ready_v2_artifact["disclosures"]
    assert ready_v3_artifact["output_token_policy"]["effective_output_ceiling_per_model"] == (
        ready_v2_artifact["output_token_policy"]["effective_output_ceiling_per_model"])


def test_ready_v3_attempt_ceiling_stress_clears_the_cap_with_positive_margin(ready_v3_artifact):
    halt_cap = Decimal(ready_v3_artifact["halt_cap_usd"])
    stress = Decimal(ready_v3_artifact["scenarios"][forecast.ATTEMPT_CEILING_STRESS_SCENARIO][
        "total_usd"])
    margin = Decimal(ready_v3_artifact["stress_margin_usd"])
    assert halt_cap == Decimal("15")
    assert stress < halt_cap
    assert margin == halt_cap - stress
    assert margin > 0


def test_ready_v3_supersedes_the_real_untouched_v2_forecast(ready_v3_artifact):
    supersedes = ready_v3_artifact["supersedes"]
    assert supersedes["tracked_path"] == forecast.SUPERSEDED_ARTIFACT_TRACKED_PATH_V3
    assert supersedes["note"] == forecast.SUPERSEDES_V2_FORECAST_NOTE
    real_v2 = json.loads(READY_ARTIFACT_V2_PATH.read_text(encoding="utf-8"))
    assert supersedes["canonical_sha256"] == phase2_plan.canonical_sha256(real_v2)


def test_ready_v3_bindings_name_role_limits_v4_and_provider_refresh(ready_v3_artifact):
    bindings = ready_v3_artifact["bindings"]
    assert bindings["role_limits_v4"]["tracked_path"] == (
        "rejudge/phase2_role_limits_v4_2026-07-19.json")
    assert bindings["provider_refresh"]["tracked_path"] == (
        "rejudge/phase2_provider_refresh_2026-07-19.json")
    assert "role_limits_v3" not in bindings


def test_ready_v3_top_level_key_drift_is_rejected(
    ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v3_artifact)
    changed["unexpected_extra_field"] = True
    with pytest.raises(forecast.PreflightForecastError, match="fields drifted"):
        _validate_v3(changed, protocol, role_limits_v4, snapshot, bundle, provider_refresh)


def test_validate_forecast_v2_rejects_the_v3_shaped_artifact(
    ready_v3_artifact, protocol, role_limits_v3, snapshot, bundle, provider_refresh,
):
    """The v2 'ready' validator must never accept the v3 shape (role_limits_v4 binding key
    instead of role_limits_v3, different artifact_id/supersedes target), or vice versa."""
    with pytest.raises(forecast.PreflightForecastError):
        forecast.validate_forecast_v2(
            ready_v3_artifact, root=ROOT, protocol=protocol, role_limits_v3=role_limits_v3,
            snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh,
        )


@pytest.mark.parametrize("field,value,match", [
    ("schema_version", "phase2_preflight_forecast_v0", "unsupported forecast schema_version"),
    ("artifact_id", "phase2_preflight_forecast_2020-01-01", "artifact_id drifted"),
    ("protocol_id", "not-the-real-protocol-id", "protocol_id disagrees"),
    ("status", "not_the_frozen_status", "status drifted"),
    ("execution_authorized", True, "execution_authorized must be exactly false"),
])
def test_validate_forecast_v3_header_fields_are_pinned(
    ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh,
    field, value, match,
):
    changed = deepcopy(ready_v3_artifact)
    changed[field] = value
    with pytest.raises(forecast.PreflightForecastError, match=match):
        _validate_v3(changed, protocol, role_limits_v4, snapshot, bundle, provider_refresh)


def test_ready_v3_role_limits_v4_binding_hash_drift_is_rejected(
    ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v3_artifact)
    sha = changed["bindings"]["role_limits_v4"]["canonical_sha256"]
    changed["bindings"]["role_limits_v4"]["canonical_sha256"] = (
        sha[:-1] + ("0" if sha[-1] != "0" else "1"))
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with"):
        _validate_v3(changed, protocol, role_limits_v4, snapshot, bundle, provider_refresh)


def test_ready_v3_supersedes_tracked_path_drift_is_rejected(
    ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v3_artifact)
    changed["supersedes"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(forecast.PreflightForecastError, match="supersedes.tracked_path must be exactly"):
        _validate_v3(changed, protocol, role_limits_v4, snapshot, bundle, provider_refresh)


def test_ready_v3_supersedes_hash_drift_is_rejected(
    ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh,
):
    """Proves the historical v2 forecast's real hash is recomputed fresh from disk, not trusted
    from this artifact's own claim -- so a forecast could never falsely claim supersession of a
    v2 file it doesn't actually match."""
    changed = deepcopy(ready_v3_artifact)
    sha = changed["supersedes"]["canonical_sha256"]
    changed["supersedes"]["canonical_sha256"] = sha[:-1] + ("0" if sha[-1] != "0" else "1")
    with pytest.raises(
        forecast.PreflightForecastError,
        match="disagrees with the real historical v2 forecast on disk",
    ):
        _validate_v3(changed, protocol, role_limits_v4, snapshot, bundle, provider_refresh)


def test_ready_v3_supersedes_note_wording_drift_is_rejected(
    ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v3_artifact)
    changed["supersedes"]["note"] = "some other note"
    with pytest.raises(forecast.PreflightForecastError, match="supersedes.note wording drifted"):
        _validate_v3(changed, protocol, role_limits_v4, snapshot, bundle, provider_refresh)


def test_ready_v3_disclosures_total_drift_is_rejected(
    ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v3_artifact)
    changed["disclosures"]["utf8_reservation_envelope_3_attempts"]["total_usd"] = "999999"
    with pytest.raises(
        forecast.PreflightForecastError,
        match="utf8_reservation_envelope_3_attempts.total_usd disagrees",
    ):
        _validate_v3(changed, protocol, role_limits_v4, snapshot, bundle, provider_refresh)


def test_ready_v3_gate_fires_when_stress_does_not_clear_the_cap(
    ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh, monkeypatch,
):
    """Isolates just ``validate_forecast_v3``'s final gate direction, mirroring the v2 block's
    own isolation test: stubs ``_validate_shared_body`` with a fixed (stress, halt_cap) pair
    where stress does NOT clear the cap."""
    monkeypatch.setattr(
        forecast, "_validate_shared_body",
        lambda *args, **kwargs: (Decimal("20"), Decimal("15")))
    with pytest.raises(
        forecast.PreflightForecastError, match="does not remain below halt_cap_usd",
    ):
        _validate_v3(
            ready_v3_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh)


def test_load_and_validate_v3_returns_the_real_artifact(ready_v3_artifact):
    assert ready_v3_artifact["artifact_id"] == forecast.ARTIFACT_ID_V3


def test_v3_check_cli_prints_canonical_sha(capsys):
    exit_code = forecast.main(["--check", "--v3"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "canonical_sha256=" in captured.out
    assert "execution_authorized=NO" in captured.out


# =================================================================================================
# v4 "ready" forecast (relaunch attempt r3): role-limits v5 (transport hardening -- SDK-vs-ledger
# retry pin split, explicit http timeout, per-call wall-clock ceiling, streaming pinned across all
# three reasoning models) instead of v4, identical economics, supersedes the real, tracked v3
# (-r2) forecast instead of the v2 forecast. The real, tracked 2026-07-19-r3 artifact this task
# builds is used directly throughout, mirroring the v3 block's own use of the real tracked
# artifact. Role-limits v5 restructures request_settings.transport (ledger_max_retries/
# ledger_max_attempts instead of the retired flat max_retries/max_attempts), so this block also
# specifically exercises _validate_retry_policy's compat reader against both the new v5 shape and
# the old v1-v4 shape, on top of every drift/gate test mirrored from the v3 block.
# =================================================================================================


READY_ARTIFACT_V3_PATH = ROOT / "rejudge" / "phase2_preflight_forecast_2026-07-19-r2.json"
READY_ARTIFACT_V4_PATH = ROOT / "rejudge" / "phase2_preflight_forecast_2026-07-19-r3.json"


@pytest.fixture(scope="module")
def role_limits_v5(protocol):
    artifact, _protocol, _snapshot = phase2_role_limits.load_and_validate_v5()
    return artifact


@pytest.fixture(scope="module")
def ready_v4_artifact():
    return forecast.load_and_validate_v4()


def _validate_v4(artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh):
    forecast.validate_forecast_v4(
        artifact, root=ROOT, protocol=protocol, role_limits_v5=role_limits_v5,
        snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh,
    )


def test_ready_v4_artifact_loads_and_validates(ready_v4_artifact, protocol):
    assert ready_v4_artifact["execution_authorized"] is False
    assert ready_v4_artifact["schema_version"] == forecast.SCHEMA_VERSION_V4
    assert ready_v4_artifact["artifact_id"] == forecast.ARTIFACT_ID_V4
    assert ready_v4_artifact["status"] == forecast.STATUS_V4
    assert ready_v4_artifact["protocol_id"] == protocol["protocol_id"]


def test_ready_v4_retry_policy_is_unchanged_from_v3(ready_v4_artifact, ready_v3_artifact):
    assert ready_v4_artifact["retry_policy"]["max_attempts"] == (
        ready_v3_artifact["retry_policy"]["max_attempts"])
    assert ready_v4_artifact["retry_policy"]["max_retries"] == (
        ready_v3_artifact["retry_policy"]["max_retries"])
    assert ready_v4_artifact["retry_policy"]["max_attempts"] == 3


def test_ready_v4_numbers_unchanged_from_v3(ready_v4_artifact, ready_v3_artifact):
    """This task's whole premise: v5 only restructures transport/streaming settings -- every
    dollar figure must be byte-identical to the real v3 (role-limits-v4-bound, -r2) artifact."""
    for scenario_name in (
        "theoretical_minimum", "no_retry_maximum", "planning_retry_scenario",
        forecast.ATTEMPT_CEILING_STRESS_SCENARIO,
    ):
        assert ready_v4_artifact["scenarios"][scenario_name]["total_usd"] == (
            ready_v3_artifact["scenarios"][scenario_name]["total_usd"])
    assert ready_v4_artifact["halt_cap_usd"] == ready_v3_artifact["halt_cap_usd"]
    assert ready_v4_artifact["stress_margin_usd"] == ready_v3_artifact["stress_margin_usd"]
    assert ready_v4_artifact["disclosures"] == ready_v3_artifact["disclosures"]
    assert ready_v4_artifact["output_token_policy"]["effective_output_ceiling_per_model"] == (
        ready_v3_artifact["output_token_policy"]["effective_output_ceiling_per_model"])
    assert ready_v4_artifact["tokenizer_pins"] == ready_v3_artifact["tokenizer_pins"]
    assert ready_v4_artifact["per_model_token_stats"] == ready_v3_artifact["per_model_token_stats"]
    assert ready_v4_artifact["corpus"] == ready_v3_artifact["corpus"]
    assert ready_v4_artifact["caveats"] == ready_v3_artifact["caveats"]


def test_ready_v4_attempt_ceiling_stress_clears_the_cap_with_positive_margin(ready_v4_artifact):
    halt_cap = Decimal(ready_v4_artifact["halt_cap_usd"])
    stress = Decimal(ready_v4_artifact["scenarios"][forecast.ATTEMPT_CEILING_STRESS_SCENARIO][
        "total_usd"])
    margin = Decimal(ready_v4_artifact["stress_margin_usd"])
    assert halt_cap == Decimal("15")
    assert stress < halt_cap
    assert margin == halt_cap - stress
    assert margin > 0


def test_ready_v4_supersedes_the_real_untouched_v3_forecast(ready_v4_artifact):
    supersedes = ready_v4_artifact["supersedes"]
    assert supersedes["tracked_path"] == forecast.SUPERSEDED_ARTIFACT_TRACKED_PATH_V4
    assert supersedes["note"] == forecast.SUPERSEDES_V3_FORECAST_NOTE
    real_v3 = json.loads(READY_ARTIFACT_V3_PATH.read_text(encoding="utf-8"))
    assert supersedes["canonical_sha256"] == phase2_plan.canonical_sha256(real_v3)


def test_ready_v4_bindings_name_role_limits_v5_and_provider_refresh(ready_v4_artifact):
    bindings = ready_v4_artifact["bindings"]
    assert bindings["role_limits_v5"]["tracked_path"] == (
        "rejudge/phase2_role_limits_v5_2026-07-19.json")
    assert bindings["provider_refresh"]["tracked_path"] == (
        "rejudge/phase2_provider_refresh_2026-07-19.json")
    assert "role_limits_v4" not in bindings


def test_ready_v4_top_level_key_drift_is_rejected(
    ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v4_artifact)
    changed["unexpected_extra_field"] = True
    with pytest.raises(forecast.PreflightForecastError, match="fields drifted"):
        _validate_v4(changed, protocol, role_limits_v5, snapshot, bundle, provider_refresh)


def test_validate_forecast_v3_rejects_the_v4_shaped_artifact(
    ready_v4_artifact, protocol, role_limits_v4, snapshot, bundle, provider_refresh,
):
    """The v3 'ready' validator must never accept the v4 shape (role_limits_v5 binding key
    instead of role_limits_v4, different artifact_id/supersedes target), or vice versa."""
    with pytest.raises(forecast.PreflightForecastError):
        forecast.validate_forecast_v3(
            ready_v4_artifact, root=ROOT, protocol=protocol, role_limits_v4=role_limits_v4,
            snapshot=snapshot, bundle=bundle, provider_refresh=provider_refresh,
        )


@pytest.mark.parametrize("field,value,match", [
    ("schema_version", "phase2_preflight_forecast_v0", "unsupported forecast schema_version"),
    ("artifact_id", "phase2_preflight_forecast_2020-01-01", "artifact_id drifted"),
    ("protocol_id", "not-the-real-protocol-id", "protocol_id disagrees"),
    ("status", "not_the_frozen_status", "status drifted"),
    ("execution_authorized", True, "execution_authorized must be exactly false"),
])
def test_validate_forecast_v4_header_fields_are_pinned(
    ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh,
    field, value, match,
):
    changed = deepcopy(ready_v4_artifact)
    changed[field] = value
    with pytest.raises(forecast.PreflightForecastError, match=match):
        _validate_v4(changed, protocol, role_limits_v5, snapshot, bundle, provider_refresh)


def test_ready_v4_role_limits_v5_binding_hash_drift_is_rejected(
    ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v4_artifact)
    sha = changed["bindings"]["role_limits_v5"]["canonical_sha256"]
    changed["bindings"]["role_limits_v5"]["canonical_sha256"] = (
        sha[:-1] + ("0" if sha[-1] != "0" else "1"))
    with pytest.raises(forecast.PreflightForecastError, match="disagrees with"):
        _validate_v4(changed, protocol, role_limits_v5, snapshot, bundle, provider_refresh)


def test_ready_v4_supersedes_tracked_path_drift_is_rejected(
    ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v4_artifact)
    changed["supersedes"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(forecast.PreflightForecastError, match="supersedes.tracked_path must be exactly"):
        _validate_v4(changed, protocol, role_limits_v5, snapshot, bundle, provider_refresh)


def test_ready_v4_supersedes_hash_drift_is_rejected(
    ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh,
):
    """Proves the historical v3 forecast's real hash is recomputed fresh from disk, not trusted
    from this artifact's own claim -- so a forecast could never falsely claim supersession of a
    v3 file it doesn't actually match."""
    changed = deepcopy(ready_v4_artifact)
    sha = changed["supersedes"]["canonical_sha256"]
    changed["supersedes"]["canonical_sha256"] = sha[:-1] + ("0" if sha[-1] != "0" else "1")
    with pytest.raises(
        forecast.PreflightForecastError,
        match="disagrees with the real historical v3 forecast on disk",
    ):
        _validate_v4(changed, protocol, role_limits_v5, snapshot, bundle, provider_refresh)


def test_ready_v4_supersedes_note_wording_drift_is_rejected(
    ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v4_artifact)
    changed["supersedes"]["note"] = "some other note"
    with pytest.raises(forecast.PreflightForecastError, match="supersedes.note wording drifted"):
        _validate_v4(changed, protocol, role_limits_v5, snapshot, bundle, provider_refresh)


def test_ready_v4_disclosures_total_drift_is_rejected(
    ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh,
):
    changed = deepcopy(ready_v4_artifact)
    changed["disclosures"]["utf8_reservation_envelope_3_attempts"]["total_usd"] = "999999"
    with pytest.raises(
        forecast.PreflightForecastError,
        match="utf8_reservation_envelope_3_attempts.total_usd disagrees",
    ):
        _validate_v4(changed, protocol, role_limits_v5, snapshot, bundle, provider_refresh)


def test_ready_v4_gate_fires_when_stress_does_not_clear_the_cap(
    ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh, monkeypatch,
):
    """Isolates just ``validate_forecast_v4``'s final gate direction, mirroring the v3 block's
    own isolation test: stubs ``_validate_shared_body`` with a fixed (stress, halt_cap) pair
    where stress does NOT clear the cap."""
    monkeypatch.setattr(
        forecast, "_validate_shared_body",
        lambda *args, **kwargs: (Decimal("20"), Decimal("15")))
    with pytest.raises(
        forecast.PreflightForecastError, match="does not remain below halt_cap_usd",
    ):
        _validate_v4(
            ready_v4_artifact, protocol, role_limits_v5, snapshot, bundle, provider_refresh)


def test_load_and_validate_v4_returns_the_real_artifact(ready_v4_artifact):
    assert ready_v4_artifact["artifact_id"] == forecast.ARTIFACT_ID_V4


def test_v4_check_cli_prints_canonical_sha(capsys):
    exit_code = forecast.main(["--check", "--v4"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "canonical_sha256=" in captured.out
    assert "execution_authorized=NO" in captured.out


# --- retry-policy compat reader: v5's restructured transport shape vs. the retired v1-v4 flat ---
# --- {max_retries, max_attempts} shape -- both must resolve to the same ledger-retry semantics --


def test_validate_retry_policy_accepts_the_v5_restructured_transport_shape():
    role_limits_v5_shaped = {
        "request_settings": {
            "transport": {
                "sdk_internal_max_retries": 0,
                "ledger_max_retries": 2,
                "ledger_max_attempts": 3,
                "http_timeout": {"connect": 10, "read": 600, "write": 60, "pool": 60},
                "per_call_wall_clock_ceiling_seconds": 1200,
            },
        },
    }
    retry_policy = {"max_retries": 2, "max_attempts": 3, "source": "some frozen source string"}
    max_attempts = forecast._validate_retry_policy(retry_policy, role_limits_v5_shaped)
    assert max_attempts == 3


def test_validate_retry_policy_still_accepts_the_v1_v4_flat_transport_shape():
    role_limits_v4_shaped = {
        "request_settings": {
            "transport": {"max_retries": 2, "max_attempts": 3},
        },
    }
    retry_policy = {"max_retries": 2, "max_attempts": 3, "source": "some frozen source string"}
    max_attempts = forecast._validate_retry_policy(retry_policy, role_limits_v4_shaped)
    assert max_attempts == 3


def test_validate_retry_policy_rejects_max_retries_drift_against_v5_shape():
    role_limits_v5_shaped = {
        "request_settings": {
            "transport": {
                "sdk_internal_max_retries": 0,
                "ledger_max_retries": 2,
                "ledger_max_attempts": 3,
                "http_timeout": {"connect": 10, "read": 600, "write": 60, "pool": 60},
                "per_call_wall_clock_ceiling_seconds": 1200,
            },
        },
    }
    retry_policy = {"max_retries": 4, "max_attempts": 5, "source": "some frozen source string"}
    with pytest.raises(
        forecast.PreflightForecastError, match="retry_policy.max_retries disagrees",
    ):
        forecast._validate_retry_policy(retry_policy, role_limits_v5_shaped)


def test_ready_v4_uses_role_limits_v5_ledger_retry_pin_via_the_real_artifact(
    ready_v4_artifact, role_limits_v5,
):
    """End-to-end confirmation (not a hand-built fixture) that the real, tracked v4 artifact's
    retry_policy really was resolved from role-limits v5's RESTRUCTURED transport section (via
    ``resolve_transport_ledger_max_retries``), not from a stale flat-shape read."""
    transport = role_limits_v5["request_settings"]["transport"]
    assert "max_retries" not in transport
    assert "max_attempts" not in transport
    assert transport["ledger_max_retries"] == 2
    assert transport["ledger_max_attempts"] == 3
    assert ready_v4_artifact["retry_policy"]["max_retries"] == transport["ledger_max_retries"]
    assert ready_v4_artifact["retry_policy"]["max_attempts"] == transport["ledger_max_attempts"]
