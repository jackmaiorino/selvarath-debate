"""Offline materialization of exact-tokenizer static prompt corpora for Phase 3 v3."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping

from rejudge import (
    phase2_canary_gate,
    phase3_plan,
    phase3_v3_inputs,
    phase3_v3_static_prompts as static_prompts,
)
from rejudge.phase2_execution import canonical_sha256


SPEC_SCHEMA_VERSION = "phase3_v3_exact_tokenizer_spec_v1"
DEFAULT_MANIFEST_NAME = "exact_tokenizer_manifest.json"


class TokenizerMaterializationError(ValueError):
    """Raised when local exact-tokenizer artifacts cannot be materialized."""


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise TokenizerMaterializationError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TokenizerMaterializationError(f"{label} must be a non-empty string")
    return value


def _portable_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _load_local_tokenizer(path: Path) -> Any:
    try:
        from transformers import AutoTokenizer
        return AutoTokenizer.from_pretrained(
            str(path), local_files_only=True, trust_remote_code=False)
    except Exception as exc:  # noqa: BLE001 - normalize tokenizer-specific failures
        raise TokenizerMaterializationError(
            f"could not load local tokenizer {path}: {exc}") from exc


def _validate_spec(
    spec: Mapping[str, Any], protocol: Mapping[str, Any], project_root: Path,
) -> dict[str, dict[str, Any]]:
    if set(spec) != {"schema_version", "execution_authorized", "models"}:
        raise TokenizerMaterializationError("tokenizer spec fields drifted")
    if spec.get("schema_version") != SPEC_SCHEMA_VERSION:
        raise TokenizerMaterializationError("unexpected tokenizer spec schema")
    if spec.get("execution_authorized") is not False:
        raise TokenizerMaterializationError("tokenizer spec cannot authorize execution")
    raw_models = _object(spec.get("models"), "models")
    required_models = list(protocol["roster"]["judges_final"])
    if set(raw_models) != set(required_models):
        raise TokenizerMaterializationError("tokenizer spec models must equal the final roster")
    models: dict[str, dict[str, Any]] = {}
    expected_fields = {
        "classification", "repository", "revision", "provider_equivalence_evidence",
        "local_tokenizer_directory",
    }
    for model in required_models:
        entry = _object(raw_models.get(model), f"models[{model!r}]")
        if set(entry) != expected_fields:
            raise TokenizerMaterializationError(f"tokenizer spec fields drifted for {model}")
        if entry.get("classification") != "exact_provider_tokenizer":
            raise TokenizerMaterializationError(
                f"tokenizer spec for {model} is not exact and provider-matched")
        normalized = dict(entry)
        for field in ("repository", "revision", "provider_equivalence_evidence"):
            normalized[field] = _text(entry.get(field), f"models[{model!r}].{field}")
        raw_directory = Path(_text(
            entry.get("local_tokenizer_directory"),
            f"models[{model!r}].local_tokenizer_directory",
        ))
        tokenizer_directory = (
            raw_directory if raw_directory.is_absolute() else project_root / raw_directory)
        if not tokenizer_directory.is_dir():
            raise TokenizerMaterializationError(
                f"local tokenizer directory does not exist: {tokenizer_directory}")
        normalized["local_tokenizer_directory"] = tokenizer_directory
        models[model] = normalized
    return models


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TokenizerMaterializationError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TokenizerMaterializationError(f"{label} must be a JSON object")
    return value


def build_exact_tokenizer_artifacts(
    *,
    protocol: Mapping[str, Any],
    tokenizer_spec: Mapping[str, Any],
    transcript_bundle_paths: Mapping[str, str | Path],
    prompt_bundle_path: str | Path,
    world_documents_directory: str | Path,
    output_directory: str | Path,
    project_root: str | Path,
    tokenizer_loader: Callable[[Path], Any] | None = None,
) -> dict[str, Any]:
    """Build all corpora and a manifest from already-local tokenizer directories."""
    phase3_plan.validate_protocol(protocol)
    root = Path(project_root)
    output = Path(output_directory)
    if not output.is_absolute():
        output = root / output
    if output.exists():
        raise TokenizerMaterializationError(
            f"refusing to overwrite existing tokenizer output directory: {output}")
    if set(transcript_bundle_paths) != set(phase3_v3_inputs.TRANSCRIPT_BUNDLE_COUNTS):
        raise TokenizerMaterializationError(
            "transcript_bundle_paths must contain exactly main and canary")
    source_paths: dict[str, Path] = {}
    for dataset, raw_path in transcript_bundle_paths.items():
        source_path = Path(raw_path)
        if not source_path.is_absolute():
            source_path = root / source_path
        source_paths[dataset] = source_path
    prompt_path = Path(prompt_bundle_path)
    if not prompt_path.is_absolute():
        prompt_path = root / prompt_path
    worlds_dir = Path(world_documents_directory)
    if not worlds_dir.is_absolute():
        worlds_dir = root / worlds_dir

    models = _validate_spec(tokenizer_spec, protocol, root)
    prompt_bundle = _load_json_object(prompt_path, "prompt bundle")
    checker_config = _load_json_object(
        phase2_canary_gate.FROZEN_CONFIG_PATH, "query-checker frozen config")
    checker_design = _load_json_object(
        phase2_canary_gate.DESIGN_PATH, "query-checker validation design")
    source_bundles: dict[str, dict[str, Any]] = {}
    transcript_entries_by_dataset: dict[str, dict[str, Mapping[str, Any]]] = {}
    for dataset, required_count in phase3_v3_inputs.TRANSCRIPT_BUNDLE_COUNTS.items():
        source_bundle = _load_json_object(
            source_paths[dataset], f"{dataset} transcript bundle")
        source_bundles[dataset] = source_bundle
        transcript_entries_by_dataset[dataset] = phase3_v3_inputs.transcript_entries(
            source_bundle, expected_count=required_count)
    worlds = sorted({
        str(entry["transcript_payload"]["world"])
        for transcript_entries in transcript_entries_by_dataset.values()
        for entry in transcript_entries.values()
    })
    world_documents: dict[str, str] = {}
    world_bindings: dict[str, dict[str, str]] = {}
    for world in worlds:
        world_path = worlds_dir / f"{world}.txt"
        try:
            content = world_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise TokenizerMaterializationError(
                f"could not read world document {world_path}: {exc}") from exc
        world_documents[world] = _text(content, f"world document {world!r}")
        world_bindings[world] = {
            "path": _portable_path(world_path, root),
            "sha256": phase3_v3_inputs.sha256_file(world_path),
        }

    loader = tokenizer_loader or _load_local_tokenizer
    output.mkdir(parents=True)
    try:
        manifest_models: dict[str, Any] = {}
        used_slugs: set[str] = set()
        for model in protocol["roster"]["judges_final"]:
            spec_entry = models[model]
            tokenizer_directory = Path(spec_entry["local_tokenizer_directory"])
            tokenizer = loader(tokenizer_directory)
            chat_template = getattr(tokenizer, "chat_template", None)
            if not isinstance(chat_template, str) or not chat_template:
                raise TokenizerMaterializationError(
                    f"local tokenizer for {model} has no chat template")
            _template_override, rendering_policy = (
                static_prompts.chat_template_rendering_policy(tokenizer))
            files = sorted(path for path in tokenizer_directory.rglob("*") if path.is_file())
            if not files:
                raise TokenizerMaterializationError(
                    f"local tokenizer directory for {model} has no files")
            file_bindings = {
                path.relative_to(tokenizer_directory).as_posix(): {
                    "path": _portable_path(path, root),
                    "sha256": phase3_v3_inputs.sha256_file(path),
                }
                for path in files
            }
            slug = model.replace("/", "--")
            if slug in used_slugs:
                raise TokenizerMaterializationError("tokenizer model path slugs collide")
            used_slugs.add(slug)
            model_dir = output / slug
            model_dir.mkdir()
            corpora: dict[str, Any] = {}
            for dataset, transcript_entries in transcript_entries_by_dataset.items():
                dataset_dir = model_dir / dataset
                dataset_dir.mkdir()
                role_corpora: dict[str, Any] = {}
                for role, variants in phase3_v3_inputs.expected_role_variants(
                        protocol, model).items():
                    rendered_path = dataset_dir / f"{role}.rendered.jsonl"
                    counts_path = dataset_dir / f"{role}.counts.jsonl"

                    def rows():
                        for variant_id in variants:
                            for transcript_key, transcript_entry in transcript_entries.items():
                                messages = static_prompts.render_static_messages(
                                    protocol=protocol,
                                    prompt_bundle=prompt_bundle,
                                    world_documents=world_documents,
                                    transcript_entry=transcript_entry,
                                    billed_model=model,
                                    role=role,
                                    variant_id=variant_id,
                                )
                                rendered, prompt_tokens = static_prompts.render_chat_prompt(
                                    tokenizer, messages)
                                rendered_sha = hashlib.sha256(
                                    rendered.encode("utf-8")).hexdigest()
                                prompt_key = canonical_sha256({
                                    "model": model,
                                    "role": role,
                                    "variant_id": variant_id,
                                    "transcript_key": transcript_key,
                                    "rendered_prompt_sha256": rendered_sha,
                                })
                                yield (
                                    {
                                        "prompt_key": prompt_key,
                                        "transcript_key": transcript_key,
                                        "variant_id": variant_id,
                                        "rendered_prompt_sha256": rendered_sha,
                                    },
                                    {"prompt_key": prompt_key, "prompt_tokens": prompt_tokens},
                                )

                    prompt_count = 0
                    with rendered_path.open(
                            "x", encoding="utf-8", newline="\n") as rendered_handle, \
                            counts_path.open(
                                "x", encoding="utf-8", newline="\n") as counts_handle:
                        for rendered_row, count_row in rows():
                            rendered_handle.write(
                                json.dumps(
                                    rendered_row, sort_keys=True, ensure_ascii=True) + "\n")
                            counts_handle.write(
                                json.dumps(count_row, sort_keys=True, ensure_ascii=True) + "\n")
                            prompt_count += 1
                    required_count = phase3_v3_inputs.TRANSCRIPT_BUNDLE_COUNTS[dataset]
                    expected_count = required_count * len(variants)
                    if prompt_count != expected_count:
                        raise TokenizerMaterializationError(
                            f"rendered prompt count drifted for {model}.{dataset}.{role}")
                    role_corpora[role] = {
                        "variant_ids": variants,
                        "transcript_count_per_variant": required_count,
                        "expected_rendered_prompt_count": expected_count,
                        "actual_rendered_prompt_count": prompt_count,
                        "rendered_corpus": {
                            "path": _portable_path(rendered_path, root),
                            "sha256": phase3_v3_inputs.sha256_file(rendered_path),
                        },
                        "per_prompt_counts": {
                            "path": _portable_path(counts_path, root),
                            "sha256": phase3_v3_inputs.sha256_file(counts_path),
                        },
                    }
                corpora[dataset] = role_corpora
            manifest_models[model] = {
                "classification": "exact_provider_tokenizer",
                "repository": spec_entry["repository"],
                "revision": spec_entry["revision"],
                "provider_equivalence_evidence": spec_entry["provider_equivalence_evidence"],
                "tokenizer_directory": _portable_path(tokenizer_directory, root),
                "tokenizer_class": type(tokenizer).__name__,
                "chat_template_sha256": hashlib.sha256(
                    chat_template.encode("utf-8")).hexdigest(),
                "chat_template_rendering": rendering_policy,
                "files": file_bindings,
                "corpora": corpora,
            }

        manifest = {
            "schema_version": phase3_v3_inputs.TOKENIZER_SCHEMA_VERSION,
            "execution_authorized": False,
            "protocol_canonical_sha256": canonical_sha256(protocol),
            "prompt_bundle": {
                "path": _portable_path(prompt_path, root),
                "canonical_sha256": canonical_sha256(prompt_bundle),
            },
            "query_checker_prompt": {
                "frozen_config": {
                    "path": _portable_path(phase2_canary_gate.FROZEN_CONFIG_PATH, root),
                    "canonical_sha256": canonical_sha256(checker_config),
                },
                "validation_design": {
                    "path": _portable_path(phase2_canary_gate.DESIGN_PATH, root),
                    "canonical_sha256": canonical_sha256(checker_design),
                },
                "system_prompt_sha256": static_prompts.CHECKER_SYSTEM_PROMPT_SHA256,
                "user_template_sha256": static_prompts.CHECKER_USER_TEMPLATE_SHA256,
            },
            "world_documents": world_bindings,
            "transcript_bundles": {
                dataset: {
                    "path": _portable_path(source_paths[dataset], root),
                    "canonical_sha256": canonical_sha256(source_bundles[dataset]),
                    "transcript_count": len(transcript_entries_by_dataset[dataset]),
                    "transcript_keys_sha256": canonical_sha256(
                        list(transcript_entries_by_dataset[dataset])),
                }
                for dataset in phase3_v3_inputs.TRANSCRIPT_BUNDLE_COUNTS
            },
            "models": manifest_models,
        }
        phase3_v3_inputs.validate_exact_tokenizer_manifest(
            manifest,
            protocol=protocol,
            project_root=root,
            tokenizer_loader=loader,
        )
        manifest_path = output / DEFAULT_MANIFEST_NAME
        with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, indent=1, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
        return {
            "manifest": manifest,
            "manifest_path": _portable_path(manifest_path, root),
            "canonical_sha256": canonical_sha256(manifest),
            "rendered_prompt_count": sum(
                corpus["actual_rendered_prompt_count"]
                for entry in manifest_models.values()
                for role_corpora in entry["corpora"].values()
                for corpus in role_corpora.values()
            ),
            "execution_authorized": False,
        }
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise
