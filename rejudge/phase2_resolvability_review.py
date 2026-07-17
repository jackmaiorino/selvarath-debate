"""Build and validate the tracked Phase-2 direct-resolvability review artifact.

The 2026-07-12 shortcut audit records the oracle reply obtained when each correct
and wrong candidate answer is submitted directly.  This module turns that compact
audit into a self-contained human-review template. The tracked default artifact is
an immutable blank template; reviewers create a separate working artifact with
``--artifact <new-path> --write`` and the execution manifest binds the completed copy.
Every source is bound by a
canonical-JSON SHA-256, so harmless formatting and line-ending changes do not alter
its identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_PATH = Path("rejudge/oracle_shortcut_audit_2026-07-12.json")
DEFAULT_REVIEW_PATH = Path("rejudge/phase2_resolvability_review.json")
QUESTION_BANK_PATHS = (
    Path("questions/carath_norn_questions.json"),
    Path("questions/selvarath_questions.json"),
    Path("questions/vethun_sarak_questions.json"),
)
SCHEMA_VERSION = "phase2_resolvability_review_v1"
EXPECTED_QUESTION_COUNT = 106
EXPECTED_PRELIMINARY_COUNTS = {"full": 6, "partial": 41, "none": 59}
CLASSES = frozenset(EXPECTED_PRELIMINARY_COUNTS)
HUMAN_REVIEW_FIELDS = {
    "class",
    "agrees_with_preliminary",
    "rubric_decision",
    "reviewer",
    "reviewed_at_utc",
    "notes",
}

# These are the only five pairs present in the frozen audit.  Unknown pairs fail
# closed instead of being silently folded into a class.
CLASS_BY_ORACLE_REPLIES = {
    ("YES", "NO"): "full",
    ("YES", "NOT ADDRESSED"): "partial",
    ("YES", "YES"): "partial",
    ("NOT ADDRESSED", "NOT ADDRESSED"): "none",
    ("NOT ADDRESSED", "YES"): "none",
}


class ResolvabilityReviewError(ValueError):
    """The review artifact or one of its source snapshots is inconsistent."""


def canonical_json(value: Any) -> str:
    """Return deterministic JSON used for all content hashes in this artifact."""
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResolvabilityReviewError(f"cannot load JSON source {path}: {exc}") from exc


def canonical_json_file_sha256(path: str | Path) -> str:
    """Hash parsed JSON content, deliberately ignoring whitespace and EOL style."""
    return canonical_sha256(_load_json(Path(path)))


def _require_record_list(value: Any, *, source: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ResolvabilityReviewError(f"{source} must be a JSON list of objects")
    return value


def _index_unique(
    rows: Sequence[Mapping[str, Any]], *, source: str,
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        question_id = row.get("id")
        if not isinstance(question_id, str) or not question_id:
            raise ResolvabilityReviewError(f"{source} contains a missing/invalid question id")
        if question_id in indexed:
            raise ResolvabilityReviewError(
                f"{source} contains duplicate question id {question_id}")
        indexed[question_id] = row
    return indexed


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ResolvabilityReviewError(f"source path escapes project root: {path}") from exc


def _human_review_template() -> dict[str, None]:
    return {
        "class": None,
        "agrees_with_preliminary": None,
        "rubric_decision": None,
        "reviewer": None,
        "reviewed_at_utc": None,
        "notes": None,
    }


def _classification_policy() -> dict[str, Any]:
    return {
        "policy_id": "direct_candidate_oracle_reply_pair_v1",
        "unit": "question",
        "meaning": {
            "full": "the oracle affirms the correct candidate and rejects the wrong candidate",
            "partial": (
                "the oracle affirms the correct candidate but either cannot address or also "
                "affirms the wrong candidate"
            ),
            "none": "the oracle does not address the correct candidate",
        },
        "reply_pair_mapping": [
            {
                "correct_candidate_reply": correct,
                "wrong_candidate_reply": wrong,
                "preliminary_class": preliminary_class,
            }
            for (correct, wrong), preliminary_class in CLASS_BY_ORACLE_REPLIES.items()
        ],
        "human_review_instruction": (
            "Review the question, candidate answers, and preserved oracle replies against the "
            "versioned question-bank source. Either confirm or override the preliminary class."
        ),
        "human_rubric_decisions": ["confirm_preliminary", "override_preliminary"],
    }


def build_review(
    *,
    project_root: str | Path = REPO_ROOT,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    question_bank_paths: Sequence[str | Path] = QUESTION_BANK_PATHS,
) -> dict[str, Any]:
    """Build a deterministic blank review template from tracked source JSON."""
    root = Path(project_root).resolve()
    audit_file = Path(audit_path)
    if not audit_file.is_absolute():
        audit_file = root / audit_file
    audit_rows = _require_record_list(
        _load_json(audit_file), source=_relative_path(audit_file, root))
    audit_by_id = _index_unique(audit_rows, source="shortcut audit")

    bank_by_id: dict[str, tuple[Mapping[str, Any], str]] = {}
    bank_sources: list[dict[str, Any]] = []
    for raw_path in question_bank_paths:
        bank_file = Path(raw_path)
        if not bank_file.is_absolute():
            bank_file = root / bank_file
        relative = _relative_path(bank_file, root)
        bank_rows = _require_record_list(_load_json(bank_file), source=relative)
        indexed = _index_unique(bank_rows, source=relative)
        overlap = set(indexed) & set(bank_by_id)
        if overlap:
            raise ResolvabilityReviewError(
                f"question ids occur in multiple banks: {sorted(overlap)!r}")
        for question_id, question in indexed.items():
            bank_by_id[question_id] = (question, relative)
        bank_sources.append({
            "path": relative,
            "canonical_json_sha256": canonical_sha256(bank_rows),
            "question_count": len(bank_rows),
            "question_ids_sha256": canonical_sha256(sorted(indexed)),
        })

    audit_ids = set(audit_by_id)
    bank_ids = set(bank_by_id)
    if audit_ids != bank_ids:
        raise ResolvabilityReviewError(
            "shortcut audit and question banks contain different IDs: "
            f"audit_only={sorted(audit_ids - bank_ids)!r}, "
            f"banks_only={sorted(bank_ids - audit_ids)!r}")
    if len(audit_ids) != EXPECTED_QUESTION_COUNT:
        raise ResolvabilityReviewError(
            f"found {len(audit_ids)} questions, expected {EXPECTED_QUESTION_COUNT}")

    records: list[dict[str, Any]] = []
    preliminary_counts: Counter[str] = Counter()
    for question_id in sorted(audit_ids):
        audit = audit_by_id[question_id]
        question, bank_path = bank_by_id[question_id]
        required_audit_fields = {"id", "world", "correct_answer", "wrong_answer"}
        if set(audit) != required_audit_fields:
            raise ResolvabilityReviewError(
                f"shortcut audit row {question_id} fields drifted: {sorted(audit)!r}")
        required_question_fields = {"world", "question", "correct_answer", "wrong_answer"}
        if not required_question_fields <= set(question):
            raise ResolvabilityReviewError(
                f"question-bank row {question_id} lacks review context")
        if audit["world"] != question["world"]:
            raise ResolvabilityReviewError(
                f"world mismatch for {question_id}: audit={audit['world']!r}, "
                f"bank={question['world']!r}")
        reply_pair = (audit["correct_answer"], audit["wrong_answer"])
        try:
            preliminary_class = CLASS_BY_ORACLE_REPLIES[reply_pair]
        except KeyError as exc:
            raise ResolvabilityReviewError(
                f"unknown oracle reply pair for {question_id}: {reply_pair!r}") from exc
        preliminary_counts[preliminary_class] += 1
        records.append({
            "question_id": question_id,
            "world": audit["world"],
            "question_bank_path": bank_path,
            "question_record_canonical_sha256": canonical_sha256(question),
            "question_text": question["question"],
            "candidate_answers": {
                "correct": question["correct_answer"],
                "wrong": question["wrong_answer"],
            },
            "oracle_replies": {
                "for_correct_candidate": audit["correct_answer"],
                "for_wrong_candidate": audit["wrong_answer"],
            },
            "preliminary_class": preliminary_class,
            "human_review": _human_review_template(),
        })

    counts = {name: preliminary_counts[name] for name in ("full", "partial", "none")}
    if counts != EXPECTED_PRELIMINARY_COUNTS:
        raise ResolvabilityReviewError(
            f"preliminary class counts drifted: {counts!r}, "
            f"expected {EXPECTED_PRELIMINARY_COUNTS!r}")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "awaiting_human_review",
        "classification_policy": _classification_policy(),
        "sources": {
            "oracle_shortcut_audit": {
                "path": _relative_path(audit_file, root),
                "canonical_json_sha256": canonical_sha256(audit_rows),
                "record_count": len(audit_rows),
                "reply_field_semantics": {
                    "correct_answer": "oracle reply for the correct candidate answer",
                    "wrong_answer": "oracle reply for the wrong candidate answer",
                },
            },
            "question_banks": bank_sources,
        },
        "summary": {
            "question_count": len(records),
            "preliminary_class_counts": counts,
            "human_review_completed": 0,
            "human_review_remaining": len(records),
            "human_class_counts": {"full": 0, "partial": 0, "none": 0},
        },
        "records": records,
    }


def _validate_timestamp(value: Any, *, question_id: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ResolvabilityReviewError(
            f"{question_id} reviewed_at_utc must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ResolvabilityReviewError(
            f"{question_id} reviewed_at_utc is invalid: {value!r}") from exc
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ResolvabilityReviewError(f"{question_id} reviewed_at_utc is not UTC")


def validate_review(
    review: Mapping[str, Any],
    *,
    project_root: str | Path = REPO_ROOT,
    audit_path: str | Path = DEFAULT_AUDIT_PATH,
    question_bank_paths: Sequence[str | Path] = QUESTION_BANK_PATHS,
    require_complete: bool = False,
) -> None:
    """Validate source bindings, immutable fields, and human-review completeness."""
    expected = build_review(
        project_root=project_root,
        audit_path=audit_path,
        question_bank_paths=question_bank_paths,
    )
    expected_top_level = {
        "schema_version", "status", "classification_policy", "sources", "summary", "records",
    }
    if set(review) != expected_top_level:
        raise ResolvabilityReviewError(
            f"review top-level fields drifted: {sorted(review)!r}")
    for immutable_key in ("schema_version", "classification_policy", "sources"):
        if review[immutable_key] != expected[immutable_key]:
            raise ResolvabilityReviewError(f"review {immutable_key} disagrees with tracked sources")

    records = review["records"]
    if not isinstance(records, list) or len(records) != EXPECTED_QUESTION_COUNT:
        raise ResolvabilityReviewError(
            f"review must contain exactly {EXPECTED_QUESTION_COUNT} records")
    completed = 0
    human_counts: Counter[str] = Counter()
    for actual, frozen in zip(records, expected["records"], strict=True):
        if not isinstance(actual, dict):
            raise ResolvabilityReviewError("review records must be objects")
        question_id = frozen["question_id"]
        immutable_record = {key: value for key, value in actual.items()
                            if key != "human_review"}
        frozen_record = {key: value for key, value in frozen.items()
                         if key != "human_review"}
        if immutable_record != frozen_record:
            raise ResolvabilityReviewError(
                f"immutable review context drifted for {question_id}")
        human = actual.get("human_review")
        if not isinstance(human, dict) or set(human) != HUMAN_REVIEW_FIELDS:
            raise ResolvabilityReviewError(
                f"human-review fields are invalid for {question_id}")
        required_values = [human[name] for name in (
            "class", "agrees_with_preliminary", "rubric_decision", "reviewer",
            "reviewed_at_utc",
        )]
        if all(value is None for value in required_values):
            if human["notes"] is not None:
                raise ResolvabilityReviewError(
                    f"blank review {question_id} cannot contain notes")
            continue
        if any(value is None for value in required_values):
            raise ResolvabilityReviewError(
                f"human review for {question_id} is only partially completed")
        if human["class"] not in CLASSES:
            raise ResolvabilityReviewError(
                f"invalid human class for {question_id}: {human['class']!r}")
        if not isinstance(human["agrees_with_preliminary"], bool):
            raise ResolvabilityReviewError(
                f"agrees_with_preliminary must be boolean for {question_id}")
        agrees = human["class"] == actual["preliminary_class"]
        if human["agrees_with_preliminary"] is not agrees:
            raise ResolvabilityReviewError(
                f"agreement flag contradicts classes for {question_id}")
        expected_decision = "confirm_preliminary" if agrees else "override_preliminary"
        if human["rubric_decision"] != expected_decision:
            raise ResolvabilityReviewError(
                f"rubric_decision for {question_id} must be {expected_decision!r}")
        if not isinstance(human["reviewer"], str) or not human["reviewer"].strip():
            raise ResolvabilityReviewError(f"reviewer is blank for {question_id}")
        _validate_timestamp(human["reviewed_at_utc"], question_id=question_id)
        if human["notes"] is not None and not isinstance(human["notes"], str):
            raise ResolvabilityReviewError(f"notes must be a string or null for {question_id}")
        completed += 1
        human_counts[human["class"]] += 1

    status = (
        "awaiting_human_review" if completed == 0
        else "human_review_complete" if completed == len(records)
        else "human_review_in_progress"
    )
    if review["status"] != status:
        raise ResolvabilityReviewError(
            f"review status is {review['status']!r}, expected {status!r}")
    expected_summary = {
        "question_count": len(records),
        "preliminary_class_counts": EXPECTED_PRELIMINARY_COUNTS,
        "human_review_completed": completed,
        "human_review_remaining": len(records) - completed,
        "human_class_counts": {
            name: human_counts[name] for name in ("full", "partial", "none")
        },
    }
    if review["summary"] != expected_summary:
        raise ResolvabilityReviewError("review summary disagrees with record-level reviews")
    if require_complete and completed != len(records):
        raise ResolvabilityReviewError(
            f"human review incomplete: {completed}/{len(records)} questions reviewed")


def render_review(review: Mapping[str, Any]) -> str:
    return json.dumps(review, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(REPO_ROOT))
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT_PATH))
    parser.add_argument("--artifact", default=str(DEFAULT_REVIEW_PATH))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="With --write only, explicitly replace an existing artifact and erase reviews.",
    )
    args = parser.parse_args(argv)

    root = Path(args.project_root).resolve()
    artifact = Path(args.artifact)
    if not artifact.is_absolute():
        artifact = root / artifact
    if args.write:
        if args.require_complete:
            parser.error("--require-complete is only valid with --check")
        review = build_review(project_root=root, audit_path=args.audit)
        rendered = render_review(review)
        if artifact.exists():
            current = artifact.read_text(encoding="utf-8")
            if current == rendered:
                print(
                    f"template already current at {artifact}; "
                    f"canonical_sha256={canonical_sha256(review)}"
                )
                return 0
            if not args.force_reset:
                print(
                    "resolvability review error: refusing to overwrite an existing review; "
                    "choose a new --artifact path or pass --force-reset to erase it",
                    file=sys.stderr,
                )
                return 2
        artifact.write_text(rendered, encoding="utf-8", newline="\n")
        print(
            f"wrote {len(review['records'])} questions to {artifact}; "
            f"canonical_sha256={canonical_sha256(review)}")
        return 0

    if args.force_reset:
        parser.error("--force-reset is only valid with --write")
    review = _load_json(artifact)
    if not isinstance(review, dict):
        raise ResolvabilityReviewError("review artifact must be a JSON object")
    validate_review(
        review,
        project_root=root,
        audit_path=args.audit,
        require_complete=args.require_complete,
    )
    print(
        f"verified {review['summary']['human_review_completed']}/"
        f"{review['summary']['question_count']} human reviews in {artifact}; "
        f"canonical_sha256={canonical_sha256(review)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
