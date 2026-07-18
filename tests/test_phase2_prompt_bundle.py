import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase2_plan, phase2_prompt_bundle as prompt_bundle


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_PATH = ROOT / "rejudge" / "phase2_prompt_bundle.json"
PROTOCOL_PATH = ROOT / "rejudge" / "phase2_protocol.json"
EXPERIMENT_PROTOCOL_PATH = ROOT / "experiment_protocol.json"


def _artifacts():
    return prompt_bundle.load_and_validate(BUNDLE_PATH, PROTOCOL_PATH, EXPERIMENT_PROTOCOL_PATH)


def _historical_judge():
    return prompt_bundle.load_experiment_protocol_judge(EXPERIMENT_PROTOCOL_PATH)


def _write_json(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _mutated_bundle_path(tmp_path: Path, mutator) -> Path:
    """Deep-copy the real, validated bundle, mutate it, and write it to tmp_path."""
    bundle, _protocol = _artifacts()
    mutated = deepcopy(bundle)
    mutator(mutated)
    return _write_json(tmp_path, "bundle.json", mutated)


def _assert_bundle_rejected(tmp_path: Path, mutator, match: str) -> None:
    path = _mutated_bundle_path(tmp_path, mutator)
    with pytest.raises(prompt_bundle.PromptBundleError, match=match):
        prompt_bundle.load_and_validate(path, PROTOCOL_PATH)


# --- happy path -------------------------------------------------------------------


def test_happy_path_loads_and_validates_real_bundle_and_protocol():
    bundle, protocol = _artifacts()
    assert bundle["schema_version"] == "phase2_prompt_bundle_candidate_v1"
    assert bundle["execution_authorized"] is False
    assert bundle["protocol_id"] == protocol["protocol_id"]
    assert set(bundle["templates"]) == prompt_bundle.EXPECTED_TEMPLATE_NAMES
    assert len(bundle["templates"]) == 17
    assert set(bundle["condition_composition"]) == prompt_bundle.CONDITION_COMPOSITION_KEYS


def test_cli_check_with_defaults_exits_zero():
    # Exercises the real tracked bundle/protocol/experiment-protocol defaults end to end.
    assert prompt_bundle.main(["--check"]) == 0


def test_cli_check_is_offline_and_prints_required_markers(capsys):
    assert prompt_bundle.main([
        "--check", "--bundle", str(BUNDLE_PATH), "--protocol", str(PROTOCOL_PATH),
        "--experiment-protocol", str(EXPERIMENT_PROTOCOL_PATH),
    ]) == 0
    output = capsys.readouterr().out
    bundle, _protocol = _artifacts()
    assert f"templates={len(bundle['templates'])}" in output
    assert phase2_plan.canonical_sha256(bundle) in output
    assert "owner_methods_review=PENDING" in output
    assert "execution_authorized=NO" in output


def test_cli_defaults_match_tracked_files():
    assert prompt_bundle.DEFAULT_BUNDLE_PATH == BUNDLE_PATH
    assert prompt_bundle.DEFAULT_PROTOCOL_PATH == PROTOCOL_PATH
    assert prompt_bundle.DEFAULT_EXPERIMENT_PROTOCOL_PATH == EXPERIMENT_PROTOCOL_PATH


# --- CLI argument handling ---------------------------------------------------------


def test_cli_rejects_no_args():
    with pytest.raises(SystemExit):
        prompt_bundle.main([])


def test_cli_rejects_unknown_flag():
    with pytest.raises(SystemExit):
        prompt_bundle.main(["--nonsense"])


# --- load path: strict JSON reading -------------------------------------------------


def test_load_rejects_non_dict_json_root(tmp_path):
    path = _write_json(tmp_path, "bundle.json", [])
    with pytest.raises(prompt_bundle.PromptBundleError, match="must contain a JSON object"):
        prompt_bundle.load_and_validate(path, PROTOCOL_PATH)


def test_load_rejects_undecodable_json(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(prompt_bundle.PromptBundleError, match="could not read"):
        prompt_bundle.load_and_validate(path, PROTOCOL_PATH)


def test_load_rejects_unreadable_path(tmp_path):
    # tmp_path is a directory; reading it as a file must fail closed, not crash.
    with pytest.raises(prompt_bundle.PromptBundleError, match="could not read"):
        prompt_bundle.load_and_validate(tmp_path, PROTOCOL_PATH)


# --- strict JSON loading (amendment D) -----------------------------------------------


def test_load_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(prompt_bundle.PromptBundleError, match="duplicate JSON key"):
        prompt_bundle.load_and_validate(path, PROTOCOL_PATH, EXPERIMENT_PROTOCOL_PATH)


def test_load_rejects_nan_literal(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text('{"a": NaN}', encoding="utf-8")
    with pytest.raises(prompt_bundle.PromptBundleError, match="non-finite literal"):
        prompt_bundle.load_and_validate(path, PROTOCOL_PATH, EXPERIMENT_PROTOCOL_PATH)


def test_load_rejects_infinity_literal(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text('{"a": Infinity}', encoding="utf-8")
    with pytest.raises(prompt_bundle.PromptBundleError, match="non-finite literal"):
        prompt_bundle.load_and_validate(path, PROTOCOL_PATH, EXPERIMENT_PROTOCOL_PATH)


def test_load_rejects_negative_infinity_literal(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text('{"a": -Infinity}', encoding="utf-8")
    with pytest.raises(prompt_bundle.PromptBundleError, match="non-finite literal"):
        prompt_bundle.load_and_validate(path, PROTOCOL_PATH, EXPERIMENT_PROTOCOL_PATH)


def test_load_experiment_protocol_judge_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "experiment_protocol.json"
    path.write_text(
        '{"judge": {"system_prompt": "x", "system_prompt": "y"}}', encoding="utf-8")
    with pytest.raises(prompt_bundle.PromptBundleError, match="duplicate JSON key"):
        prompt_bundle.load_experiment_protocol_judge(path)


def test_load_experiment_protocol_judge_rejects_non_mapping_judge(tmp_path):
    path = tmp_path / "experiment_protocol.json"
    path.write_text('{"judge": ["not", "a", "mapping"]}', encoding="utf-8")
    with pytest.raises(prompt_bundle.PromptBundleError, match="judge must be an object"):
        prompt_bundle.load_experiment_protocol_judge(path)


# --- top-level key set and scalar fields (spec a, b) --------------------------------


def test_missing_top_level_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b.pop("status"), "bundle fields drifted")


def test_extra_top_level_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b.__setitem__("unexpected", "x"), "bundle fields drifted")


def test_wrong_schema_version_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b.__setitem__("schema_version", "v2"),
        "unsupported prompt bundle schema_version",
    )


def test_wrong_bundle_id_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b.__setitem__("bundle_id", "other"),
        "prompt bundle_id drifted",
    )


def test_wrong_protocol_id_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b.__setitem__("protocol_id", "wrong-protocol-id"),
        "protocol_id disagrees with the frozen protocol",
    )


