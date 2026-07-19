"""Tests for scripts/build_phase2_preflight_manifest.py.

These tests read the REAL, tracked repo artifacts (there is no synthetic sandbox root: many of
the artifacts this builder binds are pinned by ``rejudge.phase2_execution`` to their one exact,
git-tracked path -- see ``_resolve_pinned_artifact`` -- so a faithful test has to exercise the
real repo root). They never call ``main()`` (which writes the committed manifest/authorization
files): the build-and-validate logic is tested directly via
``build_manifest_and_authorization`` + ``rejudge.phase2_execution.validate_execution_manifest``,
which is equivalent (write-then-reload is proven to be a lossless byte round trip by
``test_determinism_two_builds_are_byte_identical`` and by ``write_canonical_json``/
``load_execution_manifest`` themselves) without mutating tracked repo state as a test side
effect. The one exception is ``test_committed_artifacts_pass_real_validator``, which reads
(never writes) the actual committed ``rejudge/phase2_preflight_manifest_2026-07-19.json`` /
``..._authorization_2026-07-19.json`` produced by a real run of this builder.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from rejudge import api_client  # noqa: E402
from rejudge import phase2_capability_corpus as capability_corpus  # noqa: E402
from rejudge import phase2_execution as pe  # noqa: E402
from rejudge import phase2_plan  # noqa: E402
from rejudge import phase2_prompt_bundle as prompt_bundle  # noqa: E402
from rejudge import phase2_role_limits as role_limits  # noqa: E402
from scripts import build_phase2_preflight_manifest as b  # noqa: E402


FIXED_RECORDED_AT_UTC = "2026-07-19T01:00:00Z"


# --- shared, module-scoped build (expensive: renders the 212-entry corpus + constructs a
# --- RejudgeClient for 1,060 request-hash computations) --------------------------------------


@pytest.fixture(scope="module")
def built():
    return b.build_manifest_and_authorization(REPO_ROOT, recorded_at_utc=FIXED_RECORDED_AT_UTC)


@pytest.fixture(scope="module")
def built_again():
    """A SECOND, independent build with the same fixed timestamp, for determinism checks."""
    return b.build_manifest_and_authorization(REPO_ROOT, recorded_at_utc=FIXED_RECORDED_AT_UTC)


# ================================================================================================
# Determinism
# ================================================================================================


def test_determinism_two_builds_are_byte_identical(built, built_again):
    assert b.canonical_json_bytes(built.manifest) == b.canonical_json_bytes(built_again.manifest)
    assert b.canonical_json_bytes(built.authorization) == b.canonical_json_bytes(
        built_again.authorization)
    assert b.canonical_json_bytes(built.seed_derivation) == b.canonical_json_bytes(
        built_again.seed_derivation)
    assert built.execution_identity_sha256 == built_again.execution_identity_sha256


def test_determinism_recorded_at_utc_override_is_the_only_time_dependent_field(built):
    # Rebuilding with a DIFFERENT recorded_at_utc changes only that one field; everything else
    # (including the derived execution_identity_sha256, which recorded_at_utc never feeds) stays
    # byte-identical.
    other = b.build_manifest_and_authorization(REPO_ROOT, recorded_at_utc="2026-07-19T09:30:00Z")
    assert b.canonical_json_bytes(built.manifest) == b.canonical_json_bytes(other.manifest)
    assert built.execution_identity_sha256 == other.execution_identity_sha256
    assert built.authorization["recorded_at_utc"] != other.authorization["recorded_at_utc"]
    stripped_a = {k: v for k, v in built.authorization.items() if k != "recorded_at_utc"}
    stripped_b = {k: v for k, v in other.authorization.items() if k != "recorded_at_utc"}
    assert stripped_a == stripped_b


# ================================================================================================
# Seed derivation: frozen formula, vector tests
# ================================================================================================


def _reference_seed(namespace, question_id, model, condition, replicate_index) -> int:
    """Independent reimplementation (deliberately NOT calling production code) for vector tests."""
    payload = {
        "namespace": namespace, "question_id": question_id, "model": model,
        "condition": condition, "replicate_index": replicate_index, "call_role": "capability_qa",
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


@pytest.mark.parametrize("namespace,question_id,model,condition,replicate_index,expected", [
    ("ns-a", "q1", "model-x", "full_document_solo_qa", 0, 1470575133),
    ("ns-a", "q1", "model-x", "full_document_solo_qa", 1, 1076985575),
    ("ns-a", "q2", "model-x", "full_document_solo_qa", 0, 3406993341),
    ("ns-a", "q1", "model-y", "full_document_solo_qa", 0, 2063208643),
    (
        "phase2-pooled-hpr-2026-07-16-v1.qb-d9e52c3339ab", "carath_norn_q001",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo", "full_document_solo_qa", 0, 2108331056,
    ),
])
def test_seed_derivation_vectors(namespace, question_id, model, condition, replicate_index,
                                  expected):
    observed = b.derive_capability_qa_seed(
        namespace=namespace, question_id=question_id, model=model, condition=condition,
        replicate_index=replicate_index)
    assert observed == expected
    assert observed == _reference_seed(namespace, question_id, model, condition, replicate_index)


def test_seed_derivation_is_a_pure_function_of_its_named_inputs():
    # Changing ANY one dimension changes the seed (no accidental collapsing of dimensions), and
    # the same inputs always reproduce the same seed (no hidden nondeterminism).
    def seed(namespace: str = "ns", question_id: str = "q", model: str = "m",
              condition: str = "c", replicate_index: int = 0) -> int:
        return b.derive_capability_qa_seed(
            namespace=namespace, question_id=question_id, model=model, condition=condition,
            replicate_index=replicate_index)

    baseline = seed()
    assert seed() == baseline
    assert seed(namespace="ns2") != baseline
    assert seed(question_id="q2") != baseline
    assert seed(model="m2") != baseline
    assert seed(condition="c2") != baseline
    assert seed(replicate_index=1) != baseline


def test_seed_derivation_never_folds_in_attempt(built):
    # No attempt dimension anywhere in the formula: the same (namespace, question, model,
    # condition, replicate) pair always yields the same seed regardless of any transport retry
    # -- transport_retry_policy ("repeat identical request and seed") owns attempt-level
    # variation, not the seed derivation.
    import inspect
    assert "attempt" not in inspect.signature(b.derive_capability_qa_seed).parameters


def test_manifest_seeds_are_32_bit_non_negative_ints(built):
    seeds = [entry["seed"] for entry in built.manifest["provider_call_inventory"]]
    assert len(seeds) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    for seed in seeds:
        assert isinstance(seed, int) and not isinstance(seed, bool)
        assert 0 <= seed < 2**32


def test_seed_derivation_sidecar_is_not_bound_into_the_manifest(built):
    # The KNOWN DEVIATION: seed_derivation cannot be a manifest top-level key (frozen exact key
    # set); confirm it truly is absent from the manifest and lives only in the sidecar.
    assert "seed_derivation" not in built.manifest
    assert set(built.manifest) == pe.MANIFEST_TOP_LEVEL_KEYS
    assert built.seed_derivation["execution_authorized"] is False
    assert built.seed_derivation["execution_identity_sha256"] == built.execution_identity_sha256


# ================================================================================================
# Request-fields hash fidelity: byte-exact reuse of api_client's own code, proven against a live
# (fake-SDK) complete() call for a diverse sample of manifest entries.
# ================================================================================================


class _FakeUsage:
    def __init__(self, prompt_tokens: int = 11, completion_tokens: int = 4) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.completion_tokens_details = None


class _FakeChatCompletions:
    """Minimal fake of ``together.Together().chat.completions``.

    Branches on ``kwargs["stream"]`` exactly like the real Together endpoint would: a
    non-streaming call returns one response object; a streaming call returns an iterable of
    chunk objects, with usage on the final chunk (mirrors ``RejudgeClient._streamed_create``'s
    own documented expectation).
    """

    def __init__(self) -> None:
        self._counter = 0

    def create(self, **kwargs):
        self._counter += 1
        response_id = f"fake-{self._counter}"
        model = kwargs["model"]
        content = "ANSWER: A"
        if kwargs.get("stream"):
            first = SimpleNamespace(
                usage=None, id=response_id, model=model, system_fingerprint=None,
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=content), finish_reason=None)])
            second = SimpleNamespace(
                usage=_FakeUsage(), id=response_id, model=model,
                system_fingerprint="fake-system-fingerprint",
                choices=[SimpleNamespace(delta=SimpleNamespace(content=""), finish_reason="stop")])
            return iter([first, second])
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(
            usage=_FakeUsage(), choices=[choice], id=response_id, model=model,
            system_fingerprint="fake-system-fingerprint")


class _FakeTogetherSDK:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_FakeChatCompletions())


@pytest.fixture(scope="module")
def capability_qa_context():
    """Everything needed to reconstruct one capability_qa call's (messages, temperature)."""
    protocol = phase2_plan.load_protocol(REPO_ROOT / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)
    _planning_keys, cells_by_key = b.load_capability_planning(protocol, REPO_ROOT)
    bundle, _protocol2 = prompt_bundle.load_and_validate(
        REPO_ROOT / pe.DEFAULT_PROMPT_BUNDLE_RELATIVE_PATH,
        REPO_ROOT / pe.DEFAULT_PROTOCOL_RELATIVE_PATH)
    corpus_entries = capability_corpus.render_capability_corpus(bundle, protocol, REPO_ROOT)
    corpus_lookup = {(str(e["question_id"]), str(e["side"])): e for e in corpus_entries}
    v3_payload, _v3_protocol, snapshot = role_limits.load_and_validate_v3(project_root=REPO_ROOT)
    return SimpleNamespace(
        protocol=protocol, cells_by_key=cells_by_key, corpus_lookup=corpus_lookup,
        v3_payload=v3_payload, snapshot=snapshot)


