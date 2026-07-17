import copy
import json
import socket
from pathlib import Path

import pytest

from rejudge import phase2_resolvability_review as review_module


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "rejudge" / "phase2_resolvability_review.json"


def _built_review():
    return review_module.build_review(project_root=ROOT)


def test_tracked_blank_template_is_immutable_deterministic_and_current():
    review = _built_review()
    assert ARTIFACT.read_text(encoding="utf-8") == review_module.render_review(review)
    review_module.validate_review(review, project_root=ROOT)


def test_review_covers_106_questions_and_expected_preliminary_classes():
    review = _built_review()
    assert review["summary"] == {
        "question_count": 106,
        "preliminary_class_counts": {"full": 6, "partial": 41, "none": 59},
        "human_review_completed": 0,
        "human_review_remaining": 106,
        "human_class_counts": {"full": 0, "partial": 0, "none": 0},
    }
    assert len({row["question_id"] for row in review["records"]}) == 106
    pairs_to_classes = {
        (
            row["oracle_replies"]["for_correct_candidate"],
            row["oracle_replies"]["for_wrong_candidate"],
        ): row["preliminary_class"]
        for row in review["records"]
    }
    assert pairs_to_classes == review_module.CLASS_BY_ORACLE_REPLIES


def test_each_record_has_versioned_bank_context_and_blank_human_fields():
    review = _built_review()
    source_by_path = {
        source["path"]: source
        for source in review["sources"]["question_banks"]
    }
    assert set(source_by_path) == {
        path.as_posix() for path in review_module.QUESTION_BANK_PATHS
    }
    assert sum(source["question_count"] for source in source_by_path.values()) == 106
    for row in review["records"]:
        source = source_by_path[row["question_bank_path"]]
        assert len(source["canonical_json_sha256"]) == 64
        assert len(row["question_record_canonical_sha256"]) == 64
        assert row["question_text"]
        assert row["candidate_answers"]["correct"]
        assert row["candidate_answers"]["wrong"]
        assert set(row["human_review"]) == review_module.HUMAN_REVIEW_FIELDS
        assert all(value is None for value in row["human_review"].values())


def test_canonical_json_file_hash_ignores_line_endings_and_formatting(tmp_path):
    value = [{"id": "Q-001", "text": "line one\nline two"}, {"answer": "YES"}]
    compact = tmp_path / "compact.json"
    pretty_crlf = tmp_path / "pretty-crlf.json"
    compact.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    pretty_crlf.write_bytes(json.dumps(value, indent=2).replace("\n", "\r\n").encode("utf-8"))
    assert review_module.canonical_json_file_sha256(compact) == (
        review_module.canonical_json_file_sha256(pretty_crlf)
    )


def test_validator_rejects_context_drift_and_incomplete_human_rows():
    review = _built_review()
    drifted = copy.deepcopy(review)
    drifted["records"][0]["oracle_replies"]["for_correct_candidate"] = "NO"
    with pytest.raises(review_module.ResolvabilityReviewError,
                       match="immutable review context drifted"):
        review_module.validate_review(drifted, project_root=ROOT)

    incomplete = copy.deepcopy(review)
    incomplete["records"][0]["human_review"]["class"] = "full"
    with pytest.raises(review_module.ResolvabilityReviewError,
                       match="only partially completed"):
        review_module.validate_review(incomplete, project_root=ROOT)


def test_completion_gate_fails_closed_on_blank_template():
    review = _built_review()
    with pytest.raises(review_module.ResolvabilityReviewError,
                       match="human review incomplete: 0/106"):
        review_module.validate_review(review, project_root=ROOT, require_complete=True)


def test_review_cli_check_is_offline(monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("resolvability review attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    assert review_module.main([
        "--project-root", str(ROOT),
        "--artifact", str(ARTIFACT),
        "--check",
    ]) == 0


def test_write_creates_a_separate_working_copy_and_refuses_overwrite(tmp_path):
    working = tmp_path / "human-review.json"
    assert review_module.main([
        "--project-root", str(ROOT),
        "--artifact", str(working),
        "--write",
    ]) == 0
    created = working.read_text(encoding="utf-8")
    assert json.loads(created)["status"] == "awaiting_human_review"

    working.write_text(created.replace(
        '"status": "awaiting_human_review"',
        '"status": "human_review_in_progress"',
    ), encoding="utf-8")
    in_progress = working.read_text(encoding="utf-8")
    assert review_module.main([
        "--project-root", str(ROOT),
        "--artifact", str(working),
        "--write",
    ]) == 2
    assert working.read_text(encoding="utf-8") == in_progress