def test_status_drift_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b.__setitem__("status", "approved"),
        "prompt bundle status drifted",
    )


def test_scientific_wording_disposition_drift_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b.__setitem__("scientific_wording_disposition", "frozen"),
        "scientific wording disposition drifted",
    )


@pytest.mark.parametrize("bad_value", [True, 1])
def test_execution_authorized_truthy_is_rejected(tmp_path, bad_value):
    _assert_bundle_rejected(
        tmp_path, lambda b, v=bad_value: b.__setitem__("execution_authorized", v),
        "execution_authorized must be exactly false",
    )


# --- continuity_policy (spec c) ------------------------------------------------------


def test_continuity_policy_missing_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["continuity_policy"].pop("normalization_authorized"),
        "continuity_policy fields drifted",
    )


def test_continuity_policy_extra_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["continuity_policy"].__setitem__("extra", "x"),
        "continuity_policy fields drifted",
    )


def test_continuity_policy_value_drift_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["continuity_policy"].__setitem__("policy", "other"),
        "continuity policy drifted",
    )


def test_honest_soft_instruction_field_drift_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path,
        lambda b: b["continuity_policy"].__setitem__(
            "honest_debater_soft_instruction", "Keep it short."),
        "honest debater soft instruction drifted",
    )


def test_dishonest_soft_instruction_must_stay_none(tmp_path):
    _assert_bundle_rejected(
        tmp_path,
        lambda b: b["continuity_policy"].__setitem__(
            "dishonest_debater_soft_instruction", "Keep each reply under 400 words."),
        "dishonest debater soft instruction must remain unset",
    )


def test_normalization_authorized_truthy_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path,
        lambda b: b["continuity_policy"].__setitem__("normalization_authorized", True),
        "continuity normalization_authorized must be exactly false",
    )


# --- continuity asymmetry enforced in the actual prompts (spec d) -------------------


@pytest.mark.parametrize("template_name", prompt_bundle.HONEST_DEBATER_TEMPLATES)
def test_honest_instruction_missing_from_honest_prompt_is_rejected(tmp_path, template_name):
    def mutator(bundle, name=template_name):
        prompt = bundle["templates"][name]["system_prompt"]
        bundle["templates"][name]["system_prompt"] = prompt.replace(
            prompt_bundle.HONEST_SOFT_INSTRUCTION, "")

    _assert_bundle_rejected(
        tmp_path, mutator, "must contain the honest soft instruction verbatim")


