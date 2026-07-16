import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from rejudge import run_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fixed_git(monkeypatch):
    state = {"sha": "a" * 40, "dirty": False}

    def fake(repo_root, excluded_paths):
        return Path(repo_root).resolve(), dict(state)

    monkeypatch.setattr(run_manifest, "_git_code_state", fake)
    return state


@pytest.fixture
def run_args(tmp_path, fixed_git):
    source = tmp_path / "inputs" / "transcripts.jsonl"
    source.parent.mkdir()
    source.write_text('{"question_id":"Q1"}\n', encoding="utf-8")
    output = tmp_path / "nested" / "records.jsonl"
    args = {
        "run_kind": "phase2-main",
        "dry_run": True,
        "models": {"judge": "org/judge", "debaters": ["org/a", "org/b"]},
        "prices": {"org/judge": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}},
        "protocol_content": {"arms": ["b0", "sequential"], "replicates": 2},
        "source_files": {"transcripts": source},
        "cli_params": {"workers": 2, "limit": None, "cell_groups": ("b0", "b2")},
        "repo_root": REPO_ROOT,
    }
    return output, source, args


def test_create_and_validate_same_manifest(run_args):
    output, source, args = run_args
    created = run_manifest.ensure_run_manifest(output, **args)
    path = run_manifest.manifest_path_for(output)

    assert path == output.with_name("records.jsonl.manifest.json")
    assert json.loads(path.read_text(encoding="utf-8")) == created
    identity = created["identity"]
    assert identity["mode"] == "dry-run"
    assert identity["dry_run"] is True
    assert identity["output"] == output.resolve().as_posix()
    assert identity["source_files"]["transcripts"]["sha256"] == hashlib.sha256(
        source.read_bytes()).hexdigest()
    assert identity["source_files"]["transcripts"]["bytes"] == source.stat().st_size
    assert identity["protocol"]["content"] == args["protocol_content"]
    assert len(identity["protocol"]["sha256"]) == 64
    assert identity["code"] == {"sha": "a" * 40, "dirty": False}

    original_bytes = path.read_bytes()
    validated = run_manifest.ensure_run_manifest(output, **args)
    assert validated == created
    assert path.read_bytes() == original_bytes


def test_normalization_makes_mapping_and_set_order_irrelevant(run_args):
    output, _, args = run_args
    args["models"] = {"z": "last", "a": "first"}
    args["protocol_content"] = {"z": 2, "a": 1}
    args["cli_params"] = argparse.Namespace(tags={"z", "a"}, path=Path("some/path"))
    first = run_manifest.ensure_run_manifest(output, **args)

    equivalent = dict(args)
    equivalent["models"] = {"a": "first", "z": "last"}
    equivalent["protocol_content"] = {"a": 1, "z": 2}
    equivalent["cli_params"] = {"path": "some/path", "tags": ["a", "z"]}
    second = run_manifest.ensure_run_manifest(output, **equivalent)
    assert second["manifest_sha256"] == first["manifest_sha256"]


@pytest.mark.parametrize(
    ("field", "replacement", "changed_path"),
    [
        ("run_kind", "phase2-canary", "identity.run_kind"),
        ("dry_run", False, "identity.dry_run"),
        ("models", {"judge": "org/different"}, "identity.models"),
        ("prices", {"org/judge": {"input_per_mtok": 9.0}}, "identity.prices"),
        ("protocol_content", {"arms": ["different"]}, "identity.protocol"),
        ("cli_params", {"workers": 99}, "identity.cli_params"),
    ],
)
def test_existing_manifest_refuses_identity_changes(
        run_args, field, replacement, changed_path):
    output, _, args = run_args
    run_manifest.ensure_run_manifest(output, **args)
    changed = dict(args)
    changed[field] = replacement

    with pytest.raises(run_manifest.ManifestMismatchError, match=changed_path):
        run_manifest.ensure_run_manifest(output, **changed)


def test_existing_manifest_refuses_source_or_code_changes(run_args, fixed_git):
    output, source, args = run_args
    run_manifest.ensure_run_manifest(output, **args)

    source.write_text('{"question_id":"Q2"}\n', encoding="utf-8")
    with pytest.raises(run_manifest.ManifestMismatchError,
                       match=r"identity\.source_files\.transcripts\.sha256"):
        run_manifest.ensure_run_manifest(output, **args)

    source.write_text('{"question_id":"Q1"}\n', encoding="utf-8")
    fixed_git["sha"] = "b" * 40
    fixed_git["dirty"] = True
    with pytest.raises(run_manifest.ManifestMismatchError, match=r"identity\.code"):
        run_manifest.ensure_run_manifest(output, **args)


