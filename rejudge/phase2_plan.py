"""Deterministic, offline-only enumeration for the draft Phase-2 design.

This module deliberately has no API client, network, credential, or execution code.  It turns
``phase2_protocol.json`` into a reviewable cell inventory, validates that inventory, and prints
either a compact human summary or JSON.  The protocol remains
``draft_requires_signoff``/``execution_authorized=false``; this planner is not a live runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_PROTOCOL_PATH = Path(__file__).with_name("phase2_protocol.json")
OFFLINE_ONLY = True


class ProtocolValidationError(ValueError):
    """Raised when the draft protocol is internally inconsistent."""


class PlanValidationError(ValueError):
    """Raised when an enumerated plan has duplicates or broken dependencies."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolValidationError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProtocolValidationError(f"{label} must be an array")
    return value


def _unique_strings(value: Any, label: str) -> list[str]:
    items = _list(value, label)
    if not all(isinstance(item, str) and item for item in items):
        raise ProtocolValidationError(f"{label} must contain non-empty strings")
    strings = list(items)
    if len(strings) != len(set(strings)):
        raise ProtocolValidationError(f"{label} contains duplicates")
    return strings


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProtocolValidationError(f"{label} must be a positive integer")
    return value


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    """Validate only the facts frozen in the draft scaffold.

    Unresolved analysis/spend choices are required to remain visibly unresolved.  This keeps a
    syntactically valid draft from silently becoming an executable protocol.
    """
    if protocol.get("schema_version") != "phase2_plan_v1":
        raise ProtocolValidationError("unsupported schema_version")
    if protocol.get("status") != "draft_requires_signoff":
        raise ProtocolValidationError("status must remain draft_requires_signoff")
    if protocol.get("offline_planning_only") is not True:
        raise ProtocolValidationError("offline_planning_only must be true")
    if protocol.get("execution_authorized") is not False:
        raise ProtocolValidationError("execution_authorized must be false")
    for field in ("protocol_id", "cell_key_namespace"):
        if not isinstance(protocol.get(field), str) or not protocol[field]:
            raise ProtocolValidationError(f"{field} must be a non-empty string")

    question_set = _mapping(protocol.get("question_set"), "question_set")
    sources = _unique_strings(question_set.get("question_sources"),
                              "question_set.question_sources")
    if not sources:
        raise ProtocolValidationError("at least one question source is required")
    excluded = _unique_strings(question_set.get("calibration_excluded_question_ids"),
                               "question_set.calibration_excluded_question_ids")
    expected_excluded = _positive_int(question_set.get("expected_calibration_exclusion_count"),
                                      "expected_calibration_exclusion_count")
    expected_main = _positive_int(question_set.get("expected_main_question_count"),
                                  "expected_main_question_count")
    expected_total = _positive_int(question_set.get("expected_total_question_count"),
                                   "expected_total_question_count")
    if len(excluded) != expected_excluded or expected_excluded != 24:
        raise ProtocolValidationError("the draft must exclude exactly 24 calibration IDs")
    if expected_main != 82 or expected_total != expected_excluded + expected_main:
        raise ProtocolValidationError("the draft question counts must be 24 excluded and 82 main")
    if _positive_int(question_set.get("transcripts_per_question_per_debater"),
                     "transcripts_per_question_per_debater") != 3:
        raise ProtocolValidationError("the draft requires 3 transcripts per question/debater")

    debate_protocol = _mapping(protocol.get("debate_protocol"), "debate_protocol")
    if debate_protocol.get("name") != "blind_uncapped_3_round":
        raise ProtocolValidationError("the selected debate protocol must be blind uncapped 3-round")
    if debate_protocol.get("rounds") != 3:
        raise ProtocolValidationError("the selected debate protocol must have 3 rounds")
    if debate_protocol.get("opening_order") != "counterbalanced":
        raise ProtocolValidationError("opening order must be counterbalanced")
    if debate_protocol.get("honest_and_dishonest_use_same_model") is not True:
        raise ProtocolValidationError("honest and dishonest debaters must use the same model")

    registry = _mapping(protocol.get("model_registry"), "model_registry")
    roster = _mapping(protocol.get("roster"), "roster")
    judges = _unique_strings(roster.get("judges"), "roster.judges")
    debaters = _unique_strings(roster.get("debaters"), "roster.debaters")
    oracle = roster.get("oracle")
    if len(judges) != 4 or len(debaters) != 2 or not isinstance(oracle, str):
        raise ProtocolValidationError("the final roster requires 4 judges, 2 debaters, and 1 oracle")
    for model_id in judges + debaters + [oracle]:
        entry = _mapping(registry.get(model_id), f"model_registry[{model_id!r}]")
        prices = _mapping(entry.get("price_usd_per_million_tokens"),
                          f"prices for {model_id}")
        if set(prices) != {"input", "output"}:
            raise ProtocolValidationError(f"{model_id} must have separate input/output prices")
        for token_class, price in prices.items():
            if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
                raise ProtocolValidationError(
                    f"invalid {token_class} price for {model_id}: {price!r}")

    debate_grid = _mapping(protocol.get("debate_grid"), "debate_grid")
    if debate_grid.get("k") != 2:
        raise ProtocolValidationError("debate-grid K must be 2")
    debate_conditions = _list(debate_grid.get("conditions"), "debate_grid.conditions")
    debate_ids = [
        _mapping(condition, "debate condition").get("id") for condition in debate_conditions
    ]
    expected_debate_ids = ["b0", "sequential_b2", "batch_same_qa_b2", "placebo_b2"]
    if debate_ids != expected_debate_ids:
        raise ProtocolValidationError(
            f"debate conditions must be ordered as {expected_debate_ids!r}")
    batch = _mapping(debate_conditions[2], "batch_same_qa_b2")
    if batch.get("depends_on_condition") != "sequential_b2":
        raise ProtocolValidationError("batch_same_qa_b2 must depend on sequential_b2")

    no_debate = _mapping(protocol.get("no_debate_references"), "no_debate_references")
    if no_debate.get("k") != 3:
        raise ProtocolValidationError("no-debate K must be 3")
    if no_debate.get("has_debater_dimension") is not False:
        raise ProtocolValidationError("no-debate references cannot have a debater dimension")
    if no_debate.get("has_transcript_dimension") is not False:
        raise ProtocolValidationError("no-debate references cannot have a transcript dimension")
    no_debate_conditions = _list(no_debate.get("conditions"),
                                 "no_debate_references.conditions")
    no_debate_ids = [
        _mapping(condition, "no-debate condition").get("id")
        for condition in no_debate_conditions
    ]
    expected_no_debate_ids = ["b0", "clean_b2", "placebo_b2"]
    if no_debate_ids != expected_no_debate_ids:
        raise ProtocolValidationError(
            f"no-debate conditions must be ordered as {expected_no_debate_ids!r}")

    unresolved = _mapping(protocol.get("unresolved"), "unresolved")
    expected_unresolved = {
        "primary_tests",
        "capability_measurement",
        "cap_protection_secondary",
        "query_screening",
        "design_scope_reconciliation",
        "secondary_analyses",
        "execution_semantics",
        "launch_gates",
        "cumulative_spend",
    }
    if set(unresolved) != expected_unresolved:
        raise ProtocolValidationError(
            f"unresolved fields must be exactly {sorted(expected_unresolved)!r}")
    for name in expected_unresolved:
        item = _mapping(unresolved[name], f"unresolved.{name}")
        if item.get("status") != "unresolved_requires_signoff":
            raise ProtocolValidationError(f"unresolved.{name} must require sign-off")
    required_nulls = {
        "primary_tests": ("exact_test_definitions",),
        "capability_measurement": (
            "question_set", "scoring_rule", "replicate_count", "freeze_and_exclusion_policy"),
        "cap_protection_secondary": (
            "exact_contrast", "cell_inventory", "sample_size", "multiplicity_handling"),
        "query_screening": (
            "model_checker_definition", "retry_and_slot_policy", "checker_failure_policy",
            "audit_sampling_policy"),
        "design_scope_reconciliation": (
            "empty_evidence_table_control_disposition",
            "full_document_ceiling_anchors_disposition",
            "matched_legacy_bridge_disposition"),
        "secondary_analyses": (
            "no_debate_D_definition", "no_debate_weighting_rule",
            "no_debate_inference_method", "no_debate_multiplicity_handling",
            "resolvability_labels_source", "resolvability_analysis_rule",
            "shortcut_audit_human_pass_disposition"),
        "execution_semantics": (
            "exact_prompt_bundle_and_hashes", "temperature_by_call_role",
            "max_output_tokens_by_call_role", "reasoning_settings_by_model_and_call_role",
            "seed_and_side_assignment_policy", "retry_and_regeneration_policy",
            "exact_placebo_payload", "batch_same_qa_construction_rule", "no_debate_prompt",
            "provider_endpoint_and_version_pins"),
        "launch_gates": (
            "invalid_rate_threshold", "side_bias_threshold_percentage_points",
            "canary_cell_inventory", "canary_sample_size", "gate_evaluation_method",
            "gate_failure_action"),
        "cumulative_spend": (
            "verified_starting_spend_usd", "durable_ledger_source",
            "cross_invocation_enforcement_policy", "project_wide_locking_policy",
            "concurrent_run_policy", "provider_reconciliation_policy",
            "cap_amendment_or_supplement_policy", "approved_cumulative_ceiling_usd"),
    }
    for section, fields in required_nulls.items():
        item = _mapping(unresolved[section], f"unresolved.{section}")
        for field in fields:
            if field not in item or item[field] is not None:
                raise ProtocolValidationError(
                    f"unresolved.{section}.{field} must be present and null")


def load_protocol(path: str | Path = DEFAULT_PROTOCOL_PATH) -> dict[str, Any]:
    protocol = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(protocol, dict):
        raise ProtocolValidationError("protocol root must be an object")
    validate_protocol(protocol)
    return protocol


def load_main_question_ids(protocol: Mapping[str, Any], project_root: str | Path) -> tuple[str, ...]:
    """Load tracked question banks, validate the 106 -> 82 exclusion, and return sorted IDs."""
    question_set = _mapping(protocol.get("question_set"), "question_set")
    source_paths = _unique_strings(question_set.get("question_sources"),
                                   "question_set.question_sources")
    all_ids: list[str] = []
    root = Path(project_root)
    for source in source_paths:
        payload = json.loads((root / source).read_text(encoding="utf-8"))
        entries = _list(payload, source)
        for index, entry in enumerate(entries):
            question = _mapping(entry, f"{source}[{index}]")
            question_id = question.get("id")
            if not isinstance(question_id, str) or not question_id:
                raise ProtocolValidationError(f"{source}[{index}].id must be a non-empty string")
            all_ids.append(question_id)

    duplicates = sorted(question_id for question_id, count in Counter(all_ids).items()
                        if count > 1)
    if duplicates:
        raise ProtocolValidationError(f"duplicate question IDs in source banks: {duplicates!r}")
    expected_total = int(question_set["expected_total_question_count"])
    if len(all_ids) != expected_total:
        raise ProtocolValidationError(
            f"question banks contain {len(all_ids)} IDs, expected {expected_total}")

    excluded = set(_unique_strings(question_set.get("calibration_excluded_question_ids"),
                                   "calibration_excluded_question_ids"))
    missing_exclusions = sorted(excluded.difference(all_ids))
    if missing_exclusions:
        raise ProtocolValidationError(
            f"calibration exclusions missing from question banks: {missing_exclusions!r}")
    main_ids = tuple(sorted(set(all_ids).difference(excluded)))
    expected_main = int(question_set["expected_main_question_count"])
    if len(main_ids) != expected_main:
        raise ProtocolValidationError(
            f"exclusion produced {len(main_ids)} main IDs, expected {expected_main}")
    return main_ids