@pytest.mark.parametrize("template_name", prompt_bundle.DISHONEST_DEBATER_TEMPLATES)
def test_honest_instruction_leaking_into_dishonest_prompt_is_rejected(tmp_path, template_name):
    def mutator(bundle, name=template_name):
        bundle["templates"][name]["system_prompt"] += (
            " " + prompt_bundle.HONEST_SOFT_INSTRUCTION)

    _assert_bundle_rejected(
        tmp_path, mutator, "must not contain the honest soft instruction")


# --- template name set (spec e) -----------------------------------------------------


def test_missing_template_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"].pop("oracle"), "template name set drifted")


def test_extra_template_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["templates"]["unexpected"] = {
            "active": True, "system_prompt": "x", "user_prompt_template": "y",
        }

    _assert_bundle_rejected(tmp_path, mutator, "template name set drifted")


# --- per-template key sets (spec f) --------------------------------------------------


def test_standard_template_missing_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["oracle"].pop("system_prompt"),
        r"templates\.oracle fields drifted",
    )


def test_query_only_template_extra_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path,
        lambda b: b["templates"]["sequential_judge_query"].__setitem__("extra", "x"),
        r"templates\.sequential_judge_query fields drifted",
    )


def test_payload_template_missing_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["placebo"].pop("payload"),
        r"templates\.placebo fields drifted",
    )


def test_legacy_template_missing_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["legacy"].pop("reason"),
        r"templates\.legacy fields drifted",
    )


# --- active flags (spec g) -----------------------------------------------------------


def test_legacy_active_true_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["legacy"].__setitem__("active", True),
        "legacy template must remain inactive",
    )


def test_non_legacy_active_false_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["oracle"].__setitem__("active", False),
        r"templates\.oracle\.active must be exactly true",
    )


def test_legacy_reason_drift_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["legacy"].__setitem__("reason", "different reason"),
        "legacy retirement reason drifted",
    )


# --- placeholder discipline (spec h) --------------------------------------------------


def test_missing_placeholder_in_user_prompt_template_is_rejected(tmp_path):
    def mutator(bundle):
        template = bundle["templates"]["full_document"]
        template["user_prompt_template"] = template["user_prompt_template"].replace(
            "{debate_transcript}", "")

    _assert_bundle_rejected(
        tmp_path, mutator, r"templates\.full_document\.user_prompt_template placeholder")


def test_extra_placeholder_in_user_prompt_template_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["templates"]["oracle"]["user_prompt_template"] += " {extra_field}"

    _assert_bundle_rejected(
        tmp_path, mutator, r"templates\.oracle\.user_prompt_template placeholder")


def test_positional_placeholder_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["templates"]["oracle"]["user_prompt_template"] += " {}"

    _assert_bundle_rejected(
        tmp_path, mutator, "positional, empty, attribute, or index placeholder")


def test_malformed_format_string_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["templates"]["oracle"]["user_prompt_template"] += " {unclosed"

    _assert_bundle_rejected(tmp_path, mutator, "malformed format string")


def test_placeholder_in_system_prompt_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["templates"]["oracle"]["system_prompt"] += " {extra}"

    _assert_bundle_rejected(
        tmp_path, mutator,
        r"templates\.oracle\.system_prompt must not contain any placeholders",
    )


def test_placeholder_in_payload_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["templates"]["placebo"]["payload"] += " {extra}"

    _assert_bundle_rejected(
        tmp_path, mutator,
        r"templates\.placebo\.payload must not contain any placeholders",
    )


# --- truth-neutrality of non-debater templates (spec i) -------------------------------


def _nondebater_templates_with_field(field: str) -> list[str]:
    """Every non-debater template name that actually has ``field``.

    Derived from the module's own TEMPLATE_KEY_SETS/DEBATER_TEMPLATES rather than
    hardcoded, so this automatically tracks every eligible call site (including any
    template added later) instead of silently leaving new ones uncovered.
    """
    return sorted(
        name
        for name, expected_keys in prompt_bundle.TEMPLATE_KEY_SETS.items()
        if field in expected_keys and name not in prompt_bundle.DEBATER_TEMPLATES
    )


@pytest.mark.parametrize(
    "template_name", _nondebater_templates_with_field("system_prompt"))
@pytest.mark.parametrize("substring", prompt_bundle.TRUTH_LABELED_SUBSTRINGS)
def test_truth_label_leakage_into_nondebater_system_prompt_is_rejected(
    tmp_path, template_name, substring
):
    def mutator(bundle, name=template_name, leak=substring):
        bundle["templates"][name]["system_prompt"] += f" {leak}"

    _assert_bundle_rejected(
        tmp_path, mutator, "leaks a truth-labelled placeholder")


@pytest.mark.parametrize(
    "template_name", _nondebater_templates_with_field("payload"))
@pytest.mark.parametrize("substring", prompt_bundle.TRUTH_LABELED_SUBSTRINGS)
def test_truth_label_leakage_into_nondebater_payload_is_rejected(
    tmp_path, template_name, substring
):
    def mutator(bundle, name=template_name, leak=substring):
        bundle["templates"][name]["payload"] += f" {leak}"

    _assert_bundle_rejected(
        tmp_path, mutator, "leaks a truth-labelled placeholder")