def test_request_fields_sha256_matches_live_complete_call(built, capability_qa_context):
    """Item-2 fidelity proof: a strict production-config client, a FAKE SDK, a real complete()
    call for >=5 diverse manifest entries (one per roster model -- including the streaming-pinned
    model and the extra-request-fields model) reproduces exactly the manifest's own
    request_fields_sha256.
    """
    ctx = capability_qa_context
    entries = built.manifest["provider_call_inventory"]

    by_model: dict[str, dict] = {}
    for entry in entries:
        by_model.setdefault(entry["model"], entry)
    sample = list(by_model.values())
    assert len(sample) >= 5
    assert "Qwen/Qwen3.7-Plus" in by_model  # streaming-pinned model
    assert "openai/gpt-oss-120b" in by_model  # per-model extra_request_fields model

    model_context_limits, streaming_pinned_models, extra_request_fields, model_prices = (
        b.capability_qa_client_construction_inputs(ctx.v3_payload, ctx.snapshot))
    assert "Qwen/Qwen3.7-Plus" in streaming_pinned_models
    assert "openai/gpt-oss-120b" in extra_request_fields

    client = api_client.RejudgeClient(
        approved_cap_usd=1_000.0,
        dry_run=False,
        _sdk_client=_FakeTogetherSDK(),
        model_prices=model_prices,
        strict_model_pricing=True,
        require_explicit_reasoning_max_tokens=True,
        model_context_limits=model_context_limits,
        strict_context_mode=True,
        streaming_pinned_models=streaming_pinned_models,
        extra_request_fields=extra_request_fields,
        halt_on_unknown_charge=True,
    )

    for entry in sample:
        cell = ctx.cells_by_key[entry["planning_cell_key"]]
        corpus_entry = ctx.corpus_lookup[(str(cell["question_id"]), entry["side"])]
        messages = [
            {"role": "system", "content": corpus_entry["system_prompt"]},
            {"role": "user", "content": corpus_entry["user_prompt"]},
        ]
        resolved = role_limits.resolve_request_parameters(
            ctx.v3_payload, ctx.protocol, entry["model"], pe.CAPABILITY_CALL_ROLE)

        client.complete(
            messages=messages, model=entry["model"], temperature=resolved.temperature,
            seed=entry["seed"], max_tokens=resolved.effective_max_tokens,
            request_metadata={"execution_call_key": entry["execution_call_key"]})

        success_events = [e for e in client.usage_events if e["status"] == "success"]
        last = success_events[-1]
        assert last["metadata"]["execution_call_key"] == entry["execution_call_key"]
        assert (last["response_metadata"]["request_fields_sha256"]
                == entry["request_fields_sha256"])


