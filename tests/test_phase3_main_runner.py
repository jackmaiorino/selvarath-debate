"""Focused tests for the fake-only Phase 3 main-runner safety foundation."""
from __future__ import annotations

import inspect
import json
from collections import Counter
from pathlib import Path

import pytest

from rejudge import api_client, phase3_main_runner, phase3_plan


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inventory() -> phase3_main_runner.MainInventory:
    return phase3_main_runner.build_canonical_main_inventory(REPO_ROOT)


def _identity(root: Path, suffix: str = "a") -> phase3_main_runner.MainRunIdentity:
    return phase3_main_runner.MainRunIdentity(
        run_id=f"phase3-main-test-{suffix}",
        manifest_sha256=(suffix * 64)[:64],
        artifact_root=root.resolve(),
    )


def _run(
    root: Path,
    *,
    identity: phase3_main_runner.MainRunIdentity | None = None,
    responses: tuple[str, ...] = ("ANSWER: A",),
    body=lambda session: session,
):
    return phase3_main_runner.run_offline_foundation(
        identity or _identity(root),
        project_root=REPO_ROOT,
        offline_responses=responses,
        body=body,
    )


def test_main_inventory_is_exact_immutable_and_contains_no_canary_rows(inventory):
    assert inventory.judges == phase3_main_runner.CONFIRMED_MAIN_JUDGES
    assert len(inventory.question_ids) == 82
    assert len(inventory.transcript_cells) == 492
    assert len(inventory.judgment_cells) == 9_840
    assert len(inventory.cells) == 10_332
    assert phase3_main_runner._inventory_sha256(inventory) == (
        phase3_main_runner.EXPECTED_MAIN_INVENTORY_SHA256)
    assert {cell["kind"] for cell in inventory.cells} == {
        phase3_plan.MAIN_TRANSCRIPT_KIND,
        phase3_plan.MAIN_JUDGMENT_KIND,
    }
    assert not any("canary" in str(cell["kind"]) for cell in inventory.cells)

    per_judge = Counter(cell["judge_model"] for cell in inventory.judgment_cells)
    assert per_judge == Counter({judge: 4_920 for judge in inventory.judges})
    per_pair = Counter(
        (cell["judge_model"], cell["condition"])
        for cell in inventory.judgment_cells)
    assert set(per_pair.values()) == {984}
    assert len(per_pair) == 10
    with pytest.raises(TypeError):
        inventory.cells[0]["question_id"] = "CANARY-HELD-OUT"


def test_aggregate_preserving_inventory_mutation_is_rejected(inventory):
    cells = [dict(cell) for cell in inventory.cells]
    cells[0]["question_id"] = "CANARY-HELD-OUT"
    mutated = phase3_main_runner.MainInventory(
        question_ids=inventory.question_ids,
        judges=inventory.judges,
        cells=tuple(cells),
    )
    with pytest.raises(
            phase3_main_runner.MainInventoryError, match="exact canonical"):
        phase3_main_runner._validate_inventory(mutated)


def test_run_derives_inventory_internally(tmp_path, inventory):
    observed = _run(tmp_path, body=lambda session: session.inventory)
    assert phase3_main_runner._inventory_sha256(observed) == (
        phase3_main_runner.EXPECTED_MAIN_INVENTORY_SHA256)
    signature = inspect.signature(phase3_main_runner.run_offline_foundation)
    assert "inventory" not in signature.parameters


