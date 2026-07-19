import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from rejudge import phase2_plan, phase2_stage_family as stage_family


ROOT = Path(__file__).resolve().parents[1]
CLOSURE_PATH = ROOT / "rejudge" / "phase2_preflight_r2_closure_2026-07-19.json"
CARRYFORWARD_PATH = ROOT / "rejudge" / "phase2_preflight_carryforward_2026-07-19.json"
LEDGER_PATH = ROOT / "rejudge" / "phase2_stage_family_ledger_2026-07-19.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _artifacts():
    return _load(CLOSURE_PATH), _load(CARRYFORWARD_PATH), _load(LEDGER_PATH)


def _write_json(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _mutated(doc, mutator):
    mutated = deepcopy(doc)
    mutator(mutated)
    return mutated


def _assert_closure_rejected(tmp_path, mutator, match):
    closure, carryforward, ledger = _artifacts()
    mutated_closure = _mutated(closure, mutator)
    closure_path = _write_json(tmp_path, "closure.json", mutated_closure)
    carryforward_path = _write_json(tmp_path, "carryforward.json", carryforward)
    ledger_path = _write_json(tmp_path, "ledger.json", ledger)
    with pytest.raises(stage_family.StageFamilyError, match=match):
        stage_family.load_and_validate_all(closure_path, carryforward_path, ledger_path)


def _assert_carryforward_rejected(tmp_path, mutator, match):
    closure, carryforward, ledger = _artifacts()
    mutated_carryforward = _mutated(carryforward, mutator)
    closure_path = _write_json(tmp_path, "closure.json", closure)
    carryforward_path = _write_json(tmp_path, "carryforward.json", mutated_carryforward)
    ledger_path = _write_json(tmp_path, "ledger.json", ledger)
    with pytest.raises(stage_family.StageFamilyError, match=match):
        stage_family.load_and_validate_all(closure_path, carryforward_path, ledger_path)


def _assert_ledger_rejected(tmp_path, mutator, match):
    closure, carryforward, ledger = _artifacts()
    mutated_ledger = _mutated(ledger, mutator)
    closure_path = _write_json(tmp_path, "closure.json", closure)
    carryforward_path = _write_json(tmp_path, "carryforward.json", carryforward)
    ledger_path = _write_json(tmp_path, "ledger.json", mutated_ledger)
    with pytest.raises(stage_family.StageFamilyError, match=match):
        stage_family.load_and_validate_all(closure_path, carryforward_path, ledger_path)


# --- happy path -----------------------------------------------------------------------------


def test_happy_path_loads_and_validates_real_tracked_files():
    closure, carryforward, ledger = stage_family.load_and_validate_all()
    assert closure["schema_version"] == "phase2_preflight_r2_closure_v1"
    assert carryforward["schema_version"] == "phase2_stage_family_carryforward_v1"
    assert ledger["schema_version"] == "phase2_stage_family_ledger_v1"
    assert closure["execution_authorized"] is False
    assert carryforward["execution_authorized"] is False
    assert ledger["execution_authorized"] is False
    assert closure["committed"] is False
    assert carryforward["committed"] is False
    assert ledger["committed"] is False


def test_cli_check_with_defaults_exits_zero():
    assert stage_family.main(["--check"]) == 0


def test_cli_check_prints_required_markers(capsys):
    assert stage_family.main([
        "--check", "--closure", str(CLOSURE_PATH), "--carryforward", str(CARRYFORWARD_PATH),
        "--ledger", str(LEDGER_PATH),
    ]) == 0
    output = capsys.readouterr().out
    closure, carryforward, ledger = _artifacts()
    assert phase2_plan.canonical_sha256(closure) in output
    assert phase2_plan.canonical_sha256(carryforward) in output
    assert phase2_plan.canonical_sha256(ledger) in output
    assert "r3_available_cap_usd=14.97676869" in output
    assert "execution_authorized=NO" in output


def test_cli_defaults_match_tracked_files():
    assert stage_family.DEFAULT_CLOSURE_PATH == CLOSURE_PATH
    assert stage_family.DEFAULT_CARRYFORWARD_PATH == CARRYFORWARD_PATH
    assert stage_family.DEFAULT_LEDGER_PATH == LEDGER_PATH


def test_cli_rejects_no_args():
    with pytest.raises(SystemExit):
        stage_family.main([])


def test_cli_rejects_unknown_flag():
    with pytest.raises(SystemExit):
        stage_family.main(["--nonsense"])


# --- load path: strict JSON reading ----------------------------------------------------------


def test_load_rejects_non_dict_json_root(tmp_path):
    path = _write_json(tmp_path, "closure.json", [])
    with pytest.raises(stage_family.StageFamilyError, match="must contain a JSON object"):
        stage_family.load_and_validate_all(path, CARRYFORWARD_PATH, LEDGER_PATH)


def test_load_rejects_undecodable_json(tmp_path):
    path = tmp_path / "closure.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(stage_family.StageFamilyError, match="could not read"):
        stage_family.load_and_validate_all(path, CARRYFORWARD_PATH, LEDGER_PATH)


def test_load_rejects_unreadable_path(tmp_path):
    with pytest.raises(stage_family.StageFamilyError, match="could not read"):
        stage_family.load_and_validate_all(tmp_path, CARRYFORWARD_PATH, LEDGER_PATH)


def test_load_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "closure.json"
    path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(stage_family.StageFamilyError, match="duplicate JSON key"):
        stage_family.load_and_validate_all(path, CARRYFORWARD_PATH, LEDGER_PATH)


def test_load_rejects_nan_literal(tmp_path):
    path = tmp_path / "closure.json"
    path.write_text('{"a": NaN}', encoding="utf-8")
    with pytest.raises(stage_family.StageFamilyError, match="non-finite literal"):
        stage_family.load_and_validate_all(path, CARRYFORWARD_PATH, LEDGER_PATH)


# --- closure: top-level shape ------------------------------------------------------------------


def test_closure_missing_top_level_key_is_rejected(tmp_path):
    _assert_closure_rejected(tmp_path, lambda c: c.pop("status"), "closure fields drifted")


def test_closure_extra_top_level_key_is_rejected(tmp_path):
    _assert_closure_rejected(
        tmp_path, lambda c: c.__setitem__("unexpected", "x"), "closure fields drifted")


def test_closure_wrong_schema_version_is_rejected(tmp_path):
    _assert_closure_rejected(
        tmp_path, lambda c: c.__setitem__("schema_version", "v2"),
        "closure.schema_version must be exactly",
    )


def test_closure_wrong_stage_is_rejected(tmp_path):
    _assert_closure_rejected(
        tmp_path, lambda c: c.__setitem__("stage", "canary"),
        "closure.stage must be exactly",
    )


def test_closure_wrong_attempt_is_rejected(tmp_path):
    _assert_closure_rejected(
        tmp_path, lambda c: c.__setitem__("attempt", "r3"),
        "closure.attempt must be exactly",
    )


def test_closure_wrong_execution_identity_is_rejected(tmp_path):
    _assert_closure_rejected(
        tmp_path, lambda c: c.__setitem__("execution_identity_sha256", "0" * 64),
        "closure.execution_identity_sha256 must be exactly",
    )


def test_closure_execution_authorized_must_be_false(tmp_path):
    _assert_closure_rejected(
        tmp_path, lambda c: c.__setitem__("execution_authorized", True),
        "closure.execution_authorized must be exactly false",
    )


def test_closure_committed_must_be_false(tmp_path):
    _assert_closure_rejected(
        tmp_path, lambda c: c.__setitem__("committed", True),
        "closure.committed must be exactly false",
    )


# --- closure: prior-attempt-closure binding -----------------------------------------------------


def test_closure_prior_closure_sha_drift_is_rejected(tmp_path):
    def mutate(c):
        c["r2_manifest_binding"]["prior_attempt_closure_binding"]["canonical_sha256"] = "0" * 64
    _assert_closure_rejected(
        tmp_path, mutate,
        "prior_attempt_closure_binding.canonical_sha256 must be exactly",
    )


# --- closure: ledger event chain -----------------------------------------------------------------


def test_closure_ledger_wrong_event_count_is_rejected(tmp_path):
    def mutate(c):
        c["ledger_evidence"]["events"].pop()
    _assert_closure_rejected(
        tmp_path, mutate, "events must have exactly 5 entries")


def test_closure_ledger_broken_hash_chain_is_rejected(tmp_path):
    def mutate(c):
        c["ledger_evidence"]["events"][2]["prev_event_hash"] = "0" * 64
    _assert_closure_rejected(
        tmp_path, mutate, r"events\[2\]\.prev_event_hash must be exactly")


def test_closure_ledger_genesis_prev_hash_must_be_null(tmp_path):
    def mutate(c):
        c["ledger_evidence"]["events"][0]["prev_event_hash"] = "0" * 64
    _assert_closure_rejected(
        tmp_path, mutate, "prev_event_hash must be null for the genesis event")


def test_closure_ambiguous_call_reserved_hash_must_reference_sequence_3(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["ledger_reserved_event_hash"] = (
            c["ledger_evidence"]["events"][4]["event_hash"]
        )
    _assert_closure_rejected(
        tmp_path, mutate, "must reference ledger_evidence.events\\[3\\]")


# --- closure: gemma resolution arithmetic and classification --------------------------------------


def test_closure_wrong_classification_label_is_rejected(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["classification"] = "SOMETHING_ELSE"
    _assert_closure_rejected(tmp_path, mutate, "classification must be exactly")


def test_closure_gemma_upper_bound_arithmetic_drift_is_rejected(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["adjudicated_upper_bound_spend_usd"] = "0.02215804"
    _assert_closure_rejected(
        tmp_path, mutate, "adjudicated_upper_bound_spend_usd must be exactly")


def test_closure_gemma_multiplier_drift_is_rejected(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["adjudicated_transmission_multiplier"] = 1
    _assert_closure_rejected(
        tmp_path, mutate, "adjudicated_transmission_multiplier must be exactly 3")


def test_closure_max_possible_transmissions_drift_is_rejected(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["max_possible_transmissions"] = 1
    _assert_closure_rejected(
        tmp_path, mutate, "max_possible_transmissions must be exactly 3")


def test_closure_dollar_value_rejects_float(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["adjudicated_reserved_cost_usd"] = 0.00738601
    _assert_closure_rejected(
        tmp_path, mutate, "must be a decimal string with exactly 8 fractional digits")


def test_closure_dollar_value_rejects_wrong_precision_string(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["adjudicated_reserved_cost_usd"] = "0.007386"
    _assert_closure_rejected(
        tmp_path, mutate, "must be a decimal string with exactly 8 fractional digits")


def test_closure_resume_eligible_must_be_false(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["resume_eligible"] = True
    _assert_closure_rejected(tmp_path, mutate, "resume_eligible must be exactly false")


def test_closure_durable_output_observed_must_be_false(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["durable_output_observed"] = True
    _assert_closure_rejected(tmp_path, mutate, "durable_output_observed must be exactly false")


def test_closure_negative_evidence_field_must_be_false(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["negative_evidence"]["gemma_result_row_present"] = True
    _assert_closure_rejected(
        tmp_path, mutate, "negative_evidence.gemma_result_row_present must be exactly false")


def test_closure_r3_binding_requirements_wrong_replacement_key_is_rejected(tmp_path):
    def mutate(c):
        c["resolution"]["ambiguous_call"]["r3_binding_requirements"][
            "replacement_of_execution_call_key"] = "0" * 64
    _assert_closure_rejected(
        tmp_path, mutate, "replacement_of_execution_call_key must be exactly")


def test_closure_carried_forward_wrong_disposition_is_rejected(tmp_path):
    def mutate(c):
        c["resolution"]["carried_forward_call"]["disposition"] = "something_else"
    _assert_closure_rejected(tmp_path, mutate, "disposition must be exactly")


def test_closure_sha256sums_entry_must_be_independently_matched(tmp_path):
    def mutate(c):
        c["archive"]["sha256sums_file"]["listed_entries"][0][
            "independently_recomputed_match"] = False
    _assert_closure_rejected(
        tmp_path, mutate, "independently_recomputed_match must be exactly true")


# --- carryforward -------------------------------------------------------------------------------


def test_carryforward_missing_top_level_key_is_rejected(tmp_path):
    _assert_carryforward_rejected(
        tmp_path, lambda cf: cf.pop("actual_charge_usd"), "carryforward fields drifted")


def test_carryforward_wrong_call_key_is_rejected(tmp_path):
    _assert_carryforward_rejected(
        tmp_path, lambda cf: cf["call"].__setitem__("execution_call_key", "0" * 64),
        "call.execution_call_key must be exactly",
    )


def test_carryforward_wrong_actual_charge_is_rejected(tmp_path):
    _assert_carryforward_rejected(
        tmp_path, lambda cf: cf.__setitem__("actual_charge_usd", "0.00107329"),
        "actual_charge_usd must be exactly",
    )


def test_carryforward_raw_line_hash_drift_is_rejected(tmp_path):
    def mutate(cf):
        cf["results_row"]["raw_line"] = cf["results_row"]["raw_line"].replace(
            "ANSWER: B", "ANSWER: A")
    _assert_carryforward_rejected(
        tmp_path, mutate, "raw_line_sha256 does not match sha256")


def test_carryforward_raw_line_must_be_valid_json(tmp_path):
    def mutate(cf):
        import hashlib
        bad = "{not valid json"
        cf["results_row"]["raw_line"] = bad
        cf["results_row"]["raw_line_sha256"] = hashlib.sha256(bad.encode("utf-8")).hexdigest()
    _assert_carryforward_rejected(tmp_path, mutate, "raw_line is not valid JSON")


def test_carryforward_raw_line_execution_call_key_must_match(tmp_path):
    def mutate(cf):
        import hashlib
        parsed = json.loads(cf["results_row"]["raw_line"])
        parsed["execution_call_key"] = "0" * 64
        new_line = json.dumps(parsed)
        cf["results_row"]["raw_line"] = new_line
        cf["results_row"]["raw_line_sha256"] = hashlib.sha256(
            new_line.encode("utf-8")).hexdigest()
    _assert_carryforward_rejected(
        tmp_path, mutate, "raw_line's own execution_call_key disagrees")


def test_carryforward_execution_authorized_must_be_false(tmp_path):
    _assert_carryforward_rejected(
        tmp_path, lambda cf: cf.__setitem__("execution_authorized", True),
        "carryforward.execution_authorized must be exactly false",
    )


# --- stage-family ledger -------------------------------------------------------------------------


def test_ledger_missing_top_level_key_is_rejected(tmp_path):
    _assert_ledger_rejected(
        tmp_path, lambda l: l.pop("r3_available_cap_usd"), "ledger fields drifted")


def test_ledger_wrong_stage_cap_is_rejected(tmp_path):
    _assert_ledger_rejected(
        tmp_path, lambda l: l.__setitem__("stage_cap_usd", "20.00000000"),
        "stage_cap_usd must be exactly",
    )


def test_ledger_r2_component_sum_drift_is_rejected(tmp_path):
    def mutate(l):
        r2 = next(a for a in l["attempts"] if a["attempt"] == "r2")
        r2["accounted_spend_usd"] = "0.02323132"
    _assert_ledger_rejected(
        tmp_path, mutate,
        "accounted_spend_usd does not equal the Decimal-exact sum of its own components",
    )


def test_ledger_carried_forward_pinned_total_drift_is_rejected(tmp_path):
    def mutate(l):
        l["carried_forward_accounted_spend_usd"] = "0.02323132"
    _assert_ledger_rejected(
        tmp_path, mutate, "ledger.carried_forward_accounted_spend_usd must be exactly")


def test_ledger_r3_available_cap_arithmetic_drift_is_rejected(tmp_path):
    def mutate(l):
        l["r3_available_cap_usd"] = "14.97676868"
    _assert_ledger_rejected(
        tmp_path, mutate, "r3_available_cap_usd must be exactly")


def test_ledger_cap_never_reset_flag_must_be_true(tmp_path):
    _assert_ledger_rejected(
        tmp_path, lambda l: l.__setitem__("cap_never_reset_by_fresh_ledger", False),
        "cap_never_reset_by_fresh_ledger must be exactly true",
    )


def test_ledger_r2_missing_gemma_component_is_rejected(tmp_path):
    def mutate(l):
        r2 = next(a for a in l["attempts"] if a["attempt"] == "r2")
        r2["components"] = [
            c for c in r2["components"]
            if c["execution_call_key"] != stage_family.GEMMA_CALL_KEY
        ]
    _assert_ledger_rejected(tmp_path, mutate, "components must have exactly 2 entries")


def test_ledger_r1_wrong_ledger_id_is_rejected(tmp_path):
    def mutate(l):
        r1 = next(a for a in l["attempts"] if a["attempt"] == "r1")
        r1["ledger_id"] = "0" * 32
    _assert_ledger_rejected(tmp_path, mutate, "attempts\\[r1\\]\\.ledger_id must be exactly")


def test_ledger_missing_r1_attempt_is_rejected(tmp_path):
    def mutate(l):
        l["attempts"] = [a for a in l["attempts"] if a["attempt"] != "r1"]
    _assert_ledger_rejected(
        tmp_path, mutate, "must have exactly 2 entries \\(r1, r2\\)")


# --- cross-artifact consistency --------------------------------------------------------------


def test_cross_check_function_passes_on_real_mutually_consistent_artifacts():
    # Every dollar amount, call key, and classification label in the three real tracked
    # artifacts is independently pinned to the SAME frozen module constants (by design: this
    # closure/carryforward/ledger triple describes one already-settled historical event, not a
    # template for arbitrary future ones), so the individual per-artifact validators alone
    # already make the three artifacts impossible to disagree with each other undetected. This
    # test exercises validate_stage_family's cross-artifact code path directly and confirms it
    # accepts the real, mutually consistent triple end to end.
    closure, carryforward, ledger = _artifacts()
    stage_family.validate_stage_family(closure, carryforward, ledger)


def test_validate_stage_family_calls_all_three_individual_validators():
    # A closure that fails its OWN individual validation must halt validate_stage_family before
    # any cross-artifact comparison is attempted, even if carryforward/ledger are untouched and
    # otherwise valid.
    closure, carryforward, ledger = _artifacts()
    broken_closure = _mutated(closure, lambda c: c.__setitem__("status", "not_a_real_status"))
    with pytest.raises(stage_family.StageFamilyError, match="closure.status must be exactly"):
        stage_family.validate_stage_family(broken_closure, carryforward, ledger)


def test_validate_stage_family_arithmetic_constants_are_internally_consistent():
    # Decimal-exact sanity check on the module's own frozen constants, independent of any file.
    reserved = Decimal(stage_family.GEMMA_RESERVED_COST_USD)
    upper = Decimal(stage_family.GEMMA_UPPER_BOUND_SPEND_USD)
    assert reserved * stage_family.GEMMA_TRANSMISSION_MULTIPLIER == upper

    qwen_actual = Decimal(stage_family.QWEN_ACTUAL_CHARGE_USD)
    carried = Decimal(stage_family.CARRIED_FORWARD_ACCOUNTED_SPEND_USD)
    assert qwen_actual + upper == carried

    cap = Decimal(stage_family.STAGE_CAP_USD)
    available = Decimal(stage_family.R3_AVAILABLE_CAP_USD)
    assert cap - carried == available