@pytest.mark.parametrize(
    "template_name", _nondebater_templates_with_field("user_prompt_template"))
@pytest.mark.parametrize("substring", prompt_bundle.TRUTH_LABELED_SUBSTRINGS)
def test_truth_label_leakage_into_nondebater_user_prompt_template_is_rejected(
    tmp_path, template_name, substring
):
    def mutator(bundle, name=template_name, leak=substring):
        bundle["templates"][name]["user_prompt_template"] += f" {leak}"

    _assert_bundle_rejected(
        tmp_path, mutator, "leaks a truth-labelled placeholder")


@pytest.mark.parametrize("variant", [
    "CORRECT_ANSWER",       # case variant
    "correct answer",       # space instead of underscore
    "correct-answer",       # hyphen instead of underscore
    "correct__answer",      # doubled separator
    "Correct_Answer",       # title case
    "CoRrEcT_aNsWeR",       # mixed case
    "  correct_answer  ",   # surrounding whitespace, still exact substring once trimmed
])
def test_truth_label_leakage_case_and_spacing_variants_are_rejected(tmp_path, variant):
    # The guard must be defeated by neither casing, nor separator choice, nor doubled
    # separators; a trivial editor typo (autocapitalization, an extra underscore) must
    # never silently defeat truth-neutrality.
    def mutator(bundle, leak=variant):
        bundle["templates"]["oracle"]["system_prompt"] += f" {leak}"

    _assert_bundle_rejected(
        tmp_path, mutator, "leaks a truth-labelled placeholder")


def test_truth_label_leakage_variant_does_not_match_unrelated_text(tmp_path):
    # Sanity check that folding doesn't overreach: "correct" and "answer" appearing near
    # each other but NOT contiguous (once separators are stripped) must not false-trigger.
    def mutator(bundle):
        bundle["templates"]["oracle"]["system_prompt"] += (
            " Please answer only if the claim is unambiguously correct or incorrect.")

    path = _mutated_bundle_path(tmp_path, mutator)
    prompt_bundle.load_and_validate(path, PROTOCOL_PATH)  # must not raise


def test_debater_templates_are_exempt_from_truth_neutrality_check():
    # Debater templates legitimately reference these placeholder names; the real bundle
    # must therefore pass validation even though it "mentions" them.
    bundle, protocol = _artifacts()
    for name in prompt_bundle.DEBATER_TEMPLATES:
        assert "wrong_answer" in bundle["templates"][name]["user_prompt_template"] or (
            "correct_answer" in bundle["templates"][name]["user_prompt_template"])
    prompt_bundle.validate_bundle(bundle, protocol, _historical_judge())


# --- protocol binding for the placebo payload (spec j) ---------------------------------


def test_placebo_payload_mismatch_vs_protocol_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["placebo"].__setitem__("payload", "different wording"),
        "placebo payload disagrees with the frozen protocol wording",
    )


# --- top-level bundle/protocol type guard -----------------------------------------------


def test_non_mapping_protocol_raises_prompt_bundle_error_not_attribute_error():
    bundle, _protocol = _artifacts()
    bad_protocol: Any = ["not", "a", "dict"]
    with pytest.raises(prompt_bundle.PromptBundleError, match="protocol must be an object"):
        # Deliberately passing a non-Mapping to prove it is rejected at runtime.
        prompt_bundle.validate_bundle(
            bundle, bad_protocol, _historical_judge())  # ty: ignore[invalid-argument-type]


def test_non_mapping_bundle_with_matching_key_set_is_rejected_not_attribute_error():
    # A list whose elements happen to equal the expected top-level key set would pass a
    # bare `set(value) == set(expected)` check; it must still be rejected as non-mapping
    # before any `.get()` call is made on it.
    _bundle, protocol = _artifacts()
    fake_bundle: Any = [
        "schema_version", "bundle_id", "protocol_id", "status", "execution_authorized",
        "scientific_wording_disposition", "continuity_policy", "templates",
    ]
    with pytest.raises(prompt_bundle.PromptBundleError, match="bundle must be an object"):
        # Deliberately passing a non-Mapping to prove it is rejected at runtime.
        prompt_bundle.validate_bundle(
            fake_bundle, protocol, _historical_judge())  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("bad_value", [None, "a string", 5, ["a", "list"]])
def test_non_mapping_protocol_variants_are_rejected(bad_value):
    bundle, _protocol = _artifacts()
    with pytest.raises(prompt_bundle.PromptBundleError, match="protocol must be an object"):
        prompt_bundle.validate_bundle(bundle, bad_value, _historical_judge())


@pytest.mark.parametrize("bad_value", [None, "a string", 5, ["a", "list"]])
def test_non_mapping_bundle_variants_are_rejected(bad_value):
    _bundle, protocol = _artifacts()
    with pytest.raises(prompt_bundle.PromptBundleError, match="bundle must be an object"):
        prompt_bundle.validate_bundle(bad_value, protocol, _historical_judge())


# --- legacy-inactive invariant cross-checked against the protocol's own decision --------


def test_legacy_bridge_included_true_in_protocol_disagrees_with_hardcoded_assumption():
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    mutated_protocol["decisions"]["design_scope_reconciliation"]["matched_legacy_bridge"][
        "included"
    ] = True
    with pytest.raises(
        prompt_bundle.PromptBundleError,
        match=r"matched_legacy_bridge\.included",
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


def test_legacy_bridge_included_non_boolean_in_protocol_is_rejected():
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    mutated_protocol["decisions"]["design_scope_reconciliation"]["matched_legacy_bridge"][
        "included"
    ] = "false"
    with pytest.raises(
        prompt_bundle.PromptBundleError,
        match=r"matched_legacy_bridge\.included",
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


def test_missing_design_scope_reconciliation_in_protocol_is_rejected():
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    del mutated_protocol["decisions"]["design_scope_reconciliation"]
    with pytest.raises(
        prompt_bundle.PromptBundleError,
        match=r"design_scope_reconciliation must be an object",
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


def test_missing_matched_legacy_bridge_in_protocol_is_rejected():
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    del mutated_protocol["decisions"]["design_scope_reconciliation"]["matched_legacy_bridge"]
    with pytest.raises(
        prompt_bundle.PromptBundleError,
        match=r"matched_legacy_bridge must be an object",
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


# --- _mapping type-guard is actually pinned (spec: continuity_policy/templates) ---------


@pytest.mark.parametrize("bad_value", [[], "a string", 5, None])
def test_continuity_policy_non_mapping_is_rejected(tmp_path, bad_value):
    _assert_bundle_rejected(
        tmp_path, lambda b, v=bad_value: b.__setitem__("continuity_policy", v),
        "continuity_policy must be an object",
    )


@pytest.mark.parametrize("bad_value", [[], "a string", 5, None])
def test_templates_non_mapping_is_rejected(tmp_path, bad_value):
    _assert_bundle_rejected(
        tmp_path, lambda b, v=bad_value: b.__setitem__("templates", v),
        "templates must be an object",
    )


@pytest.mark.parametrize("bad_value", [[], "a string", 5, None])
def test_single_template_non_mapping_is_rejected(tmp_path, bad_value):
    _assert_bundle_rejected(
        tmp_path, lambda b, v=bad_value: b["templates"].__setitem__("oracle", v),
        r"templates\.oracle must be an object",
    )


# --- _string type-guard is actually pinned (system_prompt/payload/user_prompt_template) --


@pytest.mark.parametrize("bad_value", [None, 5, [], {}])
def test_system_prompt_non_string_is_rejected(tmp_path, bad_value):
    _assert_bundle_rejected(
        tmp_path,
        lambda b, v=bad_value: b["templates"]["oracle"].__setitem__("system_prompt", v),
        r"templates\.oracle\.system_prompt must be a string",
    )


@pytest.mark.parametrize("bad_value", [None, 5, [], {}])
def test_payload_non_string_is_rejected(tmp_path, bad_value):
    _assert_bundle_rejected(
        tmp_path,
        lambda b, v=bad_value: b["templates"]["placebo"].__setitem__("payload", v),
        r"templates\.placebo\.payload must be a string",
    )


@pytest.mark.parametrize("bad_value", [None, 5, [], {}])
def test_user_prompt_template_non_string_is_rejected(tmp_path, bad_value):
    _assert_bundle_rejected(
        tmp_path,
        lambda b, v=bad_value: b["templates"]["oracle"].__setitem__(
            "user_prompt_template", v),
        r"templates\.oracle\.user_prompt_template must be a string",
    )


# --- disallowed placeholder conversion / format-spec (spec h, remaining branches) --------


def test_placeholder_conversion_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["templates"]["oracle"]["user_prompt_template"] += " {query_claim!r}"

    _assert_bundle_rejected(
        tmp_path, mutator, "uses a disallowed placeholder conversion")


def test_placeholder_format_spec_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["templates"]["oracle"]["user_prompt_template"] += " {query_claim:>10}"

    _assert_bundle_rejected(
        tmp_path, mutator, "uses a disallowed placeholder format spec")


# --- protocol-side malformation branches (spec: protocol_id / decisions / execution_semantics /
# placebo_payload) -------------------------------------------------------------------------


@pytest.mark.parametrize("bad_protocol_id", ["", None, 5, []])
def test_empty_or_non_string_protocol_id_is_rejected(bad_protocol_id):
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    mutated_protocol["protocol_id"] = bad_protocol_id
    with pytest.raises(
        prompt_bundle.PromptBundleError,
        match="frozen protocol_id must be a non-empty string",
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


def test_missing_protocol_decisions_is_rejected():
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    del mutated_protocol["decisions"]
    with pytest.raises(
        prompt_bundle.PromptBundleError, match="protocol decisions must be an object"
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


@pytest.mark.parametrize("bad_decisions", [[], "a string", 5])
def test_non_mapping_protocol_decisions_is_rejected(bad_decisions):
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    mutated_protocol["decisions"] = bad_decisions
    with pytest.raises(
        prompt_bundle.PromptBundleError, match="protocol decisions must be an object"
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


def test_missing_execution_semantics_is_rejected():
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    del mutated_protocol["decisions"]["execution_semantics"]
    with pytest.raises(
        prompt_bundle.PromptBundleError,
        match=r"execution_semantics must be an object",
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


@pytest.mark.parametrize("bad_payload", ["", None, 5, []])
def test_missing_or_non_string_placebo_payload_is_rejected(bad_payload):
    bundle, protocol = _artifacts()
    mutated_protocol = deepcopy(protocol)
    mutated_protocol["decisions"]["execution_semantics"]["placebo_payload"] = bad_payload
    with pytest.raises(
        prompt_bundle.PromptBundleError,
        match="frozen placebo payload must be a non-empty string",
    ):
        prompt_bundle.validate_bundle(bundle, mutated_protocol, _historical_judge())


@pytest.mark.parametrize("bad_value", [[], "a string", 5, None])
def test_non_mapping_historical_judge_is_rejected(bad_value):
    bundle, protocol = _artifacts()
    with pytest.raises(
        prompt_bundle.PromptBundleError, match="historical judge mapping must be an object"
    ):
        prompt_bundle.validate_bundle(bundle, protocol, bad_value)


# --- top-level presence of condition_composition ---------------------------------------


def test_missing_condition_composition_top_level_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b.pop("condition_composition"), "bundle fields drifted")


# --- checker-token alignment (amendment A) ----------------------------------------------


def test_query_checker_user_prompt_token_phrase_drift_is_rejected(tmp_path):
    def mutator(bundle):
        template = bundle["templates"]["query_checker"]
        template["user_prompt_template"] = template["user_prompt_template"].replace(
            "Respond with exactly one token: allow, reject, or unresolved.",
            "Respond with exactly one token: ALLOW, REJECT, or UNRESOLVED.",
        )

    _assert_bundle_rejected(
        tmp_path, mutator, "must contain the exact checker-token phrase")


def test_query_checker_system_prompt_unresolved_phrase_drift_is_rejected(tmp_path):
    def mutator(bundle):
        template = bundle["templates"]["query_checker"]
        template["system_prompt"] = template["system_prompt"].replace(
            "return unresolved.", "return UNRESOLVED.")

    _assert_bundle_rejected(
        tmp_path, mutator, "must contain the exact unresolved-token phrase")


# --- legacy literal provenance binding (amendment B) -------------------------------------


def test_legacy_missing_new_shape_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["legacy"].pop("query_phase_prompt"),
        r"templates\.legacy fields drifted",
    )


def test_legacy_extra_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["templates"]["legacy"].__setitem__("extra_field", "x"),
        r"templates\.legacy fields drifted",
    )


@pytest.mark.parametrize("field", prompt_bundle.LEGACY_HISTORICAL_FIELDS)
def test_legacy_field_literal_mismatch_vs_historical_judge_is_rejected(tmp_path, field):
    def mutator(bundle, f=field):
        original = bundle["templates"]["legacy"][f]
        # Flip exactly the first character (never part of a `{placeholder}`, unlike the
        # last character of user_prompt_template) so this is a one-character literal
        # mismatch, not a placeholder-set or truth-neutrality change.
        flipped = ("X" if original[0] != "X" else "Y") + original[1:]
        bundle["templates"]["legacy"][f] = flipped

    _assert_bundle_rejected(
        tmp_path, mutator, rf"templates\.legacy\.{field} disagrees byte-for-byte")


def test_legacy_matches_real_historical_judge_when_unmutated():
    # Sanity check that the real amended bundle's legacy block is in fact byte-exact
    # against the tracked experiment_protocol.json judge block (not just that mutations
    # are caught).
    bundle, _protocol = _artifacts()
    historical_judge = _historical_judge()
    for field in prompt_bundle.LEGACY_HISTORICAL_FIELDS:
        assert bundle["templates"]["legacy"][field] == historical_judge[field]


# --- sentinel rendering (amendment E) -----------------------------------------------------


def test_sentinel_rendering_catches_stray_escaped_braces(tmp_path):
    # "{{stray}}" parses as pure literal text (no named field), so it survives the
    # exact-placeholder-set check unchanged, but str.format unescapes it back to literal
    # "{stray}" -- residual braces that the frozen templates never contain.
    def mutator(bundle):
        bundle["templates"]["oracle"]["user_prompt_template"] += " {{stray}}"

    _assert_bundle_rejected(
        tmp_path, mutator, "retains residual brace characters")


# --- literal payload/marker bindings (amendment F) ----------------------------------------


def test_rejection_payload_drift_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path,
        lambda b: b["templates"]["sequential_judge_rejection"].__setitem__(
            "payload", "Query rejected for a different reason"),
        r"sequential_judge_rejection\.payload disagrees",
    )


def test_empty_evidence_marker_drift_is_rejected(tmp_path):
    def mutator(bundle):
        template = bundle["templates"]["empty_evidence"]
        template["user_prompt_template"] = template["user_prompt_template"].replace(
            "[No factual claims were checked.]", "[No claims checked.]")

    _assert_bundle_rejected(
        tmp_path, mutator, "must contain the frozen empty-evidence marker line")


def test_no_debate_literal_drift_is_rejected(tmp_path):
    def mutator(bundle):
        template = bundle["templates"]["no_debate"]
        template["user_prompt_template"] = template["user_prompt_template"].replace(
            "No debate transcript or substitute transcript is provided.",
            "No transcript is available.",
        )

    _assert_bundle_rejected(
        tmp_path, mutator, "must contain the frozen no-debate literal line")


# --- condition_composition (amendment C) --------------------------------------------------


def test_condition_composition_missing_section_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["condition_composition"].pop("diagnostics"),
        "condition_composition fields drifted",
    )


def test_condition_composition_extra_key_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["condition_composition"].__setitem__("extra", {}),
        "condition_composition fields drifted",
    )


def test_condition_composition_status_drift_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["condition_composition"].__setitem__("status", "approved"),
        "condition_composition status drifted",
    )


def test_condition_composition_purpose_empty_string_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["condition_composition"].__setitem__("purpose", ""),
        "condition_composition purpose must be a non-empty string",
    )


