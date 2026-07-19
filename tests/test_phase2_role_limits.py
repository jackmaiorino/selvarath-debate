import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from rejudge import phase2_plan, phase2_role_limits as rl


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "rejudge" / "phase2_role_limits_2026-07-18.json"
V2_ARTIFACT_PATH = ROOT / "rejudge" / "phase2_role_limits_v2_2026-07-18.json"
V3_ARTIFACT_PATH = ROOT / "rejudge" / "phase2_role_limits_v3_2026-07-19.json"
V4_ARTIFACT_PATH = ROOT / "rejudge" / "phase2_role_limits_v4_2026-07-19.json"
V5_ARTIFACT_PATH = ROOT / "rejudge" / "phase2_role_limits_v5_2026-07-19.json"
DELEGATION_PATH = ROOT / "rejudge" / "phase2_preflight_delegation_2026-07-19.json"
ABORT_CLOSURE_PATH = ROOT / "rejudge" / "phase2_preflight_abort_closure_2026-07-19.json"
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"
SNAPSHOT_PATH = ROOT / "rejudge" / "phase2_provider_price_snapshot_2026-07-18.json"


def _artifacts():
    return rl.load_and_validate(ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH)


def _v2_artifacts():
    return rl.load_and_validate_v2(V2_ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH, ARTIFACT_PATH)


def _v3_artifacts():
    return rl.load_and_validate_v3(
        V3_ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH, V2_ARTIFACT_PATH, ROOT)


def _v4_artifacts():
    return rl.load_and_validate_v4(
        V4_ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH, V3_ARTIFACT_PATH, ROOT)


def _v5_artifacts():
    return rl.load_and_validate_v5(
        V5_ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH, V4_ARTIFACT_PATH, ROOT)


def _flip_hex_digest(value: str) -> str:
    """Return a still-well-formed 64-hex digest that differs from *value*."""
    last = value[-1]
    replacement = "0" if last != "0" else "1"
    return value[:-1] + replacement


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


# =================================================================================================
# v2: role_taxonomy + supersedes
# =================================================================================================


def test_v2_tracked_artifact_validates():
    artifact, protocol, snapshot = _v2_artifacts()
    assert artifact["schema_version"] == "phase2_role_limits_v2"
    assert artifact["execution_authorized"] is False
    assert artifact["protocol_id"] == protocol["protocol_id"]
    assert set(artifact["model_role_limits"]) == set(snapshot["models"])


def test_v2_reproduces_every_v1_value_byte_semantically():
    v1_artifact, _protocol, _snapshot = _artifacts()
    v2_artifact, _protocol2, _snapshot2 = _v2_artifacts()
    for key in (
        "base_role_max_tokens", "reasoning_models", "model_role_limits", "context_ceilings",
        "request_settings",
    ):
        assert v2_artifact[key] == v1_artifact[key], f"{key} diverged from v1"


def test_v2_supersedes_binds_the_real_v1_artifact():
    artifact, _protocol, _snapshot = _v2_artifacts()
    v1_artifact, _protocol2, _snapshot2 = _artifacts()
    supersedes = artifact["supersedes"]
    assert supersedes["tracked_path"] == "rejudge/phase2_role_limits_2026-07-18.json"
    assert supersedes["canonical_sha256"] == phase2_plan.canonical_sha256(v1_artifact)


def test_v2_role_taxonomy_is_frozen_and_exact():
    artifact, _protocol, _snapshot = _v2_artifacts()
    assert artifact["role_taxonomy"] == {
        "debater_turn": "debater",
        "judge_query": "judge_query",
        "oracle": "oracle",
        "judge_verdict": "judge_verdict",
        "batch_verdict": "judge_verdict",
        "query_checker": "query_checker",
        "capability_qa": "capability_qa",
    }
    assert rl.ROLE_TAXONOMY == artifact["role_taxonomy"]