def test_compute_request_fields_sha256_matches_manifest_for_every_entry(
        built, capability_qa_context):
    """Cheaper, exhaustive companion to the fidelity test above: recompute every one of the
    1,060 entries' hashes via the SAME reused helper the builder itself used, and confirm the
    manifest recorded exactly that (catches any accidental non-determinism in the builder's own
    per-entry loop, independent of whether a live complete() call would agree)."""
    ctx = capability_qa_context
    model_context_limits, streaming_pinned_models, extra_request_fields, model_prices = (
        b.capability_qa_client_construction_inputs(ctx.v3_payload, ctx.snapshot))
    hash_client = b.build_hash_only_client(
        model_context_limits=model_context_limits,
        streaming_pinned_models=streaming_pinned_models,
        extra_request_fields=extra_request_fields, model_prices=model_prices,
        approved_cap_usd=1_000.0)

    for entry in built.manifest["provider_call_inventory"]:
        cell = ctx.cells_by_key[entry["planning_cell_key"]]
        corpus_entry = ctx.corpus_lookup[(str(cell["question_id"]), entry["side"])]
        messages = [
            {"role": "system", "content": corpus_entry["system_prompt"]},
            {"role": "user", "content": corpus_entry["user_prompt"]},
        ]
        resolved = role_limits.resolve_request_parameters(
            ctx.v3_payload, ctx.protocol, entry["model"], pe.CAPABILITY_CALL_ROLE)
        recomputed = b.compute_request_fields_sha256(
            hash_client, model=entry["model"], messages=messages,
            temperature=resolved.temperature, max_tokens=resolved.effective_max_tokens,
            seed=entry["seed"])
        assert recomputed == entry["request_fields_sha256"]