def test_condition_composition_debate_grid_unknown_condition_id_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["debate_grid"]["unexpected_condition"] = {
            "transcript_protocol": "blind_uncapped_3_round",
            "presentation": "sequential_judge_presentation",
            "verdict": "sequential_judge_verdict",
        }

    _assert_bundle_rejected(
        tmp_path, mutator, r"condition_composition\.debate_grid fields drifted")


def test_condition_composition_debate_grid_missing_condition_id_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path, lambda b: b["condition_composition"]["debate_grid"].pop("b0"),
        r"condition_composition\.debate_grid fields drifted",
    )


def test_condition_composition_unknown_template_reference_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["debate_grid"]["sequential_b2"]["oracle"] = (
            "not_a_real_template")

    _assert_bundle_rejected(
        tmp_path, mutator,
        r"condition_composition\.debate_grid\.sequential_b2\.oracle must be",
    )


def test_condition_composition_legacy_template_reference_is_rejected(tmp_path):
    # legacy is inactive and must never be reachable through the composition map; a role
    # that names it directly must be rejected exactly like any other wrong template name.
    def mutator(bundle):
        bundle["condition_composition"]["debate_grid"]["sequential_b2"]["oracle"] = "legacy"

    _assert_bundle_rejected(
        tmp_path, mutator,
        r"condition_composition\.debate_grid\.sequential_b2\.oracle must be",
    )


