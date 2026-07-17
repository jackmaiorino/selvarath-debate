"""Build and validate the Phase-2 source-bound AI resolvability audit.

The operational ``full``/``partial``/``none`` class is a deterministic function
of the preserved normalized oracle-reply pair.  This module verifies that three
independent world-level review artifacts cover the frozen 106-question source,
then materializes one uniform artifact.  Semantic-quality annotations are kept
separate from the class and can never relabel or exclude a question.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from rejudge import phase2_resolvability_review as source_review


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVIEW_PATH = Path("rejudge/phase2_resolvability_review.json")
BASE_PROTOCOL_PATH = Path("rejudge/phase2_protocol.json")
DEFAULT_ARTIFACT_PATH = Path("rejudge/phase2_resolvability_ai_review.json")
AMENDMENT_PATH = Path("rejudge/phase2_resolvability_review_amendment_2026-07-16.json")
SCHEMA_VERSION = "phase2_resolvability_ai_review_v1"
WORLD_SCHEMA_VERSION = "phase2_resolvability_ai_review_world_v1"
CLASSIFICATION_POLICY_ID = "direct_candidate_oracle_reply_pair_v1"
BASE_PROTOCOL_ID = "phase2_pooled_hpr_2026_07_16_v1"
BASE_PROTOCOL_CANONICAL_SHA256 = (
    "54dce0c325b83989a1f50c26a76b687362bbdeee09f52cb23b6a0a62ecd89d75"
)

WORLD_SPECS: tuple[tuple[str, Path, Path], ...] = (
    (
        "carath_norn",
        Path("questions/carath_norn_questions.json"),
        Path("rejudge/phase2_resolvability_ai_review_carath_norn.json"),
    ),
    (
        "selvarath",
        Path("questions/selvarath_questions.json"),
        Path("rejudge/phase2_resolvability_ai_review_selvarath.json"),
    ),
    (
        "vethun_sarak",
        Path("questions/vethun_sarak_questions.json"),
        Path("rejudge/phase2_resolvability_ai_review_vethun_sarak.json"),
    ),
)

TOP_LEVEL_FIELDS = {"schema_version", "status", "review_metadata", "records", "summary"}
WORLD_METADATA_FIELDS = {
    "review_kind",
    "reviewer",
    "reviewed_at_utc",
    "world",
    "classification_policy_id",
    "source_review_template",
    "source_review_template_canonical_sha256",
    "source_question_bank",
    "scope_note",
    "human_validation_status",
    "human_validation_note",
}
WORLD_RECORD_FIELDS = {
    "question_id",
    "preliminary_class",
    "oracle_replies",
    "ai_recommended_class",
    "agrees_with_preliminary",
    "class_confidence",
    "class_mapping_verified",
    "class_adjudication_required",
    "semantic_quality_concern",
    "semantic_quality_concern_types",
    "semantic_rationale",
}
WORLD_SUMMARY_FIELDS = {
    "question_count",
    "ai_recommended_class_counts",
    "agreement_with_preliminary",
    "class_confidence_counts",
    "class_adjudication_required_count",
    "class_adjudication_question_ids",
    "semantic_quality_concern_count",
    "semantic_quality_concern_question_ids",
    "methodological_note",
}


class AIReviewError(ValueError):
    """A source, world review, or combined artifact is inconsistent."""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AIReviewError(f"cannot load JSON {path}: {exc}") from exc


def _canonical_sha256(value: Any) -> str:
    return source_review.canonical_sha256(value)


def _render(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _parse_timestamp(value: Any, *, context: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AIReviewError(f"{context} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise AIReviewError(f"invalid timestamp for {context}: {value!r}") from exc
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise AIReviewError(f"{context} is not UTC")
    return parsed


def _class_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(row["ai_recommended_class"] for row in records)
    return {name: counts[name] for name in ("full", "partial", "none")}


def _confidence_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(row["class_confidence"] for row in records)
    return {name: counts[name] for name in ("high", "medium", "low")}


def _expected_summary(records: Sequence[Mapping[str, Any]], note: str) -> dict[str, Any]:
    disagreements = [row["question_id"] for row in records if not row["agrees_with_preliminary"]]
    adjudications = [
        row["question_id"] for row in records if row["class_adjudication_required"]
    ]
    concerns = [row["question_id"] for row in records if row["semantic_quality_concern"]]
    return {
        "question_count": len(records),
        "ai_recommended_class_counts": _class_counts(records),
        "agreement_with_preliminary": {
            "agree": len(records) - len(disagreements),
            "disagree": len(disagreements),
        },
        "class_confidence_counts": _confidence_counts(records),
        "class_adjudication_required_count": len(adjudications),
        "class_adjudication_question_ids": adjudications,
        "semantic_quality_concern_count": len(concerns),
        "semantic_quality_concern_question_ids": concerns,
        "methodological_note": note,
    }


def _source_records(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_path = root / SOURCE_REVIEW_PATH
    source = _load_json(source_path)
    if not isinstance(source, dict) or not isinstance(source.get("records"), list):
        raise AIReviewError("source review artifact has invalid structure")
    records = source["records"]
    if len(records) != source_review.EXPECTED_QUESTION_COUNT:
        raise AIReviewError("source review artifact does not contain 106 questions")
    try:
        source_review.validate_review(source, project_root=root, require_complete=False)
    except source_review.ResolvabilityReviewError as exc:
        raise AIReviewError(f"source review or its tracked inputs are invalid: {exc}") from exc
    return source, records


def _validate_base_bindings(root: Path, source: Mapping[str, Any]) -> None:
    protocol = _load_json(root / BASE_PROTOCOL_PATH)
    if not isinstance(protocol, dict):
        raise AIReviewError("base protocol must be a JSON object")
    if protocol.get("protocol_id") != BASE_PROTOCOL_ID:
        raise AIReviewError("base protocol ID drifted")
    if _canonical_sha256(protocol) != BASE_PROTOCOL_CANONICAL_SHA256:
        raise AIReviewError("base protocol canonical hash drifted")
    try:
        bound_source_hash = protocol["materialization_requirements"][
            "resolvability_labels"
        ]["review_template_sha256"]
    except (KeyError, TypeError) as exc:
        raise AIReviewError("base protocol lacks the resolvability-template binding") from exc
    if bound_source_hash != _canonical_sha256(source):
        raise AIReviewError("source review template disagrees with the base protocol binding")


def validate_world_review(
    review: Mapping[str, Any],
    *,
    root: Path,
    world: str,
    question_bank_path: Path,
) -> None:
    if set(review) != TOP_LEVEL_FIELDS:
        raise AIReviewError(f"{world} top-level fields are not normalized")
    if review["schema_version"] != WORLD_SCHEMA_VERSION or review["status"] != "complete":
        raise AIReviewError(f"{world} schema version or status is invalid")

    metadata = review["review_metadata"]
    if not isinstance(metadata, dict) or set(metadata) != WORLD_METADATA_FIELDS:
        raise AIReviewError(f"{world} review metadata fields are invalid")
    source, all_source_records = _source_records(root)
    if metadata["world"] != world:
        raise AIReviewError(f"{world} metadata has the wrong world")
    if metadata["classification_policy_id"] != CLASSIFICATION_POLICY_ID:
        raise AIReviewError(f"{world} uses the wrong classification policy")
    if metadata["source_review_template"] != SOURCE_REVIEW_PATH.as_posix():
        raise AIReviewError(f"{world} names the wrong source review template")
    if metadata["source_review_template_canonical_sha256"] != _canonical_sha256(source):
        raise AIReviewError(f"{world} source review canonical hash is wrong")
    if metadata["source_question_bank"] != question_bank_path.as_posix():
        raise AIReviewError(f"{world} names the wrong question bank")
    if metadata["human_validation_status"] != "not_human_validated":
        raise AIReviewError(f"{world} must not claim human validation")
    for key in ("review_kind", "reviewer", "scope_note", "human_validation_note"):
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            raise AIReviewError(f"{world} metadata field {key} is blank")
    _parse_timestamp(metadata["reviewed_at_utc"], context=f"{world} reviewed_at_utc")

    frozen = [row for row in all_source_records if row["world"] == world]
    records = review["records"]
    if not isinstance(records, list) or len(records) != len(frozen):
        raise AIReviewError(f"{world} record count disagrees with the frozen source")
    for actual, expected in zip(records, frozen, strict=True):
        question_id = expected["question_id"]
        if not isinstance(actual, dict) or set(actual) != WORLD_RECORD_FIELDS:
            raise AIReviewError(f"{question_id} fields are not normalized")
        for field in ("question_id", "preliminary_class", "oracle_replies"):
            if actual[field] != expected[field]:
                raise AIReviewError(f"{question_id} source field {field} drifted")
        replies = actual["oracle_replies"]
        pair = (replies["for_correct_candidate"], replies["for_wrong_candidate"])
        mapped = source_review.CLASS_BY_ORACLE_REPLIES.get(pair)
        if mapped is None or actual["ai_recommended_class"] != mapped:
            raise AIReviewError(f"{question_id} recommendation violates the frozen mapping")
        if actual["agrees_with_preliminary"] is not True or mapped != expected["preliminary_class"]:
            raise AIReviewError(f"{question_id} class does not agree with the frozen source")
        if actual["class_confidence"] != "high":
            raise AIReviewError(f"{question_id} mapping confidence must be high")
        if actual["class_mapping_verified"] is not True:
            raise AIReviewError(f"{question_id} mapping is not marked verified")
        if actual["class_adjudication_required"] is not False:
            raise AIReviewError(f"{question_id} unexpectedly requires class adjudication")
        concern = actual["semantic_quality_concern"]
        types = actual["semantic_quality_concern_types"]
        if not isinstance(concern, bool) or not isinstance(types, list):
            raise AIReviewError(f"{question_id} semantic concern fields are invalid")
        if any(not isinstance(item, str) or not item.strip() for item in types):
            raise AIReviewError(f"{question_id} has an invalid semantic concern type")
        if concern != bool(types):
            raise AIReviewError(f"{question_id} semantic concern flag and types disagree")
        if not isinstance(actual["semantic_rationale"], str) or not actual["semantic_rationale"].strip():
            raise AIReviewError(f"{question_id} semantic rationale is blank")

    summary = review["summary"]
    if not isinstance(summary, dict) or set(summary) != WORLD_SUMMARY_FIELDS:
        raise AIReviewError(f"{world} summary fields are invalid")
    note = summary["methodological_note"]
    if not isinstance(note, str) or not note.strip():
        raise AIReviewError(f"{world} methodological note is blank")
    if summary != _expected_summary(records, note):
        raise AIReviewError(f"{world} summary disagrees with its records")


def build_combined(*, root: Path = REPO_ROOT) -> dict[str, Any]:
    source, source_records = _source_records(root)
    _validate_base_bindings(root, source)
    fragment_metadata: list[dict[str, str]] = []
    reviewed_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    review_times: list[str] = []
    reviewers: list[str] = []

    for world, question_bank, fragment_path in WORLD_SPECS:
        absolute_fragment = root / fragment_path
        fragment = _load_json(absolute_fragment)
        if not isinstance(fragment, dict):
            raise AIReviewError(f"{fragment_path} is not an object")
        validate_world_review(
            fragment,
            root=root,
            world=world,
            question_bank_path=question_bank,
        )
        metadata = fragment["review_metadata"]
        review_times.append(metadata["reviewed_at_utc"])
        reviewers.append(metadata["reviewer"])
        fragment_metadata.append(
            {
                "path": fragment_path.as_posix(),
                "canonical_json_sha256": _canonical_sha256(fragment),
            }
        )
        for row in fragment["records"]:
            question_id = row["question_id"]
            if question_id in reviewed_by_id:
                raise AIReviewError(f"duplicate reviewed question {question_id}")
            reviewed_by_id[question_id] = (world, row)

    records: list[dict[str, Any]] = []
    for frozen in source_records:
        question_id = frozen["question_id"]
        if question_id not in reviewed_by_id:
            raise AIReviewError(f"missing reviewed question {question_id}")
        world, reviewed = reviewed_by_id.pop(question_id)
        records.append(
            {
                "question_id": question_id,
                "world": world,
                "question_record_canonical_sha256": frozen[
                    "question_record_canonical_sha256"
                ],
                **reviewed,
            }
        )
    if reviewed_by_id:
        raise AIReviewError(f"unexpected reviewed IDs: {sorted(reviewed_by_id)}")

    note = (
        "The operational class is the deterministic output of the frozen reply-pair mapping. "
        "Semantic-quality concerns are annotations only: they do not relabel, filter, exclude, "
        "or reweight questions and are not human validation."
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "complete_ai_assisted_review_not_human_validation",
        "review_metadata": {
            "review_kind": "all_106_source_bound_ai_assisted_verification",
            "reviewer_type": "ai",
            "reviewers": reviewers,
            "reviewed_at_utc": max(
                review_times,
                key=lambda value: _parse_timestamp(value, context="component reviewed_at_utc"),
            ),
            "classification_policy_id": CLASSIFICATION_POLICY_ID,
            "source_review_template": SOURCE_REVIEW_PATH.as_posix(),
            "source_review_template_canonical_sha256": _canonical_sha256(source),
            "base_protocol_id": BASE_PROTOCOL_ID,
            "base_protocol_canonical_sha256": BASE_PROTOCOL_CANONICAL_SHA256,
            "component_world_reviews": fragment_metadata,
            "human_validation": False,
            "phase2_debate_outcomes_generated_or_inspected_as_of_review_completion": False,
            "scope_note": (
                "All 106 questions, both candidate answers, normalized oracle replies, source "
                "bindings, and reply-pair mappings were independently inspected by world."
            ),
            "semantic_annotation_provenance": (
                "The three Codex review lanes preserved rationales and concern tags, but exact "
                "model/build identifiers and full prompts were not captured. Treat the 63 "
                "semantic annotations as non-reproducible exploratory notes, not frozen labels."
            ),
        },
        "records": records,
        "summary": _expected_summary(records, note),
    }


def validate_combined(review: Mapping[str, Any], *, root: Path = REPO_ROOT) -> None:
    expected = build_combined(root=root)
    if review != expected:
        raise AIReviewError("combined AI review disagrees with its sources or normalized reviews")


def validate_amendment(
    amendment: Mapping[str, Any],
    *,
    combined_review: Mapping[str, Any],
    root: Path = REPO_ROOT,
) -> None:
    """Validate every execution-relevant binding duplicated by Amendment A1."""
    validate_combined(combined_review, root=root)
    status = amendment.get("status")
    if status not in {"proposed_pending_owner_approval", "approved_pre_outcome"}:
        raise AIReviewError(f"invalid amendment status: {status!r}")
    pending = status == "proposed_pending_owner_approval"
    state_fields = (
        {
            "proposed_amended_policy",
            "proposed_effective_protocol_id",
            "requirements_upon_approval",
            "scope_that_would_remain_unchanged",
            "would_supersede_upon_owner_approval",
        }
        if pending
        else {
            "amended_policy",
            "effective_protocol_id",
            "manifest_requirements",
            "scope_unchanged",
            "supersedes",
        }
    )
    common_fields = {
        "schema_version",
        "amendment_id",
        "status",
        "approval",
        "outcome_timing",
        "parent_protocol",
        "reason",
        "review_evidence",
        "schema_version",
        "separate_human_gate_unchanged",
    }
    if set(amendment) != common_fields | state_fields:
        raise AIReviewError("amendment fields disagree with its proposal/approval state")
    if amendment["schema_version"] != "phase2_protocol_amendment_v1":
        raise AIReviewError("invalid amendment schema version")
    if amendment["amendment_id"] != "phase2_pooled_hpr_2026_07_16_v1_a1":
        raise AIReviewError("invalid amendment ID")

    protocol = _load_json(root / BASE_PROTOCOL_PATH)
    source, _ = _source_records(root)
    if not isinstance(protocol, dict):
        raise AIReviewError("base protocol must be an object")
    parent = amendment["parent_protocol"]
    expected_parent = {
        "path": BASE_PROTOCOL_PATH.as_posix(),
        "protocol_id": BASE_PROTOCOL_ID,
        "canonical_json_sha256": _canonical_sha256(protocol),
        "public_commit": "0a21191539daae2e0807d92fcb5b1e8c179af027",
    }
    if parent != expected_parent:
        raise AIReviewError("amendment parent-protocol binding is invalid")
    combined_hash = _canonical_sha256(combined_review)

    policy_key = "proposed_amended_policy" if pending else "amended_policy"
    policy = amendment[policy_key]
    expected_mapping = [
        {
            "class": class_name,
            "correct_candidate_reply": correct,
            "wrong_candidate_reply": wrong,
        }
        for (correct, wrong), class_name in source_review.CLASS_BY_ORACLE_REPLIES.items()
    ]
    if policy.get("classification_policy_id") != CLASSIFICATION_POLICY_ID:
        raise AIReviewError("amendment classification policy ID is invalid")
    if policy.get("class_mapping") != expected_mapping:
        raise AIReviewError("amendment class mapping drifted")
    expected_pass = (
        "waiver_proposed_pending_owner_approval"
        if pending
        else "waived_by_owner_approved_amendment"
    )
    if policy.get("human_confirmation_pass") != expected_pass:
        raise AIReviewError("amendment human-pass state is invalid")
    for field in ("class_provenance", "failure_policy", "semantic_quality_policy"):
        if not isinstance(policy.get(field), str) or not policy[field].strip():
            raise AIReviewError(f"amendment policy field {field} is blank")

    effective_id_key = "proposed_effective_protocol_id" if pending else "effective_protocol_id"
    if amendment[effective_id_key] != f"{BASE_PROTOCOL_ID}+a1":
        raise AIReviewError("amendment effective protocol ID is invalid")
    requirements_key = "requirements_upon_approval" if pending else "manifest_requirements"
    requirements = amendment[requirements_key]
    if requirements.get("bind_base_protocol_canonical_sha256") != _canonical_sha256(protocol):
        raise AIReviewError("amendment manifest base-protocol binding is invalid")
    if requirements.get("bind_ai_review_canonical_sha256") != combined_hash:
        raise AIReviewError("amendment manifest AI-review binding is invalid")
    if requirements.get("owner_approval_must_be_present") is not True:
        raise AIReviewError("amendment manifest does not require owner approval")
    for field in ("bind_amendment_canonical_sha256", "effective_only_after"):
        if not isinstance(requirements.get(field), str) or not requirements[field].strip():
            raise AIReviewError(f"amendment manifest requirement {field} is blank")

    summary = combined_review["summary"]
    evidence = amendment["review_evidence"]
    expected_evidence = {
        "ai_review_canonical_sha256": combined_hash,
        "ai_review_path": DEFAULT_ARTIFACT_PATH.as_posix(),
        "class_adjudications_required": summary["class_adjudication_required_count"],
        "class_counts": summary["ai_recommended_class_counts"],
        "human_validation": False,
        "mapping_or_binding_discrepancies": summary["agreement_with_preliminary"]["disagree"],
        "question_count": summary["question_count"],
        "semantic_quality_concerns": summary["semantic_quality_concern_count"],
        "source_review_template_canonical_sha256": _canonical_sha256(source),
    }
    if evidence != expected_evidence:
        raise AIReviewError("amendment review evidence disagrees with the combined artifact")

    approval = amendment["approval"]
    approval_time: datetime | None = None
    if not isinstance(approval, dict) or approval.get("owner_approval_required") is not True:
        raise AIReviewError("amendment approval block is invalid")
    if pending:
        if approval.get("approved_by") is not None or approval.get("approved_at_utc") is not None:
            raise AIReviewError("pending amendment already contains approval values")
    else:
        if not isinstance(approval.get("approved_by"), str) or not approval["approved_by"].strip():
            raise AIReviewError("approved amendment has no owner name")
        approval_time = _parse_timestamp(
            approval.get("approved_at_utc"), context="amendment approved_at_utc"
        )

    timing = amendment["outcome_timing"]
    evidence_time = _parse_timestamp(
        timing.get("as_of_utc"), context="amendment outcome as_of_utc"
    )
    prepared_time = _parse_timestamp(
        timing.get("prepared_at_utc"), context="amendment prepared_at_utc"
    )
    if approval_time is not None and (
        evidence_time < approval_time or prepared_time < approval_time
    ):
        raise AIReviewError("approved amendment relies on timing evidence predating approval")
    if timing.get("paid_calls_recorded_from_approved_23200_cell_plan") != 0:
        raise AIReviewError("amendment records paid calls from the approved plan")
    if timing.get("phase2_debate_outcomes_generated") is not False:
        raise AIReviewError("amendment does not record a pre-generation state")
    if timing.get("phase2_debate_outcomes_inspected") is not False:
        raise AIReviewError("amendment does not record a pre-outcome-inspection state")
    if not isinstance(timing.get("evidence_basis"), str) or not timing["evidence_basis"].strip():
        raise AIReviewError("amendment timing evidence basis is blank")

    checker_rule = amendment["separate_human_gate_unchanged"]
    required_checker_phrases = (
        "decisions.query_screening",
        "materialization_requirements.query_checker",
        "external_assignments.query_checker_validator",
        "external_assignments.accepted_query_auditor",
        "human-labeled validation target",
        "every rejection and retry",
        "1% sample of accepted queries",
    )
    if not isinstance(checker_rule, str) or any(
        phrase not in checker_rule for phrase in required_checker_phrases
    ):
        raise AIReviewError("amendment does not preserve the complete human query-checker gate")

    scope_key = "scope_that_would_remain_unchanged" if pending else "scope_unchanged"
    supersedes_key = "would_supersede_upon_owner_approval" if pending else "supersedes"
    for field in (scope_key, supersedes_key):
        value = amendment[field]
        if not isinstance(value, list) or not value or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise AIReviewError(f"amendment field {field} is invalid")


def load_and_validate_amendment(
    *, root: Path = REPO_ROOT, combined_review: Mapping[str, Any]
) -> dict[str, Any]:
    amendment = _load_json(root / AMENDMENT_PATH)
    if not isinstance(amendment, dict):
        raise AIReviewError("amendment must be an object")
    validate_amendment(amendment, combined_review=combined_review, root=root)
    return amendment


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(REPO_ROOT))
    parser.add_argument("--artifact", default=str(DEFAULT_ARTIFACT_PATH))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.project_root).resolve()
    artifact = Path(args.artifact)
    if not artifact.is_absolute():
        artifact = root / artifact
    if args.write:
        review = build_combined(root=root)
        rendered = _render(review)
        if artifact.exists() and artifact.read_text(encoding="utf-8") != rendered and not args.force:
            print(
                "AI resolvability review error: refusing to overwrite a changed artifact; "
                "pass --force after inspecting the diff",
                file=sys.stderr,
            )
            return 2
        artifact.write_text(rendered, encoding="utf-8", newline="\n")
        print(
            f"wrote {len(review['records'])} AI-reviewed questions to {artifact}; "
            f"canonical_sha256={_canonical_sha256(review)}"
        )
        return 0

    if args.force:
        parser.error("--force is only valid with --write")
    review = _load_json(artifact)
    if not isinstance(review, dict):
        raise AIReviewError("combined AI review must be an object")
    validate_combined(review, root=root)
    amendment = load_and_validate_amendment(root=root, combined_review=review)
    print(
        f"verified {review['summary']['question_count']} AI-reviewed questions in {artifact}; "
        f"class_adjudications={review['summary']['class_adjudication_required_count']}; "
        f"amendment_status={amendment['status']}; "
        f"canonical_sha256={_canonical_sha256(review)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
