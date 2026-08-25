"""Tests for Phase 3 v3 exact-tokenizer and fresh-price gates."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from rejudge import judge_loop, phase2_canary_gate
from rejudge import phase3_v3_inputs as inputs
from rejudge import phase3_v3_materialization as materialization
from rejudge import phase3_v3_static_prompts as static_prompts
from rejudge.config import ARMS, position_for
from rejudge.phase2_execution import canonical_sha256

from tests.test_phase3_v3_materialization import AMENDMENT, _resolution


ROOT = Path(__file__).resolve().parents[1]
V2 = json.loads((ROOT / materialization.V2_PROTOCOL_PATH).read_text(encoding="utf-8"))
DESIGN = json.loads((ROOT / materialization.DESIGN_PATH).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def protocol():
    return materialization.materialize_protocol(
        V2, DESIGN, _resolution(materialization.PROVIDER_UNAVAILABLE_OUTCOME),
        amendment=AMENDMENT)


def _tokenizer_manifest(protocol):
    models = {}
    for model in protocol["roster"]["judges_final"]:
        corpora = {}
        for dataset, transcript_count in inputs.TRANSCRIPT_BUNDLE_COUNTS.items():
            role_corpora = {}
            for role, variants in inputs.expected_role_variants(protocol, model).items():
                expected = transcript_count * len(variants)
                role_corpora[role] = {
                    "variant_ids": variants,
                    "transcript_count_per_variant": transcript_count,
                    "expected_rendered_prompt_count": expected,
                    "actual_rendered_prompt_count": expected,
                    "rendered_corpus": {
                        "path": f"corpus/{model}/{dataset}/{role}.jsonl",
                        "sha256": "3" * 64,
                    },
                    "per_prompt_counts": {
                        "path": f"counts/{model}/{dataset}/{role}.jsonl",
                        "sha256": "4" * 64,
                    },
                }
            corpora[dataset] = role_corpora
        models[model] = {
            "classification": "exact_provider_tokenizer",
            "repository": f"repository/{model}",
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "provider_equivalence_evidence": "provider catalog exact repository mapping",
            "tokenizer_directory": f"tokenizers/{model}",
            "tokenizer_class": "FakeTokenizer",
            "chat_template_sha256": "1" * 64,
            "chat_template_rendering": {
                "mode": static_prompts.NATIVE_RENDERING_MODE,
                "effective_chat_template_sha256": "1" * 64,
                "evidence": "the tokenizer's native chat template is applied without modification",
            },
            "files": {
                "tokenizer.json": {"path": f"tokenizers/{model}/tokenizer.json", "sha256": "2" * 64}
            },
            "corpora": corpora,
        }
    return {
        "schema_version": inputs.TOKENIZER_SCHEMA_VERSION,
        "execution_authorized": False,
        "protocol_canonical_sha256": canonical_sha256(protocol),
        "prompt_bundle": {
            "path": str(materialization.PROMPT_BUNDLE_PATH).replace("\\", "/"),
            "canonical_sha256": materialization.PROMPT_BUNDLE_CANONICAL_SHA256,
        },
        "query_checker_prompt": {
            "frozen_config": {
                "path": str(materialization.CHECKER_CONFIG_PATH).replace("\\", "/"),
                "canonical_sha256": materialization.CHECKER_CONFIG_CANONICAL_SHA256,
            },
            "validation_design": {
                "path": str(
                    materialization.CHECKER_VALIDATION_DESIGN_PATH).replace("\\", "/"),
                "canonical_sha256": (
                    materialization.CHECKER_VALIDATION_DESIGN_CANONICAL_SHA256),
            },
            "system_prompt_sha256": static_prompts.CHECKER_SYSTEM_PROMPT_SHA256,
            "user_template_sha256": static_prompts.CHECKER_USER_TEMPLATE_SHA256,
        },
        "world_documents": {},
        "transcript_bundles": {
            dataset: {
                "path": f"{dataset}_bundle.json",
                "canonical_sha256": "5" * 64,
                "transcript_count": transcript_count,
                "transcript_keys_sha256": "6" * 64,
            }
            for dataset, transcript_count in inputs.TRANSCRIPT_BUNDLE_COUNTS.items()
        },
        "models": models,
    }


def test_exact_tokenizer_shape_requires_every_model_role_variant_and_prompt(protocol):
    manifest = _tokenizer_manifest(protocol)
    report = inputs.validate_exact_tokenizer_manifest(
        manifest, protocol=protocol, verify_files=False)
    assert report["verified_model_count"] == 4
    expected = sum(
        transcript_count * len(variants)
        for model in protocol["roster"]["judges_final"]
        for transcript_count in inputs.TRANSCRIPT_BUNDLE_COUNTS.values()
        for variants in inputs.expected_role_variants(protocol, model).values()
    )
    assert expected == 56_700
    assert report["verified_rendered_prompt_count"] == expected
    assert report["local_files_checked"] is False
    judge = protocol["roster"]["judges_final"][0]
    variants = inputs.expected_role_variants(protocol, judge)
    assert len(variants["judge_query"]) == 8
    assert len(variants["judge_verdict"]) == 10
    assert len(variants["query_checker"]) == 32


def test_five_judge_exact_corpus_arithmetic_includes_checker_source_variants():
    protocol = materialization.materialize_protocol(
        V2, DESIGN, _resolution(materialization.INCLUDED_OUTCOME), amendment=AMENDMENT)
    expected = sum(
        transcript_count * len(variants)
        for model in protocol["roster"]["judges_final"]
        for transcript_count in inputs.TRANSCRIPT_BUNDLE_COUNTS.values()
        for variants in inputs.expected_role_variants(protocol, model).values()
    )
    checker = protocol["roster"]["query_checker"]
    assert len(inputs.expected_role_variants(protocol, checker)["query_checker"]) == 40
    assert expected == 70_740


def test_exact_tokenizer_gate_rejects_proxy_missing_role_and_incomplete_corpus(protocol):
    manifest = _tokenizer_manifest(protocol)
    model = protocol["roster"]["judges_final"][-1]
    manifest["models"][model]["classification"] = "proxy_tokenizer_estimate"
    with pytest.raises(inputs.InputGateError, match="not exact"):
        inputs.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)

    manifest = _tokenizer_manifest(protocol)
    del manifest["models"][model]["corpora"]["main"]["judge_query"]
    with pytest.raises(inputs.InputGateError, match="billed roles"):
        inputs.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)

    manifest = _tokenizer_manifest(protocol)
    manifest["models"][model]["corpora"]["main"]["judge_verdict"][
        "actual_rendered_prompt_count"] -= 1
    with pytest.raises(inputs.InputGateError, match="incomplete"):
        inputs.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)


def test_exact_tokenizer_gate_binds_chat_template_rendering_policy(protocol):
    manifest = _tokenizer_manifest(protocol)
    model = protocol["roster"]["judges_final"][0]
    manifest["models"][model]["chat_template_rendering"]["mode"] = "unapproved_mode"
    with pytest.raises(inputs.InputGateError, match="unsupported chat-template rendering mode"):
        inputs.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)


def test_gemma3n_provider_rendering_bypasses_only_the_frozen_guard(
    monkeypatch: pytest.MonkeyPatch,
):
    guard = static_prompts._ROLE_ALTERNATION_GUARD
    original_template = "template-prefix\n" + guard + "\ntemplate-suffix"
    effective_template = original_template.replace(guard, "")
    monkeypatch.setattr(
        static_prompts,
        "GEMMA3N_CHAT_TEMPLATE_SHA256",
        hashlib.sha256(original_template.encode("utf-8")).hexdigest(),
    )
    monkeypatch.setattr(
        static_prompts,
        "GEMMA3N_PROVIDER_EFFECTIVE_TEMPLATE_SHA256",
        hashlib.sha256(effective_template.encode("utf-8")).hexdigest(),
    )

    class GuardedTokenizer:
        chat_template = original_template

        def apply_chat_template(
            self, messages, *, tokenize, add_generation_prompt, chat_template=None,
        ):
            assert add_generation_prompt is True
            assert chat_template == effective_template
            rendered = json.dumps(messages, sort_keys=True, separators=(",", ":"))
            return list(rendered.encode("utf-8")) if tokenize else rendered

    tokenizer = GuardedTokenizer()
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "presentation"},
        {"role": "user", "content": "verdict"},
    ]
    rendered, count = static_prompts.render_chat_prompt(tokenizer, messages)
    override, policy = static_prompts.chat_template_rendering_policy(tokenizer)
    assert override == effective_template
    assert policy["mode"] == static_prompts.GEMMA3N_PROVIDER_RENDERING_MODE
    assert count == len(rendered.encode("utf-8"))


def test_exact_provider_template_is_used_without_native_template_substitution(
    monkeypatch: pytest.MonkeyPatch,
):
    provider_template = "provider-exact-template-v1"
    monkeypatch.setattr(
        static_prompts,
        "QWEN38_PROVIDER_CHAT_TEMPLATE_SHA256",
        hashlib.sha256(provider_template.encode("utf-8")).hexdigest(),
    )

    class ProviderTokenizer:
        chat_template = provider_template

        def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
            assert add_generation_prompt is True
            rendered = provider_template + json.dumps(
                messages, sort_keys=True, separators=(",", ":"))
            return list(rendered.encode("utf-8")) if tokenize else rendered

    tokenizer = ProviderTokenizer()
    override, policy = static_prompts.chat_template_rendering_policy(tokenizer)
    assert override is None
    assert policy["mode"] == static_prompts.QWEN38_PROVIDER_RENDERING_MODE
    rendered, count = static_prompts.render_chat_prompt(
        tokenizer, [{"role": "user", "content": "question"}])
    assert rendered.startswith(provider_template)
    assert count == len(rendered.encode("utf-8"))


def test_v5_manifest_requires_provider_binding_and_rendering_mode_agreement(protocol):
    manifest = _tokenizer_manifest(protocol)
    model = protocol["roster"]["judges_final"][0]
    manifest["schema_version"] = inputs.TOKENIZER_SCHEMA_VERSION_V5
    manifest["provider_chat_templates"] = {
        model: {"path": "provider-template.json", "canonical_sha256": "7" * 64}
    }
    manifest["models"][model]["chat_template_rendering"]["mode"] = (
        static_prompts.QWEN38_PROVIDER_RENDERING_MODE)
    report = inputs.validate_exact_tokenizer_manifest(
        manifest, protocol=protocol, verify_files=False)
    assert report["verified_model_count"] == 4

    manifest["models"][model]["chat_template_rendering"]["mode"] = (
        static_prompts.NATIVE_RENDERING_MODE)
    with pytest.raises(inputs.InputGateError, match="binding and rendering mode disagree"):
        inputs.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)


def test_role_corpus_files_recompute_prompts_and_exact_token_counts(protocol, tmp_path: Path):
    class FakeTokenizer:
        chat_template = "fake-template-v1"

        def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
            assert add_generation_prompt is True
            rendered = json.dumps(messages, sort_keys=True, separators=(",", ":"))
            return list(rendered.encode("utf-8")) if tokenize else rendered

    model = protocol["roster"]["judges_final"][0]
    role = "judge_verdict"
    variant = "judge_verdict::b0::side0"
    prompt_bundle = json.loads(
        (ROOT / materialization.PROMPT_BUNDLE_PATH).read_text(encoding="utf-8"))
    transcript_entries = {}
    for index in range(492):
        payload = {
            "question_id": f"Q-{index:03d}",
            "transcript_index": index,
            "world": "test_world",
            "question": f"Question {index}?",
            "correct_answer": f"Correct {index}",
            "wrong_answer": f"Wrong {index}",
            "debate_transcript": [],
            "debater_model": "debater",
        }
        entry = {
            "question_id": payload["question_id"],
            "debater_model": payload["debater_model"],
            "transcript_index": index,
            "transcript_sha256": canonical_sha256(payload),
            "transcript_payload": payload,
        }
        transcript_entries[static_prompts.transcript_key(entry)] = entry

    tokenizer = FakeTokenizer()
    rendered_rows = []
    count_rows = []
    for transcript_key, transcript_entry in transcript_entries.items():
        messages = static_prompts.render_static_messages(
            protocol=protocol,
            prompt_bundle=prompt_bundle,
            world_documents={"test_world": "World document"},
            transcript_entry=transcript_entry,
            billed_model=model,
            role=role,
            variant_id=variant,
        )
        rendered_prompt, prompt_tokens = static_prompts.render_chat_prompt(tokenizer, messages)
        rendered_sha = hashlib.sha256(rendered_prompt.encode("utf-8")).hexdigest()
        prompt_key = canonical_sha256({
            "model": model,
            "role": role,
            "variant_id": variant,
            "transcript_key": transcript_key,
            "rendered_prompt_sha256": rendered_sha,
        })
        rendered_rows.append({
            "prompt_key": prompt_key,
            "transcript_key": transcript_key,
            "variant_id": variant,
            "rendered_prompt_sha256": rendered_sha,
        })
        count_rows.append({"prompt_key": prompt_key, "prompt_tokens": prompt_tokens})
    rendered_path = tmp_path / "rendered.jsonl"
    counts_path = tmp_path / "counts.jsonl"
    rendered_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rendered_rows),
        encoding="utf-8", newline="\n")
    counts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in count_rows),
        encoding="utf-8", newline="\n")
    corpus = {
        "rendered_corpus": {
            "path": str(rendered_path), "sha256": inputs.sha256_file(rendered_path)},
        "per_prompt_counts": {
            "path": str(counts_path), "sha256": inputs.sha256_file(counts_path)},
    }
    assert inputs._validate_role_corpus_files(
        model=model,
        role=role,
        variants=[variant],
        transcript_entries=transcript_entries,
        corpus_entry=corpus,
        project_root=None,
        protocol=protocol,
        prompt_bundle=prompt_bundle,
        world_documents={"test_world": "World document"},
        tokenizer=tokenizer,
    ) == 492

    count_rows[-1]["prompt_tokens"] += 1
    counts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in count_rows),
        encoding="utf-8", newline="\n")
    corpus["per_prompt_counts"]["sha256"] = inputs.sha256_file(counts_path)
    with pytest.raises(inputs.InputGateError, match="exact tokenizer count mismatch"):
        inputs._validate_role_corpus_files(
            model=model,
            role=role,
            variants=[variant],
            transcript_entries=transcript_entries,
            corpus_entry=corpus,
            project_root=None,
            protocol=protocol,
            prompt_bundle=prompt_bundle,
            world_documents={"test_world": "World document"},
            tokenizer=tokenizer,
        )


def test_static_query_checker_uses_the_live_frozen_runtime_prompt(protocol):
    payload = {
        "question_id": "Q-checker",
        "transcript_index": 0,
        "world": "test_world",
        "question": "Question?",
        "correct_answer": "Correct",
        "wrong_answer": "Wrong",
        "debate_transcript": [],
        "debater_model": "debater",
    }
    transcript = {
        "question_id": payload["question_id"],
        "debater_model": payload["debater_model"],
        "transcript_index": 0,
        "transcript_sha256": canonical_sha256(payload),
        "transcript_payload": payload,
    }
    source_judge = protocol["roster"]["judges_final"][0]
    billed_model = protocol["roster"]["query_checker"]
    prompt_bundle = json.loads(
        (ROOT / materialization.PROMPT_BUNDLE_PATH).read_text(encoding="utf-8"))
    messages = static_prompts.render_static_messages(
        protocol=protocol,
        prompt_bundle=prompt_bundle,
        world_documents={},
        transcript_entry=transcript,
        billed_model=billed_model,
        role="query_checker",
        variant_id=f"query_checker::{source_judge}::sequential_b1::side0",
    )
    assert messages[0]["content"] == phase2_canary_gate.load_frozen_checker_prompt()
    position_a_is_correct = position_for(
        ARMS["clean"], payload["question_id"], payload["transcript_index"],
        source_judge, 1,
    )
    candidate_a, candidate_b, _ = judge_loop._format_transcript(
        payload, position_a_is_correct)
    assert messages[1]["content"] == phase2_canary_gate.load_frozen_checker_user_template().format(
        candidate_a=candidate_a, candidate_b=candidate_b, query="")


def _price_snapshot(protocol, raw_path: Path, *, verified_at: str):
    raw_catalog: list[dict[str, Any]] = [
        {
            "id": model,
            "type": "chat",
            "pricing": {"input": float(index + 1), "output": float(index + 2)},
        }
        for index, model in enumerate(protocol["roster"]["judges_final"])
    ]
    raw_path.write_text(json.dumps(raw_catalog), encoding="utf-8")
    return {
        "schema_version": inputs.PRICE_SCHEMA_VERSION,
        "provider": "Together",
        "verified_at_utc": verified_at,
        "execution_authorized": False,
        "raw_catalog": {
            "path": str(raw_path),
            "canonical_sha256": canonical_sha256(raw_catalog),
            "model_count": len(raw_catalog),
        },
        "models": {
            entry["id"]: {
                "serverless_available": True,
                "input_usd_per_million": entry["pricing"]["input"],
                "output_usd_per_million": entry["pricing"]["output"],
                "catalog_entry_sha256": canonical_sha256(entry),
            }
            for entry in raw_catalog
        },
    }


def test_price_snapshot_is_raw_catalog_bound_post_resolution_and_under_24_hours(
    protocol, tmp_path: Path,
):
    snapshot = _price_snapshot(
        protocol, tmp_path / "catalog.json", verified_at="2026-08-29T01:00:00Z")
    report = inputs.validate_price_snapshot(
        snapshot,
        protocol=protocol,
        as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
    )
    assert report["age_seconds"] == 3600
    assert report["raw_catalog_checked"] is True

    with pytest.raises(inputs.InputGateError, match="24-hour"):
        inputs.validate_price_snapshot(
            snapshot,
            protocol=protocol,
            as_of=datetime(2026, 8, 30, 2, tzinfo=timezone.utc),
        )


def test_price_snapshot_rejects_pre_resolution_price_drift_and_unavailable_model(
    protocol, tmp_path: Path,
):
    snapshot = _price_snapshot(
        protocol, tmp_path / "catalog.json", verified_at="2026-08-24T01:04:54Z")
    with pytest.raises(inputs.InputGateError, match="predates"):
        inputs.validate_price_snapshot(
            snapshot,
            protocol=protocol,
            as_of=datetime(2026, 8, 24, 2, tzinfo=timezone.utc),
        )

    snapshot = _price_snapshot(
        protocol, tmp_path / "catalog2.json", verified_at="2026-08-29T01:00:00Z")
    model = protocol["roster"]["judges_final"][0]
    snapshot["models"][model]["input_usd_per_million"] += 0.5
    with pytest.raises(inputs.InputGateError, match="disagrees"):
        inputs.validate_price_snapshot(
            snapshot,
            protocol=protocol,
            as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
        )

    snapshot = _price_snapshot(
        protocol, tmp_path / "catalog3.json", verified_at="2026-08-29T01:00:00Z")
    snapshot["models"][model]["serverless_available"] = False
    with pytest.raises(inputs.InputGateError, match="not verified serverless"):
        inputs.validate_price_snapshot(
            snapshot,
            protocol=protocol,
            as_of=datetime(2026, 8, 29, 2, tzinfo=timezone.utc),
        )