# ================================================================================================
# Manifest / authorization structural + cross-consistency
# ================================================================================================


def test_manifest_top_level_keys_exactly_match_frozen_schema(built):
    assert set(built.manifest) == pe.MANIFEST_TOP_LEVEL_KEYS


def test_manifest_planning_and_inventory_counts(built):
    assert len(built.manifest["planning_cell_keys"]) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert len(built.manifest["provider_call_inventory"]) == pe.EXPECTED_CAPABILITY_CELL_COUNT
    assert (sorted(built.manifest["planning_cell_keys"])
            == [e["planning_cell_key"] for e in built.manifest["provider_call_inventory"]] or
            set(built.manifest["planning_cell_keys"])
            == {e["planning_cell_key"] for e in built.manifest["provider_call_inventory"]})


def test_manifest_caps_and_ledger(built):
    assert built.manifest["stage_cap_usd"] == b.STAGE_CAP_USD
    assert built.manifest["cumulative_cap_usd"] == b.CUMULATIVE_CAP_USD
    assert built.manifest["ledger"]["path"] == b.LEDGER_PATH
    assert built.manifest["ledger"]["path"].startswith("E:/")


def test_manifest_storage_policy_is_the_real_tracked_artifact_byte_for_byte(built):
    real_payload = json.loads(
        (REPO_ROOT / b.STORAGE_POLICY_RELATIVE_PATH).read_text(encoding="utf-8"))
    assert built.manifest["storage_policy"]["path"] == b.STORAGE_POLICY_RELATIVE_PATH
    assert built.manifest["storage_policy"]["sha256"] == pe.canonical_sha256(real_payload)
    assert real_payload["versioned_destination"] == "E:/selvarath-archive/"


def test_manifest_implementation_provenance(built):
    prov = built.manifest["implementation_provenance"]
    assert prov["git_commit"] == b.EXPECTED_GIT_COMMIT
    assert prov["code_bundle_sha256"] == pe.compute_code_bundle_sha256(REPO_ROOT)


def test_authorization_keys_and_cross_consistency_with_manifest(built):
    assert set(built.authorization) == pe.AUTHORIZATION_KEYS
    assert built.authorization["execution_identity_sha256"] == built.execution_identity_sha256
    assert built.authorization["stage"] == built.manifest["stage"]
    assert built.authorization["stage_cap_usd"] == built.manifest["stage_cap_usd"]
    assert built.authorization["cumulative_cap_usd"] == built.manifest["cumulative_cap_usd"]
    assert built.authorization["approver"] == pe.PREFLIGHT_DELEGATION_APPROVER
    assert built.authorization["approved_at_utc"] == pe.PREFLIGHT_DELEGATION_APPROVED_AT_UTC
    assert (built.authorization["approval_basis_tracked_path"]
            == pe.DEFAULT_PREFLIGHT_DELEGATION_RELATIVE_PATH.as_posix())
    real_delegation_bytes = (
        REPO_ROOT / pe.DEFAULT_PREFLIGHT_DELEGATION_RELATIVE_PATH).read_bytes()
    assert (built.authorization["approval_basis_sha256"]
            == hashlib.sha256(real_delegation_bytes).hexdigest())


