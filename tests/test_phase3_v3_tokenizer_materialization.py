"""Tests for offline Phase 3 v3 exact-tokenizer corpus materialization."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from rejudge import phase3_v3_inputs as inputs
from rejudge import phase3_v3_materialization as protocol_materialization
from rejudge import phase3_v3_static_prompts as static_prompts
from rejudge import phase3_v3_tokenizer_materialization as tokenizers
from rejudge.phase2_execution import canonical_sha256

from tests.test_phase3_v3_materialization import AMENDMENT, _resolution


ROOT = Path(__file__).resolve().parents[1]
V2 = json.loads(
    (ROOT / protocol_materialization.V2_PROTOCOL_PATH).read_text(encoding="utf-8"))
DESIGN = json.loads(
    (ROOT / protocol_materialization.DESIGN_PATH).read_text(encoding="utf-8"))


class FakeTokenizer:
    chat_template = "fake-template-v1"

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert add_generation_prompt is True
        rendered = json.dumps(messages, sort_keys=True, separators=(",", ":"))
        return list(rendered.encode("utf-8")) if tokenize else rendered


def _stage(tmp_path: Path):
    protocol = protocol_materialization.materialize_protocol(
        V2, DESIGN,
        _resolution(protocol_materialization.PROVIDER_UNAVAILABLE_OUTCOME),
        amendment=AMENDMENT,
    )
    prompt_path = tmp_path / protocol_materialization.PROMPT_BUNDLE_PATH
    prompt_path.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / protocol_materialization.PROMPT_BUNDLE_PATH, prompt_path)
    world_dir = tmp_path / "world_specs"
    world_dir.mkdir()
    (world_dir / "test_world.txt").write_text(
        "A deterministic fictional world.", encoding="utf-8")

    source_paths = {}
    for dataset, transcript_count in inputs.TRANSCRIPT_BUNDLE_COUNTS.items():
        transcripts = []
        for index in range(transcript_count):
            payload = {
                "question_id": f"{dataset}-Q-{index:03d}",
                "transcript_index": index,
                "world": "test_world",
                "question": f"{dataset} question {index}?",
                "correct_answer": f"Correct {index}",
                "wrong_answer": f"Wrong {index}",
                "debate_transcript": [],
                "debater_model": "debater",
            }
            transcripts.append({
                "question_id": payload["question_id"],
                "debater_model": payload["debater_model"],
                "transcript_index": index,
                "transcript_sha256": canonical_sha256(payload),
                "transcript_payload": payload,
            })
        source = {"transcripts": transcripts}
        source_path = tmp_path / f"{dataset}_transcripts.json"
        source_path.write_text(json.dumps(source), encoding="utf-8")
        source_paths[dataset] = source_path

    models = {}
    for index, model in enumerate(protocol["roster"]["judges_final"]):
        tokenizer_dir = tmp_path / "tokenizers" / str(index)
        tokenizer_dir.mkdir(parents=True)
        (tokenizer_dir / "tokenizer.json").write_text(
            json.dumps({"model": model}), encoding="utf-8")
        models[model] = {
            "classification": "exact_provider_tokenizer",
            "repository": f"repository/{model}",
            "revision": "0123456789abcdef0123456789abcdef01234567",
            "provider_equivalence_evidence": "exact provider mapping evidence",
            "local_tokenizer_directory": tokenizer_dir.relative_to(tmp_path).as_posix(),
        }
    spec = {
        "schema_version": tokenizers.SPEC_SCHEMA_VERSION,
        "execution_authorized": False,
        "models": models,
    }
    return protocol, prompt_path, world_dir, source_paths, spec


def test_builder_recomputes_and_validates_all_emitted_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    protocol, prompt_path, world_dir, source_paths, spec = _stage(tmp_path)
    one_variant = {"judge_verdict": ["judge_verdict::b0::side0"]}
    monkeypatch.setattr(
        static_prompts, "expected_role_variants",
        lambda _protocol, _model: one_variant,
    )
    result = tokenizers.build_exact_tokenizer_artifacts(
        protocol=protocol,
        tokenizer_spec=spec,
        transcript_bundle_paths=source_paths,
        prompt_bundle_path=prompt_path,
        world_documents_directory=world_dir,
        output_directory=tmp_path / "output",
        project_root=tmp_path,
        tokenizer_loader=lambda _path: FakeTokenizer(),
    )
    expected_prompts = sum(inputs.TRANSCRIPT_BUNDLE_COUNTS.values()) * 4
    assert result["rendered_prompt_count"] == expected_prompts
    assert result["execution_authorized"] is False
    manifest = result["manifest"]
    report = inputs.validate_exact_tokenizer_manifest(
        manifest,
        protocol=protocol,
        project_root=tmp_path,
        tokenizer_loader=lambda _path: FakeTokenizer(),
    )
    assert report["verified_rendered_prompt_count"] == expected_prompts

    first_model = protocol["roster"]["judges_final"][0]
    corpus = manifest["models"][first_model]["corpora"]["main"]["judge_verdict"]
    rendered_path = tmp_path / corpus["rendered_corpus"]["path"]
    first_row = json.loads(rendered_path.read_text(encoding="utf-8").splitlines()[0])
    assert set(first_row) == {
        "prompt_key", "transcript_key", "variant_id", "rendered_prompt_sha256",
    }


def test_builder_rejects_proxy_spec_before_creating_output(tmp_path: Path):
    protocol, prompt_path, world_dir, source_paths, spec = _stage(tmp_path)
    model = protocol["roster"]["judges_final"][0]
    spec["models"][model]["classification"] = "proxy_tokenizer_estimate"
    output = tmp_path / "output"
    with pytest.raises(tokenizers.TokenizerMaterializationError, match="not exact"):
        tokenizers.build_exact_tokenizer_artifacts(
            protocol=protocol,
            tokenizer_spec=spec,
            transcript_bundle_paths=source_paths,
            prompt_bundle_path=prompt_path,
            world_documents_directory=world_dir,
            output_directory=output,
            project_root=tmp_path,
            tokenizer_loader=lambda _path: FakeTokenizer(),
        )
    assert not output.exists()
