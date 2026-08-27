"""Decision-grade exact-tokenizer and fresh-price gates for Phase 3 v3."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from rejudge import phase3_plan, phase3_v3_static_prompts as static_prompts
from rejudge.phase2_execution import canonical_sha256


TOKENIZER_SCHEMA_VERSION = "phase3_v3_exact_tokenizer_manifest_v4"
TOKENIZER_SCHEMA_VERSION_V5 = "phase3_v3_exact_tokenizer_manifest_v5"
PRICE_SCHEMA_VERSION = "phase3_v3_price_snapshot_v1"
PRICE_SCHEMA_VERSION_V2 = "phase3_v3_price_snapshot_v2"
PROVIDER_TEMPLATE_SCHEMA_VERSION = "phase3_v3_provider_chat_template_v1"
TRANSCRIPT_BUNDLE_COUNTS = {"main": 492, "canary": 48}
REQUIRED_TRANSCRIPT_COUNT = TRANSCRIPT_BUNDLE_COUNTS["main"]


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
    try:
        return static_prompts.expected_role_variants(protocol, model)
    except static_prompts.StaticPromptError as exc:
        raise InputGateError(str(exc)) from exc


def billed_model_registry(protocol: Mapping[str, Any]) -> list[str]:
    """Every billed model in the protocol registry, in registry order.

    This is the coverage set for tokenizer, price-snapshot, and serverless-endpoint
    validation. It equals ``roster.judges_final`` for every protocol before r5, where
    gemma-4 first serves checker-only without holding a judge seat, so sealed history
    validates unchanged.
    """
    registry = _object(protocol.get("model_registry"), "protocol.model_registry")
    return list(_object(registry.get("models"), "protocol.model_registry.models"))


def transcript_entries(
    bundle: Mapping[str, Any], *, expected_count: int,
) -> dict[str, Mapping[str, Any]]:
    transcripts = bundle.get("transcripts")
    if not isinstance(transcripts, list) or len(transcripts) != expected_count:
        raise InputGateError(
            f"source transcript bundle must contain exactly {expected_count} transcripts")
    entries: dict[str, Mapping[str, Any]] = {}
    for index, raw_transcript in enumerate(transcripts):
        transcript = _object(raw_transcript, f"source transcript {index}")
        try:
            key = static_prompts.transcript_key(transcript)
        except static_prompts.StaticPromptError as exc:
            raise InputGateError(f"source transcript {index}: {exc}") from exc
        if key in entries:
            raise InputGateError("source transcript bundle has duplicate transcript identities")
        entries[key] = transcript
    return dict(sorted(entries.items()))


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
    transcript_entries: Mapping[str, Mapping[str, Any]],
    corpus_entry: Mapping[str, Any],
    project_root: str | Path | None,
    protocol: Mapping[str, Any],
    prompt_bundle: Mapping[str, Any],
    world_documents: Mapping[str, str],
    tokenizer: Any,
    dataset: str = "main",
) -> int:
    expected_transcript_count = TRANSCRIPT_BUNDLE_COUNTS[dataset]
    label = f"{model}.{dataset}.{role}"
    rendered_path = _validate_file_binding(
        _object(corpus_entry.get("rendered_corpus"), f"{label}.rendered_corpus"),
        f"{label}.rendered_corpus",
        project_root=project_root,
    )
    counts_path = _validate_file_binding(
        _object(corpus_entry.get("per_prompt_counts"), f"{label}.per_prompt_counts"),
        f"{label}.per_prompt_counts",
        project_root=project_root,
    )
    rendered_rows = _load_jsonl(rendered_path)
    count_rows = _load_jsonl(counts_path)
    expected_count = expected_transcript_count * len(variants)
    if len(rendered_rows) != expected_count or len(count_rows) != expected_count:
        raise InputGateError(
            f"{label} corpus files do not contain {expected_count} rows each")

    expected_transcript_keys = set(transcript_entries)
    counts_by_key: dict[str, int] = {}
    for index, row in enumerate(count_rows):
        if set(row) != {"prompt_key", "prompt_tokens"}:
            raise InputGateError(f"{label} count row {index} fields drifted")
        prompt_key = _sha256(
            row.get("prompt_key"), f"{label}.counts[{index}].prompt_key")
        prompt_tokens = _positive_int(
            row.get("prompt_tokens"), f"{label}.counts[{index}].prompt_tokens")
        if prompt_key in counts_by_key:
            raise InputGateError(f"{label} has duplicate per-prompt counts")
        counts_by_key[prompt_key] = prompt_tokens

    seen_prompt_keys: set[str] = set()
    seen_by_variant: dict[str, set[str]] = {variant: set() for variant in variants}
    for index, row in enumerate(rendered_rows):
        if set(row) != {
            "prompt_key", "transcript_key", "variant_id", "rendered_prompt_sha256",
        }:
            raise InputGateError(f"{label} rendered row {index} fields drifted")
        transcript_key = _sha256(
            row.get("transcript_key"), f"{label}.rendered[{index}].transcript_key")
        variant = _text(
            row.get("variant_id"), f"{label}.rendered[{index}].variant_id")
        if variant not in seen_by_variant:
            raise InputGateError(f"{label} rendered row has unexpected variant {variant}")
        prompt_sha = _sha256(
            row.get("rendered_prompt_sha256"),
            f"{label}.rendered[{index}].rendered_prompt_sha256",
        )
        transcript_entry = transcript_entries.get(transcript_key)
        if transcript_entry is None:
            raise InputGateError(f"{label} rendered row names an unknown transcript")
        try:
            messages = static_prompts.render_static_messages(
                protocol=protocol,
                prompt_bundle=prompt_bundle,
                world_documents=world_documents,
                transcript_entry=transcript_entry,
                billed_model=model,
                role=role,
                variant_id=variant,
            )
            rendered_prompt, observed_prompt_tokens = static_prompts.render_chat_prompt(
                tokenizer, messages)
        except static_prompts.StaticPromptError as exc:
            raise InputGateError(
                f"could not reproduce {label} rendered row {index}: {exc}") from exc
        observed_prompt_sha = hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest()
        if observed_prompt_sha != prompt_sha:
            raise InputGateError(f"{label} rendered prompt hash mismatch")
        expected_prompt_key = canonical_sha256({
            "model": model,
            "role": role,
            "variant_id": variant,
            "transcript_key": transcript_key,
            "rendered_prompt_sha256": prompt_sha,
        })
        if row.get("prompt_key") != expected_prompt_key:
            raise InputGateError(f"{label} rendered prompt_key drifted")
        if expected_prompt_key in seen_prompt_keys:
            raise InputGateError(f"{label} has a duplicate rendered prompt_key")
        if counts_by_key.get(expected_prompt_key) != observed_prompt_tokens:
            raise InputGateError(
                f"{label} exact tokenizer count mismatch for {expected_prompt_key}")
        seen_prompt_keys.add(expected_prompt_key)
        seen_by_variant[variant].add(transcript_key)
    for variant, observed_keys in seen_by_variant.items():
        if observed_keys != expected_transcript_keys:
            raise InputGateError(
                f"{label}.{variant} does not cover all {expected_transcript_count} "
                "transcripts exactly once")

    if set(counts_by_key) != seen_prompt_keys:
        raise InputGateError(
            f"{label} per-prompt counts do not match the rendered corpus")
    return expected_count


def _load_local_tokenizer(path: Path) -> Any:
    try:
        from transformers import AutoTokenizer
        return AutoTokenizer.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False)
    except Exception as exc:  # noqa: BLE001 - normalize optional library/model failures
        raise InputGateError(f"could not load local tokenizer {path}: {exc}") from exc


def validate_provider_chat_template_artifact(
    artifact: Mapping[str, Any], *, model: str,
    project_root: str | Path | None = None,
) -> dict[str, str]:
    """Bind a provider-exposed chat template to its raw catalog and public tokenizer."""
    expected_fields = {
        "schema_version", "artifact_id", "model_id", "provider", "observed_at_utc",
        "raw_catalog", "hugging_face", "provider_chat_template",
        "provider_chat_template_sha256", "match_status", "execution_authorized",
        "provider_inference_calls", "non_claims",
    }
    if set(artifact) != expected_fields:
        raise InputGateError("provider chat-template artifact fields drifted")
    if artifact.get("schema_version") != PROVIDER_TEMPLATE_SCHEMA_VERSION:
        raise InputGateError("unsupported provider chat-template artifact schema")
    if artifact.get("model_id") != model or artifact.get("provider") != "Together":
        raise InputGateError("provider chat-template artifact binds a different model")
    if (artifact.get("execution_authorized") is not False
            or artifact.get("provider_inference_calls") != 0):
        raise InputGateError("provider chat-template artifact cannot authorize or record inference")
    _text(artifact.get("artifact_id"), "provider template artifact_id")
    _timestamp(artifact.get("observed_at_utc"), "provider template observed_at_utc")
    if artifact.get("match_status") != "provider_template_explicit_override_required":
        raise InputGateError("provider chat-template match status drifted")
    non_claims = artifact.get("non_claims")
    if (not isinstance(non_claims, list) or not non_claims
            or not all(isinstance(item, str) and item for item in non_claims)):
        raise InputGateError("provider chat-template non-claims are invalid")

    template = _text(
        artifact.get("provider_chat_template"), "provider_chat_template")
    template_sha = _sha256(
        artifact.get("provider_chat_template_sha256"),
        "provider_chat_template_sha256",
    )
    if hashlib.sha256(template.encode("utf-8")).hexdigest() != template_sha:
        raise InputGateError("provider chat-template content hash drifted")
    if template_sha != static_prompts.QWEN38_PROVIDER_CHAT_TEMPLATE_SHA256:
        raise InputGateError("provider chat-template is not the frozen Qwen 3.8 template")

    hugging_face = _object(artifact.get("hugging_face"), "hugging_face")
    if set(hugging_face) != {
            "repository", "revision", "native_chat_template_sha256",
            "tokenizer_json_sha256"}:
        raise InputGateError("provider chat-template Hugging Face fields drifted")
    repository = _text(hugging_face.get("repository"), "hugging_face.repository")
    revision = _text(hugging_face.get("revision"), "hugging_face.revision")
    native_sha = _sha256(
        hugging_face.get("native_chat_template_sha256"),
        "hugging_face.native_chat_template_sha256",
    )
    tokenizer_json_sha = _sha256(
        hugging_face.get("tokenizer_json_sha256"),
        "hugging_face.tokenizer_json_sha256",
    )
    if repository != model:
        raise InputGateError("provider chat-template repository differs from its model")

    raw = _object(artifact.get("raw_catalog"), "raw_catalog")
    if set(raw) != {"path", "canonical_sha256", "catalog_entry_canonical_sha256"}:
        raise InputGateError("provider chat-template raw-catalog fields drifted")
    raw_path = _text(raw.get("path"), "raw_catalog.path")
    raw_sha = _sha256(raw.get("canonical_sha256"), "raw_catalog.canonical_sha256")
    entry_sha = _sha256(
        raw.get("catalog_entry_canonical_sha256"),
        "raw_catalog.catalog_entry_canonical_sha256",
    )
    if project_root is not None:
        catalog = _load_json(_resolve_path(raw_path, project_root))
        if canonical_sha256(catalog) != raw_sha:
            raise InputGateError("provider chat-template raw catalog hash drifted")
        entries = _catalog_entries(catalog)
        matches = [entry for entry in entries if entry.get("id") == model]
        if len(matches) != 1 or canonical_sha256(matches[0]) != entry_sha:
            raise InputGateError("provider chat-template catalog entry drifted")
        catalog_template = _object(
            matches[0].get("config"), "provider model config").get("chat_template")
        if catalog_template != template:
            raise InputGateError("provider catalog chat template differs from the bound template")
    return {
        "template": template,
        "provider_chat_template_sha256": template_sha,
        "native_chat_template_sha256": native_sha,
        "tokenizer_json_sha256": tokenizer_json_sha,
        "repository": repository,
        "revision": revision,
    }


def _validate_json_binding(
    binding: Mapping[str, Any], label: str, *, project_root: str | Path | None,
) -> tuple[Path, Mapping[str, Any]]:
    if set(binding) != {"path", "canonical_sha256"}:
        raise InputGateError(f"{label} fields must be exactly path and canonical_sha256")
    path = _resolve_path(_text(binding.get("path"), f"{label}.path"), project_root)
    value = _load_json(path)
    payload = _object(value, label)
    expected = _sha256(binding.get("canonical_sha256"), f"{label}.canonical_sha256")
    if canonical_sha256(payload) != expected:
        raise InputGateError(f"{label} canonical hash mismatch")
    return path, payload


def validate_exact_tokenizer_manifest(
    manifest: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any],
    project_root: str | Path | None = None,
    verify_files: bool = True,
    tokenizer_loader: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Re-render and re-tokenize every declared static prompt from bound local inputs."""
    phase3_plan.validate_protocol(protocol)
    schema_version = manifest.get("schema_version")
    expected_top_fields = {
        "schema_version", "execution_authorized", "protocol_canonical_sha256",
        "prompt_bundle", "query_checker_prompt", "world_documents",
        "transcript_bundles", "models",
    }
    if schema_version == TOKENIZER_SCHEMA_VERSION_V5:
        expected_top_fields.add("provider_chat_templates")
    if set(manifest) != expected_top_fields:
        raise InputGateError(
            f"exact-tokenizer manifest fields must be exactly {sorted(expected_top_fields)!r}")
    if schema_version not in {TOKENIZER_SCHEMA_VERSION, TOKENIZER_SCHEMA_VERSION_V5}:
        raise InputGateError("unexpected exact-tokenizer manifest schema")
    if manifest.get("execution_authorized") is not False:
        raise InputGateError("exact-tokenizer manifest cannot authorize execution")
    if manifest.get("protocol_canonical_sha256") != canonical_sha256(protocol):
        raise InputGateError("exact-tokenizer manifest binds a different v3 protocol")

    prompt_binding = _object(manifest.get("prompt_bundle"), "prompt_bundle")
    if set(prompt_binding) != {"path", "canonical_sha256"}:
        raise InputGateError("prompt_bundle fields must be exactly path and canonical_sha256")
    prompt_sha = _sha256(
        prompt_binding.get("canonical_sha256"), "prompt_bundle.canonical_sha256")
    expected_prompt_path = str(protocol["sources"]["prompt_bundle"]).replace("\\", "/")
    expected_prompt_sha = protocol["source_bindings"]["canonical_json_sha256"].get(
        expected_prompt_path)
    if prompt_sha != expected_prompt_sha:
        raise InputGateError("prompt bundle does not match the v3 protocol binding")

    checker_prompt = _object(manifest.get("query_checker_prompt"), "query_checker_prompt")
    expected_checker_fields = {
        "frozen_config", "validation_design", "system_prompt_sha256",
        "user_template_sha256",
    }
    if set(checker_prompt) != expected_checker_fields:
        raise InputGateError(
            f"query_checker_prompt fields must be exactly {sorted(expected_checker_fields)!r}")
    checker_source_fields = {
        "frozen_config": "query_checker_frozen_config",
        "validation_design": "query_checker_validation_design",
    }
    for field, protocol_source_name in checker_source_fields.items():
        binding = _object(checker_prompt.get(field), f"query_checker_prompt.{field}")
        if set(binding) != {"path", "canonical_sha256"}:
            raise InputGateError(f"query_checker_prompt.{field} fields drifted")
        declared_sha = _sha256(
            binding.get("canonical_sha256"),
            f"query_checker_prompt.{field}.canonical_sha256",
        )
        expected_path = str(protocol["sources"][protocol_source_name]).replace("\\", "/")
        expected_sha = protocol["source_bindings"]["canonical_json_sha256"].get(expected_path)
        if declared_sha != expected_sha:
            raise InputGateError(f"query-checker {field} does not match the protocol binding")
        if verify_files:
            _validate_json_binding(
                binding, f"query_checker_prompt.{field}", project_root=project_root)
        else:
            _text(binding.get("path"), f"query_checker_prompt.{field}.path")
    if checker_prompt.get("system_prompt_sha256") != (
            static_prompts.CHECKER_SYSTEM_PROMPT_SHA256):
        raise InputGateError("query-checker system prompt hash drifted")
    if checker_prompt.get("user_template_sha256") != (
            static_prompts.CHECKER_USER_TEMPLATE_SHA256):
        raise InputGateError("query-checker user template hash drifted")

    raw_transcript_bundles = _object(
        manifest.get("transcript_bundles"), "transcript_bundles")
    if set(raw_transcript_bundles) != set(TRANSCRIPT_BUNDLE_COUNTS):
        raise InputGateError("exact-tokenizer manifest must bind main and canary transcripts")
    expected_source_fields = {
        "path", "canonical_sha256", "transcript_count", "transcript_keys_sha256",
    }
    transcript_entries_by_dataset: dict[
        str, dict[str, Mapping[str, Any]]
    ] = {}
    for dataset, required_count in TRANSCRIPT_BUNDLE_COUNTS.items():
        source = _object(
            raw_transcript_bundles.get(dataset), f"transcript_bundles[{dataset!r}]")
        if set(source) != expected_source_fields:
            raise InputGateError(
                f"transcript_bundles[{dataset!r}] fields must be exactly "
                f"{sorted(expected_source_fields)!r}")
        if source.get("transcript_count") != required_count:
            raise InputGateError(
                f"exact-tokenizer manifest must bind all {required_count} {dataset} transcripts")
        source_sha = _sha256(
            source.get("canonical_sha256"),
            f"transcript_bundles[{dataset!r}].canonical_sha256",
        )
        declared_transcript_keys_sha = _sha256(
            source.get("transcript_keys_sha256"),
            f"transcript_bundles[{dataset!r}].transcript_keys_sha256",
        )
        if verify_files:
            source_path = _resolve_path(
                _text(source.get("path"), f"transcript_bundles[{dataset!r}].path"),
                project_root,
            )
            bundle = _load_json(source_path)
            if not isinstance(bundle, dict):
                raise InputGateError(f"{dataset} transcript bundle must be a JSON object")
            if canonical_sha256(bundle) != source_sha:
                raise InputGateError(f"{dataset} transcript bundle canonical hash mismatch")
            entries = transcript_entries(bundle, expected_count=required_count)
            if canonical_sha256(list(entries)) != declared_transcript_keys_sha:
                raise InputGateError(f"{dataset} transcript-key binding mismatch")
            transcript_entries_by_dataset[dataset] = entries
    prompt_bundle: Mapping[str, Any] | None = None
    world_documents: dict[str, str] = {}
    if verify_files:
        _prompt_path, prompt_bundle = _validate_json_binding(
            prompt_binding, "prompt_bundle", project_root=project_root)

    raw_worlds = _object(manifest.get("world_documents"), "world_documents")
    if verify_files:
        expected_worlds = {
            _text(
                _object(entry.get("transcript_payload"), "transcript_payload").get("world"),
                "transcript_payload.world",
            )
            for entries in transcript_entries_by_dataset.values()
            for entry in entries.values()
        }
        if set(raw_worlds) != expected_worlds:
            raise InputGateError("world-document bindings do not match transcript worlds")
    for world, raw_binding in raw_worlds.items():
        _text(world, "world_documents key")
        binding = _object(raw_binding, f"world_documents[{world!r}]")
        if verify_files:
            path = _validate_file_binding(
                binding, f"world_documents[{world!r}]", project_root=project_root)
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise InputGateError(f"could not read world document {path}: {exc}") from exc
            world_documents[world] = _text(content, f"world document {world!r}")
        else:
            if set(binding) != {"path", "sha256"}:
                raise InputGateError(f"world-document binding fields drifted for {world}")
            _text(binding.get("path"), f"world_documents[{world!r}].path")
            _sha256(binding.get("sha256"), f"world_documents[{world!r}].sha256")

    required_models = billed_model_registry(protocol)
    provider_templates: dict[str, Mapping[str, Any] | None] = {}
    if schema_version == TOKENIZER_SCHEMA_VERSION_V5:
        raw_provider_templates = _object(
            manifest.get("provider_chat_templates"), "provider_chat_templates")
        if not set(raw_provider_templates) <= set(required_models):
            raise InputGateError("provider chat-template models differ from the final roster")
        for model, raw_binding in raw_provider_templates.items():
            binding = _object(
                raw_binding, f"provider_chat_templates[{model!r}]")
            if set(binding) != {"path", "canonical_sha256"}:
                raise InputGateError(
                    f"provider chat-template binding fields drifted for {model}")
            path = _resolve_path(
                _text(binding.get("path"), f"provider_chat_templates[{model!r}].path"),
                project_root,
            )
            expected_sha = _sha256(
                binding.get("canonical_sha256"),
                f"provider_chat_templates[{model!r}].canonical_sha256",
            )
            if verify_files:
                artifact = _object(
                    _load_json(path), f"provider_chat_templates[{model!r}]")
                if canonical_sha256(artifact) != expected_sha:
                    raise InputGateError(
                        f"provider chat-template artifact hash drifted for {model}")
                validate_provider_chat_template_artifact(
                    artifact, model=model, project_root=project_root)
                provider_templates[model] = artifact
            else:
                provider_templates[model] = None

    models = _object(manifest.get("models"), "models")
    if set(models) != set(required_models):
        raise InputGateError("exact-tokenizer models must equal the final billed-model roster")
    verified_files = 0
    verified_prompts = 0
    for model in required_models:
        entry = _object(models.get(model), f"models[{model!r}]")
        expected_entry_fields = {
            "classification", "repository", "revision", "provider_equivalence_evidence",
            "tokenizer_directory", "tokenizer_class", "chat_template_sha256", "files",
            "chat_template_rendering", "corpora",
        }
        if set(entry) != expected_entry_fields:
            raise InputGateError(f"tokenizer entry fields drifted for {model}")
        if entry.get("classification") != "exact_provider_tokenizer":
            raise InputGateError(f"tokenizer for {model} is not exact and provider-matched")
        for field in ("repository", "revision", "provider_equivalence_evidence"):
            _text(entry.get(field), f"models[{model!r}].{field}")
        tokenizer_dir_text = _text(
            entry.get("tokenizer_directory"), f"models[{model!r}].tokenizer_directory")
        tokenizer_class = _text(
            entry.get("tokenizer_class"), f"models[{model!r}].tokenizer_class")
        declared_template_sha = _sha256(
            entry.get("chat_template_sha256"), f"models[{model!r}].chat_template_sha256")
        rendering = _object(
            entry.get("chat_template_rendering"),
            f"models[{model!r}].chat_template_rendering",
        )
        expected_rendering_fields = {
            "mode", "effective_chat_template_sha256", "evidence",
        }
        if set(rendering) != expected_rendering_fields:
            raise InputGateError(f"chat-template rendering fields drifted for {model}")
        rendering_mode = _text(
            rendering.get("mode"), f"models[{model!r}].chat_template_rendering.mode")
        if rendering_mode not in {
                static_prompts.NATIVE_RENDERING_MODE,
                static_prompts.GEMMA3N_PROVIDER_RENDERING_MODE,
                static_prompts.QWEN38_PROVIDER_RENDERING_MODE}:
            raise InputGateError(f"unsupported chat-template rendering mode for {model}")
        if ((rendering_mode == static_prompts.QWEN38_PROVIDER_RENDERING_MODE)
                != (model in provider_templates)):
            raise InputGateError(
                f"provider chat-template binding and rendering mode disagree for {model}")
        _sha256(
            rendering.get("effective_chat_template_sha256"),
            f"models[{model!r}].chat_template_rendering.effective_chat_template_sha256",
        )
        _text(
            rendering.get("evidence"),
            f"models[{model!r}].chat_template_rendering.evidence",
        )
        files = _object(entry.get("files"), f"models[{model!r}].files")
        if not files:
            raise InputGateError(f"tokenizer entry for {model} has no tokenizer files")
        tokenizer_dir = _resolve_path(tokenizer_dir_text, project_root)
        if verify_files and not tokenizer_dir.is_dir():
            raise InputGateError(f"local tokenizer directory does not exist: {tokenizer_dir}")
        declared_names = set(files)
        if verify_files:
            observed_names = {
                path.relative_to(tokenizer_dir).as_posix()
                for path in tokenizer_dir.rglob("*")
                if path.is_file() and ".cache" not in path.relative_to(tokenizer_dir).parts
            }
            if observed_names != declared_names:
                raise InputGateError(f"tokenizer file inventory drifted for {model}")
        for name, raw_binding in files.items():
            _text(name, f"models[{model!r}].files name")
            binding = _object(raw_binding, f"models[{model!r}].files[{name!r}]")
            if verify_files:
                bound_path = _validate_file_binding(
                    binding, f"models[{model!r}].files[{name!r}]", project_root=project_root)
                if bound_path.resolve() != (tokenizer_dir / name).resolve():
                    raise InputGateError(f"tokenizer file path escapes its inventory for {model}")
            else:
                if set(binding) != {"path", "sha256"}:
                    raise InputGateError(f"tokenizer file binding fields drifted for {model}")
                _text(binding.get("path"), f"models[{model!r}].files[{name!r}].path")
                _sha256(binding.get("sha256"), f"models[{model!r}].files[{name!r}].sha256")
            verified_files += 1

        tokenizer = None
        if verify_files:
            tokenizer = (tokenizer_loader or _load_local_tokenizer)(tokenizer_dir)
            if type(tokenizer).__name__ != tokenizer_class:
                raise InputGateError(f"loaded tokenizer class drifted for {model}")
            chat_template = getattr(tokenizer, "chat_template", None)
            if not isinstance(chat_template, str) or not chat_template:
                raise InputGateError(f"local tokenizer for {model} has no chat template")
            provider_artifact = provider_templates.get(model)
            if provider_artifact is not None:
                provider_template = validate_provider_chat_template_artifact(
                    provider_artifact, model=model, project_root=project_root)
                native_template_sha = hashlib.sha256(
                    chat_template.encode("utf-8")).hexdigest()
                if native_template_sha != provider_template[
                        "native_chat_template_sha256"]:
                    raise InputGateError(
                        f"native chat template hash drifted before provider override for {model}")
                tokenizer_json = _object(
                    files.get("tokenizer.json"),
                    f"models[{model!r}].files['tokenizer.json']",
                )
                if tokenizer_json.get("sha256") != provider_template[
                        "tokenizer_json_sha256"]:
                    raise InputGateError(
                        f"tokenizer.json differs from provider-template evidence for {model}")
                if entry.get("repository") != provider_template["repository"]:
                    raise InputGateError(
                        f"tokenizer repository differs from provider-template evidence for {model}")
                if entry.get("revision") != provider_template["revision"]:
                    raise InputGateError(
                        f"tokenizer revision differs from provider-template evidence for {model}")
                tokenizer.chat_template = provider_template["template"]
                chat_template = tokenizer.chat_template
            observed_template_sha = hashlib.sha256(
                chat_template.encode("utf-8")).hexdigest()
            if observed_template_sha != declared_template_sha:
                raise InputGateError(f"chat template hash drifted for {model}")
            _template_override, observed_rendering = (
                static_prompts.chat_template_rendering_policy(tokenizer))
            if dict(rendering) != observed_rendering:
                raise InputGateError(f"chat-template rendering policy drifted for {model}")

        expected_roles = expected_role_variants(protocol, model)
        corpora = _object(entry.get("corpora"), f"models[{model!r}].corpora")
        if set(corpora) != set(TRANSCRIPT_BUNDLE_COUNTS):
            raise InputGateError(f"corpora for {model} must cover main and canary")
        for dataset, required_count in TRANSCRIPT_BUNDLE_COUNTS.items():
            role_corpora = _object(
                corpora.get(dataset), f"models[{model!r}].corpora[{dataset!r}]")
            if set(role_corpora) != set(expected_roles):
                raise InputGateError(
                    f"{dataset} role corpora for {model} do not match its billed roles")
            for role, variants in expected_roles.items():
                label = f"{model}.{dataset}.{role}"
                corpus = _object(role_corpora.get(role), label)
                expected_corpus_fields = {
                    "variant_ids", "transcript_count_per_variant",
                    "expected_rendered_prompt_count", "actual_rendered_prompt_count",
                    "rendered_corpus", "per_prompt_counts",
                }
                if set(corpus) != expected_corpus_fields:
                    raise InputGateError(f"role corpus fields drifted for {label}")
                if corpus.get("variant_ids") != variants:
                    raise InputGateError(f"role variants drifted for {label}")
                if corpus.get("transcript_count_per_variant") != required_count:
                    raise InputGateError(
                        f"{label} must cover {required_count} transcripts per variant")
                expected_prompt_count = required_count * len(variants)
                if corpus.get("expected_rendered_prompt_count") != expected_prompt_count:
                    raise InputGateError(f"expected rendered-prompt count drifted for {label}")
                if corpus.get("actual_rendered_prompt_count") != expected_prompt_count:
                    raise InputGateError(f"rendered prompts are incomplete for {label}")
                if verify_files:
                    assert prompt_bundle is not None
                    assert tokenizer is not None
                    verified_prompts += _validate_role_corpus_files(
                        model=model,
                        role=role,
                        variants=variants,
                        transcript_entries=transcript_entries_by_dataset[dataset],
                        corpus_entry=corpus,
                        project_root=project_root,
                        protocol=protocol,
                        prompt_bundle=prompt_bundle,
                        world_documents=world_documents,
                        tokenizer=tokenizer,
                        dataset=dataset,
                    )
                else:
                    for file_field in ("rendered_corpus", "per_prompt_counts"):
                        binding = _object(corpus.get(file_field), f"{label}.{file_field}")
                        if set(binding) != {"path", "sha256"}:
                            raise InputGateError(f"{label}.{file_field} fields drifted")
                        _text(binding.get("path"), f"{label}.{file_field}.path")
                        _sha256(binding.get("sha256"), f"{label}.{file_field}.sha256")
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


