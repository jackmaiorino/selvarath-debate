from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from rejudge import phase2_resolvability_ai_review as ai_review


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "rejudge" / "phase2_resolvability_ai_review.json"
AMENDMENT = ROOT / "rejudge" / "phase2_resolvability_review_amendment_2026-07-16.json"
BASE_PROTOCOL = ROOT / "rejudge" / "phase2_protocol.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_combined_ai_review_is_current_and_complete():
    actual = _load(ARTIFACT)
    assert actual == ai_review.build_combined(root=ROOT)
    ai_review.validate_combined(actual, root=ROOT)

    assert actual["status"] == "complete_ai_assisted_review_not_human_validation"
    assert actual["review_metadata"]["human_validation"] is False
    assert (
        actual["review_metadata"][
            "phase2_debate_outcomes_generated_or_inspected_as_of_review_completion"
        ]
        is False
    )
    assert actual["summary"]["question_count"] == 106
    assert actual["summary"]["ai_recommended_class_counts"] == {
        "full": 6,
        "partial": 41,
        "none": 59,
    }
    assert actual["summary"]["class_adjudication_required_count"] == 0
    assert actual["summary"]["semantic_quality_concern_count"] == 63


def test_world_reviews_are_normalized_and_source_bound():
    for world, question_bank, fragment_path in ai_review.WORLD_SPECS:
        fragment = _load(ROOT / fragment_path)
        ai_review.validate_world_review(
            fragment,
            root=ROOT,
            world=world,
            question_bank_path=question_bank,
        )


def test_combined_review_rejects_post_hoc_class_change():
    changed = deepcopy(_load(ARTIFACT))
    changed["records"][0]["ai_recommended_class"] = "none"
    with pytest.raises(ai_review.AIReviewError):
        ai_review.validate_combined(changed, root=ROOT)


def test_combined_review_has_unique_frozen_ids_and_no_class_overrides():
    actual = _load(ARTIFACT)
    ids = [row["question_id"] for row in actual["records"]]
    assert len(ids) == len(set(ids)) == 106
    assert all(row["class_mapping_verified"] is True for row in actual["records"])
    assert all(row["class_adjudication_required"] is False for row in actual["records"])
    assert all(row["agrees_with_preliminary"] is True for row in actual["records"])
    assert all(
        row["ai_recommended_class"] == row["preliminary_class"]
        for row in actual["records"]
    )


def test_amendment_binds_base_protocol_and_ai_review_without_waiving_checker_gate():
    amendment = _load(AMENDMENT)
    artifact = _load(ARTIFACT)
    protocol = _load(BASE_PROTOCOL)
    ai_review.validate_amendment(amendment, combined_review=artifact, root=ROOT)

    assert amendment["parent_protocol"]["canonical_json_sha256"] == (
        ai_review.source_review.canonical_sha256(protocol)
    )
    assert amendment["review_evidence"]["ai_review_canonical_sha256"] == (
        ai_review.source_review.canonical_sha256(artifact)
    )
    assert amendment["review_evidence"]["question_count"] == 106
    assert amendment["review_evidence"]["mapping_or_binding_discrepancies"] == 0
    assert amendment["review_evidence"]["class_adjudications_required"] == 0
    assert amendment["review_evidence"]["human_validation"] is False
    assert amendment["outcome_timing"]["paid_calls_recorded_from_approved_23200_cell_plan"] == 0
    assert amendment["outcome_timing"]["phase2_debate_outcomes_generated"] is False
    assert amendment["outcome_timing"]["phase2_debate_outcomes_inspected"] is False
    assert "query_checker" in amendment["separate_human_gate_unchanged"]
    assert "every rejection and retry" in amendment["separate_human_gate_unchanged"]

    approval = amendment["approval"]
    if amendment["status"] == "proposed_pending_owner_approval":
        assert approval["approved_by"] is None
        assert approval["approved_at_utc"] is None
    else:
        assert amendment["status"] == "approved_pre_outcome"
        assert approval["approved_by"]
        assert approval["approved_at_utc"]


