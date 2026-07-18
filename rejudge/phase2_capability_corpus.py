"""Deterministic, offline renderer for the Phase 2 capability_qa preflight corpus.

Renders the frozen ``capability_qa`` system+user templates from
``rejudge/phase2_prompt_bundle.json`` against the real world documents (``world_specs/*.txt``)
and all 106 frozen questions (``questions/*.json``), for BOTH K=2 mirrored label assignments
(candidates swapped), producing exactly 212 message sets identical across models.

Pure function of tracked, in-repo sources only: no network, no provider imports, no
randomness. The question-id set reproduces ``rejudge.phase2_plan.enumerate_cells``'s own
capability_qa question set (the sorted union of the 82 main IDs and the calibration-excluded
IDs -- "all_106" per ``decisions.capability_measurement.question_set``) so this module can
never silently diverge from the frozen plan's own cell inventory.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rejudge import phase2_plan


DEFAULT_PROTOCOL_PATH = phase2_plan.DEFAULT_PROTOCOL_PATH
CAPABILITY_TEMPLATE_NAME = "capability_qa"
SIDES: tuple[str, ...] = ("A", "B")
EXPECTED_QUESTION_COUNT = 106
EXPECTED_REPLICATE_COUNT = 2
EXPECTED_ENTRY_COUNT = EXPECTED_QUESTION_COUNT * EXPECTED_REPLICATE_COUNT
CORPUS_ENTRY_KEYS: frozenset[str] = frozenset(
    {"question_id", "world", "side", "system_prompt", "user_prompt"})


class CapabilityCorpusError(ValueError):
    """The capability_qa corpus could not be rendered from frozen, in-repo inputs."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CapabilityCorpusError(f"{label} must be an object")
    return value


def _non_empty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise CapabilityCorpusError(f"{label} must be a non-empty string")
    return value


