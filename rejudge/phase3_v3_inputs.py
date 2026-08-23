"""Decision-grade exact-tokenizer and fresh-price gates for Phase 3 v3."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from rejudge import phase3_plan
from rejudge.phase2_execution import canonical_sha256


TOKENIZER_SCHEMA_VERSION = "phase3_v3_exact_tokenizer_manifest_v1"
PRICE_SCHEMA_VERSION = "phase3_v3_price_snapshot_v1"
REQUIRED_TRANSCRIPT_COUNT = 492
FORECAST_ROLES = frozenset({
    "judge_query", "judge_verdict", "query_checker", "oracle_verification",
})


class InputGateError(ValueError):
    """Raised when exact tokenization or fresh pricing is not decision-grade."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise InputGateError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise InputGateError(f"{label} must be a non-empty string")
    return value


def _sha256(value: Any, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise InputGateError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InputGateError(f"{label} must be a positive integer")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise InputGateError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise InputGateError(f"{label} must have a UTC offset")
    return parsed.astimezone(timezone.utc)


def _resolve_path(path_text: str, project_root: str | Path | None) -> Path:
    path = Path(path_text)
    if not path.is_absolute() and project_root is not None:
        path = Path(project_root) / path
    return path


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputGateError(f"could not read JSON {path}: {exc}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise InputGateError(f"{path}:{line_number} is not a JSON object")
                rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputGateError(f"could not read JSONL {path}: {exc}") from exc
    return rows


def expected_role_variants(protocol: Mapping[str, Any], model: str) -> dict[str, list[str]]:
    phase3_plan.validate_protocol(protocol)
    registry = _object(protocol.get("model_registry"), "protocol.model_registry")
    models = _object(registry.get("models"), "protocol.model_registry.models")
    entry = _object(models.get(model), f"protocol.model_registry.models[{model!r}]")
    billed_roles = entry.get("billed_roles")
    if (not isinstance(billed_roles, list)
            or not all(isinstance(role, str) and role for role in billed_roles)):
        raise InputGateError(f"protocol billed roles are invalid for {model}")
    conditions = protocol["debate_grid"]["conditions"]
    all_conditions = [str(condition["id"]) for condition in conditions]
    query_conditions = [str(condition["id"]) for condition in conditions
                        if int(condition["query_budget"]) > 0]
    variants_by_role = {
        "judge_query": query_conditions,
        "judge_verdict": all_conditions,
        "query_checker": ["query_checker"],
        "oracle_verification": ["oracle_verification"],
    }
    return {
        role: variants_by_role[role]
        for role in billed_roles
        if role in FORECAST_ROLES
    }


def _transcript_keys(bundle: Mapping[str, Any]) -> list[str]:
    transcripts = bundle.get("transcripts")
    if not isinstance(transcripts, list) or len(transcripts) != REQUIRED_TRANSCRIPT_COUNT:
        raise InputGateError("source transcript bundle must contain exactly 492 transcripts")
    keys: list[str] = []
    for index, raw_transcript in enumerate(transcripts):
        transcript = _object(raw_transcript, f"source transcript {index}")
        identity = {
            "debater_model": _text(
                transcript.get("debater_model"), f"source transcript {index}.debater_model"),
            "question_id": _text(
                transcript.get("question_id"), f"source transcript {index}.question_id"),
            "transcript_index": transcript.get("transcript_index"),
            "transcript_sha256": _sha256(
                transcript.get("transcript_sha256"),
                f"source transcript {index}.transcript_sha256"),
        }
        transcript_index = identity["transcript_index"]
        if (isinstance(transcript_index, bool) or not isinstance(transcript_index, int)
                or transcript_index < 0):
            raise InputGateError(f"source transcript {index}.transcript_index is invalid")
        keys.append(canonical_sha256(identity))
    if len(keys) != len(set(keys)):
        raise InputGateError("source transcript bundle has duplicate transcript identities")
    return sorted(keys)


def _validate_file_binding(
    binding: Mapping[str, Any], label: str, *, project_root: str | Path | None,
) -> Path:
    if set(binding) != {"path", "sha256"}:
        raise InputGateError(f"{label} fields must be exactly path and sha256")
    path_text = _text(binding.get("path"), f"{label}.path")
    expected_sha = _sha256(binding.get("sha256"), f"{label}.sha256")
    path = _resolve_path(path_text, project_root)
    if not path.is_file():
        raise InputGateError(f"bound file does not exist: {path}")
    observed_sha = sha256_file(path)
    if observed_sha != expected_sha:
        raise InputGateError(
            f"{label} hash mismatch: observed {observed_sha}, expected {expected_sha}")
    return path


def _validate_role_corpus_files(
    *,
    model: str,
    role: str,
    variants: Sequence[str],
    transcript_keys: Sequence[str],
    corpus_entry: Mapping[str, Any],
    project_root: str | Path | None,
) -> int:
    rendered_path = _validate_file_binding(
        _object(corpus_entry.get("rendered_corpus"), f"{model}.{role}.rendered_corpus"),
        f"{model}.{role}.rendered_corpus",
        project_root=project_root,
    )
    counts_path = _validate_file_binding(
        _object(corpus_entry.get("per_prompt_counts"), f"{model}.{role}.per_prompt_counts"),
        f"{model}.{role}.per_prompt_counts",
        project_root=project_root,
    )
    rendered_rows = _load_jsonl(rendered_path)
    count_rows = _load_jsonl(counts_path)
    expected_count = REQUIRED_TRANSCRIPT_COUNT * len(variants)
    if len(rendered_rows) != expected_count or len(count_rows) != expected_count:
        raise InputGateError(
            f"{model}.{role} corpus files do not contain {expected_count} rows each")

    expected_transcript_keys = set(transcript_keys)
    seen_prompt_keys: set[str] = set()
    seen_by_variant: dict[str, set[str]] = {variant: set() for variant in variants}
    for index, row in enumerate(rendered_rows):
        if set(row) != {
            "prompt_key", "transcript_key", "variant_id", "rendered_prompt",
            "rendered_prompt_sha256",
        }:
            raise InputGateError(f"{model}.{role} rendered row {index} fields drifted")
        transcript_key = _sha256(
            row.get("transcript_key"), f"{model}.{role}.rendered[{index}].transcript_key")
        variant = _text(
            row.get("variant_id"), f"{model}.{role}.rendered[{index}].variant_id")
        if variant not in seen_by_variant:
            raise InputGateError(f"{model}.{role} rendered row has unexpected variant {variant}")
        rendered_prompt = _text(
            row.get("rendered_prompt"), f"{model}.{role}.rendered[{index}].rendered_prompt")
        prompt_sha = _sha256(
            row.get("rendered_prompt_sha256"),
            f"{model}.{role}.rendered[{index}].rendered_prompt_sha256",
        )
        observed_prompt_sha = hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest()
        if observed_prompt_sha != prompt_sha:
            raise InputGateError(f"{model}.{role} rendered prompt hash mismatch")
        expected_prompt_key = canonical_sha256({
            "model": model,
            "role": role,
            "variant_id": variant,
            "transcript_key": transcript_key,
            "rendered_prompt_sha256": prompt_sha,
        })
        if row.get("prompt_key") != expected_prompt_key:
            raise InputGateError(f"{model}.{role} rendered prompt_key drifted")
        if expected_prompt_key in seen_prompt_keys:
            raise InputGateError(f"{model}.{role} has a duplicate rendered prompt_key")
        seen_prompt_keys.add(expected_prompt_key)
        seen_by_variant[variant].add(transcript_key)
    for variant, observed_keys in seen_by_variant.items():
        if observed_keys != expected_transcript_keys:
            raise InputGateError(
                f"{model}.{role}.{variant} does not cover all 492 transcripts exactly once")

    count_keys: set[str] = set()
    for index, row in enumerate(count_rows):
        if set(row) != {"prompt_key", "prompt_tokens"}:
            raise InputGateError(f"{model}.{role} count row {index} fields drifted")
        prompt_key = _sha256(
            row.get("prompt_key"), f"{model}.{role}.counts[{index}].prompt_key")
        _positive_int(
            row.get("prompt_tokens"), f"{model}.{role}.counts[{index}].prompt_tokens")
        if prompt_key in count_keys:
            raise InputGateError(f"{model}.{role} has duplicate per-prompt counts")
        count_keys.add(prompt_key)
    if count_keys != seen_prompt_keys:
        raise InputGateError(
            f"{model}.{role} per-prompt counts do not match the rendered corpus")
    return expected_count


def validate_exact_tokenizer_manifest(
    manifest: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any],
    project_root: str | Path | None = None,
    verify_files: bool = True,
) -> dict[str, Any]:
    """Require exact provider tokenizers plus complete role-by-transcript prompt counts."""
    phase3_plan.validate_protocol(protocol)
    if manifest.get("schema_version") != TOKENIZER_SCHEMA_VERSION:
        raise InputGateError("unexpected exact-tokenizer manifest schema")
    if manifest.get("execution_authorized") is not False:
        raise InputGateError("exact-tokenizer manifest cannot authorize execution")
    source = _object(manifest.get("source_transcript_bundle"), "source_transcript_bundle")
    expected_source_fields = {
        "path", "canonical_sha256", "transcript_count", "transcript_keys_sha256",
    }
    if set(source) != expected_source_fields:
        raise InputGateError(
            f"source_transcript_bundle fields must be exactly {sorted(expected_source_fields)!r}")
    if source.get("transcript_count") != REQUIRED_TRANSCRIPT_COUNT:
        raise InputGateError("exact-tokenizer manifest must bind all 492 main transcripts")
    source_sha = _sha256(
        source.get("canonical_sha256"), "source_transcript_bundle.canonical_sha256")
    declared_transcript_keys_sha = _sha256(
        source.get("transcript_keys_sha256"), "source_transcript_bundle.transcript_keys_sha256")
    transcript_keys: list[str] | None = None
    if verify_files:
        source_path = _resolve_path(
            _text(source.get("path"), "source_transcript_bundle.path"), project_root)
        bundle = _load_json(source_path)
        if not isinstance(bundle, dict):
            raise InputGateError("source transcript bundle must be a JSON object")
        if canonical_sha256(bundle) != source_sha:
            raise InputGateError("source transcript bundle canonical hash mismatch")
        transcript_keys = _transcript_keys(bundle)
        if canonical_sha256(transcript_keys) != declared_transcript_keys_sha:
            raise InputGateError("source transcript-key binding mismatch")

    models = _object(manifest.get("models"), "models")
    required_models = list(protocol["roster"]["judges_final"])
    if set(models) != set(required_models):
        raise InputGateError("exact-tokenizer models must equal the final billed-model roster")
    verified_files = 0
    verified_prompts = 0
    for model in required_models:
        entry = _object(models.get(model), f"models[{model!r}]")
        expected_entry_fields = {
            "classification", "repository", "revision", "provider_equivalence_evidence",
            "chat_template_sha256", "files", "role_corpora",
        }
        if set(entry) != expected_entry_fields:
            raise InputGateError(f"tokenizer entry fields drifted for {model}")
        if entry.get("classification") != "exact_provider_tokenizer":
            raise InputGateError(f"tokenizer for {model} is not exact and provider-matched")
        for field in ("repository", "revision", "provider_equivalence_evidence"):
            _text(entry.get(field), f"models[{model!r}].{field}")
        _sha256(entry.get("chat_template_sha256"), f"models[{model!r}].chat_template_sha256")
        files = _object(entry.get("files"), f"models[{model!r}].files")
        if not files:
            raise InputGateError(f"tokenizer entry for {model} has no tokenizer files")
        for name, raw_binding in files.items():
            _text(name, f"models[{model!r}].files name")
            binding = _object(raw_binding, f"models[{model!r}].files[{name!r}]")
            if verify_files:
                _validate_file_binding(
                    binding, f"models[{model!r}].files[{name!r}]", project_root=project_root)
            else:
                if set(binding) != {"path", "sha256"}:
                    raise InputGateError(f"tokenizer file binding fields drifted for {model}")
                _text(binding.get("path"), f"models[{model!r}].files[{name!r}].path")
                _sha256(binding.get("sha256"), f"models[{model!r}].files[{name!r}].sha256")
            verified_files += 1

        expected_roles = expected_role_variants(protocol, model)
        role_corpora = _object(entry.get("role_corpora"), f"models[{model!r}].role_corpora")
        if set(role_corpora) != set(expected_roles):
            raise InputGateError(f"role corpora for {model} do not match its billed roles")
        for role, variants in expected_roles.items():
            corpus = _object(role_corpora.get(role), f"models[{model!r}].role_corpora[{role!r}]")
            expected_corpus_fields = {
                "variant_ids", "transcript_count_per_variant",
                "expected_rendered_prompt_count", "actual_rendered_prompt_count",
                "rendered_corpus", "per_prompt_counts",
            }
            if set(corpus) != expected_corpus_fields:
                raise InputGateError(f"role corpus fields drifted for {model}.{role}")
            if corpus.get("variant_ids") != variants:
                raise InputGateError(f"role variants drifted for {model}.{role}")
            if corpus.get("transcript_count_per_variant") != REQUIRED_TRANSCRIPT_COUNT:
                raise InputGateError(f"{model}.{role} must cover 492 transcripts per variant")
            expected_prompt_count = REQUIRED_TRANSCRIPT_COUNT * len(variants)
            if corpus.get("expected_rendered_prompt_count") != expected_prompt_count:
                raise InputGateError(f"expected rendered-prompt count drifted for {model}.{role}")
            if corpus.get("actual_rendered_prompt_count") != expected_prompt_count:
                raise InputGateError(f"rendered prompts are incomplete for {model}.{role}")
            if verify_files:
                assert transcript_keys is not None
                verified_prompts += _validate_role_corpus_files(
                    model=model,
                    role=role,
                    variants=variants,
                    transcript_keys=transcript_keys,
                    corpus_entry=corpus,
                    project_root=project_root,
                )
            else:
                for file_field in ("rendered_corpus", "per_prompt_counts"):
                    binding = _object(
                        corpus.get(file_field), f"{model}.{role}.{file_field}")
                    if set(binding) != {"path", "sha256"}:
                        raise InputGateError(f"{model}.{role}.{file_field} fields drifted")
                    _text(binding.get("path"), f"{model}.{role}.{file_field}.path")
                    _sha256(binding.get("sha256"), f"{model}.{role}.{file_field}.sha256")
                verified_prompts += expected_prompt_count
    return {
        "validation": "pass",
        "canonical_sha256": canonical_sha256(manifest),
        "required_models": required_models,
        "verified_model_count": len(required_models),
        "verified_file_count": verified_files,
        "verified_rendered_prompt_count": verified_prompts,
        "local_files_checked": verify_files,
    }


def _catalog_entries(raw_catalog: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw_catalog, list):
        values = raw_catalog
    elif isinstance(raw_catalog, dict) and isinstance(raw_catalog.get("data"), list):
        values = raw_catalog["data"]
    else:
        raise InputGateError("raw provider catalog must be an array or an object with data array")
    entries: list[Mapping[str, Any]] = []
    for index, value in enumerate(values):
        entries.append(_object(value, f"raw catalog entry {index}"))
    return entries


def validate_price_snapshot(
    snapshot: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any],
    as_of: datetime,
    project_root: str | Path | None = None,
    verify_catalog: bool = True,
    max_age: timedelta = timedelta(hours=24),
) -> dict[str, Any]:
    """Require post-roster serverless availability and prices no more than 24 hours old."""
    phase3_plan.validate_protocol(protocol)
    if snapshot.get("schema_version") != PRICE_SCHEMA_VERSION:
        raise InputGateError("unexpected price snapshot schema")
    if snapshot.get("execution_authorized") is not False:
        raise InputGateError("price snapshot cannot authorize execution")
    if snapshot.get("provider") != "Together":
        raise InputGateError("v3 price snapshot provider must be Together")
    verified_at = _timestamp(snapshot.get("verified_at_utc"), "verified_at_utc")
    reference = as_of.astimezone(timezone.utc)
    age = reference - verified_at
    if age < timedelta(0) or age > max_age:
        raise InputGateError("price snapshot is not within the required 24-hour window")
    roster_resolved_at = _timestamp(
        protocol["roster_resolution"]["resolved_at_utc"],
        "protocol.roster_resolution.resolved_at_utc",
    )
    if verified_at < roster_resolved_at:
        raise InputGateError("price snapshot predates final roster resolution")

    raw_binding = _object(snapshot.get("raw_catalog"), "raw_catalog")
    expected_raw_fields = {"path", "canonical_sha256", "model_count"}
    if set(raw_binding) != expected_raw_fields:
        raise InputGateError(f"raw_catalog fields must be exactly {sorted(expected_raw_fields)!r}")
    raw_path_text = _text(raw_binding.get("path"), "raw_catalog.path")
    raw_sha = _sha256(raw_binding.get("canonical_sha256"), "raw_catalog.canonical_sha256")
    model_count = _positive_int(raw_binding.get("model_count"), "raw_catalog.model_count")
    catalog_by_id: dict[str, Mapping[str, Any]] | None = None
    if verify_catalog:
        raw_catalog = _load_json(_resolve_path(raw_path_text, project_root))
        if canonical_sha256(raw_catalog) != raw_sha:
            raise InputGateError("raw provider catalog canonical hash mismatch")
        entries = _catalog_entries(raw_catalog)
        if len(entries) != model_count:
            raise InputGateError("raw provider catalog model_count drifted")
        catalog_by_id = {}
        for entry in entries:
            model_id = _text(entry.get("id"), "raw catalog model id")
            if model_id in catalog_by_id:
                raise InputGateError(f"raw provider catalog has duplicate model id {model_id}")
            catalog_by_id[model_id] = entry

    models = _object(snapshot.get("models"), "models")
    required_models = list(protocol["roster"]["judges_final"])
    if set(models) != set(required_models):
        raise InputGateError("price snapshot models must equal the final billed-model roster")
    for model in required_models:
        entry = _object(models.get(model), f"models[{model!r}]")
        expected_fields = {
            "serverless_available", "input_usd_per_million",
            "output_usd_per_million", "catalog_entry_sha256",
        }
        if set(entry) != expected_fields:
            raise InputGateError(f"price entry fields drifted for {model}")
        if entry.get("serverless_available") is not True:
            raise InputGateError(f"required model is not verified serverless: {model}")
        prices: dict[str, float] = {}
        for field in ("input_usd_per_million", "output_usd_per_million"):
            value = entry.get(field)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value)) or value < 0):
                raise InputGateError(f"invalid {field} for {model}")
            prices[field] = float(value)
        catalog_entry_sha = _sha256(
            entry.get("catalog_entry_sha256"), f"models[{model!r}].catalog_entry_sha256")
        if verify_catalog:
            assert catalog_by_id is not None
            catalog_entry = catalog_by_id.get(model)
            if catalog_entry is None:
                raise InputGateError(f"required model is absent from raw catalog: {model}")
            if catalog_entry.get("type") != "chat":
                raise InputGateError(f"required model is not a chat model: {model}")
            if canonical_sha256(catalog_entry) != catalog_entry_sha:
                raise InputGateError(f"catalog entry hash mismatch for {model}")
            catalog_prices = _object(catalog_entry.get("pricing"), f"catalog pricing for {model}")
            expected_prices = {
                "input_usd_per_million": catalog_prices.get("input"),
                "output_usd_per_million": catalog_prices.get("output"),
            }
            for field, catalog_value in expected_prices.items():
                if (isinstance(catalog_value, bool)
                        or not isinstance(catalog_value, (int, float))
                        or not math.isclose(
                            prices[field], float(catalog_value), rel_tol=0.0, abs_tol=1e-12)):
                    raise InputGateError(f"snapshot price disagrees with raw catalog for {model}")
    return {
        "validation": "pass",
        "canonical_sha256": canonical_sha256(snapshot),
        "verified_at_utc": verified_at.isoformat().replace("+00:00", "Z"),
        "age_seconds": age.total_seconds(),
        "required_models": required_models,
        "raw_catalog_checked": verify_catalog,
    }