def test_lock_order_is_lease_marker_binding_ledger_journal_client_body(
    tmp_path, monkeypatch,
):
    events: list[str] = []
    lease_state = {"held": False}
    real_lease = phase3_main_runner.RunLease
    real_marker = phase3_main_runner._write_active_marker
    real_binding = phase3_main_runner._write_identity_binding
    real_ledger = phase3_main_runner._fresh_ledger_snapshot
    real_journal = phase3_main_runner.RequestJournal
    real_client = phase3_main_runner.ScriptedOfflineClient

    class ObservedLease:
        def __init__(self, path):
            self.inner = real_lease(path)

        def __enter__(self):
            self.inner.__enter__()
            lease_state["held"] = True
            events.append("lease")
            return self

        def __exit__(self, *exc_info):
            try:
                return self.inner.__exit__(*exc_info)
            finally:
                lease_state["held"] = False

    def observed_marker(identity, paths):
        assert lease_state["held"] is True
        events.append("marker")
        return real_marker(identity, paths)

    def observed_binding(identity, paths):
        assert lease_state["held"] is True
        events.append("binding")
        return real_binding(identity, paths)

    def observed_ledger(path):
        assert lease_state["held"] is True
        events.append("ledger")
        return real_ledger(path)

    def observed_journal(*args, **kwargs):
        assert lease_state["held"] is True
        events.append("journal")
        return real_journal(*args, **kwargs)

    class ObservedClient(real_client):
        def __init__(self, responses):
            assert lease_state["held"] is True
            events.append("client")
            super().__init__(responses)

    monkeypatch.setattr(phase3_main_runner, "RunLease", ObservedLease)
    monkeypatch.setattr(phase3_main_runner, "_write_active_marker", observed_marker)
    monkeypatch.setattr(phase3_main_runner, "_write_identity_binding", observed_binding)
    monkeypatch.setattr(phase3_main_runner, "_fresh_ledger_snapshot", observed_ledger)
    monkeypatch.setattr(phase3_main_runner, "RequestJournal", observed_journal)
    monkeypatch.setattr(phase3_main_runner, "ScriptedOfflineClient", ObservedClient)

    def body(_session):
        assert lease_state["held"] is True
        events.append("body")
        return "ok"

    result = phase3_main_runner.run_offline_foundation(
        _identity(tmp_path), project_root=REPO_ROOT, body=body)
    assert result == "ok"
    assert lease_state["held"] is False
    assert events == [
        "lease", "marker", "binding", "ledger", "journal", "client", "body"]


