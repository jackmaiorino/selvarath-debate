"""Materialize and validate the phase-3 main review-capacity workload.

This module is deliberately offline. It reads the sealed successor-canary reviewer packets,
constructs exact-byte-new candidate-order variants, and can write packet files for a later
capacity check. It never launches Codex, a reviewer, or any provider client.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rejudge.phase2_dual_gate import parse_reviewer_output  # noqa: E402
from rejudge.run_manifest import OutputLockedError, output_lock  # noqa: E402


PLAN_PATH_DEFAULT = (
    REPO_ROOT / "rejudge" / "phase3_main_review_capacity_preflight_plan_2026-08-29.json"
)
FINALIZATION_PATH_DEFAULT = (
    REPO_ROOT / "rejudge" / "phase3_v3_finalization_record_2026-08-29.json"
)
ARCHIVE_DIR_DEFAULT = Path("E:/selvarath-archive/phase3-v3r15-clean-2026-08-29")

SCHEMA_VERSION_V1 = "phase3_main_review_capacity_preflight_plan_v1"
SCHEMA_VERSION_V2 = "phase3_main_review_capacity_preflight_plan_v2"
SCHEMA_VERSION_V3 = "phase3_main_review_capacity_preflight_plan_v3"
SCHEMA_VERSION_V4 = "phase3_main_review_capacity_preflight_plan_v4"
SCHEMA_VERSION = SCHEMA_VERSION_V1
MATERIALIZATION_SCHEMA_VERSION = "phase3_main_review_capacity_workload_v1"
RESULT_SCHEMA_VERSION = "phase3_main_review_capacity_result_v1"
DISPATCH_HISTORY_SCHEMA_VERSION = "phase3_main_review_capacity_dispatch_event_v1"
DISPATCH_ANCHOR_SCHEMA_VERSION = "phase3_main_review_capacity_dispatch_anchor_v1"
DISPATCH_APPEND_INTENT_SCHEMA_VERSION = (
    "phase3_main_review_capacity_dispatch_append_intent_v1"
)
DISPATCH_INITIALIZATION_SCHEMA_VERSION = (
    "phase3_main_review_capacity_dispatch_initialization_v1"
)
DERIVATION_TAG_V1 = "phase3-main-review-capacity-v1"
DERIVATION_TAG_V2 = "phase3-main-review-capacity-v2"
DERIVATION_TAG_V3 = "phase3-main-review-capacity-v3"
DERIVATION_TAG_V4 = "phase3-main-review-capacity-v4"
DERIVATION_TAG_V5 = "phase3-main-review-capacity-v5"
DERIVATION_TAG = DERIVATION_TAG_V1
EXPECTED_PLAN_CANONICAL_SHA256 = "0805888c9f99b27999c82b4038f6be5498ceee22baf86703b4a1339e80988ec3"
EXPECTED_PLAN_CANONICAL_SHA256_V2 = (
    "ee14a65c8a2810480a5613720420fedfd486030b4c66d02ef9f1c36638155c5a"
)
EXPECTED_PLAN_CANONICAL_SHA256_V3 = (
    "51855e21123799950ff0daaf7803dc30e02ab6a1744a0eea1dc665eef09421d3"
)
EXPECTED_PLAN_CANONICAL_SHA256_V4 = (
    "ef9121b49b1c62ff61ceceb2de376faaac406f48eef86c20bde705f3e58a8f2e"
)
PAYLOAD_SEPARATOR = "\n\n=== QUERY PAYLOAD ===\n"
_PAYLOAD_RE = re.compile(
    r"QUERY: (?P<query>.*?)\r?\nCANDIDATE A: (?P<candidate_a>.*?)"
    r"\r?\nCANDIDATE B: (?P<candidate_b>.*)",
    flags=re.DOTALL,
)


class CapacityPreflightError(ValueError):
    """The sealed source or capacity plan does not satisfy the offline contract."""


class InjectedCapacityFault(RuntimeError):
    """Test-only deterministic process-boundary fault."""


def raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def payload_sha256(query: str, candidate_a: str, candidate_b: str) -> str:
    canonical = json.dumps(
        {"query": query, "candidate_a": candidate_a, "candidate_b": candidate_b},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapacityPreflightError(f"cannot load JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CapacityPreflightError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CapacityPreflightError(f"cannot load JSONL {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line:
            raise CapacityPreflightError(f"blank JSONL row at {path}:{line_number}")
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CapacityPreflightError(
                f"invalid JSONL row at {path}:{line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise CapacityPreflightError(f"non-object JSONL row at {path}:{line_number}")
        rows.append(row)
    return rows


@dataclass(frozen=True)
class SourcePacket:
    source_payload_sha256: str
    source_prompt_sha256: str
    query: str
    candidate_a: str
    candidate_b: str
    prompt_prefix: str


@dataclass(frozen=True)
class VariantPacket:
    rank_sha256: str
    source_payload_sha256: str
    source_prompt_sha256: str
    variant_payload_sha256: str
    variant_prompt_sha256: str
    prompt: str


@dataclass(frozen=True)
class SourceSnapshot:
    finalization_record_sha256: str
    decision_store_sha256: str
    reviewer_index_sha256: str
    source_packets: tuple[SourcePacket, ...]
    historical_prompt_hashes: frozenset[str]
    prompt_prefix_sha256: str
    packet_directories: tuple[str, ...]


@dataclass(frozen=True)
class DerivedWorkload:
    selected: tuple[VariantPacket, ...]
    eligible: tuple[VariantPacket, ...]
    collision_source_payloads: tuple[str, ...]
    summary: Mapping[str, Any]
    retry_selected: tuple[VariantPacket, ...] = ()


@dataclass(frozen=True)
class DispatchHistorySnapshot:
    raw_sha256: str
    events: tuple[Mapping[str, Any], ...]
    prefix_raw_sha256s: tuple[str, ...] = ()
    prefix_byte_counts: tuple[int, ...] = ()


def derivation_tag_from_plan(plan: Mapping[str, Any]) -> str:
    workload = plan.get("workload")
    if not isinstance(workload, Mapping):
        raise CapacityPreflightError("plan lacks workload")
    derivation_tag = workload.get("derivation_tag")
    if derivation_tag not in {
        DERIVATION_TAG_V1,
        DERIVATION_TAG_V2,
        DERIVATION_TAG_V3,
        DERIVATION_TAG_V4,
        DERIVATION_TAG_V5,
    }:
        raise CapacityPreflightError("plan has an unsupported capacity derivation tag")
    return cast(str, derivation_tag)


def _parse_prompt(prompt_bytes: bytes, *, source: Path) -> tuple[str, str, str, str]:
    try:
        prompt = prompt_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CapacityPreflightError(f"packet is not UTF-8: {source}") from exc
    split_at = prompt.rfind(PAYLOAD_SEPARATOR)
    if split_at < 0:
        raise CapacityPreflightError(f"packet lacks the frozen payload separator: {source}")
    prefix_end = split_at + len(PAYLOAD_SEPARATOR)
    prefix = prompt[:prefix_end]
    match = _PAYLOAD_RE.fullmatch(prompt[prefix_end:])
    if match is None:
        raise CapacityPreflightError(f"packet payload has an unexpected shape: {source}")
    return (
        prefix,
        match.group("query"),
        match.group("candidate_a"),
        match.group("candidate_b"),
    )


def _require_sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CapacityPreflightError(f"{field} must be a lowercase SHA-256")
    return value


def _require_safe_path_component(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or Path(value).name != value
        or "/" in value
        or "\\" in value
    ):
        raise CapacityPreflightError(f"{field} must be one safe path component")
    return value


def collect_source_snapshot(
    *, archive_dir: Path, finalization_path: Path
) -> SourceSnapshot:
    finalization = _load_json_object(finalization_path)
    declared_archive = Path(str(finalization.get("archive_path", "")))
    if declared_archive.resolve() != archive_dir.resolve():
        raise CapacityPreflightError("finalization record names a different archive")
    file_manifest = finalization.get("archive_file_manifest")
    if not isinstance(file_manifest, dict):
        raise CapacityPreflightError("finalization record lacks archive_file_manifest")

    decisions_path = archive_dir / "phase3_v3_reviewer_decisions.jsonl"
    reviewer_index_path = archive_dir / "reviewer_index.jsonl"
    declared_decisions_sha = _require_sha(
        (file_manifest.get(decisions_path.name) or {}).get("sha256"),
        field="finalization decision-store sha256",
    )
    declared_index_sha = _require_sha(
        (file_manifest.get(reviewer_index_path.name) or {}).get("sha256"),
        field="finalization reviewer-index sha256",
    )
    actual_decisions_sha = raw_sha256(decisions_path)
    actual_index_sha = raw_sha256(reviewer_index_path)
    if actual_decisions_sha != declared_decisions_sha:
        raise CapacityPreflightError("sealed reviewer decision store hash mismatch")
    if actual_index_sha != declared_index_sha:
        raise CapacityPreflightError("sealed reviewer index hash mismatch")

    decision_rows = _load_jsonl(decisions_path)
    decided_payloads: set[str] = set()
    for row_number, row in enumerate(decision_rows, 1):
        value = _require_sha(
            row.get("payload_sha256"), field=f"decision row {row_number} payload_sha256"
        )
        if value in decided_payloads:
            raise CapacityPreflightError(f"duplicate decided payload {value}")
        decided_payloads.add(value)

    index_rows = _load_jsonl(reviewer_index_path)
    directory_names: list[str] = []
    expected_count_by_directory: dict[str, int] = {}
    for row_number, row in enumerate(index_rows, 1):
        names = row.get("new_packet_directories")
        payload_count = row.get("payload_count")
        if not isinstance(names, list) or len(names) != 1:
            raise CapacityPreflightError(
                f"reviewer index row {row_number} must name one safe packet directory"
            )
        if (
            not isinstance(payload_count, int)
            or isinstance(payload_count, bool)
            or payload_count < 1
        ):
            raise CapacityPreflightError(
                f"reviewer index row {row_number} has invalid payload_count"
            )
        name = _require_safe_path_component(
            names[0], field=f"reviewer index row {row_number} packet directory"
        )
        if name in expected_count_by_directory:
            raise CapacityPreflightError(f"duplicate packet directory in reviewer index: {name}")
        directory_names.append(name)
        expected_count_by_directory[name] = payload_count

    packets: list[SourcePacket] = []
    historical_prompt_hashes: set[str] = set()
    source_payloads: set[str] = set()
    prompt_prefix_hashes: set[str] = set()
    for directory_name in directory_names:
        packet_dir = archive_dir / directory_name
        index = _load_json_object(packet_dir / "INDEX.json")
        items = index.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise CapacityPreflightError(f"packet index has invalid items: {packet_dir}")
        typed_items = cast(list[dict[str, Any]], items)
        if index.get("count") != len(typed_items):
            raise CapacityPreflightError(f"packet index count mismatch: {packet_dir}")
        if len(typed_items) != expected_count_by_directory[directory_name]:
            raise CapacityPreflightError(f"reviewer index payload count mismatch: {packet_dir}")
        for item_number, item in enumerate(typed_items, 1):
            file_name = _require_safe_path_component(
                item.get("file"),
                field=f"{packet_dir / 'INDEX.json'} item {item_number} filename",
            )
            source_payload = _require_sha(
                item.get("payload_sha256"), field=f"packet item {item_number} payload_sha256"
            )
            source_prompt = _require_sha(
                item.get("prompt_sha256"), field=f"packet item {item_number} prompt_sha256"
            )
            packet_path = packet_dir / file_name
            prompt_bytes = packet_path.read_bytes()
            if hashlib.sha256(prompt_bytes).hexdigest() != source_prompt:
                raise CapacityPreflightError(f"packet prompt hash mismatch: {packet_path}")
            prefix, query, candidate_a, candidate_b = _parse_prompt(
                prompt_bytes, source=packet_path
            )
            observed_payload = payload_sha256(query, candidate_a, candidate_b)
            if observed_payload != source_payload:
                raise CapacityPreflightError(f"packet payload hash mismatch: {packet_path}")
            if source_payload not in decided_payloads:
                raise CapacityPreflightError(
                    f"packet payload is absent from sealed decision store: {source_payload}"
                )
            if source_payload in source_payloads:
                raise CapacityPreflightError(f"duplicate source payload {source_payload}")
            if source_prompt in historical_prompt_hashes:
                raise CapacityPreflightError(f"duplicate historical prompt {source_prompt}")
            source_payloads.add(source_payload)
            historical_prompt_hashes.add(source_prompt)
            prompt_prefix_hashes.add(hashlib.sha256(prefix.encode("utf-8")).hexdigest())
            packets.append(
                SourcePacket(
                    source_payload_sha256=source_payload,
                    source_prompt_sha256=source_prompt,
                    query=query,
                    candidate_a=candidate_a,
                    candidate_b=candidate_b,
                    prompt_prefix=prefix,
                )
            )

    if source_payloads != decided_payloads:
        missing = len(decided_payloads - source_payloads)
        extra = len(source_payloads - decided_payloads)
        raise CapacityPreflightError(
            f"packet/decision payload partition mismatch: missing={missing}, extra={extra}"
        )
    if len(prompt_prefix_hashes) != 1:
        raise CapacityPreflightError("source packets do not share one frozen reviewer prefix")
    return SourceSnapshot(
        finalization_record_sha256=raw_sha256(finalization_path),
        decision_store_sha256=actual_decisions_sha,
        reviewer_index_sha256=actual_index_sha,
        source_packets=tuple(packets),
        historical_prompt_hashes=frozenset(historical_prompt_hashes),
        prompt_prefix_sha256=next(iter(prompt_prefix_hashes)),
        packet_directories=tuple(directory_names),
    )


def derive_workload(
    snapshot: SourceSnapshot,
    *,
    selection_count: int = 180,
    wave_size: int = 60,
    cohort_count: int = 2,
    derivation_tag: str = DERIVATION_TAG,
) -> DerivedWorkload:
    if (
        selection_count < 1
        or wave_size < 1
        or selection_count % wave_size
        or cohort_count not in {1, 2}
    ):
        raise CapacityPreflightError("selection_count must be a positive multiple of wave_size")
    if derivation_tag not in {
        DERIVATION_TAG_V1,
        DERIVATION_TAG_V2,
        DERIVATION_TAG_V3,
        DERIVATION_TAG_V4,
        DERIVATION_TAG_V5,
    }:
        raise CapacityPreflightError("unsupported capacity derivation tag")

    def v1_variant(source: SourcePacket) -> tuple[str, str]:
        rendered = (
            source.prompt_prefix
            + f"QUERY: {source.query}\n"
            + f"CANDIDATE A: {source.candidate_b}\n"
            + f"CANDIDATE B: {source.candidate_a}"
        )
        return (
            payload_sha256(source.query, source.candidate_b, source.candidate_a),
            rendered,
        )

    def v2_variant(source: SourcePacket) -> tuple[str, str]:
        rendered = (
            source.prompt_prefix
            + f"QUERY: {source.query}\n"
            + f"CANDIDATE B: {source.candidate_b}\n"
            + f"CANDIDATE A: {source.candidate_a}"
        )
        return (
            payload_sha256(source.query, source.candidate_a, source.candidate_b),
            rendered,
        )

    def v3_variant(source: SourcePacket) -> tuple[str, str]:
        rendered = (
            source.prompt_prefix
            + f"CANDIDATE A: {source.candidate_a}\n"
            + f"CANDIDATE B: {source.candidate_b}\n"
            + f"QUERY: {source.query}"
        )
        return (
            payload_sha256(source.query, source.candidate_a, source.candidate_b),
            rendered,
        )

    def v4_variant(source: SourcePacket) -> tuple[str, str]:
        rendered = (
            source.prompt_prefix
            + f"CANDIDATE A: {source.candidate_a}\n"
            + f"QUERY: {source.query}\n"
            + f"CANDIDATE B: {source.candidate_b}"
        )
        return (
            payload_sha256(source.query, source.candidate_a, source.candidate_b),
            rendered,
        )

    def v5_variant(source: SourcePacket) -> tuple[str, str]:
        rendered = (
            source.prompt_prefix
            + f"CANDIDATE B: {source.candidate_b}\n"
            + f"QUERY: {source.query}\n"
            + f"CANDIDATE A: {source.candidate_a}"
        )
        return (
            payload_sha256(source.query, source.candidate_a, source.candidate_b),
            rendered,
        )

    v1_prompt_hashes: set[str] = set()
    for source in snapshot.source_packets:
        _v1_payload, v1_rendered = v1_variant(source)
        v1_prompt = hashlib.sha256(v1_rendered.encode("utf-8")).hexdigest()
        if v1_prompt not in snapshot.historical_prompt_hashes:
            v1_prompt_hashes.add(v1_prompt)

    v2_prompt_hashes: set[str] = set()
    prior_prompt_hashes = set(snapshot.historical_prompt_hashes) | v1_prompt_hashes
    for source in snapshot.source_packets:
        _v2_payload, v2_rendered = v2_variant(source)
        v2_prompt = hashlib.sha256(v2_rendered.encode("utf-8")).hexdigest()
        if v2_prompt not in prior_prompt_hashes:
            v2_prompt_hashes.add(v2_prompt)

    v3_prompt_hashes: set[str] = set()
    prior_prompt_hashes.update(v2_prompt_hashes)
    for source in snapshot.source_packets:
        if not source.query:
            continue
        _v3_payload, v3_rendered = v3_variant(source)
        v3_prompt = hashlib.sha256(v3_rendered.encode("utf-8")).hexdigest()
        if v3_prompt not in prior_prompt_hashes:
            v3_prompt_hashes.add(v3_prompt)

    v4_prompt_hashes: set[str] = set()
    prior_prompt_hashes.update(v3_prompt_hashes)
    for source in snapshot.source_packets:
        if not source.query:
            continue
        _v4_payload, v4_rendered = v4_variant(source)
        v4_prompt = hashlib.sha256(v4_rendered.encode("utf-8")).hexdigest()
        if v4_prompt not in prior_prompt_hashes:
            v4_prompt_hashes.add(v4_prompt)

    excluded_prompt_hashes = set(snapshot.historical_prompt_hashes)
    if derivation_tag in {
        DERIVATION_TAG_V2,
        DERIVATION_TAG_V3,
        DERIVATION_TAG_V4,
        DERIVATION_TAG_V5,
    }:
        excluded_prompt_hashes.update(v1_prompt_hashes)
    if derivation_tag in {
        DERIVATION_TAG_V3,
        DERIVATION_TAG_V4,
        DERIVATION_TAG_V5,
    }:
        excluded_prompt_hashes.update(v2_prompt_hashes)
    if derivation_tag in {DERIVATION_TAG_V4, DERIVATION_TAG_V5}:
        excluded_prompt_hashes.update(v3_prompt_hashes)
    if derivation_tag == DERIVATION_TAG_V5:
        excluded_prompt_hashes.update(v4_prompt_hashes)

    eligible: list[VariantPacket] = []
    collisions: list[str] = []
    variant_prompt_hashes: set[str] = set()
    variant_payload_hashes: set[str] = set()
    for source in snapshot.source_packets:
        if derivation_tag in {
            DERIVATION_TAG_V3,
            DERIVATION_TAG_V4,
            DERIVATION_TAG_V5,
        } and not source.query:
            continue
        if derivation_tag == DERIVATION_TAG_V1:
            variant_payload, rendered = v1_variant(source)
        elif derivation_tag == DERIVATION_TAG_V2:
            variant_payload, rendered = v2_variant(source)
        elif derivation_tag == DERIVATION_TAG_V3:
            variant_payload, rendered = v3_variant(source)
        elif derivation_tag == DERIVATION_TAG_V4:
            variant_payload, rendered = v4_variant(source)
        else:
            variant_payload, rendered = v5_variant(source)
        variant_prompt = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        if variant_prompt in excluded_prompt_hashes:
            collisions.append(source.source_payload_sha256)
            continue
        if variant_prompt in variant_prompt_hashes or variant_payload in variant_payload_hashes:
            raise CapacityPreflightError("capacity derivation produced duplicate variants")
        variant_prompt_hashes.add(variant_prompt)
        variant_payload_hashes.add(variant_payload)
        rank = hashlib.sha256(
            f"{derivation_tag}|{source.source_payload_sha256}".encode("utf-8")
        ).hexdigest()
        eligible.append(
            VariantPacket(
                rank_sha256=rank,
                source_payload_sha256=source.source_payload_sha256,
                source_prompt_sha256=source.source_prompt_sha256,
                variant_payload_sha256=variant_payload,
                variant_prompt_sha256=variant_prompt,
                prompt=rendered,
            )
        )
    eligible.sort(key=lambda item: (item.rank_sha256, item.source_payload_sha256))
    required_eligible = selection_count * cohort_count
    if len(eligible) < required_eligible:
        raise CapacityPreflightError(
            f"only {len(eligible)} byte-new variants exist; need {required_eligible}"
        )
    selected = tuple(eligible[:selection_count])
    retry_selected = tuple(eligible[selection_count:required_eligible])
    cohorts = [selected] + ([retry_selected] if cohort_count == 2 else [])

    def records(items: Sequence[VariantPacket]) -> list[dict[str, str]]:
        return [
            {
                "rank_sha256": item.rank_sha256,
                "source_payload_sha256": item.source_payload_sha256,
                "source_prompt_sha256": item.source_prompt_sha256,
                "variant_payload_sha256": item.variant_payload_sha256,
                "variant_prompt_sha256": item.variant_prompt_sha256,
            }
            for item in items
        ]

    transformations = {
        DERIVATION_TAG_V1: "swap_candidate_a_and_candidate_b_only",
        DERIVATION_TAG_V2: (
            "render_candidate_b_line_before_candidate_a_without_changing_labels_or_contents"
        ),
        DERIVATION_TAG_V3: (
            "render_candidate_a_line_then_candidate_b_line_then_query_line_without_"
            "changing_labels_or_contents"
        ),
        DERIVATION_TAG_V4: (
            "render_candidate_a_line_then_query_line_then_candidate_b_line_without_"
            "changing_labels_or_contents"
        ),
        DERIVATION_TAG_V5: (
            "render_candidate_b_line_then_query_line_then_candidate_a_line_without_"
            "changing_labels_or_contents"
        ),
    }
    historical_exclusions = {
        DERIVATION_TAG_V1: (
            "exclude_every_variant_prompt_sha256_seen_in_source_packets"
        ),
        DERIVATION_TAG_V2: (
            "exclude_every_source_prompt_and_v1_candidate_swap_prompt_sha256"
        ),
        DERIVATION_TAG_V3: (
            "exclude_every_source_v1_candidate_swap_and_v2_candidate_line_order_"
            "prompt_sha256"
        ),
        DERIVATION_TAG_V4: (
            "exclude_every_source_v1_candidate_swap_v2_and_v3_candidate_line_order_"
            "prompt_sha256"
        ),
        DERIVATION_TAG_V5: (
            "exclude_every_source_v1_candidate_swap_v2_v3_and_v4_candidate_line_order_"
            "prompt_sha256"
        ),
    }
    summary = {
        "derivation_tag": derivation_tag,
        "transformation": transformations[derivation_tag],
        "historical_exclusion": historical_exclusions[derivation_tag],
        "source_unique_packet_count": len(snapshot.source_packets),
        "historical_prompt_sha256_count": len(snapshot.historical_prompt_hashes),
        "byte_new_eligible_count": len(eligible),
        "historical_collision_count": len(collisions),
        "cohort_count": cohort_count,
        "selection_count_per_cohort": selection_count,
        "wave_size": wave_size,
        "wave_sizes_per_cohort": [wave_size] * (selection_count // wave_size),
        "unused_byte_new_reserve_count": len(eligible) - required_eligible,
        "cohort_records_canonical_sha256s": [
            canonical_sha256(records(cohort)) for cohort in cohorts
        ],
        "cohort_source_payloads_canonical_sha256s": [
            canonical_sha256([item.source_payload_sha256 for item in cohort])
            for cohort in cohorts
        ],
        "cohort_variant_payloads_canonical_sha256s": [
            canonical_sha256([item.variant_payload_sha256 for item in cohort])
            for cohort in cohorts
        ],
        "cohort_variant_prompts_canonical_sha256s": [
            canonical_sha256([item.variant_prompt_sha256 for item in cohort])
            for cohort in cohorts
        ],
        "historical_collision_sources_canonical_sha256": canonical_sha256(
            sorted(collisions)
        ),
    }
    if derivation_tag == DERIVATION_TAG_V2:
        summary.update(
            {
                "v1_candidate_swap_prompt_sha256_count": len(v1_prompt_hashes),
                "combined_excluded_prompt_sha256_count": len(excluded_prompt_hashes),
                "sealed_source_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(snapshot.historical_prompt_hashes)
                ),
                "v1_candidate_swap_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v1_prompt_hashes)
                ),
                "combined_excluded_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(excluded_prompt_hashes)
                ),
                "all_ranked_prompt_list_canonical_sha256": canonical_sha256(
                    [item.variant_prompt_sha256 for item in eligible]
                ),
                "all_ranked_source_payload_list_canonical_sha256": canonical_sha256(
                    [item.source_payload_sha256 for item in eligible]
                ),
            }
        )
    if derivation_tag == DERIVATION_TAG_V3:
        empty_query_sources = sorted(
            source.source_payload_sha256
            for source in snapshot.source_packets
            if not source.query
        )
        summary.update(
            {
                "empty_query_source_count": len(empty_query_sources),
                "empty_query_source_payloads_canonical_sha256": canonical_sha256(
                    empty_query_sources
                ),
                "v1_candidate_swap_prompt_sha256_count": len(v1_prompt_hashes),
                "v2_candidate_line_order_prompt_sha256_count": len(v2_prompt_hashes),
                "combined_excluded_prompt_sha256_count": len(excluded_prompt_hashes),
                "sealed_source_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(snapshot.historical_prompt_hashes)
                ),
                "v1_candidate_swap_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v1_prompt_hashes)
                ),
                "v2_candidate_line_order_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v2_prompt_hashes)
                ),
                "combined_excluded_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(excluded_prompt_hashes)
                ),
                "all_ranked_prompt_list_canonical_sha256": canonical_sha256(
                    [item.variant_prompt_sha256 for item in eligible]
                ),
                "all_ranked_source_payload_list_canonical_sha256": canonical_sha256(
                    [item.source_payload_sha256 for item in eligible]
                ),
            }
        )
    if derivation_tag == DERIVATION_TAG_V4:
        empty_query_sources = sorted(
            source.source_payload_sha256
            for source in snapshot.source_packets
            if not source.query
        )
        summary.update(
            {
                "empty_query_source_count": len(empty_query_sources),
                "empty_query_source_payloads_canonical_sha256": canonical_sha256(
                    empty_query_sources
                ),
                "v1_candidate_swap_prompt_sha256_count": len(v1_prompt_hashes),
                "v2_candidate_line_order_prompt_sha256_count": len(v2_prompt_hashes),
                "v3_candidate_line_order_prompt_sha256_count": len(v3_prompt_hashes),
                "combined_excluded_prompt_sha256_count": len(excluded_prompt_hashes),
                "sealed_source_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(snapshot.historical_prompt_hashes)
                ),
                "v1_candidate_swap_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v1_prompt_hashes)
                ),
                "v2_candidate_line_order_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v2_prompt_hashes)
                ),
                "v3_candidate_line_order_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v3_prompt_hashes)
                ),
                "combined_excluded_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(excluded_prompt_hashes)
                ),
                "all_ranked_prompt_list_canonical_sha256": canonical_sha256(
                    [item.variant_prompt_sha256 for item in eligible]
                ),
                "all_ranked_source_payload_list_canonical_sha256": canonical_sha256(
                    [item.source_payload_sha256 for item in eligible]
                ),
            }
        )
    if derivation_tag == DERIVATION_TAG_V5:
        empty_query_sources = sorted(
            source.source_payload_sha256
            for source in snapshot.source_packets
            if not source.query
        )
        summary.update(
            {
                "empty_query_source_count": len(empty_query_sources),
                "empty_query_source_payloads_canonical_sha256": canonical_sha256(
                    empty_query_sources
                ),
                "v1_candidate_swap_prompt_sha256_count": len(v1_prompt_hashes),
                "v2_candidate_line_order_prompt_sha256_count": len(v2_prompt_hashes),
                "v3_candidate_line_order_prompt_sha256_count": len(v3_prompt_hashes),
                "v4_candidate_line_order_prompt_sha256_count": len(v4_prompt_hashes),
                "combined_excluded_prompt_sha256_count": len(excluded_prompt_hashes),
                "sealed_source_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(snapshot.historical_prompt_hashes)
                ),
                "v1_candidate_swap_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v1_prompt_hashes)
                ),
                "v2_candidate_line_order_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v2_prompt_hashes)
                ),
                "v3_candidate_line_order_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v3_prompt_hashes)
                ),
                "v4_candidate_line_order_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(v4_prompt_hashes)
                ),
                "combined_excluded_prompt_set_canonical_sha256": canonical_sha256(
                    sorted(excluded_prompt_hashes)
                ),
                "all_ranked_prompt_list_canonical_sha256": canonical_sha256(
                    [item.variant_prompt_sha256 for item in eligible]
                ),
                "all_ranked_source_payload_list_canonical_sha256": canonical_sha256(
                    [item.source_payload_sha256 for item in eligible]
                ),
            }
        )
    return DerivedWorkload(
        selected=selected,
        eligible=tuple(eligible),
        collision_source_payloads=tuple(sorted(collisions)),
        summary=summary,
        retry_selected=retry_selected,
    )


def _expected_thresholds() -> dict[str, Any]:
    slots_per_judge_per_condition = 984
    judge_count = 2
    positive_budget_sum = 1 + 2 + 4 + 8
    attempts_per_budget_slot = 2
    max_unique = (
        slots_per_judge_per_condition
        * judge_count
        * positive_budget_sum
        * attempts_per_budget_slot
    )
    d_max = 45
    required_rate = max_unique / d_max
    certified_rate = 60 * 24
    projected_days = max_unique / certified_rate
    return {
        "total_judgment_slots": 9840,
        "slots_per_judge_per_condition": slots_per_judge_per_condition,
        "judge_count": judge_count,
        "positive_query_budget_sum": positive_budget_sum,
        "attempts_per_budget_slot_including_free_retry": attempts_per_budget_slot,
        "maximum_unique_review_payloads_zero_dedup": max_unique,
        "D_max_elapsed_days": d_max,
        "required_rulings_per_24h": required_rate,
        "certified_rulings_per_24h": certified_rate,
        "maximum_seconds_per_60_packet_wave": 3600,
        "maximum_seconds_for_three_waves": 10800,
        "worst_case_projected_elapsed_days": projected_days,
        "elapsed_day_margin": d_max - projected_days,
        "rate_reserve_fraction": certified_rate / required_rate - 1.0,
    }


def _validate_frozen_plan(plan: Mapping[str, Any]) -> None:
    schema_version = plan.get("schema_version")
    expected_hashes = {
        SCHEMA_VERSION_V1: EXPECTED_PLAN_CANONICAL_SHA256,
        SCHEMA_VERSION_V2: EXPECTED_PLAN_CANONICAL_SHA256_V2,
        SCHEMA_VERSION_V3: EXPECTED_PLAN_CANONICAL_SHA256_V3,
        SCHEMA_VERSION_V4: EXPECTED_PLAN_CANONICAL_SHA256_V4,
    }
    if schema_version not in expected_hashes:
        raise CapacityPreflightError("unexpected capacity preflight schema")
    if canonical_sha256(plan) != expected_hashes[schema_version]:
        raise CapacityPreflightError("capacity preflight plan canonical hash mismatch")
    if plan.get("execution_authorized") is not False:
        raise CapacityPreflightError("capacity preflight plan must not authorize execution")
    if plan.get("provider_calls_authorized") is not False:
        raise CapacityPreflightError("capacity preflight plan must not authorize provider calls")
    if plan.get("main_spend_authorized") is not False:
        raise CapacityPreflightError("capacity preflight plan must not authorize main spend")
    if schema_version in {SCHEMA_VERSION_V3, SCHEMA_VERSION_V4}:
        if plan.get("external_reviewer_dispatch_authorized") is not False:
            raise CapacityPreflightError(
                "successor plan must not authorize reviewer dispatch"
            )
        if plan.get("together_calls_authorized") is not False:
            raise CapacityPreflightError(
                "successor plan must not authorize Together calls"
            )


def dispatch_history_event_hash(event: Mapping[str, Any]) -> str:
    payload = dict(event)
    payload.pop("event_hash", None)
    return canonical_sha256(payload)


def _json_object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CapacityPreflightError(f"duplicate JSON key in dispatch history: {key}")
        value[key] = item
    return value


def parse_dispatch_history_bytes(
    raw: bytes, *, source: str = "dispatch history"
) -> DispatchHistorySnapshot:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CapacityPreflightError(f"{source} is not UTF-8") from exc
    if text and not text.endswith("\n"):
        raise CapacityPreflightError(f"{source} has a torn final row")
    events: list[Mapping[str, Any]] = []
    prefix_raw_sha256s: list[str] = []
    prefix_byte_counts: list[int] = []
    prefix = b""
    for line_number, raw_line in enumerate(raw.splitlines(keepends=True), 1):
        prefix += raw_line
        prefix_raw_sha256s.append(hashlib.sha256(prefix).hexdigest())
        prefix_byte_counts.append(len(prefix))
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            raise CapacityPreflightError(f"blank row at {source}:{line_number}")
        try:
            event = json.loads(
                line, object_pairs_hook=_json_object_without_duplicate_keys
            )
        except (json.JSONDecodeError, CapacityPreflightError) as exc:
            raise CapacityPreflightError(
                f"invalid JSON at {source}:{line_number}: {exc}"
            ) from exc
        if not isinstance(event, dict):
            raise CapacityPreflightError(f"non-object row at {source}:{line_number}")
        events.append(event)
    return DispatchHistorySnapshot(
        raw_sha256=hashlib.sha256(raw).hexdigest(),
        events=tuple(events),
        prefix_raw_sha256s=tuple(prefix_raw_sha256s),
        prefix_byte_counts=tuple(prefix_byte_counts),
    )


def load_dispatch_history(path: Path) -> DispatchHistorySnapshot:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CapacityPreflightError(
            f"required dispatch-history artifact is unavailable: {path}"
        ) from exc
    return parse_dispatch_history_bytes(raw, source=str(path))


def load_bound_dispatch_history(
    path: Path, *, plan: Mapping[str, Any]
) -> DispatchHistorySnapshot:
    contract = plan.get("dispatch_history_contract")
    if not isinstance(contract, Mapping):
        raise CapacityPreflightError("plan lacks dispatch_history_contract")
    required_path = Path(str(contract.get("required_path", "")))
    if path.resolve() != required_path.resolve():
        raise CapacityPreflightError("dispatch-history path differs from the frozen plan")
    return load_dispatch_history(path)


def _required_absolute_path(value: Any, *, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise CapacityPreflightError(f"{field} must be a nonempty absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise CapacityPreflightError(f"{field} must be an absolute path")
    return path.resolve()


def _validate_interruption_evidence(
    evidence: Mapping[str, Any], *, contract: Mapping[str, Any]
) -> None:
    if set(evidence) != {
        "classification",
        "summary",
        "verified_by",
        "evidence_path",
        "evidence_raw_sha256",
        "evidence_byte_count",
    }:
        raise CapacityPreflightError(
            "environmental interruption evidence has schema drift"
        )
    if evidence.get("classification") != "environmental_interruption":
        raise CapacityPreflightError(
            "interrupted attempt has a non-environmental classification"
        )
    for field in ("summary", "verified_by"):
        value = evidence.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CapacityPreflightError(
                f"environmental interruption evidence {field} is empty"
            )
    evidence_root = _required_absolute_path(
        contract.get("interruption_evidence_root"),
        field="dispatch-history interruption_evidence_root",
    )
    evidence_path = _required_absolute_path(
        evidence.get("evidence_path"), field="environmental interruption evidence_path"
    )
    _require_safe_path_component(
        evidence_path.name, field="environmental interruption evidence filename"
    )
    if evidence_path.parent != evidence_root:
        raise CapacityPreflightError(
            "environmental interruption evidence is outside the frozen evidence root"
        )
    try:
        evidence_bytes = evidence_path.read_bytes()
    except OSError as exc:
        raise CapacityPreflightError(
            "environmental interruption evidence bytes are unavailable"
        ) from exc
    if not evidence_bytes:
        raise CapacityPreflightError("environmental interruption evidence is empty")
    if evidence.get("evidence_byte_count") != len(evidence_bytes):
        raise CapacityPreflightError(
            "environmental interruption evidence byte count mismatch"
        )
    if evidence.get("evidence_raw_sha256") != hashlib.sha256(evidence_bytes).hexdigest():
        raise CapacityPreflightError("environmental interruption evidence hash mismatch")


def _dispatch_anchor_record(
    *,
    sequence: int,
    event_hash: str,
    history_prefix_raw_sha256: str,
    history_prefix_byte_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": DISPATCH_ANCHOR_SCHEMA_VERSION,
        "sequence": sequence,
        "event_hash": event_hash,
        "history_prefix_raw_sha256": history_prefix_raw_sha256,
        "history_prefix_byte_count": history_prefix_byte_count,
    }


def _dispatch_anchor_file_name(sequence: int, event_hash: str) -> str:
    return f"{sequence:04d}_{event_hash}.json"


def _initialization_paths(required_path: Path) -> tuple[Path, Path]:
    return (
        required_path.with_name(required_path.name + ".initialize.pending.json"),
        required_path.with_name(required_path.name + ".initialized.json"),
    )


def _append_intent_path(required_path: Path) -> Path:
    return required_path.with_name(required_path.name + ".append.pending.json")


def _initialization_record(
    *, plan: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": DISPATCH_INITIALIZATION_SCHEMA_VERSION,
        "plan_canonical_sha256": canonical_sha256(plan),
        "required_path": str(
            _required_absolute_path(
                contract.get("required_path"), field="dispatch-history required_path"
            )
        ),
        "anchor_directory": str(
            _required_absolute_path(
                contract.get("anchor_directory"),
                field="dispatch-history anchor_directory",
            )
        ),
        "interruption_evidence_root": str(
            _required_absolute_path(
                contract.get("interruption_evidence_root"),
                field="dispatch-history interruption_evidence_root",
            )
        ),
        "initial_history_raw_sha256": hashlib.sha256(b"").hexdigest(),
    }


def _validate_dispatch_anchors(
    history: DispatchHistorySnapshot,
    *,
    contract: Mapping[str, Any],
    plan: Mapping[str, Any],
    allow_append_pending: bool = False,
    allowed_extra_anchor: str | None = None,
) -> None:
    if contract.get("anchor_validation_required") is False:
        return
    if contract.get("anchor_validation_required") is not True:
        raise CapacityPreflightError("dispatch-history anchor requirement is invalid")
    anchor_dir = _required_absolute_path(
        contract.get("anchor_directory"), field="dispatch-history anchor_directory"
    )
    required_path = _required_absolute_path(
        contract.get("required_path"), field="dispatch-history required_path"
    )
    initialization_pending, initialization_receipt = _initialization_paths(required_path)
    append_pending = _append_intent_path(required_path)
    expected_initialization = (
        canonical_json(_initialization_record(plan=plan, contract=contract)) + "\n"
    ).encode("utf-8")
    try:
        actual_initialization = initialization_receipt.read_bytes()
    except OSError as exc:
        raise CapacityPreflightError(
            "dispatch-history initialization receipt is unavailable"
        ) from exc
    if actual_initialization != expected_initialization:
        raise CapacityPreflightError("dispatch-history initialization receipt mismatch")
    if initialization_pending.exists() or (
        append_pending.exists() and not allow_append_pending
    ):
        raise CapacityPreflightError("dispatch-history authority has pending recovery state")
    if not anchor_dir.is_dir():
        raise CapacityPreflightError("dispatch-history anchor directory is unavailable")
    if (
        len(history.prefix_raw_sha256s) != len(history.events)
        or len(history.prefix_byte_counts) != len(history.events)
    ):
        raise CapacityPreflightError("dispatch-history prefix bindings are incomplete")
    expected_names: set[str] = set()
    for sequence, event in enumerate(history.events):
        event_hash = _require_sha(
            event.get("event_hash"), field=f"dispatch-history event {sequence} hash"
        )
        file_name = _dispatch_anchor_file_name(sequence, event_hash)
        expected_names.add(file_name)
        anchor_path = anchor_dir / file_name
        record = _dispatch_anchor_record(
            sequence=sequence,
            event_hash=event_hash,
            history_prefix_raw_sha256=history.prefix_raw_sha256s[sequence],
            history_prefix_byte_count=history.prefix_byte_counts[sequence],
        )
        expected_bytes = (canonical_json(record) + "\n").encode("utf-8")
        try:
            actual_bytes = anchor_path.read_bytes()
        except OSError as exc:
            raise CapacityPreflightError(
                f"dispatch-history anchor is unavailable for event {sequence}"
            ) from exc
        if actual_bytes != expected_bytes:
            raise CapacityPreflightError(
                f"dispatch-history anchor mismatch for event {sequence}"
            )
    try:
        actual_names = {entry.name for entry in anchor_dir.iterdir() if entry.is_file()}
        has_non_file = any(not entry.is_file() for entry in anchor_dir.iterdir())
    except OSError as exc:
        raise CapacityPreflightError("cannot enumerate dispatch-history anchors") from exc
    allowed_names = expected_names | (
        {allowed_extra_anchor} if allowed_extra_anchor is not None else set()
    )
    if has_non_file or actual_names != allowed_names:
        raise CapacityPreflightError("dispatch-history anchor set has missing or extra entries")


def _validate_dispatch_history_chain(
    history: DispatchHistorySnapshot,
    *,
    plan: Mapping[str, Any],
    workload: DerivedWorkload,
    validate_anchors: bool = True,
    allow_append_pending: bool = False,
    allowed_extra_anchor: str | None = None,
) -> None:
    contract = plan.get("dispatch_history_contract")
    if not isinstance(contract, Mapping):
        raise CapacityPreflightError("plan lacks dispatch_history_contract")
    required_keys = {
        "schema_version",
        "sequence",
        "event",
        "attempt_number",
        "cohort_number",
        "attempt_id",
        "plan_canonical_sha256",
        "cohort_records_canonical_sha256",
        "cohort_variant_prompts_canonical_sha256",
        "environmental_interruption_evidence",
        "recorded_at_utc",
        "prev_event_hash",
        "event_hash",
    }
    previous_hash = "genesis"
    allowed_events = {
        "dispatch_started",
        "attempt_completed_pass",
        "attempt_completed_fail",
        "attempt_interrupted",
    }
    for sequence, event in enumerate(history.events):
        if set(event) != required_keys:
            raise CapacityPreflightError(f"dispatch-history event {sequence} has schema drift")
        if event.get("schema_version") != DISPATCH_HISTORY_SCHEMA_VERSION:
            raise CapacityPreflightError(f"dispatch-history event {sequence} has wrong schema")
        if event.get("sequence") != sequence:
            raise CapacityPreflightError("dispatch-history sequence is not contiguous")
        if event.get("prev_event_hash") != previous_hash:
            raise CapacityPreflightError("dispatch-history hash chain is broken")
        if event.get("event_hash") != dispatch_history_event_hash(event):
            raise CapacityPreflightError("dispatch-history event hash mismatch")
        if event.get("event") not in allowed_events:
            raise CapacityPreflightError("dispatch-history event type is invalid")
        interruption_evidence = event.get("environmental_interruption_evidence")
        if event.get("event") == "attempt_interrupted":
            if not isinstance(interruption_evidence, Mapping):
                raise CapacityPreflightError(
                    "interrupted attempt lacks environmental interruption evidence"
                )
            _validate_interruption_evidence(
                cast(Mapping[str, Any], interruption_evidence), contract=contract
            )
        elif interruption_evidence is not None:
            raise CapacityPreflightError(
                "non-interruption history event carries interruption evidence"
            )
        attempt_number = event.get("attempt_number")
        cohort_number = event.get("cohort_number")
        if attempt_number not in {1, 2} or cohort_number != attempt_number:
            raise CapacityPreflightError("dispatch history has invalid attempt/cohort numbering")
        attempt_id = event.get("attempt_id")
        if (
            not isinstance(attempt_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", attempt_id) is None
        ):
            raise CapacityPreflightError("dispatch-history attempt_id is invalid")
        if event.get("plan_canonical_sha256") != canonical_sha256(plan):
            raise CapacityPreflightError("dispatch history is bound to another plan")
        cohort_index = cast(int, cohort_number) - 1
        record_hashes = workload.summary.get("cohort_records_canonical_sha256s")
        prompt_hashes = workload.summary.get("cohort_variant_prompts_canonical_sha256s")
        if (
            not isinstance(record_hashes, list)
            or len(record_hashes) != 2
            or not isinstance(prompt_hashes, list)
            or len(prompt_hashes) != 2
        ):
            raise CapacityPreflightError("workload lacks two frozen cohort bindings")
        if event.get("cohort_records_canonical_sha256") != record_hashes[cohort_index]:
            raise CapacityPreflightError("dispatch history is bound to another workload")
        if event.get("cohort_variant_prompts_canonical_sha256") != prompt_hashes[
            cohort_index
        ]:
            raise CapacityPreflightError("dispatch history is bound to other prompt bytes")
        _parse_utc(event.get("recorded_at_utc"), field="dispatch history recorded_at_utc")
        previous_hash = str(event["event_hash"])
    if validate_anchors:
        _validate_dispatch_anchors(
            history,
            contract=contract,
            plan=plan,
            allow_append_pending=allow_append_pending,
            allowed_extra_anchor=allowed_extra_anchor,
        )


def _permitted_materialization_cohort(
    history: DispatchHistorySnapshot, *, plan: Mapping[str, Any], workload: DerivedWorkload
) -> int:
    _validate_dispatch_history_chain(history, plan=plan, workload=workload)
    contract = plan.get("dispatch_history_contract")
    if not isinstance(contract, Mapping):
        raise CapacityPreflightError("plan lacks dispatch_history_contract")
    if not history.events:
        if history.raw_sha256 != contract.get("required_initial_raw_sha256"):
            raise CapacityPreflightError("dispatch history is not the bound pristine artifact")
        return 1
    if len(history.events) == 2:
        started, terminal = history.events
        if (
            started.get("event") == "dispatch_started"
            and started.get("attempt_number") == 1
            and terminal.get("event") == "attempt_interrupted"
            and terminal.get("attempt_number") == 1
            and terminal.get("attempt_id") == started.get("attempt_id")
        ):
            return 2
    raise CapacityPreflightError(
        "dispatch history does not permit materializing another cohort"
    )


def initialize_dispatch_history(
    history_path: Path,
    *,
    plan: Mapping[str, Any],
    _fault_at: str | None = None,
) -> dict[str, Any]:
    """Retry-safely create the bound empty authority under a process-scoped lock."""
    contract = plan.get("dispatch_history_contract")
    if not isinstance(contract, Mapping):
        raise CapacityPreflightError("plan lacks dispatch_history_contract")
    required_path = _required_absolute_path(
        contract.get("required_path"), field="dispatch-history required_path"
    )
    if history_path.resolve() != required_path:
        raise CapacityPreflightError("dispatch-history path differs from the frozen plan")
    anchor_dir = _required_absolute_path(
        contract.get("anchor_directory"), field="dispatch-history anchor_directory"
    )
    evidence_root = _required_absolute_path(
        contract.get("interruption_evidence_root"),
        field="dispatch-history interruption_evidence_root",
    )
    required_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path, receipt_path = _initialization_paths(required_path)
    record_bytes = (
        canonical_json(_initialization_record(plan=plan, contract=contract)) + "\n"
    ).encode("utf-8")
    try:
        with output_lock(required_path):
            if receipt_path.exists():
                if receipt_path.read_bytes() != record_bytes:
                    raise CapacityPreflightError(
                        "dispatch-history initialization receipt mismatch"
                    )
                if pending_path.exists():
                    if pending_path.read_bytes() != record_bytes:
                        raise CapacityPreflightError(
                            "dispatch-history initialization intent mismatch"
                        )
                    pending_path.unlink()
                if (
                    not required_path.is_file()
                    or required_path.read_bytes() != b""
                    or not anchor_dir.is_dir()
                    or any(anchor_dir.iterdir())
                    or not evidence_root.is_dir()
                ):
                    raise CapacityPreflightError(
                        "initialized dispatch-history authority is not pristine"
                    )
                return {
                    "initialization": "already_initialized_pristine",
                    "dispatch_history_path": str(required_path),
                    "dispatch_history_raw_sha256": hashlib.sha256(b"").hexdigest(),
                    "anchor_directory": str(anchor_dir),
                    "interruption_evidence_root": str(evidence_root),
                }
            if pending_path.exists():
                if pending_path.read_bytes() != record_bytes:
                    raise CapacityPreflightError(
                        "dispatch-history initialization intent mismatch"
                    )
            else:
                if required_path.exists() or anchor_dir.exists() or evidence_root.exists():
                    raise CapacityPreflightError(
                        "partial dispatch-history authority exists without its intent"
                    )
                _durable_write(pending_path, record_bytes.decode("utf-8"))
                if _fault_at == "after_initialization_intent":
                    raise InjectedCapacityFault("after_initialization_intent")
            if evidence_root.exists():
                if not evidence_root.is_dir() or any(evidence_root.iterdir()):
                    raise CapacityPreflightError(
                        "partial interruption-evidence root is not pristine"
                    )
            else:
                evidence_root.mkdir(parents=True)
            if _fault_at == "after_evidence_root":
                raise InjectedCapacityFault("after_evidence_root")
            if anchor_dir.exists():
                if not anchor_dir.is_dir() or any(anchor_dir.iterdir()):
                    raise CapacityPreflightError(
                        "partial dispatch-history anchor directory is not pristine"
                    )
            else:
                anchor_dir.mkdir()
            if _fault_at == "after_anchor_directory":
                raise InjectedCapacityFault("after_anchor_directory")
            if required_path.exists():
                if not required_path.is_file() or required_path.read_bytes() != b"":
                    raise CapacityPreflightError(
                        "partial dispatch-history file is not pristine"
                    )
            else:
                _durable_write(required_path, "")
            if _fault_at == "after_initial_history":
                raise InjectedCapacityFault("after_initial_history")
            _durable_write(receipt_path, record_bytes.decode("utf-8"))
            if _fault_at == "after_initialization_receipt":
                raise InjectedCapacityFault("after_initialization_receipt")
            pending_path.unlink()
            return {
                "initialization": "pass",
                "dispatch_history_path": str(required_path),
                "dispatch_history_raw_sha256": hashlib.sha256(b"").hexdigest(),
                "anchor_directory": str(anchor_dir),
                "interruption_evidence_root": str(evidence_root),
            }
    except OutputLockedError as exc:
        raise CapacityPreflightError(
            "another process holds the dispatch-history authority lock"
        ) from exc


def _append_event_attempt_number(
    history: DispatchHistorySnapshot, *, event_type: str, attempt_id: str
) -> int:
    terminal_events = {
        "attempt_completed_pass",
        "attempt_completed_fail",
        "attempt_interrupted",
    }
    events = history.events
    if not events:
        if event_type != "dispatch_started":
            raise CapacityPreflightError("first dispatch-history event must start cohort 1")
        return 1
    if len(events) == 1:
        started = events[0]
        if (
            started.get("event") == "dispatch_started"
            and started.get("attempt_number") == 1
            and event_type in terminal_events
            and started.get("attempt_id") == attempt_id
        ):
            return 1
    if len(events) == 2:
        started, interrupted = events
        if (
            started.get("event") == "dispatch_started"
            and interrupted.get("event") == "attempt_interrupted"
            and interrupted.get("attempt_number") == 1
            and interrupted.get("attempt_id") == started.get("attempt_id")
            and event_type == "dispatch_started"
            and attempt_id != started.get("attempt_id")
        ):
            return 2
    if len(events) == 3:
        first_started, interrupted, retry_started = events
        if (
            first_started.get("event") == "dispatch_started"
            and interrupted.get("event") == "attempt_interrupted"
            and retry_started.get("event") == "dispatch_started"
            and retry_started.get("attempt_number") == 2
            and retry_started.get("attempt_id") == attempt_id
            and event_type in terminal_events
        ):
            return 2
    raise CapacityPreflightError(
        "dispatch-history state does not permit the requested event"
    )


def _append_and_fsync(path: Path, payload: bytes) -> None:
    flags = os.O_APPEND | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        written = os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if written != len(payload):
        raise CapacityPreflightError("dispatch-history append was partial")


def _load_canonical_intent(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CapacityPreflightError("dispatch append intent is unavailable") from exc
    intent = _load_json_object(path)
    if raw != (canonical_json(intent) + "\n").encode("utf-8"):
        raise CapacityPreflightError("dispatch append intent is not canonical")
    return intent


def _recover_pending_append(
    required_path: Path,
    *,
    plan: Mapping[str, Any],
    workload: DerivedWorkload,
) -> Mapping[str, Any] | None:
    pending_path = _append_intent_path(required_path)
    if not pending_path.exists():
        return None
    intent = _load_canonical_intent(pending_path)
    required_keys = {
        "schema_version",
        "plan_canonical_sha256",
        "prior_history_raw_sha256",
        "prior_history_byte_count",
        "target_history_raw_sha256",
        "target_history_byte_count",
        "event",
        "anchor_file",
        "anchor_record",
    }
    if set(intent) != required_keys:
        raise CapacityPreflightError("dispatch append intent has schema drift")
    if intent.get("schema_version") != DISPATCH_APPEND_INTENT_SCHEMA_VERSION:
        raise CapacityPreflightError("dispatch append intent schema changed")
    if intent.get("plan_canonical_sha256") != canonical_sha256(plan):
        raise CapacityPreflightError("dispatch append intent belongs to another plan")
    event = intent.get("event")
    anchor_record = intent.get("anchor_record")
    if not isinstance(event, Mapping) or not isinstance(anchor_record, Mapping):
        raise CapacityPreflightError("dispatch append intent lacks event or anchor record")
    event_mapping = cast(Mapping[str, Any], event)
    anchor_name = intent.get("anchor_file")
    if not isinstance(anchor_name, str):
        raise CapacityPreflightError("dispatch append intent anchor filename is invalid")
    _require_safe_path_component(anchor_name, field="dispatch append anchor filename")
    line = (canonical_json(dict(event_mapping)) + "\n").encode("utf-8")
    prior_count = intent.get("prior_history_byte_count")
    target_count = intent.get("target_history_byte_count")
    if (
        not isinstance(prior_count, int)
        or isinstance(prior_count, bool)
        or not isinstance(target_count, int)
        or isinstance(target_count, bool)
        or target_count != prior_count + len(line)
    ):
        raise CapacityPreflightError("dispatch append intent byte counts are invalid")
    prior_sha = _require_sha(
        intent.get("prior_history_raw_sha256"), field="append intent prior hash"
    )
    target_sha = _require_sha(
        intent.get("target_history_raw_sha256"), field="append intent target hash"
    )
    expected_anchor_name = _dispatch_anchor_file_name(
        int(event_mapping.get("sequence", -1)),
        str(event_mapping.get("event_hash", "")),
    )
    expected_anchor_record = _dispatch_anchor_record(
        sequence=int(event_mapping.get("sequence", -1)),
        event_hash=str(event_mapping.get("event_hash", "")),
        history_prefix_raw_sha256=target_sha,
        history_prefix_byte_count=target_count,
    )
    if anchor_name != expected_anchor_name or dict(anchor_record) != expected_anchor_record:
        raise CapacityPreflightError("dispatch append intent anchor binding mismatch")
    current = required_path.read_bytes()
    if len(current) < prior_count or hashlib.sha256(current[:prior_count]).hexdigest() != prior_sha:
        raise CapacityPreflightError("anchored dispatch-history prefix changed during recovery")
    contract = cast(Mapping[str, Any], plan["dispatch_history_contract"])
    anchor_dir = _required_absolute_path(
        contract.get("anchor_directory"), field="dispatch-history anchor_directory"
    )
    prior = parse_dispatch_history_bytes(current[:prior_count])
    _validate_dispatch_history_chain(
        prior,
        plan=plan,
        workload=workload,
        allow_append_pending=True,
        allowed_extra_anchor=(
            anchor_name if (anchor_dir / anchor_name).is_file() else None
        ),
    )
    tail = current[prior_count:]
    if len(tail) > len(line) or not line.startswith(tail):
        raise CapacityPreflightError(
            "dispatch-history recovery tail is not an exact intent prefix"
        )
    if len(tail) < len(line):
        _append_and_fsync(required_path, line[len(tail) :])
    completed = required_path.read_bytes()
    if len(completed) != target_count or hashlib.sha256(completed).hexdigest() != target_sha:
        raise CapacityPreflightError("dispatch-history recovery target mismatch")
    recovered_history = parse_dispatch_history_bytes(completed)
    _validate_dispatch_history_chain(
        recovered_history, plan=plan, workload=workload, validate_anchors=False
    )
    recovered_attempt = _append_event_attempt_number(
        prior,
        event_type=str(event_mapping.get("event")),
        attempt_id=str(event_mapping.get("attempt_id")),
    )
    if event_mapping.get("attempt_number") != recovered_attempt:
        raise CapacityPreflightError("dispatch append intent state transition is invalid")
    anchor_path = anchor_dir / anchor_name
    anchor_bytes = (canonical_json(dict(anchor_record)) + "\n").encode("utf-8")
    if anchor_path.exists():
        if anchor_path.read_bytes() != anchor_bytes:
            raise CapacityPreflightError("dispatch-history recovery anchor mismatch")
    else:
        _durable_write(anchor_path, anchor_bytes.decode("utf-8"))
    pending_path.unlink()
    _validate_dispatch_history_chain(
        recovered_history, plan=plan, workload=workload
    )
    return event_mapping


def append_dispatch_history_event(
    history_path: Path,
    *,
    event_type: str,
    attempt_id: str,
    plan: Mapping[str, Any],
    workload: DerivedWorkload,
    recorded_at_utc: str,
    interruption_evidence_path: Path | None = None,
    interruption_summary: str | None = None,
    verified_by: str | None = None,
    _fault_at: str | None = None,
) -> dict[str, Any]:
    """Append one event through a recoverable durable intent under an OS lock."""
    if event_type not in {
        "dispatch_started",
        "attempt_completed_pass",
        "attempt_completed_fail",
        "attempt_interrupted",
    }:
        raise CapacityPreflightError("unsupported dispatch-history event")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}", attempt_id) is None:
        raise CapacityPreflightError("dispatch-history attempt_id is invalid")
    contract = plan.get("dispatch_history_contract")
    if not isinstance(contract, Mapping):
        raise CapacityPreflightError("plan lacks dispatch_history_contract")
    required_path = _required_absolute_path(
        contract.get("required_path"), field="dispatch-history required_path"
    )
    if history_path.resolve() != required_path:
        raise CapacityPreflightError("dispatch-history path differs from the frozen plan")
    try:
        with output_lock(required_path):
            recovered = _recover_pending_append(
                required_path, plan=plan, workload=workload
            )
            if recovered is not None:
                suffix = (
                    "; record a verified interruption before any retry"
                    if recovered.get("event") == "dispatch_started"
                    else ""
                )
                raise CapacityPreflightError(
                    "recovered a committed pending dispatch event; do not replay it" + suffix
                )
            history = load_bound_dispatch_history(required_path, plan=plan)
            _validate_dispatch_history_chain(history, plan=plan, workload=workload)
            attempt_number = _append_event_attempt_number(
                history, event_type=event_type, attempt_id=attempt_id
            )
            parsed_recorded_at = _parse_utc(
                recorded_at_utc, field="dispatch-history recorded_at_utc"
            )
            evidence: Mapping[str, Any] | None = None
            if event_type == "attempt_interrupted":
                if (
                    interruption_evidence_path is None
                    or not isinstance(interruption_summary, str)
                    or not interruption_summary.strip()
                    or not isinstance(verified_by, str)
                    or not verified_by.strip()
                ):
                    raise CapacityPreflightError(
                        "interruption event requires evidence path, summary, and verifier"
                    )
                resolved_evidence = interruption_evidence_path.resolve()
                try:
                    evidence_bytes = resolved_evidence.read_bytes()
                except OSError as exc:
                    raise CapacityPreflightError(
                        "environmental interruption evidence bytes are unavailable"
                    ) from exc
                evidence = {
                    "classification": "environmental_interruption",
                    "summary": interruption_summary.strip(),
                    "verified_by": verified_by.strip(),
                    "evidence_path": str(resolved_evidence),
                    "evidence_raw_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                    "evidence_byte_count": len(evidence_bytes),
                }
                _validate_interruption_evidence(evidence, contract=contract)
            elif any(
                value is not None
                for value in (
                    interruption_evidence_path,
                    interruption_summary,
                    verified_by,
                )
            ):
                raise CapacityPreflightError(
                    "non-interruption event cannot carry interruption evidence"
                )
            sequence = len(history.events)
            previous_hash = (
                str(history.events[-1]["event_hash"]) if history.events else "genesis"
            )
            event: dict[str, Any] = {
                "schema_version": DISPATCH_HISTORY_SCHEMA_VERSION,
                "sequence": sequence,
                "event": event_type,
                "attempt_number": attempt_number,
                "cohort_number": attempt_number,
                "attempt_id": attempt_id,
                "plan_canonical_sha256": canonical_sha256(plan),
                "cohort_records_canonical_sha256": workload.summary[
                    "cohort_records_canonical_sha256s"
                ][attempt_number - 1],
                "cohort_variant_prompts_canonical_sha256": workload.summary[
                    "cohort_variant_prompts_canonical_sha256s"
                ][attempt_number - 1],
                "environmental_interruption_evidence": evidence,
                "recorded_at_utc": parsed_recorded_at.isoformat().replace("+00:00", "Z"),
                "prev_event_hash": previous_hash,
                "event_hash": "",
            }
            event["event_hash"] = dispatch_history_event_hash(event)
            line = (canonical_json(event) + "\n").encode("utf-8")
            current_bytes = required_path.read_bytes()
            if hashlib.sha256(current_bytes).hexdigest() != history.raw_sha256:
                raise CapacityPreflightError("dispatch history changed during append")
            target_bytes = current_bytes + line
            anchor_name = _dispatch_anchor_file_name(sequence, str(event["event_hash"]))
            anchor_record = _dispatch_anchor_record(
                sequence=sequence,
                event_hash=str(event["event_hash"]),
                history_prefix_raw_sha256=hashlib.sha256(target_bytes).hexdigest(),
                history_prefix_byte_count=len(target_bytes),
            )
            intent = {
                "schema_version": DISPATCH_APPEND_INTENT_SCHEMA_VERSION,
                "plan_canonical_sha256": canonical_sha256(plan),
                "prior_history_raw_sha256": history.raw_sha256,
                "prior_history_byte_count": len(current_bytes),
                "target_history_raw_sha256": hashlib.sha256(target_bytes).hexdigest(),
                "target_history_byte_count": len(target_bytes),
                "event": event,
                "anchor_file": anchor_name,
                "anchor_record": anchor_record,
            }
            pending_path = _append_intent_path(required_path)
            _durable_write(pending_path, canonical_json(intent) + "\n")
            if _fault_at == "after_append_intent":
                raise InjectedCapacityFault("after_append_intent")
            if _fault_at == "after_partial_history":
                _append_and_fsync(required_path, line[: max(1, len(line) // 2)])
                raise InjectedCapacityFault("after_partial_history")
            _append_and_fsync(required_path, line)
            if _fault_at == "after_history_fsync":
                raise InjectedCapacityFault("after_history_fsync")
            anchor_dir = _required_absolute_path(
                contract.get("anchor_directory"),
                field="dispatch-history anchor_directory",
            )
            _durable_write(
                anchor_dir / anchor_name, canonical_json(anchor_record) + "\n"
            )
            if _fault_at == "after_anchor_receipt":
                raise InjectedCapacityFault("after_anchor_receipt")
            pending_path.unlink()
            if _fault_at == "after_append_intent_clear":
                raise InjectedCapacityFault("after_append_intent_clear")
            updated = load_bound_dispatch_history(required_path, plan=plan)
            _validate_dispatch_history_chain(updated, plan=plan, workload=workload)
            return {
                "append": "pass",
                "event": event,
                "dispatch_history_raw_sha256": updated.raw_sha256,
                "anchor_file": anchor_name,
            }
    except OutputLockedError as exc:
        raise CapacityPreflightError(
            "another process holds the dispatch-history authority lock"
        ) from exc


def validate_plan(
    plan: Mapping[str, Any], *, snapshot: SourceSnapshot, workload: DerivedWorkload
) -> dict[str, Any]:
    _validate_frozen_plan(plan)
    schema_version = plan.get("schema_version")
    if plan.get("status") != "owner_confirmed_offline_plan_pending_separate_execution":
        raise CapacityPreflightError("capacity preflight plan has an unexpected status")

    source = plan.get("source_bindings")
    if not isinstance(source, Mapping):
        raise CapacityPreflightError("plan lacks source_bindings")
    observed_source = {
        "finalization_record_raw_sha256": snapshot.finalization_record_sha256,
        "reviewer_decision_store_raw_sha256": snapshot.decision_store_sha256,
        "reviewer_index_raw_sha256": snapshot.reviewer_index_sha256,
        "reviewer_prompt_prefix_sha256": snapshot.prompt_prefix_sha256,
        "packet_directories_canonical_sha256": canonical_sha256(
            list(snapshot.packet_directories)
        ),
    }
    if schema_version in {
        SCHEMA_VERSION_V2,
        SCHEMA_VERSION_V3,
        SCHEMA_VERSION_V4,
    }:
        locations = plan.get("source_locations")
        if not isinstance(locations, Mapping):
            raise CapacityPreflightError("successor plan lacks source_locations")
        proposal_value = locations.get("successor_proposal")
        ratification_value = locations.get("successor_ratification")
        if not isinstance(proposal_value, str) or not isinstance(
            ratification_value, str
        ):
            raise CapacityPreflightError("successor plan lacks proposal or ratification path")
        proposal_path = (REPO_ROOT / proposal_value).resolve()
        ratification_path = (REPO_ROOT / ratification_value).resolve()
        for label, path in (
            ("successor proposal", proposal_path),
            ("successor ratification", ratification_path),
        ):
            try:
                path.relative_to(REPO_ROOT)
            except ValueError as exc:
                raise CapacityPreflightError(f"{label} must remain within the repository") from exc
        proposal = _load_json_object(proposal_path)
        ratification = _load_json_object(ratification_path)
        observed_source.update(
            {
                "successor_proposal_raw_sha256": raw_sha256(proposal_path),
                "successor_proposal_canonical_sha256": canonical_sha256(proposal),
                "successor_ratification_raw_sha256": raw_sha256(ratification_path),
                "successor_ratification_canonical_sha256": canonical_sha256(
                    ratification
                ),
            }
        )
        expected_text = proposal.get("exact_non_execution_ratification_text")
        proposal_binding = ratification.get("proposal")
        authority = ratification.get("authority")
        expected_ratification_schema = {
            SCHEMA_VERSION_V2: "phase3_main_review_capacity_successor_ratification_v1",
            SCHEMA_VERSION_V3: "phase3_main_review_capacity_v3_successor_ratification_v1",
            SCHEMA_VERSION_V4: "phase3_main_review_capacity_v4_successor_ratification_v1",
        }[cast(str, schema_version)]
        expected_authority = {
            "offline_plan_materialization_authorized": True,
            "offline_workload_materialization_authorized": True,
            "external_reviewer_dispatch_authorized": False,
            "provider_calls_authorized": False,
            "main_run_authorized": False,
            "spend_authorized": False,
        }
        if schema_version in {SCHEMA_VERSION_V3, SCHEMA_VERSION_V4}:
            expected_authority["together_calls_authorized"] = False
        if (
            ratification.get("schema_version")
            != expected_ratification_schema
            or ratification.get("approved_by") != "Jack Maiorino"
            or ratification.get("ratification_text") != expected_text
            or not isinstance(proposal_binding, Mapping)
            or proposal_binding.get("path") != proposal_value
            or proposal_binding.get("raw_sha256") != raw_sha256(proposal_path)
            or proposal_binding.get("canonical_sha256") != canonical_sha256(proposal)
            or authority != expected_authority
        ):
            raise CapacityPreflightError(
                "successor ratification does not bind the exact non-execution proposal"
            )
    if dict(source) != observed_source:
        raise CapacityPreflightError("plan source bindings differ from the sealed archive")
    if plan.get("workload") != dict(workload.summary):
        raise CapacityPreflightError("plan workload differs from deterministic materialization")
    if (
        workload.summary.get("cohort_count") != 2
        or workload.summary.get("selection_count_per_cohort") != 180
        or workload.summary.get("wave_size") != 60
        or workload.summary.get("wave_sizes_per_cohort") != [60, 60, 60]
        or len(workload.retry_selected) != 180
    ):
        raise CapacityPreflightError("capacity workload must be two 180-packet cohorts")
    if plan.get("capacity_thresholds") != _expected_thresholds():
        raise CapacityPreflightError("plan capacity thresholds changed")

    reviewer = plan.get("reviewer_configuration")
    expected_reviewer = {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "reviewer_cli_binary": "codex.cmd",
        "reviewer_cli_resolved_path": "C:/Users/Jack/AppData/Roaming/npm/codex.cmd",
        "reviewer_cli_wrapper_raw_sha256": (
            "c54db6755e710c39703f7c37512f9e35ed41042d8080558d2b84b8d2694323c3"
        ),
        "reviewer_cli_wrapper_byte_count": 341,
        "concurrency": 12,
        "fresh_ephemeral_context_per_packet": True,
        "tool_use_permitted": False,
        "wave_pending_payload_limit": 64,
        "actual_capacity_wave_size": 60,
    }
    if schema_version == SCHEMA_VERSION_V4:
        expected_reviewer["openai_provider_supports_websockets"] = False
    if reviewer != expected_reviewer:
        raise CapacityPreflightError("reviewer configuration differs from the main configuration")
    configured_cli_path = _required_absolute_path(
        expected_reviewer["reviewer_cli_resolved_path"],
        field="reviewer_cli_resolved_path",
    )
    try:
        configured_cli_bytes = configured_cli_path.read_bytes()
    except OSError as exc:
        raise CapacityPreflightError("frozen reviewer CLI wrapper is unavailable") from exc
    if len(configured_cli_bytes) != expected_reviewer["reviewer_cli_wrapper_byte_count"]:
        raise CapacityPreflightError("frozen reviewer CLI wrapper byte count mismatch")
    if (
        hashlib.sha256(configured_cli_bytes).hexdigest()
        != expected_reviewer["reviewer_cli_wrapper_raw_sha256"]
    ):
        raise CapacityPreflightError("frozen reviewer CLI wrapper hash mismatch")
    history_contract = plan.get("dispatch_history_contract")
    if not isinstance(history_contract, Mapping):
        raise CapacityPreflightError("plan lacks dispatch_history_contract")
    if history_contract.get("schema_version") != DISPATCH_HISTORY_SCHEMA_VERSION:
        raise CapacityPreflightError("dispatch-history contract schema changed")
    required_history_path = history_contract.get("required_path")
    if (
        not isinstance(required_history_path, str)
        or not Path(required_history_path).is_absolute()
    ):
        raise CapacityPreflightError("dispatch-history contract requires an absolute path")
    if history_contract.get("required_initial_raw_sha256") != hashlib.sha256(b"").hexdigest():
        raise CapacityPreflightError("dispatch-history pristine binding changed")
    if history_contract.get("append_only") is not True:
        raise CapacityPreflightError("dispatch history must be append-only")
    if history_contract.get("anchor_validation_required") is not True:
        raise CapacityPreflightError("dispatch history must require retained anchors")
    if history_contract.get("initialization_receipt_required") is not True:
        raise CapacityPreflightError(
            "dispatch history must require its initialization receipt"
        )
    for field in (
        "required_path",
        "anchor_directory",
        "interruption_evidence_root",
    ):
        _required_absolute_path(
            history_contract.get(field), field=f"dispatch-history {field}"
        )
    if history_contract.get("maximum_attempts") != 2:
        raise CapacityPreflightError("dispatch history must permit exactly two attempts")
    validity = plan.get("validity")
    if not isinstance(validity, Mapping) or validity.get("valid_for_hours") != 24:
        raise CapacityPreflightError("capacity evidence must expire after 24 hours")
    return {
        "validation": "pass",
        "cohort_count": 2,
        "selected_packet_count_per_cohort": len(workload.selected),
        "pre_frozen_packet_count": len(workload.selected) + len(workload.retry_selected),
        "byte_new_eligible_count": len(workload.eligible),
        "unused_byte_new_reserve_count": (
            len(workload.eligible) - len(workload.selected) - len(workload.retry_selected)
        ),
        "plan_canonical_sha256": canonical_sha256(plan),
    }


def _parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CapacityPreflightError(f"{field} must be a UTC timestamp string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CapacityPreflightError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CapacityPreflightError(f"{field} must carry an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, *, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CapacityPreflightError(f"{field} must be numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise CapacityPreflightError(f"{field} must be finite")
    return converted


def _validate_result_dispatch_history(
    history: DispatchHistorySnapshot, *, result: Mapping[str, Any],
    plan: Mapping[str, Any], workload: DerivedWorkload,
    _validate_history_anchors: bool,
) -> int:
    _validate_dispatch_history_chain(
        history,
        plan=plan,
        workload=workload,
        validate_anchors=_validate_history_anchors,
    )
    valid_first = False
    valid_retry = False
    if len(history.events) == 2:
        started, completed = history.events
        valid_first = (
            started.get("event") == "dispatch_started"
            and completed.get("event") == "attempt_completed_pass"
            and started.get("attempt_number") == 1
            and completed.get("attempt_number") == 1
            and started.get("attempt_id") == completed.get("attempt_id")
        )
    elif len(history.events) == 4:
        first_start, first_end, retry_start, retry_end = history.events
        valid_retry = (
            first_start.get("event") == "dispatch_started"
            and first_end.get("event") == "attempt_interrupted"
            and retry_start.get("event") == "dispatch_started"
            and retry_end.get("event") == "attempt_completed_pass"
            and first_start.get("attempt_number") == 1
            and first_end.get("attempt_number") == 1
            and retry_start.get("attempt_number") == 2
            and retry_end.get("attempt_number") == 2
            and first_start.get("attempt_id") == first_end.get("attempt_id")
            and retry_start.get("attempt_id") == retry_end.get("attempt_id")
            and first_start.get("attempt_id") != retry_start.get("attempt_id")
        )
    if not valid_first and not valid_retry:
        raise CapacityPreflightError(
            "completed failure, replay, or unverified interruption blocks capacity evidence"
        )
    cohort_number = 2 if valid_retry else 1
    started = history.events[-2]
    completed = history.events[-1]
    attempt_id = result.get("attempt_id")
    if (
        result.get("attempt_number") != cohort_number
        or result.get("cohort_number") != cohort_number
        or started.get("attempt_id") != attempt_id
        or completed.get("attempt_id") != attempt_id
    ):
        raise CapacityPreflightError("capacity result is not the recorded permitted attempt")
    if result.get("dispatch_history_raw_sha256") != history.raw_sha256:
        raise CapacityPreflightError("capacity result dispatch-history hash mismatch")
    return cohort_number


def _validate_result(
    result: Mapping[str, Any], *, plan: Mapping[str, Any], workload: DerivedWorkload,
    dispatch_history: DispatchHistorySnapshot, as_of_utc: datetime,
    require_current_freshness: bool = True,
    _validate_history_anchors: bool,
) -> dict[str, Any]:
    """Validate capacity evidence with one private predicted-history anchor mode."""
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise CapacityPreflightError("unexpected capacity result schema")
    cohort_number = _validate_result_dispatch_history(
        dispatch_history,
        result=result,
        plan=plan,
        workload=workload,
        _validate_history_anchors=_validate_history_anchors,
    )
    if result.get("attempt_status") != "complete" or result.get("interrupted") is not False:
        raise CapacityPreflightError("partial or interrupted capacity attempts are not evidence")
    if result.get("plan_canonical_sha256") != canonical_sha256(plan):
        raise CapacityPreflightError("capacity result is bound to a different plan")
    cohort_items = workload.selected if cohort_number == 1 else workload.retry_selected
    cohort_records_hash = workload.summary["cohort_records_canonical_sha256s"][
        cohort_number - 1
    ]
    if result.get("cohort_records_canonical_sha256") != cohort_records_hash:
        raise CapacityPreflightError("capacity result is bound to a different workload")
    reviewer = result.get("reviewer_configuration")
    if reviewer != plan.get("reviewer_configuration"):
        raise CapacityPreflightError("capacity result reviewer configuration mismatch")
    if result.get("reviewer_configuration_canonical_sha256") != canonical_sha256(reviewer):
        raise CapacityPreflightError("capacity result reviewer-configuration hash mismatch")
    reviewer_mapping = cast(Mapping[str, Any], reviewer)
    environment = result.get("measurement_environment")
    if not isinstance(environment, Mapping):
        raise CapacityPreflightError("capacity result lacks measurement_environment")
    for field in (
        "reviewer_cli_resolved_path",
        "reviewer_cli_wrapper_raw_sha256",
        "reviewer_cli_version",
        "host_identity",
    ):
        if not isinstance(environment.get(field), str) or not environment[field].strip():
            raise CapacityPreflightError(f"measurement_environment.{field} must be nonempty")
    measured_cli_path = _required_absolute_path(
        environment.get("reviewer_cli_resolved_path"),
        field="measurement_environment.reviewer_cli_resolved_path",
    )
    planned_cli_path = _required_absolute_path(
        reviewer_mapping.get("reviewer_cli_resolved_path"),
        field="reviewer_configuration.reviewer_cli_resolved_path",
    )
    if measured_cli_path != planned_cli_path:
        raise CapacityPreflightError("measured reviewer CLI path differs from the plan")
    measured_cli_sha = _require_sha(
        environment.get("reviewer_cli_wrapper_raw_sha256"),
        field="measurement_environment.reviewer_cli_wrapper_raw_sha256",
    )
    if measured_cli_sha != reviewer_mapping.get("reviewer_cli_wrapper_raw_sha256"):
        raise CapacityPreflightError("measured reviewer CLI wrapper hash differs from the plan")
    measured_cli_size = environment.get("reviewer_cli_wrapper_byte_count")
    if (
        not isinstance(measured_cli_size, int)
        or isinstance(measured_cli_size, bool)
        or measured_cli_size != reviewer_mapping.get("reviewer_cli_wrapper_byte_count")
    ):
        raise CapacityPreflightError("measured reviewer CLI wrapper byte count differs")
    try:
        measured_cli_bytes = measured_cli_path.read_bytes()
    except OSError as exc:
        raise CapacityPreflightError("measured reviewer CLI wrapper is unavailable") from exc
    if len(measured_cli_bytes) != measured_cli_size:
        raise CapacityPreflightError("measured reviewer CLI wrapper byte count mismatch")
    if hashlib.sha256(measured_cli_bytes).hexdigest() != measured_cli_sha:
        raise CapacityPreflightError("measured reviewer CLI wrapper hash mismatch")
    if result.get("measurement_environment_canonical_sha256") != canonical_sha256(environment):
        raise CapacityPreflightError("measurement-environment hash mismatch")

    started_at = _parse_utc(result.get("started_at_utc"), field="started_at_utc")
    completed_at = _parse_utc(result.get("completed_at_utc"), field="completed_at_utc")
    if completed_at < started_at:
        raise CapacityPreflightError("capacity result completes before it starts")
    valid_hours = int((plan.get("validity") or {}).get("valid_for_hours", 0))
    if require_current_freshness:
        if as_of_utc.tzinfo is None or as_of_utc.utcoffset() != timedelta(0):
            raise CapacityPreflightError("as_of_utc must be timezone-aware UTC")
        as_of = as_of_utc.astimezone(timezone.utc)
        if as_of < completed_at:
            raise CapacityPreflightError("capacity evidence completion lies in the future")
        if as_of > completed_at + timedelta(hours=valid_hours):
            raise CapacityPreflightError("capacity evidence has expired")

    attempt_start = _finite_number(
        result.get("monotonic_started_seconds"), field="monotonic_started_seconds"
    )
    attempt_end = _finite_number(
        result.get("monotonic_completed_seconds"), field="monotonic_completed_seconds"
    )
    attempt_elapsed = _finite_number(
        result.get("elapsed_monotonic_seconds"), field="elapsed_monotonic_seconds"
    )
    if attempt_start < 0 or attempt_end < attempt_start or attempt_elapsed < 0:
        raise CapacityPreflightError("invalid attempt monotonic interval")
    if not math.isclose(attempt_end - attempt_start, attempt_elapsed, abs_tol=1e-6):
        raise CapacityPreflightError("attempt monotonic duration is inconsistent")
    maximum_total = float(
        plan["capacity_thresholds"]["maximum_seconds_for_three_waves"]
    )
    if attempt_elapsed > maximum_total:
        raise CapacityPreflightError("capacity attempt exceeds the total time limit")

    waves = result.get("waves")
    wave_size = int(workload.summary["wave_size"])
    expected_wave_count = len(cohort_items) // wave_size
    if not isinstance(waves, list) or len(waves) != expected_wave_count:
        raise CapacityPreflightError("capacity result does not contain exactly three waves")
    previous_end = attempt_start
    total_rows = 0
    maximum_wave = float(
        plan["capacity_thresholds"]["maximum_seconds_per_60_packet_wave"]
    )
    for wave_offset, wave in enumerate(waves):
        wave_number = wave_offset + 1
        if not isinstance(wave, Mapping):
            raise CapacityPreflightError(f"invalid wave identity at position {wave_number}")
        wave_mapping = cast(Mapping[str, Any], wave)
        if wave_mapping.get("wave") != wave_number:
            raise CapacityPreflightError(f"invalid wave identity at position {wave_number}")
        if (
            wave_mapping.get("attempt_status") != "complete"
            or wave_mapping.get("interrupted") is not False
        ):
            raise CapacityPreflightError(f"wave {wave_number} is partial or interrupted")
        if wave_mapping.get("process_exit_code") != 0:
            raise CapacityPreflightError(f"wave {wave_number} process failed")
        expected_failure_counts = {
            "timeouts": 0,
            "reviewer_errors": 0,
            "tool_uses": 0,
            "prompt_hash_mismatches": 0,
            "empty_outputs": 0,
            "unexpected_rows": 0,
            "duplicate_rows": 0,
        }
        if wave_mapping.get("failure_counts") != expected_failure_counts:
            raise CapacityPreflightError(f"wave {wave_number} records a failed dispatch")
        wave_start = _finite_number(
            wave_mapping.get("monotonic_started_seconds"),
            field=f"wave {wave_number} monotonic_started_seconds",
        )
        wave_end = _finite_number(
            wave_mapping.get("monotonic_completed_seconds"),
            field=f"wave {wave_number} monotonic_completed_seconds",
        )
        wave_elapsed = _finite_number(
            wave_mapping.get("elapsed_monotonic_seconds"),
            field=f"wave {wave_number} elapsed_monotonic_seconds",
        )
        if wave_start < previous_end or wave_end < wave_start or wave_elapsed < 0:
            raise CapacityPreflightError(f"wave {wave_number} monotonic interval is invalid")
        if not math.isclose(wave_end - wave_start, wave_elapsed, abs_tol=1e-6):
            raise CapacityPreflightError(f"wave {wave_number} duration is inconsistent")
        if wave_elapsed > maximum_wave:
            raise CapacityPreflightError(f"wave {wave_number} exceeds the time limit")
        if wave_end > attempt_end:
            raise CapacityPreflightError(f"wave {wave_number} ends outside the attempt")
        previous_end = wave_end

        expected_items = cohort_items[
            wave_offset * wave_size : (wave_offset + 1) * wave_size
        ]
        expected = {item.variant_payload_sha256: item for item in expected_items}
        declared_expected = wave_mapping.get("expected_payload_sha256s")
        if declared_expected != [item.variant_payload_sha256 for item in expected_items]:
            raise CapacityPreflightError(f"wave {wave_number} expected-payload binding mismatch")
        rows = wave_mapping.get("results")
        if not isinstance(rows, list) or len(rows) != wave_size:
            raise CapacityPreflightError(
                f"wave {wave_number} does not have {wave_size} result rows"
            )
        observed: set[str] = set()
        for row_number, row in enumerate(rows, 1):
            if not isinstance(row, Mapping):
                raise CapacityPreflightError(
                    f"wave {wave_number} result {row_number} is not an object"
                )
            row_mapping = cast(Mapping[str, Any], row)
            payload = row_mapping.get("payload_sha256")
            if payload not in expected:
                raise CapacityPreflightError(
                    f"wave {wave_number} has an unexpected payload result"
                )
            if payload in observed:
                raise CapacityPreflightError(f"wave {wave_number} has a duplicate result")
            observed.add(str(payload))
            expected_item = expected[str(payload)]
            if row_mapping.get("prompt_sha256") != expected_item.variant_prompt_sha256:
                raise CapacityPreflightError(
                    f"wave {wave_number} result {row_number} prompt hash mismatch"
                )
            if row_mapping.get("ok") is not True or row_mapping.get("error") is not None:
                raise CapacityPreflightError(
                    f"wave {wave_number} result {row_number} is a reviewer error"
                )
            if row_mapping.get("commands") != [] or row_mapping.get("tool_uses") != 0:
                raise CapacityPreflightError(
                    f"wave {wave_number} result {row_number} used a tool"
                )
            raw_output = row_mapping.get("raw_output")
            parsed = parse_reviewer_output(raw_output if isinstance(raw_output, str) else None)
            if (
                not isinstance(raw_output, str)
                or raw_output != raw_output.strip()
                or parsed == (None, None, None)
            ):
                raise CapacityPreflightError(
                    f"wave {wave_number} result {row_number} violates the frozen ruling parser"
                )
        if observed != set(expected):
            raise CapacityPreflightError(f"wave {wave_number} has missing results")
        total_rows += len(rows)
    if total_rows != len(cohort_items):
        raise CapacityPreflightError("capacity result row total is incomplete")
    return {
        "validation": "pass",
        "attempt_status": "complete",
        "cohort_number": cohort_number,
        "result_rows": total_rows,
        "elapsed_monotonic_seconds": attempt_elapsed,
        "evidence_expires_at_utc": (
            completed_at + timedelta(hours=valid_hours)
        ).isoformat().replace("+00:00", "Z"),
        "certified_rulings_per_24h": plan["capacity_thresholds"][
            "certified_rulings_per_24h"
        ],
    }


def validate_result(
    result: Mapping[str, Any], *, plan: Mapping[str, Any], workload: DerivedWorkload,
    dispatch_history: DispatchHistorySnapshot, as_of_utc: datetime,
    require_current_freshness: bool = True,
) -> dict[str, Any]:
    """Validate durable capacity evidence with dispatch anchors always enforced."""
    return _validate_result(
        result,
        plan=plan,
        workload=workload,
        dispatch_history=dispatch_history,
        as_of_utc=as_of_utc,
        require_current_freshness=require_current_freshness,
        _validate_history_anchors=True,
    )


def _durable_write(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def materialize_workload(
    output_dir: Path, *, plan: Mapping[str, Any], workload: DerivedWorkload,
    dispatch_history: DispatchHistorySnapshot,
) -> dict[str, Any]:
    source_locations = plan.get("source_locations")
    if not isinstance(source_locations, Mapping):
        raise CapacityPreflightError("plan lacks source_locations")
    sealed_archive = _required_absolute_path(
        source_locations.get("sealed_archive"), field="source_locations.sealed_archive"
    )
    resolved_output = output_dir.resolve()
    if resolved_output == sealed_archive or sealed_archive in resolved_output.parents:
        raise CapacityPreflightError(
            "materialization output cannot equal or nest under the sealed source archive"
        )
    cohort_number = _permitted_materialization_cohort(
        dispatch_history, plan=plan, workload=workload
    )
    cohort_items = workload.selected if cohort_number == 1 else workload.retry_selected
    if output_dir.exists():
        raise CapacityPreflightError(f"materialization target already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    wave_size = int(workload.summary["wave_size"])
    wave_manifests: list[dict[str, Any]] = []
    for offset in range(0, len(cohort_items), wave_size):
        wave_number = offset // wave_size + 1
        wave_dir = output_dir / f"wave_{wave_number:02d}"
        wave_dir.mkdir()
        index_items: list[dict[str, Any]] = []
        for position, item in enumerate(cohort_items[offset : offset + wave_size], 1):
            file_name = f"{position:03d}_{item.variant_payload_sha256[:12]}.txt"
            _durable_write(wave_dir / file_name, item.prompt)
            index_items.append(
                {
                    "n": position,
                    "file": file_name,
                    "payload_sha256": item.variant_payload_sha256,
                    "prompt_sha256": item.variant_prompt_sha256,
                    "source_payload_sha256": item.source_payload_sha256,
                }
            )
        index = {"count": len(index_items), "items": index_items}
        index_text = json.dumps(index, ensure_ascii=False, indent=1) + "\n"
        _durable_write(wave_dir / "INDEX.json", index_text)
        wave_manifests.append(
            {
                "wave": wave_number,
                "count": len(index_items),
                "index_raw_sha256": hashlib.sha256(index_text.encode("utf-8")).hexdigest(),
            }
        )
    manifest = {
        "schema_version": MATERIALIZATION_SCHEMA_VERSION,
        "execution_authorized": False,
        "provider_calls_authorized": False,
        "plan_canonical_sha256": canonical_sha256(plan),
        "cohort_number": cohort_number,
        "cohort_records_canonical_sha256": workload.summary[
            "cohort_records_canonical_sha256s"
        ][cohort_number - 1],
        "dispatch_history_raw_sha256_before_materialization": dispatch_history.raw_sha256,
        "waves": wave_manifests,
    }
    _durable_write(
        output_dir / "WORKLOAD_MANIFEST.json",
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase3_main_review_capacity_preflight")
    parser.add_argument("--plan", type=Path, default=PLAN_PATH_DEFAULT)
    parser.add_argument("--finalization-record", type=Path, default=FINALIZATION_PATH_DEFAULT)
    parser.add_argument("--archive", type=Path, default=ARCHIVE_DIR_DEFAULT)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--materialize-dir", type=Path)
    actions.add_argument("--result", type=Path)
    actions.add_argument("--initialize-dispatch-history", action="store_true")
    actions.add_argument(
        "--append-dispatch-event",
        choices=(
            "dispatch_started",
            "attempt_completed_pass",
            "attempt_completed_fail",
            "attempt_interrupted",
        ),
    )
    parser.add_argument("--dispatch-history", type=Path)
    parser.add_argument("--as-of-utc")
    parser.add_argument("--attempt-id")
    parser.add_argument("--recorded-at-utc")
    parser.add_argument("--interruption-evidence", type=Path)
    parser.add_argument("--interruption-summary")
    parser.add_argument("--verified-by")
    args = parser.parse_args(argv)
    append_only_options = {
        "--attempt-id": args.attempt_id,
        "--recorded-at-utc": args.recorded_at_utc,
        "--interruption-evidence": args.interruption_evidence,
        "--interruption-summary": args.interruption_summary,
        "--verified-by": args.verified_by,
    }
    if args.append_dispatch_event is None:
        supplied = [name for name, value in append_only_options.items() if value is not None]
        if supplied:
            parser.error(
                f"{', '.join(supplied)} require --append-dispatch-event"
            )
    elif args.attempt_id is None or args.recorded_at_utc is None:
        parser.error("history append requires --attempt-id and --recorded-at-utc")
    if args.result is None and args.as_of_utc is not None:
        parser.error("--as-of-utc requires --result")
    if args.check and args.dispatch_history is not None:
        parser.error("--dispatch-history is incompatible with --check")
    if not args.check and args.dispatch_history is None:
        parser.error("--dispatch-history is required for the selected action")
    plan = _load_json_object(args.plan)
    snapshot = collect_source_snapshot(
        archive_dir=args.archive, finalization_path=args.finalization_record
    )
    workload = derive_workload(
        snapshot,
        derivation_tag=derivation_tag_from_plan(plan),
    )
    result = validate_plan(plan, snapshot=snapshot, workload=workload)
    if args.initialize_dispatch_history:
        result["dispatch_history_initialization"] = initialize_dispatch_history(
            cast(Path, args.dispatch_history), plan=plan
        )
    if args.append_dispatch_event is not None:
        result["dispatch_history_append"] = append_dispatch_history_event(
            cast(Path, args.dispatch_history),
            event_type=args.append_dispatch_event,
            attempt_id=cast(str, args.attempt_id),
            plan=plan,
            workload=workload,
            recorded_at_utc=cast(str, args.recorded_at_utc),
            interruption_evidence_path=args.interruption_evidence,
            interruption_summary=args.interruption_summary,
            verified_by=args.verified_by,
        )
    dispatch_history: DispatchHistorySnapshot | None = None
    if args.result is not None or args.materialize_dir is not None:
        dispatch_history = load_bound_dispatch_history(
            cast(Path, args.dispatch_history), plan=plan
        )
    if args.result is not None:
        as_of = (
            _parse_utc(args.as_of_utc, field="--as-of-utc")
            if args.as_of_utc is not None
            else datetime.now(timezone.utc)
        )
        result["measurement_result"] = validate_result(
            _load_json_object(args.result),
            plan=plan,
            workload=workload,
            dispatch_history=cast(DispatchHistorySnapshot, dispatch_history),
            as_of_utc=as_of,
        )
    if args.materialize_dir is not None:
        result["materialization"] = materialize_workload(
            args.materialize_dir,
            plan=plan,
            workload=workload,
            dispatch_history=cast(DispatchHistorySnapshot, dispatch_history),
        )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
