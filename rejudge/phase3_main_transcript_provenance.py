"""Exact offline provenance for the Phase 3 main transcript partition.

The main run does not generate debate transcripts.  Its 492 transcript results are copied
from one frozen bundle and assigned the cell keys produced by the confirmed main inventory.
This module makes that relationship independently checkable at finalization time:

* both input files are read from one stable byte sample and checked against their manifest
  raw SHA-256 bindings;
* the verification report's canonical bundle digest is checked against the parsed bundle;
* every bundle payload hash and the exact one-to-one mapping to the 492 inventory transcript
  cells is re-derived; and
* every transcript result in a chain-validated result store must equal the derived payload
  exactly, including its complete field set.

Judgment rows are deliberately ignored by the partition comparison.  Their completeness and
provenance belong to the main finalization gate.  This module performs no provider or reviewer
calls and writes no artifacts.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from rejudge import phase3_main_runner, phase3_plan
from rejudge.phase2_execution import canonical_sha256


MAIN_TRANSCRIPT_BUNDLE_SCHEMA = "phase3_transcript_bundle_v1"
TRANSCRIPT_VERIFICATION_SCHEMA = "phase3_transcript_verification_v1"
TRANSCRIPT_KEY_FIELDS = ("debater_model", "question_id", "transcript_index")
_RESULT_ROW_FIELDS = frozenset({
    "cell_key", "result", "sequence", "prev_event_hash", "event_hash",
})


class MainTranscriptProvenanceError(ValueError):
    """The bound transcript inputs or result-store transcript partition drifted."""


@dataclass(frozen=True, slots=True)
class MainTranscriptProvenance:
    """Immutable exact results derived from the manifest-bound transcript inputs."""

    ordered_cell_keys: tuple[str, ...]
    _expected_result_json: Mapping[str, str]
    main_bundle_raw_sha256: str
    transcript_verification_raw_sha256: str
    main_bundle_canonical_sha256: str
    expected_results_canonical_sha256: str

    def expected_result(self, cell_key: str) -> dict[str, Any] | None:
        """Return a fresh copy of one expected result, or ``None`` for a non-transcript key."""
        encoded = self._expected_result_json.get(cell_key)
        if encoded is None:
            return None
        value = json.loads(encoded)
        if not isinstance(value, dict):  # construction guarantees this invariant
            raise AssertionError("encoded expected transcript result is not an object")
        return value


@dataclass(frozen=True, slots=True)
class MainTranscriptPartitionVerification:
    """Summary of one exact transcript-partition comparison."""

    status: str
    expected_transcript_count: int
    observed_transcript_count: int
    expected_results_canonical_sha256: str
    result_store_raw_sha256: str | None = None


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise MainTranscriptProvenanceError(
            "transcript provenance contains a non-canonical JSON value") from exc


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise MainTranscriptProvenanceError(
            f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise MainTranscriptProvenanceError(f"{label} must be a non-empty exact string")
    return value


def _require_non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MainTranscriptProvenanceError(f"{label} must be a non-negative integer")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MainTranscriptProvenanceError(f"JSON object repeats key {key!r}")
        value[key] = item
    return value


def _parse_object(raw: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise MainTranscriptProvenanceError(
            f"{label} contains non-finite JSON number {value!r}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MainTranscriptProvenanceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise MainTranscriptProvenanceError(f"{label} must be a JSON object")
    return value


def _read_bound_object(
    path: str | Path, *, expected_raw_sha256: str, label: str,
) -> tuple[dict[str, Any], bytes]:
    source = Path(path).resolve()
    expected = _require_sha256(expected_raw_sha256, f"{label} manifest raw SHA-256")
    try:
        raw = source.read_bytes()
        repeated = source.read_bytes()
    except OSError as exc:
        raise MainTranscriptProvenanceError(
            f"could not read manifest-bound {label}: {source}") from exc
    if raw != repeated:
        raise MainTranscriptProvenanceError(
            f"manifest-bound {label} changed while it was read")
    observed = _sha256_bytes(raw)
    if observed != expected:
        raise MainTranscriptProvenanceError(
            f"manifest-bound {label} raw SHA-256 drifted: {observed} != {expected}")
    return _parse_object(raw, label), raw


def _inventory_transcript_index(
    inventory: phase3_main_runner.MainInventory,
) -> tuple[dict[tuple[str, str, int], str], tuple[str, ...]]:
    if not isinstance(inventory, phase3_main_runner.MainInventory):
        raise MainTranscriptProvenanceError(
            "inventory must be a validated Phase 3 MainInventory")
    try:
        # The inventory object is cheap to instantiate directly, so type alone is not a
        # sufficient boundary.  Re-run the runner's exact confirmed digest and partition gate.
        phase3_main_runner._validate_inventory(inventory)
    except phase3_main_runner.MainInventoryError as exc:
        raise MainTranscriptProvenanceError(
            "main inventory is not the exact confirmed enumerator output") from exc

    index: dict[tuple[str, str, int], str] = {}
    ordered_cell_keys: list[str] = []
    for position, cell in enumerate(inventory.transcript_cells):
        cell_key = _require_text(
            cell.get("cell_key"), f"inventory transcript cell {position}.cell_key")
        debater = _require_text(
            cell.get("debater_model"),
            f"inventory transcript cell {position}.debater_model",
        )
        question_id = _require_text(
            cell.get("question_id"),
            f"inventory transcript cell {position}.question_id",
        )
        transcript_index = _require_non_negative_int(
            cell.get("transcript_index"),
            f"inventory transcript cell {position}.transcript_index",
        )
        key = (debater, question_id, transcript_index)
        if key in index:
            raise MainTranscriptProvenanceError(
                f"main inventory repeats transcript mapping {key!r}")
        index[key] = cell_key
        ordered_cell_keys.append(cell_key)
    if len(index) != phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT:
        raise MainTranscriptProvenanceError(
            "main inventory does not contain exactly 492 unique transcript cells")
    return index, tuple(ordered_cell_keys)


def _derive_expected_results(
    *,
    inventory: phase3_main_runner.MainInventory,
    bundle: Mapping[str, Any],
    verification: Mapping[str, Any],
    bundle_raw_sha256: str,
    verification_raw_sha256: str,
) -> MainTranscriptProvenance:
    if verification.get("schema_version") != TRANSCRIPT_VERIFICATION_SCHEMA:
        raise MainTranscriptProvenanceError(
            "unsupported transcript verification schema")
    hashes = verification.get("bundle_canonical_sha256")
    if not isinstance(hashes, Mapping):
        raise MainTranscriptProvenanceError(
            "transcript verification omits bundle_canonical_sha256")
    pinned_bundle_sha256 = _require_sha256(
        hashes.get("main_bundle"),
        "transcript verification main bundle canonical SHA-256",
    )
    observed_bundle_sha256 = canonical_sha256(bundle)
    if observed_bundle_sha256 != pinned_bundle_sha256:
        raise MainTranscriptProvenanceError(
            "main transcript bundle differs from the transcript verification binding: "
            f"{observed_bundle_sha256} != {pinned_bundle_sha256}")

    if bundle.get("schema_version") != MAIN_TRANSCRIPT_BUNDLE_SCHEMA:
        raise MainTranscriptProvenanceError("unsupported main transcript bundle schema")
    if bundle.get("bundle") != "main":
        raise MainTranscriptProvenanceError("transcript bundle is not the main bundle")
    expected_count = _require_non_negative_int(
        bundle.get("expected_transcript_count"),
        "main transcript bundle expected_transcript_count",
    )
    actual_count = _require_non_negative_int(
        bundle.get("actual_transcript_count"),
        "main transcript bundle actual_transcript_count",
    )
    if (
        expected_count != phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT
        or actual_count != phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT
    ):
        raise MainTranscriptProvenanceError(
            "main transcript bundle count fields must both equal 492")
    raw_entries = bundle.get("transcripts")
    if not isinstance(raw_entries, list):
        raise MainTranscriptProvenanceError(
            "main transcript bundle transcripts must be a list")
    entries = cast(list[Any], raw_entries)
    if len(entries) != phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT:
        raise MainTranscriptProvenanceError(
            "main transcript bundle must contain exactly 492 transcript entries")

    inventory_index, inventory_order = _inventory_transcript_index(inventory)
    expected_json: dict[str, str] = {}
    mapped: dict[tuple[str, str, int], int] = {}
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise MainTranscriptProvenanceError(
                f"main transcript bundle entry {entry_index} must be an object")
        debater = _require_text(
            entry.get("debater_model"),
            f"main transcript bundle entry {entry_index}.debater_model",
        )
        question_id = _require_text(
            entry.get("question_id"),
            f"main transcript bundle entry {entry_index}.question_id",
        )
        transcript_index = _require_non_negative_int(
            entry.get("transcript_index"),
            f"main transcript bundle entry {entry_index}.transcript_index",
        )
        mapping_key = (debater, question_id, transcript_index)
        if mapping_key in mapped:
            raise MainTranscriptProvenanceError(
                "main transcript bundle repeats enumerator mapping "
                f"{mapping_key!r} at entries {mapped[mapping_key]} and {entry_index}")
        mapped[mapping_key] = entry_index
        cell_key = inventory_index.get(mapping_key)
        if cell_key is None:
            raise MainTranscriptProvenanceError(
                "main transcript bundle entry has no enumerator-bound transcript cell: "
                f"{mapping_key!r}")

        payload = entry.get("transcript_payload")
        if not isinstance(payload, Mapping):
            raise MainTranscriptProvenanceError(
                f"main transcript bundle entry {entry_index}.transcript_payload "
                "must be an object")
        recorded_payload_sha256 = _require_sha256(
            entry.get("transcript_sha256"),
            f"main transcript bundle entry {entry_index}.transcript_sha256",
        )
        observed_payload_sha256 = canonical_sha256(payload)
        if observed_payload_sha256 != recorded_payload_sha256:
            raise MainTranscriptProvenanceError(
                "main transcript bundle payload drifted at "
                f"{mapping_key!r}: {observed_payload_sha256} != "
                f"{recorded_payload_sha256}")
        payload_question_id = _require_text(
            payload.get("question_id"),
            f"main transcript payload {entry_index}.question_id",
        )
        if payload_question_id != question_id:
            raise MainTranscriptProvenanceError(
                f"main transcript payload disagrees with entry {entry_index} on question_id")
        payload_transcript_index = _require_non_negative_int(
            payload.get("transcript_index"),
            f"main transcript payload {entry_index}.transcript_index",
        )
        if payload_transcript_index != transcript_index:
            raise MainTranscriptProvenanceError(
                "main transcript payload disagrees with entry "
                f"{entry_index} on transcript_index")
        if "debater_model" in payload:
            payload_debater = _require_text(
                payload.get("debater_model"),
                f"main transcript payload {entry_index}.debater_model",
            )
            if payload_debater != debater:
                raise MainTranscriptProvenanceError(
                    "main transcript payload disagrees with entry "
                    f"{entry_index} on debater_model")

        result = dict(payload)
        result["cell_key"] = cell_key
        expected_json[cell_key] = _canonical_json(result)

    missing_mappings = sorted(set(inventory_index) - set(mapped))
    if missing_mappings:
        raise MainTranscriptProvenanceError(
            "main transcript bundle does not cover the exact enumerator transcript "
            f"partition, missing {missing_mappings[:5]!r}")
    if len(expected_json) != phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT:
        raise MainTranscriptProvenanceError(
            "main transcript bundle does not map one-to-one onto 492 result payloads")

    ordered_expected_json = {
        cell_key: expected_json[cell_key] for cell_key in inventory_order
    }
    expected_results_digest = canonical_sha256([
        {"cell_key": cell_key, "result": json.loads(ordered_expected_json[cell_key])}
        for cell_key in inventory_order
    ])
    return MainTranscriptProvenance(
        ordered_cell_keys=inventory_order,
        _expected_result_json=MappingProxyType(ordered_expected_json),
        main_bundle_raw_sha256=_require_sha256(
            bundle_raw_sha256, "main transcript bundle raw SHA-256"),
        transcript_verification_raw_sha256=_require_sha256(
            verification_raw_sha256, "transcript verification raw SHA-256"),
        main_bundle_canonical_sha256=observed_bundle_sha256,
        expected_results_canonical_sha256=expected_results_digest,
    )


def load_manifest_bound_main_transcript_provenance(
    *,
    inventory: phase3_main_runner.MainInventory,
    main_bundle_path: str | Path,
    transcript_verification_path: str | Path,
    expected_main_bundle_raw_sha256: str,
    expected_transcript_verification_raw_sha256: str,
) -> MainTranscriptProvenance:
    """Load and derive all 492 expected transcript results from bound input bytes."""
    bundle, bundle_raw = _read_bound_object(
        main_bundle_path,
        expected_raw_sha256=expected_main_bundle_raw_sha256,
        label="main transcript bundle",
    )
    verification, verification_raw = _read_bound_object(
        transcript_verification_path,
        expected_raw_sha256=expected_transcript_verification_raw_sha256,
        label="transcript verification",
    )
    provenance = _derive_expected_results(
        inventory=inventory,
        bundle=bundle,
        verification=verification,
        bundle_raw_sha256=_sha256_bytes(bundle_raw),
        verification_raw_sha256=_sha256_bytes(verification_raw),
    )
    # Detect mutation after the parsed byte samples were validated and used.
    try:
        final_bundle_raw = Path(main_bundle_path).resolve().read_bytes()
        final_verification_raw = Path(transcript_verification_path).resolve().read_bytes()
    except OSError as exc:
        raise MainTranscriptProvenanceError(
            "manifest-bound transcript input disappeared during verification") from exc
    if final_bundle_raw != bundle_raw or final_verification_raw != verification_raw:
        raise MainTranscriptProvenanceError(
            "manifest-bound transcript input changed during verification")
    return provenance


def verify_main_transcript_partition(
    *,
    result_rows: Sequence[Mapping[str, Any]],
    provenance: MainTranscriptProvenance,
    result_store_raw_sha256: str | None = None,
) -> MainTranscriptPartitionVerification:
    """Compare the transcript projection of validated result rows with all 492 payloads."""
    if not isinstance(provenance, MainTranscriptProvenance):
        raise MainTranscriptProvenanceError(
            "provenance must be a MainTranscriptProvenance")
    expected_keys = frozenset(provenance.ordered_cell_keys)
    observed: set[str] = set()
    for row_index, row in enumerate(result_rows):
        if not isinstance(row, Mapping):
            raise MainTranscriptProvenanceError(
                f"result row {row_index} must be an object")
        cell_key = row.get("cell_key")
        if not isinstance(cell_key, str):
            continue
        if cell_key not in expected_keys:
            parts = cell_key.split(":", 2)
            if len(parts) == 3 and parts[1] == phase3_plan.MAIN_TRANSCRIPT_KIND:
                raise MainTranscriptProvenanceError(
                    "result store contains a main transcript cell outside the exact "
                    f"enumerator partition: {cell_key}")
            continue
        if cell_key in observed:
            raise MainTranscriptProvenanceError(
                f"result store repeats main transcript cell {cell_key}")
        observed.add(cell_key)
        result = row.get("result")
        if not isinstance(result, Mapping):
            raise MainTranscriptProvenanceError(
                f"main transcript result for {cell_key} must be an object")
        expected_result_json = provenance._expected_result_json[cell_key]
        observed_result_json = _canonical_json(dict(result))
        if observed_result_json != expected_result_json:
            raise MainTranscriptProvenanceError(
                "main transcript result differs from the exact frozen payload, including "
                f"its field set: {cell_key}")

    missing = [
        cell_key for cell_key in provenance.ordered_cell_keys
        if cell_key not in observed
    ]
    if missing:
        raise MainTranscriptProvenanceError(
            "result store does not contain the exact 492-row main transcript partition; "
            f"missing {missing[:5]!r}")
    raw_sha256 = None
    if result_store_raw_sha256 is not None:
        raw_sha256 = _require_sha256(
            result_store_raw_sha256, "result store raw SHA-256")
    return MainTranscriptPartitionVerification(
        status="exact_main_transcript_partition",
        expected_transcript_count=phase3_main_runner.EXPECTED_MAIN_TRANSCRIPT_COUNT,
        observed_transcript_count=len(observed),
        expected_results_canonical_sha256=(
            provenance.expected_results_canonical_sha256),
        result_store_raw_sha256=raw_sha256,
    )


def _load_result_store_rows(
    path: str | Path,
) -> tuple[tuple[dict[str, Any], ...], bytes]:
    source = Path(path).resolve()
    try:
        raw = source.read_bytes()
        repeated = source.read_bytes()
    except OSError as exc:
        raise MainTranscriptProvenanceError(
            f"could not read main result store: {source}") from exc
    if raw != repeated:
        raise MainTranscriptProvenanceError(
            "main result store changed while transcript provenance read it")

    rows: list[dict[str, Any]] = []
    previous = "genesis"
    seen: set[str] = set()
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            raise MainTranscriptProvenanceError(
                f"main result store has a blank row at line {line_number}")
        row = _parse_object(line, f"main result store line {line_number}")
        if set(row) != _RESULT_ROW_FIELDS:
            raise MainTranscriptProvenanceError(
                f"main result store line {line_number} fields drifted")
        sequence = row.get("sequence")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence != len(rows)
        ):
            raise MainTranscriptProvenanceError(
                "main result store sequence is not exact and contiguous")
        if row.get("prev_event_hash") != previous:
            raise MainTranscriptProvenanceError("main result store hash chain is broken")
        cell_key = _require_text(
            row.get("cell_key"), f"main result store line {line_number}.cell_key")
        if cell_key in seen:
            raise MainTranscriptProvenanceError(
                f"main result store repeats cell key {cell_key}")
        result = row.get("result")
        if not isinstance(result, Mapping) or result.get("cell_key") != cell_key:
            raise MainTranscriptProvenanceError(
                f"main result store result names another cell at line {line_number}")
        expected_event_hash = _sha256_bytes(_canonical_json({
            key: row[key]
            for key in ("cell_key", "result", "sequence", "prev_event_hash")
        }).encode("utf-8"))
        if row.get("event_hash") != expected_event_hash:
            raise MainTranscriptProvenanceError(
                f"main result store event hash is invalid at line {line_number}")
        previous = expected_event_hash
        seen.add(cell_key)
        rows.append(row)
    try:
        final_raw = source.read_bytes()
    except OSError as exc:
        raise MainTranscriptProvenanceError(
            "main result store disappeared during transcript verification") from exc
    if final_raw != raw:
        raise MainTranscriptProvenanceError(
            "main result store changed during transcript verification")
    return tuple(rows), raw


def verify_main_transcript_store(
    *, result_store_path: str | Path, provenance: MainTranscriptProvenance,
) -> MainTranscriptPartitionVerification:
    """Chain-validate a result store and verify its exact transcript partition."""
    rows, raw = _load_result_store_rows(result_store_path)
    return verify_main_transcript_partition(
        result_rows=rows,
        provenance=provenance,
        result_store_raw_sha256=_sha256_bytes(raw),
    )