def test_one_journaling_client_wraps_every_scripted_call(tmp_path):
    seen_client_ids: list[int] = []

    def body(session):
        assert not hasattr(session.client, "inner")
        seen_client_ids.append(id(session.client))
        first = session.client.complete(
            [{"role": "user", "content": "first"}],
            session.inventory.judges[0], 0.0, 1, 32,
            request_metadata={"cell_key": "cell-1", "call_role": "judge_verdict"},
        )
        seen_client_ids.append(id(session.client))
        second = session.client.complete(
            [{"role": "user", "content": "second"}],
            session.inventory.judges[1], 0.0, 2, 32,
            request_metadata={"cell_key": "cell-2", "call_role": "judge_verdict"},
        )
        return first, second

    result = _run(tmp_path, responses=("", "ANSWER: B"), body=body)
    assert result == ("", "ANSWER: B")
    assert len(set(seen_client_ids)) == 1
    journal_rows = (tmp_path / "main_request_journal.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(journal_rows) == 2
    assert json.loads(journal_rows[0])["response"] == ""
    assert (tmp_path / "main_run.active.json").exists()
    assert (tmp_path / "main_run.identity.json").exists()


def test_session_client_rejects_ordinary_attribute_rebinding(tmp_path):
    def body(session):
        with pytest.raises(AttributeError, match="immutable"):
            session.client.complete = lambda *_args, **_kwargs: "ANSWER: B"
        with pytest.raises(AttributeError, match="immutable"):
            session.client._OfflineJournaledClient__complete = (
                lambda *_args, **_kwargs: "ANSWER: B")
        with pytest.raises(AttributeError, match="immutable"):
            del session.client._OfflineJournaledClient__complete
        return session.client.dry_run

    assert _run(tmp_path, body=body) is True


def test_exception_leaves_binding_and_same_identity_refuses_before_client(
    tmp_path, monkeypatch,
):
    identity = _identity(tmp_path)
    constructions = 0
    real_client = phase3_main_runner.ScriptedOfflineClient

    class CountingClient(real_client):
        def __init__(self, responses):
            nonlocal constructions
            constructions += 1
            super().__init__(responses)

    monkeypatch.setattr(phase3_main_runner, "ScriptedOfflineClient", CountingClient)
    with pytest.raises(RuntimeError, match="injected process interruption"):
        phase3_main_runner.run_offline_foundation(
            identity,
            project_root=REPO_ROOT,
            body=lambda _session: (_ for _ in ()).throw(
                RuntimeError("injected process interruption")),
        )
    assert constructions == 1
    assert identity.paths.active_marker.exists()
    assert identity.paths.identity_binding.exists()

    with pytest.raises(phase3_main_runner.MainIdentityInterrupted, match="already bound"):
        phase3_main_runner.run_offline_foundation(
            identity, project_root=REPO_ROOT, body=lambda _session: None)
    assert constructions == 1


@pytest.mark.parametrize(
    "artifact",
    ["binding", "active", "usage", "state", "journal", "unresolved",
     "results", "decisions", "completion"],
)
def test_any_existing_formal_artifact_refuses_before_start(tmp_path, artifact):
    identity = _identity(tmp_path)
    paths = identity.paths
    selected = {
        "binding": paths.identity_binding,
        "active": paths.active_marker,
        "usage": paths.usage_ledger,
        "state": api_client.usage_ledger_state_path(paths.usage_ledger),
        "journal": paths.request_journal,
        "unresolved": paths.unresolved_dispatch_marker,
        "results": paths.results,
        "decisions": paths.decisions,
        "completion": paths.completion,
    }[artifact]
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.touch()
    with pytest.raises(phase3_main_runner.MainIdentityInterrupted):
        phase3_main_runner.run_offline_foundation(
            identity, project_root=REPO_ROOT, body=lambda _session: None)
    if artifact not in {"binding", "active"}:
        assert not paths.active_marker.exists()


def test_body_deletion_is_restored_before_lease_release_and_reuse_refuses(tmp_path):
    identity = _identity(tmp_path)
    paths = identity.paths

    def deleting_body(session):
        assert session.identity.artifact_root == paths.root
        paths.active_marker.unlink()
        paths.identity_binding.unlink()
        for _label, path in paths.formal_artifacts():
            if path.exists():
                path.unlink()
        assert not paths.active_marker.exists()
        assert not paths.identity_binding.exists()
        return "deleted"

    assert _run(tmp_path, identity=identity, body=deleting_body) == "deleted"
    active = json.loads(paths.active_marker.read_text(encoding="utf-8"))
    binding = json.loads(paths.identity_binding.read_text(encoding="utf-8"))
    assert active["journal_execution_identity"] == identity.journal_execution_identity
    assert binding["journal_execution_identity"] == identity.journal_execution_identity

    with pytest.raises(phase3_main_runner.MainIdentityInterrupted, match="already bound"):
        phase3_main_runner.run_offline_foundation(
            identity, project_root=REPO_ROOT, body=lambda _session: None)
    assert paths.active_marker.exists()


def test_artifact_root_is_a_constituent_of_identity_and_paths_cannot_be_injected(tmp_path):
    first = _identity(tmp_path / "attempt-a")
    second = _identity(tmp_path / "attempt-b")
    assert first.run_id == second.run_id
    assert first.manifest_sha256 == second.manifest_sha256
    assert first != second
    assert first.journal_execution_identity != second.journal_execution_identity
    assert first.paths.lease.parent == first.artifact_root
    assert first.paths.active_marker.parent == first.artifact_root
    assert first.paths.usage_ledger.parent == first.artifact_root
    assert "paths" not in inspect.signature(
        phase3_main_runner.run_offline_foundation).parameters

    with pytest.raises(TypeError, match="unexpected keyword argument 'paths'"):
        phase3_main_runner.run_offline_foundation(
            first,
            paths=second.paths,  # ty: ignore[unknown-argument]
            project_root=REPO_ROOT,
            body=lambda _session: None,
        )
    assert not first.paths.active_marker.exists()
    assert not second.paths.active_marker.exists()


def test_chained_ledger_tamper_refuses_before_journal_and_client(
    tmp_path, monkeypatch,
):
    identity = _identity(tmp_path)
    real_prepare = phase3_main_runner.api_client.prepare_usage_ledger
    journal_calls = client_calls = 0

    def tampering_prepare(path, *, allow_create):
        ledger_identity = real_prepare(path, allow_create=allow_create)
        row = json.loads(Path(path).read_text(encoding="utf-8"))
        row["event_hash"] = "0" * 64
        Path(path).write_text(json.dumps(row) + "\n", encoding="utf-8")
        return ledger_identity

    def forbidden_journal(*_args, **_kwargs):
        nonlocal journal_calls
        journal_calls += 1
        raise AssertionError("journal opened before chained-ledger validation")

    class ForbiddenClient:
        def __init__(self, _responses):
            nonlocal client_calls
            client_calls += 1
            raise AssertionError("client constructed before ledger validation")

    monkeypatch.setattr(
        phase3_main_runner.api_client, "prepare_usage_ledger", tampering_prepare)
    monkeypatch.setattr(phase3_main_runner, "RequestJournal", forbidden_journal)
    monkeypatch.setattr(phase3_main_runner, "ScriptedOfflineClient", ForbiddenClient)
    with pytest.raises(api_client.UsageLedgerError, match="hash mismatch"):
        phase3_main_runner.run_offline_foundation(
            identity, project_root=REPO_ROOT, body=lambda _session: None)
    assert journal_calls == 0
    assert client_calls == 0
    assert identity.paths.active_marker.exists()
    assert identity.paths.identity_binding.exists()


def test_reconciliation_finding_refuses_before_client(tmp_path, monkeypatch):
    identity = _identity(tmp_path)
    monkeypatch.setattr(
        phase3_main_runner,
        "find_ambiguous_dispatches",
        lambda _journal, _events: [{"problem": "unknown_charge"}],
    )
    client_calls = 0

    class ForbiddenClient:
        def __init__(self, _responses):
            nonlocal client_calls
            client_calls += 1
            raise AssertionError("client constructed before reconciliation")

    monkeypatch.setattr(phase3_main_runner, "ScriptedOfflineClient", ForbiddenClient)
    with pytest.raises(phase3_main_runner.MainLedgerError, match="unknown_charge"):
        phase3_main_runner.run_offline_foundation(
            identity, project_root=REPO_ROOT, body=lambda _session: None)
    assert client_calls == 0
    assert identity.paths.active_marker.exists()


@pytest.mark.parametrize(
    "responses",
    [[], (), ("ANSWER: A", 1)],
    ids=["list", "empty", "non-string"],
)
def test_invalid_script_is_refused_before_identity_start(tmp_path, responses):
    identity = _identity(tmp_path)
    with pytest.raises(phase3_main_runner.OfflineClientRequired, match="exact tuple"):
        phase3_main_runner.run_offline_foundation(
            identity,
            project_root=REPO_ROOT,
            offline_responses=responses,
            body=lambda _session: None,
        )
    assert not identity.paths.active_marker.exists()
    assert not identity.paths.identity_binding.exists()


def test_flagged_provider_like_and_scripted_subclass_fail_exact_type_check():
    class FlaggedProviderLike:
        offline_only = True
        dry_run = True
        halt_on_unknown_charge = True

        def complete(self, *_args, **_kwargs):
            return "ANSWER: A"

    class ScriptedSubclass(phase3_main_runner.ScriptedOfflineClient):
        pass

    with pytest.raises(phase3_main_runner.OfflineClientRequired, match="exact"):
        phase3_main_runner._validate_offline_client(FlaggedProviderLike())
    with pytest.raises(phase3_main_runner.OfflineClientRequired, match="exact"):
        phase3_main_runner._validate_offline_client(
            ScriptedSubclass(("ANSWER: A",)))


def test_new_identity_with_new_empty_root_can_start_after_interruption(tmp_path):
    first = _identity(tmp_path / "attempt-a", "a")
    with pytest.raises(RuntimeError, match="stop"):
        phase3_main_runner.run_offline_foundation(
            first,
            project_root=REPO_ROOT,
            body=lambda _session: (_ for _ in ()).throw(RuntimeError("stop")),
        )

    second = _identity(tmp_path / "attempt-b", "b")
    result = _run(
        second.artifact_root,
        identity=second,
        body=lambda session: session.identity.run_id,
    )
    assert result == "phase3-main-test-b"
    assert first.paths.active_marker.exists()
    assert first.paths.identity_binding.exists()
    assert second.paths.active_marker.exists()
    assert second.paths.identity_binding.exists()


def test_module_exposes_no_live_factory_or_cli():
    signature = inspect.signature(phase3_main_runner.run_offline_foundation)
    assert "client_factory" not in signature.parameters
    assert "inventory" not in signature.parameters
    assert "paths" not in signature.parameters
    assert not hasattr(phase3_main_runner, "build_production_client")
    assert not hasattr(phase3_main_runner, "run_live")
    assert not hasattr(phase3_main_runner, "main")