def load_exact_context_index(
    manifest: Mapping[str, Any],
    *,
    protocol: Mapping[str, Any],
    project_root: str | Path,
    tokenizer_loader: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Validate the manifest, then load exact counts and transcript dimension lookups."""
    validation = validate_exact_tokenizer_manifest(
        manifest,
        protocol=protocol,
        project_root=project_root,
        tokenizer_loader=tokenizer_loader,
    )
    root = Path(project_root)
    transcript_keys: dict[tuple[str, str, str, int], str] = {}
    for dataset, required_count in TRANSCRIPT_BUNDLE_COUNTS.items():
        binding = _object(
            _object(manifest["transcript_bundles"], "transcript_bundles").get(dataset),
            f"transcript_bundles[{dataset!r}]",
        )
        bundle = _load_json(_resolve_path(str(binding["path"]), root))
        entries = transcript_entries(
            _object(bundle, f"{dataset} transcript bundle"), expected_count=required_count)
        for transcript_key, entry in entries.items():
            dimension_key = (
                dataset,
                str(entry["debater_model"]),
                str(entry["question_id"]),
                int(entry["transcript_index"]),
            )
            if dimension_key in transcript_keys:
                raise InputGateError(
                    f"{dataset} transcript dimensions are not unique: {dimension_key[1:]!r}")
            transcript_keys[dimension_key] = transcript_key

    prompt_tokens: dict[tuple[str, str, str, str, str], int] = {}
    models = _object(manifest.get("models"), "models")
    for model in billed_model_registry(protocol):
        model_entry = _object(models.get(model), f"models[{model!r}]")
        corpora = _object(model_entry.get("corpora"), f"models[{model!r}].corpora")
        for dataset in TRANSCRIPT_BUNDLE_COUNTS:
            role_corpora = _object(corpora.get(dataset), f"{model}.{dataset}")
            for role, raw_corpus in role_corpora.items():
                corpus = _object(raw_corpus, f"{model}.{dataset}.{role}")
                rendered_binding = _object(
                    corpus.get("rendered_corpus"), f"{model}.{dataset}.{role}.rendered")
                counts_binding = _object(
                    corpus.get("per_prompt_counts"), f"{model}.{dataset}.{role}.counts")
                rendered_rows = _load_jsonl(
                    _resolve_path(str(rendered_binding["path"]), root))
                counts_by_prompt = {
                    str(row["prompt_key"]): int(row["prompt_tokens"])
                    for row in _load_jsonl(_resolve_path(str(counts_binding["path"]), root))
                }
                for row in rendered_rows:
                    prompt_key = str(row["prompt_key"])
                    index_key = (
                        dataset,
                        str(model),
                        str(role),
                        str(row["variant_id"]),
                        str(row["transcript_key"]),
                    )
                    if index_key in prompt_tokens:
                        raise InputGateError(f"duplicate exact-context index key: {index_key!r}")
                    prompt_tokens[index_key] = counts_by_prompt[prompt_key]
    return {
        "tokenizer_manifest_canonical_sha256": canonical_sha256(manifest),
        "validation": validation,
        "transcript_keys": transcript_keys,
        "prompt_tokens": prompt_tokens,
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
    schema_version = snapshot.get("schema_version")
    expected_top_fields = {
        "schema_version", "provider", "verified_at_utc", "execution_authorized",
        "raw_catalog", "models",
    }
    if schema_version == PRICE_SCHEMA_VERSION_V2:
        expected_top_fields.add("raw_serverless_endpoints")
    if set(snapshot) != expected_top_fields:
        raise InputGateError(
            f"price snapshot fields must be exactly {sorted(expected_top_fields)!r}")
    if schema_version not in {PRICE_SCHEMA_VERSION, PRICE_SCHEMA_VERSION_V2}:
        raise InputGateError("unexpected price snapshot schema")
    if snapshot.get("execution_authorized") is not False:
        raise InputGateError("price snapshot cannot authorize execution")
    if snapshot.get("provider") != "Together":
        raise InputGateError("v3 price snapshot provider must be Together")
    verified_at = _timestamp(snapshot.get("verified_at_utc"), "verified_at_utc")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise InputGateError("as_of must be timezone-aware")
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

    serverless_by_model: dict[str, Mapping[str, Any]] | None = None
    if schema_version == PRICE_SCHEMA_VERSION_V2:
        endpoint_binding = _object(
            snapshot.get("raw_serverless_endpoints"), "raw_serverless_endpoints")
        expected_endpoint_fields = {"path", "canonical_sha256", "endpoint_count"}
        if set(endpoint_binding) != expected_endpoint_fields:
            raise InputGateError(
                "raw_serverless_endpoints fields must be exactly "
                f"{sorted(expected_endpoint_fields)!r}")
        endpoint_path_text = _text(
            endpoint_binding.get("path"), "raw_serverless_endpoints.path")
        endpoint_sha = _sha256(
            endpoint_binding.get("canonical_sha256"),
            "raw_serverless_endpoints.canonical_sha256",
        )
        endpoint_count = _positive_int(
            endpoint_binding.get("endpoint_count"),
            "raw_serverless_endpoints.endpoint_count",
        )
        if verify_catalog:
            raw_endpoints = _load_json(
                _resolve_path(endpoint_path_text, project_root))
            if canonical_sha256(raw_endpoints) != endpoint_sha:
                raise InputGateError(
                    "raw serverless endpoint inventory canonical hash mismatch")
            endpoint_entries = _catalog_entries(raw_endpoints)
            if len(endpoint_entries) != endpoint_count:
                raise InputGateError("raw serverless endpoint_count drifted")
            serverless_by_model = {}
            required_set = set(billed_model_registry(protocol))
            for model in required_set:
                matches = [
                    endpoint for endpoint in endpoint_entries
                    if endpoint.get("model") == model and endpoint.get("name") == model
                ]
                if len(matches) != 1:
                    raise InputGateError(
                        f"required model must have exactly one exact serverless endpoint: {model}")
                endpoint = matches[0]
                if endpoint.get("type") != "serverless":
                    raise InputGateError(
                        f"required model endpoint is not serverless: {model}")
                if endpoint.get("state") != "STARTED":
                    raise InputGateError(
                        f"required model serverless endpoint is not STARTED: {model}")
                serverless_by_model[model] = endpoint

    models = _object(snapshot.get("models"), "models")
    required_models = billed_model_registry(protocol)
    if set(models) != set(required_models):
        raise InputGateError("price snapshot models must equal the final billed-model roster")
    for model in required_models:
        entry = _object(models.get(model), f"models[{model!r}]")
        expected_fields = {
            "serverless_available", "input_usd_per_million",
            "output_usd_per_million", "catalog_entry_sha256",
        }
        if schema_version == PRICE_SCHEMA_VERSION_V2:
            expected_fields.add("serverless_endpoint_entry_sha256")
        if set(entry) != expected_fields:
            raise InputGateError(f"price entry fields drifted for {model}")
        if entry.get("serverless_available") is not True:
            raise InputGateError(f"required model is not verified serverless: {model}")
        prices: dict[str, float] = {}
        for field in ("input_usd_per_million", "output_usd_per_million"):
            value = entry.get(field)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(float(value)) or value <= 0):
                raise InputGateError(f"invalid {field} for {model}")
            prices[field] = float(value)
        catalog_entry_sha = _sha256(
            entry.get("catalog_entry_sha256"), f"models[{model!r}].catalog_entry_sha256")
        endpoint_entry_sha: str | None = None
        if schema_version == PRICE_SCHEMA_VERSION_V2:
            endpoint_entry_sha = _sha256(
                entry.get("serverless_endpoint_entry_sha256"),
                f"models[{model!r}].serverless_endpoint_entry_sha256",
            )
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
            if schema_version == PRICE_SCHEMA_VERSION_V2:
                assert serverless_by_model is not None
                if canonical_sha256(serverless_by_model[model]) != endpoint_entry_sha:
                    raise InputGateError(
                        f"serverless endpoint entry hash mismatch for {model}")
    return {
        "validation": "pass",
        "canonical_sha256": canonical_sha256(snapshot),
        "verified_at_utc": verified_at.isoformat().replace("+00:00", "Z"),
        "age_seconds": age.total_seconds(),
        "required_models": required_models,
        "raw_catalog_checked": verify_catalog,
        "raw_serverless_endpoints_checked": (
            verify_catalog and schema_version == PRICE_SCHEMA_VERSION_V2),
    }