def test_v2_top_level_key_drift_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["unexpected_field"] = True
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)

    changed = deepcopy(artifact)
    del changed["role_taxonomy"]
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_schema_version_drift_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["schema_version"] = "phase2_role_limits_v1"
    with pytest.raises(rl.RoleLimitsError, match="schema_version"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_execution_authorized_cannot_be_flipped_true():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["execution_authorized"] = True
    with pytest.raises(rl.RoleLimitsError, match="execution_authorized"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_artifact_id_value_drift_is_rejected():
    # Distinct from the exact-keys check: the key is present, but its value is wrong (e.g.
    # swapped for the v1 artifact_id), which only the dedicated equality check catches.
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["artifact_id"] = rl.ARTIFACT_ID
    with pytest.raises(rl.RoleLimitsError, match="v2 role-limits artifact_id drifted"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_protocol_id_value_drift_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["protocol_id"] = "not_the_real_frozen_protocol_id"
    with pytest.raises(
        rl.RoleLimitsError,
        match="v2 role-limits protocol_id disagrees with the frozen protocol",
    ):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_status_value_drift_is_rejected():
    # Distinct from deleting "status" (which trips the generic exact-keys "fields drifted"
    # check): this reassigns it to another plausible-looking string, which only the
    # dedicated status equality check catches.
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["status"] = "authorized"
    with pytest.raises(rl.RoleLimitsError, match="v2 role-limits status drifted"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_reuses_v1_section_checks():
    # Regression: v2 must reuse the exact same per-section checks as v1, not a parallel
    # re-implementation that could silently diverge. Mutating a shared section (e.g. the
    # reasoning floor) must fail with the same message family as the v1 test does.
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["reasoning_models"]["floor_max_tokens"] = 2048
    with pytest.raises(rl.RoleLimitsError, match="floor_max_tokens"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)

    changed = deepcopy(artifact)
    changed["base_role_max_tokens"]["oracle"] = 64
    with pytest.raises(rl.RoleLimitsError, match="base_role_max_tokens.oracle"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


# --- supersedes drift ----------------------------------------------------------------------------


def test_v2_supersedes_wrong_tracked_path_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(rl.RoleLimitsError, match="supersedes.tracked_path"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_supersedes_wrong_sha_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["canonical_sha256"] = _flip_hex_digest(
        changed["supersedes"]["canonical_sha256"])
    with pytest.raises(rl.RoleLimitsError, match="supersedes.canonical_sha256"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


@pytest.mark.parametrize("bad_sha", ["short", "a" * 63, "a" * 65, 12345, None, ["a" * 64]])
def test_v2_supersedes_malformed_sha_format_is_rejected(bad_sha):
    # Distinct from test_v2_supersedes_wrong_sha_is_rejected, which keeps the digest
    # well-formed (still 64 hex chars) and only exercises the later value-mismatch branch:
    # this exercises the earlier format guard (_sha256_hex) on a value that is not even a
    # well-formed 64-character string.
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["canonical_sha256"] = bad_sha
    with pytest.raises(rl.RoleLimitsError, match="must be a SHA-256 hex digest"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_supersedes_drifts_when_the_real_v1_file_on_disk_changes():
    # The supersedes hash is recomputed fresh from v1_artifact, never trusted from the v2
    # artifact's own declared value alone: if the real v1 content differs from what v2 claims
    # to supersede (e.g. the v1 file was hand-edited after v2 was frozen), validation fails.
    artifact, protocol, snapshot = _v2_artifacts()
    tampered_v1 = deepcopy(rl._load_json(ARTIFACT_PATH))
    tampered_v1["base_role_max_tokens"]["oracle"] = 999
    with pytest.raises(rl.RoleLimitsError, match="supersedes.canonical_sha256"):
        rl.validate_role_limits_v2(artifact, protocol, snapshot, tampered_v1)


def test_v2_supersedes_key_set_drift_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["extra"] = "x"
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_load_and_validate_v2_fails_closed_on_missing_v1_file(tmp_path):
    missing_v1 = tmp_path / "does_not_exist.json"
    with pytest.raises(rl.RoleLimitsError, match="could not read"):
        rl.load_and_validate_v2(V2_ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH, missing_v1)


# --- role_taxonomy drift --------------------------------------------------------------------------


def test_v2_role_taxonomy_missing_role_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    del changed["role_taxonomy"]["oracle"]
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_role_taxonomy_extra_role_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["role_taxonomy"]["unexpected_role"] = "oracle"
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


@pytest.mark.parametrize("bad_target", [None, 123, "", [], {}])
def test_v2_role_taxonomy_non_string_target_is_rejected(bad_target):
    # Distinct from the unknown-target and many-to-one checks below, which all reassign a
    # role to another *valid string*: this exercises the earlier type/emptiness guard on a
    # target that is not a non-empty string at all.
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["role_taxonomy"]["oracle"] = bad_target
    with pytest.raises(
        rl.RoleLimitsError, match=r"role_taxonomy\.oracle must be a non-empty string",
    ):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_role_taxonomy_unknown_target_is_rejected():
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["role_taxonomy"]["oracle"] = "not_a_real_protocol_call_role"
    with pytest.raises(rl.RoleLimitsError, match="not a known protocol call role"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_role_taxonomy_illegal_many_to_one_is_rejected():
    # oracle -> judge_query illegally shares a target with judge_query -> judge_query; only
    # judge_verdict and batch_verdict are allowed to share a target.
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["role_taxonomy"]["oracle"] = "judge_query"
    with pytest.raises(rl.RoleLimitsError, match="illegal many-to-one"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_role_taxonomy_reassignment_to_a_valid_target_still_rejected():
    # Swapping judge_query's and query_checker's targets keeps every target valid and
    # non-colliding (no many-to-one is created), so this mutation passes every generic
    # constraint check; only the final exact-mapping equality check catches this kind of
    # silent reassignment.
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    changed = deepcopy(artifact)
    changed["role_taxonomy"]["judge_query"] = "query_checker"
    changed["role_taxonomy"]["query_checker"] = "judge_query"
    with pytest.raises(rl.RoleLimitsError, match="frozen taxonomy mapping"):
        rl.validate_role_limits_v2(changed, protocol, snapshot, v1_artifact)


def test_v2_role_taxonomy_judge_verdict_batch_verdict_sharing_is_allowed():
    # Sanity: the one legitimate many-to-one pair does NOT raise on its own.
    artifact, protocol, snapshot = _v2_artifacts()
    v1_artifact, _p, _s = _artifacts()
    rl.validate_role_limits_v2(deepcopy(artifact), protocol, snapshot, v1_artifact)  # no raise


# =================================================================================================
# resolve_request_parameters
# =================================================================================================


def test_resolve_request_parameters_gemma_judge_query():
    artifact, protocol, _snapshot = _v2_artifacts()
    result = rl.resolve_request_parameters(
        artifact, protocol, "google/gemma-4-31B-it", "judge_query")
    assert result.effective_max_tokens == 4096
    assert result.temperature == pytest.approx(0.3)
    assert result.protocol_role == "judge_query"


def test_resolve_request_parameters_llama_oracle():
    artifact, protocol, _snapshot = _v2_artifacts()
    result = rl.resolve_request_parameters(
        artifact, protocol, "meta-llama/Llama-3.3-70B-Instruct-Turbo", "oracle")
    assert result.effective_max_tokens == 32
    assert result.temperature == pytest.approx(0.0)
    assert result.protocol_role == "oracle"


def test_resolve_request_parameters_qwen_plus_capability_qa():
    artifact, protocol, _snapshot = _v2_artifacts()
    result = rl.resolve_request_parameters(
        artifact, protocol, "Qwen/Qwen3.7-Plus", "capability_qa")
    assert result.effective_max_tokens == 4096
    assert result.temperature == pytest.approx(0.0)
    assert result.protocol_role == "capability_qa"


def test_resolve_request_parameters_batch_verdict_shares_judge_verdict_temperature():
    artifact, protocol, _snapshot = _v2_artifacts()
    batch = rl.resolve_request_parameters(
        artifact, protocol, "openai/gpt-oss-120b", "batch_verdict")
    verdict = rl.resolve_request_parameters(
        artifact, protocol, "openai/gpt-oss-120b", "judge_verdict")
    assert batch.protocol_role == verdict.protocol_role == "judge_verdict"
    assert batch.temperature == verdict.temperature


def test_resolve_request_parameters_fails_closed_on_non_applicable_pair():
    artifact, protocol, _snapshot = _v2_artifacts()
    with pytest.raises(rl.RoleLimitsError, match="not an applicable"):
        rl.resolve_request_parameters(
            artifact, protocol, "Qwen/Qwen2.5-7B-Instruct-Turbo", "debater_turn")


def test_resolve_request_parameters_fails_closed_on_unknown_model():
    artifact, protocol, _snapshot = _v2_artifacts()
    with pytest.raises(rl.RoleLimitsError, match="unknown model_id"):
        rl.resolve_request_parameters(artifact, protocol, "not/a-real-model", "oracle")


def test_resolve_request_parameters_fails_closed_on_unknown_role():
    artifact, protocol, _snapshot = _v2_artifacts()
    with pytest.raises(rl.RoleLimitsError, match="unknown limits_role"):
        rl.resolve_request_parameters(
            artifact, protocol, "meta-llama/Llama-3.3-70B-Instruct-Turbo", "not_a_real_role")


def test_resolve_request_parameters_fails_closed_on_non_mapping_role_entry():
    # A role_entry that isn't a mapping (an artifact mutated/bypassed outside
    # validate_role_limits_v2) must fail closed via resolve_request_parameters's own guard.
    artifact, protocol, _snapshot = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["model_role_limits"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"]["oracle"] = (
        "not a mapping")
    with pytest.raises(rl.RoleLimitsError, match="must be an object"):
        rl.resolve_request_parameters(
            changed, protocol, "meta-llama/Llama-3.3-70B-Instruct-Turbo", "oracle")


def test_resolve_request_parameters_fails_closed_on_non_int_effective_max_tokens():
    artifact, protocol, _snapshot = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["model_role_limits"]["meta-llama/Llama-3.3-70B-Instruct-Turbo"]["oracle"][
        "effective_request_max_tokens"] = "32"
    with pytest.raises(rl.RoleLimitsError, match="must be an integer"):
        rl.resolve_request_parameters(
            changed, protocol, "meta-llama/Llama-3.3-70B-Instruct-Turbo", "oracle")


def test_resolve_request_parameters_fails_closed_on_unknown_protocol_role():
    # role_taxonomy is only pre-validated by validate_role_limits_v2, which
    # resolve_request_parameters can be called independently of; a role_taxonomy target that
    # doesn't resolve against the live protocol's temperature_by_call_role must still fail
    # closed here, not only when reached indirectly through _validate_role_taxonomy.
    artifact, protocol, _snapshot = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["role_taxonomy"]["oracle"] = "not_a_real_protocol_call_role"
    with pytest.raises(rl.RoleLimitsError, match="is not a known protocol call role"):
        rl.resolve_request_parameters(
            changed, protocol, "meta-llama/Llama-3.3-70B-Instruct-Turbo", "oracle")


def test_resolve_request_parameters_fails_closed_on_non_number_temperature():
    artifact, protocol, _snapshot = _v2_artifacts()
    changed_protocol = deepcopy(protocol)
    changed_protocol["decisions"]["execution_semantics"]["temperature_by_call_role"][
        "oracle"] = "not a number"
    with pytest.raises(rl.RoleLimitsError, match="must be a number"):
        rl.resolve_request_parameters(
            artifact, changed_protocol, "meta-llama/Llama-3.3-70B-Instruct-Turbo", "oracle")


def test_resolve_request_parameters_result_is_frozen():
    artifact, protocol, _snapshot = _v2_artifacts()
    result = rl.resolve_request_parameters(
        artifact, protocol, "meta-llama/Llama-3.3-70B-Instruct-Turbo", "oracle")
    with pytest.raises(FrozenInstanceError):
        setattr(result, "effective_max_tokens", 999)


def test_every_applicable_pair_resolves_to_exactly_one_effective_limit_and_temperature():
    artifact, protocol, _snapshot = _v2_artifacts()
    seen = 0
    for model_id, roles in artifact["model_role_limits"].items():
        for role, entry in roles.items():
            result = rl.resolve_request_parameters(artifact, protocol, model_id, role)
            assert result.effective_max_tokens == entry["effective_request_max_tokens"]
            seen += 1
    assert seen == 24


# =================================================================================================
# v2 CLI
# =================================================================================================


def test_cli_v2_check_is_offline_and_prints_canonical_hash(capsys):
    assert rl.main([
        "--check", "--v2", "--artifact", str(V2_ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH), "--v1-artifact", str(ARTIFACT_PATH),
    ]) == 0
    output = capsys.readouterr().out
    artifact, _protocol, _snapshot = _v2_artifacts()
    assert phase2_plan.canonical_sha256(artifact) in output
    assert "execution_authorized=NO" in output


def test_cli_v2_check_uses_default_paths():
    assert rl.main(["--check", "--v2"]) == 0


def test_cli_v1_check_still_works_without_v2_flag(capsys):
    assert rl.main([
        "--check", "--artifact", str(ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH),
    ]) == 0
    output = capsys.readouterr().out
    assert "role-limits/request-settings artifact" in output


# =================================================================================================
# v3: retry-pin reduction + approval_basis
# =================================================================================================


def test_v3_tracked_artifact_validates():
    artifact, protocol, snapshot = _v3_artifacts()
    assert artifact["schema_version"] == "phase2_role_limits_v3"
    assert artifact["execution_authorized"] is False
    assert artifact["protocol_id"] == protocol["protocol_id"]
    assert set(artifact["model_role_limits"]) == set(snapshot["models"])


def test_v3_reproduces_every_v2_value_byte_semantically_except_transport():
    v2_artifact, _protocol, _snapshot = _v2_artifacts()
    v3_artifact, _protocol2, _snapshot2 = _v3_artifacts()
    for key in (
        "base_role_max_tokens", "reasoning_models", "model_role_limits", "context_ceilings",
        "role_taxonomy",
    ):
        assert v3_artifact[key] == v2_artifact[key], f"{key} diverged from v2"
    # request_settings itself differs only in its transport sub-block.
    for key in ("base_fields", "streaming_pinned_models", "per_model_extra_fields",
                "reasoning_control_note", "response_metadata_to_persist"):
        assert v3_artifact["request_settings"][key] == v2_artifact["request_settings"][key]
    assert v3_artifact["request_settings"]["transport"] != v2_artifact["request_settings"][
        "transport"]


def test_v3_transport_retry_pin_is_two_at_most_three_attempts():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    assert artifact["request_settings"]["transport"] == {"max_retries": 2, "max_attempts": 3}
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["max_retries"] = 3
    with pytest.raises(rl.RoleLimitsError, match="max_retries"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)

    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["max_attempts"] = 4
    with pytest.raises(rl.RoleLimitsError, match="max_attempts"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_supersedes_binds_the_real_v2_artifact():
    artifact, _protocol, _snapshot = _v3_artifacts()
    v2_artifact, _protocol2, _snapshot2 = _v2_artifacts()
    supersedes = artifact["supersedes"]
    assert supersedes["tracked_path"] == "rejudge/phase2_role_limits_v2_2026-07-18.json"
    assert supersedes["canonical_sha256"] == phase2_plan.canonical_sha256(v2_artifact)


def test_v3_supersedes_wrong_tracked_path_is_rejected():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(rl.RoleLimitsError, match="supersedes.tracked_path"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_supersedes_wrong_sha_is_rejected():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["canonical_sha256"] = _flip_hex_digest(
        changed["supersedes"]["canonical_sha256"])
    with pytest.raises(rl.RoleLimitsError, match="supersedes.canonical_sha256"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_supersedes_drifts_when_the_real_v2_file_on_disk_changes():
    artifact, protocol, snapshot = _v3_artifacts()
    tampered_v2 = deepcopy(rl._load_json(V2_ARTIFACT_PATH))
    tampered_v2["base_role_max_tokens"]["oracle"] = 999
    with pytest.raises(rl.RoleLimitsError, match="supersedes.canonical_sha256"):
        rl.validate_role_limits_v3(artifact, protocol, snapshot, tampered_v2, project_root=ROOT)


def test_load_and_validate_v3_fails_closed_on_missing_v2_file(tmp_path):
    missing_v2 = tmp_path / "does_not_exist.json"
    with pytest.raises(rl.RoleLimitsError, match="could not read"):
        rl.load_and_validate_v3(V3_ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH, missing_v2, ROOT)


# --- approval_basis --------------------------------------------------------------------------


def test_v3_approval_basis_binds_the_real_delegation_record():
    artifact, _protocol, _snapshot = _v3_artifacts()
    basis = artifact["approval_basis"]
    assert basis["tracked_path"] == "rejudge/phase2_preflight_delegation_2026-07-19.json"
    assert basis["sha256"] == hashlib.sha256(DELEGATION_PATH.read_bytes()).hexdigest()


def test_v3_approval_basis_wrong_tracked_path_is_rejected():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["approval_basis"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(rl.RoleLimitsError, match="approval_basis.tracked_path"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_approval_basis_wrong_sha_is_rejected():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["approval_basis"]["sha256"] = _flip_hex_digest(changed["approval_basis"]["sha256"])
    with pytest.raises(rl.RoleLimitsError, match="approval_basis.sha256"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_approval_basis_uses_raw_not_canonical_hashing():
    # The delegation record is JSON but must be hashed as raw bytes, not canonical JSON.
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    wrong_kind_of_hash = phase2_plan.canonical_sha256(
        json.loads(DELEGATION_PATH.read_text(encoding="utf-8")))
    changed = deepcopy(artifact)
    changed["approval_basis"]["sha256"] = wrong_kind_of_hash
    with pytest.raises(rl.RoleLimitsError, match="approval_basis.sha256"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_approval_basis_missing_file_fails_closed(tmp_path):
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    with pytest.raises(rl.RoleLimitsError, match="approval_basis artifact is missing"):
        rl.validate_role_limits_v3(artifact, protocol, snapshot, v2_artifact, project_root=tmp_path)


def test_v3_approval_basis_key_set_drift_is_rejected():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["approval_basis"]["extra"] = "x"
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


# --- top-level / reuse-of-v2-checks regressions --------------------------------------------------


def test_v3_top_level_key_drift_is_rejected():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["unexpected_field"] = True
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)

    changed = deepcopy(artifact)
    del changed["approval_basis"]
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_schema_version_drift_is_rejected():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["schema_version"] = "phase2_role_limits_v2"
    with pytest.raises(rl.RoleLimitsError, match="schema_version"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_artifact_id_value_drift_is_rejected():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["artifact_id"] = rl.ARTIFACT_ID_V2
    with pytest.raises(rl.RoleLimitsError, match="v3 role-limits artifact_id drifted"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_execution_authorized_cannot_be_flipped_true():
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["execution_authorized"] = True
    with pytest.raises(rl.RoleLimitsError, match="execution_authorized"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_reuses_v2_section_checks():
    # Regression: v3 must reuse the exact same per-section checks as v1/v2, not a parallel
    # re-implementation that could silently diverge.
    artifact, protocol, snapshot = _v3_artifacts()
    v2_artifact, _p, _s = _v2_artifacts()
    changed = deepcopy(artifact)
    changed["reasoning_models"]["floor_max_tokens"] = 2048
    with pytest.raises(rl.RoleLimitsError, match="floor_max_tokens"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)

    changed = deepcopy(artifact)
    changed["base_role_max_tokens"]["oracle"] = 64
    with pytest.raises(rl.RoleLimitsError, match="base_role_max_tokens.oracle"):
        rl.validate_role_limits_v3(changed, protocol, snapshot, v2_artifact, project_root=ROOT)


def test_v3_role_taxonomy_is_frozen_and_exact():
    artifact, _protocol, _snapshot = _v3_artifacts()
    assert artifact["role_taxonomy"] == rl.ROLE_TAXONOMY


def test_resolve_request_parameters_works_against_v3_artifact():
    # resolve_request_parameters accepts the v3 shape unmodified: identical
    # model_role_limits/role_taxonomy sections to v2, so every resolution matches.
    v3_artifact, protocol, _snapshot = _v3_artifacts()
    result = rl.resolve_request_parameters(
        v3_artifact, protocol, "google/gemma-4-31B-it", "judge_query")
    assert result.effective_max_tokens == 4096
    assert result.protocol_role == "judge_query"


def test_every_applicable_pair_resolves_against_v3():
    artifact, protocol, _snapshot = _v3_artifacts()
    seen = 0
    for model_id, roles in artifact["model_role_limits"].items():
        for role, entry in roles.items():
            result = rl.resolve_request_parameters(artifact, protocol, model_id, role)
            assert result.effective_max_tokens == entry["effective_request_max_tokens"]
            seen += 1
    assert seen == 24


# --- v3 CLI ----------------------------------------------------------------------------------


def test_cli_v3_check_is_offline_and_prints_canonical_hash(capsys):
    assert rl.main([
        "--check", "--v3", "--artifact", str(V3_ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH), "--v2-artifact", str(V2_ARTIFACT_PATH),
        "--project-root", str(ROOT),
    ]) == 0
    output = capsys.readouterr().out
    artifact, _protocol, _snapshot = _v3_artifacts()
    assert phase2_plan.canonical_sha256(artifact) in output
    assert "execution_authorized=NO" in output


def test_cli_v3_check_uses_default_paths():
    assert rl.main(["--check", "--v3"]) == 0


def test_cli_v2_still_works_alongside_v3_flag_added(capsys):
    assert rl.main([
        "--check", "--v2", "--artifact", str(V2_ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH), "--v1-artifact", str(ARTIFACT_PATH),
    ]) == 0
    output = capsys.readouterr().out
    assert "role-limits v2 artifact" in output


# ===================================================================================================
# --- v4: installed-SDK streaming-transport compatibility fix (EXACT ALLOWED-DIFF vs v3) -----------
# ===================================================================================================


def test_v4_tracked_artifact_validates():
    artifact, protocol, snapshot = _v4_artifacts()
    assert artifact["schema_version"] == "phase2_role_limits_v4"
    assert artifact["execution_authorized"] is False
    assert artifact["protocol_id"] == protocol["protocol_id"]
    assert set(artifact["model_role_limits"]) == set(snapshot["models"])


def test_v4_reproduces_every_v3_value_byte_semantically_except_streaming_pinned_models():
    v3_artifact, _protocol, _snapshot = _v3_artifacts()
    v4_artifact, _protocol2, _snapshot2 = _v4_artifacts()
    for key in (
        "base_role_max_tokens", "reasoning_models", "model_role_limits", "context_ceilings",
        "role_taxonomy",
    ):
        assert v4_artifact[key] == v3_artifact[key], f"{key} diverged from v3"
    # request_settings itself differs only in streaming_pinned_models's per-model value.
    for key in ("base_fields", "per_model_extra_fields", "reasoning_control_note", "transport",
                "response_metadata_to_persist"):
        assert v4_artifact["request_settings"][key] == v3_artifact["request_settings"][key]
    assert (v4_artifact["request_settings"]["streaming_pinned_models"]
            != v3_artifact["request_settings"]["streaming_pinned_models"])


def test_v4_streaming_pinned_models_drops_stream_options_keeps_stream_true():
    artifact, _protocol, _snapshot = _v4_artifacts()
    assert artifact["request_settings"]["streaming_pinned_models"] == {
        "Qwen/Qwen3.7-Plus": {"stream": True},
    }
    assert rl.STREAMING_PINNED_MODELS_V4 == {"Qwen/Qwen3.7-Plus": {"stream": True}}


def test_v4_streaming_pinned_models_reintroducing_stream_options_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["streaming_pinned_models"]["Qwen/Qwen3.7-Plus"][
        "stream_options"] = {"include_usage": True}
    with pytest.raises(rl.RoleLimitsError, match="streaming_pinned_models"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_transport_pin_is_unchanged_from_v3():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    assert artifact["request_settings"]["transport"] == {"max_retries": 2, "max_attempts": 3}
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["max_retries"] = 3
    with pytest.raises(rl.RoleLimitsError, match="max_retries"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_supersedes_binds_the_real_v3_artifact():
    artifact, _protocol, _snapshot = _v4_artifacts()
    v3_artifact, _protocol2, _snapshot2 = _v3_artifacts()
    supersedes = artifact["supersedes"]
    assert supersedes["tracked_path"] == "rejudge/phase2_role_limits_v3_2026-07-19.json"
    assert supersedes["canonical_sha256"] == phase2_plan.canonical_sha256(v3_artifact)


def test_v4_supersedes_wrong_tracked_path_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(rl.RoleLimitsError, match="supersedes.tracked_path"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_supersedes_wrong_sha_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["canonical_sha256"] = _flip_hex_digest(
        changed["supersedes"]["canonical_sha256"])
    with pytest.raises(rl.RoleLimitsError, match="supersedes.canonical_sha256"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_supersedes_drifts_when_the_real_v3_file_on_disk_changes():
    artifact, protocol, snapshot = _v4_artifacts()
    tampered_v3 = deepcopy(rl._load_json(V3_ARTIFACT_PATH))
    tampered_v3["base_role_max_tokens"]["oracle"] = 999
    with pytest.raises(rl.RoleLimitsError, match="supersedes.canonical_sha256"):
        rl.validate_role_limits_v4(artifact, protocol, snapshot, tampered_v3, project_root=ROOT)


def test_load_and_validate_v4_fails_closed_on_missing_v3_file(tmp_path):
    missing_v3 = tmp_path / "does_not_exist.json"
    with pytest.raises(rl.RoleLimitsError, match="could not read"):
        rl.load_and_validate_v4(V4_ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH, missing_v3, ROOT)


# --- approval_basis is still reused, unmodified, from v3 -----------------------------------------


def test_v4_approval_basis_still_binds_the_real_delegation_record():
    # v4 reuses v3's approval_basis check unmodified (not in the v3->v4 allowed-diff list):
    # still bound to the same frozen preflight-delegation record, still independently
    # re-verified against the real file on disk.
    artifact, _protocol, _snapshot = _v4_artifacts()
    basis = artifact["approval_basis"]
    assert basis["tracked_path"] == "rejudge/phase2_preflight_delegation_2026-07-19.json"
    assert basis["sha256"] == hashlib.sha256(DELEGATION_PATH.read_bytes()).hexdigest()


def test_v4_approval_basis_missing_file_fails_closed(tmp_path):
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    with pytest.raises(rl.RoleLimitsError, match="approval_basis artifact is missing"):
        rl.validate_role_limits_v4(artifact, protocol, snapshot, v3_artifact, project_root=tmp_path)


# --- sdk_compatibility_basis -----------------------------------------------------------------


def test_v4_sdk_compatibility_basis_binds_the_real_abort_closure_record():
    artifact, _protocol, _snapshot = _v4_artifacts()
    basis = artifact["sdk_compatibility_basis"]
    assert basis["tracked_path"] == "rejudge/phase2_preflight_abort_closure_2026-07-19.json"
    assert basis["sha256"] == hashlib.sha256(ABORT_CLOSURE_PATH.read_bytes()).hexdigest()
    assert "8ab0461" in basis["note"]
    assert "stream_options" in basis["note"]


def test_v4_sdk_compatibility_basis_wrong_tracked_path_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["sdk_compatibility_basis"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(rl.RoleLimitsError, match="sdk_compatibility_basis.tracked_path"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_sdk_compatibility_basis_wrong_sha_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["sdk_compatibility_basis"]["sha256"] = _flip_hex_digest(
        changed["sdk_compatibility_basis"]["sha256"])
    with pytest.raises(rl.RoleLimitsError, match="sdk_compatibility_basis.sha256"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_sdk_compatibility_basis_uses_raw_not_canonical_hashing():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    wrong_kind_of_hash = phase2_plan.canonical_sha256(
        json.loads(ABORT_CLOSURE_PATH.read_text(encoding="utf-8")))
    changed = deepcopy(artifact)
    changed["sdk_compatibility_basis"]["sha256"] = wrong_kind_of_hash
    with pytest.raises(rl.RoleLimitsError, match="sdk_compatibility_basis.sha256"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_sdk_compatibility_basis_missing_file_fails_closed(tmp_path):
    # approval_basis is checked first (reused unmodified from v3), so this root needs the real
    # delegation record present -- only the abort-closure file is missing here.
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    delegation_copy = tmp_path / "rejudge" / "phase2_preflight_delegation_2026-07-19.json"
    delegation_copy.parent.mkdir(parents=True, exist_ok=True)
    delegation_copy.write_bytes(DELEGATION_PATH.read_bytes())
    with pytest.raises(rl.RoleLimitsError, match="sdk_compatibility_basis artifact is missing"):
        rl.validate_role_limits_v4(artifact, protocol, snapshot, v3_artifact, project_root=tmp_path)


def test_v4_sdk_compatibility_basis_key_set_drift_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["sdk_compatibility_basis"]["extra"] = "x"
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_sdk_compatibility_basis_note_wording_drift_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["sdk_compatibility_basis"]["note"] = "some other note"
    with pytest.raises(rl.RoleLimitsError, match="sdk_compatibility_basis.note"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


# --- top-level / reuse-of-v3-checks regressions ---------------------------------------------------


def test_v4_top_level_key_drift_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["unexpected_field"] = True
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)

    changed = deepcopy(artifact)
    del changed["sdk_compatibility_basis"]
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_schema_version_drift_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["schema_version"] = "phase2_role_limits_v3"
    with pytest.raises(rl.RoleLimitsError, match="schema_version"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_artifact_id_value_drift_is_rejected():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["artifact_id"] = rl.ARTIFACT_ID_V3
    with pytest.raises(rl.RoleLimitsError, match="v4 role-limits artifact_id drifted"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_execution_authorized_cannot_be_flipped_true():
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["execution_authorized"] = True
    with pytest.raises(rl.RoleLimitsError, match="execution_authorized"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_reuses_v3_section_checks():
    # Regression: v4 must reuse the exact same per-section checks as v1/v2/v3, not a parallel
    # re-implementation that could silently diverge.
    artifact, protocol, snapshot = _v4_artifacts()
    v3_artifact, _p, _s = _v3_artifacts()
    changed = deepcopy(artifact)
    changed["reasoning_models"]["floor_max_tokens"] = 2048
    with pytest.raises(rl.RoleLimitsError, match="floor_max_tokens"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)

    changed = deepcopy(artifact)
    changed["base_role_max_tokens"]["oracle"] = 64
    with pytest.raises(rl.RoleLimitsError, match="base_role_max_tokens.oracle"):
        rl.validate_role_limits_v4(changed, protocol, snapshot, v3_artifact, project_root=ROOT)


def test_v4_role_taxonomy_is_frozen_and_exact():
    artifact, _protocol, _snapshot = _v4_artifacts()
    assert artifact["role_taxonomy"] == rl.ROLE_TAXONOMY


def test_resolve_request_parameters_works_against_v4_artifact():
    v4_artifact, protocol, _snapshot = _v4_artifacts()
    result = rl.resolve_request_parameters(
        v4_artifact, protocol, "google/gemma-4-31B-it", "judge_query")
    assert result.effective_max_tokens == 4096
    assert result.protocol_role == "judge_query"


def test_every_applicable_pair_resolves_against_v4():
    artifact, protocol, _snapshot = _v4_artifacts()
    seen = 0
    for model_id, roles in artifact["model_role_limits"].items():
        for role, entry in roles.items():
            result = rl.resolve_request_parameters(artifact, protocol, model_id, role)
            assert result.effective_max_tokens == entry["effective_request_max_tokens"]
            seen += 1
    assert seen == 24


# --- v4 CLI ----------------------------------------------------------------------------------


def test_cli_v4_check_is_offline_and_prints_canonical_hash(capsys):
    assert rl.main([
        "--check", "--v4", "--artifact", str(V4_ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH), "--v3-artifact", str(V3_ARTIFACT_PATH),
        "--project-root", str(ROOT),
    ]) == 0
    output = capsys.readouterr().out
    artifact, _protocol, _snapshot = _v4_artifacts()
    assert phase2_plan.canonical_sha256(artifact) in output
    assert "execution_authorized=NO" in output


def test_cli_v4_check_uses_default_paths():
    assert rl.main(["--check", "--v4"]) == 0


def test_cli_v3_still_works_alongside_v4_flag_added(capsys):
    assert rl.main([
        "--check", "--v3", "--artifact", str(V3_ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH), "--v2-artifact", str(V2_ARTIFACT_PATH),
        "--project-root", str(ROOT),
    ]) == 0
    output = capsys.readouterr().out
    assert "role-limits v3 artifact" in output


# ===================================================================================================
# --- v5: transport hardening after the real r2 relaunch's ambiguous-charge halt (EXACT ALLOWED-DIFF
# --- vs v4: streaming_pinned_models + the restructured transport section, only) ------------------
# ===================================================================================================


def test_v5_tracked_artifact_validates():
    artifact, protocol, snapshot = _v5_artifacts()
    assert artifact["schema_version"] == "phase2_role_limits_v5"
    assert artifact["execution_authorized"] is False
    assert artifact["protocol_id"] == protocol["protocol_id"]
    assert set(artifact["model_role_limits"]) == set(snapshot["models"])


def test_v5_reproduces_every_v4_value_except_streaming_pinned_models_and_transport():
    v4_artifact, _protocol, _snapshot = _v4_artifacts()
    v5_artifact, _protocol2, _snapshot2 = _v5_artifacts()
    for key in (
        "base_role_max_tokens", "reasoning_models", "model_role_limits", "context_ceilings",
        "role_taxonomy", "approval_basis", "sdk_compatibility_basis",
    ):
        assert v5_artifact[key] == v4_artifact[key], f"{key} diverged from v4"
    for key in ("base_fields", "per_model_extra_fields", "reasoning_control_note",
                "response_metadata_to_persist"):
        assert v5_artifact["request_settings"][key] == v4_artifact["request_settings"][key]
    assert (v5_artifact["request_settings"]["streaming_pinned_models"]
            != v4_artifact["request_settings"]["streaming_pinned_models"])
    assert v5_artifact["request_settings"]["transport"] != v4_artifact["request_settings"][
        "transport"]


def test_v5_streaming_pinned_models_extends_to_all_three_reasoning_models():
    artifact, _protocol, _snapshot = _v5_artifacts()
    assert artifact["request_settings"]["streaming_pinned_models"] == {
        "google/gemma-4-31B-it": {"stream": True},
        "openai/gpt-oss-120b": {"stream": True},
        "Qwen/Qwen3.7-Plus": {"stream": True},
    }
    assert set(artifact["request_settings"]["streaming_pinned_models"]) == set(
        rl.REASONING_MODEL_IDS)
    assert rl.STREAMING_PINNED_MODELS_V5 == artifact["request_settings"][
        "streaming_pinned_models"]
    # non-reasoning models are never pinned to streaming.
    for non_reasoning in ("Qwen/Qwen2.5-7B-Instruct-Turbo",
                          "meta-llama/Llama-3.3-70B-Instruct-Turbo"):
        assert non_reasoning not in artifact["request_settings"]["streaming_pinned_models"]


def test_v5_streaming_pinned_models_never_sends_stream_options():
    artifact, _protocol, _snapshot = _v5_artifacts()
    for entry in artifact["request_settings"]["streaming_pinned_models"].values():
        assert entry == {"stream": True}


@pytest.mark.parametrize("missing_model", ["google/gemma-4-31B-it", "openai/gpt-oss-120b",
                                            "Qwen/Qwen3.7-Plus"])
def test_v5_streaming_pinned_models_dropping_a_reasoning_model_is_rejected(missing_model):
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    del changed["request_settings"]["streaming_pinned_models"][missing_model]
    with pytest.raises(rl.RoleLimitsError, match="streaming_pinned_models"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_streaming_pinned_models_reintroducing_stream_options_is_rejected():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["streaming_pinned_models"]["Qwen/Qwen3.7-Plus"][
        "stream_options"] = {"include_usage": True}
    with pytest.raises(rl.RoleLimitsError, match="streaming_pinned_models"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


# --- request_settings.transport: the restructured v5 shape ---------------------------------------


def test_v5_transport_pins_the_new_five_field_shape():
    artifact, _protocol, _snapshot = _v5_artifacts()
    assert artifact["request_settings"]["transport"] == {
        "sdk_internal_max_retries": 0,
        "ledger_max_retries": 2,
        "ledger_max_attempts": 3,
        "http_timeout": {"connect": 10, "read": 600, "write": 60, "pool": 60},
        "per_call_wall_clock_ceiling_seconds": 1200,
    }


def test_v5_transport_old_ambiguous_max_retries_name_is_no_longer_a_valid_field():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["max_retries"] = changed["request_settings"][
        "transport"].pop("ledger_max_retries")
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_transport_sdk_internal_max_retries_must_be_zero():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["sdk_internal_max_retries"] = 1
    with pytest.raises(rl.RoleLimitsError, match="sdk_internal_max_retries"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_transport_ledger_max_retries_must_be_two():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["ledger_max_retries"] = 3
    with pytest.raises(rl.RoleLimitsError, match="ledger_max_retries"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_transport_ledger_max_attempts_must_be_max_retries_plus_one():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["ledger_max_attempts"] = 4
    with pytest.raises(rl.RoleLimitsError, match="ledger_max_attempts"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


@pytest.mark.parametrize("field,bad_value", [
    ("connect", 5), ("read", 60), ("write", 30), ("pool", 30),
])
def test_v5_transport_http_timeout_fields_are_pinned(field, bad_value):
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["http_timeout"][field] = bad_value
    with pytest.raises(rl.RoleLimitsError, match=f"http_timeout.{field}"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_transport_http_timeout_key_set_drift_is_rejected():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["http_timeout"]["extra"] = 1
    with pytest.raises(rl.RoleLimitsError, match="http_timeout"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)

    changed = deepcopy(artifact)
    del changed["request_settings"]["transport"]["http_timeout"]["pool"]
    with pytest.raises(rl.RoleLimitsError, match="http_timeout"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_transport_per_call_wall_clock_ceiling_must_be_1200():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["per_call_wall_clock_ceiling_seconds"] = 600
    with pytest.raises(rl.RoleLimitsError, match="per_call_wall_clock_ceiling_seconds"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_transport_key_set_drift_is_rejected():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["request_settings"]["transport"]["extra_field"] = "x"
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


# --- supersedes: v5 binds the real v4 artifact ----------------------------------------------------


def test_v5_supersedes_binds_the_real_v4_artifact():
    artifact, _protocol, _snapshot = _v5_artifacts()
    v4_artifact, _protocol2, _snapshot2 = _v4_artifacts()
    supersedes = artifact["supersedes"]
    assert supersedes["tracked_path"] == "rejudge/phase2_role_limits_v4_2026-07-19.json"
    assert supersedes["canonical_sha256"] == phase2_plan.canonical_sha256(v4_artifact)


def test_v5_supersedes_wrong_tracked_path_is_rejected():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["tracked_path"] = "rejudge/some_other_file.json"
    with pytest.raises(rl.RoleLimitsError, match="supersedes.tracked_path"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_supersedes_wrong_sha_is_rejected():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["supersedes"]["canonical_sha256"] = _flip_hex_digest(
        changed["supersedes"]["canonical_sha256"])
    with pytest.raises(rl.RoleLimitsError, match="supersedes.canonical_sha256"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_supersedes_drifts_when_the_real_v4_file_on_disk_changes():
    artifact, protocol, snapshot = _v5_artifacts()
    tampered_v4 = deepcopy(rl._load_json(V4_ARTIFACT_PATH))
    tampered_v4["base_role_max_tokens"]["oracle"] = 999
    with pytest.raises(rl.RoleLimitsError, match="supersedes.canonical_sha256"):
        rl.validate_role_limits_v5(artifact, protocol, snapshot, tampered_v4, project_root=ROOT)


def test_load_and_validate_v5_fails_closed_on_missing_v4_file(tmp_path):
    missing_v4 = tmp_path / "does_not_exist.json"
    with pytest.raises(rl.RoleLimitsError, match="could not read"):
        rl.load_and_validate_v5(V5_ARTIFACT_PATH, PROTOCOL_PATH, SNAPSHOT_PATH, missing_v4, ROOT)


# --- approval_basis / sdk_compatibility_basis are reused, unmodified, from v3/v4 ------------------


def test_v5_approval_basis_still_binds_the_real_delegation_record():
    artifact, _protocol, _snapshot = _v5_artifacts()
    basis = artifact["approval_basis"]
    assert basis["tracked_path"] == "rejudge/phase2_preflight_delegation_2026-07-19.json"
    assert basis["sha256"] == hashlib.sha256(DELEGATION_PATH.read_bytes()).hexdigest()


def test_v5_approval_basis_missing_file_fails_closed(tmp_path):
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    with pytest.raises(rl.RoleLimitsError, match="approval_basis artifact is missing"):
        rl.validate_role_limits_v5(artifact, protocol, snapshot, v4_artifact, project_root=tmp_path)


def test_v5_sdk_compatibility_basis_still_binds_the_real_abort_closure_record():
    artifact, _protocol, _snapshot = _v5_artifacts()
    basis = artifact["sdk_compatibility_basis"]
    assert basis["tracked_path"] == "rejudge/phase2_preflight_abort_closure_2026-07-19.json"
    assert basis["sha256"] == hashlib.sha256(ABORT_CLOSURE_PATH.read_bytes()).hexdigest()


def test_v5_sdk_compatibility_basis_missing_file_fails_closed(tmp_path):
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    delegation_copy = tmp_path / "rejudge" / "phase2_preflight_delegation_2026-07-19.json"
    delegation_copy.parent.mkdir(parents=True, exist_ok=True)
    delegation_copy.write_bytes(DELEGATION_PATH.read_bytes())
    with pytest.raises(rl.RoleLimitsError, match="sdk_compatibility_basis artifact is missing"):
        rl.validate_role_limits_v5(artifact, protocol, snapshot, v4_artifact, project_root=tmp_path)


# --- top-level / reuse-of-v4-checks regressions ---------------------------------------------------


def test_v5_top_level_key_drift_is_rejected():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["unexpected_field"] = True
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)

    changed = deepcopy(artifact)
    del changed["sdk_compatibility_basis"]
    with pytest.raises(rl.RoleLimitsError, match="fields drifted"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_schema_version_drift_is_rejected():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["schema_version"] = "phase2_role_limits_v4"
    with pytest.raises(rl.RoleLimitsError, match="schema_version"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_artifact_id_value_drift_is_rejected():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["artifact_id"] = rl.ARTIFACT_ID_V4
    with pytest.raises(rl.RoleLimitsError, match="v5 role-limits artifact_id drifted"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_execution_authorized_cannot_be_flipped_true():
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["execution_authorized"] = True
    with pytest.raises(rl.RoleLimitsError, match="execution_authorized"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_reuses_v4_section_checks():
    # Regression: v5 must reuse the exact same per-section checks as v1/v2/v3/v4, not a parallel
    # re-implementation that could silently diverge.
    artifact, protocol, snapshot = _v5_artifacts()
    v4_artifact, _p, _s = _v4_artifacts()
    changed = deepcopy(artifact)
    changed["reasoning_models"]["floor_max_tokens"] = 2048
    with pytest.raises(rl.RoleLimitsError, match="floor_max_tokens"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)

    changed = deepcopy(artifact)
    changed["base_role_max_tokens"]["oracle"] = 64
    with pytest.raises(rl.RoleLimitsError, match="base_role_max_tokens.oracle"):
        rl.validate_role_limits_v5(changed, protocol, snapshot, v4_artifact, project_root=ROOT)


def test_v5_role_taxonomy_is_frozen_and_exact():
    artifact, _protocol, _snapshot = _v5_artifacts()
    assert artifact["role_taxonomy"] == rl.ROLE_TAXONOMY


def test_resolve_request_parameters_works_against_v5_artifact():
    v5_artifact, protocol, _snapshot = _v5_artifacts()
    result = rl.resolve_request_parameters(
        v5_artifact, protocol, "google/gemma-4-31B-it", "judge_query")
    assert result.effective_max_tokens == 4096
    assert result.protocol_role == "judge_query"


def test_every_applicable_pair_resolves_against_v5():
    artifact, protocol, _snapshot = _v5_artifacts()
    seen = 0
    for model_id, roles in artifact["model_role_limits"].items():
        for role, entry in roles.items():
            result = rl.resolve_request_parameters(artifact, protocol, model_id, role)
            assert result.effective_max_tokens == entry["effective_request_max_tokens"]
            seen += 1
    assert seen == 24


# --- resolve_transport_ledger_max_retries: backward-compat reading of v4's field name -------------


def test_resolve_transport_ledger_max_retries_reads_v5_name():
    artifact, _protocol, _snapshot = _v5_artifacts()
    assert rl.resolve_transport_ledger_max_retries(artifact["request_settings"]) == 2


def test_resolve_transport_ledger_max_retries_falls_back_to_v4s_ambiguous_name():
    artifact, _protocol, _snapshot = _v4_artifacts()
    assert rl.resolve_transport_ledger_max_retries(artifact["request_settings"]) == 2


# --- v5 CLI ----------------------------------------------------------------------------------


def test_cli_v5_check_is_offline_and_prints_canonical_hash(capsys):
    assert rl.main([
        "--check", "--v5", "--artifact", str(V5_ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH), "--v4-artifact", str(V4_ARTIFACT_PATH),
        "--project-root", str(ROOT),
    ]) == 0
    output = capsys.readouterr().out
    artifact, _protocol, _snapshot = _v5_artifacts()
    assert phase2_plan.canonical_sha256(artifact) in output
    assert "execution_authorized=NO" in output


def test_cli_v5_check_uses_default_paths():
    assert rl.main(["--check", "--v5"]) == 0


def test_cli_v4_still_works_alongside_v5_flag_added(capsys):
    assert rl.main([
        "--check", "--v4", "--artifact", str(V4_ARTIFACT_PATH), "--protocol", str(PROTOCOL_PATH),
        "--snapshot", str(SNAPSHOT_PATH), "--v3-artifact", str(V3_ARTIFACT_PATH),
        "--project-root", str(ROOT),
    ]) == 0
    output = capsys.readouterr().out
    assert "role-limits v4 artifact" in output