def test_generated_manifest_passes_the_real_validator_with_authorization(built):
    validated = pe.validate_execution_manifest(
        built.manifest, project_root=REPO_ROOT, authorization=built.authorization,
        require_authorized=True)
    assert validated.authorized is True
    assert validated.execution_identity_sha256 == built.execution_identity_sha256
    assert validated.stage == pe.STAGE_CAPABILITY_PREFLIGHT
    assert len(validated.provider_call_inventory) == pe.EXPECTED_CAPABILITY_CELL_COUNT


def test_manifest_fails_validation_without_authorization_when_required(built):
    with pytest.raises(pe.ExecutionAuthorityError):
        pe.validate_execution_manifest(
            built.manifest, project_root=REPO_ROOT, authorization=None, require_authorized=True)


def test_manifest_validates_unauthorized_without_an_authorization_record(built):
    validated = pe.validate_execution_manifest(
        built.manifest, project_root=REPO_ROOT, authorization=None, require_authorized=False)
    assert validated.authorized is False
    assert validated.execution_identity_sha256 == built.execution_identity_sha256


# ================================================================================================
# Committed artifacts (read-only): the actual files this builder wrote to the tracked repo.
# ================================================================================================


def test_committed_artifacts_pass_real_validator():
    manifest_path = REPO_ROOT / b.MANIFEST_RELATIVE_PATH
    authorization_path = REPO_ROOT / b.AUTHORIZATION_RELATIVE_PATH
    assert manifest_path.is_file(), (
        f"{manifest_path} does not exist yet; run "
        "`uv run python scripts/build_phase2_preflight_manifest.py` before this test")
    assert authorization_path.is_file()

    manifest = pe.load_execution_manifest(manifest_path)
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    validated = pe.validate_execution_manifest(
        manifest, project_root=REPO_ROOT, authorization=authorization, require_authorized=True)
    assert validated.authorized is True
    print(f"committed execution_identity_sha256={validated.execution_identity_sha256}")


def test_committed_manifest_is_canonical_json():
    manifest_path = REPO_ROOT / b.MANIFEST_RELATIVE_PATH
    raw = manifest_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    assert raw == b.canonical_json_bytes(payload)


# ================================================================================================
# Git-state guard
# ================================================================================================


def test_dirty_paths_beyond_parses_porcelain_lines():
    allowed = frozenset({"a/b.json", "c.json"})
    porcelain = "\n".join([
        " M a/b.json",              # modified, allowed
        "?? c.json",                # untracked, allowed
        " M unrelated.py",          # modified, NOT allowed
        "R  old.txt -> c.json",     # rename INTO an allowed path
        '?? "quoted path.txt"',     # quoted untracked, NOT allowed
        "",                          # blank line, ignored
    ])
    dirty = b.dirty_paths_beyond(porcelain, allowed)
    assert any("unrelated.py" in line for line in dirty)
    assert any("quoted path.txt" in line for line in dirty)
    assert not any("a/b.json" in line for line in dirty)
    assert not any(line.startswith("R  old.txt") for line in dirty)
    assert len(dirty) == 2


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_tiny_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    _run_git(repo, "add", "a.txt")
    _run_git(repo, "commit", "-q", "-m", "init")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_assert_clean_git_state_passes_on_a_clean_matching_repo(tmp_path):
    repo = tmp_path / "repo"
    head = _init_tiny_repo(repo)
    result = b.assert_clean_git_state(
        repo, allowed_relative_posix_paths=frozenset(), expected_head=head)
    assert result == head


def test_assert_clean_git_state_refuses_on_unrelated_dirty_file(tmp_path):
    repo = tmp_path / "repo"
    head = _init_tiny_repo(repo)
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(b.BuilderRefusedError, match="dirty"):
        b.assert_clean_git_state(
            repo, allowed_relative_posix_paths=frozenset(), expected_head=head)


def test_assert_clean_git_state_allows_only_its_own_new_output_files(tmp_path):
    repo = tmp_path / "repo"
    head = _init_tiny_repo(repo)
    (repo / "manifest.json").write_text("{}", encoding="utf-8")
    result = b.assert_clean_git_state(
        repo, allowed_relative_posix_paths=frozenset({"manifest.json"}), expected_head=head)
    assert result == head
    # A SECOND, non-allow-listed stray file alongside the allowed one still refuses.
    (repo / "stray.json").write_text("{}", encoding="utf-8")
    with pytest.raises(b.BuilderRefusedError, match="dirty"):
        b.assert_clean_git_state(
            repo, allowed_relative_posix_paths=frozenset({"manifest.json"}), expected_head=head)


