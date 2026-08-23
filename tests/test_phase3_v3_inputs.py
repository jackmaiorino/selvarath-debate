"""Tests for Phase 3 v3 exact-tokenizer and fresh-price gates."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from rejudge import phase3_v3_inputs as inputs
from rejudge import phase3_v3_materialization as materialization
from rejudge.phase2_execution import canonical_sha256

from tests.test_phase3_v3_materialization import _resolution


ROOT = Path(__file__).resolve().parents[1]
V2 = json.loads((ROOT / materialization.V2_PROTOCOL_PATH).read_text(encoding="utf-8"))
DESIGN = json.loads((ROOT / materialization.DESIGN_PATH).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def protocol():
    return materialization.materialize_protocol(
        V2, DESIGN, _resolution("excluded_deadline"))


def _tokenizer_manifest(protocol):
    models = {}
    for model in protocol["roster"]["judges_final"]:
        role_corpora = {}
        for role, variants in inputs.expected_role_variants(protocol, model).items():
            expected = 492 * len(variants)
            role_corpora[role] = {
                "variant_ids": variants,
                "transcript_count_per_variant": 492,
                "expected_rendered_prompt_count": expected,
                "actual_rendered_prompt_count": expected,
                "rendered_corpus": {"path": f"corpus/{model}/{role}.jsonl", "sha256": "3" * 64},
                "per_prompt_counts": {"path": f"counts/{model}/{role}.jsonl", "sha256": "4" * 64},
            }
        models[model] = {
            "classification": "exact_provider_tokenizer",
            "repository": f"repository/{model}",
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "provider_equivalence_evidence": "provider catalog exact repository mapping",
            "chat_template_sha256": "1" * 64,
            "files": {
                "tokenizer.json": {"path": f"tokenizers/{model}/tokenizer.json", "sha256": "2" * 64}
            },
            "role_corpora": role_corpora,
        }
    return {
        "schema_version": inputs.TOKENIZER_SCHEMA_VERSION,
        "execution_authorized": False,
        "source_transcript_bundle": {
            "path": "bundle.json",
            "canonical_sha256": "5" * 64,
            "transcript_count": 492,
            "transcript_keys_sha256": "6" * 64,
        },
        "models": models,
    }


def test_exact_tokenizer_shape_requires_every_model_role_variant_and_prompt(protocol):
    manifest = _tokenizer_manifest(protocol)
    report = inputs.validate_exact_tokenizer_manifest(
        manifest, protocol=protocol, verify_files=False)
    assert report["verified_model_count"] == 4
    expected = sum(
        492 * len(variants)
        for model in protocol["roster"]["judges_final"]
        for variants in inputs.expected_role_variants(protocol, model).values()
    )
    assert report["verified_rendered_prompt_count"] == expected
    assert report["local_files_checked"] is False


def test_exact_tokenizer_gate_rejects_proxy_missing_role_and_incomplete_corpus(protocol):
    manifest = _tokenizer_manifest(protocol)
    model = protocol["roster"]["judges_final"][-1]
    manifest["models"][model]["classification"] = "proxy_tokenizer_estimate"
    with pytest.raises(inputs.InputGateError, match="not exact"):
        inputs.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)

    manifest = _tokenizer_manifest(protocol)
    del manifest["models"][model]["role_corpora"]["judge_query"]
    with pytest.raises(inputs.InputGateError, match="billed roles"):
        inputs.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)

    manifest = _tokenizer_manifest(protocol)
    manifest["models"][model]["role_corpora"]["judge_verdict"][
        "actual_rendered_prompt_count"] -= 1
    with pytest.raises(inputs.InputGateError, match="incomplete"):
        inputs.validate_exact_tokenizer_manifest(
            manifest, protocol=protocol, verify_files=False)


def test_role_corpus_files_require_exact_transcript_coverage_and_one_to_one_counts(tmp_path: Path):
    model = "model"
    role = "judge_verdict"
    variant = "b0"
    transcript_keys = [f"{index:064x}" for index in range(492)]
    rendered_rows = []
    count_rows = []
    for index, transcript_key in enumerate(transcript_keys):
        rendered_prompt = f"rendered prompt {index}"
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
            "rendered_prompt": rendered_prompt,
            "rendered_prompt_sha256": rendered_sha,
        })
        count_rows.append({"prompt_key": prompt_key, "prompt_tokens": index + 1})
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
        transcript_keys=transcript_keys,
        corpus_entry=corpus,
        project_root=None,
    ) == 492

    count_rows[-1]["prompt_key"] = count_rows[0]["prompt_key"]
    counts_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in count_rows),
        encoding="utf-8", newline="\n")
    corpus["per_prompt_counts"]["sha256"] = inputs.sha256_file(counts_path)
    with pytest.raises(inputs.InputGateError, match="duplicate"):
        inputs._validate_role_corpus_files(
            model=model,
            role=role,
            variants=[variant],
            transcript_keys=transcript_keys,
            corpus_entry=corpus,
            project_root=None,
        )


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
        protocol, tmp_path / "catalog.json", verified_at="2026-08-28T23:59:59Z")
    with pytest.raises(inputs.InputGateError, match="predates"):
        inputs.validate_price_snapshot(
            snapshot,
            protocol=protocol,
            as_of=datetime(2026, 8, 29, 1, tzinfo=timezone.utc),
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