def make_cell_key(
    namespace: str,
    *,
    kind: str,
    condition: str,
    question_id: str,
    judge_model: str | None,
    debater_model: str | None,
    transcript_index: int | None,
    replicate_index: int | None,
    query_budget: int | None,
) -> str:
    """Return a stable SHA-256 key over the complete scheduling identity."""
    identity = {
        "namespace": namespace,
        "kind": kind,
        "condition": condition,
        "question_id": question_id,
        "judge_model": judge_model,
        "debater_model": debater_model,
        "transcript_index": transcript_index,
        "replicate_index": replicate_index,
        "query_budget": query_budget,
    }
    canonical = json.dumps(identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{namespace}:{kind}:{digest}"


def _cell(
    namespace: str,
    *,
    kind: str,
    condition: str,
    question_id: str,
    judge_model: str | None,
    debater_model: str | None,
    transcript_index: int | None,
    replicate_index: int | None,
    query_budget: int | None,
    dependency_keys: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "cell_key": make_cell_key(
            namespace,
            kind=kind,
            condition=condition,
            question_id=question_id,
            judge_model=judge_model,
            debater_model=debater_model,
            transcript_index=transcript_index,
            replicate_index=replicate_index,
            query_budget=query_budget,
        ),
        "kind": kind,
        "condition": condition,
        "question_id": question_id,
        "judge_model": judge_model,
        "debater_model": debater_model,
        "transcript_index": transcript_index,
        "replicate_index": replicate_index,
        "query_budget": query_budget,
        "dependency_keys": list(dependency_keys),
    }


def enumerate_cells(
    protocol: Mapping[str, Any], main_question_ids: Iterable[str]
) -> list[dict[str, Any]]:
    """Enumerate transcript generation, debate judgments, and no-debate judgments."""
    validate_protocol(protocol)
    question_ids = tuple(sorted(main_question_ids))
    if len(question_ids) != len(set(question_ids)):
        raise PlanValidationError("main_question_ids contains duplicates")
    expected_questions = int(_mapping(protocol["question_set"], "question_set")
                             ["expected_main_question_count"])
    if len(question_ids) != expected_questions:
        raise PlanValidationError(
            f"received {len(question_ids)} main question IDs, expected {expected_questions}")
    excluded = set(_mapping(protocol["question_set"], "question_set")
                   ["calibration_excluded_question_ids"])
    leaked = sorted(excluded.intersection(question_ids))
    if leaked:
        raise PlanValidationError(f"calibration IDs leaked into main plan: {leaked!r}")

    namespace = str(protocol["cell_key_namespace"])
    question_set = _mapping(protocol["question_set"], "question_set")
    transcripts_per_question = int(question_set["transcripts_per_question_per_debater"])
    roster = _mapping(protocol["roster"], "roster")
    judges = list(roster["judges"])
    debaters = list(roster["debaters"])
    debate_name = str(_mapping(protocol["debate_protocol"], "debate_protocol")["name"])
    debate_grid = _mapping(protocol["debate_grid"], "debate_grid")
    debate_k = int(debate_grid["k"])
    debate_conditions = [
        _mapping(condition, "debate condition") for condition in debate_grid["conditions"]
    ]
    debate_by_id = {str(condition["id"]): condition for condition in debate_conditions}
    no_debate = _mapping(protocol["no_debate_references"], "no_debate_references")
    no_debate_k = int(no_debate["k"])
    no_debate_conditions = [
        _mapping(condition, "no-debate condition") for condition in no_debate["conditions"]
    ]

    cells: list[dict[str, Any]] = []
    transcript_keys: dict[tuple[str, str, int], str] = {}
    for debater_model in debaters:
        for question_id in question_ids:
            for transcript_index in range(transcripts_per_question):
                transcript = _cell(
                    namespace,
                    kind="debate_transcript",
                    condition=debate_name,
                    question_id=question_id,
                    judge_model=None,
                    debater_model=debater_model,
                    transcript_index=transcript_index,
                    replicate_index=None,
                    query_budget=None,
                )
                transcript_keys[(debater_model, question_id, transcript_index)] = transcript[
                    "cell_key"
                ]
                cells.append(transcript)

    for judge_model in judges:
        for debater_model in debaters:
            for question_id in question_ids:
                for transcript_index in range(transcripts_per_question):
                    transcript_key = transcript_keys[(debater_model, question_id, transcript_index)]
                    for condition in debate_conditions:
                        condition_id = str(condition["id"])
                        query_budget = int(condition["query_budget"])
                        for replicate_index in range(debate_k):
                            dependency_keys = [transcript_key]
                            dependency_condition = condition.get("depends_on_condition")
                            if dependency_condition is not None:
                                dependency = debate_by_id[str(dependency_condition)]
                                dependency_keys.append(make_cell_key(
                                    namespace,
                                    kind="debate_judgment",
                                    condition=str(dependency["id"]),
                                    question_id=question_id,
                                    judge_model=judge_model,
                                    debater_model=debater_model,
                                    transcript_index=transcript_index,
                                    replicate_index=replicate_index,
                                    query_budget=int(dependency["query_budget"]),
                                ))
                            cells.append(_cell(
                                namespace,
                                kind="debate_judgment",
                                condition=condition_id,
                                question_id=question_id,
                                judge_model=judge_model,
                                debater_model=debater_model,
                                transcript_index=transcript_index,
                                replicate_index=replicate_index,
                                query_budget=query_budget,
                                dependency_keys=dependency_keys,
                            ))

    for judge_model in judges:
        for question_id in question_ids:
            for condition in no_debate_conditions:
                for replicate_index in range(no_debate_k):
                    cells.append(_cell(
                        namespace,
                        kind="no_debate_judgment",
                        condition=str(condition["id"]),
                        question_id=question_id,
                        judge_model=judge_model,
                        debater_model=None,
                        transcript_index=None,
                        replicate_index=replicate_index,
                        query_budget=int(condition["query_budget"]),
                    ))

    validate_cells(cells, namespace)
    return cells


def duplicate_cell_keys(cells: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    counts = Counter(str(cell.get("cell_key")) for cell in cells)
    return tuple(sorted(key for key, count in counts.items() if count > 1))


def validate_cells(cells: Sequence[Mapping[str, Any]], namespace: str) -> None:
    """Reject duplicate keys, unstable keys, missing dependencies, and bad batch links."""
    duplicates = duplicate_cell_keys(cells)
    if duplicates:
        raise PlanValidationError(f"duplicate cell keys: {duplicates[:5]!r}")
    by_key = {str(cell["cell_key"]): cell for cell in cells}
    for cell in cells:
        expected_key = make_cell_key(
            namespace,
            kind=str(cell["kind"]),
            condition=str(cell["condition"]),
            question_id=str(cell["question_id"]),
            judge_model=cell.get("judge_model"),
            debater_model=cell.get("debater_model"),
            transcript_index=cell.get("transcript_index"),
            replicate_index=cell.get("replicate_index"),
            query_budget=cell.get("query_budget"),
        )
        if cell["cell_key"] != expected_key:
            raise PlanValidationError(f"unstable or malformed cell key: {cell['cell_key']!r}")
        dependencies = cell.get("dependency_keys")
        if not isinstance(dependencies, list) or not all(isinstance(key, str) for key in dependencies):
            raise PlanValidationError(f"invalid dependency list for {cell['cell_key']}")
        missing = [key for key in dependencies if key not in by_key]
        if missing:
            raise PlanValidationError(
                f"missing dependencies for {cell['cell_key']}: {missing!r}")

        if cell["kind"] == "debate_judgment":
            transcript_dependencies = [
                by_key[key] for key in dependencies if by_key[key]["kind"] == "debate_transcript"
            ]
            if len(transcript_dependencies) != 1:
                raise PlanValidationError(
                    f"debate judgment {cell['cell_key']} needs exactly one transcript dependency")
            transcript = transcript_dependencies[0]
            for field in ("question_id", "debater_model", "transcript_index"):
                if transcript[field] != cell[field]:
                    raise PlanValidationError(
                        f"transcript dependency dimension mismatch for {cell['cell_key']}")

        if cell["condition"] == "batch_same_qa_b2":
            sequential_dependencies = [
                by_key[key] for key in dependencies
                if by_key[key]["kind"] == "debate_judgment"
                and by_key[key]["condition"] == "sequential_b2"
            ]
            if len(sequential_dependencies) != 1:
                raise PlanValidationError(
                    f"batch cell {cell['cell_key']} needs exactly one sequential_b2 dependency")
            sequential = sequential_dependencies[0]
            for field in (
                "question_id", "judge_model", "debater_model", "transcript_index",
                "replicate_index",
            ):
                if sequential[field] != cell[field]:
                    raise PlanValidationError(
                        f"batch dependency dimension mismatch for {cell['cell_key']}")

        if cell["kind"] == "no_debate_judgment":
            if cell.get("debater_model") is not None or cell.get("transcript_index") is not None:
                raise PlanValidationError(
                    f"no-debate cell {cell['cell_key']} has a debate-only dimension")


def summarize_cells(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_kind = Counter(str(cell["kind"]) for cell in cells)
    debate_by_condition = Counter(
        str(cell["condition"]) for cell in cells if cell["kind"] == "debate_judgment"
    )
    no_debate_by_condition = Counter(
        str(cell["condition"]) for cell in cells if cell["kind"] == "no_debate_judgment"
    )
    batch_cells = [cell for cell in cells if cell["condition"] == "batch_same_qa_b2"]
    judgment_cells = by_kind["debate_judgment"] + by_kind["no_debate_judgment"]
    return {
        "all_cells": len(cells),
        "judgment_cells": judgment_cells,
        "debate_transcript_cells": by_kind["debate_transcript"],
        "debate_judgment_cells": by_kind["debate_judgment"],
        "no_debate_judgment_cells": by_kind["no_debate_judgment"],
        "by_kind": dict(sorted(by_kind.items())),
        "debate_judgments_by_condition": dict(sorted(debate_by_condition.items())),
        "no_debate_judgments_by_condition": dict(sorted(no_debate_by_condition.items())),
        "batch_to_sequential_dependency_edges": len(batch_cells),
        "dependency_edges_total": sum(len(cell["dependency_keys"]) for cell in cells),
    }


def build_plan(
    protocol: Mapping[str, Any], main_question_ids: Iterable[str]
) -> dict[str, Any]:
    question_ids = tuple(sorted(main_question_ids))
    cells = enumerate_cells(protocol, question_ids)
    summary = summarize_cells(cells)

    question_set = _mapping(protocol["question_set"], "question_set")
    roster = _mapping(protocol["roster"], "roster")
    debate_grid = _mapping(protocol["debate_grid"], "debate_grid")
    no_debate = _mapping(protocol["no_debate_references"], "no_debate_references")
    n_questions = len(question_ids)
    n_judges = len(roster["judges"])
    n_debaters = len(roster["debaters"])
    n_transcripts = int(question_set["transcripts_per_question_per_debater"])
    expected_transcripts = n_questions * n_debaters * n_transcripts
    expected_debate_judgments = (
        n_questions * n_judges * n_debaters * n_transcripts
        * len(debate_grid["conditions"]) * int(debate_grid["k"])
    )
    expected_no_debate_judgments = (
        n_questions * n_judges * len(no_debate["conditions"]) * int(no_debate["k"])
    )
    expected = {
        "debate_transcript_cells": expected_transcripts,
        "debate_judgment_cells": expected_debate_judgments,
        "no_debate_judgment_cells": expected_no_debate_judgments,
    }
    for field, count in expected.items():
        if summary[field] != count:
            raise PlanValidationError(
                f"{field} count mismatch: enumerated {summary[field]}, expected {count}")

    return {
        "schema_version": protocol["schema_version"],
        "protocol_id": protocol["protocol_id"],
        "status": protocol["status"],
        "offline_planning_only": True,
        "execution_authorized": False,
        "main_question_ids": list(question_ids),
        "summary": {"main_question_count": n_questions, **summary},
        "unresolved": protocol["unresolved"],
        "protocol": protocol,
        "cells": cells,
    }


def build_plan_from_paths(
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    protocol_path = Path(protocol_path)
    protocol = load_protocol(protocol_path)
    root = Path(project_root) if project_root is not None else protocol_path.resolve().parent.parent
    question_ids = load_main_question_ids(protocol, root)
    return build_plan(protocol, question_ids)


def _summary_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": plan["schema_version"],
        "protocol_id": plan["protocol_id"],
        "status": plan["status"],
        "offline_planning_only": plan["offline_planning_only"],
        "execution_authorized": plan["execution_authorized"],
        "summary": plan["summary"],
        "unresolved": plan["unresolved"],
    }


def format_summary(plan: Mapping[str, Any]) -> str:
    summary = _mapping(plan["summary"], "summary")
    unresolved = _mapping(plan["unresolved"], "unresolved")
    lines = [
        f"Phase 2 plan: {plan['status']}",
        "Execution authorized: no (offline planner only)",
        f"Main questions: {summary['main_question_count']}",
        f"Debate transcripts: {summary['debate_transcript_cells']}",
        f"Debate judgments: {summary['debate_judgment_cells']}",
        f"No-debate judgments: {summary['no_debate_judgment_cells']}",
        f"Total planned cells: {summary['all_cells']}",
        f"Batch -> sequential dependencies: {summary['batch_to_sequential_dependency_edges']}",
        "Unresolved before sign-off: " + ", ".join(sorted(unresolved)),
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the deterministic, offline-only draft Phase-2 cell plan.")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
    parser.add_argument(
        "--project-root", type=Path, default=None,
        help="Root used to resolve question source files (defaults to the protocol's repo root).")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--summary-only", action="store_true",
        help="With --json, omit the full cell inventory and print metadata/counts only.")
    args = parser.parse_args(argv)
    if args.summary_only and not args.json:
        print("--summary-only requires --json", file=sys.stderr)
        return 2
    try:
        plan = build_plan_from_paths(args.protocol, args.project_root)
    except (OSError, json.JSONDecodeError, ProtocolValidationError, PlanValidationError) as exc:
        print(f"phase2 plan error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = _summary_payload(plan) if args.summary_only else plan
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_summary(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
