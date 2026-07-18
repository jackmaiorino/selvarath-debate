from copy import deepcopy
from pathlib import Path

import pytest

from rejudge import phase2_plan, phase2_role_limits as rl


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "rejudge" / "phase2_role_limits_2026-07-18.json"
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"
SNAPSHOT_PATH = ROOT / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json"


def _artifacts():
    return rl.load_and_validate(ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH)


def test_tracked_artifact_validates():
    artifact, protocol, snapshot = _artifacts()
    assert artifact["execution_authorized"] is False
    assert artifact["protocol_id"] == protocol["protocol_id"]
    assert set(artifact["model_role_limits"]) == set(snapshot["models"])


def test_reasoning_model_set_is_frozen_and_exact():
    assert rl.REASONING_MODEL_IDS == (
        "google/gemma-4-31B-it", "openai/gpt-oss-120b", "Qwen/Qwen3.7-Plus")
    assert rl.REASONING_FLOOR_MAX_TOKENS == 4096
    # No prefix inference: a same-family sibling model must not be swept in.
    assert "Qwen/Qwen3.5-9B" not in rl.REASONING_MODEL_ID_SET
    assert "google/gemma-4-9B-it" not in rl.REASONING_MODEL_ID_SET


def test_base_role_limits_are_frozen():
    assert rl.BASE_ROLE_MAX_TOKENS == {
        "debater_turn": 512, "judge_query": 256, "oracle": 32, "judge_verdict": 512,
        "batch_verdict": 512, "query_checker": 16, "capability_qa": 32,
    }


def test_applicable_pairs_are_not_a_full_cartesian_matrix():
    artifact, _protocol, _snapshot = _artifacts()
    pairs = sum(len(roles) for roles in artifact["model_role_limits"].values())
    full_matrix = len(artifact["model_role_limits"]) * len(rl.BASE_ROLE_MAX_TOKENS)
    assert pairs == 24
    assert pairs < full_matrix


def test_effective_max_tokens_matches_frozen_floor_policy():
    assert rl.effective_max_tokens("meta-llama/Llama-3.3-70B-Instruct-Turbo", 256) == 256
    assert rl.effective_max_tokens("openai/gpt-oss-120b", 256) == 4096
    assert rl.effective_max_tokens("openai/gpt-oss-120b", 8192) == 8192