def test_amendment_rejects_execution_binding_drift():
    amendment = deepcopy(_load(AMENDMENT))
    artifact = _load(ARTIFACT)
    requirements_key = (
        "requirements_upon_approval"
        if amendment["status"] == "proposed_pending_owner_approval"
        else "manifest_requirements"
    )
    amendment[requirements_key]["bind_ai_review_canonical_sha256"] = "0" * 64
    with pytest.raises(ai_review.AIReviewError):
        ai_review.validate_amendment(amendment, combined_review=artifact, root=ROOT)


def test_amendment_rejects_query_checker_supersession_or_effective_gate_drift():
    artifact = _load(ARTIFACT)
    amendment = deepcopy(_load(AMENDMENT))
    supersedes_key = (
        "would_supersede_upon_owner_approval"
        if amendment["status"] == "proposed_pending_owner_approval"
        else "supersedes"
    )
    amendment[supersedes_key].append("materialization_requirements.query_checker")
    with pytest.raises(ai_review.AIReviewError, match="supersession scope"):
        ai_review.validate_amendment(amendment, combined_review=artifact, root=ROOT)

    amendment = deepcopy(_load(AMENDMENT))
    requirements_key = (
        "requirements_upon_approval"
        if amendment["status"] == "proposed_pending_owner_approval"
        else "manifest_requirements"
    )
    amendment[requirements_key]["effective_only_after"] = "Owner approval only."
    with pytest.raises(ai_review.AIReviewError, match="manifest requirements"):
        ai_review.validate_amendment(amendment, combined_review=artifact, root=ROOT)


def test_amendment_rejects_mutated_combined_review_even_if_hashes_are_updated():
    amendment = deepcopy(_load(AMENDMENT))
    artifact = deepcopy(_load(ARTIFACT))
    artifact["records"][0]["semantic_rationale"] += " Mutated after review."
    changed_hash = ai_review.source_review.canonical_sha256(artifact)
    requirements_key = (
        "requirements_upon_approval"
        if amendment["status"] == "proposed_pending_owner_approval"
        else "manifest_requirements"
    )
    amendment[requirements_key]["bind_ai_review_canonical_sha256"] = changed_hash
    amendment["review_evidence"]["ai_review_canonical_sha256"] = changed_hash

    with pytest.raises(ai_review.AIReviewError, match="combined AI review"):
        ai_review.validate_amendment(amendment, combined_review=artifact, root=ROOT)


def test_approved_amendment_requires_fresh_no_outcome_attestation():
    amendment = deepcopy(_load(AMENDMENT))
    artifact = _load(ARTIFACT)
    amendment["status"] = "approved_pre_outcome"
    amendment["approval"]["approved_by"] = "Jack Maiorino"
    amendment["approval"]["approved_at_utc"] = "2099-01-01T00:00:00Z"
    if "proposed_amended_policy" in amendment:
        amendment["amended_policy"] = amendment.pop("proposed_amended_policy")
        amendment["amended_policy"]["human_confirmation_pass"] = (
            "waived_by_owner_approved_amendment"
        )
        amendment["effective_protocol_id"] = amendment.pop("proposed_effective_protocol_id")
        amendment["manifest_requirements"] = amendment.pop("requirements_upon_approval")
        amendment["scope_unchanged"] = amendment.pop("scope_that_would_remain_unchanged")
        amendment["supersedes"] = amendment.pop("would_supersede_upon_owner_approval")

    with pytest.raises(ai_review.AIReviewError, match="predating approval"):
        ai_review.validate_amendment(amendment, combined_review=artifact, root=ROOT)


def test_build_fails_closed_when_named_question_banks_are_missing(tmp_path: Path):
    for relative in (
        ai_review.SOURCE_REVIEW_PATH,
        ai_review.BASE_PROTOCOL_PATH,
        ai_review.source_review.DEFAULT_AUDIT_PATH,
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)

    with pytest.raises(ai_review.AIReviewError, match="tracked inputs"):
        ai_review.build_combined(root=tmp_path)