def test_assert_clean_git_state_refuses_on_head_mismatch(tmp_path):
    repo = tmp_path / "repo"
    _init_tiny_repo(repo)
    with pytest.raises(b.BuilderRefusedError, match="HEAD"):
        b.assert_clean_git_state(
            repo, allowed_relative_posix_paths=frozenset(), expected_head="0" * 40)


# ================================================================================================
# Code-provenance byte verification: catches filter-only drift `git status` cannot see
# ================================================================================================


def test_frozen_code_bytes_diverging_from_git_blob_empty_on_a_pristine_repo(tmp_path):
    repo = tmp_path / "repo"
    head = _init_tiny_repo(repo)
    diverging = b.frozen_code_bytes_diverging_from_git_blob(
        repo, expected_head=head, frozen_relative_paths=("a.txt",))
    assert diverging == []
    # Does not raise.
    b.assert_frozen_code_bytes_match_git_blob(
        repo, expected_head=head, frozen_relative_paths=("a.txt",))


def test_frozen_code_bytes_diverging_from_git_blob_catches_autocrlf_drift_invisible_to_status(
        tmp_path):
    """The exact scenario from the code-provenance finding: ``core.autocrlf=true`` rewrites a
    tracked file's line endings on checkout, ``git status``/``git diff`` report the tree clean
    (this is what ``assert_clean_git_state`` relies on and cannot see past), but the raw bytes
    genuinely differ from the committed blob -- exactly what
    ``frozen_code_bytes_diverging_from_git_blob`` must catch instead.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    _run_git(repo, "config", "core.autocrlf", "true")
    (repo / "code.py").write_bytes(b"line1\nline2\nline3\n")
    _run_git(repo, "add", "code.py")
    _run_git(repo, "commit", "-q", "-m", "init")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
        check=True).stdout.strip()

    # Force a REAL git checkout so the smudge filter actually runs and the index's cached stat
    # reflects the resulting CRLF file: this is what makes `git status` report the tree clean
    # even though the raw bytes on disk no longer match the committed (LF) blob. A hand-written
    # byte rewrite that skips git's own checkout path does not reliably reproduce this (the
    # index's stat cache stays stale and `git status` flags it directly), so it would not
    # exercise the masking this test -- and the underlying finding -- is about.
    (repo / "code.py").unlink()
    _run_git(repo, "checkout", "--", "code.py")

    assert (repo / "code.py").read_bytes() == b"line1\r\nline2\r\nline3\r\n"
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=repo,
        capture_output=True, text=True, check=True).stdout
    assert status.strip() == ""  # git itself reports the tree as clean

    diverging = b.frozen_code_bytes_diverging_from_git_blob(
        repo, expected_head=head, frozen_relative_paths=("code.py",))
    assert diverging == ["code.py"]

    with pytest.raises(b.BuilderRefusedError, match="code.py"):
        b.assert_frozen_code_bytes_match_git_blob(
            repo, expected_head=head, frozen_relative_paths=("code.py",))


def test_frozen_code_bytes_match_git_blob_for_the_real_repo_pinned_commit():
    """Regression guard for the code-provenance finding: every one of the 9 real frozen
    code-provenance files' working-tree bytes must match the exact blob committed at the
    manifest's pinned commit, byte-for-byte -- not merely per `git status`/`git diff`, which
    core.autocrlf can fool. `git show <rev>:<path>` addresses an explicit commit, so this holds
    regardless of what HEAD currently is.
    """
    diverging = b.frozen_code_bytes_diverging_from_git_blob(
        REPO_ROOT, expected_head=b.EXPECTED_GIT_COMMIT)
    assert diverging == []


def test_committed_artifacts_git_commit_matches_expected_constant():
    # implementation_provenance.git_commit is a frozen literal (EXPECTED_GIT_COMMIT), not
    # recomputed from the live repo's current HEAD here: by the time this deliverable itself is
    # committed, HEAD has necessarily moved past the commit the manifest binds (this builder
    # cannot commit itself). This only checks internal consistency between the constant and the
    # artifact it produced, not that HEAD == EXPECTED_GIT_COMMIT right now.
    manifest_path = REPO_ROOT / b.MANIFEST_RELATIVE_PATH
    assert manifest_path.is_file()
    manifest = pe.load_execution_manifest(manifest_path)
    assert manifest["implementation_provenance"]["git_commit"] == b.EXPECTED_GIT_COMMIT