def _load_all_question_records(
    protocol: Mapping[str, Any], root: Path,
) -> dict[str, dict[str, Any]]:
    """Load every question in the frozen three question banks, keyed by id (no exclusion)."""
    question_set = _mapping(protocol.get("question_set"), "question_set")
    source_paths = question_set.get("question_sources")
    if not isinstance(source_paths, list) or not source_paths:
        raise CapabilityCorpusError("question_set.question_sources must be a non-empty list")
    records: dict[str, dict[str, Any]] = {}
    for source in source_paths:
        payload = json.loads((root / str(source)).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise CapabilityCorpusError(f"{source} must be a JSON array")
        for index, entry in enumerate(payload):
            question = _mapping(entry, f"{source}[{index}]")
            question_id = _non_empty_str(question.get("id"), f"{source}[{index}].id")
            if question_id in records:
                raise CapabilityCorpusError(
                    f"duplicate question id across banks: {question_id!r}")
            records[question_id] = dict(question)
    expected_total = int(question_set["expected_total_question_count"])
    if len(records) != expected_total:
        raise CapabilityCorpusError(
            f"question banks contain {len(records)} IDs, expected {expected_total}")
    return records


def all_106_question_ids(protocol: Mapping[str, Any], root: str | Path) -> tuple[str, ...]:
    """Reproduce ``phase2_plan.enumerate_cells``'s exact capability question-id set.

    The sorted union of the 82 main question IDs and the frozen calibration-excluded IDs.
    Fails closed if the frozen protocol's own capability_measurement.question_set is not
    literally ``"all_106"``, or if the union does not land on exactly the frozen total.
    """
    root = Path(root)
    main_ids = set(phase2_plan.load_main_question_ids(protocol, root))
    question_set = _mapping(protocol.get("question_set"), "question_set")
    excluded_ids = set(question_set.get("calibration_excluded_question_ids") or [])
    all_ids = tuple(sorted(main_ids | excluded_ids))

    decisions = _mapping(protocol.get("decisions"), "decisions")
    capability = _mapping(decisions.get("capability_measurement"), "decisions.capability_measurement")
    if capability.get("question_set") != "all_106":
        raise CapabilityCorpusError(
            "frozen protocol decisions.capability_measurement.question_set drifted from "
            "'all_106'; this module's question-id derivation must be revisited")
    expected_total = int(question_set["expected_total_question_count"])
    if len(all_ids) != expected_total:
        raise CapabilityCorpusError(
            f"capability question id union has {len(all_ids)} ids, expected {expected_total}")
    return all_ids


def _world_document_text(world: str, root: Path) -> str:
    path = root / "world_specs" / f"{world}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapabilityCorpusError(f"could not read world document for {world!r}: {exc}") from exc


def _side_candidates(question: Mapping[str, Any], side: str, label: str) -> tuple[str, str]:
    correct = _non_empty_str(question.get("correct_answer"), f"{label}.correct_answer")
    wrong = _non_empty_str(question.get("wrong_answer"), f"{label}.wrong_answer")
    if side == "A":
        return correct, wrong
    if side == "B":
        return wrong, correct
    raise CapabilityCorpusError(f"unknown side: {side!r}")


def render_capability_corpus(
    bundle: Mapping[str, Any], protocol: Mapping[str, Any], root: str | Path,
) -> list[dict[str, Any]]:
    """Render the frozen capability_qa template against all 106 questions x K2 mirrored sides.

    Returns exactly 212 entries in a fixed, deterministic order (ascending question_id, then
    side "A" then "B"), each an object with exactly ``CORPUS_ENTRY_KEYS``. The system prompt
    is copied verbatim (it has no placeholders); the user prompt is the template rendered with
    the real world document, question, and mirrored candidate texts. Deterministic and
    offline: touches only tracked JSON/txt sources, never a provider.
    """
    root = Path(root)
    templates = _mapping(bundle.get("templates"), "templates")
    template = _mapping(
        templates.get(CAPABILITY_TEMPLATE_NAME), f"templates.{CAPABILITY_TEMPLATE_NAME}")
    if template.get("active") is not True:
        raise CapabilityCorpusError(f"templates.{CAPABILITY_TEMPLATE_NAME} must be active")
    system_prompt = _non_empty_str(
        template.get("system_prompt"), f"templates.{CAPABILITY_TEMPLATE_NAME}.system_prompt")
    user_template = _non_empty_str(
        template.get("user_prompt_template"),
        f"templates.{CAPABILITY_TEMPLATE_NAME}.user_prompt_template")

    question_ids = all_106_question_ids(protocol, root)
    records = _load_all_question_records(protocol, root)
    missing = [qid for qid in question_ids if qid not in records]
    if missing:
        raise CapabilityCorpusError(f"question ids missing from source banks: {missing!r}")

    world_cache: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    for question_id in question_ids:
        question = records[question_id]
        label = f"question {question_id}"
        world = _non_empty_str(question.get("world"), f"{label}.world")
        if world not in world_cache:
            world_cache[world] = _world_document_text(world, root)
        world_document = world_cache[world]
        question_text = _non_empty_str(question.get("question"), f"{label}.question")
        for side in SIDES:
            candidate_a, candidate_b = _side_candidates(question, side, label)
            try:
                user_prompt = user_template.format(
                    world_document=world_document,
                    question=question_text,
                    candidate_a=candidate_a,
                    candidate_b=candidate_b,
                )
            except (KeyError, IndexError, ValueError) as exc:
                raise CapabilityCorpusError(
                    f"failed to render capability_qa user_prompt_template for {label}: {exc}"
                ) from exc
            entries.append({
                "question_id": question_id,
                "world": world,
                "side": side,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            })
    if len(entries) != EXPECTED_ENTRY_COUNT:
        raise CapabilityCorpusError(
            f"rendered {len(entries)} capability_qa entries, expected {EXPECTED_ENTRY_COUNT}")
    return entries


def corpus_canonical_sha256(entries: list[dict[str, Any]]) -> str:
    """Canonical-hash the rendered corpus (JSON semantics, order-preserving as a list)."""
    return phase2_plan.canonical_sha256(entries)


def load_and_render(
    root: str | Path,
    bundle_path: str | Path | None = None,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Load and validate the frozen bundle+protocol, then render the capability_qa corpus."""
    from rejudge import phase2_prompt_bundle  # local import: keeps this module import-light

    bundle_kwargs: dict[str, Any] = {}
    if bundle_path is not None:
        bundle_kwargs["bundle_path"] = bundle_path
    bundle, protocol = phase2_prompt_bundle.load_and_validate(
        protocol_path=protocol_path, **bundle_kwargs)
    entries = render_capability_corpus(bundle, protocol, root)
    return entries, bundle, protocol