def test_condition_composition_wrong_role_value_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["debate_grid"]["b0"]["presentation"] = "no_debate"

    _assert_bundle_rejected(
        tmp_path, mutator, r"condition_composition\.debate_grid\.b0\.presentation must be")


def test_condition_composition_transcript_protocol_wrong_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["cap_protection_secondary"]["capped150_b0"][
            "transcript_protocol"
        ] = "blind_uncapped_3_round"

    _assert_bundle_rejected(
        tmp_path, mutator,
        r"condition_composition\.cap_protection_secondary\.capped150_b0\.transcript_protocol "
        r"must be",
    )


def test_condition_composition_transcript_protocol_missing_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path,
        lambda b: b["condition_composition"]["debate_grid"]["b0"].pop("transcript_protocol"),
        r"condition_composition\.debate_grid\.b0 fields drifted",
    )


def test_condition_composition_transcript_protocol_forbidden_in_no_debate_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["no_debate_references"]["b0"][
            "transcript_protocol"
        ] = "blind_uncapped_3_round"

    _assert_bundle_rejected(
        tmp_path, mutator, r"condition_composition\.no_debate_references\.b0 fields drifted")


def test_condition_composition_transcript_protocol_forbidden_in_capability_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["capability_measurement"]["full_document_solo_qa"][
            "transcript_protocol"
        ] = "blind_uncapped_3_round"

    _assert_bundle_rejected(
        tmp_path, mutator,
        r"condition_composition\.capability_measurement\.full_document_solo_qa fields drifted",
    )