def test_live_run_refuses_dirty_worktree_before_manifest_creation(run_args, fixed_git):
    output, _, args = run_args
    args["dry_run"] = False
    fixed_git["dirty"] = True

    with pytest.raises(run_manifest.RunManifestError, match="clean committed worktree"):
        run_manifest.ensure_run_manifest(output, **args)
    assert not run_manifest.manifest_path_for(output).exists()


def test_generated_runtime_paths_are_excluded_only_from_git_status(
        run_args, monkeypatch):
    output, _, args = run_args
    ledger = output.with_name("records.jsonl.usage.jsonl")
    state = output.with_name("records.jsonl.usage.jsonl.state.json")
    observed = {}

    def fake(repo_root, excluded_paths):
        observed["paths"] = {Path(path).resolve() for path in excluded_paths}
        return Path(repo_root).resolve(), {"sha": "a" * 40, "dirty": False}

    monkeypatch.setattr(run_manifest, "_git_code_state", fake)
    args["generated_paths"] = (ledger, state)
    created = run_manifest.ensure_run_manifest(output, **args)

    assert ledger.resolve() in observed["paths"]
    assert state.resolve() in observed["paths"]
    # Exclusions affect Git status only; they are not silently injected into identity.
    assert "generated_paths" not in created["identity"]


def test_existing_manifest_refuses_tampering(run_args):
    output, _, args = run_args
    run_manifest.ensure_run_manifest(output, **args)
    path = run_manifest.manifest_path_for(output)
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["identity"]["cli_params"]["workers"] = 200
    path.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(run_manifest.ManifestMismatchError, match="own hash check"):
        run_manifest.ensure_run_manifest(output, **args)


def test_existing_output_without_manifest_is_not_retroactively_adopted(run_args):
    output, _, args = run_args
    output.parent.mkdir(parents=True)
    output.write_text('{"old":"unproven provenance"}\n', encoding="utf-8")

    with pytest.raises(run_manifest.ManifestMismatchError, match="retroactive adoption"):
        run_manifest.ensure_run_manifest(output, **args)
    assert not run_manifest.manifest_path_for(output).exists()


def test_exclusive_atomic_creation_has_one_winner(run_args):
    output, _, args = run_args
    start = __import__("threading").Barrier(2)

    def create(marker):
        local = dict(args)
        local["cli_params"] = {"marker": marker}
        start.wait()
        try:
            result = run_manifest.ensure_run_manifest(output, **local)
            return "created", result["identity"]["cli_params"]["marker"]
        except run_manifest.ManifestMismatchError:
            return "refused", marker

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create, ("left", "right")))

    assert sorted(kind for kind, _ in outcomes) == ["created", "refused"]
    stored = json.loads(run_manifest.manifest_path_for(output).read_text(encoding="utf-8"))
    winner = next(marker for kind, marker in outcomes if kind == "created")
    assert stored["identity"]["cli_params"]["marker"] == winner


def test_output_lock_refuses_same_process_and_can_be_reacquired(tmp_path):
    output = tmp_path / "records.jsonl"
    with run_manifest.output_lock(output) as lock_path:
        assert lock_path == output.with_name("records.jsonl.lock").resolve()
        with pytest.raises(run_manifest.OutputLockedError):
            with run_manifest.output_lock(output):
                pass

    owner = json.loads(lock_path.read_text(encoding="utf-8"))
    assert owner["pid"]
    assert owner["output"] == str(output.resolve())
    with run_manifest.output_lock(output):
        pass
    # Re-acquisition replaces the diagnostic record instead of appending another.
    assert len(lock_path.read_text(encoding="utf-8").splitlines()) == 1


def _child_lock_attempt(output: Path) -> subprocess.CompletedProcess:
    script = """
import sys
from rejudge.run_manifest import OutputLockedError, output_lock
try:
    with output_lock(sys.argv[1]):
        pass
except OutputLockedError:
    raise SystemExit(23)
"""
    return subprocess.run(
        [sys.executable, "-c", script, str(output)], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10)


def test_output_lock_refuses_a_second_process(tmp_path):
    output = tmp_path / "records.jsonl"
    with run_manifest.output_lock(output):
        refused = _child_lock_attempt(output)
    accepted = _child_lock_attempt(output)

    assert refused.returncode == 23, refused.stderr
    assert accepted.returncode == 0, accepted.stderr


def test_rejects_nonfinite_or_non_string_cli_data(run_args):
    output, _, args = run_args
    bad_float = dict(args)
    bad_float["cli_params"] = {"temperature": float("nan")}
    with pytest.raises(ValueError, match="non-finite"):
        run_manifest.ensure_run_manifest(output, **bad_float)

    bad_key = dict(args)
    bad_key["cli_params"] = {1: "not a stable CLI name"}
    with pytest.raises(TypeError, match="non-string mapping key"):
        run_manifest.ensure_run_manifest(output, **bad_key)