def test_base_role_max_tokens_drift_is_rejected():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["base_role_max_tokens"]["oracle"] = 64
    with pytest.raises(rl.RoleLimitsError, match="base_role_max_tokens.oracle"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_reasoning_model_set_drift_is_rejected():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["reasoning_models"]["model_ids"].append("Qwen/Qwen3.5-9B")
    with pytest.raises(rl.RoleLimitsError, match="frozen three-model set"):
        rl.validate_role_limits(changed, protocol, snapshot)

    changed = deepcopy(artifact)
    changed["reasoning_models"]["model_ids"] = list(reversed(
        changed["reasoning_models"]["model_ids"]))
    with pytest.raises(rl.RoleLimitsError, match="frozen three-model set"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_reasoning_floor_drift_is_rejected():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["reasoning_models"]["floor_max_tokens"] = 2048
    with pytest.raises(rl.RoleLimitsError, match="floor_max_tokens"):
        rl.validate_role_limits(changed, protocol, snapshot)


@pytest.mark.parametrize("mutation", ["missing_model", "extra_role", "wrong_role_name"])
def test_model_role_limits_key_drift_is_rejected(mutation):
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    if mutation == "missing_model":
        del changed["model_role_limits"]["Qwen/Qwen3.7-Plus"]
    elif mutation == "extra_role":
        changed["model_role_limits"]["Qwen/Qwen3.7-Plus"]["oracle"] = {
            "base_role_max_tokens": 32, "effective_request_max_tokens": 4096}
    else:
        changed["model_role_limits"]["Qwen/Qwen2.5-7B-Instruct-Turbo"]["debater_turn"] = (
            changed["model_role_limits"]["Qwen/Qwen2.5-7B-Instruct-Turbo"].pop("judge_query"))
    with pytest.raises(rl.RoleLimitsError):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_effective_value_must_be_exactly_base_or_exactly_floor():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["model_role_limits"]["openai/gpt-oss-120b"]["judge_query"][
        "effective_request_max_tokens"] = 3000
    with pytest.raises(rl.RoleLimitsError, match="effective_request_max_tokens"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_non_reasoning_model_cannot_claim_the_reasoning_floor():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["model_role_limits"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"]["oracle"][
        "effective_request_max_tokens"] = 4096
    with pytest.raises(rl.RoleLimitsError, match="effective_request_max_tokens"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_model_role_limits_roster_must_match_protocol_roster():
    artifact, protocol, snapshot = _artifacts()
    changed_protocol = deepcopy(protocol)
    changed_protocol["roster"]["debaters"] = ["meta-llama/Llama-3.3-70B-Instruct-Turbo"]
    with pytest.raises(rl.RoleLimitsError, match="roster"):
        rl.validate_role_limits(artifact, changed_protocol, snapshot)


def test_base_role_max_tokens_drift_is_rejected_per_model_role_pair():
    # Distinct from test_base_role_max_tokens_drift_is_rejected, which only mutates the
    # top-level canonical base_role_max_tokens value: this exercises the separate recorded
    # base_role_max_tokens carried on every individual (model, role) pair in
    # model_role_limits, verified independently at phase2_role_limits.py's per-pair check.
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["model_role_limits"]["openai/gpt-oss-120b"]["judge_query"][
        "base_role_max_tokens"] = 999
    with pytest.raises(
        rl.RoleLimitsError,
        match=r"model_role_limits\.openai/gpt-oss-120b\.judge_query\.base_role_max_tokens",
    ):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_roster_model_absent_from_model_registry_is_rejected():
    # A live protocol-consistency guard distinct from the FROZEN-roster drift guard below:
    # the roster references a model that its own model_registry no longer defines, with the
    # artifact's model_role_limits section left untouched.
    artifact, protocol, snapshot = _artifacts()
    changed_protocol = deepcopy(protocol)
    del changed_protocol["model_registry"]["google/gemma-4-31B-it"]
    with pytest.raises(rl.RoleLimitsError, match="model_registry"):
        rl.validate_role_limits(artifact, changed_protocol, snapshot)


def test_protocol_roster_growing_beyond_frozen_model_set_is_rejected():
    # If the protocol's roster grows to include a model outside the hardcoded
    # MODEL_ROLE_SETS/FROZEN_ROLE_LIMIT_MODEL_IDS set, this must be rejected even before the
    # artifact's own model_role_limits section is examined -- phase2_role_limits.py itself
    # must be updated (a new MODEL_ROLE_SETS entry) before such a protocol can validate.
    artifact, protocol, snapshot = _artifacts()
    changed_protocol = deepcopy(protocol)
    changed_protocol["model_registry"]["new/unfrozen-model"] = {
        "display_name": "Unfrozen", "price_usd_per_million_tokens": {"input": 1.0, "output": 1.0}}
    changed_protocol["roster"]["debaters"].append("new/unfrozen-model")
    with pytest.raises(rl.RoleLimitsError, match="no longer matches the hardcoded"):
        rl.validate_role_limits(artifact, changed_protocol, snapshot)


def test_streaming_pinned_model_absent_from_registry_is_rejected_by_request_settings():
    # request_settings is validated before model_role_limits precisely so that dropping one
    # of its two pinned models from the live registry is caught by its own specific message,
    # not always preempted by the broader model_role_limits roster/registry check.
    artifact, protocol, snapshot = _artifacts()
    changed_protocol = deepcopy(protocol)
    del changed_protocol["model_registry"]["Qwen/Qwen3.7-Plus"]
    with pytest.raises(
        rl.RoleLimitsError, match="streaming_pinned_models names a model outside"
    ):
        rl.validate_role_limits(artifact, changed_protocol, snapshot)


def test_per_model_extra_fields_model_absent_from_registry_is_rejected_by_request_settings():
    artifact, protocol, snapshot = _artifacts()
    changed_protocol = deepcopy(protocol)
    del changed_protocol["model_registry"]["openai/gpt-oss-120b"]
    with pytest.raises(
        rl.RoleLimitsError, match="per_model_extra_fields names a model outside"
    ):
        rl.validate_role_limits(artifact, changed_protocol, snapshot)


def test_context_ceilings_must_be_byte_equal_to_the_snapshot():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["context_ceilings"]["Qwen/Qwen3.7-Plus"]["context_length_tokens"] = 999999
    with pytest.raises(rl.RoleLimitsError, match="price snapshot"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_context_ceiling_source_and_note_cannot_silently_drift():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["context_ceilings"]["Qwen/Qwen3.7-Plus"]["source"] = "somewhere/else.json"
    with pytest.raises(rl.RoleLimitsError, match="source"):
        rl.validate_role_limits(changed, protocol, snapshot)

    changed = deepcopy(artifact)
    changed["context_ceilings"]["Qwen/Qwen3.7-Plus"]["note"] = "different wording"
    with pytest.raises(rl.RoleLimitsError, match="note"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_request_settings_base_fields_are_frozen():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["base_fields"].append("top_p")
    with pytest.raises(rl.RoleLimitsError, match="base_fields"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_streaming_pin_is_from_first_attempt_not_reactive():
    artifact, protocol, snapshot = _artifacts()
    entry = artifact["request_settings"]["streaming_pinned_models"]["Qwen/Qwen3.7-Plus"]
    assert entry == {"stream": True, "stream_options": {"include_usage": True}}

    changed = deepcopy(artifact)
    changed["request_settings"]["streaming_pinned_models"]["Qwen/Qwen3.7-Plus"]["stream"] = False
    with pytest.raises(rl.RoleLimitsError, match="streaming_pinned_models"):
        rl.validate_role_limits(changed, protocol, snapshot)

    changed = deepcopy(artifact)
    del changed["request_settings"]["streaming_pinned_models"]["Qwen/Qwen3.7-Plus"]
    with pytest.raises(rl.RoleLimitsError, match="streaming_pinned_models"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_per_model_extra_fields_pin_reasoning_effort_for_gpt_oss():
    artifact, protocol, snapshot = _artifacts()
    assert artifact["request_settings"]["per_model_extra_fields"] == {
        "openai/gpt-oss-120b": {"reasoning_effort": "medium"}}
    changed = deepcopy(artifact)
    changed["request_settings"]["per_model_extra_fields"]["openai/gpt-oss-120b"][
        "reasoning_effort"] = "high"
    with pytest.raises(rl.RoleLimitsError, match="per_model_extra_fields"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_reasoning_control_note_records_deliberate_omission():
    artifact, _protocol, _snapshot = _artifacts()
    note = artifact["request_settings"]["reasoning_control_note"]
    assert "DELIBERATELY OMITTED" in note
    assert "google/gemma-4-31B-it" in note and "Qwen/Qwen3.7-Plus" in note
    assert "unverified" in note


def test_transport_retry_pin_is_three_at_most_four_attempts():
    artifact, protocol, snapshot = _artifacts()
    assert artifact["request_settings"]["transport"] == {"max_retries": 3, "max_attempts": 4}
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["max_retries"] = 5
    with pytest.raises(rl.RoleLimitsError, match="max_retries"):
        rl.validate_role_limits(changed, protocol, snapshot)

    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["max_attempts"] = 10
    with pytest.raises(rl.RoleLimitsError, match="max_attempts"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_response_metadata_to_persist_is_frozen():
    artifact, protocol, snapshot = _artifacts()
    assert artifact["request_settings"]["response_metadata_to_persist"] == [
        "request_fields_sha256", "returned_model_id", "response_id", "finish_reason",
        "system_fingerprint_if_present", "prompt_tokens", "completion_tokens",
        "reasoning_tokens_if_returned",
    ]
    changed = deepcopy(artifact)
    changed["request_settings"]["response_metadata_to_persist"].pop()
    with pytest.raises(rl.RoleLimitsError, match="response_metadata_to_persist"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_execution_authorized_cannot_be_flipped_true():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["execution_authorized"] = True
    with pytest.raises(rl.RoleLimitsError, match="execution_authorized"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_top_level_key_drift_is_rejected():
    artifact, protocol, snapshot = _artifacts()
    changed = deepcopy(artifact)
    changed["unexpected_field"] = True
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits(changed, protocol, snapshot)

    changed = deepcopy(artifact)
    del changed["status"]
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits(changed, protocol, snapshot)


def test_duplicate_json_keys_are_rejected(tmp_path):
    raw = ARTIFACT_PATH.read_text(encoding="utf-8")
    corrupted = raw.replace(
        '"schema_version": "phase2_role_limits_v1",',
        '"schema_version": "phase2_role_limits_v1", "schema_version": "dup",',
        1,
    )
    path = tmp_path / "role_limits_dup.json"
    path.write_text(corrupted, encoding="utf-8")
    with pytest.raises(rl.RoleLimitsError, match="duplicate JSON key"):
        rl.load_and_validate(path, PROTOCOL_PATH, SNAPSHOT_PATH)


def test_non_finite_json_literals_are_rejected(tmp_path):
    path = tmp_path / "role_limits_nan.json"
    path.write_text('{"schema_version": NaN}', encoding="utf-8")
    with pytest.raises(rl.RoleLimitsError, match="non-finite literal"):
        rl.load_and_validate(path, PROTOCOL_PATH, SNAPSHOT_PATH)


def test_artifact_never_grants_execution_authority():
    artifact, _protocol, _snapshot = _artifacts()
    assert artifact["execution_authorized"] is False
    assert artifact["status"] != "authorized"


def test_cli_check_is_offline_and_prints_canonical_hash(capsys):
    assert rl.main([
        "--check", "--artifact", str(ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH),
    ]) == 0
    output = capsys.readouterr().out
    artifact, _protocol, _snapshot = _artifacts()
    assert phase2_plan.canonical_sha256(artifact) in output
    assert "execution_authorized=NO" in output


def test_cli_requires_check_flag():
    with pytest.raises(SystemExit):
        rl.main(["--artifact", str(ARTIFACT_PATH)])