def test_condition_composition_word_cap_instruction_wording_drift_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["transcript_generation"]["blind_capped150_3_round"][
            "word_cap"
        ]["cap_instruction"] = "Keep it under 150 words."

    _assert_bundle_rejected(tmp_path, mutator, r"word_cap\.cap_instruction wording drifted")


def test_condition_composition_word_cap_retry_reminder_wording_drift_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["transcript_generation"]["blind_capped150_3_round"][
            "word_cap"
        ]["cap_retry_reminder"] = "Try again shorter."

    _assert_bundle_rejected(tmp_path, mutator, r"word_cap\.cap_retry_reminder wording drifted")


def test_condition_composition_word_cap_must_be_null_for_uncapped_protocol_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["transcript_generation"]["blind_uncapped_3_round"][
            "word_cap"
        ] = {
            "cap_words": 400, "cap_instruction": "x", "cap_retry_reminder": "y",
            "max_cap_attempts": 3,
        }

    _assert_bundle_rejected(tmp_path, mutator, r"word_cap must be exactly null")


def test_condition_composition_cap_words_mismatch_vs_protocol_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["transcript_generation"]["blind_capped150_3_round"][
            "word_cap"
        ]["cap_words"] = 151

    _assert_bundle_rejected(tmp_path, mutator, r"word_cap\.cap_words disagrees")


def test_condition_composition_max_cap_attempts_drift_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["transcript_generation"]["blind_capped150_3_round"][
            "word_cap"
        ]["max_cap_attempts"] = 4

    _assert_bundle_rejected(tmp_path, mutator, r"word_cap\.max_cap_attempts drifted")


def test_condition_composition_cap_words_numerically_equal_float_is_rejected(tmp_path):
    # A JSON float that is numerically equal to the frozen int (150.0 == 150) must still
    # be rejected: a bare `!=` comparison alone would silently accept it, letting a bundle
    # drift off the frozen integer invariant while still passing validation.
    def mutator(bundle):
        bundle["condition_composition"]["transcript_generation"]["blind_capped150_3_round"][
            "word_cap"
        ]["cap_words"] = 150.0

    _assert_bundle_rejected(tmp_path, mutator, r"word_cap\.cap_words disagrees")


def test_condition_composition_max_cap_attempts_numerically_equal_float_is_rejected(tmp_path):
    # Same integer-invariant gap as cap_words, but for max_cap_attempts (3.0 == 3).
    def mutator(bundle):
        bundle["condition_composition"]["transcript_generation"]["blind_capped150_3_round"][
            "word_cap"
        ]["max_cap_attempts"] = 3.0

    _assert_bundle_rejected(tmp_path, mutator, r"word_cap\.max_cap_attempts drifted")


def test_condition_composition_opening_turn_drift_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["transcript_generation"]["blind_uncapped_3_round"][
            "opening_turn"
        ]["honest"] = "debate_dishonest_blind_opening"

    _assert_bundle_rejected(tmp_path, mutator, r"transcript_generation\..*\.opening_turn drifted")


def test_condition_composition_b0_gaining_oracle_role_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["debate_grid"]["b0"]["oracle"] = "oracle"

    _assert_bundle_rejected(
        tmp_path, mutator, r"condition_composition\.debate_grid\.b0 fields drifted")


def test_condition_composition_clean_condition_missing_checker_is_rejected(tmp_path):
    _assert_bundle_rejected(
        tmp_path,
        lambda b: b["condition_composition"]["debate_grid"]["sequential_b2"].pop("checker"),
        r"condition_composition\.debate_grid\.sequential_b2 fields drifted",
    )


def test_condition_composition_placebo_condition_gaining_oracle_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["debate_grid"]["placebo_b2"]["oracle"] = "oracle"

    _assert_bundle_rejected(
        tmp_path, mutator, r"condition_composition\.debate_grid\.placebo_b2 fields drifted")


def test_condition_composition_no_debate_clean_condition_gaining_placebo_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["no_debate_references"]["clean_b2"]["placebo"] = (
            "placebo")

    _assert_bundle_rejected(
        tmp_path, mutator, r"condition_composition\.no_debate_references\.clean_b2 fields drifted")


def test_condition_composition_diagnostics_judge_role_swap_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["diagnostics"]["empty_evidence_table"]["judge"] = (
            "full_document")

    _assert_bundle_rejected(
        tmp_path, mutator,
        r"condition_composition\.diagnostics\.empty_evidence_table\.judge must be",
    )


def test_condition_composition_capability_measurement_qa_drift_is_rejected(tmp_path):
    def mutator(bundle):
        bundle["condition_composition"]["capability_measurement"]["full_document_solo_qa"][
            "qa"
        ] = "capability_qa_v2"

    _assert_bundle_rejected(
        tmp_path, mutator,
        r"condition_composition\.capability_measurement\.full_document_solo_qa\.qa must be",
    )
